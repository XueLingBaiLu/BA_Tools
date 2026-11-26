"""单元格引用：一个数**落在哪**（零 Qt）。

为什么单独一个模块：卡片（app/viewmodel/unit_card.py）与编辑（core/edit/clone.py）
都要用它，而 core 不许 import app（tests/test_core_layering.py 用 AST 扫）。
第一次是写在 unit_card 里的，为了「另存为新模板」才搬下来。

寻址方式是 **(表名, 行 Id, 列名)**，不是行号 —— 插行会让行号整体位移，
而 Id 是 24 张表一致的唯一键（见 core/edit/changeset.py 的模块注释）。

## 外键列名 -> 目标表（本轮新增）

这里是**外键的唯一一张表**（`FOREIGN_TABLE`）：编辑界面要「显示名字、按名字搜」、
「这一格落在哪」、以及 core/edit/scope.py 的「哪些行属于这个单位」，三处都查它。

为什么按**列名**索引而不是 (表, 列)：实测全库 44 个 `*Id` 列里，
**没有一个列名在两处指向不同的表**（`ArmorId` 出现在 Options 与 UnitArmors，
两处都指向 Armors）。所以按列名索引是安全的 —— 而 `tests/test_edit_refs.py`
里有一条用例把这条事实钉死：任何新的 `*Id` 列若没登记，用例就红。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ba_mod_maker.core.asset.tables import resolve_table_name
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.changeset import ID_COLUMN


@dataclass(frozen=True, slots=True)
class CellRef:
    """一个数的落点：哪张表、哪一行（按 Id）、哪一列。

    编辑按这个写（core/edit/changeset.py 就是按 Id 寻址的），
    界面也按它显示「这一格改的是哪儿」。
    """

    table: str
    row_id: int
    column: str

    def __str__(self) -> str:
        return f"{self.table}#{self.row_id}.{self.column}"


#: 自引用 / 不当作外键的列。
#: - `Id`：它自己就是主键；
#: - `UnitId`：反查归属用（scope 第 2 步整表扫），不参与"往外跳"；
#: - `ReplaceUnitId`：指向的是**另一个单位**，不是这个单位的组成部分 ——
#:   走进去会把被替换的那个单位整棵子树卷进来（设计 §12 明确不做）。
NOT_FOREIGN: frozenset[str] = frozenset({"Id", "UnitId", "ReplaceUnitId"})

#: 外键列名 -> 它指向哪张表。`None` = 指向 Units（即 UnitId / ReplaceUnitId，见上）。
FOREIGN_TABLE: dict[str, str] = {
    "UnitId": "Units",
    "ReplaceUnitId": "Units",
    "CountryId": "Countries",
    "ArmorId": "Armors",
    "MobilityId": "Mobility",
    "MainSensorId": "Sensors",
    "ExtraSensorId": "Sensors",
    "SensorId": "Sensors",
    # 表名用**规范名** FlyPresets（条目名 / 磁盘文件才是 PlaneFlyPresets.json）：
    # 这个值会被拿去 instance.table()，写成文件名那一套在两种来源上都取不到表。
    "FlyPresetId": "FlyPresets",
    "AbilityId": "Abilities",
    "Ability1Id": "Abilities",
    "Ability2Id": "Abilities",
    "Ability3Id": "Abilities",
    "SmokeAmmunitionId": "Ammunitions",
    "TurretId": "Turrets",
    "ParentTurretId": "Turrets",
    "WeaponId": "Weapons",
    "PrimaryWeaponId": "Weapons",
    "SpecialWeaponId": "Weapons",
    "AmmunitionId": "Ammunitions",
    "ModificationId": "Modifications",
    "SpecializationId": "Specializations",
    "SpecializationAvailabilityId": "SpecializationAvailabilities",
}

#: `Turret0Id .. Turret20Id` 这 21 个炮塔槽的**列名**（下标 = 槽号 0..20）。
#: 与下面那句 update 是同一份知识的两种用法：那里登记「它们指向 Turrets」，
#: 这里给「要按槽号扫一遍这 21 列」的调用方（单位克隆重指 / 关系树 / 卡片）。
#: ⚠️ **只此一份**：`app/viewmodel/unit_card.py::TURRET_SLOTS` 是它的别名。
TURRET_SLOT_COLUMNS: tuple[str, ...] = tuple(f"Turret{i}Id" for i in range(21))

#: 这 21 个槽也是指向 Turrets 的外键。由代码展开，不手写 21 行 —— 手写就会漏
#: （设计 §4 说 `Turret20Id` 全库为 0，但"全库为 0"不等于"不用登记"）。
FOREIGN_TABLE.update({column: "Turrets" for column in TURRET_SLOT_COLUMNS})


def foreign_table(column: str) -> str | None:
    """这一列指向哪张表；不是外键（或指向 Units）返回 `None`。

    指向 Units 也返回 `None`：调用方要么已经在处理本单位（scope），
    要么是在做"归属"反查，两种都不该再往外跳一跳。
    """
    target = FOREIGN_TABLE.get(column)
    return None if target == "Units" else target


def display_table(column: str) -> str | None:
    """这一列在**界面上**该显示成哪张表的名字、点 ↗ 时跳到哪张表。

    与 `foreign_table()` 只差一处，但那一处很关键：**指向 Units 的那几列**
    （`UnitId` / `ReplaceUnitId`）这里返回 `"Units"`，`foreign_table()` 返回 `None`。

    为什么要分开（用户口径 2026-09-30 实机：「像是替换成另一个单位，这里很明显填的是
    ID……所有 ID，有对应 Name 或 HUDName 的，全部在右边显示，并可以转到那一条」）：

    - **显示 / 跳转**：要名字、要能点过去 —— 看这张原始映射表；
    - **scope 走图**：`unit_scope` 用 `foreign_table()`，对指向 Units 的一律返回 None ——
      否则「替换成另一个单位」会把被替换单位的**整棵子树**卷进本单位（设计 §12 明确不做）。

    共用同一个函数就会顾此失彼，所以拆成两个：改这里不影响 scope。
    """
    return FOREIGN_TABLE.get(column)


def is_foreign(column: str) -> bool:
    """这一列是不是**可跳转的**外键（不含 Id / UnitId / ReplaceUnitId）。"""
    return column not in NOT_FOREIGN and foreign_table(column) is not None


def foreign_columns(columns: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """挑出一张表里全部可跳转的外键列（保持原顺序）。"""
    return tuple(name for name in columns if is_foreign(name))


# --------------------------------------------------------------------------- #
# 查找引用：谁指着我（旧产品有、我们没有）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RefHit:
    """一条「谁指着我」的记录（数据表页底部那个只读面板按它渲染一行）。

    - `table` / `row_id`：**引用者**是哪一行（不是被引用的那一行）；
    - `column`：引用者身上那个外键列（`ArmorId` / `UnitId` / `Turret0Id` …）；
    - `label`：那一行的人话标签（`core/edit/foreign.py::row_label`）——
      没有名字列的关联行会按外键拼一句，例如「单位 BTR-82 · 装甲 Armor BTR80 Base」。
    """

    table: str
    row_id: int
    column: str
    label: str


def foreign_columns_to(table: str) -> tuple[str, ...]:
    """**全部**指向 `table` 的外键列名（保持 `FOREIGN_TABLE` 的声明顺序）。

    ⚠️ 与 `foreign_columns()` 不是一回事：那个是「一张表里能往外跳的列」，
    这个是「谁指向我」。指向 `Units` 的 `UnitId` / `ReplaceUnitId` 在这里**要算** ——
    `foreign_table()` 把它们挡掉是为了 scope 不往外走图；而「谁引用了这个单位」
    恰恰就是靠这两列回答的（一个单位的那些关联行全都靠 `UnitId` 认爹）。
    """
    return tuple(name for name, target in FOREIGN_TABLE.items() if target == table)


def referencing_rows(instance: Instance, table: str, row_id: int) -> tuple[RefHit, ...]:
    """谁引用了 `(table, row_id)`：扫一遍 24 张表里所有指向它的外键列。

    判据（`tests/test_edit_foreign.py` 有用例）：

    1. 命中行的那一列**真的**等于 `row_id`（用例会回读校验，不许编）；
    2. 只有 `FOREIGN_TABLE` 里登记为指向这张表的列才算命中 —— 不新造第二张映射表；
    3. `0` / `None` / bool 一律不算：外键里 `0` 表示「不使用」（全库约定），
       把它当命中会让"选中任意一行"都冒出成百上千条噪声；
    4. 顺序确定：表按 `instance.table_names` 的顺序、行按表内顺序、列按列序。

    代价：24 张表 / 最大 2357 行 —— 全表扫是允许的（这是"选中一行"时的一次点击）。
    """
    if not isinstance(row_id, int) or isinstance(row_id, bool) or row_id == 0:
        return ()
    available = instance.table_names
    # 表名要按**这份数据源**的叫法归一：目录来源把 FlyPresets 那张叫 PlaneFlyPresets
    # （见 core/asset/tables.py 的表名别名），两个名字都得认。
    target = resolve_table_name(table, available)
    if target is None:
        return ()
    wanted = {
        column
        for column, ref_table in FOREIGN_TABLE.items()
        if resolve_table_name(ref_table, available) == target
    }
    if not wanted:
        return ()

    # 延迟导入：foreign.py 反过来 import 本模块的 FOREIGN_TABLE（模块级），
    # 这里放模块级就会成环。
    from ba_mod_maker.core.edit.foreign import row_label

    hits: list[RefHit] = []
    for name in available:
        source = instance.table(name)
        if ID_COLUMN not in source.columns:
            continue
        hit_columns = [
            (column, source.column_index(column)) for column in source.columns if column in wanted
        ]
        if not hit_columns:
            continue
        id_index = source.column_index(ID_COLUMN)
        for row in source.rows:
            for column, index in hit_columns:
                value = row[index]
                if not isinstance(value, int) or isinstance(value, bool) or value != row_id:
                    continue
                source_id = row[id_index]
                hits.append(
                    RefHit(
                        table=name,
                        row_id=source_id,
                        column=column,
                        label=row_label(instance, name, source_id),
                    )
                )
    return tuple(hits)


# --------------------------------------------------------------------------- #
# 牵连闭包：删一行会**连带谁**（级联删除与孤儿清单共用同一把尺子）
# --------------------------------------------------------------------------- #

#: 牵连闭包的规模上限。**超了就抛错，⛔ 不静默截断** ——
#: 被截断的闭包会让人以为「只有这么多」，而级联少删（留孤儿）与多删（吃别人的数据）
#: 都是数据损坏，且**没有任何症状**。
CLOSURE_LIMIT = 20_000


def reference_closure(
    instance: Instance,
    roots: Iterable[tuple[str, int]],
    *,
    limit: int = CLOSURE_LIMIT,
) -> tuple[tuple[str, int], ...]:
    """从点名的这几行出发，**传递地**收集会被牵连的行（广度优先；**不含** roots 自己）。

    「牵连」的定义只有一句：**它指着的那一行正在被删** ⇒ 它就成孤儿。
    级联删除要的正是这个集合：删掉 A，指着 A 的 B 成孤儿；B 没了，指着 B 的 C 也成孤儿……
    一直到底。所以这是 [referencing_rows] 在图上的一趟 BFS，**没有第二套判据** ——
    哪些列算外键、`0`/`None`/bool 不算命中，全部由 [referencing_rows] 那一处说了算。

    为什么单独一个函数（而不是让调用方自己循环）：上一轮就是用
    **「整数值命中新增 Id」**算的闭包，只匹配到 6 行、明显是巧合 —— 那个量法不认**列**，
    任何等于该 Id 的整数都算命中，跟「谁引用它」根本无关（kb/37 记的就是这条废尺子）。
    闭包只能有一个算法。

    顺序确定：层序（先点名的直接引用者，再它们的引用者……），同层内按
    [referencing_rows] 的顺序（表按数据源顺序、行按表内顺序、列按列序）。
    环安全：已入队的坐标不再入队（数据里确有成环的可能，例如 A 指 B、B 又指 A）。

    代价：每个节点一次 [referencing_rows]（一次全库扫）。实测 Units#3（BMP-3）
    的闭包 38 个节点、亚秒级；大闭包由 `limit` 兜底。
    """
    seen: set[tuple[str, int]] = set()
    queue: list[tuple[str, int]] = []
    for table, row_id in roots:
        key = (table, row_id)
        if key not in seen:
            seen.add(key)
            queue.append(key)
    out: list[tuple[str, int]] = []
    head = 0
    while head < len(queue):
        table, row_id = queue[head]
        head += 1
        for hit in referencing_rows(instance, table, row_id):
            key = (hit.table, hit.row_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            queue.append(key)
            if len(out) > limit:
                raise ValueError(
                    f"牵连闭包超过 {limit} 行就停了：这份数据里删一行会连带太多行，"
                    "多半是选错了行（或数据本身成环成片）。请改小范围重来。"
                )
    return tuple(out)


def orphan_rows(
    instance: Instance,
    doomed: Iterable[tuple[str, int]],
) -> tuple[RefHit, ...]:
    """删掉 `doomed` 这几行之后**会变成孤儿的行** = **直接**指向它们的行（去重、保序）。

    与 [reference_closure] 的分工（两者都要，别混）：

    - 那个答「**级联要一起删哪些**」—— `--cascade` 用；
    - 这个答「`--force` 明知有引用也删时，库里会留下**哪些没人指向的行**」。

    只算**一层**：孙辈指的是它们的爹，爹还在 ⇒ 孤立不到孙辈。
    """
    targets = {(table, row_id) for table, row_id in doomed}
    seen: set[tuple[str, int, str]] = set()
    out: list[RefHit] = []
    for table, row_id in sorted(targets):
        for hit in referencing_rows(instance, table, row_id):
            key = (hit.table, hit.row_id, hit.column)
            if key in seen:
                continue
            seen.add(key)
            out.append(hit)
    return tuple(out)


__all__ = [
    "CLOSURE_LIMIT",
    "FOREIGN_TABLE",
    "NOT_FOREIGN",
    "CellRef",
    "RefHit",
    "display_table",
    "foreign_columns",
    "foreign_columns_to",
    "foreign_table",
    "is_foreign",
    "orphan_rows",
    "reference_closure",
    "referencing_rows",
]
