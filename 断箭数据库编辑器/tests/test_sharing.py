"""共享度索引的测试（零 Qt，用真实 DataBaseCompiled）。

期望值直接取 docs/设计-单位卡片复刻.md 第六节那张实测表（以 645 为例）。
那张表是上一轮手工查询量出来的，这一轮换成代码算，两边必须对得上。
"""

from __future__ import annotations

import time

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.sharing import build_sharing_index

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"

#: 设计实测：645 卡片上用到的那些模板行，各自被多少个单位共用。
#: (表, 行 Id, 文档里的名字, 被几个单位共用)
DESIGN_TABLE: tuple[tuple[str, int, str, int], ...] = (
    ("Sensors", 23, "Medium Plus Optics", 142),
    ("Mobility", 190, "Infantry 18kmh Airdrop", 79),
    ("Ammunitions", 21, "InfGrenade 40x102 GP30", 32),
    ("Weapons", 122, "AK-74RMO", 16),
    ("Ammunitions", 104, "PG-7VL", 7),
    ("Weapons", 32, "RocketLauncher RPG7V (PG7-L)", 6),
    ("Armors", 281, "Armor Infantry Elite 5Men", 2),
)


@pytest.fixture(scope="module")
def index():
    return build_sharing_index(load_dataset(DATASET_ROOT).instance(INSTANCE_ID))


@pytest.mark.parametrize("table,row_id,label,want", DESIGN_TABLE)
def test_sharing_matches_the_design_doc(index, table, row_id, label, want) -> None:
    assert index.of(table, row_id) == want, label


def test_link_rows_are_exclusive(index) -> None:
    # 关联行天然只属于一个单位：改它不影响别人
    assert index.of("SquadMembers", 1466) == 1
    assert index.of("WeaponAmmunitions", 3602) == 1
    assert index.of("SensorUnits", 1401) == 1
    assert index.of("UnitArmors", 1185) == 1
    assert not index.is_shared("UnitArmors", 1185)


def test_unknown_row_is_zero_not_a_guess(index) -> None:
    """查不到就返回 0（不猜）。界面靠它决定要不要标共享、能不能直接改。"""
    assert index.of("Sensors", 999999) == 0
    assert index.of("没有这张表", 1) == 0
    assert not index.is_shared("Sensors", 999999)


def test_turret_weapons_are_counted_through_turret_units(index) -> None:
    """TurretWeapons 自己不带 UnitId，要经 TurretUnits 绕一跳。

    ⚠️ **这条用例曾经是假绿的**：它只断言"存在被 3 个以上单位共用的武器"，
    而 AK-74RMO 这类**步兵**武器走的是 SquadWeapons 那条直连边 —— 于是
    "经炮塔那一跳"整条路径坏了也照样通过（见下面那条逐个数对齐的用例）。
    """
    shared_weapons = [
        row_id
        for (table, row_id), users in index._users.items()
        if table == "Weapons" and len(users) > 3
    ]
    assert shared_weapons, "一个被 3 个以上单位共用的武器都没有"


def test_vehicle_weapons_are_counted_through_turrets(index) -> None:
    """车辆武器那一跳：Weapons ← TurretWeapons ← TurretUnits，**逐个数对齐**。

    期望值在本用例里独立复算（直接走那三张表），不写死数字。

    背景（实测踩过）：`build_sharing_index` 的 via 分支曾经拿 links 的**目标列**
    （TurretWeapons.WeaponId）去查用**中间表主键**（TurretId）建的映射，键永远对不上，
    于是所有车辆武器的共享度恒为 0 或 1。上面那条用例抓不到它，
    因为它的样本全是步兵武器。这条用例的存在就是为了堵住这个洞。
    """
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)

    def col(table: str, name: str) -> int:
        return {n: i for i, n in enumerate(instance.table(table).columns)}[name]

    turret_id, turret_unit = col("TurretUnits", "TurretId"), col("TurretUnits", "UnitId")
    units_of_turret: dict[int, set[int]] = {}
    for row in instance.table("TurretUnits").rows:
        units_of_turret.setdefault(row[turret_id], set()).add(row[turret_unit])

    tw_turret, tw_weapon = col("TurretWeapons", "TurretId"), col("TurretWeapons", "WeaponId")
    want: dict[int, set[int]] = {}
    for row in instance.table("TurretWeapons").rows:
        want.setdefault(row[tw_weapon], set()).update(units_of_turret.get(row[tw_turret], set()))

    weapon_id = max(want, key=lambda key: len(want[key]))
    assert len(want[weapon_id]) > 10, "样本武器太小，测不出这一跳"
    got = index.units("Weapons", weapon_id)
    assert want[weapon_id] <= got, "经炮塔那一跳丢单位了"
    assert index.of("Weapons", weapon_id) >= len(want[weapon_id])


def test_shared_units_are_real_units(index) -> None:
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    columns = {name: i for i, name in enumerate(instance.table("Units").columns)}
    units = {row[columns["Id"]] for row in instance.table("Units").rows}
    users = index.units("Sensors", 23)
    assert len(users) == 142
    assert users <= units, "共享度里混进了不是 Units.Id 的东西"


def test_index_is_cheap_enough_to_build_on_open() -> None:
    """开屏要建一次：整表扫一遍，不能是秒级。"""
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    start = time.perf_counter()
    build_sharing_index(instance)
    assert time.perf_counter() - start < 1.0
