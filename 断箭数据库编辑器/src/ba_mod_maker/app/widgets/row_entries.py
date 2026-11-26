"""「一行词条」面板 —— 新设计的那一行样式（界面重做定稿 §1）。

用户口径（《设计-界面重做-定稿》）：

    左边是导航，右边永远是「刚点的那一行」的全部列。
    行样式统一：序号 │ 中文名 │ 值 │ 英文列名（英文列名是右边的灰字）。

两个东西分工：

- EntryList：一串 EntryRow + 就地编辑的挂载点。**不带表头、不带滚动** ——
  专精页要在这串行中间插一块 7x2 小矩阵，所以"行"和"框"必须能拆开用。
- RowEntriesPanel：EntryList + 表头（标题 / 计数 / 「⬅ 回上一层」）+ 滚动条。
  页面要的一切都在这里：给它一个 (表, 行 Id) 的格子，它把那一行的每一列铺成一横行。

跟老的那个「表→行→列 三级树」面板（一个单位 19 张表 1554 格；2026-10-01 已删，
全仓零实例化点）是**两回事** —— 那个是旧口径，别再往这里加折叠。

## 三条沿用下来的老口径（用户定过，别翻）

1. **外键写名字不写 Id**（2 → USA）；枚举 / 掩码给中文 + 灰字原值；
   空值写「（空）」。
2. **点值就地改**，改完立刻写盘（外层负责存）；没有 √/×，没有确认按钮。
3. Emoji 只放在有语义的地方（表头、返回按钮）。

## 一条新增的小口子：外键那一格有个「↗ 去看这一行」

设计里"往下走"的入口是**卡片**（点卡上的武器 → 右边换成 Weapons 那一行）。
但从 Weapons 那一行再往它引用的表走（弹种、炮塔…）就没有入口了 ——
而面包屑的"父级可点"只解决往回走。所以在**外键值**右边放一个小箭头：
点它 = 右边换成它指向的那一行（等于在树上往下走一层）。
它不改值，也不占地方（没有外键、或值是 0 时不画）。

## 两条本轮定下的口径（用户 2026-10-01 实机反馈）

1. **悬停 = 整行一条完整高亮，同一时刻只有一行**。高亮画在 EntryRow 自己身上
   （QSS `[role="entry-line"][hover="true"]`，见 theme/qss.py 的 [mid-panel] 块），
   而 `hover` 属性**只由 EntryList 写**（`set_hovered_row`）—— 行内的小控件
   （值那一格 / ↗）不再自己画 hover。
   为什么不靠 QSS 的 `:hover`：它读的是 Qt 的 WA_UnderMouse，那个旗标由
   enter/leave 事件贴上和清掉，**没有任何地方保证唯一** —— 列表一重建
   （clear() 走 detach()：hide + setParent(None)）、就地编辑一 hide() 值那一格，
   leave 就可能到不了，残留的旗标让好几行同时亮。用户实机截图上正是
   "鼠标只在第 13 行、13/18/19/20/22/23/24 共 7 行的值那一格同时亮着"，
   而取色器量到的是 color.bg_hover、不是 color.bg_selected —— 所以能确定
   那 7 格当时都处在 :hover 态（不是选中态）。
   现在唯一的画源是**一个属性**，单点写入 —— "只有一行"是结构上的保证。

2. **注释（cell.doc）只挂「中文名」那一格**（`name_label.toolTip()`）：
   悬停词条名才看得到。值那一格是**编辑入口**，它的 tooltip 是空串 ——
   鼠标停上去不许再冒框（用户原话："悬浮在修改栏上的时候，不要显示注释"）。
   就地编辑浮层里也不带 doc（见 screens/editor/inline_editor.py）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QEnterEvent, QHideEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.icons import prepare_icon
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.row_icon import RowIcon, RowMark
from ba_mod_maker.app.widgets.detach import close_editor_popups, detach
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.refs import CellRef

#: 序号那一列。四位数也放得下。
INDEX_COLUMN_WIDTH = 34
#: 中文名那一列。取值靠词典，最长的那几个也放得下。
NAME_COLUMN_WIDTH = 190
#: 英文列名（右边灰字）那一列。**定宽 + 省略号** —— 让它自己撑会把面板顶出横向滚动条。
COLUMN_COLUMN_WIDTH = 200
#: ❗ 值那一格（一个 `QPushButton`）**实际**需要的最小高度 —— 2026-10-02 实测量出来的。
#:
#: 为什么不是"我们想要的 26"：`QPushButton` 的原生 style 会按内容再加一圈 margin，
#: `minimumSizeHint()` 量出来就是 **32px**（字体 14 / 行高 17 + padding 2 + border 2
#: 只有 21），而 `styleSheet` 在 **polish 时把那个最小值写进控件**，**压过
#: `setFixedHeight()`**：实测 `min=32 / max=26`，控件实际拿到 32。
#: （本轮试过三种"钉回去"的办法 —— `max-height` / 显式 `min-height` / polish 后
#:  再 `setFixedHeight` + 盯 StyleChange 事件 —— **全部无效**，原生那个最小值是硬的。）
_VALUE_MIN_HEIGHT = 32
#: 就地编辑浮层的高度。
#: ❗ **不许再写死**：浮层里是一个 QLineEdit / QComboBox，它的高度必须装得下
#:    中英混排的实际行高（QTextLayout 实测 22.0，见 tests/test_theme.py 的探针），
#:    再加上浮层自己那 1px 上下边框。所以取 token 里那个**有守卫**的标准行高 ——
#:    但**还要再跟值那一格要的高度取大**（见上）。
EDITOR_HEIGHT = max(TOKENS.size.row_height, _VALUE_MIN_HEIGHT)
#: 一行的高度 = 浮层 + 上下各 1px。
#:
#: 沿革（这是**第三次**同一族症状了，每次都是"行高装不下那一格"）：
#:   ① 设计稿给 19 -> 24：浮层只有 22，而实际行高实测就是 **22.0**，再加边框正好被裁，
#:      用户看到"编辑栏有遮挡 / 像两个叠在一起"。
#:   ② 24 -> 28（= EDITOR_HEIGHT(26) + 2）：浮层拿到 26 >= 22 + 2(边框) + 2(余量)。
#:   ③ **28 -> 34（2026-10-02）**：这次装不下的不是浮层，是**值那一格自己**（原生 32，
#:      见 `_VALUE_MIN_HEIGHT`）—— 行高 28 时它多出 4px 探到下一行底下被**盖住**，
#:      只要有底色（检索命中 / 槽位选中 / 刚改过）就看得见。用户原话：
#:      "它下面又被遮挡了一部分"。
ROW_HEIGHT = EDITOR_HEIGHT + 2
#: 抬头左边那张**真图标**的高度（宽度按原图比例）。
#: ❗ 从 token 取，不写死像素：换个主题/DPI 时这里跟着走（与专精下拉那个图标同档）。
TITLE_ICON_HEIGHT = TOKENS.size.spec_icon
#: 图标最宽到高度的几倍（单位缩略图是宽的，别让它把抬头那一行顶爆）。
TITLE_ICON_MAX_RATIO = 3
#: 空值怎么写（用户口径：空值写「（空）」）。
EMPTY_TEXT = "（空）"


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


class EntryRow(QWidget):
    """一行词条：序号 │ 中文名 │ 值 │ 原始值 │ 英文列名。值那一格可点。

    **悬停态是这个控件自己的一个属性**（`property("hover")` / `is_hovered`），
    但不许自己改 —— 由 `EntryList.set_hovered_row()` 单点写入，
    好让"同一时刻最多一行是悬停态"成为结构上的保证（模块头 §悬停）。
    高亮画在本控件身上（role=entry-line），所以它横跨**整行**：
    序号到英文列名全在一条里。
    """

    #: 点了值（参数：FieldCell）—— 就地改。
    activated = Signal(object)
    #: 点了「↗ 去看这一行」（参数：表名, 行 Id）—— 右边换成那一行。
    link_activated = Signal(str, int)
    #: 悬停态变了（参数：这一行, 现在是不是悬停）。**只该由 EntryList 接。**
    hover_changed = Signal(object, bool)

    def __init__(self, cell: FieldCell, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cell = cell
        self._editor: QWidget | None = None
        #: 上一次**真的写到控件上**的那份数据（见 refresh：相同就一个字节都不写）。
        #: None = 还没写过（第一次一定写）。
        self._shown_cell: FieldCell | None = None
        self._shown_dirty = False
        self.setFixedHeight(ROW_HEIGHT)
        self.setProperty("role", "entry-line")
        self.setProperty("hover", "false")
        self._hovered = False
        # ❗ 纯 QWidget **子类** Qt 不会自动刷 QSS 背景（只有裸 QWidget 实例会），
        #    整行高亮就画在这个控件上 —— 不开这一位背景画不出来。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        line = QWidget()
        # ❗ 行内的容器必须**明确透明**：全局 QSS 的 QWidget{background-color: bg-base}
        #    会给纯 QWidget 实例自动开 WA_StyledBackground，它一不透明就把整行高亮
        #    盖成 bg-base（实拍过：row.grab() 整行都是 bg-base，看不到高亮）。
        line.setProperty("role", "entry-line-body")
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS.spacing.sm)

        self._index = _label(str(index), "entry-index")
        self._index.setFixedWidth(INDEX_COLUMN_WIDTH)
        self._index.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._index)

        self._name = _label(cell.cn, "entry-name")
        self._name.setFixedWidth(NAME_COLUMN_WIDTH)
        row.addWidget(self._name)

        self._value = QPushButton(cell.text or EMPTY_TEXT)
        self._value.setProperty("role", "entry-value")
        self._value.setCursor(Qt.CursorShape.PointingHandCursor)
        # ❗ Ignored 而不是 Expanding：按钮的 minimumSizeHint 是**文本宽度**，
        # 一行长值（资源键 / 整句 UIName）会把整行顶宽、冒出横向滚动条。
        self._value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._value.setFixedHeight(EDITOR_HEIGHT)
        self._value.setMinimumWidth(80)
        self._value.clicked.connect(lambda: self.activated.emit(self.cell))
        self._value_index = row.count()
        row.addWidget(self._value, 1)

        # 检索命中那一格右边挂的「命中」标签（设计稿块 10）。平时藏着 ——
        # 只有 set_hit(True) 才出来；样式走 qss.py 的 QLabel[role="hit-chip"]。
        self._hit = _label("命中", "hit-chip")
        self._hit.setToolTip("这一格是检索命中的地方（右栏才会挂它）")
        self._hit.setVisible(False)
        row.addWidget(self._hit)

        self._ident = _label("", "entry-id")
        row.addWidget(self._ident)

        self._link = QPushButton("↗")
        self._link.setProperty("role", "entry-link")
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.setFixedWidth(22)
        self._link.setFixedHeight(ROW_HEIGHT - 4)
        self._link.clicked.connect(self._emit_link)
        row.addWidget(self._link)

        self._column = _label(cell.column, "entry-column")
        self._column.setFixedWidth(COLUMN_COLUMN_WIDTH)
        self._column.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._column)

        root.addWidget(line)
        self._line_row = row
        self.refresh(cell, dirty=False)

    # ------------------------------------------------------------------ #
    # 悬停：整行一条高亮
    # ------------------------------------------------------------------ #

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(self, True)

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(self, False)

    def hideEvent(self, event: QHideEvent) -> None:
        """被隐藏 / 摘下来时，把悬停态交回去。

        Qt 只在 enter/leave 里改动鼠标旗标：控件被 `hide()`（列表重建走的
        `detach()`、就地编辑把值那一格藏起来）时，那条 leave 不一定送得到，
        旗标就留在原地 —— "好几行同时亮"就是这么来的。这里自己交回去，
        于是**离屏 / 已摘下来的行不可能还亮着**。
        """
        super().hideEvent(event)
        if self._hovered:
            self.hover_changed.emit(self, False)

    def set_hovered(self, hovered: bool) -> None:
        """**唯一的写入口**（由 `EntryList.set_hovered_row()` 调）。别在别处改。"""
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self.setProperty("hover", "true" if hovered else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    @property
    def is_hovered(self) -> bool:
        """这一行现在是不是悬停态（等于 `property("hover") == "true"`）。"""
        return self._hovered

    # ------------------------------------------------------------------ #

    def _emit_link(self) -> None:
        target = self.link_target
        if target is not None:
            self.link_activated.emit(target[0], target[1])

    @property
    def link_target(self) -> tuple[str, int] | None:
        """这一格指向哪一行（外键且值 > 0 才有；0 = 「不使用」）。"""
        raw = self.cell.raw
        foreign = self.cell.foreign
        if not foreign or not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            return None
        # ❗ 跳到**该去的那一行**：指向"关联表"（没有名字列）时，core 已经把它翻成
        #    它真正指着的那个单位了（`FieldCell.link`，与显示同一份判断）。
        #    从前这里直接返回 `(foreign, raw)` —— 于是 ↗ 会把人带到那张没有名字的中间表。
        return self.cell.link or (foreign, raw)

    def _tooltip(self) -> str:
        """**注释只挂这里**（中文名那一格）：值 → 落点 → doc（有才加）。

        用户口径（2026-10-01 实机）：悬停在「词条名」上才出注释；悬停在值那一格
        上不出 —— 值那一格是编辑入口，它的 tooltip 必须是空串（见 refresh()）。
        """
        cell = self.cell
        lines = [f"{cell.cn} = {cell.text or EMPTY_TEXT}", cell.place]
        if cell.doc:
            lines.append(cell.doc)
        return "\n".join(lines)

    def refresh(self, cell: FieldCell, *, dirty: bool) -> None:
        """改完之后就地更新显示（**不重建控件**）。

        ❗ **这一行画出来的东西没变 -> 一个字节都不写**（2026-10-01，AGENTS §0.7）：
        三栏那一页中栏 31 行、每次刷新全都走一遍，实测 **8.7ms**（尺子
        `_work/probe_vehicle_page.py` 的拆解：`ctx.cell` 只要 0.26ms，剩下全在
        控件上）—— 大头是 `set_dirty` 给两个控件各来一次 unpolish+polish，
        外加两次 `setVisible` 触发的重排。改一格时 30 行是白干的，而
        "每次刷新都碰一遍可见控件"本身就是用户看到的那一次闪。
        判据 = **这一行画出来的每一个输入**：新的 `FieldCell` 整份（名字 / 值 /
        原始值 / 落点 / 注释 …，dataclass 自带 `__eq__`，`values` 那份不参与显示
        所以是 compare=False）+ dirty。有一项变了就照旧全写一遍。
        ❗ 比的是**新数据**，不是"我上次写过" —— `"保住了控件但值还是旧的"` 不算做到。
        """
        self.cell = cell
        changed = cell != self._shown_cell or bool(dirty) != self._shown_dirty
        self._shown_cell = cell
        self._shown_dirty = bool(dirty)
        if not changed:
            return
        empty = not cell.text
        self._value.setText(cell.text or EMPTY_TEXT)
        self._value.setProperty("empty", "true" if empty else "false")
        # ❗ 值那一格是**编辑入口**：不留 tooltip（哪怕只写落点，鼠标停上去也会
        #    冒一个框挡视线）。注释与落点都挂到中文名那一格 —— 见 _tooltip()。
        self._value.setToolTip("")
        self._name.setText(cell.cn)
        self._name.setToolTip(self._tooltip())
        self._column.setText(cell.column)
        self._column.setToolTip(cell.column)
        # 原始 Id / 原始值：显示值与原始值不一样时才出现（外键、枚举/掩码）。
        raw = cell.raw_text
        show = bool(raw) and raw != "0" and raw != cell.text
        self._ident.setText(f"#{raw}" if show else "")
        self._ident.setVisible(show)
        # 原始值（枚举 / 掩码 / 外键的真身）—— 备查信息，不是注释，可以挂徽章上。
        self._ident.setToolTip(f"{cell.cn} 的原始值 = {raw}" if show else "")
        target = self.link_target
        self._link.setVisible(target is not None)
        self._link.setToolTip(f"去看 {target[0]} #{target[1]} 这一行" if target is not None else "")
        self.set_dirty(dirty)

    def set_dirty(self, dirty: bool) -> None:
        # ❗ 它也是**唯一**另一处改控件状态的地方（`RowEntriesPanel.scroll_to` 直接调它）——
        #    缓存跟着走，否则"标脏之后数据没变"那一轮 refresh 会就地返回，脏态就擦不掉了。
        self._shown_dirty = bool(dirty)
        for widget in (self._value, self._name):
            widget.setProperty("dirty", "true" if dirty else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def set_hit(self, hit: bool) -> None:
        """检索页用：这一格命中了搜索词就标黄 + 挂「命中」（设计块 10）。"""
        self.setProperty("hit", "true" if hit else "false")
        self._value.setProperty("hit", "true" if hit else "false")
        self._hit.setVisible(bool(hit))
        for widget in (self, self._value):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    # ------------------------------------------------------------------ #
    # 就地编辑
    # ------------------------------------------------------------------ #

    def attach_editor(self, editor: QWidget) -> None:
        """把编辑控件放进**值那一格**（就在画圈的地方改）。

        ❗ **写死行高**：浮层里的输入框（全局 QSS 给 min-height: 28px）比这一行
        还高，不夹住就会被上下裁掉 —— 实机看到的正是"一个只有右边有蓝边的破框"
        （用户报的"这个编辑栏显示有问题"）。高度由这一行说了算，浮层适配它。
        """
        self.close_editor()
        self._editor = editor
        # ❗ **作废"已经写过"那份缓存**：下面两句 hide 掉的是 `_value` / `_ident`，
        #    而收工时的 `close_editor()` 是靠 `refresh()` 把它们放回来的（refresh 会按
        #    数据重写 `_ident` 的可见性）。缓存不作废的话那一句 refresh 会就地返回，
        #    `"#原始值"` 那一段就再也不出现了 —— 这正是 §0.7 说的"省掉不该省的那一下"。
        self._shown_cell = None
        # ❗ 位掩码（「可瞄准目标」）是**列表式**：一行一位，高度按内容放；而这一行
        #    本身是 `setFixedHeight(ROW_HEIGHT)` ⇒ 行高也得跟着放开，否则下面几位被裁掉
        #    （用户 2026-11-11：「被遮挡住了，无法正常修改」）。
        height = EDITOR_HEIGHT
        preferred = getattr(editor, "preferred_height", None)
        if callable(preferred):
            height = preferred(height)
        editor.setFixedHeight(height)
        self.setFixedHeight(max(ROW_HEIGHT, height + 2))
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value.setVisible(False)
        self._ident.setVisible(False)
        self._line_row.insertWidget(self._value_index, editor, 1)
        editor.show()
        # ❗ 焦点交给**里面那个输入控件**，不是浮层自己：浮层是 QFrame（focusPolicy
        #    默认 NoFocus），setFocus() 是空操作 —— 不这么做，"点开一格就直接打字"
        #    第一次打不进去，得再点一下（用户实测报的）。见 InlineEditor.focus_input。
        focus_input = getattr(editor, "focus_input", None)
        if callable(focus_input):
            focus_input()
        else:
            editor.setFocus()

    def close_editor(self) -> None:
        if self._editor is not None:
            # 同 BenchRail：先收浮层再摘控件（下拉/候选是顶层窗，不跟着销毁）。
            close_editor_popups(self._editor)
            detach(self._editor)
            self._editor = None
        self._value.setVisible(True)
        # 位掩码列表式可能把这一行撑高过 —— 收工必须恢复成标准行高。
        self.setFixedHeight(ROW_HEIGHT)
        self.refresh(self.cell, dirty=self._value.property("dirty") == "true")

    @property
    def has_editor(self) -> bool:
        return self._editor is not None

    @property
    def editor(self) -> QWidget | None:
        """挂在值那一格上的编辑控件（没有就是 None）。用例按它检查浮层。"""
        return self._editor

    @property
    def line(self) -> QWidget:
        """那一行（值 / 英文列名都在里面）—— 编辑控件是它的兄弟，不是另起一行。"""
        return self._line_row.parentWidget()

    @property
    def name_label(self) -> QLabel:
        """中文名那一格 —— **注释（cell.doc）只挂它的 tooltip**（用例按它取）。"""
        return self._name

    @property
    def value_button(self) -> QPushButton:
        """值那一格（没开编辑时可见）。**它不挂 tooltip**（编辑入口不挡视线）。"""
        return self._value

    @property
    def index_text(self) -> str:
        return self._index.text()

    @property
    def column_text(self) -> str:
        return self._column.text()

    @property
    def name_text(self) -> str:
        return self._name.text()

    @property
    def value_text(self) -> str:
        return self._value.text()


class EntryList(QWidget):
    """一串 EntryRow（**不带表头、不带滚动**）+ 就地编辑的挂载点。"""

    cell_activated = Signal(object)
    link_activated = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "detail-body")
        self._rows: list[EntryRow] = []
        self._blocks: list[QWidget] = []
        self._open_row: EntryRow | None = None
        #: 现在亮着的那一行 —— **唯一**的悬停态归属（见 set_hovered_row）。
        self._hovered: EntryRow | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """清空（顺便收掉开着的编辑控件 —— 它挂的那一行马上就要没了）。"""
        self.close_editor()
        # 悬停态跟着行一起走：不交回去的话，被摘下来的行会留着 hover 属性 ——
        # 用户实机看到的"好几行同时亮"就是这么攒出来的（见模块头 §悬停）。
        self.set_hovered_row(None)
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                detach(widget)
        for row in self._rows:  # 兜底：任何一行都不许带着悬停态离开列表
            row.set_hovered(False)
        self._rows = []
        self._blocks = []

    def add_cells(
        self, cells: Sequence[FieldCell], *, hits: Iterable[CellRef] = ()
    ) -> tuple[EntryRow, ...]:
        """接着往下加行（序号从 1 连续编）。返回新建的那些行。"""
        hit_set = frozenset(hits)
        fresh: list[EntryRow] = []
        for cell in cells:
            row = EntryRow(cell, len(self._rows) + 1)
            row.activated.connect(self.cell_activated)
            row.link_activated.connect(self.link_activated)
            row.hover_changed.connect(self._on_row_hover)
            if hit_set:
                row.set_hit(cell.ref in hit_set)
            self._layout.addWidget(row)
            self._rows.append(row)
            fresh.append(row)
        return tuple(fresh)

    def add_block(self, widget: QWidget) -> None:
        """在行与行之间插一块自定义内容（专精页那个 7x2 矩阵走这里）。"""
        self._layout.addWidget(widget)
        self._blocks.append(widget)

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # 悬停：**同一时刻最多一行** —— 唯一的写入口就在下面
    # ------------------------------------------------------------------ #

    def _on_row_hover(self, row: EntryRow, hovered: bool) -> None:
        if hovered:
            self.set_hovered_row(row)
        elif self._hovered is row:
            # 只清"当前亮着的那一行"：万一 Qt 把 enter 排在 leave 前面，
            # 也不至于把刚点亮的那一行顺手灭掉。
            self.set_hovered_row(None)

    def set_hovered_row(self, row: EntryRow | None) -> None:
        """把悬停态挪到 row 上（None = 一行都不亮）。

        **全应用唯一改 EntryRow 悬停态的地方**：先灭上一行、再点新的。
        于是"同时最多一行是 hover"是这一步的结构保证，不看 Qt 的旗标、
        也不靠 enter/leave 的到达顺序。已经不在列表里的行一律当作 None。
        """
        if row is not None and row not in self._rows:
            row = None
        if row is self._hovered:
            return
        previous, self._hovered = self._hovered, row
        if previous is not None:
            previous.set_hovered(False)
        if row is not None:
            row.set_hovered(True)

    def leaveEvent(self, event: QEvent) -> None:
        """鼠标离开整串行（移到表头 / 面板外）—— 一行都不亮。"""
        super().leaveEvent(event)
        self.set_hovered_row(None)

    @property
    def hovered_row(self) -> EntryRow | None:
        """现在亮着的那一行（没有就是 None）。"""
        return self._hovered

    def set_cells(
        self,
        cells: Sequence[FieldCell],
        *,
        hits: Iterable[CellRef] = (),
        blocks: Sequence[QWidget] = (),
    ) -> None:
        """铺这一行的全部列；`blocks` 是**追加在词条下面**的自定义块。

        ❗ 顺序 = 词条 → blocks → stretch：块要紧贴最后一条词条，
        不能被 stretch 顶到面板底部（实机看就是"弹药块飘在半空"）。
        弹药块（app/screens/editor/ammo_block.py）走这里进来 —— 它属于
        "这一行的上下文"，不属于"这一行的列"，所以不进 cells。
        """
        self.clear()
        self.add_cells(cells, hits=hits)
        for block in blocks:
            self.add_block(block)
        self.add_stretch()

    # ------------------------------------------------------------------ #

    @property
    def rows(self) -> tuple[EntryRow, ...]:
        return tuple(self._rows)

    @property
    def blocks(self) -> tuple[QWidget, ...]:
        return tuple(self._blocks)

    def row_for(self, ref: CellRef) -> EntryRow | None:
        for row in self._rows:
            if row.cell.ref == ref:
                return row
        return None

    def refresh_values(
        self, lookup: Callable[[CellRef], FieldCell | None], dirty: Callable[[CellRef], bool]
    ) -> None:
        for row in self._rows:
            fresh = lookup(row.cell.ref)
            if fresh is not None:
                row.refresh(fresh, dirty=dirty(row.cell.ref))

    # ------------------------------------------------------------------ #
    # 就地编辑
    # ------------------------------------------------------------------ #

    def open_editor(self, cell: FieldCell, editor: QWidget) -> EntryRow | None:
        """把编辑控件挂到那一格上。**这一行不在列表里就返回 None**（不假装挂上了）。"""
        self.close_editor()
        row = self.row_for(cell.ref)
        if row is None:
            return None
        row.attach_editor(editor)
        self._open_row = row
        return row

    def close_editor(self) -> None:
        if self._open_row is not None:
            self._open_row.close_editor()
            self._open_row = None

    @property
    def has_editor(self) -> bool:
        return self._open_row is not None

    @property
    def open_cell(self) -> CellRef | None:
        return self._open_row.cell.ref if self._open_row is not None else None

    @property
    def open_row(self) -> EntryRow | None:
        """正在编辑的那一行（没有就是 None）。"""
        return self._open_row


class RowEntriesPanel(QFrame):
    """「刚点的那一行」的全部列：一条一行，序号 + 中文名 + 值 + 英文列名。

    表头上还挂着这一行的两把操作（给了 instance 与 link 才画）：

    - **📄 克隆一份**：复制这一行、把本单位指过去（模板行共用时只改自己那一份）。
     ⛔ 没有「换」（2026-10-19 用户命令删除）：「换」= 删除旧的 + 新增新的。

    用户口径（2026-09-30 实机反馈第二轮 + 第三轮）：

        "像是机动、传感器、装甲、这些，我不仅要能改，还要能换，
         且还能克隆一个出来" / "能力，武器，弹药什么的，也要能换"

    所以这两把操作**长在通用的词条面板上**，不是只给卡上那几块用 ——
    能力 / 武器 / 弹药 / 炮塔 / 装甲 / 机动 / 传感器 全都走同一条路。
    """

    cell_activated = Signal(object)
    link_activated = Signal(str, int)
    #: 点了「⬅ 回上一层」（导航栈里弹一层）。
    back_requested = Signal()
    #: 「📄 克隆一份」。
    clone_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "entries-panel")
        self.setMinimumWidth(TOKENS.size.entries_panel_width)
        #: 「克隆」要用的两样（set_cells 传进来）。
        self._instance = None
        self._link: CellRef | None = None
        #: 这一行的**图标位**（set_cells 传进来；None = 这一行没有图标位）。
        self._mark: RowMark | None = None
        #: 唯一的图标缓存（main_window 那份 self._armory.icons）。没有它就只有 emoji ——
        #: 用例就是这么构造的，不许崩。
        self._icon_store = None
        #: 抬头左边真的画着图没有。**独立记一份，不读 isVisible()** ——
        #: 窗口没 show 时整棵树都不可见（用例与冒烟就是这么跑的），读它会一律得到"没画"。
        self._icon_shown = False

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.xs)

        head = QHBoxLayout()
        head.setSpacing(TOKENS.spacing.sm)
        self._title = _label("", "entries-title")
        # ❗ 抬头左边那个**图标位**（用户口径 2026-10-01：「我不是要 emoji，我是要图标」）。
        #    有真图画真图、没真图才把表 emoji 画在这里 —— **图标位只能有一个**，
        #    所以标题文本本身不再带 emoji（见 EditorPage.entries_title）。
        self._icon_label = QLabel()
        self._icon_label.setProperty("role", "entries-icon")
        self._icon_label.setFixedHeight(TITLE_ICON_HEIGHT)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFont(self._title.font())  # emoji 兜底时与标题同字号
        self._icon_label.setVisible(False)
        head.addWidget(self._icon_label)
        head.addWidget(self._title)
        self._subtitle = _label("", "entries-subtitle")
        head.addWidget(self._subtitle)
        head.addStretch(1)
        self._summary = _label("", "entries-summary")
        head.addWidget(self._summary)
        # ⛔ 2026-10-19 用户命令：**删掉「换」这个功能**（AGENTS §0.10 起就只许 新增/删除/修改）。
        #    这里原来挂着一颗「🔁 换一个」—— 它把"本单位指着这一行的那一格"改成另一行，
        #    而"换"本质上就是**删除旧的 + 新增新的**。改引用请走：克隆一份（📄）或删掉再新增。

        self._clone = QPushButton("📄 克隆一份")
        self._clone.setProperty("role", "row-tool")
        self._clone.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clone.setToolTip(
            "复制这一行给本单位专用，并把本单位指过去 ——\n别人用的还是原来那一行，不受影响"
        )
        self._clone.clicked.connect(self.clone_requested)
        self._clone.setVisible(False)
        head.addWidget(self._clone)

        self._back = QPushButton("⬅ 回上一层")
        self._back.setProperty("variant", "ghost")
        self._back.clicked.connect(self.back_requested)
        self._back.setVisible(False)
        #: 独立记一份"画没画"，不读 isVisible() —— 窗口没 show() 时整棵树都不可见
        #: （用例与冒烟就是这么跑的），读 isVisible() 会一律得到"没画"。
        self._has_back = False
        head.addWidget(self._back)
        root.addLayout(head)

        #: 「换…」就地冒出来的候选列表（不弹对话框）。
        self._picker: RowPicker | None = None
        self._picker_host = QWidget()
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(0, 0, 0, 0)
        self._picker_layout.setSpacing(0)
        self._picker_host.setVisible(False)
        root.addWidget(self._picker_host)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._list = EntryList()
        self._list.cell_activated.connect(self.cell_activated)
        self._list.link_activated.connect(self.link_activated)
        self._scroll.setWidget(self._list)
        root.addWidget(self._scroll, 1)

    # ------------------------------------------------------------------ #
    # 内容
    # ------------------------------------------------------------------ #

    def set_cells(
        self,
        cells: Sequence[FieldCell],
        *,
        title: str,
        subtitle: str = "",
        summary: str = "",
        back_label: str | None = None,
        hits: Iterable[CellRef] = (),
        instance=None,
        link: CellRef | None = None,
        blocks: Sequence[QWidget] = (),
        mark: RowMark | None = None,
    ) -> None:
        """重建面板：**这一行的全部列**，按表的列序，一条一行。

        back_label 给了就画「⬅ …」那个按钮（导航栈里还有上一层时）。
        hits 是检索命中的落点（那一格标黄 + 挂「命中」）。
        instance + link 给了才画「📄 克隆一份」（link = 本单位指着它的那一格）。
        ⛔ 2026-10-19 起没有「🔁 换…」（用户命令删除）。
        blocks 是追加在词条**下面**的自定义块（弹药块走这里，见 EntryList.set_cells）。
        mark = 这一行的**图标位**（`RowMark`：真图 + 没图时的兜底 emoji）；
        给了真图就画真图（store 里还没到就等 `loaded` 再刷），没有就画 emoji，
        两样都没有整块藏起来（不留空框）。
        """
        self._close_picker()
        self._instance = instance
        self._link = link
        table = cells[0].table if cells else ""
        self._clone.setVisible(instance is not None and link is not None and table != "Units")
        self._mark = mark
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        self._summary.setText(summary)
        self.refresh_icon()
        self._has_back = back_label is not None
        self._back.setVisible(self._has_back)
        if back_label is not None:
            self._back.setText(back_label)
            self._back.setToolTip("回到上一层（Esc / 面包屑的父级也能回）")
        self._list.set_cells(cells, hits=hits, blocks=blocks)

    # ------------------------------------------------------------------ #
    # 抬头左边那个图标位（真图 or emoji，**只能有一个**）
    # ------------------------------------------------------------------ #

    def set_mark(self, mark: RowMark | None) -> None:
        """只换**图标位那一份**（抬头那个图标）：值不动、控件不重建。

        为什么要有它：`MainEditorPage.refresh()` 的契约是「**不重建右栏控件**」，
        于是它只刷值 —— 面板里记着的那份 `_mark` 从没更新过。改了缩略图键那种列之后，
        `refresh_icon()` 拿的还是**旧键**去取图，表现就是「图不跟」
        （用户口径 2026-10-01：「我改了的话，就要立即看到效果」）。
        """
        self._mark = mark
        self.refresh_icon()

    def set_icons(self, store) -> None:
        """接上**唯一**那份图标缓存（main_window 的 `self._armory.icons`）。

        store 为 None = 没装游戏 / 测试构造：图标位退回 emoji 兜底，不崩。
        """
        self._icon_store = store
        self.refresh_icon()

    def refresh_icon(self) -> None:
        """重画图标位（`icons.loaded` 到了要再调 —— 图是异步解析的）。

        有真图 -> 画真图；没真图 -> 画 `RowMark.emoji`；两样都没有 -> 整块藏起来。
        ❗ 取图只走 `IconStore.pixmap`（bundle 直读，ADR 0003）；这里**不碰**
        `cache/icons` 下的 PNG（那是没装游戏时的兜底，且旧提取把 151 张取成空心）。
        """
        mark = self._mark
        icon = mark.icon if mark is not None else None
        pixmap = None
        if icon is not None and self._icon_store is not None and icon.key:
            pixmap = self._icon_store.pixmap(icon.family, icon.key)
        if pixmap is not None and not pixmap.isNull():
            self._icon_shown = True
            self._icon_label.setText("")
            # ❗ 必须走 prepare_icon（= 按族翻 + DPR 缩放）：
            #    ① 直接 scaled(逻辑尺寸) 时 Qt 会在 150% 屏上**再放大 1.5 倍**去画，图标发虚
            #       （用户 2026-09-30 实机报「模糊」）；
            #    ② 武器 / 改型选项那两族原图**朝左**，界面要翻（用户 2026-10-01：
            #       「还是老问题，武器能不能让它们朝右」）—— 这条口径只有 prepare_icon 一份。
            self._icon_label.setPixmap(
                prepare_icon(
                    pixmap,
                    icon.family,
                    QSize(TITLE_ICON_HEIGHT * TITLE_ICON_MAX_RATIO, TITLE_ICON_HEIGHT),
                    self._icon_label,
                    icon.key,
                )
            )
            self._icon_label.setVisible(True)
            return
        fallback = mark.emoji if mark is not None else ""
        self._icon_shown = False
        self._icon_label.setPixmap(QPixmap())
        self._icon_label.setText(fallback)
        self._icon_label.setVisible(bool(fallback))

    @property
    def title_mark(self) -> RowMark | None:
        """这一行的图标位（用例与诊断用）；None = 这一行没有图标位。"""
        return self._mark

    @property
    def title_icon(self) -> RowIcon | None:
        """这一行该画的**真图**；None = 没登记 / 没有键 / 没有图标位。"""
        return self._mark.icon if self._mark is not None else None

    @property
    def title_icon_visible(self) -> bool:
        """图标位上真的画着**真图**吗（False = 正在用 emoji 兜底）。"""
        return self._icon_shown

    @property
    def title_mark_text(self) -> str:
        """图标位上现在这串字（有真图时是空串；兜底时是那个 emoji）。"""
        return self._icon_label.text()

    @property
    def title_icon_pixmap(self) -> QPixmap:
        """图标位上现在那张图（没图时是空 QPixmap）—— 用例按它验朝向/尺寸。"""
        return self._icon_label.pixmap()

    @property
    def rows(self) -> tuple[EntryRow, ...]:
        return self._list.rows

    @property
    def entry_list(self) -> EntryList:
        """里面那一串行（用例要按它断言"同时最多一行悬停"）。"""
        return self._list

    @property
    def title_text(self) -> str:
        return self._title.text()

    @property
    def subtitle_text(self) -> str:
        return self._subtitle.text()

    @property
    def summary_text(self) -> str:
        return self._summary.text()

    @property
    def back_label(self) -> str:
        return self._back.text() if self._has_back else ""

    def row_for(self, ref: CellRef) -> EntryRow | None:
        return self._list.row_for(ref)

    def refresh_values(
        self, lookup: Callable[[CellRef], FieldCell | None], dirty: Callable[[CellRef], bool]
    ) -> None:
        self._list.refresh_values(lookup, dirty)

    def scroll_to(self, ref: CellRef) -> bool:
        """滚到某一格（校验条 / 专用页点它时用）。找不到返回 False —— 不假装找到了。"""
        row = self._list.row_for(ref)
        if row is None:
            return False
        self._scroll.ensureWidgetVisible(row, 0, TOKENS.spacing.xxl)
        row.set_dirty(True)
        return True

    # ------------------------------------------------------------------ #
    # 就地编辑
    # ------------------------------------------------------------------ #

    def open_editor(self, cell: FieldCell, editor: QWidget) -> None:
        row = self._list.open_editor(cell, editor)
        if row is not None:
            self._scroll.ensureWidgetVisible(row, 0, TOKENS.spacing.xxl)

    def close_editor(self) -> None:
        self._list.close_editor()

    @property
    def has_editor(self) -> bool:
        return self._list.has_editor

    @property
    def open_cell(self) -> CellRef | None:
        return self._list.open_cell

    @property
    def open_row(self) -> EntryRow | None:
        """正在编辑的那一行（没有就是 None）。用例按它取编辑控件。"""
        return self._list.open_row

    # ------------------------------------------------------------------ #
    # 候选列表的收尾（「换」已按用户命令删除，这里只留关闭路径）
    # ------------------------------------------------------------------ #

    def _close_picker(self) -> None:
        if self._picker is None:
            self._picker_host.setVisible(False)
            return
        picker = self._picker
        self._picker = None
        self._picker_layout.removeWidget(picker)
        # ❗ 走 detach()，别写 setParent(None)：此刻 picker **是可见的**
        #    （_open_picker 里刚 setVisible(True)），可见控件被 setParent(None)
        #    会被 Qt 映射成一个顶层窗口 —— 用户报过三次的"一闪而过的小弹窗"
        #    （见 app/widgets/detach.py 的模块说明）。
        detach(picker)
        self._picker_host.setVisible(False)

    @property
    def picker_open(self) -> bool:
        return self._picker is not None

    @property
    def clone_visible(self) -> bool:
        return self._clone.isVisibleTo(self)


__all__ = [
    "COLUMN_COLUMN_WIDTH",
    "EDITOR_HEIGHT",
    "EMPTY_TEXT",
    "INDEX_COLUMN_WIDTH",
    "NAME_COLUMN_WIDTH",
    "ROW_HEIGHT",
    "EntryList",
    "EntryRow",
    "RowEntriesPanel",
]
