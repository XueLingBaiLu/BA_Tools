#!/usr/bin/env python
"""tools/gen_capabilities.py —— **产品能力清单**的生成器（唯一活来源）。

═══════════════ 它治什么 ═══════════════

用户原话（2026-10-11）：
「**我产品的所有功能，其实应该也算是一种知识，建议补进知识库**」。

现场：那天的会话**不知道产品有 Blender 第二条线**
（`blender_addon/`：9 个面板 ＋ 19 个算子），
因为盘面上**没有一处列着"产品到底能做什么"** —— 只能 grep，
而 grep 靠的是"你先知道要搜什么"。

⇒ 这份东西的唯一价值：**下一个会话不必靠 grep 猜**。三条硬要求：

1. **每条都带出处** `文件:行号`（下次能验证，也会随代码漂）；
2. **一条都不是手写的** —— 三个面全从代码里抽；
3. **它会烂，烂了要报** —— `--check` 拿真代码重抽一遍跟盘上那份对，对不上 rc=1。

═══════════════ 三个面怎么抽 ═══════════════

* **A · CLI** —— 活 parser 树：`cli.__main__._build_parser()` 起，
  递归原语**直接复用** `tests/test_no_swap_wording.py::_children`
  （AGENTS §5「逻辑只有一份」，⛔ 不在这里抄第二份）。
  出处 = `ast` 扫 `src/ba_mod_maker/cli/*.py` 的
  `add_parser("名字", help=…)` 站点，按 **(名字, 短帮助)** 配对。
* **B · Blender** —— `ast` 扫 `blender_addon/*.py`。
  面板 = 有 `bl_space_type` 的类；
  算子 = 有 `bl_idname` 而**没有** `bl_space_type`、且**不是 UIList**（见坑 3）的类。
  另与 `__init__._CLASSES`（注册表）对账。
* **C · GUI** —— `ast` 扫 `app/screens/**` 里名字以
  `Page`/`Screen` 结尾的类；外面那层注册看 `main_window.py`
  （`NAV_PAGES` 与 `self._tabs.addWidget(...)`，
  同行注释用 `tokenize` 取）。

═══════════════ 两处真踩过的坑（别再走） ═══════════════

**坑 1 · 面板也有 `bl_idname`。**
第一版按 `bl_idname` 判"这是算子"，量出来是**「面板 0 个、算子 28 个」**。
可 Blender 的 `Panel` 也写 `bl_idname`（本仓是 `BAMOD_PT_main` 那一批）。
**判据必须是 `bl_space_type`**（只有 Panel 有）。
改对之后：**面板 9 · 算子 19 = 28**，与 `blender_addon/__init__.py::_CLASSES`
（注册表，逐个数）**完全一致**。

**坑 3 · UIList 也写 `bl_idname`（2026-10-12，task-1 收尾）。**
`panels_common.BamodUnitList`（`bl_idname = "BAMOD_UL_units"`）既没有 `bl_space_type`、
又真写 `bl_idname` ⇒ 被坑 1 那条判据当成**算子**数了进去：生成器数出 **26**，
而 Blender 真注册 `dir(bpy.ops.bamod)` 只有 **25** —— **尺子自相矛盾**。
**修法**：同一条口径再排一类 —— UIList 的标记是它必须实现 `draw_item`
（Panel 写 `draw`、算子写 `execute`，两者都不写 `draw_item`）。
判据读数：生成器 26 -> **25**，与真注册**逐个相等**。

**坑 2 · CLI 出处不能"只看名字"。**
`add_parser("set")` 出现**两次**：顶层 `set`（`cli/__main__.py:346`）
与 `object set`（`cli/object_edit.py:564`）。
只看名字会拿到两个候选、不知道是哪一个；加上**短帮助**（`help=` 那个字符串常量）
之后 **27/27 唯一命中** —— 短帮助是这一层的天然标识（argparse 自己也用它渲染帮助列表）。
配不唯一时本工具**如实印出候选**，⛔ 不挑一个装作确定。

═══════════════ 怎么跑 ═══════════════

从**仓根** `BA_Mod_Maker_2.0` 跑，用**仓内 venv**（A 面要 import 产品代码）：

    .venv/Scripts/python.exe tools/gen_capabilities.py          # 重新生成
    .venv/Scripts/python.exe tools/gen_capabilities.py --check  # 只对账，不写盘
    .venv/Scripts/python.exe tools/gen_capabilities.py --selftest
    .venv/Scripts/python.exe tools/gen_capabilities.py --quiet --check

rc 口径（**逐字写进 `--help`**）：`0` 一致 / `1` 不一致（**能红**）/
`2` 用不了（import 不了产品 / 生成物读不出来）。

⛔ 本工具**只写** `docs/产品功能清单.md` 那一份；`src/` 只读（import ＋ 读文本）。

⚠ 接手体检 `tools/handoff_check.py` 与这里**分两档**（那一份要用**系统 python** 跑，
⛔ 不能 import 产品）：`--brief` 只查 `docs/产品功能清单.md` **在不在**（纯路径）；
真正的 `--check` 放在 `--measure` 那一档。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import re
import sys
import tempfile
import tokenize
import warnings
from dataclasses import dataclass
from pathlib import Path

# ───────────────────────── 落点与常量 ─────────────────────────

#: 仓根 = 本文件所在 `tools/` 的上一级。
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
ADDON = ROOT / "blender_addon"
SCREENS = SRC / "ba_mod_maker" / "app" / "screens"
MAIN_WINDOW = SRC / "ba_mod_maker" / "app" / "shell" / "main_window.py"
CLI_DIR = SRC / "ba_mod_maker" / "cli"
DOC = ROOT / "docs" / "产品功能清单.md"

#: 生成物**头一行**（逐字，Lead 钉死的）。⛔ 改它会当场报红。
REGEN_LINE = r"> ⛔ 生成物，别手改。重生成：python tools\gen_capabilities.py"

#: `--check` 的返回码（与 `--help` 里那三行同一套）。
RC_SAME = 0
RC_DIFF = 1
RC_UNUSABLE = 2

#: 计数自证那一行的四个标签。**数字从抽取结果算**，⛔ 不写死。
COUNT_LABELS = ("CLI 子命令", "Blender 面板", "算子", "GUI 页面")

#: 表格上方的机读锚点（人看不见，解析靠它认这是哪一节）。
MARKER = "<!--cap:%s-->"

#: 五个小节：(id, 标题, 表头, 引言)。顺序即文档顺序。
SECTIONS = (
    (
        "cli",
        "A · CLI 子命令",
        ("命令", "说明", "参数", "出处"),
        "活 parser 树（`ba-mod-maker --help` 能敲到的全部）。递归取法复用 "
        "`tests/test_no_swap_wording.py::_children` —— ⛔ 没有第二份。",
    ),
    (
        "panel",
        "B1 · Blender 面板",
        ("bl_idname", "名字", "出处"),
        "有 `bl_space_type` 的类（Blender 按它挂栏）。"
        "⛔ 判据不是 `bl_idname` —— 面板也有它。",
    ),
    (
        "operator",
        "B2 · Blender 算子",
        ("bl_idname", "名字", "说明", "出处"),
        "有 `bl_idname` 而**没有** `bl_space_type`、且**不是 UIList** 的类"
        "（UIList 也写 `bl_idname`；标记是它必须实现 `draw_item` —— 见模块头坑 3）。",
    ),
    (
        "page",
        "C1 · GUI 页面类",
        ("类", "基类", "出处"),
        "`app/screens/**` 里名字以 `Page` / `Screen` 结尾的类。",
    ),
    (
        "mount",
        "C2 · 外壳注册",
        ("注册", "说明", "出处"),
        "`app/shell/main_window.py`：`NAV_PAGES` 的导航页与 "
        "`self._tabs.addWidget(...)` 的页签。",
    ),
)

#: 解析时要知道每一节的表头（跳过表头行用）。
_HEADERS = {sid: headers for sid, _title, headers, _note in SECTIONS}

INTRO = """本文件回答「**这个产品到底能做什么**」——
CLI / Blender 插件 / GUI 三个面**全机械抽**，每条带出处 `文件:行号`。
⛔ **一条都不是手写的**；`--check` 会拿真代码重抽一遍跟这份对，
对不上就报红（**新增 / 消失 / 出处漂移**三档）。"""


# ───────────────────────── 数据模型 ─────────────────────────

@dataclass(frozen=True, slots=True)
class Row:
    """清单里一行。`cells` 就是**最终写进 markdown 的文字**（含反引号）。

    这样"渲染"与"解析回来"是**同一个字符串**，`--check` 的比对才不用再脱一层装饰
    （⛔ 渲染时自动加反引号、解析时再猜怎么去掉，那是两套口径）。
    """

    section: str
    cells: tuple

    @property
    def key(self):
        """对账用的身份 = (哪一节, 第一格)。"""
        return (self.section, self.cells[0])


@dataclass(frozen=True, slots=True)
class Site:
    """`add_parser("名字", help=…)` 在源码里的落点。"""

    name: str
    help: object
    file: str
    line: int

    @property
    def text(self):
        return f"{self.file}:{self.line}"


def _parse_py(path: Path):
    """读一个 .py 并 ast 解析。

    ⚠ 用 catch_warnings 压掉 SyntaxWarning：产品里有一处**既有的**坏转义
    （`blender_addon/materials_probe.py:5`），它不是本工具的问题，
    但每扫一次就打印一次会把交接体检的输出弄脏。
    ⛔ 这里**不碰产品代码**，只让自己闭嘴。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"))


def _cell(text) -> str:
    """一格文字：竖线要转义（它同时是表格分隔符），其余原样。"""
    return str(text).replace("|", r"\|").strip()


def _code(text) -> str:
    """一格里那种"这是代码"的写法。"""
    return f"`{_cell(text)}`"


# ─────────────── 面 A · CLI（活树 ＋ ast 出处） ───────────────

def _load_product():
    """把 `src` 与 `tests` 挂上 sys.path，交回 (cli.__main__, 那把既有尺子)。

    ⛔ 返回的是**既有那一份**模块，不是副本 —— 递归原语 `_children` 只有一份。
    """
    for path in (str(TESTS), str(SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import test_no_swap_wording
    from ba_mod_maker.cli import __main__ as cli_main

    return cli_main, test_no_swap_wording


def _cli_sites():
    """`ast` 扫 `cli/*.py`：((名字, help) -> [Site], 名字 -> [Site]) 两个索引。"""
    exact = {}
    loose = {}
    for path in sorted(CLI_DIR.glob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(_parse_py(path)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "add_parser" or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            help_text = None
            for kw in node.keywords:
                if kw.arg != "help" or not isinstance(kw.value, ast.Constant):
                    continue
                if isinstance(kw.value.value, str):
                    help_text = kw.value.value
            site = Site(name=first.value, help=help_text, file=rel, line=node.lineno)
            exact.setdefault((site.name, site.help), []).append(site)
            loose.setdefault(site.name, []).append(site)
    return exact, loose


def _subparsers_action(parser):
    """argparse 把子命令挂在 `_SubParsersAction` 上 —— 只有私有容器这一条路。"""
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action
    return None


def _short_helps(parser) -> dict:
    """父 parser -> {id(子 parser): 短帮助}（短帮助存在伪 action 上）。"""
    out = {}
    action = _subparsers_action(parser)
    if action is None:
        return out
    for pseudo in action._choices_actions:  # noqa: SLF001
        child = action.choices.get(pseudo.dest)
        if child is not None:
            out[id(child)] = str(pseudo.help or "")
    return out


def _iter_subcommands(parser, children, depth=1):
    """yield (子 parser, 短帮助, 深度) —— 递归原语 `children` 由调用方给。"""
    helps = _short_helps(parser)
    for child in children(parser):
        yield child, helps.get(id(child), ""), depth
        yield from _iter_subcommands(child, children, depth + 1)


def _params_of(parser) -> str:
    """这个子命令能敲的参数（位置参数名 ＋ 选项名），去掉 `-h/--help`。"""
    names = []
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        if action.option_strings:
            names.extend(o for o in action.option_strings if o not in ("-h", "--help"))
        elif action.dest and action.dest != argparse.SUPPRESS:
            names.append(str(action.dest))
    return ", ".join(_code(n) for n in names) if names else "—"


def _origin_for(name, help_text, exact, loose) -> str:
    """这一条子命令的出处：先按 (名字, 短帮助) 精配，再退到只看名字。

    实测（2026-10-11）**27/27 精配唯一命中** —— 只看名字有两处歧义：
    `set` 同时是顶层 `__main__.py:346` 与 `object set` `object_edit.py:564`。
    配不唯一时**如实印出候选**，⛔ 不挑一个装作确定（那会让"出处漂移"这条判据失灵）。
    """
    hits = exact.get((name, help_text)) or []
    if len(hits) == 1:
        return hits[0].text
    if not hits:
        hits = loose.get(name) or []
    if len(hits) == 1:
        return hits[0].text
    if not hits:
        return "（没找到 add_parser 落点）"
    return "（出处不唯一：{}）".format(" 或 ".join(s.text for s in hits))


def cli_rows(parser=None):
    """A 面：CLI 子命令行。`parser` 给了就用它（`--selftest` 塞假子命令走这条路）。"""
    cli_main, swap_ruler = _load_product()
    root = parser if parser is not None else cli_main._build_parser()  # noqa: SLF001
    children = swap_ruler._children  # noqa: SLF001 -- 复用既有递归原语，⛔ 不抄第二份
    exact, loose = _cli_sites()
    rows = []
    for child, help_text, _depth in _iter_subcommands(root, children):
        path = child.prog.replace("ba-mod-maker ", "")
        name = path.split()[-1]
        rows.append(
            Row(
                section="cli",
                cells=(
                    _code(f"ba-mod-maker {path}"),
                    _cell(help_text or "—"),
                    _params_of(child),
                    _code(_origin_for(name, help_text, exact, loose)),
                ),
            )
        )
    return sorted(rows, key=lambda r: r.cells[0])


# ───────────────────────── 面 B · Blender（ast） ─────────────────────────

def _string_attrs(node) -> dict:
    """类体里那些 `名字 = "字符串"` 的赋值（只看**直接子语句**）。"""
    out = {}
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            out.setdefault(target.id, (stmt.value.value, stmt.lineno))
    return out


def _registered_addon_classes() -> set:
    """`blender_addon/__init__.py::_CLASSES` 里的类名 —— 注册表（真挂上去的那批）。"""
    init = ADDON / "__init__.py"
    if not init.is_file():
        # 2026-10-29：Blender 插件已整体删除（内核迁到 断箭模型提取器/_ba）。
        # B1/B2 两节为空是正常的。
        return set()
    names = set()
    for node in ast.walk(_parse_py(init)):
        # ⚠ _CLASSES 是**带注解的赋值**（_CLASSES: tuple[type, ...] = (...)）——
        #   只认 ast.Assign 会一条都收不到，于是 9 个面板全被标成"没注册"（第一版就是这样）。
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if not (isinstance(target, ast.Name) and target.id == "_CLASSES"):
            continue
        if node.value is None:
            continue
        for item in ast.walk(node.value):
            if isinstance(item, ast.Attribute):
                names.add(item.attr)
    return names


def _defines(node: ast.ClassDef, name: str) -> bool:
    """类体里有没有这个方法定义。

    ★ 坑 3（2026-10-12）就是靠它修的：UIList **必须**实现 draw_item ——
    而 Panel 写 draw、算子写 execute，两者都不写 draw_item ⇒ 这条判据能把
    panels_common.BamodUnitList（也写 bl_idname）从「算子」里排出去，
    让生成器的算子数与 Blender 真注册 dir(bpy.ops.bamod) **逐个相等**。
    """
    return any(
        isinstance(stmt, ast.FunctionDef) and stmt.name == name for stmt in node.body
    )


def _local_class_index() -> dict:
    """`类名 -> (类体里的字符串属性, 本文件系统里的基类名)` —— 扫 `blender_addon/*.py`。

    ★ 为什么需要它（2026-10-14 插件重做后当场撞上）：新插件的 `bl_space_type` /
    `bl_region_type` / `bl_category` 定义在**公共基类** `panels_common.BaPanel` 上，
    十个面板的类体里一个都不写 ⇒ 只看类体的话生成器数出「面板 1 个 · 算子 21 个」
    （真注册是 10 面板 ＋ 11 算子）。判据本身没错，缺的是**继承**这一跳。
    """
    index: dict = {}
    for path in sorted(ADDON.glob("*.py")):
        for node in ast.walk(_parse_py(path)):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = tuple(b.id for b in node.bases if isinstance(b, ast.Name))
            bases += tuple(b.attr for b in node.bases if isinstance(b, ast.Attribute))
            index[node.name] = (_string_attrs(node), bases)
    return index


def _resolved_attrs(node: ast.ClassDef, index: dict) -> dict:
    """自己的字符串属性 ∪ **本地基类**的属性（沿基类链向上合并，同名以自己为准）。"""
    own, bases = index.get(node.name, (_string_attrs(node), ()))
    out = dict(own)
    seen: set = set()
    stack = list(bases)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        hit = index.get(name)
        if hit is None:
            continue
        inherited, deeper = hit
        for key, value in inherited.items():
            out.setdefault(key, value)
        stack.extend(deeper)
    return out


def blender_rows():
    """B 面：面板 ＋ 算子两节（判据见模块头「坑 1」「坑 3」）。"""
    registered = _registered_addon_classes()
    local = _local_class_index()
    panels = []
    operators = []
    for path in sorted(ADDON.glob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(_parse_py(path)):
            if not isinstance(node, ast.ClassDef):
                continue
            attrs = _string_attrs(node)
            resolved = _resolved_attrs(node, local)
            where = _code(f"{rel}:{node.lineno}")
            # ⚠ 面板 = 解析后**链上**有 bl_space_type、且自己给了 bl_idname（或已在注册表里）。
            #   后半句是为了把抽象基类 panels_common.BaPanel 排除掉 —— 它没有 bl_idname、
            #   也不在 _CLASSES 里，不该冒充一格面板。
            if "bl_space_type" in resolved and ("bl_idname" in attrs or node.name in registered):
                ident, line = attrs.get("bl_idname", ("（没有 bl_idname）", node.lineno))
                label = attrs.get("bl_label", ("—", node.lineno))[0]
                mark = "" if node.name in registered else "（⚠ 没注册）"
                panels.append(
                    Row("panel", (_code(ident), _cell(f"{label}{mark}"), _code(f"{rel}:{line}")))
                )
            elif "bl_idname" in attrs and not _defines(node, "draw_item"):
                # ★ 坑 3：UIList 也写 bl_idname（本仓 BAMOD_UL_units）⇒ 按 draw_item 排出去。
                ident, line = attrs["bl_idname"]
                # ★ 坑 4（2026-10-15「五格归并」之后暴露）：面板的 idname 一律长 `VIEW3D_PT_*`。
                #   七个退役面板类（例 panels_09_anim.VIEW3D_PT_ba_anim）拿掉了 `bpy.types.Panel`
                #   基类 ⇒ `bl_space_type` 不在解析链上 ⇒ 会掉进这一支被当成"算子"，于是同一批
                #   名字**既报「面板消失 7」又报「算子新增 7」**（`--check` 就是这么红的）。
                #   它们既不是算子、也不再是注册面板 ⇒ 不进 B 面（判据 = idname 前缀）。
                if ident.startswith("VIEW3D_PT_"):
                    continue
                label = attrs.get("bl_label", ("—", node.lineno))[0]
                note = attrs.get("bl_description", ("—", node.lineno))[0]
                operators.append(Row("operator", (_code(ident), _cell(label), _cell(note), where)))
    return (
        sorted(panels, key=lambda r: r.cells[0]),
        sorted(operators, key=lambda r: r.cells[0]),
    )


# ───────────────────────── 面 C · GUI（ast） ─────────────────────────

def _trailing_comments(path: Path) -> dict:
    """行号 -> 那一行的 `# 注释`（`tokenize` 取；`ast` 看不见普通注释）。"""
    out = {}
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string.lstrip("#").strip()
    return out


def gui_rows():
    """C 面：页面类（`app/screens/**`）＋ 外壳注册（`main_window.py`）。"""
    pages = []
    for path in sorted(SCREENS.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(_parse_py(path)):
            if not isinstance(node, ast.ClassDef):
                continue
            if not (node.name.endswith("Page") or node.name.endswith("Screen")):
                continue
            bases = ", ".join(ast.unparse(b) for b in node.bases) or "—"
            where = _code(f"{rel}:{node.lineno}")
            pages.append(Row("page", (_code(node.name), _cell(bases), where)))

    mounts = []
    rel = MAIN_WINDOW.relative_to(ROOT).as_posix()
    tree = _parse_py(MAIN_WINDOW)
    comments = _trailing_comments(MAIN_WINDOW)
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if not (isinstance(target, ast.Name) and target.id == "NAV_PAGES"):
            continue
        for item in ast.walk(node.value):
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                index = _code(f"NAV_PAGES[{len(mounts)}]")
                note = _cell(f"导航页「{item.value}」")
                mounts.append(Row("mount", (index, note, _code(f"{rel}:{node.lineno}"))))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "attr", None) != "addWidget":
            continue
        if getattr(func.value, "attr", None) != "_tabs" or not node.args:
            continue
        note = comments.get(node.lineno) or "（这一行没有注释说明）"
        mounts.append(
            Row(
                "mount",
                (
                    _code(ast.unparse(node.args[0])),
                    _cell(f"页签：{note}"),
                    _code(f"{rel}:{node.lineno}"),
                ),
            )
        )
    return sorted(pages, key=lambda r: r.cells[0]), mounts


# ───────────────────────── 采集 ＋ 渲染 ─────────────────────────

def collect(parser=None):
    """三个面一次抽全，按**固定顺序**交回全部行（顺序固定 ⇒ 渲染可逐字节复现）。"""
    rows = list(cli_rows(parser)) if parser is not None else list(cli_rows())
    panels, operators = blender_rows()
    pages, mounts = gui_rows()
    return rows + panels + operators + pages + mounts


def counts(rows) -> dict:
    out = {}
    for row in rows:
        out[row.section] = out.get(row.section, 0) + 1
    return out


def counts_line(rows) -> str:
    """末尾那句数量自证 —— **数字全部从抽取结果算出来**，⛔ 不许在文档里写死。

    （反面样本：`kb/14` 那行「21 条工具」就是写死的，
    工具加到 25 条之后它照旧写着 21。）
    """
    n = counts(rows)
    return (
        f"{COUNT_LABELS[0]} {n.get('cli', 0)} 条 · "
        f"{COUNT_LABELS[1]} {n.get('panel', 0)} 个 · "
        f"{COUNT_LABELS[2]} {n.get('operator', 0)} 个 · "
        f"{COUNT_LABELS[3]} {n.get('page', 0)} 个"
    )


def _table(headers, rows) -> list:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out.extend("| " + " | ".join(r.cells) + " |" for r in rows)
    return out


def render(rows) -> str:
    """行 -> 整份 markdown。**纯函数**（同样的行必然给出同样的字节）。"""
    lines = [
        "# 产品功能清单（生成物）",
        "",
        REGEN_LINE,
        "",
        INTRO,
        "",
        "## 三个面的抽法（改完代码跑 `--check` 就知道这份烂没烂）",
        "",
        "| 面 | 抽取方式 |",
        "|---|---|",
        "| **A** | 活 parser 树 ＋ `ast` 配 `add_parser` 出处 |",
        "| **B** | `ast` 扫 `blender_addon/*.py`（面板按 `bl_space_type` 判） |",
        "| **C** | `ast` 扫 `app/screens/**` ＋ `main_window.py` 的外壳注册 |",
        "",
    ]
    for section, title, headers, note in SECTIONS:
        subset = [r for r in rows if r.section == section]
        lines.append(f"## {title}（{len(subset)} 条）")
        lines.append("")
        lines.append(MARKER % section)
        lines.append("")
        lines.append(note)
        lines.append("")
        lines.extend(_table(headers, subset))
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(counts_line(rows))
    lines.append("")
    return "\n".join(lines)


# ───────────────────────── 解析回来（--check 用） ─────────────────────────

_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def _split_row(line: str) -> list:
    """把 markdown 表格行拆成格（`\\|` 不是分隔符，**原样留两字符**）。"""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells = []
    buf = []
    escaped = False
    for ch in body:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            buf.append(ch)
            escaped = True
        elif ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def parse_rows(text: str):
    """doc 文本 -> [Row]；格式被改得读不出来时返回 `None`（⛔ 不猜）。

    靠机读锚点 `<!--cap:小节-->` 认节 —— 人改标题不会串节。
    """
    rows = []
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        match = re.fullmatch(r"<!--cap:([a-z0-9_]+)-->", stripped)
        if match:
            section = match.group(1)
            if section not in _HEADERS:
                return None
            continue
        if section is None or not stripped.startswith("|"):
            continue
        cells = _split_row(stripped)
        if all(_SEPARATOR.match(c) for c in cells):
            continue
        if tuple(cells) == _HEADERS[section]:
            continue
        rows.append(Row(section, tuple(cells)))
    return rows


def diff_rows(old, new):
    """两批行对账 -> (新增, 消失, 变了)。顺序按**文档顺序**（稳定，好读）。"""
    old_map = {r.key: r for r in old}
    new_map = {r.key: r for r in new}
    added = [r for r in new if r.key not in old_map]
    gone = [r for r in old if r.key not in new_map]
    changed = [
        (old_map[r.key], r) for r in new if r.key in old_map and old_map[r.key].cells != r.cells
    ]
    return added, gone, changed


def _row_text(row) -> str:
    return f"[{row.section}] " + " | ".join(row.cells)


def report_diff(added, gone, changed, *, stream=None) -> list:
    """把三档差异印成人读文本（红的那一次要能直接贴出来）。"""
    out = stream if stream is not None else sys.stdout
    lines = []
    for row in added:
        lines.append("  + 新增   " + _row_text(row))
    for row in gone:
        lines.append("  - 消失   " + _row_text(row))
    for before, after in changed:
        lines.append(f"  ~ 变了   {before.key[1]}")
        lines.append("            盘上：" + " | ".join(before.cells))
        lines.append("            现在：" + " | ".join(after.cells))
    for line in lines:
        print(line, file=out)
    return lines


# ───────────────────────── --check ─────────────────────────

def check(doc=DOC, *, parser=None, quiet=False, stream=None) -> int:
    """拿真代码重抽一遍跟盘上那份对。rc：0 一致 / 1 不一致 / 2 用不了。"""
    out = stream if stream is not None else sys.stdout
    doc = Path(doc)
    if not doc.is_file():
        print(f"✗ 生成物还没生成过：{doc}", file=out)
        print("  重生成：.venv/Scripts/python.exe tools/gen_capabilities.py", file=out)
        return RC_DIFF
    text = doc.read_text(encoding="utf-8")
    if REGEN_LINE not in text:
        print(f"✗ 生成物头一行（「别手改」那行）不在了：{doc}", file=out)
        return RC_DIFF
    old = parse_rows(text)
    if old is None:
        print(f"✗ 生成物的表格读不出来（格式被改过？）：{doc}", file=out)
        return RC_DIFF
    rows = collect(parser)
    added, gone, changed = diff_rows(old, rows)
    line_old = next((ln for ln in text.splitlines() if ln.startswith(COUNT_LABELS[0])), "")
    line_new = counts_line(rows)
    if not (added or gone or changed) and line_old == line_new:
        print(f"✔ 一致：{line_new}（{len(rows)} 行逐条对上）", file=out)
        return RC_SAME
    print(
        f"✗ 不一致：新增 {len(added)} / 消失 {len(gone)} / 出处或说明变了 {len(changed)}",
        file=out,
    )
    report_diff(added, gone, changed, stream=out)
    if line_old != line_new:
        print(f"  ~ 变了   计数行：{line_old or '（没有这一行）'} -> {line_new}", file=out)
    if not quiet:
        print("  重生成：.venv/Scripts/python.exe tools/gen_capabilities.py", file=out)
    return RC_DIFF


# ───────────────── --selftest（正控 ＋ 六条负控） ─────────────────

def _probe_parser(cli_main):
    """临时往**内存里**那个 parser 塞一个假子命令（⛔ 不碰盘）。"""
    parser = cli_main._build_parser()  # noqa: SLF001
    action = _subparsers_action(parser)
    if action is None:
        raise RuntimeError("顶层 parser 上没有 subparsers")
    action.add_parser("_probe_fake", help="探针：临时塞的假子命令（只在内存里）")
    return parser


def run_selftest() -> int:
    """正控 ＋ 六条负控：证明 `--check` **能红**（⛔ 只碰临时目录）。"""
    cli_main, _swap_ruler = _load_product()
    print(f"gen_capabilities --selftest · 仓根 {ROOT.as_posix()}")
    failures = []
    tmp = tempfile.mkdtemp(prefix="gen_capabilities_selftest_")
    good_rows = collect()
    good_text = render(good_rows)
    doc = Path(tmp) / "产品功能清单.md"
    doc.write_text(good_text, encoding="utf-8")

    def record(name, passed, note):
        print(f"{'✔' if passed else '✗'} {name}：{note}")
        if not passed:
            failures.append(name)

    code = check(doc, quiet=True)
    record(
        "正控 真代码 -> 真生成物",
        code == RC_SAME,
        f"rc={code}（应为 0）—— {counts_line(good_rows)}；{len(good_rows)} 行",
    )

    probe = _probe_parser(cli_main)
    code = check(doc, parser=probe, quiet=True)
    hit = [ln for ln in render(collect(probe)).splitlines() if "_probe_fake" in ln]
    record(
        "负控 1 临时塞假子命令 -> 红",
        code == RC_DIFF and bool(hit),
        f"rc={code}（应为 1）；多出来那一行：{(hit or ['**没找到**'])[0].strip()}",
    )
    code_again = check(doc, quiet=True)
    record("负控 1' 撤掉假子命令 -> 复绿", code_again == RC_SAME, f"rc={code_again}（应为 0）")

    tampered = good_text.replace("`ba-mod-maker find`", "`ba-mod-maker finder`", 1)
    path = Path(tmp) / "tampered.md"
    path.write_text(tampered, encoding="utf-8")
    code = check(path, quiet=True)
    record(
        "负控 2 手改生成物里一条",
        code == RC_DIFF and tampered != good_text,
        f"rc={code}（应为 1）",
    )

    dropped = "\n".join(
        ln for ln in good_text.splitlines() if not ln.startswith("| `ba-mod-maker validate`")
    )
    path = Path(tmp) / "dropped.md"
    path.write_text(dropped + "\n", encoding="utf-8")
    code = check(path, quiet=True)
    record(
        "负控 3 删掉生成物里一条", code == RC_DIFF and dropped != good_text, f"rc={code}（应为 1）"
    )

    noheader = good_text.replace(REGEN_LINE, "", 1)
    path = Path(tmp) / "noheader.md"
    path.write_text(noheader, encoding="utf-8")
    code = check(path, quiet=True)
    record(
        "负控 4 删掉「别手改」那行",
        code == RC_DIFF and noheader != good_text,
        f"rc={code}（应为 1）",
    )

    code = check(Path(tmp) / "不存在.md", quiet=True)
    record("负控 5 生成物根本不在", code == RC_DIFF, f"rc={code}（应为 1）")

    print("─" * 60)
    if failures:
        print(f"✗ 自检不通过（rc=1）：{len(failures)} 条没命中 —— {'、'.join(failures)}")
        return 1
    print("✔ 负控 6 条全部命中、正控 1 条未误报（证明 --check 能红）")
    return 0


# ───────────────────────── CLI ─────────────────────────

_DESCRIPTION = """生成 / 对账 docs/产品功能清单.md —— 「产品能做什么」的唯一活清单。

三个面**全机械抽**（⛔ 一条都不是手写的），每条带出处 文件:行号：
  A · CLI     活 parser 树（复用 tests/test_no_swap_wording.py::_children）
  B · Blender ast 扫 blender_addon/*.py（面板按 bl_space_type 判）
  C · GUI     ast 扫 app/screens/** ＋ app/shell/main_window.py 的外壳注册

末尾印数量自证：CLI 子命令 N 条 · Blender 面板 M 个 · 算子 K 个 · GUI 页面 P 个。"""

_EPILOG = """rc 口径（逐字，别猜）：
  不带参数   重新生成 docs/产品功能清单.md  -> rc=0 写成功
  --check    只对账不写盘：
               rc=0  生成物与真代码**逐条一致**（含末尾那句数量自证）
               rc=1  **不一致** —— 逐条印出「新增 / 消失 / 出处或说明变了」三档
                     （生成物缺失 / 「别手改」那行被删 / 表格读不出来，同样 rc=1）
               rc=2  用不了 —— import 不了产品代码（venv / src 不在）或复用件不见了
  --selftest 正控 ＋ 六条负控（临时塞假子命令 -> 红 -> 撤掉复绿 / 手改一条 /
             删一条 / 删「别手改」/ 生成物缺失），每条都真跑一遍 check()。
             末行印「负控 N 条全部命中」。rc=0 才算过。
  --quiet    只印结论（handoff_check.py 的 --measure 那一档用）

例（从仓根 BA_Mod_Maker_2.0 跑，用仓内 venv）：
  .venv/Scripts/python.exe tools/gen_capabilities.py
  .venv/Scripts/python.exe tools/gen_capabilities.py --check
  .venv/Scripts/python.exe tools/gen_capabilities.py --selftest
"""


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="gen_capabilities.py",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", action="store_true", help="只对账不写盘（rc=0 一致 / 1 不一致）")
    parser.add_argument("--selftest", action="store_true", help="正控 ＋ 六条负控（rc=0 才算过）")
    parser.add_argument("--quiet", action="store_true", help="只印结论")
    parser.add_argument("--out", default=None, metavar="文件", help="写到别的路径")
    return parser


def main(argv=None) -> int:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(argv)
    if args.selftest:
        return run_selftest()
    doc = Path(args.out) if args.out else DOC
    if args.check:
        try:
            return check(doc, quiet=args.quiet)
        except ImportError as exc:
            print(f"✗ 用不了：import 不了产品代码（{exc}）—— 从仓根用仓内 venv 跑")
            return RC_UNUSABLE
    try:
        rows = collect()
    except ImportError as exc:
        print(f"✗ 用不了：import 不了产品代码（{exc}）—— 从仓根用仓内 venv 跑")
        return RC_UNUSABLE
    text = render(rows)
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(text, encoding="utf-8", newline="\n")
    print(f"✔ 已写出 {doc.as_posix()}（{len(text.splitlines())} 行）")
    print("  " + counts_line(rows))
    return RC_SAME


if __name__ == "__main__":
    sys.exit(main())
