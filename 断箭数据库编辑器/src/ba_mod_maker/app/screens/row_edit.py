"""「点值就地改」那条线的共用小工具（国家页 / 专精页 / 数据表页用）。

三个界面（单位编辑器、国家页、专精页）改值的动作是同一套：

    点值 -> 就地冒出 InlineEditor -> 值定了 -> 写进变更集（**只进缓存**）。

落盘不在这条线上：只有顶栏那颗「💾 保存」（app/shell/edit_session.py）。

单位编辑器那一份写在 pages/base.py::EditorPage._open_inline_editor 里（它还要管
撤销栈与「另存为新模板」）。这两个新页不需要撤销栈，所以这里只把
"造控件 + 接提交" 抽出来，避免抄第三遍。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QWidget

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.refs import CellRef


def build_inline_editor(
    cell: FieldCell,
    *,
    instance: Instance | None,
    on_commit: Callable[[CellRef, Any], None],
    on_cancel: Callable[[], None],
    link_ref: CellRef | None = None,
    parent: QWidget | None = None,
) -> InlineEditor:
    """造一个挂在这一格上的就地编辑控件（提交 / 撤手都接好）。

    值取不到（外键名字打错这类）时控件自己会把 hint 显示在输入框下面 ——
    那是 InlineEditor 的活，这里不吞异常、也不假装提交成功。
    """
    editor = InlineEditor(cell, instance=instance, link_ref=link_ref, parent=parent)
    editor.committed.connect(lambda value, ref=cell.ref: on_commit(ref, value))
    editor.cancelled.connect(on_cancel)
    return editor


__all__ = ["build_inline_editor"]
