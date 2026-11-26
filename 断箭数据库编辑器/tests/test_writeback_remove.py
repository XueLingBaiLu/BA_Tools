"""2026-10-06 · 对象层「删除」判据（用户口径 AGENTS §0.10：产品只有「新增」与「删除」）。

**判据一行**：pytest tests/test_writeback_remove.py -q -> 通过数 **从 0 收集变 N passed**

| 用例 | 判据 | 通过线 |
|---|---|---|
| test_r1_* | 新表 == 旧表**去掉那一条**（其余逐项全等）；老对象按原坐标逐字节读回 == 改前 | 相等 |
| test_r1_metadata_is_equal_length_* | Δmetadata_size / Δdata_offset == 0（等长档） | 全 0 |
| test_r1_metadata_rule_* | 元数据区 == 原区间**左移 24N** ＋ 尾部 24N 个零 | 逐字节 |
| test_deleted_pid_* | 被删 pathID：旧表 1 命中 / 新表 0 命中 | 命中数 |
| test_round_trip_* | 删除 →（反向 Δ）还原 ⇒ **整文件 sha256 == 改前** | 相等 |
| test_negative_control_* | 删不存在的 pathID ⇒ 抛错，且**一个字节都没写** | 能红 |
| test_multi_* | 一次删 2 条（不相邻）| 只剩中间那条，且逐字节没动 |

设计（Lead 2026-10-06）：删除 = **等长重写元数据区尾巴**（表少几条 ⇒ 后面左移 ⇒ 尾部补零），
数据区与所有老对象坐标**一个字节都不动**；被删对象的字节留成死区（安全档）。
"""

from __future__ import annotations

import base64
import dataclasses
import shutil
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone, reverse_delta, writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError
from test_writeback_l1 import NODE, _blob, _build_container, _build_sf, _objects


def _bundle(tmp_path: Path) -> Path:
    """与 test_writeback_l1 同构：3 个对象（1001 / 1002 / 1003）。"""
    objs = [
        (1001, _blob(b"A", 130_000)),
        (1002, _blob(b"B", 200_000)),
        (1003, _blob(b"C", 200_000)),
    ]
    path = tmp_path / "synth_remove.bundle"
    path.write_bytes(_build_container(_build_sf(objs)))
    return path


def _table(path: Path) -> list[tuple[int, int, int, int, int]]:
    with ufs.UfsReader(path) as reader:
        info = ufs.read_serialized_file(reader, reader.node(NODE))
        return list(info.objects)


def _sf(path: Path) -> ufs.SerializedFileInfo:
    with ufs.UfsReader(path) as reader:
        return ufs.read_serialized_file(reader, reader.node(NODE))


def _stream_len(path: Path) -> int:
    """解压流总长 —— 等长档要守的是它，**不是**容器字节数（重压那几块长度会差几个字节）。"""
    with ufs.UfsReader(path) as reader:
        return reader.total_uncompressed


def _remove(path: Path, *pids: int, catalog: Path | None = None, **kwargs: object):
    removals = [writer.ObjectRemoval(path_id=pid, name=f"#{pid}") for pid in pids]
    return writer.write_removals(
        path,
        removals,
        dry_run=False,
        game_root=path.parent,
        catalog_path=catalog,
        **kwargs,
    )


def test_r1_single_delete_keeps_everything_else_byte_identical(tmp_path: Path) -> None:
    """R1 主判据：表少一条、其余逐项全等；老对象按**原坐标**读回逐字节 == 改前。"""
    bundle = _bundle(tmp_path)
    before = _objects(bundle)
    table_before = _table(bundle)
    sf_before = _sf(bundle)
    stream_before = _stream_len(bundle)

    report = _remove(bundle, 1002)
    entry = report.verify["R1 删除对象"]
    assert entry["ok"] is True, entry

    after = _objects(bundle)
    assert 1002 not in after, "被删的对象还在"
    assert after[1001] == before[1001] and after[1003] == before[1003], "别的对象被动了"
    table_after = _table(bundle)
    assert [row[1] for row in table_after] == [1001, 1003]
    kept = [row for row in table_before if row[1] != 1002]
    assert [row[1:] for row in table_after] == [row[1:] for row in kept], "表项不是逐项相等"
    sf_after = _sf(bundle)
    assert sf_after.metadata_size == sf_before.metadata_size
    assert sf_after.data_offset == sf_before.data_offset
    # ⚠ 容器字节数**允许**变（重压那几个块的长度会差几字节）；等长守的是解压流长度。
    assert _stream_len(bundle) == stream_before
    assert report.plan.rows[0].level == writer.LEVEL_EQUAL


def test_r1_metadata_rule_shift_left_and_pad_zeros(tmp_path: Path) -> None:
    """等长定律：原区间**左移 24N** ＋ 尾部补 24N 个零（逐字节比，比增量式更难糊弄）。"""
    bundle = _bundle(tmp_path)
    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    sf = _sf(bundle)
    stride = sf.object_entry_stride

    _remove(bundle, 1001)
    with ufs.UfsReader(original) as old_reader:
        old_node = old_reader.node(NODE)
        old_sf = ufs.read_serialized_file(old_reader, old_node)
        meta_end = old_node.offset + ufs.SF_HEADER_SIZE + old_sf.metadata_size
        start = old_node.offset + old_sf.object_table_offset + 0 * stride
        before = bytes(old_reader.read_range(start, meta_end - start))
    with ufs.UfsReader(bundle) as new_reader:
        after = bytes(new_reader.read_range(start, meta_end - start))
    assert after == before[stride:] + b"\x00" * stride, "元数据区不是「左移 + 尾零」"


def test_multi_delete_non_adjacent_rows(tmp_path: Path) -> None:
    """一次删 2 条（不相邻：1001 与 1003）⇒ 只剩 1002，且它的字节逐字节没动。"""
    bundle = _bundle(tmp_path)
    before = _objects(bundle)
    report = _remove(bundle, 1001, 1003)
    assert report.verify["R1 删除对象"]["ok"] is True, report.verify["R1 删除对象"]
    after = _objects(bundle)
    assert list(after) == [1002]
    assert after[1002] == before[1002]
    assert len(report.plan.removed_objects) == 2


def test_round_trip_delete_then_restore_is_byte_identical(tmp_path: Path) -> None:
    """删完还能撤：反向 Δ 还原 ⇒ **整文件 sha256 == 改前**（用户口径里「各自可撤销」这条）。"""
    bundle = _bundle(tmp_path)
    real = tmp_path / ("synth0_assets_all_" + "0" * 32 + ".bundle")
    shutil.copy2(bundle, real)
    sha_before = reverse_delta.sha256_of(real)
    store = reverse_delta.PatchStore(tmp_path / "patches", game_root=tmp_path)

    report = _remove(real, 1002, patch_store=store, patch_label="删除对象")
    assert report.patch is not None
    assert reverse_delta.sha256_of(real) != sha_before

    result = store.restore()
    assert result.ok, result.lines()
    assert reverse_delta.sha256_of(real) == sha_before, "还原后不是改前那份"
    assert not store.has_pending()


def test_negative_control_unknown_pid_writes_nothing(tmp_path: Path) -> None:
    """负控（尺子先做正控）：删一个不存在的 pathID ⇒ 抛错，且文件一个字节不动。"""
    bundle = _bundle(tmp_path)
    sha_before = reverse_delta.sha256_of(bundle)
    with pytest.raises(KeyError):
        _remove(bundle, 4242)
    assert reverse_delta.sha256_of(bundle) == sha_before, "报错了却动了文件"


def test_negative_control_deleting_every_object_is_refused(tmp_path: Path) -> None:
    """负控（2026-10-06 · objadd 的负控刨出来的产品缺口）：**不许把对象删空**。

    改前：plan 放行、write 阶段被一个与语义无关的越界断言偶然挡住，而且补丁与临时文件已经写出去了。
    改后：plan 阶段就抛，且**一个字节都不写**。
    """
    bundle = _bundle(tmp_path)
    sha_before = reverse_delta.sha256_of(bundle)
    with pytest.raises(AssetError):
        _remove(bundle, 1001, 1002, 1003)
    assert reverse_delta.sha256_of(bundle) == sha_before, "报错了却动了文件"
    # 正控：删 2 条（还剩 1 条）**必须允许** —— 守卫只拦「删空」，不拦「删到只剩一条」
    report = _remove(bundle, 1001, 1002)
    assert report.verify["R1 删除对象"]["ok"] is True, report.verify["R1 删除对象"]
    assert list(_objects(bundle)) == [1003]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """默认 dry_run：算得出计划，包一个字节都没动。"""
    bundle = _bundle(tmp_path)
    sha_before = reverse_delta.sha256_of(bundle)
    plan = writer.plan_removals(bundle, [writer.ObjectRemoval(path_id=1002)])
    assert plan.removed_objects == ((1002, 1),)
    assert plan.blocked_reason == ""
    assert reverse_delta.sha256_of(bundle) == sha_before
    text = "\n".join(plan.criterion_lines())
    assert "删除" in text and "L1" in text


# --------------------------------------------------------------------------- #
# #6c（2026-10-06）：删对象时**同时摘引用** —— AssetBundle 的 m_Container / m_PreloadTable
#
# 真机铁律（kb/14 §17）：只摘对象表条目、把 AssetBundle 里指它的引用留着 ⇒
# **进游戏 UI 整块消失**（包加载失败）。所以删对象必须同时把那两处处理掉。
#
# 夹具用的是**录下来的真类型树**：下面两段 base64 是 unitlabels 包里 classID 142
# （AssetBundle）那一项的**逐字节制品**（录法见 _work\delref_typetree.py）——
# clone 的 docstring 明确支持「录下来的一段真 blob」当夹具，不必开真包。
# --------------------------------------------------------------------------- #
_AB_PREFIX = base64.b64decode(
    "AAAAAAAAAAAAAAAWAAAAAAAAAAAAAPFJAAAAAAAUeoAAAAAAAADxgAAAAAAAAAAAMjAyMi4zLjYyZjMAEwAAAAEDAAAA"
)
_AB_ENTRY = base64.b64decode(
    "jgAAAAD//5faX0aI5FpXyLQtT0JJcpc6AAAA8gAAAAMAAAAAAAAANwAAgP////8AAAAAAIAAAAAAAAAAAAAAAQABAEgDAICr"
    "AQCA/////wEAAAABgAgAAAAAAAAAAAABAAIBMQAAgDEAAID/////AgAAAAFACAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAAD"
    "AAAAAQAIAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAAAQAAAABAAgAAAAAAAAAAAABAAEA1QMAgAwAAAD/////BQAAAACAAAAA"
    "AAAAAAAAAAEAAgExAACAMQAAgP////8GAAAAAEAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAAAcAAAAAAAAAAAAAAAAAAAAB"
    "AAMAeQIAgGoAAIAMAAAACAAAAAAAAAAAAAAAAAAAAAEABADeAACAGwAAAAQAAAAJAAAAAQCAAAAAAAAAAAAAAQAEAC4DAIAk"
    "AAAACAAAAAoAAAABAIAAAAAAAAAAAAABAAEA8QAAgC0AAAD/////CwAAAACAAAAAAAAAAAAAAAEAAgExAACAMQAAgP////8M"
    "AAAAAIAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAAA0AAAAAAAAAAAAAAAAAAAABAAMAHwIAgGoAAID/////DgAAAACAAAAA"
    "AAAAAAAAAAEABABIAwCAmwAAgP////8PAAAAAIAAAAAAAAAAAAAAAQAFATEAAIAxAACA/////xAAAAABQAAAAAAAAAAAAAAB"
    "AAYA3gAAgBsDAIAEAAAAEQAAAAEAAAAAAAAAAAAAAAEABgBRAACAagAAgAEAAAASAAAAAQAAAAAAAAAAAAAAAQAEADkAAAAK"
    "AwCAFAAAABMAAAAAAAAAAAAAAAAAAAABAAUA3gAAgEMAAAAEAAAAFAAAAAAAAAAAAAAAAAAAAAEABQDeAACAUAAAAAQAAAAV"
    "AAAAAAAAAAAAAAAAAAAAAQAFAHkCAIBcAAAADAAAABYAAAAAAAAAAAAAAAAAAAABAAYA3gAAgBsAAAAEAAAAFwAAAAEAgAAA"
    "AAAAAAAAAAEABgAuAwCAJAAAAAgAAAAYAAAAAQCAAAAAAAAAAAAAAQABADkAAABiAAAAFAAAABkAAAAAAAAAAAAAAAAAAAAB"
    "AAIA3gAAgEMAAAAEAAAAGgAAAAAAAAAAAAAAAAAAAAEAAgDeAACAUAAAAAQAAAAbAAAAAAAAAAAAAAAAAAAAAQACAHkCAIBc"
    "AAAADAAAABwAAAAAAAAAAAAAAAAAAAABAAMA3gAAgBsAAAAEAAAAHQAAAAEAgAAAAAAAAAAAAAEAAwAuAwCAJAAAAAgAAAAe"
    "AAAAAQCAAAAAAAAAAAAAAQABAKYDAIBuAAAABAAAAB8AAAAAAAAAAAAAAAAAAAABAAEASAMAgIUAAAD/////IAAAAACAAAAA"
    "AAAAAAAAAAEAAgExAACAMQAAgP////8hAAAAAUAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAACIAAAABAAAAAAAAAAAAAAAB"
    "AAMAUQAAgGoAAIABAAAAIwAAAAEAAAAAAAAAAAAAAAEAAQDVAwCAlwAAAP////8kAAAAAIAAAAAAAAAAAAAAAQACATEAAIAx"
    "AACA/////yUAAAAAwAAAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAJgAAAAAAAAAAAAAAAAAAAAEAAwBIAwCAagAAgP////8n"
    "AAAAAIAAAAAAAAAAAAAAAQAEATEAAIAxAACA/////ygAAAABQAAAAAAAAAAAAAABAAUA3gAAgBsDAIAEAAAAKQAAAAEAAAAA"
    "AAAAAAAAAAEABQBRAACAagAAgAEAAAAqAAAAAQAAAAAAAAAAAAAAAQABAEwAAICmAAAAAQAAACsAAAAAQAAAAAAAAAAAAAAB"
    "AAEA3gAAgMMAAAAEAAAALAAAAAAAAAAAAAAAAAAAAAEAAQDeAACA2AAAAAQAAAAtAAAAAAAAAAAAAAAAAAAAAQABAPEAAIDk"
    "AAAA/////y4AAAAAgAAAAAAAAAAAAAABAAIBMQAAgDEAAID/////LwAAAACAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAAw"
    "AAAAAAAAAAAAAAAAAAAAAQADAB8CAIBqAACA/////zEAAAAAgAAAAAAAAAAAAAABAAQASAMAgJsAAID/////MgAAAACAAAAA"
    "AAAAAAAAAAEABQExAACAMQAAgP////8zAAAAAUAAAAAAAAAAAAAAAQAGAN4AAIAbAwCABAAAADQAAAABAAAAAAAAAAAAAAAB"
    "AAYAUQAAgGoAAIABAAAANQAAAAEAAAAAAAAAAAAAAAEABABIAwCACgMAgP////82AAAAAIAAAAAAAAAAAAAAAQAFATEAAIAx"
    "AACA/////zcAAAABQAAAAAAAAAAAAAABAAYA3gAAgBsDAIAEAAAAOAAAAAEAAAAAAAAAAAAAAAEABgBRAACAagAAgAEAAAA5"
    "AAAAAQAAAAAAAAAAAAAAQXNzZXRCdW5kbGUAbV9QcmVsb2FkVGFibGUAbV9GaWxlSUQAbV9QYXRoSUQAbV9Db250YWluZXIA"
    "QXNzZXRJbmZvAHByZWxvYWRJbmRleABwcmVsb2FkU2l6ZQBhc3NldABtX01haW5Bc3NldABtX1J1bnRpbWVDb21wYXRpYmls"
    "aXR5AG1fQXNzZXRCdW5kbGVOYW1lAG1fRGVwZW5kZW5jaWVzAG1fSXNTdHJlYW1lZFNjZW5lQXNzZXRCdW5kbGUAbV9FeHBs"
    "aWNpdERhdGFMYXlvdXQAbV9QYXRoRmxhZ3MAbV9TY2VuZUhhc2hlcwAAAAAA"
)
#: 树 blob 在项内偏移 = 4(classID) + 1(is_stripped) + 2(script_index) + 16(old_type_hash)
#: = 23（这里 script_index = -1 ⇒ 没有那 16 B 的 script_id）。
_AB_NODES = clone.read_type_nodes(_AB_ENTRY[23:], common_strings=clone.unitypy_common_strings())


def _ab_payload(
    container: list[tuple[str, int]],
    preload_ids: list[int],
    windows: list[tuple[int, int]] | None = None,
) -> bytes:
    """AssetBundle 对象字节：骨架取**产品自己的配方**，两条数组按真件形状显式给。

    ⚠ builder.assetbundle_fields(preload_ids=…) 会把**所有**容器条目的窗口写成 (0, N)
    （一整张表一个窗口）——那是它的口径，不是真件形状；真件是**每条目一个窗口**盖住该资产的对象集
    （实测 unitlabels：preloadIndex=1288 / preloadSize=2）。这里显式给 windows。
    """
    fields = builder.assetbundle_fields(
        bundle_name="synthrefs.bundle", container=container, preload_ids=preload_ids
    )
    fields["m_PreloadTable"] = [{"m_FileID": 0, "m_PathID": int(pid)} for pid in preload_ids]
    if windows is not None:
        rows = []
        for (path, pid), (index, size) in zip(container, windows, strict=True):
            rows.append(
                {
                    "first": str(path),
                    "second": {
                        "preloadIndex": int(index),
                        "preloadSize": int(size),
                        "asset": {"m_FileID": 0, "m_PathID": int(pid)},
                    },
                }
            )
        fields["m_Container"] = rows
    return clone.write_object(fields, _AB_NODES)


#: 第二张类型表项：把录制项的 classID 改成 28（Texture2D）——**只为了让表项 typeID 能区分**
#: （product 只按 classID == 142 找 AssetBundle；这一项永远不会被解，只是占个下标）。
_TEX_ENTRY = struct.pack("<i", 28) + _AB_ENTRY[4:]


def _build_sf_ab(objs: list[tuple[int, bytes]], pool: bytes = b"") -> bytes:
    """带**真类型表**（0 = 真 AssetBundle / 1 = 占位 Texture2D）的最小 SerializedFile v22。

    ⚠ 所有非 AssetBundle 的对象（贴图）都用 typeID = 1；AssetBundle 用 0。
    """
    body = bytearray()
    body += b"2022.3.62f3\x00"
    body += struct.pack("<i", 19)
    body += b"\x01"                                   # enable_type_tree = 1
    body += struct.pack("<i", 2)                      # type_count = 2
    body += _AB_ENTRY                                 # 0：录制的真 AssetBundle 项
    body += _TEX_ENTRY                                # 1：占位（classID 28）
    body += struct.pack("<i", len(objs))
    cursor = 0
    table: list[tuple[int, int, int]] = []
    for pid, payload in objs:
        table.append((pid, cursor, len(payload)))
        cursor += len(payload)
    for index, (pid, stored, size) in enumerate(table):
        body += bytes((-len(body)) % 4)
        body += struct.pack("<qqii", pid, stored, size, 0 if index == 0 else 1)
    body += struct.pack("<i", 0)                      # script types
    body += struct.pack("<i", 1)                      # externals
    body += b"Library/unity default resources" + bytes(1) + bytes(16)
    body += struct.pack("<i", 0) + bytes(1)
    body += struct.pack("<i", 0)                      # ref types
    body += bytes(1)                                  # userInformation
    body += bytes(pool)
    metadata_size = len(body)
    data_offset = 48 + metadata_size                    # ⚠ 是 align16(48 + metadata_size)
    data_offset += (-data_offset) % 16
    total = data_offset + cursor
    head = bytearray(48)
    struct.pack_into(">I", head, 8, 22)
    struct.pack_into(">I", head, 20, metadata_size)
    struct.pack_into(">q", head, 24, total)
    struct.pack_into(">q", head, 32, data_offset)
    gap = bytes(data_offset - 48 - metadata_size)
    return bytes(head) + bytes(body) + gap + b"".join(p for _pid, p in objs)


def _ref_bundle(
    tmp_path: Path,
    *,
    paths: list[str],
    pids: list[int],
    preload: list[int],
    windows: list[tuple[int, int]],
) -> Path:
    """一个「真形状」的 AssetBundle 夹具：容器 N 条（各自一个窗口）+ 预载表按需重复 pathID。"""
    objs = [(1, _ab_payload(list(zip(paths, pids, strict=True)), list(preload), list(windows)))]
    objs += [(pid, _blob(bytes([65 + i]) * 4, 300 + i)) for i, pid in enumerate(pids)]
    path = tmp_path / "synth_refs.bundle"
    path.write_bytes(_build_container(_build_sf_ab(objs)))
    return path


def _ab_tree(path: Path, pid: int = 1) -> dict:
    with ufs.UfsReader(path) as reader:
        node = reader.node(NODE)
        info = ufs.read_serialized_file(reader, node)
        for _i, row_pid, stored, size, _t in info.objects:
            if row_pid != pid:
                continue
            payload = bytes(reader.read_range(node.offset + info.data_offset + stored, size))
            return clone.read_object(payload, _AB_NODES)
    raise AssertionError(f"包里没有 pathID={pid}")


def _refs_of(tree: dict, pid: int) -> list:
    return [
        ref
        for ref in clone.collect_pptrs(tree, _AB_NODES, owner="AssetBundle")
        if int(ref.file_id) == 0 and int(ref.path_id) == pid
    ]


def _four_object_fixture(tmp_path: Path) -> Path:
    """A→1001 / B→1002（预载里出现两次）/ C→1003；删 1002 时 C 的窗口要从 3 重编号到 1。"""
    return _ref_bundle(
        tmp_path,
        paths=["Assets/A.png", "Assets/B.png", "Assets/C.png"],
        pids=[1001, 1002, 1003],
        preload=[1001, 1002, 1002, 1003],          # B 的对象在预载里出现两次（都要摘）
        windows=[(0, 1), (1, 2), (3, 1)],          # A / B / C 各一个窗口（真件形状）
    )


def test_ref_default_is_trim() -> None:
    """默认必须**同时摘引用**（trim）——「只摘条目」那个形状已经在真机上把 UI 干掉了。"""
    assert writer.ObjectRemoval(path_id=1).refs == "trim"
    assert writer.REF_MODES == ("trim", "null", "keep")


def test_ref_trim_clears_container_and_all_preload_slots(tmp_path: Path) -> None:
    """trim：命中的容器条目摘掉、预载里**同一 pathID 出现几次就摘几次**、后面的窗口下标重编号。"""
    bundle = _four_object_fixture(tmp_path)
    old_size = [row[3] for row in _table(bundle) if row[1] == 1][0]
    assert len(_refs_of(_ab_tree(bundle), 1002)) == 3, "夹具本身就该有 3 条指向 1002 的引用"

    report = _remove(bundle, 1002)
    assert report.verify["R1 删除对象"]["ok"] is True, report.verify["R1 删除对象"]

    mode, ab_pid, old_bytes, new_bytes, cleared = report.plan.ref_cleanup[0]
    assert (mode, ab_pid) == ("trim", 1)
    assert cleared == 3, f"该处理 3 条（容器 1 + 预载 2），实得 {cleared}"
    assert old_bytes == old_size and new_bytes < old_bytes, (old_bytes, new_bytes)

    tree = _ab_tree(bundle)
    assert _refs_of(tree, 1002) == [], "还有指向被删对象的引用（真机上这就是 UI 全灭）"
    assert [row["first"] for row in tree["m_Container"]] == ["Assets/A.png", "Assets/C.png"]
    assert [int(item["m_PathID"]) for item in tree["m_PreloadTable"]] == [1001, 1003]
    assert int(tree["m_Container"][1]["second"]["preloadIndex"]) == 1, "窗口没重编号"
    assert [r for r in _table(bundle) if r[1] == 1][0][3] == new_bytes, "表项 byteSize 没跟着改"
    assert list(_objects(bundle)) == [1, 1001, 1003]


def test_ref_null_keeps_length_and_zeroes_pointers(tmp_path: Path) -> None:
    """null 档：数组长度不变 ⇒ 等长；命中的 PPtr 改成 (0,0)；窗口下标无需重编号。"""
    bundle = _four_object_fixture(tmp_path)
    old_size = [row[3] for row in _table(bundle) if row[1] == 1][0]
    report = writer.write_removals(
        bundle,
        [writer.ObjectRemoval(path_id=1002, name="#1002", refs="null")],
        dry_run=False,
        game_root=bundle.parent,
    )
    assert report.verify["R1 删除对象"]["ok"] is True, report.verify["R1 删除对象"]
    mode, _pid, old_bytes, new_bytes, cleared = report.plan.ref_cleanup[0]
    assert (mode, cleared) == ("null", 3)
    assert (old_bytes, new_bytes) == (old_size, old_size), "null 档不许改长度"

    tree = _ab_tree(bundle)
    assert _refs_of(tree, 1002) == []
    assert [row["first"] for row in tree["m_Container"]] == [
        "Assets/A.png", "Assets/B.png", "Assets/C.png"
    ], "null 档不摘条目"
    assert int(tree["m_Container"][1]["second"]["asset"]["m_PathID"]) == 0
    assert [int(item["m_PathID"]) for item in tree["m_PreloadTable"]] == [1001, 0, 0, 1003]
    assert int(tree["m_Container"][2]["second"]["preloadIndex"]) == 3, "null 档不该重编号"
    assert [r for r in _table(bundle) if r[1] == 1][0][3] == old_size


def test_ref_keep_is_the_dangling_shape(tmp_path: Path) -> None:
    """负控/对照：refs="keep" = 老行为 ⇒ 条目摘了、引用**一条不少**（真机上 UI 整块消失）。

    ⛔ 这条**不是**产品口径，只用来证明「漏摘一处」时尺子量得出悬空引用。
    """
    bundle = _four_object_fixture(tmp_path)
    report = writer.write_removals(
        bundle,
        [writer.ObjectRemoval(path_id=1002, name="#1002", refs="keep")],
        dry_run=False,
        game_root=bundle.parent,
    )
    assert report.verify["R1 删除对象"]["ok"] is True      # 老行为自己仍然「合规」
    assert report.plan.ref_cleanup == ()
    assert len(_refs_of(_ab_tree(bundle), 1002)) == 3, "keep 档就该留着 3 条悬空引用"
    assert list(_objects(bundle)) == [1, 1001, 1003]


def test_ref_mode_mismatch_is_refused_without_writing(tmp_path: Path) -> None:
    """同一次删除里 refs 不一致 ⇒ 抛错，且文件一个字节不动。"""
    bundle = _four_object_fixture(tmp_path)
    sha_before = reverse_delta.sha256_of(bundle)
    with pytest.raises(AssetError):
        writer.write_removals(
            bundle,
            [
                writer.ObjectRemoval(path_id=1001, refs="trim"),
                writer.ObjectRemoval(path_id=1002, refs="null"),
            ],
            dry_run=False,
            game_root=bundle.parent,
        )
    assert reverse_delta.sha256_of(bundle) == sha_before


def test_ref_verify_removed_catches_a_missing_bytesize_patch(tmp_path: Path) -> None:
    """负控（尺子先做正控）：**漏改 AssetBundle 那条表项的 byteSize** ⇒ 写后复核必须报红。"""
    bundle = _four_object_fixture(tmp_path)
    before_copy = tmp_path / "before.bundle"
    shutil.copy2(bundle, before_copy)
    report = _remove(bundle, 1002)
    assert report.plan.ref_cleanup, "先确认这一次真的做了引用清理"
    mode, pid, old_bytes, _new_bytes, cleared = report.plan.ref_cleanup[0]
    forged = dataclasses.replace(
        report.plan, ref_cleanup=((mode, pid, old_bytes, old_bytes, cleared),)
    )
    with ufs.UfsReader(before_copy) as old_reader, ufs.UfsReader(bundle) as new_reader:
        verdict = writer._verify_removed(old_reader, new_reader, forged)
    assert verdict["ok"] is False, "谎报「长度没变」却没被复核抓住"
    assert "byteSize" in verdict["detail"] or "变短" in verdict["detail"], verdict["detail"]

