"""卡「改一格不重建」的守卫（AGENTS §0.7，用户提过三回的那条）。

用户原话：「我修改的时候你能不能不要全量保存一下，**不然界面闪一下很影响观感**」
「这我提了几回的问题了」。

判据**两条一起**（§0.7 明写，差点被漏过去的一条）：

    控件零重建   **且**   值必须是新的

只满足前一条是**假绿** —— "控件保住了、值还是旧的"与"全丢重建"一样是没做到。
所以这一份每条守卫都两样一起断言。

口径（与先例同一条：docs/交接-2026-09-30-界面批-图标树弹药.md:76/93）：

    卡上每一块有一个**署名**（它从卡上读的那几个字段）。
    署名没变 -> 那一块整块跳过（画出来必然逐字一样，所以跳过是**可证**的）；
    署名变了 -> 只重建**那一块**；块里没有可点的东西时还能只换字、连控件都不换。

读数：_work/probe_card_inplace_split.py（自己那把尺子）、
      _work/probe_vehicle_page.py（Lead 那把，三栏那一页）。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.core.data import find_dataset_root
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.clone import next_row_id
from ba_mod_maker.core.edit.refs import CellRef

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 载具（Stryker RV）：卡上 11 块里「班组成员」那一块**缺席** ——
#: "缺席的块占不占位"这条也一并守住（它曾经让逐块比对永远对不上，退回整块重排）。
VEHICLE = 21
#: 步兵（Marine Raiders）：11 块全画，含「班组成员」。
INFANTRY = 70


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _open(window, unit_id: int):
    """进单位编辑器（与真机同一条路），返回 (屏幕, 编成台页, 左栏那张卡)。"""
    window._open_unit_editor(unit_id)
    screen = window._unit_editor
    bench = screen.bench_page()
    return screen, bench, bench.card


def _ids(root: QWidget) -> set[int]:
    return {id(w) for w in root.findChildren(QWidget)}


def _labels(root: QWidget) -> list[QLabel]:
    return list(root.findChildren(QLabel))


def _content(root: QWidget) -> list[tuple]:
    """这张卡"画出来的样子"（不含 id）—— 拿来比"就地刷 == 整块重建"。"""
    return sorted(
        (
            type(w).__name__,
            str(w.property("role") or ""),
            w.toolTip(),
            w.isHidden(),
            w.text() if isinstance(w, QLabel) else "",
        )
        for w in root.findChildren(QWidget)
    )


def _edit(card, ref: CellRef, value) -> None:
    """走真机那条提交路：写进缓存 -> 卡跟上（refresh_from_changes 是唯一入口）。"""
    card.changeset.set_value(ref.table, ref.row_id, ref.column, value)
    card.refresh_from_changes()


def _ref(bench, table: str, column: str) -> CellRef:
    return CellRef(table, bench.context.unit_id, column)


def _cost_carriers(card) -> list[QLabel]:
    """卡上**画着成本**的那个控件。

    ❗ 别拿 Units.Cost 那一列去比：卡上写的是 card.cost（含改装选项那几笔加价，
    口径见 unit_card.card_cost）—— 拿列值当尺子会量空（实测单位 70：列值 110，
    卡上不是这个数）。
    """
    return [label for label in _labels(card) if label.text() == str(card.current_cost)]


# --------------------------------------------------------------------------- #
# ① 改一格：控件零重建 **且** 值是新值
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("unit_id", [VEHICLE, INFANTRY])
def test_editing_one_cell_rebuilds_nothing_and_still_shows_the_new_value(window, unit_id) -> None:
    """改一格 -> 卡上一个控件都没重建，而**画着那一格的那个控件**上就是新值。

    两条一起判：只保控件不换值 = 假绿（§0.7 明写的那一条）。
    """
    _screen, bench, card = _open(window, unit_id)
    ref = _ref(bench, "Units", "Cost")
    # ❗ 两个数不是一回事：列值 = Units.Cost，卡上那个数 = 列值 + 改装选项那几笔加价
    #    （口径 unit_card.card_cost）。差多少不重要，重要的是**步长一致**：
    #    列值 +37，卡上那个数也必然 +37。
    old = int(bench.context.changeset.value(ref.table, ref.row_id, ref.column))
    shown = int(card.current_cost)
    step = 37

    carriers = _cost_carriers(card)
    assert carriers, f"卡上没有画着成本 {shown} 的那个控件 —— 尺子对不上"
    before_widgets = _ids(card)
    before_blocks = [id(block) for block in card.body_blocks]

    _edit(card, ref, old + step)

    assert _ids(card) == before_widgets, "改一格重建了控件 —— 用户看到的就是'闪一下'"
    assert [id(block) for block in card.body_blocks] == before_blocks, "有块被换掉了"
    assert any(label.text() == str(shown + step) for label in carriers), (
        f"控件保住了，可画成本的那个控件上还是旧值（假绿）："
        f"{[label.text() for label in carriers]} 里没有 {shown + step}"
    )
    assert int(bench.context.changeset.value(ref.table, ref.row_id, ref.column)) == old + step


def test_an_edit_that_changes_nothing_rebuilds_nothing(window) -> None:
    """数据没变时刷一次：一个控件都不许动（"标脏了再补刷"那条路的兜底）。"""
    _screen, _bench, card = _open(window, VEHICLE)
    before = _ids(card)
    card.refresh_from_changes()
    assert _ids(card) == before


# --------------------------------------------------------------------------- #
# ② 结构真的变了：只有那一块重建，别的块一个控件都不动
# --------------------------------------------------------------------------- #


def test_a_structural_change_rebuilds_only_that_block(window) -> None:
    """＋加一名兵（插一行 SquadMembers）-> **只有「班组成员」那一块**换控件。

    这是"署名变了才重建"的另一半：光会跳过不够，结构真变了必须重建 ——
    不然新加的那个兵在卡上根本不出现。同时别的块一个控件都不许动。
    """
    _screen, bench, card = _open(window, INFANTRY)
    blocks_before = list(card.body_blocks)
    loadout_before = len(_ids(blocks_before[-1]))

    bench.bench_squad.add_button.click()  # 真机那条：＋ 加一名兵
    card.refresh_from_changes()

    blocks_after = list(card.body_blocks)
    assert len(blocks_after) == len(blocks_before), "块的个数不该变（只是多了一名兵）"
    rebuilt = [i for i, old in enumerate(blocks_before) if blocks_after[i] is not old]
    # 加一名兵会同时动**两块**：卡上「班组成员」那一块多一行，武器那一块也多一行
    # （班用武器那一套跟着班的人数走）。别的块一个控件都不许动。
    assert len(rebuilt) <= 2, f"只有班组成员 / 武器那两块该重建，实际重建了 {len(rebuilt)} 块"
    # ❗ 认「班组成员（N）」那个**抬头**：炮塔那一块的说明文字里也写着"班组成员"
    #    （"步兵的武器挂在班组成员上"），只按子串找会认错块。
    loadout = next(
        i
        for i, block in enumerate(blocks_after)
        if any("班组成员（" in lb.text() for lb in _labels(block))
    )
    assert loadout in rebuilt, "「班组成员」那一块变了却没重建 —— 新那名兵不会出现"
    assert len(_ids(blocks_after[loadout])) > loadout_before, "块重建了，可新那名兵没画出来"
    for i, old in enumerate(blocks_before):
        if i not in rebuilt:
            assert blocks_after[i] is old, f"第 {i} 块没变却被重建了"


# --------------------------------------------------------------------------- #
# ③ 就地刷出来的卡 == 整块重建出来的卡（防"署名漏字段"的假绿）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "column,value",
    [("Cost", 12345), ("Name", "BA_MM_TEST_NAME"), ("CurrentStealth", 9)],
)
def test_incremental_refresh_matches_a_full_rebuild(window, column, value) -> None:
    """就地刷完之后再整块重建一次，两边**画出来的东西必须逐项一样**。

    ❗ 这是防"假绿"的那把尺子：署名表漏了一个字段（= 判成"没变"而跳过），
    卡上就会留下一格旧值 —— 那正是"看起来快了、其实是错"的那种 bug。
    整块重建是**同一份数据**的另一条渲染路，拿它当基准，漏字段当场露馅。
    """
    _screen, bench, card = _open(window, VEHICLE)
    _edit(card, _ref(bench, "Units", column), value)

    inplace = _content(card)
    card._render(card.card)
    rebuilt = _content(card)

    assert inplace == rebuilt, "就地刷与整块重建画出来的东西不一样 —— 署名漏了字段"


def test_the_hidden_value_is_not_left_behind(window) -> None:
    """改回原值：卡上必须回到原来那个数（不是"只增不减"的假刷新）。"""
    _screen, bench, card = _open(window, VEHICLE)
    ref = _ref(bench, "Units", "Cost")
    shown = int(card.current_cost)
    before = _ids(card)
    carriers = _cost_carriers(card)
    assert carriers

    raw = int(bench.context.changeset.value(ref.table, ref.row_id, ref.column))
    _edit(card, ref, raw + 5)
    assert any(label.text() == str(shown + 5) for label in carriers), "改了却没画出来"
    _edit(card, ref, raw)

    assert any(label.text() == str(shown) for label in carriers), "改回原值卡上却没回来"
    assert _ids(card) == before, "改回去又重建了一遍"


# --------------------------------------------------------------------------- #
# ④ 共享度索引：改一格携弹量不该重建整库索引（也不能重建完还是旧数）
# --------------------------------------------------------------------------- #


def test_a_plain_value_edit_does_not_rebuild_the_sharing_index(instance) -> None:
    """改一格携弹量（跟共享度无关的列）-> 那份整库索引**一次都不重算**。

    实测代价：build_sharing_index 整库扫一遍 10–37ms，而它只读那么几张表的那么几列；
    原来 invalidate() 一律标脏，于是改一格携弹量也要重算一遍 ——
    那一下正压在用户点下去的那一刻上。
    """
    changeset = Changeset(instance)
    data = CardData(instance)
    built = data.sharing  # 先建一次，之后拿身份比"有没有重算"

    ammo = instance.table("WeaponAmmunitions")
    quantity = ammo.column_index("Quantity")
    row_id = ammo.rows[0][ammo.column_index("Id")]
    old = ammo.rows[0][quantity]
    changeset.set_value("WeaponAmmunitions", row_id, "Quantity", old + 1)
    data.invalidate_for(changeset)

    assert data.sharing is built, "改一格携弹量把整库共享度索引重建了一遍"
    # 值必须是新的 —— 别把"快"做成"读到旧值"
    assert data.t("WeaponAmmunitions").by_id(row_id)[quantity] == old + 1


def test_a_sharing_column_change_does_rebuild_the_index(instance) -> None:
    """反过来：改了**外键列**（这行被哪个单位用着）-> 索引必须重算，而且是新数。

    没有这一条，上面那条优化就能被"永远不重建"骗过去。
    """
    changeset = Changeset(instance)
    data = CardData(instance)
    stale = data.sharing

    links = instance.table("UnitArmors")
    unit_column = links.column_index("UnitId")
    armor_column = links.column_index("ArmorId")
    link = next(row for row in links.rows if row[unit_column] == 1)
    link_id = link[links.column_index("Id")]
    mine = link[armor_column]

    armors = instance.table("Armors")
    armor_ids = [row[armors.column_index("Id")] for row in armors.rows]
    spare = next(
        armor_id
        for armor_id in armor_ids
        if armor_id != mine and 1 not in stale.units("Armors", armor_id)
    )
    assert 1 not in stale.units("Armors", spare)

    changeset.set_value("UnitArmors", link_id, "ArmorId", spare)
    data.invalidate_for(changeset)

    assert data.sharing is not stale, "改了外键列却没重建共享度索引（那就是在显示旧数）"
    assert 1 in data.sharing.units("Armors", spare), "重建了，但那个单位没算进去"


def test_inserting_a_row_does_rebuild_the_index(instance) -> None:
    """插一行（行的有无变了）-> 索引也要重算：新行的"属于哪个单位"得进去。"""
    changeset = Changeset(instance)
    data = CardData(instance)
    stale = data.sharing

    links = instance.table("UnitArmors")
    fresh_id = next_row_id(links, changeset)
    changeset.insert_row(
        "UnitArmors",
        {"Id": fresh_id, "UnitId": 2, "ArmorId": links.rows[0][links.column_index("ArmorId")]},
    )
    data.invalidate_for(changeset)

    assert data.sharing is not stale
    assert data.sharing.of("UnitArmors", fresh_id) == 1, "新插的关联行没算进共享度"
