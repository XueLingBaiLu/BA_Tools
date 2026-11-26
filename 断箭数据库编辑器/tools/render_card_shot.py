"""把单位卡片渲染成 PNG —— 给 GUI 改动做验收截图用。

用法（在 BA_Mod_Maker_2.0 目录下）：
    .venv\Scripts\python.exe tools\render_card_shot.py --unit 645 --out docs/卡片-xxx.png
    .venv\Scripts\python.exe tools\render_card_shot.py --unit 645 --weapon 32 --height 1600

为什么要有它：卡片是离屏渲染的（WA_DontShowOnScreen + grab），
还要等 IconCache 的异步图标落地、给布局一两次事件循环才能量到正确尺寸 ——
这套"settle"顺序试出来花了不少时间，别再重新试一遍。

--weapon 给武器 Id（不是列表序号），会切到详细模式并选中那把武器，顺带把它的详情行
打到终端，方便和游戏截图逐格对。--mode compact|detail 直接指定显示模式
（默认 compact = 游戏那张"三把武器并排"的卡）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel  # noqa: E402
from ba_mod_maker.app.theme.icons import IconCache  # noqa: E402
from ba_mod_maker.app.theme.qss import build_stylesheet  # noqa: E402
from ba_mod_maker.app.theme.tokens import apply_palette  # noqa: E402
from ba_mod_maker.core.data import find_dataset_root, load_dataset  # noqa: E402


def settle(app: QApplication, ms: int = 80) -> None:
    """给 Qt 一次事件循环：布局/样式/图标回调都靠它落地。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", type=int, required=True, help="单位 Id")
    parser.add_argument("--instance", default="52085")
    parser.add_argument("--weapon", type=int, default=0, help="详细模式下要选中的武器 Id（可选）")
    parser.add_argument("--mode", default="", choices=["", "compact", "detail"], help="显示模式")
    parser.add_argument("--out", default="")
    parser.add_argument("--width", type=int, default=430)
    parser.add_argument("--height", type=int, default=1400)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    apply_palette(app)
    app.setStyleSheet(build_stylesheet())

    instance = load_dataset(find_dataset_root()).instance(args.instance)
    icons = IconCache()
    panel = UnitCardPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.set_icons(icons)
    panel.set_instance(instance)
    panel.resize(args.width, args.height)
    panel.show()
    settle(app)

    panel.show_unit(args.unit)
    settle(app)

    if args.mode:
        panel.set_mode(args.mode)
        settle(app)

    card = panel.card
    if card is None:
        print(f"#{args.unit} 装配不出卡片")
        return 1

    if args.weapon:
        weapon = next((w for w in card.weapons if w.weapon_id == args.weapon), None)
        if weapon is None:
            print(
                f"#{args.unit} 没有武器 {args.weapon}；它有："
                + ", ".join(f"{w.weapon_id}:{w.label}" for w in card.weapons)
            )
            return 1
        panel._toggle_weapon(weapon.group_key)  # noqa: SLF001 - 验收脚本，故意走私有入口
        settle(app)

    icons.wait_for_idle(5.0)
    settle(app)

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        panel.grab().save(str(target))
        print(f"写出 {target}")

    print(f"=== #{args.unit} {card.title} | 成本 {card.cost} ===")
    for stat in card.stats:
        print(f"  {stat.label:<12} {stat.value:<10} [{stat.evidence}]")
    for weapon in card.weapons:
        mark = "▶" if args.weapon and weapon.weapon_id == args.weapon else " "
        rows = [(r.label, r.value) for r in weapon.detail]
        print(f" {mark} {weapon.label} ×{weapon.count} {rows}")
        for ammo in weapon.ammo:
            print(f"      {ammo.label} ×{ammo.count} {[(r.label, r.value) for r in ammo.detail]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
