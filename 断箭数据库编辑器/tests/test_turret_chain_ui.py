"""卡上那三块（设计稿块 4 / 5 / 8 / 10）的验收：炮塔四层 · 改装槽互斥 · 21 个挂载槽。

用户明确要求（docs/设计-界面重做-定稿.md §2.3 与 §3）：

    ① 卡上画「单位 › 炮塔 › 武器 › 弹种」四层，**四层都能点**（点了中栏换成那一行的全部列）；
    ② 卡上画「改装槽 › 选项（互斥）」：选中的那一项高亮，右边标费用与"它改了什么"；
    ③ **21 个炮塔挂载槽全部列出来、一个都不折叠**（设计图 _work/design/block10.png）。

❗ 卡片仍然是**只读**的：卡上不写库、也不给 ＋/✕（写入口在关系树行尾与中栏词条面板，
Lead 定的接缝口径）。所以这一份也钉住"新加的那几块里一颗 QPushButton / QComboBox 都没有"。

样本（都在 DataBaseCompiled/52085）：

    21  = Stryker RV（载具：1 座炮塔 / 2 个改装槽 —— block9.png 那张图）
    287 = F-15E Strike Eagle（战机：6 座炮塔 / 5 个改装槽，其中一个占 12 个挂载槽 ——
          block10.png 那张图就是它）
    1   = BTR-82A（一门枪两种弹）
    645 = 步兵（没有炮塔）
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from ba_mod_maker.core.data import find_dataset_root
from conftest import settle

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 载具：1 座炮塔（M240 塔 #522）+ 2 个改装槽（武器系统 / 装甲组件）。
STRYKER = 21
#: 战机：设计稿 block10 的样本 —— 5 个改装槽，机腹挂架那条选项占 12 个挂载槽。
F15E = 287
#: 载具：一门枪（2А72）两种弹。
BTR82A = 1
#: 步兵：没有炮塔（武器挂在班组成员上）。
INFANTRY = 645

#: Stryker 的两个改装槽（Modifications#19 武器系统 / #5 装甲组件）。
MOD_WEAPONS = 19
MOD_ARMOR = 5
#: 武器系统那一槽：757 = 当前选中的 M240、1896 = Bushmaster2（+25）。
OPT_M240 = 757
OPT_BUSHMASTER2 = 1896
#: 装甲组件那一槽：1898 = SRAT + 战利品（+55）。
OPT_SRAT_APS = 1898
#: F-15E 的「机腹挂架」那一槽（设计稿 block10 的 8 选 1）。
F15_FUSELAGE = 307
#: 21 个炮塔挂载槽 —— 列名的唯一出处是 viewmodel 的 TURRET_SLOTS。
SLOT_COUNT = 21


# 夹具（qapp / instance / window）统一在 tests/conftest.py：
#   window = **一个模块一个**主窗口，每个用例只把一份干净数据接回去。


def _open(window, unit_id: int):
    """进编辑器，返回 (主编辑页, 右栏那张只读卡)。

    ❗ 2026-10-01：载具 / 直升机 / 固定翼现在**默认落在编成台**（见 bench_page.bench_kind），
    而这一份守的是**三栏那一页的卡**，且"谁在台上谁刷"（UnitEditorScreen._refresh_pages，
    见 AGENTS §0.7.1「别替看不见的界面干活」）—— 所以这里显式切回关系树那一页，
    让卡在台上、改一格它才照得到。**不是绕过 bug**：切页正是用户要看那张卡时做的事。
    """
    window._open_unit_editor(unit_id)
    window._unit_editor.show_main()
    page = window._unit_editor.main_page
    return page, page.card


def _option_row(instance, option_id: int):
    """Options 的原始行（用例自己算期望值时用，不调被测的那几个函数）。"""
    table = instance.table("Options")
    index = table.column_index("Id")
    return next(row for row in table.rows if row[index] == option_id)


def _is_default(instance, option_id: int) -> bool:
    """这一项在**数据里**是不是"这个槽选中的那一项"（Options.IsDefault）。"""
    table = instance.table("Options")
    return _option_row(instance, option_id)[table.column_index("IsDefault")] is True


def _highlight(card) -> set[str]:
    """卡上改装槽里带 selected=true 的那几行的 tooltip 首段（`Options #Id`）。

    ❗ 读的是**控件里那份状态**，不是模型现算出来的 selected —— 高亮有没有动，
    只有控件知道（上一轮的教训：断言写在现算值上，用例绿着、真机还是坏的）。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import CardNavRow

    return {
        widget.toolTip().split(" · ")[0]
        for widget in card.findChildren(CardNavRow)
        if widget.property("role") == "option-row" and widget.property("selected") == "true"
    }


def _group(card, modification_id: int):
    group = next(g for g in card.card_options if g.modification_id == modification_id)
    assert group.selected is not None, f"槽 {modification_id} 没有选中项"
    return group


def _click(qapp, widget) -> None:
    """真发一对鼠标事件（不是直接 emit 信号）—— 验的是"点得动"，不是"接了线"。"""
    point = QPointF(widget.rect().center())
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        qapp.sendEvent(
            widget,
            QMouseEvent(
                kind,
                point,
                point,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )


def _in_zoom_bar(widget, card) -> bool:
    """这颗控件是不是「卡片大小」那一排（role=card-zoom）里的？

    同一份判据在 test_ui_redesign.py / test_relation_tree.py / test_card_edit.py 里各有一份
    （copy，不是共享；改一处要同时改其它几处）。那是**显示**开关，不算可点编辑入口。
    """
    parent = widget.parentWidget()
    while parent is not None and parent is not card:
        if parent.property("role") == "card-zoom":
            return True
        parent = parent.parentWidget()
    return False


# --------------------------------------------------------------------------- #
# ③ 21 个炮塔挂载槽：一个都不折叠
# --------------------------------------------------------------------------- #


def test_every_option_lists_all_21_mount_slots(instance) -> None:
    """⭐ 用户明确要求：**21 个炮塔挂载槽全部列出来，一个都不折叠**。

    期望值在这里**自己算一遍**（直接读 Options.Turret0Id..Turret20Id，不调被测的函数）：
    非零 = 这个槽装着哪座炮塔，零 = 空槽 —— 空槽也**必须**有自己的那一格。
    """
    from ba_mod_maker.app.viewmodel.unit_card import TURRET_SLOTS, CardData, build_unit_card

    options = instance.table("Options")
    slot_indexes = [options.column_index(name) for name in TURRET_SLOTS]
    assert len(slot_indexes) == SLOT_COUNT, "21 个槽的列名只有一份出处（core/edit/refs.py）"

    card = build_unit_card(CardData(instance), F15E)
    assert len(card.card_options) == 5, "F-15E 有 5 个改装槽（设计稿 block10）"

    checked = 0
    for group in card.card_options:
        assert len(group.slots) == SLOT_COUNT, f"{group.label} 的槽位格子不是 21 个"
        assert [cell.slot for cell in group.slots] == list(range(SLOT_COUNT))
        row = _option_row(instance, group.selected.option_id)
        want = [(slot, row[index]) for slot, index in enumerate(slot_indexes)]
        got = [(cell.slot, cell.turret_id) for cell in group.slots]
        assert got == want, (group.label, group.selected.option_id)
        checked += 1
    assert checked == 5, f"只扫到 {checked} 个槽 —— 用例没真的跑起来"


def test_the_12_slot_option_shows_12_used_and_9_empty(instance) -> None:
    """设计稿 block10 那个样本：机腹挂架那条选项占 **12** 个槽 —— 12 个亮着、
    其余 **9** 个空槽也照样画出来（那张图的注解就是"其余 9 个是空槽"）。"""
    from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card

    card = build_unit_card(CardData(instance), F15E)
    group = _group(card, F15_FUSELAGE)
    assert len(group.slots) == SLOT_COUNT
    assert [cell.slot for cell in group.occupied_slots] == list(range(7, 19)), "占了 7..18"
    empty = [cell.slot for cell in group.slots if not cell.occupied]
    assert empty == [0, 1, 2, 3, 4, 5, 6, 19, 20]
    assert all(cell.label for cell in group.occupied_slots), "亮着的格子要有炮塔短名"
    assert all(not cell.label for cell in group.slots if not cell.occupied), "空槽没有名字"
    # 短名砍掉开头的 xxxTurret 那一段（PylonTurret CBU87 x1 -> CBU87 x1、Turret Empty -> Empty）
    assert {cell.label for cell in group.occupied_slots} == {"Empty", "CBU87 x1"}
    assert [cell.label for cell in group.slots[7:19]] == ["Empty"] * 6 + ["CBU87 x1"] * 6
    assert group.slots[7].text == "7 Empty" and group.slots[0].text == "0 空"


def test_only_the_selected_option_carries_the_slots(instance) -> None:
    """21 个格子画在**选中那一项**下面（设计稿 block10），而且那一项与数据一致。

    "选中"的口径是 slot_selected_option（第一条 IsDefault ?? 第一条 Order==0）——
    与关系树上那个 ● 同一条，卡不另搞一套。
    """
    from ba_mod_maker.app.viewmodel.unit_card import (
        CardData,
        build_unit_card,
        slot_selected_option,
    )

    data = CardData(instance)
    card = build_unit_card(data, STRYKER)
    for group in card.card_options:
        picked = [row for row in group.options if row.selected]
        assert len(picked) == 1, f"{group.label} 里选中的不是恰好一项：{picked}"
        assert picked[0].option_id == slot_selected_option(data, group.modification_id)
        assert len(group.slots) == SLOT_COUNT
    # 装甲组件那一条选项不占炮塔槽 —— 格子照样 21 个（只是全是空槽），
    # 而"画不画那一排"由界面决定（见 UI 那几个用例）。
    armor = _group(card, MOD_ARMOR)
    assert armor.occupied_slots == ()
    assert [cell.slot for cell in armor.slots] == list(range(SLOT_COUNT))


# --------------------------------------------------------------------------- #
# ② 改装槽 › 选项（互斥）：费用 + 它改了什么
# --------------------------------------------------------------------------- #


def test_the_option_says_its_cost_and_what_it_changed(instance) -> None:
    """右边标**费用**与"它改了什么"（设计稿 block9：`+5 隐蔽 0.666` 那一行）。

    期望值自己从原始行取（Turret0Id / ArmorId / MobilityId / ConcatenateWithUnitName /
    StealthOverride），不抄实现里的常量表。
    """
    from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card

    options = instance.table("Options")
    card = build_unit_card(CardData(instance), STRYKER)

    weapon_slot = _group(card, MOD_WEAPONS)
    picked = weapon_slot.selected
    assert picked.option_id == OPT_M240 and picked.cost_text == "±0"

    up = next(row for row in weapon_slot.options if row.option_id == OPT_BUSHMASTER2)
    base = _option_row(instance, OPT_BUSHMASTER2)
    cost = base[options.column_index("Cost")]
    assert up.cost_text == f"+{cost}" and cost == 25
    turrets = [effect for effect in up.effects if effect.table == "Turrets"]
    assert len(turrets) == 1, up.effects
    assert turrets[0].row_id == base[options.column_index("Turret0Id")] == 892
    assert turrets[0].text.startswith("槽 0 · ")
    assert any("名字后缀" in effect.text for effect in up.effects), up.effects
    assert any(effect.text == "隐蔽 1" for effect in up.effects), up.effects
    # 可点：炮塔那条效果带着 (Turrets, 行 Id)
    assert turrets[0].ref is not None and turrets[0].ref.table == "Turrets"

    armor_slot = _group(card, MOD_ARMOR)
    aps = next(row for row in armor_slot.options if row.option_id == OPT_SRAT_APS)
    row = _option_row(instance, OPT_SRAT_APS)
    assert aps.cost_text == "+55"
    reached = {(effect.table, effect.row_id) for effect in aps.effects}
    assert ("Armors", row[options.column_index("ArmorId")]) in reached
    assert ("Mobility", row[options.column_index("MobilityId")]) in reached
    assert any("Trophy" in effect.text for effect in aps.effects), aps.effects


# --------------------------------------------------------------------------- #
# ① 单位 › 炮塔 › 武器 › 弹种（四层）
# --------------------------------------------------------------------------- #


def test_the_chain_is_unit_turret_weapon_ammo(instance) -> None:
    """四层链的**数据**：当前装着的那几座炮塔 › 它的武器 › 本单位给这把枪挂的弹。

    炮塔来自 card_turrets（TurretUnits 的缺省 + 选项覆盖），**去重保序**；
    弹种那一层的 count 与卡片上那个数同口径（Quantity × HUDMultiplier）。
    """
    from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card

    card = build_unit_card(CardData(instance), STRYKER)
    assert [turret.turret_id for turret in card.turrets] == [522]
    turret = card.turrets[0]
    assert turret.label == "M240 塔", "短名 + 一个「塔」（设计图：M240 塔）"
    assert turret.slots == (0,) and turret.slot_text == "0" and turret.note == "槽 0 · 1 件武器"

    weapon = turret.weapons[0]
    assert weapon.weapon_id == 27 and weapon.label == "M240"
    ammo = weapon.ammo[0]
    ammunitions = instance.table("Ammunitions")
    template = next(
        row for row in ammunitions.rows if row[ammunitions.column_index("Id")] == ammo.ammo_id
    )
    multiplier = template[ammunitions.column_index("HUDMultiplier")]
    assert ammo.count == ammo.quantity * max(1, multiplier) == 2000
    assert ammo.link.table == "WeaponAmmunitions" and ammo.link.column == "AmmunitionId"
    assert ammo.ref.row_id == ammo.row_id

    # 一门枪两种弹：一行一种（BTR-82A 的 2А72）
    btr = build_unit_card(CardData(instance), BTR82A)
    assert [turret.turret_id for turret in btr.turrets] == [2]
    assert [weapon.weapon_id for weapon in btr.turrets[0].weapons] == [2, 7]
    assert len(btr.turrets[0].weapons[0].ammo) == 2
    # 步兵：没有炮塔那条链（武器挂在班组成员上）
    infantry = build_unit_card(CardData(instance), INFANTRY)
    assert infantry.turrets == () and infantry.card_options


def test_the_bench_sees_the_whole_turret_roster_not_just_the_current_loadout(instance) -> None:
    """⭐⭐ 用户 2026-10-02：「**还是把所有的炮塔都列出来吧，我毕竟要增删炮塔的**」。

    卡上那条链画的是**当前装着的**（`card_turrets`：IsDefault 过滤 + 当前选项的槽覆盖）——
    单位 21 只剩 1 座；编成台 ① 要画**本单位可能装的全部炮塔**（名册），否则另外 4 座在界面上
    根本够不着，也就谈不上增删。
    """
    from ba_mod_maker.app.viewmodel.unit_card import (
        CardData,
        card_turret_chain,
        roster_turret_chain,
    )

    data = CardData(instance)
    # 卡那一面**一个字节都不许变**（照游戏 ApplyCurrentMods 之后的样子画）
    card = card_turret_chain(data, STRYKER)
    assert [turret.turret_id for turret in card] == [522], "卡上那条链还是「现在装着的那一座」"
    assert card[0].slot_text == "0"

    roster = roster_turret_chain(data, STRYKER)
    ids = [turret.turret_id for turret in roster]
    assert ids == [18, 55, 522, 892, 893], f"本单位 5 座炮塔一座都不能少：{ids}"
    # 槽号只标**当前选项选中的**那一座；名册里其余四座槽号是空的（没被选项点名）
    by_id = {turret.turret_id: turret for turret in roster}
    assert by_id[522].slot_text == "0"
    assert [turret.turret_id for turret in roster if turret.slot_text] == [522]
    # 每座都带着自己的武器链 —— 要增删就得先看得见里面有什么
    assert all(turret.weapons for turret in roster)


def test_the_turret_block_title_follows_the_new_scope() -> None:
    """抬头那句必须跟着范围走 —— 还写"当前改装下"就是在说谎（用户 2026-10-02）。"""
    from ba_mod_maker.app.widgets.bench_turrets import TITLE

    assert TITLE == "① 本单位的炮塔 → 武器 → 弹药"
    assert "当前改装下" not in TITLE


# --------------------------------------------------------------------------- #
# 控件：卡上真的画出来了、点得动
# --------------------------------------------------------------------------- #


def test_the_card_draws_all_the_slot_cells(window) -> None:
    """⭐ 21 个格子**在控件里**真的画出来了：占了槽的选项各一排，每排恰好 21 格。

    判据不看颜色（颜色只从 tokens 来，见 test_theme）：看的是"格数 + 槽号 + 亮/暗"——
    亮着的是有炮塔的格（role=ability-chip），暗着的是空槽（role=card-note）。
    """
    _page, card = _open(window, F15E)
    model = card.card.card_options
    grids = [group for group in model if group.occupied_slots]
    assert grids, "样本单位应当有占了挂载槽的选项"

    chips = card.slot_chips
    assert len(chips) == SLOT_COUNT * len(grids), (len(chips), len(grids))
    cells = [cell for group in grids for cell in group.slots]
    assert len(cells) == len(chips)
    for index in range(0, len(chips), SLOT_COUNT):
        numbers = [chip.text().split(" ")[0] for chip in chips[index : index + SLOT_COUNT]]
        assert numbers == [str(slot) for slot in range(SLOT_COUNT)], numbers
    lit = [chip for chip in chips if chip.property("role") == "ability-chip"]
    assert len(lit) == sum(len(group.occupied_slots) for group in grids)
    # 亮着的格子写着炮塔短名，tooltip 指得到那一行
    for chip, cell in zip(chips, cells, strict=True):
        if cell.occupied:
            assert chip.text().startswith(f"{cell.slot} ")
            assert f"Turrets #{cell.turret_id}" in chip.toolTip()
        else:
            # ❗ 空槽**只写槽号**（2026-10-02）：20 个「N 空」连起来是一条灰字墙 ——
            #    用户圈着那一条问「这里是什么东西？改掉」；21 格照样一格不少。
            assert chip.text() == str(cell.slot)
    # ❗ 卡上**不许**再有那几段"使用说明"散文（写给开发者的话，用户看不懂也不该看）
    texts = [label.text() for label in card.findChildren(QLabel)]
    for prose in ("21 个炮塔挂载槽全列出来", "点某一项 = 选中它", "一个字节都不写"):
        assert not any(prose in text for text in texts), (prose, texts[-6:])


def test_clicking_a_lit_cell_opens_that_turret(window) -> None:
    """点一个亮着的格子 -> 中栏换成那座炮塔；点空槽 -> 换成这条选项（要占这个槽，
    就在它的 TurretNId 那一格上填炮塔 Id）。"""
    from ba_mod_maker.app.screens.editor.entries_panel import table_title
    from ba_mod_maker.core.edit.scope import RowRef

    page, card = _open(window, F15E)
    model = card.card.card_options
    cells = [cell for group in model if group.occupied_slots for cell in group.slots]
    pairs = list(zip(card.slot_chips, cells, strict=True))

    chip, cell = next(pair for pair in pairs if pair[1].occupied)
    assert cell.turret_id > 0
    chip.activated.emit()
    assert page.focus == RowRef("Turrets", cell.turret_id), (page.focus, cell)
    assert page.entries.subtitle_text.startswith(
        f"{table_title('Turrets')} Turrets#{cell.turret_id} ·"
    )

    empty_chip, empty_cell = next(pair for pair in pairs if not pair[1].occupied)
    assert empty_cell.turret_id == 0
    empty_chip.activated.emit()
    assert page.focus is not None and page.focus.table == "Options"
    assert page.focus.row_id in {group.selected.option_id for group in model}


def test_all_four_levels_swap_the_middle_column(window, qapp) -> None:
    """⭐ 四层都能点：单位 › 炮塔 › 武器 › 弹种 —— 点了中栏换成**那一行的全部列**。

    第一层（炮塔）用**真鼠标事件**打，验"点得动"；其余几层走同一条 activated 线。
    中栏列数 = 那张表的列数（一条不少），副标题写着 `表名 表#Id · N 列`。
    """
    from ba_mod_maker.app.screens.editor.entries_panel import table_title

    page, card = _open(window, STRYKER)
    labels = card.nav_labels
    unit_row = next(w for w in labels if w.text().startswith("🚙 "))
    turret_row = next(w for w in labels if w.text().startswith("🗼 "))
    weapon_row = next(w for w in labels if w.text().startswith("🔫 "))
    ammo_row = next(w for w in labels if w.text().startswith("💥 "))

    def check(widget, table: str, row_id: int) -> None:
        widget.activated.emit()
        focus = page.focus
        assert focus is not None and (focus.table, focus.row_id) == (table, row_id), (
            widget.text(),
            focus,
        )
        columns = len(window._instance.table(table).columns)
        assert len(page.entries.rows) == columns, (table, len(page.entries.rows), columns)
        assert page.entries.subtitle_text.startswith(
            f"{table_title(table)} {table}#{row_id} · {columns} 列"
        )

    turret = card.card.turrets[0]
    weapon = turret.weapons[0]
    ammo = weapon.ammo[0]

    _click(qapp, turret_row)
    assert page.focus is not None and page.focus.table == "Turrets"
    assert page.focus.row_id == turret.turret_id
    check(weapon_row, "Weapons", weapon.weapon_id)
    check(ammo_row, "Ammunitions", ammo.ammo_id)
    # 第一层：回本单位那 31 条（与树上点根节点同一条路，不跳页）
    unit_row.activated.emit()
    assert page.focus is not None and (page.focus.table, page.focus.row_id) == ("Units", STRYKER)
    assert len(page.entries.rows) == 31
    assert window._unit_editor.current_unit_id == STRYKER, "点单位那一层不该跳走"


def test_the_selected_option_is_highlighted_on_the_card(window) -> None:
    """⭐ 槽内互斥：**每个槽恰好一项高亮**（role=option-row + selected=true），
    右边标着费用与"它改了什么"。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import CardNavRow

    page, card = _open(window, STRYKER)
    model = card.card.card_options
    rows = [
        widget
        for widget in card.findChildren(CardNavRow)
        if widget.property("role") == "option-row"
    ]
    assert len(rows) == sum(len(group.options) for group in model) == 8
    selected = [widget for widget in rows if widget.property("selected") == "true"]
    assert len(selected) == 2, "两个槽各恰好一项高亮"
    for widget in rows:
        texts = [label.text() for label in widget.findChildren(QLabel)]
        mark = "◉" if widget.property("selected") == "true" else "○"
        assert texts and texts[0].startswith(mark), texts
    for widget in selected:
        texts = [label.text() for label in widget.findChildren(QLabel)]
        assert any(text.startswith("+") or text == "±0" for text in texts), texts
        assert widget.toolTip().startswith("Options #")
    # 高亮的那两项就是数据里选中那两项
    picked = {group.selected.option_id for group in model}
    marked = {
        option_id
        for option_id in picked
        if any(f"Options #{option_id}" in widget.toolTip() for widget in selected)
    }
    assert marked == picked, (marked, picked)
    assert page.entries.rows, "中栏还该有东西"


def test_clicking_an_option_row_on_the_card_selects_it(window, qapp) -> None:
    """⭐ 2026-10-01 实机反馈（用户原话：「右边改装槽，我选中的改装槽，它不会高亮」）：
    **点卡上那一行 = 选中它** —— 高亮当场跟着走，写的是 `Options.IsDefault`（槽内互斥）。

    真凶不是渲染（那条 qss 一直是好的：实测选中行底色 #2b3242、其它行 #1d222b），
    而是"选中"只挂在左树的**右键菜单**里 —— 卡上点一行只是"去看"，用户点了以为
    选中了、高亮却一点没动。

    钉的是**控件里那份状态**（option-row[selected=true] 落在谁身上）**与**数据那一格：
    只断言模型现算出来的 selected，会变成"用例绿着、真机还是坏的"（上一轮的教训）。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import CardNavRow

    page, card = _open(window, STRYKER)
    instance = window._instance

    def row_of(option_id: int):
        return next(
            widget
            for widget in card.findChildren(CardNavRow)
            if widget.property("role") == "option-row"
            and widget.toolTip().startswith(f"Options #{option_id} ·")
        )

    # ❗ 起点只按**控件里那份状态**判：这一槽在原版数据里 Options.IsDefault **两条都是
    #    false**，选中是靠"第一条 Order==0"兜底的（口径在 unit_card.slot_selected_option）
    #    —— 拿 IsDefault 当起点会冤枉代码。
    assert f"Options #{OPT_M240}" in _highlight(card), "起点：M240 那一项是选中的"
    assert f"Options #{OPT_BUSHMASTER2}" not in _highlight(card)

    _click(qapp, row_of(OPT_BUSHMASTER2))  # 点整行 = 选中它（不是直接 emit 信号）
    settle()

    assert _is_default(instance, OPT_BUSHMASTER2) is True, "点整行没写 Options.IsDefault"
    assert _is_default(instance, OPT_M240) is False, "槽内互斥：旧的那条没取消"
    assert f"Options #{OPT_BUSHMASTER2}" in _highlight(card), "高亮没跟着走"
    assert f"Options #{OPT_M240}" not in _highlight(card)


def test_a_borrowed_slots_row_on_the_card_opens_the_unit_it_stands_for(window, qapp) -> None:
    """借来的槽（645 借 108 的 743）在卡上**点它也不写** —— 落到**对的那一层**。

    "点改装槽那一行 = 选中它"这条口径**只对本单位自己的槽**成立：借来的槽写下去
    会一起改到槽的主人（口径在 options_page.select_slot_option）。
    而借来的槽里每一项都是「换成**另一个**单位」（`Options.ReplaceUnitId`：
    645 来自 108 的 3129、3128 换成 640、3301 换回 108）—— 所以点它 =
    **整屏打开那个单位**，与军械库那张卡的下拉同一条路（`variant_requested`）。

    ❗ 从前这里是"只把中栏换成那一行"，卡上一个像素都不动 —— 用户看到的就是
    "点了不亮"（probe_card_highlight_many 里 645 那 3 次点不动的就是它）。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import CardNavRow

    page, card = _open(window, INFANTRY)
    instance = window._instance
    options = instance.table("Options")
    col = options.column_index("IsDefault")
    group = next(g for g in card.card.card_options if not g.is_own_slot)
    target = next(row for row in group.options if not row.selected)
    assert target.replaces_unit_id not in (0, INFANTRY), "样本变了：这一项得是'换成别的单位'"
    ids = {row.option_id for row in group.options}
    before = {row[0]: row[col] for row in options.rows if row[0] in ids}

    row = next(
        widget
        for widget in card.findChildren(CardNavRow)
        if widget.property("role") == "option-row"
        and widget.toolTip().startswith(f"Options #{target.option_id} ·")
    )
    _click(qapp, row)
    settle()

    after = {row[0]: row[col] for row in options.rows if row[0] in ids}
    assert after == before, "借来的槽在卡上点了也不许写"
    assert window._unit_editor.current_unit_id == target.replaces_unit_id, (
        "点它该整屏打开那一项代表的单位"
    )

    """行尾那颗 `↗` 照旧只是"去看这一行的全部列"，而且**吃掉**点击 ——
    不吞的话父行会跟着再发一次 activated，一次点击变成"既选中又去看"。"""
    from ba_mod_maker.app.screens.armory.detail_panel import CardNavLabel, CardNavRow

    page, card = _open(window, STRYKER)
    row = next(
        widget
        for widget in card.findChildren(CardNavRow)
        if widget.property("role") == "option-row"
        and widget.toolTip().startswith(f"Options #{OPT_BUSHMASTER2} ·")
    )
    arrow = next(label for label in row.findChildren(CardNavLabel) if label.text() == "↗")

    _click(qapp, arrow)
    settle()

    assert page.focus is not None
    assert (page.focus.table, page.focus.row_id) == ("Options", OPT_BUSHMASTER2), "↗ 没去看那一行"
    # 只看不改：高亮还在 M240 那一项上（同一条：起点按控件状态判，不按 IsDefault）
    assert f"Options #{OPT_M240}" in _highlight(card), "↗ 不该改选中"
    assert f"Options #{OPT_BUSHMASTER2}" not in _highlight(card)

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(arrow.rect().center()),
        QPointF(arrow.rect().center()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()
    arrow.mouseReleaseEvent(event)
    assert event.isAccepted(), "没吞 —— 父行会跟着再发一次 activated"


def test_an_option_rows_name_swallows_the_click_so_one_click_is_one_action(window) -> None:
    """改装槽那一行的**名字**也要吞掉这一下 —— 一次真鼠标点击只能发一次动作。

    ❗ 离线用例发事件是**直接发给整行**的，所以"发两次"在这儿看不见；真机上点击落在
    **名字**（CardNavLabel）上，名字不吞就会冒泡给宿主那一行 —— 一次点击发两次
    `option_select_requested`：第一次写进去（状态栏「已选中「X」（+10）」），
    第二次 writes 已经是空的，状态栏于是被**后一句**盖掉：
    「X」已经是这个槽选中的那一项 —— 用户刚点完就看到这句，与事实相反。

    实测来源：真机 CU `route=global_input` 点这一行，状态栏读到的是后一句。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import CardNavLabel, CardNavRow

    _page, card = _open(window, STRYKER)
    seen: list[tuple[int, int]] = []
    card.option_select_requested.connect(lambda m, o: seen.append((m, o)))
    row = next(
        widget
        for widget in card.findChildren(CardNavRow)
        if widget.property("role") == "option-row"
        and widget.toolTip().startswith(f"Options #{OPT_BUSHMASTER2} ·")
    )
    name = next(label for label in row.findChildren(CardNavLabel) if "Bushmaster" in label.text())

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(name.rect().center()),
        QPointF(name.rect().center()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()
    name.mouseReleaseEvent(event)

    assert event.isAccepted(), "名字没吞 —— 父行会跟着再发一次，状态栏会显示后一句"
    assert seen == [(MOD_WEAPONS, OPT_BUSHMASTER2)], f"一次点击发了 {len(seen)} 次"
    _highlight(card)  # 只是确认这张卡还在（下面那条用例接着用 STRYKER）


def test_switching_an_option_moves_the_chain_and_the_armor(window) -> None:
    """⭐ 块 5 那条：**换一个选项 = 下面几块跟着变**（卡上那几块永远是当前组合的结果）。

    换一项的入口在**树上**（卡只读、不给选）：tree.select_option -> 写 Options.IsDefault，
    槽内互斥。这里验卡上四层链的炮塔与装甲那一行都跟着换过去了。
    """
    page, card = _open(window, STRYKER)
    assert card.card.turrets[0].turret_id == 522, "样本前提：缺省是 M240 塔"
    assert card.card.armor is not None and card.card.armor.row_id == 70
    assert card.card.armor.source.startswith("来自「"), "被选项覆盖的那一块要注明来自哪个选项"

    node = next(
        item
        for item in page.tree.tree.flatten()
        if item.table == "Options" and item.row_id == OPT_BUSHMASTER2
    )
    assert page.tree.select_option(node.key), "树上没有「选中这一项」这条线"
    settle()
    assert card.card.turrets[0].turret_id == 892, "换了炮塔那一项，卡上那条链没跟着走"
    assert card.card.weapons, "武器那一块也不该空"

    node = next(
        item
        for item in page.tree.tree.flatten()
        if item.table == "Options" and item.row_id == OPT_SRAT_APS
    )
    assert page.tree.select_option(node.key)
    settle()
    assert card.card.armor is not None and card.card.armor.row_id == 301, "装甲没跟着换"
    assert "Trophy" in card.card.armor.source, card.card.armor.source
    assert card.card.mobility is not None and card.card.mobility.row_id == 238


def test_the_new_blocks_still_add_no_editing_entry_point(window) -> None:
    """卡上那三块**一个可点编辑入口都没有**：没有 QPushButton（显示开关除外）、
    没有 QComboBox ——"点一层换中栏"是导航，写数据仍然只在树上与中栏。
    """
    _page, card = _open(window, F15E)
    assert card.slot_chips, "样本单位应当有挂载槽格子"
    scattered = [
        button.text() for button in card.findChildren(QPushButton) if not _in_zoom_bar(button, card)
    ]
    assert scattered == [], f"只读卡上冒出了「卡片大小」以外的按钮：{scattered}"
    assert card.findChildren(QComboBox) == []
    for name in ("cell_activated", "row_activated", "swap_requested", "clone_requested"):
        assert not hasattr(card, name), name


def test_the_card_says_where_the_buttons_live(window) -> None:
    """卡上写清"＋ / ✕ 在哪儿"（卡片只读，卡上不给按钮）—— 这一句要看得见。

    ❗ 2026-10-02 翻新：原来还断言「选中这一项」那句**使用说明**也在 ——
    用户圈着它（连同那 20 个「N 空」）说「这里是什么东西？改掉」，那一整段已删。
    所以这里改成：**"按钮在哪"那一句留着** ＋ **删掉的那段不许回来**（不是把用例删了）。
    """
    _page, card = _open(window, STRYKER)
    texts = [label.text() for label in card.findChildren(QLabel)]
    assert any("关系树" in text and "按钮" in text for text in texts), texts
    for prose in ("点某一项 = 选中它", "一个字节都不写", "与军械库的下拉同一条路"):
        assert not any(prose in text for text in texts), (prose, texts[-8:])


def test_the_card_only_writes_the_slot_selection_when_clicked(window) -> None:
    """点遍卡上所有可点的地方：**只有"选中改装槽这一项"这一件事写库**，别的照旧不动。

    ❗ 这条守卫原来叫 `..._writes_nothing_when_clicked`，口径是"卡上点哪都不写"。
    2026-10-01 用户实机反馈把它改窄了一格（原话：「右边改装槽，我选中的改装槽，
    它不会高亮」）：改装槽那几行点了**就是选中它**（写 `Options.IsDefault`）。
    于是判据从"一列都不许变"改成"**只有 IsDefault 这一列能变**，且槽内互斥" ——
    其它块（炮塔四层 / 装甲 / 机动 …）仍然是一个字节都不许动。
    """
    page, card = _open(window, STRYKER)
    options = window._instance.table("Options")
    before = {row[0]: list(row) for row in options.rows}
    assert card.nav_labels, "卡上应当有可点的行"
    for widget in card.nav_labels:
        widget.activated.emit()

    touched: dict[int, list[str]] = {}
    for row in options.rows:
        fresh = list(row)
        if fresh != before[row[0]]:
            touched[row[0]] = [
                options.columns[i]
                for i, (a, b) in enumerate(zip(before[row[0]], fresh, strict=True))
                if a != b
            ]
    assert touched, "点遍了卡上的行，改装槽的选中总该动过（这条守卫的正面样本）"
    for row_id, columns in touched.items():
        assert columns == ["IsDefault"], f"卡上点一下改了不该改的列：Options #{row_id} {columns}"
    # 槽内互斥：**这一轮点到的那些槽**各自恰好一条 IsDefault（点了一圈回来不会两条都 true）。
    # ❗ 只圈点到的槽：原版数据里别的槽（例：Mi35M 的挂架上 #255）本来就有两条 true ——
    #    那是游戏自带的数据，不是我们写的，"全库每槽至多一条"这条并不成立。
    default_col = options.column_index("IsDefault")
    mod_col = options.column_index("ModificationId")
    options_by_id = {row[0]: row for row in options.rows}
    for modification_id in {options_by_id[row_id][mod_col] for row_id in touched}:
        picked = [
            row
            for row in options.rows
            if row[mod_col] == modification_id and row[default_col] is True
        ]
        assert len(picked) == 1, (
            f"槽 #{modification_id} 有 {len(picked)} 条 IsDefault（该恰好一条）"
        )
    assert page.entries.has_editor is False
