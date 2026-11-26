"""单位编辑器左栏的**可写卡**：版式照抄军械库那张卡，每个数字/名字可点。

## 为什么是子类，不是改 UnitCardPanel

军械库右栏那张卡有用户口径与一条守卫用例管着：**「右侧的卡片就展示信息」**
（tests/test_armory_screen.py::test_card_has_no_editing_entry_points 断言
没有输入框、没有编辑方法、changeset 为 None、*_requested 恰好三个）。

本轮用户要的是"双击之后**就地改数据**" —— 那是**编辑器里的那张卡**，
不是军械库右栏那张。所以：

- UnitCardPanel **一行不改语义**（还是只读、还是不接变更集），那条守卫照旧绿；
- 可写层在这里，作为子类加信号与点击目标；版式（立绘 / 数值行 / 武器 / 选项）
  全部继承，**不抄第二份**。

## 点哪儿改哪儿

| 点 | 改的是 |
|---|---|
| 成本徽章 | UnitCard.cost_ref（多半是 Units.Cost；被选项覆盖时是那条选项的 Cost） |
| 数值行每一格 | ❌ 不给点（它是算出来的汇总，见 _stat_chip） |
| 抬头那个名字 | Units 那一行的 HUDName |
| 武器 | 进武器页（40 列） |
| 弹药行 | 进弹药页（67 列） |
| 改装槽 | 进改装选项页（46 列） |
| 立绘右下角的校验条 | 跳到出问题的那一条词条 |

## 块 7：卡上直接增删（＋ / ✕）

设计定稿 块 7：**卡上每一块都能增删**（能力 / 武器 / 装甲 / 机动 / 传感器 /
运输 / 改装槽）。两条口径钉死在这里：

1. **增**：块标题右边一个「＋」-> **卡片上就地冒搜索框**（RowPicker，不弹对话框），
   点一条即插一行 —— 落点是**关联表**（UnitArmors / UnitAbilities /
   WeaponAmmunitions…），新行 Id 走 core/edit/clone.py::next_row_id（最大 +1）。
   加武器 = 插一行 WeaponAmmunitions（**武器 × 弹种**一起挑），不是插 Weapons 模板行。
2. **删**：每一项右边一个「✕」-> 删**关联行**（Changeset.delete_row）——
   删 WeaponAmmunitions#21 = 这台车不带这把枪，Weapons#18 还在库里、别的单位照用。

**例外：「属性（N）」不给增删** —— 它是从装甲 / 机动 / 传感器算出来的汇总，
没有对应的行（_stat_chip 那条注释写着同一句话）。

❗ **卡只发意图，不写数据**：add_requested / remove_requested 交给上层，
由 EditorPage（pages/base.py）转给 EditorContext —— 写数据、撤销 / 重做栈都在
那一条线上（只进缓存；落盘走顶栏「💾 保存」），与「换… / 克隆一份」的分工完全一致。

❗ **不给 ✕ 的两种情况**（宁可不画，也不删错东西）：

- 模板行：Armors / Weapons / Abilities 这些没有 UnitId，删了全库一起变；
- 被**改装选项覆盖**的那一格（Options#504.ArmorId）：那一行的"主人"是选项，
  删掉选项行会连累同一个槽下的配置 —— 想改就去改装选项页。

## 块 11：班组成员（步兵）

一行 = 一个兵，落在 SquadMembers（带 UnitId）。标题右边「＋」= 照本单位现有的一名成员
复制一名（**候选不是全库的兵** —— 别的单位的兵带着的枪未必在本单位的 SquadWeapons 里，
挑进来当场违反那条 error 级不变量，理由写在 _squad_choices 与 _squad_section 里）；
每名成员行尾「✕」= 删本单位那一行。步兵班专用页（pages/squad_page.py）那条 +1 / ✕
**不拆**：那一条是整页视图，这一条是卡片上的入口。
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.armory.detail_panel import (
    UnitCardPanel,
    card_id_badge,
    link_tooltip,
)
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.unit_card import TYPE_HELICOPTER, TYPE_VEHICLE
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import DEFAULT_LIMIT, RowPicker
from ba_mod_maker.core.edit.foreign import find_row
from ba_mod_maker.core.edit.refs import CellRef, foreign_table
from ba_mod_maker.core.edit.scope import owner_link_ref

#: 校验条在立绘上的角色名（按最严重的那条选）。
_BADGE_ROLE_ERROR = "portrait-badge-error"
_BADGE_ROLE_WARN = "portrait-badge-warn"

#: 卡上每一块的标题 -> 块名。块名与 pages/base.py::CARD_BLOCKS 的键**一一对应**
#: （对不上的话 ＋ 会插错表，所以那条对应关系有用例守着）。
_BLOCK_BY_TITLE = {
    "🛡 装甲": "armor",
    "🚗 机动": "mobility",
    "📡 传感器": "sensors",
    "🚚 运输": "transports",
}

#: 块名 -> 「＋」的候选来自哪张表（第二项是第二列那张表，None = 单列）。
#: 空串 = 不用挑模板（改装槽：插的就是本单位自己的一行）。
_BLOCK_TABLES: dict[str, tuple[str, str | None]] = {
    "armor": ("Armors", None),
    "mobility": ("Mobility", None),
    "sensors": ("Sensors", None),
    "transports": ("SpecializationAvailabilities", None),
    "abilities": ("Abilities", None),
    "weapons": ("Weapons", "Ammunitions"),
    "modifications": ("", None),
    # 班组成员（块 11）：候选**不是**扫这张表，而是本单位现有的那几名
    # （见 _squad_choices —— 一行 SquadMembers 不是模板，全库随便挑会撞不变量）。
    "squad": ("SquadMembers", None),
}


class EditableCardPanel(UnitCardPanel):
    """可写卡：卡片版式照抄，额外提供"点一格 → 告诉上层改哪一格"。

    ❗ 底部那个「在表格中打开」**已经整条删掉**（连军械库那张卡一起）：
    用户 2026-09-30 先在编辑器这张卡上退掉它，2026-10-02 再点名
    「把在表格中打开这个按钮取消掉吧」—— 入口交给双击卡 / 面包屑 / 数据表页签。
    """

    #: 点了一个可改的格子（参数：CellRef）。
    cell_activated = Signal(object)
    #: 点了武器那一行（参数：weapon_id）—— 进武器页。
    weapon_activated = Signal(int)
    #: 点了弹种那一行（参数：weapon_id, ammo_id）—— 进弹药页。
    ammo_activated = Signal(int, int)
    #: 点了改装槽（参数：modification_id）—— 进改装选项页。
    option_activated = Signal(int)
    #: 点了卡上"某一行"（参数：表名, 行 Id）—— 右栏换成那一行的全部列。
    #: 装甲图 / 机动 / 传感器 / 运输 / 能力 都走这一条（界面重做 §1 第 4 条）。
    row_activated = Signal(str, int)
    #: 「克隆一份」：复制某一行，再把 link 指过去（参数：模板表, 行 Id, link 的 CellRef）。
    clone_requested = Signal(str, int, object)
    #: 块 7 的「＋」：给这一块加一行（参数：块名, 选中的行 Id, 第二行 Id / 0）。
    #: 写盘在上层（pages/base.py -> EditorContext），卡片自己不动数据。
    add_requested = Signal(str, int, int)
    #: 块 7 的「✕」：删这几行**关联行**（参数：那几格, 这一项在卡上的显示名）。
    remove_requested = Signal(object, str)
    #: 点了立绘上的校验条。
    finding_activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: id(控件) -> CellRef。用 eventFilter 统一收点击：
        #: 数值行那几格是 QFrame/QLabel，本身没有 clicked 信号。
        self._targets: dict[int, CellRef] = {}
        #: id(控件) -> (weapon_id, ammo_id)。点弹种行进弹药页。
        self._ammo_targets: dict[int, tuple[int, int]] = {}
        #: id(控件) -> (表名, 行 Id)。装甲图 / 能力 / 机动 / 传感器 / 运输 走这一条。
        self._row_targets: dict[int, tuple[str, int]] = {}
        #: (LinkedRow, 那一行的容器, 它的候选列表) —— 只给用例与诊断看。
        self._link_rows: list[tuple] = []
        #: 块名 -> 标题右边那个「＋」 / 就地冒出来的搜索框（每次重排都是新的）。
        self._add_buttons: dict[str, QPushButton] = {}
        self._pickers: dict[str, RowPicker] = {}
        #: 块名 -> 这一块的框（搜索框要插在它里面 = "就地"）。
        self._block_frames: dict[str, QWidget] = {}
        #: 每一项的「✕」：(要删的那几格, 显示名, 按钮)。只给用例与诊断看。
        self._removes: list[tuple[tuple[CellRef, ...], str, QPushButton]] = []
        #: 改装槽 -> 它底下有几条选项行（删槽之前要问的一句）。
        self._modification_options: dict[int, int] = {}
        #: 立绘上的校验命中（**只有新增的**，见 set_validation）。
        #: ❗ 名字不能叫 _findings —— 基类那个是 dict[单位 Id, 命中]，撞名会把
        #: _refresh_badge 打崩（'tuple' object has no attribute 'get'，实测踩过）。
        self._validation_findings: tuple = ()
        #: 每个 CellRef 要用的 link_ref（「另存为新模板」重指哪一行）。
        #: 来自 CardStat.link_ref —— 它比"本单位哪一行指着它"更准
        #: （「生命值」被选项覆盖时，指着它的是 Options#504.ArmorId）。
        self._links: dict[CellRef, CellRef] = {}
        self._portrait_frame = None
        self._validation_chip: QLabel | None = None
        for widget in (self._title, self._cost):
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.installEventFilter(self)

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def link_ref_for(self, ref: CellRef) -> CellRef | None:
        """这一格要「另存为新模板」时重指哪一行（没有就是 None）。"""
        return self._links.get(ref)

    # -- 只读访问（用例与诊断用） ---------------------------------------- #

    @property
    def add_buttons(self) -> dict[str, QPushButton]:
        """块名 -> 那个「＋」。"""
        return dict(self._add_buttons)

    @property
    def pickers(self) -> dict[str, RowPicker]:
        """块名 -> 就地冒出来的搜索框（点过「＋」才有）。"""
        return dict(self._pickers)

    @property
    def remove_buttons(self) -> tuple[tuple[tuple[CellRef, ...], str, QPushButton], ...]:
        """每一项的「✕」：(要删的那几格, 显示名, 按钮)。"""
        return tuple(self._removes)

    @property
    def link_rows(self) -> tuple[tuple, ...]:
        """装甲 / 机动 / 传感器 / 运输那几行：(LinkedRow, 容器, 它的「换…」候选列表)。"""
        return tuple(self._link_rows)

    def set_validation(self, findings: tuple) -> None:
        """立绘上的**唯一**角标：有校验问题才画，没有问题什么都不画。

        文案里的裸 Id 已经由 core/rules/humanize.py 换成了名字。
        """
        self._validation_findings = tuple(findings or ())
        if not self._validation_findings:
            if self._validation_chip is not None:
                detach(self._validation_chip)
                self._validation_chip = None
            return
        worst = (
            "error" if any(f.severity == "error" for f in self._validation_findings) else "warning"
        )
        text = f"校验 {len(self._validation_findings)} 处 · {self._validation_findings[0].message}"
        if self._validation_chip is None:
            chip = QLabel()
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("点它跳到出问题的那一条词条")
            chip.installEventFilter(self)
            self._validation_chip = chip
        chip = self._validation_chip
        chip.setText(text)
        chip.setProperty("role", _BADGE_ROLE_ERROR if worst == "error" else _BADGE_ROLE_WARN)
        chip.style().unpolish(chip)
        chip.style().polish(chip)
        chip.setToolTip(
            "\n".join(f"[{f.severity}] {f.message}" for f in self._validation_findings[:12])
            + "\n（点它跳到第一条）"
        )
        if self._portrait_frame is not None:
            self._portrait_frame.add_overlay(chip, row=1, column=1)

    @property
    def validation_text(self) -> str:
        """立绘上那条字（测试与验收用；没有时是空串 = 什么都没画）。"""
        return self._validation_chip.text() if self._validation_chip is not None else ""

    # ------------------------------------------------------------------ #
    # 点击
    # ------------------------------------------------------------------ #

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名
        if event.type() == QEvent.Type.MouseButtonRelease:
            if watched is self._validation_chip:
                self.finding_activated.emit()
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                ref = self._targets.get(id(watched))
                if ref is not None:
                    self.cell_activated.emit(ref)
                    return True
                ammo = self._ammo_targets.get(id(watched))
                if ammo is not None:
                    self.ammo_activated.emit(*ammo)
                    return True
                target = self._row_targets.get(id(watched))
                if target is not None:
                    self.row_activated.emit(*target)
                    return True
        return super().eventFilter(watched, event)

    def _clickable(self, widget: QWidget, ref: CellRef, link: CellRef | None = None) -> QWidget:
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        tip = f"{ref} —— 点它就地改"
        if link is not None:
            tip += f"\n（复制一份会重指 {link}）"
        widget.setToolTip(f"{widget.toolTip()}\n{tip}" if widget.toolTip() else tip)
        widget.installEventFilter(self)
        self._targets[id(widget)] = ref
        if link is not None:
            self._links[ref] = link
        return widget

    # ------------------------------------------------------------------ #
    # 块 7 · 增（＋）
    # ------------------------------------------------------------------ #

    def _attach_block_actions(self, frame: QWidget, block: str, title: str) -> None:
        """在块标题那一行的右边加一个「＋」，并登记这一块（点它就地冒搜索框）。

        ❗ 版式**不抄第二份**：标题是基类画的，这里只把它挪进一个横排容器
        （标题贴左、＋贴右）；武器那一块的标题本来就是一条横排布局（右边还挂着
        「简略 / 详细」），那就把 ＋ 接到那条布局的末尾，不动它的结构。
        """
        layout = frame.layout()
        if layout is None or layout.count() == 0 or block in self._block_frames:
            return
        button = QPushButton("＋")
        button.setProperty("role", "row-tool")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"给「{title}」加一行 —— 就地冒搜索框（不弹对话框）")
        button.clicked.connect(lambda _checked=False, name=block: self._toggle_add_picker(name))

        first = layout.itemAt(0)
        label = first.widget() if first is not None else None
        if isinstance(label, QLabel):
            layout.removeWidget(label)
            head = QWidget(frame)
            # ❗ 容器必须挂"透明角色"（qss.py）：没有它，这一条会被刷成底色的块。
            head.setProperty("role", "block-head")
            box = QHBoxLayout(head)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(TOKENS.spacing.xs)
            box.addWidget(label, 1)
            box.addWidget(button, 0)
            layout.insertWidget(0, head)
        elif first is not None and first.layout() is not None:
            first.layout().addWidget(button)
        else:  # pragma: no cover - 基类的每一块第一项都是标题
            layout.insertWidget(0, button)
        self._add_buttons[block] = button
        self._block_frames[block] = frame

    def _toggle_add_picker(self, block: str) -> None:
        """点「＋」：**就地**冒出搜索框（同一块再点一次就收起来）。"""
        frame = self._block_frames.get(block)
        if frame is None or self._instance is None:
            return
        picker = self._pickers.get(block)
        if picker is None:
            table, second = _BLOCK_TABLES.get(block, ("", None))
            choices = self._add_choices(block)
            if not table or (block == "squad" and not choices):
                # 不用挑模板（改装槽）；或者班里一个人都没有 —— 没有"照着复制"的对象，
                # 直接插一行最小的（只填 UnitId），与 viewmodel.new_member_values 的空表口径一致。
                self.add_requested.emit(block, 0, 0)
                return
            picker = RowPicker(
                self._instance,
                table,
                second_table=second,
                choices=choices,
                # 手工名单（本单位的枪 / 本班的人）一次列全：DEFAULT_LIMIT=12 那个上限是给
                # "扫全库那几张表"用的，而一个班最多 14 人（Marine Raiders / Spetsnaz Alpha）——
                # 按 12 条截，最后两名成员在框里根本点不到（实测踩到）。
                limit=max(DEFAULT_LIMIT, len(choices)) if choices else DEFAULT_LIMIT,
                parent=frame,
            )
            picker.setVisible(False)
            if second:
                picker.pair_picked.connect(
                    lambda first_id, second_id, name=block: self._finish_add(
                        name, first_id, second_id
                    )
                )
            else:
                picker.picked.connect(lambda row_id, name=block: self._finish_add(name, row_id, 0))
            picker.cancelled.connect(lambda picked=picker: picked.setVisible(False))
            layout = frame.layout()
            if layout is not None:
                layout.insertWidget(1, picker)  # 紧跟标题那一行 = "就地"
            self._pickers[block] = picker
        shown = picker.isVisibleTo(frame)
        if not shown:
            # 开新的之前先收掉旧的（＋ / 换… 都算）—— 卡上一次只留一个搜索框。
            self._close_pickers(keep=picker)
        picker.setVisible(not shown)
        if not shown:
            picker.start()

    def _add_choices(self, block: str) -> tuple[tuple[int, str], ...] | None:
        """「＋」的候选：手工给一张名单的块走这里，其余返回 None = 扫整张表。

        - 武器：本单位已经挂着的那些枪（见 _weapon_choices 的理由）；
        - 班组成员：本单位现有的那几名（见 _squad_choices 的理由）。
        """
        if block == "weapons":
            return self._weapon_choices()
        if block == "squad":
            return self._squad_choices()
        return None

    def _squad_choices(self) -> tuple[tuple[int, str], ...]:
        """「加一名成员」的候选 = **本单位现在的这几名**（挑一名照他复制一行）。

        为什么不扫全库那 1134 行 SquadMembers：一行不是模板，别的单位的兵带着的枪
        未必在本单位的 SquadWeapons 里 —— 挑进来当场违反「步兵武器单向包含」那条
        error 级不变量（实机后果：HUD 里那件武器不显示）。口径与写盘那一半一致，
        见 pages/base.py::EditorContext.add_squad_member。
        """
        card = self._card
        if card is None or card.loadout is None:
            return ()
        names = {choice.weapon_id: choice.label for choice in card.loadout.weapon_choices}
        return tuple(
            (
                member.member_id,
                f"{member.index} · {self._weapon_label(names, member.primary_weapon_id)}"
                f" · {self._weapon_label(names, member.special_weapon_id)}",
            )
            for member in card.loadout.members
        )

    def _weapon_choices(self) -> tuple[tuple[int, str], ...]:
        """「加武器」左列的候选 = **本单位已经挂着的武器**。

        为什么不列全库那 804 门枪：新枪得先挂到某个炮塔 / 班组成员上才算数，
        而"挂到哪座炮塔"是设计 §5 第 2 条**还没定**的那一条（炮塔可能是共用模板、
        也可能被改装选项覆盖）。这里不猜 —— 先把确定的那条路做出来：
        **给本单位已有的枪加一个弹种**（落点就是 WeaponAmmunitions 一行，
        删它 = 这台车不带这个弹种）。挂一把全新的枪属于炮塔那一层（块 4）。
        """
        card = self._card
        if card is None:
            return ()
        return tuple((weapon.weapon_id, weapon.label) for weapon in card.weapons)

    def _finish_add(self, block: str, first_id: int, second_id: int) -> None:
        picker = self._pickers.get(block)
        if picker is not None:
            picker.setVisible(False)
        self.add_requested.emit(block, int(first_id), int(second_id))

    # ------------------------------------------------------------------ #
    # 块 7 · 删（✕）
    # ------------------------------------------------------------------ #

    def _row_of(self, table_name: str, row_id: int) -> list | None:
        if self._instance is None or table_name not in self._instance.table_names:
            return None
        table = self._instance.table(table_name)
        index = table.column_index("Id")
        for row in table.rows:
            if row[index] == row_id:
                return row
        return None

    def _is_own_row(self, ref: CellRef | None) -> bool:
        """这一格所在的**行**是不是本单位的关联行？—— 决定给不给「✕」。

        判据只有一条：那张表带 UnitId，且这一行的 UnitId == 本单位。
        模板行（Armors / Weapons…）与选项覆盖那一格（Options#504.ArmorId）都不是，
        于是它们**不画 ✕**（宁可不画，也不删错东西）。
        """
        if ref is None or self._instance is None:
            return False
        if ref.table not in self._instance.table_names:
            return False
        table = self._instance.table(ref.table)
        if "UnitId" not in table.columns:
            return False
        row = self._row_of(ref.table, ref.row_id)
        return row is not None and row[table.column_index("UnitId")] == self._unit_id

    def _own_refs(self, refs: Iterable[CellRef]) -> tuple[CellRef, ...]:
        """把这几格筛成"本单位自己的关联行"（去重、保序）。"""
        out: list[CellRef] = []
        for ref in refs or ():
            if self._is_own_row(ref) and ref not in out:
                out.append(ref)
        return tuple(out)

    def _remove_button(self, refs: tuple[CellRef, ...], label: str, tip: str) -> QPushButton:
        button = QPushButton("✕")
        button.setProperty("role", "row-action")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tip)
        button.clicked.connect(
            lambda _checked=False, rows=refs, name=label: self.remove_requested.emit(rows, name)
        )
        self._removes.append((tuple(refs), label, button))
        return button

    def _own_tip(self, refs: tuple[CellRef, ...], what: str) -> str:
        places = "、".join(f"{ref.table}#{ref.row_id}" for ref in refs)
        return (
            f"删掉 {what} —— 删的是**关联行**（{places}），"
            "模板行还在库里、别的单位照用（Ctrl+Z 能撤回来）"
        )

    # -- 版式钩子 ------------------------------------------------------- #

    def _stat_chip(self, stat) -> QWidget:
        """属性那一格**只展示，不给点、也不给增删**（用户口径，2026-09-30 实机反馈）：

            "这些属性还是不要点了就能改的功能吧，一个是不明显，另一个是局限性高。"

        它本来就是从装甲 / 机动 / 传感器算出来的汇总（设计 §块 7 也写着"属性不能增删"）。
        想改就去「🛡 装甲 / 🚗 机动 / 📡 传感器」那几块 —— 那几块才是"哪一行"。
        悬浮提示里仍然写着落点，方便对照。
        """
        return super()._stat_chip(stat)

    def _armor_zone(self, zone) -> QWidget:
        """装甲图那一块：点它 = 去看 Armors 那一行（顶/背/正/侧 + 生命值都在那儿）。"""
        host = super()._armor_zone(zone)
        if zone.row_id:
            host.setCursor(Qt.CursorShape.PointingHandCursor)
            source = f"\n（这一行来自 {zone.source}）" if zone.source else ""
            host.setToolTip(f"{host.toolTip()}\n点它看 Armors#{zone.row_id} 的全部列{source}")
            host.installEventFilter(self)
            self._row_targets[id(host)] = ("Armors", zone.row_id)
        return host

    def _ability_chip(self, chip) -> QWidget:
        """能力那一格：点它 = 去看 Abilities 那一行（51 列，改布尔位在那儿）。

        块 7：能力能**删**（删的是 UnitAbilities 那一行 = 本单位没有这个能力）。
        """
        widget = super()._ability_chip(chip)
        if chip.row_id:
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.installEventFilter(self)
            self._row_targets[id(widget)] = ("Abilities", chip.row_id)
            widget.setToolTip(f"{widget.toolTip()}\n点它看 Abilities#{chip.row_id} 的全部列")
        link = self._ability_link(chip)
        refs = self._own_refs((link,) if link is not None else ())
        if not refs:
            return widget
        host = QWidget()
        host.setProperty("role", "block-item")
        box = QHBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.xxs)
        box.addWidget(widget, 0)
        box.addWidget(
            self._remove_button(refs, chip.label, self._own_tip(refs, f"能力「{chip.label}」"))
        )
        return host

    def _ability_link(self, chip) -> CellRef | None:
        """这一格能力落在本单位的哪一行 UnitAbilities 上（没有就不是本单位的）。"""
        if not chip.row_id or self._instance is None:
            return None
        return owner_link_ref(self._instance, self._unit_id, "Abilities", chip.row_id)

    def _abilities_section(self, card) -> QWidget:
        """能力那一块：标题右边补一个「＋」（从 Abilities 里挑一条挂到本单位）。"""
        frame = super()._abilities_section(card)
        self._attach_block_actions(frame, "abilities", f"能力（{len(card.abilities)}）")
        return frame

    def _squad_section(self, card) -> QWidget:
        """步兵班那一块（块 11）：标题右边补「＋」（加一名），每名成员行尾一个「✕」（减一名）。

        版式**不抄第二份**：行还是基类画的那一行（#序号 · 主武器 · 副武器），
        这里只做两件事 —— 给标题那一条加 ＋、给每一行的尾巴接一个 ✕。

        - 「＋」挑的是**本单位现有成员**（照他复制一行 SquadMembers）：一行成员不是模板行，
          而且照本单位的人复制才守得住「SquadMembers 的枪必须在同单位 SquadWeapons 里」
          那条 error 级不变量（见 EditorContext.add_squad_member）；
        - 「✕」删的是**本单位的**那一行 SquadMembers（_own_refs 先筛过：带 UnitId 且
          UnitId == 本单位），模板 / 别的单位一行不动。步兵班专用页（squad_page.py）
          那条 +1 / ✕ 的路**不拆** —— 那一条是整页视图，这一条是卡片上的入口。
        """
        frame = super()._squad_section(card)
        loadout = card.loadout
        self._attach_block_actions(frame, "squad", f"班组成员（{loadout.size}）")
        button = self._add_buttons.get("squad")
        if button is not None:
            button.setToolTip(
                "加一名士兵 —— 就地冒搜索框，挑一名照他复制一行 SquadMembers"
                "（新行 Id = 该表现有最大 +1；Ctrl+Z 能撤回来）"
            )
        layout = frame.layout()
        rows = (
            [layout.itemAt(index).widget() for index in range(layout.count())]
            if layout is not None
            else []
        )
        # 基类那一块里，带 role=card-flow 的widget 就是成员行（标题那一条是布局不是控件）。
        rows = [row for row in rows if row is not None and row.property("role") == "card-flow"]
        for member, row in zip(loadout.members, rows, strict=False):
            line = row.layout()
            if line is None:  # pragma: no cover - 版式变了
                continue
            refs = self._own_refs((CellRef("SquadMembers", member.member_id, "Id"),))
            if not refs:
                continue
            line.addWidget(
                self._remove_button(
                    refs,
                    f"{member.index}",
                    self._own_tip(refs, f"班里的第 {member.index} 名"),
                )
            )
        return frame

    def _block_takes_add(self, block: str) -> bool:
        """这一块在这张卡上给不给「＋」。

        ❗ 只有「运输」要看单位类型：`TransportAvailabilities.UnitId` 是**载具侧**
        （实测 227 行 = Type4 载具 214 + Type8 直升机 13，见 docs/关系-24表与修改入口.md §2.2）。
        步兵 / 战机 / 舰船卡上那个 ＋ 插出来的是**方向反了**的配对行（步兵当载具）——
        宁可不画，同「不给 ✕ 的两种情况」那条口径。
        「这一台车能运谁」的正经入口是运输配对，见该文档 §5 的 G5。
        """
        if block != "transports":
            return True
        card = self._card
        return card is not None and int(card.kind) in (TYPE_VEHICLE, TYPE_HELICOPTER)

    def _link_section(self, title: str, rows, empty: str) -> QWidget:
        """装甲 / 机动 / 传感器 / 运输 四块：标题右边补一个「＋」（块 7）。"""
        frame = super()._link_section(title, rows, empty)
        block = _BLOCK_BY_TITLE.get(title)
        if block is not None and self._block_takes_add(block):
            self._attach_block_actions(frame, block, title)
        return frame

    def _swap_target(self, row) -> tuple[str, int]:
        """「换…」列的是**这一格外键指向的表**，不是「这一行所在的表」。

        两者在「运输」那一块**不一样**，而且照 row.table 列就是错的：
        那一行住在 `TransportAvailabilities`（3 列、没有名字列），要换的是它那一格
        `SpecializationAvailabilityId` —— 候选必须是 `SpecializationAvailabilities`
        的行。照 row.table 列只会列成「另一些配对行」，而且那张表没有名字列、候选 0 条
        （用户实机：「那个换…也修一下」）。

        返回 (候选表, 不列出来的那个 Id)。候选表 == row.table 时保持原样
        （排除这一行自己）；换成外键指向的表时排除**这一格现在指着的那一行**。
        """
        link = getattr(row, "link", None)
        column = getattr(link, "column", "") if link is not None else ""
        target = foreign_table(column) if column else None
        if not target or target == row.table:
            return row.table, row.row_id
        return target, self._link_value(link)

    def _link_value(self, link) -> int:
        """这一格现在指着哪一行（含**未保存**的改动）；读不到给 0 = 不排除任何一条。"""
        if link is None:
            return 0
        value: object = None
        if self._changeset is not None:
            try:
                value = self._changeset.value(link.table, link.row_id, link.column)
            except Exception:  # noqa: BLE001 - 读不到不该让一个搜索框崩掉卡片
                value = None
        if value is None and self._instance is not None:
            row = find_row(self._instance, link.table, link.row_id)
            table = self._instance.table(link.table)
            if row is not None and link.column in table.columns:
                value = row[table.column_index(link.column)]
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def _link_row(self, row) -> QWidget:
        """装甲 / 机动 / 传感器 / 运输那一行：**改 · 克隆 · 删**三件事都在这儿。

        ⛔ 2026-10-19 用户命令删掉了「换」（原来那颗「🔁 换一个」）：
        「换」= 删除旧的 + 新增新的 ⇒ 改引用走「📄 克隆一份」或先删后加。

        - 改：点名字 -> 右栏换成那一行的全部列（值在那儿点着改）；
        - 克隆一份：复制这一行，再把本单位那一格指到副本上（core/edit/clone.py）。
          模板行被别人共用时，这是"只改我这一台"的唯一正确做法。
        - 删（块 7）：删**本单位那一行关联行** —— 这一块不再挂这一样，模板行不动。
        """
        host = QWidget()
        host.setProperty("role", "link-row")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.xxs)

        line = QWidget()
        row_box = QHBoxLayout(line)
        row_box.setContentsMargins(0, 0, 0, 0)
        row_box.setSpacing(TOKENS.spacing.xs)

        name = QPushButton(row.label)
        name.setProperty("role", "detail-link")
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.setToolTip(f"{link_tooltip(row)}\n点它看这一行的全部列（值在那儿改）")
        name.clicked.connect(lambda _=False, r=row: self.row_activated.emit(r.table, r.row_id))
        row_box.addWidget(name, 1)
        # ❗ 名字右边常显 `#Id`（与左卡另两处、中栏四块同一份口径与实现）。
        row_box.addWidget(card_id_badge(row), 0, Qt.AlignVCenter)

        # ⛔ 2026-10-19 用户命令：删掉「换」这个功能（AGENTS §0.10：只有 新增/删除/修改）。
        #    原来这里挂一颗「🔁 换一个」把 link 那一格改成另一个模板行 ——
        #    「换」本质就是删除旧的 + 新增新的，改引用请走「📄 克隆一份」或删掉再新增。

        clone: QPushButton | None = None
        if row.can_clone:
            clone = QPushButton("📄 克隆一份")
            clone.setProperty("role", "row-tool")
            clone.setCursor(Qt.CursorShape.PointingHandCursor)
            clone.setToolTip(
                "复制这一行给本单位专用，并把本单位指过去 ——\n"
                "别人用的还是原来那一行（模板行被共用时改它要这么做）"
            )
            row_box.addWidget(clone)

        # 块 7：删的是**本单位那一行关联行** —— 模板行（Armors / Mobility…）不动。
        refs = self._own_refs((row.link,) if row.link is not None else ())
        if refs:
            row_box.addWidget(
                self._remove_button(
                    refs, row.label, self._own_tip(refs, f"「{row.label}」在本单位的这一行挂载")
                )
            )

        box.addWidget(line)

        picker: RowPicker | None = None

        if clone is not None:
            clone.clicked.connect(
                lambda _=False, r=row: self.clone_requested.emit(r.table, r.row_id, r.link)
            )
        self._link_rows.append((row, host, picker))
        return host

    def _weapon_art(self, weapon) -> QWidget:
        """武器那一格 + 行尾的「✕」（块 7：删掉这门枪在本单位的弹药配置行）。"""
        art = super()._weapon_art(weapon)
        refs = self._own_refs(getattr(weapon, "refs", ()) or ())
        if not refs:
            return art
        host = QWidget()
        host.setProperty("role", "block-item")
        box = QHBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.xxs)
        box.addWidget(art, 1)
        box.addWidget(
            self._remove_button(
                refs,
                weapon.label,
                f"删掉 {weapon.label} 在本单位的 {len(refs)} 行 WeaponAmmunitions"
                f"（= 这台车不带这把枪）—— Weapons#{weapon.weapon_id} 还在库里，"
                "别的单位照用（Ctrl+Z 能撤回来）",
            )
        )
        return host

    def _weapons_section(self, card) -> QWidget:
        """武器那一块：标题右边补一个「＋」（加弹种），每把枪行尾一个「✕」。"""
        frame = super()._weapons_section(card)
        if card.weapons:
            self._attach_block_actions(frame, "weapons", f"武器（{len(card.weapons)}）")
        return frame

    def _options_section(self, card) -> QWidget:
        """改装槽那一块（块 7）：可以加一个槽；**空槽**（底下一条选项都没有）才给删。

        为什么不给"有选项的槽"画 ✕：删掉 Modifications 那一行，它底下的 Options
        还指着它（Options.ModificationId）—— 那是留下孤儿行，不是"这一块不挂这一样"。
        删槽这条口径设计 §5 第 2 条还没定，这里取**不牵连别人**的那一半。
        """
        frame = super()._options_section(card)
        self._attach_block_actions(frame, "modifications", f"改装槽（{len(card.options)}）")
        layout = frame.layout()
        rows = [layout.itemAt(index).widget() for index in range(layout.count())] if layout else []
        rows = [row for row in rows if row is not None and row.property("role") == "option-row"]
        for group, row in zip(card.options, rows, strict=False):
            if group.from_variant or self._options_of(group.modification_id):
                continue
            refs = self._own_refs((CellRef("Modifications", group.modification_id, "Id"),))
            if not refs:
                continue
            head = row.layout().itemAt(0)
            if head is None or head.layout() is None:  # pragma: no cover - 版式变了
                continue
            head.layout().addWidget(
                self._remove_button(
                    refs, group.label, self._own_tip(refs, f"空改装槽「{group.label}」")
                )
            )
        return frame

    def _options_of(self, modification_id: int) -> int:
        """这个槽底下有几条选项行（现算一次，重排时清缓存）。"""
        cached = self._modification_options.get(modification_id)
        if cached is not None:
            return cached
        table = self._instance.table("Options") if self._instance is not None else None
        count = 0
        if table is not None:
            index = table.column_index("ModificationId")
            count = sum(1 for row in table.rows if row[index] == modification_id)
        self._modification_options[modification_id] = count
        return count

    def _ammo_block(self, weapon, ammo) -> QWidget:
        """弹种那一栏：点它 = 进弹药页；栏尾一个「✕」= 删这一行 WeaponAmmunitions。

        ❗ 这一个 ✕ 才是设计 §块 7 那句「删 WeaponAmmunitions#21 = 这台车不带这把枪」
        的最小版：**一行**，也就是"这一个弹种不挂在这门枪上"。
        """
        block = super()._ammo_block(weapon, ammo)
        refs = self._own_refs(getattr(ammo, "refs", ()) or ())
        if refs:
            line = QWidget()
            line.setProperty("role", "block-item")
            box = QHBoxLayout(line)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(TOKENS.spacing.xs)
            box.addStretch(1)
            box.addWidget(
                self._remove_button(refs, ammo.label, self._own_tip(refs, f"弹种「{ammo.label}」"))
            )
            block.layout().addWidget(line)
        return block

    def _close_pickers(self, keep: RowPicker | None = None) -> None:
        """就地搜索框**一次只留一个**（＋ 与「换…」共用这一条）。

        用户实机反馈：先开「能力」那一块的搜索框，再滚到「武器」块点 ＋ ——
        卡上同时挂着两个搜索框、卡片被撑长。这里只 setVisible(False)，
        **不发 cancelled** —— 那个信号的意思是「用户点了取消」，收到它外层要收手。
        """
        pickers = list(self._pickers.values())
        pickers += [item[2] for item in self._link_rows if item[2] is not None]
        for picker in pickers:
            if picker is not keep:
                picker.setVisible(False)

    def _open_picker(self, picker: RowPicker) -> None:
        self._close_pickers(keep=picker)
        picker.setVisible(True)
        picker.start()


    def _portrait_section(self, card):
        frame = super()._portrait_section(card)
        self._portrait_frame = frame
        if self._validation_findings and self._validation_chip is not None:
            frame.add_overlay(self._validation_chip, row=1, column=1)
        return frame

    def _render(self, card) -> None:
        """重排前把点击目标清掉 —— 控件是新建的，旧的 id() 会留在表里。"""
        self._targets.clear()
        self._links.clear()
        self._ammo_targets.clear()
        self._row_targets.clear()
        self._link_rows = []
        self._add_buttons.clear()
        self._pickers.clear()
        self._block_frames.clear()
        self._removes.clear()
        self._modification_options.clear()
        self._portrait_frame = None
        super()._render(card)
        self._install_card_targets(card)

    def _install_card_targets(self, card) -> None:
        if card.cost_ref is not None:
            self._clickable(self._cost, card.cost_ref)
        # 抬头显示的是 HUDName（Name 是内部名）—— 点它就是改 HUDName。
        self._clickable(self._title, CellRef("Units", card.unit_id, "HUDName"))

    def _open_detail(self, group_key: int) -> None:
        """编辑器里点武器 = **去看那一行的词条**，不在这里展开"详细信息面板"。

        用户口径（2026-09-30 实机反馈）：

            "我点击武器的时候，它不会给我转移到武器词条，而是打开了详细信息面板，
             我不需要自动跳转到详细信息面板。"

        简略模式那一排武器原来走的是 _open_detail（切到 MODE_DETAIL + 展开左列），
        在编辑器里那条路是错的：卡片的职责是**导航**，右栏才是看词条的地方。
        详细模式本身还在（顶栏那个「详细」按钮用户自己点），只是不再**自动**跳。
        """
        weapons = self._card.weapons if self._card else ()
        weapon = next((row for row in weapons if row.group_key == group_key), None)
        if weapon is None:
            super()._open_detail(group_key)
            return
        self.weapon_activated.emit(weapon.weapon_id)

    def _toggle_weapon(self, group_key: int) -> None:
        """编辑器里点武器 = **进武器页**（设计 §2：点武器行 → 武器编辑器 40 列）。"""
        weapons = self._card.weapons if self._card else ()
        weapon = next((row for row in weapons if row.group_key == group_key), None)
        if weapon is None:
            super()._toggle_weapon(group_key)
            return
        self.weapon_activated.emit(weapon.weapon_id)


__all__ = ["EditableCardPanel"]
