"""四个专用页的公共契约（步兵班 / 武器 / 弹药 / 改装选项共用）。

设计 §4：**同一套版式** —— 左边「这一行的上下文」，右边「这一行的全部词条」，
点开一格还是那个就地编辑浮层。所以四页共用一个基类，各页只写「左边画什么」。

## 谁提供数据

四页都不直接碰 Instance / Changeset，而是通过 EditorContext：
它由 UnitEditorScreen 建一次，负责

- 寻址（core/edit/cells.py::unit_cells）、共享度（core/edit/sharing.py）、
  「这一行指着谁」；
- 写值 / 加行 / 删行 / 另存为新模板 —— 全部走同一个 Changeset；
- **撤销 / 重做栈**（Ctrl+Z 撤销上一条、Ctrl+Y 重做上一条撤销），四页与主屏共用一个栈。

## 页与主屏的分工

| 谁 | 管什么 |
|---|---|
| UnitEditorScreen | 顶栏、面包屑、保存、撤销、页栈、立绘上的校验角标 |
| EditorPage（本文件） | 左栏内容 + 右栏词条 + 就地编辑 |
| 四个子类 | 只覆写 build_left()：把这一行的上下文画出来 |
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.entries_panel import table_emoji, table_title
from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.bench_detail import LOCALIZED_COLUMNS
from ba_mod_maker.app.viewmodel.row_icon import RowMark, row_icon
from ba_mod_maker.app.viewmodel.unit_card import CardData, new_member_values, squad_loadout
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_entries import RowEntriesPanel
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.cells import FieldCell, cell_at, display_value, row_cells, unit_cells
from ba_mod_maker.core.edit.changeset import (
    HISTORY_LIMIT,
    Change,
    Changeset,
    RowChange,
    RowId,
)
from ba_mod_maker.core.edit.clone import add_row as core_add_row
from ba_mod_maker.core.edit.clone import clone_unit as core_clone_unit
from ba_mod_maker.core.edit.clone import (
    next_row_id,
    save_as_new_template,
)
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.refs import CellRef, foreign_table
from ba_mod_maker.core.edit.scope import (
    UNIT_COLUMN,
    RowRef,
    UnitScope,
    entry_names_in_order,
    owner_link_ref,
)
from ba_mod_maker.core.edit.scope import unit_scope as core_unit_scope
from ba_mod_maker.core.edit.sharing import SharingIndex
from ba_mod_maker.core.errors import BaModMakerError, TableError

#: 撤销栈的一条：列级改动 / 行级改动（新增·删除）/ **一批**行级改动（一次动作删几行）。
UndoEntry = tuple[str, "Change | RowChange | tuple[RowChange, ...] | tuple[UndoEntry, ...]"]


def _trim(stack: list[UndoEntry]) -> None:
    """栈上限：超了丢**最旧**那一条。

    上限只有一份（`core/edit/changeset.py::HISTORY_LIMIT = 100`）—— 用户口径是
    「Ctrl+Z / Ctrl+Y，栈上限 100」，界面这一层不再自己写一个数。
    """
    if len(stack) > HISTORY_LIMIT:
        del stack[: len(stack) - HISTORY_LIMIT]


def _batch_text(batch: tuple[RowChange, ...]) -> str:
    """一批行级改动的状态栏说法：**按表分组**写（`Options #101、#102、Modifications #41`）。

    ❗ 从前写的是 `batch[0].table` ＋ 全部 Id —— 一批只动一张表时读着是对的，
    一旦跨表（编成台「删掉整个槽」= Modifications ＋ 它的 Options）就变成
    "Options #101、#102、#41"，把 41 也说成了 Options 的行。单表那一份**逐字不变**。
    """
    return "、".join(
        f"{table} " + "、".join(f"#{row.row_id}" for row in batch if row.table == table)
        for table in dict.fromkeys(row.table for row in batch)
    )


def _group_entries(
    row_change: RowChange,
    link_change: Change | None,
    extra: Sequence[RowChange] = (),
) -> tuple[UndoEntry, ...]:
    """把"一次动作里的几条记录"按原顺序打包成**一条**撤销记录的内容。

    ❗ 用户 2026-10-02：「做吧」（做"一次 Ctrl+Z 把整次复制收回来"）——
    复制模板 = 插一行 ＋ 改一格（关联行指过去）＋ 可能还有连带复制的几行（炮塔的武器）。
    从前各记各的，用户按一次 Ctrl+Z 只回来一半；现在它们同进同出。
    """
    entries: list[UndoEntry] = [("row", row_change)]
    if link_change is not None:
        entries.append(("change", link_change))
    entries.extend(("row", change) for change in extra)
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class CardBlock:
    """卡上某一块的增删口径（界面重做定稿 块 7）。

    ❗ **只有带 UnitId 的关联表在这里** —— 删的是**关联行**，模板行（Armors /
    Weapons / Abilities…）一行不动（设计 §块 7 第 3 条）。
    「属性（N）」不出现在这张表里：它是从装甲 / 机动 / 传感器算出来的汇总，
    没有对应的行，所以它不能增删。
    """

    key: str
    label: str
    #: 关联表（本单位拥有的那一张：带 UnitId）。
    table: str
    #: 指向模板行的外键列。空串 = 这一行自己就是它（Modifications 这种「槽」）。
    column: str
    #: 「＋」的候选来自哪张模板表。空串 = 不用选，直接插一行（改装槽）。
    target: str = ""
    #: 第二条外键（武器 × 弹种：WeaponId + AmmunitionId）。
    second_column: str = ""
    #: 新行里除 UnitId / 外键之外的固定值。
    extra: tuple[tuple[str, Any], ...] = ()
    #: 「＋」不是"插一行外键关联行"，而是**照本单位现有的一行整行复制**（块 11 的班组成员：
    #: 一行 = 一个兵）。True 时 column / target / second_column 都不用 —— 值从被复制那一行搬，
    #: 落点见 EditorContext.add_squad_member。
    copy_member: bool = False


CARD_BLOCKS: dict[str, CardBlock] = {
    "armor": CardBlock("armor", "🛡 装甲", "UnitArmors", "ArmorId", "Armors"),
    "mobility": CardBlock("mobility", "🚗 机动", "UnitPropulsions", "MobilityId", "Mobility"),
    "sensors": CardBlock("sensors", "📡 传感器", "SensorUnits", "SensorId", "Sensors"),
    "transports": CardBlock(
        "transports",
        "🚚 运输",
        "TransportAvailabilities",
        "SpecializationAvailabilityId",
        "SpecializationAvailabilities",
    ),
    "abilities": CardBlock("abilities", "✨ 能力", "UnitAbilities", "AbilityId", "Abilities"),
    # 加武器 = 插一行 WeaponAmmunitions（武器 × 弹种），**不是**插 Weapons 模板行。
    # Quantity 给 1：新挂上去的枪至少有一发，具体数字在右栏那一格里改。
    "weapons": CardBlock(
        "weapons",
        "🔫 武器",
        "WeaponAmmunitions",
        "WeaponId",
        "Weapons",
        "AmmunitionId",
        (("Quantity", 1), ("Order", 0)),
    ),
    # 改装槽：这一行**自己就是**那个槽（Modifications 带 UnitId），没有模板表可选。
    "modifications": CardBlock(
        "modifications", "🔧 改装槽", "Modifications", "", "", "", (("Type", 0),)
    ),
    # 班组成员（界面重做定稿 块 11）：一行 = 一个兵，落在 SquadMembers（带 UnitId）。
    # ❗ 它**没有模板表可挑**（其他块的 target 都是一张模板表）：SquadMembers 一行不是
    #    模板，别的单位的兵带着的枪未必在本单位的 SquadWeapons 里 —— 挑进来当场违反
    #    「步兵武器单向包含」那条 error 级不变量。所以 copy_member=True：
    #    「＋」的候选是本单位的现有成员，挑一名照他复制（见 add_squad_member）。
    "squad": CardBlock("squad", "👥 班组成员", "SquadMembers", "", "", "", (), True),
    # 关系树这一轮补出来的三个块（长在树的分组节点上，见 editor/relation_tree_panel.py）：
    # 炮塔的候选名单（TurretUnits）、班用武器（SquadWeapons）、专精可用性。
    # ❗ 都是**纯新增**：上面那八块的名字与写法一个字没动（卡片那一轮还在用）。
    "turrets": CardBlock(
        "turrets", "🗼 炮塔", "TurretUnits", "TurretId", "Turrets", "", (("Order", 0),)
    ),
    "squad_weapons": CardBlock(
        "squad_weapons", "🔫 班用武器", "SquadWeapons", "WeaponId", "Weapons", "", (("Order", 0),)
    ),
    "specs": CardBlock(
        "specs",
        "🎖 专精可用性",
        "SpecializationAvailabilities",
        "SpecializationId",
        "Specializations",
    ),
}

#: "读不到这个值"的哨兵（与任何真实值都不相等 —— object() 每次都是新的）。
_MISSING = object()


def row_title(instance, row_ref) -> str:
    """一行叫什么（面包屑与右栏抬头都用它）。

    口径与别处一致：显示 **Name / HUDName**（core/edit/foreign.display_name），
    查不到名字就写「表名 #Id」—— 不编假名字。
    """
    name = display_name(instance, row_ref.table, row_ref.row_id)
    if not name or name == f"{row_ref.table} {row_ref.row_id}":
        return f"{table_title(row_ref.table)} #{row_ref.row_id}"
    return name


class EditorContext:
    """编辑服务：四页与主屏共用的一份状态与动作。"""

    def __init__(
        self,
        instance: Instance,
        unit_id: int,
        changeset: Changeset,
        data: CardData,
        sharing: SharingIndex,
        on_changed: Callable[[], None],
    ) -> None:
        self.instance = instance
        self.unit_id = unit_id
        self.changeset = changeset
        self.data = data
        self.sharing = sharing
        self._on_changed = on_changed
        self._undo: list[UndoEntry] = []
        #: **撤销过的记录**（最近一次在末尾）—— Ctrl+Y 从末尾取，后进先出。
        #: 与 core/edit/changeset.py 的重做栈同一个口径（新的写操作清空、上限 100），
        #: 但它是**这一层**的：界面的撤销走"重写值"而不是 revert()，见 undo_last 的说明。
        self._redo: list[UndoEntry] = []
        #: 卡片算出来的 link_ref（比"本单位哪一行指着它"更准，见 owner_link_ref 的说明）。
        self._links: dict[CellRef, CellRef] = {}
        self._scope: UnitScope | None = None
        self._cells: tuple[FieldCell, ...] = ()
        self.rebuild()

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #

    def rebuild(self) -> None:
        """结构变了（插/删行）之后重算 scope 与格子。"""
        self._scope = core_unit_scope(self.instance, self.unit_id)
        self._cells = unit_cells(
            self.instance, self.unit_id, sharing=self.sharing, scope=self._scope
        )

    @property
    def scope(self) -> UnitScope:
        assert self._scope is not None
        return self._scope

    @property
    def cells(self) -> tuple[FieldCell, ...]:
        return self._cells

    def cell(self, ref: CellRef) -> FieldCell | None:
        """**现取**（不查缓存）—— 缓存会在改完之后给出旧值。"""
        return cell_at(self.instance, ref, sharing=self.sharing)

    def cells_of_row(self, table: str, row_id: int) -> tuple[FieldCell, ...]:
        return row_cells(self.instance, RowRef(table, row_id), sharing=self.sharing)

    def row_ids(self, table: str) -> tuple[int, ...]:
        return self.scope.row_ids(table)

    def set_links(self, links: dict[CellRef, CellRef]) -> None:
        self._links = dict(links)

    def register_link(self, ref: CellRef, link: CellRef | None) -> None:
        if link is not None:
            self._links[ref] = link

    def link_ref(self, ref: CellRef) -> CellRef | None:
        """「另存为新模板」要重指的那一行。

        先看卡片算出来的（它知道选项覆盖），再退回"本单位哪一行指着它"。
        """
        known = self._links.get(ref)
        if known is not None:
            return known
        return owner_link_ref(self.instance, self.unit_id, ref.table, ref.row_id)

    def is_dirty(self, ref: CellRef) -> bool:
        return self.changeset.is_dirty(ref.table, ref.row_id, ref.column)

    def value_text(self, table: str, column: str, value: Any) -> str:
        """值的显示文本。**本地化键先翻成人话**。

        用户 2026-10-02：「像这样的 Custom_Option_40xS8，也要进行本地化显示」——
        `Options.UIName` 那一族列里存的是本地化键（实测 2046 行非空、1578 条像键、
        **1578/1578 都翻得出来**），FieldCell 只把它当普通文本，所以右栏/专用页一直显示键名。
        ⛔ 只改**显示**：就地编辑器仍然从 `context.cell(ref)` 取**原始值**，写回去的也还是键。
        """
        text = display_value(self.instance, table, column, value)
        if column in LOCALIZED_COLUMNS and isinstance(value, str) and value:
            return self.data.display_name(value, text)
        return text

    def table_order(self) -> tuple[str, ...]:
        return entry_names_in_order(self.instance, self.unit_id)

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #

    def set_value(self, ref: CellRef, value: Any) -> Change | None:
        self._redo.clear()  # 新的写操作 -> 重做栈作废（同 Changeset 那条口径）
        change = self.changeset.set_value(ref.table, ref.row_id, ref.column, value)
        if change is not None:
            self._remember(("change", change))
        else:
            self._drop_undone(ref)
        self._on_changed()
        return change

    def insert_row(self, table: str, values: dict) -> RowId:
        self._redo.clear()
        row_id = core_add_row(self.changeset, table, values)
        self._remember(("row", self.changeset.row_changes[-1]))
        # ❗ 插完**必须丢缓存**：Rows 的 where / by_id / where2 都缓存过这张表，
        #    不丢的话树与卡片照**旧名单**重画 —— 实机就是"点了 ＋ 加一名成员，
        #    树上没出现，只好再点一下"（用户口径 2026-10-01：「为什么我要点两下
        #    才能增加成员？」）。删除那条路（remove_rows）上一轮补过同一句，
        #    插入这条漏了 —— 同一个坑第二次踩。
        self.data.invalidate(table)
        self.rebuild()
        self._on_changed()
        return row_id

    def delete_row(self, table: str, row_id: RowId) -> None:
        self._redo.clear()
        change = self.changeset.delete_row(table, row_id)
        self._remember(("row", change))
        # ❗ 与 insert_row / remove_rows 同一条：行没了就必须丢缓存，否则树与卡片
        #    照旧名单画（班组成员页那个 ✕ 走的就是这一步 —— squad_page._remove_member）。
        self.data.invalidate(table)
        self.rebuild()
        self._on_changed()

    # ------------------------------------------------------------------ #
    # 卡上每一块的增删（界面重做定稿 块 7）
    # ------------------------------------------------------------------ #

    def association_row(self, ref: CellRef | None) -> RowRef | None:
        """这一格所在的**行**是不是本单位的关联行？是就返回那一行，否则 None。

        判据只有一条：那张表有 UnitId 列，且这一行的 UnitId == 本单位。于是

        - 模板行（Armors / Weapons / Abilities…）没有 UnitId -> None；
        - 被改装选项覆盖的那一格（Options#504.ArmorId）是**选项行**，
          不是"这台车挂着哪一行" -> 也不给删（删它会连累同一个槽下的配置）。

        结果：「✕」只长在那些"删掉 = 这台车不带这一样"的行上。
        """
        if ref is None or ref.table not in self.instance.table_names:
            return None
        table = self.instance.table(ref.table)
        if UNIT_COLUMN not in table.columns:
            return None
        try:
            row = self.changeset.row_of(ref.table, ref.row_id)
        except TableError:
            return None
        if table.rows[row][table.column_index(UNIT_COLUMN)] != self.unit_id:
            return None
        return RowRef(ref.table, ref.row_id)

    def association_rows(self, refs: Sequence[CellRef]) -> tuple[RowRef, ...]:
        """把卡上那几格收成「要删的那几行」（去重、保序）。

        一行都不合格就抛 TableError —— 界面上宁可不画那个 ✕，也不删错东西。
        """
        out: list[RowRef] = []
        for ref in refs or ():
            row = self.association_row(ref)
            if row is not None and row not in out:
                out.append(row)
        if not out:
            raise TableError(
                "这一样不是本单位的关联行，删不了",
                hint="模板行与「选项覆盖」的那一格不在这里删：它们别人也在用",
                table=next((ref.table for ref in refs or ()), "?"),
            )
        return tuple(out)

    def add_block_row(
        self, block: str, target_id: int = 0, second_id: int = 0
    ) -> tuple[str, RowId]:
        """卡上某一块的「＋」：往关联表插一行。返回 (表名, 新行 Id)。

        新行 Id 走 core/edit/clone.py::next_row_id（现有最大 +1，并把**本会话删过的
        Id**也算上）—— 这条口径设计 §5 第 1 条已经定了，不在这里另发明一套。
        ❗ 写路径一律要把 changeset 传进去，见下面那一句的注释。
        """
        spec = CARD_BLOCKS.get(block)
        if spec is None:
            raise TableError(
                f"卡上没有「{block}」这一块",
                hint="块名见 app/screens/editor/pages/base.py::CARD_BLOCKS",
            )
        if spec.copy_member:
            # 班组成员：「＋」不是"插一行外键关联行"，而是**照本单位现有的一名成员整行复制**
            # （target_id = 照哪一名；0 = 照最后一名）。落点见 add_squad_member。
            return spec.table, self.add_squad_member(int(target_id))
        values: dict[str, Any] = {"UnitId": self.unit_id}
        if spec.column:
            values[spec.column] = int(target_id)
            if spec.second_column:
                values[spec.second_column] = int(second_id)
        else:
            # 改装槽：这一行自己就是那个槽 —— 起个能读的名字（空名字在卡上只剩 #Id）。
            # ❗ 必须把 changeset 一起传进去：只传 table 的话，"删掉最大 Id 那一行"
            #    之后 next_row_id 会**捡回刚删的 Id**，而那条删除还能被 Ctrl+Z 放回来 ——
            #    于是两行数据抢一个 Id（core-fix 在 clone.py 里修的就是这个）。
            fresh_id = next_row_id(self.changeset.table(spec.table), self.changeset)
            values["Name"] = f"新改装槽 #{fresh_id}"
            values["UIName"] = ""
            values["Order"] = len(self.row_ids(spec.table))
        values.update(dict(spec.extra))
        row_id = self.insert_row(spec.table, values)
        return spec.table, row_id

    def add_squad_member(self, copy_from: int = 0) -> RowId:
        """卡上「班组成员」的「＋」：照本单位现有的一名成员复制一行 SquadMembers。

        为什么是"照本单位的人复制"、而不是"从别的表挑一行挂上来"：

        - 一行 SquadMembers = **一个兵**，它不是模板行（没有可挑的模板表）；
        - 规则引擎有一条 error 级不变量：SquadMembers 用到的每一门枪都必须在
          **本单位**的 SquadWeapons 里。照本单位现有成员复制，这条天然成立；
          从全库那 1134 行里随便挑一名别的单位的兵，挑进来当场违反
          （实机后果：HUD 里那件武器不显示）。候选的同一口径见
          app/screens/editor/editable_card.py::_squad_choices。

        参数 copy_from = 照哪一名（SquadMembers 行 Id）；0 / 找不到 / 班里一个人都没有
        时退回"照最后一名"或最小可用的一行（只填 UnitId）—— 后两种都由
        viewmodel.unit_card.new_member_values 收口，这里不另写一套。

        新行 Id 走 core/edit/clone.py::next_row_id（core_add_row 里取），不另发明。
        """
        # 读之前先丢缓存：Rows.where / by_id 都会缓存，刚插过行不丢就会照旧名单复制
        # （同 squad_page._touch_squad_cache 的理由 —— 那份 CardData 与卡片那份不是同一个）。
        self.data.t("SquadMembers").invalidate()
        loadout = squad_loadout(self.data, self.unit_id)
        if loadout is None:
            raise TableError(
                "这个单位不是步兵，没有班组成员这一套",
                hint="载具 / 飞机的人员走炮塔与运输，不是 SquadMembers（Units.Type 不是 Infantry）",
                table="SquadMembers",
            )
        index = next(
            (i for i, member in enumerate(loadout.members) if member.member_id == int(copy_from)),
            -1,
        )
        if index < 0:
            index = len(loadout.members) - 1  # 0 = 照最后一名（新兵跟老兵装具一样）
        values = new_member_values(self.data, loadout, copy_from=index)
        return self.insert_row("SquadMembers", values)

    def remove_rows(self, refs: Sequence[CellRef]) -> tuple[str, tuple[RowId, ...]]:
        """卡上某一项的「✕」：删**关联行**（模板行不动）。返回 (表名, 删掉的行 Id)。

        一次动作删几行（一门枪的 2 行弹种）记成**一条**撤销条目 —— 不然
        「已移除 X · 撤销」要按两次 Ctrl+Z 才回来，而状态栏只说了那一次。
        """
        rows = self.association_rows(refs)
        self._redo.clear()
        changes = tuple(self.changeset.delete_row(row.table, row.row_id) for row in rows)
        self._remember(("rows", changes))
        # ❗ 删完**必须丢缓存**：Rows 的 where / by_id / where2 都缓存过这些行，
        #    不丢的话树与卡片照**旧名单**重画 —— 实机表现（用户口径 2026-10-01）：
        #    删掉第 4 名班组成员，左树里那一栏还在、还数着 6 个人，中栏停在
        #    「（找不到 SquadMembers #1645）· 0 列」。同一个坑插行那次踩过
        #    （见 viewmodel/unit_card.py::Rows.invalidate 的 docstring）。
        self.data.invalidate(rows[0].table)
        self.rebuild()
        self._on_changed()
        return rows[0].table, tuple(row.row_id for row in rows)

    def delete_rows(self, rows: Sequence[tuple[str, RowId]]) -> tuple[RowRef, ...]:
        """一次动作删**多张表的多行**，记成**一条**撤销条目。返回删掉的那几行。

        与 `remove_rows` 的分工：那一份收的是**卡上的 CellRef**、只删"本单位关联行"那一类；
        这一份收的是**已定好的 (表名, 行Id)** —— 调用方（编成台的"删掉整个槽"）自己知道
        要删哪几张表：`Modifications` 那一行 ＋ 级联它下面**全部** `Options`。

        ❗ **一条撤销**（不是每张表一条）：`("rows", changes)` 走 undo_last 那条
        "批量放回去"的路，Ctrl+Z 一次就全回来。⛔ 别在控件里 for 循环调 `delete_row`
        —— 那样 Ctrl+Z 要按 N+1 次，而状态栏只说了一次（`remove_rows` 的同一口径）。

        ❗ 每张动过的表都要 `data.invalidate`（Rows 的 where / by_id / where2 都缓存过），
        否则重画出来还是旧名单 —— `remove_rows` / `insert_row` 上踩过同一个坑。
        """
        if not rows:
            return ()
        self._redo.clear()
        changes = tuple(self.changeset.delete_row(table, row_id) for table, row_id in rows)
        self._remember(("rows", changes))
        for table_name in dict.fromkeys(table for table, _row_id in rows):
            self.data.invalidate(table_name)
        self.rebuild()
        self._on_changed()
        return tuple(RowRef(table, row_id) for table, row_id in rows)

    def options_of_modification(self, modification_id: int) -> int:
        """这个改装槽底下有几条选项行 —— 删槽之前要问的一句。"""
        table = self.instance.table("Options")
        index = table.column_index("ModificationId")
        return sum(1 for row in table.rows if row[index] == modification_id)

    def clone_unit(self, unit_id: int) -> str:
        """**整单位克隆**（core/edit/clone.py::core_clone_unit）—— 一次动作、**一条**撤销。

        用户口径（2026-10-01）：「迫切需要整单位克隆功能（克隆时记得整套克隆，
        比如武器、弹药、炮塔、改装、以及改装里的炮塔指向）」。

        整套搬走的范围与重指规则写在 core/edit/clone.py 的 docstring 里；这里只做三件
        界面侧的事：走同一个变更集、把这一批行记成**一条**撤销（不然要按三十几次 Ctrl+Z）、
        以及把动过的表逐个丢缓存（不丢的话树 / 卡片照旧画老数据）。
        """
        self._redo.clear()
        result = core_clone_unit(self.changeset, unit_id)
        self._remember(("rows", result.rows))
        for table_name in {record.table for record in result.rows}:
            self.data.invalidate(table_name)
        self.rebuild()
        self._on_changed()
        return result.summary()

    def save_as_template(self, ref: CellRef) -> str:
        """另存为新模板。返回一句给状态栏的话。

        ❗ **一次动作 = 一条撤销**（用户 2026-10-02：「做吧」）—— 插模板行 ＋ 把关联行指过去
        打包成**一条** `("group", …)`。从前记两条：用户按一次 Ctrl+Z 只回来一半
        （新模板行还在、本单位那条关联行也还指着它）。
        """
        link = self.link_ref(ref)
        self._redo.clear()
        new_id, row_change, link_change = save_as_new_template(self.changeset, ref, link)
        self._remember(("group", _group_entries(row_change, link_change)))
        self.rebuild()
        self._on_changed()
        return f"已另存为新模板：{ref.table} #{new_id}（{link} 已指过去，别人没动）"

    def clone_row_to(
        self,
        table: str,
        row_id: int,
        link: CellRef,
        *,
        extras: Callable[[int], Sequence[tuple[str, dict]]] | None = None,
    ) -> tuple[int, str]:
        """复制 (table, row_id) 一行、把 link 指过去；返回 (新行 Id, 一句给状态栏的话)。

        与 save_as_template 是同一件事，只是**显式收下 link**：
        界面上"克隆一份"是从某一块（装甲/机动/能力/武器…）点出来的，
        它比 scope.owner_link_ref 更准（被改装选项覆盖时 link 是 Options 那一格）。
        """
        value_ref = CellRef(table, row_id, "Id")
        self.register_link(value_ref, link)
        self._redo.clear()
        new_id, row_change, link_change = save_as_new_template(self.changeset, value_ref, link)
        # ❗ 连带一起复制的那几行（炮塔 → 它名下的 TurretWeapons）也进**同一条**撤销记录。
        extra_changes: list[RowChange] = []
        for extra_table, extra_values in extras(new_id) if extras is not None else ():
            # 新行 Id 由 core_add_row 取（与 insert_row 同一条路）；照抄的行别带旧 Id。
            core_add_row(self.changeset, extra_table, dict(extra_values))
            extra_changes.append(self.changeset.row_changes[-1])
            self.data.invalidate(extra_table)
        self._remember(("group", _group_entries(row_change, link_change, extra_changes)))
        self.rebuild()
        self._on_changed()
        return new_id, f"已克隆 {table}#{row_id} → #{new_id}（{link} 已指过去，别人没动）"

    def insert_group(
        self,
        table: str,
        values: dict,
        *,
        extras: Callable[[RowId], Sequence[tuple[str, dict]]] | None = None,
    ) -> RowId:
        """插一行 ＋ **连带插的几行**，记成**一条**撤销条目；返回主行 Id。

        与 `clone_row_to` 的 `extras` 同一形状（新行 Id 由 `core_add_row` 取，连带那几行拿主行 Id
        当参数），区别只有一处：**没有"关联行指过去"那一步** —— 复制一整个改装槽插的是
        `Modifications`（自己带 `UnitId`，不是指向模板的外键）＋ 它名下的 `Options`
        （`ModificationId` 填新槽 Id，见 `viewmodel/bench_slots.py::copy_slot_options`）。

        ❗ **一条撤销**：用户按一次 Ctrl+Z 就该整组回来（口径同 `clone_row_to`）。

        ❗ 每张动过的表都要 `data.invalidate`（Rows 的 where / by_id / where2 都缓存过），
        不然重画出来还是旧名单 —— `insert_row` / `delete_rows` 上踩过同一个坑。
        """
        self._redo.clear()
        main_id = core_add_row(self.changeset, table, dict(values))
        row_change = self.changeset.row_changes[-1]
        self.data.invalidate(table)
        extra_changes: list[RowChange] = []
        for extra_table, extra_values in extras(main_id) if extras is not None else ():
            core_add_row(self.changeset, extra_table, dict(extra_values))
            extra_changes.append(self.changeset.row_changes[-1])
            self.data.invalidate(extra_table)
        self._remember(("group", _group_entries(row_change, None, extra_changes)))
        self.rebuild()
        self._on_changed()
        return main_id

    def undo_last(self) -> str:
        """撤销**上一条**（Ctrl+Z）。返回一句给状态栏的话。

        ❗ 用**公开的写操作**（set_value / insert_row / delete_row）而不是
        Changeset.revert：保存成功会 commit()，账本条目就清空了 —— 那时
        revert 找不到条目、revert_row 还会 KeyError，表现是**保存之后 Ctrl+Z
        全失效**（实测踩到）。"把旧值写回去"这条路不依赖账本，两种状态都对。
        （口径是"改一格只进缓存、顶栏 💾 才落盘"，但 commit 那一下照旧会清条目，
        所以这条路还是不能换成 revert。）

        撤销成功的那一条会进**重做栈**（Ctrl+Y 走 redo_last）。
        """
        while self._undo:
            kind, entry = self._undo.pop()
            if kind == "change":
                change = entry
                assert isinstance(change, Change)
                if self._value_of(change) == change.old:
                    continue  # 已经是旧值了（别处撤过），继续往上找
                self.changeset.set_value(change.table, change.row_id, change.column, change.old)
                self.rebuild()
                self._on_changed()
                self._remember_redo(("change", change))
                return f"已撤销：{change.table}#{change.row_id}.{change.column} 回到 {change.old}"
            if kind == "group":
                # ❗ 一整次"复制模板另存一份"：插的行 ＋ 指过去的关联行（＋ 连带复制的行）
                #    同进同出 —— 用户按**一次** Ctrl+Z 就该全回来（用户 2026-10-02：「做吧」）。
                group = entry
                assert isinstance(group, tuple)
                reverted: list[UndoEntry] = []
                for inner_kind, inner in reversed(group):
                    if inner_kind == "change":
                        change = inner
                        assert isinstance(change, Change)
                        if self._value_of(change) == change.old:
                            continue
                        self.changeset.set_value(
                            change.table, change.row_id, change.column, change.old
                        )
                        self.data.invalidate(change.table)
                        reverted.append(("change", change))
                        continue
                    row_change = inner
                    assert isinstance(row_change, RowChange)
                    if self._apply_row(row_change, forward=False):
                        self.data.invalidate(row_change.table)
                        reverted.append(("row", row_change))
                if not reverted:
                    continue  # 这一组已经是撤销后的样子了，继续往上找
                reverted.reverse()  # 重做要按**原顺序**再来一遍
                self.rebuild()
                self._on_changed()
                self._remember_redo(("group", tuple(reverted)))
                return f"已撤销：这一整次复制（{len(reverted)} 处一起）"
            if kind == "rows":
                # 一次动作删掉的那几行（一门枪的 2 行弹种）**一起**放回去 ——
                # 状态栏只说了一次「撤销」，就该一次撤完。
                batch = entry
                assert isinstance(batch, tuple)
                for row_change in batch:
                    self._apply_row(row_change, forward=False)
                # ❗ 撤销也是"行回来了/没了"：同样要丢缓存（否则界面照旧）。
                for row_change in batch:
                    self.data.invalidate(row_change.table)
                self.rebuild()
                self._on_changed()
                self._remember_redo(("rows", batch))
                return f"已撤销：{_batch_text(batch)} 放回去了"
            row_change = entry
            assert isinstance(row_change, RowChange)
            if not self._apply_row(row_change, forward=False):
                continue  # 那一行已经是撤销后的样子了，继续往上找
            self.data.invalidate(row_change.table)
            self.rebuild()
            self._on_changed()
            self._remember_redo(("row", row_change))
            return f"已撤销：{row_change.summary}"
        return "没有可撤销的改动"

    def redo_last(self) -> str:
        """重做**上一条撤销掉的**（Ctrl+Y）。返回一句给状态栏的话。

        ❗ 为什么不用 `Changeset.redo()`：界面这一层的撤销走的是"把值写回去"
        （见 undo_last），而不是 `Changeset.revert()` —— 后者才会往 Changeset 的
        重做栈里放记录，而且任何一次 `set_value` 都会把它清空。于是从界面按下
        Ctrl+Z 之后，Changeset 的重做栈永远是空的。所以重做栈跟撤销栈一起放在
        这里，两边口径完全对称：新的写操作清空、上限 HISTORY_LIMIT（100）。
        """
        while self._redo:
            entry = self._redo.pop()
            message = self._apply_forward(entry)
            if message is None:
                continue  # 那一条的状态已经不对了（别处动过），继续往上找
            self._undo.append(entry)
            _trim(self._undo)  # 撤销栈满时又压回来一条：同样守住上限
            return message
        return "没有可重做的改动"

    def _apply_forward(self, entry: UndoEntry) -> str | None:
        """把一条撤销过的记录**再做一遍**；状态不对（做不了）返回 None。"""
        kind, payload = entry
        if kind == "change":
            change = payload
            assert isinstance(change, Change)
            if self._value_of(change) == change.new:
                return None  # 已经是新值了（别处改过），跳过
            self.changeset.set_value(change.table, change.row_id, change.column, change.new)
            self.rebuild()
            self._on_changed()
            return f"已重做：{change.table}#{change.row_id}.{change.column} → {change.new}"
        if kind == "group":
            group = payload
            assert isinstance(group, tuple)
            done: list[UndoEntry] = []
            for inner_kind, inner in group:
                if inner_kind == "change":
                    change = inner
                    assert isinstance(change, Change)
                    if self._value_of(change) == change.new:
                        continue
                    self.changeset.set_value(change.table, change.row_id, change.column, change.new)
                    self.data.invalidate(change.table)
                    done.append(("change", change))
                    continue
                row_change = inner
                assert isinstance(row_change, RowChange)
                if self._apply_row(row_change, forward=True):
                    self.data.invalidate(row_change.table)
                    done.append(("row", row_change))
            if not done:
                return None
            self.rebuild()
            self._on_changed()
            return f"已重做：这一整次复制（{len(group)} 处一起）"
        if kind == "rows":
            batch = payload
            assert isinstance(batch, tuple)
            done = False
            for row_change in batch:
                done = self._apply_row(row_change, forward=True) or done
            if not done:
                return None
            for row_change in batch:
                self.data.invalidate(row_change.table)
            self.rebuild()
            self._on_changed()
            return f"已重做：{_batch_text(batch)} 又拿掉了"
        row_change = payload
        assert isinstance(row_change, RowChange)
        if not self._apply_row(row_change, forward=True):
            return None
        self.data.invalidate(row_change.table)
        self.rebuild()
        self._on_changed()
        return f"已重做：{row_change.summary}"

    def _apply_row(self, row_change: RowChange, *, forward: bool) -> bool:
        """行级记录做一遍：`forward=True` 是**重做**，`False` 是**撤销**（逆操作）。

        一张表说清两件事（曾经两种方向各写一遍，改一处忘一处）：

        | 记录 | forward=True（重做） | forward=False（撤销） |
        |---|---|---|
        | 插入 | 再插回来 | 删掉 |
        | 删除 | 再删掉 | 放回来 |

        返回"真的动了数据"。状态已经对了（那一行在/不在）就返回 False，
        由调用方决定是跳过还是继续往上找。
        """
        if row_change.is_insert == forward:
            if self._row_exists(row_change.table, row_change.row_id):
                return False
            self.changeset.insert_row(row_change.table, dict(row_change.values))
            return True
        if not self._row_exists(row_change.table, row_change.row_id):
            return False
        self.changeset.delete_row(row_change.table, row_change.row_id)
        return True

    def _value_of(self, change: Change) -> Any:
        try:
            return self.changeset.value(change.table, change.row_id, change.column)
        except Exception:  # noqa: BLE001 - 那一行可能已经不在了
            return _MISSING

    def _row_exists(self, table: str, row_id: int) -> bool:
        try:
            self.changeset.row_of(table, row_id)
        except Exception:  # noqa: BLE001 - TableError：没有这一行
            return False
        return True

    def _drop_undone(self, ref: CellRef) -> None:
        """改回原值 -> 条目消失了，撤销栈里那一条也就不该再留着。"""
        self._undo = [
            entry
            for entry in self._undo
            if not (
                entry[0] == "change"
                and isinstance(entry[1], Change)
                and (entry[1].table, entry[1].row_id, entry[1].column)
                == (ref.table, ref.row_id, ref.column)
            )
        ]

    def adopt_undo(self, other) -> None:
        """把**别处的撤销栈接过来**（换单位但不丢历史 —— 克隆完直接跳过去那条路）。

        ❗ 撤销条目本身**不绑定单位**（记的是表 + 行 Id + 值，账本还是同一份 changeset），
        所以搬过来就能用：用户 2026-10-02「我克隆单位的时候，希望直接跳转到那个单位」——
        跳过去之后那一笔克隆照样 Ctrl+Z 退得回来。
        ⛔ 重做栈**不搬**（换单位之后"重做"的语义不清楚，直接清掉更安全）。
        """
        self._undo = list(other._undo)  # noqa: SLF001 - 同类之间搬自己的撤销栈，这就是本方法的目的
        self._redo.clear()

    def _remember(self, entry: UndoEntry) -> None:
        """记一条撤销记录：**新的写操作作废重做栈**，栈上限同 Changeset。"""
        self._redo.clear()
        self._undo.append(entry)
        _trim(self._undo)

    def _remember_redo(self, entry: UndoEntry) -> None:
        """一条刚被撤销掉的记录进重做栈（不碰撤销栈 —— 调用方自己管）。"""
        self._redo.append(entry)
        _trim(self._redo)

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)

    @property
    def can_redo(self) -> bool:
        """有没有可以重做的撤销（界面据此决定 Ctrl+Y 亮不亮）。"""
        return bool(self._redo)


class EditorPage(QWidget):
    """专用页的基类：左「上下文」+ 右「全部词条」，四个页只覆写 build_left。"""

    #: 「← 返回单位编辑器」。
    back_requested = Signal()
    #: 点「落在哪」要跳到数据表（参数：表名, 行 Id）。
    open_table_row_requested = Signal(str, int)
    #: 页内改了数据（已由 context 记进变更集；这里只是让外层刷新角标与卡片）。
    changed = Signal()
    #: 右栏换了一行（导航：卡上点一下就走这里）—— 外层据此刷新面包屑。
    focus_changed = Signal()
    #: 用户点了右栏那个「⬅ 回单位」（从某一行回到本单位的 31 条）。
    back_to_unit_requested = Signal()
    #: 右栏那一格里点了「↗ 去看这一行」（参数：表名, 行 Id）。
    open_row_requested = Signal(str, int)
    #: 一句给状态栏的话（「换 / 克隆」这类动作说完就走，**不弹窗**）。
    status_message = Signal(str)

    def __init__(
        self,
        title: str,
        subtitle: str,
        parent: QWidget | None = None,
        *,
        header: bool = True,
    ) -> None:
        """
        参数
        ----
        header
            要不要自带一条页头（「← 返回单位编辑器」+ 标题 + 说明）。
            四个专用页要；**主编辑页不要** —— 外层（UnitEditorScreen）已经有
            面包屑那条了，再来一条就是同一句话排两行，白占 ~40px
            （用户原话："这一块纵向排列，很占空间"）。
        """
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self._header = header
        self._context: EditorContext | None = None
        self._entries: RowEntriesPanel | None = None
        #: 唯一的图标缓存（由 UnitEditorScreen.set_icons 传下来）；None = 只用 emoji 兜底。
        self._icon_store = None
        self._left: QWidget | None = None
        #: 已经接上「＋ / ✕」的那个左栏控件（卡片跨刷新是同一个对象，接一次就够）。
        self._wired_left: QWidget | None = None
        #: 右栏现在显示的是哪一行（None = 用子类覆写的 entries_cells）。
        #: ❗ 名字里带 _ref 不是啰嗦：四个专用页上早就有一个 **_focus(table, row_id)
        #: 方法**（切换它们自己的视图），属性跟它撞名会把方法盖掉 ——
        #: 实测症状是点弹种时 TypeError: 'NoneType' object is not callable。
        self._focus_ref: RowRef | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        if header:
            root.addWidget(self._build_header())

        self._body = QHBoxLayout()
        self._body.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        self._body.setSpacing(TOKENS.spacing.lg)
        self._left_host = QWidget()
        self._left_layout = QVBoxLayout(self._left_host)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(TOKENS.spacing.md)
        self._body.addWidget(self._left_host, 1)
        root.addLayout(self._body, 1)

    # ------------------------------------------------------------------ #

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setProperty("role", "editor-header")
        row = QHBoxLayout(bar)
        row.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.xs, TOKENS.spacing.lg, TOKENS.spacing.xs
        )
        row.setSpacing(TOKENS.spacing.md)
        back = QPushButton("⬅ 返回单位编辑器")
        back.setCursor(Qt.PointingHandCursor)
        back.setProperty("variant", "ghost")
        back.clicked.connect(self.back_requested)
        row.addWidget(back)
        title = QLabel(self._title)
        title.setProperty("role", "entries-title")
        row.addWidget(title)
        subtitle = QLabel(self._subtitle)
        subtitle.setProperty("role", "entries-subtitle")
        row.addWidget(subtitle)
        row.addStretch(1)
        return bar

    # ------------------------------------------------------------------ #

    @property
    def context(self) -> EditorContext | None:
        return self._context

    @property
    def entries(self) -> RowEntriesPanel | None:
        return self._entries

    def set_context(self, context: EditorContext) -> None:
        """进入这一页：接上 context，重画左栏与右栏。"""
        self._context = context
        self._rebuild()

    def _rebuild(self) -> None:
        self._rebuild_left_only()
        try:
            self._attach_entries()
        except Exception as exc:  # noqa: BLE001 - 右栏画不出来也要留个说法
            from ba_mod_maker.app import diag

            diag.failure("右栏词条重建失败", exc, page=self._title)

    def refresh(self) -> None:
        """数据变了：重画左栏 + 刷新右栏的词条文字。**不重建右栏控件**。"""
        if self._context is None:
            return
        self._rebuild_left_only()
        if self._entries is not None:
            context = self._context
            self._entries.refresh_values(
                lambda ref: context.cell(ref), lambda ref: context.is_dirty(ref)
            )
            # ❗ 图标位那一份**单独推一次**：刷新契约是「不重建控件」，所以上面只刷了值 ——
            #    而抬头那张图的键可能正是刚改的那一列（`Units.ThumbnailFileName`）。
            #    不推的话 `refresh_icon()` 拿的是面板里那份**旧 mark**，图一直不换
            #    （用户口径 2026-10-01：「我改了的话，就要立即看到效果」）。
            self._entries.set_mark(self.entries_mark())

    def _rebuild_left_only(self) -> None:
        """重画左栏。

        ⚠️ 新左栏**就是旧那个控件**时不许 deleteLater —— 主编辑页的左栏是那张卡，
        它得跨刷新留着（里面还挂着变更集订阅与点击目标）。

        ❗ **同一个控件时不摘不挂**（2026-10-01，AGENTS §0.7）：三栏那一页的左栏是
        那棵关系树、编成台的左栏是那张卡 —— 两个都是"跨刷新同一个对象"，而老写法
        每次都把**可见的**它 hide + setParent(None) + 重挂 + setVisible(True)：
        实测 **21.2ms**（116 个控件、含 99 个行尾按钮宿主；尺子 `_work/probe_vehicle_page.py`），
        而"把一整棵可见的树摘下来再挂回去"本身就是用户看到的那一次闪。
        判据只有一条：`build_left()` 返回的还是原来那个控件 -> 布局、可见性、
        位置一个字节都不用动。

        ❗ **先画新的、再摘旧的**（老顺序有代价）：老写法先摘旧的、再调 `build_left()`，
        它一抛异常左栏就**空着** —— 用户报过两次"我在这里看不到左侧的单位卡片了"，
        下面那段"把原来那个放回去"的补丁就是为它打的。现在旧的留在原地直到新的
        画出来，异常时什么都不用补（traceback 照旧进诊断日志）。
        """
        try:
            fresh = self.build_left()
        except Exception as exc:  # noqa: BLE001 - 绝不能让左栏空着
            from ba_mod_maker.app import diag

            diag.failure("左栏重排失败", exc, page=self._title)
            return  # 旧左栏还挂在布局里，一个字节都不用动
        if fresh is not None and fresh is self._left:
            # 同一个控件：不 hide、不 setParent(None)、不重新 addWidget ——
            # 这就是那 21ms，也是"改一格闪一下"里左栏那一份。
            if self._left_layout.indexOf(fresh) < 0:
                # 兜底自愈：左栏被别处摘走过（历史上"左栏一整块空白"就是这种状态）——
                # 只补挂，不重建。
                self._left_layout.addWidget(fresh, 1)
                fresh.setVisible(True)
            self._wire_left_card(fresh)
            return
        stale: list[QWidget] = []
        while self._left_layout.count():
            item = self._left_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # ❗ 这里**只摘不删**（hide + setParent(None)），删除统一在下面按
                #    previous/fresh 判据做。曾经图省事写成 detach() —— 它带一句
                #    deleteLater()，于是"同一个控件要留着"那条判据被绕过：
                #    主编辑页那张卡第 2 次 rebuild 就被排进删除队列，第 3 次进
                #    编辑器直接 RuntimeError（Internal C++ object already deleted）。
                #    实测复现：open_unit 连开三次，第 3 次炸。
                widget.hide()
                widget.setParent(None)
                stale.append(widget)
        for widget in stale:
            if widget is not fresh:
                widget.deleteLater()
        self._left = fresh
        if fresh is not None:
            self._left_layout.addWidget(fresh, 1)
            # ❗ **同一个控件被"摘下来又放回去"时，Qt 不会自己把它显示回来。**
            #    上面那句 widget.hide() 是**显式隐藏**，靠 addWidget 加回布局并不会
            #    撤销它 —— 主编辑页的左栏**就是**那张卡（build_left() 返回 self.card），
            #    于是：右栏照常、左栏整块空白。用户报过两次（"看不到左侧的单位卡片"），
            #    实测复现：点武器进武器页 -> 回单位 -> card.isVisibleTo(page) == False。
            fresh.setVisible(True)
        self._wire_left_card(fresh)

    # ------------------------------------------------------------------ #
    # 卡上的「＋ / ✕」（界面重做定稿 块 7）
    # ------------------------------------------------------------------ #
    #
    # 卡片只发意图，写盘全在 EditorContext 里 —— 与「换… / 克隆一份」同一条分工。
    # ❗ 接线放在**这一层**（而不是 MainEditorPage.__init__）：左栏是谁只有子类知道，
    #    这一层拿到 fresh 之后按鸭子类型接一次（控件有 add_requested /
    #    remove_requested 两个信号才接），所以以后再加一块也不用改主屏。

    def _wire_left_card(self, widget: QWidget | None) -> None:
        """左栏是「可写卡」时接上增删那两条线（接一次，不重复接）。"""
        if widget is None or widget is self._wired_left:
            return
        add = getattr(widget, "add_requested", None)
        remove = getattr(widget, "remove_requested", None)
        if add is None or remove is None:
            return
        add.connect(self._add_card_row)
        remove.connect(self._remove_card_rows)
        self._wired_left = widget

    def _add_card_row(self, block: str, target_id: int, second_id: int = 0) -> None:
        """卡上某一块的「＋」：插一行关联行。成功写状态栏，**错误才弹窗**。"""
        context = self._context
        if context is None:
            return
        try:
            table, row_id = context.add_block_row(block, target_id, second_id)
        except BaModMakerError as exc:
            QMessageBox.warning(self, "没有加上", str(exc))
            return
        spec = CARD_BLOCKS.get(block)
        label = spec.label if spec is not None else block
        self.status_message.emit(
            f"已加一行：{label} · {self._added_detail(block, target_id, second_id)}"
            f"（{table} #{row_id}）"
        )
        self.changed.emit()

    def _squad_weapon_users(self, refs) -> int:
        """这些 refs 里有班用武器的注册行吗？那门枪还有**几名成员**扛着（删之前调）。

        口径：注册行一没，规则引擎那条 error 级不变量就被违反（成员扛的枪必须在
        本单位的 SquadWeapons 里）—— 所以这句话必须在**删之前**算。
        """
        context = self._context
        if context is None:
            return 0
        ids = {ref.row_id for ref in refs if ref.table == "SquadWeapons"}
        if not ids:
            return 0
        squad = context.data.t("SquadWeapons")
        weapons = {squad.i(row, "WeaponId") for row in squad.all() if squad.i(row, "Id") in ids}
        if not weapons:
            return 0
        members = context.data.t("SquadMembers")
        return sum(
            1
            for row in members.all()
            if members.i(row, "UnitId") == context.unit_id
            and (
                members.i(row, "PrimaryWeaponId") in weapons
                or members.i(row, "SpecialWeaponId") in weapons
            )
        )

    def _added_detail(self, block: str, target_id: int, second_id: int) -> str:
        """状态栏里那一句「加的是什么」—— 一律显示**名字**，不给用户看 Id。"""
        context = self._context
        spec = CARD_BLOCKS.get(block)
        if context is None or spec is None:
            return block
        if spec.second_column:
            ammo = foreign_table(spec.second_column) or spec.second_column
            return (
                f"{display_name(context.instance, spec.target, int(target_id))}"
                f" × {display_name(context.instance, ammo, int(second_id))}"
            )
        if spec.copy_member:
            # 班组成员：状态栏那句话里不给用户看 Id（口径：界面一律显示名字）——
            # 成员行本来就没有名字，所以说"照本单位现有成员复制的一名士兵"。
            return "照本单位现有成员复制的一名士兵"
        if spec.target:
            return display_name(context.instance, spec.target, int(target_id))
        return "本单位的新槽"

    def _remove_card_rows(self, refs, label: str = "") -> None:
        """卡上某一项的「✕」：删关联行，状态栏给一次「已移除 X · 撤销」。"""
        context = self._context
        if context is None:
            return
        # ❗ 删**之前**先看有没有人在用（删完那行就没了，查不出来）——
        #    现在只有一条：班用武器的注册行被删掉时，还有成员扛着这门枪，
        #    那条 error 级不变量（SquadMembers ⊆ SquadWeapons）当场被违反，
        #    实机表现是游戏里那件武器不显示（docs/关系图-步兵链.md §10）。
        still_used = self._squad_weapon_users(refs)
        try:
            table, ids = context.remove_rows(refs)
        except BaModMakerError as exc:
            QMessageBox.warning(self, "没有删掉", str(exc))
            return
        name = label or display_name(context.instance, table, ids[0])
        places = "、".join(f"#{row_id}" for row_id in ids)
        note = (
            f"　⚠ 还有 {still_used} 名成员扛着它 —— 游戏里那件武器会不显示（Ctrl+Z 可撤）"
            if still_used
            else ""
        )
        self.status_message.emit(f"已移除 {name} · 撤销（Ctrl+Z · {table} {places}）{note}")
        # ❗ 删掉的正是中栏正在看的那一行 -> 回「单位」那一行。不回退的话中栏就停在
        #    「（找不到 SquadMembers #1645）· 0 列」上 —— 一行 0 列的空气，用户看到的
        #    是"删了之后界面坏了"（用户口径 2026-10-01 实机截图就是这个状态）。
        focus = self._focus_ref
        if focus is not None and focus.table == table and focus.row_id in ids:
            self.set_focus(self.unit_row_ref())
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # 子类只写这两个
    # ------------------------------------------------------------------ #

    def build_left(self) -> QWidget | None:
        """左边「这一行的上下文」。四页各写各的。"""
        raise NotImplementedError

    def entries_cells(self) -> tuple[FieldCell, ...]:
        """右栏要列哪些格子 —— **永远是「一行」**（界面重做定稿 §1 第 1 条）。

        - 有 focus（卡上点了某一样东西）：那一行的全部列；
        - 没有 focus：用子类覆写的口径（四个专用页各自给"这一行的"）。
        """
        assert self._context is not None
        if self._focus_ref is not None:
            return self._context.cells_of_row(self._focus_ref.table, self._focus_ref.row_id)
        return self._context.cells

    def entries_title(self) -> str:
        """中栏抬头 = **这一行叫什么**（表#Id 放在副标题里）。

        用户口径 2026-10-01：「武器的图标、弹药的图标、改装的图标，都应该在
        标题栏上显示，而并非只有文字」—— 抬头前面挂**这一行所属表**的记号。
        记号只有一份出处：entries_panel.table_emoji（= TABLE_EMOJI 那张登记表），
        不许在这里再写一遍 emoji，也不许在别处硬编码。
        """
        if self._focus_ref is None or self._context is None:
            return "词条"
        return row_title(self._context.instance, self._focus_ref)

    def entries_mark(self) -> RowMark | None:
        """中栏抬头左边那个**图标位**：中栏正在看的那一行的真图 + 没图时的兜底 emoji。

        图标位**只能有一个**（用户口径 2026-10-01：「我不是要 emoji，我是要图标」）：
        有真图画真图、没真图才画 emoji。取图在控件那一侧走 `IconStore.pixmap`。
        """
        focus = self._focus_ref
        if focus is None or self._context is None:
            return None
        return RowMark(
            icon=row_icon(self.instance_of(), focus.table, focus.row_id),
            emoji=table_emoji(focus.table),
        )

    def set_icons(self, store) -> None:
        """接上**唯一**那份图标缓存（UnitEditorScreen.set_icons 往下发）。"""
        self._icon_store = store
        if self._entries is not None:
            self._entries.set_icons(store)

    def refresh_icon(self) -> None:
        """图异步到了：抬头再刷一次（别只画一次 —— 进编辑器时图常常还没解析完）。"""
        if self._entries is not None:
            self._entries.refresh_icon()

    def entries_subtitle(self) -> str:
        """副标题 = 「表#Id · N 列 · 点值就地改」—— 表名那一段前面同样带记号。"""
        if self._focus_ref is None:
            return ""
        table = self._focus_ref.table
        return (
            f"{table_title(table)} {table}#{self._focus_ref.row_id}"
            f" · {len(self.entries_cells())} 列 · 点值就地改"
        )

    def instance_of(self):
        """当前实例（没有 context 时是 None）—— 页面画标题要用。"""
        return self._context.instance if self._context is not None else None

    def entries_summary(self) -> str:
        return f"{len(self.entries_cells())} 列"

    def entries_blocks(self) -> tuple[QWidget, ...]:
        """中栏词条**下面**追加的块（默认一块都没有）。

        ❗ 「这一行的列」和「这一行的上下文」是两回事：弹药（一把枪用哪些弹、
        各多少发）属于后者 —— 它是 WeaponAmmunitions 里**别的行**，不是 Weapons
        这一行的列。所以它走 blocks，不进 entries_cells()（进去会把列数与
        "40 列"那个计数也改错）。挂载点在 RowEntriesPanel.set_cells(blocks=...)。
        """
        return ()

    # ------------------------------------------------------------------ #
    # 换 / 克隆：右栏这一行的两把操作
    # ------------------------------------------------------------------ #

    def focus_row_ref(self) -> RowRef | None:
        """右栏现在这一行。主编辑页用 _focus_ref；专用页可以覆写成自己的焦点。"""
        return self._focus_ref

    def focus_link(self) -> CellRef | None:
        """**本单位哪一格**指着这一行 —— 没有它就没有"换/克隆"。

        先问 context.link_ref（卡片算出来的更准：被改装选项覆盖时它知道是 Options
        那一格），再退到 scope.owner_link_ref（任何带 UnitId 的表里指向它的那一列）。
        """
        focus = self.focus_row_ref()
        if focus is None or self._context is None:
            return None
        return self._context.link_ref(CellRef(focus.table, focus.row_id, "Id"))

    def swap_link(self, table: str, new_id: int, link: CellRef) -> None:
        """「换」：把 link 那一格改成另一个模板行的 Id。"""
        if self._context is None:
            return
        registered = self.register_weapon_if_missing(link, new_id)
        self._context.set_value(link, new_id)
        self.status_message.emit(f"已换成 {table}#{new_id}（{link} 已指过去）{registered}")

    def clone_link(self, table: str, row_id: int, link: CellRef) -> None:
        """「克隆一份」：复制这一行 -> 本单位指过去 -> 原来那一行还在（别人照用）。"""
        if self._context is None:
            return
        new_id, message = self._context.clone_row_to(table, row_id, link)
        self.status_message.emit(message)

    def swap_focus(self, new_id: int) -> None:
        """「🔁 换…」选好了 -> 指过去，并把右栏切到**新那一行**。"""
        focus = self.focus_row_ref()
        link = self.focus_link()
        if focus is None or link is None:
            return
        self.swap_link(focus.table, new_id, link)
        self.set_focus(RowRef(focus.table, new_id))

    def clone_focus(self) -> None:
        """「📄 克隆一份」-> 复制 -> 指过去 -> 右栏切到副本上。"""
        focus = self.focus_row_ref()
        link = self.focus_link()
        if focus is None or link is None:
            return
        self.clone_link(focus.table, focus.row_id, link)
        new_id = self._context.cell(link).raw if self._context is not None else None
        if isinstance(new_id, int) and new_id > 0:
            self.set_focus(RowRef(focus.table, new_id))

    def entries_back_label(self) -> str | None:
        """右栏右上角那个「⬅ …」。主编辑页从某一行的视角看出去时才有。"""
        if self._focus_ref is None or self._context is None:
            return None
        if (self._focus_ref.table, self._focus_ref.row_id) == ("Units", self._context.unit_id):
            return None
        count = len(self._context.cells_of_row("Units", self._context.unit_id))
        return f"⬅ 回单位（{count} 条）"

    # ------------------------------------------------------------------ #
    # 导航：右栏换成「那一行」
    # ------------------------------------------------------------------ #

    def set_focus(self, row_ref: RowRef | None) -> None:
        """右栏换成那一行（卡上点一下就走这里）。**不动左栏**。"""
        self._focus_ref = row_ref
        self._attach_entries()
        self.focus_changed.emit()

    @property
    def focus(self) -> RowRef | None:
        return self._focus_ref

    def unit_row_ref(self) -> RowRef | None:
        if self._context is None:
            return None
        return RowRef("Units", self._context.unit_id)

    def page_title(self) -> str:
        """这一页自己的抬头（面包屑第一段就是它）—— 外层与用例走这里，别去戳 _title。"""
        return self._title

    def trail_segments(self) -> tuple[str, ...]:
        """这一页在面包屑里占的那几段（外层会往前补「军械库 › 单位名」）。"""
        instance = self.instance_of()
        if self._focus_ref is None or instance is None:
            return (self._title,)
        if self._focus_ref.table == "Units" and self._context is not None:
            # 本单位那一行在面包屑里就叫「单位」（设计稿：军械库 › Stryker RV › 单位）。
            return ("单位",)
        # 尾段（行名那一段）带**该行所属表**的记号，与中栏抬头同一个来源
        # （用户口径 2026-10-01：军械库 › 单位名 › 🔧 这一行的名字）。
        table = self._focus_ref.table
        return (table_title(table), row_title(instance, self._focus_ref))

    # ------------------------------------------------------------------ #
    # 右栏 + 就地编辑（四页共用这一套）
    # ------------------------------------------------------------------ #

    def _attach_entries(self) -> None:
        if self._entries is not None:
            self._body.removeWidget(self._entries)
            detach(self._entries)
            self._entries.deleteLater()
        panel = RowEntriesPanel()
        panel.set_icons(self._icon_store)
        panel.cell_activated.connect(self._open_inline_editor)
        panel.link_activated.connect(self.open_row_requested)
        panel.back_requested.connect(self.back_to_unit_requested)
        # 「克隆一份」：任何一行都行（能力 / 武器 / 弹药 / 炮塔 / 装甲…）。
        # ⛔ 2026-10-19 用户命令删掉「换」（RowEntriesPanel 不再有 swap_requested）。
        panel.clone_requested.connect(self.clone_focus)
        self._entries = panel
        self._body.addWidget(panel, 2)
        panel.set_cells(
            self.entries_cells(),
            title=self.entries_title(),
            subtitle=self.entries_subtitle(),
            summary=self.entries_summary(),
            back_label=self.entries_back_label(),
            instance=self.instance_of(),
            link=self.focus_link(),
            blocks=self.entries_blocks(),
            mark=self.entries_mark(),
        )

    #: 成员身上这两列是"他的枪"：改它们时若班用武器注册表里没有那门枪，就自动补一行
    #: （用户口径 2026-10-01：「我给新成员添加武器时，如果注册表没有，自动给我注册」）。
    MEMBER_WEAPON_COLUMNS = ("PrimaryWeaponId", "SpecialWeaponId")

    def register_weapon_if_missing(self, ref: CellRef, value: Any) -> str:
        """成员换了把枪、注册表里没有 -> 自动插一行 `SquadWeapons`，返回一句给状态栏的话。

        为什么必须补：规则引擎那条 error 级不变量是「SquadMembers 用到的每一门枪都
        必须在同单位的 SquadWeapons 里」（docs/关系图-步兵链.md §3）—— 违反了，
        实机表现是**游戏里那件武器不显示**。用户不该为此自己跑另一处补注册。

        不补的两种情形：① 不是成员的主/副武器列；② 值是 0（这个兵没这件武器）。
        已经注册过的不重复插（唯一键 UnitId + WeaponId）。
        """
        context = self._context
        if context is None or ref.table != "SquadMembers":
            return ""
        if ref.column not in self.MEMBER_WEAPON_COLUMNS:
            return ""
        weapon_id = value if isinstance(value, int) else 0
        if weapon_id <= 0:
            return ""
        squad = context.data.t("SquadWeapons")
        if any(
            squad.i(row, "UnitId") == context.unit_id and squad.i(row, "WeaponId") == weapon_id
            for row in squad.all()
        ):
            return ""
        context.insert_row(
            "SquadWeapons",
            {
                "UnitId": context.unit_id,
                "WeaponId": weapon_id,
                # Order 排到最后：不把用户已经排好的清单打乱。
                "Order": len(context.row_ids("SquadWeapons")),
            },
        )
        name = display_name(self.instance_of(), "Weapons", weapon_id)
        return f"　班用武器注册表里没有 {name} —— 已自动注册一行"

    def _open_inline_editor(self, cell: FieldCell) -> None:
        context = self._context
        if context is None or self._entries is None:
            return
        editor = InlineEditor(cell, instance=context.instance, link_ref=context.link_ref(cell.ref))
        editor.committed.connect(lambda value, ref=cell.ref: self._commit(ref, value))
        editor.cancelled.connect(self._entries.close_editor)
        editor.open_place_requested.connect(self.open_table_row_requested)
        self._entries.open_editor(cell, editor)

    def _commit(self, ref: CellRef, value: Any) -> None:
        assert self._context is not None
        registered = self.register_weapon_if_missing(ref, value)
        self._context.set_value(ref, value)
        if registered:
            self.status_message.emit(f"已改 {ref} → {value}{registered}")
        if self._entries is not None:
            # ❗ 先把**刚改的这一格**就地更新掉，再收浮层：整页刷新已经排到下一轮
            #    （见 unit_editor_screen._schedule_refresh），不补这一下的话，
            #    浮层收起的瞬间那一格还写着旧值 —— 一帧的闪烁。
            row = self._entries.row_for(ref)
            fresh = self._context.cell(ref)
            if row is not None and fresh is not None:
                row.refresh(fresh, dirty=self._context.is_dirty(ref))
            self._entries.close_editor()
        self.changed.emit()

    def close_editor(self) -> bool:
        if self._entries is not None and self._entries.has_editor:
            self._entries.close_editor()
            return True
        return False


@dataclass(frozen=True, slots=True)
class PageSpec:
    """四个专用页的登记项（主屏按它建页 / 打开页）。"""

    key: str
    title: str
    subtitle: str
    factory: Callable[[], EditorPage]


__all__ = [
    "CARD_BLOCKS",
    "CardBlock",
    "EditorContext",
    "EditorPage",
    "PageSpec",
    "UndoEntry",
    "row_title",
]
