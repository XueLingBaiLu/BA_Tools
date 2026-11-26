r"""`spec_readings` 的判据（按原版规格体检 —— 只读尺子）。

**判据一行**：`pytest tests/test_spec_readings.py -q -n 0` -> 通过数 0 -> N，
其中 **4 条是负控**（每一条都先证明这把尺子**能红**）。

| # | 喂什么 | 必须 |
|---|---|---|
| 正控 1 | 已知三角形 (0,0,0)/(3,0,0)/(0,4,0) | 世界面积 **6.0**（手算） |
| 正控 2 | 两个**完全重叠**的三角形 | 岛面积之和 = **2 倍**单个 · 覆盖率 = **1 倍**单个 |
| 正控 3 | 铺满 [0,1]² 的两个三角形 | 覆盖率 **100.0 %** |
| 正控 4（slow） | 游戏里 `DLC2_XA180EST` 的原版件 | TD **155.9** · 岛面积和 **143.3 %** ·
|  |  | 覆盖率 **68.02 %** · tris **25,751** · 贴图 **2048²** ·
|  |  | 逐级 tris **(25751, 10300, 2575, 0)** |
| 负控 1 | 通道表里**没有** uv1 | `has_uv1` **False** |
| 负控 2 | coverage 曲线 69.71 / 68.57 / 68.02 | `uv_coverage` 取**最大栅格**那一档 **68.02**，
|  |  | ⛔ 不是 `max()` |
| 负控 3 | 世界面积 0 / UV 面积 0 / 边长 0 | `texel_density` 返回 **0.0**（⛔ 不除零） |
| 负控 4 | 空 indices / 空 uv | 面积 **0.0**，⛔ 不抛 |

⛔ 那 4 条负控的意义（AGENTS §0.9 动作 2）：**正控只证明「跑得通」，能红才叫尺子**。
负控 1 与负控 2 **本轮各真红过一次**（修前 0/3、修前 69.71），所以它们不是摆设。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import spec_readings

REPO = Path(__file__).resolve().parents[1]

#: 正控那件（Baltic Battalion · XA-180EST）；键 = `Units.ModelFileName` 的原样值。
CONTROL_KEY = "DLC2_XA180EST"


def _game_root() -> Path | None:
    """游戏根（没装就是 None —— 用例据此 skip，⛔ 不假装通过）。"""
    from ba_mod_maker.core.assets.game import find_game_root

    root = find_game_root()
    return Path(str(root)) if root else None


# --------------------------------------------------------------------------- #
# 正控（纯函数：不碰游戏文件）
# --------------------------------------------------------------------------- #
def test_world_area_of_a_known_triangle() -> None:
    """正控 1：3-4-5 直角三角形 -> **6.0** m²。"""
    got = spec_readings.mesh_world_area([(0, 0, 0), (3, 0, 0), (0, 4, 0)], [0, 1, 2])
    assert got == pytest.approx(6.0)


def test_stacked_triangles_are_two_different_readings() -> None:
    """正控 2 ★ 本模块存在的理由：**重叠在两条读数里待遇相反**。

    同一片三角形铺两遍：岛面积之和翻倍（重复计），覆盖率**一点不变**（只算一次）。
    谁把这两条合成一个「UV 填充率」，谁就会得出相反的结论。
    """
    uv = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5)]
    twice = [0, 1, 2, 0, 1, 2]
    once = [0, 1, 2]
    one = spec_readings.mesh_uv_area(uv, once)
    assert spec_readings.mesh_uv_area(uv, twice) == pytest.approx(one * 2.0)
    cover_twice = spec_readings.raster_coverage(spec_readings.uv_triangles(uv, twice), 512)
    cover_once = spec_readings.raster_coverage(spec_readings.uv_triangles(uv, once), 512)
    assert cover_twice == pytest.approx(cover_once, abs=0.01)
    assert cover_once > 0.0


def test_raster_coverage_saturates_at_one_hundred() -> None:
    """正控 3：两个三角形铺满整个 UV 方框 -> 覆盖率 **100.0 %**（尺子的上界对）。"""
    uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    idx = [0, 1, 2, 0, 2, 3]
    cover = spec_readings.raster_coverage(spec_readings.uv_triangles(uv, idx), 256)
    assert cover == pytest.approx(100.0, abs=0.5)


# --------------------------------------------------------------------------- #
# 负控（每条都证明尺子**能红**）
# --------------------------------------------------------------------------- #
def test_has_uv1_goes_red_without_the_channel() -> None:
    """负控 1 ★ **本轮真红过一次**：修前读到 0/3（通道串带了 dim，判据写成了精确等于）。"""
    without = spec_readings.MeshReading(
        name="a", path_id=1, triangles=1, vertices=3,
        world_area=1.0, uv_area=1.0,
        channels=("position", "uv0"), channel_dims=(3, 2),
    )
    with_it = spec_readings.MeshReading(
        name="b", path_id=2, triangles=1, vertices=3,
        world_area=1.0, uv_area=1.0,
        channels=("position", "uv0", "uv1"), channel_dims=(3, 2, 2),
    )
    assert without.has_uv1 is False
    assert with_it.has_uv1 is True
    assert with_it.channel_text() == "position(dim3) uv0(dim2) uv1(dim2)"


def test_uv_coverage_takes_the_converged_raster() -> None:
    """负控 2 ★ **本轮也真红过一次**：覆盖率随栅格变大**单调下降**，

    收敛值是**栅格最大**那一档，⛔ 不是 `max()`（修前报了 69.71，正控要的是 68.02）。
    """
    r = spec_readings.SpecReadings(
        unit_key="x",
        coverage={2048: 69.71, 4096: 68.57, 8192: 68.02},
    )
    assert r.uv_coverage == pytest.approx(68.02)
    assert r.uv_coverage != max(r.coverage.values())


def test_texel_density_is_zero_on_degenerate() -> None:
    """负控 3：世界面积 0 / UV 面积 0 / 边长 0 都给 **0.0**（⛔ 不除零）。"""
    assert spec_readings.texel_density(0.5, 0.0, 2048) == 0.0
    assert spec_readings.texel_density(0.0, 1.0, 2048) == 0.0
    assert spec_readings.texel_density(0.5, 1.0, 0) == 0.0


def test_empty_mesh_gives_zero_not_an_exception() -> None:
    """负控 4：空 indices / 空 uv ⇒ **0.0**，⛔ 不抛（空网格是合法状态）。"""
    assert spec_readings.mesh_world_area([], []) == 0.0
    assert spec_readings.mesh_uv_area([], []) == 0.0
    assert spec_readings.uv_triangles([], []) == []


def test_missing_key_raises_instead_of_printing_an_empty_table() -> None:
    """负控：键定位不到 ⇒ `SpecReadError`。

    ⛔ 不许印空表 —— 空表会被读成「这件没有资产」，而真相是「你敲的键不在 catalog 里」。
    """
    root = _game_root()
    if root is None:
        pytest.skip("没装游戏")
    with pytest.raises(spec_readings.SpecReadError):
        spec_readings.readings_of_unit(root, "没有这个键")


def test_bundle_without_a_sole_prefab_is_refused(tmp_path: Path) -> None:
    """负控：包不存在 ⇒ 点名拒（⛔ 不当成「读完了，什么都没有」）。"""
    with pytest.raises(spec_readings.SpecReadError):
        spec_readings.readings_of_bundle(tmp_path / "没有这个包.bundle")


# --------------------------------------------------------------------------- #
# 正控 4：原版件（slow —— 要真读游戏 bundle，默认档不跑）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_control_reproduces_on_the_original_asset() -> None:
    """正控 4：原版 `DLC2_XA180EST` —— 五个读数**全部复现**独立探针。

    独立探针：`_work/mobygames/xa180_td2.py`（本单元只读它写下的读数，⛔ 不复用它算）。
    """
    root = _game_root()
    if root is None:
        pytest.skip("没装游戏")
    r = spec_readings.readings_of_unit(root, CONTROL_KEY)
    assert r.td_runtime == pytest.approx(spec_readings.CONTROL_TD_RUNTIME, abs=0.05)
    assert r.uv_island_sum == pytest.approx(spec_readings.CONTROL_UV_ISLAND_SUM, abs=0.05)
    assert r.uv_coverage == pytest.approx(spec_readings.CONTROL_UV_COVERAGE, abs=0.05)
    assert r.triangles == spec_readings.CONTROL_TRIS_LOD0
    assert r.lod_tris == spec_readings.CONTROL_TRI_LEVELS
    assert r.base_color_edge == spec_readings.CONTROL_TEXTURE_EDGE
    assert r.world_area == pytest.approx(247.490, abs=0.01)
    assert r.uv1_meshes == len(r.meshes) == 3
    # ⚠ 交付口径 = **算出来的**运行口径 × 2，⛔ 不是「正控那个四舍五入过的常数 × 2」
    #   （155.9 是印出来的三位；真值 155.8553... ⇒ 交付档 311.71，不是 311.8）。
    assert r.td_delivery() == pytest.approx(r.td_runtime * spec_readings.DELIVERY_FACTOR, rel=1e-12)
    lines = "\n".join(r.lines())
    assert "155.9" in lines and "143.3" in lines and "68.02" in lines
    assert "⛔ 这不是闸门" in lines

# --------------------------------------------------------------------------- #
# 面板那一侧的缝：`blender_addon/verify_entry.py::spec_lines`（③ 验 的第三把尺子）
# --------------------------------------------------------------------------- #
def _load_verify_entry():
    """把 `blender_addon/verify_entry.py` 当独立模块加载。

    ⚠ 本模块**不 import bpy**（文件头写着），所以不需要 bpy 桩 —— 照抄
    `tests/test_selfcheck.py::_load` 那一份（⛔ 不另发明一套加载器）。
    """

    from conftest import load_addon_module

    # ★ 2026-10-18：verify_entry 有 `from . import session` ⇒ 必须走合成包加载
    #   （裸模块名会 `attempted relative import with no known parent package`）。
    return load_addon_module("verify_entry", as_name="w11_verify_entry_spec")


def test_spec_lines_refuses_when_not_exactly_one_source() -> None:
    """负控：`spec_lines` 要**正好一个**来源 —— 0 个或 2 个都拒（⛔ 不静默挑一个）。"""
    mod = _load_verify_entry()
    for kwargs in ({}, {"unit_key": "X", "bundle": "Y"}):
        with pytest.raises(mod.VerifyEntryError):
            mod.spec_lines(**kwargs)


@pytest.mark.slow
def test_spec_lines_gives_the_panel_the_same_readings() -> None:
    """正控（slow）：面板读到的**就是** CLI 读到的那三个数（同一条内核链）。"""
    root = _game_root()
    if root is None:
        pytest.skip("没装游戏")
    from conftest import load_addon_module

    mod = _load_verify_entry()
    # ★ 2026-10-18：`spec_lines` 不吃 game_root 了（根从会话取 —— M2 整改）
    session_layer = load_addon_module("session")
    with session_layer.session_for(CONTROL_KEY, game_root=root) as unit_session:
        lines = mod.spec_lines(unit_key=CONTROL_KEY, unit_session=unit_session)
    text = "\n".join(lines)
    assert "[按原版规格体检]" in text
    assert "155.9" in text and "143.3" in text and "68.02" in text
    assert "25,751" in text
    assert "⛔ 这不是闸门" in text

@pytest.mark.slow
def test_spec_on_a_bundle_file_is_the_same_ruler() -> None:
    """正控（slow）：**包文件那一档**（`--bundle`）与**单位键那一档**（`--unit`）读数相同。

    两条入口只有「怎么找到源」不同，量的是**同一段**实现（`_readings_within`）——
    这条用例钉住的就是这一点，顺带把 `--asset-path` 那一格与「多条 prefab ⇒ 点名拒」也验了。
    """
    root = _game_root()
    if root is None:
        pytest.skip("没装游戏")
    from ba_mod_maker.asset_kernel import skin_install

    with skin_install.open_unit(root, CONTROL_KEY) as unit:
        bundle_path = Path(str(unit.bundle))
        asset_path = str(unit.asset_path)
    assert bundle_path.is_file(), bundle_path
    with pytest.raises(spec_readings.SpecReadError):
        spec_readings.readings_of_bundle(bundle_path)   # 容器里不止一条 prefab ⇒ 拒
    r = spec_readings.readings_of_bundle(bundle_path, asset_path=asset_path)
    assert r.triangles == spec_readings.CONTROL_TRIS_LOD0
    assert r.lod_tris == spec_readings.CONTROL_TRI_LEVELS
    assert r.uv_island_sum == pytest.approx(spec_readings.CONTROL_UV_ISLAND_SUM, abs=0.05)
    assert r.uv_coverage == pytest.approx(spec_readings.CONTROL_UV_COVERAGE, abs=0.05)
    assert r.base_color_edge == spec_readings.CONTROL_TEXTURE_EDGE