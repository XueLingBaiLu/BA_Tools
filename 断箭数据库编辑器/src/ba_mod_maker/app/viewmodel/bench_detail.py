"""编成台右栏的**详情**视图：武器 / 弹药本身长什么样（零 Qt）。

口径
----
设计稿（docs/交接-2026-10-01-单位编成台设计.md §0/§1 规则 1）说「右边永远是刚点的那一行的全部列」。
用户 2026-10-01 实机反馈补了**另一面**：

> 「我还要能看到武器、弹药的具体信息，可以在右上角加个按钮，这样就切换自如了。」

所以右栏有两种视图，右上角一颗按钮来回切：

| 视图 | 画什么 | 谁需要它 |
|---|---|---|
| 列 | 那一行的**全部原始列**（改数据时看的就是它） | 想改值 / 想知道这一行的 Id |
| 详情 | 那一行指向的**模板行**（武器 / 弹药 / 装甲 / 机动 / 能力 / 传感器） | 游戏信息卡那几行 |

取数**不自己算**：行全部来自 `unit_card.weapon_detail_rows` / `ammo_detail_rows` ——
与游戏那张卡同一套公式、同一份出处（那两个函数的 docstring 里挂着 ISIL 行号）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ba_mod_maker.app.viewmodel.row_icon import RowMark, row_icon
from ba_mod_maker.app.viewmodel.table_labels import table_emoji
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.core.edit.cells import row_cells
from ba_mod_maker.core.edit.scope import RowRef

#: 详情视图的两种来源。
DETAIL_WEAPON = "weapon"
DETAIL_AMMO = "ammo"

#: 炮塔那种「关联行 -> 模板行」：`TurretUnits.TurretId -> Turrets`。
#: 用户 2026-10-02：「既然我能看到子炮塔，那我为什么不能改？」—— 点炮塔行时右栏的
#: **列**面只有 TurretUnits 那 4 列，而定义「挂在谁下面」的 `ParentTurretId` 长在
#: `Turrets` 那一行上，所以那一行必须能开出来改（与武器 / 弹药 / 属性同一条路）。
_T_TURRET_UNITS = "TurretUnits"
_T_TURRETS = "Turrets"

#: 哪张表的行指向武器（值是列名；`Weapons` 自己那一行用 Id 当武器 Id）。
_WEAPON_OF: dict[str, str] = {
    "SquadWeapons": "WeaponId",
    "TurretWeapons": "WeaponId",
    "WeaponAmmunitions": "WeaponId",
}

_T_WEAPONS = "Weapons"
_T_AMMO = "Ammunitions"
_T_WA = "WeaponAmmunitions"

#: 详情来源第三种：**属性行**（装甲 / 机动 / 能力 / 传感器）指向的模板行。
#: 用户 2026-10-02：「我希望点击装甲、机动、能力、传感器时，能像修改武器、弹药之类的，
#: 修改其中的详细信息」—— 那些「详细信息」（装甲值 / 生命值 / 前进速度 / 地面视野…）
#: 长在**模板行**上；关联行只有 (Id, UnitId, 外键) 三列，光看它什么也改不了。
DETAIL_STAT = "stat"

#: 关联表 -> (外键列, 模板表, 人话类别)。
#: ❗ 与 viewmodel/bench_slots.py 的 StatKind 同一份口径（那边管中栏取数，这边管右栏详情）；
#:   两处都只写「哪张表指向哪张表」这一件事，别在这里另发明一套取数。
_STAT_OF: dict[str, tuple[str, str, str]] = {
    "UnitArmors": ("ArmorId", "Armors", "装甲"),
    "UnitPropulsions": ("MobilityId", "Mobility", "机动"),
    "UnitAbilities": ("AbilityId", "Abilities", "能力"),
    "SensorUnits": ("SensorId", "Sensors", "传感器"),
}


@dataclass(frozen=True, slots=True)
class DetailLine:
    """详情里的一行：左中文名 + 右值（+ 可选的灰字提示）。

    `ref` = **这一行对应哪一格**（core/edit/refs.CellRef）。
    有 ref 的行**右栏可以就地改**；没有 ref 的行是**算出来的**（游戏信息卡那几行：
    「瞄准时间」是 AimTimeMin/AimTimeMax 两列合并换算的），只能看 ——
    要改就改它下面同名的原始列（同一组里就摆着）。

    用户 2026-10-01 原话：「右边的数据我要都能改啊，我不能改我看着干嘛？」
    """

    label: str
    value: str
    hint: str = ""
    ref: object | None = None
    #: 这一行的值是不是**指向另一行的外键**；是就给出那一行的 Id（0 = 不是 / 取不到）。
    #: 右栏画在值**右边**那一小段弱化小字（用户口径 2026-10-01：
    #: 「所有出现诸如单位、武器、弹药、炮塔 ID 的时候，都在右边显示其 ID」）。
    ref_id: int = 0

    @property
    def editable(self) -> bool:
        """这一行能不能就地改（算出来的行不能）。"""
        return self.ref is not None


@dataclass(frozen=True, slots=True)
class DetailGroup:
    """一组详情（一把武器一组、一种弹一组）。"""

    title: str
    rows: tuple[DetailLine, ...]
    #: 图标位：这一组画的是**哪一行**（真图 + 没图时的表 emoji 兜底）。
    #: 取法与中栏那些行**同一份**（`row_icon`：族名 / 键列的唯一出处是
    #: `core/assets/families.py::ROW_ICON_SOURCES`）。None = 这一组没有图标位。
    mark: RowMark | None = None

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True, slots=True)
class BenchDetail:
    """右栏「详情」视图的全部内容。"""

    kind: str
    title: str
    subtitle: str
    groups: tuple[DetailGroup, ...]

    @property
    def rows(self) -> tuple[DetailLine, ...]:
        """展平后的所有行（计数与用例用）。"""
        out: list[DetailLine] = []
        for group in self.groups:
            out.extend(group.rows)
        return tuple(out)

    @property
    def has_content(self) -> bool:
        return any(group.rows for group in self.groups)


#: ❗ 这里**只画全列**，不画「卡片行」。
#: 先说为什么要加卡片行：用户 2026-10-01「武器的词条有这么多，难道我就只看这么点吗？」
#: 于是把 unit_card 那几行（游戏信息卡口径）摆进去；随后用户又指出
#: 「你既然都显示全部词条了，**就不要显示这一脚只读信息了**」——
#: 卡片行是**算出来的、只读的**（「有效射程」= 四列射程合并换算），
#: 而全列既能看又能改，留着它纯属占地方。
#: 结论：**只留全列**（占位列也留，与设计稿块 2 的口径一致）。
COLUMN_ROWS_HINT = "这一列的原始值"


def referenced_id(cell) -> int:
    """这一格指向的是哪一行（外键的原始 Id）；不是外键 / 取不到 / 0 都给 0。

    判据用 `FieldCell.foreign`（词典说"这一列是外键"），**不是**看列名里有没有 "ID" ——
    列名会漂，而"这一列是不是外键"是数据本身。`0` 在外键里是"不使用"，那也不显示。
    主键那一行同样给 0：它的值本身就是 Id，再补一个 `#Id` 是废话。

    只有一份实现：右栏的**列**视图与**详情**视图都调它。
    """
    if not getattr(cell, "foreign", None):
        return 0
    raw = getattr(cell, "raw", None)
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return 0


#: 这几列的值**可能是本地化键**（实测 Options.UIName：2046 行非空、1578 条像键、
#: 1578/1578 都翻得出来）—— 显示前先翻一下，翻不出来就原样（那些列里也有明文）。
LOCALIZED_COLUMNS: frozenset[str] = frozenset({"UIName", "HUDName", "UIDescription"})


def _column_lines(
    instance, table: str, row_id: int, *, skip: frozenset[str], localize=None
) -> tuple[DetailLine, ...]:
    """这一行的**全部列**（中文名 + 值），按表里的列序，一个不删。

    `skip` = 卡片行已经给过的中文名（能对上就不重复画）。
    FieldCell 已经把外键换成名字、枚举换成中文、空值写成「（空）」——
    这里不重新格式化，免得出现"两种写法"。
    """
    if instance is None:
        return ()
    try:
        cells = row_cells(instance, RowRef(str(table), int(row_id)))
    except Exception:  # noqa: BLE001 - 表/行没了就当没有列可画，别把右栏整块炸掉
        return ()
    out: list[DetailLine] = []
    for cell in cells:
        label = str(getattr(cell, "cn", "") or getattr(cell, "column", ""))
        if not label or label in skip:
            continue
        text = str(getattr(cell, "text", "") or "")
        # ❗ 用户 2026-10-02：「像这样的 Custom_Option_40xS8，也要进行本地化显示」——
        #    UIName 那一族列里存的是**本地化键**，FieldCell 只把它当普通文本，
        #    所以右栏一直在显示键名。这里按列名做一次解析（翻不出来照旧用原文）。
        column = str(getattr(cell, "column", "") or "")
        raw = getattr(cell, "raw", None)
        if localize is not None and column in LOCALIZED_COLUMNS and isinstance(raw, str) and raw:
            text = localize(raw, text)
        out.append(
            DetailLine(
                label=label,
                value=text,
                hint=f"{table}.{getattr(cell, 'column', '')} · {COLUMN_ROWS_HINT}",
                # ❗ 带上格子引用 = 右栏那一行**可以就地改**（用户口径：「我要都能改」）。
                ref=getattr(cell, "ref", None),
                ref_id=referenced_id(cell),
            )
        )
    return tuple(out)


def _weapon_id_of(data: CardData, table: str, row_id: int) -> int:
    """这一行指向的武器 Id（0 = 没有 / 认不出）。"""
    if table == _T_WEAPONS:
        return int(row_id)
    column = _WEAPON_OF.get(table)
    if column is None:
        return 0
    rows = data.t(table)
    row = rows.by_id(int(row_id))
    return rows.i(row, column) if row is not None else 0


def _ammo_id_of(data: CardData, table: str, row_id: int) -> int:
    """这一行指向的弹药 Id（0 = 没有）。"""
    if table == _T_AMMO:
        return int(row_id)
    if table != _T_WA:
        return 0
    rows = data.t(_T_WA)
    row = rows.by_id(int(row_id))
    return rows.i(row, "AmmunitionId") if row is not None else 0


def _turret_of(data: CardData, table: str, row_id: int) -> tuple[str, int] | None:
    """炮塔关联行 -> 它指向的 `Turrets` 那一行（不是 `TurretUnits` 行就给 None）。

    `TurretUnits` 只有 Id / UnitId / TurretId / Order 四列；炮塔的名字 / 模型 /
    **父炮塔** / 三个角度 / 转速全在 `Turrets` 上（10 列）。
    """
    if str(table) != _T_TURRET_UNITS or not data.has_table(_T_TURRET_UNITS):
        return None
    rows = data.t(_T_TURRET_UNITS)
    row = rows.by_id(int(row_id))
    if row is None:
        return None
    turret_id = rows.i(row, "TurretId")
    if turret_id <= 0 or data.t(_T_TURRETS).by_id(turret_id) is None:
        return None
    return (_T_TURRETS, turret_id)


def _stat_of(data: CardData, table: str, row_id: int) -> tuple[str, int, str] | None:
    """这一行是哪一类属性行、它指向哪个模板行（不是那四类就给 None）。

    认不出这一行 / 外键是 0 / 模板行已经不在了 -> None（右栏那颗切换钮据此禁用，
    不许留一个点不动的按钮 —— 与武器那几类同一条口径）。
    """
    spec = _STAT_OF.get(str(table))
    if spec is None:
        return None
    column, target, category = spec
    if not data.has_table(str(table)) or not data.has_table(target):
        return None
    rows = data.t(str(table))
    row = rows.by_id(int(row_id))
    if row is None:
        return None
    link_id = rows.i(row, column)
    if link_id <= 0 or data.t(target).by_id(link_id) is None:
        return None
    return (target, link_id, category)


def _mark_for(instance, table: str, row_id: int) -> RowMark | None:
    """这一组的**图标位**：真图 + 没真图时的表 emoji 兜底（`RowMark` 的口径）。

    用户口径（2026-10-02）：「你给这个标题加个图标，一眼就能看出是什么武器」。

    ❗ 与中栏那些行**同一份出处**：真图走 `row_icon(instance, 表, 行)`（族名与键列只在
    `core/assets/families.py` 里登记一次，界面不许自己拼）；没真图才退回**表 emoji** ——
    装甲 / 机动 / 能力 / 传感器这四张模板表本来就没有图标列（`row_icon` 给 None），
    给个 🛡 / 🚗 / ✨ / 📡 也比空白强。两样都没有才 None（图标位整块藏起来，不留空框）。
    """
    if instance is None or int(row_id) <= 0:
        return None
    return RowMark(icon=row_icon(instance, str(table), int(row_id)), emoji=table_emoji(str(table)))


def detail_for(
    data: CardData, unit_id: int, table: str, row_id: int, *, instance=None
) -> BenchDetail | None:
    """这一行有没有「详情」可看；没有就返回 None（右栏那颗切换钮会因此**藏起来**）。

    认得出详情的行有**三族**：

    - **武器 / 弹药**：Weapons / SquadWeapons / TurretWeapons / WeaponAmmunitions /
      Ammunitions（弹种自己那一行）；
    - **属性行**（2026-10-02 加）：UnitArmors / UnitPropulsions / UnitAbilities / SensorUnits
      —— 详情 = 它指向的 Armors / Mobility / Abilities / Sensors 那一行；
    - **炮塔关联行**（2026-10-02 加）：TurretUnits —— 详情 = 它指向的 Turrets 那一行
      （`ParentTurretId` 就长在那里：用户「既然我能看到子炮塔，那我为什么不能改？」）。

    **只给一个组**：点弹药就是弹药、点武器就是武器、点装甲就是那套装甲。

    每一组里行的顺序 = **先卡片行（游戏口径），再这一行的全部列**（40 / 67 列一个不删）。
    `instance` 传进来才画得出全列（要它去查词典中文名与外键名）；不传就只有卡片行。
    """
    if data is None or int(unit_id) < 0:
        return None
    turret = _turret_of(data, table, row_id)
    if turret is not None:
        target, turret_id = turret
        rows = _column_lines(
            instance,
            target,
            turret_id,
            skip=frozenset(),
            localize=data.display_name,
        )
        if not rows:
            return None
        name = data.name_of(target, turret_id)
        return BenchDetail(
            kind=DETAIL_STAT,
            title=name,
            subtitle=f"{table} #{row_id} · 详情（它指向的炮塔那一行 · 全部列都能改）",
            groups=(
                DetailGroup(
                    title=f"炮塔 {name}（{target} #{turret_id}）",
                    rows=rows,
                    mark=_mark_for(instance, target, turret_id),
                ),
            ),
        )
    stat = _stat_of(data, table, row_id)
    if stat is not None:
        target, link_id, category = stat
        rows = _column_lines(
            instance,
            target,
            link_id,
            skip=frozenset(),
            localize=data.display_name,
        )
        if not rows:
            return None
        name = data.name_of(target, link_id)
        return BenchDetail(
            kind=DETAIL_STAT,
            title=name,
            subtitle=f"{table} #{row_id} · 详情（它指向的模板行 · 全部列都能改）",
            groups=(
                DetailGroup(
                    title=f"{category} {name}（{target} #{link_id}）",
                    rows=rows,
                    mark=_mark_for(instance, target, link_id),
                ),
            ),
        )
    weapon_id = _weapon_id_of(data, table, row_id)
    ammo_id = _ammo_id_of(data, table, row_id)
    if not weapon_id and not ammo_id:
        return None

    weapon = data.weapon(weapon_id) if weapon_id else None
    ammo = data.ammo(ammo_id) if ammo_id else None
    groups: list[DetailGroup] = []

    def ammo_group() -> DetailGroup | None:
        if ammo is None:
            return None
        rows = _column_lines(
            instance,
            _T_AMMO,
            ammo_id,
            skip=frozenset(),
            localize=data.display_name,
        )
        if not rows:
            return None
        name = data.name_of(_T_AMMO, ammo_id)
        return DetailGroup(
            title=f"弹药 {name}（Ammunitions #{ammo_id}）",
            rows=rows,
            mark=_mark_for(instance, _T_AMMO, ammo_id),
        )

    def weapon_group() -> DetailGroup | None:
        if weapon is None:
            return None
        rows = _column_lines(
            instance,
            _T_WEAPONS,
            weapon_id,
            skip=frozenset(),
            localize=data.display_name,
        )
        if not rows:
            return None
        name = data.name_of(_T_WEAPONS, weapon_id)
        return DetailGroup(
            title=f"武器 {name}（Weapons #{weapon_id}）",
            rows=rows,
            mark=_mark_for(instance, _T_WEAPONS, weapon_id),
        )

    # ❗ **只画你点的那个东西**：点弹药行就给弹药、点武器行就给武器。
    #    用户 2026-10-01：「我都选了弹药，怎么还有武器的信息？」——
    #    原来是"两个都给"（点携弹行时弹药在前、武器紧随其后），那一截纯属噪音：
    #    要看枪点武器行就是了。
    wants_ammo = table in (_T_AMMO, _T_WA) and ammo is not None
    order = (ammo_group, weapon_group) if wants_ammo else (weapon_group, ammo_group)
    for builder in order:
        group = builder()
        if group is not None and not group.is_empty:
            groups.append(group)
            break

    if not groups:
        return None

    kind = DETAIL_AMMO if ammo_id else DETAIL_WEAPON
    if weapon is not None and ammo is not None:
        title = f"{data.name_of(_T_WEAPONS, weapon_id)} × {data.name_of(_T_AMMO, ammo_id)}"
    elif weapon is not None:
        title = data.name_of(_T_WEAPONS, weapon_id)
    else:
        title = data.name_of(_T_AMMO, ammo_id)
    subtitle = f"{table} #{row_id} · 详情（它指向的模板行 · 全部列都能改）"
    return BenchDetail(kind=kind, title=title, subtitle=subtitle, groups=tuple(groups))
