"""从磁盘读取数据源。

负责三件事：找到数据源目录 → 加载实例 → 把单张表读成 `Table`。

## 表名归一（2026-10-01）

目录数据源过去按**文件干**建表名，而 `core/asset/tables.py` 的规范表名里有一处不
一致：条目名（= 磁盘文件名）`PlaneFlyPresets.json` ↔ **表名 `FlyPresets`**。
于是同一张表在两个来源里叫两个名字 —— zip / 容器来源叫 `FlyPresets`，目录来源叫
`PlaneFlyPresets`。后果实测过两条：

1. 规则书里 `fk.Mobility.FlyPresetId` 的目标表只能写其中一个名字，另一个来源上
   **这条规则整条跑不了**（引擎降级成一条 warning「跑不了这条规则」）；
2. 界面上凡是拿这个名字当键的地方（表中文名 / emoji / 外键跳转）都要写两遍。

所以归一只做在 `load_dataset` 一处（:func:`canonical_table_name`）：磁盘文件名一个字
都不动（游戏认那个名字），**交出去的表名一律是规范名**。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from ba_mod_maker.core.asset import tables
from ba_mod_maker.core.data.dataset import Dataset, Instance, Table
from ba_mod_maker.core.data.schema import (
    TableSchema,
    load_instance_roles,
    load_manifest,
    load_schema,
)
from ba_mod_maker.core.errors import DataSourceError

#: 数据源目录的标志文件名。有它才能认定是一个合法数据源。
DATASET_DIRNAME = "DataBaseCompiled"

#: 实例目录名的长度特征（它们是 pathID，当前为 5 位数字）。
#: 用来区分「实例目录」和「其他杂物」，而不硬编码 52082/52085。
_MIN_INSTANCE_DIRNAME_LEN = 3


def find_dataset_root(start: Path | None = None, *, max_levels: int = 5) -> Path | None:
    """从 `start` 向上逐层查找，找到包含 `_schema.json` 的目录。

    为什么是向上找而不是写死路径：数据源和产品目录是并列的，
    写死就把产品绑在了某台机器的目录布局上。

    返回 `None` 表示未找到（调用方决定是弹选择框还是报错）。
    """
    current = (start or Path.cwd()).resolve()
    for _ in range(max_levels):
        candidate = current / DATASET_DIRNAME
        if _is_dataset_root(candidate):
            return candidate
        if _is_dataset_root(current):
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def canonical_table_name(name: str) -> str:
    """「按文件名建的表名」-> `core/asset/tables.py` 的**规范表名**。

    全库只有一对不一致：条目名（= 磁盘文件名）`PlaneFlyPresets.json` ↔ 表名
    `FlyPresets`。目录数据源按文件干建表名，于是同一张表在 zip / 容器来源里叫
    `FlyPresets`、在目录来源里叫 `PlaneFlyPresets`（见模块 docstring）。

    **认不出来就原样返回** —— 归一不是"猜一个名字"，只做注册表里登记过的那对映射
    （`tables.BY_ENTRY` 是那张注册表本身，不另抄一份）。
    """
    naming = tables.BY_ENTRY.get(f"{name}.json")
    return naming.table if naming is not None else name


def _canonical_schema(raw: dict[str, TableSchema]) -> dict[str, TableSchema]:
    """schema 的键（与 `TableSchema.name`）一律换成规范表名。

    `TableSchema.name` 也一起换：它出现在报错话术里（`index_of` 的 KeyError），
    留着旧名字会让人以为数据源里真有两张表。
    """
    out: dict[str, TableSchema] = {}
    for name, spec in raw.items():
        canonical = canonical_table_name(name)
        out[canonical] = spec if canonical == name else replace(spec, name=canonical)
    return out


def _canonical_manifest(raw: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """manifest 的键同样归一 —— 否则 `Instance.manifest_entry("FlyPresets")` 查空。

    `_manifest.json` 的键取自 `zip_entry`（文件干那一套），所以它一直是
    `PlaneFlyPresets`；不对齐的话那张表的行数对账会被**静默跳过**。
    """
    return {canonical_table_name(name): entry for name, entry in raw.items()}


def load_dataset(root: Path) -> Dataset:
    """加载一个数据源：schema + 目录下所有实例。

    实例目录的认定规则：名字是数字且含 `_manifest.json`。
    （没有硬编码 52082/52085：游戏更新后 pathID 会变。）

    ⚠️ 这里同时做了**表名归一**（:func:`canonical_table_name`）—— 全仓只此一处，
    别在各处打补丁：目录来源交出去的表名必须与 zip / 容器来源一致，否则规则书、
    界面标签、外键跳转都会在两个来源上表现不一样。
    """
    root = Path(root)
    schema = _canonical_schema(load_schema(root))

    instances: dict[str, Instance] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not _looks_like_instance(child):
            continue
        instances[child.name] = Instance(
            path_id=child.name,
            directory=child,
            schema=schema,
            manifest=_canonical_manifest(load_manifest(child)),
        )

    if not instances:
        raise DataSourceError(
            f"{root} 下没找到任何实例目录",
            hint="每个实例应是一个含 _manifest.json 的数字命名子目录",
        )

    return Dataset(
        root=root,
        schema=schema,
        instances=instances,
        instance_roles=load_instance_roles(root),
    )


def read_table(instance_dir: Path, schema: dict[str, TableSchema], name: str) -> Table:
    """从**散文件**读一张表。

    两种形状都要处理：

    1. **数组表**（ 23 张）—— `[{列:值}, ...]`，直接按 schema 列顺序取值。
    2. **异形表**（`PlaneFlyPresets`）—— `{key: {列:值}}`，归一化成
       「key + 整个对象」的两列表，让界面不需要特殊分支。

    解析与校验都在 :func:`table_from_bytes` 里：zip / 容器来源
    （`Instance.original_bytes`）走的是**同一个函数**，免得两条路各有一套列校验。
    """
    table_schema = schema.get(name)
    if table_schema is None:
        raise KeyError(f"数据源中不存在表 {name}")

    path = instance_dir / f"{name}.json"
    if not path.is_file():
        # 表名与**条目名**不一致的那一张（`FlyPresets` <-> `PlaneFlyPresets.json`）：
        # 目录数据源有的按文件干建表、有的按规范名建表，两边都得能读。
        naming = tables.BY_TABLE.get(name)
        alternative = instance_dir / naming.zip_entry if naming else None
        if alternative is not None and alternative.is_file():
            path = alternative
    try:
        # 原来是 `path.open(encoding="utf-8")` + `json.load`；改成读字节是为了把解析
        # 交给 `table_from_bytes`（两种来源同一套），报错话术一个字没改。
        blob = path.read_bytes()
    except FileNotFoundError:
        raise DataSourceError(
            f"实例 {instance_dir.name} 缺少表文件：{path.name}",
            hint="该实例可能提取不完整，建议重跑 extract_dbc.py",
            table=name,
        ) from None
    return table_from_bytes(blob, schema, name, source_path=path)


class TableBytes(NamedTuple):
    """一张「未改动的表」的**原始字节**，以及它是从哪来的。"""

    #: 原始明文 JSON 字节。
    blob: bytes
    #: 放进 `Table.source_path` 的路径：散文件是真路径，字节来源用条目名。
    path: Path


def entry_name_for(table: str) -> str:
    """表名 -> 权威条目名（`FlyPresets` -> `PlaneFlyPresets.json`）。

    24 张表里只有这一对有别名（见 `core/asset/tables.py` 的表名别名）。
    """
    naming = tables.BY_TABLE.get(table)
    return naming.zip_entry if naming is not None else f"{table}.json"


def locate_table_bytes(
    directory: Path,
    table: str,
    entry: str,
    original_bytes: Mapping[str, bytes] | None,
) -> TableBytes | None:
    """未改动的表，原始字节从哪来：**散文件 -> 打开时那份字节 -> None（两处都没有）**。

    这条顺序就是「未改动的表用哪份字节」的**规则本身**，所以实现只有这一份：
    保存（`core/edit/package.py::_original_bytes`）与重读整表
    （`dataset.Instance.reload_table`）都调这里 —— 两边各写一份的话，
    迟早出现"存进去的字节和重读回来的不是一个来源"。

    为什么散文件优先：`DataBaseCompiled` 那条路（`Instance.original_bytes is None`）
    的行为一个字节都不许变；zip / 容器来源磁盘上本来就没有散文件，自然落到第二档。
    """
    for candidate in (directory / f"{table}.json", directory / entry):
        if candidate.is_file():
            return TableBytes(candidate.read_bytes(), candidate)
    if original_bytes is not None:
        blob = original_bytes.get(entry)
        if blob is not None:
            return TableBytes(blob, Path(entry))
    return None


def table_from_bytes(
    blob: bytes, schema: dict[str, TableSchema], name: str, *, source_path: Path
) -> Table:
    """从**原始字节**建一张表（zip / 容器 / DBC_json 来源，散文件那条也走这里）。

    校验与散文件那条完全一样：顶层必须是数组、**不许出现 schema 不认识的列**
    （见 :func:`_column_order`；旧快照少列是允许的，缺哪几列记在
    `Table.missing_columns`）—— 否则"重读回来的表"与"打开时的表"会是两个形状。
    """
    table_schema = schema.get(name)
    if table_schema is None:
        raise KeyError(f"数据源中不存在表 {name}")
    try:
        # 显式 decode 而不是 `json.loads(blob)`：后者会吞掉 UTF-8 BOM，
        # 与原来 `path.open(encoding="utf-8")` 的行为不一致。
        payload: Any = json.loads(blob.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DataSourceError(
            f"表 {name} 不是合法 JSON：{source_path}",
            hint="文件可能被截断",
            error=f"第 {exc.lineno} 行：{exc.msg}",
            table=name,
        ) from None

    if not isinstance(payload, list):
        raise DataSourceError(
            f"表 {name} 的顶层结构不是数组，而是 {type(payload).__name__}",
            hint="当前实测的 24 张表全部是数组；出现其他形状说明数据格式变了",
            table=name,
        )
    return _build_list_shaped(name, table_schema, payload, source_path)


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #


class ColumnLayout(NamedTuple):
    """一张表**实际**的列（展示顺序）＋ 它比 `_schema.json` **少**了哪几列。

    `missing` 非空 = 这个实例是**旧版本快照**（理由见 :func:`_column_order`），
    不是数据坏了。它必须**交出去**（`Table.missing_columns`），⛔ 不许静默：
    用户在界面上看到的是一张少了几列的表，得能查出少的是哪几列。
    """

    columns: tuple[str, ...]
    missing: tuple[str, ...]


def _column_order(name: str, table_schema: TableSchema, payload: list[Any]) -> ColumnLayout:
    """决定列的**展示顺序**，并把「比 schema 少了哪几列」一并交出去。

    为什么用 JSON 的键顺序而不用 `_schema.json`：

    实测：`_schema.json` 的列序是**字母序**（24/24 张表都满足），
    而 JSON 的键顺序是**游戏里的声明顺序**。例如 Units：

    - 字母序：AudioPreset, CategoryType, ContentMembership, Cost, CountryId, ...
    - 声明序：Id, Name, HUDName, HUDIcon, ..., ModelFileName, ...

    后者把 Id / Name / HUDName 这类同族字段放在一起，符合阅读直觉；
    字母序会把它们打散到表的两端。

    ## 三种情形（2026-10-18 改；判据从「完全相等」拆成两条）

    1. **相等** —— 返回 JSON 的键序（就是上面说的声明序）。
    2. **实例缺列**（只少不多）—— **允许**：按**该实例自己的列**返回，
       缺的列名放进 `ColumnLayout.missing`。理由见下 ★。
    3. **实例多出 schema 不认识的列** —— 仍然 `DataSourceError`。
       那是**真漂移**：产品不认识这列，按它写回就是静默丢数据。

    ★ 为什么「实例缺列」必须允许：`DataBaseCompiled/` 是**多版本快照**。
    实测 2026-10-18 有 7 个实例（52082 / 52085 / 52460 / 52461 / 52462 /
    52463 / 52486），它们**不是同一版本**：`52460`（2026-10-17 12:00 重抽
    的那份）的 `Units` 只有 **28** 列，比 `_schema.json` 的 31 列少
    `TimeModifierRefund` / `TimeModifierResurrect` / `UpkeepMultiplier`；
    另 6 张旧表也少列（`Abilities` 少 15 / `Weapons` 少 4 /
    `Ammunitions` 少 4 / `Options` 少 3 / `Turrets` 少 1 /
    `SquadWeapons` 少 1），逐张都对得上该实例 `_manifest.json` 的 `keys`。
    用户能在状态栏**逐个实例打开**，旧快照天然没有后来新增的列 ——
    拿新 schema 去卡旧快照，等于让旧实例直接打不开。

    ⛔ 两条不许退：**多出来的未知列仍然报错**（真漂移必须当场红）；
    **缺了哪几列不许吞掉**（见 `Table.missing_columns` /
    `Instance.missing_columns()`）。
    """
    expected = set(table_schema.columns)
    if not payload or not isinstance(payload[0], dict):
        # 没有行就当「一列都没少」：没有数据可读，也不该凭空指责它缺列。
        return ColumnLayout(table_schema.columns, ())

    actual = tuple(payload[0].keys())
    unknown = set(actual) - expected
    if unknown:
        raise DataSourceError(
            f"表 {name} 的列与 _schema.json 不一致",
            hint="数据源可能被改过（出现了产品不认识的列），建议重跑 extract_dbc.py",
            table=name,
            missing=sorted(expected - set(actual))[:6],
            extra=sorted(unknown)[:6],
        )
    present = expected.intersection(actual)
    missing = tuple(column for column in table_schema.columns if column not in present)
    return ColumnLayout(actual, missing)


def _build_list_shaped(
    name: str, table_schema: TableSchema, payload: list[Any], path: Path
) -> Table:
    """把 [{列:值}] 转成按列顺序的行列表。

    列用**这个实例自己的**（`ColumnLayout.columns`）—— 旧版本快照少几列时，
    行的宽度跟着它自己的列走，不会凭空多出几个 None。
    少了哪几列挂在 `Table.missing_columns` 上，⛔ 不静默。
    """
    layout = _column_order(name, table_schema, payload)
    columns = layout.columns
    rows: list[list[Any]] = []
    for position, item in enumerate(payload):
        if not isinstance(item, dict):
            raise DataSourceError(
                f"表 {name} 的第 {position} 行不是对象：{type(item).__name__}",
                table=name,
            )
        # 用 get 而不是 []：实测下本次数据 100% 对齐，
        # 但对缺列行为不确定的数据，缺失比报错更可用（显示为 None）。
        rows.append([item.get(column) for column in columns])
    return Table(
        name=name,
        columns=columns,
        rows=rows,
        source_columns=columns,
        source_path=path,
        missing_columns=layout.missing,
    )


def _is_dataset_root(path: Path) -> bool:
    return path.is_dir() and (path / "_schema.json").is_file()


def _looks_like_instance(path: Path) -> bool:
    name = path.name
    if len(name) < _MIN_INSTANCE_DIRNAME_LEN or not name.isdigit():
        return False
    return (path / "_manifest.json").is_file()
