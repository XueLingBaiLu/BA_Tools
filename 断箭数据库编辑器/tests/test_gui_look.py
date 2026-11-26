r"""v0.16.0 · GUI **画法**的机械判据（U1–U9）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_gui_look.py -n 0 -q
      -> 改前（0.15.0 那棵树）**至少 6 类红**；改完（v0.16.0）全绿。

## 尺子只有一份

_work/panelsplit/draw_all.py —— J1–J20 用的同一把，本文件**不复制**它，只读它的 JSON。
⚠ 这一版**按需扩展了它三项**（⛔ 不是新造一把尺子）：

  · header / header_error —— draw_header() 画了什么。折叠态外面那一行就是它，
    而 Blender 把它的内容画在**面板名左边**；旧尺子只看 draw() ⇒ 抬头是
    「尺子看不见的东西」，难看了一整版没人发现。
  · ["set", 名, 值] —— layout 的属性赋值（use_property_split / use_property_decorate）。
    对齐口径同样是画法的一部分，不记 = 没有判据。
  · ["row"] … ["/row"] —— row 的**边界**。原来容器与子控件被拍平在一条序列里，
    分不出哪些控件属于哪个 row ⇒ 「空 row」「一排三颗按钮」同样是看不见的。

## 为什么每一条都带正控

AGENTS §0.12.1：「先造一把能红的尺子」。下面 _UglyPanel 是一个**已知难看**的假面板，
test_u0_* 要求 U1–U8 的**每一条**都在它身上报出来；_CleanPanel 是负控。

## 口径出处

docs/设计-Blender插件GUI视觉规范.md §1（真机读数）与 §2（L1–L8 八条规则）。
"""
from __future__ import annotations

import functools
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import types
import unicodedata
from pathlib import Path

import pytest

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"
RULER = WORK / "panelsplit" / "draw_all.py"
#: Blender 的图标枚举（1033 项，本机 Blender 5.2.1 LTS 实测；重跑命令见视觉规范 §5）。
ICONS_JSON = WORK / "gui_review" / "blender_api.json"


# --------------------------------------------------------------------------- #
# 尺子（只读 import，⛔ 不复制）
# --------------------------------------------------------------------------- #
def _ruler():
    spec = importlib.util.spec_from_file_location("gui_look_ruler", RULER)
    assert spec is not None and spec.loader is not None, f"找不到尺子 {RULER}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=2)
def panels_json(flavour: str = "default") -> dict:
    """真画一遍 13 个面板，读回 JSON。flavour="empty" = 空态现场（C-12 那面旗标）。"""
    out = Path(tempfile.mkdtemp(prefix=f"gui_look_{flavour}_")) / "panels.json"
    cmd = [sys.executable, str(RULER), str(out)]
    if flavour == "empty":
        cmd += ["--settings", "empty"]
    done = subprocess.run(  # 自己仓里的常驻探针，参数全是常量（没有外部输入）
        cmd, cwd=str(REPO_ROOT), capture_output=True, timeout=300, check=False
    )
    assert done.returncode == 0, f"尺子跑不动：{done.stdout}\n{done.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def icons() -> frozenset:
    assert ICONS_JSON.is_file(), (
        f"缺图标表 {ICONS_JSON} —— 重跑：blender --background --factory-startup "
        f"--python _work/gui_review/probe_api.py"
    )
    return frozenset(json.loads(ICONS_JSON.read_text(encoding="utf-8"))["icons"])


#: 视觉规范（**两个上限的权威那一份**）。
#: ★ 2026-10-15（五格归并）：`panels_common.py` 里的 `HEADER_WIDTH` / `TEXT_WIDTH_MAX` /
#: `display_width` **全没了**（实查 `display_width|HEADER_WIDTH|TEXT_WIDTH_MAX|WIDTH`
#: 在 blender_addon/ 里 0 命中）⇒ 产品那份**回声**没了，回到**权威**那一份。
#: ⚠ 这不叫放松：判据还是那个数、还是量**画出来的东西**，只是不再指着一段已经不存在的源码。
SPEC = REPO_ROOT / "docs" / "设计-Blender插件GUI视觉规范.md"
_SPEC_LIMITS: tuple[tuple[str, str], ...] = (
    # L4：一行可见文本 <= 40 显示格（规范里那句「40 是**量出来的**」）
    ("TEXT_WIDTH_MAX", r"\*\*L4\*\*[^\n]*?≤\s*(\d+)\s*显示格"),
    # 抬头新口径：短状态 <= 18 显示格
    ("HEADER_WIDTH", r"抬头是\*\*短状态\*\*（≤\s*(\d+)\s*显示格"),
)


@functools.lru_cache(maxsize=1)
def constants() -> dict:
    """两个宽度上限 —— 从**视觉规范**里读（⛔ 不 import bpy、⛔ 不抄进用例里写死）。"""
    text = SPEC.read_text(encoding="utf-8")
    found: dict = {}
    for name, pattern in _SPEC_LIMITS:
        match = re.search(pattern, text)
        assert match is not None, (
            f"{SPEC.name} 里读不到 {name} 的口径（锚点那句改了？）—— 上限不许猜"
        )
        found[name] = int(match.group(1))
    return found


def display_width(text: object) -> int:
    """显示格数（CJK / 全角 = 2）。口径与 panels_common.display_width 相同（有交叉用例）。"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text or "")
    )


# --------------------------------------------------------------------------- #
# 判据本体（纯函数：seq -> 问题清单 —— 正控与产品树走的是**同一段代码**）
# --------------------------------------------------------------------------- #
def _is_control(item: list) -> bool:
    return bool(item) and item[0] in ("label", "prop", "operator", "template_list")


def _text_of(item: list) -> str:
    if item[0] in ("label",):
        return str(item[1])
    if item[0] in ("prop", "operator"):
        return str(item[2])
    return ""


def _icon_of(item: list) -> str:
    if item[0] == "label":
        return str(item[2]) if len(item) > 2 else ""
    if item[0] in ("prop", "operator"):
        return str(item[3]) if len(item) > 3 else ""
    return ""


#: 面板名后面跟这些字符 = 「标题 + 后缀」（例：`③ 选 · 单位（选一个再导入）`）。
#: 面板名出现在行**中间** = 正文里提到它，**不是**重复抬头（例：真报告行 `[③ 涂装导出] …`）。
_TITLE_SUFFIX = "（(：: 　 \t"


def u1_repeats_title(seq: list, bl_label: str) -> list:
    """U1：面板体里**不许再写一遍面板名**（真机 V2，8/13 块中招）。

    ## ★ 2026-10-11 晚 · 落点收紧（A 组在真报告上量到假红）

    **旧落点**：`bl_label in 文本`。**为什么错**：`BamodPaintPanel` 的面板名就是「涂装」两个字，
    而真跑时报告行是 `[③ 涂装导出] 512x512 DXT5 …` ⇒ 用 `in` 会把它误判成重复抬头
    （探针的合成数据里没有这句，所以一直是绿的 —— 典型的「尺子看不见就不报」）。
    **新落点**：`文本 == 面板名` **或** `文本以面板名开头且紧跟一个分隔符`。
    负控见 `_PanelMentioningItsTitleInProse`。
    """
    if not bl_label:
        return []
    out: list[str] = []
    for item in seq:
        if item[0] != "label":
            continue
        text = _text_of(item)
        if text == bl_label or (
            text.startswith(bl_label) and text[len(bl_label) :][:1] in _TITLE_SUFFIX
        ):
            out.append(text)
    return out


def u2_header_problems(header: list, limit: int) -> list:
    """U2：折叠抬头 = **短状态**，不是一句说明书（真机 V3，24–25 字符）。"""
    problems: list = []
    for item in header:
        if item[0] != "label":
            problems.append(f"抬头里出现了 {item[0]}（只许一行 label）")
            continue
        text = str(item[1])
        if display_width(text) > limit:
            problems.append(f"抬头 {text!r} 宽 {display_width(text)} > {limit}")
        for bad in ("（", "「", "还没做", "先点"):
            if bad in text:
                problems.append(f"抬头 {text!r} 里有 {bad!r}（那是说明书，不是状态位）")
    return problems


def u3_box_problems(seq: list) -> list:
    """U3（★ 2026-10-15 **口径重读**，Lead 拍板）：面板体不许以 box 开头 ＋ **每个 box 自带抬头**。

    **为什么改**：旧口径那半「全面板 box 数 <= 1」是**13 小格时代**的形状 —— 现在一格装了
    七个折叠块，而 `panels_common.block()` 展开时**就是** `layout.box()` ⇒ 那条结构上不可能满足
    （实测：edit 10 个 / install 6 / verify 5 / ledger 3 / take 3）。
    换成「box() 之后、放控件之前至少一条 `label()`」—— 那才是「别给我一堆没名字的框」的本意。
    留在原地的半条：**面板体第一条不许是 box**（先说状态／读数，再给框）。
    """
    problems: list = []
    if seq and seq[0][0] == "box":
        problems.append("面板体第一条就是 box（整块面板白加一层边框与内缩）")
    for index, item in enumerate(seq):
        if item[0] != "box":
            continue
        first = next((later for later in seq[index + 1 :] if _is_control(later)), None)
        if first is None or first[0] != "label":
            kind = "后面什么都没画" if first is None else f"第一条控件是 {first[0]}"
            problems.append(f"#{index} 这个 box 没抬头（{kind}）")
    return problems


def u4_overwide(seq: list, limit: int) -> list:
    """U4：**一行可见文本 <= limit 显示格**。Blender 的 label 只截断不换行（真机 V4）。"""
    out: list = []
    for index, item in enumerate(seq):
        if item[0] not in ("label", "prop", "operator"):
            continue
        text = _text_of(item)
        if text and display_width(text) > limit:
            out.append((index, text, display_width(text)))
    return out


def u5_row_problems(seq: list, *, max_controls: int = 2) -> list:
    """U5（★ 2026-10-15 **口径重读**，Lead 拍板）：一行控件 **> 2 就报**；**= 0 也报**；= 1 不报。

    **为什么改**：旧口径把「row 里只有 1 颗控件」也叫「空包装」—— 而 `row(align=True)` 里包**一颗**
    `prop` / `operator` 在 Blender 里是**惯用法**（控宽度、对齐按钮），实测五格里 24 处全是它，
    不是缺陷。旧口径真正要抓的是 `row()` 套一层**什么也没干**。
    留下的两条：**> 2 颗控件**（窄侧栏约 300 px 会把按钮文字截成「…」）与 **= 0**（空行 = 漏画）。

    边界标记来自尺子的 _Layout：["row"] … ["/row"]（没闭合 = 一直画到序列末尾）。
    """
    problems: list = []
    index = 0
    while index < len(seq):
        if seq[index][0] != "row":
            index += 1
            continue
        scan = index + 1
        while scan < len(seq) and seq[scan][0] != "/row":
            scan += 1
        inner = seq[index + 1 : scan]
        controls = [item for item in inner if _is_control(item)]
        if not controls:
            problems.append(f"#{index} row 里一颗控件都没有（空行 = 漏画）")
        elif len(controls) > max_controls:
            problems.append(f"#{index} row 里挤了 {len(controls)} 颗控件（窄侧栏会被截断）")
        index = scan + 1
    return problems


def u6_bad_icons(seq: list, known: frozenset) -> list:
    """U6：图标名必须在 Blender 的枚举里（写错了真机上画不出来，而且很难一眼看出）。"""
    return [
        f"{item[0]}#{index} 的图标 {_icon_of(item)!r}"
        for index, item in enumerate(seq)
        if _icon_of(item) and _icon_of(item) != "NONE" and _icon_of(item) not in known
    ]


def u7_operators_without_icon(seq: list) -> list:
    """U7：每颗算子都要有图标（图标是**控件自己的属性**，⛔ 不另起一颗图标标签）。"""
    return [item[1] for item in seq if item[0] == "operator" and _icon_of(item) in ("", "NONE")]


def u8_alignment_missing(seq: list) -> bool:
    """U8：面板体必须设过对齐口径（真机 V6，13/13 块没设）。"""
    return ["set", "use_property_split", "True"] not in seq


# --------------------------------------------------------------------------- #
# 正控 / 负控：喂已知难看的假面板，上面每一条都必须报；干净面板一条都不许报
# --------------------------------------------------------------------------- #
class _UglyPanel:
    """⛔ **已知难看**：整块套 box ＋ 面板名再写一遍 ＋ 一句 120 格长句 ＋
    单颗按钮套 row ＋ 一排挤三颗按钮 ＋ 没设对齐口径 ＋ 一颗假图标 ＋ 一颗没图标的算子。"""

    bl_idname = "BAMOD_PT_fake_ugly"
    bl_label = "① 起步 · 环境"
    bl_space_type = "VIEW_3D"

    def draw(self, context: object) -> None:
        box = self.layout.box()  # type: ignore[attr-defined]
        box.label(text="① 起步 · 环境", icon="FILE_FOLDER")
        box.label(text="并入的增量（研究件，只作词汇表）：" + "x" * 100)
        lone = box.row(align=True)
        lone.operator("bamod.detect_game")
        crowded = box.row(align=True)
        crowded.operator("bamod.detect_game", icon="INFO")
        crowded.operator("bamod.scan_units", icon="INFO")
        crowded.operator("bamod.import_unit", icon="INFO")
        # ★ 2026-10-15 口径重读：再放一条**空 row**（u5 新口径的第二条：= 0 也要报）
        box.row(align=True)
        box.label(text="检测环境", icon="NOT_A_REAL_ICON")
        # ★ 口径重读：再放一个**没抬头**的 box（u3 新口径的第二条）
        bare = self.layout.box()  # type: ignore[attr-defined]
        bare.operator("bamod.detect_game", icon="INFO")

    def draw_header(self, context: object) -> None:
        self.layout.label(text="（还没做：先点「检测游戏路径」）")  # type: ignore[attr-defined]


class _CleanPanel:
    """负控：v0.16.0 的形状（begin ＋ heading ＋ 一对按钮 ＋ readings ＋ 一颗图标）。"""

    bl_idname = "BAMOD_PT_fake_clean2"
    bl_label = "① 起步 · 环境"
    bl_space_type = "VIEW_3D"

    def draw(self, context: object) -> None:
        layout = self.layout  # type: ignore[attr-defined]
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="游戏根目录", icon="FILE_FOLDER")
        pair = layout.row(align=True)
        pair.operator("bamod.detect_game", text="检测游戏路径", icon="ZOOM_SELECTED")
        pair.operator("bamod.check_environment", text="检测环境", icon="SETTINGS")
        box = layout.box()
        box.label(text="就绪", icon="CHECKMARK")

    def draw_header(self, context: object) -> None:
        self.layout.label(text="未检测")  # type: ignore[attr-defined]


def _draw(panel_class: type) -> tuple:
    ruler = _ruler()
    body, header = ruler._Layout(), ruler._Layout()
    panel = panel_class.__new__(panel_class)
    ctx = types.SimpleNamespace(scene=types.SimpleNamespace(bamod=ruler._Settings()))
    panel.layout = body
    panel.draw(ctx)
    # ⛔ 子面板不一定有 draw_header（例：⑤›原始读数）—— 那就画不出东西，不是错。
    draw_header = getattr(panel_class, "draw_header", None)
    if draw_header is not None:
        panel.layout = header
        draw_header(panel, ctx)
    return body.seq, header.seq


def test_u0_the_ruler_sees_a_known_ugly_panel() -> None:
    """★ 正控：_UglyPanel 上，U1–U8 **每一条**都必须报出来（少一条 = 那条是假绿）。"""
    limit_h, limit_t = constants()["HEADER_WIDTH"], constants()["TEXT_WIDTH_MAX"]
    body, header = _draw(_UglyPanel)
    assert u1_repeats_title(body, _UglyPanel.bl_label), body
    assert u2_header_problems(header, limit_h), header
    assert u3_box_problems(body), body
    assert u4_overwide(body, limit_t), body
    assert u5_row_problems(body), body
    assert u6_bad_icons(body, icons()), body
    assert u7_operators_without_icon(body), body
    assert u8_alignment_missing(body), body


def test_u0_the_ruler_is_quiet_on_a_clean_panel() -> None:
    """★ 负控：v0.16.0 的形状 ⇒ U1–U8 一条都不许报（⛔ 尺子不是恒真）。"""
    limit_h, limit_t = constants()["HEADER_WIDTH"], constants()["TEXT_WIDTH_MAX"]
    body, header = _draw(_CleanPanel)
    assert u1_repeats_title(body, _CleanPanel.bl_label) == [], body
    assert u2_header_problems(header, limit_h) == [], header
    assert u3_box_problems(body) == [], body
    assert u4_overwide(body, limit_t) == [], body
    assert u5_row_problems(body) == [], body
    assert u6_bad_icons(body, icons()) == [], body
    assert u7_operators_without_icon(body) == [], body
    assert u8_alignment_missing(body) is False, body


def test_u3_ruler_reports_a_box_without_a_heading() -> None:
    """★ 负控（口径重读）：`box()` 之后直接放控件 ⇒ 必须报「这个 box 没抬头」。"""
    planted = [["row"], ["/row"], ["box"], ["operator", "bamod.detect_game", "", "INFO"]]
    assert u3_box_problems(planted) == ["#2 这个 box 没抬头（第一条控件是 operator）"], (
        u3_box_problems(planted)
    )
    # 面板体第一条就是 box：两条一起报（它同时也没抬头）
    both = [["box"], ["operator", "bamod.detect_game", "", "INFO"]]
    assert u3_box_problems(both) == [
        "面板体第一条就是 box（整块面板白加一层边框与内缩）",
        "#0 这个 box 没抬头（第一条控件是 operator）",
    ], u3_box_problems(both)
    # 另一半（留在原地的旧口径）：面板体第一条就是 box 也要报；有抬头的 box 只报那一条
    head_first = [["box"], ["label", "标题", ""]]
    assert u3_box_problems(head_first) == ["面板体第一条就是 box（整块面板白加一层边框与内缩）"]
    # 正控：抬头在位 ⇒ 一条都不报（⛔ 尺子不是恒真）
    assert u3_box_problems([["row"], ["label", "标题", ""], ["/row"]]) == []
    # 空的 box（后面什么都没画）也要报
    assert u3_box_problems([["row"], ["/row"], ["box"]]) == ["#2 这个 box 没抬头（后面什么都没画）"]


def test_u5_ruler_reports_an_empty_row_and_a_crowded_row() -> None:
    """★ 负控（口径重读）：空 row 要报、> 2 颗控件要报、**1 颗不报**。"""
    assert u5_row_problems([["row"], ["/row"]]) == ["#0 row 里一颗控件都没有（空行 = 漏画）"]
    crowded = [
        ["row"],
        ["operator", "a", "", "INFO"],
        ["operator", "b", "", "INFO"],
        ["operator", "c", "", "INFO"],
        ["/row"],
    ]
    assert u5_row_problems(crowded) == ["#0 row 里挤了 3 颗控件（窄侧栏会被截断）"]
    # ★ 重读的正控：一颗 / 两颗**都不许**报（旧口径会把一颗那颗报成「空包装」）
    assert u5_row_problems([["row"], ["operator", "a", "", "INFO"], ["/row"]]) == []
    assert u5_row_problems([
        ["row"], ["operator", "a", "", "INFO"], ["operator", "b", "", "INFO"], ["/row"]
    ]) == []


class _PanelMentioningItsTitleInProse:
    """负控（落点收紧用）：面板名出现在**正文中间** —— 真报告行 `[③ 涂装导出] …`。"""

    bl_idname = "BAMOD_PT_fake_prose"
    bl_label = "涂装"
    bl_space_type = "VIEW_3D"

    def draw(self, context: object) -> None:
        layout = self.layout  # type: ignore[attr-defined]
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="[③ 涂装导出] 512x512 DXT5 · 349552 B", icon="INFO")
        layout.operator("bamod.paint_export", text="导出贴图", icon="EXPORT")


def test_u0_u1_does_not_flag_the_title_mentioned_in_prose() -> None:
    """★ 负控：正文里**提到**面板名 ≠ 重复抬头（用 `in` 会假红，用「== 或 名字+分隔符」才准）。"""
    body, _header = _draw(_PanelMentioningItsTitleInProse)
    assert u1_repeats_title(body, _PanelMentioningItsTitleInProse.bl_label) == [], body
    # 正控：同样两个字，**当抬头写**就必须报
    assert u1_repeats_title([["label", "涂装", ""]], "涂装") == ["涂装"]
    assert u1_repeats_title([["label", "涂装：512² DXT5", ""]], "涂装") == ["涂装：512² DXT5"]


def test_u0_the_icon_ruler_can_go_red() -> None:
    """★ 图标表自带正控：真表里有 CHECKMARK、没有 NOT_A_REAL_ICON。"""
    assert "CHECKMARK" in icons()
    assert "NOT_A_REAL_ICON" not in icons()
    assert len(icons()) > 500, len(icons())


# --------------------------------------------------------------------------- #
# U1–U8：产品树（两份：默认 ＋ 空态）
# --------------------------------------------------------------------------- #
def _each_panel():
    for flavour in ("default", "empty"):
        for item in panels_json(flavour)["panels"]:
            yield flavour, item


def test_u1_no_panel_repeats_its_own_name_in_the_body() -> None:
    """★ U1：面板体里不许再写一遍面板名（真机 V2：8/13 块中招）。"""
    problems = []
    for flavour, item in _each_panel():
        for title in u1_repeats_title(item["seq"], item["bl_label"]):
            problems.append(f"{flavour}/{item['class']}: {title}")
    assert problems == [], problems


def _retired(why: str):
    """题目没了：**如实 skip ＋ 点名为什么**，⛔ 不硬凑一条假绿（task-9/10 口径）。"""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            pytest.skip(why)
        return inner

    return wrap


def header_missing_problems(header: list, header_error: str) -> list[str]:
    """判据本体（纯函数）：**每一格都要有 `draw_header`，且它至少画出一条控件**。

    ★ 2026-10-15 晚：用户批准补回折叠态抬头（Lead 给五格各加了 `draw_header`）⇒
    这条从**具名 skip 改回能红的规则**。折叠态外面那一行就是 `draw_header` 画的东西 ——
    它空着时用户只看得到面板名（`bl_label`），状态一个字都没有。
    """
    problems: list[str] = []
    if header_error:
        problems.append(f"draw_header 抛了：{header_error}")
    controls = [
        item
        for item in header
        if item and item[0] in ("label", "prop", "operator", "template_list")
    ]
    if not controls:
        problems.append("draw_header 一条控件都没画（折叠态那行就是它 —— 空 = 用户只看得到面板名）")
    return problems


def test_u2_every_panel_has_a_header_that_draws_something() -> None:
    """★ U2 前置（与 c07 同源）：五格**各自**必须有 `draw_header`，且至少画出一条控件。"""
    problems = [
        f"{flavour}/{item['class']}: {text}"
        for flavour, item in _each_panel()
        for text in header_missing_problems(item["header"], item["header_error"])
    ]
    assert problems == [], problems


def test_u2_ruler_can_go_red_on_a_missing_or_broken_header() -> None:
    """★ 负控（能红）：抬头空着 / 只画容器不画控件 / `draw_header` 抛了 ⇒ 一条都不许漏。"""
    assert header_missing_problems([], "") == [
        "draw_header 一条控件都没画（折叠态那行就是它 —— 空 = 用户只看得到面板名）"
    ]
    assert header_missing_problems([["box"], ["row"]], "") != []
    assert header_missing_problems([["label", "只读", ""]], "") == []
    assert header_missing_problems([["label", "只读", ""]], "AttributeError: boom") == [
        "draw_header 抛了：AttributeError: boom"
    ]


def test_u2_collapsed_headers_are_short_status_not_sentences() -> None:
    """★ U2：折叠抬头 <= HEADER_WIDTH 显示格，且不含括号 / 「 / 还没做（真机 V3）。"""
    limit = constants()["HEADER_WIDTH"]
    problems = []
    for flavour, item in _each_panel():
        assert not item["header_error"], (item["class"], item["header_error"])
        for text in u2_header_problems(item["header"], limit):
            problems.append(f"{flavour}/{item['class']}: {text}")
    assert problems == [], problems


def test_u3_no_panel_body_is_wrapped_in_a_box() -> None:
    """★ U3：面板体不以 box 开头，全面板 box <= 1（真机 V1：13/13 块中招）。"""
    problems = [
        f"{flavour}/{item['class']}: {text}"
        for flavour, item in _each_panel()
        for text in u3_box_problems(item["seq"])
    ]
    assert problems == [], problems


def test_u4_no_visible_line_is_wider_than_the_limit() -> None:
    """★ U4：一行可见文本 <= TEXT_WIDTH_MAX 显示格（真机 V4：最长 120 格）。"""
    limit = constants()["TEXT_WIDTH_MAX"]
    problems = [
        f"{flavour}/{item['class']}#{index}: {width} 格 {text[:60]!r}"
        for flavour, item in _each_panel()
        for index, text, width in u4_overwide(item["seq"], limit)
    ]
    assert problems == [], problems


def test_u5_no_lone_or_crowded_button_rows() -> None:
    """★ U5：row 里不许只有一颗控件；也不许挤三颗以上按钮（真机 V5）。"""
    problems = [
        f"{flavour}/{item['class']}: {text}"
        for flavour, item in _each_panel()
        for text in u5_row_problems(item["seq"])
    ]
    assert problems == [], problems


def test_u6_every_icon_name_exists_in_blender() -> None:
    """★ U6：所有图标名都在 Blender 5.2.1 的枚举里。"""
    known = icons()
    problems = [
        f"{flavour}/{item['class']}: {text}"
        for flavour, item in _each_panel()
        for text in u6_bad_icons(item["seq"], known)
    ]
    assert problems == [], problems


def test_u7_every_operator_carries_its_own_icon() -> None:
    """★ U7：每颗算子都要有图标。"""
    problems = [
        f"{flavour}/{item['class']}: {idname}"
        for flavour, item in _each_panel()
        for idname in u7_operators_without_icon(item["seq"])
    ]
    assert problems == [], problems


def test_u8_every_panel_body_sets_the_alignment() -> None:
    """★ U8：面板体必须设过 use_property_split（真机 V6：13/13 块没设）。"""
    problems = [
        f"{flavour}/{item['class']}"
        for flavour, item in _each_panel()
        if u8_alignment_missing(item["seq"])
    ]
    assert problems == [], problems


# --------------------------------------------------------------------------- #
# U9：折行尺子（纯函数，与 panels_common.wrap_text **同一口径**）
# --------------------------------------------------------------------------- #
def test_u9_every_panel_exposes_a_header_and_alignment_slot() -> None:
    """★ 前置：扩展后的尺子对每个面板都写了 header / header_error 两个键。"""
    for item in panels_json()["panels"]:
        assert "header" in item and "header_error" in item, item["class"]
