"""「这一格落在哪」—— 词条格子的纯函数（零 Qt）。

三件事在 core 里各有一份实现，界面只做展示（这一条是任务书点名的）：

| 问题 | 在哪 | 函数 |
|---|---|---|
| 哪些行属于这个单位 | core/edit/scope.py | unit_scope |
| 一行被几个单位共用 | core/edit/sharing.py | SharingIndex.of |
| **这一格落在哪** | **本模块** | cell_at / unit_cells |

## 「落在哪」包含四样

1. **寻址**：CellRef(表名, 行 Id, 列名) —— 编辑按它写（Changeset 按 Id 寻址）；
2. **中文名与说明**：core/edit/dictionary.py（词典）；
3. **显示值**：枚举 / 掩码 / 布尔解成中文，原始值留在 raw_text；
4. **共享度**：传了 SharingIndex 就带上"这一行被几个单位用"。

## 为什么格子要一次性全算出来

右栏要按表分组显示 1211 个格子（BTR-82A）。逐格去问 scope / sharing
就是 1211 次全表扫；这里一次算完再切片，实测单位级 ~60ms。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ba_mod_maker.core.data.columns import (
    ColumnKind,
    ColumnSpec,
    infer_column_specs,
    parse_value,
)
from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.edit.dictionary import FieldInfo, control_for, decode, field_info
from ba_mod_maker.core.edit.foreign import display_name, resolved_target
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef, UnitScope, unit_scope
from ba_mod_maker.core.edit.sharing import SharingIndex

#: 取值类型的字符串形态（infer_column_specs 给的是枚举，这里统一成 str 传）。
_KIND_TEXT: dict[ColumnKind, str] = {
    ColumnKind.INT: "int",
    ColumnKind.FLOAT: "float",
    ColumnKind.BOOL: "bool",
    ColumnKind.TEXT: "text",
    ColumnKind.UNKNOWN: "unknown",
}


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """一列的静态信息（与具体哪一行无关）。**按表的全部列**给，不筛。"""

    table: str
    column: str
    index: int
    cn: str
    doc: str
    kind: str
    control: str
    value_kind: str
    foreign: str | None
    #: 枚举 / 掩码的值域。compare=False：字典不可哈希，而格子要能放进 set。
    values: dict[str, str] = field(compare=False, default_factory=dict)
    missing: bool = False

    @property
    def is_foreign(self) -> bool:
        return self.foreign is not None

    @property
    def subtitle(self) -> str:
        """「原列名」那一行小字。表名与行 Id 由调用方拼（它有行上下文）。"""
        return self.column


@dataclass(frozen=True, slots=True)
class FieldCell:
    """一格：**落在哪**（ref）+ 中文名 + 显示值 + 共享度。

    ref 是编辑用的寻址；row_label 是"这一行叫什么"（关联行没有名字时
    是「表 #Id」这种说明性文本，不编假名字）。
    """

    ref: CellRef
    cn: str
    doc: str
    control: str
    kind: str
    value_kind: str
    raw: Any
    text: str
    raw_text: str
    values_unknown: bool
    shared_with: int
    row_label: str
    foreign: str | None
    #: 这一格**该跳到哪一行**（外键才有）：`(表, 行 Id)`。
    #:
    #: ❗ 与外键指向的那张表**不一定相同**：指向"关联表"（没有名字列的那种，例
    #: `SpecializationAvailabilities`）时，要跳到它**真正指着的那个单位** ——
    #: 用户 2026-10-02 原话：「应该是单位的 HUDName……而且我要能直接跳转到那个单位」。
    #: 判断只有 `foreign.resolved_target` 一份，显示与跳转都用它。
    link: tuple[str, int] | None = None
    values: dict[str, str] = field(compare=False, default_factory=dict)
    missing: bool = False

    @property
    def table(self) -> str:
        return self.ref.table

    @property
    def row_id(self) -> int:
        return self.ref.row_id

    @property
    def column(self) -> str:
        return self.ref.column

    @property
    def place(self) -> str:
        """「表#Id · 原列名」那行小字。"""
        return f"{self.ref.table}#{self.ref.row_id} · {self.ref.column}"

    @property
    def is_shared(self) -> bool:
        return self.shared_with > 1

    def edited_value_text(self, value: Any) -> str:
        """把"改完之后的值"也解成中文（界面要就地回显）。"""
        info = _info_from(self)
        return decode(info, value).text


def _info_from(cell: FieldCell) -> FieldInfo:
    return FieldInfo(
        table=cell.table,
        column=cell.column,
        cn=cell.cn,
        doc=cell.doc,
        kind=cell.kind,
        control=cell.control,
        values=cell.values,
        evidence="",
        foreign=cell.foreign,
        missing=cell.missing,
    )


def table_columns(instance: Instance, table_name: str) -> tuple[ColumnInfo, ...]:
    """一张表的**全部列**（一个不少）。四个专用页按它渲染"40 / 67 / 46 列"。

    取值类型从**实际数据**推断（core/data/columns.py），
    词典只给中文名与枚举 —— 两者分开，各自可单独断言。
    """
    if table_name not in instance.table_names:
        return ()
    table = instance.table(table_name)
    specs = infer_column_specs(table.columns, table.rows)
    out: list[ColumnInfo] = []
    for index, column in enumerate(table.columns):
        info = field_info(table_name, column)
        value_kind = _KIND_TEXT.get(specs[index].kind, "unknown")
        out.append(
            ColumnInfo(
                table=table_name,
                column=column,
                index=index,
                cn=info.cn,
                doc=info.doc,
                kind=info.kind,
                control=control_for(info, value_kind),
                value_kind=value_kind,
                foreign=info.foreign,
                values=dict(info.values),
                missing=info.missing,
            )
        )
    return tuple(out)


def cell_at(
    instance: Instance,
    ref: CellRef,
    *,
    sharing: SharingIndex | None = None,
) -> FieldCell | None:
    """单独取一格；行不存在返回 None（界面上"这行被删了"应当是空，不是报错）。"""
    if ref.table not in instance.table_names:
        return None
    table = instance.table(ref.table)
    if ref.column not in table.columns:
        return None
    id_index = table.column_index("Id")
    row = None
    for candidate in table.rows:
        if candidate[id_index] == ref.row_id:
            row = candidate
            break
    if row is None:
        return None
    return _build(instance, table, row, ref.column, sharing=sharing)


def unit_cells(
    instance: Instance,
    unit_id: int,
    *,
    sharing: SharingIndex | None = None,
    scope: UnitScope | None = None,
) -> tuple[FieldCell, ...]:
    """这个单位的全部格子，按 scope 的稳定序（表名 -> 行 Id -> 列序）。"""
    scope = scope if scope is not None else unit_scope(instance, unit_id)
    if not scope.rows:
        return ()

    # 每张表只建一次索引：1211 个格子逐格扫表就是 1211 次全表扫。
    cache: dict[str, tuple[Any, dict[int, list]]] = {}
    out: list[FieldCell] = []
    for row_ref in scope.rows:
        table = instance.table(row_ref.table)
        entry = cache.get(row_ref.table)
        if entry is None:
            id_index = table.column_index("Id")
            entry = (id_index, {row[id_index]: row for row in table.rows})
            cache[row_ref.table] = entry
        row = entry[1].get(row_ref.row_id)
        if row is None:  # pragma: no cover - scope 已经筛过存在性
            continue
        for column in table.columns:
            built = _build(instance, table, row, column, sharing=sharing)
            if built is not None:
                out.append(built)
    return tuple(out)


def row_cells(
    instance: Instance,
    row_ref: RowRef,
    *,
    sharing: SharingIndex | None = None,
) -> tuple[FieldCell, ...]:
    """一行的全部格子（专用页用它渲染"这一行的上下文"）。"""
    if row_ref.table not in instance.table_names:
        return ()
    table = instance.table(row_ref.table)
    id_index = table.column_index("Id")
    row = None
    for candidate in table.rows:
        if candidate[id_index] == row_ref.row_id:
            row = candidate
            break
    if row is None:
        return ()
    cells = [_build(instance, table, row, column, sharing=sharing) for column in table.columns]
    return tuple(cell for cell in cells if cell is not None)


def group_by_table(cells: tuple[FieldCell, ...]) -> tuple[tuple[str, tuple[FieldCell, ...]], ...]:
    """按表分组，**保持第一次出现的顺序**（scope 的稳定序）。

    为什么不用 dict 直接返回：界面要顺序，而 dict 在 3.7+ 有序但语义上
    不承诺"按首次出现" —— 这里显式说出来。
    """
    order: list[str] = []
    buckets: dict[str, list[FieldCell]] = {}
    for cell in cells:
        if cell.table not in buckets:
            buckets[cell.table] = []
            order.append(cell.table)
        buckets[cell.table].append(cell)
    return tuple((name, tuple(buckets[name])) for name in order)


def parse_cell_text(cell: FieldCell, text: str) -> Any:
    """把用户在编辑框里敲的文本转成该列该有的类型。

    **为什么必须有这一步**：JSON 里的值是有类型的，用户在框里敲的永远是文本。
    不做转换就会把 5500 变成 "5500"，游戏读到字符串直接出错或静默失效
    （core/data/columns.py 的模块注释写的就是这件事）。
    转换规则只有那一份实现：core.data.columns.parse_value。

    转换不了时抛 TableError（带 hint），界面直接把 hint 显示在输入框下面。
    """
    spec = ColumnSpec(
        name=cell.column,
        kind=_COLUMN_KIND.get(cell.value_kind, ColumnKind.TEXT),
        nullable=True,
    )
    return parse_value(spec, text)


#: 取值类型字符串 -> ColumnKind。两边各有一套名字（一边给界面看，一边给解析用）。
_COLUMN_KIND: dict[str, ColumnKind] = {
    "int": ColumnKind.INT,
    "float": ColumnKind.FLOAT,
    "bool": ColumnKind.BOOL,
    "text": ColumnKind.TEXT,
    "unknown": ColumnKind.UNKNOWN,
}


def display_value(instance: Instance, table: str, column: str, value: Any) -> str:
    """单独解一个值的显示文本（专用页的"改完回显"用它）。"""
    info = field_info(table, column)
    specs = _kind_of_value(value)
    control_for(info, specs)  # 只为触发控件判定；解码本身不看控件
    return decode(info, value).text


def _kind_of_value(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "text"
    return "unknown"


def _kind_from_column(table: Table, column: str) -> str:
    """当前格是 null 时，用**同一列其它行**推断列类型（类型来自证据，不来自这一格）。

    只在这一格推不出来（`unknown`）时才调用 —— 也就是 null 格。
    扫到第一个非 null 就返回它的类型；全列都是 null（如 `Mobility.FlyPreset`、`Note` 这类
    真·占位列）返回 `unknown`，编辑框仍按文本处理（与从前一致）。

    ⚠️ O(行数)，只在 null 格上走；实测 24 张表里 null 最密的是 `Mobility.ModelFileName`
    （261/268 行），
    整表建格也只是几万次比较，不值当为它加缓存（加了就有"数据改了缓存没跟上"的新坑）。
    """
    position = table.column_index(column)
    for row in table.rows:
        kind = _kind_of_value(row[position])
        if kind != "unknown":
            return kind
    return "unknown"


def _build(
    instance: Instance,
    table,
    row: list,
    column: str,
    *,
    sharing: SharingIndex | None,
) -> FieldCell | None:
    if column not in table.columns:
        return None
    position = table.column_index(column)
    id_index = table.column_index("Id")
    row_id = row[id_index]
    value = row[position]

    info = field_info(table.name, column)
    value_kind = _kind_of_value(value)
    if value_kind == "unknown":
        # ❗ 这一格是 null 时，光看它自己推不出列类型。退回去看**同一列其它行**：
        #   全表都是 int 的列，编辑框就必须按 int 解析 —— 否则用户敲的 2 会原样变成 '2'。
        #   实测事故（2026-11-03）：从 null 格起手改
        #   SpecializationAvailabilities.MaxAvailabilityXp0，
        #   写回的是字符串；游戏能转，但产品自己 armory._int() 读成 0（只认 int）。
        #   真正全空的列（如 Note）扫完仍返回 unknown ⇒ 仍按文本处理，行为不变。
        value_kind = _kind_from_column(table, column)
    decoded = decode(info, value)
    shared = sharing.of(table.name, row_id) if sharing is not None else 0

    # 外键：界面上**大字是名字**、小字才是「表#Id · 原列名」（设计 §6）。
    # 0 = 「不使用」，不是「指向 Id 0」（关系图 §5 第 1 条）—— 如实写出来。
    label = ""
    text = decoded.text
    link: tuple[str, int] | None = None
    if info.foreign is not None and value is not None:
        if _positive(value):
            label = display_name(instance, info.foreign, value)
            # 指向**关联表**（没有名字列的那种）时，这一格显示成它**指着的那个单位** ——
            # 用户 2026-10-02：`TransportAvailabilities.乘客侧` 从前写
            # "SpecializationAvailabilities #773"，「应该是单位的 HUDName，不应该是这么一长串」。
            # ❗ 只在**这一格**上走这一跳（`display_name` 自己不动：它还被挑行的下拉用着，
            #    那边要的是"这一行是什么"）。`resolved_target` 是显示与跳转共用的那一份判断。
            via = resolved_target(instance, info.foreign, int(value))
            if via is not None:
                nested = display_name(instance, via[0], via[1])
                if nested and not nested.startswith("（"):
                    label = nested
            text = label
            # 关联表（没有名字列）再往下走一跳 —— 跳到**它指着的那个单位**，
            # 而不是那张没有名字的中间表（与显示同一份判断）。
            link = resolved_target(instance, info.foreign, int(value)) or (
                info.foreign,
                int(value),
            )
        else:
            label = "（不使用）"
            text = "（不使用）"
    if not label:
        label = display_name(instance, table.name, row_id)

    return FieldCell(
        ref=CellRef(table.name, row_id, column),
        cn=info.cn,
        doc=info.doc,
        control=control_for(info, value_kind),
        kind=info.kind,
        value_kind=value_kind,
        raw=value,
        text=text,
        raw_text=decoded.raw_text,
        values_unknown=decoded.unknown,
        shared_with=shared,
        row_label=label,
        foreign=info.foreign,
        link=link,
        values=dict(info.values),
        missing=info.missing,
    )


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = [
    "ColumnInfo",
    "FieldCell",
    "cell_at",
    "display_value",
    "group_by_table",
    "parse_cell_text",
    "row_cells",
    "table_columns",
    "unit_cells",
]
