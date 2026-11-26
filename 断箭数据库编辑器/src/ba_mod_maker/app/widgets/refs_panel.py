"""「被谁引用」面板：数据表页底部那块**只读**的引用清单。

选中一行时列出**谁引用了它**（外键反查）。这是"改一个共享行就是改一大片"
（设计 §6：645 的传感器被 142 个单位共用）在界面上唯一的出口 ——
不看这一眼就改，等于闭着眼睛动别人的数据。

三件事刻意不做：

1. **只读**：这里一个字节都不写（写路径只有 `EditSession` 一处）；
2. **不自己实现反查**：口径只有一份 —— `core.edit.refs.referencing_rows()`，
   本面板只负责"把结果画出来、双击跳过去"；
3. **不猜**：反查还没接上时如实写一句"还没接上"，不假装"没有引用者"
   （"没有"与"不知道"是两回事，界面说错比不说更糟）。

数据由外面通过 `show_hits()` 传进来，本控件不读磁盘、不认识 EditSession ——
与包里其它控件同一个契约（见 `app/widgets/__init__.py`）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.tokens import TOKENS

#: 面板固定高度：它挂在数据表页最底下，不该抢表格的地方。
PANEL_HEIGHT = 148

#: 还没选中任何行时的提示。
NOTHING_SELECTED = "选中一行，这里列出「谁引用了它」（双击一条就跳过去）"

#: 反查还没接上时的提示（T1 交付 `core.edit.refs.referencing_rows` 之前就这一句）。
UNAVAILABLE = "引用反查还没接上（core.edit.refs.referencing_rows）——不猜，先如实说"

#: 接上了、但确实没有别人引用。
NO_HITS = "没有别的行引用这一行"

#: 四列：表 │ Id │ 列 │ 名称。
_COLUMNS = ("表", "Id", "列", "名称")

#: 一条引用上挂着 (表名, 行 Id)，双击时用它。
_HIT_ROLE = Qt.ItemDataRole.UserRole


class ReferencedByPanel(QFrame):
    """只读的「被谁引用」清单。

    对外入口：`show_hits()` / `show_unavailable()` / `clear()`；
    对外信号：`hit_activated(表名, 行 Id)`（双击某一条）。
    """

    #: 双击一条引用 -> 跳到那张表的那一行。
    hit_activated = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = QLabel()
        self._hint = QLabel()
        self._tree = QTreeWidget()
        self._build()
        self.clear()

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """没选中行（或换了表）：回到"选中一行试试"的初始样子。"""
        self._caption.setText("🔗 被谁引用")
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._tree.topLevelItemCount():
            self._tree.takeTopLevelItem(0)
        self._set_hint(NOTHING_SELECTED)

    def show_unavailable(self) -> None:
        """反查还没有实现：如实说，**不**把它画成"没有引用者"。"""
        self._caption.setText("🔗 被谁引用")
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._tree.topLevelItemCount():
            self._tree.takeTopLevelItem(0)
        self._set_hint(UNAVAILABLE)

    def show_hits(self, table: str, row_id: int, hits: Iterable[Any]) -> None:
        """把反查结果画出来。`hits` 是 `core.edit.refs.RefHit` 的可迭代。

        每个 hit 要四样：`table` / `row_id` / `column` / `label`（口径见
        `core.edit.refs.referencing_rows` 的签名）。
        """
        items = tuple(hits)
        self._caption.setText(f"🔗 被谁引用 · {table} #{row_id}（{len(items)} 条）")
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._tree.topLevelItemCount():
            self._tree.takeTopLevelItem(0)
        for hit in items:
            item = QTreeWidgetItem(
                [
                    str(hit.table),
                    f"#{hit.row_id}",
                    str(hit.column),
                    str(hit.label) if hit.label else "（没有名字）",
                ]
            )
            item.setData(0, _HIT_ROLE, (str(hit.table), int(hit.row_id)))
            item.setToolTip(0, f"跳到 {hit.table} #{hit.row_id}")
            self._tree.addTopLevelItem(item)
        self._set_hint("" if items else NO_HITS)

    def activate_current(self) -> bool:
        """打开当前选中的那一条（双击与用例走同一条路）。返回是否发出去了。"""
        item = self._tree.currentItem()
        if item is None:
            return False
        payload = item.data(0, _HIT_ROLE)
        if not payload:
            return False
        table, row_id = payload
        self.hit_activated.emit(str(table), int(row_id))
        return True

    @property
    def hit_count(self) -> int:
        """现在画着几条引用（用例读它，比数控件稳）。"""
        return self._tree.topLevelItemCount()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        self.setProperty("panel", "true")
        self.setFixedHeight(PANEL_HEIGHT)

        self._caption.setProperty("role", "caption")
        self._hint.setProperty("role", "muted")
        self._hint.setWordWrap(True)

        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(list(_COLUMNS))
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        # 只读：不许编辑、不许就地改（这块是"给你看"的，不是第二个编辑面）。
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._tree.setColumnWidth(0, TOKENS.size.sidebar_width // 2)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.xs, TOKENS.spacing.md, TOKENS.spacing.xs
        )
        layout.setSpacing(TOKENS.spacing.xxs)
        layout.addWidget(self._caption)
        layout.addWidget(self._tree, 1)
        layout.addWidget(self._hint)

    def _set_hint(self, text: str) -> None:
        self._hint.setText(text)
        self._hint.setVisible(bool(text))

    def _on_double_clicked(self, _item: QTreeWidgetItem, _column: int) -> None:
        self.activate_current()


__all__ = [
    "NOTHING_SELECTED",
    "NO_HITS",
    "PANEL_HEIGHT",
    "UNAVAILABLE",
    "ReferencedByPanel",
]
