"""`asset_kernel/unitypy_source.py` 的**跨包 PPtr**（deps）读侧。

来历（2026-10-17 实测）：豹2A8 车体在 `dlc_2_baltic_assets…` 包，履带材质
`US_HeavyTrack` 在 `units` 包 —— PPtr 的 `m_FileID=3` -> `externals[2]` = units 的 cab。
`_read()` 只认同一个 env 里已加载的文件 -> 抛 `FileNotFoundError` 并被吞成 `None` ->
插件拿到 `<material …>` 空记录 -> 履带落到 Blender 默认白。

本文件两道闸：

1. **快**（不开包）：cab 索引的路径/指纹/缓存口径 ＋ `_external_cab()` 的解析；
2. **真件**（`-m slow`，要游戏目录）：同一个 prefab 在 deps=False 下是空材质、
   在 deps=True 下解出 `US_HeavyTrack`，且贴图解码与「直接开 units 包」逐字节相同。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_mod_maker.asset_kernel import unitypy_source as usrc

#: `units_lock.py` 住在仓内 `tools/`（产品路径不带它），真件用例要串行开包就得把它放上 sys.path。
_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ------------------------------------------------------------------ 快：索引口径
def test_cab_index_path_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "cabs.json"
    monkeypatch.setenv(usrc.CAB_INDEX_ENV, str(target))
    assert usrc.cab_index_path(tmp_path) == target


def test_cab_index_path_default_is_user_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(usrc.CAB_INDEX_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    got = usrc.cab_index_path(tmp_path / "aa")
    assert got == tmp_path / "BAModMaker" / "aa_cab_index.json"


def test_aa_stamp_only_tracks_bundles_and_changes_with_size(tmp_path: Path) -> None:
    aa = tmp_path / "PC"
    _write(aa / "a.bundle", b"1234")
    _write(aa / "catalog.bin", b"ignored")
    first = usrc.aa_stamp(aa)
    _write(aa / "catalog.bin", b"ignored-but-longer")
    assert usrc.aa_stamp(aa) == first, "catalog / 非 .bundle 文件不该影响指纹"
    _write(aa / "a.bundle", b"12345")
    assert usrc.aa_stamp(aa) != first, "包大小变了指纹必须变（换版本要失效）"


def test_load_cab_index_returns_cache_when_stamp_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aa = tmp_path / "PC"
    _write(aa / "units.bundle", b"xx")
    cache = tmp_path / "cabs.json"
    cache.write_text(
        json.dumps({"stamp": usrc.aa_stamp(aa), "cabs": {"cab-abc": "units.bundle"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(usrc.CAB_INDEX_ENV, str(cache))
    assert usrc.load_cab_index(aa, build=False) == {"cab-abc": "units.bundle"}


def test_load_cab_index_ignores_stale_stamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aa = tmp_path / "PC"
    _write(aa / "units.bundle", b"xx")
    cache = tmp_path / "cabs.json"
    cache.write_text(
        json.dumps({"stamp": "old", "cabs": {"cab-abc": "units.bundle"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(usrc.CAB_INDEX_ENV, str(cache))
    assert usrc.load_cab_index(aa, build=False) == {}


def test_build_cab_index_skips_broken_bundle(tmp_path: Path) -> None:
    """一个坏包不许废掉整份索引。"""
    aa = tmp_path / "PC"
    _write(aa / "broken.bundle", b"not a bundle at all")
    _write(aa / "missing.bundle", b"")
    assert usrc.build_cab_index(aa) == {}


# ---------------------------------------------------------------- 快：PPtr -> cab
def test_external_cab_reads_externals_by_one_based_file_id() -> None:
    """`m_FileID` 是 1-based 下标；实测 dlc_2 的 file=3 -> externals[2] = units 的 cab。"""
    names = (
        "CAB-0ec21893c554a6480ec296d5e33e3216",
        "CAB-4716c9c1344ca1695732971278eb4892",
        "CAB-1e53370fe5e57c593d6b445e251b8d1c",
    )
    externals = tuple(SimpleNamespace(path=f"archive:/{n}/{n}") for n in names)
    ptr = SimpleNamespace(m_FileID=3, assetsfile=SimpleNamespace(externals=externals))
    source = usrc.AssetSource.__new__(usrc.AssetSource)
    assert source._external_cab(ptr) == "cab-1e53370fe5e57c593d6b445e251b8d1c"


def test_external_cab_is_empty_for_local_and_out_of_range_pointers() -> None:
    source = usrc.AssetSource.__new__(usrc.AssetSource)
    externals = (SimpleNamespace(path="archive:/CAB-aaa/CAB-aaa"),)
    local = SimpleNamespace(m_FileID=0, assetsfile=SimpleNamespace(externals=externals))
    too_far = SimpleNamespace(m_FileID=9, assetsfile=SimpleNamespace(externals=externals))
    assert source._external_cab(local) == ""
    assert source._external_cab(too_far) == ""


def test_read_ptr_returns_none_without_deps() -> None:
    """deps 关着时行为与旧版一致：跨包 PPtr -> None（不抛）。"""

    class Boom:
        def read(self):
            raise FileNotFoundError("cross-file")

    boom = Boom()
    boom.m_FileID = 1
    boom.assetsfile = SimpleNamespace(
        externals=(SimpleNamespace(path="archive:/CAB-x/CAB-x"),)
    )
    source = usrc.AssetSource.__new__(usrc.AssetSource)
    source.deps = False
    assert source.read_ptr(boom) is None
    assert source.read_ptr(None) is None


# ------------------------------------------------- 真件（-m slow，要游戏目录）
def _aa_pc() -> Path | None:
    try:
        from ba_mod_maker.core.assets.game import aa_dir, find_game_root
    except ImportError:
        return None
    root = find_game_root()
    if root is None:
        return None
    candidate = aa_dir(root) / "PC"
    return candidate if candidate.is_dir() else None


DLC2 = "dlc_2_baltic_assets_assets_all_7ede1c10dcf21654cf3ca0e69c78cc90.bundle"
HULL = "Assets/Resources_moved/ModelPrefabs/DLC2/DLC2_Leopard2A8.prefab"
TRACK_MAT = 2337225327417825442
LOD_MAT = 8349683979956743758
UNITS_CAB = "cab-1e53370fe5e57c593d6b445e251b8d1c"
#: `US_Track_LP_TrackUS_BaseMap` 的 RGBA 摘要（1024x512）。
#: 判据：依赖包里解出来的图必须与「直接开 units 包」逐字节相同。
TRACK_BASE_SHA = "9a01d3abed0816012d7348ab3074f034fbd88fd25ec3c67af5108dd47aa68ad4"


def _require_aa() -> Path:
    aa = _aa_pc()
    if aa is None or not (aa / DLC2).is_file():
        pytest.skip("没有游戏目录 / 没有 dlc_2 包")
    return aa


@pytest.mark.slow
def test_deps_off_leaves_cross_bundle_material_empty() -> None:
    """负控：关掉 deps 时那颗材质仍解不出来（证明这把尺子能红）。"""
    aa = _require_aa()
    from units_lock import units_lock

    with units_lock("pytest:deps-off"), usrc.open_source(aa / DLC2) as source:
        _nodes, materials = source.read_prefab_nodes(HULL)
        rec = materials[TRACK_MAT]
        assert rec["name"] == f"<material {TRACK_MAT}>"
        assert rec["shader"] == ""
        assert rec["texture_slots"] == ()


@pytest.mark.slow
def test_deps_on_resolves_cross_bundle_material_and_texture() -> None:
    """正控：deps=True 时同一颗材质解出真名 + 24 槽，基色图逐字节等于直连 units 包。"""
    aa = _require_aa()
    from units_lock import units_lock

    with units_lock("pytest:deps-on"), usrc.open_source(aa / DLC2, deps=True) as source:
        _nodes, materials = source.read_prefab_nodes(HULL)
        rec = materials[TRACK_MAT]
        assert rec["name"] == "US_HeavyTrack"
        assert rec["shader"] == "HDRP/Lit"
        assert len(rec["texture_slots"]) == 24
        textured = [(slot, tid) for slot, tid in rec["texture_slots"] if tid]
        assert [slot for slot, _tid in textured] == [
            "_BaseColorMap",
            "_MainTex",
            "_MaskMap",
            "_NormalMap",
        ]
        base = dict(textured)["_BaseColorMap"]
        info = source.read_texture(base)
        rgba = source.decode_texture_rgba(base)
        assert (info.width, info.height) == (1024, 512)
        assert hashlib.sha256(rgba).hexdigest() == TRACK_BASE_SHA
        assert UNITS_CAB in source._dep_loaded


@pytest.mark.slow
def test_deps_on_resolves_cross_bundle_shader() -> None:
    """`LODs`（HDRP/Unlit，4 个空槽）—— 材质与 shader 都在依赖包里，名字必须解出来。"""
    aa = _require_aa()
    from units_lock import units_lock

    with units_lock("pytest:deps-shader"), usrc.open_source(aa / DLC2, deps=True) as source:
        _nodes, materials = source.read_prefab_nodes(HULL)
        rec = materials[LOD_MAT]
        assert rec["name"] == "LODs"
        assert rec["shader"] == "HDRP/Unlit"
        assert len(rec["texture_slots"]) == 4
        assert all(tid is None for _slot, tid in rec["texture_slots"])


def test_import_module_uses_deps() -> None:
    """口径：插件那条导入链必须带着 deps=True（源代码级断言，不开包）。"""
    origin = Path(usrc.__file__).resolve().parents[3]
    importer = origin / "blender_addon" / "importer.py"
    if not importer.is_file():
        pytest.skip("没有 blender_addon 目录")
    assert "deps=True" in importer.read_text(encoding="utf-8")
