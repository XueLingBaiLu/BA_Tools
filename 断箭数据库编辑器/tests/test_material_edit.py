r"""v0.17.0 判据：**④ 改 · 外观 真的能改材质**（候选 → 参数 → 干跑 → 写回）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_material_edit.py -n 0 -q
      -> 收集 0 -> N；每条**先红后绿**。

## 尺子只有一份

画法那半边（④ 画出来的东西）**不另造尺子** —— 直接复用 `tests/test_gui_look.py` 的 `panels_json()`
与 `u1..u8` 谓词（它们背后是共享尺子 `_work/panelsplit/draw_all.py`）。本文件只加**行为**判据。

## 三条读数（设计稿 §0.1）

| # | 尺子 | 改前 | 目标 |
|---|---|---|---|
| **M1** | ④ 面板画出来的 `operator` 数 | 0 颗 | ≥4 颗 |
| **M2** | 参数白名单（负控：没有的参数**不许**列出来） | —— | 两边都成立 |
| **M3** | 观感入口 | 无 | ★ E3：出图三颗已撤 ⇒ 改走 A9 的 `bamod.viewport_rendered`（② 参考台） |
"""
from __future__ import annotations

import functools
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _gui_look():
    """复用 test_gui_look 的 panels_json（⛔ 本文件不另造画法尺子）。"""
    return _load("material_edit_gui_look", REPO_ROOT / "tests" / "test_gui_look.py")


def _props():
    """影子加载 properties（要 unit_picker；bpy 用桩）。

    ★ 形状照 `test_gui_rules._properties_module` —— ⛔ 不另造一套加载法。
    """
    ruler = _gui_look()._ruler()
    ruler._stub_bpy()
    package = types.ModuleType("material_edit_props")
    package.__path__ = []  # type: ignore[attr-defined]
    sys.modules[package.__name__] = package
    out = None
    for name in ("unit_picker", "panels_common", "properties"):
        module = _load(f"{package.__name__}.{name}", ADDON / f"{name}.py")
        setattr(package, name, module)
        out = module
    assert out is not None
    return out


def _materials_module():
    """影子加载 materials.py（bpy 桩只要 data.materials）—— 候选来源那两条判据用它。"""
    ruler = _gui_look()._ruler()
    ruler._stub_bpy()
    stub = sys.modules["bpy"]
    stub.data = types.SimpleNamespace(materials=[])  # type: ignore[attr-defined]
    sys.path.insert(0, str(REPO_ROOT / "src"))
    # ⚠ 2026-10-18（M2-A）：`materials.py` 现在模块级 `from . import session` ⇒ 当**包**加载
    #   （裸模块会 'attempted relative import with no known parent package'）。
    package = types.ModuleType("material_edit_addon")
    package.__path__ = [str(ADDON)]  # type: ignore[attr-defined]
    sys.modules[package.__name__] = package
    module = _load(f"{package.__name__}.materials", ADDON / "materials.py")
    package.materials = module  # type: ignore[attr-defined]
    return module


def _material(
    name: str, scalar_names: str | None, shader: str = "", game: str = ""
) -> object:
    """一个假材质：`mat.get(prop)` 的行为与 Blender 一致（没有那个属性返回 None）。"""
    props: dict[str, object] = {}
    if scalar_names is not None:
        props["bamod_scalar_names"] = scalar_names
    if shader:
        props["bamod_shader"] = shader
    if game:
        props["bamod_material_name"] = game
    return types.SimpleNamespace(name=name, get=lambda key, _p=props: _p.get(key))


# --------------------------------------------------------------------------- #
# M1 · ④ 面板必须给出**可点的入口**（现在 0 颗算子）
# --------------------------------------------------------------------------- #
#: ★ 2026-10-15（五格归并）：这一格现在叫 **② 改 · 这个件**（`panels_edit.py`），
#: 旧名 `BamodMaterialsPanel` 随 `panels_materials.py` 一起下线。
PANEL = "VIEW3D_PT_ba_edit"
#: **改材质那条链**现在长这样（实测 `_work/testport/edit_panel_ops.py` 打出来的真实控件表）：
#: 一次「材质动作」（`m_action` 分流）＋ 撤 ＋ 选 PNG ＋ 套用 ＋ 几何段。
WANTED_OPERATORS = (
    "ba_mod.edit_material",
    "ba_mod.edit_material_restore",
    "ba_mod.edit_texture_pick",
    "ba_mod.edit_texture",
    "ba_mod.edit_xform",
)
#: 那条链上用户要填的几格（旧那三格 `material_pick` / `mat_param_pick` / `writeback_value` 没了）。
WANTED_PROPS = ("m_action", "m_arg1", "m_texture", "t_slot")


def _panel(flavour: str = "default") -> dict:
    look = _gui_look()
    for item in look.panels_json(flavour)["panels"]:
        if item["class"] == PANEL:
            return item
    raise AssertionError(f"尺子没画到 {PANEL}")


def test_m1_the_edit_panel_offers_the_whole_chain() -> None:
    """★ M1：② 改 里那条链的五颗算子都在（材质动作 / 撤 / 选 PNG / 套用 / 几何段）＋ 四格要填的。"""
    seq = _panel()["seq"]
    ops = [item[1] for item in seq if item[0] == "operator"]
    props = [item[1] for item in seq if item[0] == "prop"]
    missing = [name for name in WANTED_OPERATORS if name not in ops]
    assert missing == [], f"② 改 还缺这些算子：{missing}；现在画出来的是 {ops}"
    nope = [name for name in WANTED_PROPS if name not in props]
    assert nope == [], f"② 改 没画这些格子：{nope}；现在画出来的是 {props}"


def test_m1_the_ruler_can_go_red_when_the_chain_is_missing() -> None:
    """★ 正控：喂一个**只有一颗**算子的假面板 —— 同一段判据必须把缺的其余四颗全报出来。"""
    fake = [["operator", "ba_mod.edit_material", "", "PLAY"]]
    ops = [item[1] for item in fake if item[0] == "operator"]
    missing = [name for name in WANTED_OPERATORS if name not in ops]
    assert missing == list(WANTED_OPERATORS[1:]), missing
    assert len(missing) == 4, missing


def test_m1_the_materials_panel_still_passes_the_look_rules() -> None:
    """★ ④ 画法仍须过 U1/U3/U5/U8（v0.16.0 的口径不许被本轮打翻）。"""
    look = _gui_look()
    limit_h, limit_t = look.constants()["HEADER_WIDTH"], look.constants()["TEXT_WIDTH_MAX"]
    for flavour in ("default", "empty"):
        item = _panel(flavour)
        assert not item["header_error"], item["header_error"]
        assert look.u1_repeats_title(item["seq"], item["bl_label"]) == [], item["bl_label"]
        assert look.u3_box_problems(item["seq"]) == [], item["seq"]
        assert look.u4_overwide(item["seq"], limit_t) == [], flavour
        assert look.u5_row_problems(item["seq"]) == [], flavour
        assert look.u8_alignment_missing(item["seq"]) is False, flavour
        assert look.u2_header_problems(item["header"], limit_h) == [], flavour


# --------------------------------------------------------------------------- #
# 行为 · 选中即写目标（⛔ 空值不许把目标清掉）
# --------------------------------------------------------------------------- #
def _retired(why: str):
    """题目没了：**如实 skip ＋ 点名为什么**，⛔ 不硬凑一条假绿（task-9/10 口径）。"""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            pytest.skip(why)
        return inner

    return wrap


def _settings(**over: object) -> object:
    settings = types.SimpleNamespace(
        writeback_material="",
        writeback_param="",
        writeback_value=0.0,
        material_pick="",
        mat_param_pick="",
    )
    settings.__dict__.update(over)
    return settings


class _Rows:
    """CollectionProperty 的替身（clear / add / len / 下标）—— 形状照尺子的 `_Rows`。',
    ''

    ⚠ 必须是**真类**：`SimpleNamespace` 上挂 `__len__` 不管用（特殊方法看类型，不看实例）。
    `value` 是本轮多出来的那一格（`BamodParamRow` 才有）。
    """

    def __init__(self, value: float = 0.0) -> None:
        self.items: list[object] = []
        self.value = value

    def clear(self) -> None:
        self.items.clear()

    def add(self):
        row = types.SimpleNamespace(key="", name="", value=self.value)
        self.items.append(row)
        return row

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        return self.items[index]


def _rows(pairs: list[tuple[str, str]], value: float = 0.0) -> _Rows:
    rows = _Rows(value)
    for key, name in pairs:
        row = rows.add()
        row.key, row.name = key, name
    return rows


@_retired(
    "题目没了：这一批的行为本体（properties.py::_remember_material_pick /"
    " _remember_param_pick / pick_rows / material_rows）在 blender_addon 里 **0 命中**"
    " —— 随 properties.py 一起下线。② 改 现在用 m_action(EnumProperty) ＋ 文件选择器"
    "(m_texture) 取目标，**没有「候选行 → 选中即写目标」这一层**；"
    "materials.material_candidate_rows() 还在，但全仓没有任何调用点。"
    "⇒ 要恢复得先在产品侧立回那一层（⛔ 不是测试缺口，报告里已点名）。"
)
def test_material_pick_writes_the_target_material() -> None:
    """★ 选一个材质 ⇒ 写进 `writeback_material`；⛔ 空值不写（那等于没选）。"""
    props = _props()
    settings = _settings(material_pick="RU_T72B_track")
    props._remember_material_pick(settings)
    assert settings.writeback_material == "RU_T72B_track"
    before = settings.writeback_material
    settings.material_pick = ""
    props._remember_material_pick(settings)
    assert settings.writeback_material == before


@_retired(
    "题目没了：这一批的行为本体（properties.py::_remember_material_pick /"
    " _remember_param_pick / pick_rows / material_rows）在 blender_addon 里 **0 命中**"
    " —— 随 properties.py 一起下线。② 改 现在用 m_action(EnumProperty) ＋ 文件选择器"
    "(m_texture) 取目标，**没有「候选行 → 选中即写目标」这一层**；"
    "materials.material_candidate_rows() 还在，但全仓没有任何调用点。"
    "⇒ 要恢复得先在产品侧立回那一层（⛔ 不是测试缺口，报告里已点名）。"
)
def test_param_pick_writes_the_field_and_its_current_value() -> None:
    """★ 选一个参数 ⇒ 写进 `writeback_param` **并且**把实读到的当前值填进 `writeback_value`。

    这一条是本轮的核心手感：用户先看见"现在是多少"，再改。
    """
    props = _props()
    rows = _rows([("_EmissiveIntensity", "_EmissiveIntensity = 3")], value=3.0)
    settings = _settings(mat_param_pick="_EmissiveIntensity", writeback_value=99.0)
    settings.mat_param_rows = rows
    props._remember_param_pick(settings)
    assert settings.writeback_param == "_EmissiveIntensity"
    assert settings.writeback_value == 3.0, "当前值没填进来（用户会以为默认值就是现值）"
    # 负控：空值不许改任何东西
    settings.mat_param_pick = ""
    settings.writeback_param = "KEEP"
    settings.writeback_value = 7.0
    props._remember_param_pick(settings)
    assert settings.writeback_param == "KEEP" and settings.writeback_value == 7.0


@_retired(
    "题目没了：这一批的行为本体（properties.py::_remember_material_pick /"
    " _remember_param_pick / pick_rows / material_rows）在 blender_addon 里 **0 命中**"
    " —— 随 properties.py 一起下线。② 改 现在用 m_action(EnumProperty) ＋ 文件选择器"
    "(m_texture) 取目标，**没有「候选行 → 选中即写目标」这一层**；"
    "materials.material_candidate_rows() 还在，但全仓没有任何调用点。"
    "⇒ 要恢复得先在产品侧立回那一层（⛔ 不是测试缺口，报告里已点名）。"
)
def test_param_pick_refills_the_value_even_when_the_target_is_unchanged() -> None:
    """★ 重选**同一个**参数时也要把现值填回来（`remember_pick` 会因为"目标一样"提前返回）。"""
    props = _props()
    rows = _rows([("_Metal", "_Metal = 0.318")], value=0.318)
    settings = _settings(
        mat_param_pick="_Metal", writeback_param="_Metal", writeback_value=0.0
    )
    settings.mat_param_rows = rows
    props._remember_param_pick(settings)
    assert settings.writeback_value == 0.318


# --------------------------------------------------------------------------- #
# 向后兼容 · `pick_rows` 默认行为逐字不变（④⑤⑦ 三格与用例都压着它）
# --------------------------------------------------------------------------- #
@_retired(
    "题目没了：这一批的行为本体（properties.py::_remember_material_pick /"
    " _remember_param_pick / pick_rows / material_rows）在 blender_addon 里 **0 命中**"
    " —— 随 properties.py 一起下线。② 改 现在用 m_action(EnumProperty) ＋ 文件选择器"
    "(m_texture) 取目标，**没有「候选行 → 选中即写目标」这一层**；"
    "materials.material_candidate_rows() 还在，但全仓没有任何调用点。"
    "⇒ 要恢复得先在产品侧立回那一层（⛔ 不是测试缺口，报告里已点名）。"
)
def test_pick_rows_default_tooltip_is_still_path_id() -> None:
    """★ 加了三个可选参数之后，**默认那条路一个字都没变**
    （C-19 三格的 tooltip 还是 `pathID：`）。
    """
    props = _props()
    settings = _settings(mesh_path_id="")
    settings.mesh_candidate_rows = _rows([("6513056008332064871", "T72_track_d（1024² DXT5）")])
    rows = props.pick_rows(settings, "mesh_candidate_rows", "mesh_path_id")
    assert rows == [("6513056008332064871", "T72_track_d（1024² DXT5）",
                     "pathID：6513056008332064871")], rows


@_retired(
    "题目没了：这一批的行为本体（properties.py::_remember_material_pick /"
    " _remember_param_pick / pick_rows / material_rows）在 blender_addon 里 **0 命中**"
    " —— 随 properties.py 一起下线。② 改 现在用 m_action(EnumProperty) ＋ 文件选择器"
    "(m_texture) 取目标，**没有「候选行 → 选中即写目标」这一层**；"
    "materials.material_candidate_rows() 还在，但全仓没有任何调用点。"
    "⇒ 要恢复得先在产品侧立回那一层（⛔ 不是测试缺口，报告里已点名）。"
)
def test_material_rows_writes_the_current_target_into_the_list() -> None:
    """★ 当前目标永远在下拉里（否则 Blender 会把它重置成第一条 ⇒ 目标被悄悄改掉）。"""
    props = _props()
    settings = _settings(writeback_material="RU_T72B 1")
    settings.material_candidate_rows = _rows([("RU_T72B_track", "RU_T72B_track（HDRP/Lit）")])
    rows = props.material_rows(settings)
    assert [key for key, _n, _t in rows][0] == "RU_T72B 1", rows
    assert "pathID" not in rows[0][2], "材质名不是 pathID，tooltip 不许照抄那一句"


# --------------------------------------------------------------------------- #
# M2 · 候选来源：只列**导入过的**材质（负控：手搓材质不许出现）
# --------------------------------------------------------------------------- #
def test_m2_only_imported_materials_are_offered() -> None:
    """★ M2 负控：手搓材质（没有 `bamod_scalar_names`）**不许**出现在候选里。

    理由：写回是按**游戏里的 m_Name** 找目标的，列错了等于给一个打不打的靶子。
    """
    materials = _materials_module()
    imported = _material("RU_T72B_track", "_Smoothness,_Metallic", "HDRP/Lit")
    handmade = _material("MyOwnShader", None)
    sys.modules["bpy"].data.materials = [imported, handmade]
    rows = materials.material_candidate_rows()
    assert [key for key, _label in rows] == ["RU_T72B_track"], rows
    assert rows[0][1] == "RU_T72B_track（HDRP/Lit）", rows


def test_m2_candidate_keys_are_the_game_material_names() -> None:
    """★ 候选的**键**必须是**游戏里的材质名**，不是 Blender 里那个带单位前缀的。

    ## 为什么（真机读数，2026-10-11 亲眼量的，不是推的）

    `RU_T72B` 导进来之后，Blender 里的材质叫 **`RU_T72B__RU_T72B_track`**
    （单位键 ＋ `__` ＋ 游戏名），而写回是按**游戏里的 m_Name `RU_T72B_track`** 去包里找目标的。
    第一版把 Blender 名当键交给了写回 ⇒ **dry run 会找不到目标**
    （`_work/gui_review/probe_real_chain.py` 当场抓到，本轮已修）。
    """
    materials = _materials_module()
    imported = _material(
        "RU_T72B__RU_T72B_track", "_Smoothness", "HDRP/Lit", game="RU_T72B_track"
    )
    sys.modules["bpy"].data.materials = [imported]
    rows = materials.material_candidate_rows()
    assert [key for key, _label in rows] == ["RU_T72B_track"], rows
    assert rows[0][1] == "RU_T72B_track（HDRP/Lit）", rows
    # 负控：没记过游戏名的材质**退回 Blender 名**（⛔ 也不许硬顶成游戏名）
    plain = _material("HandMade", "_Smoothness", "")
    sys.modules["bpy"].data.materials = [plain]
    assert [key for key, _label in materials.material_candidate_rows()] == ["HandMade"]


def test_m2_material_shader_is_not_guessed() -> None:
    """★ 没记过 shader 的材质 ⇒ 空串（⛔ 不许猜成 HDRP/Lit）。"""
    materials = _materials_module()
    unknown = _material("RU_X", "_Smoothness", "")
    assert materials.material_shader(unknown) == ""

