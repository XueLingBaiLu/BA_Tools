"""字段词典窗口（☰ 菜单里那个「📖 字段词典…」）。

旧产品有一个词典窗口（24 张表、每个字段的含义、外键与枚举对照，可搜索）；
新产品此前只有行内的悬浮提示 —— 想看"这一列到底是什么意思"必须先把鼠标停在某一格上。
这个窗口把那份知识**摊开**：左栏选表、右上逐列看、右下看这一列的取值长什么样。

## 版面（照 `docs/设计-界面重做-定稿.md` 的三栏口径）

    ┌ 搜索框 ─────────────────────────────────────────────┐
    │ 表（24）  │ 该表每一列：中文名 │ 列名 │ 类型 │ 说明    │
    │ 📦 单位…  ├──────────────────────────────────────────┤
    │ 🔫 武器…  │ 选中列：位置 / 说明 / 取值观测 / 取值对照  │
    └───────────┴──────────────────────────────────────────┘

**左栏只列 24 张表**，不做成"一堵墙"（用户口径：卡墙只留给军械库那一屏）。

## 三件事刻意不做

1. **不写数据**：这是只读窗口，一个字节都不落盘（写路径仍然只有 EditSession 那一处）；
2. **不另抄词典**：中文名 / 说明 / 枚举值域全部走 `core/edit/dictionary.py`，
   中文表名与 emoji 走 `app/screens/editor/entries_panel.py`（那两个函数是唯一来源）；
3. **不编**：两份词典都没有的列，写「词典未收录」并照样把英文列名与取值观测列出来 ——
   "这里有个缺口"本身就是有用的信息，假装它有中文名才是错的。

数据层在 `dictionary_data.py`（零 Qt），本文件只负责摆控件与接线。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.dialogs.dictionary_data import (
    ColumnEntry,
    TableEntry,
    column_entries,
    filter_tables,
    first_table_name,
    table_entries,
    table_size,
    visible_columns,
)
from ba_mod_maker.app.screens.editor.entries_panel import table_emoji, table_title
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core.data.dataset import Instance

#: 窗口初始尺寸（逻辑像素）。词典是"查东西"的窗口，宽一点才不用横向滚。
WINDOW_WIDTH = 1180
WINDOW_HEIGHT = 720

#: 左栏宽度：只放"emoji + 中文表名"，够用即可。
TABLE_LIST_WIDTH = 220

#: 右下那两块的最小高度，免得被上面的列清单挤没。
DETAIL_MIN_HEIGHT = 200

#: 搜索框的提示语 —— 三样能搜的东西就写在这句里（写全了用户才知道能搜什么）。
SEARCH_PLACEHOLDER = "搜表名 / 中文名 / 英文列名…"

#: 悬浮提示：让用户知道"这个英文列名才是写回文件时用的那个"。
ENGLISH_HINT = "英文列名（表里那一列就叫这个名字）"


class DictionaryWindow(QDialog):
    """只读的字段词典：左选表、右上逐列、右下看取值。"""

    def __init__(self, parent: QWidget | None = None, *, instance: Instance | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("📖 字段词典")
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        #: 数据源（可空：没有它也能用 —— 列清单与说明来自词典文件本身）。
        self._instance: Instance | None = None
        self._tables: tuple[TableEntry, ...] = ()
        self._columns: tuple[ColumnEntry, ...] = ()
        #: 程序自己改选中项时置真，避免"填列表"把"刷新右栏"再触发一遍。
        self._syncing = False

        self._build()
        self.set_instance(instance)

    # ------------------------------------------------------------------ #
    # 对外接口（Lead 定死的两个，别改签名）
    # ------------------------------------------------------------------ #

    def set_instance(self, instance: Instance | None) -> None:
        """换数据源：有实例就带上取值观测，没有就只显示词典本身。"""
        self._instance = instance
        self._reload_tables()

    # ------------------------------------------------------------------ #
    # 版面
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.lg, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.sm)

        root.addLayout(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_detail_split())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        root.addLayout(self._build_footer())

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(TOKENS.spacing.sm)

        title = QLabel("📖 字段词典")
        title.setProperty("role", "table-title")
        row.addWidget(title)

        self._search = QLineEdit()
        self._search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search, 1)

        self._count = QLabel("")
        self._count.setProperty("role", "caption")
        row.addWidget(self._count)
        return row

    def _build_table_panel(self) -> QWidget:
        panel = QWidget()
        panel.setProperty("role", "list-panel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.xs)

        self._table_list = QListWidget()
        self._table_list.setProperty("role", "table-list")
        self._table_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table_list.setUniformItemSizes(True)
        self._table_list.currentRowChanged.connect(self._on_table_changed)
        box.addWidget(self._table_list, 1)

        self._table_list.setMinimumWidth(TABLE_LIST_WIDTH)
        return panel

    def _build_detail_split(self) -> QSplitter:
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._build_column_tree())
        split.addWidget(self._build_column_detail())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        return split

    def _build_column_tree(self) -> QTreeWidget:
        self._column_tree = QTreeWidget()
        self._column_tree.setHeaderLabels(["中文名", "列名", "类型", "说明"])
        self._column_tree.setRootIsDecorated(False)
        self._column_tree.setUniformRowHeights(True)
        self._column_tree.setAlternatingRowColors(True)
        self._column_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._column_tree.currentItemChanged.connect(self._on_column_changed)

        header = self._column_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._column_tree.setColumnWidth(0, TOKENS.size.sidebar_width // 2)
        self._column_tree.setColumnWidth(1, TOKENS.size.sidebar_width // 2)
        self._column_tree.setColumnWidth(2, TOKENS.size.sidebar_width // 2)
        return self._column_tree

    def _build_column_detail(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.xs, TOKENS.spacing.md, TOKENS.spacing.xs
        )
        box.setSpacing(TOKENS.spacing.xxs)

        self._column_title = QLabel("")
        self._column_title.setProperty("role", "table-title")
        box.addWidget(self._column_title)

        self._column_place = QLabel("")
        self._column_place.setProperty("role", "mono")
        box.addWidget(self._column_place)

        self._column_doc = QLabel("")
        self._column_doc.setProperty("role", "muted")
        self._column_doc.setWordWrap(True)
        box.addWidget(self._column_doc)

        self._column_stats = QLabel("")
        self._column_stats.setProperty("role", "caption")
        self._column_stats.setWordWrap(True)
        box.addWidget(self._column_stats)

        self._value_tree = QTreeWidget()
        self._value_tree.setHeaderLabels(["取值", "含义"])
        self._value_tree.setRootIsDecorated(True)
        self._value_tree.setUniformRowHeights(True)
        self._value_tree.setAlternatingRowColors(True)
        self._value_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        value_header = self._value_tree.header()
        value_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        value_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._value_tree.setColumnWidth(0, TOKENS.size.sidebar_width // 2)
        box.addWidget(self._value_tree, 1)
        panel.setMinimumHeight(DETAIL_MIN_HEIGHT)
        return panel

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(TOKENS.spacing.sm)
        hint = QLabel("只读：这里只解释列与取值，改值仍在单位编辑器 / 数据表页。")
        hint.setProperty("role", "muted")
        row.addWidget(hint)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        return row

    # ------------------------------------------------------------------ #
    # 填数据
    # ------------------------------------------------------------------ #

    def _reload_tables(self) -> None:
        keep = self.selected_table()
        self._tables = table_entries(self._instance, title_of=table_title, emoji_of=table_emoji)
        self._fill_table_list(filter_tables(self._tables, self._search.text()), keep=keep)
        self._reload_columns()

    def _fill_table_list(self, entries: tuple[TableEntry, ...], *, keep: str | None) -> None:
        self._syncing = True
        try:
            # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
            #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
            while self._table_list.count():
                self._table_list.takeItem(0)
            for entry in entries:
                item = QListWidgetItem(entry.label)
                item.setData(Qt.ItemDataRole.UserRole, entry.name)
                item.setToolTip(f"{entry.title}（{entry.name}）")
                self._table_list.addItem(item)
            wanted = first_table_name(entries, keep)
            if wanted is not None:
                self._select_table_item(wanted)
        finally:
            self._syncing = False

    def _select_table_item(self, name: str) -> None:
        for row in range(self._table_list.count()):
            item = self._table_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self._table_list.setCurrentRow(row)
                return
        self._table_list.setCurrentRow(-1)

    def selected_table(self) -> str | None:
        """当前选中的表名（没选就是 None）。"""
        item = self._table_list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return name if isinstance(name, str) else None

    def _reload_columns(self) -> None:
        name = self.selected_table()
        self._columns = column_entries(name, self._instance) if name else ()
        visible = visible_columns(self._columns, self._search.text())
        self._fill_column_tree(visible)
        self._count.setText(self._count_text(len(visible)))

    def _count_text(self, visible: int) -> str:
        name = self.selected_table()
        if name is None:
            return f"{len(self._tables)} 张表（没有匹配）"
        size = table_size(self._instance, name)
        parts = [f"{len(self._tables)} 张表", f"{name} {visible}/{len(self._columns)} 列"]
        if size is not None:
            parts.append(f"{size[0]} 行 × {size[1]} 列")
        return " · ".join(parts)

    def _fill_column_tree(self, entries: tuple[ColumnEntry, ...]) -> None:
        self._syncing = True
        try:
            # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
            #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
            while self._column_tree.topLevelItemCount():
                self._column_tree.takeTopLevelItem(0)
            for entry in entries:
                item = QTreeWidgetItem([entry.title, entry.column, entry.type_text, entry.doc])
                item.setData(0, Qt.ItemDataRole.UserRole, entry.column)
                item.setToolTip(1, ENGLISH_HINT)
                self._column_tree.addTopLevelItem(item)
            if entries:
                self._column_tree.setCurrentItem(self._column_tree.topLevelItem(0))
        finally:
            self._syncing = False
        self._show_column()

    def _current_column(self) -> ColumnEntry | None:
        item = self._column_tree.currentItem()
        if item is None:
            return None
        column = item.data(0, Qt.ItemDataRole.UserRole)
        for entry in self._columns:
            if entry.column == column:
                return entry
        return None

    def _show_column(self) -> None:
        entry = self._current_column()
        if entry is None:
            self._column_title.setText("（没有选中列）")
            self._column_place.setText("")
            self._column_doc.setText(
                "左栏选一张表；上面那一列的中文名 / 英文名 / 类型 / 说明都在这里。"
            )
            self._column_stats.setText("")
            # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
            while self._value_tree.topLevelItemCount():
                self._value_tree.takeTopLevelItem(0)
            return

        self._column_title.setText(f"{entry.title}　{entry.column}")
        place = f"{entry.table}.{entry.column}"
        if entry.foreign:
            place = f"{place}　→ {entry.foreign}"
        self._column_place.setText(place)
        self._column_doc.setText(entry.doc or "（词典没有写这一列的说明）")
        self._column_stats.setText(self._stats_text(entry))
        self._fill_value_tree(entry)

    def _stats_text(self, entry: ColumnEntry) -> str:
        if entry.stats is None:
            return "取值观测：要打开数据源才有（现在只显示产品自带的词典）。"
        return f"取值观测（本实例）：{entry.stats.summary}"

    def _fill_value_tree(self, entry: ColumnEntry) -> None:
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._value_tree.topLevelItemCount():
            self._value_tree.takeTopLevelItem(0)
        if entry.values:
            group = QTreeWidgetItem([f"取值对照（{len(entry.values)} 档）", ""])
            for raw, text in entry.values:
                group.addChild(QTreeWidgetItem([raw, text]))
            self._value_tree.addTopLevelItem(group)
            group.setExpanded(True)
        if entry.stats is not None and entry.stats.samples:
            samples = QTreeWidgetItem(["样例（出现最多的几个）", "解出来的意思"])
            for raw in entry.stats.samples:
                decoded = entry.decode_text(raw)
                samples.addChild(QTreeWidgetItem([raw, "" if decoded == raw else decoded]))
            self._value_tree.addTopLevelItem(samples)
            samples.setExpanded(True)
        if self._value_tree.topLevelItemCount() == 0:
            self._value_tree.addTopLevelItem(
                QTreeWidgetItem(["（词典没有登记取值）", "按原始值原样显示"])
            )

    # ------------------------------------------------------------------ #
    # 响应
    # ------------------------------------------------------------------ #

    def _on_search_changed(self, _text: str = "") -> None:
        """搜索框一变：表清单与列清单**同时**过滤（一处输入、两处收窄）。"""
        self._fill_table_list(
            filter_tables(self._tables, self._search.text()), keep=self.selected_table()
        )
        self._reload_columns()

    def _on_table_changed(self, _row: int = -1) -> None:
        if self._syncing:
            return
        self._reload_columns()

    def _on_column_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        self._show_column()


__all__ = ["SEARCH_PLACEHOLDER", "WINDOW_HEIGHT", "WINDOW_WIDTH", "DictionaryWindow"]
