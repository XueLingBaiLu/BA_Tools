"""就地冒出来的候选列表（「换…」「＋」都用它）——设计块 7 的"卡片上就地冒搜索框"。

用户口径（本轮）：

    像是机动、传感器、装甲、这些，我不仅要能改，**还要能换**，
    且还能克隆一个出来，然后换成那个克隆过的。

    自定义选项我还是改不了，加不了。            <- 块 7：卡上每一块都要能「＋ / ✕」

「换」= 把本单位指着那一行的那一格（外键）改成另一行；「＋」= 挑一行插进关联表。
两个动作都要给一个候选列表，所以这里是**就地**的搜索框 + 候选列表：
不弹对话框（用户："我不需要这么多弹窗"），点一条就选定，Esc 或点「取消」就收手。

## 两张表一起挑（块 7 的「武器 × 弹种」）

加武器插的是 **WeaponAmmunitions**（一行 = 武器 × 弹种），所以给了 second_table
之后这里变**两列**：左列挑武器、右列挑弹种，点右列那一条才算选定
（发 pair_picked(武器, 弹种)）。左列只选不发 —— 少一次"还没挑弹种就写库"。

左列的候选有两种来源：

- 默认：search_rows 当场扫表（core/edit/foreign.py，Name 与 HUDName 都认）；
- 给了 choices：**只筛这几个**（加武器时就是"本单位已经挂着的武器"）。

候选一律来自 core/edit/foreign.py::search_rows，**不自己扫表** ——
那套中文名/显示名的口径只有一份。
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.foreign import name_columns, search_rows

#: 一次列几条（多了就是一面墙；打字筛剩下的再出来）。
DEFAULT_LIMIT = 12


class RowPicker(QWidget):
    """候选列表：一行 = 名字 + #Id。「换…」用单列，「＋（武器 × 弹种）」用双列。"""

    #: 选中了哪一行（参数：行 Id）。**双列模式下不发** —— 那时候发 pair_picked。
    picked = Signal(int)
    #: 双列模式选中了一对（参数：第一列行 Id, 第二列行 Id）。
    pair_picked = Signal(int, int)
    #: 收手（Esc / 点了「取消」）。
    cancelled = Signal()

    def __init__(
        self,
        instance: Instance,
        table: str,
        *,
        second_table: str | None = None,
        exclude: int = 0,
        limit: int = DEFAULT_LIMIT,
        choices: Iterable[tuple[int, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._instance = instance
        self._table = table
        self._second_table = second_table
        self._exclude = exclude
        self._limit = limit
        #: 手工候选（行 Id, 名字）；给了就不扫表（加武器时 = 本单位已有的武器）。
        self._choices: tuple[tuple[int, str], ...] | None = (
            tuple(choices) if choices is not None else None
        )
        #: 第一列现在选中的那一行（双列模式下右列要拿它配对）。
        self._first_id = 0
        #: 第一列**已经定死**时它的名字（见 lock_first）：非空 = 不再让人挑第一列。
        self._first_label = ""
        self._first_locked = False
        self.setProperty("role", "row-picker")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, TOKENS.spacing.xxs, 0, TOKENS.spacing.xxs)
        root.setSpacing(TOKENS.spacing.xxs)

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.xs)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(self._placeholder(table))
        self._filter.textChanged.connect(self._refill)
        self._filter.returnPressed.connect(self._take_first)
        head.addWidget(self._filter, 1)

        self._second_filter: QLineEdit | None = None
        if second_table:
            self._second_filter = QLineEdit()
            self._second_filter.setPlaceholderText(self._placeholder(second_table))
            self._second_filter.textChanged.connect(self._refill_second)
            self._second_filter.returnPressed.connect(self._take_first_second)
            head.addWidget(self._second_filter, 1)

        cancel = QPushButton("取消")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setProperty("variant", "ghost")
        cancel.clicked.connect(self.cancelled)
        head.addWidget(cancel)
        root.addLayout(head)

        lists = QHBoxLayout()
        lists.setSpacing(TOKENS.spacing.xs)
        self._list = QListWidget()
        self._list.setProperty("role", "row-list")
        self._list.setMaximumHeight(TOKENS.size.row_height * 6)
        self._list.itemActivated.connect(self._take_item)
        self._list.itemClicked.connect(self._take_item)
        self._list.currentRowChanged.connect(self._on_first_row_changed)
        lists.addWidget(self._list, 1)

        self._second_list: QListWidget | None = None
        if second_table:
            self._second_list = QListWidget()
            self._second_list.setProperty("role", "row-list")
            self._second_list.setMaximumHeight(TOKENS.size.row_height * 6)
            self._second_list.itemActivated.connect(self._take_second)
            self._second_list.itemClicked.connect(self._take_second)
            lists.addWidget(self._second_list, 1)
        root.addLayout(lists)

        self._hint = QLabel("")
        self._hint.setProperty("role", "muted")
        root.addWidget(self._hint)
        self._refill("")
        self._refill_second("")

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """露出来并聚焦输入框（外层负责 setVisible(True)）。"""
        self._filter.setFocus()
        self._filter.selectAll()

    # -- 候选 ------------------------------------------------------------ #

    def _placeholder(self, table: str) -> str:
        if not name_columns(self._instance, table):
            # 关联行整张表都没有名字列：候选名是**外键拼**出来的（见 foreign.row_label）。
            return f"🔎 打字筛 {table}（这张表没有名字列，按外键名字筛）…"
        return f"🔎 打字筛 {table}（Name 与 HUDName 都认）…"

    def _candidates(self, text: str) -> tuple[tuple[int, str], ...]:
        """第一列的候选（行 Id, 显示文字）。有手工候选就不扫表。"""
        needle = (text or "").strip().casefold()
        if self._choices is not None:
            return tuple(
                (row_id, f"{name}   #{row_id}")
                for row_id, name in self._choices
                if row_id != self._exclude and (not needle or needle in name.casefold())
            )
        if not self._table:
            return ()
        hits = search_rows(self._instance, self._table, text or "", limit=400)
        return tuple(
            (hit.row_id, f"{hit.name}   #{hit.row_id}")
            for hit in hits
            if hit.row_id != self._exclude
        )

    def _second_candidates(self, text: str) -> tuple[tuple[int, str], ...]:
        """第二列的候选（弹种）。"""
        if not self._second_table:
            return ()
        hits = search_rows(self._instance, self._second_table, text or "", limit=400)
        return tuple((hit.row_id, f"{hit.name}   #{hit.row_id}") for hit in hits)

    def _refill(self, text: str) -> None:
        candidates = self._candidates(text)
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._list.count():
            self._list.takeItem(0)
        shown = 0
        for row_id, label in candidates:
            self._append(self._list, row_id, label)
            shown += 1
            if shown >= self._limit:
                break
        if shown:
            self._list.setCurrentRow(0)
        else:
            self._first_id = 0
        self._update_hint(len(candidates), shown)

    def _refill_second(self, text: str) -> None:
        if self._second_list is None:
            return
        candidates = self._second_candidates(text)
        # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
        #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
        while self._second_list.count():
            self._second_list.takeItem(0)
        for row_id, label in candidates[: self._limit if not text else 400]:
            self._append(self._second_list, row_id, label)
        if self._second_list.count():
            self._second_list.setCurrentRow(0)
        if self._second_table:
            total = len(candidates)
            if total > self._second_list.count():
                self._hint.setText(
                    f"共 {total} 条 {self._second_table} 候选，显示前 "
                    f"{self._second_list.count()} 条 —— 接着打字就筛"
                )

    def _append(self, widget: QListWidget, row_id: int, label: str) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, row_id)
        widget.addItem(item)

    def _update_hint(self, total: int, shown: int) -> None:
        if self._second_table:
            if self._first_label:
                # 第一列从武器行那颗「＋」进来时已经定了 —— 只叫人挑弹种（见 lock_first）。
                self._hint.setText(
                    f"给「{self._first_label}」加一种弹：点一个弹种 —— 一行 = 武器 × 弹种"
                )
                return
            picked = self._first_id or 0
            what = f"已选 #{picked}" if picked else "还没挑武器"
            self._hint.setText(
                f"先挑武器（{shown} / {total} 门，{what}），再点一个弹种 —— 一行 = 武器 × 弹种"
            )
            return
        self._hint.setText(
            f"共 {total} 条候选，显示前 {shown} 条 —— 接着打字就筛"
            if total > shown
            else f"{shown} 条候选"
        )

    # -- 选定 ------------------------------------------------------------ #

    def _on_first_row_changed(self, _row: int) -> None:
        self._first_id = self.current_first_id
        if self._second_table:
            self._update_hint(self._list.count(), self._list.count())

    def _take_first(self) -> None:
        """回车：单列 = 选第一条；双列 = 选中的武器定了，去挑弹种。"""
        if self._second_table:
            if self._second_filter is not None:
                self._second_filter.setFocus()
            return
        if self._list.count():
            self.picked.emit(int(self._list.item(0).data(Qt.ItemDataRole.UserRole)))

    def _take_item(self, item: QListWidgetItem) -> None:
        if self._second_table:
            # 左列只是"选武器"：还没挑弹种，不写库。
            self._first_id = int(item.data(Qt.ItemDataRole.UserRole))
            self._update_hint(self._list.count(), self._list.count())
            if self._second_filter is not None:
                self._second_filter.setFocus()
            return
        self.picked.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _take_second(self, item: QListWidgetItem) -> None:
        if self._first_id <= 0:
            self._hint.setText("先在左边挑一门武器，再点弹种")
            return
        self.pair_picked.emit(self._first_id, int(item.data(Qt.ItemDataRole.UserRole)))

    def _take_first_second(self) -> None:
        if self._second_list is not None and self._second_list.count():
            self._take_second(self._second_list.item(0))

    # -- 只读访问（用例用） ---------------------------------------------- #

    @property
    def table(self) -> str:
        return self._table

    @property
    def second_table(self) -> str | None:
        return self._second_table

    @property
    def count(self) -> int:
        return self._list.count()

    @property
    def second_count(self) -> int:
        """第二列（弹种）现在列了几条。"""
        return self._second_list.count() if self._second_list is not None else 0

    def row_id_at(self, index: int) -> int:
        return int(self._list.item(index).data(Qt.ItemDataRole.UserRole))

    def second_row_id_at(self, index: int) -> int:
        assert self._second_list is not None
        return int(self._second_list.item(index).data(Qt.ItemDataRole.UserRole))

    @property
    def first_locked(self) -> bool:
        """第一列是不是被**钉死**了（从武器行那颗「＋」进来的那种，见 lock_first）。"""
        return self._first_locked

    @property
    def current_first_id(self) -> int:
        """第一列现在选中的行 Id（没选是 0）。

        ❗ 被 `lock_first` 钉死时**以钉的那一份为准**：那一列这时已经藏起来了，
        列表控件的 currentItem 还停在原文第一行上 —— 照控件读会读到**另一门枪**。
        """
        if self._first_locked:
            return self._first_id
        item = self._list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else 0

    def choose_first(self, row_id: int) -> bool:
        """选中第一列某一行（**不发信号**）—— 用例用它跳过"打字筛"。"""
        for index in range(self._list.count()):
            if self.row_id_at(index) == row_id:
                self._list.setCurrentRow(index)
                self._first_id = row_id
                return True
        return False

    def lock_first(self, row_id: int, label: str = "") -> bool:
        """第一列**已经定了**（从武器行那一颗「＋」进来：枪就是那一门）。

        ❗ 为什么不复用 `choose_first`：它要求那一行**已经在候选列表里**，而
        "给这门枪加弹"时候选名单是"本单位现有的枪"——这门枪若属于**当前没选中的
        那套改装选项**（树上照样画着它，用户点的正是这种），名单里根本没有它，
        choose_first 会静默失败，用户看到的是**另一门枪**被选中。所以这里直接把
        第一列钉死、并把那一列藏掉，只留弹种给人挑。

        返回 False = 这张 picker 没有第二列（不该这么用）。
        """
        if self._second_table is None:
            return False
        self._first_id = int(row_id)
        self._first_label = label
        self._first_locked = True
        self._filter.setVisible(False)
        self._list.setVisible(False)
        self._refill_second("")
        if self._second_filter is not None:
            self._second_filter.setFocus()
        self._update_hint(len(self._second_candidates("")), self.second_count)
        return True

    def pick_second(self, index: int) -> None:
        """点第二列第 index 行（发 pair_picked）。"""
        assert self._second_list is not None
        self._take_second(self._second_list.item(index))


__all__ = ["DEFAULT_LIMIT", "RowPicker"]
