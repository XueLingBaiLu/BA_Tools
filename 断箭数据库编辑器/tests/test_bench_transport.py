"""编成台「运输配对」那一栏（TransportAvailabilities）—— 载具 / 飞机那张图上它摆第 ⑧ 块
（本模块的 TITLE 常量只当**默认值**用，装配时由 bench_page 按位置写编号）。

样本全部取真数据（DataBaseCompiled / 默认实例 52085），Id 都是只读探针跑出来钉死的：

- **Units#206**（BTR-ZD Skrezhet）有 **1** 条配对：TransportAvailabilities#902
  -> SpecializationAvailabilities#172（UnitId 109 = VDV Igla / SpecializationId 7 = VDV Brigade
  / MaxAvailabilityXp0 4）；
- **Units#70**（Marine Raiders）没有任何运输配对（空态那句灰字走它）；
- **Units#110**（VDV Kornet）在 SpecializationAvailabilities 里**有**行 -> 「＋」能加成；
- **Units#206 自己**在 SpecializationAvailabilities 里**没有**行 -> 「＋」必须拒绝
  （承运方不必自己有 SA 行）。

一行上有**四个不同的东西**，用例逐个分开钉（别让"改错了哪一个"也能过）：

| 手势 | 落到 |
|---|---|
| 点整行 | row_selected("TransportAvailabilities", 902)（配对行）|
| 行尾 ↗ | row_activated("Units", 109)（被运的那个单位）|
| 行尾 ✕ | 删 TransportAvailabilities#902 |
| 右边徽章 | #109（被运单位，不是配对行自己的 #902）|

三条纪律：断言钉**控件里那一份状态**（BenchRow 的四个标签文字），不现算；
点控件用 **QTest 真点**（不是 emit 冒充）；每个用例收尾销毁自己建的控件。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from ba_mod_maker.app.screens.editor.pages.base import EditorContext
from ba_mod_maker.app.viewmodel import bench_transport as vm
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.app.widgets.bench_kit import BenchEmpty
from ba_mod_maker.app.widgets.bench_transport import HINT, TITLE, TransportBench
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.sharing import build_sharing_index

#: 真数据里那几个 Id（探针跑出来的，换数据源要跟着改）。
CARRIER = 206
CARRIED = 109
EMPTY_UNIT = 70
ADDABLE = 110
PAIR_ROW = 902
PAIR_SA = 172
CAP = 4


# --------------------------------------------------------------------------- #
# 夹具与小工具
# --------------------------------------------------------------------------- #


def make_context(instance, unit_id):
    """与 UnitEditorScreen 建的那一份同构（寻址 / 共享度 / 撤销栈都走 core）。"""
    return EditorContext(
        instance,
        unit_id,
        Changeset(instance),
        CardData(instance),
        build_sharing_index(instance),
        lambda: None,
    )


@pytest.fixture()
def context(instance):
    return make_context(instance, CARRIER)


@pytest.fixture()
def bench(qapp):
    """建一个 TransportBench（收尾统一销毁 —— conftest 对顶层控件数有上限钉子）。"""
    made = []

    def build(ctx=None):
        widget = TransportBench()
        made.append(widget)
        widget.set_context(ctx)
        return widget

    yield build
    for widget in made:
        widget.deleteLater()


def click(widget) -> None:
    """中心点一下（QTest 送鼠标事件，不是 emit 冒充）。"""
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())


def click_row(row) -> None:
    """点**整行**：落点挑左下角那块（名字那一段），避开行尾的 ✕ / ↗。"""
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(10, row.rect().center().y()))


def names(bench: TransportBench) -> list[tuple[str, str, str, str]]:
    """界面上那四格文字，逐行读出来（名字 / 灰字 / 行尾值 / 徽章）。"""
    return [
        (
            row.name_label().text(),
            row.detail_label().text(),
            row.value_label().text(),
            row.id_label().text(),
        )
        for row in bench.rows()
    ]


# --------------------------------------------------------------------------- #
# 前提：SA 与"被运单位"是 1:1（"＋"只挑一级的全部依据）
# --------------------------------------------------------------------------- #


def test_specialization_availability_is_one_to_one_with_the_carried_unit(instance) -> None:
    """`SpecializationAvailabilities` 每个 `UnitId` **只有一行**（实测 338 行 / 338 个单位）。

    这条是「＋」**只挑一级**成立的全部依据：挑中一个单位，才能唯一确定要写进
    `TransportAvailabilities.SpecializationAvailabilityId` 的那个 SA。
    哪天数据变成一对多，`sa_id_of_unit` 的"取最小 Id"就变成了一个隐式选择 ——
    这条断言会当场红，逼人回来重新设计那一级挑。
    """
    from collections import Counter

    table = instance.table(vm.SPEC_AVAIL_TABLE)
    index = {name: i for i, name in enumerate(table.columns)}
    per_unit = Counter(row[index["UnitId"]] for row in table.rows)
    assert table.row_count == len(per_unit) == 338, (table.row_count, len(per_unit))
    assert max(per_unit.values()) == 1, "有单位的 SA 行不止一条 —— 一级挑的前提没了"
    assert instance.table("Units").row_count == 540


# --------------------------------------------------------------------------- #
# 取数与渲染：一行上的四个东西各有出处
# --------------------------------------------------------------------------- #


def test_206_renders_one_pair_with_the_carried_unit(instance, bench) -> None:
    """206 那唯一一条配对：名字/灰字/值/徽章四格，且徽章是**被运单位**的 Id。"""
    widget = bench(make_context(instance, CARRIER))
    assert len(widget.rows()) == 1
    line = widget.lines()[0]
    assert (line.row_id, line.sa_id, line.carried_id, line.cap) == (
        PAIR_ROW,
        PAIR_SA,
        CARRIED,
        CAP,
    )
    assert names(widget) == [("VDV Igla", "能被它搭载 · 专精 VDV Brigade · 上限 4", "1 行", "#109")]


def test_the_badge_is_the_carried_unit_not_the_pair_row(instance, bench) -> None:
    """⚠️ 徽章写 #109（被运单位），**不是**写这条配对行自己的 #902。

    口径同 ② 弹药行（写 Ammunitions#143 而不是 WeaponAmmunitions 那一行）、
    ③ 装甲行（写 Armors#118 而不是 UnitArmors 那一行）：行上那个 Id 是
    "这一行说的是哪个实体"，配对行自己由"点整行 -> 右栏"去看。
    """
    widget = bench(make_context(instance, CARRIER))
    badge = widget.rows()[0].id_label().text()
    assert badge == "#109"
    assert badge != "#902"
    # 反过来也要成立：配对行 Id 不能从别处漏到行上。
    for row in widget.rows():
        assert row.id_label().text() != f"#{PAIR_ROW}"


def test_clicking_the_row_selects_the_pair_row(instance, bench) -> None:
    """点整行 -> `row_selected("TransportAvailabilities", 902)`（右栏换成**配对行**）。"""
    widget = bench(make_context(instance, CARRIER))
    seen = []
    widget.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    click_row(widget.rows()[0])
    assert seen == [(vm.TABLE, PAIR_ROW)]


def test_the_pair_row_has_no_arrow_any_more(instance, bench) -> None:
    """❗ 翻口径（2026-10-02）：行尾那颗 ↗（去看被运的那个单位）按用户要求**删了**。

    要看被运单位，走右栏那一列外键那格的小 ↗（`BenchRail.link_activated`）——
    页面上那条接线**还在**（BenchPage 里 `rail.link_activated -> _open_row`）。
    这一条原来叫 `test_the_link_button_goes_to_the_carried_unit`。
    """
    widget = bench(make_context(instance, CARRIER))
    assert not hasattr(widget.rows()[0], "link_button")
    assert not hasattr(widget, "row_activated")


def test_the_remove_button_deletes_only_the_pair_row(instance, bench) -> None:
    """行尾 ✕ 删这条配对；`SA` 那一行与被运单位那一行**一个字节不动**。"""
    widget = bench(make_context(instance, CARRIER))
    seen = []
    widget.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    click(widget.rows()[0].remove_button())
    assert widget.rows() == ()
    assert [rc.row_id for rc in widget._context.changeset.row_changes] == [PAIR_ROW]
    # 对方那两行还在（表本身没被删行）。
    assert instance.table(vm.SPEC_AVAIL_TABLE).row_count == 338
    assert instance.table("Units").row_count == 540
    # 一条都不剩时不发"选中空行"这种没意义的信号。
    assert seen == []


# --------------------------------------------------------------------------- #
# 空态 / 幂等 / 就地刷新
# --------------------------------------------------------------------------- #


def test_no_context_draws_the_empty_state(bench) -> None:
    """`set_context(None)` 画空态、不许炸（Lead 定的接口第 4 条）。"""
    widget = bench(None)
    assert widget.rows() == ()
    assert widget.lines() == ()
    empties = [w.text() for w in widget.findChildren(BenchEmpty)]
    assert len(empties) == 1
    assert empties[0]


def test_a_unit_without_any_pair_says_so(instance, bench) -> None:
    """没有任何配对（单位 70）时画那句灰字，不是空框。"""
    widget = bench(make_context(instance, EMPTY_UNIT))
    assert widget.rows() == ()
    empties = [w.text() for w in widget.findChildren(BenchEmpty)]
    assert empties == [vm.transport_empty_text(EMPTY_UNIT)]


def test_refresh_is_idempotent(instance, bench) -> None:
    """幂等：连调两次得到同样的行，不叠控件、不漏控件。"""
    widget = bench(make_context(instance, CARRIER))
    before = names(widget)
    widget.refresh()
    widget.refresh()
    assert names(widget) == before
    assert len(widget.rows()) == 1


def test_reload_values_keeps_every_widget(instance, bench) -> None:
    """`reload_values()`：结构没变时**一个控件都不重建**（用户提过几回那条）。"""
    widget = bench(make_context(instance, CARRIER))
    rows_before = widget.rows()
    widget.reload_values()
    assert widget.rows() == rows_before  # 同一个对象（不是等值的另一批）
    assert names(widget) == [("VDV Igla", "能被它搭载 · 专精 VDV Brigade · 上限 4", "1 行", "#109")]


# --------------------------------------------------------------------------- #
# 「＋ 加一行」：一级挑 + 只插 TransportAvailabilities 一行
# --------------------------------------------------------------------------- #


def test_add_picker_offers_units(instance, bench) -> None:
    """「＋」就地冒候选，候选表 = `Units`（一级挑 —— Lead 定案）。"""
    widget = bench(make_context(instance, CARRIER))
    assert widget.picker() is None
    click(widget.add_button())
    picker = widget.picker()
    assert picker is not None
    assert picker.table == vm.UNITS_TABLE
    assert picker.count > 0


def test_sa_id_of_unit_hits_and_misses(instance) -> None:
    """反查：110 有行（命中）、206 自己**没有**行（0 —— 承运方不必自己有 SA 行）。"""
    data = CardData(instance)
    hit = vm.sa_id_of_unit(data, ADDABLE)
    assert hit > 0
    table = instance.table(vm.SPEC_AVAIL_TABLE)
    index = {name: i for i, name in enumerate(table.columns)}
    assert next(r for r in table.rows if r[index["Id"]] == hit)[index["UnitId"]] == ADDABLE
    assert vm.sa_id_of_unit(data, CARRIER) == 0
    assert vm.sa_id_of_unit(data, 0) == 0
    assert vm.sa_id_of_unit(data, 999999) == 0


def test_adding_a_pair_writes_exactly_three_columns(instance, bench) -> None:
    """加成：只插 `TransportAvailabilities` 一行，三列齐（Id / UnitId 承运方 / SA）。

    **不造第二张表的行**（Lead 定案）。
    """
    widget = bench(make_context(instance, CARRIER))
    messages = []
    widget.status_message.connect(messages.append)
    new_id = widget.add_line(ADDABLE)
    assert new_id > 0
    assert len(widget.rows()) == 2
    # 新行指向 110 那条 SA（由 sa_id_of_unit 反查），被运方写 110、上限照 SA 走。
    added = widget.lines()[-1]
    assert (added.row_id, added.carried_id) == (new_id, ADDABLE)
    assert added.sa_id == vm.sa_id_of_unit(CardData(instance), ADDABLE)
    # SA 那张表一行都没多（只插配对表）。
    assert instance.table(vm.SPEC_AVAIL_TABLE).row_count == 338
    assert instance.table("Units").row_count == 540
    record = widget._context.changeset.row_changes[-1]
    assert record.table == vm.TABLE
    assert record.row_id == new_id
    assert record.kind == "insert"
    assert record.value_of("UnitId") == CARRIER
    assert record.value_of(vm.LINK_COLUMN) == added.sa_id
    assert messages and "已加一条运输配对" in messages[-1]


def test_adding_a_unit_without_an_sa_row_is_refused(instance, bench) -> None:
    """挑中的单位在 `SA` 里没有行 -> **不写库**，只给一句 status_message（绝不弹窗）。"""
    widget = bench(make_context(instance, CARRIER))
    messages = []
    widget.status_message.connect(messages.append)
    assert widget.add_line(CARRIER) == 0
    assert len(widget.rows()) == 1
    assert widget._context.changeset.row_changes == ()
    assert messages and "加不了" in messages[0]


def test_new_row_id_comes_from_next_row_id(instance) -> None:
    """新行 Id 走 `next_row_id`（不是"最大 + 1"—— 这张表的 Id 有大空洞）。"""
    data = CardData(instance)
    changeset = Changeset(instance)
    values = vm.new_transport_values(data, CARRIER, CARRIED, changeset=changeset)
    assert values is not None
    from ba_mod_maker.core.edit.clone import next_row_id

    assert values["Id"] == next_row_id(instance.table(vm.TABLE), changeset)
    assert values["UnitId"] == CARRIER
    assert values[vm.LINK_COLUMN] == PAIR_SA
    # 反查不到 -> None（调用方据此拒绝）。
    assert vm.new_transport_values(data, CARRIER, CARRIER) is None


def test_the_row_clone_saves_a_new_specialization_availability(instance, context, bench) -> None:
    """⭐ 运输行那颗 ⧉ = 复制它**指着的** `SpecializationAvailabilities` 另存一份、本行指过去。

    口径与 ③–⑦ 属性行那一颗同源（交接 §23）：复制的是它指的那一行（「哪个单位能带它」），
    复制完把本行指过去 —— 新那一条随便改 specialization / 上限，别人一个字节不动。
    """
    widget = bench(context)
    widget.section().set_collapsed(False)
    line = widget.lines()[0]
    row = widget.rows()[0]
    assert [tool.text() for tool in row.tools()] == ["⧉"], "运输行要有一颗复制"

    def cell(table: str, row_id: int, column: str):
        rows = instance.table(table)
        i_id, i_col = rows.column_index("Id"), rows.column_index(column)
        for item in rows.rows:
            if item[i_id] == row_id:
                return item[i_col]
        raise AssertionError(f"{table} 里没有 #{row_id}")

    def has_row(table: str, row_id: int) -> bool:
        rows = instance.table(table)
        i_id = rows.column_index("Id")
        return any(item[i_id] == row_id for item in rows.rows)

    assert (
        cell("TransportAvailabilities", line.row_id, "SpecializationAvailabilityId") == line.sa_id
    )
    seen: list[tuple[str, int]] = []
    widget.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    click(row.tools()[0])

    new_sa = cell("TransportAvailabilities", line.row_id, "SpecializationAvailabilityId")
    assert new_sa != line.sa_id, "本行没指到新那一条"
    assert has_row("SpecializationAvailabilities", new_sa), "模板行没另存出来"
    assert has_row("SpecializationAvailabilities", line.sa_id), "原来那条不许动"
    assert seen == [("SpecializationAvailabilities", new_sa)], "复制完右栏要落到新那一条"


def test_titles_match_the_design(instance, bench) -> None:
    """抬头常量（与设计图逐字一致）—— Lead 按这两个名字装配。"""
    assert (TITLE, HINT) == ("④ 运输", "TransportAvailabilities")


# --------------------------------------------------------------------------- #
# 折叠栏（用户 2026-10-02）：默认收起、点抬头展开 / 收起，控件一个都不重建
# --------------------------------------------------------------------------- #


def test_the_block_is_collapsed_by_default_and_toggles_without_rebuilding(instance, bench) -> None:
    """⭐ 运输这一栏默认收起（用户口径「折叠栏默认是折叠状态」）；点抬头展开、再点收起。

    判据三条（与 tests/test_bench_kit.py 那套同源，这里验的是**这一块真的吃到默认值**）：
    默认 `is_collapsed()`；点抬头空白换态 + 记号 ▸/▾；两种态下 `body_widgets()` 是
    **同一批控件**（AGENTS §0.7：收起只是 hide，一个都不许重建）。
    """
    widget = bench(make_context(instance, CARRIER))
    section = widget.section()

    assert section.collapsible()
    assert section.is_collapsed() is True
    assert section.caret_label().text() == "▸"
    kept = section.body_widgets()
    assert kept, "那条配对行该在主体里 —— 不然这条守卫是空转"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is False
    assert section.caret_label().text() == "▾"
    assert section.body_widgets() == kept, "展开不许重建控件"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is True
    assert section.body_widgets() == kept, "收起同样不许重建控件"


def test_the_picker_lives_inside_the_block_body(instance, bench) -> None:
    """候选宿主在**主体**里（回归闸）：挂回主体外，"收起这一块"就藏不住它了。"""
    widget = bench(make_context(instance, CARRIER))
    click(widget.add_button())
    picker = widget.picker()
    assert picker is not None
    assert widget.section().body().isAncestorOf(picker)
