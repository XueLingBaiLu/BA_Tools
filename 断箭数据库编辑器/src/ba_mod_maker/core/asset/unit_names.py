"""单位 / 炮塔**名字表**：内部键 -> 游戏里显示的名字（**产品侧唯一读法**）。

## 口径（用户 2026-10-12 选定，⛔ 别改）

名字表**读游戏本体** `<游戏根>/BrokenArrow_Data/data.unity3d`。
⛔ **不用 zip**：用户原话「**你别用这种压缩包文件当名字表！**」——
包括装回 `<游戏根>/BA_Mod.zip` 那条。

## 实测代价（★ 推翻了源码里那句担心，可复算）

`importer.py` 里原来写着「为它去开一次 6.6 GB 的容器正是 2026-10-04 那条卡顿事故的形状」。
**2026-10-12 实测**（`_work/a8_probe_cost.py`）：

| 步 | 读数 |
|---|---|
| 文件 | 7,122,751,635 B（6.64 GiB）· mtime 2026-10-11T20:45:16 |
| `open_source(data.unity3d)` | **1.47 s** —— 不是 4 分钟：4 分钟是**写回整文件** |
| `slot.json_payload()`（Units） | 0.00 s · **540 行** |

⇒ 读一次 1.5 秒，所以「读一次就缓存」这条**没有任何性能借口不做**，但也**没有借口放进面板重绘路径**
（1.5 s 的重绘 = 卡死）。

## 为什么要有这个模块（而不是让插件直接读容器）

**Blender 自带的 Python 没有 pycryptodome** —— 实测（Blender 5.2.1 / CPython 3.13.13）：
`HAS_CRYPTO False`。而 `core/asset/dbcryp.py` 顶层就 `from Crypto.Cipher import AES`，
容器里 24 张表全是 **AES-256-CBC** 字段级加密。

⇒ 分工：**产品 CLI/内核读容器（有 Crypto，1.5 s）-> 落一份小 JSON 缓存 -> 插件读那份明文缓存**
（插件 **0 依赖**，读的是几十 KB 的 JSON）。`data.unity3d` 仍然是**唯一来源**，⛔ 不是 zip。

## 缓存与失效

* 缓存里记下来源的**廉价指纹** `(size, mtime_ns)`（`core/session.py::_file_stamp` 同一口径）；
* `is_fresh()` 只做一次 `stat`（微秒级）—— 指纹一变就说明游戏更新/被改过，缓存作废重读；
* 落点由调用方给（`default_cache_path()` 给的是用户级目录，⛔ **不写游戏目录**：往游戏目录塞文件会
  触发联机红线 —— `kb/26` §7：注入类**绝不联机**／静态替换**可以联机、风险自负**，
  一个缓存不值这个代价）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import DataSourceError

__all__ = [
    "CACHE_FORMAT",
    "CACHE_NAME",
    "UNIT_TABLE",
    "TURRET_TABLE",
    "TURRET_LINK_TABLE",
    "build_name_table",
    "default_cache_path",
    "display_name",
    "fingerprint_of",
    "is_fresh",
    "load_name_table",
    "save_name_table",
    "source_file",
]

#: 缓存文件格式版本（字段形状变了就 +1，读到旧版当没有）。
CACHE_FORMAT = 1
#: 缓存文件名（放在哪个目录由调用方决定）。
CACHE_NAME = "units_names.json"

#: 缓存 JSON 的**读前**上限 —— 旧家 `blender_addon/importer.py:407` 就有这条（8 MiB），
#: 插件重做时**丢了**：2026-10-15 实测（`_work/testport/probe_cache_guard.py`）
#: 喂它 7,122,751,635 B 的容器路径 ⇒ `read_text` **被调 1 次**再拒。
#: ⛔ 「结果是 None」不等于「代价没付」—— 7.12 GB 进内存就是那条卡顿事故的形状。
#: 口径照旧家逐字：**严格大于**才拒，且在 `read_text` **之前**。
CACHE_MAX_BYTES = 8 * 1024 * 1024

#: 名字表的权威出处（`ModelFileName` 就是 catalog 的地址键）。
UNIT_TABLE = "Units"
#: 炮塔模型名表。
TURRET_TABLE = "Turrets"
#: 单位 × 炮塔 配对表（列 Id / UnitId / TurretId / Order）。
TURRET_LINK_TABLE = "TurretUnits"

#: 三张表里用到的列（⛔ 只在这里写一遍）。
_ID = "Id"
_UNIT_MODEL = "ModelFileName"
_UNIT_HUD = "HUDName"
_UNIT_NAME = "Name"
_TURRET_NAME = "Name"


def source_file(game_root: str | Path) -> Path:
    """名字表的**唯一来源**：`<游戏根>/BrokenArrow_Data/data.unity3d`。"""
    return Path(game_root) / "BrokenArrow_Data" / "data.unity3d"


def default_cache_path() -> Path:
    """缓存的默认落点：用户级目录（⛔ 不落游戏目录、⛔ 不落 Steam 目录）。

    `%LOCALAPPDATA%/BAModMaker/units_names.json`；取不到环境变量时退到 `~/.bamod-maker/`。
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".bamod-maker"
    return root / "BAModMaker" / CACHE_NAME


def fingerprint_of(path: str | Path) -> dict[str, Any]:
    """来源文件的廉价指纹：`{path, size, mtime_ns}`（一次 stat，7 GB 也一样快）。"""
    target = Path(path)
    try:
        info = target.stat()
    except OSError as exc:
        raise DataSourceError(
            f"读不到名字表的来源：{target}",
            hint="先确认游戏根目录（BrokenArrow_Data/data.unity3d 在不在）",
            error=str(exc),
        ) from None
    return {"path": str(target), "size": int(info.st_size), "mtime_ns": int(info.st_mtime_ns)}


# --------------------------------------------------------------------------- #
# 读容器（只在有 Crypto 的那一侧跑：CLI / 内核）
# --------------------------------------------------------------------------- #
def _pairs_by_key(
    rows: Iterable[Mapping[str, Any]], key_column: str, name_columns: tuple[str, ...]
) -> dict[str, str]:
    """(键, 名字) 一串 -> 对照表。**只有这一处**做清洗（与插件侧同一条口径）。

    ★ 取名口径（v0.17.2 定，照抄 `blender_addon/importer.py::_names_from_pairs`）：
    **保留第一行**（表里最早那行，通常就是这个单位的本名），被多行共用时如实标成
    `本名 等 N 个` —— ⛔ 不假装一个键只有一个单位，也⛔ 不许「最后一行赢」。
    """
    first: dict[str, str] = {}
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(key_column) or "").strip()
        if not key:
            continue
        label = ""
        for column in name_columns:
            label = str(row.get(column) or "").strip()
            if label:
                break
        if key not in counts:
            counts[key] = 1
            first[key] = label
            continue
        counts[key] += 1
        if not first[key] and label:
            first[key] = label
    return {
        key: (f"{label} 等 {counts[key]} 个" if counts[key] > 1 else label)
        for key, label in first.items()
    }


def _rows(instance: Any, table: str) -> list[dict[str, Any]]:
    """一张表 -> `[{列: 值}]`（走产品内核读侧，⛔ 不自造解析）。"""
    if table not in instance.table_names:
        return []
    slot = instance.table(table)
    columns = list(slot.columns)
    return [dict(zip(columns, row, strict=False)) for row in slot.rows]


def build_name_table(game_root: str | Path) -> dict[str, Any]:
    """读 `data.unity3d` 造名字表（**唯一入口**）。⚠ 真读盘，调用方负责缓存。

    产物同时带三样（一次读盘三份都用得上）：

    * `units`  —— `Units.ModelFileName -> HUDName`（+ 同名多行标注）—— **A8 要的就是它**；
    * `turrets` —— `Turrets.ModelFileName -> Name` —— 炮塔单独导入/可挑（B5）的底座；
    * `pairs`  —— `单位模型键 -> [{turret, order, turret_id}]` —— 车体 × 炮塔配对的底座。
    """
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.session import open_source

    container = source_file(game_root)
    stamp = fingerprint_of(container)
    src = open_source(container)
    try:
        dataset = dataset_from_source(src)
        instance = next(iter(dataset.instances.values()))
        unit_rows = _rows(instance, UNIT_TABLE)
        turret_rows = _rows(instance, TURRET_TABLE)
        link_rows = _rows(instance, TURRET_LINK_TABLE)
        units = _pairs_by_key(unit_rows, _UNIT_MODEL, (_UNIT_HUD, _UNIT_NAME))
        turrets = _pairs_by_key(turret_rows, _UNIT_MODEL, (_TURRET_NAME,))
    finally:
        src.close()

    units_by_id = {int(r.get(_ID) or 0): str(r.get(_UNIT_MODEL) or "") for r in unit_rows}
    turrets_by_id = {int(r.get(_ID) or 0): str(r.get(_UNIT_MODEL) or "") for r in turret_rows}
    pairs: dict[str, list[dict[str, Any]]] = {}
    for row in link_rows:
        unit_model = units_by_id.get(int(row.get("UnitId") or 0), "")
        turret_model = turrets_by_id.get(int(row.get("TurretId") or 0), "")
        if not unit_model or not turret_model:
            continue
        pairs.setdefault(unit_model, []).append(
            {
                "turret": turret_model,
                "order": int(row.get("Order") or 0),
                "turret_id": int(row.get("TurretId") or 0),
                "unit_id": int(row.get("UnitId") or 0),
            }
        )
    for items in pairs.values():
        items.sort(key=lambda item: (item["order"], item["turret_id"]))

    return {
        "format": CACHE_FORMAT,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": stamp,
        "counts": {
            "units_rows": len(unit_rows),
            "model_keys": len(units),
            "multi_row_keys": sum(1 for key, label in units.items() if " 等 " in label),
            "turret_rows": len(turret_rows),
            "turret_keys": len(turrets),
            "turret_links": sum(len(v) for v in pairs.values()),
            "paired_units": len(pairs),
        },
        "units": units,
        "turrets": turrets,
        "pairs": pairs,
    }


# --------------------------------------------------------------------------- #
# 缓存读写与失效
# --------------------------------------------------------------------------- #
def save_name_table(payload: Mapping[str, Any], out: str | Path) -> Path:
    """原子写缓存（先写 .tmp 再 replace —— 半截文件比没有更坏）。"""
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)
    return target


def load_name_table(path: str | Path) -> dict[str, Any] | None:
    """读缓存；文件不在 / 版本不对 / 坏了 -> `None`（⛔ 不抛 —— 调用方要能显示「没有名字表」）。"""
    target = Path(path)
    if not target.is_file():
        return None
    try:
        # ★ 读**之前**判大小（⛔ 不许先读再看）：给一个 7.12 GB 的容器路径时，
        #   「不是缓存」这件事一次 `stat` 就能回答，⛔ 不该先把整份读进内存。
        if target.stat().st_size > CACHE_MAX_BYTES:
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("format") != CACHE_FORMAT:
        return None
    if not isinstance(payload.get("units"), dict):
        return None
    return payload


def is_fresh(payload: Mapping[str, Any] | None, game_root: str | Path) -> bool:
    """缓存还新不新 —— 只比 `(size, mtime_ns)`（一次 stat，不读内容）。"""
    if not payload:
        return False
    recorded = payload.get("source") or {}
    if not isinstance(recorded, Mapping):
        return False
    try:
        now = fingerprint_of(source_file(game_root))
    except DataSourceError:
        return False
    return (recorded.get("size"), recorded.get("mtime_ns")) == (now["size"], now["mtime_ns"])


def display_name(payload: Mapping[str, Any] | None, model_key: str) -> str:
    """这个内部键在游戏里叫什么；没有就空串（⛔ 不编）。"""
    if not payload:
        return ""
    units = payload.get("units")
    if not isinstance(units, Mapping):
        return ""
    return str(units.get(str(model_key)) or "")
