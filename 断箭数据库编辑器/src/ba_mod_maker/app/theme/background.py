"""程序化背景：深色渐变 + 细斜纹 + 确定性噪点。

为什么不用游戏截图（ADR 0002 决定 6）：

- 游戏截图里有 UI 残留（角色卡、按钮），直接当底图会脏；
- 拉伸后细节失真，且会把产品绑在一张外部图片上；
- 换分辨率还得重做图。

做法：按尺寸缓存一张 QPixmap，只在尺寸变化时重绘。
为什么必须缓存：斜纹是逐线画的（1600px 宽要上千条），
放进 paintEvent 会让滚动卡顿。

❗ 色值一律取自 tokens，本文件不出现任何字面色值
（tests/test_theme.test_no_hardcoded_colors_outside_tokens 会扫）。
"""

from __future__ import annotations

import random

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ba_mod_maker.app.theme.tokens import TOKENS, ThemeTokens

#: 噪点贴片边长。够大才不会看出重复，够小才便宜。
_NOISE_TILE = 96

#: 斜纹的横向间距（px）与斜率（1.0 = 45 度）。0.7 约 35 度。
_HATCH_STEP = 9
_HATCH_SLOPE = 0.7

#: 噪点随机种子。**固定**——每次启动纹理一样，截图对比才有意义。
_NOISE_SEED = 20261014


def _noise_brush(tokens: ThemeTokens) -> QBrush:
    """生成噪点贴片，用来消除大面积渐变的色带。"""
    rng = random.Random(_NOISE_SEED)
    image = QImage(_NOISE_TILE, _NOISE_TILE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    base = QColor(tokens.color.text_strong)
    painter = QPainter(image)
    for _ in range(_NOISE_TILE * 6):
        base.setAlpha(rng.randrange(6, 20))
        painter.setPen(base)
        painter.drawPoint(rng.randrange(_NOISE_TILE), rng.randrange(_NOISE_TILE))
    painter.end()
    return QBrush(image)


class Backdrop:
    """按尺寸缓存的程序化背景。同一个尺寸只渲染一次。"""

    def __init__(self, tokens: ThemeTokens = TOKENS) -> None:
        self._tokens = tokens
        self._cache: QPixmap | None = None
        self._noise: QBrush | None = None

    def pixmap(self, size: QSize) -> QPixmap:
        if size.isEmpty():
            return QPixmap()
        if self._cache is None or self._cache.size() != size:
            self._cache = self._render(size)
        return self._cache

    def paint_into(self, painter: QPainter, origin: QPoint, host_size: QSize, rect: QRect) -> None:
        """把这套背景画进 painter 的 rect 区域。

        origin 是"目标控件左上角在宿主坐标系里的位置"——纹理锚在宿主上，
        所以滚动区自己画的时候要按这个偏移取源区域，滚起来纹理才不会跟着跑。
        """
        pixmap = self.pixmap(host_size)
        if pixmap.isNull() or rect.isEmpty():
            return
        source = QRect(origin.x() + rect.x(), origin.y() + rect.y(), rect.width(), rect.height())
        clipped = source.intersected(QRect(0, 0, pixmap.width(), pixmap.height()))
        if clipped.isEmpty():
            # 落到缓存图之外（宿主变小/窗口刚建好）：用底色兜住，别留花屏。
            painter.fillRect(rect, QColor(self._tokens.color.bg_base))
            return
        target = QRect(
            rect.x() + clipped.x() - source.x(),
            rect.y() + clipped.y() - source.y(),
            clipped.width(),
            clipped.height(),
        )
        painter.drawPixmap(target, pixmap, clipped)

    def _render(self, size: QSize) -> QPixmap:
        colors = self._tokens.color
        pixmap = QPixmap(size)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 1) 纵向渐变。上浅下深，让内容区像被"压"在场景里。
        gradient = QLinearGradient(QPointF(0, 0), QPointF(0, size.height()))
        gradient.setColorAt(0.0, QColor(colors.backdrop_top))
        gradient.setColorAt(1.0, QColor(colors.backdrop_bottom))
        rect = QRect(0, 0, size.width(), size.height())
        painter.fillRect(rect, QBrush(gradient))

        # 2) 细斜纹：给纯色底一点方向感，但不抢内容。
        hatch = QPen(QColor(colors.backdrop_line))
        hatch.setWidth(1)
        painter.setPen(hatch)
        height = size.height()
        reach = height * _HATCH_SLOPE
        offset = -reach
        while offset < size.width():
            painter.drawLine(QPointF(offset, height), QPointF(offset + reach, 0.0))
            offset += _HATCH_STEP

        # 3) 噪点。
        if self._noise is None:
            self._noise = _noise_brush(self._tokens)
        painter.fillRect(rect, self._noise)

        painter.end()
        return pixmap


class BackdropWidget(QWidget):
    """把程序化背景画在自己身上的容器。子控件应当保持透明。"""

    def __init__(self, parent: QWidget | None = None, tokens: ThemeTokens = TOKENS) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._backdrop = Backdrop(tokens)

    @property
    def backdrop(self) -> Backdrop:
        return self._backdrop

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        self._backdrop.paint_into(painter, QPoint(0, 0), self.size(), self.rect())
        painter.end()
