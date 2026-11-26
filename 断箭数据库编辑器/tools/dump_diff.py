r"""离线读到的库 ↔ 探针从游戏内存 dump 出来的库：逐表逐字段对比（零 Qt）。

## 判据一行

    .venv\Scripts\python.exe tools\dump_diff.py <离线目录> <dump.json>
    -> 结论行 「表 N / 只离线有 X 行 / 只在线有 Y 行 / 值不同 Z 格」：
       两侧同一份数据 -> 0 / 0 / 0；
       改一格值 或 翻一个 bool -> 「值不同」+1；
       删一行 -> 「只离线有」+1；加一行 -> 「只在线有」+1。
       —— 四个动作**各自**只动一个读数（tests/test_dump_diff.py 逐条断言，多一格即红）。

## 它回答的四件事（就是四节输出）

1. **表级**：每张表 离线行数 / 在线行数 / 只有离线有的 Id / 只有在线有的 Id；
2. **字段级**：两边都有、值不同的 (表, Id, 列) 三元组，逐条带两边的值；
3. **列级**：只在某一侧存在的列（列集合 = 该表所有行键的并集）；
4. **结论行**：「表 N / 只离线有 X 行 / 只在线有 Y 行 / 值不同 Z 格」。

## 数据格式契约（dump.json，与探针落盘的是同一份）

    {
      "meta": {
        "generated_utc": "2026-10-10T00:00:00Z",
        "mod_version": "0.1.0",
        "game_source": "compiled",
        "table_count": 24,
        "row_counts": {"Units": 540},
        "failed_tables": ["Specializations"]
      },
      "tables": {"Units": [{"Id": 1, "Name": "..."}]}
    }

- 表名 / 列名**逐字**等于 DataBaseCompiled/<pid>/*.json 的键名（= 游戏 C# 行的属性名）；
- 值原样：数字就是数字、bool 就是 true/false；**不换算、不取整、不丢字段**；
- 每张表按 Id 升序；读不到的表要出现在 meta.failed_tables 里。

## 口径（写死在这里，免得两个人各理解一套）

| 情形 | 怎么算 |
|---|---|
| 两侧都有这张表 | 正常对比：Id 交集比字段，Id 差集进表级 |
| dump 里没有它，但列在 failed_tables | 记「dump 读失败」；不算成「只有离线有」 |
| dump 没有它、也没列进 failed_tables | 记「dump 缺表」，同样**不算**成整表离线独有 |
| dump 有、离线没有 | 记「仅 dump 有」，不参与「表 N」 |
| 列只在一侧存在 | **只在列级报一次**，不按行重复报成字段级 |
| 某一行单独缺一个共有列 | 按字段级报，缺的那侧显示 <缺列> |
| 同一个 Id 出现多次 | 取**第一次**出现的那行参与比较，另记「重复 Id」 |
| dump 表名写成别名（FlyPresets）| 按 tables.py 别名对齐，另记一条异常（契约要求文件干名） |

### 现算列（`probe.computed_columns`）：排除，但**要说出来**

探针在顶层写 `probe.computed_columns`（形如 `Units.OriginalCost`）：这批列在游戏里
**没有 backing field**、是现算的，派生值天然可能差 1 ulp（实测
`PlaneFlyPresets.CornerSpeedYaw`：离线 41.81818 / 在线 41.818184，按 `struct.pack("<f")`
验过位真的不同 —— 那是现算的舍入，不是数据差异）。

口径：这批列**不进**「值不同」的计数与列表（结论行的 Z 是排除后的数），但**必须**单列
一节 `[现算列] 已按口径排除 N 格（列：…）` 把排除掉的东西摆出来 —— 忽略要说出来。
`probe` / `computed_columns` 缺失或空数组 -> 照常**全量**比对（老 dump 照样能跑）。

### 打印折叠（防刷屏）

同一列的「值不同」超过 20 格时，折成一行 `列 <表>.<列>：N 格（前 5 例）` 再跟 5 条例子
—— 实测 `Turrets.ParentTurretId` 一列能刷 1000+ 行，会把别的列整个埋掉。
计数一个字都不改；`--limit` 数的是**格子**，折叠表头不算格子。

### 一侧必须是离线目录（⛔ 两份 dump 不能互比）

本工具**固定**一侧是离线数据源（要有 `_schema.json` + `<pid>/_manifest.json`），
另一侧才是探针 dump。两份 dump 互比没有列清单、也没有「我们自己读到什么」那一侧 ——
要拿别的库来比，先把它落成 DataBaseCompiled 那样的目录。

值比较是**类型敏感**的：true 与 1、1 与 1.0 都算「值不同」——
dump 的意义是"游戏里到底是什么"，类型变了就是要报。

退出码：0 无差异 / 1 有差异（含结构异常）/ 2 用法或读取错误。

## 用法

    .venv\Scripts\python.exe tools\dump_diff.py D:\断箭模组制作\DataBaseCompiled\52085 dump.json
    .venv\Scripts\python.exe tools\dump_diff.py <DataBaseCompiled> --instance 52085 dump.json
    ... [--table Units] [--table Weapons] [--limit 20] [--json out.json]

--limit 只截断**打印**，计数一个字都不改（结论行永远是全量）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
#: 直接跑脚本时 src 不在 sys.path 上（没装成包也能用）—— 与 tools/ 下其他薄封装同款。
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ba_mod_maker.core.asset import tables as table_naming  # noqa: E402
from ba_mod_maker.core.data.loader import read_table  # noqa: E402
from ba_mod_maker.core.data.schema import TableSchema, load_schema  # noqa: E402
from ba_mod_maker.core.errors import UserFacingError  # noqa: E402

#: 行的键列（契约：每张表的每一行都有它）。
ID_COLUMN = "Id"
#: 某一侧缺这个格子时，报告里显示的那个记号（两侧都缺 = 相等 = 不报）。
MISSING_CELL = "<缺列>"
SCHEMA_FILENAME = "_schema.json"
MANIFEST_FILENAME = "_manifest.json"

#: 每张表的对比状态（报告里逐字出现）。
STATUS_COMPARED = "对比"
STATUS_DUMP_FAILED = "dump 读失败"
STATUS_DUMP_MISSING = "dump 缺表"
STATUS_DUMP_ONLY = "仅 dump 有"

#: 探针写在**顶层**的那个键（现算列声明在 `probe.computed_columns` 里）。
PROBE_KEY = "probe"
COMPUTED_KEY = "computed_columns"

#: 同一列的值不同超过这么多格 -> 打印时折成「列 X：N 格（前 5 例）」。
FOLD_THRESHOLD = 20
#: 折叠时给几条例。
FOLD_EXAMPLES = 5


class DumpFormatError(ValueError):
    """dump.json 不符合契约（顶层结构 / 行不是对象 / 表不是数组）。"""


@dataclass(frozen=True, slots=True)
class OfflineSource:
    """离线数据源：根（有 _schema.json）+ 实例目录（有 24 个表文件）。"""

    root: Path
    instance_dir: Path
    schema: dict[str, TableSchema]

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(self.schema)


@dataclass(frozen=True, slots=True)
class DumpFile:
    """探针落盘的那份 JSON（读进来 + 校验过形状）。"""

    path: Path
    meta: dict[str, Any]
    tables: dict[str, list[dict[str, Any]]]
    failed_tables: tuple[str, ...]
    notes: tuple[str, ...] = ()
    #: `probe.computed_columns` 原样（形如 `Units.OriginalCost`）；缺失/空 = 没声明。
    computed_columns: tuple[str, ...] = ()
    #: 上面那批解析成 (表, 列)。表名可能是外号，对齐到离线表名在 `compare` 里做。
    computed_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ValueDiff:
    """一个格子的差异：(表, Id, 列) + 两边的值。"""

    table: str
    row_id: Any
    column: str
    offline: Any
    online: Any

    def label(self) -> str:
        return f"{self.table}[Id={_show(self.row_id)}].{self.column}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "id": self.row_id,
            "column": self.column,
            "offline": self.offline,
            "online": self.online,
        }


@dataclass(slots=True)
class TableDiff:
    """一张表的全部读数。"""

    name: str
    status: str
    offline_rows: int = 0
    online_rows: int = 0
    only_offline_ids: list[Any] = field(default_factory=list)
    only_online_ids: list[Any] = field(default_factory=list)
    value_diffs: list[ValueDiff] = field(default_factory=list)
    #: 按「现算列」口径**排除**掉的格子：不进 value_diffs，但报告里单列一节摆出来。
    excluded_diffs: list[ValueDiff] = field(default_factory=list)
    only_offline_columns: list[str] = field(default_factory=list)
    only_online_columns: list[str] = field(default_factory=list)
    duplicate_offline_ids: list[Any] = field(default_factory=list)
    duplicate_online_ids: list[Any] = field(default_factory=list)
    rows_without_id_offline: list[int] = field(default_factory=list)
    rows_without_id_online: list[int] = field(default_factory=list)
    online_ids_not_sorted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "offline_rows": self.offline_rows,
            "online_rows": self.online_rows,
            "only_offline_ids": self.only_offline_ids,
            "only_online_ids": self.only_online_ids,
            "only_offline_columns": self.only_offline_columns,
            "only_online_columns": self.only_online_columns,
            "value_diffs": [d.as_dict() for d in self.value_diffs],
            "excluded_diffs": [d.as_dict() for d in self.excluded_diffs],
            "duplicate_offline_ids": self.duplicate_offline_ids,
            "duplicate_online_ids": self.duplicate_online_ids,
            "rows_without_id_offline": self.rows_without_id_offline,
            "rows_without_id_online": self.rows_without_id_online,
            "online_ids_not_sorted": self.online_ids_not_sorted,
        }


@dataclass(slots=True)
class DiffReport:
    """一次对比的全部结论。"""

    offline_dir: Path | None
    dump_path: Path
    dump_meta: dict[str, Any]
    failed_tables: tuple[str, ...]
    tables: list[TableDiff]
    notes: tuple[str, ...] = ()
    #: dump 声明的现算列（`probe.computed_columns` 原样）；空 = 没声明 -> 全量比对。
    computed_columns: tuple[str, ...] = ()

    @property
    def compared_tables(self) -> list[TableDiff]:
        return [t for t in self.tables if t.status == STATUS_COMPARED]

    @property
    def table_count(self) -> int:
        """真正比过的表数（读失败 / 缺表 / 仅 dump 有都不算）。"""
        return len(self.compared_tables)

    @property
    def value_diffs(self) -> list[ValueDiff]:
        return [d for t in self.compared_tables for d in t.value_diffs]

    @property
    def excluded_diffs(self) -> list[ValueDiff]:
        """按「现算列」口径排除掉的格子（不计入 value_diffs，但**要摆出来**）。"""
        return [d for t in self.compared_tables for d in t.excluded_diffs]

    def excluded_counts(self) -> list[tuple[str, int]]:
        """排除掉的格子按「表.列」计数，保持第一次出现的顺序。"""
        counts: dict[str, int] = {}
        for d in self.excluded_diffs:
            key = f"{d.table}.{d.column}"
            counts[key] = counts.get(key, 0) + 1
        return list(counts.items())

    @property
    def only_offline_count(self) -> int:
        return sum(len(t.only_offline_ids) for t in self.compared_tables)

    @property
    def only_online_count(self) -> int:
        return sum(len(t.only_online_ids) for t in self.compared_tables)

    @property
    def only_offline_column_count(self) -> int:
        return sum(len(t.only_offline_columns) for t in self.compared_tables)

    @property
    def only_online_column_count(self) -> int:
        return sum(len(t.only_online_columns) for t in self.compared_tables)

    def anomalies(self) -> list[str]:
        """结构异常：不是「值不同」，但会让上面那些读数**不完整**。"""
        out = list(self.notes)
        for t in self.tables:
            if t.status == STATUS_DUMP_FAILED:
                out.append(
                    f"表 {t.name}：dump 读失败（在 meta.failed_tables 里）—— 离线 "
                    f"{t.offline_rows} 行没比对；**不**算成「只有离线有」"
                )
                continue
            if t.status == STATUS_DUMP_MISSING:
                out.append(
                    f"表 {t.name}：dump 里没有，也没列进 meta.failed_tables —— 离线 "
                    f"{t.offline_rows} 行没比对（探针要把它写进 failed_tables）"
                )
                continue
            if t.status == STATUS_DUMP_ONLY:
                out.append(
                    f"表 {t.name}：只有 dump 有（离线数据源里没这张表），"
                    f"{t.online_rows} 行不参与「表 N」"
                )
                continue
            if t.duplicate_offline_ids:
                out.append(f"表 {t.name}：离线有重复 Id {_show(t.duplicate_offline_ids[:8])}"
                           "（取第一次出现的那行参与比较）")
            if t.duplicate_online_ids:
                out.append(f"表 {t.name}：dump 有重复 Id {_show(t.duplicate_online_ids[:8])}"
                           "（取第一次出现的那行参与比较）")
            if t.rows_without_id_offline:
                out.append(f"表 {t.name}：离线第 {_show(t.rows_without_id_offline[:8])} 行没有 Id"
                           "（这些行没参与比较）")
            if t.rows_without_id_online:
                out.append(f"表 {t.name}：dump 第 {_show(t.rows_without_id_online[:8])} 行没有 Id"
                           "（这些行没参与比较）")
            if t.online_ids_not_sorted:
                out.append(f"表 {t.name}：dump 这侧没按 Id 升序（契约规则 4）")
            shared = {str(v) for v in t.only_offline_ids} & {str(v) for v in t.only_online_ids}
            if shared:
                out.append(
                    f"表 {t.name}：Id {_show(sorted(shared)[:8])} 只出现在一侧，"
                    "但两侧字面相同 —— 可能是 Id 类型不同（契约要求原样）"
                )
        return out

    def has_differences(self) -> bool:
        return bool(
            self.value_diffs
            or self.only_offline_count
            or self.only_online_count
            or self.only_offline_column_count
            or self.only_online_column_count
            or self.anomalies()
        )

    def conclusion_line(self) -> str:
        return (
            f"表 {self.table_count} / 只离线有 {self.only_offline_count} 行"
            f" / 只在线有 {self.only_online_count} 行 / 值不同 {len(self.value_diffs)} 格"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "offline_dir": str(self.offline_dir) if self.offline_dir else None,
            "dump": str(self.dump_path),
            "dump_meta": self.dump_meta,
            "failed_tables": list(self.failed_tables),
            "notes": list(self.notes),
            "computed_columns": list(self.computed_columns),
            "tables": [t.as_dict() for t in self.tables],
            "excluded_diffs": [d.as_dict() for d in self.excluded_diffs],
            "totals": {
                "tables": self.table_count,
                "only_offline_rows": self.only_offline_count,
                "only_online_rows": self.only_online_count,
                "value_diffs": len(self.value_diffs),
                "only_offline_columns": self.only_offline_column_count,
                "only_online_columns": self.only_online_column_count,
                "excluded_value_diffs": len(self.excluded_diffs),
                "computed_columns": len(self.computed_columns),
                "anomalies": len(self.anomalies()),
            },
            "conclusion": self.conclusion_line(),
        }


# --------------------------------------------------------------------------- #
# 读入
# --------------------------------------------------------------------------- #


def resolve_offline(path: Path | str, *, instance: str | None = None) -> OfflineSource:
    """把用户给的那个路径认成「数据源根 + 实例目录」。

    两种给法都收：`DataBaseCompiled`（根，实例目录是它的子目录）与
    `DataBaseCompiled/52085`（实例目录，根在上一层）。
    """
    given = Path(path)
    if not given.exists():
        raise FileNotFoundError(f"离线目录不存在：{given}")

    if (given / SCHEMA_FILENAME).is_file():
        candidates = sorted(
            d.name
            for d in given.iterdir()
            if d.is_dir() and (d / MANIFEST_FILENAME).is_file()
        )
        if instance is not None:
            chosen = instance
            if chosen not in candidates:
                raise FileNotFoundError(
                    f"实例 {chosen} 不在 {given}（候选：{', '.join(candidates) or '无'}）"
                )
        elif len(candidates) == 1:
            chosen = candidates[0]
        else:
            raise ValueError(
                f"{given} 下有 {len(candidates)} 个实例（{', '.join(candidates)}）"
                "—— 给实例目录，或用 --instance 指定"
            )
        return OfflineSource(given, given / chosen, load_schema(given))

    parent = given.parent
    if (parent / SCHEMA_FILENAME).is_file():
        return OfflineSource(parent, given, load_schema(parent))

    raise FileNotFoundError(
        f"{given} 里没有 {SCHEMA_FILENAME}，它的上一层也没有 —— 给 DataBaseCompiled 根或 "
        "DataBaseCompiled/<pid> 实例目录（⛔ 两份 dump 不能互比：本工具一侧必须是离线数据源）"
    )


def read_offline_tables(source: OfflineSource) -> dict[str, list[dict[str, Any]]]:
    """按 schema 逐张读离线表（走的正是产品那条 loader.read_table）。

    返回「表名 -> 行 dict 列表」，行 dict 的键序 = JSON 的键序 = 游戏声明序。
    ⚠️ 交出来的是**产品读到的东西**：`read_table` 对缺列的行补 None
    （见 loader._build_list_shaped），所以这里量的就是"我们离线读到了什么"。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for name in source.schema:
        table = read_table(source.instance_dir, source.schema, name)
        out[name] = [
            dict(zip(table.columns, row, strict=True))
            for row in table.rows
        ]
    return out


def read_dump(path: Path | str) -> DumpFile:
    """读探针落盘的 dump.json 并校验形状（形状不对就抛，不猜）。"""
    where = Path(path)
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"找不到 dump 文件：{where}") from None
    except json.JSONDecodeError as exc:
        raise DumpFormatError(f"{where} 不是合法 JSON：第 {exc.lineno} 行 {exc.msg}") from None

    if not isinstance(raw, dict):
        raise DumpFormatError(f"{where} 顶层应是对象，实际是 {type(raw).__name__}")

    tables_raw = raw.get("tables")
    if not isinstance(tables_raw, dict):
        raise DumpFormatError(f'{where} 的 tables 不是对象（契约：{{"表名": [行, ...]}}）')

    meta_raw = raw.get("meta")
    meta: dict[str, Any] = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    failed_raw = meta.get("failed_tables")
    failed = tuple(str(x) for x in failed_raw) if isinstance(failed_raw, list) else ()

    notes: list[str] = [] if isinstance(meta_raw, dict) else [f"{where} 没有 meta 对象"]
    computed, computed_pairs = _read_computed_columns(raw.get(PROBE_KEY), where, notes)
    tables: dict[str, list[dict[str, Any]]] = {}
    for name, value in tables_raw.items():
        if isinstance(value, dict):
            rows: list[Any] = list(value.values())
            notes.append(
                f"表 {name}：dump 里是对象而不是数组 —— 按 {{key: 行}} 解出 {len(rows)} 行，"
                "契约要求数组"
            )
        elif isinstance(value, list):
            rows = list(value)
        else:
            raise DumpFormatError(f"表 {name} 的值应是数组，实际是 {type(value).__name__}")
        bad = [i for i, row in enumerate(rows) if not isinstance(row, dict)]
        if bad:
            raise DumpFormatError(f"表 {name} 的第 {bad[:3]} 行不是对象（从 0 数）")
        tables[str(name)] = [dict(row) for row in rows]

    return DumpFile(where, meta, tables, failed, tuple(notes), computed, computed_pairs)


# --------------------------------------------------------------------------- #
# 对比
# --------------------------------------------------------------------------- #


def _read_computed_columns(
    probe_raw: Any, where: Path, notes: list[str]
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """读 `probe.computed_columns`（缺失 / 空数组 -> 没声明，照常全量比对）。

    形状不对的条目**先记一条异常再跳过** —— 少排除一列是看得见的，不许静默。
    """
    if probe_raw is None:
        return (), ()
    if not isinstance(probe_raw, dict):
        notes.append(f"{where} 的 probe 不是对象（{type(probe_raw).__name__}）—— 当没声明现算列")
        return (), ()
    raw_columns = probe_raw.get(COMPUTED_KEY)
    if raw_columns is None:
        return (), ()
    if not isinstance(raw_columns, list):
        notes.append(
            f"probe.{COMPUTED_KEY} 不是数组（{type(raw_columns).__name__}）—— 当没声明现算列"
        )
        return (), ()
    declared: list[str] = []
    pairs: list[tuple[str, str]] = []
    for entry in raw_columns:
        parts = entry.split(".") if isinstance(entry, str) else []
        if len(parts) != 2 or not all(parts):
            notes.append(f"probe.{COMPUTED_KEY} 里的 {entry!r} 不是「表.列」—— 已跳过")
            continue
        declared.append(entry)
        pairs.append((parts[0], parts[1]))
    return tuple(declared), tuple(pairs)


def _value_equal(a: Any, b: Any) -> bool:
    """类型敏感的值相等（true 与 1、1 与 1.0 都算不同）。"""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_value_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_value_equal(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _id_key(value: Any) -> tuple[str, Any]:
    """Id 的字典键：带上类型名，免得 True 和 1 撞成一个键。"""
    return (type(value).__name__, value)


def _sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (0, value)
    return (2, str(value))


def _show(value: Any) -> str:
    """报告里怎么显示一个值：JSON 原样（字符串带引号、bool 是 true/false）。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


@dataclass(slots=True)
class _Side:
    """一侧（离线或 dump）按 Id 索引后的样子。"""

    index: dict[tuple[str, Any], dict[str, Any]] = field(default_factory=dict)
    ids: list[Any] = field(default_factory=list)
    duplicates: list[Any] = field(default_factory=list)
    without_id: list[int] = field(default_factory=list)
    not_sorted: bool = False


def _index_rows(rows: Sequence[dict[str, Any]]) -> _Side:
    """按 Id 建索引。重复 Id 取第一次出现的那行；没 Id 的行只记下标。"""
    side = _Side()
    for position, row in enumerate(rows):
        if ID_COLUMN not in row or row[ID_COLUMN] is None:
            side.without_id.append(position)
            continue
        value = row[ID_COLUMN]
        try:
            key = _id_key(value)
            if key in side.index:
                side.duplicates.append(value)
                continue
            side.index[key] = row
        except TypeError:  # Id 不可哈希（list / dict）—— 当没 Id
            side.without_id.append(position)
            continue
        side.ids.append(value)
    side.not_sorted = any(
        _sort_key(b) < _sort_key(a)
        for a, b in zip(side.ids, side.ids[1:], strict=False)
    )
    return side


def _ordered_columns(rows: Sequence[dict[str, Any]]) -> list[str]:
    """该表所有行键的并集，按第一次出现的顺序（= 游戏声明序）。"""
    seen: dict[str, None] = {}
    for row in rows:
        for column in row:
            seen.setdefault(column, None)
    return list(seen)


def _compare_table(
    name: str,
    offline_rows: Sequence[dict[str, Any]],
    online_rows: Sequence[dict[str, Any]],
    computed_columns: Collection[str] = (),
) -> TableDiff:
    """一张表的：行数 / Id 差集 / 字段级差异 / 单侧列。

    `computed_columns` 里的列是游戏**现算**的：值不同记进 `excluded_diffs`
    （报告里单列一节摆出来），**不进** `value_diffs`。
    """
    item = TableDiff(name=name, status=STATUS_COMPARED)
    item.offline_rows = len(offline_rows)
    item.online_rows = len(online_rows)

    left = _index_rows(offline_rows)
    right = _index_rows(online_rows)
    item.duplicate_offline_ids = left.duplicates
    item.duplicate_online_ids = right.duplicates
    item.rows_without_id_offline = left.without_id
    item.rows_without_id_online = right.without_id
    item.online_ids_not_sorted = right.not_sorted

    offline_columns = _ordered_columns(offline_rows)
    online_columns = set(_ordered_columns(online_rows))
    item.only_offline_columns = sorted(set(offline_columns) - online_columns)
    item.only_online_columns = sorted(online_columns - set(offline_columns))
    common_columns = [c for c in offline_columns if c in online_columns]

    for key, left_row in left.index.items():
        right_row = right.index.get(key)
        if right_row is None:
            item.only_offline_ids.append(left_row[ID_COLUMN])
            continue
        for column in common_columns:
            ov = left_row.get(column, MISSING_CELL)
            nv = right_row.get(column, MISSING_CELL)
            if _value_equal(ov, nv):
                continue
            diff = ValueDiff(name, left_row[ID_COLUMN], column, ov, nv)
            if column in computed_columns:
                item.excluded_diffs.append(diff)
            else:
                item.value_diffs.append(diff)
    for key, right_row in right.index.items():
        if key not in left.index:
            item.only_online_ids.append(right_row[ID_COLUMN])

    item.only_offline_ids.sort(key=_sort_key)
    item.only_online_ids.sort(key=_sort_key)
    return item


def _meta_contract_notes(dump: DumpFile) -> list[str]:
    """dump 自己的 meta 与它自己的 tables 对不上 -> 记异常（这会让读数不完整）。

    三处自相矛盾：[[BT]]table_count[[BT]] / [[BT]]row_counts[[BT]] 与实际行数 /
    一张表既在 [[BT]]failed_tables[[BT]] 里又在 [[BT]]tables[[BT]] 里。
    """
    notes: list[str] = []
    declared = dump.meta.get("table_count")
    if isinstance(declared, int) and declared != len(dump.tables):
        notes.append(f"meta.table_count={declared}，但 tables 里实际有 {len(dump.tables)} 张表")
    counts = dump.meta.get("row_counts")
    if isinstance(counts, dict):
        for name, count in counts.items():
            rows = dump.tables.get(name)
            if rows is None:
                notes.append(f"meta.row_counts 里有 {name}，但 tables 里没有这张表")
            elif isinstance(count, int) and count != len(rows):
                notes.append(f"表 {name}：meta.row_counts={count}，tables 里实际 {len(rows)} 行")
    for name in dump.failed_tables:
        if name in dump.tables:
            notes.append(f"表 {name}：既在 meta.failed_tables 里，又在 tables 里（自相矛盾）")
    return notes


def _align_dump_names(
    offline_names: Iterable[str], dump_tables: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """dump 表名按 core/asset/tables.py 的别名对齐到离线表名（另记一条异常）。

    只有一对外号：`FlyPresets`（外部库那套叫法）↔ `PlaneFlyPresets`（磁盘文件干，
    契约要求探针用这个）。对齐是为了不让一次叫法不同被读成「整张表没了」。
    """
    known = set(offline_names)
    aligned: dict[str, list[dict[str, Any]]] = {}
    notes: list[str] = []
    for name, rows in dump_tables.items():
        target = name if name in known else table_naming.resolve_table_name(name, known)
        if target is not None and target != name and target not in aligned:
            notes.append(
                f"dump 的表名是 {name}，已按表名别名对齐到 {target}"
                f"（契约要求用文件干名 {target}）"
            )
            aligned[target] = rows
        else:
            aligned[name] = rows
    return aligned, notes


def _resolve_computed_columns(
    dump: DumpFile,
    offline_tables: dict[str, list[dict[str, Any]]],
    aligned: dict[str, list[dict[str, Any]]],
    all_names: set[str],
) -> tuple[dict[str, set[str]], list[str]]:
    """`probe.computed_columns` -> 「离线表名 -> 现算列集合」。

    表名走与表对齐同一套别名解析；表 / 列**两侧都不存在**就记一条异常
    （声明的列名拼错了要看得见，不许静默少排除一列）。被 `--table` 筛掉的表安静跳过。
    """
    by_table: dict[str, set[str]] = {}
    notes: list[str] = []
    for table, column in dump.computed_pairs:
        target = table if table in all_names else table_naming.resolve_table_name(table, all_names)
        if target is None:
            notes.append(
                f"probe.{COMPUTED_KEY} 声明了 {table}.{column}：离线没有表 {table} —— 已跳过"
            )
            continue
        if target not in offline_tables:
            continue
        present = column in _ordered_columns(offline_tables[target]) or column in _ordered_columns(
            aligned.get(target, [])
        )
        if not present:
            notes.append(
                f"probe.{COMPUTED_KEY} 声明了 {target}.{column}，但两侧都没有这一列 —— 已跳过"
            )
            continue
        by_table.setdefault(target, set()).add(column)
    return by_table, notes


def compare(
    offline: dict[str, list[dict[str, Any]]],
    dump: DumpFile,
    *,
    only: Iterable[str] | None = None,
    offline_dir: Path | None = None,
) -> DiffReport:
    """逐表对比。纯函数：不碰盘、不看环境（用例直接喂字典）。

    `offline_dir` 只进报告抬头（`compare` 自己不知道离线目录在哪）。
    """
    names = set(only) if only is not None else None
    offline_tables = {k: v for k, v in offline.items() if names is None or k in names}
    dump_tables = {k: v for k, v in dump.tables.items() if names is None or k in names}

    aligned, alias_notes = _align_dump_names(offline_tables, dump_tables)
    computed_by_table, computed_notes = _resolve_computed_columns(
        dump, offline_tables, aligned, set(offline)
    )
    tables: list[TableDiff] = []
    for name, rows in offline_tables.items():
        if name in aligned:
            tables.append(
                _compare_table(name, rows, aligned[name], computed_by_table.get(name, ()))
            )
        elif name in dump.failed_tables:
            tables.append(
                TableDiff(name=name, status=STATUS_DUMP_FAILED, offline_rows=len(rows))
            )
        else:
            tables.append(
                TableDiff(name=name, status=STATUS_DUMP_MISSING, offline_rows=len(rows))
            )
    for name in sorted(aligned):
        if name not in offline_tables:
            tables.append(
                TableDiff(name=name, status=STATUS_DUMP_ONLY, online_rows=len(aligned[name]))
            )

    return DiffReport(
        offline_dir=offline_dir,
        dump_path=dump.path,
        dump_meta=dump.meta,
        failed_tables=dump.failed_tables,
        tables=tables,
        notes=(*dump.notes, *_meta_contract_notes(dump), *alias_notes, *computed_notes),
        computed_columns=dump.computed_columns,
    )


# --------------------------------------------------------------------------- #
# 打印
# --------------------------------------------------------------------------- #


def _show_ids(ids: Sequence[Any], limit: int | None) -> str:
    shown = list(ids) if limit is None else list(ids[:limit])
    text = _show(shown)
    if limit is not None and len(ids) > limit:
        text += f" …（共 {len(ids)} 个）"
    return text


def _group_by_column(diffs: Sequence[ValueDiff]) -> list[tuple[str, list[ValueDiff]]]:
    """按「表.列」分组，保持第一次出现的顺序。"""
    groups: dict[str, list[ValueDiff]] = {}
    for d in diffs:
        groups.setdefault(f"{d.table}.{d.column}", []).append(d)
    return list(groups.items())


def _field_lines(diffs: Sequence[ValueDiff], limit: int | None) -> list[str]:
    """字段级那一节的行（同一列刷太多 -> 折叠，免得把别的列埋掉）。"""
    lines: list[str] = []
    cells = 0
    stopped = False
    for key, group in _group_by_column(diffs):
        if stopped:
            break
        folded = len(group) > FOLD_THRESHOLD
        shown = group[:FOLD_EXAMPLES] if folded else group
        if folded:
            lines.append(f"  列 {key}：{len(group)} 格（前 {len(shown)} 例）")
        for d in shown:
            if limit is not None and cells >= limit:
                stopped = True
                break
            lines.append(f"  {d.label()}: 离线 {_show(d.offline)} -> 在线 {_show(d.online)}")
            cells += 1
    if limit is not None and len(diffs) > cells:
        lines.append(f"  …（共 {len(diffs)} 格，这里只打印前 {cells} 格）")
    return lines


def _computed_lines(report: DiffReport, limit: int | None) -> list[str]:
    """「现算列」那一节：排除的**摆出来**（忽略要说出来，不许静默）。"""
    grouped = _group_by_column(report.excluded_diffs)
    listed = grouped if limit is None else grouped[:limit]
    names = "、".join(key for key, _ in listed) or "无"
    lines = [f"[现算列] 已按口径排除 {len(report.excluded_diffs)} 格（列：{names}）"]
    declared = report.computed_columns
    if not declared:
        lines.append(f"  dump 没声明 probe.{COMPUTED_KEY} -> 全量比对，一格都没排除")
        return lines
    shown_declared = declared if limit is None else declared[:limit]
    lines.append(f"  dump 声明 {len(declared)} 列现算：" + "、".join(shown_declared))
    if not report.excluded_diffs:
        lines.append(f"  这 {len(declared)} 列本次一格都没差")
        return lines
    for key, group in listed:
        examples = group[:FOLD_EXAMPLES]
        head = f"  {key}：{len(group)} 格"
        if len(group) > len(examples):
            head += f"（前 {len(examples)} 例）"
        lines.append(head)
        for d in examples:
            lines.append(f"    {d.label()}: 离线 {_show(d.offline)} -> 在线 {_show(d.online)}")
    if limit is not None and len(grouped) > limit:
        lines.append(f"  …（共 {len(grouped)} 列被排除过）")
    return lines


def format_report(report: DiffReport, *, limit: int | None = None) -> list[str]:
    """报告正文。

    两条只管打印、**不改计数**的规则：

    - 同一列的值不同超过 `FOLD_THRESHOLD` 格 -> 折成「列 X：N 格（前 5 例）」；
    - `limit` 数的是**格子**（折叠表头不算格子）。
    """
    lines: list[str] = []
    meta_bits = ", ".join(
        f"{k}={_show(v)}" for k, v in report.dump_meta.items() if k != "row_counts"
    )
    lines.append(f"[dump] {report.dump_path}")
    lines.append(f"       meta: {meta_bits or '（空）'}")

    lines.append("")
    lines.append("[表级] 表名 离线行 在线行 只离线有 只在线有 状态")
    for t in report.tables:
        lines.append(
            f"  {t.name:<26} {t.offline_rows:>6} {t.online_rows:>6}"
            f" {len(t.only_offline_ids):>6} {len(t.only_online_ids):>6}   {t.status}"
        )
        if t.only_offline_ids:
            lines.append(f"      只有离线有的 Id: {_show_ids(t.only_offline_ids, limit)}")
        if t.only_online_ids:
            lines.append(f"      只有在线有的 Id: {_show_ids(t.only_online_ids, limit)}")

    diffs = report.value_diffs
    lines.append("")
    lines.append(f"[字段级] 两边都有、值不同的 (表, Id, 列)：{len(diffs)} 格")
    lines.extend(_field_lines(diffs, limit))

    lines.append("")
    lines.extend(_computed_lines(report, limit))

    lines.append("")
    lines.append(
        f"[列级] 仅离线有的列 {report.only_offline_column_count} 个"
        f" / 仅在线有的列 {report.only_online_column_count} 个"
    )
    for t in report.tables:
        if t.only_offline_columns:
            lines.append(f"  {t.name}: 仅离线有 {_show(t.only_offline_columns)}")
        if t.only_online_columns:
            lines.append(f"  {t.name}: 仅在线有 {_show(t.only_online_columns)}")

    anomalies = report.anomalies()
    lines.append("")
    lines.append(f"[结构异常] {len(anomalies)} 条（不计入上面三个读数，但会让它们不完整）")
    for text in anomalies if limit is None else anomalies[:limit]:
        lines.append(f"  {text}")

    lines.append("")
    lines.append(f"[结论] {report.conclusion_line()}")
    return lines


def build_parser() -> argparse.ArgumentParser:
    """命令行。

    ⛔ 一侧**必须**是离线数据源目录（有 `_schema.json`）—— 两份 dump 不能互比。
    这句话写进 description，是因为真踩过：拿两份 dump 互喂，第一侧会以
    「没有 _schema.json」报错，而现象看起来像「工具坏了」。
    """
    parser = argparse.ArgumentParser(
        prog="dump_diff.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "离线读到的库 ↔ 探针 dump 的库：逐表逐字段对比。\n"
            "⛔ 一侧**必须**是离线数据源目录（有 _schema.json + <pid>/_manifest.json），\n"
            "   另一侧才是探针 dump.json —— **两份 dump 不能互比**（没有列清单那一侧，\n"
            "   也没有「我们自己读到什么」）。要比别的库，先把它落成 DataBaseCompiled 那样的目录。"
        ),
        epilog=(
            "例：\n"
            "  python tools\\dump_diff.py ..\\DataBaseCompiled\\52085 dump.json\n"
            "  python tools\\dump_diff.py ..\\DataBaseCompiled --instance 52085 dump.json\n"
            "  （可选 --table Units / --limit 20 / --json out.json）"
        ),
    )
    parser.add_argument(
        "offline_dir",
        type=Path,
        help="离线数据源目录（**必须有 _schema.json**）：DataBaseCompiled 根，或 <pid> 实例目录",
    )
    parser.add_argument(
        "dump",
        type=Path,
        help="探针落盘的 dump.json（meta / tables / 可选 probe.computed_columns）—— 不是离线目录",
    )
    parser.add_argument("--instance", default=None, help="给数据源根时指定实例 id（如 52085）")
    parser.add_argument(
        "--table",
        action="append",
        default=None,
        help="只比这张表（可重复；认 core/asset/tables.py 里的别名）",
    )
    parser.add_argument("--limit", type=int, default=None, help="每节最多打印多少条（不改计数）")
    parser.add_argument(
        "--json", type=Path, default=None, dest="json_out", help="把完整报告另存一份 JSON"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """.venv\\Scripts\\python.exe tools\\dump_diff.py <离线目录> <dump.json> [选项]"""
    args = build_parser().parse_args(argv)

    try:
        source = resolve_offline(args.offline_dir, instance=args.instance)
        offline = read_offline_tables(source)
        dump = read_dump(args.dump)
        only = None
        if args.table:
            only = []
            for name in args.table:
                target = table_naming.resolve_table_name(name, offline)
                if target is None:
                    raise ValueError(
                        f"离线数据源里没有表 {name}；可选：{', '.join(offline)}"
                    )
                if target not in only:
                    only.append(target)
        report = compare(offline, dump, only=only, offline_dir=source.instance_dir)
    except (OSError, ValueError, UserFacingError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    for line in format_report(report, limit=args.limit):
        print(line)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"[json] {args.json_out}")

    return 1 if report.has_differences() else 0


if __name__ == "__main__":
    raise SystemExit(main())
