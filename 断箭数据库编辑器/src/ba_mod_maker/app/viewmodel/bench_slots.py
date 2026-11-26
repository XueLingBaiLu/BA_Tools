"""编成台的属性栏（装甲 / 机动 / 能力 / 传感器 / 专精可用性）+ 改装槽的取数与规则。**零 Qt。**

2026-10-02 拆块：用户口径「专精可用性单独放一栏，装甲、机动、能力、传感器，都应该单独一栏」
—— 原来看上去是"③ 一整块（三类挤在一起）"，现在**一类一栏**；各栏的抬头编号由
bench_page 按**摆第几块**生成（本模块与各块的常量里一个字都不写编号）。

口径出处：

- `docs/交接-2026-10-01-单位编成台设计.md` §3 口径 1（**只有 ＋ 和 ✕，没有"换"**）、
  口径 7（删的是**关联行**，模板行不动）；
- 设计图 `docs/设计稿-编成台-步兵.png` 的 ③ ④ 两块；
- Lead 2026-10-01 定稿的**两处有意偏离**（会写进交接）：
  ① ③ 主体 = **一行 = 关联表里的一行**（不是一行一个类别 —— F-15E 的 UnitPropulsions
     有 2 行、UnitAbilities 有 3 行，按类别折叠会丢行）；
  ② ③ 抬头的 ＋ 按表拆成**三颗**（「＋ 装甲 / ＋ 机动 / ＋ 能力」）—— 设计图上写的是
     一颗「＋ 加一行」，但底下是 3 张表、一颗按钮说不清落到哪张。

四条钉死的规则（都有用例）：

1. ③ 读 `UnitId = 本单位` 的那些**关联行**（UnitArmors / UnitPropulsions /
   UnitAbilities），✕ 删的就是它；Armors / Mobility / Abilities 那些**模板行一个字节不动**；
2. ④ 一个槽 = `Modifications` 一行，槽内互斥 = 点一项写 `Options.IsDefault`
   （判据是反编译的 GetDefaultOptions：`第一条 IsDefault ?? 第一条 Order == 0`，
   见 `unit_card.SELECTION_COLUMN`）；
3. **借来的槽一个字节都不许写**（`CardOptionGroup.is_own_slot == False`）——
   那是变体单位借槽主人的槽（实测 645 借 108 的 743），翻 IsDefault 会一起改到槽的主人；
4. 新行 Id 一律 `core/edit/clone.py::next_row_id`（**不写"行数 + 1"**：Id 有大空洞）。

还有一条**只给折叠态用**的口径（2026-10-01，飞机版相对载具版唯一的局部改动）：
飞机（F-15E）5 个槽共 31 个选项，全列铺不下，所以界面可以"一行一个槽、只显示当前选中项"。
**取数这一层要交出"当前选中那一项"这件事**（`SlotGroupView.selected_option`），
判据仍然只有一份 —— `unit_card.card_option_groups` 算出来的 `option.selected`
（= 反编译的 `第一条 IsDefault ?? 第一条 Order == 0`），界面不许自己比 Id。

❗ 本模块不得 import 任何 Qt 符号（tests/test_core_layering.py 用 AST 扫）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ba_mod_maker.app.viewmodel.unit_card import (
    SELECTION_COLUMN,
    CardData,
    CardOptionGroup,
    CardOptionRow,
    OptionEffect,
    card_option_groups,
    exclusive_selection_writes,
    option_short_labels,
    row_name,
    short_names,
)
from ba_mod_maker.core.edit.changeset import ID_COLUMN, Changeset
from ba_mod_maker.core.edit.clone import next_row_id
from ba_mod_maker.core.edit.refs import CellRef

# --------------------------------------------------------------------------- #
# 属性栏：一类 = 一张关联表 -> 一张模板表
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StatKind:
    """属性栏里的一类：**关联表** -> **它指向的模板表**，以及界面上那几个字。

    action 是抬头那颗按钮上的字（Lead 定稿：一颗按钮一张表，不改）。
    """

    #: 关联表（行 = "本单位挂着这一样"，✕ 删的就是它）
    table: str
    #: 指向模板行的外键列（ArmorId / MobilityId / AbilityId / SensorId / SpecializationId）
    column: str
    #: 模板表（Armors / Mobility / Abilities / Sensors / Specializations —— **只读不改**）
    target: str
    #: 人话类别（写在行尾 value 那一格）
    category: str
    #: 抬头那颗按钮上的字
    action: str


#: ③ 原本的三类（装甲 / 机动 / 能力）—— **顺序 = 界面上那三栏的顺序**。
STAT_KINDS: tuple[StatKind, ...] = (
    StatKind("UnitArmors", "ArmorId", "Armors", "装甲", "＋ 装甲"),
    StatKind("UnitPropulsions", "MobilityId", "Mobility", "机动", "＋ 机动"),
    StatKind("UnitAbilities", "AbilityId", "Abilities", "能力", "＋ 能力"),
)

#: 传感器（用户 2026-10-02 定：「传感器」自己单独一栏）—— 三种形态都常显。
#: 数据侧：SensorUnits 全库 609 行 / 539 个单位，**每个单位 1–3 行**（实测 473/62/4），
#: 所以这一栏在三种形态上都有行可画（规则 req.SensorUnits 也要求 ≥1）。
SENSOR_KIND = StatKind("SensorUnits", "SensorId", "Sensors", "传感器", "＋ 传感器")

#: 专精可用性（用户 2026-10-02 定：也单独一栏）。**空就空着**，不填假值。
#: 取数口径**照抄** relation_tree._specs_group：一行 = SpecializationAvailabilities
#: 里 UnitId == 本单位 的一行，label = Specializations.Name。
#: 数据侧：全库 338 行 / **338 个不同 UnitId**（= 一单位最多一行，1:1）；单位 23 = **0 行**。
SPEC_KIND = StatKind(
    "SpecializationAvailabilities",
    "SpecializationId",
    "Specializations",
    "专精可用性",
    "＋ 专精可用性",
)

#: 这一块能画的那几类（顺序 = 界面上各栏的顺序，见 bench_page 的装配）。
STAT_KIND_ALL: tuple[StatKind, ...] = (*STAT_KINDS, SENSOR_KIND, SPEC_KIND)

#: 「上限 N」那一列 —— 与 bench_transport.CAP_COLUMN 同一列、同一口径
#: （Xp1/Xp2/Xp3 全库非默认行数都是 0，是占位，见 docs/数据-表与列清单.md）。
SPEC_CAP_COLUMN = "MaxAvailabilityXp0"


def stat_kind(table: str) -> StatKind | None:
    """按关联表名取那一类（找不到给 None —— 界面据此拒绝「加一行」）。"""
    for kind in STAT_KIND_ALL:
        if kind.table == table:
            return kind
    return None


def stat_block_title(kinds: tuple[StatKind, ...]) -> str:
    """这一栏抬头那行字（**没有编号**）：把这一栏画的那几类拼起来。

    编号由**摆第几块**决定（bench_page 装配时按位置写），所以这里一个字都不带
    —— 块的常量里写死编号就会出现「摆 ⑧ 却写着 ④」那种一份数据两个说法。
    """
    return " / ".join(kind.category for kind in kinds)


def stat_block_hint(kinds: tuple[StatKind, ...]) -> str:
    """抬头右边那句灰字说明（这一栏读的是哪几张关联表）。"""
    return " · ".join(kind.table for kind in kinds)


@dataclass(frozen=True, slots=True)
class StatLine:
    """属性栏里的一行 = 关联表里的一行。

    row_id 是**关联行**（✕ 删它），link_row_id 是它指向的**模板行**（点进去看它）。
    """

    #: 关联表名（UnitArmors …）—— row_selected 发出去的就是它
    table: str
    #: 关联行 Id
    row_id: int
    #: 模板行 Id（Armors / Mobility / Abilities 那一行）
    link_row_id: int
    #: 名字（`unit_card.row_name` 的口径：显示名，不是 Id）
    label: str
    #: 一句说明（「装甲值 10 · 生命 70」…）
    detail: str = ""
    #: 类别（装甲 / 机动 / 能力 / 传感器 / 专精可用性）
    category: str = ""
    #: 模板表名（界面用它取那一行的名字）
    link_table: str = ""

    @property
    def row_ref(self) -> CellRef:
        return CellRef(self.table, self.row_id, ID_COLUMN)


#: 能力行那句说明取这些"它到底干什么"的开关（顺序 = 界面上读起来的顺序）。
ABILITY_FLAGS: tuple[tuple[str, str], ...] = (
    ("IsRadar", "雷达"),
    ("IsLaserDesignator", "激光指示"),
    ("IsInfantrySprint", "冲刺"),
    ("IsSmoke", "烟雾"),
    ("IsAPS", "主动防御"),
    ("IsSneakAbility", "潜行"),
    ("IsDecoy", "诱饵"),
    ("IsResupplyAbility", "补给"),
    ("CanHeal", "治疗"),
    ("CanRepair", "维修"),
    ("CanArtilleryAutoFire", "自动炮击"),
)

#: 传感器那句说明：(列名, 界面上的字)。0 = 这一档视野没有，就不写。
SENSOR_OPTICS: tuple[tuple[str, str], ...] = (
    ("OpticsGround", "地面"),
    ("OpticsLowAltitude", "低空"),
    ("OpticsHighAltitude", "高空"),
)


def _speed_text(value: float) -> str:
    """速度保留一位小数（设计图上是「前进 18.0 km/h」）。"""
    return f"{value:.1f}"


def _health_note(data: CardData, unit_id: int, health: int) -> str:
    """「（= 5 × 14 人）」—— 步兵的血是"一个人多少血 × 班里有几个人"。

    除不尽就不写（宁可少一句话，也不要编一个假等式）。
    """
    members = len(data.squad(unit_id))
    if members and health > 0 and health % members == 0:
        return f"（= {health // members} × {members} 人）"
    return ""


def _armor_detail(data: CardData, unit_id: int, row: Any) -> str:
    rows = data.t("Armors")
    value = rows.i(row, "ArmorValue")
    health = rows.i(row, "MaxHealthPoints")
    return f"装甲值 {value} · 生命 {health}" + _health_note(data, unit_id, health)


def _mobility_detail(data: CardData, row: Any) -> str:
    rows = data.t("Mobility")
    parts = [f"前进 {_speed_text(rows.f(row, 'MaxSpeedRoad'))} km/h"]
    if rows.b(row, "IsAirDroppable"):
        parts.append("可空投")
    if rows.b(row, "IsAmphibious"):
        parts.append("两栖")
    return " · ".join(parts)


def _ability_detail(data: CardData, row: Any) -> str:
    rows = data.t("Abilities")
    parts: list[str] = []
    for column, label in ABILITY_FLAGS:
        if not rows.b(row, column):
            continue
        # 冲刺那一条带一个秒数（Abilities.SprintDuration）；其余开关没有可读的量。
        seconds = rows.f(row, "SprintDuration") if column == "IsInfantrySprint" else 0.0
        parts.append(f"{label} {seconds:g} 秒" if seconds > 0 else label)
    return " · ".join(parts) or "这条能力没开任何效果列"


def _sensor_detail(data: CardData, row: Any) -> str:
    """视野那三个数是**浮点**（实测 Sensors#23 = 700.0 / 1000.0 / 900.0）。

    ❗ 这里必须用 `f()`：`i()` 对浮点一律给 0（同一张表的 ArmorValue 才是整数），
    用错就是"传感器那一行永远没有说明"。
    """
    rows = data.t("Sensors")
    parts = [
        f"{label} {rows.f(row, column):g}" for column, label in SENSOR_OPTICS if rows.f(row, column)
    ]
    return "光学 " + " · ".join(parts) if parts else ""


def _spec_detail(data: CardData, assoc: Any) -> str:
    """专精可用性那一行的说明 = 「上限 N」。

    N 取**关联行**上的 MaxAvailabilityXp0（与 bench_transport.CAP_COLUMN 同一条口径、
    同一个出处）：Xp1 / Xp2 / Xp3 三列全库非默认行数都是 0（占位，AGENTS §0.5 判据 1），
    实测取值是 1 / 2 / 3 / 4 / 6 / 8 —— **没有 0**。
    0 只可能是这一会话刚用「＋」插进来的新行（还没填），那时不写这一句（不编假数）。
    """
    rows = data.t(SPEC_KIND.table)
    cap = rows.i(assoc, SPEC_CAP_COLUMN)
    return f"上限 {cap}" if cap > 0 else ""


def _detail_of(
    kind: StatKind, data: CardData, unit_id: int, link_id: int, assoc: Any = None
) -> str:
    """说明文字写在**模板行**上（Armors / Mobility / Abilities / Sensors），

    ❗ 不是关联行 —— 关联行只有 (Id, UnitId, 外键) 三列，读它只会得到一堆 0
    （第一版就是这里读错了对象，冒烟跑出来"装甲值 0 · 生命 0"）。

    ⚠️ 唯一的例外是**专精可用性**：它的「上限 N」长在**关联行**上
    （SpecializationAvailabilities.MaxAvailabilityXp0），模板表 Specializations
    那一行没有这一列 —— 所以那一路拿的是 assoc（关联行本身）。
    """
    if kind.table == SPEC_KIND.table:
        return _spec_detail(data, assoc) if assoc is not None else ""
    if link_id <= 0:
        return "（这一行没指向任何模板行）"
    target = data.t(kind.target).by_id(link_id)
    if target is None:
        return f"（{kind.target} #{link_id} 已经不在了）"
    if kind.table == "UnitArmors":
        return _armor_detail(data, unit_id, target)
    if kind.table == "UnitPropulsions":
        return _mobility_detail(data, target)
    if kind.table == "UnitAbilities":
        return _ability_detail(data, target)
    return _sensor_detail(data, target)


def _kind_rows(data: CardData, kind: StatKind, unit_id: int) -> tuple[Any, ...]:
    """这一类里 `UnitId = 本单位` 的那些行，按**行 Id** 排（表的原始顺序不稳）。"""
    table = data.t(kind.table)
    return tuple(sorted(table.where("UnitId", unit_id), key=lambda row: table.i(row, ID_COLUMN)))


def stat_lines(
    data: CardData, unit_id: int, *, kinds: tuple[StatKind, ...] = STAT_KINDS
) -> tuple[StatLine, ...]:
    """这一栏的全部行：一行 = 关联表里的一行。

    ❗ kinds 是**唯一**的口径开关（2026-10-02 拆块：装甲 / 机动 / 能力 / 传感器 /
    专精可用性各一栏，每栏只画自己那一类）。⛔ 不许再留第二个开关
    （从前那个 with_sensors= 已删 —— 两个开关就会有两个真相）。

    真数据（实例 52085）：单位 70 三类共 3 行；**F-15E（287）是 1 + 2 + 3 = 6 行**
    —— 所以"一行一个类别"那种画法会丢行，这里是一行一个关联行。
    """
    out: list[StatLine] = []
    for kind in kinds:
        table = data.t(kind.table)
        for row in _kind_rows(data, kind, unit_id):
            row_id = table.i(row, ID_COLUMN)
            link_id = table.i(row, kind.column)
            out.append(
                StatLine(
                    table=kind.table,
                    row_id=row_id,
                    link_row_id=link_id,
                    label=row_name(data, kind.target, link_id)
                    if link_id
                    else f"{kind.table} #{row_id}",
                    detail=_detail_of(kind, data, unit_id, link_id, row),
                    category=kind.category,
                    link_table=kind.target,
                )
            )
    return tuple(out)


def new_stat_values(
    data: CardData,
    unit_id: int,
    table: str,
    target_id: int,
    *,
    changeset: Changeset | None = None,
) -> dict[str, Any]:
    """③ 的「＋」要插的那一行（`insert_row` 的 values）。

    三列：`Id`（**next_row_id**，把本会话删过的 Id 也算上）、`UnitId` = 本单位、
    外键列 = 挑中的模板行。其余列留空（表本来就只有这三列）。

    ❗ 别自己写"最大 Id + 1"：Id 有大空洞，而且"删掉最大那一行"之后会捡回刚删的 Id
    （core/edit/clone.py::next_row_id 的注释里写着这条）。
    """
    kind = stat_kind(table)
    if kind is None:
        raise KeyError(
            f"{table} 不是编成台这几栏里的表（{', '.join(item.table for item in STAT_KIND_ALL)}）"
        )
    target = changeset.table(table) if changeset is not None else data.instance.table(table)
    return {
        ID_COLUMN: next_row_id(target, changeset),
        "UnitId": int(unit_id),
        kind.column: int(target_id),
    }


# --------------------------------------------------------------------------- #
# ④ 改装槽：槽内互斥（点一项 = 选中它）
# --------------------------------------------------------------------------- #

#: 选项那一行的 ref 落在那张表上（写的是它的 IsDefault）。
OPTIONS_TABLE = "Options"
MODIFICATIONS_TABLE = "Modifications"


@dataclass(frozen=True, slots=True)
class SlotOptionView:
    """④ 里的一**项**：圆圈 + 短名 + 效果一句话 + 费用。"""

    option_id: int
    name: str
    detail: str
    cost_text: str
    selected: bool
    #: Options.OptionPicture（族名见 core/assets/families.py::OPTION_PICTURE_FAMILY）
    icon: str = ""
    modification_id: int = 0
    #: 这个槽是不是本单位的（False = 借来的槽，只给看不给选）
    is_own_slot: bool = True

    @property
    def ref(self) -> CellRef:
        return CellRef(OPTIONS_TABLE, self.option_id, ID_COLUMN)


@dataclass(frozen=True, slots=True)
class SlotGroupView:
    """④ 里的一个**槽**：`Modifications` 一行 + 槽内互斥的那几项。"""

    modification_id: int
    title: str
    is_own_slot: bool = True
    owner_unit_id: int = 0
    #: 借来的槽那一句（本单位的槽是空串）
    note: str = ""
    options: tuple[SlotOptionView, ...] = ()

    @property
    def current_option_id(self) -> int:
        for option in self.options:
            if option.selected:
                return option.option_id
        return 0

    @property
    def selected_option(self) -> SlotOptionView | None:
        """**当前选中那一项**；一项都没选中（也不在 Order==0 上）时给 None。

        折叠态（飞机：一行一个槽、只显示当前选中项）要的就是它 ——
        判据只有 `option.selected` 这一份（出处见模块注释），界面不再自己比 Id。
        """
        for option in self.options:
            if option.selected:
                return option
        return None

    @property
    def option_count(self) -> int:
        """这个槽里有几条选项 —— 设计的「N 选 1」里的那个 N。"""
        return len(self.options)

    @property
    def ref(self) -> CellRef:
        return CellRef(MODIFICATIONS_TABLE, self.modification_id, ID_COLUMN)


@dataclass(frozen=True, slots=True)
class SlotSelection:
    """点一下选项行的**结果**：要写哪几格 + 一句给状态栏的话。

    refused=True = **一个字节都不许写**（借来的槽 / 选项不属于这个槽 / 没有这个选项）。
    """

    writes: tuple[tuple[int, str, bool], ...] = ()
    message: str = ""
    refused: bool = False
    option_label: str = ""
    cost: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.writes)


def slot_owner_unit(data: CardData, modification_id: int) -> int:
    """这个槽挂在哪个单位上（`Modifications.UnitId`）；查不到就是 0。

    与 `app/screens/editor/pages/options_page.py::slot_owner_unit` 同一口径，
    零 Qt 的那一份放在这里（那一页 import 了 Qt，viewmodel 不能反过来依赖它）。
    """
    rows = data.t(MODIFICATIONS_TABLE)
    modification = rows.by_id(modification_id)
    return rows.i(modification, "UnitId") if modification is not None else 0


def slot_titles(data: CardData, groups: tuple[CardOptionGroup, ...]) -> dict[int, str]:
    """槽名：**掐掉本单位各槽名字里共有的词**（`unit_card.short_names` 的口径）。

    为什么要掐：`Modifications.Name` 是内部名（"Squad MarineRaiders"），一个单位几个槽
    时前面那一截人人都一样 —— 一屏 5 个槽全写同一句，等于没写。
    ⚠️ 只有一个槽时 short_names 会**退回原名**（共有词就是它自己），这是它的既有口径。
    """
    rows = data.t(MODIFICATIONS_TABLE)
    labels = {
        group.modification_id: rows.s(rows.by_id(group.modification_id), "Name", "")
        for group in groups
    }
    short = short_names(labels)
    return {
        modification_id: (short.get(modification_id) or "").strip() or raw.strip()
        for modification_id, raw in labels.items()
    }


def slot_option_labels(data: CardData, modification_id: int) -> dict[int, str]:
    """槽内每一项的**短名** —— 转发 `unit_card.option_short_labels`（口径只有那一份）。

    两条口径（先掐槽名前缀、再退同槽共有词）与逐条理由都搬到了 `unit_card`：
    卡片的 `_option_label` 在「本地化值是 null」那条回退链上也要用它，
    而 bench_slots 已经 import unit_card、反过来不行。这里保留这个名字是因为
    `bench_turrets` 等调用方按它 import（改名要动一片，不值当）。
    """
    return option_short_labels(data, modification_id)


def option_icon(data: CardData, option_id: int) -> str:
    """这一项自己的图（Options.OptionPicture）；没有就空串（界面留空位，不画假图）。"""
    rows = data.t(OPTIONS_TABLE)
    return rows.s(rows.by_id(option_id), "OptionPicture", "")


def effect_sentence(effect: OptionEffect) -> str:
    """一条效果写成人话：标签 + 值（标签已经在值里就不重复）。

    例：`("换成这套装甲", "T-72B 车体")` -> "换成这套装甲 T-72B 车体"；
    `("炮塔挂载槽", "槽 7–12 · 19 · MK82 SE x1")` -> 原样。
    """
    text = effect.text.strip()
    label = effect.label.strip()
    if not text:
        return label
    if not label or label in text:
        return text
    return f"{label} {text}"


def option_effect_text(data: CardData, option: CardOptionRow) -> str:
    """一项的"效果一句话"。

    两条来源（都在 `unit_card.option_effects` 里）：

    1. `Options.ReplaceUnitId` 非 0 —— 这一项 = **换成另一个单位**，设计图上那句话是
       「选中 → 这个班变成 #70 Marine Raiders」；
    2. 其余效果逐条拼（装甲 / 机动 / 传感器 / 能力 / 挂载槽 / 隐蔽 …）。

    一条都没有就明说"这一项不改别的列"（别留白，用户看不出是空还是没画出来）。
    """
    parts: list[str] = []
    if option.replaces_unit_id:
        name = data.name_of("Units", option.replaces_unit_id)
        parts.append(f"选中 → 这个班变成 #{option.replaces_unit_id} {name}".rstrip())
    parts.extend(
        effect_sentence(effect) for effect in option.effects if effect.column != "ReplaceUnitId"
    )
    return " · ".join(parts) or "这一项不改别的列"


def mod_groups(
    data: CardData, unit_id: int, *, selected: Any = None
) -> tuple[CardOptionGroup, ...]:
    """④ 的槽 › 选项（含**变体借来的槽**那条路）。

    直接用 `unit_card.card_option_groups` —— 判据（`is_own_slot` / 当前项 /
    "这一项改了什么"）只有那一份实现，这里不再另写一套。
    """
    return card_option_groups(data, unit_id, selected=selected)


def slot_views(data: CardData, unit_id: int, *, selected: Any = None) -> tuple[SlotGroupView, ...]:
    """④ 界面上要画的那一份：槽名 + 槽内每一项的（短名 / 效果 / 费用 / 选中态 / 图）。"""
    groups = mod_groups(data, unit_id, selected=selected)
    titles = slot_titles(data, groups)
    out: list[SlotGroupView] = []
    for group in groups:
        labels = slot_option_labels(data, group.modification_id)
        options = tuple(
            SlotOptionView(
                option_id=option.option_id,
                name=labels.get(option.option_id) or option.label,
                detail=option_effect_text(data, option),
                cost_text=option.cost_text,
                selected=option.selected,
                icon=option_icon(data, option.option_id),
                modification_id=group.modification_id,
                is_own_slot=group.is_own_slot,
            )
            for option in group.options
        )
        title = (
            titles.get(group.modification_id)
            or group.label
            or f"{MODIFICATIONS_TABLE} #{group.modification_id}"
        ).strip()
        note = (
            ""
            if group.is_own_slot
            else f"借来的槽（属于单位 #{group.owner_unit_id}）· 只给看不给选"
        )
        out.append(
            SlotGroupView(
                modification_id=group.modification_id,
                title=title,
                is_own_slot=group.is_own_slot,
                owner_unit_id=group.owner_unit_id,
                note=note,
                options=options,
            )
        )
    return tuple(out)


def amount_text(value: Any) -> str:
    """费用按**相对价**写：加多少钱（0 就写 0）。

    与 `options_page.py::_amount` 同一口径 —— 那一页 import 了 Qt，
    所以这条小函数在这里留一份零 Qt 的。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if not value:
        return "0"
    return f"+{value:g}"


def select_option(
    data: CardData, unit_id: int, modification_id: int, option_id: int
) -> SlotSelection:
    """「这个槽选中这一项」—— **要写哪几格**（纯函数，零 Qt）。

    判据逐条照 `app/screens/editor/pages/options_page.py::select_slot_option`
    （那一条是既有口径，别另起一套）；这里只把"写盘"那一步留给界面：
    界面拿 `writes` 逐格走 `EditorContext.set_value`。

    三个"不许写"的分支都返回 `refused=True`：

    - 没有这个选项；
    - **槽不是本单位的**（变体借来的槽：改它会一起改到槽的主人）；
    - 选项不属于这个槽（串槽 = 改错数据）。

    已经是那一项时返回空 `writes`（`refused=False`）—— 界面据此**什么都不写**、
    状态栏说一句「已经是这一项」，撤销栈里也不多一笔。
    """
    rows = data.t(OPTIONS_TABLE)
    option = data.option(option_id)
    if option is None:
        return SlotSelection(message=f"没有选项 #{option_id}", refused=True)
    owner = slot_owner_unit(data, modification_id)
    if owner != unit_id:
        return SlotSelection(
            message=(
                f"这个槽（#{modification_id}）挂在单位 #{owner} 上，不是本单位的槽 —— "
                "变体借来的槽只给看不给改（改它会一起改到槽的主人）"
            ),
            refused=True,
        )
    if rows.i(option, "ModificationId") != modification_id:
        return SlotSelection(
            message=f"选项 #{option_id} 不在这个槽（#{modification_id}）里", refused=True
        )
    label = slot_option_labels(data, modification_id).get(option_id) or f"#{option_id}"
    cost = rows.i(option, "Cost")
    writes = exclusive_selection_writes(data, modification_id, option_id)
    if not writes:
        return SlotSelection(
            message=f"「{label}」已经是这个槽选中的那一项", option_label=label, cost=cost
        )
    also = len(writes) - 1
    tail = f" · 同槽另外 {also} 项已自动取消" if also else ""
    return SlotSelection(
        writes=writes,
        message=f"已选中「{label}」（{amount_text(cost)}）{tail}",
        option_label=label,
        cost=cost,
    )


def new_option_values(
    data: CardData, modification_id: int, *, changeset: Changeset | None = None
) -> dict[str, Any]:
    """④ 的「＋ 加选项」要插的那一行（`Options` 一行）。

    只填**能让它自己站住**的那几列：Id（next_row_id）、ModificationId（加到哪个槽）、
    Name（空名字在卡上只剩 #Id）、Order（该槽现有 Order 的**最大值 + 1**）、IsDefault=False
    （**新加的一项不抢当前项** —— 抢了就等于"点一下 ＋ 把用户的配置改了"）。
    其余 40 来列留空 = 这一项不改任何东西。
    """
    rows = data.t(OPTIONS_TABLE)
    target = (
        changeset.table(OPTIONS_TABLE)
        if changeset is not None
        else data.instance.table(OPTIONS_TABLE)
    )
    fresh_id = next_row_id(target, changeset)
    # ❗ Order 取该槽现有 Order 的**最大值 + 1**，不是"条数"：同槽删过行之后两个数就分叉
    #    （删掉 Order=0 那条 -> 只剩 1 条、末位序号 1，而新行该排在 2）。
    siblings = rows.where("ModificationId", modification_id)
    order = max((rows.i(row, "Order") for row in siblings), default=-1) + 1
    return {
        ID_COLUMN: fresh_id,
        "ModificationId": int(modification_id),
        "Name": f"新选项 #{fresh_id}",
        "UIName": "",
        "Order": order,
        SELECTION_COLUMN: False,
    }


def copy_option_values(
    data: CardData, option_id: int, *, modification_id: int = 0, changeset: Changeset | None = None
) -> dict[str, Any] | None:
    """照抄 `Options` 那一行（**整行 46 列**），只换身份。找不到源行给 `None`。

    用户 2026-10-02：「给所有的子选项都加一个复制按钮，因为很多时候我都是复制一栏再在上面
    进行修改的」。实测一条真实选项只有 **10/46 列非空**（#208 40x S-8）—— 照抄确实省事。

    只改这三样（其余逐列照抄）：

    - `Id` = `next_row_id`（本会话删过的 Id 也算上）；
    - `Order` = **目标槽现有 Order 的最大值 + 1**（复制件接在槽尾，不是源行的 Order）；
    - `IsDefault` = **False** —— 复制出来那一项**不抢当前项**（与 `new_option_values` 同一条
      口径：点一下「复制」不该把用户已经选好的配置改掉）。

    `modification_id` 不给（0）就照抄源行那一列；给了就落到那个槽（调用方保证它存在）。
    """
    rows = data.t(OPTIONS_TABLE)
    row = rows.by_id(int(option_id))
    if row is None:
        return None
    target_slot = int(modification_id) or rows.i(row, "ModificationId")
    table = (
        changeset.table(OPTIONS_TABLE)
        if changeset is not None
        else data.instance.table(OPTIONS_TABLE)
    )
    values = {column: rows.get(row, column) for column in table.columns}
    siblings = rows.where("ModificationId", target_slot)
    values[ID_COLUMN] = next_row_id(table, changeset)
    values["ModificationId"] = target_slot
    values["Order"] = max((rows.i(item, "Order") for item in siblings), default=-1) + 1
    values[SELECTION_COLUMN] = False
    return values


def new_slot_values(
    data: CardData, unit_id: int, *, changeset: Changeset | None = None
) -> dict[str, Any]:
    """④ 的「＋ 加一个槽」要插的那一行（`Modifications` 一行）。**新槽是空的。**

    只填**能让它自己站住**的那几列（`Modifications` 一共就 7 列，全在下面）：

    - `Id` = `next_row_id`（本会话删过的 Id 也算上，见 clone.py 的说明）；
    - `UnitId` = 本单位 —— 这一列就是"这个槽挂在哪台车上"（`slot_owner_unit` 读它）；
    - `Name` = `新改装槽 #<Id>`（空名字在卡上只剩 #Id，与加选项那条同一口径）；
    - `UIName` = `""`（留空 = 用 Name 那条路，不编假本地化键）；
    - `Type` = 0（图标族的分支在 `row_icon` 里：`Type == 5` 是装甲盾牌，其余落到通用齿轮
      —— 0 就是齿轮，装甲槽要盾牌去右栏改这一列）；
    - `ThumbnailFileName` = `""`（缩略图资源键留空，别编一个不存在的键）；
    - `Order` = **该单位现有 Order 的最大值 + 1**（不是"槽个数"：同一台车上删过槽之后
      两个数就分叉，与 `new_option_values` 的 Order 同一条口径）。

    ❗ 新槽**一条选项都没有**（Options 那边一行都不插）—— 界面据此说清"用 ＋ 加选项往里加"。
    实测（实例 52085）：单位 46 三个槽的 Order 全是 0 ⇒ 新槽 = 1；单位 6 = 1 / 0 / 2 ⇒ 3。
    """
    rows = data.t(MODIFICATIONS_TABLE)
    target = (
        changeset.table(MODIFICATIONS_TABLE)
        if changeset is not None
        else data.instance.table(MODIFICATIONS_TABLE)
    )
    fresh_id = next_row_id(target, changeset)
    siblings = rows.where("UnitId", unit_id)
    order = max((rows.i(row, "Order") for row in siblings), default=-1) + 1
    return {
        ID_COLUMN: fresh_id,
        "UnitId": int(unit_id),
        "Name": f"新改装槽 #{fresh_id}",
        "UIName": "",
        "Type": 0,
        "ThumbnailFileName": "",
        "Order": order,
    }


#: 复制一个槽时给名字加的后缀 —— 只在**本单位已经有同名槽**时才加（自然键 UnitId + Name）。
SLOT_COPY_SUFFIX = " 副本"


def _free_slot_name(rows, siblings, base: Any) -> str:
    """本单位内**没被占**的槽名：空着就用原名，占了就 "原名 副本" / "原名 副本2"…

    为什么非要不重名：`Modifications` 的自然键是 **UnitId + Name**
    （`rules/rulebook.json::natkey.Modifications.UnitId_Name`，原库实测唯一），
    而槽名就是列表里那一行的身份 —— 同名两条用户分不出哪个是复制件
    （`slot_titles` 掐的是"本单位各槽共有的词"，同名时掐不出任何区别）。
    """
    stem = str(base or "").strip()
    if not stem:
        return str(base or "")
    used = {rows.s(item, "Name", "") for item in siblings}
    if stem not in used:
        return stem
    candidate = f"{stem}{SLOT_COPY_SUFFIX}"
    index = 2
    while candidate in used:
        candidate = f"{stem}{SLOT_COPY_SUFFIX}{index}"
        index += 1
    return candidate


def copy_slot_values(
    data: CardData, modification_id: int, *, unit_id: int = 0, changeset: Changeset | None = None
) -> dict[str, Any] | None:
    """照抄 `Modifications` 那一行（**整个槽**）—— 找不到源行给 `None`。

    用户 2026-10-02：「改装槽这里的 Options 也要能复制，也要能改」—— 编成台里别处的行
    都有一颗 ⧉，只有槽行没有；而"复制一栏再在上面改"正是用户 2026-10-02 的原话用法。
    这一份只算**槽那一行**，它名下的选项见 `copy_slot_options`（界面把两半记成一条撤销）。

    只改这三样（其余逐列照抄）：

    - `Id` = `next_row_id`（本会话删过的 Id 不再复用）；
    - `Order` = **本单位现有 Order 的最大值 + 1**（复制件接在槽尾，与 `new_slot_values` 同一条）；
    - `Name` = 照抄；**本单位已经有同名槽**时才加「 副本」（见 `_free_slot_name`）。

    `unit_id` 不给（0）= 照抄源行那一列（这个槽挂在哪台车上就不变）。
    """
    rows = data.t(MODIFICATIONS_TABLE)
    row = rows.by_id(int(modification_id))
    if row is None:
        return None
    table = (
        changeset.table(MODIFICATIONS_TABLE)
        if changeset is not None
        else data.instance.table(MODIFICATIONS_TABLE)
    )
    owner = int(unit_id) or rows.i(row, "UnitId")
    values = {column: rows.get(row, column) for column in table.columns}
    values[ID_COLUMN] = next_row_id(table, changeset)
    values["UnitId"] = owner
    siblings = rows.where("UnitId", owner)
    values["Order"] = max((rows.i(item, "Order") for item in siblings), default=-1) + 1
    values["Name"] = _free_slot_name(rows, siblings, values.get("Name"))
    return values


def copy_slot_options(
    data: CardData, modification_id: int, *, new_modification_id: int
) -> tuple[dict[str, Any], ...]:
    """照抄这个槽下面**全部** `Options`（每行 46 列），指到新槽 —— 按**槽内原顺序**回。

    与 `copy_option_values`（复制**一条**选项）的两处不同，都是"整槽"这件事决定的：

    - `Id` = **None**：一批 N 条，在这里取会取到同一个 Id —— 由 `core_add_row` 现场逐条取
      （`core/edit/clone.py::add_row` 里那句 `payload[ID_COLUMN] is None` 就是为它留的）；
    - `Order` / `IsDefault` **照抄**：复制件落在**新槽**里，而槽内互斥是按槽算的
      （`Options.ModificationId`），照抄不抢任何东西 —— 复制出来的槽与原件一模一样，
      这正是「复制一栏再在上面进行修改」要的起点。单条复制那条 `IsDefault=False`
      是为"别把**当前槽**的选中项换掉"，两件事不冲突。

    顺序 = `(Order, Id)`：表里行序不稳，按槽内顺序回才可断言。
    """
    rows = data.t(OPTIONS_TABLE)
    table = data.instance.table(OPTIONS_TABLE)
    kids = rows.where("ModificationId", int(modification_id))
    ordered = sorted(kids, key=lambda item: (rows.i(item, "Order"), rows.i(item, ID_COLUMN)))
    out: list[dict[str, Any]] = []
    for item in ordered:
        values = {column: rows.get(item, column) for column in table.columns}
        values[ID_COLUMN] = None
        values["ModificationId"] = int(new_modification_id)
        out.append(values)
    return tuple(out)


def option_ids_of(data: CardData, modification_id: int) -> tuple[int, ...]:
    """这个槽底下**全部选项行**的 Id（删整槽之前要拿它级联）。

    判据只有一条：`Options.ModificationId == 这个槽`；实测（实例 52085）**只有 Options
    这一张表**有 `ModificationId` 列（46 列里那一列），所以级联名单就是它，没有第二张表。
    按 Id 排序（表的原始行序不稳，撤销放回来的顺序要可断言）。
    """
    rows = data.t(OPTIONS_TABLE)
    return tuple(
        sorted(rows.i(row, ID_COLUMN) for row in rows.where("ModificationId", int(modification_id)))
    )


__all__ = [
    "ABILITY_FLAGS",
    "MODIFICATIONS_TABLE",
    "OPTIONS_TABLE",
    "SELECTION_COLUMN",
    "SENSOR_KIND",
    "SENSOR_OPTICS",
    "SPEC_CAP_COLUMN",
    "SPEC_KIND",
    "STAT_KINDS",
    "STAT_KIND_ALL",
    "SlotGroupView",
    "SlotOptionView",
    "SlotSelection",
    "StatKind",
    "StatLine",
    "amount_text",
    "SLOT_COPY_SUFFIX",
    "copy_option_values",
    "copy_slot_options",
    "copy_slot_values",
    "effect_sentence",
    "mod_groups",
    "new_option_values",
    "new_slot_values",
    "new_stat_values",
    "option_effect_text",
    "option_icon",
    "option_ids_of",
    "select_option",
    "slot_option_labels",
    "slot_owner_unit",
    "slot_titles",
    "slot_views",
    "stat_block_hint",
    "stat_block_title",
    "stat_kind",
    "stat_lines",
]
