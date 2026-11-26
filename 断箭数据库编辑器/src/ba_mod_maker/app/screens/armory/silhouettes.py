"""按类别程序化绘制的单位剪影。

为什么先程序化而不是直接上游戏图标（ADR 0002）：卡片尺寸要围绕**真实剪影的
视觉重量**来调。七个类别如果长得一样，"像不像"就无从判断。

M2 会换成从 Units.ThumbnailFileName 提取的真实 PNG，届时**只换绘制来源，
不动布局**——两者都是给卡片一个 QPixmap。

坐标系统一为 100x100，调用方用 QTransform 缩放到目标矩形。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ba_mod_maker.app.theme.tokens import TOKENS

_SHAPE = 100.0


def _recon() -> QPainterPath:
    """侦察：双筒望远镜 + 顶部准星。"""
    path = QPainterPath()
    path.addEllipse(QPointF(32.0, 50.0), 15.0, 15.0)
    path.addEllipse(QPointF(68.0, 50.0), 15.0, 15.0)
    path.addRect(QRectF(40.0, 46.0, 20.0, 8.0))
    path.addRect(QRectF(48.0, 16.0, 4.0, 20.0))
    path.addRect(QRectF(36.0, 16.0, 28.0, 4.0))
    return path


def _infantry() -> QPainterPath:
    """步兵：头盔 + 躯干 + 四肢。"""
    path = QPainterPath()
    path.addEllipse(QPointF(50.0, 24.0), 11.0, 11.0)
    path.addRoundedRect(QRectF(38.0, 38.0, 24.0, 32.0), 8.0, 8.0)
    path.addRect(QRectF(41.0, 68.0, 7.0, 20.0))
    path.addRect(QRectF(52.0, 68.0, 7.0, 20.0))
    path.addRect(QRectF(28.0, 42.0, 9.0, 6.0))
    path.addRect(QRectF(63.0, 42.0, 9.0, 6.0))
    return path


def _vehicle() -> QPainterPath:
    """载具：履带 + 车体 + 炮塔 + 炮管。"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(8.0, 60.0, 84.0, 20.0), 9.0, 9.0)
    path.addRect(QRectF(14.0, 46.0, 72.0, 16.0))
    path.addRoundedRect(QRectF(28.0, 30.0, 34.0, 18.0), 4.0, 4.0)
    path.addRect(QRectF(56.0, 35.0, 40.0, 5.0))
    return path


def _support() -> QPainterPath:
    """支援：牵引炮 —— 斜置身管 + 炮架 + 大架。"""
    path = QPainterPath()
    tube = QPainterPath()
    tube.addRect(QRectF(16.0, 20.0, 66.0, 9.0))
    rotate = QTransform()
    rotate.translate(50.0, 38.0)
    rotate.rotate(-26.0)
    rotate.translate(-50.0, -38.0)
    path.addPath(rotate.map(tube))
    path.addRoundedRect(QRectF(22.0, 56.0, 56.0, 14.0), 4.0, 4.0)
    path.addEllipse(QPointF(28.0, 76.0), 12.0, 12.0)
    path.addRect(QRectF(46.0, 58.0, 6.0, 24.0))
    path.addRect(QRectF(56.0, 68.0, 32.0, 6.0))
    return path


def _logistics() -> QPainterPath:
    """未分类（CategoryType=4）：卡车。这一类（舰船）全都不进卡墙，剪影实际用不到。"""
    path = QPainterPath()
    path.addRect(QRectF(8.0, 42.0, 54.0, 32.0))
    path.addRoundedRect(QRectF(62.0, 54.0, 30.0, 20.0), 4.0, 4.0)
    path.addEllipse(QPointF(26.0, 80.0), 10.0, 10.0)
    path.addEllipse(QPointF(74.0, 80.0), 10.0, 10.0)
    return path


def _helicopter() -> QPainterPath:
    """直升机：机身 + 尾梁 + 主旋翼 + 起落橇。"""
    path = QPainterPath()
    path.addEllipse(QPointF(38.0, 54.0), 25.0, 17.0)
    path.addRect(QRectF(56.0, 50.0, 34.0, 7.0))
    path.addRect(QRectF(86.0, 38.0, 5.0, 28.0))
    path.addRect(QRectF(10.0, 28.0, 82.0, 4.0))
    path.addRect(QRectF(48.0, 32.0, 5.0, 9.0))
    path.addRect(QRectF(26.0, 72.0, 28.0, 4.0))
    return path


def _aircraft() -> QPainterPath:
    """战机：三角翼。"""
    path = QPainterPath()
    path.addPolygon(
        QPolygonF(
            [QPointF(50.0, 20.0), QPointF(94.0, 78.0), QPointF(50.0, 66.0), QPointF(6.0, 78.0)]
        )
    )
    path.addRect(QRectF(46.0, 14.0, 8.0, 66.0))
    path.addRect(QRectF(32.0, 70.0, 36.0, 5.0))
    return path


def _ship() -> QPainterPath:
    """舰船：舰体 + 上层建筑 + 桅杆。"""
    path = QPainterPath()
    path.addPolygon(
        QPolygonF(
            [QPointF(6.0, 58.0), QPointF(94.0, 58.0), QPointF(82.0, 84.0), QPointF(18.0, 84.0)]
        )
    )
    path.addRect(QRectF(38.0, 34.0, 26.0, 24.0))
    path.addRect(QRectF(48.0, 14.0, 5.0, 22.0))
    path.addRect(QRectF(30.0, 46.0, 46.0, 5.0))
    return path


SHAPE_RECON = 0
SHAPE_INFANTRY = 1
SHAPE_VEHICLE = 2
SHAPE_SUPPORT = 3
SHAPE_LOGISTICS = 4
SHAPE_HELICOPTER = 5
SHAPE_AIRCRAFT = 6
SHAPE_SHIP = 7

#: 立绘框的**固定长宽比**。
#:
#: 取绝大多数立绘的比例：实测 433 张里 **431 张是 816x548 ≈ 1.486**
#: （另 1 张 1024x512、1 张 2048x1540）。
#: 这样 99.5% 的单位是像素级贴合，不用裁也不用补边。
#:
#: ❗ 框必须**固定**，不能跟着每张原图变。
#: 踩过的坑：一度让框高跟随原图比例，结果 MOTOPEKHOTA（2:1）的框比别的步兵矮一截，
#: 一排看过去长短不一。**长宽一致优先于"零裁切"**——超出固定框的部分按 cover 直接裁，
#: 这正是游戏军械库的做法。
PORTRAIT_ASPECT: float = 816.0 / 548.0

#: 下标 = CategoryType。顺序与 CATEGORY_LABELS 严格一致。
_BUILDERS: tuple[Callable[[], QPainterPath], ...] = (
    _recon,
    _infantry,
    _vehicle,
    _support,
    _logistics,
    _helicopter,
    _aircraft,
    _ship,
)

#: UnitType -> 剪影。【实测】2 步兵 / 4 载具 / 8 直升机 / 16 战机 / 32 舰船。
_KIND_TO_SHAPE: dict[int, int] = {
    2: SHAPE_INFANTRY,
    4: SHAPE_VEHICLE,
    8: SHAPE_HELICOPTER,
    16: SHAPE_AIRCRAFT,
    32: SHAPE_SHIP,
}


def silhouette_index(category: int, kind: int) -> int:
    """决定这个单位画哪个剪影。

    **优先按 UnitType（物理形态），而不是 CategoryType（用途）。**
    实踩：侦察类里坐着 M1A2 和 LAV-25——按类别画成望远镜是错的。
    UnitType 缺失或未知时才退回 CategoryType。
    """
    shape = _KIND_TO_SHAPE.get(kind)
    if shape is not None:
        return shape
    if 0 <= category < len(_BUILDERS):
        return category
    return SHAPE_INFANTRY


#: 剪影路径的缓存。**必须缓存**：路径由几十条线段拼成，
#: 四百张卡滚动时每帧重建是纯浪费（实测一次 paintEvent 因此贵 10 倍）。
_PATHS: dict[int, QPainterPath] = {}


def silhouette_path(category: int) -> QPainterPath:
    """取某个类别的剪影。越界时退回步兵剪影（宁可有个形状，也不要空白）。

    ⚠️ 返回的是**共享**对象，调用方不许改它（draw_silhouette 只读）。
    """
    cached = _PATHS.get(category)
    if cached is None:
        builder = _BUILDERS[category] if 0 <= category < len(_BUILDERS) else _infantry
        cached = builder()
        # 子路径要取并集：默认的奇偶填充会在重叠处挖洞。
        cached.setFillRule(Qt.FillRule.WindingFill)
        _PATHS[category] = cached
    return cached


#: 画好的剪影位图缓存：(剪影, 宽, 高, dpr, 颜色) -> QPixmap。
#: 卡片是定尺寸的，同一张剪影要画几百次——**光栅化一次、之后只 blit**。
#: 实测：改成 blit 之后单次 paintEvent 从 ~0.9ms 掉到 ~0.3ms（冷缓存滚动尤其明显）。
_SILHOUETTE_PIXMAPS: dict[tuple, QPixmap] = {}
_SILHOUETTE_CACHE_MAX = 96


def draw_silhouette(painter: QPainter, shape: int, rect: QRectF, color: QColor) -> None:
    """把剪影画进 rect。painter 的 pen 会被忽略。

    shape 是**剪影下标**，不是类别——用 silhouette_index() 换算。
    """
    dpr = 1.0
    device = painter.device()
    if device is not None and hasattr(device, "devicePixelRatioF"):
        dpr = device.devicePixelRatioF() or 1.0
    width = max(1, int(round(rect.width())))
    height = max(1, int(round(rect.height())))
    key = (shape, width, height, round(dpr, 2), color.rgb())

    pixmap = _SILHOUETTE_PIXMAPS.get(key)
    if pixmap is None:
        pixmap = QPixmap(int(width * dpr), int(height * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        target = QPainter(pixmap)
        target.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        transform = QTransform()
        transform.scale(width * dpr / _SHAPE, height * dpr / _SHAPE)
        target.setPen(Qt.PenStyle.NoPen)
        target.setBrush(QBrush(color))
        target.drawPath(transform.map(silhouette_path(shape)))
        target.end()
        if len(_SILHOUETTE_PIXMAPS) >= _SILHOUETTE_CACHE_MAX:
            _SILHOUETTE_PIXMAPS.clear()
        _SILHOUETTE_PIXMAPS[key] = pixmap

    painter.drawPixmap(rect.topLeft(), pixmap)


class SilhouetteView(QWidget):
    """详情面板的图像位：有真实立绘就画立绘，没有就画程序化剪影。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shape = SHAPE_INFANTRY
        self._color: QColor | None = None
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scale_key: tuple[int, int, float] | None = None

        # 让外层按「给定宽度需要多高」来排 —— 见 heightForWidth。
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_silhouette(self, shape: int, color: QColor | None = None) -> None:
        self._shape = shape
        self._color = color
        self._pixmap = None
        self._scale_key = None
        self.update()

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        """设置真实立绘。传 None 等于回到剪影。"""
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._scale_key = None
        self.update()

    # ------------------------------------------------------------------ #
    # 尺寸：**框高跟随原图比例**
    # ------------------------------------------------------------------ #

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        """按**固定长宽比**算高度，与是哪张图无关。

        这样所有单位（含 MOTOPEKHOTA）的立绘框长宽完全一致，
        超出的部分由 cover 直接裁掉，不做任何缩放变形。
        """
        if width <= 0:
            return TOKENS.size.portrait_height
        return max(80, int(width / PORTRAIT_ASPECT))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        width = self.width() or 400
        return QSize(width, self.heightForWidth(width))

    def scaled_pixmap(self) -> QPixmap | None:
        """按控件尺寸与**设备像素比**缩放好的立绘，**cover（裁切填满）**。

        为什么是 cover 不是 contain：实机右栏的立绘是**填满**整块的
        （宽幅原图裁掉两侧），不是留黑边的 letterbox。
        之前用 KeepAspectRatio，430 宽的框里只出现 250 宽的图，看着"偏小"。

        与卡片同理：只按逻辑尺寸缩放会在 HiDPI 屏上发虚。
        """
        if self._pixmap is None:
            return None
        ratio = self.devicePixelRatioF() or 1.0
        key = (self.width(), self.height(), ratio)
        if self._scale_key != key or self._scaled is None:
            target = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
            scaled = self._pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            self._scaled = scaled
            self._scale_key = key
        return self._scaled

    def has_pixmap(self) -> bool:
        return self._pixmap is not None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        scaled = self.scaled_pixmap()
        if scaled is not None:
            ratio = scaled.devicePixelRatio() or 1.0
            # cover 会超出控件，**必须裁剪**，否则图会画到相邻分区上去。
            painter.setClipRect(self.rect())
            painter.drawPixmap(
                int((self.width() - scaled.width() / ratio) / 2.0),
                int((self.height() - scaled.height() / ratio) / 2.0),
                scaled,
            )
            painter.end()
            return

        color = self._color
        if color is None:
            color = QColor(TOKENS.color.silhouette)
        side = min(self.width(), self.height())
        rect = QRectF(
            (self.width() - side) / 2.0, (self.height() - side) / 2.0, float(side), float(side)
        )
        draw_silhouette(painter, self._shape, rect, color)
        painter.end()
