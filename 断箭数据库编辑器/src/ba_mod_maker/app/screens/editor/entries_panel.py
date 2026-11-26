"""表名 / 表记号这两张登记表的**转发站** + 「中文名」那一列的宽。

## 真正的家在哪

`TABLE_CN` / `TABLE_EMOJI` / `table_title()` / `table_emoji()` 已经搬到
`app/viewmodel/table_labels.py` —— 搬家的唯一原因：`app/widgets/` 下的控件（数据表页的左栏）
也要用它们，而从 `screens.editor.entries_panel` import 会顺着 `screens/editor/__init__.py`
把 `editable_card` 拉进来、再 import 回 widgets —— **成环**（实测只能用函数内延迟 import 绕）。

这里仍然 re-export 那几个名字（老调用方一行没改）：中栏抬头与副标题、面包屑、词典窗口、
专用页（options_page）都是按老路径取的。

❗ **中栏 = app/widgets/row_entries.py::RowEntriesPanel**，不是本文件。

那个「表 -> 行 -> 列」的老面板（`CellRow` / `_Header` / `RowSection` / `GroupSection`
+ `PLACE_COLUMN_WIDTH` / 三个缩进常量）在 2026-10-01 的第二次清理里**全删**了：它当时已经
一个产品调用方都没有，只剩 `tests/test_unit_editor.py` 拿它们当「中栏不该有折叠栏」的哨兵 ——
哨兵改成**行为面**判据（31 行一条不少 / 一行都没被收起来 / 中栏里没有可勾选的折叠开关）之后，
那几个类就没必要留着当纪念品。别再照它写第二份。
"""

from __future__ import annotations

from ba_mod_maker.app.viewmodel.table_labels import (
    TABLE_CN,
    TABLE_EMOJI,
    table_emoji,
    table_title,
)

#: 「中文名」那一列的宽（设计稿里是等宽的一列，值从同一处起）。
#: ⚠️ 中栏那一份在 app/widgets/row_entries.py（190，自带序号列）；这一份只有改装选项页在用。
NAME_COLUMN_WIDTH = 168

__all__ = [
    "NAME_COLUMN_WIDTH",
    "TABLE_CN",
    "TABLE_EMOJI",
    "table_emoji",
    "table_title",
]
