"""探针：右栏「可瞄准目标」（Ammunitions.TargetType，位掩码）的就地编辑器**排几行**。

用户 2026-11-11：「可瞄准目标这里，还是做成列表式的，不然被遮挡住了，无法正常修改」。

判据读数（一行一项 vs 挤在一行）：

    mask_rows=<n>      <- 改前 1（10 个复选框全在 row 0）/ 改后 10（一行一位）
    mask_overflows=<0/1>  <- 内容宽过大、超出编辑区（被裁掉 = 改不动）

跑法（仓根 = 断箭数据库编辑器）：

    set QT_QPA_PLATFORM=offscreen && .venv\\Scripts\\python.exe _work\\probe_inline_mask_layout.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor  # noqa: E402
from ba_mod_maker.core.edit.cells import FieldCell  # noqa: E402
from ba_mod_maker.core.edit.dictionary import CONTROL_MASK, field_info  # noqa: E402
from ba_mod_maker.core.edit.refs import CellRef  # noqa: E402

#: 用户截图那一格：WeaponAmmunitions 指向的 Ammunitions#464（AMRAAM，TargetType=1944 空）。
ROW_ID = 464


def main() -> int:
    app = QApplication.instance() or QApplication([])
    info = field_info("Ammunitions", "TargetType")
    cell = FieldCell(
        ref=CellRef("Ammunitions", ROW_ID, "TargetType"),
        cn=info.cn,
        doc=info.doc,
        control=CONTROL_MASK,
        kind=info.kind,
        value_kind="int",
        raw=1944,
        text="",
        raw_text="1944",
        values_unknown=False,
        shared_with=1,
        row_label=f"Ammunitions#{ROW_ID}",
        foreign=None,
        values=dict(info.values),
    )
    editor = InlineEditor(cell)
    editor.resize(260, 28)  # 右栏值那一格的宽度量级
    editor.show()
    app.processEvents()

    boxes = [box for _bit, box in editor._mask_boxes]
    ys = []
    max_right = 0
    for box in boxes:
        point = box.mapTo(editor, QPoint(0, 0))
        ys.append(point.y())
        max_right = max(max_right, point.x() + box.width())
    rows = len(set(ys))
    overflow = 1 if max_right > editor.width() else 0
    print(f"mask_bits={len(boxes)}")
    print(f"mask_rows={rows}")
    print(f"mask_overflows={overflow}")
    print(f"mask_max_right={max_right}")
    print(f"editor_width={editor.width()}")
    print(f"editor_hint_h={editor.sizeHint().height()}")
    print(f"editor_hint_w={editor.sizeHint().width()}")
    return 0


if __name__ == "__main__":
    # ❗ offscreen 平台在解释器退出、Qt 析构那一刻会返 1（字体目录缺失那条警告）——
    #    判据要的是"命令能跑、读到数"，所以刷完输出直接走 os._exit，别让 Qt 定退出码。
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
