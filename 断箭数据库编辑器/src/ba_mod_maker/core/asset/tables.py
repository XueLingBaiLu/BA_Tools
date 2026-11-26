"""24 张表的**权威清单**（零 Qt）。

三套名字必须同时说清楚，否则迟早漂移：

| 名字 | 出现在哪 | 例子 |
|---|---|---|
| **字段名** field | 容器对象里的字段 | `Units`、`AbilitiesJson`、`FlyPresetsJson` |
| **表名** table | 数据表本体 / 界面 / 校验 | `Units`、`Abilities`、`FlyPresets` |
| **条目名** zip_entry | `BA_Mod.zip` 与磁盘 JSON 文件名 | `Units.json`、`PlaneFlyPresets.json`
| **游戏读的键** alias | 只写在包里、给游戏查表用 | `PlaneFlyPreset.json`（**无 s**）| |

实测要点（都踩过）：

1. **字段名大多带 `Json` 后缀，`Units` 例外**（它就叫 `Units`）。
2. **表名 `FlyPresets` 的条目名是 `PlaneFlyPresets.json`** —— 唯一一处不一致。
   写成 `FlyPresets.json` 会被游戏**静默忽略**（不报错、不生效）。
   `DataBaseSourceData` 的属性叫 `PlaneFlyPresets`，而 `DataBaseCompiled` 的字段叫
   `FlyPresetsJson`，中间那层名字换过一次。
   ❗ **但这张表还要再写一份 `PlaneFlyPreset.json`（无 s）** —— 游戏读包时不按属性名找，
   而是按**模型类型名** `typeof(T).Name` 找，那个类型叫 `Table<PlaneFlyPreset>`。
   只写复数 = 这条被静默跳过 = 飞行预设表是空的 = `LoadMobility` NRE = **整个军械库空白**
   （2026-10-02 实机踩到）。见下面 `ZIP_ENTRY_ALIASES`。
3. **磁盘文件名 = 条目名**（实测 `DataBaseCompiled/<pid>/*.json` 与 BA_Mod.zip 条目逐一对应）。

这个元组是**单一事实源**：字段序 = 容器里的字段顺序 = 游戏声明的顺序。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple


class TableNaming(NamedTuple):
    """一张表的三套名字。"""

    field: str
    """容器对象里的字段名。决定字段顺序。"""

    table: str
    """表名。数据表本体、界面、校验规则都用它。"""

    @property
    def zip_entry(self) -> str:
        """`BA_Mod.zip` 的条目名，同时也是磁盘上的 JSON 文件名。"""
        return ZIP_ENTRY_OVERRIDES.get(self.table, f"{self.table}.json")

    @property
    def alias_entries(self) -> tuple[str, ...]:
        """除主条目外还要写进包的条目名（内容相同；见 `ZIP_ENTRY_ALIASES`）。"""
        return ZIP_ENTRY_ALIASES.get(self.table, ())


#: 表名 -> 条目名。**只有一处例外**，其余都是 `<表名>.json`。
ZIP_ENTRY_OVERRIDES = {"FlyPresets": "PlaneFlyPresets.json"}

#: 表名 -> 还要**额外再写一份**的条目名（内容与主条目逐字节相同）。
#:
#: 依据（全是实测/反汇编，别再推翻）：
#:
#: - `DataBaseSourceData.ReadTablesFromZip`：条目名去掉 `.json` 后去
#:   `_tableTypesByName` 里查，**查不到就 `continue`（静默跳过）**——ISIL 5524/5556；
#: - `DataBaseSourceData.GetTableRows<T>`：取行时用的键是 `typeof(T).Name`
#:   （`Type.GetTypeFromHandle` → `get_Name`）——ISIL 5939-5962；
#: - 这批表的**模型类型名**里只有一处与属性名不同：`Table<PlaneFlyPreset>`（**单数**）
#:   vs 属性 `PlaneFlyPresets`（复数）——cpp2il `DataBaseSourceData.cs` 字段表；
#: - 工坊里那份**已知能被游戏读**的 db.zip（`kb/04` §5.3，25 个条目）就是把
#:   `PlaneFlyPresets.json` 与 `PlaneFlyPreset.json` **两个都写、内容逐字节相同**；
#: - 实机症状（2026-10-02，`GameLogs/Gamelog__2026_10_02__15_18.log:114`）：
#:   `LoadUnits.LoadMobility` NRE → `DataBaseService.Load()` 起不来 →
#:   `GetAllUnits()` NRE → **军械库一张卡都没有**。
#:
#: 游戏对不认识的条目一律静默跳过，所以多写这一份**没有副作用**；
#: 不写则那张表是空的（游戏是"缺哪张就不加载哪张"）。
ZIP_ENTRY_ALIASES: dict[str, tuple[str, ...]] = {"FlyPresets": ("PlaneFlyPreset.json",)}

#: 24 张表，顺序 = 游戏声明的顺序 = 容器里的字段顺序。
#: 来源：`DataBaseCompiled/<pid>/_manifest.json`，两个实例一致。
TABLES: tuple[TableNaming, ...] = (
    TableNaming("Units", "Units"),
    TableNaming("AbilitiesJson", "Abilities"),
    TableNaming("UnitAbilitiesJson", "UnitAbilities"),
    TableNaming("AmmunitionsJson", "Ammunitions"),
    TableNaming("ArmorsJson", "Armors"),
    TableNaming("MobilityJson", "Mobility"),
    TableNaming("FlyPresetsJson", "FlyPresets"),
    TableNaming("UnitPropulsionsJson", "UnitPropulsions"),
    TableNaming("CountriesJson", "Countries"),
    TableNaming("TurretsJson", "Turrets"),
    TableNaming("TurretUnitsJson", "TurretUnits"),
    TableNaming("WeaponsJson", "Weapons"),
    TableNaming("TurretWeaponsJson", "TurretWeapons"),
    TableNaming("WeaponAmmunitionsJson", "WeaponAmmunitions"),
    TableNaming("SensorUnitsJson", "SensorUnits"),
    TableNaming("SensorsJson", "Sensors"),
    TableNaming("SquadMembersJson", "SquadMembers"),
    TableNaming("SquadWeaponsJson", "SquadWeapons"),
    TableNaming("ModificationsJson", "Modifications"),
    TableNaming("OptionsJson", "Options"),
    TableNaming("UnitArmorsJson", "UnitArmors"),
    TableNaming("SpecializationAvailabilitiesJson", "SpecializationAvailabilities"),
    TableNaming("SpecializationsJson", "Specializations"),
    TableNaming("TransportAvailabilitiesJson", "TransportAvailabilities"),
)

#: 字段名，按容器里的顺序。
FIELD_NAMES: tuple[str, ...] = tuple(t.field for t in TABLES)

#: 表名，按容器里的顺序。
TABLE_NAMES: tuple[str, ...] = tuple(t.table for t in TABLES)

#: 条目名，按容器里的顺序。
ENTRY_NAMES: tuple[str, ...] = tuple(t.zip_entry for t in TABLES)

#: 表名 -> 命名信息。
BY_TABLE: dict[str, TableNaming] = {t.table: t for t in TABLES}

#: 条目名 -> 命名信息。**含别名条目** —— 读包时那个单数名字也要算"我们认得的"，
#: 否则它会被当成"别人的未知条目"原样带回去，包里同一份内容出现两次。
BY_ENTRY: dict[str, TableNaming] = {
    **{t.zip_entry: t for t in TABLES},
    **{name: t for t in TABLES for name in t.alias_entries},
}

#: 除 24 张主条目外还要写进包的别名条目名（按声明顺序）。
ALIAS_ENTRY_NAMES: tuple[str, ...] = tuple(n for t in TABLES for n in t.alias_entries)


def package_entry_names() -> tuple[str, ...]:
    """**真正写进 BA_Mod.zip 的**条目名（主条目 + 紧跟其后的别名条目），按声明顺序。"""
    return tuple(n for t in TABLES for n in (t.zip_entry, *t.alias_entries))


def table_of(entry: str) -> str | None:
    """条目名 -> 表名。不认得就返回 `None`（游戏对未知条目是**静默跳过**）。"""
    base = entry.rsplit("/", 1)[-1]
    naming = BY_ENTRY.get(base)
    return naming.table if naming else None


# --------------------------------------------------------------------------- #
# 表名别名：同一张表在不同数据源里的两种写法
# --------------------------------------------------------------------------- #


def _build_table_aliases() -> dict[str, tuple[str, ...]]:
    """表名 -> **同一张表在别的数据源里可能叫什么**（两个方向都登记）。

    为什么需要：`FlyPresets` 的条目名是 `PlaneFlyPresets.json`，而**按文件名建表**的
    数据源（`DataBaseCompiled/<pid>/`，schema 的键就是文件干）会把这张表叫
    `PlaneFlyPresets` —— 而外部库（`BA_Mod.zip`）那一套叫 `FlyPresets`。两套都得认，
    否则「换个数据源打开」就会在查这张表时 KeyError。

    实测（用户报"战机侧的东西都显示不出来"）：他打开的数据源里没有 `PlaneFlyPresets`，
    而固定翼的【前进速度】【回转半径】两格都挂在飞行预设那一行上 —— 一 KeyError
    就是**整张战机卡片装配失败**，界面停在上一个单位上。
    """
    aliases: dict[str, list[str]] = {}
    for naming in TABLES:
        stem = naming.zip_entry[: -len(".json")]
        if stem == naming.table:
            continue
        aliases.setdefault(naming.table, []).append(stem)
        aliases.setdefault(stem, []).append(naming.table)
    return {name: tuple(values) for name, values in aliases.items()}


#: 见 `_build_table_aliases` 的注释。目前只有 `FlyPresets` <-> `PlaneFlyPresets` 一对。
TABLE_ALIASES: dict[str, tuple[str, ...]] = _build_table_aliases()


def resolve_table_name(name: str, available: Iterable[str]) -> str | None:
    """把表名归一到 `available` 里**真实存在**的那一个；都没有则 None。

    `available` 传数据源现成的表名（`Instance.table_names` / `_schema.json` 的键）。
    """
    names = set(available)
    if name in names:
        return name
    for alias in TABLE_ALIASES.get(name, ()):
        if alias in names:
            return alias
    return None
