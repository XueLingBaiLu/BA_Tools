"""命令行给的那个路径 -> 一份可以直接扫的 `Instance`（零 Qt，**不依赖 app**）。

`validate` 与 `find` 吃的都是 `core.data.dataset.Instance`：规则引擎收它
（`core/rules/engine.py::RuleEngine.validate`），「谁引用了这一行」也收它
（`core/edit/refs.py::referencing_rows`）。所以「源怎么变成实例」只写在这里**一处**，
两条命令都调 :func:`load`，谁也不自己拼一份（AGENTS §5「逻辑只有一份」）。

## 认哪些源（`--help` 里也这么写）

| 命令行给的 | 走哪条 |
|---|---|
| `DataBaseCompiled/`（含 `_schema.json`） | `core.data.load_dataset` |
| `DataBaseCompiled/52085/`（数字名 + `_manifest.json`） | 同上，实例就是这一层 |
| `BA_Mod.zip`（老名字 `db.zip` 也认，按内容识） | `core.session.open_source` + 桥 |
| `DataBaseCompiled.json` | 同上 |
| `data.unity3d` / 游戏根目录 | 同上（**只读**打开，一个字节都不写） |

「桥」= `core/data/from_source.py::dataset_from_source()`（2026-10-01 从
`app/shell/db_channel.py` 搬进 core 的，GUI 与 CLI 现在共用这一份）。

两种形状最后交出来的都是同一种 `Instance`，命令实现不分叉。

## 一个踩过的坑（已修，2026-10-01）

第一次跑 `validate` 时逮到：**同一份数据的两种来源给出不一样的警告数** ——
目录来源 699、zip 来源 700。多出来的是 `fk.Mobility.FlyPresetId`：规则书里那条规则的
目标表写作 `PlaneFlyPresets`，而 zip / 容器 / json 来源里这张表的**规范名是
`FlyPresets`**（`PlaneFlyPresets.json` 只是条目名，见 `core/asset/tables.py` 那张
三套名字的表）—— 引擎取不到表，把它降级成一条 warning「跑不了这条规则」，
于是**那条规则在 zip 来源上等于没跑**。

修的不是 CLI，是名字本身：`core/data/loader.py::load_dataset` 一处归一到规范名
（磁盘文件名一个字没动），规则书跟着改用 `FlyPresets`。现在两种来源都是 699，
`tests/test_table_naming.py` 用内存小样本钉住「两边一致 + 那条规则真的报出真违规」。

⚠️ 顺带记一条**不成立**的直觉：这不是"桥没装好"（`original_bytes`）的症状 ——
实测装与不装结论一模一样。引擎取不到表时拿到的是 `KeyError`，
`RuleEngine.validate` 接得住，只是记成一条 warning。**别拿"换个装载方式"解释这类症状。**
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ba_mod_maker.core.data.dataset import Instance

#: 数据源目录的标志文件（与 `core/data/loader.py` 同一份知识，这里只读）。
SCHEMA_MARKER = "_schema.json"

#: 实例目录的标志文件（同上）。
MANIFEST_MARKER = "_manifest.json"


@dataclass(frozen=True, slots=True)
class Loaded:
    """一次装载的结果：能扫的实例 + 几句**如实**的说明（报告头直接读它）。"""

    instance: Instance
    path: Path
    #: 人读的来源种类，如「外部库 BA_Mod.zip」「目录数据源 DataBaseCompiled」。
    label: str
    #: 机器读的来源种类：`directory` / `data.unity3d` / `DataBaseCompiled.json` /
    #: `BA_Mod.zip`。
    kind: str
    #: 容器 / json 来源才有；`BA_Mod.zip` 与目录来源是 `None`（**不猜**，见下）。
    path_id: str | None
    tables: int
    rows: int


def dataset_root_of(path: Path) -> Path | None:
    """给一个目录，判断它是不是数据源（或数据源里的某个实例目录）。

    是就返回**数据源根**；不是返回 `None`（调用方接着走 `open_source` 那条）。
    判据与 `core/data/loader.py` 一致：根看 `_schema.json`，实例看「数字名字 +
    `_manifest.json`」。
    """
    if not path.is_dir():
        return None
    if (path / SCHEMA_MARKER).is_file():
        return path
    if path.name.isdigit() and (path / MANIFEST_MARKER).is_file():
        return path.parent
    return None


@contextmanager
def load(path: str | Path) -> Iterator[Loaded]:
    """打开 `path`，交出一份能扫的 `Loaded`（容器来源的读取器在退出时关掉）。"""
    target = Path(path)
    root = dataset_root_of(target)
    if root is not None:
        yield _from_directory(root, target)
        return

    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source

    with session.open_source(target) as opened:
        # 桥只有这一份（core 的；它 2026-10-01 从 app/shell/db_channel.py 搬过来）。
        dataset = dataset_from_source(opened)
        instance = dataset.instance(dataset.default_instance_id)
        yield _describe(
            instance,
            path=opened.path,
            label=opened.kind.label,
            kind=opened.kind.value,
            path_id=str(opened.meta.path_id) if opened.meta.path_id is not None else None,
        )


def _from_directory(root: Path, target: Path) -> Loaded:
    """目录数据源：走 `load_dataset`（与界面「打开目录」同一条路）。"""
    from ba_mod_maker.core.data import load_dataset

    dataset = load_dataset(root)
    # 给的是实例目录就用它；给的是根就按 _rules.json 的角色取默认实例。
    path_id = target.name if target != root else dataset.default_instance_id
    instance = dataset.instance(path_id)
    label = f"目录数据源 {root.name}"
    label += f"/{path_id}" if target != root else f"（默认实例 {path_id}）"
    return _describe(instance, path=target, label=label, kind="directory", path_id=path_id)


def _describe(
    instance: Instance, *, path: Path, label: str, kind: str, path_id: str | None
) -> Loaded:
    """把实例的规模数出来（表数 / 行数）。**全部读进来**是故意的：

    规则引擎要跑 24 张表的规则，`find` 的名字检索也要整库过一遍 ——
    两条命令后面必然会碰每一张表，这里顺手数一遍不会多花什么。
    """
    rows = 0
    names = instance.table_names
    for name in names:
        rows += instance.table(name).row_count
    return Loaded(
        instance=instance,
        path=Path(os.path.abspath(path)),
        label=label,
        kind=kind,
        path_id=path_id,
        tables=len(names),
        rows=rows,
    )


__all__ = ["Loaded", "dataset_root_of", "load"]
