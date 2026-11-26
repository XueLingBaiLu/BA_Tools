"""读 `_schema.json` 与 `_manifest.json`。

这两个文件是数据侧交付的机器可读契约，是列名与行数的权威来源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import DataSourceError

SCHEMA_FILENAME = "_schema.json"
RULES_FILENAME = "_rules.json"

#: `_rules.json` 里标记「默认非战役实例」的角色名。
#: 实测值：{"default_non_campaign": 52085, "campaign_route": 52082}。
DEFAULT_NON_CAMPAIGN_ROLE = "default_non_campaign"

MANIFEST_FILENAME = "_manifest.json"


@dataclass(frozen=True, slots=True)
class TableSchema:
    """一张表的列定义。

    属性
    ----
    name
        表名，如 `Units`。
    columns
        列名元组。

        ⚠️ **它的顺序是字母序，不是游戏里的声明顺序。**
        实测：24/24 张表都满足 `columns == sorted(columns)`。
        它的权威性在于「有哪些列」（集合），不在于“先显示哪列”。
        展示顺序取自 JSON 文件的键顺序（见 `core/data/loader.py`）。
    """

    name: str
    columns: tuple[str, ...]

    def index_of(self, column: str) -> int:
        """返回列下标；不存在时抛 `KeyError`。"""
        try:
            return self.columns.index(column)
        except ValueError:
            raise KeyError(f"表 {self.name} 不存在列 {column}") from None


def load_schema(root: Path) -> dict[str, TableSchema]:
    """读取 `_schema.json`，返回「表名 -> 列定义」。

    异常
    ----
    DataSourceError
        文件缺失、非法 JSON，或某表的列定义不是字符串数组。
    """
    path = root / SCHEMA_FILENAME
    raw = _read_json(path, what="表结构（_schema.json）")

    if not isinstance(raw, dict) or not raw:
        raise DataSourceError(
            f"{path} 应当是非空对象",
            hint="确认路径指向 DataBaseCompiled 目录本身",
        )

    schemas: dict[str, TableSchema] = {}
    for table_name, columns in raw.items():
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            raise DataSourceError(
                f"{SCHEMA_FILENAME} 中表 {table_name} 的列定义应为字符串数组",
                table=table_name,
            )
        schemas[table_name] = TableSchema(name=table_name, columns=tuple(columns))

    return schemas


def load_instance_roles(root: Path) -> dict[str, str]:
    """从 `_rules.json` 的 instances 段读「实例 id -> 角色名」。

    为什么读它而不是硬编码 52085/52082：

    `_rules.json` 的 instances 段写着
    `{"default_non_campaign": 52486, "campaign_route": [52460, 52461, 52462, 52463]}`，
    这是数据侧给出的**机器可读权威**。硬编码会把产品钉死在这一版 pathID 上，
    而 pathID 会随游戏更新变化——到时候症状是「打开就报找不到实例」。

    ⚠ 一个角色可以指**多个**实例（2026-10-15 实测：本体里有**四个**战役 profile ——
    `ConfigProfile_1.1.0.2 / _1.1.0.4 / _1.1.2 / _1.2.0`，都该标「战役」；
    而 `default_non_campaign` 仍只有一个，被 `DefaultConfigProfile` 引用的那个）
    ⇒ 值可以是**纯数字**、**数字字符串**、或它们的**数组**。

    返回值只保留「认得出是 pathID」的那些；`note` / `evidence` 这类说明性键忽略。
    文件缺失或结构不符时返回空字典：**角色信息是增强，不是运行前提**，
    没有它产品照样要能打开。
    """
    path = root / RULES_FILENAME
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    section = raw.get("instances")
    if not isinstance(section, dict):
        return {}

    def ids_of(value: object) -> list[str]:
        """一个值 -> 它指的那些实例 id。空 = 这不是一个 id（note / evidence 之类的说明键）。"""
        if isinstance(value, bool):        # bool 是 int 的子类，必须先挡掉
            return []
        if isinstance(value, int):
            return [str(value)]
        if isinstance(value, str) and value.isdigit():
            return [value]
        if isinstance(value, (list, tuple)):
            out: list[str] = []
            for item in value:
                out.extend(ids_of(item))
            return out
        return []

    roles: dict[str, str] = {}
    for role, value in section.items():
        for path_id in ids_of(value):
            roles[path_id] = role
    return roles


def load_manifest(instance_dir: Path) -> dict[str, dict[str, Any]]:
    """读取实例目录下的 `_manifest.json`，返回「表名 -> 条目」。

    ⚠️ **键名取自 `zip_entry` 的文件名干，不是 `table` 字段。**

    实测依据（两个实例都一样）：同一张表在 manifest 里有三个不同名字——

    | 字段 | 值 | 是否与磁盘一致 |
    |---|---|---|
    | `table` | `FlyPresets` | ❌ `_schema.json` 里没有这个名字 |
    | `field` | `FlyPresetsJson` | ❌ C# 字段名 |
    | `zip_entry` | `PlaneFlyPresets.json` | ✅ **唯一与磁盘一致** |

    这正是交接文档提到的那一处改名，但证据落在 `zip_entry` 上而不是 `field` 上。
    用 `table` 做键会导致 `PlaneFlyPresets` 查不到 manifest 条目。

    另一个实测提醒：异形表的 `rows` 是 `-1`。
    所以**不要把 manifest 的 rows 当作真实行数**，真实行数以实际读出的数据为准。
    """
    path = instance_dir / MANIFEST_FILENAME
    raw = _read_json(path, what="实例清单（_manifest.json）")

    tables = raw.get("tables")
    if not isinstance(tables, list):
        raise DataSourceError(f"{path} 缺少 tables 数组")

    manifest: dict[str, dict[str, Any]] = {}
    for entry in tables:
        if not isinstance(entry, dict):
            continue
        key = _entry_key(entry)
        if key is not None:
            manifest[key] = entry
    return manifest


def manifest_name_map(instance_dir: Path) -> dict[str, str]:
    """返回「canonical 表名 -> manifest 里的 `table` 字段」的对照，**只列出两者不同的**。

    用于诊断与文档：它把“哪几张表改过名”变成可读输出，
    而不是让后来人再去猜。
    """
    raw = _read_json(instance_dir / MANIFEST_FILENAME, what="实例清单（_manifest.json）")
    mapping: dict[str, str] = {}
    for entry in raw.get("tables", []):
        if not isinstance(entry, dict):
            continue
        key = _entry_key(entry)
        declared = entry.get("table")
        if key is not None and isinstance(declared, str) and declared != key:
            mapping[key] = declared
    return mapping


def _entry_key(entry: dict[str, Any]) -> str | None:
    """从 manifest 条目里取出与磁盘一致的表名。

    优先 `zip_entry` 的文件名干（实测唯一可靠），回退到 `table`。
    """
    zip_entry = entry.get("zip_entry")
    if isinstance(zip_entry, str) and zip_entry.endswith(".json"):
        stem = zip_entry[: -len(".json")]
        if stem:
            return stem
    declared = entry.get("table")
    return declared if isinstance(declared, str) else None


def _read_json(path: Path, *, what: str) -> Any:
    """读一个 JSON 文件，把常见失败翻译成人话。"""
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise DataSourceError(
            f"找不到{what}：{path}",
            hint="检查数据源路径是否指向 DataBaseCompiled",
        ) from None
    except UnicodeDecodeError as exc:
        raise DataSourceError(
            f"{what}不是合法的 UTF-8：{path}",
            hint="文件可能损坏，建议重跑 extract_dbc.py",
            error=str(exc),
        ) from None
    except json.JSONDecodeError as exc:
        raise DataSourceError(
            f"{what}不是合法 JSON：{path}",
            hint="文件可能被截断，建议重跑 extract_dbc.py",
            error=f"第 {exc.lineno} 行：{exc.msg}",
        ) from None
