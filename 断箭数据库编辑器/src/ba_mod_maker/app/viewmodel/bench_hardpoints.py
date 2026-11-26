"""编成台 ①（**所有载具**：飞机 / 直升机 / 地面车 / 舰船）「21 个挂载槽」的取数与规则。**零 Qt。**

口径出处：

- `docs/交接-2026-10-01-单位编成台设计.md` §3 口径 5：飞机 ①＝**21 格挂载图，一个都不折叠**
  （含空的与未用的）；口径 1：**只有 ＋ 和 ✕，没有"换"**；口径 8：挂点指数据库
  `Options.Turret0..20Id`（**编辑对象**），3D 模型挂点不碰；
- 设计图 `docs/设计稿-编成台-飞机.png` 的中栏 ①（F-15E Strike Eagle / Units#287）；
  生成器 `_work/design/gen_bench_air.py::cell()` 是格子上那三档字的**原始口径**；
- `docs/设计-界面重做-定稿.md` §2.3：21 个炮塔挂载槽**全部列出来**（用户明确要求）。

## 为什么不能直接用 `unit_card.turret_slot_cells()`

那个函数只算**一条选项**的 21 格（卡上画的就是"这条选项占了哪些槽"）。
编成台 ① 要的是**整架飞机现在挂着什么** —— 一个单位有 5 个改装槽，每个槽各有一条
当前选中项（`IsDefault` 为真，否则 `Order == 0`，判据见 `unit_card.SELECTION_COLUMN`
与 `CardData.default_option`），**每个槽负责几个槽位**：

    实测 F-15E（Units#287，实例 52085，实跑）：
      mod #305 Weapon F15E WingPylons1  -> 选项 #884  -> 槽 1,2 = Turrets#300 / 槽 3,4 = #548
      mod #306 Weapon F15E WingPylons2  -> 选项 #891  -> 槽 5,6 = #556
      mod #307 Weapons F15E Fuselage    -> 选项 #897  -> 槽 7-12 = #160 / 槽 13-18 = #557
      mod #308 Weapons F15E Centerline  -> 选项 #900  -> 槽 19 = #160
      mod #543 Ability F15E             -> 选项 #1929 -> 一个槽都不占
      → 槽 0 与槽 20 **不属于任何改装槽**（设计图上那两格写着这六个字）

所以这一层做的是**按槽合并 + 记下"这格是谁放的"**：`owner_modification_id` /
`owner_option_id` / `owner_label` / `owner_name` 是右栏与 tooltip 要的东西。

## 一格上那三档字（照 `gen_bench_air.py::cell()`，逐档对上设计图）

| 这一格 | 主字 | 右上角小字 | 实例 |
|---|---|---|---|
| 不属于任何改装槽（**没有任何选项用过这一列**） | `空` | 「不属于任何改装槽」 | 槽 0 / 槽 20 |
| 有选项占它、但挂空挂架 | **改装槽的短名** | `空` | 槽 7-12 写 `Fuselage`、槽 19 写 `Centerline` |
| 真有东西 | **装的那件武器名** | `×N` | `CBU-87 Rockeye` ×4 / `Sidewinder` ×1 |

⚠️ 中档那两句话**不是"这格没东西"**：它是"这一格由哪个改装槽管"——
`Fuselage` = `Modifications#307 Weapons F15E Fuselage`，这才是格子上那句字的来源
（`Weapons F15E ` 那一截按 `gen_bench_air.py::owner_of()` 的口径从名字里去掉）。

## 四条钉死的规则

1. **21 格一个都不折叠**（含空槽、含"不属于任何改装槽"的格）——
   `hardpoint_view().cells` 恒 21 个；
2. `Options.Turret20Id` **全库 2046/2046 恒 0**（产品规则 `zero.Options.Turret20Id`：
   "不要用它当第 21 个炮塔槽"）—— 第 20 格**永远是空的，但仍然要画**。
   这里**不特判**：扫到就用，游戏哪天启用它我们不改代码
   （与 `unit_card.option_turret_slots` 同一条口径）；
3. **"空槽" = 格子上写着「空」的那两档**（`TurretNId == 0` **或** 挂的是空挂架
   `Turret Empty`）—— 用户在界面上看到的都是「空」，所以「＋ 加一个挂载」两档都挂得上。
   ⚠️ 这一条**与 `CardData.card_turrets()` 不是同一件事**：那边 `Turret Empty` 也算
   "这个槽被 `TurretNId` 覆盖了"（游戏用它表示"这个挂架装不了东西"），
   这里按**编成台的操作口径**办（用户看到空就该能挂）。
   **实测依据**：F-15E 的槽 7-12 / 19 全是空挂架 #160 —— 按"只有 `TurretNId == 0`
   才算空"来算的话，「＋ 加一个挂载」在这架飞机上一次都点不动（21 格没有一格是 0）。
3b. **归属（这个槽归哪条选项管）= 结构，不是当前值**（`_slot_owners`）：
   判据是"那条选项的 `TurretNId` 被它自己的选项用过"。所以 `TurretNId` 清成 0 之后
   **仍然归原来那条选项管** —— 不然"✕ 清掉再 ＋ 挂回来"就再也回不来了（实测踩到）。
4. **合并的算法逐字照 `CardData.card_turrets()`**（游戏 ApplyCurrentMods 的口径）：
   一个槽位取**第一条把它填成非 0 的选中选项**。实测 F-15E 的合并结果与
   `card_turrets()` 逐格相同（只差槽 0：那边是 `TurretUnits` 的默认挂架 #547，
   这里按"空槽 + 不属于任何改装槽"画 —— 口径 8 说挂载槽的编辑对象就是 `TurretNId`）。

❌ **不许在界面层直接写 `Table.rows`**：写值一律经 `EditorContext.set_value(CellRef(...))`。

❗ 本模块不得 import 任何 Qt 符号（`tests/test_core_layering.py` 用 AST 扫）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ba_mod_maker.app.viewmodel.unit_card import (
    SELECTION_COLUMN,
    CardData,
    slot_selected_option,
)
from ba_mod_maker.core.edit.changeset import ID_COLUMN
from ba_mod_maker.core.edit.refs import TURRET_SLOT_COLUMNS, CellRef

#: 槽位数（`Options.Turret0Id` … `Options.Turret20Id`）。列名只有一份出处：
#: `core/edit/refs.py::TURRET_SLOT_COLUMNS`，这里只把长度收成一个名字。
SLOT_COUNT = len(TURRET_SLOT_COLUMNS)

#: 挂载槽是**改装选项**的列 —— 「＋ 加一个挂载」写的就是这一格。
OPTIONS_TABLE = "Options"
MODIFICATIONS_TABLE = "Modifications"
TURRETS_TABLE = "Turrets"
WEAPONS_TABLE = "Weapons"
AMMUNITIONS_TABLE = "Ammunitions"
WEAPON_AMMO_TABLE = "WeaponAmmunitions"

#: 空格子上那个字（设计图：`12 空`）。
EMPTY_TEXT = "空"

#: 没有任何选项占这个槽时，格子上那一行小字（设计图：第 0 / 第 20 格）。
UNCLAIMED_NOTE = "不属于任何改装槽"

#: 「＋ 加一个挂载」在 21 格都占满时的那句话（**绝不弹窗**）。
NO_FREE_SLOT_MESSAGE = "21 个挂载槽都占满了 —— 先 ✕ 掉一个再挂"

#: `Modifications.Name` 开头那两个分类词 —— 掐短名时**别当成单位名**掐掉。
#: 与 `gen_bench_air.py::owner_of()` 的 `replace("Weapon F15E ", "").replace("Weapons F15E ", "")`
#: 等价，但按**本单位共有词**现算（不把 "F15E" 写死在这条通用链路上）。
_MOD_PREFIX_WORDS: tuple[str, ...] = ("Weapons", "Weapon")


def slot_column(slot: int) -> str:
    """第 slot 个挂载槽对应 `Options` 的哪一列（越界时抛 IndexError，别静默给错列）。"""
    return TURRET_SLOT_COLUMNS[int(slot)]


def is_valid_slot(slot: int) -> bool:
    """这个槽号在 0..20 里吗（界面用它挡掉越界的写入）。"""
    return 0 <= int(slot) < SLOT_COUNT


# --------------------------------------------------------------------------- #
# 当前选中项
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SlotPick:
    """一个改装槽**当前选中**的那一项（合并 21 格要的就是它）。"""

    modification_id: int
    option_id: int
    #: 改装槽原名（`Weapons F15E Fuselage`）
    label: str = ""
    #: 掐掉单位名那一截的短名（`Fuselage`）—— 空格子上画的就是它
    short: str = ""

    @property
    def valid(self) -> bool:
        return self.modification_id > 0 and self.option_id > 0

    @property
    def ref(self) -> CellRef:
        return CellRef(OPTIONS_TABLE, self.option_id, ID_COLUMN)


def _unit_words(rows: Any, modifications: tuple[Any, ...]) -> set[str]:
    """本单位几个改装槽名字里**共有**的词（多半是单位名，例：F15E）。

    只拿这一组的名字求交集 —— 与 `unit_card.short_names` 同一条口径；
    "Weapons / Weapon" 不算共有词（同一个单位里这两族常常并存，实测 F-15E 就是）。
    """
    names = [rows.s(modification, "Name", "") for modification in modifications]
    names = [name for name in names if name]
    if not names:
        return set()
    common: set[str] = set(names[0].split())
    for name in names[1:]:
        common &= set(name.split())
    return common - set(_MOD_PREFIX_WORDS)


def _owner_short(rows: Any, modification_id: int, common: set[str], cache: dict[int, str]) -> str:
    """改装槽名字里的"它管的是哪一块"（`Weapons F15E Fuselage` -> `Fuselage`）。

    两条都试，第一条能用的就用：① 掐掉共有词；② 再砍掉开头那个 `Weapons` / `Weapon`。
    **砍不出东西就退回原名**（宁可长一点，也不要画一个空字 —— 空格子上那句话是唯一
    说明"这格归谁管"的地方）。
    """
    cached = cache.get(modification_id)
    if cached is not None:
        return cached
    row = rows.by_id(modification_id)
    name = rows.s(row, "Name", "") if row is not None else ""
    words = [word for word in name.split() if word not in common] if name else []
    if words and words[0] in _MOD_PREFIX_WORDS:
        words = words[1:]
    out = " ".join(words).strip() or name
    cache[modification_id] = out
    return out


def selected_picks(data: CardData, unit_id: int) -> tuple[SlotPick, ...]:
    """本单位**每个改装槽**当前选中的那一项（按 `Modifications.Order, Id`）。

    判据与游戏一致（`CardData.default_option`）：`IsDefault` 为真的第一条，
    否则 `Order == 0` 的第一条；两条都不成立 -> 这个槽没选中任何东西，**跳过**
    （不猜、不补一个假的）。
    """
    rows = data.t(MODIFICATIONS_TABLE)
    modifications = data.modifications_of(unit_id)
    common = _unit_words(rows, modifications)
    cache: dict[int, str] = {}
    out: list[SlotPick] = []
    for modification in modifications:
        modification_id = rows.i(modification, "Id")
        option_id = slot_selected_option(data, modification_id)
        if option_id <= 0:
            continue
        out.append(
            SlotPick(
                modification_id=modification_id,
                option_id=option_id,
                label=rows.s(modification, "Name", ""),
                short=_owner_short(rows, modification_id, common, cache),
            )
        )
    return tuple(out)


def _slot_owners(
    data: CardData, picks: tuple[SlotPick, ...]
) -> tuple[dict[int, SlotPick], dict[int, int]]:
    """21 个槽 -> （**归属**，**当前值**）。

    两件事必须分开，不然「✕ 清一个槽」会**把那个槽永久废掉**（清成 0 之后它
    既不属于任何改装槽、也再也挂不上东西 —— 实测踩到过）：

    - **归属**（owner）：槽 N 归哪条选中选项管。判据 = **那条选项的 `TurretNId` 被它
      自己的选项用过**（同 `ModificationId` 的任意一条 `Options` 在该列非 0）。
      这是**结构**，清成 0 之后仍然成立 —— 于是"清掉还能挂回来"。
      一条都没用过这个列的槽 -> 归属为空 -> 设计图上那句「不属于任何改装槽」。
      例：F-15E 的槽 0 / 20（全库只有 `Turret19Id` 有 15 行非 0，`Turret20Id` 恒 0）。
    - **当前值**（value）：`TurretNId` 的合并结果，**第一条非 0 的选中选项赢**
      （逐字照 `CardData.card_turrets()` 的 `next(... > 0)`）。

    ⚠️ 归属"用过这一列"是**按槽位判**的，不是按"这一条选项非 0"判的 ——
    `gen_bench_air.py::cell()` 用的是后者（`state[i] is None` -> 不属于任何改装槽），
    这里有意与它不同：那边只画一次、不需要"清完还能挂回来"，
    而编成台要能 ＋ / ✕ 来回。
    """
    options = data.t(OPTIONS_TABLE)
    owners: dict[int, SlotPick] = {}
    values: dict[int, int] = {}
    used: set[int] = set()
    # ① 结构：哪条选项"用过"这个列（同槽的所有选项一起看）。
    chosen = {pick.modification_id: pick for pick in picks}
    for modification_id, pick in chosen.items():
        for sibling in data.options_of(modification_id):
            for slot, column in enumerate(TURRET_SLOT_COLUMNS):
                if slot in used:
                    continue
                if options.i(sibling, column) > 0:
                    used.add(slot)
                    owners[slot] = pick
    # ② 当前值：第一条非 0 的选中选项赢（**顺序 = selected_picks 的顺序**）。
    for pick in picks:
        row = options.by_id(pick.option_id)
        if row is None:
            continue
        for slot, column in enumerate(TURRET_SLOT_COLUMNS):
            if slot in values:
                continue
            value = options.i(row, column)
            if value > 0:
                values[slot] = value
    return owners, values


# --------------------------------------------------------------------------- #
# 一格上装的那件东西
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HardpointLoad:
    """格子上**装的那件东西**：炮塔 -> 武器 -> 弹（`card_turret_chain` 的同一走法）。

    label 与 count 的取法（实测 F-15E 的 4 种挂架全部对上设计图）：

    | 炮塔 | 武器 | 格子写 |
    |---|---|---|
    | `PylonTurret AIM9 Wingtip` #300 | `Sidewinder` #113 | `Sidewinder` ×1 |
    | `PylonTurret AMRAAM Wingtip x1` #548 | `AMRAAM` #118 | `AMRAAM` ×1 |
    | `PylonTurret CBU87 x4` #556 | `CBU-87 Rockeye` #398 | `CBU-87 Rockeye` ×4 |
    | `PylonTurret CBU87 x1` #557 | `CBU-87 Rockeye` #400 | `CBU-87 Rockeye` ×1 |
    | `Turret Empty` #160 | ——（没有武器） | **不是这一档**（那格画改装槽名，见 HardpointCell） |
    """

    #: 0 = 这一格没有可画的东西（空挂架）—— 由调用方换画"归哪个改装槽管"
    label: str = ""
    #: 0 = 不写 `×数量`（空挂架 / 没挂上弹）
    count: int = 0
    #: 这一格真正挂着的那座炮塔（0 = 没有）—— 右栏与 tooltip 要它
    turret_id: int = 0
    #: 这件东西落在哪张表哪一行（点一格 -> 去看它的全部列）
    table: str = ""
    row_id: int = 0

    @property
    def count_text(self) -> str:
        return f"×{self.count}" if self.count > 0 else ""


def _primary_load(data: CardData, unit_id: int, turret_id: int) -> HardpointLoad:
    """一座炮塔 -> 格子上那件东西（第一件武器 ＋ 它名下携弹量之和）。

    为什么是"第一件"：挂架炮塔实测只带一件武器（F-15E 的 4 种全一样），
    与 `gen_bench_air.py::cell()` 的 `tws[0]` 同一条口径。
    不带武器的炮塔（`Turret Empty`）返回**带 turret_id 的空 load** ——
    那一档由调用方换画 `owner_name`（设计图第 7-12 / 19 格写的是改装槽名）。
    """
    weapons = data.turret_weapons(turret_id)
    if not weapons:
        return HardpointLoad(turret_id=turret_id, table=TURRETS_TABLE, row_id=turret_id)
    weapon_id = weapons[0]
    label = data.name_of(WEAPONS_TABLE, weapon_id)
    carriers = data.weapon_ammo_rows(unit_id, weapon_id)
    if not carriers:
        return HardpointLoad(
            label=label, turret_id=turret_id, table=WEAPONS_TABLE, row_id=weapon_id
        )
    carrying = data.t(WEAPON_AMMO_TABLE)
    ammo_rows = data.t(AMMUNITIONS_TABLE)
    count = 0
    for row in carriers:
        template = ammo_rows.by_id(carrying.i(row, "AmmunitionId"))
        # HUDMultiplier 与 card_turret_chain._chain_ammo 同一口径（缺行时按 1）。
        multiplier = ammo_rows.i(template, "HUDMultiplier") if template is not None else 1
        count += carrying.i(row, "Quantity") * max(1, multiplier)
    return HardpointLoad(
        label=label, count=count, turret_id=turret_id, table=WEAPONS_TABLE, row_id=weapon_id
    )


# --------------------------------------------------------------------------- #
# 一格
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HardpointCell:
    """**一格**（槽号 0..20）现在画的是什么 —— 界面只读它，不自己判。"""

    slot: int
    #: `Options.TurretNId` 现在的值（0 = 空）
    turret_id: int = 0
    #: 装的那件武器名（空格子是空串）
    label: str = ""
    #: 0 = 不写 `×数量`
    count: int = 0
    #: 放它上去的那个改装槽 / 那条选项（0 = 没有选中项占这个槽）
    owner_modification_id: int = 0
    owner_option_id: int = 0
    #: 那个改装槽的名字（tooltip 里给全名）
    owner_label: str = ""
    #: 那个改装槽的短名（`Fuselage`）—— **空格子上画的就是它**
    owner_name: str = ""
    #: 点一格要去看哪一行
    table: str = ""
    row_id: int = 0
    #: **没有任何选中选项**占这个槽（设计图上第 0 格与第 20 格那几个字）
    unclaimed: bool = False

    @property
    def occupied(self) -> bool:
        """`TurretNId` 非 0（**含空挂架** `Turret Empty`）。"""
        return self.turret_id > 0

    @property
    def loaded(self) -> bool:
        """这一格**真装了东西**（有武器名）—— 空挂架也是 False。"""
        return bool(self.label)

    @property
    def free(self) -> bool:
        """这一格**还挂得上一件东西**：不归任何改装槽的挂不了，其余"空"的都挂得上。

        ❗ "空"按**用户看到的那档字**判（`TurretNId == 0` **或** 挂的是空挂架）
        —— 见模块头第 3 条。这不是理论情形：F-15E 的槽 7-12 / 19 全是空挂架 #160。
        """
        return not self.unclaimed and not self.loaded

    @property
    def count_text(self) -> str:
        return f"×{self.count}" if self.count > 0 else ""

    @property
    def text(self) -> str:
        """格子上那一行主字（设计图：`CBU-87 Rockeye` / `Fuselage` / `空`）。"""
        return self.label or self.owner_name or EMPTY_TEXT

    @property
    def note(self) -> str:
        """格子右上角那一行小字：`不属于任何改装槽` > `×N` > `空`（设计图三档逐字一致）。"""
        if self.unclaimed:
            return UNCLAIMED_NOTE
        return self.count_text or EMPTY_TEXT

    @property
    def state(self) -> str:
        """给 QSS 选择器用的三档之一：`unclaimed` / `empty` / `on`（**不新增角色**）。"""
        if self.unclaimed:
            return "unclaimed"
        return "on" if self.loaded else "empty"

    @property
    def ref(self) -> CellRef | None:
        """这一格装着的东西落在哪一行（空槽 / 没落点 -> None）。"""
        if self.table and self.row_id:
            return CellRef(self.table, self.row_id, ID_COLUMN)
        return None

    @property
    def owner_ref(self) -> CellRef | None:
        """放它上去的那条 `Options`（没有 -> None）—— 点空格时要去看它。"""
        if self.owner_option_id > 0:
            return CellRef(OPTIONS_TABLE, self.owner_option_id, ID_COLUMN)
        return None

    @property
    def tooltip(self) -> str:
        """悬浮提示：这一格是什么、归谁管、点了去哪。"""
        if self.unclaimed:
            return (
                f"炮塔挂载槽 {self.slot}：不属于任何改装槽"
                f"（本单位所有改装选项的 Options.Turret{self.slot}Id 都没在用它）"
            )
        head = f"炮塔挂载槽 {self.slot}"
        if self.loaded:
            head += f" → {self.label}"
            if self.count:
                head += f" ×{self.count}"
            head += f"（Turrets #{self.turret_id}）"
        else:
            head += f" → {EMPTY_TEXT}"
            if self.occupied:
                head += f"（Turrets #{self.turret_id} 是空挂架）"
        owner = self.owner_label or self.owner_name
        if owner:
            head += f"\n由改装槽「{owner}」放上来（Options #{self.owner_option_id}）"
        if self.loaded:
            head += "\n点它 → 右栏换成这件武器的全部列"
        else:
            head += "\n点它 → 右栏换成这条选项的全部列；✕ 清掉这一格"
        return head


# --------------------------------------------------------------------------- #
# 整块
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HardpointView:
    """整架飞机现在的 21 格（`cells` 恒 21 个，顺序 = 槽号 0..20）。"""

    unit_id: int
    cells: tuple[HardpointCell, ...] = ()
    picks: tuple[SlotPick, ...] = ()

    @property
    def occupied_count(self) -> int:
        """`TurretNId` 非 0 的格数（含空挂架）—— 与 `CardData.card_turrets()` 的"占了几格"同义。"""
        return sum(1 for cell in self.cells if cell.occupied)

    @property
    def loaded_count(self) -> int:
        """**真装了东西**的格数（空挂架不算）—— 抬头旁边那句「装了 N 件」。"""
        return sum(1 for cell in self.cells if cell.loaded)

    @property
    def free_slots(self) -> tuple[int, ...]:
        """还挂得上东西的槽号（升序）——「＋ 加一个挂载」挂到**第一个**上面。"""
        return tuple(cell.slot for cell in self.cells if cell.free)

    @property
    def first_free_slot(self) -> int:
        free = self.free_slots
        return free[0] if free else -1

    @property
    def unclaimed_slots(self) -> tuple[int, ...]:
        return tuple(cell.slot for cell in self.cells if cell.unclaimed)

    def cell_at(self, slot: int) -> HardpointCell | None:
        for cell in self.cells:
            if cell.slot == int(slot):
                return cell
        return None

    @property
    def summary(self) -> str:
        """格子矩阵下面那一句（照设计图 ① 的口径：装了 N 件 / 哪几个不归槽管）。"""
        parts = [f"21 个挂载槽，装了 {self.loaded_count} 件"]
        unclaimed = self.unclaimed_slots
        if unclaimed:
            parts.append("不属于任何改装槽：" + "、".join(str(slot) for slot in unclaimed))
        return " · ".join(parts)


def hardpoint_view(data: CardData, unit_id: int) -> HardpointView:
    """本单位的 **21 格挂载图**（设计图 ① 的那一份数据）。

    一个单位在编成台上画哪一块由**形态**决定（载具=四层链 / 飞机与直升机=21 格），
    这里不做那个判断 —— 只按 unit_id 算格子（没有改装槽的单位会得到 21 个空格，
    界面自己不接这一块）。
    """
    picks = selected_picks(data, unit_id)
    owners, values = _slot_owners(data, picks)
    cells: list[HardpointCell] = []
    for slot in range(SLOT_COUNT):
        owner = owners.get(slot)
        turret_id = values.get(slot, 0)
        owner_kwargs = {
            "owner_modification_id": owner.modification_id if owner else 0,
            "owner_option_id": owner.option_id if owner else 0,
            "owner_label": owner.label if owner else "",
            "owner_name": owner.short if owner else "",
        }
        if turret_id <= 0:
            cells.append(HardpointCell(slot=slot, unclaimed=owner is None, **owner_kwargs))
            continue  # 空槽（含"归某条选项管但值是 0"）—— 归属照样记着，挂得回来
        load = _primary_load(data, unit_id, turret_id)
        cells.append(
            HardpointCell(
                slot=slot,
                turret_id=turret_id,
                label=load.label,
                count=load.count,
                table=load.table,
                row_id=load.row_id,
                **owner_kwargs,
            )
        )
    return HardpointView(unit_id=int(unit_id), cells=tuple(cells), picks=picks)


# --------------------------------------------------------------------------- #
# 写：＋ 挂一个 / ✕ 清 0（都只回"要写哪几格"，落地由 EditorContext 做）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HardpointWrite:
    """一次挂载动作的**结果**：要写哪几格 + 一句给状态栏的话。

    `writes` 为空 = **一个字节都不许写**（没空槽 / 槽位不归任何改装槽 /
    本来就是空的）—— 界面据此不调 `set_value`，撤销栈里也不会多一笔。
    """

    writes: tuple[tuple[int, str, int], ...] = ()
    message: str = ""
    slot: int = -1
    turret_id: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.writes)


def place_writes(view: HardpointView, turret_id: int, slot: int | None = None) -> HardpointWrite:
    """「＋ 加一个挂载」：把 `turret_id` 写进某一格（默认 = **第一个空槽**）。

    - 落点 = 参数给的 `slot`；不给就用 `view.first_free_slot`；
    - 那一格归**哪个改装槽的哪条选项**管，就写那条选项的 `TurretNId`
      （`owner_option_id` 是唯一的落点 —— 没有它就没地方写）；
    - `Turret Empty` 那种空挂架**可以直接覆盖**（用户看到是空的，就该能挂），
      而装了真东西的格子**拒绝**（口径 1 没有"换"，要先 ✕ 再 ＋）；
    - **不属于任何改装槽**的格（第 0 / 20 格）写不了：没有 `Options` 行可写，
      所以这里**如实拒绝**并给一句人话（不偷偷挑一个别的槽，那会让用户以为挂上了）。
    """
    if int(turret_id) <= 0:
        return HardpointWrite(message="先挑一座炮塔 / 挂架再挂上去")
    target = view.first_free_slot if slot is None else int(slot)
    if target < 0:
        return HardpointWrite(message=NO_FREE_SLOT_MESSAGE, turret_id=int(turret_id))
    if not is_valid_slot(target):
        return HardpointWrite(message=f"没有第 {target} 个挂载槽（只有 0..{SLOT_COUNT - 1}）")
    cell = view.cell_at(target)
    if cell is None:  # pragma: no cover - 21 格恒在，上面守过槽号
        return HardpointWrite(message=f"没有第 {target} 个挂载槽")
    if cell.unclaimed:
        return HardpointWrite(
            message=(
                f"槽 {target} 不属于任何改装槽（要改的是 Options.Turret{target}Id，"
                "而这个槽没有任何选项在用它）—— 先给它所属的那个改装槽加一条选项"
            ),
            slot=target,
            turret_id=int(turret_id),
        )
    if not cell.free:
        return HardpointWrite(
            message=f"槽 {target} 上已经挂着东西了 —— 先 ✕ 掉它再挂",
            slot=target,
            turret_id=int(turret_id),
        )
    if cell.owner_option_id <= 0:  # pragma: no cover - unclaimed 那条已经挡过
        return HardpointWrite(
            message=f"槽 {target} 的值不是任何改装槽放的 —— 这里改不了它", slot=target
        )
    was_empty = "空挂架" if cell.occupied else "空槽"
    return HardpointWrite(
        writes=((cell.owner_option_id, slot_column(target), int(turret_id)),),
        message=(
            f"已把炮塔 #{int(turret_id)} 挂到槽 {target}（原来是{was_empty} · "
            f"Options #{cell.owner_option_id}）"
        ),
        slot=target,
        turret_id=int(turret_id),
    )


def clear_writes(data: CardData, view: HardpointView, slot: int) -> HardpointWrite:
    """「✕ 清掉这个槽的挂载」：把 `Options.TurretNId` 写 0。

    ❗ **不是删 Turrets 模板行**（口径 1/7：只有增删，"删的是覆盖那一格"）——
    这里清的是这条**改装选项**上的一个格子，炮塔本身还在库里、别的单位照用。

    已经是 0 的空槽：`writes` 空 + 一句"本来就是空的"（不留白、也不假写一笔）。
    空挂架（`Turret Empty`）清得掉：写 0 之后那一格变成"归某个改装槽管的空槽"，
    仍然是「空」，视觉上不多不少。
    """
    if not is_valid_slot(slot):
        return HardpointWrite(message=f"没有第 {int(slot)} 个挂载槽（只有 0..{SLOT_COUNT - 1}）")
    cell = view.cell_at(slot)
    if cell is None:  # pragma: no cover - 21 格恒在，上面守过槽号
        return HardpointWrite(message=f"没有第 {int(slot)} 个挂载槽", slot=int(slot))
    if not cell.occupied:
        return HardpointWrite(message=f"槽 {cell.slot} 本来就是空的", slot=cell.slot)
    if cell.owner_option_id <= 0:
        return HardpointWrite(
            message=(
                f"槽 {cell.slot} 的值不是任何改装槽放的 —— 这里改不了它"
                "（要改的是它在 Options 里的那一格，而那一行不在本单位的改装槽里）"
            ),
            slot=cell.slot,
        )
    name = data.name_of(TURRETS_TABLE, cell.turret_id)
    return HardpointWrite(
        writes=((cell.owner_option_id, slot_column(cell.slot), 0),),
        message=(
            f"已清掉槽 {cell.slot} 的挂载：{name}（Turrets #{cell.turret_id}）"
            f"—— 只改了 Options #{cell.owner_option_id} 的那一格，炮塔模板行没动"
        ),
        slot=cell.slot,
        turret_id=cell.turret_id,
    )


__all__ = [
    "AMMUNITIONS_TABLE",
    "EMPTY_TEXT",
    "MODIFICATIONS_TABLE",
    "NO_FREE_SLOT_MESSAGE",
    "OPTIONS_TABLE",
    "SELECTION_COLUMN",
    "SLOT_COUNT",
    "TURRETS_TABLE",
    "UNCLAIMED_NOTE",
    "WEAPONS_TABLE",
    "WEAPON_AMMO_TABLE",
    "HardpointCell",
    "HardpointLoad",
    "HardpointView",
    "HardpointWrite",
    "SlotPick",
    "clear_writes",
    "hardpoint_view",
    "is_valid_slot",
    "place_writes",
    "selected_picks",
    "slot_column",
]
