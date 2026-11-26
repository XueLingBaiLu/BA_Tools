r"""where —— 把「左边 / 中间 / 右边」翻译成产品里的**尺子**，并给出实时读数。

由来（2026-10-03，用户问的）：

    用户：「该怎么尽量防止你靠猜的弊端？」

那天连着翻车三次，根因之一是**方位词没落地**：我以为「中间」是"页面中部"，
于是拿 page.findChildren(QWidget) 当尺子，量出 96 处违规 —— 其中绝大多数其实是
**左栏**那张卡的护甲区与属性格子。尺子错、数据对，结果按错的范围改了三轮。
口径与四条动作见 AGENTS.md §0.9。

用法（**只读**，不写任何文件）：

    .venv\Scripts\python.exe tools\where.py                 # 静态对照表
    .venv\Scripts\python.exe tools\where.py 中栏            # 只打那一栏
    .venv\Scripts\python.exe tools\where.py --rules         # 规矩 → 尺子 → 断言
    .venv\Scripts\python.exe tools\where.py 中栏 --live     # 真机读数（默认 offscreen）
    .venv\Scripts\python.exe tools\where.py 中栏 --live --show --unit 191

⚠️ **用尺子之前先看「正控」那一行**：左栏那张卡上挂着注释是**对的**（护甲区 / 属性格子），
所以 --live 里它应当 > 0。要是它也是 0，说明尺子或环境坏了 —— 那时**不许**拿读数下结论
（AGENTS.md §0.9 动作 2）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 方位词 → 产品标识。**尺子只认右列**，不收方位词本身。
PANES: tuple[tuple[str, str, str], ...] = (
    (
        "左栏",
        "BenchPage.build_left() 返回的那张卡（page.card）",
        "app/screens/editor/bench_page.py::build_left",
    ),
    (
        "中栏",
        "BenchPage._scroll（QScrollArea；里面的 widget 是 role=bench-body）",
        "bench_page.py::_attach_entries → insertWidget(1, self._scroll, 3)",
    ),
    (
        "右栏",
        "BenchPage.rail（BenchRail）",
        "bench_page.py:214 + _attach_entries → addWidget(self.rail, 0)",
    ),
)

#: 顶栏/其它布局**不在方位词口径内** —— 写在这里免得下一个人拿"上面"来当范围。
NOT_A_PANE = (
    "顶栏（MainWindow 的按钮条）不在这张表里：方位词只管编成台那三栏。",
    "「关系树 / 全部列 / 只读卡」那几页（screens/editor/pages/*）是另一套布局，也不在表里。",
)

#: 现存规矩 → 尺子 → 断言。**加新规矩就加一行**（落点约定见 AGENTS.md §0.9）。
RULES: tuple[tuple[str, str, str, str], ...] = (
    (
        "中栏**任何控件都不挂** tooltip",
        "BenchPage._scroll",
        "tests/test_bench_page.py::test_nothing_in_the_middle_pane_carries_a_tooltip",
        "「我叫你不要在中间显示悬浮窗注释 / 我要你在右边显示悬浮窗注释」",
    ),
    (
        "右栏**每条词条都必须有** tooltip（全名 ＋ 表.列）",
        "BenchPage.rail 里 role=bench-rail-key 的标签",
        "tests/test_bench_detail.py::test_every_rail_key_carries_its_annotation",
        "「我右边的词条注释悬浮窗你给我加回来啊」",
    ),
    (
        "左栏那张卡的数据注释**保留**（不在「中间不许有」的范围里）",
        "BenchPage.card（role=armor-zone / stat-chip）",
        "tests/test_armory_screen.py 那一套卡片用例",
        "——",
    ),
    (
        "中栏折叠栏**默认收起**；记号 / 名字 / 图标三处都能折",
        "BenchSection / TurretsBench 的行",
        "tests/test_bench_kit.py::test_a_section_is_collapsed_by_default 等",
        "「我要所有折叠栏默认折叠」「我点图标也要能展开和折叠折叠栏」",
    ),
)


def _table(only: str | None) -> None:
    print("方位词 → 尺子（产品标识）：")
    for name, ident, where in PANES:
        if only and only not in (name, ident):
            continue
        print(f"  {name}  {ident}")
        print(f"        出处：{where}")
    print()
    for line in NOT_A_PANE:
        print(f"  ⚠️ {line}")
    print()
    print("规矩 → 尺子 → 断言（加新规矩就加一行，落点见 AGENTS.md §0.9）：")
    for rule, ruler, test, quote in RULES:
        if only and only not in rule and not only.startswith("t"):
            continue
        print(f"  · {rule}")
        print(f"      尺子：{ruler}")
        print(f"      断言：{test}")
        print(f"      原话：{quote}")
    print()
    print("真机读数（几何 ＋ 控件计数）：where.py [中栏] --live")


def _live(unit: int, show: bool, only: str | None) -> int:
    if not show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from PySide6.QtWidgets import QApplication, QWidget

        from ba_mod_maker.app.shell.main_window import MainWindow
        from ba_mod_maker.app.theme import apply_theme
        from ba_mod_maker.core.data import find_dataset_root
    except Exception as exc:  # 工具：起不来就说清楚，别抛栈
        print(f"[where] 起不来（{type(exc).__name__}: {exc}）—— 只能给静态表：")
        print()
        _table(only)
        return 2

    dataset = find_dataset_root()
    if dataset is None:
        print("[where] 找不到 DataBaseCompiled 数据集（BA_MOD_MAKER_GAME / 仓根）—— 只能给静态表：")
        print()
        _table(only)
        return 2

    app = QApplication([])
    apply_theme(app)
    window = MainWindow()
    window.load_dataset(dataset)
    window._open_unit_editor(unit)
    window.resize(1700, 1000)
    window.show()
    app.processEvents()

    page = window._unit_editor.bench_page()
    panes = (("左栏", page.card), ("中栏", page._scroll), ("右栏", page.rail))
    print(f"单位 #{unit} | 窗口 {window.width()}x{window.height()} | 尺子读数：")
    for name, widget in panes:
        if only and only != name:
            continue
        kids = widget.findChildren(QWidget)
        tips = [w for w in kids if w.toolTip()]
        print(
            f"  {name}  x={widget.x()} y={widget.y()} w={widget.width()} h={widget.height()}"
            f" | 后代控件 {len(kids)} | 挂 tooltip {len(tips)}"
        )
        for w in tips[:5]:
            shown = w.text()[:16] if hasattr(w, "text") else ""
            print(f"        · {type(w).__name__}({shown!r}) {w.toolTip()[:40]!r}")

    print()
    # 正控：左栏那张卡上**应当**挂着注释（护甲区 / 属性格子）——
    card_tips = len([w for w in page.card.findChildren(QWidget) if w.toolTip()])
    print(
        f"  正控（左栏那张卡的注释浮窗）= {card_tips} —— 必须 > 0，"
        "否则这套量法 / 环境是坏的，别拿读数下结论。"
    )
    keys = [w for w in page.rail.findChildren(QWidget) if w.property("role") == "bench-rail-key"]
    bare = [w for w in keys if not w.toolTip()]
    print(f"  右栏词条 = {len(keys)} 条 | 没有注释浮窗的 = {len(bare)} 条（规矩：必须 0）")
    mid = [w for w in page._scroll.findChildren(QWidget) if w.toolTip()]
    print(f"  中栏挂着 tooltip 的控件 = {len(mid)} 个（规矩：必须 0）")
    print()
    print("复算这两条规矩：")
    for rule, _ruler, test, _quote in RULES[:2]:
        file_part, _, case_part = test.partition("::")
        name = file_part.split("tests/")[-1]
        print(f"  · {rule}")
        print(f"      pytest tests/{name} -q -k {case_part}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把「左边 / 中间 / 右边」翻译成产品里的尺子，并给出实时读数（只读）。",
    )
    parser.add_argument("词", nargs="?", default=None, help="左栏 / 中栏 / 右栏（省略 = 全打）")
    parser.add_argument("--rules", action="store_true", help="只打「规矩 → 尺子 → 断言」")
    parser.add_argument(
        "--live",
        action="store_true",
        help="起应用取实时几何与计数（默认 offscreen）",
    )
    parser.add_argument(
        "--show", action="store_true", help="用真显示器起窗口（会闪一个窗口，收尾硬退）"
    )
    parser.add_argument("--unit", type=int, default=21, help="开哪台单位（默认 21 = Stryker RV）")
    args = parser.parse_args()

    if args.show and not args.live:
        args.live = True
    if args.rules:
        _table("t")
        return 0
    if not args.live:
        _table(args.词)
        return 0
    return _live(args.unit, args.show, args.词)


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    # ❗ 图标加载线程会让进程留着、窗口不散（用户报过"窗口还得我手动关"）—— 工具收尾硬退。
    os._exit(rc)
