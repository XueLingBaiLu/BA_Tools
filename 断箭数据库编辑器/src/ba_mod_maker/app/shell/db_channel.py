"""把「数据库通道」（`core/session`）桥接进 GUI 的数据源模型（零 Qt）。

## 为什么需要这一层

产品里有两套数据模型，它们各自都对，但从前没搭桥：

| 模型 | 在哪 | 形状 |
|---|---|---|
| `core.data.Dataset` | GUI 一直在用 | 目录 + 24 个 JSON 文件，`Instance.table()` **惰加载** |
| `core.session.OpenedSource` | 数据库通道 | 内存里的 `ObjectMeta`（来自三种文件之一） |

结果：CLI 能用「打开/保存/提取/导入」，GUI 却只能开 `DataBaseCompiled` 目录。
这一层把前者装进后者的形状。

## 为什么不落临时目录

最直觉的做法是"把 24 张表写成临时 JSON 再 `load_dataset(临时目录)`"。**不行** ——
`loader._column_order()` 有一条硬校验：**不许出现 `_schema.json` 不认识的列**（2026-10-18 起口径：
旧快照"缺列"是允许的、会被报成 `missing_columns`，但"多出未知列"仍然报错）。
临时目录里没有 `_schema.json`。

所以这里**在内存里直接造** `Dataset`/`Instance`/`Table`，并把 `Instance` 的惰加载
缓存预填满 —— `Instance.table()` 命中缓存就根本不碰磁盘（见 `dataset.py`）。

## 写回时怎么保证"没改过的表逐字节没变"

GUI 的编辑落在 `Table.rows`（**内存**），而 `core/edit/package.py` 要的是
**原始字节**（未改动的表逐字节搬进新包）。这一层只负责把来源的字节原样挂在
`Instance.original_bytes` 上；"哪些表要重新序列化"由**账本**回答
（`app/shell/edit_session.py` -> `core/edit/changeset.py`），
这里不再有第二套"改动清单"。

## 一条实测事实：判"改没改"按**内容**，不按字节

`Mobility` 的明文里有个 `1.5E+08`，而 Python 的 `json` 只会输出 `150000000.0` ——
"读出来再写回去"即使一个值都没动，字节也变了。按字节判等的两个假结论：

- 改动清单里凭空多出 `Mobility`（在说假话）；
- 保存时把没动过的表也重新序列化（密文不再逐字节复现原版）。

所以一律按**内容**判（账本记的是 `(表, 行 Id, 列)` 的值）。
📌 这条原先记在本模块那份"打开时行内容快照"（`_BASELINES`）的注释里；
那份快照随**保存通道收口**一起删了（全仓零调用方，见 AGENTS §4.5）——
知识留在这儿，别再踩一遍。
"""

from __future__ import annotations

from ba_mod_maker.core.data.dataset import (
    Instance,
)
from ba_mod_maker.core.data.from_source import (  # noqa: F401 - 老调用方仍按这两个名字取
    dataset_from_source,
    opened_file_of,
)
from ba_mod_maker.core.session import OpenedSource

#: 这些名字来自容器对象，是**容器的字段名**；GUI 要用的是**表名**。
#: 表名与条目名的对应关系由 `core.asset.tables` 那张权威清单给出。


def _iter_loaded_tables(instance: Instance):
    """只遍历**已经加载进内存**的表。

    为什么不用 `instance.table_names`：那会触发惰加载，而 `BA_Mod.zip` 这种来源
    本该只统计我们真的碰过的表。没加载过 = 用户没看过 = 不参与统计。
    """
    loaded = getattr(instance, "_tables", None)
    if loaded is None:  # pragma: no cover - 防御：将来 Instance 改了实现
        return []
    return list(loaded.items())


def describe_source(src: OpenedSource) -> tuple[str, str]:
    """返回 `(标题, 副标题)`，给窗口标题与状态栏用。"""
    pid = src.meta.path_id
    id_part = f"pathID {pid}" if pid is not None else "无 pathID"
    title = f"{src.path.name}"
    subtitle = f"{src.kind.label} · {id_part} · {len(src.meta.tables)} 张表"
    return title, subtitle
