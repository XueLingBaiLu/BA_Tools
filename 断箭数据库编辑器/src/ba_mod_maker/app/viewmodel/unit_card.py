"""单位卡片的数据装配（零 Qt）。

卡片不是"读一行"，是**沿选项图合成**出来的。三条合成关系（全部来自反编译）：

1. **传感器** <- Options.MainSensorId / ExtraSensorId：ApplyCurrentMods
   （UnitsInfoService VA 0x180707A80）把 unit.Sensors **整个替换**（是赋值不是 Add，
   基础表被丢掉）；再从改写后的列表取 Max。
2. **成本** <- Units.OriginalCost + Sum(已选选项 Cost)（同一个方法 ISIL 218-254）。
3. **改型面板** <- Options.ReplaceUnitId：变体单位自己的 Modifications 是空的。
   实测：645 'VDV Dsh 2' 的「小队装备配置」来自单位 **108** 的 Modification 743，
   由 option 3129（UIName=Custom_Option_Thermobaric_launcher -> 温压火箭弹）替换而来。
   不走这条图，变体单位的卡片就是空的。

数值行是 **(图标, 取值函数, 适用条件) 的列表**，按单位逐条筛 —— **不能按单位类型写死模板**：
BMD-2 与 9K51 Grad 同为载具，一个 8 格一个 7 格（Grad 压根没有"座位"那一格，
由 Units.GetInfantrySlots() > 0 决定，不是"值为 0 就不显示"）。
上限 InfocardConfig.MAX_STATS_WITH_INITIAL_SPACING = 8，超了把间距从 14 压到 4。

证据等级见 docs/研究-单位卡片公式.md；本模块每条公式都标了 VA / 偏移出处。
**未解的留钩子（PENDING_DETAIL_ROWS），不填假值。**

❗ 本模块不得 import 任何 Qt 符号（tests/test_core_layering.py 用 AST 扫）。
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ba_mod_maker.core.asset.tables import resolve_table_name
from ba_mod_maker.core.assets.families import STAT_ICON_KEYS, TARGET_ICON_KEYS
from ba_mod_maker.core.assets.localization import Localization, load_localization
from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.edit.foreign import display_name as foreign_name
from ba_mod_maker.core.edit.refs import TURRET_SLOT_COLUMNS, CellRef
from ba_mod_maker.core.edit.sharing import (
    EDGES,
    LINK_TABLES,
    SharingIndex,
    build_sharing_index,
)

# --------------------------------------------------------------------------- #
# 常量（全部从游戏 assets / 反编译读出，不是猜的）
# --------------------------------------------------------------------------- #

#: InfocardConfig (data.unity3d / globalgamemanagers.assets / pathID 52160 @0x18)
#: 用户口径的"距离一律 ×2"就是它 —— 是配置项，不是硬编码。
EFFECTIVE_RANGE_MULTIPLIER = 2.0
#: InfocardConfig.RoundDigits @0x20 -> 浮点行格式 "0.##"
ROUND_DIGITS = 2
#: InfocardConfig.MAX_STATS_WITH_INITIAL_SPACING：数值位最多 8 个，超过压缩间距。
MAX_STATS_PER_LINE = 8
#: 数值行间距（0x1861AB908 / 0x1861AABA0）：正常 14，挤不下时 4。
#: ❗ 那两个数是**实机的物理像素**，和卡片尺寸同一个坑（见 docs/交接-单位卡片GUI.md §9.1）：
#: 直接当逻辑像素用会宽 1.5 倍 —— 8 格那一行就挤不下、掉到第二行去。
#: 图标放大到 22 逻辑 + 每格留 2 的内边距之后，格与格之间只要 2/0 就够了
#: （实机那一排 8 格占 329 逻辑；我们 8 格 = 8×37 + 7×2 = 310，一行放得下还留余量）。
STAT_SPACING_WIDE = 2
STAT_SPACING_TIGHT = 0

#: GameConfig (pathID 52089)
RESURRECT_DELAY_FLOOR = 420.0  # 0x1B4
RESURRECT_DELAY_MULTIPLIER = 1.5  # 0x1B8
REPURCHASE_DELAY_FLOOR = 30.0  # 0x184
REPURCHASE_DELAY_MULTIPLIER = 0.5  # 0x188
MAXIMUM_REFUND_PERCENT = 90.0  # 0x1A8
#: 0x180 —— **实测为 0**，所以"补给耗时"那一路是死代码（maxResupplyTime × 0）。
REFUND_LOST_AMMO_TIME_MULTIPLIER = 0.0
PLANES_REFUND_FLOOR = 60.0  # 0x18C
PLANES_REFUND_HP_MULTIPLIER = 0.5  # 0x190
PLANES_REFUND_AMMO_K = 1.0  # 0x194
PLANES_REFUND_AMMO_P = 5.0  # 0x198
PLANES_DEATH_IS_MULTIPLIER = True  # 0x19C
PLANES_DEATH_REFUND_MULTIPLIER = 2.5  # 0x1A0
PLANES_DEATH_PENALTY = 0.0  # 0x1BC

#: SupplyConfig (pathID 52170) -> GetAmmoResupplyMultiplier = 4/1000*10 = 0.04
SUPPLY_AMMO_COST_MULTIPLIER = 4.0  # 0x24
SUPPLY_VALUE_BOUGHT = 10.0  # 0x1C
SUPPLY_EXCHANGE_RATE = 1000.0  # 0x20

#: LoadUnits 给步兵班算重量的系数：imul eax,[rax+18h],7Dh（0x7D = 125）。
#: 出处：_work/isil/.../DataBase/LoadUnits.txt 第 1442-1443 行 ——
#: SquadMembers.Count × 125 写回 [unit+94h]。数据库里步兵的 Weight 列是 1，
#: **卡片上那个 625 是这里算出来的**（载具才直读 Weight 列）。
INFANTRY_WEIGHT_PER_MEMBER = 125

#: Units.Type（dump.cs UnitType）
TYPE_INFANTRY = 2
TYPE_VEHICLE = 4
TYPE_HELICOPTER = 8
TYPE_AIRCRAFT = 16
TYPE_SHIP = 32

#: Units.CategoryType（UnitCategoryTypeUI）；6 = Aircrafts，视野那一格取高空。
CAT_INFANTRY = 1
CAT_AIRCRAFTS = 6

#: RefundSystem.GetPurchaseDelay 的 options 位（RefundTimeOptions）
REFUND_IS_DEAD = 1
REFUND_UNTOUCHED = 2
REFUND_OUT_OF_AMMO = 4

#: Options.Turret0Id..Turret20Id —— 21 个槽位（全单位共用一张表，各改型组不互相覆盖）。
#: 列名只有一份出处：`core/edit/refs.py::TURRET_SLOT_COLUMNS`（这里是别名，
#: 老调用方与用例仍按 TURRET_SLOTS 取）。
TURRET_SLOTS: tuple[str, ...] = TURRET_SLOT_COLUMNS

# --------------------------------------------------------------------------- #
# 枚举 -> 本地化键（照抄 InfoCardLocalization 里的跳转表，不是"小写化"猜的）
# --------------------------------------------------------------------------- #

#: WeaponType -> ui_enum_weapontype_*
#: 出处：InfoCardLocalization.txt 2793-2928（跳转表 0x5212A8 / 0x5212D0 / 0x5212EC / 0x521304）。
#: ⚠️ 键名**不是**成员名的小写：LightMachineGun -> machinegun_light、
#: GrenadeLauncher -> gl、RPGReloadable -> rpg、MainGun -> main_tankgun。
WEAPON_TYPE_KEYS: Mapping[int, str] = {
    1: "rifle",
    2: "marksman_rifle",
    3: "sniper_rifle",
    4: "antimaterial_rifle",
    5: "assault_rifle",
    6: "battle_rifle",
    25: "smg",
    26: "machinegun_light",
    27: "machinegun",
    28: "machinegun_heavy",
    29: "minigun",
    30: "autorifle",
    40: "shotgun",
    60: "gl",
    61: "gl",
    62: "rpg",
    63: "rpg",
    64: "hand_granade",
    80: "atgm",
    81: "atgm",
    82: "missile_cruise",
    83: "missile_ballistic",
    84: "missile_sead",
    85: "missile_manpad",
    86: "missile_sam",
    87: "missile_a2a_radar",
    88: "missile_a2a_ir",
    89: "missile_a2g",
    110: "main_tankgun",
    111: "howitzer",
    112: "mortar",
    113: "mlrs",
    114: "autocannon",
    115: "aa_gun",
    116: "plane_gun",
    140: "pod_rockets",
    141: "pod_gun",
    160: "bomb",
    161: "bomb_highdrag",
    162: "bomb_smart",
}

#: TrajectoryType -> ui_enum_trajectory_*
#: 出处：InfoCardLocalization.txt 2967-3011。DirectShot(10) -> direct 这类
#: "成员名 != 键名"的对应就写在跳转里，别自己拼。
TRAJECTORY_KEYS: Mapping[int, str] = {
    10: "direct",
    20: "artillery",
    30: "mortar",
    40: "mlrs",
    100: "missile",
    110: "missile",
    120: "missile",
    200: "cruise",
    300: "ballistic",
    400: "bomb",
    410: "bomb_highdrag",
}

#: Ammunitions.TargetType 的位。**照 dump.cs 的 TargetType（[Flags]，TypeDefIndex 1368）**：
#: None=0, Ground=1, Infantry=2, Vehicle=4, Helicopter=8, Aircraft=16, Ship=32,
#: Projectile=128, SEADMissile=256, CruiseMissile=512, BallisticMissile=1024。
#: ❗ 别信「1 是步兵」—— 那是旧列清单里的错说明（本轮已修）：**1 是 Ground，步兵是 2**。
TARGET_GROUND = 1
TARGET_INFANTRY = 2
TARGET_VEHICLE = 4
TARGET_HELICOPTER = 8
TARGET_AIRCRAFT = 16
TARGET_SHIP = 32
TARGET_PROJECTILE = 128
TARGET_SEAD_MISSILE = 256
TARGET_CRUISE_MISSILE = 512
TARGET_BALLISTIC_MISSILE = 1024
#: 四种「导弹类」目标（ISIL 213-266 那句 (TargetType & 0x780) == 0x780）。
#: 全中时游戏只画**一个**【目标 - 导弹】，否则逐条列出来。
TARGET_MISSILE_BITS = (
    TARGET_PROJECTILE | TARGET_SEAD_MISSILE | TARGET_CRUISE_MISSILE | TARGET_BALLISTIC_MISSILE
)

#: 详情行的证据等级。写进数据里，界面/测试/文档三处口径一致。
#: - 已确认 = 反编译读到实现 + 常量从 DLL 读出（有样本更好，没有也至少是"读出来的"）
#: - 强假设 = 反编译读到实现，但某个分支/pairing 还没钉死
EVIDENCE_CONFIRMED = "已确认"
EVIDENCE_ASSUMED = "强假设"

#: 这些武器显示【每秒伤害】而**不是**【伤害】—— 两者互斥（ISIL 366-427）。
#: 出处 WeaponHelper.CanDisplayDamagePerSecond（isil 213-236）：
#: type ∈ {1..6, 25..30, 40, 114, 115, 116, 141}。
DPS_WEAPON_TYPES = frozenset({1, 2, 3, 4, 5, 6, 25, 26, 27, 28, 29, 30, 40, 114, 115, 116, 141})

#: 【齐射时长】只对这三种武器发射（UnitInfoCard ISIL 615-644 的三个 Compare）：
#: 榴弹炮 Howitzer=111 / 迫击炮 Mortar=112 / 多管火箭炮 MLRS=113。
SALVO_WEAPON_TYPES = frozenset({111, 112, 113})

#: TrajectoryType.DirectShot —— 武器第一个弹种是它时，"弹匣容量/装填时间"那一支才可能被选中。
DIRECT_SHOT_TRAJECTORY = 10

#: 装填时间那种"min ≈ max 就只显示一个数"的相对容差：[0x1861AAEA0] = 1e-06，
#: 另有一个 8×[Mathf 静态首字段] 的下限项（数量级 1e-45，实际不起作用）。
RELOAD_TOLERANCE = 1e-06

#: WeaponHelper.RateOfFire 里的换算：VA 0x1861AAFAC = **60.0**（每秒 → 每分钟）。
RATE_OF_FIRE_PER_MINUTE = 60.0
#: 射速取整的步长：VA 0x1861AABA4 = **5.0**（RoundToNearestMultiple 的第二个参数）。
RATE_OF_FIRE_STEP = 5.0

#: UtilsClass.RoundToNearestMultiple 的步长：VA 0x1861AAB74 = **0.05**
#: （用 PE 节表把 RVA 映射到文件偏移才读得对；直接拿 RVA 当偏移读到的是别的数据 —— 踩过）
ROUND_STEP = 0.05

#: 展开了但**还没解出显示公式**的详情行。留钩子，不填假值（设计 §10.2）。
#: 解一条就把它从这里删掉、加进 DETAIL_SPECS，并给 verify_card_formulas.py 补样本。
#: ⚠️ **已经解出来的不再列在这里**（界面按 AmmoRow.detail / WeaponGroupRow.detail 画）：
#:   弹药侧：类型 / 有效射程 / 地面·低空·高空射程 / 伤害 / 爆炸半径 / 穿深 / 补给时间 / 补给值
#:   武器侧：瞄准时间 / 弹匣容量 / 装填时间
#: ⚠️ **动能伤害 / 爆炸伤害不是单独的行** —— 它们是【穿深】那一格的类型后缀
#:   （ArmorTargeted == Kinetic(1) ? 动能 : 爆炸），别再当成两行去找。
#: ❗ **现在是空的** —— 上一轮还挂着的四条（齐射时长 / 无视掩体 / 可被主动防御系统拦截 /
#: 补给消耗）都解出来了，靠的是把「本地化键 → InfoCardLocalization 的静态字段偏移」这条链
#: 走通（`_work/isil/.../InfoCardLocalization.txt` 里每个键都有一句
#: `LoadAddress r8, [rbp+NNN]`，再找 `Move [rax+OFF], reg` 就是它的字段偏移），
#: 拿偏移回 `AmmoInfoExpansionPanel` / `UnitInfoCard` 里搜读点即可：
#:
#: - 齐射时长（0x180）：`Type ∈ {榴弹炮 111, 迫击炮 112, 多管火箭炮 113}` ->
#:   `"{Short} / {Normal} / {Long}GroundAttackBurst"`；
#: - 无视掩体（0x288）：`IgnoreCover × 100 + "%"`，> 0 才发；
#: - 可被主动防御系统拦截（0x290）：`CanBeIntercepted` 为真时发一行，值就是 `"+"`；
#: - 补给消耗（0x208）：`SupplyCost × AmmoResupplyModifier + " pts"`，其中
#:   `AmmoResupplyModifier = supplyConfig.GetAmmoResupplyMultiplier()`（= 0.04）。
#:   ⚠️ 上一轮说"不能拿 0.04 顶替、那是另一回事"是**误判**：实测那个静态就是它的返回值。
#:
#: 仍然没做的同类行（列都在库里，只是行还没定位，先不显示）：
#: 可被拦截（`ui_infocard_ammo_interceptable`）、多目标（`weapon_multitarget`）、
#: 最大速度（`ammo_max_speed`）、攻顶 / 集束 / 燃烧弹 / 反辐射 / 激光制导 / 制导方式、
#: 需要雷达 / 已消音 / 移动时无法射击 / 自动装填 / 近距离作战。
PENDING_DETAIL_ROWS: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _int(value: Any, default: int = 0) -> int:
    """JSON 里可能是 null。**bool 不算 int**（否则 True 会变成 1）。"""
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    return float(value) if isinstance(value, (int, float)) else default


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def bankers_round(value: float) -> float:
    """C# Math.Round / Mathf.Round：银行家舍入（.5 进偶数）。"""
    floor = math.floor(value)
    delta = value - floor
    if delta > 0.5:
        return floor + 1
    if delta < 0.5:
        return floor
    return floor if floor % 2 == 0 else floor + 1


def round5(value: float) -> int:
    """(int)Math.Round(x) / 5 * 5 —— 先四舍五入，再向零截断到 5 的倍数。"""
    number = int(bankers_round(value))
    quotient = abs(number) // 5
    return quotient * 5 if number >= 0 else -(quotient * 5)


def fmt_number(value: float) -> str:
    """Single.ToString("0.##")（RoundDigits = 2）。整数不带小数点。"""
    if value % 1 == 0:
        return str(int(value))
    return f"{value:.{ROUND_DIGITS}f}".rstrip("0").rstrip(".")


def fmt_time(seconds: float) -> str:
    """TimeSpan.FromSeconds(x).ToString("m\\:ss") —— 截断到秒。"""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def rotation_radius(turn_rate: float, forward_speed: float) -> float:
    """AircraftHelper.GetRotationRadius（VA 0x18038EA80）：v*(tan t+1)/tan t。

    ⚠️ 是 **tan** 不是 cos（0x186179110）。t = MaxSpeedYaw × pi/180。
    """
    theta = math.radians(turn_rate)
    tangent = math.tan(theta)
    if tangent == 0:
        return 0.0
    return forward_speed * (tangent + 1.0) / tangent


# --------------------------------------------------------------------------- #
# 表访问
# --------------------------------------------------------------------------- #


class Rows:
    """一张表的**列名索引 + Id 索引 + 按列建一次的倒排索引**。

    为什么要包一层：unit_card 每次都要按 (UnitId) / (TurretId) / (UnitId, WeaponId)
    反复筛表，而 Table.column_index 是线性查找、全表扫描是 540 行级别。
    建一次索引，切单位就不会重算。

    ❗ `where()` 以前是"按 (列, 值) 缓存结果、第一次遇到这个值就**全表扫一遍**"。
    而 Options.ModificationId 有 2440 个不同的值 —— 同一张 417 行的表被扫了 2440 遍
    （实测 4020 次 `where`、1,518,177 次 `_int`，军械库那 0.70s 的大头）。
    现在改成**按列建一次** `值 -> 行` 的倒排索引（`_by_value`），之后是一次字典查。
    """

    __slots__ = ("table", "name", "_columns", "_ids", "_where", "_by_value")

    def __init__(self, table: Table) -> None:
        self.table = table
        self.name = table.name
        self._columns = {column: i for i, column in enumerate(table.columns)}
        self._ids: dict[int, Sequence[Any]] | None = None
        #: 自定义键缓存（`cache()` 与 `where2()` 的结果，键的语义与改前一致）。
        self._where: dict[tuple[Any, ...], tuple[Sequence[Any], ...]] = {}
        #: 列名 -> {列值: (行, ...)}。**第一次问这一列时建一次**，之后只查字典。
        self._by_value: dict[str, dict[Any, tuple[Sequence[Any], ...]]] = {}

    def index_of(self, column: str) -> int:
        try:
            return self._columns[column]
        except KeyError:
            raise KeyError(f"表 {self.name} 没有列 {column}") from None

    def has(self, column: str) -> bool:
        return column in self._columns

    def get(self, row: Sequence[Any], column: str, default: Any = None) -> Any:
        index = self._columns.get(column)
        if index is None or index >= len(row):
            return default
        value = row[index]
        return default if value is None else value

    def i(self, row: Sequence[Any], column: str, default: int = 0) -> int:
        """等价于 `_int(self.get(row, column), default)`，只是省掉两层函数调用。

        `i` 是卡片墙那条链上的热函数（一次军械库刷新 2.6 万次），
        内联掉 `get()` 的字典查 + None 判断，判定顺序与 `_int` 逐条对齐。
        """
        index = self._columns.get(column)
        if index is None or index >= len(row):
            return default
        value = row[index]
        if value is None or isinstance(value, bool) or not isinstance(value, int):
            return default
        return value

    def f(self, row: Sequence[Any], column: str, default: float = 0.0) -> float:
        return _float(self.get(row, column), default)

    def s(self, row: Sequence[Any], column: str, default: str = "") -> str:
        return _text(self.get(row, column)) or default

    def b(self, row: Sequence[Any], column: str) -> bool:
        return self.get(row, column) is True

    def by_id(self, row_id: int) -> Sequence[Any] | None:
        if self._ids is None:
            index = self.index_of("Id")
            self._ids = {_int(row[index]): row for row in self.table.rows}
        return self._ids.get(row_id)

    def _values_of(self, column: str) -> dict[Any, tuple[Sequence[Any], ...]]:
        """按列建一次倒排索引：`值 -> 该值的全部行`（**顺序 = 表内原始顺序**）。

        键按 `_int` 归一 —— 与旧的逐行 `_int(row[index]) == value` 逐字对齐：
        null / bool / 字符串都落到 `0` 那一格，`where(col, 0)` 照样查得到它们。
        列不存在时由 `index_of` 抛 KeyError（与改前一致）。
        """
        index = self._by_value.get(column)
        if index is None:
            at = self.index_of(column)
            bucket: dict[Any, list[Sequence[Any]]] = {}
            for row in self.table.rows:
                bucket.setdefault(_int(row[at]), []).append(row)
            index = {value: tuple(rows) for value, rows in bucket.items()}
            self._by_value[column] = index
        return index

    def where(self, column: str, value: Any) -> tuple[Sequence[Any], ...]:
        """该列等于 value 的全部行（**整列建一次索引**，之后是一次字典查）。

        查不到给空元组；顺序仍是表内顺序。
        """
        rows = self._values_of(column).get(value)
        return () if rows is None else rows

    def where2(self, a: str, av: Any, b: str, bv: Any) -> tuple[Sequence[Any], ...]:
        key = (a, av, b, bv)
        cached = self._where.get(key)
        if cached is None:
            left = self._values_of(a).get(av, ())
            right = self._values_of(b).get(bv, ())
            # 拿**短的那边**当候选、按另一列过滤 —— 表内顺序不会乱。
            if len(right) < len(left):
                at, other = self.index_of(a), av
                cached = tuple(row for row in right if _int(row[at]) == other)
            else:
                at, other = self.index_of(b), bv
                cached = tuple(row for row in left if _int(row[at]) == other)
            self._where[key] = cached
        return cached

    def all(self) -> Sequence[Sequence[Any]]:
        return self.table.rows

    def invalidate(self) -> None:
        """丢掉 Id 索引、**按列倒排索引**与筛结果缓存。

        ❗ **改过这张表之后必须调** —— 否则读到的还是改动前那份结果
        （实测踩过：插了一行 SquadMembers，卡片还显示 5 人）。
        按列索引是新的，这里必须一起丢 —— 漏了它就是"改了数卡片不动"那个老毛病。
        """
        self._ids = None
        self._where.clear()
        self._by_value.clear()

    def cache(self, key: tuple[Any, ...], builder: Callable[[], tuple[Sequence[Any], ...]]):
        """按自定义键缓存一次筛选（变体反查用）。"""
        cached = self._where.get(key)
        if cached is None:
            cached = builder()
            self._where[key] = cached
        return cached


# --------------------------------------------------------------------------- #
# 卡片上的引用
# --------------------------------------------------------------------------- #


#: 一个数的落点。定义在 core/edit/refs.py —— 卡片与编辑都要用，而 core 不许 import app。


@dataclass(frozen=True, slots=True)
class DetailRow:
    """展开态里的一行详情（标题 + 值）。**只放解出来的**，没解的不进来。

    evidence 写在数据里：调试图/测试/文档三处口径一致，谁也不用猜哪条是钉死的。
    """

    key: str
    label: str
    value: str
    hint: str = ""
    evidence: str = EVIDENCE_CONFIRMED


@dataclass(frozen=True, slots=True)
class AmmoTarget:
    """弹药能打的一种目标 —— 游戏里那一排小图标（AmmoInfoExpansionPanel.SetTargetTraits）。

    icon 是**地址键**（core/assets/families.TARGET_ICON_KEYS 里那条），界面拿它去
    infocard_targets 族取图；label 是游戏的本地化文案（"目标 - 载具"），当 tooltip 用
    —— 游戏那排也只有图没有字（SetTraitItem 的第二个参数就是 tooltip）。
    导弹那一项可能是多行（游戏把逐条名字用换行拼起来，见 ammo_target_traits）。
    """

    #: 位名（infantry / vehicle / helicopter / aircraft / missiles），诊断与测试用
    key: str
    icon: str
    label: str


@dataclass(frozen=True, slots=True)
class AmmoRow:
    """展开态里的一个弹种。"""

    ammo_id: int
    label: str
    icon: str
    trajectory: str
    #: 界面上的数字 = TotalAmmoCount × Ammunitions.HUDMultiplier（**已确认**）
    count: int
    #: 求和结果（未乘 HUDMultiplier），编辑面板要显示两者
    raw_total: int
    multiplier: int
    #: 这一弹种在本单位那些携弹行的落点 —— **AmmunitionId 列**（换弹种写这里）。
    #: 同组多把武器（一个班 5 把同一把枪）就有多个。
    refs: tuple[CellRef, ...] = ()
    #: 同一批行的 **Quantity 列**（改携弹量写这里）。
    quantity_refs: tuple[CellRef, ...] = ()
    #: 展开态的详情行（只含已解出的；清单见 PENDING_DETAIL_ROWS）
    detail: tuple[DetailRow, ...] = ()
    #: 能打什么（那排小图标）。TargetType 的 Ground / Ship 两位游戏里没有图标，
    #: 所以步枪那种 TargetType==1 的弹药这里是空的 —— 与游戏一致，不是漏画。
    targets: tuple[AmmoTarget, ...] = ()
    #: 这个弹种被多少个单位共用（改它要另存为新模板）
    shared_with: int = 0


@dataclass(frozen=True, slots=True)
class CompactCell:
    """简略模式那一行里的一格（游戏里三把武器并排对齐的那几列）。

    为什么要有 key：**跨武器行按同一个顺序排，列才对得齐** —— 这就是"并列"。
    text 为空 = 这把武器这一列没有值，但**格还占着位**，后面的列不会串位。
    label 是列标题（取自真正画出来的那一行，所以"每秒伤害/伤害"这种互斥列不会写错）。
    """

    key: str
    label: str
    text: str
    tooltip: str = ""


@dataclass(frozen=True, slots=True)
class WeaponGroupRow:
    """收起态里的一行武器。

    游戏是按 **首个弹种** 把武器分组的（UnitInfoCard b__109_4），每组一个按钮：
    显示组内第一把武器 + 行内的 xN（N = 组内武器数，步兵就是带这把枪的人数）。
    """

    group_key: int
    weapon_id: int
    label: str
    icon: str
    kind: str
    count: int
    weapons: tuple[int, ...]
    ammo: tuple[AmmoRow, ...]
    refs: tuple[CellRef, ...] = ()
    #: 武器行被多少个单位共用
    shared_with: int = 0
    #: 换弹种 / 加弹种的候选项（这把武器在库里的弹种）
    ammo_choices: tuple[AmmoChoiceRow, ...] = ()
    #: 武器自己的详情行（瞄准时间 / 弹匣容量 / 装填时间）
    detail: tuple[DetailRow, ...] = ()
    #: 展开态里还没解出公式的行（留钩子，不填假值）
    pending: tuple[str, ...] = PENDING_DETAIL_ROWS
    #: 简略模式那一行的格子（顺序 = COMPACT_COLUMNS，取自**第一个弹种**）
    compact: tuple[CompactCell, ...] = ()
    #: 这一把武器**所有弹种**能打的目标（并集，按游戏那排的固定顺序）——
    #: 简略模式画的就是它。见 weapon_targets() 的注释（只取第一个弹种会漏）。
    targets: tuple[AmmoTarget, ...] = ()


@dataclass(frozen=True, slots=True)
class CardStat:
    """数值行里的一格。value_ref 是这个数本身，link_ref 是"本单位指向它"的那一行。"""

    key: str
    #: InfocardConfig 的字段名（例 "Armor" / "HealthInfantry"），见设计 §3 的字段顺序。
    icon: str
    label: str
    value: str
    hint: str = ""
    value_ref: CellRef | None = None
    link_ref: CellRef | None = None
    #: 这个值被多少个单位共用（0 = 独占或算出来的）。改共享值要"另存为新模板"。
    shared_with: int = 0
    #: 人话备注（例："由 125 × 5 名成员算出"）。
    note: str = ""
    #: 证据等级。绝大多数数值行都被截图逐格复核过（已确认）；
    #: 只有"没有样本、只能从汇编推"的那几格标成强假设（例：船的前进速度取哪个字段）。
    evidence: str = EVIDENCE_CONFIRMED
    #: 这一格的**图标地址键**（families.STAT_ICON_KEYS 里那条）；空 = 这一格还没对上图，
    #: 界面就退回画文字标签（不填假值）。
    sprite: str = ""


@dataclass(frozen=True, slots=True)
class LinkedRow:
    """卡片上一块「能点进去看的那一行」（机动 / 传感器 / 运输）。

    界面重做定稿 §1 第 4 条：**卡上能点的一切都通向它的那一行**。
    所以卡片上这几块不只是显示名字，还要带着"这一行是谁"（表 + 行 Id）——
    点一下右栏就换成它的全部列。

    口径与别处一致：label 是**名字**（外键显示名），不是 Id。
    """

    table: str
    row_id: int
    label: str
    #: 右边那点小字（这一行是从哪儿来的 / 有几个）。
    note: str = ""
    #: **本单位哪一格的哪一列指着它**（外键那一格）。「换」= 改这一格；
    #: 「克隆一份」= 复制这一行，再把这一格指过去（core/edit/clone.py）。
    #: 没有它就只能改、不能换 —— 这一条是本轮用户点名要的。
    link: CellRef | None = None
    #: 这一行是谁给过来的（例：来自「SRAT」这个选项）—— 只有**被选项覆盖**的
    #: 那几块才有（设计稿块 5：下面那几块永远显示当前组合的结果，并注明来自哪个选项）。
    source: str = ""

    @property
    def ref(self) -> CellRef:
        return CellRef(self.table, self.row_id, "Id")

    @property
    def can_clone(self) -> bool:
        """能不能"克隆一份"。

        单位（Units）不行：那是**另一个单位**，不是本单位可以私有的模板行 ——
        复制一个单位出来没有任何意义（运输配对那一块就属于这种）。
        """
        if self.link is None or self.table == "Units":
            return False
        # 指向它的那一格**不能是它自己**：运输那张表是"配对行"（自己指向自己的
        # 专精可用性），复制它没有意义（用户报的"运输的逻辑是有问题的"）。
        return self.link.table != self.table


@dataclass(frozen=True, slots=True)
class AbilityChip:
    key: str
    label: str
    hint: str = ""
    #: 这一条能力落在 Abilities 的哪一行（0 = 没对上，界面就不给"去看这一行"）。
    row_id: int = 0


@dataclass(frozen=True, slots=True)
class AmmoChoiceRow:
    """换弹种 / 加弹种时的候选项。

    候选 = **这把武器在数据库里用过的那些弹种**（全库扫 WeaponAmmunitions 按 WeaponId），
    不是全部 630 种弹药：那样既选不动，也容易加出一条游戏压根不显示的携弹行
    （游戏显示哪些弹种由武器自己的 Ammunitions 列表决定，不是有行就显示）。
    """

    ammo_id: int
    label: str
    trajectory: str
    #: 本单位这把武器当前已经在用它（界面要标出来，别让人重复加）
    owned: bool = False


@dataclass(frozen=True, slots=True)
class SquadMemberRow:
    """步兵班里的一名成员（SquadMembers 一行）。

    一行 = 一个兵，带一件主武器 +（可选）一件副武器。卡片上那排武器就是按这些行
    **按人**收集出来的，所以改这里的枪，武器行与弹量都会跟着动。
    """

    member_id: int
    index: int
    primary_weapon_id: int
    special_weapon_id: int

    def ref(self, column: str) -> CellRef:
        """这一行某一列的落点（写值走 core/edit/changeset.py）。"""
        return CellRef("SquadMembers", self.member_id, column)

    @property
    def row_ref(self) -> CellRef:
        """整行的落点（删行用；Id 列是业务键）。"""
        return CellRef("SquadMembers", self.member_id, "Id")


@dataclass(frozen=True, slots=True)
class WeaponChoiceRow:
    """配枪下拉里的一项。候选 = 该单位 SquadWeapons 里那一批。

    为什么只用 SquadWeapons：规则引擎有一条不变量「SquadMembers 带的武器必须在
    同单位的 SquadWeapons 里」。给个 804 种武器的全表下拉，选完就违规了。
    """

    weapon_id: int
    label: str
    kind: str = ""


@dataclass(frozen=True, slots=True)
class SquadLoadout:
    """步兵班的编成：成员 + 可选武器。**加枪 / 换枪 / 5 人改 6 人**都靠它。"""

    unit_id: int
    members: tuple[SquadMemberRow, ...]
    weapon_choices: tuple[WeaponChoiceRow, ...]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True, slots=True)
class OptionChoiceRow:
    option_id: int
    label: str
    cost: int
    is_current: bool
    #: 选了它就**换成另一个单位**（Options.ReplaceUnitId）—— 改型，不是换装备。
    replaces_unit_id: int = 0
    #: 选项图片（Options.OptionPicture，实测是 Weapons/Icons 下那张，与武器图标同一批素材）。
    #: 游戏的自定义选项那一排就画它。空 = 这一条没有图。
    icon: str = ""


@dataclass(frozen=True, slots=True)
class OptionGroupRow:
    """底部「自定义选项」里的一组（对应一条 Modifications）。

    **槽内互斥**：一个槽里永远只有一项是"选中的"（`is_current`）——
    界面上点另一项 = 把选中挪过去（`exclusive_selection_writes`），不是叠加。
    """

    modification_id: int
    #: 这一组挂在哪个单位上。变体单位（如 645）这里会 **不是** 当前单位（是 108）。
    owner_unit_id: int
    label: str
    current_label: str
    current_cost: int
    #: 当前这一项自己的图片（界面上那一排的图标）；没有就空。
    current_icon: str = ""
    choices: tuple[OptionChoiceRow, ...] = ()
    #: 本组当前选择来自"指向当前单位的那个替换选项"（变体路径）。
    from_variant: bool = False
    #: 这个槽**属于本单位**吗（`Modifications.UnitId == 当前单位`）。
    #: False = 变体路径借来的槽（645 借 108 的 743）：卡片照旧画，
    #: 但**不给选** —— 在借来的槽上翻 `Options.IsDefault` 会一起改到槽的主人
    #: （任务书第 5 条：变体单位不要弄坏）。
    is_own_slot: bool = True

    @property
    def selected_option_id(self) -> int:
        """当前**选中**的那一项（槽内互斥，最多一项）；没有就是 0。"""
        for choice in self.choices:
            if choice.is_current:
                return choice.option_id
        return 0


# --------------------------------------------------------------------------- #
# 卡上那三块：21 个炮塔挂载槽 / 炮塔四层链 / 改装槽的互斥选项
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TurretSlotCell:
    """一个**炮塔挂载槽**（0..20）上现在装着什么。

    口径（设计稿块 10 + 用户明确要求）：21 个槽**全部列出来、一个都不折叠** ——
    空槽也是一格（turret_id == 0 是正常值，不是"没算出来"）。
    """

    slot: int
    turret_id: int
    label: str = ""

    @property
    def occupied(self) -> bool:
        return self.turret_id > 0

    @property
    def text(self) -> str:
        """格子上那串字（设计图是「7 MK82 SE x1」/「0 空」）。"""
        return f"{self.slot} {self.label}" if self.occupied else f"{self.slot} 空"

    @property
    def ref(self) -> CellRef | None:
        """这一格装着的那座炮塔（空槽没有落点）。"""
        return CellRef("Turrets", self.turret_id, "Id") if self.occupied else None


@dataclass(frozen=True, slots=True)
class ChainAmmo:
    """四层链最底下那一层：一种弹 —— 落在 **WeaponAmmunitions** 那一行上。

    设计稿块 8 对着的那张卡点的是 **Ammunitions**（弹种模板的 67 列），
    而数量 (Quantity) 在这一行上 —— 两个落点都留着：点名字走模板行，
    `link` 是"改数量 / 换弹种"的那一格。
    """

    row_id: int
    ammo_id: int
    label: str
    #: WeaponAmmunitions.Quantity（格子里那个数）
    quantity: int
    #: 卡片上那个数 = Quantity × Ammunitions.HUDMultiplier（与 AmmoRow.count 同口径）
    count: int
    #: 「换弹种」改的是这一格
    link: CellRef

    @property
    def ref(self) -> CellRef:
        """这一层点下去要落到的那一行（携弹行 —— 数量在这一行上改）。"""
        return CellRef("WeaponAmmunitions", self.row_id, "Id")


@dataclass(frozen=True, slots=True)
class ChainWeapon:
    """四层链第三层：一门武器（Weapons 那一行）。"""

    weapon_id: int
    label: str
    ammo: tuple[ChainAmmo, ...] = ()

    @property
    def ref(self) -> CellRef:
        return CellRef("Weapons", self.weapon_id, "Id")


@dataclass(frozen=True, slots=True)
class ChainTurret:
    """四层链第二层：一座**当前装着**的炮塔（Turrets 那一行）。

    slots = 它占了哪几个挂载槽（当前选项组合算出来的槽号，升序）。同一座炮塔常占
    好几个槽（实测 F-15E 的 Turret Empty 占 7..12 + 19）—— 这里只画**一行**，
    槽号写在行尾；逐格的那一份视图是 TurretSlotCell。
    """

    turret_id: int
    label: str
    full_label: str
    slots: tuple[int, ...]
    weapons: tuple[ChainWeapon, ...] = ()

    @property
    def ref(self) -> CellRef:
        return CellRef("Turrets", self.turret_id, "Id")

    @property
    def slot_text(self) -> str:
        """占了哪几个槽：「0」/「1–2」/「7–12 · 19」。"""
        return slot_range_text(self.slots)

    @property
    def note(self) -> str:
        slots = f"槽 {self.slot_text}" if self.slots else "不在挂载槽上"
        if not self.weapons:
            return f"{slots} · 空挂架"
        return f"{slots} · {len(self.weapons)} 件武器"


@dataclass(frozen=True, slots=True)
class OptionEffect:
    """一条改装选项**改了什么**里的一条（卡上那行灰字；能落到某一行的还可点）。

    table + row_id 非零时它指向**改了哪一行**（例：换成这套装甲 -> Armors#70）。
    """

    column: str
    label: str
    text: str
    table: str = ""
    row_id: int = 0

    @property
    def ref(self) -> CellRef | None:
        return CellRef(self.table, self.row_id, "Id") if self.table and self.row_id else None


@dataclass(frozen=True, slots=True)
class CardOptionRow:
    """卡上改装槽里的一**项**（槽内互斥：同槽最多一项 selected）。"""

    option_id: int
    label: str
    cost: int
    selected: bool
    effects: tuple[OptionEffect, ...] = ()
    #: 选了它 = 换成另一个单位（Options.ReplaceUnitId）—— 那是改型，不是换装备。
    replaces_unit_id: int = 0

    @property
    def cost_text(self) -> str:
        return f"+{self.cost}" if self.cost else "±0"

    @property
    def summary(self) -> str:
        """右边那串「它改了什么」（一行灰字）。"""
        return " · ".join(effect.text for effect in self.effects)

    @property
    def ref(self) -> CellRef:
        return CellRef("Options", self.option_id, "Id")


@dataclass(frozen=True, slots=True)
class CardOptionGroup:
    """卡上改装槽里的一个**槽**（Modifications 那一行）+ 槽内互斥的那几项。

    slots = **选中那一项**的 21 个炮塔挂载槽（0..20 全在，一个都不折叠）。
    一项都没选中（或那一条选项扫不出来）时是空元组 —— 界面据此不画那一排格子。
    """

    modification_id: int
    label: str
    options: tuple[CardOptionRow, ...] = ()
    selected_label: str = ""
    selected_cost: int = 0
    #: 这个槽属于本单位吗（变体借来的槽只给看不给选）
    is_own_slot: bool = True
    #: 这个槽挂在哪个单位上（`Modifications.UnitId`）。借来的槽靠它说清"是谁的槽" ——
    #: 实测 645 的那一组属于单位 108，卡上那句「借来的槽（属于单位 #108）」就是它。
    owner_unit_id: int = 0
    slots: tuple[TurretSlotCell, ...] = ()

    @property
    def selected(self) -> CardOptionRow | None:
        return next((row for row in self.options if row.selected), None)

    @property
    def occupied_slots(self) -> tuple[TurretSlotCell, ...]:
        return tuple(cell for cell in self.slots if cell.occupied)

    @property
    def ref(self) -> CellRef:
        return CellRef("Modifications", self.modification_id, "Id")


@dataclass(frozen=True, slots=True)
class ArmorZone:
    """「装甲图」里的一个部位 —— 实机右卡立绘四角那 4 块之一。

    每块是**两个数**：动能（Kin，白字）与破甲（Heat，橙字），部位名小字在上。
    顺序与摆位见 ARMOR_ZONE_SPECS / armor_zones()。
    """

    #: 部位键（top / rear / front / side）—— 界面按它决定压在哪个角、两个数是并排还是上下。
    key: str
    #: 部位名（游戏本地化 ui_infocard_armor_*，缺游戏时退回产品自带的中文）。
    label: str
    #: 动能装甲（Armors.KinArmor*）—— 实机是白字。
    kinetic: int
    #: 破甲（HEAT）装甲（Armors.HeatArmor*）—— 实机是橙字。
    heat: int
    #: 这四个数落在 Armors 的哪一行（点装甲图 = 去看那一行的 14 列）。
    row_id: int = 0
    #: 这一行是谁给过来的（缺省 = UnitArmors；被选项覆盖时是那条选项）。
    source: str = ""


@dataclass(frozen=True, slots=True)
class UnitCard:
    """一张单位卡片的全部数据。界面只排版，不再算。"""

    unit_id: int
    title: str
    subtitle: str
    cost: int
    cost_ref: CellRef | None
    #: 立绘 / 缩略图 / 模型 的地址键（与 ArmoryCard 同一套口径）
    portrait_key: str
    icon_key: str
    model_key: str
    #: CategoryType / Type —— 界面没有立绘时按它们画剪影
    category: int
    kind: int
    stats: tuple[CardStat, ...]
    abilities: tuple[AbilityChip, ...]
    weapons: tuple[WeaponGroupRow, ...]
    options: tuple[OptionGroupRow, ...]
    #: 「单位 › 炮塔 › 武器 › 弹种」四层链里**当前装着**的那几座炮塔（步兵为空）。
    #: 设计稿块 4/8：四层都能点，点了中栏换成那一行的全部列。
    turrets: tuple[ChainTurret, ...] = ()
    #: 卡上改装槽那一份：槽 › 互斥选项（选中的带 ●、右边标费用与"改了什么"）
    #: + **选中那一项的 21 个炮塔挂载槽**（一个都不折叠）。设计稿块 5 / 10。
    #: ❗ 与 options 是同一份数据的两种画法：那一份给军械库那张卡的下拉用。
    card_options: tuple[CardOptionGroup, ...] = ()
    #: 「装甲图」：4 个部位 × (动能, 破甲)，压在立绘四角（实机就是这样）。
    #: **空元组 = 不画**：非载具（步兵/直升机/战机/舰船）与 8 个数全零的载具都是空的。
    #: 口径见 armor_zones()。
    armor_zones: tuple[ArmorZone, ...] = ()
    #: 装甲（Armors 那一行）：顶/背/正/侧 + 生命值都在它里面。
    armor: LinkedRow | None = None
    #: 机动（Mobility 那一行）：速度 / 倒车 / 敏捷 / 两栖全在它里面。
    mobility: LinkedRow | None = None
    #: 传感器（Sensors 各一行，选项改写之后的）。
    sensors: tuple[LinkedRow, ...] = ()
    #: 运输配对（TransportAvailabilities 的行）。
    transports: tuple[LinkedRow, ...] = ()
    #: 步兵班的编成（加枪 / 换枪 / 加人减人）。非步兵是 None。
    loadout: SquadLoadout | None = None
    #: 本地化是不是降级了（读不到游戏包）。界面要明说。
    degraded: bool = False

    @property
    def stat_spacing(self) -> int:
        """数值行间距：满 8 格以上压缩（InfocardConfig 的 14 / 4）。"""
        return STAT_SPACING_WIDE if len(self.stats) <= MAX_STATS_PER_LINE else STAT_SPACING_TIGHT


# --------------------------------------------------------------------------- #
# 关系图索引
# --------------------------------------------------------------------------- #

_T_UNITS = "Units"
_T_SENSORS = "Sensors"
_T_SENSOR_UNITS = "SensorUnits"
_T_ARMORS = "Armors"
_T_UNIT_ARMORS = "UnitArmors"
_T_MOBILITY = "Mobility"
_T_UNIT_PROPULSIONS = "UnitPropulsions"
_T_SQUAD_MEMBERS = "SquadMembers"
_T_SQUAD_WEAPONS = "SquadWeapons"
_T_WEAPONS = "Weapons"
_T_AMMO = "Ammunitions"
_T_WEAPON_AMMO = "WeaponAmmunitions"
_T_MODIFICATIONS = "Modifications"
_T_OPTIONS = "Options"
_T_TURRETS = "Turrets"
_T_TURRET_UNITS = "TurretUnits"
_T_TURRET_WEAPONS = "TurretWeapons"
_T_ABILITIES = "Abilities"
_T_UNIT_ABILITIES = "UnitAbilities"
#: 表名用**规范名** `FlyPresets`（条目名 / 磁盘文件才是 `PlaneFlyPresets.json`）。
#: 这个常量会被塞进 `CellRef`（见下面的 speed_forward / turn_radius 两格），
#: 而 CellRef 里的名字会一路走到 `Changeset` -> `Instance.table(name)` ——
#: 名字写错就是「点那一格直接 KeyError」（`tests/test_table_naming.py` 有用例守着）。
_T_FLY_PRESETS = "FlyPresets"
_T_TRANSPORT_AVAIL = "TransportAvailabilities"


def _sharing_fields() -> frozenset[tuple[str, str]]:
    """共享度索引**真正读到的** (表, 列)。

    ❗ 从 core/edit/sharing.py 的 EDGES / LINK_TABLES **推**出来，不在这里另抄一份 ——
    照抄一份的话，那边加一条边而这里忘了改，症状就是"改了那一格共享度不更新"，
    而且没有任何东西会报错。推导口径与 build_sharing_index 的读法逐行对应：

        - 引用方表上：外键列 column、UnitId（unit_column）、经一跳时用的 via_key；
        - 中间表上：via 的那两列（主键 + UnitId）；
        - key_column 给了就再算上（Options 那条计的是选项自己的 Id）；
        - LINK_TABLES（带 UnitId 的关联行）：Id + UnitId 决定"这一行属于哪个单位"。
    """
    fields: set[tuple[str, str]] = set()
    for edge in EDGES:
        fields.add((edge.from_table, edge.column))
        fields.add((edge.from_table, edge.unit_column))
        if edge.via is not None:
            middle, via_column, via_unit_column = edge.via
            fields.add((middle, via_column))
            fields.add((middle, via_unit_column))
            fields.add((edge.from_table, edge.via_key or via_column))
        if edge.key_column:
            fields.add((edge.from_table, edge.key_column))
    for name in LINK_TABLES:
        fields.add((name, "Id"))
        fields.add((name, "UnitId"))
    return frozenset(fields)


#: 改这几列才会让共享度过期。改别处（携弹量 / 成本 / 名字…）一律不重建。
_SHARING_FIELDS = _sharing_fields()
#: 这几张表的**行集合**变了（插 / 删一行）也要重建共享度。
_SHARING_TABLES = frozenset(name for name, _column in _SHARING_FIELDS)


class CardData:
    """按 Id 直取的关系图。**建一次，反复用**（切单位时重建是浪费）。"""

    def __init__(
        self,
        instance: Instance,
        localization: Localization | None = None,
        sharing: SharingIndex | None = None,
    ) -> None:
        self.instance = instance
        self.loc = localization if localization is not None else load_localization()
        #: 共享度：改一个数会波及多少单位。没给就现建（整表扫一遍 17ms）。
        self._sharing = sharing if sharing is not None else build_sharing_index(instance)
        #: 共享度脏了没（见 invalidate / invalidate_for 与 sharing 属性）。
        self._sharing_stale = False
        #: 上一次据以判共享度的那批改动 {(表, 行 Id, 列)} 与它来自哪个变更集。
        #: 与 now 比对称差 = "这一批到底动了哪儿"（见 invalidate_for）。
        self._sharing_seen: set[tuple[str, int, str]] | None = None
        self._sharing_source = None
        self._tables: dict[str, Rows] = {}
        #: (表, 行 Id) -> 显示名。卡上一条选项就有好几条外键，一个单位几十条选项 ——
        #: display_name 是 O(行数)，不缓存的话换一格的刷新要扫几十万行。
        self._names: dict[tuple[str, int], str] = {}

    @property
    def sharing(self) -> SharingIndex:
        """共享度索引 —— **惰性重建**。

        ❗ 原来 invalidate() 每次都同步重建一遍（整库扫，实测 ~10ms）。
        而 invalidate 挂在变更集订阅上：**改一格就重建一次** —— 打字发涩的一大来源。
        改成"先标脏、真要用的时候再建"：改一格的那些路径（画条目、刷新树）
        大多用不到共享度，于是这一下就省掉了。
        """
        if self._sharing_stale:
            self._sharing = build_sharing_index(self.instance)
            self._sharing_stale = False
        return self._sharing

    def t(self, name: str) -> Rows:
        """取一张表（**名字按别名归一**，见 `resolve_table_name`）。

        ❗ 归一这一步是必需的：从前同一张表在「按文件名建表」的数据源里叫
        `PlaneFlyPresets`，在 db.zip 那一套里叫 `FlyPresets`。写死一个名字，
        换个数据源就会 KeyError —— 实测症状是**战机那一类卡片整类装配不出来**
        （用户报的"战机侧的东西都显示不出来"）。

        2026-10-01 起两种来源都交规范名（`core/data/loader.py` 一处归一），这里
        仍然留着归一：别名输入（老工具、老包）与"写死了别的写法"的调用方都还要能用。
        """
        rows = self._tables.get(name)
        if rows is None:
            resolved = resolve_table_name(name, self.instance.table_names)
            if resolved is None:
                raise KeyError(f"数据源中不存在表 {name}")
            rows = Rows(self.instance.table(resolved))
            self._tables[name] = rows
            self._tables[resolved] = rows
        return rows

    def has_table(self, name: str) -> bool:
        """这张表在**当前数据源**里存在吗（规范名或别名任一）。"""
        return resolve_table_name(name, self.instance.table_names) is not None

    def invalidate(self, table: str | None = None) -> None:
        """改过表之后调它：丢掉筛结果与 Id 索引，**把共享度标脏**（不立刻重建）。

        界面把它挂在变更集的订阅上（见 app/screens/armory/detail_panel.py），
        这样「改了数 -> 卡片跟着动」是自动的，不靠谁记得手动刷。

        ❗ 这是"**不知道改了哪儿**"的那条路（换数据源 / 放弃全部改动 / 别的调用方）：
        共享度一律标脏，宁可多算。知道改了哪几格的走 invalidate_for（卡片那条路）
        —— 两者丢缓存的范围**一样**，只有共享度那一步的判据不同。
        """
        if table is None:
            for rows in self._tables.values():
                rows.invalidate()
        else:
            rows = self._tables.get(table)
            if rows is not None:
                rows.invalidate()
        # 共享度是整库扫出来的（实测 10–37ms）：这里只标脏，真用到时在 sharing 属性里重建。
        self._sharing_stale = True
        self._sharing_seen = None
        # 显示名缓存一起丢：改了名字（UIName / Name / HUDName）之后卡上写的还得是新的。
        self._names.clear()

    def invalidate_for(self, changeset) -> None:
        """按**变更集**丢缓存：丢的东西与 invalidate() 完全一样，**除了共享度**。

        共享度只在"这一批改动真碰到了它读的那几列"时才重建（见 _SHARING_FIELDS）。
        为什么非分这一刀（实测）：build_sharing_index 是整库扫一遍（10–37ms），
        而它只读那么几张表的那么几列；原来一律标脏，于是**改一格携弹量也要重建
        一次整库索引**，那 37ms 就压在用户点下去的那一下上。

        判据用的是"这一批改动"：与上一次对过账的那份比**对称差** ——
        新增的（改了）与消失的（撤销 / 改回原值 / commit 清账）都算数。
        """
        if changeset is None:
            self.invalidate()
            return
        now = {(change.table, change.row_id, change.column) for change in changeset.changes}
        # 行级改动（插 / 删一行）没有列名，记空串 —— 行集合变了，共享度就得重建。
        now |= {(row.table, row.row_id, "") for row in changeset.row_changes}
        if self._sharing_source is not changeset or self._sharing_seen is None:
            touched = now
        else:
            touched = now.symmetric_difference(self._sharing_seen)
        for table, _row_id, column in touched:
            if (table, column) in _SHARING_FIELDS or (not column and table in _SHARING_TABLES):
                self._sharing_stale = True
                break
        self._sharing_source = changeset
        self._sharing_seen = now
        for rows in self._tables.values():
            rows.invalidate()
        self._names.clear()

    # -- 单位与它挂的东西 ------------------------------------------------ #

    def unit(self, unit_id: int) -> Sequence[Any] | None:
        return self.t(_T_UNITS).by_id(unit_id)

    def unit_type(self, unit: Sequence[Any]) -> int:
        return self.t(_T_UNITS).i(unit, "Type")

    def category(self, unit: Sequence[Any]) -> int:
        return self.t(_T_UNITS).i(unit, "CategoryType", -1)

    def squad(self, unit_id: int) -> tuple[Sequence[Any], ...]:
        rows = self.t(_T_SQUAD_MEMBERS)
        return tuple(sorted(rows.where("UnitId", unit_id), key=lambda row: rows.i(row, "Id")))

    def option_override(
        self, unit_id: int, selected: Mapping[int, int] | None, column: str, table: str
    ) -> tuple[int, Sequence[Any]] | None:
        """选中选项里的第一个非零覆盖 —— 返回 (选项 Id, 目标行)，没有就 None。

        游戏那两句是（ApplyCurrentMods VA 0x180707A80）：
            unit.CurrentArmor    = CurrentOptions.FirstOrDefault(o => o.Armor    != null).Armor
            unit.CurrentMobility = CurrentOptions.FirstOrDefault(o => o.Mobility != null).Mobility
        顺序 = CurrentOptions（与 selected_options 同序）；**指向的行不存在**时
        那条的导航属性是 null、会被跳过（不是"停下"）—— 所以这里逐条往下找第一个解得开的。
        """
        rows = self.t(_T_OPTIONS)
        for option in self.selected_options(unit_id, selected):
            row_id = rows.i(option, column)
            if row_id <= 0:
                continue
            target = self.t(table).by_id(row_id)
            if target is not None:
                return (rows.i(option, "Id"), target)
        return None

    def armor(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> Sequence[Any] | None:
        """CurrentArmor（Units+0xE0）。

        没人覆盖时 = UnitArmors 第一条 -> Armors 行（LoadUnits 装进来的）；
        **被选项覆盖时 = 选中选项里第一个带 ArmorId 的那一行**：
            unit.CurrentArmor = CurrentOptions.FirstOrDefault(o => o.Armor != null).Armor
        （Options.Armor 由 ArmorId 关联，dump.cs Options+0x78 -> +0xF0）。
        而 Units.get_Armor() 读的就是 0xE0（_work/isil/.../DataBase/Models/Units.txt:1703-1707）
        —— 卡片读的字段正是被改写的那一个，所以**缺省局也要走这条口径**。
        """
        override = self.option_override(unit_id, selected, "ArmorId", _T_ARMORS)
        if override is not None:
            return override[1]
        link = self.armor_link(unit_id)
        if link is None:
            return None
        return self.t(_T_ARMORS).by_id(self.t(_T_UNIT_ARMORS).i(link, "ArmorId"))

    def armor_link(self, unit_id: int) -> Sequence[Any] | None:
        links = self.t(_T_UNIT_ARMORS).where("UnitId", unit_id)
        return links[0] if links else None

    def mobility(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> Sequence[Any] | None:
        """CurrentMobility（Units+0xE8）—— 与 armor 同型，被选项的 MobilityId 覆盖。

        Units.get_Mobility() 读的就是 0xE8（Units.txt:1726-1730）；
        速度 / 倒车 / 敏捷 / 悬停 / 起重 / 两栖 全部由它派生，所以 selected 必须传进来。
        """
        override = self.option_override(unit_id, selected, "MobilityId", _T_MOBILITY)
        if override is not None:
            return override[1]
        link = self.mobility_link(unit_id)
        if link is None:
            return None
        return self.t(_T_MOBILITY).by_id(self.t(_T_UNIT_PROPULSIONS).i(link, "MobilityId"))

    def stealth_override(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> tuple[float, int | None]:
        """隐蔽值：选中的选项带 StealthOverride（非 0）就盖掉 Units.CurrentStealth。

        返回 (生效的值, 是哪条选项盖的)。**这一列在数据库里，不要只读 Units。**
        """
        rows = self.t(_T_OPTIONS)
        for option in self.selected_options(unit_id, selected):
            value = rows.f(option, "StealthOverride")
            if value:
                return value, rows.i(option, "Id")
        unit = self.unit(unit_id)
        if unit is None:
            return 0.0, None
        return self.t(_T_UNITS).f(unit, "CurrentStealth"), None

    def mobility_link(self, unit_id: int) -> Sequence[Any] | None:
        links = self.t(_T_UNIT_PROPULSIONS).where("UnitId", unit_id)
        return links[0] if links else None

    def fly_preset(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> Sequence[Any] | None:
        """这个单位的飞行预设行；没有（或数据源里根本没这张表）返回 None。

        ❗ 「没这张表」要**当成没有预设**，不能让整张卡塌：
        数据源的表名/完整性不由我们决定，而固定翼的速度与回转半径都挂在这一行上。
        """
        mobility = self.mobility(unit_id, selected)
        if mobility is None:
            return None
        preset_id = self.t(_T_MOBILITY).i(mobility, "FlyPresetId")
        if not preset_id or not self.has_table(_T_FLY_PRESETS):
            return None
        return self.t(_T_FLY_PRESETS).by_id(preset_id)

    def base_sensors(self, unit_id: int) -> tuple[Sequence[Any], ...]:
        """LoadSensors（LoadUnits VA 0x18028F390 段）：SensorUnits 里该单位的**全部**行。

        这一步"取 Max"本来是对的 —— 是 ApplyCurrentMods 之后才需要小心。
        """
        links = self.t(_T_SENSOR_UNITS)
        ordered = sorted(links.where("UnitId", unit_id), key=lambda row: links.i(row, "Id"))
        out = []
        for row in ordered:
            sensor = self.t(_T_SENSORS).by_id(links.i(row, "SensorId"))
            if sensor is not None:
                out.append(sensor)
        return tuple(out)

    def ability_rows(self, unit_id: int) -> tuple[Sequence[Any], ...]:
        links = self.t(_T_UNIT_ABILITIES)
        ordered = sorted(links.where("UnitId", unit_id), key=lambda row: links.i(row, "Id"))
        out = []
        for row in ordered:
            ability = self.t(_T_ABILITIES).by_id(links.i(row, "AbilityId"))
            if ability is not None:
                out.append(ability)
        return tuple(out)

    # -- 选项图 ---------------------------------------------------------- #

    def modifications_of(self, unit_id: int) -> tuple[Sequence[Any], ...]:
        rows = self.t(_T_MODIFICATIONS)
        return tuple(
            sorted(
                rows.where("UnitId", unit_id),
                key=lambda row: (rows.i(row, "Order"), rows.i(row, "Id")),
            )
        )

    def options_of(self, modification_id: int) -> tuple[Sequence[Any], ...]:
        rows = self.t(_T_OPTIONS)
        return tuple(
            sorted(
                rows.where("ModificationId", modification_id),
                key=lambda row: (rows.i(row, "Order"), rows.i(row, "Id")),
            )
        )

    def option(self, option_id: int) -> Sequence[Any] | None:
        return self.t(_T_OPTIONS).by_id(option_id)

    def variant_options(self, unit_id: int) -> tuple[Sequence[Any], ...]:
        """**指向**这个单位的替换选项（Options.ReplaceUnitId == unit_id）。

        实测 645：自己 0 条 Modification，是单位 108 的 Modification 743 里
        option 3129 把它换出来的。全表扫一遍是 3800 行级别，可接受（结果缓存）。
        """
        rows = self.t(_T_OPTIONS)
        index = rows.index_of("ReplaceUnitId")

        def build() -> tuple[Sequence[Any], ...]:
            return tuple(row for row in rows.all() if _int(row[index]) == unit_id)

        return rows.cache(("__variant__", unit_id), build)

    def default_option(self, modification_id: int) -> Sequence[Any] | None:
        """GetDefaultOptions（VA 0x18070C0F0）：

            mod.Options.FirstOrDefault(o => o.IsDefault)
                ?? mod.Options.FirstOrDefault(o => o.Order == 0)

        ⚠️ 实测 615 个 Modification 里 436 个一个 IsDefault 都没有 ——
        所以"退化为 Order==0"才是常态。
        """
        options = self.options_of(modification_id)
        rows = self.t(_T_OPTIONS)
        for option in options:
            if rows.b(option, "IsDefault"):
                return option
        for option in options:
            if rows.i(option, "Order") == 0:
                return option
        return None

    def selected_options(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> tuple[Sequence[Any], ...]:
        """unit.CurrentOptions（Units+0xF0）：每个改型组当前选中的那一项。

        selected = {modification_id: option_id}，模拟"玩家已经选好的挂架"；
        不给就取缺省（GetDefaultOptions）。
        """
        rows = self.t(_T_OPTIONS)
        out: list[Sequence[Any]] = []
        for modification in self.modifications_of(unit_id):
            modification_id = self.t(_T_MODIFICATIONS).i(modification, "Id")
            picked: Sequence[Any] | None = None
            if selected and modification_id in selected:
                candidate = self.option(selected[modification_id])
                if candidate is not None and rows.i(candidate, "ModificationId") == modification_id:
                    picked = candidate
            if picked is None:
                picked = self.default_option(modification_id)
            if picked is not None:
                out.append(picked)
        return tuple(out)

    # -- 武器与弹药 ------------------------------------------------------ #

    def weapon(self, weapon_id: int) -> Sequence[Any] | None:
        return self.t(_T_WEAPONS).by_id(weapon_id)

    def weapon_ammo_rows(self, unit_id: int, weapon_id: int) -> tuple[Sequence[Any], ...]:
        """(UnitId, WeaponId) 的携弹**行**，按 (Order, AmmunitionId) 排 —— 与 weapon_ammo 同序。

        为什么单开一条：卡上「炮塔 › 武器 › 弹种」第四层点下去要落到**具体那一行**
        （携弹量在 WeaponAmmunitions 行上改），所以除了 (弹种, 数量, 顺序)，还得带回行 Id。
        """
        rows = self.t(_T_WEAPON_AMMO)
        entries = list(rows.where2("UnitId", unit_id, "WeaponId", weapon_id))
        entries.sort(key=lambda row: (rows.i(row, "Order"), rows.i(row, "AmmunitionId")))
        return tuple(entries)

    def weapon_ammo(self, unit_id: int, weapon_id: int) -> tuple[tuple[int, int, int], ...]:
        """Weapons.WeaponAmmunitions（LoadUnits.LoadAmmunitions）：

            (UnitId, WeaponId) 过滤 -> dict[wa.AmmunitionId] = wa.Quantity

        返回 ((AmmunitionId, Quantity, Order), ...)，按 Order 再按 Id 排
        —— 游戏取的是 Ammunitions.First()，顺序就来自这里。
        """
        rows = self.t(_T_WEAPON_AMMO)
        return tuple(
            (rows.i(row, "AmmunitionId"), rows.i(row, "Quantity"), rows.i(row, "Order"))
            for row in self.weapon_ammo_rows(unit_id, weapon_id)
        )

    def name_of(self, table: str, row_id: int) -> str:
        """一行叫什么（**带缓存**，invalidate() 时一起清掉）。

        口径与 row_name 逐字一致，区别只在缓存：core/edit/foreign.py::display_name
        每次都全表扫一遍，而卡上一条选项就有好几条外键、一个单位几十条选项
        （F-15E 5 个槽 31 条选项）—— 不缓存的话换一格的刷新要扫几十万行。
        """
        key = (table, row_id)
        cached = self._names.get(key)
        if cached is None:
            cached = row_name(self, table, row_id)
            self._names[key] = cached
        return cached

    def ammo(self, ammo_id: int) -> Sequence[Any] | None:
        return self.t(_T_AMMO).by_id(ammo_id)

    def weapon_ids(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> tuple[int, ...]:
        """界面左竖排那个武器数组（UnitInfoCard.SetUnitWeaponList）。

        步兵：SquadMembers 每行的 PrimaryWeapon + SpecialWeapon，**按人**不去重
              （这就是 x5 / x8 那个"武器数"）。
        其它：unit.Turrets（含 children）-> Weapons。

        ❗ 必须把 selected 传给 card_turrets：炮塔槽位是**选项**定的
        （Options.Turret0Id..Turret20Id），漏了它这一排画的还是缺省挂架
        （Su-57 换 2xKh59MK2 后仍显示 3xR-77-1）。
        """
        unit = self.unit(unit_id)
        if unit is not None and self.unit_type(unit) == TYPE_INFANTRY:
            members = self.t(_T_SQUAD_MEMBERS)
            out: list[int] = []
            for member in self.squad(unit_id):
                for column in ("PrimaryWeaponId", "SpecialWeaponId"):
                    weapon_id = members.i(member, column)
                    if weapon_id > 0:
                        out.append(weapon_id)
            return tuple(out)
        # ❗ 必须递归子炮塔：UnitInfoCard 走的是
        # unit.Turrets ∪ unit.Turrets.children -> SelectMany(t => t.Weapons)。
        # 只取顶层会漏掉挂架下的武器（Su-25T / Mi-8 这类挂载型单位尤其明显）。
        out = []
        for turret_id in self.card_turrets(unit_id, selected):
            out.extend(_walk_turret_weapons(self, turret_id))
        return tuple(out)

    # -- 炮塔树（撤回时间用的对象，与武器数组**不是同一套**） ----------- #

    def card_turrets(
        self, unit_id: int, selected: Mapping[int, int] | None = None
    ) -> tuple[int, ...]:
        """ApplyCurrentMods 之后 unit.Turrets（Units+0x140）。

        步兵：LoadSquads -> CreateFakeTurret（VA 0x18028AF00 段）造"假炮塔"，
              **按武器 Id 去重** —— 一个班 5 把 AK 只算一把的携弹量。
              用 -weaponId 编码，与真炮塔区分。
        其它：LoadTurrets（只收 Turrets.IsDefault == true）再按槽位 0..20 覆盖。
        """
        unit = self.unit(unit_id)
        members = self.t(_T_SQUAD_MEMBERS)
        if unit is not None and self.unit_type(unit) == TYPE_INFANTRY:
            seen: list[int] = []
            for member in self.squad(unit_id):
                for column in ("PrimaryWeaponId", "SpecialWeaponId"):
                    weapon_id = members.i(member, column)
                    if weapon_id > 0 and weapon_id not in seen:
                        seen.append(weapon_id)
            return tuple(-weapon_id for weapon_id in seen)

        turrets: list[int] = []
        links = self.t(_T_TURRET_UNITS)
        ordered = sorted(
            links.where("UnitId", unit_id),
            key=lambda row: (links.i(row, "Order"), links.i(row, "Id")),
        )
        for link in ordered:
            turret = self.t(_T_TURRETS).by_id(links.i(link, "TurretId"))
            if turret is not None and self.t(_T_TURRETS).b(turret, "IsDefault"):
                turrets.append(self.t(_T_TURRETS).i(turret, "Id"))

        options = self.selected_options(unit_id, selected)
        option_rows = self.t(_T_OPTIONS)
        for slot, column in enumerate(TURRET_SLOTS):
            picked = next((o for o in options if option_rows.i(o, column) > 0), None)
            if picked is None:
                continue
            turret_id = option_rows.i(picked, column)
            if slot < len(turrets):
                turrets[slot] = turret_id
            else:
                turrets.append(turret_id)
        return tuple(turrets)

    def turret_weapons(self, turret_id: int) -> tuple[int, ...]:
        if turret_id < 0:
            return (-turret_id,)
        rows = self.t(_T_TURRET_WEAPONS)
        ordered = sorted(
            rows.where("TurretId", turret_id),
            key=lambda row: (rows.i(row, "Order"), rows.i(row, "Id")),
        )
        return tuple(rows.i(row, "WeaponId") for row in ordered if rows.i(row, "WeaponId") > 0)

    def turret_children(self, turret_id: int) -> tuple[int, ...]:
        if turret_id < 0:
            return ()
        turrets = self.t(_T_TURRETS)
        index = turrets.index_of("ParentTurretId")
        return tuple(
            turrets.i(row, "Id")
            for row in turrets.all()
            if index < len(row) and _int(row[index]) == turret_id
        )

    # -- 本地化 ---------------------------------------------------------- #

    def label(self, key: str, default: str = "") -> str:
        return self.loc.label(key, default) if key else default

    def hint(self, key: str) -> str:
        return self.loc.hint(key) if key else ""

    def display_name(self, key: str, fallback: str) -> str:
        """数据库里的 UIName / HUDName 既可能是本地化键也可能是明文。

        实测：Modifications.UIName = 'Custom_Slot_Squad_loadout'（键，中文=小队装备配置），
        而 Options.UIName 有的是键（'Custom_Option_PKP'）、有的是明文（'M-SHORAD, Stingers'）。
        规则：先查本地化，查不到就用原文。

        ❗ **表里有这个键、但翻出来是占位符 "None"** 时要退到 `fallback`（内部名），
        **绝不能**把键名本身画出来 —— `dlc_3_Custom_Option_OSV96` 这种机器名给人看等于没写。
        实测（`_work/w9c_scan_none.py`）：全库 22259 个非空字符串格子里唯一命中占位符的就是
        `Options.UIName Id=3039`（用户报的「左边显示的是 None」），回退到
        `Options.Name = 'Squad FSBSnaipery AMR'`。
        判据是 `loc.has(key)`（键在表里）＋ `loc.text(key)`（翻出了真正文）分开看：
        两者一起才分得清"明文原样显示"与"翻不出来要回退"。
        """
        if not key:
            return fallback
        if self.loc.has(key):
            return self.loc.text(key) or fallback or key
        return key or fallback


# --------------------------------------------------------------------------- #
# 公式（每条都标了出处；样本见 _work/verify_card_formulas.py）
# --------------------------------------------------------------------------- #


def base_cost(data: CardData, unit: Sequence[Any]) -> float:
    """PrepareForMods（LoadUnits VA 0x180291090）：OriginalCost == 0 时它是 Cost。"""
    units = data.t(_T_UNITS)
    original = units.i(unit, "OriginalCost")
    return float(original) if original else float(units.i(unit, "Cost"))


def card_cost(data: CardData, unit_id: int, selected: Mapping[int, int] | None = None) -> float:
    """卡片头部的成本 = OriginalCost + Sum(已选选项 Cost)（ApplyCurrentMods ISIL 218-254）。"""
    unit = data.unit(unit_id)
    if unit is None:
        return 0.0
    options = data.t(_T_OPTIONS)
    return base_cost(data, unit) + sum(
        float(options.i(option, "Cost")) for option in data.selected_options(unit_id, selected)
    )


def unit_name(data: CardData, unit_id: int, selected: Mapping[int, int] | None = None) -> str:
    """卡片抬头（与左墙那张卡）的名字 = HUDName ?? Name，再套上**选项的名字覆盖**。

    两条列（Options 里互斥：全库没有一行同时非空）——

    - `ReplaceUnitName`：整个换掉（Id 1 的缺省项 501 -> "BTR-82A"）；
    - `ConcatenateWithUnitName`：**原样拼**在后面（Id 230 缺省项 534 的 "B" -> "PT-76B"，
      选项 536 的 "-57" -> "PT-76-57"）。

    ❗ 值里带不带空格就是游戏里的样子（" Javelin" / " Trophy" vs "A1" / "1"），
    不要再自己插分隔符。多个组都带名字覆盖时按 selected_options 的顺序依次套用。

    实机锚（docs/实机-*.png）：BTR-82 卡名写 **BTR-82A**、T-72B 卡名写 **T-72B1**
    （缺省项 1449 拼 "1"）、PT-76 缺省写 **PT-76B**、换成 AU-220M 后写 **PT-76-57**
    —— 四条都对得上；T-72B1/PT-76B 这两条同时说明**缺省也要走这条口径**。
    """
    unit = data.unit(unit_id)
    if unit is None:
        return f"#{unit_id}"
    units = data.t(_T_UNITS)
    name = units.s(unit, "HUDName") or units.s(unit, "Name") or f"#{unit_id}"
    options = data.t(_T_OPTIONS)
    for option in data.selected_options(unit_id, selected):
        replaced = options.s(option, "ReplaceUnitName")
        if replaced:
            name = replaced
            continue
        concatenated = options.s(option, "ConcatenateWithUnitName")
        if concatenated:
            name = name + concatenated
    return name


def _option_text(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None, column: str
) -> str:
    """选中选项里**第一个非空**的字符串覆盖列（Options.<column>）；没有就 ""。"""
    rows = data.t(_T_OPTIONS)
    for option in data.selected_options(unit_id, selected):
        value = rows.s(option, column)
        if value:
            return value
    return ""


def unit_thumbnail_key(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> str:
    """卡片缩略图（左墙那张卡画的图）的图键 = 选中选项里第一个非空覆盖 ?? Units 那一列。

    与 `unit_name` **同一套纪律**（这是重点，别只对"换过选项"生效）：

    1. **缺省也要走这条口径** —— `selected=None` 就是游戏缺省选项（GetDefaultOptions），
       缺省项自己带覆盖时照样生效。缺省缩略图就被改掉的单位实测 **17 个**（23 / 72 / 108 / 129 …）。
    2. 取选中选项里**第一个非空**的 `Options.ThumbnailOverride`；一条都没有才回落
       `Units.ThumbnailFileName`。
    3. 覆盖**不以 ReplaceUnitId 为条件**：换单位的那些选项由界面"跳到那个单位"处理，
       本函数只管"这张卡该画哪张图"。

    证据（先查库、再看反编译，见 AGENTS §0.5）：

    - 列：`Options.ThumbnailOverride`（字符串，字段 +0x60）。全库非空 **224** 条，
      其中 **131** 条不换单位（ReplaceUnitId == 0）—— 这 131 条就是我们原来漏掉的。
    - 读点：`Units.get_CurrentThumbnail()` / `set_CurrentThumbnail`
      （_work/isil/.../DataBase/Models/Units.txt:1421/1431）—— 卡片画的是 **Current** 那个值，
      不是原始列；它与 CurrentArmor(0xE0) / CurrentMobility(0xE8) 并列。
    - 条件：ApplyCurrentMods 的闭包里有一条 `!string.IsNullOrEmpty(opt.<+0x60>)` 谓词
      （UnitsInfoService_NestedType___c.txt:345），与护甲/机动同一套 FirstOrDefault 写法
      —— 所以是"第一个非空"，不是"最后一个"。
    """
    unit = data.unit(unit_id)
    if unit is None:
        return ""
    override = _option_text(data, unit_id, selected, "ThumbnailOverride")
    if override:
        return override
    return data.t(_T_UNITS).s(unit, "ThumbnailFileName")


def unit_portrait_key(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> str:
    """卡片立绘（右卡顶上那张通栏大图）的图键 = 第一个非空 PortraitOverride ?? Units 那一列。

    与 `unit_thumbnail_key` / `unit_name` 同一条口径（缺省也走覆盖，覆盖为空才回落）。
    立绘覆盖少得多：`Options.PortraitOverride` 非空 **23** 条，**全部**带 ReplaceUnitId；
    其中 8 个 DLC2 单位的**缺省项**就带覆盖，但那些值恰好等于 `Units.PortraitFileName`，
    所以"缺省就变"的单位是 **0 个** —— 这条口径现在不改任何一张图，只是把路铺对：
    将来选项表里出现"不换单位、只换立绘"的条目时它会自己生效。

    证据：`Units.get_CurrentPortrait()` / `set_CurrentPortrait`（Units.txt:1444/1454）、
    `Options.PortraitOverride`（Options.txt:301，字段 +0x68）、ApplyCurrentMods 闭包里
    对应的 `!IsNullOrEmpty` 谓词（UnitsInfoService_NestedType___c.txt:373）。
    """
    unit = data.unit(unit_id)
    if unit is None:
        return ""
    override = _option_text(data, unit_id, selected, "PortraitOverride")
    if override:
        return override
    return data.t(_T_UNITS).s(unit, "PortraitFileName")


#: 「装甲图」4 个部位的**顺序、列名、本地化键**：
#: (部位键, 动能列, 破甲列, 本地化键, 本地化缺失时的中文)。
#:
#: - 顺序 = 实机那张卡的四角（上排 顶部|背面，下排 正面|侧面），见 docs/实机-04-T-72B.png；
#: - 列名照 `Armors` 表逐字（注意 **Sides 是复数**，而本地化键是单数
#:   `ui_infocard_armor_side` —— 两边写法不一样，别照着一边改另一边）；
#: - 本地化键实测在游戏包里（keys/chi 5379 条）：正面 / 侧面 / 顶部 / 背面，
#:   与实机截图逐字一致；缺游戏时用后面那个兜底中文，值一样。
ARMOR_ZONE_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("top", "KinArmorTop", "HeatArmorTop", "ui_infocard_armor_top", "顶部"),
    ("rear", "KinArmorRear", "HeatArmorRear", "ui_infocard_armor_rear", "背面"),
    ("front", "KinArmorFront", "HeatArmorFront", "ui_infocard_armor_front", "正面"),
    ("side", "KinArmorSides", "HeatArmorSides", "ui_infocard_armor_side", "侧面"),
)

#: 装甲图上「抗穿 / 抗破」这两个名字（实机只有颜色区分，名字在 tooltip 里）。
#: 键同样在游戏本地化包里。
ARMOR_KINETIC_KEY = "ui_infocard_armor_kinetic"
ARMOR_HEAT_KEY = "ui_infocard_armor_heat"


def armor_zones(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> tuple[ArmorZone, ...]:
    """地面载具的「装甲图」：4 个部位 × (动能, 破甲)。**不画时返回空元组**。

    显示条件（用全库数据定，理由如下）：
    **Type == 4（载具）且 8 个数里有非零**。
    实测本版库（52085）：Type=4 共 234 个、其中 226 个非零；而步兵(194)/直升机(45)/
    战机(62)/舰船(5) **一个非零都没有** —— 所以这块图只可能出现在地面载具上，
    与用户口径「地面载具的装甲」一致。8 个数全零的那 8 个载具**不画**：
    与 `_t_armor`「ArmorValue == 0 就不出那一格」同一风格（实机没有这种样本）。

    取行**必须**走 `CardData.armor()`：它已经处理 `Options.ArmorId` 的覆盖
    （**缺省也走覆盖**，见 docs/交接-单位卡片GUI.md §13.2 第 4 条）。
    绕过它自己去查 `UnitArmors`，换「装甲组件」时这块就不会变。

    实机对照（3 张截图逐格命中，也顺带把列映射钉死了）：
    单位 9 T-72B1 -> 顶部 60/80、背面 100/100、正面 550/800、侧面 100/250；
    单位 1 BTR-82A -> 20/20、20/20、30/30、20/20；
    单位 217 BRDM-3 -> 20/20、20/20、30/30、20/20。
    """
    unit = data.unit(unit_id)
    if unit is None or data.unit_type(unit) != TYPE_VEHICLE:
        return ()
    armor = data.armor(unit_id, selected)
    if armor is None:
        return ()
    rows = data.t(_T_ARMORS)
    # 这一行是谁给过来的：缺省 = UnitArmors 指的那一行；被选项覆盖时是那条选项的 ArmorId。
    armor_id = rows.i(armor, "Id")
    source = "UnitArmors"
    override = data.option_override(unit_id, selected, "ArmorId", _T_ARMORS)
    if override is not None:
        source = f"Options#{override[0]}.ArmorId"
    zones = [
        ArmorZone(
            key=key,
            label=data.label(loc_key, fallback),
            kinetic=rows.i(armor, kin_column),
            heat=rows.i(armor, heat_column),
            row_id=armor_id,
            source=source,
        )
        for key, kin_column, heat_column, loc_key, fallback in ARMOR_ZONE_SPECS
    ]
    if not any(zone.kinetic or zone.heat for zone in zones):
        return ()
    return tuple(zones)


def effective_sensors(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> tuple[Sequence[Any], ...]:
    """选项改写之后的 unit.Sensors（ApplyCurrentMods ISIL 2140-2265）。

        main  = CurrentOptions.FirstOrDefault(o => o.MainSensorId  > 0)
                -> unit.Sensors = new List<Sensors>{ Sensors[main.MainSensorId] }   ★赋值
        extra = CurrentOptions.FirstOrDefault(o => o.ExtraSensorId > 0)
                -> 存在且 Id 不重复时 Add

    这就是 BMD-2 显示 1200（选项 183 的 MainSensor=2 -> OpticsGround 600）而不是
    Max(600,700)*2 = 1400 的原因。全库 MainSensorId>0 的选项 184 个 / 覆盖 69 个单位。
    """
    base = list(data.base_sensors(unit_id))
    options = data.selected_options(unit_id, selected)
    rows = data.t(_T_OPTIONS)
    sensors = data.t(_T_SENSORS)

    main = next((option for option in options if rows.i(option, "MainSensorId") > 0), None)
    if main is not None:
        sensor = sensors.by_id(rows.i(main, "MainSensorId"))
        base = [sensor] if sensor is not None else []

    extra = next((option for option in options if rows.i(option, "ExtraSensorId") > 0), None)
    if extra is not None:
        sensor = sensors.by_id(rows.i(extra, "ExtraSensorId"))
        if sensor is not None and not any(
            sensors.i(existing, "Id") == sensors.i(sensor, "Id") for existing in base
        ):
            base.append(sensor)
    return tuple(base)


def optics_triple(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> tuple[float, float, float]:
    """(地面, 低空, 高空)，取改写后传感器列表的 Max，**已乘 EffectiveRangeMultiplier**。"""
    sensors = effective_sensors(data, unit_id, selected)
    if not sensors:
        return (0.0, 0.0, 0.0)
    rows = data.t(_T_SENSORS)
    ground = max(rows.f(sensor, "OpticsGround") for sensor in sensors)
    low = max(rows.f(sensor, "OpticsLowAltitude") for sensor in sensors)
    high = max(rows.f(sensor, "OpticsHighAltitude") for sensor in sensors)
    return (
        ground * EFFECTIVE_RANGE_MULTIPLIER,
        low * EFFECTIVE_RANGE_MULTIPLIER,
        high * EFFECTIVE_RANGE_MULTIPLIER,
    )


def get_infantry_slots(data: CardData, unit_id: int) -> int:
    """Units.GetInfantrySlots()（VA 0x180295B40）—— 【座位格的条件】。

        if (Type == Infantry) return 0;
        if (CategoryType != 0) { if (CategoryType == Infantry) return 0; return InfantrySlots; }
        if (SquadMembers.Count > 0) return 0;
        return InfantrySlots;

    "有没有这一格"由它决定；发射时显示的是 Units.InfantrySlots **原列**。
    样本：BMD-2 有(5)、Mi-8 有(21)、Grad 没有(0 且不是显示 0)、步兵没有。
    """
    unit = data.unit(unit_id)
    if unit is None:
        return 0
    units = data.t(_T_UNITS)
    slots = units.i(unit, "InfantrySlots")
    if data.unit_type(unit) == TYPE_INFANTRY:
        return 0
    category = data.category(unit)
    if category != 0:
        if category == CAT_INFANTRY:
            return 0
        return slots
    if data.squad(unit_id):
        return 0
    return slots


def card_weight(data: CardData, unit_id: int) -> tuple[int, bool]:
    """(显示重量, 是不是算出来的)。

    步兵 = **125 × 班成员数**（LoadUnits: imul eax,[rax+18h],7Dh），
    其它 = Units.Weight 原列（BMD-2 = 8500、Grad = 14000，与截图一致）。
    """
    unit = data.unit(unit_id)
    if unit is None:
        return (0, False)
    if data.unit_type(unit) == TYPE_INFANTRY:
        return (INFANTRY_WEIGHT_PER_MEMBER * len(data.squad(unit_id)), True)
    return (data.t(_T_UNITS).i(unit, "Weight"), False)


def _walk_turret_weapons(data: CardData, turret_id: int) -> tuple[int, ...]:
    out = list(data.turret_weapons(turret_id))
    for child in data.turret_children(turret_id):
        out.extend(_walk_turret_weapons(data, child))
    return tuple(out)


def ammo_resupply(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> tuple[float, float, float]:
    """GetAmmoResupplyTime（VA 0x1806CD290）的 (maxResupplyTime, totalAmmoCost, planesAmmoEffect)。

    走的武器集合是 **unit.Turrets 树**（步兵 = 按武器去重后的假炮塔），
    与卡片上那排武器（按人）**不是同一套** —— 反直觉但已核实（研究文档 §4.3）。
    """
    counts: dict[int, int] = {}
    for turret_id in data.card_turrets(unit_id, selected):
        for weapon_id in _walk_turret_weapons(data, turret_id):
            for ammo_id, quantity, _order in data.weapon_ammo(unit_id, weapon_id):
                counts[ammo_id] = counts.get(ammo_id, 0) + quantity

    rows = data.t(_T_AMMO)
    max_resupply = 0.0
    total_cost = 0.0
    planes = 0.0
    for ammo_id, quantity in counts.items():
        ammo = data.ammo(ammo_id)
        if ammo is None:
            continue
        resupply_time = rows.f(ammo, "ResupplyTime")
        max_resupply = max(max_resupply, quantity * resupply_time)
        total_cost += quantity * rows.f(ammo, "SupplyCost")
        planes += quantity * (resupply_time**PLANES_REFUND_AMMO_P)
    planes = planes ** (1.0 / PLANES_REFUND_AMMO_P) if planes > 0 else 0.0
    return (max_resupply, total_cost, planes)


def ammo_resupply_multiplier() -> float:
    """SupplyConfig.GetAmmoResupplyMultiplier（VA 0x180716210）= 4/1000*10 = 0.04。"""
    return SUPPLY_AMMO_COST_MULTIPLIER / SUPPLY_EXCHANGE_RATE * SUPPLY_VALUE_BOUGHT


def purchase_delay(
    data: CardData,
    unit_id: int,
    options: int,
    *,
    cost: float | None = None,
    selected: Mapping[int, int] | None = None,
) -> float:
    """RefundSystem.GetPurchaseDelay（VA 0x1806CD8A0）的三个分支。

    options：1 = IsDead（阵亡后，表头那个数）、2 = UntouchedTime（毫发无损）、
    4 = OutOfAmmo（弹药耗尽）。UnitInfoCard 里三次调用的实参正是 2 / 4 / 1。
    """
    unit = data.unit(unit_id)
    if unit is None:
        return 0.0
    units = data.t(_T_UNITS)
    option_rows = data.t(_T_OPTIONS)

    card_price = card_cost(data, unit_id, selected) if cost is None else float(cost)
    is_dead = bool(options & REFUND_IS_DEAD)
    untouched = bool(options & REFUND_UNTOUCHED)
    out_of_ammo = bool(options & REFUND_OUT_OF_AMMO)

    picked = data.selected_options(unit_id, selected)
    # ❗ 这两列在库里是 float（dump.cs：Options+0x50 / +0x54 都是 Single），
    #    必须用 f()：i() 走 _int，对 float 一律返回 default 0 ——
    #    实测 12 / 7 条选项的覆盖会被静默丢掉（"换了选项撤回时间不变"）。
    mod_refund = sum(option_rows.f(o, "TimeModifierRefund") for o in picked)
    mod_resurrect = sum(option_rows.f(o, "TimeModifierResurrect") for o in picked)
    _max_resupply, total_cost, planes = ammo_resupply(data, unit_id, selected)

    if data.category(unit) == CAT_AIRCRAFTS:
        refund_mods = units.f(unit, "TimeModifierRefund") + mod_refund
        resurrect_mods = units.f(unit, "TimeModifierResurrect") + mod_resurrect
        if is_dead and not PLANES_DEATH_IS_MULTIPLIER:
            refund_mods = resurrect_mods
        k9, k10 = (0.0, 0.0) if PLANES_DEATH_IS_MULTIPLIER else (1.0, PLANES_DEATH_PENALTY)
        value = card_price * k9 * PLANES_REFUND_HP_MULTIPLIER + PLANES_REFUND_FLOOR
        if not untouched:
            value += planes * PLANES_REFUND_AMMO_K
        value += k10 + refund_mods
        if is_dead and PLANES_DEATH_IS_MULTIPLIER:
            value = value * PLANES_DEATH_REFUND_MULTIPLIER + resurrect_mods
        return max(value, 1.0)

    if is_dead:
        base = 0.0
        floor, multiplier = RESURRECT_DELAY_FLOOR, RESURRECT_DELAY_MULTIPLIER
        extra = units.f(unit, "TimeModifierResurrect") + mod_resurrect
    else:
        if out_of_ammo:
            base = min(
                max(card_price - ammo_resupply_multiplier() * total_cost, 0.0),
                card_price * MAXIMUM_REFUND_PERCENT / 100.0,
            )
        else:
            base = card_price * MAXIMUM_REFUND_PERCENT / 100.0
        floor, multiplier = REPURCHASE_DELAY_FLOOR, REPURCHASE_DELAY_MULTIPLIER
        extra = units.f(unit, "TimeModifierRefund") + mod_refund
    value = (card_price - base) * multiplier + floor
    if not untouched:
        value = max(value, _max_resupply * REFUND_LOST_AMMO_TIME_MULTIPLIER)
    return max(value + extra, 1.0)


# --------------------------------------------------------------------------- #
# 数值行：(图标, 取值函数, 适用条件) 的列表 —— 顺序照抄 SetUnitStatistic
# --------------------------------------------------------------------------- #

_ICON_ARMOR = "Armor"
_ICON_HEALTH = "Health"
_ICON_HEALTH_INFANTRY = "HealthInfantry"
_ICON_SOLDIER_COUNT = "SoldierCount"
_ICON_HEAVY_LIFT = "HeavyLift"
_ICON_SEATS = "SeatsCount"
_ICON_OPTICS = "Optics"
_ICON_STEALTH = "Stealth"
_ICON_SPEED_FORWARD_VEHICLES = "SpeedForwardVehicles"
_ICON_SPEED_FORWARD_HELICOPTERS = "SpeedForwardHelicopters"
_ICON_SPEED_FORWARD_AIRCRAFTS = "SpeedForwardAircrafts"
_ICON_SPEED_FORWARD_INFANTRY = "SpeedForwardInfantry"
_ICON_SPEED_BACKWARD_VEHICLES = "SpeedBackwardVehicles"
_ICON_AGILITY_HELICOPTERS = "AgilityHelicopters"
_ICON_AGILITY_PLANES = "AgilityPlanes"
_ICON_FUEL = "Fuel"
_ICON_WEIGHT = "Weight"
_ICON_REFUND = "RefundTime"

#: 本地化键（全部实测存在于游戏包里）
_L_ARMOR = "ui_infocard_armor"
_L_HEALTH = "ui_infocard_health"
_L_SOLDIER_COUNT = "ui_infocard_squad_count"
_L_HEAVY_LIFT = "ui_infocard_cargo_capacity"
_L_SEATS = "ui_infocard_cargo_seats"
_L_OPTICS = "ui_infocard_optics"
_L_STEALTH = "ui_infocard_stealth"
_L_SPEED_FORWARD = "ui_infocard_speed_forward"
_L_SPEED_BACK = "ui_infocard_speed_back"
_L_AGILITY = "ui_infocard_agility"
_L_TURN_RADIUS = "ui_infocard_plane_turn_radius"
_L_FUEL = "ui_infocard_fuel"
_L_WEIGHT = "ui_infocard_weight"
_L_REFUND = "ui_infocard_refund"
_L_REFUND_DEATH = "ui_infocard_refund_death"
_L_REFUND_UNSCATHED = "ui_infocard_refund_unscathed"
_L_REFUND_NOAMMO = "ui_infocard_refund_noammo"
_L_GROUND_RANGE = "ui_infocard_ammo_ground_range"
_L_LOWALT_RANGE = "ui_infocard_ammo_lowalt_range"
_L_HIGHALT_RANGE = "ui_infocard_ammo_highalt_range"
_L_NO_WEAPONS = "ui_infocard_no_weapons"
#: 展开态详情行的标题（实测都在游戏包里）
_L_TRAJECTORY = "ui_infocard_ammo_trajectory"
_L_EFFECTIVE_RANGE = "ui_infocard_ammo_effective_range"
_L_DAMAGE = "ui_infocard_ammo_damage"
_L_RESUPPLY_TIME = "ui_infocard_ammo_resupply_time"
_L_SUPPLY_COST = "ui_infocard_ammo_supply_cost"
#: 武器侧那几行（OnWeaponButtonSelected 里用的标题）
_L_AIM_TIME = "ui_infocard_weapon_aimtime"
_L_MAGAZINE = "ui_infocard_weapon_magazinesize"
_L_RELOAD_TIME = "ui_infocard_weapon_reloadtime"
#: 弹药侧的另外几条
_L_PENETRATION = "ui_infocard_ammo_penetration"
_L_BLAST_RADIUS = "ui_infocard_ammo_radius_blast"
_L_KINETIC = "ui_infocard_ammo_kinetic"
_L_HEAT = "ui_infocard_ammo_heat"
#: 每秒伤害 / 武器散布 —— 两条都**有实机截图样本**（VDV Metis-M 那两张：
#: AK-74RMO 0.4/s 与 V: 2m / H: 9 m、PKP-M 1.1/s），见 ammo_detail_rows 的注释。
_L_DAMAGE_PER_SECOND = "ui_infocard_ammo_damage_per_second"
_L_DISPERSION = "ui_infocard_ammo_dispersion"
#: 这一轮补上的四条（键都在游戏包里，取值与格式见各自函数的注释）
_L_IGNORE_COVER = "ui_infocard_ammo_ignore_cover"
_L_APS_TARGET = "ui_infocard_ammo_aps_target"
_L_RESUPPLY_COST = "ui_infocard_ammo_resupply_cost"
_L_SALVO_LENGTH = "ui_infocard_weapon_salvo_length"
#: 武器侧：射速（与"弹匣容量+装填时间"互斥的那一支）
_L_RATE_OF_FIRE = "ui_infocard_weapon_rate_of_fire"
_L_RATE_OF_FIRE_HINT = "ui_infocard_weapon_hint_rate_of_fire"

#: 弹药的目标类型（那排小图标）。键名出处：InfoCardLocalization.TryLocalize 里
#: 存进字段的那些 —— 偏移 0x220/0x228/0x230/0x260/0x238/0x240/0x248/0x250/0x258。
_L_TARGET_INFANTRY = "ui_infocard_ammo_target_infantry"
_L_TARGET_VEHICLE = "ui_infocard_ammo_target_vehicles"
_L_TARGET_HELICOPTER = "ui_infocard_ammo_target_helicopters"
_L_TARGET_AIRCRAFT = "ui_infocard_ammo_target_air"
_L_TARGET_MISSILES = "ui_infocard_ammo_target_missiles"
_L_TARGET_PROJECTILES = "ui_infocard_ammo_target_projectiles"
_L_TARGET_SEAD = "ui_infocard_ammo_target_sead"
_L_TARGET_CRUISE = "ui_infocard_ammo_target_cruise_missiles"
_L_TARGET_BALLISTIC = "ui_infocard_ammo_target_ballistic_missiles"

#: 目标类型图标：TargetType 位 -> (TARGET_ICON_KEYS 的键, 本地化键, 中文兜底)。
#: 出处 AmmoInfoExpansionPanel.txt 5346-5481（反汇编）+ 5665-5981（ISIL）：
#:   sprite [rcx+1C8/1D0/1D8/1E0] 配 InfoCardLocalization[0x220/228/230/260]，
#:   每个分支后面接的都是 SetTraitItem（图标）而不是 SetStatisticLine（数值行）。
#: 顺序 = 游戏里的图标顺序（步兵 -> 载具 -> 直升机 -> 固定翼）。
TARGET_TRAIT_SPECS: tuple[tuple[int, str, str, str], ...] = (
    (TARGET_INFANTRY, "infantry", _L_TARGET_INFANTRY, "目标 - 步兵"),
    (TARGET_VEHICLE, "vehicle", _L_TARGET_VEHICLE, "目标 - 载具"),
    (TARGET_HELICOPTER, "helicopter", _L_TARGET_HELICOPTER, "目标 - 直升机"),
    (TARGET_AIRCRAFT, "aircraft", _L_TARGET_AIRCRAFT, "目标 - 飞机"),
)

#: 导弹类的四条：单独命中时各自列一条（共用同一个导弹图标），全中则合成一条【导弹】。
#: 出处 ISIL 168-266：String.Concat(逐条文案 + "\n") 再 TrimEnd("\n")，配 sprite [rcx+1E8]。
TARGET_MISSILE_SPECS: tuple[tuple[int, str, str], ...] = (
    (TARGET_PROJECTILE, _L_TARGET_PROJECTILES, "目标 - 投射物"),
    (TARGET_SEAD_MISSILE, _L_TARGET_SEAD, "目标 - 防空压制"),
    (TARGET_CRUISE_MISSILE, _L_TARGET_CRUISE, "目标 - 巡航导弹"),
    (TARGET_BALLISTIC_MISSILE, _L_TARGET_BALLISTIC, "目标 - 弹道导弹"),
)


@dataclass(frozen=True, slots=True)
class _Ctx:
    """装配一格数值时要看的一切。"""

    data: CardData
    unit_id: int
    unit: Sequence[Any]
    selected: Mapping[int, int] | None
    cost: float


def _speed_forward_icon(ctx: _Ctx) -> str:
    kind = ctx.data.unit_type(ctx.unit)
    if kind == TYPE_INFANTRY:
        return _ICON_SPEED_FORWARD_INFANTRY
    if kind == TYPE_HELICOPTER:
        return _ICON_SPEED_FORWARD_HELICOPTERS
    if kind == TYPE_AIRCRAFT:
        return _ICON_SPEED_FORWARD_AIRCRAFTS
    return _ICON_SPEED_FORWARD_VEHICLES


def _t_armor(ctx: _Ctx) -> str | None:
    """ArmorValue > 0 才有这一格 —— Grad 是 0，所以它只有 7 格。"""
    armor = ctx.data.armor(ctx.unit_id, ctx.selected)
    if armor is None:
        return None
    value = ctx.data.t(_T_ARMORS).i(armor, "ArmorValue")
    return str(value) if value > 0 else None


def _t_health(ctx: _Ctx) -> str | None:
    armor = ctx.data.armor(ctx.unit_id, ctx.selected)
    if armor is None:
        return None
    value = ctx.data.t(_T_ARMORS).i(armor, "MaxHealthPoints")
    return str(value) if value > 0 else None


def _t_soldier_count(ctx: _Ctx) -> str | None:
    if ctx.data.unit_type(ctx.unit) != TYPE_INFANTRY:
        return None
    count = len(ctx.data.squad(ctx.unit_id))
    return str(count) if count > 0 else None


def _t_heavy_lift(ctx: _Ctx) -> str | None:
    mobility = ctx.data.mobility(ctx.unit_id, ctx.selected)
    if mobility is None:
        return None
    value = ctx.data.t(_T_MOBILITY).i(mobility, "HeavyLiftWeight")
    return str(value) if value > 0 else None


def _t_seats(ctx: _Ctx) -> str | None:
    if get_infantry_slots(ctx.data, ctx.unit_id) <= 0:
        return None
    return str(ctx.data.t(_T_UNITS).i(ctx.unit, "InfantrySlots"))


def _t_optics(ctx: _Ctx) -> str | None:
    ground, _low, high = optics_triple(ctx.data, ctx.unit_id, ctx.selected)
    if ground == 0 and high == 0:
        return None
    head = high if ctx.data.category(ctx.unit) == CAT_AIRCRAFTS else ground
    return fmt_number(head)


def _t_stealth(ctx: _Ctx) -> str | None:
    """1 / 隐蔽值，格式 0.##（恒真，没有条件）。

    ⚠️ 隐蔽值不只在 Units 里：**Options.StealthOverride 会盖掉它**
    （全库 20 条选项在用：0.4 / 0.666 / 1.0，例如 Stryker RV 换炮塔、TigrBRSHM、BRM3）。
    只读 Units.CurrentStealth 的话，这些单位换装后卡片上的隐蔽是错的。
    """
    stealth, _option_id = ctx.data.stealth_override(ctx.unit_id, ctx.selected)
    if not stealth:
        return None
    return fmt_number(1.0 / stealth)


def _t_speed_forward(ctx: _Ctx) -> str | None:
    mobility = ctx.data.mobility(ctx.unit_id, ctx.selected)
    if mobility is None:
        return None
    rows = ctx.data.t(_T_MOBILITY)
    kind = ctx.data.unit_type(ctx.unit)
    if kind == TYPE_AIRCRAFT:
        preset = ctx.data.fly_preset(ctx.unit_id, ctx.selected)
        if preset is None:
            return None
        # Round5(Round(MaxSpeed × 3.6))  —— 读 FlyPreset，**不是 Mobility**
        return str(round5(bankers_round(ctx.data.t(_T_FLY_PRESETS).f(preset, "MaxSpeed") * 3.6)))
    if kind == TYPE_SHIP:
        # ⚠️ 船这一支**没有截图样本**：ISIL 里前进那一格读的是 MaxSpeedRoad(0x40)，
        # 而船的水面速度在同表的 MaxSpeedWater(0x48)。全库舰船只有几条，
        # 拿不到对照，所以先用 MaxSpeedWater 并标成强假设（研究文档 §9.10）。
        return fmt_number(rows.f(mobility, "MaxSpeedWater"))
    return fmt_number(rows.f(mobility, "MaxSpeedRoad"))


def _t_speed_back(ctx: _Ctx) -> str | None:
    """倒车那一行**只对 Type == 4（载具）发射**（固定翼第 6 格是回转半径）。"""
    if ctx.data.unit_type(ctx.unit) != TYPE_VEHICLE:
        return None
    mobility = ctx.data.mobility(ctx.unit_id, ctx.selected)
    if mobility is None:
        return None
    return fmt_number(ctx.data.t(_T_MOBILITY).f(mobility, "MaxSpeedReverse"))


def _t_agility(ctx: _Ctx) -> str | None:
    if ctx.data.unit_type(ctx.unit) != TYPE_HELICOPTER:
        return None
    mobility = ctx.data.mobility(ctx.unit_id, ctx.selected)
    if mobility is None:
        return None
    return fmt_number(ctx.data.t(_T_MOBILITY).f(mobility, "Agility"))


def _t_turn_radius(ctx: _Ctx) -> str | None:
    """固定翼第 6 格 = 回转半径（不是"倒车"）。

    floor5(GetRotationRadius(MaxSpeed, MaxSpeedYaw) × 2)；Su-25T = 510。
    """
    if ctx.data.unit_type(ctx.unit) != TYPE_AIRCRAFT:
        return None
    preset = ctx.data.fly_preset(ctx.unit_id, ctx.selected)
    if preset is None:
        return None
    rows = ctx.data.t(_T_FLY_PRESETS)
    radius = rotation_radius(rows.f(preset, "MaxSpeedYaw"), rows.f(preset, "MaxSpeed"))
    return str(round5(bankers_round(radius * EFFECTIVE_RANGE_MULTIPLIER)))


def _t_fuel(ctx: _Ctx) -> str | None:
    if ctx.data.unit_type(ctx.unit) != TYPE_AIRCRAFT:
        return None
    mobility = ctx.data.mobility(ctx.unit_id, ctx.selected)
    if mobility is None:
        return None
    return fmt_number(ctx.data.t(_T_MOBILITY).f(mobility, "LoiteringTime"))


def _t_weight(ctx: _Ctx) -> str | None:
    """重量：Type == 2 或 4 才有这一格。步兵是 125 × 人数算出来的。"""
    if ctx.data.unit_type(ctx.unit) not in (TYPE_INFANTRY, TYPE_VEHICLE):
        return None
    value, _computed = card_weight(ctx.data, ctx.unit_id)
    return str(value)


def _t_refund(ctx: _Ctx) -> str:
    """表头那个大数字 = **阵亡后**的回转时间（options=1）。

    这一格的图标是骷髅时钟（`DeathTimer.png`）—— 语义对得上，不是凑巧：
    显示的本来就是"阵亡后"那条（用户确认）。
    """
    return fmt_time(
        purchase_delay(ctx.data, ctx.unit_id, REFUND_IS_DEAD, cost=ctx.cost, selected=ctx.selected)
    )


@dataclass(frozen=True, slots=True)
class StatSpec:
    """数值行的一条：**(图标, 取值函数, 适用条件)**。

    取值函数返回 None = 这一格不发射（条件不过）。顺序即 SetUnitStatistic 的发射顺序。
    """

    key: str
    icon: str | Callable[[_Ctx], str]
    loc_key: str
    value: Callable[[_Ctx], str | None]

    def icon_of(self, ctx: _Ctx) -> str:
        return self.icon(ctx) if callable(self.icon) else self.icon


#: 14 条，顺序照抄 UnitInfoCard.SetUnitStatistic（VA 0x180535450）。
#: ⚠️ **不要按单位类型写死模板**：这里每条自己判条件，出来的格数自然不同
#: （步兵 8 格 / 载具 8 格或 7 格 / 直升机 9 格 / 固定翼 8 格）。
STAT_SPECS: tuple[StatSpec, ...] = (
    StatSpec("armor", _ICON_ARMOR, _L_ARMOR, _t_armor),
    # 生命值的图标按类型换：步兵用 HealthInfantry
    StatSpec(
        "health",
        lambda ctx: (
            _ICON_HEALTH_INFANTRY if ctx.data.unit_type(ctx.unit) == TYPE_INFANTRY else _ICON_HEALTH
        ),
        _L_HEALTH,
        _t_health,
    ),
    StatSpec("soldier_count", _ICON_SOLDIER_COUNT, _L_SOLDIER_COUNT, _t_soldier_count),
    StatSpec("heavy_lift", _ICON_HEAVY_LIFT, _L_HEAVY_LIFT, _t_heavy_lift),
    StatSpec("seats", _ICON_SEATS, _L_SEATS, _t_seats),
    StatSpec("optics", _ICON_OPTICS, _L_OPTICS, _t_optics),
    StatSpec("stealth", _ICON_STEALTH, _L_STEALTH, _t_stealth),
    StatSpec("speed_forward", _speed_forward_icon, _L_SPEED_FORWARD, _t_speed_forward),
    StatSpec("speed_back", _ICON_SPEED_BACKWARD_VEHICLES, _L_SPEED_BACK, _t_speed_back),
    StatSpec("agility", _ICON_AGILITY_HELICOPTERS, _L_AGILITY, _t_agility),
    StatSpec("turn_radius", _ICON_AGILITY_PLANES, _L_TURN_RADIUS, _t_turn_radius),
    StatSpec("fuel", _ICON_FUEL, _L_FUEL, _t_fuel),
    StatSpec("weight", _ICON_WEIGHT, _L_WEIGHT, _t_weight),
    StatSpec("refund", _ICON_REFUND, _L_REFUND, _t_refund),
)


def _stat_hint(ctx: _Ctx, key: str) -> str:
    """悬浮提示。游戏里 tooltip = 本地化键的第二行；视野/撤回另有专属三条。"""
    data = ctx.data
    if key == "optics":
        ground, low, high = optics_triple(data, ctx.unit_id, ctx.selected)
        parts = [
            f"{data.label(_L_GROUND_RANGE)} {fmt_number(ground)}",
            f"{data.label(_L_LOWALT_RANGE)} {fmt_number(low)}",
            f"{data.label(_L_HIGHALT_RANGE)} {fmt_number(high)}",
        ]
        return "\n".join(part for part in parts if part)
    if key == "refund":
        unscathed = purchase_delay(
            data, ctx.unit_id, REFUND_UNTOUCHED, cost=ctx.cost, selected=ctx.selected
        )
        no_ammo = purchase_delay(
            data, ctx.unit_id, REFUND_OUT_OF_AMMO, cost=ctx.cost, selected=ctx.selected
        )
        death = purchase_delay(
            data, ctx.unit_id, REFUND_IS_DEAD, cost=ctx.cost, selected=ctx.selected
        )
        return "\n".join(
            (
                f"{data.label(_L_REFUND_UNSCATHED)} - {fmt_time(unscathed)}",
                f"{data.label(_L_REFUND_NOAMMO)} - {fmt_time(no_ammo)}",
                f"{data.label(_L_REFUND_DEATH)} - {fmt_time(death)}",
            )
        )
    if key == "weight":
        value, computed = card_weight(data, ctx.unit_id)
        if computed:
            members = len(data.squad(ctx.unit_id))
            return f"{value} = {INFANTRY_WEIGHT_PER_MEMBER} × {members} 名成员"
    return ""


def _unit_ref(column: str, unit_id: int) -> CellRef:
    return CellRef(_T_UNITS, unit_id, column)


def _link_ref(table: str, link: Sequence[Any] | None, column: str, rows: Rows) -> CellRef | None:
    if link is None:
        return None
    return CellRef(table, rows.i(link, "Id"), column)


def _armor_stat_ref(ctx: _Ctx, column: str) -> tuple[CellRef | None, CellRef | None, str]:
    """护甲 / 生命那一格的 (值在哪, 本单位指向谁, 备注)。

    被选项覆盖时（Options.ArmorId）值来自**选项指的那一行** Armors，而「本单位指向它」
    的那一格是 Options#N.ArmorId —— 编辑落点要跟着走，否则改了 UnitArmors，
    被覆盖的那张卡根本不会变（clone.py 的「另存为新模板」也正是按 link_ref 指过去的）。
    """
    data = ctx.data
    override = data.option_override(ctx.unit_id, ctx.selected, "ArmorId", _T_ARMORS)
    armor = data.armor(ctx.unit_id, ctx.selected)
    ref = (
        CellRef(_T_ARMORS, data.t(_T_ARMORS).i(armor, "Id"), column) if armor is not None else None
    )
    if override is not None:
        option_id = override[0]
        return (
            ref,
            CellRef(_T_OPTIONS, option_id, "ArmorId"),
            f"被选项 #{option_id} 的 Options.ArmorId 覆盖（ApplyCurrentMods 写 unit.CurrentArmor）",
        )
    link = _link_ref(
        _T_UNIT_ARMORS, data.armor_link(ctx.unit_id), "ArmorId", data.t(_T_UNIT_ARMORS)
    )
    return (ref, link, "")


def _mobility_stat_ref(ctx: _Ctx, column: str) -> tuple[CellRef | None, CellRef | None, str]:
    """速度 / 倒车 / 敏捷 / 悬停 / 起重那一格的落点（口径同 _armor_stat_ref）。"""
    data = ctx.data
    override = data.option_override(ctx.unit_id, ctx.selected, "MobilityId", _T_MOBILITY)
    mobility = data.mobility(ctx.unit_id, ctx.selected)
    ref = (
        CellRef(_T_MOBILITY, data.t(_T_MOBILITY).i(mobility, "Id"), column)
        if mobility is not None
        else None
    )
    if override is not None:
        option_id = override[0]
        return (
            ref,
            CellRef(_T_OPTIONS, option_id, "MobilityId"),
            f"被选项 #{option_id} 的 Options.MobilityId 覆盖"
            "（ApplyCurrentMods 写 unit.CurrentMobility）",
        )
    link = _link_ref(
        _T_UNIT_PROPULSIONS,
        data.mobility_link(ctx.unit_id),
        "MobilityId",
        data.t(_T_UNIT_PROPULSIONS),
    )
    return (ref, link, "")


def _stat_ref(ctx: _Ctx, key: str) -> tuple[CellRef | None, CellRef | None, str]:
    """(值在哪, 本单位指向谁, 备注)。编辑与"另存为新模板"全靠它。"""
    data = ctx.data
    if key == "armor":
        return _armor_stat_ref(ctx, "ArmorValue")
    if key == "health":
        return _armor_stat_ref(ctx, "MaxHealthPoints")
    if key == "soldier_count":
        return (None, None, "人数 = SquadMembers 的行数；靠增删行来改")
    if key == "heavy_lift":
        return _mobility_stat_ref(ctx, "HeavyLiftWeight")
    if key == "seats":
        return (_unit_ref("InfantrySlots", ctx.unit_id), None, "")
    if key == "optics":
        sensors = effective_sensors(data, ctx.unit_id, ctx.selected)
        _ground, _low, high = optics_triple(data, ctx.unit_id, ctx.selected)
        column = (
            "OpticsHighAltitude"
            if data.category(ctx.unit) == CAT_AIRCRAFTS and high
            else "OpticsGround"
        )
        ref = None
        if sensors:
            best = max(sensors, key=lambda sensor: data.t(_T_SENSORS).f(sensor, column))
            ref = CellRef(_T_SENSORS, data.t(_T_SENSORS).i(best, "Id"), column)
        links = data.t(_T_SENSOR_UNITS).where("UnitId", ctx.unit_id)
        link = (
            CellRef(_T_SENSOR_UNITS, data.t(_T_SENSOR_UNITS).i(links[0], "Id"), "SensorId")
            if links
            else None
        )
        return (ref, link, "视野 = 取 Max 后 × 2（EffectiveRangeMultiplier）")
    if key == "stealth":
        _value, option_id = data.stealth_override(ctx.unit_id, ctx.selected)
        if option_id is not None:
            return (
                CellRef(_T_OPTIONS, option_id, "StealthOverride"),
                None,
                "隐蔽 = 1 / 该列；这一格是**选项**盖的（Options.StealthOverride）",
            )
        return (_unit_ref("CurrentStealth", ctx.unit_id), None, "隐蔽 = 1 / 该列")
    if key in ("speed_forward", "speed_back", "agility", "fuel"):
        kind = data.unit_type(ctx.unit)
        if key == "speed_forward" and kind == TYPE_AIRCRAFT:
            preset = data.fly_preset(ctx.unit_id, ctx.selected)
            ref = (
                CellRef(_T_FLY_PRESETS, data.t(_T_FLY_PRESETS).i(preset, "Id"), "MaxSpeed")
                if preset is not None
                else None
            )
            return (ref, None, "固定翼前进 = Round5(Round(MaxSpeed × 3.6))，读 FlyPreset")
        column = {
            "speed_forward": "MaxSpeedRoad",
            "speed_back": "MaxSpeedReverse",
            "agility": "Agility",
            "fuel": "LoiteringTime",
        }[key]
        if key == "speed_forward" and kind == TYPE_SHIP:
            column = "MaxSpeedWater"
        return _mobility_stat_ref(ctx, column)
    if key == "turn_radius":
        preset = data.fly_preset(ctx.unit_id, ctx.selected)
        ref = (
            CellRef(_T_FLY_PRESETS, data.t(_T_FLY_PRESETS).i(preset, "Id"), "MaxSpeedYaw")
            if preset is not None
            else None
        )
        return (ref, None, "floor5(v×(tanθ+1)/tanθ×2)，θ = MaxSpeedYaw")
    if key == "weight":
        value, computed = card_weight(data, ctx.unit_id)
        if computed:
            return (None, None, f"{value} = {INFANTRY_WEIGHT_PER_MEMBER} × 班成员数")
        return (_unit_ref("Weight", ctx.unit_id), None, "")
    if key == "refund":
        return (
            _unit_ref("Cost", ctx.unit_id),
            None,
            "1.5×Cost + 420 + TimeModifierResurrect（含已选选项修正）",
        )
    return (None, None, "")


def build_stats(
    data: CardData,
    unit_id: int,
    *,
    selected: Mapping[int, int] | None = None,
    cost: float | None = None,
) -> tuple[CardStat, ...]:
    """按 STAT_SPECS 逐条筛，出数值行。"""
    unit = data.unit(unit_id)
    if unit is None:
        return ()
    ctx = _Ctx(
        data=data,
        unit_id=unit_id,
        unit=unit,
        selected=selected,
        cost=card_cost(data, unit_id, selected) if cost is None else cost,
    )
    out: list[CardStat] = []
    for spec in STAT_SPECS:
        value = spec.value(ctx)
        if value is None or value == "":
            continue
        ref, link, note = _stat_ref(ctx, spec.key)
        evidence = EVIDENCE_CONFIRMED
        if spec.key == "speed_forward" and data.unit_type(unit) == TYPE_SHIP:
            evidence = EVIDENCE_ASSUMED
        # 共享度按**值本身那一行**算（不是关联行）：传感器行 142 个单位共用，
        # 而 SensorUnits 那一行只属于本单位 —— 两个数说的是不同的事。
        shared = data.sharing.of(ref.table, ref.row_id) if ref is not None else 0
        icon_name = spec.icon_of(ctx)
        out.append(
            CardStat(
                key=spec.key,
                icon=icon_name,
                label=data.label(spec.loc_key, spec.key),
                sprite=STAT_ICON_KEYS.get(icon_name, ""),
                value=value,
                hint=_stat_hint(ctx, spec.key),
                value_ref=ref,
                link_ref=link,
                shared_with=shared,
                note=note,
                evidence=evidence,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# 能力图标
# --------------------------------------------------------------------------- #

_L_ABILITY = {
    "airdrop": "ui_infocard_ability_airdropable",
    "amphibious": "ui_infocard_ability_amphibious",
    "laser": "ui_infocard_ability_laser",
    "smoke": "ui_infocard_ability_smoke",
    "sprint": "ui_infocard_ability_sprint",
    "aps": "ui_infocard_ability_aps",
    "decoy": "ui_infocard_ability_decoys",
    "radar": "ui_infocard_ability_radar",
    "resupply": "ui_infocard_ability_resupply",
    "autofire": "ui_infocard_ability_autofire",
    "sneak": "ui_infocard_ability_sneak",
}


def build_abilities(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> tuple[AbilityChip, ...]:
    """右上那排能力图标。

    判据全部来自 Abilities 的布尔列（走 UnitAbilities 关联表）与 Mobility：
    IsSmoke / IsInfantrySprint / IsSneakAbility / IsAPS / IsDecoy / IsRadar /
    IsLaserDesignator / IsResupplyAbility / IsArtilleryAutoFire / IsAirDroppable / IsAmphibious。
    顺序照 InfocardConfig 的字段顺序（LaserDesignation, Smoke, Sprint, Aps, Decoy, Radar,
    Amphibious, Airdrop, Resupply, AutoFire, Sneak）。
    """
    abilities = data.ability_rows(unit_id)
    rows = data.t(_T_ABILITIES)
    # 两栖那一位读的是 Mobility.IsAmphibious —— 而 Mobility 会被选项的 MobilityId 覆盖
    # （unit.CurrentMobility，见 CardData.mobility），所以 selected 要传进来。
    mobility = data.mobility(unit_id, selected)
    mobility_rows = data.t(_T_MOBILITY)

    def any_ability(column: str) -> bool:
        return any(rows.b(ability, column) for ability in abilities)

    def first_row(column: str) -> int:
        """这一位能力落在哪一行（界面重做 §1 第 4 条：点它 -> 去看那一行的 51 列）。"""
        for ability in abilities:
            if rows.b(ability, column):
                return rows.i(ability, "Id")
        return 0

    smoke_count = sum(
        rows.i(ability, "SmokeAmmunitionQuantity")
        for ability in abilities
        if rows.b(ability, "IsSmoke")
    )
    has_mobility = mobility is not None

    flags: list[tuple[str, bool, str, int]] = [
        ("laser", any_ability("IsLaserDesignator"), "", first_row("IsLaserDesignator")),
        (
            "smoke",
            any_ability("IsSmoke"),
            f"×{smoke_count}" if smoke_count else "",
            first_row("IsSmoke"),
        ),
        ("sprint", any_ability("IsInfantrySprint"), "", first_row("IsInfantrySprint")),
        ("aps", any_ability("IsAPS"), "", first_row("IsAPS")),
        ("decoy", any_ability("IsDecoy"), "", first_row("IsDecoy")),
        ("radar", any_ability("IsRadar"), "", first_row("IsRadar")),
        # 两栖 / 空投这两条读的是 **Mobility 那一行**（不是 Abilities），
        # 所以它们点了去的是 Mobility（界面上就是"机动"那一块）。
        ("amphibious", has_mobility and mobility_rows.b(mobility, "IsAmphibious"), "", 0),
        ("airdrop", has_mobility and mobility_rows.b(mobility, "IsAirDroppable"), "", 0),
        ("resupply", any_ability("IsResupplyAbility"), "", first_row("IsResupplyAbility")),
        ("autofire", any_ability("IsArtilleryAutoFire"), "", first_row("IsArtilleryAutoFire")),
        ("sneak", any_ability("IsSneakAbility"), "", first_row("IsSneakAbility")),
    ]
    chips: list[AbilityChip] = []
    for key, present, suffix, row_id in flags:
        if not present:
            continue
        loc_key = _L_ABILITY[key]
        label = data.label(loc_key, key)
        chips.append(
            AbilityChip(
                key=key,
                label=f"{label} {suffix}".strip(),
                hint=data.hint(loc_key),
                row_id=row_id,
            )
        )
    return tuple(chips)


# --------------------------------------------------------------------------- #
# 武器（收起态一行 / 展开态一排弹种）
# --------------------------------------------------------------------------- #


def weapon_choice_rows(data: CardData, unit_id: int) -> tuple[WeaponChoiceRow, ...]:
    """配枪下拉的候选：该单位 SquadWeapons 里的那批武器（按 Id 排，稳定）。"""
    links = data.t(_T_SQUAD_WEAPONS).where("UnitId", unit_id)
    pool = sorted({data.t(_T_SQUAD_WEAPONS).i(link, "WeaponId") for link in links})
    out: list[WeaponChoiceRow] = []
    for weapon_id in pool:
        if weapon_id <= 0:
            continue
        weapon = data.weapon(weapon_id)
        if weapon is None:
            continue
        weapons = data.t(_T_WEAPONS)
        type_key = WEAPON_TYPE_KEYS.get(weapons.i(weapon, "Type"), "")
        out.append(
            WeaponChoiceRow(
                weapon_id=weapon_id,
                label=data.display_name(
                    weapons.s(weapon, "HUDName"), weapons.s(weapon, "Name", f"#{weapon_id}")
                ),
                kind=data.loc.enum("weapontype", type_key) if type_key else "",
            )
        )
    return tuple(out)


def squad_loadout(data: CardData, unit_id: int) -> SquadLoadout | None:
    """步兵班的编成。非步兵返回 None（载具的人员走炮塔与运输，不是这套）。"""
    unit = data.unit(unit_id)
    if unit is None or data.unit_type(unit) != TYPE_INFANTRY:
        return None
    rows = data.t(_T_SQUAD_MEMBERS)
    members = tuple(
        SquadMemberRow(
            member_id=rows.i(row, "Id"),
            index=index + 1,
            primary_weapon_id=rows.i(row, "PrimaryWeaponId"),
            special_weapon_id=rows.i(row, "SpecialWeaponId"),
        )
        for index, row in enumerate(data.squad(unit_id))
    )
    return SquadLoadout(
        unit_id=unit_id, members=members, weapon_choices=weapon_choice_rows(data, unit_id)
    )


def new_member_values(
    data: CardData, loadout: SquadLoadout, *, copy_from: int | None = None
) -> dict[str, Any]:
    """【加一名成员】要写哪些列：**复制最后一名成员的装备**（同一个班，装具一样）。

    班里一个人都没有时给一行最小可用的（只填 UnitId），别编一个武器 Id 出来。
    """
    table = data.instance.table(_T_SQUAD_MEMBERS)
    values: dict[str, Any] = {column: None for column in table.columns}
    values["UnitId"] = loadout.unit_id
    source_index = len(loadout.members) - 1 if copy_from is None else copy_from
    if 0 <= source_index < len(loadout.members):
        source = loadout.members[source_index]
        rows = data.t(_T_SQUAD_MEMBERS)
        row = rows.by_id(source.member_id)
        if row is not None:
            for column in table.columns:
                if column in ("Id", "UnitId"):
                    continue
                values[column] = rows.get(row, column)
        values["UnitId"] = loadout.unit_id
    return values


def weapon_ammo_choices(data: CardData, unit_id: int, weapon_id: int) -> tuple[AmmoChoiceRow, ...]:
    """这把武器能换的弹种：**全库**里这把武器用过的弹药（外加本单位已有的）。

    为什么不给全部 630 种弹药：游戏显示哪些弹种由武器自己的 Ammunitions 列表决定，
    随手加一条库外弹药，游戏多半压根不显示 —— 那是【看着改了其实没改】。
    """
    links = data.t(_T_WEAPON_AMMO)
    ammo_index = links.index_of("AmmunitionId")
    candidates: set[int] = set()
    for row in links.all():
        if ammo_index >= len(row):
            continue
        if links.i(row, "WeaponId") == weapon_id:
            candidates.add(links.i(row, "AmmunitionId"))
    owned = {ammo_id for ammo_id, _qty, _order in data.weapon_ammo(unit_id, weapon_id)}
    ammo_rows = data.t(_T_AMMO)
    out: list[AmmoChoiceRow] = []
    for ammo_id in sorted(candidates):
        ammo = data.ammo(ammo_id)
        if ammo is None:
            continue
        trajectory_key = TRAJECTORY_KEYS.get(ammo_rows.i(ammo, "TrajectoryType"), "")
        out.append(
            AmmoChoiceRow(
                ammo_id=ammo_id,
                label=data.display_name(
                    ammo_rows.s(ammo, "HUDName"), ammo_rows.s(ammo, "Name", f"#{ammo_id}")
                ),
                trajectory=data.loc.enum("trajectory", trajectory_key) if trajectory_key else "",
                owned=ammo_id in owned,
            )
        )
    return tuple(out)


def rate_of_fire_second(weapon: Sequence[Any], rows: Rows) -> float:
    """WeaponHelper.RateOfFireSecond(weapon, countOfWeapon=1, 两个修正=1) —— 发/秒。

    出处：WeaponHelper.txt ISIL 010-102。是"最短与最长剖面取中"：

        minTime = TBBMin/c×mod × (mag/burstMax − 1)
                  + (mag − mag/burstMax) × TBSIB + ReloadMin/c×magMod
        maxTime = TBBMax/c×mod × (mag/burstMin − 1)
                  + (mag − mag/burstMin) × TBSIB + ReloadMax/c×magMod
        t       = minTime + (maxTime − minTime) × 0.5      # [0x1861AAB7C] = 0.5
        return    mag / abs(t)                             # [0x1861AAE90] = abs 掩码

    ⚠️ 这几个时序字段**没有被加载逻辑改过**：LoadWeapon（LoadUnits.txt 8743-8871）
    只做 Clone + 两个俯仰角取负 + 灌弹药列表，所以公式看到的就是数据库里的值。
    （这条是"输入列到底对不对"的判据 —— 轻武器的 4.0s 间隔看着像占位，但确实是库里的值。）
    """
    magazine = float(rows.i(weapon, "MagazineSize"))
    burst_min = float(rows.i(weapon, "ShotsPerBurstMin"))
    burst_max = float(rows.i(weapon, "ShotsPerBurstMax"))
    tbb_min = rows.f(weapon, "TimeBetweenBurstsMin")
    tbb_max = rows.f(weapon, "TimeBetweenBurstsMax")
    between = rows.f(weapon, "TimeBetweenShotsInBurst")
    reload_min = rows.f(weapon, "MagazineReloadTimeMin")
    reload_max = rows.f(weapon, "MagazineReloadTimeMax")
    if magazine <= 0 or burst_min <= 0 or burst_max <= 0:
        return 0.0
    low = tbb_min * (magazine / burst_max - 1.0) + (magazine - magazine / burst_max) * between
    high = tbb_max * (magazine / burst_min - 1.0) + (magazine - magazine / burst_min) * between
    total = (low + reload_min) + ((high + reload_max) - (low + reload_min)) * 0.5
    if total == 0:
        return 0.0
    return magazine / abs(total)


def fmt_float32(value: float) -> str:
    """C# Single.ToString() 的默认格式（.NET Core 3.0+ = 最短可往返表示）。

    为什么不用 fmt_number：射程/伤害那些走的是 `Single.ToString(x, _floatRoundStr, culture)`
    （格式串 "0.##"），而瞄准时间/装填时间走的是 `String.Format("{0} sec", 浮点)`
    —— 默认格式，四位小数会被原样打出来（AK 的 1.0 打 "1"，0.6666667 打 "0.6666667"）。
    """
    target = struct.unpack("<f", struct.pack("<f", value))[0]
    for precision in range(1, 10):
        text = f"{target:.{precision}g}"
        if struct.unpack("<f", struct.pack("<f", float(text)))[0] != target:
            continue
        # g 会给 20.0 这种整数套上 2e+01；.NET 的默认格式不打科学计数法（量级正常时）。
        if "e" in text or "E" in text:
            text = f"{Decimal(text):f}"
        return text
    return repr(target)


def weapon_detail_rows(
    data: CardData, unit_id: int, weapon: Sequence[Any]
) -> tuple[DetailRow, ...]:
    """武器自己的那几行（选武器时显示）。

    出处：UnitInfoCard.OnWeaponButtonSelected（isil 11849 起，ISIL 段 12890-13360）。
    顺序照它调 SetStatisticLine 的先后：瞄准时间 -> 弹匣容量 -> 装填时间 -> 射速 -> ...

    只放解出来的：瞄准时间 / 弹匣容量 / 装填时间。
    【射速】那一行卡在 `[rbx+48]`（一个还没追出来的对象）上，见 PENDING_DETAIL_ROWS。
    """
    weapons = data.t(_T_WEAPONS)
    unit = data.unit(unit_id)
    kind = weapons.i(weapon, "Type")
    magazine = weapons.i(weapon, "MagazineSize")
    out: list[DetailRow] = []

    # 【弹匣容量 + 装填时间】与【射速】**互斥**（ISIL 380-395）——这是**分支**，不是并列的行：
    #   flag = MagazineSize > 1 且该武器第一个弹种的轨迹是直射弹药(10)
    #   flag 且类型不是 110/140  -> 走射速那一支
    #   非 flag 且类型 == 60(GL) -> 也是射速那一支
    #   其余                      -> 弹匣容量(+装填时间)
    # 射速那一支的值还没解出来（卡在 [rbx+48]），所以那些武器这两行**都不出** ——
    # 宁缺勿错：步枪卡片上多一行游戏里没有的"弹匣容量"比少一行更糟。
    entries = data.weapon_ammo(unit_id, weapons.i(weapon, "Id"))
    first_direct = (
        bool(entries)
        and data.t(_T_AMMO).i(data.ammo(entries[0][0]), "TrajectoryType") == DIRECT_SHOT_TRAJECTORY
    )
    flag = magazine > 1 and first_direct
    rate_of_fire_branch = (flag and kind not in (110, 140)) or (not flag and kind == 60)

    # —— 瞄准时间：AimTimeMin == AimTimeMax ? "{min} sec" : "{min} - {max} sec"
    # 炸弹（160 BombDumb / 161 BombDrag）不出这一行。
    if kind not in (160, 161):
        aim_min = weapons.f(weapon, "AimTimeMin")
        aim_max = weapons.f(weapon, "AimTimeMax")
        value = (
            f"{fmt_float32(aim_min)} sec"
            if aim_min == aim_max
            else f"{fmt_float32(aim_min)} - {fmt_float32(aim_max)} sec"
        )
        out.append(
            DetailRow(
                key="aim_time",
                label=data.label(_L_AIM_TIME, "瞄准时间"),
                value=value,
            )
        )

    # —— 射速那一支：RateOfFire(weapon,1) × 该武器第一个弹种的 HUDMultiplier，按 5 取整。
    # 出处 ISIL 504-545：先 RateOfFire(weapon, 1)，再乘 [rbx+0x30]，rbx 是**该武器的第一个弹种**
    # （反汇编 12216 处 rbx = FirstOrDefault(weapon.Ammunitions)，
    #   而 Ammunitions+0x30 = HUDMultiplier），
    # 最后 RoundToNearestMultiple(x, 5.0)；提示键 ui_infocard_weapon_hint_rate_of_fire。
    if rate_of_fire_branch:
        first_ammo = data.ammo(entries[0][0]) if entries else None
        multiplier = data.t(_T_AMMO).i(first_ammo, "HUDMultiplier") if first_ammo else 0
        per_minute = rate_of_fire_second(weapon, weapons) * RATE_OF_FIRE_PER_MINUTE
        rate = round_to_multiple(per_minute * multiplier, RATE_OF_FIRE_STEP)
        out.append(
            DetailRow(
                key="rate_of_fire",
                label=data.label(_L_RATE_OF_FIRE, "射速"),
                value=fmt_number(rate),
                hint=data.label(_L_RATE_OF_FIRE_HINT, ""),
                # 公式与输入都读全了，但**没有截图样本**对过（AK 算出来是 45），按强假设标。
                evidence=EVIDENCE_ASSUMED,
            )
        )

    # —— 弹匣容量：MagazineSize > 1（类型那一层由上面的分支决定）
    if not rate_of_fire_branch and magazine > 1:
        out.append(
            DetailRow(
                key="magazine",
                label=data.label(_L_MAGAZINE, "弹匣容量"),
                value=str(magazine),
            )
        )

    # —— 装填时间：min 与 max 差得比容差大就打区间，否则只打 max。
    # 固定翼上只给航炮（116 PlaneGun）出这一行。
    is_aircraft = unit is not None and data.unit_type(unit) == TYPE_AIRCRAFT
    if not rate_of_fire_branch and (not is_aircraft or kind == 116):
        reload_min = weapons.f(weapon, "MagazineReloadTimeMin")
        reload_max = weapons.f(weapon, "MagazineReloadTimeMax")
        tolerance = max(abs(reload_min), abs(reload_max)) * RELOAD_TOLERANCE
        value = (
            f"{fmt_float32(reload_min)} - {fmt_float32(reload_max)} sec"
            if abs(reload_max - reload_min) > tolerance
            else f"{fmt_float32(reload_max)} sec"
        )
        out.append(
            DetailRow(
                key="reload_time",
                label=data.label(_L_RELOAD_TIME, "装填时间"),
                value=value,
            )
        )

    # —— 齐射时长：只对榴弹炮 / 迫击炮 / 多管火箭炮发射（SALVO_WEAPON_TYPES），
    # 值是**短 / 中 / 长**三档齐射发数（ISIL 615-644：`"{0} / {1} / {2}"` 配
    # ShortGroundAttackBurst / NormalGroundAttackBurst / LongGroundAttackBurst）。
    # 样本：Grad = 10 / 20 / 40、HIMARS = 2 / 3 / 6（设计 §9.2 那张 Grad 截图里也有这一行）。
    if kind in SALVO_WEAPON_TYPES:
        out.append(
            DetailRow(
                key="salvo_length",
                label=data.label(_L_SALVO_LENGTH, "齐射时长"),
                value=(
                    f"{weapons.i(weapon, 'ShortGroundAttackBurst')} / "
                    f"{weapons.i(weapon, 'NormalGroundAttackBurst')} / "
                    f"{weapons.i(weapon, 'LongGroundAttackBurst')}"
                ),
            )
        )
    return tuple(out)


def round_to_multiple(value: float, step: float = ROUND_STEP) -> float:
    """UtilsClass.RoundToNearestMultiple —— 四舍五入到 step 的整数倍。

    step 取自 VA 0x1861AAB74 = 0.05（研究文档 §9.5）。舍入沿用仓里其它地方的
    银行家舍入（C# Math.Round 的默认行为）。
    """
    if step <= 0:
        return value
    return bankers_round(value / step) * step


def _range_rows(data: CardData, ammo: Sequence[Any], prefix: str) -> tuple[DetailRow, ...]:
    """射程那几行。读数出处：AmmoInfoExpansionPanel ISIL 145-365。

        Ground = ammo.GroundRange; Low = LowAltRange; High = HighAltRange
        if (Low  == 0) Low  = Ground          # 3303-3315 的归一化
        if (High == 0) High = Ground
        if (ammo.IsBomb()) -> 一行都不发射
        if ((ammo.TargetType & 24) == 0 || 三个值全相等)
            一条【有效射程】= 前缀 + ToString(Ground*2) + " m"
            前缀 = MinimalRange>0 ? RoundStr(min*2)+" - " : ""
        else 依次发射【地面】【低空】【高空】三条（各自 >0 才发），值 = 前缀 + ToString(x*2) + " m"
    24 = Helicopter(8) | Aircraft(16)，所以"能打空中"的弹药才分三条。
    距离一律 × InfocardConfig.EffectiveRangeMultiplier（同一个配置项，2.0）。
    """
    rows = data.t(_T_AMMO)
    ground = rows.f(ammo, "GroundRange")
    low = rows.f(ammo, "LowAltRange") or ground
    high = rows.f(ammo, "HighAltRange") or ground
    minimal = rows.f(ammo, "MinimalRange")
    multiplier = EFFECTIVE_RANGE_MULTIPLIER

    if _is_bomb(data, ammo):
        return ()

    head = f"{fmt_number(minimal * multiplier)} - " if minimal > 0 else ""
    target = rows.i(ammo, "TargetType")
    if (target & 24) == 0 or (ground == low == high):
        return (
            DetailRow(
                key="range",
                label=data.label(_L_EFFECTIVE_RANGE, "有效射程"),
                value=f"{head}{fmt_number(ground * multiplier)} m",
            ),
        )
    out: list[DetailRow] = []
    for key, loc_key, value in (
        ("range_ground", _L_GROUND_RANGE, ground),
        ("range_low", _L_LOWALT_RANGE, low),
        ("range_high", _L_HIGHALT_RANGE, high),
    ):
        if value <= 0:
            continue
        out.append(
            DetailRow(
                key=key,
                label=data.label(loc_key, key),
                value=f"{prefix}{fmt_number(value * multiplier)} m",
                # 三条各自的标题与值一一对应是**读出来的形状**，但没有截图对过。
                evidence=EVIDENCE_ASSUMED,
            )
        )
    return tuple(out)


def _is_bomb(data: CardData, ammo: Sequence[Any]) -> bool:
    """Ammunitions.IsBomb()（ISIL 171 行那次调用）：名字里带 clus 或者轨迹是炸弹。

    出处：dump.cs 的 Ammunitions.AMMO_CLUSTER_NAME_SUBSTRING = "clus"，
    以及 IsBomb 自身（轨迹 LowDragBomb 400 / HighDragBomb 410）。
    """
    name = data.t(_T_AMMO).s(ammo, "Name").lower()
    if "clus" in name:
        return True
    return data.t(_T_AMMO).i(ammo, "TrajectoryType") in (400, 410)


def ammo_target_traits(data: CardData, ammo: Sequence[Any]) -> tuple[AmmoTarget, ...]:
    """弹药「能打什么」—— AmmoInfoExpansionPanel.SetTargetTraits（ISIL 22-297）。

    读 Ammunitions.TargetType 的位，每位配一个固定的 sprite 与文案。
    **Ground(1) 与 Ship(32) 游戏里不画图标** —— 所以步枪那种 TargetType == 1 的弹药
    这一排是空的（游戏里也空）。判据：那两段汇编里压根没有 test bit 0 / bit 5。

    导弹类那四位有一条合并规则（ISIL 213-266）：**全中**时只出一条【目标 - 导弹】
    （sprite 0x1E8 + 文案 0x238），否则各自一条、共用导弹图标、文案用换行拼再 TrimEnd。
    """
    rows = data.t(_T_AMMO)
    bits = rows.i(ammo, "TargetType")
    out: list[AmmoTarget] = [
        AmmoTarget(key=name, icon=TARGET_ICON_KEYS[name], label=data.label(loc_key, fallback))
        for mask, name, loc_key, fallback in TARGET_TRAIT_SPECS
        if bits & mask
    ]
    missiles_icon = TARGET_ICON_KEYS["missiles"]
    if (bits & TARGET_MISSILE_BITS) == TARGET_MISSILE_BITS:
        out.append(
            AmmoTarget(
                key="missiles",
                icon=missiles_icon,
                label=data.label(_L_TARGET_MISSILES, "目标 - 导弹"),
            )
        )
    else:
        parts = [
            data.label(loc_key, fallback)
            for mask, loc_key, fallback in TARGET_MISSILE_SPECS
            if bits & mask
        ]
        if parts:
            out.append(AmmoTarget(key="missiles", icon=missiles_icon, label="\n".join(parts)))
    return tuple(out)


#: 游戏那排目标图标的固定顺序（`TARGET_TRAIT_SPECS` + 导弹那一条的键）。
_TARGET_ORDER: tuple[str, ...] = ("infantry", "vehicle", "helicopter", "aircraft", "missiles")


def weapon_targets(ammo_list: Sequence[AmmoRow]) -> tuple[AmmoTarget, ...]:
    """这一把武器**所有弹种**能打的目标（并集，按游戏那排的固定顺序）。

    为什么不是"第一个弹种的"：一把枪常常两种弹（AP / HE），只取第一个会**漏**。
    实测（52085）：

      BMD-2 的 2А42-30mm —— AP 只有【载具】，HE 有【步兵/载具/直升机】；
      RPG-7V —— PG-7VL 有【载具/直升机】，TBG-7L 温压多一个【步兵】。

    只画第一个弹种，卡片就会写着"这把枪打不了步兵"（用户报的"可瞄准目标的图标没显示全"）。
    每个弹种各自那排仍然在详细模式里（`AmmoRow.targets`）。
    """
    found: dict[str, AmmoTarget] = {}
    for ammo in ammo_list:
        for target in ammo.targets:
            found.setdefault(target.key, target)
    return tuple(found[key] for key in _TARGET_ORDER if key in found)


def ammo_detail_rows(
    data: CardData, unit_id: int, weapon: Sequence[Any], ammo: Sequence[Any]
) -> tuple[DetailRow, ...]:
    """一个弹种的详情行（**只放解出来的**）。出处见研究文档 §9.4/§9.5。

    顺序照 SetMainAmmoStats 里 SetStatisticLine 的先后：类型 -> 射程 -> ... -> 补给。
    还没解的行不在这里，见 PENDING_DETAIL_ROWS。
    """
    rows = data.t(_T_AMMO)
    unit = data.unit(unit_id)
    out: list[DetailRow] = []

    trajectory_key = TRAJECTORY_KEYS.get(rows.i(ammo, "TrajectoryType"), "")
    if trajectory_key:
        out.append(
            DetailRow(
                key="trajectory",
                label=data.label(_L_TRAJECTORY, "类型"),
                value=data.loc.enum("trajectory", trajectory_key),
            )
        )

    out.extend(_range_rows(data, ammo, ""))

    # 【伤害】与【每秒伤害】**互斥**（ISIL 366-427）：白名单里的武器显示每秒伤害，其余显示伤害。
    #
    # 每秒伤害 = RoundToNearestMultiple(RateOfFireSecond(weapon, 1) × ammo.Damage, 0.05) + "/s"
    # 出处 ISIL 366-427（RateOfFireSecond 本身见 rate_of_fire_second 的注释）。
    #
    # ⚠️ 上一轮把它标成"输入列对不上、先不实现"，根因是**漏了 0.05 那一步取整**：
    #   判据把 0.375 当成"应该显示 0.4 却算不出来"，其实 0.375 → 7.5 → 取整 8 → **0.4**。
    #   两个实机样本（VDV Metis-M 那两张截图）逐字复核：
    #     AK-74RMO  0.25   × 1.5  = 0.375 -> 0.4/s   实机 0.4/s  ✓
    #     PKP-M     0.4839 × 2.25 = 1.089 -> 1.1/s   实机 1.1/s  ✓
    weapon_type = data.t(_T_WEAPONS).i(weapon, "Type")
    damage = rows.f(ammo, "Damage")
    if weapon_type in DPS_WEAPON_TYPES:
        if damage:
            per_second = rate_of_fire_second(weapon, data.t(_T_WEAPONS)) * damage
            out.append(
                DetailRow(
                    key="damage_per_second",
                    label=data.label(_L_DAMAGE_PER_SECOND, "每秒伤害"),
                    value=f"{fmt_number(round_to_multiple(per_second, ROUND_STEP))}/s",
                )
            )
    elif damage:
        out.append(
            DetailRow(
                key="damage",
                label=data.label(_L_DAMAGE, "伤害"),
                value=fmt_number(round_to_multiple(damage)),
            )
        )

    # 爆炸半径 = HealthAOERadius × 2 + " m"（ISIL 507-559），只在 >0 时发。
    blast_radius = rows.f(ammo, "HealthAOERadius")
    if blast_radius > 0:
        out.append(
            DetailRow(
                key="blast_radius",
                label=data.label(_L_BLAST_RADIUS, "爆炸半径"),
                value=f"{fmt_number(blast_radius * EFFECTIVE_RANGE_MULTIPLIER)} m",
            )
        )

    # 穿深 = "{ground} mm {装甲类型}"，两个穿深不同就打区间（ISIL 595-797）。
    # ⚠️ 动能伤害 / 爆炸伤害**不是单独的行** —— 它们是这一格的类型后缀
    #   （ArmorTargeted == Kinetic(1) ? 动能 : 爆炸），别当成两行去实现。
    penetration = rows.f(ammo, "PenetrationAtGroundRange")
    penetration_min = rows.f(ammo, "PenetrationAtMinRange")
    armour_label = data.label(_L_KINETIC if rows.i(ammo, "ArmorTargeted") == 1 else _L_HEAT, "")
    head = fmt_number(penetration)
    if penetration != penetration_min:
        head = f"{head} - {fmt_number(penetration_min)}"
    out.append(
        DetailRow(
            key="penetration",
            label=data.label(_L_PENETRATION, "穿深"),
            value=f"{head} mm {armour_label}".strip(),
        )
    )

    # 武器散布 = "V: {垂直 × 2}m / H: {水平 × 2} m"（两个半径都走 EffectiveRangeMultiplier）。
    # 列名出自映射文档（DispersionVerticalRadius / DispersionHorizontalRadius）；
    # 实机样本：AK-74RMO 的 5.45x39mm 是 (1.0, 4.5) -> 实机那一行 `V: 2m / H: 9 m` —— **逐字命中**。
    # 只给 >0 的弹药发行；两个半径有一个是 0 的那一支没有样本对过，标强假设。
    dispersion_v = rows.f(ammo, "DispersionVerticalRadius")
    dispersion_h = rows.f(ammo, "DispersionHorizontalRadius")
    if dispersion_v > 0 or dispersion_h > 0:
        multiplier = EFFECTIVE_RANGE_MULTIPLIER
        out.append(
            DetailRow(
                key="dispersion",
                label=data.label(_L_DISPERSION, "武器散布"),
                value=(
                    f"V: {fmt_number(dispersion_v * multiplier)}m / "
                    f"H: {fmt_number(dispersion_h * multiplier)} m"
                ),
                evidence=(
                    EVIDENCE_CONFIRMED
                    if dispersion_v > 0 and dispersion_h > 0
                    else EVIDENCE_ASSUMED
                ),
            )
        )

    # 补给值 = SupplyCost + " kg"。实机里它在【补给时间】**前面**（ISIL 3992 vs 4075），
    # 这里照实机的先后排（上一轮排反了）。
    supply_cost = rows.f(ammo, "SupplyCost")
    if unit is not None and data.unit_type(unit) != TYPE_AIRCRAFT:
        out.append(
            DetailRow(
                key="supply_cost",
                label=data.label(_L_SUPPLY_COST, "补给值"),
                value=f"{fmt_number(supply_cost)} kg",
                # 分支开关是哪一个局部变量还没钉死（[rbp+119]），所以这条按"非固定翼"给。
                evidence=EVIDENCE_ASSUMED,
            )
        )

    resupply_time = rows.f(ammo, "ResupplyTime")
    if resupply_time > 1.0:
        out.append(
            DetailRow(
                key="resupply_time",
                label=data.label(_L_RESUPPLY_TIME, "补给时间"),
                value=fmt_number(resupply_time),
            )
        )

    # —— 补给消耗 = SupplyCost × UnitInfoCard.AmmoResupplyModifier + " pts"（ISIL 961-991）。
    #    AmmoResupplyModifier 的赋值点（UnitInfoCard ISIL 061-114）：
    #      supplyConfig != null ? supplyConfig.GetAmmoResupplyMultiplier() : 1.0f
    #    cctor 里那个静态默认就是 1.0；游戏里有 SupplyConfig，所以是 4/1000×10 = 0.04。
    #    ⚠️ **更正上一轮的一条误判**：那条注释说"不能拿 0.04 顶替 —— 两回事"，
    #    实测那个静态**就是** GetAmmoResupplyMultiplier 的返回值，没有第二套。
    #    格式照实机：Single.ToString(值, "0.##") + " pts" —— 便宜的弹（0.1 kg）会显示成
    #    "0 pts"，那是实机的写法；精确值放在 tooltip 里（`hint`）。
    resupply_cost = supply_cost * ammo_resupply_multiplier()
    out.append(
        DetailRow(
            key="resupply_cost",
            label=data.label(_L_RESUPPLY_COST, "补给消耗"),
            value=f"{fmt_number(resupply_cost)} pts",
            hint=f"精确值 {resupply_cost:g} pts（实机按 InfocardConfig.RoundDigits 取两位）",
        )
    )

    # —— 无视掩体 = IgnoreCover × 100 + "%"，只在 > 0 时发（ISIL 1030-1094：
    #    IgnoreCover <= 0 直接跳过）。那个 100.0 是 VA 0x1861AADF8，
    #    与"百分数除法"同一个常量（见 docs/研究-单位卡片公式.md 的常量表）。
    ignore_cover = rows.f(ammo, "IgnoreCover")
    if ignore_cover > 0:
        out.append(
            DetailRow(
                key="ignore_cover",
                label=data.label(_L_IGNORE_COVER, "无视掩体"),
                value=f"{fmt_number(ignore_cover * 100)}%",
            )
        )

    # —— 可被主动防御系统拦截：是就画一行，**值就是 "+"**（ISIL 1113 起：
    #    CanBeIntercepted != 0 时标题配 "+"）。
    # ❗ 这一列是 **bool**，必须用 `rows.b()`：`rows.i()` 对 bool 一律给 default（0），
    #    用它写会让这一行永远不出现（实测踩过：全库 103 条可拦截弹药一条都没画出来）。
    if rows.b(ammo, "CanBeIntercepted"):
        out.append(
            DetailRow(
                key="aps_target",
                label=data.label(_L_APS_TARGET, "可被主动防御系统拦截"),
                value="+",
            )
        )
    return tuple(out)


#: 简略模式的列（顺序即列序）。游戏那一行的顺序：
#: 有效射程 -> 携弹量 -> 穿深 -> 火力（每秒伤害；不在白名单里的武器是【伤害】）。
#: 不按武器类型写死：每格自己判"有没有这条详情行"，没有就留空格。
COMPACT_COLUMNS: tuple[str, ...] = ("range", "ammo", "penetration", "power")

#: 前三列的标题。游戏那一行用的是图标 + 徽章（没有文字），所以说不出"游戏原文"；
#: 这三个是产品自己的列名（携弹量那个键游戏里压根没有，见 docs/数据-信息卡字段映射.md）。
_COMPACT_TITLES: dict[str, tuple[str, str]] = {
    "range": (_L_EFFECTIVE_RANGE, "有效射程"),
    "ammo": ("", "携弹量"),
    "penetration": (_L_PENETRATION, "穿深"),
}


def compact_cells(data: CardData, ammo: AmmoRow) -> tuple[CompactCell, ...]:
    """简略模式那一行：把详情行压成 4 个并排的格子（顺序 = COMPACT_COLUMNS）。

    **值一律从 ammo.detail 里取**，只把几个数削成紧凑写法 —— 简略与详细说的是同一件事，
    不会一处改了另一处没改。削法都有实机样本（VDV Metis-M 那两张截图）：

    - 射程取**末段**：PKP-M 详细行是 "10 - 600 m"，实机简略行写的是 "600m"；
    - 穿深取**首个数字**：AK 详细行是 "7 - 15 mm 动能伤害"，实机简略行写的是 "7"；
    - 火力：白名单武器给每秒伤害（"0.4/s"），其余给【伤害】那一格（Metis-M 实机 = 11）。
    """
    by_key = {row.key: row for row in ammo.detail}
    cells: list[CompactCell] = []

    row = by_key.get("range") or by_key.get("range_ground")
    cells.append(
        CompactCell(
            key="range",
            label=data.label(*_COMPACT_TITLES["range"]),
            text=row.value.split(" - ")[-1].strip() if row is not None else "",
            tooltip=f"{row.label} {row.value}" if row is not None else "",
        )
    )

    ammo_note = f"携弹量 ×{ammo.count}"
    if ammo.multiplier != 1:
        ammo_note += f"（Σ{ammo.raw_total} × HUDMultiplier {ammo.multiplier}）"
    cells.append(
        CompactCell(
            key="ammo",
            label=data.label(*_COMPACT_TITLES["ammo"]),
            text=f"×{ammo.count}",
            tooltip=ammo_note,
        )
    )

    row = by_key.get("penetration")
    cells.append(
        CompactCell(
            key="penetration",
            label=data.label(*_COMPACT_TITLES["penetration"]),
            text=row.value.split(" mm")[0].split(" - ")[0].strip() if row is not None else "",
            tooltip=f"{row.label} {row.value}" if row is not None else "",
        )
    )

    # 每秒伤害与【伤害】互斥，所以这一列两选一 —— 标签跟着真正画出来的那条走。
    row = by_key.get("damage_per_second") or by_key.get("damage")
    cells.append(
        CompactCell(
            key="power",
            label=row.label if row is not None else "",
            text=row.value if row is not None else "",
            tooltip=f"{row.label} {row.value}" if row is not None else "",
        )
    )
    return tuple(cells)


def compact_titles(weapons: Sequence[WeaponGroupRow]) -> tuple[str, ...]:
    """简略模式的列标题（与 COMPACT_COLUMNS 同序）。

    第四列跟着数据走：都是【每秒伤害】就写每秒伤害、都是【伤害】就写伤害、
    两个都有（同一张卡上有的武器在白名单里、有的不在）就写中性的"火力" ——
    写死一个会与格子里的值对不上。
    """
    rows = [weapon.compact for weapon in weapons if len(weapon.compact) == len(COMPACT_COLUMNS)]
    titles: list[str] = []
    for index in range(len(COMPACT_COLUMNS)):
        labels = {row[index].label for row in rows if row[index].label}
        titles.append(labels.pop() if len(labels) == 1 else "火力")
    return tuple(titles)


def weapon_groups(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> dict[int, tuple[int, ...]]:
    """按**首个弹种**把武器分组（UnitInfoCard b__109_4）。

    没有 WeaponAmmunitions 行的武器会被跳过（游戏那个 First() 会拿到空列表）。
    武器来自 unit.Turrets —— 那是 **ApplyCurrentMods 之后**的炮塔（选项换挂架），
    所以 selected 必须一路传到底，否则画出来的还是缺省挂架那一排。
    """
    groups: dict[int, list[int]] = {}
    for weapon_id in data.weapon_ids(unit_id, selected):
        ammo = data.weapon_ammo(unit_id, weapon_id)
        if not ammo:
            continue
        groups.setdefault(ammo[0][0], []).append(weapon_id)
    return {key: tuple(value) for key, value in groups.items()}


def _ammo_order(data: CardData, unit_id: int, weapon_id: int) -> tuple[int, ...]:
    return tuple(ammo_id for ammo_id, _quantity, _order in data.weapon_ammo(unit_id, weapon_id))


def build_weapons(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> tuple[WeaponGroupRow, ...]:
    """收起态那几行武器 + 展开态每个弹种的显示值（selected = 玩家选好的改型）。"""
    weapons = data.t(_T_WEAPONS)
    ammo_rows = data.t(_T_AMMO)
    links = data.t(_T_WEAPON_AMMO)

    out: list[WeaponGroupRow] = []
    for group_key, group in weapon_groups(data, unit_id, selected=selected).items():
        weapon = data.weapon(group[0])
        if weapon is None:
            continue
        # 每个弹种：TotalAmmoCount = Sum_{w 属于同组武器} WeaponAmmunitions[弹种]
        totals: dict[int, int] = {}
        refs: dict[int, list[CellRef]] = {}
        quantity_refs: dict[int, list[CellRef]] = {}
        for weapon_id in group:
            for ammo_id, quantity, _order in data.weapon_ammo(unit_id, weapon_id):
                totals[ammo_id] = totals.get(ammo_id, 0) + quantity
        for link in links.where2("UnitId", unit_id, "WeaponId", group[0]):
            row_ref = links.i(link, "Id")
            ammo_key = links.i(link, "AmmunitionId")
            # ❗ 两个列表分开放：换弹种写 AmmunitionId、改携弹量写 Quantity。
            #   早先只有一个 refs 且写死 Quantity —— 换弹种会把弹种 Id 写进携弹量（实测踩过）。
            refs.setdefault(ammo_key, []).append(CellRef(_T_WEAPON_AMMO, row_ref, "AmmunitionId"))
            quantity_refs.setdefault(ammo_key, []).append(
                CellRef(_T_WEAPON_AMMO, row_ref, "Quantity")
            )

        ammo_list: list[AmmoRow] = []
        for ammo_id in _ammo_order(data, unit_id, group[0]):
            ammo = data.ammo(ammo_id)
            if ammo is None or ammo_id not in totals:
                continue
            multiplier = ammo_rows.i(ammo, "HUDMultiplier")
            trajectory_key = TRAJECTORY_KEYS.get(ammo_rows.i(ammo, "TrajectoryType"), "")
            ammo_list.append(
                AmmoRow(
                    ammo_id=ammo_id,
                    label=data.display_name(
                        ammo_rows.s(ammo, "HUDName"), ammo_rows.s(ammo, "Name", f"#{ammo_id}")
                    ),
                    icon=ammo_rows.s(ammo, "HUDIcon"),
                    trajectory=data.loc.enum("trajectory", trajectory_key)
                    if trajectory_key
                    else "",
                    count=totals[ammo_id] * multiplier,
                    raw_total=totals[ammo_id],
                    multiplier=multiplier,
                    refs=tuple(refs.get(ammo_id, ())),
                    quantity_refs=tuple(quantity_refs.get(ammo_id, ())),
                    detail=ammo_detail_rows(data, unit_id, weapon, ammo),
                    targets=ammo_target_traits(data, ammo),
                    shared_with=data.sharing.of(_T_AMMO, ammo_id),
                )
            )

        # 简略模式那一行取**第一个弹种**（与分组、与射速用的是同一个"首个弹种"口径）。
        first_ammo = ammo_list[0] if ammo_list else None
        type_key = WEAPON_TYPE_KEYS.get(weapons.i(weapon, "Type"), "")
        weapon_id = weapons.i(weapon, "Id")
        out.append(
            WeaponGroupRow(
                group_key=group_key,
                weapon_id=weapon_id,
                label=data.display_name(
                    weapons.s(weapon, "HUDName"), weapons.s(weapon, "Name", f"#{weapon_id}")
                ),
                icon=weapons.s(weapon, "HUDIcon"),
                kind=data.loc.enum("weapontype", type_key) if type_key else "",
                # 行内的 xN：WeaponsCount（步兵 = 带这把枪的人数）
                count=len(group),
                weapons=group,
                ammo=tuple(ammo_list),
                refs=tuple(
                    CellRef(_T_WEAPON_AMMO, links.i(link, "Id"), "Quantity")
                    for link in links.where2("UnitId", unit_id, "WeaponId", group[0])
                ),
                shared_with=data.sharing.of(_T_WEAPONS, weapon_id),
                ammo_choices=weapon_ammo_choices(data, unit_id, weapon_id),
                detail=weapon_detail_rows(data, unit_id, weapon),
                compact=compact_cells(data, first_ammo) if first_ammo is not None else (),
                targets=weapon_targets(ammo_list),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# 自定义选项（含变体路径）
# --------------------------------------------------------------------------- #


def strip_slot_prefix(name: str, prefix: str) -> str:
    """把**槽名那一截**从选项名前面掐掉（设计图："Squad MarineRaiders Base" -> "Base"）。

    前缀必须落在**词边界**上：`Squad MarineRaidersX` 不算 `Squad MarineRaiders` 的后代
    （不然会掐出半个词的短名）。掐不动给空串，让调用方换下一条口径。

    ❗ 住在 unit_card 而不是 bench_slots：中栏（`bench_slots.slot_option_labels`）与
    卡片（`_option_label` 的回退链）都要用它 —— bench_slots 已经 import 本模块，
    反过来不行。口径只有这一份。
    """
    prefix = prefix.strip()
    if not prefix or not name.startswith(prefix):
        return ""
    rest = name[len(prefix) :]
    if rest and not rest[0].isspace():
        return ""
    return rest.strip()


def option_short_labels(data: CardData, modification_id: int) -> dict[int, str]:
    """槽内每一项的**短名**。两条口径按顺序试：

    1. **掐掉槽名那一截** —— 设计图上那一排就是 "Base" / "LongRange"，
       生成器写的是 `Options.Name.replace(Modifications.Name + " ", "")`；
    2. 掐不动就退回 `short_names`（去掉同槽**共有**的词）——
       F-15E 那种 "Plane F15E Pylon MK82 SE x1" 靠它。

    两条都没得掐（名字是空的）才退回 UIName -> 本地化文本 —— 短名是给人看的，
    别写成空的。

    ❗ 口径**只有这一份**：中栏 `bench_slots.slot_option_labels` 是它的转发，
    卡片的 `_option_label` 在「翻不出来」那条路上也用它。
    """
    rows = data.t(_T_OPTIONS)
    mods = data.t(_T_MODIFICATIONS)
    # ❗ 槽名在 **Modifications** 表上。第一版拿 modification_id 去 Options 表查 ->
    #    查出来的是"另一张表里 Id 恰好相同的那一行"，前缀掐不中，于是全靠 short_names
    #    兜着；一加进名字不同的新选项，short_names 就不共词了，短名当场退化成内部名。
    slot_name = mods.s(mods.by_id(modification_id), "Name", "")
    options = data.options_of(modification_id)
    labels = {rows.i(option, "Id"): rows.s(option, "Name", "") for option in options}
    common = short_names(labels)
    out: dict[int, str] = {}
    for option_id, raw in labels.items():
        stripped = raw.strip()
        name = strip_slot_prefix(stripped, slot_name) or (common.get(option_id) or "").strip()
        if not name:
            row = rows.by_id(option_id)
            name = data.display_name(rows.s(row, "UIName"), stripped or f"#{option_id}")
        out[option_id] = name
    return out


def _option_label(data: CardData, option_id: int) -> str:
    """一条选项给人看的短名 —— **只有这一处**。

    UIName 优先（那是本地化键，例 Custom_Option_M2_HMG -> "M2 重机枪"），
    没有就退回 Name（"MainTurret Stryker RV M2" 这种内部名），再没有写 #Id。
    卡上的选项行、以及"来自「X」这个选项"那一句，都用它。
    """
    rows = data.t(_T_OPTIONS)
    row = rows.by_id(option_id)
    if row is None:
        return f"Options #{option_id}"
    name = rows.s(row, "Name", f"#{option_id}")
    ui = rows.s(row, "UIName")
    if ui and data.loc.declared(ui) and not data.loc.text(ui):
        # 本地化表里声明过这个键、**值是 null**（= 游戏自己没填）——
        # 卡片那一行是给人看的，退到**短名**（口径同中栏），再退内部名。
        # ⛔ 绝不把键名（dlc_3_Custom_Option_OSV96 这种）当文案画出去。
        # 实测全库只有 Options #3039 一个格子走这条路（_work/w9c_scan_none.py）。
        short = option_short_labels(data, rows.i(row, "ModificationId")).get(option_id, "")
        return short or name
    return data.display_name(ui, name)


def _choices_of(
    data: CardData, modification_id: int, current_id: int
) -> tuple[OptionChoiceRow, ...]:
    rows = data.t(_T_OPTIONS)
    return tuple(
        OptionChoiceRow(
            option_id=rows.i(option, "Id"),
            label=_option_label(data, rows.i(option, "Id")),
            cost=rows.i(option, "Cost"),
            is_current=rows.i(option, "Id") == current_id,
            replaces_unit_id=rows.i(option, "ReplaceUnitId"),
            icon=rows.s(option, "OptionPicture"),
        )
        for option in data.options_of(modification_id)
    )


def _group_of(
    data: CardData,
    modification_id: int,
    owner_unit_id: int,
    current_id: int,
    *,
    from_variant: bool,
    unit_id: int,
) -> OptionGroupRow:
    """一组。`unit_id` = **正在看的那个单位**（判"这个槽是不是本单位的"）。

    `owner_unit_id` 与 `unit_id` 不是同一件事：本单位自己的槽两者相等；
    变体路径借来的槽（645 借 108 的 743）owner 是 108、unit_id 是 645。
    """
    mods = data.t(_T_MODIFICATIONS)
    modification = mods.by_id(modification_id)
    choices = _choices_of(data, modification_id, current_id)
    current = next((choice for choice in choices if choice.is_current), None)
    label = (
        data.display_name(mods.s(modification, "UIName"), mods.s(modification, "Name"))
        if modification is not None
        else ""
    )
    return OptionGroupRow(
        modification_id=modification_id,
        owner_unit_id=owner_unit_id,
        label=label or f"#{modification_id}",
        current_label=current.label if current is not None else "",
        current_cost=current.cost if current is not None else 0,
        current_icon=current.icon if current is not None else "",
        choices=choices,
        from_variant=from_variant,
        # 「这个槽属于本单位吗」按**那一行的 UnitId** 判，不按传入的 owner_unit_id ——
        # 后者在第一段里就是 unit_id（恒真），拿它判等于没判。
        is_own_slot=(modification is not None and mods.i(modification, "UnitId") == unit_id),
    )


def option_groups(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> tuple[OptionGroupRow, ...]:
    """底部「自定义选项」。

    两条来源（缺第二条，变体单位的卡片就是空的）：

    1. **本单位自己的 Modifications**（`Modifications.UnitId == 本单位`）：
       当前项 = `selected` 预览 ?? `default_option`（IsDefault ?? Order==0）；
    2. **指向本单位的替换选项**所在的那个槽（`Options.ReplaceUnitId == unit_id`）——
       且**只补本单位没有的槽**。实测 645 就是这样拿到 108 的 Modification 743 的
       （645 自己 0 条 Modifications）。

    ❗ **第二条只补槽，不抢「当前项」** —— 这一条踩过，别再改回去。

    单位自己的槽里出现一条「指向自己」的选项是**常态**：那些槽叫
    `Custom_Slot_Squad_loadout`，整槽就是"变成哪个班组型号"，其中一条指回本单位
    自己 = 基础型（`Name` 里带 Base）。实测 76 的槽 404 是 `#1482 .. Base -> 76`、
    `#1483 -> 339`；136 的 409、138 的 481 同型。从前这里拿「指向本单位的那个选项」
    去**覆盖**本单位槽的当前项，于是卡上的 ● 与写盘那条路（`exclusive_selection_writes`
    认的 `default_option`）分叉 —— 用户点 1483 确实写进去了（数据里 `IsDefault=true`），
    但下一帧又被覆盖回 1482，症状就是**点了不高亮**（10 个单位 × 每个槽每一项、
    30 次真鼠标点击里 12 次不跟）。

    游戏那边的判据只有一条（`GetDefaultOptions` VA 0x18070C0F0，见
    docs/研究-单位卡片公式.md §5.1）：

        mod.Options.FirstOrDefault(o => o.IsDefault)
            ?? mod.Options.FirstOrDefault(o => o.Order == 0)

    `ReplaceUnitId` **不参与**选当前项。所以本单位的槽一律按它算；只有**借来的槽**
    （本单位自己没有那一行，是"指向本单位的替换选项"把它带进来的）才拿"把本单位
    换出来的那一项"当当前项 —— 那时它是"你是从哪来的"，不是"你选了哪个"。
    """
    mods = data.t(_T_MODIFICATIONS)
    options = data.t(_T_OPTIONS)

    groups: list[OptionGroupRow] = []

    for modification in data.modifications_of(unit_id):
        modification_id = mods.i(modification, "Id")
        picked: Sequence[Any] | None = None
        if selected and modification_id in selected:
            candidate = data.option(selected[modification_id])
            if candidate is not None and options.i(candidate, "ModificationId") == modification_id:
                picked = candidate
        if picked is None:
            picked = data.default_option(modification_id)
        current_id = options.i(picked, "Id") if picked is not None else 0
        groups.append(
            _group_of(
                data, modification_id, unit_id, current_id, from_variant=False, unit_id=unit_id
            )
        )

    covered = {group.modification_id for group in groups}
    for option in data.variant_options(unit_id):
        modification_id = options.i(option, "ModificationId")
        if modification_id in covered:
            # 本单位的槽：当前项由 IsDefault ?? Order==0 说了算，这一条只是它的一项。
            continue
        modification = mods.by_id(modification_id)
        owner = mods.i(modification, "UnitId") if modification is not None else 0
        groups.append(
            _group_of(
                data,
                modification_id,
                owner,
                options.i(option, "Id"),
                from_variant=True,
                unit_id=unit_id,
            )
        )
    return tuple(groups)


# --------------------------------------------------------------------------- #
# 槽内互斥：选中一项 = 写 Options.IsDefault
# --------------------------------------------------------------------------- #

#: 「选中」写在 Options 的哪一列。
#: 判据是反编译出来的 GetDefaultOptions（VA 0x18070C0F0）：
#:     第一条 IsDefault ?? 第一条 Order == 0
#: 所以 `IsDefault` 是数据里**唯一**能表达"这个槽默认装哪一项"的列
#: （Order 是排列顺序，不是选中；同一槽里 Order 还可以重复）。
SELECTION_COLUMN = "IsDefault"


def slot_selected_option(data: CardData, modification_id: int) -> int:
    """这个槽**现在选中**的那一项（IsDefault ?? Order==0 那一条）；没有就是 0。

    与 `CardData.default_option` 是同一口径，这里只把它收成一个 Id ——
    界面要拿它画"选中态"，而画的地方不该自己再走一遍那个 ?? 逻辑。
    """
    picked = data.default_option(modification_id)
    if picked is None:
        return 0
    return data.t(_T_OPTIONS).i(picked, "Id")


def exclusive_selection_writes(
    data: CardData, modification_id: int, option_id: int
) -> tuple[tuple[int, str, bool], ...]:
    """「这个槽选中 option_id」= 要写哪几格；返回 ((Options 行 Id, 列名, 新值), ...)。

    **槽内互斥**：同一个 ModificationId 下最多一条 `IsDefault = true`。
    这一项写 True，同槽里其它还是 True 的写 False —— 于是"选了 A 之后 B 不再选中"
    是**数据里的事实**，不是界面记的一个临时状态。

    已经是这个样子就返回空元组：点同一个（或点回当前项）不算改动，
    撤销栈里不会多一笔，写盘也不会因为一次空点就多写一遍。

    纯函数、零 Qt：谁写由界面决定（编辑器走 EditorContext.set_value，
    军械库那张只读卡不接写盘通道，只预览）。
    """
    rows = data.t(_T_OPTIONS)
    picked = data.option(option_id)
    if picked is None or rows.i(picked, "ModificationId") != modification_id:
        # 选项不属于这个槽 —— 一个字都不许写（宁可什么都不做，也不要串槽）。
        return ()
    writes: list[tuple[int, str, bool]] = []
    for option in data.options_of(modification_id):
        row_id = rows.i(option, "Id")
        selected = row_id == option_id
        if rows.b(option, SELECTION_COLUMN) == selected:
            continue
        writes.append((row_id, SELECTION_COLUMN, selected))
    # 选中的那一格排最前：状态栏与（将来的）撤销顺序都按它走。
    writes.sort(key=lambda item: item[0] != option_id)
    return tuple(writes)


# --------------------------------------------------------------------------- #
# 21 个炮塔挂载槽 / 炮塔四层链 / 改装槽（设计稿块 4、5、8、10）
# --------------------------------------------------------------------------- #


def slot_range_text(slots: Sequence[int]) -> str:
    """槽号压缩成一段字：「0」/「1–2」/「7–12 · 19」（升序、去重、连续段折成 a–b）。"""
    ordered = sorted({int(slot) for slot in slots})
    if not ordered:
        return ""
    parts: list[str] = []
    start = previous = ordered[0]
    for slot in ordered[1:]:
        if slot == previous + 1:
            previous = slot
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = slot
    parts.append(str(start) if start == previous else f"{start}–{previous}")
    return " · ".join(parts)


def union_ids(values: Sequence[int]) -> tuple[int, ...]:
    """取非零值、去重、**保序**（关系树与卡上那条链共用一份）。"""
    out: list[int] = []
    for value in values:
        if value and value not in out:
            out.append(int(value))
    return tuple(out)


def short_names(labels: Mapping[int, str]) -> dict[int, str]:
    """炮塔短名：去掉**所有名字共有**的词（多半是单位名），再砍掉开头那个 xxxTurret。

    实测两族都靠这两刀：

    - MainTurret StrykerRV M240 -> M240（共有的 MainTurret / StrykerRV 一起去掉）；
    - MainTurret M1A2SEP2 M256 120mm 与 CupolaTurret M1A2SEP2 M2 ->
      M256 120mm / M2（共有的只有单位名，再各自砍掉开头的 xxxTurret）。

    ⚠️ 只在**一组**名字上成立：名字只剩一座炮塔时"共有词"就是它自己，全砍光，
    于是退回原名。所以算短名要给**候选名单**（见 candidate_turret_ids），
    别只拿当前装着的那几座算。
    """
    names = [name for name in labels.values() if name]
    common: set[str] = set(names[0].split()) if names else set()
    for name in names[1:]:
        common &= set(name.split())
    out: dict[int, str] = {}
    for row_id, name in labels.items():
        words = [word for word in name.split() if word not in common] if name else []
        if words and words[0].endswith("Turret"):
            words = words[1:]
        out[row_id] = " ".join(words) or name
    return out


def turret_label(name: str) -> str:
    """炮塔短名 + 一个「塔」（设计图：M240 塔）；本来就是短的就不再加。"""
    short = name.strip()
    if not short:
        return "炮塔"
    return short if short.endswith("塔") else f"{short} 塔"


def short_turret_name(name: str) -> str:
    """砍掉开头那个 xxxTurret 词 —— 21 个槽那一排格子上用的短名。

    为什么不复用 short_names：那一条要**一组**名字才成立，而一条选项常常只挂一座
    炮塔（实测 F-15E 的 option 897：12 个槽全是 Turret Empty），全砍光就没名字了。
    这里只砍一个前缀 token：PylonTurret MK82 SE x1 -> MK82 SE x1。
    """
    head, _, tail = name.partition(" ")
    if tail and head.endswith("Turret"):
        return tail.strip() or name
    return name


def option_turret_slots(data: CardData, option_id: int) -> tuple[tuple[int, int], ...]:
    """一条改装选项占了哪几个炮塔挂载槽：((槽号, TurretId), ...)，按槽号升序。

    - **21 个槽全扫**（Turret0Id…Turret20Id，列名只有一份：TURRET_SLOTS），
      一个都不折叠 —— 用户明确要求；
    - 只回**非零**的：0 = 这一条选项没占这个槽；
    - Turret20Id 在原版里恒为 0（core/rules/rulebook.json 的 zero.Options.Turret20Id：
      「不要用它当第 21 个炮塔槽」），但它仍然是一个槽位 —— 扫到就用、不特判，
      游戏哪天启用它我们不用改代码。

    ⚠️ **语义口径**：这 21 个槽在游戏里是「**池 + 选项子集**」，**不是**
    「把第 N 个槽换成这一座」（docs/关系-24表与修改入口.md:108 与
    docs/交接-单位编辑器实现.md:443 都把这条写死了）。所以调用方画出来的文案
    只说"槽 N · <炮塔名>"，不许写成"替换第 N 个炮塔"。

    ❗ 唯一出处在这里（unit_card）；relation_tree 转发这个名字给老调用方。
    """
    table = data.t("Options")
    row = table.by_id(option_id)
    if row is None:
        return ()
    out: list[tuple[int, int]] = []
    for slot, column in enumerate(TURRET_SLOTS):
        turret_id = table.i(row, column)
        if turret_id > 0:
            out.append((slot, turret_id))
    return tuple(out)


def turret_slot_cells(data: CardData, option_id: int) -> tuple[TurretSlotCell, ...]:
    """一条选项的**21 个**炮塔挂载槽（0..20 全在，一个都不折叠）。

    ❗ 空槽也是一格：turret_id == 0 说明这条选项没占这个槽 —— 用户要看到
    "哪几个槽是空的"，只画非零的那几个正好把这条信息删掉（设计稿块 10）。
    """
    table = data.t("Options")
    row = table.by_id(option_id)
    names: dict[int, str] = {}
    out: list[TurretSlotCell] = []
    for slot, column in enumerate(TURRET_SLOTS):
        turret_id = table.i(row, column) if row is not None else 0
        if turret_id <= 0:
            out.append(TurretSlotCell(slot=slot, turret_id=0))
            continue
        label = names.get(turret_id)
        if label is None:
            label = short_turret_name(data.name_of("Turrets", turret_id))
            names[turret_id] = label
        out.append(TurretSlotCell(slot=slot, turret_id=turret_id, label=label))
    return tuple(out)


def candidate_turret_ids(
    data: CardData, unit_id: int, groups: Sequence[OptionGroupRow] | None = None
) -> tuple[int, ...]:
    """本单位**可能**装的炮塔：TurretUnits（候选名单）∪ 改装选项的 21 个槽。

    与 card_turrets 的区别：这个**不看当前选中哪一项**。它有两个用途：
    ① 短名要在一组名字上算（见 short_names）；② 关系树的「🗼 炮塔」那一组画的是它。
    """
    links = data.t(_T_TURRET_UNITS)
    ordered = sorted(
        links.where("UnitId", unit_id),
        key=lambda row: (links.i(row, "Order"), links.i(row, "Id")),
    )
    ids = [links.i(row, "TurretId") for row in ordered]
    options = data.t(_T_OPTIONS)
    for group in groups if groups is not None else option_groups(data, unit_id):
        for option in data.options_of(group.modification_id):
            ids.extend(options.i(option, column) for column in TURRET_SLOTS)
    return union_ids(ids)


#: 选项**改了哪一行**的覆盖列 -> 卡上怎么写（列清单：Options 那 46 列，
#: 实测非零的列一个不漏 —— 漏一个就是"这条选项改了什么"说不全）。
_OPTION_LINK_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("ArmorId", "换成这套装甲", "Armors"),
    ("MobilityId", "换成这套机动", "Mobility"),
    ("MainSensorId", "换成主传感器", "Sensors"),
    ("ExtraSensorId", "加一副传感器", "Sensors"),
    ("Ability1Id", "能力 1", "Abilities"),
    ("Ability2Id", "能力 2", "Abilities"),
    ("Ability3Id", "能力 3", "Abilities"),
    ("ReplaceUnitId", "换成另一个单位", "Units"),
)

#: 改了**一串字**的覆盖列 -> (列名, 卡上怎么写, 值前面加什么)。
_OPTION_TEXT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("ConcatenateWithUnitName", "名字后缀", "+"),
    ("ThumbnailOverride", "换缩略图", ""),
    ("PortraitOverride", "换立绘", ""),
    ("AudioPresetOverride", "换语音", ""),
)


def option_effects(data: CardData, option_id: int) -> tuple[OptionEffect, ...]:
    """一条改装选项**改了什么**（卡上那行灰字；一条 = 一项改动）。

    三条口径：

    1. **21 个炮塔挂载槽按炮塔归并**：同一座炮塔常占好几个槽（实测 F-15E 的
       option 897 占 12 个槽、全是同一座 Turret Empty），一条写清是哪些槽；
    2. 其余覆盖列逐条列出来（装甲 / 机动 / 主副传感器 / 能力 1-3 / 换成另一个单位 /
       名字后缀 / 隐蔽 / 计时修正 / 缩略图与立绘 / 语音 / 动态炮塔）；
    3. Cost **不在**里面 —— 费用单独一格画（cost_text），别写两遍。
    """
    rows = data.t(_T_OPTIONS)
    row = rows.by_id(option_id)
    if row is None:
        return ()
    out: list[OptionEffect] = []
    by_turret: dict[int, list[int]] = {}
    for slot, column in enumerate(TURRET_SLOTS):
        turret_id = rows.i(row, column)
        if turret_id > 0:
            by_turret.setdefault(turret_id, []).append(slot)
    for turret_id, slots in by_turret.items():
        out.append(
            OptionEffect(
                column=TURRET_SLOTS[slots[0]],
                label="炮塔挂载槽",
                text=(
                    f"槽 {slot_range_text(slots)} · "
                    f"{short_turret_name(data.name_of('Turrets', turret_id))}"
                ),
                table="Turrets",
                row_id=turret_id,
            )
        )
    for column, label, table in _OPTION_LINK_COLUMNS:
        row_id = rows.i(row, column)
        if row_id > 0:
            out.append(OptionEffect(column, label, data.name_of(table, row_id), table, row_id))
    stealth = rows.f(row, "StealthOverride")
    if stealth:
        out.append(OptionEffect("StealthOverride", "隐蔽覆盖", f"隐蔽 {fmt_number(stealth)}"))
    for column, label in (
        ("TimeModifierRefund", "退款计时"),
        ("TimeModifierResurrect", "复活计时"),
    ):
        value = rows.f(row, column)
        if value:
            out.append(OptionEffect(column, label, f"{label} {fmt_number(value)}"))
    if rows.b(row, "IsDynamicTurretOption"):
        out.append(OptionEffect("IsDynamicTurretOption", "动态炮塔", "动态炮塔"))
    for column, label, prefix in _OPTION_TEXT_COLUMNS:
        text = rows.s(row, column)
        if text:
            out.append(OptionEffect(column, label, f"{label} {prefix}{text}".strip()))
    return tuple(out)


def card_option_groups(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> tuple[CardOptionGroup, ...]:
    """卡上的改装槽：槽 › 选项（互斥，选中的那一项带 ●）+ 选中项的 21 个挂载槽。

    搭在 option_groups 上 —— **变体借来的槽**（645 借 108 的 743）那条路只有一份实现，
    这里只往上加两样：每项"改了什么"，以及选中那一项的 21 个槽。
    """
    out: list[CardOptionGroup] = []
    for group in option_groups(data, unit_id, selected=selected):
        options = tuple(
            CardOptionRow(
                option_id=choice.option_id,
                label=choice.label,
                cost=choice.cost,
                selected=choice.is_current,
                effects=option_effects(data, choice.option_id),
                replaces_unit_id=choice.replaces_unit_id,
            )
            for choice in group.choices
        )
        picked = group.selected_option_id
        out.append(
            CardOptionGroup(
                modification_id=group.modification_id,
                label=group.label,
                options=options,
                selected_label=group.current_label,
                selected_cost=group.current_cost,
                is_own_slot=group.is_own_slot,
                owner_unit_id=group.owner_unit_id,
                slots=turret_slot_cells(data, picked) if picked else (),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

_CATEGORY_LABELS = {
    0: "侦察",
    1: "步兵",
    2: "载具",
    3: "支援",
    4: "未分类",
    5: "直升机",
    6: "战机",
}
_KIND_LABELS = {
    TYPE_INFANTRY: "步兵",
    TYPE_VEHICLE: "载具",
    TYPE_HELICOPTER: "直升机",
    TYPE_AIRCRAFT: "战机",
    TYPE_SHIP: "舰船",
}


def row_name(data: CardData, table: str, row_id: int) -> str:
    """一行叫什么 —— 走 core/edit/foreign.py 的显示名（Name / HUDName，跳过本地化键）。

    ❗ 别用 CardData.display_name：那个是**本地化文本**的解析器（key -> 文本），
    传表名进去只会原样返回表名（实测踩过：机动那一块显示成 "Mobility"）。
    """
    name = foreign_name(data.instance, table, row_id) if row_id else ""
    return name or f"{table} #{row_id}"


def card_armor(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> LinkedRow | None:
    """装甲那一行（Armors）—— 顶/背/正/侧 + 生命值都在它里面。

    link 是"本单位哪一格指着它"：缺省是 UnitArmors#N.ArmorId；
    被改装选项覆盖时是 Options#M.ArmorId（换 / 克隆要改的就是这一格）。
    """
    row = data.armor(unit_id, selected)
    if row is None:
        return None
    row_id = data.t(_T_ARMORS).i(row, "Id")
    link: CellRef | None = None
    source = ""
    override = data.option_override(unit_id, selected, "ArmorId", _T_ARMORS)
    if override is not None:
        link = CellRef(_T_OPTIONS, override[0], "ArmorId")
        # 块 5：被选项覆盖的那几块要注明"来自哪个选项"。
        source = f"来自「{_option_label(data, override[0])}」这个选项"
    else:
        link_row = data.armor_link(unit_id)
        if link_row is not None:
            link = CellRef(_T_UNIT_ARMORS, data.t(_T_UNIT_ARMORS).i(link_row, "Id"), "ArmorId")
    return LinkedRow(
        table=_T_ARMORS,
        row_id=row_id,
        label=row_name(data, _T_ARMORS, row_id),
        link=link,
        source=source,
    )


def card_mobility(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> LinkedRow | None:
    """机动那一行（Mobility）—— 选项的 MobilityId 覆盖在这里生效。"""
    row = data.mobility(unit_id, selected)
    if row is None:
        return None
    rows = data.t(_T_MOBILITY)
    row_id = rows.i(row, "Id")
    link: CellRef | None = None
    source = ""
    override = data.option_override(unit_id, selected, "MobilityId", _T_MOBILITY)
    if override is not None:
        link = CellRef(_T_OPTIONS, override[0], "MobilityId")
        source = f"来自「{_option_label(data, override[0])}」这个选项"
    else:
        link_row = data.mobility_link(unit_id)
        if link_row is not None:
            link = CellRef(
                _T_UNIT_PROPULSIONS,
                data.t(_T_UNIT_PROPULSIONS).i(link_row, "Id"),
                "MobilityId",
            )
    return LinkedRow(
        table=_T_MOBILITY,
        row_id=row_id,
        label=row_name(data, _T_MOBILITY, row_id),
        link=link,
        source=source,
    )


def card_sensors(
    data: CardData, unit_id: int, selected: Mapping[int, int] | None = None
) -> tuple[LinkedRow, ...]:
    """传感器那几行（Sensors）—— 选项的 Main/ExtraSensorId 覆盖在这里生效。

    每一行都带着 link（本单位哪一格指着它）：缺省是 SensorUnits#N.SensorId，
    被选项覆盖时是 Options#M.MainSensorId / ExtraSensorId。
    """
    links = data.t(_T_SENSOR_UNITS)
    out: list[LinkedRow] = []
    for link_row in sorted(links.where("UnitId", unit_id), key=lambda r: links.i(r, "Id")):
        sensor_id = links.i(link_row, "SensorId")
        if sensor_id <= 0:
            continue
        out.append(
            LinkedRow(
                table=_T_SENSORS,
                row_id=sensor_id,
                label=row_name(data, _T_SENSORS, sensor_id),
                link=CellRef(_T_SENSOR_UNITS, links.i(link_row, "Id"), "SensorId"),
            )
        )

    options = data.t(_T_OPTIONS)
    chosen = data.selected_options(unit_id, selected)
    overrides = (
        ("MainSensorId", "主传感器（选项）"),
        ("ExtraSensorId", "副传感器（选项）"),
    )
    for column, note in overrides:
        option = next((o for o in chosen if options.i(o, column) > 0), None)
        if option is None:
            continue
        sensor_id = options.i(option, column)
        if column == "MainSensorId":
            # 主传感器是**赋值**（覆盖掉上面那几行），副传感器是追加 —— 与
            # effective_sensors() 的语义逐字一致。
            out = []
        if any(item.row_id == sensor_id for item in out):
            continue
        out.append(
            LinkedRow(
                table=_T_SENSORS,
                row_id=sensor_id,
                label=row_name(data, _T_SENSORS, sensor_id),
                note=note,
                link=CellRef(_T_OPTIONS, options.i(option, "Id"), column),
            )
        )
    return tuple(out)


def card_transports(data: CardData, unit_id: int) -> tuple[LinkedRow, ...]:
    """运输配对（TransportAvailabilities 里 UnitId == 这个单位的那几行）。

    ❗ **这张表只有 3 列**：Id / SpecializationAvailabilityId / UnitId（实测 52085，
    见 docs/数据-表与列清单.md §TransportAvailabilities）。
    上一轮我按"运输方"这个想当然的名字猜了一个不存在的 TransportUnitId 列，
    rows.i() 对不存在的列返回 0 —— 于是两行都显示成 "Units #0"
    （用户报的"这个运输的逻辑是有问题的"）。**列名先去列清单里查，别猜。**
    """
    if not data.has_table(_T_TRANSPORT_AVAIL):
        return ()
    rows = data.t(_T_TRANSPORT_AVAIL)
    out: list[LinkedRow] = []
    if "SpecializationAvailabilityId" not in rows.table.columns:
        return ()
    for row in sorted(rows.where("UnitId", unit_id), key=lambda item: rows.i(item, "Id")):
        row_id = rows.i(row, "Id")
        availability = rows.i(row, "SpecializationAvailabilityId")
        out.append(
            LinkedRow(
                table=_T_TRANSPORT_AVAIL,
                row_id=row_id,
                label=f"专精可用性 #{availability}" if availability else "（空）",
                note="运输配对",
                # 「换」= 换一个专精可用性（改这一格）。
                # 不能克隆 —— 这是"配对行"，不是可以私有化的模板行。
                link=CellRef(_T_TRANSPORT_AVAIL, row_id, "SpecializationAvailabilityId"),
            )
        )
    return tuple(out)


# -- 四层链：单位 › 炮塔 › 武器 › 弹种（设计稿块 4/8） -------------------- #


def _chain_ammo(data: CardData, unit_id: int, weapon_id: int) -> tuple[ChainAmmo, ...]:
    """这门武器在本单位挂着的每一种弹（WeaponAmmunitions 行，带回行 Id）。"""
    carrying = data.t(_T_WEAPON_AMMO)
    ammo_rows = data.t(_T_AMMO)
    out: list[ChainAmmo] = []
    for row in data.weapon_ammo_rows(unit_id, weapon_id):
        ammo_id = carrying.i(row, "AmmunitionId")
        row_id = carrying.i(row, "Id")
        quantity = carrying.i(row, "Quantity")
        template = ammo_rows.by_id(ammo_id)
        multiplier = ammo_rows.i(template, "HUDMultiplier") if template is not None else 1
        out.append(
            ChainAmmo(
                row_id=row_id,
                ammo_id=ammo_id,
                label=data.name_of(_T_AMMO, ammo_id),
                quantity=quantity,
                count=quantity * max(1, multiplier),
                link=CellRef(_T_WEAPON_AMMO, row_id, "AmmunitionId"),
            )
        )
    return tuple(out)


def _chain_weapons(data: CardData, unit_id: int, turret_id: int) -> tuple[ChainWeapon, ...]:
    """这座炮塔（**含子炮塔**）挂着的武器 —— 与卡片武器那一排同一个走法。"""
    out: list[ChainWeapon] = []
    for weapon_id in union_ids(_walk_turret_weapons(data, turret_id)):
        out.append(
            ChainWeapon(
                weapon_id=weapon_id,
                label=data.name_of(_T_WEAPONS, weapon_id),
                ammo=_chain_ammo(data, unit_id, weapon_id),
            )
        )
    return tuple(out)


def _chain_from_ids(
    data: CardData, unit_id: int, ids: Sequence[int], placed: Sequence[int]
) -> tuple[ChainTurret, ...]:
    """把一串 turret_id 装成四层链 —— `card_turret_chain` 与 `roster_turret_chain` **共用这一份**。

    `placed` 只用来算**槽号**（`slots`）：不在 `placed` 里的炮塔槽号是空的
    （编成台的名册里那些"本单位有、当前改装没选"的炮塔就是这样）。
    短名按**候选名单**算（candidate_turret_ids）：只拿当前装着的那几座去截公共前缀，
    常常只剩一座、截不出东西来（实测 Stryker RV 只有 M240 塔一座）。
    """
    ids = tuple(ids)
    names = {turret_id: data.name_of(_T_TURRETS, turret_id) for turret_id in ids}
    candidates = candidate_turret_ids(data, unit_id)
    shorts = short_names({tid: data.name_of(_T_TURRETS, tid) for tid in candidates})
    out: list[ChainTurret] = []
    for turret_id in ids:
        full = names[turret_id]
        out.append(
            ChainTurret(
                turret_id=turret_id,
                label=turret_label(shorts.get(turret_id, full)),
                full_label=full,
                slots=tuple(slot for slot, value in enumerate(placed) if value == turret_id),
                weapons=_chain_weapons(data, unit_id, turret_id),
            )
        )
    return tuple(out)


def card_turret_chain(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> tuple[ChainTurret, ...]:
    """「单位 › 炮塔 › 武器 › 弹种」四层链里**当前装着**的那几座炮塔（卡上那一面）。

    - **炮塔** = card_turrets（TurretUnits 的缺省 + 当前选项的 21 个槽覆盖）
      **去重保序**：同一座炮塔占好几个槽时只画一行，槽号写在它行尾；
    - **武器** = 这座炮塔（含子炮塔）挂着的武器，按 TurretWeapons 的 (Order, Id)；
    - **弹种** = 本单位为这门武器挂的携弹行（WeaponAmmunitions），带回**行 Id**；
    - 步兵没有炮塔（card_turrets 那条路是**负数编码的假炮塔**）—— 返回空元组。
    """
    placed = data.card_turrets(unit_id, selected)
    if not placed or placed[0] < 0:
        return ()
    return _chain_from_ids(data, unit_id, union_ids(placed), placed)


def roster_turret_chain(
    data: CardData, unit_id: int, *, selected: Mapping[int, int] | None = None
) -> tuple[ChainTurret, ...]:
    """**本单位可能装的全部炮塔**（不管 `Turrets.IsDefault`、也不管当前选中哪一项）。

    ❗ 与 `card_turret_chain` 的差别**只在"取了哪些 turret_id"**：

    - 卡 -> `card_turrets`（`IsDefault` 过滤 + 当前选项的槽覆盖）=「**现在装的是什么**」。
      那是游戏 `ApplyCurrentMods` 之后的样子，卡上必须照它画，一个字节都不该改；
    - 这里 -> `candidate_turret_ids`（TurretUnits 候选名单 ∪ 选项的 21 个槽）
      =「**这个单位能有哪几座**」—— 用户 2026-10-02 原话：
      「还是把所有的炮塔都列出来吧，我毕竟要增删炮塔的」。
      实测单位 21：名册 **5 座**（M2 / Mk19 / M240 / Bushmaster2 / Bushmaster2 Jav），
      而卡上那条链只剩 **1 座** —— 另外 4 座在界面上根本够不着，也就谈不上增删。

    槽号仍按**当前选中**算：没被选项选中的炮塔 `slots` 是空的（界面上就没有槽号那一小段）。
    """
    placed = data.card_turrets(unit_id, selected)
    if placed and placed[0] < 0:
        return ()
    return _chain_from_ids(data, unit_id, candidate_turret_ids(data, unit_id), placed or ())


def build_unit_card(
    source: Instance | CardData,
    unit_id: int,
    *,
    selected: Mapping[int, int] | None = None,
    localization: Localization | None = None,
) -> UnitCard | None:
    """装配一张单位卡片。单位不存在时返回 None。

    参数
    ----
    source
        Instance（会现建索引）或已经建好的 CardData（切单位时复用）。
    selected
        {modification_id: option_id}，模拟玩家已经选好的改型；不给就取缺省。
    """
    data = source if isinstance(source, CardData) else CardData(source, localization)
    unit = data.unit(unit_id)
    if unit is None:
        return None

    units = data.t(_T_UNITS)
    cost = card_cost(data, unit_id, selected)
    category = data.category(unit)
    category_label = _CATEGORY_LABELS.get(category, f"类型 {category}")
    subtitle_parts = [f"Id {unit_id}", category_label]
    kind = data.unit_type(unit)
    kind_label = _KIND_LABELS.get(kind, "")
    if kind_label and kind_label != category_label:
        subtitle_parts.append(kind_label)

    return UnitCard(
        unit_id=unit_id,
        # 卡片抬头用 **HUDName**：实测 645 的 Name='VDV Dsh 2' 而 HUDName='VDV Shturm'，
        # 游戏卡片上写的是后者（截图核对过）。**再套选项的名字覆盖**（见 unit_name）——
        # 实机 BTR-82 那张卡写的是 BTR-82A、T-72B 写的是 T-72B1、PT-76 写的是 PT-76B。
        title=unit_name(data, unit_id, selected),
        subtitle=" · ".join(subtitle_parts),
        cost=int(cost),
        # ❗「这一格落在哪」要说真话：base_cost 是 OriginalCost **非 0 时**才用它，
        #   否则就是 Cost。实测 52085 全 540 行 OriginalCost 都是 0（占位列），
        #   所以卡片上那个金色数字实际来自 Units.Cost —— 指到 OriginalCost
        #   会让编辑器"改了这一格，卡片纹丝不动"（这个坑本轮由单位编辑器发现）。
        cost_ref=CellRef(
            _T_UNITS,
            unit_id,
            "OriginalCost" if units.i(unit, "OriginalCost") else "Cost",
        ),
        # 缩略图与立绘也走**选项覆盖**那条口径（与 unit_name 同一套纪律）：
        # 缺省就要走，覆盖为空才回落 Units 那两列。见 unit_thumbnail_key / unit_portrait_key。
        portrait_key=unit_portrait_key(data, unit_id, selected),
        icon_key=unit_thumbnail_key(data, unit_id, selected),
        model_key=units.s(unit, "ModelFileName"),
        category=category,
        kind=kind,
        stats=build_stats(data, unit_id, selected=selected, cost=cost),
        # 装甲图（地面载具才有）：与 stats 同一套 selected，换「装甲组件」跟着重算。
        armor_zones=armor_zones(data, unit_id, selected),
        abilities=build_abilities(data, unit_id, selected=selected),
        # 卡上补出来的几块（用户："补到卡上，每样一块"）：装甲 / 机动 / 传感器 / 运输。
        armor=card_armor(data, unit_id, selected),
        mobility=card_mobility(data, unit_id, selected),
        sensors=card_sensors(data, unit_id, selected),
        transports=card_transports(data, unit_id),
        weapons=build_weapons(data, unit_id, selected=selected),
        options=option_groups(data, unit_id, selected=selected),
        # 块 4/8：单位 › 炮塔 › 武器 › 弹种（四层都能点）。
        turrets=card_turret_chain(data, unit_id, selected=selected),
        # 块 5/10：改装槽 › 互斥选项 + 选中那一项的 21 个炮塔挂载槽。
        card_options=card_option_groups(data, unit_id, selected=selected),
        loadout=squad_loadout(data, unit_id),
        degraded=data.loc.is_degraded,
    )


__all__ = [
    "ARMOR_HEAT_KEY",
    "ARMOR_KINETIC_KEY",
    "ARMOR_ZONE_SPECS",
    "EFFECTIVE_RANGE_MULTIPLIER",
    "INFANTRY_WEIGHT_PER_MEMBER",
    "MAX_STATS_PER_LINE",
    "PENDING_DETAIL_ROWS",
    "STAT_SPECS",
    "STAT_SPACING_TIGHT",
    "STAT_SPACING_WIDE",
    "TRAJECTORY_KEYS",
    "WEAPON_TYPE_KEYS",
    "DPS_WEAPON_TYPES",
    "EVIDENCE_ASSUMED",
    "EVIDENCE_CONFIRMED",
    "AbilityChip",
    "AmmoChoiceRow",
    "AmmoRow",
    "ArmorZone",
    "DetailRow",
    "LinkedRow",
    "CardData",
    "card_armor",
    "card_mobility",
    "card_sensors",
    "card_transports",
    "row_name",
    "CardStat",
    "CellRef",
    "OptionChoiceRow",
    "OptionGroupRow",
    "Rows",
    "SquadLoadout",
    "SquadMemberRow",
    "StatSpec",
    "UnitCard",
    "WeaponChoiceRow",
    "WeaponGroupRow",
    "ammo_detail_rows",
    "ammo_resupply",
    "ammo_resupply_multiplier",
    "armor_zones",
    "bankers_round",
    "base_cost",
    "build_abilities",
    "build_stats",
    "build_unit_card",
    "build_weapons",
    "card_cost",
    "card_weight",
    "effective_sensors",
    "fmt_float32",
    "fmt_number",
    "fmt_time",
    "get_infantry_slots",
    "SELECTION_COLUMN",
    "exclusive_selection_writes",
    "option_groups",
    "slot_selected_option",
    "unit_name",
    "unit_portrait_key",
    "unit_thumbnail_key",
    "new_member_values",
    "optics_triple",
    "purchase_delay",
    "squad_loadout",
    "weapon_choice_rows",
    "rotation_radius",
    "round5",
    "round_to_multiple",
    "weapon_ammo_choices",
    "weapon_detail_rows",
    "weapon_groups",
    # -- 块 4/5/8/10：四层链 · 21 个挂载槽 · 改装槽的互斥选项 ------------- #
    "CardOptionGroup",
    "CardOptionRow",
    "ChainAmmo",
    "ChainTurret",
    "ChainWeapon",
    "OptionEffect",
    "TurretSlotCell",
    "candidate_turret_ids",
    "card_option_groups",
    "card_turret_chain",
    "option_effects",
    "option_turret_slots",
    "roster_turret_chain",
    "short_names",
    "short_turret_name",
    "slot_range_text",
    "turret_label",
    "turret_slot_cells",
    "union_ids",
]
