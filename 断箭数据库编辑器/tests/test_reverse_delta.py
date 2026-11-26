"""ADR 0007 判据：反向 Δ 安装 / 还原（⛔ 不拷整包当备份）。

**判据一行**：pytest tests/test_reverse_delta.py -q -> 通过数 **从 0 收集变 N passed**
（本文件新建前不存在）。

| 用例 | 判据（ADR 0007 第 4 条） | 通过线 |
|---|---|---|
| test_round_trip_* | 改 → 存 Δ → 还原 ⇒ **整文件 sha256 == 改前**（bundle 与 catalog） | 相等 |
| test_negative_control_* | 还原前手改 1 字节 ⇒ restore **拒绝**，文件逐位不动、补丁留着 | 能红 |
| test_corrupted_patch_blob_* | 补丁 blob 被改过 ⇒ 拒绝（blob 自身 sha256 自证） | 能红 |
| test_delta_chain_* | Δ（变长）那条链同样能还原 | 相等 |
| test_two_installs_* | 装两次只还原一次 ⇒ 回到**最早**那份（不是半改状态） | 相等 |
| test_catalog_baseline_* | catalog 只存**差异块**，⛔ 不存整份 | < 整份 |
| test_no_whole_copy_* | 真写路径上不再产出整包副本（report.backup is None） | 一个副本都没有 |
| test_patch_store_on_another_drive_* | 补丁柜落别的盘 ⇒ 拒绝（红线） | 能红 |
| test_frozen_* | PatchStore / PatchBuilder 的冻结接口 | 名字对得上 |

## 为什么是合成件

与 tests/test_writeback_l1.py 同一套最小容器 / catalog 构造器：判据全落在 **Δ 记录本身**
（哪块被重压、落点在第几块、攒起来的字节能不能拼回旧文件）上，与容器多大无关。

真件的判定性实验走 _work/delta_roundtrip.py（**真包副本**，跑完删夹具，⛔ 不碰游戏目录）。
本轮实测读数（2026-10-05）：318,810,757 B 的 unitportraits 包，
补丁容器部分 **73,288 B = 0.0230%**（判据 < 1% ✓）、catalog 部分 4,096 B；
改 → 存 Δ → 还原之后两个文件的整文件 sha256 都回到改前（正控也能红）。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import reverse_delta, writer
from ba_mod_maker.asset_kernel import user_items as ui
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError
from test_user_items import PID as UI_PID
from test_user_items import UNIT as UI_UNIT
from test_user_items import _blob as _ui_blob
from test_user_items import _container as _ui_container
from test_user_items import _payload as _ui_payload
from test_user_items import _sf as _ui_sf
from test_user_items import _tree as _ui_tree
from test_writeback_l1 import (
    TARGET_PID,
    _blob,
    _build_container,
    _build_sf,
    _bundle_name,
    _equal_change,
    _objects,
    write_catalog_sized,
)


def _bundle(tmp_path: Path) -> Path:
    """与 test_writeback_l1 同构：3 个对象，跨若干块。"""
    objs = [
        (1001, _blob(b"A", 130_000)),
        (1002, _blob(b"B", 200_000)),
        (1003, _blob(b"C", 200_000)),
    ]
    path = tmp_path / "synth.bundle"
    path.write_bytes(_build_container(_build_sf(objs)))
    return path


def _fixture(tmp_path: Path, *, pad: int = 200) -> tuple[Path, Path]:
    """(真名的包, catalog)：catalog 里登记这个包，另有 pad 条无关记录把文件撑到几十 KB。"""
    bundle = _bundle(tmp_path)
    plan = writer.plan_writeback(bundle, [_equal_change(bundle)])
    real = tmp_path / _bundle_name(0)
    shutil.copy2(bundle, real)
    rows = [(0, plan.disk_crc, real.stat().st_size)]
    rows += [(i, 0, 0) for i in range(1, pad + 1)]
    catalog = write_catalog_sized(tmp_path / "catalog.json", rows)
    return real, catalog


def _store(tmp_path: Path) -> reverse_delta.PatchStore:
    return reverse_delta.PatchStore(tmp_path / "patches", game_root=tmp_path)


def _install(real: Path, catalog: Path, store: reverse_delta.PatchStore, tmp_path: Path):
    return writer.write_objects(
        real,
        [_equal_change(real)],
        dry_run=False,
        game_root=tmp_path,
        catalog_path=catalog,
        patch_store=store,
        patch_label="测试：反向 Δ",
    )


# --------------------------------------------------------------------------- #
# 判据一行：改 → 存 Δ → 还原
# --------------------------------------------------------------------------- #
def test_round_trip_restores_both_files_byte_identical(tmp_path: Path) -> None:
    """判据：还原后**整文件 sha256 == 改前**（bundle 与 catalog 两个目标都要）。"""
    real, catalog = _fixture(tmp_path)
    sha_bundle = reverse_delta.sha256_of(real)
    sha_catalog = reverse_delta.sha256_of(catalog)
    store = _store(tmp_path)

    report = _install(real, catalog, store, tmp_path)
    assert report.patch is not None and report.patch.is_file()
    assert store.pending() is not None, "写完却没落补丁 —— 反向 Δ 不成立"
    assert reverse_delta.sha256_of(real) != sha_bundle

    result = store.restore()
    assert result.ok, result.lines()
    assert reverse_delta.sha256_of(real) == sha_bundle, "还原后整文件 sha256 没回到改前"
    assert reverse_delta.sha256_of(catalog) == sha_catalog
    assert not store.has_pending(), "还原成功之后补丁必须删掉"


def test_negative_control_tampered_file_refuses_and_changes_nothing(tmp_path: Path) -> None:
    """尺子先做正控：手改 1 字节之后 restore 必须**拒绝**，而且一个字节都不许动。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    with real.open("r+b") as handle:
        handle.seek(1024)
        handle.write(b"\xee")
    tampered = reverse_delta.sha256_of(real)
    with pytest.raises(AssetError, match="拒绝自动还原"):
        store.restore()
    assert reverse_delta.sha256_of(real) == tampered, "拒绝还原时居然动了文件"
    assert store.has_pending(), "拒绝还原时不该把补丁删掉"


def test_corrupted_patch_blob_is_rejected(tmp_path: Path) -> None:
    """补丁自己被改过 ⇒ 读盘时就拒绝（blob 的 sha256 自证）。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    blob = store.blob_path.read_bytes()
    store.blob_path.write_bytes(bytes([blob[0] ^ 0xFF]) + blob[1:])
    with pytest.raises(AssetError, match="blob 自身 sha256"):
        store.restore()


def test_delta_chain_growth_also_restores(tmp_path: Path) -> None:
    """Δ（变长）那条链：只有 3 块重压、其余原样搬 —— 还原同样要逐位回到改前。"""
    real, catalog = _fixture(tmp_path)
    sha_bundle = reverse_delta.sha256_of(real)
    store = _store(tmp_path)
    _start, old = _objects(real)[TARGET_PID]
    change = writer.DeltaChange(
        growths=(
            writer.ObjectGrowth(
                path_id=TARGET_PID, payload=old + b"\x00" * 64, name="SYNTH", type_name="Texture2D"
            ),
        )
    )
    assert writer.plan_delta(real, change, catalog_path=catalog).allow_grow
    report = writer.write_delta(
        real,
        change,
        dry_run=False,
        game_root=tmp_path,
        catalog_path=catalog,
        patch_store=store,
    )
    assert report.patch is not None
    assert reverse_delta.sha256_of(real) != sha_bundle
    store.restore()
    assert reverse_delta.sha256_of(real) == sha_bundle


def test_two_installs_one_restore_goes_back_to_the_first_baseline(tmp_path: Path) -> None:
    """再装覆盖它 = **合并**：基线永远是「用户装之前那一刻」，不是「第一次装完的样子」。"""
    real, catalog = _fixture(tmp_path)
    sha0 = reverse_delta.sha256_of(real)
    sha_cat0 = reverse_delta.sha256_of(catalog)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    sha1 = reverse_delta.sha256_of(real)
    _install(real, catalog, store, tmp_path)
    patch = store.pending()
    assert patch is not None and len(patch.targets) == 2
    bundle_target = next(t for t in patch.targets if isinstance(t, reverse_delta.BundleTarget))
    assert bundle_target.sha_before == sha0, "合并之后基线被换掉了"
    assert bundle_target.sha_after == reverse_delta.sha256_of(real)
    assert reverse_delta.sha256_of(real) != sha1
    store.restore()
    assert reverse_delta.sha256_of(real) == sha0
    assert reverse_delta.sha256_of(catalog) == sha_cat0


# --------------------------------------------------------------------------- #
# 体积与形态：不存整份、不拷整包
# --------------------------------------------------------------------------- #
def test_catalog_baseline_stores_only_diff_chunks(tmp_path: Path) -> None:
    """catalog 只存**差异块**（4 KB 对齐）—— 实测一条 m_Crc 改动只差 10 B，别存整份 4 MB。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    patch = store.pending()
    target = next(t for t in patch.targets if isinstance(t, reverse_delta.FileTarget))
    assert not target.whole
    assert len(target.parts) == 1, target.parts
    assert target.stored_bytes <= reverse_delta._FILE_CHUNK
    assert target.stored_bytes < catalog.stat().st_size, "只该存差异块"


def test_no_whole_copy_in_the_delta_path(tmp_path: Path) -> None:
    """真写路径上**一个整包副本都不产出**：只有补丁柜那两份文件，F5 换成 Δ 自证。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    report = _install(real, catalog, store, tmp_path)
    assert report.backup is None, "反向 Δ 那条链不该再产出整包副本"
    assert report.patch is not None
    assert report.verify["F5 反向 Δ"]["ok"] is True
    assert report.verify["F5b 反向 Δ"]["ok"] is True
    assert "F5 备份" not in report.verify
    made = sorted(p.name for p in (tmp_path / "patches").iterdir())
    assert made == [reverse_delta.PATCH_BLOB_NAME, reverse_delta.PATCH_JSON_NAME]
    # ⚠ 2026-10-06 实测踩到过的回归：Δ 分支当初少了 else，旧的整包备份链**也**跑了一遍
    #    （结果 Steam\steamapps\common\bamod_backup 涨到 35 GB）。这两条就是它的锁。
    strays = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.bak"))
    assert strays == [], f"Δ 那条链不许产出整包副本，却留下 {strays}"
    assert not (tmp_path / "bamod_backup").exists(), "Δ 那条链不该建旧备份目录"


def test_patch_store_on_another_drive_is_rejected(tmp_path: Path) -> None:
    """红线：补丁柜必须跟随游戏所在盘（历史教训：备份落 C 盘白占 6.95 GB）。"""
    drive = str(tmp_path.drive).upper()
    other = "D:\\bamod_should_never_be_created" if drive != "D:" else "C:\\bamod_never"
    with pytest.raises(AssetError, match="跟随游戏所在盘"):
        reverse_delta.PatchStore(Path(other) / "patches", game_root=tmp_path)


def test_discard_drops_the_patch_without_touching_files(tmp_path: Path) -> None:
    """显式丢弃：只删补丁、不动文件（用户自认不要这条退路）。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    sha_after = reverse_delta.sha256_of(real)
    lines = store.discard()
    assert any("已丢弃补丁" in line for line in lines)
    assert not store.has_pending()
    assert reverse_delta.sha256_of(real) == sha_after, "丢弃补丁不该动文件"
    with pytest.raises(AssetError, match="没有补丁"):
        store.restore()


# --------------------------------------------------------------------------- #
# 冻结接口
# --------------------------------------------------------------------------- #
def test_frozen_interfaces() -> None:
    """接口冻结：CLI / 队友照着这几个名字写，改了要一起改。"""
    assert list(inspect.signature(reverse_delta.PatchStore.__init__).parameters) == [
        "self",
        "root",
        "game_root",
    ]
    assert list(inspect.signature(reverse_delta.PatchBuilder.capture_bundle).parameters) == [
        "self",
        "reader",
        "placement",
        "sha_before",
        "size_before",
    ]
    assert list(inspect.signature(reverse_delta.PatchBuilder.capture_file).parameters) == [
        "self",
        "path",
        "before",
        "after",
    ]
    assert list(inspect.signature(reverse_delta.PatchStore.restore).parameters) == [
        "self",
        "check_sha",
    ]
    assert reverse_delta.PATCH_JSON_NAME == "install.rmod.json"
    assert reverse_delta.PATCH_BLOB_NAME == "install.rmod.bin"
    assert reverse_delta.patch_store_root(Path("X")) == Path("X") / "patches"
    # 2026-10-06：格式升到 2 —— BundleTarget 改成紧凑编码（stored + verbatim_runs）
    assert reverse_delta.FORMAT_VERSION == 2
    assert reverse_delta.verbatim_placement(3, dirty={1}, verbatim_from=None) == {0: 0, 2: 2}
    assert reverse_delta.verbatim_placement(3, dirty={}, verbatim_from={2: 0}) == {0: 2}


def test_compact_encoding_collapses_the_block_table(tmp_path: Path) -> None:
    """紧凑编码（2026-10-06）：块表**不进 JSON** —— 只留「存下来的那几块 ＋ 几段平移区间」。

    由来：真件 units 41,440 块，按一条一块序列化 ⇒ install.rmod.json **7,328,804 B**
    （载荷才 171,912 B）。改成 stored ＋ verbatim_runs 之后同规模应当 ~1 KB。
    判据：合成件写回后
      * stored 条数 == 被重压的块数（远小于块总数）；
      * verbatim_runs 覆盖其余全部块，且 L1 那条链的 shift 全 0；
      * **补丁 JSON < 4 KB**（旧编码一条块记录 ≈ 180 B）；
      * 还原仍然逐位可复算（紧凑编码不许把正确性弄丢）。
    """
    real, catalog = _fixture(tmp_path)
    sha_before = reverse_delta.sha256_of(real)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)

    with ufs.UfsReader(real) as reader:
        after_blocks = len(reader.blocks)
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    entry = next(t for t in raw["targets"] if t["kind"] == "bundle")
    stored = entry["stored"]
    runs = entry["verbatim_runs"]
    assert entry["block_count"] == after_blocks
    assert 0 < len(stored) < after_blocks, stored
    covered = sum(r["hi"] - r["lo"] for r in runs)
    assert covered + len(stored) == entry["block_count"], (covered, len(stored), entry)
    assert all(r["shift"] == 0 for r in runs), runs
    assert "blocks" not in entry, "旧编码（一条一块）又回来了"
    size = store.json_path.stat().st_size
    assert size < 4096, f"补丁 JSON {size:,} B —— 块表又写回 JSON 里了？"

    store.restore()
    assert reverse_delta.sha256_of(real) == sha_before



# --------------------------------------------------------------------------- #
# RangeTarget（task-17：⑥ 层那种「一次只改容器里一颗对象」的反向 Δ）
# --------------------------------------------------------------------------- #
#: 合成容器里那个填充对象的 pathID（与 test_user_items 的 OTHER_PID 同一个值）。
UI_OTHER_PID = 52175


def _range_container(tmp_path: Path, name: str = "ggm.unity3d") -> Path:
    """合成容器：一个填充对象 ＋ 那颗 UserItemsConfig（与 test_user_items 同一份夹具）。"""
    path = tmp_path / name
    path.write_bytes(
        _ui_container(
            _ui_sf([(UI_OTHER_PID, _ui_blob(b"F", 200_000)), (UI_PID, _ui_payload(_ui_tree()))])
        )
    )
    return path


def _add_plan(case: Path):
    return ui.plan_add(
        case,
        UI_PID,
        unit_id=UI_UNIT,
        item=ui.SkinItem(9999, "9 Probe", (), 1, 1, 0),
        reference_id=2232,
    )


def test_range_target_is_written_as_format_3(tmp_path: Path) -> None:
    """含 range 的补丁写 **format 3**（老版本读到 3 会明确拒绝，不会当成 FileTarget 读错）。"""
    store = _store(tmp_path)
    case = _range_container(tmp_path)
    out = tmp_path / "out.unity3d"
    result = ui.install(_add_plan(case), out, patch_store=store)
    assert result.patch_path is not None and result.patch_path.is_file()
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    assert raw["format"] == reverse_delta.RANGE_FORMAT_VERSION == 3
    assert [t["kind"] for t in raw["targets"]] == ["range"]
    assert store.pending() is not None, "写出去又读不回来 —— 补丁白记了"


def test_a_patch_without_range_still_writes_format_2(tmp_path: Path) -> None:
    """只有包 / 整文件两档的补丁**照旧写 2** ⇒ 既有的 v2 补丁一条都不作废。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    assert raw["format"] == reverse_delta.FORMAT_VERSION == 2
    assert set(reverse_delta.SUPPORTED_FORMATS) == {2, 3}


def test_unknown_format_and_unknown_kind_are_rejected(tmp_path: Path) -> None:
    """⛔ 不认识的格式 / 不认识的 kind 一律拒绝（不许拿旧代码解释新补丁）。"""
    real, catalog = _fixture(tmp_path)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))

    raw["format"] = 99
    store.json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssetError, match="格式版本"):
        store.pending()

    raw["format"] = 2
    raw["targets"][0]["kind"] = "hologram"
    store.json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssetError, match="kind"):
        store.pending()


def test_mixed_patch_restores_bundle_and_range_in_one_pass(tmp_path: Path) -> None:
    """**混合柜子**（Lead 点名）：同一个 patches/ 里既有包那档、也有 range 这档 ⇒ 一趟全还原。

    这正是真机的形状：③ 层改 units 包（bundle ＋ catalog 两条），⑥ 层改 data.unity3d 里那颗对象。
    """
    real, catalog = _fixture(tmp_path)
    sha_bundle = reverse_delta.sha256_of(real)
    sha_catalog = reverse_delta.sha256_of(catalog)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    case = _range_container(tmp_path)
    sha_case = reverse_delta.sha256_of(case)
    out = tmp_path / "range-out.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    sha_out = reverse_delta.sha256_of(out)
    assert sha_out != sha_case, "range 那一步没写字节 —— 这一条等于没跑"

    patch = store.pending()
    assert patch is not None
    assert {t.kind for t in patch.targets} == {"bundle", "file", "range"}
    assert patch.format == reverse_delta.RANGE_FORMAT_VERSION
    report = store.restore()
    assert report.ok, report.lines()
    assert reverse_delta.sha256_of(real) == sha_bundle, "包没回到改前"
    assert reverse_delta.sha256_of(catalog) == sha_catalog, "catalog 没回到改前"
    assert reverse_delta.sha256_of(out) == sha_case, "range 那条没逐位回到源件那一份"
    assert not store.has_pending()


def test_range_lines_read_like_a_sentence(tmp_path: Path) -> None:
    """assets status 印的是人话：标签 ＋ pathID ＋ 单位 ＋ 那一段字节数 ＋ 补丁体积。"""
    store = _store(tmp_path)
    case = _range_container(tmp_path)
    out = tmp_path / "out.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    text = chr(10).join(store.status_lines())
    for needle in ("可见性层新增", "pathID=" + str(UI_PID), "单位 " + str(UI_UNIT),
                   "那一段", "补丁存原始字节"):
        assert needle in text, f"status 里缺 {needle!r}：" + text
    assert "228" in text, text


def test_range_rebuild_self_check_can_go_red(tmp_path: Path) -> None:
    """**尺子正控**：补丁里那段原始字节改一格 ⇒ 还原必须红，且文件一个字节都不动。"""
    store = _store(tmp_path)
    case = _range_container(tmp_path)
    out = tmp_path / "out.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    installed = reverse_delta.sha256_of(out)
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    ref = raw["targets"][0]["old_bytes"]
    blob = bytearray(store.blob_path.read_bytes())
    blob[ref["offset"]] ^= 0xFF
    store.blob_path.write_bytes(bytes(blob))
    raw["blob_sha256"] = hashlib.sha256(bytes(blob)).hexdigest()
    store.json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssetError, match="sha256 对不上"):
        store.restore()
    assert reverse_delta.sha256_of(out) == installed, "拒绝还原时居然动了文件"
    assert store.has_pending(), "拒绝还原时不该把补丁删掉"


def test_capture_range_twice_keeps_the_first_baseline(tmp_path: Path) -> None:
    """连记两次**同一段**区间：基线（old_bytes / sha_before）保持最早那份，new_size 跟到最新。

    这就是「第二次安装不许把基线改成半改状态」那条口径在 range 档上的落实；
    基线对不上（当前 sha ≠ 补丁记的 sha_after）时必须**拒绝合并**。
    """
    store = _store(tmp_path)
    builder = store.begin("单元判据")
    out = tmp_path / "product.unity3d"
    common = {
        "path": out,
        "node_path": ui.NODE_PATH,
        "path_id": UI_PID,
        "stream_start": 0,
        "label": "可见性层新增：合成件",
    }
    first = builder.capture_range(
        **common, old_bytes=b"A" * 10, new_size=20,
        sha_before="b" * 64, sha_after="1" * 64, size_before=100, size_after=110,
    )
    assert first.old_size == 10 and first.stored_bytes == 10
    again = builder.capture_range(
        **common, old_bytes=b"B" * 20, new_size=30,
        sha_before="1" * 64, sha_after="2" * 64, size_before=110, size_after=120,
    )
    assert again is first, "同一段区间该合并成一条，不是新起一条"
    assert again.old_size == 10, "基线被改成中间态了"
    assert builder._blob_slice(again.old_bytes) == b"A" * 10
    assert again.new_size == 30 and again.sha_after == "2" * 64
    assert again.sha_before == "b" * 64 and again.size_before == 100
    with pytest.raises(AssetError, match="装后"):
        builder.capture_range(
            **common, old_bytes=b"C" * 5, new_size=40,
            sha_before="WRONG", sha_after="3" * 64, size_before=120, size_after=130,
        )


def test_a_range_target_repoints_at_the_product_not_the_source(tmp_path: Path) -> None:
    """range 目标记的是**产物**（要滚回去的是它），origin 才是干跑读的那份源件。"""
    store = _store(tmp_path)
    case = _range_container(tmp_path)
    out = tmp_path / "scrollback.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    target = store.pending().targets[0]
    assert Path(target.path) == out
    assert target.sha_before == reverse_delta.sha256_of(case)
    assert target.sha_after == reverse_delta.sha256_of(out)


# --------------------------------------------------------------------------- #
# BUG-2 / BUG-3（2026-10-11 实机暴露的两条）—— 判据都落在「撤得掉」上
# --------------------------------------------------------------------------- #
def _mid_stream_container(tmp_path: Path, name: str = "mid.unity3d") -> Path:
    """合成件：目标对象**夹在两条大对象中间**（落在整块内部），装完那一段比一个块长。

    这就是真机 `data.unity3d` 的形状：那颗 UserItemsConfig 在解压流中间、跨若干块。
    """
    path = tmp_path / name
    path.write_bytes(
        _ui_container(
            _ui_sf(
                [
                    (UI_OTHER_PID, _ui_blob(b"F", 300_000)),
                    (UI_PID, _ui_payload(_ui_tree())),
                    (UI_OTHER_PID + 1, _ui_blob(b"G", 300_000)),
                ]
            )
        )
    )
    return path


def test_range_restore_is_bit_exact_when_the_payload_grows(tmp_path: Path) -> None:
    """★ BUG-2：**变长**安装之后，range 还原必须逐位回到源件（含块边界）。

    旧实现的 bug：还原按「对象盖到的块」重切那一段，而装的那一刻在尾巴上多切出来的
    小块不在范围内 ⇒ 装后文件比原始文件多一个块（块表不同、解压流相同）⇒ 整文件 sha256
    对不上 ⇒ fail-closed 拒绝 ⇒ **撤不掉**（2026-10-11 真机 data.unity3d 现场）。

    判据一行：`pytest tests/test_reverse_delta.py -q -k payload_grows`
             → 还原后 sha256 **== 装前**（旧实现 != ）。
    """
    case = _mid_stream_container(tmp_path)
    sha_case = reverse_delta.sha256_of(case)
    plan = _add_plan(case)
    assert plan.delta != 0, "这一条要的是变长：payload 必须长大"
    store = _store(tmp_path)
    out = tmp_path / "mid-out.unity3d"
    ui.install(plan, out, patch_store=store)
    target = store.pending().targets[0]
    assert isinstance(target, reverse_delta.RangeTarget)
    assert target.run_end > target.run_start, "重切段没记进补丁 —— BUG-2 会复发"
    report = store.restore()
    assert report.ok, report.lines()
    assert reverse_delta.sha256_of(out) == sha_case, "还原没逐位回到装前（块边界没复现）"


def test_an_old_patch_without_the_run_span_still_restores(tmp_path: Path) -> None:
    """**老补丁兼容**：重切段抹成 0/0（= 修复前写的补丁）⇒ 按 128 KiB 网格推出来，照样逐位回。

    这就是真机那一单的形状：补丁是修复前写的，柜里没有重切段。
    """
    case = _mid_stream_container(tmp_path)
    sha_case = reverse_delta.sha256_of(case)
    store = _store(tmp_path)
    out = tmp_path / "mid-old.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    recorded = store.pending().targets[0]
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    raw["targets"][0]["run_start"] = 0
    raw["targets"][0]["run_end"] = 0
    store.json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    # 交叉核对：推导出来的段必须与装那一刻记下的那段**一模一样**
    with ufs.UfsReader(out) as reader:
        node = reader.node(recorded.node_path)
        info = ufs.read_serialized_file(reader, node)
        row = next(r for r in info.objects if int(r[1]) == int(recorded.path_id))
        start, size = node.offset + info.data_offset + int(row[2]), int(row[3])
        derived = reverse_delta._run_to_rechunk(reader, recorded, start, size)
    assert derived == (recorded.run_start, recorded.run_end), "网格推导与实记账不是同一条边界"
    report = store.restore()
    assert report.ok, report.lines()
    assert reverse_delta.sha256_of(out) == sha_case, "老补丁没逐位回到装前"


def test_without_the_extension_the_restore_goes_red(tmp_path: Path, monkeypatch) -> None:
    """**尺子正控**：把「扩尾」关掉（= 修复前的行为）⇒ 同一条还原必须**红**。

    能红才说明上面那两条「逐位回去」是被这条边界挣来的，不是碰巧过的。
    """
    case = _mid_stream_container(tmp_path)
    store = _store(tmp_path)
    out = tmp_path / "mid-red.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    installed = reverse_delta.sha256_of(out)
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    raw["targets"][0]["run_start"] = 0
    raw["targets"][0]["run_end"] = 0  # 逼它走推导那条路
    store.json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    def _no_extend(reader, target, start, size):  # 正控用的一次性替身：不扩尾
        return ufs.block_start_of(reader, start), ufs.block_start_of(reader, start)

    monkeypatch.setattr(reverse_delta, "_run_to_rechunk", _no_extend)
    with pytest.raises(AssetError, match="sha256 对不上"):
        store.restore()
    assert reverse_delta.sha256_of(out) == installed, "拒绝还原时居然动了文件"
    assert store.has_pending(), "拒绝还原时不该把补丁删掉"


def test_partial_restore_drops_the_targets_already_scrolled_back(tmp_path: Path) -> None:
    """★ BUG-3：半次还原之后，**已经回到装前**的那几条不许再留在柜里。

    现场形状（2026-10-11 真机）：units/catalog 还原成功、data.unity3d 被拒 ⇒ 旧实现把三条
    记录原样留着 ⇒ 下一次写被 stale-guard 拦，而唯一的逃生门 `assets discard` 会连
    没还原的那条一起丢。

    判据一行：`pytest tests/test_reverse_delta.py -q -k partial_restore`
             → 抛异常之后柜里的目标 **3 → 1**（只剩没还原回去的那条 range）。
    """
    real, catalog = _fixture(tmp_path)
    sha_bundle = reverse_delta.sha256_of(real)
    sha_catalog = reverse_delta.sha256_of(catalog)
    store = _store(tmp_path)
    _install(real, catalog, store, tmp_path)
    case = _mid_stream_container(tmp_path)
    out = tmp_path / "partial.unity3d"
    ui.install(_add_plan(case), out, patch_store=store)
    assert len(store.pending().targets) == 3
    raw = json.loads(store.json_path.read_text(encoding="utf-8"))
    ref = next(t["old_bytes"] for t in raw["targets"] if t["kind"] == "range")
    blob = bytearray(store.blob_path.read_bytes())
    blob[ref["offset"]] ^= 0xFF
    store.blob_path.write_bytes(bytes(blob))
    raw["blob_sha256"] = hashlib.sha256(bytes(blob)).hexdigest()
    store.json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssetError):
        store.restore()
    assert reverse_delta.sha256_of(real) == sha_bundle, "包没回到改前"
    assert reverse_delta.sha256_of(catalog) == sha_catalog, "catalog 没回到改前"
    left = store.pending()
    assert left is not None, "没还原回去的那条不该被删掉"
    assert [t.kind for t in left.targets] == ["range"], "已还原的目标还留在柜里"

