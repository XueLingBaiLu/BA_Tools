r"""资产浏览器内核的用例：真包列表 + 建议口径 + 批量登记（含**已登记键跳过**的正控）。

## 判据一行

    .venv\Scripts\python.exe -m pytest tests/test_asset_browser.py -q -n 0
      -> 真包 643 个对象、容器那条 prefab 名 = US_AH-1Z Viper；同一批登记跑两遍，
         第二遍 added == 0 / skipped == 2（第二遍的 added 就是主判据）

## 三条硬口径（都钉在用例里，别改口径只改数字）

1. **真包**：`_work/unitybuild/out-ah1z3/us_ah_1z_viper_assets_all_*.bundle`（26.9 MB）。
   它在本机就在；不在就 skip（不拿游戏目录当夹具）。
2. **坏包 / 空包必须报错**：不存在、0 字节、垃圾字节 —— 三种都断言 AssetError。
3. **批量登记是纯函数**：入参字节调用后逐字节不变；已登记的键**跳过**（不是报错）。

⛔ 不读游戏目录：合成的二进制 catalog 用产品的 `catalog_bin_write.serialize` 写 ——
手拼字节等于第二份实现（照 tests/test_writer_catalog_bin.py 的同一条口径）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.core.assets import asset_browser as ab
from ba_mod_maker.core.assets import catalog_bin as cb
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError

import os

# 工作根：数据已移出仓（2026-11-09）。也可用 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REAL_BUNDLE = (
    WORK
    / "unitybuild"
    / "out-ah1z3"
    / "us_ah_1z_viper_assets_all_f512cf5a4fb37d72a3a567e177da06d6.bundle"
)

BUNDLE_A = "alpha_assets_all_" + "1" * 32 + ".bundle"
BUNDLE_B = "beta_assets_all_" + "2" * 32 + ".bundle"
EXISTING_KEY = "SYNTH_EXISTING"
BUNDLE_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
BUNDLED_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
UNITY_CORE = "UnityEngine.CoreModule"


def _synth_catalog() -> bytes:
    """合成的二进制 catalog：2 条包登记 ＋ 1 条**已登记**的地址（登记跳过的正控要用它）。"""
    model = cbw.BinModel(
        provider_id="AddressablesMainContentCatalog",
        build_result_hash="0" * 32,
    )
    model.entries = [
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=BUNDLE_A,
            internal_id="aa/PC/" + BUNDLE_A,
            provider=BUNDLE_PROVIDER,
            dep_ranks=(),
            dep_hash=0,
            extra=cbw.AssetBundleOptions(
                hash_bytes=bytes(range(16)),
                bundle_name=BUNDLE_A[: -len(".bundle")],
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
            primary_key=BUNDLE_B,
            internal_id="aa/PC/" + BUNDLE_B,
            provider=BUNDLE_PROVIDER,
            dep_ranks=(),
            dep_hash=0,
            extra=cbw.AssetBundleOptions(
                hash_bytes=bytes(range(16, 32)),
                bundle_name=BUNDLE_B[: -len(".bundle")],
                crc=222,
                bundle_size=2000,
                common_raw=bytes.fromhex("0000200004000000"),
                type_asm=UNITY_CORE,
                type_cls="UnityEngine.AssetBundleRequestOptions",
            ),
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.AssetBundle",
            rank=1,
        ),
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=EXISTING_KEY,
            internal_id="Assets/Synth/Existing.prefab",
            provider=BUNDLED_PROVIDER,
            dep_ranks=(0,),
            dep_hash=0,
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.GameObject",
            rank=2,
        ),
        # 一条 Sprite 型条目：add_address(resource_type=3) 要「从真件抄类型」，没有它就抄不到
        cbw.BinEntry(
            loc_offset=-1,
            primary_key="SYNTH_SPRITE_TEMPLATE",
            internal_id="Assets/Mods/BAModMaker/template.png",
            provider=BUNDLED_PROVIDER,
            dep_ranks=(1,),
            dep_hash=0,
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.Sprite",
            rank=3,
        ),
    ]
    model.keys = [
        cbw.BinKey(name=BUNDLE_A, ranks=(0,), type_asm="mscorlib", type_cls="System.String"),
        cbw.BinKey(name=BUNDLE_B, ranks=(1,), type_asm="mscorlib", type_cls="System.String"),
        cbw.BinKey(name=EXISTING_KEY, ranks=(2,), type_asm="mscorlib", type_cls="System.String"),
        cbw.BinKey(
            name="SYNTH_SPRITE_TEMPLATE",
            ranks=(3,),
            type_asm="mscorlib",
            type_cls="System.String",
        ),
    ]
    raw = cbw.serialize(model)
    # 夹具自证：它得真的是「4 键 / 4 条目」的二进制 catalog（不然下面的断言全是假的）
    assert cb.looks_like_catalog_bin(raw) and cbw.counts(raw) == (4, 4, 4)
    return raw


# ---------------------------------------------------------------------------
# 1. 真包：列表 / prefab 名 / 叶子类没名字
# ---------------------------------------------------------------------------


def test_real_bundle_lists_every_object_with_six_keys() -> None:
    """正控：真包 643 个对象，每行六个键一个不少（键名是 GUI 的收口契约）。"""
    if not REAL_BUNDLE.is_file():
        pytest.skip(f"真包不在：{REAL_BUNDLE}")
    rows = ab.list_bundle_contents(REAL_BUNDLE)
    assert len(rows) == 643, len(rows)
    for row in rows:
        assert set(row) == set(ab.ROW_KEYS), row
    assert sum(1 for row in rows if row["asset_name"]) == 341
    # 排序口径：path_id 升序
    assert [row["path_id"] for row in rows] == sorted(row["path_id"] for row in rows)


def test_real_bundle_exposes_the_prefab_name_and_real_container_path() -> None:
    """★ 主判据：容器那条的**对象名就是 prefab 名**，asset_path 是包内真实容器路径。"""
    if not REAL_BUNDLE.is_file():
        pytest.skip(f"真包不在：{REAL_BUNDLE}")
    rows = ab.list_bundle_contents(REAL_BUNDLE)
    prefabs = [row for row in rows if row["asset_path_source"] == "container"]
    assert len(prefabs) == 1, prefabs
    prefab = prefabs[0]
    assert prefab["asset_name"] == "US_AH-1Z Viper"
    assert prefab["asset_path"] == (
        "assets/resources_moved/modelprefabs/helicopters/us_ah-1z viper.prefab"
    )
    assert prefab["type_class"] == "GameObject" and prefab["class_id"] == 1


def test_real_bundle_leaf_classes_have_no_name_but_are_still_listed() -> None:
    """不是失败：没有类型树的叶子类（Mesh/Texture2D/Shader/MonoScript）名字读不出来。"""
    if not REAL_BUNDLE.is_file():
        pytest.skip(f"真包不在：{REAL_BUNDLE}")
    rows = ab.list_bundle_contents(REAL_BUNDLE)
    leaves = [row for row in rows if row["class_id"] in ab.NO_TYPE_TREE_CLASSES]
    assert leaves, "真包里应该至少有一个叶子类对象"
    for row in leaves:
        assert row["asset_name"] == ""
        assert row["asset_path"] == "" and row["asset_path_source"] == "none"
    # 有名字的对象：路径来源只能是 suggested（不是容器条目）
    suggested = [row for row in rows if row["asset_path_source"] == "suggested"]
    assert suggested
    for row in suggested:
        assert row["asset_path"].startswith(ab.MODEL_ASSET_PREFIX)
        assert row["asset_path"].endswith(".prefab")


# ---------------------------------------------------------------------------
# 2. 空包 / 坏包必须报错（fail-closed）
# ---------------------------------------------------------------------------


def test_missing_bundle_raises() -> None:
    with pytest.raises(AssetError):
        ab.list_bundle_contents(REAL_BUNDLE.parent / "definitely-not-here.bundle")


def test_empty_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "empty.bundle"
    bad.write_bytes(b"")
    with pytest.raises(AssetError):
        ab.list_bundle_contents(bad)


def test_garbage_bundle_raises(tmp_path: Path) -> None:
    bad = tmp_path / "garbage.bundle"
    bad.write_bytes(b"NOT-A-UNITYFS-BUNDLE" * 64)
    with pytest.raises(AssetError):
        ab.list_bundle_contents(bad)


# ---------------------------------------------------------------------------
# 3. 建议口径（纯函数）
# ---------------------------------------------------------------------------


def test_suggest_key_uppercases_and_replaces_illegal_chars() -> None:
    assert ab.suggest_key("US_AH-1Z Viper") == "US_AH-1Z_VIPER"
    assert ab.suggest_key("  us ah 1z  ") == "US_AH_1Z"
    assert ab.suggest_key("./x") == "X"
    assert ab.suggest_key("A" * 100) == "A" * ab.KEY_MAX_LENGTH


def test_suggest_key_refuses_blank_or_underivable() -> None:
    for bad in ("", "   ", "_", "-", "."):
        with pytest.raises(AssetError):
            ab.suggest_key(bad)


def test_suggest_asset_path_has_prefab_suffix_once() -> None:
    assert ab.suggest_asset_path("AH-1Z Viper") == (
        ab.MODEL_ASSET_PREFIX + "AH-1Z Viper.prefab"
    )
    assert ab.suggest_asset_path("already.prefab") == ab.MODEL_ASSET_PREFIX + "already.prefab"
    with pytest.raises(AssetError):
        ab.suggest_asset_path("   ")


# ---------------------------------------------------------------------------
# 4. 批量登记：正控 = 同一批跑两遍，第二遍 0 条新增
# ---------------------------------------------------------------------------


def test_register_batch_adds_once_then_skips_everything() -> None:
    raw = _synth_catalog()
    entries = [
        {"key": "SYNTH_NEW", "asset_path": "Assets/Synth/New.prefab", "bundle": BUNDLE_A},
        {"key": EXISTING_KEY, "asset_path": "Assets/Synth/Existing.prefab", "bundle": BUNDLE_A},
        {"key": "SYNTH_NOBUNDLE", "asset_path": "Assets/Synth/X.prefab", "bundle": "nope.bundle"},
        {"asset_path": "Assets/Synth/NoKey.prefab", "bundle": BUNDLE_A},
    ]
    first = ab.register_batch(raw, entries, source_note="用例合成件")
    assert [item["status"] for item in first["results"]] == [
        "added",
        "skipped",
        "failed",
        "failed",
    ]
    assert (first["added"], first["skipped"], first["failed"]) == (1, 1, 2)
    assert first["changed"] is True
    assert first["results"][1]["reason"] == "key_exists"
    assert first["results"][2]["reason"] == "bundle_not_registered"
    assert first["results"][3]["reason"].startswith("bad_entry:")
    assert tuple(first["results"][0]) == ("key", "asset_path", "bundle", "status", "reason")
    # 新键真的进了键表，且条目数 +1
    names = cbw.key_names(first["catalog_bytes"])
    assert "SYNTH_NEW" in names and len(names) == 5
    assert first["counts_after"] == (5, 5, 5)
    # 依赖声明 = own-only：新条目只依赖自己那个包条目（1 个，且是包 A）
    model = cbw.read_model(first["catalog_bytes"])
    rank = names.index("SYNTH_NEW")
    new_entry = model.entries[model.keys[rank].ranks[0]]
    assert new_entry.internal_id == "Assets/Synth/New.prefab"
    assert len(new_entry.dep_ranks) == 1
    assert model.entries[new_entry.dep_ranks[0]].internal_id.endswith(BUNDLE_A)
    # ★ 第二遍：同一批（键已在）⇒ 0 条新增（这就是那条正控）
    second = ab.register_batch(first["catalog_bytes"], entries)
    assert second["added"] == 0
    assert second["skipped"] == 2
    assert second["failed"] == 2
    assert second["changed"] is False
    assert second["catalog_bytes"] == first["catalog_bytes"]
    assert all(item["status"] != "added" for item in second["results"])


def test_register_batch_does_not_touch_the_input_bytes() -> None:
    """纯函数：入参 bytes 调用前后逐字节相同（⛔ 不许原地改）。"""
    raw = _synth_catalog()
    snapshot = bytes(raw)
    ab.register_batch(
        raw,
        [{"key": "SYNTH_PURE", "asset_path": "Assets/Synth/Pure.prefab", "bundle": BUNDLE_B}],
    )
    assert raw == snapshot
    assert cbw.counts(raw) == (4, 4, 4)


def test_register_batch_per_entry_resource_type_is_honoured() -> None:
    """entry 里的 resource_type 覆盖函数默认值（3 = 图标，真件是 Sprite 型）。"""
    raw = _synth_catalog()
    out = ab.register_batch(
        raw,
        [
            {
                "key": "SYNTH_ICON",
                "asset_path": "Assets/Mods/BAModMaker/icon.png",
                "bundle": BUNDLE_B,
                "resource_type": 3,
            }
        ],
        resource_type=1,
    )
    assert out["added"] == 1
    names = cbw.key_names(out["catalog_bytes"])
    model = cbw.read_model(out["catalog_bytes"])
    entry = model.entries[model.keys[names.index("SYNTH_ICON")].ranks[0]]
    assert entry.dep_ranks and model.entries[entry.dep_ranks[0]].internal_id.endswith(BUNDLE_B)


def test_register_batch_refuses_non_catalog_bytes() -> None:
    with pytest.raises(AssetError):
        ab.register_batch(b"not-a-catalog", [])
    with pytest.raises(AssetError):
        ab.register_batch("not-bytes", [])  # type: ignore[arg-type]


def test_register_batch_empty_entries_is_a_noop() -> None:
    raw = _synth_catalog()
    out = ab.register_batch(raw, [])
    assert (out["added"], out["skipped"], out["failed"]) == (0, 0, 0)
    assert out["changed"] is False and out["catalog_bytes"] == raw

