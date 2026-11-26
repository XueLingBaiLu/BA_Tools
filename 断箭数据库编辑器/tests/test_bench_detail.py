"""编成台右栏的「详情」视图 —— 武器 / 弹药本身的信息（用户 2026-10-01 要的那一面）。

用户原话：「我还要能看到武器、弹药的具体信息，可以在右上角加个按钮，这样就切换自如了。」

两段：① 零 Qt 的取数（bench_detail.detail_for）；② 真窗口里那颗按钮真的来回切。
③④⑤ 右栏自己的其他口径（就地改 / 不重建 / **分组抬头可折叠、默认收起**）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ba_mod_maker.app.viewmodel.bench_detail import (
    DETAIL_AMMO,
    DETAIL_STAT,
    DETAIL_WEAPON,
    detail_for,
)
from ba_mod_maker.app.viewmodel.row_icon import row_icon
from ba_mod_maker.app.viewmodel.table_labels import table_emoji
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.app.widgets.bench_kit import BenchRow, SlotCard
from ba_mod_maker.app.widgets.bench_rail import (
    MAX_REMEMBERED_ROWS,
    MODE_COLUMNS,
    MODE_DETAIL,
    _GroupMemory,
)
from ba_mod_maker.core.data import find_dataset_root
from ba_mod_maker.core.edit.refs import CellRef

DATASET_ROOT = find_dataset_root()

#: TransportAvailabilities#1293 是 BTR/Stryker 那条运输配对的**乘客侧**行：
#: 它的 SpecializationAvailabilityId=773 -> 那张关联表的 UnitId=277（Scouts）。
TRANSPORT_ROW = 1293

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 样本：单位 65 Cavalry Scouts —— 3 件班用武器（M4A1 / M203 / Javelin），每件各 1 种弹。
UNIT = 65


def _rows(data: CardData, table: str, unit_id: int) -> list:
    return list(data.t(table).where("UnitId", unit_id))


# --------------------------------------------------------------------------- #
# ① 取数（零 Qt）
# --------------------------------------------------------------------------- #


def test_weapon_row_gives_the_weapon_detail(instance) -> None:
    """点一件武器行：详情 = 武器自己那几行（瞄准时间 / 射速 / 装填时间…）。"""
    data = CardData(instance)
    row = _rows(data, "SquadWeapons", UNIT)[0]
    row_id = data.t("SquadWeapons").i(row, "Id")
    weapon_id = data.t("SquadWeapons").i(row, "WeaponId")

    detail = detail_for(data, UNIT, "SquadWeapons", row_id, instance=instance)
    assert detail is not None and detail.kind == DETAIL_WEAPON
    assert detail.title == data.name_of("Weapons", weapon_id)
    labels = [line.label for line in detail.rows]
    # ❗ 用户 2026-10-01：「武器的词条有这么多，难道我就只看这么点吗？」→ 全列一个不少；
    #   随后又要求「你既然都显示全部词条了，就不要显示这一脚只读信息了」→
    #   **卡片行（瞄准时间/射速那种算出来的、只读的）已经删掉**，这里只允许有列。
    assert "武器类型" in labels and "弹匣容量" in labels, labels
    assert "瞄准时间" not in labels, "卡片行该删了（算出来的、只读的那几行）"
    assert len(detail.groups[0].rows) >= 40, len(detail.groups[0].rows)
    assert all(line.editable for line in detail.rows), "详情里每一行都要能改"


def test_ammo_row_gives_only_the_ammo(instance) -> None:
    """点一条携弹行（WeaponAmmunitions）：**只给弹药**，不给武器（点武器行才看武器）。"""
    data = CardData(instance)
    rows = _rows(data, "WeaponAmmunitions", UNIT)

    def weapon_name(row) -> str:
        return data.name_of("Weapons", data.t("WeaponAmmunitions").i(row, "WeaponId"))

    javelin = next(r for r in rows if weapon_name(r) == "Javelin")
    row_id = data.t("WeaponAmmunitions").i(javelin, "Id")

    detail = detail_for(data, UNIT, "WeaponAmmunitions", row_id, instance=instance)
    assert detail is not None and detail.kind == DETAIL_AMMO
    # ❗ 只给**弹药**那一组：用户 2026-10-01「我都选了弹药，怎么还有武器的信息？」
    assert [g.title.split("（")[0] for g in detail.groups] == ["弹药 Javelin"]
    # 弹那一组 = **全部 67 列**（一个不删），且**不再有只读的卡片行**
    ammo_labels = [line.label for line in detail.groups[0].rows]
    assert "制导方式" in ammo_labels and "攻顶" in ammo_labels, ammo_labels[-6:]
    assert len(ammo_labels) >= 67, len(ammo_labels)
    assert "本单位携弹" not in ammo_labels and "有效射程" not in ammo_labels, "只读卡片行该删了"
    assert all(line.editable for line in detail.rows)
    labels = [line.label for line in detail.rows]
    # 卡片行删掉之后，这些信息靠**列**给（弹道类型 / 伤害 / 地面射程穿深 / 补给值…）
    assert {"弹道类型", "伤害", "地面射程穿深", "补给值"} <= set(labels), labels
    # 第一行就是**这一行的列**（携弹量在「列」那一面里，同样能改）
    assert detail.groups[0].rows[0].label == "弹药 ID（主键）"
    assert detail.groups[0].rows[0].value == "44"


def test_ammunition_row_gives_only_the_ammo(instance) -> None:
    """点弹种自己那一行（Ammunitions）：同样只给弹药那一组。"""
    data = CardData(instance)
    rows = _rows(data, "WeaponAmmunitions", UNIT)
    ammo_id = data.t("WeaponAmmunitions").i(rows[0], "AmmunitionId")

    detail = detail_for(data, UNIT, "Ammunitions", ammo_id, instance=instance)
    assert detail is not None and detail.kind == DETAIL_AMMO
    assert len(detail.groups) == 1 and detail.groups[0].title.startswith("弹药 ")


def test_without_the_instance_there_is_nothing_to_draw(instance) -> None:
    """不传 instance → 画不出列（查词典中文名要用它）→ 返回 None（切换钮据此禁用）。

    这条参数是有意的，别把 instance 变成隐藏依赖。
    """
    data = CardData(instance)
    row = _rows(data, "SquadWeapons", UNIT)[0]
    row_id = data.t("SquadWeapons").i(row, "Id")
    assert detail_for(data, UNIT, "SquadWeapons", row_id) is None


def test_rows_without_a_detail_say_so(instance) -> None:
    """认不出详情的行返回 None（那颗切换钮据此禁用，不许留个点不动的按钮）。"""
    data = CardData(instance)
    member = _rows(data, "SquadMembers", UNIT)[0]
    assert detail_for(data, UNIT, "SquadMembers", data.t("SquadMembers").i(member, "Id")) is None
    assert detail_for(data, UNIT, "Units", UNIT) is None
    assert detail_for(data, UNIT, "SquadWeapons", 999999) is None


# ---- 属性行（装甲 / 机动 / 能力 / 传感器）：详情 = 它指向的**模板行**，全部列都能改 ---- #
#
# 用户 2026-10-02：「我希望点击装甲、机动、能力、传感器时，能像修改武器、弹药之类的，
# 修改其中的详细信息」—— 那些详细信息（装甲值 / 生命值 / 前进速度 / 地面视野…）长在
# Armors / Mobility / Abilities / Sensors 上；关联行只有 (Id, UnitId, 外键) 三列。
#
#: 实例 52085 · 单位 21（Stryker RV）：(关联表, 关联行, 模板表, 模板行, 类别, 列数)。
STAT_LINKS = (
    ("UnitArmors", 97, "Armors", 70, "装甲", 14),
    ("UnitPropulsions", 54, "Mobility", 12, "机动", 22),
    ("UnitAbilities", 781, "Abilities", 82, "能力", 51),
    ("SensorUnits", 318, "Sensors", 5, "传感器", 7),
)
STAT_UNIT = 21


@pytest.mark.parametrize(
    ("table", "row_id", "target", "link_id", "category", "columns"), STAT_LINKS
)
def test_a_stat_row_gives_the_template_row_it_points_at(
    instance, table, row_id, target, link_id, category, columns
) -> None:
    """每一类属性行都给**模板行**的全部列，而且**每一列都能改**（用户要的就是改它）。"""
    data = CardData(instance)
    detail = detail_for(data, STAT_UNIT, table, row_id, instance=instance)

    assert detail is not None, f"{table}#{row_id} 没有详情"
    assert detail.kind == DETAIL_STAT
    assert detail.subtitle == f"{table} #{row_id} · 详情（它指向的模板行 · 全部列都能改）"
    assert len(detail.groups) == 1
    group = detail.groups[0]
    assert group.title == f"{category} {data.name_of(target, link_id)}（{target} #{link_id}）"
    assert len(group.rows) == columns, f"{target} 该有 {columns} 列"
    assert all(line.editable for line in group.rows), "模板行的列一列都不许是死的"


def test_the_armor_detail_carries_the_numbers_the_user_reads(instance) -> None:
    """装甲那一栏的详细信息 = 装甲值 / 生命值 / 四个方向的抗破抗穿 —— 逐字在里面。"""
    data = CardData(instance)
    detail = detail_for(data, STAT_UNIT, "UnitArmors", 97, instance=instance)
    values = {line.label: line.value for line in detail.rows}
    assert {"装甲值", "生命值", "正面抗破", "顶部抗穿"} <= set(values), list(values)
    assert values["生命值"] == "14"


def test_the_rail_shows_the_localized_uinaMe_not_the_raw_key(instance) -> None:
    """⭐ 用户 2026-10-02：「像这样的 Custom_Option_40xS8，也要进行本地化显示」。

    实测 Options.UIName：2046 行非空、1578 条是本地化键、**1578/1578 都翻得出来** ——
    右栏原来把键名原样画出来（截图里就是 Custom_Option_Empty / Custom_Option_40xS8）。
    这里守的是右栏**列视图/就地刷新**共用的那个取值口（bench_rail._line_text）。
    """
    from ba_mod_maker.app.widgets.bench_rail import _line_text
    from ba_mod_maker.core.edit.cells import row_cells
    from ba_mod_maker.core.edit.scope import RowRef

    data = CardData(instance)
    cells = row_cells(instance, RowRef("Options", 208))  # Ka-52 的 40x S-8
    by_column = {cell.column: cell for cell in cells}
    localized = data.display_name("Custom_Option_40xS8", "")
    assert localized and localized != "Custom_Option_40xS8", "本地化表里没这条？样本要重挑"
    assert by_column["UIName"].text == "Custom_Option_40xS8", "FieldCell 本身仍是原文"
    assert _line_text(by_column["UIName"]) == localized
    assert "Custom_Option" not in _line_text(by_column["UIName"])
    # 明文那一列/那一类照旧原样（不是键就一个字节都不动）
    assert _line_text(by_column["Name"]) == by_column["Name"].text


def test_the_uinaMe_column_is_shown_localized_on_the_standalone_page_too(instance) -> None:
    """专用页那条回显路径（EditorContext.value_text）走同一把尺子。"""
    from ba_mod_maker.app.screens.editor.pages.base import EditorContext
    from ba_mod_maker.app.viewmodel.unit_card import CardData as _CardData
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.edit.sharing import build_sharing_index

    data = _CardData(instance)
    context = EditorContext(
        instance,
        46,
        Changeset(instance),
        data,
        build_sharing_index(instance),
        lambda: None,
    )
    rows = instance.table("Options")
    row = next(item for item in rows.rows if item[0] == 208)
    shown = context.value_text("Options", "UIName", "Custom_Option_40xS8")
    assert shown == data.display_name("Custom_Option_40xS8", shown)
    assert "Custom_Option" not in shown
    # 明文那一类照旧原样（不是键就一个字节都不动）
    assert context.value_text("Options", "UIName", "M-SHORAD, Stingers") == "M-SHORAD, Stingers"
    assert row is not None


def test_a_stat_row_without_a_target_has_no_detail(instance) -> None:
    """坏外键 / 不是那四类 -> None（那颗切换钮据此禁用）。"""
    data = CardData(instance)
    assert detail_for(data, STAT_UNIT, "UnitArmors", 999999, instance=instance) is None
    assert detail_for(data, STAT_UNIT, "SpecializationAvailabilities", 1) is None


# ---- 炮塔行（① 那三层）：详情 = 它指向的**炮塔那一行**，全部列都能改 ---- #
#
# 用户 2026-10-02：「既然我能看到子炮塔，那我为什么不能改？」—— 点炮塔行时右栏的
# **列**面只有 TurretUnits 那 4 列（Id / UnitId / TurretId / Order），而炮塔本身
# （名字 / 模型 / **父炮塔** / 三个角度 / 转速）长在 Turrets 上（10 列）。
#
#: 实例 52085 · 单位 54（M1A1 FEP）：(TurretUnits 行, 炮塔Id, 层)。
#: 138 = 主塔 #89（顶层）、141 = 它的子塔 #90（挂 89 下）。
TURRET_UNIT = 54
TURRET_LINKS = ((138, 89, 0), (141, 90, 1))
TURRET_COLUMNS = 10


def test_a_turret_row_gives_the_turrets_row_it_points_at(instance) -> None:
    """炮塔行（含**子炮塔**）的详情 = `Turrets` 那一行的 10 列，每一列都能改。"""
    data = CardData(instance)
    for row_id, turret_id, _depth in TURRET_LINKS:
        detail = detail_for(data, TURRET_UNIT, "TurretUnits", row_id, instance=instance)

        assert detail is not None, f"TurretUnits#{row_id} 没有详情"
        assert detail.kind == DETAIL_STAT
        assert detail.subtitle == (
            f"TurretUnits #{row_id} · 详情（它指向的炮塔那一行 · 全部列都能改）"
        )
        assert len(detail.groups) == 1
        group = detail.groups[0]
        assert group.title == f"炮塔 {data.name_of('Turrets', turret_id)}（Turrets #{turret_id}）"
        assert len(group.rows) == TURRET_COLUMNS, f"Turrets 该有 {TURRET_COLUMNS} 列"
        assert all(line.editable for line in group.rows), "炮塔那 10 列一列都不许是死的"


def test_the_turret_detail_carries_the_parent_turret_column(instance) -> None:
    """⭐ 定义「挂在谁下面」的那一列（父炮塔 ID）就在里面 —— 子炮塔能改的就是它。

    ⚠️ 外键那一格显示的是**目标行的名字**（与右栏列面同一条口径），不是原始 Id；
    顶层炮塔那一列是 null -> 空串（`Turrets.ParentTurretId` 实测 965 行是 null）。
    """
    data = CardData(instance)
    child = detail_for(data, TURRET_UNIT, "TurretUnits", 141, instance=instance)
    values = {line.label: line.value for line in child.rows}
    assert "父炮塔 ID" in values, list(values)
    assert values["父炮塔 ID"] == "MainTurret M1A1FEP", "HMG 塔（Turrets#90）挂在主塔 #89 下"

    top = detail_for(data, TURRET_UNIT, "TurretUnits", 138, instance=instance)
    top_values = {line.label: line.value for line in top.rows}
    assert top_values["父炮塔 ID"] == "", "顶层炮塔那一列是 null（右栏显示空）"


def test_a_turret_row_without_a_target_has_no_detail(instance) -> None:
    """坏外键 / 不是 TurretUnits 那种行 -> None（那颗切换钮据此禁用）。"""
    data = CardData(instance)
    assert detail_for(data, TURRET_UNIT, "TurretUnits", 999999, instance=instance) is None
    assert detail_for(data, TURRET_UNIT, "Turrets", 89, instance=instance) is None


def test_the_detail_group_carries_the_icon_of_the_row_it_shows(instance) -> None:
    """⭐ 详情那一组带**图标位**：武器 / 弹药给游戏里那张真图（族 + 键），
    四张模板表没有图标列 -> 退回表 emoji。

    用户 2026-10-02：「你给这个标题加个图标，一眼就能看出是什么武器」。
    ❗ 真图的族名 / 键**不在这里另算**：断言就对着同一份 row_icon（唯一入口）。
    """
    data = CardData(instance)
    row = _rows(data, "SquadWeapons", UNIT)[0]
    row_id = data.t("SquadWeapons").i(row, "Id")
    weapon_id = data.t("SquadWeapons").i(row, "WeaponId")

    mark = detail_for(data, UNIT, "SquadWeapons", row_id, instance=instance).groups[0].mark
    assert mark is not None, "武器那一组该有图标位"
    assert mark.icon is not None and mark.icon.key, "武器该有游戏里那张真图"
    assert mark.icon == row_icon(instance, "Weapons", weapon_id)
    assert mark.emoji == table_emoji("Weapons")

    ammo_row = _rows(data, "WeaponAmmunitions", UNIT)[0]
    ammo_mark = (
        detail_for(
            data,
            UNIT,
            "WeaponAmmunitions",
            data.t("WeaponAmmunitions").i(ammo_row, "Id"),
            instance=instance,
        )
        .groups[0]
        .mark
    )
    assert ammo_mark is not None and ammo_mark.icon is not None, "弹药那一组也该有真图"

    # 装甲 / 机动 / 能力 / 传感器：那四张模板表没有图标列 -> 真图 None、emoji 兜底
    armor_mark = detail_for(data, STAT_UNIT, "UnitArmors", 97, instance=instance).groups[0].mark
    assert armor_mark is not None
    assert armor_mark.icon is None
    assert armor_mark.emoji == table_emoji("Armors")


# --------------------------------------------------------------------------- #
# ② 真窗口：右上角那颗按钮
# --------------------------------------------------------------------------- #


def test_the_rail_switches_between_detail_and_columns(window) -> None:
    """选中一条携弹行 → 默认就是**详情**；点右上角那颗按钮 ↔ 原始列，来回都对。"""
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    ammo_row = page.context.row_ids("WeaponAmmunitions")[0]

    page._on_row_selected("WeaponAmmunitions", ammo_row)
    rail = page.rail
    assert rail.mode() == MODE_DETAIL, "有详情的行应该先给详情（用户要的就是它）"
    assert rail.group_titles(), "详情要有分组标题"
    labels = dict(rail.row_texts())
    assert "弹道类型" in labels, labels  # 列（不是被删掉的卡片行「类型」）
    # ❗ 一条都不许截断：点弹药就给**弹药那 67 列**（以前卡在 24 行上，后来又多了武器那组）
    assert len(rail.row_texts()) >= 67, len(rail.row_texts())

    rail.toggle_button().click()
    assert rail.mode() == MODE_COLUMNS, "点一下应该切回原始列"
    assert len(rail.row_texts()) == 6, "WeaponAmmunitions 是 6 列"
    assert rail.toggle_button().isEnabled()

    rail.toggle_button().click()
    assert rail.mode() == MODE_DETAIL, "再点一下要回得来"


def test_the_toggle_is_hidden_when_the_row_has_no_detail(window) -> None:
    """没有详情的行（班组成员 / Units / 专精可用性）：**整颗按钮藏起来**，不许灰着占位。

    用户 2026-10-03 指着截图里 SpecializationAvailabilities 那行的按钮说：
    「**如果说这个按钮按不了的话，就把它隐藏起来**」—— 从前是 setEnabled(False)（灰着占位）。
    正控在最后一段：换到有详情的行，按钮必须**回来**且能点。
    """
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    member = page.context.row_ids("SquadMembers")[0]

    page._on_row_selected("SquadMembers", member)
    rail = page.rail
    assert rail.mode() == MODE_COLUMNS
    assert not rail.has_detail()
    assert rail.toggle_button().isHidden(), "按不了的按钮要藏起来（不许灰着占位）"
    assert rail.row_texts(), "该画的列还是要画（不许因为没详情就留白）"

    # 正控：有详情的行 → 按钮露出来、能点（别把它整条弄没了）
    ammo = page.context.row_ids("WeaponAmmunitions")[0]
    page._on_row_selected("WeaponAmmunitions", ammo)
    assert rail.has_detail()
    assert not rail.toggle_button().isHidden(), "有详情的行上按钮必须露面"
    assert rail.toggle_button().isEnabled()
    assert rail.mode() == MODE_DETAIL


def test_switching_to_columns_sticks_for_the_next_row(window) -> None:
    """用户点过「列」之后，再点别的行也该是列 —— 切换要"自如"，别每行弹回详情。"""
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    rows = page.context.row_ids("WeaponAmmunitions")
    page._on_row_selected("WeaponAmmunitions", rows[0])
    page.rail.set_mode(MODE_COLUMNS)
    page._on_row_selected("WeaponAmmunitions", rows[1])
    assert page.rail.mode() == MODE_COLUMNS


def test_clicking_an_armor_row_shows_the_armors_row_editable(window) -> None:
    """⭐ 点中栏 ③ 装甲那一行 → 右栏默认给 **Armors 那一行**（14 列全可改）；

    那颗按钮切回「列」= 这条**关联行**自己的 3 列（两面都还在，不是二选一）。
    """
    window._open_unit_editor(STAT_UNIT)
    page = window._unit_editor.bench_page()
    line = page.bench_armor.lines()[0]

    page._on_row_selected(line.table, line.row_id)
    rail = page.rail
    assert rail.has_detail() is True, "装甲那一行现在有详情了（它指向 Armors）"
    # ❗ 不断言"默认那一面"：window 是**模块共用**的，前面那条用例点过「列」之后
    #    _prefer_columns 会留着（那是既有口径：用户挑过哪一面就一直是那一面）。
    rail.set_mode(MODE_DETAIL)
    assert rail.mode() == MODE_DETAIL
    name = page.context.data.name_of("Armors", line.link_row_id)
    assert rail.group_titles() == (f"装甲 {name}（Armors #{line.link_row_id}）",)
    assert len(rail.editable_labels()) == 14, "Armors 14 列，一列都不许是死的"
    assert {"装甲值", "生命值"} <= set(rail.editable_labels())

    rail.toggle_button().click()
    assert rail.mode() == MODE_COLUMNS
    assert len(rail.editable_labels()) == 3, "UnitArmors 只有 Id / UnitId / ArmorId 三列"


# --------------------------------------------------------------------------- #
# ③ 就地改（用户口径：「右边的数据我要都能改啊，我不能改我看着干嘛？」）
# --------------------------------------------------------------------------- #


def test_every_column_row_in_the_rail_is_editable(window) -> None:
    """列视图里**每一格**都是可点的值（不是只读标签）。"""
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    ammo_row = page.context.row_ids("WeaponAmmunitions")[0]
    page._on_row_selected("WeaponAmmunitions", ammo_row)
    page.rail.set_mode(MODE_COLUMNS)

    assert len(page.rail.editable_labels()) == 6, "WeaponAmmunitions 6 列，一列都不许是死的"
    assert "携弹量" in page.rail.editable_labels()


def test_clicking_a_rail_value_opens_the_editor_and_the_edit_lands_in_the_cache(window) -> None:
    """⭐ 点值 → 就地开编辑器 → 打字回车 → **进缓存**（真控件、真按键，不是 emit 冒充）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    ammo_row = page.context.row_ids("WeaponAmmunitions")[0]
    page._on_row_selected("WeaponAmmunitions", ammo_row)
    page.rail.set_mode(MODE_COLUMNS)

    target = page.rail.value_widget("携弹量")
    assert target is not None
    QTest.mouseClick(target, Qt.LeftButton, pos=target.rect().center())
    assert page.rail.has_editor(), "点值之后要就地冒出编辑器"

    box = page.rail.editor().findChild(QLineEdit)
    assert box is not None, "整数列应该给一个输入框"
    box.selectAll()
    QTest.keyClicks(box, "321")
    QTest.keyClick(box, Qt.Key_Return)

    ref = f"WeaponAmmunitions#{ammo_row}.Quantity"
    assert window._edit_session.changeset.value("WeaponAmmunitions", ammo_row, "Quantity") == 321, (
        f"{ref} 没写进缓存"
    )
    assert not page.rail.has_editor(), "提交之后浮层要收起来"


def test_every_row_in_the_detail_view_is_editable(window) -> None:
    """详情里**没有只读行**了 —— 每一行都是可点的值。

    用户 2026-10-01：「你既然都显示全部词条了，就不要显示这一脚只读信息了」——
    原来那几行是游戏信息卡口径（算出来的、只读），现在详情只剩可改的全列。
    """
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    page._on_row_selected("SquadWeapons", page.context.row_ids("SquadWeapons")[0])
    rail = page.rail
    # 走哪一面是**用户挑过就记住**的（别的用例会切到列）—— 这里显式回到详情。
    rail.set_mode(MODE_DETAIL)

    editable = rail.editable_labels()
    assert editable, "详情该有可改的列"
    assert len(editable) == len(rail.row_texts()), "详情里不许有只读行"
    assert "武器类型" in editable and "瞄准时间下限" in editable


# --------------------------------------------------------------------------- #
# ④ 改一格**不许重建控件**（用户提过几回：「不要全量保存一下，界面闪一下很影响观感」）
# --------------------------------------------------------------------------- #


def test_the_editor_opens_on_the_row_you_clicked(window) -> None:
    """点哪一行的值，编辑框就长在**那一行里**。

    实机报过的症状：点第 3 行，编辑框长在最下面一行 —— 根因是"该长在哪"被存成了
    **最后画的那一行**（在 _add_line 里赋值，循环里被覆盖）。现在落点跟着被点的那一格。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    ammo = page.context.row_ids("WeaponAmmunitions")[0]
    page._on_row_selected("WeaponAmmunitions", ammo)
    page.rail.set_mode(MODE_COLUMNS)

    target = page.rail.value_widget("携弹量")
    assert target is not None
    QTest.mouseClick(target, Qt.LeftButton, pos=target.rect().center())
    editor = page.rail.editor()
    assert editor is not None
    assert editor.parentWidget() is target.parentWidget(), "编辑框长到别人那一行去了"


def test_one_edit_rebuilds_nothing_and_still_shows_the_new_value(window) -> None:
    """⭐ 改一格：**控件一个都不许重建**，但值必须真的是新的。

    记录在案的先例（动手前该读的那两条）：
    - docs/交接-2026-09-30-界面批-图标树弹药.md:76/93 —— 署名变了才整套重建，
      没变走 reload()（改数量不重建）；
    - docs/交接-2026-09-30-夜-实机反馈批.md:18 —— 重建前后保住 QScrollArea 的滚动值。

    实测（_work/probe_bench_flash.py，改一格携弹量）：本用例钉的就是它 ——
    改前 9 个 BenchRow / 3 个 SlotCard，改后**逐个 id 相同**（顺序都一样），
    且右栏与中栏都显示 ×321。
    """
    window._open_unit_editor(65)
    page = window._unit_editor.bench_page()
    ammo = page.context.row_ids("WeaponAmmunitions")[0]
    page._on_row_selected("WeaponAmmunitions", ammo)
    page.rail.set_mode(MODE_COLUMNS)

    before_rows = [id(w) for w in page.findChildren(BenchRow)]
    before_cells = [id(w) for w in page.findChildren(SlotCard)]
    assert before_rows and before_cells

    cell = next(
        c for c in page.context.cells_of_row("WeaponAmmunitions", ammo) if c.column == "Quantity"
    )
    page._commit_rail(cell.ref, 321)
    window._unit_editor._refresh_pages()

    assert [id(w) for w in page.findChildren(BenchRow)] == before_rows, "BenchRow 被重建了"
    assert [id(w) for w in page.findChildren(SlotCard)] == before_cells, "SlotCard 被重建了"
    assert page.rail.value_widget("携弹量").text() == "321", "控件保住了但值没更新"
    assert "×321" in [row.value_label().text() for row in page.bench_loadout.ammo_rows()], (
        "中栏 ② 那一行也要就地跟上"
    )


def test_the_rail_key_column_fits_the_longest_name(window) -> None:
    """右栏中文名列的宽度要跟着**最长那个**走。

    实机报过「文字被遮挡住了一部分」：量出来是「所属步兵班 ID（占位）」需要 113px，
    而我当时把这一列**钉死在 104px** → 右边被硬切（裁像素才看清切的是右半边）。
    判据：每一个中文名要么**完整装得下**，要么是**主动省略**（… 结尾）且 tooltip 里有全名。
    """
    window._open_unit_editor(65)
    page = window._unit_editor.bench_page()
    rail = page.rail
    rail.set_mode(MODE_COLUMNS)

    labels = [w for w in rail.findChildren(QLabel) if w.property("role") == "bench-rail-key"]
    assert labels, "右栏该有中文名那一列"
    for label in labels:
        shown = label.text()
        if shown.endswith("…"):
            assert label.toolTip(), f"{shown} 省略了却没有 tooltip 说全名"
            continue
        need = label.fontMetrics().horizontalAdvance(shown)
        assert need <= label.width(), (
            f"「{shown}」需要 {need}px，格子只有 {label.width()}px（被切）"
        )


def test_every_rail_key_carries_its_annotation(window) -> None:
    """右栏**每一条**中文名都要能悬浮看到「全名 ＋ 这一格对应哪一列」。

    用户 2026-10-03：「**我右边的词条注释悬浮窗你给我加回来啊**」。
    2026-10-02 曾收紧成"只在被省略号截断时才挂"，这一条把它反过来：一律挂。
    """
    window._open_unit_editor(65)
    rail = window._unit_editor.bench_page().rail
    rail.set_mode(MODE_COLUMNS)

    labels = [w for w in rail.findChildren(QLabel) if w.property("role") == "bench-rail-key"]
    assert labels, "右栏该有中文名那一列"
    missing = [w.text() for w in labels if not w.toolTip()]
    assert not missing, f"这些中文名没有注释浮窗：{missing}"
    for label in labels:
        first = label.toolTip().splitlines()[0]
        shown = label.text()
        if shown.endswith("…"):
            assert first.startswith(shown[:-1]), f"{shown} 的提示没给全名：{first!r}"
        else:
            assert first == shown, f"「{shown}」的提示第一行不是它自己：{first!r}"


def test_a_bool_cell_opens_a_text_box_not_a_button(window, qapp) -> None:
    """⭐ 用户 2026-10-02：「把所有布尔值的编辑栏，通通改成输入文字的编辑栏，别搞什么按钮」。

    判据两层：① 点开那一格是 QLineEdit（里面没有 QCheckBox），预填「是」；
    ② 打「否」收工 = 真的写 False；打一句读不懂的 = 报错、不写、编辑器不关。
    """
    window._open_unit_editor(KA_52)
    page = window._unit_editor.bench_page()
    page._on_row_selected("Options", KA_OPTION_DEFAULT)  # 那张截图里的 #3343
    ref = CellRef("Options", KA_OPTION_DEFAULT, "IsDefault")
    assert page.context.cell(ref).raw is True, "样本变了：这一格该是布尔 true"

    target = page.rail.value_widget("是否默认")
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.rect().center())
    qapp.processEvents()
    editor = page.rail.editor()
    assert editor is not None, "点开没有编辑器"
    assert editor.findChildren(QCheckBox) == [], "布尔格又长回勾选按钮了"
    box = editor.findChild(QLineEdit)
    assert box is not None, "布尔格该是一个文本框"
    assert box.text() == "是", f"该预填「是」，实际 {box.text()!r}"

    box.setText("否")
    box.editingFinished.emit()
    qapp.processEvents()
    assert page.context.cell(ref).raw is False, "打了「否」却没写进去"

    # 读不懂的值：报错、不写、**不关**（与其它格同一条口径）
    target = page.rail.value_widget("是否默认")
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.rect().center())
    qapp.processEvents()
    editor = page.rail.editor()
    assert editor is not None
    box = editor.findChild(QLineEdit)
    box.setText("随便写点啥")
    box.editingFinished.emit()
    qapp.processEvents()
    assert page.context.cell(ref).raw is False, "读不懂也照写 = 写坏数据"
    assert page.rail.editor() is not None, "读不懂的值不该把编辑器关掉"
    assert not editor._error.isHidden(), "读不懂要在框下面说一句"


def test_typing_through_an_ime_is_not_lost_by_clicking_away(window, qapp) -> None:
    """⭐ 用户 2026-10-02：「我改这一栏，点了空白区域，改动没有生效」——真凶是输入法。

    复现（_work/probe_ime_preedit.py 实测）：点开那一格（内容全选）→ 输入法开始拼字（preedit）
    → 那一刻 `QLineEdit.text()` 里**没有**那几个字（实测全选的「是」当场变空串）
    → 用户点空白 ⇒ 事件过滤器**当场收工**，把空串按「否」写进库里，拼的字随控件一起销毁 ✗。

    判据：拼字中不收工（值不动、编辑器不关）；等输入法上屏之后再点一次才收工。
    """
    window._open_unit_editor(KA_52)
    page = window._unit_editor.bench_page()
    page._on_row_selected("Options", KA_OPTION_DEFAULT)
    ref = CellRef("Options", KA_OPTION_DEFAULT, "IsDefault")
    before = page.context.cell(ref).raw
    assert before is True

    target = page.rail.value_widget("是否默认")
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.rect().center())
    qapp.processEvents()
    editor = page.rail.editor()
    box = editor.findChild(QLineEdit)
    box.selectAll()
    # 输入法"还在拼"：preedit 非空，而 text() 里拿不到那几个字
    QApplication.sendEvent(box, QInputMethodEvent("否", []))
    qapp.processEvents()
    assert box.text() == "", "样本变了：拼字中 text() 该是空的（这正是坑）"

    rail = page.rail
    spot = rail.rect().bottomLeft()
    spot.setY(max(1, spot.y() - 8))
    spot.setX(max(1, spot.x() + 12))
    QTest.mouseClick(rail, Qt.MouseButton.LeftButton, pos=spot)
    qapp.processEvents()
    assert page.context.cell(ref).raw is before, "拼字中那一下把值写坏了（用户报的就是这个）"
    assert page.rail.editor() is not None, "拼字中不该收工"

    # 输入法上屏（preedit 清空 + 那几个字进 text()）之后再点一次：正常收工
    box.setText("否")
    QApplication.sendEvent(box, QInputMethodEvent("", []))
    qapp.processEvents()
    QTest.mouseClick(rail, Qt.MouseButton.LeftButton, pos=spot)
    qapp.processEvents()
    assert page.context.cell(ref).raw is False, "上屏之后的收工该把「否」写进去"


def test_writing_one_into_the_default_field_clears_the_other_options(window, qapp) -> None:
    """⭐ 2026-10-02 口径翻之后（用户：「它不就是一个布尔值吗？我直接改成0或1就行了」）：

    「设为当前项」的唯一入口变成**右栏那一格「是否默认」**（点选项行不再写它）——
    于是"槽内互斥"得由写这一格那条路兜住，否则一个槽会出现两个 true。
    判据：往 #233 写 1 -> #233 是 true、同槽原本默认的 #208 被自动写成 false，
    状态栏当场说「同槽另外 1 项已自动取消」。
    """
    window._open_unit_editor(KA_52)
    page = window._unit_editor.bench_page()
    said: list[str] = []
    page.status_message.connect(said.append)
    slot_of_208 = CellRef("Options", 208, "IsDefault")
    slot_of_233 = CellRef("Options", 233, "IsDefault")
    assert page.context.cell(slot_of_208).raw is True, "样本变了：#208 原本是槽 75 的默认项"
    assert page.context.cell(slot_of_233).raw is False

    page._commit_rail(slot_of_233, True)
    qapp.processEvents()

    assert page.context.cell(slot_of_233).raw is True, "写进去的 1 没了"
    assert page.context.cell(slot_of_208).raw is False, "同槽另一项没被清 0（会出现两个默认）"
    assert "同槽另外 1 项已自动取消" in said[-1], said[-1]


def test_opening_the_editor_never_pushes_the_key_column_out(window) -> None:
    """⭐ 用户原话：「开始是好的，但**点了一下编辑栏**，就成这样了」——
    点开就地编辑器之后，右栏**不许横向滚动**（一滚，中文名左边就被推出视口）。

    根因（量出来的）：值那一格是长文本按钮（`UNIT_SOUND_PRESET_US_INFANTRY_SCOUTS`），
    它的 minimumSizeHint 跟着文字长，把整行撑得比视口宽 —— viewport=450 / content=494；
    点开编辑器、焦点落进来时 Qt 为了把输入框露出来把滚动区**向右滚 44px**，
    而横向滚动条是被隐藏的（用户滚不回来）→ 整列中文名左边被切。

    修法：值那一格换成 _ElidedValueButton（可压缩 + 省略号 + 全名进 tooltip），
    并在挂编辑器时把横向滚动归零兜一道。
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel as _QLabel

    window._open_unit_editor(65)
    page = window._unit_editor.bench_page()
    rail = page.rail
    rail.set_mode(MODE_COLUMNS)
    scroll = rail._scroll
    keys = [w for w in rail.findChildren(_QLabel) if w.property("role") == "bench-rail-key"]
    assert keys
    assert scroll.horizontalScrollBar().value() == 0, "静止时就不该横向滚动"

    # 点开一个值（挑最长的那一格：它以前就是把行撑宽的那一个）
    label, _name, widget = max(rail._value_labels, key=lambda t: len(t[0]))
    assert isinstance(widget, QPushButton), f"{label} 该是可点的值"
    QTest.mouseClick(widget, Qt.LeftButton, pos=widget.rect().center())
    assert rail.has_editor(), "点了值该冒出编辑器"

    assert scroll.horizontalScrollBar().value() == 0, "点开编辑器之后右栏被横向滚走了"
    for key_label in keys:
        visible_x = key_label.mapTo(scroll.viewport(), QPoint(0, 0)).x()
        assert visible_x >= 0, f"「{key_label.text()}」被推出视口（x={visible_x}）"


def test_switching_rows_updates_the_rail_title_too(window) -> None:
    """⭐ 换一行，**标题必须跟着换**（用户 2026-10-01：「我都选了AT了，怎么红圈处还是AA？」）。

    根因：`set_selection` 里 `same_selection` 算在 `self._table/_row_id` **赋值之后** → 恒为真，
    于是换行时走了「只刷值」那条路，正文换成新行、标题留着上一行 ——
    实机就是「标题 Options#1857（AA）」+「正文 选项 ID（主键）1497（AT）」。
    """
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    rows = page.context.row_ids("Options")
    assert len(rows) >= 2, "这个单位该有两个以上改装选项"
    page._on_row_selected("Options", rows[0])
    first = page.rail.title_text()
    page._on_row_selected("Options", rows[1])
    assert page.rail.title_text() != first, "换了行，标题还是上一行的"
    assert str(rows[1]) in page.rail.title_text(), page.rail.title_text()


def test_closing_the_editor_leaves_no_popup_behind(window, qapp) -> None:
    """⭐ 收工时**不许留顶层残窗** —— 用户截图里"黑框盖住下面几行"就是它。

    实测（_work/probe_rail_popup_leftover.py）：点开右栏一个外键值 → 弹开下拉 → 提交，
    编辑器收了，Qt 那个卷动特效壳（QRollEffect，顶层窗）**还留在屏幕上**（321x306）。
    hidePopup() 收不掉它（它不是 QComboBox 的 view），得按类名收。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QComboBox

    window._open_unit_editor(125)
    page = window._unit_editor.bench_page()
    members = page.context.row_ids("SquadMembers")
    page._on_row_selected("SquadMembers", members[0])
    rail = page.rail
    rail.set_mode(MODE_COLUMNS)

    pick = next(k for k in rail.editable_labels() if k.startswith("主武器"))
    widget = rail.value_widget(pick)
    QTest.mouseClick(widget, Qt.LeftButton, pos=widget.rect().center())
    combo = rail.editor().findChild(QComboBox)
    assert combo is not None, "外键那一格该是个下拉"
    combo.showPopup()
    qapp.processEvents()

    cell = next(
        c
        for c in page.context.cells_of_row("SquadMembers", members[0])
        if c.column == "PrimaryWeaponId"
    )
    page._commit_rail(cell.ref, 525)
    qapp.processEvents()

    leftovers = [
        w.metaObject().className()
        for w in QApplication.topLevelWidgets()
        if w.isVisible() and w.metaObject().className() == "QRollEffect"
    ]
    assert not leftovers, f"收工留下顶层残窗：{leftovers}"
    assert not rail.has_editor()


#: 用户 2026-10-02 截图那一路：Ka-52（单位 46）→ 右栏「炮塔挂载槽 N」= 外键下拉（Turrets 1055 行）。
KA_52 = 46
KA_OPTION_S8 = 1664
KA_OPTION_VIKHR = 210
#: 布尔格样本：Ka-52 内侧挂架 · S8（实测 IsDefault=true，且 Order=2 —— 它不是第一条）。
KA_OPTION_DEFAULT = 208


def _popup_leftovers() -> list[str]:
    """还留在屏幕上的顶层浮层 / 卷动壳（主窗口之外的那些）。"""
    from PySide6.QtWidgets import QApplication

    return [
        w.metaObject().className()
        for w in QApplication.topLevelWidgets()
        if w.isVisible() and w.metaObject().className() in ("QListView", "QRollEffect")
    ]


def _open_a_foreign_popup(window, qapp):
    """把右栏一个外键格点开、打半截搜索词（浮层弹出来、值解析不了 = 用户截图那一态）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox

    window._open_unit_editor(KA_52)
    page = window._unit_editor.bench_page()
    page._on_row_selected("Options", KA_OPTION_S8)
    rail = page.rail
    rail.set_mode(MODE_COLUMNS)
    label = next(t for t in rail.editable_labels() if t.startswith("炮塔挂载槽"))
    widget = rail.value_widget(label)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
    qapp.processEvents()
    combo = rail.editor().findChild(QComboBox)
    line = combo.lineEdit()
    assert line is not None
    line.selectAll()
    QTest.keyClicks(line, "Left")  # 半截搜索词 —— 收工时解析不了，正是用户那一态
    # ❗ 测试环境里打字**不会自动弹浮层**（窗口没被激活），手动弹一下 —— 与既有那条
    #    test_closing_the_editor_leaves_no_popup_behind 同一种写法。
    combo.showPopup()
    qapp.processEvents()
    return page, rail, combo


def test_switching_rows_closes_the_rail_editor_and_its_popup(window, qapp) -> None:
    """⭐ 用户 2026-10-02：「右栏开着外键下拉、框里还是半截搜索词时，点了中栏那一条」——
    那一下之后右栏**不许再挂着编辑器、也不许留顶层浮层**（用户截图里那个黑框）。

    为什么从前的路子会留：半截搜索词解析不了 ⇒ 按既定口径"报错、不写、**不关**"⇒
    编辑器与它弹出的候选浮层（**顶层窗**）都留着；而那一格属于**上一行**。
    """
    page, rail, _combo = _open_a_foreign_popup(window, qapp)
    assert rail.has_editor(), "样本没进到「编辑器挂着」那一态"
    assert _popup_leftovers(), "样本没把候选浮层弹出来"

    page._on_row_selected("Options", KA_OPTION_VIKHR)  # 中栏点了另一条（用户那一下）
    qapp.processEvents()

    assert not rail.has_editor(), "换了行，右栏的就地编辑器还挂着"
    assert not _popup_leftovers(), f"换了行还留着顶层浮层：{_popup_leftovers()}"


#: Ka-52（单位 46）右栏那一行里，「炮塔挂载槽 0」对应的列名。
TURRET0_COLUMN = "Turret0Id"


def test_picking_a_foreign_candidate_keeps_the_list_and_writes_it(window, qapp) -> None:
    """⭐ 用户 2026-10-02：「我左图点了之后，然后就出现了右图的情况」。

    左图 = 候选列表画得好好的（6 条）；右图 = 点完候选之后**列表被清空**，还开着的浮层
    变成一个**空黑洞**，下面一行红字报错。

    根因：点候选时 Qt 把候选文本（`名字  #Id`）塞进行编辑 ⇒ `editTextChanged` 拿它去服务端搜
    —— 名字里没有 `#Id`，**0 条** ⇒ 列表被清空 ⇒ 浮层成空壳、`_selected_foreign_id` 再也对不上。
    修法：① 框里那串字**正好是某一项**就不再重筛；② 候选列表里按下那一下走 `_pick_candidate`
    （先写文字、再收工），与资源键那条同一套。
    """
    from ba_mod_maker.core.edit.refs import CellRef

    page, rail, combo = _open_a_foreign_popup(window, qapp)
    assert combo.count() > 0
    ref = CellRef("Options", KA_OPTION_S8, TURRET0_COLUMN)
    expected = combo.itemData(0)
    pick_text = combo.itemText(0)

    # ① 点候选那一下 Qt 干的事：把候选文本写进行编辑 —— **列表不许被清空**
    combo.setEditText(pick_text)
    qapp.processEvents()
    assert combo.count() > 0, "点候选之后候选被清空了 —— 浮层会变成一个空黑洞（用户那张右图）"
    assert page.rail.editor() is not None, "还没收工呢，编辑器不该先没"

    # ② 真按那一条：先写文字、再当场收工（与资源键那条同一条口径）
    combo.completer().completionModel()  # 候选模型还在（没被清）
    rail.editor()._pick_candidate(combo.completer().completionModel().index(0, 0))
    qapp.processEvents()

    assert page.context.cell(ref).raw == expected, "点了候选却没写回那一格"
    assert not rail.has_editor(), "点完候选编辑器没收工"
    assert not _popup_leftovers(), f"点完候选还留着浮层：{_popup_leftovers()}"


def test_leaving_the_page_or_the_unit_takes_the_popup_with_it(window, qapp) -> None:
    """切页 / 换单位也要把右栏的就地编辑器（含候选浮层）收走。

    ❗ 浮层是**顶层窗**：页面藏了它**不会**跟着藏 —— 上一页 / 上一台车那个下拉会飘在新内容
    头上（用户 2026-10-02 截图里那个黑框，里面只剩半张旧 pixmap，所以大半是黑的）。
    """
    page, rail, _combo = _open_a_foreign_popup(window, qapp)
    assert rail.has_editor() and _popup_leftovers(), "样本没进到「浮层开着」那一态"

    page.hide()  # = 切页 / 最小化（QStackedWidget 换页走的就是它）
    qapp.processEvents()
    assert not rail.has_editor()
    assert not _popup_leftovers(), f"页面藏了还留着顶层浮层：{_popup_leftovers()}"

    page.show()
    qapp.processEvents()
    _page2, rail2, _c2 = _open_a_foreign_popup(window, qapp)
    assert rail2.has_editor() and _popup_leftovers()

    window._open_unit_editor(70)  # 换单位（内部走 BenchPage.set_context）
    qapp.processEvents()
    assert not rail2.has_editor()
    assert not _popup_leftovers(), f"换了单位还留着顶层浮层：{_popup_leftovers()}"


def test_a_rail_rebuild_also_takes_the_popup_with_it(window, qapp) -> None:
    """右栏自己重画（`set_selection` -> `_render` -> `clear`）也要把浮层收走。

    这条**不经页面**（直接换行），走的是 `BenchRail.clear` —— 它从前只 `detach` 编辑器、
    不收浮层，于是"编辑器没了、黑框还在"，而且之后谁都不会再来收它。
    """
    page, rail, _combo = _open_a_foreign_popup(window, qapp)
    assert rail.has_editor()

    rail.set_selection(
        "Options", KA_OPTION_VIKHR, page.context.cells_of_row("Options", KA_OPTION_VIKHR)
    )
    qapp.processEvents()

    assert not rail.has_editor()
    assert not _popup_leftovers(), f"重画之后还留着顶层浮层：{_popup_leftovers()}"


def test_the_rail_link_goes_to_the_unit_a_link_row_points_at(window) -> None:
    """⭐⭐ 用户 2026-10-02：`乘客侧` 那一格指向的是**关联表**（没有名字列）。

    两件事一起验：
      ① 显示 —— 不再写 `SpecializationAvailabilities #773`，写它真正指着的那个单位（Scouts）；
      ② 跳转 —— 右栏行尾那颗 ↗ 要跳到**那个单位**，不是那张没有名字的中间表。
    """
    window._open_unit_editor(21)
    page = window._unit_editor.bench_page()
    page._on_row_selected("TransportAvailabilities", TRANSPORT_ROW)
    rail = page.rail
    rail.set_mode(MODE_COLUMNS)

    # ① 显示：乘客侧那一格写的是**单位名**，不是那张关联表
    pairs = rail.row_texts()  # 成对的 (中文名, 值)
    values = [value for _key, value in pairs]
    assert "Scouts" in values, pairs
    assert not any("SpecializationAvailabilities #" in v for v in values), pairs

    # ② 跳转：↗ 指向 Units#277（不是 SpecializationAvailabilities#773）
    got: list[tuple[str, int]] = []
    rail.link_activated.connect(lambda t, r: got.append((t, r)))
    arrows = [b for b in rail.findChildren(QPushButton) if "Scouts" in b.toolTip()]
    assert arrows, "乘客侧那一行没有 ↗（用户：我要能直接跳转到那个单位）"
    arrows[0].click()
    assert got == [("Units", 277)], got


# --------------------------------------------------------------------------- #
# ⑤ 分组的折与不折（2026-10-02 两轮口径，**按组数分**）
#
# - **≥2 组**：抬头可折、**默认收起**（用户 2026-10-02：「把所有能做成折叠栏的都做成折叠栏」＋
#   「折叠栏默认是折叠状态」）；手感照中栏 BenchSection（冻结件，见 bench_kit.py）。
# - **只有一组**：**不套折叠栏** —— 记号藏起来、抬头点了不动、行一直露着，标题换成正经一行。
#   用户 2026-10-02（截图圈着右栏那一行）：「这种地方你就别搞折叠栏了，又小又难看」；
#   与中栏那条既有口径同一条：「只有一条的不套折叠，纯粹的画蛇添足」。
#
# ⚠️ 真实数据今天**一律只有一组**（点武器 / 弹药 / 装甲 / 机动 / 能力 / 传感器都只给一组），
# 所以"能折"那条路靠 _two_group_detail() 那份夹具喂 —— 不是造一个用不上的分支，
# 是**原来那条路要继续有守卫**（将来一组里再分小节时它就在了）。
# --------------------------------------------------------------------------- #


def _detail_rail(page, table: str, row_id: int):
    """选中一行 → **把展开记忆归零** → 进"详情"那一面。

    ❗ 为什么归零：`window` 是**模块共用**的（conftest），右栏与它那份记忆跨用例活着 ——
    不归零的话"默认收起"这条会读到上一个用例留下的展开态。
    """
    page._on_row_selected(table, row_id)
    rail = page.rail
    rail._group_memory = _GroupMemory()
    rail.set_mode(MODE_DETAIL)
    return rail


def _two_group_detail():
    """两组的假详情：**只有 ≥2 组才给折**，而真实数据今天一律只给一组。

    所以"能折"那条路（默认收起 / 点抬头 / 记忆）只能靠这份夹具喂 ——
    它守的是**原来那条路**：将来一组里再分小节时它就在了。
    """
    from ba_mod_maker.app.viewmodel.bench_detail import BenchDetail, DetailGroup, DetailLine

    return BenchDetail(
        kind="stat",
        title="两组夹具",
        subtitle="两组的假详情",
        groups=(
            DetailGroup(title="甲组", rows=(DetailLine(label="甲一", value="1"),)),
            DetailGroup(title="乙组", rows=(DetailLine(label="乙一", value="2"),)),
        ),
    )


def _fake_rail(page, row_id: int = 900001, *, reset_memory: bool = True):
    """把右栏接上那份两组的假详情（**不动数据**，也不走 _on_row_selected）。"""
    if reset_memory:
        page.rail._group_memory = _GroupMemory()
    page.rail.set_selection("Weapons", row_id, (), detail=_two_group_detail())
    page.rail.set_mode(MODE_DETAIL)
    return page.rail


def test_a_single_group_is_not_a_fold_bar(window, qapp) -> None:
    """⭐ 只有一组时**不套折叠栏**：记号藏起来、抬头点了不动、行一直露着。

    用户 2026-10-02（截图圈着右栏那一行）：「这种地方你就别搞折叠栏了，又小又难看」。
    ❗ 改前这一条验的是"每一组都收起"—— 那条口径对 **≥2 组** 仍然成立（见下一条）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    row = page.context.row_ids("WeaponAmmunitions")[0]
    rail = _detail_rail(page, "WeaponAmmunitions", row)

    titles = rail.group_titles()
    assert titles, "详情要有标题（这一组是什么）"
    # 既有读数口语义不变：标题就是详情里的组标题**原文**（记号不在里面）
    assert titles == tuple(group.title for group in rail.detail_groups())
    assert len(titles) == 1, "真实数据今天一律只有一组（点武器/弹药/属性行都是）"
    title = titles[0]

    assert rail.group_foldable(title) is False
    assert rail.group_caret(title).isHidden() is True, "只有一组还给记号 = 又小又难看那一版"
    assert rail.group_collapsed(title) is False, "只有一组：行**一直露着**，没有收起这一说"
    rows = rail.group_rows(title)
    assert rows, f"{title} 下面该有行"
    assert all(w.isVisibleTo(rail) for w in rows), "不套折叠栏，行不该藏起来"

    # 点抬头**什么都不发生**（它不是折叠栏：连 clicked 都没接）
    head = rail.group_head(title)
    qapp.processEvents()
    QTest.mouseClick(head, Qt.LeftButton, pos=head.rect().center())
    qapp.processEvents()
    assert rail.group_collapsed(title) is False
    assert all(w.isVisibleTo(rail) for w in rows)
    assert rail.expanded_groups() == (), "不折的组不该往记忆里写东西"


def test_the_group_title_carries_the_icon_of_that_row(window) -> None:
    """⭐ 标题那一格的图标位：接上 store 就画**游戏里那张真图**；没 store 时是表 emoji 兜底。

    用户 2026-10-02：「你给这个标题加个图标，一眼就能看出是什么武器」。
    """
    from PySide6.QtGui import QPixmap

    class Store:
        """假图标缓存（与 test_bench_slots._FakeIconStore 同一套）：记下问过哪张图。"""

        def __init__(self) -> None:
            self.asked: list[tuple[str, str]] = []

        def pixmap(self, family: str, key: str) -> QPixmap:
            self.asked.append((family, key))
            pixmap = QPixmap(8, 8)
            pixmap.fill()
            return pixmap

    window._open_unit_editor(UNIT)
    page = window._unit_editor.bench_page()
    row = page.context.row_ids("SquadWeapons")[0]
    rail = _detail_rail(page, "SquadWeapons", row)
    title = rail.group_titles()[0]

    # 先归零：window 是模块共用，前一条用例可能动过这份缓存。
    rail.set_icons(None)
    assert rail.group_has_real_icon(title) is False
    assert rail.group_icon_visible(title) is True, "没真图也要有 emoji 兜底，别留空框"
    assert rail.group_mark_emoji(title) == table_emoji("Weapons")

    store = Store()
    rail.set_icons(store)
    assert rail.group_has_real_icon(title) is True
    assert rail.group_icon_family(title) == "weapon_icons"
    assert (rail.group_icon_family(title), rail.group_icon_key(title)) in store.asked


def test_two_groups_fold_and_start_collapsed(window, qapp) -> None:
    """≥2 组：抬头可折、**默认收起**（用户 2026-10-02：「折叠栏默认是折叠状态」）。"""
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    rail = _fake_rail(page)

    titles = rail.group_titles()
    assert len(titles) == 2
    assert rail.expanded_groups() == (), "刚进来不该有任何一组是展开的"
    for title in titles:
        assert rail.group_foldable(title) is True
        assert rail.group_collapsed(title) is True, title
        assert rail.group_caret(title).text() == "▸", title
        assert rail.group_caret(title).isHidden() is False
        rows = rail.group_rows(title)
        assert rows, f"{title} 下面该有行"
        assert all(not w.isVisibleTo(rail) for w in rows), f"{title} 说收起却还露着"


def test_clicking_a_group_head_folds_the_same_rows_in_place(window, qapp) -> None:
    """② 点抬头空白 = 展开 / 再点 = 收起；**行控件是同一批**（只 hide，不销毁）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    rail = _fake_rail(page)
    title = rail.group_titles()[0]
    head = rail.group_head(title)
    assert head is not None, "分组抬头该是可点的控件"
    qapp.processEvents()
    before = [id(w) for w in rail.group_rows(title)]

    QTest.mouseClick(head, Qt.LeftButton, pos=head.rect().center())
    qapp.processEvents()
    assert rail.group_collapsed(title) is False, "点抬头空白没展开（抬头没几何？）"
    assert rail.group_caret(title).text() == "▾"
    assert all(w.isVisibleTo(rail) for w in rail.group_rows(title)), "展开了行还藏着"
    assert rail.expanded_groups() == (title,)

    QTest.mouseClick(head, Qt.LeftButton, pos=head.rect().center())
    qapp.processEvents()
    assert rail.group_collapsed(title) is True
    assert rail.group_caret(title).text() == "▸"
    assert all(not w.isVisibleTo(rail) for w in rail.group_rows(title))
    assert [id(w) for w in rail.group_rows(title)] == before, "折叠把行控件销毁/重建了"


def test_a_folded_group_survives_switching_the_face(window, qapp) -> None:
    """③ 同一行内 详情→列→详情：用户展开过的那一组**不许被收回默认**。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    rail = _fake_rail(page)
    all_titles = rail.group_titles()
    title = all_titles[0]
    head = rail.group_head(title)
    qapp.processEvents()
    QTest.mouseClick(head, Qt.LeftButton, pos=head.rect().center())
    qapp.processEvents()
    assert rail.group_collapsed(title) is False

    rail.set_mode(MODE_COLUMNS)
    assert rail.group_titles() == (), "列那一面是平铺的（这一行的全部列），没有可折的分组"
    assert rail.expanded_groups() == (title,), "切到列那一面，记忆不该丢"

    rail.set_mode(MODE_DETAIL)
    assert rail.group_titles() == all_titles, "切回来那一组不见了（这一面该画同一批组）"
    assert rail.group_collapsed(title) is False, "同一行切一次面就把用户展开的组收回了"
    assert rail.group_caret(title).text() == "▾"
    assert all(w.isVisibleTo(rail) for w in rail.group_rows(title))


def test_another_row_comes_back_folded_and_the_old_row_is_remembered(window, qapp) -> None:
    """③ 换一行 → 按**那一行自己**的记忆（第一次见 = 全收起）；切回原行 → 恢复。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    rail = _fake_rail(page, 900001)
    title = rail.group_titles()[0]
    head = rail.group_head(title)
    qapp.processEvents()
    QTest.mouseClick(head, Qt.LeftButton, pos=head.rect().center())
    qapp.processEvents()
    assert rail.expanded_groups() == (title,)

    _fake_rail(page, 900002, reset_memory=False)  # 换一行（同一份两组详情）
    assert rail.expanded_groups() == (), "新的一行继承了上一行的展开态"
    assert all(rail.group_collapsed(item) for item in rail.group_titles())

    _fake_rail(page, 900001, reset_memory=False)  # 切回原行
    assert rail.expanded_groups() == (title,), "切回原行，用户展开过的组没恢复"
    assert rail.group_collapsed(title) is False
    assert all(w.isVisibleTo(rail) for w in rail.group_rows(title))


def test_the_remembered_rows_are_capped() -> None:
    """③ 上限：按行记的那份记忆会随点选无限增 —— 满了丢**最早**那条（FIFO）。

    零 Qt 的一条：直接量那份记忆本身（`_GroupMemory`），不去点 200 行。
    """
    memory = _GroupMemory(limit=3)
    for i in range(5):
        memory.set_open(("Units", i), "G", expanded=True)
    assert memory.rows() == (("Units", 2), ("Units", 3), ("Units", 4)), "不是 FIFO 丢最早的"
    assert not memory.is_open(("Units", 0), "G")

    memory.set_open(("Units", 4), "G", expanded=False)
    assert not memory.is_open(("Units", 4), "G")
    assert len(memory.rows()) == 3, "记着的行数不许超上限"
    assert MAX_REMEMBERED_ROWS >= 100, "常驻上限该是三位数，别退化成几十"


def test_name_cells_open_a_type_ahead_list(window) -> None:
    """用户 2026-10-03（指着「装甲名」那一格）：「**我不要这个垃圾放大镜检索，我要输入就能在
    下面出来一列来给我检索**」。

    判据：名字列（Name / HUDName / UIName）那一格点开的是**可编辑下拉**（打字即筛，
    候选 = 这一列在这张表里用过的值）；正控：数字格（装甲值）仍是**单行框**，不许变下拉。
    """
    window._open_unit_editor(70)
    page = window._unit_editor.bench_page()
    page._on_row_selected("UnitArmors", page.context.row_ids("UnitArmors")[0])
    rail = page.rail
    QApplication.processEvents()

    def holder_of(key: str):
        for holder in rail.findChildren(QWidget):
            if holder.property("role") != "bench-slot-line":
                continue
            label = next(
                (c for c in holder.findChildren(QLabel) if c.property("role") == "bench-rail-key"),
                None,
            )
            if label is not None and label.text() == key:
                return holder
        return None

    def open_cell(key: str) -> None:
        holder = holder_of(key)
        assert holder is not None, f"这一屏该有「{key}」那一行"
        button = next(
            c for c in holder.findChildren(QPushButton) if c.property("role") == "entry-value"
        )
        QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
        QApplication.processEvents()

    open_cell("装甲名")
    # ⚠️ 用例里窗口没 show()：**别用 isVisible()**（那是"祖先也可见"），用 isHidden()
    #    —— 实机那一轮（_work/gui_drive_round69_*.py）量的是 isVisible，两边判据一致。
    combos = [c for c in rail.findChildren(QComboBox) if not c.isHidden()]
    assert len(combos) == 1, "「装甲名」点开该是一个可编辑下拉（打字即出候选）"
    combo = combos[0]
    assert combo.isEditable()
    assert combo.count() >= 2, f"候选该是这一列用过的名字（实机 319 条），拿到 {combo.count()}"
    before = combo.count()
    combo.lineEdit().setText("Men")
    QApplication.processEvents()
    assert 0 < combo.count() < before, f"打字该把候选筛窄：{before} → {combo.count()}"
    rail.close_editor()
    QApplication.processEvents()

    # 正控：数字格不许变成下拉（它也是 control=text，但不是名字列）
    open_cell("装甲值")
    assert not [c for c in rail.findChildren(QComboBox) if not c.isHidden()], "数字格不该有下拉"
    assert [c for c in rail.findChildren(QLineEdit) if not c.isHidden()], "数字格该是单行框"
    rail.close_editor()
