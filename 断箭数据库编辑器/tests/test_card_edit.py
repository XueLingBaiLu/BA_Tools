"""树上直接增删的验收：＋ / 📄 / 🗑（本轮把入口从卡片搬到了关系树）。

用户口径（2026-09-30）：

    "自定义选项我还是改不了，加不了。"        -> 每一组都能「＋」
    卡片整块只读                             -> 动作迁到**树节点**上（行尾 ＋ / 📄 / 🗑 + 右键）

这一份只测**那三条线**（分组顺序 / 标签 / ID 由 test_relation_tree.py 守着）：

    ① 每一组（分组节点）右边有一个「＋」-> 就地冒搜索框（不弹对话框），点一条插一行；
    ② 每一项右边有一个「🗑」-> 删的是**关联行**，模板行不动；
    ③ 「📄 克隆一份」-> 复制模板行 + 把本单位指着它的那一格指过去（core/edit/clone.py）；
    ④ 撤销把删掉的那一行放回去（一次动作删几行 = 一次撤回来）；
    ⑤ 改完立刻落盘（既有行为，这里逐条复查）。

样本：单位 21（Stryker RV）/ 1（BTR-82A）/ 645（VDV Shturm，步兵）。
"""

from __future__ import annotations

import json
import zipfile

import pytest
from PySide6.QtWidgets import QComboBox, QPushButton

from ba_mod_maker.app.screens.editor.relation_tree_panel import (
    TOOL_ADD,
    TOOL_CLONE,
    TOOL_REMOVE,
)
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 载具（装甲 / 机动被改装选项覆盖：link 落在 Options 上；有 2 组运输配对 + 5 种炮塔配法）。
STRYKER = 21
#: 载具：其中一门武器（2А72）**有两个弹种** —— 用来验"删整门枪 = 删几行、一次撤回来"。
BTR82A = 1
#: 步兵：装甲 / 机动 / 传感器 / 能力 都是本单位自己的关联行（link 落在带 UnitId 的表上）。
INFANTRY = 645
#: Stryker RV 的另一门枪（Bushmaster2 塔底下那门）——**不在**当前选中的那套改装选项里
#: （现在装的是 M240 塔），所以"给这门枪加弹"必须钉死第一列才做得成。
BUSHMASTER2 = 23

#: 树上有「＋」的组（步兵 / 载具各一套；「属性」不是一组 —— 它是算出来的汇总，没有对应的行）。
VEHICLE_ADD_GROUPS = {
    "mods",
    "turrets",
    "ammo",
    "armor",
    "mobility",
    "sensors",
    "abilities",
    "specs",
    "transports",
}
INFANTRY_ADD_GROUPS = {
    "mods",
    "ammo",
    "armor",
    "mobility",
    "sensors",
    "abilities",
    "squad",
    "specs",
}


# 夹具（qapp / instance / window）统一在 tests/conftest.py：
#   window = **一个模块一个**主窗口，每个用例只把一份干净数据接回去
#   （为什么、怎么保证隔离，见 conftest 的模块 docstring）。


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _open(window, unit_id: int):
    """进编辑器，返回 (编辑器, 主编辑页, 关系树控件)。

    ❗ 2026-10-01：双击单位现在**默认落在编成台**（步兵形态，用户口径）。
    这一份测的是三栏那一页（卡上增删 / 关系树），所以进来之后显式切回去。
    """
    window._open_unit_editor(unit_id)
    window._unit_editor.show_main()
    editor = window._unit_editor
    page = editor.main_page
    return editor, page, page.tree


def _rows(instance, table: str) -> dict[int, dict]:
    """表里 Id -> 整行（按列名）。"""
    target = instance.table(table)
    index = target.column_index("Id")
    return {row[index]: dict(zip(target.columns, row, strict=True)) for row in target.rows}


def _zip_rows(path, table: str) -> dict[int, dict]:
    with zipfile.ZipFile(path) as archive:
        rows = json.loads(archive.read(f"{table}.json").decode("utf-8"))
    return {row["Id"]: row for row in rows}


def _node_by_label(tree, table: str, label: str):
    """按 (表, 名字) 找树上那个节点 —— 名字是 display_name 出来的那串。"""
    for item in tree.tree.flatten():
        if item.table == table and label in item.label:
            return item
    raise AssertionError(f"树上没有 {table} 的「{label}」这个节点")


def _node_by_rows(tree, table: str, row_ids: set[int]):
    """按"🗑 要删的那几行"找节点（一门枪两个弹种就是两行一起删）。"""
    for item in tree.tree.flatten():
        if item.table == table and {ref.row_id for ref in item.remove_refs} == row_ids:
            return item
    raise AssertionError(f"树上没有 🗑 指向 {table} {row_ids} 的节点")


def _has_remove_for(tree, table: str) -> bool:
    return any(item.can_remove and item.table == table for item in tree.tree.flatten())


# --------------------------------------------------------------------------- #
# ① 每一组都有 ＋；「属性」不是一组
# --------------------------------------------------------------------------- #


def test_vehicle_groups_offer_add_and_the_write_layer_agrees(window) -> None:
    """载具：改装槽 / 炮塔 / 弹药 / 装甲 / 机动 / 传感器 / 技能 / 专精 / 运输 都有「＋」。

    「运输」的那个 ＋ 从前只长在载具侧（TransportAvailabilities.UnitId 是载具）；
    现在它长在树的「🚚 运输配对」分组上，同一条口径。
    """
    from ba_mod_maker.app.screens.editor.pages.base import CARD_BLOCKS

    _editor, _page, tree = _open(window, STRYKER)
    groups = {group.key: group for group in tree.tree.groups}
    with_add = {key for key, group in groups.items() if group.add_block}
    assert with_add == VEHICLE_ADD_GROUPS, with_add
    for group in groups.values():
        assert not group.add_block or group.add_block in CARD_BLOCKS, group.add_block
    assert [b.text() for b in tree.tool_buttons("armor")] == [TOOL_ADD]


def test_infantry_groups_offer_add_but_not_transports(window) -> None:
    """步兵：多一个「班组成员 / 班用武器」，**没有**运输配对的 ＋（插出来方向反了）。"""
    _editor, _page, tree = _open(window, INFANTRY)
    groups = {group.key: group for group in tree.tree.groups}
    with_add = {key for key, group in groups.items() if group.add_block}
    assert with_add == INFANTRY_ADD_GROUPS, with_add


def test_the_computed_stats_are_not_a_group(window) -> None:
    """「属性（N）」不给增删 —— 它是从装甲 / 机动 / 传感器算出来的汇总，**没有对应的行**，
    所以树上根本没有这一组（想改就去那三组；Units 那一行的列在中栏）。"""
    _editor, _page, tree = _open(window, INFANTRY)
    labels = [group.label for group in tree.tree.groups]
    assert not any("属性" in label for label in labels), labels
    assert len(labels) == 10


# --------------------------------------------------------------------------- #
# ② ＋：插一行 + 立刻落盘
# --------------------------------------------------------------------------- #


def test_add_inserts_a_row_and_writes_it_to_disk(window, tmp_path) -> None:
    """＋ -> 就地冒搜索框 -> 点一条：关联表 +1 行、新 Id = 现有最大 +1、**立刻落盘**。"""
    _editor, _page, tree = _open(window, INFANTRY)
    before = _rows(window._instance, "UnitArmors")
    arms_before = _rows(window._instance, "Armors")
    biggest = max(before)

    assert tree.click_tool("armor", TOOL_ADD)
    picker = tree.picker
    assert picker is not None and picker.isVisibleTo(picker.parentWidget())
    assert picker.table == "Armors"
    new_target = picker.row_id_at(0)
    picker.picked.emit(new_target)

    after = _rows(window._instance, "UnitArmors")
    assert len(after) == len(before) + 1
    created = max(after)
    assert created == biggest + 1, "新行 Id 不是现有最大 +1"
    assert after[created]["UnitId"] == INFANTRY
    assert after[created]["ArmorId"] == new_target
    # 模板行一行没动
    assert _rows(window._instance, "Armors") == arms_before
    # 用户口径（2026-09-29）：只进缓存 —— 点「💾 保存」才落盘
    assert not (tmp_path / "BA_Mod.zip").exists(), "还没点保存，盘上不该有"
    _editor.save()
    assert created in _zip_rows(tmp_path / "BA_Mod.zip", "UnitArmors")


def test_weapon_add_picks_weapon_and_ammo_in_two_columns(window, tmp_path) -> None:
    """加弹种 = 插一行 WeaponAmmunitions（**武器 × 弹种**一起挑），不是插模板行。"""
    _editor, _page, tree = _open(window, BTR82A)
    weapons = _rows(window._instance, "Weapons")
    ammunitions = _rows(window._instance, "Ammunitions")
    before = _rows(window._instance, "WeaponAmmunitions")

    assert tree.click_tool("ammo", TOOL_ADD)
    picker = tree.picker
    assert picker is not None
    assert picker.table == "Weapons" and picker.second_table == "Ammunitions"
    assert picker.count >= 1 and picker.second_count >= 1
    # 左列 = 本单位已经挂着的武器（树上是"炮塔 > 武器"两层）
    unit_weapons = {
        weapon.row_id
        for turret in tree.tree.group("turrets").children
        for weapon in turret.children
        if weapon.table == "Weapons"
    }
    assert unit_weapons, "样本单位应当挂着武器"
    assert {picker.row_id_at(i) for i in range(picker.count)} <= unit_weapons

    weapon_id = picker.row_id_at(0)
    picker.choose_first(weapon_id)
    picker.pick_second(0)
    ammo_id = picker.second_row_id_at(0)

    after = _rows(window._instance, "WeaponAmmunitions")
    assert len(after) == len(before) + 1
    new_id = max(after)
    assert after[new_id]["UnitId"] == BTR82A
    assert after[new_id]["WeaponId"] == weapon_id
    assert after[new_id]["AmmunitionId"] == ammo_id
    # 模板行一行没动
    assert len(_rows(window._instance, "Weapons")) == len(weapons)
    assert len(_rows(window._instance, "Ammunitions")) == len(ammunitions)
    assert not (tmp_path / "BA_Mod.zip").exists(), "还没点保存，盘上不该有"
    window._unit_editor.save()
    assert new_id in _zip_rows(tmp_path / "BA_Mod.zip", "WeaponAmmunitions")


def test_a_weapon_row_can_add_an_ammo_type(window) -> None:
    """⭐ 武器行上那颗「＋」= 给**这一门枪**加一种弹（第一列钉死，只挑弹种）。

    用户 2026-10-01 实机反馈原话：「左边没有给武器加弹药的选项」——本轮之前只有
    「💣 弹药」组抬头那一颗「＋」，它从零问"哪门枪 × 哪种弹"；站在某一门枪上想给它
    配弹时，树上根本没有入口（实测 8 个武器节点的 add_block 全是空串）。

    样本取用户当时点的那一门（Bushmaster II #23）——它**不在**当前那套改装选项的
    武器里，所以它也不在「＋」的候选名单里：这正是必须"钉死第一列"而不是
    choose_first（要求那一行已在名单里）的原因。
    """
    _editor, page, tree = _open(window, STRYKER)
    weapon = next(
        node
        for turret in tree.tree.group("turrets").children
        for node in turret.children
        if node.table == "Weapons" and node.row_id == BUSHMASTER2
    )
    assert weapon.add_block == "weapons", "武器行上没有「＋」"
    assert weapon.add_first_id == BUSHMASTER2, "「＋」得预置成这一门枪"
    assert BUSHMASTER2 not in {w.weapon_id for w in page.card.card.weapons}

    before = _rows(window._instance, "WeaponAmmunitions")
    assert tree.click_tool(weapon.key, TOOL_ADD)
    picker = tree.picker
    assert picker is not None and picker.second_table == "Ammunitions"
    assert picker.first_locked, "枪已经定了，不该再叫人挑一遍"
    assert picker.current_first_id == BUSHMASTER2  # 它是个 property，不是方法
    assert picker.second_count >= 1

    picker.pick_second(0)
    ammo_id = picker.second_row_id_at(0)

    after = _rows(window._instance, "WeaponAmmunitions")
    assert len(after) == len(before) + 1
    created = max(after)
    assert after[created]["UnitId"] == STRYKER
    assert after[created]["WeaponId"] == BUSHMASTER2
    assert after[created]["AmmunitionId"] == ammo_id


def test_modification_block_adds_a_slot_of_this_unit(window, tmp_path) -> None:
    """改装槽：＋ 不用挑模板（这一行自己就是那个槽），插的是本单位的 Modifications 行。"""
    _editor, _page, tree = _open(window, STRYKER)
    before = _rows(window._instance, "Modifications")
    mine = sum(1 for row in before.values() if row["UnitId"] == STRYKER)

    assert tree.click_tool("mods", TOOL_ADD)  # 不用挑：直接插

    after = _rows(window._instance, "Modifications")
    created = max(after)
    assert created not in before
    assert after[created]["UnitId"] == STRYKER
    assert sum(1 for row in after.values() if row["UnitId"] == STRYKER) == mine + 1
    assert not (tmp_path / "BA_Mod.zip").exists(), "还没点保存，盘上不该有"
    window._unit_editor.save()
    assert created in _zip_rows(tmp_path / "BA_Mod.zip", "Modifications")


# --------------------------------------------------------------------------- #
# ③ 📄 克隆一份：复制模板行 + 把本单位指过去（别人不动）
# --------------------------------------------------------------------------- #


def test_clone_makes_a_private_copy_and_points_at_it(window, tmp_path) -> None:
    """步兵的装甲：📄 = 复制 Armors 那一行 -> UnitArmors 指过去 -> 原来那一行还在。"""
    editor, _page, tree = _open(window, INFANTRY)
    node = _node_by_label(tree, "Armors", "Armor Infantry")
    assert node.can_clone and node.clone_row is not None and node.link is not None
    before = _rows(window._instance, "Armors")
    link_before = editor.context.cell(node.link).raw

    assert tree.click_tool(node.key, TOOL_CLONE)

    after = _rows(window._instance, "Armors")
    assert len(after) == len(before) + 1, "没有复制出新的一行"
    new_id = editor.context.cell(node.link).raw
    assert new_id != link_before and new_id in after, "本单位那一格没有指到副本上"
    assert link_before in after, "原来那一行被删了？别人还在用它"
    assert not (tmp_path / "BA_Mod.zip").exists(), "克隆只进缓存，还没落盘"
    editor.save()
    assert new_id in _zip_rows(tmp_path / "BA_Mod.zip", "Armors"), "点了保存才落盘"


def test_clone_is_not_offered_on_rows_that_are_not_templates(window) -> None:
    """Units 那一行（另一个单位）与运输配对行不给 📄。"""
    _editor, _page, tree = _open(window, STRYKER)
    assert TOOL_CLONE not in [b.text() for b in tree.tool_buttons(tree.tree.roots[0].key)]
    for child in tree.tree.group("transports").children:
        assert not child.can_clone
        assert TOOL_CLONE not in [b.text() for b in tree.tool_buttons(child.key)]


# --------------------------------------------------------------------------- #
# ④ 🗑：删的是关联行，模板行不动；撤销放回来
# --------------------------------------------------------------------------- #


def test_remove_deletes_the_link_row_but_keeps_the_template(window, tmp_path) -> None:
    """删 WeaponAmmunitions#1193 = 这台车不带这个弹种；Ammunitions 那一行还在库里。"""
    _editor, _page, tree = _open(window, STRYKER)
    before = _rows(window._instance, "WeaponAmmunitions")
    ammo_before = _rows(window._instance, "Ammunitions")
    assert 1193 in before
    node = _node_by_rows(tree, "Ammunitions", {1193})

    assert tree.click_tool(node.key, TOOL_REMOVE)

    after = _rows(window._instance, "WeaponAmmunitions")
    assert 1193 not in after
    assert len(after) == len(before) - 1
    # 模板行一行不动
    assert _rows(window._instance, "Ammunitions") == ammo_before
    assert not (tmp_path / "BA_Mod.zip").exists(), "还没点保存，盘上不该有"
    window._unit_editor.save()
    assert 1193 not in _zip_rows(tmp_path / "BA_Mod.zip", "WeaponAmmunitions")


def test_removing_a_weapon_takes_its_ammo_rows_and_one_undo_brings_them_back(window) -> None:
    """一门枪两个弹种：🗑 一次删两行，**一次 Ctrl+Z 一起放回来**（撤销栈一次一条）。"""
    editor, _page, tree = _open(window, BTR82A)
    before = _rows(window._instance, "WeaponAmmunitions")
    assert 904 in before and 905 in before
    node = _node_by_rows(tree, "Weapons", {904, 905})

    tree.click_tool(node.key, TOOL_REMOVE)

    after = _rows(window._instance, "WeaponAmmunitions")
    assert 904 not in after and 905 not in after

    editor.undo()
    restored = _rows(window._instance, "WeaponAmmunitions")
    assert restored[904] == before[904], "撤销没有把删掉的那一行放回来"
    assert restored[905] == before[905]
    assert len(restored) == len(before)


def test_undo_puts_the_removed_row_back_and_rewrites_the_package(window, tmp_path) -> None:
    """撤销 = 把删掉的那一行原样插回去；再点保存，包里也是放回来的样子。

    口径（2026-09-29）：删 / 撤销都只动缓存，盘上要等「💾 保存」。
    """
    editor, _page, tree = _open(window, INFANTRY)
    before = _rows(window._instance, "UnitArmors")
    node = _node_by_label(tree, "Armors", "Armor Infantry")
    assert node.remove_refs and node.remove_refs[0].table == "UnitArmors"

    tree.click_tool(node.key, TOOL_REMOVE)
    assert len(_rows(window._instance, "UnitArmors")) == len(before) - 1
    assert not (tmp_path / "BA_Mod.zip").exists(), "还没点保存，盘上不该有"
    editor.save()
    assert len(_zip_rows(tmp_path / "BA_Mod.zip", "UnitArmors")) == len(before) - 1

    editor.undo()
    assert _rows(window._instance, "UnitArmors") == before, "撤销没有把那一行原样放回来"
    editor.save()
    assert _zip_rows(tmp_path / "BA_Mod.zip", "UnitArmors") == before, (
        "撤销之后保存，包里该是放回来的样子"
    )


def test_removed_message_goes_to_the_status_bar_with_an_undo_hint(window) -> None:
    """成功**不弹窗**：状态栏一句「已移除 X · 撤销」（AGENTS §5 的弹窗规则）。"""
    from PySide6.QtWidgets import QApplication

    _editor, _page, tree = _open(window, STRYKER)
    node = _node_by_rows(tree, "Ammunitions", {1193})
    said: list[str] = []
    window._unit_editor.status_message.connect(said.append)

    tree.click_tool(node.key, TOOL_REMOVE)

    # ❗ 状态栏不只这一句（可能还有"已移除…"之外的），所以收集**这一下说过的话**，
    #    验其中有那一句"已移除 X · 撤销"。（改动只进缓存之后不再有"已自动保存…"。）
    assert any("已移除" in text and "撤销" in text for text in said), said
    assert QApplication.activeModalWidget() is None, "删一行不该弹窗"


# --------------------------------------------------------------------------- #
# ⑤ 不给 🗑 的地方：模板行 / 选项覆盖那一格 / 有选项的槽
# --------------------------------------------------------------------------- #


def test_rows_owned_by_an_option_offer_no_remove(window) -> None:
    """装甲 / 机动 被改装选项覆盖时（Stryker RV）—— 那一格的"主人"是选项，不给删。"""
    editor, _page, tree = _open(window, STRYKER)
    armor = next(item for item in tree.tree.flatten() if item.table == "Armors")
    assert armor.link is not None and armor.link.table == "Options", "样本单位的装甲应当由选项覆盖"
    assert editor.context.association_row(armor.link) is None
    assert armor.can_remove is False, "选项覆盖的那一格不该给 🗑"
    # 而传感器 / 运输 / 能力 / 弹药 是本单位自己的行 —— 有 🗑
    tables = {
        ref.table for item in tree.tree.flatten() if item.can_remove for ref in item.remove_refs
    }
    own_rows = {"SensorUnits", "TransportAvailabilities", "UnitAbilities", "WeaponAmmunitions"}
    assert own_rows <= tables, f"本单位自己的关联行应当有 🗑，实际只有 {tables}"


def test_ability_remove_keeps_the_ability_template(window) -> None:
    """删能力 = 删本单位的 UnitAbilities 行；Abilities 那一行（51 列）还在库里。"""
    _editor, _page, tree = _open(window, INFANTRY)
    abilities_before = _rows(window._instance, "Abilities")
    node = next(item for item in tree.tree.group("abilities").children if item.can_remove)
    row_id = node.remove_refs[0].row_id
    assert node.remove_refs[0].table == "UnitAbilities"

    tree.click_tool(node.key, TOOL_REMOVE)

    assert row_id not in _rows(window._instance, "UnitAbilities")
    assert _rows(window._instance, "Abilities") == abilities_before


def test_a_slot_with_options_offers_no_remove(window) -> None:
    """有选项的改装槽不给 🗑 —— 删它会留下孤儿选项行（设计 §5 第 2 条还没定，取安全那半）。"""
    _editor, _page, tree = _open(window, STRYKER)
    slots = [child for child in tree.tree.group("mods").children if child.has_row]
    assert slots, "样本单位应当有改装槽"
    assert all(slot.children for slot in slots), "样本单位的槽底下应当有选项"
    assert not _has_remove_for(tree, "Modifications"), "有选项的槽不该给 🗑"


def test_picker_can_be_cancelled_and_stays_in_place(window) -> None:
    """「＋」就地冒搜索框（**不弹对话框**）；点「取消」收起来。"""
    from PySide6.QtWidgets import QApplication

    _editor, _page, tree = _open(window, INFANTRY)
    assert tree.click_tool("sensors", TOOL_ADD)
    picker = tree.picker
    assert picker is not None and picker.isVisibleTo(picker.parentWidget())
    picker.cancelled.emit()
    assert tree.picker is None and tree.picker_visible is False
    assert QApplication.activeModalWidget() is None, "「＋」不该弹对话框"


# --------------------------------------------------------------------------- #
# ⑥ 运输：候选来自**外键指向的那张表**
# --------------------------------------------------------------------------- #


def test_transport_add_lists_the_foreign_table(window) -> None:
    """TransportAvailabilities 只有 3 列（没有名字列）：＋ 的候选要列
    SpecializationAvailabilities（外键指向的那张表），不是"另一些配对行"。"""
    _editor, _page, tree = _open(window, STRYKER)
    assert tree.click_tool("transports", TOOL_ADD)
    picker = tree.picker
    assert picker is not None
    assert picker.table == "SpecializationAvailabilities", f"运输的候选列错了：{picker.table}"
    assert picker.count >= 1, "候选 0 条 = 用户看到的那个空框"


# --------------------------------------------------------------------------- #
# ⑦ 步兵班：成员能加能减；载具没有这一组
# --------------------------------------------------------------------------- #


def test_infantry_squad_group_has_a_remove_per_member(window) -> None:
    _editor, _page, tree = _open(window, INFANTRY)
    members = [
        child for child in tree.tree.group("squad").children if child.table == "SquadMembers"
    ]
    assert len(members) == 5
    for member in members:
        assert member.can_remove
        assert TOOL_REMOVE in [b.text() for b in tree.tool_buttons(member.key)]


def test_vehicle_has_no_squad_rows(window) -> None:
    _editor, _page, tree = _open(window, STRYKER)
    squad = tree.tree.group("squad")
    # 原来这里是那一列的灰字标签（tag）；用户 2026-09-29 把「有 / 可以」两列删掉之后，
    # 「非步兵」写成行尾灰字（note），tooltip 里再说一遍 —— 信息一个都没丢。
    assert "非步兵" in squad.note and "非步兵" in squad.hint
    assert squad.children == ()
    assert tree.tool_buttons(squad.key) == ()


def test_squad_add_copies_a_member_and_writes_it_to_disk(window, tmp_path) -> None:
    """「＋」= 照本单位现有的一名成员复制一行 SquadMembers（不挑别的单位的兵）。"""
    _editor, _page, tree = _open(window, INFANTRY)
    before = _rows(window._instance, "SquadMembers")
    mine = [row for row in before.values() if row["UnitId"] == INFANTRY]

    assert tree.click_tool("squad", TOOL_ADD)

    after = _rows(window._instance, "SquadMembers")
    created = max(after)
    assert created not in before
    assert after[created]["UnitId"] == INFANTRY
    assert sum(1 for row in after.values() if row["UnitId"] == INFANTRY) == len(mine) + 1
    assert not (tmp_path / "BA_Mod.zip").exists(), "还没点保存，盘上不该有"
    _editor.save()
    assert created in _zip_rows(tmp_path / "BA_Mod.zip", "SquadMembers")
    # 新兵跟最后一名老兵装具一样（口径在 viewmodel.unit_card.new_member_values）
    assert after[created]["PrimaryWeaponId"] == mine[-1]["PrimaryWeaponId"]


def test_squad_remove_deletes_only_that_member_and_undo_puts_it_back(window) -> None:
    editor, _page, tree = _open(window, INFANTRY)
    before = _rows(window._instance, "SquadMembers")
    node = tree.tree.group("squad").children[2]
    assert node.table == "SquadMembers"
    victim = node.row_id

    tree.click_tool(node.key, TOOL_REMOVE)
    assert victim not in _rows(window._instance, "SquadMembers")

    editor.undo()
    assert _rows(window._instance, "SquadMembers") == before


# --------------------------------------------------------------------------- #
# ⑧ 行尾按钮的文案 / 一次只开一个搜索框
# --------------------------------------------------------------------------- #


def test_row_tool_buttons_show_their_whole_text(window) -> None:
    """用户口径：「“换...”你也给我显示全了啊」—— 树上的按钮只有 ＋ / 📄 / 🗑 三个字符，
    **没有省略号**，而且每一颗都有说明（悬浮提示）。

    ❗ 文案取自 relation_tree_panel 的 TOOL_* 常量（控件与用例同一份）—— 这里换成
    常量之后**不能**只断"文案在这三个里"（那会变成盖章）：还要断**哪一行该长哪几颗**
    （能力的顺序就是 _tools_widget 画的顺序），控件少长/多长一颗都会红。
    """
    _editor, _page, tree = _open(window, STRYKER)
    nodes = {item.key: item for item in tree.tree.flatten()}
    seen = 0
    for key in tree.visible_keys():
        node = nodes[key]
        expected = []
        if node.can_add:
            expected.append(TOOL_ADD)
        if node.can_clone:
            expected.append(TOOL_CLONE)
        if node.can_remove:
            expected.append(TOOL_REMOVE)
        buttons = tree.tool_buttons(key)
        assert [button.text() for button in buttons] == expected, (key, node.label)
        for button in buttons:
            seen += 1
            assert "..." not in button.text() and "…" not in button.text(), button.text()
            assert button.toolTip(), f"{key} 上那颗 {button.text()} 没有说明"
    assert seen > 0, "一颗行尾按钮都没有？"


def test_only_one_inline_picker_is_open_at_a_time(window) -> None:
    """先开「装甲」的候选框，再点「机动」的 ＋ —— 一次只留一个（卡片那边踩过同一个坑）。"""
    _editor, _page, tree = _open(window, STRYKER)
    assert tree.click_tool("armor", TOOL_ADD)
    first = tree.picker
    assert first is not None
    assert tree.click_tool("mobility", TOOL_ADD)
    second = tree.picker
    assert second is not None and second is not first
    assert tree.picker_visible, "第二个候选框没有冒出来"


# --------------------------------------------------------------------------- #
# ⑨ 只读卡（本轮把卡上的编辑入口全拿掉了）
# --------------------------------------------------------------------------- #


def _in_zoom_bar(widget, card) -> bool:
    """这颗控件是不是「放大 / 缩小」那一排（role=card-zoom）里的？

    那是**显示**开关（只改卡片画多大、不写库、不改数据），不算"可点的编辑入口"。
    ❗ 同一份判据在 tests/test_relation_tree.py 与 tests/test_card_edit.py 里各有一份
    （copy，不是共享；改一处要同时改另一处）—— 两边守的是同一句话的不同半边：
    那边守「树上不许长编辑入口」，这边守「只读卡上不许长编辑入口」。
    """
    parent = widget.parentWidget()
    while parent is not None and parent is not card:
        if parent.property("role") == "card-zoom":
            return True
        parent = parent.parentWidget()
    return False


def test_readonly_card_has_no_button_no_combo_and_no_open_in_table(window) -> None:
    """单位编辑器那张卡**没有任何编辑按钮 / 下拉**，也没有底部那个「在表格中打开」。

    用户口径（2026-09-30）：卡片整块只读、不再是导航；改数据只在中间那栏。
    军械库那张卡照旧（它自己那一套由 test_armory_screen.py 守着）。

    ❗ 2026-09-29 用户口径：只读卡上要给一个「放大 / 缩小」开关（role=card-zoom）——
    它只改显示大小、一个字节都不写。除那一排之外仍然一颗按钮都不许有
    （老断言是"一颗按钮都不许有"，那是加这个开关之前的版本）。
    """
    _editor, _page, _tree = _open(window, STRYKER)
    card = window._unit_editor.main_page.card
    texts = [button.text() for button in card.findChildren(QPushButton)]
    assert "在表格中打开" not in texts
    scattered = [
        button.text() for button in card.findChildren(QPushButton) if not _in_zoom_bar(button, card)
    ]
    assert scattered == [], f"只读卡上冒出了「放大 / 缩小」以外的按钮：{scattered}"
    # 防止上面那条变成盖章：例外**只有**那一排，而且 _in_zoom_bar 得真的会分辨。
    zoom_buttons = [b for b in card.findChildren(QPushButton) if _in_zoom_bar(b, card)]
    assert zoom_buttons, "只读卡上应当有「放大 / 缩小」那一排（用户 2026-09-29 口径）"
    assert not _in_zoom_bar(card, card), "_in_zoom_bar 恒真了 —— 那上面那条就没在验东西"
    assert card.findChildren(QComboBox) == []
