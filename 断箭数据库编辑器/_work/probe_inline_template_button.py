"""量「共享行那颗 📄（另存为新模板）」还在不在 —— 改前 1，改后 0。

用户 2026-11-11（指着右栏编辑浮层那条悬浮提示）：「不需要这个复制功能，删掉」。
这条探针只数**浮层里的按钮**，不碰数据库、不碰界面外的东西。

❗ 控制台是 GBK：按钮文案里有 emoji，直接 print 会 UnicodeEncodeError ——
   所以文案一律转成 unicode_escape 再打印。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "src")

from PySide6.QtWidgets import QApplication, QPushButton

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.refs import CellRef


def _esc(text: str) -> str:
    return text.encode("unicode_escape").decode("ascii")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    cell = FieldCell(
        ref=CellRef("Armors", 1, "MaxHealth"),
        cn="生命值",
        doc="",
        control="text",
        kind="int",
        value_kind="int",
        raw=100,
        text="100",
        raw_text="100",
        values_unknown=False,
        shared_with=4,
        row_label="Armors#1",
        foreign=None,
    )
    editor = InlineEditor(cell, link_ref=CellRef("Options", 504, "ArmorId"))
    editor.show()
    app.processEvents()
    buttons = editor.findChildren(QPushButton)
    print(f"shared_with={cell.shared_with} has_link_ref=True")
    print(f"template_button_count={len(buttons)}")
    print("button_texts=" + repr([_esc(b.text()) for b in buttons]))
    print("button_tips=" + repr([_esc(b.toolTip()) for b in buttons]))
    print(f"editor_height={editor.height()}")
    editor.close()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
