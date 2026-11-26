"""「不要全量保存」的守卫 —— 改一格只做那一格该做的事（2026-10-01 用户口径）。

用户原话（提过几回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」
「尽量避免全量保存，全量保存时，GUI 会卡一下，好多修改完全没必要全量保存」。

四条判据，每条都对着 cProfile / 打点量出来的一个**具体代价**（读数见
`_work/probe_edit_profile.py` 与 `_work/probe_after_a.py`）：

====== ============================================================ ==============
判据   原来发生什么                                                  实测
====== ============================================================ ==============
①      不在台上的那一页也被同步整块重排（卡的订阅回调）                58.9 ms
②      改一格就把 16687 行全扫一遍（validate(instance) 传的是 None）   22–73 ms
③      改一格就重取三页 + 面包屑的图标（哪怕改的是携弹量）              白花
④      只标脏不补刷 = 切回来还是旧值                                  ——
====== ============================================================ ==============

②是**正确性**守卫而不是性能守卫：RuleEngine.validate(instance, table=...) 是引擎
本来就有的增量入口（core/rules/engine.py 模块说明第一句），但"少跑规则"就是**漏报**，
所以这里逐条比对"增量报告 == 全量报告"。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from ba_mod_maker.core.data import find_dataset_root
from ba_mod_maker.core.edit.refs import CellRef

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 步兵单位（Marine Raiders，14 名兵 + 4 行班用武器 + 4 行携弹）—— 编成台那一页的样本。
SQUAD = 70


def _open_bench(window, unit_id: int = SQUAD):
    """双击那条路：进单位 -> 落编成台。返回 (screen, bench)。"""
    window._open_unit_editor(unit_id)
    screen = window._unit_editor
    assert screen.on_bench_page(), "步兵单位应当落在编成台上（land_on_bench）"
    return screen, screen.bench_page()


def _widget_ids(root: QWidget) -> set[int]:
    return {id(w) for w in root.findChildren(QWidget)} | {id(root)}


# --------------------------------------------------------------------------- #
# ① 不在台上的那一页，一个控件都不许重建
# --------------------------------------------------------------------------- #


def test_hidden_page_no_longer_subscribes_to_the_changeset(window) -> None:
    """关系树那一页的卡**不再自己订阅**变更集 —— 那一次回调是同步跑的整块重排。

    在编成台上改一格时，这一页根本不可见，替它重排就是白花 58.9ms/次。
    """
    screen, _bench = _open_bench(window)
    assert screen._main.card._unsubscribe is None, "隐藏页的卡又自己订阅上了"


def test_editing_a_cell_rebuilds_nothing_on_the_hidden_page(window, qapp) -> None:
    """改一格之后，**不在台上的那一页控件一个都没重建**（判据：id 集合一模一样）。

    与 AGENTS §0.7 同一条纪律，只是对象从"当前页"换成"看不见的那一页"。
    """
    screen, bench = _open_bench(window)
    ammo = bench.context.row_ids("WeaponAmmunitions")[0]
    ref = next(
        cell
        for cell in bench.context.cells_of_row("WeaponAmmunitions", ammo)
        if cell.column == "Quantity"
    ).ref

    before = _widget_ids(screen._main)
    saved = window._edit_session.changeset is not None
    assert saved
    value = int(bench.context.changeset.value("WeaponAmmunitions", ammo, "Quantity"))
    bench.context.set_value(ref, value + 7)
    screen._refresh_pages()
    qapp.processEvents()

    assert _widget_ids(screen._main) == before, "隐藏页被重建了（用户看到的就是'闪一下'）"
    assert bench.context.changeset.value("WeaponAmmunitions", ammo, "Quantity") == value + 7


# --------------------------------------------------------------------------- #
# ② 增量校验：报告必须与全量**逐条一致**（正确性守卫）
# --------------------------------------------------------------------------- #


def _assert_incremental_equals_full(screen, what: str) -> None:
    incremental = screen._report_for_changes()
    full = screen._engine.validate(screen._instance)
    assert incremental is not None
    assert incremental.outcomes == full.outcomes, (
        f"{what}：增量报告与全量报告不一致（漏跑规则 = 漏报）"
    )


def test_incremental_report_matches_the_full_one_after_edits(window) -> None:
    """改几张表 + 删一行之后，增量报告必须与全量**逐条 identical**（顺序也要一致）。

    为什么这条最要紧：增量 = "只重跑改动到的那几张表的规则"。规则书里
    `foreign_key` 读 `target`、`required_per_unit` 读 Units、跨表不变量读哪几张表
    根本没声明 —— 少跑一条规则就是**漏报**，而漏报比慢严重得多。
    """
    screen, bench = _open_bench(window)
    context = bench.context

    # 1) Units 上改一格（required_per_unit / 外键规则都读它）
    units_cell = context.cell(CellRef("Units", SQUAD, "InfantrySlots"))
    if units_cell is not None:
        context.set_value(units_cell.ref, int(units_cell.raw or 0) + 1)
        screen._refresh_findings()
        _assert_incremental_equals_full(screen, "改 Units")

    # 2) 携弹量（WeaponAmmunitions 自己的规则）
    ammo = context.row_ids("WeaponAmmunitions")[0]
    quantity = context.changeset.value("WeaponAmmunitions", ammo, "Quantity")
    context.set_value(CellRef("WeaponAmmunitions", ammo, "Quantity"), int(quantity) + 3)
    screen._refresh_findings()
    _assert_incremental_equals_full(screen, "改携弹量")

    # 3) 班用武器（跨表不变量 cross.* 读 SquadWeapons，而它的 table 是 None）
    squad_weapons = context.row_ids("SquadWeapons")
    if squad_weapons:
        order = context.changeset.value("SquadWeapons", squad_weapons[0], "Order")
        context.set_value(CellRef("SquadWeapons", squad_weapons[0], "Order"), int(order or 0) + 5)
        screen._refresh_findings()
        _assert_incremental_equals_full(screen, "改班用武器")

    # 4) 删一行（行级改动走的是另一条记账路）
    context.delete_row("WeaponAmmunitions", ammo)
    screen._refresh_findings()
    _assert_incremental_equals_full(screen, "删携弹行")


def test_incremental_report_equals_full_for_a_breaking_edit(window) -> None:
    """**真的会改变校验结果**的那一刀也要一致：删掉一名成员在用的班用武器。

    上面那条用例改的都是"改不出问题"的值，这条要的是**跨表不变量真的响**——
    不变量归属的表是 `None`（规则书里查不到），全靠"表无关规则每次都跑"兜住。
    """
    screen, bench = _open_bench(window)
    context = bench.context
    before = screen._engine.validate(screen._instance).counts

    squad_weapons = context.row_ids("SquadWeapons")
    assert squad_weapons, "样本单位应当有班用武器行"
    context.delete_row("SquadWeapons", squad_weapons[0])
    screen._refresh_findings()

    full = screen._engine.validate(screen._instance)
    assert full.counts != before, "这一刀没改出任何校验结果 —— 这条用例失去意义了"
    _assert_incremental_equals_full(screen, "删班用武器")


def test_affected_tables_follows_foreign_key_targets(window) -> None:
    """`_affected_tables` 必须把"读这张表的规则所归属的表"也算进来。

    漏了它：删掉 Weapons 一行之后，指向它的 TurretWeapons 外键规则不会重跑（角标不报）。
    """
    screen, _bench = _open_bench(window)
    engine = screen._engine
    affected = screen._affected_tables({"Weapons"})
    via_target = {
        rule.get("table") for rule in engine.rules_for(None) if rule.get("target") == "Weapons"
    }
    assert via_target - {None}, "规则书里应当有以 Weapons 为 target 的外键规则"
    assert via_target <= affected, f"以 Weapons 为 target 的规则没被重跑：{via_target - affected}"


def test_discarding_every_change_recomputes_the_badge(window, qapp) -> None:
    """「放弃改动」（revert_all）之后角标必须回到干净 —— 这条**不走 _on_changed**。

    增量报告是"只重跑改动到的那几张表"算出来的；而 revert_all 直接把账本清空、
    不发 _on_changed，于是没人告诉报告"那几张表要重跑"。
    不整份作废的话，症状是：数据明明改回来了，立绘上那条「校验 N 处」还挂着。
    """
    screen, bench = _open_bench(window)
    context = bench.context
    clean = screen.validation_text
    assert clean == "", f"样本单位一进来就该是干净的，现在是 {clean!r}"

    # 改坏一条**跨表不变量**：删掉一名成员在用的班用武器（角标会报）
    weapons = context.row_ids("SquadWeapons")
    assert weapons
    context.delete_row("SquadWeapons", weapons[0])
    screen._refresh_findings()
    assert screen.validation_text, "改坏了却没冒角标 —— 这条用例失去意义了"

    screen.discard_changes()
    qapp.processEvents()
    assert screen.validation_text == "", f"放弃改动之后角标还在：{screen.validation_text!r}"


# --------------------------------------------------------------------------- #
# ③ 图标：只在"改到图标键那一列"时才重取
# --------------------------------------------------------------------------- #


def test_editing_a_non_icon_column_does_not_refetch_icons(window, qapp) -> None:
    """改携弹量不该重取图标；改 Units.ThumbnailFileName 必须重取（正向对照）。"""
    screen, bench = _open_bench(window)
    calls: list[int] = []
    original = screen._refresh_icons

    def counting() -> None:
        calls.append(1)
        original()

    screen._refresh_icons = counting
    try:
        ammo = bench.context.row_ids("WeaponAmmunitions")[0]
        quantity = bench.context.changeset.value("WeaponAmmunitions", ammo, "Quantity")
        bench.context.set_value(CellRef("WeaponAmmunitions", ammo, "Quantity"), int(quantity) + 1)
        screen._refresh_pages()
        qapp.processEvents()
        assert calls == [], "改携弹量把图标重取了一遍（跟图无关的列）"

        bench.context.set_value(CellRef("Units", SQUAD, "ThumbnailFileName"), "BA_MM_TEST_THUMB")
        screen._refresh_pages()
        qapp.processEvents()
        assert calls, "改了缩略图键却没重取图标 —— 用户口径：我改了就要立即看到效果"
    finally:
        screen._refresh_icons = original


# --------------------------------------------------------------------------- #
# ④ 标脏的页，切回来必须是新值
# --------------------------------------------------------------------------- #


def test_a_dirty_page_shows_the_new_value_when_it_comes_back(window, qapp) -> None:
    """只标脏不补刷 = 假省事：切回关系树那一页时，卡上必须是**新**值。

    判据照 AGENTS §0.7 的第二条来：**值真的变了**才算过 ——
    "控件保住了、值还是旧的"同样是没做到。
    """
    screen, bench = _open_bench(window)
    context = bench.context
    card = screen._main.card.card
    assert card is not None
    # 挑卡上**真画着**的一格：它的 value_ref 直接指着这一行（步兵与载具的 stat 集合不一样，
    # 写死 key 会随样本漂 —— 这里按"指向 Units 那一行"挑，量出来的读数见
    # _work/probe_unit70_stats.py：stealth = Units#70.CurrentStealth）。
    targets = [
        stat
        for stat in card.stats
        if stat.value_ref is not None and stat.value_ref.table == "Units"
    ]
    assert targets, "样本单位卡上应当有指向 Units 那一行的格子"
    target = next((stat for stat in targets if stat.key == "stealth"), targets[0])
    before = str(target.value)
    # 原值一起记下来：后面要**改回去再验一遍**（见用例末尾）。
    old_raw = context.changeset.value(
        target.value_ref.table, target.value_ref.row_id, target.value_ref.column
    )

    new_value = 9 if not str(before).startswith("9") else 21
    context.set_value(target.value_ref, new_value)
    screen._refresh_pages()

    assert screen._main in screen._dirty_pages, "隐藏页应当被标脏，等上台再刷"

    screen.show_main()
    qapp.processEvents()

    assert screen._main not in screen._dirty_pages, "切回来却没补刷"
    shown = screen._main.card.card
    assert shown is not None
    after = str(next(stat.value for stat in shown.stats if stat.key == target.key))
    assert after != before, f"切回来显示的还是旧值（{before}）"

    # 反向再验一次：把那一格改回原样，卡上必须回到原来那个数。
    # 为什么要有这一步：光看"after != before"分不清"卡真的跟着这一格走"还是"别的什么碰巧变了"；
    # 改回去能复原，才证明卡读的就是这一格（与 AGENTS §0.7 第二条同一个精神）。
    context.set_value(target.value_ref, old_raw)
    screen._main.card.refresh_from_changes()
    again = str(next(stat.value for stat in screen._main.card.card.stats if stat.key == target.key))
    assert again == before, f"改回原值卡上却没回来：{again} != {before}"
