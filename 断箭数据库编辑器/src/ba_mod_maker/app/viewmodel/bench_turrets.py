"""编成台 ①「**本单位的**炮塔 → 武器 → 弹药」的取数与规则 —— **零 Qt**
（`tests/test_core_layering.py` 用 AST 扫：`app/viewmodel/` 里一个 Qt 符号都不许有）。

出处：`docs/交接-2026-10-01-单位编成台设计.md` §3 口径 1 / 4 / 7 ＋ 设计图
`docs/设计稿-编成台-载具.png` 的 ① 那一块。那份设计稿的逐字来源是
`_work/design/gen_bench_vehicle.py:101-125`（我读了它，别再造一遍）：

    炮塔行 = Turret{槽} │ 炮塔名 │ Turrets.ModelFileName 的 basename │ N 件武器 · M 条弹 │ ✕
    武器行 = 武器图标   │ 武器名 │ N 条弹药                          │ ✕
    弹药行 = 弹种图标   │ 弹种名 │ ×数量                             │ ✕

四句话说完这一块：

1. **四层层级**：当前选中改装 → 炮塔 → 武器 → 弹药。⚠️ **炮塔那一层画的是"本单位可能
   装的全部炮塔"**（名册 = `TurretUnits` 候选名单 ∪ 选项的 21 个槽，
   `unit_card.roster_turret_chain`）—— 用户 2026-10-02：「还是把所有的炮塔都列出来吧，
   我毕竟要增删炮塔的」。只画"当前装着的"那几座时实测单位 21 只剩 1 座，另外 4 座
   在界面上根本够不着，也就谈不上增删（卡上那一面仍是"当前装着的"，那是游戏
   `ApplyCurrentMods` 之后的样子，⛔ 一个字节都不许改）。后三层分别是 `TurretUnits` /
   `TurretWeapons` / `WeaponAmmunitions` 三张**关联行**（✕ 删的就是它们），
   名字 / 图标来自 `Turrets` / `Weapons` / `Ammunitions` 三张**模板表**
   （⛔ 模板行一个字节都不动 —— 口径 1「只有 ＋ 和 ✕，没有换」＋口径 7）；
   ⚠️ 口径 1 **2026-10-02 用户复述确认**：「换」本质上就是删除再新增，单独做一颗按钮非常多余 ——
   要换 = ✕ 删 + ＋ 增，或者在中栏点一行、到**右栏直接改那一列**（外键那格本来就是可挑行的）；
   「改顺序」同理 = 在右栏直接改 Order 那一格。① 这一块的行尾只有 ＋ 与 ✕。
2. **树本身不另写一套**：走 `unit_card.roster_turret_chain`（与卡上那条链共用装配代码，
   含"选项的 21 个槽覆盖 TurretUnits 缺省名单"那条口径）。这里只补三样卡上不带、
   而"删一行"必须有 / 设计图上要画的东西：**行 Id**（`unit_row_id` /
   `link_row_id` / `AmmoLine.row_id`）、**图标键**（`Weapons.HUDIcon` /
   `Ammunitions.HUDIcon`）、**模型文件名**（设计图里那串灰字）；
3. **只有 ＋ 和 ✕**（＋ 加一座炮塔 / 挂武器 / 加弹种；✕ 删关联行）。规则是**纯函数**
   （`plan_add_*` / `plan_remove_turret` / `plan_quantity`）—— 算好"要写哪几格" + 一句人话；
   "改"（顺序与换）不在这里做：中栏点一行 → 右栏列出**那一行的全部列** → 直接改那一格
   （`Order` / `TurretId` / `WeaponId` / `AmmunitionId` 都是普通列）；写库一律经
   `EditorContext`（set_value / insert_row / delete_row）；
4. **新行 Id 一律 `core/edit/clone.py::next_row_id`** —— 别写"最大 + 1"：Id 有大空洞，
   而且"删掉最大那一行"之后会捡回刚删的 Id（两行抢一个 Id，见那个函数的注释）。

三条**共享**事实（删之前必须知道，界面照它说人话）：

- `TurretUnits` / `WeaponAmmunitions` **带 UnitId** —— 行是"本单位那一份"，删它只动本单位；
- `TurretWeapons` **不带 UnitId**（6 列：Id / WeaponId / TurretId / Order / WeaponChannel /
  WeaponPriority），它挂在**炮塔**上：实测 174/1055 座炮塔被不止一个单位引用（最多 83 个，见
  `core/edit/clone.py::SHARED_TEMPLATE_TABLES` 上面那段），所以删一条 TurretWeapons 会
  一起改到共用那座炮塔的别的车 —— `turret_users()` 就是给界面说这句用的；
- 当前选项的 21 个槽（`Options.Turret0Id..Turret20Id`）**压过** TurretUnits 的缺省名单
  （`CardData.card_turrets` 的顺序就是"先缺省、再按槽覆盖"），所以删掉一行 TurretUnits
  **未必**让那座炮塔从界面上消失 —— `option_slot_columns()` 给界面算出"还指着它的槽"。

❗ 本模块不得 import 任何 Qt 符号（`tests/test_core_layering.py` 用 AST 扫）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

# ❗ 选项的**短名**口径只有一份（`bench_slots.slot_option_labels`：先掐槽名前缀、再退
#    `unit_card.short_names`）—— 抬头那句「改装「ZU23」…」必须与 ② 那一块逐字一致，
#    自己再实现一遍就会出现"① 写 ZU-23-2、② 写 ZU23"那种同页不同名。
#    同一份里还有 `slot_owner_unit`（这个槽挂在哪个单位上）—— 借来的槽一个字节都不许写。
from ba_mod_maker.app.viewmodel.bench_slots import slot_option_labels, slot_owner_unit
from ba_mod_maker.app.viewmodel.unit_card import (
    TURRET_SLOTS,
    CardData,
    _walk_turret_weapons,
    option_turret_slots,
    roster_turret_chain,
    row_name,
    short_names,
    slot_range_text,
)
from ba_mod_maker.core.assets.families import (
    AMMO_ICON_FAMILY,
    WEAPON_ICON_FAMILY,
    row_icon_source,
)
from ba_mod_maker.core.edit.changeset import ID_COLUMN, Changeset
from ba_mod_maker.core.edit.clone import next_row_id
from ba_mod_maker.core.edit.refs import CellRef

# ---- 表名与列名：字面量只在这一个文件里出现一次 ----------------------------- #

#: 关联表（✕ 删这一行 = 删"本单位那一份"）
TABLE_TURRET_UNITS = "TurretUnits"
TABLE_TURRET_WEAPONS = "TurretWeapons"
TABLE_WEAPON_AMMO = "WeaponAmmunitions"
#: 模板表（只读不改；名字 / 图标 / 换弹种都在这儿）
TABLE_TURRETS = "Turrets"
TABLE_WEAPONS = "Weapons"
TABLE_AMMO = "Ammunitions"
#: 改装槽与选项（当前选中哪一项 = 这一块的"当前改装"）
TABLE_OPTIONS = "Options"
TABLE_MODIFICATIONS = "Modifications"

COLUMN_ID = ID_COLUMN
COLUMN_UNIT = "UnitId"
COLUMN_ORDER = "Order"
COLUMN_TURRET = "TurretId"
COLUMN_WEAPON = "WeaponId"
COLUMN_AMMO = "AmmunitionId"
COLUMN_QUANTITY = "Quantity"
#: 武器的两个挂载属性（`TurretWeapons` 上按炮塔覆盖；`Weapons` 模板行的同名列是占位列）。
COLUMN_CHANNEL = "WeaponChannel"
COLUMN_PRIORITY = "WeaponPriority"
#: 设计图上炮塔行中间那串灰字（`Turrets.ModelFileName` 的 basename）。
COLUMN_MODEL = "ModelFileName"
#: 父炮塔那一列（`Turrets` 的**自引用**外键）。❗ 实测 1055 行里 **965 行是 null**（不是 0）——
#: 判空认 None（`Rows.i()` 对 None 给 default 0，走它就安全）。
COLUMN_PARENT_TURRET = "ParentTurretId"

#: 图标 = (族名, 键列)。**唯一出处**是 `core/assets/families.py::ROW_ICON_SOURCES`
#: （界面不许自己抄族名 / 列名：抄错一个字母的症状是"这一格永远没图标"，还不报错）。
#: 末尾那个 or 只是兜底 —— 这两张表在 ROW_ICON_SOURCES 里都登记着（test_row_icon.py 守着）。
WEAPON_ICON_SOURCE: tuple[str, str] = row_icon_source(TABLE_WEAPONS) or (
    WEAPON_ICON_FAMILY,
    "HUDIcon",
)
AMMO_ICON_SOURCE: tuple[str, str] = row_icon_source(TABLE_AMMO) or (AMMO_ICON_FAMILY, "HUDIcon")
#: ⚠️ `Turrets` **没有**图标源（ROW_ICON_SOURCES 里没登记它）—— 炮塔行靠名字前那个 🗼
#: 认层，不画假图标（`row_icon_source("Turrets") is None`，写成常量给用例断言）。
TURRET_ICON_SOURCE: tuple[str, str] | None = row_icon_source(TABLE_TURRETS)

#: 读之前必须丢缓存的表（`Rows.where / by_id` 都缓存过筛结果）。
_READ_TABLES: tuple[str, ...] = (
    TABLE_TURRET_UNITS,
    TABLE_TURRET_WEAPONS,
    TABLE_WEAPON_AMMO,
    TABLE_TURRETS,
    TABLE_WEAPONS,
    TABLE_AMMO,
    TABLE_OPTIONS,
    # 2026-10-02 分组要读它（这座塔归哪个改装槽）—— 漏了它 where() 会拿缓存里的旧名单。
    TABLE_MODIFICATIONS,
)


def _fresh(data: CardData, tables: tuple[str, ...] = _READ_TABLES) -> None:
    """把这几张表的筛结果丢掉再读（与 `bench_loadout.loadout_lines` 同一条）。

    为什么界面这一层也要丢：刚插过 / 删过行的表，`where()` 给的是**旧名单**
    （`EditorContext` 的写路径也丢，但界面不许依赖"别人替我丢过了"）。
    """
    for name in tables:
        data.t(name).invalidate()


def _text(rows, row, column: str) -> str:
    """一格文本（行不在就给空串 —— 别让"换数据源版本"变成整块崩）。"""
    if row is None:
        return ""
    value = rows.s(row, column, "")
    return "" if value in (None, "None") else str(value).strip()


def _basename(path: str) -> str:
    """`RU_BTR_D_New/RU_BTR_D_Skrezhet` -> `RU_BTR_D_Skrezhet`（设计图那一格就是这么切的）。"""
    if not path:
        return ""
    return path.replace("\\", "/").rsplit("/", 1)[-1].strip()


def _one_line(text: str) -> str:
    """提示语压成一行（tooltip / 状态栏都只有一行）。"""
    return " ".join(str(text).split())


# --------------------------------------------------------------------------- #
# 取出来的那三样东西（一行 = 一个数据类，界面照着摆控件）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AmmoLine:
    """一条携弹行（`WeaponAmmunitions` 一行）—— 四层链最底下那一层。"""

    #: `WeaponAmmunitions.Id`（✕ 删的是它）
    row_id: int
    #: `Ammunitions.Id`（模板行：名字 / 图标 / HUDMultiplier 在它身上）
    ammo_id: int
    label: str
    icon_key: str
    #: `WeaponAmmunitions.Quantity` —— 设计图那个「×400」写的是它（**可改的那一格**）
    quantity: int
    #: 卡片上那个数 = Quantity × `Ammunitions.HUDMultiplier`（与 `ChainAmmo.count` 同口径）
    count: int
    #: 这门武器挂的是**哪个槽**上的弹（同一门枪挂在两座炮塔上时用它认归属）
    source_turret_id: int = 0

    @property
    def ref(self) -> CellRef:
        return CellRef(TABLE_WEAPON_AMMO, self.row_id, COLUMN_ID)

    @property
    def quantity_ref(self) -> CellRef:
        """携弹量那一格（改数量写它 —— 与 `bench_loadout.quantity_ref` 同一格）。"""
        return CellRef(TABLE_WEAPON_AMMO, self.row_id, COLUMN_QUANTITY)

    @property
    def value_text(self) -> str:
        """行尾那一格：设计图逐字是「×数量」。"""
        return f"×{self.quantity}"

    @property
    def detail_text(self) -> str:
        """只有与卡面数**不一样**时才多说一句（HUDMultiplier ≠ 1 的那种弹）。"""
        return f"（卡面 ×{self.count}）" if self.count != self.quantity else ""

    @property
    def note(self) -> str:
        return _one_line(
            f"{self.label}（Ammunitions #{self.ammo_id}）"
            f" · 本单位这一行 WeaponAmmunitions #{self.row_id}：携弹 ×{self.quantity}"
            + (f"（卡面 ×{self.count}）" if self.count != self.quantity else "")
        )


@dataclass(frozen=True, slots=True)
class WeaponLine:
    """四层链第三层：一门武器（`TurretWeapons` 一行 ＋ 它下面的弹）。"""

    #: `TurretWeapons.Id`（✕ 删的是它；0 = 这张表里没有这一行，那这一层只是"画着"）
    link_row_id: int
    #: `Weapons.Id`（模板行：名字 / 图标在它身上）
    weapon_id: int
    label: str
    icon_key: str
    ammo: tuple[AmmoLine, ...] = ()
    #: 这条关联行挂在**哪座炮塔**上（`_walk_turret_weapons` 会连子炮塔一起走，
    #: 所以它可能不等于界面上那一行的顶层炮塔 —— 删行 / 说人话都要按它来）
    source_turret_id: int = 0

    @property
    def ref(self) -> CellRef:
        """点这一行 -> 右栏画它的全部列：有关联行就给关联行，没有才退回模板行。"""
        if self.link_row_id > 0:
            return CellRef(TABLE_TURRET_WEAPONS, self.link_row_id, COLUMN_ID)
        return CellRef(TABLE_WEAPONS, self.weapon_id, COLUMN_ID)

    @property
    def value_text(self) -> str:
        """行尾那一格：设计图逐字是「N 条弹药」。"""
        return f"{len(self.ammo)} 条弹药"

    @property
    def note(self) -> str:
        where = (
            f"TurretWeapons #{self.link_row_id} → Turrets #{self.source_turret_id}"
            if self.link_row_id > 0
            else f"TurretWeapons 里没有这一行 · Turrets #{self.source_turret_id}"
        )
        return _one_line(
            f"🔫 {self.label}（Weapons #{self.weapon_id}）· {where} · {len(self.ammo)} 条弹药"
        )


@dataclass(frozen=True, slots=True)
class TurretLine:
    """四层链第二层：一座**当前装着**的炮塔（`ChainTurret` ＋ 删行要的行 Id）。"""

    turret_id: int
    #: `TurretUnits.Id`（✕ 删的是它；0 = 本单位没有这一行，是**选项的槽**把它填进来的）
    unit_row_id: int
    #: 界面上的名字（`unit_card.turret_label` 的短名 + 塔）
    label: str
    #: `Turrets.Name` 全名（tooltip 用）
    full_label: str
    #: 占了哪几个挂载槽，压缩成一段字（`slot_range_text`：「0」/「7–12 · 19」）。
    #: ❗ 来源是**当前选项里真正指着它的列**（`option_slot_columns`），不是 card_turrets
    #:    那个稀疏列表的序号 —— 否则"挪到槽 3"之后行上还写着 Turret0（自相矛盾）。
    slot_text: str
    #: `Turrets.ModelFileName` 的 basename（设计图里那串灰字；实测 954 是空串）
    model_name: str = ""
    weapons: tuple[WeaponLine, ...] = ()
    #: 父炮塔 Id（`Turrets.ParentTurretId`；0 = 顶层 / 父塔不在这一份名册里）
    parent_id: int = 0
    #: 父炮塔在界面上的短名（画「↳ 挂在 X 下」用；没有父塔时留空）
    parent_label: str = ""
    # ❗ 这里曾经有 home_slot / home_label / also_label 三个字段（"这一行归哪个槽"＋
    #    "也被谁用"那串灰字）。用户 2026-10-02 定：**既然每个用到它的槽下面都列了它，
    #    就不必再写「也被 X 用」**（"都已经显示出来了，为什么还要这种多余的文字呢"）——
    #    归属由"画在哪一组下面"本身表达，三个字段一起删掉。

    @property
    def depth(self) -> int:
        """这一行画在第几层（0 = 顶层炮塔，1 = 挂在父塔下面的子炮塔）。

        ❗ 与 `parent_id` **不是**同一件事：父塔不在名册里的那种子塔 `parent_id` 仍留在 0
        （见 `nest_turret_lines` 口径 1）——缩进档位读的是这个。
        """
        return 1 if self.parent_id else 0

    @property
    def ref(self) -> CellRef:
        if self.unit_row_id > 0:
            return CellRef(TABLE_TURRET_UNITS, self.unit_row_id, COLUMN_ID)
        return CellRef(TABLE_TURRETS, self.turret_id, COLUMN_ID)

    @property
    def weapon_count(self) -> int:
        return len(self.weapons)

    @property
    def ammo_count(self) -> int:
        return sum(len(weapon.ammo) for weapon in self.weapons)

    @property
    def value_text(self) -> str:
        """行尾那一格：设计图逐字是「N 件武器 · M 条弹」。"""
        return f"{self.weapon_count} 件武器 · {self.ammo_count} 条弹"

    @property
    def detail_text(self) -> str:
        """名字右边那串灰字：设计图是「Turret2」＋ 模型文件名（954 那种没有模型，只剩槽号）。

        ❗ 子炮塔（`Turrets.ParentTurretId` 指着名册里的另一座）前面**多一句
        「↳ 挂在 X 下」**—— 用户 2026-10-02：「把层级画出来吧」。
        """
        pin = f"Turret{self.slot_text}" if self.slot_text else "不在挂载槽上"
        body = f"{pin} · {self.model_name}" if self.model_name else pin
        if not self.parent_id:
            return body
        parent = self.parent_label or f"炮塔 #{self.parent_id}"
        return f"↳ 挂在「{parent}」下 · {body}"

    @property
    def note(self) -> str:
        tail = (
            f" · 本单位的挂载行 TurretUnits #{self.unit_row_id}"
            if self.unit_row_id > 0
            else " · 本单位在 TurretUnits 里没有这一行（槽是当前选项填的）"
        )
        return _one_line(
            f"🗼 {self.full_label}（Turrets #{self.turret_id}）"
            f" · 槽 {self.slot_text or '—'} · {self.value_text}{tail}"
        )


@dataclass(frozen=True, slots=True)
class SlotGroup:
    """③ 里**按改装槽分组**的那一层（用户 2026-10-02：「炮塔和改装槽，也要有树状关系」）。

    一组 = 一个改装槽 ＋ 它的选项挂上来的那几座塔（子炮塔跟着它的顶层塔同组）。
    `modification_id == 0` 那一组 = **不属于任何改装槽**（本车自带，或这个单位根本没有槽）。
    """

    #: `Modifications.Id`（0 = 不属于任何改装槽）
    modification_id: int = 0
    #: 槽名（与 ② 改装槽那一块的短名同一份口径）
    label: str = ""
    #: 抬头右边那串灰字（由哪几个选项挂上 / N 座塔）
    note: str = ""
    turrets: tuple[TurretLine, ...] = ()

    @property
    def turret_count(self) -> int:
        return len(self.turrets)

    @property
    def value_text(self) -> str:
        """抬头行尾那一格：`N 座塔 · M 件武器 · K 条弹`。"""
        weapons = sum(turret.weapon_count for turret in self.turrets)
        ammo = sum(turret.ammo_count for turret in self.turrets)
        return f"{self.turret_count} 座塔 · {weapons} 件武器 · {ammo} 条弹"


@dataclass(frozen=True, slots=True)
class TurretTree:
    """① 这一块画出来的**全部**内容：当前选项 ＋ 它下面那一座四层链。"""

    #: 当前选中的那一项（`Options.Id`；0 = 这个单位没有可用的改装选项）
    option_id: int = 0
    #: 那一项在界面上的短名（口径见 `bench_slots.slot_option_labels`）
    option_label: str = ""
    #: 那一项填了几个炮塔槽（抬头那句「填了 N 个炮塔槽」）
    slot_count: int = 0
    #: 这一项是不是**本单位的槽**里的一项（False = 变体借来的槽，一个字节都不许写）
    own_option: bool = True
    turrets: tuple[TurretLine, ...] = ()
    #: 按**改装槽**分好组的同一批炮塔（`turrets` 是它们的平铺版，给老调用方/用例用）。
    #: 用户 2026-10-02：「炮塔和改装槽，也要有树状关系」—— 界面按它画分组抬头。
    groups: tuple[SlotGroup, ...] = ()

    @property
    def weapon_count(self) -> int:
        return sum(turret.weapon_count for turret in self.turrets)

    @property
    def ammo_count(self) -> int:
        return sum(turret.ammo_count for turret in self.turrets)

    @property
    def has_rows(self) -> bool:
        return bool(self.turrets)


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #


def current_option(data: CardData, unit_id: int) -> tuple[int, str, bool, int]:
    """「当前改装」= 当前选中那一项选项：`(选项Id, 短名, 是不是本单位的槽, 填了几个槽)`。

    口径**只有一份**：`CardData.selected_options`（`IsDefault ?? Order == 0`，与卡片、
    ② 那一块、写盘那条路完全同一份 —— 别自己再算一次）。

    选哪一项（Lead 只钉了"当前选中选项"这几个字，这里定死并写明）：

    1. 先取本单位各槽的当前项，**第一个填了炮塔槽的**那一项 —— 抬头说「改装「X」填了
       N 个炮塔槽」说的就是"炮塔是这一项给的"，一个都没填的槽拿不到下一句。
       ❗「第一个」按 `Modifications.Order` 排（`CardData.modifications_of` 的顺序），
       不是按 Id —— 实测 F-15E（287）是 308(Order 0) → 307 → 306 → 305 → 543，
       所以落到 308（Turret19Id 一个槽）。
       ⚠️ 多槽单位（F-15E 那种 5 个改装槽）这里只说得清"其中一个槽"：① 画出来的**整条链**
       是 `CardData.card_turrets` 把所有当前项**合起来**算的（载具样本 206 只有一个槽，
       两者一致 —— 这一块的主场就是载具）。
    2. 都没填（新单位 / 还没配过）就退回**第一个**当前项 —— 至少让抬头说得出一句话；
    3. 一个当前项都没有（单位没有 Modification）→ `(0, "", True, 0)`，界面画空态。
    """
    unit_id = int(unit_id)
    rows = data.t(TABLE_OPTIONS)
    best: tuple[int, str, bool, int] | None = None
    for option in data.selected_options(unit_id):
        option_id = rows.i(option, COLUMN_ID)
        modification_id = rows.i(option, "ModificationId")
        own = slot_owner_unit(data, modification_id) == unit_id
        slots = count_filled_slots(data, option_id)
        label = _option_label(data, modification_id, option_id)
        if best is None:
            best = (option_id, label, own, slots)
        if slots > 0:
            return (option_id, label, own, slots)
    return best if best is not None else (0, "", True, 0)


def _option_label(data: CardData, modification_id: int, option_id: int) -> str:
    """选项在界面上的短名 —— 与 ② 那一块同一份口径（借来的槽也照样算）。"""
    labels = slot_option_labels(data, modification_id)
    name = (labels.get(int(option_id)) or "").strip()
    if name:
        return name
    rows = data.t(TABLE_OPTIONS)
    row = rows.by_id(option_id)
    return _text(rows, row, "UIName") or _text(rows, row, "Name") or f"#{option_id}"


def count_filled_slots(data: CardData, option_id: int) -> int:
    """这一项填了几个非零的 `TurretNId`（0..20 全扫，一个都不折叠 —— 用户口径）。"""
    rows = data.t(TABLE_OPTIONS)
    row = rows.by_id(option_id)
    if row is None:
        return 0
    return sum(1 for column in TURRET_SLOTS if rows.i(row, column) > 0)


def option_slot_ids(data: CardData, option_id: int) -> tuple[int, ...]:
    """这一项填了哪几个槽（升序）。"""
    rows = data.t(TABLE_OPTIONS)
    row = rows.by_id(option_id)
    if row is None:
        return ()
    return tuple(slot for slot, column in enumerate(TURRET_SLOTS) if rows.i(row, column) > 0)


def option_slot_columns(
    data: CardData, option_id: int, turret_id: int
) -> tuple[tuple[int, str], ...]:
    """这一项里**指着这座炮塔**的槽：`((槽号, 列名), ...)`（升序）。

    用途只有一个：✕ 删掉一座炮塔之后，界面上那一行是不是**还会被选项的槽顶回来**
    （`CardData.card_turrets` 的"先缺省、再按槽覆盖"）—— 会的话界面得把话说明白，
    并且**在同一个动作里**把那些槽腾掉（否则用户看到的是"点了 ✕ 没反应"）。
    """
    rows = data.t(TABLE_OPTIONS)
    row = rows.by_id(option_id)
    if row is None:
        return ()
    out: list[tuple[int, str]] = []
    for slot, column in enumerate(TURRET_SLOTS):
        if rows.i(row, column) == int(turret_id):
            out.append((slot, column))
    return tuple(out)


def turret_users(data: CardData, turret_id: int) -> tuple[int, ...]:
    """还有哪些单位的 `TurretUnits` 指着这座炮塔（升序、去重）。

    ❗ `TurretWeapons` **不带 UnitId**，一条武器关联行挂在炮塔上 —— 删它会一起改到
    共用这座炮塔的**每一台车**（实测最多 83 个单位共用一座）。界面拿这份名单说人话。
    """
    links = data.t(TABLE_TURRET_UNITS)
    units = {links.i(row, COLUMN_UNIT) for row in links.where(COLUMN_TURRET, int(turret_id))}
    return tuple(sorted(unit for unit in units if unit > 0))


def _turret_units_row(data: CardData, unit_id: int, turret_id: int) -> int:
    """本单位挂在 TurretUnits 里的那一行 Id（同一座炮塔挂了两行时取 `(Order, Id)` 第一条）。

    取不到给 0：那座炮塔是**选项的槽**填进来的（TurretUnits 缺省名单里没有它）——
    界面据此把 ✕ 收起来（没有那一行，就没有"本单位那一份"可删）。
    """
    links = data.t(TABLE_TURRET_UNITS)
    rows = sorted(
        links.where2(COLUMN_UNIT, int(unit_id), COLUMN_TURRET, int(turret_id)),
        key=lambda row: (links.i(row, COLUMN_ORDER), links.i(row, COLUMN_ID)),
    )
    return links.i(rows[0], COLUMN_ID) if rows else 0


def _turret_weapon_row(data: CardData, turret_id: int, weapon_id: int) -> int:
    """一条武器关联行的 Id：在**这座炮塔（含子炮塔）**的 TurretWeapons 行里找 `weapon_id`。

    ❗ 不能只查顶层炮塔：`unit_card._walk_turret_weapons` 会把子炮塔的武器也算进这座炮塔
    （Su-25T / Mi-8 那种挂架型单位就是这样），只查顶层会得到 0，于是那一行永远没有 ✕。

    走法与 `_weapon_link` 只有一份（下面那个）；这里只取行 Id，不管它挂在哪座炮塔上。
    """
    return _weapon_link(data, turret_id, weapon_id)[0]


def turret_parent_id(turret_rows: Any, turret_id: int) -> int:
    """这一座炮塔的父炮塔 Id（`Turrets.ParentTurretId`；null / 0 / 没那一行都给 0）。

    ❗ 那一列实测 **965 行是 null**（不是 0）—— 走 `Rows.i()`（它对 None 给 default 0），
    别自己 `row[index] == 0`（那样 null 会被当成「父塔是 #0」）。
    """
    row = turret_rows.by_id(int(turret_id))
    return turret_rows.i(row, COLUMN_PARENT_TURRET) if row is not None else 0


def nest_turret_lines(lines: tuple[TurretLine, ...], turret_rows: Any) -> tuple[TurretLine, ...]:
    """把名册排成**父子相邻**：父塔一行，紧跟它下面那几座子炮塔（深一层）。

    用户 2026-10-02：「**把层级画出来吧**」—— 数据侧就是 `Turrets.ParentTurretId`（自引用）。

    三条口径：

    1. **只认「父塔也在这一份名册里」的子塔**：名册只收 `TurretUnits` ∪ 选项的 21 个槽，
       父塔没被收进来时那一行留在**顶层**（缩进一档却看不见父行，比平铺更糊涂）；
    2. 子塔之间的相对顺序 = **名册原来的顺序**（`TurretUnits` 的 Order/Id）—— 不重排；
    3. 实测最深 1 层，但走的是递归（真出现两层也不会漏），并带 `seen` 防环形数据。
    """
    by_id = {line.turret_id: line for line in lines}
    children: dict[int, list[int]] = {}
    roots: list[int] = []
    for line in lines:
        parent = turret_parent_id(turret_rows, line.turret_id)
        if parent and parent in by_id:
            children.setdefault(parent, []).append(line.turret_id)
        else:
            roots.append(line.turret_id)

    out: list[TurretLine] = []

    def emit(turret_id: int, parent: TurretLine | None, seen: frozenset[int]) -> None:
        line = by_id[turret_id]
        if parent is not None:
            line = replace(line, parent_id=parent.turret_id, parent_label=parent.label)
        out.append(line)
        if turret_id in seen:
            return
        next_seen = seen | {turret_id}
        for child in children.get(turret_id, ()):
            if child not in next_seen:
                emit(child, line, next_seen)

    for root in roots:
        emit(root, None, frozenset())
    return tuple(out)


#: "不属于任何改装槽"那一组的抬头（本车自带 / 这个单位根本没有改装槽）
NO_SLOT_LABEL = "不属于任何改装槽"
#: `structure_key` 里分组抬头用的假表名（它不是任何一张表的行，只要稳定且不撞）
GROUP_KEY = "(改装槽分组)"


def slot_order(data: CardData, unit_id: int) -> tuple[int, ...]:
    """本单位的改装槽 Id，**按界面顺序**（`Order` 再 `Id` —— 与 ② 那一块同一条）。

    分组抬头要靠它定序；"借来的槽"也在这儿（槽属于别的单位时 `where(UnitId)` 查不到它，
    但那种槽的选项不会出现在本单位的选项里，所以分组天然只看本单位的槽）。
    """
    mods = data.t(TABLE_MODIFICATIONS)
    rows = list(mods.where(COLUMN_UNIT, int(unit_id)))
    rows.sort(key=lambda row: (mods.i(row, COLUMN_ORDER), mods.i(row, ID_COLUMN)))
    return tuple(mods.i(row, ID_COLUMN) for row in rows)


def slot_home_labels(data: CardData, unit_id: int) -> dict[int, str]:
    """本单位的槽名（**短名**）—— 与 `bench_slots.slot_titles` 同一份口径（掐掉共有的词）。"""
    mods = data.t(TABLE_MODIFICATIONS)
    labels = {
        mods.i(row, ID_COLUMN): mods.s(row, "Name", "")
        for row in mods.where(COLUMN_UNIT, int(unit_id))
    }
    short = short_names(labels)
    return {
        mid: (short.get(mid) or "").strip() or (raw or "").strip() for mid, raw in labels.items()
    }


def slot_homes(data: CardData, unit_id: int) -> dict[int, tuple[int, tuple[int, ...]]]:
    """每座炮塔**归哪个改装槽**（第一个引用它的槽）＋ 引用它的**全部**槽。

    口径 = `Options.Turret0Id…Turret20Id` 里非零的那些列（同一份关系，关系树
    `relation_tree._option_slot_children` 画的是"槽 N · 炮塔名"那几条）。一座塔被两个槽
    挂上是常态（实测单位 46：9 座里 **5 座**被不止一个选项挂上）—— 它只归**第一个**槽，
    用户 2026-10-02 定的画法：**每一个用到它的槽下面都列一行**（同一行 Id）。
    """
    unit_id = int(unit_id)
    options = data.t(TABLE_OPTIONS)
    home: dict[int, int] = {}
    owners: dict[int, list[int]] = {}
    for mid in slot_order(data, unit_id):
        for option in options.where("ModificationId", mid):
            for _slot_index, turret_id in option_turret_slots(data, options.i(option, ID_COLUMN)):
                home.setdefault(int(turret_id), mid)
                seen = owners.setdefault(int(turret_id), [])
                if mid not in seen:
                    seen.append(mid)
    return {turret_id: (home[turret_id], tuple(owners[turret_id])) for turret_id in home}


def _group_by_slot(
    data: CardData, unit_id: int, lines: tuple[TurretLine, ...]
) -> tuple[tuple[TurretLine, ...], tuple[SlotGroup, ...]]:
    """把名册按**改装槽**分组，并给每一行补上 `home_slot / home_label / also_label`。

    - 顶层塔归 `slot_homes` 给的那个槽；**子塔跟着父塔**那一组（子塔自己不另开一组）；
    - 槽名查不到的（借来的槽）算"不属于任何改装槽"；
    - 分组顺序 = 槽的界面顺序；"不属于任何改装槽"永远**排最后**；
    - 抬头那串灰字 = 这个槽里**哪几个选项**挂上了本组的塔（实测常是好几个：S8 / S13 / GSh23L）。
    """
    homes = slot_homes(data, unit_id)
    labels = slot_home_labels(data, unit_id)
    order = slot_order(data, unit_id)
    buckets: dict[int, list[TurretLine]] = {}
    out: list[TurretLine] = []
    current_owners: tuple[int, ...] = (0,)
    for line in lines:
        # ❗ 用户 2026-10-02：「**既然也被使用，就给使用的那个折叠栏里也加上啊**」——
        #    一座塔**出现在每一个用到它的槽下面**（多对多：实测单位 46 的 9 座里 5 座被不止一个
        #    槽用到）。行还是**同一行**（✕ 删的是同一份 TurretUnits 行），不再写「也被 X 用」
        #    那种灰字 —— 归属由"它画在哪一组下面"本身表达（用户当天补的第二句）。
        if line.parent_id:
            owners = current_owners  # 子塔跟着父塔
        else:
            owners = homes.get(line.turret_id, (0, ()))[1] or (0,)
            owners = tuple(mid for mid in owners if mid in labels) or (0,)
            current_owners = owners
        out.append(line)
        for mid in owners:
            buckets.setdefault(mid, []).append(line)

    options = data.t(TABLE_OPTIONS)
    groups: list[SlotGroup] = []
    for mid in (*order, 0):
        bucket = buckets.get(mid)
        if not bucket:
            continue
        if mid == 0:
            label = NO_SLOT_LABEL
            note = f"没有任何改装槽的选项挂它 · {len(bucket)} 座塔"
        else:
            label = labels.get(mid, f"改装槽 #{mid}")
            wanted = {line.turret_id for line in bucket}
            names: list[str] = []
            for option in options.where("ModificationId", mid):
                mounted = {
                    turret_id
                    for _slot, turret_id in option_turret_slots(data, options.i(option, ID_COLUMN))
                }
                if mounted & wanted:
                    labels_by_option = slot_option_labels(data, mid)
                    names.append(labels_by_option.get(options.i(option, ID_COLUMN), ""))
            who = " / ".join(name for name in names if name) or "（没有选项挂它）"
            note = f"由 {who} 挂上 · {len(bucket)} 座塔"
        groups.append(SlotGroup(modification_id=mid, label=label, note=note, turrets=tuple(bucket)))
    return tuple(out), tuple(groups)


def turret_tree(data: CardData, unit_id: int, *, selected: Any = None) -> TurretTree:
    """① 这一块的全部内容（**本单位可能装的全部炮塔**，每座一条四层链）。

    - 链走 `unit_card.roster_turret_chain`（与卡上那条链共用同一份装配代码，只差"取了哪些
      turret_id"：卡取"当前装着的"、这里取名册）；
    - 补的三样：TurretUnits 行 Id、TurretWeapons 行 Id、图标键与模型文件名；
    - 步兵：`card_turrets` 那条路给的是**负数编码的假炮塔**，card_turret_chain 直接给空
      —— 于是这里也是空（界面上画"这个单位没有炮塔"）。
    """
    unit_id = int(unit_id)
    _fresh(data)
    option_id, option_label, own, slot_count = current_option(data, unit_id)
    # ❗ 取**名册**那一份（本单位可能装的全部炮塔），不是卡上"当前装着的那几座" —— 用户
    #    2026-10-02：「还是把所有的炮塔都列出来吧，我毕竟要增删炮塔的」。
    chain = roster_turret_chain(data, unit_id, selected=selected)
    # ❗ 槽号的来源：**当前选项里真正指着这座炮塔的列**（option_slot_columns），不是
    #    ChainTurret.slots —— 后者是 card_turrets 那个稀疏列表的**序号**：在右栏把
    #    Options.Turret0Id 清空、改到 Turret3Id 之后，那个序号仍是 0，行上就写着 Turret0，
    #    与刚改的值对不上（Lead 2026-10-02 定：① 这一行必须写真实槽号）。
    #    ⛔ 卡那一面（unit_card.card_turret_chain / ChainTurret.slots）一个字节都不动。
    weapons_rows = data.t(TABLE_WEAPONS)
    ammo_rows = data.t(TABLE_AMMO)
    turret_rows = data.t(TABLE_TURRETS)
    out: list[TurretLine] = []
    for turret in chain:
        weapons: list[WeaponLine] = []
        for weapon in turret.weapons:
            template = weapons_rows.by_id(weapon.weapon_id)
            ammo: list[AmmoLine] = []
            for item in weapon.ammo:
                ammo_template = ammo_rows.by_id(item.ammo_id)
                ammo.append(
                    AmmoLine(
                        row_id=item.row_id,
                        ammo_id=item.ammo_id,
                        label=item.label,
                        icon_key=_text(ammo_rows, ammo_template, AMMO_ICON_SOURCE[1]),
                        quantity=item.quantity,
                        count=item.count,
                        source_turret_id=turret.turret_id,
                    )
                )
            weapons.append(
                WeaponLine(
                    link_row_id=_turret_weapon_row(data, turret.turret_id, weapon.weapon_id),
                    weapon_id=weapon.weapon_id,
                    label=weapon.label,
                    icon_key=_text(weapons_rows, template, WEAPON_ICON_SOURCE[1]),
                    ammo=tuple(ammo),
                    source_turret_id=turret.turret_id,
                )
            )
        columns = option_slot_columns(data, option_id, turret.turret_id) if option_id else ()
        out.append(
            TurretLine(
                turret_id=turret.turret_id,
                unit_row_id=_turret_units_row(data, unit_id, turret.turret_id),
                label=turret.label,
                full_label=turret.full_label,
                slot_text=slot_range_text([slot for slot, _column in columns]),
                model_name=_basename(
                    _text(turret_rows, turret_rows.by_id(turret.turret_id), COLUMN_MODEL)
                ),
                weapons=tuple(weapons),
            )
        )
    nested = nest_turret_lines(tuple(out), turret_rows)
    grouped, groups = _group_by_slot(data, unit_id, nested)
    return TurretTree(
        option_id=option_id,
        option_label=option_label,
        slot_count=slot_count,
        own_option=own,
        turrets=grouped,
        groups=groups,
    )


def structure_key(tree: TurretTree) -> tuple[tuple[str, int], ...]:
    """这一块**结构**的署名：画出来的每一行，是哪张表的哪一行。

    用途只有一个 —— **值变了就地换字、结构变了才重建**（用户口径，提过几回：
    「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」；先例
    `docs/交接-2026-09-30-界面批-图标树弹药.md:76/93` 与 `bench_loadout.structure_key`）。

    三层的身份都用**关联行**（有关联行时）；没有关联行的那种（选项的槽顶出来的炮塔 /
    TurretWeapons 里缺一行的武器）拿模板行当身份 —— 两张表名不同，不会撞。
    每组开头再插一条分组抬头（假表名 `GROUP_KEY`），抬头变了照样重建。
    """
    out: list[tuple[str, int]] = []
    # ❗ 按**分组**署名（不是按名册）：用户 2026-10-02 起一座塔会同时列在好几个槽下面
    #    （「既然也被使用，就给使用的那个折叠栏里也加上啊」）—— 某座塔从某一组挪进/挪出
    #    必须触发重建，否则它就留在旧的那一组下面了。
    for group in tree.groups:
        out.append((GROUP_KEY, group.modification_id))
        for turret in group.turrets:
            out.append((turret.ref.table, turret.ref.row_id))
            for weapon in turret.weapons:
                out.append((weapon.ref.table, weapon.ref.row_id))
                out.extend((TABLE_WEAPON_AMMO, ammo.row_id) for ammo in weapon.ammo)
    return tuple(out)


# --------------------------------------------------------------------------- #
# 规则：新挂上的武器**自动补最常用的那一条弹**（用户 2026-10-02）
# --------------------------------------------------------------------------- #


def common_ammo(data: CardData, weapon_id: int) -> tuple[int, int] | None:
    """这门武器在**全库**最常用的那一条弹：`(AmmunitionId, Quantity)`；一条都没有时 None。

    口径（用户 2026-10-02：「我希望我在加新炮塔时，**能自动为它的武器加上最常用的弹药**」）：
    在 `WeaponAmmunitions` 里数同一 `WeaponId` 的 `(弹, 数量)` 对，
    取出现次数最多的那一对；**并列时取弹 Id 小的**（同一个动作重跑两次必须给同一个答案 ——
    不然"点两下 ＋"会写出两种数据，撤销栈也解释不清）。

    实测：武器 #103（S-8）全库只有一种 (弹 #81, ×20)（16 行）；武器 #928（Kh-25M）
    只有 (弹 #733「Missile AGM Kh25ML Heli」, ×1) —— 大多数武器其实只有一种选择。
    """
    rows = data.t(TABLE_WEAPON_AMMO)
    counts: Counter[tuple[int, int]] = Counter()
    for row in rows.where(COLUMN_WEAPON, int(weapon_id)):
        counts[(rows.i(row, COLUMN_AMMO), rows.i(row, COLUMN_QUANTITY))] += 1
    if not counts:
        return None
    best = min(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    return best[0]


@dataclass(frozen=True, slots=True)
class AmmoFill:
    """＋ 加一座炮塔 / 挂一门武器之后**自动补**的那一条弹（口径见 common_ammo）。"""

    weapon_id: int = 0
    weapon_label: str = ""
    ammo_id: int = 0
    ammo_label: str = ""
    quantity: int = 1

    @property
    def text(self) -> str:
        """给人看的那半句（状态栏用，例如「S-8 → 80mm S-8 ×20」）。"""
        return f"{self.weapon_label} → {self.ammo_label} ×{self.quantity}"


def ammo_fill_for(data: CardData, unit_id: int, weapon_id: int) -> AmmoFill | None:
    """这一门武器**该自动补**的那一条弹；不该补时 None。

    不补的三种情形：① 本单位**已经有**这一门武器的弹药行了（不动用户已有的配置）；
    ② 这门武器在全库一条弹药行都没有（那种得人自己挑，界面照实说一句）；
    ③ 武器 Id 无效。
    """
    unit_id, weapon_id = int(unit_id), int(weapon_id)
    if weapon_id <= 0 or unit_id < 0:
        return None
    rows = data.t(TABLE_WEAPON_AMMO)
    if rows.where2(COLUMN_UNIT, unit_id, COLUMN_WEAPON, weapon_id):
        return None
    pair = common_ammo(data, weapon_id)
    if pair is None:
        return None
    ammo_id, quantity = pair
    return AmmoFill(
        weapon_id=weapon_id,
        weapon_label=row_name(data, TABLE_WEAPONS, weapon_id),
        ammo_id=ammo_id,
        ammo_label=row_name(data, TABLE_AMMO, ammo_id),
        quantity=quantity,
    )


def ammo_fills_for_turret(data: CardData, unit_id: int, turret_id: int) -> tuple[AmmoFill, ...]:
    """这一座炮塔（**连子炮塔**）上、本单位缺弹的每一门武器各补一条（顺序 = 武器 Id 序）。

    ⚠️ 「连子炮塔」与界面上那一棵树同一条走法（`unit_card._walk_turret_weapons`）——
    只查顶层炮塔会漏掉挂在它下面的子塔那几门武器。
    """
    out: list[AmmoFill] = []
    for weapon_id in sorted(set(_walk_turret_weapons(data, int(turret_id)))):
        fill = ammo_fill_for(data, unit_id, weapon_id)
        if fill is not None:
            out.append(fill)
    return tuple(out)


def weapons_without_any_ammo(data: CardData, turret_id: int) -> tuple[str, ...]:
    """这座炮塔（连子炮塔）上**全库都没有弹药行**的那几门武器（界面照实说一句用）。"""
    out: list[str] = []
    for weapon_id in sorted(set(_walk_turret_weapons(data, int(turret_id)))):
        if common_ammo(data, weapon_id) is None:
            out.append(row_name(data, TABLE_WEAPONS, weapon_id))
    return tuple(out)


# --------------------------------------------------------------------------- #
# 规则：＋ 挂一座炮塔 / ✕ 删一座炮塔之前要问的几句
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AddTurretPlan:
    """「＋ 加一座炮塔」挑完之后要写的那两处（纯函数：界面照着执行）。"""

    turret_id: int = 0
    #: 挂到**哪一项选项**的槽上（`Options.Id`）
    option_id: int = 0
    option_label: str = ""
    #: 挂到第几个槽（0..20）＋ 它叫什么列（`Turret3Id`）
    slot: int = -1
    column: str = ""
    #: `TurretUnits` 要插的那一行（`insert_row` 的 values）
    values: dict[str, Any] = field(default_factory=dict)
    #: 这一项里**已经**挂过同一座炮塔（允许，但要在状态栏说一句 —— 用户要灵活性）
    duplicated: bool = False
    #: 非空 = 不给加，界面照这句说人话（✅ 不弹窗）
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


def _option_of(data: CardData, unit_id: int, option_id: int) -> tuple[int, str, bool, int]:
    """点名某一项选项算一遍（与 `current_option` 同口径，只是不挑"当前项"）。"""
    rows = data.t(TABLE_OPTIONS)
    row = rows.by_id(option_id)
    if row is None:
        return (0, "", True, 0)
    modification_id = rows.i(row, "ModificationId")
    return (
        int(option_id),
        _option_label(data, modification_id, option_id),
        slot_owner_unit(data, modification_id) == int(unit_id),
        count_filled_slots(data, option_id),
    )


def plan_add_turret(
    data: CardData,
    unit_id: int,
    turret_id: int,
    *,
    option_id: int = 0,
    changeset: Changeset | None = None,
) -> AddTurretPlan:
    """挂一座炮塔：**写当前选项的下一个空 `TurretNId`** ＋ 在本单位 TurretUnits 补一行。

    Lead 定的落点（2026-10-01）："打开 RowPicker 选一座 Turrets，然后把它挂到当前选中选项
    的下一个空 TurretNId，并在本单位的 TurretUnits 里补一行"。两处都要写，理由不同：

    - **写选项的槽**才是"界面上看得见"的那一步：`CardData.card_turrets` 是"先取 TurretUnits
      缺省名单、再按槽覆盖（`turrets[slot] = 该槽的炮塔`，槽号超出就 append）"——
      不写槽，新挂的炮塔在 ① 那一块根本不出现；
    - **TurretUnits 补一行**是"本单位记一份"（口径 7：连带的下游取本单位那一份），
      也是本块 ✕ 的删点。

    三处拒绝（都返回一句人话，界面不写库、不弹窗）：

    1. 这个单位没有可用的改装选项 —— 21 个槽写在哪一行说不清；
    2. 当前选项是**借来的槽**（变体单位借槽主人的，实测 645 借 108 的 743）——
       写它会一起改到槽的主人，一个字节都不许写；
    3. 21 个槽全填满了 —— 先删一座（✕）再挂。

    已挂过同一座炮塔**不拒绝**（用户要"增减自由"）：照挂，`duplicated=True` 让界面说一句。

    ⚠️ 第 2 条拒绝在这条路上**走不到**（`current_option` 只遍历本单位自己的 Modifications，
    于是 `own` 恒 True）—— 留着是因为 `option_id` 那条显式入口将来要接"变体单位的槽"
    （`bench_slots` 那边 645 借 108 的 743 就是这一类）：那时这个闸门就是唯一一道。
    """
    turret_id = int(turret_id)
    unit_id = int(unit_id)
    if turret_id <= 0:
        return AddTurretPlan(turret_id=turret_id, refused="要挑一座炮塔才能挂")
    target, label, own, _count = (
        _option_of(data, unit_id, option_id) if option_id > 0 else current_option(data, unit_id)
    )
    if target <= 0:
        return AddTurretPlan(
            turret_id=turret_id,
            refused="这个单位没有可用的改装选项 —— 炮塔槽（Options.TurretNId）没有地方写，加不了",
        )
    if not own:
        return AddTurretPlan(
            turret_id=turret_id,
            option_id=target,
            option_label=label,
            refused=(
                f"当前选项「{label}」是**借来的槽**（属于别的单位）—— 往它的炮塔槽里写会一起"
                "改到槽的主人，加不了"
            ),
        )
    rows = data.t(TABLE_OPTIONS)
    row = rows.by_id(target)
    if row is None:
        return AddTurretPlan(
            turret_id=turret_id,
            option_id=target,
            option_label=label,
            refused=f"选项 #{target} 已经不在了",
        )
    filled = {slot: rows.i(row, column) for slot, column in enumerate(TURRET_SLOTS)}
    duplicated = turret_id in filled.values()
    free = [slot for slot in range(len(TURRET_SLOTS)) if filled.get(slot, 0) <= 0]
    if not free:
        return AddTurretPlan(
            turret_id=turret_id,
            option_id=target,
            option_label=label,
            refused=f"「{label}」的 21 个炮塔槽全填满了 —— 先把不用的那几座删掉（✕）再挂",
        )
    slot = free[0]
    return AddTurretPlan(
        turret_id=turret_id,
        option_id=target,
        option_label=label,
        slot=slot,
        column=TURRET_SLOTS[slot],
        values=new_turret_unit_values(
            data, unit_id, turret_id, option_id=target, changeset=changeset
        ),
        duplicated=duplicated,
    )


def new_turret_unit_values(
    data: CardData,
    unit_id: int,
    turret_id: int,
    *,
    option_id: int = 0,
    changeset: Changeset | None = None,
) -> dict[str, Any]:
    """`TurretUnits` 要插的那一行：4 列全给。

    - `Id` = **next_row_id**（把本会话删过的 Id 也算上；别写"最大 + 1"）；
    - `UnitId` = 本单位；`TurretId` = 挑中的那座炮塔；
    - `Order` = 本单位现有 Order 的**最大值 + 1** —— 与
      `bench_slots.new_option_values` 同一条理由：`card_turrets` 按 `(Order, Id)` 排，
      Order 取"条数"在同单位删过行之后就与末位序号分叉，新炮塔会插到中间去。
      最大值 + 1 让它排在队尾 = 槽号追加（`turrets.append`），与"挂到第 N 个空槽"一致。

    `option_id` 只是留给状态栏/用例的上下文，不写进那一行（这张表只有 4 列）。
    """
    table = (
        changeset.table(TABLE_TURRET_UNITS)
        if changeset is not None
        else data.instance.table(TABLE_TURRET_UNITS)
    )
    links = data.t(TABLE_TURRET_UNITS)
    siblings = links.where(COLUMN_UNIT, int(unit_id))
    order = max((links.i(item, COLUMN_ORDER) for item in siblings), default=-1) + 1
    return {
        COLUMN_ID: next_row_id(table, changeset),
        COLUMN_UNIT: int(unit_id),
        COLUMN_TURRET: int(turret_id),
        COLUMN_ORDER: order,
    }


@dataclass(frozen=True, slots=True)
class TurretRemoval:
    """✕ 删一座炮塔之前算好的那几件事（纯函数：界面照着执行并说人话）。"""

    turret_id: int
    #: 要删的 `TurretUnits` 行（0 = 本单位没有这一行）
    unit_row_id: int = 0
    #: 当前选项里**还指着它**的槽：`((槽号, 列名), ...)` —— 不腾掉它们，这一行删了也还在
    slots: tuple[tuple[int, str], ...] = ()
    option_id: int = 0
    option_label: str = ""
    #: 当前选项是不是本单位的槽（False = 借来的槽，那几格一个字节都不许写）
    own_option: bool = True
    #: 共用这座炮塔的**别的**单位（影响的是 TurretWeapons 那条路；TurretUnits 是本单位的）
    other_units: tuple[int, ...] = ()

    @property
    def removable(self) -> bool:
        """有没有"本单位那一份"可删。"""
        return self.unit_row_id > 0

    @property
    def clears(self) -> tuple[tuple[int, str], ...]:
        """真正要从选项里腾掉的槽（借来的槽一个字节都不许写）。"""
        return self.slots if self.own_option else ()

    @property
    def slot_text(self) -> str:
        return slot_range_text([slot for slot, _column in self.slots])


def plan_remove_turret(data: CardData, unit_id: int, turret_id: int) -> TurretRemoval:
    """✕ 那一行的全部后果 —— 界面按它写库 + 说人话。

    ✕ 的主写按 Lead 定的口径：**删本单位那一行 `TurretUnits`**（模板行 `Turrets` 不动）。
    但只看这一步会踩一个真坑：当前选项的槽**压过** TurretUnits 缺省名单，删了那一行，
    炮塔照样画在界面上（用户看到的就是"点了 ✕ 没反应"）。所以这份计划里带上
    "当前选项里还指着它的槽"，界面**在同一个动作里**把它们腾成 0（只在本单位的槽上写），
    并把这件事说在状态栏 —— 设计图右栏那一条正是「✕ 删掉这一座炮塔…这类后果也当场说」。
    """
    turret_id = int(turret_id)
    unit_id = int(unit_id)
    option_id, label, own, _count = current_option(data, unit_id)
    return TurretRemoval(
        turret_id=turret_id,
        unit_row_id=_turret_units_row(data, unit_id, turret_id),
        slots=option_slot_columns(data, option_id, turret_id) if option_id else (),
        option_id=option_id,
        option_label=label,
        own_option=own,
        other_units=tuple(unit for unit in turret_users(data, turret_id) if unit != unit_id),
    )


# --------------------------------------------------------------------------- #
# 规则：＋ 挂武器（往 TurretWeapons 插一行；它是**共享件** —— 写之前要说共用人数）
# --------------------------------------------------------------------------- #


def _weapon_link(data: CardData, turret_id: int, weapon_id: int) -> tuple[int, int]:
    """(TurretWeapons 行Id, 这一行挂在哪座炮塔上) —— 沿子炮塔找（0, 0 = 没有这一行）。

    与 _turret_weapon_row 同一走法（它会连子炮塔一起走，Su-25T / Mi-8 那种挂架型单位
    的武器挂在子炮塔上）。**多回一个 owner** 是因为一条武器关联行挂在**炮塔**上，
    "共用它的别的单位"要按那座炮塔算（`turret_users`）—— 只查顶层会算错。
    """
    links = data.t(TABLE_TURRET_WEAPONS)
    candidates: list[int] = [int(turret_id)]
    seen = {int(turret_id)}
    index = 0
    while index < len(candidates):
        for child in data.turret_children(candidates[index]):
            if child not in seen:
                seen.add(child)
                candidates.append(child)
        index += 1
    for owner in candidates:
        rows = sorted(
            links.where2(COLUMN_TURRET, owner, COLUMN_WEAPON, int(weapon_id)),
            key=lambda row: (links.i(row, COLUMN_ORDER), links.i(row, COLUMN_ID)),
        )
        if rows:
            return links.i(rows[0], COLUMN_ID), int(owner)
    return 0, 0


def new_weapon_link_values(
    data: CardData,
    turret_id: int,
    weapon_id: int,
    *,
    changeset: Changeset | None = None,
) -> dict[str, Any]:
    """TurretWeapons 要插的那一行（6 列全给）。

    - Id = **next_row_id**（把本会话删过的 Id 也算上；别写"最大 + 1"）；
    - TurretId = 挂到哪座炮塔；WeaponId = 挑中的武器；
    - Order = 这座炮塔现有 Order 的**最大值 + 1**（排到队尾 —— 与
      new_turret_unit_values / bench_slots.new_option_values 同一条：删过行之后
      "条数"与末位序号就分叉，新武器会插到中间去）；
    - WeaponChannel / WeaponPriority = **照 Weapons 模板行同名列填**（缺则 0）。
      ⚠️ 实测 Weapons 那两列全表是 0 的占位列，真正生效的值在 TurretWeapons 上，
      所以这里填出来通常就是 0 —— 但口径仍是"照模板行填"，游戏哪天改了不用动代码。
    """
    turret_id, weapon_id = int(turret_id), int(weapon_id)
    table = (
        changeset.table(TABLE_TURRET_WEAPONS)
        if changeset is not None
        else data.instance.table(TABLE_TURRET_WEAPONS)
    )
    links = data.t(TABLE_TURRET_WEAPONS)
    siblings = links.where(COLUMN_TURRET, turret_id)
    order = max((links.i(row, COLUMN_ORDER) for row in siblings), default=-1) + 1
    weapons = data.t(TABLE_WEAPONS)
    template = weapons.by_id(weapon_id)
    return {
        COLUMN_ID: next_row_id(table, changeset),
        COLUMN_WEAPON: weapon_id,
        COLUMN_TURRET: turret_id,
        COLUMN_ORDER: order,
        COLUMN_CHANNEL: weapons.i(template, COLUMN_CHANNEL),
        COLUMN_PRIORITY: weapons.i(template, COLUMN_PRIORITY),
    }


@dataclass(frozen=True, slots=True)
class AddWeaponPlan:
    """＋ 挂武器：TurretWeapons 要插的那一行（纯函数：界面照着 insert_row）。"""

    unit_id: int = 0
    turret_id: int = 0
    turret_label: str = ""
    weapon_id: int = 0
    weapon_label: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    #: 这座炮塔**已经挂着**同一门武器（允许 —— 用户要灵活性，界面补一句）
    duplicated: bool = False
    #: 共用这座炮塔的**别的**单位（非空时状态栏说人数）
    other_units: tuple[int, ...] = ()
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


def plan_add_weapon(
    data: CardData,
    unit_id: int,
    turret_id: int,
    weapon_id: int,
    *,
    changeset: Changeset | None = None,
) -> AddWeaponPlan:
    """＋ 挂武器：往 TurretWeapons 插一行（TurretWeapons 不带 UnitId —— 共用行要带警告）。"""
    unit_id, turret_id, weapon_id = int(unit_id), int(turret_id), int(weapon_id)
    turret_label = data.name_of(TABLE_TURRETS, turret_id) if turret_id > 0 else ""
    base: dict[str, Any] = {
        "unit_id": unit_id,
        "turret_id": turret_id,
        "turret_label": turret_label,
    }
    if weapon_id <= 0:
        return AddWeaponPlan(**base, refused="要挑一门武器才能挂")
    if turret_id <= 0:
        return AddWeaponPlan(**base, weapon_id=weapon_id, refused="先挑一座炮塔")
    if data.t(TABLE_TURRETS).by_id(turret_id) is None:
        return AddWeaponPlan(
            **base, weapon_id=weapon_id, refused=f"Turrets #{turret_id} 已经不在了"
        )
    if data.t(TABLE_WEAPONS).by_id(weapon_id) is None:
        return AddWeaponPlan(
            **base, weapon_id=weapon_id, refused=f"Weapons #{weapon_id} 已经不在了"
        )
    weapon_label = data.name_of(TABLE_WEAPONS, weapon_id)
    links = data.t(TABLE_TURRET_WEAPONS)
    siblings = links.where(COLUMN_TURRET, turret_id)
    duplicated = any(links.i(row, COLUMN_WEAPON) == weapon_id for row in siblings)
    others = tuple(unit for unit in turret_users(data, turret_id) if unit != unit_id)
    return AddWeaponPlan(
        unit_id=unit_id,
        turret_id=turret_id,
        turret_label=turret_label,
        weapon_id=weapon_id,
        weapon_label=weapon_label,
        values=new_weapon_link_values(data, turret_id, weapon_id, changeset=changeset),
        duplicated=duplicated,
        other_units=others,
    )


# --------------------------------------------------------------------------- #
# 规则：＋ 加弹种 / 改携弹量（WeaponAmmunitions 带 UnitId —— 只动本单位这一份）
# --------------------------------------------------------------------------- #


def new_ammo_values(
    data: CardData,
    unit_id: int,
    weapon_id: int,
    ammo_id: int,
    *,
    changeset: Changeset | None = None,
    quantity: int | None = None,
) -> dict[str, Any]:
    """WeaponAmmunitions 要插的那一行（6 列全给：Id / UnitId / WeaponId / AmmunitionId /
    Order / Quantity）。

    - Id = **next_row_id**；UnitId = 本单位（这张表带 UnitId —— 只动本单位）；
    - Order = (本单位, 这门武器) 现有 Order 的**最大值 + 1**；
    - Quantity 默认 **1**（设计图那格是「×数量」，加完点它就地改）；自动补弹那条传
      `quantity=常见数量` —— 见 common_ammo()。
    """
    unit_id, weapon_id, ammo_id = int(unit_id), int(weapon_id), int(ammo_id)
    table = (
        changeset.table(TABLE_WEAPON_AMMO)
        if changeset is not None
        else data.instance.table(TABLE_WEAPON_AMMO)
    )
    rows = data.t(TABLE_WEAPON_AMMO)
    siblings = rows.where2(COLUMN_UNIT, unit_id, COLUMN_WEAPON, weapon_id)
    order = max((rows.i(row, COLUMN_ORDER) for row in siblings), default=-1) + 1
    return {
        COLUMN_ID: next_row_id(table, changeset),
        COLUMN_UNIT: unit_id,
        COLUMN_WEAPON: weapon_id,
        COLUMN_AMMO: ammo_id,
        COLUMN_ORDER: order,
        COLUMN_QUANTITY: 1 if quantity is None else max(1, int(quantity)),
    }


@dataclass(frozen=True, slots=True)
class AddAmmoPlan:
    """＋ 加弹种：WeaponAmmunitions 要插的那一行（纯函数）。"""

    unit_id: int = 0
    turret_id: int = 0
    weapon_id: int = 0
    weapon_label: str = ""
    ammo_id: int = 0
    ammo_label: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    #: 这门枪**已经挂着**同一种弹（拒绝 —— 点「×数量」改数量就行，与 ② 那一块同口径）
    duplicated: bool = False
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused


def plan_add_ammo(
    data: CardData,
    unit_id: int,
    weapon_id: int,
    ammo_id: int,
    *,
    turret_id: int = 0,
    changeset: Changeset | None = None,
) -> AddAmmoPlan:
    """＋ 加弹种：往 WeaponAmmunitions 插一行（带 UnitId —— 只动本单位这一份）。"""
    unit_id, weapon_id, ammo_id, turret_id = (
        int(unit_id),
        int(weapon_id),
        int(ammo_id),
        int(turret_id),
    )
    weapon_label = data.name_of(TABLE_WEAPONS, weapon_id) if weapon_id > 0 else ""
    base: dict[str, Any] = {
        "unit_id": unit_id,
        "turret_id": turret_id,
        "weapon_id": weapon_id,
        "weapon_label": weapon_label,
    }
    if ammo_id <= 0:
        return AddAmmoPlan(**base, refused="要挑一种弹才能加")
    if weapon_id <= 0:
        return AddAmmoPlan(**base, refused="这一行没有 WeaponId —— 加不了弹")
    if data.t(TABLE_AMMO).by_id(ammo_id) is None:
        return AddAmmoPlan(**base, ammo_id=ammo_id, refused=f"Ammunitions #{ammo_id} 已经不在了")
    ammo_label = data.name_of(TABLE_AMMO, ammo_id)
    rows = data.t(TABLE_WEAPON_AMMO)
    if any(
        rows.i(row, COLUMN_AMMO) == ammo_id
        for row in rows.where2(COLUMN_UNIT, unit_id, COLUMN_WEAPON, weapon_id)
    ):
        return AddAmmoPlan(
            **base,
            ammo_id=ammo_id,
            ammo_label=ammo_label,
            duplicated=True,
            refused=f"{weapon_label} 已经挂着 {ammo_label} 了 —— 点那一行的「×数量」改数量就行",
        )
    return AddAmmoPlan(
        unit_id=unit_id,
        turret_id=turret_id,
        weapon_id=weapon_id,
        weapon_label=weapon_label,
        ammo_id=ammo_id,
        ammo_label=ammo_label,
        values=new_ammo_values(data, unit_id, weapon_id, ammo_id, changeset=changeset),
    )


@dataclass(frozen=True, slots=True)
class QuantityPlan:
    """改携弹量：那一格落在哪（纯函数；界面拿 ref 开就地编辑器）。"""

    unit_id: int = 0
    row_id: int = 0
    quantity: int = 0
    label: str = ""
    weapon_label: str = ""
    refused: str = ""

    @property
    def ok(self) -> bool:
        return not self.refused

    @property
    def ref(self) -> CellRef | None:
        if self.row_id <= 0:
            return None
        return CellRef(TABLE_WEAPON_AMMO, self.row_id, COLUMN_QUANTITY)


def plan_quantity(data: CardData, unit_id: int, row_id: int) -> QuantityPlan:
    """改携弹量那一格的落点：WeaponAmmunitions#<row>.Quantity。

    只认**本单位那一行**（这张表带 UnitId）；行没了 / 不是本单位的 → refused。
    """
    unit_id, row_id = int(unit_id), int(row_id)
    rows = data.t(TABLE_WEAPON_AMMO)
    row = rows.by_id(row_id) if row_id > 0 else None
    if row is None:
        return QuantityPlan(
            unit_id=unit_id, row_id=row_id, refused=f"WeaponAmmunitions #{row_id} 已经不在了"
        )
    if rows.i(row, COLUMN_UNIT) != unit_id:
        return QuantityPlan(
            unit_id=unit_id,
            row_id=row_id,
            refused=f"WeaponAmmunitions #{row_id} 不是本单位那一行 —— 改不了",
        )
    ammo_id = rows.i(row, COLUMN_AMMO)
    weapon_id = rows.i(row, COLUMN_WEAPON)
    return QuantityPlan(
        unit_id=unit_id,
        row_id=row_id,
        quantity=rows.i(row, COLUMN_QUANTITY),
        label=data.name_of(TABLE_AMMO, ammo_id) if ammo_id > 0 else f"#{row_id}",
        weapon_label=data.name_of(TABLE_WEAPONS, weapon_id) if weapon_id > 0 else "",
    )


__all__ = [
    "AMMO_ICON_SOURCE",
    "COLUMN_AMMO",
    "COLUMN_CHANNEL",
    "COLUMN_ID",
    "COLUMN_MODEL",
    "COLUMN_ORDER",
    "COLUMN_PRIORITY",
    "COLUMN_QUANTITY",
    "COLUMN_TURRET",
    "COLUMN_UNIT",
    "COLUMN_WEAPON",
    "TABLE_AMMO",
    "TABLE_MODIFICATIONS",
    "TABLE_OPTIONS",
    "TABLE_TURRETS",
    "TABLE_TURRET_UNITS",
    "TABLE_TURRET_WEAPONS",
    "TABLE_WEAPON_AMMO",
    "TABLE_WEAPONS",
    "TURRET_ICON_SOURCE",
    "WEAPON_ICON_SOURCE",
    "AddAmmoPlan",
    "AddTurretPlan",
    "AddWeaponPlan",
    "AmmoLine",
    "QuantityPlan",
    "TurretLine",
    "TurretRemoval",
    "TurretTree",
    "WeaponLine",
    "count_filled_slots",
    "current_option",
    "new_ammo_values",
    "new_turret_unit_values",
    "new_weapon_link_values",
    "option_slot_columns",
    "option_slot_ids",
    "plan_add_ammo",
    "plan_add_turret",
    "plan_add_weapon",
    "plan_quantity",
    "plan_remove_turret",
    "structure_key",
    "turret_tree",
    "turret_users",
]
