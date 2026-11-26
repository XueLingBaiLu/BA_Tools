"""编成台 ②「班用武器与弹药」：一件武器一行，弹挂在它下面。

口径出处：`docs/交接-2026-10-01-单位编成台设计.md` §3（口径 1 / 2）＋ 设计图
`docs/设计稿-编成台-步兵.png` 的 ② 那一块。**三个真数据样本**（实例 52085）——
下一个会话先看这三行，就知道这一块为什么要这么读：

- **70** Marine Raiders CQC —— SquadWeapons 4 行（Id 51 / 53 / 54 / 386 →
  Mk18 CQBR / M1014 / M32 MGL / M72A10 LAW），每门枪各 1 行 WeaponAmmunitions：
  设计图 ② 那一块的样本，一件武器一行、弹挂在它下面；
- **63** Assaultmen SMAW —— SquadWeapons **#81 = M27 IAR，一行 WeaponAmmunitions 都没有**
  （真数据里就有这种空枪）：所以空枪要写「缺弹 · 空枪」，别静默留白；
- **206** BTR-ZD Skrezhet —— **SquadWeapons 0 行**，可 WeaponAmmunitions 有 4 行
  （834 / 835 / 836 / 837）：② 读的是**班用武器注册表**，不是 WeaponAmmunitions 整表 ——
  载具的弹挂在炮塔武器上，不该出现在这一块。

策略与 `test_ammo_block.py` 一致：**真数据 + 真 EditorContext + 真窗口**。这一层要验的
正是"控件和数据接对了没有"，假控件只能验"我以为的接线"。图标用**假 store**——
不 new IconCache（真 IconCache 会起加载线程、按住 bundle，见 conftest 的
`_icon_threads_do_not_outlive_the_module`）；这一层要验的是"族与键接对了没有"，
不是"图能不能取到"。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from ba_mod_maker.app.screens.editor.pages.base import EditorContext
from ba_mod_maker.app.viewmodel.bench_loadout import (
    MISSING_AMMO_TEXT,
    TABLE_AMMO,
    TABLE_SQUAD_WEAPONS,
    TABLE_WEAPON_AMMO,
    TABLE_WEAPONS,
    ammo_choices,
    ammo_is_known,
    loadout_lines,
    member_carriers,
    owned_ammo_ids,
    quantity_ref,
    registered_weapon_ids,
    structure_key,
)
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.app.widgets.bench_loadout import (
    ADD_WEAPON_TEXT,
    AMMO_INDENT,
    HINT,
    NO_UNIT_TEXT,
    NO_WEAPONS_TEXT,
    TITLE,
    LoadoutBench,
)
from ba_mod_maker.core.assets.families import AMMO_ICON_FAMILY, WEAPON_ICON_FAMILY
from ba_mod_maker.core.data import find_dataset_root
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.sharing import build_sharing_index
from conftest import clean_instance, settle

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 设计图 ② 的样本：4 件武器，每件 1 行弹。
UNIT = 70
#: 真数据里的「缺弹」样本：SquadWeapons #81（M27 IAR）一行弹都没有。
UNIT_EMPTY = 63
#: 载具样本：SquadWeapons 0 行 / WeaponAmmunitions 4 行。
UNIT_VEHICLE = 206
#: 一门枪挂两种弹的样本（VDV Dsh 2）。
UNIT_TWO_AMMO = 645

# 单位 70 那四行（实测，见模块 docstring 的表）
MK18, MK18_AMMO_ROW = 67, 2859
M1014, M1014_AMMO_ROW = 45, 1628
M32, M32_AMMO_ROW = 68, 2248
LAW, LAW_AMMO_ROW = 212, 1980
#: 单位 70 还没注册过的武器（SCAR-H）。
NEW_WEAPON = 71
#: 单位 63 那门**空枪**（M27 IAR）。
EMPTY_WEAPON = 51

#: ② 那一块在界面上应有的行序（武器行 + 它下面的弹行，按界面先后）。
UNIT70_ROW_IDS = [
    51,
    MK18_AMMO_ROW,
    53,
    M1014_AMMO_ROW,
    54,
    M32_AMMO_ROW,
    386,
    LAW_AMMO_ROW,
]


# --------------------------------------------------------------------------- #
# 夹具与小工具
# --------------------------------------------------------------------------- #


class FakeStore(QObject):
    """假图标缓存：谁问过（族, 键）都记下来，并给一张小图。

    ❗ 用例**不 new IconCache**（见模块 docstring）。给真 QPixmap 是为了让
    `IconLabel.has_icon()` 有东西可答 —— "这一格到底画上了没有"是这一层要守的。
    """

    loaded = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, str]] = []

    def pixmap(self, family: str, key: str):
        self.asked.append((family, key))
        pixmap = QPixmap(40, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap


def _card(instance) -> CardData:
    return CardData(instance)


def _line(lines, weapon_id: int):
    return next(line for line in lines if line.weapon_id == weapon_id)


def _ammo_row_ids(lines, weapon_id: int) -> list[int]:
    return [ammo.row_id for ammo in _line(lines, weapon_id).ammo]


def _set_cell(data: CardData, table: str, row_id: int, column: str, value) -> None:
    """直接改内存里那一格（模拟"库里本来就是另一个次序"）。"""
    rows = data.t(table)
    row = rows.by_id(row_id)
    assert row is not None
    row[rows.index_of(column)] = value
    data.invalidate()


def _context(instance, unit_id: int) -> EditorContext:
    """一份真 EditorContext（与 UnitEditorScreen 建的那一份同构）。

    ❗ 这一层不走 window._open_unit_editor：那是 Lead 的装配层（bench_page），
    这一块控件不该被别的块的进度挡住 —— 而这一层要验的正是"控件和数据接对了没有"。
    """
    return EditorContext(
        instance,
        unit_id,
        Changeset(instance),
        CardData(instance),
        build_sharing_index(instance),
        lambda: None,
    )


@pytest.fixture()
def bench_factory(qapp):
    """建一块编成台 ②（用例收尾真销毁 —— 别给测试进程留顶层控件）。

    ❗ 必须依赖 `qapp`：没有 QApplication 就建 QWidget，Qt 会直接
    "Must construct a QApplication before a QWidget" 然后**进程崩掉**（不是用例红）。
    """
    made: list[LoadoutBench] = []

    def build(unit_id: int = UNIT, store=None, parent=None) -> LoadoutBench:
        widget = LoadoutBench(parent)
        widget.set_context(_context(clean_instance(), unit_id))
        if store is not None:
            widget.set_icons(store)
        made.append(widget)
        return widget

    yield build
    for widget in made:
        widget.setParent(None)
        widget.deleteLater()


def _click(widget) -> None:
    """真点一下（QTest 的鼠标事件）—— 不用 emit 冒充"点过了"。"""
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


def _watch(bench: LoadoutBench) -> dict:
    """把三个信号记下来（录制回调，不冒充点击）。"""
    seen: dict = {"selected": [], "status": []}
    bench.row_selected.connect(lambda table, row_id: seen["selected"].append((table, row_id)))
    bench.status_message.connect(seen["status"].append)
    return seen


def _label_with_role(widget, role: str) -> QLabel | None:
    return next(
        (label for label in widget.findChildren(QLabel) if label.property("role") == role), None
    )


# =========================================================================== #
# 一、取数（零 Qt：直接断言那一份纯函数）
# =========================================================================== #


def test_unit_70_is_one_row_per_weapon_with_its_ammo_underneath(instance) -> None:
    """⭐ 设计图 ② 的样本：4 件武器，每件各挂一种弹 —— 行 Id、名字、图标、数量全钉住。"""
    lines = loadout_lines(_card(instance), UNIT)

    assert [
        (line.squad_weapon_id, line.weapon_id, line.label, line.icon_key) for line in lines
    ] == [
        (51, MK18, "Mk18 CQBR", "INF_CQBR"),
        (53, M1014, "M1014", "INF_M1014"),
        (54, M32, "M32 MGL", "INF_M32"),
        (386, LAW, "M72A10 LAW", "INF_LAW"),
    ]
    mk18 = _line(lines, MK18)
    assert [
        (ammo.row_id, ammo.ammo_id, ammo.label, ammo.icon_key, ammo.quantity) for ammo in mk18.ammo
    ] == [(MK18_AMMO_ROW, 143, "5.56x45mm", "AMMO_BULLET_SHORT", 120)]
    assert [line.weapon_ammo_row_id for line in lines] == [
        MK18_AMMO_ROW,
        M1014_AMMO_ROW,
        M32_AMMO_ROW,
        LAW_AMMO_ROW,
    ]
    assert [
        (ammo.ammo_id, ammo.quantity)
        for ammo in _line(lines, M1014).ammo + _line(lines, M32).ammo + _line(lines, LAW).ammo
    ] == [(359, 48), (470, 60), (130, 1)]


def test_ammo_rows_are_sorted_by_order_then_id(instance) -> None:
    """弹行的次序 = (Order, Id) —— 两条判据各验一次（改数据来验，不猜）。"""
    data = _card(instance)

    # ① Order 是主判据：RPG-7V2 那两行的 Order 是 1 / 2
    assert _ammo_row_ids(loadout_lines(data, UNIT_TWO_AMMO), 162) == [4127, 4128]
    _set_cell(data, TABLE_WEAPON_AMMO, 4127, "Order", 2)
    _set_cell(data, TABLE_WEAPON_AMMO, 4128, "Order", 1)
    assert _ammo_row_ids(loadout_lines(data, UNIT_TWO_AMMO), 162) == [4128, 4127], (
        "Order 对调了，弹行次序没跟着翻 —— 说明没按 Order 排"
    )

    # ② Order 一样时按 **Id**（不是按弹种 Id）：RPG-7V 那两行的 Order 都是 0。
    #    把两行的弹种**调换** —— 按 Id 排仍旧 #4136 在前，若按 AmmunitionId 排就会翻过来。
    _set_cell(data, TABLE_WEAPON_AMMO, 4136, "AmmunitionId", 906)
    _set_cell(data, TABLE_WEAPON_AMMO, 4137, "AmmunitionId", 104)
    lines = loadout_lines(data, UNIT_TWO_AMMO)
    assert _ammo_row_ids(lines, 32) == [4136, 4137]
    assert [ammo.ammo_id for ammo in _line(lines, 32).ammo] == [906, 104], (
        "行序对了，但弹种没跟着那一格走（名字 / 图标会张冠李戴）"
    )


def test_a_weapon_without_ammo_reads_as_empty_not_as_an_error(instance) -> None:
    """⭐ 真数据里的空枪：单位 63 的 SquadWeapons #81（M27 IAR）一行弹都没有。

    库里确实没有那一行（不是"读失败"）—— 所以 ammo 是空元组、weapon_ammo_row_id 是 0，
    界面上由 MissingAmmoRow 写成「缺弹 · 空枪」。
    """
    data = _card(instance)
    lines = loadout_lines(data, UNIT_EMPTY)

    assert [(line.squad_weapon_id, line.weapon_id, line.label) for line in lines] == [
        (35, 61, "SMAW"),
        (81, EMPTY_WEAPON, "M27 IAR"),
        (412, 362, "M16A4"),
    ]
    assert [(ammo.row_id, ammo.quantity) for ammo in _line(lines, 61).ammo] == [
        (2596, 3),
        (2701, 3),
    ]
    empty = _line(lines, EMPTY_WEAPON)
    assert empty.ammo == ()
    assert empty.weapon_ammo_row_id == 0
    assert data.t(TABLE_WEAPON_AMMO).where2("UnitId", UNIT_EMPTY, "WeaponId", EMPTY_WEAPON) == ()
    assert [ammo.row_id for ammo in _line(lines, 362).ammo] == [2798]


def test_a_vehicle_weapon_ammunition_row_is_not_a_squad_weapon(instance) -> None:
    """⭐ 单位 206（BTR-ZD）：**SquadWeapons 0 行，WeaponAmmunitions 却有 4 行**。

    这一条就是"② 读 SquadWeapons、不是读 WeaponAmmunitions 整表"的判据 ——
    载具的弹挂在炮塔武器上（TurretWeapons），不属于"班用武器"这一块。
    """
    data = _card(instance)
    assert loadout_lines(data, UNIT_VEHICLE) == ()
    assert len(data.t(TABLE_WEAPON_AMMO).where("UnitId", UNIT_VEHICLE)) == 4


def test_registered_weapons_and_owned_ammo_are_two_different_questions(instance) -> None:
    """「这个班注册了哪些枪」与「这门枪挂了哪些弹」是两句话，别混。"""
    data = _card(instance)
    assert registered_weapon_ids(data, UNIT) == frozenset({MK18, M1014, M32, LAW})
    assert owned_ammo_ids(data, UNIT, MK18) == frozenset({143})
    assert owned_ammo_ids(data, UNIT, NEW_WEAPON) == frozenset(), "没挂的枪问出来必须是空的"


def test_ammo_choices_are_what_the_weapon_uses_in_the_whole_db(instance) -> None:
    """候选弹种的口径只有一份（unit_card.weapon_ammo_choices）：全库这把枪用过的 + 本单位已有的。

    单位 70 的 Mk18 CQBR（Weapons#67）在**全库**只用过一种弹（Ammunitions#143）——
    所以"这门枪能加什么"是有依据的，不是 630 种随便挑。
    """
    data = _card(instance)
    assert ammo_choices(data, UNIT, MK18) == ((143, "5.56x45mm"),)
    assert ammo_is_known(data, UNIT, MK18, 143) is True
    assert ammo_is_known(data, UNIT, MK18, 1) is False, "全库这把枪没用过 #1"
    every_ammo = {row[0] for row in instance.table(TABLE_AMMO).rows}
    assert len(every_ammo) > 100, "Ammunitions 全表不该只有几条 —— 不然这条判据是空转"
    assert {ammo_id for ammo_id, _label in ammo_choices(data, UNIT, MK18)} < every_ammo


def test_member_carriers_counts_the_squad(instance) -> None:
    """删班用武器注册行之前要问的那一句：本单位还有几名成员扛着它。

    单位 70：Mk18 CQBR 有 12 个人扛（主武器）、M1014 有 4 个；单位 63 的 M27 IAR
    一个都没有 —— 所以删它没人受害，删 Mk18 会当场违反「SquadMembers ⊆ SquadWeapons」。
    """
    data = _card(instance)
    assert member_carriers(data, UNIT, MK18) == 12
    assert member_carriers(data, UNIT, M1014) == 4
    assert member_carriers(data, UNIT_EMPTY, EMPTY_WEAPON) == 0


def test_quantity_ref_points_at_that_carrying_row(instance) -> None:
    """改数量写的是**那一行**的 Quantity（按行 Id 寻址，不是按行下标）。"""
    ref = quantity_ref(MK18_AMMO_ROW)
    assert (ref.table, ref.row_id, ref.column) == (TABLE_WEAPON_AMMO, MK18_AMMO_ROW, "Quantity")
    assert str(ref) == "WeaponAmmunitions#2859.Quantity"


# =========================================================================== #
# 二、画（真窗口 + 真 EditorContext）
# =========================================================================== #


def test_the_head_says_what_the_design_says(bench_factory) -> None:
    """抬头三件：标题、灰字说明、「＋ 加一件武器」—— 文案按设计图钉死。"""
    bench = bench_factory(UNIT)
    assert _label_with_role(bench, "bench-section-title").text() == TITLE
    assert _label_with_role(bench, "bench-section-hint").text() == HINT
    assert bench.add_weapon_button().text() == ADD_WEAPON_TEXT
    assert bench.add_weapon_button().isEnabled()


def test_rows_mirror_the_squad_weapons_and_their_ammo(bench_factory) -> None:
    """⭐ 界面上的行 = 库里的行：武器行 4 条、弹行 4 条，且弹行贴在它那门枪下面。"""
    bench = bench_factory(UNIT)

    assert [(row.row_id, row.line.label) for row in bench.weapon_rows()] == [
        (51, "Mk18 CQBR"),
        (53, "M1014"),
        (54, "M32 MGL"),
        (386, "M72A10 LAW"),
    ]
    assert all(row.table == TABLE_SQUAD_WEAPONS for row in bench.weapon_rows())
    assert [
        (row.row_id, row.line.label, row.value_label().text()) for row in bench.ammo_rows()
    ] == [
        (MK18_AMMO_ROW, "5.56x45mm", "×120"),
        (M1014_AMMO_ROW, "12ga SLUG", "×48"),
        (M32_AMMO_ROW, "40×46mm", "×60"),
        (LAW_AMMO_ROW, "66mm HE", "×1"),
    ]
    assert all(row.table == TABLE_WEAPON_AMMO for row in bench.ammo_rows())
    # 行序：武器行后面紧跟着它自己的弹行（库里就是这样挂的）
    assert [row.row_id for row in bench.rows()] == UNIT70_ROW_IDS
    assert [row.row_id for row in bench.weapon_rows()[0].ammo_rows] == [MK18_AMMO_ROW]
    assert bench.lines() == loadout_lines(bench.context().data, UNIT)


def test_ammo_rows_are_indented_and_all_names_start_on_one_line(bench_factory) -> None:
    """弹行缩进一档；同一档里图标位等宽 —— 名字才不会左右跳（设计图每列是一条竖线）。"""
    bench = bench_factory(UNIT)
    weapon_row = bench.weapon_rows()[0]
    ammo_row = bench.ammo_rows()[0]

    assert ammo_row.indent == weapon_row.indent + AMMO_INDENT
    assert weapon_row.indent < ammo_row.indent
    assert {row.indent for row in bench.weapon_rows()} == {weapon_row.indent}
    assert {row.indent for row in bench.ammo_rows()} == {ammo_row.indent}
    assert {row.icon_label().maximumWidth() for row in bench.weapon_rows()} == {80}
    assert {row.icon_label().maximumWidth() for row in bench.ammo_rows()} == {64}


def test_an_empty_weapon_says_missing_ammo_instead_of_leaving_it_blank(bench_factory) -> None:
    """⭐ 单位 63 的 M27 IAR 一行弹都没有 -> 弹位写「缺弹 · 空枪」，别静默留白。"""
    bench = bench_factory(UNIT_EMPTY)

    assert [row.line.label for row in bench.weapon_rows()] == ["SMAW", "M27 IAR", "M16A4"]
    empty_weapon_row = next(row for row in bench.weapon_rows() if row.row_id == 81)
    assert empty_weapon_row.ammo_rows == ()
    placeholder = bench.missing_ammo_rows()
    assert len(placeholder) == 1
    assert placeholder[0].name_label().text() == MISSING_AMMO_TEXT
    assert "「＋」" in placeholder[0].detail_label().text()
    assert placeholder[0].indent == empty_weapon_row.indent + AMMO_INDENT
    # 占位行就贴在那门枪下面
    rows = bench.rows()
    assert rows.index(placeholder[0]) == rows.index(empty_weapon_row) + 1
    # 另外两门枪的弹行照常画
    assert [row.row_id for row in bench.ammo_rows()] == [2596, 2701, 2798]


def test_the_block_shows_nothing_when_there_are_no_squad_weapons(bench_factory) -> None:
    """载具（单位 206）：这一块写一句"还没有"，不留白、也不装成有内容。"""
    bench = bench_factory(UNIT_VEHICLE)
    assert bench.weapon_rows() == () and bench.ammo_rows() == ()
    assert _label_with_role(bench, "bench-empty").text() == NO_WEAPONS_TEXT


def test_icons_come_from_the_weapon_and_ammo_families(bench_factory) -> None:
    """图标 = Weapons.HUDIcon（族 weapon_icons）/ Ammunitions.HUDIcon（族 ammo_icons）。"""
    store = FakeStore()
    bench = bench_factory(UNIT, store)

    weapon_row = bench.weapon_rows()[0]
    assert weapon_row.icon_label().family == WEAPON_ICON_FAMILY
    assert weapon_row.icon_label().key == "INF_CQBR"
    ammo_row = bench.ammo_rows()[0]
    assert ammo_row.icon_label().family == AMMO_ICON_FAMILY
    assert ammo_row.icon_label().key == "AMMO_BULLET_SHORT"
    assert weapon_row.icon_label().has_icon() and ammo_row.icon_label().has_icon()
    assert (WEAPON_ICON_FAMILY, "INF_CQBR") in store.asked
    assert (AMMO_ICON_FAMILY, "AMMO_BULLET_SHORT") in store.asked


def test_the_icon_store_comes_from_outside(bench_factory) -> None:
    """store 是外面递进来的（`set_icons`）：没接就留空位、接了原地补上，都不重建控件。"""
    bench = bench_factory(UNIT)  # 没接 store
    weapon_row = bench.weapon_rows()[0]
    assert not weapon_row.icon_label().has_icon()

    store = FakeStore()
    bench.set_icons(store)

    assert bench.weapon_rows()[0] is weapon_row, "set_icons 不该重建行"
    assert weapon_row.icon_label().has_icon()


def test_the_weapon_and_ammo_rows_clone_their_templates(bench_factory) -> None:
    """⭐ 步兵这一块也加上 ⧉（用户：「步兵也加上」）—— 复制 `Weapons` / `Ammunitions` 模板另存一份。

    与 ① 的武器/弹药行同一条口径（交接 §23–§25）：复制模板、本行（`SquadWeapons` /
    `WeaponAmmunitions`）指过去；别人用着的那一行一个字节不动。
    """
    bench = bench_factory(UNIT)
    bench.section().set_collapsed(False)
    # ❗ 读的是**这一块自己的实例**：`bench_factory` 每次给的是 `clean_instance()`
    #    （写操作不许污染共享的那一份），拿外层 fixture 的 instance 查会"什么都没发生"。
    instance = bench.context().instance

    def column_values(table: str, column: str) -> set:
        rows = instance.table(table)
        index = rows.column_index(column)
        return {row[index] for row in rows.rows}

    weapon_row = bench.weapon_rows()[0]
    assert [tool.text() for tool in weapon_row.tools()] == ["⧉"], "武器行要有一颗复制"
    weapons_before = column_values("Weapons", "Id")
    old_weapon = column_values("SquadWeapons", "WeaponId")
    _click(weapon_row.tools()[0])
    new_weapons = column_values("Weapons", "Id") - weapons_before
    assert len(new_weapons) == 1, "模板行没另存出来"
    new_weapon = new_weapons.pop()
    assert new_weapon in column_values("SquadWeapons", "WeaponId"), "本行没指到新模板"
    assert old_weapon <= column_values("SquadWeapons", "WeaponId"), "别的行不许动"
    assert old_weapon <= column_values("Weapons", "Id")

    ammo_row = bench.ammo_rows()[0]
    assert [tool.text() for tool in ammo_row.tools()] == ["⧉"], "弹药行要有一颗复制"
    ammos_before = column_values("Ammunitions", "Id")
    old_ammo = column_values("WeaponAmmunitions", "AmmunitionId")
    _click(ammo_row.tools()[0])
    new_ammos = column_values("Ammunitions", "Id") - ammos_before
    assert len(new_ammos) == 1, "弹种模板没另存出来"
    new_ammo = new_ammos.pop()
    assert new_ammo in column_values("WeaponAmmunitions", "AmmunitionId"), "本行没指到新弹种"
    assert old_ammo <= column_values("WeaponAmmunitions", "AmmunitionId")


def test_refresh_is_idempotent(bench_factory) -> None:
    """数据变了重画：连画三次行序不变、主体控件数不涨（不是"只加不清"）。"""
    bench = bench_factory(UNIT)
    assert [row.row_id for row in bench.rows()] == UNIT70_ROW_IDS
    widgets = len(bench.section().body_widgets())

    bench.refresh()
    bench.refresh()

    assert [row.row_id for row in bench.rows()] == UNIT70_ROW_IDS
    assert len(bench.section().body_widgets()) == widgets


def test_setting_the_context_to_none_clears_the_block(bench_factory) -> None:
    """None = 清空（换单位 / 离开编辑器那条路）。"""
    bench = bench_factory(UNIT)
    bench.set_context(None)
    assert bench.rows() == ()
    assert bench.lines() == ()
    assert bench.selected_row_id() == 0


# =========================================================================== #
# 三、点击 / 选中 / 去看那一行
# =========================================================================== #


def test_clicking_a_row_reports_which_row_and_keeps_it_selected(bench_factory) -> None:
    """点行 -> row_selected(表, 行Id)，并且控件里那份选中态跟着走（同一时刻只亮一行）。"""
    bench = bench_factory(UNIT)
    seen = _watch(bench)

    weapon_row = bench.weapon_rows()[0]
    _click(weapon_row)
    assert seen["selected"] == [(TABLE_SQUAD_WEAPONS, 51)]
    assert (bench.selected_table(), bench.selected_row_id()) == (TABLE_SQUAD_WEAPONS, 51)
    assert weapon_row.selected is True
    assert [row.selected for row in bench.weapon_rows()] == [True, False, False, False]

    ammo_row = bench.ammo_rows()[0]
    _click(ammo_row)
    assert seen["selected"][-1] == (TABLE_WEAPON_AMMO, MK18_AMMO_ROW)
    assert bench.selected_row_id() == MK18_AMMO_ROW
    assert ammo_row.selected is True and weapon_row.selected is False


def test_the_rows_have_no_arrow_any_more(bench_factory) -> None:
    """❗ 翻口径（2026-10-02）：行尾那颗 ↗（去看这一行）按用户要求**删了**。

    武器行 / 弹药行现在只剩 ＋（挂武器 / 加弹种）与 ✕（删关联行）；
    "去看这一行的全部列"点这一行本身就有（右栏）。这一条原来叫
    `test_the_arrow_asks_for_the_full_row`。
    """
    bench = bench_factory(UNIT)

    assert not hasattr(bench.weapon_rows()[0], "link_button")
    assert not hasattr(bench.ammo_rows()[0], "link_button")
    assert not hasattr(bench, "row_activated")


def test_only_plus_and_cross_no_swap_button(bench_factory) -> None:
    """用户口径 1：**只有 ＋ 和 ✕，没有"换"** —— 这一块上不许出现"换"字。"""
    bench = bench_factory(UNIT_EMPTY)
    texts = [button.text() for button in bench.findChildren(QPushButton)]
    assert texts, "这一块本来就该有按钮 —— 不然这条守卫是空转"
    assert not [text for text in texts if "换" in text or "🔁" in text], texts


# =========================================================================== #
# 四、删（写库一律经 EditorContext）
# =========================================================================== #


def test_the_cross_on_a_weapon_row_deletes_only_the_link_row(bench_factory) -> None:
    """⭐ 武器行 ✕ = 删那一行 SquadWeapons（**Weapons 模板行一行不动**），并说出还有谁在用。"""
    bench = bench_factory(UNIT)
    templates_before = len(bench.context().instance.table(TABLE_WEAPONS).rows)

    bench.weapon_rows()[0].remove_button().click()
    settle()

    remaining = bench.context().data.t(TABLE_SQUAD_WEAPONS).where("UnitId", UNIT)
    assert len(remaining) == 3
    assert bench.context().data.t(TABLE_SQUAD_WEAPONS).i(remaining[0], "Id") == 53
    assert len(bench.context().instance.table(TABLE_WEAPONS).rows) == templates_before
    assert [row.line.label for row in bench.weapon_rows()] == ["M1014", "M32 MGL", "M72A10 LAW"]
    assert [row.row_id for row in bench.rows()] == [
        53,
        M1014_AMMO_ROW,
        54,
        M32_AMMO_ROW,
        386,
        LAW_AMMO_ROW,
    ]
    assert "已移除班用武器" in bench.status_text()
    assert "Weapons 模板行没动" in bench.status_text()
    assert "12 名成员扛着它" in bench.status_text(), "还有 12 个人扛着 Mk18 —— 必须当场说清"


def test_the_cross_on_an_ammo_row_leaves_the_weapon_row_alone(bench_factory) -> None:
    """⭐ 弹行 ✕ = 删那一行携弹行；武器行还在，那门枪当场变成「缺弹 · 空枪」。"""
    bench = bench_factory(UNIT)

    bench.ammo_rows()[0].remove_button().click()
    settle()

    assert bench.context().data.weapon_ammo(UNIT, MK18) == ()
    assert [line.squad_weapon_id for line in bench.lines()] == [51, 53, 54, 386]
    assert [row.row_id for row in bench.ammo_rows()] == [M1014_AMMO_ROW, M32_AMMO_ROW, LAW_AMMO_ROW]
    assert [row.row_id for row in bench.rows()] == [
        51,
        0,
        53,
        M1014_AMMO_ROW,
        54,
        M32_AMMO_ROW,
        386,
        LAW_AMMO_ROW,
    ], "缺弹占位行的 row_id 是 0，它就该贴在那门枪下面"
    assert len(bench.missing_ammo_rows()) == 1
    assert "已移除弹种" in bench.status_text()
    # 模板行一行不动（Ammunitions 少一行都算错）
    assert bench.context().data.t(TABLE_AMMO).by_id(143) is not None


# =========================================================================== #
# 五、就地改携弹量
# =========================================================================== #


def test_clicking_the_quantity_opens_the_inline_editor_in_that_cell(bench_factory) -> None:
    """点「×120」-> 就在那一格上开浮层（不弹对话框、没有确认按钮），而且**不算选中这一行**。"""
    bench = bench_factory(UNIT)
    ammo_row = bench.ammo_rows()[0]

    _click(ammo_row.value_label())

    assert bench.has_editor() and bench.open_ammo_row() is ammo_row
    assert ammo_row.has_editor
    assert bench.editor() is not None and bench.editor().parent() is ammo_row
    assert not ammo_row.value_label().isVisibleTo(ammo_row), "值那一格要让给浮层"
    assert bench.selected_row_id() == 0, "点数量只是改值，别顺带把它选中（那一下被吃掉了）"


def test_typing_a_new_quantity_writes_it_through_the_context(bench_factory) -> None:
    """回车提交 -> set_value(WeaponAmmunitions#2859.Quantity)；行跟着重画成 ×999。"""
    bench = bench_factory(UNIT)
    ammo_row = bench.ammo_rows()[0]
    _click(ammo_row.value_label())
    editor = bench.editor()
    assert editor is not None

    editor.input_widget.setText("999")
    QTest.keyClick(editor.input_widget, Qt.Key.Key_Return)
    settle()

    assert bench.context().changeset.value(TABLE_WEAPON_AMMO, MK18_AMMO_ROW, "Quantity") == 999
    assert bench.context().data.weapon_ammo(UNIT, MK18) == ((143, 999, 0),)
    assert bench.ammo_rows()[0].value_label().text() == "×999"
    assert not bench.has_editor(), "改完要收工"
    assert "已改携弹量" in bench.status_text() and "×120 → ×999" in bench.status_text()


def test_a_bad_quantity_is_not_written_and_the_status_bar_says_so(bench_factory) -> None:
    """非法输入**不写库**：缓存里连一条改动都不该有，状态栏给一句人话（不弹窗）。"""
    bench = bench_factory(UNIT)
    ammo_row = bench.ammo_rows()[0]
    _click(ammo_row.value_label())
    editor = bench.editor()
    assert editor is not None

    editor.input_widget.setText("abc")
    QTest.keyClick(editor.input_widget, Qt.Key.Key_Return)
    settle()

    changeset = bench.context().changeset
    assert changeset.change_for(TABLE_WEAPON_AMMO, MK18_AMMO_ROW, "Quantity") is None
    assert changeset.value(TABLE_WEAPON_AMMO, MK18_AMMO_ROW, "Quantity") == 120
    assert "携弹量没改" in bench.status_text() and "值没写进去" in bench.status_text()
    assert ammo_row.line.quantity == 120, "控件里记的那一份也不许变"


# =========================================================================== #
# 六、＋：加一件武器 / 给这门枪加一种弹
# =========================================================================== #


def test_the_plus_on_a_weapon_row_locks_the_weapon_and_offers_ammo(bench_factory) -> None:
    """⭐ 武器行「＋」= 给**这门枪**加弹：第一列（武器）钉死看不清，只让人挑弹种。"""
    bench = bench_factory(UNIT)
    # ❗ 折叠栏默认收起（用户 2026-10-02 口径）：候选列表长在这一块的**主体**里，
    #    先把整块展开再验"它真的冒出来了" —— 否则是拿"整块收着"当"候选没冒出来"。
    bench.section().set_collapsed(False)
    weapon_row = bench.weapon_rows()[0]

    weapon_row.add_button().click()

    picker = bench.picker()
    assert picker is not None and bench.picker_visible()
    assert picker.table == TABLE_WEAPONS and picker.second_table == TABLE_AMMO
    assert picker.first_locked is True
    assert picker.current_first_id == MK18, "钉死的必须是点的那门枪"
    assert picker.second_count > 0
    # 就地冒在**这门枪的弹行下面**（不是页面底部、更不是弹窗）
    layout = bench.section().body_layout()
    assert layout.indexOf(picker.parentWidget()) == layout.indexOf(weapon_row.ammo_rows[-1]) + 1


def test_picking_an_ammo_inserts_a_carrying_row_for_that_weapon(bench_factory) -> None:
    """选定 -> WeaponAmmunitions 多一行（UnitId, WeaponId, AmmunitionId, Quantity=1）；
    库里没见过的组合**照样写**，只在状态栏提示一句（Lead 2026-10-01 批复）。"""
    bench = bench_factory(UNIT)
    bench.weapon_rows()[0].add_button().click()
    picker = bench.picker()
    assert picker is not None
    data = bench.context().data
    # 候选里挑一条"全库这把枪没见过"的 —— 走那条"只提示、照样写"的分支
    # （候选次序由 search_rows 定，不写死下标，免得换个次序这条用例就假绿）。
    index = next(
        i
        for i in range(picker.second_count)
        if not ammo_is_known(data, UNIT, MK18, picker.second_row_id_at(i))
    )
    ammo_id = picker.second_row_id_at(index)
    assert ammo_id not in owned_ammo_ids(data, UNIT, MK18), "前置条件：这条弹还没挂"

    picker.pick_second(index)  # 等价于点第二列那一条
    settle()

    entries = bench.context().data.weapon_ammo(UNIT, MK18)
    assert (ammo_id, 1) in [(row_ammo, quantity) for row_ammo, quantity, _order in entries], entries
    assert len(entries) == 2
    assert bench.picker() is None and not bench.picker_visible(), "选定之后候选列表要收掉"
    assert len(bench.ammo_rows()) == 5
    assert [ammo.ammo_id for ammo in bench.weapon_rows()[0].ammo_rows] == [143, ammo_id]
    assert "已加弹" in bench.status_text() and "默认 1 发" in bench.status_text()
    assert "全库还没见过这件武器配" in bench.status_text(), "库外组合要提示，但不拦"


def test_adding_the_same_ammo_twice_is_refused_with_a_sentence(bench_factory) -> None:
    """这门枪已经挂着那种弹了 -> 不再插一行（唯一键 班 × 武器 × 弹种），状态栏说清去哪改。"""
    bench = bench_factory(UNIT)
    before = len(bench.context().data.t(TABLE_WEAPON_AMMO).where("UnitId", UNIT))

    bench.weapon_rows()[0].add_button().click()
    picker = bench.picker()
    assert picker is not None
    picker.pair_picked.emit(MK18, 143)  # 与"点了第二列里 5.56x45mm 那一条"同一条路
    settle()

    after = len(bench.context().data.t(TABLE_WEAPON_AMMO).where("UnitId", UNIT))
    assert after == before == 4
    assert "已经挂着" in bench.status_text() and "×数量" in bench.status_text()


def test_adding_a_weapon_already_in_the_squad_is_refused(bench_factory) -> None:
    """uniq.SquadWeapons（班 × 武器）是 **error 级**：已经在册的那门枪再加一次要拦住。"""
    bench = bench_factory(UNIT)
    before = len(bench.context().data.t(TABLE_SQUAD_WEAPONS).where("UnitId", UNIT))

    bench.add_weapon_button().click()
    picker = bench.picker()
    assert picker is not None
    picker.picked.emit(MK18)  # 与"在候选列表里点了 Mk18 CQBR 那一条"同一条路
    settle()

    after = len(bench.context().data.t(TABLE_SQUAD_WEAPONS).where("UnitId", UNIT))
    assert after == before == 4
    assert "已经在班用武器里了" in bench.status_text()


def test_adding_a_new_weapon_registers_it_and_says_it_has_no_ammo_yet(bench_factory) -> None:
    """⭐「＋ 加一件武器」-> 插一行 SquadWeapons（Id 走 next_row_id、Order = 行数）；
    **加武器不带弹是允许的**，但状态栏要说清"还没有配弹"。"""
    bench = bench_factory(UNIT)
    data = bench.context().data
    squad = data.t(TABLE_SQUAD_WEAPONS)
    biggest = max(squad.i(row, "Id") for row in squad.all())

    bench.add_weapon_button().click()
    picker = bench.picker()
    assert picker is not None
    picker.picked.emit(NEW_WEAPON)
    settle()

    rows = data.t(TABLE_SQUAD_WEAPONS).where("UnitId", UNIT)
    assert len(rows) == 5
    fresh = next(row for row in rows if squad.i(row, "WeaponId") == NEW_WEAPON)
    fresh_id = squad.i(fresh, "Id")
    assert fresh_id > biggest, "新行 Id 必须大于库里现有的最大 Id（next_row_id，不是行数+1）"
    assert squad.i(fresh, "Order") == 4, "Order 排到最后（行数）"
    assert bench.weapon_rows()[-1].line.label == "SCAR-H"
    assert len(bench.missing_ammo_rows()) == 1, "新武器还没有弹 -> 就是那条「缺弹 · 空枪」"
    assert "已加班用武器" in bench.status_text()
    assert "还没有配弹" in bench.status_text()


# =========================================================================== #
# 六点五、改一格别闪：**值**变了就地换字，一个控件都不重建
#
# 用户口径（提过几回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
# 先例：docs/交接-2026-09-30-界面批-图标树弹药.md:76/93 —— 署名变了才整套重建，没变走
# reload()；量它的探针是 _work/probe_bench_flash.py（BenchRow 保住了几个）。
# =========================================================================== #


def test_changing_one_value_keeps_every_row_widget(bench_factory) -> None:
    """⭐ 改一格携弹量 = **值**变了：控件 id 集合前后**完全相同**，但字必须是新的。

    "控件保住了、值没变" = 假绿 —— 所以这条同时钉住控件身份与「×N」。
    """
    bench = bench_factory(UNIT)
    before = [id(row) for row in bench.rows()]
    weapon_ids = [id(row) for row in bench.weapon_rows()]
    ammo_ids = [id(row) for row in bench.ammo_rows()]
    ammo_row = bench.ammo_rows()[0]
    assert ammo_row.value_label().text() == "×120"

    bench.context().set_value(quantity_ref(MK18_AMMO_ROW), 321)
    bench.reload_values()  # 外层（bench_page）判署名没变时调的就是这一手

    assert [id(row) for row in bench.rows()] == before, "改一格不许重建行控件"
    assert [id(row) for row in bench.weapon_rows()] == weapon_ids
    assert [id(row) for row in bench.ammo_rows()] == ammo_ids
    assert bench.ammo_rows()[0] is ammo_row
    assert ammo_row.value_label().text() == "×321"
    assert ammo_row.line.quantity == 321
    assert [ammo.row_id for ammo in bench.weapon_rows()[0].ammo_rows] == [MK18_AMMO_ROW]


def test_every_displayed_field_follows_a_value_only_change(bench_factory) -> None:
    """值这一路要覆盖**所有显示字段**：武器名 / 武器图标 / 弹种名 / 弹图标 / ×N。

    漏一个 = 界面上留一句旧字，而控件都还在 —— 最容易被当成"没改"。
    """
    bench = bench_factory(UNIT, FakeStore())
    weapon_row = bench.weapon_rows()[0]
    ammo_row = bench.ammo_rows()[0]
    context = bench.context()

    context.set_value(CellRef(TABLE_WEAPON_AMMO, MK18_AMMO_ROW, "Quantity"), 77)
    context.set_value(CellRef(TABLE_WEAPONS, MK18, "HUDName"), "Mk18 CQBR（改）")
    context.set_value(CellRef(TABLE_WEAPONS, MK18, "HUDIcon"), "INF_M1014")
    context.set_value(CellRef(TABLE_AMMO, 143, "HUDName"), "5.56x45mm（改）")
    context.set_value(CellRef(TABLE_AMMO, 143, "HUDIcon"), "AMMO_M72")
    bench.reload_values()

    assert weapon_row is bench.weapon_rows()[0], "值这一路不许换控件"
    assert weapon_row.name_label().text() == "Mk18 CQBR（改）"
    assert weapon_row.icon_label().key == "INF_M1014"
    assert weapon_row.icon_label().has_icon()
    assert ammo_row.name_label().text() == "5.56x45mm（改）"
    assert ammo_row.icon_label().key == "AMMO_M72"
    assert ammo_row.value_label().text() == "×77"


def test_refresh_only_rewrites_values_when_the_structure_did_not_change(bench_factory) -> None:
    """页面那条路（refresh）：署名没变就只换字 —— 与 reload_values 同一条口径。"""
    bench = bench_factory(UNIT)
    before = [id(row) for row in bench.rows()]

    bench.context().set_value(quantity_ref(MK18_AMMO_ROW), 7)
    bench.refresh()

    assert structure_key(bench.lines()) == structure_key(loadout_lines(bench.context().data, UNIT))
    assert [id(row) for row in bench.rows()] == before
    assert bench.ammo_rows()[0].value_label().text() == "×7"


def test_reload_values_falls_back_to_a_rebuild_when_the_structure_changed(bench_factory) -> None:
    """结构真变了（删掉最后一发弹）：就地换字换不出来 —— 退回整块重建，绝不留旧行。"""
    bench = bench_factory(UNIT)
    before = [id(row) for row in bench.rows()]

    bench.context().delete_row(TABLE_WEAPON_AMMO, MK18_AMMO_ROW)
    bench.reload_values()  # 外层若没看出结构变了，这一手必须自己兜住

    assert [id(row) for row in bench.rows()] != before
    assert [row.row_id for row in bench.rows()] == [
        51,
        0,
        53,
        M1014_AMMO_ROW,
        54,
        M32_AMMO_ROW,
        386,
        LAW_AMMO_ROW,
    ]
    assert len(bench.missing_ammo_rows()) == 1
    assert bench.missing_ammo_rows()[0].name_label().text() == MISSING_AMMO_TEXT


def test_the_three_empty_states_still_swap(bench_factory) -> None:
    """三种处境（没打开单位 / 这个班没武器 / 有行）各自那句灰字都要换对。

    ❗ 这是回归闸：值那一路一开始只比"行署名"，空结构 == 空结构 —— 从"没打开单位"
    切到"这个班没武器"时灰字会留着不换（实测抓到的）。
    """
    bench = bench_factory(UNIT)
    assert [row.row_id for row in bench.rows()] == UNIT70_ROW_IDS

    bench.set_context(None)
    assert _label_with_role(bench, "bench-empty").text() == NO_UNIT_TEXT
    assert bench.rows() == ()

    bench.set_context(_context(clean_instance(), UNIT_VEHICLE))
    assert _label_with_role(bench, "bench-empty").text() == NO_WEAPONS_TEXT
    assert bench.rows() == ()

    bench.set_context(_context(clean_instance(), UNIT))
    assert [row.row_id for row in bench.rows()] == UNIT70_ROW_IDS
    assert _label_with_role(bench, "bench-empty") is None, "有行了，那句灰字要收掉"


# =========================================================================== #
# 七、结构守卫
# =========================================================================== #


def test_building_the_block_never_maps_a_stray_window(module_window, bench_factory, qapp) -> None:
    """建这一块、点开两处候选列表，都不许冒出顶层小窗（回归闸）。

    形状只有一条（见 app/diag/log.py::StrayWindowGuard）：**控件自己还没有父控件的时候
    被显示**（setVisible / show）就会被 Qt 映射成一个真顶层窗，用户看到的是"闪一下的
    小弹窗"。本块里两处候选列表宿主 + 就地编辑浮层都要走"先加进布局、再显示"这条路。

    ❗ 必须真的 show() 一个宿主窗口：控件**可见**时才会被映射；不可见的控件怎么折腾
    都不出窗，那样这条用例抓不到 bug。
    """
    from ba_mod_maker.app.diag import install_stray_window_guard

    guard = install_stray_window_guard(qapp)
    module_window.show()
    qapp.processEvents()
    before = len(guard.caught)

    for unit_id in (UNIT, UNIT_EMPTY, UNIT_VEHICLE):
        bench = bench_factory(unit_id, FakeStore(), parent=module_window)
        bench.add_weapon_button().click()  # 就地冒候选
        if not bench.weapon_rows():
            continue
        bench.weapon_rows()[0].add_button().click()  # 空枪那条也要试
        if bench.ammo_rows():
            _click(bench.ammo_rows()[0].value_label())  # 就地编辑浮层
        settle(1)

    qapp.processEvents()
    assert guard.caught[before:] == [], guard.caught[before:]


# =========================================================================== #
# 八、折叠栏（用户 2026-10-02）：默认收起、点抬头展开 / 收起，控件一个都不重建
# =========================================================================== #


def test_the_block_is_collapsed_by_default_and_toggles_without_rebuilding(bench_factory) -> None:
    """⭐ ② 这一块默认收起（用户口径「折叠栏默认是折叠状态」）；点抬头展开、再点收起。

    判据三条（与 tests/test_bench_kit.py 那套同源，这里验的是**这一块真的吃到默认值**）：
    默认 `is_collapsed()`；点抬头空白换态 + 记号 ▸/▾；两种态下 `body_widgets()` 是
    **同一批控件**（AGENTS §0.7：收起只是 hide，一个都不许重建）。
    """
    bench = bench_factory(UNIT)
    section = bench.section()

    assert section.collapsible()
    assert section.is_collapsed() is True
    assert section.caret_label().text() == "▸"
    kept = section.body_widgets()
    assert kept, "武器行该在主体里 —— 不然这条守卫是空转"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is False
    assert section.caret_label().text() == "▾"
    assert section.body_widgets() == kept, "展开不许重建控件"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is True
    assert section.body_widgets() == kept, "收起同样不许重建控件"
    assert bench.status_text() == "", "折叠是纯界面态：不说话、更不写库"


def test_the_ammo_picker_lives_inside_the_block_body(bench_factory) -> None:
    """候选宿主在**主体**里（回归闸）：挂回主体外，"收起这一块"就藏不住它了。"""
    bench = bench_factory(UNIT)
    bench.weapon_rows()[0].add_button().click()
    picker = bench.picker()
    assert picker is not None
    assert bench.section().body().isAncestorOf(picker)
