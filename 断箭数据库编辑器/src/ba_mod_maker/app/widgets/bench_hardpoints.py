"""编成台 ①（**所有载具**）：**21 个挂载槽**的格子矩阵。**一个都不折叠。**

覆盖飞机 / 直升机 / 地面车 / 舰船四类载具（用户 2026-11-11：「让所有载具都显示这些挂载槽」）。

出处：`docs/交接-2026-10-01-单位编成台设计.md` §3 口径 5 ＋ 设计图
`docs/设计稿-编成台-飞机.png` 的中栏 ①（F-15E Strike Eagle / Units#287）。

    ① 挂载槽                              [＋ 加一个挂载]
    ┌ 1 ×1 ┐┌ 2 ×1 ┐…┌ 6 ×4 ┐          <- 序号 + ×数量，铺满自动换行
    │ Sidewinder │                       <- 装的那件武器（空格画"归哪个改装槽管"）
    └────────┘
    21 个挂载槽，装了 12 件 · 不属于任何改装槽：0、20

## 两种"折叠"别混（2026-10-02）

- **块本身**（`BenchSection`）= 可折、**默认收起**（用户口径：「折叠栏默认是折叠状态」）；
- **21 个格子** = **一个都不折**（口径 5：挂载图一屏全看得见才有意义）——
  格子不在任何折叠容器里，收起只收"整块"。为此 `_grid_box` / `_foot` / 候选宿主
  都挂在 section **主体**里（从前挂在 root 上，点了抬头 ▸ 也藏不住那 21 格）。

## 三条硬纪律（与 bench_slots / bench_squad / bench_loadout 同一套）

1. 这一层**一个字节都不写库** —— 只发意图信号；写库一律经 `EditorContext.set_value`
   （在 `HardpointsBench._apply` 里，由页面注入的 context 执行）。
   取数与规则全在 `app/viewmodel/bench_hardpoints.py`（零 Qt）；
2. **不弹窗**：成功 / 没有改动 / 解释都走 `status_message` 一句话；
3. **改一格不许全量重建**：结构没变走 `reload_values()`（一个控件都不重建），
   只有插 / 删 / 换挂载那种结构变化才走 `refresh()`。

## 就地刷新怎么判"结构变了"

`structure_key()` = 逐格 `(slot, state, text, note, free)`（**"这格画出来是什么"的指纹**）。
换一件挂载、清一个槽、装/卸一批弹都会改它 —— 于是 21 个格子各自 `apply_cell`
就地改字；指纹一样时连字都不重写（`_HardpointCard.apply_cell` 自己比）。

## 角色（qss）只用已存在的

这一层**实际用到**的只有这些（全在 `theme/qss.py` 里已有）：

```
bench-section / bench-section-title / bench-section-hint   <- BenchSection 自己带的
bench-action          <- 抬头那颗「＋ 加一个挂载」+ 格子右上角的 ✕
bench-body            <- 候选列表宿主
bench-grid            <- BenchGrid（FlowLayout，21 格自动换行）
bench-slot            <- 一格
bench-slot-index      <- 格子左上角那个槽号
bench-slot-line       <- 格子主字
entry-id              <- 格子右上角那一小段（`×N` / `空` / 「不属于任何改装槽」）
bench-row-tool        <- 那一小段上的 ✕（与 BenchRow 的行尾小按钮同一档）
muted                 <- 格子矩阵下面那句小结
```

⚠️ **空档的暗色**没有现成角色，这一层用 **`setEnabled(False)`**（Qt 自带的 disabled 色）
实现 —— **没有新建 role、没有改 qss**。

> 📌 **别去加那条 role**（Lead 核 task-9 时的裁定）：设计图上空档就是**同一套样式**
> （"7 Fuselage 空"没有特殊底色），而加一条新 role 要动 `theme/qss.py`
> ＋ 有被别的块误用的风险，收益只是一个底色 —— **不值**。
> `HardpointCell.state`（`unclaimed` / `empty` / `on`）留着是为了让这条口径
> **随时可改**：真要上正统 muted 色，把 `_HardpointCard.apply_cell` 末尾那两行
> `setEnabled` 换成 `setProperty("state", cell.state)` + `restyle(self)` 即可。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import bench_hardpoints as vm
from ba_mod_maker.app.viewmodel.bench_hardpoints import HardpointCell, HardpointView
from ba_mod_maker.app.widgets.bench_kit import (
    BenchAction,
    BenchEmpty,
    BenchGrid,
    BenchSection,
    restyle,
)
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.edit.refs import CellRef

#: 抬头 = **折叠栏的名字**（用户 2026-11-11：「这个折叠栏的名字就叫『挂载槽』」——
#: 「（一个都不折叠）」那截不要了）；圈码由 bench_page._number_sections 按位置现生成。
TITLE = "① 挂载槽"
#: 抬头右边那句灰字（设计图原文）。
HINT = "当前 N 个改装槽选中项各自占的槽 —— 空的也画出来"
#: 抬头那颗主动作按钮上的字（设计图：`＋ 加一个挂载`）。
ADD_TEXT = "＋ 加一个挂载"

#: 一格的尺寸。设计图那一栏 7 列一行；152 与步兵 / 载具的格子同档（`SlotCard` 默认）。
SLOT_WIDTH = 152
SLOT_HEIGHT = 62

#: 还没进单位时主体那句灰字（**不是空框** —— 空框看不出是空的还是没画出来）。
NO_UNIT_TEXT = "（还没有打开单位）"

#: 格子上主字最多几个字（超了写省略号，全文进 tooltip）。
#: ❗ **按字数截，不按字体度量截** —— 与 `readonly_card.CHIP_NAME_LIMIT`（12）同一档、
#: 同一个理由：格子是定宽的 `QLabel`，用 `fontMetrics().elidedText()` 的话
#: `cell_texts()` 会随**装了什么字体**变化（离屏裸字体量出来是「CBU-87 Roc…」），
#: 断言"每格写了什么"就成了断言字体。按字数截是确定的。
CELL_NAME_LIMIT = 14


def structure_key(view: HardpointView | None) -> tuple:
    """整块的**结构署名**：逐格"画出来是什么"。

    `None`（没接 context）也进署名 —— 不这么办的话"没打开单位"切到"21 个空格"
    会签一样、上一句灰字留着不换（`bench_loadout.block_key` 踩过同一个坑）。
    """
    if view is None:
        return (NO_UNIT_TEXT,)
    return tuple((cell.slot, cell.state, cell.text, cell.note, cell.free) for cell in view.cells)


class _HardpointCard(QFrame):
    """一格：左上序号 + 右上那一小段字 + 行尾 ✕；下面一行"装的是什么"。

    ❗ 为什么**不直接用 `bench_kit.SlotCard`**：它没有 ✕（`remove_requested` 只服务
    `SlotCard` 自己的 `_remove`，而那个按钮在这一档里被固定宽度 / 位置的设计挡着），
    而这一块"✕ = 把 `Options.TurretNId` 清 0"是**必要交互**（口径 1：只有增和删）。
    所以这里按同一套视觉（`bench-slot` / `bench-slot-index` / `bench-slot-line` /
    `entry-id` / `bench-row-tool`）自己摆 —— **一个 role 都没有新造**。
    """

    #: 点了这一格（选中它 -> 右栏换成它的全部列）。
    activated = Signal()
    #: 点了右上角的 ✕（清掉这个槽的挂载）。
    remove_requested = Signal()

    def __init__(self, cell: HardpointCell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-slot")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(SLOT_WIDTH, SLOT_HEIGHT)
        self.slot = cell.slot
        self._cell = cell
        #: 就地刷新比对的缓存（`apply_cell` 里"没变就一个字都不写"）。
        self._shown: HardpointCell | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.sm, TOKENS.spacing.xxs, TOKENS.spacing.xs, TOKENS.spacing.xs
        )
        root.setSpacing(TOKENS.spacing.xxs)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        self._index = QLabel(str(cell.slot))
        self._index.setProperty("role", "bench-slot-index")
        # 右上那一小段（`×N` / `空` / 「不属于任何改装槽」）—— 用与中栏同一套 Id 小徽章
        # 的弱化样式（`entry-id`），不另发明一个 role。
        self._note = QLabel(cell.note)
        self._note.setProperty("role", "entry-id")
        self._remove = BenchAction("✕", tooltip=f"清掉槽 {cell.slot} 的挂载（只清这一格）")
        self._remove.setProperty("role", "bench-row-tool")
        self._remove.setFixedHeight(14)
        self._remove.clicked.connect(self.remove_requested)
        top.addWidget(self._index, 0, Qt.AlignVCenter)
        top.addStretch(1)
        top.addWidget(self._note, 0, Qt.AlignVCenter)
        top.addWidget(self._remove, 0, Qt.AlignVCenter)
        root.addLayout(top)

        self._main = QLabel(cell.text)
        self._main.setProperty("role", "bench-slot-line")
        self._main.setWordWrap(False)
        self._main.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(self._main, 0)
        root.addStretch(1)

        self.apply_cell(cell)

    # ------------------------------------------------------------------ #

    @staticmethod
    def clip(text: str) -> str:
        """格子上的短字：超过 `CELL_NAME_LIMIT` 个字就截 + 省略号（**按字数，不按字体**）。

        与 `readonly_card._clip`（CHIP_NAME_LIMIT = 12）同一个做法 —— 见那个常量的说明。
        """
        return text if len(text) <= CELL_NAME_LIMIT else f"{text[:CELL_NAME_LIMIT]}…"

    def apply_cell(self, cell: HardpointCell) -> None:
        """**就地**把这一格换成最新的一份（不新建、不销毁任何控件）。

        ❗ 判据 = **这一格画出来的每一个输入**（`HardpointCell` 整份，dataclass 自带
        `__eq__`）：一样就一个字节都不写 —— 与 `EntryRow.refresh` 同一条
        （用户提过三回的"改一格别全闪"）。
        """
        changed = cell != self._shown
        self._shown = cell
        self.slot = cell.slot
        self._cell = cell
        self._index.setText(str(cell.slot))
        self._note.setText(cell.note)
        self._remove.setVisible(cell.occupied)
        self._main.setText(self.clip(cell.text))
        # 空档（含"不属于任何改装槽"）用 disabled 色；装了东西的是正常前景色。
        # ⚠️ 不新建 role：Qt 自带 disabled 就是弱化色。**这一条是 Lead 裁定的**
        #    （加 role 要动 qss ＋ 有被别的块误用的风险，收益只是一个底色 —— 不值）。
        #    `cell.state` 三档留着，真要用时把下面两行换成
        #    `setProperty("state", cell.state)` + restyle(self) 即可。
        self._main.setEnabled(cell.loaded)
        self._note.setEnabled(cell.loaded or not cell.unclaimed)
        if changed:
            restyle(self)

    @property
    def cell(self) -> HardpointCell:
        return self._cell

    def main_label(self) -> QLabel:
        """格子主字那一个 QLabel（用例按它取文字 / 判 disabled）。"""
        return self._main

    def note_label(self) -> QLabel:
        return self._note

    def index_label(self) -> QLabel:
        return self._index

    def remove_button(self) -> BenchAction:
        return self._remove

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        """点格子本体（✕ 是子按钮，不会走到这里）-> 选中它。"""
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(event)


#: 还没量过宽度时，用这个宽度去估"内容要多高"（与 bench_squad._GridHost 同一条口径：
#: 猜宽一点没关系 —— 猜宽了行数少、高度只会偏小）。
_GUESS_WIDTH = 640


class _GridHost(QScrollArea):
    """装着格子矩阵，但**自己不滚** —— 高度 = 这个宽度下内容要几行，滚动交给外层中栏。

    照 `bench_squad._GridHost` 抄：`BenchGrid` 的 FlowLayout 是 `hasHeightForWidth=True` 的，
    必须放在 QScrollArea 里才撑得开；但**不能出现两层滚动条**
    （中栏已经有一层）。所以两根条一律 AlwaysOff，高度一路透给 FlowLayout。
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

    def grid(self) -> BenchGrid:
        return self._grid

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 的驼峰
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 的驼峰
        inner = max(1, int(width) - 2 * self.frameWidth())
        return int(self._grid.heightForWidth(inner))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 的驼峰
        width = self.width() or self._grid.sizeHint().width()
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 的驼峰
        return QSize(0, self.heightForWidth(self.width() or _GUESS_WIDTH))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        super().resizeEvent(event)
        width = self.width()
        if width <= 0:
            return
        target = self.heightForWidth(width)
        if target > 0 and self.minimumHeight() != target:
            self.setMinimumHeight(target)
            self.updateGeometry()


class HardpointsBench(QWidget):
    """编成台 ①（所有载具：飞机 / 直升机 / 地面车 / 舰船）：21 个挂载槽，一个都不折叠。

    对外接口（Lead 定的四件套 + 三信号，四块编成台一模一样）::

        row_selected   = Signal(str, int)
        status_message = Signal(str)
        set_context(context) / set_icons(store) / refresh() / reload_values()
        cells() / cell_texts()      # 用例读数：21 个格子控件 / 它们的文字
    """

    #: 选中了某一格（"Turrets", 炮塔 Id）—— 装了东西时是**那座炮塔**；
    #: 空格子发的是**那条选项**（Options 行 Id），因为改槽位就在它的 TurretNId 上。
    row_selected = Signal(str, int)
    #: 一句人话（成功 / 没有改动 / 解释），**绝不弹窗**。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = None
        self._icons = None
        self._view: HardpointView | None = None
        self._cards: tuple[_HardpointCard, ...] = ()
        self._grid: BenchGrid | None = None
        self._host: _GridHost | None = None
        self._picker: RowPicker | None = None
        self._picker_table = ""
        #: 建的时候记下来的那一份结构署名（不是现算的 —— 见 structure_key 的说明）。
        self._drawn_key: tuple = ()
        self._selected: int = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOKENS.spacing.xs)

        self._section = BenchSection(TITLE, HINT)
        self._add = self._section.add_action(
            ADD_TEXT,
            primary=True,
            tooltip="从 Turrets 里挑一座挂到第一个空槽（就地冒候选，不弹对话框）",
        )
        # ❗ 必须甩掉 Qt 塞进来的 checked(bool)：直接连 `self.begin_add` 的话，那个
        #    `False` 会落到 `slot` 参数上变成 **0**，于是「＋ 加一个挂载」永远去查**槽 0**。
        #    实测（2026-10-02，单位 46 Ka-52）：第一个空槽是 **3**，而槽 0 是
        #    「不属于任何改装槽」（`free=False`）⇒ 永远弹「槽 0 已经占着了 —— 先 ✕ 掉它再挂」，
        #    21 个格子一个都挂不上 —— 用户报的"加一个挂载不知道怎么用"就是它。
        self._add.clicked.connect(lambda _=False: self.begin_add())
        root.addWidget(self._section)

        #: 候选列表在**这一块下面**就地冒出来（用户口径：不需要这么多弹窗）。
        #: ❗ 2026-10-02（折叠）：候选宿主 / 21 格矩阵 / 小结**全部搬进 section 主体**。
        #:    从前它们挂在 root 上（主体之外），而 `BenchSection` 收起只 hide 主体 ——
        #:    于是点了抬头 ▸ 也藏不住这 21 格（"折块"名存实亡）。搬的只是**容器**，
        #:    格子的排布与间距一个字节没动（口径 5：21 个格子本身一个都不折）。
        #:    这一块不用 `clear_body()`（清格子走自己的 `_grid_layout`），宿主不会被连坐。
        #:    顺序与搬之前**逐字一致**：候选 → 矩阵 → 小结。
        self._picker_host = QWidget()
        self._picker_host.setProperty("role", "bench-body")
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(
            TOKENS.spacing.sm, 0, TOKENS.spacing.sm, TOKENS.spacing.sm
        )
        self._picker_layout.setSpacing(0)
        self._section.body_layout().addWidget(self._picker_host)
        self._picker_host.setVisible(False)

        #: 格子矩阵的**常驻**宿主：21 个格子跨刷新留着（`reload_values` 就不重建它们）。
        self._grid_box = QWidget()
        self._grid_layout = QVBoxLayout(self._grid_box)
        self._grid_layout.setContentsMargins(
            TOKENS.spacing.sm, 0, TOKENS.spacing.sm, TOKENS.spacing.xs
        )
        self._grid_layout.setSpacing(TOKENS.spacing.xs)
        self._section.body_layout().addWidget(self._grid_box)

        self._foot = QLabel("")
        self._foot.setProperty("role", "muted")
        self._foot.setContentsMargins(TOKENS.spacing.sm, 0, TOKENS.spacing.sm, TOKENS.spacing.sm)
        self._foot.setVisible(False)
        self._section.body_layout().addWidget(self._foot)
        root.addStretch(1)

        self.refresh()

    # ------------------------------------------------------------------ #
    # 四件套
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上 `EditorContext`（`None` = 清空、画空态）。"""
        self._context = context
        self._selected = -1
        self.close_picker()
        self.refresh()

    def set_icons(self, store) -> None:
        """接上**唯一**那份图标缓存。

        这一块现在**不画图标**（设计图的 21 格里只有字与 `×数量`），所以只保存 ——
        留着它是因为四块编成台共用同一条装配接口（`bench_slots.StatsBench.set_icons` 同理）。
        """
        self._icons = store

    def refresh(self) -> None:
        """数据变了重画（**幂等**：连调两次得到同样的 21 格，不叠控件、不漏控件）。

        - 结构没变（`structure_key` 一样）-> 走 `reload_values()`，**一个控件都不重建**；
        - 结构变了（`set_context` 那次、或换了挂载）-> 重建那 21 个格子。
        """
        context = self._context
        if context is None:
            self._view = None
        else:
            # 读之前先丢缓存：Options / Modifications / Turrets 都按列建过倒排索引，
            # 不丢就照旧那一份画（`bench_squad.refresh` 同一个理由）。
            context.data.invalidate(vm.OPTIONS_TABLE)
            context.data.invalidate(vm.MODIFICATIONS_TABLE)
            self._view = vm.hardpoint_view(context.data, context.unit_id)
        fresh_key = structure_key(self._view)
        if self._cards and self._view is not None and fresh_key == self._drawn_key:
            self.reload_values()
            return
        self._rebuild()

    def reload_values(self) -> None:
        """**就地**把 21 格的字换成最新的 —— 一个控件都不重建（用户提过几回的那条）。

        三条边界（与 `bench_squad.reload_values` / `bench_slots.reload_values` 同一条）：

        - 21 个格子对不上 / 签名变了 -> 退回 `refresh()` 整块重建；
        - 某一格对不上 -> **跳过它**，不为它重建（外层马上会 refresh）；
        - 全对得上才回写 `_view`，这样 `_cards` 与 `_view.cells` 永远等长同序。
        """
        context = self._context
        if context is None:
            # 没接 context（空态）：签名也要跟着走 —— 上一份是"21 格"时它必须换回空态。
            if self._drawn_key != structure_key(None):
                self.refresh()
            return
        if not self._cards:  # pragma: no cover - refresh 建不出来才会这样
            self.refresh()
            return
        context.data.invalidate(vm.OPTIONS_TABLE)
        context.data.invalidate(vm.MODIFICATIONS_TABLE)
        view = vm.hardpoint_view(context.data, context.unit_id)
        if structure_key(view) != self._drawn_key:
            self.refresh()
            return
        self._view = view
        for card, cell in zip(self._cards, view.cells, strict=True):
            card.apply_cell(cell)
        self._sync_foot()
        self._sync_selection()

    # ------------------------------------------------------------------ #
    # 读数（用例与装配用 —— 顺序 = 槽号 0..20）
    # ------------------------------------------------------------------ #

    def cells(self) -> tuple[_HardpointCard, ...]:
        """21 个格子控件（**顺序 = 槽号 0..20**）。空态 / 没接 context 时是空元组。"""
        return self._cards

    def cell_texts(self) -> tuple[str, ...]:
        """21 个格子现在写着的文字（主字，已省略号截断）—— 一条断言查完"写了什么"。"""
        return tuple(card.main_label().text() for card in self._cards)

    def cell_labels(self) -> tuple[QLabel, ...]:
        """21 个格子主字那一个 QLabel（用例要判 disabled / tooltip 时用）。"""
        return tuple(card.main_label() for card in self._cards)

    def notes(self) -> tuple[str, ...]:
        """21 格右上角那一小段字（`×N` / `空` / 「不属于任何改装槽」）。"""
        return tuple(card.note_label().text() for card in self._cards)

    def grid(self) -> BenchGrid | None:
        """格子矩阵那个流式容器（`BenchGrid`，21 格自动换行）。"""
        return self._grid

    def view(self) -> HardpointView | None:
        """现在画着的那一份取数结果（`None` = 没接 context）。**控件里存的这份**，不现算。"""
        return self._view

    def section(self) -> BenchSection:
        return self._section

    def add_button(self) -> BenchAction:
        """抬头那颗「＋ 加一个挂载」。"""
        return self._add

    def foot_text(self) -> str:
        return self._foot.text() if self._foot.isVisibleTo(self) else ""

    def selected_slot(self) -> int:
        """现在选中的槽号（-1 = 一个都没选）。"""
        return self._selected

    def picker(self) -> RowPicker | None:
        return self._picker

    def picker_visible(self) -> bool:
        return self._picker is not None and self._picker_host.isVisibleTo(self)

    # ------------------------------------------------------------------ #
    # 画
    # ------------------------------------------------------------------ #

    def _rebuild(self) -> None:
        """重建 21 个格子（**结构变了**才走这条）。"""
        self._clear_slots()
        self._drawn_key = structure_key(self._view)
        if self._view is None:
            self._grid_layout.addWidget(BenchEmpty(NO_UNIT_TEXT))
            self._foot.setVisible(False)
            return
        grid = BenchGrid()
        cards: list[_HardpointCard] = []
        for cell in self._view.cells:
            card = _HardpointCard(cell)
            card.activated.connect(lambda slot=cell.slot: self._emit_select(slot))
            card.remove_requested.connect(lambda slot=cell.slot: self.clear_slot(slot))
            grid.add_cell(card)
            cards.append(card)
        host = _GridHost(grid)
        self._grid = grid
        self._host = host
        self._cards = tuple(cards)
        self._grid_layout.addWidget(host)
        self._sync_foot()
        self._sync_selection()

    def _clear_slots(self) -> None:
        """把上一批格子摘干净（`detach` = 先 hide 再 setParent(None)，别闪顶层小窗）。"""
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                detach(widget)
        if self._grid is not None:
            self._grid.clear()
        self._grid = None
        self._host = None
        self._cards = ()

    def _sync_foot(self) -> None:
        text = self._view.summary if self._view is not None else ""
        self._foot.setText(text)
        self._foot.setVisible(bool(text))

    def _sync_selection(self) -> None:
        for card in self._cards:
            card.setProperty("selected", "true" if card.slot == self._selected else "false")
            restyle(card)

    # ------------------------------------------------------------------ #
    # 选中 / 两个意图
    # ------------------------------------------------------------------ #

    def select_slot(self, slot: int) -> None:
        """选中某一格（**只改控件里那份状态 + 高亮**，不发信号）。"""
        self._selected = int(slot)
        self._sync_selection()

    def clear_selection(self) -> None:
        """清掉选中（页面在**别的块**选中别的东西时调它）—— 不发信号、不写库。"""
        if self._selected < 0:
            return
        self._selected = -1
        self._sync_selection()

    def _emit_select(self, slot: int) -> None:
        """点一格：先选中它，再按这一格是"装了东西"还是"空"发对应那一行。

        - 装了东西 -> 发**那座炮塔**（`("Turrets", turret_id)`）—— 右栏看它的全部列；
        - 空 -> 发**那条选项**（`(vm.OPTIONS_TABLE, owner_option_id)`）—— 改槽位就在它的
          `TurretNId` 那一格上（与 `readonly_card._slot_chip` 同一条口径）。
        不归任何改装槽的格：**只选中，不发信号**（没有可看的行 —— 不假装有）。
        """
        cell = self._cell_of(slot)
        if cell is None:
            return
        self.select_slot(slot)
        if cell.loaded:
            self.row_selected.emit(vm.TURRETS_TABLE, cell.turret_id)
        elif cell.owner_option_id > 0:
            self.row_selected.emit(vm.OPTIONS_TABLE, cell.owner_option_id)
        else:
            self._say(f"槽 {cell.slot} 不属于任何改装槽 —— 没有可以打开的那一行")

    def _cell_of(self, slot: int) -> HardpointCell | None:
        view = self._view
        return None if view is None else view.cell_at(slot)

    def _say(self, text: str) -> None:
        """说一句人话（状态栏）—— 成功 / 没有改动都不弹窗。"""
        self.status_message.emit(str(text))

    # ------------------------------------------------------------------ #
    # 动作：＋ 加一个挂载 / ✕ 清掉这个槽
    # ------------------------------------------------------------------ #

    def begin_add(self, slot: int | None = None) -> bool:
        """「＋ 加一个挂载」：就地冒候选列表（候选 = `Turrets` 全表）。

        返回值：有没有接下来（`False` = 没接 context / 没有空槽，外面不用等信号）。
        """
        context = self._context
        view = self._view
        if context is None or view is None:
            self._say(NO_UNIT_TEXT)
            return False
        if self._picker is not None:  # 再点一下 = 收手（与 bench_slots 同一手感）
            self.close_picker()
            return False
        target = view.first_free_slot if slot is None else int(slot)
        if target < 0:
            self._say(vm.NO_FREE_SLOT_MESSAGE)
            return False
        cell = view.cell_at(target)
        if cell is None or not cell.free:
            self._say(f"槽 {target} 已经占着了 —— 先 ✕ 掉它再挂")
            return False
        picker = RowPicker(context.instance, vm.TURRETS_TABLE, parent=self._picker_host)
        picker.picked.connect(lambda turret_id, slot=target: self._on_picked(int(turret_id), slot))
        picker.cancelled.connect(self.close_picker)
        self.close_picker()
        self._picker = picker
        self._picker_table = vm.TURRETS_TABLE
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        picker.start()
        self.select_slot(target)
        self._say(f"给槽 {target} 挑一座炮塔 / 挂架（挂着的那座会替换掉这一格的空档位）")
        return True

    def _on_picked(self, turret_id: int, slot: int) -> None:
        """候选里点了一座炮塔：先收起列表，再写那一格。"""
        self.close_picker()
        self.place(slot, turret_id)

    def place(self, slot: int, turret_id: int) -> int:
        """把 `turret_id` 挂到 `slot`（**写库经 `EditorContext.set_value`**）。

        返回"写了几格"（0 = 一个字节都没写 —— 没接 context / 官方拒绝）。
        """
        context = self._context
        view = self._view
        if context is None or view is None:
            self._say(NO_UNIT_TEXT)
            return 0
        result = vm.place_writes(view, turret_id, slot=slot)
        if not result.changed:
            self._say(result.message)
            return 0
        self._apply(result)
        name = context.data.name_of(vm.TURRETS_TABLE, int(turret_id))
        self._say(f"{result.message}（{name}）")
        self.refresh()
        return len(result.writes)

    def add_to_first_free(self, turret_id: int) -> int:
        """不带槽号的入口（用例 / 将来的键盘路径）：「＋」挂到**第一个空槽**。"""
        view = self._view
        if view is None:
            self._say(NO_UNIT_TEXT)
            return 0
        target = view.first_free_slot
        if target < 0:
            self._say(vm.NO_FREE_SLOT_MESSAGE)
            return 0
        return self.place(target, turret_id)

    def clear_slot(self, slot: int) -> int:
        """「✕ 清掉这个槽的挂载」：把那一格的 `Options.TurretNId` 写 0。

        返回"写了几格"（0 = 本来就空 / 没接 context）。
        """
        context = self._context
        view = self._view
        if context is None or view is None:
            self._say(NO_UNIT_TEXT)
            return 0
        result = vm.clear_writes(context.data, view, slot)
        if not result.changed:
            self._say(result.message)
            return 0
        self._apply(result)
        self._say(result.message)
        self.refresh()
        return len(result.writes)

    def _apply(self, result: vm.HardpointWrite) -> None:
        """把 VM 算出来的那几格写进账本（**唯一写入口**：`EditorContext.set_value`）。"""
        context = self._context
        if context is None:  # pragma: no cover - 调用方都守过
            return
        for row_id, column, value in result.writes:
            context.set_value(CellRef(vm.OPTIONS_TABLE, row_id, column), value)

    # ------------------------------------------------------------------ #
    # 候选列表（就地冒，不弹对话框）
    # ------------------------------------------------------------------ #

    def close_picker(self) -> None:
        """收起候选列表（点中 / Esc / 换一块都走它）。"""
        picker = self._picker
        self._picker = None
        self._picker_table = ""
        if picker is not None:
            self._picker_layout.removeWidget(picker)
            detach(picker)
        self._picker_host.setVisible(False)


__all__ = [
    "ADD_TEXT",
    "HINT",
    "NO_UNIT_TEXT",
    "SLOT_HEIGHT",
    "SLOT_WIDTH",
    "TITLE",
    "HardpointsBench",
    "structure_key",
]
