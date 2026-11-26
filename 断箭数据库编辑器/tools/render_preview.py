"""把主窗口渲染成 PNG，用于审阅深色主题。

用法:

    uv run python tools/render_preview.py [输出路径] [--offscreen]

为什么默认用真实平台（windows）而不是 offscreen:

- offscreen 平台**没有字体**（QFontDatabase 返回 0 个字体族），截图里的文字根本不会渲染，
  对不对得上真实界面；
- 主题审阅要看的就是“深色下文字刺不刺眼”，没字的截图没意义。

真实平台会瞬间弹出一个窗口（约 1 秒）。需要完全无弹窗时用 --offscreen，
但请知道那张图不能代表真实渲染效果。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_OUTPUT = Path("docs/theme-preview-dark.png")
WIDTH, HEIGHT = 1600, 940
SETTLE_MS = 600
QUIT_MS = 1200


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染主窗口截图")
    parser.add_argument("output", nargs="?", default=str(DEFAULT_OUTPUT), help="输出 PNG 路径")
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="用 offscreen 平台（无弹窗，但无字体，仅用于 CI 冒烟）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFontDatabase, QFontInfo
    from PySide6.QtWidgets import QApplication

    from ba_mod_maker.app.shell.main_window import MainWindow
    from ba_mod_maker.app.theme import apply_theme

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    apply_theme(app)

    families = QFontDatabase.families()
    resolved = QFontInfo(app.font()).family()
    print(
        f"平台：{app.platformName()}｜可用字体族：{len(families)}｜实际字体：{resolved or '(无)'}"
    )
    # 说明为什么 PNG 的像素尺寸大于窗口的逻辑尺寸：HiDPI。
    # 不写出来的话，2400x1410 对 1600x940 的窗口会被当成 bug 查半天。
    print(
        f"设备像素比：{app.devicePixelRatio():g}"
        f"（窗口逻辑尺寸 {WIDTH}x{HEIGHT} → 截图 {int(WIDTH * app.devicePixelRatio())}"
        f"x{int(HEIGHT * app.devicePixelRatio())} 物理像素）"
    )
    if not families:
        print(
            "警告：该平台下没有任何字体，截图中的文字将不可见。 参考异常结果，不代表真实界面。",
            file=sys.stderr,
        )

    window = MainWindow()
    window.resize(WIDTH, HEIGHT)
    window.show()
    window.raise_()
    window.activateWindow()

    exit_code = 0

    def capture() -> None:
        nonlocal exit_code
        pixmap = window.grab()
        if not pixmap.save(str(out_path)):
            print(f"截图保存失败：{out_path}", file=sys.stderr)
            exit_code = 1
            app.quit()
            return
        size_kb = out_path.stat().st_size / 1024
        print(f"截图已保存：{out_path}  ({pixmap.width()}x{pixmap.height()}, {size_kb:.1f} KB)")

    QTimer.singleShot(SETTLE_MS, capture)
    QTimer.singleShot(QUIT_MS, app.quit)
    app.exec()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
