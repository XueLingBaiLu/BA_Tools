"""哪些行属于这个单位 —— 「这个单位的全部词条」的边界（零 Qt）。

## 为什么必须先把它在 core 里定死

编辑器的右栏要列出"这个单位牵扯到的每一张表的每一行每一列"。
**边界不定死，界面上的数就会跟着实现漂** —— 上一版设计图印过
「11 张表 · 1144 个格子」，而那是靠 Id 撞号误收算出来的（交接 §5-2 的历史坑）。
所以这条规则只在这里写一次，界面与设计图生成器都读它的结果。

## 规则（四段并集，全部有实测依据）

1. **本体**：`Units#unit_id` 那一行。
2. **拥有行**：**任何**带 `UnitId` 列的表里 `UnitId == unit_id` 的行。
   实测 52085 是 **11 张**（UnitPropulsions / UnitArmors / SensorUnits / UnitAbilities /
   TurretUnits / WeaponAmmunitions / SquadMembers / SquadWeapons / Modifications /
   SpecializationAvailabilities / TransportAvailabilities）。
   不写死这 11 个名字：哪张表带 UnitId 就收哪张 —— 游戏加一张带 UnitId 的表时不用改代码。
3. **随行**（`ATTACHED_EDGES`，只有两条）：结构上**跟着这个单位走**、但自己不带 UnitId 的行：
   - `TurretWeapons`：`TurretId` ∈ 本单位 `TurretUnits.TurretId` 的集合（这个炮塔上挂的武器行）；
   - `Options`：`ModificationId` ∈ 本单位 `Modifications.Id` 的集合（这个改装槽下的选项行）。
4. **模板行**：从 1 + 2 出发，沿 `core/edit/refs.foreign_table` 走 **2 跳**
   （Modifications → 无；UnitPropulsions → Mobility → PlaneFlyPresets；
   TurretUnits → Turrets → Turrets(ParentTurretId)；WeaponAmmunitions → Weapons / Ammunitions …）。

### 分类表不进词条（规则 v2，用户口径）

`REFERENCE_ONLY_TABLES` = **Countries（阵营）· Specializations（师级专精）**：
它们是**分类 / 编制**，不是这个单位的组成部分 —— 用户原话
"阵营、专精不应该出现在单位下，它们跟单位没什么关系，应该放在左上角的军械库旁边"。

所以第 4 段**不走**这两张表。信息没丢：
`Units.CountryId` 那一格仍在本体行里，外键**显示的是国家名**；
`SpecializationAvailabilities.SpecializationId` 同样显示专精名。
只是不再把它们各自摊成一组词条。

### 第 3 段的行**不再往外走**（这条是实测出来的，别改）

试过"从所有已收行继续走反向边"的写法：一个选项的 21 个炮塔槽 → 21 个炮塔 →
它们的武器行 → …，BTR-82A 从 1211 格涨到 **120022 格**，而且把别的单位的选项也卷进来
（那些选项的 `ArmorId` 正好指着同一个装甲模板）。那不是"属于这个单位"，
是"和这个单位用了同一个模板"。所以随行行收进来**只为显示**，不作为继续扩张的起点。

## 实测读数（52085，规则 v2）

| 单位 | 表 | 行 | 格 |
|---|---|---|---|
| BTR-82A（Id 1，载具） | 19 | 63 | 1180 |
| VDV Shturm（Id 645，步兵） | 14 | 37 | 831 |
| 全库 540 个单位最坏（Id 168） | 19 | 178 | 4289 |

（v1 是 21 / 1211，多出来的正是 Countries 与 Specializations 两张分类表。）

全库跑一遍约 1 秒（每个单位 ~2ms），不需要缓存。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.edit.refs import CellRef, foreign_table

#: 拥有行所在的判据列。
UNIT_COLUMN = "UnitId"

#: 表的主键列。
ID_COLUMN = "Id"

#: 模板行的最大跳数。2 跳的理由：`UnitPropulsions → Mobility → PlaneFlyPresets`
#: 与 `TurretUnits → Turrets → Turrets` 都是两跳；再多就会把整个库卷进来
#: （Options 的 21 个炮塔槽是重灾区）。
TEMPLATE_HOPS = 2

#: **分类 / 编制**表：不当作"这个单位的词条"。
#: 用户口径（本轮）："阵营、专精不应该出现在单位下，它们跟单位没什么关系，
#: 应该放在左上角的军械库旁边" —— 军械库的筛选带里就是这两样（国家 chip + 专精下拉）。
#: 信息没丢：Units.CountryId / SpecializationAvailabilities.SpecializationId
#: 仍在各自的行里，外键显示的是**名字**。
REFERENCE_ONLY_TABLES: frozenset[str] = frozenset({"Countries", "Specializations"})


@dataclass(frozen=True, slots=True)
class AttachedEdge:
    """一条「随行」边：`child.COLUMN` 指着父表，父行由本单位**拥有或引用**。"""

    child: str
    column: str
    parent: str


#: 随行边。**只有两条**，都是实测确认过"不带 UnitId、但结构上属于这个单位"的表。
#: 加一条之前先问：这张表的行是"这个单位的一部分"，还是"别人也这么用"？
#: 后者不许加（见模块注释「第 3 段的行不再往外走」）。
ATTACHED_EDGES: tuple[AttachedEdge, ...] = (
    AttachedEdge("TurretWeapons", "TurretId", "Turrets"),
    AttachedEdge("Options", "ModificationId", "Modifications"),
)


@dataclass(frozen=True, slots=True)
class RowRef:
    """一行：哪张表、哪个 Id。"""

    table: str
    row_id: int

    def __str__(self) -> str:
        return f"{self.table}#{self.row_id}"


@dataclass(frozen=True, slots=True)
class UnitScope:
    """一个单位牵扯到的全部行（以及由它展开的格子）。

    `rows` 按 (表名, 行 Id) 排序，**顺序是稳定的** —— 界面按它排词条，
    设计图生成器按它算格子数，两边必须一致。
    """

    unit_id: int
    rows: tuple[RowRef, ...]
    #: 每一格：表 × 行 × 列。长度 = `cell_count`。
    cells: tuple[CellRef, ...] = field(default=())

    @property
    def tables(self) -> tuple[str, ...]:
        """涉及到的表名（去重，按行序首次出现）。"""
        seen: dict[str, None] = {}
        for row in self.rows:
            seen.setdefault(row.table, None)
        return tuple(seen)

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    def rows_of(self, table: str) -> tuple[RowRef, ...]:
        return tuple(row for row in self.rows if row.table == table)

    def row_ids(self, table: str) -> tuple[int, ...]:
        return tuple(row.row_id for row in self.rows if row.table == table)

    def cells_of(self, table: str) -> tuple[CellRef, ...]:
        return tuple(cell for cell in self.cells if cell.table == table)

    def contains(self, table: str, row_id: int) -> bool:
        return RowRef(table, row_id) in self._row_set

    @property
    def _row_set(self) -> frozenset[RowRef]:
        return frozenset(self.rows)


def _index(table: Table) -> tuple[int, dict[int, list]]:
    """(Id 列下标, {Id: 行})。"""
    id_index = table.column_index(ID_COLUMN)
    return id_index, {row[id_index]: row for row in table.rows}


def owned_rows(instance: Instance, unit_id: int) -> dict[str, set[int]]:
    """第 1 + 2 段：本体那一行 + 所有带 UnitId 的表里属于它的行。"""
    owned: dict[str, set[int]] = defaultdict(set)
    try:
        units = instance.table("Units")
    except KeyError:
        return {}
    id_index, by_id = _index(units)
    if unit_id not in by_id:
        return {}
    owned["Units"].add(unit_id)

    for name in instance.table_names:
        if name == "Units":
            continue
        table = instance.table(name)
        if UNIT_COLUMN not in table.columns:
            continue
        unit_index = table.column_index(UNIT_COLUMN)
        id_index, _ = _index(table)
        for row in table.rows:
            if row[unit_index] == unit_id:
                owned[name].add(row[id_index])
    return dict(owned)


def attached_rows(instance: Instance, owned: dict[str, set[int]]) -> dict[str, set[int]]:
    """第 3 段：随行（`ATTACHED_EDGES`）。

    父行的来源有两种，缺一不可：
    - 本单位**拥有**这张父表（Modifications：随行行的 `ModificationId` 就是本单位的槽 Id）；
    - 本单位的某个拥有行**引用**了这张父表（TurretUnits.TurretId → Turrets，
      随行行是挂在这个炮塔上的武器行）。
    """
    out: dict[str, set[int]] = defaultdict(set)
    for edge in ATTACHED_EDGES:
        if edge.child not in instance.table_names:
            continue
        try:
            child = instance.table(edge.child)
        except KeyError:  # pragma: no cover - 表清单刚查过
            continue
        if edge.column not in child.columns:
            continue
        parents: set[int] = set()
        for name, ids in owned.items():
            table = instance.table(name)
            if name == edge.parent:
                parents |= ids
            reference = _column_leading_to(table, edge.parent)
            if reference is None:
                continue
            index = table.column_index(reference)
            rows = {row[table.column_index(ID_COLUMN)]: row for row in table.rows}
            for row_id in ids:
                value = rows.get(row_id, [None] * (index + 1))[index]
                if _positive_int(value):
                    parents.add(value)
        if not parents:
            continue
        column = child.column_index(edge.column)
        id_index = child.column_index(ID_COLUMN)
        for row in child.rows:
            if row[column] in parents:
                out[edge.child].add(row[id_index])
    return dict(out)


def _column_leading_to(table: Table, target: str) -> str | None:
    """这张表上第一列指向 `target` 的外键（没有就 None）。"""
    for name in table.columns:
        if foreign_table(name) == target:
            return name
    return None


def _positive_int(value: object) -> bool:
    """库里 `0` = 「不使用」，不是「指向 Id 0」（关系图 §5 第 1 条）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def template_rows(
    instance: Instance, seeds: dict[str, set[int]], *, hops: int = TEMPLATE_HOPS
) -> dict[str, set[int]]:
    """第 4 段：从种子行出发走 `hops` 跳外键，收到的模板行。"""
    found: dict[str, set[int]] = defaultdict(set)
    frontier: list[tuple[str, int]] = [
        (name, row_id) for name, ids in seeds.items() for row_id in sorted(ids)
    ]
    for _ in range(hops):
        nxt: list[tuple[str, int]] = []
        for name, row_id in frontier:
            if name not in instance.table_names:
                continue
            table = instance.table(name)
            id_index, by_id = _index(table)
            row = by_id.get(row_id)
            if row is None:
                continue
            for position, column in enumerate(table.columns):
                target = foreign_table(column)
                value = row[position]
                if target is None or not _positive_int(value):
                    continue
                if target not in instance.table_names:
                    continue
                if target in REFERENCE_ONLY_TABLES:
                    # 分类 / 编制（阵营、师级专精）不算这个单位的词条 ——
                    # 它们住在军械库左上角的筛选带里。见模块注释「规则 v2」。
                    continue
                if value in seeds.get(target, ()) or value in found[target]:
                    continue
                found[target].add(value)
                nxt.append((target, value))
        if not nxt:
            break
        frontier = nxt
    return dict(found)


def unit_scope(instance: Instance, unit_id: int) -> UnitScope:
    """这个单位牵扯到的全部行与格子。

    单位不存在时返回空 scope（`rows` 为空）—— **不抛异常**：
    界面上"这个单位被删了"应当是空列表，不是一张报错卡。
    """
    owned = owned_rows(instance, unit_id)
    if not owned:
        return UnitScope(unit_id=unit_id, rows=())

    attached = attached_rows(instance, owned)
    templates = template_rows(instance, owned)

    merged: dict[str, set[int]] = defaultdict(set)
    for source in (owned, attached, templates):
        for name, ids in source.items():
            merged[name] |= ids

    rows: list[RowRef] = []
    cells: list[CellRef] = []
    # 排序：先按表名，再按 Id —— 稳定且与界面分组顺序一致。
    for name in sorted(merged):
        if name not in instance.table_names:
            continue
        table = instance.table(name)
        id_index = table.column_index(ID_COLUMN)
        present = {row[id_index] for row in table.rows}
        for row_id in sorted(merged[name] & present):
            rows.append(RowRef(name, row_id))
            cells.extend(CellRef(name, row_id, column) for column in table.columns)
    return UnitScope(unit_id=unit_id, rows=tuple(rows), cells=tuple(cells))


def owner_link_ref(instance: Instance, unit_id: int, table: str, row_id: int) -> CellRef | None:
    """本单位**哪一行指着** (table, row_id) —— 即「另存为新模板」要改指的那一条。

    「另存为新模板」= 复制模板行 + 把本单位的关联行指过去（core/edit/clone.py）。
    少了这一步，复制出来的行没人用。这里回答的就是"那一行是哪一行"。

    ⚠️ **查不到就是查不到（返回 None），不猜**。实测有一条会查不到：
    BTR-82A 的「生命值」落在 Armors#1，而本单位的 UnitArmors 指的是 Armors#167 ——
    Armors#1 是被 **Options#504.ArmorId** 覆盖进来的。那种情况必须用卡片算出来的
    `CardStat.link_ref`（它知道覆盖），而不是这里。

    有 `IsDefault` 列时优先取缺省那一条（一个单位对同一张模板表可以有多行，
    比如两个装甲部位）。
    """
    if table not in instance.table_names:
        return None
    matches: list[tuple[str, int, str]] = []
    for name in instance.table_names:
        if name == "Units":
            continue
        candidate = instance.table(name)
        if UNIT_COLUMN not in candidate.columns:
            continue
        reference = _column_leading_to(candidate, table)
        if reference is None:
            continue
        unit_index = candidate.column_index(UNIT_COLUMN)
        id_index = candidate.column_index(ID_COLUMN)
        foreign_index = candidate.column_index(reference)
        for row in candidate.rows:
            if row[unit_index] == unit_id and row[foreign_index] == row_id:
                matches.append((name, row[id_index], reference))
    if not matches:
        return None
    default = _default_row_id(instance, matches)
    for name, candidate_id, reference in matches:
        if candidate_id == default:
            return CellRef(name, candidate_id, reference)
    name, candidate_id, reference = matches[0]
    return CellRef(name, candidate_id, reference)


def _default_row_id(instance: Instance, matches: list[tuple[str, int, str]]) -> int:
    """有 IsDefault 列时，返回 IsDefault=true 的那一行的 Id；否则返回 -1。"""
    for name, candidate_id, _reference in matches:
        table = instance.table(name)
        if "IsDefault" not in table.columns:
            continue
        flag = table.column_index("IsDefault")
        id_index = table.column_index(ID_COLUMN)
        for row in table.rows:
            if row[id_index] == candidate_id and row[flag] is True:
                return candidate_id
    return -1


def entry_names_in_order(instance: Instance, unit_id: int) -> tuple[str, ...]:
    """单位编辑器右栏的表顺序：本体 → 拥有行 → 随行 → 模板行。

    为什么不按字母序：`Units` 排在最前、模板行排在最后，读起来才是
    "这个单位长什么样 → 它引用了哪些模板"。`unit_scope` 的 `rows` 是按表名排的，
    那是**稳定序**；这里是**阅读序**，两者都用得上，所以分开给。
    """
    scope = unit_scope(instance, unit_id)
    owned = owned_rows(instance, unit_id)
    attached = {edge.child for edge in ATTACHED_EDGES}
    buckets: list[list[str]] = [[], [], [], []]
    for name in scope.tables:
        if name == "Units":
            buckets[0] = [name]
        elif name in owned:
            buckets[1].append(name)
        elif name in attached:
            buckets[2].append(name)
        else:
            buckets[3].append(name)
    return tuple(name for bucket in buckets for name in bucket)


__all__ = [
    "ATTACHED_EDGES",
    "ID_COLUMN",
    "REFERENCE_ONLY_TABLES",
    "TEMPLATE_HOPS",
    "UNIT_COLUMN",
    "AttachedEdge",
    "RowRef",
    "UnitScope",
    "attached_rows",
    "entry_names_in_order",
    "owned_rows",
    "owner_link_ref",
    "template_rows",
    "unit_scope",
]
