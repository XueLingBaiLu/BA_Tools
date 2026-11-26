"""编成台 ④ 运输配对的取数与规则。**零 Qt。**

口径出处：

- 设计图 `docs/设计稿-编成台-载具.png` 中栏 ④（BTR-ZD Skrezhet / `Units#206`）；
- `docs/交接-2026-10-01-单位编成台设计.md` §3 口径 1（**只有 ＋ 和 ✕，没有"换"**）、
  口径 7（删的是**关联行**，对方那一行一个字节不动）；
- 取数口径**复用** `app/viewmodel/unit_card.py::card_transports` 那一条路（不另写一份）；
- Lead 2026-10-01 定案：**「＋」是一级挑（只挑一个单位）**，理由见下。

## 这三张表（列名逐条查过 `docs/数据-表与列清单.md`，**没有猜**）

```
TransportAvailabilities          (227 行 × 3 列)
    Id                            主键 —— ✕ 删的是它
    UnitId                        **承运方**（就是编成台当前这个单位）
    SpecializationAvailabilityId  -> SpecializationAvailabilities.Id
SpecializationAvailabilities     (338 行 × 7 列)
    Id / SpecializationId / UnitId / MaxAvailabilityXp0..Xp3
    UnitId                        **被运的那个单位**（206 的 109 = VDV Igla）
    SpecializationId              -> Specializations.Id（7 = VDV Brigade）
    MaxAvailabilityXp0            界面上那句「上限 N」
Specializations                  Name / UIName …
```

真数据（实例 52085）逐条核过：

- `Units#206`（BTR-ZD Skrezhet）只有 **1** 条配对：`TransportAvailabilities#902`
  -> `SA#172`（UnitId 109 / SpecializationId 7 / MaxAvailabilityXp0 4）
  —— 与设计图上那行「VDV Igla │能被它搭载 · 专精 VDV Brigade · 上限 4」逐字对得上；
- **方向**：`TransportAvailabilities.UnitId` 是**承运方**；`SA.UnitId` 才是**被运方**。
  ⚠️ 承运方**不必自己有 SA 行**（206 = 0 条、1 = 0 条）—— 所以"从本车的 SA 行里挑"
  那种画法**恒为空**，别这么写；
- **`SA` 与"被运单位"是 1:1**：`SA` 338 行覆盖 338 个单位，**没有一个单位的 SA 行不止一条**
  （Lead 的只读探针 `_work/probe_transport_dir.py` 实测）。于是挑一个单位就唯一确定了
  `SA` 行 → 这就是「＋」只挑一级的依据（`sa_id_of_unit`）；
- 「上限」**只能取 `MaxAvailabilityXp0`**：`Xp1 / Xp2 / Xp3` 三列 338 行非默认行数都是 **0**
  （全表同值 0，是占位，AGENTS §0.5 判据 1）；而 `MaxAvailabilityXp0` 的取值是
  1 / 2 / 3 / 4 / 6 / 8（137 行是 4），**没有 0** —— 界面上不会出现「上限 0」；
- 227 条配对**没有一条**指向不存在的 `SA`，也没有一条的 `SA.UnitId` 是 0。

❗ 本模块不得 import 任何 Qt 符号（`tests/test_core_layering.py` 用 AST 扫）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ba_mod_maker.app.viewmodel.unit_card import CardData, card_transports, row_name
from ba_mod_maker.core.edit.changeset import ID_COLUMN, Changeset
from ba_mod_maker.core.edit.clone import next_row_id

# --------------------------------------------------------------------------- #
# 三张表的名字（字面量只在这里一份）
# --------------------------------------------------------------------------- #

#: 被编辑的那张表：一行 = 一条运输配对。
TABLE = "TransportAvailabilities"
#: 配对指向的那张表（「被运单位 + 专精 + 上限」三样都在它身上）。
SPEC_AVAIL_TABLE = "SpecializationAvailabilities"
#: 专精名从这张表取。
SPECIALIZATIONS_TABLE = "Specializations"
#: 被运方是**单位**（名字走 Units.HUDName，见 core/edit/foreign.py::NAME_COLUMN）。
UNITS_TABLE = "Units"

#: 用哪一档上限。❌ 别改成 Xp1..Xp3 —— 那三列全库非默认行数 = 0（见模块 docstring）。
CAP_COLUMN = "MaxAvailabilityXp0"

#: 这一块里一行的两个外键列。
LINK_COLUMN = "SpecializationAvailabilityId"
OWNER_COLUMN = "UnitId"


@dataclass(frozen=True, slots=True)
class TransportLine:
    """④ 里的一行 = `TransportAvailabilities` 里的一行。

    `row_id` 是**配对行**（✕ 删它），`sa_id` 是它指向的
    `SpecializationAvailabilities`（✕ 不动对方那一行，只断开这条配对）。
    """

    #: 配对行 Id —— ✕ 删的就是它
    row_id: int
    #: 指向的 `SpecializationAvailabilities.Id`
    sa_id: int
    #: 承运方（= 编成台当前这个单位）
    carrier_id: int
    #: 被运的那个单位 Id（`SA.UnitId`；读不出来给 0）
    carried_id: int
    #: 被运单位在界面上的名字（`Units.HUDName` 优先，见 `row_name`）
    carried_label: str
    #: 专精名（`Specializations.Name`；读不出来给「专精 #N」）
    spec_label: str
    #: 「上限 N」那个 N（`SA.MaxAvailabilityXp0`）
    cap: int

    @property
    def carried_text(self) -> str:
        """左边那一格：被运单位叫什么。

        ❗ `SA.UnitId` 指向的单位**已经不在了**（被删过）时照实说，不编一个名字 ——
        与 `row_name` 的口径同一条（"与其编一个假名字，不如说这行没有名字"）。
        """
        if self.carried_id <= 0:
            return "（这一条配对没指向单位）"
        if self.carried_label:
            return self.carried_label
        return f"（{UNITS_TABLE} #{self.carried_id} 已经不在了）"

    @property
    def detail_text(self) -> str:
        """右边那串灰字：「能被它搭载 · 专精 X · 上限 N」（设计图逐字）。"""
        return f"能被它搭载 · 专精 {self.spec_label} · 上限 {self.cap}"


def _spec_label(data: CardData, spec_id: int) -> str:
    """专精名（读不出来照实说，不编）。"""
    if spec_id <= 0:
        return "（这条配对没指向专精）"
    if data.has_table(SPECIALIZATIONS_TABLE):
        return row_name(data, SPECIALIZATIONS_TABLE, spec_id)
    return f"专精 #{spec_id}"


def _sa_of_id(data: CardData, sa_id: int) -> Any:
    """按 `SA.Id` 取它那一行（没有就 None）。"""
    if sa_id <= 0:
        return None
    return data.t(SPEC_AVAIL_TABLE).by_id(sa_id)


def _line_of(data: CardData, carrier_id: int, row: Any, sa_id: int) -> TransportLine:
    """一条配对行 -> 界面要的 `TransportLine`（「被运方 + 专精 + 上限」三样都在 `SA` 那一行上）。"""
    spec_avail = data.t(SPEC_AVAIL_TABLE)
    target = _sa_of_id(data, sa_id)
    carried_id = spec_avail.i(target, "UnitId") if target is not None else 0
    spec_id = spec_avail.i(target, "SpecializationId") if target is not None else 0
    cap = spec_avail.i(target, CAP_COLUMN) if target is not None else 0
    return TransportLine(
        row_id=int(data.t(TABLE).i(row, ID_COLUMN)),
        sa_id=int(sa_id),
        carrier_id=int(carrier_id),
        carried_id=int(carried_id),
        carried_label=row_name(data, UNITS_TABLE, carried_id) if carried_id > 0 else "",
        spec_label=_spec_label(data, spec_id),
        cap=int(cap),
    )


def transport_lines(data: CardData, unit_id: int) -> tuple[TransportLine, ...]:
    """本单位（**承运方**）的全部运输配对，按配对行 Id 升序。

    走法与 `unit_card.card_transports` 完全一致：本表 `UnitId == 本单位` 的那些行，
    `SpecializationAvailabilityId` 指到 `SpecializationAvailabilities`，
    再从那一行取「被运单位 + 专精 + 上限」。

    ❗ 空元组 = 这一台车没有任何运输配对（跑车多半是空的），**不是**"没算出来"。
    """
    if not data.has_table(TABLE) or not data.has_table(SPEC_AVAIL_TABLE):
        return ()
    rows = data.t(TABLE)
    if LINK_COLUMN not in rows.table.columns or OWNER_COLUMN not in rows.table.columns:
        return ()
    out: list[TransportLine] = []
    for link in card_transports(data, unit_id):
        # `card_transports` 给的是**配对行 Id**（顺序也由它定：按 Id 升序）。
        row = rows.by_id(link.row_id)
        if row is None:  # pragma: no cover - 同一次读里不会丢行
            continue
        out.append(_line_of(data, unit_id, row, rows.i(row, LINK_COLUMN)))
    return tuple(out)


def sa_id_of_unit(data: CardData, unit_id: int) -> int:
    """被运单位 `unit_id` 对应的 `SpecializationAvailabilities.Id`；没有这条给 **0**。

    这就是「＋」**只挑一级**的全部依据：`SA` 与"被运单位"是 1:1（338 行覆盖 338 个单位，
    没有一对多 —— Lead 的只读探针实测），所以挑中一个单位就唯一确定了要写进
    `SpecializationAvailabilityId` 的那个 `SA`。

    ❗ 一条都没有时返回 0（**不猜、也不为了凑一条去造 `SA` 行**）—— 调用方据此拒绝并
    给一句 `status_message`。多条命中时取 **Id 最小**那条（防御性：真出现一对多时行为仍然确定）。
    """
    if unit_id <= 0 or not data.has_table(SPEC_AVAIL_TABLE):
        return 0
    spec_avail = data.t(SPEC_AVAIL_TABLE)
    if OWNER_COLUMN not in spec_avail.table.columns:
        return 0
    hits = sorted(spec_avail.i(row, ID_COLUMN) for row in spec_avail.where(OWNER_COLUMN, unit_id))
    return int(hits[0]) if hits else 0


def new_transport_values(
    data: CardData,
    carrier_id: int,
    carried_unit_id: int,
    *,
    changeset: Changeset | None = None,
) -> dict[str, Any] | None:
    """④ 的「＋」要插的那一行（`EditorContext.insert_row` 的 values）；插不了给 **None**。

    三列（这张表只有 3 列）：`Id`（**next_row_id**）、`UnitId` = **承运方**（本单位）、
    `SpecializationAvailabilityId` = 被运单位反查出来的那条 `SA`。

    ❗ 别自己写"最大 Id + 1"：Id 有大空洞，而且"删掉最大那一行"之后会捡回刚删的 Id
    （`core/edit/clone.py::next_row_id` 的注释里写着这条）。
    ❗ `Id` **必须自己给**：不给（或给非 int）`Changeset.insert_row` 会抛 `TableError`。
    """
    sa_id = sa_id_of_unit(data, carried_unit_id)
    if carrier_id <= 0 or sa_id <= 0:
        return None
    table = changeset.table(TABLE) if changeset is not None else data.instance.table(TABLE)
    return {
        ID_COLUMN: next_row_id(table, changeset),
        OWNER_COLUMN: int(carrier_id),
        LINK_COLUMN: sa_id,
    }


def transport_empty_text(carrier_id: int) -> str:
    """这块空着时那句灰字（**不是空框** —— 空框看不出是空的还是没画出来）。"""
    return f"（{UNITS_TABLE} #{carrier_id} 还没有运输配对 —— 用右上角的 ＋ 加一行）"


__all__ = [
    "CAP_COLUMN",
    "LINK_COLUMN",
    "OWNER_COLUMN",
    "SPECIALIZATIONS_TABLE",
    "SPEC_AVAIL_TABLE",
    "TABLE",
    "UNITS_TABLE",
    "TransportLine",
    "new_transport_values",
    "sa_id_of_unit",
    "transport_empty_text",
    "transport_lines",
]
