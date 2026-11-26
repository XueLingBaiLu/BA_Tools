"""检索页：一个框，名字 / HUD 名 / UIName / 图标·缩略图·立绘文件名 / Id 都能查。

照旧产品那套查法重做（`_work/design/overhaul3.png` 底部那一块）：

- **左栏是结果、右栏是"刚点的那一行"**（设计稿 §3 块 10 / 设计图 `_work/design/block13.png`）：
  一点 = 右栏显示它的**全部列**；双击 / `Enter` / 右栏那颗「在数据表里打开」才真跳过去；
- **输入即筛** —— 没有"检索"按钮。多按一次回车才出结果，正是旧产品要修掉的毛病；
- `F3` / `Ctrl+F` 聚焦，`↑↓` 选，`Enter` 直接打开那一行；
- 结果行 = `表 | Id | 名称 | 命中的哪几列`：表那一格是 **emoji + 中文表名**，
  命中的列画成一排**小胶囊**（`role="hit-chip"`）；最多先给 100 条，并如实写「共 N 条」；
- **Id 要显示**（用户口径）：做 mod 是"点名到行"的活；
- 右栏**只读但可导航**（用户 2026-10-02 定的口径）：点值那一格，是外键就跳去**它指向的那一行**，
  不是外键就打开**这一行** —— 不许出现"点了没反应"的格子；
- 右栏那一行的**命中格标黄 + 挂「命中」** —— 不另造一套：走现成的
  `RowEntriesPanel.set_cells(hits=…)`，样式在 `theme/qss.py`（`[hit="true"]` / `role="hit-chip"`）；
- 空框给一句提示，不倒一面墙。

搜哪些列、怎么排序、截断怎么算：全在 `core/edit/foreign.py::lookup_rows` ——
这一层只负责画，**不自己扫表**（口径只有一份，用例也能直接断言那个纯函数）。

为什么不再用 `core/data/search.py::search_instance`（整库逐格搜子串）：
它是"某个值在库里出现过没有"，会把 24 张表里凡是含这几个字的格全倒出来
（搜 "Stryker" 会连 SquadMembers 的模型文件名都算一条）。用户要的是
**按名字/图标文件名找到那一行**，不是找格子。

## 「按行折叠」住在核心层（实测：早就成立了，别在界面里再折一遍）

`LookupHit` 的注释写着"**一行一条**，不是一格一条" —— 同一行有几列命中只算一条，
命中的列全记在 `hit.columns` 里（界面画成胶囊）。实跑 `lookup_rows(52085, …)`：

    "Stryker" -> total=112，命中 112 条，落在 **112 行**上（1:1）
    "BTR"     -> 86 / 86     "M1A2" -> 84 / 84

所以界面这一层**不做去重**：拿到什么画什么，折叠的口径只有 `lookup_rows` 一份。

对外入口：`set_instance()` / `focus_input()` / `run_search()`。
对外信号：`hit_activated`（表名, **行 Id**, 第一个命中的列）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.table_labels import table_emoji, table_title
from ba_mod_maker.app.widgets.row_entries import RowEntriesPanel
from ba_mod_maker.app.widgets.table_model import cell_text
from ba_mod_maker.core.data import Instance
from ba_mod_maker.core.edit.cells import row_cells
from ba_mod_maker.core.edit.foreign import LookupHit, LookupResult, lookup_rows
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef

#: 输入后多久才真扫库（毫秒）。打字是连发的，攒一下再扫 —— 少几次整表重排。
_DEBOUNCE_MS = 100

#: 表头四列（用户口径：表 | Id | 名称 | 命中的是哪一列）。
_HEADERS = ("表", "Id", "名称", "命中的是哪一列")

#: 输入框里的提示。直接告诉用户这个框能查什么。
_PLACEHOLDER = "名字 / HUD 名 / UIName / 图标·缩略图·立绘文件名 / Id…"

#: 空框时那句话 —— 不是一面空白，也不是把整库倒出来。
_HINT = "输入名字 / HUD 名 / UIName / 图标·缩略图·立绘文件名 / Id（纯数字按 Id 精确命中）"

#: 键盘三件套，写在结果上方让人一眼看见。
_CHIPS = "F3 聚焦 · ↑↓ 选 · Enter 直接打开"

#: Id 用小一号的暗色，命中列用 warning —— 一眼看出"为什么它被搜出来"。
_ID_BRUSH = QBrush(QColor(TOKENS.color.text_secondary))
_HIT_BRUSH = QBrush(QColor(TOKENS.color.warning))


class GlobalSearch(QWidget):
    """跨 24 张表的检索页。"""

    #: 打开一条结果：（表名, **行 Id**, 第一个命中的列）。
    #: 第二个参数是**行 Id**，不是行下标 —— 外层拿它就是对外的那个 Id
    #: （Units 里就是单位 Id）。列名只给第一个命中的那个：跳转要用真列名。
    hit_activated = Signal(str, int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance: Instance | None = None
        self._result: LookupResult | None = None
        #: 右栏现在显示的是哪一条（没有就是 None）。
        self._current: LookupHit | None = None

        self._input = QLineEdit()
        self._summary = QLabel()
        self._results = QTreeWidget()
        self._detail = RowEntriesPanel()
        self._open_button = QPushButton()
        self._open_button.setCursor(Qt.PointingHandCursor)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)

        self._build_ui()
        self._connect()

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #

    def set_instance(self, instance: Instance | None) -> None:
        """换实例。旧结果对新实例无意义，一律清掉。"""
        self._debounce.stop()
        self._instance = instance
        self._result = None
        self._results.clear()
        self._clear_detail()
        self._input.setEnabled(instance is not None)
        if instance is None:
            self._input.clear()
            self._sync_summary()
            return
        if self._input.text().strip():
            # 换了实例、框里还有词：当场在新实例上重搜 —— 旧结果对新实例无意义，
            # 但把词留着却只给一句通用提示，看起来像"搜了没结果"。
            self.run_search()
            return
        self._sync_summary()

    def focus_input(self) -> None:
        """把光标送进搜索框（F3 / Ctrl+F 用）。"""
        self._input.setFocus()
        self._input.selectAll()

    def run_search(self, query: str | None = None) -> LookupResult | None:
        """立刻扫一次并填结果表（防抖定时器会被取消）。未加载数据源返回 None。"""
        self._debounce.stop()
        if self._instance is None:
            return None
        if query is not None and query != self._input.text():
            # 外层/用例直接给词：同步进输入框（否则清空按钮与提示句对不上）。
            self._input.blockSignals(True)
            self._input.setText(query)
            self._input.blockSignals(False)
        result = lookup_rows(self._instance, self._input.text())
        self._result = result
        self._populate(result)
        return result

    @property
    def result(self) -> LookupResult | None:
        """最近一次检索的结果（外壳与用例读它，别去扒控件）。"""
        return self._result

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        title = QLabel("检索")
        title.setProperty("role", "table-title")
        desc = QLabel("照旧产品那套查法：一个框，什么都能查")
        desc.setProperty("role", "muted")
        chips = QLabel(_CHIPS)
        chips.setProperty("role", "muted")

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.sm)
        head.addWidget(title)
        head.addWidget(desc)
        head.addStretch(1)
        head.addWidget(chips)

        self._input.setPlaceholderText(_PLACEHOLDER)
        self._input.setClearButtonEnabled(True)
        # ↑↓ 在输入框里就能换选中行 —— 手不用离开键盘。
        self._input.installEventFilter(self)

        self._summary.setProperty("role", "caption")
        self._summary.setWordWrap(True)

        self._results.setColumnCount(len(_HEADERS))
        self._results.setHeaderLabels(list(_HEADERS))
        self._results.setAlternatingRowColors(True)
        self._results.setRootIsDecorated(False)
        self._results.setUniformRowHeights(True)
        self._results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # ❗ 前三列**是有意收窄的**：第 4 列（命中的列 → 胶囊）靠 stretchLastSection 吃剩下的，
        #    前三列一宽它就变 0 宽 —— 实测 170+80+320=570，左栏正好 570，胶囊一格都画不出来。
        self._results.setColumnWidth(0, 150)
        self._results.setColumnWidth(1, 64)
        self._results.setColumnWidth(2, 210)
        self._results.header().setStretchLastSection(True)

        # 命中的列画成小胶囊（item 的 text 不变，见 _HitChipDelegate）。
        self._chips = _HitChipDelegate(self._results)
        self._results.setItemDelegateForColumn(3, self._chips)

        # ---- 左栏：框 + 一句话 + 结果表 ----
        left = QWidget()
        # 左栏就用 qss.py 里那个标准的列表面板 role（别自造 —— test_theme 的守卫会红）。
        left.setProperty("role", "list-panel")
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(TOKENS.spacing.md)
        left_box.addLayout(head)
        left_box.addWidget(self._input)
        left_box.addWidget(self._summary)
        left_box.addWidget(self._results, 1)

        # ---- 右栏：刚点的那一行的**全部列**（设计稿块 10）----
        self._open_button.setProperty("role", "row-tool")
        self._open_button.setVisible(False)
        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.addStretch(1)
        tool_row.addWidget(self._open_button)
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(TOKENS.spacing.sm)
        right_box.addLayout(tool_row)
        right_box.addWidget(self._detail, 1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([460, 940])
        self._split = split

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg
        )
        layout.setSpacing(TOKENS.spacing.md)
        layout.addWidget(split, 1)

    def _connect(self) -> None:
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._activate_current)
        self._debounce.timeout.connect(self.run_search)
        # ❗ 两个动作分家（设计稿块 10 / 设计图 block13）：**选一条 = 右栏看它**；
        #    双击 / Enter / 右栏那颗按钮才是"真跳过去"。
        #    从前单击也直接跳走 —— 那样右栏永远没机会显示（block13 那一帧就是
        #    左边点着 Stryker RV、右边看着它的 31 列）。
        self._results.currentItemChanged.connect(self._on_current_changed)
        self._results.itemClicked.connect(self._on_current_changed)
        self._results.itemActivated.connect(self._on_item_activated)
        self._results.itemDoubleClicked.connect(self._on_item_activated)
        self._open_button.clicked.connect(self._open_current_in_table)
        # 右栏**不是死的**：点值那一格 = 导航（用户 2026-10-02 定的口径，见下两条的说明）。
        # 与编辑器里那套完全同源（pages/base.py 也是接这两个信号），不另造一套。
        self._detail.cell_activated.connect(self._on_detail_cell)
        self._detail.link_activated.connect(self._on_detail_link)

    # ------------------------------------------------------------------ #
    # 响应
    # ------------------------------------------------------------------ #

    def _on_text_changed(self, text: str) -> None:
        """打字：攒 100ms 再扫。清空输入就立刻回到提示句，别留着上一次的结果误导人。"""
        if not text.strip():
            self._debounce.stop()
            self._result = None
            self._results.clear()
            self._clear_detail()
            self._sync_summary()
            return
        self._debounce.start()

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        """双击 / Enter：**真跳到那一行**（选一条只更新右栏，不跳）。"""
        hit = item.data(0, _HIT_ROLE)
        if hit is not None:
            self._emit_hit(hit)

    def _emit_hit(self, hit: LookupHit) -> None:
        """跳转信号只有这一个出口（双击 / Enter / 右栏按钮都走它）。"""
        self.hit_activated.emit(str(hit.table), int(hit.row_id), str(hit.column))

    def _activate_current(self) -> None:
        """回车：打开当前那一条；一条都没选中就打开第一条。"""
        item = self._results.currentItem() or self._results.topLevelItem(0)
        if item is not None:
            self._on_item_activated(item, 0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt 命名
        """输入框里的 ↑↓ / PgUp / PgDn 换成结果列表的选中行。"""
        if watched is self._input and event.type() == QEvent.Type.KeyPress:
            step = {
                Qt.Key.Key_Up: -1,
                Qt.Key.Key_Down: 1,
                Qt.Key.Key_PageUp: -10,
                Qt.Key.Key_PageDown: 10,
            }.get(event.key())
            if step is not None:
                self._move_selection(step)
                return True
        return super().eventFilter(watched, event)

    def _move_selection(self, step: int) -> None:
        count = self._results.topLevelItemCount()
        if not count:
            return
        current = self._results.currentItem()
        index = self._results.indexOfTopLevelItem(current) if current is not None else -1
        target = min(count - 1, max(0, index + step))
        item = self._results.topLevelItem(target)
        self._results.setCurrentItem(item)
        self._results.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    # ------------------------------------------------------------------ #
    # 画结果
    # ------------------------------------------------------------------ #

    def _populate(self, result: LookupResult) -> None:
        self._results.clear()
        for hit in result.hits:
            item = QTreeWidgetItem(
                [_table_label(hit.table), f"#{hit.row_id}", hit.name, hit.column_text]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, (hit.table, hit.row_id, hit.column))
            item.setData(0, _HIT_ROLE, hit)
            # 胶囊那一格：**文本仍是 "A · B"**（用例 / 复制 / 无障碍读的都是它），
            # 代理只是照这份列表把它画成一排小胶囊 —— 一份数据两种画法。
            item.setData(3, Qt.ItemDataRole.UserRole, list(hit.columns))
            item.setToolTip(0, f"{hit.table} #{hit.row_id} · {_table_label(hit.table)}")
            item.setToolTip(2, hit.name)
            item.setToolTip(3, f"{hit.column_text} = {_brief(hit.value)}")
            item.setForeground(1, _ID_BRUSH)
            item.setForeground(3, _HIT_BRUSH)
            self._results.addTopLevelItem(item)
        if result.count:
            # 先选中第一条：右栏立刻就是它（Enter 仍然是"打开它"）。
            self._results.setCurrentItem(self._results.topLevelItem(0))
            # ❗ 上面那句会发 currentItemChanged -> _on_current_changed；这里再显式来一次，
            #    因为"从没选中到选中第一条"时信号在少数路径上不发（Qt 只在**变了**才发）。
            self._show_detail(result.hits[0])
        else:
            self._clear_detail()
        self._sync_summary(result)

    # ------------------------------------------------------------------ #
    # 右栏：刚点的那一行的全部列
    # ------------------------------------------------------------------ #

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous=None) -> None:
        hit = current.data(0, _HIT_ROLE) if current is not None else None
        if hit is None:
            self._clear_detail()
            return
        self._show_detail(hit)

    def _show_detail(self, hit: LookupHit) -> None:
        """右栏 = 这一行的**全部列**，命中的格子标黄 + 挂「命中」（设计稿块 10）。

        命中的落点用 `CellRef(表, 行 Id, 列)` 交给 `RowEntriesPanel` —— 那套
        「标黄 + 挂标签」是现成的（`theme/qss.py` 的 `[hit="true"]`），这里不另造一份。
        """
        self._current = hit
        cells = (
            ()
            if self._instance is None
            else row_cells(self._instance, RowRef(hit.table, hit.row_id))
        )
        hits = tuple(CellRef(hit.table, hit.row_id, column) for column in hit.columns)
        label = _table_label(hit.table)
        self._detail.set_cells(
            cells,
            title=hit.name or f"{hit.table} #{hit.row_id}",
            subtitle=f"{label} {hit.table}#{hit.row_id} · {len(cells)} 列 · 命中的格子标黄",
            hits=hits,
        )
        # 按钮的字要说真话：Units 双击是**进单位编辑器**（外壳那条路），别的表才去数据表页。
        to_editor = hit.table == "Units"
        self._open_button.setText("在单位编辑器里打开" if to_editor else "在数据表里打开")
        self._open_button.setToolTip(f"打开 {hit.table} #{hit.row_id}（与双击/回车同一条路）")
        self._open_button.setVisible(True)

    def _clear_detail(self) -> None:
        """没有选中项时右栏留白（不显示上一条的残影）。"""
        self._current = None
        self._detail.set_cells((), title="", subtitle="")
        self._open_button.setVisible(False)

    # ------------------------------------------------------------------ #
    # 右栏 = 导航（用户 2026-10-02 定的口径）
    #
    # 这一栏**只读**（不接就地编辑）—— 检索页的职责是"找"，改归编辑器 / 数据表页。
    # 但"点了没反应"是另一种坏：所以点值那一格**一律有事发生**：
    #   ① 它是外键（指向别的一行）-> 跳到**它指向的那一行**（与行尾那个 ↗ 同一条路）；
    #   ② 它不指向别处       -> 打开**这一行**（与右上角那颗按钮同一条路）。
    # ------------------------------------------------------------------ #

    def _on_detail_link(self, table: str, row_id: int) -> None:
        """行尾那个 ↗：跳到它指向的那一行。

        列名给空串 —— 链接只知道"目标表 + 目标行"，没有"命中哪一列"可言；
        外壳对空列名就是"只定位到行、不选格"（`main_window._on_search_hit`）。
        """
        self.hit_activated.emit(str(table), int(row_id), "")

    def _on_detail_cell(self, cell) -> None:
        """点值那一格：指向别处就跳过去；否则打开这一行（**不做无反应的点击**）。

        ⚠️ 信号里带的是 **`FieldCell`**（不是 `CellRef`）—— `EntryRow` 的 `activated` 发的是
        `self.cell`。别照着"它是个 ref"去用（第一版就是这么错的：`row_for(ref)` 找不到行,
        于是每一下点击都掉进兜底那条路，外键那一路永远走不到）。
        """
        row = self._detail.row_for(cell.ref)
        target = row.link_target if row is not None else None
        if target is not None:
            self.hit_activated.emit(str(target[0]), int(target[1]), "")
            return
        if self._current is not None:
            self._emit_hit(self._current)

    def _open_current_in_table(self) -> None:
        """右栏那颗「在…里打开」：与双击 / Enter 走**同一条路**（同一个信号）。"""
        if self._current is not None:
            self._emit_hit(self._current)

    def _sync_summary(self, result: LookupResult | None = None) -> None:
        """结果上方那句话：多少条 / 被截断了没有 / 没找到。"""
        if self._instance is None:
            self._summary.setText("尚未加载数据源")
            return
        if result is None or not self._input.text().strip():
            self._summary.setText(f"共 {len(self._instance.table_names)} 张表一起搜 · {_HINT}")
            return
        if result.total == 0:
            self._summary.setText(f"没找到「{result.query.strip()}」 · {_HINT}")
            return
        text = f"共 {result.total} 条命中 · 按行折叠（一条 = 一行，命中的列挂在胶囊里）"
        if result.truncated:
            text += f" · 先显示前 {result.count} 条（接着打字就窄）"
        self._summary.setText(f"{text} · {_CHIPS}")


def _brief(value: object, limit: int = 80) -> str:
    """值压成一行短文本（结果表里不能出现一整行 JSON）。"""
    text = cell_text(value)
    return text if len(text) <= limit else text[:limit] + "…"


#: item 上挂 `LookupHit` 本体的 role（`UserRole` 已经给了 (表, 行, 列) 那个三元组）。
_HIT_ROLE = Qt.ItemDataRole.UserRole + 1


def _table_label(table: str) -> str:
    """结果行第一格：**emoji + 中文表名**（两张登记表在 `viewmodel/table_labels.py`）。"""
    emoji = table_emoji(table)
    title = table_title(table)
    return f"{emoji} {title}" if emoji else title


class _HitChipDelegate(QStyledItemDelegate):
    """「命中了哪几列」画成一排小胶囊（QSS 里 `role="hit-chip"` 那一套颜色）。

    为什么走代理、不往格子里塞控件：`QTreeWidget` 的键盘（↑↓ / Enter）与选中态是白送的，
    塞 `setItemWidget` 会把它们弄丢（本仓为这类事吃过亏）。而且 item 的 **text 保持 "A · B"**
    —— 用例、复制、无障碍读的都是它，胶囊只是"同一份数据的另一种画法"。
    """

    def paint(self, painter: QPainter, option, index) -> None:
        columns = index.data(Qt.ItemDataRole.UserRole)
        if index.column() != 3 or not columns:
            super().paint(painter, option, index)
            return
        # 先照常画那一格的底色（选中 / 交替行色）—— 不画的话选中那一行会缺一块。
        style = option.widget.style() if option.widget is not None else None
        if style is not None:
            style.drawPrimitive(
                QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget
            )
        font = QFont(option.font)
        font.setPointSize(TOKENS.typography.size_xs)
        painter.save()
        painter.setFont(font)
        metrics = painter.fontMetrics()
        height = metrics.height() + 4
        x = option.rect.left() + TOKENS.spacing.xs
        top = option.rect.center().y() - height / 2
        painter.setBrush(QBrush(QColor(TOKENS.color.warning_subtle)))
        painter.setPen(QPen(QColor(TOKENS.color.warning), 1))
        drawn = 0
        for column in columns:
            width = metrics.horizontalAdvance(column) + 2 * TOKENS.spacing.xs + 2
            if x + width > option.rect.right() - TOKENS.spacing.xs:
                break  # 放不下就不画了（tooltip 里那一串是完整的）
            rect = QRectF(x, top, width, height)
            painter.drawRoundedRect(rect, TOKENS.radius.sm, TOKENS.radius.sm)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, column)
            x += width + TOKENS.spacing.xs
            drawn += 1
        if drawn < len(columns):
            # ❗ 画不完就补一个省略号 —— 不然看着像"只命中了这两列"。
            painter.drawText(
                QRectF(x, top, TOKENS.spacing.lg, height),
                Qt.AlignmentFlag.AlignCenter,
                "…",
            )
        painter.restore()
