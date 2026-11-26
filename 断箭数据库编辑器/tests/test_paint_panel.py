"""W6-B2 ③ 涂装面板的机械判据（工单 §3「验收判据」那张表）。

## 这把尺子量什么

1. **纯函数 paint_panel_lines**：空 / 坏 JSON / 正常三态（首行**永远**是级别，
   空与坏都不许是空面板）；
2. **等长公式**：整条 mip 链的字节数由 (格式, 宽, 高, mip 数) 唯一决定 ——
   512² DXT5 + 10 级 = **349,552 B**（== 靶子那张的 m_StreamData.size）；
3. **编码器**：Pillow 逐级编 DDS、去掉 128 B 头、按 mip0 -> mipN 拼 ⇒ 长度 == 公式；
4. **负控（能红才算尺子）**：导回 PNG **尺寸不符** / 载荷**长度不符** ⇒ 必须抛并中止；
5. **冻结标识**：面板 bl_idname / bl_label、3 个算子的 bl_idname、BamodSettings 的 7 个新字段、
   「等级只有一档」、真写回仍走 invoke_confirm + confirmed、writer 入口的调用形状、
   以及 `panels / operators` **模块顶层不许 import asset_kernel**（否则插件注册不上）。

⛔ 本文件**不读游戏盘**：编码用的 PNG 是现场造的（`_synthetic_rgba`）。
真件的读数（哪张图 / 多少字节 / 是否与 m_StreamData.size 等长）由 `_work/w6_paint_*.py`
与 Blender 无头那一趟给出。

## 为什么是 bpy 桩 + 合成包

`blender_addon/*.py` 依赖 bpy，而 pytest 里没有 Blender。所以：
- bpy 换成**最小桩**（只要 `props.*` 能当类属性、`types.Panel/Operator/PropertyGroup` 能当基类）；
- `operators.py` 顶层的 `from . import importer, runtime` 由**合成包**里两个空模块顶上
  —— ⛔ **不执行** `blender_addon/__init__.py`（那条路要真 Blender，由 tests/test_contracts.py
  的 C4「注册探针」管）。

判据一行：`pytest tests/test_paint_panel.py -q` -> 通过数 从 0 收集变 26 passed。
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"
#: 合成包名 —— ⛔ 不用 `blender_addon` 那个真名字，免得撞上别的测试或真 Blender。
PKG = "bamod_paint_under_test"

#: 靶子读数（Lead 实测；本机 _work/w6_paint_encode_probe.py 在整条链上复核过）。
TARGET_WIDTH = 512
TARGET_HEIGHT = 512
TARGET_MIPS = 10
TARGET_DXT5_BYTES = 349552
TARGET_DXT1_BYTES = 174776

#: 冻结标识（工单 W6-B2 §2 逐字）。
PAINT_PANEL_ID = "BAMOD_PT_paint"
# ★ C-03（2026-10-11）：旧 ③ 涂装面板降级成 ④ 改 · 外观 的默认折叠子面板 ⇒ 标题去掉编号。
PAINT_PANEL_LABEL = "涂装"
PAINT_OPERATOR_IDS = {
    "BamodPaintExport": "bamod.paint_export",
    "BamodPaintPlan": "bamod.paint_plan",
    "BamodPaintExecute": "bamod.paint_execute",
}
PAINT_FIELDS = (
    "paint_material",
    "paint_slot",
    "paint_export_png",
    "paint_import_png",
    "paint_report_json",
    "paint_level",
    "paint_texture_path_id",
)


# --------------------------------------------------------------------------- #
# 加载 addon（bpy 桩 + 合成包）
# --------------------------------------------------------------------------- #
def _bpy_stub() -> types.ModuleType:
    """最小 bpy 桩：只要能让 blender_addon 的模块**定义**跑完（不执行任何算子）。"""
    existing = sys.modules.get("bpy")
    if existing is not None:
        return existing
    bpy = types.ModuleType("bpy")

    def _prop(**kwargs: object) -> dict[str, object]:
        return dict(kwargs)

    bpy.props = types.SimpleNamespace(  # type: ignore[attr-defined]
        StringProperty=_prop,
        BoolProperty=_prop,
        FloatProperty=_prop,
        EnumProperty=_prop,
        PointerProperty=_prop,
    )
    bpy.types = types.SimpleNamespace(  # type: ignore[attr-defined]
        Panel=type("Panel", (), {}),
        Operator=type("Operator", (), {}),
        PropertyGroup=type("PropertyGroup", (), {}),
    )
    sys.modules["bpy"] = bpy
    return bpy


@functools.lru_cache(maxsize=1)
def _addon() -> dict[str, types.ModuleType]:
    """按路径加载 panels / operators / selftest（带 bpy 桩与合成包的相对 import）。"""
    _bpy_stub()
    package = types.ModuleType(PKG)
    package.__path__ = []  # type: ignore[attr-defined]  -- 空路径：子模块只在 sys.modules 里找
    sys.modules[PKG] = package
    for stub_name in ("importer", "runtime"):
        stub = types.ModuleType(f"{PKG}.{stub_name}")
        sys.modules[stub.__name__] = stub
        setattr(package, stub_name, stub)
    out: dict[str, types.ModuleType] = {}
    # ★ task-9（2026-10-11）拆包：③ 涂装面板在 panels_paint.py，它 `from .panels_common import …`
    #   ⇒ 先把共用件挂进包里（保持 `__path__ = []` 的老口径：⛔ 不让它去盘上找别的兄弟模块）。
    for name in ("unit_picker", "panels_common", "panels_paint", "operators", "selftest"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", ADDON / f"{name}.py")
        assert spec is not None and spec.loader is not None, f"加载不了 {name}.py"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(package, name, module)
        spec.loader.exec_module(module)
        out[name] = module
    return out


if not (ADDON / "panels_paint.py").is_file():
    pytest.skip(
        "旧模块 panels_paint.py 已在插件重做时删除 —— 这份用例待移植",
        allow_module_level=True,
    )
PANELS = _addon()["panels_paint"]
OPERATORS = _addon()["operators"]
SELFTEST = _addon()["selftest"]


def _source(name: str) -> str:
    return (ADDON / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 现场（现场造的 512² / 64² PNG —— ⛔ 不读游戏盘）
# --------------------------------------------------------------------------- #
def _synthetic_rgba(width: int, height: int) -> bytes:
    """确定性 RGBA（带 7.7% 的 A=0 打孔，与 _ALPHATEST_ON 那张同量级）。"""
    return bytes(
        value
        for index in range(width * height)
        for value in (
            (index * 7) & 0xFF,
            (index * 13) & 0xFF,
            (index * 29) & 0xFF,
            0 if index % 13 == 0 else 255,
        )
    )


def _write_png(path: Path, width: int, height: int) -> Path:
    from PIL import Image

    image = Image.frombytes("RGBA", (width, height), _synthetic_rgba(width, height))
    image.save(path)
    return path


@pytest.fixture(scope="module")
def target_png(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """512² / 10 级那条链 —— 与靶子同尺寸、同 mip 数（字节数应当正好 349,552）。"""
    return _write_png(
        tmp_path_factory.mktemp("paint") / "target_512.png", TARGET_WIDTH, TARGET_HEIGHT
    )


@pytest.fixture(scope="module")
def tiny_png(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """64² —— 给负控用（编码快，不必每次编整条 512 链）。"""
    return _write_png(tmp_path_factory.mktemp("paint") / "tiny_64.png", 64, 64)


# --------------------------------------------------------------------------- #
# 1 · paint_panel_lines 三态
# --------------------------------------------------------------------------- #
def test_paint_panel_lines_first_line_is_always_the_level() -> None:
    for report in ("", "{}", "not json at all", json.dumps(["x"])):
        lines = PANELS.paint_panel_lines(report, "L1")
        assert lines, "空面板不算数（⛔ 不许返回空列表）"
        assert lines[0] == "级别 L1 等长（原地覆盖）", lines[0]


def test_paint_panel_lines_empty_state_says_what_to_do() -> None:
    lines = PANELS.paint_panel_lines("", "L1")
    assert lines == ["级别 L1 等长（原地覆盖）", PANELS.PAINT_EMPTY_HINT]


def test_paint_panel_lines_bad_json_does_not_crash() -> None:
    lines = PANELS.paint_panel_lines("{不是 JSON", "L1")
    assert lines[0] == "级别 L1 等长（原地覆盖）"
    assert PANELS.PAINT_JSON_ERROR_HINT in lines[1]
    assert "JSONDecodeError" in lines[1]
    # 负控：坏 JSON 不许伪装成一份正常报告
    assert not [line for line in lines if line.startswith("[③ 涂装")]


def test_paint_panel_lines_normal_renders_each_report_line() -> None:
    report = ["[③ 涂装干跑] 键 RU_T72B", "  等长编码: DXT5 349,552 B"]
    lines = PANELS.paint_panel_lines(json.dumps(report, ensure_ascii=False), "L1")
    assert lines == ["级别 L1 等长（原地覆盖）", *report]


def test_paint_panel_lines_unknown_level_is_reported_not_swallowed() -> None:
    """⛔ 认不出的等级不许悄悄当成 L1（设计稿 §9.10.6：级别列写着 L1 才允许走原地覆盖）。"""
    lines = PANELS.paint_panel_lines("", "Δ")
    assert lines[0] == "级别 Δ（不认识的等级，⛔ 不执行）"
    assert PANELS.paint_panel_lines("", "")[0] == "级别 （未选等级）"


# --------------------------------------------------------------------------- #
# 2 · 等长公式
# --------------------------------------------------------------------------- #
def test_paint_mip_dims_halve_down_to_one() -> None:
    assert OPERATORS.paint_mip_dims(512, 512, 10) == [
        (512, 512),
        (256, 256),
        (128, 128),
        (64, 64),
        (32, 32),
        (16, 16),
        (8, 8),
        (4, 4),
        (2, 2),
        (1, 1),
    ]


def test_paint_mip_bytes_round_up_to_whole_blocks() -> None:
    assert OPERATORS.paint_mip_bytes(4, 4, 16) == 16
    assert OPERATORS.paint_mip_bytes(1, 1, 16) == 16  # 不足一块按一块
    assert OPERATORS.paint_mip_bytes(6, 6, 8) == 32  # ceil(6/4)² = 4 块
    assert OPERATORS.paint_mip_bytes(8, 2, 16) == 32  # 2x1 块


def test_paint_payload_size_matches_the_target_stream() -> None:
    """靶子那一行：512² DXT5 + 10 级 == m_StreamData.size == 349,552 B。"""
    assert (
        OPERATORS.paint_payload_size(512, 512, 10, pixel_format="DXT5") == TARGET_DXT5_BYTES
    )
    assert OPERATORS.paint_payload_size(512, 512, 10, pixel_format="DXT1") == TARGET_DXT1_BYTES


def test_paint_payload_size_positive_control_mip_count_must_matter() -> None:
    """尺子正控：把 mip 数从 10 改成 9，读数**必须**变（不变就说明尺子量不到东西）。"""
    nine = OPERATORS.paint_payload_size(512, 512, 9, pixel_format="DXT5")
    assert nine == TARGET_DXT5_BYTES - 16
    assert nine != TARGET_DXT5_BYTES


def test_paint_payload_size_positive_control_format_must_matter() -> None:
    assert OPERATORS.paint_payload_size(512, 512, 10, pixel_format="DXT1") != (
        OPERATORS.paint_payload_size(512, 512, 10, pixel_format="DXT5")
    )


def test_paint_pixel_format_only_non_crunched() -> None:
    assert OPERATORS.paint_pixel_format(10) == "DXT1"
    assert OPERATORS.paint_pixel_format(12) == "DXT5"
    for crunched in (28, 29):
        with pytest.raises(ValueError, match="W7"):
            OPERATORS.paint_pixel_format(crunched)
    with pytest.raises(ValueError, match="W7"):
        OPERATORS.paint_pixel_format(4)


def test_paint_mip_dims_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="正数"):
        OPERATORS.paint_mip_dims(0, 512, 10)
    with pytest.raises(ValueError, match="mip"):
        OPERATORS.paint_mip_dims(512, 512, 0)


# --------------------------------------------------------------------------- #
# 3 · 编码器长度（+ 4 · 负控）
# --------------------------------------------------------------------------- #
def test_encoder_payload_length_equals_the_formula(target_png: Path) -> None:
    payload, pixel_format = OPERATORS.paint_build_payload(
        target_png,
        format_id=12,
        width=TARGET_WIDTH,
        height=TARGET_HEIGHT,
        mips=TARGET_MIPS,
        expect_size=TARGET_DXT5_BYTES,
    )
    assert pixel_format == "DXT5"
    assert len(payload) == TARGET_DXT5_BYTES
    assert len(payload) == OPERATORS.paint_payload_size(
        TARGET_WIDTH, TARGET_HEIGHT, TARGET_MIPS, pixel_format="DXT5"
    )


def test_encoder_negative_control_png_size_mismatch(
    target_png: Path, tmp_path: Path
) -> None:
    """负控：导回 PNG 缩成 256² ⇒ 必须报「尺寸不符」并中止（能红才算尺子）。"""
    from PIL import Image

    small = tmp_path / "half.png"
    with Image.open(target_png) as handle:
        handle.resize((256, 256)).save(small)
    with pytest.raises(ValueError, match="尺寸不符"):
        OPERATORS.paint_build_payload(
            small,
            format_id=12,
            width=TARGET_WIDTH,
            height=TARGET_HEIGHT,
            mips=TARGET_MIPS,
            expect_size=TARGET_DXT5_BYTES,
        )


def test_encoder_negative_control_length_mismatch_off_by_one(tiny_png: Path) -> None:
    """G1 / G7 负控：期望值比真值多 1 B（= 故意写超 1 个字节）⇒ 必须报「长度不符」。"""
    exact = OPERATORS.paint_payload_size(64, 64, 7, pixel_format="DXT5")
    with pytest.raises(ValueError, match="长度不符"):
        OPERATORS.paint_build_payload(
            tiny_png, format_id=12, width=64, height=64, mips=7, expect_size=exact + 1
        )
    # 正控（同一把尺子的另一头）：期望值正好时**不抛**
    payload, _ = OPERATORS.paint_build_payload(
        tiny_png, format_id=12, width=64, height=64, mips=7, expect_size=exact
    )
    assert len(payload) == exact


def test_encoder_negative_control_shorter_mip_chain(tiny_png: Path) -> None:
    """负控：只编 6 级 mip（少一级）⇒ 长度对不上，必须报「长度不符」。"""
    full = OPERATORS.paint_payload_size(64, 64, 7, pixel_format="DXT5")
    shorter = OPERATORS.paint_payload_size(64, 64, 6, pixel_format="DXT5")
    assert shorter < full, "少一级必须更短 —— 否则这条负控本身就不成立"
    with pytest.raises(ValueError, match="长度不符"):
        OPERATORS.paint_build_payload(
            tiny_png, format_id=12, width=64, height=64, mips=6, expect_size=full
        )


def test_encoder_missing_png_must_raise(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        OPERATORS.paint_build_payload(
            tmp_path / "nope.png", format_id=12, width=64, height=64, mips=7, expect_size=1
        )


def test_paint_alpha_stats_counts_pixels_with_alpha_le_2() -> None:
    rgba = bytes(
        [
            0, 0, 0, 0,
            1, 2, 3, 2,
            1, 2, 3, 3,
            1, 2, 3, 255,
        ]
    )
    stats = OPERATORS.paint_alpha_stats(rgba)
    assert stats == {"pixels": 4, "alpha_le2": 2, "alpha_le2_ratio": 0.5}
    assert OPERATORS.paint_alpha_stats(b"") == {
        "pixels": 0,
        "alpha_le2": 0,
        "alpha_le2_ratio": 0.0,
    }


# --------------------------------------------------------------------------- #
# 5 · 冻结标识
# --------------------------------------------------------------------------- #
def _paint_level_items(source: str) -> list:
    """properties.py 源码 -> BamodSettings.paint_level 的 items 字面量。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "BamodSettings"):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            if stmt.target.id != "paint_level":
                continue
            for keyword in _prop_call(stmt).keywords:
                if keyword.arg == "items":
                    return ast.literal_eval(keyword.value)
    raise AssertionError("properties.py 里没找到 BamodSettings.paint_level 的 items")


def _fields() -> dict[str, ast.AnnAssign]:
    tree = ast.parse(_source("properties.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BamodSettings":
            return {
                stmt.target.id: stmt
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError("properties.py 里没找到 BamodSettings")


def _prop_call(node: ast.AnnAssign) -> ast.Call:
    """`name: bpy.props.X(...)` 是**注解式**声明（Blender 读 __annotations__，那个调用在
    annotation 上）；也兜住 `name: T = bpy.props.X(...)` 那种赋值式写法。

    ⚠ 第一版把 call 写成 `node.value` —— 对注解式声明它恒为 None，两条用例当场红。
    """
    for candidate in (node.annotation, node.value):
        if isinstance(candidate, ast.Call):
            return candidate
    raise AssertionError(f"{ast.dump(node.target)} 不是 bpy.props.X(...) 形式的声明")


def _prop_kwargs(node: ast.AnnAssign) -> dict[str, ast.expr]:
    call = _prop_call(node)
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}


def test_bamod_settings_has_the_seven_frozen_paint_fields() -> None:
    fields = _fields()
    assert set(PAINT_FIELDS) <= set(fields), f"少的字段：{set(PAINT_FIELDS) - set(fields)}"
    defaults = {
        "paint_material": "RU_T72B_track",
        "paint_slot": "_BaseColorMap",
        "paint_export_png": "_work/w6_paint_export.png",
        "paint_import_png": "_work/w6_paint_export.png",
        "paint_report_json": "",
        "paint_texture_path_id": "",
    }
    for name, expected in defaults.items():
        assert ast.literal_eval(_prop_kwargs(fields[name])["default"]) == expected, name
    assert ast.literal_eval(_prop_kwargs(fields["paint_level"])["default"]) == "L1"


def test_paint_level_enum_has_exactly_one_level() -> None:
    """⛔ 不许造假档位：下拉里出现什么，就必须真的做过什么（本单只有 L1 等长）。"""
    source = _source("properties.py")
    items = _paint_level_items(source)
    assert len(items) == 1, f"paint_level 只能有一档，实际 {len(items)} 档：{items}"
    assert items[0][0] == "L1"
    assert "等长" in items[0][1]
    # 另一侧：同一份源码里 writeback_level 也是一档，抽取器必须真的认**字段名**，
    # 抽到的是 paint_level 那一条（本单唯一的档位说明里写着 mip）。
    assert "mip" in items[0][2]
    # 尺子正控：同一把尺子对**两档**的源码必须报 2（能红才算尺子）—— 用自造源码，
    # ⛔ 不去改真文件（第一版拿 str.replace 动真源码，既打偏到 writeback_level 又把括号弄丢）。
    doctored = (
        "class BamodSettings:\n"
        "    paint_level: bpy.props.EnumProperty(\n"
        "        items=[\n"
        '            ("L1", "L1 等长（原地覆盖）", ""),\n'
        '            ("Δ", "变长（本单不支持）", ""),\n'
        "        ],\n"
        '        default="L1",\n'
        "    )\n"
    )
    assert len(_paint_level_items(doctored)) == 2


def test_panel_and_operator_identifiers_are_frozen() -> None:
    panel = PANELS.BamodPaintPanel
    assert panel.bl_idname == PAINT_PANEL_ID
    assert panel.bl_label == PAINT_PANEL_LABEL
    assert panel.bl_space_type == "VIEW_3D"
    for class_name, idname in PAINT_OPERATOR_IDS.items():
        assert getattr(OPERATORS, class_name).bl_idname == idname, class_name


def test_paint_panel_wires_the_three_operators() -> None:
    source = _source("panels_paint.py")
    for idname in PAINT_OPERATOR_IDS.values():
        assert f'"{idname}"' in source, idname


def test_paint_panel_renders_only_through_the_pure_function() -> None:
    """面板**只渲染不计算**：draw() 里必须有 paint_panel_lines（而不是自己拼行）。"""
    segment = _class_source("panels_paint.py", "BamodPaintPanel")
    assert "paint_panel_lines(" in segment
    assert "paint_report_json" in segment


def _class_source(name: str, class_name: str) -> str:
    source = _source(name)
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} 里没有 {class_name}")


def test_paint_execute_keeps_the_confirm_handshake() -> None:
    """AGENTS §5：不可逆的大动作必须弹一次确认，且 execute 里 confirmed=False 要拒跑。"""
    source = _source("operators.py")
    for class_name in ("BamodPaintExecute", "BamodWritebackExecute"):
        segment = _class_source("operators.py", class_name)
        assert "invoke_confirm" in segment, class_name
        assert "if not self.confirmed:" in segment, class_name
    # 尺子正控：没有确认闸门的源码必须判否（这把尺子红得起来）
    assert not _has_confirm_gate("class X:\n    def execute(self):\n        return 1\n", "X")
    assert _has_confirm_gate(source, "BamodPaintExecute")


def _has_confirm_gate(source: str, class_name: str) -> bool:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            segment = ast.get_source_segment(source, node) or ""
            return "invoke_confirm" in segment and "if not self.confirmed:" in segment
    return False


def _module_level_imports(source: str) -> list[str]:
    """**只扫模块顶层**（ast.body）—— 函数体内的 import 不算。"""
    names: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_asset_kernel_is_never_imported_at_module_top() -> None:
    """⛔ 模块顶层 import asset_kernel ⇒ 插件注册不上（那是「面板都没有」，更难查）。"""
    for name in ("panels_paint.py", "operators.py", "properties.py"):
        top = _module_level_imports(_source(name))
        offenders = [item for item in top if item.startswith("ba_mod_maker")]
        assert not offenders, f"{name} 顶层 import 了内核：{offenders}"
    # 尺子正控：顶层的 ba_mod_maker import 必须被判出来（这把尺子红得起来）
    doomed = "from ba_mod_maker.asset_kernel.writer import writeback_texture_file\n"
    assert [item for item in _module_level_imports(doomed) if item.startswith("ba_mod_maker")]
    # 另一侧：这些 import 真的存在 —— 只是都在**函数体内**（延迟 import）
    assert "ba_mod_maker.asset_kernel.writer import" in _source("operators.py")


def test_paint_operators_call_the_frozen_writer_entry() -> None:
    """③ 面板只走 writer 的**唯一入口** writeback_texture_file（签名已冻结）。"""
    tree = ast.parse(_source("operators.py"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "writeback_texture_file"
    ]
    assert len(calls) == 1, f"writeback_texture_file 的调用点应当只有 1 处，实际 {len(calls)}"
    call = calls[0]
    keywords = {keyword.arg for keyword in call.keywords}
    assert {"material_name", "slot", "payload", "dry_run", "project_root"} <= keywords, keywords
    assert len(call.args) == 2, "前两个位置参数应当是 game_root 与 unit_key"


def test_selftest_ruler_covers_paint_and_can_go_red() -> None:
    """selftest 那把尺子扩到了 ③ 涂装（面板 + 3 个算子），且它自己**红得起来**。"""
    assert SELFTEST.PAINT_PANEL == PAINT_PANEL_ID
    assert SELFTEST.PAINT_OPS == ("paint_export", "paint_plan", "paint_execute")
    green = SELFTEST.missing_registrations(
        [PAINT_PANEL_ID],
        ["paint_export", "paint_plan", "paint_execute"],
        panels=(SELFTEST.PAINT_PANEL,),
        ops=SELFTEST.PAINT_OPS,
    )
    assert green == []
    red = SELFTEST.missing_registrations(
        [PAINT_PANEL_ID], ["paint_plan"], panels=(SELFTEST.PAINT_PANEL,), ops=SELFTEST.PAINT_OPS
    )
    assert red == ["bamod.paint_export", "bamod.paint_execute"]
    # W5 那把尺子的语义**一个字没改**（喂假名字仍然必须正好 2 条）
    legacy = SELFTEST.missing_registrations(
        [],
        [],
        panels=(SELFTEST.ABSENT_PANEL,),
        ops=(SELFTEST.ABSENT_OP,),
    )
    assert len(legacy) == 2
