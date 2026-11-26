"""界面重做（2026-09-30 定稿）的验收用例。

用户口径来自 docs/设计-界面重做-定稿.md。这一份只测**新定的那几条**，
老行为由各自的模块用例守着（test_unit_editor / test_armory_screen / …）。

本轮落地的：

    块 2  顶栏压成一行 36px；菜单收进 ☰；五个页签；面包屑；保存胶囊（没有保存按钮）
    块 1  单位词条 = Units 那一行的 31 条，一条一行
    块 4  点那一行 -> 中栏换成那一行；面包屑父级可点；「⬅ 回上一层」
           （本轮入口从"卡上点一下"换成"树上点一行"：卡片整块只读、不再是导航面）
    块 3  国家页 / 专精页（两个新页）
    行样式  序号 │ 中文名 │ 值 │ 英文列名
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget

from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 设计稿的示例单位：Stryker RV（Id 21）。
STRYKER = 21


# 夹具（qapp / instance / window）统一在 tests/conftest.py：
#   window = **一个模块一个**主窗口，每个用例只把一份干净数据接回去
#   （为什么、怎么保证隔离，见 conftest 的模块 docstring）。


def _open_unit(window, unit_id: int = STRYKER) -> None:
    """进编辑器并**切回关系树那一页**（本模块守的就是它，不是编成台）。

    ❗ 2026-10-02：三种形态现在都**默认落在编成台**（`land_on_bench`，
    见 tests/test_bench_page.py 里那两条），而本模块从"行样式 / 面包屑 / 中栏换行 /
    图标刷新"到"没订阅变更集"全部讲的是**三栏那一页** —— 落在编成台上时
    面包屑是 3 段（…编成台）、`main_page` 也不在台上。

    所以这里显式切回去，**不是绕过 bug**：切页正是用户要看那张卡 / 那一行时做的事
    （同一个做法见 tests/test_turret_chain_ui.py::_open）。
    """
    window._open_unit_editor(unit_id)
    window._unit_editor.show_main()


def _emoji_of(table: str) -> str:
    """某张表的**兜底记号** —— 只从登记表取（entries_panel.table_emoji），不写死 emoji。

    ❗ 记号现在画在**图标位**上（有真图就画真图），不再写进标题文字里。
    """
    from ba_mod_maker.app.screens.editor.entries_panel import table_emoji

    return table_emoji(table)


def _title_of(table: str) -> str:
    """某张表的中文名（副标题那一段用；entries_panel.table_title 是唯一出处）。"""
    from ba_mod_maker.app.screens.editor.entries_panel import table_title

    return table_title(table)


def _click_tree_row(page, group: str, index: int = 0):
    """⭐ 本轮的入口：**点树上的那一行**（旧入口是"卡上点一下"）。

    返回被点开的模型节点；中栏这时就该显示它的全部列。
    """
    node = page.tree.tree.group(group).children[index]
    assert page.tree.activate_key(node.key), node.key
    return node


# --------------------------------------------------------------------------- #
# 块 2 · 顶栏：一行 36px
# --------------------------------------------------------------------------- #


def test_topbar_is_one_row_with_five_tabs(window) -> None:
    from ba_mod_maker.app.shell.main_window import NAV_PAGES
    from ba_mod_maker.app.theme.tokens import TOKENS

    bar = window._breadcrumb.parentWidget()
    assert bar.property("role") == "topbar"
    assert bar.height() == TOKENS.size.topbar_height == 36, "顶栏要是一行 36px"
    assert [b.text() for b in window._nav_buttons.values()] == list(NAV_PAGES)


def test_the_save_button_is_wired_to_the_write_path(window) -> None:
    """按钮点下去要真的走到写盘那条路（不是接了空槽）。

    测试的数据源是内存里拼的（没有"打开的那个文件"），所以这里能观察到的是
    那条路给出的解释语 —— 状态栏说"无法写回"就证明点击确实到了写盘入口。
    """
    window._save_button.setEnabled(True)  # 绕过"没改动就灭着"
    window._save_button.click()
    assert "无法写回" in window._status_label.text(), window._status_label.text()


def test_the_save_button_is_the_only_way_to_write(window) -> None:
    """⭐ 用户口径（2026-09-29）：**加一个保存按钮**，把缓存里的改动写进数据库。

    （旧口径是"删掉保存按钮、改一格自动写盘"—— 用户已经推翻：那样每改一格都要
     全量写一遍包 + 全库校验，改起来卡。现在按键只动内存，落盘是一个显式动作。）
    """
    from PySide6.QtWidgets import QPushButton

    texts = [b.text() for b in window.findChildren(QPushButton)]
    assert any("保存" in text for text in texts), f"顶栏没有保存按钮：{texts}"
    assert window._save_pill.property("role") == "save-pill"
    assert not window._save_button.isEnabled(), "没改动时保存按钮该是灭的"


def test_the_menu_bar_no_longer_takes_a_row(window) -> None:
    """三条菜单收进 ☰ —— 它自己那一行（约 25px）不再占屏。"""
    assert window.menuBar().isVisible() is False
    titles = [a.text() for a in window.menu().actions()]
    assert titles == ["📂 文件", "✏ 编辑", "👁 视图"]


def test_the_editor_has_no_header_row_of_its_own(window) -> None:
    """编辑器那一条「返回 / 面包屑 / 保存」也取消了 —— 全归共用的顶栏。"""
    _open_unit(window)
    bars = [
        w
        for w in window._unit_editor.findChildren(QWidget)
        if w.property("role") == "editor-header"
    ]
    assert bars == [], "编辑器不该再自带一条顶栏"


# --------------------------------------------------------------------------- #
# 块 1 + 块 4 · 右栏 = 刚点的那一行；面包屑
# --------------------------------------------------------------------------- #


def test_entering_the_editor_shows_the_units_row(window) -> None:
    _open_unit(window)
    page = window._unit_editor.main_page
    assert len(page.entries.rows) == 31
    assert page.entries.subtitle_text.startswith(f"{_title_of('Units')} Units#{STRYKER} · 31 列")
    # 记号在**图标位**上（有真图画真图、没真图才是 emoji），不在文字里。
    assert page.entries.title_mark is not None
    assert page.entries.title_mark.emoji == _emoji_of("Units")
    assert window._unit_editor.trail() == ("军械库", "Stryker RV", "单位")


def test_clicking_a_tree_row_swaps_the_middle_column(window) -> None:
    """**行为保留、入口换掉**：点一行 = 中栏换成那一行的全部列（不是就地改）。

    旧入口是"卡上点一个数"——本轮卡片整块只读、不再是导航面，入口迁到关系树上。
    """
    _open_unit(window)
    page = window._unit_editor.main_page
    node = _click_tree_row(page, "armor")

    assert page.focus is not None and page.focus.table == node.table
    assert page.entries.subtitle_text.startswith(
        f"{_title_of(node.table)} {node.table}#{node.row_id}"
    )
    trail = window._unit_editor.trail()
    assert trail[0] == "军械库" and trail[1] == "Stryker RV" and len(trail) == 4
    assert page.entries.back_label.startswith("⬅ 回单位")


def test_breadcrumb_goes_back_one_level(window) -> None:
    _open_unit(window)
    page = window._unit_editor.main_page
    _click_tree_row(page, "armor")

    window._unit_editor.go_back()
    assert window._unit_editor.trail() == ("军械库", "Stryker RV", "单位")
    assert len(page.entries.rows) == 31

    # 再回一层 = 出编辑器（军械库）
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY

    window._unit_editor.go_back()
    assert window._tabs.currentIndex() == PAGE_ARMORY


def test_breadcrumb_targets_are_clickable(window) -> None:
    """面包屑的**父级可点**（块 4：回去的路两条都要）。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    _click_tree_row(page, "armor")

    window._crumb_go(1)  # 点「Stryker RV」
    assert window._unit_editor.trail() == ("军械库", "Stryker RV", "单位")

    _click_tree_row(page, "armor")
    window._crumb_go(0)  # 点「军械库」
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY

    assert window._tabs.currentIndex() == PAGE_ARMORY


# --------------------------------------------------------------------------- #
# 行样式：序号 │ 中文名 │ 值 │ 英文列名
# --------------------------------------------------------------------------- #


def test_row_style_has_four_columns(window) -> None:
    from ba_mod_maker.app.widgets.row_entries import EMPTY_TEXT

    _open_unit(window)
    rows = window._unit_editor.main_page.entries.rows
    assert [row.index_text for row in rows] == [str(i) for i in range(1, 32)]
    assert all(row.name_text for row in rows), "中文名那一列不许空"
    assert all(row.column_text for row in rows), "英文列名那一列不许空"
    # 空值写「（空）」（而不是空着 —— 空着看不出"这一列有没有值"）
    empties = [row for row in rows if row.value_text == EMPTY_TEXT]
    assert empties, "样本单位里应当有占位列（空值）"


def test_foreign_value_shows_the_name_and_offers_a_link(window) -> None:
    """外键写名字不写 Id；右边那个 ↗ 是"去看这一行"。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    row = next(r for r in page.entries.rows if r.cell.foreign and r.link_target is not None)
    assert row.value_text != str(row.cell.raw), "外键要显示名字，不是 Id"
    assert row.link_target is not None

    seen: list[tuple[str, int]] = []
    page.entries.link_activated.connect(lambda table, rid: seen.append((table, rid)))
    row._emit_link()
    assert seen == [row.link_target]


# --------------------------------------------------------------------------- #
# 块 3 · 国家页
# --------------------------------------------------------------------------- #


def test_country_page_shows_the_whole_table(window) -> None:
    """Countries 3 行 × 7 列整张铺开；第一列画国旗。"""
    page = window._country
    assert page.row_ids == (1, 2, 3)
    assert page.column_count == 7
    # 表头：每一列的英文列名都在轴上（用例按按钮找得到格子）
    for row_id in page.row_ids:
        for column in ("Id", "Name", "UIName", "MaxPoints", "Hidden"):
            assert page.cell_button(row_id, column) is not None, (row_id, column)
    # 国旗那一列：有图键的至少画了一张（没图键的写「（空）」）
    assert page.flag_label(1) is not None
    assert page.flag_label(3) is not None


def test_country_cell_edit_goes_to_cache_then_the_save_button_writes(window, tmp_path) -> None:
    """国家页走同一条口径：格子里改的值先进缓存，顶栏「💾 保存」才落盘。"""
    from ba_mod_maker.core.edit.refs import CellRef

    window._edit_session.set_save_path(tmp_path / "BA_Mod.zip")
    page = window._country
    button = page.cell_button(1, "MaxPoints")
    assert button is not None
    before = int(button.text())

    page._open_editor(page._cells[(1, "MaxPoints")])
    assert page.editing is not None, "点格子没有就地冒出编辑控件"
    page._commit(CellRef("Countries", 1, "MaxPoints"), before + 1)

    assert window._edit_session.last_saved_path is None, "改一格不该落盘"
    assert window._save_button.isEnabled(), "有改动 -> 保存按钮该亮"
    assert page.cell_button(1, "MaxPoints").text() == str(before + 1)

    window._edit_session.save()
    assert window._edit_session.last_saved_path is not None
    assert window._edit_session.last_saved_path.name == "BA_Mod.zip"
    assert not window._save_button.isEnabled(), "写完之后按钮该灭"


# --------------------------------------------------------------------------- #
# 块 3 · 专精页
# --------------------------------------------------------------------------- #


def test_spec_page_folds_the_branch_matrix(window) -> None:
    """左边 13 行；右边 = 身份 9 条 + 7×2 矩阵 + MaxSlots（合计 24 列）。"""
    from ba_mod_maker.app.screens.reference.spec_page import BRANCHES

    page = window._spec
    assert page._list.count() == 13, "Specializations 整张表 13 行"
    assert page._specs and len(page._specs) == 13
    page.show_row(page._specs[0].specialization_id)

    identity = len(page._list_widget.rows)
    matrix = page._matrix.value_count
    tail = len(page._tail.rows)
    assert identity == 9, f"身份 / 显示是 9 条：{identity}"
    assert matrix == len(BRANCHES) * 2 == 14, "7 个兵种 × (槽位数 + 点数预算)"
    assert tail == 1, "最后是 MaxSlots"
    assert identity + matrix + tail == 24, "24 列一个不多一个不少"
    assert page._title.text(), "抬头要有专精名"


# --------------------------------------------------------------------------- #
# 保存胶囊
# --------------------------------------------------------------------------- #


def test_save_pill_and_button_follow_the_cache(window, tmp_path) -> None:
    """胶囊与保存按钮跟着**缓存里还有几处没落盘**走（不再有"自动保存"）。"""
    from ba_mod_maker.core.edit.refs import CellRef

    _open_unit(window)
    target = tmp_path / "BA_Mod.zip"
    window._unit_editor.set_save_path(target)
    assert not window._save_button.isEnabled(), "刚打开、没改动 -> 灭的"

    window._unit_editor.apply_cell(CellRef("Units", STRYKER, "Cost"), 999)
    pill = window._save_pill
    assert window._save_button.isEnabled(), "有改动 -> 保存按钮该亮"
    assert pill.state == "dirty" and "1 处未保存" in pill.text(), pill.text()
    assert not target.exists(), "还没点保存，盘上不该有"

    # ❗ 这里直接走会话写盘（测试的数据源是内存里拼的，没有"打开的那个文件"，
    #    而顶栏按钮走的是 _save_current —— 那条路要 _source，见下面的接线用例）。
    window._unit_editor.save()
    assert pill.state == "saved" and "已存" in pill.text() and "BA_Mod.zip" in pill.text()
    assert not window._save_button.isEnabled(), "写完之后按钮该灭"


# --------------------------------------------------------------------------- #
# 块 3 · 卡上补出来的三块（机动 / 传感器 / 运输）
# --------------------------------------------------------------------------- #


def test_card_has_the_three_new_blocks(window) -> None:
    """用户原话："补到卡上，每样一块" —— 机动 / 传感器 / 运输 原来卡上没有。"""
    from PySide6.QtWidgets import QLabel

    _open_unit(window)
    card = window._unit_editor.main_page.card.card
    assert card.mobility is not None, "没有机动行？"
    assert card.sensors, "没有传感器行？"
    assert card.transports, "没有运输配对行？"
    titles = [w.text() for w in window._unit_editor.main_page.card.findChildren(QLabel)]
    for title in ("🚗 机动", "📡 传感器", "🚚 运输"):
        assert any(title in text for text in titles), f"卡上没有这一块：{title}"


def test_clicking_mobility_on_the_card_opens_that_row(window) -> None:
    """点卡上的机动 -> 右栏换成 Mobility 那一行的 22 列。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    mobility = page.card.card.mobility
    assert mobility is not None
    page._open_row(mobility.table, mobility.row_id)

    assert page.focus is not None and page.focus.table == "Mobility"
    assert page.entries.subtitle_text.startswith(
        f"{_title_of('Mobility')} Mobility#{mobility.row_id} · 22 列"
    )
    assert len(page.entries.rows) == 22
    # 面包屑尾段是**纯行名**，记号在它自己的图标位上。
    assert window._unit_editor.trail()[-1] == mobility.label
    assert window._unit_editor.trail_marks()[-1].emoji == _emoji_of("Mobility")


def test_clicking_the_armor_row_opens_the_armors_row(window) -> None:
    """**行为保留、入口换掉**：点装甲 -> 中栏去看 Armors 那一行（顶/背/正/侧 + 生命值都在那儿）。

    旧入口是立绘上那张装甲图（整块只读的卡上已经不给点）；现在这一行在树的「🛡 装甲」组里。
    """
    _open_unit(window)
    page = window._unit_editor.main_page
    node = _click_tree_row(page, "armor")
    assert node.table == "Armors"
    assert page.entries.subtitle_text.startswith(
        f"{_title_of('Armors')} Armors#{node.row_id} · 14 列"
    )
    # 卡上那张装甲图还在画（只是不给点）。
    assert page.card.card.armor_zones


def test_editor_card_is_readonly_and_is_not_a_navigator(window) -> None:
    """卡片整块**只读**：卡上零可写入口（改数据只在中间那栏）。

    ❗ 2026-10-01 口径更新（设计稿 §7 的 a：块 4/5/8/10 落地）：卡**不是「不可点」**，
    而是「**只读不给写 + 点一层换中栏**」—— 炮塔四层 / 改装槽那 21 个格子 / 装甲机动那几行
    都能点了，点了中栏换成那一行的全部列（readonly_card 往上发 open_row_requested）。
    本用例的**断言一条没改**（仍然绿）：卡上没有按钮与下拉、`_open_detail` 仍是空操作、
    可写卡那四个信号名一个都不在 —— 它守的是「卡上不给写」，不是「卡上不给点」。
    """
    from PySide6.QtWidgets import QComboBox, QPushButton

    _open_unit(window)
    page = window._unit_editor.main_page
    card = page.card
    assert card.card is not None and card.card.stats, "样本单位应当有属性行"
    # ❗ 只读卡上现在有「卡片大小 100/90/80/70%」那一排（用户 2026-09-29 要的显示开关，
    #    见 readonly_card.py）—— 它是**显示**开关，不写库、不改数据，不算"编辑入口"。
    #    除它之外仍然一颗按钮都不许有（判据与 test_relation_tree.py / test_card_edit.py
    #    里那份 _in_zoom_bar 同一套）。
    scattered = [b.text() for b in card.findChildren(QPushButton) if not _in_zoom_bar(b, card)]
    assert scattered == [], f"只读卡上冒出了「卡片大小」以外的按钮：{scattered}"
    assert card.findChildren(QComboBox) == [], "只读卡上还有下拉"
    # 可写卡那几条"可点"的路一条都不在（点它不会换行、不会写库）。
    for name in ("cell_activated", "row_activated", "swap_requested", "clone_requested"):
        assert not hasattr(card, name), name
    focus_before = page.focus
    page.card._open_detail(0) if hasattr(page.card, "_open_detail") else None
    assert page.focus == focus_before, "点卡片居然把中栏换了"
    assert page.entries.has_editor is False


# --------------------------------------------------------------------------- #
# 实机反馈（2026-09-30 第二轮）：
#   ① 属性栏两端对齐、且**不给点**；② 装甲/机动/传感器/运输 要能改、能换、能克隆
# --------------------------------------------------------------------------- #


def test_stat_chips_are_display_only(window) -> None:
    """用户原话："这些属性还是不要点了就能改的功能吧，一个是不明显，另一个是局限性高。"

    属性那一排是从装甲 / 机动 / 传感器算出来的**汇总**（设计里也写着"不能增删"）——
    想改就去「🛡 装甲 / 🚗 机动 / 📡 传感器」那几块，那几块才是"哪一行"。
    本轮更进一步：**整张卡只读**，连"抬头 / 成本"也不给点了。
    """
    from PySide6.QtWidgets import QPushButton

    _open_unit(window)
    card = window._unit_editor.main_page.card
    assert card.card is not None and card.card.stats
    # 只读卡上连"点击目标表"这个东西都没有（可写卡那套 _targets 已随卡片一起去掉）。
    assert not hasattr(card, "_targets"), "只读卡上还留着点击目标表"
    hosts = [w for w in card.findChildren(QWidget) if w.property("role") == "card-flow"]
    assert hosts, "属性那一排没有用横排布局"
    for host in hosts:
        assert host.findChildren(QPushButton) == [], "属性那一排冒出了可点控件"


def _in_zoom_bar(widget, card) -> bool:
    """这颗控件是不是「卡片大小」那一排（role=card-zoom）里的？

    同一份判据在 test_relation_tree.py / test_card_edit.py 里各有一份
    （copy，不是共享；改一处要同时改另一处）。
    """
    parent = widget.parentWidget()
    while parent is not None and parent is not card:
        if parent.property("role") == "card-zoom":
            return True
        parent = parent.parentWidget()
    return False


def test_stats_row_justifies_when_it_fits_and_wraps_when_narrow(qapp) -> None:
    """属性那一排：**放得下就两端对齐，放不下就折行**。

    口径变过（2026-09-29）：原来是"永远一个 QHBoxLayout + 两端 stretch"，但那一排的
    最小宽 = 各格最小宽之和（实测 374~379），比只读卡的视口还宽十几个像素 —— 于是
    整张卡被顶出面板，底部冒横向滚动条、立绘右侧那两个装甲数字被裁（用户报的
    "右边的卡片显示不全"）。现在换成自摆的 _StatRow：一行塞得下就两端对齐
    （用户："希望是两端对齐，而不是左对齐"），塞不下就按格间距折行。
    """
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QWidget

    from ba_mod_maker.app.screens.armory.detail_panel import _StatRow

    class Chip(QWidget):
        def sizeHint(self) -> QSize:
            return QSize(40, 20)

        def minimumSizeHint(self) -> QSize:
            return QSize(40, 20)

    chips = [Chip() for _ in range(8)]
    row = _StatRow(chips, spacing=6)
    try:
        # ❗ 必须先 show()：Qt 对"从未显示过"的控件不会真的创建它，
        #    这时候 resize() 只改几何、**不派发 resizeEvent** —— 而 _StatRow 的排法
        #    就在 resizeEvent 里（实测：不 show 时 _layout_chips 调用 0 次，
        #    八个格子全停在默认的 640x480 几何上）。
        row.show()
        qapp.processEvents()

        # 放得下（8*40 + 7*6 = 362 <= 400）-> 两端对齐
        row.resize(400, 20)
        assert chips[0].x() == 0, "首格没有贴左"
        assert chips[-1].x() + chips[-1].width() == row.width(), "末格没有贴右"
        assert row.height() == 20, "一行放得下不该折行"

        # 放不下（每行只塞得下 2 格）-> 折行，且没有一格探出宽度
        row.resize(120, 20)
        qapp.processEvents()
        assert row.height() > 20, "放不下时要折行"
        for chip in chips:
            assert chip.x() + chip.width() <= row.width(), "折行后还有格子探出宽度"
    finally:
        row.deleteLater()


def test_swap_points_the_unit_at_another_row(window, qapp) -> None:
    """「换…」= 把本单位指着它的那一格改成另一个模板行。

    ❗ 卡片随页面刷新、排在下一轮事件循环（2026-10-01「不要全量保存」那一轮的契约变化：
    它不再自己订阅变更集 —— 那一次同步回调是 58.9ms 的整块重排，而且在编成台上
    刷的是一页用户看不见的界面，见 UnitEditorScreen._refresh_pages）。
    """
    _open_unit(window)
    page = window._unit_editor.main_page
    armor = page.card.card.armor
    assert armor is not None and armor.link is not None

    table = window._instance.table("Armors")
    id_index = table.column_index("Id")
    old = page.context.cell(armor.link).raw
    other = next(row[id_index] for row in table.rows if row[id_index] != old)

    page._swap_row("Armors", other, armor.link)
    assert page.context.cell(armor.link).raw == other
    qapp.processEvents()  # 卡片刷新排在下一轮（见 docstring）
    assert page.card.card.armor.row_id == other, "卡片没有跟着换过来"


def test_clone_makes_a_private_copy_and_points_at_it(window) -> None:
    """「克隆一份」：复制这一行 -> 本单位指过去 -> 原来那一行还在（别人照用）。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    mobility = page.card.card.mobility
    assert mobility is not None and mobility.link is not None

    table = window._instance.table("Mobility")
    id_index = table.column_index("Id")
    before = len(table.rows)

    page._clone_row("Mobility", mobility.row_id, mobility.link)

    assert len(table.rows) == before + 1, "没有复制出新的一行"
    new_id = page.context.cell(mobility.link).raw
    assert new_id != mobility.row_id
    assert any(row[id_index] == new_id for row in table.rows), "新行不在表里"
    assert any(row[id_index] == mobility.row_id for row in table.rows), (
        "原来那一行被删了？别人还在用它"
    )
    assert window._edit_session.last_saved_path is None, "克隆只进缓存，还没落盘"
    window._unit_editor.save()
    assert window._edit_session.last_saved_path is not None, "点了保存才写盘"


def test_swap_button_opens_the_picker_in_place(window) -> None:
    """「🔁 换一个」**不弹对话框**，在表头 / 树上就地冒出候选列表（用户："我不需要这么多弹窗"）。

    本轮位置：模板行的「换一个」在**中栏词条面板**的抬头（那条线一直通用），
    运输配对那种"面板认不出 link"的行则由**树节点**上的「🔁 换一个…」负责。
    """
    from PySide6.QtWidgets import QApplication

    _open_unit(window)
    page = window._unit_editor.main_page
    node = _click_tree_row(page, "armor")
    panel = page.entries
    assert panel.swap_visible, "这一行没有「换一个」"
    assert panel.picker_open is False

    panel._open_picker()
    picker = panel._picker
    assert picker is not None and picker.isVisibleTo(picker.parentWidget())
    assert picker.count > 0 and picker.table == node.table

    seen: list[int] = []
    panel.swap_requested.connect(seen.append)
    new_id = picker.row_id_at(0)
    panel._on_picked(new_id)
    assert seen == [new_id], "选完没有发「换一个」"
    assert QApplication.activeModalWidget() is None, "「换一个」不该弹对话框"


def test_transport_row_offers_swap_but_not_clone(window) -> None:
    """运输那一行的"模板行"是**另一个单位** —— 可以换运输方，但克隆一个单位没有意义。

    入口换到树上：节点右键那条「🔁 换一个…」的候选来自外键指向的
    SpecializationAvailabilities；⧉ 则**不长**（配对行不给克隆）。
    """
    _open_unit(window)
    page = window._unit_editor.main_page
    tree = page.tree
    transports = tree.tree.group("transports").children
    assert transports, "样本单位应当有运输配对行"
    for node in transports:
        assert node.link is not None, "运输那一行也要能换"
        assert node.can_clone is False
        assert "⧉" not in [b.text() for b in tree.tool_buttons(node.key)]

    seen: list[tuple] = []
    tree.swap_requested.connect(lambda table, row_id, link: seen.append((table, row_id, link)))
    node = transports[0]
    assert tree.request_swap(node.key)
    picker = tree.picker
    assert picker is not None and picker.table == "SpecializationAvailabilities"
    new_id = picker.row_id_at(0)
    picker.picked.emit(new_id)
    assert seen and seen[0][0] == "SpecializationAvailabilities" and seen[0][2] == node.link


def test_inline_editor_fits_inside_its_row(window) -> None:
    """⭐ 实机 bug（用户："这个编辑栏显示有问题"）：浮层里的输入框比**行**还高。

    根因：全局 QSS 给 QLineEdit/QComboBox 写了 min-height: 28px（普通表单用），
    而"一行词条"的行高只有 24px —— 不压的话输入框上下被裁掉，实机看到的是
    "一个只有右边有蓝边的破框"。所以行内的浮层必须被行高夹住。
    """
    from PySide6.QtWidgets import QApplication, QLineEdit

    from ba_mod_maker.app.theme import apply_theme
    from ba_mod_maker.app.widgets.row_entries import ROW_HEIGHT

    # ❗ 必须先把主题装上：那条"浮层里的输入框 min-height: 0"就写在 QSS 里，
    # 不 apply_theme 测的是 Fusion 的默认最小高度（25px），不是真实配置。
    apply_theme(QApplication.instance())

    _open_unit(window)
    page = window._unit_editor.main_page
    row = page.entries.rows[6]
    page._open_inline_editor(row.cell)
    editor = row.editor
    assert editor is not None, "点了值没有冒出编辑控件"
    assert editor.minimumHeight() == editor.maximumHeight() == ROW_HEIGHT - 2, "浮层没有被行高夹住"
    line = editor.findChild(QLineEdit)
    assert line is not None
    assert line.minimumSizeHint().height() <= ROW_HEIGHT - 2, (
        f"输入框最小高度 {line.minimumSizeHint().height()} > 行高 {ROW_HEIGHT}，会被裁掉"
    )


# --------------------------------------------------------------------------- #
# 实机反馈（第三轮）：国家页被拉长；专精图标发虚
# --------------------------------------------------------------------------- #


def test_country_table_is_not_stretched(window, qapp) -> None:
    """⭐ 用户原话："你这个界面，是不是拉得过长了？"

    根因：QGridLayout 把多余高度**平摊给每一行**（每行 stretch 都是 0），
    于是表头、英文列名、三行数据被拉得隔老远。行尾必须留一个"吃剩饭"的空行。
    """
    page = window._country
    page.resize(1500, 800)
    page.show()
    qapp.processEvents()
    grid = page._grid

    assert grid.rowStretch(len(page.row_ids) + 1) == 1, "表格下面没有吃掉多余高度的空行"
    header = grid.cellRect(0, 1).height()
    first = grid.cellRect(1, 1).height()
    assert header < 60, f"表头那一行被拉成 {header}px"
    assert first < 60, f"数据行被拉成 {first}px"
    page.hide()


def test_icons_are_scaled_for_the_screen_ratio(qapp) -> None:
    """⭐ 用户原话："专精的图标不怎么清晰"。

    根因：直接把原图缩到**逻辑**尺寸（22px）贴上去，150% 的屏上 Qt 还要再放大
    1.5 倍 —— 于是发虚。正确做法：缩到**物理**像素（逻辑 x dpr）再设 devicePixelRatio。
    """
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QWidget

    from ba_mod_maker.app.theme.icons import scale_for_widget

    class Screen150(QWidget):
        def devicePixelRatioF(self) -> float:
            return 1.5

    widget = Screen150()
    source = QPixmap(96, 96)
    source.fill()
    out = scale_for_widget(source, QSize(22, 22), widget)

    assert out.devicePixelRatio() == 1.5
    assert out.width() == 33, f"150% 下应当取 33 物理像素：{out.width()}"
    assert out.height() == 33
    widget.deleteLater()


def test_spec_icon_uses_the_physical_pixmap(window, qapp) -> None:
    """页面真的把那张按屏幕缩放取好的图贴到标签上了（不是原始 96x96 也不是逻辑 22）。"""
    from ba_mod_maker.app.shell.main_window import PAGE_SPEC

    window.show_page(PAGE_SPEC)
    qapp.processEvents()
    icon = window._spec._icon
    pixmap = icon.pixmap()
    if pixmap.isNull():
        pytest.skip("本机没有专精图标素材（未提取过 cache/icons）")
    assert not pixmap.isNull()
    assert icon.width() == 22
    ratio = float(window.devicePixelRatioF() or 1.0)
    target = round(22 * ratio)
    # KeepAspectRatio：长边 = 物理像素，另一条边按原图比例（专精图标不是正方形）
    assert max(pixmap.width(), pixmap.height()) == target, (
        f"贴上去的是 {pixmap.width()}x{pixmap.height()}，长边应当按屏幕缩放取 {target}px"
    )
    assert max(pixmap.width(), pixmap.height()) > 22, "还是那张逻辑尺寸的图（会发虚）"
    assert pixmap.devicePixelRatio() == ratio


# --------------------------------------------------------------------------- #
# 实机反馈（第四轮）：能力 / 武器 / 弹药 也要能换 —— 两把操作长在通用词条面板上
# --------------------------------------------------------------------------- #


def _focus_row(page, table: str, row_id: int):
    from ba_mod_maker.core.edit.scope import RowRef

    page.set_focus(RowRef(table, row_id))
    return page.entries


def _ability_link(page):
    cell = next(
        c
        for c in page.context.cells
        if c.table == "UnitAbilities" and c.column == "AbilityId" and c.raw
    )
    return cell


def test_ability_row_offers_swap_and_clone(window) -> None:
    """用户原话："能力，武器，弹药什么的，也要能换"。

    右栏表头那两把操作是**通用的**：任何一行，只要"本单位有一格指着它"就画出来。
    能力那一行指着它的是 UnitAbilities#N.AbilityId。
    """
    _open_unit(window)
    page = window._unit_editor.main_page
    cell = _ability_link(page)

    panel = _focus_row(page, "Abilities", cell.raw)
    assert panel.swap_visible, "能力那一行没有「换…」"
    assert panel.clone_visible, "能力那一行没有「克隆一份」"
    assert page.focus_link() == cell.ref


def test_weapon_and_ammo_rows_offer_swap(window) -> None:
    """武器 / 弹药同理 —— 指着它们的是 WeaponAmmunitions 的 WeaponId / AmmunitionId。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    weapon = page.card.card.weapons[0]

    panel = _focus_row(page, "Weapons", weapon.weapon_id)
    assert panel.swap_visible, "武器那一行没有「换…」"
    link = page.focus_link()
    assert link is not None and link.table == "WeaponAmmunitions"

    ammo_id = weapon.ammo[0].ammo_id if weapon.ammo else None
    if ammo_id is not None:
        panel = _focus_row(page, "Ammunitions", ammo_id)
        assert panel.swap_visible, "弹药那一行没有「换…」"
        link = page.focus_link()
        assert link is not None and link.table == "WeaponAmmunitions"


def test_panel_swap_repoints_the_unit_and_follows(window) -> None:
    """面板上的「换…」：改外键那一格，右栏切到新那一行。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    cell = _ability_link(page)
    panel = _focus_row(page, "Abilities", cell.raw)

    table = window._instance.table("Abilities")
    id_index = table.column_index("Id")
    current = page.context.cell(cell.ref).raw
    other = next(row[id_index] for row in table.rows if row[id_index] != current)

    panel.swap_requested.emit(other)
    assert page.context.cell(cell.ref).raw == other
    assert page.focus is not None and page.focus.row_id == other, "右栏没有切到新那一行"


def test_panel_clone_makes_a_private_ability(window) -> None:
    """面板上的「克隆一份」：复制 Abilities 那一行 -> UnitAbilities 指过去 -> 原行还在。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    cell = _ability_link(page)
    panel = _focus_row(page, "Abilities", cell.raw)

    table = window._instance.table("Abilities")
    id_index = table.column_index("Id")
    before = len(table.rows)
    original = page.context.cell(cell.ref).raw

    panel.clone_requested.emit()

    assert len(table.rows) == before + 1
    new_id = page.context.cell(cell.ref).raw
    assert new_id != original
    assert any(row[id_index] == original for row in table.rows), "原来那一行被删了"
    assert page.focus is not None and page.focus.row_id == new_id, "右栏没切到副本上"


def test_panel_hides_the_actions_when_nothing_points_at_the_row(window) -> None:
    """没有"本单位指着它的那一格"时**不画**这两把操作 —— 不画一个点了没反应的按钮。"""
    _open_unit(window)
    page = window._unit_editor.main_page
    # 随便挑一张本单位不直接引用的表（这里用 Countries —— 它只是外键目标）
    panel = _focus_row(page, "Countries", 1)
    assert page.focus_link() is None
    assert panel.swap_visible is False
    assert panel.clone_visible is False


# --------------------------------------------------------------------------- #
# 诊断日志（用户点名要的："我建议你做个log系统，专门用来诊断产品"）
# --------------------------------------------------------------------------- #


def test_diagnostics_writes_a_log_file(qapp, tmp_path, monkeypatch) -> None:
    """黑匣子：装上之后，事件与失败都会落到一个文件里。"""
    from ba_mod_maker.app.diag import log as diag_log

    monkeypatch.setenv(diag_log.LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(diag_log, "_configured_path", None)
    # ❗ 用完必须把原来的 handler **还回去**：别的用例（与真产品）就靠它写日志，
    #   清掉不还的话后面的用例全都没有文件日志了（踩过）。
    saved = list(diag_log._logger.handlers)
    diag_log._logger.handlers.clear()
    try:
        target = diag_log.setup_logging()
        diag_log.set_context(page="单位编辑器", unit=125, instance="52085")
        diag_log.event("进单位编辑器")
        try:
            raise ValueError("卡片装配失败（样本）")
        except ValueError as exc:
            diag_log.failure("卡片装配失败", exc)

        assert target.is_file(), f"日志没落到文件：{target}"
        text = target.read_text(encoding="utf-8")
        assert "进单位编辑器" in text
        assert "page=单位编辑器" in text and "unit=125" in text
        assert "ValueError: 卡片装配失败（样本）" in text
        assert "Traceback" in text, "失败必须带完整 traceback"
    finally:
        diag_log._logger.handlers[:] = saved
        diag_log.set_context(page=None, unit=None, instance=None)


def test_guard_reports_failure_without_killing_the_caller() -> None:
    """guard：一段代码出问题时记下来、并把 ok 置 False，界面据此画"失败卡"。"""
    from ba_mod_maker.app import diag

    with diag.guard("样本动作") as ok:
        pass
    assert ok["ok"] is True

    with diag.guard("样本动作") as ok:
        raise RuntimeError("炸了")
    assert ok["ok"] is False


def test_the_left_column_never_goes_blank(window, monkeypatch) -> None:
    """⭐ 实机症状：左栏那张卡整块不见了（右栏照常）。

    根因形状：_rebuild_left_only 先把左栏控件摘下来，再调 build_left() ——
    后者一抛异常，控件就再也没被放回去。所以那条路必须**兜住**：
    出错时把原来那个控件放回去，并把 traceback 写进诊断日志。
    """
    from ba_mod_maker.app import diag

    _open_unit(window)
    page = window._unit_editor.main_page
    tree = page.tree
    calls: list[str] = []
    monkeypatch.setattr(diag, "failure", lambda name, exc, **kw: calls.append(name))

    def boom():
        raise RuntimeError("样本：关系树重排炸了")

    monkeypatch.setattr(page, "build_left", boom)
    page._rebuild_left_only()

    assert calls and calls[0] == "左栏重排失败", "失败没有被记进诊断日志"
    assert page._left is tree, "左栏没有把原来那棵树放回去 —— 会变成一块空白"
    assert tree.parent() is not None, "树被摘下来之后没放回去"


def test_the_log_dialog_shows_the_path_and_the_tail(window, qapp) -> None:
    """☰ -> 「📜 日志…」那个窗口：路径 + 尾部内容（用户要能把它发给我们）。"""
    from ba_mod_maker.app.diag import log as diag_log
    from ba_mod_maker.app.diag.ui import LogDialog

    dialog = LogDialog(window)
    diag_log.event("样本事件：日志对话框")
    dialog.reload()
    assert "日志文件：" in dialog._path.text()
    assert "样本事件：日志对话框" in dialog._text.toPlainText()
    dialog.deleteLater()
    del qapp


def test_clicking_a_weapon_on_the_tree_shows_its_row(window) -> None:
    """⭐ 实机反馈（行为保留、入口换掉）："我点击武器的时候，它不会给我转移到武器词条，
    而是打开了详细信息面板，我不需要自动跳转到详细信息面板。"

    本轮卡片整块只读（连"详细模式"那对按钮都不画了），武器那一行在树的
    「🗼 炮塔 › 武器」节点上：点它 -> **中栏换成 Weapons 那一行的全部列**，
    绝不切到"详细信息面板"、也不跳任何页。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import MODE_COMPACT

    _open_unit(window)
    editor = window._unit_editor
    page = editor.main_page
    card = page.card
    assert card.card is not None and card.card.weapons, "样本单位应当有武器"

    assert card.mode == MODE_COMPACT, "默认是简略模式"
    node = next(
        item
        for turret in page.tree.tree.group("turrets").children
        for item in turret.children
        if item.table == "Weapons"
    )
    assert page.tree.activate_key(node.key)
    assert card.mode == MODE_COMPACT, "点了武器不该自动切到详细信息面板"
    assert editor.current_page is page, "点武器不该跳页"
    assert page.focus is not None and page.focus.table == "Weapons"
    columns = len(window._instance.table("Weapons").columns)
    assert page.entries.subtitle_text.startswith(
        f"{_title_of('Weapons')} Weapons#{node.row_id} · {columns} 列"
    )


def test_the_card_comes_back_after_a_detour(window) -> None:
    """⭐ 实机第二次报的"又跳转不回去了 / 看不到左侧的单位卡片"。

    根因（离屏复现，判据是 card.isVisibleTo(page) == False）：
    _rebuild_left_only 先把左栏 hide() + setParent(None)，再 build_left() ——
    主编辑页返回的**就是同一个卡片对象**，而 Qt 里"显式 hide + addWidget 放回去"
    **不会**自动显示回来。于是右栏照常、左栏整块空白。
    触发路径：点武器进武器页 -> 回单位（close_page 里会 refresh）。"""
    from ba_mod_maker.app.screens.editor.pages.weapon_page import WeaponPage

    _open_unit(window)
    editor = window._unit_editor
    page = editor.main_page
    weapon = page.card.card.weapons[0]

    editor.open_weapon_page(weapon.weapon_id)  # 专用页那条路本身留着（不在主屏入口上）
    assert isinstance(editor.current_page, WeaponPage)

    editor.go_back()  # 回单位（这条路上会 _rebuild_left_only）
    assert editor.current_page is page
    assert page._left is page.tree, "左栏不是那棵关系树了"
    assert page.tree.isVisibleTo(page), "树被 hide 之后没显示回来 —— 左栏会变空白"
    assert page.tree.parent() is not None
    assert page.card.isVisibleTo(page), "右栏那张只读卡也没显示回来"
    assert page.card.parent() is not None


# --------------------------------------------------------------------------- #
# 第五轮实机反馈：运输逻辑错了；小弹窗要彻底解决
# --------------------------------------------------------------------------- #


def test_transport_rows_use_the_real_columns(window) -> None:
    """⭐ 用户："这个运输的逻辑是有问题的"。

    根因：TransportAvailabilities **只有 3 列**（Id / SpecializationAvailabilityId /
    UnitId），我上一轮按想当然的名字猜了 TransportUnitId —— rows.i() 对不存在的列
    返回 0，于是每一行都显示成 "Units #0"。列名必须去 docs/数据-表与列清单.md 查。
    """
    from ba_mod_maker.app.shell.main_window import PAGE_ARMORY

    window.show_page(PAGE_ARMORY)
    instance = window._instance
    table = instance.table("TransportAvailabilities")
    assert "TransportUnitId" not in table.columns, "这张表没有运输方那一列"
    assert "SpecializationAvailabilityId" in table.columns

    # 单位 29（LAV-25A1）有两行运输配对
    window._open_unit_editor(29)
    card = window._unit_editor.main_page.card.card
    assert card.transports, "单位 29 应当有运输配对行"
    for row in card.transports:
        assert row.table == "TransportAvailabilities"
        assert row.label.startswith("专精可用性 #"), f"标签还是错的：{row.label}"
        assert "Units #0" not in row.label
        assert row.link is not None and row.link.column == "SpecializationAvailabilityId"


def test_the_stray_window_guard_hides_and_logs(qapp) -> None:
    """⭐ 用户第三次要求："我要求你彻底解决弹小弹窗的问题"。

    成因已钉死：**控件自己还没有父控件的时候被显示**。所以常驻两道保险：
    当场 hide()（用户看不到）+ 调用栈写日志（下一轮照着改源头）。
    """
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.diag.log import StrayWindowGuard

    guard = StrayWindowGuard(qapp)
    guard.hide_strays = True  # 隐藏那道保险（默认关，见类注释）
    qapp.installEventFilter(guard)
    try:
        orphan = QLabel("走火的小窗")  # 故意不给父控件
        orphan.setVisible(True)  # 这一句在实机上就会闪一个 202x79 的窗
        qapp.processEvents()
        assert guard.caught, "守卫没有逮到那个无父控件被显示的控件"
        assert orphan.isVisible() is False, "开了保险就必须当场隐藏"
        assert "QLabel" in guard.caught[0]
        assert "test_ui_redesign.py" in guard.caught[0], "日志里要有调用栈（来源）"
        orphan.deleteLater()
    finally:
        qapp.removeEventFilter(guard)


# --------------------------------------------------------------------------- #
# 第六轮实机反馈 · 中栏词条行（task-1）：
#   悬停 = 整行一条完整高亮，同一时刻只有一行；注释只挂「中文名」
# --------------------------------------------------------------------------- #


def _send_enter(qapp, widget) -> None:
    """Qt 真机上发的就是 QEnterEvent（裸 QEvent 会被 enterEvent 的类型检查顶掉）。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent

    qapp.sendEvent(widget, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))


def _send_leave(qapp, widget) -> None:
    from PySide6.QtCore import QEvent

    qapp.sendEvent(widget, QEvent(QEvent.Type.Leave))


def _lit(rows) -> list[int]:
    """现在亮着（悬停态）的行号。"""
    return [i for i, row in enumerate(rows) if row.property("hover") == "true"]


def _entries(window):
    return window._unit_editor.main_page.entries


def _hover_pixels(widget):
    """把控件 grab() 下来，返回 (宽, 高, 高亮底色像素坐标)。

    高亮底色 = color.bg_hover（不是 bg_selected，两者是不同的令牌）。
    """
    from PySide6.QtGui import QColor

    from ba_mod_maker.app.theme.tokens import TOKENS

    hover = QColor(TOKENS.color.bg_hover)
    image = widget.grab().toImage()
    hits = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y) == hover
    ]
    return image.width(), image.height(), hits


#: 样式表是 **QApplication 级**的：套一次对后面所有用例都有效，重复套只是白花钱。
#: apply_theme 的代价是 O(活着控件数)（实测 ~55us/控件），而本模块是 module 级窗口，
#: 控件上千 —— 见 tests/conftest.py 的 _flush_deferred_deletes（那里记着这条为什么致命）。
_THEME_APPLIED = False


def _theme():
    global _THEME_APPLIED
    from PySide6.QtWidgets import QApplication

    if _THEME_APPLIED:
        return
    from ba_mod_maker.app.theme import apply_theme

    apply_theme(QApplication.instance())
    _THEME_APPLIED = True


def test_hover_lights_exactly_one_row(window, qapp) -> None:
    """⭐ 用户原话："悬浮在修改栏上方的时候，这个会显示多个高亮。"

    根因：高亮原本是**值那一格的 QSS `:hover`** —— Qt 判它读的是
    WA_UnderMouse，那个旗标只有 enter/leave 会清，而列表重建（clear() 走
    detach()：hide + setParent(None)）与就地编辑（hide() 值那一格）都会让
    leave 送不到，旗标留在原地 —— 实机上就是"鼠标只在第 13 行、7 格同时亮"。

    现在悬停态是 EntryRow 自己的一个属性，且**只有一个写入口**
    （EntryList.set_hovered_row：先灭上一行、再点新的）：
    "同一时刻最多一行"是结构保证，不看 Qt 的旗标、也不看 enter/leave 的顺序。
    """
    _theme()
    _open_unit(window)
    panel = _entries(window)
    rows = panel.rows
    assert len(rows) > 10, "样本行太少，这条断言不成立"
    # 清掉"光标恰好落在某一行上"时 Qt 合成的悬停（show() 时会补发一次 Enter）
    panel.entry_list.set_hovered_row(None)
    assert _lit(rows) == [], "一开始不该有任何一行是悬停态"

    _send_enter(qapp, rows[3])
    assert _lit(rows) == [3]
    assert rows[3].is_hovered is True

    _send_enter(qapp, rows[7])
    assert _lit(rows) == [7], "同时只能亮一行"
    assert rows[3].property("hover") == "false", "上一行没有把悬停态交回去"
    assert panel.entry_list.hovered_row is rows[7]

    _send_leave(qapp, rows[7])
    assert _lit(rows) == [], "鼠标离开后一行都不该亮"
    assert panel.entry_list.hovered_row is None


def test_hover_survives_out_of_order_enter_leave(window, qapp) -> None:
    """enter/leave 乱序（Qt 把旧行的 leave 排在新行的 enter 之后）也不许灭掉刚亮的那行。"""
    _open_unit(window)
    rows = _entries(window).rows
    _send_enter(qapp, rows[2])
    _send_enter(qapp, rows[5])
    _send_leave(qapp, rows[2])  # 迟到的 leave
    assert _lit(rows) == [5]


def test_rebuild_takes_the_hover_down_with_it(window, qapp) -> None:
    """列表重建后，旧行不许还亮着 —— 这正是实机"7 格同亮"的攒法之一。"""
    _theme()
    _open_unit(window)
    panel = _entries(window)
    old = panel.rows
    _send_enter(qapp, old[4])
    assert _lit(old) == [4]

    panel.set_cells([row.cell for row in old], title=panel.title_text)

    assert old[4].property("hover") == "false", "重建后旧行还留着悬停态"
    assert old[4].is_hovered is False
    assert _lit(panel.rows) == [], "新铺的行也不该自带悬停态"


def test_hover_highlight_spans_the_whole_row(window, qapp) -> None:
    """⭐ 高亮必须**横跨整行**（序号 → 英文列名），不是只包住值那一格。

    离屏量像素：把亮着的那一行 grab() 下来，找高亮底色的左右边界，
    和行自己的宽度、以及值那一格的边界比。
    """
    _theme()
    _open_unit(window)
    panel = _entries(window)
    panel.resize(900, 700)
    panel.show()
    qapp.processEvents()
    row = panel.rows[3]

    _, _, cold = _hover_pixels(row)
    assert cold == [], "没悬停的行不该有高亮底色"

    _send_enter(qapp, row)
    qapp.processEvents()
    width, _, hits = _hover_pixels(row)
    assert hits, "悬停后整行没有画出高亮底色"
    xs = [x for x, _ in hits]
    assert min(xs) <= 2 and max(xs) >= width - 3, (
        f"高亮只覆盖 x={min(xs)}..{max(xs)}（行宽 {width}）—— 没有横跨整行"
    )
    value = row.value_button.geometry()
    assert min(xs) < value.left(), "高亮左边界还在值那一格里面"
    assert max(xs) > value.right(), "高亮右边界还在值那一格里面"


def test_stale_qt_hover_flag_cannot_paint_a_second_highlight(window, qapp) -> None:
    """就算 Qt 把 WA_UnderMouse 留在好几个值那一格上（= 实机那 7 格的可见状态），

    也不许再冒出第二个高亮 —— 现在高亮只有一个画源（行属性，单点写入）。
    修好之前，这 7 格会各自画出高亮底色；现在它们必须是干净的。
    """
    from PySide6.QtCore import Qt

    _theme()
    _open_unit(window)
    panel = _entries(window)
    panel.resize(900, 700)
    panel.show()
    qapp.processEvents()
    rows = panel.rows
    for row in (rows[3], rows[8], rows[9], rows[10], rows[12], rows[13], rows[14]):
        row.value_button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    qapp.processEvents()

    lit = [i for i, row in enumerate(rows) if _hover_pixels(row)[2]]
    assert lit == [], f"残留的 :hover 旗标又画出了高亮：{lit}"


def test_doc_annotation_hangs_on_the_name_label_only(window) -> None:
    """⭐ 用户原话："悬浮在修改栏上的时候，不要显示注释，注释只在悬浮在词条上的时候显示。"

    落点（表#Id · 原列名）与 doc 一起挂「中文名」那一格；值那一格是**编辑入口**，
    tooltip 必须是空串（鼠标停上去不许再冒一个框）。
    """
    _open_unit(window)
    rows = _entries(window).rows
    with_doc = [row for row in rows if row.cell.doc]
    assert with_doc, "样本单位里应当有带 doc 的词条"
    for row in with_doc:
        tip = row.name_label.toolTip()
        assert row.cell.doc in tip, "注释没挂在词条名上"
        assert row.cell.place in tip, "落点没挂在词条名上"
        assert f"{row.cell.cn} =" in tip

    for row in rows:
        assert row.value_button.toolTip() == "", "值那一格是编辑入口，不许有 tooltip"
        if not row.cell.doc:
            continue
        for child in row.findChildren(QWidget):
            if child.property("role") in ("entry-id", "entry-link"):
                assert row.cell.doc not in child.toolTip(), "徽章/箭头带上了注释"


def test_clicking_elsewhere_closes_the_editor(window, qapp) -> None:
    """点窗口里别的地方 -> 编辑栏收工（保存）。用户口径 2026-09-30 实机：

        我点击左侧空白区域时，我要看到它这个编辑栏消失，而不是还停在那里

    ❗ 只靠 focusOutEvent 不够：左栏那块空白是普通 QWidget（NoFocus），
    点上去**焦点根本不转移**，focusOut 不会来 —— 浮层于是原地不动。
    所以浮层在 QApplication 上装了事件过滤器（见 InlineEditor.__init__）。
    """
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    _open_unit(window)
    page = window._unit_editor.main_page
    row = next(r for r in page.entries.rows if r.cell.doc)
    page._open_inline_editor(row.cell)
    assert row.editor is not None, "没打开浮层"

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(2, 2),
        QPointF(2, 2),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(page.tree, press)  # 点左栏（窗口里别的地方）

    assert row.editor is None, "点了别处，编辑栏还停在那里"


def test_clicking_inside_the_editor_keeps_it_open(window, qapp) -> None:
    """反过来：按在浮层**自己**身上不许把它关掉（否则下拉一点就没了）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    _open_unit(window)
    page = window._unit_editor.main_page
    row = next(r for r in page.entries.rows if r.cell.doc)
    page._open_inline_editor(row.cell)
    editor = row.editor
    assert editor is not None

    inner = editor.findChildren(object)
    target = editor
    for child in inner:
        from PySide6.QtWidgets import QWidget

        if isinstance(child, QWidget):
            target = child
            break
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1, 1),
        QPointF(1, 1),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(target, press)
    assert row.editor is not None, "点浮层自己身上不该关"


# --------------------------------------------------------------------------- #
# 中栏标题 / 面包屑带「表图标」（用户口径 2026-10-01）
#
# 用户原话：「武器的图标、弹药的图标、改装的图标，都应该在标题栏上显示，
#            而并非只有文字」。
# ❗ 记号只有一份出处：entries_panel.table_emoji（= TABLE_EMOJI 那张登记表）——
#    用例也从函数取，改登记表就跟着走，不会"用例绿、界面没图标"。
# --------------------------------------------------------------------------- #


def test_the_middle_column_title_has_one_icon_slot(window) -> None:
    """⭐ 抬头 = **图标位** + 纯行名；副标题纯文字；面包屑尾段 = 图标位 + 纯行名。

    图标位**只能有一个**（用户口径 2026-10-01 第二轮：「我不是要 emoji，我是要图标」）：
    有真图画真图，没真图才把表 emoji 画在**图标位**上；文字里不再出现 emoji。
    """
    _open_unit(window)
    page = window._unit_editor.main_page
    screen = window._unit_editor
    weapon = page.card.card.weapons[0]
    assert weapon.ammo, "样本单位的武器没挂弹 —— 弹药那一条验不到"
    options = page.tree.tree.group("mods").children[0].children[0]
    assert options.table == "Options", f"取到的不是改装选项行：{options.table}"

    cases = (
        ("Weapons", weapon.weapon_id),
        ("Ammunitions", weapon.ammo[0].ammo_id),
        ("Options", options.row_id),
    )
    for table, row_id in cases:
        _focus_row(page, table, row_id)
        icon = _emoji_of(table)
        title = page.entries.title_text
        # 图标位：这一行有真图就画真图，没有就退回表 emoji（两样都在模型里）。
        mark = page.entries.title_mark
        assert mark is not None and mark.emoji == icon, (table, mark)
        # 文字里**不许**再有 emoji —— 它只在图标位上。
        assert not title.startswith(icon), f"{table} 行的抬头还把 emoji 写进文字：{title!r}"
        assert page.entries.subtitle_text.startswith(f"{_title_of(table)} {table}#{row_id}"), (
            f"{table} 行的副标题不是纯文字：{page.entries.subtitle_text!r}"
        )
        # 面包屑尾段：纯行名 + 它自己的图标位。
        trail = screen.trail()
        trail_marks = screen.trail_marks()
        assert not trail[-1].startswith(icon), f"{table} 行的面包屑尾段还带 emoji：{trail}"
        assert trail_marks[-1].emoji == icon, (table, trail_marks[-1])


def test_the_two_icons_the_user_named_are_the_registry_ones() -> None:
    """登记表本身的取值也钉一下（用户原话点名的就是武器的🔫与弹药的💥）。

    这一条判的是**登记表**，不是显示：显示那一条从 table_emoji 取（见上）。
    """
    assert _emoji_of("Weapons") == "🔫"
    assert _emoji_of("Ammunitions") == "💥"
    assert _emoji_of("Options"), "改装选项那一支不该是空串（具体是哪一支由登记表定）"


def test_inline_editor_carries_no_doc(window) -> None:
    """浮层里不许带注释 —— "注释只在悬停词条名时出现"的另一半。"""
    _theme()
    _open_unit(window)
    page = window._unit_editor.main_page
    row = next(r for r in page.entries.rows if r.cell.doc)
    page._open_inline_editor(row.cell)
    editor = row.editor
    assert editor is not None, "点了值没有冒出编辑控件"
    for widget in [editor, *editor.findChildren(QWidget)]:
        assert row.cell.doc not in widget.toolTip()


# --------------------------------------------------------------------------- #
# 第七轮 · 摘控件那条纪律（用户报过三次的「一闪而过的小弹窗」）
# --------------------------------------------------------------------------- #

_APP_DIR = Path(__file__).resolve().parents[1] / "src" / "ba_mod_maker" / "app"


def _function_body_nodes(function):
    """函数体里的节点（**不进**嵌套的函数 / lambda —— 别的函数里做了什么不算数）。"""
    import ast

    out = []
    stack = list(function.body)
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)
    return out


def _orphaning_sites(source: str, filename: str = "<scan>") -> list[str]:
    """找「可见控件被直接 setParent(None)」的写法。

    判据：**同一个函数体里**，X.setParent(None) 之前必须有一句 X.hide()。
    唯一允许写这句的地方是 app/widgets/detach.py::detach() —— 它先 hide 再摘，
    所以它落在**扫描范围里**也不报（顺带守住"有人把它的 hide 删了"）。
    """
    import ast

    tree = ast.parse(source, filename=filename)
    bad: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        hidden: set[str] = set()
        calls = [
            node
            for node in _function_body_nodes(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        for node in sorted(calls, key=lambda item: (item.lineno, item.col_offset)):
            target = ast.unparse(node.func.value)
            if node.func.attr == "hide":
                hidden.add(target)
                continue
            if node.func.attr != "setParent":
                continue
            if not (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value is None
            ):
                continue
            if target not in hidden:
                bad.append(f"{filename}:{node.lineno}: {ast.unparse(node)}")
    return bad


#: 故意写成**修之前**的样子（本轮修掉的那 7 处全是这个形状）。
_ORPHAN_SAMPLE = """
def rebuild(self):
    while self._grid.count():
        item = self._grid.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
"""

#: 修好之后的样子（先 hide 再摘 —— 也就是 detach() 里那两句）。
_ORPHAN_SAMPLE_FIXED = """
def rebuild(self):
    while self._grid.count():
        item = self._grid.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
"""


def test_the_orphan_scan_catches_a_missing_hide() -> None:
    """先证明这条守卫抓得住它要抓的东西 —— 否则它只是一条常绿的摆设。"""
    assert _orphaning_sites(_ORPHAN_SAMPLE, "sample.py"), "扫描器没抓到缺少 hide() 的摘法"
    assert not _orphaning_sites(_ORPHAN_SAMPLE_FIXED, "sample.py"), "补上 hide() 之后误报"


def test_the_app_wide_scan_catches_an_orphan_outside_screens(tmp_path) -> None:
    """§5-2 的判据：往 `app/` 的**任意一处**塞一句裸 setParent(None) → 红。

    用临时目录冒充 app/：证明**枚举那一层**也盖住了 screens/ 之外的地方
    （只测解析器不够 —— 老守卫扫的是 screens/，widgets/ 里塞一句它一辈子看不见）。
    """
    fake = tmp_path / "app"
    (fake / "widgets").mkdir(parents=True)
    (fake / "widgets" / "probe.py").write_text(
        "def rebuild(self, widget):\n    widget.setParent(None)\n", encoding="utf-8"
    )
    bad: list[str] = []
    for path in sorted(fake.rglob("*.py")):
        bad += _orphaning_sites(path.read_text(encoding="utf-8"), path.name)
    assert bad, "整树枚举没抓到 widgets/ 下那句裸 setParent(None)"


def test_the_app_never_orphans_a_widget_into_a_top_level_window() -> None:
    """**整个 `app/`** 下不许把**可见**控件直接 setParent(None)。

    setParent(None) 会把控件变成顶层窗口，而 Qt 对「摘下来那一刻还可见」的控件会
    真的映射出一个悬浮小窗 —— 用户眼里就是「有时候会闪出一个小弹窗」，报过三次。
    纪律：摘控件一律走 app/widgets/detach.py::detach()（先 hide 再摘再 deleteLater）。

    ❗ 扫描范围是**整个 app/**，不止 screens/（小事清单 §5-2）：闪窗的源头不挑目录，
    widgets / dialogs / shell 一样能长出来。detach() 自己的实现**也在范围里** ——
    判据是"先 hide 再摘"，它满足所以不报（顺带守住有人把那句 hide 删掉）。

    这条扫源码（AST），不依赖真的显示窗口；实机那一版回归闸是
    tests/test_unit_editor.py::test_nothing_flashes_as_a_top_level_window。
    """
    bad: list[str] = []
    scanned: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        name = path.relative_to(_APP_DIR).as_posix()
        scanned.append(name)
        bad += _orphaning_sites(path.read_text(encoding="utf-8"), name)
    covered = {name.split("/", 1)[0] for name in scanned}
    assert {"screens", "widgets", "dialogs", "shell"} <= covered, (
        f"守卫只扫到了 {sorted(covered)} —— 它得盖住整个 app/"
    )
    assert "widgets/detach.py" in scanned, "detach() 自己的实现没进扫描范围"
    assert not bad, (
        "这些地方把可见控件直接 setParent(None)（会闪顶层小窗）——"
        "改走 app/widgets/detach.py::detach()：\n" + "\n".join(f"  {line}" for line in bad)
    )


def test_the_reference_pages_detach_instead_of_orphaning(window, monkeypatch) -> None:
    """国家页 / 专精页重建与收浮层**真的**调了 detach()（行为面，不只看源码）。"""
    from ba_mod_maker.app.screens.reference import country_page, spec_page

    calls: list[object] = []
    real_country = country_page.detach
    real_spec = spec_page.detach_widget

    def spy_country(widget):
        calls.append(widget)
        real_country(widget)

    def spy_spec(widget):
        calls.append(widget)
        real_spec(widget)

    monkeypatch.setattr(country_page, "detach", spy_country)
    monkeypatch.setattr(spec_page, "detach_widget", spy_spec)

    country = window._country
    country.rebuild()
    assert calls, "国家页重建没走 detach() —— 控件是被直接摘的"

    spec = window._spec
    spec.show_row(spec._specs[0].specialization_id)  # 先把矩阵填上
    calls.clear()
    spec.show_row(spec._specs[1].specialization_id)  # 换一行 -> 旧的格子被摘掉
    assert calls, "专精页换行重建没走 detach()"

    calls.clear()
    country._open_editor(country._cells[(1, "MaxPoints")])
    assert country.editing is not None, "国家页没冒出就地编辑浮层"
    country._close_editor()
    assert calls, "国家页收就地编辑浮层没走 detach()"

    calls.clear()
    spec._open_list_editor(spec._list_widget.rows[0].cell)
    assert spec._editing is not None, "专精页没冒出就地编辑浮层"
    spec._close_editor()
    assert calls, "专精页收就地编辑浮层没走 detach()"


# --------------------------------------------------------------------------- #
# 真图标（2026-10-01 用户口径）：「我不是要 emoji，我是要图标」
#
# 标题 / 面包屑上那一行画的是**游戏里那张图**：取图只走 IconStore.pixmap
# （bundle 直读，ADR 0003）；没图 / 没 store / 图还没到 -> 退回标题里那个表 emoji。
# --------------------------------------------------------------------------- #


class _StubSignal:
    """`loaded` 的桩：能 connect、能手动 emit（图是异步到的，用例要能催它一次）。"""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self) -> None:
        for slot in list(self._slots):
            slot()


class _StubIcons:
    """桩图标缓存：`pixmap` 给一张 1x1 的图，并记下被问过哪些 (族, 键)。

    `ready=False` = 图还没解析出来（真机上进编辑器那一刻常常这样）。
    """

    def __init__(self, *, ready: bool = True, pixmap_source=None) -> None:
        from PySide6.QtGui import QPixmap

        self.loaded = _StubSignal()
        self.asked: list[tuple[str, str]] = []
        self.ready = ready
        self._pixmap = pixmap_source if pixmap_source is not None else QPixmap(1, 1)

    def pixmap(self, family: str, key: str):
        self.asked.append((family, key))
        return self._pixmap if self.ready else None


def _half_white_source():
    """不对称假图：左半边白、右半边透明 —— 翻没翻一眼看得出。"""
    from PySide6.QtGui import QColor, QPainter, QPixmap

    source = QPixmap(100, 40)
    source.fill(QColor(0, 0, 0, 0))
    painter = QPainter(source)
    painter.fillRect(0, 0, 50, 40, QColor("#ffffff"))
    painter.end()
    return source


def _alpha_at(pixmap, side: str) -> int:
    """图里左/右那半边的 alpha（0 = 透明）。"""
    image = pixmap.toImage()
    x = int(image.width() * (0.2 if side == "left" else 0.8))
    return image.pixelColor(x, image.height() // 2).alpha()


def test_the_title_icon_faces_right_like_the_game(window) -> None:
    """⭐ **抬头那一路也要翻**（用户 2026-10-01：「还是老问题，武器能不能让它们朝右」）。

    上一轮新加的中栏抬头没走「翻不翻」那份口径 —— 于是武器又朝左。
    现在统一走 `prepare_icon`，这条用例用一张「左半白、右半空」的假图当武器图标：
    抬头拿到的图**左边必须是空的**（说明真翻了）。判据与卡片那条同源。
    """
    from ba_mod_maker.core.edit.scope import RowRef

    _open_unit(window)
    page = window._unit_editor.main_page
    weapon_id = page.card.card.weapons[0].weapon_id

    page.set_icons(_StubIcons(pixmap_source=_half_white_source()))
    page.set_focus(RowRef("Weapons", weapon_id))
    mark = page.entries.title_mark
    assert mark is not None and mark.icon is not None, "武器行该有真图标位"
    assert mark.icon.family == "weapon_icons"

    pixmap = page.entries.title_icon_pixmap
    assert pixmap is not None and not pixmap.isNull(), "抬头没画上图"
    assert _alpha_at(pixmap, "left") == 0, "抬头那张武器图没翻（枪口还在左边）"
    assert _alpha_at(pixmap, "right") > 0


def test_the_title_icon_does_not_mirror_ammo(window) -> None:
    """⚠️ 弹药那族**不许翻**：同一张假图走 ammo_icons 应当保持原样（子弹本来就朝右）。"""
    from ba_mod_maker.core.edit.scope import RowRef

    _open_unit(window)
    page = window._unit_editor.main_page
    weapon = page.card.card.weapons[0]
    assert weapon.ammo, "样本武器的弹是空的？"

    page.set_icons(_StubIcons(pixmap_source=_half_white_source()))
    page.set_focus(RowRef("Ammunitions", weapon.ammo[0].ammo_id))
    mark = page.entries.title_mark
    assert mark is not None and mark.icon is not None
    assert mark.icon.family == "ammo_icons"

    pixmap = page.entries.title_icon_pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert _alpha_at(pixmap, "left") > 0, "弹药图标被翻了 —— 它本来就朝右"
    assert _alpha_at(pixmap, "right") == 0


def test_the_middle_title_draws_the_rows_real_icon(window) -> None:
    """⭐ 中栏抬头左边 = **那一行的真图标**；没 store 时退回标题里那个表 emoji。

    用户原话：「我不是要 emoji，我是要图标，单位图标、武器图标、弹药图标、改装图标这种」。
    """
    from ba_mod_maker.app.viewmodel.row_icon import row_icon

    _open_unit(window)
    page = window._unit_editor.main_page
    assert page.focus is not None and page.focus.table == "Units"
    want = row_icon(window._instance, "Units", STRYKER)
    assert want is not None and want.key, "样本单位的缩略图键是空的？"

    # ① 没 store：不画真图，**图标位退回表 emoji**（图标位只能有一个），
    #    而标题文字本身是纯行名 —— emoji 不再写进文字里。
    page.set_icons(None)
    assert page.entries.title_icon_visible is False
    assert page.entries.title_mark_text == _emoji_of("Units"), page.entries.title_mark_text
    assert not page.entries.title_text.startswith(_emoji_of("Units")), page.entries.title_text

    # ② 给了桩 store：画真图，而且问的是**那一行那一族那个键**。
    stub = _StubIcons()
    page.set_icons(stub)
    assert page.entries.title_icon == want
    assert page.entries.title_icon_visible is True
    assert (want.family, want.key) in stub.asked, stub.asked


def test_the_title_icon_refreshes_when_icons_arrive(window) -> None:
    """⭐ 图是**异步**到的：进编辑器时常常还没解析完 —— `icons.loaded` 到了要再刷一次。"""
    from ba_mod_maker.app.viewmodel.row_icon import row_icon

    _open_unit(window)
    page = window._unit_editor.main_page
    want = row_icon(window._instance, "Units", STRYKER)
    assert want is not None

    stub = _StubIcons(ready=False)  # 还没到货：pixmap() 给 None
    # ❗ 走**外层那个接缝**（UnitEditorScreen.set_icons）—— `loaded` 由它接一次，
    #    页面那一层只管画（接两次就会刷两遍）。main_window 走的也是这条路。
    window._unit_editor.set_icons(stub)
    assert page.entries.title_icon_visible is False

    stub.ready = True
    stub.loaded.emit()  # 真机上是 IconCache 解析完那一刻发的
    assert page.entries.title_icon_visible is True, "图到了没再刷一次（用户只看到 emoji）"


def test_the_breadcrumb_tail_draws_the_same_real_icon(window) -> None:
    """⭐ 面包屑每一段的**图标位**：有真图画真图、没真图才画 emoji，文字始终是纯段名。"""
    from ba_mod_maker.app.viewmodel.row_icon import RowIcon, RowMark
    from ba_mod_maker.app.widgets.breadcrumb import BreadcrumbBar

    _open_unit(window)
    bar = window._breadcrumb
    assert bar.segments[-1] == "单位"

    stub = _StubIcons()
    bar.set_icons(stub)
    window._sync_trail()
    assert bar.icon_drawn[-1] is True, bar.icon_drawn
    assert bar.mark_texts[-1] == "", "有真图时图标位不该再写 emoji"
    assert bar.segments == ("军械库", "Stryker RV", "单位")

    # 没 store 的干净控件：真图一个都不画，但**图标位上的 emoji 兜底照画**，
    # 段名与分隔符一字不变（emoji 不再写进文字里）。
    plain = BreadcrumbBar()
    plain.set_trail(
        ("军械库", "Stryker RV", "单位"),
        marks=(
            RowMark(),
            RowMark(icon=RowIcon("unit_labels", "K"), emoji="🔫"),
            RowMark(icon=RowIcon("unit_labels", "K"), emoji="🛡"),
        ),
    )
    assert plain.icon_drawn == (False, False, False)
    assert plain.mark_texts == ("", "🔫", "🛡")
    assert plain.trail_text == "军械库 › Stryker RV › 单位"
    plain.deleteLater()


# --------------------------------------------------------------------------- #
# 改了就要立刻看见：图标键那种列改完，抬头 / 面包屑的图要**重取**
# --------------------------------------------------------------------------- #


def test_changing_an_icon_key_updates_the_header_icon_at_once(window, monkeypatch) -> None:
    """改了**图标键**那种列，抬头那个图标要**立刻**重取 —— 用户口径 2026-10-01：
    「我改了的话，就要立即看到效果」。

    原来那条路只排了「重排当前页 + 重绘主卡」，**没有重取图标**：而抬头与面包屑那两张
    图的键正是刚改的 `Units.ThumbnailFileName`（unit_labels 那一族），不重取就一直是旧图。
    """
    from ba_mod_maker.app.widgets.row_entries import RowEntriesPanel
    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(29)
    # ❗ 切回三栏那一页再改：载具默认落在编成台，而「谁在台上谁刷」
    #    （_refresh_pages，AGENTS §0.7.1）—— 不在台上时这一格改了抬头不会重取，
    #    用例会红在一个**真实但无关**的原因上。
    window._unit_editor.show_main()
    page = window._unit_editor.main_page
    ref = CellRef("Units", 29, "ThumbnailFileName")
    cell = page.context.cell(ref)
    other = "US_CAVALRY_SCOUTS-Label"
    assert cell.text != other, "样本本身就等于新键，换一个"

    refreshed: list[str] = []
    real = RowEntriesPanel.refresh_icon

    def spy(self):
        refreshed.append("x")
        return real(self)

    monkeypatch.setattr(RowEntriesPanel, "refresh_icon", spy)

    page._commit(ref, other)
    window._unit_editor._refresh_pages()

    assert refreshed, "改完值没有重取图标 —— 抬头那张图会一直是旧的"
    mark = page.entries_mark()
    assert mark is not None and mark.icon is not None
    assert mark.icon.key == other, mark.icon.key
    # ❗ 只断言 `entries_mark()` 是**恒真**的（那是现算的）—— 真机上踩过：
    #    面板里记着的那份 `_mark` 不更新时，`refresh_icon()` 拿旧键去取图，
    #    量出来 pixmap 一直没变（63x27 的车 vs 22x27 的气球，逐像素差为 0）。
    #    所以要钉**控件里那份**。
    panel_icon = page.entries.title_icon
    assert panel_icon is not None and panel_icon.key == other, (
        "控件里那份 mark 没跟上 —— 抬头那张图还是旧的（真机量到过 pixmap 不变）"
    )
