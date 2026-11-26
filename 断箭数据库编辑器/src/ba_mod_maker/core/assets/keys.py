"""从实例里收集全部图标键。

预加载要"一次把图都取好"，先得知道**要取哪些键**。
键有两个来源（见 families.FAMILIES）：DB 的列（column 字段），
以及族自己写死的常量键（keys 字段 —— 例：目标类型图标，数据表里没有这一列）。
去重后按族分组。
"""

from __future__ import annotations

from collections.abc import Iterable

from ba_mod_maker.core.assets.families import FAMILIES


def icon_keys(instance) -> dict[str, tuple[str, ...]]:
    """实例 -> {族名: (键, ...)}。

    没键的族直接不出现在结果里（宁缺勿错：表/列不在就说明这一版数据没有它，
    常量键也没有就说明这一族本来就没图）。
    """
    grouped: dict[str, tuple[str, ...]] = {}
    for family in FAMILIES:
        seen: dict[str, None] = {}
        table_name, _, column = family.column.partition(".")
        if table_name and column:
            try:
                table = instance.table(table_name)
                index = table.column_index(column)
            except (KeyError, AttributeError):
                index = -1
            if index >= 0:
                for row in table.rows:
                    value = row[index] if index < len(row) else None
                    if isinstance(value, str) and value.strip():
                        seen.setdefault(value.strip(), None)
        for key in family.keys:
            if key:
                seen.setdefault(key, None)
        if seen:
            grouped[family.name] = tuple(seen)
    return grouped


def total_keys(grouped: dict[str, Iterable[str]]) -> int:
    return sum(len(v) for v in grouped.values())


__all__ = ["icon_keys", "total_keys"]
