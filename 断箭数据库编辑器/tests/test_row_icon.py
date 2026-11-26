"""一行该画哪张**真图标**（viewmodel/row_icon.py，零 Qt）。

用户口径（2026-10-01）：「我不是要 emoji，我是要图标，单位图标、武器图标、弹药图标、
改装图标这种」。这一份钉两件事：

1. 「表 → (族, 键列)」**只有一份出处**（core/assets/families.py::ROW_ICON_SOURCES），
   而且每一族的族名与键列都真的对得上登记表（抄错一个字母在这里就红）；
2. `row_icon` 对那一行给正确的 (族, 键)；没登记的 / 空键 / 没这一行都给 None
   —— 界面照口径**退回表 emoji**，不画空框。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.app.viewmodel.row_icon import RowIcon, row_icon
from ba_mod_maker.core.assets.families import (
    ENUM_ICON_SOURCES,
    FAMILIES,
    MOD_SLOT_ICON_KEYS,
    ROW_ICON_SOURCES,
    enum_icon_source,
    family,
    row_icon_source,
)
from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 四族各一个样本行（实测 Id）。
UNIT = 1  # BTR-82A
WEAPON = 2  # 2А72 30mm
AMMO = 361  # 5.45x39mm
OPTION = 757  # Stryker RV 的 Custom_Slot_Weapon_package 选项


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


def _key_of(instance, table: str, row_id: int, column: str) -> str:
    target = instance.table(table)
    id_index = target.column_index("Id")
    index = target.column_index(column)
    return next(row[index] for row in target.rows if row[id_index] == row_id)


def test_every_mapping_entry_matches_the_registry() -> None:
    """映射里的**族名与键列**都要与 FAMILIES 登记表对得上（防漂移）。

    这就是"族名字面量只在 families.py 一份"的守卫：界面从这份映射取，抄错就红。
    """
    assert ROW_ICON_SOURCES, "映射是空的"
    assert len(FAMILIES) >= len(ROW_ICON_SOURCES)
    for table, (family_name, column) in ROW_ICON_SOURCES.items():
        entry = family(family_name)
        assert entry is not None, f"{table} 指向了没注册的族 {family_name!r}"
        assert entry.column == f"{table}.{column}", (table, entry.column, column)
        assert row_icon_source(table) == (family_name, column)


def test_known_tables_have_a_source_and_unknown_ones_do_not() -> None:
    assert row_icon_source("Units")[0] == "unit_labels"
    assert row_icon_source("Weapons") == ("weapon_icons", "HUDIcon")
    assert row_icon_source("Ammunitions") == ("ammo_icons", "HUDIcon")
    assert row_icon_source("Options") == ("option_pictures", "OptionPicture")
    # Modifications 走的是**枚举那条路**（Type -> 常量表），不是"整列一个键"：
    # 它没有可用的键列（ThumbnailFileName 全表为空），但**有图**（按 ModificationType 选）。
    assert row_icon_source("Modifications") is None
    assert enum_icon_source("Modifications") is not None
    assert row_icon_source("Turrets") is None
    assert row_icon_source("没有这张表") is None


def test_every_enum_entry_points_at_a_registered_family() -> None:
    """枚举那条路：族要注册过，而且**族的 keys 恰好就是那张表会用到的图**（防漂移）。"""
    assert ENUM_ICON_SOURCES, "枚举映射是空的"
    for table, (family_name, _column, keys, fallback) in ENUM_ICON_SOURCES.items():
        entry = family(family_name)
        assert entry is not None, f"{table} 指向了没注册的族 {family_name!r}"
        assert entry.column == "", "枚举那条路的键不来自列，column 该留空"
        assert set(entry.keys) == {*keys.values(), fallback}, (table, entry.keys)


#: 改装槽样本：BT-3F（单位 212）的两个槽 —— 183 是 Turret(Type 3)、184 是 Armor(Type 5)。
MOD_TURRET = 183
MOD_ARMOR = 184


def test_modification_rows_get_the_emblem_by_type(instance) -> None:
    """改装槽的图标**按 ModificationType 查常量表**（键不在列里）。

    为什么不能读列：`ThumbnailFileName` 全表为空（615 行里 612 个 None + 3 个空串），
    游戏自己的 `Modifications` 类也只声明字段、UI 一处不读它。
    出处：反编译 `IconsLoader.LoadModificationButtonIcon(ModificationType modType, string name)`
    —— 全游戏只有两张按钮图：Armor 槽给盾牌，其余给通用那颗
    （用户 2026-10-01 的实机「改装栏」截图里，「装甲组件」那一行画的就是盾牌）。
    """
    family_name, column, _keys, fallback = enum_icon_source("Modifications")
    assert column == "Type"
    assert family_name == "mod_slot_icons"

    armor = row_icon(instance, "Modifications", MOD_ARMOR)
    assert armor is not None
    assert armor.family == family_name
    assert armor.key == MOD_SLOT_ICON_KEYS[5], armor.key  # Type 5 = Armor -> 盾牌
    assert armor.key.endswith("Modification Armor Slot Icon.png"), armor.key

    turret = row_icon(instance, "Modifications", MOD_TURRET)
    assert turret is not None and turret.key == fallback, turret

    # 全表扫一遍：**每一行都给得出键**（游戏也只有这两张，不存在"没图"），
    # 而且只会是那两张之一 —— 不许在这里拼地址。
    table = instance.table("Modifications")
    id_index = table.column_index("Id")
    allowed = {*MOD_SLOT_ICON_KEYS.values(), fallback}
    seen = set()
    for row in table.rows:
        icon = row_icon(instance, "Modifications", row[id_index])
        assert icon is not None and icon.family == family_name, row[id_index]
        assert icon.key in allowed, icon
        seen.add(icon.key)
    assert seen == allowed, f"两张图都该被用到，实际只用了 {seen}"


def test_row_icon_reads_that_rows_key_column(instance) -> None:
    for table, row_id, column in (
        ("Units", UNIT, "ThumbnailFileName"),
        ("Weapons", WEAPON, "HUDIcon"),
        ("Ammunitions", AMMO, "HUDIcon"),
        ("Options", OPTION, "OptionPicture"),
    ):
        want = _key_of(instance, table, row_id, column)
        assert want, f"样本 {table}#{row_id} 的 {column} 是空的，换一行样本"
        icon = row_icon(instance, table, row_id)
        assert icon is not None, (table, row_id)
        assert icon == RowIcon(ROW_ICON_SOURCES[table][0], want)


def test_no_row_no_instance_or_unmapped_table_gives_none(instance) -> None:
    assert row_icon(instance, "Units", 99999999) is None  # 没这一行
    assert row_icon(None, "Units", UNIT) is None  # 没实例
    assert row_icon(instance, "Units", 0) is None  # Id 非正
    assert row_icon(instance, "SquadMembers", 1) is None  # 这张表没登记
    assert row_icon(instance, "Turrets", 18) is None  # 也没登记
    assert row_icon(instance, "Modifications", 99999999) is None  # 这条路也没这一行


class _FakeTable:
    def __init__(self, columns: tuple[str, ...], rows: list) -> None:
        self.columns = columns
        self.rows = rows

    def column_index(self, name: str) -> int:
        # 与真的 core.data.dataset.Table 同一契约：列名不存在抛 **KeyError**（不是 ValueError）。
        try:
            return self.columns.index(name)
        except ValueError:
            raise KeyError(f"假表不存在列 {name}") from None


class _FakeInstance:
    """只够 row_icon 用（table_names + table + 一张假表）。"""

    def __init__(self, table: _FakeTable) -> None:
        self.table_names = ("Units",)
        self._table = table

    def table(self, name: str) -> _FakeTable:
        return self._table


def test_empty_whitespace_or_missing_column_gives_none() -> None:
    """键是空串/空白 -> None；**这一版数据里没有那一列**也不许炸。"""
    table = _FakeTable(("Id", "ThumbnailFileName"), [[7, ""], [8, "   "], [9, "KEY_9"]])
    fake = _FakeInstance(table)
    assert row_icon(fake, "Units", 7) is None
    assert row_icon(fake, "Units", 8) is None  # 只有空白也算空
    assert row_icon(fake, "Units", 9) == RowIcon("unit_labels", "KEY_9")
    missing = _FakeInstance(_FakeTable(("Id",), [[7]]))
    assert row_icon(missing, "Units", 7) is None
