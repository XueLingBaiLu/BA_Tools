r"""② 改 · 炮塔配对段：把「这个单位挂哪个炮塔」写进产品数据库。

## 这个模块回答什么

§52-2 的后半：① 拿 能把车体与炮塔**分别**导进 Blender、配对表也读得出来，
但**写回「这个单位挂哪一座」一直没有入口**。本模块补它：先量现状 -> 出计划（零写入）
-> 走产品数据库入口写回。读数全在 `_work/turret/`（每条都能复算）。

## 口径表（先量后写）

| 概念 | 住在哪 | 谁说了算 |
|---|---|---|
| 单位**实际挂的**炮塔 | `TurretUnits` ＋ `Options.Turret0..20Id` | `CardData.card_turrets` |
| **挂点**（可编辑对象） | `Options.Turret0..20Id`（21 列） | 用户 2026-10-01：只碰数据库挂点 |
| 名册（候选） | `TurretUnits(Id, UnitId, TurretId, Order)` | 只有 `IsDefault` 的进基础挂载 |
| 炮塔的地址键 | `Turrets.ModelFileName` | 与 ① 拿 的键同一把 |

`card_turrets` 的两步（**唯一出处** = `app/viewmodel/unit_card.py::card_turrets`）：

1. `TurretUnits` 里本单位那些行按 `(Order, Id)` 排，**只收 `Turrets.IsDefault == true`** 的；
2. 再用**当前选中那条选项**的 21 个挂点 `Options.Turret0..20Id` 逐位覆盖 ——
   第 k 位有值就替换那一格、超出就追加。⚠ 这一步**不看 IsDefault**。

3D 模型上的挂点**不碰**（用户 2026-10-01 澄清：数据库挂点才是编辑对象）。

## 实测（`_work/db121/BA_Mod.zip` = 1.2.1 现场 · 2026-10-15）

| 读数 | 数字 |
|---|---|
| `Units` / `Turrets` / `TurretUnits` 行数 | 542 / 1057 / **1600** |
| `TurretUnits` 里有行的单位 | **312**（其中 202 个有 `IsDefault` 炮塔） |
| `Turrets.ModelFileName` | 671 个取值 / **45 个撞车** |
| 样例 T-72B（`Units#9` · `RU_T72B`） | 名册 6 行 · 挂载 `(703,)` |

* 键撞车的例子：`RU_T72B/RU_T72B` 同时属于 Turrets#8 与 #703 ⇒ **写回一律用 `TurretId`**，
  ⛔ 不拿 `ModelFileName` 当唯一键（撞车时本模块抛错点名，请调用方给 `turret_id=`）。
* T-72B 的挂载为什么是 703：名册里唯一的 `IsDefault` 是 #8 `MainTurret T72B`，
  被选项 #1449 的 `Turret0Id=703` 顶掉 —— 这就是「挂点压过缺省名册」那条口径。

## 三个原子动作（AGENTS §0.10：**新增 / 删除 / 修改**；⛔ 没有「换」）

| 动词 | 写哪一格 | 入口 | 什么时候用 |
|---|---|---|---|
| **修改** | `Options.Turret{k}Id` | `plan(slot=k)` | 那个挂点已经被选项占着 |
| **新增** | `TurretUnits` 补一行 | `plan()` | 名册里没有这一座 |
| **删除** | `TurretUnits` 删一行 | `plan(remove=True)` | 摘掉这一座 |

「换一座」= **删掉旧的 ＋ 新增新的**（两步各自可撤），⛔ 本模块不提供「换」这个复合入口。

## 走的是哪条产品入口（⛔ 不自己写字节）

写盘只有一条链（与 `cli/__main__.py::_cmd_set`、`cli/row_delete.py::run` **同一套**）：

    core.session.open_source -> core.data.from_source.dataset_from_source
    -> core.edit.changeset.Changeset -> core.session.save_instance

反向 Δ 照 `row-delete-delta.json` 同一形状留一份 `<落点>.turret-pair-delta.json`，
`restore()` 是它唯一的还原入口（往返**逐字节**）。

⚠ **已知缺口（不是绕过产品）**：CLI 里**没有**「新增数据表整行」的入口（三原子动词里那一格
只在 GUI 的 `EditorContext.insert_row` 里）。`plan()` 的名册新增走的是它背后**同一个**
`Changeset.insert_row`（Id 由 `core/edit/clone.py::next_row_id` 取）—— CLI 那条补上后换成调它。

## 五道闸（与 `cli/row_delete.py` 逐条对齐）

1. **默认干跑**：`apply(..., write=False)` 一个字节都不写（连 Δ 都不落）；
2. **写前核那一版**：计划是照某一版算的，盘上变了就拒写。小文件核 sha256，
   大文件退到 `(字节数, mtime_ns)` —— ⛔ 不是「不核」；
3. **游戏在跑就拒写**，⚠ 只对**游戏目录里那一份**生效（影子根不受：那是探针自己的地盘）。
   尺子 = `core/assets/game.py::is_game_running`（与 row-delete 同一把）。
   ⚠ 边界：AGENTS §0.17 实测「从 Python 宿主起的子进程看不到用户桌面的进程」⇒
   这把尺子在这台机上**可能假绿**（产品级问题，不是本模块引入的）；
   真写游戏目录前请人工确认游戏已退出；
4. **写后读回**：重开落点按 `card_turrets` 逐条核（挂载列表 / 名册行数），对不上就抛错；
5. **解不出来一律拒**：单位 / 炮塔 / 挂点解不出来抛 `TurretEntryError`
   （= 产品 CLI 的 rc=2 等价物），⛔ 一个字节都不写（负控点名，见探针）。

## 判据一行

    _work/take/probe_turret_pair.py -> TURRET_VERDICT GREEN
    （干跑零写入 sha256+mtime 逐位不变 · 正控「改一格」挂点 703->8 且读回变了 ·
     正控「名册新增」挂载 1 条->2 条且名册 6 行->7 行 · 还原与装前 sha256 逐位相同 ·
     负控两条干净拒且零写入）

## 边界（明写）

* 只改**数据库**（`BA_Mod.zip` / `DataBaseCompiled.json` / `data.unity3d`），
  ⛔ 不碰 AssetBundle 里的模型；
* 名册里加一座 `IsDefault=false` 的炮塔**不会**改变游戏算出来的挂载（只进候选名单）——
  `plan.notes` 会明写这句，⛔ 不假装挂上了；
* `data.unity3d` 那条路**没有「写回它本身」的落点**（重写一次 7 GB / 约 4 分钟；
  产品口径 `opened_file_of` 对容器返回 None）⇒ 本模块要 `out=` 另存，或走产品 `import` 链；
* ⛔ 本模块**不 import bpy**：参数全是路径与整数、返回值全是读数行 ——
  子进程工人（`blender_addon/_cli_worker.py`）能按文件路径原样装载并跑它。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ACTION_ADD",
    "ACTION_REMOVE",
    "ACTION_SLOT",
    "DELTA_KIND",
    "DELTA_VERSION",
    "EXIT_UNUSABLE",
    "HEAD_MAX",
    "CellOp",
    "DeleteOp",
    "InsertOp",
    "MountLine",
    "TurretEntryError",
    "TurretPlan",
    "apply",
    "check",
    "delta_path",
    "effective_mounts",
    "find_turret",
    "find_unit",
    "fingerprint",
    "mounts_lines",
    "plan",
    "plan_lines",
    "restore",
    "roster_lines",
    "slot_columns",
    "source_of",
    "summary",
]

#: 摘要行上限（契约：第 1 行 <= 60 字；探针量它）。
HEAD_MAX = 60
#: 拒写的退出码 —— 与产品 CLI 的 EXIT_UNUSABLE 同一个数。插件这条路上没有退出码，
#: 等价物 = 抛 TurretEntryError（面板捕获后弹错误，一个字节都不写）。
EXIT_UNUSABLE = 2
#: 反向 Δ 的格式标识与版本（照 `cli/row_delete.py` 的 DELTA_KIND / DELTA_VERSION 形状）。
DELTA_KIND = "turret-pair-delta"
DELTA_VERSION = 1
#: 超过这个大小就不整文件 sha256（容器 7 GB 读一遍要几十秒）—— 退到 `(字节数, mtime_ns)`，
#: ⛔ 不是「不核」。
SHA_LIMIT = 96 * 1024 * 1024

UNIT_TABLE = "Units"
TURRET_TABLE = "Turrets"
LINK_TABLE = "TurretUnits"
OPTION_TABLE = "Options"

COLUMN_ID = "Id"
COLUMN_MODEL = "ModelFileName"
COLUMN_HUD = "HUDName"
COLUMN_NAME = "Name"
COLUMN_DEFAULT = "IsDefault"
COLUMN_UNIT = "UnitId"
COLUMN_TURRET = "TurretId"
COLUMN_ORDER = "Order"

ACTION_SLOT = "修改挂点"
ACTION_ADD = "新增名册行"
ACTION_REMOVE = "删除名册行"

#: 摘要里单位名 / 炮塔名的截断长度（中文一个字算一个字符）。
SHORT_NAME = 18


class TurretEntryError(ValueError):
    """这条路上**不许猜**的东西（单位 / 炮塔 / 挂点解不出来，或盘上已经不是规划那一版）。

    与产品 CLI 的 rc=2 等价（插件这条路上没有退出码，等价物 = 抛它；⛔ 一个字节都不写）。
    """

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = str(hint or "")

    def lines(self) -> list[str]:
        """两行读数（面板照 `★ 类型: 原文` 画，与网格段同一形状）。"""
        out = [f"★ TurretEntryError: {self}"]
        if self.hint:
            out.append("  " + self.hint)
        return out


# --------------------------------------------------------------------------- #
# 小工具（⛔ 不碰 bpy / 不碰盘上的写）
# --------------------------------------------------------------------------- #
def source_of(game_root: str | Path) -> Path:
    """名字表 / 数据库的**唯一来源**：`<游戏根>/BrokenArrow_Data/data.unity3d`。

    口径与 `core/asset/unit_names.py::source_file` 是**同一个函数**（这里只转发，⛔ 不另写）。
    """
    from ba_mod_maker.core.asset import unit_names

    return unit_names.source_file(game_root)


def slot_columns() -> tuple[str, ...]:
    """21 个挂点列名 —— ⛔ 唯一出处 `core/edit/refs.py::TURRET_SLOT_COLUMNS`。"""
    from ba_mod_maker.core.edit.refs import TURRET_SLOT_COLUMNS

    return tuple(str(name) for name in TURRET_SLOT_COLUMNS)


def delta_path(target: str | Path) -> Path:
    """反向 Δ 的落点（照 `row-delete` 的 `<落点>.<kind>.json` 形状）。"""
    return Path(f"{target}.{DELTA_KIND}.json")


def _as_int(value: Any, default: int = 0) -> int:
    """数值读取：bool 不算数、数字字符串认（表里什么都可能有）。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256(path: str | Path) -> str:
    """整文件 sha256（按块读 —— 大文件也不吃内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _stamp(path: str | Path) -> tuple[int, int]:
    """`(字节数, mtime_ns)` —— 干跑「零写入」那条判据的第二把尺子。"""
    info = Path(path).stat()
    return (int(info.st_size), int(info.st_mtime_ns))


def fingerprint(path: str | Path | None) -> dict[str, Any]:
    """某一版落点的指纹：小文件 sha256，大文件退到 `(size, mtime_ns)`。

    ⛔ `path is None`（容器那种没有写回落点的来源）也是一条**读数**，不是异常。
    """
    if path is None:
        return {"path": "", "size": 0, "mtime_ns": 0, "sha256": "", "note": "没有落点"}
    target = Path(path)
    size, mtime_ns = _stamp(target)
    sha = _sha256(target) if size <= SHA_LIMIT else ""
    note = "" if sha else f"超过 {SHA_LIMIT} B：只核 (字节数, mtime_ns)"
    return {
        "path": str(target),
        "size": size,
        "mtime_ns": mtime_ns,
        "sha256": sha,
        "note": note,
    }


def _same_version(before: Mapping[str, Any], now: Mapping[str, Any]) -> bool:
    """两版指纹是不是同一版（sha256 有就核它，没有就核 size + mtime_ns）。"""
    if not before or not now:
        return False
    if _text(before.get("sha256")):
        return _text(before.get("sha256")) == _text(now.get("sha256"))
    old = (_as_int(before.get("size")), _as_int(before.get("mtime_ns")))
    new = (_as_int(now.get("size")), _as_int(now.get("mtime_ns")))
    return old == new


def _clip(line: str, limit: int = HEAD_MAX) -> str:
    text = str(line).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(name: Any, limit: int = SHORT_NAME) -> str:
    text = _text(name)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _near(wanted: str, values: Iterable[str], limit: int = 4) -> str:
    """最接近的几个候选（给「解不出来」那句 hint 用；⛔ 不自动挑一个）。"""
    pool = [str(value) for value in values]
    hits = difflib.get_close_matches(str(wanted), pool, n=limit, cutoff=0.4)
    return "、".join(hits) if hits else "（一个都不像）"


# --------------------------------------------------------------------------- #
# 原子动作（AGENTS §0.10 三动词：新增 / 删除 / 修改 —— ⛔ 没有「换」）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CellOp:
    """**修改**一格：`table[row_id].column` 由 `old` 改成 `new`。"""

    table: str
    row_id: int
    column: str
    old: Any
    new: Any

    def as_json(self) -> dict[str, Any]:
        return {
            "op": "cell",
            "table": self.table,
            "row_id": int(self.row_id),
            "column": self.column,
            "old": self.old,
            "new": self.new,
        }


@dataclass(frozen=True, slots=True)
class InsertOp:
    """**新增**一行（`index=None` = 追加到末尾 —— 逐字节还原的前提）。"""

    table: str
    row_id: int
    values: Mapping[str, Any]
    index: int | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "op": "insert",
            "table": self.table,
            "row_id": int(self.row_id),
            "index": self.index,
            "values": dict(self.values),
        }


@dataclass(frozen=True, slots=True)
class DeleteOp:
    """**删除**一行（`index` = 它在表数组里的**原下标**，还原要按它插回去）。"""

    table: str
    row_id: int
    index: int
    values: Mapping[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {
            "op": "delete",
            "table": self.table,
            "row_id": int(self.row_id),
            "index": int(self.index),
            "values": dict(self.values),
        }


Op = CellOp | InsertOp | DeleteOp


def _op_from_json(item: Mapping[str, Any]) -> Op:
    """反向 Δ 里的一条 -> 动作对象。⛔ 认不出的形状直接抛（不许静默跳过一条）。"""
    kind = _text(item.get("op"))
    table = _text(item.get("table"))
    row_id = _as_int(item.get("row_id"), -1)
    if not kind or not table or row_id < 0:
        raise TurretEntryError(f"反向 Δ 里有认不出的动作：{dict(item)!r}")
    if kind == "cell":
        column = _text(item.get("column"))
        return CellOp(table, row_id, column, item.get("old"), item.get("new"))
    if kind == "insert":
        index = item.get("index")
        values = dict(item.get("values") or {})
        return InsertOp(table, row_id, values, None if index is None else _as_int(index))
    if kind == "delete":
        values = dict(item.get("values") or {})
        return DeleteOp(table, row_id, _as_int(item.get("index")), values)
    raise TurretEntryError(f"反向 Δ 里有认不出的动作类型：{kind!r}")


@dataclass(frozen=True, slots=True)
class MountLine:
    """挂载列表里的一条（游戏口径；`slot` = 它在列表里的位置）。"""

    #: 在挂载列表里的位置（0 起）—— 与 `CardData.card_turrets` 的下标逐位对齐。
    slot: int
    turret_id: int
    name: str
    #: `Turrets.ModelFileName`（地址键；⚠ 实测 45 个取值撞车，⛔ 别拿它当唯一键）。
    key: str
    is_default: bool
    #: 这一条是**名册**（`TurretUnits` 的缺省行）还是**挂点**（选项槽覆盖）来的。
    source: str
    #: 人话挂点：`名册 TurretUnits#755（Order=0）` / `选项 #1449「…」的 Turret0Id`。
    hardpoint: str

    def line(self, indent: str = "  ") -> str:
        head = f"{indent}{int(self.slot)} · {self.name or '（无名）'}"
        tail = f"（Turrets#{int(self.turret_id)} · 键 {self.key or '（没有模型键）'}"
        tail += f" · {self.hardpoint}）"
        return head + tail


@dataclass(frozen=True, slots=True)
class TurretPlan:
    """一次「指定挂哪个炮塔」的计划（**零写入**的产物，可撤的凭据也在它身上）。"""

    action: str
    source: str
    unit_id: int
    unit_key: str
    unit_hud: str
    turret_id: int
    turret_key: str
    turret_name: str
    turret_is_default: bool
    ops: tuple[Op, ...]
    mounts_before: tuple[int, ...]
    mounts_after: tuple[int, ...]
    links_before: int
    links_after: int
    fingerprint_before: Mapping[str, Any] = field(default_factory=dict)
    slot: int | None = None
    option_id: int | None = None
    order: int | None = None
    notes: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        """这个计划到底改没改东西（挂载列表或名册行数有一处变了就算改）。"""
        return (self.mounts_before != self.mounts_after) or (self.links_before != self.links_after)


# --------------------------------------------------------------------------- #
# 打开数据源 / 读表（⛔ 一条路：产品内核那套）
# --------------------------------------------------------------------------- #
@contextmanager
def _open(source: str | Path):
    """打开数据源 -> `(instance, 打开的那个文件或 None)`。

    ⛔ 一条路：`core.session.open_source` ＋ `core.data.from_source.dataset_from_source`
    （与 `cli/__main__.py::_cmd_set` / `cli/row_delete.py::run` 同一套）。
    """
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source, opened_file_of

    opened = session.open_source(source)
    with opened as src:
        dataset = dataset_from_source(src)
        instance = dataset.instance(dataset.default_instance_id)
        file = opened_file_of(src)
        yield instance, (Path(file.path) if file is not None else None)


def _rows(instance: Any, table: str) -> list[dict[str, Any]]:
    """一张表 -> `[{列: 值}]`（走产品内核读侧，⛔ 不自造解析）。"""
    if table not in instance.table_names:
        return []
    slot = instance.table(table)
    return [dict(zip(slot.columns, row, strict=False)) for row in slot.rows]


def _row_index(instance: Any, table: str, row_id: int) -> int:
    """某一行在**表数组里的物理下标**（删除 / 还原逐字节都靠它）。"""
    slot = instance.table(table)
    column = slot.columns.index(COLUMN_ID)
    for position, row in enumerate(slot.rows):
        if _as_int(row[column], -1) == int(row_id):
            return position
    raise TurretEntryError(f"{table} 里找不到 {COLUMN_ID}={row_id} 那一行")


def _card_data(instance: Any):
    """产品的卡片口径（`CardData`）—— `card_turrets` / `selected_options` 的唯一出处。"""
    from ba_mod_maker.app.viewmodel.unit_card import CardData

    return CardData(instance)


def _changeset(instance: Any):
    """产品内核的变更集（写路径唯一入口，⛔ 本模块不自己改字节）。"""
    from ba_mod_maker.core.edit.changeset import Changeset

    return Changeset(instance)


def _unit_options(options: Any, hit: Sequence[Any]) -> str:
    """一串选项行 -> `#1、#2` 那种点名（读数行用）。"""
    return "、".join(f"#{_as_int(options.i(row, COLUMN_ID))}" for row in hit)


def find_unit(
    instance: Any, *, unit: str | None = None, unit_id: int | None = None
) -> dict[str, Any]:
    """单位 -> 它在 `Units` 里的那一行。

    `unit` = `Units.ModelFileName` 的**原样值**（与产品 CLI `newasset --unit` 同口径，
    ⛔ 不是显示名）；同名多行时**抛错点名**，请调用方给 `unit_id=`。
    """
    rows = _rows(instance, UNIT_TABLE)
    if unit_id is not None:
        hit = [row for row in rows if _as_int(row.get(COLUMN_ID)) == int(unit_id)]
        if not hit:
            raise TurretEntryError(
                f"`{UNIT_TABLE}` 里没有 {COLUMN_ID}={unit_id} 这一行",
                hint=f"`{UNIT_TABLE}` 共 {len(rows)} 行",
            )
        return hit[0]
    key = _text(unit)
    if not key:
        raise TurretEntryError("没给单位 —— 要 `unit=`（ModelFileName 原样值）或 `unit_id=`")
    hit = [row for row in rows if _text(row.get(COLUMN_MODEL)) == key]
    if not hit:
        near = _near(key, (_text(row.get(COLUMN_MODEL)) for row in rows))
        raise TurretEntryError(
            f"没有模型键是 {key!r} 的单位",
            hint=f"最接近的：{near}（键 = Units.ModelFileName 的原样值）",
        )
    if len(hit) > 1:
        names = "、".join(
            f"#{_as_int(row.get(COLUMN_ID))}（{_text(row.get(COLUMN_HUD))}）" for row in hit[:6]
        )
        raise TurretEntryError(
            f"{key!r} 对应 {len(hit)} 个单位",
            hint=f"用 unit_id= 指定是哪一个：{names}",
        )
    return hit[0]


def find_turret(
    instance: Any, *, turret: str | None = None, turret_id: int | None = None
) -> dict[str, Any]:
    """炮塔 -> 它在 `Turrets` 里的那一行。

    ⚠ **键不唯一**：实测 `Turrets.ModelFileName` 671 个取值里 **45 个撞车**
    （例 `RU_T72B/RU_T72B` 同时属于 Turrets#8 与 #703）⇒ 撞车时**抛错点名**，
    请调用方给 `turret_id=`（① 拿 的配对行本来就带 `turret_id`）。⛔ 不自动挑一个。
    """
    rows = _rows(instance, TURRET_TABLE)
    if turret_id is not None:
        hit = [row for row in rows if _as_int(row.get(COLUMN_ID)) == int(turret_id)]
        if not hit:
            raise TurretEntryError(f"`{TURRET_TABLE}` 里没有 {COLUMN_ID}={turret_id} 这一行")
        return hit[0]
    key = _text(turret)
    if not key:
        raise TurretEntryError("没给炮塔 —— 要 `turret=`（键或名字）或 `turret_id=`")
    hit = [row for row in rows if _text(row.get(COLUMN_MODEL)) == key]
    if not hit:
        hit = [row for row in rows if _text(row.get(COLUMN_NAME)) == key]
    if not hit:
        near_key = _near(key, (_text(row.get(COLUMN_MODEL)) for row in rows))
        near_name = _near(key, (_text(row.get(COLUMN_NAME)) for row in rows))
        raise TurretEntryError(
            f"没有键 / 名字是 {key!r} 的炮塔",
            hint=f"最接近的键：{near_key}；最接近的名字：{near_name}",
        )
    if len(hit) > 1:
        names = "、".join(
            f"#{_as_int(row.get(COLUMN_ID))}（{_text(row.get(COLUMN_NAME))}）" for row in hit[:6]
        )
        raise TurretEntryError(
            f"{key!r} 对应 {len(hit)} 座炮塔（键会撞车）",
            hint=f"用 turret_id= 指定：{names}",
        )
    return hit[0]


def _unit_links(instance: Any, unit_id: int) -> list[dict[str, Any]]:
    """本单位在 `TurretUnits` 里的那些行，按游戏口径 `(Order, Id)` 排。"""
    every = _rows(instance, LINK_TABLE)
    links = [row for row in every if _as_int(row.get(COLUMN_UNIT)) == int(unit_id)]
    links.sort(key=lambda row: (_as_int(row.get(COLUMN_ORDER)), _as_int(row.get(COLUMN_ID))))
    return links


def _annotated_mounts(instance: Any, data: Any, unit_id: int) -> list[MountLine]:
    """逐条带挂点的挂载列表（口径与 `CardData.card_turrets` 逐字对齐）。"""
    turrets = {_as_int(row.get(COLUMN_ID)): row for row in _rows(instance, TURRET_TABLE)}
    out: list[MountLine] = []
    for row in _unit_links(instance, unit_id):
        turret = turrets.get(_as_int(row.get(COLUMN_TURRET)))
        if turret is None or not bool(turret.get(COLUMN_DEFAULT)):
            continue
        hardpoint = f"名册 TurretUnits#{_as_int(row.get(COLUMN_ID))}"
        hardpoint += f"（Order={_as_int(row.get(COLUMN_ORDER))}）"
        out.append(
            MountLine(
                slot=len(out),
                turret_id=_as_int(turret.get(COLUMN_ID)),
                name=_text(turret.get(COLUMN_NAME)),
                key=_text(turret.get(COLUMN_MODEL)),
                is_default=True,
                source="名册",
                hardpoint=hardpoint,
            )
        )
    options = data.t(OPTION_TABLE)
    selected = list(data.selected_options(int(unit_id)))
    for slot, column in enumerate(slot_columns()):
        picked = None
        for option in selected:
            if _as_int(options.i(option, column)) > 0:
                picked = option
                break
        if picked is None:
            continue
        turret_id = _as_int(options.i(picked, column))
        turret = turrets.get(turret_id)
        name = _text(turret.get(COLUMN_NAME)) if turret else f"（Turrets 里没有 #{turret_id}）"
        key = _text(turret.get(COLUMN_MODEL)) if turret else ""
        is_default = bool(turret.get(COLUMN_DEFAULT)) if turret else False
        option_id = _as_int(options.i(picked, COLUMN_ID))
        option_name = _text(options.s(picked, COLUMN_NAME))
        line = MountLine(
            slot=slot if slot < len(out) else len(out),
            turret_id=turret_id,
            name=name,
            key=key,
            is_default=is_default,
            source="挂点",
            hardpoint=f"选项 #{option_id}「{option_name}」的 {column}",
        )
        if line.slot < len(out):
            out[line.slot] = line
        else:
            out.append(line)
    return out


def roster(instance: Any, unit_id: int) -> list[dict[str, Any]]:
    """本单位在 `TurretUnits` 里的**全部**行（名册；含 `IsDefault=False` 的那些）＋ 炮塔行。

    ⚠ 与 `effective_mounts` **不是一件事**（2026-10-18 实测，别再混）：

    * `effective_mounts` = **游戏口径的挂载列表**（与 `CardData.card_turrets` 逐位对拍过），
      只有 `Turrets.IsDefault` 的行才上名册，其余被选项槽覆盖 ⇒ T-72B 只有 **1** 条；
    * 本函数 = **名册**（`TurretUnits` 的 6 行）⇒ T-72B 实测 **6 行**，其中
      **3 行的 `Turrets.ModelFileName` 是 None**（那 3 个 cupola 炮塔根本不加载模型，Q5 已闭合）。

    研究文档里那句「6 条配对里 3 条 `ModelFileName=None`」说的就是**这张名册**。
    """
    from ba_mod_maker.core.edit.refs import TURRET_SLOT_COLUMNS

    _ = TURRET_SLOT_COLUMNS  # 口径同一处（选项槽列名），本函数不需要它，但别另立一套
    turrets = {_as_int(row.get(COLUMN_ID)): row for row in _rows(instance, TURRET_TABLE)}
    out: list[dict[str, Any]] = []
    for link in _unit_links(instance, unit_id):
        turret_id = _as_int(link.get(COLUMN_TURRET))
        row = turrets.get(turret_id) or {}
        out.append(
            {
                "link_id": _as_int(link.get(COLUMN_ID)),
                "order": _as_int(link.get(COLUMN_ORDER)),
                "turret_id": turret_id,
                "name": _text(row.get(COLUMN_NAME)),
                "model_filename": _text(row.get(COLUMN_MODEL)),
                "is_default": bool(row.get(COLUMN_DEFAULT)) if row else False,
            }
        )
    return out


def effective_mounts(instance: Any, unit_id: int) -> list[MountLine]:
    """本单位**游戏口径**的挂载列表（逐条带挂点）。

    ⛔ 算两遍对拍：`CardData.card_turrets`（产品唯一出处）＋ 本模块的逐条注释版，
    两者**逐位必须相同** —— 不同就抛错（口径漂了当场红，⛔ 不静默用自己那份）。
    """
    data = _card_data(instance)
    truth = tuple(_as_int(value) for value in data.card_turrets(int(unit_id)))
    mine = _annotated_mounts(instance, data, int(unit_id))
    ids = tuple(line.turret_id for line in mine)
    if ids != truth:
        raise TurretEntryError(
            "本模块算的挂载与产品口径 `CardData.card_turrets` 对不上",
            hint=f"我们 {ids} vs 产品 {truth} —— 口径漂了，先读 unit_card.py::card_turrets",
        )
    return mine


# --------------------------------------------------------------------------- #
# 计划（**零写入**）：三动词各一条路
# --------------------------------------------------------------------------- #
def _apply_ops(changeset: Any, ops: Sequence[Op]) -> None:
    """把动作落到**内存里的**变更集上（⛔ 一个字节都不写盘）。"""
    for op in ops:
        if isinstance(op, CellOp):
            changeset.set_value(op.table, op.row_id, op.column, op.new)
        elif isinstance(op, InsertOp):
            changeset.insert_row(op.table, dict(op.values), index=op.index)
        else:
            changeset.delete_row(op.table, op.row_id)


def _simulate(instance: Any, ops: Sequence[Op], unit_id: int) -> tuple[tuple[int, ...], int]:
    """把 ops 在**内存里**试算一遍 -> `(游戏口径挂载列表, 本单位名册行数)`。

    ⚠ 它改的是**内存里那份实例**（`Changeset` 就落在表的行上）；
    调用方拿去写盘之前，⛔ 必须自己确认要写。
    """
    _apply_ops(_changeset(instance), ops)
    mounts = tuple(line.turret_id for line in effective_mounts(instance, unit_id))
    return mounts, len(_unit_links(instance, unit_id))


def _invert(instance: Any, ops: Sequence[Op]) -> tuple[Op, ...]:
    """反向 Δ：改一格 -> 写回老值；插一行 -> 删它；删一行 -> 按原下标原值插回去。"""
    out: list[Op] = []
    for op in reversed(list(ops)):
        if isinstance(op, CellOp):
            out.append(CellOp(op.table, op.row_id, op.column, op.new, op.old))
        elif isinstance(op, InsertOp):
            try:
                index = _row_index(instance, op.table, op.row_id)
            except TurretEntryError:
                index = -1  # 删行用不到下标；-1 = 「不知道」（⛔ 不编一个）
            out.append(DeleteOp(op.table, op.row_id, index, dict(op.values)))
        else:
            out.append(InsertOp(op.table, op.row_id, dict(op.values), op.index))
    return tuple(out)


def _slot_ops(
    data: Any, unit_id: int, turret_id: int, slot: int, *, option_id: int | None = None
) -> tuple[Op, ...]:
    """**修改**：把本单位**当前选中那条选项**的第 `slot` 个挂点写成 `turret_id`。"""
    columns = slot_columns()
    if not 0 <= int(slot) < len(columns):
        raise TurretEntryError(
            f"挂点 {slot} 越界：只有 0..{len(columns) - 1} 这 {len(columns)} 个挂点",
            hint="列名 = core/edit/refs.py::TURRET_SLOT_COLUMNS（Options.Turret0..20Id）",
        )
    column = columns[int(slot)]
    options = data.t(OPTION_TABLE)
    selected = list(data.selected_options(int(unit_id)))
    if not selected:
        raise TurretEntryError(
            "本单位一条改装选项都没有 —— 没有可写的挂点",
            hint="走名册那条路：plan() 不带 slot",
        )
    picked = None
    if option_id is not None:
        picked = next(
            (row for row in selected if _as_int(options.i(row, COLUMN_ID)) == int(option_id)),
            None,
        )
        if picked is None:
            raise TurretEntryError(
                f"选项 #{option_id} 不是本单位当前选中的那一条",
                hint=f"当前选中的是：{_unit_options(options, selected)}",
            )
    else:
        for option in selected:
            if _as_int(options.i(option, column)) > 0:
                picked = option
                break
        if picked is None:
            raise TurretEntryError(
                f"第 {int(slot)} 个挂点（{column}）现在没有选项占着 —— 写进去不会生效",
                hint="要挂新东西走名册那条路（不带 slot）；确实要写某一格就给 option_id=",
            )
    old = options.i(picked, column)
    if _as_int(old) == int(turret_id):
        raise TurretEntryError(
            f"{column} 已经是这一座了（没有变化）",
            hint="换一座 = 先 plan(remove=True) 摘掉、再 plan() 挂新的（两步各自可撤）",
        )
    picked_id = _as_int(options.i(picked, COLUMN_ID))
    return (CellOp(OPTION_TABLE, picked_id, column, old, int(turret_id)),)


def _add_ops(
    instance: Any, changeset: Any, unit_id: int, turret_id: int, *, order: int | None
) -> tuple[Op, ...]:
    """**新增**：给本单位在 `TurretUnits` 补一行（Id 取产品内核的 `next_row_id`）。"""
    from ba_mod_maker.core.edit.clone import next_row_id

    links = _unit_links(instance, unit_id)
    same = [row for row in links if _as_int(row.get(COLUMN_TURRET)) == int(turret_id)]
    if same:
        raise TurretEntryError(
            f"本单位名册里已经有这一座（TurretUnits#{_as_int(same[0].get(COLUMN_ID))}）",
            hint="要改挂载用 plan(slot=k)；要重挂先 plan(remove=True) 摘掉",
        )
    new_id = int(next_row_id(changeset.table(LINK_TABLE), changeset))
    if order is None:
        order = max((_as_int(row.get(COLUMN_ORDER)) for row in links), default=0)
    values = {
        COLUMN_ID: new_id,
        COLUMN_UNIT: int(unit_id),
        COLUMN_TURRET: int(turret_id),
        COLUMN_ORDER: int(order),
    }
    return (InsertOp(LINK_TABLE, new_id, values, None),)


def _remove_ops(instance: Any, unit_id: int, turret_id: int) -> tuple[Op, ...]:
    """**删除**：摘掉本单位名册里指着这一座的那一行。"""
    links = _unit_links(instance, unit_id)
    hit = [row for row in links if _as_int(row.get(COLUMN_TURRET)) == int(turret_id)]
    if not hit:
        pointers = "、".join(f"Turrets#{_as_int(row.get(COLUMN_TURRET))}" for row in links)
        raise TurretEntryError(
            "本单位名册里没有这一座 —— 没得删",
            hint=f"本单位名册指向：{pointers}",
        )
    row = hit[0]
    row_id = _as_int(row.get(COLUMN_ID))
    return (DeleteOp(LINK_TABLE, row_id, _row_index(instance, LINK_TABLE, row_id), dict(row)),)


def plan(
    source: str | Path,
    unit: str | None = None,
    turret: str | None = None,
    *,
    unit_id: int | None = None,
    turret_id: int | None = None,
    slot: int | None = None,
    remove: bool = False,
    order: int | None = None,
    option_id: int | None = None,
) -> TurretPlan:
    """出计划（**零写入**）。动作由参数决定，⛔ 不猜：

    | 给的参数 | 动作 | 写哪一格 |
    |---|---|---|
    | `slot=k` | 修改 | `Options.Turret{k}Id`（本单位当前选中的那条选项） |
    | `remove=True` | 删除 | `TurretUnits` 本单位那一行 |
    | 都不给 | 新增 | `TurretUnits` 补一行 |

    `order=` 只在新增时用（缺省 = 本单位现有最大 Order，排队尾）；
    `option_id=` 只在 `slot=k` 时用（缺省 = 当前占着这个挂点的那条选中选项）。
    """
    path = Path(source)
    with _open(path) as (instance, opened_path):
        unit_row = find_unit(instance, unit=unit, unit_id=unit_id)
        uid = _as_int(unit_row.get(COLUMN_ID))
        turret_row = find_turret(instance, turret=turret, turret_id=turret_id)
        tid = _as_int(turret_row.get(COLUMN_ID))
        before = tuple(line.turret_id for line in effective_mounts(instance, uid))
        links_before = len(_unit_links(instance, uid))
        changeset = _changeset(instance)
        data = _card_data(instance)
        chosen_slot: int | None = None
        chosen_option: int | None = None
        if slot is not None:
            action = ACTION_SLOT
            ops = _slot_ops(data, uid, tid, int(slot), option_id=option_id)
            chosen_slot = int(slot)
            chosen_option = ops[0].row_id
        elif remove:
            action = ACTION_REMOVE
            ops = _remove_ops(instance, uid, tid)
        else:
            action = ACTION_ADD
            ops = _add_ops(instance, changeset, uid, tid, order=order)
        chosen_order = 0
        for op in ops:
            if isinstance(op, InsertOp):
                chosen_order = _as_int(op.values.get(COLUMN_ORDER))
        notes: list[str] = []
        if action == ACTION_ADD and not bool(turret_row.get(COLUMN_DEFAULT)):
            notes.append(
                "这一座 IsDefault=false ⇒ 名册加它**不会**改变游戏算出来的挂载"
                "（只进候选名单）—— 规则在 CardData.card_turrets"
            )
        mounts_after, links_after = _simulate(instance, ops, uid)
        if mounts_after == before and links_after == links_before:
            notes.append("这个计划**什么都没改**（挂载与名册逐条相同）—— 先确认挑对了炮塔")
        return TurretPlan(
            action=action,
            source=str(path),
            unit_id=uid,
            unit_key=_text(unit_row.get(COLUMN_MODEL)),
            unit_hud=_text(unit_row.get(COLUMN_HUD)),
            turret_id=tid,
            turret_key=_text(turret_row.get(COLUMN_MODEL)),
            turret_name=_text(turret_row.get(COLUMN_NAME)),
            turret_is_default=bool(turret_row.get(COLUMN_DEFAULT)),
            ops=ops,
            mounts_before=before,
            mounts_after=mounts_after,
            links_before=links_before,
            links_after=links_after,
            fingerprint_before=fingerprint(opened_path),
            slot=chosen_slot,
            option_id=chosen_option,
            order=chosen_order if action == ACTION_ADD else None,
            notes=tuple(notes),
        )


# --------------------------------------------------------------------------- #
# 读数行（面板 / 子进程工人只认这个形状：第 1 行 <= 60 字摘要）
# --------------------------------------------------------------------------- #
def _ids(values: Sequence[int]) -> str:
    return "[" + "、".join(str(int(value)) for value in values) + "]"


def _fp_text(item: Mapping[str, Any]) -> str:
    """指纹 -> 一句人话（读数行用）。"""
    if not item:
        return "（没有指纹）"
    sha = _text(item.get("sha256"))
    if sha:
        return f"sha256 {sha[:16]}…（{_as_int(item.get('size'))} B）"
    return f"size={_as_int(item.get('size'))} mtime_ns={_as_int(item.get('mtime_ns'))}"


def _op_line(op: Op) -> str:
    if isinstance(op, CellOp):
        return f"修改 {op.table}#{op.row_id}.{op.column}：{op.old!r} -> {op.new!r}"
    values = "，".join(f"{key}={value!r}" for key, value in op.values.items())
    if isinstance(op, InsertOp):
        return f"新增 {op.table}#{op.row_id}：{values}"
    return f"删除 {op.table}#{op.row_id}（原下标 {op.index}）：{values}"


def summary(designed: TurretPlan) -> str:
    """一句人话（<= 60 字）—— 面板只画这一行。"""
    if not isinstance(designed, TurretPlan):
        return _clip(str(designed))
    unit = _short(designed.unit_hud or designed.unit_key or f"Units#{designed.unit_id}")
    turret = _short(designed.turret_name or designed.turret_key or f"Turrets#{designed.turret_id}")
    if designed.action == ACTION_SLOT:
        head = f"{unit}：挂点 {designed.slot} 改成 {turret}"
    elif designed.action == ACTION_ADD:
        head = f"{unit}：名册加一座 {turret}"
    else:
        head = f"{unit}：摘掉 {turret}"
    tail = f"（挂载 {len(designed.mounts_before)}->{len(designed.mounts_after)} 条）"
    return _clip(head + tail if len(head) + len(tail) <= HEAD_MAX else head)


def plan_lines(designed: TurretPlan) -> list[str]:
    """计划的读数行（第 1 行是摘要）。"""
    unit = designed.unit_hud or "（没有 HUDName）"
    key = designed.unit_key or "（没有键）"
    turret_key = designed.turret_key or "（没有模型键）"
    turret = designed.turret_name or "（无名）"
    lines = [summary(designed)]
    lines.append(f"源      : {designed.source}")
    lines.append(f"单位    : {unit}（Units#{designed.unit_id} · 键 {key}）")
    turret_line = f"炮塔    : {turret}（Turrets#{designed.turret_id} · 键 {turret_key}"
    turret_line += f" · IsDefault={designed.turret_is_default}）"
    lines.append(turret_line)
    lines.append(f"动作    : {designed.action}")
    for op in designed.ops:
        lines.append("  " + _op_line(op))
    before = f"{len(designed.mounts_before)} 条 {_ids(designed.mounts_before)}"
    after = f"{len(designed.mounts_after)} 条 {_ids(designed.mounts_after)}"
    lines.append(f"挂载    : {before} -> {after}")
    links = f"TurretUnits 本单位 {designed.links_before} 行 -> {designed.links_after} 行"
    lines.append(f"名册    : {links}")
    lines.append(f"盘上    : {_fp_text(designed.fingerprint_before)}")
    for note in designed.notes:
        lines.append("⚠ " + note)
    return lines


def check(
    source: str | Path,
    unit: str | None = None,
    turret: str | None = None,
    *,
    unit_id: int | None = None,
    turret_id: int | None = None,
) -> list[str]:
    """只读核对：本单位现在挂什么 / 名册几行 / 要挑的那一座在哪（⛔ 一个字节都不写）。"""
    with _open(Path(source)) as (instance, opened_path):
        unit_row = find_unit(instance, unit=unit, unit_id=unit_id)
        uid = _as_int(unit_row.get(COLUMN_ID))
        mounts = effective_mounts(instance, uid)
        links = _unit_links(instance, uid)
        hud = _short(_text(unit_row.get(COLUMN_HUD)) or _text(unit_row.get(COLUMN_MODEL)))
        head = f"{hud or '（无名）'}（Units#{uid}）：挂 {len(mounts)} 条 · 名册 {len(links)} 行"
        lines = [_clip(head)]
        where = opened_path if opened_path is not None else source
        lines.append(f"源      : {where}")
        lines.append(f"单位键  : {_text(unit_row.get(COLUMN_MODEL)) or '（没有）'}")
        if mounts:
            lines.extend(line.line() for line in mounts)
        else:
            lines.append("挂载    : （0 条 —— 名册里没有 IsDefault 的炮塔，也没有选项槽覆盖）")
        if turret is not None or turret_id is not None:
            row = find_turret(instance, turret=turret, turret_id=turret_id)
            tid = _as_int(row.get(COLUMN_ID))
            is_default = bool(row.get(COLUMN_DEFAULT))
            name = _text(row.get(COLUMN_NAME)) or "（无名）"
            key = _text(row.get(COLUMN_MODEL)) or "（没有模型键）"
            lines.append(f"挑的炮塔: {name}（Turrets#{tid} · 键 {key} · IsDefault={is_default}）")
            mounted = [line for line in mounts if line.turret_id == tid]
            in_roster = [item for item in links if _as_int(item.get(COLUMN_TURRET)) == tid]
            if mounted:
                where_line = f"第 {mounted[0].slot} 条 · {mounted[0].hardpoint}"
                lines.append(f"          ✔ 现在挂着（{where_line}）")
            elif in_roster:
                why = "它被选项的挂点顶掉了（看上面每条的挂点那一段）"
                if not is_default:
                    why = "它 IsDefault=false（游戏只把 IsDefault 的算进基础挂载）"
                rid = _as_int(in_roster[0].get(COLUMN_ID))
                lines.append(f"          名册里有它（TurretUnits#{rid}）但没进挂载列表 —— {why}")
            else:
                lines.append("          名册里没有它 ⇒ 走「新增」那条路（plan() 不带 slot）")
        return lines


def mounts_lines(
    source: str | Path, *, unit: str | None = None, unit_id: int | None = None
) -> list[str]:
    """本单位挂载列表的读数行（第 1 行是摘要）—— 面板「读」那一格照它画。"""
    with _open(Path(source)) as (instance, opened_path):
        unit_row = find_unit(instance, unit=unit, unit_id=unit_id)
        uid = _as_int(unit_row.get(COLUMN_ID))
        mounts = effective_mounts(instance, uid)
        hud = _short(_text(unit_row.get(COLUMN_HUD)) or _text(unit_row.get(COLUMN_MODEL)))
        where = opened_path if opened_path is not None else source
        lines = [_clip(f"{hud or '（无名）'}（Units#{uid}）：挂 {len(mounts)} 条")]
        lines.append(f"源      : {where}")
        lines.extend(line.line() for line in mounts)
        return lines


def roster_lines(
    source: str | Path, *, unit: str | None = None, unit_id: int | None = None
) -> list[str]:
    """本单位名册（`TurretUnits` 那些行）的读数行 —— 含**没进挂载**的候选。"""
    with _open(Path(source)) as (instance, opened_path):
        unit_row = find_unit(instance, unit=unit, unit_id=unit_id)
        uid = _as_int(unit_row.get(COLUMN_ID))
        links = _unit_links(instance, uid)
        turrets = {_as_int(row.get(COLUMN_ID)): row for row in _rows(instance, TURRET_TABLE)}
        hud = _short(_text(unit_row.get(COLUMN_HUD)) or _text(unit_row.get(COLUMN_MODEL)))
        where = opened_path if opened_path is not None else source
        lines = [_clip(f"{hud or '（无名）'}（Units#{uid}）：名册 {len(links)} 行")]
        lines.append(f"源      : {where}")
        for row in links:
            tid = _as_int(row.get(COLUMN_TURRET))
            turret = turrets.get(tid)
            name = _text(turret.get(COLUMN_NAME)) if turret else f"（Turrets 里没有 #{tid}）"
            key = _text(turret.get(COLUMN_MODEL)) if turret else ""
            flag = bool(turret.get(COLUMN_DEFAULT)) if turret else False
            mark = "进挂载" if flag else "只进候选（IsDefault=false）"
            rid = _as_int(row.get(COLUMN_ID))
            order = _as_int(row.get(COLUMN_ORDER))
            lines.append(
                f"  TurretUnits#{rid} · Turrets#{tid} {name or '（无名）'}"
                f"（键 {key or '（没有模型键）'}）· Order={order} · {mark}"
            )
        return lines


# --------------------------------------------------------------------------- #
# 写（⛔ 只有这一条链：产品内核的 session + Changeset）
# --------------------------------------------------------------------------- #
def _delta_document(designed: TurretPlan, target: str, after: Mapping[str, Any]) -> dict[str, Any]:
    """反向 Δ 的文档（照 `cli/row_delete.py::_delta_document` 的形状）。"""
    return {
        "kind": DELTA_KIND,
        "version": DELTA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": designed.action,
        "source": designed.source,
        "target": str(target),
        "unit": {"id": designed.unit_id, "key": designed.unit_key, "hud": designed.unit_hud},
        "turret": {
            "id": designed.turret_id,
            "key": designed.turret_key,
            "name": designed.turret_name,
        },
        "mounts_before": list(designed.mounts_before),
        "mounts_after": list(designed.mounts_after),
        "links_before": designed.links_before,
        "links_after": designed.links_after,
        "fingerprint_before": dict(designed.fingerprint_before),
        "fingerprint_after": dict(after),
        "ops": [op.as_json() for op in designed.ops],
        "restore": "turret_entry.restore(<落点>)  <- 唯一还原入口（往返逐字节）",
    }


def _check_op_current(instance: Any, op: Op) -> None:
    """写前逐条核「老值还在不在」—— ⛔ 不盲写（计划是照某一版算的）。"""
    rows = _rows(instance, op.table)
    present = [row for row in rows if _as_int(row.get(COLUMN_ID)) == int(op.row_id)]
    if isinstance(op, CellOp):
        if not present:
            raise TurretEntryError(
                f"{op.table} 里已经找不到 {COLUMN_ID}={op.row_id} 那一行 —— 拒写"
            )
        now = present[0].get(op.column)
        if now != op.old:
            where = f"{op.table}#{op.row_id}.{op.column}"
            raise TurretEntryError(f"{where} 现在是 {now!r}，不是规划时的 {op.old!r} —— 拒写")
    elif isinstance(op, InsertOp):
        if present:
            raise TurretEntryError(f"{op.table} 里已经有 Id={op.row_id} 的行 —— 拒写")
    elif not present:
        raise TurretEntryError(f"{op.table} 里已经找不到 Id={op.row_id} 的行 —— 拒写")


def _verify_written(target: str | Path, designed: TurretPlan) -> list[str]:
    """写后读回（⛔ 不许省）：重开落点，按 `card_turrets` 逐条核。"""
    with _open(Path(target)) as (instance, _opened):
        mounts = tuple(line.turret_id for line in effective_mounts(instance, designed.unit_id))
        links = len(_unit_links(instance, designed.unit_id))
    tail = f"挂载 {len(mounts)} 条 {_ids(mounts)} · 名册 {links} 行"
    if mounts != tuple(designed.mounts_after) or links != designed.links_after:
        plan_tail = f"挂载 {_ids(designed.mounts_after)} · 名册 {designed.links_after} 行"
        raise TurretEntryError(
            f"写后读回对不上：{tail}（计划是 {plan_tail}）",
            hint="落点可能被别的东西同时改了 —— 用 restore 撤回去再查",
        )
    return [f"写后读回: ✔ {tail}"]


def _inside_game_root(source: str | Path) -> bool:
    """这一份落点在不在**游戏目录**里（只有它才受「游戏在跑就别写」那道闸）。"""
    try:
        from ba_mod_maker.core.assets import game as game_mod

        root = game_mod.find_game_root()
    except Exception:  # noqa: BLE001 -- 探不到游戏根 = 不是游戏目录那条路
        return False
    if root is None:
        return False
    try:
        Path(source).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _game_running() -> bool:
    """游戏在不在跑 —— 唯一入口 `core/assets/game.py::is_game_running`（与 row-delete 同一把）。"""
    from ba_mod_maker.core.assets import game as game_mod

    return bool(game_mod.is_game_running())


def _save(instance: Any, changeset: Any, target: str | None):
    """写盘唯一入口：`core.session.save_instance`（`target=None` = 写回打开的那个文件）。"""
    from ba_mod_maker.core import session

    return session.save_instance(instance, changeset.changed_tables(), target=target)


def apply(
    source: str | Path,
    unit: str | None = None,
    turret: str | None = None,
    *,
    unit_id: int | None = None,
    turret_id: int | None = None,
    slot: int | None = None,
    remove: bool = False,
    order: int | None = None,
    option_id: int | None = None,
    write: bool = False,
    out: str | Path | None = None,
    allow_running: bool = False,
) -> list[str]:
    """写回（**默认干跑**）：先 `plan`（零写入）-> 核盘上那一版 -> 写 -> 读回 -> 留反向 Δ。

    返回**读数行** `list[str]`（第 1 行 <= 60 字摘要）—— 面板只画摘要、明细进「看详情」；
    子进程工人（`blender_addon/_cli_worker.py`）也照这个形状收。

    ⛔ 不写盘的两条路：`write=False`（干跑）、以及任何拒写（抛 `TurretEntryError`）。
    """
    designed = plan(
        source,
        unit,
        turret,
        unit_id=unit_id,
        turret_id=turret_id,
        slot=slot,
        remove=remove,
        order=order,
        option_id=option_id,
    )
    lines = plan_lines(designed)
    if not write:
        lines.append("（⛔ 没给 write=True：干跑，一个字节都没写）")
        return lines
    with _open(Path(source)) as (instance, opened_path):
        target = Path(out) if out is not None else opened_path
        if target is None:
            raise TurretEntryError(
                "这个来源没有「写回它本身」的落点（容器重写一次 7 GB / 约 4 分钟）",
                hint="给 out=<另存一份 BA_Mod.zip>；容器那条写回走产品 import 那条链",
            )
        now = fingerprint(opened_path)
        if not _same_version(designed.fingerprint_before, now):
            old_text = _fp_text(designed.fingerprint_before)
            raise TurretEntryError(
                "落点已经不是规划时那一版 —— 拒写（一个字节都没写）",
                hint=f"规划时 {old_text} ／ 现在 {_fp_text(now)}",
            )
        if not allow_running and _inside_game_root(target) and _game_running():
            raise TurretEntryError(
                "游戏正在运行 —— 拒写（一个字节都没写）",
                hint="退出游戏再写；确实要在游戏在跑时写就给 allow_running=True",
            )
        for op in designed.ops:
            _check_op_current(instance, op)
        changeset = _changeset(instance)
        _apply_ops(changeset, designed.ops)
        report = _save(instance, changeset, None if out is None else str(out))
        after = fingerprint(report.path)
        delta_file = delta_path(report.path)
        delta_file.parent.mkdir(parents=True, exist_ok=True)
        document = _delta_document(designed, str(report.path), after)
        delta_file.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    on_disk = Path(report.path).stat().st_size
    lines.append(f"落点    : {report.path}（磁盘 {on_disk:,} B）")
    was = _fp_text(designed.fingerprint_before)
    lines.append(f"指纹    : 写前 {was} -> 写后 {_fp_text(after)}")
    lines.extend(_verify_written(report.path, designed))
    lines.append(f"反向 Δ  : {delta_file}（还原走 turret_entry.restore(<落点>)）")
    return lines


def restore(
    source: str | Path,
    *,
    delta: str | Path | None = None,
    write: bool = True,
    out: str | Path | None = None,
    allow_running: bool = False,
) -> list[str]:
    """按反向 Δ 还原（`<落点>.turret-pair-delta.json` 是**唯一**入口；往返逐字节）。

    逐条核：Δ 记的落点对不对、盘上是不是我们写完那一版、还原后的指纹与装前是否**逐位相同**。
    """
    target = Path(source)
    delta_file = Path(delta) if delta is not None else delta_path(target)
    if not delta_file.is_file():
        raise TurretEntryError(
            f"找不到反向 Δ：{delta_file}",
            hint=f"Δ 与落点同目录，名字是 <落点>.{DELTA_KIND}.json",
        )
    document = json.loads(delta_file.read_text(encoding="utf-8"))
    kind = _text(document.get("kind"))
    if kind != DELTA_KIND or _as_int(document.get("version")) != DELTA_VERSION:
        raise TurretEntryError(f"这份 Δ 不是 {DELTA_KIND} v{DELTA_VERSION}：{delta_file}")
    recorded = Path(str(document.get("target") or target))
    if recorded.resolve() != target.resolve():
        raise TurretEntryError(f"Δ 记的落点是 {recorded}，与给的不一致 —— 拒还原（零写入）")
    ops = tuple(_op_from_json(item) for item in (document.get("ops") or ()))
    action = _clip(_text(document.get("action")), 12)
    lines = [f"还原：{target.name}（{len(ops)} 步 · {action}）"]
    with _open(target) as (instance, opened_path):
        now = fingerprint(opened_path)
        if not _same_version(document.get("fingerprint_after") or {}, now):
            written = _fp_text(document.get("fingerprint_after") or {})
            raise TurretEntryError(
                "落点已经不是我们写完那一版 —— 拒还原（一个字节都没写）",
                hint=f"写完那版 {written} ／ 现在 {_fp_text(now)}",
            )
        lines.append(f"写时    : {_text(document.get('created_at'))}")
        lines.append(f"盘上    : {_fp_text(now)}")
        unit_id = _as_int((document.get("unit") or {}).get("id"))
        if not write:
            lines.append("（⛔ write=False：只出计划，一个字节都没写）")
            return lines
        if not allow_running and _inside_game_root(target) and _game_running():
            raise TurretEntryError("游戏正在运行 —— 拒还原（一个字节都没写）")
        inverse = _invert(instance, ops)
        for op in inverse:
            _check_op_current(instance, op)
        changeset = _changeset(instance)
        _apply_ops(changeset, inverse)
        report = _save(instance, changeset, None if out is None else str(out))
    back = fingerprint(report.path)
    if not _same_version(document.get("fingerprint_before") or {}, back):
        was = _fp_text(document.get("fingerprint_before") or {})
        raise TurretEntryError(
            f"还原后与装前**不是逐位相同**：{was} vs {_fp_text(back)}",
            hint="落点先别动 —— 把 Δ 与落点都留着取原文",
        )
    with _open(Path(report.path)) as (instance, _opened):
        mounts = tuple(line.turret_id for line in effective_mounts(instance, unit_id))
        links = len(_unit_links(instance, unit_id))
    on_disk = Path(report.path).stat().st_size
    lines.append(f"落点    : {report.path}（磁盘 {on_disk:,} B）")
    was_mounts = _ids(document.get("mounts_before") or ())
    was_links = document.get("links_before")
    now_text = f"挂载 {len(mounts)} 条 {_ids(mounts)} · 名册 {links} 行"
    lines.append(f"读回    : {now_text}（装前 {was_mounts} · {was_links} 行）")
    lines.append(f"逐位回装前: ✔ {_fp_text(back)}")
    return lines
