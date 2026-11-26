r"""D 线 · 面板层（`panels_anim.py`）的机械判据：**纯文本 + 注册自检**。

**判据一行**：`pytest tests/test_anim_panel.py -q -n 0` -> 通过数 **0 -> N**（≥ 3 条负控）。

## 为什么按路径加载 + bpy 桩

`blender_addon/__init__.py` 顶层 `import bpy`（只有 Blender 里有）。本文件与
tests/test_blender_skin.py 同一姿势：造一个最小 bpy 桩，**只让模块定义跑完**，
再断面板那几个**纯函数**（`anim_panel_lines` / `panel_ready`）。

## 量什么

| 段 | 判据 | 负控 |
|---|---|---|
| A | 面板文本：空态一句人话 · 坏 JSON 一句人话 + 异常类型 · 正常逐行 | 坏 JSON 不许抛 |
| B | 注册自检：`Scene.bamod` 缺字段 -> `panel_ready` 报**缺哪几个** | 缺一个也要报出来 |
| C | 口径锁：面板要的 6 个字段 / 4 个算子名与源码调用**对得上** | 名字改了必须红 |

"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_ADDON = _REPO / "blender_addon"


def _bpy_stub() -> None:
    if "bpy" in sys.modules:
        return
    bpy = types.ModuleType("bpy")
    # ★ 2026-10-15：桩里必须有 Operator/PropertyGroup —— 缺了会**污染别的测试文件**
    #   见 test_blender_clone / test_paint_panel）。
    bpy.types = types.SimpleNamespace(
        Panel=type("Panel", (), {}),
        Operator=type("Operator", (), {}),
        PropertyGroup=type("PropertyGroup", (), {}),
    )
    sys.modules["bpy"] = bpy


def _load_panel() -> types.ModuleType:
    _bpy_stub()
    name = "bamod_anim_panel_under_test"
    spec = importlib.util.spec_from_file_location(name, _ADDON / "panels_anim.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if not (_ADDON / "panels_anim.py").is_file():
    pytest.skip(
        "旧模块 panels_anim.py 已在插件重做时删除（新面板＝panels_09_anim.py）—— 待移植",
        allow_module_level=True,
    )
PANEL = _load_panel()


# --------------------------------------------------------------------------- #
# A · 面板文本（纯函数）
# --------------------------------------------------------------------------- #
def test_empty_report_gives_a_human_sentence() -> None:
    """空态 -> 一句人话（⛔ 不是空列表：空列表在面板上就是一个什么都没有的框）。"""
    assert PANEL.anim_panel_lines("") == [PANEL.ANIM_EMPTY_HINT]


def test_broken_json_gives_a_human_sentence_with_the_exception_name() -> None:
    """★ 负控：喂坏 JSON -> 一句人话 + 异常类型名，**不许抛**。

    （面板每帧都调它；抛了就是整块面板崩，用户看到的是红框。）
    """
    lines = PANEL.anim_panel_lines("{这不是 JSON")
    assert len(lines) == 1
    assert PANEL.ANIM_ERROR_HINT in lines[0]
    assert "JSONDecodeError" in lines[0] or "ValueError" in lines[0], lines


def test_normal_report_is_drawn_line_by_line() -> None:
    """正常读数逐行画（顺序不许变）。"""
    import json

    payload = json.dumps(["第一行", "第二行"], ensure_ascii=False)
    assert PANEL.anim_panel_lines(payload) == ["第一行", "第二行"]


def test_negative_control_empty_list_falls_back_to_the_hint() -> None:
    """负控：给一个空数组 -> 回落到那句人话（⛔ 不许画成空框）。"""
    assert PANEL.anim_panel_lines("[]") == [PANEL.ANIM_EMPTY_HINT]
    assert PANEL.anim_panel_lines("{}") == [PANEL.ANIM_EMPTY_HINT]


def test_negative_control_non_string_rows_are_stringified() -> None:
    """负控：数组里混了数字/对象 -> 一律转字符串（面板只认 str）。"""
    lines = PANEL.anim_panel_lines("[1, 2]")
    assert lines == ["1", "2"], lines


def test_clip_summary_line_shape() -> None:
    """一行读数（面板与算子共用）—— 形状固定，别让两处各写一份。"""
    line = PANEL.clip_summary_line("T72B hatch", 4, 1.0, 60.0, 1, "1 / 1")
    assert "T72B hatch" in line and "曲线 4 条" in line and "时长 1.0000 s" in line
    assert "60 Hz" in line and "骨 1 根" in line and "1 / 1" in line, line


# --------------------------------------------------------------------------- #
# B · 注册自检
# --------------------------------------------------------------------------- #
def test_panel_ready_reports_every_missing_field() -> None:
    """★ 缺字段 -> 报**缺哪几个**（不是"能不能用"一句话）。"""
    settings = types.SimpleNamespace(anim_clip="x")
    ready, why = PANEL.panel_ready(settings)
    assert ready is False
    for name in ("anim_bundle", "anim_path_table", "anim_allow_partial",
                 "anim_report_json", "anim_summary"):
        assert name in why, (name, why)
    assert "anim_clip" not in why, why


def test_panel_ready_passes_when_all_fields_are_there() -> None:
    """六个字段齐了 -> 可以画。"""
    settings = types.SimpleNamespace(**{n: "" for n in PANEL.ANIM_SETTING_FIELDS})
    ready, why = PANEL.panel_ready(settings)
    assert ready is True and why == ""


def test_negative_control_one_missing_field_is_enough_to_refuse() -> None:
    """★ 负控：只少一个字段也必须报不 ready（⛔ 不许"差不多就行"）。"""
    fields = {n: "" for n in PANEL.ANIM_SETTING_FIELDS}
    fields.pop("anim_summary")
    ready, why = PANEL.panel_ready(types.SimpleNamespace(**fields))
    assert ready is False and "anim_summary" in why, why


# --------------------------------------------------------------------------- #
# C · 口径锁（面板 <-> 注册面 / 源码调用）
# --------------------------------------------------------------------------- #
def test_the_field_list_is_exactly_the_six_we_asked_for() -> None:
    """面板要的字段清单**逐字**钉住（改名字必须同时改这里 + properties + 报告）。"""
    assert PANEL.ANIM_SETTING_FIELDS == (
        "anim_bundle", "anim_path_table", "anim_clip",
        "anim_allow_partial", "anim_report_json", "anim_summary",
    )


def test_operator_names_match_what_the_panel_actually_calls() -> None:
    """面板真正调用的 idname 清单，必须与 `ANIM_OPERATORS` **集合相等**（双向钉住）。

    ⚠ 2026-10-12（pkgline · task-6）面板加了「写回」三颗 ⇒ 原断言写死「那 4 个」必然过期。
    改成**集合相等**（比原来更强，⛔ 不是放松）：既防「调了没登记的算子」（真机点一下报红），
    也防「登记了却没人调的孤儿」。
    """
    import re
    from pathlib import Path

    src = Path(PANEL.__file__).read_text(encoding="utf-8")
    called = set(re.findall(r'row\.operator\("(bamod\.[a-z_]+)"', src))
    assert called == set(PANEL.ANIM_OPERATORS), (
        sorted(called), sorted(PANEL.ANIM_OPERATORS))


def test_negative_control_panel_bl_idname_and_label() -> None:
    """标识锁：bl_idname / bl_label / 分类 / 默认折叠 —— 侧栏编号那条尺子会量它。"""
    cls = PANEL.BamodAnimPanel
    assert cls.bl_idname == "BAMOD_PT_anim"
    assert cls.bl_label == "⑨ 改 · 动画"
    assert cls.bl_category == "BA Mod"
    assert cls.bl_options == {"DEFAULT_CLOSED"}
    assert cls.bl_space_type == "VIEW_3D" and cls.bl_region_type == "UI"


def test_negative_control_panel_only_calls_the_declared_operators() -> None:
    """★ 负控：面板源码里出现的 `row.operator("bamod.*")` **必须**都在 `ANIM_OPERATORS` 里。

    这条防的是"面板偷偷调了一个没人注册的算子"—— 真机上就是点一下报红。
    """
    import re

    text = (_ADDON / "panels_anim.py").read_text(encoding="utf-8")
    called = set(re.findall(r'row\.operator\("(bamod\.[a-z_]+)"', text))
    assert called, "面板里一个算子都没调？那它就是个只读面板"
    assert called <= set(PANEL.ANIM_OPERATORS), sorted(called - set(PANEL.ANIM_OPERATORS))
