"""「另存为新模板」：复制一行、本单位指过去、别人不动（零 Qt）。

用户定的第 1 条：共享行**可改**，但必须能另存为新模板 —— 实测（设计 §6）
645 的传感器被 **142** 个单位共用、机动 79 个、弹药 32 个，改一个数就是一大片。

做法三步，全部走变更集（core/edit/changeset.py）：

1. 复制 `value_ref` 那一行，给一个新 Id（`Changeset.insert_row`）；
2. 把本单位那条关联行（`link_ref`，如 `UnitArmors#1185.ArmorId`）指到新行；
3. 别人用的还是原来那一行 —— **一点没动**。

为什么要有 `link_ref`：卡片上的数在共享行里（`Armors`），但"谁用它"在关联行里
（`UnitArmors`）。少了关联行这一步，复制出来的行没人引用，等于白复制。
"""

from __future__ import annotations

from dataclasses import dataclass

from ba_mod_maker.core.data.dataset import Table
from ba_mod_maker.core.edit.changeset import (
    ID_COLUMN,
    Change,
    Changeset,
    RowChange,
    RowId,
)
from ba_mod_maker.core.edit.refs import TURRET_SLOT_COLUMNS, CellRef
from ba_mod_maker.core.errors import TableError


def next_row_id(table: Table, changeset: Changeset | None = None) -> int:
    """该表下一个没用到的 Id = 「现有最大 + 1」与「本会话记过的最大 Id + 1」取大者。

    为什么不用"行数 + 1"：Id 不连续（删过行、手写过），撞上就覆盖别人的数据。

    为什么还要 changeset（这一版修的坑）：**删掉最大 Id 那一行**之后，表里就
    "没有那个 Id"了，于是"现有最大 + 1"正好捡回**刚删掉的那个 Id** ——
    而 `Changeset.insert_row` 的守卫看的是**表里**有没有同 Id 的行，表里确实没有，
    所以守卫也拦不住。可撤销那条删除会把旧行按原 Id 放回来，于是**两行数据抢一个 Id**。
    所以把本会话的行级记录（删除 + 插入）里的最大 Id 也计入：删过的 Id 不再复用。

    `changeset` 省略 = 只看表里现有的（旧行为，向后兼容）：调用方手上没有会话时
    （如探针 / 只读展示）仍然能用，但**写路径一律要传**。
    """
    biggest = _max_row_id(table)
    if changeset is not None:
        biggest = max(biggest, _max_recorded_row_id(changeset, table.name))
    return biggest + 1


def _max_row_id(table: Table) -> int:
    """表里现有行的最大 Id（非整数 / bool 一律不算）。"""
    index = table.column_index(ID_COLUMN)
    biggest = 0
    for row in table.rows:
        value = row[index]
        if isinstance(value, int) and not isinstance(value, bool):
            biggest = max(biggest, value)
    return biggest


def _max_recorded_row_id(changeset: Changeset, table_name: str) -> int:
    """本会话在这张表上**记过**的最大 Id（删除记录 + 插入记录）。

    ⚠️ 只算**当前还挂着**的记录：删了又原样加回来 = 什么也没发生
    （两条记录会互相抵消，见 `Changeset.insert_row` / `delete_row`），
    那种 Id 复用是安全的 —— 表里那一行还在，行数对得上。
    """
    biggest = 0
    for record in changeset.row_changes:
        if record.table != table_name:
            continue
        if isinstance(record.row_id, int) and not isinstance(record.row_id, bool):
            biggest = max(biggest, record.row_id)
    return biggest


def clone_row(changeset: Changeset, ref: CellRef, *, new_id: int | None = None) -> int:
    """把 `ref` 指的那一行复制成新的一行，返回新 Id。

    只动内存；落盘仍走 core/session（三道闸在那儿）。
    """
    table = changeset.table(ref.table)
    row = changeset.row_of(ref.table, ref.row_id)
    values = {column: table.rows[row][i] for i, column in enumerate(table.columns)}
    # ❗ 传 changeset：删过最大 Id 的行之后，光看表会**捡回刚删的 Id**（见 next_row_id）。
    fresh = next_row_id(table, changeset) if new_id is None else new_id
    values[ID_COLUMN] = fresh
    return changeset.insert_row(ref.table, values)


def save_as_new_template(
    changeset: Changeset, value_ref: CellRef, link_ref: CellRef | None
) -> tuple[RowId, RowChange, Change | None]:
    """另存为新模板：复制 `value_ref` 那一行，并把 `link_ref` 指到新行。

    返回 (新行 Id, 插行改动, 改关联行的改动)。`link_ref` 为 None 时抛 TableError ——
    没有关联行的值本来就是"这一格自己的"，直接改即可，复制出来没人用。
    """
    if link_ref is None:
        raise TableError(
            f"{value_ref} 没有关联行，不需要另存为新模板",
            hint="这一格是本单位独占的（或算出来的），直接改就行",
            table=value_ref.table,
        )
    new_id = clone_row(changeset, value_ref)
    link_change = changeset.set_value(link_ref.table, link_ref.row_id, link_ref.column, new_id)
    row_change = next(
        change
        for change in changeset.row_changes
        if change.table == value_ref.table and change.row_id == new_id
    )
    return new_id, row_change, link_change


def add_row(changeset: Changeset, table_name: str, values: dict) -> RowId:
    """往关联表里**加一行**（加枪 / 换弹种 / 5 人改 6 人）。

    Id 不给就取 `next_row_id(table, changeset)`（本会话删过的 Id 不复用）。
    返回值是可用在别处的新 Id。
    """
    table = changeset.table(table_name)
    payload = dict(values)
    # ❗ 用 is None 判，不用 setdefault：拼行的人往往会把 Id 列**显式写成 None**
    #   （列集合来自表本身），setdefault 见到键已存在就不会补 —— 实测踩过。
    if payload.get(ID_COLUMN) is None:
        payload[ID_COLUMN] = next_row_id(table, changeset)
    return changeset.insert_row(table_name, payload)


# --------------------------------------------------------------------------- #
# 整单位克隆（用户 2026-10-01：「迫切需要」，整套搬走）
# --------------------------------------------------------------------------- #

#: 克隆要整套搬走的表 —— 判据只有一条：这张表带着 `UnitId`（行属于某个单位）。
#: 实测 24 张表里正好 11 张（DataBaseCompiled/52085 全表扫过）。
#: ⚠️ 老工具（`旧知识包/Database_Editor_Old/ba_db_tool.py::clone_unit`）漏了
#: `TransportAvailabilities` 与 `SpecializationAvailabilities` 两张 —— 克隆出来的单位
#: 会少掉运输配对与专精可用性。这里按「带 UnitId 就属于单位」收全。
UNIT_OWNED_TABLES: tuple[str, ...] = (
    "UnitArmors",
    "UnitPropulsions",
    "SensorUnits",
    "UnitAbilities",
    "SpecializationAvailabilities",
    "TransportAvailabilities",
    "TurretUnits",
    "WeaponAmmunitions",
    "Modifications",
    "SquadMembers",
    "SquadWeapons",
)

#: 克隆时**不复制、仍共享**的模板行。与老工具口径一致（它的 docstring 原话：
#: Weapons are shared (not copied)）—— 想「只改我这一台」时走卡上的「📄 克隆一份」
#: （`save_as_new_template`）。
#: `Turrets` / `TurretWeapons` **不在**这一列：它们是共享模板，但用户点名要整套克隆
#: （「炮塔、以及改装里的炮塔指向」）—— 实测 174/1055 座炮塔被不止一个单位引用、
#: 最多的一座被 83 个单位用，不克隆就等于「改新单位的炮塔会动到别人的车」。
SHARED_TEMPLATE_TABLES: tuple[str, ...] = (
    "Weapons",
    "Ammunitions",
    "Armors",
    "Mobility",
    "Sensors",
    "Abilities",
    "FlyPresets",
    "Countries",
    "Specializations",
)


@dataclass(frozen=True, slots=True)
class CloneResult:
    """整单位克隆的结果：新单位 Id + 这次插进去的每一行 + 每张表插了几行。

    `rows` 是给界面用的：一次克隆要算**一条**撤销（`EditorContext._remember(("rows", ...))`），
    否则用户得按三十几次 Ctrl+Z 才退得回去。
    """

    unit_id: int
    rows: tuple[RowChange, ...]
    counts: tuple[tuple[str, int], ...]

    def summary(self) -> str:
        """一句给状态栏的话。"""
        parts = "、".join(f"{table} {n}" for table, n in self.counts if n)
        return f"已克隆为 Units#{self.unit_id}：新插 {len(self.rows)} 行（{parts}）"


def _snapshot(changeset: Changeset, table_name: str) -> list[dict]:
    """整表快照（列 -> 值）。**先拍后写**：插行会改表，边遍历边插会漏行或炸。"""
    table = changeset.table(table_name)
    return [dict(zip(table.columns, row, strict=False)) for row in table.rows]


def _rows_of_unit(rows: list[dict], unit_id: int) -> list[dict]:
    return [row for row in rows if row.get("UnitId") == unit_id]


def _row_change_of(changeset: Changeset, table_name: str, row_id: RowId) -> RowChange:
    for record in reversed(changeset.row_changes):
        if record.table == table_name and record.row_id == row_id:
            return record
    raise TableError(  # pragma: no cover - insert_row 一定记了账
        f"{table_name}#{row_id} 刚插进去却没找到账目", table=table_name
    )


def clone_unit(changeset: Changeset, unit_id: int) -> CloneResult:
    """把一个单位**整套**克隆成新单位（新 Id）；返回新单位 Id 与插入的每一行。

    复制的范围：11 张带 `UnitId` 的自有表（`UNIT_OWNED_TABLES`）+ 这个单位引用到的
    炮塔链（`Turrets` + 它们的 `TurretWeapons`）+ 每个改装槽底下的 `Options`。
    模板行（`SHARED_TEMPLATE_TABLES`）**不复制**，新单位仍指着库里原来那一行。

    **重指**是这一步的正事（老工具漏了两处，正是用户点名要的）：

    | 指向 | 重指成 |
    |---|---|
    | 11 张自有表的 `UnitId` | 新单位 Id |
    | `TurretUnits.TurretId` | 克隆出来的那座炮塔 |
    | `TurretWeapons.TurretId` | 同上（`WeaponId` **不重指** —— 武器共享） |
    | `Turrets.ParentTurretId` | 克隆出来的父炮塔（子母炮塔；老工具漏了） |
    | `Options.ModificationId` | 克隆出来的那个改装槽 |
    | `Options.Turret0Id..Turret20Id` | 克隆出来的炮塔（**用户点名要的**；老工具漏了） |

    ❗ `Options.ReplaceUnitId` **不重指**：它指向的是**另一个单位**（「换单位开」），
    克隆时把那个单位整棵子树卷进来是错的（`core/edit/refs.py` 的 FOREIGN_TABLE 注释里
    写着同一条口径）。
    """
    units = _snapshot(changeset, "Units")
    source = next((row for row in units if row.get(ID_COLUMN) == unit_id), None)
    if source is None:
        raise TableError(f"Units#{unit_id} 不存在，克隆不了", table="Units")

    # ---- 第一步：只读快照（插行会改表，先拍完再写） ------------------------ #
    owned = {name: _rows_of_unit(_snapshot(changeset, name), unit_id) for name in UNIT_OWNED_TABLES}
    mod_ids = [row[ID_COLUMN] for row in owned["Modifications"]]
    options = _snapshot(changeset, "Options")
    options_by_mod: dict[int, list[dict]] = {
        mid: [row for row in options if row.get("ModificationId") == mid] for mid in mod_ids
    }

    # 这个单位引用到的炮塔：TurretUnits 的候选名单 + 各选项的 21 个槽
    needed: list[int] = []

    def _need(value: object) -> None:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            and value not in needed
        ):
            needed.append(value)

    for row in owned["TurretUnits"]:
        _need(row.get("TurretId"))
    for rows_of_mod in options_by_mod.values():
        for row in rows_of_mod:
            for column in TURRET_SLOT_COLUMNS:
                _need(row.get(column))

    turret_rows = {row[ID_COLUMN]: row for row in _snapshot(changeset, "Turrets")}
    turret_weapons = _snapshot(changeset, "TurretWeapons")

    # ---- 第二步：开写 ------------------------------------------------------ #
    rows: list[RowChange] = []
    counts: dict[str, int] = {}

    def insert(table_name: str, values: dict) -> int:
        payload = dict(values)
        payload[ID_COLUMN] = None  # add_row 见 None 就取 next_row_id（本会话删过的 Id 不复用）
        new_id = add_row(changeset, table_name, payload)
        rows.append(_row_change_of(changeset, table_name, new_id))
        counts[table_name] = counts.get(table_name, 0) + 1
        return new_id

    # 0) 新单位先占 Id —— 后面所有关联行都要指它
    new_unit = dict(source)
    new_unit.pop(ID_COLUMN, None)
    new_unit_id = insert("Units", new_unit)

    # 1) 炮塔链（含父炮塔；有了 map 才能重指 TurretUnits / TurretWeapons / Options）
    turret_map: dict[int, int] = {}
    in_progress: set[int] = set()

    def clone_turret(old_id: int) -> int:
        if old_id in turret_map:
            return turret_map[old_id]
        if old_id in in_progress:  # 父炮塔成环：停止递归，保持原值
            return old_id
        values = turret_rows.get(old_id)
        if values is None:
            return old_id
        in_progress.add(old_id)
        parent = values.get("ParentTurretId")
        # ❗ 这里那个**尾逗号**曾经把 new_parent 变成单元素元组（(None,) / (1417,)）：
        #    写进包就是 JSON 数组 "ParentTurretId": [null] —— 游戏读 Turrets 时当场
        #    JsonReaderException（实测 Gamelog 21_56：Path '[1055].ParentTurretId'），
        #    DataBaseService 起不来 ⇒ **整个军械库空白**（用户 2026-10-02 报的就是它）。
        #    ⛔ 别再加括号/逗号：这一列在库里是**标量或 null**（实测 965 null / 90 int，没有数组）。
        new_parent = (
            clone_turret(parent)
            if isinstance(parent, int) and not isinstance(parent, bool) and parent > 0
            else parent
        )
        new_id = insert("Turrets", {**values, "ParentTurretId": new_parent})
        turret_map[old_id] = new_id
        in_progress.discard(old_id)
        for mount in turret_weapons:
            if mount.get("TurretId") == old_id:
                insert("TurretWeapons", {**mount, "TurretId": new_id})
        return new_id

    for turret_id in needed:
        clone_turret(turret_id)

    # 2) 11 张自有表：逐行复制、UnitId 指新单位
    mod_map: dict[int, int] = {}
    for table_name in UNIT_OWNED_TABLES:
        for row in owned[table_name]:
            payload = {**row, "UnitId": new_unit_id}
            if table_name == "TurretUnits":
                payload["TurretId"] = turret_map.get(row.get("TurretId"), row.get("TurretId"))
            new_id = insert(table_name, payload)
            if table_name == "Modifications":
                mod_map[row[ID_COLUMN]] = new_id

    # 3) 每个克隆出来的改装槽 -> 它底下的选项（Turret 指向也一起重指）
    for old_mod_id, mod_options in options_by_mod.items():
        new_mod_id = mod_map.get(old_mod_id)
        if new_mod_id is None:  # pragma: no cover - mod_map 覆盖了每一个槽
            continue
        for option in mod_options:
            payload = {**option, "ModificationId": new_mod_id}
            for column in TURRET_SLOT_COLUMNS:
                payload[column] = turret_map.get(option.get(column), option.get(column))
            insert("Options", payload)

    return CloneResult(
        unit_id=new_unit_id,
        rows=tuple(rows),
        counts=tuple(counts.items()),
    )


__all__ = [
    "SHARED_TEMPLATE_TABLES",
    "UNIT_OWNED_TABLES",
    "CloneResult",
    "add_row",
    "clone_row",
    "clone_unit",
    "next_row_id",
    "save_as_new_template",
]
