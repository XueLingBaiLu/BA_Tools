"""字体工厂：把令牌里的族名与字距变成 QFont，并把**游戏自带的字体**注册进 Qt。

为什么集中在这里：

- 族名里带引号，每次都要 replace 容易漏；
- **字距必须每处都带上**——QSS 不支持 letter-spacing，
  中文的 condensed 观感全靠它，散着写迟早漏掉一处；
- 游戏字体的注册只有一处，免得"某个入口忘了注册、字体悄悄回落系统字体"。

游戏字体（Inter + Noto Serif CJK SC）从 `fonts_assets_all_*.bundle` 里取，
抽取逻辑在 `core/assets/fonts.py`（零 Qt）；这里只负责把它交给 QFontDatabase。
没装游戏时**静默回落**到 `tokens.typography.ui_family` 里排在后面的系统字体族。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

from ba_mod_maker.app.theme.tokens import TOKENS, ThemeTokens
from ba_mod_maker.core.assets.fonts import ensure_fonts


@lru_cache(maxsize=64)
def _cached(family: str, size: int, spacing: int, bold: bool) -> QFont:
    font = QFont(family.replace('"', ""), size)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    # 小字号汉字要全提示才清晰（与 apply_palette 保持一致，别只改一处）。
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


def _make(family: str, size: int, tokens: ThemeTokens, *, bold: bool) -> QFont:
    # ❗ **必须缓存**：paintEvent 里每次都要取字体（一张卡两次），
    # 每次新建 QFont + 设字距 + 设 hinting 在四百张卡滚动时是实打实的开销。
    # 返回一份**副本**：QFont 是隐式共享的，拷贝只是加引用计数，
    # 但这样调用方就算改了也污染不到缓存。
    return QFont(_cached(family, size, tokens.typography.letter_spacing_percent, bold))


def ui_font(size: int, *, bold: bool = False, tokens: ThemeTokens = TOKENS) -> QFont:
    """界面文本字体（Bahnschrift SemiCondensed 打头）。"""
    return _make(tokens.typography.ui_family, size, tokens, bold=bold)


def data_font(size: int, *, bold: bool = False, tokens: ThemeTokens = TOKENS) -> QFont:
    """数据文本字体（Arial Narrow 打头，数字等宽）。"""
    return _make(tokens.typography.data_family, size, tokens, bold=bold)


# --------------------------------------------------------------------------- #
# 游戏字体：注册一次，全局可用
# --------------------------------------------------------------------------- #

#: 注册结果缓存（族名）。None = 还没试过。
_registered: tuple[str, ...] | None = None


def register_game_fonts(
    *,
    cache_dir: Path | None = None,
    game_root: Path | None = None,
    force: bool = False,
) -> tuple[str, ...]:
    """把游戏包里那几支字体注册进 Qt。**幂等**，返回注册到的族名（可能为空）。

    失败路径一律静默：没装游戏、没装 UnityPy、包坏了、字体文件读了但 Qt 不认——
    只是"用系统字体"，界面照常能用（与图标/本地化的降级口径一致）。

    首次冷启动要从包里抽出 50 MB（实测约 2.7 秒），之后走缓存约 1 毫秒。
    """
    global _registered
    if _registered is not None and not force:
        return _registered
    families: list[str] = []
    try:
        paths = ensure_fonts(game_root, cache_dir)
    except Exception:  # noqa: BLE001 - 字体只是好看，不许拦住启动
        paths = ()
    for path in paths:
        try:
            font_id = QFontDatabase.addApplicationFont(str(path))
        except Exception:  # noqa: BLE001 - 单支失败不影响其它
            continue
        if font_id < 0:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in families:
                families.append(family)
    _registered = tuple(families)
    return _registered


def registered_game_families() -> tuple[str, ...]:
    """已经注册到 Qt 的游戏字体族（诊断 / 状态栏 / 测试用）。**不触发注册**。"""
    return _registered or ()
