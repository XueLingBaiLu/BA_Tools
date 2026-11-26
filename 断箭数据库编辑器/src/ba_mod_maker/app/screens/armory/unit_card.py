"""军械库的单张单位卡。

为什么自绘而不是拼子控件：卡片要在 168x118 里同时摆下剪影、成本徽章和名字。
用布局拼要 3 个子控件 + 3 层边距，缩放时对不齐；一次 paintEvent 画完更可控，
也更快（"全部单位"一屏四百张）。

图标位按 **2:1 左右的宽幅**留白：游戏的 ThumbnailFileName 实测是 416x216。
有真实图标就画真实图标（cache/icons/），没有就回落到程序化剪影——
一台没跑过 tools/extract_icons.py 的机器上界面必须照常能用。

❌ **卡墙上没有悬浮提示**（用户 2026-10-02：「我不要这个悬浮窗」）。从前
`UnitCard._update_tooltip` 会拼 5 行（名字 / Id·类别 / 类型 / 成本 / 图标键）挂在卡片上，
一悬停就弹出来盖住小半个卡墙 —— 实跑 52085：**395 张卡，每张都是 5 行**。
要那一行的信息去**右侧详情面板**（`UnitCardPanel`，31 列全在）；
⛔ **别再给卡片 `setToolTip`**（tests/test_armory_screen.py 里有用例守着）。
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QWidget

from ba_mod_maker.app.screens.armory.silhouettes import draw_silhouette, silhouette_index
from ba_mod_maker.app.theme.fonts import ui_font
from ba_mod_maker.app.theme.icons import IconCache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import ArmoryCard
from ba_mod_maker.core.assets.families import UNIT_LABEL_FAMILY

#: 单位缩略图那一族。**字面量只在 core/assets/families.py** —— 这里不再抄字符串。
ICON_FAMILY = UNIT_LABEL_FAMILY

#: 布局常量（卡片是定尺寸的，这些不会变）。
#: 按实机截图量的：卡片 160×110 **物理**（=107×73 逻辑 @150%），成本数字在右上角、
#: 名字在格底**最多两行**（实机 BTR-MDM / Rakushka 就是两行，不是省略号），中间留给图标。
_TOP_PAD = 1.5
_BADGE_HEIGHT = 12.5
#: 名字区高度：够两行**且带 descender**。
#: 实测 9pt 的 ascent=12 / descent=3：两行至少 12 + 12 + 3 = **27**，
#: 给 22 时每格只有 11 —— `Snaipersy` 的 p/y 尾巴正好被格子底切掉（用户报的"没显示全"）。
_NAME_HEIGHT = 27.0
_BADGE_WIDTH = 30.0
#: 名字最多几行（实机最多两行；再多就省略号）。
_NAME_LINES = 2


def _name_lines(metrics: QFontMetrics, text: str, width: float, max_lines: int = _NAME_LINES):
    """名字排成最多 `max_lines` 行。

    规则照实机：**先在空格处断词**（"BTR-MDM Rakushka" -> 两行），
    单个词比整行还宽时按字符断（中文名没有空格）。真超过两行才把最后一行省略号截断。
    """
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if not current or metrics.horizontalAdvance(trial) <= width:
            current = trial
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)

    # 单个词自己就超宽（中文名）：按字符断。
    out: list[str] = []
    for line in lines:
        if metrics.horizontalAdvance(line) <= width:
            out.append(line)
            continue
        piece = ""
        for char in line:
            if piece and metrics.horizontalAdvance(piece + char) > width:
                out.append(piece)
                piece = char
            else:
                piece += char
        if piece:
            out.append(piece)

    if len(out) <= max_lines:
        return out or [""]
    rest = " ".join(out[max_lines - 1 :])
    tail = metrics.elidedText(rest, Qt.TextElideMode.ElideRight, int(width))
    return [*out[: max_lines - 1], tail]


class UnitCard(QFrame):
    """一张单位卡：剪影 + 右上角成本 + 左上角可用数 + 下方单位名。"""

    #: 单击选中（参数：unit_id）
    clicked = Signal(int)
    #: 双击"打开"（参数：unit_id）
    activated = Signal(int)

    def __init__(
        self,
        card: ArmoryCard,
        parent: QWidget | None = None,
        icons: IconCache | None = None,
    ) -> None:
        super().__init__(parent)
        self._card = card
        #: 卡墙（build_armory_sheet）给的**缺省**成本。预览只改 `_card.cost`，不动它，
        #: 好让"换回缺省"不需要重算数据（见 set_cost / reset_cost）。
        self._default_cost = card.cost
        #: 同上，卡墙给的**缺省**名字（预览改了名字之后复位用）。
        self._default_name = card.display_name
        #: 同上，卡墙给的**缺省**缩略图键（预览换了图之后复位用）。
        self._default_icon_key = card.icon_key
        self._icons = icons
        self._selected = False
        self._hovered = False
        #: (设备像素比, 缩放好的图标)。dpr 变了（窗口挪到另一块屏）要重算。
        self._scaled_icon: tuple[float, QPixmap | None] | None = None
        self.setFixedSize(TOKENS.size.card_width, TOKENS.size.card_height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # 图标是**异步**从 bundle 里取的：迟到时要把这张卡重画一次。
        # 已经拿到图的卡直接返回——四百张卡不该为一张图集体重绘。
        if icons is not None:
            icons.loaded.connect(self._on_icons_loaded)

    def _on_icons_loaded(self) -> None:
        if self._scaled_icon is None or self._scaled_icon[1] is not None:
            return
        self._scaled_icon = None
        self.update()

    # ------------------------------------------------------------------ #
    # 只读接口
    # ------------------------------------------------------------------ #

    @property
    def card(self) -> ArmoryCard:
        return self._card

    @property
    def unit_id(self) -> int:
        return self._card.unit_id

    @property
    def default_cost(self) -> int:
        """卡墙给的**缺省**成本（不含选项预览）—— `reset_cost` 用它复位。"""
        return self._default_cost

    @property
    def default_name(self) -> str:
        """卡墙给的**缺省**名字（不含选项预览）—— `reset_name` 用它复位。"""
        return self._default_name

    @property
    def default_icon_key(self) -> str:
        """卡墙给的**缺省**缩略图键（不含选项预览）—— `reset_icon_key` 用它复位。"""
        return self._default_icon_key

    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def set_cost(self, cost: int) -> None:
        """**只改成本徽章**（右卡换了自定义选项时左墙跟着走），不重建整张卡。

        为什么要有这条：整屏 refresh 会重建 395 个控件，用户对卡顿敏感
        （docs/交接-本轮会话状态.md §3）；而换选项只影响一个数字。
        成本仍然是**预览**：只改这个控件的显示，回不到数据里。
        """
        cost = int(cost)
        if cost == self._card.cost:
            return
        self._card = replace(self._card, cost=cost)
        self.update()

    def reset_cost(self) -> None:
        """回到卡墙给的缺省成本（换单位 / 整屏重建之后）。"""
        self.set_cost(self._default_cost)

    def set_name(self, name: str) -> None:
        """**只改卡名**（右卡换了自定义选项时左墙跟着走），不重建整张卡。

        实机口径：换装会改名字，而且左列表卡跟着改 —— PT-76 选 AU-220M 之后，
        左卡与右卡抬头一起变成 PT-76-57（docs/实机-03a/03b）。名字与成本一样是
        **预览**：只改这个控件的显示，不落库。

        改的是 `ArmoryCard.display_name`（它优先取 hud_name），`ArmoryCard.name`
        保留原始列值 —— 与"成本只改显示"同一套做法。
        """
        name = str(name)
        if not name or name == self._card.display_name:
            return
        self._card = replace(self._card, hud_name=name)
        self.update()

    def reset_name(self) -> None:
        """回到卡墙给的缺省名字（换单位 / 整屏重建之后）。"""
        self.set_name(self._default_name)

    def set_icon_key(self, key: str) -> None:
        """**只换缩略图**（右卡换了自定义选项时左墙跟着换图），不重建整张卡。

        实机口径：`Options.ThumbnailOverride` 非空且不换单位的选项有 131 条 ——
        玩家换装之后卡上那张小图是会变的。与成本/卡名一样是**预览**：只改显示。

        ❗ 必须把 `_scaled_icon` 缓存作废：它是按设备像素比缓存的**缩放后**位图，
        换了键不失效的话 `paintEvent` 会继续画旧图（缓存命中，连 `pixmap()` 都不会再问）。
        """
        key = str(key)
        if key == self._card.icon_key:
            return
        self._card = replace(self._card, icon_key=key)
        # 缓存里那张是**上一个键**的图，留着就是画错图。
        self._scaled_icon = None
        self.update()

    def reset_icon_key(self) -> None:
        """回到卡墙给的缺省缩略图键（换单位 / 整屏重建之后）。"""
        self.set_icon_key(self._default_icon_key)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._card.unit_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._card.unit_id)
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #

    def name_rect(self) -> QRectF:
        """名字区的外框（卡片格底那两行）。**一定在卡片内**。"""
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        return QRectF(
            rect.x() + 3.0, rect.bottom() - _NAME_HEIGHT, rect.width() - 6.0, _NAME_HEIGHT
        )

    def name_baselines(self, count: int, metrics: QFontMetrics) -> list[float]:
        """每行文字的**基线 y**。

        为什么不用"等分矩形 + AlignVCenter"（两版都栽过）：
          1. 行距写死 11、每行矩形给 13 -> 第二行垂到卡片外（"文字溢出卡片"）；
          2. 改成均分之后每格 11，而 9pt 的 ascent+descent = 15 -> p/y 的尾巴被格底切掉
             （"没有显示全"）。
        自己排基线就没这些事：首行按 ascent 落，行距 = min(字体行高, 余量/(行数-1))，
        最后一行的 descent 正好压在名字区底边。
        """
        block = self.name_rect()
        rows = max(1, count)
        if rows == 1:
            # 单行：把字面（ascent+descent）在名字区里垂直居中。
            return [block.top() + (block.height() + metrics.ascent() - metrics.descent()) / 2.0]
        room = block.height() - metrics.ascent() - metrics.descent()
        step = min(float(metrics.height()), room / (rows - 1))
        return [block.top() + metrics.ascent() + index * step for index in range(rows)]

    def cost_rect(self, metrics: QFontMetrics | None = None) -> QRectF:
        """成本数字的绘制框：**右边贴死，左边按文字宽度长出去**。

        ❗ 为什么不能写死宽度（原来是 `_BADGE_WIDTH = 30`）：
        `drawText` 是右对齐，盒子比文字窄就把**最左边那位**裁掉 ——
        实测 MiG-31K (S) 的 1500 被画成 "|500"（用户报的"钱数没有显示全"）。
        保持右边缘不动（`_BADGE_WIDTH` 当最小宽），位数多了往左长。
        """
        if metrics is None:
            metrics = QFontMetrics(ui_font(TOKENS.typography.size_xs, bold=True))
        text = str(self._card.cost)
        width = max(_BADGE_WIDTH, float(metrics.horizontalAdvance(text)) + 2.0)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        return QRectF(rect.right() - 8.0 - width, rect.y() + _TOP_PAD, width, _BADGE_HEIGHT)

    def avatar_rect(self) -> QRectF:
        """图标区。夹在徽章行与名字行之间，**宽度按 card_icon_ratio 收窄并居中**。

        为什么不占满宽度：实机卡片上图标四周是有留白的（图标宽约卡片宽的 0.7）。
        按满宽画会让图标贴到卡片边缘，视觉上"太大、太挤"。
        """
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        top = rect.y() + _TOP_PAD + _BADGE_HEIGHT
        height = max(1.0, rect.bottom() - _NAME_HEIGHT - top)
        width = min(rect.width() - 16.0, rect.width() * TOKENS.size.card_icon_ratio)
        return QRectF(rect.center().x() - width / 2.0, top, width, height)

    def _icon(self) -> QPixmap | None:
        """按卡片尺寸缩放好的真实图标；没有则 None。

        ❗ **必须按设备像素比缩放，再把 dpr 设回去。**
        只按逻辑尺寸缩放的话，150% 屏上这张图会被合成器再放大 1.5 倍，
        图标就发虚——这是最容易"看着差不多但就是不清爽"的那种 bug。

        缩放结果缓存：卡片是定尺寸的，每次 paintEvent 重算纯属浪费
        （"全部单位"四百张卡，滚动时会很痛）。
        """
        ratio = self.devicePixelRatioF() or 1.0
        if self._scaled_icon is not None and self._scaled_icon[0] == ratio:
            return self._scaled_icon[1]

        result: QPixmap | None = None
        if self._icons is not None and self._card.icon_key:
            source = self._icons.pixmap(ICON_FAMILY, self._card.icon_key)
            if source is not None:
                box = self.avatar_rect().size()
                target = QSize(int(box.width() * ratio), int(box.height() * ratio))
                result = source.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                result.setDevicePixelRatio(ratio)
        self._scaled_icon = (ratio, result)
        return result

    def _palette(self) -> tuple[QColor, QColor, QColor, QColor]:
        """(底、边、剪影、名字字色)。三态：选中 / 悬停 / 常态。

        ❗ **常态是透明的（alpha=0）**：实机卡片就是"图标 + 名字 + 右上角成本"浮在场景上，
        没有底色也没有边框。原先每张卡都画实心方块 + 1px 边，一屏几十张就成了一堵砖墙 ——
        用户说的"太拥挤"就是这个。悬停/选中才给底与边（那两处需要明确的状态反馈）。
        """
        colors = TOKENS.color
        if self._selected:
            return (
                QColor(colors.card_bg_selected),
                QColor(colors.card_border_selected),
                QColor(colors.silhouette_selected),
                QColor(colors.text_strong),
            )
        if self._hovered:
            return (
                QColor(colors.card_bg_hover),
                QColor(0, 0, 0, 0),
                QColor(colors.text_primary),
                QColor(colors.text_primary),
            )
        return (
            QColor(0, 0, 0, 0),
            QColor(0, 0, 0, 0),
            QColor(colors.silhouette),
            QColor(colors.text_secondary),
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        background, border, silhouette, name_color = self._palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 常态两个 alpha 都是 0：一笔都不画（见 _palette 的注释）。
        if background.alpha() or border.alpha():
            painter.setPen(QPen(border, 1.0) if border.alpha() else Qt.PenStyle.NoPen)
            painter.setBrush(background if background.alpha() else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, float(TOKENS.radius.md), float(TOKENS.radius.md))

        # 图标：有真图就画真图，居中；没有就回落成程序化剪影。
        avatar = self.avatar_rect()
        icon = self._icon()
        if icon is not None:
            # 设过 dpr 的 pixmap，画出来的**逻辑尺寸**是 width/dpr。
            ratio = icon.devicePixelRatio() or 1.0
            painter.drawPixmap(
                int(avatar.center().x() - icon.width() / ratio / 2.0),
                int(avatar.center().y() - icon.height() / ratio / 2.0),
                icon,
            )
        else:
            side = min(avatar.width(), avatar.height())
            target = QRectF(
                avatar.center().x() - side / 2.0,
                avatar.center().y() - side / 2.0,
                side,
                side,
            )
            draw_silhouette(
                painter,
                silhouette_index(self._card.category, self._card.kind),
                target,
                silhouette,
            )

        # 成本：右上角**金色数字**（实机就是这样，没有底色方块）。
        # 暖金只出现在这一个位置 —— 去掉了方块，这条纪律反而更纯。
        # ❗ 成本盒子**按文字量算**，别写死宽度（见 cost_rect 的注释）。
        cost_font = ui_font(TOKENS.typography.size_xs, bold=True)
        painter.setPen(QColor(TOKENS.color.cost_text))
        painter.setFont(cost_font)
        painter.drawText(
            self.cost_rect(QFontMetrics(cost_font)),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            str(self._card.cost),
        )

        # 可用数**不画在卡片正面**：参考截图里卡片正面只有成本一个数字。
        # 它只在右侧详情面板里（31 列全在）——那是 mod 作者真正要改的数；
        # ⛔ 也不许挂在悬浮提示上（用户 2026-10-02：「我不要这个悬浮窗」，见 module docstring）。

        # 单位名：**最多两行，往下排**（实机 BTR-MDM / Rakushka 就是这样）。
        # 只有两行都放不下时才用省略号。
        font = ui_font(TOKENS.typography.size_xs)
        painter.setFont(font)
        painter.setPen(name_color)
        block = self.name_rect()
        metrics = QFontMetrics(font)
        lines = _name_lines(metrics, self._card.display_name, block.width())
        for line, baseline in zip(lines, self.name_baselines(len(lines), metrics), strict=True):
            painter.drawText(
                QPointF(block.center().x() - metrics.horizontalAdvance(line) / 2.0, baseline),
                line,
            )

        painter.end()
