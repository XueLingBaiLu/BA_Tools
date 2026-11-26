"""弹药专用页（Ammunitions 67 列）：携弹量 + 能打什么 + 同武器的其它弹。

这一层要验的是"控件和数据接对了没有"，所以样本全部取真数据（DataBaseCompiled/52085
的 Units#1 · BTR-82A），不是自造的假表：

- 右栏**67 列一条一行、滚就滚**，次序 = **表自己的列序**（设计 §3 块 6 / 图 block6）；
- 抬头按设计图：这一行的名字 + Ammunitions#19 · 一条一行，往下滚 + 条数 + ⬅ 回单位；
- 携弹量**独立地从 WeaponAmmunitions 那一行读一遍**再和界面上的数比
  （Ammunitions 表里压根没有 Quantity 这一列 —— 这是设计点名的那条坑），
  卡片上那个数还要再乘 Ammunitions.HUDMultiplier；
- TargetType 36 -> 「载具 + 舰船」，原始值仍读得到；
- 换弹种后右栏换的是另一行、列数还是 67，且**一个字节的数据都没改**。

无字体的平台（offscreen）会 skip —— 与 test_unit_editor.py 同一策略。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 设计稿的示例单位 BTR-82A（Id 1）；它那一门 2A72 30mm 是 Weapons#2。
UNIT = 1
WEAPON = 2
#: 同一门武器下的两个弹种：208 = AP（携弹量 30）、207 = HE（携弹量 70）。
AMMO_AP = 208
AMMO_HE = 207


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    if not QFontDatabase.families():
        pytest.skip("该平台没有可用字体，控件度量不可信")
    return app


@pytest.fixture(autouse=True)
def no_shared_icons(monkeypatch):
    """默认图标来源换成 None：用例不建共享 IconCache、不起加载线程、不读游戏包。"""
    from ba_mod_maker.app.screens.editor.pages import ammo_page

    monkeypatch.setattr(ammo_page, "default_icons", lambda: None)


@pytest.fixture()
def instance():
    """每个用例一份新实例：用例会改内存里的行。"""
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


@pytest.fixture()
def context(instance):
    """与 UnitEditorScreen 建的那一份同构（寻址 / 共享度 / 撤销栈都走 core）。"""
    from ba_mod_maker.app.screens.editor.pages.base import EditorContext
    from ba_mod_maker.app.viewmodel.unit_card import CardData
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.edit.sharing import build_sharing_index

    return EditorContext(
        instance,
        UNIT,
        Changeset(instance),
        CardData(instance),
        build_sharing_index(instance),
        lambda: None,
    )


def _page(context, ammo_id=AMMO_AP, weapon_id=WEAPON, icons=None):
    from ba_mod_maker.app.screens.editor.pages.ammo_page import AmmoPage

    page = AmmoPage(weapon_id, ammo_id, None, icons=icons)
    page.set_context(context)
    return page


def _row(instance, table, **match):
    """按列值找一行（**独立于界面**去读表，免得断言和自我实现打转）。"""
    data = instance.table(table)
    index = {name: i for i, name in enumerate(data.columns)}
    for row in data.rows:
        if all(row[index[column]] == value for column, value in match.items()):
            return row
    raise AssertionError(f"{table} 里没有 {match} 这一行")


def _labels(page):
    from PySide6.QtWidgets import QLabel

    return [widget.text() for widget in page.findChildren(QLabel) if widget.text()]


def _buttons(page):
    from PySide6.QtWidgets import QPushButton

    return [widget for widget in page.findChildren(QPushButton)]


def _button_with(page, needle):
    hits = [button for button in _buttons(page) if needle in button.text()]
    assert len(hits) == 1, f"界面上「{needle}」的按钮应当只有一个，实际 {len(hits)} 个"
    return hits[0]


def _button_in(widget, text):
    hits = [button for button in _buttons(widget) if button.text() == text]
    assert len(hits) == 1, f"「{text}」按钮应当只有一个，实际 {len(hits)} 个"
    return hits[0]


# --------------------------------------------------------------------------- #
# 右栏：67 列一个不少，按功能分组重排
# --------------------------------------------------------------------------- #


def test_right_column_is_all_67_columns_in_table_order(qapp, instance, context) -> None:
    """① 67 列一个不少，**次序就是表自己的列序**（设计图 block6：01 Id … 67）。"""
    from ba_mod_maker.app.screens.editor.pages.ammo_page import ordered_columns

    page = _page(context)
    cells = page.entries_cells()
    columns = [cell.column for cell in cells]
    assert len(cells) == 67
    assert len(set(columns)) == 67, "有重复列"
    assert set(columns) == set(instance.table("Ammunitions").columns)
    assert page.column_names() == ordered_columns(instance.table("Ammunitions").columns)
    assert all(cell.ref.table == "Ammunitions" and cell.ref.row_id == AMMO_AP for cell in cells)

    # 表列序 = 设计图那个序：01 Id / 02 Name / 03 HUDName / 04 HUDIcon /
    # 05 HUDMultiplier（显示倍数）/ 06 UiOptions（弹药界面选项）…
    # （设计图 block6.png 的第 5、6 行就是这两个；曾经按功能分组重排时它们是
    #  ModelFileName / AudioPreset —— 那正是这一页要改掉的那件事。）
    schema = list(instance.table("Ammunitions").columns)
    assert page.column_names() == tuple(schema)
    assert page.column_names()[:6] == (
        "Id",
        "Name",
        "HUDName",
        "HUDIcon",
        "HUDMultiplier",
        "UiOptions",
    )
    assert page.column_names()[-1] == schema[-1]


def test_column_groups_still_classify_every_column(qapp, instance, context) -> None:
    """功能归类：67 列**每一列都在且只在一个组里**；它不再给右栏排序。"""
    from ba_mod_maker.app.screens.editor.pages.ammo_page import (
        AMMO_COLUMN_GROUPS,
        column_group,
        ordered_columns,
    )

    grouped = [column for _title, _note, columns in AMMO_COLUMN_GROUPS for column in columns]
    schema = list(instance.table("Ammunitions").columns)
    assert len(grouped) == len(set(grouped)) == 67, "分组表里有重复列"
    assert set(grouped) == set(schema), "分组表漏了列（或写了不存在的列）"
    assert column_group("TargetType") == "能打什么"
    assert column_group("Ammunitions 里没这一列") is None
    # ❗ 分组**不再决定右栏次序**（设计图 block6 是表列序）：ordered_columns 原样返回，
    #    既不筛也不重排 —— 分组表没提到的列照样在右栏、还在原位置上。
    assert ordered_columns(schema) == tuple(schema)
    assert ordered_columns(("Zzz 没登记的列", "TargetType")) == ("Zzz 没登记的列", "TargetType")
    assert ordered_columns(()) == ()


# --------------------------------------------------------------------------- #
# 携弹量：在 WeaponAmmunitions.Quantity，不在弹药行上
# --------------------------------------------------------------------------- #


def test_quantity_comes_from_weapon_ammunitions(qapp, instance, context) -> None:
    """② 界面上的携弹量 == WeaponAmmunitions.Quantity 那一格（独立读表比一遍）。"""
    from ba_mod_maker.core.edit.refs import CellRef

    row = _row(instance, "WeaponAmmunitions", UnitId=UNIT, WeaponId=WEAPON, AmmunitionId=AMMO_AP)
    index = instance.table("WeaponAmmunitions").columns.index("Quantity")
    expected = row[index]
    assert expected != 0, "样本的携弹量不该是 0（换一个样本）"
    assert "Quantity" not in instance.table("Ammunitions").columns, "弹药行上不该有这一列"

    page = _page(context)
    assert page.quantity_raw() == expected
    assert page.quantity_text() == str(expected)
    cell = page.quantity_cell()
    assert cell is not None
    assert cell.ref.table == "WeaponAmmunitions" and cell.ref.column == "Quantity"
    assert context.cell(CellRef("Ammunitions", AMMO_AP, "Quantity")) is None

    button = _button_with(page, "本单位的携弹量")
    assert str(expected) in button.text()
    assert "不在弹药行上" in button.text()
    assert "WeaponAmmunitions#" in button.toolTip()
    # 卡片上那个数 = 本数 × Ammunitions.HUDMultiplier（那一列就在右栏 67 列里）——
    # 左栏这句提醒必须挂着，别让两个数被当成一回事（设计点名的第 3 条口径）。
    assert "HUDMultiplier" in page.column_names()
    assert any("Ammunitions.HUDMultiplier" in text for text in _labels(page))


def test_quantity_row_can_be_edited_in_place_and_undone(qapp, instance, context) -> None:
    """左栏那一格点开就能就地改（浮层挂左栏 —— 右栏里没有这一格），Ctrl+Z 撤回。"""
    from PySide6.QtWidgets import QLineEdit

    page = _page(context)
    before = page.quantity_raw()
    cell = page.quantity_cell()
    assert cell is not None

    _button_with(page, "本单位的携弹量").click()
    assert page.quantity_editor_open
    editor = page.quantity_editor
    assert editor is not None
    line = editor.findChild(QLineEdit)
    line.setText("35")
    # 没有确认按钮了（用户口径）：回车 / 失焦即生效
    line.editingFinished.emit()

    assert not page.quantity_editor_open, "提交完浮层要收起来"
    assert page.quantity_raw() == 35
    assert str(35) in _button_with(page, "本单位的携弹量").text(), "左栏那个数没跟着变"
    assert context.changeset.is_dirty("WeaponAmmunitions", cell.row_id, "Quantity")

    assert context.undo_last().startswith("已撤销")
    page.refresh()
    assert page.quantity_raw() == before
    assert context.changeset.is_empty


# --------------------------------------------------------------------------- #
# 能打什么：位掩码逐位解成中文，原始值备查
# --------------------------------------------------------------------------- #


def test_target_type_is_decoded_to_chinese_and_raw_is_kept(qapp, instance, context) -> None:
    """③ TargetType 36 -> 「载具 + 舰船」，原始值 36 仍读得到。"""
    page = _page(context)
    assert page.target_cell() is not None and page.target_cell().raw == 36
    assert page.target_text() == "载具 + 舰船"
    assert page.target_raw_text() == "36"
    assert page.target_bits() == ("载具", "舰船")

    labels = _labels(page)
    assert "载具 + 舰船" in labels
    assert any("原始值 TargetType = 36" in text for text in labels)
    assert any("逐位：载具、舰船" in text for text in labels)


def test_target_art_lists_the_traits_the_card_would_draw(qapp, instance, context) -> None:
    """卡片上会画的那几张目标图（ammo_target_traits）—— 图取不到就退回它的文案。"""
    from ba_mod_maker.app.viewmodel.unit_card import ammo_target_traits

    traits = ammo_target_traits(context.data, context.data.ammo(AMMO_AP))
    assert traits, "样本弹药应当有目标图（TargetType 36 -> 载具那张）"
    page = _page(context)
    labels = _labels(page)
    for trait in traits:
        assert trait.label in labels, f"目标图 {trait.key} 既没画图也没退回文字"


# --------------------------------------------------------------------------- #
# 同武器的其它弹：点一下换本页（页内换，不动页栈、不写数据）
# --------------------------------------------------------------------------- #


def test_switching_ammo_swaps_the_row_and_keeps_67_columns(qapp, instance, context) -> None:
    """④ 换弹种后右栏换的是另一行、列数还是 67，且数据一个字节没变。"""
    page = _page(context)
    assert page.entries_cells()[0].row_id == AMMO_AP
    assert page.quantity_raw() == 30

    assert page.switch_to(AMMO_HE) is True
    cells = page.entries_cells()
    assert len(cells) == 67
    assert {cell.row_id for cell in cells} == {AMMO_HE}, "右栏还在画上一行"
    assert page.ammo_id == AMMO_HE
    assert page.quantity_raw() == 70
    # 换了弹种，面板头跟着换（Id 在副标题里；抬头是这一行的名字）
    assert f"Ammunitions#{AMMO_HE}" in page.entries_subtitle()
    assert page.entries is not None and page.entries.subtitle_text == page.entries_subtitle()
    assert context.changeset.is_empty, "换一页看看不该写数据"

    assert page.switch_to(AMMO_HE) is False, "已经是这一行，不该重建"
    assert page.switch_to(999_999) is False, "不存在的弹药不该换过去"
    assert page.ammo_id == AMMO_HE


def test_other_ammo_of_the_same_weapon_is_clickable(qapp, instance, context) -> None:
    """左栏列出这一门武器能装的弹（WeaponAmmunitions 同 WeaponId），点一下就换过去。"""
    page = _page(context)
    ids = [ammo_id for ammo_id, _label, _quantity in page.ammo_choices()]
    assert set(ids) == {AMMO_HE, AMMO_AP}
    assert len(ids) == len(set(ids)), "同一弹种不该列两遍"
    names = {ammo_id: label for ammo_id, label, _quantity in page.ammo_choices()}
    assert names[AMMO_AP] == _row_cell(instance, "Ammunitions", AMMO_AP, "HUDName")

    current = _button_with(page, f"弹药模板 #{AMMO_AP}")
    assert "当前" in current.text()
    other = _button_with(page, f"弹药模板 #{AMMO_HE}")
    assert "当前" not in other.text()
    other.click()
    assert page.ammo_id == AMMO_HE
    assert _button_with(page, f"弹药模板 #{AMMO_HE}").text().startswith("▸")


def test_ammo_the_unit_does_not_carry_says_so(qapp, instance, context) -> None:
    """这个单位没配这个弹时如实说，不编一个数；右栏照样 67 列。"""
    # Ammunitions#9（7.62x54mm）挂在 Weapons#7 上，不是这一门 Weapons#2。
    page = _page(context, ammo_id=9)
    assert page.quantity_cell() is None
    assert page.quantity_raw() is None
    assert page.quantity_text() == ""
    assert len(page.entries_cells()) == 67
    assert any("没配这个弹" in text for text in _labels(page))
    assert [choice[0] for choice in page.ammo_choices()] == [AMMO_HE, AMMO_AP]


# --------------------------------------------------------------------------- #
# 图标：取不到就退回文字，绝不留空白
# --------------------------------------------------------------------------- #


def test_no_icon_source_falls_back_to_text(qapp, context) -> None:
    """没有图标来源也能建页：那个位置写弹种名，不是空白框。"""
    page = _page(context, icons=_NoIcons())
    assert len(page.entries_cells()) == 67
    assert page.icon_shown is False
    assert page.icon_fallback_text == "30x165mm AP"
    assert "30x165mm AP" in _labels(page)
    assert any("图标取不到" in widget.toolTip() for widget in _labels_widgets(page))

    # 连"默认来源"都没有（autouse 把 default_icons 换成了 None）时同样能建
    plain = _page(context)
    assert plain.icon_shown is False
    assert plain.icon_fallback_text


def test_icon_from_the_cache_is_used(qapp, context) -> None:
    """有图就用图：族键 ammo_icons，键是 Ammunitions.HUDIcon。"""
    icons = _FakeIcons(_pixmap())
    page = _page(context, icons=icons)
    assert page.icon_shown is True
    assert page.icon_fallback_text == ""
    assert ("ammo_icons", "AMMO_BULLET_SHORT") in icons.calls
    assert _pixmap_labels(page), "图到了却没贴上"
    # 卡片那排目标图同样走图标（族 infocard_targets）
    assert any(family == "infocard_targets" for family, _key in icons.calls)


def test_icon_arriving_later_replaces_the_text(qapp, context) -> None:
    """图是异步到的：到了就重画左栏（IconCache.loaded）。"""
    icons = _FakeIcons(None)
    page = _page(context, icons=icons)
    assert page.icon_shown is False
    assert page.icon_fallback_text

    icons.pixmap_result = _pixmap()
    icons.loaded.emit()
    assert page.icon_shown is True
    assert page.icon_fallback_text == ""
    assert _pixmap_labels(page)


def test_right_column_is_one_entry_per_line_and_scrolls(qapp, instance, context) -> None:
    """设计 §3 块 6：67 列**一条一行**，一屏放不下就滚（不是网格、不是折叠）。"""
    from PySide6.QtWidgets import QScrollArea

    from ba_mod_maker.app.widgets.row_entries import ROW_HEIGHT

    page = _page(context)
    panel = page.entries
    assert panel is not None
    rows = panel.entry_list
    cells = page.entries_cells()
    # 一条一行：67 列 = 67 个行控件（序号连着编）
    assert len(rows.rows) == len(cells) == 67
    assert [row.index_text for row in rows.rows[:3]] == ["1", "2", "3"]
    assert rows.rows[-1].index_text == "67"
    # 一行里四样都在，且次序跟着表列序走（序号 / 中文名 / 值 / 英文列名）
    for row, cell in zip(rows.rows, cells, strict=True):
        assert row.column_text == cell.column
        assert row.name_text == cell.cn
    assert rows.rows[0].value_text == (cells[0].text or "（空）")
    assert rows.rows[4].column_text == "HUDMultiplier", "设计图第 5 行就是它"
    # 滚就滚：整串比一屏高，而且真的挂在一个滚动区里
    assert rows.sizeHint().height() >= 67 * ROW_HEIGHT
    scroll = panel.findChild(QScrollArea)
    assert scroll is not None and scroll.widget() is rows


def test_panel_header_follows_the_design(qapp, instance, context) -> None:
    """面板抬头按设计图 block6：这一行的名字 + Ammunitions#19 · 一条一行，往下滚 + 67 条词条。"""
    page = _page(context)
    assert page.entries is not None
    # 抬头 = **这一行的名字**（设计图写 12.7x99mm —— 实测 display_name 给的就是 HUDName）
    name = _row_cell(instance, "Ammunitions", AMMO_AP, "HUDName")
    assert page.entries.title_text == name
    assert page.entries_title() == name
    # 副标题与右端小结 = 设计图那一行的两截
    assert page.entries_subtitle() == f"Ammunitions#{AMMO_AP} · 一条一行，往下滚"
    assert page.entries.subtitle_text == page.entries_subtitle()
    assert page.entries_summary() == "67 条词条"
    assert page.entries.summary_text == "67 条词条"
    assert "按功能分组" not in page.entries.subtitle_text + page.entries.summary_text
    assert name in _labels(page)
    # 图标位：这一行有真图就画真图，没图退回表 emoji（💥）—— 不留空框
    mark = page.entries_mark()
    assert mark is not None and mark.emoji == "💥"
    assert page.entries.title_mark == mark


def test_panel_header_has_the_back_to_unit_button(qapp, context) -> None:
    """面板头那颗「⬅ 回单位（31 条）」：条数 = Units 那一行的列数（设计图 block6）。"""
    page = _page(context)
    assert page.entries is not None
    assert page.entries_back_label() == "⬅ 回单位（31 条）"
    assert page.entries.back_label == "⬅ 回单位（31 条）"
    # 按下去有反应：面板的 back -> 这一页的 back_requested（外层 _push -> close_page）
    seen: list[str] = []
    page.back_requested.connect(lambda: seen.append("back"))
    _button_in(page, "⬅ 回单位（31 条）").click()
    assert seen == ["back"], "那颗按钮是个死键"


def test_back_button_returns_to_the_unit_in_a_real_page_stack(qapp, instance) -> None:
    """真页面栈里也回得去：面板 back -> EditorPage.back_requested -> _push 的 close_page。

    这一页是「被 push 上来的专用页」，外层只给专用页接了 back_requested
    （unit_editor_screen._push），所以这条链必须在真页面上验一次 —— 不然它就是个死键。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.pages.ammo_page import AmmoPage

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(UNIT)
    screen.open_ammo_page(WEAPON, AMMO_AP)
    page = screen.current_page
    assert isinstance(page, AmmoPage)
    assert len(page.entries_cells()) == 67
    assert page.entries is not None and page.entries.back_label == "⬅ 回单位（31 条）"

    _button_in(page, "⬅ 回单位（31 条）").click()
    assert screen.current_page is screen.main_page, "点了「回单位」没有回到单位编辑器"
    screen.deleteLater()


def test_columns_note_is_a_function_index_not_the_order(qapp, context) -> None:
    """左栏那份速查表只做**功能归类** —— 不能再说「右栏按功能分组显示」。"""
    page = _page(context)
    notes = [text for text in _labels(page) if "功能归类" in text]
    assert len(notes) == 1
    assert "右栏 67 列按表列序一条一行" in notes[0]
    assert "身份 7" in notes[0] and "能打什么 3" in notes[0]
    assert not [text for text in _labels(page) if "按功能分组显示" in text]


# --------------------------------------------------------------------------- #
# 工具与替身
# --------------------------------------------------------------------------- #


class _NoIcons:
    """三个来源全断的替身：没有 pixmap()、更没有 loaded 信号。"""

    def pixmap(self, family, key):
        return None


class _FakeIcons(QObject):
    """只会发一张固定图的 IconCache 替身：断言的是"接线接上了没有"。"""

    loaded = Signal()

    def __init__(self, pixmap=None) -> None:
        super().__init__()
        self.pixmap_result = pixmap
        self.calls: list[tuple[str, str]] = []

    def pixmap(self, family, key):
        self.calls.append((family, str(key)))
        if not key:
            return None
        return self.pixmap_result


def _pixmap():
    from PySide6.QtGui import QColor, QPixmap

    pixmap = QPixmap(20, 6)
    pixmap.fill(QColor(TOKENS.color.text_strong))
    return pixmap


def _labels_widgets(page):
    from PySide6.QtWidgets import QLabel

    return list(page.findChildren(QLabel))


def _pixmap_labels(page):
    return [
        widget
        for widget in _labels_widgets(page)
        if widget.pixmap() is not None and not widget.pixmap().isNull()
    ]


def _row_cell(instance, table, row_id, column):
    data = instance.table(table)
    index = {name: i for i, name in enumerate(data.columns)}
    for row in data.rows:
        if row[index["Id"]] == row_id:
            return row[index[column]]
    raise AssertionError(f"{table} 里没有 #{row_id}")
