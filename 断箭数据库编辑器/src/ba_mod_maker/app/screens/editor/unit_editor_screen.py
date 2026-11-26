"""单位编辑器：第 4 个页（**不进导航栏**，只能双击进 / 返回出）。

设计见 docs/设计-界面重做-定稿.md 与 _work/design/overhaul3.png。范围一句话：

    双击军械库卡片墙上的一个单位 -> 三栏：**左关系树 ｜ 中这一行的全部列 ｜ 右那张卡（只读）**。
    点树上任意一行，中栏换成那一行的全部列；改数据只在中间那栏，动作长在树的节点上。

## 三栏的分工（用户 2026-09-30 定）

| 栏 | 谁 | 干什么 |
|---|---|---|
| 左 | `editor/relation_tree_panel.RelationTreePanel` | 分组钻取；节点行尾 ＋ / ⧉ / ✕ |
| 中 | `widgets/row_entries.RowEntriesPanel` | 刚点的那一行的**全部列**，点一格就地改 |
| 右 | `editor/readonly_card.ReadOnlyCardPanel` | 游戏那张卡的样子，**整块只读** |

- 卡片**不再是导航面**：点卡上任何东西都不跳页、不写库（用户："卡片整块只读……不再是导航"）；
- 「＋ / ✕」仍走 `EditorContext.add_block_row / remove_rows`，「⧉」仍走 `core/edit/clone.py`；
- 旧的四个专用页（武器 / 弹药 / 改装选项 / 步兵班）**保留**，新主屏不用它们而已。

## 三条纪律（与军械库那边一致）

1. **不自己算数**：哪些行属于这个单位（core/edit/scope.py）、这一格落在哪
   （core/edit/cells.py）、树上怎么分组（app/viewmodel/relation_tree.py）全在别处，
   界面只画。
2. **不弹窗**：「完成 / 成功」写状态栏；「没有改动」也写状态栏；
   只有错误才弹（AGENTS §5）。
3. **颜色只从 tokens 来**。

## 撤销

Ctrl+Z 撤销**上一条**（栈在 EditorContext 里，四页共用）。
「没有改动」时反正是空栈，状态栏说一句就行。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from ba_mod_maker.app.screens.editor.ammo_block import AmmoBlock, carries_weapon
from ba_mod_maker.app.screens.editor.bench_page import BenchPage, bench_kind
from ba_mod_maker.app.screens.editor.entries_panel import table_emoji
from ba_mod_maker.app.screens.editor.pages.ammo_page import AmmoPage
from ba_mod_maker.app.screens.editor.pages.base import EditorContext, EditorPage
from ba_mod_maker.app.screens.editor.pages.options_page import OptionsPage, select_slot_option
from ba_mod_maker.app.screens.editor.pages.squad_page import SquadPage
from ba_mod_maker.app.screens.editor.pages.weapon_page import WeaponPage
from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel
from ba_mod_maker.app.screens.editor.relation_tree_panel import RelationTreePanel
from ba_mod_maker.app.shell.edit_session import SAVE_DIR_ENV, EditSession  # noqa: F401 - 旧接缝
from ba_mod_maker.app.theme.icons import shared_icon_cache
from ba_mod_maker.app.viewmodel.row_icon import RowMark, row_icon
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.core.assets.families import ENUM_ICON_SOURCES, ROW_ICON_SOURCES
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.package import policy_for
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef
from ba_mod_maker.core.edit.sharing import build_sharing_index
from ba_mod_maker.core.rules.delta import unit_finding_delta
from ba_mod_maker.core.rules.engine import RuleEngine, ValidationReport
from ba_mod_maker.core.rules.humanize import humanize_ids

#: 主编辑页（关系树｜全列｜只读卡）在页栈里的下标。
PAGE_MAIN = 0

#: 编成台在页栈里的下标 —— **第二个顶层页**，与主编辑页并列、共用同一个 EditorContext。
#: 用户 2026-10-01 口径：双击单位进编成台；关系树退居兜底（顶栏那颗「🌲 关系树」）。
PAGE_BENCH = 1

#: **会影响图标的 (表, 列)** —— 只有改到这几列才值得重取图标（见 _icon_changes）。
#: 出处就是"画哪张图"的那两处单一来源（core/assets/families.py），这里只做并集，不另立一份口径：
#: 整列即键的 4 张表 + 枚举查键的 Modifications + 立绘/缩略图那两条覆盖列。
#: 为什么需要它：从前每次改动都调 _refresh_icons()（中栏抬头 + 面包屑 + 三页全重取），
#: 而用户改的绝大多数是携弹量 / 血量这种**跟图标无关**的列 —— 那是白花的钱。
ICON_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    [(table, column) for table, (_family, column) in ROW_ICON_SOURCES.items()]
    + [(table, source[1]) for table, source in ENUM_ICON_SOURCES.items()]
    + [
        ("Units", "PortraitFileName"),
        ("Options", "ThumbnailOverride"),
        ("Options", "PortraitOverride"),
    ]
)

#: 上面那些列所在表的集合（插/删这几张表的**行**也会换图标）。
ICON_TABLES: frozenset[str] = frozenset(table for table, _column in ICON_COLUMNS)

#: 每种 check **除了 rule["table"] 之外**还会读哪些表 —— 照 core/rules/engine.py 的实现写死。
#: 增量校验的正确性靠它 + 下面那条"增量 == 全量"的守卫用例一起兜（见 _affected_tables）。
_CHECK_EXTRA_TABLES: dict[str, tuple[str, ...]] = {
    # _check_required_per_unit：units = _table(instance, "Units")
    "required_per_unit": ("Units",),
}
#: 跨表不变量（_check_cross_table 那一族）读哪些表**没有声明在规则书里**，
#: 所以归属它的那张表**每次都重跑** —— 宁可多算 5 条，也不能漏报（角标是数据正确性提示）。
_ALWAYS_RERUN_CHECKS: frozenset[str] = frozenset({"cross_table"})

#: 单位历史（「↩ 回上个单位」）的层数上限 —— 超了丢**最旧**那一层。
#: 20 层远超"一天里真的来回跳"的次数，同时把内存钉死在一个常数上。
UNIT_HISTORY_LIMIT = 20

#: （SAVE_DIR_ENV 已经搬到 app/shell/edit_session.py —— 写盘通道只有一份。
#: 这里仍然转出这个名字：老的用例按 unit_editor_screen.SAVE_DIR_ENV 取也认。）


class MainEditorPage(EditorPage):
    """主编辑页：**左 = 关系树 ｜ 中 = 这一行的全部列 ｜ 右 = 游戏那张卡（只读）**。

    用户 2026-09-30 定稿：钻取都在左边那棵树里；中间永远是「刚点的那一行」；
    右边那张卡**整块只读**，点它不跳页、不写库（"卡片整块只读……不再是导航"）。

    进编辑器时中栏是 Units#unit_id 的 31 条（树根那一行被选中）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        # 不自带页头：外层那条面包屑已经写着「军械库 › 单位 › 单位编辑器」，
        # 再来一条同义的就是排两行字、白占 ~40px（用户报的"很占空间"）。
        super().__init__(
            "单位编辑器",
            "左：关系树 · 中：这一行的全部列 · 右：游戏那张卡（只读）",
            parent,
            header=False,
        )
        # 左栏 = 关系树（build_left 返回它）。点任意一行 -> 中栏换成那一行。
        self.tree = RelationTreePanel()
        self.tree.row_activated.connect(self._open_row)
        # 树上那两把操作与「选中这一项」：
        # ⧉ = 复制这一行 + 把本单位指着它的那一格指过去（core/edit/clone.py）；
        # 选中 = 写 Options.IsDefault（槽内互斥，口径在 options_page.select_slot_option）。
        self.tree.clone_requested.connect(self._clone_row)
        # ⛔ 2026-10-19 用户命令删掉「换」：树上不再有「🔁 换一个」这个入口。
        self.tree.select_option_requested.connect(self._select_slot_option)
        self.tree.status_message.connect(self.status_message)
        # ❗「↗ 去看这一行」这条路**一直没接上**：RowEntriesPanel.link_activated 一路发到
        #    EditorPage.open_row_requested，但从 f130c5f（界面重做第一批）起就没有人接它 ——
        #    实机表现是"点了那个小箭头没反应"，中栏武器行外的弹种 / 炮塔那一层也就下不去。
        #    接法：与"在左树上点一行"完全同一条路（换成那一行的全部列 + 树上跟着高亮）。
        #    弹药块里点弹种名也走这条。
        self.open_row_requested.connect(self._open_row)
        # ❗ 树上的「＋ / ✕」由 EditorPage._wire_left_card 按鸭子类型接一次
        #    （控件有 add_requested / remove_requested 就接）—— 这里不重复接，
        #    重复接会让一次点击插两行。
        # 右栏 = 游戏那张卡，只读（没有下拉、没有按钮、点哪都不跳）。
        self.card = ReadOnlyCardPanel()
        # ❗ 卡上「点改装槽那一项 = 选中它」——与树上「选中这一项」**同一条口径**
        #    （options_page.select_slot_option：写 Options.IsDefault，槽内互斥）。
        #    用户 2026-10-01 实机反馈："右边改装槽，我选中的改装槽，它不会高亮"。
        self.card.option_select_requested.connect(self._select_slot_option)
        self.card.set_icons(shared_icon_cache())
        self._body.addWidget(self.card, 1)
        self._findings: tuple = ()
        #: 中栏词条**下面**挂的那些块（现在只有弹药块）—— 数据改了要跟着重画。
        self._blocks: list[QWidget] = []
        #: 现在这批块是**按哪几把枪**建的（(槽名, WeaponId)），由 entries_blocks() 记。
        #: refresh() 拿它判"要不要整套重建" —— 改了主/副武器这个署名就变了。
        self._mounted_slots: tuple[tuple[str, int], ...] = ()

    #: 旧的四个专用页入口（UnitEditorScreen.open_weapon_page / open_ammo_page /
    #: open_options_page）。新主屏不再从卡上发这三个信号（卡片不是导航面了），
    #: 四条线本身保留：那四个页还在，用例与将来的入口照旧能开。
    weapon_requested = Signal(int)
    ammo_requested = Signal(int, int)
    options_requested = Signal(int)
    #: 「跳到一个**单位**」（参数：Units 行 Id）。
    #: ❗ 单独一条而不是在页面里直接调 open_unit：页面不认识外层那个屏幕，
    #:    而"整屏换过去"要重建 context（树 / 卡 / 面包屑 / 撤销栈一起换），
    #:    那件事只有 UnitEditorScreen 做得了 —— 与上面三条老信号同一套分工。
    unit_requested = Signal(int)

    def set_context(self, context: EditorContext) -> None:
        # 进编辑器看的就是**这个单位的 31 条**（用户口径：不是 19 张表的 1554 格）。
        self._focus_ref = RowRef("Units", context.unit_id)
        self.card.set_instance(context.instance)
        # ❗ **不让卡自己订阅变更集**（follow=False）。
        #    订阅回调是**同步**跑在 set_value 里的整块重排（实测 58.9ms，cProfile 量的：
        #    里面在建控件 setParent/addWidget/ensurePolished）——而这一页在编成台上
        #    **用户根本看不见**，等于每改一格都替一页看不见的界面白干 59ms。
        #    改由外层按"这一页是不是正在台上"驱动（见 UnitEditorScreen._refresh_pages）：
        #    在台上才刷，不在台上只标脏、切回来再刷。
        self.card.set_changeset(context.changeset, follow=False)
        self.card.show_unit(context.unit_id)
        self.tree.set_context(context.instance, context.unit_id, context.data)
        # 卡片算出来的 value_ref -> link_ref 交给 context：
        # 「生命值」被选项覆盖时，指着它的是 Options#504.ArmorId 而不是 UnitArmors ——
        # 这条只有卡片算得出来（core/edit/scope.owner_link_ref 是通用退路）。
        context.set_links(
            {
                stat.value_ref: stat.link_ref
                for stat in self.card.card.stats
                if stat.value_ref is not None and stat.link_ref is not None
            }
            if self.card.card is not None
            else {}
        )
        super().set_context(context)
        self.set_findings(self._findings)
        self._sync_tree_selection()

    def set_findings(self, findings: tuple) -> None:
        """立绘上的**唯一**角标：有校验问题才画，没问题什么都不画。"""
        self._findings = tuple(findings or ())
        self.card.set_validation(self._findings)

    @property
    def findings(self) -> tuple:
        """当前的校验角标（编成台那一页也画同一份 —— 两个顶层页同源）。"""
        return self._findings

    def build_left(self) -> QWidget:
        """左栏 = 关系树（跨刷新是**同一个对象**，所以基类那套 hide/show 的判据都成立）。"""
        return self.tree

    def _open_row(self, table: str, row_id: int) -> None:
        """树上某一行的名字被点了 / 中栏那一行点了 ↗ —— 中栏换成那一行的**全部列**。

        ❗ **Units 行是唯一的例外**：那个目标的身份是"另一个单位"，只换中栏不够
        （用户口径 2026-10-01：「我希望跳转到单位的时候，整体都跳转过去」）——
        左树 / 右卡 / 面包屑 / 撤销栈 / 共享度索引会全都停在原来那个单位。
        所以这一条发给外层 unit_requested，由它走 open_unit（整屏换过去）。
        别的表保持原样：它们本来就是"看这一行的 46 列"。
        """
        if table == "Units":
            self.unit_requested.emit(int(row_id))
            return
        self.set_focus(RowRef(table, row_id))

    def _attach_entries(self) -> None:
        """中栏固定插在**左栏与右栏之间**（基类是 addWidget 到末尾，三栏里会排到右边）。"""
        super()._attach_entries()
        if self._entries is not None:
            self._body.removeWidget(self._entries)
            self._body.insertWidget(1, self._entries, 2)

    def entries_blocks(self) -> tuple[QWidget, ...]:
        """中栏补一块「💣 弹药」——当这一行是本单位挂着的一把枪时。

        为什么它不进 `entries_cells()`：弹药落在 **WeaponAmmunitions**
        （一行 = 这把枪 × 一种弹，带 Quantity），是**别的行**，不是 Weapons 这一行的列；
        塞进列里会把"40 列"那个计数也改错。挂载口径见 pages/base.py::entries_blocks。

        用户口径（2026-09-30 实机反馈）：
            "我需要能调武器使用的弹药，拥有多少弹药"
        所以这一块要能：**看**每一把枪用哪些弹各多少发、**改**数量、**换**弹种、
        **加 / 删**一条携弹行。四条动作全部只发意图，写盘仍走 EditorContext。
        """
        context = self._context
        focus = self._focus_ref
        self._blocks = []
        if context is None or focus is None:
            self._mounted_slots = ()
            return ()
        slots = self._ammo_block_slots(context, focus)
        # 记住"这批块是按哪几把枪建的"；refresh() 比它决定重建还是 reload。
        self._mounted_slots = slots
        blocks: list[AmmoBlock] = []
        for slot_label, weapon_id in slots:
            block = AmmoBlock(
                context.instance,
                context.data,
                context.unit_id,
                weapon_id,
                cell_lookup=context.cell,
                slot_label=slot_label,
            )
            # 改数量 = 与中栏词条行同一套就地编辑（浮层挂在那一格上，由块自己开）。
            block.quantity_committed.connect(self._commit_ammo_quantity)
            # 点弹种名 = 去看 Ammunitions 那一行的 67 列。
            block.link_activated.connect(self.open_row_requested)
            # ＋ / 🗑 走与树上、卡片上同一套落点（插一行 / 删关联行）。
            block.add_requested.connect(self._add_weapon_ammo)
            block.remove_requested.connect(self._remove_card_rows)
            blocks.append(block)
        self._blocks = blocks
        return tuple(blocks)

    #: 「💣 弹药」块能挂在哪两张表的行上。
    #:
    #: - `Weapons`：载具 / 炮塔那条路 —— 行 Id 就是 WeaponId；
    #: - `SquadMembers`：**步兵**那条路 —— 每个成员自己的 `PrimaryWeaponId` /
    #:   `SpecialWeaponId`（0 = 没有），主武器与副武器**各一块**。
    #:
    #: ❗ `SquadWeapons` **不是**入口（我第一版做错了）：它只是"这个步兵班注册了
    #:    这件武器"——用户口径 2026-10-01：「SquadWeapons 只是给这个步兵班注册了
    #:    这个武器而已」。真正可独立设置的是**每个成员的主武器 / 副武器，以及它们
    #:    各自的弹药**；弹药按 (UnitId, WeaponId) 存在 `WeaponAmmunitions`
    #:    （docs/关系图-步兵链.md §3/§6）。
    #:
    #:    口径补充（**查库实测**，不是推断）：`WeaponAmmunitions` 只有 6 列
    #:    （Id / UnitId / WeaponId / AmmunitionId / Order / Quantity），**没有成员
    #:    那一列** —— 所以同一个班里两名扛同一把枪的成员**共用**那一行弹药
    #:    （645：4 名扛 AK-74RMO 的成员共用 #3602 = 80 发）。"独立"成立在
    #:    "每人每把枪各走自己那条弹药行"，不是"同枪不同人各存一份"。
    AMMO_BLOCK_TABLES = ("Weapons", "SquadMembers")

    def _ammo_block_slots(
        self, context: EditorContext, focus: RowRef
    ) -> tuple[tuple[str, int], ...]:
        """这一行现在**该挂哪几块**弹药（(槽名, WeaponId)）—— 唯一的"块署名"。

        建块与"要不要整套重建"共用这一份，免得两处各算一遍（算出来不一样就会
        每轮刷新都重建，或者该出现的块永远不出现）。
        只有"本单位真的挂着这把枪"才给块 —— 翻到别的单位用的模板行时，画一块
        "这枪没弹"的红字是噪音（判据见 ammo_block.carries_weapon）。
        """
        return tuple(
            (slot_label, weapon_id)
            for slot_label, weapon_id in self._ammo_slots(context, focus)
            if carries_weapon(context.data, context.unit_id, weapon_id)
        )

    def _ammo_slots(self, context: EditorContext, focus: RowRef) -> tuple[tuple[str, int], ...]:
        """中栏这一行有哪几把枪要配弹药（拿不到就是空元组 —— 不给块）。

        返回 ((标题前缀, WeaponId), ...)：成员那一行给"主武器 X / 副武器 Y"两块，
        武器那一行给一块（前缀空 —— 中栏这一行本身就是那把枪）。
        """
        if focus.table == "Weapons":
            return (("", int(focus.row_id)),)
        if focus.table != "SquadMembers":
            return ()
        out: list[tuple[str, int]] = []
        for column, label in (("PrimaryWeaponId", "主武器"), ("SpecialWeaponId", "副武器")):
            cell = context.cell(CellRef("SquadMembers", focus.row_id, column))
            raw = getattr(cell, "raw", None)
            if not isinstance(raw, int) or raw <= 0:
                continue  # 0 = 这个兵没有副武器（原版大量如此）
            weapon = display_name(context.instance, "Weapons", raw)
            out.append((f"{label} {weapon}", raw))
        return tuple(out)

    def _commit_ammo_quantity(self, ref, value) -> None:
        """弹药块里数量改好了：**只写进变更集**（口径：只进缓存，点💾才写盘）。"""
        context = self._context
        if context is None or ref is None:
            return
        context.set_value(ref, value)
        self.status_message.emit(f"已改 {ref} → {value}（只进缓存，点💾才写盘）")
        self.changed.emit()

    def _add_weapon_ammo(self, weapon_id: int, ammo_id: int) -> None:
        """「＋ 加一种弹」：插一行 WeaponAmmunitions（Quantity 给 1，与块 7 同一口径）。"""
        self._add_card_row("weapons", weapon_id, ammo_id)

    def _swap_weapon_ammo(self, ref, ammo_id: int) -> None:
        """「🔁 换弹种」：改的是那一格的 AmmunitionId（与树上那颗🔁同一件事）。"""
        context = self._context
        if context is None or ref is None:
            return
        context.set_value(ref, int(ammo_id))
        name = display_name(context.instance, "Ammunitions", int(ammo_id))
        self.status_message.emit(f"已换成 {name}（{ref} 已改，只进缓存）")
        self.changed.emit()

    def _select_slot_option(self, modification_id: int, option_id: int) -> None:
        """树上点了改装槽里的一个选项：**选中它**（写 Options.IsDefault，槽内互斥）。

        口径与"写哪几格"都在 `options_page.select_slot_option` 那一份里
        （树节点与「改装选项」页走同一个入口，不抄第二遍）。
        一个字节都没写时（借来的槽 / 已经是它）它也把原因说回来 —— 只写状态栏，不弹窗。
        """
        context = self._context
        if context is None:
            return
        self.status_message.emit(select_slot_option(context, modification_id, option_id))

    def _swap_row(self, table: str, new_id: int, link) -> None:
        """树上那个「🔁 换一个」—— 与中栏表头那颗按钮同一套实现。"""
        if link is None:
            return
        self.swap_link(table, new_id, link)

    def _clone_row(self, table: str, row_id: int, link) -> None:
        """树上那个「⧉ 克隆一份」—— 与中栏表头那颗按钮同一套实现。"""
        if link is None:
            return
        self.clone_link(table, row_id, link)

    def jump_to_finding(self) -> None:
        """跳到**出问题的那一行**（中栏换成它）。

        ❗ 卡片现在是只读的（点它不跳页、也不当导航），所以这条路不再挂在角标上；
        留给状态栏 / 面包屑 / 将来的入口调用（角标本身还画，见 set_findings）。
        """
        for finding in self._findings:
            for row_id in finding.row_ids or ():
                self.set_focus(RowRef(finding.table, row_id))
                return
            if finding.unit_ids:
                self.set_focus(RowRef("Units", finding.unit_ids[0]))
                return

    def set_focus(self, row_ref: RowRef | None) -> None:
        """中栏换成那一行（树 / 面板都走这里），顺带把树上那一条高亮过来。"""
        super().set_focus(row_ref)
        self._sync_tree_selection()

    def _sync_tree_selection(self) -> None:
        """树上高亮中栏正在看的那一行（找不到就不动 —— 不假装选中了）。"""
        focus = self._focus_ref
        if focus is not None:
            self.tree.select_row(focus.table, focus.row_id)

    def refresh(self) -> None:
        """数据改了：重画左栏（树）+ 刷新中栏的词条文字 + 角标。

        ❗ 树的**内容**要跟着重画（插了行 / 删了行 / 换了选项，分组与行数都变了），
        但控件本身跨刷新是同一个对象（别在 _rebuild_left_only 里被删掉）。
        """
        context = self._context
        focus = self._focus_ref
        # ❗ **这一行配的枪换了**（改了 PrimaryWeaponId / SpecialWeaponId —— 就地编辑下拉与
        #    「🔁 换一个」两条路都算）-> 块得**整套重建**，光 reload 不够：
        #    旧的 AmmoBlock 是拿**旧 WeaponId + 旧槽名**建的，reload() 读回来的还是旧那把枪
        #    的弹药行与旧标题（用户口径 2026-10-01：「我希望我在改武器的时候，它下面能立马
        #    显示出我武器的变化，这样我就能直接去给武器添加弹药了」）。
        #    判据 = _ammo_slots 的署名（与建块时**同一份来源**，不另发明一套）。
        slots_changed = (
            context is not None
            and focus is not None
            and self._ammo_block_slots(context, focus) != self._mounted_slots
        )
        if slots_changed:
            self._attach_entries()  # 重建中栏（含块）——新块自己会读新武器
        super().refresh()
        # 卡上面那一次刷新原来靠它自己的订阅（follow=True）——现在归这里。
        # 顺序在 tree 之前：卡与树是两块独立的东西，谁先谁后不影响判据。
        self.card.refresh_from_changes()
        self.tree.refresh()
        self.set_findings(self._findings)
        self._sync_tree_selection()
        if slots_changed:
            return
        # ❗ 块**不在**词条行里，refresh_values 刷不到它：弹药数量改完不重画，
        #    界面上就是"改了数字但那一行还写着旧的"（缓存也一起丢，见 AmmoBlock._entries）。
        for block in self._blocks:
            if isinstance(block, AmmoBlock):
                block.reload()


class UnitEditorScreen(QWidget):
    """单位编辑器主屏（QStackedWidget 里的第 4 页）。"""

    #: 用户要回军械库（← 按钮 / Esc）。
    back_requested = Signal()
    #: 一句给状态栏的话（成功与"没有改动"都走这里，**不弹窗**）。
    status_message = Signal(str)
    #: 缓存里有几处改动变了（顶栏据此点亮/熄灭「💾 保存」、改写胶囊文案）。
    dirty_changed = Signal()
    #: 要跳到数据表页的某一行（参数：表名, 行 Id）。
    open_table_row_requested = Signal(str, int)
    #: 要跳到数据表页**整页**（编成台右栏「数据表页」那颗胶囊）。
    tables_requested = Signal()
    #: 面包屑该重画了（右栏换了行 / 进了专用页 / 回了上一层）。
    trail_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance = None
        self._unit_id = -1
        self._changeset: Changeset | None = None
        self._data: CardData | None = None
        #: 编成台页面（None = 还没建过）。见 _ensure_bench()。
        self._engine: RuleEngine | None = None
        self._baseline = None
        self._context: EditorContext | None = None
        #: 校验角标的延后重算（见 _schedule_findings）。
        self._findings_timer: QTimer | None = None
        #: **改了数据、但还没重画的顶层页**（口径见 _refresh_pages：
        #: 用户没在看的那一页只标脏，等它被显示出来再刷）。
        self._dirty_pages: set[QWidget] = set()
        #: 上一次重取图标时账本里那几笔"跟图标有关"的改动（见 _icon_changes）。
        self._icon_signature: tuple = ()
        #: 增量校验：上一次算出来的**当前**报告 + 规则书顺序 + 两张记账表。
        #: 见 _report_for_changes —— 改一格只重跑"改动到的那几张表"的规则。
        self._report: ValidationReport | None = None
        self._rule_order: tuple[str, ...] | None = None
        self._dirty_tables: set[str] = set()
        self._report_tables: set[str] = set()
        #: 整页刷新（左栏 + 卡片）的合并计时器 —— 见 _schedule_refresh。
        self._refresh_timer: QTimer | None = None
        #: 写盘通道。外层（主窗口）会塞进来它那一份；单独建编辑器时自给自足。
        self._session = EditSession(self)
        #: 唯一的图标缓存（main_window 的 self._armory.icons）；None = 只用 emoji 兜底。
        self._icon_store = None
        #: **单位历史**（最近一个在末尾）：「↩ 回上个单位」用它。
        #: 只在"跳到另一个单位"那一下压栈；从军械库新进 / 换数据源时清空；回退不压栈。
        self._unit_history: list[int] = []

        # ❗ 编辑器**不再自带顶栏**（界面重做定稿 块 2：3 行 90px -> 1 行 36px）。
        # 从前那一条「⬅ 返回军械库 + 面包屑 + 💾 保存」的东西，现在归共用顶栏：
        # 面包屑在 _build_topbar 里，保存状态是那个常驻胶囊（没有保存按钮）。
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        self._main = MainEditorPage()
        self._main.back_requested.connect(self.back_requested)
        self._main.open_table_row_requested.connect(self.open_table_row_requested)
        self._main.changed.connect(self._on_changed)
        self._main.status_message.connect(self.status_message)
        self._main.focus_changed.connect(self.trail_changed)
        self._main.back_to_unit_requested.connect(self.show_unit_row)
        self._main.weapon_requested.connect(self.open_weapon_page)
        self._main.ammo_requested.connect(self.open_ammo_page)
        self._main.options_requested.connect(self.open_options_page)
        # 「跳到一个单位」= 整屏换过去（见 MainEditorPage._open_row 的说明）。
        self._main.unit_requested.connect(self.open_unit_row)
        self._stack.addWidget(self._main)
        #: 编成台（第二个顶层页）。**延迟创建** —— 见 _ensure_bench()。
        self._bench: BenchPage | None = None
        root.addWidget(self._stack, 1)
        # ❗ **切到哪一页就把那一页补刷一次**（它可能是被标脏了好久的那一页）。
        #    用 currentChanged 而不是逐处改 setCurrentWidget：这一页有六七条切换路径
        #    （show_main / show_bench / open_unit / close_page / go_back / 面包屑 / 历史回退），
        #    逐个改迟早漏一个；漏了的症状正是"切回来还是旧值"。
        self._stack.currentChanged.connect(self._on_page_shown)

        QShortcut(QKeySequence("Escape"), self, activated=self._on_escape)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        # ❗ 这里原来还有一条 Ctrl+S —— 与顶栏「💾 保存」那个 QAction **同键**，
        #    同一个键两条绑定是歧义（按下触发哪一条看谁先抢到）。保存只能有一条
        #    通道：main_window 的 QAction -> EditSession.save()（AGENTS §4.5）。

    # ------------------------------------------------------------------ #
    # 面包屑（顶栏那一条听这几个）
    # ------------------------------------------------------------------ #

    def _tail_segments(self) -> tuple[str, ...]:
        page = self._stack.currentWidget()
        return page.trail_segments() if isinstance(page, EditorPage) else ()

    def trail(self) -> tuple[str, ...]:
        """面包屑：军械库 › 单位名 › 当前层（父级可点，见主窗口的 _crumb_go）。"""
        if self._instance is None or self._unit_id < 0:
            return ()
        return ("军械库", self.unit_name(), *self._tail_segments())

    def trail_marks(self) -> tuple[RowMark, ...]:
        """面包屑每一段配的**图标位**（与 trail() 一一对应）。

        段 = 军械库（无图标位）｜单位名（Units 那一行的缩略图 + 单位表 emoji 兜底）
        ｜*_tail_segments，其中**最后一段**就是中栏正在看的那一行 —— 武器的图标 /
        弹药的图标 / 改装的图标就落在它身上。**图标位只能有一个**：
        有真图画真图，没真图才画 emoji（两样都没有就是空 RowMark）。
        """
        if self._instance is None or self._unit_id < 0:
            return ()
        marks: list[RowMark] = [
            RowMark(),
            RowMark(
                icon=row_icon(self._instance, "Units", self._unit_id),
                emoji=table_emoji("Units"),
            ),
        ]
        tail = self._tail_segments()
        if tail:
            # 编成台那一页没有"当前焦点行"（尾段就是「编成台」三个字）—— 图标位留空。
            focus = None if self.on_bench_page() else self._main.focus
            marks.extend([RowMark()] * (len(tail) - 1))
            if focus is not None:
                marks.append(
                    RowMark(
                        icon=row_icon(self._instance, focus.table, focus.row_id),
                        emoji=table_emoji(focus.table),
                    )
                )
            else:
                marks.append(RowMark())
        return tuple(marks)

    def set_icons(self, store) -> None:
        """接上**唯一**那份图标缓存（main_window 传它的 self._armory.icons）。

        ❗ 一个 store 只接一次 `loaded`：图是异步解析的，进编辑器那一刻常常还没到 ——
        到了要**再刷一次**（中栏抬头 + 面包屑），否则用户看到的是永远只有 emoji。
        """
        fresh = store is not self._icon_store
        self._icon_store = store
        self._main.set_icons(store)
        if self._bench is not None:
            self._bench.set_icons(store)
        page = self._stack.currentWidget()
        if isinstance(page, EditorPage) and page is not self._main:
            page.set_icons(store)
        if store is not None and fresh:
            store.loaded.connect(self._refresh_icons)
        # 这一下是**无条件**的（图刚接上 / 刚到货，必须重画一遍）——
        # 顺手把签名对齐，免得紧接着的第一次改动静又白重取一次。
        self._refresh_icons()
        self._icon_signature = self._icon_changes()

    def _refresh_icons(self) -> None:
        """图到了（或刚接上 store）：中栏抬头与面包屑各再刷一次。"""
        self._main.refresh_icon()
        if self._bench is not None:
            self._bench.refresh_icon()
        page = self._stack.currentWidget()
        if isinstance(page, EditorPage) and page is not self._main:
            page.refresh_icon()
        self.trail_changed.emit()

    # ------------------------------------------------------------------ #
    # 编成台 / 关系树：两个**顶层**页（共用同一个 EditorContext）
    # ------------------------------------------------------------------ #

    @property
    def bench_ready(self) -> bool:
        """编成台**建过没有**（没建过就不去碰它，也别为它花构造时间）。"""
        return self._bench is not None

    def bench_page(self) -> BenchPage:
        """拿到编成台页面（没有就现在建）。用例与顶栏那颗切换钮用它。"""
        return self._ensure_bench()

    def _ensure_bench(self) -> BenchPage:
        """建编成台页面（**首次真的要显示它的时候**才建）。

        两个理由，都不是洁癖：

        1. **不为没看的东西花时间与内存**：载具 / 飞机这一轮还没有编成台，
           它们连构造都不该发生（双击进去看到的仍是关系树那一页）；
        2. **炸点要晚、要准**：中栏那三块由并行队友各自交付，构造时会 import 它们 ——
           在这里建 = 只影响"真要看编成台"的那条路，而不是把整个编辑器一起带崩。
        """
        if self._bench is not None:
            return self._bench
        bench = BenchPage()
        bench.back_requested.connect(self.back_requested)
        bench.open_table_row_requested.connect(self.open_table_row_requested)
        bench.changed.connect(self._on_changed)
        bench.status_message.connect(self.status_message)
        bench.focus_changed.connect(self.trail_changed)
        bench.back_to_unit_requested.connect(self.show_unit_row)
        bench.unit_requested.connect(self.open_unit_row)
        bench.page_requested.connect(self._on_page_requested)
        self._stack.addWidget(bench)
        self._bench = bench
        if self._icon_store is not None:
            bench.set_icons(self._icon_store)
        if self._context is not None:
            bench.set_context(self._context)
        bench.set_findings(self._main.findings)
        return bench

    def on_bench_page(self) -> bool:
        return self._bench is not None and self._stack.currentWidget() is self._bench

    def current_bench_kind(self) -> str:
        """当前单位该用哪张编成台（\"squad\" / \"vehicle\" / \"\"）。"""
        if self._data is None or self._unit_id < 0:
            return ""
        return bench_kind(self._data, self._unit_id)

    def show_bench(self) -> bool:
        """切到编成台（当前页就是它时也重刷一次）。"""
        if self._context is None:
            return False
        bench = self._ensure_bench()
        if bench.context is not self._context:
            bench.set_context(self._context)
        if self._icon_store is not None:
            bench.set_icons(self._icon_store)
        bench.set_findings(self._main.findings)
        self._stack.setCurrentWidget(bench)
        self.trail_changed.emit()
        return True

    def land_on_bench(self) -> bool:
        """**用户路径**（军械库双击进来）：有编成台的形态就直接落上去。

        三种编成台都做好了（2026-10-01 补的载具 / 直升机 / 固定翼）—— 判据在 bench_kind()：
        有班成员的走**步兵**那张，飞得起来的（直升机 / 固定翼）走 **21 格挂载图**，
        其余走**炮塔四层层级**。

        ⚠️ **这里有两条交叉验证过的硬事实**（2026-10-01 两轮排查 + 2026-10-02 收口，别再推翻）：

        1. **"只放步兵"那道闸已经拆了**：它原本是为了躲开一个"必现"的 access violation
           （把载具/飞行默认落到编成台就崩）。病灶**不在编成台里**，而在
           `QListWidget.clear()`（模型整表重置那条路径）—— 全页堆下 A 对照崩 / D 换成
           `takeItem` 循环干净，确定性。5 处已全部换成 `takeItem` 循环（`f459f08`）。
        2. **AV 的最终归属是 PySide6 6.11.2 的 shiboken**：WER + VEH 给出的现场是
           `Shiboken::BindingManager::releaseWrapper` 里 `mov rbp,[rax+rsi*8]`、
           `rax=rsi=0 / rcx=1`（在一张 NULL 表上跑第 0 次迭代），不是我们的野指针、
           不是地址复用、不是堆损坏（三条都被实测排除）。详见
           `docs/交接-2026-10-01-载具飞机编成台.md` §6。

        所以这道闸**不该再挂回来** —— 挂着它，"载具/飞行默认落编成台"这条用户口径
        就一直是坏的（顶栏「🔧 编成台」能进去，但双击不是那条路）。
        """
        if not self.current_bench_kind():
            return False
        return self.show_bench()

    def show_main(self) -> None:
        """切回关系树那一页（编成台右栏「关系树（全 24 表）」走这里）。

        ❗ 这里**不再无条件** `self._main.refresh()`：切页那一下
        `QStackedWidget.currentChanged` 已经把脏页补刷了（见 _on_page_shown），
        再刷一遍就是同一帧里把整页（树 + 词条 + 卡）**连做两次** ——
        实测 118.3ms + 71.3ms（`_work/probe_no_full_refresh.py`，"切页时 refresh 的遍数"那一行）。
        `_refresh_page_if_dirty` 是**幂等**的：第一次已经把脏标记吃掉，第二次什么都不做；
        而"已经在这一页上"时 setCurrentWidget 不发信号，这一句就是那道兜底。
        """
        self._stack.setCurrentWidget(self._main)
        self._refresh_page_if_dirty(self._main)
        self.trail_changed.emit()

    def _on_page_requested(self, key: str) -> None:
        """编成台请求切页（tree = 关系树 / tables = 数据表页）。

        ⚠️ 2026-10-02：发射它的「⑤ 其他入口」那两颗胶囊已整块删掉 —— 今天没有发射点。
        这两条路本身还在（顶栏那颗「🌲 关系树」/ 页签），所以这条件缝**留着**。
        """
        if key == "tree":
            self.show_main()
            return
        if key == "tables":
            self.tables_requested.emit()

    def can_go_back(self) -> bool:
        return self._unit_id >= 0

    def show_unit_row(self) -> None:
        """回到「这个单位的 31 条」（右栏那个「⬅ 回单位」按钮）。"""
        if self.on_bench_page():
            assert self._bench is not None
            self._bench.show_unit_row()
            self.trail_changed.emit()
            return
        if self._stack.currentWidget() is not self._main:
            self.close_page()
        self._main.set_focus(RowRef("Units", self._unit_id))
        self.trail_changed.emit()

    def go_back(self) -> None:
        """回上一层（顶栏那个 ← / 右栏那个「⬅ 回单位」/ Esc）。

        顺序（用户口径 2026-10-01）：专用页 → 本单位那一行 → **上一个单位**（历史非空时）
        → 军械库。所以"跳到一个单位之后按 ←"回到的是刚才那个单位，不是一步跨回军械库。
        """
        if self._unit_id < 0:
            return
        if self.on_bench_page():
            # 编成台是顶层页：先"回本单位那一行"，再回上一个单位，最后才回军械库。
            assert self._bench is not None
            if self._bench.selected_row() != ("Units", self._unit_id):
                self.show_unit_row()
                return
            if self.can_return_to_previous_unit():
                self.return_to_previous_unit()
                return
            self.back_requested.emit()
            return
        if self._stack.currentWidget() is not self._main:
            self.close_page()
            self.trail_changed.emit()
            return
        focus = self._main.focus
        if focus is not None and (focus.table, focus.row_id) != ("Units", self._unit_id):
            self.show_unit_row()
            return
        # 焦点已经在本单位那一行：**先回上一个单位**，历史空了才回军械库
        # （用户口径：跳过来之后按 ← 不该一步跨回军械库）。
        if self.can_return_to_previous_unit():
            self.return_to_previous_unit()
            return
        self.back_requested.emit()

    def go_to(self, index: int) -> None:
        """点面包屑的第 index 段：0 = 军械库，1 = 单位，再深就回它上一层。"""
        if index <= 0:
            self.back_requested.emit()
            return
        self.show_unit_row()

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def set_session(self, session: EditSession) -> None:
        """接上外层那一份写盘通道（整个会话共用一份，见 app/shell/edit_session.py）。"""
        self._session = session
        if self._instance is not None:
            session.set_instance(self._instance)
            self._changeset = session.changeset
        self.trail_changed.emit()

    def save_policy(self):
        """保存口径（顶栏胶囊的悬浮提示用它）。"""
        return policy_for(self._instance) if self._instance is not None else None

    def set_instance(self, instance) -> None:
        self._instance = instance
        self._engine = RuleEngine() if instance is not None else None
        # ❗ 这里**不**算基线：下一句 _drop_context() 里的 _reset_baseline() 会用
        #    同一份没变过的数据再算一遍，结果一模一样（实测逐字段一致，
        #    见 _work/probe_validate_invariant.py）。一次全库校验约 133ms。
        self._session.set_instance(instance)
        # 换数据源 = 换了另一库 Id：旧历史里那些 Id 在新库里可能是**别的单位**，
        # 不能留着（否则那颗「↩ 回上个单位」会跳到风马牛不相及的一行）。
        self._unit_history.clear()
        self._drop_context()

    # ------------------------------------------------------------------ #
    # 单位历史（「↩ 回上个单位」）
    # ------------------------------------------------------------------ #

    def _remember_unit(self, unit_id: int) -> None:
        """把**刚才那个**单位压进历史 —— 只在"跳到另一个单位"那一下调。

        上限 UNIT_HISTORY_LIMIT（超了丢最旧那一层）。回退**不**走这里：
        否则 A→B 记 A、回 A 又记 B，两个单位之间来回弹。
        """
        if unit_id < 0:
            return
        self._unit_history.append(unit_id)
        if len(self._unit_history) > UNIT_HISTORY_LIMIT:
            del self._unit_history[: len(self._unit_history) - UNIT_HISTORY_LIMIT]

    def can_return_to_previous_unit(self) -> bool:
        """有没有"上一个单位"可回（顶栏那颗按钮与 ← / Esc 都看它）。"""
        return self._unit_id >= 0 and bool(self._unit_history)

    def previous_unit_id(self) -> int | None:
        """上一个单位是谁（没有就 None）—— 顶栏那颗按钮的 tooltip 用它。"""
        return self._unit_history[-1] if self._unit_history else None

    def history_depth(self) -> int:
        """历史里还压着几层（用例与诊断用）。"""
        return len(self._unit_history)

    def unit_label(self, unit_id: int) -> str:
        """某个单位的显示名（按钮 tooltip 用）；读不到就退回「单位 #Id」。"""
        if self._instance is None:
            return f"单位 #{unit_id}"
        return display_name(self._instance, "Units", unit_id) or f"单位 #{unit_id}"

    def return_to_previous_unit(self) -> str:
        """回上一个单位（顶栏「↩ 回上个单位」/ ← / Esc 都走这里）。返回一句状态栏的话。

        两条硬口径：

        - **不压栈**（keep_history=True）—— 否则在两个单位之间来回弹；
        - 历史里那些"库里已经没有了"的单位直接丢掉，不拿它去建一个空 scope
          （那会让界面看着像白屏）。
        """
        while self._unit_history:
            previous = self._unit_history.pop()
            if previous == self._unit_id or not self._unit_exists(previous):
                continue
            self.open_unit(previous, keep_history=True)
            message = f"已回到上个单位：{self.unit_name()}（#{previous}）"
            self.status_message.emit(message)
            return message
        message = "没有上个单位可回了"
        self.status_message.emit(message)
        return message

    def clone_current_unit(self) -> str:
        """整单位克隆（顶栏「⧉ 克隆整个单位」）。返回一句状态栏的话。

        ❗ 口径翻了（用户 2026-10-02：「**我克隆单位的时候，希望直接跳转到那个单位**」）：
        克隆完**直接进新单位**（open_unit(..., keep_history=True)），不再让人按 F3 输一串号。

        ⚠️ 从前不切过去的理由是"一切单位就换新 context、撤销栈跟着清空" —— 那句**不成立**：
        变更集归 EditSession 所有（_drop_context 的注释里就写着），换 context 不动它，
        所以克隆那一笔 Ctrl+Z 照样退得回来（状态栏现在也这么写）。
        """
        context = self._context
        if context is None or self._unit_id is None:
            message = "没在编辑器里，克隆不了"
            self.status_message.emit(message)
            return message
        message = context.clone_unit(self._unit_id)
        new_id = self._clone_hint_id(message)
        if not new_id:  # pragma: no cover - summary() 的格式变了才会走到
            self.status_message.emit(f"{message} · Ctrl+Z 撤销这次克隆")
            return message
        # 直接跳过去（keep_history=True = 编辑器内部换单位，单位历史原样留着）。
        self.open_unit(int(new_id), keep_history=True, keep_undo=True)
        # ❗ **立刻**发（不许排延时）：排队的写法在用例里（没有跑事件循环）根本不发，
        #    真机上也会被页面自己那句提示盖掉 —— 用户要的"看到克隆结果"就没了。
        self.status_message.emit(f"{message} · 已直接跳到新单位（Ctrl+Z 撤销这次克隆）")
        return message

    @staticmethod
    def _clone_hint_id(message: str) -> str:
        """从「已克隆为 Units#541：…」里抠出新单位 Id（抠不到就给空串）。

        ❗ 只取 Units# 后面**连着的**那一段数字：从前是把整句里的数字全拼起来，于是那句提示
        写成了「按 F3 输 707571121111112312」—— 前面是新 Id、后面是"插了 87 行"与各表行数
        （实测踩过：用户截图上就是这一串）。
        """
        marker = "Units#"
        start = message.find(marker)
        if start < 0:  # pragma: no cover - summary() 的格式变了才会走到
            return ""
        rest = message[start + len(marker) :]
        digits = ""
        for ch in rest:
            if not ch.isdigit():
                break
            digits += ch
        return digits

    def open_unit(
        self, unit_id: int, *, keep_history: bool = False, keep_undo: bool = False
    ) -> None:
        """进编辑器：换单位 = 新建 context（寻址、共享度、撤销栈都跟着换）。

        `keep_history=False`（默认）= **从军械库新进一个单位**：单位历史清空
        （用户口径：「从军械库新进一个单位 → 历史清空」，否则上一次那条链会跟着串）。
        `keep_history=True` = 编辑器内部换单位（跳到 Units 行 / 回上个单位）：历史原样留着。
        """
        if self._instance is None:
            return
        if not keep_history:
            self._unit_history.clear()
        # ❗ keep_undo=True：把撤销栈**搬到新 context**（撤销条目不绑定单位）——
        #    克隆完直接跳过去那条路要它，否则"Ctrl+Z 撤销这次克隆"就是句空话
        #    （实测：换 context 之后 `undo_last()` 直接答"没有可撤销的改动"）。
        previous = self._context if keep_undo else None
        self._drop_context()
        self._unit_id = unit_id
        assert self._changeset is not None
        assert self._data is not None
        self._context = EditorContext(
            self._instance,
            unit_id,
            self._changeset,
            self._data,
            build_sharing_index(self._instance),
            self._on_changed,
        )
        if previous is not None:
            self._context.adopt_undo(previous)
        self._main.set_context(self._context)
        if self.on_bench_page():
            # 已经在编成台上（例如"克隆完直接跳过去"）—— 跟着换过去，别把用户踢回旧界面。
            assert self._bench is not None
            self._bench.set_context(self._context)
        else:
            self._stack.setCurrentIndex(PAGE_MAIN)
        # 基线 = **这次进编辑器时**的形态（那句「原版既有违规不报」就靠它）。
        # ❗ 上面 _drop_context() 刚用同一份数据算过一次，而 EditorContext 与
        #    set_context 都不改行（实测逐字段一致，探针 ①②③）——
        #    所以直接把它当「现状」用，不再重算。
        #    一次全库校验实测约 133ms，而进一次编辑器原来要跑 5 遍，现在 1 遍。
        self._refresh_findings(report=self._baseline)
        # 换单位 = 两个顶层页都是新建的，图标是**顺手取到的新值**；
        # 把签名对齐，免得紧接着的第一次改动又白重取一遍。
        self._icon_signature = self._icon_changes()
        self.trail_changed.emit()

    def open_unit_row(self, unit_id: int) -> str:
        """跳到一个 **Units 行** = 进那个单位的编辑器（**整屏**换过去）。

        用户口径（2026-10-01）：「我希望跳转到单位的时候，整体都跳转过去」——
        从前这条路只 set_focus，于是中栏换成了那个单位的列，左树 / 右卡 / 面包屑 /
        撤销栈 / 共享度索引**全都还是原来那个单位**（看着像换了，其实没换）。

        三条口径：

        - **就是本单位**：只把中栏切到它那一行 —— 树上点根节点走的就是这里，
          而 open_unit 会重建 context（白丢撤销栈），那一下不该重建；
        - **换一个单位**：走现成的 open_unit(unit_id)（与军械库双击进单位同一条路）。
          缓存里的改动不归它管（账本挂在 EditSession 上），**一处都不会丢**；
        - **库里没有这个 Id**：只写状态栏一句话，原地不动 —— 不抛异常、不白屏
          （从前拿一个不存在的 Id 去建 context，scope 是空的）。

        返回那句给状态栏的话（空串 = 顺利换过去了）。
        """
        if self._instance is None:
            message = "尚未加载数据源"
            self.status_message.emit(message)
            return message
        if unit_id == self._unit_id:
            self._main.set_focus(RowRef("Units", unit_id))
            return ""
        if not self._unit_exists(unit_id):
            message = f"库里没有单位 #{unit_id} —— 跳不过去"
            self.status_message.emit(message)
            return message
        # ❗ **只有这里压栈**（"跳到另一个单位"就这一个入口）：
        #    从军械库新进走 open_unit 默认路径（清空），回退走 return_to_previous_unit（不压）。
        self._remember_unit(self._unit_id)
        self.open_unit(unit_id, keep_history=True)
        return ""

    def _unit_exists(self, unit_id: int) -> bool:
        """Units 表里有没有这个 Id。**跳转前的存在性判据只有这一处。**

        用 CardData 的 by_id（现成的 Id 索引），不自己扫表：
        `_drop_context()` 每次换数据源/换单位都会重建一份新的 `_data`。
        """
        data = self._data
        if data is None:
            return False
        return data.t("Units").by_id(unit_id) is not None

    def leave(self) -> str:
        """离开编辑器，返回一句给状态栏的话。

        ❗ **不再还原改动**了。旧口径是"离开即放弃"，现在的口径是**留在缓存里**
        （改一格只进缓存，写盘只走顶栏 💾 那一条）—— 退出编辑器不等于放弃，
        再还原反而会把用户没保存的改动悄悄吃掉。
        """
        count = self._session.change_count
        self._drop_context()
        self.trail_changed.emit()
        return f"已退出编辑器（{count} 处改动尚未落盘）" if count else ""

    def discard_changes(self) -> str:
        """把**没落盘**的改动还原掉，返回一句给状态栏的话（没有改动就是空串）。

        正常路径上这永远不是空的：改一格只进缓存，账本里就有东西 ——
        这个入口是"退出编辑器"时问用户"要不要放弃那几处改动"用的。
        """
        count = self._session.revert_all()
        if not count:
            return ""
        if self._data is not None:
            self._data.invalidate()
        if self._context is not None:
            self._context.rebuild()
        # ❗ 这条路**不走 _on_changed**（revert_all 不经过 EditorContext），
        #    所以 _mark_tables_dirty 收不到通知；不把报告整份作废的话，
        #    角标会停在"改坏了"那一条上（数据明明已经改回来了）。
        self._invalidate_report()
        self._refresh_findings()
        return f"已放弃 {count} 处未落盘的改动"

    def _drop_context(self) -> None:
        """丢掉 context（换单位 / 换数据源时调用）。变更集归 EditSession 所有。"""
        self._context = None
        self._unit_id = -1
        self._changeset = self._session.changeset
        # ❗ 只建一份：原来这里是"两个 if 各建一份"，而第二个 if 无条件覆盖第一个
        #    —— 第一份白建（CardData 实测约 40ms/份，见 _work/probe_bind_cost.py）。
        if self._instance is not None:
            self._data = CardData(self._instance)
        self._reset_baseline()

    @property
    def changeset(self) -> Changeset | None:
        return self._changeset

    @property
    def context(self) -> EditorContext | None:
        return self._context

    @property
    def main_page(self) -> MainEditorPage:
        return self._main

    @property
    def card_panel(self) -> ReadOnlyCardPanel:
        return self._main.card

    @property
    def validation_text(self) -> str:
        """立绘上那条字（空串 = 什么都没画）。"""
        return self._main.card.validation_text

    @property
    def tree_panel(self) -> RelationTreePanel:
        """左栏那棵关系树（用例与诊断用）。"""
        return self._main.tree

    @property
    def current_unit_id(self) -> int:
        return self._unit_id

    def unit_scope_summary(self) -> str:
        if self._context is None:
            return ""
        scope = self._context.scope
        return f"{scope.table_count} 张表 · {scope.cell_count} 个格子"

    # ------------------------------------------------------------------ #
    # 专用页
    # ------------------------------------------------------------------ #

    def open_weapon_page(self, weapon_id: int) -> None:
        self._push(WeaponPage(weapon_id))

    def open_ammo_page(self, weapon_id: int, ammo_id: int) -> None:
        self._push(AmmoPage(weapon_id, ammo_id))

    def open_options_page(self, option_id: int) -> None:
        self._push(OptionsPage(option_id))

    def open_squad_page(self) -> None:
        self._push(SquadPage())

    def squad_size(self) -> int:
        """当前单位有几名班组成员（0 = 不是步兵 / 这个班是空的）。

        ⚠️ 只给顶栏那颗「👥 步兵班」判可见性用 —— 班成员是 `SquadMembers` 表里
        `UnitId == 本单位` 的行（那张表没有别的归属列）。
        """
        if self._instance is None or self._unit_id is None:
            return 0
        if "SquadMembers" not in self._instance.table_names:
            return 0
        table = self._instance.table("SquadMembers")
        index = table.column_index("UnitId")
        return sum(1 for row in table.rows if row[index] == self._unit_id)

    def _push(self, page: EditorPage) -> None:
        if self._context is None:
            return
        page.back_requested.connect(self.close_page)
        page.open_table_row_requested.connect(self.open_table_row_requested)
        page.changed.connect(self._on_changed)
        page.status_message.connect(self.status_message)
        self._stack.addWidget(page)
        page.set_icons(self._icon_store)
        page.set_context(self._context)
        self._stack.setCurrentWidget(page)

    def close_page(self) -> None:
        """从专用页回主编辑页（并把它删掉 —— 下次进是新的）。"""
        page = self._stack.currentWidget()
        if page is self._main:
            return
        self._stack.setCurrentWidget(self._main)
        self._stack.removeWidget(page)
        page.deleteLater()
        # 同上：切页已经补刷过脏页，这里只兜"本来就在这一页上"那一路（幂等，不会刷两遍）。
        self._refresh_page_if_dirty(self._main)
        self._main.refresh_icon()

    @property
    def current_page(self) -> EditorPage:
        return self._stack.currentWidget()

    @property
    def page_titles(self) -> tuple[str, ...]:
        """页栈里每一页的标题（测试用：验"四页各能进"）。"""
        pages = (self._stack.widget(i) for i in range(self._stack.count()))
        return tuple(page.page_title() for page in pages)

    def close_editor(self) -> bool:
        page = self._stack.currentWidget()
        if page is not self._main:
            self.close_page()
            return True
        return self._main.close_editor()

    # ------------------------------------------------------------------ #
    # 改动 / 校验
    # ------------------------------------------------------------------ #

    def _on_changed(self) -> None:
        """数据改了 —— **只动缓存，不写盘**。

        用户口径（2026-09-29，改了前一条"改完自动保存"）：
        「打开一个数据库，实际上是先将数据库放进缓存里，然后在缓存上改，
          改的时候改哪个栏就保存哪个栏，而不是一改就全保存，不然用起来就比较卡，
          然后加一个保存按钮，将缓存里的文件保存进实际的数据库。」

        所以这里只剩三件事：刷新两栏、把角标**延后**算、通知顶栏（保存按钮亮起来）。
        真正的写盘只有一个入口：顶栏的「💾 保存」（或 Ctrl+S → UnitEditorScreen.save）。

        为什么角标也延后：校验要跑一遍全库（实测 26ms），同步算就是"打字发涩"的
        来源之一；它是给人看的提示，晚一轮事件循环没有区别。
        """
        # ❗ **只标脏，不刷**：谁在台上谁马上刷；不在台上的等它被显示出来（见 _refresh_pages）。
        self._mark_pages_dirty()
        self._mark_tables_dirty()
        self._schedule_findings()
        # ❗ 重排左栏 + 重绘卡片也**排到下一轮**（实测约 40ms）。
        #    为什么必须异步：它同步做在「浮层收起」之前，而 Qt 要等事件循环空下来
        #    才重画 —— 那 40ms 于是全变成「浮层卡在那儿不动」（用户口径 2026-09-30：
        #    「这个编辑栏消失的时候还是会顿一下」；只砍掉重复调用仍是 61ms）。
        #    与角标同一个道理、同一个手法（见 _schedule_findings）。
        #    ⚠️ 契约变化：改完要**下一轮事件循环**界面才跟上（用例见 conftest.settle）。
        self._schedule_refresh()
        self.dirty_changed.emit()

    def _schedule_refresh(self) -> None:
        """把「重排当前页 + 重绘主卡」排到下一轮事件循环（同一轮里改十格只做一次）。"""
        if self._refresh_timer is None:
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.timeout.connect(self._refresh_pages)
        self._refresh_timer.start(0)

    def _mark_pages_dirty(self) -> None:
        """改了数据 -> 相关顶层页都标脏。**只是标脏**，刷不刷看它在不在台上。

        ❗ **三个都要标**：两个并列的顶层页 + **当前正在台上的那一页**。
        第三条不能省 —— 专用页（武器 / 弹药 / 改装选项 / 步兵班）是 _push 上来的
        EditorPage，它们有自己的左栏且**正被用户看着**；只标 main/bench 的话，
        它们永远不在 _dirty_pages 里 -> _refresh_pages 不刷它们 -> 改一格左栏不动。
        实测症状就是 test_editor_options_page::test_non_empty_turret_slots_...
        （清空一个炮塔槽之后左栏那个按钮还画着）。
        """
        self._dirty_pages.add(self._main)
        if self._bench is not None:
            self._dirty_pages.add(self._bench)
        current = self._stack.currentWidget()
        if current is not None:
            self._dirty_pages.add(current)

    def _refresh_page_if_dirty(self, page) -> None:
        """这一页脏了就刷它，然后把脏标记消掉。"""
        if page is None or page not in self._dirty_pages:
            return
        self._dirty_pages.discard(page)
        if isinstance(page, EditorPage):
            page.refresh()

    def _on_page_shown(self, _index: int) -> None:
        """切页了 —— 刚上台的那一页如果是脏的，**这时候**才补刷。

        这就是"只刷用户看得见的那一页"的另一半：没有被标脏就什么都不做，
        所以来回切页不会平白重建控件（用户口径：「不要全量保存」「界面闪一下」）。
        """
        self._refresh_page_if_dirty(self._stack.currentWidget())

    def _refresh_pages(self) -> None:
        """真正重排/重绘的那一步（由 _schedule_refresh 排出来）。

        ❗ **只刷用户正在看的那一页**（用户口径：「我修改的时候你能不能不要全量保存一下，
        不然界面闪一下很影响观感」+「尽量别全量保存」）。别的顶层页只**标脏**，
        等它被显示出来再刷（见 _on_page_shown）。

        为什么这条是必须的（不是洁癖）：改一格时**不在台上的那一页**也被同步整块重排过 ——
        cProfile 实测 `MainEditorPage.card` 那一次订阅回调 **58.9ms**，里面在建控件
        （setParent 19 / addWidget 145 / ensurePolished，见 _work/probe_edit_profile.py）；
        用户在编成台上**根本看不见它**。专用页浮在主屏上时同理：主屏留脏，close_page 补刷。
        """
        self._refresh_page_if_dirty(self._stack.currentWidget())
        # ❗ **图标只在"这次改动动了图标键那一列"时才重取**：抬头与面包屑那两个图标的键
        #    可能正是刚改的这一列（`Units.ThumbnailFileName` -> unit_labels 那一族）。
        #    用户口径 2026-10-01：「我改了的话，就要立即看到效果」——所以该重取时必须重取；
        #    但改携弹量 / 血量这种跟图无关的列时**一次都不该重取**（三页 + 面包屑全刷）。
        signature = self._icon_changes()
        if signature != self._icon_signature:
            self._icon_signature = signature
            self._refresh_icons()

    def _icon_changes(self) -> tuple:
        """账本里**跟图标有关**的那几笔改动（列级 + 行级）。

        判据只有一条：改的列在 `ICON_COLUMNS` 里，或者插/删的行落在 `ICON_TABLES` 里。
        比的是"这批改动本身"而不是"有没有改动"，所以改十格携弹量不会让图标重取十次。
        """
        changeset = self._changeset
        if changeset is None:
            return ()
        out = [
            (change.table, change.row_id, change.column)
            for change in changeset.changes
            if (change.table, change.column) in ICON_COLUMNS
        ]
        out.extend(
            (row_change.table, row_change.row_id, row_change.kind)
            for row_change in changeset.row_changes
            if row_change.table in ICON_TABLES
        )
        return tuple(out)

    def _schedule_findings(self) -> None:
        """把"重算校验角标"排到下一轮事件循环（同一轮里改十格只算一次）。"""
        if self._findings_timer is None:
            self._findings_timer = QTimer(self)
            self._findings_timer.setSingleShot(True)
            self._findings_timer.timeout.connect(self._refresh_findings)
        self._findings_timer.start(0)

    @property
    def pending_count(self) -> int:
        """缓存里还有几处改动没写进数据库（顶栏那个保存按钮看它）。"""
        return self._session.change_count

    def _reset_baseline(self) -> None:
        """把当前状态认成新的校验基线（保存成功后 / 换数据源时）。"""
        if self._engine is None or self._instance is None:
            self._baseline = None
            self._report = None
            self._rule_order = None
            self._dirty_tables.clear()
            self._report_tables.clear()
            return
        self._baseline = self._engine.validate(self._instance)
        # 增量校验的起点：基线**就是**"当前状态"那一份报告（见 _report_for_changes）。
        self._report = self._baseline
        self._rule_order = None
        self._dirty_tables.clear()
        self._report_tables.clear()

    # ---- 增量校验（改一格只重跑"改动到的那几张表"的规则）-------------- #

    def _invalidate_report(self) -> None:
        """把增量那份报告**整份作废**（下次 _report_for_changes 全量重算一遍）。

        什么时候必须这样：**数据从"改过"变回原样、而账本同时清空**的那条路
        （discard_changes 里的 revert_all）。它不发 _on_changed，
        所以 _mark_tables_dirty 收不到通知 —— 不整份作废，角标就停在旧结论上。
        这条路是用户手点的低频动作（"放弃改动"），全量重算一次 26ms 完全付得起：
        **正确性优先于那点耗时**。
        """
        self._report = None
        self._dirty_tables.clear()
        self._report_tables.clear()

    def _changeset_tables(self) -> set[str]:
        """账本里**动了哪几张表**（撤干净的表不会出现在这里）。"""
        changeset = self._changeset
        return set(changeset.changed_tables()) if changeset is not None else set()

    def _mark_tables_dirty(self) -> None:
        """这几张表的规则要重跑。

        ❗ 第二种情况（`_report_tables - changed_now`）不能省：用户把某张表**撤干净**之后
        它就从 `changed_tables()` 里消失了，而报告里还留着"改过"的样子 ——
        不重跑那张表，角标就一直挂在已经改回来的单位上。
        """
        changed_now = self._changeset_tables()
        self._dirty_tables |= changed_now | (self._report_tables - changed_now)

    def _rule_ids(self) -> tuple[str, ...]:
        """规则书里的规则 id，**按规则书顺序**（增量报告要按它并回缓存，顺序才不会漂）。"""
        if self._rule_order is None and self._engine is not None:
            self._rule_order = tuple(rule["id"] for rule in self._engine.rules_for(None))
        return self._rule_order or ()

    def _affected_tables(self, changed: set[str]) -> set[str]:
        """改动这几张表会波及哪些表的规则 —— 按规则书里的**读表声明**做一遍闭包。

        为什么需要闭包（少这一步就是漏报，而漏报比慢严重得多）：

        - `foreign_key` 读 `rule["table"]` **和** `rule["target"]`：删掉 Weapons 一行，
          指向它的 TurretWeapons 外键规则必须重跑（凭据在 `rule["target"]` 上）；
        - `default_flag_required` 读 `target`（Armors）；
        - `required_per_unit` 读 Units（照 `_check_required_per_unit` 的实现，
          见 _CHECK_EXTRA_TABLES）；
        - **跨表不变量**读哪几张表没有声明在规则书里（`_check_cross_*` 里写死），
          所以归属它的表每次都重跑（见 _ALWAYS_RERUN_CHECKS）。

        闭包不完整 = 角标不报 —— 所以除了这里写清依据，还有一条
        「**增量报告必须与全量报告逐条一致**」的守卫用例守着
        （tests/test_editor_no_full_refresh.py）。
        """
        affected = set(changed)
        engine = self._engine
        if engine is None:
            return affected
        for rule in engine.rules_for(None):
            if rule.get("check") in _ALWAYS_RERUN_CHECKS and rule.get("table"):
                affected.add(rule["table"])
        for _ in range(4):  # 依赖很浅（规则 -> 表）；4 轮足够收敛，收敛了就提前退出
            grew = False
            for rule in engine.rules_for(None):
                reads = {rule.get("table"), rule.get("target")}
                reads.update(_CHECK_EXTRA_TABLES.get(rule.get("check"), ()))
                reads.discard(None)
                owner = rule.get("table")
                if reads & affected and owner and owner not in affected:
                    affected.add(owner)
                    grew = True
            if not grew:
                break
        return affected

    def _patch_report(self, cached: ValidationReport, affected: set[str]) -> ValidationReport:
        """把受影响那几张表的规则重跑一遍，按**规则书顺序**并回缓存。"""
        engine, instance = self._engine, self._instance
        if engine is None or instance is None:
            return cached
        fresh: dict[str, object] = {}
        for table in sorted(affected):
            for outcome in engine.validate(instance, table=table).outcomes:
                fresh[outcome.rule_id] = outcome
        if not fresh:
            return cached
        by_id = {outcome.rule_id: outcome for outcome in cached.outcomes}
        merged = []
        for rule_id in self._rule_ids():
            outcome = fresh.get(rule_id) or by_id.get(rule_id)
            if outcome is not None:
                merged.append(outcome)
        return ValidationReport(tuple(merged))

    def _report_for_changes(self) -> ValidationReport | None:
        """**当前**报告 —— 只重跑改动到的那几张表的规则，没有改动就一次都不跑。

        为什么这不是"少算"：`core/rules/engine.py` 的模块说明第一句就是
        「每条规则都带 `table`，所以"改了一张表"只需要重跑那张表的规则，不必 24 张表全扫」，
        `RuleEngine.validate(instance, table=...)` 就是为它准备的入口 —— 而调用方一直传 None，
        于是**改一格 = 16687 行全扫一遍**（实测 26–73ms，见 _work/probe_edit_profile.py）。

        代价对比（实测，单位 65 改一格携弹量）：全量 26–73ms → 增量约 1–3ms。
        """
        engine, instance = self._engine, self._instance
        if engine is None or instance is None:
            return None
        cached = self._report
        if cached is None:
            cached = self._baseline if self._baseline is not None else engine.validate(instance)
        tables = set(self._dirty_tables)
        self._dirty_tables.clear()
        if tables:
            cached = self._patch_report(cached, self._affected_tables(tables))
        self._report = cached
        self._report_tables = self._changeset_tables()
        return cached

    def _refresh_findings(self, report=None) -> None:
        """立绘上的角标判据：**新增**的命中（原版既有违规不报）。

        实测：打开时基线有 699 warning / 1036 info，540 个单位里 28 个
        一打开就带命中（BTR-82A 自己带 1 条）。不减基线就是"一打开一片红"。
        """
        if self._instance is None or self._engine is None or self._baseline is None:
            self._main.set_findings(())
            return
        if self._unit_id < 0:
            self._main.set_findings(())
            return
        # report 可以由调用方递进来（刚算过的那份）——省掉一次约 133ms 的全库校验。
        # 不传就走**增量**（只重跑改动到的那几张表，见 _report_for_changes）；
        # 变更集订阅、定时器、编辑后刷新走的都是这条路。
        if report is None:
            report = self._report_for_changes()
        delta = unit_finding_delta(self._instance, self._baseline, report)
        findings = delta.get(self._unit_id, ())
        self._main.set_findings(tuple(_humanized(self._instance, finding) for finding in findings))
        # 编成台那一页的卡是**同一张卡的另一个实例**，角标也要跟着变（两个顶层页同源）。
        if self._bench is not None:
            self._bench.set_findings(self._main.findings)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #

    def undo(self) -> None:
        if not self._active():
            return
        if self._context is None:
            self.status_message.emit("编辑器还没打开")
            return
        self.status_message.emit(self._context.undo_last())
        # 撤销也是一次改动 -> 同样**只进缓存**（口径：改一格只进缓存、顶栏 💾 才落盘）。
        # 从前这里跟一句"立刻存盘"，那是自动保存时代的注释；自动保存已经没有了。
        # 脏态与胶囊由 context 回调里的 _on_changed 刷新，这里不用再做什么。

    def redo(self) -> None:
        """重做**上一条撤销掉的**（Ctrl+Y）—— 与 undo() 完全对称。"""
        if not self._active():
            return
        if self._context is None:
            self.status_message.emit("编辑器还没打开")
            return
        self.status_message.emit(self._context.redo_last())

    def apply_cell(self, ref, value) -> None:
        """从外部写一格（测试 / 将来别的入口用）。"""
        if self._context is None:
            return
        self._context.set_value(ref, value)

    def save(self, *, silent: bool = False) -> None:
        """保存 = 写 BA_Mod.zip。**毫秒级**；成功与"没有改动"都只写状态栏。

        真正的写盘在那份共用的 EditSession 里（plan -> write -> verify -> commit）。
        参数 silent 是**老自动保存**留下的：只压掉"没有改动"那句空话。
        自动保存（改完立刻写）已经没有了（AGENTS §4.5）：改一格只进缓存，
        写盘只有顶栏「💾 保存」/ ☰「保存」/ Ctrl+S 那一条通道；今天没有
        调用方传 silent=True（grep 过），别照着这条注释复活它。

        ❗ **校验基线不跟着保存走**（曾经重置过，是个错）：基线的语义是
        "这次打开编辑器时是什么样"，一句"原版既有违规不报"就够；
        用户自己改出来的违规**要**报。
        """
        if self._instance is None:
            if not silent:
                self.status_message.emit("尚未加载数据源")
            return
        message = self._session.save(silent=silent)
        self._refresh_findings()
        # 写盘之后缓存就空了（commit）—— 顶栏那颗按钮要跟着灭。
        self.dirty_changed.emit()
        # silent 只压掉"没有改动"那句空话（EditSession 那时候返回空串）。
        if message:
            self.status_message.emit(message)

    def default_save_path(self) -> Path:
        """保存写到哪儿 —— 口径只有一份，在 `core/edit/package.default_save_target`。

        三档（用户 2026-09-30 定："保存写回**你打开的那个文件**"，且不生成 .bak）：

        | 打开的是 | 落点 |
        |---|---|
        | `BA_Mod.zip` / `DataBaseCompiled.json` | **那个文件本身**（同一路径原子替换） |
        | `DataBaseCompiled` 目录 | <该目录的上一级>/ `BA_Mod.zip` |
        | `data.unity3d` | 同上（那条路是真的 7.1 GB / 约 4 分钟） |
        """
        return self._session.default_save_path()

    def set_save_path(self, path) -> None:
        """测试与将来「另存为」用：指定保存落点，不再弹选择框。"""
        self._session.set_save_path(path)

    @property
    def session(self) -> EditSession:
        return self._session

    def _on_escape(self) -> None:
        """Esc = 回上一层：先关浮层 / 先退专用页 / 再回单位那一行；都没有才回军械库。"""
        if not self._active():
            return
        if self.close_editor():
            return
        self.go_back()

    def _active(self) -> bool:
        """这三个快捷键（Esc / Ctrl+Z / Ctrl+Y）**只在编辑器这一页看得见时**才管用。

        ❗ 快捷键挂的是窗口级上下文：编辑器藏在 QStackedWidget 的第 4 页里，
        不判一下的话，人在军械库里按 Ctrl+Z 会撤掉编辑器里的改动、
        按 Esc 会莫名切页（用户会以为界面在抽风）。
        （Ctrl+S 不在这里 —— 它归顶栏那一个 QAction，见 __init__ 里的说明。）

        判据是"**我这一页是不是那个栈的当前页**"，不是 isVisible()：
        窗口没 show() 的时候（用例与 smoke 就是这么跑的）整棵树都不可见，
        用 isVisible() 会把正常调用也拦掉。
        """
        parent = self.parentWidget()
        if parent is None:
            return True  # 单独建出来的（用例 / smoke）
        if isinstance(parent, QStackedWidget):
            return parent.currentWidget() is self
        return self.isVisible() or not parent.isVisible()

    def unit_name(self) -> str:
        from ba_mod_maker.core.edit.foreign import display_name

        if self._instance is None or self._unit_id < 0:
            return ""
        return display_name(self._instance, "Units", self._unit_id)


def _humanized(instance, finding):
    """把命中文案里的裸 Id 换成名字（用户口径：界面不给用户看 Id）。"""
    from ba_mod_maker.core.rules.engine import Finding

    text = humanize_ids(instance, finding.message)
    return Finding(
        rule_id=finding.rule_id,
        severity=finding.severity,
        table=finding.table,
        message=text,
        row_ids=finding.row_ids,
        unit_ids=finding.unit_ids,
    )


__all__ = ["PAGE_MAIN", "MainEditorPage", "UnitEditorScreen"]
