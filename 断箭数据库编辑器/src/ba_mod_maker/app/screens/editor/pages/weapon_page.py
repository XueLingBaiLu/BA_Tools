"""武器专用页：剪影 + 装在哪几个炮塔 + 这个单位能装的弹；右栏 = Weapons 40 列。

设计 §4 与图 B ④（单件大图 docs/设计稿-武器编辑器.png）。用户口径：
「点武器行 → 武器编辑器」。**40 列一个不少** —— 列数与顺序都由
`core/edit/cells.py::row_cells` 现取，不在界面里写死。

## 三条实测（这一页存在的理由）

1. `Weapons.WeaponChannel / WeaponPriority` 全 804 行都是 0（**占位**），
   真值在 `TurretWeapons` 同名两列 —— 所以「装在哪」把挂载行那两列摆出来，
   点炮塔名就把右栏换成**那条挂载记录**（那儿才改得动）。
2. 携弹量在 **`WeaponAmmunitions.Quantity`**，不在弹药行上 —— 「能装的弹」
   一行一条**携弹记录**（不是一行一个弹种 Id），所以每行的 ×N 与它下面那行
   「武器携弹 #905」一一对应，点 ×N 就地改的就是那一行。
   ⚠️ 卡片上那个数（×210）是 `ΣQuantity × Ammunitions.HUDMultiplier`，
   这一页显示的是**原始携弹量**（×70）—— 别把两个数当成一回事。
3. 右栏 40 列**按功能分组**（身份 / 瞄准·稳定 / 射击 / 齐射 / 炮口指向 / 标志位）：
   分组**只影响显示顺序**，列仍然全部来自 `cells_of_row("Weapons", id)`；
   没被那 6 组点名的两列（上面那两个占位列）按原序垫在最后，一个不少。

## 页内换右栏（「点弹种进弹药页」）

设计说「点弹种 → 弹药编辑器（67 列）」。这一页**没有** ammo_requested 那种信号，
也不许改基类，所以就在本页内换右栏：

| 视图（`_focus_table`） | 右栏 | 怎么进 |
|---|---|---|
| `Weapons`（默认） | 这一把武器的 40 列 | 打开这一页 / 「← 回到武器（40 列）」 |
| `TurretWeapons` | 这条挂载记录的 6 列 | 点「装在哪」里的炮塔名 |
| `Ammunitions` | 弹药 67 列 **+ 那条 WeaponAmmunitions 6 列** | 点「能装的弹」里的弹种 |

弹药那一格把携弹行一起列出来是**故意的**：携弹量不在弹药行上，不列出来就没地方改。
右栏那一串是**两张表的格子拼成的一条平铺列表**（Ammunitions 的 67 列 + 那条
WeaponAmmunitions 的 6 列），每一行的小字自己写着落在哪张表。

⚠️ 左栏那一格点下去时，**先把右栏换到含它的视图再开浮层** ——
`RowEntriesPanel.open_editor`（widgets/row_entries.py）找不到那一行时会静默
什么都不显示（浮层建了却没处挂）。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.pages.base import EditorContext, EditorPage
from ba_mod_maker.app.theme.icons import IconCache, prepare_icon, shared_icon_cache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core.assets.families import AMMO_ICON_FAMILY, WEAPON_ICON_FAMILY
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.refs import CellRef

#: 这一页的主题：一把武器。
TABLE = "Weapons"
#: 弹种（点一下要「进」的那一页）。
AMMO_TABLE = "Ammunitions"
#: 携弹记录（**携弹量在这儿**，不在弹药行上）。
LINK_TABLE = "WeaponAmmunitions"
#: 炮塔挂载记录（**武器通道 / 优先级真值在这儿**）。
MOUNT_TABLE = "TurretWeapons"
#: 炮塔模板（名字）。
TURRET_TABLE = "Turrets"
#: 本单位的炮塔归属（TurretUnits -> TurretWeapons 那一跳的起点）。
TURRET_UNIT_TABLE = "TurretUnits"

#: 图标族键从 core/assets/families.py 取（字面量只在那儿一份）——
#: 这一页用的是 `Weapons.HUDIcon` / `Ammunitions.HUDIcon` 两族。

#: 武器剪影的**英雄盒**（逻辑像素）。原图 231×82 ≈ 2.8:1，比卡片上那颗大得多
#: —— 这一页左栏就这一张图。盒子固定：图是异步到的，先占好位子免得一到货就跳。
HERO_BOX = QSize(288, 96)
#: 弹种行前面那颗小图标（原图 174×36 ≈ 4.9:1）。
AMMO_ICON_BOX = QSize(66, 14)

#: 40 列的**功能分组**（顺序即右栏的阅读序）。与设计图 B ④ 的组标题同一份口径
#:（tools/gen_unit_editor_mock.py::WEAPON_GROUPS）。
#: ❗ 这只是**排序**：没被点名的列（WeaponChannel / WeaponPriority 两个占位列）
#:   按原序垫在最后，仍然一个不少 —— tests/test_editor_weapon_page.py 有一条用例
#:   钉死「6 组刚好点全 38 列 + 2 个占位列」。
FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("身份", ("Id", "Name", "HUDName", "Type", "HUDIcon", "ModelFileName", "AudioPreset")),
    (
        "瞄准·稳定",
        (
            "AimTimeMin",
            "AimTimeMax",
            "CanShootOnTheMove",
            "StabilizerQuality",
            "MultiTargetTracking",
            "SimultaneousTracking",
        ),
    ),
    (
        "射击",
        (
            "MagazineSize",
            "MagazineReloadTimeMin",
            "MagazineReloadTimeMax",
            "ShotsPerBurstMin",
            "ShotsPerBurstMax",
            "TimeBetweenBurstsMin",
            "TimeBetweenBurstsMax",
            "TimeBetweenShotsInBurst",
        ),
    ),
    ("齐射", ("ShortGroundAttackBurst", "NormalGroundAttackBurst", "LongGroundAttackBurst")),
    ("炮口指向", ("LowerVerticalAngle", "UpperVerticalAngles", "VerticalRotationSpeed")),
    (
        "标志位",
        (
            "AutoLoaded",
            "Silent",
            "HasPriorityOnChassis",
            "CQC",
            "IsRadarDependent",
            "IsVerticalLauncher",
            "IsLowAltDirectWeapon",
            "IsHighAltDirectWeapon",
            "IsUnderbarrel",
            "CanBeMerged",
            "FlashPerShot",
        ),
    ),
)


def order_columns(
    cells: tuple[FieldCell, ...],
    groups: tuple[tuple[str, tuple[str, ...]], ...] = FIELD_GROUPS,
) -> tuple[FieldCell, ...]:
    """按功能分组重排，**一个不少、不重复**。

    组内按组里给的列序；没被任何组点名的列按原序垫在最后。所以分组表里
    拼错一个列名不会丢列 —— 那一列会落到「垫后」里（有一条用例专门盯这件事）。
    """
    by_column = {cell.column: cell for cell in cells}
    out: list[FieldCell] = []
    taken: set[str] = set()
    for _title, columns in groups:
        for column in columns:
            cell = by_column.get(column)
            if cell is not None and column not in taken:
                out.append(cell)
                taken.add(column)
    out.extend(cell for cell in cells if cell.column not in taken)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Mount:
    """一把武器在**本单位**的一个挂载点：哪个炮塔、哪条 `TurretWeapons` 记录。"""

    turret_id: int
    turret_name: str
    row_id: int
    order: int
    channel: int
    priority: int

    @property
    def headline(self) -> str:
        """这一行「落在哪」的小字。"""
        return f"炮塔模板 #{self.turret_id} · 挂载行 {MOUNT_TABLE}#{self.row_id}"

    @property
    def detail(self) -> str:
        """武器通道 / 优先级 —— 这两列在 Weapons 表里是占位，真值在这儿。"""
        return f"顺序 {self.order} · 武器通道 {self.channel} · 武器优先级 {self.priority}"


@dataclass(frozen=True, slots=True)
class AmmoLine:
    """本单位这门武器的**一条携弹记录**（= `WeaponAmmunitions` 的一行）。"""

    row_id: int
    ammo_id: int
    label: str
    icon: str
    quantity: int | None
    order: int

    @property
    def quantity_ref(self) -> CellRef:
        """「点一下就地改携弹量」写的那一格。"""
        return CellRef(LINK_TABLE, self.row_id, "Quantity")

    @property
    def quantity_text(self) -> str:
        return f"×{self.quantity}" if self.quantity is not None else "×?"

    @property
    def place(self) -> str:
        return f"弹药模板 #{self.ammo_id} · 武器携弹 #{self.row_id}"


class WeaponPage(EditorPage):
    """一把武器（含「这个单位在哪几个炮塔上装它、能装哪些弹」）。"""

    def __init__(self, weapon_id: int, parent: QWidget | None = None) -> None:
        super().__init__("武器", f"武器模板 #{weapon_id} · 40 列", parent)
        self._weapon_id = weapon_id
        #: 右栏现在看的是哪一行：Weapons（武器自己）/ TurretWeapons / Ammunitions。
        self._focus_table = TABLE
        self._focus_row = weapon_id
        #: 图标源。没注入过（_icons_ready 为假）= 惰性取全局那一份。
        self._icons: IconCache | None = None
        self._icons_ready = False
        self._connected_cache: object | None = None
        #: 等图的那几个标签：(标签, 族, 键, 盒子)。
        self._icon_labels: list[tuple[QLabel, str, str, QSize]] = []

    # ------------------------------------------------------------------ #
    # 图标
    # ------------------------------------------------------------------ #

    def set_icons(self, icons: IconCache | None) -> None:
        """接上图标来源。

        **None = 明确不要图标**（剪影退回文字），不是「用默认那一份」——
        没装游戏 / 没缓存的机器上「取不到图」是常态，而这一页**绝不留空白**。
        """
        self._icons = icons
        self._icons_ready = True
        self._connect_icons(icons)
        self.refresh()  # 重画左栏（refresh 自己会在还没接上 context 时什么都不做）

    def _icon_cache(self) -> IconCache | None:
        """没注入过就用全局那一份（与主编辑页同一个 IconCache，进程内只有一份）。"""
        if not self._icons_ready:
            self._icons_ready = True
            self._icons = shared_icon_cache()
            self._connect_icons(self._icons)
        return self._icons

    def _connect_icons(self, icons: object | None) -> None:
        """图是**异步**到的（要从游戏包里取）：到了补一次，绝不为了等图卡住界面。

        换来换去时旧连接不摘 —— 槽里每次都读**当前**这一个 cache，
        所以旧 cache 的 loaded 只会白跑一次，不会画错图。
        """
        loaded = getattr(icons, "loaded", None)
        if loaded is None or icons is self._connected_cache:
            return
        self._connected_cache = icons
        loaded.connect(self._apply_icons)

    def _apply_icons(self) -> None:
        """只补还没有图的那几个（已经有图的别重画）。"""
        for label, family, key, box in list(self._icon_labels):
            pixmap = label.pixmap()
            if pixmap is not None and not pixmap.isNull():
                continue
            self._apply_icon(label, family, key, box)

    def _apply_icon(self, label: QLabel, family: str, key: str, box: QSize) -> bool:
        """把图贴上去：**按族翻 + DPR 缩放**，统一走 prepare_icon。

        武器剪影那一族原图**朝左**、实机画的是朝右，翻不翻的口径只有一份
        （core/assets/families.py 的 IconFamily.mirrored）；自己 scaled+翻转的那个版本
        已经删掉 —— 抄一份就迟早会漏一处（用户 2026-10-01：「还是老问题」）。
        """
        if not key:
            return False
        icons = self._icon_cache()
        if icons is None:
            return False
        pixmap = icons.pixmap(family, key)
        if pixmap is None:
            return False
        label.setPixmap(prepare_icon(pixmap, family, box, label, key))
        label.setText("")  # 图到了，回落文字让位
        return True

    def _silhouette(self, key: str) -> QLabel:
        """武器剪影。**取不到图就写文字（那个图标键），绝不留空白。**"""
        art = QLabel()
        art.setFixedSize(HERO_BOX)
        art.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        art.setProperty("role", "muted")
        if not key:
            art.setText("（这一把没有剪影素材）")
            return art
        art.setText(key)
        art.setToolTip(f"{WEAPON_ICON_FAMILY} · {key}")
        self._icon_labels.append((art, WEAPON_ICON_FAMILY, key, HERO_BOX))
        self._apply_icon(art, WEAPON_ICON_FAMILY, key, HERO_BOX)
        return art

    def _ammo_art(self, key: str) -> QLabel | None:
        """弹种前面那颗小图；**没有键就不占位**（旁边的弹种名还在，不是空白）。"""
        if not key:
            return None
        art = QLabel()
        art.setFixedSize(AMMO_ICON_BOX)
        art.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        art.setProperty("role", "entry-place")
        art.setText(key)
        self._icon_labels.append((art, AMMO_ICON_FAMILY, key, AMMO_ICON_BOX))
        self._apply_icon(art, AMMO_ICON_FAMILY, key, AMMO_ICON_BOX)
        return art

    # ------------------------------------------------------------------ #
    # 数据（全部现取：改完一行之后不能再读缓存）
    # ------------------------------------------------------------------ #

    def set_context(self, context: EditorContext) -> None:
        self._focus_table = TABLE
        self._focus_row = self._weapon_id
        super().set_context(context)

    def mounts(self) -> tuple[Mount, ...]:
        """本单位的哪几个炮塔装了它 —— `TurretUnits` -> `TurretWeapons`。

        行来自 `context.row_ids`（core/edit/scope.py 的「随行」那一段），
        再按本单位的炮塔 Id 对一遍 —— 界面不自己算「哪些行属于这个单位」。
        """
        context = self.context
        if context is None:
            return ()
        turrets = self._unit_turrets()
        out: list[Mount] = []
        for row_id in context.row_ids(MOUNT_TABLE):
            turret = self._number(MOUNT_TABLE, row_id, "TurretId")
            if turret not in turrets:
                continue
            if self._number(MOUNT_TABLE, row_id, "WeaponId") != self._weapon_id:
                continue
            out.append(
                Mount(
                    turret_id=turret,
                    turret_name=turrets[turret],
                    row_id=row_id,
                    order=self._number(MOUNT_TABLE, row_id, "Order"),
                    channel=self._number(MOUNT_TABLE, row_id, "WeaponChannel"),
                    priority=self._number(MOUNT_TABLE, row_id, "WeaponPriority"),
                )
            )
        return tuple(sorted(out, key=lambda mount: (mount.turret_id, mount.order, mount.row_id)))

    def ammo_lines(self) -> tuple[AmmoLine, ...]:
        """这个单位给这门武器配的弹：**一行一条携弹记录** + 它的 `Quantity`。"""
        context = self.context
        if context is None:
            return ()
        out: list[AmmoLine] = []
        for row_id in self._link_rows():
            ammo_id = self._number(LINK_TABLE, row_id, "AmmunitionId")
            quantity_cell = context.cell(CellRef(LINK_TABLE, row_id, "Quantity"))
            quantity = quantity_cell.raw if quantity_cell is not None else None
            out.append(
                AmmoLine(
                    row_id=row_id,
                    ammo_id=ammo_id,
                    label=self._row_name(AMMO_TABLE, ammo_id, f"弹药 #{ammo_id}"),
                    icon=self._text(AMMO_TABLE, ammo_id, "HUDIcon"),
                    quantity=quantity if isinstance(quantity, int) else None,
                    order=self._number(LINK_TABLE, row_id, "Order"),
                )
            )
        return tuple(sorted(out, key=lambda line: (line.order, line.row_id)))

    @property
    def focus_table(self) -> str:
        """右栏现在看的是哪张表的哪一行（测试与「我在哪」的判据）。"""
        return self._focus_table

    @property
    def focus_row(self) -> int:
        return self._focus_row

    @property
    def left_column(self) -> QWidget | None:
        """左栏那个控件（测试用：验左栏到底画了什么）。"""
        return self._left

    # ------------------------------------------------------------------ #
    # 换右栏（页内导航）
    # ------------------------------------------------------------------ #

    def _show_weapon(self) -> None:
        self._focus(TABLE, self._weapon_id)

    def _show_mount(self, row_id: int) -> None:
        self._focus(MOUNT_TABLE, row_id)

    def _show_ammo(self, ammo_id: int) -> None:
        self._focus(AMMO_TABLE, ammo_id)

    def _focus(self, table: str, row_id: int) -> None:
        """换右栏（并重画左栏的高亮）。**不改数据，所以不发 changed。**"""
        if (table, row_id) == (self._focus_table, self._focus_row):
            return
        self._focus_table = table
        self._focus_row = row_id
        self._rebuild()
        self._scroll_to_focus()

    def _scroll_to_focus(self) -> None:
        """换过去之后把那一段滚进视野（不然用户看到的还是上一屏）。"""
        if self._entries is None or self._focus_table == TABLE:
            return
        self._entries.scroll_to(CellRef(self._focus_table, self._focus_row, "Id"))

    def _edit_cell(self, ref: CellRef) -> None:
        """左栏那一格点一下：就地改。

        ⚠️ 先把右栏换到「含这一行」的视图 —— `RowEntriesPanel.open_editor` 在那一行
        不在列表里时会静默什么都不显示（浮层建了却没处挂，看着像坏了）。
        """
        if ref.table == LINK_TABLE:
            self._show_ammo(self._number(LINK_TABLE, ref.row_id, "AmmunitionId"))
        elif ref.table == MOUNT_TABLE:
            self._show_mount(ref.row_id)
        elif ref.table == AMMO_TABLE:
            self._show_ammo(ref.row_id)
        elif ref.table == TABLE:
            self._show_weapon()
        context = self.context
        cell = context.cell(ref) if context is not None else None
        if cell is not None:
            self._open_inline_editor(cell)

    # ------------------------------------------------------------------ #
    # 左栏
    # ------------------------------------------------------------------ #

    def build_left(self) -> QWidget | None:
        context = self.context
        assert context is not None
        self._icon_labels = []
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.sm)
        box.addWidget(self._head(context))
        box.addWidget(self._mounts_section(context))
        box.addWidget(self._ammo_section(context))
        box.addWidget(self._note())
        box.addStretch(1)
        return host

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setProperty("role", "editor-row")
        box = QVBoxLayout(frame)
        box.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        box.setSpacing(TOKENS.spacing.xs)
        return frame, box

    def _head(self, context: EditorContext) -> QWidget:
        """左栏顶上那张卡：看武器时是武器自己，点了挂载点 / 弹种就换成那一行。"""
        if self._focus_table == MOUNT_TABLE:
            return self._mount_head()
        if self._focus_table == AMMO_TABLE:
            return self._ammo_head(context)
        return self._weapon_head(context)

    def _weapon_head(self, context: EditorContext) -> QWidget:
        frame, box = self._card()
        box.addWidget(self._silhouette(self._text(TABLE, self._weapon_id, "HUDIcon")))
        line = QHBoxLayout()
        line.setSpacing(TOKENS.spacing.sm)
        name = context.cell(CellRef(TABLE, self._weapon_id, "HUDName"))
        title = QLabel(name.text if name is not None else f"武器 #{self._weapon_id}")
        title.setProperty("role", "entries-title")
        line.addWidget(title)
        line.addStretch(1)
        columns = len(context.cells_of_row(TABLE, self._weapon_id))
        place = QLabel(f"武器模板 #{self._weapon_id} · {columns} 列")
        place.setProperty("role", "entry-place")
        line.addWidget(place)
        box.addLayout(line)

        return frame

    def _mount_head(self) -> QWidget:
        """选中的那条挂载记录：通道 / 优先级 / 顺序都能就地改（真值就在这儿）。"""
        frame, box = self._card()
        mount = self._mount_by_row(self._focus_row)
        title = QLabel(mount.turret_name if mount is not None else f"挂载行 #{self._focus_row}")
        title.setProperty("role", "entries-title")
        box.addWidget(title)
        subtitle = QLabel(mount.headline if mount is not None else MOUNT_TABLE)
        subtitle.setProperty("role", "entries-subtitle")
        box.addWidget(subtitle)
        if mount is not None:
            for column, text in (
                ("WeaponChannel", f"武器通道：{mount.channel}"),
                ("WeaponPriority", f"武器优先级：{mount.priority}"),
                ("Order", f"顺序：{mount.order}"),
            ):
                box.addWidget(self._edit_button(CellRef(MOUNT_TABLE, mount.row_id, column), text))
        note = QLabel(
            f"{TABLE}.WeaponChannel / WeaponPriority 全库都是 0（占位）——"
            f"载具真正读的是上面这两个值（{MOUNT_TABLE}）。"
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addWidget(self._back_button())
        return frame

    def _ammo_head(self, context: EditorContext) -> QWidget:
        """选中的弹种：本单位的携弹量（在 WeaponAmmunitions 上）点一下就改。"""
        frame, box = self._card()
        line = self._ammo_by_id(self._focus_row)
        title = QLabel(line.label if line is not None else f"弹药 #{self._focus_row}")
        title.setProperty("role", "entries-title")
        box.addWidget(title)
        subtitle = QLabel(f"弹药模板 #{self._focus_row}")
        subtitle.setProperty("role", "entries-subtitle")
        box.addWidget(subtitle)
        if line is not None:
            box.addWidget(
                self._edit_button(
                    line.quantity_ref,
                    f"本单位的携弹量：{line.quantity_text}（{LINK_TABLE}#{line.row_id}.Quantity）",
                )
            )
        note = QLabel(f"携弹量在 {LINK_TABLE}.Quantity，不在弹药行上。")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addWidget(self._back_button())
        return frame

    def _back_button(self) -> QPushButton:
        button = QPushButton("← 回到武器（40 列）")
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty("variant", "ghost")
        button.setToolTip("右栏换回这一把武器的 40 列")
        button.clicked.connect(lambda _=False: self._show_weapon())
        return button

    def _edit_button(self, ref: CellRef, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", "entry-value")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"{ref.table}#{ref.row_id} · {ref.column}（点它就地改）")
        button.clicked.connect(lambda _=False, r=ref: self._edit_cell(r))
        return button

    def _mounts_section(self, context: EditorContext) -> QWidget:
        frame, box = self._card()
        head = QLabel("装在哪")
        head.setProperty("role", "detail-section")
        box.addWidget(head)
        mounts = self.mounts()
        if not mounts:
            hint = QLabel(
                "这个单位没有把它装在炮塔上。\n"
                "（步兵武器走 SquadWeapons —— 班用武器不在这里；也可以从卡片上的武器行进。）"
            )
            hint.setProperty("role", "muted")
            hint.setWordWrap(True)
            box.addWidget(hint)
            return frame
        for mount in mounts:
            row = QHBoxLayout()
            row.setSpacing(TOKENS.spacing.sm)
            button = QPushButton(mount.turret_name)
            button.setProperty("role", "entry-value")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(
                f"{MOUNT_TABLE}#{mount.row_id}（{mount.headline}）——"
                "点它：右栏换成这条挂载记录，武器通道 / 优先级在那儿改"
            )
            button.clicked.connect(lambda _=False, r=mount.row_id: self._show_mount(r))
            row.addWidget(button)
            row.addStretch(1)
            detail = QLabel(mount.detail)
            detail.setProperty("role", "entry-place")
            row.addWidget(detail)
            box.addLayout(row)
        return frame

    def _ammo_section(self, context: EditorContext) -> QWidget:
        frame, box = self._card()
        head = QLabel("这个单位能装的弹（点一行进弹药页）")
        head.setProperty("role", "detail-section")
        box.addWidget(head)
        lines = self.ammo_lines()
        if not lines:
            hint = QLabel(
                f"这个单位没给它配弹（{LINK_TABLE} 里没有 WeaponId = {self._weapon_id} 的行）。"
            )
            hint.setProperty("role", "muted")
            hint.setWordWrap(True)
            box.addWidget(hint)
            return frame
        for line in lines:
            box.addWidget(self._ammo_row(line))
        return frame

    def _ammo_row(self, line: AmmoLine) -> QWidget:
        """一行一条携弹记录：弹种名（点它进弹药页）+ ×携弹量（点它就地改）。"""
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(TOKENS.spacing.sm)
        art = self._ammo_art(line.icon)
        if art is not None:
            row.addWidget(art)
        focused = self._focus_table == AMMO_TABLE and self._focus_row == line.ammo_id
        name = QPushButton(f"{'▸ ' if focused else ''}{line.label}")
        name.setProperty("role", "entry-value")
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.setToolTip(f"点它：右栏换成弹药模板 #{line.ammo_id}（67 列）")
        name.clicked.connect(lambda _=False, a=line.ammo_id: self._show_ammo(a))
        row.addWidget(name, 1)

        count = QPushButton(line.quantity_text)
        count.setProperty("role", "entry-value")
        count.setCursor(Qt.CursorShape.PointingHandCursor)
        count.setToolTip(
            f"携弹量 = {LINK_TABLE}#{line.row_id}.Quantity（点它就地改）。\n"
            "卡片上那个数是 Σ携弹量 × Ammunitions.HUDMultiplier，与这里不是一回事。"
        )
        count.clicked.connect(lambda _=False, r=line.quantity_ref: self._edit_cell(r))
        row.addWidget(count)
        outer.addLayout(row)

        place = QLabel(line.place)
        place.setProperty("role", "entry-place")
        outer.addWidget(place)
        return host

    def _note(self) -> QWidget:
        groups = " → ".join(title for title, _columns in FIELD_GROUPS)
        note = QLabel(
            f"右栏 40 列按功能分组显示：{groups}。\n"
            f"{TABLE}.WeaponChannel / WeaponPriority 是全表同值的占位列 ——"
            "真值在「装在哪」那条挂载行里。\n"
            f"携弹量在 {LINK_TABLE}.Quantity，不在弹药行上。"
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        return note

    # ------------------------------------------------------------------ #
    # 取数小工具（都走 context.cell，现取）
    # ------------------------------------------------------------------ #

    def _unit_turrets(self) -> dict[int, str]:
        """本单位的炮塔：`TurretUnits.TurretId` -> `Turrets.Name`。"""
        context = self.context
        out: dict[int, str] = {}
        if context is None:
            return out
        for row_id in context.row_ids(TURRET_UNIT_TABLE):
            turret_id = self._number(TURRET_UNIT_TABLE, row_id, "TurretId")
            if turret_id <= 0:
                continue
            out.setdefault(turret_id, self._row_name(TURRET_TABLE, turret_id, f"炮塔 #{turret_id}"))
        return out

    def _link_rows(self) -> tuple[int, ...]:
        """本单位这门武器的携弹记录行（`WeaponAmmunitions` 里 WeaponId = 这一把的）。"""
        context = self.context
        if context is None:
            return ()
        return tuple(
            row_id
            for row_id in context.row_ids(LINK_TABLE)
            if self._number(LINK_TABLE, row_id, "WeaponId") == self._weapon_id
        )

    def _mount_by_row(self, row_id: int) -> Mount | None:
        return next((mount for mount in self.mounts() if mount.row_id == row_id), None)

    def _ammo_by_id(self, ammo_id: int) -> AmmoLine | None:
        return next((line for line in self.ammo_lines() if line.ammo_id == ammo_id), None)

    def _number(self, table: str, row_id: int, column: str) -> int:
        context = self.context
        cell = context.cell(CellRef(table, row_id, column)) if context is not None else None
        value = cell.raw if cell is not None else None
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def _text(self, table: str, row_id: int, column: str) -> str:
        context = self.context
        cell = context.cell(CellRef(table, row_id, column)) if context is not None else None
        value = cell.raw if cell is not None else None
        return value if isinstance(value, str) else ""

    def _row_name(self, table: str, row_id: int, fallback: str) -> str:
        """那一行显示成什么（名字取不到就退回「表 #Id」，不编假名字）。"""
        context = self.context
        if context is None or row_id <= 0:
            return fallback
        for column in ("HUDName", "Name", "UIName"):
            cell = context.cell(CellRef(table, row_id, column))
            if cell is not None and cell.text:
                return cell.text
        return fallback

    # ------------------------------------------------------------------ #
    # 右栏
    # ------------------------------------------------------------------ #

    def focus_row_ref(self):
        """右栏现在这一行 —— 通用词条面板表头上那两把操作（换/克隆）按它算。

        专用页有自己的焦点（_focus_table/_focus_row），不像主编辑页那样用 _focus_ref。
        """
        from ba_mod_maker.core.edit.scope import RowRef

        if not self._focus_table or not self._focus_row:
            return None
        return RowRef(self._focus_table, self._focus_row)

    def entries_cells(self) -> tuple[FieldCell, ...]:
        """右栏要列的格子。

        - 武器（默认）：40 列，**按功能分组重排**；
        - 挂载点：那条 `TurretWeapons` 的 6 列；
        - 弹种：弹药 67 列 **+ 那条 WeaponAmmunitions 的 6 列**（携弹量在那儿）。
        """
        context = self.context
        if context is None or context.cell(CellRef(TABLE, self._weapon_id, "Id")) is None:
            return ()
        if self._focus_table == MOUNT_TABLE:
            return context.cells_of_row(MOUNT_TABLE, self._focus_row)
        if self._focus_table == AMMO_TABLE:
            cells = list(context.cells_of_row(AMMO_TABLE, self._focus_row))
            line = self._ammo_by_id(self._focus_row)
            if line is not None:
                cells.extend(context.cells_of_row(LINK_TABLE, line.row_id))
            return tuple(cells)
        return order_columns(context.cells_of_row(TABLE, self._weapon_id))

    def entries_order(self) -> tuple[str, ...]:
        if self._focus_table == MOUNT_TABLE:
            return (MOUNT_TABLE,)
        if self._focus_table == AMMO_TABLE:
            return (AMMO_TABLE, LINK_TABLE)
        return (TABLE,)

    def entries_title(self) -> str:
        """右栏的抬头 —— 设计图 B ④⑤ 写的就是「XX 模板的全部词条」。"""
        if self._focus_table == MOUNT_TABLE:
            return "炮塔挂载的全部词条"
        if self._focus_table == AMMO_TABLE:
            return "弹药模板的全部词条"
        return "武器模板的全部词条"

    def entries_summary(self) -> str:
        cells = self.entries_cells()
        if not cells:
            return f"{TABLE}#{self._weapon_id} · 还没有这一行"
        if self._focus_table == MOUNT_TABLE:
            return f"炮塔挂载#{self._focus_row} · {len(cells)} 列（武器通道 / 优先级真值）"
        if self._focus_table == AMMO_TABLE:
            line = self._ammo_by_id(self._focus_row)
            extra = f" + 武器携弹#{line.row_id}" if line is not None else ""
            return f"弹药模板#{self._focus_row} · 67 列{extra}（携弹量在那一行的 Quantity）"
        return f"武器模板#{self._weapon_id} · {len(cells)} 列 · 按功能分组"


__all__ = [
    "AMMO_ICON_BOX",
    "AMMO_ICON_FAMILY",
    "AMMO_TABLE",
    "FIELD_GROUPS",
    "HERO_BOX",
    "LINK_TABLE",
    "MOUNT_TABLE",
    "TABLE",
    "TURRET_TABLE",
    "TURRET_UNIT_TABLE",
    "WEAPON_ICON_FAMILY",
    "AmmoLine",
    "Mount",
    "WeaponPage",
    "order_columns",
]
