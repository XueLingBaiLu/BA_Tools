"""编成台右栏：**选中项的全部列**（＋ 外键那格的小 ↗ = 去看它指向的那一行）。

口径（docs/交接-2026-10-01-单位编成台设计.md §2/§3）：
- 右边永远是「刚点的那一行」的全部列 —— 编成台是入口，不替代数据表；
- ⚠️ 2026-10-02：曾经挂在这里、后来搬到中栏 ⑤ 的「其他入口」（专精可用性 / 运输配对 /
  立绘音效 / 关系树 / 数据表页）已随中栏那一块**整块删掉** —— 前两样现在各自有正经的一栏，
  后两样本来就在顶栏切换钮 / 页签里；这一层只剩「抬头 + 这一行的列」。
- 不弹窗：这里只发信号，写状态栏是页面的事。

这一层零写库：只读 FieldCell（core/edit/cells.py）把值画出来。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import lru_cache

from PySide6.QtCore import Qt, Signal
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

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.bench_detail import LOCALIZED_COLUMNS, referenced_id
from ba_mod_maker.app.viewmodel.row_icon import RowMark
from ba_mod_maker.app.widgets.bench_kit import (
    BenchAction,
    BenchRow,
    IconLabel,
    id_badge_text,
    restyle,
)
from ba_mod_maker.app.widgets.detach import close_editor_popups, detach
from ba_mod_maker.core.assets.localization import load_localization

#: ❗ 这里**不再截断**：用户 2026-10-01 原话「你能不能显示全了啊，武器的词条有这么多，
#: 难道我就只看这么点吗？」—— 右栏本来就是滚动区，武器 40 列 / 弹药 67 列全部画出来，
#: 「看这一行的全部 N 列」那颗按钮仍然通数据表页（那是**改值**的地方，不是"看得全"的替代）。

#: 右栏的两面（右上角那颗按钮来回切）。
MODE_COLUMNS = "columns"
MODE_DETAIL = "detail"

#: 详情标题那一格**图标位**的尺寸（用户 2026-10-02：「你给这个标题加个图标，
#: 一眼就能看出是什么武器」）。宽**钉住**：图标长宽比不同时标题会左右抖。
GROUP_ICON_HEIGHT = 18
GROUP_ICON_WIDTH = 24


class _ElidedValueButton(QPushButton):
    """右栏"值"那一格：**可以被压缩**，装不下就省略号（全名进 tooltip）。

    ❗ 为什么非它不可：QPushButton 的 minimumSizeHint 会跟着**文字宽度**长 ——
    像 `UNIT_SOUND_PRESET_US_INFANTRY_SCOUTS` 这种长值会把整行撑到比视口还宽，
    于是右栏内容横向溢出（实测 viewport=450 / content=494）。
    后果不是"右边被切"这么简单：点开编辑栏、焦点落进来时 Qt 为了把输入框露出来，
    会把滚动区**向右滚**（横向条被我设成隐藏，用户滚不回来）——
    整列中文名的**左边**就这样被推出去了。用户原话：「开始是好的，但点了一下编辑栏，就成这样了」。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._apply_elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        room = max(24, self.width() - 12)
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, room)
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


def _line_ref_id(source) -> int:
    """这一行的值**指向哪一行**（外键的原始 Id）；不是外键 / 取不到 / 0 都给 0。

    两种来源各说各话，判据只有一份：

    - **列**视图的 source 是 FieldCell -> 交给 viewmodel 的 referenced_id（它按
      FieldCell.foreign 判，不看列名里有没有 "ID"）；
    - **详情**视图的 source 是 DetailLine -> 它自己带着 ref_id（同一个函数算的）。

    右栏把非 0 的那个画成值**右边**的一小段弱化小字（用户口径 2026-10-01：
    「所有出现诸如单位、武器、弹药、炮塔 ID 的时候，都在右边显示其 ID」）。
    """
    if source is None:
        return 0
    own = getattr(source, "ref_id", None)
    if isinstance(own, int) and own > 0:
        return own
    return referenced_id(source)


def _id_text(ref_id: int) -> str:
    """右边那一小段写什么；0 = 什么都不写（空 QLabel 不占宽）。

    ❗ 格式只有一份实现（`bench_kit.id_badge_text`）—— 中栏 4 块与左卡用的是同一个：
    同一行在两张面上显示的必须是同一个数、同一个写法。
    """
    return id_badge_text(ref_id)


def _line_label(source) -> str:
    """一行数据源的名字（FieldCell 用词典中文名 / DetailLine 用自己的 label）。"""
    if hasattr(source, "column"):  # FieldCell
        return str(getattr(source, "cn", "") or getattr(source, "column", ""))
    return str(getattr(source, "label", "") or "")


@lru_cache(maxsize=1)
def _localizer():
    """本地化表（惰性加载一次；查不到的键 `text()` 返回空串）。"""
    return load_localization()


def _line_text(source) -> str:
    """一行数据源的**显示值**（FieldCell 用已经格式化好的 text，DetailLine 用 value）。

    ❗ 用户 2026-10-02：「像这样的 Custom_Option_40xS8，也要进行本地化显示」——
    UIName 那一族列里存的是**本地化键**，FieldCell 只把它当普通文本，所以右栏一直在
    显示键名。实测 Options.UIName 的 1578 条键 **1578/1578 都翻得出来**
    （列名清单在 `bench_detail.LOCALIZED_COLUMNS`，右栏与专用页共用一把尺子）。
    翻不出来就照旧用原文（那几列里也有本身就是明文的，比如 M-SHORAD, Stingers）。
    """
    if hasattr(source, "column"):  # FieldCell
        text = str(getattr(source, "text", "") or "")
        column = str(getattr(source, "column", "") or "")
        raw = getattr(source, "raw", None)
        if column in LOCALIZED_COLUMNS and isinstance(raw, str) and raw:
            return _localizer().text(raw) or text
        return text
    return str(getattr(source, "value", "") or "")


#: 右栏详情里"记住哪些组被展开过"的**行数上限**。
#: ❗ 两头都不能走：行是随用户点选无限增的，不封顶就是一个常驻小泄漏（一晚上点过几百行
#: 全留着）；而一换行就忘，用户展开过的那一下又白点了。所以给一个上限 ——
#: 满了丢**最早**那条（FIFO）：被丢的那一行只是回到默认（全收起），不会画错。
#: 口径由 Lead 2026-10-02 定。
MAX_REMEMBERED_ROWS = 200


class _GroupMemory:
    """「哪一行里哪些组被用户展开过」—— 按 (表, 行 Id) 记，满了 FIFO 丢最早的。

    ❗ 键是**行**、不是组标题：同一行切列↔详情 / reload / 重画回来，
    仍保持用户当时的展开态（用户 2026-10-02：「所有能做成折叠栏的都做成折叠栏」＋
    「折叠栏默认是折叠状态」）；换到别的行按**那一行自己**的记忆，第一次见 = 全收起。
    同名组跨行因此不会串味。
    """

    def __init__(self, limit: int = MAX_REMEMBERED_ROWS) -> None:
        self.limit = int(limit)
        self._rows: dict[tuple[str, int], set[str]] = {}

    def is_open(self, key: tuple[str, int], title: str) -> bool:
        """这一行的这一组，用户展开过没有（没记过 = 收起）。"""
        return title in self._rows.get(key, ())

    def set_open(self, key: tuple[str, int], title: str, *, expanded: bool) -> None:
        """记下这一行的这一组展开了 / 收起了。"""
        remembered = self._rows.get(key)
        if remembered is None:
            if self.limit > 0 and len(self._rows) >= self.limit:
                # FIFO：dict 保序，最早插进来的那个就是"最早那条"。
                self._rows.pop(next(iter(self._rows)), None)
            remembered = set()
            self._rows[key] = remembered
        if expanded:
            remembered.add(title)
        else:
            remembered.discard(title)

    def titles(self, key: tuple[str, int]) -> set[str]:
        """这一行被展开过的组标题（**副本** —— 调用方改不到里面那份）。"""
        return set(self._rows.get(key, ()))

    def rows(self) -> tuple[tuple[str, int], ...]:
        """现在记着哪些行（读数口：用例按它看上限有没有兜住）。"""
        return tuple(self._rows)


class _GroupHead(QWidget):
    """右栏分组抬头那颗**可点的**壳：点一下 = 展开 / 收起这一组下面的行。

    手感照 bench_kit._SectionHead（那一套是冻结件，不许改）：
    只认"点在我自己身上"那一下（`rect().contains`）—— 抬头右边将来要是有按钮，
    它自己吃掉点击，不会被顺手当成折叠。
    """

    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _RailGroup:
    """详情里的一组的折叠态：抬头（▸/▾ 单占一格 + 标题）＋ 这一组下面的行控件。

    **收起 = 只 hide 行，控件一个都不销毁**（AGENTS §0.7）—— 展开回来原样、不重建、不闪。

    ❗ foldable=False（**只有一组**时）：不画记号、抬头不可点、行**一直露着**。
    用户 2026-10-02：「这种地方你就别搞折叠栏了，又小又难看」——
    与中栏那条既有口径同一条：「只有一条的不套折叠，纯粹的画蛇添足」。
    """

    __slots__ = (
        "caret",
        "collapsed",
        "emoji",
        "foldable",
        "head",
        "icon",
        "label",
        "mark",
        "rows",
        "title",
    )

    def __init__(
        self,
        title: str,
        head: QWidget,
        caret: QLabel,
        label: QLabel,
        *,
        foldable: bool = True,
        icon: IconLabel | None = None,
        emoji: QLabel | None = None,
        mark: RowMark | None = None,
    ) -> None:
        self.title = str(title)
        self.head = head
        self.caret = caret
        self.label = label
        #: 这一组能不能折（≥2 组才给折）—— 见类 docstring。
        self.foldable = bool(foldable)
        #: 图标位（真图 + emoji 兜底）与它该画的那一行（`RowMark`）。
        self.icon = icon
        self.emoji = emoji
        self.mark = mark
        self.rows: list[QWidget] = []
        #: 可折的默认收起（用户口径）；只有一组时**永远展开**。
        self.collapsed = self.foldable

    def refresh_icon(self, store) -> None:
        """重画图标位：有真图 -> 真图；没真图 -> `mark` 的 emoji 兜底；都没有 -> 藏起来。

        ❗ 取图只经 `IconLabel.set_source`（内部走 `prepare_icon`：族翻转 + DPR；
        §0.7 那条口径），这里**不碰** `cache/icons` 下的 PNG。
        """
        if self.icon is None or self.emoji is None:
            return
        mark = self.mark
        source = mark.icon if mark is not None else None
        self.icon.set_source(
            store,
            source.family if source is not None else "",
            source.key if source is not None else "",
        )
        if self.icon.has_icon():
            self.icon.setVisible(True)
            self.emoji.setVisible(False)
            return
        fallback = mark.emoji if mark is not None else ""
        self.icon.setVisible(False)
        self.emoji.setText(fallback)
        self.emoji.setVisible(bool(fallback))

    def add_row(self, row: QWidget) -> None:
        """这一组下面多一行（画的时候挂进来）；收起态挂进来就是 hidden。"""
        self.rows.append(row)
        row.setVisible(not self.collapsed)

    def set_collapsed(self, collapsed: bool) -> bool:
        """收起 / 展开（返回**最后**是不是收起）。收起只 hide，控件一个都不销毁。

        ❗ 不可折的那一种（只有一组）永远给 False：行**一直露着** —— 它的抬头
        根本不是折叠栏（`_add_group` 连 clicked 都不接）。
        """
        if not self.foldable:
            for row in self.rows:
                row.setVisible(True)
            return False
        self.collapsed = bool(collapsed)
        self.caret.setText("▸" if self.collapsed else "▾")
        self.head.setToolTip("点一下展开这一组" if self.collapsed else "点一下收起这一组")
        for row in self.rows:
            row.setVisible(not self.collapsed)
        return self.collapsed


class BenchRail(QFrame):
    """右栏：选中项的全部列 + 入口。

    信号
    ----
    link_activated(table, row_id)
        点了**某一列外键**右边那颗小 ↗（去看那一列指向的行）。
        ❗ 2026-10-02：右上角那颗「在数据表页里打开这一行」已按用户要求删掉
        （原话：「我不要转到行的这个功能，删掉」）—— 行级跳转只剩这一条。
    cell_activated(ref)
        点了某一行的值 —— 页面就地开编辑器（用户口径：「右边的数据我要都能改」）。
    """

    #: 点了某一行的值（参数：core/edit/refs.CellRef）—— 页面据此就地开编辑器。
    #: 用户 2026-10-01：「右边的数据我要都能改啊，我不能改我看着干嘛？」
    cell_activated = Signal(object)
    #: 「↗ 去看那一列指向的那一行」：外键那一格右边那颗小箭头（表名, 行 Id）。
    #: 指向"关联表"时给的是**它真正指着的单位**（`FieldCell.link`）。
    #: ❗ 行级那颗 ↗（"在数据表页打开当前这一行"）2026-10-02 已按用户要求删掉 —— 只剩这一条。
    link_activated = Signal(str, int)
    #: 详情里某一组展开/收起之后发一次（参数：组标题, 现在是不是收起）。
    #: 手感与中栏 `BenchSection.toggled` 同一套（那是冻结件，右栏自己实现一遍）。
    group_toggled = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-rail")
        self.setMinimumWidth(300)
        self.setMaximumWidth(TOKENS.size.detail_panel_width + 60)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 抬头 ------------------------------------------------------ #
        head = QWidget()
        head.setProperty("role", "bench-section-head")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head_box = QHBoxLayout(head)
        head_box.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.sm, TOKENS.spacing.sm, TOKENS.spacing.sm
        )
        head_box.setSpacing(TOKENS.spacing.sm)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(TOKENS.spacing.xxs)
        self._title = QLabel("没有选中任何东西")
        self._title.setProperty("role", "bench-rail-title")
        self._subtitle = QLabel("点中栏任意一行 / 一格，这里就换成它的全部列")
        self._subtitle.setProperty("role", "bench-rail-sub")
        self._subtitle.setWordWrap(True)
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        head_box.addLayout(titles, 1)
        # 右上角那颗切换钮 —— 用户 2026-10-01：「可以在右上角加个按钮，这样就切换自如了」。
        self._toggle = BenchAction(
            "🔍 详情", tooltip="看这一行指向的那一行（武器 / 弹药 / 属性本体）"
        )
        self._toggle.clicked.connect(self.toggle_mode)
        head_box.addWidget(self._toggle, 0, Qt.AlignTop)
        # ❗ 2026-10-02：右上角那颗 ↗（"在数据表页里打开这一行"）按用户要求**整颗删掉**了
        # （原话：「我不要转到行的这个功能，删掉」）。理由本来就成立：右栏已经把这一行的
        # 全部列都画出来了（还都能就地改），那颗按钮是重复入口。
        # ⛔ 别再往右栏抬头加第二颗行级跳转按钮 —— 行级只剩"某一格外键指向的那一行"那一条。
        root.addWidget(head)

        # ---- 选中项那一行的全部列 -------------------------------------- #
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setProperty("role", "bench-body")
        self._rows_box = QVBoxLayout(holder)
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(TOKENS.spacing.xxs)
        self._scroll.setWidget(holder)
        root.addWidget(self._scroll, 1)

        # ❗「其他入口」那排胶囊 2026-10-01 搬到中栏 ⑤、2026-10-02 又随 ⑤ **整块删掉**
        #   （用户：「下面这个其它入口就可以不需要了」）—— 右栏只剩「抬头 + 这一行的列」，
        #   中栏也再没有那一块（bench_page 里连 OTHER_ENTRIES 都不在了）。
        self._row_id = 0
        self._table = ""
        self._value_labels: list[tuple[str, QLabel, QLabel]] = []
        #: 每行**值右边**那一小段弱化小字（外键的原始 Id）。与 `_value_labels` **按下标对齐**：
        #: 两者都在 _add_line 里成对 append、在 clear() 里一起清。
        #: 为什么单开一个 list 而不把它塞进 _value_labels 那个三元组：
        #: 已有用例按三元组解包（tests/test_bench_detail.py::test_all_rows_are_editable 之类），
        #: 为了一个显示细节去改公共读数的形状不划算。
        self._id_labels: list[QLabel] = []
        #: 中文名那一格与它的完整文本（宽度要按最长那个统一算，见 _finish_key_column）。
        self._key_labels: list[QLabel] = []
        self._key_texts: list[tuple[str, str]] = []
        self._group_labels: list[QLabel] = []
        #: 详情里每一组（抬头 + 它下面那批行）—— 与 `_group_labels` 按下标对齐。
        self._groups: list[_RailGroup] = []
        #: 现在正在画的那一组：`_add_line` 把行挂进它；不在任何组里时是 None。
        self._current_group: _RailGroup | None = None
        #: 用户展开过的组，按**行**记（见 `_GroupMemory`）—— 跨重画/换面保住用户那一下。
        self._group_memory = _GroupMemory()
        #: 图标缓存（与中栏各块 / 左卡**同一份**）—— 详情标题那一格的图标位由它给。
        self._icons = None
        self._store_hooked = False
        self._extra_rows: list[BenchRow] = []
        #: 现在这一行（两种视图共用同一份数据源）。
        self._cells: tuple = ()
        self._detail = None
        #: 每一行背后的**数据源**（FieldCell / DetailLine）—— 刷值时按它取新文本，不重建控件。
        self._value_sources: list = []
        #: 上一次真画出来的指纹（见 _render_key）—— 用来判"只刷值"还是"重画"。
        self._last_key: tuple = ()
        self._head_text: tuple[str, str] = ("", "")
        #: 现在挂着的就地编辑器（同时最多一个），以及它顶替掉的那一格。
        self._editor: QWidget | None = None
        self._edit_host: tuple[QHBoxLayout, QWidget] | None = None
        #: 用户挑过哪一面：默认**详情**（用户要的就是它）；点过「列」之后就一直保持列。
        self._prefer_columns = False

    # ------------------------------------------------------------------ #
    # 入口胶囊
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 画选中项
    # ------------------------------------------------------------------ #

    def set_selection(
        self,
        table: str,
        row_id: int,
        cells: Sequence,
        *,
        detail=None,
        title: str = "",
        subtitle: str = "",
        extra: Iterable[BenchRow] = (),
    ) -> None:
        """右栏换成这一行：**列**与**详情**两份数据一起给，按当前那面画。

        cells  = core/edit/cells.py::FieldCell 序列（context.cells_of_row(...) 直接给）
        detail = app/viewmodel/bench_detail.py::BenchDetail，None = 这一行没有可展开的详情
                 （那颗切换钮会禁用，并在 tooltip 里说清为什么）
        """
        # ❗ **先比、再赋值**：写反了 same_selection 恒为真 → 切到另一行也走"只刷值"那条路，
        #    于是正文换成新行了、**标题还留着上一行的**。
        #    实机症状（用户 2026-10-01）：「我都选了 AT 了，怎么红圈处还是 AA？」——
        #    标题写着 Options#1857（AA），正文第一行却是「选项 ID（主键）1497」（AT）。
        same_selection = (str(table), int(row_id)) == (self._table, self._row_id)
        self._table = str(table)
        self._row_id = int(row_id)
        self._cells = tuple(cells)
        self._detail = detail
        #: 抬头 = (标题, 说明)。说明留空时按当前那一面现拼 —— 切面时它要跟着变。
        self._head_text = (title or f"{table} #{row_id}", subtitle)
        # 判据要**连"画的是哪一面、哪些行"一起比**：只看 (表, 行) 会漏掉
        # "同一行从列切到详情"这种内容变了的情况（实测：分组标题会留在上一面）。
        sources = (
            [line for group in detail.groups for line in group.rows]
            if (detail is not None and self.mode() == MODE_DETAIL)
            else list(self._cells)
        )
        if (
            same_selection
            and self._value_sources
            and self._render_key() == self._last_key
            and self._update_values(sources)
        ):
            # ❗ **同一次选中内改一格**：只把文字换掉，控件一个都不重建。
            #    重建 116 行会把滚动条甩回顶部、整块重画一遍 —— 用户原话
            #    「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
            # ❗ 标题/说明**必须跟着换**：行的结构一样不代表还是同一行
            #    （同一张表的两行，标签完全一样）。
            self._sync_head()
            self._sync_toggle()
            return
        self._render(extra=extra)

    def show_row(
        self,
        table: str,
        row_id: int,
        cells: Sequence,
        *,
        title: str = "",
        subtitle: str = "",
        extra: Iterable[BenchRow] = (),
    ) -> None:
        """只给列那份（兼容旧调用）：等于 set_selection(detail=None)。"""
        self.set_selection(
            table, row_id, cells, detail=None, title=title, subtitle=subtitle, extra=extra
        )

    # ---- 两面切换 ------------------------------------------------------ #

    def has_detail(self) -> bool:
        return self._detail is not None

    def mode(self) -> str:
        """现在画的是哪一面（"detail" / "columns"）。"""
        return MODE_DETAIL if (self.has_detail() and not self._prefer_columns) else MODE_COLUMNS

    def set_mode(self, mode: str) -> bool:
        """切到指定那一面；这一行没有详情时切不过去（返回 False）。"""
        if mode == MODE_DETAIL and not self.has_detail():
            return False
        self._prefer_columns = mode == MODE_COLUMNS
        self._render()
        return True

    def toggle_mode(self) -> bool:
        """右上角那颗按钮：列 ↔ 详情。"""
        if not self.has_detail():
            return False
        return self.set_mode(MODE_COLUMNS if self.mode() == MODE_DETAIL else MODE_DETAIL)

    def toggle_button(self) -> BenchAction:
        return self._toggle

    # ---- 滚动位置（重建前后要保住 —— 见 docs/交接-2026-09-30-夜-实机反馈批.md:18） ---- #

    def scroll_value(self) -> int:
        return self._scroll.verticalScrollBar().value()

    def set_scroll_value(self, value: int) -> None:
        self._scroll.verticalScrollBar().setValue(int(value))

    def detail_groups(self) -> tuple:
        return tuple(self._detail.groups) if self.has_detail() else ()

    # ---- 画 ------------------------------------------------------------ #

    def _render_key(self) -> tuple:
        """ "现在应该画成什么样"的指纹：哪一面 + 逐行标签。一样就只刷值、不重建控件。"""
        if self.mode() == MODE_DETAIL and self._detail is not None:
            labels = tuple(line.label for group in self._detail.groups for line in group.rows)
        else:
            labels = tuple(
                str(getattr(cell, "cn", "") or getattr(cell, "column", "")) for cell in self._cells
            )
        return (self.mode(), labels)

    def _render(self, *, extra: Iterable[BenchRow] = ()) -> None:
        self.clear()
        detail_mode = self.mode() == MODE_DETAIL
        self._sync_head()
        if detail_mode:
            # ❗ **只有一组就不套折叠栏**（用户 2026-10-02：「这种地方你就别搞折叠栏了，
            #    又小又难看」）—— 与中栏那条既有口径同一条：只有一条的不套折叠。
            foldable = len(self._detail.groups) >= 2
            for group in self._detail.groups:
                self._add_group(group.title, foldable=foldable, mark=group.mark)
                for line in group.rows:
                    self._add_line(
                        line.label, line.value, hint=line.hint, ref=line.ref, source=line
                    )
        else:
            for cell in self._cells:
                # ❗ 列视图**每一格都能改**（FieldCell 自带 ref）—— 用户口径：
                #    「右边的数据我要都能改啊，我不能改我看着干嘛？」
                self._add_line(
                    str(getattr(cell, "cn", "") or getattr(cell, "column", "")),
                    _line_text(cell),  # 本地化键先翻（见 _line_text 的说明）
                    hint=f"{self._table}.{getattr(cell, 'column', '')}",
                    ref=getattr(cell, "ref", None),
                    source=cell,
                )
        # extra 那几行不属于任何一组（它们是"入口"那批），别被顺手收掉。
        self._current_group = None
        for row in extra:
            self._rows_box.addWidget(row)
            self._extra_rows.append(row)
        self._rows_box.addStretch(1)
        self._finish_key_column()
        self._sync_toggle()
        self._last_key = self._render_key()

    def _sync_head(self) -> None:
        """抬头那一行（标题 + 灰字说明）按当前 _head_text 重画。

        `_render` 里也调它 —— 抽出来是为了让"只刷值"那条路也能换标题。
        """
        title, subtitle = self._head_text
        if not subtitle:
            subtitle = (
                self._detail.subtitle
                if (self.mode() == MODE_DETAIL and self._detail is not None)
                else f"共 {len(self._cells)} 列 · 改完只进缓存，写盘在顶栏「保存」"
            )
        self._title.setText(title or f"{self._table} #{self._row_id}")
        self._subtitle.setText(subtitle)

    def _finish_key_column(self) -> None:
        """中文名那一列：按**这一批最长的那个**统一给宽，装不下就省略号 + tooltip。

        留 42% 的天花板 —— 名字再长也不许把值挤没（值才是要改的东西）。
        """
        if not self._key_labels:
            return
        need = max(label.sizeHint().width() for label in self._key_labels)
        ceiling = max(120, int(self.width() * 0.42) if self.width() else 160)
        width = max(96, min(need, ceiling))
        fm = self._key_labels[0].fontMetrics()
        for label, (full, hint) in zip(self._key_labels, self._key_texts, strict=True):
            label.setFixedWidth(width)
            label.setText(full)
            if fm.horizontalAdvance(full) > width - 4:
                label.setText(fm.elidedText(full, Qt.ElideRight, width - 4))
            # ❗ 提示**一律挂上**（不再"只在被省略时"）—— 用户 2026-10-03：
            #    「**我右边的词条注释悬浮窗你给我加回来啊**」。
            #    ⚠️ 这一句**把 2026-10-02 那条收紧反过来**了，别再顺手翻回去：
            #      那一轮用户对着浮出来的「缺省选中 ／ Options.IsDefault」说「改掉改掉」，
            #      收紧的是"名字明明看得见还弹框"；这一轮他要的是**每条都能看到这一格
            #      对应哪一列**（第二行的 hint，例：Units.HUDName）—— 两个要求只能这么同时满足：
            #      提示照挂，第一行给全名（截断时补全），第二行给列名。
            label.setToolTip(f"{full}\n{hint}".strip() if hint else full)

    def _sync_toggle(self) -> None:
        # ❗ 这一行**没有可展开的详情 → 整颗按钮藏起来**（不再 setEnabled(False) 灰着占位）。
        #    用户 2026-10-03 指着截图里 SpecializationAvailabilities 那行的按钮说：
        #    「**如果说这个按钮按不了的话，就把它隐藏起来**」。
        #    藏它不占位（布局把那一格收回），换到有详情的行时再露出来 —— 所以下面那一支
        #    每一支都要先 setVisible(True)。
        if not self.has_detail():
            self._toggle.setText("🔍 详情")
            self._toggle.setToolTip("")
            self._toggle.setVisible(False)
            return
        self._toggle.setVisible(True)
        self._toggle.setEnabled(True)
        if self.mode() == MODE_DETAIL:
            self._toggle.setText("📋 列")
            self._toggle.setToolTip("换成「这一行的全部列」（原始列名 + 值）")
        else:
            self._toggle.setText("🔍 详情")
            self._toggle.setToolTip(
                "换成「它指向的那一行」的全部列（武器 / 弹药 / 装甲 / 机动 / 能力 / 传感器的本体）"
            )

    def _add_group(self, text: str, *, foldable: bool = True, mark: RowMark | None = None) -> None:
        """详情里的一组：**可点的抬头**（▸/▾ 单占一格 + 标题），**默认收起**。

        ❗ foldable=False（**只有一组**）：抬头**不是**折叠栏 —— 不给手型、不接 clicked、
        记号藏起来，标题换成正经一行（role `bench-rail-group-title`，size-sm）而不是
        折叠栏那种 xs 小字。用户 2026-10-02：「这种地方你就别搞折叠栏了，又小又难看」。

        ❗ 折叠记号**不并进标题文字**：那颗标题 QLabel 的原样是既有读数口
        （`group_titles()` / qss.py 的 `QLabel[role="bench-rail-group"]`）——
        记号另起一格（role = `bench-rail-group-caret`），标题一个字都不加。
        """
        head = _GroupHead()
        head.setProperty("role", "bench-rail-group-head")
        head.setCursor(Qt.PointingHandCursor if foldable else Qt.ArrowCursor)
        head_box = QHBoxLayout(head)
        # 与原来的分组 QLabel 同一份外边距（现在由**抬头**吃这份边距，标题回到 0）。
        head_box.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.sm, TOKENS.spacing.md, TOKENS.spacing.xxs
        )
        head_box.setSpacing(TOKENS.spacing.xs)
        caret = QLabel()
        caret.setProperty("role", "bench-rail-group-caret")
        # 图标位：真图（`IconLabel` 走 prepare_icon）与 emoji 兜底同位，同时只显示一个
        # （`RowMark` 的口径：图标位只能有一个）。
        icon = IconLabel(GROUP_ICON_HEIGHT, width=GROUP_ICON_WIDTH)
        emoji = QLabel("")
        emoji.setProperty("role", "bench-icon")
        emoji.setFixedSize(GROUP_ICON_WIDTH, GROUP_ICON_HEIGHT)
        emoji.setAlignment(Qt.AlignCenter)
        label = QLabel(text)
        # ❗ 一组时是**正经一行**（新角色），不是折叠栏那档 xs 小字（用户嫌又小又难看）。
        label.setProperty("role", "bench-rail-group" if foldable else "bench-rail-group-title")
        label.setWordWrap(True)
        head_box.addWidget(caret, 0, Qt.AlignTop)
        head_box.addWidget(icon, 0, Qt.AlignVCenter)
        head_box.addWidget(emoji, 0, Qt.AlignVCenter)
        head_box.addWidget(label, 1)
        # ❗ 先挂进布局（拿到父控件）再谈可见性 —— 反过来藏控件会被映射成顶层小窗。
        caret.setVisible(foldable)
        group = _RailGroup(
            text, head, caret, label, foldable=foldable, icon=icon, emoji=emoji, mark=mark
        )
        group.refresh_icon(self._icons)
        if foldable:
            head.clicked.connect(lambda g=group: self._toggle_group(g))
        self._rows_box.addWidget(head)
        self._groups.append(group)
        self._group_labels.append(label)
        self._current_group = group
        # 这一行用户上次展开过这一组就还展开着；没记过 = 默认收起（不可折的恒展开）。
        group.set_collapsed(foldable and not self._group_memory.is_open(self._row_key(), text))

    # ---- 详情标题那一格的图标位 ---- #

    def set_icons(self, store) -> None:
        """接一份图标缓存（与中栏各块 / 左卡**同一份**）：详情标题那一格的真图由它给。

        图是**异步**到的（`store.loaded` 到了才拿得到）：这里自己接一次 `loaded`，
        到了再判"真图还是 emoji 兜底" —— `IconLabel` 只重画它自己的 pixmap，
        它不知道旁边还有个兜底位要跟着切（与 `bench_slots._GroupHead` 同一个坑）。
        """
        self._icons = store
        if store is not None and not self._store_hooked:
            loaded = getattr(store, "loaded", None)
            if loaded is not None:
                loaded.connect(self._sync_group_icons)
                self._store_hooked = True
        self._sync_group_icons()

    def _sync_group_icons(self) -> None:
        """把现在画着的每一组的图标位重画一次（**就地**，一个控件都不重建）。"""
        for group in self._groups:
            group.refresh_icon(self._icons)

    # ---- 分组折叠（详情那一面；手感照 BenchSection，读经见 _GroupHead） ---- #

    def _row_key(self) -> tuple[str, int]:
        """折叠态记忆的键：**哪一行**（表, 行 Id）—— 不是组标题（同名组跨行不该串味）。"""
        return (self._table, self._row_id)

    def _toggle_group(self, group: _RailGroup) -> bool:
        """点分组抬头那一下走它（发 `group_toggled`）。返回新的折叠态。"""
        if not group.foldable:
            # 只有一组时抬头根本不是折叠栏（连 clicked 都没接）—— 这里再拦一道。
            return False
        state = group.set_collapsed(not group.collapsed)
        self._group_memory.set_open(self._row_key(), group.title, expanded=not state)
        # ❗ 展开这一帧就要有正确几何：不激活时 LayoutRequest 是**排队**处理的，
        #    实机抓帧会看到展开那一块空一拍（与 attach_editor 同一个坑，AGENTS §0.7）。
        self._rows_box.activate()
        self.group_toggled.emit(group.title, state)
        return state

    def _group_of(self, title: str) -> _RailGroup | None:
        for group in self._groups:
            if group.title == title:
                return group
        return None

    def show_message(self, text: str) -> None:
        """没有选中项时的一句说明（不许留白）。"""
        self._cells = ()
        self._detail = None
        self._head_text = ("没有选中任何东西", text)
        self._render()

    # ---- 给用例的读数（续） -------------------------------------------- #

    def group_titles(self) -> tuple[str, ...]:
        """每一组的**标题原文**（折叠记号不在里面 —— 它是单占一格的另一个控件）。"""
        return tuple(label.text() for label in self._group_labels)

    def group_head(self, title: str) -> QWidget | None:
        """按组标题取那颗**可点的抬头**（用例 QTest.mouseClick 它 = 用户点抬头空白）。"""
        group = self._group_of(title)
        return group.head if group is not None else None

    def group_caret(self, title: str) -> QLabel | None:
        """那一组的 ▸/▾（单占一格那一位）—— 用例按它读折叠态，不去数字符。

        ❗ 只有一组时它是**藏着的**（那一组不是折叠栏）—— 用例按 `isHidden()` 读这一条。
        """
        group = self._group_of(title)
        return group.caret if group is not None else None

    def group_foldable(self, title: str) -> bool:
        """这一组能不能折（**≥2 组**才给折）—— 见 _RailGroup 的 docstring。"""
        group = self._group_of(title)
        return bool(group.foldable) if group is not None else False

    def group_icon_family(self, title: str) -> str:
        """图标位上真图的族名（没真图时是空串）。"""
        group = self._group_of(title)
        return group.icon.family if group is not None and group.icon is not None else ""

    def group_icon_key(self, title: str) -> str:
        """图标位上真图的键（没真图时是空串）。"""
        group = self._group_of(title)
        return group.icon.key if group is not None and group.icon is not None else ""

    def group_has_real_icon(self, title: str) -> bool:
        """图标位上画的是**真图**吗（False = 正在用 emoji 兜底 / 两样都没有）。"""
        group = self._group_of(title)
        return bool(group is not None and group.icon is not None and group.icon.has_icon())

    def group_mark_emoji(self, title: str) -> str:
        """这一组登记的兜底 emoji（`RowMark.emoji`）。"""
        group = self._group_of(title)
        return group.mark.emoji if group is not None and group.mark is not None else ""

    def group_icon_visible(self, title: str) -> bool:
        """图标位上真的有东西吗（真图 or emoji）。

        ❗ **不读 isVisible()** —— 窗口没 show 时整棵树恒 False（与中栏 `mark_visible` 同一条）。
        """
        group = self._group_of(title)
        if group is None or group.icon is None:
            return False
        if group.icon.has_icon():
            return True
        return bool(group.emoji.text()) if group.emoji is not None else False

    def group_rows(self, title: str) -> tuple[QWidget, ...]:
        """那一组下面的行控件（收起 = 它们 hide，**控件一个都不销毁**）。"""
        group = self._group_of(title)
        return tuple(group.rows) if group is not None else ()

    def group_collapsed(self, title: str) -> bool:
        """那一组现在是不是收起的（找不到这个标题也给 True，与"默认收起"同一口径）。"""
        group = self._group_of(title)
        return True if group is None else group.collapsed

    def expanded_groups(self) -> tuple[str, ...]:
        """**这一行**被用户展开过的组标题（读数口，不暴露内部那份记忆）。

        顺序：先按当前画出来的组顺序，剩下的按标题排（换到"列"那一面时
        `group_titles()` 是空的，这时读的就是"记着哪些"）。
        """
        remembered = self._group_memory.titles(self._row_key())
        shown = [title for title in self.group_titles() if title in remembered]
        return tuple(shown) + tuple(sorted(remembered - set(shown)))

    def value_widget(self, label: str) -> QWidget | None:
        """按中文名取那一行的**值控件**（可改的行是按钮，算出来的行是标签）。"""
        for key, _name, widget in self._value_labels:
            if key == label:
                return widget
        return None

    def editable_labels(self) -> tuple[str, ...]:
        """哪些行现在能就地改（值那一格是 QPushButton 的）。"""
        return tuple(
            key for key, _name, widget in self._value_labels if isinstance(widget, QPushButton)
        )

    # ---- 就地编辑（与三栏那套同一个 InlineEditor，接法照抄 EntryRow.attach_editor） ---- #

    def _activate_cell(self, ref, host: tuple[QHBoxLayout, QWidget]) -> None:
        """点了某一格：先把"编辑器该长在哪"定下来，再发信号（顺序不能反）。"""
        self._edit_host = host
        self.cell_activated.emit(ref)

    def attach_editor(self, editor: QWidget) -> None:
        """把编辑控件放进**值那一格**（就在点的地方改）。

        ❗ 行高写死：全局 QSS 给输入框 min-height: 28px，比这一行还高，
        不夹住就会被上下裁掉（三栏那边踩过一模一样的坑，见 row_entries.attach_editor）。
        """
        self.close_editor()
        host = self._edit_host
        if host is None:
            return
        line, value_widget = host
        self._editor = editor
        # ❗ 位掩码（「可瞄准目标」）是**列表式**：一行一位，高度要按内容放 —— 固定一行高
        #    会把后面几位裁掉（用户 2026-11-11：「被遮挡住了，无法正常修改」）。
        height = TOKENS.size.row_height
        preferred = getattr(editor, "preferred_height", None)
        if callable(preferred):
            height = preferred(height)
        editor.setFixedHeight(height)
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        value_widget.setVisible(False)
        line.insertWidget(line.indexOf(value_widget), editor, 1)
        editor.show()
        # ❗ 立刻激活布局：插进来的控件要**这一帧**就有正确位置。
        #    不激活的话，LayoutRequest 是**排队**处理的 —— 实机抓帧能看到右栏整块
        #    空一拍（用户说的"闪一下"就是这么来的）。先量后改：
        #    _work/probe_rail_editor_place.py 显示 processEvents 之后一切正常，
        #    而 grab() 抢在布局前就抓到空栏。
        holder = value_widget.parentWidget()
        if holder is not None and holder.layout() is not None:
            holder.layout().activate()
        # 兜一道：内容再宽也**不许横向滚**（一滚，中文名左边就被推出视口）。
        self._scroll.horizontalScrollBar().setValue(0)
        focus_input = getattr(editor, "focus_input", None)
        if callable(focus_input):
            focus_input()
        else:
            editor.setFocus()

    def close_editor(self) -> None:
        """收起就地编辑器，把原来那一格露回来。"""
        if self._editor is not None:
            # ❗ **先收浮层，再摘控件** —— 下拉/候选是顶层窗，不跟着销毁（用户截图那个黑框）。
            close_editor_popups(self._editor)
            detach(self._editor)
            self._editor = None
        if self._edit_host is not None:
            line, value_widget = self._edit_host
            value_widget.setVisible(True)
            holder = value_widget.parentWidget()
            if holder is not None and holder.layout() is not None:
                holder.layout().activate()

    def has_editor(self) -> bool:
        return self._editor is not None

    def editor(self) -> QWidget | None:
        """挂在值那一格上的编辑控件（用例按它检查浮层）。"""
        return self._editor

    def clear(self) -> None:
        if self._editor is not None:
            # 重画会把这一格整个换掉 —— 编辑器先摘掉，别留个孤儿浮层。
            # ❗ **先收浮层再摘控件**（与 `close_editor` 同一条）：下拉 / 候选是**顶层窗**，
            #    不随控件销毁 —— 这条从前只写了一半（只 `detach`），实测能留下一个黑框
            #    （用户 2026-10-02 截图：「有BUG，你看一下」）。
            close_editor_popups(self._editor)
            detach(self._editor)
            self._editor = None
        self._edit_host = None
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                detach(widget)
        self._value_labels.clear()
        self._id_labels.clear()
        self._value_sources.clear()
        self._key_labels.clear()
        self._key_texts.clear()
        self._group_labels.clear()
        self._groups.clear()
        self._current_group = None
        self._extra_rows.clear()

    def _add_line(self, key: str, value: str, *, hint: str = "", ref=None, source=None) -> None:
        """画一行。`ref` 不为空 → **值那一格可以点开就地改**（用户口径：都要能改）。

        `ref` 为空 = 这一行是**算出来的**（游戏信息卡那几行，比如「瞄准时间」是
        AimTimeMin/AimTimeMax 合并换算的），只能看：注释挂在中文名那一格的悬浮提示里，
        说清"要改就改下面那几列"。
        """
        holder = QWidget()
        holder.setProperty("role", "bench-slot-line")
        line = QHBoxLayout(holder)
        line.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.xxs, TOKENS.spacing.md, TOKENS.spacing.xxs
        )
        line.setSpacing(TOKENS.spacing.sm)
        key_label = QLabel(key)
        key_label.setProperty("role", "bench-rail-key")
        # ❗ 宽度**不写死**：中文名长短差很多（「所属步兵班 ID（占位）」要 113px），
        #    钉死 104 会把它硬切掉右边（用户报的"文字被遮挡"—— 裁像素量出来的）。
        #    真实宽度在 _finish_key_column() 里按这一批的**最长那个**统一给，
        #    装不下就省略号 + tooltip（不再硬切）。
        self._key_labels.append(key_label)
        self._key_texts.append((key, hint))
        text = value or "（空）"
        if ref is not None:
            value_widget: QWidget = _ElidedValueButton(text)
            value_widget.setProperty("role", "entry-value")  # 与三栏词条同一套"点一下就地改"
            value_widget.setProperty("empty", "false" if value else "true")
            value_widget.setCursor(Qt.PointingHandCursor)
            value_widget.setToolTip("点一下就地改（改完只进缓存，写盘在顶栏「保存」）")
            # ❗ 落点必须**跟着被点的那一行**：_edit_host 是"当前编辑器的位置"，
            #    在 _add_line 里直接赋值 = 永远剩最后画的那一行 —— 实机症状是
            #    "点第 3 行，编辑框长在第 6 行下面"（用户 2026-10-01 报的）。
            host = (line, value_widget)
            value_widget.clicked.connect(lambda _=False, r=ref, h=host: self._activate_cell(r, h))
        else:
            value_widget = QLabel(text)
            value_widget.setProperty("role", "bench-rail-value")
            value_widget.setToolTip(
                hint or "这一行是游戏算出来的（不是某一列）——要改就改同一组里对应的那几列"
            )
        value_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # ❗ 值**右边**那一小段：这一行的值指向哪一行（外键的原始 Id）。
        #    用户口径 2026-10-01：「所有出现诸如单位、武器、弹药、炮塔 ID 的时候，
        #    都在右边显示其 ID」；而在此之前它只在**点开编辑栏时**才出现 ——
        #    用户又补了一句：「在没有编辑栏的时候，也在右边显示 ID」。
        #    样式复用中栏 / 关系树那一套 `entry-id`（小徽章、弱化色），口径只有一份。
        #    非外键行写空串：空 QLabel 不占宽，行还是原来那个样子。
        id_label = QLabel(_id_text(_line_ref_id(source)))
        id_label.setProperty("role", "entry-id")
        id_label.setToolTip("这一行的值指向的那一行的 Id")
        # 外键那一格右边给一颗「↗ 去看它指向的那一行」——与三栏中栏那套同一个 role / 同一个含义。
        # ❗ 用户 2026-10-02 原话：「我要能直接跳转到那个单位」。
        #    目标取 `FieldCell.link`（关联表会翻成它指着的单位），不是 `foreign`。
        link = getattr(source, "link", None)
        if link is None:
            rid = _line_ref_id(source)
            foreign = getattr(source, "foreign", None)
            if rid and foreign:
                link = (foreign, rid)
        link_button = None
        if link is not None:
            link_button = QPushButton("↗")
            link_button.setProperty("role", "entry-link")
            link_button.setCursor(Qt.CursorShape.PointingHandCursor)
            link_button.setFixedWidth(22)
            link_button.setToolTip(f"去看 {text}（{link[0]} #{link[1]}）")
            link_button.clicked.connect(
                lambda _=False, t=link: self.link_activated.emit(str(t[0]), int(t[1]))
            )
        line.addWidget(key_label, 0, Qt.AlignTop)
        line.addWidget(value_widget, 1)
        if link_button is not None:
            line.addWidget(link_button, 0, Qt.AlignTop)
        line.addWidget(id_label, 0, Qt.AlignTop)
        self._rows_box.addWidget(holder)
        if self._current_group is not None:
            # 这一行属于当前那一组：收起态挂进来就是 hidden（控件照样在，只是不占位）。
            self._current_group.add_row(holder)
        self._value_labels.append((key, key_label, value_widget))
        self._id_labels.append(id_label)
        self._value_sources.append(source)

    def _update_values(self, sources: Sequence) -> bool:
        """把每行的**文字**换成最新的一份（控件不动）。对不上就返回 False → 调用方重画。

        ❗ 必须拿**新数据**（这一轮刚传进来的 cells / detail.groups）按位置写回 ——
        第一版读的是渲染时存下的那批 FieldCell，于是"控件保住了、值还是旧的"
        （实机读数：右栏还写着 ×80，而中栏与缓存都已经是 321）。
        """
        if len(sources) != len(self._value_labels):
            return False
        texts: list[str] = []
        for source, (key, _key_label, _widget) in zip(sources, self._value_labels, strict=True):
            if _line_label(source) != key:
                return False
            texts.append(_line_text(source))
        for text, (_key, _key_label, value_widget) in zip(texts, self._value_labels, strict=True):
            shown = text or "（空）"
            if isinstance(value_widget, _ElidedValueButton):
                value_widget.set_full_text(shown)  # 会自己按当前宽度省略
            else:
                value_widget.setText(shown)
            if isinstance(value_widget, QPushButton):
                value_widget.setProperty("empty", "false" if text else "true")
                restyle(value_widget)
        # ❗ 右边那一段也要跟着新数据走：换了主武器，Id 就从 #67 变成别的 ——
        #    只刷值不刷 Id 的话，行上写着新枪、右边还挂着旧 Id（"控件保住了、值还是旧的"同一个坑）。
        for source, id_label in zip(sources, self._id_labels, strict=True):
            id_label.setText(_id_text(_line_ref_id(source)))
        self._value_sources = list(sources)
        return True

    # ---- 给用例的读数 -------------------------------------------------- #

    def title_text(self) -> str:
        return self._title.text()

    def subtitle_text(self) -> str:
        return self._subtitle.text()

    def row_texts(self) -> tuple[tuple[str, str], ...]:
        return tuple((key, label.text()) for key, _k, label in self._value_labels)

    def current_row(self) -> tuple[str, int]:
        return (self._table, self._row_id)
