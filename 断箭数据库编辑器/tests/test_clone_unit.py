"""整单位克隆（core/edit/clone.py::clone_unit）—— 用户 2026-10-01：「迫切需要」。

钉四件事：

1. **11 张带 `UnitId` 的自有表整套复制**，新行的 `UnitId` 全指新单位；
2. **炮塔链一起复制**（`Turrets` + `TurretWeapons`），而且 **`Options` 的 21 个炮塔槽
   重指到克隆出来的炮塔** —— 老工具（`旧知识包/Database_Editor_Old/ba_db_tool.py::clone_unit`）
   漏了这一步，用户点名要它；`Turrets.ParentTurretId` 同样重指；
3. **模板行不复制**（Weapons / Ammunitions / Armors / Mobility / Sensors / Abilities），
   行数一行不变 —— 与老工具口径一致（Weapons are shared）；
4. **撤得回去**：`CloneResult.rows` 就是这次动作插的每一行，全撤之后表回到原样。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.clone import (
    SHARED_TEMPLATE_TABLES,
    UNIT_OWNED_TABLES,
    clone_unit,
)
from ba_mod_maker.core.edit.refs import TURRET_SLOT_COLUMNS

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: BT-3F（单位 212）—— 有 5 个炮塔候选、2 个改装槽、3 行携弹；实测样本。
UNIT = 212


@pytest.fixture
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


def _rows(instance, table: str) -> list[dict]:
    target = instance.table(table)
    return [dict(zip(target.columns, row, strict=False)) for row in target.rows]


def _of_unit(instance, table: str, unit_id: int) -> list[dict]:
    return [row for row in _rows(instance, table) if row.get("UnitId") == unit_id]


def _options_of(instance, mod_ids: list[int]) -> list[dict]:
    return [row for row in _rows(instance, "Options") if row.get("ModificationId") in mod_ids]


def test_clone_copies_every_unit_owned_table(instance) -> None:
    """11 张自有表一套不落，新行的 UnitId 全指新单位。"""
    assert len(UNIT_OWNED_TABLES) == 11
    changeset = Changeset(instance)
    before = {name: len(_of_unit(instance, name, UNIT)) for name in UNIT_OWNED_TABLES}
    assert sum(before.values()) > 0, "样本单位一行自有行都没有？换一个样本"

    result = clone_unit(changeset, UNIT)

    assert result.unit_id != UNIT
    for name in UNIT_OWNED_TABLES:
        after = _of_unit(instance, name, result.unit_id)
        assert len(after) == before[name], (name, before[name], len(after))
        assert all(row["UnitId"] == result.unit_id for row in after)
    for name in UNIT_OWNED_TABLES:
        assert len(_of_unit(instance, name, UNIT)) == before[name]


def test_clone_remaps_the_turret_pointers_inside_the_cloned_options(instance) -> None:
    """⭐ 用户点名要的那一步：改装选项里的炮塔指向要指到**克隆出来的**炮塔。

    老工具漏了它 —— 克隆出来的单位的改装槽还指着原来那座共享炮塔，
    于是「改新单位的炮塔」等于改别人的车。
    """
    changeset = Changeset(instance)
    old_mods = [row["Id"] for row in _of_unit(instance, "Modifications", UNIT)]
    old_options = _options_of(instance, old_mods)
    old_turrets = {row["Id"] for row in _of_unit(instance, "TurretUnits", UNIT)}
    old_slots = [
        row[column] for row in old_options for column in TURRET_SLOT_COLUMNS if row.get(column)
    ]
    assert old_slots, "样本单位没有「选项里带炮塔指向」的数据，这条用例就白测了"
    turrets_before = {row["Id"] for row in _rows(instance, "Turrets")}

    result = clone_unit(changeset, UNIT)

    # 新炮塔 = 克隆之后多出来的那些（克隆前拍过照，不靠猜阈值）
    fresh_turrets = {row["Id"] for row in _rows(instance, "Turrets")} - turrets_before
    assert fresh_turrets, "克隆没复制任何炮塔？"
    new_mods = [row["Id"] for row in _of_unit(instance, "Modifications", result.unit_id)]
    new_options = _options_of(instance, new_mods)
    assert len(new_options) == len(old_options)
    seen = []
    for row in new_options:
        for column in TURRET_SLOT_COLUMNS:
            value = row.get(column)
            if value:
                seen.append(value)
                assert value not in old_slots, f"{column} 还指着原来那座炮塔 {value}"
                assert value in fresh_turrets, f"{column}={value} 不是克隆出来的炮塔"
    assert seen, "克隆出来的选项一个炮塔都没指？"

    new_links = _of_unit(instance, "TurretUnits", result.unit_id)
    assert {row["TurretId"] for row in new_links} <= fresh_turrets
    assert not ({row["TurretId"] for row in new_links} & old_turrets)


def test_clone_keeps_the_template_rows_shared(instance) -> None:
    """模板行一行都不复制（武器 / 弹药 / 装甲…共享）—— 与老工具口径一致。"""
    changeset = Changeset(instance)
    before = {name: len(_rows(instance, name)) for name in SHARED_TEMPLATE_TABLES}
    clone_unit(changeset, UNIT)
    after = {name: len(_rows(instance, name)) for name in SHARED_TEMPLATE_TABLES}
    assert after == before, {k: (before[k], after[k]) for k in before if before[k] != after[k]}


def test_clone_leaves_the_source_unit_alone(instance) -> None:
    """原件一个字节都不许动。"""
    changeset = Changeset(instance)
    mods = [row["Id"] for row in _of_unit(instance, "Modifications", UNIT)]
    before_unit = next(row for row in _rows(instance, "Units") if row["Id"] == UNIT)
    before_options = _options_of(instance, mods)
    before_links = _of_unit(instance, "TurretUnits", UNIT)

    clone_unit(changeset, UNIT)

    assert next(row for row in _rows(instance, "Units") if row["Id"] == UNIT) == before_unit
    assert _options_of(instance, mods) == before_options
    assert _of_unit(instance, "TurretUnits", UNIT) == before_links


#: Ka-52（单位 46）：克隆它会把 11 座炮塔模板一起复制 —— 那条事故路径的样本。
FLYING_UNIT = 46


def test_a_cloned_turret_never_writes_a_list_into_parent_turret_id(instance) -> None:
    """⭐ 2026-10-02 实机事故：克隆出来的炮塔把 ParentTurretId 写成了 **JSON 数组** [null]。

    根因：clone_unit 里那句
        new_parent = (clone_turret(parent) if … else parent,)
    的**尾逗号**把值变成了单元素元组（(None,) / (1417,)）⇒ 落盘就是 [null] / [1417]。
    游戏读 Turrets 时当场 JsonReaderException（用户机器 Gamelog 21_56：
    Path 1055.ParentTurretId）⇒ DataBaseService 起不来 ⇒ **整个军械库空白**。

    判据：克隆插的每一行 Turrets，ParentTurretId 都必须是**标量或 None**（库里实测 965 null /
    90 int，一个数组都没有）。
    """
    changeset = Changeset(instance)
    result = clone_unit(changeset, FLYING_UNIT)

    table = changeset.table("Turrets")
    column = table.columns.index("ParentTurretId")
    cloned = [row for row in result.rows if row.table == "Turrets"]
    assert cloned, "样本变了：克隆这架飞机该连炮塔模板一起复制"
    for record in cloned:
        value = table.rows[changeset.row_of("Turrets", record.row_id)][column]
        assert not isinstance(value, (list, tuple)), (
            f"Turrets#{record.row_id} 的 ParentTurretId 写成了 {value!r} —— 落盘就是 JSON 数组，",
            "游戏读包会当场炸（军械库整片空白）",
        )


def test_two_clones_get_two_ids(instance) -> None:
    """连着克隆两次不许撞 Id（next_row_id 要看得见本会话插过的行）。"""
    changeset = Changeset(instance)
    first = clone_unit(changeset, UNIT)
    second = clone_unit(changeset, UNIT)
    assert first.unit_id != second.unit_id
    for name in ("Modifications", "TurretUnits", "WeaponAmmunitions"):
        a = {row["Id"] for row in _of_unit(instance, name, first.unit_id)}
        b = {row["Id"] for row in _of_unit(instance, name, second.unit_id)}
        assert not (a & b), name


def test_every_cloned_row_can_be_undone_in_one_batch(instance) -> None:
    """`CloneResult.rows` 就是「这次动作插的每一行」：全撤之后表回到原样。

    界面照 `EditorContext._remember(("rows", result.rows))` 把它记成**一条**撤销，
    所以这里钉的是「这批账目确实覆盖了每一行」。
    """
    changeset = Changeset(instance)
    tables = ("Units", *UNIT_OWNED_TABLES, "Turrets", "TurretWeapons", "Options")
    before = {name: len(_rows(instance, name)) for name in tables}

    result = clone_unit(changeset, UNIT)

    assert {row.table for row in result.rows} <= set(before)
    after = {name: len(_rows(instance, name)) for name in tables}
    assert after != before, "克隆居然一行都没插？"

    for record in result.rows:
        changeset.revert_row(record)
    restored = {name: len(_rows(instance, name)) for name in tables}
    assert restored == before, {
        k: (before[k], restored[k]) for k in before if before[k] != restored[k]
    }
