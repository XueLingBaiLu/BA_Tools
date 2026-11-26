"""中栏「这一行的上下文」第一块：**这把枪用哪些弹、各多少发**。

用户口径（2026-09-30 实机反馈，原话）：

    我需要能调武器使用的弹药，拥有多少弹药

数据落点在 `WeaponAmmunitions`：一行 = 本单位的一把枪 × 一种弹，带 `Quantity`。
所以它**不是** `Weapons` 那一行的列（Weapons 那 40 列里没有弹）——
它是"这一行的上下文"，挂在中栏词条**下面**，走
`RowEntriesPanel.set_cells(blocks=...)` 那条通路（见 widgets/row_entries.py）。

⚠️ **别把"这一格里的数"当成"卡片上的数"**：卡片画的携弹量 =
`Quantity × Ammunitions.HUDMultiplier × 本单位装了几门这门枪`
（实测：7.62x54mm 那门机枪 400 × 5 = 显示 2000；AK 一个班 5 人 × 80 × 3 = 1200）。
这条口径在本仓出现过三次（core/fields/field_labels.json、editor/pages/weapon_page.py、
editor/pages/ammo_page.py），所以这一块**两个数都写**：可改的是 Quantity，
卡片上那个数写在它右边（不一样时才出现）。

四条口径（都是产品里已经定过的，别自己发明）：

1. **只发意图，不写盘** —— 改数量 / 加一条 / 删一条全部发信号，
   落盘在外层（EditorContext），与树上那些按钮同一套分工；
2. **不弹对话框** —— 「＋ 加一种弹」在这一块里**就地**冒候选列表
   （app/widgets/row_picker.py，与树上那颗＋是同一个控件）；
3. **空值 / 缺列要说实话** —— 读不到 Quantity 那一格就不假装能改（按钮置灰 + 说明）；
4. 颜色只从 app/theme/tokens.py 来；Emoji 只放在有语义的地方。

为什么数量那一格用 `entry-value` 这个 role：它和中栏词条行里的"值"是**同一种东西**
（点一下就地改），复用同一套 QSS 才不会出现"这块的按钮长得跟别处不一样"。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_entries import (
    EDITOR_HEIGHT,
    INDEX_COLUMN_WIDTH,
    NAME_COLUMN_WIDTH,
    ROW_HEIGHT,
)
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.refs import CellRef

#: 一张携弹行 = 这些列（都来自 WeaponAmmunitions）。
TABLE = "WeaponAmmunitions"
#: 数量那一列（点一下就地改的就是它）。
QUANTITY_COLUMN = "Quantity"
#: 弹种外键那一列。
AMMO_COLUMN = "AmmunitionId"

#: 一行的高度 —— 与中栏词条行**同高**，两块贴在一起才不像两块拼的。
#:
#: ❗ 这里一度写着 24（那时词条行也是 24）。词条行在 2026-09-30 改成
#:    `EDITOR_HEIGHT + 2 = 28` 之后这一块没跟着改，于是：
#:    ① 弹药行比上面的词条行矮 4px，两块贴在一起就不像一块
#:       （用户口径 2026-09-30 实机：「这一块看起来比较怪，跟上面的保持一致」）；
#:    ② 它自己的就地编辑浮层拿的是 `LINE_HEIGHT - 2 = 22`，比 `EDITOR_HEIGHT` 少 4px
#:       —— 正是词条行那边报过两次的「编辑栏被裁」。
#:    所以**从 row_entries 取，不在这里写数**：那一份有 test_theme 的守卫，
#:    这一份再写一遍就是第二个真相源（上一条注释写着 ROW_HEIGHT、值是 24 就是教训）。
LINE_HEIGHT = ROW_HEIGHT


def carries_weapon(data, unit_id: int, weapon_id: int) -> bool:
    """这个单位挂没挂这把枪？没挂 = 你在看一行模板，中栏**不给**弹药块。

    ❗ 判据是"本单位的武器链里有没有它"，不是"有没有携弹行"：
    一把枪挂上去了但一条弹都没有，是**有效状态**（该给「＋ 加一种弹」）；
    而翻到别的单位用的模板行时给一块"这枪没弹"的红字，是噪音。

    读之前先丢缓存 —— 刚插/删过携弹行时不丢就会照旧名单答（Rows.where2 是缓存的）。
    """
    data.invalidate(TABLE)
    if data.weapon_ammo(unit_id, weapon_id):
        return True
    try:
        return int(weapon_id) in set(data.weapon_ids(unit_id))
    except Exception:  # noqa: BLE001 - 武器链算不出来就当没挂（宁可不给按钮）
        return False


class AmmoLine(QFrame):
    """一条携弹行：〔序号列留空〕│ 弹种名 │ ×数量 │ 🗑 │ WeaponAmmunitions #Id。

    2026-10-19：这里原来还有一颗「🔁 换弹种」—— 用户命令删掉「换」（AGENTS §0.10）。
    

    ❗ **列线跟着上面的词条行走**（`widgets/row_entries.EntryRow`）：名字从同一个 x
    开始、数量落在「值」那一格上、工具与徽章贴右边。它一度是自己一套（名字顶格、
    数量定宽 64 挤在中间）—— 用户口径 2026-09-30 实机：「这一块看起来比较怪，
    跟上面的保持一致」。
    """

    #: 点了弹种名（-> 中栏换成 Ammunitions 那一行）。
    link_activated = Signal(str, int)
    #: 点了数量那一格 —— 这一块自己去开就地编辑（弹药行不在词条列表里，
    #: EntryList.open_editor 找不到它，所以浮层由这一块自己挂，见 AmmoBlock）。
    #: 点了数量那一格（就地改浮层由这一块自己开）。
    quantity_clicked = Signal()
    #: 鼠标进出这一行（参数：这一行, 现在是不是悬停）—— **只报告，不自己改**。
    #: 唯一的写入口是 AmmoBlock.set_hovered_line()：先灭上一行、再点新的，
    #: 于是"同一时刻只有一行亮"是结构保证（与词条行那套完全一致）。
    hover_changed = Signal(object, bool)
    #: 点了「🗑 删掉这一条携弹行」。
    remove_requested = Signal()

    def __init__(
        self,
        *,
        row_id: int,
        ammo_id: int,
        name: str,
        quantity: int,
        quantity_cell: FieldCell | None,
        ammo_ref: CellRef,
        hud_total: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # ❗ 与中栏词条行**同一个 role**：整行高亮那条 QSS（[mid-panel] 块里的
        #    QWidget[role="entry-line"][hover="true"]）直接用，不再单独写一套 ——
        #    用户报的"弹药栏的高亮还是老一套"就是因为我先前给弹种名那颗按钮
        #    自己写了一条 :hover，亮出来只有半个格子。
        self.setProperty("role", "entry-line")
        self.setProperty("hover", "false")
        self._hovered = False
        self.setFixedHeight(LINE_HEIGHT)
        self.row_id = int(row_id)
        self.ammo_id = int(ammo_id)
        #: 这一格的 AmmunitionId 落在哪。
        self.ammo_ref = ammo_ref
        self.quantity_cell = quantity_cell
        #: 现在挂在数量那一格上的编辑浮层（没有就是 None）。
        self._editor: QWidget | None = None

        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.sm)
        self._box = box

        # ❗ 序号那一列**留空**：弹药行不在词条列表里、没有序号，但列线要对得上
        #    —— 名字必须从上面那 40 行的中文名同一个 x 开始，否则一眼就是两段拼的。
        #    这里用**一个真控件**（与词条行那颗序号 QLabel 同宽），不是 addSpacing：
        #    实测纯 spacer 后面**不加 item 间距**，名字会落在 34 而不是 40（差 6px）。
        #    也不能用裸 QWidget —— 全局 QSS 会给它开 WA_StyledBackground 刷成
        #    bg-base，把整行高亮盖掉（row_entries.py 里记过这一条）。QLabel 空着
        #    再用 entry-line-body 那条透明规则，两个坑都躲开。
        gutter = QLabel("")
        gutter.setProperty("role", "entry-line-body")
        gutter.setFixedWidth(INDEX_COLUMN_WIDTH)
        box.addWidget(gutter)

        # 弹种名：点它 = 去看 Ammunitions 那一行的 67 列（写值在那儿）。
        self._name = QPushButton(name)
        self._name.setProperty("role", "ammo-name")
        self._name.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name.setToolTip(f"去看 Ammunitions #{ammo_id} 这一行的全部列")
        self._name.setFixedHeight(EDITOR_HEIGHT)
        # 与词条行「中文名」那一列同宽 —— 于是右边的数量落在同一条列线上。
        self._name.setFixedWidth(NAME_COLUMN_WIDTH)
        # ❗ 横向上**不许**再用 Ignored（"宽度无所谓"）：那样布局把这一格的宽度算成 0，
        #    右边那个撑满的数量会直接压到名字身上 —— 实机量到名字在 34、数量也在 40，
        #    两个文本叠在一起。宽度已经写死，Fixed 才说得清"我就占这 190"。
        self._name.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._name.clicked.connect(lambda: self.link_activated.emit("Ammunitions", ammo_id))
        box.addWidget(self._name)

        # 数量：中栏词条行里"值"那一套（点一下就地改）。
        self._quantity = QPushButton(f"×{quantity}")
        self._quantity.setProperty("role", "entry-value")
        self._quantity.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quantity.setFixedHeight(EDITOR_HEIGHT)
        # ❗ 不写死宽度：它就是词条行里「值」那一格 —— 同一条列线、同一段宽度、
        #    同样的左对齐（曾经定宽 64 挤在名字右边，于是"×24"悬在中栏正中间）。
        self._quantity.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        #: 数量那一格的 clicked 接没接上 —— "就地改值"那条路（reload_values）要知道，
        #: 免得重复接一次（接两次 = 点一下开两个浮层）。
        self._quantity_wired = False
        if quantity_cell is None:
            # 读不到这一格（列不在表里 / 行没了）—— 不假装能改。
            self._quantity.setEnabled(False)
            self._quantity.setToolTip("这一格读不到（WeaponAmmunitions.Quantity 不在表里）")
        else:
            self._quantity.setToolTip(
                f"{quantity_cell.cn} = {quantity_cell.text or quantity}\n"
                f"{quantity_cell.place}\n点一下就地改"
            )
            self._quantity.clicked.connect(self.quantity_clicked)
            self._quantity_wired = True
        box.addWidget(self._quantity, 1)
        self._quantity_index = box.count() - 1

        # 卡片上那个数（Quantity × HUDMultiplier × 同型门数）：与格子里这个数
        # 不一样时才画 —— 一样还写一遍就是噪音（纯数字改数字）。
        shown = bool(hud_total) and hud_total != quantity
        self._hud = QLabel(f"卡上 {hud_total}" if shown else "")
        self._hud.setProperty("role", "entry-place")
        self._hud.setToolTip(
            f"卡片上画的携弹量 = 本数 × Ammunitions.HUDMultiplier × 同型门数（本数 {quantity}）"
        )
        # ❗ 顺序不能反：**先 addWidget（收进布局、拿到父控件），再 setVisible**。
        #    反过来就是"控件自己还没有父控件的时候被显示"= Qt 真的把它映射成一个
        #    顶层小窗（用户报过两次的"闪一下的小弹窗"，诊断日志里那条例外的
        #    "走火的顶层小窗"就是它）。这一条踩过一次，别再踩。
        box.addWidget(self._hud)
        self._hud.setVisible(shown)

        # ⛔ 2026-10-19 用户命令删掉了这颗「🔁 换弹种」：改弹种请删这一条再新增一条。

        self._remove = QPushButton("🗑")
        self._remove.setProperty("role", "row-tool")
        self._remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove.setFixedWidth(24)
        self._remove.setFixedHeight(ROW_HEIGHT - 4)
        self._remove.setToolTip("删掉这一条携弹行（Ctrl+Z 能撤回来）")
        self._remove.clicked.connect(self.remove_requested)
        box.addWidget(self._remove)

        self._ident = QLabel(f"{TABLE} #{row_id}")
        self._ident.setProperty("role", "entry-id")
        box.addWidget(self._ident)

    # ------------------------------------------------------------------ #
    # 就地改值（AmmoBlock.reload 里"行署名没变"那条路）
    # ------------------------------------------------------------------ #

    def reload_values(
        self, *, quantity: int, quantity_cell: FieldCell | None, hud_total: int | None = None
    ) -> None:
        """只把**数字**写回去 —— 控件一个都不换（AGENTS §0.7）。

        配套 `AmmoBlock.reload` 的"行署名没变"那条路：署名（WeaponAmmunitions 行 Id +
        弹种 Id）没变时，弹种名与「🗑」那颗按钮的信号里嵌着的东西都没变
        —— 变的只有数量，那就 `setText`。
        """
        self.quantity_cell = quantity_cell
        self._quantity.setText(f"×{quantity}")
        if quantity_cell is None:
            self._quantity.setEnabled(False)
            self._quantity.setToolTip("这一格读不到（WeaponAmmunitions.Quantity 不在表里）")
        else:
            self._quantity.setEnabled(True)
            self._quantity.setToolTip(
                f"{quantity_cell.cn} = {quantity_cell.text or quantity}\n"
                f"{quantity_cell.place}\n点一下就地改"
            )
            if not self._quantity_wired:
                self._quantity.clicked.connect(self.quantity_clicked)
                self._quantity_wired = True
        # 卡片上那个数：与格子里这个数不一样时才画（口径同 __init__，不另写一套）。
        shown = bool(hud_total) and hud_total != quantity
        self._hud.setText(f"卡上 {hud_total}" if shown else "")
        self._hud.setVisible(shown)

    # ------------------------------------------------------------------ #
    # 悬停：只报告，由 AmmoBlock 单点写入（与 EntryRow 同一套契约）
    # ------------------------------------------------------------------ #

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().enterEvent(event)
        self.hover_changed.emit(self, True)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().leaveEvent(event)
        self.hover_changed.emit(self, False)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().hideEvent(event)
        # 行被摘掉/藏起来时也要交还悬停态，否则属性留在原地。
        if self._hovered:
            self.hover_changed.emit(self, False)

    def set_hovered(self, hovered: bool) -> None:
        """**唯一的写入口**（由 AmmoBlock.set_hovered_line() 调）。别在别处改。"""
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self.setProperty("hover", "true" if hovered else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def is_hovered(self) -> bool:
        """这一行现在是不是悬停态（等于 property("hover") == "true"）。"""
        return self._hovered

    # ------------------------------------------------------------------ #
    # 就地编辑（就在数量那一格上改，与中栏词条行同一种手势）
    # ------------------------------------------------------------------ #

    def attach_editor(self, editor: QWidget) -> None:
        """把编辑控件放进**数量那一格**（行高由这一行说了算，浮层适配它）。

        口径与 `widgets/row_entries.EntryRow.attach_editor` 一致：浮层里的输入框
        比这一行还高，不夹住就会被上下裁掉（实机看就是"只有一个蓝边的破框"）。

        ❗ 高度取 `EDITOR_HEIGHT`（与词条行**同一份**）。这里一度写的是
        `LINE_HEIGHT - 2`，而那时 LINE_HEIGHT 还是 24 —— 浮层只有 22，
        正是词条行那边报过两次的那个"被裁"。行高统一之后两个数本来就相等，
        但取哪一个是**有守卫的**那个，别退回"减 2"。
        """
        self.close_editor()
        self._editor = editor
        editor.setFixedHeight(EDITOR_HEIGHT)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._quantity.setVisible(False)
        self._box.insertWidget(self._quantity_index, editor, 1)
        editor.show()
        # ❗ 焦点交给里面那个输入控件（浮层自己是 QFrame，setFocus() 是空操作）——
        #    与 EntryRow.attach_editor 同一条口径，见 InlineEditor.focus_input。
        focus_input = getattr(editor, "focus_input", None)
        if callable(focus_input):
            focus_input()
        else:
            editor.setFocus()

    def close_editor(self) -> None:
        if self._editor is not None:
            detach(self._editor)
            self._editor = None
        self._quantity.setVisible(True)

    @property
    def has_editor(self) -> bool:
        return self._editor is not None

    @property
    def editor(self) -> QWidget | None:
        return self._editor

    # ------------------------------------------------------------------ #

    @property
    def name_text(self) -> str:
        return self._name.text()

    @property
    def quantity_text(self) -> str:
        return self._quantity.text()

    @property
    def name_button(self) -> QPushButton:
        return self._name

    @property
    def quantity_button(self) -> QPushButton:
        return self._quantity

    @property
    def hud_text(self) -> str:
        """卡片上那个携弹量（不一样时才有字；一样就是空串）。

        ❗ 读**写进去的字**，不读 isVisible()：窗口没 show() 时整棵树都不可见
        （用例与冒烟就是这么跑的），读 isVisible() 会一律得到"没画"。
        """
        return self._hud.text()

    @property
    def remove_button(self) -> QPushButton:
        return self._remove

    @property
    def can_edit_quantity(self) -> bool:
        return self._quantity.isEnabled()


class AmmoBlock(QFrame):
    """挂在武器那一行下面的「💣 弹药（N）」块。

    只发意图（见模块头那条口径）：外层接 `quantity_activated / link_activated /
    add_requested / remove_requested`，落盘全在 EditorContext。
    """

    #: 数量改好了（参数：Quantity 的 CellRef, 新值）—— 外层 set_value + 立刻保存。
    quantity_committed = Signal(object, object)
    #: 点了弹种名 / 「＋ 加一种弹」选好了（参数：表名, 行 Id）。
    link_activated = Signal(str, int)
    #: 「＋ 加一种弹」选好了（参数：weapon_id, ammo_id）-> add_block_row("weapons", ...)。
    add_requested = Signal(int, int)
    #: 「🗑 删一条」（参数：要删的 CellRef 元组, 显示名）。
    remove_requested = Signal(object, str)

    def __init__(
        self,
        instance,
        data,
        unit_id: int,
        weapon_id: int,
        *,
        cell_lookup,
        slot_label: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """
        参数
        ----
        instance / data
            数据源与那张 CardData（与卡片、左树**共用一份**，别各建一份）。
        unit_id / weapon_id
            本单位 + 中栏正在看的那把枪。
        cell_lookup
            取一格 FieldCell 的函数（传 EditorContext.cell）—— 数量的显示与就地改
            都靠它，找不到列时给 None（这一块会置灰，不假装能改）。
        """
        super().__init__(parent)
        self.setProperty("role", "ammo-block")
        #: 这一块是**哪一把枪**的弹药（"主武器 AK-74RMO" / 空 = 中栏这一行就是那把枪）。
        self._slot_label = slot_label
        self._instance = instance
        self._data = data
        self._unit_id = int(unit_id)
        self._weapon_id = int(weapon_id)
        self._cell_lookup = cell_lookup
        self._lines: list[AmmoLine] = []
        #: 现在这批行是**按哪些行**建的：@@((WeaponAmmunitions 行 Id, 弹种 Id), ...)@@。
        #: reload() 拿它判"要不要整套重建"（见那里的说明）；空元组 = 还没建过。
        self._line_signature: tuple = ()
        self._picker: RowPicker | None = None
        #: 现在开着编辑浮层的那一行（没有就是 None）。
        self._open_line: AmmoLine | None = None
        #: 现在亮着的那一行 —— **唯一**的悬停态归属（见 set_hovered_line）。
        self._hovered_line: AmmoLine | None = None
        #: 本单位装了几门这门枪（惰性算一次；卡片上的携弹量要乘它）。
        self._instances: int | None = None
        #: 重建后要补回去的滚动位置（窗口期见 _restore_scroll）。
        self._scroll_bar = None
        self._scroll_target: int | None = None
        self._scroll_budget = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, TOKENS.spacing.sm, 0, 0)
        root.setSpacing(TOKENS.spacing.xxs)

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.sm)
        self._title = QLabel("")
        self._title.setProperty("role", "entries-subtitle")
        head.addWidget(self._title)
        self._hint = QLabel("点数量就地改 · 🗑 删一条")
        self._hint.setProperty("role", "entry-place")
        head.addWidget(self._hint)
        head.addStretch(1)
        self._add = QPushButton("＋ 加一种弹")
        self._add.setProperty("role", "row-tool")
        self._add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add.setToolTip("给这把枪挂上一种弹（就地冒候选列表，不弹对话框）")
        self._add.clicked.connect(self._open_add_picker)
        head.addWidget(self._add)
        root.addLayout(head)

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        root.addWidget(self._rows_host)

        #: 「＋」就地冒出来的候选列表（与树上那颗＋同一个控件）。
        self._picker_host = QWidget()
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(0, 0, 0, 0)
        self._picker_layout.setSpacing(0)
        self._picker_host.setVisible(False)
        root.addWidget(self._picker_host)

        self.reload()

    def _title_text(self, kinds: int, total: int, hud: int, *, suffix: str) -> str:
        """块标题：「💣 主武器 AK-74RMO · 弹药（1 种 · 本数 80 发）」。

        `slot_label` 空着就是"中栏这一行本身就是那把枪"（载具/炮塔那条路）——
        那种时候再写一遍枪名是噪音。
        """
        return f"{self._bare_title()}（{kinds} 种 · 本数 {total} 发{suffix}）"

    def _bare_title(self) -> str:
        """不带计数的标题（读不到数据时用）。"""
        head = f"{self._slot_label} · " if self._slot_label else ""
        return f"💣 {head}弹药"

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #

    def _entries(self) -> tuple[tuple[int, int, int], ...]:
        """本单位的这把枪挂了哪些弹：((AmmunitionId, Quantity, Order), ...)。

        口径只有一份：CardData.weapon_ammo（与左树、卡片同一份）。
        ❗ 读之前先丢缓存 —— 刚插/删过携弹行时不丢就会照旧名单画
        （Rows.where2 是缓存的，实测踩过同类问题）。
        """
        self._data.invalidate(TABLE)
        return tuple(self._data.weapon_ammo(self._unit_id, self._weapon_id))

    def instances_in_unit(self) -> int:
        """这个单位装了几门这门枪（同型多门 -> 卡片上的携弹量要乘它）。

        口径与 viewmodel.unit_card.weapon_groups 一致：它按 weapon_ids 里的**每一项**
        求和（AK 一个班 5 个人就有 5 项，所以卡片上写 1200 而不是 240）。
        """
        if self._instances is None:
            try:
                ids = list(self._data.weapon_ids(self._unit_id))
            except Exception:  # noqa: BLE001 - 武器链算不出来时按 1 门算（不编数）
                ids = []
            count = sum(1 for weapon_id in ids if int(weapon_id) == self._weapon_id)
            self._instances = max(1, count)
        return self._instances

    def hud_total(self, ammo_id: int, quantity: int) -> int:
        """卡片上那个携弹量（见模块头那条 ⚠️）。"""
        rows = self._data.t("Ammunitions")
        row = rows.by_id(ammo_id)
        multiplier = rows.i(row, "HUDMultiplier") if row is not None else 1
        return quantity * max(1, multiplier) * self.instances_in_unit()

    def _row_id_of(self, ammo_id: int, quantity: int) -> int:
        """这一条携弹行是哪一行（WeaponAmmunitions.Id）—— 改数量要按行寻址。"""
        rows = self._data.t(TABLE)
        for row in rows.where2("UnitId", self._unit_id, "WeaponId", self._weapon_id):
            if rows.i(row, AMMO_COLUMN) == ammo_id and rows.i(row, QUANTITY_COLUMN) == quantity:
                return rows.i(row, "Id")
        return 0

    def carried_by_unit(self) -> bool:
        """这个单位挂没挂这把枪（口径见模块级 carries_weapon）。"""
        return carries_weapon(self._data, self._unit_id, self._weapon_id)

    # ------------------------------------------------------------------ #
    # 画
    # ------------------------------------------------------------------ #

    def _scroll_value(self):
        """包着自己的那个滚动区的竖直滚动条（找不到就是 None）。"""
        from PySide6.QtWidgets import QScrollArea

        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QScrollArea):
                return widget.verticalScrollBar()
            widget = widget.parentWidget()
        return None

    #: 补充滚动位置最多等几次范围变化；超过就当"这次是真的矮了"，不再追。
    SCROLL_RETRIES = 3
    #: 补充窗口的兜底时限（毫秒）——实测那一趟 Qt 重算在 50ms 内跑完。
    SCROLL_ARM_MS = 250

    def _restore_scroll(self, bar, value) -> None:
        """把滚动位置放回去（重建之后调）。

        ❗ **不能只 setValue 一次**：实测（_work/gui_live_scroll3.py，真机）

            reload() 出 scroll=421
            refresh() 出 scroll=421
            RANGE 0..393  content=1145 值=421   ← Qt 下一趟重算：内容先矮一行
            VALUE -> 393/393                    ← 范围一缩，值被夹走
            RANGE 0..421  content=1173 值=393   ← 范围立刻长回来，值已经丢了

        那一趟是 Qt 自己跑的（抓栈只有 app.exec()，没有 Python 调用方）：新建的
        携弹行还没参与第一次布局，内容高度先少一行 -> QScrollArea 把范围缩到 393
        并把滚动值夹过去；范围下一瞬间就长回来了，可值已经丢。用户看到的就是
        「弹药栏的编辑框消失后滑条向上跳一格」（2026-10-01 实机）。

        所以这里**记住"想要的值"**，并在 rangeChanged 上等一次：范围长回够用就把
        值补上（最多补 SCROLL_RETRIES 次 + SCROLL_ARM_MS 兜底，补上/超时就摘钩，
        免得它变成一颗远处的雷）。
        """
        if bar is None or not value:
            return
        self._scroll_bar = bar
        self._scroll_target = int(value)
        self._scroll_budget = self.SCROLL_RETRIES
        bar.setValue(self._scroll_target)
        with contextlib.suppress(RuntimeError, TypeError):
            bar.rangeChanged.disconnect(self._on_scroll_range)
        bar.rangeChanged.connect(self._on_scroll_range)
        QTimer.singleShot(self.SCROLL_ARM_MS, self._disarm_scroll_restore)

    def _on_scroll_range(self, _minimum: int, maximum: int) -> None:
        """范围变了：够用就把当初那个滚动值补上（见 _restore_scroll）。"""
        bar = self._scroll_bar
        wanted = self._scroll_target
        if bar is None or wanted is None:
            return
        self._scroll_budget -= 1
        if maximum >= wanted:
            bar.setValue(wanted)
            self._scroll_target = None
        if self._scroll_target is None or self._scroll_budget <= 0:
            self._disarm_scroll_restore()

    def _disarm_scroll_restore(self) -> None:
        """摘钩（补上了 / 超时了 / 又要重画了都走这里）。"""
        self._scroll_target = None
        bar = self._scroll_bar
        if bar is None:
            return
        with contextlib.suppress(RuntimeError, TypeError):
            bar.rangeChanged.disconnect(self._on_scroll_range)

    def reload(self) -> None:
        """重画这一块（插/删/改之后由外层调）。

        ❗ **行还在、只有数字变了 -> 就地写**（2026-10-01，AGENTS §0.7）：
        老写法无条件把每一行 detach + 重建 —— 实测 **14 个控件只保住 6 个**、
        这一块自己 **3.6ms**（尺子：`_work/probe_vehicle_page.py` 同一套量法，
        单位 21 聚焦 Weapons#18 时量到），而"改一格携弹量"的时候哪几种弹、
        每种的 Id 一个字都没变，变的只是那个数字。重建的代价还不止耗时：
        被摘掉的行会重画一次，用户看到的就是那一下闪。
        判据 = **行署名** `((WeaponAmmunitions 行 Id, 弹种 Id), ...)`（含顺序）：
        一样 -> 只把数字写回去；变了（加了一条弹 / 换弹种 / 删了一条）才整套重建。

        （"保住滚动位置"那件事只对**重建**那条路有意义 —— 见 `_restore_scroll`：
          Qt 会在这一趟**之后**再重算一次范围，在这里撑高度拦不住，实测白做。
          就地写那条路内容高度不变，滚动位置本来就不会动。）
        """
        # ❗ 先收浮层：重建那条路会把每一行都摘掉，浮层挂在行上，不先收就是在
        #    "一个已经 deleteLater 的控件"上继续收键盘（用户报过的小弹窗同族）。
        self.close_editor()
        # 悬停态也跟着行一起走：不交回去的话，被摘下来的行会留着 hover 属性。
        self.set_hovered_line(None)
        entries = self._entries()
        carried = self.carried_by_unit()
        self._add.setVisible(carried)
        if not carried:
            # 正常运行路径上外层（MainEditorPage.entries_blocks）根本不会建这一块；
            # 这里兜住"数据在两次刷新之间变了"的那种情况，别画一个点了没反应的＋。
            self._clear_lines()
            self._title.setText(self._bare_title())
            self._hint.setText(f"这个单位没挂 Weapons#{self._weapon_id} —— 你在看一行模板")
            return
        total = sum(quantity for _ammo_id, quantity, _order in entries)
        hud = sum(self.hud_total(ammo_id, quantity) for ammo_id, quantity, _order in entries)
        suffix = "" if hud == total else f" · 卡片上 {hud} 发"
        self._title.setText(self._title_text(len(entries), total, hud, suffix=suffix))
        self._hint.setText("点数量就地改 · 🗑 删一条")
        # 行 Id 只算一遍（建行与"要不要重建"共用这一份，免得两处各算一套）。
        rows_now = tuple(
            (self._row_id_of(ammo_id, quantity), ammo_id, quantity)
            for ammo_id, quantity, _order in entries
        )
        signature = tuple((row_id, ammo_id) for row_id, ammo_id, _quantity in rows_now)
        if rows_now and signature == self._line_signature and len(self._lines) == len(rows_now):
            # 就是这一批行：只把数字写回去。控件一个不换，滚动位置也不会跳。
            for line, (row_id, ammo_id, quantity) in zip(self._lines, rows_now, strict=True):
                line.reload_values(
                    quantity=quantity,
                    quantity_cell=(
                        self._cell_lookup(CellRef(TABLE, row_id, QUANTITY_COLUMN))
                        if row_id
                        else None
                    ),
                    hud_total=self.hud_total(ammo_id, quantity),
                )
            return
        # ❗ 重建前后**保住滚动位置**：下面把每一行都摘掉，内容高度瞬间塌成 0，
        #    QScrollArea 会趁机把滚动值夹回顶部 —— 再填回来时内容就「向上跳一下」
        #    （用户口径 2026-09-30 实机：「编辑栏消失的时候，中间还会向上跳一下，
        #      似乎中间重新加载了一遍弹药项」）。
        #    这个塌陷一直都在，只是刷新改成异步之后它从「同一帧内」变成了看得见的一跳。
        keep_scroll = self._scroll_value()
        saved = keep_scroll.value() if keep_scroll is not None else 0
        self._clear_lines()
        if not rows_now:
            empty = QLabel("这把枪还没挂弹 —— 点右边「＋ 加一种弹」。")
            empty.setProperty("role", "empty-hint")
            self._rows_layout.addWidget(empty)
            return

        for row_id, ammo_id, quantity in rows_now:
            quantity_cell = (
                self._cell_lookup(CellRef(TABLE, row_id, QUANTITY_COLUMN)) if row_id else None
            )
            line = AmmoLine(
                row_id=row_id,
                ammo_id=ammo_id,
                name=display_name(self._instance, "Ammunitions", ammo_id),
                quantity=quantity,
                quantity_cell=quantity_cell,
                ammo_ref=CellRef(TABLE, row_id, AMMO_COLUMN),
                hud_total=self.hud_total(ammo_id, quantity),
            )
            line.hover_changed.connect(self._on_line_hover)
            line.link_activated.connect(self.link_activated)
            line.quantity_clicked.connect(
                lambda _checked=False, target=line: self._open_quantity_editor(target)
            )
            line.remove_requested.connect(
                lambda _checked=False, ref=line.ammo_ref, name=line.name_text: (
                    self.remove_requested.emit((ref,), name)
                )
            )
            self._rows_layout.addWidget(line)
            self._lines.append(line)
        self._line_signature = signature

        # 行都回来了，再把滚动位置放回去（见上面 keep_scroll 的说明）。
        self._restore_scroll(keep_scroll, saved)

    def _clear_lines(self) -> None:
        """把每一行摘下来（署名一起作废 —— 下一次 reload 必须重建）。

        ❗ 走 detach（先 hide 再 setParent(None)）：可见控件被直接 setParent(None)
        会被 Qt 映射成顶层小窗（见 widgets/detach.py）。
        """
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                detach(widget)
        self._lines = []
        self._line_signature = ()

    # ------------------------------------------------------------------ #
    # 悬停：整行一条高亮，同一时刻只有一行
    # ------------------------------------------------------------------ #

    def _on_line_hover(self, line: AmmoLine, hovered: bool) -> None:
        if hovered:
            self.set_hovered_line(line)
        elif self._hovered_line is line:
            self.set_hovered_line(None)

    def set_hovered_line(self, line: AmmoLine | None) -> None:
        """把悬停态**唯一地**给某一行（先灭上一行、再点新的）。

        与 row_entries.EntryList.set_hovered_row 同一套口径 —— 不看 Qt 的
        WA_UnderMouse，所以"同时只有一行亮"是这一步的结构保证。
        """
        if line is self._hovered_line:
            return
        previous, self._hovered_line = self._hovered_line, line
        if previous is not None:
            previous.set_hovered(False)
        if line is not None:
            line.set_hovered(True)

    @property
    def hovered_line(self) -> AmmoLine | None:
        return self._hovered_line

    # ------------------------------------------------------------------ #
    # 就地编辑：数量那一格
    # ------------------------------------------------------------------ #

    def _open_quantity_editor(self, line: AmmoLine) -> None:
        """点数量 -> 就在那一格上开浮层（不弹对话框、没有确认按钮）。

        ❗ 浮层由**这一块**自己挂，不走 `RowEntriesPanel.open_editor`：
        那一套按 CellRef 去"这一行的列"里找行，而弹药行是 WeaponAmmunitions 里
        **别的行**，在词条列表里根本不存在（实测会把浮层默默丢掉）。
        """
        cell = line.quantity_cell
        if cell is None:
            return
        self.close_editor()
        editor = InlineEditor(cell, instance=self._instance)
        editor.committed.connect(
            lambda value, ref=cell.ref: self.quantity_committed.emit(ref, value)
        )
        editor.cancelled.connect(self.close_editor)
        line.attach_editor(editor)
        self._open_line = line

    def close_editor(self) -> None:
        if self._open_line is not None:
            line, self._open_line = self._open_line, None
            line.close_editor()

    @property
    def has_editor(self) -> bool:
        return self._open_line is not None

    @property
    def open_row(self) -> AmmoLine | None:
        return self._open_line

    # ------------------------------------------------------------------ #
    # 就地冒候选（不弹对话框）
    # ------------------------------------------------------------------ #

    def _open_add_picker(self) -> None:
        """「＋ 加一种弹」：候选 = Ammunitions 全表（就地搜索）。"""
        if self._picker is not None:
            self.close_picker()
            return
        picker = RowPicker(self._instance, "Ammunitions", parent=self._picker_host)
        picker.picked.connect(self._on_add_picked)
        picker.cancelled.connect(self.close_picker)
        self._picker = picker
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        picker.start()

    def _on_add_picked(self, ammo_id: int) -> None:
        self.close_picker()
        self.add_requested.emit(self._weapon_id, int(ammo_id))


    def close_picker(self) -> None:
        if self._picker is not None:
            picker = self._picker
            self._picker = None
            self._picker_layout.removeWidget(picker)
            # ❗ removeWidget 只是从布局里拿掉，控件**还是可见的** —— 直接
            #    setParent(None) 会被 Qt 映射成顶层小窗（同上一条）。
            detach(picker)
        self._picker_host.setVisible(False)

    # ------------------------------------------------------------------ #
    # 只读访问（用例与诊断用）
    # ------------------------------------------------------------------ #

    @property
    def lines(self) -> tuple[AmmoLine, ...]:
        return tuple(self._lines)

    @property
    def weapon_id(self) -> int:
        return self._weapon_id

    @property
    def title_text(self) -> str:
        return self._title.text()

    @property
    def hint_text(self) -> str:
        return self._hint.text()

    @property
    def add_button(self) -> QPushButton:
        return self._add

    @property
    def picker(self) -> RowPicker | None:
        return self._picker

    @property
    def picker_visible(self) -> bool:
        return self._picker is not None and self._picker_host.isVisibleTo(self)


__all__ = [
    "AMMO_COLUMN",
    "LINE_HEIGHT",
    "QUANTITY_COLUMN",
    "TABLE",
    "AmmoBlock",
    "AmmoLine",
    "carries_weapon",
]
