"""变更集（`core/edit/changeset.py`）的测试。

全部内存构造，不碰游戏文件、不碰 Qt。

这里钉住的是**四条决定**，不是实现细节：

1. 旧值是**首次**改动前的值（`55 → 60 → 70` 的「改前」恒为 55）；
2. 改回原值时条目**整条消失**（不留 "55 → 55" 这种空条目）；
3. 按 `Id` 寻址，所以**插行之后旧改动不错位**（这条是它存在的全部理由）；
4. 一处单元格 = 一条改动，跨表能汇总成「共 N 行 M 张表」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.core.data.dataset import Dataset, Instance, Table
from ba_mod_maker.core.data.schema import TableSchema
from ba_mod_maker.core.edit.changeset import HISTORY_LIMIT, Changeset, unit_ids_from
from ba_mod_maker.core.errors import TableError

_UNITS = ("Id", "Name", "Cost")
_AMMO = ("Id", "UnitId", "Quantity")


def _table(name: str, columns: tuple[str, ...], rows: list[list]) -> Table:
    return Table(
        name=name,
        columns=columns,
        rows=rows,
        source_columns=columns,
        source_path=Path(f"{name}.json"),
    )


def _instance() -> Instance:
    """两行 Units + 两行 WeaponAmmunitions 的最小实例。

    `Units` 的 Id 刻意取 1 / 2（连续），`WeaponAmmunitions` 取 512 / 513
    —— 后者用来证明"键不是行号"：Id 512 落在第 0 行。
    """
    inst = Instance(
        path_id="52085",
        directory=Path("."),
        schema={
            "Units": TableSchema(name="Units", columns=_UNITS),
            "WeaponAmmunitions": TableSchema(name="WeaponAmmunitions", columns=_AMMO),
        },
        manifest={},
    )
    inst._tables["Units"] = _table("Units", _UNITS, [[1, "BTR-82", 55], [2, "BTR-80", 40]])
    inst._tables["WeaponAmmunitions"] = _table(
        "WeaponAmmunitions", _AMMO, [[512, 1, 30], [513, 2, 12]]
    )
    return inst


def _dataset(inst: Instance) -> Dataset:
    return Dataset(root=Path("."), schema=inst.schema, instances={"52085": inst}, instance_roles={})


# --------------------------------------------------------------------------- #
# 1. 旧值语义
# --------------------------------------------------------------------------- #


def test_set_value_writes_through_and_records_the_old_value() -> None:
    cs = Changeset(_instance())
    change = cs.set_value("Units", 1, "Cost", 60)

    assert change is not None
    assert (change.old, change.new) == (55, 60)
    assert change.table == "Units" and change.row_id == 1 and change.column == "Cost"
    # 值真的落到了表上，不是只记了个账
    assert cs.value("Units", 1, "Cost") == 60


def test_the_second_edit_keeps_the_first_old_value() -> None:
    """`55 → 60 → 70` 的「改前」必须是 55，不能跟着漂成 60。

    漂了的话，界面上的「改前」会随用户多改几次而变化 ——
    用户看到的是"我相对于**最初**改了多少"，不是"相对于上一次"。
    """
    cs = Changeset(_instance())
    cs.set_value("Units", 1, "Cost", 60)
    cs.set_value("Units", 1, "Cost", 70)

    changes = cs.changes
    assert len(changes) == 1, "同一处单元格只应有一条改动"
    assert (changes[0].old, changes[0].new) == (55, 70)


def test_editing_back_to_the_original_removes_the_entry() -> None:
    """改回原值 = 没有改动。留一条 `55 → 55` 会让「变更清单」说假话。"""
    cs = Changeset(_instance())
    cs.set_value("Units", 1, "Cost", 60)
    assert cs.set_value("Units", 1, "Cost", 55) is None
    assert cs.changes == ()
    assert cs.is_dirty("Units", 1, "Cost") is False
    assert cs.value("Units", 1, "Cost") == 55


def test_writing_the_same_value_is_not_a_change() -> None:
    cs = Changeset(_instance())
    assert cs.set_value("Units", 1, "Cost", 55) is None
    assert cs.is_empty


def test_zero_is_a_real_value_not_a_missing_one() -> None:
    """`0` 是合法值（外键里 0 = 不使用），不能被当成"没改"。"""
    cs = Changeset(_instance())
    change = cs.set_value("Units", 2, "Cost", 0)
    assert change is not None and change.new == 0
    assert cs.count == 1


# --------------------------------------------------------------------------- #
# 2. 撤销
# --------------------------------------------------------------------------- #


def test_revert_restores_the_original_and_drops_the_entry() -> None:
    cs = Changeset(_instance())
    change = cs.set_value("Units", 1, "Cost", 60)
    assert change is not None
    cs.revert(change)

    assert cs.value("Units", 1, "Cost") == 55
    assert cs.is_empty


def test_revert_all_restores_every_touched_cell() -> None:
    inst = _instance()
    cs = Changeset(inst)
    cs.set_value("Units", 1, "Cost", 60)
    cs.set_value("WeaponAmmunitions", 512, "Quantity", 45)
    assert cs.total_rows() == 2

    cs.revert_all()

    assert cs.is_empty
    assert cs.value("Units", 1, "Cost") == 55
    assert cs.value("WeaponAmmunitions", 512, "Quantity") == 30


def test_revert_all_does_not_reload_from_disk() -> None:
    """撤销走的是"写回旧值"，不是 `Instance.reload_table`（磁盘上根本没有这些文件）。

    这条守住"撤销不需要能读盘"：通过了就说明它没去碰磁盘。
    """
    inst = _instance()
    cs = Changeset(inst)
    cs.set_value("Units", 1, "Name", "BTR-82M")
    cs.revert_all()
    assert cs.value("Units", 1, "Name") == "BTR-82"
    # 表对象还是原来那个（没被 reload_table 换掉）
    assert inst.table("Units").row_count == 2


# --------------------------------------------------------------------------- #
# 2.5 重做：撤销完还能放回来（Ctrl+Y）
# --------------------------------------------------------------------------- #


def test_undo_then_redo_brings_the_value_back() -> None:
    """撤销 -> 重做：值回来，条目也回到待保存清单里（它又「脏」了）。"""
    cs = Changeset(_instance())
    change = cs.set_value("Units", 1, "Cost", 60)
    assert change is not None
    assert cs.can_redo is False, "还没撤销过，重做栈是空的"

    cs.revert(change)
    assert cs.value("Units", 1, "Cost") == 55
    assert cs.can_redo is True

    back = cs.redo()
    assert back is change
    assert cs.value("Units", 1, "Cost") == 60
    assert cs.changes == (change,), "重做过的那条要回到待保存清单里"
    assert cs.can_redo is False, "栈里只有一条，重做完就空了"


def test_redo_with_nothing_to_redo_is_harmless() -> None:
    """没有可重做的返回 None（界面据此写一句"没有可重做的"）。"""
    cs = Changeset(_instance())
    assert cs.can_redo is False
    assert cs.redo() is None


def test_a_new_edit_clears_the_redo_stack() -> None:
    """任何新的写操作都清空重做栈 —— 历史一分叉，重做就会把刚改的顶掉。"""
    cs = Changeset(_instance())
    change = cs.set_value("Units", 1, "Cost", 60)
    assert change is not None
    cs.revert(change)
    assert cs.can_redo is True

    cs.set_value("Units", 2, "Cost", 41)  # 新的写操作

    assert cs.can_redo is False
    assert cs.redo() is None
    assert cs.value("Units", 1, "Cost") == 55, "被撤销的那条不许被重做顶回来"


def test_undo_all_then_redo_puts_the_edits_back_one_by_one() -> None:
    """撤销全部之后能一条条重做（后进先出），全部回来后计数与原来一致。"""
    inst = _instance()
    cs = Changeset(inst)
    cs.set_value("Units", 1, "Cost", 60)
    cs.set_value("WeaponAmmunitions", 512, "Quantity", 45)
    cs.revert_all()
    assert cs.is_empty and cs.can_redo

    assert cs.redo() is not None
    assert cs.redo() is not None

    assert cs.can_redo is False
    assert cs.value("Units", 1, "Cost") == 60
    assert cs.value("WeaponAmmunitions", 512, "Quantity") == 45
    assert cs.total_rows() == 2


def test_redo_puts_a_deleted_row_back_and_takes_it_away_again() -> None:
    """行级：删掉 -> 撤销（放回来）-> 重做（再删掉），记录也回到清单里。"""
    cs = Changeset(_instance())
    before = len(cs.table("Units").rows)
    change = cs.delete_row("Units", 2)
    cs.revert_row(change)
    assert len(cs.table("Units").rows) == before

    again = cs.redo()
    assert again == change
    assert len(cs.table("Units").rows) == before - 1
    assert cs.row_changes == (change,)
    assert cs.can_redo is False


def test_redo_removes_an_inserted_row_again() -> None:
    """行级：插进去 -> 撤销（拿掉）-> 重做（再插回来），值也在。"""
    cs = Changeset(_instance())
    before = len(cs.table("Units").rows)
    cs.insert_row("Units", {"Id": 3, "Name": "新的", "Cost": 10})
    inserted = cs.row_changes[0]
    cs.revert_row(inserted)
    assert len(cs.table("Units").rows) == before

    assert cs.redo() == inserted

    assert len(cs.table("Units").rows) == before + 1
    assert cs.value("Units", 3, "Name") == "新的"


def test_commit_clears_the_redo_stack() -> None:
    """commit（写盘成功「认下来」）之后，重做栈也该清 —— 那段历史已经不成立了。"""
    cs = Changeset(_instance())
    change = cs.set_value("Units", 1, "Cost", 60)
    assert change is not None
    cs.revert(change)

    cs.commit()

    assert cs.can_redo is False
    assert cs.redo() is None


def _instance_with_rows(count: int) -> Instance:
    """一张有 count 行的 Units —— 上限那条用例要凑够 100+ 条**不同**的改动。"""
    inst = Instance(
        path_id="52085",
        directory=Path("."),
        schema={"Units": TableSchema(name="Units", columns=_UNITS)},
        manifest={},
    )
    inst._tables["Units"] = _table(
        "Units", _UNITS, [[row_id, f"U{row_id}", row_id] for row_id in range(1, count + 1)]
    )
    return inst


def test_the_redo_stack_stops_at_a_hundred() -> None:
    """栈上限 100（用户口径 Ctrl+Z / Ctrl+Y）：超了丢最旧的，内存不会无限涨。

    ⚠️ 要用**互不相同**的格子：同一条改动 revert 之后，下一次 set_value 会清空重做栈
    （新的写操作），所以"改了又撤、撤了又改"永远只有 1 条。
    """
    total = HISTORY_LIMIT + 5
    cs = Changeset(_instance_with_rows(total))
    for row_id in range(1, total + 1):
        assert cs.set_value("Units", row_id, "Cost", 1000 + row_id) is not None

    for change in tuple(cs.changes):  # 一次撤完 -> 每条都进重做栈
        cs.revert(change)

    redone = 0
    while cs.redo() is not None:
        redone += 1

    assert redone == HISTORY_LIMIT, "重做栈上限是 100"


# --------------------------------------------------------------------------- #
# 3. 寻址不受插行影响（这条是变更集存在的全部理由）
# --------------------------------------------------------------------------- #


def test_addressing_survives_row_insertion() -> None:
    """在表头插入一行之后，之前记下的改动**必须仍然指向原行**。

    这正是行号制做不到的事：原本第 0 行的 Id=1，插一行后它变成第 1 行，
    而 `(行号, 列)` 记的还是 0 —— 撤销时会把值写进新插入的那一行。
    """
    inst = _instance()
    cs = Changeset(inst)
    change = cs.set_value("Units", 1, "Cost", 60)
    assert change is not None

    # 模拟克隆新增：往表头插一行（最坏情况，所有行号都位移）
    inst.table("Units").rows.insert(0, [900, "新单位", 10])
    cs.invalidate("Units")

    assert cs.value("Units", 1, "Cost") == 60, "插行后取值应当仍落在 Id=1 那一行"
    assert cs.row_of("Units", 1) == 1

    cs.revert(change)
    assert inst.table("Units").rows[1][2] == 55, "旧值要写回 Id=1 那一行"
    assert inst.table("Units").rows[0][2] == 10, "新插入的那一行不许被动到"


def test_invalidate_actually_rebuilds_the_index() -> None:
    """不调 `invalidate()` 就会拿到过期的行号 —— 这条把契约写清楚。"""
    inst = _instance()
    cs = Changeset(inst)
    assert cs.row_of("Units", 2) == 1

    inst.table("Units").rows.insert(0, [900, "新单位", 10])
    assert cs.row_of("Units", 2) == 1, "没 invalidate 就是旧索引（已知且刻意）"

    cs.invalidate("Units")
    assert cs.row_of("Units", 2) == 2


# --------------------------------------------------------------------------- #
# 4. 跨表汇总（「这次改动会写到哪」）
# --------------------------------------------------------------------------- #


def test_changed_tables_and_rows_are_deduplicated_and_ordered() -> None:
    cs = Changeset(_instance())
    cs.set_value("Units", 1, "Cost", 60)
    cs.set_value("Units", 1, "Name", "BTR-82M")  # 同一行第二列
    cs.set_value("WeaponAmmunitions", 512, "Quantity", 45)

    assert cs.count == 3, "三处单元格 = 三条改动"
    assert cs.changed_tables() == ("Units", "WeaponAmmunitions")
    assert cs.changed_rows("Units") == (1,), "同一行的两列只算一行"
    assert cs.total_rows() == 2, "设计稿的「共 2 行 · 2 张表」"


def test_changes_keep_the_order_they_were_made_in() -> None:
    """「变更清单」按发生顺序展示，不按字典序。"""
    cs = Changeset(_instance())
    cs.set_value("WeaponAmmunitions", 512, "Quantity", 45)
    cs.set_value("Units", 1, "Cost", 60)
    assert [c.table for c in cs.changes] == ["WeaponAmmunitions", "Units"]


def test_unit_ids_from_only_picks_the_units_table() -> None:
    """左栏只给"Units 表本身被改过"的单位打角标。

    改一个弹药数量动的是 `WeaponAmmunitions`，不是 `Units`；
    跨表反查属于关系图（设计稿的「写到哪」），不塞进这个函数。
    """
    cs = Changeset(_instance())
    cs.set_value("Units", 1, "Cost", 60)
    cs.set_value("WeaponAmmunitions", 512, "Quantity", 45)

    assert unit_ids_from(cs.changes) == frozenset({1})


# --------------------------------------------------------------------------- #
# 5. 订阅与错误
# --------------------------------------------------------------------------- #


def test_subscribe_fires_on_every_mutation_and_unsubscribes() -> None:
    cs = Changeset(_instance())
    hits: list[int] = []
    off = cs.subscribe(lambda: hits.append(1))

    cs.set_value("Units", 1, "Cost", 60)
    cs.set_value("Units", 1, "Cost", 70)
    change = cs.changes[0]
    cs.revert(change)
    assert len(hits) == 3

    off()
    cs.set_value("Units", 2, "Cost", 41)
    assert len(hits) == 3, "退订之后不该再收到"


def test_missing_row_id_raises_a_user_facing_error() -> None:
    cs = Changeset(_instance())
    with pytest.raises(TableError) as info:
        cs.set_value("Units", 999, "Cost", 1)
    assert "999" in str(info.value)
    assert info.value.hint, "必须带 hint，界面要直接展示"


def test_unknown_column_raises_a_user_facing_error() -> None:
    cs = Changeset(_instance())
    with pytest.raises(TableError) as info:
        cs.set_value("Units", 1, "NoSuchColumn", 1)
    assert "NoSuchColumn" in str(info.value)


def test_two_instances_do_not_share_state() -> None:
    """两个实例各有一份变更集 —— 这样"换数据源就要重建变更集"是显式的。"""
    a = Changeset(_instance())
    b = Changeset(_instance())
    a.set_value("Units", 1, "Cost", 60)
    assert b.is_empty


# --------------------------------------------------------------------------- #
# 6. 行级改动：新增 / 删除（卡片要能加枪、换弹种、5 人改 6 人）
# --------------------------------------------------------------------------- #


def test_insert_row_appends_and_records_a_row_change() -> None:
    """插一行要有 Id（业务键），并且记成 RowChange —— 不是"一列一条改动"。"""
    cs = Changeset(_instance())
    row_id = cs.insert_row("WeaponAmmunitions", {"Id": 514, "UnitId": 1, "Quantity": 7})

    assert row_id == 514
    assert cs.row_changes[0].is_insert
    assert cs.row_changes[0].row_id == 514
    assert cs.row_changes[0].table == "WeaponAmmunitions"
    assert cs.changes == (), "插行不该产生列级改动"
    assert cs.count == 1 and not cs.is_empty
    assert cs.changed_tables() == ("WeaponAmmunitions",)
    assert cs.changed_rows("WeaponAmmunitions") == (514,)
    # 缺的列填 None，不炸
    assert cs.value("WeaponAmmunitions", 514, "Quantity") == 7


def test_insert_row_refuses_a_taken_id() -> None:
    """不覆盖已有 Id —— 那会把别人的数据吃掉。"""
    cs = Changeset(_instance())
    with pytest.raises(TableError):
        cs.insert_row("Units", {"Id": 1, "Name": "撞车", "Cost": 1})


def test_insert_row_needs_an_integer_id() -> None:
    cs = Changeset(_instance())
    with pytest.raises(TableError):
        cs.insert_row("Units", {"Name": "没有 Id"})


def test_insert_does_not_break_addressing_by_id() -> None:
    """插行会位移行号，但寻址按 Id —— 所以旧改动不会错位（本模块存在的理由）。"""
    cs = Changeset(_instance())
    cs.set_value("WeaponAmmunitions", 513, "Quantity", 99)  # 第 1 行
    cs.insert_row("WeaponAmmunitions", {"Id": 514, "UnitId": 2, "Quantity": 1})
    assert cs.value("WeaponAmmunitions", 513, "Quantity") == 99
    assert cs.value("WeaponAmmunitions", 514, "Quantity") == 1
    cs.revert_all()
    assert cs.value("WeaponAmmunitions", 513, "Quantity") == 12


def test_delete_row_absorbs_column_changes_on_that_row() -> None:
    """删掉的行上的列级改动会消失 —— 否则撤销时会往一个不存在的行写值。"""
    cs = Changeset(_instance())
    cs.set_value("Units", 2, "Cost", 45)
    assert cs.count == 1

    change = cs.delete_row("Units", 2)
    assert change.kind == "delete"
    assert change.value_of("Name") == "BTR-80"
    assert cs.changes == (), "行没了，它上面的列改动也该没了"
    assert cs.count == 1 and cs.row_changes == (change,)


def test_revert_row_puts_a_deleted_row_back() -> None:
    cs = Changeset(_instance())
    before = len(cs.table("Units").rows)
    change = cs.delete_row("Units", 2)
    assert len(cs.table("Units").rows) == before - 1

    cs.revert_row(change)
    assert len(cs.table("Units").rows) == before
    assert cs.is_empty
    assert cs.value("Units", 2, "Name") == "BTR-80"


def test_revert_row_removes_an_inserted_row() -> None:
    cs = Changeset(_instance())
    before = len(cs.table("Units").rows)
    cs.insert_row("Units", {"Id": 3, "Name": "新的", "Cost": 10})
    assert len(cs.table("Units").rows) == before + 1

    cs.revert_all()
    assert len(cs.table("Units").rows) == before
    assert cs.is_empty


def test_revert_all_handles_columns_and_rows_together() -> None:
    """撤销全部：先还原列、再撤行 —— 顺序反了会往已被拿掉的行写值。"""
    cs = Changeset(_instance())
    cs.set_value("Units", 1, "Cost", 60)
    cs.insert_row("Units", {"Id": 3, "Name": "新的", "Cost": 10})
    cs.delete_row("Units", 2)
    assert cs.count == 3

    cs.revert_all()
    assert cs.is_empty
    assert cs.value("Units", 1, "Cost") == 55
    assert cs.value("Units", 2, "Name") == "BTR-80"
    assert len(cs.table("Units").rows) == 2


def test_insert_then_delete_leaves_no_trace() -> None:
    """插一行又删掉 = 什么也没发生。

    实踩：5 人改 6 人、又改回 5 人（删掉刚加的那行），撤销全部时它**又回来了**
    —— 因为插入记录被删除记录顶掉，撤销看到的是「删除」，就把行加了回去。
    """
    cs = Changeset(_instance())
    before = len(cs.table("Units").rows)
    cs.insert_row("Units", {"Id": 3, "Name": "新的", "Cost": 10})
    cs.delete_row("Units", 3)

    assert cs.row_changes == (), "净效果为零，不该留下任何行级改动"
    assert cs.is_empty
    cs.revert_all()
    assert len(cs.table("Units").rows) == before


def test_delete_then_reinsert_the_same_row_cancels_out() -> None:
    """删一行、再把一模一样的行加回来 = 什么也没发生（撤销不该把它抹掉）。"""
    cs = Changeset(_instance())
    before = len(cs.table("Units").rows)
    cs.delete_row("Units", 2)
    cs.insert_row("Units", {"Id": 2, "Name": "BTR-80", "Cost": 40})

    assert cs.row_changes == ()
    assert cs.is_empty
    cs.revert_all()
    assert len(cs.table("Units").rows) == before
    assert cs.value("Units", 2, "Name") == "BTR-80"


def test_delete_then_reinsert_different_values_refuses() -> None:
    """同 Id 但值不一样：那是另一行数据，不能悄悄顶掉原来的删除记录。"""
    cs = Changeset(_instance())
    cs.delete_row("Units", 2)
    with pytest.raises(TableError):
        cs.insert_row("Units", {"Id": 2, "Name": "换了个名字", "Cost": 40})


def test_commit_clears_entries_without_reverting() -> None:
    """commit() = 把改动**认下来**：条目清空，但值仍是改过的那个。

    单位编辑器本轮加的：改动是直接写进 Table.rows 的，保存成功之后若不 commit，
    再点保存会把同一批改动写第二遍（还说"有 N 张表改动"）；
    而离开编辑器时按"未保存就还原"处理，又会把刚保存成功的东西撤回去。
    """
    cs = Changeset(_instance())
    cs.set_value("Units", 1, "Cost", 4242)
    assert cs.count == 1

    cs.commit()
    assert cs.is_empty, "commit 之后不该还有条目"
    assert cs.changed_tables() == ()
    assert cs.value("Units", 1, "Cost") == 4242, "commit 是认下来，不是还原"

    # 认下来之后，能不能再改？能 —— 新的基数是 commit 那一刻的值。
    cs.set_value("Units", 1, "Cost", 4243)
    assert cs.old_value("Units", 1, "Cost") == 4242
    cs.revert_all()
    assert cs.value("Units", 1, "Cost") == 4242


def test_insert_row_fills_missing_columns_with_type_zero_not_none() -> None:
    """新增行缺列时：**全表没有 None 的列**要给零值，不能留 None。

    事故（2026-11-03 实测，真机闪退）：`insert_row` 原来一律填 None，于是新增的
    `SpecializationAvailabilities#1232` 把 `MaxAvailabilityXp0..3` 写成了 JSON null；
    游戏侧 `dump.cs:53804` 那四个字段是**不可空 int** ⇒ 存档进 `BA_Mod.zip` 后
    游戏在首个场景加载期间抛托管异常闪退（0xE06D7363 / KERNELBASE）。

    判据：`null_in_nonnullable_columns` 4 → 0（同一张官方表的 338 行里这四列零个 null）。
    """
    columns = ("Id", "SpecializationId", "UnitId", "MaxAvailabilityXp0", "Note")
    rows = [[6, 1, 10, 100, None], [7, 2, 11, 200, None]]
    name = "SpecializationAvailabilities"
    table = _table(name, columns, rows)
    inst = Instance(
        path_id="52739",
        directory=Path("."),
        schema={name: TableSchema(name=name, columns=columns)},
        manifest={},
    )
    inst._tables[name] = table

    cs = Changeset(inst)
    cs.insert_row(name, {"Id": 1232, "SpecializationId": 3, "UnitId": 180})

    new_row = table.rows[-1]
    assert new_row[3] == 0, "全表没有 None 的 int 列必须补 0（补 null 会让游戏闪退）"
    assert new_row[4] is None, "本来就可空的列保持 None —— 别替我填空串"
