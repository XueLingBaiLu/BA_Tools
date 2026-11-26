"""弹药专用页：左栏 = 这一行的上下文（图标 / 名称 / 本单位的携弹量 / 能打什么 /
同武器其它弹），右栏 = Ammunitions **67 列一条一行、往下滚**（设计稿 §3 块 6 与
设计图 `_work/design/block6.png`）。

三条口径：

1. **67 列一个不少**。右栏的列仍然全部来自 `context.cells_of_row("Ammunitions", id)`
   （core/edit/cells.py），这一页只决定**显示顺序** —— 而顺序就是**表自己的列序**：
   设计图画的正是 01 Id → 05 HUDMultiplier → 06 UiOptions → …→ 67 一路到底，
   没有小标题、不分组（见 ordered_columns）。
2. **"能打什么"是位掩码逐位解出来的中文**（core/edit/dictionary.py::decode，
   36 -> 「载具 + 舰船」），旁边留原始值备查；再画上卡片会画的那几张目标图
   （app/viewmodel/unit_card.py::ammo_target_traits）。
3. ⚠️ **携弹量在 `WeaponAmmunitions.Quantity`，不在弹药行上** —— Ammunitions 表
   的 67 列里压根没有 Quantity 这一列（实测）。卡片上那个数还会再乘
   `Ammunitions.HUDMultiplier`。

## 「一条一行、滚就滚」

右栏那 67 条由 widgets/row_entries.py::RowEntriesPanel 铺（QScrollArea + 一条一行
的 EntryRow），一屏放不下就滚 —— 设计图那句"一屏 30 条、滚一屏半"说的就是它。
**行高、序号格式、滚动条都在那个控件里**（`row_entries.ROW_HEIGHT`），这一页不重复实现。

## AMMO_COLUMN_GROUPS 不再是右栏的次序

那张表现在只做**功能归类**（"这一列是干什么的"）：左栏底部那份速查表与它的 tooltip
从它来。早期版本拿它给右栏重排（"分组只影响显示顺序"）—— 设计图 block6 定的是
一路按表列序往下滚，所以 `ordered_columns()` 已经改回表列序，一个列不筛不丢。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.armory.detail_panel import (
    AMMO_ICON_FAMILY,
    TARGET_ICON_FAMILY,
    icon_box,
)
from ba_mod_maker.app.screens.editor.entries_panel import table_emoji
from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.app.screens.editor.pages.base import EditorPage, row_title
from ba_mod_maker.app.theme.icons import prepare_icon  # 运行时要用（不是注解）
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.row_icon import RowMark, row_icon
from ba_mod_maker.app.viewmodel.unit_card import ammo_target_traits
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.dictionary import decode, field_info
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef

if TYPE_CHECKING:
    from ba_mod_maker.app.theme.icons import IconCache

TABLE = "Ammunitions"

#: 携弹量落在哪张表 —— **不是弹药行上**（设计 §5 第 4 步点名的那条）。
WEAPON_AMMO_TABLE = "WeaponAmmunitions"

#: 67 列的**功能归类**（不是右栏次序）：(标题, 一句话说明, 列名)。
#:
#: ⚠️ 右栏次序由 ordered_columns() 说了算（= 表自己的列序，设计图 block6）；
#:    这一份只回答"这一列是干什么的" —— 左栏底部那份速查表与它的 tooltip 用它。
#:
#: 分组顺序与文案对齐设计稿图 B ⑤（tools/gen_unit_editor_mock.py::AMMO_GROUPS）；
#: 设计稿那份只列了 55 列，剩下 12 列按语义并进就近的组（如 VfxCountPerShotMax
#: 并在「每发特效数量（下限）」旁边、AimTimeMin/MaxOverride 另立「瞄准 · 装填」）。
#: 有一条用例钉着：**67 列每一列都在且只在一个组里**。
AMMO_COLUMN_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "身份",
        "弹药模板表 · 只这一行",
        ("Id", "Name", "HUDName", "HUDIcon", "ModelFileName", "AudioPreset", "FXPreset"),
    ),
    (
        "弹道",
        "直射还是抛物线、飞多快",
        (
            "TrajectoryType",
            "MuzzleVelocity",
            "MaxSpeed",
            "Acceleration",
            "BurnTime",
            "LoftAngle",
            "LoftHeight",
        ),
    ),
    (
        "射程",
        "卡片上那几个 m 都 ×2",
        (
            "MinimalRange",
            "GroundRange",
            "LowAltRange",
            "HighAltRange",
            "OverflyHeight",
            "NonSeadProjectileRangeOverride",
            "SeadProjectileRangeOverride",
        ),
    ),
    (
        "伤害 · 穿深",
        "卡片「伤害 / 穿深 / 爆炸半径」的原料",
        (
            "Damage",
            "StressDamage",
            "DamageOverTimeDuration",
            "PenetrationAtMinRange",
            "PenetrationAtGroundRange",
            "ArmorTargeted",
            "HealthAOERadius",
            "StressAOERadius",
            "OverpressureRadius",
            "CriticMultiplier",
            "NoDamageFalloff",
            "TopArmorAttack",
            "IsTopArmorArmorAttack",
        ),
    ),
    (
        "散布",
        "卡片「武器散布」那一行",
        (
            "DispersionHorizontalRadius",
            "DispersionVerticalRadius",
            "DispersionMinimal",
            "LaunchMarginAngle",
        ),
    ),
    (
        "制导 · 导引头",
        "只有导弹用得上",
        (
            "Seeker",
            "LaserGuided",
            "SeekerAngle",
            "MaxSeekerDistance",
            "CanReaquire",
            "AimStartDelay",
            "RotationSpeed",
            "MainEngineIgnitionDelay",
            "PreEgnitionRotationSpeed",
        ),
    ),
    (
        "瞄准 · 装填",
        "非 0 时盖掉武器上的瞄准时间",
        ("AimTimeMinOverride", "AimTimeMaxOverride", "PreventReloadWhileTracked"),
    ),
    (
        "被打 / 防御",
        "「无视掩体」「可被 APS 拦截」在这一组",
        (
            "IgnoreCover",
            "CanBeIntercepted",
            "CanBeTargeted",
            "GenerateSmoke",
            "SmokeRadius",
            "SmokeDuration",
            "SmokeFadeDuration",
        ),
    ),
    (
        "能打什么",
        "位掩码 -> 一串中文（不是数字）",
        ("TargetType", "UiOptions", "Airburst"),
    ),
    (
        "补给",
        "卡片「补给值 / 补给时间 / 补给消耗」",
        (
            "SupplyCost",
            "ResupplyTime",
            "HUDMultiplier",
            "VfxCountPerShot",
            "VfxCountPerShotMax",
            "RadioFuseDistance",
        ),
    ),
    (
        "其它",
        "词典标了「没解，别当依据」的那一列",
        ("StripingIgnore",),
    ),
)


def column_group(column: str) -> str | None:
    """这一列属于哪个**功能归类**；表里没提到就返回 None（不编一个组给它）。

    ⚠️ 只喂左栏那份速查表，**不影响右栏次序**（见 ordered_columns）。
    """
    for title, _note, columns in AMMO_COLUMN_GROUPS:
        if column in columns:
            return title
    return None


def ordered_columns(columns: Sequence[str]) -> tuple[str, ...]:
    """右栏的显示顺序 = **表自己的列序**（设计图 block6：01…67 一条一行，往下滚）。

    ❗ **这里不重排**：设计 §3 块 6 与设计图 block6.png 画的正是 Ammunitions 的
    列序（01 Id / 05 HUDMultiplier / 06 UiOptions / … / 67），与 `cells_of_row`
    现取的顺序逐列一致。曾经按 AMMO_COLUMN_GROUPS 重排（"分组只影响显示顺序"）
    是重做前的口径 —— 照设计图改回表列序。

    留着它是因为"右栏次序"只该有一个出处（entries_cells 只问它），顺便钉住
    **一列不筛、一列不丢、不重复**：进去什么列，出来还是那些列、那个序。
    """
    return tuple(columns)


def default_icons() -> IconCache | None:
    """这一页默认的图标来源 —— 进程内共享那一份 IconCache。

    ❗ 真实应用里 `UnitEditorScreen` 建主编辑页时已经调过 `shared_icon_cache()`，
    这里只是**复用同一份**（不是为这一页新起一条加载线程；那份缓存本来就是进程级的）。
    测试与"没装游戏"的环境把它换成替身或 None 即可 —— 换掉之后这一页照常能建，
    只是图标退回文字。
    """
    from ba_mod_maker.app.theme.icons import shared_icon_cache

    return shared_icon_cache()


class AmmoPage(EditorPage):
    """一个弹种（含"本单位的携弹量"、"能打什么"与"同武器的其它弹"）。"""

    def __init__(
        self,
        weapon_id: int,
        ammo_id: int,
        parent: QWidget | None = None,
        *,
        icons: IconCache | None = None,
    ) -> None:
        # 顶栏那两行**不带弹药 Id**：这一页能就地换弹种（见 switch_to），
        # 写死 Id 的话换完就成了错的。Id 在左栏那块牌子上，跟着换。
        super().__init__("弹药", f"{TABLE} 67 列 · 一条一行，往下滚")
        self._weapon_id = weapon_id
        self._ammo_id = ammo_id
        self._icons = icons
        self._icons_connected = False
        self._icon_shown = False
        self._art_text = ""
        #: 携弹量那一格的浮层（它落在 WeaponAmmunitions 上，右栏里没有它）。
        self._quantity_editor: InlineEditor | None = None
        self._quantity_slot: QVBoxLayout | None = None
        # 构造时就接上 loaded：外层直接塞进来的图标来源，图到货了也要能补上。
        self._connect_icons()
        # ❗ 面板右上角那颗「⬅ 回单位（N 条）」（见 entries_back_label）落在
        #    back_to_unit_requested 上，而这一页是**被 push 上来的专用页**：
        #    UnitEditorScreen._push 只给专用页接了 back_requested -> close_page，
        #    _main.back_to_unit_requested 那条是主编辑页自己的（→ show_unit_row）。
        #    不接这一步，那颗按钮就是死键（点了没反应）。接上之后它的语义与标签一致：
        #    关掉这一页回单位编辑器（主编辑页面就是本单位那一行）。
        self.back_to_unit_requested.connect(self.back_requested.emit)

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #

    @property
    def weapon_id(self) -> int:
        return self._weapon_id

    @property
    def ammo_id(self) -> int:
        return self._ammo_id

    @property
    def icons(self) -> IconCache | None:
        return self._icons

    def set_icons(self, icons: IconCache | None) -> None:
        """换图标来源（测试给替身；真实应用由外层给）。"""
        self._icons = icons
        self._icons_connected = False
        self._connect_icons()
        if self.context is not None:
            self._rebuild_left_only()

    @property
    def icon_shown(self) -> bool:
        """左栏那个弹种图标画出来了吗（False = 退回文字）。"""
        return self._icon_shown

    @property
    def icon_fallback_text(self) -> str:
        """没图时留在那个位置上的文字（有图时是空串）—— 绝不留空白。"""
        return self._art_text

    def switch_to(self, ammo_id: int) -> bool:
        """页内换弹种：**不换页、不改基类**，只把这一页指向同一门武器的另一个弹。

        换页不是编辑 —— 不写数据、不发 changed。返回是否真的换了。
        """
        if ammo_id == self._ammo_id:
            return False
        context = self.context
        if context is None or context.cell(CellRef(TABLE, ammo_id, "Id")) is None:
            return False
        self._ammo_id = ammo_id
        self._rebuild()
        return True

    # ------------------------------------------------------------------ #
    # 读数据（左栏与测试共用同一份）
    # ------------------------------------------------------------------ #

    def column_names(self) -> tuple[str, ...]:
        """右栏实际画出来的列（按显示顺序）。"""
        return tuple(cell.column for cell in self.entries_cells())

    def quantity_cell(self) -> FieldCell | None:
        """本单位的携弹量那一格 —— **WeaponAmmunitions.Quantity**，不是弹药行上的数。"""
        context = self.context
        if context is None:
            return None
        for ammo_id, _order, row_id in self._weapon_ammo():
            if ammo_id == self._ammo_id:
                return context.cell(CellRef(WEAPON_AMMO_TABLE, row_id, "Quantity"))
        return None

    def quantity_raw(self) -> Any:
        """携弹量的原始值（表里那一格是什么就是什么）；这个单位没配就是 None。"""
        cell = self.quantity_cell()
        return cell.raw if cell is not None else None

    def quantity_text(self) -> str:
        cell = self.quantity_cell()
        return cell.text if cell is not None else ""

    def target_cell(self) -> FieldCell | None:
        return self._cell("TargetType")

    def target_text(self) -> str:
        """TargetType 逐位解出来的中文（36 -> 「载具 + 舰船」）。"""
        cell = self.target_cell()
        return cell.text if cell is not None else ""

    def target_raw_text(self) -> str:
        """原始值备查（"36"）。"""
        cell = self.target_cell()
        return cell.raw_text if cell is not None else ""

    def target_bits(self) -> tuple[str, ...]:
        """逐位解出来的那几位中文（顺序按位值从小到大）。"""
        cell = self.target_cell()
        if cell is None:
            return ()
        return decode(field_info(TABLE, "TargetType"), cell.raw).bits

    def ammo_choices(self) -> tuple[tuple[int, str, str], ...]:
        """这一门武器在本单位下的弹种：(弹种 Id, 显示名, 携弹量文本)。

        行来自 `context.row_ids("WeaponAmmunitions")` —— 哪些行属于这个单位是
        core/edit/scope.py 说的，界面只按 WeaponId 过滤，**不在界面里另算一遍归属**。
        """
        context = self.context
        if context is None:
            return ()
        out: list[tuple[int, str, str]] = []
        seen: set[int] = set()
        for ammo_id, _order, row_id in self._weapon_ammo():
            if ammo_id in seen:
                continue
            seen.add(ammo_id)
            name = context.cell(CellRef(TABLE, ammo_id, "HUDName"))
            label = name.text if name is not None and name.text else f"弹药 #{ammo_id}"
            quantity = context.cell(CellRef(WEAPON_AMMO_TABLE, row_id, "Quantity"))
            out.append((ammo_id, label, quantity.text if quantity is not None else "—"))
        return tuple(out)

    def _cell(self, column: str) -> FieldCell | None:
        context = self.context
        if context is None:
            return None
        return context.cell(CellRef(TABLE, self._ammo_id, column))

    def _weapon_ammo(self) -> tuple[tuple[int, int, int], ...]:
        """这一门武器在本单位下的携弹行：((弹种 Id, Order, 行 Id), ...)。"""
        context = self.context
        if context is None:
            return ()
        out: list[tuple[int, int, int]] = []
        for row_id in context.row_ids(WEAPON_AMMO_TABLE):
            weapon = context.cell(CellRef(WEAPON_AMMO_TABLE, row_id, "WeaponId"))
            ammo = context.cell(CellRef(WEAPON_AMMO_TABLE, row_id, "AmmunitionId"))
            if weapon is None or ammo is None:
                continue
            if weapon.raw != self._weapon_id:
                continue
            ammo_id = _as_int(ammo.raw)
            if ammo_id is None:
                continue
            order = context.cell(CellRef(WEAPON_AMMO_TABLE, row_id, "Order"))
            out.append((ammo_id, _as_int(order.raw) if order is not None else 0, row_id))
        out.sort(key=lambda item: (item[1] or 0, item[0], item[2]))
        return tuple(out)

    # ------------------------------------------------------------------ #
    # 左栏
    # ------------------------------------------------------------------ #

    def build_left(self) -> QWidget | None:
        assert self.context is not None
        # 旧左栏（连同挂在它上面的携弹量浮层）马上要被销毁 —— 引用先清掉。
        self._quantity_editor = None
        self._quantity_slot = None
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.md)
        box.addWidget(self._identity())
        box.addWidget(self._targets())
        box.addWidget(self._choices())
        box.addWidget(self._groups_note())
        box.addStretch(1)
        return host

    def _frame(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setProperty("role", "editor-row")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        inner.setSpacing(TOKENS.spacing.xs)
        return frame, inner

    def _identity(self) -> QWidget:
        """图标 + HUDName + 内部名 + 共用数 + 本单位的携弹量。"""
        context = self.context
        assert context is not None
        frame, inner = self._frame()

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.md)
        head.addWidget(self._art(), 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(TOKENS.spacing.xxs)
        name = self._cell("HUDName")
        title = QLabel(name.text if name is not None and name.text else f"弹药 #{self._ammo_id}")
        title.setProperty("role", "entries-title")
        title.setWordWrap(True)
        column.addWidget(title)

        place = QLabel(f"弹药模板 #{self._ammo_id} · {TABLE}#{self._ammo_id}")
        place.setProperty("role", "entries-subtitle")
        column.addWidget(place)

        internal = self._cell("Name")
        if internal is not None and internal.text:
            note = QLabel(f"内部名 {internal.text}")
            note.setProperty("role", "entry-count")
            note.setWordWrap(True)
            column.addWidget(note)
        if name is None:
            missing = QLabel("（这一行弹药不在这份数据里）")
            missing.setProperty("role", "inline-error")
            column.addWidget(missing)
        head.addLayout(column, 1)
        inner.addLayout(head)

        quantity = self.quantity_cell()
        if quantity is None:
            empty = QLabel(
                f"本单位的携弹量：（这个单位没配这个弹 —— {WEAPON_AMMO_TABLE} 里没有对应的行）"
            )
            empty.setProperty("role", "entry-place")
            empty.setWordWrap(True)
            inner.addWidget(empty)
        else:
            button = QPushButton(
                f"本单位的携弹量 ×{quantity.text} —— 存在「武器携弹」表，不在弹药行上"
            )
            button.setProperty("role", "entry-value")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{quantity.place} —— 点一下就地改（Ctrl+Z 撤销）")
            button.clicked.connect(lambda _checked=False, cell=quantity: self._edit_quantity(cell))
            inner.addWidget(button)
            # 携弹量那一行**不在右栏**（右栏画的是弹药行的 67 列），所以浮层挂在这里 ——
            # 基类的 _open_inline_editor 只往右栏那一行下面挂。
            self._quantity_slot = QVBoxLayout()
            self._quantity_slot.setContentsMargins(0, 0, 0, 0)
            inner.addLayout(self._quantity_slot)
            inner.addWidget(self._multiplier_note())

        return frame

    def _multiplier_note(self) -> QWidget:
        """卡片上那个数 = 本表这一格 × HUDMultiplier（两列都要说清楚，别混）。"""
        cell = self._cell("HUDMultiplier")
        if cell is not None:
            text = (
                "卡片上画的携弹量 = 本数 × Ammunitions.HUDMultiplier"
                f"（本行 {cell.text}）—— 两列都在右栏里"
            )
        else:
            text = "卡片上画的携弹量还要再乘 Ammunitions.HUDMultiplier"
        note = QLabel(text)
        note.setProperty("role", "entry-place")
        note.setWordWrap(True)
        return note

    def _edit_quantity(self, cell: FieldCell) -> None:
        """点开携弹量那一格：就地编辑浮层挂在**左栏**（右栏里没有这一格）。"""
        context = self.context
        if context is None or self._quantity_slot is None:
            return
        self._close_quantity_editor()
        editor = InlineEditor(cell, instance=context.instance, link_ref=context.link_ref(cell.ref))
        editor.committed.connect(lambda value, ref=cell.ref: self._commit_quantity(ref, value))
        editor.cancelled.connect(self._close_quantity_editor)
        editor.open_place_requested.connect(self.open_table_row_requested)
        self._quantity_slot.addWidget(editor)
        self._quantity_editor = editor

    def _commit_quantity(self, ref: CellRef, value: Any) -> None:
        """写回变更集（走基类那条路），再让左栏那个数跟着变。"""
        self._close_quantity_editor()
        self._commit(ref, value)
        self.refresh()

    def _close_quantity_editor(self) -> None:
        if self._quantity_editor is not None:
            detach(self._quantity_editor)
            self._quantity_editor = None

    @property
    def quantity_editor(self) -> InlineEditor | None:
        """携弹量那一格的浮层（没点开就是 None）。"""
        return self._quantity_editor

    @property
    def quantity_editor_open(self) -> bool:
        """携弹量的就地编辑浮层开着吗。"""
        return self._quantity_editor is not None

    def _art(self) -> QLabel:
        """弹种图标（族 ammo_icons，键 Ammunitions.HUDIcon）。

        取不到（没装游戏 / 图还没解析完 / 这一行没键）就**退回文字** ——
        设计稿里这个位置是图，但留一个空白框比写两个字更糟。
        """
        box = icon_box(AMMO_ICON_FAMILY)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = self._cell("HUDIcon")
        key = icon.text if icon is not None else ""
        icons = self._icon_cache()
        pixmap = icons.pixmap(AMMO_ICON_FAMILY, key) if icons is not None and key else None

        self._icon_shown = False
        self._art_text = ""
        if pixmap is not None and not pixmap.isNull():
            label.setFixedSize(box)
            # ❗ 走唯一入口 prepare_icon：这一族是 ammo_icons，**不翻**（子弹本来就朝右），
            #    但 DPR 那段规则与别处只能是同一份。
            label.setPixmap(prepare_icon(pixmap, AMMO_ICON_FAMILY, box, label, key))
            label.setToolTip(f"{AMMO_ICON_FAMILY} · {key}")
            self._icon_shown = True
        else:
            name = self._cell("HUDName")
            self._art_text = (
                name.text if name is not None and name.text else f"弹药 #{self._ammo_id}"
            )
            label.setText(self._art_text)
            label.setProperty("role", "entry-name")
            label.setWordWrap(True)
            label.setMinimumWidth(box.width())
            label.setToolTip(f"图标取不到（键 {key or '—'}）—— 先显示弹种名，图到了会自动补上")
        return label

    def _connect_icons(self) -> None:
        """图标是异步到的：到了就重画左栏。"""
        loaded = getattr(self._icons, "loaded", None)
        if loaded is None or self._icons_connected:
            return
        loaded.connect(self.refresh)
        self._icons_connected = True

    def _icon_cache(self) -> IconCache | None:
        if self._icons is None:
            self._icons = default_icons()
            self._connect_icons()
        return self._icons

    def _targets(self) -> QWidget:
        """「能打什么」：位掩码逐位解成中文 + 原始值备查 + 卡片会画的那几张目标图。"""
        frame, inner = self._frame()

        head = QLabel("能打什么（位掩码逐位解出来的，不是数字）")
        head.setProperty("role", "detail-section")
        head.setWordWrap(True)
        inner.addWidget(head)

        cell = self.target_cell()
        if cell is None:
            missing = QLabel("（读不到这一行弹药）")
            missing.setProperty("role", "entry-place")
            inner.addWidget(missing)
            return frame

        decoded = QLabel(cell.text)
        decoded.setProperty("role", "entry-name")
        decoded.setWordWrap(True)
        inner.addWidget(decoded)

        bits = self.target_bits()
        if bits:
            each = QLabel("逐位：" + "、".join(bits))
            each.setProperty("role", "entry-count")
            each.setWordWrap(True)
            inner.addWidget(each)

        raw = QLabel(f"原始值 TargetType = {cell.raw_text}（备查）")
        raw.setProperty("role", "entry-place")
        raw.setWordWrap(True)
        inner.addWidget(raw)

        inner.addWidget(self._target_art())
        return frame

    def _target_art(self) -> QWidget:
        """卡片上会画的那几张目标图（ammo_target_traits）—— 图取不到就退回它的文案。"""
        context = self.context
        assert context is not None
        host = QWidget()
        line = QHBoxLayout(host)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(TOKENS.spacing.xs)

        row = context.data.ammo(self._ammo_id)
        traits = ammo_target_traits(context.data, row) if row is not None else ()
        if not traits:
            # Ground(1) 与 Ship(32) 游戏里就没有素材（见 ammo_target_traits 的注释）——
            # 这里如实说「游戏也不画」，不是漏画。
            note = QLabel(
                "（这一行弹药的目标类型，游戏里本来就不画图标 —— 地面 / 舰船两位没有素材）"
            )
            note.setProperty("role", "entry-place")
            note.setWordWrap(True)
            line.addWidget(note)
            line.addStretch(1)
            return host

        box = icon_box(TARGET_ICON_FAMILY)
        icons = self._icon_cache()
        for trait in traits:
            chip = QLabel(trait.label)
            chip.setProperty("role", "entry-place")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(trait.label)
            chip.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            pixmap = icons.pixmap(TARGET_ICON_FAMILY, trait.icon) if icons is not None else None
            if pixmap is not None and not pixmap.isNull():
                chip.setText("")
                chip.setFixedSize(box)
                chip.setPixmap(prepare_icon(pixmap, TARGET_ICON_FAMILY, box, chip, trait.icon))
            else:
                chip.setMinimumSize(box)
            line.addWidget(chip)
        line.addStretch(1)
        return host

    def _choices(self) -> QWidget:
        """同武器下的弹种：点一下换本页的弹（页内换，不动页栈）。"""
        frame, inner = self._frame()

        head = QLabel(f"这一门武器（Weapons#{self._weapon_id}）能装的弹（点一下换过来）")
        head.setProperty("role", "detail-section")
        head.setWordWrap(True)
        inner.addWidget(head)

        choices = self.ammo_choices()
        if not choices:
            note = QLabel(
                f"（这个单位的 {WEAPON_AMMO_TABLE} 里没有这一门武器 —— 换弹种要去武器页）"
            )
            note.setProperty("role", "entry-place")
            note.setWordWrap(True)
            inner.addWidget(note)
            return frame

        for ammo_id, label, quantity in choices:
            current = ammo_id == self._ammo_id
            button = QPushButton()
            button.setProperty("role", "entry-value")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if current:
                # 当前这一行只用文字标出来：没有「选中态」的 QSS 角色可用，
                # 不拿 dirty（那是「已改动」）冒充。
                button.setText(f"▸ {label}　弹药模板 #{ammo_id}（当前）")
                button.setToolTip("这一页正在看的就是它")
            else:
                button.setText(f"{label}　弹药模板 #{ammo_id}　携弹量 ×{quantity}")
                button.setToolTip(f"点一下换到弹药模板 #{ammo_id}（不写数据，只是换一页看）")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(
                    lambda _checked=False, target=ammo_id: self.switch_to(target)
                )
            inner.addWidget(button)

        if len(choices) == 1:
            only = QLabel("（这门武器在本单位下只有这一个弹种）")
            only.setProperty("role", "entry-place")
            inner.addWidget(only)
        return frame

    def _groups_note(self) -> QWidget:
        """右栏那 67 列一条一行（表列序）往下滚；这一份是**功能归类**速查。

        分组不再决定右栏次序（设计图 block6 要的是表列序），所以这句话必须说清
        「归类而已」—— 否则它和右栏眼见的次序自相矛盾。
        """
        columns = self.column_names()
        parts: list[str] = []
        for title, _note, group in AMMO_COLUMN_GROUPS:
            present = [column for column in group if column in columns]
            if present:
                parts.append(f"{title} {len(present)}")
        label = QLabel(
            f"右栏 {len(columns)} 列按表列序一条一行（往下滚）。"
            "这份是功能归类，只回答「这一列是干什么的」：" + " · ".join(parts)
        )
        label.setProperty("role", "entry-count")
        label.setWordWrap(True)
        label.setToolTip(
            "\n".join(
                f"{title}：{'、'.join(column for column in group if column in columns)}"
                for title, _note, group in AMMO_COLUMN_GROUPS
                if any(column in columns for column in group)
            )
        )
        return label

    # ------------------------------------------------------------------ #
    # 右栏
    # ------------------------------------------------------------------ #

    def focus_row_ref(self):
        """右栏现在这一行（弹药模板）—— 通用词条面板表头的「换/克隆」按它算。"""
        if not self._ammo_id:
            return None
        return RowRef(TABLE, self._ammo_id)

    def entries_cells(self) -> tuple[FieldCell, ...]:
        """这一行弹药的全部格子 —— **67 列一个不少**，按**表列序**一条一行。"""
        context = self.context
        if context is None:
            return ()
        cells = context.cells_of_row(TABLE, self._ammo_id)
        if not cells:
            return ()
        by_column = {cell.column: cell for cell in cells}
        return tuple(by_column[column] for column in ordered_columns(list(by_column)))

    def entries_title(self) -> str:
        """面板抬头 = **这一行叫什么**（设计图 block6 写的是「12.7x99mm」）。

        名字只认一份出处：base.row_title -> core/edit/foreign.display_name
        （Ammunitions#19 实测给的就是 HUDName 的 12.7x99mm）；查不到才退回「弹药 #19」。
        """
        context = self.context
        if context is None:
            return f"弹药 #{self._ammo_id}"
        return row_title(context.instance, RowRef(TABLE, self._ammo_id))

    def entries_subtitle(self) -> str:
        """设计图 block6 那一行：Ammunitions#19 · 一条一行，往下滚。

        「67 条词条」那截在右端的小结里（同一个面板头的两个标签，见 entries_summary）。
        """
        return f"{TABLE}#{self._ammo_id} · 一条一行，往下滚"

    def entries_summary(self) -> str:
        """面板头右端 = **一共几条词条**（设计图 block6 写的就是「67 条词条」）。

        ⚠️ 设计图还有一句「一屏 30 条 · 下面还有 37 条」：一屏放得下几条由窗口高度
        与 row_entries.ROW_HEIGHT 决定，这一页算不出来（写死一个数，窗口一变就是假的），
        所以这里只写算得出的那个。滚动条与「往下滚」由 RowEntriesPanel 保证。
        """
        return f"{len(self.entries_cells())} 条词条"

    def entries_back_label(self) -> str | None:
        """面板右上角那颗「⬅ 回单位（31 条）」—— 设计图 block6 的返回键之一。

        条数 = **Units 那一行的列数**（实测 31），与 base.entries_back_label() 同一个
        口径；那一份只在有 focus 时才画，而这一页的右栏就是「弹药这一行」、不走
        _focus_ref，所以要在这里给出来（否则面板头一颗返回键都没有）。

        ❗ 设计图另一个键「⬅ 回武器（40 条）」**没有做**：面板只支持一个 back 键，
        而且 _push 只把 back_requested 接给 close_page，没有「回上一页」那条路 ——
        要做得动 base.py / widgets/row_entries.py / unit_editor_screen.py（都不在
        这一页的写入范围）。别在这里假装有。
        """
        context = self.context
        if context is None:
            return None
        count = len(context.cells_of_row("Units", context.unit_id))
        if count <= 0:
            return None
        return f"⬅ 回单位（{count} 条）"

    def entries_mark(self) -> RowMark | None:
        """抬头左边那个**图标位**：弹种的真图（Ammunitions.HUDIcon）+ 没图时的 💥。

        用户口径 2026-10-01：「我不是要 emoji，我是要图标」—— 画真图还是 emoji 由
        图标位自己决定（RowEntriesPanel.refresh_icon），这里只回答「该画哪张」。
        base.entries_mark() 只在有 focus 时给，而这一页没有 focus；不给的话，同一行
        （Ammunitions）在弹药页抬头没有图标、在主编辑页却有 —— 两处不一样。
        """
        context = self.context
        instance = context.instance if context is not None else None
        return RowMark(icon=row_icon(instance, TABLE, self._ammo_id), emoji=table_emoji(TABLE))

    def entries_order(self) -> tuple[str, ...]:
        return (TABLE,)


def _as_int(value: Any) -> int | None:
    """整数就是整数；bool 与别的类型一律返回 None（不猜）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


__all__ = [
    "AMMO_COLUMN_GROUPS",
    "TABLE",
    "WEAPON_AMMO_TABLE",
    "AmmoPage",
    "column_group",
    "default_icons",
    "ordered_columns",
]
