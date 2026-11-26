"""编成台 ① 「这个班」的取数 —— **零 Qt**（AGENTS §4 第 2 条）。

出处：docs/交接-2026-10-01-单位编成台设计.md §3 口径 3 与图 docs/设计稿-编成台-步兵.png。
一格 = SquadMembers 的一行 = **一个兵**：

    01  ✕
    [枪图标] Mk18 CQBR        <- 主武器：Weapons.HUDIcon 那张游戏原生图 + 名字
    [枪图标] M72A10 LAW       <- 副武器：SpecialWeaponId；0 = 「没有副武器」（不是空白）
    officer                   <- 脚注：ModelFileName 去掉「阵营_单位_」前缀

为什么取数单独一层：规则要能直接断言（tests/test_bench_squad.py 全部断言钉在这一层
的返回值与控件的读数上），控件那一层只管摆控件与发信号。

三条不重造轮子：

1. 成员名单与序号走 `unit_card.squad_loadout`（它就是「按 Id 升序 + 1 起编号」）；
2. 名字走 `unit_card.row_name`（core/edit/foreign.display_name：Name / HUDName，
   查不到写「表名 #Id」——不编假名字）；**别用 CardData.display_name**，那是本地化键解析器，
   传表名进去只会原样返回表名（unit_card.row_name 的 docstring 实测踩过）；
3. 图标的族名+键列走 `viewmodel/row_icon.py`（唯一出处 core/assets/families.py::
   ROW_ICON_SOURCES：Weapons -> (weapon_icons, HUDIcon)）——这里不抄任何字面量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ba_mod_maker.app.viewmodel.row_icon import row_icon
from ba_mod_maker.app.viewmodel.unit_card import (
    CardData,
    new_member_values,
    row_name,
    squad_loadout,
)
from ba_mod_maker.core.edit.scope import RowRef

#: 班成员表（24 张表之一）。列（数据源实测 6 列）：
#: Id / DeathPriority / ModelFileName / PrimaryWeaponId / SpecialWeaponId / UnitId。
TABLE = "SquadMembers"

#: 武器表 —— 一格上那两行枪的名字与图标都从它来。
WEAPONS = "Weapons"

#: SpecialWeaponId 是 0 / None 时那一行写什么。**不是空白** —— 空白看不出
#: 「这名兵没配副武器」还是「界面没画出来」。口径与
#: app/screens/editor/pages/squad_page.py::_cell_text 同一条：库里 0 = 「不使用」，
#: 不是「指向 Id 0」。
NO_SPECIAL = "没有副武器"

#: 一个人都没有时那一句（控件层拿它画 BenchEmpty，不留空框）。
EMPTY_SQUAD = "这个班一个人都没有"

#: 这个单位压根不是步兵（没有班成员这一套）——与「0 人班」不是同一件事。
EMPTY_NOT_INFANTRY = "这个单位不是步兵 —— 没有班组成员这一套"


@dataclass(frozen=True, slots=True)
class SquadCell:
    """一格 = 一个兵。控件层要画什么，全在这里；控件不再自己算。

    `primary_key` / `special_key` 是**图标键**（Weapons.HUDIcon 那一列的值），
    族名不在这里：它只有一份出处（core/assets/families.py::WEAPON_ICON_FAMILY）。
    没有键（武器不存在 / Id 是 0）时给空串 —— 控件照口径留空位，不画虚线框。
    `row_ref` 是 (表, 行 Id)，`str()` 出来就是 `SquadMembers#117`。
    """

    member_id: int
    #: 序号，**1 起**（显示时补零成 01..14，见控件层）。
    index: int
    primary_weapon_id: int
    primary_label: str
    primary_key: str
    special_weapon_id: int
    special_label: str
    special_key: str
    #: 脚注 = ModelFileName 去掉单位前缀（"US_MarineRaider_rifle" -> "rifle"）。
    footnote: str
    row_ref: RowRef


def member_footnote(model_file_name: str) -> str:
    """士兵模型键 -> 格脚那一行小字：**去掉「阵营_单位_」前缀**。

    规则与实测（DataBaseCompiled/52085 · SquadMembers 全表 1134 行 / 286 种取值）：

    - 取值形态是 `<阵营>_<单位>_<角色…>`：`US_MarineRaider_rifle`、
      `RU_VDV_rifle_1`、`DLC3_SpetsnazAlpha_officer`；
    - 段数分布：1 段 1 行 / 2 段 134 行 / 3 段 899 行 / 4 段 100 行
      —— 也就是 999/1134 行有前缀可去；
    - 前缀榜（前两段）：RU_Motostrelki 110、US_Army 98、RU_VDV 55、US_Ranger 54、
      US_Airborne 47、US_Marine 42、RU_GRUSpetsnaz 40、US_MARINE 32、
      US_MarineRaider 28、DLC3_SpetsnazAlpha 28 …

    所以口径是「按 `_` 切，丢掉前两段，剩下的原样接回去」：

    >>> member_footnote("US_MarineRaider_rifle")   # -> "rifle"
    >>> member_footnote("RU_VDV_rifle_1")          # -> "rifle_1"（不是 "VDV_rifle_1"）
    >>> member_footnote("US_Rangers")              # 只有两段：认不出前缀，原样返回

    为什么不去"猜"前缀长度（比如找第一个下划线）：`RU_VDV_rifle_1` 那种取值里
    角色名自己还带下划线，按第一个下划线切会切出 `VDV_rifle_1`（实机就是错的）。
    """
    text = (model_file_name or "").strip()
    if not text:
        return ""
    parts = text.split("_")
    if len(parts) < 3:
        return text
    # 末尾是空段（"US_MarineRaider_" 这种）时不返回空串 —— 宁可原样显示。
    return "_".join(parts[2:]) or text


def squad_cells(data: CardData, unit_id: int) -> tuple[SquadCell, ...]:
    """这个班的格子（**按 Id 升序**，序号 1 起）。非步兵 / 一个人都没有 -> 空元组。

    顺序不在这里排：`squad_loadout` 走 `CardData.squad`，那一句已经按 Id 升序
    （`sorted(..., key=Id)`）—— 再排一次就等于有两份口径。
    """
    loadout = squad_loadout(data, unit_id)
    if loadout is None:
        return ()
    rows = data.t(TABLE)
    out: list[SquadCell] = []
    for member in loadout.members:
        row = rows.by_id(member.member_id)
        model = rows.s(row, "ModelFileName") if row is not None else ""
        out.append(
            SquadCell(
                member_id=member.member_id,
                index=member.index,
                primary_weapon_id=member.primary_weapon_id,
                primary_label=_weapon_label(data, member.primary_weapon_id),
                primary_key=_weapon_key(data, member.primary_weapon_id),
                special_weapon_id=member.special_weapon_id,
                special_label=_special_label(data, member.special_weapon_id),
                special_key=_weapon_key(data, member.special_weapon_id),
                footnote=member_footnote(model),
                row_ref=RowRef(TABLE, member.member_id),
            )
        )
    return tuple(out)


def squad_empty_text(data: CardData | None, unit_id: int | None) -> str:
    """一个人都没有时那一句灰字 —— 「0 人班」与「不是步兵」分开说。

    两者在界面上的下一步完全不同：前者点「＋ 加一名兵」就有救，
    后者点了会抛 TableError（这个单位没有班成员这一套）。
    """
    if data is None or unit_id is None:
        return EMPTY_SQUAD
    if squad_loadout(data, unit_id) is None:
        return EMPTY_NOT_INFANTRY
    return EMPTY_SQUAD


def copy_member_values(data: CardData, unit_id: int, member_id: int) -> dict[str, Any] | None:
    """「⧉ 复制这一名」要写的那一行：**整行照抄**（Id 不抄、UnitId 照本单位）。

    实现落到 `unit_card.new_member_values(copy_from=…)` —— 与「＋ 加一名兵」同一个落点，
    不另写一套"逐列搬值"（列一变就会漏一个；2026-10-01 那轮的 6 个错全是这类）。
    新 Id 由 EditorContext.insert_row -> core/edit/clone.py::next_row_id 给，这里不碰。

    这一名不在这个班里（Id 过时 / 不是本单位的人）-> None，调用方别写库。
    """
    loadout = squad_loadout(data, unit_id)
    if loadout is None:
        return None
    index = next(
        (i for i, member in enumerate(loadout.members) if member.member_id == int(member_id)),
        -1,
    )
    if index < 0:
        return None
    return new_member_values(data, loadout, copy_from=index)


# --------------------------------------------------------------------------- #
# 一格上那两行枪
# --------------------------------------------------------------------------- #


def _positive(value: object) -> int:
    """库里 0 = 「不使用」，不是「指向 Id 0」（关系图 §5 第 1 条）。

    返回**可用的正 Id**（0 = 没有），顺带把 bool 挡掉（True 不是 Id 1）。
    """
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def _weapon_label(data: CardData, weapon_id: object) -> str:
    """主武器那一行的名字（**沿用 row_name 的既有口径**），0 -> 空串。"""
    weapon = _positive(weapon_id)
    return row_name(data, WEAPONS, weapon) if weapon else ""


def _special_label(data: CardData, weapon_id: object) -> str:
    """副武器那一行：0 / None -> 「没有副武器」（**不是空白**）。"""
    weapon = _positive(weapon_id)
    return row_name(data, WEAPONS, weapon) if weapon else NO_SPECIAL


def _weapon_key(data: CardData, weapon_id: object) -> str:
    """这一门枪的图标键（Weapons.HUDIcon）；没有 -> 空串（控件留空位）。

    走 `row_icon`（**唯一出处** core/assets/families.py::ROW_ICON_SOURCES）：
    族名与键列都不在这里抄字面量，武器表没登记 / 那一列不在这一版数据里也给空串。
    """
    weapon = _positive(weapon_id)
    if not weapon:
        return ""
    icon = row_icon(data.instance, WEAPONS, weapon)
    return "" if icon is None else icon.key


__all__ = [
    "EMPTY_NOT_INFANTRY",
    "EMPTY_SQUAD",
    "NO_SPECIAL",
    "TABLE",
    "WEAPONS",
    "SquadCell",
    "copy_member_values",
    "member_footnote",
    "squad_cells",
    "squad_empty_text",
]
