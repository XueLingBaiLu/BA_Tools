"""数据源管理：定位、加载、代表 `DataBaseCompiled` 的 24 张表。

对象模型：

    Dataset          整个数据源（schema + N 个实例）
      -> Instance    一个实例（比如 52085），含 24 张表
        -> Table     一张表（列名 + 行）

关键设计决定：

- **列的权威来源是 `_schema.json`，不是 JSON 文件的第一行。**
  实测：两者完全一致（24/24 张表、0 处不匹配）。
- **行内存形式是 `list`（按列顺序）而非 `dict`。**
  表格渲染按索引取值，列表比字典快，且避免每行都存一份列名。

❗ 本包不得 import 任何 Qt 符号。
"""

from ba_mod_maker.core.data.columns import (
    ColumnKind,
    ColumnSpec,
    format_value,
    infer_column_specs,
    parse_value,
)
from ba_mod_maker.core.data.dataset import Dataset, Instance, Table
from ba_mod_maker.core.data.loader import (
    DATASET_DIRNAME,
    find_dataset_root,
    load_dataset,
    read_table,
)
from ba_mod_maker.core.data.schema import (
    DEFAULT_NON_CAMPAIGN_ROLE,
    TableSchema,
    load_instance_roles,
    load_schema,
)
from ba_mod_maker.core.data.search import (
    DEFAULT_LIMIT,
    SearchHit,
    SearchResult,
    search_all,
    search_instance,
)

__all__ = [
    "DATASET_DIRNAME",
    "DEFAULT_NON_CAMPAIGN_ROLE",
    "DEFAULT_LIMIT",
    "ColumnKind",
    "ColumnSpec",
    "Dataset",
    "Instance",
    "SearchHit",
    "SearchResult",
    "Table",
    "TableSchema",
    "find_dataset_root",
    "format_value",
    "infer_column_specs",
    "load_dataset",
    "load_instance_roles",
    "load_schema",
    "parse_value",
    "read_table",
    "search_all",
    "search_instance",
]
