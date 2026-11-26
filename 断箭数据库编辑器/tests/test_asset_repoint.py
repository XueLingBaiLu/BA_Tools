"""`newasset repoint`（改指向）的判据 ＋ 五条负控。

**判据一行**：`.venv\\Scripts\\python.exe -m pytest tests/test_asset_repoint.py -q -n 0`

正控（在 `_work/p0_restore` 的**真件副本**上跑过，真游戏目录零写入）：
`newasset repoint --unit A2BR8 --key A2BR4 --id 9`
  -> `pack list-units`：**T-72B** · Id=9 · A2BR8 -> **A2BR4**；
  -> `catalog.bin` sha256 **不变**（`96AD6083…`）；读数 27,772 / 27,772 / 20,622 **三项都没变**；
  -> `BA_Mod.zip` `b4397d62…` -> `65c192ac…`（326,555 -> 326,554 B）；
  -> 撤：`newasset restore` -> zip 回到 `b4397d62…`、`list-units` 回到 A2BR8、柜子清空。
负控（同样在副本上跑过，全部 exit 2 且点名）：
未登记键 / 包条目键 / png 地址 / 已经指着它 / 行不存在。

这里不碰真游戏目录：合成二进制 catalog（含一个指向 `.prefab` 的键、一个指向 `.png` 的键）＋ 一份
最小 BA_Mod.zip，只测 repoint 自己的账。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import dbzip, external_db, new_asset
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError

OLD_KEY = "A2BR8"
NEW_KEY = "A2BR4"
DUP_KEY = "SYNTH\\DUP_base"
#: 合法键名，但 catalog 里它指向 `.png`（真件实测 `me_tagIcon`）
ICON_KEY = "me_tagIcon"
PREFAB = "Assets/Resources_moved/BAMod/RU_T72B_Base.prefab"
ICON = "Assets/EditorSystem/Images/new tag.png"
BUNDLE = "p0test_assets_all_" + "a" * 32 + ".bundle"
BUNDLE_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
BUNDLED_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
UNITY_CORE = "UnityEngine.CoreModule"
RUNTIME = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"


def _bundle_entry(rank: int) -> cbw.BinEntry:
    return cbw.BinEntry(
        loc_offset=-1,
        primary_key=BUNDLE,
        internal_id=RUNTIME + "\\aa\\PC\\" + BUNDLE,
        provider=BUNDLE_PROVIDER,
        dep_ranks=(),
        dep_hash=0,
        extra=cbw.AssetBundleOptions(
            hash_bytes=bytes(range(16)),
            bundle_name=BUNDLE[: -len(".bundle")],
            crc=111,
            bundle_size=1000,
            common_raw=bytes.fromhex("0000200004000000"),
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.AssetBundleRequestOptions",
        ),
        type_asm=UNITY_CORE,
        type_cls="UnityEngine.AssetBundle",
        rank=rank,
    )


def _address_entry(rank: int, key: str, internal_id: str) -> cbw.BinEntry:
    return cbw.BinEntry(
        loc_offset=-1,
        primary_key=key,
        internal_id=internal_id,
        provider=BUNDLED_PROVIDER,
        dep_ranks=(0,),
        dep_hash=999,
        type_asm=UNITY_CORE,
        type_cls="UnityEngine.GameObject",
        rank=rank,
    )


def _synth_catalog() -> bytes:
    """4 条目 / 4 键：一个包 ＋ 两个 prefab 地址（旧键/新键）＋ 一个 png 地址。"""
    model = cbw.BinModel(
        provider_id="AddressablesMainContentCatalog",
        build_result_hash="0" * 32,
    )
    model.entries = [
        _bundle_entry(0),
        _address_entry(1, OLD_KEY, PREFAB),
        _address_entry(2, NEW_KEY, PREFAB),
        _address_entry(3, ICON_KEY, ICON),
    ]
    model.keys = [
        cbw.BinKey(name=name, ranks=(rank,), type_asm="mscorlib", type_cls="System.String")
        for rank, name in enumerate((BUNDLE, OLD_KEY, NEW_KEY, ICON_KEY))
    ]
    raw = cbw.serialize(model)
    assert cbw.looks_like_catalog_bin(raw)
    return raw


def _units_payload(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _units_rows() -> list[dict]:
    return [
        {
            "Id": 9,
            "Name": "T-72B",
            "HUDName": "T-72B",
            "ModelFileName": OLD_KEY,
            "ThumbnailFileName": "RU_T72B-Label",
        },
        {"Id": 90, "Name": "重名甲", "ModelFileName": DUP_KEY, "ThumbnailFileName": "DUP-Label"},
        {"Id": 478, "Name": "重名乙", "ModelFileName": DUP_KEY, "ThumbnailFileName": "DUP-Label"},
    ]


def _make_game(tmp_path: Path) -> tuple[Path, Path]:
    """假游戏根（catalog.bin ＋ catalog.hash ＋ `BA_Mod\\BA_Mod.zip`）＋ 工程目录。"""
    root = tmp_path / "game"
    aa = root / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    (aa / "PC").mkdir(parents=True)
    raw = _synth_catalog()
    (aa / "catalog.bin").write_bytes(raw)
    (aa / "catalog.hash").write_bytes(cbw.hash128_hex(raw).encode("ascii"))
    external_db.ensure_zip_dir(root)
    dbzip.DbZip(
        tables={"Units": _units_payload(_units_rows()), "Armors": b'[{"Id":1}]'}
    ).write(external_db.game_zip_path(root))
    project = tmp_path / "project"
    project.mkdir()
    return root, project


def _rows(zip_path: Path) -> list[dict]:
    raw = dbzip.DbZip.read(zip_path).tables["Units"]
    return json.loads(raw.decode("utf-8"))


def _plan(
    root: Path, project: Path, *, unit: str = OLD_KEY, key: str = NEW_KEY, row_id: int | None = 9
):
    return new_asset.plan_repoint(
        unit=unit, key=key, row_id=row_id, game_root=root, project_root=project
    )


def test_repoint_moves_the_row_and_leaves_catalog_alone(tmp_path):
    """★ 判据：行 X -> Y；catalog 逐字节不变；撤得回原样。"""
    root, project = _make_game(tmp_path)
    catalog = root / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "catalog.bin"
    zip_path = external_db.game_zip_path(root)
    before_catalog, before_zip = catalog.read_bytes(), zip_path.read_bytes()

    plan = _plan(root, project)
    assert (plan.row_id, plan.old_key, plan.new_key) == (9, OLD_KEY, NEW_KEY)
    assert plan.asset_path == PREFAB
    assert plan.row_hud == "T-72B"

    report = new_asset.run_repoint(plan, check_game_running=False)
    assert report.verified

    # ① 那一格真的变了（读的是**盘上那份**）
    assert _rows(zip_path)[plan.row_index]["ModelFileName"] == NEW_KEY
    # ② catalog 一个字节都没动、条目读数三项都没变
    assert catalog.read_bytes() == before_catalog
    assert report.catalog_counts_after == plan.catalog_counts
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == report.zip_sha256
    # ③ 撤得回原样（逐字节）
    outcome = new_asset.restore(game_root=root, project_root=project, check_game_running=False)
    assert outcome.ok
    assert zip_path.read_bytes() == before_zip


def test_repoint_refuses_a_key_that_is_not_in_the_catalog(tmp_path):
    root, project = _make_game(tmp_path)
    with pytest.raises(AssetError) as info:
        _plan(root, project, key="NOPE123")
    assert "没有可用的资产地址键" in str(info.value)


def test_repoint_refuses_a_bundle_key(tmp_path):
    """包条目不是资产地址（`asset_path_of_key` 只认非 `.bundle` 的 internal_id）。"""
    root, project = _make_game(tmp_path)
    with pytest.raises(AssetError) as info:
        _plan(root, project, key=BUNDLE)
    assert "没有可用的资产地址键" in str(info.value)


def test_repoint_refuses_an_address_that_is_not_a_prefab(tmp_path):
    root, project = _make_game(tmp_path)
    with pytest.raises(AssetError) as info:
        _plan(root, project, key=ICON_KEY)
    assert "不是模型地址" in str(info.value)


def test_repoint_refuses_when_the_row_already_points_there(tmp_path):
    root, project = _make_game(tmp_path)
    with pytest.raises(AssetError) as info:
        _plan(root, project, key=OLD_KEY)
    assert "已经是" in str(info.value)


def test_repoint_refuses_an_ambiguous_row_without_id(tmp_path):
    root, project = _make_game(tmp_path)
    with pytest.raises(AssetError) as info:
        _plan(root, project, unit=DUP_KEY, key=NEW_KEY, row_id=None)
    assert "不唯一" in str(info.value) or "有 2 行" in str(info.value)


def test_dry_run_writes_nothing(tmp_path):
    root, project = _make_game(tmp_path)
    zip_path = external_db.game_zip_path(root)
    before = zip_path.read_bytes()
    report = new_asset.run_repoint_dry(_plan(root, project))
    assert report.dry_run and not report.verified
    assert zip_path.read_bytes() == before
    assert not (project / "patches").exists()
    assert any("干跑" in line for line in report.lines())