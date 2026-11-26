"""编成台属性栏（装甲 / 机动 / 能力 / 传感器 / 专精可用性）+ 改装槽（槽内互斥）。

样本全部取真数据（DataBaseCompiled / 实例 52085），Id 都是探针跑出来钉死的：

- **单位 70**（Marine Raiders，步兵 14 人）：UnitArmors#605 / UnitPropulsions#708 /
  UnitAbilities#1018，一个改装槽 Modifications#423（两条选项 1520 Base / 1521 LongRange）；
- **单位 287**（F-15E）：1 + 2 + 3 = **6 行**（所以"一行一个类别"的画法会丢行）；
- **单位 645**：它的槽 Modifications#743 **属于单位 108**（借来的槽）—— 点它一个字节都不许写；
- **单位 46**（Ka-52，直升机 = 折叠态）：3 个槽 #75 / #76 / #77，各 3 / 5 / 2 条选项 ——
  第十轮「增删整槽」的样本（§末尾那一节；交接 §16 里写的 "2/5/2" 是笔误）。

三条纪律：断言钉**控件里那一份状态**（BenchRow.selected / ●○ / 标签文字），不现算；
点控件用 **QTest 真点**（不是 emit 冒充）；每个用例收尾销毁自己建的控件。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest

from ba_mod_maker.app.screens.editor.pages.base import EditorContext
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import bench_slots as vm
from ba_mod_maker.app.viewmodel.row_icon import row_icon
from ba_mod_maker.app.viewmodel.table_labels import table_emoji
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.app.widgets.bench_slots import (
    SLOTS_HINT,
    SLOTS_TITLE,
    STATS_HINT,
    STATS_TITLE,
    SlotsBench,
    StatsBench,
)
from ba_mod_maker.core.assets.families import OPTION_PICTURE_FAMILY
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.clone import next_row_id
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.sharing import build_sharing_index

#: 真数据里那几个 Id（探针跑出来的，改数据源要跟着改）。
UNIT = 70
UNIT_AIR = 287
UNIT_VARIANT = 645
ARMOR_ROW, ARMOR_LINK = 605, 118
MOBILITY_ROW, MOBILITY_LINK = 708, 190
ABILITY_ROW, ABILITY_LINK = 1018, 103
SENSOR_ROW, SENSOR_LINK = 121, 23
SLOT = 423
OPT_BASE, OPT_LONG = 1520, 1521
BORROWED_SLOT = 743
BORROWED_OWNER = 108
BORROWED_CURRENT = 3129
BORROWED_OTHER = 3128
#: 改装槽 **≥ 2 个**的载具样本 —— 块内分组折叠只在这种块上存在（单位 70 / 206 都只有 1 个槽）。
#: 实测单位 6：3 个槽 #41 Weapons / #39 Armor / #565 Ability，每槽各 2 个选项。
UNIT_SLOTS = 6
SLOT_IDS = (41, 39, 565)
#: 给 ③ 的「＋」用的模板行（Abilities#4 = Empty ability，没人挂过它）。
ABILITY_TEMPLATE = 4
#: 第十轮「增删整槽」的样本：**Ka-52（单位 46，直升机 = 折叠态那一套）**。
#: 3 个槽 #75 / #76 / #77，各 **3 / 5 / 2** 条选项（实测探针跑出来的；
#: 交接 §16 里写的 "2/5/2" 是笔误 —— 槽 75 还多一条 #1663 GSh23L）。
UNIT_KA = 46
KA_SLOTS = (75, 76, 77)
KA_OPTIONS = {
    75: (208, 233, 1663),
    76: (210, 211, 234, 1664, 1665),
    77: (212, 213),
}


# --------------------------------------------------------------------------- #
# 夹具与小工具
# --------------------------------------------------------------------------- #


def make_context(instance, unit_id):
    """与 UnitEditorScreen 建的那一份同构（寻址 / 共享度 / 撤销栈都走 core）。"""
    return EditorContext(
        instance,
        unit_id,
        Changeset(instance),
        CardData(instance),
        build_sharing_index(instance),
        lambda: None,
    )


@pytest.fixture()
def context(instance):
    return make_context(instance, UNIT)


@pytest.fixture()
def variant_context(instance):
    return make_context(instance, UNIT_VARIANT)


@pytest.fixture()
def benches(qapp):
    """建编成台控件（收尾统一销毁 —— conftest 对顶层控件数有上限钉子）。"""
    made = []

    def build(cls, context=None, **kwargs):
        widget = cls(**kwargs)
        made.append(widget)
        widget.set_context(context)
        return widget

    yield build
    for widget in made:
        widget.deleteLater()


@pytest.fixture()
def messages():
    return []


def click(widget) -> None:
    """真的点一下（QTest 送鼠标事件，不是 emit 冒充）。"""
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())


def data_of(instance):
    return CardData(instance)


def cell(instance, table, row_id, column):
    """独立于界面去读表里那一格（免得断言和自我实现打转）。"""
    rows = instance.table(table)
    index = {name: i for i, name in enumerate(rows.columns)}
    for row in rows.rows:
        if row[index["Id"]] == row_id:
            return row[index[column]]
    raise AssertionError(f"{table} 里没有 #{row_id}")


def has_row(instance, table, row_id) -> bool:
    rows = instance.table(table)
    index = rows.column_index("Id")
    return any(row[index] == row_id for row in rows.rows)


def isdefault_snapshot(instance):
    """全表 Options.IsDefault 的一份快照（比对"一个字节都没动"）。"""
    rows = instance.table("Options")
    id_index, flag_index = rows.column_index("Id"), rows.column_index("IsDefault")
    return tuple(sorted((row[id_index], bool(row[flag_index])) for row in rows.rows))


def labels_of(widget):
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


# --------------------------------------------------------------------------- #
# ③ 取数：一行 = 关联表里的一行
# --------------------------------------------------------------------------- #


def test_stat_lines_read_the_three_association_tables(instance) -> None:
    lines = vm.stat_lines(data_of(instance), UNIT)
    assert [(line.table, line.row_id, line.link_row_id, line.category) for line in lines] == [
        ("UnitArmors", ARMOR_ROW, ARMOR_LINK, "装甲"),
        ("UnitPropulsions", MOBILITY_ROW, MOBILITY_LINK, "机动"),
        ("UnitAbilities", ABILITY_ROW, ABILITY_LINK, "能力"),
    ]
    assert [line.label for line in lines] == [
        "Armor Infantry Elite 14Men",
        "Infantry 18kmh Airdrop",
        "Sprint30 Smoke",
    ]


def test_armor_line_says_value_and_health(instance) -> None:
    line = vm.stat_lines(data_of(instance), UNIT)[0]
    assert line.detail == "装甲值 10 · 生命 70（= 5 × 14 人）"


def test_mobility_line_says_speed_and_airdrop(instance) -> None:
    line = vm.stat_lines(data_of(instance), UNIT)[1]
    assert line.detail == "前进 18.0 km/h · 可空投"


def test_ability_line_lists_the_switches_it_turns_on(instance) -> None:
    line = vm.stat_lines(data_of(instance), UNIT)[2]
    assert line.detail == "冲刺 10 秒 · 烟雾"


def test_every_association_row_gets_its_own_line(instance) -> None:
    """F-15E：1 装甲 + 2 机动 + 3 能力 —— 一行一个类别会丢行，这里 6 行都在。"""
    lines = vm.stat_lines(data_of(instance), UNIT_AIR)
    assert [line.table for line in lines] == [
        "UnitArmors",
        "UnitPropulsions",
        "UnitPropulsions",
        "UnitAbilities",
        "UnitAbilities",
        "UnitAbilities",
    ]
    assert [line.row_id for line in lines] == [453, 725, 726, 848, 849, 850]


def test_sensors_come_from_their_own_kind(instance) -> None:
    """传感器是**单独一栏**（2026-10-02 拆块）—— 只有传了 SENSOR_KIND 才画它。

    kinds 是这一层的**唯一**开关（从前那个 with_sensors= 已删：两个开关 = 两个真相）。
    """
    data = data_of(instance)
    assert all(line.table != "SensorUnits" for line in vm.stat_lines(data, UNIT))
    lines = vm.stat_lines(data, UNIT, kinds=(*vm.STAT_KINDS, vm.SENSOR_KIND))
    sensor = lines[-1]
    assert (sensor.table, sensor.row_id, sensor.link_row_id, sensor.category) == (
        "SensorUnits",
        SENSOR_ROW,
        SENSOR_LINK,
        "传感器",
    )
    assert sensor.label == "Medium Plus Optics"
    assert sensor.detail == "光学 地面 700 · 低空 900 · 高空 1000"


def test_spec_kind_reads_specialization_availabilities(instance) -> None:
    """专精可用性：一行 = SA 里 UnitId == 本单位 的一行，label = Specializations.Name。

    口径照抄 relation_tree._specs_group。实测单位 70 有一条（SA#102 / 专精 3 = USMC / 上限 2）；
    单位 23（Typhoon VDV）**一条都没有** —— 用户 2026-10-02 已确认：空就空着，不填假值。
    """
    data = data_of(instance)
    lines = vm.stat_lines(data, UNIT, kinds=(vm.SPEC_KIND,))
    assert [(line.table, line.row_id, line.link_row_id, line.category) for line in lines] == [
        ("SpecializationAvailabilities", 102, 3, "专精可用性")
    ]
    assert lines[0].label == "USMC"
    assert lines[0].detail == "上限 2"
    assert lines[0].link_table == "Specializations"
    assert vm.stat_lines(data, 23, kinds=(vm.SPEC_KIND,)) == ()


def test_new_stat_values_works_for_specialization_availabilities(instance) -> None:
    """⑦ 那一栏的「＋」也走同一条路（外键列 = SpecializationId）。"""
    context = make_context(instance, UNIT)
    values = vm.new_stat_values(
        context.data, UNIT, "SpecializationAvailabilities", 7, changeset=context.changeset
    )
    expected = next_row_id(instance.table("SpecializationAvailabilities"), context.changeset)
    assert values == {"Id": expected, "UnitId": UNIT, "SpecializationId": 7}
    assert vm.stat_kind("SpecializationAvailabilities") is vm.SPEC_KIND


def test_new_stat_values_use_next_row_id(instance) -> None:
    context = make_context(instance, UNIT)
    values = vm.new_stat_values(
        context.data, UNIT, "UnitAbilities", ABILITY_TEMPLATE, changeset=context.changeset
    )
    expected = next_row_id(instance.table("UnitAbilities"), context.changeset)
    assert values == {"Id": expected, "UnitId": UNIT, "AbilityId": ABILITY_TEMPLATE}
    assert expected > max(row[0] for row in instance.table("UnitAbilities").rows)


def test_new_stat_values_never_reuse_a_row_deleted_this_session(instance) -> None:
    """删掉最大 Id 那一行之后不许捡回它（core/edit/clone.py::next_row_id 的口径）。"""
    context = make_context(instance, UNIT)
    biggest = max(row[0] for row in instance.table("UnitAbilities").rows)
    context.changeset.delete_row("UnitAbilities", biggest)
    values = vm.new_stat_values(
        context.data, UNIT, "UnitAbilities", ABILITY_TEMPLATE, changeset=context.changeset
    )
    assert values["Id"] == biggest + 1


def test_new_stat_values_rejects_a_table_that_is_not_in_block_3(instance) -> None:
    context = make_context(instance, UNIT)
    with pytest.raises(KeyError):
        vm.new_stat_values(context.data, UNIT, "Weapons", 1)


# --------------------------------------------------------------------------- #
# ④ 取数：槽 › 互斥选项
# --------------------------------------------------------------------------- #


def test_slot_views_name_the_slot_and_its_options(instance) -> None:
    groups = vm.slot_views(data_of(instance), UNIT)
    heads = [
        (group.modification_id, group.title, group.is_own_slot, group.note) for group in groups
    ]
    assert heads == [(SLOT, "Squad MarineRaiders", True, "")]
    options = groups[0].options
    assert [option.name for option in options] == ["Base", "LongRange"]
    assert [option.cost_text for option in options] == ["+10", "±0"]
    assert [option.selected for option in options] == [True, False]
    assert groups[0].current_option_id == OPT_BASE
    assert options[0].icon == "INF_CQBR"


def test_option_effect_text_is_the_design_sentence(instance) -> None:
    data = data_of(instance)
    groups = vm.slot_views(data, UNIT)
    assert [option.detail for option in groups[0].options] == [
        "选中 → 这个班变成 #70 Marine Raiders",
        "选中 → 这个班变成 #359 Marine Raiders",
    ]


def test_select_option_writes_only_this_slot(instance) -> None:
    result = vm.select_option(data_of(instance), UNIT, SLOT, OPT_LONG)
    assert result.writes == ((OPT_LONG, "IsDefault", True),)
    assert (result.refused, result.option_label, result.cost) == (False, "LongRange", 0)
    assert result.message == "已选中「LongRange」（0）"


def test_select_option_is_a_noop_when_it_is_already_the_current_one(instance) -> None:
    """已经是这一项 = 空元组 —— 界面据此一个字都不写（点一下不该多一笔撤销）。"""
    result = vm.select_option(data_of(instance), UNIT_AIR, 305, 884)
    assert result.writes == ()
    assert result.refused is False
    assert "已经是这个槽选中的那一项" in result.message


def test_select_option_refuses_a_borrowed_slot(instance) -> None:
    result = vm.select_option(data_of(instance), UNIT_VARIANT, BORROWED_SLOT, BORROWED_OTHER)
    assert result.writes == ()
    assert result.refused is True
    assert "不是本单位的槽" in result.message
    assert f"#{BORROWED_OWNER}" in result.message


def test_select_option_refuses_an_option_of_another_slot(instance) -> None:
    result = vm.select_option(data_of(instance), UNIT, SLOT, BORROWED_OTHER)
    assert result.writes == ()
    assert result.refused is True
    assert "不在这个槽" in result.message


def test_new_option_order_is_max_plus_one_not_the_count(instance) -> None:
    """Order 取该槽现有 Order 的**最大值 + 1**，不是"条数" —— 删过行之后两个数会分叉。

    删掉 Order=0 的 Base 之后，槽里只剩 1 条（Order=1）：条数口径会给 1，
    最大值 + 1 给 2（Lead 2026-10-01 钉的就是后者）。
    """
    context = make_context(instance, UNIT)
    context.delete_row("Options", OPT_BASE)
    values = vm.new_option_values(context.data, SLOT, changeset=context.changeset)
    assert values["Order"] == 2


def test_new_option_values_does_not_steal_the_current_pick(instance) -> None:
    context = make_context(instance, UNIT)
    values = vm.new_option_values(context.data, SLOT, changeset=context.changeset)
    expected = next_row_id(instance.table("Options"), context.changeset)
    assert values["Id"] == expected
    assert values["ModificationId"] == SLOT
    assert values["IsDefault"] is False
    assert values["Order"] == 2
    assert values["Name"] == f"新选项 #{expected}"


# --------------------------------------------------------------------------- #
# ③ 控件：一行一个关联行，抬头一颗 ＋ 管一张表
# --------------------------------------------------------------------------- #


def test_stats_bench_shows_one_row_per_association_row(benches, context) -> None:
    bench = benches(StatsBench, context)
    assert [(line.table, line.row_id) for line in bench.lines()] == [
        ("UnitArmors", ARMOR_ROW),
        ("UnitPropulsions", MOBILITY_ROW),
        ("UnitAbilities", ABILITY_ROW),
    ]
    assert [row.name_label().text() for row in bench.rows()] == [
        "Armor Infantry Elite 14Men",
        "Infantry 18kmh Airdrop",
        "Sprint30 Smoke",
    ]
    assert [row.detail_label().text() for row in bench.rows()] == [
        "装甲值 10 · 生命 70（= 5 × 14 人）",
        "前进 18.0 km/h · 可空投",
        "冲刺 10 秒 · 烟雾",
    ]
    assert [row.value_label().text() for row in bench.rows()] == ["装甲", "机动", "能力"]
    # ③ 的 value 格是"装甲 / 机动 / 能力"这类词，不许借费用那支暖金
    assert {row.value_label().property("role") for row in bench.rows()} == {"bench-row-value"}
    assert all(row.remove_button().isHidden() is False for row in bench.rows())
    # ❗ 行尾那颗 ↗（「去看这一行」）2026-10-02 按用户要求删了 —— 这一块只剩 ✕。
    assert all(not hasattr(row, "link_button") for row in bench.rows())


def test_stats_head_has_one_add_button_per_table(benches, context) -> None:
    bench = benches(StatsBench, context)
    assert [button.text() for button in bench.add_buttons()] == ["＋ 装甲", "＋ 机动", "＋ 能力"]
    assert STATS_TITLE in labels_of(bench)
    assert STATS_HINT in labels_of(bench)


def test_clicking_a_stat_row_selects_it(benches, context, messages) -> None:
    bench = benches(StatsBench, context)
    bench.status_message.connect(messages.append)
    seen = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    click(bench.rows()[1])
    assert seen == [("UnitPropulsions", MOBILITY_ROW)]
    assert bench.selected() == ("UnitPropulsions", MOBILITY_ROW)
    assert bench.selected_table() == "UnitPropulsions"
    assert bench.selected_row_id() == MOBILITY_ROW
    assert [row.selected for row in bench.rows()] == [False, True, False]
    assert messages == []  # 选中不是写操作，不该说话


def test_a_stat_row_has_no_arrow_any_more(benches, context) -> None:
    """❗ 翻口径（2026-10-02）：行尾那颗 ↗「去看这一行」按用户要求**删了**。

    点这一行本身就是"右栏给它的全部列"；它指向的模板行（Armors / Mobility / …）
    走右栏的「🔍 详情」（`bench_detail`）。这一条原来叫
    `test_stat_row_link_button_goes_to_that_row`。
    """
    bench = benches(StatsBench, context)
    assert not hasattr(bench.rows()[2], "link_button")
    assert not hasattr(bench, "row_activated")


def test_stat_row_remove_deletes_only_the_association_row(
    instance, benches, context, messages
) -> None:
    bench = benches(StatsBench, context)
    bench.status_message.connect(messages.append)
    bench.rows()[0].remove_button().click()
    assert not has_row(instance, "UnitArmors", ARMOR_ROW)
    assert has_row(instance, "Armors", ARMOR_LINK)  # 模板行一个字节没动
    assert [line.table for line in bench.lines()] == ["UnitPropulsions", "UnitAbilities"]
    assert "UnitArmors #605" in messages[-1]
    assert "模板没动" in messages[-1]


def test_the_stat_row_clone_saves_a_new_armor_template_for_this_unit(
    instance, benches, context, messages
) -> None:
    """⭐ 用户 2026-10-02 定的口径：「**复制装甲模板另存一份**」。

    点 ③ 装甲那一行的 ⧉ ⇒ Armors 多一行（照模板整行照抄、只换 Id）、
    UnitArmors#605 的 ArmorId 指到新行、**别人用着的那份一个字节没动**；
    复制完当场选中新模板行（右栏落到它，直接在上面改）。
    """
    bench = benches(StatsBench, context)  # 单位 70：装甲 / 机动 / 能力
    bench.status_message.connect(messages.append)
    row = bench.rows()[0]
    assert [tool.text() for tool in row.tools()] == ["⧉"], "行尾要有一颗复制（在 ✕ 左边）"
    assert cell(instance, "UnitArmors", ARMOR_ROW, "ArmorId") == ARMOR_LINK
    expected = next_row_id(instance.table("Armors"), context.changeset)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    click(row.tools()[0])

    assert has_row(instance, "Armors", expected), "模板行没另存出来"
    assert cell(instance, "UnitArmors", ARMOR_ROW, "ArmorId") == expected, "关联行要指到新模板"
    for column in ("Name", "ArmorValue", "MaxHealthPoints", "KinArmorFront", "IsDefault"):
        assert cell(instance, "Armors", expected, column) == cell(
            instance, "Armors", ARMOR_LINK, column
        ), column
    assert has_row(instance, "Armors", ARMOR_LINK), "别人用着的那一份不许动"
    assert seen == [("Armors", expected)], "复制完右栏要落到新模板行"
    assert "别人没动" in messages[-1]
    # ❗ 模板行 ＋ 关联行 = **一条**撤销（用户 2026-10-02：「做吧」）
    assert context.undo_depth == 1, "整次复制要打包成一条"
    assert "撤销" in context.undo_last()
    assert cell(instance, "UnitArmors", ARMOR_ROW, "ArmorId") == ARMOR_LINK, "一次 Ctrl+Z 该全回来"
    assert not has_row(instance, "Armors", expected), "新模板行没撤掉"


def test_add_line_writes_next_row_id_and_shows_up(instance, benches, context, messages) -> None:
    bench = benches(StatsBench, context)
    bench.status_message.connect(messages.append)
    expected = next_row_id(instance.table("UnitAbilities"), context.changeset)
    click(bench.add_buttons()[2])
    picker = bench.picker()
    assert picker is not None
    assert picker.table == "Abilities"
    assert bench.picker_table() == "UnitAbilities"
    picker.picked.emit(ABILITY_TEMPLATE)  # 候选列表里点中了一行（RowPicker 的公开信号）
    assert bench.picker() is None  # 选完就收起
    assert cell(instance, "UnitAbilities", expected, "UnitId") == UNIT
    assert cell(instance, "UnitAbilities", expected, "AbilityId") == ABILITY_TEMPLATE
    assert [line.row_id for line in bench.lines()][-1] == expected
    assert "已加一行能力" in messages[-1]


def test_add_line_for_an_unknown_table_does_nothing(instance, benches, context) -> None:
    bench = benches(StatsBench, context)
    assert bench.add_line("Weapons", 1) == 0
    assert context.changeset.is_empty is True


def test_stats_bench_without_context_is_a_hint(benches) -> None:
    bench = benches(StatsBench)
    assert bench.rows() == ()
    assert bench.lines() == ()
    assert bench.selected() == ("", 0)
    assert any("还没有选中单位" in text for text in labels_of(bench))
    assert bench.begin_add("UnitArmors") is False


def test_stats_bench_can_be_cleared(benches, context) -> None:
    """clear_selection()：清高亮但**不发信号、不写库**（页面拿它做"一屏只有一个选中"）。"""
    bench = benches(StatsBench, context)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    click(bench.rows()[1])
    assert bench.selected() == ("UnitPropulsions", MOBILITY_ROW)
    assert [row.selected for row in bench.rows()] == [False, True, False]
    seen.clear()

    bench.clear_selection()

    assert bench.selected() == ("", 0)
    assert [row.selected for row in bench.rows()] == [False, False, False]
    assert seen == [], "清选中不许再发一次 row_selected（否则会绕回来又清别人一遍）"
    bench.clear_selection()  # 幂等：已经清干净了，什么都不做


def test_stats_bench_can_draw_a_single_kind(benches, context) -> None:
    """拆块之后一栏只画一类：kinds=(装甲,) 就只有 UnitArmors 那一行 ＋ 一颗按钮。"""
    bench = benches(StatsBench, context, kinds=(vm.STAT_KINDS[0],))
    assert [line.table for line in bench.lines()] == ["UnitArmors"]
    assert [button.text() for button in bench.add_buttons()] == ["＋ 装甲"]
    assert bench.kinds() == (vm.STAT_KINDS[0],)
    assert bench.section_title() == "装甲"  # 没编号（编号由装配方按位置写）
    assert bench.section_hint() == "UnitArmors"


def test_stats_bench_says_which_kind_is_empty(instance, benches) -> None:
    """空态那句要写**这一栏**是什么（不是"装甲 / 机动 / 能力"那一套）。

    单位 23（Typhoon VDV）在 SpecializationAvailabilities 里**一条都没有**（实测 0 行）
    —— 用户 2026-10-02 已确认：「空就空着」。
    """
    bench = benches(StatsBench, make_context(instance, 23), kinds=(vm.SPEC_KIND,))
    assert bench.lines() == ()
    assert any("还没有专精可用性行" in text for text in labels_of(bench))


# --------------------------------------------------------------------------- #
# ④ 控件：点一行 = 选中它；借来的槽一个字节都不写
# --------------------------------------------------------------------------- #


def test_slots_bench_draws_the_circle_and_the_cost(benches, context) -> None:
    bench = benches(SlotsBench, context)
    assert bench.group_titles() == ("Squad MarineRaiders",)
    assert [row.name_label().text() for row in bench.rows()] == ["● Base", "○ LongRange"]
    assert [row.value_label().text() for row in bench.rows()] == ["+10", "±0"]
    assert [row.detail_label().text() for row in bench.rows()] == [
        "选中 → 这个班变成 #70 Marine Raiders",
        "选中 → 这个班变成 #359 Marine Raiders",
    ]
    assert bench.selected_option_id() == OPT_BASE
    # ❗ 翻口径（2026-10-02）：整行高亮 = **页面选中**（一屏只有一个）；这条选项是不是
    #    生效的那一项由名字前那颗 ● / ○ 表达（上面 507 行已钉）。刚进来还没有页面选中。
    assert [row.selected for row in bench.rows()] == [False, False]
    # 选项行现在**有 ✕**（删这一条选项）—— 用户 2026-10-02：「所有有增删空间的，一律加上」
    assert all(row.remove_button().isHidden() is False for row in bench.rows())
    assert all(not hasattr(row, "link_button") for row in bench.rows())
    assert SLOTS_TITLE in labels_of(bench)
    assert SLOTS_HINT in labels_of(bench)
    assert any("2 个选项" in text for text in labels_of(bench))


def test_only_the_page_selected_option_row_is_highlighted(benches, context) -> None:
    """⭐ ② 的整行高亮 = **页面选中**（一屏只有一个）—— 不是 Options.IsDefault。

    用户 2026-10-02：「我为什么又一次性选中了这么多？」—— 三个挂载组各有 1 条生效项，
    从前那三条**都整行高亮**，看着就像"一次选了 3 行"。现在"谁生效"只由名字前那颗
    ● / ○ 表达（1 选 1），整行高亮只留给右栏正在画的那一行。
    """
    bench = benches(SlotsBench, context)
    assert [row.selected for row in bench.rows()] == [False, False], "刚进来还没有页面选中"
    assert [row.name_label().text()[:1] for row in bench.rows()] == ["●", "○"], "● 是数据态，照旧"

    click(bench.row_for(OPT_LONG))
    assert bench.row_for(OPT_LONG).selected is True
    assert bench.row_for(OPT_BASE).selected is False, "同一屏只许亮一行"


def test_the_option_row_remove_deletes_that_option(instance, benches, context, messages) -> None:
    """行尾 ✕ = 删掉**这一条选项**（Options 那一行）；槽（Modifications）本身一个字节不动。

    用户 2026-10-02：「我不能自己增删挂载。所有有增删空间的，一律给我加上增删的功能」。
    """
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    row = bench.row_for(OPT_LONG)
    assert row is not None and row.remove_button().isHidden() is False

    click(row.remove_button())

    assert not has_row(instance, "Options", OPT_LONG)
    assert has_row(instance, "Modifications", SLOT), "槽那一行不许跟着没"
    assert len(bench.rows()) == 1
    assert f"Options #{OPT_LONG}" in messages[-1] and "撤销" in messages[-1]


def test_removing_the_active_option_says_the_game_falls_back(benches, context, messages) -> None:
    """删掉**正选着的那一项**要当场说清：这个槽现在没有选中项，游戏会退回 Order 最小的那一条。"""
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    assert bench.row_for(OPT_BASE).name_label().text().startswith("●")

    click(bench.row_for(OPT_BASE).remove_button())

    assert "退回 Order 最小" in messages[-1]
    assert bench.selected_option_id() == 0, "● 没了，这一槽就没有选中项"


def test_clicking_an_option_row_only_opens_it(instance, benches, context, messages) -> None:
    """⭐ 2026-10-02 口径翻（用户：「它不就是一个布尔值吗？我直接改成0或1就行了」）：
    点一行选项 = **在右栏打开它**（看 / 改它的全部列），**一个字节都不写**。

    改前这一下会写 Options.IsDefault —— 于是用户手写在右栏的 0/1 会被它悄悄盖回去
    （用户报：「我写0，它变成了否，然后回来再看，它又变成了是」）。
    """
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    seen = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    click(bench.rows()[1])
    assert seen == [("Options", OPT_LONG)], "右栏要换成它"
    assert context.changeset.count == 0, "看它一眼不该写库"
    # 样本里 Base / LongRange 两条 IsDefault 都是 false（当前项靠 Order==0 兜底）—— 点行不动它们
    assert cell(instance, "Options", OPT_BASE, "IsDefault") is False, "默认项没被动"
    assert [row.name_label().text() for row in bench.rows()] == ["● Base", "○ LongRange"]
    assert messages[-1] == "已打开「LongRange」（0）"


def test_clicking_the_same_option_again_writes_nothing(
    instance, benches, context, messages
) -> None:
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    click(bench.rows()[1])
    click(bench.rows()[1])
    assert context.changeset.count == 0, "点两次也不该写库（点行只是打开它）"
    assert messages[-1] == "已打开「LongRange」（0）"
    assert cell(instance, "Options", OPT_BASE, "IsDefault") is False, "默认项没被动"


def test_switching_back_clears_the_dirty_entry(instance, benches, context, messages) -> None:
    """改回原值 = 没有改动（Changeset 的口径）。

    真数据里 1520/1521 两条 IsDefault **都是 false**（当前项靠 Order==0 兜底），所以
    点 Base 写进去的是 "false -> true"（真改动），点回 LongRange 那一下把 1521 那条
    **整条消掉** —— 最后只剩 Base 一项是脏的。
    """
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    # ❗ 2026-10-02 起"选中一项"是**写右栏那一格**（点选项行只打开它）—— 这条用例改成直接写那一格。
    context.set_value(CellRef("Options", OPT_LONG, "IsDefault"), True)
    context.set_value(CellRef("Options", OPT_BASE, "IsDefault"), True)
    context.set_value(CellRef("Options", OPT_LONG, "IsDefault"), False)
    assert cell(instance, "Options", OPT_BASE, "IsDefault") is True
    assert cell(instance, "Options", OPT_LONG, "IsDefault") is False
    assert context.changeset.count == 1
    assert context.is_dirty(CellRef("Options", OPT_BASE, "IsDefault")) is True
    assert context.is_dirty(CellRef("Options", OPT_LONG, "IsDefault")) is False
    assert bench.selected_option_id() == OPT_BASE


def test_borrowed_slot_click_writes_nothing(instance, benches, variant_context, messages) -> None:
    """借来的槽（Mod#743 属于单位 108）：点它只发 row_selected，**一个字节都不写**。"""
    bench = benches(SlotsBench, variant_context)
    before = isdefault_snapshot(instance)
    bench.status_message.connect(messages.append)
    seen = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    click(bench.row_for(BORROWED_OTHER))
    assert seen == [("Options", BORROWED_OTHER)]
    assert variant_context.changeset.is_empty is True
    assert isdefault_snapshot(instance) == before
    assert bench.selected_option_id() == BORROWED_CURRENT  # 数据没变 -> ● 不动
    assert "借来的" in messages[-1]
    assert f"#{BORROWED_OWNER}" in messages[-1]


def test_borrowed_slot_head_says_whose_slot_it_is(benches, variant_context) -> None:
    bench = benches(SlotsBench, variant_context)
    assert any(
        "借来的槽（属于单位 #108）" in text and "只给看不给选" in text for text in labels_of(bench)
    )


def test_an_option_row_has_no_arrow_any_more(benches, context) -> None:
    """❗ 翻口径（2026-10-02）：那颗 ↗（去看 `Options` 那一行）按用户要求删了。

    点这一行 = 选中它（写 `Options.IsDefault`）+ 右栏换成它的全部列。
    这一条原来叫 `test_option_link_button_goes_to_the_options_row`。
    """
    bench = benches(SlotsBench, context)
    assert not hasattr(bench.row_for(OPT_LONG), "link_button")
    assert not hasattr(bench, "row_activated")


def test_add_option_goes_to_the_focus_slot(instance, benches, context, messages) -> None:
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    expected = next_row_id(instance.table("Options"), context.changeset)
    bench.add_button().click()
    assert cell(instance, "Options", expected, "ModificationId") == SLOT
    assert cell(instance, "Options", expected, "IsDefault") is False
    assert cell(instance, "Options", expected, "Name") == f"新选项 #{expected}"
    assert bench.selected_option_id() == OPT_BASE  # 新项不抢当前项
    assert [row.name_label().text() for row in bench.rows()] == [
        "● Base",
        "○ LongRange",
        f"○ 新选项 #{expected}",
    ]
    assert "已加一条选项" in messages[-1]


def test_add_option_on_a_borrowed_slot_does_nothing(
    instance, benches, variant_context, messages
) -> None:
    bench = benches(SlotsBench, variant_context)
    bench.status_message.connect(messages.append)
    before = isdefault_snapshot(instance)
    assert bench.add_option() == 0
    assert variant_context.changeset.is_empty is True
    assert isdefault_snapshot(instance) == before
    assert "借来的" in messages[-1]


def test_option_row_shows_its_picture_from_the_store(benches, context) -> None:
    """图标只走 store（外面 set_icons 传进来的那份）—— 这里用一个假 store 钉住族名与键。"""

    class Store:
        def __init__(self) -> None:
            self.asked: list[tuple[str, str]] = []

        def pixmap(self, family: str, key: str) -> QPixmap:
            self.asked.append((family, key))
            pixmap = QPixmap(4, 4)
            pixmap.fill()
            return pixmap

    store = Store()
    bench = benches(SlotsBench, context)
    bench.set_icons(store)
    label = bench.row_for(OPT_BASE).icon_label()
    assert (label.family, label.key) == (OPTION_PICTURE_FAMILY, "INF_CQBR")
    assert label.has_icon() is True
    assert (OPTION_PICTURE_FAMILY, "INF_CQBR") in store.asked


def test_option_cost_uses_the_warm_gold_role(benches, context) -> None:
    """费用那一格走 role `bench-row-cost`（暖金 = tokens 的 cost_text，设计稿 +10 / ±0）。"""
    bench = benches(SlotsBench, context)
    assert [row.value_label().property("role") for row in bench.rows()] == [
        "bench-row-cost",
        "bench-row-cost",
    ]
    assert [row.value_label().text() for row in bench.rows()] == ["+10", "±0"]


# --------------------------------------------------------------------------- #
# 就地换值：结构没变时一个控件都不重建（用户口径：别闪）
# --------------------------------------------------------------------------- #


def _widget_ids(widget) -> set[int]:
    """这一块现在活着的**全部**子控件（一个都不许换）。"""
    from PySide6.QtWidgets import QWidget as _QWidget

    return {id(child) for child in widget.findChildren(_QWidget)}


def test_clicking_an_option_keeps_every_widget(instance, benches, context, messages) -> None:
    """点一项 = 值变了（IsDefault）—— 只许就地换 ● 与费用，控件集合**逐个相同**。"""
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    before = _widget_ids(bench)
    row_ids = [id(row) for row in bench.rows()]
    click(bench.rows()[1])
    assert _widget_ids(bench) == before
    assert [id(row) for row in bench.rows()] == row_ids
    # ❗ selected_option_id() 读的是**数据里**当前那一项 —— 点行不写库，所以它还是 Base
    assert bench.selected_option_id() == OPT_BASE
    # 点行只是打开它：● 不换（数据没动），控件也一个不重建
    assert [row.name_label().text() for row in bench.rows()] == ["● Base", "○ LongRange"]
    assert context.changeset.count == 0


def test_reload_values_rewrites_the_stat_text_in_place(instance, benches, context) -> None:
    """③ 的说明文字（装甲值 / 生命）：改一格之后就地换字，控件与行 Id 都不变。"""
    bench = benches(StatsBench, context)
    before = _widget_ids(bench)
    row_ids = [id(row) for row in bench.rows()]
    context.set_value(CellRef("Armors", ARMOR_LINK, "MaxHealthPoints"), 84)
    bench.reload_values()
    assert _widget_ids(bench) == before
    assert [id(row) for row in bench.rows()] == row_ids
    assert bench.rows()[0].detail_label().text() == "装甲值 10 · 生命 84（= 6 × 14 人）"
    assert bench.lines()[1].detail == "前进 18.0 km/h · 可空投"  # 别的行没被串改


def test_reload_values_rewrites_the_cost_in_place(instance, benches, context) -> None:
    """④ 的费用：模板值改了就换字（role 还是那一支暖金，控件不换）。"""
    bench = benches(SlotsBench, context)
    before = _widget_ids(bench)
    context.set_value(CellRef("Options", OPT_LONG, "Cost"), 25)
    bench.reload_values()
    assert _widget_ids(bench) == before
    assert [row.value_label().text() for row in bench.rows()] == ["+10", "+25"]
    assert [row.value_label().property("role") for row in bench.rows()] == [
        "bench-row-cost",
        "bench-row-cost",
    ]
    assert bench.options()[1].cost_text == "+25"  # 读数也跟着是新的


def test_reload_values_falls_back_to_refresh_when_rows_change(instance, benches, context) -> None:
    """结构真变了（外层的署名本该拦住）：兜一道，退回 refresh() 而不是写错行。"""
    bench = benches(StatsBench, context)
    context.delete_row("UnitAbilities", ABILITY_ROW)
    bench.reload_values()
    assert [line.table for line in bench.lines()] == ["UnitArmors", "UnitPropulsions"]
    assert len(bench.rows()) == 2


# --------------------------------------------------------------------------- #
# 装配接口（Lead 按这个接口装）
# --------------------------------------------------------------------------- #


def test_both_benches_expose_the_assembly_interface() -> None:
    """两块共用的那套接口（页面只认这几个名字）。

    ❗ 翻口径（2026-10-02）：`row_activated`（行尾 ↗「去看这一行」）按用户要求删了 ——
    接口从三个信号降到两个；这一条**改成负向**，免得它哪天悄悄长回来。
    """
    for cls in (StatsBench, SlotsBench):
        for name in ("row_selected", "status_message"):
            assert hasattr(cls, name), f"{cls.__name__} 少了信号 {name}"
        assert not hasattr(cls, "row_activated"), f"{cls.__name__} 又长回了 row_activated"
        for name in ("set_context", "refresh", "reload_values", "set_icons"):
            assert callable(getattr(cls, name)), f"{cls.__name__} 少了 {name}()"


def test_section_titles_match_the_design() -> None:
    """抬头默认值**不带编号**（拆块之后编号 = 摆第几块，由 bench_page 生成）。

    ❗ 除了字面量，这里还钉一条**推导**：三类都画时，拼出来的抬头就等于那两个常量 ——
    常量与 stat_block_title 不许各说各的（从前是 ③ 那个写死的编号在当单一真相）。
    """
    assert (STATS_TITLE, STATS_HINT) == (
        "装甲 / 机动 / 能力",
        "UnitArmors · UnitPropulsions · UnitAbilities",
    )
    assert (SLOTS_TITLE, SLOTS_HINT) == (
        "改装槽（槽内互斥）",
        "Modifications / Options · 选中哪个决定这个班变成什么",
    )
    assert vm.stat_block_title(vm.STAT_KINDS) == STATS_TITLE
    assert vm.stat_block_hint(vm.STAT_KINDS) == STATS_HINT
    assert vm.stat_block_title((vm.SENSOR_KIND,)) == "传感器"
    assert vm.stat_block_title((vm.SPEC_KIND,)) == "专精可用性"
    assert vm.stat_block_hint((vm.SPEC_KIND,)) == "SpecializationAvailabilities"


# --------------------------------------------------------------------------- #
# 折叠态（飞机）：一行一个槽、只显示当前选中项，候选就地展开
#   —— 设计图 docs/设计稿-编成台-飞机.png 的 ②（它自己标的那句 ⚠️：31 个选项铺不下）
# --------------------------------------------------------------------------- #

#: F-15E（单位 287）的 5 个槽（探针跑出来的 Modifications 行 Id，改数据源要跟着改）。
AIR_SLOTS = (308, 307, 306, 305, 543)
#: 那 5 个槽里一共几条选项 —— 就是"铺不下"的那个数。
AIR_OPTION_COUNT = 31
#: 载具 / 飞机那份抬头（设计图把这一块编成 ②；步兵是 ④ —— 见 SlotsBench.__init__）。
AIR_SLOTS_TITLE = "② 改装槽（槽内互斥）"
AIR_SLOTS_HINT = "Modifications / Options · 一行一个槽，展开再选"


@pytest.fixture()
def air_context(instance):
    return make_context(instance, UNIT_AIR)


def test_collapsed_defaults_to_the_old_behaviour(benches, context) -> None:
    """**不传 collapsed = 加这一手之前那一态**（步兵 / 载具）：逐字不变。"""
    bench = benches(SlotsBench, context)
    assert bench.is_collapsed() is False
    assert bench.collapsed is False
    assert bench.slot_rows() == ()
    assert bench.expand_button() is None
    assert bench.toggle_slot(SLOT) is False
    assert bench.is_expanded(SLOT) is False
    assert bench.expanded_slots() == ()
    # 非折叠态"全部选项都已经在界面上"，所以候选读数 = 那几行本身（不是空元组）
    assert bench.candidate_rows(SLOT) == bench.rows()
    assert [row.name_label().text() for row in bench.rows()] == ["● Base", "○ LongRange"]
    assert any("2 个选项" in text for text in labels_of(bench))  # 灰字抬头还是老样子


def test_collapsed_bench_draws_one_row_per_slot(benches, air_context) -> None:
    """F-15E：5 个槽、31 个选项 —— 折叠态每个槽**只有一行**，候选一个都没建。"""
    bench = benches(SlotsBench, air_context, collapsed=True)
    assert bench.is_collapsed() is True
    assert bench.group_titles() == (
        "Weapons Centerline",
        "Weapons Fuselage",
        "Weapon WingPylons2",
        "Weapon WingPylons1",
        "Ability",
    )
    rows = bench.slot_rows()
    assert len(rows) == 5
    assert [row.id_label().text() for row in rows] == ["#308", "#307", "#306", "#305", "#543"]
    assert [row.toggle_button().text() for row in rows] == [
        "▸ 展开候选（7 项）",
        "▸ 展开候选（8 项）",
        "▸ 展开候选（10 项）",
        "▸ 展开候选（4 项）",
        "▸ 展开候选（2 项）",
    ]
    # 中间那段 = N 选 1 · ● 当前项 · 效果（真数据钉一条，其余按 viewmodel 推）
    assert rows[0].detail_label().text() == "7 选 1 · ● Empty · 炮塔挂载槽 槽 19 · Empty"
    expected = []
    for group in bench.groups():
        selected = group.selected_option
        assert selected is not None
        expected.append(f"{group.option_count} 选 1 · ● {selected.name} · {selected.detail}")
    assert [row.detail_label().text() for row in rows] == expected
    assert [row.cost_label().text() for row in rows] == ["±0", "+120", "+140", "+45", "±0"]
    assert bench.rows() == ()  # 一个候选行都还没建
    assert all(bench.candidate_rows(mid) == () for mid in AIR_SLOTS)
    assert bench.expanded_slots() == ()
    assert all(row.selected is False for row in rows)
    assert bench.selected_option_id() == 900  # 折叠态也得答得出来（扫 groups 不扫 rows）


def test_expanding_a_slot_builds_the_candidates_in_place(benches, context) -> None:
    """点「展开候选」= 就地建出那几行；槽那一行**一个控件都不换**（AGENTS §0.7）。"""
    bench = benches(SlotsBench, context, collapsed=True)
    row = bench.slot_row(SLOT)
    assert row is not None
    kids = _widget_ids(row)
    button = bench.expand_button()  # 不给槽 Id = 第一个槽
    assert button is bench.expand_button(SLOT)
    assert bench.expand_buttons() == (button,)
    assert button.text() == "▸ 展开候选（2 项）"
    click(button)
    assert bench.is_expanded(SLOT) is True
    assert bench.expanded_slots() == (SLOT,)
    assert bench.slot_row(SLOT) is row
    assert _widget_ids(row) == kids  # 槽那一行没被重建
    assert button.text() == "▾ 收起候选（2 项）"
    # ❗ 翻口径（2026-10-02）：展开态**不再整行高亮**（高亮只留给页面选中的那一行）——
    #    上面那句 button.text() 与下面真的多出来的候选行才是判据。
    assert row.selected is False
    assert [item.name_label().text() for item in bench.candidate_rows(SLOT)] == [
        "● Base",
        "○ LongRange",
    ]
    assert bench.rows() == bench.candidate_rows(SLOT)
    assert [item.value_label().text() for item in bench.candidate_rows(SLOT)] == ["+10", "±0"]


def test_clicking_the_slot_row_toggles_and_keeps_the_widgets(benches, context) -> None:
    """点行体 = 展开，再点 = 收起；收起只切可见性，那几行**留着**（下次展开不再建）。"""
    bench = benches(SlotsBench, context, collapsed=True)
    # ❗ 折叠栏默认收起（用户 2026-10-02：「折叠栏默认是折叠状态」）：这条验的是**槽那一行**
    #    的展开 / 收起，所以先把"整块"展开再断言 isVisibleTo —— 否则整块主体藏着，
    #    那几行永远判 False。⛔ 不许把断言放宽成"看不见也算过"（那是假绿）。
    bench.section().set_collapsed(False)
    row = bench.slot_row(SLOT)
    click(row)
    assert bench.is_expanded(SLOT) is True
    candidates = bench.candidate_rows(SLOT)
    kept = [id(item) for item in candidates]
    assert all(item.isVisibleTo(bench) for item in candidates)
    click(row)
    assert bench.is_expanded(SLOT) is False
    assert bench.slot_row(SLOT) is row
    assert [id(item) for item in bench.candidate_rows(SLOT)] == kept  # 控件留着
    assert not any(item.isVisibleTo(bench) for item in candidates)  # 只是藏起来了
    assert bench.expand_button(SLOT).text() == "▸ 展开候选（2 项）"
    assert row.selected is False
    assert bench.collapse_slot(SLOT) is False  # 已经收起 -> 不再动
    assert bench.expand_slot(SLOT) is True
    assert bench.expand_slot(SLOT) is True  # 已经展开 -> 幂等，不重建


def test_clicking_a_candidate_in_collapsed_mode_selects_it(
    instance, benches, context, messages
) -> None:
    """展开态点一项 = **只在右栏打开它**（不写库），槽那一行与候选行**就地**跟上。"""
    bench = benches(SlotsBench, context, collapsed=True)
    bench.status_message.connect(messages.append)
    click(bench.expand_button())
    before = _widget_ids(bench)
    rows_before = [id(item) for item in bench.rows()]
    click(bench.candidate_rows(SLOT)[1])  # LongRange（同槽另一项）
    assert context.changeset.count == 0, "点候选只是打开它"
    assert cell(instance, "Options", OPT_BASE, "IsDefault") is False, "数据一个字节都没动"
    # selected_option_id() 读的是**数据里**当前那一项（点行不写库，所以它没变）
    assert bench.selected_option_id() == OPT_BASE, "当前项没被动（点行只是打开它）"
    assert [item.name_label().text() for item in bench.candidate_rows(SLOT)] == [
        "● Base",
        "○ LongRange",
    ]
    assert bench.slot_row(SLOT).detail_label().text().startswith("2 选 1 · ● Base")
    assert _widget_ids(bench) == before  # 一个控件都没重建
    assert [id(item) for item in bench.rows()] == rows_before
    assert messages[-1] == "已打开「LongRange」（0）"
    assert bench.is_expanded(SLOT) is True  # 选中不把展开态吃掉


def test_collapsed_reload_values_rewrites_in_place_while_expanded(
    instance, benches, context
) -> None:
    """**展开态下值变了**：就地改字（槽那一行 + 候选行 + 费用），控件不换。"""
    bench = benches(SlotsBench, context, collapsed=True)
    click(bench.expand_button())
    before = _widget_ids(bench)
    context.set_value(CellRef("Options", OPT_BASE, "Cost"), 30)
    context.set_value(CellRef("Options", OPT_LONG, "Cost"), 25)
    bench.reload_values()
    assert _widget_ids(bench) == before
    assert [item.value_label().text() for item in bench.candidate_rows(SLOT)] == ["+30", "+25"]
    assert [item.value_label().property("role") for item in bench.candidate_rows(SLOT)] == [
        "bench-row-cost",
        "bench-row-cost",
    ]
    # 槽那一行写的是**当前选中项**的费用与效果 —— 跟着数据走
    assert bench.slot_row(SLOT).cost_label().text() == "+30"
    assert bench.options()[0].cost_text == "+30"
    detail = bench.slot_row(SLOT).detail_label().text()
    assert "选中 → 这个班变成 #70 Marine Raiders" in detail


def test_collapsed_reload_values_falls_back_when_options_change(instance, benches, context) -> None:
    """结构真变了（删了一条选项）：退回 refresh()，而且**展开态不许丢**。"""
    bench = benches(SlotsBench, context, collapsed=True)
    click(bench.expand_button())
    context.delete_row("Options", OPT_LONG)
    bench.reload_values()
    assert bench.is_expanded(SLOT) is True
    assert [item.name_label().text() for item in bench.candidate_rows(SLOT)] == ["● Base"]
    assert bench.expand_button(SLOT).text() == "▾ 收起候选（1 项）"


def test_collapsed_add_option_keeps_the_expanded_slot(instance, benches, context, messages) -> None:
    """「＋ 加选项」加的是**展开着的那个槽**（展开顺手记成焦点），加完展开态不丢。"""
    bench = benches(SlotsBench, context, collapsed=True)
    bench.status_message.connect(messages.append)
    expected = next_row_id(instance.table("Options"), context.changeset)
    click(bench.expand_button())  # 展开 = 同时成为「＋ 加选项」的焦点
    click(bench.add_button())
    assert bench.focus_slot() == SLOT
    assert bench.is_expanded(SLOT) is True
    assert cell(instance, "Options", expected, "IsDefault") is False
    assert [item.name_label().text() for item in bench.candidate_rows(SLOT)] == [
        "● Base",
        "○ LongRange",
        f"○ 新选项 #{expected}",
    ]
    assert "已加一条选项" in messages[-1]


def test_collapsed_air_all_five_slots_expand_to_thirty_one_options(benches, air_context) -> None:
    """真数据：5 个槽全展开 = 31 行 —— 这正是"一行一个槽"要解决的那件事。"""
    bench = benches(SlotsBench, air_context, collapsed=True)
    assert [group.option_count for group in bench.groups()] == [7, 8, 10, 4, 2]
    for mid in AIR_SLOTS:
        assert bench.expand_slot(mid) is True
    assert bench.expanded_slots() == AIR_SLOTS
    assert [len(bench.candidate_rows(mid)) for mid in AIR_SLOTS] == [7, 8, 10, 4, 2]
    assert len(bench.rows()) == AIR_OPTION_COUNT
    assert len(bench.expand_buttons()) == 5
    texts = [button.text() for button in bench.expand_buttons()]
    assert texts == [
        "▾ 收起候选（7 项）",
        "▾ 收起候选（8 项）",
        "▾ 收起候选（10 项）",
        "▾ 收起候选（4 项）",
        "▾ 收起候选（2 项）",
    ]


def test_a_collapsed_slot_row_has_no_arrow_any_more(benches, context) -> None:
    """❗ 翻口径（2026-10-02）：槽那一行的 ↗（去看 `Modifications` 那一行）删了。

    槽那一行现在只剩「展开候选 / 收起候选」那颗 ＋ 行体本身（点它 = 展开）。
    这一条原来叫 `test_collapsed_slot_row_link_goes_to_the_modifications_row`。
    """
    bench = benches(SlotsBench, context, collapsed=True)
    assert not hasattr(bench.slot_row(SLOT), "link_button")
    assert not hasattr(bench, "row_activated")


def test_collapsed_slot_row_shows_the_current_option_picture(benches, context) -> None:
    """槽那一行的图标 = **当前选中那一项**的图；图晚到就地补，不重建。"""

    class Store:
        def __init__(self) -> None:
            self.asked: list[tuple[str, str]] = []

        def pixmap(self, family: str, key: str) -> QPixmap:
            self.asked.append((family, key))
            pixmap = QPixmap(4, 4)
            pixmap.fill()
            return pixmap

    store = Store()
    bench = benches(SlotsBench, context, collapsed=True)
    before = _widget_ids(bench)
    bench.set_icons(store)  # 候选还没展开 —— 也不许整块重建
    assert _widget_ids(bench) == before
    label = bench.slot_row(SLOT).icon_label()
    assert (label.family, label.key) == (OPTION_PICTURE_FAMILY, "INF_CQBR")
    assert label.has_icon() is True
    assert (OPTION_PICTURE_FAMILY, "INF_CQBR") in store.asked


def test_collapsed_borrowed_slot_warns_on_its_row_and_writes_nothing(
    instance, benches, variant_context, messages
) -> None:
    """借来的槽（743 属于单位 108）：那一行自己写着"只给看不给选"，点候选不写字节。"""
    bench = benches(SlotsBench, variant_context, collapsed=True)
    before = isdefault_snapshot(instance)
    bench.status_message.connect(messages.append)
    detail = bench.slot_row(BORROWED_SLOT).detail_label().text()
    assert "借来的槽（属于单位 #108）" in detail
    assert "只给看不给选" in detail
    assert detail.startswith("3 选 1 · ● HE")
    assert bench.expand_slot(BORROWED_SLOT) is True
    click(bench.row_for(BORROWED_OTHER))
    assert variant_context.changeset.is_empty is True
    assert isdefault_snapshot(instance) == before
    assert bench.selected_option_id() == BORROWED_CURRENT  # 数据没变 -> 当前项不动
    assert "借来的" in messages[-1]


def test_section_title_and_hint_are_passed_per_form(benches, context) -> None:
    """抬头编号按形态传：载具 / 飞机 = ②（设计图），步兵 = ④（默认值，一个字没动）。"""
    default = benches(SlotsBench, context)
    assert default.section_title() == SLOTS_TITLE
    assert default.section_hint() == SLOTS_HINT
    air = benches(SlotsBench, context, collapsed=True, title=AIR_SLOTS_TITLE, hint=AIR_SLOTS_HINT)
    assert air.section_title() == AIR_SLOTS_TITLE
    assert air.section_hint() == AIR_SLOTS_HINT
    assert AIR_SLOTS_TITLE in labels_of(air)
    assert SLOTS_TITLE not in labels_of(air)  # 默认那一段不许漏在里面


def test_collapsed_without_context_is_a_hint(benches) -> None:
    """没接上下文：折叠态也是一句空话，不给"点了没反应"的空行。"""
    bench = benches(SlotsBench, None, collapsed=True)
    assert bench.slot_rows() == ()
    assert bench.expand_button() is None
    assert bench.toggle_slot(SLOT) is False
    assert any("还没有选中单位" in text for text in labels_of(bench))


# --------------------------------------------------------------------------- #
# 折叠栏 ①「块」本身（用户 2026-10-02）：默认收起、点抬头展开 / 收起，控件一个都不重建
#   —— 属性栏那五栏与改装槽都在这一份里（它们同属 bench_slots.py）。
# --------------------------------------------------------------------------- #


def test_the_stats_block_is_collapsed_by_default_and_toggles(benches, context) -> None:
    """③ 这块默认收起；点抬头展开、再点收起 —— `body_widgets()` 前后是**同一批控件**。"""
    bench = benches(StatsBench, context)
    section = bench.section()

    assert section.collapsible()
    assert section.is_collapsed() is True
    assert section.caret_label().text() == "▸"
    kept = section.body_widgets()
    assert kept, "三行关联行该在主体里 —— 不然这条守卫是空转"

    click(section.head())

    assert section.is_collapsed() is False
    assert section.caret_label().text() == "▾"
    assert section.body_widgets() == kept, "展开不许重建控件"

    click(section.head())

    assert section.is_collapsed() is True
    assert section.body_widgets() == kept, "收起同样不许重建控件"


def test_the_stats_picker_lives_inside_the_block_body(benches, context) -> None:
    """候选宿主在**主体**里（回归闸）：挂回主体外，"收起这一块"就藏不住它。"""
    bench = benches(StatsBench, context)
    click(bench.add_buttons()[2])
    picker = bench.picker()
    assert picker is not None
    assert bench.section().body().isAncestorOf(picker)


def test_the_slots_block_is_collapsed_by_default_and_toggles(benches, context) -> None:
    """④ 这块默认收起（步兵 / 载具那一态 —— 飞机那套 collapsed 是另一回事）；点抬头换态。"""
    bench = benches(SlotsBench, context)
    section = bench.section()

    assert bench.collapsed is False, "前提：这一条验的是**非折叠态**的块"
    assert section.is_collapsed() is True
    kept = section.body_widgets()
    assert kept

    click(section.head())

    assert section.is_collapsed() is False
    assert section.body_widgets() == kept

    click(section.head())

    assert section.is_collapsed() is True
    assert section.body_widgets() == kept


# --------------------------------------------------------------------------- #
# 折叠栏 ② 块内分组（非折叠态 + **≥2 个槽**）：默认收起、点那条灰字抬头展开 / 收起
#   用户口径：「只有一条的不套折叠，纯粹的画蛇添足」—— 只 1 个槽的块内不折。
# --------------------------------------------------------------------------- #


@pytest.fixture()
def vehicle_context(instance):
    """改装槽 ≥ 2 个的载具（单位 6：3 个槽各 2 个选项）—— 块内折叠只在这种块上存在。"""
    return make_context(instance, UNIT_SLOTS)


def test_a_single_slot_does_not_fold_inside(benches, context) -> None:
    """用户口径「只有一条的不套折叠」：单位 70 只 1 个槽 -> 块内不折、连记号都不给。"""
    bench = benches(SlotsBench, context)

    assert bench.groups_are_folded() is False
    assert bench.is_group_expanded(SLOT) is False
    assert bench.toggle_group(SLOT) is False
    assert bench.group_body(SLOT) is None
    head = bench.group_head(SLOT)
    assert head is not None
    assert head.collapsible() is False and head.caret_label().isHidden()
    # 那一条灰字抬头照旧平铺、选项行照旧一行行列着（与加折叠之前逐字一致）
    assert [row.name_label().text() for row in bench.rows()] == ["● Base", "○ LongRange"]


def test_two_or_more_slots_fold_inside_and_default_to_collapsed(benches, vehicle_context) -> None:
    """单位 6（3 个槽）：每槽那条灰字抬头可点、**默认收起**；选项行在 refresh 时就建好了。"""
    bench = benches(SlotsBench, vehicle_context)
    # ❗ 整块先展开（折叠栏默认收起）：不展开的话 `isVisibleTo` 恒 False，这条就成空转了。
    bench.section().set_collapsed(False)

    assert bench.groups_are_folded() is True
    assert bench.group_titles() == ("Weapons", "Armor", "Ability")
    assert bench.expanded_groups() == ()
    for mid in SLOT_IDS:
        head, body = bench.group_head(mid), bench.group_body(mid)
        assert head is not None and body is not None
        assert head.collapsible() is True
        assert head.caret_label().text() == "▸"
        assert not body.isVisibleTo(bench), "默认收起 = 那几行藏着"
        # 收起 ≠ 不建：控件都在，展开只是 setVisible(True)（AGENTS §0.7）
        assert len(bench.candidate_rows(mid)) == 2
    assert len(bench.rows()) == 6

    click(bench.group_head(SLOT_IDS[0]))

    assert bench.is_group_expanded(SLOT_IDS[0]) is True
    assert bench.group_body(SLOT_IDS[0]).isVisibleTo(bench)
    assert bench.group_head(SLOT_IDS[0]).caret_label().text() == "▾"
    assert bench.expanded_groups() == (SLOT_IDS[0],)
    assert not bench.group_body(SLOT_IDS[1]).isVisibleTo(bench), "别的槽不受影响"

    click(bench.group_head(SLOT_IDS[0]))

    assert bench.is_group_expanded(SLOT_IDS[0]) is False
    assert not bench.group_body(SLOT_IDS[0]).isVisibleTo(bench)
    assert bench.group_head(SLOT_IDS[0]).caret_label().text() == "▸"


def test_folding_a_group_keeps_every_widget_and_writes_nothing(
    instance, benches, vehicle_context
) -> None:
    """展开 / 收起一个槽：那几行**一个控件都不重建**，而且一个字节都不写库。"""
    bench = benches(SlotsBench, vehicle_context)
    bench.section().set_collapsed(False)
    before = _widget_ids(bench)
    row_ids = [id(row) for row in bench.rows()]
    body = bench.group_body(SLOT_IDS[0])

    click(bench.group_head(SLOT_IDS[0]))
    assert _widget_ids(bench) == before
    assert [id(row) for row in bench.rows()] == row_ids
    assert bench.group_body(SLOT_IDS[0]) is body

    click(bench.group_head(SLOT_IDS[0]))
    assert _widget_ids(bench) == before
    assert bench.group_body(SLOT_IDS[0]) is body
    assert vehicle_context.changeset.is_empty is True, "折叠是纯界面态，一个字都不许写"


def test_the_expanded_group_survives_a_refresh(benches, vehicle_context) -> None:
    """展开态跨 refresh 留着（关系树那条口径：「以后每次重画都恢复用户当时的展开态」）。"""
    bench = benches(SlotsBench, vehicle_context)
    bench.section().set_collapsed(False)
    bench.expand_group(SLOT_IDS[0])

    bench.refresh()

    assert bench.is_group_expanded(SLOT_IDS[0]) is True
    assert bench.group_body(SLOT_IDS[0]).isVisibleTo(bench)
    assert bench.is_group_expanded(SLOT_IDS[1]) is False


def test_the_expanded_group_survives_a_unit_switch(instance, benches) -> None:
    """展开态跨**换单位**也留着（槽 Id 全局唯一，不会串到另一台车身上）。"""
    bench = benches(SlotsBench, make_context(instance, UNIT_SLOTS))
    bench.expand_group(SLOT_IDS[0])

    bench.set_context(make_context(instance, 21))  # 换一台车（2 个槽：#19 / #5）
    assert bench.groups_are_folded() is True
    assert bench.is_group_expanded(SLOT_IDS[0]) is False, "这台车上没有那个槽"
    assert bench.is_group_expanded(19) is False

    bench.set_context(make_context(instance, UNIT_SLOTS))
    assert bench.is_group_expanded(SLOT_IDS[0]) is True, "回到原来的车，展开态要回来"


# --------------------------------------------------------------------------- #
# 折叠栏 ③ 块内分组抬头的**外观**（用户 2026-10-02：「改装槽的折叠栏太小了」）
#   一条正经的行：row_height、整条可点 + hover、槽名 md+bold+secondary（比块抬头弱一档）、
#   记号单占一格；图标位 = 游戏里那张改装按钮图（收起态也在）。
# --------------------------------------------------------------------------- #


class _FakeIconStore(QObject):
    """假图标缓存（与 test_bench_loadout.FakeStore 同一套）：记下问过哪张图，给一张小图。

    ⛔ 用例**不 new IconCache**（那会起加载线程、按住 bundle）。
    """

    loaded = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, str]] = []

    def pixmap(self, family: str, key: str) -> QPixmap:
        self.asked.append((family, key))
        pixmap = QPixmap(8, 8)
        pixmap.fill()
        return pixmap


class _LazyIconStore(_FakeIconStore):
    """图**还没解析出来**的假缓存：`loaded` 一发才有图（模拟异步到货那一路）。"""

    def __init__(self) -> None:
        super().__init__()
        self.ready = False

    def pixmap(self, family: str, key: str) -> QPixmap:
        self.asked.append((family, key))
        if not self.ready:
            return QPixmap()  # 空 = 还没到
        pixmap = QPixmap(8, 8)
        pixmap.fill()
        return pixmap


def test_the_group_head_is_a_real_clickable_row(instance, benches, vehicle_context) -> None:
    """抬头是一条**正经的行**：高度 row_height、整条可点（左端 / 右端都算）、控件不重建。"""
    bench = benches(SlotsBench, vehicle_context)
    bench.section().set_collapsed(False)  # 块先展开：这一条验的是组抬头本身
    head = bench.group_head(SLOT_IDS[0])
    assert head is not None

    assert head.property("role") == "bench-group-head"
    assert head.minimumHeight() == TOKENS.size.row_height == head.maximumHeight()
    assert head.caret_label().property("role") == "bench-section-caret"
    assert head.caret_label().text() == "▸"
    assert head.count_label().text() == "2 个选项"
    assert head.width() > 4, "前提：整条可点 —— 它得真的有宽度"

    body = bench.group_body(SLOT_IDS[0])
    before = _widget_ids(bench)
    # 整条可点：贴着最右边那一点（「2 个选项」那一段）也算
    QTest.mouseClick(
        head, Qt.MouseButton.LeftButton, pos=QPoint(head.width() - 2, head.height() // 2)
    )
    assert bench.is_group_expanded(SLOT_IDS[0]) is True and body.isVisibleTo(bench)
    assert _widget_ids(bench) == before, "展开不许重建控件"
    # 再点贴着记号那一头，照样收得回去
    QTest.mouseClick(head, Qt.MouseButton.LeftButton, pos=QPoint(2, head.height() // 2))
    assert bench.is_group_expanded(SLOT_IDS[0]) is False
    assert not body.isVisibleTo(bench)
    assert _widget_ids(bench) == before


def test_the_group_head_title_is_not_a_footnote_any_more(benches, vehicle_context) -> None:
    """抬头文字不再走 `bench-section-hint`（9px muted 的脚注档）—— 用户嫌的就是它太小。"""
    from PySide6.QtWidgets import QLabel

    bench = benches(SlotsBench, vehicle_context)
    head = bench.group_head(SLOT_IDS[0])
    assert head is not None

    assert head.title_label().property("role") == "bench-group-title"
    assert head.title_label().text() == "Weapons"
    roles = [label.property("role") for label in head.findChildren(QLabel)]
    assert "bench-section-hint" not in roles, "脚注档那条样式不许再出现在这条抬头里"
    # 槽名 / 「N 个选项」/ 说明各占一格（说明是弱档：拼一起就把它染成槽名那么重）
    assert head.count_label().property("role") == "entry-count"
    assert head.count_label().text() == "2 个选项"
    assert head.note_label().property("role") == "entry-count"
    assert head.note_label().text() == ""


def test_the_armor_slot_head_wears_the_icon_row_icon_gives(instance, benches) -> None:
    """用户「我记得改装槽是有它的图标的」：装甲槽（Type=5）那颗 = `row_icon` 给的那张。"""
    store = _FakeIconStore()
    bench = benches(SlotsBench, make_context(instance, UNIT_SLOTS))
    bench.set_icons(store)
    head = bench.group_head(39)  # Armor（Type=5）
    expected = row_icon(instance, "Modifications", 39)
    assert expected is not None
    assert (head.icon_family(), head.icon_key()) == (expected.family, expected.key)
    assert head.has_real_icon() is True
    assert (expected.family, expected.key) in store.asked


def test_the_other_slots_wear_their_own_icon_not_the_armor_one(instance, benches) -> None:
    """别的槽（Type≠5）走的是 `row_icon` 给的**另一张**（齿轮），而且两张确实不同。"""
    store = _FakeIconStore()
    bench = benches(SlotsBench, make_context(instance, UNIT_SLOTS))
    bench.set_icons(store)
    for mid in (41, 565):
        head = bench.group_head(mid)
        expected = row_icon(instance, "Modifications", mid)
        assert expected is not None
        assert (head.icon_family(), head.icon_key()) == (expected.family, expected.key)
    armor = row_icon(instance, "Modifications", 39)
    other = row_icon(instance, "Modifications", 41)
    assert armor is not None and other is not None and armor.key != other.key, (
        "两个键一样的话，上面那两条断言是空转"
    )


def test_the_head_icon_stays_visible_while_the_group_is_collapsed(instance, benches) -> None:
    """图标是**抬头的身份**（不是内容）：这一组收起着，它照旧画在那一行上。"""
    bench = benches(SlotsBench, make_context(instance, UNIT_SLOTS))
    bench.section().set_collapsed(False)  # 块展开（组本来就是默认收起）
    bench.set_icons(_FakeIconStore())
    head = bench.group_head(39)
    assert bench.is_group_expanded(39) is False
    assert head.icon_label().isVisibleTo(bench) is True, "收起态图标也要看得见"


def test_a_head_without_an_icon_store_falls_back_to_the_table_emoji(
    benches, vehicle_context
) -> None:
    """没接 store（没装游戏 / 用例构造）：不炸 —— 退回**表 emoji**，别留空框。

    Modifications 那张表的 emoji 是 🔧（`table_emoji` 给的，用例按它断言）。
    """
    bench = benches(SlotsBench, vehicle_context)  # 没 set_icons
    head = bench.group_head(SLOT_IDS[0])
    assert head is not None
    assert head.has_real_icon() is False
    assert head.mark_visible() is True
    assert head.mark_emoji() == table_emoji("Modifications")
    assert head.emoji_label().text() == table_emoji("Modifications")
    assert head.icon_label().isHidden() is True, "真图位这时是藏着的（图标位只能有一个）"


def test_the_icon_lands_in_place_when_the_store_finishes(instance, benches) -> None:
    """图是**异步**到的：`store.loaded` 一到就从 emoji 换成真图，控件一个都不重建。"""
    bench = benches(SlotsBench, make_context(instance, UNIT_SLOTS))
    head = bench.group_head(39)
    before = _widget_ids(bench)
    assert head.has_real_icon() is False and head.emoji_label().isHidden() is False

    store = _LazyIconStore()
    bench.set_icons(store)
    assert head.has_real_icon() is False, "图还没解析出来 —— 先照 emoji 画"

    store.ready = True
    store.loaded.emit()

    assert head.has_real_icon() is True
    assert head.emoji_label().isHidden() is True
    assert _widget_ids(bench) == before, "换图标不许重建控件"


# --------------------------------------------------------------------------- #
# 第十轮（2026-10-02）：② **增删整槽** —— 加一个空槽 / 删掉整个槽（级联它的选项、一条撤销）
#   用户原话：「左卡不加，但增删整槽也加！」（左卡继续只读、一个按钮都不加，那一半不做）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def ka_context(instance):
    """Ka-52（单位 46）—— 直升机走**折叠态**（一行一个槽），进门就是三个槽。"""
    return make_context(instance, UNIT_KA)


def rows_snapshot(instance, table, row_id):
    """整行原样快照（撤销放回来要**逐字节**一样，不只是"Id 还在"）。"""
    rows = instance.table(table)
    index = rows.column_index("Id")
    for row in rows.rows:
        if row[index] == row_id:
            return tuple(row)
    raise AssertionError(f"{table} 里没有 #{row_id}")


# ---- 取数那两条纯函数 -------------------------------------------------------- #


def test_option_ids_of_lists_every_option_of_that_slot(instance) -> None:
    """级联名单只有一条判据：`Options.ModificationId == 这个槽`（全库只有这一列引用它）。"""
    data = data_of(instance)
    assert vm.option_ids_of(data, 75) == KA_OPTIONS[75]
    assert vm.option_ids_of(data, 76) == KA_OPTIONS[76]
    assert vm.option_ids_of(data, 999_999) == (), "没有这个槽 = 空名单，不是异常"


def test_new_slot_values_stand_on_their_own(instance) -> None:
    """新槽只填 `Modifications` 那 7 列；`Order` = **该单位现有 Order 的最大值 + 1**。"""
    data = data_of(instance)
    values = vm.new_slot_values(data, UNIT_KA)
    assert set(values) == {
        "Id",
        "UnitId",
        "Name",
        "UIName",
        "Type",
        "ThumbnailFileName",
        "Order",
    }
    assert values["UnitId"] == UNIT_KA
    assert values["Name"] == f"新改装槽 #{values['Id']}"
    assert (values["UIName"], values["Type"], values["ThumbnailFileName"]) == ("", 0, "")
    # 实测：单位 46 三个槽的 Order 全是 0 -> 新槽写 1；
    # 单位 6 = 1 / 0 / 2 -> 写 3（**不是"有几个槽就写几"**，删过槽之后两个数会分叉）。
    assert values["Order"] == 1
    assert vm.new_slot_values(data, UNIT_SLOTS)["Order"] == 3


def test_new_slot_values_never_reuse_a_row_deleted_this_session(instance) -> None:
    """删掉最大 Id 那个槽之后，新槽不许捡回它的 Id（core/edit/clone.py::next_row_id 的口径）。"""
    context = make_context(instance, UNIT_KA)
    rows = instance.table("Modifications")
    biggest = max(row[0] for row in rows.rows)
    context.changeset.delete_row("Modifications", biggest)

    values = vm.new_slot_values(context.data, UNIT_KA, changeset=context.changeset)

    assert values["Id"] == biggest + 1
    assert values["Id"] > max(row[0] for row in rows.rows), "捡回了刚删掉的那个 Id"


# ---- 加一个槽 ---------------------------------------------------------------- #


def test_add_slot_inserts_one_empty_slot_and_points_the_focus_at_it(
    instance, benches, ka_context, messages
) -> None:
    """「＋ 加一个槽」= 插一行 `Modifications`（UnitId / Order 对），**一条选项都不建**。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    before = len(bench.slot_rows())
    expected = next_row_id(instance.table("Modifications"), ka_context.changeset)

    click(bench.add_slot_button())

    assert has_row(instance, "Modifications", expected)
    assert cell(instance, "Modifications", expected, "UnitId") == UNIT_KA
    assert cell(instance, "Modifications", expected, "Order") == 1
    assert vm.option_ids_of(ka_context.data, expected) == (), "新槽是空的（一条选项都没有）"
    assert len(bench.slot_rows()) == before + 1
    assert bench.focus_slot() == expected, "焦点要落到新槽上（下一步多半是往里加选项）"
    assert ka_context.undo_depth == 1, "加一个槽是**一条**撤销"
    assert "空的" in messages[-1] and f"#{expected}" in messages[-1]


def test_add_option_selects_the_new_row_so_the_rail_lands_on_it(
    instance, benches, ka_context, messages
) -> None:
    """⭐ 用户 2026-10-02：「改装槽里的加选项……改到 Options 上」。

    加完那一条**当场就是它**：发一次 `row_selected("Options", 新行Id)`，右栏直接落到
    这条 Options 行（那 46 列就在眼前）。从前只写一句状态栏"点它才会选中"，
    右栏还停在上一条选中项上 —— 用户加完还得在列表里再找一次。
    """
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    expected = next_row_id(instance.table("Options"), ka_context.changeset)
    bench.expand_slot(75)  # 展开 = 记成「＋ 加选项」的焦点

    click(bench.add_button())  # 真点那颗按钮（不是 emit 冒充）

    assert seen == [(vm.OPTIONS_TABLE, expected)], f"加完没把右栏切到新 Options 行：{seen}"
    assert cell(instance, "Options", expected, "ModificationId") == 75, "新行要落在焦点槽里"
    assert f"#{expected}" in messages[-1] and "右栏就是它" in messages[-1]
    # 页面上那一份选中态也翻到新行（钉控件里那份状态，不现算）
    rows = bench.candidate_rows(75)
    assert rows[-1].selected is True
    assert sum(1 for row in rows if row.selected) == 1, "一屏只有一个选中"


def test_add_slot_selects_the_new_slot_row_too(instance, benches, ka_context, messages) -> None:
    """同上：加完**一整槽**也当场切到那条 `Modifications` 行（状态栏正是让人"去右栏改名字"）。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    new_id = bench.add_slot()

    assert seen == [(vm.MODIFICATIONS_TABLE, new_id)], f"加完没把右栏切到新槽那一行：{seen}"
    assert "右栏已切到它" in messages[-1]


def test_the_head_keeps_only_add_a_slot_and_each_slot_row_gets_its_own_add(
    instance, benches, context
) -> None:
    """抬头只剩「＋ 加一个槽」；**加选项**挪到每个槽行上（一槽一颗 ＋）。

    ❗ 翻口径（用户 2026-10-02）：「加选项是做在画红圈的这个地方（槽行上），不要放在
    原来的地方，不然我怎么知道是给哪个槽加选项？」—— 表头那颗「＋ 加选项」删掉。
    """
    bench = benches(SlotsBench, context)  # 单位 70：只 1 个槽，非折叠态（1 个 _GroupHead）
    assert bench.add_slot_button().text() == "＋ 加一个槽"
    buttons = bench.add_buttons()
    assert len(buttons) == 1, "一槽一颗"
    assert buttons[0].text() == "＋"
    assert bench.add_button() is not bench.add_slot_button()
    assert not hasattr(bench, "_add"), "表头那颗「＋ 加选项」不许再长回来"
    # 非折叠态（_GroupHead 那条路）点一下：选项要落到**这一槽**（单位 70 的 SLOT=423）
    expected = next_row_id(instance.table("Options"), context.changeset)
    buttons[0].click()
    assert cell(instance, "Options", expected, "ModificationId") == SLOT


def test_each_slot_adds_the_option_to_its_own_slot_not_the_focus_one(
    instance, benches, ka_context, messages
) -> None:
    """⭐ 用户 2026-10-02 报的正题：**一槽一颗 ＋**，点哪一槽那颗就加到哪一槽。

    Ka-52（单位 46）三个槽 #75/#76/#77：先把焦点放到 #75，再点 **#77 那一行**的 ＋ ——
    选项必须落在 **#77**（从前只有表头一颗，认的是"焦点槽"，用户根本看不出加到哪去了）。
    """
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    assert [button.text() for button in bench.add_buttons()] == ["＋", "＋", "＋"]
    bench.expand_slot(75)  # 焦点 = 75
    assert bench.focus_slot() == 75
    expected = next_row_id(instance.table("Options"), ka_context.changeset)

    click(bench.add_button(77))

    assert cell(instance, "Options", expected, "ModificationId") == 77, (
        "加到了焦点槽上 —— 用户点的可是 #77 那一行那颗 ＋"
    )
    assert "槽 #77" in messages[-1]
    assert bench.focus_slot() == 77, "加完焦点要跟着到这一槽"
    assert bench.row_selected is not None  # 右栏照样切到新行（上一条用例守细节）


# ---- 复制一条选项（用户 2026-10-02：「复制一栏再在上面进行修改」）------------- #


def test_copy_option_values_copies_the_whole_row_and_only_swaps_identity(instance) -> None:
    """照抄整行 46 列，只换 Id / Order / IsDefault。

    实测（实例 52085）：一条真实选项只有 10/46 列非空（#208 = 40x S-8）—— 所以
    「复制一栏再改」才是用户要的用法。IsDefault=False 是硬口径：点一下复制不许
    把当前选中项换掉（与 new_option_values 同一条）。
    """
    context = make_context(instance, UNIT_KA)
    rows = context.data.t("Options")
    source = rows.by_id(208)  # InnerPylon KA52 S8（槽 #75，Order 2）
    assert source is not None
    columns = instance.table("Options").columns
    before = {column: rows.get(source, column) for column in columns}
    expected = next_row_id(instance.table("Options"), context.changeset)

    values = vm.copy_option_values(context.data, 208, changeset=context.changeset)

    assert values is not None
    assert values["Id"] == expected
    assert values["ModificationId"] == before["ModificationId"], "不给槽号就照抄源行那一列"
    siblings = [
        rows.i(item, "Order") for item in rows.where("ModificationId", before["ModificationId"])
    ]
    assert values["Order"] == max(siblings) + 1, "复制件接在槽尾"
    assert values["IsDefault"] is False, "复制出来的不许抢当前项"
    for column, value in before.items():
        if column in ("Id", "Order", "IsDefault"):
            continue
        assert values[column] == value, f"{column} 没照抄"


def test_copied_option_can_land_in_another_slot(instance) -> None:
    """给了槽号就复制到那个槽 —— ModificationId 跟着换。"""
    context = make_context(instance, UNIT_KA)
    values = vm.copy_option_values(context.data, 208, modification_id=77)
    assert values is not None and values["ModificationId"] == 77


def test_copy_option_values_gives_none_when_the_row_is_gone(instance) -> None:
    """源行已经不在了 -> None（界面据此说一句人话，不炸）。"""
    context = make_context(instance, UNIT_KA)
    assert vm.copy_option_values(context.data, 999_999) is None


def test_the_option_row_has_a_copy_button_that_copies_that_row(
    instance, benches, ka_context, messages
) -> None:
    """一行的 ⧉ = 照 **这一条** 复制（整行照抄、只换 Id），复制完右栏当场落到新行。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    bench.expand_slot(77)  # OuterPylon KA52：选项 #212 / #213
    row = bench.row_for(213)
    assert row is not None
    assert [tool.text() for tool in row.tools()] == ["⧉"], "行尾要有一颗复制（在 ✕ 左边）"
    expected = next_row_id(instance.table("Options"), ka_context.changeset)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    click(row.tools()[0])

    assert has_row(instance, "Options", expected)
    for column in ("Name", "UIName", "Cost", "OptionPicture", "ModificationId"):
        assert cell(instance, "Options", expected, column) == cell(
            instance, "Options", 213, column
        ), column
    assert cell(instance, "Options", expected, "IsDefault") is False
    assert ka_context.undo_depth == 1, "复制是**一条**撤销"
    assert seen == [(vm.OPTIONS_TABLE, expected)], "复制完右栏要落到新行"
    assert "已复制这一条选项" in messages[-1]


# ---- 删掉整个槽（两个行类各一颗 ✕）------------------------------------------ #


def test_remove_slot_deletes_the_slot_and_every_option_under_it(
    instance, benches, vehicle_context, messages
) -> None:
    """非折叠态的抬头 ✕（载具 6，槽 41）= 删 `Modifications` ＋ 级联它下面 2 条 `Options`。"""
    bench = benches(SlotsBench, vehicle_context)
    bench.status_message.connect(messages.append)
    head = bench.group_head(41)
    assert head is not None
    assert head.remove_button().text() == "✕"
    assert head.remove_button().isHidden() is False

    click(head.remove_button())

    assert not has_row(instance, "Modifications", 41)
    assert [has_row(instance, "Options", oid) for oid in (101, 102)] == [False, False]
    assert "2 条选项" in messages[-1] and "一条撤销" in messages[-1]
    assert vehicle_context.undo_depth == 1, "整槽 ＋ 级联 = **一条**撤销"
    # 别的槽一个字节没动
    assert has_row(instance, "Modifications", 39) and has_row(instance, "Options", 94)
    assert has_row(instance, "Modifications", 565) and has_row(instance, "Options", 1990)
    assert [group.modification_id for group in bench.groups()] == [39, 565]


def test_the_collapsed_slot_row_has_its_own_remove_button(
    instance, benches, ka_context, messages
) -> None:
    """折叠态（Ka-52）那一行也要能删掉整个槽 —— **两个行类各一颗 ✕**。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    row = bench.slot_row(75)
    assert row is not None
    assert row.remove_button().text() == "✕"
    assert row.remove_button().isHidden() is False

    click(row.remove_button())

    assert not has_row(instance, "Modifications", 75)
    assert [has_row(instance, "Options", oid) for oid in KA_OPTIONS[75]] == [False] * 3
    assert "3 条选项" in messages[-1]
    remaining = [group.modification_id for group in bench.groups()]
    assert 75 not in remaining and 76 in remaining and 77 in remaining


def test_a_single_slot_block_can_still_delete_its_only_slot(
    instance, benches, context, messages
) -> None:
    """只有 1 个槽的块（步兵 70，块内**不折**）也长着 ✕ —— 删完这一块就空了。"""
    bench = benches(SlotsBench, context)
    bench.status_message.connect(messages.append)
    assert bench.groups_are_folded() is False
    head = bench.group_head(SLOT)
    assert head is not None and head.remove_button().isHidden() is False

    click(head.remove_button())

    assert not has_row(instance, "Modifications", SLOT)
    assert [has_row(instance, "Options", oid) for oid in (OPT_BASE, OPT_LONG)] == [False, False]
    assert bench.groups() == ()
    assert any("没有改装槽" in text for text in labels_of(bench))


def test_one_ctrl_z_brings_the_whole_slot_back(instance, benches, ka_context) -> None:
    """删整槽 ＋ 级联选项 = **一条**撤销：Ctrl+Z 一次，槽与它的选项**逐字节**回来。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    ids = (75, *KA_OPTIONS[75])

    def table_of(row_id: int) -> str:
        return "Modifications" if row_id == 75 else "Options"

    before = {row_id: rows_snapshot(instance, table_of(row_id), row_id) for row_id in ids}

    click(bench.slot_row(75).remove_button())
    assert ka_context.undo_depth == 1

    message = ka_context.undo_last()

    assert message.startswith("已撤销") and "Modifications #75" in message
    for row_id in ids:
        assert rows_snapshot(instance, table_of(row_id), row_id) == before[row_id], (
            f"{table_of(row_id)}#{row_id} 没逐字节回来"
        )
    # 控件这一层不在撤销栈上（页面收到 changed 才重画）：这里照页面那条路再画一次。
    bench.refresh()
    assert len(bench.slot_rows()) == 3


def test_remove_slot_clears_the_expanded_state_and_the_focus(benches, ka_context) -> None:
    """槽没了 -> 展开态 / 焦点那两份记忆跟着清（不然会指着一个不存在的槽）。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    assert bench.expand_slot(75) is True
    assert bench.focus_slot() == 75

    click(bench.slot_row(75).remove_button())

    assert bench.expanded_slots() == ()
    assert bench.focus_slot() == 0
    assert bench.slot_row(75) is None


def test_remove_slot_on_a_borrowed_slot_writes_nothing(
    instance, benches, variant_context, messages
) -> None:
    """借来的槽（743 属于单位 108）：那颗 ✕ 点了只说话 —— **一个字节都不写**。"""
    bench = benches(SlotsBench, variant_context)
    before = isdefault_snapshot(instance)
    bench.status_message.connect(messages.append)
    head = bench.group_head(BORROWED_SLOT)
    assert head is not None

    click(head.remove_button())

    assert variant_context.changeset.is_empty is True
    assert has_row(instance, "Modifications", BORROWED_SLOT)
    assert has_row(instance, "Options", BORROWED_CURRENT)
    assert has_row(instance, "Options", BORROWED_OTHER)
    assert isdefault_snapshot(instance) == before
    assert "借来的" in messages[-1] and "只给看不给删" in messages[-1]


def test_remove_slot_refuses_a_slot_that_is_not_on_this_unit(benches, context, messages) -> None:
    """本单位的块上点不到别的车的槽；直接调也要当场说清（一个字节都不写）。"""
    bench = benches(SlotsBench, context)  # 单位 70
    bench.status_message.connect(messages.append)

    message = bench.remove_slot(BORROWED_SLOT)

    assert "没有改装槽" in message
    assert context.changeset.is_empty is True


def test_the_remove_button_never_also_toggles(benches, vehicle_context, ka_context) -> None:
    """两颗 ✕ 各自吃掉点击：点抬头那颗**不顺带折一下**，点槽行那颗**不顺带展开**。

    同族的坑这个仓踩过：炮塔行那格 ▸ 只吃 release 会"一次点击又折又选中"
    （见 STATUS「两个都要 accept」那一条）。这里 QPushButton 自己吃掉 press/release，
    但这条守卫要钉住它 —— 谁把它换成 QLabel / 手搓鼠标处理就会红。
    """
    folded = benches(SlotsBench, vehicle_context)
    head = folded.group_head(SLOT_IDS[0])
    assert head is not None
    toggles: list[int] = []
    head.clicked.connect(lambda: toggles.append(1))

    click(head.remove_button())

    assert toggles == [], "点 ✕ 不该顺手把这组折一下"

    air = benches(SlotsBench, ka_context, collapsed=True)
    row = air.slot_row(75)
    assert row is not None
    fired: list[str] = []
    row.activated.connect(lambda: fired.append("activated"))
    row.toggle_requested.connect(lambda: fired.append("toggle"))

    click(row.remove_button())

    assert fired == [], "点槽行的 ✕ 不该顺手展开 / 收起候选"


def test_the_copy_button_never_also_toggles(benches, ka_context, vehicle_context) -> None:
    """点 ⧉ 各自吃掉点击：**不顺带展开**候选、也**不顺带**折一下那一组（同族的坑踩过）。"""
    air = benches(SlotsBench, ka_context, collapsed=True)
    row = air.slot_row(75)
    fired: list[str] = []
    row.activated.connect(lambda: fired.append("activated"))
    row.select_requested.connect(lambda: fired.append("select"))

    click(row.copy_button())

    assert fired == [], "点 ⧉ 不该顺手展开 / 选中这一行"
    assert air.is_expanded(75) is False, "点 ⧉ 不该把原件展开"

    folded = benches(SlotsBench, vehicle_context)
    head = folded.group_head(SLOT_IDS[0])
    assert head is not None
    toggles: list[str] = []
    head.clicked.connect(lambda: toggles.append("clicked"))
    head.select_requested.connect(lambda: toggles.append("select"))

    click(head.copy_button())

    assert toggles == [], "点 ⧉ 不该顺手折一下那一组"


# ---- 整槽复制（槽行那颗 ⧉）---------------------------------------------------- #


def options_of(instance, modification_id) -> list[tuple]:
    """某张 `Options` 名下那几行（按 Id 升序 —— 表里行序不稳，断言要可复现）。"""
    rows = instance.table("Options")
    mid = rows.column_index("ModificationId")
    return sorted(
        (tuple(row) for row in rows.rows if row[mid] == modification_id),
        key=lambda row: row[rows.column_index("Id")],
    )


def test_copy_slot_values_copies_the_whole_row_and_only_swaps_identity(instance) -> None:
    """照抄 `Modifications` 整行（7 列），只换 Id / Order；**名字重了才加「 副本」**。

    为什么名字要动：`Modifications` 的自然键是 **UnitId + Name**
    （`rules/rulebook.json::natkey.Modifications.UnitId_Name`，原库实测唯一），
    同名两条在槽列表里也分不出来（`slot_titles` 掐的是"本单位各槽共有的词"）。
    """
    context = make_context(instance, UNIT_KA)
    values = vm.copy_slot_values(context.data, 75, changeset=context.changeset)

    assert values is not None
    assert values["Id"] == next_row_id(instance.table("Modifications"), context.changeset)
    assert values["UnitId"] == UNIT_KA
    assert values["Order"] == 1, "接在本单位槽尾（#75 / #76 / #77 的 Order 全是 0）"
    assert values["Name"] == "Weapons InnerPylons Ka52 副本"
    assert values["UIName"] == cell(instance, "Modifications", 75, "UIName"), "其余列逐列照抄"
    assert values["Type"] == cell(instance, "Modifications", 75, "Type")
    assert vm.copy_slot_values(context.data, 999_999, changeset=context.changeset) is None


def test_copy_slot_options_copy_every_row_into_the_slot(instance) -> None:
    """整行 46 列照抄、指到新槽；`Id` 留 `None`。

    `Id` 为什么留空：一批 N 条在这儿取会**取到同一个 Id** —— 由 `core_add_row` 现场逐条取。
    """
    data = data_of(instance)
    copies = vm.copy_slot_options(data, 75, new_modification_id=999)

    assert len(copies) == len(KA_OPTIONS[75]) == 3
    assert [item["Id"] for item in copies] == [None] * 3
    assert [item["ModificationId"] for item in copies] == [999] * 3
    rows = data.t("Options")
    source = sorted(
        rows.where("ModificationId", 75),
        key=lambda item: (rows.i(item, "Order"), rows.i(item, "Id")),
    )
    for item, original in zip(copies, source, strict=True):
        for column in instance.table("Options").columns:
            if column in ("Id", "ModificationId"):
                continue
            assert item[column] == rows.get(original, column), column
    # 原件那一条选中的，复制件里也还是选中的（照抄 IsDefault：新槽里不抢任何东西）
    assert [bool(item["IsDefault"]) for item in copies] == [
        bool(rows.get(item, "IsDefault")) for item in source
    ]


def test_the_slot_row_copy_button_copies_the_whole_slot_in_one_undo(
    instance, benches, ka_context, messages
) -> None:
    """⭐ 用户 2026-10-02：「改装槽这里的 Options 也要能复制，也要能改」。

    Ka-52（单位 46）→ 点 **#75 那一行的 ⧉**：`Modifications` 多一行（整行照抄、名字加「 副本」、
    Order 接槽尾）＋ 它名下 **3 条选项**一起过去（指到新槽），**一条撤销**，右栏当场落到新槽。
    """
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)
    assert [button.text() for button in bench.copy_buttons()] == ["⧉", "⧉", "⧉"]
    before = {row_id: rows_snapshot(instance, "Modifications", row_id) for row_id in KA_SLOTS}
    expected = next_row_id(instance.table("Modifications"), ka_context.changeset)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    click(bench.copy_button(75))

    assert has_row(instance, "Modifications", expected)
    assert cell(instance, "Modifications", expected, "UnitId") == UNIT_KA
    assert cell(instance, "Modifications", expected, "Name") == "Weapons InnerPylons Ka52 副本"
    assert cell(instance, "Modifications", expected, "Order") == 1
    assert cell(instance, "Modifications", expected, "Type") == cell(
        instance, "Modifications", 75, "Type"
    )
    copies = options_of(instance, expected)
    assert len(copies) == 3, "槽里的 3 条选项一起复制过去了"
    flag = instance.table("Options").column_index("IsDefault")
    assert sum(1 for row in copies if row[flag]) == 1, "照抄 IsDefault（新槽里不抢任何东西）"
    assert ka_context.undo_depth == 1, "整槽 ＋ 它的 3 条选项 = **一条**撤销"
    assert seen == [(vm.MODIFICATIONS_TABLE, expected)], "复制完右栏要落到新槽那一行"
    assert "已复制这一整个槽" in messages[-1] and "3 条选项" in messages[-1]
    for row_id, snapshot in before.items():
        assert rows_snapshot(instance, "Modifications", row_id) == snapshot, "原件一行没动"

    message = ka_context.undo_last()

    assert message.startswith("已撤销")
    assert not has_row(instance, "Modifications", expected)
    assert [has_row(instance, "Options", row[0]) for row in copies] == [False] * 3
    for row_id, snapshot in before.items():
        assert rows_snapshot(instance, "Modifications", row_id) == snapshot


def test_the_group_head_copy_button_copies_the_whole_slot(
    instance, benches, vehicle_context, messages
) -> None:
    """非折叠态（载具 6，3 个槽）：灰字抬头那颗 ⧉ 与折叠态那一颗是同一件事。"""
    bench = benches(SlotsBench, vehicle_context)
    bench.status_message.connect(messages.append)
    head = bench.group_head(SLOT_IDS[0])
    assert head is not None
    assert head.copy_button().text() == "⧉"
    expected = next_row_id(instance.table("Modifications"), vehicle_context.changeset)

    click(head.copy_button())

    assert has_row(instance, "Modifications", expected)
    assert cell(instance, "Modifications", expected, "UnitId") == UNIT_SLOTS
    assert cell(instance, "Modifications", expected, "Name") == "Weapons HMMWV TOW 副本"
    assert len(options_of(instance, expected)) == 2
    assert vehicle_context.undo_depth == 1
    assert "已复制这一整个槽" in messages[-1]


def test_copying_the_copy_gets_its_own_name(instance, benches, ka_context, messages) -> None:
    """连着复制两次：第二份叫「 副本2」—— 本单位里**没有两个同名的槽**（自然键那条）。"""
    bench = benches(SlotsBench, ka_context, collapsed=True)
    bench.status_message.connect(messages.append)

    first = bench.copy_slot(75)
    second = bench.copy_slot(75)

    assert first != second
    assert cell(instance, "Modifications", first, "Name") == "Weapons InnerPylons Ka52 副本"
    assert cell(instance, "Modifications", second, "Name") == "Weapons InnerPylons Ka52 副本2"
    names = {cell(instance, "Modifications", mid, "Name") for mid in (75, 76, 77, first, second)}
    assert len(names) == 5, "本单位五个槽，名字一个都不许重"


def test_copy_slot_refuses_a_borrowed_slot(instance, benches, variant_context, messages) -> None:
    """借来的槽（743 属于单位 108）：那颗 ⧉ 点了只说话 —— **一个字节都不写**。"""
    bench = benches(SlotsBench, variant_context)
    bench.status_message.connect(messages.append)
    before = isdefault_snapshot(instance)

    assert bench.copy_slot(BORROWED_SLOT) == 0

    assert variant_context.changeset.is_empty is True
    assert has_row(instance, "Modifications", BORROWED_SLOT)
    assert isdefault_snapshot(instance) == before
    assert "借来的" in messages[-1] and "只给看不给复制" in messages[-1]


def test_clicking_a_slot_row_also_asks_the_rail_for_that_row(
    benches, context, ka_context, vehicle_context
) -> None:
    """点槽行 / 抬头 = 右栏画这条 `Modifications`（"能看见就要能改"）。

    折 / 展开那条口径**一个字没动**：点一下两样都发。
    """
    air = benches(SlotsBench, ka_context, collapsed=True)
    seen: list[tuple[str, int]] = []
    air.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    click(air.slot_row(75))

    assert seen == [(vm.MODIFICATIONS_TABLE, 75)]
    assert air.is_expanded(75) is True, "顺手还是要展开候选（从前的口径）"
    assert [row.selected for row in air.candidate_rows(75)] == [False] * 3, (
        "右栏已经换成槽那一行了 —— 这一块里**选项行**的旧高亮要跟着清（一屏只有一个选中）"
    )
    assert air.slot_row(75).selected is False, "槽行自己不画点选态（那条口径没动）"

    folded = benches(SlotsBench, vehicle_context)
    seen_folded: list[tuple[str, int]] = []
    folded.row_selected.connect(lambda table, row_id: seen_folded.append((table, row_id)))

    click(folded.group_head(SLOT_IDS[0]))

    assert seen_folded == [(vm.MODIFICATIONS_TABLE, SLOT_IDS[0])]
    assert folded.is_group_expanded(SLOT_IDS[0]) is True

    single = benches(SlotsBench, context)  # 单位 70：只 1 个槽，抬头**不可折**
    seen_single: list[tuple[str, int]] = []
    single.row_selected.connect(lambda table, row_id: seen_single.append((table, row_id)))

    click(single.group_head(SLOT))

    assert seen_single == [(vm.MODIFICATIONS_TABLE, SLOT)], "不可折的那一条也要能进右栏"
