"""编辑核心（零 Qt）。

- changeset —— 界面里**所有**编辑的唯一入口（按 Id 寻址、留旧值、可单条撤销；
  列表改动与行级改动分开记）。
- clone —— 「另存为新模板」与加/删行（用户定的第 1 条：共享行可改，但别人不动）。
- sharing —— 一个模板行被多少个单位共用（卡片上要标出来）。
- refs —— 单元格引用（表名 + 行 Id + 列名）+ **外键列 -> 目标表**的唯一一张表。
- scope —— 「哪些行属于这个单位」（编辑器右栏的边界，**只在 core 里定义一次**）。
- cells —— 「这一格落在哪」：寻址 + 中文名 + 显示值 + 共享度。
- dictionary —— 字段词典：中文名 / 作用说明 / 枚举与位掩码解码。
- foreign —— 外键显示名字、按名字搜。
- package —— 保存成 BA_Mod.zip（平铺 24 条目，未动的表逐字节搬过去）。
- asset_refs / asset_changeset —— **资产侧**的同一件事：落点是 AssetRef（容器 + pathID +
  字段或字节区间），账本是 AssetChangeset（三个原子动词 set / add / delete，⛔ 没有「换」）。

为什么不放在 app/：这里全是可直接断言的纯逻辑，不该挂在 Qt 上
（与 core/data、core/assets 同一条纪律，由 tests/test_core_layering.py 扫）。
"""

from ba_mod_maker.core.edit.asset_changeset import (
    MISSING,
    AssetChangeset,
    AssetEdit,
    AssetPlan,
)
from ba_mod_maker.core.edit.asset_refs import AssetRef, ByteSpan, FieldPath
from ba_mod_maker.core.edit.cells import (
    ColumnInfo,
    FieldCell,
    cell_at,
    display_value,
    group_by_table,
    row_cells,
    table_columns,
    unit_cells,
)
from ba_mod_maker.core.edit.changeset import Change, Changeset, RowChange
from ba_mod_maker.core.edit.clone import add_row, clone_row, next_row_id, save_as_new_template
from ba_mod_maker.core.edit.dictionary import (
    CONTROL_BOOL,
    CONTROL_ENUM,
    CONTROL_FOREIGN,
    CONTROL_MASK,
    CONTROL_NUMBER,
    CONTROL_RESOURCE,
    CONTROL_TEXT,
    Decoded,
    FieldInfo,
    control_for,
    decode,
    field_info,
)
from ba_mod_maker.core.edit.foreign import (
    NameHit,
    display_name,
    find_row,
    name_column,
    search_rows,
)
from ba_mod_maker.core.edit.package import (
    PACKAGE_FORMAT,
    PackagePlan,
    PackagePolicy,
    plan_package,
    policy_for,
    read_package_rows,
    verify_package,
    write_package,
)
from ba_mod_maker.core.edit.refs import (
    FOREIGN_TABLE,
    NOT_FOREIGN,
    CellRef,
    RefHit,
    foreign_columns,
    foreign_columns_to,
    foreign_table,
    is_foreign,
    referencing_rows,
)
from ba_mod_maker.core.edit.scope import (
    ATTACHED_EDGES,
    RowRef,
    UnitScope,
    entry_names_in_order,
    owned_rows,
    unit_scope,
)
from ba_mod_maker.core.edit.sharing import SharingIndex, build_sharing_index

__all__ = [
    "ATTACHED_EDGES",
    "MISSING",
    "AssetChangeset",
    "AssetEdit",
    "AssetPlan",
    "AssetRef",
    "ByteSpan",
    "CONTROL_BOOL",
    "CONTROL_ENUM",
    "CONTROL_FOREIGN",
    "CONTROL_MASK",
    "CONTROL_NUMBER",
    "CONTROL_RESOURCE",
    "CONTROL_TEXT",
    "FOREIGN_TABLE",
    "NOT_FOREIGN",
    "PACKAGE_FORMAT",
    "CellRef",
    "Change",
    "Changeset",
    "ColumnInfo",
    "Decoded",
    "FieldCell",
    "FieldInfo",
    "FieldPath",
    "NameHit",
    "PackagePlan",
    "PackagePolicy",
    "RefHit",
    "RowChange",
    "RowRef",
    "SharingIndex",
    "UnitScope",
    "add_row",
    "build_sharing_index",
    "cell_at",
    "clone_row",
    "control_for",
    "decode",
    "display_name",
    "display_value",
    "entry_names_in_order",
    "field_info",
    "find_row",
    "foreign_columns",
    "foreign_columns_to",
    "foreign_table",
    "group_by_table",
    "is_foreign",
    "name_column",
    "next_row_id",
    "owned_rows",
    "plan_package",
    "policy_for",
    "read_package_rows",
    "referencing_rows",
    "row_cells",
    "save_as_new_template",
    "search_rows",
    "table_columns",
    "unit_cells",
    "unit_scope",
    "verify_package",
    "write_package",
]
