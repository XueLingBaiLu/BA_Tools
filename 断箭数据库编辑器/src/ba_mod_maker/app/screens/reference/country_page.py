"""国家页（界面重做定稿 块 3）—— 一个**新页**，顶栏第二个页签。

用户口径：

    国家页：Countries 3 行 × 7 列整张铺开成表格，第一列画国旗
    （cache/icons/flags 的真图）。
    ❌ 用户否掉了「国旗大图预览」那一块，已删（"看起来太俗了"）。

为什么不挂在单位下面：阵营是**分类**，不是单位的组成部分。用户原话是
"阵营、专精不应该出现在单位下，它们跟单位没什么关系，应该放在左上角的军械库旁边"
（core/edit/scope.py 的 REFERENCE_ONLY_TABLES 是同一口径）。

## 一整张表铺开，为什么还点得动

行样式与别处一致（中文名 + 值 + 灰字英文列名），只是摆成了表格：
**点格子就地改**，改动只进缓存；落盘只有一条通道 app/shell/edit_session.py
（顶栏那颗「💾 保存」）。
国旗那一列不可改（它是资源键，图在本机 cache/icons/flags 里；没有就写「（空）」，
不编占位图）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.row_edit import build_inline_editor
from ba_mod_maker.app.theme.icons import scale_for_widget, shared_icon_cache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.core.assets.families import FLAG_FAMILY
from ba_mod_maker.core.edit.cells import FieldCell, row_cells, table_columns
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef

TABLE = "Countries"
#: 国旗图标族 —— 从 core/assets/families.py 取（字面量只在那儿一份）。
#: 表格里"国旗"那一列的列宽。
FLAG_COLUMN_WIDTH = 90


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


class CountryPage(QWidget):
    """Countries 整张表铺开 + 点格子就地改。"""

    #: 一句给状态栏的话（成功不弹窗，错误由外层弹）。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance = None
        self._session = None
        self._icons = shared_icon_cache()
        self._flag_labels: dict[int, QLabel] = {}
        self._flag_keys: dict[int, str] = {}
        self._value_buttons: dict[tuple[int, str], QPushButton] = {}
        self._cells: dict[tuple[int, str], FieldCell] = {}
        self._editing: QWidget | None = None
        self._editing_key: tuple[int, str] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.sm)

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.sm)
        self._title = _label("🏳 国家", "table-title")
        head.addWidget(self._title)
        self._subtitle = _label("", "entries-subtitle")
        head.addWidget(self._subtitle)
        head.addStretch(1)
        root.addLayout(head)

        self._hint = _label(
            "国旗是游戏包里的真图（cache/icons/flags）；没图的写「（空）」。"
            "点格子就地改；改动只进缓存，点顶栏「💾 保存」才落盘。",
            "muted",
        )
        root.addWidget(self._hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content = QWidget()
        self._content.setProperty("role", "detail-body")
        self._grid = QGridLayout(self._content)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(TOKENS.spacing.lg)
        self._grid.setVerticalSpacing(TOKENS.spacing.xs)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

        # 图标是异步到的：到了就补（与军械库那面旗同一套做法）。
        self._icons.loaded.connect(self._refresh_flags)

    # ------------------------------------------------------------------ #

    def set_session(self, session) -> None:
        """写盘通道（整个会话共用一份）。"""
        self._session = session

    def set_instance(self, instance) -> None:
        self._instance = instance
        self.rebuild()

    def rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # ❗ 摘可见控件必须走 detach（先 hide 再 setParent(None)）——
                #    直接摘会被 Qt 映射成一个顶层小窗（见 widgets/detach.py）。
                detach(widget)
        self._flag_labels.clear()
        self._flag_keys.clear()
        self._value_buttons.clear()
        self._cells.clear()
        self._editing = None

        if self._instance is None or TABLE not in self._instance.table_names:
            self._subtitle.setText("尚未加载数据源")
            return

        columns = table_columns(self._instance, TABLE)
        self._subtitle.setText(f"{TABLE} · {len(columns)} 列 · 点格子就地改")

        # 表头：中文名 + 灰字英文列名（与行样式同一口径）。
        flag_head = _label("国旗", "entry-name")
        flag_head.setFixedWidth(FLAG_COLUMN_WIDTH)
        self._grid.addWidget(flag_head, 0, 0)
        for offset, info in enumerate(columns):
            head = QWidget()
            box = QVBoxLayout(head)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(0)
            box.addWidget(_label(info.cn, "entry-name"))
            english = _label(info.column, "entry-column")
            english.setAlignment(Qt.AlignmentFlag.AlignLeft)
            box.addWidget(english)
            self._grid.addWidget(head, 0, offset + 1)
        self._grid.setColumnStretch(len(columns) + 1, 1)

        last_row = 0
        for row_index, row_ref in enumerate(self._rows(), start=1):
            last_row = row_index
            cells = row_cells(self._instance, row_ref)
            by_column = {cell.column: cell for cell in cells}

            flag_key = self._flag_key(row_ref.row_id)
            flag = QLabel()
            flag.setFixedSize(TOKENS.size.flag_width * 2, TOKENS.size.flag_height * 2)
            flag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if flag_key:
                self._flag_keys[row_ref.row_id] = flag_key
                pixmap = self._icons.pixmap(FLAG_FAMILY, flag_key)
                if pixmap is not None:
                    # 同样按屏幕缩放取图（150% 屏上不这么做旗子会发虚）。
                    flag.setPixmap(scale_for_widget(pixmap, flag.size(), self))
                else:
                    flag.setText("…")
            else:
                flag.setText("（空）")
                flag.setProperty("role", "entry-value")
            self._flag_labels[row_ref.row_id] = flag
            self._grid.addWidget(flag, row_index, 0)

            for offset, info in enumerate(columns):
                cell = by_column.get(info.column)
                if cell is None:
                    continue
                self._cells[(row_ref.row_id, info.column)] = cell
                button = QPushButton(cell.text or "（空）")
                button.setProperty("role", "entry-value")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                tip = f"{cell.cn} · {cell.place}"
                if cell.doc:
                    tip += f"\n{cell.doc}"
                button.setToolTip(tip)
                button.clicked.connect(lambda _=False, c=cell: self._open_editor(c))
                self._grid.addWidget(button, row_index, offset + 1)
                self._value_buttons[(row_ref.row_id, info.column)] = button

        # ❗ **多余的高度全给表格下面那一行**。
        # QGridLayout 默认把多余的高度**平摊给每一行**（各行的 stretch 都是 0）——
        # 没有一个"吃剩饭"的空行时，整张表会被拉成一行一条、隔得老远，
        # 正是用户报的"你这个界面，是不是拉得过长了？"。
        # 列方向同理：右边那个 stretch 列吃掉多余宽度。
        self._grid.setRowStretch(last_row + 1, 1)

    def _rows(self) -> tuple[RowRef, ...]:
        table = self._instance.table(TABLE)
        index = table.column_index("Id")
        return tuple(RowRef(TABLE, row[index]) for row in table.rows)

    # ------------------------------------------------------------------ #
    # 只读访问（用例与将来别的入口按这几个名字取，别去摸私有字段）
    # ------------------------------------------------------------------ #

    @property
    def row_ids(self) -> tuple[int, ...]:
        return tuple(row.row_id for row in self._rows()) if self._instance else ()

    @property
    def column_count(self) -> int:
        if self._instance is None or TABLE not in self._instance.table_names:
            return 0
        return len(self._instance.table(TABLE).columns)

    def cell_button(self, row_id: int, column: str) -> QPushButton | None:
        return self._value_buttons.get((row_id, column))

    def flag_label(self, row_id: int) -> QLabel | None:
        return self._flag_labels.get(row_id)

    @property
    def editing(self) -> QWidget | None:
        return self._editing

    def _flag_key(self, row_id: int) -> str:
        try:
            table = self._instance.table(TABLE)
        except KeyError:  # pragma: no cover - 上面已经查过表在不在
            return ""
        if "FlagFileName" not in table.columns:
            return ""
        id_index = table.column_index("Id")
        flag_index = table.column_index("FlagFileName")
        for row in table.rows:
            if row[id_index] == row_id:
                value = row[flag_index]
                return value if isinstance(value, str) else ""
        return ""

    def _refresh_flags(self) -> None:
        """图标迟到就补上（只补还没有 pixmap 的那些）。"""
        for row_id, key in list(self._flag_keys.items()):
            label = self._flag_labels.get(row_id)
            if label is None or label.pixmap() is not None:
                continue
            pixmap = self._icons.pixmap(FLAG_FAMILY, key)
            if pixmap is None:
                continue
            label.setText("")
            label.setPixmap(scale_for_widget(pixmap, label.size(), self))

    # ------------------------------------------------------------------ #
    # 就地编辑
    # ------------------------------------------------------------------ #

    def _open_editor(self, cell: FieldCell) -> None:
        """点格子：把编辑控件**放进这一格**（不是另开一行 / 另开窗）。"""
        if self._editing is not None:
            self._close_editor()
        key = (cell.row_id, cell.column)
        button = self._value_buttons.get(key)
        if button is None:
            return
        editor = build_inline_editor(
            cell,
            instance=self._instance,
            on_commit=self._commit,
            on_cancel=self._close_editor,
        )
        place = self._grid.getItemPosition(self._grid.indexOf(button))
        button.setVisible(False)
        self._grid.addWidget(editor, place[0], place[1])
        self._editing = editor
        self._editing_key = key
        editor.setFocus()

    def _close_editor(self) -> None:
        if self._editing is None:
            return
        editor = self._editing
        key = self._editing_key
        self._editing = None
        self._editing_key = None
        self._grid.removeWidget(editor)
        detach(editor)  # 先 hide 再摘（可见控件直接 setParent(None) 会变顶层小窗）
        if key is not None:
            button = self._value_buttons.get(key)
            if button is not None:
                button.setVisible(True)

    def _commit(self, ref: CellRef, value) -> None:
        if self._session is None:
            return
        self._session.set_value(ref, value)
        self._close_editor()
        # 用户口径（2026-09-29）：改一格**只进缓存**，写盘交给顶栏那颗「💾 保存」。
        # 这里原来每次提交都 save(silent=True) —— 全量写一遍包（实测 27ms）+ 全库校验，
        # 所以"改起来比较卡"。现在只改内存，保存是显式动作。
        self.rebuild()


__all__ = ["FLAG_COLUMN_WIDTH", "FLAG_FAMILY", "TABLE", "CountryPage"]
