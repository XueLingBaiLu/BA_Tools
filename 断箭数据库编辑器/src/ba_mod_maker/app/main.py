"""GUI 启动入口。

职责很窄：建 QApplication → 装主题 → **预取图标** → 开主窗口。
不在这里做任何业务初始化（那是 core 的事）。

❗ 顺序有讲究：主窗口**先建但不显示**，等图标预取跑完再 show。
不这样的话，窗口一出来用户就能滚，而每滚一屏都要现开 bundle
（立绘 304 MB、DLC 428/724 MB），必然一顿一顿。
缓存已就绪时预取是空转（1500 次 stat ≈ 10 ms），用户感觉不到这一步。
"""

from __future__ import annotations

import gc
import sys

from PySide6.QtWidgets import QApplication

from ba_mod_maker import __version__
from ba_mod_maker.app.shell.main_window import MainWindow
from ba_mod_maker.app.shell.prefetch import PrefetchSplash
from ba_mod_maker.app.theme import apply_theme


def _tune_gc() -> None:
    """放宽分代回收阈值。

    Qt 应用里对象图很大、且绝大多数是无环的 Qt 包装对象；默认阈值下
    滚动时很容易撞上一次**全量回收**——实测一次停顿 50~77ms，
    表现就是"滑条一卡一卡的"。放宽之后 gen2 触发频率降到十分之一左右。
    """
    gc.set_threshold(50_000, 50, 100)


def main(argv: list[str] | None = None) -> int:
    """启动 GUI，返回进程退出码。"""
    _tune_gc()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("BA_Mod_Maker")
    app.setApplicationDisplayName("BA_Mod_Maker 2.0")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("BA_Mod_Maker")

    apply_theme(app)

    # 先建窗（构造时已经把预取排好队），**但先不显示**。
    window = MainWindow()
    PrefetchSplash.run_for(window.icons)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
