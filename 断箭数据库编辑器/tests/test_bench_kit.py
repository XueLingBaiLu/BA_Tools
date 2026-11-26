"""基础件两条口径的守卫：**折叠栏默认收起**（用户 2026-10-02）＋ 行尾工具按钮的位置。

- 折叠（`BenchSection`）：默认收起、点抬头空白展开/收起、**控件一个都不重建**、
  抬头右边的动作按钮照旧能点（点它不会顺手把整块收起来）、标题文字不被 ▸ 污染；
- 行尾工具（`BenchRow.add_tool`）：🔁 / ＋ / ↑ / ↓ 一律排在 ✕ **左边**（后加的不越位）。

⚠️ 范围口径：折叠只到**编成台这一页**；单位编辑器「关系树页」的中栏不折 ——
那条哨兵在 tests/test_unit_editor.py:150（"中栏里没有可勾选的折叠开关"）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from ba_mod_maker.app.widgets.bench_kit import BenchRow, BenchSection


def test_a_long_detail_keeps_the_row_end_inside(qapp) -> None:
    """长描述**不许**把行尾的值 / Id / ＋✕ 顶出行外。

    用户 2026-10-03（LAV-25 那屏）：「**这右边的东西不见了**」＋「**被挤出边界了吧**」。
    根因：行里的灰字是普通 `QLabel`，最小宽 = 整段文字的宽（实测 LAV25A1 那条 **637**），
    行只有 640 时行尾被顶到 **664** 之外，而 Qt 只会把它们**裁掉**。灰字改成
    `ElidedLabel`（可压到 0 + 自己省略）之后行尾一律留在行内。
    """
    row = BenchRow(
        name="LAV25A1",
        detail="换成这套装甲 Armor LAV25A1 · 换成主传感器 Medium Plus Optics · " * 4,
        value="±0",
        id_text="#18",
    )
    plus = row.add_tool("＋", tooltip="加一条")
    remove = row.remove_button()
    row.resize(640, 40)
    row.layout().activate()

    right = max(tool.x() + tool.width() for tool in (plus, remove))
    assert right <= row.width(), f"行尾被顶出去了：按钮右缘 {right} > 行宽 {row.width()}"
    # 灰字**压得动**（最小宽 0）—— 压不动正是那条 bug 的根；同时 text() 仍给全文。
    assert row.detail_label().minimumSizeHint().width() == 0
    assert row.detail_label().text().startswith("换成这套装甲")


def _role(widget, role: str) -> QLabel:
    return next(label for label in widget.findChildren(QLabel) if label.property("role") == role)


def test_a_section_is_collapsed_by_default(qapp) -> None:
    """用户原话：「折叠栏默认是折叠状态」—— 建出来就是收起的。"""
    section = BenchSection("① 测试块", "灰字说明")

    assert section.collapsible()
    assert section.is_collapsed()
    assert section.body().isHidden(), "收起 = 主体 hide"
    assert section.caret_label().text() == "▸"
    # ❌ 抬头那一条**也不挂**（用户 2026-10-03：「把这个注释也给我去掉」，图里正是
    #    「点一下收起」那一句）—— 中栏一个 tooltip 都不许有。
    assert section.head().toolTip() == ""


def test_clicking_the_head_toggles_without_rebuilding_anything(qapp) -> None:
    """点抬头空白 = 展开；**控件一个都不销毁重建**（AGENTS §0.7：能 hide 别重建）。"""
    section = BenchSection("① 测试块")
    rows = [BenchRow(name=f"行 {i}") for i in range(3)]
    for row in rows:
        section.add_widget(row)
    kept = section.body_widgets()

    QTest.mouseClick(section.head(), Qt.LeftButton, pos=section.head().rect().center())

    assert not section.is_collapsed() and not section.body().isHidden()
    assert section.caret_label().text() == "▾"
    assert section.body_widgets() == kept, "展开/收起不许重建控件"

    QTest.mouseClick(section.head(), Qt.LeftButton, pos=section.head().rect().center())
    assert section.is_collapsed() and section.body().isHidden()
    assert section.body_widgets() == kept


def test_the_action_buttons_still_work_and_do_not_toggle_the_section(qapp) -> None:
    """抬头右边那几颗（＋ 加一项…）**照旧能点**，而且点它不会顺手把整块折起来。"""
    section = BenchSection("① 测试块")
    hits: list[int] = []
    button = section.add_action("＋ 加一座炮塔")
    button.clicked.connect(lambda: hits.append(1))

    QTest.mouseClick(button, Qt.LeftButton, pos=button.rect().center())

    assert hits == [1], "收起状态下动作按钮也要点得动"
    assert section.is_collapsed(), "点动作按钮不许把整块折起来/展开"


def test_a_title_is_not_polluted_by_the_caret(qapp) -> None:
    """标题那一格必须**逐字**等于宿主给的那句（先例：test_bench_loadout 拿 TITLE 比）。"""
    title = "① 本单位的炮塔 → 武器 → 弹药"
    section = BenchSection(title, "改装「ZU23」填了 3 个炮塔槽")

    assert _role(section, "bench-section-title").text() == title
    assert _role(section, "bench-section-hint").text() == "改装「ZU23」填了 3 个炮塔槽"
    assert _role(section, "bench-section-caret").text() == "▸", "记号单占一格，不并进标题"


def test_the_title_and_hint_have_readers(qapp) -> None:
    """编号由装配方按"摆第几块"写 —— 写之前得先**读**得到现在那一行。

    读数口读的是**控件里那一份**（与 caret_label 同一条口径），不是构造时存的那个字符串。
    """
    section = BenchSection("③ 装甲", "UnitArmors")
    assert section.title_text() == "③ 装甲"
    assert section.hint_text() == "UnitArmors"
    section.set_title("④ 机动")
    section.set_hint("UnitPropulsions")
    assert (section.title_text(), section.hint_text()) == ("④ 机动", "UnitPropulsions")


def test_the_caret_is_a_clickable_cell_not_a_pixel_hunt(qapp) -> None:
    """记号那一格是**点得到的一格**，不是"瞄准那一小撮"。

    用户 2026-10-02 实机原话：「我展开折叠栏，**要点这个小三角形，很不好操作**」——
    实测改前 8×14（只有那 8px 宽的一条能折）。尺寸钉在代码里
    （`bench_kit.CARET_WIDTH/CARET_HEIGHT`），所以这一条**不起主题**也守得住。
    """
    from ba_mod_maker.app.widgets.bench_kit import CARET_HEIGHT, CARET_WIDTH

    section = BenchSection("① 测试块")
    caret = section.caret_label()

    assert (caret.width(), caret.height()) == (CARET_WIDTH, CARET_HEIGHT)
    assert CARET_WIDTH >= 20 and CARET_HEIGHT >= 20, (
        f"折叠格太小：{CARET_WIDTH}x{CARET_HEIGHT}（改前实测 8x14，点偏一两像素就落到别处）"
    )


def test_a_section_can_opt_out_of_folding(qapp) -> None:
    """`collapsible=False`：不给记号、set_collapsed 也不动。

    留给"就这么一块、折它没意义"的场合（先例：用户说过只有一条的别套折叠）。
    """
    section = BenchSection("测试块", collapsible=False, collapsed=False)

    assert not section.collapsible() and not section.is_collapsed()
    assert not section.body().isHidden()
    assert section.caret_label().isHidden()
    assert section.set_collapsed(True) is False
    assert not section.body().isHidden(), "不许折的块点了也不许收起"


def test_toggle_announces_the_new_state(qapp) -> None:
    """`toggled(now_collapsed)`：宿主还要跟着做别的时接它。"""
    section = BenchSection("① 测试块")
    seen: list[bool] = []
    section.toggled.connect(seen.append)

    section.toggle_collapsed()
    section.toggle_collapsed()

    assert seen == [False, True]


def test_row_tools_land_left_of_the_remove_button(qapp) -> None:
    """行尾工具（🔁 换 / ＋ 加 / ↑ / ↓）**排在 ✕ 左边**，后加的也不越位。"""
    row = BenchRow(name="M240 塔")
    swap = row.add_tool("🔁", tooltip="换一座炮塔")
    up = row.add_tool("↑", tooltip="上移")
    hits: list[str] = []
    swap.clicked.connect(lambda: hits.append("swap"))
    up.clicked.connect(lambda: hits.append("up"))

    swap.click()
    up.click()

    layout = row.layout()
    assert hits == ["swap", "up"]
    assert row.tools() == (swap, up)
    assert layout.indexOf(swap) < layout.indexOf(up) < layout.indexOf(row.remove_button())
