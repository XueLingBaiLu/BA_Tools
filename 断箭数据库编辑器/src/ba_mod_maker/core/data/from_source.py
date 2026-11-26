"""把一次「打开」的结果（`OpenedSource`）装成上层认的 `Dataset`。

## 为什么它在 core/ 里

这三件（`opened_file_of` / `dataset_from_source` / `_fallback_id`）本来是
`app/shell/db_channel.py` 里的，零 Qt、依赖也只有 core —— 但 `src/ba_mod_maker/cli/`
的硬约束是「**不依赖 app**」（README 的分层表），无人值守的 `validate` / `find`
也要把打开的源喂给规则引擎（`core/rules/engine.py` 收的是 `Instance`）。
搬到 core 之后 CLI 与 GUI 用**同一份**桥接逻辑，既不破那条分层约束，也不抄第二份。
`app/shell/db_channel.py` 仍然 re-export 这两个名字（老调用方一个都不用改）。

## 三种来源怎么判「写回哪个文件」

只有 `opened_file_of()` 一处：`BA_Mod.zip` / `DataBaseCompiled.json` 写回它自己，
`data.unity3d` 给 `None`（重写一次 7.1 GB / 约 4 分钟，不能挂在自动保存上）。
"""

from __future__ import annotations

from pathlib import Path

from ba_mod_maker.core.data.dataset import (
    FORMAT_JSON,
    FORMAT_ZIP,
    Dataset,
    Instance,
    OpenedFile,
    Table,
)
from ba_mod_maker.core.data.schema import DEFAULT_NON_CAMPAIGN_ROLE, TableSchema
from ba_mod_maker.core.errors import DataSourceError
from ba_mod_maker.core.session import OpenedSource, SourceKind


def opened_file_of(src: OpenedSource) -> OpenedFile | None:
    """这次打开的**文件**要不要作为「保存写回它本身」的落点。

    用户口径（2026-09-30）：「保存写回**你打开的那个文件**」。

    | 打开的是 | 返回 | 为什么 |
    |---|---|---|
    | `BA_Mod.zip`（老名字 `db.zip` 也认） | 那个文件 | 毫秒级写回；包里的未知条目原样带回去 |
    | `DataBaseCompiled.json` | 那个文件 | ~5.8 MB，毫秒级 |
    | **`data.unity3d`** | **`None`** | 见下 |
    | 目录数据源（`DataBaseCompiled/`） | —— | 走的是 `load_dataset`，根本不进这里 |

    `data.unity3d` 为什么是 `None`：重写一次要 7.1 GB / 约 4 分钟，不能挂在
    「改一格就自动保存」上。那条路的落点仍是**同级目录的 `BA_Mod.zip`**（没变）。

    判据用 `src.kind`（**按内容认的**，不是扩展名）—— 用户手上的包可能叫别的名字。
    ⚠️ 这里也是**唯一**决定「哪种来源可以写回文件」的地方：落点怎么算在
    `core/edit/package.py::default_save_target()`，格式在 `OpenedFile.format`。
    """
    if src.kind is SourceKind.UNITY3D:
        return None
    if src.kind is SourceKind.DBC_JSON:
        # json 是容器对象的序列化：写回要用**打开时那个对象**（头/尾 + 每张表的 IV）。
        return OpenedFile(path=Path(src.path), format=FORMAT_JSON, meta=src.meta)
    return OpenedFile(
        path=Path(src.path),
        format=FORMAT_ZIP,
        # 原包里我们不认识的条目：写回时原样带上（游戏跳过它们，删掉就是静默改别人的包）。
        extra=dict(src.extra_entries or {}),
    )


def dataset_from_source(src: OpenedSource) -> Dataset:
    """把一次「打开」的结果装成上层认的 `Dataset`。

    返回的 `Dataset` 里，实例的表**已经全部在内存里**，不会再去读磁盘。
    """
    schema: dict[str, TableSchema] = {}
    tables: dict[str, Table] = {}
    manifest: dict[str, dict] = {}

    for slot in src.meta.tables:
        rows = slot.json_payload()
        if not isinstance(rows, list):
            raise DataSourceError(
                f"表 {slot.table} 的顶层不是数组",
                hint="实测 24 张表都是数组；出现别的形状说明数据有问题",
            )
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        columns = tuple(first.keys())
        schema[slot.table] = TableSchema(name=slot.table, columns=columns)
        tables[slot.table] = Table(
            name=slot.table,
            columns=columns,
            rows=[list(row.values()) for row in rows],
            source_columns=columns,
            source_path=Path(f"{slot.zip_entry}"),
        )
        manifest[slot.table] = {"rows": len(rows), "keys": len(columns)}

    path_id = str(src.meta.path_id) if src.meta.path_id is not None else _fallback_id(src)
    instance = Instance(
        path_id=path_id,
        directory=src.path.parent,
        schema=schema,
        manifest=manifest,
        # ★ 打开时那份来源的**原始明文字节**（条目名 -> 字节）。
        #   这几种来源在磁盘上都没有散文件，保存时「未改动的表」只能靠它逐字节搬进新包；
        #   它就是 session._save_dbzip() 写回时用的那份字节（见 core/edit/package.py 的模块说明）。
        original_bytes={slot.zip_entry: slot.data for slot in src.meta.tables},
        # ★ 打开的那个**文件**（保存写回它本身）。三种来源怎么判只有一处：
        #   `opened_file_of()` —— 那里写着为什么 data.unity3d 是 None。
        opened_file=opened_file_of(src),
    )
    # ★ 预填惰加载缓存：`Instance.table()` 命中它就不会去读磁盘。
    instance._tables.update(tables)  # noqa: SLF001 — 这是本函数存在的全部意义

    roles: dict[str, str] = {}
    if src.kind is not SourceKind.DB_ZIP:
        # BA_Mod.zip 不带 pathID（它没有容器元数据），所以没有「默认实例」这回事。
        roles[path_id] = DEFAULT_NON_CAMPAIGN_ROLE

    return Dataset(
        root=src.path.parent,
        schema=schema,
        instances={path_id: instance},
        instance_roles=roles,
    )


def _fallback_id(src: OpenedSource) -> str:
    """`BA_Mod.zip` 没有 pathID 时，给它一个**能用的**实例 id。

    ⚠️ **不能是任意字符串**：`core.data.Dataset.latest_instance_id` 会做
    `max(instance_ids, key=int)`，非数字直接抛 `ValueError`
    （实测踩到：加载 BA_Mod.zip 时炸在 UI 构建里）。

    也**不能编一个 pathID**：`BA_Mod.zip` 是明文表，不携带任何容器元数据，
    它的 pathID 本来就是未知的。写回容器时用的是**目标容器**的 pathID
    （见 `session.import_into_container`）。

    所以：能查到游戏容器就用它的 pathID（用户手上那份数据本来也是它的），
    查不到才退到中性占位 `0`。
    """
    from ba_mod_maker.core.assets import game as game_mod

    root = game_mod.find_game_root_for_db()
    if root is not None:
        try:
            from ba_mod_maker.core.asset import ufs

            with ufs.UfsReader(game_mod.data_file(root)) as reader:
                found = ufs.find_database_objects(reader)
            # 与 open_source 同一口径：取 pathID 最大的那个。
            return str(found[-1][0])
        except Exception:  # noqa: BLE001 — 查不到就退占位；
            # 不该因为这台机器没有游戏而打不开一个 BA_Mod.zip
            pass
    return "0"


__all__ = ["dataset_from_source", "opened_file_of"]
