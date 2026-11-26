"""跨表检索。

用户真正想做的事情很少是“打开某张表”，而是“**找到 BTR-82 在哪**”。
24 张表 × 16,687 行，人工翻是不现实的。

为什么放在 core：检索是**数据能力**，不是界面能力——CLI 同样需要它，
而且这样它能脱离 Qt 被测试。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.core.data.dataset import Instance, Table

#: 单次检索返回的上限。
#: 为什么要限：搜 "0" 会命中几千行，全部堆进界面既没意义也会卡。
#: 超出时在结果里明说，不静默截断。
DEFAULT_LIMIT = 500


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一条命中。

    属性
    ----
    table
        表名。
    row
        **原始**行下标（不是过滤后的）——跳转回表格时要用它。
        `-1` 表示命中的是**列名本身**，不是某个单元格。
    column
        命中的列名。
    value
        命中的原始值。
    """

    table: str
    row: int
    column: str
    value: Any

    def describe(self) -> str:
        """给界面看的一行文本。

        列名命中与单元格命中要能一眼分开——前者只告诉你“这列存在”。
        """
        if self.is_column_hit:
            return f"列名命中：{self.column}"
        return f"行 {self.row}  ·  {self.column} = {_brief(self.value)}"

    @property
    def is_column_hit(self) -> bool:
        """是否为“列名命中”而非“单元格命中”。"""
        return self.row < 0


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一次检索的全部结果。"""

    query: str
    hits: tuple[SearchHit, ...]
    truncated: bool
    scanned_cells: int
    scanned_tables: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.hits)

    def by_table(self) -> dict[str, list[SearchHit]]:
        """按表分组，保持表的原先后顺序。"""
        grouped: dict[str, list[SearchHit]] = {}
        for hit in self.hits:
            grouped.setdefault(hit.table, []).append(hit)
        return grouped


def search_instance(
    instance: Instance,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    tables: Sequence[str] | None = None,
) -> SearchResult:
    """在一个实例的表里搜子串（大小写不敏感）。

    第一次调用会把涉及的表全部读进内存（`Instance.table()` 是惰加载的），
    实测 24 张表全量加载约 110ms，属于可接受的一次性开销。
    """
    needle = query.strip().casefold()
    if not needle:
        return SearchResult(query=query, hits=(), truncated=False, scanned_cells=0)

    names = tuple(tables) if tables is not None else instance.table_names
    hits: list[SearchHit] = []
    scanned_tables: list[str] = []
    scanned = 0
    truncated = False

    for name in names:
        table = instance.table(name)
        scanned_tables.append(name)
        table_cells = 0
        for hit, cells in _scan_table(table, needle):
            table_cells = cells
            if len(hits) >= limit:
                truncated = True
                break
            hits.append(hit)
        scanned += table_cells or (table.row_count * table.column_count + table.column_count)
        if truncated:
            break

    return SearchResult(
        query=query,
        hits=tuple(hits),
        truncated=truncated,
        scanned_cells=scanned,
        scanned_tables=tuple(scanned_tables),
    )


def search_all(
    instances: dict[str, Instance],
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    progress: Callable[[str], None] | None = None,
) -> dict[str, SearchResult]:
    """在多个实例上搜同一个关键词。键为实例 id。"""
    results: dict[str, SearchResult] = {}
    for path_id, instance in instances.items():
        if progress is not None:
            progress(path_id)
        results[path_id] = search_instance(instance, query, limit=limit)
    return results


def _scan_table(table: Table, needle: str) -> Iterator[tuple[SearchHit, int]]:
    """扫一张表，逐条交出 `(命中, 截至本条已扫过的单元格数)`。

    第二个值**只增不减**：调用方拿最后一次的值就是这张表的扫描量，
    即使中途因上限退出，也仍然是真数。

    先报**列名命中**：用户搜“ModelFileName”时，他想知道的是**这个列在哪张表**，
    而不是那些列里恰好写着这个字的单元格。
    """
    column_hits = 0
    for column_name in table.columns:
        column_hits += 1
        if needle in column_name.casefold():
            yield (
                SearchHit(table=table.name, row=-1, column=column_name, value=column_name),
                column_hits,
            )
            break

    scanned = column_hits
    for row_index, row in enumerate(table.rows):
        for column_index, value in enumerate(row):
            scanned += 1
            if value is None:
                continue
            if needle in _text_of(value).casefold():
                yield (
                    SearchHit(
                        table=table.name,
                        row=row_index,
                        column=table.columns[column_index],
                        value=value,
                    ),
                    scanned,
                )


def _text_of(value: Any) -> str:
    """把值变成可搜文本。

    与界面展示保持一致：整数值的 float 不带 `.0`，
    否则用户搜 "5500" 会搜不到存成 5500.0 的值。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _brief(value: Any, limit: int = 60) -> str:
    """把值压成短文本（列表里不能出现一整行 JSON）。"""
    text = _text_of(value)
    return text if len(text) <= limit else text[:limit] + "…"
