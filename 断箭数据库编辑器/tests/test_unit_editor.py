"""单位编辑器的集成冒烟（双击进入 / 就地编辑 / 专用页 / 保存）。

为什么不用假控件：这一层要验的正是"控件和数据接对了没有" ——
双击到底进没进编辑器、改一格卡片跟不跟、立绘上的角标什么时候出现。
用假控件测出来的只是"我以为的接线"。

无字体的平台（offscreen）会 skip —— 与 test_armory_screen.py 同一策略：
宁可不给结论，也不给假结论。
"""

from __future__ import annotations

import zipfile

import pytest
from PySide6.QtWidgets import QWidget

from ba_mod_maker.app.screens.editor.entries_panel import table_emoji, table_title
from ba_mod_maker.app.screens.editor.relation_tree_panel import COL_ID
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 设计稿的示例单位：BTR-82A（Id 1）。实测边界读数是 21 张表 / 1211 个格子。
BTR82A = 1
#: 步兵单位（Id 645）：有班组成员行，用来验专用页 +1 / ✕。
INFANTRY = 645


# 夹具（qapp / instance / window）统一在 tests/conftest.py：
#   window = **一个模块一个**主窗口，每个用例只把一份干净数据接回去
#   （为什么、怎么保证隔离，见 conftest 的模块 docstring）。


# --------------------------------------------------------------------------- #
# 第 1 步 · 入口：双击
# --------------------------------------------------------------------------- #


def test_double_click_opens_the_editor_and_escape_returns(window) -> None:
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY, PAGE_UNIT_EDITOR

    assert window._tabs.currentIndex() == PAGE_ARMORY
    _double_click(window, BTR82A)
    assert window._tabs.currentIndex() == PAGE_UNIT_EDITOR
    assert window._unit_editor.current_unit_id == BTR82A
    # 「← 返回军械库」与 Esc 同效
    window._unit_editor.back_requested.emit()
    assert window._tabs.currentIndex() == PAGE_ARMORY


def test_single_click_still_only_selects(window) -> None:
    """单击 = 选中看卡（不进编辑器）—— 用户定的交互契约第 1 条。"""
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY

    row = window._armory.row_widgets[0]
    card = row.armory_row.cards[0]
    row.card_clicked.emit(card.unit_id)
    assert window._tabs.currentIndex() == PAGE_ARMORY
    assert window._armory.detail_panel.current_unit_id() == card.unit_id


def test_editor_is_not_in_the_navigation_bar(window) -> None:
    """编辑器**不进导航栏**：顶部是 军械库 / 国家 / 专精 / 数据表 / 检索 五个页签。

    用户口径（界面重做定稿 块 2）：国家 / 专精 插在军械库与数据表中间；
    编辑器仍然只能靠双击卡进、靠面包屑最左边那一级出。
    """
    from PySide6.QtWidgets import QPushButton

    buttons = [b.text() for b in window.findChildren(QPushButton) if b.property("role") == "nav"]
    assert buttons == ["军械库", "国家", "专精", "数据表", "检索"]


def test_entering_the_editor_does_not_touch_the_data(window) -> None:
    """进编辑器：**数据一个字节没变**（判据：变更集为空）。"""
    _double_click(window, BTR82A)
    assert window._unit_editor.changeset.is_empty
    assert window._unit_editor.context is not None


# --------------------------------------------------------------------------- #
# 第 2 步 · 骨架：左卡 + 右「全部词条」+ 立绘校验角标
# --------------------------------------------------------------------------- #


def test_the_right_panel_lists_the_units_row(window) -> None:
    """⭐ 口径变更（界面重做定稿 块 1 / §2.1）：

        「我说它的全部词条，是 **Units 的词条**，别把其它表的词条带进去。」

    双击单位 -> 右栏是 Units 那一行的 **31 列**，一条一行、一屏看完。
    不是从前的 19 张表 / 1180 格（那个数还在 unit_scope_summary()，但它
    回答的是另一个问题：这个单位牵扯到哪些行）。
    """
    _double_click(window, BTR82A)
    editor = window._unit_editor
    panel = editor.main_page.entries
    assert panel is not None
    assert len(panel.rows) == 31, f"Units 那一行是 31 列：{len(panel.rows)}"
    # 抬头 = **图标位** + 纯行名；副标题纯文字（用户口径 2026-10-01 第二轮：
    # 「我不是要 emoji，我是要图标」——图标位只能有一个，emoji 只在没真图时兜底）。
    assert panel.title_text == "BTR-82"
    assert panel.title_mark is not None
    assert panel.title_mark.emoji == table_emoji("Units")
    assert panel.subtitle_text.startswith(f"{table_title('Units')} Units#{BTR82A} · 31 列")
    assert panel.summary_text == "31 列"
    # 这个单位牵扯到的行仍然算得出来（另一个口径，界面上不当"词条"用）
    assert editor.unit_scope_summary() == "19 张表 · 1180 个格子"


def test_entry_rows_show_the_chinese_name_and_the_place(window) -> None:
    """一条 = 序号 + 中文名 + 值 + **英文列名**（界面重做定稿 §1 的行样式）。"""
    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    panel = window._unit_editor.main_page.entries
    row = panel.row_for(CellRef("Units", BTR82A, "Cost"))
    assert row is not None
    assert row.name_text == "基础成本"
    assert row.column_text == "Cost"
    assert row.value_text  # 值那一格有字
    # 落点（表#Id · 原列名）在悬浮提示里 —— 行右边只写**英文列名**
    assert "Units#1 · Cost" in row.name_label.toolTip()


def test_entries_are_one_row_per_column_with_no_folding(window) -> None:
    """31 条一条一行，**不分小标题、不折叠**（用户口径："一条不删"）。

    老的三级树（表 -> 行 -> 列）是上一版口径，这里守住它不在这一页长回来。

    §5-4：那套老面板的类（entries_panel 的 GroupSection / RowSection / CellRow）
    已经删了，哨兵不能再指着它们 —— 换成**行为面**三条，抓的是"折叠加回中栏"本身：

    ① 31 条一条不少、序号 1..31；
    ② 一行都没被收起来（每一行的值那一格都会随中栏显示出来）；
    ③ 中栏里没有**可勾选的按钮** —— 老折叠头 _Header 就是 setCheckable(True) +
       勾选态控制 body 显隐的，那种东西一回来这条就红。
    """
    from PySide6.QtWidgets import QPushButton

    _double_click(window, BTR82A)
    panel = window._unit_editor.main_page.entries
    assert [row.index_text for row in panel.rows] == [str(i) for i in range(1, 32)], "条数变了"
    assert all(row.value_button.isVisibleTo(panel) for row in panel.rows), "有词条行被折叠收起来了"
    toggles = [button for button in panel.findChildren(QPushButton) if button.isCheckable()]
    assert not toggles, f"中栏里冒出了折叠开关（可勾选的按钮）：{[b.text() for b in toggles]}"
    assert not panel.has_editor


def test_entries_are_only_the_units_row(window) -> None:
    """阵营 / 专精（以及别的表）都不进这个单位的词条 —— 只列 Units 那一行。"""
    _double_click(window, BTR82A)
    panel = window._unit_editor.main_page.entries
    assert {row.cell.table for row in panel.rows} == {"Units"}


def test_the_panel_header_says_which_row_you_are_looking_at(window) -> None:
    """抬头 = 这一行叫什么；副标题 = 表#Id · N 列（设计块 4 的标题口径）。

    ❗ 抬头前面是**图标位**（用户口径 2026-10-01：「武器的图标、弹药的图标、改装的
    图标，都应该在标题栏上显示」）：有真图画真图、没真图才画表 emoji 兜底 ——
    所以标题文字本身是纯行名，记号只有一份出处（entries_panel.table_emoji）。
    """
    _double_click(window, BTR82A)
    panel = window._unit_editor.main_page.entries
    assert panel.title_text == "BTR-82"
    assert panel.title_mark is not None and panel.title_mark.emoji == table_emoji("Units")
    assert panel.subtitle_text == f"{table_title('Units')} Units#{BTR82A} · 31 列 · 点值就地改"
    assert panel.summary_text == "31 列"


#: 用户报的那个样本：单位 21（Stryker RV）的炮塔 Turrets#18 —— 它在树上出现两次
#: （改装槽引用路径 `mods/…/slot/0` + 炮塔组规范位置 `turrets/18`）。
DUP_UNIT = 21
DUP_ROW = 18


def test_clicking_the_turret_row_expands_the_turret_group_not_the_mod_slot(window) -> None:
    """⭐ 用户报的：「我明明点的是画红圈的炮塔（M2 塔 Turrets#18），为什么展开的却是上面的改装件？」

    同一个 (表, 行 Id) 在树上出现两次：改装槽那条是**引用路径**，炮塔组那条是**规范位置**。
    两条规则：① 程序化切焦点（没有点选记录，例：↗ / 检索 / 进编辑器）落**规范位置**；
    ② 用户点的是哪一条，高亮与展开就停在哪一条（不能被 find 抢走）。
    """
    from ba_mod_maker.core.edit.scope import RowRef

    window._open_unit_editor(DUP_UNIT)
    page = window._unit_editor.main_page
    tree = page.tree

    hits = [
        item.key
        for item in tree.tree.flatten()
        if item.table == "Turrets" and item.row_id == DUP_ROW
    ]
    assert len(hits) >= 2, f"样本不再重复（数据变了？）：{hits}"
    slot_key = next(key for key in hits if key.startswith("mods/"))
    group_key = next(key for key in hits if not key.startswith("mods/"))
    assert group_key == "turrets/18", hits

    # ① 程序化切到 Turrets#18：展开的是**炮塔那条路**，一个 mods 节点都不许展开。
    page.set_focus(RowRef("Turrets", DUP_ROW))
    assert tree.current_key == group_key, f"高亮跑到 {tree.current_key} 去了"
    expanded = tree.expanded_keys()
    assert "turrets" in expanded and group_key in expanded, expanded
    assert not any(key.startswith("mods") for key in expanded), f"点炮塔却展开了改装件：{expanded}"

    # ② 反向：用户点的就是改装槽里那条槽位行 -> 必须停在它自己身上。
    tree._clicked_key = slot_key
    assert tree.select_row("Turrets", DUP_ROW)
    assert tree.current_key == slot_key, f"被抢回去了：{tree.current_key}"
    assert slot_key.rsplit("/slot/", 1)[0] in tree.expanded_keys()


def test_nothing_flashes_as_a_top_level_window(qapp, instance) -> None:
    """全程**不许冒出顶层窗**（菜单 / tooltip 这类本来就该是顶层的除外）。

    背景：用户报的"点一个单位的卡片会出现一个小弹窗"。2026-09-30 实锤了真凶：
    `detail_panel._WeaponArt.__init__` 里"无父控件先 setVisible(True)、后 addWidget"
    —— 点卡片那条路上**确实**有一个建窗点。回归闸在
    `test_armory_screen.py::test_clicking_a_card_never_maps_a_stray_window`。

    本条守的是**编辑器这一路**：进编辑器 / 开就地编辑 / 关整页这一圈不许冒出新的顶层窗。

    ⚠️ 上一轮写在这里的结论有两条是错的，别再照着找：
    ① "点卡片这条路上没有任何一处创建窗口" —— 错的（见上）；
    ② "摘控件（setParent(None)）会闪窗" —— 错的：Qt 摘之前会先 hide，
       实测把无父父控件里的子控件 show() 一个窗都不出。闪窗的**唯一**形状是
       "这个控件自己还没有父控件的时候被显示"。
    """
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtWidgets import QApplication, QWidget

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    class Spy(QObject):
        """记录"冒出来的顶层窗"。菜单 / tooltip 这类本来就该是顶层，排除掉。"""

        IGNORED = {Qt.WindowType.Popup, Qt.WindowType.ToolTip, Qt.WindowType.Tool}

        def __init__(self) -> None:
            super().__init__()
            self.seen: list[str] = []

        def eventFilter(self, obj, event):
            if (
                event.type() == QEvent.Type.Show
                and isinstance(obj, QWidget)
                and obj.parent() is None
                and obj.windowType() not in self.IGNORED
            ):
                self.seen.append(type(obj).__name__)
            return False

    screen = UnitEditorScreen()
    screen.resize(1400, 800)
    screen.set_instance(instance)
    # ❗ 必须真的 show()：控件**可见**时 setParent(None) 才会变成顶层窗 ——
    # 不可见的控件摘下来什么也不会发生，那样这条用例就抓不到 bug（变异验证过）。
    screen.show()
    qapp.processEvents()
    spy = Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        screen.open_unit(BTR82A)
        page = screen.main_page
        cell = screen.context.cell(CellRef("Units", BTR82A, "Cost"))
        page._open_inline_editor(cell)
        page._commit(cell.ref, 60)  # 改完 -> 浮层被摘掉
        page._open_inline_editor(screen.context.cell(cell.ref))
        page.close_editor()  # 直接关
        screen.open_weapon_page(screen.card_panel.card.weapons[0].weapon_id)
        screen.close_page()  # 销毁一整页（里面也在摘控件）
    finally:
        QApplication.instance().removeEventFilter(spy)
    assert spy.seen == [], f"这些控件曾经变成过顶层小窗（用户看到的那个小弹窗）：{spy.seen}"
    screen.deleteLater()


def test_entering_the_editor_repeatedly_keeps_the_three_columns_alive(qapp, instance) -> None:
    """反复进出编辑器：三栏那三个控件（树 / 词条 / 只读卡）**一个都不许被删掉**。

    实踩（1f08c38 引入的回归）：`EditorPage._rebuild_left_only` 拿 `detach()` 摘左栏，
    而 detach() 里带一句 `deleteLater()` —— 主编辑页的左栏**就是那个常驻控件**
    （`build_left()` 返回同一棵关系树），于是第 2 次重排就把它排进了删除队列，
    第 3 次双击进编辑器直接 RuntimeError: Internal C++ object already deleted：
    终端打 traceback、左栏空掉。实测复现：open_unit 连开三次，第 3 次炸。

    ❗ 关键是那句 `sendPostedEvents(DeferredDelete)` —— deleteLater() 要等事件循环
    才真的删；不推这一下，用例永远绿。
    """
    from PySide6.QtCore import QEvent

    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    screen = UnitEditorScreen()
    screen.resize(1400, 800)
    screen.set_instance(instance)
    try:
        for unit_id in (BTR82A, INFANTRY, BTR82A, INFANTRY):
            screen.open_unit(unit_id)
            qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            tree = screen.tree_panel
            card = screen.card_panel
            tree.objectName()  # C++ 对象被删了，这一句会抛 RuntimeError
            card.objectName()
            assert screen.main_page._left is tree, "左栏不再是那棵关系树了"
            assert screen.main_page.card is card, "右栏那张只读卡被换掉了"
            assert screen.main_page.entries is not None, "中栏词条没建起来"
    finally:
        screen.deleteLater()


def test_no_validation_chip_when_nothing_is_wrong(window) -> None:
    """**立绘上什么都没有** —— 原版既有违规不报（BTR-82A 基线里本来就有 1 条）。"""
    _double_click(window, BTR82A)
    assert window._unit_editor.validation_text == ""


def test_breaking_a_field_shows_the_chip_and_reverting_clears_it(window, qapp) -> None:
    """座位数 8 -> 2 -> 立绘上冒「校验 N 处」；改回 8 -> 角标消失。

    ❗ 角标是**延后一轮事件循环**算的（改一格就同步跑一遍全库校验太费，
    见 UnitEditorScreen._schedule_findings），所以这里每次改完要 processEvents()。
    """
    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    editor = window._unit_editor
    seats = editor.context.cell(CellRef("Units", BTR82A, "InfantrySlots"))
    assert seats is not None and seats.raw == 8

    editor.apply_cell(seats.ref, 2)
    qapp.processEvents()  # 角标延后一轮算（见上面的说明）
    assert editor.validation_text.startswith("校验 5 处")
    assert "BTR-82" in editor.validation_text, "文案里的 Id 要换成名字"
    assert "Id 1" not in editor.validation_text

    editor.apply_cell(seats.ref, 8)
    qapp.processEvents()
    assert editor.validation_text == ""


# --------------------------------------------------------------------------- #
# 第 3 步 · 就地编辑
# --------------------------------------------------------------------------- #


def test_editing_a_cell_updates_the_card_and_undo_restores_it(window, qapp) -> None:
    """改「座位数」-> 卡片跟着变；Ctrl+Z 回原值。

    ❗ 卡片的刷新现在**排到下一轮事件循环**（2026-10-01「不要全量保存」这一轮）：
    它原来自己订阅变更集，同步跑在 set_value 里 —— 在编成台上改一格时，
    **看不见的**关系树那一页也被整块重排（实测 58.9ms，见 _work/probe_edit_profile.py）。
    改成"谁在台上谁刷"之后，三栏这一页的卡跟中栏词条 / 左树同一个节奏
    （UnitEditorScreen._schedule_refresh -> _refresh_pages -> MainEditorPage.refresh），
    所以这里每次改完要 processEvents() —— 与角标那条是同一个契约（见本文件 :329）。
    用户看到的没有区别：定时器 0ms，重画在同一帧里完成，点击那一下反而不再被堵住。
    """
    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    editor = window._unit_editor
    # ❗ 2026-10-01：载具现在**默认落在编成台**，而 card_panel 恒指**主页面**那张卡；
    #    加上"谁在台上谁刷"（_refresh_pages / AGENTS §0.7.1），不切页的话改一格那张卡不动。
    #    这一条守的是三栏那一页的卡，所以显式切回去 —— 与用户点「🌲 关系树」是同一个动作。
    editor.show_main()
    card = editor.card_panel
    before = _stat_text(card, "seats")
    assert before == "8"

    editor.apply_cell(CellRef("Units", BTR82A, "InfantrySlots"), 9)
    qapp.processEvents()  # 卡片的刷新排在下一轮（见 docstring）
    assert _stat_text(card, "seats") == "9", "卡片没跟着数据走"

    editor.undo()
    qapp.processEvents()
    assert _stat_text(card, "seats") == "8"
    assert editor.changeset.is_empty


def test_opening_a_cell_gives_the_keyboard_right_away(qapp, instance) -> None:
    """⭐ 用户原话（2026-09-29）：「点一下，再输入数字，发现第一次输不进去，还得再点一下」。

    真因：浮层自己是 QFrame（focusPolicy 默认 NoFocus）—— editor.setFocus() 是
    空操作；构造里那句 line.setFocus() 又发生在控件还没有父控件的时候。
    所以挂上浮层之后必须把焦点交给**里面那个输入控件**（InlineEditor.focus_input）。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.resize(1400, 800)
    screen.set_instance(instance)
    screen.show()  # 焦点要有窗口才谈得上
    qapp.processEvents()
    try:
        screen.open_unit(BTR82A)
        page = screen.main_page
        cell = screen.context.cell(CellRef("Units", BTR82A, "Cost"))
        page._open_inline_editor(cell)
        editor = page.entries.open_row.editor
        assert editor is not None
        assert editor.window().focusWidget() is editor.input_widget, "点开就该能直接打字"
    finally:
        screen.close()
        screen.deleteLater()


def test_inline_editor_opens_on_the_value_and_commits(qapp, instance) -> None:
    """点开一格 -> 出现输入框 -> Enter 确认 -> 数据变了、浮层收起来。"""
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    page = screen.main_page
    panel = page.entries
    assert panel is not None

    cell = screen.context.cell(CellRef("Units", BTR82A, "Cost"))
    assert cell is not None and cell.raw == 55
    page._open_inline_editor(cell)
    assert panel.has_editor
    assert panel.open_cell == cell.ref

    page._commit(cell.ref, 60)
    assert not panel.has_editor
    assert screen.context.cell(cell.ref).raw == 60
    qapp.processEvents()  # 卡片随页面刷新，排在下一轮（见上一个用例的说明）
    # 卡片跟着走：成本徽章就是 card_cost = Cost + 选中选项费用之和
    assert screen.card_panel.current_cost == 60
    screen.deleteLater()


def test_foreign_cells_show_names_not_ids(qapp, instance) -> None:
    """外键那一格显示的是**名字**；原始 Id 只在悬浮提示里。"""
    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    cells = screen.context.cells
    mobility = next(c for c in cells if c.table == "UnitPropulsions" and c.column == "MobilityId")
    assert mobility.foreign == "Mobility"
    assert mobility.text == "Wheels BTR82A NonAmph"
    assert mobility.text != str(mobility.raw)
    assert mobility.raw_text == str(mobility.raw)
    screen.deleteLater()


def test_shared_cell_warns_and_direct_edit_really_touches_the_others(qapp, instance) -> None:
    """「生命值」落在 Armors#1（4 个单位共用）：

    - 界面上要**说清楚**"这一行还被 N 个单位用着 —— 直接改会一起变"；
    - 直接改**真的**会一起变（这不是 bug，是共享的语义 —— 所以才有那句话）；
    - 另一条用例验「另存为新模板」之后别人不动。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    context = screen.context

    ref = _health_ref(screen)
    cell = context.cell(ref)
    assert cell.shared_with == 4, "样本装甲模板的共享度变了？"
    assert context.link_ref(ref) is not None, "「生命值」必须能另存为新模板"
    # 设计稿点名的那条：这一格被选项覆盖，link_ref 是 Options#504.ArmorId。
    assert str(context.link_ref(ref)) == "Options#504.ArmorId"

    others = sorted(context.sharing.units(ref.table, ref.row_id))
    assert len(others) == 4
    before = {uid: _armor_health(instance, uid) for uid in others}

    context.set_value(ref, cell.raw + 1)
    assert _armor_health(instance, BTR82A) == cell.raw + 1
    changed = [uid for uid in others if _armor_health(instance, uid) != before[uid]]
    assert changed, "共享行改了别人却没事？那共享度是假的"
    assert len(changed) == 4, "共用它的 4 个单位应当一起变"
    screen.deleteLater()


def test_save_as_new_template_leaves_the_other_units_alone(qapp, instance) -> None:
    """走「另存为新模板」之后：Armors 多一行、Options#504.ArmorId 指过去，

    **另外 3 个单位的生命值逐字节不变**（设计 §5 的验收判据 b）。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.clone import next_row_id
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    context = screen.context

    ref = _health_ref(screen)
    cell = context.cell(ref)
    link = context.link_ref(ref)
    assert link is not None
    others = [uid for uid in sorted(context.sharing.units(ref.table, ref.row_id)) if uid != BTR82A]
    assert others
    before = {uid: _armor_health(instance, uid) for uid in others}

    table = instance.table("Armors")
    expected = next_row_id(table)
    message = context.save_as_template(ref)
    assert "另存为新模板" in message
    assert any(row[table.column_index("Id")] == expected for row in table.rows)
    assert context.cell(link).raw == expected, "Options#504.ArmorId 没指到新行"

    context.set_value(CellRef(ref.table, expected, ref.column), cell.raw + 3)
    assert _armor_health(instance, BTR82A) == cell.raw + 3
    for uid in others:
        assert _armor_health(instance, uid) == before[uid], f"单位 {uid} 被连带改了"
    screen.deleteLater()


def test_a_shared_cell_offers_no_copy_button(qapp, instance) -> None:
    """点开「生命值」（4 个单位共用）：浮层里**一个按钮都没有**。

    ❗ 用户 2026-11-11（指着那颗 📄 的悬浮提示「复制一份给本单位专用」）：
       「不需要这个复制功能，删掉」—— 按钮、信号 `save_as_template_requested`、
       两处宿主的 connect、`_save_as_template` 这个槽一起删了。
    ⚠️ 删的是**入口**，不是「共享行不能改了」：共享行照旧直接改那一行（别人一起变）。
    这条用例就是那把尺子 —— 按钮再被挂回来，这里当场红。
    """
    from PySide6.QtWidgets import QPushButton

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.scope import RowRef

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    page = screen.main_page
    ref = _health_ref(screen)
    cell = screen.context.cell(ref)
    assert cell.shared_with == 4, "样本变了：这一格不再是被 4 个单位共用"

    # ❗ 先"走到那一行"——右栏永远只显示刚点的那一行，生命值落在 Armors#1，
    #    不在 Units#21 上（界面上这一步是点卡上的「生命值」）。
    page.set_focus(RowRef(ref.table, ref.row_id))
    page._open_inline_editor(cell)
    editor = page.entries.open_row.editor
    assert editor is not None
    texts = [b.text() for b in editor.findChildren(QPushButton)]
    assert texts == [], f"共享行的浮层里不该有按钮了：{texts}"
    assert not hasattr(editor, "save_as_template_requested"), "那个信号删了就别再挂回来"
    screen.deleteLater()


def test_inline_editor_has_no_extra_buttons_on_an_exclusive_cell(qapp, instance) -> None:
    """独占的一格（座位数落在 Units 自己身上）：同样只剩输入控件。

    与共享那一格同一条口径 —— 浮层里**没有**任何按钮（`📄` 那个「复制一份给本单位专用」
    已按用户 2026-11-11 的话删掉，见上一条用例）。
    """
    from PySide6.QtWidgets import QPushButton

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    page = screen.main_page
    cell = screen.context.cell(CellRef("Units", BTR82A, "InfantrySlots"))
    page._open_inline_editor(cell)
    editor = page.entries.open_row.editor
    assert editor is not None
    texts = [b.text() for b in editor.findChildren(QPushButton)]
    assert texts == [], f"独占的一格只剩输入控件，不该有按钮：{texts}"
    screen.deleteLater()


def test_the_editor_sits_in_the_same_row_as_the_value(qapp, instance) -> None:
    """用户口径："我要的是直接在画红圈的地方改，别出这种烦人的窗口"。

    判据：编辑控件是那一行**值那一格**里的兄弟控件，不是另起一行；
    而且编辑期间值按钮是**藏起来**的（同一格不能同时有值和输入框）。
    """
    from PySide6.QtWidgets import QLineEdit

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.resize(1400, 800)
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    page = screen.main_page
    cell = screen.context.cell(CellRef("Units", BTR82A, "Cost"))
    page._open_inline_editor(cell)
    row = page.entries.open_row
    assert row is not None
    editor = row.editor
    assert editor is not None
    # 它是"那一行"（line 那一条横排）里的兄弟控件 —— 不是下面另起的一块
    assert editor.parent() is row.line, "编辑控件必须长在那一行里"
    assert not row.value_button.isVisible(), "编辑期间原来那个值要藏起来（同一格不留两份）"
    assert editor.findChild(QLineEdit) is not None
    # 行高只多了一点（不是一个"面板"）
    assert editor.sizeHint().height() < 60, editor.sizeHint().height()
    screen.deleteLater()


def test_clicking_a_value_in_the_entries_panel_really_opens_the_editor(qapp, instance) -> None:
    """**点**（真鼠标事件）右栏某一格的值 -> 浮层出现。

    这条是补课：原来的用例直接调 page._open_inline_editor(cell)，把
    老的三级面板（CellRow -> GroupSection -> 那个表→行→列面板；**那套类已删**，见
    entries_panel 的模块注释）那条信号链绕过去了 ——
    于是"点哪一格都没反应"这种断链测不出来（用户实测报的就是这个）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.resize(1400, 800)
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    page = screen.main_page
    panel = page.entries
    row = panel.row_for(CellRef("Units", BTR82A, "Cost"))
    assert row is not None

    QTest.mouseClick(row.value_button, Qt.MouseButton.LeftButton)  # 就是要真点一下
    assert panel.has_editor, "点了值却没弹出编辑浮层 —— 信号链断了"
    assert panel.open_cell == CellRef("Units", BTR82A, "Cost")
    screen.deleteLater()


def test_main_page_does_not_repeat_the_screens_header(window) -> None:
    """主编辑页**不自带页头**：外层已经有面包屑那条了，两条同义 = 白占一行。

    用户原话："这一块纵向排列，很占空间"。
    """
    _double_click(window, BTR82A)
    editor = window._unit_editor
    bars = [
        w for w in editor.main_page.findChildren(QWidget) if w.property("role") == "editor-header"
    ]
    assert bars == [], "主编辑页不该再画一条页头"
    # 专用页要保留（它是"← 返回单位编辑器"的唯一出口）
    editor.open_weapon_page(editor.card_panel.card.weapons[0].weapon_id)
    page_bars = [
        w
        for w in editor.current_page.findChildren(QWidget)
        if w.property("role") == "editor-header"
    ]
    assert len(page_bars) == 1


# --------------------------------------------------------------------------- #
# 第 4 步 · 四个专用页
# --------------------------------------------------------------------------- #


def test_four_pages_open_with_all_columns(qapp, instance) -> None:
    """四页各能进，列一个不少（Weapons 40 / Ammunitions 67 / Options 46 / SquadMembers 6）。"""
    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)

    card = screen.card_panel.card
    weapon_id = card.weapons[0].weapon_id
    ammo_id = card.weapons[0].ammo[0].ammo_id
    screen.open_weapon_page(weapon_id)
    assert screen.page_titles[-1] == "武器"
    assert len(screen.current_page.entries_cells()) == 40

    screen.open_ammo_page(weapon_id, ammo_id)
    assert screen.page_titles[-1] == "弹药"
    assert len(screen.current_page.entries_cells()) == 67

    screen.open_options_page(card.options[0].choices[0].option_id if card.options else 501)
    assert screen.page_titles[-1] == "改装选项"
    assert len(screen.current_page.entries_cells()) == 46

    screen.close_page()
    assert screen.current_page is screen.main_page

    screen.open_unit(INFANTRY)
    screen.open_squad_page()
    assert screen.page_titles[-1] == "班组成员"
    screen.deleteLater()


def test_squad_page_adds_and_removes_a_member(qapp, instance) -> None:
    """+1 / ✕ 真的改到数据（步兵 645）。"""
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.pages.squad_page import SquadPage

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    screen.open_squad_page()
    page = screen.current_page
    assert isinstance(page, SquadPage)
    before = len(screen.context.row_ids("SquadMembers"))
    assert before > 0, "样本步兵没有班组成员行"

    page._add_member()
    assert len(screen.context.row_ids("SquadMembers")) == before + 1
    screen.undo()
    assert len(screen.context.row_ids("SquadMembers")) == before

    page._remove_member(screen.context.row_ids("SquadMembers")[0])
    assert len(screen.context.row_ids("SquadMembers")) == before - 1
    screen.undo()
    assert len(screen.context.row_ids("SquadMembers")) == before
    screen.deleteLater()


def test_removing_a_squad_member_updates_the_tree_and_falls_back(qapp, instance) -> None:
    """⭐ 用户口径（2026-10-01 实机）：删掉班组成员之后，**左树里那一栏还在、还数着 6 个人**，
    中栏停在「（找不到 SquadMembers #1645）· 0 列」——
    「我删掉步兵后，这个步兵的栏还在，这种问题你完全可以实机试出来」。

    真因两条，都在"删"这条路上：
    ① 删完没丢 `CardData` 缓存 -> 树照**旧名单**重画
       （同一个坑插行那次踩过，见 viewmodel/unit_card.py::Rows.invalidate）；
    ② 中栏的 focus 还指着被删掉的那一行 -> 不回退就停在"找不到"上。

    这条量的是**用户看得见的两样东西**：树上还剩几条、中栏停在哪一行
    —— 只断言 `context.row_ids()` 变少是抓不到这个 bug 的（那一条一直是绿的）。
    """
    from PySide6.QtWidgets import QTreeWidget

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.scope import RowRef
    from conftest import settle

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    page = screen.main_page
    panel = page.tree
    widget = panel.findChild(QTreeWidget)
    assert widget is not None

    def walk(item):
        yield item
        for index in range(item.childCount()):
            yield from walk(item.child(index))

    def member_items() -> list:
        # ❗ 按**右边那一列**的 `#Id` 认（2026-10-01：表名从名字列挪走了，
        #    `"SquadMembers" in item.text(0)` 再也匹配不上任何一行）。
        wanted = {f"#{member_id}" for member_id in page.context.row_ids("SquadMembers")}
        out = []
        for index in range(widget.topLevelItemCount()):
            for item in walk(widget.topLevelItem(index)):
                if item.text(COL_ID) in wanted:
                    out.append(item)
        return out

    members = page.context.row_ids("SquadMembers")
    assert len(members) >= 4, "样本步兵要有几名成员才验得动"
    victim = members[3]
    page.set_focus(RowRef("SquadMembers", victim))
    settle()
    before = len(member_items())
    assert before == len(members), f"树上应当列出全部 {len(members)} 名成员，现在 {before}"

    assert panel.click_tool(f"squad/member/{victim}", "🗑"), "树上那一行应当有删除按钮"
    settle()

    assert len(member_items()) == before - 1, "删掉的那一名不许还留在树上"
    assert page.focus == RowRef("Units", INFANTRY), (
        "中栏要回单位那一行 —— 停在（找不到 …）上就是用户看到的那个坏状态"
    )
    screen.deleteLater()


def test_the_squad_weapon_registry_can_be_added_and_removed(qapp, instance) -> None:
    """⭐ 用户口径（2026-10-01）：「我还要能自己增删武器的注册」。

    `SquadWeapons` = 这个班注册了哪些武器（成员的主/副武器、它们的弹药都挂在它下面）。
    - **加**：组节点那颗 ＋ 要**先把"能加什么"摆出来**（班组成员 / 班用武器）——
      从前第二块只藏在右键菜单里，用户找不到，所以他说"我要能自己增删"；
    - **删**：每门武器那一行的 🗑；
    - 删的若是**还有成员扛着**的枪，状态栏要当场说清 —— 那条 error 级不变量
      （SquadMembers ⊆ SquadWeapons）一被违反，游戏里那件武器就不显示。
    """
    from PySide6.QtWidgets import QTreeWidget

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.relation_tree_panel import TOOL_ADD, TOOL_REMOVE
    from conftest import settle

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    page = screen.main_page
    panel = page.tree
    messages: list[str] = []
    screen.status_message.connect(messages.append)
    assert panel.findChild(QTreeWidget) is not None

    old_ids = set(page.context.row_ids("SquadWeapons"))
    assert old_ids, "样本步兵要有班用武器注册行"

    # ① 组节点上要有**两颗**按钮：＋（班组成员）/ 🔫（班用武器）—— 后者从前只在右键菜单里。
    #    ＋ 是"照现有成员复制"，不冒候选；🔫 才冒候选（挑一门 Weapons）。
    members_before = len(page.context.row_ids("SquadMembers"))
    assert panel.click_tool("squad", TOOL_ADD), "＋ 加一名班组成员"
    settle()
    assert len(page.context.row_ids("SquadMembers")) == members_before + 1
    screen.undo()
    settle()
    assert len(page.context.row_ids("SquadMembers")) == members_before

    assert panel.click_tool("squad", "🔫"), "🔫 加一门班用武器（新入口，以前只在右键菜单里）"
    picker = panel.picker
    assert picker is not None and picker.table == "Weapons", "候选来自 Weapons 模板表"
    picker.picked.emit(picker.row_id_at(0))
    settle()
    added = set(page.context.row_ids("SquadWeapons")) - old_ids
    assert len(added) == 1, "加一门班用武器 = 插一行 SquadWeapons"

    # ② 那一行的 🗑 能删掉它
    row_id = next(iter(added))
    assert panel.click_tool(f"squad/weapon/{row_id}", TOOL_REMOVE)
    settle()
    assert set(page.context.row_ids("SquadWeapons")) == old_ids

    # ③ 删"还有成员扛着"的注册行：状态栏要当场说清
    squad = page.context.data.t("SquadWeapons")
    used = next(
        squad.i(row, "Id")
        for row in squad.all()
        # 122 = AK-74RMO（645 里 4 名成员扛它 —— 删掉注册行就会踩那条不变量）
        if squad.i(row, "UnitId") == INFANTRY and squad.i(row, "WeaponId") == 122
    )
    assert panel.click_tool(f"squad/weapon/{used}", TOOL_REMOVE)
    settle()
    assert any("名成员" in text and "还有" in text for text in messages), messages
    assert TOOL_ADD == "＋"
    screen.deleteLater()


def test_changing_a_members_weapon_registers_it_automatically(qapp, instance) -> None:
    """⭐ 用户口径（2026-10-01）：「我给新成员添加武器时，如果注册表没有，自动给我注册」。

    不补的话，那条 error 级不变量当场被违反（成员的枪必须在同单位的 SquadWeapons 里），
    实机表现是**游戏里那件武器不显示** —— 用户不该为此自己跑另一处补注册。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef
    from ba_mod_maker.core.edit.scope import RowRef
    from conftest import settle

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    page = screen.main_page
    member = page.context.row_ids("SquadMembers")[0]
    page.set_focus(RowRef("SquadMembers", member))

    def registered_weapons() -> set[int]:
        squad = page.context.data.t("SquadWeapons")
        return {
            squad.i(row, "WeaponId") for row in squad.all() if squad.i(row, "UnitId") == INFANTRY
        }

    before = registered_weapons()
    weapons = page.context.data.t("Weapons")
    fresh = next(
        weapons.i(row, "Id") for row in weapons.all() if weapons.i(row, "Id") not in before
    )

    # ① 换一把**没注册**的枪 -> 自动补一行 SquadWeapons
    page._commit(CellRef("SquadMembers", member, "PrimaryWeaponId"), fresh)
    settle()
    assert fresh in registered_weapons(), "注册表里没有的枪要自动注册"

    # ② 同一把枪再来一次：不许重复注册（唯一键 UnitId + WeaponId）
    count = len(page.context.row_ids("SquadWeapons"))
    page._commit(CellRef("SquadMembers", member, "PrimaryWeaponId"), fresh)
    settle()
    assert len(page.context.row_ids("SquadWeapons")) == count

    # ③ 0 = 这个兵没这件武器：不注册
    page._commit(CellRef("SquadMembers", member, "SpecialWeaponId"), 0)
    settle()
    assert len(page.context.row_ids("SquadWeapons")) == count
    screen.deleteLater()


def test_removing_a_member_from_the_squad_page_updates_the_tree(qapp, instance) -> None:
    """班组成员页那个 ✕ 删完，**树上**要少一名。

    `squad_page._remove_member` 走的是 `EditorContext.delete_row` —— 它和
    `remove_rows` / `insert_row` 一样会改"行集合"，同样必须丢 `CardData` 缓存；
    漏了就是用户在树上看到的"删了那一栏还在"（这一次是在班组成员页那条路上）。
    """
    from PySide6.QtWidgets import QTreeWidget

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from conftest import settle

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    page = screen.main_page
    widget = page.tree.findChild(QTreeWidget)
    assert widget is not None

    def walk(item):
        yield item
        for index in range(item.childCount()):
            yield from walk(item.child(index))

    def member_rows() -> int:
        wanted = {f"#{member_id}" for member_id in page.context.row_ids("SquadMembers")}
        total = 0
        for index in range(widget.topLevelItemCount()):
            for item in walk(widget.topLevelItem(index)):
                # 按右边那一列的 `#Id` 认（表名不在名字列里了）。
                if item.text(COL_ID) in wanted:
                    total += 1
        return total

    members = page.context.row_ids("SquadMembers")
    assert len(members) >= 2
    before = member_rows()
    page.context.delete_row("SquadMembers", members[0])
    settle()
    assert member_rows() == before - 1, "删完树要跟着少一名"
    screen.deleteLater()


def test_changing_a_members_weapon_updates_the_tree_note(qapp, instance) -> None:
    """改成员的主武器 -> 树上那一行的说明要**跟着变**。

    这一条查的是同一类坑的第三种写法：`set_value` 改的是**被索引过的列**
    （PrimaryWeaponId），`CardData` 里那张"按值找行"的索引不丢就是旧名字 ——
    树、卡片都照旧值画。前两种（插入 / 删除）已经补过丢缓存，这一种要一起逼出来。
    """
    from PySide6.QtWidgets import QTreeWidget

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef
    from ba_mod_maker.core.edit.scope import RowRef
    from conftest import settle

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    page = screen.main_page
    panel = page.tree
    widget = panel.findChild(QTreeWidget)
    assert widget is not None

    def note_of(member_id: int) -> str:
        for index in range(widget.topLevelItemCount()):
            item = widget.topLevelItem(index)
            stack = [item]
            while stack:
                node = stack.pop()
                stack.extend(node.child(i) for i in range(node.childCount()))
                # ID 在右边那一列（只有 #Id；表名只在 tooltip 里）。
                if node.text(COL_ID) == f"#{member_id}":
                    return node.text(0)
        return ""

    member = page.context.row_ids("SquadMembers")[0]
    page.set_focus(RowRef("SquadMembers", member))
    settle()
    before = note_of(member)
    assert before, "树上应当有这一名成员"

    squad = page.context.data.t("SquadWeapons")
    registered = [squad.i(row, "WeaponId") for row in squad.all()]
    weapons = page.context.data.t("Weapons")
    other = next(
        weapons.i(row, "Id") for row in weapons.all() if weapons.i(row, "Id") not in registered[:1]
    )
    page._commit(CellRef("SquadMembers", member, "PrimaryWeaponId"), other)
    settle()
    after = note_of(member)
    assert after != before, f"改了主武器，树上那一行还是旧说明：{after!r}"
    screen.deleteLater()


def test_adding_a_member_shows_up_in_the_tree_right_away(qapp, instance) -> None:
    """⭐ 用户口径（2026-10-01）：「**为什么我要点两下才能增加成员？**」

    真因：`insert_row` 插完没丢 `CardData` 缓存 -> 树照**旧名单**重画 ——
    点了 ＋ 树上没出现，用户只好再点一下（于是名不加也得加）。
    与"删了不刷新"（§3②）是同一个坑，这次在**插入**那条路上。
    """
    from PySide6.QtWidgets import QTreeWidget

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.relation_tree_panel import TOOL_ADD
    from conftest import settle

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    page = screen.main_page
    panel = page.tree
    widget = panel.findChild(QTreeWidget)
    assert widget is not None

    def walk(item):
        yield item
        for index in range(item.childCount()):
            yield from walk(item.child(index))

    def member_rows() -> int:
        wanted = {f"#{member_id}" for member_id in page.context.row_ids("SquadMembers")}
        total = 0
        for index in range(widget.topLevelItemCount()):
            for item in walk(widget.topLevelItem(index)):
                # 按右边那一列的 `#Id` 认（表名不在名字列里了）。
                if item.text(COL_ID) in wanted:
                    total += 1
        return total

    before = member_rows()
    assert before > 0
    assert panel.click_tool("squad", TOOL_ADD), "组节点那颗 ＋ 加一名成员"
    settle()
    assert member_rows() == before + 1, "加完树要立刻跟着变（不该点第二下）"
    screen.deleteLater()


# --------------------------------------------------------------------------- #
# 第 5 步 · 保存
# --------------------------------------------------------------------------- #


def test_default_save_path_is_next_to_the_dataset(qapp, instance, monkeypatch) -> None:
    """自动保存的默认落点 = **数据源同级目录**下的 BA_Mod.zip（不弹选择框）。

    为什么不问用户：口径是"改了后自动保存"，每改一格弹一次选择框比不保存还烦。
    也不写游戏目录（AGENTS：除明确要求不写游戏目录）—— 拷进游戏那一步由用户自己做。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.unit_editor_screen import SAVE_DIR_ENV

    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    screen = UnitEditorScreen()
    screen.set_instance(instance)
    assert screen.default_save_path() == instance.directory.parent / "BA_Mod.zip"
    # 环境变量能覆盖它（用例就是靠这条把落点指到临时目录的）
    monkeypatch.setenv(SAVE_DIR_ENV, str(instance.directory.parent / "临时"))
    assert screen.default_save_path().parent.name == "临时"
    screen.deleteLater()


def test_editing_only_touches_the_cache_until_you_press_save(window, tmp_path) -> None:
    """⭐ 用户口径（2026-09-29）：**改动只进缓存，点「💾 保存」才写进数据库**。

    原话：「打开一个数据库，实际上是先将数据库放进缓存里，然后在缓存上改，
    改的时候改哪个栏就保存哪个栏，而不是一改就全保存，不然用起来就比较卡，
    然后加一个保存按钮，将缓存里的文件保存进实际的数据库。」

    为什么改：每次改一格都全量写一遍包（实测 27ms）+ 全库校验（26ms），打字发涩。
    仍然守住那两条：成功**写状态栏**（不弹窗）；**未动的表逐字节没变**。
    """
    import json

    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    editor = window._unit_editor
    target = tmp_path / "BA_Mod.zip"
    editor.set_save_path(target)

    seen: list[str] = []
    editor.status_message.connect(seen.append)

    editor.save()  # 没有改动 -> 不写盘、也不该弹窗
    assert seen == ["没有改动，未写盘"]
    assert not target.exists()

    editor.apply_cell(CellRef("Units", BTR82A, "Cost"), 60)
    assert not target.exists(), "改一格就写盘了 —— 那不叫缓存（用户口径：不要一改就全保存）"
    assert editor.pending_count == 1, "改动要在缓存里记着"

    editor.save()
    assert target.is_file(), "点了保存却没落盘"
    assert "已写回" in seen[-1] or "已保存" in seen[-1], seen
    assert editor.pending_count == 0, "写盘之后缓存该清空（commit）"
    with zipfile.ZipFile(target) as archive:
        # ❗ 25：24 张表 ＋ **别名那个飞行预设条目**（`PlaneFlyPreset.json` —— 游戏按
        #    typeof(T).Name 找表，FlyPresets 的真名是单数；漏了它整座军械库会空，
        #    实测见 kb/12 §1.4 与交接 §19）。这条断言从前写 24，当时漏改。
        assert len(archive.namelist()) == 25
        rows = json.loads(archive.read("Units.json").decode("utf-8"))
        assert next(row["Cost"] for row in rows if row["Id"] == BTR82A) == 60
        # 未动的表逐字节等于源文件
        source = (editor.context.instance.directory / "Armors.json").read_bytes()
        assert archive.read("Armors.json") == source

    # 第二次改动同样只进缓存；再点保存才动盘
    editor.apply_cell(CellRef("Units", BTR82A, "Cost"), 61)
    with zipfile.ZipFile(target) as archive:
        rows = json.loads(archive.read("Units.json").decode("utf-8"))
    assert next(row["Cost"] for row in rows if row["Id"] == BTR82A) == 60, (
        "没点保存，盘上还是上一次的值"
    )
    editor.save()
    with zipfile.ZipFile(target) as archive:
        rows = json.loads(archive.read("Units.json").decode("utf-8"))
    assert next(row["Cost"] for row in rows if row["Id"] == BTR82A) == 61


def test_leaving_keeps_the_cached_changes(window, tmp_path) -> None:
    """「返回军械库」不还原、也不落盘 —— 改动还**在缓存里**。

    新口径下这三件事是分开的：改（进缓存）/ 保存（写盘）/ 撤销（Ctrl+Z）。
    「离开即还原」是自动保存之前的老规则，早就不在了。
    """
    import json

    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    editor = window._unit_editor
    target = tmp_path / "BA_Mod.zip"
    editor.set_save_path(target)
    cell = editor.context.cell(CellRef("Units", BTR82A, "Cost"))
    editor.apply_cell(cell.ref, 999)
    assert editor.pending_count == 1, "改动该在缓存里"

    editor.back_requested.emit()
    assert "尚未落盘" in editor.leave(), "还有没写盘的改动，离开时要说一句"
    _double_click(window, BTR82A)
    kept = editor.context.cell(CellRef("Units", BTR82A, "Cost"))
    assert kept.raw == 999, "缓存里的改动不该被还原"
    assert not target.exists(), "没点保存，盘上不该有"

    editor.save()
    with zipfile.ZipFile(target) as archive:
        rows = json.loads(archive.read("Units.json").decode("utf-8"))
    assert next(row["Cost"] for row in rows if row["Id"] == BTR82A) == 999


def test_saved_changes_are_kept_when_leaving(window, tmp_path) -> None:
    """保存之后再返回：**不能把刚保存的改动也撤回去**（内存与磁盘必须一致）。

    这一条是两个动作的分工：保存成功 -> Changeset.commit()（认下来）；
    没保存就离开 -> revert_all()（放弃）。
    """
    import json
    import zipfile

    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    editor = window._unit_editor
    editor.set_save_path(tmp_path / "BA_Mod.zip")
    editor.apply_cell(CellRef("Units", BTR82A, "Cost"), 61)
    editor.save()
    assert editor.changeset.is_empty, "保存成功后条目该清空"

    editor.save()  # 再点一次：应当是"没有改动"，不是把同一批再写一遍
    editor.back_requested.emit()
    with zipfile.ZipFile(tmp_path / "BA_Mod.zip") as archive:
        rows = json.loads(archive.read("Units.json").decode("utf-8"))
    assert next(row["Cost"] for row in rows if row["Id"] == BTR82A) == 61
    _double_click(window, BTR82A)
    assert editor.context.cell(CellRef("Units", BTR82A, "Cost")).raw == 61, (
        "保存过的改动被「返回」撤回去了 —— 内存与磁盘不一致"
    )


# --------------------------------------------------------------------------- #
# 跳到「单位」= 整屏跳过去（用户口径 2026-10-01）
#
# 用户原话：「我希望跳转到单位的时候，整体都跳转过去」——
# 从前所有跳转都只 set_focus（只换中栏），左树 / 右卡 / 面包屑 / 撤销栈
# 全停在原来那个单位。现在目标是 Units 行时走 open_unit。
# --------------------------------------------------------------------------- #


def test_jumping_to_a_units_row_moves_the_whole_screen(window) -> None:
    """⭐ 跳到一个 Units 行 -> 整屏换过去：单位 / 关系树 / 卡片 / 面包屑一起换。

    这条路就是中栏那一行的 ↗（外键指向另一个单位）：entries.link_activated ->
    EditorPage.open_row_requested -> MainEditorPage._open_row -> unit_requested。
    """
    from ba_mod_maker.core.edit.foreign import display_name
    from ba_mod_maker.core.edit.scope import RowRef

    _double_click(window, BTR82A)
    screen = window._unit_editor
    page = screen.main_page
    instance = screen.context.instance
    target_name = display_name(instance, "Units", INFANTRY)

    assert screen.current_unit_id == BTR82A
    assert screen.trail()[1] != target_name, "前置条件：一开始不在目标单位上"

    page.open_row_requested.emit("Units", INFANTRY)  # 就是点那个 ↗

    assert screen.current_unit_id == INFANTRY, "跳转没有整体跳过去（单位没换）"
    assert screen.main_page.tree.tree.unit_id == INFANTRY, "左树还停在原来那个单位"
    assert screen.card_panel.card.unit_id == INFANTRY, "右卡还停在原来那个单位"
    assert screen.trail()[1] == target_name, f"面包屑还写着 {screen.trail()[1]}"
    assert page.focus == RowRef("Units", INFANTRY), "中栏没停在那个单位那一行"


def test_jumping_to_another_table_only_moves_the_middle_column(window) -> None:
    """非 Units 的跳转保持现状：只换中栏 —— 单位 / 撤销栈都不许动。"""
    from ba_mod_maker.core.edit.scope import RowRef

    _double_click(window, BTR82A)
    screen = window._unit_editor
    page = screen.main_page
    target = next(row for row in screen.context.scope.rows if row.table != "Units")
    undo_before = screen.context.undo_depth
    unit_before = screen.current_unit_id

    page.open_row_requested.emit(target.table, target.row_id)

    assert screen.current_unit_id == unit_before, f"看一行 {target.table} 不该换单位"
    assert page.focus == RowRef(target.table, target.row_id), "中栏没换成那一行"
    assert screen.context.undo_depth == undo_before, "撤销栈被重建了（不该）"


def test_jumping_to_another_unit_keeps_the_cached_edits(window) -> None:
    """切单位**不许丢缓存里的改动**（口径：退出编辑器不等于放弃）。"""
    from ba_mod_maker.core.edit.refs import CellRef

    _double_click(window, BTR82A)
    screen = window._unit_editor
    before_count = screen.session.change_count
    ref = CellRef("Units", BTR82A, "Cost")
    before = int(screen.context.cell(ref).raw)
    screen.apply_cell(ref, before + 3)
    count = screen.session.change_count
    assert count == before_count + 1, "改一格该进账本"

    screen.main_page.open_row_requested.emit("Units", INFANTRY)

    assert screen.current_unit_id == INFANTRY
    assert screen.session.change_count == count, "跳转把缓存里的改动弄丢了"


def test_jumping_to_a_missing_unit_stays_put_and_says_so(window) -> None:
    """库里没有那个 Id：状态栏一句人话、原地不动 —— 不白屏、不抛异常。"""
    _double_click(window, BTR82A)
    screen = window._unit_editor
    missing = 999_999
    assert not screen._unit_exists(missing)

    screen.main_page.open_row_requested.emit("Units", missing)

    assert screen.current_unit_id == BTR82A, "跳不过去就该原地不动"
    assert str(missing) in window._status_label.text(), (
        f"状态栏没说明白为什么没跳：{window._status_label.text()!r}"
    )
    assert screen.main_page.focus is not None


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _double_click(window, unit_id: int) -> None:
    """双击 = 卡片发 card_activated（unit_card.py::mouseDoubleClickEvent 那条路）。"""
    for row in window._armory.row_widgets:
        widget = row.card_widget(unit_id)
        if widget is not None:
            row.card_activated.emit(unit_id)
            return
    raise AssertionError(f"卡墙上没有单位 {unit_id}")


def _health_ref(screen):
    """卡片上「生命值」那一格的落点（跟着选项覆盖走）。"""
    card = screen.card_panel.card
    assert card is not None
    return next(stat.value_ref for stat in card.stats if stat.key == "health")


def _stat_text(card_panel, key: str) -> str:
    card = card_panel.card
    assert card is not None
    return next(stat.value for stat in card.stats if stat.key == key)


def _cells(window, table: str):
    return [cell for cell in window._unit_editor.context.cells if cell.table == table]


def _armor_health(instance, unit_id: int) -> int:
    """这个单位当前的「生命值」（跟着选项覆盖走的那条链）。"""
    from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card

    card = build_unit_card(CardData(instance), unit_id)
    assert card is not None
    return int(next(stat.value for stat in card.stats if stat.key == "health"))


# --------------------------------------------------------------------------- #
# Ctrl+Z / Ctrl+Y：撤销要能重做（用户口径）
# --------------------------------------------------------------------------- #


def test_ctrl_z_then_ctrl_y_puts_the_value_back(qapp, instance) -> None:
    """改一格 -> 撤销 -> **重做**（值回来）；再改一格 -> 重做栈清空、Ctrl+Y 无效。"""
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    context = screen.context
    ref = CellRef("Units", BTR82A, "Cost")
    before = int(context.cell(ref).raw)

    screen.apply_cell(ref, before + 7)
    assert int(context.cell(ref).raw) == before + 7
    assert not context.can_redo, "还没撤销过，重做栈就该是空的"

    screen.undo()
    assert int(context.cell(ref).raw) == before, "Ctrl+Z 没把值撤回去"
    assert context.can_redo, "撤销之后应当有东西可以重做"

    screen.redo()
    assert int(context.cell(ref).raw) == before + 7, "Ctrl+Y 没把值重做回来"
    assert not context.can_redo, "重做完了重做栈该空"

    # 再撤销，然后**改一格**：新的写操作作废重做栈（否则 Ctrl+Y 会把刚改的顶掉）
    screen.undo()
    assert context.can_redo
    screen.apply_cell(ref, before + 9)
    assert not context.can_redo, "新的写操作之后还留着可重做的记录"
    assert context.redo_last() == "没有可重做的改动"
    screen.deleteLater()


def test_ctrl_y_replays_a_deleted_row_and_takes_it_away_again(qapp, instance) -> None:
    """行级改动同样要能重做：删一行 -> 撤销放回 -> 重做再拿掉 -> 撤销又回来。"""
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.pages.squad_page import SquadPage

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(INFANTRY)
    screen.open_squad_page()
    page = screen.current_page
    assert isinstance(page, SquadPage)
    victim = page.member_ids[-1]

    page._remove_member(victim)
    assert victim not in page.member_ids

    screen.undo()
    assert victim in page.member_ids, "撤销没把那一行放回来"
    screen.redo()
    assert victim not in page.member_ids, "重做没把那一行再拿掉"
    screen.undo()
    assert victim in page.member_ids, "再撤销一次该又回来"
    screen.deleteLater()


def test_the_editor_owns_ctrl_y_and_no_longer_owns_ctrl_s(qapp, instance) -> None:
    """快捷键清单：Ctrl+Z / Ctrl+Y 在编辑器里；**Ctrl+S 不在**（归顶栏那一个 QAction）。

    同一个键两条绑定是歧义（按下触发哪一条看谁先抢到）。保存只有一条通道：
    main_window 的「💾 保存」QAction -> EditSession.save()（AGENTS §4.5）。
    """
    from PySide6.QtGui import QShortcut

    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    screen = UnitEditorScreen()
    keys = [shortcut.key().toString() for shortcut in screen.findChildren(QShortcut)]
    assert "Ctrl+Z" in keys, keys
    assert "Ctrl+Y" in keys, f"没有 Ctrl+Y —— 撤销能撤不能重做：{keys}"
    assert "Ctrl+S" not in keys, f"编辑器还占着 Ctrl+S（与顶栏保存 QAction 同键）：{keys}"
    screen.deleteLater()


def test_the_undo_and_redo_stacks_are_capped_at_hundred() -> None:
    """栈上限 100（用户口径），而且**这个数只有一份**（Changeset 里那个常量）。"""
    from ba_mod_maker.app.screens.editor.pages import base as pages_base
    from ba_mod_maker.core.edit.changeset import HISTORY_LIMIT

    assert HISTORY_LIMIT == 100
    stack: list = [("change", None)] * (HISTORY_LIMIT + 5)
    pages_base._trim(stack)
    assert len(stack) == HISTORY_LIMIT, "超上限之后没有丢最旧的那一条"


# --------------------------------------------------------------------------- #
# 外键那一格：打字即服务端筛（用户报的真 bug，2026-09-30）
# --------------------------------------------------------------------------- #


def test_typing_latin_2a72_in_the_foreign_combo_resolves(qapp, instance) -> None:
    """⭐ 用户报的：改武器那一格，打 `2A72` 搜不出来、打 `72` 反而能搜出来。

    真因：`Weapons.HUDName` 里那个 «А» 是**西里尔 А（U+0410）**，
    而 QCompleter 的本地过滤只认显示名 —— 拉丁 A 永远匹配不上。
    现在外键下拉**打字即服务端筛**（同时搜 Name 与 HUDName、两边都折同形字），
    候选文本带 `#Id`（5 行同名时人眼才分得清），而 `value()` **不猜**：
    只有"精确 / 唯一候选"才落值，多条同名一律报错让人从下拉里挑。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.core.edit.refs import CellRef
    from ba_mod_maker.core.edit.scope import RowRef
    from ba_mod_maker.core.errors import TableError

    want = {2, 270, 294, 381, 382}
    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    page = screen.main_page
    # 中栏换成 BTR-82A 那门 2А72 所在的行：TurretWeapons#838 的 WeaponId。
    page.set_focus(RowRef("TurretWeapons", 838))
    cell = screen.context.cell(CellRef("TurretWeapons", 838, "WeaponId"))
    assert cell is not None and cell.foreign == "Weapons", cell
    page._open_inline_editor(cell)
    editor = page.entries.open_row.editor
    assert editor is not None
    combo = editor.findChild(QComboBox)
    assert combo is not None and combo.isEditable()
    ref = cell.ref
    before = screen.context.cell(ref).raw
    assert before == 2

    def typed(text: str) -> None:
        """像用户那样**全选再打**（打开时框里已经写着当前那个名字）。"""
        combo.lineEdit().selectAll()
        QTest.keyClicks(combo.lineEdit(), text)
        qapp.processEvents()

    # ① 打 2A72 -> 正是那 5 条，而且**每条文本都不一样**（末尾带 #Id）。
    typed("2A72")
    assert combo.count() == 5, f"打字没有去服务端筛（候选 {combo.count()} 条）"
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert {combo.itemData(i) for i in range(combo.count())} == want
    assert len(set(texts)) == 5, texts
    assert all("  #" in text for text in texts), texts
    assert all(text.endswith(f"#{combo.itemData(i)}") for i, text in enumerate(texts)), texts
    assert all(isinstance(combo.itemData(i), int) for i in range(combo.count()))

    # ①b 只有折叠能搜到的词（拉丁 `2A72 30mm`：Name 里没有 " 30mm" 那一截）。
    #     不折的话候选会是 0 条 —— 同形字折叠的变异验证就靠这一句。
    typed("2A72 30mm")
    assert combo.count() == 5, f"同形字没折？（候选 {combo.count()} 条）"

    # ② 从下拉里挑一条 = 明确意图 -> value() 就是那一条的 Id。
    combo.blockSignals(True)  # 别让 currentIndexChanged 当场写库（数据集是模块共享的）
    combo.setCurrentIndex(2)
    combo.blockSignals(False)
    picked = combo.itemData(2)
    assert picked in want
    assert editor.value() == picked

    # ③ 打一个**唯一**命中的词 -> 不用挑也能解析（Gadyuka 2 只有 Weapons#382）。
    typed("Gadyuka 2")
    assert combo.count() == 1, [combo.itemText(i) for i in range(combo.count())]
    assert editor.value() == 382

    # ④ 多条的词直接回车 -> 抛错，而且**一个字节都不许写进去**（不替用户挑第一条）。
    typed("2A72")
    with pytest.raises(TableError) as excinfo:
        editor.value()
    message = str(excinfo.value)
    assert "匹配到" in message and "5" in message, message
    QTest.keyClick(combo.lineEdit(), Qt.Key.Key_Return)  # 真回车：_commit 吞错、只画红字
    qapp.processEvents()
    assert screen.context.cell(ref).raw == before == 2, "多条同名时不许替用户挑一条写进去"
    screen.deleteLater()


# --------------------------------------------------------------------------- #
# 「↩ 回上个单位」（用户口径 2026-10-01：「加一条回上个单位吧」）
#
# 背景：上一轮做了「跳到 Units 行 = 整屏跳过去」，跳过去之后按 ← 直接回军械库，
# 回不到刚才那个单位。规则：
#   跳转到**另一个单位**时把刚才那个压栈（≤20）；从军械库新进一个单位时清空；
#   **回退不压栈**（否则两个单位之间来回弹）；回退不丢缓存里的改动。
# --------------------------------------------------------------------------- #

#: 跳转链上用的四个真实单位。
UNIT_A = BTR82A  # 1
UNIT_B = INFANTRY  # 645
UNIT_C = 21  # Stryker RV
UNIT_D = 76  # Rangers Snipers（用户截图里那个替换目标）


def _jump(window, unit_id: int) -> None:
    """走"中栏那一行的 ↗"那条路跳到另一个单位（就是用户在截图上点的那一下）。"""
    window._unit_editor.main_page.open_row_requested.emit("Units", unit_id)


def test_unit_history_pops_back_one_level_at_a_time(window) -> None:
    """A→B→C：previous 依次是 B、A；回到底 can_return 为 False（且**不来回弹**）。"""
    window._open_unit_editor(UNIT_A)
    screen = window._unit_editor
    assert screen.history_depth() == 0, "刚进编辑器不该有历史"

    _jump(window, UNIT_B)
    _jump(window, UNIT_C)
    assert screen.current_unit_id == UNIT_C
    assert screen.history_depth() == 2
    assert screen.previous_unit_id() == UNIT_B, "上一个该是刚离开的 B"

    screen.return_to_previous_unit()
    assert screen.current_unit_id == UNIT_B, "没回到 B"
    assert screen.previous_unit_id() == UNIT_A

    screen.return_to_previous_unit()
    assert screen.current_unit_id == UNIT_A, "没回到 A"
    assert screen.can_return_to_previous_unit() is False
    assert screen.previous_unit_id() is None
    assert screen.history_depth() == 0
    # 回退不压栈：在 A 上再回一次，还是"没有上个单位可回了"（A 与 B 不来回弹）
    assert screen.return_to_previous_unit() == "没有上个单位可回了"
    assert screen.current_unit_id == UNIT_A


def test_jumping_again_after_a_return_pushes_the_unit_you_are_on(window) -> None:
    """回退后再跳一次 D：压的是**当前这个**（B），不是被消费掉的旧链 —— 钉死语义。"""
    window._open_unit_editor(UNIT_A)
    screen = window._unit_editor
    _jump(window, UNIT_B)
    _jump(window, UNIT_C)  # 历史 [A, B]
    screen.return_to_previous_unit()  # -> B，历史 [A]
    assert screen.current_unit_id == UNIT_B
    assert screen.history_depth() == 1

    _jump(window, UNIT_D)  # 压的是 B
    assert screen.previous_unit_id() == UNIT_B, "压错了：不是刚离开的那个单位"
    assert screen.history_depth() == 2

    screen.return_to_previous_unit()
    assert screen.current_unit_id == UNIT_B
    screen.return_to_previous_unit()
    assert screen.current_unit_id == UNIT_A
    assert screen.can_return_to_previous_unit() is False


def test_entering_from_the_armory_clears_the_unit_history(window) -> None:
    """从军械库新进一个单位 -> 历史清空（上一次那条链不许串过来）。"""
    window._open_unit_editor(UNIT_A)
    screen = window._unit_editor
    _jump(window, UNIT_B)
    _jump(window, UNIT_C)
    assert screen.history_depth() == 2

    window._open_unit_editor(UNIT_A)  # 从军械库双击进来

    assert screen.current_unit_id == UNIT_A
    assert screen.history_depth() == 0, "从军械库进来时历史该清空"
    assert screen.can_return_to_previous_unit() is False


def test_returning_keeps_the_cached_edits(window) -> None:
    """回退**不许丢**缓存里的改动（口径：退出 / 换单位都不等于放弃）。"""
    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(UNIT_A)
    screen = window._unit_editor
    ref = CellRef("Units", UNIT_A, "Cost")
    before = int(screen.context.cell(ref).raw)
    screen.apply_cell(ref, before + 3)
    count = screen.session.change_count

    _jump(window, UNIT_B)
    screen.return_to_previous_unit()

    assert screen.current_unit_id == UNIT_A
    assert screen.session.change_count == count, "回退把缓存里的改动弄丢了"


def test_the_back_unit_button_follows_the_history_and_the_page(window) -> None:
    """那颗按钮：不在编辑器里 / 历史空 -> 藏着；有历史 -> 露出来 + tooltip 写清回哪个。"""
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY

    button = window._back_unit_button
    window._open_unit_editor(UNIT_A)
    assert button.isHidden(), "刚进来没有历史，按钮不该露出来"

    _jump(window, UNIT_B)
    assert not button.isHidden(), "有历史了，按钮该露出来"
    assert f"#{UNIT_A}" in button.toolTip(), f"tooltip 没写清回哪个单位：{button.toolTip()!r}"
    assert window._unit_editor.unit_label(UNIT_A) in button.toolTip()

    button.click()  # 点它 = 真的回去
    assert window._unit_editor.current_unit_id == UNIT_A
    assert button.isHidden(), "回到底之后按钮该收起来"

    _jump(window, UNIT_B)
    window.show_page(PAGE_ARMORY)  # 离开编辑器
    assert button.isHidden(), "不在编辑器里就不该露出这颗按钮"


def test_escape_goes_to_the_previous_unit_before_the_armory(window) -> None:
    """← / Esc 也走它：焦点已在本单位那一行 + 历史非空 -> 先回上一个单位。"""
    window._open_unit_editor(UNIT_A)
    screen = window._unit_editor
    _jump(window, UNIT_B)

    back_calls: list[int] = []
    screen.back_requested.connect(lambda: back_calls.append(1))

    screen.go_back()  # ＝ 顶栏那个 ← / Esc

    assert screen.current_unit_id == UNIT_A, "← 没先回上一个单位"
    assert back_calls == [], "还有历史时不该直接回军械库"

    screen.go_back()  # 历史空了 -> 这才回军械库
    assert back_calls == [1], "历史空了该回军械库"


def test_the_unit_history_is_capped_at_twenty(window) -> None:
    """历史最多 20 层（任务口径），超了丢**最旧**那一层。"""
    from ba_mod_maker.app.screens.editor.unit_editor_screen import UNIT_HISTORY_LIMIT

    window._open_unit_editor(UNIT_A)
    screen = window._unit_editor
    for unit_id in range(1, UNIT_HISTORY_LIMIT + 6):
        screen._remember_unit(unit_id)

    assert UNIT_HISTORY_LIMIT == 20
    assert screen.history_depth() == UNIT_HISTORY_LIMIT
    assert screen.previous_unit_id() == UNIT_HISTORY_LIMIT + 5, "留下的该是最新的那一层"
