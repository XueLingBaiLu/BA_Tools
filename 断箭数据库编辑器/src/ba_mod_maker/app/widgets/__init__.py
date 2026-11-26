"""可复用的自包含控件。

❗ 这里的每个控件都必须**可单独嵌入**：不依赖主窗口、不假设布局。
首屏设计变更时，这里的控件应该原样搬过去就能用。

| 控件 | 职责 | 对外入口 |
|---|---|---|
| `TableBrowser` | 单张表的浏览与编辑 | `set_instance()` / `select_table()` / `select_cell()` |
| `GlobalSearch` | 跨 24 张表检索 | `set_instance()` / `run_search()` / `focus_input()` |
| `RowEntriesPanel` | **一行词条**（序号│中文名│值│英文列名） | `set_cells()` / `open_editor()` |
| `BreadcrumbBar` | 顶栏面包屑，父级可点 | `set_trail()` |
| `SavePill` | 顶栏常驻保存状态胶囊 | `mark_saved()` / `mark_failed()` |
| `ReferencedByPanel` | 数据表页底部「被谁引用」（只读） | `show_hits()` / `hit_activated` |

都不读磁盘：数据由外层通过 `set_instance()` / `set_cells()` / `show_hits()` 传入，
**写**则由外层注入写通道（`TableWriteChannel` -> `app/shell/edit_session.py`）——
"改一格只进那一份账本"这条口径在包外决定，包内不自己记。
"""

from ba_mod_maker.app.widgets.breadcrumb import BreadcrumbBar
from ba_mod_maker.app.widgets.delegates import CellDelegate
from ba_mod_maker.app.widgets.flow_layout import FlowLayout
from ba_mod_maker.app.widgets.global_search import GlobalSearch
from ba_mod_maker.app.widgets.refs_panel import ReferencedByPanel
from ba_mod_maker.app.widgets.row_entries import EntryRow, RowEntriesPanel
from ba_mod_maker.app.widgets.save_pill import SavePill
from ba_mod_maker.app.widgets.table_browser import TableBrowser
from ba_mod_maker.app.widgets.table_model import TableModel, TableWriteChannel, cell_text, row_id_of

__all__ = [
    "BreadcrumbBar",
    "CellDelegate",
    "EntryRow",
    "FlowLayout",
    "GlobalSearch",
    "ReferencedByPanel",
    "RowEntriesPanel",
    "SavePill",
    "TableBrowser",
    "TableModel",
    "TableWriteChannel",
    "cell_text",
    "row_id_of",
]
