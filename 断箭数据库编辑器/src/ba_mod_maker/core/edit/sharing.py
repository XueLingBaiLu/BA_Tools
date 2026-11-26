"""共享度：一个模板行被多少个**单位**共用（零 Qt）。

## 为什么必须有它

卡片上显示的东西**几乎全是共享模板行**。实测（docs/设计-单位卡片复刻.md §6，以 645 为例）：

| 卡片上的东西 | 被几个单位共用 |
|---|---|
| 传感器 Medium Plus Optics | **142** |
| 机动 Infantry 18kmh Airdrop | **79** |
| 弹药 InfGrenade 40x102 GP30 | **32** |
| 武器 AK-74RMO | **16** |
| 弹药 PG-7VL | 7 |
| 武器 RPG-7V | 6 |
| 装甲 Armor Infantry Elite 5Men | 2 |
| SquadMembers / SquadWeapons / WeaponAmmunitions / UnitArmors | **独占**（1） |

一动就是几十上百个单位 —— 所以每处共享值旁边要标「被 N 个单位共用」，
改共享值要走「另存为新模板」（复制一行、本单位指过去、别人不动）。

## 判据是**引用边**，不是"值相等"

"这个传感器被谁用"要看 `SensorUnits`，不是"看还有谁的 Sensors 值一样"。
值相等但不同 Id 的两行是两回事：改一行不能动到另一行。

`关联行`（带 UnitId 的那些表）天然属于一个单位，共享度就是 1 —— 设计 §6 说的"独占"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ba_mod_maker.core.data.dataset import Instance


@dataclass(frozen=True, slots=True)
class Edge:
    """一条引用边：`from_table.column` 指向 `table` 的某一行（按 Id）。

    属性
    ----
    table / column
        被引用的表，以及引用它的那一列（外键）。
    from_table
        引用方所在的表。
    unit_column
        引用方表上哪一列是 UnitId（关联行都有）。
    via
        需要经一跳才能拿到 UnitId 时用：(中间表, 中间表的外键列, 中间表的 UnitId 列)。
        实测只有 TurretWeapons 与 Options 需要它 —— 它们自己不带 UnitId。
    via_key
        经一跳时，**引用方表上**用哪一列去查中间表。
        留空 = 与 via 的第二项同名（TurretWeapons.TurretId 查 TurretUnits.TurretId）。
        Options 需要单独给：它的是 Options.ModificationId 查 Modifications.Id
        —— 两个列名不一样，靠同名那一套查不到（查了会拿 Id 去比 ModificationId）。
    key_column
        **被计数的那个 Id 取引用方哪一列**。留空 = 取 column 指向的值（默认语义：
        "这一行被谁引用"）。Options 那条要单独给 "Id"：被计数的行是
        **选项自己**，而 column（ModificationId）只用来找它的归属改装槽。
    """

    table: str
    column: str
    from_table: str
    unit_column: str = "UnitId"
    via: tuple[str, str, str] | None = None
    via_key: str = ""
    key_column: str = ""


#: 全部引用边。加一条之前先确认：这张表**真有**这一列（拿不准就别加，宁可少标）。
EDGES: tuple[Edge, ...] = (
    Edge("Sensors", "SensorId", "SensorUnits"),
    Edge("Mobility", "MobilityId", "UnitPropulsions"),
    Edge("Armors", "ArmorId", "UnitArmors"),
    Edge("Abilities", "AbilityId", "UnitAbilities"),
    Edge("Weapons", "WeaponId", "SquadWeapons"),
    Edge("Weapons", "WeaponId", "TurretWeapons", via=("TurretUnits", "TurretId", "UnitId")),
    Edge("Ammunitions", "AmmunitionId", "WeaponAmmunitions"),
    Edge("Turrets", "TurretId", "TurretUnits"),
    # 本轮补的两条（单位编辑器要用）：选项行、专精行。
    # 补之前它们的共享度恒为 0（没有边 = 查不到），于是「另存为新模板」
    # 在改装选项那一页永远不会出现 —— 而那正是最需要它的地方
    # （一个 Options 行可以被几十个改装槽用）。
    Edge(
        "Options",
        "ModificationId",
        "Options",
        via=("Modifications", "Id", "UnitId"),
        via_key="ModificationId",
        key_column="Id",
    ),
    Edge("Specializations", "SpecializationId", "SpecializationAvailabilities"),
)

#: 带 UnitId 的表 = 关联行（这一行就属于那一个单位）。
LINK_TABLES: tuple[str, ...] = (
    "SquadMembers",
    "SquadWeapons",
    "WeaponAmmunitions",
    "UnitArmors",
    "UnitPropulsions",
    "UnitAbilities",
    "SensorUnits",
    "TurretUnits",
    "TransportAvailabilities",
    "SpecializationAvailabilities",
    # 本轮补：实测带 UnitId 的表是 **11 张**，这张原来漏了 ——
    # 症状是 Modifications 的每一行共享度报 0（"查不到"），而不是 1（"独占"）。
    # 界面据 0 会以为"这一行没有归属"，而它其实就属于这一个单位。
    "Modifications",
)


@dataclass(frozen=True, slots=True)
class SharingIndex:
    """(表, 行 Id) -> 用它的那些单位。**建一次，反复用**。"""

    _users: Mapping[tuple[str, int], frozenset[int]] = field(default_factory=dict)
    _unit_of_row: Mapping[tuple[str, int], int] = field(default_factory=dict)

    def of(self, table: str, row_id: int) -> int:
        """这个模板行被多少个单位共用。

        关联行（带 UnitId 的表）返回 1；查不到返回 0（**不猜**）。
        """
        users = self._users.get((table, row_id))
        if users is not None:
            return len(users)
        return 1 if (table, row_id) in self._unit_of_row else 0

    def units(self, table: str, row_id: int) -> frozenset[int]:
        """用它的那些单位（关联行给 {属主单位}）。"""
        users = self._users.get((table, row_id))
        if users is not None:
            return users
        owner = self._unit_of_row.get((table, row_id))
        return frozenset({owner}) if owner is not None else frozenset()

    def is_shared(self, table: str, row_id: int) -> bool:
        """被**不止一个**单位用 —— 改它要另存为新模板。"""
        return self.of(table, row_id) > 1

    @property
    def labeled_rows(self) -> int:
        """有多少行有共享度可标（诊断用）。"""
        return len(self._users) + len(self._unit_of_row)


def _unit_of(link_row, columns: Mapping[str, int], unit_column: str) -> int:
    index = columns.get(unit_column)
    if index is None or index >= len(link_row):
        return 0
    value = link_row[index]
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def build_sharing_index(instance: Instance) -> SharingIndex:
    """建一遍全部引用边。540 个单位、几千行关联行，一次 10ms 级；建完缓存住。"""
    users: dict[tuple[str, int], set[int]] = {}
    unit_of_row: dict[tuple[str, int], int] = {}

    def add(table: str, row_id: int, unit_id: int) -> None:
        if row_id <= 0 or unit_id <= 0:
            return
        users.setdefault((table, row_id), set()).add(unit_id)

    for edge in EDGES:
        try:
            links = instance.table(edge.from_table)
        except KeyError:
            continue  # 这一版数据没有这张表：少标一个，不猜
        columns = {name: i for i, name in enumerate(links.columns)}
        foreign = columns.get(edge.column)
        if foreign is None:
            continue

        #: 中间表主键 -> 用它的那些单位。经一跳时用（只有一个 Edge 需要）。
        via_units: dict[int, set[int]] | None = None
        #: 同一个列名在**本表**上的下标（如 TurretWeapons 上的 TurretId）。
        link_foreign: int | None = None
        if edge.via is not None:
            via_table, via_column, via_unit_column = edge.via
            try:
                middle = instance.table(via_table)
            except KeyError:
                continue
            middle_columns = {name: i for i, name in enumerate(middle.columns)}
            middle_foreign = middle_columns.get(via_column)
            # 引用方表上查中间表用的列：默认与 via 的第二项同名，
            # Options 那条要单独给（Options.ModificationId 查 Modifications.Id）。
            link_foreign = columns.get(edge.via_key or via_column)
            if middle_foreign is None or link_foreign is None:
                continue
            via_units = {}
            for row in middle.rows:
                key = row[middle_foreign]
                unit_id = _unit_of(row, middle_columns, via_unit_column)
                if isinstance(key, int) and not isinstance(key, bool) and unit_id:
                    # ❗ 收**全部**单位，不是第一个：一个炮塔模板可以挂在多个单位上
                    #    （实测 1055 个炮塔里 174 个如此），只记第一个会让共享度偏小。
                    via_units.setdefault(key, set()).add(unit_id)

        # 被计数的 Id 取哪一列：默认是外键列（"这一行被谁引用"），
        # Options 那条取自己的 Id（"这个选项被哪些单位用着"）。
        key_index = columns.get(edge.key_column) if edge.key_column else foreign

        for row in links.rows:
            if key_index is None or key_index >= len(row):
                continue
            key = row[key_index]
            if not isinstance(key, int) or isinstance(key, bool) or key <= 0:
                continue
            if via_units is not None and link_foreign is not None:
                # ❗ 用**本表那一列**（TurretWeapons.TurretId）去查中间表的主键。
                #    曾经这里拿目标列（WeaponId）去查 TurretId 建的表 —— 键对不上，
                #    于是经炮塔那一跳永远查不到，**车辆武器的共享度恒为 0 或 1**。
                #    测试没抓到是因为那几个期望值全是**步兵**武器（走 SquadWeapons 那条直连边）。
                for unit_id in via_units.get(row[link_foreign], ()):
                    add(edge.table, key, unit_id)
            else:
                add(edge.table, key, _unit_of(row, columns, edge.unit_column))

    for name in LINK_TABLES:
        try:
            table = instance.table(name)
        except KeyError:
            continue
        columns = {name_: i for i, name_ in enumerate(table.columns)}
        id_index = columns.get("Id")
        if id_index is None:
            continue
        for row in table.rows:
            row_id = row[id_index]
            unit_id = _unit_of(row, columns, "UnitId")
            if isinstance(row_id, int) and not isinstance(row_id, bool) and unit_id:
                unit_of_row[(name, row_id)] = unit_id

    return SharingIndex(
        _users={key: frozenset(value) for key, value in users.items()},
        _unit_of_row=unit_of_row,
    )


__all__ = ["EDGES", "LINK_TABLES", "Edge", "SharingIndex", "build_sharing_index"]
