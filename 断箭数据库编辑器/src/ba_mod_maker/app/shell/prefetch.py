"""开机预取：**先把图标灌满，再开主窗口**。

用户的原话是"能不能一次加载到位后，再打开 GUI"。可以，而且这是对的做法：

- 不预取就开窗，用户一滚就要现开 bundle（立绘包 304 MB、DLC 包 428/724 MB），
  滚动必然一顿一顿；
- 预取放在开窗之前，界面出来之后**一次都不会等**，滚动是纯内存/磁盘读。

缓存已经就绪时（第二次以后的启动）这里什么都不做：检查只是 1500 次 stat，
约 10 ms，看不到任何多余的东西。

进度**必须看得见**（用户原话："我希望在加载的时候能看到进度条"）
--------------------------------------------------------------
因此对话框上有四样东西，一个都不能省：

1. 进度条本体；
2. 计数与百分比（640/1519 · 42%）；
3. **剩余时间**（按最近 6 秒的速率估，见 IconCache.prefetch_eta）；
4. **正在取哪一族**（开一个大包要一两秒没有产出，只留个不动的进度条
   会被当成卡死）。

样式与文案和主窗口状态栏共用（IconCache 的 prefetch_status_text），
两处各写一套迟早会说不到一块去。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ba_mod_maker.app.theme.fonts import ui_font
from ba_mod_maker.app.theme.icons import prefetch_status_text
from ba_mod_maker.app.theme.tokens import TOKENS


class PrefetchSplash(QDialog):
    """预取进度条。**只在真有事可做时出现**。"""

    def __init__(self, cache, parent=None) -> None:
        super().__init__(parent)
        self._cache = cache
        self._total = 0
        self.setWindowTitle("BA_Mod_Maker 2.0")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        title = QLabel("正在准备图标")
        title.setFont(ui_font(TOKENS.typography.size_lg, bold=True))
        layout.addWidget(title)

        hint = QLabel(
            "首次运行要从游戏资源里取出全部图标（约 1500 张）。\n"
            "取完之后启动就是秒开，滚动也不会再等。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1)
        self._bar.setTextVisible(False)
        layout.addWidget(self._bar)

        # 计数 / 百分比 / 剩余时间：一行说清楚"还要多久"。
        self._status = QLabel("正在清点需要取哪些图标…")
        self._status.setFont(ui_font(TOKENS.typography.size_sm))
        layout.addWidget(self._status)

        # 正在取哪一族。开大包时进程没有产出，全靠这一行说明"它在忙"。
        self._current = QLabel("")
        self._current.setProperty("role", "muted")
        self._current.setFont(ui_font(TOKENS.typography.size_xs))
        layout.addWidget(self._current)

        self._skip = QPushButton("跳过（后台继续）")
        self._skip.setCursor(Qt.PointingHandCursor)
        self._skip.setToolTip("窗口先开，图标在后台继续取；状态栏上还能看到进度。")
        self._skip.clicked.connect(self.reject)
        layout.addWidget(self._skip, alignment=Qt.AlignmentFlag.AlignRight)

        game = getattr(cache, "game_root", None)
        source = QLabel(f"游戏目录：{game}" if game else "游戏目录：未找到，将使用本地缓存")
        source.setFont(ui_font(TOKENS.typography.size_xs))
        layout.addWidget(source)

        cache.progress.connect(self._on_progress)

    # ------------------------------------------------------------------ #

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self._total = total
        self._bar.setRange(0, total)
        self._bar.setValue(done)
        self._status.setText(prefetch_status_text(self._cache, done=done, total=total))
        current = getattr(self._cache, "prefetch_current_label", "")
        # 跑完的那一刻不再显示"正在取"，否则会停在最后一族上误导人。
        self._current.setText(f"正在取：{current}" if current and done < total else "")
        if done >= total:
            self.accept()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        # 排队的活儿是**开窗之前**就排好的，这里补一次当前进度
        self._on_progress(self._cache.prefetch_done, self._cache.prefetch_total)

    # ------------------------------------------------------------------ #

    @classmethod
    def run_for(cls, cache) -> bool:
        """需要预取就显示进度并等它跑完。返回**是否真的等了**。

        没有待办（缓存已就绪）时立刻返回 False，连窗口都不建。
        """
        total = getattr(cache, "prefetch_total", 0)
        if total <= 0 or getattr(cache, "prefetch_done", 0) >= total:
            return False
        dialog = cls(cache)
        dialog.exec()
        return True


__all__ = ["PrefetchSplash"]
