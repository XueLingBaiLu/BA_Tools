"""主题层。

两件事：

1. `tokens` —— 设计令牌的**唯一来源**（色值/间距/圆角/字体）。
2. `qss`   —— 把令牌渲染成 Qt 样式表，应用到 QApplication。

❗ 不要在其他任何地方硬编码颜色。要改主题，只改 `tokens.py`。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from ba_mod_maker.app.theme.background import Backdrop, BackdropWidget
from ba_mod_maker.app.theme.fonts import register_game_fonts, registered_game_families
from ba_mod_maker.app.theme.icons import (
    IconCache,
    find_icon_cache,
    find_runtime_cache,
    reset_shared_icon_cache,
    shared_icon_cache,
)
from ba_mod_maker.app.theme.qss import build_stylesheet
from ba_mod_maker.app.theme.tokens import TOKENS, ThemeTokens, apply_palette

__all__ = [
    "TOKENS",
    "Backdrop",
    "BackdropWidget",
    "IconCache",
    "ThemeTokens",
    "find_icon_cache",
    "find_runtime_cache",
    "register_game_fonts",
    "registered_game_families",
    "reset_shared_icon_cache",
    "shared_icon_cache",
    "apply_palette",
    "apply_theme",
    "build_stylesheet",
]


def apply_theme(app: QApplication, tokens: ThemeTokens = TOKENS) -> str:
    """把令牌应用到整个应用：调色板 + 样式表 + Windows 标题栏深色。

    返回生成的 QSS，方便调试与测试时直接查看。
    """
    # 顺序很重要：先定色制（会影响原生控件与标题栏），再应用 QSS 覆盖。
    apply_palette(app, tokens)
    stylesheet = build_stylesheet(tokens)
    app.setStyleSheet(stylesheet)
    return stylesheet
