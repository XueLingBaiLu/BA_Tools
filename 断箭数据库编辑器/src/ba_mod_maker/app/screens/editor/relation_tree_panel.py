"""单位编辑器左栏：**单位关系树**（设计稿 overhaul3.png 那一条）。

三栏的分工（用户 2026-09-30 定）：

    左：关系树（钻取都在一屏里）｜ 中：这一行的全部列 ｜ 右：游戏那张卡（只读）

这一层只做三件事：

1. **画**：把 viewmodel/relation_tree.py 那棵树摆成 QTreeWidget ——
   **两列**：名字（后面紧跟 ×N 与 表名 #Id）｜ 工具（＋ 📄 🗑）；
2. **选**：点任意一行 -> 发 `row_activated(表, 行 Id)`，中栏换成那一行的全部列；
3. **动作**：节点行尾 `＋ / ⧉ / ✕`（以及右键菜单）—— 只发意图，写盘全在外层
   （`EditorContext.add_block_row / remove_rows / clone_row_to`）。

❗ 用户 2026-09-29 口径：原来中间那两列「有 N / 可以 N」**整个删掉**（树上不再有那种
列，也不再有「绿=有、金=可以」两类标签）：数字并进行尾灰字（note），tooltip 只说
「这一组 / 这一行是什么」。颜色只从 app/theme/tokens.py 来。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.pages.base import CARD_BLOCKS
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.relation_tree import (
    RelationTree,
    TreeItem,
    build_relation_tree,
)
from ba_mod_maker.app.viewmodel.unit_card import build_unit_card
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import DEFAULT_LIMIT, RowPicker
from ba_mod_maker.core.edit.refs import foreign_table

#: **三列**：名字（`● 名字 qty · note`）｜ #Id ｜ 工具（＋ 📄 🗑）。
#:
#: ❗ 这里原来有四列（名字 ｜ 有 N ｜ 可以 N ｜ 工具）—— 用户 2026-09-29 口径：
#:    "把「有 *」和「可以 *」去掉"，选的是**整列删掉**。COL_HAVE / COL_CAN 与那两类
#:    标签的颜色映射（绿=有、金=可以）一并清掉了，别再放回来。
#: ❗ **ID 单独一列（2026-10-01 用户口径）**：「左边红圈里面的 SquadMembers #* 太占空间了，
#:    能不能只在右边显示 #* 编号？」—— 名字列只留名字，`#Id` 挪到最右边自己一列
#:    （右对齐、弱化色、定宽），表名只留在 tooltip 里。
#:    这一条**推翻了**上一版"ID 并进名字列"的硬口径（当时怕单独一列被挤出可视区）；
#:    实测口径的反面：并进名字列时它把名字挤没了，单独一列反而更省 —— 列宽只按
#:    **最大 id 位数**算（`_id_column_width`），比 `SquadMembers #113` 短一大截。
COL_NAME = 0
COL_ID = 1
COL_TOOLS = 2

#: `#Id` 那一列左右的留白（右对齐要一点右边距，左边与名字列分开）。
ID_PADDING = 14

#: 建树前的占位宽 —— 真宽度在 refresh() 里按**最大 id 位数**算（`_id_column_width`）。
ID_WIDTH_FALLBACK = 44

#: 行尾那三颗按钮的列宽（一颗 24 + 间距）。
TOOLS_WIDTH = 78

#: 行尾那三颗按钮的**文案**（加一行 / 克隆一份 / 删本单位的关联行）。
#:
#: ❗ **控件与用例共用这一份，谁也别在用例里再写一遍字面量。**
#:    上一次把 `⧉` / `✕` 换成 Emoji 时用例里的字面量没跟上，
#:    click_tool 按文本找不到按钮（返回 False）-> 9 条用例直接红了，而且是"静默"红
#:    （不是报错，是那条动作根本没发生）。要改这几颗按钮长什么样，改这里一处。
TOOL_ADD = "＋"
TOOL_CLONE = "📄"
TOOL_REMOVE = "🗑"

#: 左栏（这一棵树）的最小宽度。
#:
#: 原来是 TOKENS.size.editor_card_width = 520 —— 那个数是给**四列**留的。砍掉
#: 「有 N / 可以 N」两列后名字列 = 可视宽度 - TOOLS_WIDTH，宽度该还一些给中栏/右栏。
#:
#: 实测（离屏，脚本 _work/gui_opt/left-tree_measure.py；样本 Stryker RV=21 与
#: VDV Shturm=645，两棵树最深都到 d3）：
#:   面板 460 -> 可视 427 -> 名字列 349、工具列 78（stretchLastSection 已关，见 __init__）。
#:   判据 = 最深那一层的**核心文字**（名字 ×N + 表名 #Id）整段放得下（行尾小字放不下时
#:   Qt 从右边省略，先吃掉的是它，不算挤掉 ID）。
#:   * 645：最深那一层（改装槽 > 槽 > 选项）在 460 全部放得下；只有一条纯说明行
#:     「步兵没有炮塔：武器挂在班组成员上（见下面那一组）」差 33px（同一句话就在 tooltip 里）。
#:   * 21：除 1 条 40 字的改装选项（Options #1898 = 斯崔克反应装甲模块(SRAT II) +
#:     战利品主动防护系统(Trophy)，它的名字本身就 ~500px）外全部放得下。那一条要放到
#:     面板 662 才不省略 —— 比旧的 520 还宽，不能为它把左栏撑大；它的 ID 在悬停 tooltip 里。
#:   * 再往下调（420 -> 名字列 309）连普通行都开始"名字连着 ID 一起出省略号"，所以取 460。
TREE_MIN_WIDTH = 460

#: 「＋」的候选**手工给名单**的两个块（其余扫整张模板表）：
#: 武器 = 本单位已经挂着的那些；班用武器 = 扫 Weapons 全表（挂给步兵班用）。
_MANUAL_CHOICE_BLOCKS = frozenset({"weapons"})

#: 节点在 `QTreeWidgetItem` 上的数据槽：存这一行的 key（回查 TreeItem 用）。
_KEY_ROLE = Qt.ItemDataRole.UserRole


def _name_text(node: TreeItem) -> str:
    """名字那一列的字：`● 名字 qty · note`（● = 当前生效）—— **不含 ID**。

    用户 2026-10-01 改口：「SquadMembers #* 太占空间了，能不能只在右边显示 #* 编号？」
    → ID 挪到右边的 `COL_ID` 列（见 `TreeItem.id_short`），表名只留在 tooltip 里。
    ❌ 旧口径（"ID 全部显示：表名 #Id 写在名字后面"，2026-09-29 定的）**已作废**，
       别再拼回来 —— 那条把名字列撑满，用户报的就是它。
    """
    return ("● " if node.selected else "") + node.display


def _id_column_width(tree: RelationTree, metrics) -> int:
    """`#Id` 那一列的宽度：按**最大 id 位数**算（`#` + 数字），不写死像素。

    这一列要刚好放下最长的那个号 —— 多一个像素都是从名字列抢的（窄左栏最缺横向
    空间）。留白只给右对齐的一点边距。
    """
    digits = max((len(str(item.row_id)) for item in tree.flatten() if item.has_row), default=1)
    return metrics.horizontalAdvance("#" + "0" * digits) + ID_PADDING


class RelationTreePanel(QFrame):
    """关系树控件。外面（MainEditorPage）只接信号，不碰里面的控件。"""

    #: 点了树上的一行（参数：表名, 行 Id）—— 中栏换成那一行的全部列。
    row_activated = Signal(str, int)
    #: 点了某一组的「＋」（参数：块名, 目标行 Id, 第二行 Id）—— 走 EditorContext.add_block_row。
    add_requested = Signal(str, int, int)
    #: 点了某一行的「⧉ 克隆一份」（参数：模板表, 行 Id, 本单位指着它的那一格）。
    clone_requested = Signal(str, int, object)
    #: 点了某一行的「✕」（参数：要删的关联行那几格, 这一项在树上的显示名）。
    #: ❗ 两个参数是**照着** EditorPage._remove_card_rows 的签名来的（那一层接它）。
    remove_requested = Signal(object, str)
    #: 「选中这一项」（参数：改装槽 Id, 选项 Id）—— 走 Options.IsDefault（槽内互斥）。
    select_option_requested = Signal(int, int)
    #: 一句给状态栏的话（"没有可加的目标"这类解释，不弹窗）。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # ❗ 这里原来还有一句 setProperty("role", "relation-tree")，而 QSS 里
        #    **没有任何选择器**用它（只有下面那棵树是 relation-tree-view）——
        #    设了 role 就得有选择器，否则是个死属性（守卫见
        #    tests/test_ui_redesign.py::test_every_role_set_in_code_has_a_selector）。
        # 左栏现在只要装得下"名字 + ×N + 表名 #Id + 三颗行尾按钮"（4 列 -> 2 列），
        # 最小宽度从 TOKENS.size.editor_card_width(520) 降到 TREE_MIN_WIDTH，宽度还给中栏。
        self.setMinimumWidth(TREE_MIN_WIDTH)

        self._instance = None
        self._unit_id = -1
        self._data = None
        self._tree: RelationTree | None = None
        #: key -> 模型节点（画按钮、回查动作用）。
        self._nodes: dict[str, TreeItem] = {}
        #: key -> 树上的项（用例与高亮用）。
        self._items: dict[str, QTreeWidgetItem] = {}
        #: 上一次画出来的**结构署名**（见 _shape）—— refresh() 拿它判"要不要整套重建"。
        #: 空元组 = 还没画过（或刚 clear 过）：那一定走重建。
        self._shape_sig: tuple = ()
        #: 行尾那几颗按钮的挂载点：(项, 控件)。
        #: ❗ `setItemWidget` 必须等**项已经插进视图之后**再调 —— 先挂后插会丢
        #:    （实测：按钮一个都不出现，`itemWidget` 返回 None）。所以先攒起来。
        self._pending_tools: list[tuple[QTreeWidgetItem, QWidget]] = []
        #: 中栏换了行 -> 这里跟着高亮；高亮又发 row_activated 会绕回来，用这个闸拦住。
        self._syncing = False
        #: 现在展开着哪些节点（key）。**None = 还没建过树** —— 第一次只展开根。
        #: 用户口径：折叠栏**默认全收起**（2026-09-29 实机反馈），所以不再 expandAll()。
        self._expanded: set[str] | None = None
        #: 上一次选中的是哪个 key —— 只有**换了新行**才把它那条路展开
        #: （不然"用户手动收起"会被每一次自动刷新顶回去）。
        self._selected_key = ""
        #: **用户实际点中**的那一行的 key（`_on_current_changed` 里记，程序化选中不记）。
        #: 同一个 (表, 行 Id) 在树上会出现两次（改装槽引用路径 + 组里规范位置），
        #: 有它才能"点哪条就停哪条"（见 select_row）。
        self._clicked_key = ""
        self._picker: RowPicker | None = None
        self._picker_block = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.xs)

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.sm)
        self._title = QLabel("单位关系")
        self._title.setProperty("role", "entries-title")
        head.addWidget(self._title)
        self._subtitle = QLabel("")
        self._subtitle.setProperty("role", "entries-subtitle")
        head.addWidget(self._subtitle)
        head.addStretch(1)
        self._expand = QPushButton("收起全部")
        self._expand.setProperty("variant", "ghost")
        self._expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand.clicked.connect(self._toggle_expand)
        head.addWidget(self._expand)
        root.addLayout(head)

        self._widget = QTreeWidget()
        self._widget.setProperty("role", "relation-tree-view")
        self._widget.setColumnCount(3)  # 名字 ｜ #Id ｜ 工具（有 / 可以 两列已整列删掉）
        self._widget.setHeaderHidden(True)
        self._widget.setRootIsDecorated(True)
        self._widget.setUniformRowHeights(True)
        # 缩进收窄：这一棵树最深有 5 层（改装槽 > 槽 > 选项），默认缩进会把最深那几行
        # 的名字整列吃掉。
        self._widget.setIndentation(14)
        self._widget.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._widget.customContextMenuRequested.connect(self._open_menu)
        self._widget.currentItemChanged.connect(self._on_current_changed)
        # 用户手动展开/收起也要记下来（否则下一次 refresh 重建就把展开态吃了）。
        self._widget.itemExpanded.connect(self._remember_expanded)
        self._widget.itemCollapsed.connect(self._remember_expanded)
        header = self._widget.header()
        # ❗ 一定要关掉"最后一列自动拉伸"（QTreeView 的默认值是开的）：开着的话名字那一列
        #    只能分到一半宽度，另一半白送给只放两三颗按钮的工具列。关掉之后名字列 =
        #    可视宽度 - TOOLS_WIDTH，深层的"表名 #Id"才留得住。
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_TOOLS, QHeaderView.ResizeMode.Fixed)
        self._widget.setColumnWidth(COL_ID, ID_WIDTH_FALLBACK)
        self._widget.setColumnWidth(COL_TOOLS, TOOLS_WIDTH)
        root.addWidget(self._widget, 1)

        #: 「＋」就地冒出来的候选列表（与卡片那边同一套：**不弹对话框**）。
        self._picker_host = QWidget()
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(0, 0, 0, 0)
        self._picker_layout.setSpacing(0)
        self._picker_host.setVisible(False)
        root.addWidget(self._picker_host)
        self.clear()

    # ------------------------------------------------------------------ #
    # 接数据
    # ------------------------------------------------------------------ #

    def set_context(self, instance, unit_id: int, data) -> None:
        """进单位：接上实例 + 这张 CardData（与卡片共用一份，别各建一份）。"""
        self._instance = instance
        self._unit_id = int(unit_id)
        self._data = data
        self.close_picker()
        self.refresh()

    def clear(self) -> None:
        self._tree = None
        self._nodes = {}
        self._items = {}
        self._shape_sig = ()
        self._expanded = None
        self._selected_key = ""
        self._widget.clear()
        self._title.setText("单位关系")
        self._subtitle.setText("")

    def refresh(self) -> None:
        """重画整棵树（结构可能变了：插了行 / 删了行 / 换了选项）。

        选中态按**中栏那一行**恢复（外层 set_focus 时会调 select_row）。

        ❗ **署名没变就只改字**（2026-10-01，AGENTS §0.7）：老写法无条件
        `clear()` + 全套重建 —— 实测 **27.2ms**（其中建模型只要 3.0ms，剩下 24ms
        全在造 QTreeWidgetItem 与 99 个行尾按钮宿主），可"改一格"时这棵树的结构
        一个字都没变：重建的后果就是用户看到的那一次闪，还要额外兜展开态与滚动位置。
        判据 = **结构署名**（见 `_shape`）：key 序列 + 每个节点画出来的按钮配方 +
        子节点个数。署名一样 -> 把名字 / #Id / tooltip 就地写回去（`_decorate_item`），
        一个控件都不删不建；署名变了才整套重建，**并且保住滚动值**（先例：
        `docs/交接-2026-09-30-夜-实机反馈批.md:18`）。
        """
        if self._instance is None or self._unit_id < 0 or self._data is None:
            self.clear()
            return
        tree = build_relation_tree(self._instance, self._unit_id, data=self._data)
        if tree is None:
            self.clear()
            return
        self._tree = tree
        if self._items and self._shape(tree) == self._shape_sig:
            # 结构一个字没变：只把"字"改掉 ——
            # 控件不删不建，展开态、滚动位置、当前选中项原封不动。
            self._nodes = {node.key: node for node in tree.flatten()}
            for node in tree.flatten():
                item = self._items.get(node.key)
                if item is not None:
                    self._decorate_item(item, node)
            self._widget.setColumnWidth(COL_ID, _id_column_width(tree, self._widget.fontMetrics()))
            self._title.setText("单位关系")
            self._subtitle.setText(f"{tree.title} · {tree.subtitle}")
            return
        self._nodes = {}
        self._items = {}
        self._syncing = True
        # 重建前先记下滚动位置：不记的话树会"跳回顶上"（AGENTS §0.7 第二条先例）。
        vbar = self._widget.verticalScrollBar().value()
        hbar = self._widget.horizontalScrollBar().value()
        try:
            self._widget.clear()
            self._pending_tools = []
            for root_item in tree.roots:
                self._widget.addTopLevelItem(self._build_item(root_item))
            # 项已经在视图里了，这时候挂行尾按钮才留得住。
            for item, tools in self._pending_tools:
                self._widget.setItemWidget(item, COL_TOOLS, tools)
            self._pending_tools = []
            # `#Id` 列的宽度按这棵树里**最长的那个号**算（每次重建都跟着数据走）。
            self._widget.setColumnWidth(COL_ID, _id_column_width(tree, self._widget.fontMetrics()))
            self._restore_expanded()
            self._sync_expand_text()
            self._widget.verticalScrollBar().setValue(vbar)
            self._widget.horizontalScrollBar().setValue(hbar)
        finally:
            self._syncing = False
        self._shape_sig = self._shape(tree)
        self._title.setText("单位关系")
        self._subtitle.setText(f"{tree.title} · {tree.subtitle}")

    # ------------------------------------------------------------------ #
    # 结构署名（refresh 判"要不要整套重建"的**唯一**判据）
    # ------------------------------------------------------------------ #

    def _shape(self, tree: RelationTree) -> tuple:
        """这棵树"长什么样"的署名 —— **只含结构，不含字**。

        含：每个节点的 key（先序 —— 顺序也算）、kind、有没有真实行、子节点个数，
        以及**行尾画哪几颗按钮**（`_tool_specs` 的文字 + tooltip：tooltip 里嵌着
        这一行的名字，所以"名字变了"也该重建，不然 hover 上还写着旧名字）。
        **不含**名字 / #Id / 数量 / 灰色小字 —— 那些是"字"，署名相同时就地写回。
        """
        return tuple(
            (
                node.key,
                node.kind,
                node.has_row,
                len(node.children),
                self._tool_specs(node),
            )
            for node in tree.flatten()
        )

    # ------------------------------------------------------------------ #
    # 展开 / 收起（用户口径：折叠栏**默认全收起**）
    # ------------------------------------------------------------------ #

    def _expanded_keys(self) -> set[str]:
        return {key for key, item in self._items.items() if item.isExpanded()}

    def expanded_keys(self) -> tuple[str, ...]:
        """现在展开着的节点 key（先序）—— 用例验「展开了哪一条路」用它。"""
        order = {key: index for index, key in enumerate(self._items)}
        return tuple(sorted(self._expanded_keys(), key=lambda key: order.get(key, 0)))

    def _remember_expanded(self, *_args) -> None:
        self._expanded = self._expanded_keys()
        self._sync_expand_text()

    def _restore_expanded(self) -> None:
        """重建之后把展开态放回去。

        ❗ **第一次**（_expanded is None）只展开根节点：单位那一行下面的十个分组
        全都收起 —— 这是用户的口径（"所有折叠栏默认收起"）。以后每次重画都恢复
        用户当时的展开态，别把他手动展开的东西吃掉。
        """
        if self._expanded is None:
            self._widget.expandToDepth(0)
        else:
            for key in self._expanded:
                item = self._items.get(key)
                if item is not None:
                    item.setExpanded(True)
        self._expanded = self._expanded_keys()

    # ------------------------------------------------------------------ #
    # 画
    # ------------------------------------------------------------------ #

    def _build_item(self, node: TreeItem) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, _KEY_ROLE, node.key)
        self._decorate_item(item, node)
        self._nodes[node.key] = node
        self._items[node.key] = item
        tools = self._tools_widget(node)
        if tools is not None:
            self._pending_tools.append((item, tools))
        for child in node.children:
            item.addChild(self._build_item(child))
        return item

    def _decorate_item(self, item: QTreeWidgetItem, node: TreeItem) -> None:
        """把一个节点的**字**写到树上已经存在的那个项里。

        ❗ **画新项与"就地改字"共用这一份**（refresh 里署名没变那条路）：分成两份写
        迟早只改一边 —— 就地那条路漏一个字段，表现就是"控件保住了、显示还是旧的"
        （AGENTS §0.7 判据的第二条）。所以这里只写"字"（名字、#Id、tooltip、灰字色），
        结构（key / 子节点 / 行尾按钮）由调用方保证。
        """
        item.setText(COL_NAME, _name_text(node))
        # 右侧那一列：只有 `#Id`（表名进 tooltip），右对齐 + 弱化色。
        # 抬头 / 说明行没有行 -> 空串（用户口径：编号只在有行的地方显示）。
        item.setText(COL_ID, node.id_short)
        item.setTextAlignment(COL_ID, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(COL_ID, QBrush(QColor(TOKENS.color.text_secondary)))
        if node.has_row:
            tip = f"{node.id_text} —— 点它看这一行的全部列"
        else:
            # 抬头 / 说明行：tooltip 说的是"这一组是什么"。
            tip = node.hint or node.display
        # 两列都挂同一句：鼠标停在编号那一列也要能读到**表名**（名字列已经不带表名了）。
        item.setToolTip(COL_NAME, tip)
        item.setToolTip(COL_ID, tip)
        if node.kind == "note":
            item.setForeground(COL_NAME, QBrush(QColor(TOKENS.color.text_disabled)))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)

    def _tools_widget(self, node: TreeItem) -> QWidget | None:
        """行尾那三颗：＋（这一组加一行）/ 📄（克隆一份）/ 🗑（删本单位的关联行）。

        ❗ 后两颗用 **Emoji**（用户 2026-09-30：「克隆图标和删除图标看着比较简陋，用 Emoji 吧」）——
        原来那颗 `⧉`（U+29C9）在真机上就是一个小方块，`✕` 也太素。
        一颗都没有就不画（宁可不画，也不画一个点了没反应的按钮）。
        """
        specs = self._tool_specs(node)
        if not specs:
            return None
        host = QWidget()
        # ❗ 这个宿主控件**必须透明**：全局那条 `QWidget { background-color: bg-base }`
        #    会给它刷一层比树行底色更深的方块（实测：用户报的"下面的背景变深太突兀"）。
        #    role 见 theme/qss.py 的 [row-tools] 块。
        host.setProperty("role", "row-tools")
        box = QHBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.xxs)
        for text, tip, action in specs:
            box.addWidget(self._tool(text, tip, node.key, action))
        box.addStretch(1)
        return host

    def _tool_specs(self, node: TreeItem) -> tuple[tuple[str, str, str], ...]:
        """这一行**行尾画哪几颗**按钮：`((按钮文字, tooltip, 动作名), ...)`。

        ❗ **画按钮（_tools_widget）与"结构署名"（_shape）共用这一份** ——
        两处各写一遍的话，改了这一行的名字之后 tooltip 还写着旧名字，而署名也判不出
        "该重建"。文本口径见 _tools_widget 的 docstring。
        """
        specs: list[tuple[str, str, str]] = []
        if node.can_add:
            specs.append(
                (
                    TOOL_ADD,
                    (
                        # 武器行：这颗「＋」只做一件事 —— 给这一门枪加一种弹。
                        f"给「{node.label}」加一种弹"
                        if node.add_first_id
                        else f"给「{node.label}」加一行（{self._block_label(node.add_block)}）"
                    ),
                    "add",
                )
            )
        # ❗ 一组的第二块（例：「班组成员 / 班用武器」里的**班用武器**）从前只挂在右键
        #    菜单上 —— 用户 2026-10-01 报「我还要能自己增删武器的注册」，就是找不到它。
        #    现在给它**自己一颗按钮**（emoji 取那一块的名字前缀），不用翻右键。
        if node.alt_add_block:
            label = self._block_label(node.alt_add_block)
            specs.append((label.split()[0], f"＋ 加一行（{label}）", "add_alt"))
        if node.can_clone:
            specs.append((TOOL_CLONE, "复制一份给本单位专用", "clone"))
        if node.can_remove:
            specs.append((TOOL_REMOVE, "删掉本单位的这一行（Ctrl+Z 能撤回来）", "remove"))
        return tuple(specs)

    @staticmethod
    def _block_label(block: str) -> str:
        """块名 -> 界面上的名字（没有就走原样，别把内部名漏给用户）。"""
        spec = CARD_BLOCKS.get(block)
        return spec.label if spec is not None else block

    def _tool(self, text: str, tip: str, key: str, action: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", "row-tool")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tip)
        button.setFixedWidth(24)  # Emoji 比 ⧉/✕ 宽一点，22 会把 📄 挤掉
        button.clicked.connect(lambda _checked=False, k=key, a=action: self._run_action(k, a))
        return button

    # ------------------------------------------------------------------ #
    # 动作（只发意图，不写数据）
    # ------------------------------------------------------------------ #

    def _run_action(self, key: str, action: str) -> None:
        node = self._nodes.get(key)
        if node is None:
            return
        if action == "add":
            # 武器行那一颗「＋」把"哪门枪"一起带下去（add_first_id），
            # 于是一个人只需要挑弹种（用户 2026-10-01：「左边没有给武器加弹药的选项」）。
            self.request_add(node.add_block, node.add_first_id, node.label)
        elif action == "add_alt":
            self.request_add(node.alt_add_block)
        elif action == "clone":
            if node.clone_row is not None and node.link is not None:
                self.clone_requested.emit(node.clone_row.table, node.clone_row.row_id, node.link)
        elif action == "remove":
            self.remove_requested.emit(tuple(node.remove_refs), node.label)

    def request_add(self, block: str, first_id: int = 0, first_label: str = "") -> bool:
        """某一组的「＋」：要挑模板就**就地**冒候选列表，不用挑的直接发意图。

        `first_id` 非 0 = 双列块的第一列**已经定了**（武器行：枪就是这一门），
        候选列表把那一列钉死、只留第二列给人挑（见 RowPicker.lock_first）。

        返回"有没有接下来"（False = 这个块没得加，外面不用等信号）。
        """
        spec = CARD_BLOCKS.get(block)
        if spec is None or self._instance is None:
            return False
        if spec.copy_member or not spec.target:
            # 班组成员（照本单位现有成员复制一名）/ 改装槽（插的就是它自己）。
            self.add_requested.emit(block, 0, 0)
            return True
        choices = self._choices_for(block)
        if choices is not None and not choices:
            self.status_message.emit(f"没有可加的候选：{spec.target} 里一条都没有")
            return False
        # ❗ 第二列要的是**表名**：CARD_BLOCKS.second_column 是列名（AmmunitionId），
        #    得先过 foreign_table 换成它指向的那张表（Ammunitions）。
        second_table = foreign_table(spec.second_column) if spec.second_column else None
        picker = RowPicker(
            self._instance,
            spec.target,
            second_table=second_table,
            choices=choices,
            limit=max(DEFAULT_LIMIT, len(choices)) if choices else DEFAULT_LIMIT,
            parent=self._picker_host,
        )
        if spec.second_column:
            picker.pair_picked.connect(self._on_pair_picked)
        else:
            picker.picked.connect(self._on_picked)
        picker.cancelled.connect(self.close_picker)
        self.close_picker()
        self._picker = picker
        self._picker_block = block
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        picker.start()
        if first_id and spec.second_column:
            # 武器行那颗「＋」：枪已经定了，别叫人再挑一遍（见 RowPicker.lock_first）。
            picker.lock_first(int(first_id), first_label)
        return True

    def _choices_for(self, block: str) -> tuple[tuple[int, str], ...] | None:
        """「＋」的候选：手工名单的块（加武器 = 本单位已有的枪）走这里，其余 None。"""
        if block not in _MANUAL_CHOICE_BLOCKS or self._data is None:
            return None
        card = build_unit_card(self._data, self._unit_id)
        if card is None:
            return ()
        if block == "weapons":
            return tuple((weapon.weapon_id, weapon.label) for weapon in card.weapons)
        return None


    def _link_value(self, link) -> int:
        """这一格现在指着哪一行（读实例里的原值；读不到给 0 = 不排除任何一条）。"""
        if self._data is None or link.table not in self._instance.table_names:
            return 0
        rows = self._data.t(link.table)
        row = rows.by_id(link.row_id)
        if row is None or link.column not in rows.table.columns:
            return 0
        value = row[rows.index_of(link.column)]
        return value if isinstance(value, int) and not isinstance(value, bool) else 0


    def _on_picked(self, row_id: int) -> None:
        block = self._picker_block
        self.close_picker()
        self.add_requested.emit(block, int(row_id), 0)

    def _on_pair_picked(self, first_id: int, second_id: int) -> None:
        block = self._picker_block
        self.close_picker()
        self.add_requested.emit(block, int(first_id), int(second_id))

    def close_picker(self) -> None:
        if self._picker is not None:
            picker = self._picker
            self._picker = None
            self._picker_layout.removeWidget(picker)
            # ❗ removeWidget 只是从布局里拿掉，控件**还是可见的** —— 直接
            #    setParent(None) 会被 Qt 映射成顶层小窗（见 widgets/detach.py）。
            detach(picker)
        self._picker_host.setVisible(False)

    # ------------------------------------------------------------------ #
    # 选中与右键
    # ------------------------------------------------------------------ #

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous=None) -> None:
        if self._syncing or current is None:
            return
        node = self._nodes.get(current.data(0, _KEY_ROLE))
        if node is None:
            return
        # ❗ 记下"用户点的是哪一条"：同一个 (表, 行 Id) 树上有两条，select_row 要靠它
        #    把高亮/展开留在用户点的那一条上（程序化选中走 _syncing，不会覆盖它）。
        self._clicked_key = node.key
        if node.has_row:
            self.row_activated.emit(node.table, node.row_id)

    def _clicked_node(self, table: str, row_id: int) -> TreeItem | None:
        """**用户刚点的那一行**是不是就是 (table, row_id)：是就返回它，否则 None。"""
        if not self._clicked_key:
            return None
        node = self._nodes.get(self._clicked_key)
        if node is None or node.table != table or node.row_id != row_id:
            return None
        return node

    def select_row(self, table: str, row_id: int) -> bool:
        """中栏换了行 -> 树上跟着高亮。找不到就不动（返回 False）。

        走下层的**模型**找，不按显示文字找 —— 名字里现在同时有名字、ID 与行尾小字。

        ❗ **用户点的那一条优先**：同一个 (表, 行 Id) 在树上会出现两次 —— 改装槽选项
        下面那条槽位行（`mods/…/slot/{n}`）与组里那一行（`turrets/{id}`）。用户点的
        是哪一条，高亮和展开就必须落在那一条上：

        - 点炮塔组那一行 -> 展开炮塔组（不能跑去展开改装件：用户报的就是这个）；
        - 点改动槽里那条槽位行 -> 停在槽位行（不能被 find 的规范位置抢回去）。

        没有点选记录（或记录的不是这一行，例：↗ 跳转 / 检索跳转 / 进编辑器）时
        退回 `RelationTree.find()` —— 它优先返回规范位置。
        """
        if self._tree is None:
            return False
        node = self._clicked_node(table, row_id) or self._tree.find(table, row_id)
        if node is None:
            return False
        item = self._items.get(node.key)
        if item is None:
            return False
        self._select_item(item, node.key)
        return True

    def select_key(self, key: str) -> bool:
        item = self._items.get(key)
        if item is None:
            return False
        self._select_item(item, key)
        return True

    def _select_item(self, item: QTreeWidgetItem, key: str = "") -> None:
        self._syncing = True
        try:
            if key and key != self._selected_key:
                # 换了**新的一行**：把它到根的这条路展开 —— 收起状态下换了行，
                # 不展开就等于"中栏换了、树上什么也看不见"。
                # 只在换行时做：不然用户手动收起会被每次自动刷新顶回去。
                self._selected_key = key
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                if item.childCount():
                    # ❗ 选中的这一行**自己**也展开：用户点一门枪，要的就是"下面
                    #    直接露出它挂的每一种弹"（原话："左边的武器下，还是没有
                    #    显示它拥有的弹药"）。这不是"默认全展开"—— 只有被选中的
                    #    那一行会开，其余分组仍是收起的（口径见测试
                    #    test_the_tree_starts_collapsed）。
                    item.setExpanded(True)
            self._widget.setCurrentItem(item)
            self._widget.scrollToItem(item)
        finally:
            self._syncing = False

    def activate_key(self, key: str) -> bool:
        """用例用：当作用户点了某一行（发 row_activated，不走 Qt 的选中事件）。"""
        node = self._nodes.get(key)
        if node is None or not node.has_row:
            return False
        self.row_activated.emit(node.table, node.row_id)
        return True

    def _open_menu(self, point) -> None:
        item = self._widget.itemAt(point)
        if item is None:
            return
        node = self._nodes.get(item.data(0, _KEY_ROLE))
        if node is None:
            return
        menu = QMenu(self)
        if node.has_row:
            menu.addAction(
                "看这一行的全部列",
                lambda: self.row_activated.emit(node.table, node.row_id),
            )
        if self._modification_of(node):
            menu.addAction(
                "选中这一项（写 Options.IsDefault）",
                lambda key=node.key: self.select_option(key),
            )
        # ⛔ 2026-10-19 用户命令：「🔁 换一个…」已删除（AGENTS §0.10 只有 新增/删除/修改）。
        for block in (node.add_block, node.alt_add_block):
            if block:
                menu.addAction(
                    f"＋ 加一行（{CARD_BLOCKS[block].label if block in CARD_BLOCKS else block}）",
                    lambda _checked=False, name=block: self.request_add(name),
                )
        if node.can_clone:
            menu.addAction("⧉ 克隆一份", lambda: self._run_action(node.key, "clone"))
        if node.can_remove:
            menu.addAction("✕ 删掉本单位的这一行", lambda: self._run_action(node.key, "remove"))
        if menu.actions():
            menu.exec(self._widget.viewport().mapToGlobal(point))

    def select_option(self, key: str) -> bool:
        """「选中这一项」：改装槽里的某个选项 -> 写 Options.IsDefault（槽内互斥）。

        盘口在 options_page.select_slot_option 那一份里（外层接这个信号），
        这里只把 (槽 Id, 选项 Id) 挑出来。
        """
        node = self._nodes.get(key)
        if node is None:
            return False
        modification_id = self._modification_of(node)
        if not modification_id:
            return False
        self.select_option_requested.emit(modification_id, node.row_id)
        return True

    @staticmethod
    def _modification_of(node: TreeItem) -> int:
        """这一项是不是某个改装槽下的选项？是就返回槽 Id（右键"选中这一项"用它）。"""
        if node.table != "Options":
            return 0
        key = node.key
        if not key.startswith("mods/"):
            return 0
        parts = key.split("/")
        try:
            return int(parts[1])
        except (IndexError, ValueError):  # pragma: no cover - key 由模型生成
            return 0

    # ------------------------------------------------------------------ #
    # 展开 / 收起
    # ------------------------------------------------------------------ #

    def _toggle_expand(self) -> None:
        if self._expand.text() == "收起全部":
            self._widget.collapseAll()
        else:
            self._widget.expandAll()
        self._sync_expand_text()

    def _sync_expand_text(self) -> None:
        """按钮文案跟着**真实展开态**走（默认收起时它是「展开全部」）。"""
        self._expand.setText("收起全部" if self._expanded_keys() else "展开全部")

    # ------------------------------------------------------------------ #
    # 只读访问（用例与诊断用）
    # ------------------------------------------------------------------ #

    @property
    def tree(self) -> RelationTree | None:
        return self._tree

    @property
    def current_key(self) -> str:
        """现在高亮的那一行 key（没有就是空串）—— 用例与诊断用。"""
        item = self._widget.currentItem()
        return str(item.data(0, _KEY_ROLE)) if item is not None else ""

    @property
    def widget(self) -> QTreeWidget:
        return self._widget

    @property
    def picker(self) -> RowPicker | None:
        return self._picker

    @property
    def picker_visible(self) -> bool:
        return self._picker is not None and self._picker_host.isVisibleTo(self)

    def item(self, key: str) -> QTreeWidgetItem | None:
        return self._items.get(key)

    def node_for(self, table: str, row_id: int) -> TreeItem | None:
        """按 (表, 行 Id) 找模型节点（外层点开某一行时要读它的 link）。"""
        return self._tree.find(table, row_id) if self._tree is not None else None

    def item_text(self, key: str, column: int = COL_NAME) -> str:
        item = self._items.get(key)
        return item.text(column) if item is not None else ""

    def tool_buttons(self, key: str) -> tuple[QPushButton, ...]:
        """某一行的行尾按钮（用例按文字找 ＋ / ⧉ / ✕）。"""
        item = self._items.get(key)
        if item is None:
            return ()
        host = self._widget.itemWidget(item, COL_TOOLS)
        if host is None:
            return ()
        return tuple(host.findChildren(QPushButton))

    def click_tool(self, key: str, text: str) -> bool:
        """用例用：点某一行的某一颗行尾按钮。"""
        for button in self.tool_buttons(key):
            if button.text() == text:
                button.click()
                return True
        return False

    def visible_keys(self) -> tuple[str, ...]:
        """现在树上画出来的 key（先序）——用例验顺序/分组用它。"""
        out: list[str] = []

        def walk(item: QTreeWidgetItem) -> None:
            out.append(item.data(0, _KEY_ROLE))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self._widget.topLevelItemCount()):
            walk(self._widget.topLevelItem(index))
        return tuple(out)


__all__ = [
    "COL_NAME",
    "COL_ID",
    "COL_TOOLS",
    "TOOLS_WIDTH",
    "TOOL_ADD",
    "TOOL_CLONE",
    "TOOL_REMOVE",
    "TREE_MIN_WIDTH",
    "RelationTreePanel",
]
