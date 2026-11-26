"""编成台 ②「班用武器与弹药」—— 中栏可操作区的那一块（一件武器一行，弹挂在它下面）。

口径（`docs/交接-2026-10-01-单位编成台设计.md` §3 口径 1/2 ＋ 设计图 ② 那一块）：

- 抬头「② 班用武器与弹药」＋ 灰字说明 ＋「＋ 加一件武器」；
- **武器行** = 一行 `SquadWeapons`（关联行）：图标 ＋ 名字；行尾「＋」（给**这门枪**加一种弹）、
  「✕」（删这一行关联行 —— **Weapons 模板行一行不动**）；
- **弹行** = 一行 `WeaponAmmunitions`：缩进一档，弹种图标 ＋ 弹种名 ＋「×数量」；
  「×数量」点开就地改（InlineEditor，没有确认按钮），行尾「✕」删这一行；
- **只有 ＋ 和 ✕，没有"换"**（用户口径 1）；一门枪一行弹都没有时写
  `MISSING_AMMO_TEXT`（「缺弹 · 空枪」），**别静默留白**。

三条硬纪律（都有测试守着）：

1. 这一层**一个字节都不写库** —— 写库一律经 `EditorContext`
   （set_value / insert_row / delete_row / add_block_row）；控件只发意图信号；
2. **不弹窗**：成功 / 没有改动 / 解释都走 `status_message` 一句话（错误才由上层弹）；
3. 基础件是共享的 `widgets/bench_kit.py`：BenchSection / BenchRow /
   BenchAction / BenchEmpty / IconLabel / `id_badge_text`（**共享件，不是谁的私产**：
   `BenchRow.id_text` 那一格就是这一层在 2026-10-01 提的需求）。图标取图只经 `IconLabel.set_source`
   （内部走 `prepare_icon`，负责族翻转 + DPR）；store 是**外面传进来的那一份**
   （`set_icons`），这一层不 new IconCache。

装配接口（Lead 按它接）：

    class LoadoutBench(QWidget):
        row_selected = Signal(str, int)      # (表名, 行Id)
        status_message = Signal(str)         # 一句人话，绝不弹窗
        def set_context(self, context) -> None   # EditorContext | None
        def refresh(self) -> None                # 数据变了重画（幂等）
        def reload_values(self) -> None          # 只是**值**变了：就地换字，一个控件都不重建
        def set_icons(self, store) -> None
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.bench_loadout import (
    AMMO_ICON_SOURCE,
    COLUMN_AMMO,
    COLUMN_WEAPON,
    MISSING_AMMO_TEXT,
    TABLE_AMMO,
    TABLE_SQUAD_WEAPONS,
    TABLE_WEAPON_AMMO,
    TABLE_WEAPONS,
    WEAPON_ICON_SOURCE,
    AmmoLine,
    WeaponLine,
    ammo_is_known,
    loadout_lines,
    member_carriers,
    owned_ammo_ids,
    quantity_ref,
    registered_weapon_ids,
    structure_key,
)
from ba_mod_maker.app.widgets.bench_kit import (
    BenchAction,
    BenchEmpty,
    BenchRow,
    BenchSection,
    id_badge_text,
)
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.edit.clone import next_row_id
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.refs import CellRef

#: 抬头的两行字 —— 设计图 ② 那一块原话（用例按它断言，别在别处再抄一遍）。
TITLE = "② 班用武器与弹药"
HINT = "一件武器一行，弹挂在它下面（WeaponAmmunitions 按「班 × 武器」）"
ADD_WEAPON_TEXT = "＋ 加一件武器"
#: 武器行尾那颗「＋」：**给这门枪**加一种弹（设计图里是光一颗 ＋，含义写在 tooltip 里）。
ADD_AMMO_TEXT = "＋"
NO_WEAPONS_TEXT = "这个班还没注册任何班用武器 —— 点右上「＋ 加一件武器」"
NO_UNIT_TEXT = "（还没有打开单位）"
#: 「缺弹 · 空枪」占位行右边那句灰字（**值**这一路也要写它 —— 占位行文字算显示字段）。
MISSING_AMMO_DETAIL = "这门枪还一行弹都没有 —— 点上面那一行的「＋」加一种弹"

#: 弹行缩进一档：弹挂在武器**下面**，缩进才看得出从属（设计图里弹种名比武器名右移一档）。
AMMO_INDENT = TOKENS.spacing.lg
#: 图标位高度：武器行 20 / 弹行 16（与设计图的层级一致）。
WEAPON_ICON_HEIGHT = 20
AMMO_ICON_HEIGHT = 16

#: 整块自己的三种**处境** —— 也要进结构署名：三种处境画的是三句不同的话
#: （「还没有打开单位」/「这个班还没注册任何班用武器」/ 一行行真数据）。
#: ❗ 光比"行署名"的话，空结构 == 空结构 —— 从"没打开单位"切到"这个班没武器"时
#:    就会把上一句留着不换（实测被用例当场抓住：单位 206 那一块写着"还没有打开单位"）。
STATE_NO_UNIT = "·没有单位"
STATE_NO_ROWS = "·这个班没有班用武器"
STATE_ROWS = "·有行"


def block_key(context, lines: tuple[WeaponLine, ...]) -> tuple:
    """整块的**结构**署名：处境 ＋ 每一行是哪张表的哪一行（见 viewmodel.structure_key）。

    值变了（署名没变）→ `LoadoutBench.reload_values()` 就地换字；
    署名变了 → 整块重建。用户口径（提过几回）：「我修改的时候你能不能不要全量保存一下，
    不然界面闪一下很影响观感」。
    """
    if context is None:
        return (STATE_NO_UNIT,)
    if not lines:
        return (STATE_NO_ROWS,)
    return (STATE_ROWS, *structure_key(lines))


class QuantityEditor(InlineEditor):
    """携弹量那一格的就地编辑 —— 值转不动时**额外**说一句给状态栏。

    InlineEditor 自己会把错误画在浮层里（`inline-error`），但那是"就地"的：用户一点走
    就没了。编成台这一层的口径是「非法输入不写、状态栏说一句」（见模块头）——
    所以在它**唯一**的出错漏斗 `_show_error` 上再补一次 emit
    （`inline_editor.py` 里所有"值转不动"都只走这一个函数，没有第二条路）。
    """

    #: 值没转成（参数：为什么）。**值没有写进去** —— 外层接到就写一句状态栏。
    rejected = Signal(str)

    def _show_error(self, message: str) -> None:
        super()._show_error(message)
        self.rejected.emit(str(message))


class _IconRow(BenchRow):
    """带图标位的行（武器行 / 弹行共用的一半）：把「族 + 键」记在控件上，换 store 时能重取。"""

    #: 这一行落在哪张表（信号与用例都用它）—— 子类写死。
    table: str = ""

    def __init__(
        self,
        *,
        store,
        family: str,
        key: str,
        icon_height: int,
        indent: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(icon_height=icon_height, **kwargs)
        self._store = store
        self._family = family
        self._key = key
        self._icon_height = icon_height
        self._indent = 0
        # ❗ 图标位**钉成同一档宽度**（= IconLabel 自己那条 `height * 4` 的取图宽度）：
        #    不钉的话名字会跟着图标长宽比左右跳（弹药族 174×36 取满 64、步枪族 231×82 只有 45），
        #    而设计图里每一列的起点是一条竖线。取 height*4 是**不会裁到图**的那一档。
        self.icon_label().setFixedWidth(icon_height * 4)
        self.set_indent(indent)
        self.reload_icon()

    def reload_icon(self) -> None:
        """按记住的族 + 键重取一次图（store.loaded 到了由 IconLabel 自己补）。"""
        self.set_icon(self._store, self._family, self._key)

    def apply_icon_key(self, key: str) -> None:
        """就地换图标键（**值**变了那一路 —— 不重建控件，见 LoadoutBench.reload_values）。"""
        self._key = str(key or "")
        self.reload_icon()

    def set_icons(self, store) -> None:
        """换上外面那份图标缓存（`LoadoutBench.set_icons` 往下发）。"""
        self._store = store
        self.reload_icon()

    def set_indent(self, width: int) -> None:
        """左边多留一档（弹行挂在武器行下面，缩进才看得出从属）。"""
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)  # bench_kit 里那一行是 QHBoxLayout
        margins = layout.contentsMargins()
        left = margins.left() + int(width)
        layout.setContentsMargins(left, margins.top(), margins.right(), margins.bottom())
        self._indent = left

    @property
    def indent(self) -> int:
        """左边距（px）—— 用例按它断言"弹行确实缩进了一档"。"""
        return self._indent


class WeaponRow(_IconRow):
    """一件武器一行（一行 `SquadWeapons`）：图标 ＋ 名字，行尾「＋ / ✕」。"""

    table = TABLE_SQUAD_WEAPONS

    def __init__(self, line: WeaponLine, store=None, parent: QWidget | None = None) -> None:
        super().__init__(
            store=store,
            family=WEAPON_ICON_SOURCE[0],
            key=line.icon_key,
            icon_height=WEAPON_ICON_HEIGHT,
            name=line.label,
            # 这一行说的是哪一门枪 -> 值右边给 Weapons 的 Id（口径见 bench_kit.id_badge_text）。
            id_text=id_badge_text(line.weapon_id),
            removable=True,
            parent=parent,
        )
        self.line = line
        self.row_id = line.squad_weapon_id
        self.weapon_id = line.weapon_id
        #: 挂在它下面的弹行（那一组建完由 LoadoutBench 填上）—— 控件里那份状态，用例读它。
        self.ammo_rows: tuple[AmmoRow, ...] = ()
        self._add = BenchAction(
            ADD_AMMO_TEXT, tooltip=f"给「{line.label}」加一种弹（就地冒候选列表，不弹对话框）"
        )
        # ❗ 用「行尾小按钮」那一档（与 ✕ 同一套），**不要** bench-action 那个带边框的样式：
        #    QSS 给这两档按钮算出来的最小高度是 30 / 28，而行的固定高度只有 28（内容区 20）——
        #    带边框的 ＋ 会明显长出这一行、压到下面那条弹行上（真机截图量到的）。
        #    行尾那两颗按钮要长得一样，这条越界是 bench_kit / qss 那一层的事（已报 Lead）。
        self._add.setProperty("role", "bench-row-tool")
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        layout.insertWidget(layout.indexOf(self.remove_button()), self._add, 0, Qt.AlignVCenter)

    def apply_line(self, line: WeaponLine) -> None:
        """就地换值：名字 / 图标 / 那颗「＋」的提示 / 悬停提示。**一个控件都不重建**。

        （用户口径，提过几回：「我修改的时候你能不能不要全量保存一下，界面闪一下很影响观感」
          —— 先例 docs/交接-2026-09-30-界面批-图标树弹药.md:76/93。）
        """
        self.line = line
        self.set_name(line.label)
        # ❗ Id 也要跟着走：换了枪名不换 Id，右边就挂着上一门的号（"控件保住了、值还是旧的"）。
        self.set_id_text(id_badge_text(line.weapon_id))
        self.apply_icon_key(line.icon_key)

    def add_button(self) -> BenchAction:
        """行尾那颗「＋」= 给**这门枪**加一种弹。"""
        return self._add


class AmmoRow(_IconRow):
    """一条携弹行（一行 `WeaponAmmunitions`）：缩进一档，行尾「×数量」＋「✕」。"""

    table = TABLE_WEAPON_AMMO

    #: 点了「×数量」那一格（开就地编辑）—— 这一下**不算**"选中这一行"。
    quantity_requested = Signal()

    def __init__(self, line: AmmoLine, store=None, parent: QWidget | None = None) -> None:
        super().__init__(
            store=store,
            family=AMMO_ICON_SOURCE[0],
            key=line.icon_key,
            icon_height=AMMO_ICON_HEIGHT,
            indent=AMMO_INDENT,
            name=line.label,
            value=f"×{line.quantity}",
            # 这一行说的是哪一种弹 -> 值右边给 Ammunitions 的 Id（不是携弹行自己的 Id）。
            id_text=id_badge_text(line.ammo_id),
            removable=True,
            parent=parent,
        )
        self.line = line
        self.row_id = line.row_id
        self.ammo_id = line.ammo_id
        self._editor: QWidget | None = None
        self.value_label().setCursor(Qt.PointingHandCursor)
        self.value_label().installEventFilter(self)

    def apply_line(self, ammo: AmmoLine) -> None:
        """就地换值：弹种名 / 图标 / 「×数量」/ 悬停提示。**一个控件都不重建**。

        ❗ 值那一格可能正被就地编辑浮层占着（它这时是藏起来的）—— 照旧写进去：
        收工之后看到的就是新值，而浮层一个字节都没被碰。
        """
        self.line = ammo
        self.ammo_id = ammo.ammo_id
        self.set_name(ammo.label)
        self.set_value(f"×{ammo.quantity}")
        self.set_id_text(id_badge_text(ammo.ammo_id))
        self.apply_icon_key(ammo.icon_key)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 的驼峰
        """点「×数量」= 开就地编辑。

        ❗ 这一下要**吃掉**：QLabel 不接受鼠标事件，不吃的话它会冒到行上 ——
        于是"点数量"顺带把这一行选中了，用户看到的是两件事一起发生。
        """
        if (
            obj is self.value_label()
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.LeftButton
        ):
            self.quantity_requested.emit()
            return True
        return super().eventFilter(obj, event)

    # ---- 就地编辑那一格（与 ammo_block.AmmoLine.attach_editor 同一套口径） ---- #

    def attach_editor(self, editor: QWidget) -> None:
        """把浮层放进「×数量」那一格（行高不变，值那一格先藏起来）。"""
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        self.close_editor()
        self._editor = editor
        editor.setFixedHeight(TOKENS.size.row_height)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.value_label().setVisible(False)
        layout.insertWidget(layout.indexOf(self.value_label()), editor, 1)
        editor.show()
        focus_input = getattr(editor, "focus_input", None)
        if callable(focus_input):
            focus_input()
        else:
            editor.setFocus()

    def close_editor(self) -> None:
        if self._editor is not None:
            detach(self._editor)
            self._editor = None
        self.value_label().setVisible(True)

    @property
    def has_editor(self) -> bool:
        return self._editor is not None

    @property
    def editor(self) -> QWidget | None:
        return self._editor


class MissingAmmoRow(_IconRow):
    """「这门枪一行弹都没有」的占位行 —— 设计图 ② 就写着「缺弹 · 空枪」，别留白。

    它**不是**库里的一行（`row_id = 0`）：不删、不跳、点了也不选中 —— 只把"这儿是空的"
    画出来，并说清 ＋ 在哪。
    """

    table = ""

    def __init__(self, squad_weapon_id: int = 0, store=None, parent: QWidget | None = None) -> None:
        super().__init__(
            store=store,
            family="",
            key="",
            icon_height=AMMO_ICON_HEIGHT,
            indent=AMMO_INDENT,
            name=MISSING_AMMO_TEXT,
            detail=MISSING_AMMO_DETAIL,
            removable=False,
            parent=parent,
        )
        self.row_id = 0
        #: 它挂在**哪一门枪**下面（就地在值那一路配对靠它；用例也读它）。
        self.squad_weapon_id = int(squad_weapon_id)
        self.setCursor(Qt.ArrowCursor)
        self.setAttribute(Qt.WA_Hover, False)

    def apply_line(self, line: WeaponLine) -> None:
        """就地换字（现在是两个常量，但"占位行文字"也在值这一路要覆盖的字段里）。"""
        self.squad_weapon_id = line.squad_weapon_id
        self.set_name(MISSING_AMMO_TEXT)
        self.set_detail(MISSING_AMMO_DETAIL)


class LoadoutBench(QWidget):
    """编成台 ②：班用武器与弹药（一件武器一行，弹挂在它下面）。

    两个信号都只是**意图**：谁接谁决定写什么。
    """

    #: 点了某一行（参数：表名, 行Id）—— 右栏照这个画。
    row_selected = Signal(str, int)
    #: 一句人话（成功 / 没有改动 / 解释）—— **绝不弹窗**。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = None
        self._store = None
        self._lines: tuple[WeaponLine, ...] = ()
        self._body_widgets: list[QWidget] = []
        self._rows: list[BenchRow] = []
        self._groups: list[tuple[WeaponRow, tuple[AmmoRow, ...]]] = []
        self._selected: tuple[str, int] = ("", 0)
        #: **建的时候**记下来的那一份结构署名（处境 + 行署名）——"现在画着什么"。
        #: ❗ 不许拿"现在的 context + self._lines"现算：set_context 是先换 context 再 refresh 的，
        #:    现算会把"还没打开单位"那块灰字也算成"这个班没有武器"，于是那一句留着不换
        #:    （实测被用例抓住：单位 206 那一块写着"还没有打开单位"）。
        self._drawn_key: tuple = ()
        self._status = ""
        self._picker: RowPicker | None = None
        self._editor_row: AmmoRow | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._section = BenchSection(TITLE, HINT, self)
        self._add_weapon = self._section.add_action(
            ADD_WEAPON_TEXT, tooltip="从 Weapons 里挑一件挂到这个班（就地冒候选，不弹对话框）"
        )
        self._add_weapon.clicked.connect(self._open_weapon_picker)
        root.addWidget(self._section)

        #: 「＋」就地冒出来的候选列表的**落脚点**：一个常驻宿主，先加进主体、藏着，
        #: 用的时候再挪到该出现的位置（就地冒候选，不弹对话框）。
        #: ❗ 先 add_widget 再 setVisible —— 控件还没有父控件时被显示，Qt 会真的开一个
        #:    顶层小窗（用户报过的"闪一下的小弹窗"，见 widgets/detach.py 的说明）。
        self._picker_host = QWidget()
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(0, 0, 0, 0)
        self._picker_layout.setSpacing(0)
        self._picker_host.setVisible(False)
        self._section.add_widget(self._picker_host)
        #: 行都贴着抬头堆：竖着撑开时别把几行摊平（stretch 永远在主体最后一项）。
        self._section.add_stretch()
        self.refresh()

    # ------------------------------------------------------------------ #
    # 接线
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上编辑服务（`EditorContext`）；None = 清空。"""
        self._context = context
        if context is None:
            self._selected = ("", 0)
        self.refresh()

    def set_icons(self, store) -> None:
        """接上**外面传进来的**那一份图标缓存（别在这一层 new IconCache）。"""
        self._store = store
        for row in self._rows:
            if isinstance(row, _IconRow):
                row.set_icons(store)

    # ------------------------------------------------------------------ #
    # 画：**值**变了就地换字、**结构**变了才重建（用户口径：别全量重建闪一下）
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """数据变了重画（幂等）。选中态能留就留（那一行还在的话）。

        ❗ **结构没变就只换值**：那一趟走 reload_values()（一个控件都不重建）；
        结构真变了（插 / 删行、某门枪有弹↔没弹）才整块重建 —— 判据见 structure_key。
        """
        context = self._context
        fresh = () if context is None else loadout_lines(context.data, context.unit_id)
        if context is not None and block_key(context, fresh) == self._block_key():
            self._apply_lines(fresh)
            return
        self._close_editor()
        self._close_picker()
        self._clear_body()
        self._lines = fresh
        if context is None:
            self._add_body(BenchEmpty(NO_UNIT_TEXT))
        else:
            for line in fresh:
                self._build_group(line)
            if not fresh:
                self._add_body(BenchEmpty(NO_WEAPONS_TEXT))
        self._drawn_key = block_key(context, fresh)
        self._place_picker(None)
        if self._selected not in {(row.table, row.row_id) for row in self._rows}:
            self._selected = ("", 0)
        self._apply_selection()

    def reload_values(self) -> None:
        """只是**值**变了：就地把现有控件的文字 / 图标换成最新的，**一个控件都不重建**。

        用户口径（提过几回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下
        很影响观感」。先例：docs/交接-2026-09-30-界面批-图标树弹药.md:76/93 ——
        署名变了才整套重建，没变走 reload()。

        三条边界：
        1. 只 setText / 换图标键 —— 不 clear()、不 deleteLater()、不动布局
           （配不上控件的那一项**跳过**：不新建也不销毁）；
        2. **结构真变了**（插 / 删行、某门枪有弹↔没弹）就退回 refresh() 整块重建 ——
           宁可闪一下，也不能少画一行 / 留一行旧的（外层署名没看出来时它是最后一道闸）；
        3. 还没接 context（没打开单位）时什么都不做。
        """
        context = self._context
        if context is None:
            return
        fresh = loadout_lines(context.data, context.unit_id)
        if block_key(context, fresh) != self._block_key():
            self.refresh()
            return
        self._apply_lines(fresh)

    def _block_key(self) -> tuple:
        """现在**画着**的那一份结构署名（建那块的时候记下来的，不是现算的）。"""
        return self._drawn_key

    def _apply_lines(self, fresh: tuple[WeaponLine, ...]) -> None:
        """按 (表, 行Id) 配对，把新值写进**已有**控件；配不上的跳过（见 reload_values）。"""
        weapons = {row.row_id: row for row in self.weapon_rows()}
        ammo = {row.row_id: row for row in self.ammo_rows()}
        missing = {row.squad_weapon_id: row for row in self.missing_ammo_rows()}
        for line in fresh:
            weapon_row = weapons.get(line.squad_weapon_id)
            if weapon_row is not None:
                weapon_row.apply_line(line)
            if not line.ammo:
                missing_row = missing.get(line.squad_weapon_id)
                if missing_row is not None:
                    missing_row.apply_line(line)
                continue
            for item in line.ammo:
                ammo_row = ammo.get(item.row_id)
                if ammo_row is not None:
                    ammo_row.apply_line(item)
        self._lines = fresh

    # ------------------------------------------------------------------ #
    # ⧉ 复制模板另存一份（与 ①/③–⑦ 同一条口径，见交接 §23–§25）
    # ------------------------------------------------------------------ #

    def _clone_template(self, target_table: str, target_id: int, link: CellRef, what: str) -> int:
        """复制 (target_table, target_id) 那一行另存一份，再把 link 指过去。"""
        context = self._context
        if context is None:
            return 0
        if not target_id or not link.row_id:
            self.status_message.emit(f"这一{what}没有落点 —— 复制不了")
            return 0
        new_id, message = context.clone_row_to(target_table, int(target_id), link)
        self.status_message.emit(f"{message} —— 右栏就是新模板，直接在上面改")
        self.refresh()
        self.row_selected.emit(target_table, new_id)
        return new_id

    def clone_weapon(self, line: WeaponLine) -> int:
        """武器行 ⧉：复制 `Weapons` 那一行另存一份，本行（`SquadWeapons`）指过去。"""
        return self._clone_template(
            TABLE_WEAPONS,
            line.weapon_id,
            CellRef(TABLE_SQUAD_WEAPONS, line.squad_weapon_id, COLUMN_WEAPON),
            "门武器",
        )

    def clone_ammo(self, line: AmmoLine) -> int:
        """弹药行 ⧉：复制 `Ammunitions` 那一行另存一份，本行（`WeaponAmmunitions`）指过去。"""
        return self._clone_template(
            TABLE_AMMO,
            line.ammo_id,
            CellRef(TABLE_WEAPON_AMMO, line.row_id, COLUMN_AMMO),
            "条弹种",
        )

    def _build_group(self, line: WeaponLine) -> None:
        """一件武器 ＋ 它下面的弹行（没有弹就画一个「缺弹 · 空枪」的占位行）。"""
        weapon_row = WeaponRow(line, self._store)
        weapon_row.activated.connect(lambda row=weapon_row: self._on_row_clicked(row))
        weapon_row.remove_requested.connect(lambda row=weapon_row: self._remove_weapon(row))
        weapon_row.add_button().clicked.connect(
            lambda _checked=False, row=weapon_row: self._open_ammo_picker(row)
        )
        # ⧉ = 复制这门武器指着的 `Weapons` 模板另存一份（口径同 ①/③–⑦，见交接 §23–§25）。
        clone_weapon = weapon_row.add_tool("⧉", tooltip="复制这门武器另存一份（别人不动）")
        clone_weapon.clicked.connect(lambda _=False, line=line: self.clone_weapon(line))
        self._add_body(weapon_row)
        self._rows.append(weapon_row)

        ammo_rows: list[AmmoRow] = []
        if not line.ammo:
            # ❗ 占位行也要进 _rows：不然 rows() / missing_ammo_rows() 看不见它
            #    （"空枪要看得出来"这条就变成只在像素上成立、用例抓不到）。
            missing_row = MissingAmmoRow(line.squad_weapon_id, self._store)
            self._add_body(missing_row)
            self._rows.append(missing_row)
        for ammo in line.ammo:
            ammo_row = AmmoRow(ammo, self._store)
            ammo_row.activated.connect(lambda row=ammo_row: self._on_row_clicked(row))
            ammo_row.remove_requested.connect(lambda row=ammo_row: self._remove_ammo(row))
            ammo_row.quantity_requested.connect(
                lambda row=ammo_row: self._open_quantity_editor(row)
            )
            clone_ammo = ammo_row.add_tool("⧉", tooltip="复制这个弹种另存一份（别人不动）")
            clone_ammo.clicked.connect(lambda _=False, line=ammo: self.clone_ammo(line))
            self._add_body(ammo_row)
            self._rows.append(ammo_row)
            ammo_rows.append(ammo_row)
        weapon_row.ammo_rows = tuple(ammo_rows)
        self._groups.append((weapon_row, tuple(ammo_rows)))

    def _add_body(self, widget: QWidget) -> None:
        """往主体尾部（stretch **之前**）加一个控件，并记下来（重画时要能摘干净）。"""
        self._section.body_layout().insertWidget(self._tail_index(), widget)
        self._body_widgets.append(widget)

    def _tail_index(self) -> int:
        """主体最后那个 stretch 的下标（stretch 永远是最后一项）。"""
        return max(0, self._section.body_layout().count() - 1)

    def _clear_body(self) -> None:
        """把上一次画的行全摘掉（走 detach：先 hide 再 setParent(None)）。"""
        layout = self._section.body_layout()
        for widget in self._body_widgets:
            layout.removeWidget(widget)
            detach(widget)
        self._body_widgets.clear()
        self._rows.clear()
        self._groups.clear()

    # ------------------------------------------------------------------ #
    # 选中 / 两个意图信号
    # ------------------------------------------------------------------ #

    def select(self, table: str, row_id: int) -> None:
        """选中某一行（只改控件里那份状态 + 高亮，**不发信号**）。"""
        self._selected = (str(table), int(row_id))
        self._apply_selection()

    def _apply_selection(self) -> None:
        table, row_id = self._selected
        for row in self._rows:
            row.set_selected(row_id > 0 and row.table == table and row.row_id == row_id)

    def clear_selection(self) -> None:
        """清掉选中（页面在**别的块**选中别的东西时调它）—— 不发信号、不写库。"""
        if self._selected == ("", 0):
            return
        self.select("", 0)

    def _on_row_clicked(self, row: BenchRow) -> None:
        if row.row_id <= 0:
            return
        self.select(row.table, row.row_id)
        self.row_selected.emit(row.table, row.row_id)

    # ------------------------------------------------------------------ #
    # 写：一律经 EditorContext
    # ------------------------------------------------------------------ #

    def _say(self, text: str) -> None:
        """说一句人话（状态栏）—— 成功 / 没有改动都不弹窗。"""
        self._status = str(text)
        self.status_message.emit(self._status)

    def _remove_ammo(self, row: AmmoRow) -> None:
        """弹行「✕」：删这一行 `WeaponAmmunitions`（武器行与 Ammunitions 模板行都不动）。"""
        context = self._context
        if context is None:
            return
        label, row_id = row.line.label, row.row_id
        context.delete_row(TABLE_WEAPON_AMMO, row_id)
        self._say(f"已移除弹种：{label}（WeaponAmmunitions #{row_id}）· 撤销（Ctrl+Z）")
        self.refresh()

    def _remove_weapon(self, row: WeaponRow) -> None:
        """武器行「✕」：删这一行 `SquadWeapons` —— **Weapons 模板行一行不动**。"""
        context = self._context
        if context is None:
            return
        # ❗ 删之前先数"还有几名成员扛着它"（删完那行就没了，数不出来）：
        #    SquadMembers ⊆ SquadWeapons 是 error 级不变量，违反了游戏里那件武器不显示。
        carriers = member_carriers(context.data, context.unit_id, row.weapon_id)
        label, row_id = row.line.label, row.row_id
        context.delete_row(TABLE_SQUAD_WEAPONS, row_id)
        note = (
            f"　⚠ 还有 {carriers} 名成员扛着它 —— 游戏里那件武器会不显示（Ctrl+Z 可撤）"
            if carriers
            else ""
        )
        self._say(
            f"已移除班用武器：{label}（SquadWeapons #{row_id}）"
            f"—— Weapons 模板行没动 · 撤销（Ctrl+Z）{note}"
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # 就地改携弹量
    # ------------------------------------------------------------------ #

    def _open_quantity_editor(self, row: AmmoRow) -> None:
        """点「×数量」：就在那一格上开浮层（没有确认按钮）。"""
        context = self._context
        if context is None:
            return
        self._close_editor()
        cell = context.cell(quantity_ref(row.row_id))
        if cell is None:
            # 这一列不在（数据源换版本了）：不假装能改，说一句就走。
            self._say(f"改不了：WeaponAmmunitions #{row.row_id} 没有 Quantity 这一列")
            return
        editor = QuantityEditor(cell, instance=context.instance)
        editor.committed.connect(lambda value, target=row: self._apply_quantity(target, value))
        editor.rejected.connect(
            lambda message, target=row: self._say(f"携弹量没改：{message}（值没写进去）")
        )
        editor.cancelled.connect(self._close_editor)
        row.attach_editor(editor)
        self._editor_row = row

    def _apply_quantity(self, row: AmmoRow, value) -> None:
        context = self._context
        if context is None:
            return
        old = row.line.quantity
        change = context.set_value(quantity_ref(row.row_id), value)
        self._close_editor()
        if change is None:
            self._say(f"{row.line.label} 的携弹量本来就是 ×{value}")
        else:
            self._say(f"已改携弹量：{row.line.label} ×{old} → ×{value}")
        self.refresh()

    def _close_editor(self) -> None:
        row, self._editor_row = self._editor_row, None
        if row is not None:
            row.close_editor()

    # ------------------------------------------------------------------ #
    # 就地冒候选（不弹对话框）
    # ------------------------------------------------------------------ #

    def _open_weapon_picker(self) -> None:
        """「＋ 加一件武器」：候选 = `Weapons` 全表（就地搜索）。再点一下 = 收手。"""
        context = self._context
        if context is None:
            return
        if self._picker is not None:
            self._close_picker()
            return
        picker = RowPicker(context.instance, TABLE_WEAPONS, parent=self._picker_host)
        picker.picked.connect(self._on_weapon_picked)
        picker.cancelled.connect(self._close_picker)
        self._mount_picker(picker, after=None)

    def _open_ammo_picker(self, row: WeaponRow) -> None:
        """武器行「＋」：给**这门枪**加一种弹 —— 第一列钉死这门枪，只让人挑弹种。"""
        context = self._context
        if context is None:
            return
        if self._picker is not None:
            self._close_picker()
            return
        picker = RowPicker(
            context.instance, TABLE_WEAPONS, second_table=TABLE_AMMO, parent=self._picker_host
        )
        if not picker.lock_first(row.weapon_id, row.line.label):
            # 没有第二列 = 这个 picker 不该在这儿用；宁可什么都不冒，也不给一个会插错表的入口。
            detach(picker)
            self._say("加弹的候选列表没出来（RowPicker 没有第二列）—— 请报给开发者")
            return
        picker.pair_picked.connect(self._on_ammo_picked)
        picker.cancelled.connect(self._close_picker)
        after = row.ammo_rows[-1] if row.ammo_rows else row
        self._mount_picker(picker, after=after)

    def _mount_picker(self, picker: RowPicker, *, after: QWidget | None) -> None:
        self._place_picker(after)
        self._picker = picker
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        picker.start()

    def _place_picker(self, after: QWidget | None) -> None:
        """把候选列表挪到该出现的位置（after=None = 所有行下面）。"""
        layout = self._section.body_layout()
        layout.removeWidget(self._picker_host)
        if after is None:
            index = self._tail_index()
        else:
            position = layout.indexOf(after)
            index = position + 1 if position >= 0 else self._tail_index()
        layout.insertWidget(index, self._picker_host)

    def _close_picker(self) -> None:
        picker, self._picker = self._picker, None
        if picker is not None:
            self._picker_layout.removeWidget(picker)
            detach(picker)  # 先 hide 再 setParent(None)：别留一个顶层小窗
        self._picker_host.setVisible(False)

    # ------------------------------------------------------------------ #
    # 「＋」选定之后：写库（经 EditorContext）
    # ------------------------------------------------------------------ #

    def _on_weapon_picked(self, weapon_id: int) -> None:
        """挑好一件武器 -> 插一行 `SquadWeapons`（关联行；Weapons 模板行不动）。"""
        self._close_picker()
        context = self._context
        if context is None:
            return
        weapon_id = int(weapon_id)
        data = context.data
        # 名字**现取**（与 viewmodel 同一条口径：带缓存的那份会给出旧字，见 _label 的说明）。
        label = display_name(context.instance, TABLE_WEAPONS, weapon_id)
        if weapon_id in registered_weapon_ids(data, context.unit_id):
            # uniq.SquadWeapons（UnitId + WeaponId）是 error 级：同一个班 × 同一门枪只留一行。
            self._say(f"{label} 已经在班用武器里了 —— 同一个班 × 同一门枪只留一行")
            return
        # ❗ 新行 Id 走 core/edit/clone.py::next_row_id（**别写"行数 + 1"**：Id 有大空洞，
        #    而且它会把"本会话删过的 Id"也算上 —— 只传 table 的话删掉最大 Id 会捡回那个号）。
        row_id = context.insert_row(
            TABLE_SQUAD_WEAPONS,
            {
                "Id": next_row_id(context.instance.table(TABLE_SQUAD_WEAPONS), context.changeset),
                "UnitId": context.unit_id,
                "WeaponId": weapon_id,
                # Order 排到最后：不打乱用户已经排好的清单（与注册武器那条同一口径）。
                "Order": len(context.row_ids(TABLE_SQUAD_WEAPONS)),
            },
        )
        self._say(
            f"已加班用武器：{label}（SquadWeapons #{row_id}）"
            "—— 它还没有配弹，点这一行尾巴的「＋」给它加一种弹"
        )
        self.refresh()

    def _on_ammo_picked(self, weapon_id: int, ammo_id: int) -> None:
        """挑好一种弹 -> 插一行 `WeaponAmmunitions`（走 CARD_BLOCKS["weapons"] 那条落点）。"""
        self._close_picker()
        context = self._context
        if context is None:
            return
        weapon_id, ammo_id = int(weapon_id), int(ammo_id)
        data = context.data
        weapon = display_name(context.instance, TABLE_WEAPONS, weapon_id)
        ammo = display_name(context.instance, TABLE_AMMO, ammo_id)
        if ammo_id in owned_ammo_ids(data, context.unit_id, weapon_id):
            # 同一个班 × 武器 × 弹种留一行就够（游戏自己读的是 First()）——
            # 再插一行是"看着加上了、其实哪一行生效说不清"。
            self._say(
                f"{weapon} 已经挂着 {ammo} 了（同一个班 × 武器 × 弹种只留一行）"
                "—— 点那一行的「×数量」改数量就行"
            )
            return
        # ❗ 这一句必须在 **insert 之前**算：刚插进去的那一行会让"全库这把枪用过这种弹"
        #    当场变成 True —— 先插后判的话，提示永远不出现（实测踩到，用例抓住的）。
        known = ammo_is_known(data, context.unit_id, weapon_id, ammo_id)
        table, row_id = context.add_block_row("weapons", weapon_id, ammo_id)
        # 挑到"全库没见过"的组合：**只提示、照样写**（用户点了他就是要 —— 这工具是给
        # mod 作者加新配置用的，堵死它等于把功能砍一半；口径见 Lead 2026-10-01 的批复）。
        note = "" if known else f"　⚠ 全库还没见过这件武器配「{ammo}」这种弹，游戏里可能不显示"
        self._say(
            f"已加弹：{weapon} × {ammo}（{table} #{row_id} · 默认 1 发 —— 点「×1」就地改）{note}"
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # 只读访问（用例与装配用）
    # ------------------------------------------------------------------ #

    def context(self):
        """现在接的 EditorContext（没有就是 None）。"""
        return self._context

    def section(self) -> BenchSection:
        return self._section

    def add_weapon_button(self) -> BenchAction:
        """抬头那颗「＋ 加一件武器」。"""
        return self._add_weapon

    def lines(self) -> tuple[WeaponLine, ...]:
        """现在画着的那些武器（**控件里那份状态**，不是现算的）。"""
        return self._lines

    def rows(self) -> tuple[BenchRow, ...]:
        """现在画着的所有行（武器行与弹行，按界面上的先后）。"""
        return tuple(self._rows)

    def weapon_rows(self) -> tuple[WeaponRow, ...]:
        return tuple(row for row, _ammo in self._groups)

    def ammo_rows(self) -> tuple[AmmoRow, ...]:
        return tuple(row for _weapon, ammo in self._groups for row in ammo)

    def missing_ammo_rows(self) -> tuple[MissingAmmoRow, ...]:
        """画着「缺弹 · 空枪」的那几行。"""
        return tuple(row for row in self._rows if isinstance(row, MissingAmmoRow))

    def selected_table(self) -> str:
        return self._selected[0]

    def selected_row_id(self) -> int:
        return self._selected[1]

    def status_text(self) -> str:
        """最近一句状态栏（用例断言它 —— 成功与"没改动"都走这里，绝不弹窗）。"""
        return self._status

    def picker(self) -> RowPicker | None:
        return self._picker

    def picker_visible(self) -> bool:
        return self._picker is not None and self._picker_host.isVisibleTo(self)

    def has_editor(self) -> bool:
        return self._editor_row is not None

    def open_ammo_row(self) -> AmmoRow | None:
        return self._editor_row

    def editor(self) -> QWidget | None:
        return self._editor_row.editor if self._editor_row is not None else None


__all__ = [
    "ADD_AMMO_TEXT",
    "ADD_WEAPON_TEXT",
    "AMMO_INDENT",
    "HINT",
    "MISSING_AMMO_TEXT",
    "NO_UNIT_TEXT",
    "NO_WEAPONS_TEXT",
    "TITLE",
    "AmmoRow",
    "LoadoutBench",
    "MissingAmmoRow",
    "QuantityEditor",
    "WeaponRow",
]
