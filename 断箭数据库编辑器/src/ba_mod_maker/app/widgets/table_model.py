"""把一张表接成 Qt 的表格模型（**可编辑**）。

为什么用 `QAbstractTableModel` 而不是 `QTableWidget`：

- `QTableWidget` 为每个单元格建一个 QTableWidgetItem 对象。
  `WeaponAmmunitions` 是 2357×6 尚可，但 `Ammunitions` 630×67 就是 42,210 个对象，
  且每次切表都要重建一遍；
- 模型方案下，Qt 只向模型要“当前可见那几十行”，切表只是换一个数据源。

编辑的关键不是“能改字”，而是**类型不能改对**：
用户敲进来的永远是文本，而 JSON 里的值是有类型的。
转换与校验在 `core.data.columns`，本模型只负责调它。

## 写表**不在这里**（2026-10-01）

从前 `setData` 是"自己写表"的：`self._table.rows[行][列] = 值` 再加一个自己的
`_dirty` 集合。后果是顶栏那个胶囊（只看 `EditSession.change_count`）**看不见**
数据表页改了什么 —— 💾 按钮不亮、Ctrl+S 说"没有改动"，而表其实已经被改了。

现在写入必须经**外壳注入的写通道**（:class:`TableWriteChannel`），
它落到 `app/shell/edit_session.py` 那**一份**账本上（按行 **Id** 寻址，
不是行下标 —— 插行会让行下标整体位移）。黄底、放弃改动也都读同一份账本。

行级写入（**整表加行 / 删行**，2026-10-01）走同一条路：通道上的
`insert_row` / `delete_row` 两个口子 -> `Changeset.insert_row` / `delete_row`。
新行的 **Id 由外壳分配**（`core/edit/clone.py::next_row_id(表, 账本)`），
本模型不自己算 Id —— 账本里的行级记录只有外壳拿得到。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Number
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core.data import (
    ColumnSpec,
    Table,
    format_value,
    infer_column_specs,
    parse_value,
)
from ba_mod_maker.core.edit.changeset import ID_COLUMN
from ba_mod_maker.core.errors import TableError

#: 超过这个长度的单元格在表格里截断显示（完整值在详情面板看）。
#: 为什么要截：单元格内容过长会把整张表撑得无法对齐，也让列宽自适失效。
_MAX_CELL_CHARS = 120

_TRUNCATION_MARK = "…"

#: 空值的前景色。预先构造：它在每个空单元格上都会被要求一次。
_MUTED_COLOR = QColor(TOKENS.color.text_muted)

#: 已修改单元格的背景。用暗黄，不抢眼但扫一眼就能看出哪里动过。
_DIRTY_BRUSH = QBrush(QColor(TOKENS.color.warning_subtle))

_EDIT_REVERT = "__revert__"


@dataclass(frozen=True, slots=True)
class TableWriteChannel:
    """数据表页的**写通道**：四个常驻口子 + 三个可选口子，全部指向**同一份账本**。

    由外壳（`app/shell/main_window.py`）注入，落到
    `app/shell/edit_session.py::EditSession` —— 也就是顶栏胶囊与「💾 保存」
    读的那一份。这样"改一格只进缓存"这条口径在**数据表页**上才真的成立：

    - `write_cell(表名, 行 Id, 列名, 值) -> bool`：写一格（**只进缓存，不落盘**）；
    - `is_dirty(表名, 行 Id, 列名) -> bool`：这一格改过没有（单元格黄底用它）；
    - `dirty_count(表名) -> int`：这张表改了几处（「放弃改动」按钮用它）；
    - `revert_table(表名) -> int`：丢掉**这张表**的改动（账本里逐条写回旧值，
      **不**再从磁盘重读整表 —— 重读会把磁盘上本来就与内存不一致的地方也抹掉）。

    行级的前两个口子是**可选**的（2026-10-01 加，整表加行 / 删行）：

    - `insert_row(表名, 整行值) -> 新行 Id`：插一行。**Id 由外壳分配**
      （`core/edit/clone.py::add_row` -> `next_row_id(表, 账本)`：
      现有最大 +1，且**本会话删过的 Id 不复用**）—— 所以调用方把 Id 传
      `None` 就行，别在这边自己算：算 Id 需要账本里的行级记录，而账本在壳里。
    - `delete_row(表名, 行 Id) -> None`：删一行（整行旧值留在账本里，可撤销）。

    为什么做成回调而不是直接拿 `EditSession`：`app/widgets/` 里的控件
    必须**自包含**（见本包 `__init__` 的说明：不依赖主窗口），
    而且用例可以塞一个替身进来单独断言数据表页的行为。

    没接上行操作（`None`）时数据表页**只读行、不假装能改**：
    按钮置灰并在状态栏说明原因，绝不退化成"自己改内存里的行"
    （那正是 `setData` 注释里记着的那个 P0）。
    """

    write_cell: Callable[[str, int, str, Any], bool]
    is_dirty: Callable[[str, int, str], bool]
    dirty_count: Callable[[str], int]
    revert_table: Callable[[str], int]
    #: 插一行；返回新行 Id（Id 由壳分配，见类说明）。没接上就是 None。
    insert_row: Callable[[str, Mapping[str, Any]], int] | None = None
    #: 删一行（按行 Id）。没接上就是 None。
    delete_row: Callable[[str, int], None] | None = None
    #: 撤销**最近一条**改动，返回一句给状态栏的话（Ctrl+Z 走它）。
    #: 账本里那条撤销栈（列级 + 行级）在外壳手里，所以这一口也只能由外壳给；
    #: 没接上时数据表页**不装** Ctrl+Z（装了却没得撤比不装更困惑）。
    undo_last: Callable[[], str] | None = None

    @property
    def can_edit_rows(self) -> bool:
        """行级增删接上了没有（两个口子是一套，缺一个就不算接上）。"""
        return self.insert_row is not None and self.delete_row is not None


def row_id_of(table: Table, source_row: int) -> int | None:
    """这一行的业务键（`Id` 列的值）；没有 `Id` 列返回 `None`。

    "哪一行"在账本里是 **Id**，不是行下标（`core/edit/changeset.py` 的模块说明）。
    所以凡是"把界面上选中的行交给账本/关系图"的地方，都要先过这一道。
    """
    try:
        index = table.column_index(ID_COLUMN)
    except KeyError:
        return None
    value = table.rows[source_row][index]
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class TableModel(QAbstractTableModel):
    """一张表的**可编辑**模型。

    写入走外壳注入的写通道（:class:`TableWriteChannel`）进账本，**本模型自己不写表**、
    也不自己记脏：改了什么、改了几处由那份账本回答。本模型只管
    "类型转得对不对、能不能撤回、可见行映射对不对"。

    属性
    ----
    filter_text
        大小写不敏感的子串过滤。空串表示不过滤。
        过滤会重建可见行映射，不修改原始数据。
    """

    #: 有单元格被改了（或改回了）。
    edits_changed = Signal()

    def __init__(self, table: Table | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._table: Table | None = None
        self._specs: tuple[ColumnSpec, ...] = ()
        self._visible_rows: list[int] = []
        self._filter_text = ""
        #: `Id` 列的下标（没有就是 None）。按 Id 寻址要频繁用它，缓存一份。
        self._id_column: int | None = None
        #: 外壳注入的写通道；None = 还没接上（这时 `setData` 拒绝写入并说明原因）。
        self._channel: TableWriteChannel | None = None

        if table is not None:
            self.set_table(table)

    # ------------------------------------------------------------------ #
    # 数据源
    # ------------------------------------------------------------------ #

    @property
    def table(self) -> Table | None:
        return self._table

    @property
    def filter_text(self) -> str:
        return self._filter_text

    @property
    def specs(self) -> tuple[ColumnSpec, ...]:
        """各列的推断类型（供编辑器代理与校验使用）。"""
        return self._specs

    def column_spec(self, column: int) -> ColumnSpec | None:
        if 0 <= column < len(self._specs):
            return self._specs[column]
        return None

    def set_write_channel(self, channel: TableWriteChannel | None) -> None:
        """接上写通道（外壳注入；也可以用例塞替身）。

        ⚠️ **没接上时 `setData` 拒绝写入**，不退化成"直接改表" ——
        数据表页可编辑的前提正是"改动能进账本"；退化成直接改内存就是那个 P0
        （改了却没进账、顶栏不亮、保存说"没有改动"）。
        """
        self._channel = channel
        self.refresh_dirty()

    @property
    def has_write_channel(self) -> bool:
        return self._channel is not None

    def set_table(self, table: Table | None) -> None:
        """换一张表。会重置过滤与按 Id 寻址用的列下标。"""
        self.beginResetModel()
        self._table = table
        self._specs = infer_column_specs(table.columns, table.rows) if table else ()
        self._filter_text = ""
        try:
            self._id_column = table.column_index(ID_COLUMN) if table is not None else None
        except KeyError:
            self._id_column = None
        self._rebuild_visible_rows()
        self.endResetModel()
        self.edits_changed.emit()

    def set_filter_text(self, text: str) -> None:
        """设置过滤关键词。相同则直接返回，避免无谓重建。"""
        normalized = text.strip()
        if normalized == self._filter_text:
            return
        self.beginResetModel()
        self._filter_text = normalized
        self._rebuild_visible_rows()
        self.endResetModel()

    @property
    def is_filtered(self) -> bool:
        return bool(self._filter_text)

    def source_row(self, row: int) -> int:
        """可视行下标 -> 原始行下标。"""
        if 0 <= row < len(self._visible_rows):
            return self._visible_rows[row]
        raise IndexError(f"行下标越界：{row}")

    def _rebuild_visible_rows(self) -> None:
        if self._table is None:
            self._visible_rows = []
            return
        if not self._filter_text:
            self._visible_rows = list(range(self._table.row_count))
            return

        needle = self._filter_text.casefold()
        self._visible_rows = [
            index
            for index, row in enumerate(self._table.rows)
            if any(needle in cell_text(value).casefold() for value in row)
        ]

    # ------------------------------------------------------------------ #
    # 改动追踪（**读账本，不是自己记**）
    # ------------------------------------------------------------------ #

    @property
    def dirty_count(self) -> int:
        """本表在**账本**里改了几处。没接通道 / 没表 -> 0。"""
        if self._channel is None or self._table is None:
            return 0
        return self._channel.dirty_count(self._table.name)

    @property
    def has_edits(self) -> bool:
        return self.dirty_count > 0

    def is_dirty(self, source_row: int, column: int) -> bool:
        """这一格改过没有（读账本）。列越界 / 没有 Id 列 -> False。"""
        table = self._table
        if table is None or self._channel is None:
            return False
        row_id = self._row_id(source_row)
        column_name = self._column_name(column)
        if row_id is None or column_name is None:
            return False
        return self._channel.is_dirty(table.name, row_id, column_name)

    def refresh_dirty(self) -> None:
        """账本变了：把可见区域重画一遍（黄底跟着账本走）。

        **刻意不发 `edits_changed`**：这一条是"刷新显示"，不是"又有新改动"；
        发信号会让"账本变了 -> 刷新 -> 又发信号"绕成圈。
        """
        if self._table is None or self.rowCount() == 0 or self.columnCount() == 0:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(self.rowCount() - 1, self.columnCount() - 1),
            [Qt.ItemDataRole.BackgroundRole],
        )

    def _row_id(self, source_row: int) -> int | None:
        """这一行的业务键（Id 列）。**账本按 Id 寻址**，行下标只用来读表。"""
        if self._table is None or self._id_column is None:
            return None
        if not 0 <= source_row < self._table.row_count:
            return None
        value = self._table.rows[source_row][self._id_column]
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _column_name(self, column: int) -> str | None:
        if self._table is None or not 0 <= column < self._table.column_count:
            return None
        return self._table.columns[column]

    def revert_all(self) -> int:
        """放弃**本表**的全部改动，返回还原了几处。

        走账本（`revert_table`：逐条把旧值写回去），**不再从磁盘重读整表**——
        重读等于"拿磁盘覆盖内存"：磁盘上本来就与内存不一致的地方会被一起抹掉，
        而且表对象会被换掉，别处持有的引用当场悬空。账本里每格都有旧值
        （`core/edit/changeset.py`），逐条写回才是"放弃改动"。
        """
        if self._table is None or self._channel is None:
            return 0
        count = self._channel.revert_table(self._table.name)
        # 行级改动（新增/删除）也被 revert_table 撤掉了 —— 行数变了，可见行映射得重建。
        self.reload_rows()
        self.refresh_dirty()
        self.edits_changed.emit()
        return count

    # ------------------------------------------------------------------ #
    # 行级：插/删之后的**原位刷新**与取值
    # ------------------------------------------------------------------ #

    @property
    def can_edit_rows(self) -> bool:
        """整表加行 / 删行接上了没有（外壳没接就置灰，见 TableWriteChannel）。"""
        return self._channel is not None and self._channel.can_edit_rows

    @property
    def has_id_column(self) -> bool:
        """这张表有没有 `Id` 列。

        账本与行操作全按 **Id** 寻址（行下标会被插行挤位移），没有它就做不了行级增删。
        """
        return self._id_column is not None

    def reload_rows(self) -> None:
        """表里的行被**原位**增删过：重建可见行映射与列类型，然后重画。

        为什么不直接调 `set_table`：那是"换一张表"—— 会清掉过滤词、重发
        `edits_changed`。插一行/删一行换的是**同一张表**的行数：
        用户正在筛的那几个字要留着（删掉一行后过滤结果该少一条，而不是全表倒出来），
        也不该假装"又有新改动"（账本没变，变的是表本身）。

        `infer_column_specs` 跟着重算：新行的值可能让某一列的类型判断变化
        （例如原本全 null 的列，复制过来的那一行有值）。
        """
        if self._table is None:
            return
        self.beginResetModel()
        self._specs = infer_column_specs(self._table.columns, self._table.rows)
        self._rebuild_visible_rows()
        self.endResetModel()

    def row_values(self, source_row: int) -> dict[str, Any]:
        """这一行的整行值（列名 -> 值）—— 「加行 / 复制行」拼新行就用它。

        `Table.cell` 取的是**同一个对象**（浅拷贝，与 `core/edit/clone.py::clone_row`
        同一口径）：数值 / 字符串都是不可变的，列表 / 字典值会共享引用。
        这与"复制一行"在其他入口上的行为一致，不在这里另发明一套深拷贝。
        """
        if self._table is None:
            raise IndexError("还没有表，取不到行值")
        row = self._table.rows[source_row]
        return {column: row[index] for index, column in enumerate(self._table.columns)}

    def row_of_id(self, row_id: int) -> int | None:
        """行 Id -> **原始**行下标；找不到返回 None（没有 Id 列也返回 None）。

        插/删行之后要把选中挪到新行上，而账本与界面都按 Id 说话，所以要有这一步。
        """
        table = self._table
        if table is None or self._id_column is None:
            return None
        for index, row in enumerate(table.rows):
            value = row[self._id_column]
            if isinstance(value, int) and not isinstance(value, bool) and value == row_id:
                return index
        return None

    # ------------------------------------------------------------------ #
    # QAbstractTableModel 接口
    # ------------------------------------------------------------------ #

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802 — Qt 命名
        if parent is not None and parent.isValid():
            return 0
        return len(self._visible_rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802 — Qt 命名
        if parent is not None and parent.isValid():
            return 0
        return self._table.column_count if self._table is not None else 0

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """只读模式下不给 ItemIsEditable；否则双击会弹编辑框却改不了。"""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or self._table is None:
            return None

        source_row = self.source_row(index.row())
        value = self._table.cell(source_row, index.column())

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self.display_text(index.row(), index.column())

        if role == Qt.ItemDataRole.ToolTipRole:
            raw = cell_text(value)
            if len(raw) <= _MAX_CELL_CHARS:
                return None
            return raw

        # 改过的单元格给一层底色，一眼能看出改动面（**读账本**，不是模型自己记的）
        if role == Qt.ItemDataRole.BackgroundRole and self.is_dirty(source_row, index.column()):
            return _DIRTY_BRUSH

        # 数值右对齐，便于竖着比大小。
        # 用 numbers.Number 而不是 (int, float)：后者会把 bool 也算进去。
        is_numeric = isinstance(value, Number) and not isinstance(value, bool)
        if role == Qt.ItemDataRole.TextAlignmentRole and is_numeric:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 空值用暗色标出，一眼能看出哪些字段没填。
        if role == Qt.ItemDataRole.ForegroundRole and value is None:
            return _MUTED_COLOR

        return None

    def setData(  # noqa: N802 — Qt 命名
        self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        """写入一个单元格 —— **经账本**（`TableWriteChannel`），本模型不写表。

        返回 `False` 表示拒绝（Qt 会保持原值）。
        转换失败 / 没有 Id 列 / 没接写通道时抛 `TableError`，
        由代理捕获并展示给用户（静默失败比报错更糟）。
        """
        table = self._table
        if not index.isValid() or table is None:
            return False
        if role != Qt.ItemDataRole.EditRole:
            return False

        source_row = self.source_row(index.row())
        column = index.column()
        spec = self.column_spec(column)
        if spec is None:
            return False

        current = table.cell(source_row, column)
        parsed = parse_value(spec, str(value))

        if parsed == current and type(parsed) is type(current):
            # 没变（含类型相同）就不记账，否则“点进去又出来”会伪造出一条改动。
            return False

        row_id = self._row_id(source_row)
        if row_id is None:
            raise TableError(
                f"{table.name} 里改不了这一格：找不到行 Id",
                hint=(
                    "账本按各表的 Id 列寻址（行号会在插行之后整体位移）。"
                    "这张表没有 Id 列，或这一格的 Id 不是整数。"
                ),
                table=table.name,
            )
        if self._channel is None:
            raise TableError(
                f"{table.name} 现在改不了：数据表页还没接上写盘通道",
                hint=(
                    "写入必须走 EditSession（唯一一份账本）；"
                    "接通道在外壳里做，见 app/shell/main_window.py。"
                ),
                table=table.name,
            )
        column_name = table.columns[column]
        if not self._channel.write_cell(table.name, row_id, column_name, parsed):
            return False

        self.dataChanged.emit(
            index,
            index,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.EditRole,
                Qt.ItemDataRole.BackgroundRole,
            ],
        )
        self.edits_changed.emit()
        return True

    def headerData(  # noqa: N802 — Qt 命名
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if self._table is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole and 0 <= section < self._table.column_count:
                return self._table.columns[section]
            # 列头提示里标出该列类型，省得用户试错
            if role == Qt.ItemDataRole.ToolTipRole and 0 <= section < len(self._specs):
                spec = self._specs[section]
                return f"{spec.name}（{spec.kind}）{spec.placeholder}"
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return section + 1
        return None

    # ------------------------------------------------------------------ #

    def display_text(self, row: int, column: int) -> str:
        """表格里显示的文本（已截断）。该方法也供按列宽自适调用。"""
        if self._table is None:
            return ""
        raw = cell_text(self._table.cell(self.source_row(row), column))
        if len(raw) > _MAX_CELL_CHARS:
            return raw[:_MAX_CELL_CHARS] + _TRUNCATION_MARK
        return raw

    def edit_text(self, source_row: int, column: int) -> str:
        """编辑框里的初始文本（**未截断、可原样解析回去**）。"""
        if self._table is None:
            return ""
        spec = self.column_spec(column)
        value = self._table.cell(source_row, column)
        return format_value(spec, value) if spec else cell_text(value)


def cell_text(value: Any) -> str:
    """把任意 JSON 值变成**只读展示**用的文本。

    三个特殊处理：

    - `None` -> 空串（而非字面的 "None"），避免误以为真有这个值；
    - `bool` -> ✓ / 空，比 True/False 更容易扫视；
    - `float` 且是整数值 -> 去掉 ".0"（数据里大量 1.0 这种本该是整数的值）。

    编辑框请用 `core.data.columns.format_value`：它要求能原样解析回去。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✓" if value else ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
