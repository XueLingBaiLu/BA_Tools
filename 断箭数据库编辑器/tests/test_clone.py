"""「另存为新模板」与增删行的测试（零 Qt，用真实 DataBaseCompiled）。

这一组用例要证明的是**用户定的第 1 条真的成立**：

    共享行可改，但改了之后**别人不动**。

所以每条都在真实数据上做，并且**同时**看两个单位：改 645 的装甲，
另一个用同一个装甲模板的单位必须一点没变。

每个用例自己 load 一份数据集（Instance 的表是内存里的，改了就地改）——
互相之间不会串。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card, card_weight
from ba_mod_maker.core.assets.localization import Localization
from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.clone import add_row, clone_row, next_row_id, save_as_new_template
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import TableError

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"


@pytest.fixture()
def instance():
    """每个用例一份干净实例：变更集是**就地改内存**的。"""
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


@pytest.fixture()
def data(instance):
    return CardData(instance, Localization(entries={}))


def _stat(card, key: str) -> str:
    return next(stat.value for stat in card.stats if stat.key == key)


def test_next_row_id_does_not_collide(instance) -> None:
    table = instance.table("Armors")
    index = table.column_index("Id")
    fresh = next_row_id(table)
    assert fresh > max(row[index] for row in table.rows)


def test_save_as_new_template_leaves_others_alone(instance, data) -> None:
    """645 的装甲另存为新模板：只有 645 换到新行，原来那一行原封不动。"""
    changeset = Changeset(instance)
    armor = data.armor(645)
    armor_id = data.t("Armors").i(armor, "Id")
    before = data.t("Armors").i(armor, "ArmorValue")
    users_before = data.sharing.units("Armors", armor_id)
    assert len(users_before) > 1, "样本必须是共享行，否则这条用例没有意义"

    other_unit = next(uid for uid in sorted(users_before) if uid != 645)
    other_armor_before = data.t("Armors").i(data.armor(other_unit), "Id")

    new_id, row_change, link_change = save_as_new_template(
        changeset,
        CellRef("Armors", armor_id, "ArmorValue"),
        CellRef("UnitArmors", 1185, "ArmorId"),
    )
    assert row_change.is_insert and row_change.row_id == new_id
    assert link_change is not None and link_change.new == new_id

    # 改新模板的值：645 变了……
    changeset.set_value("Armors", new_id, "ArmorValue", 99)
    data.invalidate()  # 改过表之后必须丢缓存（产品里由变更集的订阅做）
    assert _stat(build_unit_card(data, 645), "armor") == "99"
    # ……而原来那一行、以及用它的另一个单位，一点没动
    assert data.t("Armors").i(data.t("Armors").by_id(armor_id), "ArmorValue") == before
    assert data.t("Armors").i(data.armor(other_unit), "Id") == other_armor_before
    assert _stat(build_unit_card(data, other_unit), "armor") == str(before)


def test_revert_all_puts_the_shared_row_back(instance, data) -> None:
    """撤销全部：新行没了、关联行指回原处、卡片数字回到 10。"""
    changeset = Changeset(instance)
    before_len = len(instance.table("Armors").rows)
    save_as_new_template(
        changeset,
        CellRef("Armors", 281, "ArmorValue"),
        CellRef("UnitArmors", 1185, "ArmorId"),
    )
    assert len(instance.table("Armors").rows) == before_len + 1

    changeset.revert_all()
    data.invalidate()
    assert changeset.is_empty
    assert len(instance.table("Armors").rows) == before_len
    assert data.t("UnitArmors").i(data.t("UnitArmors").by_id(1185), "ArmorId") == 281
    assert _stat(build_unit_card(data, 645), "armor") == "10"


def test_adding_a_squad_member_changes_the_card(instance, data) -> None:
    """5 人改 6 人：卡片上的人数变 6、重量跟着变 750（125 × 6）。

    这是"增删行"那条需求的落点 —— 数值行不是读一行，是**沿关系图算**出来的。
    """
    assert _stat(build_unit_card(data, 645), "soldier_count") == "5"
    assert card_weight(data, 645) == (625, True)

    changeset = Changeset(instance)
    add_row(
        changeset,
        "SquadMembers",
        {"UnitId": 645, "PrimaryWeaponId": 122, "SpecialWeaponId": 0, "DeathPriority": 0},
    )
    data.invalidate()
    card = build_unit_card(data, 645)
    assert _stat(card, "soldier_count") == "6"
    assert _stat(card, "weight") == "750"
    # 武器数组也跟着多一把（按人收集的）
    assert len(build_unit_card(data, 645).weapons[0].weapons) == 6

    changeset.revert_all()
    data.invalidate()
    assert _stat(build_unit_card(data, 645), "soldier_count") == "5"


def test_insert_refuses_an_occupied_id(instance) -> None:
    """插行不许覆盖已有 Id —— 那会把别人的数据吃掉。"""
    changeset = Changeset(instance)
    with pytest.raises(TableError):
        changeset.insert_row("Armors", {"Id": 281, "ArmorValue": 1})


def test_delete_row_keeps_the_old_row_for_undo(instance, data) -> None:
    """删行：旧值整行留着，撤销时放回去。"""
    changeset = Changeset(instance)
    before = len(instance.table("SquadMembers").rows)
    change = changeset.delete_row("SquadMembers", 1470)
    data.invalidate()
    assert change.kind == "delete"
    assert len(instance.table("SquadMembers").rows) == before - 1
    assert changeset.row_changes == (change,)
    changeset.revert_row(change)
    assert len(instance.table("SquadMembers").rows) == before
    assert changeset.is_empty


def test_save_as_new_template_needs_a_link_row(instance) -> None:
    """没有关联行的值不用另存（复制出来没人引用），要给出人话提示。"""
    changeset = Changeset(instance)
    with pytest.raises(TableError):
        save_as_new_template(changeset, CellRef("Units", 645, "Weight"), None)


def test_next_row_id_does_not_reuse_an_id_deleted_this_session(instance) -> None:
    """删掉最大 Id 那一行之后，下一个新行**不许**捡回那个 Id。

    症状（本轮修的）：`next_row_id` 只看表里现有的最大 Id —— 删掉最大那一行之后，
    它正好把**刚删掉的 Id** 又发出去；而 `Changeset.insert_row` 的守卫看的是
    「表里有没有同 Id 的行」，表里确实没有，于是放行。可撤销那条删除会把旧行按
    原 Id 放回来 —— **两行数据抢一个 Id**。
    """
    table = instance.table("Armors")
    biggest = max(row[table.column_index("Id")] for row in table.rows)
    changeset = Changeset(instance)
    changeset.delete_row("Armors", biggest)

    assert next_row_id(table, changeset) == biggest + 1
    # 光看表（不传 changeset）还是旧行为 —— 所以**写路径必须传它**
    assert next_row_id(table) == biggest

    new_id = add_row(changeset, "Armors", {"ArmorValue": 1})
    assert new_id == biggest + 1

    # 撤销那条删除 -> 旧行按原 Id 回来，两行同时在，Id 不撞
    changeset.revert_row(changeset.row_changes[0])
    ids = {row[table.column_index("Id")] for row in table.rows}
    assert {biggest, biggest + 1} <= ids


def test_clone_row_also_avoids_an_id_deleted_this_session(instance) -> None:
    """`clone_row`（另存为新模板那条路）取新 Id 时同样不能捡回刚删的 Id。"""
    table = instance.table("Armors")
    biggest = max(row[table.column_index("Id")] for row in table.rows)
    changeset = Changeset(instance)
    changeset.delete_row("Armors", biggest)

    new_id = clone_row(changeset, CellRef("Armors", 281, "ArmorValue"))

    assert new_id == biggest + 1
    assert instance.table("Armors").rows[-1][table.column_index("Id")] == new_id
