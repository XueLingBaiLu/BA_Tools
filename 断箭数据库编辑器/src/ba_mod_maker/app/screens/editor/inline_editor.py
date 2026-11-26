"""「点开一格」的就地编辑 —— **就在那一行里改**（单位编辑器右栏用）。

用户口径（三轮，越改越短）：

> "我修改的时候不要弹窗口……根本不需要修改的时候，还有这么大个窗口。"
> "我要的是直接在画红圈的地方改，别出这种烦人的窗口，改了后自动保存。"
> "能不能不要搞这种√和×。"

所以这里**只有一个控件**，就长在那一格的位置上：

    ▸ 改装槽 ID（主键）   [ 68        ]              Modifications#68 · Id

- **没有确认/取消按钮**：改了就算数（外层立刻把值写进缓存）；回车或点到别处即收工，Esc 撤手；
- 输入框占的就是原来那个值的位置（同一行、同样缩进），行高不变；
- 注释不进浮层（doc 只挂在**词条名**那一格的悬浮提示里，见 row_entries.py）；
- 外键是**可编辑下拉**（打字筛、显示名字、写回 Id）。

改完由 UnitEditorScreen 写进**缓存**（Changeset）—— 不碰盘：落盘只走顶栏
「💾 保存」。这个控件只负责"取到新值"。
"""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.widgets.detach import hide_roll_effects
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.cells import FieldCell, parse_cell_text
from ba_mod_maker.core.edit.dictionary import (
    CONTROL_BOOL,
    CONTROL_ENUM,
    CONTROL_FOREIGN,
    CONTROL_MASK,
    CONTROL_RESOURCE,
    decode,
    field_info,
)
from ba_mod_maker.core.edit.foreign import (
    LOOKUP_NAME_COLUMNS,
    NameHit,
    fold_confusables,
    match_text,
    resource_key_candidates,
    search_rows,
)
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import BaModMakerError, TableError

#: 外键下拉一次装多少行（界面是"打字筛"，装全表才能筛得到）。
FOREIGN_CHOICES = 4000
#: 候选重建之后多久扫一遍 Qt 那个卷动壳（`QRollEffect`）。正常动画 ~200ms ——
#: 扫太早会把动画打断，太晚用户已经看见黑框了。
ROLL_EFFECT_SWEEP_MS = 300

#: 候选显示文本 = 名字 + 两个空格 + `#Id`（与关系树「表名 #Id」同一口径）。
#: ❗ 为什么要带 Id：实测那 5 门武器的 HUDName **一模一样**（都是「2А72 30mm」），
#:    只显示名字的话人在下拉里根本分不出 T1 / T2 / T3 / Gadyuka —— 挑哪一条全靠猜。
#:    只改**下拉里**的显示：`itemData` 仍是行 Id，单元格里那一行显示的名字也不动。
def _choice_text(hit) -> str:
    return f"{hit.name}  #{hit.row_id}"


#: 候选文本末尾那个 `#Id`（解析时要能把它摘掉再比名字）。
_CHOICE_ID = re.compile(r"\s+#\d+$")


def _choice_name(text: str) -> str:
    """候选显示文本 -> 名字那一半（摘掉结尾的 `#Id`）。"""
    return _CHOICE_ID.sub("", text)


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label

#: 位掩码折叠态：一位都没选时框里写这个（与「空值写（空）」同一套口径）。
MASK_NONE_TEXT = "（无）"
#: 折叠态里把选中的那几位连起来的那一个分隔符。
MASK_SEPARATOR = " · "


class _MaskSelector(QWidget):
    """**所有位掩码列共用**的一个编辑部件：一行摘要 ＋ 点开在下方展开的勾选列表。

    用户 2026-11-11 三句话决定了它（原话）：

    - 「可瞄准目标这里，还是做成列表式的，不然被遮挡住了，无法正常修改」
      → 选了「行内下拉 ＋ 勾选列表」这一形态；
    - 「设计要统一，不要单独只改这一项」
      → 所以这里**没有任何一个字段的特例**：全库 4 个 `kind=mask` 的列
        （`Ammunitions.TargetType` 10 位 / `Units.Type` 5 位 /
        `Ammunitions.UiOptions` 1 位 / `Abilities.SupplyTargetsMask` 1 位）
        走的是同一个部件、同一套交互、同一套样式；
    - 之前那一版把 10 个复选框**竖排铺满整格**（右栏只有一格宽，直接吃掉半屏），
      作废 —— 折叠态只占一行。

    形态::

        [ 地面 · 载具 · 飞机        ▾ ]    <- 折叠：就是值那一格（role=entry-value）
        ┌───────────────────────────┐
        │ ☑ 地面   ☑ 载具   ☐ 步兵  │    <- 点开：在**下方**展开，不弹独立浮窗
        └───────────────────────────┘

    ❗ 勾选**不立刻落库** —— 与文本框 / 下拉同一条路：点别处时 `InlineEditor` 才
       `value()` 读一次并收工（"改完就算数"那个手势只有一处）。
    """

    #: 展开 / 收起状态变了（参数：现在是不是展开）。外层据此重算编辑器高度。
    expanded_changed = Signal(bool)

    def __init__(self, cell: FieldCell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._unit_height = TOKENS.size.row_height
        self._expanded = False
        self._rows: list[tuple[int, str, QCheckBox]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOKENS.spacing.xxs)

        self.button = QPushButton()
        # ❗ 与「值那一格」同一个 role：折叠时这一格长得与别的字段**一模一样**（这就是统一）。
        self.button.setProperty("role", "entry-value")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self.toggle)
        root.addWidget(self.button)

        self.list_host = QWidget()
        rows = QVBoxLayout(self.list_host)
        rows.setContentsMargins(TOKENS.spacing.sm, 0, 0, 0)
        rows.setSpacing(TOKENS.spacing.xxs)
        current = cell.raw if isinstance(cell.raw, int) else 0
        for key, text in sorted(cell.values.items(), key=lambda kv: int(kv[0])):
            bit = int(key)
            if bit == 0:
                continue
            box = QCheckBox(text)
            box.setChecked(bool(current & bit))
            box.setToolTip(f"{text}（这一位 = {bit}）")
            box.toggled.connect(lambda _state: self._refresh_summary())
            rows.addWidget(box)
            self._rows.append((bit, text, box))
        self.list_host.setVisible(False)
        root.addWidget(self.list_host)
        self.button.setToolTip(f"{cell.cn}：点一下展开全部 {len(self._rows)} 项；勾选完点别处生效")
        self._refresh_summary()
        self.setFixedHeight(self._unit_height)

    # ---- 读数口（外层 / 用例 / 探针共用这一份，别在别处再算一遍） ----
    def rows(self) -> tuple[tuple[int, str, QCheckBox], ...]:
        return tuple(self._rows)

    def value(self) -> int:
        total = 0
        for bit, _text, box in self._rows:
            if box.isChecked():
                total |= bit
        return total

    def summary(self) -> str:
        chosen = [text for _bit, text, box in self._rows if box.isChecked()]
        return MASK_SEPARATOR.join(chosen) if chosen else MASK_NONE_TEXT

    def is_expanded(self) -> bool:
        return self._expanded

    def list_height(self) -> int:
        """展开列表**要多少高**（隐藏时 sizeHint 照样有效）。"""
        return self.list_host.sizeHint().height()

    def set_unit_height(self, height: int) -> None:
        """宿主给的单行高（右栏 26 / 三栏 34）—— 折叠态就占这么高。"""
        self._unit_height = max(1, int(height))
        self._apply_height()

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.list_host.setVisible(expanded)
        self._apply_height()
        self._refresh_summary()
        self.expanded_changed.emit(expanded)

    def _apply_height(self) -> None:
        extra = (self.list_height() + TOKENS.spacing.xxs) if self._expanded else 0
        self.setFixedHeight(self._unit_height + extra)
        self.updateGeometry()

    def _refresh_summary(self) -> None:
        arrow = "▴" if self._expanded else "▾"
        self.button.setText(f"{self.summary()}    {arrow}")


class InlineEditor(QFrame):
    """一格的就地编辑。**占的就是那一格的位置**，只有一个控件、没有按钮。"""

    #: 值变了（参数：已按列类型转换好的值）。外层收到就写回 + 立刻保存 + 收工。
    committed = Signal(object)
    #: 没改就撤手了（Esc / 焦点离开）。
    cancelled = Signal()
    #: 用户点了「落在哪」（参数：表名, 行 Id）—— 跳到数据表。界面上不再画，
    #: 留着给将来的入口用（列名那一格已经有 tooltip）。
    open_place_requested = Signal(str, int)

    def __init__(
        self,
        cell: FieldCell,
        *,
        instance: Instance | None = None,
        link_ref: CellRef | None = None,
        parent: QWidget | None = None,
        show_raw_id: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "inline-editor")
        self._cell = cell
        self._instance = instance
        self._link_ref = link_ref
        #: 值右边那一段要不要显示**外键的原始 Id**。
        #: 口径是「Id 永远在值右边、而且**只出现一次**」：
        #: - 中栏词条行 / 弹药行本身就有 `entry-id` 那一列（原始 Id 在那）-> 传 False，
        #:   再显示一遍就是同一个 `#2` 挨着出现两次；
        #: - 编成台右栏那一行只有「中文名 + 值」两段，没有那一列 -> 传 True。
        self._show_raw_id = show_raw_id
        self._input: QWidget | None = None
        self._error = _label("", "inline-error")
        self._error.setVisible(False)
        self._preview = _label("", "inline-raw")
        self._mask_boxes: list[tuple[int, QCheckBox]] = []
        #: 位掩码那一个选择器（只有 CONTROL_MASK 才有；别的字段是 None）。
        self._mask_selector: _MaskSelector | None = None
        #: 宿主给的**单行高**（折叠态编辑器就占这么高）；展开时在它上面加列表高度。
        self._unit_height = TOKENS.size.row_height
        self._done = False
        #: 输入法**还在拼**的那几个字（preedit）。❗ 拼字期间 `QLineEdit.text()` 里**没有**
        #: 它们（实测：全选着「是」时收到 preedit「否」，text() 当场变成空串）—— 见 eventFilter。
        self._preedit = ""
        #: 外键下拉"打字即服务端筛"的最近一次结果与查询词 —— 解析部分名字时要用
        #: （`2A72` 只是显示名 `2А72 30mm` 的前缀，见 `_selected_foreign_id`）。
        self._foreign_hits: tuple[NameHit, ...] = ()
        self._foreign_query: str | None = None
        #: 美术列（缩略图 / 图标 / 立绘）那个可编辑下拉的最近一次查询词。
        self._art_query: str | None = None
        #: 资源键那条下拉的候选列表（要自己在它上面收鼠标松开，见 `_build_resource_key`）。
        self._popup_view: QAbstractItemView | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOKENS.spacing.xxs)

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(TOKENS.spacing.xs)
        main.addWidget(self._build_widget(), 1)
        main.addWidget(self._preview)
        # ❗ 用户 2026-11-11（指着这条悬浮提示）：「不需要这个复制功能，删掉」——
        #    共享行那颗「📄 复制一份给本单位专用」连同它的信号一起删了。
        #    共享行的值**照旧可以直接改**（改的就是那一行，别人一起变），只是不再给退路。
        root.addLayout(main)
        root.addWidget(self._error)
        self._update_preview()

        # ❗ 点到**这个窗口里**任何别的地方 = 收工（用户口径 2026-09-30 实机：
        #    「我点击左侧空白区域时，我要看到它这个编辑栏消失，而不是还停在那里」）。
        #    只靠 focusOut 不够：左边那块空白是普通 QWidget（NoFocus），点上去
        #    焦点根本不转移 —— focusOut 不会来，浮层就原地不动。
        #    ❗ 必须挂 **QApplication**：挂在窗口上只能收到"发给窗口自己"的事件，
        #    而点左栏空白是发给那个子控件的 —— 等于没挂（我第一版就是这么挂错的）。
        #    代价是要自己认掉"浮层里那颗下拉弹出来的候选列表"（它是独立顶层窗口）。
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ------------------------------------------------------------------ #
    # 控件
    # ------------------------------------------------------------------ #

    def _build_widget(self) -> QWidget:
        control = self._cell.control
        if control == CONTROL_BOOL:
            # ❗ 用户 2026-10-02：「**把所有布尔值的编辑栏，通通改成输入文字的编辑栏，
            #    别搞什么按钮**」—— 从前这里是个 QCheckBox("是")（勾选控件 = 一个按钮）。
            #    现在改成文本框：
            #    预填「是 / 否」，收工走 editingFinished（与其它文本格同一条路）。
            #    读得懂 是/否、true/false、1/0、y/n、on/off、真/假、✓/✗
            #    （唯一那份口径在 core/data/columns.py::_TRUE_WORDS / _FALSE_WORDS）；
            #    读不懂就**报错、不写、不关**，parse_value 的 hint 会显示在框下面。
            edit = QLineEdit(self._cell.text)
            edit.setToolTip(f"{self._cell.cn}：填「是」或「否」（也认 true / false、1 / 0）")
            edit.selectAll()
            edit.editingFinished.connect(self._commit)
            self._input = edit
            edit.setFocus()
            return edit
        if control == CONTROL_ENUM:
            combo = QComboBox()
            for key, text in sorted(self._cell.values.items(), key=lambda kv: int(kv[0])):
                combo.addItem(text, int(key))
            index = combo.findData(self._cell.raw)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.currentIndexChanged.connect(lambda _i: self._commit())
            self._input = combo
            combo.setFocus()
            return combo
        if control == CONTROL_MASK:
            # ❗ 位掩码走**同一个部件**（`_MaskSelector`）：折叠一行 ＋ 点开在下方展开。
            #    全库 4 个 mask 列共用它，一个特例都不写 —— 用户 2026-11-11：
            #    「设计要统一，不要单独只改这一项」。
            #    （上一版是 10 个复选框竖排铺满整格：右栏一格宽，直接吃掉半屏，作废。）
            return self._build_mask()
        if control == CONTROL_FOREIGN:
            return self._build_foreign()
        if str(getattr(self._cell, "column", "")) in LOOKUP_NAME_COLUMNS:
            # 🔎 名字列（Name / HUDName / UIName）：**打字即出候选**，就在这一格下面。
            #    用户 2026-10-03（指着右栏「装甲名」那一格）：「**我不要这个垃圾放大镜检索，
            #    我要输入就能在下面出来一列来给我检索**」。
            #    做法 = 资源键那一支原样搬过来（可编辑下拉 + 服务端筛）：候选同样是"这一列
            #    在这张表里**用过的值**"（`resource_key_candidates`）；框里写什么就提交什么，
            #    打一个从没出现过的名字照旧算数（换名字是正当需求，别把人的手绑死在下拉上）。
            #    ⚠️ 判据用**列名**不用 control：`Armors.ArmorValue`（装甲值）也是 control=text，
            #    但它不是名字列（实测 core/edit/dictionary.field_info）。
            return self._build_resource_key()
        if control == CONTROL_RESOURCE:
            # **资源键**那一列（缩略图 / 图标 / 立绘 / 音效预设…）：可编辑下拉 + 打字即筛。
            # 用户口径（2026-10-01）：「缩略图我也要能直接输入然后搜索，而不是我去找然后
            # 复制名字，那个名字太长了，很难记」。
            # 判据用 control 而不是自己再判一次列名 —— 词典那句写着「界面按它选控件，
            # 不在界面里再判一次类型」（core/edit/dictionary.py）。
            return self._build_resource_key()
        line = QLineEdit(str(self._cell.raw) if self._cell.raw is not None else "")
        line.setPlaceholderText("留空 = 不填" if self._cell.value_kind == "text" else "填一个数")
        line.setToolTip(self._cell.cn)
        line.textChanged.connect(lambda _text: self._update_preview())
        # ❗ editingFinished 在**回车**与**失焦**时都发 —— 正好是"改完就算数"。
        line.editingFinished.connect(self._commit)
        self._input = line
        line.setFocus()
        line.selectAll()
        return line


    def _build_mask(self) -> QWidget:
        """位掩码：**所有 mask 列共用**的那一个选择器（折叠一行 ＋ 点开列表）。

        ❗ 折叠态高度 = 宿主给的单行高（`preferred_height()` 记下来的那个）；展开时
          `_apply_mask_height()` 自己把编辑器加高，**宿主不用改** —— 右栏 / 三栏两处
          本来就只给了「一行高」这一个契约。
        """
        selector = _MaskSelector(self._cell)
        selector.set_unit_height(self._unit_height)
        selector.expanded_changed.connect(lambda _expanded: self._apply_mask_height())
        self._mask_selector = selector
        # `_mask_boxes` 是老接口（value() / input_widget / 用例都读它）—— 由选择器派生。
        self._mask_boxes = [(bit, box) for bit, _text, box in selector.rows()]
        self._input = selector
        selector.button.setFocus()
        return selector

    def _apply_mask_height(self) -> None:
        """折叠 = 一行高；展开 = 一行高 ＋ 列表高度（父布局当场重排，不许闪一拍）。"""
        selector = self._mask_selector
        if selector is None:
            return
        extra = (selector.list_height() + TOKENS.spacing.xxs) if selector.is_expanded() else 0
        self.setFixedHeight(self._unit_height + extra)
        self.updateGeometry()
        holder = self.parentWidget()
        if holder is not None and holder.layout() is not None:
            holder.layout().activate()

    # ---- 位掩码的对外读数口（探针 / 用例共用，别在别处再算一遍） ----
    def mask_summary(self) -> str:
        """折叠态那颗按钮上的字（选中的几位用「 · 」连起来；一位没选是「（无）」）。"""
        return "" if self._mask_selector is None else self._mask_selector.summary()

    def mask_expanded(self) -> bool:
        return self._mask_selector is not None and self._mask_selector.is_expanded()

    def toggle_mask_list(self) -> None:
        """展开 / 收起那一份勾选列表（探针与用例的唯一入口）。"""
        if self._mask_selector is not None:
            self._mask_selector.toggle()
    def _build_foreign(self) -> QWidget:
        """外键：**可编辑下拉**。打字**即服务端筛**，显示的是名字，写回去的是 Id。

        ❗ 为什么不能靠 QCompleter 的本地过滤（2026-09-30 用户报的真 bug）：
        本地过滤只认**显示名**，而数据里那件武器的 HUDName 写着「2А72 30mm」
        —— 那个「А」是**西里尔 А（U+0410）**，用户键盘打的是拉丁 A：`2A72` 永远匹配不上，
        而打 `72` 反而能出（用户报的就是这个）。现在每次改字都去 `search_rows` 问一次服务端
        （它同时搜 Name 与 HUDName，且两边都折同形字），候选列表就是"筛出来的那几行"。
        """
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setToolTip(f"点开或打字筛 {self._cell.foreign}（Name 与 HUDName 都认，同形字也认）")
        self._foreign_hits = ()
        self._foreign_query = None
        if self._instance is not None and self._cell.foreign:
            self._foreign_hits = search_rows(
                self._instance, self._cell.foreign, "", limit=FOREIGN_CHOICES
            )
            for hit in self._foreign_hits:
                combo.addItem(_choice_text(hit), hit.row_id)
        current = combo.findData(self._cell.raw)
        if current >= 0:
            combo.setCurrentIndex(current)
            # ❗ **框里只写名字**（用户口径 2026-10-01）：
            #    「这个 ID 显示，不应该在编辑栏里显示，而是在其右边显示」。
            #    候选文本「名字  #Id」是给**下拉列表**分辨那几门同名炮塔用的（见 _choice_text），
            #    但可编辑下拉的框里显示的就是候选项文本 —— 于是那个 Id 被塞进了编辑框。
            #    这里用 lineEdit().setText() 而**不是** combo.setEditText()：后者只改字、
            #    会顺手把 currentIndex 弄丢，而"选中的是哪一条"正是 _selected_foreign_id
            #    的第一判据（那 5 门炮塔的 HUDName 一模一样，丢了索引就只能报"多条同名"）。
            #    Id 改由右边那一段显示（见 _update_preview）。
            self._show_foreign_name(combo, current)
        else:
            combo.setCurrentIndex(-1)
            combo.setEditText(self._cell.text if self._cell.raw else "")
        completer = combo.completer()
        if completer is not None:
            # ❗ 本地过滤**必须关掉**：候选是服务端筛出来的，再让 completer 按显示名过滤一遍，
            # 等于把"靠 Name 才命中的那些行"又滤掉 —— 正是这个 bug。
            completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        combo.editTextChanged.connect(self._on_foreign_text_changed)
        # 框里的字一变，右边那段（Id）跟着走 —— 不然打完字它还停在旧 Id 上。
        combo.editTextChanged.connect(lambda _text: self._update_preview())
        combo.currentIndexChanged.connect(lambda _i: self._commit())
        if combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText(
                f"🔎 按名字搜 {self._cell.foreign}…（Name 与 HUDName 都认）"
            )
            combo.lineEdit().editingFinished.connect(self._commit)
        # ❗ **候选列表里鼠标按下那一下 = 用户选了这一条**（先写文字、再收工）—— 与资源键那条
        #    同一套机制（`_pick_candidate`）。不接管的话 Qt 会**先改 currentIndex、后改文字**：
        #    那一刻框里还是半截搜索词（`S8`），于是
        #    ① `currentIndexChanged -> _commit()` 当场报「对不上「S8」；匹配到 6 条」——
        #       那行红字就是它；
        #    ② 候选文本（`名字  #Id`）被 `editTextChanged` 拿去服务端搜 —— 搜不到 ⇒ 列表被清空 ⇒
        #       还开着的浮层变成一个**空黑洞**（用户 2026-10-02：「我左图点了之后，
        #       然后就出现了右图的情况」）。
        popup = completer.popup() if completer is not None else None
        if isinstance(popup, QAbstractItemView):
            self._popup_view = popup
            popup.viewport().installEventFilter(self)
            popup.clicked.connect(self._on_candidate_clicked)
        self._input = combo
        combo.setFocus()
        return combo

    def _build_resource_key(self) -> QWidget:
        """资源键那一列：**可编辑下拉 + 打字即筛**（候选 = 这一列在这张表里用过的值）。

        ❗ 打字 / 粘贴一个候选里没有的键**照旧算数**（`editingFinished` 那条路）——
        换一张从没被用过的图是正当需求，别把人的手绑死在下拉上；这一条与改动前
        那个纯文本框的行为一致，不算回归。
        """
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setToolTip(f"{self._cell.cn}：打字筛这一列用过的名字（也可以直接粘一个游戏里的键）")
        self._input = combo  # ❗ 先挂上：_reload_art_items 要用它
        self._art_query = None
        self._reload_art_items("")
        index = combo.findText(self._cell.text)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(self._cell.text)
        completer = combo.completer()
        if completer is not None:
            # ❗ 本地过滤**必须关掉** —— 与外键那条同一个坑（2026-09-30 的「打 2A72 搜不出来」）：
            #    默认的 PopupCompletion 会拿框里那串字按**前缀**把模型再滤一遍，
            #    而我们给的候选已经是筛过的（`BLT_SNIPER-Label` 不以 `sniper` 开头）——
            #    再滤一次就一条不剩，表现就是**打字之后什么都没弹出来**（用户实测报的）。
            completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
            # ❗ **点候选 = 当场收工**，不能只靠 `currentIndexChanged`：重建候选时
            #    `clear()` + `addItem()` 已经把 currentIndex 悄悄设成 0（那时信号关着），
            #    于是「点第一条」= 索引没变 = 信号不发 = **不写** —— 用户还得再点一下别处
            #    才收工。用户口径 2026-10-01：「我输入 PDSS，然后要点一下，点了一下后，
            #    我还要再点一下，这纯粹就是多余操作」（筛出来只剩一条时最明显）。
            #    `activated` 的语义是「用户**选了**一条」，与索引变没变无关。
            completer.activated.connect(lambda _index: self._commit())
            # ❗❗ 但**单击候选根本不发 `activated`** —— 实测（真鼠标事件，11 条候选）：
            #    点完之后行编辑里的字**变了**，而 `activated=0`、`currentIndexChanged=[]`、
            #    浮层一直开着 → 用户得再点一下别处才收工。
            #    原因：单击只走 `QCompleter` 的 `clicked` 那条路（把文字放进行编辑），
            #    `activated` 是**双击 / 回车**的语义（Windows 下那条
            #    `SH_ItemView_ActivateItemOnSingleClick` 默认是关的）。
            #    所以必须直接挂候选列表自己的 `clicked`。
            popup = completer.popup()
            if isinstance(popup, QAbstractItemView):
                # ❗❗ 挂信号**不够** —— 真鼠标事件数出来的（11 条候选，点第 4 条）：
                #    `popup.clicked` 0 次、`activated` 0 次、`currentIndexChanged` 0 次，
                #    **只有 `highlighted` 发了 1 次**；框里的字变了，浮层却一直开着
                #    → 用户得再点一下别处（「这纯粹就是多余操作」）。
                #    所以不走信号：自己在候选列表的 viewport 上收**鼠标松开**那一下，
                #    用 `indexAt` 定位到底点了哪一条 —— 这条路不依赖 Qt 发哪个信号。
                popup.viewport().installEventFilter(self)
                self._popup_view = popup
                popup.clicked.connect(self._on_candidate_clicked)  # 能发的环境里也认
        # 这两条放在初值设好**之后**才接：否则上面那几句会当场触发一次 commit。
        combo.editTextChanged.connect(self._on_art_text_changed)
        combo.currentIndexChanged.connect(lambda _i: self._commit())
        if combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText("🔎 打字筛这一列用过的名字…")
            combo.lineEdit().editingFinished.connect(self._commit)
        combo.setFocus()
        return combo

    def _show_foreign_name(self, combo: QComboBox, position: int) -> None:
        """外键那一格**框里**只写名字 —— 行 Id 归右边那一段（用户口径 2026-10-01）。

        两件都要做对：

        - 用 lineEdit().setText() 而不是 combo.setEditText()：后者会顺带把 currentIndex
          弄丢，而"选中的是哪一条"正是 _selected_foreign_id 的第一判据
          （那 5 门炮塔的 HUDName 一模一样，丢了索引就只能报"多条同名"）；
        - 改显示**不算打字**：不关信号的话会触发 _on_foreign_text_changed 拿这个名字
          再筛一遍，下拉当场被滤成一条。
        """
        line = combo.lineEdit()
        if line is None:
            return
        combo.blockSignals(True)
        try:
            line.setText(_choice_name(combo.itemText(position)))
        finally:
            combo.blockSignals(False)

    def _on_candidate_clicked(self, index) -> None:
        """（信号那条路）鼠标点了候选 -> 写进框、当场收工。

        用户口径 2026-10-01：「我输入 PDSS，然后要点一下，点了一下后，我还要再点一下，
        这纯粹就是多余操作」。
        """
        if index.isValid():
            self._pick_candidate(index)

    def _pick_candidate(self, index) -> None:
        """用户**点中**候选里的一条 -> 把那条写进框里，并**当场收工**。"""
        combo = self._input
        if isinstance(combo, QComboBox):
            text = str(index.data() or "")
            if text:
                # 关掉信号再塞文字：免得又触发一次「打字 -> 重筛」。
                combo.blockSignals(True)
                try:
                    combo.setEditText(text)
                finally:
                    combo.blockSignals(False)
        self._commit()

    def _on_art_text_changed(self, text: str) -> None:
        """打字 -> 重筛候选（同一个词不重复筛；这是单表一列的子串比较，毫秒级）。"""
        if self._instance is None or text == self._art_query:
            return
        self._reload_art_items(text)

    def _reload_art_items(self, needle: str) -> None:
        """按当前文本重建候选：**把用户打的字原样还回去**（clear 会把行编辑冲掉）。"""
        combo = self._input
        if not isinstance(combo, QComboBox) or self._instance is None:
            return
        keys = resource_key_candidates(
            self._instance,
            self._cell.ref.table,
            self._cell.ref.column,
            needle=needle,
        )
        self._art_query = needle
        combo.blockSignals(True)
        try:
            # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
            #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
            while combo.count():
                combo.removeItem(0)
            for key in keys:
                combo.addItem(key, key)
            combo.setEditText(needle)
        finally:
            combo.blockSignals(False)
        # 把筛好的候选**当场弹出来**：不弹出的话用户只看见自己打的字，
        # 以为这功能没反应（实测就是这么报上来的）。前缀置空 —— 已经筛过了，别再滤一遍。
        if needle and combo.count():
            completer = combo.completer()
            if completer is not None:
                completer.setCompletionPrefix("")
                completer.complete()
        elif not combo.count():
            # 一条都没筛出来：把浮层收掉 —— 留着一个**空浮层**就是用户看到的"黑洞"。
            combo.hidePopup()
        # 与外键那条同一条：卷动壳被打断时会留在屏幕上（见 _reload_foreign_items 的说明）。
        QTimer.singleShot(ROLL_EFFECT_SWEEP_MS, hide_roll_effects)

    def _on_foreign_text_changed(self, text: str) -> None:
        """打字 -> **去服务端筛一遍**（同一个词不重复筛）。

        ❗ 不做防抖是故意的：`search_rows` 就是一次全表扫 + 子串比较（Weapons 千行量级），
        而防抖会把"敲完字立刻看得见结果"变成"过 150ms 才看得见"，用例也得跟着等定时器。
        """
        if self._instance is None or not self._cell.foreign:
            return
        if text == self._foreign_query:
            return
        combo = self._input
        # ❗ **点候选**那一下也走这里：Qt 把候选文本（`名字  #Id`）塞进行编辑 —— 那串字拿去
        #    服务端搜是**搜不到**的（名字里没有 `#Id`）⇒ 恰好 0 条 ⇒ 列表当场被清空 ⇒
        #    还开着的浮层变成一个**空黑洞**（用户 2026-10-02 截图：「我左图点了之后，然后就出现了
        #    右图的情况」），而且 `_selected_foreign_id` 之后再也对不上 ⇒ 那行红字报错。
        #    所以先认一句：**框里那串字正好是某一项**（点了候选 / 把完整名字打完）就不再重筛。
        if isinstance(combo, QComboBox) and combo.findText(text) >= 0:
            self._foreign_query = text
            return
        self._reload_foreign_items(text)

    def _reload_foreign_items(self, needle: str) -> None:
        """按当前文本重建候选：**保住已经选中的 Id**，并把用户打的字原样还回去。

        ❗ 两件都必须做：
        - `clear()` + `addItem()` 会把行编辑里的字冲掉 → 用户打到一半的字就没了；
        - 重建期间 `currentIndexChanged` 会乱发（可能当场 commit 一个不是我选的 Id）
          → 用 blockSignals 关掉，重建完再开。
        """
        combo = self._input
        if not isinstance(combo, QComboBox) or self._instance is None:
            return
        keep = combo.currentData()
        hits = search_rows(self._instance, self._cell.foreign, needle, limit=FOREIGN_CHOICES)
        self._foreign_hits = hits
        self._foreign_query = needle
        combo.blockSignals(True)
        try:
            # ❗ 不用 clear()：走模型整表重置会踩 shiboken releaseWrapper 的 NULL 查表
            #    （实测与证据见 docs/交接-2026-10-01-载具飞机编成台.md §6.7-§6.10）
            while combo.count():
                combo.removeItem(0)
            for hit in hits:
                combo.addItem(_choice_text(hit), hit.row_id)
            if keep is not None:
                index = combo.findData(keep)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.setEditText(needle)
        finally:
            combo.blockSignals(False)
        if not combo.count():
            # 一条都没筛出来：把浮层收掉 —— 留着一个**空浮层**就是用户看到的"黑洞"。
            combo.hidePopup()
        # ❗ 候选重建 = 浮层被收了又弹；Qt 给下拉展开动画造的那个壳（`QRollEffect`）**被打断时**
        #    会留在屏幕上** —— 它只是一张旧 pixmap + 一块没画过的黑底，用户看到的就是"一个黑框，
        #    上面一行候选、下面全黑"（2026-10-02 实机截图）。
        #    `hidePopup()` 收不掉它（它不是 QComboBox 的 view），只有按类名扫。
        #    ⏱ 延时扫：正常动画 ~200ms，扫太早会把动画打断；`ROLL_EFFECT_SWEEP_MS` 之后还在的
        #    才是残壳。**不持 self**（定时器到点可能编辑器已经没了）。
        QTimer.singleShot(ROLL_EFFECT_SWEEP_MS, hide_roll_effects)

    # ------------------------------------------------------------------ #
    # 焦点
    # ------------------------------------------------------------------ #

    @property
    def cell(self) -> FieldCell:
        """正在改的这一格（**进来的那一份**，不是控件里的当前值 —— 当前值看 value()）。"""
        return self._cell

    def mask_rows(self) -> int:
        """这一格有**几项**可勾（不是位掩码就是 0）—— 折叠态仍然只占一行。"""
        return len(self._mask_boxes)

    def preferred_height(self, row_height: int) -> int:
        """这一格**该有多高** —— 折叠态一律是**一行**（宿主不用为某个字段特殊处理）。

        ❗ 用户 2026-11-11 定稿：位掩码是「一行摘要 ＋ 点开列表」，**不是**铺满整格。
          展开时由 `_apply_mask_height()` 自己加高，宿主契约保持「一格 = 一行」。
        """
        self._unit_height = max(1, int(row_height))
        if self._mask_selector is not None:
            self._mask_selector.set_unit_height(self._unit_height)
        return self._unit_height

    @property
    def input_widget(self) -> QWidget | None:
        """里面**真正收键盘**的那个控件（位掩码时是那颗「点开」按钮）。"""
        if self._mask_selector is not None:
            return self._mask_selector.button
        return self._input

    def focus_input(self) -> None:
        """把焦点交给里面的输入控件 —— 外层挂上浮层之后**必须**调一次。

        ❗ 浮层自己是 `QFrame`，focusPolicy 默认 NoFocus：`editor.setFocus()` 是
        **空操作**。构造里那句 `line.setFocus()` 又发生在"控件还没有父控件"的时候，
        等于没设。于是"点开一格 -> 直接打字"第一次打不进去，得再点一下才生效
        （用户 2026-09-29 实测报的："第一次输不进去，还得再点一下，很难用"）。

        调用点：`widgets/row_entries.EntryRow.attach_editor` 与
        `screens/editor/ammo_block.AmmoLine.attach_editor`。
        """
        target = self.input_widget
        if target is None:
            return
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        if isinstance(target, QLineEdit):
            target.selectAll()
        elif isinstance(target, QComboBox) and target.isEditable():
            # ❗ 外键下拉的框里**已经写着当前那个名字**（例："2А72 30mm"）—— 不全选的话，
            #    用户打的字会**追加**在旧名字后面（离屏实测："2А72 30mm2A72"），
            #    服务端筛出来 0 条、下拉当场空掉。文本框那条路一直是全选的（上一句），
            #    下拉这里补上同一条口径。
            line = target.lineEdit()
            if line is not None:
                line.selectAll()

    # ------------------------------------------------------------------ #
    # 焦点：点到别处就收工（没有按钮，这是唯一的"离开"手势）
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        """本窗口里别的地方被按下 -> 收工（保存）。见 __init__ 里的说明。

        浮层自己（以及它的子控件）被按下时不管 —— 那是在浮层里面操作。
        """
        # ❗ 候选浮层**可能是已经被销毁的 C++ 对象**：重建候选（清空 + `addItem()`）时
        #    Qt 会换掉那个 view，而 `self._popup_view` 还指着老的 —— 直接碰它会在事件过滤器里抛
        #    「Internal C++ object (QListView) already deleted」（实测：`test_unit_editor` 那条
        #    「打 2A72」用例是**用例跑完之后**才炸的，症状是 ERROR 不是 FAILED）。先问一句还活着吗。
        # ❗ 输入法每条消息都记一笔：拼字中（preedit 非空）时**绝不抢着收工**。
        #    用户 2026-10-02 报的那条：「我改这一栏，点了空白区域，改动没有生效」——
        #    实测复现（_work/probe_ime_preedit.py）：点开那一格（全选「是」）→ 输入法开始拼「否」
        #    → 那一刻 text() 是**空串** → 用户点空白 ⇒ 我们的过滤器**当场收工**，
        #    把空串按「否」解析写进库里（布尔值空串 = false），而那几个字随控件一起被销毁
        #    ⇒ 改动静默丢失/被写错。拼字中先不收工：让 Qt 照常把焦点交给点到的地方，
        #    输入法收到焦点离开会**先把拼的字上屏**，随后 editingFinished 再走正常收工那条路。
        if event.type() == QEvent.Type.InputMethod and hasattr(event, "preeditString"):
            self._preedit = str(event.preeditString() or "")
        popup_view = self._popup_view
        if (
            not self._done
            and popup_view is not None
            and isValid(popup_view)
            and obj is popup_view.viewport()
        ):
            # 资源键那条：候选列表里**鼠标按下**的那一下 = 用户选了这一条。
            # ❗ 必须是**按下**，不能等松开 —— 实测（app 级事件过滤器记账）：
            #    点在候选上只收到 `MouseButtonPress`，**`MouseButtonRelease` 根本没来**
            #    （弹窗是 `Qt::Popup`，它会把鼠标抓走）。等松开 = 永远等不到，
            #    于是「字填进去了、浮层还开着」，用户只能再点一下别处 —— 就是那句
            #    「点了一下后，我还要再点一下，这纯粹就是多余操作」。
            if event.type() == QEvent.Type.MouseButtonPress:
                index = popup_view.indexAt(event.position().toPoint())
                if index.isValid():
                    self._pick_candidate(index)
                    return True  # 这一下由我们处理掉，别再让弹窗自己走一遍
            return False
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and not self._done
            and isinstance(obj, QWidget)
            and not self._is_my_ui(obj)
        ):
            if self._preedit:
                # 输入法还没上屏：这一下先不收工（理由见上面那段）。焦点交给点到的地方之后，
                # Qt 会让输入法把拼的字上屏 -> editingFinished -> _commit() 走正常那条路。
                return False
            self._commit()
        return False

    def _is_my_ui(self, widget: QWidget) -> bool:
        """这一下是按在浮层上（或浮层自己弹出来的候选列表上）吗。

        候选列表是**独立顶层窗口**（Qt.Popup），它的父是浮层里那颗 QComboBox ——
        不认它的话，点候选项的第一下就会把浮层关掉。
        """
        if widget is self or self.isAncestorOf(widget):
            return True
        owner = widget.window().parent()
        return owner is not None and (owner is self or self.isAncestorOf(owner))

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().focusOutEvent(event)
        # 焦点在控件之间跳（比如下拉弹开）也会走这里，延到下一轮再判。
        QTimer.singleShot(0, self._close_if_focus_left)

    def _close_if_focus_left(self) -> None:
        # ❗ 这个函数是 QTimer.singleShot(0, ...) 排出来的，而浮层可能已经被
        #    detach()（= deleteLater）摘掉了 —— 那时 C++ 对象已经没了，碰一下就抛
        #    "Internal C++ object (InlineEditor) already deleted"。诊断日志里抓到过
        #    （一条一条地刷，每次都带 traceback）。所以先问一句"这个对象还活着吗"。
        from shiboken6 import isValid

        if not isValid(self) or self._done or not self.isVisible():
            return
        focused = QApplication.focusWidget()
        if focused is not None and (focused is self or self.isAncestorOf(focused)):
            return
        # ❗ 点到别处 = **收工**（用户口径 2026-09-30 实机：「点击到别处的时候，
        #    这个编辑栏自动保存修改并变回之前的样子」—— 说的是"值落进缓存"）。
        #    原来是 cancelled —— 手一抖
        #    点开别的地方，刚填的值就没了，而界面上什么提示都没有。
        #    _commit() 自己会处理"值转不动"：显示错误、**不关闭**、不写坏值；
        #    已经收工过（_done）则直接返回。所以这里无脑调它就行。
        self._commit()

    # ------------------------------------------------------------------ #
    # 值
    # ------------------------------------------------------------------ #

    def value(self) -> Any:
        """当前控件里的值（已按列类型转好）；转不了时抛 BaModMakerError。"""
        control = self._cell.control
        # ❗ 布尔值走**文本**那条路（末尾那句 parse_cell_text）—— 用户 2026-10-02 要求
        #    布尔编辑栏与别的格子一样是"输入文字"，所以这里不再单独读 isChecked()。
        if control == CONTROL_ENUM:
            data = self._input.currentData()
            return data if data is not None else 0
        if control == CONTROL_MASK:
            total = 0
            for bit, box in self._mask_boxes:
                if box.isChecked():
                    total |= bit
            return total
        if control == CONTROL_FOREIGN:
            return self._selected_foreign_id()
        if isinstance(self._input, QComboBox):
            # 资源键那一条：可编辑下拉（缩略图 / 图标 / 立绘 / 音效预设）。
            return self._resource_key_value()
        return parse_cell_text(self._cell, self._input.text())

    def _resource_key_value(self) -> str:
        """资源键那一格收工时取哪个值 —— 三档，**不猜、也不静默写坏**。

        1. 框里那串字**正好是某一项**（点了候选，或把完整键名粘/打完）→ 用它；
        2. 那串字**只是某个键的一部分** → 说明用户打的是**搜索词**（「stryker」），
           抛错、不写、不关浮层：把半截搜索词写进这一格 = 悄悄把数据写坏；
        3. 谁都对不上 → 当成**库里还没有的新键**，照旧允许直接填（改动前那个纯文本框
           就是这个行为 —— 不能因为换成下拉就把「填一个新键」这条路堵死）。

        为什么要有第 2 条：可编辑下拉里，用户打完筛选词常常**直接点走**（改主意了）。
        旧文本框那条路下这只会写进一个拼错的名字，而下拉这边写进去的是**搜索词本身**。
        """
        combo = self._input
        typed = combo.currentText().strip()
        if not typed:
            return ""  # 留空 = 这一格空着（与旧文本框一致）
        position = combo.currentIndex()
        if 0 <= position < combo.count() and combo.itemText(position) == typed:
            return typed  # ① 点中的就是它
        if self._instance is None:  # pragma: no cover - 没有实例时不该走到这里
            return typed
        keys = resource_key_candidates(
            self._instance,
            self._cell.ref.table,
            self._cell.ref.column,
            # ❗ 这里要**全量**（不截断）：截断了「正好是某一项」就查不到、
            #    那条搜索词守卫也会漏 —— 两者都靠这份完整名单。
            limit=len(self._instance.table(self._cell.ref.table).rows) + 1,
        )
        folded = {match_text(key): key for key in keys}
        want = match_text(typed)
        if want in folded:
            return folded[want]  # ①' 打的恰好是某一项（同形字也认）
        matched = [key for key in keys if want in match_text(key)]
        if len(matched) == 1:
            # 打的是搜索词，但**只筛出一条** —— 那就是它，与外键那条同口径
            # （`_selected_foreign_id` 第 3 条：「恰好一条才用」）。
            # 不然按回车 / 点走之后还得回去点一下那条候选（同一句用户口径）。
            return matched[0]
        if matched:
            raise TableError(
                f"「{typed}」不是这一列里的名字（这是搜索词，筛出 {len(matched)} 条）",
                hint="从弹出来的列表里选一条；要填库里还没有的键，就把完整键名打完",
            )
        return typed  # ③ 库里还没有这个键：照旧允许

    def _selected_foreign_id(self) -> int:
        """下拉/打字 -> Id。**不猜**：对不上就明说，绝不在多条同名里替用户挑一条。

        三条路：

        1. 文本与某一项**一字不差**（点了候选项、或把 `2А72 30mm  #2` 整串打进来）-> 用它；
        2. 与候选**名字那一半**相等（同形字折叠后比）—— 恰好一条才用；
        3. 都不中 -> 看本次服务端筛的结果：**恰好一条**才用；0 条 / 多条一律抛错。

        ❗ 为什么第 3 条不许"取第一条"（2026-09-30）：那 5 行的 HUDName 一模一样
        （都是「2А72 30mm」，靠 Name 区分 T1/T2/T3/Gadyuka），静默取第一条 =
        用户以为改的是 T1、其实写进了 Gadyuka —— 这种错比报一句错贵得多。
        """
        combo = self._input
        typed = combo.currentText().strip()
        if not typed:
            return 0  # 库里 0 = 「不使用」

        # ① 与某一项一字不差（点选候选项时，行编辑里就是那一项的文本）。
        position = combo.currentIndex()
        if 0 <= position < combo.count() and combo.itemText(position) == typed:
            return int(combo.itemData(position))
        index = combo.findText(typed, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            return int(combo.itemData(index))

        # ①' 框里写的就是**当前选中那一条的名字**。
        #     本轮把 Id 挪到右边之后（用户口径 2026-10-01：「这个 ID 显示，不应该在编辑栏里
        #     显示，而是在其右边显示」），框里只剩名字 —— 上面那条"一字不差"就再也比不上了，
        #     于是**同名外键**（实测全库有同名 HUDName：「M27 IAR」两门 Id 1 / 51）
        #     会掉进下面 ② 报"多条同名"。
        #     判据用 currentIndex：它是 Qt 记的"选中了哪一条"，而框里的字正是我们**按它写进去的**
        #     （`_show_foreign_name`）。用户另打了别的名字时，这个索引要么是 -1、要么名字对不上，
        #     所以这一条不会替他挑一条 —— "不猜"那条纪律不动。
        position = combo.currentIndex()
        if 0 <= position < combo.count() and typed == _choice_name(combo.itemText(position)):
            return int(combo.itemData(position))

        # ② 候选的**名字那一半**与打的字相等（`2A72 30mm` vs 项文本 `2А72 30mm  #2`）。
        #    同形字折叠在这里起作用：拉丁 `2A72 30mm` 与西里尔 `2А72 30mm` 折完一样。
        folded = fold_confusables(typed).casefold()
        matched = [
            position
            for position in range(combo.count())
            if fold_confusables(_choice_name(combo.itemText(position))).casefold() == folded
        ]
        if len(matched) == 1:
            return int(combo.itemData(matched[0]))
        if len(matched) > 1:
            raise TableError(
                f"对不上「{typed}」；现在匹配到 {len(matched)} 条",
                hint="多条同名，挑一条（候选末尾的 #Id 就是行号）",
            )

        # ③ 打字即服务端筛：筛出来**恰好一条**才敢落值。
        hits = self._foreign_hits if typed == self._foreign_query else ()
        if len(hits) == 1:
            return int(hits[0].row_id)
        if not hits:
            raise TableError(
                f"对不上「{typed}」",
                hint="换个词或把名字打全（只认完整名字，不猜）",
            )
        raise TableError(
            f"对不上「{typed}」；现在匹配到 {len(hits)} 条",
            hint="多条同名，挑一条（候选末尾的 #Id 就是行号）",
        )

    def _update_preview(self) -> None:
        """值右边那一小段：**只在显示值与原始值不一样时才出现**。

        纯数字改数字（68 -> 68）写一行"→ 68"是噪音；枚举 / 掩码 / 外键才有意义。
        """
        try:
            value = self.value()
        except BaModMakerError:
            self._preview.setText("")
            return
        if self._cell.control == CONTROL_FOREIGN:
            # 外键：框里只有名字，**原始 Id 显示在它右边**（用户口径 2026-10-01：
            # 「这个 ID 显示，不应该在编辑栏里显示，而是在其右边显示」）。
            # 为什么不用「→ 名字（原始值 Id）」那一套：decode() 对外键给不出名字
            # （实测 text == raw_text == 50），那句话在这里是空转；名字本来就在框里。
            # ❗ 只有宿主**没有** entry-id 那一列时才显示 —— 否则同一个 `#2` 会出现两次
            #    （见 __init__ 的 show_raw_id）。
            self._preview.setText(f"#{value}" if (self._show_raw_id and value) else "")
            return
        if self._cell.control == CONTROL_MASK:
            # ❗ 用户 2026-11-11（指着展开列表中部浮着的那行字）：「画红圈里面的东西是什么？
            #    我不需要，太碍眼了」。
            #    位掩码的**显示值已经在折叠那颗按钮上**（选中项用「 · 」连起来）——
            #    这里再写一遍「→ 直升机 + 飞机 + …（原始值 1944）」就是同一件事的第二遍；
            #    而且展开列表时这一段被 QHBoxLayout 垂直居中，正好浮在列表中间。
            self._preview.setText("")
            return
        info = field_info(self._cell.table, self._cell.column)
        decoded = decode(info, value)
        if decoded.raw_text == decoded.text:
            self._preview.setText("")
            return
        raw = f"（原始值 {decoded.raw_text}）" if decoded.raw_text else ""
        self._preview.setText(f"→ {decoded.text}{raw}")

    # ------------------------------------------------------------------ #

    def _commit(self) -> None:
        """值定了就报出去 —— **没有确认按钮**（用户口径：不要 √ 和 ×）。"""
        if self._done:
            return
        try:
            value = self.value()
        except BaModMakerError as exc:
            self._show_error(str(exc))
            return
        self._done = True
        self.committed.emit(value)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)


__all__ = [
    "FOREIGN_CHOICES",
    "InlineEditor",
]
