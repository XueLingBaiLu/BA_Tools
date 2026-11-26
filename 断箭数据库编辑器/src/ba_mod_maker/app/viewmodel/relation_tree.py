"""单位关系树：把「一个单位牵扯到的每一行」摆成一棵树（**零 Qt**）。

口径（用户 2026-09-30 定，见 docs/设计-界面重做-定稿.md 与 _work/design/overhaul3.png）：

    左：单位关系树 ｜ 中：这一行的全部列 ｜ 右：游戏那张卡（整块只读）

## 分组顺序（用户定，别自己改）

    1 改装槽（放最前 —— 它决定别的块生效哪一套）
    2 炮塔 · 3 弹药 · 4 装甲 · 5 机动 · 6 传感器 · 7 技能
    8 班组成员 / 班用武器（步兵才有）· 9 专精可用性 · 10 运输配对

## 数量写在行尾灰字里（用户 2026-09-29 口径，别自己改）

原来树上有「有 N / 可以 N」两列（tag / tag2），用户口径是**两列整个删掉**
（原话："把「有 *」和「可以 *」去掉"）。数字不另画一列 —— 它并进每一行的
**行尾灰字**（note，肉眼看得见）；tooltip（hint）只说"这一组 / 这一行是什么"。
❗ **别把「有 / 可以」这两个词加回来**：分组的"有/可以"两类语义已经不存在了。

唯一的例外是**弹药的 ×N**（qty）：用户要"一眼看得见拥有多少弹药"，
所以它贴在名字紧后面（在"表名 #Id"之前），不会被省略号吃掉。

## 三条硬口径

1. **ID 全部显示**：`表名 #Id` 写在名字后面（做 mod 要点名到行）；
2. **不提示共用**：这份模型里没有「N 个单位在用」这类字段（用户否过两次）；
3. **没有引用（出）/（入）**：树里只有上面那 10 组，别再加"引用"两组。

## 这一份只算、不画

节点带着**动作线索**（add_block / remove_refs / link），由控件去接按钮 ——
写盘一律走 EditorContext（＋ = add_block_row、🗑 = remove_rows、📄 = clone_row_to），
这里一个字节都不写。
"""

from __future__ import annotations

from dataclasses import dataclass

from ba_mod_maker.app.viewmodel.unit_card import (
    TURRET_SLOTS,
    TYPE_AIRCRAFT,
    TYPE_HELICOPTER,
    TYPE_INFANTRY,
    TYPE_SHIP,
    TYPE_VEHICLE,
    CardData,
    UnitCard,
    build_unit_card,
    candidate_turret_ids,
    option_turret_slots,
    short_names,
    turret_label,
    union_ids,
)
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import UNIT_COLUMN, RowRef

#: 每一组的 key（用例按它找组，别写中文标题）。
GROUP_MODS = "mods"
GROUP_TURRETS = "turrets"
GROUP_AMMO = "ammo"
GROUP_ARMOR = "armor"
GROUP_MOBILITY = "mobility"
GROUP_SENSORS = "sensors"
GROUP_ABILITIES = "abilities"
GROUP_SQUAD = "squad"
GROUP_SPECS = "specs"
GROUP_TRANSPORTS = "transports"

#: 「引用路径」节点的 key 前缀：改装槽选项下面那层槽位行（`mods/{mid}/opt/{oid}/slot/{n}`）。
#: 同一个 (表, 行 Id) 会在树上出现两次 —— 这条是引用路径，组里那条才是规范位置。
#: 见 `RelationTree.find`：程序化切焦点优先落规范位置。
REFERENCE_KEY_PREFIX = "mods/"

#: 十个分组的顺序（用户 2026-09-30 定：改装槽放最前）。
GROUP_ORDER: tuple[str, ...] = (
    GROUP_MODS,
    GROUP_TURRETS,
    GROUP_AMMO,
    GROUP_ARMOR,
    GROUP_MOBILITY,
    GROUP_SENSORS,
    GROUP_ABILITIES,
    GROUP_SQUAD,
    GROUP_SPECS,
    GROUP_TRANSPORTS,
)

#: 每一组画在树上的标题（emoji 只放在有语义的地方）。
GROUP_TITLES: dict[str, str] = {
    GROUP_MODS: "🧩 改装槽",
    GROUP_TURRETS: "🗼 炮塔",
    GROUP_AMMO: "💣 弹药",
    GROUP_ARMOR: "🛡 装甲",
    GROUP_MOBILITY: "🏎 机动",
    GROUP_SENSORS: "📡 传感器",
    GROUP_ABILITIES: "⚡ 技能",
    GROUP_SQUAD: "👥 班组成员 / 班用武器",
    GROUP_SPECS: "🎖 专精可用性",
    GROUP_TRANSPORTS: "🚚 运输配对",
}

#: ❗ 这里原来有 TAG_HAVE / TAG_CAN / TAG_NA 三类标签（绿=有、金=可以）——
#: 用户 2026-09-29 把「有 / 可以」两列整个删了，标签机制一并清掉；数字改进行尾灰字。
#: 单位类型 -> 抬头那个 emoji。
_KIND_EMOJI: dict[int, str] = {
    TYPE_INFANTRY: "🪖",
    TYPE_VEHICLE: "🚙",
    TYPE_HELICOPTER: "🚁",
    TYPE_AIRCRAFT: "✈",
    TYPE_SHIP: "🚢",
}


@dataclass(frozen=True, slots=True)
class TreeItem:
    """树上的一个节点。

    - `kind`：unit（本体）/ group（分组抬头）/ row（一行）/ note（灰字说明，不可选）；
    - `table` + `row_id`：这一行是谁（都在库里真实存在，用例会扫一遍）；空 = 抬头节点；
    - `refs`：这个名字里**提到的所有行**（含行尾内联的那些），用例按它验存在性；
    - `remove_refs`：这一项的「✕」要删的**关联行**（空 = 不给 ✕）；
    - `link`：本单位哪一格指着这一行（「⧉ 克隆一份」要把那一格指过去）；
    - `add_block` / `alt_add_block`：这一组的「＋」走哪个块（见 pages/base.py::CARD_BLOCKS）。
    """

    key: str
    kind: str
    label: str
    table: str = ""
    row_id: int = 0
    #: 贴在名字**紧后面**的数量（现在只有弹药行用：×4）。用户口径："我需要能调武器
    #: 使用的弹药，拥有多少弹药" —— 要一眼看得见，所以它排在"表名 #Id"之前。
    #: ❗ 0 也要写（×0）："这一条弹一发都没有"是有效信息，别按"没有就不画"处理。
    qty: str = ""
    note: str = ""
    #: 悬浮提示（解释这一组 / 这一行**是什么**；数字已经画在行尾灰字里，别重复）。
    hint: str = ""
    refs: tuple[RowRef, ...] = ()
    remove_refs: tuple[CellRef, ...] = ()
    link: CellRef | None = None
    #: ⧉ 要**复制哪一行**（None = 不给 ⧉）。多数节点就是它自己；
    #: 「班用武器」那一条要复制的是它指向的 Weapons 行（复制 SquadWeapons 行没有意义）。
    clone_row: RowRef | None = None
    add_block: str = ""
    alt_add_block: str = ""
    #: 「＋」**第一列预先定好的那一行**（0 = 不预置，照旧从零挑）。
    #: 现在只有武器行用它："给这门枪加一种弹" —— 枪就是这一行，别再叫人挑一遍
    #: （用户 2026-10-01：「左边没有给武器加弹药的选项」）。
    add_first_id: int = 0
    #: ● 标「当前生效」（炮塔的几种配法 / 改装槽里选中的那一项）。
    selected: bool = False
    children: tuple[TreeItem, ...] = ()

    @property
    def has_row(self) -> bool:
        """这一格能不能点开看（抬头与说明行不行）。"""
        return bool(self.table) and self.row_id > 0

    @property
    def can_add(self) -> bool:
        return bool(self.add_block)

    @property
    def can_clone(self) -> bool:
        """「⧉ 克隆一份」：既有"要复制的那一行"、又有"要重指的那一格"才给。

        ❗ 不给克隆的两种：Units（那是另一个单位）与"配对行"（TransportAvailabilities：
        复制一份没有意义 —— 与 LinkedRow.can_clone 同一条口径）。
        """
        if self.clone_row is None or self.link is None:
            return False
        return self.clone_row.table != "Units" and self.link.table != "Units"

    @property
    def can_remove(self) -> bool:
        return bool(self.remove_refs)

    @property
    def row_ref(self) -> RowRef | None:
        return RowRef(self.table, self.row_id) if self.has_row else None

    @property
    def display(self) -> str:
        """控件画在第一列的那串字（名字 + 数量 + 行尾灰字的合成）—— ID 在**右侧单独一列**。"""
        head = f"{self.label} {self.qty}".strip()
        return f"{head} · {self.note}" if self.note else head

    @property
    def id_text(self) -> str:
        """**完整口径**：`表名 #Id` —— 现在只出现在 tooltip 里（右侧那一列是短号）。"""
        return f"{self.table} #{self.row_id}" if self.has_row else ""

    @property
    def id_short(self) -> str:
        """右侧「ID」那一列的字：**只有 `#Id`**，不带表名；抬头 / 说明行留空。

        用户 2026-10-01：「左边红圈里面的 SquadMembers #* 太占空间了，能不能只在
        右边显示 #* 编号？」—— 表名从名字列挪走（挪进 tooltip），ID 自己占最右边一列。
        这一条**推翻了**原来的硬口径「表名 #Id 写在名字后面」（当时怕窄左栏把 ID 挤出
        可视区；现在 ID 单独一列、定宽右对齐，反而更省地方）。
        """
        return f"#{self.row_id}" if self.has_row else ""


@dataclass(frozen=True, slots=True)
class RelationTree:
    """一个单位的整棵树（根 = 本体那一行）。"""

    unit_id: int
    title: str
    subtitle: str
    roots: tuple[TreeItem, ...]

    def flatten(self) -> tuple[TreeItem, ...]:
        """先序展开（用例扫"每个节点的行都真实存在"用它）。"""
        out: list[TreeItem] = []

        def walk(item: TreeItem) -> None:
            out.append(item)
            for child in item.children:
                walk(child)

        for root in self.roots:
            walk(root)
        return tuple(out)

    @property
    def groups(self) -> tuple[TreeItem, ...]:
        """十个分组抬头（顺序就是 GROUP_ORDER）。"""
        return self.roots[0].children if self.roots else ()

    def group(self, key: str) -> TreeItem | None:
        return next((item for item in self.groups if item.key == key), None)

    def find(self, table: str, row_id: int) -> TreeItem | None:
        """按 (表, 行 Id) 找节点 —— 中栏换了行时高亮它。

        ❗ 同一个 (表, 行 Id) 在树上可能出现**不止一次**：改装槽选项下面那层槽位行
        （key = `mods/{mid}/opt/{oid}/slot/{n}`，table 也是 Turrets）与炮塔组里那一行
        （key = `turrets/{id}`）。`mods/` 那条是**引用路径**（"这个选项挂了这座炮塔"），
        组里那条才是**规范位置**（这一行本来属于哪一组）。
        然而 **mods 组排在十个分组的最前**，先序第一个命中的就是引用路径 ——
        实测用户报的「我明明点的是画红圈的炮塔（M2 塔 Turrets#18），为什么展开的
        却是上面的改装件？」就是这么撞上的。

        所以这里**优先返回 key 不以 `mods/` 开头的那个**：程序化切焦点
        （↗ 跳转 / 检索跳转 / 进编辑器 / set_focus）应当落在规范位置。
        一个都没有（两处都在 mods 里）时退回第一个 —— 兜底必须是确定的，不许随机。
        """
        first: TreeItem | None = None

        def walk(item: TreeItem) -> TreeItem | None:
            """先序走一遍，**命中就返回**（不再先把整棵树 flatten 成元组再扫）。"""
            nonlocal first
            if item.table == table and item.row_id == row_id:
                if not item.key.startswith(REFERENCE_KEY_PREFIX):
                    return item
                if first is None:
                    first = item
            for child in item.children:
                found = walk(child)
                if found is not None:
                    return found
            return None

        for root in self.roots:
            found = walk(root)
            if found is not None:
                return found
        return first

    def mentioned_rows(self) -> tuple[RowRef, ...]:
        """树上**提到过**的每一行（节点自己 + 行尾内联的），去重保序。"""
        seen: dict[RowRef, None] = {}
        for item in self.flatten():
            if item.has_row:
                seen.setdefault(RowRef(item.table, item.row_id), None)
            for ref in item.refs:
                seen.setdefault(ref, None)
        return tuple(seen)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _row_exists(instance: Instance, table: str, row_id: int) -> bool:
    if row_id <= 0 or table not in instance.table_names:
        return False
    rows = instance.table(table)
    index = rows.column_index("Id")
    return any(row[index] == row_id for row in rows.rows)


def _own_row_ref(instance: Instance, unit_id: int, link: CellRef | None) -> tuple[CellRef, ...]:
    """这一格所在的**行**是不是本单位的关联行？是就返回删它要用的那一格。

    判据与 EditorContext.association_row 逐字一致：那张表带 UnitId，且这一行的
    UnitId == 本单位。模板行与"选项覆盖"的那一格都不是 —— 宁可不给 ✕。
    """
    if link is None or link.table not in instance.table_names:
        return ()
    table = instance.table(link.table)
    if UNIT_COLUMN not in table.columns:
        return ()
    id_index = table.column_index("Id")
    unit_index = table.column_index(UNIT_COLUMN)
    for row in table.rows:
        if row[id_index] == link.row_id and row[unit_index] == unit_id:
            return (CellRef(link.table, link.row_id, "Id"),)
    return ()


def _note_of(parts: list[str]) -> str:
    return " · ".join(part for part in parts if part)


# 炮塔短名 / 「…塔」/ 去重保序这三把小工具**已经搬到 viewmodel/unit_card.py** ——
# 卡上那条四层链要用的就是同一份（逻辑只有一份）。这里保留旧名字转发，
# 老调用方与本模块内部照旧按 _short_names / _turret_label / _union 取。
_short_names = short_names
_turret_label = turret_label
_union = union_ids


def _kind_emoji(kind: int) -> str:
    return _KIND_EMOJI.get(int(kind), "📦")


# --------------------------------------------------------------------------- #
# 改装选项 -> 炮塔挂载槽（0..20）
# `option_turret_slots` 也搬到 unit_card 了（卡上那 21 个格子要用同一份），
# 上面的 import 把它转发回来 —— 老调用方与用例照旧按 relation_tree 取。


def _option_slot_children(
    data: CardData, modification_id: int, option_id: int
) -> tuple[TreeItem, ...]:
    """选项节点下面那一层：**它挂上的每一个炮塔挂载槽一行**（`槽 N · <炮塔名>`）。

    一行 = 一个槽（哪怕好几行指的是同一座炮塔 —— 那正是"池 + 子集"的常态：
    实测 F-15E 的 `Custom_Slot_Fuselage` 选项 899 有 12 个槽，只涉及 2 座炮塔）。
    每一行都可点：点它 -> 中栏换成 `Turrets` 那一行（ID 照硬口径显示）。
    """
    instance = data.instance
    out: list[TreeItem] = []
    # 同一座炮塔常常占好几个槽（实测 F-15E 的选项 #899：12 个槽只涉及 2 座炮塔）。
    # ❗ `display_name` 每次都**全表扫一遍**找那一行（core/edit/foreign.py:87-98 是 O(行数)），
    #    不为 12 行重复扫 12 遍 —— 这棵树在"改一格"的刷新路径上要重画。
    names: dict[int, str] = {}
    for slot, turret_id in option_turret_slots(data, option_id):
        name = names.get(turret_id)
        if name is None:
            name = display_name(instance, "Turrets", turret_id)
            names[turret_id] = name
        out.append(
            TreeItem(
                key=f"mods/{modification_id}/opt/{option_id}/slot/{slot}",
                kind="row",
                label=f"槽 {slot} · {name}",
                table="Turrets",
                row_id=turret_id,
                hint=(
                    f"这一条选项在炮塔挂载槽 {slot}（Options.{TURRET_SLOTS[slot]}）上挂着 {name}。"
                    "21 个槽是「池 + 选项子集」，不是「替换第 N 个炮塔」——"
                    "同一座炮塔可以同时占好几个槽。"
                ),
                refs=(RowRef("Turrets", turret_id),),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# 十个组
# --------------------------------------------------------------------------- #


def _mods_group(data: CardData, card: UnitCard) -> TreeItem:
    """改装槽（放最前：它决定别的块生效哪一套）。

    槽 = Modifications 一行；槽底下的选项 = Options（槽内互斥，● 是选中那一项，
    口径见 `unit_card.default_option`：第一条 IsDefault ?? 第一条 Order==0）。
    选项再往下一层 = 它**挂上的炮塔挂载槽**（`Options.Turret0Id`…`Turret20Id`）。
    """
    slots: list[TreeItem] = []
    total_choices = 0
    for group in card.options:
        total_choices += len(group.choices)
        choices: list[TreeItem] = []
        for choice in group.choices:
            note = "±0" if choice.cost == 0 else f"+{choice.cost}"
            if choice.replaces_unit_id:
                note = _note_of([note, "换成另一个单位"])
            choices.append(
                TreeItem(
                    key=f"mods/{group.modification_id}/opt/{choice.option_id}",
                    kind="row",
                    label=choice.label,
                    table="Options",
                    row_id=choice.option_id,
                    note=note,
                    selected=choice.is_current,
                    # 选项下面把 21 个炮塔挂载槽**全列出来**（非零的才画，一个都不折叠）。
                    # 选项下面把 21 个炮塔挂载槽**全列出来**（非零的才画，一个都不折叠）。
                    children=_option_slot_children(data, group.modification_id, choice.option_id),
                )
            )
        # 空槽才给 ✕（有选项的槽删掉会把 Options 留成孤儿行，同 editable_card 的口径）。
        remove = (
            ()
            if choices
            else _own_row_ref(
                data.instance, card.unit_id, CellRef("Modifications", group.modification_id, "Id")
            )
        )
        slots.append(
            TreeItem(
                key=f"mods/{group.modification_id}",
                kind="row",
                label=group.label,
                table="Modifications",
                row_id=group.modification_id,
                note=_note_of(
                    [
                        f"当前：{group.current_label}" if group.current_label else "",
                        "±0" if group.current_cost == 0 else f"+{group.current_cost}",
                        "借来的槽" if not group.is_own_slot else "",
                    ]
                ),
                refs=(RowRef("Modifications", group.modification_id),),
                remove_refs=remove,
                children=tuple(choices),
            )
        )
    return TreeItem(
        key=GROUP_MODS,
        kind="group",
        label=GROUP_TITLES[GROUP_MODS],
        note=f"{len(card.options)} 个槽 · {total_choices} 个选项",
        hint="一行 = 一个改装槽；● 是这一槽当前选中的那一项",
        add_block="modifications",
        children=tuple(slots),
    )


def _turret_children(data: CardData, card: UnitCard, active: set[int]) -> tuple[TreeItem, ...]:
    """炮塔的几种配法 + 每一种挂着哪些武器。

    ❗ 候选名单 = TurretUnits（本单位允许装哪些）∪ 改装选项里出现的炮塔
    （Options.Turret0Id 到 Turret20Id，实测 Stryker RV 的 5 种配法都在里面）。
    """
    instance = data.instance
    links = data.t("TurretUnits")
    ordered = sorted(
        (row for row in links.all() if links.i(row, "UnitId") == card.unit_id),
        key=lambda row: (links.i(row, "Order"), links.i(row, "Id")),
    )
    # 候选名单只有一份出处（unit_card.candidate_turret_ids）：卡上那条四层链算短名
    # 用的是同一份 —— 两边各扫一遍，早晚会出现"树上叫 M240、卡上叫 MainTurret…M240"。
    turret_ids = candidate_turret_ids(data, card.unit_id, card.options)

    names = {turret_id: display_name(instance, "Turrets", turret_id) for turret_id in turret_ids}
    shorts = _short_names(names)
    out: list[TreeItem] = []
    for turret_id in turret_ids:
        short = shorts[turret_id]
        weapons: list[TreeItem] = []
        carrying = data.t("WeaponAmmunitions")
        ammo_rows = data.t("Ammunitions")
        for weapon_id in _union(list(data.turret_weapons(turret_id))):
            # ✕ = 删本单位给这门枪挂的**携弹行**（WeaponAmmunitions），模板行不动。
            rows = sorted(
                carrying.where2("UnitId", card.unit_id, "WeaponId", weapon_id),
                key=lambda row: (carrying.i(row, "Order"), carrying.i(row, "AmmunitionId")),
            )
            # ❗ 用户原话（2026-09-29）：「左边的武器下，还是没有显示它拥有的弹药」。
            #    第一版把弹种名塞进**行尾小字**，一长就被省略号吃掉（实测真机：
            #    "M830A1 HEAT ×24 · …" —— 第二种弹根本看不见）。所以改成：
            #    武器下面**一行一种弹**（自己的子节点），名字/发数/ID 都在自己那一行里。
            #    于是它也变成可点的：点一条弹 -> 中栏换成那一条携弹行的 6 列（数量在那儿改）、
            #    🔁 换弹种、🗑 删这一条 —— 用户第一轮要的"调弹药"在左栏也齐了。
            ammo_children: list[TreeItem] = []
            hud_total = 0
            for row in rows:
                ammo_id = carrying.i(row, "AmmunitionId")
                row_id = carrying.i(row, "Id")
                quantity = carrying.i(row, "Quantity")
                template = ammo_rows.by_id(ammo_id)
                multiplier = ammo_rows.i(template, "HUDMultiplier") if template else 1
                hud = quantity * max(1, multiplier)
                hud_total += hud
                ammo_children.append(
                    TreeItem(
                        key=f"turrets/{turret_id}/w/{weapon_id}/a/{row_id}",
                        kind="row",
                        label=display_name(instance, "Ammunitions", ammo_id),
                        table="WeaponAmmunitions",
                        row_id=row_id,
                        note=f"×{hud}",
                        hint=(
                            f"卡片上 {hud} 发 = 格子里的携弹量 {quantity}"
                            f" × Ammunitions.HUDMultiplier {multiplier}"
                            if multiplier != 1
                            else f"{quantity} 发（点这一行可改数量）"
                        ),
                        refs=(
                            RowRef("WeaponAmmunitions", row_id),
                            RowRef("Ammunitions", ammo_id),
                        ),
                        remove_refs=(CellRef("WeaponAmmunitions", row_id, "Id"),),
                        link=CellRef("WeaponAmmunitions", row_id, "AmmunitionId"),
                    )
                )
            weapons.append(
                TreeItem(
                    key=f"turrets/{turret_id}/w/{weapon_id}",
                    kind="row",
                    label=display_name(instance, "Weapons", weapon_id),
                    table="Weapons",
                    row_id=weapon_id,
                    # 「＋」= 给**这一门枪**加一种弹（插一行 WeaponAmmunitions）：
                    # add_first_id 把第一列钉成这杆枪，只留弹种给人挑。
                    add_block="weapons",
                    add_first_id=weapon_id,
                    # 行尾只写"几种弹、共多少发"（**卡片上那个数**，与子行、与「💣 弹药」
                    # 那一组同口径）；每种弹叫什么、多少发，在下面自己的行里。
                    note=(
                        f"{len(ammo_children)} 种弹 · 共 {hud_total} 发" if ammo_children else ""
                    ),
                    hint="展开就能看到这门枪挂的每一种弹（一行一种）",
                    refs=(RowRef("Weapons", weapon_id),)
                    + tuple(RowRef("WeaponAmmunitions", carrying.i(row, "Id")) for row in rows),
                    remove_refs=tuple(
                        CellRef("WeaponAmmunitions", carrying.i(row, "Id"), "Id") for row in rows
                    ),
                    children=tuple(ammo_children),
                )
            )
        link_row = next((row for row in ordered if links.i(row, "TurretId") == turret_id), None)
        refs = [RowRef("Turrets", turret_id)]
        if link_row is not None:
            refs.append(RowRef("TurretUnits", links.i(link_row, "Id")))
        out.append(
            TreeItem(
                key=f"turrets/{turret_id}",
                kind="row",
                label=_turret_label(short),
                table="Turrets",
                row_id=turret_id,
                note=f"{len(weapons)} 件武器" if weapons else "",
                refs=tuple(refs),
                remove_refs=(
                    _own_row_ref(
                        instance,
                        card.unit_id,
                        CellRef("TurretUnits", links.i(link_row, "Id"), "Id"),
                    )
                    if link_row is not None
                    else ()
                ),
                link=(
                    CellRef("TurretUnits", links.i(link_row, "Id"), "TurretId")
                    if link_row is not None
                    else None
                ),
                clone_row=RowRef("Turrets", turret_id) if link_row is not None else None,
                selected=turret_id in active,
                children=tuple(weapons),
            )
        )
    return tuple(out)


def _turrets_group(data: CardData, card: UnitCard) -> TreeItem:
    """炮塔：几种配法（TurretUnits），● 标当前生效的那一种。"""
    if int(card.kind) == TYPE_INFANTRY:
        return TreeItem(
            key=GROUP_TURRETS,
            kind="group",
            label=GROUP_TITLES[GROUP_TURRETS],
            hint="步兵没有炮塔：武器挂在班组成员上（见下面那一组）",
            children=(
                TreeItem(
                    key=f"{GROUP_TURRETS}/note",
                    kind="note",
                    label="步兵没有炮塔：武器挂在班组成员上（见下面那一组）",
                ),
            ),
        )
    active = {turret_id for turret_id in data.card_turrets(card.unit_id) if turret_id > 0}
    children = _turret_children(data, card, active)
    return TreeItem(
        key=GROUP_TURRETS,
        kind="group",
        label=GROUP_TITLES[GROUP_TURRETS],
        note=f"{len(children)} 种配法",
        hint="一行 = 一种配法；● 是当前生效的那一种",
        add_block="turrets",
        children=children,
    )


def _ammo_group(data: CardData, card: UnitCard) -> TreeItem:
    """弹药：本单位实际带着的每一种弹（WeaponAmmunitions 里用到的那些弹种）。

    同一弹种挂在几把枪上只画一条（refs 收全部携弹行）；✕ 删的是**携弹行**
    （WeaponAmmunitions），不是 Ammunitions 模板行。

    ❗ 每一行名字后面都贴着 **×N**（qty 那一格）—— 用户口径："我需要能调武器使用的
    弹药，拥有多少弹药"。N 与卡片上那个数字同口径（Quantity 之和 × HUDMultiplier，
    见 unit_card.AmmoRow.count），0 也照写。
    """
    entries: dict[int, dict] = {}
    for weapon in card.weapons:
        for ammo in weapon.ammo:
            entry = entries.setdefault(
                ammo.ammo_id, {"ammo": ammo, "count": 0, "rows": [], "weapons": []}
            )
            entry["count"] += ammo.count
            entry["weapons"].append(weapon.label)
            for ref in list(ammo.refs) + list(ammo.quantity_refs):
                if ref.row_id not in entry["rows"]:
                    entry["rows"].append(ref.row_id)

    children: list[TreeItem] = []
    for ammo_id, entry in entries.items():
        ammo = entry["ammo"]
        children.append(
            TreeItem(
                key=f"{GROUP_AMMO}/{ammo_id}",
                kind="row",
                label=ammo.label or f"Ammunitions #{ammo_id}",
                table="Ammunitions",
                row_id=ammo_id,
                qty=f"×{entry['count']}",
                note="／".join(dict.fromkeys(entry["weapons"])),
                refs=(RowRef("Ammunitions", ammo_id),),
                remove_refs=tuple(
                    CellRef("WeaponAmmunitions", row_id, "Id") for row_id in entry["rows"]
                ),
            )
        )
    return TreeItem(
        key=GROUP_AMMO,
        kind="group",
        label=GROUP_TITLES[GROUP_AMMO],
        note=f"{len(children)} 种弹",
        hint="一行 = 一种弹；×N = 本单位的携弹量（与卡片同一个口径）；✕ 删的是携弹行",
        add_block="weapons",
        children=tuple(children),
    )


def _linked_group(
    data: CardData, card: UnitCard, *, key: str, rows, block: str, unit: str = "项"
) -> TreeItem:
    """装甲 / 机动 / 传感器三块共用的一小段：一行一个模板行 + ✕ 删本单位的关联行。

    unit 只影响组头行尾那个数量词（"1 项" / "3 条"），别用它改语义。
    """
    instance = data.instance
    children: list[TreeItem] = []
    for row in rows:
        refs = [RowRef(row.table, row.row_id)]
        if row.link is not None and (row.link.table, row.link.row_id) != (
            row.table,
            row.row_id,
        ):
            refs.append(RowRef(row.link.table, row.link.row_id))
        children.append(
            TreeItem(
                key=f"{key}/{row.table}/{row.row_id}",
                kind="row",
                label=row.label,
                table=row.table,
                row_id=row.row_id,
                note=row.note,
                refs=tuple(refs),
                remove_refs=_own_row_ref(instance, card.unit_id, row.link),
                link=row.link,
                clone_row=RowRef(row.table, row.row_id) if row.link is not None else None,
            )
        )
    return TreeItem(
        key=key,
        kind="group",
        label=GROUP_TITLES[key],
        note=f"{len(children)} {unit}",
        add_block=block,
        children=tuple(children),
    )


def _abilities_group(data: CardData, card: UnitCard) -> TreeItem:
    """技能：UnitAbilities 一行一条，点开看 Abilities 那张模板行（51 列）。"""
    instance = data.instance
    rows = data.t("UnitAbilities")
    links: dict[int, list[int]] = {}
    for row in rows.all():
        if rows.i(row, "UnitId") != card.unit_id:
            continue
        ability_id = rows.i(row, "AbilityId")
        if ability_id > 0:
            links.setdefault(ability_id, []).append(rows.i(row, "Id"))
    children: list[TreeItem] = []
    for ability_id, link_ids in links.items():
        exists = _row_exists(instance, "Abilities", ability_id)
        children.append(
            TreeItem(
                key=f"{GROUP_ABILITIES}/{ability_id}",
                kind="row",
                label=display_name(instance, "Abilities", ability_id),
                table="Abilities" if exists else "UnitAbilities",
                row_id=ability_id if exists else link_ids[0],
                refs=tuple([RowRef("Abilities", ability_id)] if exists else [])
                + tuple(RowRef("UnitAbilities", row_id) for row_id in link_ids),
                remove_refs=tuple(CellRef("UnitAbilities", row_id, "Id") for row_id in link_ids),
                link=CellRef("UnitAbilities", link_ids[0], "AbilityId"),
                clone_row=RowRef("Abilities", ability_id) if exists else None,
            )
        )
    return TreeItem(
        key=GROUP_ABILITIES,
        kind="group",
        label=GROUP_TITLES[GROUP_ABILITIES],
        note=f"{len(children)} 条",
        add_block="abilities",
        children=tuple(children),
    )


def _squad_group(data: CardData, card: UnitCard) -> TreeItem:
    """班组成员 / 班用武器（**只有步兵有**）。

    - 成员：SquadMembers 一行 = 一个兵（主武器 / 副武器）；
    - 班用武器：SquadWeapons 一行 = 这一班允许带的枪（Order 在那一行上）。
    """
    if int(card.kind) != TYPE_INFANTRY or card.loadout is None:
        return TreeItem(
            key=GROUP_SQUAD,
            kind="group",
            label=GROUP_TITLES[GROUP_SQUAD],
            # 「非步兵」原来是那一列的灰字。它是**关键**信息（决定这一组为什么是空的），
            # 所以贴着名字写出来；tooltip 里再说一遍"武器挂在哪"。
            note="非步兵",
            hint="非步兵：武器挂在炮塔上（见上面那一组）",
        )
    instance = data.instance
    loadout = card.loadout
    names = {choice.weapon_id: choice.label for choice in loadout.weapon_choices}
    members: list[TreeItem] = [
        TreeItem(
            key=f"{GROUP_SQUAD}/member/{member.member_id}",
            kind="row",
            # 用户 2026-10-01：「第 1 名之类的，能不能就显示 1」—— 只留纯序号，
            # 「第 / 名」两个字在窄左栏里是白占的（这一行本来就按序号排）。
            label=str(member.index),
            table="SquadMembers",
            row_id=member.member_id,
            note=_note_of(
                [
                    names.get(member.primary_weapon_id, f"武器 #{member.primary_weapon_id}")
                    if member.primary_weapon_id
                    else "（空）",
                    names.get(member.special_weapon_id, f"武器 #{member.special_weapon_id}")
                    if member.special_weapon_id
                    else "",
                ]
            ),
            refs=(RowRef("SquadMembers", member.member_id),),
            remove_refs=(CellRef("SquadMembers", member.member_id, "Id"),),
        )
        for member in loadout.members
    ]
    squad_weapons = data.t("SquadWeapons")
    weapons: list[TreeItem] = []
    for row in sorted(
        (row for row in squad_weapons.all() if squad_weapons.i(row, "UnitId") == card.unit_id),
        key=lambda row: (squad_weapons.i(row, "Order"), squad_weapons.i(row, "Id")),
    ):
        row_id = squad_weapons.i(row, "Id")
        weapon_id = squad_weapons.i(row, "WeaponId")
        weapons.append(
            TreeItem(
                key=f"{GROUP_SQUAD}/weapon/{row_id}",
                kind="row",
                label=display_name(instance, "Weapons", weapon_id),
                table="SquadWeapons",
                row_id=row_id,
                # 与炮塔下面那些武器行同一条：给这一门班用武器加一种弹。
                add_block="weapons",
                add_first_id=weapon_id,
                refs=(RowRef("SquadWeapons", row_id), RowRef("Weapons", weapon_id)),
                remove_refs=(CellRef("SquadWeapons", row_id, "Id"),),
                link=CellRef("SquadWeapons", row_id, "WeaponId"),
                # ⧉ 复制的是**这门枪的模板行**（Weapons），不是 SquadWeapons 那一行。
                clone_row=RowRef("Weapons", weapon_id) if weapon_id else None,
            )
        )
    return TreeItem(
        key=GROUP_SQUAD,
        kind="group",
        label=GROUP_TITLES[GROUP_SQUAD],
        note=f"{len(members)} 名成员 · {len(weapons)} 门班用武器",
        hint="一行 = 一个兵 / 一门枪；✕ 删的是本单位的关联行",
        add_block="squad",
        alt_add_block="squad_weapons",
        children=tuple(members + weapons),
    )


def _spec_name(data: CardData, spec_id: int) -> str:
    rows = data.t("Specializations")
    row = rows.by_id(spec_id)
    if row is None:
        return f"Specializations #{spec_id}"
    return rows.s(row, "Name") or f"Specializations #{spec_id}"


def _specs_group(data: CardData, card: UnitCard) -> TreeItem:
    """专精可用性：哪些专精能带这个单位（SpecializationAvailabilities 一行一个专精）。"""
    rows = data.t("SpecializationAvailabilities")
    children: list[TreeItem] = []
    for row in sorted(
        (row for row in rows.all() if rows.i(row, "UnitId") == card.unit_id),
        key=lambda row: rows.i(row, "Id"),
    ):
        row_id = rows.i(row, "Id")
        spec_id = rows.i(row, "SpecializationId")
        children.append(
            TreeItem(
                key=f"{GROUP_SPECS}/{row_id}",
                kind="row",
                label=_spec_name(data, spec_id),
                table="SpecializationAvailabilities",
                row_id=row_id,
                refs=(
                    RowRef("SpecializationAvailabilities", row_id),
                    RowRef("Specializations", spec_id),
                ),
                remove_refs=(CellRef("SpecializationAvailabilities", row_id, "Id"),),
                link=CellRef("SpecializationAvailabilities", row_id, "SpecializationId"),
            )
        )
    return TreeItem(
        key=GROUP_SPECS,
        kind="group",
        label=GROUP_TITLES[GROUP_SPECS],
        note=f"{len(children)} 个专精",
        hint="一行 = 一个专精能带这个单位",
        add_block="specs",
        children=tuple(children),
    )


def _transports_group(data: CardData, card: UnitCard) -> TreeItem:
    """运输配对：谁运谁（TransportAvailabilities 一行 = 运谁 · 在哪个专精）。

    ❗ 这一行的 UnitId 是**载具侧**（实测 227 行 = 载具 214 + 直升机 13），
    所以「＋」只长在载具 / 直升机上（同 editable_card._block_takes_add 的口径）。
    """
    instance = data.instance
    rows = data.t("TransportAvailabilities")
    availabilities = data.t("SpecializationAvailabilities")
    children: list[TreeItem] = []
    for row in sorted(
        (row for row in rows.all() if rows.i(row, "UnitId") == card.unit_id),
        key=lambda row: rows.i(row, "Id"),
    ):
        row_id = rows.i(row, "Id")
        availability_id = rows.i(row, "SpecializationAvailabilityId")
        availability = availabilities.by_id(availability_id)
        passenger = availabilities.i(availability, "UnitId") if availability is not None else 0
        spec_id = (
            availabilities.i(availability, "SpecializationId") if availability is not None else 0
        )
        label = (
            display_name(instance, "Units", passenger)
            if passenger
            else f"专精可用性 #{availability_id}"
        )
        refs = [RowRef("TransportAvailabilities", row_id)]
        if availability_id:
            refs.append(RowRef("SpecializationAvailabilities", availability_id))
        if passenger:
            refs.append(RowRef("Units", passenger))
        children.append(
            TreeItem(
                key=f"{GROUP_TRANSPORTS}/{row_id}",
                kind="row",
                label=label,
                table="TransportAvailabilities",
                row_id=row_id,
                note=_spec_name(data, spec_id) if spec_id else "",
                refs=tuple(refs),
                remove_refs=(CellRef("TransportAvailabilities", row_id, "Id"),),
                link=CellRef("TransportAvailabilities", row_id, "SpecializationAvailabilityId"),
            )
        )
    can_add = int(card.kind) in (TYPE_VEHICLE, TYPE_HELICOPTER)
    return TreeItem(
        key=GROUP_TRANSPORTS,
        kind="group",
        label=GROUP_TITLES[GROUP_TRANSPORTS],
        note=f"{len(children)} 组配对",
        hint="一行 = 运谁 · 在哪个专精",
        add_block="transports" if can_add else "",
        children=tuple(children),
    )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def build_relation_tree(
    source: Instance | CardData, unit_id: int, *, data: CardData | None = None
) -> RelationTree | None:
    """装配一个单位的关系树（单位不存在时返回 None，与 build_unit_card 一致）。"""
    index = (
        data if data is not None else (source if isinstance(source, CardData) else CardData(source))
    )
    card = build_unit_card(index, unit_id)
    if card is None:
        return None
    groups = {
        GROUP_MODS: _mods_group(index, card),
        GROUP_TURRETS: _turrets_group(index, card),
        GROUP_AMMO: _ammo_group(index, card),
        GROUP_ARMOR: _linked_group(
            index, card, key=GROUP_ARMOR, rows=(card.armor,) if card.armor else (), block="armor"
        ),
        GROUP_MOBILITY: _linked_group(
            index,
            card,
            key=GROUP_MOBILITY,
            rows=(card.mobility,) if card.mobility else (),
            block="mobility",
        ),
        GROUP_SENSORS: _linked_group(
            index, card, key=GROUP_SENSORS, rows=card.sensors, block="sensors"
        ),
        GROUP_ABILITIES: _abilities_group(index, card),
        GROUP_SQUAD: _squad_group(index, card),
        GROUP_SPECS: _specs_group(index, card),
        GROUP_TRANSPORTS: _transports_group(index, card),
    }
    root = TreeItem(
        key=f"unit/{unit_id}",
        kind="unit",
        label=f"{_kind_emoji(card.kind)} {card.title}",
        table="Units",
        row_id=unit_id,
        note=card.subtitle,
        refs=(RowRef("Units", unit_id),),
        children=tuple(groups[key] for key in GROUP_ORDER),
    )
    return RelationTree(unit_id=unit_id, title=card.title, subtitle=card.subtitle, roots=(root,))


__all__ = [
    "GROUP_ABILITIES",
    "GROUP_AMMO",
    "GROUP_ARMOR",
    "GROUP_MODS",
    "GROUP_MOBILITY",
    "GROUP_ORDER",
    "GROUP_SENSORS",
    "GROUP_SQUAD",
    "GROUP_SPECS",
    "GROUP_TITLES",
    "GROUP_TRANSPORTS",
    "GROUP_TURRETS",
    "RelationTree",
    "TreeItem",
    "build_relation_tree",
    "option_turret_slots",
]
