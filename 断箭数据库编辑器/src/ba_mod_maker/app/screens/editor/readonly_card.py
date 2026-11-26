"""单位编辑器右栏那张卡：**游戏里的样子，整块只读**。

口径（用户 2026-09-30 定，见 docs/设计-界面重做-定稿.md 与 _work/design/overhaul3.png）：

    卡片整块只读：原来「点一格就地改」「点图标 / 名字跳页」「＋ / ✕ / 换一个 / 克隆一份」
    **全部拿掉**；改数据只在中间那栏（这一行的全部列），动作长在关系树的节点上。

所以这一张是`军械库那张 UnitCardPanel` 的子类，只**覆写掉三处可点的入口**：

| 覆写 | 为什么 |
|---|---|
| 显示模式那对小按钮（简略 / 详细） | 那是军械库那张卡的行为，编辑器这张不给点 |
| 改装槽的 `QComboBox` | 下拉整个拿掉了；**选中**改由"点那一行"承担（见下面那一段） |
| 点武器 / 点底部按钮那几条路 | 卡片不再是导航面（用户："不再是导航"） |

军械库那张 `UnitCardPanel` **一行都不改**（用户口径："军械库沿行现状，别重做"）。

再加上这一轮（2026-10-01）的两条，都走基类那两个**默认关**的开关，军械库那张不受影响：

- **`fit_to_width = True`** —— 卡片按栏宽自适应：内容宽 = 视口宽 × 档位，放不下的块
  折行（属性那一排折行、简略武器表换成一行一条）。于是**不会再有横向滚动条** ——
  用户原话："右边的卡片显示不全，不要让我手动向右向左滑，这个观感很不好。"
- **`zoom_levels = (100, 90, 80, 70)`** —— 卡片底下那排「卡片大小」：整张卡等比缩放
  （字号 + 图标盒 + 内容宽一起缩）。当前档位就是选中那颗按钮，文案里没有省略号。

**2026-10-01 实机反馈又改了一条口径**（用户原话：「右边改装槽，我选中的改装槽，它不会
高亮」）：改装槽那几行的**整行点击 = 选中它**（写 `Options.IsDefault`，槽内互斥），
`↗` 才是"去看这一行的全部列"。原来"选中"只挂在左树的右键菜单里 —— 卡上点一行只是
"去看"，用户点了以为选中了、高亮却一点没动（实机复现过：右键那条路本身是好的，
缺的是**可发现性**）。借来的槽照旧不给选。

校验角标还在：它只是立绘上那个 "!"，不是可点入口。

## 块 4 / 5 / 8 / 10：卡上多出来的三块（2026-10-01）

设计稿：`_work/design/block8.png`（炮塔四层）、`block9.png`（改装槽互斥）、
`block10.png`（21 个挂载槽一个都不折叠）。三块都**只读**：卡上不写库、也不给 ＋/✕
—— 写入口仍在关系树行尾与中栏词条面板（Lead 定的接缝口径），卡上只负责
**"画出来 + 点一层换中栏那一行"**：

1. **单位 › 炮塔 › 武器 › 弹种**（`UnitCard.turrets`）：四层都能点，点了中栏换成那一行的
   全部列（炮塔 Turrets / 武器 Weapons / 弹种 Ammunitions，ID 照硬口径写 `#Id`）；
2. **改装槽 › 选项（互斥）**（`UnitCard.card_options`）：选中的那一项高亮
   （role=option-row + selected=true），右边标费用与"它改了什么"（`CardOptionRow.summary`）；
3. **21 个炮塔挂载槽全列出来、一个都不折叠**（`CardOptionGroup.slots` 恒为 21 格）：
   画在**选中那一项**下面，空槽也在（用户明确要求）。

## 点一层怎么落到中栏

"点一行 -> 中栏换成那一行"的唯一入口是**那一页**的 `EditorPage.open_row_requested`
（`MainEditorPage.__init__` 把它接到 `_open_row`）。本文件改不了 unit_editor_screen.py，
所以按鸭子类型**往上找那一页**（与 `pages/base.py::_wire_left_card` 同一套写法），
找到了就把 (表, 行 Id) 发出去 —— 于是"卡片不再是导航面"那条老口径只剩
"不跳页、不写库"，**换中栏**这条回来了（设计 §1 第 4 条：卡上能点的一切都通向它的那一行）。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ba_mod_maker.app.screens.armory.detail_panel import (
    ZOOM_LEVELS,
    CardNavLabel,
    CardNavRow,
    UnitCardPanel,
    _label,
    card_id_badge,
    id_badge,
    link_tooltip,
)
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.unit_card import (
    CardOptionGroup,
    CardOptionRow,
    TurretSlotCell,
    UnitCard,
)
from ba_mod_maker.app.widgets.flow_layout import FlowLayout

#: 21 个槽那一排格子上，名字最长留几个字（再长就把一排挤成一列 —— 全文在 tooltip 里）。
CHIP_NAME_LIMIT = 12


class ReadOnlyCardPanel(UnitCardPanel):
    """只读卡：版式全部继承，只把"能点的地方"拿掉。"""

    #: **按栏宽自适应**（用户报的"右边的卡片显示不全，不要让我手动向右向左滑"）：
    #: 内容宽度 = 视口宽 × 档位，放不下的块折行（属性那一排折行、简略武器表换成
    #: 一行一条），于是**不会再有横向滚动条**，立绘四角那 4 个装甲数字也都看得全。
    #:
    #: ❗ 军械库那张卡是 False（用户口径："军械库设计还是原来的好"）——
    #: 那个开关就是为这件事留的，不是"顺手也给它开了"。
    fit_to_width = True

    #: 「放大 / 缩小」档位。只有这张卡有（军械库那张是空的，一颗按钮都不出现）。
    zoom_levels = ZOOM_LEVELS

    #: 「选中改装槽的这一项」（参数：改装槽 Id, 选项 Id）—— 由主编辑页接一次，
    #: 落到 options_page.select_slot_option（写 Options.IsDefault，槽内互斥）。
    #: ❗ 2026-10-01 实机反馈补的这条：用户原话「右边改装槽，我选中的改装槽，
    #: 它不会高亮」—— 原来"选中"只挂在左树的**右键菜单**里，卡上点一行只是"去看"。
    option_select_requested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "readonly-card")
        #: 这个单位头上的校验命中（只画角标；文案与旧卡一致，见 validation_text）。
        self._validation: tuple = ()
        #: 21 格那一排的格子（每次重排重建；用例与"21 个一个不少"都按它读）。
        self._slot_chips: list[QLabel] = []

    def clear(self) -> None:
        """基类那一条 + 把 21 格那一排的引用丢掉（它们已经跟着正文一起被销毁了）。

        ❗ 不清的话 slot_chips 会指着一批已经 deleteLater 的标签 —— 用例一碰就
        RuntimeError（Internal C++ object already deleted）。
        """
        super().clear()
        self._slot_chips = []

    # ------------------------------------------------------------------ #
    # 三处可点的入口：全部拿掉
    # ------------------------------------------------------------------ #

    def _mode_switch(self) -> QWidget:
        """不画「简略 / 详细」那两颗按钮（只读卡固定用简略那种版式）。"""
        return QWidget()

    def _open_detail(self, group_key: int) -> None:
        """点武器不再跳任何地方（只读卡不是导航面）—— 它照样什么都不做。"""

    def _toggle_weapon(self, group_key: int) -> None:
        """同上：简略模式那一行武器不给点。"""

    def set_option_writer(self, writer=None) -> None:
        """只读卡**永远不接**写盘通道 —— 改装槽的选中改在树上（● 那一条）。"""
        self._option_writer = None

    def _on_option_picked(self, group, combo) -> None:  # pragma: no cover - 没有下拉，防回归
        """没有下拉就发不出这个信号；留着是为了"哪天有人加了控件"时不写库。"""

    # ------------------------------------------------------------------ #
    # 点一层 -> 中栏换成那一行（卡自己不写库、也不跳页）
    # ------------------------------------------------------------------ #

    def _page_signal(self):
        """往上找那一页的 `open_row_requested`（编辑器里 = MainEditorPage）。

        为什么不新开一条信号：卡在右栏，而**接线的地方**
        （`MainEditorPage.__init__`: `self.open_row_requested.connect(self._open_row)`）
        不在本文件里。`EditorPage.open_row_requested` 本来就已经是"点一行 -> 中栏换成
        那一行"的唯一入口，复用它就一处口径、也不会出现"两条路各发一次"。

        找不到（裸控件：用例单独建一张卡）时返回 None —— 调用方据此什么都不做。
        """
        widget = self.parentWidget()
        while widget is not None:
            signal = getattr(widget, "open_row_requested", None)
            if signal is not None and hasattr(signal, "emit"):
                return signal
            widget = widget.parentWidget()
        return None

    def _navigate(self, table: str, row_id: int) -> bool:
        """卡上点了某一层 -> 中栏换成那一行的全部列。返回"发出去了没"。

        `Units` 那一层也走同一条：`_open_row` 对 Units 有专门口径（回本单位就切中栏、
        换单位才整屏跳 —— 见 `UnitEditorScreen.open_unit_row`），卡不另搞一套。
        """
        if row_id <= 0:
            return False
        target = self._page_signal()
        if target is None:
            return False
        target.emit(table, row_id)
        return True

    @staticmethod
    def _row_tooltip(table: str, row_id: int) -> str:
        return f"{table} #{row_id} · 点它 → 中栏换成这一行的全部列"

    def _nav_row(
        self,
        text: str,
        table: str,
        row_id: int,
        *,
        indent: int = 0,
        note: str = "",
        tooltip: str = "",
        badge: QWidget | None = None,
    ) -> QWidget:
        """卡上一行**可点**的东西：左边名字（悬浮变蓝），右边一行小字 + `#Id`。"""
        host = QWidget()
        # 透明容器（qss 里已有 link-row 那一条）—— 不挂它会被刷成一块 bg-base。
        host.setProperty("role", "link-row")
        row = QHBoxLayout(host)
        row.setContentsMargins(self._px(TOKENS.spacing.sm) * indent, 0, 0, 0)
        row.setSpacing(self._px(TOKENS.spacing.sm))
        name = CardNavLabel(
            text,
            "detail-link",
            tooltip=tooltip or self._row_tooltip(table, row_id),
            wrap=self.fit_to_width,
        )
        name.activated.connect(lambda t=table, r=row_id: self._navigate(t, r))
        row.addWidget(name, 1)
        if note:
            row.addWidget(_label(note, "card-note"), 0)
        # ❗ Id 摆在**最右**（用户口径：「都在右边显示其 ID」）—— 与中栏那四块同一套 role。
        if badge is not None:
            row.addWidget(badge, 0)
        return host

    # ------------------------------------------------------------------ #
    # 块 5 + 块 10：改装槽 › 选项（互斥）+ 21 个炮塔挂载槽
    # ------------------------------------------------------------------ #

    def _options_section(self, card: UnitCard) -> QWidget:
        """改装槽那两块（原来是"槽名 + 当前 + 一串 ○/● 文字"，只读版：画全 + 可点）。

        - **槽内互斥**：一个槽一行"选中的那一项"高亮（role=option-row + selected=true），
          其余列在它下面 —— 项数就是 `N 选 1` 里的 N；
        - **右边标费用与"它改了什么"**：`CardOptionRow.cost_text` + `.summary`；
        - **21 个炮塔挂载槽一个都不折叠**：见 `_slot_grid`。

        ❗ 卡上**不给选**：换一项 = 写 `Options.IsDefault`，那件事在左边关系树的
        「🧩 改装槽 › ⋯」节点上（右键 → 选中这一项）。卡跟着数据重画。
        """
        frame, layout = self._frame("detail-card")
        self._slot_chips = []
        header = QHBoxLayout()
        header.setSpacing(self._px(TOKENS.spacing.sm))
        header.addWidget(_label(f"🧩 改装槽（{len(card.card_options)}）", "detail-section"), 1)
        header.addWidget(_label("槽内互斥，选一个", "card-note"), 0)
        layout.addLayout(header)
        if not card.card_options:
            layout.addWidget(_label("这个单位没有改装槽。", "empty-hint", wrap=True))
            return frame
        for group in card.card_options:
            layout.addWidget(self._option_group(group))
        return frame

    def _option_group(self, group: CardOptionGroup) -> QWidget:
        """一个改装槽：槽名 + `N 选 1` + 那几项（选中的高亮）+ 选中项的 21 个挂载槽。"""
        host = QWidget()
        # 透明容器（qss 里已有 block-item 那一条）。
        host.setProperty("role", "block-item")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(self._px(TOKENS.spacing.xs))
        head = QHBoxLayout()
        head.setSpacing(self._px(TOKENS.spacing.sm))
        # 槽名 + `#槽 Id`（两个槽可能**重名** —— 实测 F-15E 的 WingPylons1/2 都叫「机翼挂架」）。
        head.addWidget(_label(group.label, "weapon-name"), 1)
        head.addWidget(_label(f"#{group.modification_id}", "card-note"), 0)
        head.addWidget(_label(f"{len(group.options)} 选 1", "card-note"), 0)
        if not group.is_own_slot:
            # 变体单位借来的槽（645 借 108 的 743）：卡片照画，但不给选 —— 写它会改到主人。
            head.addWidget(
                _label(f"借来的槽（属于单位 #{group.owner_unit_id}），只给看不给选", "card-note"), 0
            )
        box.addLayout(head)
        for row in group.options:
            box.addWidget(self._option_row(group, row))
        if group.occupied_slots:
            box.addWidget(self._slot_grid(group))
        return host

    def _option_row(self, group: CardOptionGroup, row: CardOptionRow) -> QWidget:
        """一项：`◉/○ 名字` + 费用 + 「它改了什么」+ 行尾那颗 `↗`。

        ❗ **点这一行 = 选中它**（写 `Options.IsDefault`，槽内互斥）——2026-10-01
        按实机反馈改的口径。用户原话：「右边改装槽，我选中的改装槽，它不会高亮」：
        原来"选中"只挂在左树的**右键菜单**里，卡上点一行只是"去看"，于是用户点了
        以为选中了、卡上的高亮却一点没动。「去看这一行的全部列」挪到行尾那颗 `↗`
        （卡上其它块照旧是"点一下换中栏那一行"）。

        借来的槽（`group.is_own_slot == False`）**不给选** —— 写它会一起改到槽的主人
        （口径在 options_page.select_slot_option）。但"点它"要落到**对的那一层**：
        这一项是"换成另一个单位"（`Options.ReplaceUnitId`）时点它 = **打开那个单位**
        （整屏跳过去，与军械库那张卡的下拉同一条路）；否则只把中栏换成这一行的全部列。
        实测单位 645（自己没有改装槽，卡上那一组是借 108 的 743）：从前点下去卡上
        一点反应都没有，用户看到的就是"点了不亮"。
        """
        host = CardNavRow("option-row")
        if row.selected:
            # 选中态那条 qss：QFrame[role="option-row"][selected="true"]
            host.setProperty("selected", "true")
        picks = bool(group.is_own_slot)
        #: 借来的槽里这一项是"换成**另一个**单位"时，点它 = **打开那个单位** ——
        #: 与军械库那张卡同一个手势（`detail_panel._on_option_picked` 第一条路就是
        #: `variant_requested`），也与用户口径「跳转到单位就整体跳过去」一致
        #: （`unit_editor_screen.open_unit_row`）。一个字节都不写：那个槽不是本单位的。
        opens_unit = (
            0
            if picks
            else (row.replaces_unit_id if row.replaces_unit_id not in (0, self._unit_id) else 0)
        )
        if picks:
            action = "点这一行 = 选中它（写 Options.IsDefault，槽内互斥）"
        elif opens_unit:
            action = (
                f"借来的槽只给看不给选 · 点它 → 打开单位 #{opens_unit}（这一项 = 换成那个单位）"
            )
        else:
            action = "借来的槽只给看不给选 · 点它 → 中栏换成这一行"
        tip = [f"Options #{row.option_id} · {action}；点行尾 ↗ 只看不改"]
        if row.replaces_unit_id:
            tip.append(f"选了它会换成单位 #{row.replaces_unit_id}（改型，不是换装备）")
        if row.effects:
            tip.extend(effect.text for effect in row.effects)
        host.setToolTip("\n".join(tip))
        box = QVBoxLayout(host)
        box.setContentsMargins(
            self._px(TOKENS.spacing.sm),
            self._px(TOKENS.spacing.xxs),
            self._px(TOKENS.spacing.sm),
            self._px(TOKENS.spacing.xxs),
        )
        box.setSpacing(self._px(TOKENS.spacing.xxs))
        line = QHBoxLayout()
        line.setSpacing(self._px(TOKENS.spacing.sm))
        mark = "◉" if row.selected else "○"
        # ❗ `swallow=True`：名字这一下**到此为止**，别再冒泡给宿主那一行。
        #    名字的动作与整行**是同一个**（`name.activated -> host.activated`），
        #    不吞的话一次真鼠标点击会发两次 `option_select_requested`：第一次写进去
        #    （状态栏「已选中「X」（+10）」），第二次 writes 已经是空的，于是**后一句把它
        #    盖掉** ——「X」已经是这个槽选中的那一项。用户刚点完就看到这句，与事实相反。
        #    实测（真机 CU，route=global_input 点这一行）就是这个症状。
        name = CardNavLabel(
            f"{mark} {row.label}",
            "weapon-name" if row.selected else "detail-link",
            tooltip=host.toolTip(),
            wrap=self.fit_to_width,
            swallow=True,
        )
        name.activated.connect(host.activated.emit)
        line.addWidget(name, 1)
        line.addWidget(_label(row.cost_text, "option-cost"), 0)
        line.addWidget(id_badge(row.option_id), 0)
        # ❗ 这一项自己那一行（`Options#N`）—— 从前**只有 tooltip 里有**，
        #    用户口径是「都在右边显示其 ID」。费用右边、↗ 左边，与右栏 / 中栏 /
        #    左卡其它块同一个格局（值右边、工具前）。
        # 行尾那颗 ↗ = **只去看**这一行的全部列。它 swallow 掉点击，不惊动整行那一下
        # （不吞的话一次点击会同时"选中它"和"去看它"，见 CardNavLabel.swallow）。
        open_it = CardNavLabel(
            "↗",
            "detail-link",
            tooltip=f"看 Options #{row.option_id} 的全部列（不改选中）",
            swallow=True,
        )
        open_it.activated.connect(lambda o=row.option_id: self._navigate("Options", o))
        line.addWidget(open_it, 0)
        box.addLayout(line)
        # 「它改了什么」单独一行铺满整个宽度（挤在名字右边会被压成四五行，见块 9 那张图）——
        # 全文同时挂在那一行的 tooltip 上（太长时看得见整句）。
        summary = row.summary
        if summary:
            box.addWidget(_label(summary, "card-note", wrap=True))
        if picks:
            # 本单位自己的槽：点整行 = 选中它（外层落到 select_slot_option）。
            host.activated.connect(
                lambda m=group.modification_id, o=row.option_id: self.option_select_requested.emit(
                    m, o
                )
            )
        elif opens_unit:
            host.activated.connect(lambda u=opens_unit: self._navigate("Units", u))
        else:
            host.activated.connect(lambda o=row.option_id: self._navigate("Options", o))
        return host

    def _slot_grid(self, group: CardOptionGroup) -> QWidget:
        """块 10 · **这个选项的 21 个炮塔挂载槽全列出来，一个都不折叠**。

        `group.slots` 恒为 21 格（0..20，`TurretSlotCell`）：亮着的 = 这个槽装着哪座炮塔，
        暗着的 = 空槽（`Options.TurretNId == 0`）。点一格 -> 中栏换成那一行：
        装了炮塔的换成那座炮塔（Turrets 的 10 列），空槽换成**这条选项**（改槽位就在它的
        `TurretNId` 那一格上）。

        ❗ **空槽只写槽号**（不是「N 空」），**下面也不再有那句说明**（2026-10-02）：
        20 个空槽写成「N 空」，连起来就是一条灰字墙 —— 用户圈着那一条问
        「这里是什么东西？改掉」。21 格本身一格都不少（那是既有口径）。
        """
        host = QWidget()
        host.setProperty("role", "card-flow")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(self._px(TOKENS.spacing.xs))
        cells = QWidget()
        cells.setProperty("role", "card-flow")
        gap = self._px(TOKENS.spacing.xxs)
        flow = FlowLayout(cells, h_spacing=gap, v_spacing=gap)
        picked = group.selected
        option_id = picked.option_id if picked is not None else 0
        for cell in group.slots:
            flow.addWidget(self._slot_chip(cell, option_id))
        box.addWidget(cells)
        return host

    def _slot_chip(self, cell: TurretSlotCell, option_id: int) -> QLabel:
        """21 格里的**一格**。名字太长就截断，全文在 tooltip 里。"""
        if cell.occupied:
            # ❗ Id 并进格子文字：格子在 FlowLayout 里（一行好几个），**没有"右边"那一格**，
            #    硬挂徽章会把它挤成两行。全文仍在 tooltip 里。
            text = f"{cell.slot} {self._clip(cell.label)} #{cell.turret_id}"
            tooltip = (
                f"炮塔挂载槽 {cell.slot} → {cell.label}（Turrets #{cell.turret_id}）\n"
                "点它 → 中栏换成这座炮塔的 10 列"
            )
            chip = CardNavLabel(text, "ability-chip", tooltip=tooltip)
            chip.activated.connect(lambda t="Turrets", r=cell.turret_id: self._navigate(t, r))
            self._slot_chips.append(chip)
            return chip
        # ❗ 空槽**只写槽号**（不再写「N 空」）：20 个空槽连起来就是一条灰字墙 ——
        #    用户 2026-10-02 圈着那一条问「这里是什么东西？改掉」。
        #    21 格照样一格不落：空槽是暗的，tooltip 说清"它是空的、点它去哪儿"。
        chip = CardNavLabel(f"{cell.slot}", "card-note")
        chip.setToolTip(
            f"炮塔挂载槽 {cell.slot} 是空的（Options.Turret{cell.slot}Id = 0）\n"
            "点它 → 中栏换成这条选项的 46 列（要占这个槽就在那一格上填炮塔 Id）"
        )
        chip.activated.connect(lambda r=option_id: self._navigate("Options", r))
        self._slot_chips.append(chip)
        return chip

    def _ability_chip(self, chip) -> QWidget:
        """只读卡上的能力格子：把"这一条落在 `Abilities` 哪一行"**并进格子文字**。

        ❗ 为什么不是右边挂一个徽章：能力格子是 `FlowLayout` 里的小块（一行好几个），
        **没有"右侧"那一格** —— 塞徽章会把那一排挤成两行。所以并进文字：
        `两栖 #103`。`row_id == 0`（没对上库里那一行）时一个字都不加。

        ❗ **覆写在这里、不改基类**：军械库那张卡的口径是「设计还是原来的好」，
        基类 `UnitCardPanel._ability_chip` 一个字节都不许动（`tests/test_ui_redesign.py`
        与 `test_armory_screen.py` 都盯着那张卡）。
        """
        widget = super()._ability_chip(chip)
        row_id = getattr(chip, "row_id", 0)
        if isinstance(widget, QLabel) and isinstance(row_id, int) and row_id > 0:
            widget.setText(f"{widget.text()} #{row_id}")
        return widget

    @staticmethod
    def _clip(name: str) -> str:
        """格子上的短名（设计稿块 10 的格子就是「7 MK82 SE x1」这种短字）。"""
        return name if len(name) <= CHIP_NAME_LIMIT else f"{name[:CHIP_NAME_LIMIT]}…"

    @property
    def slot_chips(self) -> tuple[QLabel, ...]:
        """21 格那几排的**全部格子**（顺序 = 画出来的顺序；用例按它验"21 个一个不少"）。

        有几个"占了槽的选项"就有几排，每排恰好 21 格 —— 这是用户明确要的那条：
        **21 个炮塔挂载槽全部列出来，一个都不折叠**。
        """
        return tuple(self._slot_chips)

    @property
    def nav_labels(self) -> tuple[QLabel, ...]:
        """卡上**可点的**每一行 / 每一格（用例按文案找"哪一层"，例：以 🗼 开头那一行）。"""
        return tuple(self.findChildren(CardNavLabel))

    # ------------------------------------------------------------------ #
    # 块 4 / 8：单位 › 炮塔 › 武器 › 弹种（四层，四层都能点）
    # ------------------------------------------------------------------ #

    def _turret_section(self, card: UnitCard) -> QWidget:
        """四层链。卡上画的是**当前装着**的那几座炮塔（选项覆盖之后那一套）。

        每一层都可点（点了中栏换成那一行的全部列）：

            单位 Units › 炮塔 Turrets › 武器 Weapons › 弹种 Ammunitions

        ❗ 「＋ / ✕」不长在卡上（卡片只读）：加武器 / 加弹种 / 删携弹行都在左边关系树
        行尾那几个按钮与中栏词条面板上。
        """
        frame, layout = self._frame("detail-card")
        layout.addWidget(_label(f"🗼 炮塔 › 武器 › 弹种（{len(card.turrets)}）", "detail-section"))
        if not card.turrets:
            layout.addWidget(
                _label("这个单位没有炮塔（步兵的武器挂在班组成员上）。", "empty-hint", wrap=True)
            )
            return frame
        hint = "点任意一层 → 中栏换成那一行的全部列"
        layout.addWidget(
            self._nav_row(
                f"🚙 {card.title}",
                "Units",
                card.unit_id,
                # ❗ Id 从 note 里**挪到徽章**上：§6 那条"只出现一次"——同一行两遍就不算数了。
                badge=id_badge(card.unit_id),
                tooltip=f"Units #{card.unit_id} · 点它回本单位的 31 条",
            )
        )
        for turret in card.turrets:
            layout.addWidget(
                self._nav_row(
                    f"🗼 {turret.label}",
                    "Turrets",
                    turret.turret_id,
                    note=turret.note,
                    badge=id_badge(turret.turret_id),
                    tooltip=(
                        f"{turret.full_label}（Turrets #{turret.turret_id}）\n"
                        f"{turret.note} · 点它 → 中栏换成这座炮塔的 10 列"
                    ),
                )
            )
            for weapon in turret.weapons:
                layout.addWidget(
                    self._nav_row(
                        f"🔫 {weapon.label}",
                        "Weapons",
                        weapon.weapon_id,
                        indent=1,
                        badge=id_badge(weapon.weapon_id),
                        tooltip=(
                            f"{weapon.label}（Weapons #{weapon.weapon_id}）\n"
                            "点它 → 中栏换成这门武器的 40 列"
                        ),
                    )
                )
                for ammo in weapon.ammo:
                    layout.addWidget(
                        self._nav_row(
                            f"💥 {ammo.label}",
                            "Ammunitions",
                            ammo.ammo_id,
                            indent=2,
                            note=f"携弹 {ammo.count}",
                            badge=id_badge(ammo.ammo_id),
                            tooltip=(
                                f"{ammo.label}（Ammunitions #{ammo.ammo_id}，67 列）\n"
                                f"点它 → 中栏换成这一行的全部列；本单位的携弹行是"
                                f" WeaponAmmunitions #{ammo.row_id}（{ammo.quantity} 发）"
                            ),
                        )
                    )
        layout.addWidget(
            _label(
                f"{hint}。加武器 / 加弹种 / 删携弹行走左边关系树行尾那几个按钮"
                "（卡片只读，卡上不给按钮）。",
                "card-note",
                wrap=True,
            )
        )
        return frame

    # ------------------------------------------------------------------ #
    # 卡上那几块的顺序（块 5 定：改装槽最前 —— 它决定别的块生效哪一套）
    # ------------------------------------------------------------------ #

    def _section_plan(self, card: UnitCard) -> list[tuple[object, Callable[[], QWidget | None]]]:
        """与基类（军械库那张卡）的差别只有两处，其余照旧：

        1. **改装槽挪到最前面**，并画成「槽 › 互斥选项 + 21 个挂载槽」；
        2. 属性下面插一块「单位 › 炮塔 › 武器 › 弹种」四层链。

        装甲 / 机动 / 传感器 / 运输那几块照旧走基类（它们本来就跟着选项重算）——
        被选项覆盖时 LinkedRow.source 会写一句「来自 X 这个选项」。

        ❗ 每块前面的那个元组是**署名**（这一块从卡上读的字段）。改一格之后
        基类的 _refresh_card_in_place 就拿新旧署名逐块比：没变的块一个控件都不碰。
        加块 / 改块时**必须同时改这里** —— 署名漏一个字段的症状是「那一格改了卡上不动」。
        """
        return [
            (
                (
                    "portrait",
                    card.portrait_key,
                    card.icon_key,
                    card.category,
                    card.kind,
                    card.armor_zones,
                ),
                lambda: self._portrait_section(card),
            ),
            (("stats", card.stats), lambda: self._stats_section(card)),
            (
                (
                    "options",
                    card.card_options,
                    self._unit_id,
                    self._option_writer is not None,
                ),
                lambda: self._options_section(card),
            ),
            (
                ("turrets", card.title, card.unit_id, card.turrets),
                lambda: self._turret_section(card),
            ),
            (
                ("armor", card.armor),
                lambda: self._link_section(
                    "🛡 装甲", (card.armor,) if card.armor else (), "这个单位没有装甲模板行。"
                ),
            ),
            (
                ("mobility", card.mobility),
                lambda: self._link_section(
                    "🚗 机动", (card.mobility,) if card.mobility else (), "这个单位没有机动行。"
                ),
            ),
            (
                ("sensors", card.sensors),
                lambda: self._link_section("📡 传感器", card.sensors, "这个单位没有传感器行。"),
            ),
            (
                ("transports", card.transports),
                lambda: self._link_section("🚚 运输", card.transports, "没有运输配对行。"),
            ),
            (("abilities", card.abilities), lambda: self._abilities_section(card)),
            (
                ("weapons", card.weapons, self._mode, self._expanded),
                lambda: self._weapons_section(card),
            ),
            (("loadout", card.loadout), lambda: self._loadout_section(card)),
        ]

    def _link_row(self, row) -> QWidget:
        """一块里的一行：名字可点（中栏换成那一行），右边跟着"来自哪个选项"。

        设计 §1 第 4 条：卡上能点的一切都通向它的那一行（装甲 / 机动 / 传感器 / 运输
        四块原来只是文字 —— 现在它们与炮塔那条链同一个走法）。
        """
        note = getattr(row, "source", "")
        return self._nav_row(
            row.label,
            row.table,
            row.row_id,
            tooltip=f"{link_tooltip(row)}\n点它 → 中栏换成这一行的全部列",
            note=note,
            badge=card_id_badge(row),
        )

    # ------------------------------------------------------------------ #
    # 校验角标（立绘上那个 "!" —— 它只是个提示，不是可点入口）
    # ------------------------------------------------------------------ #

    def set_validation(self, findings: tuple) -> None:
        """接上"**新增的**校验命中"（原版既有违规不报，见 UnitEditorScreen）。

        与旧的可写卡**同一句文案**（用例按 `校验 N 处 · 第一条` 断言），
        区别是它不再可点 —— 卡片不是导航面了。
        """
        self._validation = tuple(findings or ())
        self.set_findings({self._unit_id: self._validation})

    @property
    def validation_text(self) -> str:
        """立绘那条字（空串 = 什么都没画）。"""
        if not self._validation:
            return ""
        return f"校验 {len(self._validation)} 处 · {self._validation[0].message}"

    def show_unit(self, unit_id: int, specialization_id: int | None = None) -> None:
        """换单位（基类那一条），顺带把角标按新单位重画一遍。"""
        super().show_unit(unit_id, specialization_id)
        self._refresh_badge()

    @property
    def validation_badge(self) -> QLabel:
        """立绘抬头那个 "!"（用例按它验"有问题才画"）。"""
        return self._badge


__all__ = ["CHIP_NAME_LIMIT", "ReadOnlyCardPanel"]
