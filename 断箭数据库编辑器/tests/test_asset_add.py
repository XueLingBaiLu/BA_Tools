r"""资产新增（`newasset add` / `newasset restore`）的判据 —— 一条命令：PNG -> 游戏里出现。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_asset_add.py -q`
→ 通过用例数 **从 0（本文件不存在）变 22 passed**（另有一条标了 slow，
它要**只读**打开游戏里 17 MB 的 unitlabels 包；`pytest --all` 时 +1 passed）。

⛔ 全部用例都在 `tmp_path` 造的**假游戏根**上跑 —— 一个字节都不写 `D:\Steam\...`。

| 用例组 | 判据 |
|---|---|
| 图片适配 blur / plain / fill | 416x216；主体 216x216 居中在 x=100；三种构图的"绿角"各归各位 |
| 行序 | flip 自逆，且**真的翻了**（首行 == 原末行） |
| 键 / 包名 | 同名同键 ⇒ 同名包；m_Hash == 文件名 _all_ 尾段（真件口径，不是 md5(文件)） |
| Units 那一行 | 重名 fail-closed（真机实测 542 行里 13 个重名）；只重写 Units.json |
| catalog | 计数头 +2 / +2 / +3（与 kb/14 §8 第二批同形） |
| 一条链 | 装完 → restore → **整棵游戏树的 sha256 逐条回到装前**；写盘炸则用补丁撤回 |

**三条正控**（AGENTS §0.9 动作 2：尺子先能红）：

① 行序不翻 ⇒ 按游戏方向比**不相等**（这里是「存储序 != 源图」那一行）；
② 装完手改 1 字节 ⇒ `restore` **必须拒绝**，且那个文件一个字节不动、补丁还在；
③ Units.json 不是我们的序列化口径（带缩进）⇒ 计划阶段就**拒绝**（否则反向 Δ ≈ 整包）。
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from _fake_dll import fake_dll_bytes  # tests/ 由 pytest 插进 sys.path
from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.asset_kernel import reverse_delta as rd
from ba_mod_maker.core.asset import dbzip, dllpatch, external_db, new_asset, ufs
from ba_mod_maker.core.assets.game import bundle_dir, find_game_root
from ba_mod_maker.core.errors import AssetError
from test_dllpatch import _pretend_original


@pytest.fixture(autouse=True)
def _fake_dll_is_a_known_original(monkeypatch):
    """本文件的假游戏根拿 `fake_dll_bytes()` 当原版 DLL ⇒ 得让它对得上 `BUILDS`。

    2026-10-14 起地址表**按原版 sha256 分版本**：`apply()` 认不出版本就（正确地）拒绝，
    夹具要冒充原版只能给它造一个假 `Build`（手法与 tests/test_dllpatch.py 同一份）。
    """
    _pretend_original(monkeypatch, fake_dll_bytes())

# --------------------------------------------------------------------------- #
# 合成件（手搓，不用被测代码拼结构）
# --------------------------------------------------------------------------- #
KEY = "ASSET_ADD_TEST-Label"
UNIT_UNIQUE = "SYNTH_UNIQUE"
UNIT_DUP = "SYNTH\\DUP_base"
ALPHA = "alpha_assets_all_" + "1" * 32 + ".bundle"
BETA = "beta_assets_all_" + "2" * 32 + ".bundle"
GROUP = "group/dep1"
ADDR_ONE = "addr/one"
SHARED_ASSET = "Assets/Synth/Shared.prefab"
GREEN = (30, 200, 30, 255)


def _source_png(path: Path, *, size: tuple[int, int] = (200, 200)) -> Path:
    """合成源图：蓝底 + 正中红方块 + **左上角一块绿**（绿角是量构图用的记号）。"""
    width, height = size
    image = Image.new("RGBA", size, (0, 40, 200, 255))
    image.paste((230, 30, 30, 255), (width // 4, height // 4, width * 3 // 4, height * 3 // 4))
    image.paste(GREEN, (0, 0, width // 8, height // 8))
    image.save(path)
    return path


@pytest.fixture()
def source_png(tmp_path: Path) -> Path:
    return _source_png(tmp_path / "source.png")


def _pixel(rgba: bytes, x: int, y: int, width: int = new_asset.LABEL_WIDTH) -> tuple[int, ...]:
    at = (y * width + x) * 4
    return tuple(rgba[at : at + 4])


def _count_color(rgba: bytes, color: tuple[int, ...]) -> int:
    want = bytes(color)
    return sum(1 for at in range(0, len(rgba), 4) if rgba[at : at + 4] == want)


def _units_payload(rows: list[dict], *, indent: int | None = None) -> bytes:
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), indent=indent)
    return text.encode("utf-8")


def _units_rows() -> list[dict]:
    return [
        {"Id": 1, "Name": "唯一", "ModelFileName": UNIT_UNIQUE, "ThumbnailFileName": "ONE-Label"},
        {"Id": 90, "Name": "重名甲", "ModelFileName": UNIT_DUP, "ThumbnailFileName": "DUP-Label"},
        {
            "Id": 478,
            "Name": "重名乙",
            "ModelFileName": UNIT_DUP,
            "ThumbnailFileName": "SYNTH_DUP_CMD-Label",
        },
    ]


def _write_zip(path: Path, units: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    dbzip.DbZip(tables={"Units": units, "Armors": b'[{"Id":1}]'}).write(path)
    return path


def _synth_catalog() -> dict:
    """一份最小但合规的 catalog：2 个包 + 1 个依赖组 + 1 条地址。"""
    internal = [
        cw.RUNTIME_PATH + cw.PC_SEGMENT + ALPHA,
        cw.RUNTIME_PATH + cw.PC_SEGMENT + BETA,
        SHARED_ASSET,
    ]
    extra = bytearray()
    offsets: list[int] = []
    for key, crc in ((ALPHA, 111), (BETA, 222)):
        offsets.append(len(extra))
        bundle = cw.CatalogBundle(
            key, key[0] * 32, crc, 1000 + crc, internal_name=key[: -len(".bundle")]
        )
        extra += cw.extra_data_blob(bundle.options())
    key_values = (ALPHA, BETA, GROUP, ADDR_ONE)
    key_blobs = [cw.key_bytes(cw.KEY_UTF8, value) for value in key_values]
    key_offsets: list[int] = []
    pos = 4
    for blob in key_blobs:
        key_offsets.append(pos)
        pos += len(blob)
    buckets = [[0], [1], [0, 1], [2]]
    entries = [
        [0, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH, offsets[0], 0, 0],
        [1, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH, offsets[1], 1, 0],
        [2, cw.BUNDLED_ASSET_PROVIDER, 2, 999, -1, 3, 1],
    ]
    key_data = bytearray(struct.pack("<i", len(key_blobs)))
    for blob in key_blobs:
        key_data += blob
    bucket_data = bytearray(struct.pack("<i", len(buckets)))
    for index, members in enumerate(buckets):
        bucket_data += struct.pack("<ii", key_offsets[index], len(members))
        bucket_data += struct.pack(f"<{len(members)}i", *members)
    entry_data = bytearray(struct.pack("<i", len(entries)))
    for row in entries:
        entry_data += struct.pack("<7i", *row)
    return {
        "m_LocatorId": "AddressablesMainContentCatalog",
        "m_BuildResultHash": "0" * 32,
        "m_ProviderIds": [
            "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider",
            "UnityEngine.ResourceManagement.ResourcesProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
            "",
        ],
        "m_InternalIds": internal,
        "m_KeyDataString": __import__("base64").b64encode(bytes(key_data)).decode("ascii"),
        "m_BucketDataString": __import__("base64").b64encode(bytes(bucket_data)).decode("ascii"),
        "m_EntryDataString": __import__("base64").b64encode(bytes(entry_data)).decode("ascii"),
        "m_ExtraDataString": __import__("base64").b64encode(bytes(extra)).decode("ascii"),
        "m_resourceTypes": [
            {"m_AssemblyName": "Unity.RM", "m_ClassName": "GameObject"},
            {"m_AssemblyName": "Unity.RM", "m_ClassName": "Texture2D"},
        ],
        "m_InternalIdPrefixes": [],
    }


def _catalog_bytes(raw: dict) -> bytes:
    return json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _make_game(tmp_path: Path, *, dll_patched: bool = False) -> tuple[Path, Path]:
    """假游戏根 + 工程目录（catalog / BA_Mod\\BA_Mod.zip / aa\\PC / 合成 GameAssembly.dll）。"""
    root = tmp_path / "game"
    aa = root / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    pc = aa / "PC"
    pc.mkdir(parents=True)
    (aa / "catalog.json").write_bytes(_catalog_bytes(_synth_catalog()))
    external_db.ensure_zip_dir(root)
    _write_zip(external_db.game_zip_path(root), _units_payload(_units_rows()))
    (pc / "unitlabels_dummy.bundle").write_bytes(b"meta source placeholder")
    (pc / "ammoicons_dummy.bundle").write_bytes(b"sprite source placeholder")
    original = fake_dll_bytes()
    (root / dllpatch.BACKUP_NAME).write_bytes(original)
    (root / dllpatch.DLL_NAME).write_bytes(
        dllpatch.patched_image(original) if dll_patched else original
    )
    project = tmp_path / "project"
    project.mkdir()
    return root, project


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    return _make_game(tmp_path)[0]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


def _snapshot(root: Path) -> dict[str, str]:
    """整棵树的"逐字节"读数：相对路径 -> sha256。"""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _fake_build_bundle(**kwargs) -> builder.MinBundlePlan:
    """把造包换成一份**合成容器**（tmp 里的最小包）—— 链路的其余部分照真跑。"""
    name = new_asset.bundle_file_name(str(kwargs["key"]))
    stream = f"synth stream for {name}".encode()
    container = builder.build_unityfs(
        stream, builder.node_path_for(name), block_type=builder.BLOCK_NONE
    )
    return builder.MinBundlePlan(
        data=container.data,
        crc32=builder.crc32_of_stream(stream),
        serialized_file_size=len(stream),
        node_path=container.node_path,
        serialized=None,
        unityfs=container,
        container=(),
        texture_path_id=2,
        assetbundle_path_id=1,
        sprite_path_id=None,
        type_class_ids=(),
    )


def _plan(
    game: Path, project: Path, source_png: Path, *, unit: str = UNIT_DUP, row_id: int | None = 478
):
    return new_asset.plan_add(
        png=source_png,
        unit=unit,
        key=KEY,
        row_id=row_id,
        game_root=game,
        project_root=project,
    )


# --------------------------------------------------------------------------- #
# ① 图片适配
# --------------------------------------------------------------------------- #
def test_fit_blur_keeps_the_subject_centred_and_dims_the_sides(source_png: Path) -> None:
    """主体等比缩到 216x216 居中（左边缘在 x=100），两侧是压暗过的模糊背景。"""
    out = new_asset.fit_image(source_png, fit="blur")
    assert len(out) == new_asset.LABEL_WIDTH * new_asset.LABEL_HEIGHT * 4
    assert _pixel(out, 208, 108) == (230, 30, 30, 255)  # 正中 = 源图正中的红
    assert _pixel(out, 101, 1) == GREEN  # 源图左上角那块绿，落在主体左上角
    assert _pixel(out, 99, 1) != GREEN  # 主体左边缘**就在 x=100**
    # 两侧是**另一幅内容**（整图缩到 416x216 + 模糊 + 压暗）：
    assert _pixel(out, 0, 0) != _pixel(out, 208, 108)
    assert _pixel(out, 0, 0)[3] == int(255 * new_asset.BLUR_DIM)  # 压暗（alpha 也跟着压）


def test_fit_plain_leaves_the_sides_transparent(source_png: Path) -> None:
    out = new_asset.fit_image(source_png, fit="plain")
    assert _pixel(out, 208, 108) == (230, 30, 30, 255)
    assert _pixel(out, 101, 1) == GREEN
    assert _pixel(out, 0, 0) == (0, 0, 0, 0)
    assert _pixel(out, new_asset.LABEL_WIDTH - 1, new_asset.LABEL_HEIGHT - 1) == (0, 0, 0, 0)


def test_fit_fill_crops_instead_of_letterboxing(source_png: Path) -> None:
    """fill 是**居中裁切**：源图上下被切掉 ⇒ 左上角那块绿**不在**结果里，且没有透明像素。"""
    fill = new_asset.fit_image(source_png, fit="fill")
    blur = new_asset.fit_image(source_png, fit="blur")
    assert _count_color(fill, GREEN) == 0
    assert _count_color(blur, GREEN) > 0  # 正控：同一个量法在 blur 上**能红**
    assert _count_color(fill, (0, 0, 0, 0)) == 0
    assert _pixel(fill, 208, 108) == (230, 30, 30, 255)  # 正中还是那块红


def test_fit_can_go_red_and_refuses_an_unknown_mode(source_png: Path) -> None:
    """尺子先能红：换一张源图 / 换一种构图，读数必须变；不认得的构图直接拒。"""
    other = _source_png(source_png.with_name("other.png"), size=(300, 120))
    assert new_asset.fit_image(source_png, fit="blur") != new_asset.fit_image(other, fit="blur")
    modes = {mode: new_asset.fit_image(source_png, fit=mode) for mode in new_asset.FIT_MODES}
    assert len(set(modes.values())) == len(new_asset.FIT_MODES)  # 三种构图两两不同
    with pytest.raises(AssetError):
        new_asset.fit_image(source_png, fit="zoom")


# --------------------------------------------------------------------------- #
# ② 行序（Unity 贴图自下而上）
# --------------------------------------------------------------------------- #
def test_flip_rows_is_its_own_inverse_and_really_flips() -> None:
    width, height = 3, 2
    top = bytes([1, 2, 3, 255] * width)
    bottom = bytes([9, 8, 7, 255] * width)
    buf = top + bottom
    flipped = new_asset.to_unity_image_data(buf, width, height)
    assert flipped == bottom + top  # 真的翻了（首行换成了原末行）
    assert new_asset.flip_rows(flipped, width, height) == buf  # 自逆
    assert new_asset.to_unity_image_data(buf, width, height) != buf  # 正控：这个量法能红
    with pytest.raises(AssetError):
        new_asset.flip_rows(buf, width, height + 1)  # 行长对不上 ⇒ 拒绝，不许猜


# --------------------------------------------------------------------------- #
# ③ 键 / 包名（真机口径）
# --------------------------------------------------------------------------- #
def test_bundle_name_is_deterministic_and_hash_is_the_name_tail() -> None:
    name = new_asset.bundle_file_name(KEY)
    assert name.endswith(".bundle") and "_assets_all_" in name
    tail = name[: -len(".bundle")].rsplit("_all_", 1)[-1]
    assert len(tail) == 16 and all(char in "0123456789abcdef" for char in tail)
    facts = new_asset.bundle_facts(name, b"x" * 10, 12345)
    assert facts["m_hash"] == tail  # 真件口径：文件名 _all_ 尾段
    assert facts["m_hash"] != hashlib.md5(b"x" * 10).hexdigest()  # 不是 md5(文件)
    assert facts["bundle_name"] == name
    assert facts["internal_name"] == name[: -len(".bundle")]
    assert facts["bundle_size"] == 10 and facts["crc"] == 12345
    assert new_asset.bundle_file_name(KEY) == name  # 确定性
    assert new_asset.bundle_file_name(KEY + "2") != name


def test_validate_key_is_fail_closed() -> None:
    assert new_asset.validate_key(KEY) == KEY
    for bad in ("", "../evil", "a b", "中文键", "x" * 65, "-lead"):
        with pytest.raises(AssetError):
            new_asset.validate_key(bad)
    assert new_asset.asset_path_of(KEY) == f"Assets/Mods/BAModMaker/{KEY}.png"


# --------------------------------------------------------------------------- #
# ④ Units 那一行
# --------------------------------------------------------------------------- #
def test_find_unit_row_is_fail_closed_on_duplicate_model_file_names() -> None:
    rows = _units_rows()
    index, row = new_asset.find_unit_row(rows, unit=UNIT_UNIQUE)
    assert (index, row["Id"]) == (0, 1)
    with pytest.raises(AssetError) as dup:
        new_asset.find_unit_row(rows, unit=UNIT_DUP)
    message = str(dup.value)
    assert "2 行" in message and "Id=90" in message and "Id=478" in message
    assert "DUP-Label" in message and "--id" in message  # 报错要能照着做
    index2, row2 = new_asset.find_unit_row(rows, unit=UNIT_DUP, row_id=478)
    assert (index2, row2["Name"]) == (2, "重名乙")
    with pytest.raises(AssetError):
        new_asset.find_unit_row(rows, unit=UNIT_DUP, row_id=999)
    with pytest.raises(AssetError) as missing:
        new_asset.find_unit_row(rows, unit="SYNTH_NOPE")
    assert "SYNTH" in str(missing.value)  # 命中 0 时给候选


def test_rewrite_zip_row_touches_only_the_units_table(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path / "BA_Mod.zip", _units_payload(_units_rows()))
    before = dbzip.DbZip.read(zip_path)
    edit = new_asset.rewrite_zip_row(zip_path, unit=UNIT_DUP, key=KEY, row_id=478)
    assert (edit.row_index, edit.row_id, edit.old_key) == (2, 478, "SYNTH_DUP_CMD-Label")
    out = tmp_path / "after.zip"
    out.write_bytes(edit.zip_after)
    after = dbzip.DbZip.read(out)
    assert set(after.tables) == set(before.tables)
    assert after.tables["Armors"] == before.tables["Armors"]  # 别的表逐字节没动
    rows = json.loads(after.tables["Units"].decode("utf-8"))
    assert rows[2]["ThumbnailFileName"] == KEY and rows[0]["ThumbnailFileName"] == "ONE-Label"
    assert edit.table_count == len(after.tables)


def test_rewrite_zip_row_refuses_a_non_canonical_units_table(tmp_path: Path) -> None:
    """**正控 ③**：Units.json 带缩进（别的工具的写法）⇒ 必须拒绝。

    不拒的话反向 Δ 会退化成整份表 ≈ 拷整包当备份 —— ADR 0007 明令不许。
    """
    zip_path = _write_zip(tmp_path / "pretty.zip", _units_payload(_units_rows(), indent=2))
    with pytest.raises(AssetError) as excinfo:
        new_asset.rewrite_zip_row(zip_path, unit=UNIT_UNIQUE, key=KEY)
    assert "序列化口径" in str(excinfo.value)
    ok = _write_zip(tmp_path / "canonical.zip", _units_payload(_units_rows()))
    assert new_asset.rewrite_zip_row(ok, unit=UNIT_UNIQUE, key=KEY).zip_after  # 正控的反面


def test_rewrite_zip_row_refuses_the_same_key_twice(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path / "BA_Mod.zip", _units_payload(_units_rows()))
    edit = new_asset.rewrite_zip_row(zip_path, unit=UNIT_UNIQUE, key=KEY)
    again = tmp_path / "again.zip"
    again.write_bytes(edit.zip_after)
    with pytest.raises(AssetError) as excinfo:
        new_asset.rewrite_zip_row(again, unit=UNIT_UNIQUE, key=KEY)
    assert "已经是" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# ⑤ catalog
# --------------------------------------------------------------------------- #
def _facts() -> dict:
    return new_asset.bundle_facts(new_asset.bundle_file_name(KEY), b"y" * 64, 987654321)


def _synth_catalog_bytes() -> bytes:
    return _catalog_bytes(_synth_catalog())


def test_add_catalog_entries_adds_two_keys_and_three_entries() -> None:
    before = _synth_catalog_bytes()
    after, registration = new_asset.add_catalog_entries(
        before, key=KEY, asset_path=new_asset.asset_path_of(KEY), facts=_facts()
    )
    assert len(after) > len(before)
    edit = ce.CatalogEdit.from_bytes(after)
    counts = edit.counts
    assert counts == (4 + 2, 4 + 2, 3 + 3)  # +2 键 +2 桶 +3 条目（与 kb/14 §8 第二批同形）
    assert edit.key_index_of(KEY) == registration.key_index
    assert edit.key_index_of(_facts()["bundle_name"]) == registration.bundle_key_index
    assert edit.buckets[registration.key_index] == [
        registration.asset_entry_index,
        registration.alias_entry_index,
    ]
    assert edit.entries[registration.asset_entry_index][ce.ENTRY_RESOURCE_TYPE] == 3
    assert edit.entries[registration.alias_entry_index][ce.ENTRY_RESOURCE_TYPE] == 4
    entries = edit.entries
    assert entries[registration.bundle_entry_index][ce.ENTRY_PROVIDER] == cw.ASSET_BUNDLE_PROVIDER
    assert entries[registration.asset_entry_index][ce.ENTRY_PROVIDER] == cw.BUNDLED_ASSET_PROVIDER
    assert edit.internal_ids[registration.internal_id_index] == new_asset.asset_path_of(KEY)


def test_add_catalog_entries_is_idempotent_only_through_removal() -> None:
    """**正控**：同一个键再增一次必须红（入库的键不许覆盖）；删掉之后才能再加。"""
    before = _synth_catalog_bytes()
    after, _registration = new_asset.add_catalog_entries(
        before, key=KEY, asset_path=new_asset.asset_path_of(KEY), facts=_facts()
    )
    with pytest.raises(AssetError) as excinfo:
        new_asset.add_catalog_entries(
            after, key=KEY, asset_path=new_asset.asset_path_of(KEY), facts=_facts()
        )
    assert "已经有这个键" in str(excinfo.value)
    edit = ce.CatalogEdit.from_bytes(after)
    report = edit.remove_address(KEY)
    assert report.counts_after == (5, 5, 4)  # 只删地址：包登记那条键还在
    edit.remove_bundle(_facts()["bundle_name"])
    assert edit.counts == (4, 4, 3)
    assert edit.to_bytes() == before  # 新增 + 删除 = 逐字节恒等


# --------------------------------------------------------------------------- #
# ⑥ 一条链：装 -> 撤
# --------------------------------------------------------------------------- #
def test_plan_add_needs_the_game_root_package(game: Path, project: Path, source_png: Path) -> None:
    """基线只认 <游戏根>\\BA_Mod\\BA_Mod.zip；没有就报错，并且 hint 里给出可执行的下一步。"""
    external_db.game_zip_path(game).unlink()
    with pytest.raises(AssetError) as excinfo:
        _plan(game, project, source_png)
    message = str(excinfo.value)
    assert "BA_Mod.zip" in message and "extract" in message and "data.unity3d" in message


def test_chain_add_then_restore_is_bit_exact(
    game: Path, project: Path, source_png: Path, monkeypatch
) -> None:
    """判据一行：装完再撤 ⇒ **整棵游戏树的 sha256 逐条回到装前**。"""
    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    before = _snapshot(game)
    plan = _plan(game, project, source_png)
    report = new_asset.run_add(plan, force_dll=True, check_game_running=False)

    assert plan.bundle_path.is_file() and plan.bundle_path.read_bytes() == plan.bundle_data
    assert plan.catalog_path.read_bytes() == plan.catalog_after
    assert plan.zip_path.read_bytes() == plan.zip_after
    assert report.install_verified is True and report.dll_applied is True
    assert dllpatch.status(game).state is dllpatch.PatchState.PATCHED
    assert new_asset.manifest_path(project).is_file()
    assert _snapshot(game) != before

    out = new_asset.restore(game_root=game, project_root=project, check_game_running=False)
    assert out.ok is True
    assert _snapshot(game) == before  # ★ 逐字节回到装前
    assert not new_asset.manifest_path(project).is_file()
    assert list((project / "patches").glob("*")) == []


def test_restore_refuses_after_tampering(
    game: Path, project: Path, source_png: Path, monkeypatch
) -> None:
    """**正控 ②**：装完手改 1 字节 ⇒ restore 必须拒绝，那个文件一个字节不动、补丁还在。"""
    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    _plan_and_install(game, project, source_png)
    target = external_db.game_zip_path(game)
    data = bytearray(target.read_bytes())
    data[-1] ^= 0x01
    target.write_bytes(bytes(data))
    edited = target.read_bytes()

    with pytest.raises(AssetError) as excinfo:
        new_asset.restore(game_root=game, project_root=project, check_game_running=False)
    assert "不符" in str(excinfo.value) and "拒绝" in str(excinfo.value)
    assert target.read_bytes() == edited  # 一个字节都没动
    assert (project / rd.PATCH_DIR_NAME / rd.PATCH_JSON_NAME).is_file()  # 补丁还在（可重试）


def _plan_and_install(game: Path, project: Path, source_png: Path):
    plan = _plan(game, project, source_png)
    return plan, new_asset.run_add(plan, force_dll=True, check_game_running=False)


def test_a_failed_write_rolls_back_with_the_patch(
    game: Path, project: Path, source_png: Path, monkeypatch
) -> None:
    """写盘炸在第三步：必须用补丁**撤回**（现场回到装前、补丁与清单都清掉）。"""
    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    before = _snapshot(game)
    plan = _plan(game, project, source_png)
    real = new_asset._write_verified
    calls = {"n": 0}

    def boom(path, data, *, what):
        calls["n"] += 1
        # 写盘次序：1 = catalog.bin · 2 = catalog.hash（2026-10-14 起）· 3 = 新包。
        # 本用例要的**就是**「新包那一笔炸」（见 docstring），所以打在 3 上。
        if calls["n"] == 3:
            raise OSError("注入的写盘失败")
        return real(path, data, what=what)

    monkeypatch.setattr(new_asset, "_write_verified", boom)
    with pytest.raises(AssetError) as excinfo:
        new_asset.run_add(plan, force_dll=True, check_game_running=False)
    assert "失败" in str(excinfo.value) and "撤" in str(excinfo.value)
    assert _snapshot(game) == before  # ★ 现场回到装前
    assert list((project / "patches").glob("*")) == []
    assert not new_asset.manifest_path(project).is_file()


def test_restore_says_already_original_when_the_dll_is_back(
    game: Path, project: Path, source_png: Path, monkeypatch
) -> None:
    """**现场读数优先**：DLL 已经回到原版 ⇒ 印「已是原版，无需还原」，不报错、也不动它。"""
    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    _plan_and_install(game, project, source_png)  # 本单把 DLL 打了
    dllpatch.restore(game, check_game_running=False)  # 别的东西/用户把它还原了
    sha = hashlib.sha256((game / dllpatch.DLL_NAME).read_bytes()).hexdigest()
    out = new_asset.restore(game_root=game, project_root=project, check_game_running=False)
    assert out.ok is True and "已是原版" in out.dll_line
    assert hashlib.sha256((game / dllpatch.DLL_NAME).read_bytes()).hexdigest() == sha


def test_restore_never_silently_keeps_a_patched_dll_without_the_manifest(
    tmp_path: Path, source_png: Path, monkeypatch
) -> None:
    """清单丢了而 DLL 是补丁态 ⇒ 保持原样 ＋ 一条可执行的 hint（⛔ 不许静默）。"""
    game, project = _make_game(tmp_path, dll_patched=True)
    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    _plan_and_install(game, project, source_png)
    new_asset.manifest_path(project).unlink()
    out = new_asset.restore(game_root=game, project_root=project, check_game_running=False)
    assert out.ok is True
    assert "认不出" in out.dll_line and "patch restore" in out.dll_line
    assert dllpatch.status(game).state is dllpatch.PatchState.PATCHED  # 一个字节没动


def test_restore_is_idempotent_and_the_cli_is_wired(
    game: Path, project: Path, source_png: Path, monkeypatch
) -> None:
    """撤两次都该成功（第二次「无需还原」）；顺带走一遍真 CLI 的 handler。"""
    from ba_mod_maker.cli import __main__ as cli_main

    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    _plan_and_install(game, project, source_png)
    argv = ["newasset", "restore", "--game", str(game), "--project-root", str(project)]
    assert cli_main.main(argv) == 0
    assert cli_main.main(argv) == 0  # 幂等：再撤一次还是 0
    assert cli_main.main(["newasset"]) == 2  # 没给动作 ⇒ 用法错
    # --force-dll 与 --keep-dll 互斥，且 force-dll 在 restore 上没有意义 ⇒ 直接拒（exit 2）
    forced = ["newasset", "restore", "--force-dll", "--game", str(game)]
    assert cli_main.main(forced) == 2


def test_a_dll_that_was_already_patched_is_left_alone(
    tmp_path: Path, source_png: Path, monkeypatch
) -> None:
    """DLL 本来就打着（不是本单打的）⇒ 报告要明说，且 restore **不许**撤它。"""
    game, project = _make_game(tmp_path, dll_patched=True)
    monkeypatch.setattr(new_asset, "build_bundle", _fake_build_bundle)
    dll_sha = hashlib.sha256((game / dllpatch.DLL_NAME).read_bytes()).hexdigest()
    _plan, report = _plan_and_install(game, project, source_png)
    assert report.dll_applied is False
    assert "不是本单打的" in report.dll_line
    manifest = json.loads(new_asset.manifest_path(project).read_text(encoding="utf-8"))
    assert manifest["dll_applied"] is False

    out = new_asset.restore(game_root=game, project_root=project, check_game_running=False)
    assert out.ok is True
    assert hashlib.sha256((game / dllpatch.DLL_NAME).read_bytes()).hexdigest() == dll_sha  # 没动它
    assert "保持原样" in out.dll_line and "patch restore" in out.dll_line  # hint 不许省


# --------------------------------------------------------------------------- #
# ⑦ CLI 形状
# --------------------------------------------------------------------------- #
def test_cli_newasset_parser_matches_the_module() -> None:
    """CLI 的 --fit 取值必须与 new_asset.FIT_MODES 是同一套（逻辑只有一份）。"""
    from ba_mod_maker.cli import __main__ as cli_main

    parser = cli_main._build_parser()
    base_argv = ["newasset", "add", "--png", "p.png", "--unit", "u", "--key", "k"]
    args = parser.parse_args(base_argv)
    assert (args.command, args.action, args.fit, args.row_id, args.dry_run) == (
        "newasset",
        "add",
        "blur",
        None,
        False,
    )
    assert parser.parse_args([*base_argv, "--force-dll"]).force_dll is True
    for mode in new_asset.FIT_MODES:
        assert parser.parse_args([*base_argv, "--fit", mode]).fit == mode
    with pytest.raises(SystemExit):
        parser.parse_args([*base_argv, "--fit", "zoom"])
    restore_argv = ["newasset", "restore", "--game", "g", "--project-root", "p"]
    restore_args = parser.parse_args(restore_argv)
    assert (restore_args.command, restore_args.action, restore_args.keep_dll) == (
        "newasset",
        "restore",
        False,
    )
    assert parser.parse_args([*restore_argv, "--keep-dll"]).keep_dll is True
    with pytest.raises(SystemExit):
        parser.parse_args(["newasset", "add", "--unit", "u", "--key", "k"])  # 缺 --png
    # **互斥**（Lead 2026-10-06 定）：两条 DLL 开关同时给 ⇒ 必须是参数错误，不是悄悄取一个
    with pytest.raises(SystemExit):
        parser.parse_args([*restore_argv, "--keep-dll", "--force-dll"])
    with pytest.raises(SystemExit):
        parser.parse_args([*base_argv, "--force-dll", "--keep-dll"])  # add 上 keep 本就不认


# --------------------------------------------------------------------------- #
# ⑧ 真类型树那条（只读游戏包；默认被 -m "not slow" 挡在外面）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_bundle_from_the_real_type_tree_reads_back_in_game_direction(
    tmp_path: Path, source_png: Path
) -> None:
    """用游戏真包的类型树造一个包，独立解析（UnityPy）后**方向**与登记值都对。

    正控：按**存储序**直接比必须不相等 —— 这条尺子能红（kb/14 §8 硬知识 1）。
    """
    import UnityPy

    root = find_game_root()
    if root is None:
        pytest.skip("找不到游戏目录（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    pc = bundle_dir(root)
    meta = sorted(pc.glob("unitlabels*.bundle"))
    sprite = sorted(pc.glob("ammoicons*.bundle"))
    if not meta or not sprite:
        pytest.skip("游戏包的命名变了（找不到 unitlabels / ammoicons）")

    key = "ASSET_ADD_TEST-Label"
    rgba = new_asset.fit_image(source_png, fit="blur")
    plan = new_asset.build_bundle(
        key=key, image_display=rgba, meta_bundle=meta[0], sprite_bundle=sprite[0]
    )
    path = tmp_path / new_asset.bundle_file_name(key)
    path.write_bytes(plan.data)
    with ufs.UfsReader(path) as reader:
        stream = bytes(reader.read_range(0, reader.total_uncompressed))
    assert zlib.crc32(stream) & 0xFFFFFFFF == plan.crc32  # catalog 的 m_Crc 口径
    assert len(path.read_bytes()) == plan.bundle_size == len(plan.data)

    env = UnityPy.load(str(path))
    textures = [obj for obj in env.objects if obj.type.name == "Texture2D"]
    sprites = [obj for obj in env.objects if obj.type.name == "Sprite"]
    capsules = [obj for obj in env.objects if obj.type.name == "AssetBundle"]
    assert (len(textures), len(sprites), len(capsules)) == (1, 1, 1)
    texture = textures[0].read_typetree()
    assert (texture["m_Width"], texture["m_Height"]) == (
        new_asset.LABEL_WIDTH,
        new_asset.LABEL_HEIGHT,
    )
    assert texture["m_TextureFormat"] == 4  # RGBA32
    stored = bytes(texture["image data"])
    assert new_asset.flip_rows(stored, new_asset.LABEL_WIDTH, new_asset.LABEL_HEIGHT) == rgba
    assert stored != rgba  # 正控：存储序 == 源图就该红（说明行序没翻）
    sprite_tree = sprites[0].read_typetree()
    assert sprite_tree["m_RD"]["texture"]["m_PathID"] == textures[0].path_id
    assert sprite_tree["m_RD"]["m_VertexData"]["m_VertexCount"] == 4
    assert len(sprite_tree["m_RD"]["m_IndexBuffer"]) == 12
    uv = sprite_tree["m_RD"]["uvTransform"]
    assert (uv["x"], uv["y"], uv["z"], uv["w"]) == (100.0, 208.0, 100.0, 108.0)
    capsule = capsules[0].read_typetree()
    assert capsule["m_Name"] == new_asset.bundle_file_name(key)
    # UnityPy 的 m_Container 是 [(路径, {asset/preloadIndex/...}), ...]（元组，不是字典）
    assert [pair[0] for pair in capsule["m_Container"]] == [new_asset.asset_path_of(key)] * 2
