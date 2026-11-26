"""编成台 / 卡片**共用**的基础件（不是谁的私产：按口径加自己那一格）。

出处：docs/交接-2026-10-01-单位编成台设计.md §2/§3（四张设计图＋十条口径）。
口径（动手前先读一遍）：

1. **只有 ＋ 和 ✕，没有"换"** —— 换 = 删掉再新增，**不给它单独做一颗按钮**。
   用户 2026-10-02 复述（原话）：「我很讨厌"换"这个功能，它本质上就是删除再新增，
   你给这个功能单独做一个按钮，非常多余」。
   > 📌 **这一段来回过一次，别再翻第三遍**：
   > 同一天上午用户先选了"要换 / 要排序"，于是做了 🔁（换炮塔/武器/弹种）
   > ＋ ↑↓（行序）＋ ⇄（挪挂载槽）；
   > 下午用户推翻：「改顺序我还是直接改 order 吧，不用那三个按钮了」＋上面那句"讨厌换"。
   > ⇒ **中间栏只管 ＋ / ✕**；**"改某一格"一律去右栏**（点一行 → 右栏列出它的全部列，
   >   值那一格点开就改，外键那格自带挑行候选）。
   > ⛔ **别再加 🔁 / ↑↓ / ⇄ 这种"替你改一格"的按钮**。
   > ⚠️ 已知后果：改列值走的是**通用列编辑器**，共用行（`TurretWeapons` 不带 UnitId）被**右栏**改时
   >   不再有人数警告（✕ 删那条路的警告还在）。要补警告得在 rail 那一层做 —— 见交接档 §5。
2. **折叠栏**（用户 2026-10-02）：「把所有能做成折叠栏的都做成折叠栏」＋「折叠栏默认是
   折叠状态」→ `BenchSection` 自带折叠、**默认收起**；点抬头空白展开；抬头右边那几颗
   动作按钮照旧能点。⚠️ **范围只到编成台这一页**（用户选的那一档）——
   单位编辑器「关系树页」的中栏**不折**（那是另一条既有口径 + 一条哨兵用例）。
3. **骨架统一**：左 = 单位卡（只读＋实时预览）／中 = 可操作区／右 = 选中项＋入口；
3. 颜色**只能**从 app/theme/tokens.py 来；**选中态 = bg_active 底 + border_select 边**
   （不许回 accent 蓝 —— 用户 2026-10-01 口径，tests/test_theme.py 守着）；
4. 图标唯一入口是 `app/theme/icons.py::prepare_icon`（它负责 family_mirrors 翻转 + DPR），
   取图用 `shared_icon_cache()`；**界面不许自己 scaled() / transformed()**；
5. 改一块跑一块：改完 `.venv\\Scripts\\python.exe -c "import ba_mod_maker.app.widgets.bench_kit"`。

这一层只做"摆控件 + 发信号"，**一个字节都不写库** —— 写库一律经 EditorContext。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.theme.icons import prepare_icon
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.flow_layout import FlowLayout

#: 空的显示口径（与 app/widgets/row_entries.py::EMPTY_TEXT 一致）。
EMPTY_TEXT = "（空）"

#: 折叠记号 ▸/▾ 那一格的**点击矩形**（块抬头那一颗与炮塔行那一颗共用一档）。
#:
#: 用户 2026-10-02 实机原话：「我展开折叠栏，**要点这个小三角形，很不好操作**」——
#: 实测改前只有 **8×14**（QSS 只钉了 min-width: spacing.md），点偏一两像素就落到行体上，
#: 而那一下是"选中这一行"，不是"折"。
#:
#: ❗ 尺寸**钉在代码里**、不靠 QSS：QSS 只在 apply_theme 之后才生效，而"点得到"是几何、
#: 不是配色 —— 这样不起主题的用例也能守住它（先例：bench-action 那条 QSS 盖掉
#: setFixedHeight 的坑，写在这两处都会打架，尺寸只留一个出处）。
#: 24×20 = 竖着铺满一行内高（行 = size.row_height + 2，上下各留 spacing.xs 的边距）。
#: ⚠️ 两个记号**必须同宽**：它们在屏幕上竖着排在同一列，一宽一窄会看着错位。
CARET_WIDTH = TOKENS.spacing.xxl
CARET_HEIGHT = TOKENS.size.row_height + 2 - 2 * TOKENS.spacing.xs


def restyle(widget: QWidget) -> None:
    """改过动态属性（selected / hover / …）之后必须重算样式，否则 QSS 不重画。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class IconLabel(QLabel):
    """一个图标位：**异步**取图 —— store 里还没有就留空，loaded 到了自己补上。

    用法：`label.set_source(store, "weapon_icons", "INF_CQBR")`；store 传 None 时留空不崩。
    """

    def __init__(self, height: int, *, width: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-icon")
        self._height = max(1, int(height))
        self._width = max(0, int(width))
        self._store = None
        self._family = ""
        self._key = ""
        self._hooked = False
        self.setFixedHeight(self._height)
        if self._width:
            self.setFixedWidth(self._width)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    # ---- 取值 ---------------------------------------------------------- #

    def set_source(self, store, family: str, key: str) -> None:
        """接一份图标缓存 + 族 + 键。键为空 → 留空位（不画虚线框，图都取不到才是空）。"""
        self._store = store
        self._family = str(family or "")
        self._key = str(key or "")
        if store is not None and not self._hooked:
            loaded = getattr(store, "loaded", None)
            if loaded is not None:
                loaded.connect(self.reload)
            self._hooked = True
        self.reload()

    def reload(self) -> None:
        """重新取一次图（store.loaded 到了、或数据改了）。"""
        store, family, key = self._store, self._family, self._key
        if store is None or not family or not key:
            self.clear()
            return
        pixmap = store.pixmap(family, key)
        if pixmap is None or pixmap.isNull():
            self.clear()
            return
        size = QSize(self._width or self._height * 4, self._height)
        # ❗ key 要传到底：option_pictures 逐资产判翻不翻（见 families.family_mirrors）。
        self.setPixmap(prepare_icon(pixmap, family, size, self, key))

    @property
    def family(self) -> str:
        return self._family

    @property
    def key(self) -> str:
        return self._key

    def has_icon(self) -> bool:
        pixmap = self.pixmap()
        return pixmap is not None and not pixmap.isNull()


class ElidedLabel(QLabel):
    """装不下就画省略号的 QLabel —— **行里的灰字（描述）专用**。

    ❗ 为什么非它不可（用户 2026-10-03 在 LAV-25 那屏报的「**这右边的东西不见了**」，
    又补一句「**被挤出边界了吧**」—— 两句都对）：
    QLabel 的 `minimumSizeHint` 跟着**整段文字**长 —— 行里那段描述实测最小宽 **637**
    （LAV25A1 那条："换成这套装甲 Armor LAV25A1 · 换成主传感器 … · 名字后缀 +A1"），
    而中栏在 1700 宽的窗口里只有 **640**；于是它把行尾的**值 / Id 徽章 / ＋⧉✕** 一起
    顶出行外（实测工具右缘 664 > 行宽 640），Qt 只会把它们**裁掉**，不会自己省略。
    先例与逐字理由见 `bench_rail._ElidedValueButton`（右栏"值"那一格是同一类毛病）。

    `text()` 仍返回**全文**（屏幕上那句是省略版）—— 既有用例与状态栏读的都是全文。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text or ""
        self.setMinimumWidth(0)
        #: 可以让到 0 —— 布局把剩下的空间按 stretch 给它，装不下才省略。
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 的驼峰
        self._full = text or ""
        self._apply_elide()

    def text(self) -> str:
        """**全文**（不是屏幕上那句省略版）。"""
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 的驼峰
        """最小宽压到 0 —— 不压的话它又会把行尾顶出去（那正是这条 bug）。"""
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        room = max(0, self.width() - 2)
        shown = (
            self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, room)
            if room
            else ""
        )
        super().setText(shown)
        # ❌ **中栏不挂注释浮窗**（用户 2026-10-03：「我叫你不要在中间显示悬浮窗注释 /
        #    我要你在右边显示悬浮窗注释」）—— 浮窗会盖住下面几行。被省略的全文去右栏看。


class BenchAction(QPushButton):
    """编成台上的动作按钮（＋ 加一项 / ✕ / 复制…）。primary=True 是主动作。"""

    def __init__(
        self,
        text: str,
        *,
        primary: bool = False,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "bench-action")
        self.setProperty("primary", "true" if primary else "false")
        self.setCursor(Qt.PointingHandCursor)
        # ❌ 中栏不许挂 tooltip（用户 2026-10-03：「我叫你不要在中间显示悬浮窗注释 /
        #    我要你在右边显示悬浮窗注释」）—— 注释一律由右栏 bench_rail 承担。
        #    ⚠️ tooltip 这个形参**留着**（一堆调用点还在传），但**故意不用** ——
        #    别把它接回 setToolTip()，那正是这一轮撤销的东西。
        del tooltip
        height = TOKENS.size.control_height - 6
        self.setFixedHeight(height)


class _SectionHead(QWidget):
    """抬头的**可点空白**：点一下 = 展开 / 收起。

    ❗ 只认"点在我自己身上"那一下（`rect().contains`）：抬头右边那几颗动作按钮
    （＋ / ⧉ / 🔁）自己吃掉点击，不会顺手把整块收起来。
    """

    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class BenchSection(QFrame):
    """中栏的一块：抬头（▸/▾ ｜ ① 标题 · 灰字说明 · 右侧动作）＋ 主体（竖着堆行/格子）。

    **可折叠、默认收起**（用户 2026-10-02：「把所有能做成折叠栏的都做成折叠栏」＋
    「折叠栏默认是折叠状态」）：

    - 点抬头空白 = 展开/收起（`set_collapsed` / `toggle_collapsed` / `is_collapsed`）；
    - 抬头右边的 ＋ / ⧉ 这些按钮**照旧能点**，折叠不会把它们吞掉；
    - 折的是**主体**：收起只是 `hide()`，控件一个都不销毁 —— 展开回来原样，
      不重建、不闪（AGENTS §0.7）。折叠态**只有一条**：没有第二份"折叠版布局"。
    """

    #: 展开/收起之后发一次（参数 = 现在是不是收起）—— 宿主还要跟着做别的时接它。
    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        hint: str = "",
        parent: QWidget | None = None,
        *,
        collapsible: bool = True,
        collapsed: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-section")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = _SectionHead()
        head.setProperty("role", "bench-section-head")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.xs, TOKENS.spacing.sm, TOKENS.spacing.xs
        )
        head_row.setSpacing(TOKENS.spacing.sm)
        # ❗ 折叠记号**单占一格**，不并进标题文字：标题是"渲染后的完整抬头"，
        #    用例直接做相等断言（先例 test_bench_loadout 拿 TITLE 比 set_title 那句）
        #    —— 把 ▸ 塞进标题会当场把那些断言弄红。
        self._caret = QLabel()
        self._caret.setProperty("role", "bench-section-caret")
        self._caret.setAlignment(Qt.AlignCenter)
        # ❗ 记号那一格是**点得到的一格**（见 CARET_WIDTH）：24 宽靠左对齐会贴边，
        #    所以居中；宽度固定，两个字形宽窄不同也不会让标题左右抖。
        self._caret.setFixedSize(CARET_WIDTH, CARET_HEIGHT)
        self._title = QLabel(title)
        self._title.setProperty("role", "bench-section-title")
        self._hint = QLabel(hint)
        self._hint.setProperty("role", "bench-section-hint")
        self._actions = QHBoxLayout()
        self._actions.setSpacing(TOKENS.spacing.xs)
        head_row.addWidget(self._caret, 0, Qt.AlignVCenter)
        head_row.addWidget(self._title, 0, Qt.AlignVCenter)
        head_row.addWidget(self._hint, 1, Qt.AlignVCenter)
        head_row.addLayout(self._actions)
        root.addWidget(head)

        self._body = QWidget()
        self._body.setProperty("role", "bench-body")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        root.addWidget(self._body, 1)

        self._head = head
        self._collapsible = bool(collapsible)
        self._collapsed = False
        if self._collapsible:
            head.setCursor(Qt.PointingHandCursor)
            head.clicked.connect(self.toggle_collapsed)
        self._caret.setVisible(self._collapsible)
        self.set_collapsed(bool(collapsed) if self._collapsible else False)

    # ---- 折叠 ---------------------------------------------------------- #

    def collapsible(self) -> bool:
        """这一块能不能折（False = 抬头不带记号、点了也不动）。"""
        return self._collapsible

    def is_collapsed(self) -> bool:
        """现在是不是收起的（**默认 True** —— 用户口径：折叠栏默认折叠状态）。"""
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> bool:
        """收起 / 展开（返回**最后**是不是收起）。收起 = 主体 hide，控件一个都不销毁。"""
        collapsed = bool(collapsed) and self._collapsible
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        if self._collapsible:
            self._caret.setText("▸" if collapsed else "▾")
        # ❌ 中栏不许挂 tooltip（用户 2026-10-03：「我叫你不要在中间显示悬浮窗注释 /
        #    我要你在右边显示悬浮窗注释」）—— 注释一律由右栏 bench_rail 承担。
        self.setProperty("collapsed", "true" if collapsed else "false")
        restyle(self)
        return self._collapsed

    def toggle_collapsed(self) -> bool:
        """点抬头那一下走它（发 `toggled`）。返回新的折叠态。"""
        state = self.set_collapsed(not self._collapsed)
        self.toggled.emit(state)
        return state

    def caret_label(self) -> QLabel:
        """抬头那颗 ▸/▾（用例按它读折叠态 —— 不去数字符）。"""
        return self._caret

    def head(self) -> QWidget:
        """抬头那一整条（点它 = 折叠；用例拿它 QTest.mouseClick）。"""
        return self._head

    # ---- 抬头 ---------------------------------------------------------- #

    def add_action(self, text: str, *, primary: bool = False, tooltip: str = "") -> BenchAction:
        """抬头右侧加一颗按钮（＋ 加一项 / ⧉ 复制…）。返回控件给调用方连信号。"""
        button = BenchAction(text, primary=primary, tooltip=tooltip)
        self._actions.addWidget(button)
        return button

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)

    def title_text(self) -> str:
        """抬头那行字（读数口：装配方按"摆第几块"写编号之前先读它、去掉旧编号）。

        ❗ 与 caret_label() 同一条口径：读**控件里那一份**，不现算 ——
        装配方写完编号之后，用例也从这里读"编号与位置一致"，两边同一份状态。
        """
        return self._title.text()

    def hint_text(self) -> str:
        """抬头右边那句灰字说明（读数口，同上）。"""
        return self._hint.text()

    # ---- 主体 ---------------------------------------------------------- #

    def body(self) -> QWidget:
        """主体容器（要自己排布局时用它；一般用 add_widget）。"""
        return self._body

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def add_stretch(self) -> None:
        self._body_layout.addStretch(1)

    def body_widgets(self) -> tuple[QWidget, ...]:
        return tuple(
            self._body_layout.itemAt(i).widget()
            for i in range(self._body_layout.count())
            if self._body_layout.itemAt(i).widget() is not None
        )

    def clear_body(self) -> None:
        """清空主体：**必须先 hide 再 setParent(None)**，否则会闪一个顶层小窗。"""
        for widget in self.body_widgets():
            detach(widget)


def id_badge_text(ref_id: int) -> str:
    """「这一行指向的那一行」的 Id 写成什么样（`0` = 什么都不写）。

    口径只有一份（用户 2026-10-01）：「所有出现诸如单位、武器、弹药、炮塔 ID 的时候，
    都在右边显示其 ID」。`0` 在外键里是"不使用"，主键那一行也不补（值本身就是 Id）。

    ❗ 返回**空串**而不是"0"：空 QLabel 不占宽，没有 Id 的行还是原来那个样子。
    """
    if not isinstance(ref_id, int) or isinstance(ref_id, bool):
        return ""
    return f"#{ref_id}" if ref_id > 0 else ""


class BenchRow(QFrame):
    """一行：图标 │ 名字 │ 说明 │ 值 │ #Id │ ✕。

    两个信号都只是"意图"：谁接谁决定写什么。

    ❗ 2026-10-02：行尾那颗 ↗（「去看这一行」= 跳数据表页）**整个删掉了**
    —— 用户圈着它说「我不要转到行的这个功能，删掉」。中栏点一行就已经是"看它的全部列"；
    要去某一列指向的行，走**右栏那一格外键**右边那颗小 ↗（条目角色 `entry-link`，那条留着）。
    """

    #: 点了这一行（左上角除 ✕ 之外的任何地方）。
    activated = Signal()
    #: 点了行尾 ✕（删这一行）。
    remove_requested = Signal()

    def __init__(
        self,
        *,
        name: str = "",
        detail: str = "",
        value: str = "",
        id_text: str = "",
        icon_height: int = 20,
        removable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-row")
        self.setProperty("selected", "false")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(
            TOKENS.spacing.sm, TOKENS.spacing.xs, TOKENS.spacing.sm, TOKENS.spacing.xs
        )
        row.setSpacing(TOKENS.spacing.sm)

        self._icon = IconLabel(icon_height)
        self._name = QLabel(name)
        self._name.setProperty("role", "bench-row-name")
        self._name.setMinimumWidth(120)
        # ❗ 灰字用 ElidedLabel（可压缩 + 自己省略）：普通 QLabel 的最小宽跟着整段文字长，
        #    长描述会把行尾的值 / Id / ＋⧉✕ 顶出行外 —— 见 ElidedLabel 的注释。
        self._detail = ElidedLabel(detail)
        self._detail.setProperty("role", "bench-row-detail")
        self._value = QLabel(value)
        self._value.setProperty("role", "bench-row-value")
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # ❗ 值**右边**那一小段：这一行指向的那一行的 Id（写什么见 id_badge_text）。
        #    与右栏 / 中栏 / 关系树**同一套 role**（entry-id 小徽章），不另发明样式。
        #    默认空串 —— 没传 id 的宿主一个像素都不变。
        self._id = QLabel(id_text)
        self._id.setProperty("role", "entry-id")
        # ❗ 空 Id **直接藏起来**：entry-id 那个 role 自带底框，空着也画一个**小方块**
        #    （用户 2026-10-03 指着分组抬头右端问「那个小方块是什么？没用就给它去掉」——
        #    分组抬头本来就没有 Id，id_badge_text(0) 给的就是空串）。
        #    ⚠️ 可见性**等加进布局之后再定**（见下面那一句）：此刻它还没有父控件，
        #    setVisible(True) 会被 Qt 映射成一个真顶层小窗（StrayWindowGuard 抓的就是它）。

        row.addWidget(self._icon, 0, Qt.AlignVCenter)
        row.addWidget(self._name, 0, Qt.AlignVCenter)
        row.addWidget(self._detail, 1, Qt.AlignVCenter)
        row.addWidget(self._value, 0, Qt.AlignVCenter)
        row.addWidget(self._id, 0, Qt.AlignVCenter)
        #: 加进布局（= 有父控件）之后才敢定可见性 —— 空 Id 不留那个小方块。
        self._id.setVisible(bool(id_text))

        #: 宿主自己加的行尾工具（🔁 换 / ＋ 加 / ↑ / ↓ …）—— `add_tool()` 往里塞。
        #: 次序：宿主加的那些**都在 ✕ 左边**（后加的插在 ✕ 前面）。
        self._tools: list[BenchAction] = []

        self._remove = BenchAction("✕", tooltip="删掉这一行")
        self._remove.setProperty("role", "bench-row-tool")
        self._remove.clicked.connect(self.remove_requested)
        row.addWidget(self._remove, 0, Qt.AlignVCenter)

        self.set_removable(removable)

        #: 细行（一行一行那种），高度钉死，别让 QLabel 撑开。
        self.setFixedHeight(TOKENS.size.row_height + 2)

    # ---- 内容 ---------------------------------------------------------- #

    def set_icon(self, store, family: str, key: str) -> None:
        self._icon.set_source(store, family, key)

    def set_name(self, text: str) -> None:
        self._name.setText(text)

    def set_detail(self, text: str) -> None:
        self._detail.setText(text)

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def set_id_text(self, text: str) -> None:
        """就地换那一小段 Id。

        ❗ 走"就地刷新"那条路时**必须**跟着新数据一起写（与右栏 `_update_values` 同一个坑）：
        只刷值不刷 Id 的话，行上写着新枪、右边还挂着旧 Id。
        """
        self._id.setText(text)

    def id_label(self) -> QLabel:
        return self._id

    def set_removable(self, removable: bool) -> None:
        self._remove.setVisible(bool(removable))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        restyle(self)

    @property
    def selected(self) -> bool:
        return self.property("selected") == "true"

    # ---- 只点行本身（不含 ✕） ------------------------------------------ #

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def name_label(self) -> QLabel:
        return self._name

    def detail_label(self) -> QLabel:
        return self._detail

    def value_label(self) -> QLabel:
        return self._value

    def icon_label(self) -> IconLabel:
        return self._icon

    def remove_button(self) -> BenchAction:
        return self._remove

    def add_tool(self, text: str, *, tooltip: str = "", primary: bool = False) -> BenchAction:
        """行尾**再加一颗**工具按钮（🔁 换一座 / ＋ 加一条 / ↑ / ↓ …）—— 排在 ✕ 左边。

        ❗ 位置钉在 ✕ 左边（`indexOf(self._remove)`）：后加的那些不会跑到行中间去，
        也不会盖住既有的那颗 ✕。宿主自己连信号 —— 这一层只摆控件。
        """
        button = BenchAction(text, primary=primary, tooltip=tooltip)
        button.setProperty("role", "bench-row-tool")
        layout = self.layout()
        assert isinstance(layout, QHBoxLayout)  # bench_kit 里那一行就是 QHBoxLayout
        layout.insertWidget(layout.indexOf(self._remove), button, 0, Qt.AlignVCenter)
        self._tools.append(button)
        return button

    def tools(self) -> tuple[BenchAction, ...]:
        """宿主自己加的行尾工具（**不含** ✕）—— 用例按它读"这一行有没有 🔁"。"""
        return tuple(self._tools)


class SlotCard(QFrame):
    """一格：左上序号 + 右上 ✕；中间若干"图标 + 文字"行；底部一行小字。

    步兵编成台（一格 = 一个兵）与飞机挂载图（一格 = 一个挂载点）共用它。
    """

    #: 点了这一格（选中它）。
    activated = Signal()
    #: 点了右上角 ✕。
    remove_requested = Signal()

    def __init__(
        self,
        index_text: str = "",
        *,
        id_text: str = "",
        width: int = 152,
        height: int = 104,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-slot")
        self.setProperty("selected", "false")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(width, height)
        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.sm, TOKENS.spacing.xxs, TOKENS.spacing.xs, TOKENS.spacing.xs
        )
        root.setSpacing(TOKENS.spacing.xxs)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        self._index = QLabel(index_text)
        self._index.setProperty("role", "bench-slot-index")
        # ❗ 这一格**指向的那一行**的 Id（兵格 = SquadMembers 那一行）；口径见 id_badge_text。
        #    摆在右上、✕ 的**左边** —— 用户口径是「在右边显示其 ID」。
        self._id = QLabel(id_text)
        self._id.setProperty("role", "entry-id")
        self._remove = BenchAction("✕", tooltip="删掉这一格")
        self._remove.setProperty("role", "bench-row-tool")
        self._remove.setFixedHeight(14)
        self._remove.clicked.connect(self.remove_requested)
        top.addWidget(self._index, 0, Qt.AlignVCenter)
        top.addStretch(1)
        top.addWidget(self._id, 0, Qt.AlignVCenter)
        top.addWidget(self._remove, 0, Qt.AlignVCenter)
        root.addLayout(top)

        self._lines: list[tuple[IconLabel, QLabel]] = []
        self._lines_box = QVBoxLayout()
        self._lines_box.setContentsMargins(0, 0, 0, 0)
        self._lines_box.setSpacing(1)
        root.addLayout(self._lines_box)
        root.addStretch(1)

        self._foot = QLabel("")
        self._foot.setProperty("role", "bench-slot-foot")
        root.addWidget(self._foot)

    # ---- 内容 ---------------------------------------------------------- #

    def add_line(self, text: str = "", *, icon_height: int = 15) -> tuple[IconLabel, QLabel]:
        """加一行「图标 + 文字」，返回 (图标位, 标签) 给调用方填。"""
        holder = QWidget()
        holder.setProperty("role", "bench-slot-line")
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(TOKENS.spacing.xs)
        icon = IconLabel(icon_height)
        label = QLabel(text)
        label.setProperty("role", "bench-slot-line")
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        line.addWidget(icon, 0, Qt.AlignVCenter)
        line.addWidget(label, 1, Qt.AlignVCenter)
        self._lines_box.addWidget(holder)
        self._lines.append((icon, label))
        return icon, label

    def set_line(self, index: int, text: str) -> None:
        if 0 <= index < len(self._lines):
            self._lines[index][1].setText(text)

    def set_line_icon(self, index: int, store, family: str, key: str) -> None:
        if 0 <= index < len(self._lines):
            self._lines[index][0].set_source(store, family, key)

    def set_footnote(self, text: str) -> None:
        self._foot.setText(text)

    def set_index(self, text: str) -> None:
        self._index.setText(text)

    def set_id_text(self, text: str) -> None:
        """就地换右上那一小段 Id（"这一格是谁"）—— 与 BenchRow.set_id_text 同一条。"""
        self._id.setText(text)

    def id_label(self) -> QLabel:
        return self._id

    def set_removable(self, removable: bool) -> None:
        self._remove.setVisible(bool(removable))

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        restyle(self)

    @property
    def selected(self) -> bool:
        return self.property("selected") == "true"

    def lines(self) -> tuple[tuple[IconLabel, QLabel], ...]:
        return tuple(self._lines)

    def footnote_label(self) -> QLabel:
        return self._foot

    def index_label(self) -> QLabel:
        return self._index

    def remove_button(self) -> BenchAction:
        return self._remove

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(event)


class BenchEmpty(QLabel):
    """ "这里还没有东西"的一句灰字（别用空白，用户看不出是空的还是没画出来）。"""

    def __init__(self, text: str = "（还没有）", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "bench-empty")
        self.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.sm, TOKENS.spacing.md, TOKENS.spacing.sm
        )


class BenchGrid(QWidget):
    """格子矩阵：FlowLayout 流式换行（步兵 14 格 / 飞机 21 格都用它）。

    ⚠️ FlowLayout 的 hasHeightForWidth=True，**必须**放在 QScrollArea 里才撑得开。
    """

    def __init__(self, *, margin: int = 0, spacing: int = 7, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-grid")
        self._flow = FlowLayout(self, margin=margin, h_spacing=spacing, v_spacing=spacing)

    def add_cell(self, cell: QWidget) -> None:
        self._flow.addWidget(cell)

    def clear(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                detach(widget)

    def cells(self) -> tuple[QWidget, ...]:
        out = []
        for i in range(self._flow.count()):
            item = self._flow.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                out.append(widget)
        return tuple(out)
