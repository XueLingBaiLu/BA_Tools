"""词典窗口的数据层（**零 Qt**）。

窗口只负责画；"有哪些表 / 这张表有哪些列 / 这一列怎么解释 / 这一列的取值长什么样"
全在这里算出来 —— 于是这些结论可以脱离窗口直接断言
（`tests/test_dictionary_window.py` 里一半的用例就是这么写的）。

## 数据从哪来（一份都不另抄）

| 来源 | 提供什么 |
|---|---|
| `core/fields/field_labels.json` | 391 列的中文名与说明（产品自带，见 core/fields/README.md） |
| `core/fields/field_enums.json` | 159 列的枚举 / 位掩码 / bool 值域 |
| `core/edit/dictionary.py` | 上面两份的**唯一**读取与解码 API（`field_info` / `decode`） |
| `core/data/columns.py` | 一列的值类型（`infer_column_specs`）与值 → 文本 |
| `core/asset/tables.py` | 24 张表的**规范表名与顺序**（`TABLE_NAMES`），以及表名别名归一 |
| `Instance`（可选） | 取值观测：行数、取值种类、非默认行数、样例 |

**中文表名与 emoji 不在这里再抄一份** —— 由调用方把
`app/screens/editor/entries_panel.py::table_title / table_emoji` 传进来
（那两个函数是界面上"这张表叫什么"的唯一来源）。

## 表名的两套写法（`FlyPresets` vs `PlaneFlyPresets`）

词典 JSON 的键用的是**条目名**那一套（`PlaneFlyPresets`），而外部库（`BA_Mod.zip`）
那一套把这张表叫 `FlyPresets`。归一交给 `core/asset/tables.py::resolve_table_name`
（那里已经有别名表，别再写一份），本模块只负责在查词典前先归一。

## 「非默认行数」的口径

与 `tools/gen_table_columns.py:112-116`（`docs/数据-表与列清单.md` 的生成器）**逐字相同**：

- 取值种类 = `len(Counter(values))`
- 非默认行数 = 行数 − 出现最多的那个取值的行数（0 = 全表同值，多半是占位）
- 样例 = 出现最多的三个取值，按 `str(value)` 渲染（所以空值显示成 `None` ——
  那份文档里就是 `None` 这个词，改了就对不上）

口径只有那一处定义，这里照抄那三行并在 `column_stats` 上写明出处。

## 搜索框怎么过滤（一个框管两栏，两栏的判据不一样）

一个输入框要同时管「表」和「列」，而两栏的字段不同 —— 如果各按各自的字段硬过滤，
会出现两种废掉的结果：

- 输入「单位本体」（表名命中）→ 表只剩 1 张，但**列全军覆没**（没有哪一列的中文名含这四个字）；
- 输入「基础成本」（列名命中）→ **表清单空掉**，右栏跟着没了。

所以判据写成「**表名命中 或 它有列名命中**」（`filter_tables`），
而列清单是「**命中就只留命中的；一个都没命中（说明命中的是表名）就整表都显示**」
（`visible_columns`）。两种输入都能得到用户想要的那一屏。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ba_mod_maker.core.asset import tables as table_naming
from ba_mod_maker.core.data.columns import ColumnKind, infer_column_specs
from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.edit.dictionary import (
    CONTROL_BOOL,
    CONTROL_ENUM,
    CONTROL_FOREIGN,
    CONTROL_MASK,
    CONTROL_RESOURCE,
    decode,
    field_enums,
    field_info,
    field_labels,
)

#: 词典里查不到这一列时，界面上写的那句话（**不假装有中文名**）。
MISSING_NOTE = "词典未收录"

#: 词典 kind -> 中文。`text` 故意留空：那只是"没有更具体的说法"，不是一种类型。
_INFO_KIND_CN: dict[str, str] = {
    "enum": "枚举",
    "mask": "位掩码",
    "bool": "是 / 否",
    "other": "整数（非枚举）",
    "text": "",
}

#: 取值类型（`ColumnKind`）-> 中文。
_KIND_CN: dict[str, str] = {
    ColumnKind.INT.value: "整数",
    ColumnKind.FLOAT.value: "小数",
    ColumnKind.BOOL.value: "是 / 否",
    ColumnKind.TEXT.value: "文本",
    ColumnKind.UNKNOWN.value: "未知",
}

#: 控件 -> 中文。`text`（兜底）不参与展示，见 `ColumnEntry.type_text`。
_CONTROL_CN: dict[str, str] = {
    CONTROL_BOOL: "开关",
    CONTROL_ENUM: "下拉",
    CONTROL_MASK: "逐位勾选",
    CONTROL_FOREIGN: "外键搜索",
    # 现在也覆盖**本地化键**那一类（UIName / UIDescription）—— 两者都是"必须一字不差
    # 打出来的长键"，界面给的都是可搜下拉（出处：core/edit/dictionary.py::_RESOURCE_COLUMNS）。
    CONTROL_RESOURCE: "资源键 / 本地化键",
}

#: 样例最多显示几个（与生成器的 `most_common(3)` 同一个数）。
SAMPLE_LIMIT = 3


def _identity(name: str) -> str:
    """表名的兜底展示：没有中文名就照实显示表名（不编）。"""
    return name


def _no_emoji(_name: str) -> str:
    """emoji 的兜底：没有就不画（不拿一个假的顶上去）。"""
    return ""


@dataclass(frozen=True, slots=True)
class TableEntry:
    """左栏一行：一张表。"""

    name: str
    #: 词典 JSON 里的键（`FlyPresets` -> `PlaneFlyPresets`）。
    dict_key: str
    title: str
    emoji: str

    @property
    def label(self) -> str:
        """列表里显示的那一行（emoji 没有就只剩中文名）。"""
        return f"{self.emoji} {self.title}".strip()


@dataclass(frozen=True, slots=True)
class ColumnStats:
    """一列的取值观测。字段含义见模块 docstring 的口径那一节。"""

    rows: int
    distinct: int
    non_default: int
    samples: tuple[str, ...]

    @property
    def summary(self) -> str:
        return (
            f"{self.rows} 行 · 取值种类 {self.distinct} · 非默认 {self.non_default}"
            f" · 样例 {' / '.join(self.samples) if self.samples else '—'}"
        )


@dataclass(frozen=True, slots=True)
class ColumnEntry:
    """右上一行：一列。"""

    table: str
    dict_key: str
    column: str
    cn: str
    doc: str
    #: 词典给的说法（enum / mask / bool / other / text）。
    kind: str
    #: 该给什么控件（core/edit/dictionary.py 的 CONTROL_*）。
    control: str
    #: 取值对照（值 -> 含义），掩码按数值升序。
    values: tuple[tuple[str, str], ...]
    evidence: str
    foreign: str | None
    #: 两份词典都没有这一列。
    missing: bool
    #: 从**数据**推出来的取值类型（没有实例就是 None）。
    observed_kind: str | None = None
    nullable: bool | None = None
    stats: ColumnStats | None = None

    @property
    def title(self) -> str:
        """中文名；没收录就写那句话 + 英文列名。"""
        return self.cn if not self.missing else f"{self.column}（{MISSING_NOTE}）"

    @property
    def type_text(self) -> str:
        """类型那一格：取值类型 · 词典说法 · 控件 · 外键指向。"""
        parts: list[str] = []
        if self.observed_kind:
            parts.append(_KIND_CN.get(self.observed_kind, self.observed_kind))
        spoken = _INFO_KIND_CN.get(self.kind, "")
        if spoken:
            parts.append(spoken)
        widget = _CONTROL_CN.get(self.control, "")
        if widget:
            parts.append(widget)
        if self.nullable:
            parts.append("可空")
        text = " · ".join(parts) if parts else "—"
        if self.foreign:
            text = f"{text} → {self.foreign}"
        return text

    def decode_text(self, raw: str) -> str:
        """把一个原始值解成中文（枚举 / 掩码 / bool 都吃字符串，见 decode）。"""
        return decode(field_info(self.dict_key, self.column), raw).text


def dictionary_table_key(name: str) -> str:
    """表名 -> 词典 JSON 里的键（别名归一只走 `core/asset/tables.py` 那一份）。"""
    resolved = table_naming.resolve_table_name(name, field_labels().keys())
    if resolved is not None:
        return resolved
    resolved = table_naming.resolve_table_name(name, table_naming.TABLE_NAMES)
    return resolved if resolved is not None else name


def table_names(instance: Instance | None = None) -> tuple[str, ...]:
    """要做成清单的表名：有实例就用实例的，没有就用产品自己那 24 张。"""
    if instance is not None:
        return tuple(instance.table_names)
    return table_naming.TABLE_NAMES


def table_entries(
    instance: Instance | None = None,
    *,
    title_of: Callable[[str], str] = _identity,
    emoji_of: Callable[[str], str] = _no_emoji,
) -> tuple[TableEntry, ...]:
    """左栏的全部表。

    `title_of` / `emoji_of` 由界面注入（产品里就是 entries_panel 那两个函数）——
    本模块不持有"表叫什么"这份知识，避免出现第二份。
    """
    return tuple(
        TableEntry(
            name=name,
            dict_key=dictionary_table_key(name),
            title=title_of(name) or name,
            emoji=emoji_of(name),
        )
        for name in table_names(instance)
    )


@lru_cache(maxsize=64)
def dictionary_columns(dict_key: str) -> tuple[str, ...]:
    """只靠词典文件列出一张表的列（没有数据源也能用）。

    顺序：**先 field_labels 的列**（JSON 的键序就是原始列序），
    再补 field_enums 里独有的那几列（README：391 列 + 剩 16 列 = 407）。
    """
    labels = field_labels().get(dict_key)
    out: list[str] = list(labels) if isinstance(labels, dict) else []
    prefix = f"{dict_key}."
    for full in field_enums():
        if not full.startswith(prefix):
            continue
        column = full[len(prefix) :]
        if column not in out:
            out.append(column)
    return tuple(out)


def _instance_table(instance: Instance | None, name: str) -> Table | None:
    """实例里的这张表（惰加载）；没有就 None。"""
    if instance is None:
        return None
    resolved = table_naming.resolve_table_name(name, instance.table_names)
    if resolved is None:
        return None
    return instance.table(resolved)


def table_size(instance: Instance | None, name: str) -> tuple[int, int] | None:
    """(行数, 列数)；没有实例、或实例里没这张表 -> None。"""
    table = _instance_table(instance, name)
    if table is None:
        return None
    return table.row_count, table.column_count


def column_stats(values: Sequence[Any]) -> ColumnStats:
    """一列的取值观测。口径与 `tools/gen_table_columns.py:112-116` 逐字相同（见模块 docstring）。"""
    if not values:
        return ColumnStats(rows=0, distinct=0, non_default=0, samples=())
    counter = Counter(values)
    top_count = counter.most_common(1)[0][1]
    samples = tuple(str(value) for value, _ in counter.most_common(SAMPLE_LIMIT))
    return ColumnStats(
        rows=len(values),
        distinct=len(counter),
        non_default=len(values) - top_count,
        samples=samples,
    )


def _sorted_values(values: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """取值对照的显示顺序：全是数字（掩码 / 枚举）就按数值升序，否则保持词典顺序。"""
    keys = list(values)
    if keys and all(key.lstrip("-").isdigit() for key in keys):
        keys.sort(key=int)
    return tuple((key, values[key]) for key in keys)


def column_entries(table: str, instance: Instance | None = None) -> tuple[ColumnEntry, ...]:
    """一张表的全部列（有实例就用实例的真实列序 + 取值观测）。"""
    real = _instance_table(instance, table)
    key = dictionary_table_key(real.name if real is not None else table)
    columns = tuple(real.columns) if real is not None else dictionary_columns(key)

    specs = infer_column_specs(real.columns, real.rows) if real is not None else ()
    out: list[ColumnEntry] = []
    for index, column in enumerate(columns):
        info = field_info(key, column)
        observed_kind: str | None = None
        nullable: bool | None = None
        stats: ColumnStats | None = None
        if real is not None:
            spec = specs[index]
            observed_kind = spec.kind.value
            nullable = spec.nullable
            stats = column_stats([row[index] for row in real.rows])
        out.append(
            ColumnEntry(
                table=real.name if real is not None else table,
                dict_key=key,
                column=column,
                cn=info.cn,
                doc=info.doc,
                kind=info.kind,
                control=info.control,
                values=_sorted_values(dict(info.values)),
                evidence=info.evidence,
                foreign=info.foreign,
                missing=info.missing,
                observed_kind=observed_kind,
                nullable=nullable,
                stats=stats,
            )
        )
    return tuple(out)


def matches(query: str, *fields: str) -> bool:
    """即时过滤的判据：**大小写无关的子串**；空查询=全都要。"""
    needle = query.strip().casefold()
    if not needle:
        return True
    return any(needle in field.casefold() for field in fields if field)


@lru_cache(maxsize=64)
def _column_search_index(dict_key: str) -> tuple[tuple[str, str], ...]:
    """(英文列名, 中文名) 一份，给「搜列名也找得到表」用。

    为什么要缓存：`filter_tables` 每敲一个字都要问 24 张表（共 407 列），
    每列过一遍 `field_info`；缓存之后这一步是纯查表。
    """
    return tuple(
        (column, field_info(dict_key, column).cn) for column in dictionary_columns(dict_key)
    )


def filter_tables(entries: Sequence[TableEntry], query: str) -> tuple[TableEntry, ...]:
    """表清单的过滤：中文表名 / 英文表名 / 词典键，**或者它有一列的列名命中**。

    后半截是「搜列名也找得到表」的唯一办法（见模块 docstring 的搜索那一节）——
    判据用的是**词典侧**的列（产品知识，内存里就有），不去碰实例：
    有实例时逐表问列名会把 24 张表全读进内存，那是每敲一个字 200ms 的代价。
    """
    if not query.strip():
        return tuple(entries)
    out: list[TableEntry] = []
    for entry in entries:
        if matches(query, entry.title, entry.name, entry.dict_key):
            out.append(entry)
            continue
        if any(matches(query, column, cn) for column, cn in _column_search_index(entry.dict_key)):
            out.append(entry)
    return tuple(out)


def filter_columns(entries: Sequence[ColumnEntry], query: str) -> tuple[ColumnEntry, ...]:
    """列清单的过滤：中文名 / 英文列名。"""
    return tuple(e for e in entries if matches(query, e.cn, e.column))


def visible_columns(entries: Sequence[ColumnEntry], query: str) -> tuple[ColumnEntry, ...]:
    """列清单在搜索框下**该显示哪些**：命中列名就只留命中的，否则整表都显示。

    「否则」那一支对应的是「这一屏是搜表名进来的」—— 那时一个列名都不匹配，
    整表显示才是用户要的（空列表会让他以为这张表没有列）。
    """
    hit = filter_columns(entries, query)
    return hit if hit else tuple(entries)


def first_table_name(entries: Sequence[TableEntry], current: str | None) -> str | None:
    """过滤之后该选哪张表：当前这张还在就留着，否则退到第一张（没有就 None）。"""
    if current is not None and any(e.name == current for e in entries):
        return current
    return entries[0].name if entries else None


__all__ = [
    "MISSING_NOTE",
    "SAMPLE_LIMIT",
    "ColumnEntry",
    "ColumnStats",
    "TableEntry",
    "column_entries",
    "column_stats",
    "dictionary_columns",
    "dictionary_table_key",
    "filter_columns",
    "filter_tables",
    "first_table_name",
    "matches",
    "table_entries",
    "table_names",
    "table_size",
    "visible_columns",
]
