r"""资产新增 · **模型**那一档（newasset add-model）的判据 + 三条负控。

**判据一行**：.venv\Scripts\python.exe -m pytest tests/test_asset_add_model.py -q
→ 通过用例数 **从 0（本文件不存在）变 16 passed**（另有一条标了 slow：真游戏根 + 真 catalog
上跑一次 dry-run，断言形状，不写盘）。

⛔ 全部用例都在 tmp_path 造的**假游戏根**上跑 —— 一个字节都不写 D:\Steam\...。

| 用例组 | 判据 |
|---|---|
| 地址形状 | **1 条 entry**、provider=2、resource_type=**1**、带依赖组（label 才是 (3,4) 两条） |
| 依赖 hash | **抄**真件模型地址的 entry[3]（用例里 = 999），⛔ 不是算出来的 |
| 计数 | +3 键 / +3 桶 / **+2 条目**（label 是 +2/+2/+3） |
| 数据库 | 写 ModelFileName，**不动** ThumbnailFileName |
| 报告 | 模型报告里**没有**「416x216 / 像素」那一段（plan_lines 按 kind 分支） |
| 干跑 | 整棵游戏树 sha256 逐条不变，且**连补丁柜都没建** |
| 一条链 | 装完 → restore → 整棵树回到装前，且 `BA_Mod\` / `aa\PC` 两处都不残留这个包 |
| catalog.hash | 装完 `aa/catalog.hash` == `hash128_hex(catalog.bin)`；装前写错的必须被改对 |
| ★ 落点（块 B） | 包落 `<游戏根>\BA_Mod\<包>`；catalog 包条目与落点同一处，`aa\PC` 不残留 |

**三条负控**（任务书判据 ③，都要能红）：

① 键（或包名）已经在 catalog 里 ⇒ fail-closed；
② 给的 facts 与包**对不上** ⇒ 拒（catalog 的 m_Crc/m_BundleSize 是游戏加载的依据，
   错了就是一个加载不上的地址）；
③ resource_type 与 kind 的形状不符 ⇒ 拒；依赖来源不是模型地址 / 不存在 / 空哈希 ⇒ 拒。

**与 _work/route-b/routeb_install.py 的对拍**（任务书判据 ①）在
_work/model-add/cross-check.py：同一份输入下两个工具逐字段相同（含地址 entry 七格、
+3/+3/+2、catalog 文件长）。那里用的是**改名后的同一份包** —— 产品的「同名包已登记」是
fail-closed 的（负控 ①），而那个包已经被装进去过。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path

import pytest

from _fake_dll import fake_dll_bytes  # tests/ 由 pytest 插进 sys.path
from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import dbzip, dllpatch, external_db, new_asset
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError
from test_dllpatch import _pretend_original


@pytest.fixture(autouse=True)
def _fake_dll_is_a_known_original(monkeypatch):
    """本文件的假游戏根拿 `fake_dll_bytes()` 当原版 DLL ⇒ 得让它对得上 `BUILDS`。

    2026-10-14 起地址表**按原版 sha256 分版本**：`apply()` 认不出版本就（正确地）拒绝，
    夹具要冒充原版只能给它造一个假 `Build`（手法与 tests/test_dllpatch.py 同一份）。
    """
    _pretend_original(monkeypatch, fake_dll_bytes())

KEY = "MODEL_ADD_TEST-Unit"
UNIT_MODEL = "addr/one"
UNIT_DUP = "SYNTH\\DUP_base"
ALPHA = "alpha_assets_all_" + "1" * 32 + ".bundle"
BETA = "beta_assets_all_" + "2" * 32 + ".bundle"
GROUP = "group/dep1"
ADDR_ONE = "addr/one"
SHARED_ASSET = "Assets/Synth/Shared.prefab"
#: 真件模型地址那条 entry[3] 的**合成**值（形状与 RU_T72B 的 1392117474 同款）。
DEP_HASH = 999
#: 二进制 catalog 里两条 provider 串的**真件后缀**（产品按后缀认，⛔ 不写死全名）。
BUNDLE_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
BUNDLED_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
#: 二进制 catalog 里的 Unity 装配名（形状抄真件；夹具自己选一个）。
UNITY_CORE = "UnityEngine.CoreModule"


# --------------------------------------------------------------------------- #
# 合成件（与 tests/test_asset_add.py 同款；⛔ 不用被测代码拼结构）
# --------------------------------------------------------------------------- #
def _units_payload(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _units_rows() -> list[dict]:
    return [
        {
            "Id": 1,
            "Name": "模型单位",
            "ModelFileName": UNIT_MODEL,
            "ThumbnailFileName": "ONE-Label",
        },
        {"Id": 90, "Name": "重名甲", "ModelFileName": UNIT_DUP, "ThumbnailFileName": "DUP-Label"},
        {"Id": 478, "Name": "重名乙", "ModelFileName": UNIT_DUP, "ThumbnailFileName": "DUP-Label"},
    ]


def _write_zip(path: Path, units: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    dbzip.DbZip(tables={"Units": units, "Armors": b'[{"Id":1}]'}).write(path)
    return path


def _synth_catalog() -> dict:
    """2 个包 + 1 个依赖组 + **1 条模型形状的地址**（resource_type=1、带依赖键、hash=999）。"""
    internal = [
        cw.RUNTIME_PATH + cw.PC_SEGMENT + ALPHA,
        cw.RUNTIME_PATH + cw.PC_SEGMENT + BETA,
        SHARED_ASSET,
    ]
    extra = bytearray()
    offsets: list[int] = []
    for key, crc in ((ALPHA, 111), (BETA, 222)):
        offsets.append(len(extra))
        extra += cw.extra_data_blob(
            cw.CatalogBundle(
                key, key[0] * 32, crc, 1000 + crc, internal_name=key[: -len(".bundle")]
            ).options()
        )
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
        [2, cw.BUNDLED_ASSET_PROVIDER, 2, DEP_HASH, -1, 3, 1],
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
        "m_KeyDataString": base64.b64encode(bytes(key_data)).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bytes(bucket_data)).decode("ascii"),
        "m_EntryDataString": base64.b64encode(bytes(entry_data)).decode("ascii"),
        "m_ExtraDataString": base64.b64encode(bytes(extra)).decode("ascii"),
        "m_resourceTypes": [
            {"m_AssemblyName": "Unity.RM", "m_ClassName": "GameObject"},
            {"m_AssemblyName": "Unity.RM", "m_ClassName": "Texture2D"},
        ],
        "m_InternalIdPrefixes": [],
    }


def _catalog_bytes(raw: dict) -> bytes:
    return json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _make_game(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "game"
    aa = root / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    (aa / "PC").mkdir(parents=True)
    (aa / "catalog.json").write_bytes(_catalog_bytes(_synth_catalog()))
    external_db.ensure_zip_dir(root)
    _write_zip(external_db.game_zip_path(root), _units_payload(_units_rows()))
    original = fake_dll_bytes()
    (root / dllpatch.BACKUP_NAME).write_bytes(original)
    (root / dllpatch.DLL_NAME).write_bytes(original)
    project = tmp_path / "project"
    project.mkdir()
    return root, project


BIN_HASH_WRONG = b"0" * 32


def _aa(root: Path) -> Path:
    return root / "BrokenArrow_Data" / "StreamingAssets" / "aa"


def _synth_bin_catalog() -> bytes:
    '''合成的**二进制** catalog（1.2.1 形状）：1 条包登记 ＋ 1 条模型地址。

    ⛔ 这里**故意**用产品的 `catalog_bin_write.serialize` 把一份显式模型写成字节 ——
    二进制 catalog 的书写次序是官方 Writer 的逐语义移植，手拼字节等于**第二份实现**（必然漂移）。
    那一步是纯函数、不碰盘；它自己的正控是「读真件 → 写回 == 真件逐字节相同」。
    '''
    model = cbw.BinModel(
        provider_id="AddressablesMainContentCatalog",
        build_result_hash="0" * 32,
    )
    model.entries = [
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=ALPHA,
            # 真件形状（kb/02 §4）：包地址 = {…RuntimePath}\PC\<包>。落点改写只认这个形状。
            internal_id=cw.RUNTIME_PATH + cw.PC_SEGMENT + ALPHA,
            provider=BUNDLE_PROVIDER,
            dep_ranks=(),
            dep_hash=0,
            extra=cbw.AssetBundleOptions(
                hash_bytes=bytes(range(16)),
                bundle_name=ALPHA[: -len(".bundle")],
                crc=111,
                bundle_size=1000,
                common_raw=bytes.fromhex("0000200004000000"),
                type_asm=UNITY_CORE,
                type_cls="UnityEngine.AssetBundleRequestOptions",
            ),
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.AssetBundle",
            rank=0,
        ),
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=ADDR_ONE,
            internal_id=SHARED_ASSET,
            provider=BUNDLED_PROVIDER,
            dep_ranks=(0,),
            dep_hash=DEP_HASH,
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.GameObject",
            rank=1,
        ),
    ]
    model.keys = [
        cbw.BinKey(name=ALPHA, ranks=(0,), type_asm="mscorlib", type_cls="System.String"),
        cbw.BinKey(name=ADDR_ONE, ranks=(1,), type_asm="mscorlib", type_cls="System.String"),
    ]
    raw = cbw.serialize(model)
    # 夹具自证：它得真的是「2 键 / 2 条目」的二进制 catalog（不然下面的断言全是假的）
    assert cbw.looks_like_catalog_bin(raw) and cbw.counts(raw) == (2, 2, 2)
    return raw


def _make_bin_game(tmp_path: Path, *, hash_bytes: bytes | None = BIN_HASH_WRONG):
    '''1.2.1 形状的假游戏根：`aa/catalog.bin` ＋ 一份**故意写错**的 `aa/catalog.hash`。'''
    root = tmp_path / "game"
    aa = _aa(root)
    (aa / "PC").mkdir(parents=True)
    (aa / "catalog.bin").write_bytes(_synth_bin_catalog())
    if hash_bytes is not None:
        (aa / "catalog.hash").write_bytes(hash_bytes)
    external_db.ensure_zip_dir(root)
    _write_zip(external_db.game_zip_path(root), _units_payload(_units_rows()))
    original = fake_dll_bytes()
    (root / dllpatch.BACKUP_NAME).write_bytes(original)
    (root / dllpatch.DLL_NAME).write_bytes(original)
    project = tmp_path / "project"
    project.mkdir()
    return root, project


def _hash_matches_the_bin(root: Path) -> bool:
    '''尺子：`aa/catalog.hash` == `hash128_hex(整份 aa/catalog.bin)`（32 个 ASCII 字符、无换行）。

    口径出处：`core/assets/catalog_bin.py` 的外部 oracle（真件 `81d9efef…` 逐字相同）。
    '''
    return (_aa(root) / "catalog.hash").read_bytes() == cbw.hash128_hex(
        (_aa(root) / "catalog.bin").read_bytes()
    ).encode("ascii")


def _registered_bundle_internal_id(raw: bytes, bundle_name: str) -> str:
    """从 catalog 字节里读回这个包的 internalId（两种格式都认；⛔ 不按代码推一遍）。"""
    if cbw.looks_like_catalog_bin(raw):
        for entry in cbw.read_model(raw).entries:
            if (entry.internal_id or "").endswith(bundle_name):
                return str(entry.internal_id)
    else:
        for internal_id in ce.CatalogEdit.from_bytes(raw).internal_ids:
            if internal_id.endswith(bundle_name):
                return str(internal_id)
    raise AssertionError("catalog 里没有这个包的条目：" + bundle_name)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    return _make_game(tmp_path)[0]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


@pytest.fixture()
def source_bundle(tmp_path: Path) -> Path:
    """一个**真的 UnityFS 文件**（元数据区是产品自己编的：0 类型 / 0 对象
    ⇒ 包内没有 AssetBundle 对象可读）。

    ⛔ 手搓容器就够了：本档要验的是 catalog / DB / 报告的形状，不是造包（造包在 asset_kernel）。
    """
    meta = mw.SerializedMetadata(
        header=mw.SerializedFileHeader(0, 0, 22, 0, 0, b"\x00" * 3, 0, 0, 0, 0),
        unity_version="2022.3.62f3",
        target_platform=19,
        enable_type_tree=False,
        types=[],
        objects=[],
        script_types=[],
        externals=[],
        ref_types=[],
        user_information="",
    )
    name = "modeladdtest_assets_all_" + "a" * 32 + ".bundle"
    container = builder.build_unityfs(
        mw.build_metadata(meta), builder.node_path_for(name), block_type=builder.BLOCK_NONE
    )
    path = tmp_path / name
    path.write_bytes(container.data)
    return path


def _plan(game: Path, project: Path, bundle: Path, **kwargs: object):
    params: dict[str, object] = {
        "unit": UNIT_MODEL,
        "key": KEY,
        "row_id": 1,
        "game_root": game,
        "project_root": project,
    }
    params.update(kwargs)
    return new_asset.plan_add_model(bundle=bundle, **params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ① 地址形状 / 依赖 hash / 计数
# --------------------------------------------------------------------------- #
def test_model_address_is_one_entry_with_resource_type_one(
    game: Path, project: Path, source_bundle: Path
) -> None:
    plan = _plan(game, project, source_bundle)
    row = list(plan.registration.address_row)
    assert row == [
        plan.registration.internal_id_index,
        cw.BUNDLED_ASSET_PROVIDER,  # provider = 2
        plan.registration.dependency_group_index,  # 依赖键 = **组**键
        DEP_HASH,  # 依赖 hash = 抄来的
        -1,
        plan.registration.key_index,
        new_asset.KIND_RESOURCE_TYPE["model"],  # resource_type = 1
    ]
    assert row[6] == 1
    assert plan.registration.kind == "model"
    # 模型地址**不加**那条类型 4 的别名 entry（图标才有）
    assert plan.registration.alias_entry_index == -1

    edit = ce.CatalogEdit.from_bytes(plan.catalog_after)
    key = edit.key_index_of(KEY)
    assert key is not None
    assert len(edit.buckets[key]) == 1, "模型地址的桶里只该有 1 条 entry"
    assert edit.entries[edit.buckets[key][0]][ce.ENTRY_RESOURCE_TYPE] == 1


def test_counts_are_three_keys_three_buckets_two_entries(
    game: Path, project: Path, source_bundle: Path
) -> None:
    plan = _plan(game, project, source_bundle)
    before, after = plan.registration.counts_before, plan.registration.counts_after
    assert (after[0] - before[0], after[1] - before[1], after[2] - before[2]) == (3, 3, 2)


def test_dependency_hash_is_copied_from_a_real_prefab_address(
    game: Path, project: Path, source_bundle: Path
) -> None:
    plan = _plan(game, project, source_bundle)
    # 这一行的 ModelFileName 就是 ADDR_ONE ⇒ 默认依赖来源 = 它
    assert plan.dependency_source_key == ADDR_ONE
    assert plan.registration.dependency_hash == DEP_HASH
    assert "抄自" in "\n".join(plan.plan_lines())


def test_dependency_source_can_be_pointed_explicitly(
    game: Path, project: Path, source_bundle: Path
) -> None:
    plan = _plan(game, project, source_bundle, unit=UNIT_DUP, row_id=90,
                 dependency_source=ADDR_ONE)
    assert plan.registration.dependency_hash == DEP_HASH
    assert plan.old_key == UNIT_DUP


def test_default_dependency_source_fails_closed_when_the_row_is_not_a_real_address(
    game: Path, project: Path, source_bundle: Path
) -> None:
    """这一行的 ModelFileName 不在 catalog 里 ⇒ 拒（⛔ 不许瞎编一个依赖 hash）。"""
    with pytest.raises(AssetError, match="依赖来源键"):
        _plan(game, project, source_bundle, unit=UNIT_DUP, row_id=90)


def test_dependency_source_must_be_a_model_address(
    game: Path, project: Path, source_bundle: Path
) -> None:
    """来源键存在、但那条 entry 不是模型地址（resource_type != 1）⇒ 拒。"""
    catalog = _catalog_bytes(_synth_catalog())
    edit = ce.CatalogEdit.from_bytes(catalog)
    # 把 ADDR_ONE 那条地址改成图标形状（resource_type=3）
    key = edit.key_index_of(ADDR_ONE)
    assert key is not None
    edit.entries[edit.buckets[key][0]][ce.ENTRY_RESOURCE_TYPE] = 3
    with pytest.raises(AssetError, match="不是模型地址"):
        new_asset.dependency_hash_from(edit, ADDR_ONE)
    with pytest.raises(AssetError, match="没有依赖来源键"):
        new_asset.dependency_hash_from(edit, "根本没有这个键")


# --------------------------------------------------------------------------- #
# ② 数据库那一列
# --------------------------------------------------------------------------- #
def test_model_writes_modelfilename_and_leaves_the_icon_column(
    game: Path, project: Path, source_bundle: Path, tmp_path: Path
) -> None:
    plan = _plan(game, project, source_bundle)
    assert plan.old_key == UNIT_MODEL
    out = tmp_path / "after.zip"
    out.write_bytes(plan.zip_after)
    rows = json.loads(dbzip.DbZip.read(out).tables["Units"].decode("utf-8"))
    assert rows[0]["ModelFileName"] == KEY
    assert rows[0]["ThumbnailFileName"] == "ONE-Label"  # 图标那一列一个字节没动
    assert rows[0]["Id"] == 1 and rows[0]["Name"] == "模型单位"
    assert rows[1]["ModelFileName"] == UNIT_DUP  # 别的行没动
    assert len(rows) == 3


# --------------------------------------------------------------------------- #
# ③ 负控
# --------------------------------------------------------------------------- #
def test_existing_key_is_rejected(game: Path, project: Path, source_bundle: Path) -> None:
    plan = _plan(game, project, source_bundle)
    facts = {
        "bundle_name": plan.bundle_name,
        "m_hash": plan.bundle_name[: -len(".bundle")].rsplit("_all_", 1)[-1],
        "crc": plan.bundle_crc32,
        "bundle_size": len(plan.bundle_data),
        "internal_name": plan.bundle_name[: -len(".bundle")],
    }
    with pytest.raises(AssetError, match="已经有这个键"):
        new_asset.add_catalog_entries(
            plan.catalog_after, key=KEY, asset_path=plan.asset_path, facts=facts, kind="model",
            dependency_source_key=ADDR_ONE,
        )
    with pytest.raises(AssetError, match="已经有这条包登记"):
        new_asset.add_catalog_entries(
            plan.catalog_after, key="ANOTHER-Key", asset_path="Assets/X.prefab", facts=facts,
            kind="model", dependency_source_key=ADDR_ONE,
        )


def test_facts_must_match_the_file(game: Path, project: Path, source_bundle: Path) -> None:
    facts, _data, _size = new_asset.bundle_facts_of_file(source_bundle)
    # 对的 facts：过
    assert _plan(game, project, source_bundle, facts=facts) is not None
    # 错一个 crc：拒
    bad = dict(facts)
    bad["crc"] = int(facts["crc"]) + 1
    with pytest.raises(AssetError, match="facts 与文件对不上"):
        _plan(game, project, source_bundle, facts=bad)
    # 缺键：拒
    with pytest.raises(AssetError, match="缺"):
        _plan(game, project, source_bundle, facts={"crc": facts["crc"]})


def test_resource_type_must_match_the_kind(game: Path, project: Path, source_bundle: Path) -> None:
    facts, _data, _size = new_asset.bundle_facts_of_file(source_bundle)
    catalog = (game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "catalog.json").read_bytes()
    with pytest.raises(AssetError, match="resource_type 必须是 1"):
        new_asset.add_catalog_entries(
            catalog, key=KEY, asset_path="Assets/X.prefab", facts=facts, kind="model",
            resource_type=3, dependency_source_key=ADDR_ONE,
        )
    with pytest.raises(AssetError, match="resource_type 必须是 3"):
        new_asset.add_catalog_entries(
            catalog, key=KEY, asset_path="Assets/X.png", facts=facts, kind="label",
            resource_type=1,
        )
    with pytest.raises(AssetError, match="不认得的 kind"):
        new_asset.add_catalog_entries(
            catalog, key=KEY, asset_path="Assets/X.prefab", facts=facts, kind="mesh",
        )


# --------------------------------------------------------------------------- #
# ④ 报告形状 / 干跑零写入 / 一条链
# --------------------------------------------------------------------------- #
def test_plan_lines_has_no_image_section(game: Path, project: Path, source_bundle: Path) -> None:
    text = "\n".join(_plan(game, project, source_bundle).plan_lines())
    assert "416x216" not in text
    assert "像素" not in text
    assert "fit=" not in text
    assert "ModelFileName" in text
    assert "① 新包" in text and "② catalog" in text and "③ 数据库" in text


def test_dry_run_writes_nothing(game: Path, project: Path, source_bundle: Path) -> None:
    """判据 ④：--dry-run 下游戏目录与工程目录**一个字节都不写**（连补丁柜都不建）。"""
    before_game, before_project = _snapshot(game), _snapshot(project)
    plan = _plan(game, project, source_bundle)
    report = new_asset.run_add_dry(plan)
    assert report.dry_run
    assert "一个字节都没写" in report.lines()[0]
    assert _snapshot(game) == before_game
    assert _snapshot(project) == before_project
    assert not new_asset.manifest_path(project).exists()
    assert not (project / "patches").exists()


def test_install_then_restore_returns_the_whole_tree(
    game: Path, project: Path, source_bundle: Path
) -> None:
    """一条链：装（写 catalog + 落包 + 写 ModelFileName + 打 DLL）→ restore → 逐字节回到装前。"""
    before = _snapshot(game)
    plan = _plan(game, project, source_bundle)
    report = new_asset.run_add(plan, force_dll=True, check_game_running=False)
    assert report.verified
    assert plan.bundle_path.is_file()
    assert plan.bundle_path.read_bytes() == plan.bundle_data
    # ★ 块 B：落点只有一处 —— `<游戏根>\BA_Mod\<包>`，catalog 那条 internalId 与它**同一处**。
    assert plan.bundle_path == game / new_asset.MOD_DIR_NAME / plan.bundle_name
    registered = _registered_bundle_internal_id(plan.catalog_path.read_bytes(), plan.bundle_name)
    assert registered == plan.registration.internal_id
    assert registered == cw.RUNTIME_PATH + new_asset.MOD_SEGMENT + plan.bundle_name
    assert not (
        game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / plan.bundle_name
    ).exists()
    edit = ce.CatalogEdit.from_bytes(plan.catalog_path.read_bytes())
    key = edit.key_index_of(KEY)
    assert key is not None and edit.entries[edit.buckets[key][0]][ce.ENTRY_RESOURCE_TYPE] == 1
    package = dbzip.DbZip.read(plan.zip_path)
    row = json.loads(package.tables["Units"].decode("utf-8"))[0]
    assert row["ModelFileName"] == KEY
    assert row["ThumbnailFileName"] == "ONE-Label"

    outcome = new_asset.restore(game_root=game, project_root=project, check_game_running=False)
    assert outcome.ok, outcome.lines()
    assert _snapshot(game) == before
    assert not plan.bundle_path.exists()
    # ★ 块 B 的遗留缺陷：restore 之后 `BA_Mod\` 与 `aa\PC` 都不许残留这个包。
    assert [p for p in game.rglob("*") if p.is_file() and plan.bundle_name in p.name] == []


# --------------------------------------------------------------------------- #
# ⑤ catalog.hash 与 catalog.bin **同步**（1.2.1 口径）
# --------------------------------------------------------------------------- #
def test_install_rewrites_catalog_hash_so_it_matches_the_new_bin(
    tmp_path: Path, source_bundle: Path
) -> None:
    '''判据一行：装前 `catalog.hash` 是**故意错的** `000…`，装完必须 == `hash128_hex(catalog.bin)`。

    为什么是真需求：运行时按 `catalog.hash` 做更新比对，
    盘上「新 bin + 旧 hash」= 整份 catalog 可能不被认。
    ⛔ 全部动作都在这份临时游戏根上，游戏目录一个字节都不写。
    '''
    root, project = _make_bin_game(tmp_path)
    bin_file, hash_file = _aa(root) / "catalog.bin", _aa(root) / "catalog.hash"
    assert hash_file.read_bytes() == BIN_HASH_WRONG
    assert not _hash_matches_the_bin(root)  # ★ 正控：尺子此刻**红**（它认得出错值）

    plan = _plan(root, project, source_bundle)
    assert plan.catalog_path == bin_file
    # ★ 块 B：二进制 catalog 那条包地址与落点**同一处**（`<游戏根>\BA_Mod\`）。
    assert _registered_bundle_internal_id(plan.catalog_after, plan.bundle_name) == (
        cw.RUNTIME_PATH + new_asset.MOD_SEGMENT + plan.bundle_name
    )
    assert plan.catalog_hash_path == hash_file
    assert plan.catalog_hash_before == BIN_HASH_WRONG
    assert plan.catalog_hash_after.decode("ascii") == cbw.hash128_hex(plan.catalog_after)
    assert "catalog.hash" in chr(10).join(plan.plan_lines())

    new_asset.run_add(plan, force_dll=True, check_game_running=False)

    written = hash_file.read_bytes()
    assert bin_file.read_bytes() == plan.catalog_after
    assert _hash_matches_the_bin(root)  # ★ 核心断言
    assert written == plan.catalog_hash_after
    assert len(written) == 32 and set(written) <= set(b"0123456789abcdef")
    assert not written.endswith(b"\n")  # 真件就是 32 字节、**无换行**

    # 负控：改掉 `catalog.hash` **一个字符** ⇒ 同一把尺子必须红
    tampered = bytearray(written)
    tampered[0] = ord("f") if tampered[0] != ord("f") else ord("0")
    hash_file.write_bytes(bytes(tampered))
    assert not _hash_matches_the_bin(root)


def test_install_creates_catalog_hash_when_it_was_missing(
    tmp_path: Path, source_bundle: Path
) -> None:
    '''盘上原本**没有** catalog.hash（`before` = 空）⇒ 必须能创建；

    撤回口径交给补丁柜自己（它的既有语义：装前不存在 ⇒ 还原 = 删掉它）。
    '''
    root, project = _make_bin_game(tmp_path, hash_bytes=None)
    hash_file = _aa(root) / "catalog.hash"
    assert not hash_file.exists()
    plan = _plan(root, project, source_bundle)
    assert plan.catalog_hash_before == b""
    new_asset.run_add(plan, force_dll=True, check_game_running=False)
    assert _hash_matches_the_bin(root)

    outcome = new_asset.restore(game_root=root, project_root=project, check_game_running=False)
    assert outcome.ok, outcome.lines()
    assert not hash_file.exists()  # 装前不存在 ⇒ 还原 = 删掉它




def test_icon_line_shape_is_unchanged(game: Path, project: Path, source_bundle: Path) -> None:
    """零回归的**就地断言**：不传 kind 时仍是图标形状（(3,4) 两条、路径 .png）。"""
    facts, _data, _size = new_asset.bundle_facts_of_file(source_bundle)
    catalog = (game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "catalog.json").read_bytes()
    after, registration = new_asset.add_catalog_entries(
        catalog, key=KEY, asset_path=new_asset.asset_path_of(KEY), facts=facts
    )
    assert new_asset.asset_path_of(KEY).endswith(".png")
    assert registration.kind == "label"
    assert registration.alias_entry_index >= 0
    edit = ce.CatalogEdit.from_bytes(after)
    key = edit.key_index_of(KEY)
    assert key is not None
    assert len(edit.buckets[key]) == 2
    types = sorted(edit.entries[e][ce.ENTRY_RESOURCE_TYPE] for e in edit.buckets[key])
    assert types == [3, 4]


@pytest.mark.slow
def test_real_catalog_model_dry_run(tmp_path: Path) -> None:
    """真游戏根 + 真 catalog 上跑一次 dry-run（只读）：形状与依赖 hash 抄法。

    输入包 = 真游戏目录里那个 routeb 包**改名的一份拷贝**（产品对「同名包已登记」fail-closed）。
    """
    import shutil

    from ba_mod_maker.core.assets.game import find_game_root

    root = find_game_root()
    if root is None:
        pytest.skip("找不到游戏根目录（BA_MOD_MAKER_GAME 指错了？）")
    source = Path(root) / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC"
    hits = sorted(source.glob("routebunit_assets_all_*.bundle"))
    if not hits:
        pytest.skip("游戏目录里没有 routebunit 包（W5 那一次装机被撤了？）")
    copy = tmp_path / "modeladdprobe_assets_all_5847a22c37556b4a.bundle"
    shutil.copy2(hits[0], copy)
    project = tmp_path / "project"
    project.mkdir()
    plan = new_asset.plan_add_model(
        bundle=copy,
        unit="RU_T72B",
        key="ModelAddProbe01",
        dependency_source="RU_T72B",
        game_root=root,
        project_root=project,
    )
    row = list(plan.registration.address_row)
    assert row[1] == cw.BUNDLED_ASSET_PROVIDER
    assert row[6] == 1
    assert row[3] == plan.registration.dependency_hash == 1392117474
    assert row[2] == plan.registration.dependency_group_index
    before, after = plan.registration.counts_before, plan.registration.counts_after
    assert (after[0] - before[0], after[1] - before[1], after[2] - before[2]) == (3, 3, 2)
    assert plan.asset_path.endswith(".prefab")
    assert plan.bundle_name == copy.name
