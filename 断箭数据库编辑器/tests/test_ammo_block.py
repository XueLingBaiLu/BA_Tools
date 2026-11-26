"""中栏「💣 弹药」块：能调武器使用的弹药、拥有多少弹药。

用户口径（2026-09-30 实机反馈，原话）：

    我需要能调武器使用的弹药，拥有多少弹药

数据落点：`WeaponAmmunitions`（一行 = 本单位的枪 × 一种弹，带 `Quantity`）。
这一块挂在**中栏词条下面**（`RowEntriesPanel.set_cells(blocks=...)`），不是
Weapons 那一行的列 —— 所以这里同时守住两条：①块在列下面；②"40 列"那个计数不被带歪。

用真主窗口 + 真数据（与 test_unit_editor.py 同一策略）：
这一层要验的正是"控件和数据接对了没有"，假控件只能验"我以为的接线"。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root
from conftest import settle

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 用户截图上的那台车：Id 2 = M1A2 SEP v2 TUA，挂着 M256 120mm（Weapons#266）。
TUA = 2
M256 = 266
#: 步兵单位（Id 645）：AK-74RMO（Weapons#122）挂 5.45x39mm（Ammunitions#361）80 发。
#: 步兵的枪是挂在**班组成员**上的，但携弹行照样带 UnitId —— 同一条链，别只验载具。
INFANTRY = 645
AK74 = 122

#: 它给这门炮挂的两种弹（实测：M830A1 HEAT 24 发 / M829A3 APFSDS 16 发）。
HEAT = 251
APFSDS = 254
AMMO_TABLE = "WeaponAmmunitions"


# 夹具（qapp / instance / window）统一在 tests/conftest.py：
#   window = **一个模块一个**主窗口，每个用例只把一份干净数据接回去
#   （为什么、怎么保证隔离，见 conftest 的模块 docstring）。


def _open(window, unit_id: int):
    """进编辑器，返回 (主编辑页,)。中栏一开始是 Units 那一行。

    ❗ 2026-10-01：双击单位现在**默认落在编成台**（步兵形态，用户口径）。
    这一份测的是三栏那一页的中栏（弹药块），所以进来之后显式切回去 ——
    不切的话"用户路径改了"会被读成"弹药块坏了"。
    """
    window._open_unit_editor(unit_id)
    window._unit_editor.show_main()
    return window._unit_editor.main_page


def _blocks(page):
    """中栏现在挂着的弹药块（用 findChildren 找，不碰面板的私有字段）。"""
    from ba_mod_maker.app.screens.editor.ammo_block import AmmoBlock

    return tuple(page.findChildren(AmmoBlock))


def _block_for(window, unit_id: int, weapon_id: int):
    """打开某把枪那一行，返回 (页面, 弹药块 或 None)。"""
    from ba_mod_maker.core.edit.scope import RowRef

    page = _open(window, unit_id)
    page.set_focus(RowRef("Weapons", weapon_id))
    blocks = _blocks(page)
    return page, (blocks[0] if blocks else None)


def _fresh_ammo_id(page) -> int:
    """一种这门炮还没挂过的弹（Ammunitions 全表里随便挑一个）。"""
    table = page.context.instance.table("Ammunitions")
    index = table.column_index("Id")
    return next(row[index] for row in table.rows if row[index] not in (HEAT, APFSDS))


# --------------------------------------------------------------------------- #
# 看得见：这把枪用哪些弹、各多少发
# --------------------------------------------------------------------------- #


def test_the_block_lists_every_ammo_the_weapon_uses(window) -> None:
    """⭐ M256 120mm 挂着两种弹：M830A1 HEAT ×24、M829A3 APFSDS ×16。"""
    page, block = _block_for(window, TUA, M256)
    assert block is not None
    assert block.title_text == "💣 弹药（2 种 · 本数 40 发）"
    assert [(line.name_text, line.quantity_text) for line in block.lines] == [
        ("M830A1 HEAT", "×24"),
        ("M829A3 APFSDS", "×16"),
    ]
    assert all(line.can_edit_quantity for line in block.lines), "数量要点得动才叫'拥有多少弹药'"
    # 这两种弹的 HUDMultiplier 都是 1、这门炮也只装 1 门 —— 卡片上就是这两个数，
    # 一样还写一遍是噪音，所以「卡上 N」不该出现（不一样时才出现，见下一条）。
    assert [line.hud_text for line in block.lines] == ["", ""]


def test_hovering_an_ammo_line_highlights_that_whole_line_only(window) -> None:
    """⭐ 用户原话（2026-09-29）：「中间弹药栏的高亮还是老一套」。

    弹药行的悬停必须与中栏词条行**同一套**：整行一条高亮（复用 role=entry-line
    那条 QSS）、同一时刻只有一行；而且画源是**属性**，不是 Qt 的 WA_UnderMouse
    —— 所以旗标残留也画不出第二个高亮。
    """
    from PySide6.QtCore import Qt

    _page, block = _block_for(window, TUA, M256)
    first, second = block.lines[0], block.lines[1]
    assert first.property("role") == "entry-line", "整行高亮那条 QSS 是按这个 role 命中的"
    assert not first.is_hovered and not second.is_hovered

    first.hover_changed.emit(first, True)  # 等价于鼠标进了第一行
    assert first.is_hovered and block.hovered_line is first
    assert not second.is_hovered

    second.hover_changed.emit(second, True)  # 鼠标移到第二行
    assert second.is_hovered and block.hovered_line is second
    assert not first.is_hovered, "同一时刻只能亮一行"

    second.hover_changed.emit(second, False)  # 鼠标离开
    assert not second.is_hovered and block.hovered_line is None

    # Qt 的旗标就算残留在每一行上，也不该多亮一行。
    for line in block.lines:
        line.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    assert [line.is_hovered for line in block.lines] == [False, False]


def test_building_the_block_never_maps_a_stray_window(window, qapp) -> None:
    """建弹药块**不许**冒出顶层小窗 —— 回归闸（本轮真踩过一次）。

    形状只有一条（见 app/diag/log.py::StrayWindowGuard）：**控件自己还没有父控件的时候
    被显示**（setVisible / show）就会被 Qt 映射成一个真顶层窗，用户看到的是"闪一下的
    小弹窗"。本轮「卡上 N」那个 QLabel 就是先 setVisible、后 addWidget —— 被诊断日志
    里的"走火的顶层小窗"当场逮到。

    ❗ 必须真的 show()：控件**可见**时才会被映射；不可见的控件怎么折腾都不出窗，
    那样这条用例抓不到 bug。
    """
    from PySide6.QtWidgets import QApplication

    guard = getattr(QApplication.instance(), "_ba_stray_guard", None)
    assert guard is not None, "MainWindow 应当已经把产品自己的小弹窗守卫装上来"

    window.show()
    qapp.processEvents()
    before = len(guard.caught)
    _block_for(window, TUA, M256)
    _block_for(window, INFANTRY, AK74)  # 步兵那条（会画出「卡上 N」）
    qapp.processEvents()
    assert guard.caught[before:] == [], guard.caught[before:]


def test_an_infantry_weapon_gets_the_same_block(window) -> None:
    """步兵也走同一条链：枪挂在班组成员上，携弹行照样是 WeaponAmmunitions。"""
    _page, block = _block_for(window, INFANTRY, AK74)
    assert block is not None
    assert [(line.name_text, line.quantity_text) for line in block.lines] == [("5.45x39mm", "×80")]
    # ⚠️ 卡片上写的是 1200，不是 80：5 人 × 80 发 × HUDMultiplier(3)。
    # 这一条就是这仓里踩过三次的那个坑，别再让界面只写裸 Quantity。
    assert block.lines[0].hud_text == "卡上 1200"
    assert "卡片上 1200 发" in block.title_text


def test_the_block_sits_right_under_the_columns_not_inside_them(window) -> None:
    """块挂在词条**下面**：40 列还是 40 列，块不算列。"""
    from ba_mod_maker.app.widgets.row_entries import EntryList

    page, block = _block_for(window, TUA, M256)
    panel = page.entries
    listed = panel.findChild(EntryList)
    assert listed is not None and listed.blocks == (block,)
    order = [listed._layout.itemAt(i).widget() for i in range(listed._layout.count())]
    assert order.index(block) == order.index(listed.rows[-1]) + 1, "块要贴着最后一条词条"
    assert len(listed.rows) == 40
    assert panel.summary_text == "40 列"


def test_a_unit_row_has_no_ammo_block(window) -> None:
    """中栏看「单位」那一行时没有弹药块（它不是一把枪）。"""
    page = _open(window, TUA)
    assert page.focus is not None and page.focus.table == "Units"
    assert _blocks(page) == ()


def test_a_weapon_this_unit_does_not_carry_has_no_ammo_block(window) -> None:
    """翻到别的单位用的模板行时不给这一块 —— 画一块"这枪没弹"是噪音。"""
    from ba_mod_maker.core.edit.scope import RowRef

    page = _open(window, TUA)
    context = page.context
    carried = set(context.data.weapon_ids(TUA))
    weapons = context.instance.table("Weapons")
    index = weapons.column_index("Id")
    other = next(row[index] for row in weapons.rows if row[index] not in carried)
    page.set_focus(RowRef("Weapons", other))
    assert _blocks(page) == ()


# --------------------------------------------------------------------------- #
# 改得动：数量 / 换弹种 / 加一条 / 删一条
# --------------------------------------------------------------------------- #


def test_clicking_the_quantity_opens_the_inline_editor_and_writes_it(window) -> None:
    """⭐ 点数量就地改，改完立刻写进变更集，块跟着重画（不是只有词条行会刷）。"""
    page, block = _block_for(window, TUA, M256)
    line = block.lines[0]

    line.quantity_button.click()
    assert block.has_editor and block.open_row is line, "点了数量要开在【那一格】上"
    editor = line.editor
    assert editor is not None
    editor.committed.emit(99)  # 与用户按回车同一条路

    settle()  # 块要跟着重画（刷新异步，见 conftest.settle）
    assert page.context.changeset.value(AMMO_TABLE, line.row_id, "Quantity") == 99
    assert block.lines[0].quantity_text == "×99", "块要跟着重画（它不在词条行里，刷不到）"
    assert block.title_text == "💣 弹药（2 种 · 本数 115 发）"


def test_add_button_inserts_a_carrying_row_with_one_round(window) -> None:
    """「＋ 加一种弹」：就地冒候选（不弹对话框），选中就插一行，默认带 1 发。"""
    page, block = _block_for(window, TUA, M256)
    fresh = _fresh_ammo_id(page)

    block.add_button.click()
    assert block.picker is not None and block.picker_visible
    block.picker.picked.emit(fresh)

    settle()  # 刷新异步，见 conftest.settle
    entries = page.context.data.weapon_ammo(TUA, M256)
    assert len(entries) == 3
    assert (fresh, 1) in [(ammo_id, quantity) for ammo_id, quantity, _order in entries]
    assert len(block.lines) == 3


def test_remove_button_deletes_that_carrying_row(window) -> None:
    """「🗑 删一条」：删的是**本单位的携弹行**，不是 Ammunitions 模板行。"""
    page, block = _block_for(window, TUA, M256)
    before = len(page.context.instance.table("Ammunitions").rows)

    block.lines[0].remove_button.click()

    settle()  # 刷新异步，见 conftest.settle
    entries = page.context.data.weapon_ammo(TUA, M256)
    assert entries == ((APFSDS, 16, 0),)
    assert len(block.lines) == 1
    assert len(page.context.instance.table("Ammunitions").rows) == before, "模板行一行都不许动"


def test_ammo_line_has_no_swap_button(window) -> None:
    """⛔ 2026-10-19 用户命令：**删掉「换」的功能**（AGENTS §0.10 只有 新增/删除/修改）。

    这一条是**负控**：原来那颗「🔁 换弹种」必须真的不在了 ——
    改回任何一颗「换」按钮，`hasattr` 立刻变 True ⇒ 本用例红。
    改弹种的正路：删掉这一条携弹行，再「＋ 加一种弹」。
    """
    _page, block = _block_for(window, TUA, M256)
    line = block.lines[0]
    assert not hasattr(line, "swap_button"), "「换」被加回来了？"
    assert not hasattr(block, "swap_requested"), "「换」的信号又被挂回来了？"


def test_squad_member_row_gives_ammo_for_both_weapon_slots(window) -> None:
    """⭐ 用户口径（2026-10-01 实机，**纠正过我一次**）：

        「步兵班组里每个成员，主武器和副武器，以及它们各自的弹药都是可以独立设置的，
          SquadWeapons 只是给这个步兵班注册了这个武器而已」

    所以入口是**成员那一行**（`SquadMembers`）：主武器 / 副武器**各一块**弹药
    （`WeaponAmmunitions` 按 (UnitId, WeaponId) 存）；`SquadWeapons` 那一行
    **不给**块 —— 它只是注册表（我第一版把它当入口，是错的）。

    样本：645 的第 1 名 #1466 = 主武器 AK-74RMO(122) 80 发 + 副武器 GP-34(137) 24 发。
    口径补充（查库实测）：`WeaponAmmunitions` 没有成员列，**同枪的成员共用一行**。
    """
    from ba_mod_maker.core.edit.scope import RowRef

    page = _open(window, INFANTRY)

    page.set_focus(RowRef("SquadMembers", 1466))
    blocks = _blocks(page)
    assert len(blocks) == 2, "一个成员要给主武器与副武器各一块弹药"
    assert [block.weapon_id for block in blocks] == [AK74, 137]
    assert blocks[0].title_text.startswith("💣 主武器 "), blocks[0].title_text
    assert blocks[1].title_text.startswith("💣 副武器 "), blocks[1].title_text
    assert "本数 80 发" in blocks[0].title_text
    assert "本数 24 发" in blocks[1].title_text
    assert [line.quantity_text for line in blocks[0].lines] == ["×80"]
    assert [line.quantity_text for line in blocks[1].lines] == ["×24"]
    assert all(line.can_edit_quantity for block in blocks for line in block.lines), (
        "能改数量才叫打通"
    )

    # 副武器 = 0 的兵（#1467）：只给主武器那一块，不画空块。
    page.set_focus(RowRef("SquadMembers", 1467))
    only = _blocks(page)
    assert len(only) == 1 and only[0].weapon_id == AK74

    # SquadWeapons 只是注册表：那一行不给弹药块（弹药挂在成员的主/副武器上）。
    page.set_focus(RowRef("SquadWeapons", 1165))
    assert _blocks(page) == (), "SquadWeapons 是注册表，不是弹药入口"


# --------------------------------------------------------------------------- #
# ⭐ 改武器 -> 下面的弹药块**立刻跟上**（用户口径 2026-10-01）
#
# 用户原话：「我希望我在改武器的时候，它下面能立马显示出我武器的变化，
#            这样我就能直接去给武器添加弹药了。」
#
# 真凶（本轮先查清再改，三条路一条条走过）：
#   ① 刷新**到了** —— _commit -> context.set_value -> screen._on_changed
#      -> _schedule_refresh -> _refresh_pages -> MainEditorPage.refresh ✓
#   ② 「🔁 换一个」也**不是**没发通知：EditorContext.set_value 结尾就调 _on_changed
#      （页面级 changed 信号只是绕一圈回到同一个回调，缺它不影响刷新）；
#   ③ 真凶是**块被复用**：refresh() 只对**已经存在的** AmmoBlock 调 reload()，
#      而那批块是拿**旧 WeaponId + 旧槽名**建的 —— reload() 读回来的还是旧那把枪的
#      弹药行与旧标题，控件对象 id 都不变（Lead 的探针实测：id 一样、标题还是旧枪）。
#   修法：entries_blocks() 记下"这批块是按哪几把枪建的"（= _ammo_slots 的署名），
#        refresh() 发现署名变了就整套 _attach_entries() 重建 —— 新块自己会读新武器。
# --------------------------------------------------------------------------- #

#: Lead 的复现样本：单位 70 的第 117 名成员，副武器 212(M72A10 LAW) -> 266(M256 120mm)。
MEMBER_UNIT = 70
MEMBER_ROW = 117
OLD_SPECIAL = 212
NEW_SPECIAL = M256  # 266


def _weapon_name(page, weapon_id: int) -> str:
    from ba_mod_maker.core.edit.foreign import display_name

    return display_name(page.context.instance, "Weapons", weapon_id)


def _block_with_weapon(page, weapon_id: int):
    return next((block for block in _blocks(page) if block.weapon_id == weapon_id), None)


def _member_page(window):
    from ba_mod_maker.core.edit.scope import RowRef

    page = _open(window, MEMBER_UNIT)
    page.set_focus(RowRef("SquadMembers", MEMBER_ROW))
    settle()
    return page


def test_changing_the_weapon_rebuilds_the_ammo_block_below(window) -> None:
    """① 就地编辑改 SpecialWeaponId -> settle -> 块标题换成新武器，旧武器名不再出现。"""
    from ba_mod_maker.core.edit.refs import CellRef

    page = _member_page(window)
    ref = CellRef("SquadMembers", MEMBER_ROW, "SpecialWeaponId")
    assert int(page.context.cell(ref).raw) == OLD_SPECIAL, "样本数据变了（前置条件）"
    old_name = _weapon_name(page, OLD_SPECIAL)
    new_name = _weapon_name(page, NEW_SPECIAL)
    assert old_name != new_name
    before = [block.title_text for block in _blocks(page)]
    assert any(old_name in title for title in before), before

    page._commit(ref, NEW_SPECIAL)  # 与用户在那一格的下拉里选一门新枪同一条路
    settle()

    after = [block.title_text for block in _blocks(page)]
    assert any(new_name in title for title in after), f"改了武器，块标题没跟上：{after}"
    assert not any(old_name in title for title in after), f"旧武器名还挂着：{after}"
    assert _block_with_weapon(page, NEW_SPECIAL) is not None, (
        f"新武器那一块没建出来：{[b.weapon_id for b in _blocks(page)]}"
    )


def test_the_plus_button_after_changing_the_weapon_adds_ammo_for_the_new_weapon(window) -> None:
    """改完武器，「＋ 加一种弹」插的必须是**新武器**那一条 WeaponAmmunitions。

    这是用户那句话的目的（"这样我就能直接去给武器添加弹药了"）——
    标题对了但 ＋ 还插旧武器，等于没修。
    """
    from ba_mod_maker.core.edit.refs import CellRef

    page = _member_page(window)
    page._commit(CellRef("SquadMembers", MEMBER_ROW, "SpecialWeaponId"), NEW_SPECIAL)
    settle()

    block = _block_with_weapon(page, NEW_SPECIAL)
    assert block is not None
    fresh = _fresh_ammo_id(page)
    block.add_button.click()
    assert block.picker is not None, "「＋」没就地冒候选"
    block.picker.picked.emit(fresh)
    settle()

    entries = page.context.data.weapon_ammo(MEMBER_UNIT, NEW_SPECIAL)
    assert (fresh, 1) in [(ammo_id, quantity) for ammo_id, quantity, _order in entries], entries


def test_swapping_the_weapon_rebuilds_the_ammo_block_too(window) -> None:
    """②「🔁 换一个」那条路（EditorPage.swap_link）同样要跟上。"""
    from ba_mod_maker.core.edit.refs import CellRef

    page = _member_page(window)
    link = CellRef("SquadMembers", MEMBER_ROW, "SpecialWeaponId")
    old_name = _weapon_name(page, OLD_SPECIAL)
    new_name = _weapon_name(page, NEW_SPECIAL)

    page.swap_link("Weapons", NEW_SPECIAL, link)  # 就是那颗 🔁 的落点
    settle()

    assert int(page.context.cell(link).raw) == NEW_SPECIAL
    titles = [block.title_text for block in _blocks(page)]
    assert any(new_name in title for title in titles), f"🔁 换完块标题没跟上：{titles}"
    assert not any(old_name in title for title in titles), f"旧武器名还挂着：{titles}"


def test_clicking_the_ammo_name_goes_to_that_ammunition_row(window) -> None:
    """点弹种名 = 去看 Ammunitions 那一行的全部列（中栏换行）。"""
    from ba_mod_maker.core.edit.scope import RowRef

    page, block = _block_for(window, TUA, M256)
    block.lines[0].name_button.click()
    assert page.focus == RowRef("Ammunitions", HEAT)


def test_the_go_to_row_arrow_in_the_column_list_now_works(window) -> None:
    """中栏每条外键右边的「↗」：点了要真的换成它指向的那一行。

    （这条路从 f130c5f 起就是断的 —— 信号发出去没人接，点了没反应。弹药块点弹种名
    走的是同一条，所以在这里一起守。）
    """
    from PySide6.QtWidgets import QPushButton

    from ba_mod_maker.core.edit.scope import RowRef

    # 中栏一开始是 Units 那一行，它带着 CountryId -> Countries（一定有外键列）。
    page = _open(window, TUA)
    row = next((item for item in page.entries.rows if item.link_target is not None), None)
    assert row is not None, "样本这一行应当有外键列"
    target = row.link_target
    assert target is not None
    arrow = next(button for button in row.findChildren(QPushButton) if button.text() == "↗")
    arrow.click()
    assert page.focus == RowRef(target[0], target[1])


def test_ammo_line_sits_on_the_same_grid_as_the_entry_rows(window) -> None:
    """⭐ 用户原话（2026-09-30 实机）：「这一块看起来比较怪，跟上面的保持一致」。

    弹药行曾经是**自己一套**：行高 24（词条行 28）、名字顶格（词条行的中文名在
    序号那一列之后，第 42px）、数量定宽 64 挤在中栏正中间（词条行的「值」是撑满
    那一列）—— 一眼就是两段拼的。2026-09-30 夜里那次改错了地方（删的是一条
    只有军械库卡片在用的 `ammo-line` 规则），用户回「还是没改」。

    这里守三条**结构性**判据（不依赖窗口 show 过没有，量的是控件自己的约束）：
    ① 行高与词条行是同一份（不是"再写一个数"）；
    ② 名字列与词条行「中文名」那一列同宽；
    ③ 名字左边空出序号那一列 —— 于是弹种名与上面的中文名**同一个 x**。
    """
    from PySide6.QtWidgets import QSizePolicy

    from ba_mod_maker.app.screens.editor.ammo_block import LINE_HEIGHT
    from ba_mod_maker.app.widgets.row_entries import (
        EDITOR_HEIGHT,
        INDEX_COLUMN_WIDTH,
        NAME_COLUMN_WIDTH,
        ROW_HEIGHT,
        EntryRow,
    )

    page, block = _block_for(window, TUA, M256)
    assert block is not None
    line = block.lines[0]

    # ① 行高：与词条行同一份常量（并且装得下它自己的浮层）
    assert LINE_HEIGHT == ROW_HEIGHT, "弹药行必须与词条行同高 —— 两块贴在一起才像一块"
    # 与 test_theme.test_entry_row_fits_its_inline_editor 同一条判据：
    # 行高 = 浮层 + 上下各 1px（实机看就是"被裁的破框"）。
    assert LINE_HEIGHT >= EDITOR_HEIGHT + 2, "行高装不下就地编辑浮层（实机看就是被裁的破框）"
    rows = page.findChildren(EntryRow)
    assert rows, "这一页本来就该有词条行 —— 不然这条守卫是空转"
    assert line.maximumHeight() == rows[0].maximumHeight() == ROW_HEIGHT

    # ②③ 列线：名字同宽、左边留出序号那一列
    assert line.name_button.minimumWidth() == NAME_COLUMN_WIDTH
    assert rows[0].name_label.minimumWidth() == NAME_COLUMN_WIDTH, (
        "词条行那边的名字列变了 —— 两边的列宽都要跟着这一个常量走"
    )
    first = line.layout().itemAt(0)
    assert first is not None and first.widget() is not None, (
        "第一项必须是序号列的留白（一个真控件）—— 否则弹种名会顶到最左边、与上面的中文名错开一列"
    )
    assert first.widget().minimumWidth() == INDEX_COLUMN_WIDTH
    # ❗ 名字那一格的横向策略不许是 Ignored：Ignored = "宽度无所谓"，布局会把它的
    #    宽度算成 0，右边撑满的数量就压到名字身上（实机量到名字在 34、数量在 40，
    #    两个文本叠在一起）。宽度已经写死，策略必须是 Fixed。
    assert line.name_button.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Ignored, (
        "名字格的尺寸策略若是 Ignored，数量那一格会压到它身上"
    )
