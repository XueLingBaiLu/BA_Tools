"""一个类别块：左侧类别名 + **流式换行**的卡片。

❗ 这里刻意**没有分页**。第一版用了左右箭头翻页（"1/14"），那是错的：
真实军械库是卡片自动换行、整页纵向滚动的（见 ADR 0002 引用的参考截图）。
一行放几张由面板宽度自然决定，不做任何截断。

所有卡片一次性建出来。实测 395 张自绘控件的构建与排布在本机是可接受的，
换来的好处是滚动条长度、换行位置全部由 Qt 自然算出，不需要自己维护页码状态。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ba_mod_maker.app.screens.armory.unit_card import UnitCard
from ba_mod_maker.app.theme.icons import IconCache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import ArmoryRow
from ba_mod_maker.app.widgets import FlowLayout


class CategoryRow(QWidget):
    """一个类别块。"""

    card_clicked = Signal(int)
    card_activated = Signal(int)

    def __init__(
        self,
        row: ArmoryRow,
        parent: QWidget | None = None,
        icons: IconCache | None = None,
    ) -> None:
        super().__init__(parent)
        self._row = row
        self._icons = icons
        self._widgets: list[UnitCard] = []
        self._selected_unit = -1

        # 让外层（QVBoxLayout / QScrollArea）按"给定宽度需要多高"来排，
        # 否则流式布局会被压成一行。
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(TOKENS.spacing.md)

        self._label = QLabel(row.label)
        self._label.setProperty("role", "category-label")
        self._label.setFixedWidth(TOKENS.size.category_label_width)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._label)

        self._flow_host = QWidget()
        # 透明角色见 qss.py：没有它，这块会把卡片流末尾的空白刷成一整块纯色。
        self._flow_host.setProperty("role", "card-flow")
        host_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        host_policy.setHeightForWidth(True)
        self._flow_host.setSizePolicy(host_policy)
        self._flow = FlowLayout(self._flow_host, 0, TOKENS.size.card_gap, TOKENS.size.card_gap)
        layout.addWidget(self._flow_host, 1)

        for card in row.cards:
            widget = UnitCard(card, icons=icons)
            widget.clicked.connect(self.card_clicked)
            widget.activated.connect(self.card_activated)
            widget.set_selected(card.unit_id == self._selected_unit)
            self._flow.addWidget(widget)
            self._widgets.append(widget)

        self._label.setToolTip(f"{row.label} · {len(row.cards)} 个")

    # ------------------------------------------------------------------ #
    # 只读
    # ------------------------------------------------------------------ #

    @property
    def armory_row(self) -> ArmoryRow:
        return self._row

    @property
    def card_widgets(self) -> tuple[UnitCard, ...]:
        """本块的全部卡片控件。**没有分页，所以是全量。**"""
        return tuple(self._widgets)

    def line_count(self, width: int) -> int:
        """给定面板宽度，这些卡片会排成几行。"""
        return max(1, -(-len(self._widgets) // max(1, self.cards_per_line(width))))

    def cards_per_line(self, width: int = 0) -> int:
        """一行放得下几张。宽度为 0 时用当前实际宽度。"""
        available = width or max(0, self._flow_host.width())
        available = available or max(
            0, self.width() - TOKENS.size.category_label_width - TOKENS.spacing.md
        )
        step = TOKENS.size.card_width + TOKENS.size.card_gap
        return max(1, (available + TOKENS.size.card_gap) // step) if available else 1

    # ------------------------------------------------------------------ #
    # 选中
    # ------------------------------------------------------------------ #

    def set_selected_unit(self, unit_id: int) -> None:
        self._selected_unit = unit_id
        for widget in self._widgets:
            widget.set_selected(widget.unit_id == unit_id)

    def selected_unit(self) -> int:
        return self._selected_unit

    # ------------------------------------------------------------------ #
    # 徽章与卡名（跟着右卡的选项预览走，见 armory_screen._sync_wall_labels）
    # ------------------------------------------------------------------ #

    def card_widget(self, unit_id: int) -> UnitCard | None:
        """按单位 Id 取本行的卡片控件；本行没有就 None。"""
        for widget in self._widgets:
            if widget.unit_id == unit_id:
                return widget
        return None

    def reset_card_labels(self, *, except_unit: int = -1) -> None:
        """把本行每张卡的**成本徽章、名字与缩略图**恢复成卡墙给的缺省值。

        `except_unit` 那一张跳过（它是面板正在显示的单位，这三样另有来源）。
        已经是缺省值的卡是空操作，所以整墙扫一遍也就几百次比较。
        """
        for widget in self._widgets:
            if widget.unit_id != except_unit:
                widget.reset_cost()
                widget.reset_name()
                widget.reset_icon_key()

    # ------------------------------------------------------------------ #
    # 尺寸
    # ------------------------------------------------------------------ #

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        inner = max(1, width - TOKENS.size.category_label_width - TOKENS.spacing.md)
        return max(TOKENS.size.card_height, self._flow.heightForWidth(inner))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名
        return True

    def sizeHint(self):  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QSize

        width = self.width() or TOKENS.size.card_width * 6
        return QSize(width, self.heightForWidth(width))
