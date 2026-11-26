"""数据集的内存模型：Dataset / Instance / Table。

这三层对应界面上的三层导航：数据源 → 实例 → 表。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.core.asset import container
from ba_mod_maker.core.data.schema import TableSchema
from ba_mod_maker.core.errors import DataSourceError


@dataclass(slots=True)
class Table:
    """一张已加载的表。

    属性
    ----
    name
        表名。
    columns
        列名元组，顺序即展示顺序。
    rows
        行数据，**每行是与 `columns` 同序的列表**。
    source_columns
        原始 JSON 的列名元组。对数组表而言等于 `columns；
        对异形表而言是它的原始 33 列，而 `columns` 已被归一化为两列。
    source_path
        它从哪个文件来。
    missing_columns
        这张表**比 `_schema.json` 少了哪几列**（按 schema 的字母序，空 = 一列不少）。

        非空不是错误 —— `DataBaseCompiled/` 是**多版本快照**，旧实例天然没有
        后来新增的列（实测 2026-10-18：`52460` 的 `Units` 只有 28 列，少
        `TimeModifierRefund` / `TimeModifierResurrect` / `UpkeepMultiplier`）。
        它是**读数**，不是异常：⛔ 不许静默 —— 界面/CLI 要能查出少了哪几列
        （判据与理由见 `core/data/loader.py::_column_order`）。
    """

    name: str
    columns: tuple[str, ...]
    rows: list[list[Any]]
    source_columns: tuple[str, ...]
    source_path: Path
    #: 见类 docstring；默认空 = 「不知道有缺」这一情形也用空（`from_source` 由数据
    #: 自己造 schema，本来就不存在「schema 有、这份没有」的差）。
    missing_columns: tuple[str, ...] = ()

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def cell(self, row: int, column: int) -> Any:
        """取单元格值；越界抛 `IndexError`。"""
        return self.rows[row][column]

    def column_index(self, column: str) -> int:
        """列名 -> 下标；不存在抛 `KeyError`。"""
        try:
            return self.columns.index(column)
        except ValueError:
            raise KeyError(f"表 {self.name} 不存在列 {column}") from None

    def row_signature(self, limit: int = 4) -> tuple[Any, ...]:
        """前 `limit` 行的签名，用于快速判定「数据有没有变」。"""
        return tuple(tuple(row) for row in self.rows[:limit])

    def check_against_manifest(self, entry: dict[str, Any]) -> list[str]:
        """与 manifest 条目对账，返回差异描述（空则一致）。

        用于捕获「第三方改过 JSON」这类情况。
        """
        problems: list[str] = []
        expected_keys = entry.get("keys")
        if isinstance(expected_keys, int) and expected_keys != self.column_count:
            problems.append(f"列数 manifest={expected_keys} 实际={self.column_count}")

        manifest_rows = entry.get("rows")
        if (
            isinstance(manifest_rows, int)
            and manifest_rows >= 0
            and manifest_rows != self.row_count
        ):
            problems.append(f"行数 manifest={manifest_rows} 实际={self.row_count}")

        first_keys = entry.get("first_keys")
        if isinstance(first_keys, list) and self.columns:
            actual = list(self.columns[: len(first_keys)])
            if actual != list(first_keys):
                problems.append("前几列名与 manifest 不一致")
        return problems


#: `OpenedFile.format` 的两个取值：写回**打开时那个文件**时按哪种格式写。
#: 归这里定义是为了不把 `core.session.SourceKind` 引进数据模型（那是"打开"那层的词汇）。
FORMAT_ZIP = "zip"
FORMAT_JSON = "json"


@dataclass(frozen=True, slots=True)
class OpenedFile:
    """这个实例是从**哪个文件**打开的 —— 保存要写回它本身（用户口径 2026-09-30）。

    「保存写回**你打开的那个文件**」这句话要成立，光记一个路径不够：还得记
    **按什么格式写**、以及包里**那些我们不认识的条目**。三样一起挂在实例上，
    理由与 `Instance.original_bytes` 完全相同（见 `core/edit/package.py` 的模块说明：
    同一份字节 > 同一处文件、不猜来源、来源跟着实例走）。

    字段
    ----
    path
        打开的那个文件。保存**写回这个路径**（临时文件 + 原子替换，不留任何副本）。
    format
        写回格式：`FORMAT_ZIP` = 平铺 24 个条目的外部库；`FORMAT_JSON` = 容器对象的
        JSON 文本。**不能按扩展名判断** —— 识别本来就按内容（`session.detect_kind`），
        用户手上的包可能叫别的名字。
    extra
        原包里我们不认识的条目（条目名 -> 原始字节）。写回时原样带上：游戏会静默
        跳过它们，但删掉就是**静默改别人的包**。
    meta
        `FORMAT_JSON` 要的容器对象（对象头/尾 + 每张表的 IV）：写回 json 就是把它
        序列化成文本。zip 来源用不上（`None`）。
    """

    path: Path
    format: str
    extra: dict[str, bytes] = field(default_factory=dict)
    meta: container.ObjectMeta | None = None


@dataclass(slots=True)
class Instance:
    """一个数据库实例，如 `52085`。

    表是**惰加载**的：只有被访问过的表才会读进内存。
    原因：24 张表合计 5 MB、两个实例 10 MB，启动时全读会白白拖慢冷启动。
    """

    path_id: str
    directory: Path
    schema: dict[str, TableSchema]
    manifest: dict[str, dict[str, Any]]
    _tables: dict[str, Table] = field(default_factory=dict, repr=False)
    #: 打开这个实例的那个来源里，**每个条目名的原始明文字节**（如 `Units.json` -> 字节）。
    #:
    #: - `None` = 普通目录数据源：原始字节就在 `directory/<条目名>.json` 里（行为与从前一致）；
    #: - 非 None = 这个实例是**打开一个容器 / 包 / 单个 json** 得来的：磁盘上没有散文件，
    #:   未改动的表要从这里拿字节。装它的是 `app/shell/db_channel.py::dataset_from_source()`，
    #:   内容就是 `OpenedSource.meta.tables[*].data`（与 `session._save_dbzip()` 同一份字节）。
    #:
    #: 为什么把来源挂在实例上，而不是"记下 zip 路径、保存时再读一遍"，
    #: 见 `core/edit/package.py` 模块说明里那一节（同一份字节 > 同一处文件；不靠猜来源）。
    original_bytes: dict[str, bytes] | None = None
    #: 打开这个实例的那个**文件**（保存写回它本身）；`None` = 不写回文件。
    #:
    #: - 目录数据源（`DataBaseCompiled/`）→ `None`：落点仍是 `<数据源同级>/BA_Mod.zip`；
    #: - `BA_Mod.zip` / `DataBaseCompiled.json` → 有值：写回它本身（见 :class:`OpenedFile`）；
    #: - **`data.unity3d` 也是 `None`**：重写一次 7.1 GB / 约 4 分钟，不能挂在
    #:   "改一格就自动保存"上；那条路的落点仍是同级目录的 `BA_Mod.zip`。
    #:
    #: 三种来源怎么判**只有一处**：`app/shell/db_channel.py::opened_file_of()`；
    #: 落点怎么算也只有一处：`core/edit/package.py::default_save_target()`。
    opened_file: OpenedFile | None = None

    @property
    def table_names(self) -> tuple[str, ...]:
        """该实例下可用的表名，按 schema 顺序。"""
        return tuple(self.schema.keys())

    def table(self, name: str) -> Table:
        """取一张表（首次访问时读入）。"""
        cached = self._tables.get(name)
        if cached is not None:
            return cached

        table = self._read_table(name)
        self._tables[name] = table
        return table

    def reload_table(self, name: str) -> Table:
        """重新读一张表，并替换缓存里的那份。

        用途：用户想放弃改动。由于内存里的行是被直接改的，
        想回到干净状态**只能重读**——不能反推。

        来源是 zip / 容器 / DBC_json 时，读的是**打开时那份字节**
        （`original_bytes`）—— 那几种来源磁盘上本来就没有散文件。
        """
        table = self._read_table(name)
        self._tables[name] = table
        return table

    def _read_table(self, name: str) -> Table:
        """这张表的原始字节从哪来：**散文件 -> 打开时那份字节**（与保存同一条口径）。

        口径本身在 `data/loader.py::locate_table_bytes`（只有那一份实现，
        保存路径也调它）；这里只负责挑一条路、并在两处都没有时报错。

        报错**分两种话术**：普通目录数据源走原来的 `read_table`
        （"缺少表文件 / 表名不存在"一个字都不变），
        zip / 容器来源则说清"重读不了"——与保存那条报错同一个口径。
        """
        from ba_mod_maker.core.data import loader

        if self.original_bytes is not None:
            found = loader.locate_table_bytes(
                self.directory, name, loader.entry_name_for(name), self.original_bytes
            )
            if found is not None:
                return loader.table_from_bytes(
                    found.blob, self.schema, name, source_path=found.path
                )
            if name not in self.schema:
                raise KeyError(f"数据源中不存在表 {name}")
            raise DataSourceError(
                f"找不到 {name} 的源文件（{self.directory}）",
                hint="重读整表要把它恢复成**打开时**那份，而这个来源里没有它的字节，"
                "所以**重读不了**。请用「打开…」重新打开 data.unity3d / "
                "DataBaseCompiled.json / BA_Mod.zip。",
                table=name,
            )
        return loader.read_table(self.directory, self.schema, name)

    def preload(self) -> Iterator[tuple[str, Table]]:
        """逐张加载并交出，供界面做带进度的预加载。"""
        for name in self.table_names:
            yield name, self.table(name)

    def manifest_entry(self, name: str) -> dict[str, Any]:
        return self.manifest.get(name, {})

    def missing_columns(self, name: str) -> tuple[str, ...]:
        """这个实例的某张表**比 `_schema.json` 少了哪几列**（按 schema 字母序）。

        空 = 一列不少。非空 = 这是**旧版本快照**（见 `Table.missing_columns`
        与 `core/data/loader.py::_column_order`），不是数据损坏。

        为什么要挂在实例上：用户是在**状态栏逐个实例切**的，问的就是
        「这个实例缺什么」—— 让他自己去 diff 两张表的列名不现实。
        """
        return self.table(name).missing_columns


@dataclass(slots=True)
class Dataset:
    """整个数据源：一位 schema + 若干实例。"""

    root: Path
    schema: dict[str, TableSchema]
    instances: dict[str, Instance]
    #: 实例 id -> 角色名（来自 `_rules.json`）。
    #: 为空表示数据源没提供角色信息，此时一切退回「按目录名排序」。
    instance_roles: dict[str, str] = field(default_factory=dict)

    @property
    def instance_ids(self) -> tuple[str, ...]:
        return tuple(self.instances.keys())

    @property
    def latest_instance_id(self) -> str:
        """pathID 最大的那个实例。

        【用户实证】游戏更新后，常用的 `DataBaseCompiled` 是 **pathID 更大** 的那个。
        本轮未用代码复核这一点（手头只有一个版本的实例目录），所以标为用户实证。

        一处相容的数据读数（不是证明）：`_refs_52082_52085.json` 里
        较大的 52085 对应 `DefaultConfigProfile`，较小的 52082 对应
        `ConfigProfile_1.1.0.4`（战役）——与 `_rules.json` 的角色分配一致。

        为什么取最大而不是最小：目录名排序的第一个恰好是**最小**的，
        而最小的那个恰恰是战役实例。选错的后果是用户打开就在改一份
        他不用的数据。
        """
        return max(self.instance_ids, key=int)

    @property
    def stale_role_ids(self) -> tuple[str, ...]:
        """`_rules.json` 里提到、但数据源里不存在的实例 id。

        非空 = **游戏很可能更新过**：pathID 换了，而规则文件还是上一版的。
        此时应当重跑 `make_rules.py` 而不是继续用旧角色。

        为什么要把它曝露出来：否则失效是静默的——界面会一声不吿地退到
        “取最大 pathID”，用户永远不知道自己的规则文件已经过期。
        """
        return tuple(pid for pid in self.instance_roles if pid not in self.instances)

    @property
    def default_instance_id(self) -> str:
        """打开时应当选中的实例 = **pathID 最大的那个**（见 `latest_instance_id`）。

        用户口径（2026-10-15 原话）：「**默认指到 pathID 最大的那个**」。

        ★ 为什么从「`_rules.json` 优先、取不到才退到最大」改成「一律取最大」：
          旧逻辑在旧数据源上看着对，只是因为 52085 恰好**既是角色里的默认、又是最大的**。
          2026-10-15 从本体重抽数据源之后两者分家了 —— `_rules.json`（2026-09-27 生成，
          而 `make_rules.py` 里那两个 id 是**写死**的）说默认是 52085（540 行），
          而本体里最大的是 **52486（542 行）**。于是「用户打开就在改一份他不用的数据」
          正是这条逻辑本来要防的事。
        ★ 另一条硬理由：**容器源取的就是 pathID 最大的那个**
          （`core/session.py::_open_unity3d` → `ufs.find_database_objects(...)[-1]`，三处都是）
          ⇒ 目录源与容器源从此选同一个实例，两条路不会各改各的。
        ⛔ 别再退回「角色优先」：角色文件里的 id 是**生成期**写死的，游戏一更新就过期。

        `instance_roles` 仍保留，但**只当标签**（界面上标「战役 / 默认」）；
        `stale_role_ids` 照旧报告规则文件过期。

        ⚠️ 这不是「唯一重要的实例」：data 侧的另一条结论是
        「只改一份 = 只有一半模式生效」。默认只决定**先打开哪个**。
        """
        return self.latest_instance_id

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(self.schema.keys())

    def instance(self, path_id: str) -> Instance:
        try:
            return self.instances[path_id]
        except KeyError:
            raise KeyError(f"数据源中没有实例 {path_id}，可用：{list(self.instances)}") from None

    def verify(self, path_id: str, *, tables: Sequence[str] | None = None) -> dict[str, list[str]]:
        """把指定实例的表与 manifest 对账，返回「表名 -> 问题列表」。

        只返回有问题的表；一致的表不出现在结果里。
        """
        target = self.instance(path_id)
        names = tables if tables is not None else target.table_names
        report: dict[str, list[str]] = {}
        for name in names:
            table = target.table(name)
            problems = table.check_against_manifest(target.manifest_entry(name))
            if problems:
                report[name] = problems
        return report
