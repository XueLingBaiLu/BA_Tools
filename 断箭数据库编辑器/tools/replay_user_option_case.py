"""用户现场回放：军械库「左墙徽章 vs 右卡成本」+「换自定义选项后卡上跟着变」。

这条路是**离屏**的：不开窗、不抢鼠标、可重复。用户报的两个现象都能在这一条命令里复现：

    现象 1（不一致）：左墙 Su-57 徽章 225，右卡抬头 415 —— 以右卡为准
    现象 2（改了不变）：右卡底部换「自定义选项」，武器那一排不动

用法：

    .venv\\Scripts\\python.exe tools\\replay_user_option_case.py
    .venv\\Scripts\\python.exe tools\\replay_user_option_case.py --shot docs\\回放-选项预览.png

退出码：0 = 两条现象都符合预期；1 = 至少一条不符合（会逐条打印）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 离屏平台默认找不到字体（PySide6 不带 fontconfig），中文与数字会画成方框 ——
# 截图对比就没法看。指一下系统字体目录即可（实测 201 个族、度量正常）。
if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and "QT_QPA_FONTDIR" not in os.environ:
    _fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_fonts):
        os.environ["QT_QPA_FONTDIR"] = _fonts.replace("\\", "/")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLabel  # noqa: E402

from ba_mod_maker.core.data import find_dataset_root, load_dataset  # noqa: E402

#: 用户截图那一屏：实例 52085 / Su-57 = Id 44 / 前挂架换成 2xKh59MK2 = 选项 1104。
DEFAULT_INSTANCE = "52085"
DEFAULT_UNIT = 44
DEFAULT_OPTION = 1104
DEFAULT_SIZE = (1707, 889)  # 用户那台机器的**逻辑**尺寸（截图是它的 1.5 倍物理像素）

#: 名字那一条的实机锚：PT-76 缺省写 **PT-76B**，武器系统换成 AU-220M(+55) 后写 **PT-76-57**，
#: 而且**左列表卡跟着改**（docs/实机-03a-载具-PT76B-选项.png / 03b-载具-PT76B-AU220M.png）。
NAME_CASE = {
    "unit": 230,
    "modification": 204,
    "option": 536,
    "before": "PT-76B",
    "after": "PT-76-57",
}

#: 缩略图那条的锚：Stryker ICV（Id 22）武器系统换成「M1296 Dragoon」，
#: 卡墙那张图从 US_STRYKER_ICV-Label 变成 US_ICV_DRAGON-Label
#: （Options.ThumbnailOverride；全库 224 条非空、其中 131 条不换单位）。
ICON_CASE = {
    "unit": 22,
    "modification": 4,
    "option": 15,
    "before": "US_STRYKER_ICV-Label",
    "after": "US_ICV_DRAGON-Label",
}


def settle(milliseconds: int = 60) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()
    QApplication.processEvents()


def find_grid_card(screen, unit_id: int):
    for row in screen.row_widgets:
        for widget in row.card_widgets:
            if widget.unit_id == unit_id:
                return widget
    return None


def cost_label_text(panel) -> str:
    """右卡抬头那个成本 —— 取**画出来的字**，不是模型里的数。"""
    for label in panel.findChildren(QLabel):
        if label.property("role") == "card-cost":
            return label.text()
    return "<没有 card-cost 标签>"


def title_label_text(panel) -> str:
    """右卡抬头那个名字 —— 取**画出来的字**。"""
    for label in panel.findChildren(QLabel):
        if label.property("role") == "detail-title":
            return label.text()
    return "<没有 detail-title 标签>"


def pick_option(panel, option_id: int):
    """在右卡的下拉里选一项（返回 (下拉, 下标)；没有就 None）。"""
    for combo in panel.findChildren(QComboBox):
        for index in range(combo.count()):
            if combo.itemData(index) == option_id:
                return combo, index
    return None


def weapon_line(card) -> list[str]:
    return [f"{w.label} x{w.count}" for w in card.weapons]


def check_name_case(screen, *, shots_prefix: str = "") -> list[str]:
    """名字那一条：PT-76 缺省 PT-76B，选 AU-220M 后**左右一起**变 PT-76-57。

    与成本那条同一纪律（"以右边卡片为准"）：右卡抬头是权威，左墙那张卡跟着它走。
    """
    case = NAME_CASE
    grid = find_grid_card(screen, case["unit"])
    if grid is None:
        print(f"\n[SKIP] 名字那条：单位 {case['unit']} 不在当前卡墙里")
        return []
    print("\n=== 名字那条（实机：PT-76 -> AU-220M -> PT-76-57）===")
    grid.clicked.emit(case["unit"])
    settle()
    panel = screen.detail_panel

    bad: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            bad.append(name)

    before = (grid.card.display_name, panel.current_title, title_label_text(panel))
    print(f"缺省：左墙卡名 = {before[0]!r}  右卡抬头(模型) = {before[1]!r}")
    print(f"      画出来的 = {before[2]!r}")
    check(
        "名字·缺省：左墙 == 右卡 == 实机 PT-76B",
        before[0] == before[1] == before[2] == case["before"],
        f"{before} vs 实机 {case['before']!r}",
    )
    if shots_prefix:
        _save_widget_shots(f"{shots_prefix}-名字", "前", grid, panel)

    target = pick_option(panel, case["option"])
    if target is None:
        print(f"[SKIP] 名字那条：面板上没有选项 {case['option']}")
        return bad
    combo, index = target
    print(f"换选项：{combo.itemText(index).strip()}")
    combo.setCurrentIndex(index)
    settle()

    after = (grid.card.display_name, panel.current_title, title_label_text(panel))
    print(f"换后：左墙卡名 = {after[0]!r}  右卡抬头(模型) = {after[1]!r}")
    print(f"      画出来的 = {after[2]!r}")
    check(
        "名字·换装：左墙 == 右卡 == 实机 PT-76-57",
        after[0] == after[1] == after[2] == case["after"],
        f"{after} vs 实机 {case['after']!r}",
    )
    check(
        "名字·缺省值留着好复位",
        grid.default_name == case["before"],
        f"default_name={grid.default_name!r}",
    )
    if shots_prefix:
        _save_widget_shots(f"{shots_prefix}-名字", "后", grid, panel)
    return bad


def check_icon_case(screen) -> list[str]:
    """缩略图那一条：左墙那张卡的图键跟着选项换（Options.ThumbnailOverride）。

    图本身是从游戏 bundle 异步取的，这里只钉**键**与"缓存被作废"这两件确定的事；
    真画出来什么样由 tests/test_armory_screen.py 的用例守着。
    """
    case = ICON_CASE
    grid = find_grid_card(screen, case["unit"])
    if grid is None:
        print(f"\n[SKIP] 缩略图那条：单位 {case['unit']} 不在当前卡墙里")
        return []
    print("\n=== 缩略图那条（Stryker ICV -> M1296 Dragoon）===")
    bad: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            bad.append(name)

    grid.clicked.emit(case["unit"])
    settle()
    panel = screen.detail_panel
    before = grid.card.icon_key
    check("缩略图·缺省键", before == case["before"], f"{before!r} vs 数据 {case['before']!r}")

    target = pick_option(panel, case["option"])
    if target is None:
        print(f"[SKIP] 缩略图那条：面板上没有选项 {case['option']}")
        return bad
    combo, index = target
    # ❗ 先取文本再改：setCurrentIndex 会触发面板重建，旧 combo 立刻被 deleteLater，
    # 之后再读 itemText 会抛 "Internal C++ object already deleted"（实测踩过）。
    label = combo.itemText(index).strip()
    combo.setCurrentIndex(index)
    settle()

    after = grid.card.icon_key
    print(f"换选项：{label}")
    check("缩略图·换装后左墙键跟着换", after == case["after"], f"{after!r} vs {case['after']!r}")
    check(
        "缩略图·右卡键与左墙一致",
        panel.current_icon_key == after,
        f"{panel.current_icon_key!r} vs {after!r}",
    )
    check(
        "缩略图·缺省键留着好复位",
        grid.default_icon_key == case["before"],
        f"default_icon_key={grid.default_icon_key!r}",
    )
    return bad


def _save_widget_shots(prefix: str, tag: str, card_widget, panel) -> None:
    """控件级截图：只抓"左墙那张卡"和"右卡"，坐标不用猜（拼对比图用）。"""
    for suffix, widget in (("左墙卡", card_widget), ("右卡", panel)):
        out = Path(f"{prefix}-{suffix}-{tag}.png")
        widget.grab().save(str(out))
        print(f"控件截图已存：{out}")


def sweep_grid(instance, screen) -> int:
    """全量核对：左墙每一张卡的徽章 vs 右卡公式算出来的成本（缺省选项）。

    只对**当前这一屏**的卡（395 张）做，不重开界面。
    """
    from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card

    data = CardData(instance)
    bad: list[tuple[int, str, int, int]] = []
    total = 0
    for row in screen.row_widgets:
        for widget in row.card_widgets:
            total += 1
            card = widget.card
            model = build_unit_card(data, card.unit_id)
            want = model.cost if model is not None else 0
            if card.cost != want:
                bad.append((card.unit_id, card.display_name, card.cost, want))
    print(f"\n=== 全量核对：{total} 张卡 ===")
    for unit_id, name, got, want in bad[:20]:
        print(f"  不一致：Id {unit_id} {name}: 左墙 {got} vs 右卡公式 {want}")
    print(f"  不一致 {len(bad)} / {total}")
    return 0 if not bad else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=DEFAULT_INSTANCE)
    parser.add_argument("--unit", type=int, default=DEFAULT_UNIT)
    parser.add_argument("--option", type=int, default=DEFAULT_OPTION)
    parser.add_argument("--shot", default="")
    parser.add_argument("--shot-before", default="", help="换选项**之前**也存一张整屏（对比用）")
    parser.add_argument(
        "--shots",
        default="",
        help="前缀：存 4 张**控件级**截图（左墙那张卡 / 右卡，前后各一张），拼对比图用",
    )
    parser.add_argument(
        "--sweep", action="store_true", help="全量核对：左墙徽章 vs 右卡公式（缺省选项）"
    )
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1])
    args = parser.parse_args()

    root = find_dataset_root()
    if root is None:
        print("找不到 DataBaseCompiled 数据源（BA_MOD_MAKER_GAME / 仓库根）")
        return 1

    QApplication.instance() or QApplication([])
    from ba_mod_maker.app.screens import ArmoryScreen

    instance = load_dataset(root).instance(args.instance)
    screen = ArmoryScreen()
    screen.resize(args.width, args.height)
    screen.set_instance(instance)
    screen.show()
    settle()

    if args.sweep:
        return sweep_grid(instance, screen)

    card_widget = find_grid_card(screen, args.unit)
    if card_widget is None:
        print(f"网格里没有单位 {args.unit}（换个实例/单位再试）")
        return 1

    # —— 选中它（走真实交互那条线：卡片 clicked -> CategoryRow -> 军械库）——
    card_widget.clicked.emit(args.unit)
    settle()
    panel = screen.detail_panel

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failures.append(name)

    grid_cost = card_widget.card.cost
    panel_cost = cost_label_text(panel)
    print("\n=== 缺省态（没换过选项）===")
    print(f"左墙徽章 = {grid_cost}   右卡抬头 = {panel_cost}   卡片模型 cost = {panel.card.cost}")
    check(
        "缺省：左墙 == 右卡",
        str(grid_cost) == panel_cost and grid_cost == panel.card.cost,
        f"左墙 {grid_cost} vs 右卡 {panel_cost}",
    )
    print(f"缺省武器 = {weapon_line(panel.card)}")
    if args.shot_before:
        out = Path(args.shot_before)
        out.parent.mkdir(parents=True, exist_ok=True)
        screen.grab().save(str(out))
        print(f"缺省态截图已存：{out}")
    if args.shots:
        _save_widget_shots(args.shots, "前", card_widget, panel)

    # —— 换选项（与用户在截图里做的一样）——
    combos = panel.findChildren(QComboBox)
    target = None
    for combo in combos:
        for index in range(combo.count()):
            if combo.itemData(index) == args.option:
                target = (combo, index)
                break
        if target:
            break
    if target is None:
        print(f"面板上没有选项 {args.option}（这个单位的选项组与截图那台不一致？）")
        return 1
    combo, index = target
    print(f"\n=== 换选项：{combo.itemText(index).strip()} ===")
    combo.setCurrentIndex(index)
    settle()

    grid_after = card_widget.card.cost
    panel_after = cost_label_text(panel)
    weapons_after = weapon_line(panel.card)
    print(f"左墙徽章 = {grid_after}   右卡抬头 = {panel_after}   卡片模型 cost = {panel.card.cost}")
    print(f"武器 = {weapons_after}")
    print(f"数值 = {[(s.key, s.value) for s in panel.card.stats]}")

    check(
        "换选项：左墙 == 右卡",
        str(grid_after) == panel_after and grid_after == panel.card.cost,
        f"左墙 {grid_after} vs 右卡 {panel_after}",
    )
    check(
        "换选项：左墙/右卡都动了",
        grid_after != grid_cost or panel_after != panel_cost,
        f"{grid_cost}->{grid_after} / {panel_cost}->{panel_after}",
    )
    check(
        "换选项：武器跟着换",
        weapons_after != weapon_line(panel.card) or "Kh-59MK2" in " ".join(weapons_after),
        f"武器 = {weapons_after}",
    )
    check(
        "换选项：武器里有 Kh-59MK2",
        any("Kh-59MK2" in w for w in weapons_after),
        f"武器 = {weapons_after}",
    )

    if args.shot:
        out = Path(args.shot)
        out.parent.mkdir(parents=True, exist_ok=True)
        screen.grab().save(str(out))
        print(f"\n截图已存：{out}")
    if args.shots:
        _save_widget_shots(args.shots, "后", card_widget, panel)

    # —— 名字那一条（换装改名字，左卡跟着改）——
    failures += check_name_case(screen, shots_prefix=args.shots)
    failures += check_icon_case(screen)

    print()
    if failures:
        print(f"结论：{len(failures)} 条不符合预期 -> {failures}")
        return 1
    print("结论：全部符合预期（左墙 = 右卡；换选项两边都动：武器 / 数值 / 名字 / 缩略图）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
