"""列的**值类型系统**：推断、校验、文本↔值 转换。

为什么需要它：JSON 里的值是有类型的（int / float / bool / str / null），
而用户在表格里敲进去的永远是**文本**。不做类型转换就写回去，
会把 `5500` 变成 `"5500"`，游戏读到字符串会直接出错或静默失效。

实测依据（本数据源）：

- `WeaponAmmunitions` 6 列**全是 int**；
- `Ammunitions` 67 列里 38 列是 float、11 列 bool；
- `Units` 同时有 int / float / str / bool / null。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ba_mod_maker.core.errors import TableError


class ColumnKind(StrEnum):
    """一个列的值类型。

    只有五种，因为数据里只会出现这五种。"""

    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    TEXT = "text"
    UNKNOWN = "unknown"

    @property
    def is_numeric(self) -> bool:
        return self in (ColumnKind.INT, ColumnKind.FLOAT)


#: 用户输入里被当作“真”的写法。
_TRUE_WORDS = frozenset({"true", "1", "yes", "y", "on", "是", "真", "✓"})
#: 被当作“假”的写法。空串不在此列：空串对 bool 列意味着 False。
_FALSE_WORDS = frozenset({"false", "0", "no", "n", "off", "否", "假", "✗", ""})


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """一列的推断结果。

    属性
    ----
    name
        列名。
    kind
        推断出的类型。
    nullable
        该列是否存在 null 值。决定空串输入应当变成
        `None`（该列允许）还是报错（该列不允许）。
    """

    name: str
    kind: ColumnKind
    nullable: bool

    @property
    def placeholder(self) -> str:
        """编辑框的提示文本（告诉用户该填什么格式）。"""
        if self.kind == ColumnKind.BOOL:
            return "是 / 否"
        if self.kind == ColumnKind.INT:
            return "整数" + ("（可留空）" if self.nullable else "")
        if self.kind == ColumnKind.FLOAT:
            return "小数" + ("（可留空）" if self.nullable else "")
        return "（可留空）" if self.nullable else ""


def infer_column_specs(
    columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> tuple[ColumnSpec, ...]:
    """从实际数据推断每一列的类型。

    策略：取该列第一个非 null 值的类型。
    为什么不看全部行：24 张表实测类型一致（同列不混类），
    而全量扫在 2357 行 × 67 列时是白花的开销。

    **类型冲突时降级为 TEXT**：一旦发现同列出现云两种不同类型，
    就不能再按某一种类型去转换用户输入——宁可当文本，也不能猜。
    """
    specs: list[ColumnSpec] = []

    for column_index, name in enumerate(columns):
        first_kind: ColumnKind | None = None
        mixed = False
        nullable = False

        for row in rows:
            value = row[column_index] if column_index < len(row) else None
            if value is None:
                nullable = True
                continue
            kind = _kind_of(value)
            if first_kind is None:
                first_kind = kind
            elif kind != first_kind:
                mixed = True
                break

        if first_kind is None:
            kind = ColumnKind.UNKNOWN
        elif mixed:
            kind = ColumnKind.TEXT
        else:
            kind = first_kind

        specs.append(ColumnSpec(name=name, kind=kind, nullable=nullable))

    return tuple(specs)


def parse_value(spec: ColumnSpec, text: str) -> Any:
    """把用户输入的文本按列类型转成 JSON 值。

    异常
    ----
    TableError
        文本不能转成该列的类型，或该列不允许空值。
        异常里带 `hint`，界面可以直接展示给用户。
    """
    raw = text.strip()

    if spec.kind == ColumnKind.BOOL:
        lowered = raw.lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
        raise TableError(
            f"列 {spec.name} 是是非值，读不懂“{text}”",
            hint="请填 是 / 否 / true / false / 1 / 0",
            column=spec.name,
        )

    if raw == "":
        if spec.nullable:
            return None
        raise TableError(
            f"列 {spec.name} 不允许留空",
            hint="该列在原数据里没有空值",
            column=spec.name,
        )

    if spec.kind == ColumnKind.INT:
        try:
            number = int(raw, 10)
        except ValueError:
            raise TableError(
                f"列 {spec.name} 需要整数，读不懂“{text}”",
                hint="例：5500、-1、0",
                column=spec.name,
            ) from None
        return number

    if spec.kind == ColumnKind.FLOAT:
        try:
            return float(raw)
        except ValueError:
            raise TableError(
                f"列 {spec.name} 需要数值，读不懂“{text}”",
                hint="例：1.0、0.25、-3",
                column=spec.name,
            ) from None

    # TEXT / UNKNOWN：原样存。
    # UNKNOWN 不能当空值处理，否则用户永远填不进去东西。
    return text


def format_value(spec: ColumnSpec, value: Any) -> str:
    """把 JSON 值变成编辑框里的文本。

    与 `app.widgets.table_model.cell_text` 的区别：后者面向**只读展示**
    （bool 变 ✓、float 去 .0），而本函数面向**可编辑**——必须给出能被
    原样解析回去的文本，false 就是 "false" 而不是空串。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _kind_of(value: Any) -> ColumnKind:
    """单个值的类型。注意 bool 必须先于 int 判断。"""
    if isinstance(value, bool):
        return ColumnKind.BOOL
    if isinstance(value, int):
        return ColumnKind.INT
    if isinstance(value, float):
        return ColumnKind.FLOAT
    if isinstance(value, str):
        return ColumnKind.TEXT
    return ColumnKind.UNKNOWN
