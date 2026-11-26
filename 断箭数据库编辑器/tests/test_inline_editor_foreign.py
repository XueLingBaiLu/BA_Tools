"""外键那一格的显示口径：**框里只有名字，原始 Id 在它右边、而且只出现一次**。

用户口径（2026-10-01，指着编成台右栏那一格说）：
「这个 ID 显示，不应该在编辑栏里显示，而是在其右边显示」。

背景（先量后改，`_work/probe_rail_foreign_editor.py`）：候选文本为了分辨那几门
**同名**炮塔写成了「名字  #Id」（`_choice_text`），而可编辑下拉的框里显示的就是
候选项文本 —— 于是 `M4A1  #50` 整个被塞进了编辑框，右边那个 `inline-raw` 标签
空着（因为 `decode()` 对外键给不出名字，实测 text == raw_text == 50）。

这一份守四条，每条都对着一个**真会坏**的地方：

1. 框里不许再出现 `#Id`；它出现在右边那一段；
2. **同名**外键（实测「M27 IAR」两门，Id 1 / 51）仍要解析成"用户选的那一条"，
   不许退化成"多条同名"报错 —— 这一条是改第一版时就当场坏掉的（被自己的探针抓到）；
3. 中栏词条行**本来就有** entry-id 那一列 -> 编辑器里不许再显示一遍（同一个 #2 出现两次）；
4. 右边多出来的那一段**不许把行撑出横向滚动条**（用户在手第五手刚为横向溢出吃过亏）。
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QScrollArea

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 样本：Marine Raiders（步兵，成员的主武器 = Weapons 外键）。
SQUAD = 70
#: 样本：Stryker RV（载具 —— 进的是**三栏**那一页，中栏词条行自带 entry-id 列）。
VEHICLE = 21


def _open_bench(window, unit_id: int = SQUAD):
    window._open_unit_editor(unit_id)
    screen = window._unit_editor
    assert screen.on_bench_page()
    return screen, screen.bench_page()


def _select_member(bench):
    """把右栏选中项挪到**一名班成员**那一行 —— 它才有「主武器 ID」这个外键格。"""
    context = bench.context
    bench._on_row_selected("SquadMembers", context.row_ids("SquadMembers")[0])
    return context


def _raw_label(editor) -> QLabel:
    found = [lb for lb in editor.findChildren(QLabel) if lb.property("role") == "inline-raw"]
    assert found, "编辑器里应当有那一段（role=inline-raw）"
    return found[0]


def _duplicate_named_weapons(instance):
    """全库里 HUDName 一样的那几门武器（没有就 skip —— 换版本可能就没了）。"""
    table = instance.table("Weapons")
    name_col, id_col = table.column_index("HUDName"), table.column_index("Id")
    seen: dict[str, list[int]] = {}
    for row in table.rows:
        seen.setdefault(str(row[name_col]), []).append(row[id_col])
    for name, ids in seen.items():
        if name.strip() and len(ids) >= 2:
            return name, sorted(ids)
    return "", []


# --------------------------------------------------------------------------- #
# ① 框里只有名字，Id 在右边
# --------------------------------------------------------------------------- #


def _rail_ids(rail) -> dict[str, str]:
    """右栏每一行**值右边**那一小段（中文名 -> #Id 文本）。"""
    # strict=True：两边**按下标对齐**（BenchRail._add_line 成对 append）—— 漂了就要当场炸。
    paired = zip(rail._value_labels, rail._id_labels, strict=True)
    return {key: label.text() for (key, _k, _w), label in paired}


def test_the_rail_shows_the_id_at_the_right_without_any_editor(window, qapp) -> None:
    """**编辑栏关着**时也要在值右边显示 Id（用户口径 2026-10-01 的第二句）。

    > 「我需要的是，在没有编辑栏的时候，也在右边显示 ID」

    同时守两条边界：**非外键行不显示**（主键那一行的值本身就是 Id，补一个 `#90` 是废话）；
    外键值为 0（「不使用」）也不显示。「该显示才显示」比"到处都有"难，也更容易被改坏。
    """
    _screen, bench = _open_bench(window)
    _select_member(bench)
    rail = bench.rail
    rail.set_mode("columns")
    qapp.processEvents()

    context = bench.context
    member = context.row_ids("SquadMembers")[0]
    cells = {c.cn or c.column: c for c in context.cells_of_row("SquadMembers", member)}
    ids = _rail_ids(rail)

    # 逐行拿数据对一遍规则（不写死某一行的期望值 —— 样本换一个单位就漂）。
    # 规则：**外键且 Id > 0** 才显示；主键那一行（值本身就是 Id）与「不使用」（0）都不显示。
    assert ids, "右栏一行都没画出来？"
    for key, shown in ids.items():
        cell = cells.get(key)
        assert cell is not None, f"右栏画了数据里没有的一行：{key}"
        want = ""
        if cell.foreign and isinstance(cell.raw, int) and not isinstance(cell.raw, bool):
            want = f"#{cell.raw}" if cell.raw > 0 else ""
        assert shown == want, f"{key}：右边写着 {shown!r}，按规则该是 {want!r}"
    # 至少得**真的**有一行显示出来，否则上面那条循环空转也能过。
    assert any(v for v in ids.values()), f"一行 Id 都没显示：{ids}"


def test_the_edit_box_shows_the_name_and_the_id_stays_at_the_row_right(window, qapp) -> None:
    """点开外键那一格：框里只有名字，Id **仍然只由那一行右边**显示（不出现两份）。"""
    _screen, bench = _open_bench(window)
    _select_member(bench)
    rail = bench.rail
    rail.value_widget("主武器 ID").click()
    qapp.processEvents()

    editor = rail.editor()
    assert isinstance(editor, InlineEditor)
    combo = editor.findChild(QComboBox)
    assert combo is not None
    box = combo.currentText()
    assert "#" not in box, f"编辑框里还带着 Id：{box!r}"
    assert box == editor.cell.text, f"框里应当是显示名：{box!r} != {editor.cell.text!r}"
    # 编辑栏里**不再**自带那一段 —— Id 由行画，否则同一行会出现两个 #67。
    assert _raw_label(editor).text() == ""
    assert _rail_ids(rail)["主武器 ID"] == f"#{editor.cell.raw}", "编辑栏开着时行上那一段必须还在"
    # 下拉候选项**仍然**带 #Id（那几门同名炮塔就靠它分辨）—— 别顺手把它也删了。
    assert any("#" in combo.itemText(i) for i in range(min(5, combo.count())))


# --------------------------------------------------------------------------- #
# ② 同名外键仍要解析成"选中的那一条"
# --------------------------------------------------------------------------- #


def test_duplicate_named_rows_still_resolve_to_the_one_that_is_selected(window) -> None:
    """HUDName 一样的两门武器：框里只有名字，但解析必须是**索引指的那一条**。

    为什么这条最要紧：框里不再有 `#Id` 之后，"一字不差"那条判据就废了，
    同名的那几门会一起落进 `多条同名` 报错 —— 而它们正是当初加 `#Id` 的原因。
    """
    _screen, bench = _open_bench(window)
    instance = window._instance
    name, ids = _duplicate_named_weapons(instance)
    if len(ids) < 2:
        pytest.skip("这一版数据里没有同名武器")

    context = bench.context
    member = context.row_ids("SquadMembers")[0]
    base = next(
        cell
        for cell in context.cells_of_row("SquadMembers", member)
        if cell.column == "PrimaryWeaponId"
    )
    first, second = ids[0], ids[1]

    editor = InlineEditor(dataclasses.replace(base, raw=first, text=name), instance=instance)
    assert editor.value() == first, "初值那一条没解析对"

    combo = editor.findChild(QComboBox)
    position = combo.findData(second)
    assert position >= 0
    combo.blockSignals(True)
    combo.setCurrentIndex(position)
    combo.lineEdit().setText(combo.itemText(position).rsplit("#", 1)[0].strip())
    combo.blockSignals(False)
    assert editor.value() == second, "换成另一条同名的之后解析错了（不许退化成多条同名）"


# --------------------------------------------------------------------------- #
# ③ 宿主已经有 entry-id 列时不许显示两遍
# --------------------------------------------------------------------------- #


def test_a_row_that_already_shows_its_id_does_not_show_it_twice(window, qapp) -> None:
    """三栏中栏的词条行自带 `entry-id` 列 —— 编辑器里不许再显示一遍同一个 Id。

    与右栏那条是**同一条纪律的两个面**：Id 永远在值右边，而且**只出现一次**；
    谁画它由宿主决定（中栏/弹药行自己有那一列，右栏没有 -> 右栏那一段由 BenchRail 画）。
    """
    window._open_unit_editor(VEHICLE)
    screen = window._unit_editor
    page = screen.main_page
    row = next(r for r in page.entries.rows if r.cell.foreign)
    row._value.click()
    qapp.processEvents()

    editor = row.editor
    assert editor is not None
    assert _raw_label(editor).text() == "", "这一行已经有 entry-id 列了，编辑器里不该再来一遍"
    ids = [lb.text() for lb in row.findChildren(QLabel) if lb.property("role") == "entry-id"]
    assert ids == [f"#{row.cell.raw}"], f"行自己那一段该显示原始 Id：{ids}"


# --------------------------------------------------------------------------- #
# ④ 右边那一段不许把行撑出横向滚动条
# --------------------------------------------------------------------------- #


def test_the_id_never_pushes_the_row_into_a_horizontal_scrollbar(window, qapp) -> None:
    """加一段字最容易踩的坑（第五手刚为横向溢出吃过亏）：内容宽度不许超过视口。"""
    _screen, bench = _open_bench(window)
    _select_member(bench)
    bench.rail.value_widget("主武器 ID").click()
    qapp.processEvents()
    overflow = {
        id(area): area.horizontalScrollBar().maximum()
        for area in window.findChildren(QScrollArea)
        if area.horizontalScrollBar().maximum() > 0
    }
    assert not overflow, f"这几处横向溢出了（hbar max）：{overflow}"
