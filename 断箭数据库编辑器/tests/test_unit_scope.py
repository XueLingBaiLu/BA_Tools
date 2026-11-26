"""「哪些行属于这个单位」的守卫（零 Qt，用真实 DataBaseCompiled）。

这条规则是单位编辑器右栏的**边界**：图上的"19 张表 · 997 个格子"、
界面上的分组、专用页能点到什么，全都读它。所以它必须在 core 里定死，
而且**用例把边界的两头都钉住**：

- 该收的收（炮塔挂载行、改装槽下的选项行、两跳外的飞行包线）；
- 不该收的不收（ReplaceUnitId 指向的**另一个单位**、别的单位的关联行）。
"""

from __future__ import annotations

import time

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.refs import FOREIGN_TABLE, foreign_table
from ba_mod_maker.core.edit.scope import (
    ATTACHED_EDGES,
    entry_names_in_order,
    owned_rows,
    unit_scope,
)

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


# --------------------------------------------------------------------------- #
# 边界（实测读数）
# --------------------------------------------------------------------------- #


def test_btr82a_scope_matches_the_measured_numbers(instance) -> None:
    """BTR-82A（Id 1）= 19 张表 / 61 行 / 1180 格。

    这三个数是本轮**实测**的边界读数（规则 v1），不是从旧图抄的。
    改边界必须同时改这条用例与 docs/设计-单位编辑器-双击进入.md。
    """
    scope = unit_scope(instance, 1)
    assert (scope.table_count, scope.row_count, scope.cell_count) == (19, 61, 1180)


def test_infantry_scope_is_smaller_and_has_squad_rows(instance) -> None:
    """步兵（645）没有炮塔那一支，但多出班组成员 / 班用武器。"""
    scope = unit_scope(instance, 645)
    tables = scope.tables
    assert "SquadMembers" in tables
    assert "SquadWeapons" in tables
    assert "TurretUnits" not in tables
    assert scope.cell_count < 1180


def test_scope_only_contains_rows_that_exist(instance) -> None:
    """收到的每一行都真的在表里 —— 防"按 Id 撞号误收"那个历史坑。"""
    for ref in unit_scope(instance, 1).rows:
        table = instance.table(ref.table)
        ids = {row[table.column_index("Id")] for row in table.rows}
        assert ref.row_id in ids, ref


def test_reference_tables_are_not_part_of_the_units_entries(instance) -> None:
    """阵营 / 师级专精是**分类**，不是这个单位的词条（规则 v2，用户口径：
    "阵营、专精不应该出现在单位下，它们跟单位没什么关系，应该放在左上角的军械库旁边"）。

    信息没丢：Units.CountryId 与 SpecializationAvailabilities.SpecializationId
    仍各自在行里，外键显示的是名字（见 test_unit_cells 的外键用例）。
    """
    scope = unit_scope(instance, 1)
    assert "Countries" not in scope.tables
    assert "Specializations" not in scope.tables
    # 但"专精可用性"是本单位自己的关联行（带 UnitId），要留
    assert "SpecializationAvailabilities" in scope.tables


def test_owned_rows_are_the_eleven_unit_tables(instance) -> None:
    """第 2 段的判据是**列**不是表名清单：带 UnitId 的表全收。"""
    owned = owned_rows(instance, 1)
    with_unit_id = {
        name for name in instance.table_names if "UnitId" in instance.table(name).columns
    }
    assert set(owned) - {"Units"} == with_unit_id & set(owned)
    assert len(with_unit_id) == 11, f"实测带 UnitId 的表是 11 张，现在是 {len(with_unit_id)}"


def test_replace_unit_id_is_not_followed(instance) -> None:
    """Options.ReplaceUnitId 指向**别的单位**，走进去会把它整棵子树卷进来。

    BTR-82A 的 Options#501 有 ReplaceUnitId；那个单位不该出现在 scope 里。
    """
    options = instance.table("Options")
    column = options.column_index("ReplaceUnitId")
    replaced = {
        row[column] for row in options.rows if isinstance(row[column], int) and row[column] > 0
    }
    assert replaced, "样本单位没有 ReplaceUnitId，这条用例就测不到东西"

    scope_ids = {ref.row_id for ref in unit_scope(instance, 1).rows_of("Units")}
    assert scope_ids == {1}
    assert not (scope_ids & replaced)


def test_attached_rows_come_from_the_two_registered_edges(instance) -> None:
    """随行只有两张表：TurretWeapons（炮塔上的武器行）与 Options（槽下的选项行）。"""
    assert {edge.child for edge in ATTACHED_EDGES} == {"TurretWeapons", "Options"}
    scope = unit_scope(instance, 1)
    assert scope.row_ids("TurretWeapons"), "炮塔挂载行没收到"
    assert scope.row_ids("Options"), "改装选项行没收到"


def test_attached_edges_stay_in_the_scope(instance) -> None:
    """本单位的炮塔上挂的武器行，一行不少。"""
    # ❗ unit_scope 是全库扫（实测约 2.5s/次，见 test_scope_is_cheap_enough_for_every_unit），
    #    这里只要一份 —— 之前调了两遍，白等 2.5s。
    scope = unit_scope(instance, 1)

    turret_units = instance.table("TurretUnits")
    tu_id = turret_units.column_index("Id")
    tu_turret = turret_units.column_index("TurretId")
    mine = {
        row[tu_turret]
        for row in turret_units.rows
        if row[tu_id] in set(scope.row_ids("TurretUnits"))
    }
    turret_weapons = instance.table("TurretWeapons")
    tw_id = turret_weapons.column_index("Id")
    tw_turret = turret_weapons.column_index("TurretId")
    want = {row[tw_id] for row in turret_weapons.rows if row[tw_turret] in mine}
    assert want
    assert want <= set(scope.row_ids("TurretWeapons"))


def test_templates_are_two_hops_away(instance) -> None:
    """两跳：UnitPropulsions -> Mobility -> FlyPresets（飞机才有）。"""
    assert "Mobility" in unit_scope(instance, 1).tables
    assert "Armors" in unit_scope(instance, 1).tables
    assert "Weapons" in unit_scope(instance, 1).tables
    # 机动模板的飞行包线：只有飞机用得上，但规则本身要能走到（选一架飞机）。
    aircraft = _first_unit_of_type(instance, 16)
    assert "FlyPresets" in unit_scope(instance, aircraft).tables


def test_missing_unit_is_an_empty_scope_not_an_error(instance) -> None:
    """单位不存在 -> 空 scope。界面上"这个单位被删了"应当是空列表，不是报错卡。"""
    scope = unit_scope(instance, 999999)
    assert scope.rows == ()
    assert scope.cell_count == 0
    assert scope.tables == ()


def test_cells_are_every_column_of_every_row(instance) -> None:
    """格子数 = 每张表「行数 × 列数」之和（不是"表数 × 列数"）。"""
    scope = unit_scope(instance, 1)
    want = sum(
        len(instance.table(name).columns) * len(scope.row_ids(name)) for name in scope.tables
    )
    assert scope.cell_count == want


def test_entry_order_puts_the_unit_first_and_templates_last(instance) -> None:
    """右栏的表顺序：本体 -> 拥有行 -> 随行 -> 模板行。"""
    order = entry_names_in_order(instance, 1)
    assert order[0] == "Units"
    attached = {edge.child for edge in ATTACHED_EDGES}
    positions = {name: index for index, name in enumerate(order)}
    owned = owned_rows(instance, 1)
    for name in order:
        if name in ("Units",) or name not in positions:
            continue
        if name in attached:
            assert positions[name] > max(
                positions[other] for other in owned if other in positions and other != "Units"
            ), name
        elif name not in owned:
            assert positions[name] > max(
                positions[other] for other in attached if other in positions
            ), name
    assert set(order) == set(unit_scope(instance, 1).tables)


# --------------------------------------------------------------------------- #
# 外键表不许有洞
# --------------------------------------------------------------------------- #


def test_every_foreign_column_is_registered(instance) -> None:
    """全库每一个 *Id 列都在 FOREIGN_TABLE 里（除 Id / UnitId / ReplaceUnitId）。

    **这条是防漂移的**：游戏加了新外键列而我们没登记，
    scope 就会静默地少走一支（界面上一整张表凭空消失），
    而没有任何一处会报错。这里让它当场红。
    """
    unregistered: list[str] = []
    dataset = load_dataset(DATASET_ROOT)
    for inst_id in dataset.instance_ids:
        other = dataset.instance(inst_id)
        for name in other.table_names:
            for column in other.table(name).columns:
                if not column.endswith("Id") or column in ("Id", "UnitId", "ReplaceUnitId"):
                    continue
                if column not in FOREIGN_TABLE:
                    unregistered.append(f"{name}.{column}")
    assert not unregistered, f"这些外键列没登记到 FOREIGN_TABLE：{sorted(set(unregistered))}"


def test_foreign_targets_exist_in_the_dataset(instance) -> None:
    """登记的每一张目标表都真的存在（写错表名 = scope 静默少一支）。"""
    missing = sorted({t for t in FOREIGN_TABLE.values() if t not in instance.table_names})
    assert not missing, missing


def test_units_targets_are_not_followed() -> None:
    """指向 Units 的列一律不算"可跳转外键"（往别处跳会把整库卷进来）。"""
    assert foreign_table("UnitId") is None
    assert foreign_table("ReplaceUnitId") is None
    assert foreign_table("ArmorId") == "Armors"


def test_scope_is_cheap_enough_for_every_unit(instance) -> None:
    """540 个单位全算一遍要够快（界面进编辑器时现算）。"""
    units = instance.table("Units")
    id_index = units.column_index("Id")
    start = time.perf_counter()
    for row in units.rows:
        unit_scope(instance, row[id_index])
    assert time.perf_counter() - start < 6.0


def _first_unit_of_type(instance, type_bit: int) -> int:
    """找第一个 Type 掩码里含 type_bit 的单位。"""
    units = instance.table("Units")
    id_index = units.column_index("Id")
    type_index = units.column_index("Type")
    for row in units.rows:
        value = row[type_index]
        if isinstance(value, int) and not isinstance(value, bool) and value & type_bit:
            return row[id_index]
    raise AssertionError(f"库里没有 Type&{type_bit} 的单位")
