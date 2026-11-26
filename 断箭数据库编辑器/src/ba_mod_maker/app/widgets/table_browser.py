"""表浏览器——自包含的可嵌入组件（**三栏：表 › 行 › 列**）。

设计契约（为什么这么写）：

- **不依赖主窗口**：它只接收一个 `Instance`，自己管自己的内部布局。
  首屏设计变更时，把它放到任何位置都能用。
- **不读磁盘**：数据由调用方通过 `set_instance()` 传入，它不知道数据源在哪。
- **写只有一条路**：改一格 / 加一行 / 删一行都经外壳注入的写通道
  （`table_model.TableWriteChannel` -> `app/shell/edit_session.py`），进**同一份账本**。
  没接通道时它**拒绝**写并说明原因 —— 绝不退化成"偷偷改内存"（那正是 P0 的形状）。

## 三栏版式（界面重做定稿 §3 块 9）

    表（24 张，按行数降序）  ›  行  ›  列

- **左**：24 张表一张一条 —— `emoji + 中文名 + 英文表名 + 行数×列数`，
  **按行数降序**（设计稿原话）；上面一个「搜表名 / 英文名」的框。
  表名与 emoji 走产品里那两张登记表（`table_title` / `table_emoji`），不在这里抄第二份。
- **中**：当前表的行，**仍然是全列表格**（`TableModel` + `CellDelegate`）：
  按列筛选、类型化编辑（bool 下拉 / 整数校验 / 越界报错）一个都没少；
  上面一排行操作按钮。为什么保留全列表格而不是做成「Id + 名字」两列：
  ① 数据表页的既有契约（`tests/test_editing_and_search.py`）按**列下标**寻址，
     缩列会让"跳到某一列"（检索命中 / 引用跳转）失去落点；
  ② 类型化编辑只有这里能给（右栏是只读的列面板）。
- **右**：选中那一行的**每一列一条**（序号 │ 中文名 │ 值 │ 英文列名），
  与 `app/widgets/row_entries.py::RowEntriesPanel` 同一套行样式（这里只读，
  编辑仍在中栏 —— 同一个值不给两个编辑入口）。
  中文列名走词典（`core/edit/dictionary.py`），查不到回落英文列名，不编。

## 行操作（2026-10-01 新增）

三个操作，**全部走 `EditSession` + `Changeset.insert_row/delete_row`**（唯一写盘/记账入口）：

| 操作 | 做什么 | 新行长什么样 |
|---|---|---|
| **＋ 加行** | 往当前表插一行 | **照抄该表第一行的值，只换 Id** |
| **⧉ 复制行** | 复制**选中**的那一行 | 照抄选中行的值，只换 Id |
| **🗑 删行** | 删掉**选中**的那一行 | —— |

口径（写死在这里，别再各页各定一套）：

1. **新行 = 复制一行、只换 Id**，不是"插一行全空"。24 张表没有自增主键，也没有
   "必填 / 默认值"这一说：全 None 的一行在游戏眼里多半是非法行（外键全 0、名字为空），
   用户还得自己补 67 列。照抄一行至少"长得像这个表"，改几个数就能用。
   加行照抄的是**该表第一行**（用户口径：新行从第一行来）；表里一行都没有时
   只能给全列 `None` —— 那是这张表本来就空，没有可抄的。
2. **Id 由外壳分配**：`values[Id] = None` 传下去，外壳走
   `core/edit/clone.py::add_row` -> `next_row_id(表, 账本)`
   （现有最大 +1，并把**本会话删过的 Id** 也算上 —— 否则"删掉最大 Id 再插一行"
   会捡回那个 Id，而那次删除还能被撤销，两行数据就抢一个 Id）。
   本控件不自己算 Id：那要读账本里的行级记录，而账本在外壳手里。
3. **不弹确认框**（用户口径：「我不需要这么多弹窗」）。结果写**状态栏一句话**
   （`status_message` 信号），删行那句话说清"有几行引用它"；
   删之前"被谁引用"看底部那块只读面板（它跟着选中的行走）。
4. **只进缓存**：加/删/复制都只是账本里的一条 `RowChange`，**不落盘**
   （落盘仍只有顶栏 💾 那一条路）。Ctrl+Z 可撤：快捷键装在**本控件自己**身上
   （`WidgetWithChildrenShortcut`，只在数据表页聚焦时生效 —— 全局那条会与
   单位编辑器的 Ctrl+Z 撞成 ambiguous shortcut），动作走
   `TableWriteChannel.undo_last()`；没接这个回调时不装快捷键。

对外入口：`set_instance()` + `select_table()` / `select_row()` / `select_cell()`；
对外信号：`table_changed` / `row_selected` / `status_message`。
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.table_labels import table_emoji, table_title
from ba_mod_maker.app.widgets.delegates import CellDelegate
from ba_mod_maker.app.widgets.table_model import (
    TableModel,
    TableWriteChannel,
    cell_text,
    row_id_of,
)
from ba_mod_maker.core.data import Instance, Table
from ba_mod_maker.core.edit.changeset import ID_COLUMN
from ba_mod_maker.core.edit.dictionary import field_info
from ba_mod_maker.core.edit.refs import RefHit, referencing_rows

#: 自适列宽时参考的最大行数。全表扫一遍在 2357 行时也很快，
#: 但没必要——列宽只需要代表性。
_AUTOSIZE_SAMPLE_ROWS = 200

#: 左栏「搜表名 / 英文名」框里的提示语（也是它的宽度基准）。
_TABLE_FILTER_HINT = "搜表名 / 英文名…"


def _column_title(table: str, column: str) -> str:
    """列的中文名（词典只有一份：`core/edit/dictionary.py`；查不到回落英文列名）。"""
    return field_info(table, column).cn


def _column_width_bounds(metrics: QFontMetrics) -> tuple[int, int]:
    """列宽的下限与上限（px）。

    **从字体度量推导，而不是写死像素。**
    为什么：字号是可调的（见 tokens.typography）。
    若写死 56/320，字号一变大，下限会把窄列挤得看不清、
    上限会让宽列过早截断——而且下次改字号还得再找一遍这里。

    用数字宽度做基准：表格用等宽字体，所有字符同宽，
    所以它就是“一个字符多宽”的直接度量。
    """
    char = max(1, metrics.horizontalAdvance("0"))
    return char * 6, char * 34


#: 置 True 时打印列宽调试信息（环境变量 `BA_DEBUG_WIDTHS=1`）。
_DEBUG_WIDTHS = os.environ.get("BA_DEBUG_WIDTHS") == "1"

#: 空值文本用的暗色笔刷（单元格与列面板共用）。
_MUTED_BRUSH = QBrush(QColor(TOKENS.color.text_muted))

#: 左栏的初始宽度（右栏同理）。中栏吃掉剩下的。
_SIDE_PANEL_WIDTH = 320


class TableBrowser(QWidget):
    """一张表的浏览器：**三栏**（表 › 行 › 列）+ 行增删 + 类型化编辑。"""

    #: 当前表变了（带表名）。
    table_changed = Signal(str)
    #: 选中了一行（带原始行下标，-1 表示取消选择）。
    row_selected = Signal(int)
    #: 一句话给状态栏（行操作结果 / 撤销结果）。**不弹窗**（AGENTS §5）：
    #: 成功与解释都写字，只有错误才弹（错误由外壳的写盘通道负责）。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance: Instance | None = None
        self._channel: TableWriteChannel | None = None
        self._model = TableModel(parent=self)
        #: Ctrl+Z 那个快捷键；没接 undo_last 回调时是 None（不装）。
        self._undo_shortcut: QShortcut | None = None
        #: 上一次见到的行数 —— 别的页插/删行（如整单位克隆）之后要据此重建行映射。
        self._row_count = 0

        # 左栏：24 张表（按行数降序）
        self._table_caption = QLabel()
        self._table_filter = QLineEdit()
        self._table_list = QListWidget()
        # 中栏：当前表的行（全列表格 + 行操作）
        self._search = QLineEdit()
        self._add_button = QPushButton("＋ 加行")
        self._add_button.setCursor(Qt.PointingHandCursor)
        self._copy_button = QPushButton("⧉ 复制行")
        self._copy_button.setCursor(Qt.PointingHandCursor)
        self._delete_button = QPushButton("🗑 删行")
        self._delete_button.setCursor(Qt.PointingHandCursor)
        self._view = QTableView()
        self._edit_state = QLabel()
        self._revert_button = QPushButton("放弃改动")
        self._revert_button.setCursor(Qt.PointingHandCursor)
        self._title = QLabel()
        self._stats = QLabel()
        # 右栏：选中行的每一列
        self._detail = QTreeWidget()
        self._empty_hint = QLabel()

        self._build_ui()
        self._connect()

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #

    @property
    def current_table_name(self) -> str:
        """当前**已加载**的表名（"" = 没有表）。

        读的是模型而不是左栏的选中项：左栏的过滤可能把当前这张表滤掉，
        但"正在看哪张表"这件事只有模型知道。
        """
        table = self._model.table
        return table.name if table is not None else ""

    @property
    def model(self) -> TableModel:
        """对外只读暴露，方便嵌入方做额外渲染（如状态列）。"""
        return self._model

    @property
    def table_names_in_order(self) -> tuple[str, ...]:
        """左栏现在列着的表名（**按行数降序**）。用例与冒烟读它。"""
        return tuple(
            self._table_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._table_list.count())
        )

    def set_write_channel(self, channel: TableWriteChannel | None) -> None:
        """接上写通道（外壳注入，见 `table_model.TableWriteChannel`）。

        没接上时表格仍然可看，但**改不了**（`setData` 会说明原因）——
        "改一格只进缓存"这条口径要求写入必须落到那一份账本上。
        行操作（加行 / 复制行 / 删行）与 Ctrl+Z 同理：通道缺哪个口子就停哪个功能，
        不降级成"自己改内存"。
        """
        self._channel = channel
        self._model.set_write_channel(channel)
        self._install_undo_shortcut()
        self._sync_row_actions()
        self._on_edits_changed()

    def set_instance(self, instance: Instance | None) -> None:
        """换数据实例。会重建左栏表列表（按行数降序），并选中行数最多的那一张。"""
        self._instance = instance
        self._table_filter.blockSignals(True)
        self._table_filter.clear()
        self._table_filter.blockSignals(False)
        self._populate_tables()

        first = self._table_list.item(0)
        self.select_table(first.data(Qt.ItemDataRole.UserRole) if first is not None else None)

    def select_table(self, name: str | None) -> None:
        """选中指定表（需已通过 `set_instance` 传入实例）。"""
        if name is None or self._instance is None:
            self._apply_table(None)
            return
        if name not in self._instance.table_names:
            return
        if self.current_table_name != name:
            row = self._list_row_of(name)
            if row < 0:
                # 左栏的过滤把它滤掉了 —— 清掉过滤，保证"跳过去"真的跳得过去
                # （检索命中 / 引用跳转都走这条路）。
                self._table_filter.clear()
                row = self._list_row_of(name)
            if row < 0:
                return
            self._table_list.setCurrentRow(row)
            return
        # 已经在该表上（比如初始化时），直接加载。
        self._load_table(name)

    def set_filter(self, text: str) -> None:
        """代码方设置过滤词（与用户在搜索框里输入等效）。"""
        if self._search.text() == text:
            self._model.set_filter_text(text)
            self._after_filter_change()
            return
        self._search.setText(text)

    def select_row(self, source_row: int) -> None:
        """按**原始**行下标选中一行（会自动重置过滤以保证可见）。"""
        if self._model.is_filtered:
            self.set_filter("")
        for visible in range(self._model.rowCount()):
            if self._model.source_row(visible) == source_row:
                index = self._model.index(visible, 0)
                self._view.setCurrentIndex(index)
                self._view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                return

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self._title.setProperty("role", "table-title")
        self._stats.setProperty("role", "caption")
        self._empty_hint.setProperty("role", "muted")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._edit_state.setProperty("role", "caption")
        self._revert_button.setProperty("variant", "ghost")
        self._revert_button.setEnabled(False)
        self._revert_button.setToolTip(
            "丢弃**本表**还没保存的改动（按账本逐条写回旧值，不重读磁盘）"
        )

        self._configure_table_list()
        self._configure_search()
        self._configure_row_tools()
        self._configure_view()
        self._configure_detail()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_rows_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([_SIDE_PANEL_WIDTH, 760, _SIDE_PANEL_WIDTH])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg
        )
        layout.setSpacing(TOKENS.spacing.md)
        layout.addLayout(self._build_header())
        layout.addWidget(splitter, 1)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(TOKENS.spacing.sm)
        row.addWidget(self._title)
        row.addWidget(self._stats)
        row.addStretch(1)
        row.addWidget(self._edit_state)
        row.addWidget(self._revert_button)
        row.addWidget(self._search)
        return row

    def _build_table_panel(self) -> QWidget:
        """左栏：24 张表（设计稿：`表 › 行 › 列` 的第一栏）。"""
        panel = QWidget()
        panel.setProperty("role", "list-panel")
        panel.setMinimumWidth(240)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.xs, TOKENS.spacing.md
        )
        layout.setSpacing(TOKENS.spacing.xs)

        self._table_caption.setProperty("role", "subtitle")
        layout.addWidget(self._table_caption)
        layout.addWidget(self._table_filter)
        layout.addWidget(self._table_list, 1)
        return panel

    def _build_rows_panel(self) -> QWidget:
        """中栏：行操作一条 + 当前表的行（全列表格）。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(TOKENS.spacing.md, 0, TOKENS.spacing.md, TOKENS.spacing.md)
        layout.setSpacing(TOKENS.spacing.xs)

        tools = QHBoxLayout()
        tools.setSpacing(TOKENS.spacing.sm)
        tools.addWidget(self._add_button)
        tools.addWidget(self._copy_button)
        tools.addWidget(self._delete_button)
        tools.addStretch(1)
        layout.addLayout(tools)
        layout.addWidget(self._view, 1)
        return panel

    def _build_detail_panel(self) -> QWidget:
        """右栏：选中行的每一列（一行一条，与词条面板同一套样式）。"""
        panel = QFrame()
        panel.setProperty("panel", "true")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(
            TOKENS.spacing.xs, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        layout.setSpacing(TOKENS.spacing.xs)

        caption = QLabel("选中行的每一列（序号 │ 中文名 │ 值 │ 英文列名）")
        caption.setProperty("role", "caption")
        layout.addWidget(caption)
        layout.addWidget(self._detail, 1)
        layout.addWidget(self._empty_hint)
        return panel

    def _configure_table_list(self) -> None:
        self._table_list.setProperty("role", "table-list")
        self._table_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table_list.setUniformItemSizes(True)
        self._table_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table_filter.setPlaceholderText(_TABLE_FILTER_HINT)
        self._table_filter.setClearButtonEnabled(True)

    def _configure_search(self) -> None:
        self._search.setPlaceholderText("在本表内过滤（名字 / Id / 任意一格）…")
        self._search.setClearButtonEnabled(True)
        search_font = QFontMetrics(self._search.font())
        self._search.setFixedWidth(
            max(
                240,
                min(
                    420,
                    search_font.horizontalAdvance(self._search.placeholderText() or "x" * 20)
                    + TOKENS.spacing.xxl,
                ),
            )
        )

    def _configure_row_tools(self) -> None:
        """三个行操作按钮的样式与提示。默认置灰 —— 接上通道才亮。"""
        self._add_button.setProperty("role", "row-add")
        self._copy_button.setProperty("variant", "ghost")
        self._delete_button.setProperty("role", "row-action")
        for button in (self._add_button, self._copy_button, self._delete_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setEnabled(False)
        self._sync_row_actions()

    def _configure_view(self) -> None:
        view = self._view
        view.setModel(self._model)
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # 编辑触发：双击，或在选中单元格上直接敲字（EditKeyPressed）。
        # 不用 AnyKeyPressed：那会让方向键/翻页键误开编辑框。
        view.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        view.setItemDelegate(CellDelegate(view))
        view.setWordWrap(False)
        view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.verticalHeader().setDefaultSectionSize(TOKENS.size.row_height)
        # 行号列的宽度按“最大行号有几位”算，不写死。
        # 540 行只需 3 位，但 2357 行需 4 位——写死就会把后者的末位裁掉。
        row_number_font = QFontMetrics(view.font())
        digits = len(str(max(1, self._model.rowCount()))) + 2
        view.verticalHeader().setMinimumWidth(
            row_number_font.horizontalAdvance("0" * digits) + TOKENS.spacing.xl
        )
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        view.horizontalHeader().setHighlightSections(False)

    def _configure_detail(self) -> None:
        detail = self._detail
        detail.setColumnCount(4)
        detail.setHeaderLabels(["#", "字段", "值", "列名"])
        detail.setAlternatingRowColors(True)
        detail.setRootIsDecorated(False)
        detail.setUniformRowHeights(True)
        detail.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        detail.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        detail.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        detail.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        detail.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        detail.setColumnWidth(0, TOKENS.spacing.xxl + TOKENS.spacing.xs)
        detail.setColumnWidth(1, TOKENS.size.sidebar_width // 2)
        detail.setColumnWidth(3, TOKENS.size.sidebar_width // 2)

    def _connect(self) -> None:
        self._table_list.currentItemChanged.connect(self._on_table_item_changed)
        self._table_filter.textChanged.connect(self._on_table_filter_changed)
        self._search.textChanged.connect(self._on_search_changed)
        self._view.selectionModel().currentRowChanged.connect(self._on_current_row_changed)
        self._model.edits_changed.connect(self._on_edits_changed)
        self._revert_button.clicked.connect(self._on_revert_clicked)
        self._view.doubleClicked.connect(self._on_cell_double_clicked)
        self._add_button.clicked.connect(self.add_row)
        self._copy_button.clicked.connect(self.copy_row)
        self._delete_button.clicked.connect(self.delete_row)
        # 右键 = 同一套行操作（工具栏之外再给一个顺手的入口）。
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._open_row_menu)

    # ------------------------------------------------------------------ #
    # 左栏：表列表
    # ------------------------------------------------------------------ #

    def _populate_tables(self) -> None:
        """重建左栏。**按行数降序**（设计稿 块 9），并尽量保持当前选中项。"""
        keep = self.current_table_name
        names = self._filtered_table_names()

        self._table_list.blockSignals(True)
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._table_list.count():
            self._table_list.takeItem(0)
        for name in names:
            table = self._instance.table(name) if self._instance is not None else None
            item = QListWidgetItem(
                f"{table_emoji(name)} {table_title(name)}　{name}　"
                f"{table.row_count}×{table.column_count}"
                if table is not None
                else name
            )
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(
                f"{table_title(name)}（{name}）"
                + (f"\n{table.row_count} 行 × {table.column_count} 列" if table else "")
            )
            self._table_list.addItem(item)
        self._table_list.setCurrentRow(-1)
        self._table_list.blockSignals(False)

        self._table_list.setEnabled(self._instance is not None)
        self._table_filter.setEnabled(self._instance is not None)
        self._table_caption.setText(
            f"数据表 {len(names)} 张" if self._instance is not None else "数据表"
        )

        row = self._list_row_of(keep) if keep else -1
        if row >= 0:
            self._table_list.blockSignals(True)
            self._table_list.setCurrentRow(row)
            self._table_list.blockSignals(False)

    def _filtered_table_names(self) -> tuple[str, ...]:
        """左栏要列的表名：**行数降序**，再按过滤词筛（中文名与英文名都认）。"""
        if self._instance is None:
            return ()
        ordered = sorted(
            self._instance.table_names,
            key=lambda name: (-self._instance.table(name).row_count, name),
        )
        needle = self._table_filter.text().strip().casefold()
        if not needle:
            return tuple(ordered)
        return tuple(
            name
            for name in ordered
            if needle in name.casefold() or needle in table_title(name).casefold()
        )

    def _list_row_of(self, name: str) -> int:
        """表名 -> 左栏第几项；不在列表里（被过滤掉了 / 没有）返回 -1。"""
        for row in range(self._table_list.count()):
            if self._table_list.item(row).data(Qt.ItemDataRole.UserRole) == name:
                return row
        return -1

    def _on_table_filter_changed(self, _text: str) -> None:
        self._populate_tables()

    def _on_table_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        name = current.data(Qt.ItemDataRole.UserRole) if current is not None else ""
        self._load_table(name or "")

    # ------------------------------------------------------------------ #
    # 响应
    # ------------------------------------------------------------------ #

    def _load_table(self, name: str) -> None:
        if self._instance is None or not name:
            self._apply_table(None)
            return
        table = self._instance.table(name)
        self._apply_table(table)
        self.table_changed.emit(name)

    def _apply_table(self, table: Table | None) -> None:
        self._model.set_table(table)
        self._row_count = table.row_count if table is not None else 0

        if table is None:
            self._title.setText("未打开数据源")
            self._stats.setText("")
            self._empty_hint.setText("尚未加载数据源")
            self._empty_hint.setVisible(True)
            self._detail.setVisible(False)
            self._search.setEnabled(False)
            self._autosize_columns()
            self._sync_row_actions()
            return

        self._title.setText(f"{table_title(table.name)} {table.name}")
        self._search.setEnabled(True)
        self._detail.setVisible(True)
        self._empty_hint.setVisible(False)
        self._update_stats()
        self._autosize_columns()
        self._model.set_filter_text("")
        self._search.clear()
        self._show_row_detail(None)
        self._sync_row_actions()
        self._on_edits_changed()

    def _on_search_changed(self, text: str) -> None:
        self._model.set_filter_text(text)
        self._after_filter_change()

    def _after_filter_change(self) -> None:
        self._update_stats()
        self._show_row_detail(None)
        self._sync_row_actions()

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self._sync_row_actions()
        if not current.isValid():
            self._show_row_detail(None)
            self.row_selected.emit(-1)
            return
        source_row = self._model.source_row(current.row())
        self._show_row_detail(source_row)
        self.row_selected.emit(source_row)

    def _on_edits_changed(self) -> None:
        """账本变了：刷新本表的改动状态与列面板。

        ⚠️ 这里的数字**读的是同一份账本**（`TableModel.dirty_count` ->
        `EditSession`），不是页面自己记的一套 —— AGENTS §4.5：
        页面不许再自己记「已改 N 处」。顶栏胶囊才是全局的唯一指示，
        这里那句只说**本表**（「放弃改动」按钮的作用域就是本表）。
        """
        count = self._model.dirty_count
        if count:
            self._edit_state.setText(f"本表 {count} 处未保存")
            self._revert_button.setEnabled(True)
        else:
            self._edit_state.setText("")
            self._revert_button.setEnabled(False)
        # 改完当前行后，列面板必须跟着变——否则展示的是旧值。
        self._refresh_detail()

    def refresh_dirty_state(self) -> None:
        """外壳的账本变了（别处也在改）：把本表重画一遍并跟上状态。

        数据表页不是唯一的写入口（单位编辑器 / 国家页 / 专精页改的是同一份账本），
        所以"账本变了"这件事必须由外壳广播进来，不能只靠本页自己的编辑动作。
        **行数也可能被别处改掉**（整单位克隆一次插几十行），所以这里顺手对一下行数 ——
        不然可见行映射停在旧行数上，再点这一页就会画到不存在的行上。
        """
        table = self._model.table
        if table is not None and table.row_count != self._row_count:
            self._after_row_count_change()
        self._model.refresh_dirty()
        self._update_stats()
        self._sync_row_actions()
        self._on_edits_changed()

    def _refresh_detail(self) -> None:
        current = self._view.currentIndex()
        if current.isValid():
            self._show_row_detail(self._model.source_row(current.row()))

    def _on_revert_clicked(self) -> None:
        """「放弃改动」= 丢掉**本表**在账本里的改动（逐条写回旧值，不重读磁盘）。"""
        reverted = self._model.revert_all()  # 行级改动也在里面 -> 行数可能变
        self._row_count = self._model.table.row_count if self._model.table else 0
        self._populate_tables()  # 左栏那个「行数×列数」跟着变
        self._update_stats()
        self._sync_row_actions()
        if reverted:
            self._edit_state.setText(f"已放弃本表 {reverted} 处改动")
            self._revert_button.setEnabled(False)

    def _on_cell_double_clicked(self, index: QModelIndex) -> None:
        """双击一个单元格后，把聚焦交给编辑框(Qt 已开始编辑，这里只需保证选中）。"""
        if index.isValid():
            self.row_selected.emit(self._model.source_row(index.row()))

    def select_cell(self, source_row: int, column_name: str) -> bool:
        """跳到指定单元格（供全局检索结果跳转）。

        返回 `False` 表示列名不存在。会自动清掉表内过滤以保证目标可见。
        """
        table = self._model.table
        if table is None:
            return False
        try:
            column = table.column_index(column_name)
        except KeyError:
            return False

        if self._model.is_filtered:
            self.set_filter("")
        self.select_row(source_row)
        index = self._view.currentIndex()
        if index.isValid():
            self._view.setCurrentIndex(self._model.index(index.row(), column))
            self._view.scrollTo(
                self._model.index(index.row(), column),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
        return True

    # ------------------------------------------------------------------ #
    # 行操作（加行 / 复制行 / 删行）—— 全部走写通道 -> 同一份账本
    # ------------------------------------------------------------------ #

    def add_row(self) -> bool:
        """＋ 加行：往当前表插一行 —— **照抄该表第一行的值、只换 Id**。

        为什么是"照抄第一行"而不是"插一行全空"：见模块 docstring 的口径第 1 条。
        表里一行都没有（空表）时没有可抄的，只能给全列 `None`。
        返回 `True` 表示插进去了（界面与状态栏都已跟上）。
        """
        table = self._model.table
        if table is None:
            self.status_message.emit("还没打开数据源，加不了行")
            return False
        if not self._row_actions_available(table):
            return False

        had_rows = table.row_count > 0
        values = self._model.row_values(0) if had_rows else {}
        new_id = self._insert_values(values)
        if new_id is None:  # pragma: no cover - 上面守过通道
            return False
        note = "照抄第一行的值，只换 Id" if had_rows else "空表：整行都是空值"
        self.status_message.emit(f"已新增 {table.name} 行 #{new_id}（{note}）· Ctrl+Z 可撤")
        return True

    def copy_row(self) -> bool:
        """⧉ 复制行：复制**选中那一行** —— 内容照抄、Id 换一个（见模块 docstring）。"""
        table = self._model.table
        if table is None:
            self.status_message.emit("还没打开数据源，复制不了行")
            return False
        if not self._row_actions_available(table):
            return False
        source = self._selected_source_row()
        if source is None:
            self.status_message.emit("先在中间选一行，再点「复制行」")
            return False

        source_id = row_id_of(table, source)
        new_id = self._insert_values(self._model.row_values(source))
        if new_id is None:  # pragma: no cover - 上面守过通道
            return False
        self.status_message.emit(
            f"已复制 {table.name}#{source_id} → #{new_id}（内容照抄，只换 Id）· Ctrl+Z 可撤"
        )
        return True

    def delete_row(self) -> bool:
        """🗑 删行：删掉**选中那一行**。不弹确认框，被谁引用写进状态栏。

        「被谁引用」在**删之前**问（反查口径只有一份：`core.edit.refs.referencing_rows`），
        数量写进那句话 —— 删一个被 142 个单位共用的模板行，用户该当场知道，
        而不是回头发现一片单位都变了。
        """
        table = self._model.table
        if table is None:
            self.status_message.emit("还没打开数据源，删不了行")
            return False
        if not self._row_actions_available(table):
            return False
        source = self._selected_source_row()
        if source is None:
            self.status_message.emit("先在中间选一行，再点「删行」")
            return False
        row_id = row_id_of(table, source)
        if row_id is None:  # pragma: no cover - 上面守过 Id 列
            self.status_message.emit(f"{table.name} 这一行没有整数 Id，删不了")
            return False

        hits = self._referencing_rows(table.name, row_id)
        channel = self._channel
        if channel is None or channel.delete_row is None:  # pragma: no cover - 上面守过
            return False
        visible = self._visible_row_of(source)
        channel.delete_row(table.name, row_id)

        self._after_row_count_change()
        if self._model.rowCount():
            self.select_row(self._model.source_row(min(visible, self._model.rowCount() - 1)))
        else:
            self._show_row_detail(None)
            self._sync_row_actions()
        self._on_edits_changed()

        referenced = f"有 {len(hits)} 行引用它" if hits else "没有别的行引用它"
        self.status_message.emit(f"已删除 {table.name}#{row_id}（{referenced}）· Ctrl+Z 可撤")
        return True

    def _insert_values(self, values: dict[str, Any]) -> int | None:
        """插一行并刷新界面：**Id 交给外壳分配**（见模块 docstring 口径第 2 条）。"""
        table = self._model.table
        channel = self._channel
        if table is None or channel is None or channel.insert_row is None:
            return None
        payload = dict(values)
        # ❗ 一律置 None：Id 由外壳走 add_row -> next_row_id(表, 账本) 分配。
        #   本控件手上没有账本，"本会话删过的 Id 不复用"这条就实现不了。
        payload[ID_COLUMN] = None
        new_id = channel.insert_row(table.name, payload)

        self._after_row_count_change()
        row = self._model.row_of_id(new_id)
        if row is not None:
            self.select_row(row)  # 新行在末尾，选中它让用户当场看见
        else:  # pragma: no cover - 刚插进去的行不可能找不到
            self._sync_row_actions()
        self._on_edits_changed()
        return new_id

    def _after_row_count_change(self) -> None:
        """行数变了（插 / 删 / 撤销）：把**所有**跟着行数走的东西刷新一遍。

        哪些东西跟着行数走，一处都别落：

        - 可见行映射（`reload_rows`）—— 不重建就会画到不存在的行上；
        - 左栏表名后面那个「行数×列数」与中栏的「N 行」；
        - 列宽（新行的值可能更长）。
        """
        self._model.reload_rows()
        self._row_count = self._model.table.row_count if self._model.table else 0
        self._populate_tables()
        self._update_stats()
        self._autosize_columns()

    def _row_actions_available(self, table: Table) -> bool:
        """行操作能不能做。不能就**说明原因**（状态栏，不弹窗）。"""
        if self._channel is None or not self._channel.can_edit_rows:
            self.status_message.emit(
                "这个入口没接上行操作（加行 / 复制行 / 删行）—— 见 TableWriteChannel"
            )
            return False
        if not self._model.has_id_column:
            self.status_message.emit(
                f"{table.name} 没有整数 Id 列；行操作按 Id 寻址（行下标会被插行挤位移）"
            )
            return False
        return True

    def _sync_row_actions(self) -> None:
        """三个按钮的可用性 + 提示。**没接通道就置灰**，不假装能改。"""
        wired = self._channel is not None and self._channel.can_edit_rows
        has_table = self._model.table is not None and self._model.has_id_column
        has_row = self._view.currentIndex().isValid()
        why = "" if wired else "（这个入口没接上行操作）"

        self._add_button.setEnabled(wired and has_table)
        self._copy_button.setEnabled(wired and has_table and has_row)
        self._delete_button.setEnabled(wired and has_table and has_row)
        self._add_button.setToolTip(
            f"往当前表插一行：照抄第一行的值、只换 Id；不弹确认框，Ctrl+Z 可撤{why}"
        )
        self._copy_button.setToolTip(
            f"复制选中的那一行：内容照抄、只换 Id；不弹确认框，Ctrl+Z 可撤{why}"
        )
        self._delete_button.setToolTip(
            "删掉选中的那一行（被谁引用看底部那块面板）；不弹确认框，Ctrl+Z 可撤" + why
        )

    def _selected_source_row(self) -> int | None:
        index = self._view.currentIndex()
        if not index.isValid() or self._model.table is None:
            return None
        return self._model.source_row(index.row())

    def _visible_row_of(self, source_row: int) -> int:
        """原始行下标 -> 可视行下标（找不到就给 0，删除后要挑个邻居选中）。"""
        for visible in range(self._model.rowCount()):
            if self._model.source_row(visible) == source_row:
                return visible
        return 0

    def _referencing_rows(self, table: str, row_id: int) -> tuple[RefHit, ...]:
        """谁引用了这一行。**反查口径只有一份**（core.edit.refs.referencing_rows）。"""
        if self._instance is None:
            return ()
        return tuple(referencing_rows(self._instance, table, row_id))

    def _row_menu(self) -> QMenu:
        """右键菜单：**与工具栏那三颗一一对应**（可用性也照抄）。

        单独一个方法：用例要能查"菜单里是哪三项、亮不亮"，而不必真的弹出菜单
        （`exec` 是阻塞的，离屏平台上也弹不出来）。
        """
        menu = QMenu(self)
        for button in (self._add_button, self._copy_button, self._delete_button):
            action = menu.addAction(button.text().strip())
            action.setEnabled(button.isEnabled())
            action.setToolTip(button.toolTip())
            action.triggered.connect(lambda _checked=False, target=button: target.click())
        return menu

    def _open_row_menu(self, pos) -> None:
        """中栏右键：同一套行操作（工具栏那三颗的镜像）。"""
        index = self._view.indexAt(pos)
        if index.isValid():
            self._view.setCurrentIndex(index)
            self._sync_row_actions()
        self._row_menu().exec(self._view.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ #
    # Ctrl+Z（装在控件自己身上，见模块 docstring 口径第 4 条）
    # ------------------------------------------------------------------ #

    def _install_undo_shortcut(self) -> None:
        """接上 `undo_last` 才装 Ctrl+Z；没接就**不装**（装了没得撤更困惑）。"""
        wired = self._channel is not None and self._channel.undo_last is not None
        if wired and self._undo_shortcut is None:
            shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), self)
            # ❗ WidgetWithChildrenShortcut：只在**本控件**（含子控件）聚焦时生效 ——
            #    窗口级那条会与单位编辑器里的 Ctrl+Z 撞成 ambiguous shortcut。
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._undo_last_change)
            self._undo_shortcut = shortcut
        elif not wired and self._undo_shortcut is not None:
            self._undo_shortcut.setEnabled(False)
            self._undo_shortcut.deleteLater()
            self._undo_shortcut = None

    @property
    def has_undo_shortcut(self) -> bool:
        """Ctrl+Z 装了没有（用例读它；没接 undo_last 时就该是 False）。"""
        return self._undo_shortcut is not None

    def _undo_last_change(self) -> None:
        """撤销最近一条改动（栈在外壳那份账本里），并跟上界面。"""
        channel = self._channel
        if channel is None or channel.undo_last is None:
            return
        table = self._model.table
        before = table.row_count if table is not None else -1
        keep_id = self._selected_row_id()
        message = channel.undo_last()
        after = table.row_count if table is not None else -1

        if after != before:
            # 行回来了 / 被拿掉了：可见行映射必须重建，否则会画到不存在的行上。
            self._after_row_count_change()
            row = self._model.row_of_id(keep_id) if keep_id is not None else None
            if row is not None:
                self.select_row(row)
            else:
                self._sync_row_actions()
        else:
            self._model.refresh_dirty()
        self._update_stats()
        self._on_edits_changed()
        if message:
            self.status_message.emit(message)

    def _selected_row_id(self) -> int | None:
        """选中那一行的 Id（撤销之后要把选中挪回来）。"""
        table = self._model.table
        source = self._selected_source_row()
        if table is None or source is None:
            return None
        return row_id_of(table, source)

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #

    def _update_stats(self) -> None:
        table = self._model.table
        if table is None:
            self._stats.setText("")
            return

        parts = [f"{table.row_count} 行", f"{table.column_count} 列"]
        if self._model.is_filtered:
            parts.append(f"命中 {self._model.rowCount()} 行")
        self._stats.setText(" · ".join(parts))

    def _autosize_columns(self) -> None:
        """按前 `_AUTOSIZE_SAMPLE_ROWS` 行的内容算列宽。

        不用 `resizeColumnsToContents()`：它会扫全表，630×67 时明显卡顿。
        """
        table = self._model.table
        if table is None:
            return

        metrics = QFontMetrics(self._view.font())
        min_width, max_width = _column_width_bounds(metrics)
        header = self._view.horizontalHeader()
        sample = min(self._model.rowCount(), _AUTOSIZE_SAMPLE_ROWS)

        for column in range(table.column_count):
            width = metrics.horizontalAdvance(table.columns[column]) + TOKENS.spacing.xxl
            for row in range(sample):
                text = self._model.display_text(row, column)
                if not text:
                    continue
                width = max(width, metrics.horizontalAdvance(text) + TOKENS.spacing.lg)
            header.resizeSection(column, max(min_width, min(max_width, width)))

        # 对二进制坐标随机取几列看看宽度分布
        self._log_width_sample(table)

    def _log_width_sample(self, table: Table) -> None:
        """调试用：打印列宽分布，方便评估自适是否合理。"""
        if not _DEBUG_WIDTHS:
            return
        header = self._view.horizontalHeader()
        widths = [header.sectionSize(i) for i in range(table.column_count)]
        print(f"[{table.name}] 列宽：最小={min(widths)} 最大={max(widths)} 合计={sum(widths)}")

    def _show_row_detail(self, source_row: int | None) -> None:
        """右栏：选中那一行的每一列（序号 │ 中文名 │ 值 │ 英文列名）。

        与 `row_entries.RowEntriesPanel` 同一套行样式；这里**只读** ——
        同一个值不给两个编辑入口，类型化编辑在中栏（CellDelegate）。
        """
        self._detail.clear()
        table = self._model.table
        if table is None or source_row is None:
            self._empty_hint.setText("选中一行以查看全部列" if table is not None else "")
            self._empty_hint.setVisible(table is not None)
            return

        self._empty_hint.setVisible(False)
        row = table.rows[source_row]
        for column_index, column_name in enumerate(table.columns):
            value = row[column_index]
            text = cell_text(value)
            item = QTreeWidgetItem(
                [
                    f"{column_index + 1:02d}",
                    _column_title(table.name, column_name),
                    text,
                    column_name,
                ]
            )
            item.setToolTip(1, f"{column_name}（第 {column_index + 1} 列）")
            item.setToolTip(2, text)
            item.setToolTip(3, column_name)
            if value is None:
                item.setForeground(2, _MUTED_BRUSH)
            item.setForeground(3, _MUTED_BRUSH)
            self._detail.addTopLevelItem(item)
