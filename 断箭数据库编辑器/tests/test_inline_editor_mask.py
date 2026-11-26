"""位掩码那一格的**统一形态**：折叠一行 ＋ 点开在下方展开的勾选列表。

用户 2026-11-11 三句话：
1. 「可瞄准目标这里，还是做成列表式的，不然被遮挡住了，无法正常修改」
   → 形态选了「行内下拉 ＋ 勾选列表」；
2. 「你改得像一坨屎，没有任何可用性」—— 指的是我上一版把 10 个复选框**竖排铺满整格**：
   右栏只有一格宽，10 行直接把右栏吃掉半屏，还要滚；
3. 「**设计要统一，不要单独只改这一项**」—— 所以这一份**不是**只验 TargetType：
   全库 4 个 `kind=mask` 列里挑 3 个（位数最多 / 中等 / 1 位）一起验，
   任何一个长出特例都会红。

守四条（每条都对着「改回去就会坏」的地方）：

1. **折叠态 = 一行高**（`preferred_height()` 就是宿主给的那一行，不按位数撑高）；
2. **点开才展开**（默认列表不可见；展开后每一项都在列表里、高度才上去）；
3. **勾选不立刻落库**（与文本框 / 下拉同一条契约：点别处才 `_commit()`）；
4. **读数口唯一**（`mask_summary()` / `mask_expanded()` / `toggle_mask_list()`），
   用例与探针共用，别各算一遍。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.dictionary import CONTROL_MASK, field_info
from ba_mod_maker.core.edit.refs import CellRef

#: 宿主给的单行高（`TOKENS.size.row_height` = 26；用例不依赖主题）。
ROW_HEIGHT = 26

#: (表, 列, 行 Id, 当前值, 期望位数) —— 覆盖位数最多 / 中等 / 1 位三种。
SAMPLES = (
    ("Ammunitions", "TargetType", 464, 1944, 10),
    ("Units", "Type", 206, 4, 5),
    ("Abilities", "SupplyTargetsMask", 1, 1, 1),
)


def _cell(table: str, column: str, row_id: int, raw: int) -> FieldCell:
    info = field_info(table, column)
    return FieldCell(
        ref=CellRef(table, row_id, column),
        cn=info.cn,
        doc=info.doc,
        control=CONTROL_MASK,
        kind=info.kind,
        value_kind="int",
        raw=raw,
        text="",
        raw_text=str(raw),
        values_unknown=False,
        shared_with=1,
        row_label=f"{table}#{row_id}",
        foreign=None,
        values=dict(info.values),
    )


def _open(editor: InlineEditor, qapp) -> InlineEditor:
    editor.show()
    qapp.processEvents()
    return editor


@pytest.mark.parametrize(("table", "column", "row_id", "raw", "bits"), SAMPLES)
def test_mask_is_one_row_until_you_open_it(qapp, table, column, row_id, raw, bits) -> None:
    """⭐ 折叠 = **一行高**；点开才展开，且每一项都在列表里。"""
    editor = _open(InlineEditor(_cell(table, column, row_id, raw)), qapp)
    label = f"{table}.{column}"

    assert editor.mask_rows() == bits, f"{label}：位数变了（{editor.mask_rows()} != {bits}）"
    assert editor.preferred_height(ROW_HEIGHT) == ROW_HEIGHT, (
        f"{label}：折叠态应当是**一行高**（不是按位数撑高）"
    )
    assert editor.mask_expanded() is False, f"{label}：默认必须是折叠的"
    boxes = [box for _bit, box in editor._mask_boxes]
    assert boxes, f"{label}：应当有可勾的项"
    assert not any(box.isVisibleTo(editor) for box in boxes), f"{label}：折叠时列表不许画出来"

    editor.toggle_mask_list()
    qapp.processEvents()
    assert editor.mask_expanded() is True, f"{label}：点一下要展开"
    assert all(box.isVisibleTo(editor) for box in boxes), f"{label}：展开后每一项都要在"
    assert editor.height() > ROW_HEIGHT, f"{label}：展开后编辑器要长高（否则被裁）"
    # 一行一项（不是横排挤在一起）：y 互不相同、x 相同。
    ys = {box.mapTo(editor, QPoint(0, 0)).y() for box in boxes}
    xs = {box.mapTo(editor, QPoint(0, 0)).x() for box in boxes}
    assert len(ys) == len(boxes), f"{label}：应当一行一项"
    assert len(xs) == 1, f"{label}：列表项应当左对齐"
    editor.close()


@pytest.mark.parametrize(("table", "column", "row_id", "raw", "bits"), SAMPLES)
def test_checking_does_not_commit_until_you_leave(qapp, table, column, row_id, raw, bits) -> None:
    """⭐ 勾选不立刻落库 —— 与文本框 / 下拉同一条契约（点别处才 `_commit()`）。"""
    editor = _open(InlineEditor(_cell(table, column, row_id, raw)), qapp)
    fired: list[object] = []
    editor.committed.connect(fired.append)
    before = editor.value()

    editor.toggle_mask_list()
    qapp.processEvents()
    _bit, box = editor._mask_boxes[0]
    box.setChecked(not box.isChecked())
    qapp.processEvents()

    assert fired == [], f"{table}.{column}：勾选就提交了 —— 应当等点别处"
    assert editor.value() != before, f"{table}.{column}：勾选没进 value()"
    # 折叠态那颗按钮上的字 = 汇总（选中项用「 · 」连起来）。
    assert editor.mask_summary(), f"{table}.{column}：汇总文本空着"
    editor.close()


@pytest.mark.parametrize(("table", "column", "row_id", "raw", "bits"), SAMPLES)
def test_mask_writes_no_redundant_preview(qapp, table, column, row_id, raw, bits) -> None:
    """⭐ 折叠按钮上已经有「A · B · C」了，值右边那段灰字**不许再写一遍**。

    用户 2026-11-11 指着展开列表中部浮着的那行字：「画红圈里面的东西是什么？
    我不需要，太碍眼了」—— 那行就是 `_preview`（`→ A + B + C（原始值 1944）`），
    与折叠摘要同一件事的第二遍，而且展开时被 QHBoxLayout 垂直居中、正好浮在列表中间。
    """
    editor = _open(InlineEditor(_cell(table, column, row_id, raw)), qapp)
    assert editor.mask_summary() != "", f"{table}.{column}：折叠摘要该有字"
    assert editor._preview.text() == "", f"{table}.{column}：值右边不许再写一遍"
    editor.toggle_mask_list()
    qapp.processEvents()
    assert editor._preview.text() == "", f"{table}.{column}：展开后更不许有那一行"
    editor.close()

def test_a_plain_cell_keeps_the_one_row_contract(qapp) -> None:
    """非位掩码的格子**一点都没变**：仍然只要一行高（统一 ≠ 一刀切）。"""
    info = field_info("Ammunitions", "MaxSpeed")
    cell = FieldCell(
        ref=CellRef("Ammunitions", 464, "MaxSpeed"),
        cn=info.cn,
        doc=info.doc,
        control="text",
        kind=info.kind,
        value_kind="int",
        raw=1,
        text="1",
        raw_text="1",
        values_unknown=False,
        shared_with=1,
        row_label="Ammunitions#464",
        foreign=None,
    )
    editor = _open(InlineEditor(cell), qapp)
    assert editor.preferred_height(ROW_HEIGHT) == ROW_HEIGHT
    assert editor.mask_rows() == 0
    assert editor.mask_expanded() is False
    editor.close()
