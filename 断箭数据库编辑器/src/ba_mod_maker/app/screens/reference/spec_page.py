"""专精页（界面重做定稿 块 3）—— 一个**新页**，顶栏第三个页签。

用户口径：

    专精页：左边 13 行选行 + 右边 24 列；其中 7 个兵种的
    「槽位数 + 点数预算」拼成 7×2 小矩阵（侦察/步兵/载具/支援/后勤/直升机/战机），
    最后 MaxSlots。标题旁一个 22px 专精图标（cache/icons/spec_icons）。
    ❌ 插画大图那一块已删（用户："看起来太俗了"）。

为什么两栏：这张表是「13 行 × 24 列」，一行里既有身份信息（Id/Name/Icon…）
又有 14 个槽位与点数预算。身份照别的页一条一行（行样式统一），
14 个数字拼成矩阵才读得出"这个专精能带多少"。

改动同样只进缓存（Changeset）：点值就地改，落盘走 app/shell/edit_session.py
（顶栏那颗「💾 保存」）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.row_edit import build_inline_editor
from ba_mod_maker.app.theme.icons import scale_for_widget, shared_icon_cache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import list_countries, list_specializations
from ba_mod_maker.app.widgets.detach import detach as detach_widget
from ba_mod_maker.app.widgets.row_entries import EntryList
from ba_mod_maker.core.assets.families import SPEC_ICON_FAMILY
from ba_mod_maker.core.edit.cells import FieldCell, row_cells
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef

#: 专精图标族 —— 从 core/assets/families.py 取（字面量只在那儿一份）。
TABLE = "Specializations"
#: 7 个兵种：列名（槽位数 / 点数预算）与中文名。顺序 = 游戏卡片上的顺序。
BRANCHES: tuple[tuple[str, str, str], ...] = (
    ("Recon", "侦察", "侦察"),
    ("Infantry", "步兵", "步兵"),
    ("Combat", "载具", "载具"),
    ("Support", "支援", "支援"),
    ("Logistics", "后勤", "后勤"),
    ("Helicopters", "直升机", "直升机"),
    ("Air", "战机", "战机"),
)
#: 左栏宽度（设计稿：560 太宽是检索页的；这一页给 300 够放 13 行）。
LIST_WIDTH = 300


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


class _MatrixBlock(QWidget):
    """7 个兵种的「槽位数 / 点数预算」小矩阵（14 个可改的格子）。"""

    value_activated = Signal(object, object)  # (FieldCell, QPushButton)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "detail-card")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.sm, TOKENS.spacing.md, TOKENS.spacing.sm
        )
        self._grid.setHorizontalSpacing(TOKENS.spacing.lg)
        self._grid.setVerticalSpacing(TOKENS.spacing.xs)
        self._buttons: dict[tuple[int, str], QPushButton] = {}

    def rebuild(self, cells: dict[str, FieldCell]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # ❗ 摘可见控件必须走 detach（先 hide 再 setParent(None)）——
                #    直接摘会被 Qt 映射成一个顶层小窗（见 widgets/detach.py）。
                detach_widget(widget)
        self._buttons = {}

        for column, text in ((0, "兵种"), (1, "槽位数"), (2, "点数预算")):
            head = _label(text, "matrix-head")
            if column:
                head.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(head, 0, column)
        for row_index, (key, cn, _) in enumerate(BRANCHES, start=1):
            self._grid.addWidget(_label(cn, "entry-name"), row_index, 0)
            for column, suffix in ((1, "Slots"), (2, "Points")):
                name = f"{key}{suffix}"
                cell = cells.get(name)
                if cell is None:
                    continue
                button = QPushButton(cell.text or "（空）")
                button.setProperty("role", "entry-value")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.setFixedWidth(120)
                button.setToolTip(f"{cell.cn} · {cell.place}")
                button.clicked.connect(
                    lambda _=False, c=cell, b=button: self.value_activated.emit(c, b)
                )
                self._grid.addWidget(button, row_index, column)
                self._buttons[(cell.row_id, name)] = button
        self._grid.setColumnStretch(3, 1)

    def button_for(self, row_id: int, column: str) -> QPushButton | None:
        return self._buttons.get((row_id, column))

    @property
    def value_count(self) -> int:
        """矩阵里那几格（7 个兵种 × 2 = 14）。"""
        return len(self._buttons)

    def place_of(self, button: QPushButton) -> tuple[int, int]:
        return self._grid.getItemPosition(self._grid.indexOf(button))

    def attach(self, editor: QWidget, place: tuple[int, int]) -> None:
        self._grid.addWidget(editor, place[0], place[1])

    def detach(self, editor: QWidget) -> None:
        self._grid.removeWidget(editor)
        # ❗ 名字与 widgets/detach.py 那个函数撞了，所以这里用别名 —— 摘之前必须
        #    先 hide（见 widgets/detach.py 的说明：可见控件直接 setParent(None) 会变顶层小窗）。
        detach_widget(editor)


class SpecPage(QWidget):
    """左选行 + 右看列（24 列：身份 9 条 + 7×2 矩阵 + MaxSlots）。"""

    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance = None
        self._session = None
        self._icons = shared_icon_cache()
        self._specs: tuple = ()
        self._rows: dict[int, RowRef] = {}
        self._editing: QWidget | None = None
        self._editing_host = None
        self._editing_place: tuple[int, int] | None = None
        self._editing_button: QPushButton | None = None
        self._current = -1

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QWidget()
        left.setProperty("role", "list-panel")
        left.setFixedWidth(LIST_WIDTH)
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        left_box.setSpacing(TOKENS.spacing.sm)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("🔎 搜专精…")
        self._filter.textChanged.connect(self._apply_filter)
        left_box.addWidget(self._filter)
        self._list = QListWidget()
        self._list.setProperty("role", "row-list")
        self._list.currentItemChanged.connect(self._on_row_picked)
        left_box.addWidget(self._list, 1)
        root.addWidget(left)

        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        right_box.setSpacing(TOKENS.spacing.sm)
        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.sm)
        self._icon = QLabel()
        self._icon.setFixedSize(TOKENS.size.spec_icon + 4, TOKENS.size.spec_icon + 4)
        head.addWidget(self._icon)
        self._title = _label("", "table-title")
        head.addWidget(self._title)
        self._subtitle = _label("", "entries-subtitle")
        head.addWidget(self._subtitle)
        head.addStretch(1)
        right_box.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content = QWidget()
        self._content.setProperty("role", "detail-body")
        content_box = QVBoxLayout(self._content)
        content_box.setContentsMargins(0, 0, 0, 0)
        content_box.setSpacing(TOKENS.spacing.sm)
        self._identity = _label("身份 / 显示", "detail-section")
        content_box.addWidget(self._identity)
        self._list_widget = EntryList()
        self._list_widget.cell_activated.connect(self._open_list_editor)
        content_box.addWidget(self._list_widget)
        self._matrix = _MatrixBlock()
        self._matrix.value_activated.connect(self._open_matrix_editor)
        content_box.addWidget(self._matrix)
        self._tail = EntryList()
        self._tail.cell_activated.connect(self._open_list_editor)
        content_box.addWidget(self._tail)
        content_box.addStretch(1)
        self._scroll.setWidget(self._content)
        right_box.addWidget(self._scroll, 1)
        root.addWidget(right, 1)

        self._icons.loaded.connect(self._apply_icon)

    # ------------------------------------------------------------------ #

    def set_session(self, session) -> None:
        self._session = session

    def set_instance(self, instance) -> None:
        self._instance = instance
        self.rebuild()

    def rebuild(self) -> None:
        self._close_editor()
        self._list.blockSignals(True)
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._list.count():
            self._list.takeItem(0)
        self._rows = {}
        self._specs = ()
        if self._instance is None or TABLE not in self._instance.table_names:
            self._list.blockSignals(False)
            self._title.setText("")
            self._subtitle.setText("尚未加载数据源")
            self._list_widget.set_cells(())
            self._matrix.rebuild({})
            self._tail.set_cells(())
            return

        countries = {c.country_id: c for c in list_countries(self._instance)}
        # hangar_only=False：设计稿要的是**整张表 13 行**（含 ShowInHangar=false 的
        # DLC / Editor 那几个）—— 这一页是"看数据"，不是"玩家能选什么"。
        self._specs = list_specializations(self._instance, hangar_only=False)
        for spec in self._specs:
            country = countries.get(spec.country_id)
            code = country.name[:2].upper() if country and country.name else ""
            item = QListWidgetItem(f"#{spec.specialization_id}  {spec.display_name}      {code}")
            item.setData(Qt.ItemDataRole.UserRole, spec.specialization_id)
            self._list.addItem(item)
            self._rows[spec.specialization_id] = RowRef(TABLE, spec.specialization_id)
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for index in range(self._list.count()):
            item = self._list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _on_row_picked(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None or self._instance is None:
            return
        spec_id = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(spec_id, int):
            return
        self.show_row(spec_id)

    # ------------------------------------------------------------------ #

    def show_row(self, spec_id: int) -> None:
        """右栏换成这一行的全部列（身份一条一行 + 7×2 矩阵 + MaxSlots）。"""
        self._close_editor()
        self._current = spec_id
        row = self._rows.get(spec_id)
        if row is None or self._instance is None:
            return
        cells = row_cells(self._instance, row)
        by_column = {cell.column: cell for cell in cells}

        spec = next((s for s in self._specs if s.specialization_id == spec_id), None)
        name = spec.display_name if spec is not None else f"#{spec_id}"
        self._title.setText(name)
        self._subtitle.setText(f"{TABLE}#{spec_id} · {len(cells)} 列 · 点值就地改")
        self._apply_icon(spec.icon_key if spec is not None else "")

        matrix_columns = {
            f"{key}{suffix}" for key, _, _ in BRANCHES for suffix in ("Slots", "Points")
        }
        identity = [
            cell
            for cell in cells
            if cell.column not in matrix_columns and cell.column != "MaxSlots"
        ]
        self._list_widget.set_cells(identity)
        self._matrix.rebuild(by_column)
        tail = [cell for cell in cells if cell.column == "MaxSlots"]
        self._tail.set_cells(tail)

    def _apply_icon(self, key: str = "") -> None:
        spec = next((s for s in self._specs if s.specialization_id == self._current), None)
        icon_key = key or (spec.icon_key if spec is not None else "")
        if not icon_key:
            self._icon.clear()
            return
        pixmap = self._icons.pixmap(SPEC_ICON_FAMILY, icon_key)
        if pixmap is None:
            return
        # 按**屏幕缩放**取图：150% 屏上直接把 22 逻辑像素的图贴上去会发虚
        # （用户报的"专精的图标不怎么清晰"）。
        self._icon.setPixmap(scale_for_widget(pixmap, self._icon.size(), self))
        self._icon.setToolTip(f"专精图标：{icon_key}")

    # ------------------------------------------------------------------ #
    # 就地编辑
    # ------------------------------------------------------------------ #

    def _open_list_editor(self, cell: FieldCell) -> None:
        if self._editing is not None:
            self._close_editor()
        host = self._list_widget if self._list_widget.row_for(cell.ref) else self._tail
        editor = build_inline_editor(
            cell,
            instance=self._instance,
            on_commit=self._commit,
            on_cancel=self._close_editor,
        )
        if host.open_editor(cell, editor) is None:
            return
        self._editing = editor
        self._editing_host = "list"
        self._editing_place = None
        self._editing_button = None

    def _open_matrix_editor(self, cell: FieldCell, button: QPushButton) -> None:
        if self._editing is not None:
            self._close_editor()
        editor = build_inline_editor(
            cell,
            instance=self._instance,
            on_commit=self._commit,
            on_cancel=self._close_editor,
        )
        place = self._matrix.place_of(button)
        button.setVisible(False)
        self._matrix.attach(editor, place)
        self._editing = editor
        self._editing_host = "matrix"
        self._editing_place = place
        self._editing_button = button
        editor.setFocus()

    def _close_editor(self) -> None:
        if self._editing is None:
            return
        editor = self._editing
        host, place, button = self._editing_host, self._editing_place, self._editing_button
        self._editing = None
        self._editing_host = None
        self._editing_place = None
        self._editing_button = None
        if host == "matrix":
            self._matrix.detach(editor)
        else:
            self._list_widget.close_editor()
            self._tail.close_editor()
            detach_widget(editor)
        if button is not None:
            button.setVisible(True)
        del place

    def _commit(self, ref: CellRef, value) -> None:
        if self._session is None:
            return
        self._session.set_value(ref, value)
        self._close_editor()
        # 用户口径（2026-09-29）：改一格**只进缓存**，写盘交给顶栏那颗「💾 保存」。
        # 这里原来每次提交都 save(silent=True) —— 全量写一遍包（实测 27ms）+ 全库校验，
        # 所以"改起来比较卡"。现在只改内存，保存是显式动作。
        self.show_row(self._current)


__all__ = ["BRANCHES", "LIST_WIDTH", "SPEC_ICON_FAMILY", "TABLE", "SpecPage"]
