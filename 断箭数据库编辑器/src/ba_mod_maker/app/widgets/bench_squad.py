"""编成台 ① 「这个班」：格子矩阵，**一格 = 一个兵**。

出处：docs/交接-2026-10-01-单位编成台设计.md §3 口径 3 + 图 docs/设计稿-编成台-步兵.png。

    ① 这个班   SquadMembers · 一格 = 一个兵 · 图标 = 游戏原生 HUDIcon   [＋ 加一名兵][⧉ 复制这一名]
    ┌ 01 ✕ ┐┌ 02 ✕ ┐┌ 03 ✕ ┐ …            <- 序号（01..14，Id 升序）+ 右上 ✕
    │ [图]Mk18 CQBR │                        <- 主武器：Weapons.HUDIcon + 名字
    │ [图]M1014     │                        <- 副武器；0 -> 「没有副武器」（不是空白）
    │ officer       │                        <- 脚注 = ModelFileName 去掉「阵营_单位_」前缀
    └───────┘

三条纪律（AGENTS §4）：本层**只摆控件 + 发信号**，一个字节都不写库 —— 写库全部经
EditorContext（add_squad_member / insert_row / delete_row），新行 Id 由
core/edit/clone.py::next_row_id 给。取数与规则在 app/viewmodel/bench_squad.py（零 Qt）。

**本控件不发"跳到别的行"**（那一整件事 2026-10-02 按用户要求删了）。一个动作一个含义：
格子上的手势只有两个，点 = 选中、右上 ✕ = 删；双击再叠一个"跳走"是隐藏的第二含义。

不弹窗（AGENTS §5）：成功 / 没有改动 / 加不了 一律走 status_message 一句话。

**改一格不重建**（用户提过几回：「不要全量保存，界面闪一下很影响观感」）：
结构没变（署名一样）时外层调 `reload_values()` —— **就地换值，一个控件都不重建**；
只有插/删行那种结构变化才调 `refresh()`。两条先例见 reload_values 的 docstring。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.viewmodel.bench_squad import (
    TABLE,
    SquadCell,
    copy_member_values,
    squad_cells,
    squad_empty_text,
)
from ba_mod_maker.app.viewmodel.unit_card import squad_loadout
from ba_mod_maker.app.widgets.bench_kit import (
    BenchEmpty,
    BenchGrid,
    BenchSection,
    SlotCard,
    id_badge_text,
)
from ba_mod_maker.core.assets.families import WEAPON_ICON_FAMILY
from ba_mod_maker.core.errors import BaModMakerError

#: 抬头（照设计稿原字）。
TITLE = "① 这个班"
HINT = "SquadMembers · 一格 = 一个兵 · 图标 = 游戏原生 HUDIcon"

#: 格的尺寸 —— 与 bench_kit.SlotCard 的默认一致（152 宽 × 104 高）。一格装三样：
#: 序号 + 两行枪（图标 + 名字）+ 一行脚注。一行排几格由 FlowLayout 按中栏宽度自己换行
#: （设计稿那一栏是 7 格一行；本轮真机截图在 1060px 宽下是 6 格一行，14 名兵两行多）。
SLOT_WIDTH = 152
SLOT_HEIGHT = 104

#: 还没进单位时主体那句灰字（**不是空框** —— 空框看不出是空的还是没画出来）。
NOT_IN_EDITOR = "还没进单位 —— 选中一个单位之后这里画出他的班"


class SquadBench(QWidget):
    """① 这个班：14 格一屏排开，一格一个兵。

    对外两个信号（见类属性）与下面那几个读数方法；装配见 BenchPage（Lead 那边）。
    """

    #: 选中了某一名：("SquadMembers", 行 Id) —— 右栏照这个画。
    row_selected = Signal(str, int)
    #: 一句人话（成功 / 没有改动 / 加不了），**绝不弹窗**。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = None
        self._store = None
        self._selected: int | None = None
        self._cells: tuple[SquadCell, ...] = ()
        self._slots: tuple[SlotCard, ...] = ()
        #: 每一格对应的 SquadMembers 行 Id（与 _slots 等长同序）——reload_values 靠它
        #: 把"新值"对回"老控件"；**没有它就只能重建**，那就闪了。
        self._slot_ids: tuple[int, ...] = ()
        self._grid: BenchGrid | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._section = BenchSection(TITLE, HINT)
        self._add_button = self._section.add_action(
            "＋ 加一名兵",
            primary=True,
            tooltip=(
                "照当前选中的这名复制一名新兵（没选中就照最后一名）—— "
                "新 Id、卡片「成员数」跟着 +1，Ctrl+Z 能撤回来"
            ),
        )
        self._add_button.clicked.connect(self._on_add)
        self._copy_button = self._section.add_action(
            "⧉ 复制这一名",
            tooltip="先在左边点一格选中一名兵 —— 没选中就没有可复制的这一名",
        )
        self._copy_button.clicked.connect(self._on_copy)
        root.addWidget(self._section)

        self.refresh()

    # ------------------------------------------------------------------ #
    # 进出（统一接口）
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上 EditorContext（None = 清空，画回空态）。换单位 = 换一份 context。"""
        self._context = context
        # 换单位 / 换 context：选中的那一行已经不属于这里了。
        self._selected = None
        self.refresh()

    def set_icons(self, store) -> None:
        """接上**唯一**那份图标缓存（外面 set_icons 传进来的 store）。

        ⛔ 不自己 new IconCache（会撞 access violation）—— store 为 None 时图标位留空，
        文字照画（与 app/screens/editor/pages/base.py::set_icons 同一条）。
        """
        self._store = store
        for slot, cell in zip(self._slots, self._cells, strict=True):
            slot.set_line_icon(0, store, WEAPON_ICON_FAMILY, cell.primary_key)
            slot.set_line_icon(1, store, WEAPON_ICON_FAMILY, cell.special_key)

    def refresh(self) -> None:
        """数据变了重画。**幂等**：连调两次得到同样的格子，不叠控件、不漏控件。

        收尾两步都不能省：BenchSection.clear_body（先 hide 再 setParent(None)，否则
        闪一个顶层小窗，见 widgets/detach.py）+ BenchGrid.clear（把上一批格子摘干净）。
        """
        context = self._context
        if context is None:
            self._cells = ()
        else:
            # 读之前先丢缓存：Rows.where / by_id / 倒排索引都缓存过这张表，不丢就照旧名单画
            # （squad_page._touch_squad_cache 同一个理由 —— 实测症状是"插了一行卡片还显示 5 人"）。
            context.data.invalidate(TABLE)
            self._cells = squad_cells(context.data, context.unit_id)

        # 选中的那一名没了（删了 / 撤销了插入 / 换了单位）-> 退回"没选中"，⧉ 跟着禁用。
        if self._selected is not None and self._selected not in self.member_ids():
            self._selected = None

        if self._grid is not None:
            self._grid.clear()  # 上一批格子：先摘干净再丢容器
            self._grid = None
        self._section.clear_body()
        self._slots = ()
        self._slot_ids = ()

        if self._cells:
            grid = BenchGrid()
            built: list[SlotCard] = []
            for cell in self._cells:
                slot = self._build_slot(cell)
                grid.add_cell(slot)
                built.append(slot)
            self._grid = grid
            self._slots = tuple(built)
            self._slot_ids = tuple(cell.member_id for cell in self._cells)
            self._section.add_widget(_GridHost(grid))
        else:
            self._section.add_widget(BenchEmpty(self._empty_text()))
        self._sync_buttons()

    def reload_values(self) -> None:
        """**就地换值：一个控件都不重建**（结构真变了由外层判署名后调 refresh()）。

        用户口径（提过几回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
        先例：docs/交接-2026-09-30-界面批-图标树弹药.md:76（署名变了才整套重建，没变走 reload）、
        docs/交接-2026-09-30-夜-实机反馈批.md:18（重建别让中栏向上跳）。

        这一块会变的字段都在 `_apply_cell` 里：序号、主/副武器名、图标键、脚注、选中态、提示。
        三条边界：

        - 一行都拿不到（没 context / 空态）-> 直接返回，那两条路由 refresh() 管；
        - 某一格对不上这一行了（结构变了）-> **跳过它**，不为它重建（外层马上会 refresh()）；
        - 全部对得上才回写 `self._cells` —— 这样 `_cells` 与 `_slots` 永远等长同序，
          `set_icons` / `_paint_selection` 那两处 zip(strict=True) 不会炸。
        """
        context = self._context
        if context is None or not self._slots:
            return
        context.data.invalidate(TABLE)
        fresh = {cell.member_id: cell for cell in squad_cells(context.data, context.unit_id)}
        if self._selected is not None and self._selected not in fresh:
            # 选中的那一名没了（插/删行）——**不在这里重建**，只把选中态退掉。
            self._selected = None
            self._sync_buttons()

        updated: list[SquadCell] = []
        for slot, member_id in zip(self._slots, self._slot_ids, strict=True):
            cell = fresh.get(member_id)
            if cell is None:
                continue
            self._apply_cell(slot, cell)
            updated.append(cell)
        if len(updated) == len(self._slots):
            self._cells = tuple(updated)

    # ------------------------------------------------------------------ #
    # 读数（测试与右栏用）
    # ------------------------------------------------------------------ #

    @property
    def cells(self) -> tuple[SquadCell, ...]:
        """当前这一屏的格子（按 Id 升序）——**控件里存的这一份**，不现算。"""
        return self._cells

    def cells_now(self) -> tuple[SquadCell, ...]:
        """同 cells，方法形式（统一接口里给"读数方法"留的那一手）。"""
        return self._cells

    def member_ids(self) -> tuple[int, ...]:
        """这一屏上每一格的 SquadMembers 行 Id（阅读序）。"""
        return tuple(cell.member_id for cell in self._cells)

    def slots(self) -> tuple[SlotCard, ...]:
        """每一格那个 SlotCard（个数与 cells 一一对应）。"""
        return self._slots

    def selected_row_id(self) -> int | None:
        """当前选中的那一名（None = 没选中 —— ⧉ 禁用、＋ 照最后一名）。"""
        return self._selected

    @property
    def add_button(self):
        return self._add_button

    @property
    def copy_button(self):
        return self._copy_button

    @property
    def section(self) -> BenchSection:
        return self._section

    # ------------------------------------------------------------------ #
    # 选中
    # ------------------------------------------------------------------ #

    def select(self, member_id: int) -> None:
        """选中一名（点一格走的就是这条路）：同一时刻只有一个选中 + 发 row_selected。

        不在这一屏上的 Id 不认（数据可能刚变过）——直接返回，不发信号。
        """
        if member_id not in self.member_ids():
            return
        self._selected = member_id
        self._paint_selection()
        self._sync_buttons()
        self.row_selected.emit(TABLE, member_id)

    def _paint_selection(self) -> None:
        for slot, cell in zip(self._slots, self._cells, strict=True):
            slot.set_selected(cell.member_id == self._selected)

    def clear_selection(self) -> None:
        """清掉选中（页面在**别的块**选中别的东西时调它）—— 不发信号、不写库。

        ⧉ 那两颗按钮的可用性跟着走（没选中就没得复制）—— 与 select() 同一条收尾。
        """
        if self._selected is None:
            return
        self._selected = None
        self._paint_selection()
        self._sync_buttons()

    # ------------------------------------------------------------------ #
    # 动作（＋ 加一名 / ⧉ 复制这一名 / ✕ 删这个兵）
    # ------------------------------------------------------------------ #

    def _on_add(self) -> None:
        """「＋ 加一名兵」——**走 EditorContext.add_squad_member**，不另写一套复制。"""
        context = self._context
        if context is None:
            return
        try:
            row_id = context.add_squad_member(self._selected or 0)
        except (BaModMakerError, KeyError) as exc:
            self.status_message.emit(f"没有加成：{exc}")
            return
        self._selected = int(row_id)  # 加完就选中新那一名（右栏跟着换过去）
        self.refresh()
        self.row_selected.emit(TABLE, int(row_id))
        self.status_message.emit(f"已加一名兵：{TABLE} #{row_id}（Ctrl+Z 能撤回来）")

    def _on_copy(self) -> None:
        """「⧉ 复制这一名」——照选中那一行整行照抄，新 Id 走 next_row_id（insert_row 里取）。

        没选中就不做（按钮本来就是禁用的，这里再挡一次：被程序调到也不许乱写）。
        """
        context = self._context
        if context is None:
            return
        if self._selected is None:
            self.status_message.emit("先在左边点一格选中一名兵 —— 没选中就没有可复制的这一名")
            return
        source = self._selected
        values = copy_member_values(context.data, context.unit_id, source)
        if values is None:
            self.status_message.emit(f"没找到 {TABLE} #{source} —— 复制不了，先刷新一下")
            return
        try:
            row_id = context.insert_row(TABLE, values)
        except (BaModMakerError, KeyError) as exc:
            self.status_message.emit(f"没有复制成：{exc}")
            return
        self._selected = int(row_id)
        self.refresh()
        self.row_selected.emit(TABLE, int(row_id))
        self.status_message.emit(
            f"已复制这一名：{TABLE} #{row_id}（照 #{source} 整行照抄，Ctrl+Z 能撤回来）"
        )

    def _remove(self, member_id: int) -> None:
        """格上 ✕ = 删掉这个兵（EditorContext.delete_row -> 新名单击可撤销）。"""
        context = self._context
        if context is None:
            return
        try:
            context.delete_row(TABLE, member_id)
        except (BaModMakerError, KeyError) as exc:
            self.status_message.emit(f"没有删掉：{exc}")
            return
        if self._selected == member_id:
            self._selected = None
        self.refresh()
        self.status_message.emit(f"已删掉这个兵：{TABLE} #{member_id}（Ctrl+Z 能撤回来）")

    # ------------------------------------------------------------------ #
    # 画一格
    # ------------------------------------------------------------------ #

    def _build_slot(self, cell: SquadCell) -> SlotCard:
        """建**空壳**，值由 _apply_cell 填 —— 与 reload_values 走同一段填值代码。

        （两处各写一遍的后果：改了一边忘了另一边，"改完值不跟着走"就是那么来的。）
        """
        slot = SlotCard("", width=SLOT_WIDTH, height=SLOT_HEIGHT)
        # 第 0 行 = 主武器，第 1 行 = 副武器（BenchSection 那两行就是照这个顺序加的）。
        slot.add_line("")
        slot.add_line("")
        self._apply_cell(slot, cell)
        slot.activated.connect(lambda member_id=cell.member_id: self.select(member_id))
        slot.remove_requested.connect(lambda member_id=cell.member_id: self._remove(member_id))
        return slot

    def _apply_cell(self, slot: SlotCard, cell: SquadCell) -> None:
        """把一格的值写进**已经存在**的那个控件 —— 不新建、不销毁任何控件。"""
        slot.set_index(f"{cell.index:02d}")
        slot.set_id_text(id_badge_text(cell.member_id))
        # 这一格 = SquadMembers 里的一行 —— 右上角把它的 Id 常显（口径见 bench_kit.id_badge_text）。
        # ❗ 写在 _apply_cell（建格与就地刷新**共用**这一段）而不是 _build_slot：
        #    两处各写一遍的后果，上面那段注释已经吃过一次。
        slot.set_line(0, cell.primary_label)
        slot.set_line_icon(0, self._store, WEAPON_ICON_FAMILY, cell.primary_key)
        slot.set_line(1, cell.special_label)
        slot.set_line_icon(1, self._store, WEAPON_ICON_FAMILY, cell.special_key)
        slot.set_footnote(cell.footnote)
        slot.set_selected(cell.member_id == self._selected)

    def _empty_text(self) -> str:
        context = self._context
        if context is None:
            return NOT_IN_EDITOR
        return squad_empty_text(context.data, context.unit_id)

    # ------------------------------------------------------------------ #
    # 按钮状态
    # ------------------------------------------------------------------ #

    def _infantry(self) -> bool:
        """这个单位有没有班成员这一套（没有就只能看，不能加）。"""
        context = self._context
        if context is None:
            return False
        return squad_loadout(context.data, context.unit_id) is not None

    def _sync_buttons(self) -> None:
        # ❌ 按钮上**不挂 tooltip**（用户 2026-10-03：「我叫你不要在中间显示悬浮窗注释 /
        #    我要你在右边显示悬浮窗注释」）—— 两颗按钮只按能不能点（enabled）说话。
        infantry = self._infantry()
        self._add_button.setEnabled(infantry)
        copy_ready = infantry and self._selected is not None
        self._copy_button.setEnabled(copy_ready)


#: 还没量过宽度时，用这个宽度去估"内容要多高"（猜宽一点没关系：猜宽了行数少、
#: 高度只会偏小，所以这里故意按**窄栏**估 —— 先给一个够大的高度，第一次真 resize 就校正）。
_GUESS_WIDTH = 640


class _GridHost(QScrollArea):
    """装着格子矩阵，但**自己不滚** —— 高度 = 这个宽度下内容要几行，滚动交给外层中栏。

    为什么还得是 QScrollArea：BenchGrid 的 FlowLayout 是 hasHeightForWidth=True 的，
    要有人按"当前宽度"给它铺格子（见 widgets/bench_kit.py::BenchGrid 的提醒）。

    ❗ 但 QScrollArea 默认的 sizeHint 是个很矮的高度，会把 14 格压成看不见的一行，
    而且它自带一根纵向条 —— **两层滚动条同时出现是用户会骂的那种小问题**
    （Lead 2026-10-01 口径：BenchPage 中栏已经有一层 QScrollArea，四块一起滚）。所以：

    - 两根条一律 AlwaysOff（这一层永远没有可滚的东西）；
    - hasHeightForWidth / heightForWidth / sizeHint / minimumSizeHint 一路透给 FlowLayout；
    - resize 之后把最小高度钉到"当前宽度下内容多高" —— 保证 14 格一屏全看得见。
    """

    def __init__(self, grid: BenchGrid, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = grid
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setWidget(grid)

    # ---- 高度跟着内容走 ------------------------------------------------ #

    def grid(self) -> BenchGrid:
        return self._grid

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 的驼峰
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 的驼峰
        """这个宽度下内容要多高（帧宽减掉 —— NoFrame 时就是原样）。"""
        inner = max(1, int(width) - 2 * self.frameWidth())
        return int(self._grid.heightForWidth(inner))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 的驼峰
        width = self.width() or self._grid.sizeHint().width()
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 的驼峰
        return QSize(0, self.heightForWidth(self.width() or _GUESS_WIDTH))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:
        """最小高度 = 当前宽度下的内容高度。

        宽度还是 0（还没进布局）时不动手 —— 那一刻算出来的是"每格一行"的荒唐高度，
        钉下去会让首帧抖一下；等第一次真 resize 再校正。
        """
        width = self.width()
        if width <= 0:
            return
        target = self.heightForWidth(width)
        if target > 0 and self.minimumHeight() != target:
            self.setMinimumHeight(target)
            self.updateGeometry()


__all__ = ["HINT", "NOT_IN_EDITOR", "SLOT_HEIGHT", "SLOT_WIDTH", "TITLE", "SquadBench"]
