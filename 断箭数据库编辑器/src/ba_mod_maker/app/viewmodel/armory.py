"""军械库视图的数据整形（零 Qt）。

把 Instance 的若干张表整形成「专精 → 类别行 → 卡片」的纯数据结构，
以及单个单位的详情（武器 / 弹药 / 运输 / 选项）。

设计依据见 docs/adr/0002-主屏-军械库视图.md。三条核心规则：

1. **类别行 = Units.CategoryType 0..6**，行序即枚举序；
2. **卡片集 = 该专精在 SpecializationAvailabilities 里直挂的单位**，
   再排除 DisplayInArmory != true 与 IsUnitModification == true；
3. **成本徽章 = 右卡那张卡的成本**（`unit_card.card_cost`：OriginalCost + Σ 缺省选项 Cost），
   可用数上限 = MaxAvailabilityXp0。

> ❗ 第 3 条 2026-10 改过：以前直接取 `Units.Cost` 原始列，于是 Su-57 左墙写 225、
> 右卡写 335（换过挂架 415）—— 用户报的"左右两边不一致，以右边为准"就是这个。
> **右卡是权威**，左墙一律走同一个 `card_cost`。

❗ 本模块不得 import 任何 Qt 符号。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.app.viewmodel.unit_card import (
    CardData,
    card_cost,
    unit_name,
    unit_portrait_key,
    unit_thumbnail_key,
)
from ba_mod_maker.core.assets.families import UNCLASSIFIED_FLAG_KEY
from ba_mod_maker.core.data.dataset import Instance, Table

# --------------------------------------------------------------------------- #
# 枚举中文名
# --------------------------------------------------------------------------- #

#: CategoryType 0..6 的中文名。**顺序即枚举顺序，不可改。**
#: 来源：反编译 BrokenArrow.DataBase.Enums.UnitCategoryTypeUI
#:       { None=-1, Recon, Infantry, Vehicles, Support, Logistic, Helicopters, Aircrafts }
#:
#: ❗ 下标 4 的枚举名是 Logistic，但**实机军械库里没有这一类**（首页图例只有 6 类：
#: 侦察/步兵/载具/支援/直升机/战机），而且落在这一类的单位（舰船 36 个）
#: `DisplayInArmory` 全是 false —— 一张卡都不上墙。用户口径（2026-09-30）：
#: 把它叫「**未分类**」，别叫「后勤」（名不副实，点进去也是空的）。
CATEGORY_LABELS: tuple[str, ...] = ("侦察", "步兵", "载具", "支援", "未分类", "直升机", "战机")

#: UnitType 的中文名。【实测】按单位名称分布验证：
#: 2 -> 步兵班，4 -> BTR/BMP 等，8 -> Mi-8/Ka-52，16 -> Su-57/A-10，32 -> Zubr/LCAC。
KIND_LABELS: dict[int, str] = {2: "步兵", 4: "载具", 8: "直升机", 16: "战机", 32: "舰船"}

#: 专精没有任何单位可用时的占位标题。
UNASSIGNED_TITLE = "未编入任何专精"

_T_UNITS = "Units"
_T_COUNTRIES = "Countries"
_T_SPECS = "Specializations"
_T_SPEC_AVAIL = "SpecializationAvailabilities"
_T_TRANSPORT_AVAIL = "TransportAvailabilities"
_T_TURRET_UNITS = "TurretUnits"
_T_TURRET_WEAPONS = "TurretWeapons"
_T_SQUAD_WEAPONS = "SquadWeapons"
_T_SQUAD_MEMBERS = "SquadMembers"
_T_WEAPONS = "Weapons"
_T_WEAPON_AMMO = "WeaponAmmunitions"
_T_AMMO = "Ammunitions"
_T_MODIFICATIONS = "Modifications"
_T_OPTIONS = "Options"


# --------------------------------------------------------------------------- #
# 取值助手
# --------------------------------------------------------------------------- #


def _column(table: Table, name: str) -> list[Any]:
    """取一列的值。列不存在时抛 KeyError（宁可炸，也不要静默给空）。"""
    index = table.column_index(name)
    return [row[index] for row in table.rows]


def _column_or_none(table: Table, name: str) -> list[Any] | None:
    """取一列；列不存在返回 None。用于跨版本可能缺列的场合。"""
    try:
        return _column(table, name)
    except KeyError:
        return None


def _int(value: Any, default: int = 0) -> int:
    """JSON 里可能是 null。bool 不算 int（否则 True 会变成 1）。"""
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    return float(value) if isinstance(value, (int, float)) else default


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def category_label(category: int) -> str:
    """CategoryType -> 中文名。越界时**不猜**，直接显示原始数字。"""
    if 0 <= category < len(CATEGORY_LABELS):
        return CATEGORY_LABELS[category]
    return f"类型 {category}"


# --------------------------------------------------------------------------- #
# 单位内部行
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Unit:
    """Units 表一行的强类型视图。字段名与列名一一对应。"""

    unit_id: int
    name: str
    hud_name: str
    cost: int
    category: int
    kind: int
    country_id: int
    role: int
    icon_key: str
    portrait_key: str
    model_key: str
    description: str
    audio_preset: str
    stealth: float
    infantry_slots: int
    weight: int
    length: float
    width: float
    height: float
    displayable: bool

    @property
    def display_name(self) -> str:
        """界面上优先显示 **HUDName**（游戏里 HUD/军械库写的就是它），为空退回 Name。

        用户明确要求：卡片墙上要显示 HUDName。实测 540 个单位里 **181 个**两者不同，
        差别就在这——`Name` 带内部前缀与配置后缀（`DLC3 FSB Snaipery`、`Marine Raiders CQC`），
        `HUDName` 才是游戏里那个（`FSB Snaipery`、`Marine Raiders`）。
        右栏卡片的抬头一直用的是 HUDName（`unit_card.build_unit_card`），这次把左墙对齐。
        """
        return self.hud_name or self.name or f"#{self.unit_id}"


def _load_units(instance: Instance) -> tuple[_Unit, ...]:
    """一次性把 Units 表整表转成强类型行。540 行的规模，不必做惰加载。"""
    table = instance.table(_T_UNITS)
    c = {
        name: _column(table, name)
        for name in (
            "Id",
            "Name",
            "HUDName",
            "Cost",
            "CategoryType",
            "Type",
            "CountryId",
            "Role",
            "ThumbnailFileName",
            "PortraitFileName",
            "ModelFileName",
            "Description",
            "AudioPreset",
            "Stealth",
            "InfantrySlots",
            "Weight",
            "Length",
            "Width",
            "Height",
            "DisplayInArmory",
            "IsUnitModification",
        )
    }
    units: list[_Unit] = []
    for i in range(table.row_count):
        units.append(
            _Unit(
                unit_id=_int(c["Id"][i]),
                name=_text(c["Name"][i]),
                hud_name=_text(c["HUDName"][i]),
                cost=_int(c["Cost"][i]),
                category=_int(c["CategoryType"][i], -1),
                kind=_int(c["Type"][i]),
                country_id=_int(c["CountryId"][i]),
                role=_int(c["Role"][i]),
                icon_key=_text(c["ThumbnailFileName"][i]),
                portrait_key=_text(c["PortraitFileName"][i]),
                model_key=_text(c["ModelFileName"][i]),
                description=_text(c["Description"][i]),
                audio_preset=_text(c["AudioPreset"][i]),
                stealth=_float(c["Stealth"][i]),
                infantry_slots=_int(c["InfantrySlots"][i]),
                weight=_int(c["Weight"][i]),
                length=_float(c["Length"][i]),
                width=_float(c["Width"][i]),
                height=_float(c["Height"][i]),
                displayable=(
                    c["DisplayInArmory"][i] is True and c["IsUnitModification"][i] is not True
                ),
            )
        )
    return tuple(units)


# --------------------------------------------------------------------------- #
# 专精与阵营
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CountryInfo:
    country_id: int
    name: str
    ui_name: str
    flag_key: str
    max_points: int
    hidden: bool

    @property
    def display_name(self) -> str:
        return self.ui_name or self.name or f"阵营 {self.country_id}"


@dataclass(frozen=True, slots=True)
class SpecializationInfo:
    specialization_id: int
    country_id: int
    name: str
    ui_name: str
    icon_key: str
    illustration_key: str
    slots: tuple[int, ...]
    points: tuple[int, ...]

    @property
    def display_name(self) -> str:
        """界面显示用。UI 上那个「VDV旅」是本地化文本，本仓不解析本地化表，
        所以直接用英文 Name —— 宁可显示原文，也不要瞎翻一个中文名。"""
        return self.name or f"专精 {self.specialization_id}"


def list_countries(instance: Instance) -> tuple[CountryInfo, ...]:
    """全部阵营。Hidden 的照常返回，由界面决定藏不藏。"""
    table = instance.table(_T_COUNTRIES)
    ids = _column(table, "Id")
    return tuple(
        CountryInfo(
            country_id=_int(ids[i]),
            name=_text(_column(table, "Name")[i]),
            ui_name=_text(_column(table, "UIName")[i]),
            flag_key=_text(_column(table, "FlagFileName")[i]),
            max_points=_int(_column(table, "MaxPoints")[i]),
            hidden=_column(table, "Hidden")[i] is True,
        )
        for i in range(table.row_count)
    )


#: 「未分类」那一格的 Id —— **不是 Countries 表里的行**，借 -1 表示。
#:
#: 用户口径（2026-10-02 深夜，原话「有些没有国家的单位，统一放在"未分类"国家里」＋
#: 「国旗我记得在游戏里有，是个橙底，问号旗」）：收两类单位 ——
#: ① `CountryId` 指向**隐藏国家**的；② `CountryId` 在 Countries 表里**查不到**的。
#: 旗子的键见 `families.UNCLASSIFIED_FLAG_KEY`（那面问号旗没有 DB 键，理由写在那边）。
UNCLASSIFIED_COUNTRY_ID = -1

#: 那一格显示的名字。
UNCLASSIFIED_NAME = "未分类"


def unclassified_country() -> CountryInfo:
    """「未分类」那一格 —— 不在 Countries 表里，所以是**造**出来的一条。"""
    return CountryInfo(
        country_id=UNCLASSIFIED_COUNTRY_ID,
        name=UNCLASSIFIED_NAME,
        ui_name="",
        flag_key=UNCLASSIFIED_FLAG_KEY,
        max_points=0,
        hidden=False,
    )


def unclassified_country_ids(instance: Instance) -> frozenset[int]:
    """哪些 CountryId 算「未分类」：**隐藏国家**的 Id ＋ 表里**根本没登记**的 Id。

    实测（`_work/probe_unclassified_countries.py`，编译库 52085）：Countries 3 行
    （1 Russia / 2 USA / 3 Editor，Editor 的 `Hidden=True`）；Units 540 行里 CountryId=3 的
    8 行（全是 ZOM）、查不到的 0 行。
    """
    table = instance.table(_T_COUNTRIES)
    ids = _column(table, "Id")
    hidden_column = _column(table, "Hidden")
    known = {_int(ids[i]) for i in range(table.row_count)}
    hidden = {_int(ids[i]) for i in range(table.row_count) if hidden_column[i] is True}
    used = {_int(value) for value in _column(instance.table(_T_UNITS), "CountryId")}
    return frozenset(hidden | (used - known))


def _country_matches(country_id: int, wanted: int, unclassified: frozenset[int]) -> bool:
    """这一行算不算「wanted」那一格。**逻辑只有这一份**：卡片集与计数共用它。"""
    if wanted == UNCLASSIFIED_COUNTRY_ID:
        return country_id in unclassified
    return country_id == wanted


def list_specializations(
    instance: Instance, *, country_id: int | None = None, hangar_only: bool = True
) -> tuple[SpecializationInfo, ...]:
    """专精列表。默认只给 ShowInHangar=true 的（那是玩家真能选的）。"""
    table = instance.table(_T_SPECS)
    show = _column(table, "ShowInHangar")
    ids = _column(table, "Id")
    item: list[SpecializationInfo] = []
    for i in range(table.row_count):
        if hangar_only and show[i] is not True:
            continue
        cid = _int(_column(table, "CountryId")[i])
        # 「未分类」那一格**不按国家筛专精** —— 那些单位可能挂在任何国家的专精下；
        # 按国家筛的话下拉里一个都不剩（用户 2026-10-03 报的原话：「我说我还要分专精，你没分」）。
        if country_id is not None and country_id != UNCLASSIFIED_COUNTRY_ID and cid != country_id:
            continue
        item.append(
            SpecializationInfo(
                specialization_id=_int(ids[i]),
                country_id=cid,
                name=_text(_column(table, "Name")[i]),
                ui_name=_text(_column(table, "UIName")[i]),
                icon_key=_text(_column(table, "Icon")[i]),
                illustration_key=_text(_column(table, "Illustration")[i]),
                slots=tuple(
                    _int(_column(table, key)[i])
                    for key in (
                        "ReconSlots",
                        "InfantrySlots",
                        "CombatSlots",
                        "SupportSlots",
                        "LogisticsSlots",
                        "HelicoptersSlots",
                        "AirSlots",
                    )
                ),
                points=tuple(
                    _int(_column(table, key)[i])
                    for key in (
                        "ReconPoints",
                        "InfantryPoints",
                        "CombatPoints",
                        "SupportPoints",
                        "LogisticsPoints",
                        "HelicoptersPoints",
                        "AirPoints",
                    )
                ),
            )
        )
    return tuple(item)


# --------------------------------------------------------------------------- #
# 可用性索引
# --------------------------------------------------------------------------- #


def _availability_rows(instance: Instance) -> tuple[tuple[int, int, int], ...]:
    """(specialization_id, unit_id, max_availability_xp0)，保持表内原始行序。

    行序很重要：它是行内卡片的默认排序依据（见 ADR 0002「未闭环」）。
    """
    table = instance.table(_T_SPEC_AVAIL)
    spec_ids = _column(table, "SpecializationId")
    unit_ids = _column(table, "UnitId")
    max0 = _column(table, "MaxAvailabilityXp0")
    return tuple(
        (_int(spec_ids[i]), _int(unit_ids[i]), _int(max0[i])) for i in range(table.row_count)
    )


def _spec_unit_index(
    rows: tuple[tuple[int, int, int], ...],
) -> dict[int, dict[int, tuple[int, int]]]:
    """spec_id -> {unit_id: (全局行序, 可用数上限)}。同一组合重复时取首个。"""
    index: dict[int, dict[int, tuple[int, int]]] = {}
    for position, (spec_id, unit_id, max0) in enumerate(rows):
        bucket = index.setdefault(spec_id, {})
        bucket.setdefault(unit_id, (position, max0))
    return index


# --------------------------------------------------------------------------- #
# 卡片与行
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ArmoryCard:
    """一张单位卡。字段与界面上的可见元素一一对应。"""

    unit_id: int
    name: str
    hud_name: str
    cost: int
    category: int
    kind: int
    country_id: int
    icon_key: str
    portrait_key: str
    availability: int

    @property
    def display_name(self) -> str:
        """卡墙上显示的名字：**HUDName 优先**（见 _Unit.display_name 的说明）。"""
        return self.hud_name or self.name or f"#{self.unit_id}"

    @property
    def category_label(self) -> str:
        return category_label(self.category)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, "")


@dataclass(frozen=True, slots=True)
class ArmoryRow:
    """一个类别行：左侧类别名 + 行内卡片。"""

    category: int
    label: str
    cards: tuple[ArmoryCard, ...]

    @property
    def count(self) -> int:
        return len(self.cards)


@dataclass(frozen=True, slots=True)
class ArmorySheet:
    """一屏军械库的内容。"""

    title: str
    subtitle: str
    specialization_id: int | None
    country_id: int | None
    only_unassigned: bool
    rows: tuple[ArmoryRow, ...]

    @property
    def total(self) -> int:
        return sum(row.count for row in self.rows)


#: 成本的唯一来源是 app/viewmodel/unit_card.py 的 card_cost（右卡那张卡用的就是它）。
#: 缓存键里放"表对象 + 行数"：`Instance.reload_table()` 会换掉 Table 对象（"放弃改动"走这条），
#: 插行 / 删行会改行数；**改值不用管** —— Rows 缓存的是行对象本身，读到的永远是当前值。
_COST_TABLES: tuple[str, ...] = (_T_UNITS, _T_MODIFICATIONS, _T_OPTIONS)
_COST_DATA_CACHE: OrderedDict[int, tuple[Instance, tuple[int, ...], CardData]] = OrderedDict()
_COST_DATA_CACHE_MAX = 4


def _cost_data(instance: Instance) -> CardData:
    """复用一份 CardData，供 `card_cost` 算左墙徽章。

    为什么必须缓存（实测，52085 / 395 张卡）：

    - `CardData()` 自己 21ms（本地化 + 共享度索引）；
    - **冷**状态下算完 395 张卡的成本 144ms（第一次要建 Modifications / Options 的索引），
      热了只要 2.6ms。

    卡墙每次改过滤词 / 切专精都要重建一次；不缓存就是每个按键白花 160ms，
    正是用户抱怨过的那种卡顿（见 docs/交接-本轮会话状态.md §3）。
    """
    key = id(instance)
    fingerprint = tuple(
        value
        for name in _COST_TABLES
        for value in (id(instance.table(name)), instance.table(name).row_count)
    )
    cached = _COST_DATA_CACHE.get(key)
    if cached is not None and cached[0] is instance and cached[1] == fingerprint:
        _COST_DATA_CACHE.move_to_end(key)
        return cached[2]

    data = CardData(instance)
    _COST_DATA_CACHE[key] = (instance, fingerprint, data)
    _COST_DATA_CACHE.move_to_end(key)
    while len(_COST_DATA_CACHE) > _COST_DATA_CACHE_MAX:
        _COST_DATA_CACHE.popitem(last=False)
    return data


def _card_cost(data: CardData, unit_id: int) -> int:
    """左墙徽章的数字 = **右卡那张卡的成本**（缺省选项）。

    ❗ 口径只有这一处。以前这里用的是 `Units.Cost` 原始列，于是同一个 Su-57
    左墙 225、右卡 335（换过挂架 415）——用户看到的就是"两边不一致"。
    """
    return int(card_cost(data, unit_id))


def _card_name(data: CardData, unit_id: int) -> str:
    """左墙卡名 = **右卡抬头**那个名字（缺省选项）。

    ❗ 与成本同一条纪律：口径只有一处（`unit_card.unit_name`）。以前这里直接用
    `Units.HUDName`，于是实机写 **BTR-82A / T-72B1 / PT-76B** 的单位，我们墙上写的是
    BTR-82 / T-72B / PT-76 —— 选项的名字覆盖列（Options.ReplaceUnitName /
    ConcatenateWithUnitName）没接。实机四条锚见 unit_name 的 docstring。
    """
    return unit_name(data, unit_id)


def _card_keys(data: CardData, unit_id: int) -> tuple[str, str]:
    """左墙那张卡的 (缩略图键, 立绘键) = **右卡那条口径**（缺省选项）。

    ❗ 与成本/卡名同一条纪律：口径只有一处（`unit_card.unit_thumbnail_key` /
    `unit_card.unit_portrait_key`）。以前这里直接抄 Units 那两列，于是缺省项带
    `Options.ThumbnailOverride` 的 **17 个单位**墙上画的还是原始列那张图
    （23 / 72 / 108 / 129 …）。界面层拿到的就是这个键，不要再自己算第二遍。
    """
    return (unit_thumbnail_key(data, unit_id), unit_portrait_key(data, unit_id))


def _make_card(
    unit: _Unit,
    availability: int,
    cost: int,
    display_name: str,
    icon_key: str,
    portrait_key: str,
) -> ArmoryCard:
    return ArmoryCard(
        unit_id=unit.unit_id,
        name=unit.name,
        hud_name=display_name,
        cost=cost,
        category=unit.category,
        kind=unit.kind,
        country_id=unit.country_id,
        icon_key=icon_key,
        portrait_key=portrait_key,
        availability=availability,
    )


def build_armory_sheet(
    instance: Instance,
    *,
    specialization_id: int | None = None,
    country_id: int | None = None,
    only_unassigned: bool = False,
    include_hidden: bool = False,
) -> ArmorySheet:
    """整形出一屏军械库。

    参数
    ----
    specialization_id
        指定专精时，卡片集 = 该专精直挂的单位。
    country_id
        阵营过滤。对任何模式都生效。
        `UNCLASSIFIED_COUNTRY_ID`（-1）是**例外**：见下面的 pool。
    only_unassigned
        只看「未编入任何专精」的单位（无视 specialization_id）。
    include_hidden
        连**上不了军械库**的单位一起放（`DisplayInArmory != true`
        或 `IsUnitModification == true`）。
        用户口径（2026-10-03 原话）：「**我要能在产品里面，看到所有单位！**」
        —— 默认 False（军械库只放能上场的 397），打开后「全部」就是 Units 整表（542）。
    """
    units = _load_units(instance)
    unclassified = (
        unclassified_country_ids(instance) if country_id == UNCLASSIFIED_COUNTRY_ID else frozenset()
    )
    if include_hidden or country_id == UNCLASSIFIED_COUNTRY_ID:
        # 两条都要用**整张 Units 表**：
        # ① include_hidden —— 用户 2026-10-03：「**我要能在产品里面，看到所有单位！**」
        #    （军械库默认只放能上场的 397；打开开关后连游戏里不上屏的 / 改型一起放，542）；
        # ② 「未分类」那一格是**收容所** —— 用户同日：「**上不了军械库的也给我上**」。
        pool = list(units)
    else:
        pool = [unit for unit in units if unit.displayable]
    data = _cost_data(instance)

    rows = _availability_rows(instance)
    spec_index = _spec_unit_index(rows)
    assigned = {unit_id for _, unit_id, _ in rows}

    availability_of: dict[int, int] = {}
    order_of: dict[int, int] = {}

    if only_unassigned:
        selected = [unit for unit in pool if unit.unit_id not in assigned]
        title = UNASSIGNED_TITLE
    elif specialization_id is not None:
        bucket = spec_index.get(specialization_id, {})
        selected = [unit for unit in pool if unit.unit_id in bucket]
        for unit_id, (position, max0) in bucket.items():
            order_of[unit_id] = position
            availability_of[unit_id] = max0
        info = next(
            (
                s
                for s in list_specializations(instance, hangar_only=False)
                if s.specialization_id == specialization_id
            ),
            None,
        )
        title = info.display_name if info is not None else f"专精 {specialization_id}"
    else:
        selected = list(pool)
        title = "全部单位"

    if country_id is not None:
        selected = [
            unit for unit in selected if _country_matches(unit.country_id, country_id, unclassified)
        ]

    buckets: dict[int, list[_Unit]] = {}
    for unit in selected:
        buckets.setdefault(unit.category, []).append(unit)

    armory_rows: list[ArmoryRow] = []
    for category in sorted(buckets):
        ordered = sorted(buckets[category], key=lambda u: (order_of.get(u.unit_id, 0), u.unit_id))
        armory_rows.append(
            ArmoryRow(
                category=category,
                label=category_label(category),
                cards=tuple(
                    _make_card(
                        unit,
                        availability_of.get(unit.unit_id, 0),
                        _card_cost(data, unit.unit_id),
                        _card_name(data, unit.unit_id),
                        *_card_keys(data, unit.unit_id),
                    )
                    for unit in ordered
                ),
            )
        )

    counts = build_counts(
        instance,
        specialization_id=specialization_id,
        country_id=country_id,
        include_hidden=include_hidden,
    )
    parts: list[str] = []
    if specialization_id is not None:
        parts.append(f"{title} {counts.specialization}")
    if country_id is not None:
        parts.append(f"{_country_name(instance, country_id)} {counts.country}")
    parts.append(f"全部 {counts.total}")
    parts.append(f"未编入专精 {counts.unassigned}")
    subtitle = " · ".join(parts)

    return ArmorySheet(
        title=title,
        subtitle=subtitle,
        specialization_id=specialization_id,
        country_id=country_id,
        only_unassigned=only_unassigned,
        rows=tuple(armory_rows),
    )


@dataclass(frozen=True, slots=True)
class ArmoryCounts:
    """编成头右侧那三个数。分开给，界面自己决定怎么排。"""

    specialization: int
    country: int
    total: int
    unassigned: int


def count_units(
    instance: Instance,
    *,
    specialization_id: int | None = None,
    country_id: int | None = None,
    only_unassigned: bool = False,
    include_hidden: bool = False,
) -> int:
    """数一下符合条件、且可上屏的单位有几个。

    只读 Id 与两个布尔列，不构造卡片——会被表头反复调用。
    """
    table = instance.table(_T_UNITS)
    display = _column(table, "DisplayInArmory")
    modific = _column(table, "IsUnitModification")
    unit_ids = _column(table, "Id")
    countries = _column(table, "CountryId")

    assigned = {unit_id for _, unit_id, _ in _availability_rows(instance)}
    allowed: set[int] | None = None
    if specialization_id is not None:
        allowed = set(_spec_unit_index(_availability_rows(instance)).get(specialization_id, {}))

    unclassified = unclassified_country_ids(instance) if country_id is not None else frozenset()
    total = 0
    for i in range(table.row_count):
        # 与 sheet 同一份口径：开关打开、或「未分类」那一格（收容所）⇒ 不上屏的也数。
        if not (include_hidden or country_id == UNCLASSIFIED_COUNTRY_ID) and (
            display[i] is not True or modific[i] is True
        ):
            continue
        unit_id = _int(unit_ids[i])
        if only_unassigned and unit_id in assigned:
            continue
        if allowed is not None and unit_id not in allowed:
            continue
        if country_id is not None and not _country_matches(
            _int(countries[i]), country_id, unclassified
        ):
            continue
        total += 1
    return total


def unit_row_index(instance: Instance, unit_id: int) -> int:
    """unit_id 在 Units 表里的**行下标**（0 基）。找不到返回 -1。

    军械库按 Id 说话，表格按下标定位（TableBrowser.select_row）。
    这个换算必须有且只有一处，否则迟早两边对不上。
    """
    table = instance.table(_T_UNITS)
    ids = _column(table, "Id")
    for index in range(table.row_count):
        if _int(ids[index]) == unit_id:
            return index
    return -1


def _country_name(instance: Instance, country_id: int) -> str:
    if country_id == UNCLASSIFIED_COUNTRY_ID:
        return UNCLASSIFIED_NAME
    for country in list_countries(instance):
        if country.country_id == country_id:
            return country.display_name
    return f"阵营 {country_id}"


def build_counts(
    instance: Instance,
    *,
    specialization_id: int | None = None,
    country_id: int | None = None,
    include_hidden: bool = False,
) -> ArmoryCounts:
    """编成头上那三个数。**三个作用域各自独立**，不因当前选择而消失。

    为什么必须同时给：只看专精会让人以为全游戏只有 31 个单位。
    """
    return ArmoryCounts(
        specialization=(
            count_units(
                instance, specialization_id=specialization_id, include_hidden=include_hidden
            )
            if specialization_id is not None
            else 0
        ),
        country=(
            count_units(instance, country_id=country_id, include_hidden=include_hidden)
            if country_id is not None
            else 0
        ),
        total=count_units(instance, include_hidden=include_hidden),
        unassigned=count_units(instance, only_unassigned=True, include_hidden=include_hidden),
    )


# --------------------------------------------------------------------------- #
# 单位详情
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AmmoEntry:
    ammo_id: int
    name: str
    hud_name: str
    icon_key: str
    quantity: int

    @property
    def display_name(self) -> str:
        return self.name or self.hud_name or f"#{self.ammo_id}"


@dataclass(frozen=True, slots=True)
class WeaponEntry:
    weapon_id: int
    name: str
    hud_name: str
    icon_key: str
    quantity: int
    ammo: tuple[AmmoEntry, ...]

    @property
    def display_name(self) -> str:
        return self.name or self.hud_name or f"#{self.weapon_id}"


@dataclass(frozen=True, slots=True)
class TransportEntry:
    unit_id: int
    name: str
    cost: int
    icon_key: str


@dataclass(frozen=True, slots=True)
class OptionChoice:
    option_id: int
    name: str
    ui_name: str
    cost: int
    is_default: bool

    @property
    def display_name(self) -> str:
        return self.ui_name or self.name or f"#{self.option_id}"


@dataclass(frozen=True, slots=True)
class OptionGroup:
    """一组自定义选项（对应一条 Modifications）。"""

    modification_id: int
    name: str
    ui_name: str
    icon_key: str
    order: int
    choices: tuple[OptionChoice, ...]

    @property
    def display_name(self) -> str:
        return self.ui_name or self.name or f"#{self.modification_id}"


@dataclass(frozen=True, slots=True)
class UnitDetail:
    """右侧详情面板需要的全部数据。"""

    card: ArmoryCard
    description: str
    model_key: str
    audio_preset: str
    dimensions: tuple[float, float, float]
    weight: int
    stealth: float
    infantry_slots: int
    role: int
    availability_xp: tuple[int, int, int, int]
    weapons: tuple[WeaponEntry, ...]
    transports: tuple[TransportEntry, ...]
    option_groups: tuple[OptionGroup, ...]


def _weapon_ids_for_unit(instance: Instance, unit_id: int) -> tuple[tuple[int, int], ...]:
    """单位挂的武器 -> ((weapon_id, order), ...)，按 order 稳定排序。

    两条链（两条都跑，取并集）：

    - 载具：TurretUnits(UnitId, TurretId) -> TurretWeapons(TurretId, WeaponId)
    - 步兵：SquadWeapons(UnitId, WeaponId)
    """
    found: dict[int, int] = {}

    turret_units = instance.table(_T_TURRET_UNITS)
    tu_unit = _column(turret_units, "UnitId")
    tu_turret = _column(turret_units, "TurretId")
    turrets = {
        _int(tu_turret[i]) for i in range(turret_units.row_count) if _int(tu_unit[i]) == unit_id
    }
    if turrets:
        turret_weapons = instance.table(_T_TURRET_WEAPONS)
        tw_turret = _column(turret_weapons, "TurretId")
        tw_weapon = _column(turret_weapons, "WeaponId")
        tw_order = _column(turret_weapons, "Order")
        for i in range(turret_weapons.row_count):
            if _int(tw_turret[i]) not in turrets:
                continue
            weapon_id = _int(tw_weapon[i])
            found.setdefault(weapon_id, _int(tw_order[i]))

    squad_weapons = instance.table(_T_SQUAD_WEAPONS)
    sw_unit = _column(squad_weapons, "UnitId")
    sw_weapon = _column(squad_weapons, "WeaponId")
    sw_order = _column_or_none(squad_weapons, "Order") or [0] * squad_weapons.row_count
    for i in range(squad_weapons.row_count):
        if _int(sw_unit[i]) != unit_id:
            continue
        weapon_id = _int(sw_weapon[i])
        found.setdefault(weapon_id, _int(sw_order[i]))

    return tuple(sorted(found.items(), key=lambda pair: (pair[1], pair[0])))


def _squad_carrier_counts(instance: Instance, unit_id: int) -> dict[int, int]:
    """步兵班里每把武器有几个人带 -> {weapon_id: 人数}。

    这就是详情面板上那个 x9 / x6 / x3 的来源（实测吻合截图）。
    """
    table = instance.table(_T_SQUAD_MEMBERS)
    member_unit = _column(table, "UnitId")
    primary = _column(table, "PrimaryWeaponId")
    special = _column(table, "SpecialWeaponId")
    counts: dict[int, int] = {}
    for i in range(table.row_count):
        if _int(member_unit[i]) != unit_id:
            continue
        for column in (primary, special):
            weapon_id = _int(column[i])
            if weapon_id:
                counts[weapon_id] = counts.get(weapon_id, 0) + 1
    return counts


def _ammo_for(instance: Instance) -> dict[tuple[int, int], tuple[AmmoEntry, ...]]:
    """(unit_id, weapon_id) -> 弹药条目。"""
    table = instance.table(_T_WEAPON_AMMO)
    wa_unit = _column(table, "UnitId")
    wa_weapon = _column(table, "WeaponId")
    wa_ammo = _column(table, "AmmunitionId")
    wa_qty = _column(table, "Quantity")
    wa_order = _column_or_none(table, "Order") or [0] * table.row_count

    ammo_table = instance.table(_T_AMMO)
    ammo_by_id: dict[int, tuple[str, str, str]] = {}
    a_id = _column(ammo_table, "Id")
    a_name = _column(ammo_table, "Name")
    a_hud = _column(ammo_table, "HUDName")
    a_icon = _column(ammo_table, "HUDIcon")
    for i in range(ammo_table.row_count):
        ammo_by_id[_int(a_id[i])] = (_text(a_name[i]), _text(a_hud[i]), _text(a_icon[i]))

    grouped: dict[tuple[int, int], list[tuple[int, AmmoEntry]]] = {}
    for i in range(table.row_count):
        ammo_id = _int(wa_ammo[i])
        name, hud, icon = ammo_by_id.get(ammo_id, ("", "", ""))
        entry = AmmoEntry(
            ammo_id=ammo_id,
            name=name,
            hud_name=hud,
            icon_key=icon,
            quantity=_int(wa_qty[i]),
        )
        key = (_int(wa_unit[i]), _int(wa_weapon[i]))
        grouped.setdefault(key, []).append((_int(wa_order[i]), entry))

    return {
        key: tuple(entry for _, entry in sorted(items, key=lambda pair: pair[0]))
        for key, items in grouped.items()
    }


def _transports_for(
    instance: Instance, unit_id: int, specialization_id: int | None
) -> tuple[TransportEntry, ...]:
    """该单位的运输选项。

    走 TransportAvailabilities：它的 SpecializationAvailabilityId 指回
    SpecializationAvailabilities.Id。不指定专精时取并集。
    """
    avail_table = instance.table(_T_SPEC_AVAIL)
    av_ids = _column(avail_table, "Id")
    av_unit = _column(avail_table, "UnitId")
    av_spec = _column(avail_table, "SpecializationId")

    wanted = {
        _int(av_ids[i])
        for i in range(avail_table.row_count)
        if _int(av_unit[i]) == unit_id
        and (specialization_id is None or _int(av_spec[i]) == specialization_id)
    }
    if not wanted:
        return ()

    units_by_id = {unit.unit_id: unit for unit in _load_units(instance)}
    table = instance.table(_T_TRANSPORT_AVAIL)
    t_spec_avail = _column(table, "SpecializationAvailabilityId")
    t_unit = _column(table, "UnitId")

    seen: dict[int, int] = {}
    for i in range(table.row_count):
        if _int(t_spec_avail[i]) not in wanted:
            continue
        transport_id = _int(t_unit[i])
        seen.setdefault(transport_id, i)

    entries: list[TransportEntry] = []
    for transport_id, _ in sorted(seen.items(), key=lambda pair: pair[1]):
        unit = units_by_id.get(transport_id)
        entries.append(
            TransportEntry(
                unit_id=transport_id,
                name=unit.display_name if unit is not None else f"#{transport_id}",
                cost=unit.cost if unit is not None else 0,
                icon_key=unit.icon_key if unit is not None else "",
            )
        )
    return tuple(entries)


def _option_groups_for(instance: Instance, unit_id: int) -> tuple[OptionGroup, ...]:
    """自定义选项：Modifications(UnitId) -> Options(ModificationId)。"""
    mods = instance.table(_T_MODIFICATIONS)
    m_id = _column(mods, "Id")
    m_unit = _column(mods, "UnitId")
    m_name = _column(mods, "Name")
    m_ui = _column(mods, "UIName")
    m_thumb = _column_or_none(mods, "ThumbnailFileName") or [None] * mods.row_count
    m_order = _column_or_none(mods, "Order") or [0] * mods.row_count

    headers: dict[int, tuple[str, str, str, int, int]] = {}
    for i in range(mods.row_count):
        if _int(m_unit[i]) != unit_id:
            continue
        modification_id = _int(m_id[i])
        headers[modification_id] = (
            _text(m_name[i]),
            _text(m_ui[i]),
            _text(m_thumb[i]),
            _int(m_order[i]),
            i,
        )
    if not headers:
        return ()

    options = instance.table(_T_OPTIONS)
    o_mod = _column(options, "ModificationId")
    o_id = _column(options, "Id")
    o_name = _column(options, "Name")
    o_ui = _column(options, "UIName")
    o_cost = _column(options, "Cost")
    o_default = _column(options, "IsDefault")
    o_order = _column_or_none(options, "Order") or [0] * options.row_count

    choices: dict[int, list[tuple[int, int, OptionChoice]]] = {}
    for i in range(options.row_count):
        modification_id = _int(o_mod[i])
        if modification_id not in headers:
            continue
        choices.setdefault(modification_id, []).append(
            (
                _int(o_order[i]),
                i,
                OptionChoice(
                    option_id=_int(o_id[i]),
                    name=_text(o_name[i]),
                    ui_name=_text(o_ui[i]),
                    cost=_int(o_cost[i]),
                    is_default=o_default[i] is True,
                ),
            )
        )

    groups: list[OptionGroup] = []
    for modification_id, (name, ui_name, thumb, order, _row_index) in sorted(
        headers.items(), key=lambda pair: (pair[1][3], pair[1][4])
    ):
        items = sorted(choices.get(modification_id, []), key=lambda triple: (triple[0], triple[1]))
        groups.append(
            OptionGroup(
                modification_id=modification_id,
                name=name,
                ui_name=ui_name,
                icon_key=thumb,
                order=order,
                choices=tuple(triple[2] for triple in items),
            )
        )
    return tuple(groups)


def build_unit_detail(
    instance: Instance, unit_id: int, *, specialization_id: int | None = None
) -> UnitDetail | None:
    """整形出右侧详情面板所需的全部数据。单位不存在时返回 None。"""
    units = _load_units(instance)
    unit = next((item for item in units if item.unit_id == unit_id), None)
    if unit is None:
        return None

    availability: dict[int, tuple[int, int, int, int]] = {}
    avail_table = instance.table(_T_SPEC_AVAIL)
    av_unit = _column(avail_table, "UnitId")
    av_spec = _column(avail_table, "SpecializationId")
    av_max = [
        _column(avail_table, key)
        for key in (
            "MaxAvailabilityXp0",
            "MaxAvailabilityXp1",
            "MaxAvailabilityXp2",
            "MaxAvailabilityXp3",
        )
    ]
    for i in range(avail_table.row_count):
        if _int(av_unit[i]) != unit_id:
            continue
        spec_id = _int(av_spec[i])
        if specialization_id is not None and spec_id != specialization_id:
            continue
        availability[spec_id] = tuple(_int(column[i]) for column in av_max)  # type: ignore[assignment]
        break

    xp = availability.get(
        specialization_id if specialization_id is not None else next(iter(availability), -1),
        (0, 0, 0, 0),
    )

    carriers = _squad_carrier_counts(instance, unit_id)
    ammo_index = _ammo_for(instance)

    weapons_table = instance.table(_T_WEAPONS)
    w_id = _column(weapons_table, "Id")
    w_name = _column(weapons_table, "Name")
    w_hud = _column(weapons_table, "HUDName")
    w_icon = _column(weapons_table, "HUDIcon")
    weapon_info: dict[int, tuple[str, str, str]] = {}
    for i in range(weapons_table.row_count):
        weapon_info[_int(w_id[i])] = (_text(w_name[i]), _text(w_hud[i]), _text(w_icon[i]))

    weapons: list[WeaponEntry] = []
    for weapon_id, _order in _weapon_ids_for_unit(instance, unit_id):
        name, hud, icon = weapon_info.get(weapon_id, ("", "", ""))
        weapons.append(
            WeaponEntry(
                weapon_id=weapon_id,
                name=name,
                hud_name=hud,
                icon_key=icon,
                quantity=carriers.get(weapon_id, 0),
                ammo=ammo_index.get((unit_id, weapon_id), ()),
            )
        )

    return UnitDetail(
        card=_make_card(
            unit,
            xp[0],
            _card_cost(_cost_data(instance), unit_id),
            _card_name(_cost_data(instance), unit_id),
            *_card_keys(_cost_data(instance), unit_id),
        ),
        description=unit.description,
        model_key=unit.model_key,
        audio_preset=unit.audio_preset,
        dimensions=(unit.length, unit.width, unit.height),
        weight=unit.weight,
        stealth=unit.stealth,
        infantry_slots=unit.infantry_slots,
        role=unit.role,
        availability_xp=xp,  # type: ignore[arg-type]
        weapons=tuple(weapons),
        transports=_transports_for(instance, unit_id, specialization_id),
        option_groups=_option_groups_for(instance, unit_id),
    )
