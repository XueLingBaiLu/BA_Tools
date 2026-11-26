"""编成台（单位修改 GUI 重做）· **骨架**用例。

这一份守的是**用户在真机上走的那条路**：军械库双击一个步兵单位 → 落在编成台。
AGENTS §0.6 的教训：用例全绿在"直接调 API"上、没人问过"用户能不能点到" ——
所以这里一律从 `window._open_unit_editor()`（= 双击那条路）进，不自己去 open_unit。

各块自己的用例在 tests/test_bench_squad.py / test_bench_loadout.py / test_bench_slots.py
（属性那五栏 ＋ 改装槽都在 test_bench_slots.py 里）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from ba_mod_maker.app.screens.editor.bench_page import (
    CIRCLED,
    KIND_AIR,
    KIND_SQUAD,
    KIND_VEHICLE,
    bench_kind,
)
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.app.widgets.bench_hardpoints import HardpointsBench
from ba_mod_maker.app.widgets.bench_kit import BenchRow, BenchSection, SlotCard
from ba_mod_maker.app.widgets.bench_loadout import LoadoutBench
from ba_mod_maker.app.widgets.bench_slots import SlotsBench, StatsBench
from ba_mod_maker.app.widgets.bench_squad import SquadBench
from ba_mod_maker.app.widgets.bench_transport import TransportBench
from ba_mod_maker.app.widgets.bench_turrets import TurretsBench
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

#: 编成台上「＋ 加一项」那一排按钮的框色（TOKENS.color.border_strong）——
#: 主动作与普通动作**同一套**。2026-10-02 用户：「这三个按钮和其它按钮保持一致，
#: 即框的颜色要一致」（那时主动作那条 QSS 用的是 #4c6ef5 蓝框）。
EXPECTED_ACTION_FRAME = "#333b48"
#: 同一排按钮的**底色**（TOKENS.color.bg_elevated）—— 主动作也一样（2026-10-02「底色也拉平」）。
EXPECTED_ACTION_FILL = "#1d222b"

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 三个形态的样本（实例 52085 实测）：
#: - 步兵 Marine Raiders（14 名兵 / 4 件班用武器）；
#: - 载具 BTR-ZD Skrezhet（设计稿那张，3 座炮塔）；
#: - 固定翼 F-15E Strike Eagle（设计稿那张，5 个改装槽共 31 个选项）；
#: - 直升机 Mi-8MTV-5（**FlyPresetId 是 0**，只能靠 Units.Type 判成 air —— 见 bench_page._flies）。
INFANTRY = 70
VEHICLE = 206
AIRCRAFT = 287
HELICOPTER = 7
#: 用户 2026-10-02 截图那台（③装甲 ④机动 ⑤能力 ⑥传感器 四栏都有行）—— 选中互斥那两条用它。
STRYKER = 21


# --------------------------------------------------------------------------- #
# 形态判据（零 Qt）
# --------------------------------------------------------------------------- #


def test_bench_kind_reads_the_data_not_the_enum(instance) -> None:
    """判据是「有没有班组成员」，不是 Units.Type 的数字。"""
    data = CardData(instance)
    assert bench_kind(data, INFANTRY) == KIND_SQUAD
    assert bench_kind(data, VEHICLE) == KIND_VEHICLE
    assert bench_kind(data, -1) == ""


# --------------------------------------------------------------------------- #
# 用户路径：双击 / 切换钮 / 兜底
# --------------------------------------------------------------------------- #


def test_nothing_in_the_middle_pane_carries_a_tooltip(window) -> None:
    """用户 2026-10-03（连报两回）：「**我叫你不要在中间显示悬浮窗注释 / 我要你在右边显示悬浮窗
    注释**」＋「**把这个注释也给我去掉**」（那一张指的是块抬头上的「点一下收起」）。

    规矩只有一条、也只用**一条尺子**量：**编成台（中栏）里任何控件都不挂 tooltip**；
    注释（全名、表.列）由**右栏**承担 —— 右栏本身排除在外（那一侧相反：每一条词条都
    **必须**有提示，见 test_bench_detail::test_every_rail_key_carries_its_annotation）。
    """
    window._open_unit_editor(STRYKER)
    page = window._unit_editor.bench_page()
    # ⚠️ **中栏 = page._scroll**（_attach_entries：中栏插在左栏与右栏之间）。
    #    别拿整个 page 当尺子 —— 左栏是那张卡（护甲区 / 属性格子本来就该有注释浮窗），
    #    右栏是 BenchRail（那边**每一条都必须有**）。
    middle = page._scroll
    #: ❗ 量完必须**还原**：window 是**模块级**夹具（这个页面跨用例活着），展开态会被后面
    #:    「三形态每一块默认收起」那几条看见。实测（2026-10-18）：这条把 STRYKER 的
    #:    TurretsBench 留成展开 ⇒ `test_every_bench_block_starts_collapsed[206-vehicle]` 必红
    #:    （单独跑那一条是绿的）。所以记原状态、量完还原：这条用例**不许改变界面状态**。
    sections = list(middle.findChildren(BenchSection))
    was = [section.is_collapsed() for section in sections]
    for section in sections:
        section.set_collapsed(False)
    offenders: list[str] = []
    for widget in middle.findChildren(QWidget):
        if widget.toolTip():
            shown = widget.text()[:14] if hasattr(widget, "text") else ""
            offenders.append(f"{type(widget).__name__}({shown!r})")
    # 量完了 —— 先还原成量之前的样子，再断言（断言变红也不许把展开态留给下一条）。
    for section, collapsed in zip(sections, was, strict=True):
        section.set_collapsed(collapsed)
    assert not offenders, f"中栏还有挂着提示的控件：{offenders[:8]}"


def test_double_click_lands_on_the_bench(window) -> None:
    """步兵单位双击进来，落的是编成台，面包屑最后一段写着「编成台」。"""
    window._open_unit_editor(INFANTRY)
    editor = window._unit_editor
    assert editor.on_bench_page(), "双击步兵单位应该落在编成台"
    assert editor.trail()[-1] == "编成台"


def test_a_vehicle_lands_on_its_own_bench(window) -> None:
    """载具这一轮**有编成台了** —— 落点从关系树页改成它自己那张。

    ❗ 这一条原来叫 `test_vehicle_still_opens_the_tree_page`，守的是「载具暂时进关系树页」
    （那一轮的注释写着「半成品界面比旧界面更糟」）。三种形态都实现之后那条前提**作废**，
    所以这里改守**新**口径 —— **不是把用例删掉**。
    """
    window._open_unit_editor(VEHICLE)
    editor = window._unit_editor
    assert editor.on_bench_page(), "载具双击进来就该落在编成台（不用再点切换钮）"
    assert editor.current_bench_kind() == KIND_VEHICLE
    assert not window._bench_toggle_button.isHidden(), "有编成台的形态就该露切换钮"
    assert editor.show_bench(), "点顶栏那颗切换钮必须进得去"
    page = editor.bench_page()
    # ① 栏 = 挂载槽（用户 2026-11-11），② 栏才是炮塔层级 —— 既不是"随便画了点什么"，
    # 更不是步兵那 14 格。
    sections = page.sections()
    assert isinstance(sections[0], HardpointsBench), sections
    assert isinstance(sections[1], TurretsBench), sections

    # ✅ 2026-10-02：**默认就落上去**这条闸补回来了。
    #    （那道"只放步兵"的临时闸是为了躲 shiboken 的 AV，病灶已定位在
    #     `QListWidget.clear()` 这条回收路径上、5 处已换成 takeItem 循环；
    #     见 unit_editor_screen.land_on_bench 与交接档 §6。）


def test_an_aircraft_lands_on_its_own_bench(window) -> None:
    """固定翼 → ① 栏是 **21 格挂载图**（用户 2026-10-01 口径：直升机 + 固定翼 = 挂载图）。"""
    window._open_unit_editor(AIRCRAFT)
    editor = window._unit_editor
    assert editor.current_bench_kind() == KIND_AIR
    assert editor.show_bench()
    assert isinstance(editor.bench_page().sections()[0], HardpointsBench)


def test_a_helicopter_lands_on_the_hardpoint_bench_too(window) -> None:
    """直升机也走挂载图 —— 它的 `Mobility.FlyPresetId` 是 **0**，只能靠 Units.Type(8) 判。

    这一条专门钉住 `_flies` 那条"两条判据取并集"：只用 FlyPresetId 的话，Mi-8 会掉进载具形态。
    """
    window._open_unit_editor(HELICOPTER)
    editor = window._unit_editor
    assert editor.current_bench_kind() == KIND_AIR
    assert editor.show_bench()
    assert isinstance(editor.bench_page().sections()[0], HardpointsBench)


def test_the_add_hardpoint_button_targets_the_first_free_slot(window) -> None:
    """⭐ 回归（用户 2026-10-02：「加一个挂载不知道怎么用」）：那颗 ＋ 必须落到**第一个空槽**。

    病灶（已修）：`self._add.clicked.connect(self.begin_add)` —— Qt 把 `clicked` 的
    `checked=False` 当第一个实参塞进 `slot`，于是「＋ 加一个挂载」永远去查**槽 0**。
    实测 F-15E（单位 287）第一个空槽是 **7**（Ka-52 是 3），而槽 0 是「不属于任何改装槽」
    ⇒ 永远弹「槽 0 已经占着了 —— 先 ✕ 掉它再挂」，21 个格子一个也挂不上。
    """
    window._open_unit_editor(AIRCRAFT)
    editor = window._unit_editor
    editor.show_bench()
    block = editor.bench_page().sections()[0]
    assert isinstance(block, HardpointsBench)
    block.section().set_collapsed(False)  # 候选列表挂在主体里：先展开，不然 picker_visible 恒 False
    view = block.view()
    assert view is not None
    expected = view.first_free_slot
    assert expected > 0, "样本变了：第一个空槽不该是 0 —— 那样这条用例就没有鉴别力了"

    said: list[str] = []
    block.status_message.connect(said.append)

    QTest.mouseClick(block.add_button(), Qt.MouseButton.LeftButton)

    assert not any("已经占着了" in text for text in said), f"还在拿槽 0 去查：{said}"
    assert block.picker_visible() is True, "＋ 没把候选列表冒出来"
    assert block.selected_slot() == expected, (
        f"落点不是第一个空槽：{block.selected_slot()} != {expected}"
    )
    block.close_picker()
    # ❗ 收尾折回去：window 是**模块级**夹具（这个页面跨用例活着），留着展开态会污染
    #    后面"三形态每一块默认收起"那几条（串行跑时顺序固定，必红；xdist 下靠分 worker 侥幸不撞）。
    block.section().set_collapsed(True)


#: 中栏各形态的**类型**顺序（用户 2026-10-02 口径 1：③ 拆成装甲/机动/能力，
#: 传感器与专精可用性各一栏，⑤ 其他入口整块删掉；同日飞机又加了「炮塔 › 武器 › 弹种」那块）。
#: 步兵（8）：班成员 / 班用武器 / 装甲 / 机动 / 能力 / 传感器 / 专精可用性 / 改装槽。
#: 载具（9）：**挂载槽** / 炮塔层级 / 改装槽 / 装甲 / 机动 / 能力 / 传感器 / 专精可用性 / 运输。
#: 飞机（9）：**挂载槽** / 改装槽 / **炮塔层级** / 装甲 / 机动 / 能力 / 传感器 / 专精可用性 / 运输。
SQUAD_ORDER = (
    SquadBench,
    LoadoutBench,
    StatsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    SlotsBench,
)
VEHICLE_ORDER = (
    # ❗ 2026-11-11 用户：「让所有载具都显示这些挂载槽」—— 载具也有 ① 挂载槽了。
    HardpointsBench,
    TurretsBench,
    SlotsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    TransportBench,
)
AIR_ORDER = (
    HardpointsBench,
    SlotsBench,
    # ❗ 用户 2026-10-02（截图停在「炮塔模板 #725」上）：
    #    「**在改装槽下，我还要能继续修改武器和弹药**」——
    #    飞机/直升机也摆上那块共用的「炮塔 › 武器 › 弹种」（摆在 ② 改装槽之后）。
    TurretsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    StatsBench,
    TransportBench,
)


@pytest.mark.parametrize(
    ("unit_id", "kind", "want"),
    [
        (INFANTRY, KIND_SQUAD, SQUAD_ORDER),
        (VEHICLE, KIND_VEHICLE, VEHICLE_ORDER),
        (AIRCRAFT, KIND_AIR, AIR_ORDER),
    ],
)
def test_the_mid_column_order_is_the_one_the_design_asks_for(window, unit_id, kind, want) -> None:
    """中栏各形态的块与顺序（用户 2026-10-02 口径 1）。

        步兵（8）①班 ②班用武器与弹药 ③装甲 ④机动 ⑤能力 ⑥传感器 ⑦专精可用性 ⑧改装槽
        载具（9）①挂载槽 ②炮塔层级 ③改装槽 ④装甲 ⑤机动 ⑥能力 ⑦传感器 ⑧专精可用性 ⑨运输
        飞机（9）①挂载槽 ②改装槽 ③炮塔层级 ④装甲 ⑤机动 ⑥能力 ⑦传感器 ⑧专精可用性 ⑨运输

    ❗ 顺序**和类型都**要查：只查顺序的话，把 SlotsBench 摆在 ② 而实际画的是别的块也能过。
    ❗ 抬头圈码必须**按位置生成**：真机上出现过"改装槽摆在 ② 却写着 ④"、一屏两个 ④。
    """
    window._open_unit_editor(unit_id)
    editor = window._unit_editor
    assert editor.current_bench_kind() == kind
    assert editor.show_bench()
    sections = editor.bench_page().sections()
    assert len(sections) == len(want)
    assert tuple(type(s) for s in sections) == want, [type(s).__name__ for s in sections]

    # 每一块抬头那个圈码 = 它摆第几块（bench_page._number_sections 现生成）。
    for index, block in enumerate(sections):
        section = _bench_section_of(block)
        assert section.title_text().startswith(CIRCLED[index]), (
            f"第 {index + 1} 块（{type(block).__name__}）抬头写着 {section.title_text()!r}"
        )

    # 属性那五栏**各画各的**（不是五块都画三类）—— 按 kinds 读，钉住"一栏一类"。
    stats_blocks = [s for s in sections if isinstance(s, StatsBench)]
    assert len(stats_blocks) == 5
    assert [tuple(kind.category for kind in block.kinds()) for block in stats_blocks] == [
        ("装甲",),
        ("机动",),
        ("能力",),
        ("传感器",),
        ("专精可用性",),
    ]

    # 改装槽那一块：步兵摆 ⑧；飞机摆 ②；载具摆 ③（2026-11-11 载具 ① 多了挂载槽，顺延）。
    slots = next(s for s in sections if isinstance(s, SlotsBench))
    expected_mark = {KIND_SQUAD: "⑧", KIND_VEHICLE: "③", KIND_AIR: "②"}[kind]
    position = sections.index(slots) + 1
    assert slots.section_title().startswith(expected_mark), (
        f"改装槽抬头 {slots.section_title()!r} 与它摆的位置（第 {position} 块）对不上"
    )


def test_the_toggle_button_switches_both_ways(window) -> None:
    """顶栏那颗「编成台 / 关系树」：两个顶层页都得点得到（关系树是兜底，不许藏死）。"""
    window._open_unit_editor(INFANTRY)
    editor = window._unit_editor
    button = window._bench_toggle_button
    assert not button.isHidden(), "步兵该看得到切换钮"
    assert button.text() == "🌲 关系树"

    button.click()
    assert editor.current_page is editor.main_page, "切过去应该是关系树那一页"
    assert button.text() == "🔧 编成台"

    button.click()
    assert editor.on_bench_page(), "再点一次要回得来"
    assert button.text() == "🌲 关系树"


def test_the_other_entries_block_is_gone_and_the_tree_still_has_a_way(window) -> None:
    """⭐ 第 ⑤ 块「其他入口」**整块删掉**（用户 2026-10-02：「下面这个其它入口就可以不需要了」）。

    这一条**不是把旧用例删掉**，是把它翻成负向断言 ＋ 仍然要成立的那一半：

    - 负向：那一块连装配接口都不在了（四个口子全没），中栏也不许再有 bench-chip 胶囊；
    - 仍要成立：它当时唯一能真去的地方（关系树 = 全库兜底）**照旧能去** ——
      改走顶栏那颗切换钮（那条路另有 test_the_toggle_button_switches_both_ways 守着）。

    改前这一条的名字是 test_the_tree_stays_reachable_from_the_bench_chip。
    """
    window._open_unit_editor(INFANTRY)
    editor = window._unit_editor
    page = editor.bench_page()
    for name in ("OTHER_ENTRIES", "entries_section", "other_entries_chips", "click_other_entry"):
        assert not hasattr(page, name), f"⑤ 其他入口已经整块删掉，{name} 不该还在"
    chips = [
        widget for widget in page.findChildren(QWidget) if widget.property("role") == "bench-chip"
    ]
    assert chips == [], "编成台上不该再有中栏胶囊（⑤ 那一块唯一的产物）"
    # 兜底入口照旧能去（用户口径没变：关系树是"全 24 表"的退路）。
    editor.show_main()
    assert editor.current_page is editor.main_page


def test_every_section_really_gets_the_unit(window) -> None:
    """⭐ 中栏**每一块都**接到 context 了没有 —— 这一条是被真机打脸之后补的。

    2026-10-01 真机走查：四块抬头都在、底下全写着「还没有打开单位」——
    原因是 BenchPage.set_context 只把 context 给了卡与右栏，**忘了转发给中栏四块**。
    离屏用例当时 10 条全绿（它们只看骨架与右栏），所以这一条专门钉"内容真的画出来了"：
    抬头在 + 里面是空的 = 就是那个 bug。
    """
    window._open_unit_editor(INFANTRY)
    page = window._unit_editor.bench_page()

    members = page.bench_squad.cells_now()
    assert len(members) == 14, "Marine Raiders 是 14 人班 —— 一格一个兵（空态说明 context 没接上）"

    weapons = page.bench_loadout.lines()
    assert weapons, "② 班用武器与弹药：SquadWeapons 那 4 行要画出来"

    # ③–⑦ 五栏**各画各的**（拆块之后一个 StatsBench 只画一类），而且不许互相串。
    assert page.bench_armor.lines(), "③ 装甲：UnitArmors 那一行要画出来"
    assert page.bench_mobility.lines(), "④ 机动：UnitPropulsions 要画出来"
    assert page.bench_ability.lines(), "⑤ 能力：UnitAbilities 要画出来"
    assert page.bench_sensors.lines(), "⑥ 传感器：SensorUnits 要画出来"
    assert page.bench_specs.lines(), "⑦ 专精可用性：单位 70 实测有一条 SA 行（#102）"
    assert {line.table for line in page.bench_armor.lines()} == {"UnitArmors"}
    assert {line.table for line in page.bench_specs.lines()} == {"SpecializationAvailabilities"}

    options = page.bench_slots.options()
    assert options, "④ 改装槽：两组互斥选项要画出来"


# --------------------------------------------------------------------------- #
# 选中互斥（用户 2026-10-02：「为什么我能同时选中四个？」）
#
# 属性那一族拆成五栏之后，每栏是**独立控件**、各记各的选中 —— 连点 ③④⑤⑥ 会同时亮四行
# （真机截图里就是那样）。口径：**一屏只有一个选中**，右栏也就只跟最后点的那一行。
# --------------------------------------------------------------------------- #


def _lit_rows(block) -> list:
    """这一块现在高亮的行（**读控件里那一份**，不现算）。"""
    return [row for row in block.rows() if row.selected]


def test_only_one_row_stays_highlighted_across_the_mid_column(window, qapp) -> None:
    """⭐ 连点 ③④⑤⑥ 四栏的第一行 —— **只许最后点的那一行亮着**。"""
    window._open_unit_editor(STRYKER)
    editor = window._unit_editor
    assert editor.show_bench()
    page = editor.bench_page()
    blocks = (page.bench_armor, page.bench_mobility, page.bench_ability, page.bench_sensors)
    for block in blocks:
        block.section().set_collapsed(False)
    qapp.processEvents()

    for block in blocks:
        rows = block.rows()
        assert rows, "前提：Stryker RV（#21）在这四栏里都有行，不然这条守卫是空转"
        QTest.mouseClick(rows[0], Qt.MouseButton.LeftButton, pos=rows[0].rect().center())
        qapp.processEvents()
        lit = [b for b in blocks if _lit_rows(b)]
        assert lit == [block], [type(b).__name__ for b in lit]

    assert page.selected_row() == ("SensorUnits", blocks[-1].lines()[0].row_id)

    # ❗ 收尾折回去：window 是**模块级**夹具（这个页面跨用例活着），留着展开态会污染
    #    后面那条「默认收起」（先例：test_the_hardpoint_block_really_hides_its_21_cells）。
    for block in blocks:
        block.section().set_collapsed(True)


def test_clicking_a_stat_row_keeps_the_slot_circles(window, qapp) -> None:
    """② 的 ● 是**数据态**（Options.IsDefault）—— 高亮归"选中互斥"管，● 一个都不许动。

    ❗ 翻口径（2026-10-02）：从前这条的判据是"整行高亮也不许清"；现在 ② 的高亮改成
    **页面选中**（一屏只有一个），点别处就该清掉高亮 —— 但 ● / ○ 是数据，三条全都要在。
    """
    window._open_unit_editor(STRYKER)
    editor = window._unit_editor
    assert editor.show_bench()
    page = editor.bench_page()
    names_before = [row.name_label().text() for row in page.bench_slots.rows()]
    assert any(text.startswith("● ") for text in names_before), (
        "前提：这台车有一个槽选中的那一项（不然这条守卫是空转）"
    )

    page.bench_armor.section().set_collapsed(False)
    qapp.processEvents()
    row = page.bench_armor.rows()[0]
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=row.rect().center())
    qapp.processEvents()

    assert [r.name_label().text() for r in page.bench_slots.rows()] == names_before, "● 不许被清"
    assert page.bench_slots.selected_option_id() != 0
    assert not any(r.selected for r in page.bench_slots.rows()), "高亮该被选中互斥清掉"
    page.bench_armor.section().set_collapsed(True)  # 收尾折回去（同上）


def test_entering_the_bench_writes_nothing(window) -> None:
    """进编成台一个字节都不许写（卡是只读、页面只画）。"""
    window._open_unit_editor(INFANTRY)
    assert window._edit_session.change_count == 0


# --------------------------------------------------------------------------- #
# 右栏：选中项的全部列
# --------------------------------------------------------------------------- #


def test_the_rail_follows_the_selection_and_opens_the_data_table(window) -> None:
    """点中栏一行 → 右栏换成它的全部列；"去数据表页看那一行"这条路仍然通。

    ❗ 翻口径（2026-10-02）：右栏右上角那颗 ↗（「在数据表页里打开这一行」）按用户要求
    删了 —— 这一条原来点它。现在编成台里**唯一**还发这条路的是右栏每一列外键那格的
    小 ↗（`link_activated`），所以这里直接发那条信号、断言页面照样把请求转出去。
    """
    window._open_unit_editor(INFANTRY)
    page = window._unit_editor.bench_page()
    member_id = page.context.row_ids("SquadMembers")[0]

    page._on_row_selected("SquadMembers", member_id)
    assert page.rail.current_row() == ("SquadMembers", member_id)
    assert page.rail.row_texts(), "右栏要真的把这一行的列画出来"

    assert not hasattr(page.rail, "open_button"), "右上角那颗 ↗ 已经删了"

    seen: list[tuple[str, int]] = []
    page.open_table_row_requested.connect(lambda t, r: seen.append((t, r)))
    page.rail.link_activated.emit("SquadMembers", member_id)
    assert seen == [("SquadMembers", member_id)], "那一刻应该请求打开数据表页的那一行"


# --------------------------------------------------------------------------- #
# 基础件（bench_kit 是 Lead 的，队友只读；这一条守着它别长毛）
# --------------------------------------------------------------------------- #


def test_the_kit_section_rebuilds_cleanly(qapp) -> None:
    """clear_body 之后不留孤儿控件（下一次 add 不会重复堆）。"""
    section = BenchSection("① 测试", "灰字说明")
    action = section.add_action("＋ 加一项")
    assert action.text() == "＋ 加一项"
    row = BenchRow(name="一门枪", detail="说明", value="×120")
    section.add_widget(row)
    assert section.body_widgets() == (row,)
    section.clear_body()
    assert section.body_widgets() == ()


def test_the_kit_slot_card_marks_selection(qapp) -> None:
    """一格的选中态是**属性**（QSS 按 [selected="true"] 画），不是自绘。"""
    cell = SlotCard("05")
    icon, label = cell.add_line("Mk18 CQBR")
    cell.set_line(0, "M72A10 LAW")
    cell.set_footnote("rifle")
    assert label.text() == "M72A10 LAW"
    assert not cell.selected
    cell.set_selected(True)
    assert cell.selected and cell.property("selected") == "true"
    cell.set_selected(False)
    assert cell.property("selected") == "false"
    assert icon.family == ""


def test_row_tail_button_fits_inside_the_row(qapp) -> None:
    """⭐ 行尾 ✕/↗ **不许越出行**（越出那几 px 会被下一行盖住 → 点不到）。

    2026-10-01 队友用真机量出来的：全局 QPushButton 那条 min-height=28 盖掉了
    bench_kit 的 setFixedHeight，行高 28 而按钮算成 30。先量后改
    （_work/probe_bench_kit_metrics.py），现在高度由 QSS 定死；这一条守着它别再长回去。
    """
    row = BenchRow(name="Mk18 CQBR", detail="5.56x45 T2 CQC", value="×120")
    row.show()
    qapp.processEvents()
    # ❗ 行尾只剩 ✕ 了（那颗 ↗ 2026-10-02 按用户要求删掉）—— 高度与越界照旧要守住。
    button = row.remove_button()
    assert button.height() > 0, "行尾 ✕ 高度为 0"
    assert button.y() + button.height() <= row.height(), (
        f"行尾 ✕ 越出行：底部 {button.y() + button.height()} > 行高 {row.height()}"
    )


def test_the_kit_row_emits_only_intentions(qapp) -> None:
    """BenchRow 两个信号都只是"意图"——控件自己一个字节都不写。

    ❗ 2026-10-02：`link_requested`（行尾 ↗）按用户要求删了 —— 这条从"三个"改成"两个"。
    """
    row = BenchRow(name="x")
    fired: list[str] = []
    row.activated.connect(lambda: fired.append("activated"))
    row.remove_requested.connect(lambda: fired.append("removed"))
    row.remove_button().click()
    assert fired == ["removed"]
    assert not hasattr(row, "link_requested")


# --------------------------------------------------------------------------- #
# 折叠栏（用户 2026-10-02）：「所有能做成折叠栏的都做成折叠栏」＋「默认折叠状态」
#
# 范围**只到编成台这一页**；折的是"块"（BenchSection），块内部那 21 个挂载格 /
# 14 个兵格**一个都不折**（口径 5）。三形态的每一块（8 / 9 / 9）都要吃到新默认。
# --------------------------------------------------------------------------- #


def _bench_section_of(block):
    """一块控件暴露的那个折叠栏：SquadBench 是属性 section，其余是方法 section()。"""
    inner = getattr(block, "section", None)
    return inner() if callable(inner) else inner


@pytest.mark.parametrize(
    ("unit_id", "kind"),
    [(INFANTRY, KIND_SQUAD), (VEHICLE, KIND_VEHICLE), (AIRCRAFT, KIND_AIR)],
)
def test_every_bench_block_starts_collapsed(window, unit_id, kind) -> None:
    """⭐ 三种形态的中栏块（步兵 8 / **载具 9** / **飞机 9**），**建出来就是收起的**。"""
    window._open_unit_editor(unit_id)
    editor = window._unit_editor
    assert editor.current_bench_kind() == kind
    assert editor.show_bench()
    page = editor.bench_page()

    blocks = page.sections()
    # 块数从**顺序表**里取（单一来源）—— 步兵 8 / 载具 9 / 飞机 9
    # （2026-11-11 载具也加了 ① 挂载槽）。
    assert (
        len(blocks)
        == {
            KIND_SQUAD: len(SQUAD_ORDER),
            KIND_VEHICLE: len(VEHICLE_ORDER),
            KIND_AIR: len(AIR_ORDER),
        }[kind]
    ), f"{kind} 的块数变了：{len(blocks)}"
    for block in blocks:
        section = _bench_section_of(block)
        assert section.collapsible(), f"{type(block).__name__} 的折叠栏不可折"
        assert section.is_collapsed(), f"{type(block).__name__} 没吃到默认收起"
        assert section.caret_label().text() == "▸"
        assert section.body().isHidden(), "收起 = 主体 hide（控件一个都不销毁）"
        # ❗ "折得动"的判据（Lead 2026-10-02 定的那一句）：收了这一块之后，**section 之外
        #    不许还有可见控件** —— 挂在它外面的控件收起时藏不住（bench_hardpoints /
        #    bench_slots / bench_transport 都踩过这个形状；bench_turrets 的候选宿主还挂在
        #    外面，归 task-2，这里只守"别是**可见**的"）。
        outside = [
            widget
            for widget in block.findChildren(
                QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
            )
            if widget is not section and widget.isVisibleTo(block)
        ]
        assert outside == [], [type(w).__name__ for w in outside]
    # ⑤ 其他入口**整块删掉**了（用户 2026-10-02）—— 顺手守它别长回来。
    assert not hasattr(page, "entries_section")


def test_the_bench_chip_role_has_no_producer_any_more(window) -> None:
    """⑤ 其他入口删掉之后，bench-chip 那个 role 在编成台上**一个控件都没有**。

    改前这一条验的是"点抬头展开 / 收起，那排胶囊一个都不重建"（那一块自己的行为）。
    块没了，所以翻成负向断言：不许以任何形式把那排胶囊偷偷留在别处
    —— theme/qss.py 里那两条 bench-chip 规则**暂时没有调用方**（注释里写明了原因）。
    """
    window._open_unit_editor(INFANTRY)
    page = window._unit_editor.bench_page()
    chips = [
        widget for widget in page.findChildren(QWidget) if widget.property("role") == "bench-chip"
    ]
    assert chips == [], [type(w).__name__ for w in chips]
    # 中栏就那 8 块 —— 没有"第 9 块"替它站着。
    assert len(page.sections()) == 8


def test_every_bench_action_button_wears_the_same_frame(window) -> None:
    """⭐ 用户 2026-10-02（圈了 ①/③/⑨ 那三颗）：「这三个按钮和其它按钮保持一致，即框的颜色要一致」。

    病灶（已修）：主动作（`primary=True`）那条 QSS 用的是 `border-focus`（#4c6ef5 蓝），
    同一排里别的动作按钮是 `border-strong`（#333b48 灰）—— 一眼就不是一套。
    判据：**同一页所有 bench-action 按钮**画出来的最外圈像素色只有一种（不靠读 QSS 文本，
    直接 grab 下来数边上的像素）。
    """
    from collections import Counter

    window._open_unit_editor(AIRCRAFT)
    editor = window._unit_editor
    assert editor.show_bench()
    # ❗ 必须**真显示**再量：不 show 的窗口里控件没被 polish，grab() 拿到的是没上样式表的底色
    #    （实测 #1e1e1e），那样量出来的框色全是假的。
    # ❗ 用例环境默认**没上主题**（conftest 的 qapp 不调 apply_theme）—— 不上样式表的话
    #    每颗按钮都是默认调色板的 #1e1e1e，量出来的框色全是假的。这里补上产品启动时那一步。
    from ba_mod_maker.app.theme import apply_theme

    apply_theme(QApplication.instance())
    window.show()
    QApplication.processEvents()
    page = editor.bench_page()
    buttons = [
        button
        for button in page.findChildren(QPushButton)
        if button.property("role") == "bench-action"
    ]
    assert len(buttons) >= 8, f"样本变了：这一页只有 {len(buttons)} 颗动作按钮"
    assert any(button.property("primary") == "true" for button in buttons), "没有主动作就没有鉴别力"

    for button in buttons:
        image = button.grab().toImage()
        width, height = image.width(), image.height()
        seen: Counter = Counter()
        for x in range(width):
            for y in (0, height - 1):
                seen[image.pixelColor(x, y).name()] += 1
        for y in range(height):
            for x in (0, width - 1):
                seen[image.pixelColor(x, y).name()] += 1
        frame = seen.most_common(1)[0][0]
        assert frame == EXPECTED_ACTION_FRAME, (
            f"{button.text()!r} 的框色是 {frame}，与其它按钮不是一套"
        )
        # 用户 2026-10-02 第二句：「底色也拉平」—— 底色也一起量
        # （改前主动作那条是 accent-subtle #20283b）。
        inner: Counter = Counter()
        for x in range(1, width - 1):
            for y in range(1, height - 1):
                inner[image.pixelColor(x, y).name()] += 1
        fill = inner.most_common(1)[0][0]
        assert fill == EXPECTED_ACTION_FILL, (
            f"{button.text()!r} 的底色是 {fill}，与其它按钮不是一套"
        )


def test_the_hardpoint_block_really_hides_its_21_cells(window) -> None:
    """⭐ ① 21 格挂载图：折的是**块**，那 21 个格子一个都不折（口径 5）。

    回归闸：从前 `_grid_box` / `_foot` 挂在 section **外面**（主体之外），而
    `BenchSection` 收起只 hide 主体 —— 点了抬头 ▸ 那 21 格照旧画着（折了个寂寞）。
    """
    window._open_unit_editor(AIRCRAFT)
    editor = window._unit_editor
    assert editor.show_bench()
    hardpoints = editor.bench_page().sections()[0]
    assert isinstance(hardpoints, HardpointsBench)
    section = hardpoints.section()
    cards = hardpoints.cells()
    assert len(cards) == 21, "F-15E 的挂载图是 21 格 —— 样本变了这一节的读数要跟着改"
    assert section.is_collapsed() is True
    assert not any(card.isVisibleTo(hardpoints) for card in cards), "收起时 21 格不许还画着"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is False
    assert all(card.isVisibleTo(hardpoints) for card in cards), "展开后 21 格都要回来"
    assert hardpoints.cells() == cards, "折块不许重建那 21 个格子"
    # 格子矩阵 / 候选宿主 / 那句小结**都在主体里**（这才是"折块折得动"的判据：
    # 搬之前它们挂在 root 上，折了也藏不住）。
    assert section.body().isAncestorOf(hardpoints.grid())
    assert len(section.body_widgets()) == 3

    # ❗ 收尾折回去：`window` 是**模块级**夹具（模块里所有用例共用同一个页面），
    #    留着展开态会污染后面"默认收起"那几条（xdist worksteal 下用例顺序不固定 ——
    #    实测 [287-air] 被它红过一次）。
    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())
    assert section.is_collapsed() is True
    assert not any(card.isVisibleTo(hardpoints) for card in cards)


@pytest.mark.parametrize(
    ("unit_id", "label"),
    [(AIRCRAFT, "固定翼 F-15E"), (HELICOPTER, "直升机 Mi-8MTV-5"), (VEHICLE, "载具 BTR-ZD")],
)
def test_the_hardpoint_section_is_named_hangzaiacao(window, unit_id, label) -> None:
    """⭐ 用户 2026-11-11：折叠栏的名字就叫「挂载槽」，不要「（一个都不折叠）」那截。

    同时对**所有载具**成立（同一天那句「让所有载具都显示这些挂载槽」）——
    固定翼 / 直升机 / 地面车三样本都应当是同一块、同一个抬头。
    """
    window._open_unit_editor(unit_id)
    editor = window._unit_editor
    assert editor.show_bench()
    sections = editor.bench_page().sections()
    assert isinstance(sections[0], HardpointsBench), f"{label}：① 栏应当是挂载槽"
    title = sections[0].section().title_text()
    assert title == "① 挂载槽", f"{label}：折叠栏名字是 {title!r}"
    assert "一个都不折叠" not in title
