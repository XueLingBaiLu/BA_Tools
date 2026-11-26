"""量「值右边那一小段灰字」（`_preview`）在位掩码上是不是重复的那一行字。

用户 2026-11-11 截图红圈：右栏「可瞄准目标」展开的列表中部浮着
「直升机 · 飞机 · 投射物 · 防空压制 · 巡航导弹」—— 说「我不需要，太碍眼了」。

这条探针只读数：折叠摘要 ／ preview 文本与长度 ／ 展开后 preview 落在哪一行。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "src")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.dictionary import CONTROL_MASK, field_info
from ba_mod_maker.core.edit.refs import CellRef


def _esc(text: str) -> str:
    return text.encode("unicode_escape").decode("ascii")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    info = field_info("Ammunitions", "TargetType")
    cell = FieldCell(
        ref=CellRef("Ammunitions", 464, "TargetType"),
        cn=info.cn,
        doc=info.doc,
        control=CONTROL_MASK,
        kind=info.kind,
        value_kind="int",
        raw=1944,
        text="1944",
        raw_text="1944",
        values_unknown=False,
        shared_with=1,
        row_label="WeaponAmmunitions#4240",
        foreign=None,
        values=dict(info.values),
    )
    editor = InlineEditor(cell)
    editor.show()
    app.processEvents()
    print("collapsed_summary=" + _esc(editor.mask_summary()))
    print("collapsed_preview=" + _esc(editor._preview.text()))
    print(f"collapsed_preview_len={len(editor._preview.text())}")
    editor.toggle_mask_list()
    app.processEvents()
    print(f"expanded_editor_h={editor.height()}")
    print(f"expanded_preview_y={editor._preview.mapTo(editor, QPoint(0, 0)).y()}")
    print(f"expanded_button_h={editor._mask_selector.button.height()}")
    print("expanded_preview=" + _esc(editor._preview.text()))
    print(f"expanded_preview_len={len(editor._preview.text())}")
    editor.close()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
