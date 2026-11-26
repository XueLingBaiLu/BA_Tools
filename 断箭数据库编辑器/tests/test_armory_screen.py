"""军械库主屏的集成冒烟测试（需要真实字体）。

为什么不 mock 控件：这一层要验证的正是"控件和数据接对了没有"——
行数、卡片数、选中联动、翻页。用假控件测出来的只是"我以为的接线"。

无字体的平台（offscreen）会 skip —— 与 test_theme 同一策略：
宁可不给结论，也不给假结论。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QLabel, QWidget

from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
VDV_SPEC_ID = 7


# qapp 在 tests/conftest.py；`instance` 由下面这个**模块级**夹具提供（见它的说明）。


@pytest.fixture(scope="module")
def instance(shared_instance):
    """⚠️ 这里**故意**用模块级（与 conftest 里那个 function 级的 `instance` 不同）。

    两个理由：

    1. 这个文件的用例**全是只读**的 —— 卡片只读、改数据只在中间那栏，而且文件里
       另有两条用例专门断言"一个字节没动"。隔离由这条契约保证，不由"每例一份"保证。
    2. 同一个 `Instance` 复用能让 `Rows` 的列索引与 `CardData` 缓存**热着**：
       换成每例一份之后实测 setup 中位 0.12s -> 0.18s、整模块 20.4s -> 26.3s
       （每条用例都要把 24 张表的倒排索引重冷建一遍）。

    ⚠️ 这里的 `screen` **故意**保持 function 级：这些用例整篇都在断言那面卡墙的
       内容与选中态（专精 / 国家 / 过滤词 / 简略详细模式），而这些态就长在控件上。
       改成 module 级要靠"每条用例自己复位"，那正是要避免的顺序依赖。
    """
    return shared_instance


@pytest.fixture()
def screen(qapp, instance):
    from ba_mod_maker.app.screens import ArmoryScreen

    widget = ArmoryScreen()
    widget.resize(1400, 800)
    widget.set_instance(instance)
    yield widget
    widget.deleteLater()


# --------------------------------------------------------------------------- #
# 默认态
# --------------------------------------------------------------------------- #


def test_default_sheet_is_all_units(screen) -> None:
    assert screen.sheet_title == "全部单位"
    assert screen.visible_card_count == 395
    assert len(screen.row_widgets) == 7


def test_first_card_is_selected_on_load(screen) -> None:
    """空着的详情面板看起来像坏了，所以进数据源就自动选第一张。"""
    assert screen.detail_panel.current_unit_id() >= 0


def test_counts_show_three_independent_scopes(screen) -> None:
    text = screen.counts_text
    assert "全部 395" in text
    assert "未编入专精 57" in text


# --------------------------------------------------------------------------- #
# 专精
# --------------------------------------------------------------------------- #


def test_select_vdv_specialization(screen) -> None:
    screen.select_specialization(VDV_SPEC_ID)
    assert screen.sheet_title == "VDV Brigade"
    assert screen.visible_card_count == 31
    assert [row.armory_row.count for row in screen.row_widgets] == [7, 6, 2, 3, 7, 6]
    assert [row.armory_row.label for row in screen.row_widgets] == [
        "侦察",
        "步兵",
        "载具",
        "支援",
        "直升机",
        "战机",
    ]


def test_unassigned_pseudo_sheet_is_reachable(screen) -> None:
    """57 个未编入专精的单位必须能在界面上走到，不许藏。"""
    from ba_mod_maker.app.screens.armory.armory_screen import UNASSIGNED_ONLY

    screen.select_specialization(UNASSIGNED_ONLY)
    assert screen.sheet_title == "未编入任何专精"
    assert screen.visible_card_count == 57


def test_switching_country_resets_specialization(screen) -> None:
    screen.select_specialization(VDV_SPEC_ID)
    screen.select_country(2)
    # VDV 是俄罗斯专精，切到美国后不能再停留在它上面。
    assert screen.sheet_title == "全部单位"
    assert screen.visible_card_count == 196


# --------------------------------------------------------------------------- #
# 选中联动
# --------------------------------------------------------------------------- #


def test_clicking_a_card_updates_the_detail_panel(screen) -> None:
    screen.select_specialization(VDV_SPEC_ID)
    row = screen.row_widgets[0]
    card = row.armory_row.cards[0]
    row.card_clicked.emit(card.unit_id)
    assert screen.detail_panel.current_unit_id() == card.unit_id

    # 卡片必须真的填了内容，而不是只改了个 Id。
    # 抬头用 HUDName：645 的 Name 是 VDV Dsh 2，游戏卡片上写的是 VDV Shturm。
    panel = screen.detail_panel
    assert panel._title.text() == (card.hud_name or card.display_name)
    assert panel.card is not None
    assert panel.card.stats, "数值行一格都没有"
    assert panel._cost.text() == str(panel.card.cost)


class _StrayWindowSpy(QObject):
    """抓「无父控件被显示」。

    控件造出来还没给父控件就 setVisible(True) / show() 时，Qt 会**真的**把它
    当成独立顶层窗映射出来（实测 class=Qt6112QWindowIcon、202x79、白底还没画），
    下一行 addWidget 才把它收进布局 —— 用户看到的就是「闪一下的小弹窗」。
    """

    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self._sink = sink

    def eventFilter(self, watched, event) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
            and watched.parent() is None
        ):
            self._sink.append(f"{type(watched).__name__} objectName={watched.objectName()!r}")
        return False


def test_clicking_a_card_never_maps_a_stray_window(qapp, screen) -> None:
    """点卡片不能让任何控件被当成独立顶层窗映射出来（用户报的「小弹窗」）。

    2026-09-30 实锤：`detail_panel._WeaponArt.__init__` 里 `badge.setVisible(...)`
    写在 `box.addWidget(badge)` **前面**，而 `_label()` 造出来的标签是无父控件的
    —— 武器数量 >1 的单位，**每次单击卡片**都闪一个 202x79 的顶层窗。
    修法就是两句换序；这条用例守住它，别让谁再写回去。
    """
    panel = screen.detail_panel
    leaks: list[str] = []
    spy = _StrayWindowSpy(leaks)
    qapp.installEventFilter(spy)
    badges = 0
    try:
        for row in list(screen.row_widgets)[:6]:
            for card in row.card_widgets:
                card.clicked.emit(card.unit_id)
                qapp.processEvents()
                badges += sum(
                    1
                    for w in panel.findChildren(QLabel)
                    if w.property("role") == "weapon-count" and not w.isHidden()
                )
                if badges >= 3:
                    break
            if badges >= 3:
                break
    finally:
        qapp.removeEventFilter(spy)
    assert badges, "一轮下来没渲染出任何「×N」武器徽章（count>1），这个用例是空跑的"
    assert leaks == [], f"这些控件被当成独立顶层窗映射出来了：{leaks}"


def test_the_category_chips_live_in_a_folded_section(screen) -> None:
    """⭐ 用户 2026-10-02：「还是一行，**类别放在主界面里，还是做成折叠栏**」。

    判据：① 类别 chip 住在一个可折的栏里、**默认收起**（本仓口径：能折的都折、默认折叠）；
    ② 收起时那排 chip 不可见（不是"折了个寂寞"）。
    """
    section = screen._category_section
    assert section.collapsible() is True, "类别栏不可折"
    assert section.is_collapsed() is True, "类别栏没有默认收起"
    body = section.body()
    chips = list(screen._category_buttons.values())
    assert chips, "类别 chip 一个都没有"
    assert all(body.isAncestorOf(chip) for chip in chips), "类别 chip 还在筛选行里"
    assert not any(chip.isVisibleTo(screen) for chip in chips), "收起时那排 chip 不该还画着"


def test_selection_follows_the_sheet(screen) -> None:
    """切专精后，详情面板不能还停在"不在这个编制里"的单位上。

    实踩：从全部单位切到 VDV，面板还显示 M1A2 SEP v2 TUA（美国坦克），
    而它不在 VDV 编制里——面板和网格说的不是同一件事。
    """
    screen.select_specialization(VDV_SPEC_ID)
    vdv_ids = {card.unit_id for row in screen.row_widgets for card in row.armory_row.cards}
    assert screen.detail_panel.current_unit_id() in vdv_ids

    # 切回全部单位，选中项仍应落在视野内。
    screen.select_specialization(None)
    all_ids = {card.unit_id for row in screen.row_widgets for card in row.armory_row.cards}
    assert screen.detail_panel.current_unit_id() in all_ids


def test_selection_clears_when_nothing_matches(screen) -> None:
    screen.set_filter_text("这个词肯定不存在")
    assert screen.detail_panel.current_unit_id() == -1


# --------------------------------------------------------------------------- #
# 类别与过滤
# --------------------------------------------------------------------------- #


def test_category_chip_filters_rows(screen) -> None:
    screen._on_category_chip(1)
    assert [row.armory_row.label for row in screen.row_widgets] == ["步兵"]
    assert screen.visible_card_count == 62


def test_filter_text_narrows_cards(screen) -> None:
    screen.set_filter_text("Stryker")
    assert 0 < screen.visible_card_count < 395
    names = [
        card.display_name.casefold() for row in screen.row_widgets for card in row.armory_row.cards
    ]
    assert all("stryker" in name for name in names)


def test_filter_with_no_match_shows_hint(screen) -> None:
    screen.set_filter_text("这个词肯定不存在")
    assert screen.visible_card_count == 0
    assert len(screen.row_widgets) == 0


# --------------------------------------------------------------------------- #
# 翻页
# --------------------------------------------------------------------------- #


def test_category_row_flows_instead_of_paging(qapp) -> None:
    """一行放不下就换行，**不做分页**。

    第一版用了左右箭头翻页（"1/14"），那是错的：真实军械库是卡片流式换行、
    整页纵向滚动的（见 ADR 0002 引用的参考截图）。
    这条测试同时守住"不许把分页加回来"。
    """
    from PySide6.QtWidgets import QToolButton

    from ba_mod_maker.app.screens.armory.category_row import CategoryRow
    from ba_mod_maker.app.viewmodel import build_armory_sheet

    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    sheet = build_armory_sheet(instance)
    big = max(sheet.rows, key=lambda row: row.count)
    assert big.count > 6, "样本行必须比一行多，否则这个测试没有意义"

    widget = CategoryRow(big)
    widget.resize(1200, 400)

    # 卡片必须全在——分页会丢卡，流式不会。
    assert len(widget.card_widgets) == big.count
    # 翻页箭头不该存在。
    assert widget.findChildren(QToolButton) == []

    # 面板越窄，需要的高度越大；一行放得下的张数越少。
    assert widget.heightForWidth(420) > widget.heightForWidth(1600)
    assert widget.cards_per_line(420) < widget.cards_per_line(1600)
    assert widget.line_count(420) > widget.line_count(1600)
    widget.deleteLater()


# --------------------------------------------------------------------------- #
# 立绘框：高度必须跟随原图比例
# --------------------------------------------------------------------------- #


def test_portrait_box_is_the_same_for_every_unit(qapp) -> None:
    """立绘框的长宽必须**固定**，不随原图变化。

    实踩（用户报的）：一度让框高跟随每张原图的比例，结果 MOTOPEKHOTA
    （1024x512 = 2:1）的框比别的步兵矮一截，一排看过去长短不一。
    现在按**固定比例**算高度；超出的部分由 cover 直接裁掉——游戏军械库就是这么做。
    """
    from PySide6.QtGui import QPixmap

    from ba_mod_maker.app.screens.armory.silhouettes import PORTRAIT_ASPECT, SilhouetteView

    # 固定比例取绝大多数立绘的比例（433 张里 431 张）。
    assert abs(PORTRAIT_ASPECT - 816 / 548) < 1e-9

    view = SilhouetteView()
    view.resize(400, 400)
    baseline = view.heightForWidth(400)
    assert baseline == int(400 / PORTRAIT_ASPECT)

    for size in ((816, 548), (1024, 512), (2048, 1540)):
        view.set_pixmap(QPixmap(*size))
        assert view.heightForWidth(400) == baseline, f"{size} 让框高变了——框必须固定"
    view.deleteLater()


def test_portrait_pixmap_keeps_its_aspect_when_scaled(qapp) -> None:
    """缩放后比例必须与原图一致 —— 也就是"只裁不拉"。

    这条把"看起来被拉伸"这个说法钉死：要么是裁切，要么是缩放，
    绝不能是比例变了。
    """
    from PySide6.QtGui import QPixmap

    from ba_mod_maker.app.screens.armory.silhouettes import SilhouetteView

    source = QPixmap(1024, 512)
    view = SilhouetteView()
    view.resize(395, 236)
    view.set_pixmap(source)

    scaled = view.scaled_pixmap()
    assert scaled is not None
    ratio = scaled.devicePixelRatio() or 1.0
    width = scaled.width() / ratio
    height = scaled.height() / ratio
    assert abs(width / height - 1024 / 512) < 0.01, "比例变了 = 真的被拉伸了"
    # cover：至少一边铺满
    assert width >= 395 - 1 and height >= 236 - 1
    view.deleteLater()


# --------------------------------------------------------------------------- #
# 滚动范围：必须与内容一一对应（用户报的「下拉条太长了」）
# --------------------------------------------------------------------------- #


def _settle(qapp, milliseconds: int = 40) -> None:
    """跑一小段**真实**事件循环。

    为什么不能只 processEvents()：布局请求是投递给父控件的，
    一次 processEvents 不一定让 QScrollArea 把滚动范围重算完。
    真实运行时时时刻刻都有事件循环，所以这里必须模拟同样的条件，
    否则测出来的是"测试没等够"，不是产品有问题。
    """
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()
    qapp.processEvents()


def test_scroll_range_matches_the_content(qapp, screen) -> None:
    """滚动范围 = 内容高 - 视口高。多一个像素都是滚不到头的空白。

    实踩：画布的 minimumSizeHint 走的是 QLayout.totalMinimumSize()，
    而流式布局在「一行只放一张卡」的最窄宽度下算出 56130px，
    按真实宽度排完只要 9838px。QScrollArea 取"minSizeHint 与视口的较大者"，
    于是 395 张卡的列表能往下拖 46000 多像素的空白，滑块被压到 9px
    （贴住 QSS 的 min-height 下限），完全看不出自己读到哪儿了。
    """
    screen.resize(1400, 800)
    screen.show()
    _settle(qapp)

    scroll = screen._scroll
    canvas = scroll.widget()
    bar = scroll.verticalScrollBar()
    viewport = scroll.viewport()

    # 关键断言：画布高 = 按"视口宽度"流式排完的高度。
    # 旧行为会让它变成"一行只放一张卡"的堆叠高度（56130），这里立刻红。
    flow = canvas.layout().heightForWidth(viewport.width())
    assert canvas.height() == max(flow, viewport.height()), (
        f"画布高 {canvas.height()}，而按 {viewport.width()}px 宽排完只要 {flow}px"
    )
    content = canvas.minimumSizeHint().height()
    assert content == flow, f"minimumSizeHint 报 {content}，流式布局报 {flow} —— 两者必须一致"
    assert bar.maximum() - bar.minimum() == max(0, content - viewport.height()), (
        f"滚动范围 0..{bar.maximum()} 与内容 {content} / 视口 {viewport.height()} 对不上"
    )
    # 滑块长度要能反映进度：这里是几百张卡的长列表，滑块不该几乎占满整条。
    ratio = bar.pageStep() / (bar.maximum() + bar.pageStep())
    assert 0.02 < ratio < 0.5, f"滑块占比 {ratio:.3f}，不像是在反映内容长度"
    screen.hide()


def test_scroll_range_follows_the_content_when_filtering(qapp, screen) -> None:
    """内容变短，滚动范围必须跟着收（"根据内容自动调节"）。

    换一个单位本来就少的过滤词，再换回空过滤词，两次都要对得上。
    """
    screen.resize(1400, 800)
    screen.show()
    _settle(qapp)

    scroll = screen._scroll
    bar = scroll.verticalScrollBar()
    viewport = scroll.viewport()
    assert bar.maximum() > 0, "初始态就该能滚 —— 否则下面的断言是假绿"

    screen.set_filter_text("Typhoon")
    _settle(qapp)
    few = screen.visible_card_count
    assert 0 < few <= 12, f"样本要小才有意义，这次过滤出 {few} 个"
    assert bar.maximum() == 0, "内容比视口矮时不该还有滚动范围"
    assert not bar.isVisible(), "内容装得下就不该显示滚动条"

    screen.set_filter_text("")
    _settle(qapp)
    flow = scroll.widget().layout().heightForWidth(viewport.width())
    assert bar.maximum() - bar.minimum() == max(0, flow - viewport.height())
    assert bar.maximum() > 0, "清掉过滤词后又该能滚了"
    screen.hide()


# --------------------------------------------------------------------------- #
# 阵营 chip：用游戏内的国旗素材（不是文字）
# --------------------------------------------------------------------------- #


def test_country_chips_use_the_in_game_flags(screen) -> None:
    """Russia / USA 显示游戏内国旗 —— 素材键取自 Countries.FlagFileName。"""
    from ba_mod_maker.app.theme import IconCache, find_icon_cache
    from ba_mod_maker.app.viewmodel import (
        UNCLASSIFIED_COUNTRY_ID,
        list_countries,
        unclassified_country,
    )

    if find_icon_cache() is None:
        pytest.skip("本机没有 cache/icons（先跑 tools/extract_icons.py）")

    cache = IconCache()
    buttons = {cid: b for cid, b in screen._country_buttons.items() if cid is not None}
    assert buttons, "一个阵营 chip 都没建出来"

    by_id = {c.country_id: c for c in list_countries(screen._instance)}
    # 「未分类」那一格不在 Countries 表里 —— 它是 viewmodel 造的，旗子却同一族（random flag）。
    by_id[UNCLASSIFIED_COUNTRY_ID] = unclassified_country()
    for country_id, button in buttons.items():
        country = by_id[country_id]
        if cache.pixmap("flags", country.flag_key) is None:
            assert button.text() == country.display_name, "没素材必须退回文字"
            continue
        assert not button.icon().isNull(), f"{country.display_name} 没拿到国旗"
        assert button.text() == "", "有国旗就不必再把国名写一遍"
        # 只剩一面旗时，名字只能靠提示与读屏补回来。
        assert button.toolTip() == country.display_name
        assert button.accessibleName() == country.display_name
        assert button.iconSize().width() > 0 and button.iconSize().height() > 0


def test_country_chip_falls_back_to_text_without_icons(qapp, screen, tmp_path) -> None:
    """没有任何图标来源时必须退回文字：绝不允许出现既没旗又没字的空按钮。

    ❗ 三个来源都要断掉（没游戏、没旧缓存、没运行时缓存）——只断一个的话
    另外两个会照常把旗子掏出来，这条测试就成了假绿。
    """
    from ba_mod_maker.app.theme import IconCache

    screen._icons = IconCache(
        tmp_path / "not-here",
        game_root=tmp_path / "no-game",
        runtime_cache=tmp_path / "no-runtime",
    )
    screen._rebuild_country_chips()

    buttons = [b for cid, b in screen._country_buttons.items() if cid is not None]
    assert buttons, "降级后阵营 chip 不该消失"
    for button in buttons:
        assert button.text(), "没素材时 chip 必须有文字"
        assert button.icon().isNull()


def test_unclassified_chip_sits_rightmost(screen) -> None:
    """「未分类」永远排在国家那一排**最右**（用户 2026-10-02 深夜要的那一格）。"""
    from ba_mod_maker.app.viewmodel import UNCLASSIFIED_COUNTRY_ID

    button = screen._country_buttons.get(UNCLASSIFIED_COUNTRY_ID)
    assert button is not None, "国家那一排没有「未分类」chip"
    assert button.property("chipKey") == "country", "重画要靠它认出来（_rebuild_country_chips）"

    layout = screen._country_separator.parentWidget().layout()
    others = [
        other
        for cid, other in screen._country_buttons.items()
        if cid not in (None, UNCLASSIFIED_COUNTRY_ID)
    ]
    assert others, "俄罗斯 / 美国两面旗都没建出来"
    for other in others:
        assert layout.indexOf(button) > layout.indexOf(other), "「未分类」必须排在最后"


def test_clicking_unclassified_filters_to_that_bucket(screen) -> None:
    """点它 = 只看未分类（**真点按钮**，不是 emit 冒充点过了）。"""
    from ba_mod_maker.app.viewmodel import UNCLASSIFIED_COUNTRY_ID

    screen._country_buttons[UNCLASSIFIED_COUNTRY_ID].click()
    assert screen._country_id == UNCLASSIFIED_COUNTRY_ID
    assert screen._country_buttons[UNCLASSIFIED_COUNTRY_ID].isChecked()
    assert "未分类 8" in screen.counts_text
    # 「上不了军械库的也给我上」：8 个 ZOM（DisplayInArmory=False）现在都在墙上。
    assert screen.visible_card_count == 8


def test_include_hidden_switch_shows_every_unit(screen) -> None:
    """「隐藏单位」开关：**真点那颗按钮**，整张 Units 表（540）全上墙。

    用户 2026-10-03 原话：「**我要能在产品里面，看到所有单位！**」
    """
    assert screen.visible_card_count == 395
    assert screen.include_hidden is False

    screen._include_hidden_button.click()
    assert screen.include_hidden is True
    assert screen.visible_card_count == 540
    assert "全部 540" in screen.counts_text

    screen._include_hidden_button.click()
    assert screen.include_hidden is False
    assert screen.visible_card_count == 395


def test_spec_chips_sit_after_the_country_chips(screen) -> None:
    """专精 = **国家后面那排图标**。

    用户 2026-10-03 原话：「**专精不是有图标吗？国家后面跟几个专精的图标。**」


    14 颗 = 「全部专精」＋ 12 个专精（带游戏内图标）＋ 「未编入任何专精」。
    """
    from ba_mod_maker.app.screens.armory.armory_screen import ALL_SPECIALIZATIONS
    from ba_mod_maker.app.viewmodel import UNCLASSIFIED_COUNTRY_ID

    buttons = screen._spec_buttons
    assert len(buttons) == 14, "12 个专精 ＋ 全部专精 ＋ 未编入任何专精"
    names = [b.toolTip() for key, b in buttons.items() if key not in (ALL_SPECIALIZATIONS, -2)]
    assert "VDV Brigade" in names, f"12 个专精里没有 VDV：{names}"

    layout = screen._spec_separator.parentWidget().layout()
    last_country = screen._country_buttons[UNCLASSIFIED_COUNTRY_ID]
    for button in buttons.values():
        assert layout.indexOf(button) > layout.indexOf(last_country), "专精 chip 必须排在国家后面"
    assert layout.indexOf(screen._include_hidden_button) > max(
        layout.indexOf(b) for b in buttons.values()
    ), "「隐藏单位」开关排在最右"


def test_unclassified_keeps_the_specialization_chips(screen) -> None:
    """选「未分类」之后专精那排**不能被清空**（用户 2026-10-03：「我说我还要分专精，你没分」）。

    按国家筛专精时 -1 一个都匹配不上 ⇒ 那排就只剩「全部专精 / 未编入任何专精」两颗。
    """
    from ba_mod_maker.app.viewmodel import UNCLASSIFIED_COUNTRY_ID

    before = len(screen._spec_buttons)
    screen._country_buttons[UNCLASSIFIED_COUNTRY_ID].click()
    after = [b.toolTip() for b in screen._spec_buttons.values()]
    assert len(after) == before == 14, f"未分类下专精少了：{after}"
    assert "VDV Brigade" in after


def test_flag_icon_keeps_the_source_aspect(qapp) -> None:
    """旗标绘制尺寸必须与原图同比 —— 拉扁的国旗一眼就能看出来。

    游戏素材是 272x128（2.125:1）：普通旗标 34x16 是它的 1/8，
    军械库 chip 那颗 51x24 是它的 3/8（用户 2026-10-03 要求放大后仍是整数倍）。
    """
    from PySide6.QtGui import QImage

    from ba_mod_maker.app.theme import IconCache, find_icon_cache
    from ba_mod_maker.app.theme.tokens import TOKENS

    root = find_icon_cache()
    if root is None:
        pytest.skip("本机没有 cache/icons（先跑 tools/extract_icons.py）")

    entries = IconCache().family_files("flags")
    assert entries, "manifest 里没有 flags 族"
    pairs = (
        (TOKENS.size.flag_width, TOKENS.size.flag_height, "旗标"),
        (TOKENS.size.chip_flag_width, TOKENS.size.chip_flag_height, "军械库 chip 旗标"),
    )
    for width, height, where in pairs:
        drawn = width / height
        for key, relative in entries.items():
            image = QImage(str(root / relative))
            assert not image.isNull(), f"{relative} 读不出来"
            source = image.width() / image.height()
            assert abs(source - drawn) < 0.02, (
                f"{where}：{key} 原图比例 {source:.3f}，绘制比例 {drawn:.3f} —— 会被拉变形"
            )


# --------------------------------------------------------------------------- #
# 容器不许自己刷底色（用户报的「这里的底部是纯色的」）
# --------------------------------------------------------------------------- #


def _pixel_probe(image, widget, x: int, y: int):
    """按控件坐标取 grab 图上的像素（grab 的图带设备像素比，不能直接下标）。"""
    sx = image.width() / max(1, widget.width())
    sy = image.height() / max(1, widget.height())
    return image.pixelColor(int(x * sx), int(y * sy))


@pytest.fixture()
def styled(qapp):
    """临时套上产品样式表。

    这几条像素级用例**必须戴着真 QSS 跑**：坑就出在全局 `QWidget` 规则上，
    不套样式表的话没有任何容器会刷底色，测出来是假绿。
    """
    from ba_mod_maker.app.theme.qss import build_stylesheet

    previous = qapp.styleSheet()
    qapp.setStyleSheet(build_stylesheet())
    yield
    qapp.setStyleSheet(previous)


def test_card_flow_does_not_paint_over_the_backdrop(qapp, styled) -> None:
    """类别块里的流容器必须**不被涂色**，好让背景纹理透出来。

    实踩（用户报的「这里的底部是纯色的」）：全局 `QWidget` 规则会给每个控件刷
    bg-base，而**纯 QWidget 实例**（不是子类）会被 Qt 自动加上 WA_StyledBackground。
    QWidget 子类反而不会被自动刷——所以只有裸容器露馅：每个类别最后一行右边的
    空白被刷成一整块纯色（实测 #12151b），底下 BackdropWidget 的斜纹一点都透不出来。
    """
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    from ba_mod_maker.app.screens.armory.category_row import CategoryRow
    from ba_mod_maker.app.viewmodel import build_armory_sheet

    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    sheet = build_armory_sheet(instance)
    biggest = max(sheet.rows, key=lambda row: row.count)

    widget = CategoryRow(biggest)
    width = 1200
    widget.resize(width, widget.heightForWidth(width))

    host = widget._flow_host

    # ❗ 只画子控件、**不画窗口底色**（DrawChildren）。
    # 用 grab() 的话 Qt 会先把整块刷成调色板的窗口色（套上样式表后恰好就是 bg-base），
    # 那样"有没有被涂色"就分不出来了——取巧的参照点会让这条用例变成假绿。
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)

    # 参照点：流容器左侧那 8px 间隔（在 CategoryRow 里、但不属于流容器）
    reference = _pixel_probe(image, widget, host.x() - 4, host.height() - 40)
    # 待测点：最后一行卡片右边的空位
    tail = _pixel_probe(image, widget, host.x() + host.width() - 40, host.height() - 40)

    assert reference.alpha() == 0, f"参照点本身就不透明（{reference.name()}），取点不对"
    assert tail.alpha() == 0, (
        f"流容器末尾被涂成了 {tail.name()}（alpha={tail.alpha()}）——"
        "它必须透明，让 BackdropWidget 的纹理透出来"
    )
    widget.deleteLater()


def test_detail_body_does_not_paint_over_the_panel(qapp, screen, styled) -> None:
    """详情体的卡片间隙必须透出**面板自己的底色**，而不是 bg-base。

    同一条根因（裸 QWidget 容器被 Qt 自动刷底）：面板是 bg-panel，
    而详情体一度把卡片间隙刷成更暗的 bg-base。
    """
    screen.resize(1400, 800)
    screen.show()
    _settle(qapp)

    panel = screen.detail_panel
    body = panel._body
    layout = body.layout()
    widgets = [
        w for w in (layout.itemAt(i).widget() for i in range(layout.count())) if w is not None
    ]
    widgets.sort(key=lambda w: w.geometry().top())
    assert len(widgets) >= 2, "详情体里至少要有两张卡片才量得出间隙"

    gap_y = (widgets[0].geometry().bottom() + widgets[1].geometry().top()) // 2
    gap_point = body.mapTo(panel, QPoint(body.width() // 2, gap_y))

    image = panel.grab().toImage()
    gap = _pixel_probe(image, panel, gap_point.x(), gap_point.y())
    # 参照：面板左边距（滚动区之外）一定是面板底色
    reference = _pixel_probe(image, panel, 5, panel.height() // 2)

    from PySide6.QtGui import QColor

    from ba_mod_maker.app.theme.tokens import TOKENS

    assert reference != QColor(TOKENS.color.bg_base), "参照点恰好是 bg-base，失去参照意义"
    assert gap == reference, (
        f"详情体的卡片间隙是 {gap.name()}，而面板底色是 {reference.name()} ——容器不该自己刷底色"
    )
    screen.hide()


# --------------------------------------------------------------------------- #
# 单位卡片：收起 / 展开 / 自定义选项（设计-单位卡片复刻.md 10.1-3）
# --------------------------------------------------------------------------- #


def test_card_defaults_to_collapsed(qapp, screen) -> None:
    """默认收起（用户定的第 5 条）：进单位时没有任何武器被展开。"""
    screen.select_specialization(VDV_SPEC_ID)
    panel = screen.detail_panel
    assert panel.expanded_weapon is None


def test_clicking_a_weapon_selects_it_in_detailed_mode(qapp, screen) -> None:
    """点武器 = 切到详细模式并选中它；再点同一把**不会**把详情清空。

    模式是标题右边那对按钮的事（简略 / 详细）。详细模式里左竖排总得有一把选中态，
    否则右半边会退回第一把、左边却一把都不高亮。
    """
    from ba_mod_maker.app.screens.armory.armory_screen import UNASSIGNED_ONLY

    # 645 有 3 组武器，用它当样本（它挂在未编入专精里）
    screen.select_specialization(UNASSIGNED_ONLY)
    panel = screen.detail_panel
    panel.show_unit(645)
    card = panel.card
    assert card is not None and len(card.weapons) == 3

    key = card.weapons[0].group_key
    panel._toggle_weapon(key)
    assert panel.expanded_weapon == key
    panel._toggle_weapon(key)
    assert panel.expanded_weapon == key

    other = card.weapons[1].group_key
    panel._toggle_weapon(other)
    assert panel.expanded_weapon == other


# --------------------------------------------------------------------------- #
# 两种显示模式（用户口径：复刻游戏那张卡的简略 / 详细）
# --------------------------------------------------------------------------- #


def _compact_grid(panel):
    """简略模式那张表的 QGridLayout（找不到 = 表没建出来）。"""
    from PySide6.QtWidgets import QGridLayout, QWidget

    host = next(
        (w for w in panel.findChildren(QWidget) if w.property("role") == "compact-table"),
        None,
    )
    assert host is not None, "简略模式应当有一张紧凑表"
    layout = host.layout()
    assert isinstance(layout, QGridLayout), f"紧凑表必须是网格（列才对得齐），现在是 {layout}"
    return layout


def test_weapons_default_to_the_compact_mode(qapp, screen) -> None:
    """默认简略（用户定的第 5 条：默认收起），并且**两种模式都能切**。"""
    from PySide6.QtWidgets import QPushButton

    from ba_mod_maker.app.screens.armory.armory_screen import UNASSIGNED_ONLY
    from ba_mod_maker.app.screens.armory.detail_panel import MODE_COMPACT, MODE_DETAIL

    screen.select_specialization(UNASSIGNED_ONLY)
    panel = screen.detail_panel
    panel.show_unit(132)
    assert panel.mode == MODE_COMPACT
    assert panel.expanded_weapon is None, "简略模式没有「展开哪一把」这回事"
    buttons = [b.text() for b in panel.findChildren(QPushButton) if b.property("role") == "chip"]
    assert buttons == ["简略", "详细"], f"武器标题右边要有那两个按钮，现在是 {buttons}"

    panel.set_mode(MODE_DETAIL)
    assert panel.mode == MODE_DETAIL
    assert panel.expanded_weapon == panel.card.weapons[0].group_key, "详细模式要自动选中第一把"
    panel.set_mode(MODE_COMPACT)
    assert panel.mode == MODE_COMPACT
    assert _compact_grid(panel) is not None

    with pytest.raises(ValueError):
        panel.set_mode("没这个模式")


def test_compact_rows_share_the_columns(qapp, screen) -> None:
    """简略模式：一把武器一行、**列是共享的**（用户要的"并列"）。

    AK-74RMO / PKP-M / Metis-M 三行的射程、携弹量、穿深、火力必须落在**同一列**上 ——
    这正是实机那张卡的样子，也是原来"一块一块"时办不到的事。
    """
    from ba_mod_maker.app.screens.armory.armory_screen import UNASSIGNED_ONLY
    from ba_mod_maker.app.viewmodel.unit_card import COMPACT_COLUMNS

    screen.select_specialization(UNASSIGNED_ONLY)
    panel = screen.detail_panel
    panel.show_unit(132)
    card = panel.card
    grid = _compact_grid(panel)

    assert grid.rowCount() == len(card.weapons) + 1, "表头 + 每把武器一行"
    # 0 = 武器那一格（大剪影），1 = 弹簧列（把数值块推到右边），2..5 = 四列数据，6 = 目标图标
    assert grid.columnCount() == len(COMPACT_COLUMNS) + 3
    assert grid.itemAtPosition(1, 1) is None, "第 1 列是弹簧列，不该有控件"
    for line, weapon in enumerate(card.weapons, start=1):
        assert len(weapon.compact) == len(COMPACT_COLUMNS), weapon.label
        for index in range(len(COMPACT_COLUMNS)):
            item = grid.itemAtPosition(line, index + 2)
            assert item is not None, f"第 {line} 行第 {index} 列空着 —— 列会串位"
            label = item.widget()
            assert label.text() == weapon.compact[index].text
            assert label.alignment() & Qt.AlignmentFlag.AlignRight, "数值格要右对齐"
            assert label.toolTip(), "每一格都要能悬浮看到完整说法"


def test_detailed_mode_lays_each_value_on_one_line(qapp, screen) -> None:
    """详细模式：一格数据**一行**（标签贴左、值贴右）—— 不再一格一个方框。"""
    from PySide6.QtWidgets import QFrame, QLabel, QWidget

    from ba_mod_maker.app.screens.armory.armory_screen import UNASSIGNED_ONLY
    from ba_mod_maker.app.screens.armory.detail_panel import MODE_DETAIL

    screen.resize(1400, 800)
    screen.show()
    _settle(qapp)
    screen.select_specialization(UNASSIGNED_ONLY)
    panel = screen.detail_panel
    panel.show_unit(132)
    panel.set_mode(MODE_DETAIL)
    _settle(qapp)

    card = panel.card
    weapon = next(w for w in card.weapons if w.group_key == panel.expanded_weapon)
    expected = len(weapon.detail) + sum(len(ammo.detail) for ammo in weapon.ammo)
    assert expected >= 6, "样本太少，这条测试没有意义"

    lines = [w for w in panel.findChildren(QWidget) if w.property("role") == "stat-line"]
    assert len(lines) == expected, f"每一条详情行都该是一行（期望 {expected}，实得 {len(lines)}）"
    for line in lines:
        labels = [
            child
            for child in line.children()
            if isinstance(child, QLabel) and child.property("role") == "stat-line-label"
        ]
        values = [
            child
            for child in line.children()
            if isinstance(child, QLabel) and child.property("role") == "stat-line-value"
        ]
        assert len(labels) == 1 and len(values) == 1, "一行里就该一个标签 + 一个值"
        label_y = labels[0].mapTo(line, labels[0].rect().center()).y()
        value_y = values[0].mapTo(line, values[0].rect().center()).y()
        assert abs(label_y - value_y) <= 2, "标签和值不在同一行 —— 那就不是「并列」了"
        assert labels[0].x() < values[0].x(), "标签在左、值在右"

    chips = [w for w in panel.findChildren(QFrame) if w.property("role") == "stat-chip"]
    assert len(chips) == len(card.stats), (
        "只有数值行还用小格子；武器详情一格一个方框正是用户不要的那个"
    )
    screen.hide()


def test_ammo_rows_show_the_target_type_icons(qapp, screen) -> None:
    """弹药的「能打什么」：每个弹种一行小图标，文案挂在 tooltip 上（游戏里也只有图）。

    645 的 RPG-7V：PG-7VL = 载具 + 直升机；TBG-7L 温压 = 步兵 + 载具 + 直升机。
    """
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.armory.armory_screen import UNASSIGNED_ONLY

    screen.select_specialization(UNASSIGNED_ONLY)
    panel = screen.detail_panel
    panel.show_unit(645)
    rpg = next(w for w in panel.card.weapons if w.label == "RPG-7V")
    panel._toggle_weapon(rpg.group_key)

    chips = [
        label for label in panel.findChildren(QLabel) if label.property("role") == "target-chip"
    ]
    assert [chip.toolTip() for chip in chips] == [
        "目标 - 载具",
        "目标 - 直升机",
        "目标 - 步兵",
        "目标 - 载具",
        "目标 - 直升机",
    ]


def test_card_text_stays_inside_the_card(screen) -> None:
    """卡片要**包住**自己的文字 —— 用户报过两次：「溢出卡片」与「下面没显示全」。

    两版都栽过，所以这里连**字面**（ascent/descent）一起验：

    1. 行按 `index x 行距(11)` 排、矩形给 13 高 -> 第二行垂到卡片外（溢出）；
    2. 改成均分矩形后每格 11，而 9pt 的 ascent+descent=15 -> `Snaipersy` 的 p/y 尾巴被切。
    现在按基线排（`name_baselines`），本用例验的就是"每一行的字面都在卡片里"。
    """
    from PySide6.QtGui import QFontMetrics

    from ba_mod_maker.app.screens.armory.unit_card import UnitCard, _name_lines
    from ba_mod_maker.app.theme.fonts import ui_font
    from ba_mod_maker.app.theme.tokens import TOKENS

    metrics = QFontMetrics(ui_font(TOKENS.typography.size_xs))
    cards = screen.findChildren(UnitCard)
    assert cards, "一张卡都没有？"
    for card in cards:
        name = card.card.display_name
        block = card.name_rect()
        assert block.top() >= 0, (name, block)
        assert block.bottom() <= card.height(), (name, block)
        # 名字区不能压到图标区上（两行名字是画在图标下面的）
        assert card.avatar_rect().bottom() <= block.top() + 0.01, name
        lines = _name_lines(metrics, name, block.width())
        baselines = card.name_baselines(len(lines), metrics)
        assert len(baselines) == len(lines)
        for line, baseline in zip(lines, baselines, strict=True):
            ink_top = baseline - metrics.ascent()
            ink_bottom = baseline + metrics.descent()
            assert ink_top >= 0, (name, line, ink_top)
            assert ink_bottom <= card.height() + 0.01, (name, line, ink_bottom, card.height())


def test_the_cost_is_not_clipped(qapp, instance) -> None:
    """成本数字**不能裁**：写死 30px 的盒子把 MiG-31K (S) 的 1500 画成了 "|500"。

    用户报的"MiG-31K 的钱数没有显示全"就是这个：`drawText` 右对齐，盒子比文字窄就
    把**最左边那位**裁掉。现在盒子按文字宽度算（`cost_rect`），右边缘不动、位数多了往左长。
    """
    from PySide6.QtGui import QFontMetrics

    from ba_mod_maker.app.screens.armory.unit_card import UnitCard
    from ba_mod_maker.app.theme.fonts import ui_font
    from ba_mod_maker.app.theme.tokens import TOKENS
    from ba_mod_maker.app.viewmodel import build_armory_sheet

    sheet = build_armory_sheet(instance)
    cards = {card.unit_id: card for row in sheet.rows for card in row.cards}
    metrics = QFontMetrics(ui_font(TOKENS.typography.size_xs, bold=True))

    big = cards[702]  # MiG-31K (S)，1500 —— 四位数，就是报出来的那个
    small = cards[44]  # Su-57，335（右卡口径：225 + 缺省选项 50 + 60，见本条上面的用例）
    assert big.cost == 1500 and small.cost == 335, "样本变了，这条用例要重挑"

    wide = UnitCard(big)
    narrow = UnitCard(small)
    for widget, card in ((wide, big), (narrow, small)):
        text = str(card.cost)
        assert widget.cost_rect(metrics).width() >= metrics.horizontalAdvance(text), (
            f"{card.display_name} 的成本 {text} 会被裁掉最左边那位"
        )
    # 右边缘不动（成本一直贴在右上角），位数多了往左长
    from dataclasses import replace

    huge = UnitCard(replace(big, cost=123456))
    assert wide.cost_rect(metrics).right() == narrow.cost_rect(metrics).right()
    assert huge.cost_rect(metrics).width() > wide.cost_rect(metrics).width()
    assert huge.cost_rect(metrics).right() == wide.cost_rect(metrics).right()
    assert huge.cost_rect(metrics).width() >= metrics.horizontalAdvance("123456")
    wide.deleteLater()
    narrow.deleteLater()
    huge.deleteLater()


def test_the_card_wall_never_pops_a_hover_tooltip(qapp, instance) -> None:
    """军械库卡墙悬停**不再弹**那个悬浮窗（用户 2026-10-02：「我不要这个悬浮窗」）。

    从前 `UnitCard._update_tooltip` 会拼 5 行（名字 / Id·类别 / 类型 / 成本 / 图标键）；
    实跑 52085：**395 张卡每张都是 5 行**，一悬停就盖住小半个卡墙。
    `toolTip() == ""` 是**机制**上的判据 —— 控件没有提示文本，Qt 就不可能弹框；
    三个就地刷新口（改成本 / 改名字 / 换图标）也不许顺手挂一个回来。
    ⛔ 要那一行的信息去右侧详情面板（`UnitCardPanel`，31 列全在）。
    """
    from ba_mod_maker.app.screens.armory.unit_card import UnitCard
    from ba_mod_maker.app.viewmodel import build_armory_sheet

    sheet = build_armory_sheet(instance)
    cards = [card for row in sheet.rows for card in row.cards]
    assert len(cards) > 300, "样本太小，这条守卫会变空转"
    for card in cards[:5]:
        widget = UnitCard(card)
        assert widget.toolTip() == "", f"{card.display_name} 上又长回了悬浮提示"
        widget.deleteLater()

    widget = UnitCard(cards[0])
    widget.set_cost(cards[0].cost + 10)
    assert widget.toolTip() == "", "改成本那条就地刷新又挂回了悬浮提示"
    widget.set_name("换了个名字")
    assert widget.toolTip() == "", "改名字那条就地刷新又挂回了悬浮提示"
    widget.set_icon_key("OTHER_LABEL")
    assert widget.toolTip() == "", "换图标那条就地刷新又挂回了悬浮提示"
    widget.deleteLater()


def test_card_name_wraps_instead_of_eliding(qapp) -> None:
    """单位名**换行往下排**（实机 BTR-MDM / Rakushka 两行），不是一句省略号。

    用户："有的名字没有显示全，应该参考游戏内的 GUI，名字往下放展示。"
    只有真超过两行时，最后一行才用省略号。
    """
    from PySide6.QtGui import QFontMetrics

    from ba_mod_maker.app.screens.armory.unit_card import _NAME_LINES, _name_lines
    from ba_mod_maker.app.theme.fonts import ui_font
    from ba_mod_maker.app.theme.tokens import TOKENS

    metrics = QFontMetrics(ui_font(TOKENS.typography.size_xs))
    assert _name_lines(metrics, "BTR-MDM Rakushka", 96.0) == ["BTR-MDM", "Rakushka"]
    assert _name_lines(metrics, "Rangers", 96.0) == ["Rangers"]
    # 一行放不下、两行放得下 -> 两行，且**一个省略号都没有**
    two = _name_lines(metrics, "RQ-4 Global Hawk", 96.0)
    assert len(two) == 2 and not any(line.endswith("…") for line in two), two
    # 真超过两行 -> 最后一行省略号
    long = _name_lines(metrics, "Very Long Unit Name Here Indeed", 96.0)
    assert len(long) == _NAME_LINES and long[-1].endswith("…"), long
    # 中文名没有空格：按字符断
    cjk = _name_lines(metrics, "混血儿M1反坦克导弹", 60.0)
    assert 0 < len(cjk) <= _NAME_LINES and all(cjk)


def test_stat_chips_use_the_in_game_icons(qapp, instance) -> None:
    """数值格改成「图标在上、数字在下、没有文字」—— 实机那样，8 格才挤得进一行。

    文字标签没丢：它挪到 tooltip 的第一行（那里本来就有「标签 值」）。
    """
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    class _FakeIcons(QObject):
        loaded = Signal()

        def pixmap(self, family, key):
            if not key:
                return None
            pixmap = QPixmap(18, 18)
            pixmap.fill(QColor("#ffffff"))
            return pixmap

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(_FakeIcons())
    panel.show_unit(645)
    card = panel.card
    assert all(stat.sprite for stat in card.stats), "每一格都该有实机图标"
    # 图标到位之后：不再有 stat-name 的文字标签，但有对应的悬浮提示
    names = [label for label in panel.findChildren(QLabel) if label.property("role") == "stat-name"]
    assert names == [], "数值格不该再画文字标签"
    from PySide6.QtWidgets import QFrame

    chips = [w for w in panel.findChildren(QFrame) if w.property("role") == "stat-chip"]
    assert any(chip.toolTip().startswith("装甲值 10") for chip in chips), (
        "标签应当还在 tooltip 里：" + str([c.toolTip()[:24] for c in chips])
    )


def test_option_rows_carry_the_picture_and_the_current_choice(qapp, instance) -> None:
    """自定义选项那一排：图 + 组名 + 成本 + 下拉，且下拉**停在当前那一项**上。

    这一栏是卡片上**唯一**能动的控件 —— 换选项看预览（像游戏），不写数据。
    """
    from PySide6.QtWidgets import QComboBox, QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)  # 只看数据接没接上：没有图标来源时文字照旧
    panel.show_unit(645)
    group = panel.card.options[0]
    assert group.current_icon, "当前项应当带 Options.OptionPicture"
    current = next(c for c in group.choices if c.is_current)
    assert current.icon == group.current_icon
    texts = [label.text() for label in panel.findChildren(QLabel)]
    assert group.label in texts, "组名要画出来"
    combos = panel.findChildren(QComboBox)
    assert len(combos) == len(panel.card.options), "一组一个下拉"
    assert combos[0].count() == len(group.choices)
    assert combos[0].currentData() == current.option_id, "下拉要停在当前那一项上"
    assert combos[0].currentText().startswith(group.current_label), "当前选择要看得见"


def test_option_dropdown_changes_the_card_without_touching_data(qapp, instance) -> None:
    """底部「自定义选项」要能像游戏里那样换 —— 换的是**预览**，数据一个字节都不动。

    用户口径（2026-09-29）："虽然卡片是只读了，但是卡片里的自定义选项还是要能像游戏里
    那样能变呀"。实机换改型 = 整张卡重算（成本 / 数值 / 武器都跟着走）。
    """
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    units = instance.table("Units")
    before_rows = [list(row) for row in units.rows]

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)
    panel.show_unit(1)  # BTR-82：武器系统(190) + 装甲组件(191)
    card = panel.card
    assert len(panel.findChildren(QComboBox)) == len(card.options) == 2

    cost_before = card.cost
    optics_before = {s.key: s.value for s in card.stats}["optics"]
    panel.findChildren(QComboBox)[0].setCurrentIndex(1)  # BTR-82A -> BTR-82AT(+10)
    assert panel.card.cost == cost_before + 10, "成本要跟着改型走"
    assert {s.key: s.value for s in panel.card.stats}["optics"] != optics_before
    assert panel.selected_options == {190: 503}

    panel.findChildren(QComboBox)[1].setCurrentIndex(1)  # 附加装甲(+10)
    assert panel.card.cost == cost_before + 20
    assert panel.selected_options == {190: 503, 191: 505}

    # 一个字节都没写：卡片上没有变更集，表也原样
    assert panel.changeset is None
    assert [list(row) for row in units.rows] == before_rows


# --------------------------------------------------------------------------- #
# 左墙成本徽章 <- 右卡（用户："以右边卡片为准"）
# --------------------------------------------------------------------------- #


def _wall_card(screen, unit_id: int):
    """卡墙里那个单位的卡片控件；不在视野里返回 None。"""
    for row in screen.row_widgets:
        widget = row.card_widget(unit_id)
        if widget is not None:
            return widget
    return None


def _click_card(screen, unit_id: int) -> None:
    """点卡片（等价于用户单击）—— 走的是产品里那条真实信号链。"""
    widget = _wall_card(screen, unit_id)
    assert widget is not None, f"单位 {unit_id} 不在当前卡墙里"
    widget.clicked.emit(unit_id)


# --------------------------------------------------------------------------- #
# 卡名 <- 右卡抬头（实机：PT-76 -> PT-76-57）
# --------------------------------------------------------------------------- #


def test_wall_name_follows_the_option_preview(qapp, screen) -> None:
    """卡名也跟右卡走：缺省 PT-76B，选 AU-220M 后**左右一起**变 PT-76-57。

    实机口径（docs/实机-03a/03b）：换装会改名字，而且左列表卡跟着改 ——
    PT-76 选「AU-220M 57毫米炮」之后，左卡与右卡抬头一起变成 PT-76-57。
    名字来自 Options.ReplaceUnitName / ConcatenateWithUnitName（见 unit_card.unit_name）。
    """
    _click_card(screen, 230)
    panel = screen.detail_panel
    assert _wall_card(screen, 230).card.display_name == "PT-76B", "缺省就该是 PT-76B（实机口径）"
    assert panel.current_title == "PT-76B"

    _pick_option(panel, 204, 536)  # 武器系统 -> AU-220M 57毫米炮（+55）

    assert panel.current_title == "PT-76-57"
    assert _wall_card(screen, 230).card.display_name == "PT-76-57"
    # 与成本一样：改的是**显示**，缺省值留着好复位
    assert _wall_card(screen, 230).default_name == "PT-76B"
    assert _wall_card(screen, 44).card.display_name == "Su-57", "别的卡不许跟着改名"


def test_changing_unit_resets_the_wall_name(qapp, screen) -> None:
    """换单位 = 预览清空：左墙那张卡的名字要回到缺省。"""
    _click_card(screen, 230)
    _pick_option(screen.detail_panel, 204, 536)
    assert _wall_card(screen, 230).card.display_name == "PT-76-57"

    _click_card(screen, 44)
    assert _wall_card(screen, 230).card.display_name == "PT-76B", "面板换成 44 了，230 必须回缺省名"

    _click_card(screen, 230)
    assert _wall_card(screen, 230).card.display_name == "PT-76B"
    assert screen.detail_panel.current_title == "PT-76B"


def test_the_default_name_is_the_right_card_title_everywhere(qapp, screen) -> None:
    """整墙逐张：卡名 == 右卡那张卡的抬头（缺省）—— 与成本同一条纪律。

    实机锚：BTR-82 写 BTR-82A、T-72B 写 T-72B1、PT-76 写 PT-76B（截图在 docs/实机-*.png）。
    """
    assert screen.visible_card_count == 395
    for row in screen.row_widgets:
        for card in row.armory_row.cards:
            widget = row.card_widget(card.unit_id)
            assert widget.card.display_name == card.display_name
    assert _wall_card(screen, 1).card.display_name == "BTR-82A"
    assert _wall_card(screen, 9).card.display_name == "T-72B1"
    assert _wall_card(screen, 230).card.display_name == "PT-76B"


def _pick_option(panel, modification_id: int, option_id: int) -> None:
    """在右卡底部某一组下拉里选一项（等价于用户点下拉）。"""
    from PySide6.QtWidgets import QComboBox

    index = next(
        i for i, group in enumerate(panel.card.options) if group.modification_id == modification_id
    )
    combo = panel.findChildren(QComboBox)[index]
    target = next(j for j in range(combo.count()) if combo.itemData(j) == option_id)
    combo.setCurrentIndex(target)


def test_wall_badge_is_the_right_card_cost_by_default(qapp, screen) -> None:
    """缺省时左墙 Su-57 徽章 = **右卡成本 335**，不是 `Units.Cost` 原始列的 225。

    用户原话："右边卡片的数据和左边卡片的数据（主要是价格）不一致，改成一致的，
    以右边卡片为准。" 右卡的公式是 OriginalCost + Σ(缺省选项 Cost)（见
    docs/研究-单位卡片公式.md §5.3），左墙以前直接取 Units.Cost，于是差了 110。
    """
    # 还没点开它的时候，徽章就已经是右卡口径（谁也没预览过）
    assert _wall_card(screen, 44).card.cost == 335
    # 整墙都从卡墙数据出发（此时没有任何预览）
    for row in screen.row_widgets:
        for card in row.armory_row.cards:
            assert row.card_widget(card.unit_id).card.cost == card.cost

    _click_card(screen, 44)
    assert screen.detail_panel.current_unit_id() == 44
    assert screen.detail_panel.current_cost == 335
    assert _wall_card(screen, 44).card.cost == 335, "左墙徽章必须等于右卡的成本"
    # 别的卡也照同一口径（702 = MiG-31K (S)，缺省选项 ±0，与原列同值）
    assert _wall_card(screen, 702).card.cost == 1500


def test_option_preview_updates_that_one_card_only(qapp, screen) -> None:
    """换「自定义选项」→ 左墙那张卡**立刻**变成 415，且**不整屏重建**。

    用户就是在预览里把挂架换成 2xKh59MK2 的：右卡 415、左墙还停在 225，那就是"不一致"的现场。
    卡墙一次几百张卡，整屏 refresh 会重建全部控件（用户对卡顿敏感）。
    """
    _click_card(screen, 44)
    panel = screen.detail_panel
    before = [id(w) for row in screen.row_widgets for w in row.card_widgets]

    _pick_option(panel, 340, 1104)  # 前弹舱 -> 2xKh59MK2巡航导弹(+130, 缺省那项是 +50)

    assert panel.current_cost == 415
    assert _wall_card(screen, 44).card.cost == panel.card.cost == 415
    # 徽章是 415，而这张卡的**缺省**成本仍是 335（复位靠它，不重算数据）
    assert _wall_card(screen, 44).default_cost == 335
    assert _wall_card(screen, 702).card.cost == 1500, "别的卡不许跟着动"
    after = [id(w) for row in screen.row_widgets for w in row.card_widgets]
    assert after == before, "换了选项不该整屏重建卡片（只改那一个数字）"


def test_changing_unit_resets_the_badge(qapp, screen) -> None:
    """换到别的单位再回来 = 335：面板换单位会把预览清空，左边那张卡要跟着回缺省。"""
    _click_card(screen, 44)
    _pick_option(screen.detail_panel, 340, 1104)
    assert _wall_card(screen, 44).card.cost == 415

    _click_card(screen, 702)
    assert _wall_card(screen, 44).card.cost == 335, "面板已经换成 702 了，44 必须回缺省"

    _click_card(screen, 44)
    assert _wall_card(screen, 44).card.cost == 335
    assert screen.detail_panel.current_cost == 335


def test_the_preview_survives_a_full_rebuild(qapp, screen) -> None:
    """整屏重建（refresh / 过滤 / 切专精）之后，预览中的单位仍显示预览值。"""
    _click_card(screen, 44)
    _pick_option(screen.detail_panel, 340, 1104)
    assert _wall_card(screen, 44).card.cost == 415

    screen.refresh()
    assert _wall_card(screen, 44).card.cost == 415, "refresh 把预览抖掉了"
    assert screen.detail_panel.current_cost == 415

    screen.set_filter_text("Su-57")
    assert screen.visible_card_count >= 1
    assert _wall_card(screen, 44).card.cost == 415, "过滤重建之后预览也得在"

    screen.select_specialization(VDV_SPEC_ID)
    assert _wall_card(screen, 44).card.cost == 415
    assert screen.detail_panel.current_unit_id() == 44


def test_the_wall_badge_is_a_preview_not_an_edit(qapp, screen, instance) -> None:
    """徽章变的是**预览**：卡墙数据仍是缺省值，表一个字节都不动。"""
    units = instance.table("Units")
    before_rows = [list(row) for row in units.rows]

    _click_card(screen, 44)
    sheet_card = next(
        card for row in screen.row_widgets for card in row.armory_row.cards if card.unit_id == 44
    )
    _pick_option(screen.detail_panel, 340, 1104)
    assert _wall_card(screen, 44).card.cost == 415

    assert sheet_card.cost == 335, "卡墙数据（armory_row）不该被预览改写"
    assert screen.changeset is not None and screen.changeset.is_empty
    assert [list(row) for row in units.rows] == before_rows


def test_a_replace_unit_option_is_navigation_not_equipment(qapp, instance) -> None:
    """`Options.ReplaceUnitId` 指向别的单位时，那一项是**换单位**：只发 `variant_requested`。

    实测 645 的「小队装备配置」组三项里两项指向别的单位（640 / 108），
    面板自己不跳 —— 跳由军械库决定（目标可能不在当前视野里）。
    """
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)
    seen: list[int] = []
    panel.variant_requested.connect(seen.append)
    panel.show_unit(645)
    panel.findChildren(QComboBox)[0].setCurrentIndex(0)  # 「突击」-> 单位 640
    assert seen == [640]
    assert panel.current_unit_id() == 645, "面板不自己跳"
    assert panel.selected_options == {}, "换单位不是换装备，别记进预览"


def test_stat_chip_has_no_shared_badge(qapp, instance) -> None:
    """**哪里都不再提「共用」**（用户 2026-09-30：不要提示什么单位共用）。

    原来是「数值格上不显示、tooltip 里说」；现在连 tooltip 也不说 ——
    复制一份（原「另存为新模板」）那条路还在，只是不再挂在「共用」这句话上。
    """
    from PySide6.QtWidgets import QFrame, QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)
    panel.show_unit(645)  # 645 的「装甲值 / 生命值」原来都带共用提示
    texts = [label.text() for label in panel.findChildren(QLabel)]
    assert not [text for text in texts if "共用" in text], texts
    assert not [text for text in texts if "个单位用" in text], texts
    chips = [w for w in panel.findChildren(QFrame) if w.property("role") == "stat-chip"]
    assert chips, "一个数值格都没有？"
    leaky = [chip.toolTip() for chip in chips if "共用" in chip.toolTip()]
    assert not leaky, f"tooltip 里还提着共用：{leaky}"


def test_weapon_rows_draw_a_big_crisp_silhouette(qapp, instance) -> None:
    """武器那一格要画**实机尺寸的大剪影**，而且**按 DPR 放大**（不然 150% 下是糊的）。

    两条都是用户报过的：

    - "武器的图标看起来太小了"：原图 231×82（2.8:1），以前塞进 18×18 的方框里缩成一条虫；
    - "小且模糊"：QPixmap 按设备像素算，只缩到逻辑尺寸再交给 Qt，会被拉大 1.5 倍。
    """
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QFrame, QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import (
        WEAPON_ICON_FAMILY,
        UnitCardPanel,
        icon_box,
    )
    from ba_mod_maker.app.theme.tokens import TOKENS

    class _FakeIcons(QObject):
        """只会发一张固定图的 IconCache 替身：断言的是"接线接上了没有"。"""

        loaded = Signal()

        def pixmap(self, family, key):
            if not key:
                return None
            pixmap = QPixmap(231, 82)
            pixmap.fill(QColor("#ffffff"))
            return pixmap

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(_FakeIcons())
    panel.show_unit(645)
    arts = [w for w in panel.findChildren(QFrame) if w.property("role") == "weapon-art"]
    assert len(arts) == 3, "645 有 3 把武器，应当有 3 格"

    box = icon_box(WEAPON_ICON_FAMILY)
    assert box.height() == TOKENS.card.icon_weapon
    assert box.width() >= 90, "剪影的盒子要够宽（实机那把枪是 145×50 物理）"
    for art in arts:
        art_labels = [
            label
            for label in art.findChildren(QLabel)
            if label.pixmap() is not None and not label.pixmap().isNull()
        ]
        assert art_labels, "武器那一格没有剪影"
        art_label = art_labels[0]
        assert art_label.size() == box, "剪影标签的尺寸要按族里的盒子来"
        pixmap = art_label.pixmap()
        ratio = art_label.devicePixelRatioF() or 1.0
        assert pixmap.devicePixelRatio() == ratio, (
            "pixmap 没带 DPR —— Qt 会把它拉大，看起来就是糊的"
        )
        # 高度铺满盒子、宽度按原图比例（231:82 -> 2.82）—— 不是硬拉成方框
        assert pixmap.height() == round(box.height() * ratio)
        assert pixmap.width() <= round(box.width() * ratio)
        assert pixmap.width() >= round(box.height() * ratio * 2.0), "剪影被压窄了"


def test_weapon_silhouette_faces_right_like_the_game(qapp, instance) -> None:
    """武器剪影要**朝右**：原图（`INF_AK74RMO.png` 231×82）朝左，实机卡上是朝右的。

    游戏在 UI 里水平翻了一次，我们也得翻（用户报的"武器在 GUI 里是朝左边的"）。
    这条用一张**不对称的假图**验翻转真的发生了：左半边白、右半边空 —— 翻完应当反过来。
    """
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QFrame, QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    source = QPixmap(100, 40)
    source.fill(QColor(0, 0, 0, 0))
    painter = QPainter(source)
    painter.fillRect(0, 0, 50, 40, QColor("#ffffff"))
    painter.end()

    class _FakeIcons(QObject):
        loaded = Signal()

        def pixmap(self, family, key):
            return source if key else None

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(_FakeIcons())
    panel.show_unit(645)
    arts = [w for w in panel.findChildren(QFrame) if w.property("role") == "weapon-art"]
    assert arts, "一个武器格都没有？"
    label = next(
        x
        for x in arts[0].findChildren(QLabel)
        if x.pixmap() is not None and not x.pixmap().isNull()
    )
    image = label.pixmap().toImage()
    left = image.pixelColor(image.width() // 4, image.height() // 2)
    right = image.pixelColor(image.width() * 3 // 4, image.height() // 2)
    assert left.alpha() < 40, f"左边不该有图（翻转没生效？）left={left.name()}"
    assert right.alpha() > 200, f"右边应当是实心（翻转没生效？）right={right.name()}"
    assert label.layoutDirection() == Qt.LayoutDirection.LeftToRight  # 只是别把整个控件翻了


def test_target_icons_are_not_clipped_by_padding(qapp, instance) -> None:
    """「能打什么」那排图标必须**整张画出来**：标签是按族盒子钉死的（20×20），

    样式表里再加 border/padding 就会把方图裁掉一圈 —— 用户报的"图标没显示全"。
    判据：内容矩形装得下整张图。
    """
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import (
        TARGET_ICON_FAMILY,
        UnitCardPanel,
        icon_box,
    )

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)  # 没有图也能量：盒子是固定的
    panel.show_unit(645)
    panel.set_mode("detail")
    chips = [x for x in panel.findChildren(QLabel) if x.property("role") == "target-chip"]
    assert chips, "一个目标图标都没有？"
    box = icon_box(TARGET_ICON_FAMILY)
    for chip in chips:
        rect = chip.contentsRect()
        assert rect.width() >= box.width(), f"内容区只有 {rect.width()}px，图会被裁"
        assert rect.height() >= box.height(), f"内容区只有 {rect.height()}px，图会被裁"


def test_card_has_no_editing_entry_points(qapp, instance) -> None:
    """卡片上**没有任何写数据的入口**（用户口径：右侧的卡片就展示信息）。

    唯一的例外是底部「自定义选项」的下拉 —— 那是**预览**（用户明确要求保留，像游戏）：
    换选项只重算显示，不落库。所以这里钉两件事：

    1. 没有输入框 / 数字框 / 可编辑文本，也没有增删行那类方法；
    2. 下拉的条数 = 选项组数（不多不少），交互后**没有任何写入**。
    """
    from PySide6.QtWidgets import (
        QAbstractSpinBox,
        QComboBox,
        QLineEdit,
        QTextEdit,
    )

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)
    panel.show_unit(645)
    for kind in (QLineEdit, QTextEdit, QAbstractSpinBox):
        assert not panel.findChildren(kind), f"卡片上出现了编辑控件：{kind.__name__}"
    for gone in ("_apply_edit", "_begin_edit", "_add_member", "_change_ammo_type"):
        assert not hasattr(panel, gone), f"卡片上还留着编辑入口：{gone}"

    combos = panel.findChildren(QComboBox)
    assert len(combos) == len(panel.card.options), "下拉只能是选项那一栏，一组一个"
    assert panel.changeset is None, "卡片不接变更集"

    # 对外信号只有两个：换单位（导航）、选项预览（只读）
    # ❗ 2026-10-02 用户：「把在表格中打开这个按钮取消掉吧」—— 那个信号连同按钮一起删了。
    names = {name for name in dir(panel) if name.endswith("_requested")}
    assert names == {"option_preview_requested", "variant_requested"}, names


def test_target_chips_fall_back_to_text_without_icons(qapp, instance) -> None:
    """没有图标来源（没装游戏、也没缓存）时这一排不能是空白 —— 退回文字。

    空白比多几个字更糟：用户会以为「这把武器什么都不能打」。
    """
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)  # 三个来源全断
    panel.show_unit(645)
    rpg = next(w for w in panel.card.weapons if w.label == "RPG-7V")
    panel._toggle_weapon(rpg.group_key)

    chips = [
        label for label in panel.findChildren(QLabel) if label.property("role") == "target-chip"
    ]
    assert [chip.text() for chip in chips] == [
        "目标 - 载具",
        "目标 - 直升机",
        "目标 - 步兵",
        "目标 - 载具",
        "目标 - 直升机",
    ]
    assert all(chip.pixmap().isNull() for chip in chips)


def test_variant_unit_shows_the_parent_option_group(qapp, screen) -> None:
    """645 自己的 Modifications 是空的：不沿 ReplaceUnitId 这条图，卡片底部就是空的。"""
    panel = screen.detail_panel
    panel.show_unit(645)
    card = panel.card
    assert card is not None
    assert len(card.options) == 1
    assert card.options[0].owner_unit_id == 108
    assert card.options[0].from_variant
    assert card.options[0].current_label == "温压火箭弹"


def test_a_broken_unit_says_why_instead_of_a_stale_card(qapp, instance, monkeypatch) -> None:
    """装配炸了必须**说出来**，不能停在上一个单位上。

    实踩（用户报的"战机那一类点不开，右边还是上一个单位"）：Qt 的槽里抛异常**不会**崩，
    只往 stderr 打一行，界面留在原地 —— 卡片显示的是别的单位，看起来像"这类单位显示不出来"。
    现在：清掉旧卡 + 卡片上写原因 + 发 `card_failed`（状态栏也跟着写）。
    """
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.armory import detail_panel as module
    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.show_unit(353)  # 先正常显示一个（AGS-40 Balkan）
    assert panel.card is not None

    failures: list[tuple[int, str]] = []
    panel.card_failed.connect(lambda uid, why: failures.append((uid, why)))

    def boom(data, unit_id, **kwargs):
        raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(module, "build_unit_card", boom)
    panel.show_unit(702)

    assert panel.current_unit_id() == 702, "选中项要跟着走，别装作没事"
    assert panel.card is None, "旧卡片必须清掉"
    assert failures and failures[0][0] == 702, failures
    texts = [label.text() for label in panel.findChildren(QLabel)]
    assert any("装配失败" in text for text in texts), texts[:6]
    assert not any("AGS-40" in text for text in texts), "上一个单位的卡片不能留着"


def test_validation_badge_is_one_exclamation_with_a_tooltip(qapp, screen, instance) -> None:
    """角标：**只有本轮新冒出来的命中**才画（原版既有违规不报）。

    用户口径（15 条硬约定第 11 条）：打开时基线就有 699 warning / 1036 info，
    540 个单位里 **28 个一打开就带命中**（BTR-82A 自己带 1 条 req.SquadMembers）。
    照"有命中就画"做，每个载具一打开就是红的 —— 这条用例曾经正是那么断言的，
    本轮按用户口径改成"先什么都不画，改坏了才画"。
    """
    panel = screen.detail_panel
    assert panel._findings == {}, "刚打开就冒角标了？原版既有违规不报"

    units = instance.table("Units")
    row = next(r for r in units.rows if r[units.column_index("Id")] == 1)
    column = units.column_index("InfantrySlots")
    keep = row[column]
    row[column] = 2  # 故意改坏：装不下一个班
    try:
        screen._refresh_findings()
        assert panel._findings, "改坏了却不报？"
        flagged = next(iter(panel._findings))
        panel.show_unit(flagged)
        assert panel._badge.text() == "!"
        assert not panel._badge.isHidden()
        assert panel._badge.toolTip(), "问题要写在悬浮提示里"
        assert "Id 1" not in panel._badge.toolTip(), "文案里的裸 Id 要换成名字"

        clean = next(uid for uid in (645, 111, 374) if uid not in panel._findings)
        panel.show_unit(clean)
        assert panel._badge.isHidden(), "没有命中就不该有角标"
    finally:
        row[column] = keep
        screen._refresh_findings()
    assert panel._findings == {}, "改回来了角标还在"


# --------------------------------------------------------------------------- #
# 增删行与换装：加枪 / 换弹种 / 5 人改 6 人（用户定的第 6 条）
# --------------------------------------------------------------------------- #


def test_loadout_section_only_shows_for_infantry(qapp, screen) -> None:
    panel = screen.detail_panel
    panel.show_unit(645)
    assert panel.card.loadout is not None
    assert panel.card.loadout.size == 5
    panel.show_unit(90)  # BMD-2：载具，没有班组成员
    assert panel.card.loadout is None


# --------------------------------------------------------------------------- #
# 左墙缩略图 / 右卡立绘 <- 右卡的选项预览（Options.ThumbnailOverride / PortraitOverride）
# --------------------------------------------------------------------------- #


def test_wall_icon_follows_the_option_preview(qapp, screen) -> None:
    """换「自定义选项」→ 左墙那张卡的缩略图键跟着换，且**缩放缓存必须作废**。

    数据侧的底（tests/test_unit_card.py 钉着）：Stryker ICV(22) 的「武器系统」里选
    Dragoon(4 -> 15) 会带 `Options.ThumbnailOverride = US_ICV_DRAGON-Label`，
    而且 ReplaceUnitId == 0（不换单位）—— 这种只能靠"图跟着选项走"才对。
    """
    _click_card(screen, 22)
    panel = screen.detail_panel
    widget = _wall_card(screen, 22)
    assert widget is not None, "单位 22 不在卡墙里，测不了"
    assert widget.card.icon_key == "US_STRYKER_ICV-Label"
    assert panel.current_icon_key == "US_STRYKER_ICV-Label"
    assert widget.default_icon_key == "US_STRYKER_ICV-Label"

    # 先让它"画过一帧"：缩放缓存填上（换了键不失效的话 paintEvent 会继续用它）
    widget._icon()
    assert widget._scaled_icon is not None

    other_before = _wall_card(screen, 44).card.icon_key
    before = [id(w) for row in screen.row_widgets for w in row.card_widgets]
    _pick_option(panel, 4, 15)  # 武器系统 -> M1296 Dragoon

    assert panel.card.icon_key == "US_ICV_DRAGON-Label"
    assert widget.card.icon_key == "US_ICV_DRAGON-Label"
    assert widget._scaled_icon is None, "换了键必须把缩放缓存作废，否则还画旧图"
    assert _wall_card(screen, 22) is widget, "只改显示，不许重建控件"
    assert [id(w) for row in screen.row_widgets for w in row.card_widgets] == before
    assert _wall_card(screen, 44).card.icon_key == other_before, "别的卡不许跟着换图"


def test_changing_unit_drops_the_wall_icon_preview(qapp, screen) -> None:
    """换单位 = 预览清空：那张卡的缩略图键回到缺省（与成本、卡名同一条路）。"""
    _click_card(screen, 22)
    _pick_option(screen.detail_panel, 4, 15)
    assert _wall_card(screen, 22).card.icon_key == "US_ICV_DRAGON-Label"

    _click_card(screen, 44)
    assert _wall_card(screen, 22).card.icon_key == "US_STRYKER_ICV-Label", (
        "面板已经换成 44 了，22 必须回缺省那张图"
    )
    assert _wall_card(screen, 22)._scaled_icon is None

    _click_card(screen, 22)
    assert _wall_card(screen, 22).card.icon_key == "US_STRYKER_ICV-Label"
    assert screen.detail_panel.current_icon_key == "US_STRYKER_ICV-Label"


def test_wall_icon_preview_survives_a_full_rebuild(qapp, screen) -> None:
    """整屏重建（refresh / 过滤）之后，预览中的单位墙上还是覆盖那张图。"""
    _click_card(screen, 22)
    _pick_option(screen.detail_panel, 4, 15)
    assert _wall_card(screen, 22).card.icon_key == "US_ICV_DRAGON-Label"

    screen.refresh()
    assert _wall_card(screen, 22).card.icon_key == "US_ICV_DRAGON-Label", "refresh 把预览图抖掉了"
    assert screen.detail_panel.current_icon_key == "US_ICV_DRAGON-Label"

    screen.set_filter_text("Stryker")
    assert _wall_card(screen, 22).card.icon_key == "US_ICV_DRAGON-Label", "过滤重建之后也得在"


def test_portrait_follows_the_option_preview_and_is_refetched(qapp, instance) -> None:
    """预览换了立绘 → 界面**拿新键重新取图**；异步到货补上的是新那张。

    数据侧的底：513（Jalaväe Jagu）换成 2140（Gelezinis Vilkas）时
    `Options.PortraitOverride = DLC2/GELEZINIS_VILKAS/GELEZINIS_VILKAS`。
    真图要从 304 MB 的 unitportraits 包里取，测试用替身只记"要过哪些键"。

    ❗ 这条路只能用 `set_selected_options`（它就是"设预览"的那个 API）：
    立绘覆盖那 23 条**全部**带 ReplaceUnitId，走下拉的话 `_on_option_picked` 会判成
    "换单位"（发 `variant_requested` 去导航），压根到不了重画这一步。
    两条路最后都汇到 `_render` -> `_portrait_section`，所以这里测的正是换图那条取图路径。
    """
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QPixmap

    from ba_mod_maker.app.screens.armory.detail_panel import PORTRAIT_FAMILY, UnitCardPanel

    class _SpyIcons(QObject):
        loaded = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.asked: list[tuple[str, str]] = []
            self.ready: dict[str, QPixmap] = {}

        def pixmap(self, family: str, key: str) -> QPixmap | None:
            self.asked.append((family, key))
            return self.ready.get(key)

    icons = _SpyIcons()
    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(icons)
    panel.show_unit(513)

    old_key = "DLC2/JALAVAE_JAGU/JALAVAE_JAGU"
    new_key = "DLC2/GELEZINIS_VILKAS/GELEZINIS_VILKAS"
    assert panel.current_portrait_key == old_key
    assert (PORTRAIT_FAMILY, old_key) in icons.asked, "立绘要按当前键去取图"
    assert not panel._portrait_view.has_pixmap()

    panel.set_selected_options({602: 2140})  # 换到 Gelezinis Vilkas（只改预览）

    assert panel.current_portrait_key == new_key
    assert panel.card.portrait_key == new_key
    assert (PORTRAIT_FAMILY, new_key) in icons.asked, "重画时必须拿新键再取一次图，不能沿用上一张"

    # 异步到货：晚到的那张图要补在**新的**立绘上
    icons.ready[new_key] = QPixmap(8, 8)
    icons.loaded.emit()
    assert panel._portrait_view.has_pixmap()


# --------------------------------------------------------------------------- #
# 装甲图（地面载具立绘四角那 4 块）—— 界面侧：真的画出来了没有
# --------------------------------------------------------------------------- #


def _armor_panel(qapp, instance, unit_id: int):
    """一块只装了右卡的离屏面板（不开窗、不抢鼠标），已经画好某个单位。"""
    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = UnitCardPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.set_instance(instance)
    panel.resize(390, 1200)
    panel.show()
    panel.show_unit(unit_id)
    _settle(qapp)
    return panel


def _armor_labels(panel) -> dict[str, list]:
    """画在右卡上的装甲图标签，按 role 分组（顺序 = 建的顺序 = 部位顺序）。"""
    from PySide6.QtWidgets import QLabel

    groups: dict[str, list] = {"armor-zone-name": [], "armor-kinetic": [], "armor-heat": []}
    for label in panel.findChildren(QLabel):
        role = label.property("role")
        if role in groups:
            groups[role].append(label)
    return groups


def _nearest_color(image, panel, widget, target) -> float:
    """在 widget 那块区域里找与 target 最接近的像素，返回 RGB 色距（0 = 一模一样）。

    ❗ 不能取标签正中心那一个像素：两个汉字之间、两个数字之间正好是空隙。
    """
    top_left = widget.mapTo(panel, QPoint(0, 0))
    sx = image.width() / max(1, panel.width())
    sy = image.height() / max(1, panel.height())
    best = 1e9
    for y in range(widget.height()):
        for x in range(widget.width()):
            color = image.pixelColor(int((top_left.x() + x) * sx), int((top_left.y() + y) * sy))
            distance = (
                (color.red() - target.red()) ** 2
                + (color.green() - target.green()) ** 2
                + (color.blue() - target.blue()) ** 2
            )
            best = min(best, distance)
    return best**0.5


def test_vehicle_card_draws_the_armor_zones(qapp, instance, styled) -> None:
    """M1A2 的右卡上**真的画出** 4 个部位 × 2 个数（不是只有模型里有）。

    用户给的实机截图：顶部 60/60、背面 100/100、正面 750/1300、侧面 120/250，
    部位名小字在上、动能白字、破甲橙字，整块压在立绘上。
    """
    from PySide6.QtGui import QColor

    from ba_mod_maker.app.theme.tokens import TOKENS

    panel = _armor_panel(qapp, instance, 2)
    labels = _armor_labels(panel)

    assert [label.text() for label in labels["armor-zone-name"]] == [
        "顶部",
        "背面",
        "正面",
        "侧面",
    ]
    assert [label.text() for label in labels["armor-kinetic"]] == ["60", "100", "750", "120"]
    assert [label.text() for label in labels["armor-heat"]] == ["60", "100", "1300", "250"]
    for role, group in labels.items():
        assert len(group) == 4, role
        for label in group:
            assert label.isVisible(), (role, label.text())
            assert label.width() > 0 and label.height() > 0, (role, label.text())

    # 压**在立绘上**：四块都落在立绘框里，且叠在立绘之上（Qt 里后建的在上层）
    view = panel._portrait_view
    frame = view.parentWidget()
    children = list(frame.children())
    zones = [
        child
        for child in children
        if hasattr(child, "property") and child.property("role") == "armor-zone"
    ]
    assert len(zones) == 4
    assert children.index(view) < min(children.index(zone) for zone in zones), "装甲图被立绘盖住了"
    for zone in zones:
        assert frame.rect().contains(zone.geometry()), (zone.geometry(), frame.rect())

    # 颜色是**画出来的**：橙（破甲）与白（动能）都得在立绘上找得到 —— 只看模型测不到这条。
    image = panel.grab().toImage()
    orange = min(
        _nearest_color(image, panel, label, QColor(TOKENS.color.armor_heat))
        for label in labels["armor-heat"]
    )
    assert orange < 40, (
        f"破甲数字没有画出橙色（最接近的像素差 {orange:.0f}，"
        f"tokens 里是 {TOKENS.color.armor_heat}）"
    )
    white = min(
        _nearest_color(image, panel, label, QColor(TOKENS.color.text_strong))
        for label in labels["armor-kinetic"]
    )
    assert white < 40, f"动能数字没有画出白色（最接近的像素差 {white:.0f}）"
    panel.deleteLater()


def test_armor_zones_follow_the_armor_option_on_the_card(qapp, instance, styled) -> None:
    """换「自定义选项 -> 装甲组件」这块图跟着重算（Options.ArmorId 那条链）。

    BRDM-3 缺省 20/20、20/20、30/30、20/20（与 docs/实机-04b-BRDM-3.png 逐格一致），
    换成 Uparmored（选项 509，ArmorId=167）后是 20/20、30/120、60/150、40/120。
    """
    panel = _armor_panel(qapp, instance, 217)
    before = _armor_labels(panel)
    assert [label.text() for label in before["armor-kinetic"]] == ["20", "20", "30", "20"]
    assert [label.text() for label in before["armor-heat"]] == ["20", "20", "30", "20"]

    _pick_option(panel, 193, 509)  # 装甲组件：默认 -> Uparmored（+10）
    _settle(qapp)

    after = _armor_labels(panel)
    assert [label.text() for label in after["armor-kinetic"]] == ["20", "30", "60", "40"]
    assert [label.text() for label in after["armor-heat"]] == ["20", "120", "150", "120"]
    # 部位名与顺序不变（只换数）
    assert [label.text() for label in after["armor-zone-name"]] == [
        "顶部",
        "背面",
        "正面",
        "侧面",
    ]
    panel.deleteLater()


def test_infantry_card_draws_no_armor_zones(qapp, instance, styled) -> None:
    """非载具（645 步兵）**一个部位都不画** —— 但立绘照旧在。"""
    panel = _armor_panel(qapp, instance, 645)
    labels = _armor_labels(panel)
    assert all(not group for group in labels.values()), labels
    assert panel._portrait_view is not None
    assert panel._portrait_view.height() > 0
    panel.deleteLater()


def test_armory_screen_shows_the_armor_zones_for_a_vehicle(qapp, screen, styled) -> None:
    """整条路都通：从卡墙点一张载具 -> 右卡上真的出现装甲图。"""
    screen.resize(1400, 800)
    screen.show()
    _settle(qapp)
    _click_card(screen, 2)  # M1A2 SEP v2 TUA（用户截图那张）
    _settle(qapp)

    labels = _armor_labels(screen.detail_panel)
    assert [label.text() for label in labels["armor-zone-name"]] == [
        "顶部",
        "背面",
        "正面",
        "侧面",
    ]
    assert [label.text() for label in labels["armor-kinetic"]] == ["60", "100", "750", "120"]
    assert [label.text() for label in labels["armor-heat"]] == ["60", "100", "1300", "250"]
    screen.hide()


# --------------------------------------------------------------------------- #
# 只读卡（单位编辑器右栏）：不再横向滚动 + 「放大 / 缩小」（task-3）
# --------------------------------------------------------------------------- #


def _readonly_panel(qapp, instance, unit_id: int, width: int = 419):
    """一块只装了**单位编辑器那张只读卡**的离屏面板，已经画好某个单位。

    419 = 用户那台机器上右栏的实际逻辑宽度（实机截图 628 物理 / 150%）。
    """
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    panel = ReadOnlyCardPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.set_instance(instance)
    panel.set_icons(None)
    panel.resize(width, 1200)
    panel.show()
    panel.show_unit(unit_id)
    _settle(qapp)
    return panel


def _scroll_metrics(panel):
    from PySide6.QtWidgets import QScrollArea

    scroll = panel.findChild(QScrollArea)
    return scroll, scroll.horizontalScrollBar().maximum(), scroll.viewport().width()


def _grid_tables(panel) -> list:
    """这张卡上"列对齐那张简略表"有几张（0 = 用的是一行一条那种排法）。"""
    from PySide6.QtWidgets import QGridLayout

    return [
        widget
        for widget in panel.findChildren(QWidget)
        if widget.property("role") == "compact-table" and isinstance(widget.layout(), QGridLayout)
    ]


@pytest.mark.parametrize("unit_id", [2, 645, 374, 92, 7])
@pytest.mark.parametrize("width", [390, 419, 520])
def test_readonly_card_never_scrolls_sideways(qapp, instance, styled, unit_id, width) -> None:
    """用户原话："右边的卡片显示不全，不要让我手动向右向左滑，这个观感很不好。"

    两条判据缺一不可：

    1. 横向滚动条的 range 是 **0**（不是"藏起来"—— 藏起来只会把右边的数裁掉）；
    2. 立绘那一帧整个装在视口里（实机被裁掉的「背面 100/100」「侧面 120/250」
       就是它：帧比视口宽，右半边连同压在角上的数字一起跑到滚动区外面）。

    样本覆盖四种最容易把卡撑宽的形态：载具（装甲图 + 7 格）、步兵（8 格 + 班组成员）、
    固定翼（4 把武器 + 4 个目标图标）、火箭炮（7 格）。390 = 面板自己的最小宽。
    """
    panel = _readonly_panel(qapp, instance, unit_id, width)
    _scroll, hmax, viewport = _scroll_metrics(panel)
    body = panel._body.minimumSizeHint().width()
    assert hmax == 0, f"#{unit_id} @{width}px 还在横向滚动：{hmax}px（正文最小宽 {body}）"
    portrait = panel._portrait_view.parentWidget()
    assert portrait.width() <= viewport, (
        f"#{unit_id} @{width}px：立绘帧 {portrait.width()}px 比视口 {viewport}px 宽，右侧会被裁"
    )
    panel.deleteLater()


def test_armory_card_is_left_exactly_as_it_was(qapp, instance, styled) -> None:
    """用户硬口径："军械库设计还是原来的好"。

    自适应那个开关**默认是关的**，打开它的只有只读卡 —— 所以军械库那张：
    没有档位、连那一排控件都不建、武器区还是原来那张列对齐的表格。
    """
    from PySide6.QtWidgets import QPushButton

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    assert UnitCardPanel.fit_to_width is False, "默认必须是关的"
    assert UnitCardPanel.zoom_levels == (), "默认不画那排按钮"
    panel = _armor_panel(qapp, instance, 2)
    assert panel._zoom_row is None
    chips = [b for b in panel.findChildren(QPushButton) if b.property("role") == "chip"]
    assert all(not b.text().endswith("%") for b in chips), (
        f"军械库那张卡上冒出了档位按钮：{[b.text() for b in chips]}"
    )
    assert len(_grid_tables(panel)) == 1, "简略武器区必须还是那张列对齐的表格"
    panel.deleteLater()


def test_zoom_switch_lives_only_on_the_readonly_card(qapp, instance, styled) -> None:
    """「放大 / 缩小」开关：只在只读卡上，文案里没有省略号，当前档位看得出来。

    role 是字面量 **card-zoom** —— tests/test_relation_tree.py 那条"只读卡上没有
    可点的编辑入口"的不变量就是按它放行这一排按钮的，改名会把那条用例打红。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import ZOOM_LEVELS

    panel = _readonly_panel(qapp, instance, 645)
    row = panel._zoom_row
    assert row is not None and row.property("role") == "card-zoom"
    buttons = [button for _level, button in panel._zoom_buttons]
    assert [b.text() for b in buttons] == ["100%", "90%", "80%", "70%"]
    for button in buttons:
        assert "…" not in button.text() and "..." not in button.text(), button.text()
    assert [b.text() for b in buttons if b.isChecked()] == ["100%"], "缺省是最大那一档"
    assert panel.zoom_levels == ZOOM_LEVELS == (100, 90, 80, 70)
    panel.deleteLater()


def test_zoom_levels_resize_the_card_and_keep_it_inside(qapp, instance, styled) -> None:
    """换档：**卡片真的变小**（立绘宽可量），而且每一档都仍然不横向滚动。

    立绘是 heightForWidth 的（宽 = 视口宽 × 档位），所以它是"整张卡等比缩了"的
    最直接证据 —— 用户要的也正是这个观感。
    """
    panel = _readonly_panel(qapp, instance, 2, 419)
    widths: list[int] = []
    for level in (100, 90, 80, 70):
        panel.set_zoom(level)
        _settle(qapp)
        _scroll, hmax, viewport = _scroll_metrics(panel)
        assert hmax == 0, f"{level}% 下又出现了横向滚动条"
        got = panel._portrait_view.width()
        assert got <= viewport, f"{level}%：立绘 {got}px 超出视口 {viewport}px"
        widths.append(got)
        checked = [b.text() for _l, b in panel._zoom_buttons if b.isChecked()]
        assert checked == [f"{level}%"], f"按钮没说自己在哪一档：{checked}"
    assert widths == sorted(widths, reverse=True), widths
    assert widths[0] > widths[-1], widths
    assert abs(widths[-1] / widths[0] - 0.7) < 0.05, f"70% 缩得不到位：{widths}"
    panel.deleteLater()


def test_zoom_fonts_follow_the_tokens(qapp, instance, styled) -> None:
    """档位的字号必须与 tokens 算出来的一致（QSS 里没法做乘法，只能钉一遍）。

    那几组 px 在 theme/qss.py 的 [right-card] 块里是**写死**的（模板不支持运算）。
    这条按 tokens 现算一遍再和渲染出来的量对照：改 tokens 而没改 QSS 会立刻红 ——
    否则症状是"图标缩了、字没缩"，光看图很难发现。
    """
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.armory.detail_panel import ZOOM_FONT_FLOOR
    from ba_mod_maker.app.theme.tokens import TOKENS

    probes = {
        "detail-title": TOKENS.card.font_title,
        "detail-section": TOKENS.card.font_section,
        "stat-number": TOKENS.card.font_number,
        "weapon-name": TOKENS.card.font_body,
        "compact-head": TOKENS.card.font_column,
        "card-note": TOKENS.typography.size_xs,
        "empty-hint": TOKENS.typography.size_sm,
        "armor-kinetic": TOKENS.card.font_armor,
    }
    # 两个样本：载具（有装甲图那几号字）+ 步兵（有小字注释）。
    panels = [_readonly_panel(qapp, instance, 2), _readonly_panel(qapp, instance, 645)]
    for level in (100, 90, 80, 70):
        for each in panels:
            each.set_zoom(level)
        _settle(qapp)
        got: dict[str, int] = {}
        for each in panels:
            for label in each.findChildren(QLabel):
                role = label.property("role")
                if role in probes:
                    got.setdefault(role, label.font().pixelSize())
        missing = sorted(set(probes) - set(got))
        assert not missing, f"这两个样本上没找到这些 role：{missing}（探针该换了）"
        for role, base_px in probes.items():
            want = (
                base_px if level == 100 else max(ZOOM_FONT_FLOOR, int(base_px * level / 100 + 0.5))
            )
            assert got.get(role) == want, (
                f"{role} @{level}%：渲染成 {got.get(role)}px，tokens 算出来该是 {want}px"
            )
    for each in panels:
        each.deleteLater()


def test_readonly_card_keeps_the_aligned_table_when_there_is_room(qapp, instance, styled) -> None:
    """折行是**放不下才发生**的：宽栏上仍旧用那张列对齐的表格（用户要的"并列"）。

    实测：645 那张表的最小宽 421px，视口 ~384 时放不下 -> 换成一行一条；
    栏拉到 700px 时放得下 -> 回到表格。两个方向都钉住，免得"一刀切换掉"。
    """
    narrow = _readonly_panel(qapp, instance, 645, 390)
    mid = _readonly_panel(qapp, instance, 645, 520)
    wide = _readonly_panel(qapp, instance, 645, 900)
    forms = [bool(_grid_tables(panel)) for panel in (narrow, mid, wide)]
    assert forms[2], "900px 的栏里放得下，就该回到列对齐那张表"
    assert forms == sorted(forms), f"栏越窄反而越早用表格：{forms}"
    for panel in (narrow, mid, wide):
        panel.deleteLater()
