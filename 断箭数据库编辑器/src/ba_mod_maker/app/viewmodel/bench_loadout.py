"""编成台 ②「班用武器与弹药」的取数与规则 —— **零 Qt**（tests/test_core_layering.py 用 AST 扫）。

出处：`docs/交接-2026-10-01-单位编成台设计.md` §3（十条口径）＋ 设计图
`docs/设计稿-编成台-步兵.png` 的 ② 那一块。三句话说完：

1. **一件武器一行**：一行 = `SquadWeapons` 里本单位（带 UnitId）的那一批；名字与图标
   走它指的 `Weapons` **模板行**（`HUDIcon`，族 weapon_icons）；
2. **弹挂在它下面**：`WeaponAmmunitions` 按（班 × 武器）=（UnitId, WeaponId）取，
   一门枪可以挂好几种弹 —— 按 `(Order, Id)` 排。一门枪一行弹都没有时由控件写
   `MISSING_AMMO_TEXT`（「缺弹 · 空枪」），**别静默留白**；
3. **只有 ＋ 和 ✕，没有"换"**：这一层只回答"现在有什么"，写库一律经 `EditorContext`
   （set_value / insert_row / delete_row / add_block_row）。

❗ 弹行**不是**武器行的列：数量 `Quantity` 在 `WeaponAmmunitions` 那一行上 ——
所以一个弹行 = 库里**另外一行**（取数口径与 `screens/editor/ammo_block.py` 一致，
但那一套控件带「🔁 换」，本轮口径只要 ＋ / ✕，所以不复用它的控件）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ba_mod_maker.app.viewmodel.unit_card import CardData, weapon_ammo_choices
from ba_mod_maker.core.assets.families import (
    AMMO_ICON_FAMILY,
    WEAPON_ICON_FAMILY,
    row_icon_source,
)
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.refs import CellRef

# ---- 表名与列名：字面量只在这一个文件里出现一次 ----------------------------- #

TABLE_SQUAD_WEAPONS = "SquadWeapons"
TABLE_WEAPON_AMMO = "WeaponAmmunitions"
TABLE_WEAPONS = "Weapons"
TABLE_AMMO = "Ammunitions"
TABLE_MEMBERS = "SquadMembers"

COLUMN_ID = "Id"
COLUMN_UNIT = "UnitId"
COLUMN_WEAPON = "WeaponId"
COLUMN_AMMO = "AmmunitionId"
COLUMN_QUANTITY = "Quantity"
COLUMN_ORDER = "Order"

#: 「这门枪一行弹都没有」的显示文本（设计图 ② 那一块就是这么标的）。
#: 控件与用例**共用这一个常量** —— 别在两边各写一遍字面量。
MISSING_AMMO_TEXT = "缺弹 · 空枪"

#: 「缺弹 · 空枪」占位行在**结构署名**里用的"表名"（见 structure_key）。
#: 它不是库里的一行，但**占一行**：一门枪从有弹变成没弹（或反过来）时结构变了 ——
#: 那是"少一个控件 / 多一个控件"，就地换值换不出来，必须整块重建。
MISSING_AMMO_ROW_KEY = "·缺弹·空枪"

#: 图标 = (族名, 键列)。**唯一出处**是 `core/assets/families.py::ROW_ICON_SOURCES`
#: （界面不许自己抄族名 / 列名：抄错一个字母的症状是"这一格永远没图标"，还不报错）。
#: 末尾那个 or 只是兜底 —— 这两张表在 ROW_ICON_SOURCES 里都登记着（test_row_icon.py 守着）。
WEAPON_ICON_SOURCE: tuple[str, str] = row_icon_source(TABLE_WEAPONS) or (
    WEAPON_ICON_FAMILY,
    "HUDIcon",
)
AMMO_ICON_SOURCE: tuple[str, str] = row_icon_source(TABLE_AMMO) or (AMMO_ICON_FAMILY, "HUDIcon")


def _label(data: CardData, table: str, row_id: int) -> str:
    """一行现在叫什么 —— **每次现取**（core/edit/foreign.py::display_name，唯一口径）。

    为什么不用 `CardData.name_of`（卡上那条路）：它带名字缓存（`_names`），只有
    `CardData.invalidate()` 才清；而"就地换值"这条路上必须给**刚改过的**字 ——
    缓存里那份会让"控件保住了、字还是旧的"变成一次假绿（用户口径：界面要跟着改）。
    代价：一次现取 = 把那张表扫一遍（Weapons 804 / Ammunitions 630 行），
    这一块最多十几行 —— 毫秒级，可忽略。
    """
    return display_name(data.instance, table, int(row_id))


# ---- 读出来的那两样东西 ------------------------------------------------------ #


@dataclass(frozen=True, slots=True)
class AmmoLine:
    """一条携弹行（`WeaponAmmunitions` 一行）。

    属性
    ----
    row_id
        这一行自己的 Id —— 改数量（`quantity_ref`）与「✕」删这一行都按它寻址。
    ammo_id
        `Ammunitions.Id`（弹种模板行：名字、图标、HUDMultiplier 都在它身上）。
    label / icon_key
        弹种显示名 / `Ammunitions.HUDIcon`（族见 `AMMO_ICON_SOURCE`）。
    quantity
        `WeaponAmmunitions.Quantity` —— 设计图 ② 那个「×200」。
    """

    row_id: int
    ammo_id: int
    label: str
    icon_key: str
    quantity: int


@dataclass(frozen=True, slots=True)
class WeaponLine:
    """一件班用武器（`SquadWeapons` 一行）＋挂在它下面的弹。

    属性
    ----
    squad_weapon_id
        `SquadWeapons.Id` —— 「✕」删的是**这一行**（关联行），不是 Weapons 模板行。
    weapon_id
        `Weapons.Id`（模板行：名字与 `HUDIcon` 从它来；删它 = 动别的单位）。
    label / icon_key
        武器显示名 / `Weapons.HUDIcon`（族见 `WEAPON_ICON_SOURCE`）。
    ammo
        挂在这门枪下面的弹行，按 `(Order, Id)` 排；空元组 = 这门枪一行弹都没有
        （界面上写 `MISSING_AMMO_TEXT`，别留白）。
    weapon_ammo_row_id
        它**第一行**弹的 Id（没有弹 = 0）：设计图里武器行与它的弹行是一对一的
        （单位 70 四件武器各一行），状态栏 / 悬停提示要指出"这门枪现在挂的是哪一条"时用它。
    """

    squad_weapon_id: int
    weapon_id: int
    label: str
    icon_key: str
    ammo: tuple[AmmoLine, ...]
    weapon_ammo_row_id: int = 0


# ---- 取数 -------------------------------------------------------------------- #


def _sorted_rows(rows, table, *, unit_id: int, weapon_id: int | None = None):
    """(Order, Id) 排 —— 与设计口径一致（Order 是游戏自己的次序，Id 是稳定的第二判据）。"""
    if weapon_id is None:
        picked = table.where(COLUMN_UNIT, unit_id)
    else:
        picked = table.where2(COLUMN_UNIT, unit_id, COLUMN_WEAPON, weapon_id)
    return sorted(picked, key=lambda row: (table.i(row, COLUMN_ORDER), table.i(row, COLUMN_ID)))


def _ammo_line(data: CardData, row, links) -> AmmoLine:
    ammo_id = links.i(row, COLUMN_AMMO)
    ammo = data.t(TABLE_AMMO)
    template = ammo.by_id(ammo_id)
    return AmmoLine(
        row_id=links.i(row, COLUMN_ID),
        ammo_id=ammo_id,
        label=_label(data, TABLE_AMMO, ammo_id),
        icon_key=ammo.s(template, AMMO_ICON_SOURCE[1]) if template is not None else "",
        quantity=links.i(row, COLUMN_QUANTITY),
    )


def _weapon_line(data: CardData, unit_id: int, link_row, squad, links) -> WeaponLine:
    weapon_id = squad.i(link_row, COLUMN_WEAPON)
    weapons = data.t(TABLE_WEAPONS)
    template = weapons.by_id(weapon_id)
    ammo = tuple(
        _ammo_line(data, row, links)
        for row in _sorted_rows(links, links, unit_id=unit_id, weapon_id=weapon_id)
    )
    return WeaponLine(
        squad_weapon_id=squad.i(link_row, COLUMN_ID),
        weapon_id=weapon_id,
        label=_label(data, TABLE_WEAPONS, weapon_id),
        icon_key=weapons.s(template, WEAPON_ICON_SOURCE[1]) if template is not None else "",
        ammo=ammo,
        weapon_ammo_row_id=ammo[0].row_id if ammo else 0,
    )


def loadout_lines(data: CardData, unit_id: int) -> tuple[WeaponLine, ...]:
    """本单位的班用武器 + 每件武器挂着的弹（编成台 ② 的全部内容）。

    ❗ 读之前先丢缓存：`Rows.where / where2` 都把筛选结果缓存着，刚插过 / 删过行
    不丢就会照**旧名单**画（`ammo_block._entries` 与 `unit_card.Rows.invalidate`
    是同一条；`EditorContext` 的写路径也会丢，但界面这一层不许依赖"别人替我丢过了"）。
    """
    squad = data.t(TABLE_SQUAD_WEAPONS)
    links = data.t(TABLE_WEAPON_AMMO)
    squad.invalidate()
    links.invalidate()
    return tuple(
        _weapon_line(data, unit_id, row, squad, links)
        for row in _sorted_rows(None, squad, unit_id=unit_id)
    )


def structure_key(lines: tuple[WeaponLine, ...]) -> tuple[tuple[str, int], ...]:
    """这一块**结构**的署名：画出来的每一行，是哪张表的哪一行（含缺弹占位行）。

    用途只有一个 —— **值变了就地换字、结构变了才重建**：

    - 署名没变 → `LoadoutBench.reload_values()`：只改现有控件的文字 / 图标，
      **一个控件都不重建**（用户口径，提过几回：「我修改的时候你能不能不要全量保存一下，
      不然界面闪一下很影响观感」；先例 docs/交接-2026-09-30-界面批-图标树弹药.md:76/93 ——
      署名变了才整套重建，没变走 reload()）；
    - 署名变了（插行 / 删行 / 一门枪有弹↔没弹）→ `LoadoutBench.refresh()` 整块重建。

    为什么占位行也算：它是一条**看得见的行**，"这门枪现在没弹"与"有弹"是两种结构；
    漏了它，删掉最后一发弹时就会只换字、少掉/多出一个控件。
    """
    out: list[tuple[str, int]] = []
    for line in lines:
        out.append((TABLE_SQUAD_WEAPONS, line.squad_weapon_id))
        if line.ammo:
            out.extend((TABLE_WEAPON_AMMO, ammo.row_id) for ammo in line.ammo)
        else:
            out.append((MISSING_AMMO_ROW_KEY, line.squad_weapon_id))
    return tuple(out)


# ---- 规则：＋ 之前要问的两句 ------------------------------------------------- #


def ammo_choices(data: CardData, unit_id: int, weapon_id: int) -> tuple[tuple[int, str], ...]:
    """这门武器能加的弹种：`(弹种Id, 显示名)`。

    口径**只有一份** —— `viewmodel/unit_card.py::weapon_ammo_choices`：全库里这门武器
    用过的弹种 ∪ 本单位已经挂着的。为什么不给全部 630 种：游戏显示哪些弹种由武器自己的
    弹药列表决定，随手加一条库外弹药，游戏多半压根不显示 —— 那是「看着改了其实没改」。
    """
    return tuple(
        (choice.ammo_id, choice.label)
        for choice in weapon_ammo_choices(data, unit_id, int(weapon_id))
    )


def ammo_is_known(data: CardData, unit_id: int, weapon_id: int, ammo_id: int) -> bool:
    """这门武器**在库里**用过这种弹吗。

    不是"不许加"——用户要灵活性；但不在名单里就得在状态栏说一句
    （口径见 `ammo_choices`），别让人以为游戏里会显示。
    """
    return int(ammo_id) in {
        choice_id for choice_id, _label in ammo_choices(data, unit_id, weapon_id)
    }


def owned_ammo_ids(data: CardData, unit_id: int, weapon_id: int) -> frozenset[int]:
    """这门枪**现在**挂着的弹种（`Rulebook: uniq.WeaponAmmunitions` = 班 × 武器 × 弹种）。

    口径只有一份：`CardData.weapon_ammo`（与卡片、左树同一份）。
    """
    return frozenset(
        ammo_id for ammo_id, _quantity, _order in data.weapon_ammo(int(unit_id), int(weapon_id))
    )


def registered_weapon_ids(data: CardData, unit_id: int) -> frozenset[int]:
    """这个班已经注册的班用武器。

    `rulebook.json::uniq.SquadWeapons` 是 **error 级**（唯一键 = UnitId + WeaponId，
    原版 0 组重复）—— 所以「＋ 加一件武器」得先问这一句，别插出第二行同一个班 × 同一门枪。
    """
    squad = data.t(TABLE_SQUAD_WEAPONS)
    return frozenset(squad.i(row, COLUMN_WEAPON) for row in squad.where(COLUMN_UNIT, int(unit_id)))


def member_carriers(data: CardData, unit_id: int, weapon_id: int) -> int:
    """本单位还有几名成员扛着这门枪（**删注册行之前**问的一句）。

    为什么必须在删之前算：规则引擎有一条 error 级不变量「SquadMembers 用到的每一门枪
    都必须在**同单位**的 SquadWeapons 里」（docs/关系图-步兵链.md §3）—— 删了注册行、
    还有成员扛着它，实机表现是游戏里那件武器**不显示**。
    （口径与 `pages/base.py::_squad_weapon_users` 一致，这里按"一门枪"问。）
    """
    members = data.t(TABLE_MEMBERS)
    members.invalidate()
    weapon_id = int(weapon_id)
    return sum(
        1
        for row in members.where(COLUMN_UNIT, int(unit_id))
        if members.i(row, "PrimaryWeaponId") == weapon_id
        or members.i(row, "SpecialWeaponId") == weapon_id
    )


def quantity_ref(row_id: int) -> CellRef:
    """这一行弹的携弹量那一格 —— 就地改数量写的就是它（`EditorContext.set_value`）。"""
    return CellRef(TABLE_WEAPON_AMMO, int(row_id), COLUMN_QUANTITY)


__all__ = [
    "AMMO_ICON_SOURCE",
    "COLUMN_AMMO",
    "COLUMN_ID",
    "COLUMN_ORDER",
    "COLUMN_QUANTITY",
    "COLUMN_UNIT",
    "COLUMN_WEAPON",
    "MISSING_AMMO_ROW_KEY",
    "MISSING_AMMO_TEXT",
    "TABLE_AMMO",
    "TABLE_MEMBERS",
    "TABLE_SQUAD_WEAPONS",
    "TABLE_WEAPON_AMMO",
    "TABLE_WEAPONS",
    "WEAPON_ICON_SOURCE",
    "AmmoLine",
    "WeaponLine",
    "ammo_choices",
    "ammo_is_known",
    "loadout_lines",
    "member_carriers",
    "owned_ammo_ids",
    "quantity_ref",
    "registered_weapon_ids",
    "structure_key",
]
