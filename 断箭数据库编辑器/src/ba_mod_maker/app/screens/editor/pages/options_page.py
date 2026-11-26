"""改装选项专用页：选项图标 + 所属槽 + 同槽其它选项；右栏 = Options 46 列。

设计 §4 与图 B ⑥（单件大图 docs/设计稿-Options编辑器.png）。这一页要摆平四件事：

1. **46 列一个不少，按功能分组显示**。
   `core.edit.cells.row_cells` 给的是**表里的列序**（Id 打头、Turret0Id..Turret20Id 垫底），
   而设计稿右栏的阅读序是 8 组：身份 / 显示 / 成本与时间 / 覆盖·装甲与机动 / 覆盖·传感器 /
   覆盖·能力 / 覆盖·隐蔽与整单位替换 / 覆盖·炮塔挂载（21 个槽）。
   分组**只影响显示顺序**：`OPTION_GROUPS` 是唯一那份顺序表（46 列正好分完），
   `entries_cells()` 按它重排；**没登记的列一律追加在最后** —— 将来数据里多一列，
   这一页只会多画一格，不会悄悄少一列。
   ⚠️ 右栏那个面板（widgets/row_entries.py::RowEntriesPanel）**不按表分组**，
   一行一条平铺，所以右栏是"一组 46 行"；子组的标题与说明由左栏「覆盖」那一节给出。

2. **选项图标**：族键 `option_pictures`，键是 `Options.OptionPicture`
   （`core.assets.families` 里那一族声明的 column 就是它，tests/test_icons.py 守着）。
   取不到图就退回文字 —— 没装游戏的机器上这一页照常能用（与卡片同一条降级链）。

3. **同槽选项 = 互斥槽，点一下就选中它**（本轮定的口径，块 5）：
   **选中 = `Options.IsDefault`，同一个 `ModificationId` 下最多一条 true。**
   出处：反编译的 `GetDefaultOptions`（VA 0x18070C0F0）= 第一条 IsDefault ?? 第一条
   Order==0 —— 所以 IsDefault 是数据里唯一能表达"这个槽默认装哪一项"的列
   （Order 只是排列顺序，同槽还可以重复）。点一下的落点是 `select_slot_option`：
   写数据 -> 只进缓存（落盘走顶栏 💾）-> 卡片与这一页跟着重画。**变体借来的槽只给看不给写**
   （`Modifications.UnitId != 当前单位`：在借来的槽上翻 IsDefault 会一起改到槽的主人）。

4. **21 个炮塔槽 = 池 + 选项子集**：`Turret0Id..Turret20Id` 是「这一项要装的炮塔列表」。
   ❌ 别写成「替换第 N 槽」—— 数据侧那一步没定位，设计 §12.1 点名按"池 + 子集"呈现。
   非空的槽可以点开就地改；一个都没装时也要说清"21 个槽仍然都在右栏的 46 列里"。
   `Turret20Id` 全库为 0（字段词典点名"别拿它当第 21 个炮塔槽做新功能"），
   所以左栏**只画非空的槽**、永远不会给它留位置；右栏那 46 列里照旧保留它（一个不少）。

弹窗规则（AGENTS §5）：本页**一个弹窗都没有** —— 成功走 `changed` 信号（外层写状态栏），
错误由就地编辑浮层自己显示。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.entries_panel import NAME_COLUMN_WIDTH, table_title
from ba_mod_maker.app.screens.editor.pages.base import EditorContext, EditorPage
from ba_mod_maker.app.theme.icons import prepare_icon, shared_icon_cache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.unit_card import (
    SELECTION_COLUMN,
    exclusive_selection_writes,
    slot_selected_option,
)
from ba_mod_maker.core.assets.families import OPTION_PICTURE_FAMILY
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.foreign import display_name
from ba_mod_maker.core.edit.refs import CellRef

TABLE = "Options"
#: 改装槽那张表（`Options.ModificationId` 指向它）。
SLOT_TABLE = "Modifications"

#: 选项图标那一族（键 = `Options.OptionPicture`）。**字面量只在那份登记表里**，
#: 这里只留一个名字给本页用 —— 从前是本地抄一份字符串，抄错就是「永远没图标」。
ICON_FAMILY = OPTION_PICTURE_FAMILY

#: 左栏那张图标框的尺寸（设计稿上约 132 × 106 逻辑像素）。
ICON_BOX_WIDTH = 132
ICON_BOX_HEIGHT = 106
#: 同槽选项行上的小图标（设计稿上约 52 × 26）。
ICON_ROW_WIDTH = 52
ICON_ROW_HEIGHT = 26

#: 21 个炮塔挂载槽。**一个不少** —— 右栏那 46 列里有这 21 列。
TURRET_COLUMNS: tuple[str, ...] = tuple(f"Turret{i}Id" for i in range(21))

#: 覆盖列里**指向另一个单位**的那一列。core 的 refs.NOT_FOREIGN 把它排除在外键之外
#: （走进去会把被替换单位的整棵子树卷进来 —— 设计 §12 明确不做），所以 `cell.text`
#: 是 '231' 这种裸 Id。界面这一处自己解一次名字（用户口径：界面不给用户看 Id）。
UNIT_REF_COLUMNS: dict[str, str] = {"ReplaceUnitId": "Units"}


@dataclass(frozen=True, slots=True)
class OptionGroup:
    """右栏的一个功能分组：标题 + 一句话说明 + 这一组有哪几列。

    分组**只影响显示顺序**（见 `ordered_columns`），不影响"哪些列要画"。
    """

    title: str
    note: str
    columns: tuple[str, ...]


IDENTITY_GROUP = OptionGroup(
    "身份",
    "改装选项表 · 只这一行",
    ("Id", "ModificationId", "Name", "UIName", "Order", "IsDefault"),
)
DISPLAY_GROUP = OptionGroup(
    "显示",
    "选这一项时卡片上换什么图 / 什么名",
    (
        "OptionPicture",
        "ThumbnailOverride",
        "PortraitOverride",
        "AudioPresetOverride",
        "ReplaceUnitName",
        "ConcatenateWithUnitName",
    ),
)
COST_GROUP = OptionGroup(
    "成本与时间",
    "卡片成本 = 单位基础价 + 默认项费用之和",
    ("Cost", "TimeModifierRefund", "TimeModifierResurrect"),
)
#: 覆盖列那四组（左栏的小节标题与右栏的显示序用的是同一份）。
OVERRIDE_GROUPS: tuple[OptionGroup, ...] = (
    OptionGroup("覆盖 · 装甲与机动", "非 0 = 把单位本体那一维整套换掉", ("ArmorId", "MobilityId")),
    OptionGroup("覆盖 · 传感器", "主传感器是整套替换，不是追加", ("MainSensorId", "ExtraSensorId")),
    OptionGroup("覆盖 · 能力", "最多三项", ("Ability1Id", "Ability2Id", "Ability3Id")),
    OptionGroup(
        "覆盖 · 隐蔽与整单位替换",
        "ReplaceUnitId 非 0 = 这一项是「换成另一个单位」",
        ("StealthOverride", "ReplaceUnitId", "IsDynamicTurretOption"),
    ),
)
#: 炮塔那 21 列。说明按用户口径写：**池 + 选项子集**，不是"按序号替换某个槽"
#: （数据侧那一步没定位，设计 §12.1 点名按前者呈现）。
TURRET_GROUP = OptionGroup(
    "覆盖 · 炮塔挂载（21 个槽）",
    "池 + 选项子集：这 21 列是「这一项要装的炮塔列表」（Turret20Id 全库为 0，保留）",
    TURRET_COLUMNS,
)
#: 右栏 46 列的显示顺序 = 这 8 组顺着排（46 列正好分完）。
OPTION_GROUPS: tuple[OptionGroup, ...] = (
    IDENTITY_GROUP,
    DISPLAY_GROUP,
    COST_GROUP,
    *OVERRIDE_GROUPS,
    TURRET_GROUP,
)


def ordered_columns(columns: Iterable[str]) -> tuple[str, ...]:
    """按 `OPTION_GROUPS` 的顺序重排列名；**没登记的列追加在最后**。

    追加那一步是"一个不少"的兜底：将来数据里多一列，这一页只会多画一格，
    不会悄悄少一列。表里的原列序只用来给"没登记的那些"排序。
    """
    items = tuple(columns)
    present = set(items)
    seen: set[str] = set()
    out: list[str] = []
    for group in OPTION_GROUPS:
        for column in group.columns:
            if column in present and column not in seen:
                seen.add(column)
                out.append(column)
    for column in items:
        if column not in seen:
            seen.add(column)
            out.append(column)
    return tuple(out)


def slot_option_label(data, option) -> str:
    """一个选项显示什么名字（口径与 viewmodel 的 `_choices_of` 一致：UIName 优先）。"""
    rows = data.t(TABLE)
    return data.display_name(
        rows.s(option, "UIName"), rows.s(option, "Name", f"#{rows.i(option, 'Id')}")
    )


def slot_owner_unit(data, modification_id: int) -> int:
    """这个改装槽挂在哪个单位上（`Modifications.UnitId`）。查不到就是 0。"""
    rows = data.t(SLOT_TABLE)
    modification = rows.by_id(modification_id)
    return rows.i(modification, "UnitId") if modification is not None else 0


def select_slot_option(context: EditorContext, modification_id: int, option_id: int) -> str:
    """**「这个槽选中这一项」——写数据的那一下（界面里唯一一份）。**

    写哪几格由 viewmodel 的 `exclusive_selection_writes` 说了算（槽内互斥 =
    同一 ModificationId 下最多一条 `Options.IsDefault=true`）；这里只负责把它交给
    `EditorContext.set_value` —— 于是外层那三个后果照旧：**写进缓存（只进缓存，
    落盘走顶栏 💾）**、重画卡片与右栏、撤销栈里记一笔。

    返回一句给状态栏的话。**一个字都没写**时（借来的槽 / 已经是它 / 不是这个槽的选项）
    返回的是原因 —— 照旧只写状态栏，不弹窗。
    """
    data = context.data
    option = data.option(option_id)
    if option is None:
        return f"没有选项 #{option_id}"
    owner = slot_owner_unit(data, modification_id)
    if owner != context.unit_id:
        return (
            f"这个槽（#{modification_id}）挂在单位 #{owner} 上，不是本单位的槽 —— "
            "变体借来的槽只给看不给改（改它会一起改到槽的主人）"
        )
    if data.t(TABLE).i(option, "ModificationId") != modification_id:
        return f"选项 #{option_id} 不在这个槽（#{modification_id}）里"
    label = slot_option_label(data, option)
    writes = exclusive_selection_writes(data, modification_id, option_id)
    if not writes:
        return f"「{label}」已经是这个槽选中的那一项"
    for row_id, column, value in writes:
        context.set_value(CellRef(TABLE, row_id, column), value)
    # 写完丢一次缓存：`CardData` 把 Options 的行视图与倒排索引缓存过，而**这一份**
    # 是 context 那份（四个专用页读它）。
    # ⚠️ 它**不是**「点了不高亮」的原因 —— 实测加不加这句，那 12/30 次一样不跟：
    #    真因是 `option_groups` 的变体覆盖把卡的当前项改写掉了（见
    #    `_work/卡片高亮-复现证据.md`）。留着它只是因为值确实变了，别让下一页读到旧行。
    context.data.invalidate(TABLE)
    also = len(writes) - 1
    tail = f" · 同槽另外 {also} 项已自动取消" if also else ""
    return f"已选中「{label}」（{_amount(data.t(TABLE).i(option, 'Cost'))}）{tail}"


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


def _wrap(text: str, role: str) -> QLabel:
    label = _label(text, role)
    label.setWordWrap(True)
    return label


def _positive_int(value: object) -> bool:
    """是不是"指向某一行的正数 Id"（0 / bool / 文本都不算）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _amount(value: object) -> str:
    """费用按**相对价**写：加多少钱（0 就写 0）。Options.Cost 不是"改成多少钱"。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if not value:
        return "0"
    return f"+{value:g}"


class OptionIconBox(QFrame):
    """左栏那一格选项图：取到图就贴图，取不到就退回文字（不空着占位）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "editor-row")
        self.setFixedSize(ICON_BOX_WIDTH, ICON_BOX_HEIGHT)
        self._label = QLabel()
        self._label.setProperty("role", "empty-hint")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        box = QVBoxLayout(self)
        box.setContentsMargins(
            TOKENS.spacing.sm, TOKENS.spacing.sm, TOKENS.spacing.sm, TOKENS.spacing.sm
        )
        box.addWidget(self._label)

    def set_content(self, pixmap: QPixmap | None, fallback: str) -> None:
        if pixmap is None:
            self._label.setPixmap(QPixmap())
            self._label.setText(fallback)
            return
        self._label.setText("")
        self._label.setPixmap(pixmap)

    @property
    def has_pixmap(self) -> bool:
        shown = self._label.pixmap()
        return shown is not None and not shown.isNull()

    @property
    def text(self) -> str:
        return self._label.text()


class _Section(QFrame):
    """左栏的一个小节：标题 + 说明 + 内容。**只是版式**，样式全走已有角色。"""

    def __init__(self, title: str, note: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "editor-row")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.sm, TOKENS.spacing.md, TOKENS.spacing.sm
        )
        outer.setSpacing(TOKENS.spacing.xs)
        outer.addWidget(_wrap(title, "detail-section"))
        if note:
            outer.addWidget(_wrap(note, "muted"))
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(TOKENS.spacing.xs)
        outer.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget


class OptionsPage(EditorPage):
    """一个改装选项（含"同一个槽里的其它选项"）。"""

    def __init__(self, option_id: int, parent: QWidget | None = None, *, icons=None) -> None:
        # 副标题里**不写选项号**：同槽切换之后它就旧了，而 base.py 不给改标题的入口。
        # 选项号写在左栏身份卡上（跟着切换走）。
        super().__init__(
            "改装选项", "Options 46 列 · 其中 21 个炮塔挂载槽（池 + 选项子集）", parent
        )
        self._option_id = int(option_id)
        #: 图标缓存。默认延迟取进程内共享那份；也能由调用方注入（用例用假缓存，不碰 bundle）。
        self._icons = icons
        self._icons_connected = False
        self._icon_box: OptionIconBox | None = None
        #: 要贴图的地方：同槽按钮 (按钮, Options.OptionPicture 键)。
        self._sibling_buttons: list[tuple[QPushButton, str]] = []
        #: 这个槽**当前选中**的那一项那个按钮（没有选中项时 None）。
        self._selected_button: QPushButton | None = None
        self._override_buttons: dict[str, QPushButton] = {}
        self._turret_buttons: dict[str, QPushButton] = {}

    # ------------------------------------------------------------------ #
    # 只读（外层与用例看这里）
    # ------------------------------------------------------------------ #

    @property
    def option_id(self) -> int:
        return self._option_id

    @property
    def icon_box(self) -> OptionIconBox | None:
        return self._icon_box

    @property
    def sibling_buttons(self) -> tuple[QPushButton, ...]:
        return tuple(button for button, _key in self._sibling_buttons)

    @property
    def selected_button(self) -> QPushButton | None:
        """这个槽**当前选中**的那一项的按钮（画了选中态的那个）；没有选中项时 None。"""
        return self._selected_button

    @property
    def override_buttons(self) -> dict[str, QPushButton]:
        """覆盖列的值按钮，按列名索引（左栏那些）。"""
        return dict(self._override_buttons)

    @property
    def turret_buttons(self) -> dict[str, QPushButton]:
        """画出来的炮塔槽按钮，按列名索引（**只有非空的**）。"""
        return dict(self._turret_buttons)

    # ------------------------------------------------------------------ #
    # 左栏
    # ------------------------------------------------------------------ #

    def build_left(self) -> QWidget | None:
        if self.context is None:
            return None
        self._icon_box = None
        self._sibling_buttons = []
        self._selected_button = None
        self._override_buttons = {}
        self._turret_buttons = {}

        content = QWidget()
        content.setProperty("role", "detail-body")
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.md)
        for section in (self._identity(), self._siblings(), self._overrides(), self._turrets()):
            box.addWidget(section)
        box.addStretch(1)
        self._apply_icons()

        # 21 个炮塔槽全非空时左栏能到一千多像素，套一层滚动 —— 挤扁了会把下面几组裁掉。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _identity(self) -> QWidget:
        """身份卡：图标 + UIName + 选项号 + 所属槽 + 费用（相对价）+ 缺省项。"""
        frame = QFrame()
        frame.setProperty("role", "editor-row")
        row = QHBoxLayout(frame)
        row.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        row.setSpacing(TOKENS.spacing.lg)

        self._icon_box = OptionIconBox()
        row.addWidget(self._icon_box, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(TOKENS.spacing.xs)

        name = self._cell("UIName")
        info.addWidget(
            _wrap(name.text if name is not None else f"选项 #{self._option_id}", "entries-title")
        )
        info.addWidget(_label(f"选项 #{self._option_id}", "entry-place"))

        slot = self._cell("ModificationId")
        info.addWidget(_label(f"改装槽：{self._slot_name(slot)}", "entries-subtitle"))

        cost = self._cell("Cost")
        fee = QLabel(f"改装件费用 {_amount(cost.raw) if cost is not None else '—'}")
        fee.setProperty("role", "card-cost")
        info.addWidget(fee, 0, Qt.AlignmentFlag.AlignLeft)
        info.addWidget(_label("（相对价：加多少钱，不是改成多少钱）", "entry-place"))

        default = self._cell("IsDefault")
        info.addWidget(
            _wrap(
                f"缺省项：{default.text if default is not None else '—'}"
                " · 卡片成本 = 单位基础价 + 默认项费用之和",
                "muted",
            )
        )
        info.addWidget(self._selection_state(slot))
        row.addLayout(info, 1)
        return frame

    def _selection_state(self, slot: FieldCell | None) -> QLabel:
        """这一项**是不是这个槽现在选中的那一项** —— 选中态要一眼看得出来。

        判据是数据（`Options.IsDefault` ?? `Order==0`），不是"这一页正在看谁"：
        两者会不一样（可以点开一个没选中的选项看它的 46 列）。
        颜色只从 tokens 来（qss 的 `option-selected` 那一条）。
        """
        context = self.context
        if context is None or slot is None or not _positive_int(slot.raw):
            return _label("", "entry-place")
        modification_id = int(slot.raw)
        selected_id = slot_selected_option(context.data, modification_id)
        if selected_id == self._option_id:
            return _label("✓ 已选中：这个槽现在装的就是它", "option-selected")
        name = ""
        picked = context.data.option(selected_id) if selected_id else None
        if picked is not None:
            name = slot_option_label(context.data, picked)
        return _label(
            f"未选中：这个槽现在选的是「{name}」" if name else "未选中：这个槽一项都没选",
            "muted",
        )

    def _siblings(self) -> QWidget:
        """这个槽里的全部选项 —— **互斥**：点一下就选中它（其余自动取消）。

        选中态取自**数据**（`slot_selected_option` = IsDefault ?? Order==0），
        不是这一页记的临时状态：所以写下去、撤销回去，这一排跟着对。

        借来的槽（变体路径，槽的主人是别的单位）**只给看不给写** ——
        点它只换这一页看的行，理由写在那一排上面。
        """
        section = _Section("这个槽里的选项（互斥 · 点一下就选中它）")
        slot = self._cell("ModificationId")
        if slot is None or not _positive_int(slot.raw):
            section.add(_wrap("这一行没有所属槽（ModificationId = 0）", "muted"))
            return section

        context = self.context
        assert context is not None
        modification_id = int(slot.raw)
        rows = context.data.options_of(modification_id)
        selected_id = slot_selected_option(context.data, modification_id)
        owner = slot_owner_unit(context.data, modification_id)
        writable = owner == context.unit_id
        if not writable:
            section.add(
                _wrap(
                    f"这个槽（#{modification_id}）挂在单位 #{owner} 上（变体借来的）——"
                    "只给看不给改：改它会一起改到槽的主人。",
                    "muted",
                )
            )

        table = context.data.t(TABLE)
        for row in rows:
            option_id = table.i(row, "Id")
            name = slot_option_label(context.data, row)
            is_selected = option_id == selected_id
            viewing = option_id == self._option_id
            mark = "●" if is_selected else "○"
            suffix = " · 已选中" if is_selected else (" · 正在看" if viewing else "")
            fee = _amount(table.i(row, "Cost"))
            button = QPushButton(f"{mark} {name}（费用 {fee}）{suffix}")
            button.setProperty("role", "entry-value")
            if is_selected:
                # 选中态：颜色只从 tokens 来（qss 的 [selected="true"] 那一条）。
                button.setProperty("selected", "true")
            elif viewing:
                # 「正在看」复用 entry-value 的 dirty 态（描一圈强调边）。
                button.setProperty("dirty", "true")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if is_selected:
                button.setToolTip("这个槽现在选中的就是它（槽内互斥：一个槽只有一项）")
            elif writable:
                button.setToolTip(
                    f"点它 = 选中这一项（写 Options.{SELECTION_COLUMN}，同槽其它项自动取消）。"
                    "改动只进缓存，点顶栏「💾 保存」才落盘"
                )
            else:
                button.setToolTip(
                    f"这个槽挂在单位 #{owner} 上（变体借来的）—— 点它只换这一页看的行，不写数据"
                )
            button.clicked.connect(lambda _=False, oid=option_id: self._pick(oid))
            self._sibling_buttons.append((button, table.s(row, "OptionPicture")))
            if is_selected:
                self._selected_button = button
            section.add(button)
        if not rows:
            section.add(_wrap("这个槽里一项都没有", "muted"))
        return section

    def _overrides(self) -> QWidget:
        """覆盖列单独分组：非 0 = 把单位本体那一维整套换掉；每一列都能点开就地改。"""
        section = _Section(
            "覆盖（非 0 = 把单位本体那一维整套换掉）",
            "★ 这就是「换装改变单位」的唯一入口：覆盖列一旦非 0，卡片上的"
            "成本 / 武器 / 数值 / 名字 / 缩略图 / 装甲六条链都要跟着重算。",
        )
        for group in OVERRIDE_GROUPS:
            section.add(_wrap(group.title, "entry-name"))
            section.add(_wrap(group.note, "entry-count"))
            for column in group.columns:
                cell = self._cell(column)
                if cell is None:
                    continue
                row, button = self._cell_row(cell)
                self._override_buttons[column] = button
                section.add(row)
        return section

    def _turrets(self) -> QWidget:
        """21 个炮塔挂载槽 —— **池 + 选项子集**。

        ❌ 不是"按序号替换某个槽"：这几列是**这一项要装的炮塔列表**（数据侧那一步
        没定位，设计 §12.1 点名按"池 + 子集"呈现）。非空的都能点开就地改；
        全空时也要说清 21 个槽仍然都在右栏。

        `Turret20Id` 全库为 0，所以这一节永远不画它（字段词典：别给这个槽留位置）；
        它仍然在右栏的 46 列里，要填也能从那儿点开。
        """
        section = _Section("覆盖 · 炮塔挂载（21 个槽）", TURRET_GROUP.note)
        filled = 0
        for column in TURRET_COLUMNS:
            cell = self._cell(column)
            if cell is None or not _positive_int(cell.raw):
                continue
            filled += 1
            row, button = self._cell_row(cell)
            self._turret_buttons[column] = button
            section.add(row)
        if not filled:
            section.add(_wrap("这一项 21 个槽全空 —— 池里一个炮塔都没挑。", "muted"))
        section.add(
            _wrap(
                "21 个槽都在右栏的 46 列里（这里只画非空的；空槽也能在右栏点开填）。",
                "entry-place",
            )
        )
        return section

    def _cell_row(self, cell: FieldCell) -> tuple[QWidget, QPushButton]:
        """一行覆盖列：中文名 │ 可点的值 │ 「它指向哪一行」小字。

        值是**那一行的名字**，不是裸 Id（ReplaceUnitId 要自己解一次，
        见 UNIT_REF_COLUMNS）；小字按设计稿写成「装甲模板#1 · ArmorId」——
        比"本页这一格落在 Options#504"有用：本页本来就是那一行。
        """
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS.spacing.sm)

        name = _label(cell.cn, "entry-name")
        name.setFixedWidth(NAME_COLUMN_WIDTH)
        row.addWidget(name)

        text = self._ref_text(cell)
        button = QPushButton(text)
        button.setProperty("role", "entry-value")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"{cell.cn} = {text}\n{cell.place}\n{cell.doc}")
        button.clicked.connect(lambda _=False, target=cell: self._open_inline_editor(target))
        row.addWidget(button, 1)

        row.addWidget(_label(self._ref_place(cell), "entry-place"))
        return host, button

    def _ref_text(self, cell: FieldCell) -> str:
        """这一格写什么：指向另一行的给**那一行的名字**，别的照 core 的显示值。"""
        target = UNIT_REF_COLUMNS.get(cell.column)
        if target is None:
            return cell.text
        if not _positive_int(cell.raw):
            return "（不使用）"
        return display_name(self._instance(), target, int(cell.raw))

    def _ref_place(self, cell: FieldCell) -> str:
        """它指向哪一行：「目标表 # 行 Id · 列名」；没指向谁就退回本页的落点。"""
        target = cell.foreign or UNIT_REF_COLUMNS.get(cell.column)
        if target is None or not _positive_int(cell.raw):
            return cell.place
        return f"{table_title(target)}#{cell.raw} · {cell.column}"

    def _slot_name(self, slot: FieldCell | None) -> str:
        """所属槽显示**槽的名字**，不是那个 Id。

        ❗ 走 core 的 display_name，**不读 UIName 那一格**：
        Modifications.UIName 在这一版数据里是内部标识（Custom_Slot_Weapon_package），
        Name 才是人看的（Weapons BTR82）。候选顺序的口径只有 core/edit/foreign.py 一份。
        """
        if slot is None or not _positive_int(slot.raw):
            return "（没有所属槽）"
        context = self.context
        if context is not None:
            return display_name(context.instance, "Modifications", int(slot.raw))
        return f"改装槽 #{slot.raw}"

    def _pick(self, option_id: int) -> None:
        """点了一个选项：**选中它**（写 `Options.IsDefault`，槽内互斥），再把这一页切过去。

        写数据那一下在 `select_slot_option` 里：它走 `EditorContext.set_value`
        ——于是外层照旧"写进缓存 + 重画卡片与右栏"（落盘走顶栏 💾）。

        借来的槽（变体）与"点的就是现在这一项"两种情况**一个字都不写**：
        `select_slot_option` 会把原因说回来，这里照旧只写状态栏、不弹窗。
        """
        if option_id == self._option_id:
            return
        context = self.context
        if context is None:
            return
        slot = self._cell("ModificationId")
        if slot is None or not _positive_int(slot.raw):
            self._switch_to(option_id)
            return
        message = select_slot_option(context, int(slot.raw), option_id)
        self._switch_to(option_id)
        self.status_message.emit(message)

    def _switch_to(self, option_id: int) -> None:
        """换这一页看的是哪一行（**不改数据**）。

        ❗ 它**不是**"选中"：选中是 `_pick` 写 `Options.IsDefault` 那一下
        （见模块 docstring 的口径）。换完发 changed：外层照旧刷新角标与卡片，
        变更集里一条都没有。
        """
        if option_id == self._option_id:
            return
        self._option_id = int(option_id)
        self._rebuild()
        self.changed.emit()

    # ------------------------------------------------------------------ #
    # 图标
    # ------------------------------------------------------------------ #

    def _icon_cache(self):
        """图标缓存：延迟取**进程内共享**那一份（没装游戏时 pixmap 一律 None）。"""
        if self._icons is None:
            self._icons = shared_icon_cache()
        if not self._icons_connected:
            loaded = getattr(self._icons, "loaded", None)
            if loaded is not None:
                loaded.connect(self._on_icons_loaded)
            self._icons_connected = True
        return self._icons

    def _pixmap(self, key: str) -> QPixmap | None:
        if not key:
            return None
        return self._icon_cache().pixmap(ICON_FAMILY, key)

    def _apply_icons(self) -> None:
        """把到货的图贴上去。

        图标是**一批批到的**（IconCache.loaded 每 80ms 合并一次），到货后只换图、
        不重建控件 —— 免得把用户正在看的左栏抖一下。
        """
        if self._icon_box is not None:
            picture = self._picture_key()
            pixmap = self._pixmap(picture)
            self._icon_box.set_content(
                None
                if pixmap is None
                # 翻不翻（这一族要翻）+ DPR 缩放：唯一入口 prepare_icon。
                else prepare_icon(
                    pixmap,
                    ICON_FAMILY,
                    QSize(ICON_BOX_WIDTH, ICON_BOX_HEIGHT),
                    self._icon_box,
                    picture,
                ),
                self._icon_fallback(),
            )
        for button, key in self._sibling_buttons:
            pixmap = self._pixmap(key)
            button.setIcon(
                QIcon(
                    prepare_icon(
                        pixmap,
                        ICON_FAMILY,
                        QSize(ICON_ROW_WIDTH, ICON_ROW_HEIGHT),
                        button,
                        key,
                    )
                )
                if pixmap
                else QIcon()
            )

    def _on_icons_loaded(self) -> None:
        self._apply_icons()

    def _picture_key(self) -> str:
        cell = self._cell("OptionPicture")
        return str(cell.raw) if cell is not None and cell.raw else ""

    def _icon_fallback(self) -> str:
        """取不到图时那格里写什么：先写图标键，没键就写选项名（不编）。"""
        key = self._picture_key()
        if key:
            return key
        name = self._cell("UIName")
        if name is not None and name.text:
            return name.text
        return f"选项 #{self._option_id}"

    # ------------------------------------------------------------------ #
    # 取数（一律走 core / context，界面不算数）
    # ------------------------------------------------------------------ #

    def _instance(self):
        context = self.context
        assert context is not None
        return context.instance

    def _cell(self, column: str) -> FieldCell | None:
        context = self.context
        if context is None:
            return None
        return context.cell(CellRef(TABLE, self._option_id, column))

    def _foreign_cell(self, table: str, row_id: int, column: str) -> FieldCell | None:
        context = self.context
        if context is None:
            return None
        return context.cell(CellRef(table, row_id, column))

    # ------------------------------------------------------------------ #
    # 右栏（46 列，按功能分组**排显示顺序**）
    # ------------------------------------------------------------------ #

    def entries_cells(self) -> tuple[FieldCell, ...]:
        context = self.context
        if context is None:
            return ()
        cells = context.cells_of_row(TABLE, self._option_id)
        if not cells:
            return ()
        by_column = {cell.column: cell for cell in cells}
        return tuple(by_column[column] for column in ordered_columns(by_column))

    def entries_order(self) -> tuple[str, ...]:
        return (TABLE,)

    def entries_title(self) -> str:
        """右栏抬头：设计稿写的就是这句（左上角那句「改装选项的全部词条」）。"""
        return "改装选项的全部词条"

    def entries_summary(self) -> str:
        return (
            f"{TABLE}#{self._option_id} · {len(self.entries_cells())} 列 · {len(OPTION_GROUPS)} 组"
        )


__all__ = [
    "ICON_FAMILY",
    "IDENTITY_GROUP",
    "OPTION_GROUPS",
    "OVERRIDE_GROUPS",
    "TABLE",
    "TURRET_COLUMNS",
    "TURRET_GROUP",
    "OptionGroup",
    "OptionIconBox",
    "OptionsPage",
    "ordered_columns",
]
