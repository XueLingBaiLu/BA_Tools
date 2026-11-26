"""探针：位掩码编辑器的**统一形态**（折叠一行 + 可展开勾选列表）。

覆盖**全部 4 个位掩码列**（`field_enums` 里 kind=mask）：

    Abilities.SupplyTargetsMask (1 位) · Ammunitions.TargetType (10 位)
    Ammunitions.UiOptions (1 位) · Units.Type (5 位)

判据读数：

    <col>_collapsed_h=<n>   <- 折叠态编辑器高度（改前 TargetType=10 行高）
    <col>_bits=<n>          <- 位数量
    <col>_has_toggle=<0/1>  <- 有没有「点开」这一手（改前 0 / 改后 1）
    <col>_expanded_rows=<n> <- 展开后列表里可见的项数（应 = bits）

跑法（仓根 = 断箭数据库编辑器）：

    $env:QT_QPA_PLATFORM='offscreen'; .venv\\Scripts\\python.exe _work\\probe_mask_dropdown.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor  # noqa: E402
from ba_mod_maker.core.edit.cells import FieldCell  # noqa: E402
from ba_mod_maker.core.edit.dictionary import CONTROL_MASK, field_info  # noqa: E402
from ba_mod_maker.core.edit.refs import CellRef  # noqa: E402

#: (表, 列, 行 Id, 当前值) —— 覆盖 4 个 mask 列里的 3 个（有 2 个只有 1 位，形态一样）。
SAMPLES = (
    ("Ammunitions", "TargetType", 464, 1944),
    ("Units", "Type", 206, 4),
    ("Abilities", "SupplyTargetsMask", 1, 1),
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


def main() -> int:
    app = QApplication.instance() or QApplication([])
    for table, column, row_id, raw in SAMPLES:
        prefix = f"{table.split('.')[-1]}.{column}".lower()
        editor = InlineEditor(_cell(table, column, row_id, raw))
        editor.show()
        app.processEvents()
        boxes = [box for _bit, box in editor._mask_boxes]
        print(f"{prefix}_collapsed_h={editor.height()}")
        print(f"{prefix}_sizehint_h={editor.sizeHint().height()}")
        print(f"{prefix}_bits={len(boxes)}")
        toggle = getattr(editor, "toggle_mask_list", None)
        print(f"{prefix}_has_toggle={1 if callable(toggle) else 0}")
        if callable(toggle):
            toggle()
            app.processEvents()
            visible = sum(1 for box in boxes if box.isVisibleTo(editor))
            print(f"{prefix}_expanded_h={editor.height()}")
            print(f"{prefix}_expanded_rows={visible}")
        editor.close()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
