"""编成台属性栏（装甲 / 机动 / 能力 / 传感器 / 专精可用性）+ 改装槽（槽内互斥）的控件。

2026-10-02 拆块：**一类一栏** —— StatsBench(kinds=(...)) 只画传进来的那一类。
各栏抬头那个圈码由 bench_page 按**摆第几块**生成，所以 STATS_TITLE / SLOTS_TITLE
这两个默认值里**不带编号**（一份数据一个说法）。

两块共用一套口径（详见 `app/viewmodel/bench_slots.py` 的模块注释与
`docs/交接-2026-10-01-单位编成台设计.md` §3）：

- **只有 ＋ 和 ✕，没有"换"**（口径 1）；
- ✕ 删的是**关联行**（UnitArmors / UnitPropulsions / UnitAbilities），
  Armors / Mobility / Abilities 那些模板行一个字节不动（口径 7）；
- ④ 点一项 = **选中它**（写 `Options.IsDefault`，槽内互斥）；**借来的槽只给看不给选**
  （`CardOptionGroup.is_own_slot == False`，实测 645 借 108 的 743）。

这一层只做三件事：摆控件、把意图转给 `EditorContext`、把结果写状态栏。
取数与规则全在 viewmodel（零 Qt），颜色全在 tokens（本文件一个色值都没有）。

④ 的**费用那一格**用 role `bench-row-cost`（暖金 = tokens 的 cost_text：设计稿那一列的
+10 / +25 / +55 就是它）；③ 的 value 格仍旧是 `bench-row-value` —— 那里面写的是
"装甲 / 机动 / 能力"这类词，不是钱，不许染成金色。

Lead 定稿的两处偏离（设计图 -> 实现）：③ 一行 = 关联表里的一行；③ 的 ＋ 按表拆成三颗
（「＋ 装甲 / ＋ 机动 / ＋ 能力」）—— 一颗按钮说不清落到哪张表。
❗ 2026-10-02 拆块之后**一栏只有一张表**：kinds 传几类就长几颗按钮，装配方一块只传一类
（所以每块抬头现在是一颗 ＋）。

## 折叠态（SlotsBench(collapsed=True)，只给载具 / 飞机用）

设计图 docs/设计稿-编成台-飞机.png 的 ② 自己标了一句 ⚠️：「F-15E 的 31 个选项铺不下
（载具 BTR-ZD 只有 3 个，可以全列）。所以改装槽做成『一行一个槽、只显示当前选中项，
候选就地展开』—— 这是飞机版相对载具版**唯一的局部改动**。」实现就是下面这一个开关：

开关只有两个取值：

- collapsed=False（**默认**，步兵 / 载具）：每个槽一行灰字抬头（槽名 · N 个选项）
  ＋ **该槽全部**选项逐行列出；
- collapsed=True（飞机）：每个槽**只有一行** —— 槽名 · #槽Id · N 选 1 · ● 当前选中项 · 费用。
  点这一行 / 「▸ 展开候选」**就地**展开候选（第一次展开才建那几行，之后只切可见性），
  再点一下收起。

❗ **默认值决定步兵那一块的行为**：collapsed=False 与加这一手之前**逐字一致**
（现有 tests/test_bench_slots.py 一条都不许改）；装载顺序由 bench_page 定。

## 块内分组折叠（2026-10-02，用户口径：「把所有能做成折叠栏的都做成折叠栏」＋「默认折叠」）

**非折叠态**（步兵 / 载具）里"每个槽那一条灰字抬头"也能点开 / 收起该槽的选项行：

- **只有 ≥ 2 个槽时才折**（用户既有口径：「只有一条的不套折叠，纯粹的画蛇添足」）——
  只 1 个槽就是原来那条平铺的灰字抬头，连记号都不给；
- **默认收起**；点抬头 = 展开，再点 = 收起（`toggle_group` / `is_group_expanded`）；
- 展开态跨 `refresh()` **与换单位**都留着（`_open_groups`）——与关系树那条口径一致
  （「以后每次重画都恢复用户当时的展开态」），别每次重画又回到默认收起；
- 收起 / 展开**只切可见性**，选项行一个都不重建（AGENTS §0.7）；
- 记号 ▸/▾ **单占一格**（role `bench-section-caret`），灰字抬头文字**逐字不变**
  （先例：用例按文字断言「N 个选项」/「借来的槽（属于单位 #108）」）。

⚠️ 与上面那套 `collapsed=True`（飞机）**不是一回事**：那套是"一行一个槽 + 候选就地展开"
（`_open_slots` / `is_expanded` / `candidate_rows`），语义一个字节都没动。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import bench_slots as vm
from ba_mod_maker.app.viewmodel.bench_slots import SlotGroupView, SlotOptionView, StatLine
from ba_mod_maker.app.viewmodel.row_icon import RowMark, row_icon
from ba_mod_maker.app.viewmodel.table_labels import table_emoji
from ba_mod_maker.app.viewmodel.unit_card import row_name
from ba_mod_maker.app.widgets.bench_kit import (
    BenchAction,
    BenchEmpty,
    BenchRow,
    BenchSection,
    IconLabel,
    id_badge_text,
    restyle,
)
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.assets.families import OPTION_PICTURE_FAMILY
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import TableError

#: 属性栏那几块抬头的**默认值**（三类都画时才用得上的那一种）。
#: ❗ 里面**不带编号**：编号 = 摆第几块，由 bench_page 生成（一份数据一个说法）。
#: 拆块之后每块只画一类，抬头按 kinds 现算，见 StatsBench.__init__。
STATS_TITLE = "装甲 / 机动 / 能力"
STATS_HINT = "UnitArmors · UnitPropulsions · UnitAbilities"

#: 改装槽抬头的**默认值**（❌ 不带编号）：载具 / 飞机摆 ②，步兵摆 ⑧
#: —— 编号由 bench_page 按位置写，块自己写死就会出现两个说法。
SLOTS_TITLE = "改装槽（槽内互斥）"
SLOTS_HINT = "Modifications / Options · 选中哪个决定这个班变成什么"

#: 选项那一行前面的圆圈：「●」= 这个槽现在选中的就是它。
MARK_ON = "●"
MARK_OFF = "○"

#: 折叠态（飞机）那一行的两颗开关文案。**不许出现省略号** —— 用户 2026-09-30 口径：
#: 「『换...』你也给我显示全了啊」；语义靠词组补齐，不靠省略号。
EXPAND_TEXT = "▸ 展开候选"
COLLAPSE_TEXT = "▾ 收起候选"

#: 折叠态里槽名那一列的最小宽 —— 几个槽的 #槽Id 与说明要对在同一条列线上（设计图 ② 就是这样）。
SLOT_TITLE_MIN_WIDTH = 160


class StatsBench(QWidget):
    """属性栏里的一栏：一行 = 关联表里的一行，抬头一颗 ＋ 管一张表。

    2026-10-02 拆块之后**一栏只画一类**（kinds=(vm.STAT_KINDS[0],) 就是「装甲」那一栏）
    —— 默认值仍然是三类一起（vm.STAT_KINDS），所以既有"直接建一块"的用例逐字不变。

    发两种话：`row_selected`（我选中了它）/ `status_message`（一句人话，绝不弹窗）。
    """

    #: (表名, 行Id) —— 关联行（UnitArmors …），右栏照这个画
    row_selected = Signal(str, int)
    #: 一句人话（绝不弹窗）
    status_message = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        kinds: tuple[vm.StatKind, ...] | None = None,
        title: str = "",
        hint: str = "",
    ) -> None:
        """kinds = 这一栏画哪几类（拆块之后就是**一类一栏**）。

        ❗ 默认 None -> vm.STAT_KINDS（三类一起）—— 与拆块之前**逐字一致**，
        所以"直接建一块 StatsBench()"的既有用例一条都不用改。

        title / hint 只在**显式传**时才用；不传就按 kinds 现算
        （vm.stat_block_title / stat_block_hint）—— **编号一个字都不带**：
        编号 = 摆第几块，由 bench_page 写。
        """
        super().__init__(parent)
        self._context = None
        self._icons = None
        self._kinds: tuple[vm.StatKind, ...] = tuple(kinds) if kinds else vm.STAT_KINDS
        self._section_title = str(title) or vm.stat_block_title(self._kinds)
        self._section_hint = str(hint) or vm.stat_block_hint(self._kinds)
        self._lines: tuple[StatLine, ...] = ()
        self._rows: list[tuple[StatLine, BenchRow]] = []
        #: 主体里**本类自己**画着的控件 —— 见 _clear_body 的理由（候选宿主不在此列）。
        self._body_widgets: list[QWidget] = []
        self._selected: tuple[str, int] = ("", 0)
        self._picker: RowPicker | None = None
        self._picker_table = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOKENS.spacing.xs)

        self._section = BenchSection(self._section_title, self._section_hint)
        self._add_buttons: dict[str, BenchAction] = {}
        for kind in self._kinds:
            button = self._section.add_action(
                kind.action, tooltip=f"从 {kind.target} 里挑一行挂到本单位"
            )
            button.clicked.connect(lambda _=False, table=kind.table: self.begin_add(table))
            self._add_buttons[kind.table] = button
        root.addWidget(self._section)

        # 候选列表**就地在这一块下面**冒出来（用户口径：不需要这么多弹窗）。
        # ❗ 2026-10-02（折叠）：宿主搬进 section **主体**里。从前它挂在 root 上（主体之外），
        #    于是"收起这一块"只 hide 主体，候选列表会留在屏幕上（点抬头 ▸ 之后这一块
        #    并没有真的只剩抬头那一行）。主体里的行改由本类自己记账（_body_widgets +
        #    _clear_body），不再用 section.clear_body()：那一手会把宿主一起 detach 掉，
        #    下一帧 close_picker 就是在碰一个已经 deleteLater 的控件。
        self._picker_host = QWidget()
        self._picker_host.setProperty("role", "bench-body")
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(
            TOKENS.spacing.sm, 0, TOKENS.spacing.sm, TOKENS.spacing.sm
        )
        self._picker_layout.setSpacing(0)
        self._section.body_layout().addWidget(self._picker_host)
        self._picker_host.setVisible(False)

    # ------------------------------------------------------------------ #
    # Lead 定的装配接口
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上 `EditorContext`（None = 清空）。写库一律经它，这一层不碰 changeset。"""
        self._context = context
        self._selected = ("", 0)
        self.close_picker()
        self.refresh()

    def refresh(self) -> None:
        """按当前数据重画（幂等）：数据变了就调它。"""
        self._clear_body()
        self._rows.clear()
        context = self._context
        if context is None:
            self._lines = ()
            self._add_body(BenchEmpty("（还没有选中单位）"))
            return
        self._lines = vm.stat_lines(context.data, context.unit_id, kinds=self._kinds)
        if not self._lines:
            self._add_body(
                BenchEmpty(
                    f"（本单位还没有{vm.stat_block_title(self._kinds)}行 —— 用右上角的 ＋ 挂一行）"
                )
            )
            return
        for line in self._lines:
            row = BenchRow(
                name=line.label,
                detail=line.detail,
                value=line.category,
                # ❗ 取 **link_row_id**（模板行）不是 row_id（关联行）：这一行的名字说的是
                #    Armors / Mobility / Abilities 那一行（实测 Armor Infantry Elite 14Men
                #    就是 Armors#118，与左卡 tooltip 里那个 #118 是同一行）。
                #    link_id 为 0 时 label 自己已经写着 "UnitArmors #N"，这里给空串不重复。
                id_text=id_badge_text(line.link_row_id),
            )
            row.activated.connect(lambda line=line: self._on_clicked(line))
            row.remove_requested.connect(lambda line=line: self.remove_line(line))
            # ❗ 用户 2026-10-02 定的口径：「**复制装甲模板另存一份**」—— 关联行的复制
            #    不是再挂一条同样的关联，而是把**模板行**（Armors / Mobility / …）另存一份、
            #    本单位这条关联行指过去（别人用着的那份不动）。走 clone_row_to，一行都不另写。
            clone_tool = row.add_tool(
                "⧉", tooltip="把这一行的模板另存一份给本单位（新 Id，别人不动）"
            )
            clone_tool.clicked.connect(lambda _=False, line=line: self.clone_line(line))
            row.set_selected(self._is_selected(line))
            self._add_body(row)
            self._rows.append((line, row))

    # ---- 主体记账（候选宿主钉在主体最后，行排在它前面）------------------ #

    def _add_body(self, widget: QWidget) -> None:
        """往主体里加一个控件（**永远排在候选宿主之前**）。"""
        self._section.body_layout().insertWidget(self._tail_index(), widget)
        self._body_widgets.append(widget)

    def _tail_index(self) -> int:
        """候选宿主那一项的下标（没有它时退到末尾）。"""
        layout = self._section.body_layout()
        index = layout.indexOf(self._picker_host)
        return index if index >= 0 else layout.count()

    def _clear_body(self) -> None:
        """把上一次画的行全摘掉（走 detach：先 hide 再 setParent(None)）—— 候选宿主留着。"""
        layout = self._section.body_layout()
        for widget in self._body_widgets:
            layout.removeWidget(widget)
            detach(widget)
        self._body_widgets.clear()

    def reload_values(self) -> None:
        """**就地**把值换成最新的 —— 一个控件都不重建（用户提过几回的那条）。

        用户原话：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
        `refresh()` 会把每一行 detach 掉再重建，肉眼就是闪一下；**结构没变**（还是那几行）
        时走这一条：只写文字（名字 / 说明 / 类别），控件、布局、滚动位置一个都不动。

        结构真变了（插 / 删行）本该由外层判署名后调 `refresh()`（BenchPage 就是这么做的）；
        这里再兜一道 —— 行集合对不上，就地写会把值写到**错行**上，那就退回 refresh()。
        """
        context = self._context
        if context is None or not self._rows:
            return
        lines = vm.stat_lines(context.data, context.unit_id, kinds=self._kinds)
        if [(line.table, line.row_id) for line in lines] != [
            (line.table, line.row_id) for line in self._lines
        ]:
            self.refresh()
            return
        widgets = [row for _line, row in self._rows]
        self._rows = []
        for line, row in zip(lines, widgets, strict=True):
            row.set_name(line.label)
            row.set_detail(line.detail)
            row.set_value(line.category)
            # ❗ Id 跟着新数据走：把这一行重指到另一个模板行之后，名字变了 Id 也得变。
            row.set_id_text(id_badge_text(line.link_row_id))
            self._rows.append((line, row))
        self._lines = lines

    def set_icons(self, store) -> None:
        """接一份图标缓存（`theme/icons.py::shared_icon_cache()` 那份）。

        ③ 的行上不画图标（设计图那块就没有），所以这里现在只做保存 ——
        留着它是因为这一层与另外两块共用同一条装配接口。
        """
        self._icons = store

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #

    def clone_line(self, line: StatLine) -> int:
        """「⧉」：把这一行**指向的模板行**另存一份（新 Id），本单位这条关联行指过去。

        口径是用户 2026-10-02 当场定的：「**复制装甲模板另存一份**」—— 复制的对象是
        `Armors` / `Mobility` / `Abilities` / `Sensors` / `Specializations` 那一行，
        **不是**再挂一条同样的关联行；别人用着的那一份一个字节不动
        （`EditorContext.clone_row_to` 的既有语义，与卡上那颗 📄 另存为新模板同一套实现）。

        复制完**当场选中新模板行** —— 复制的用途就是照着改，右栏直接落到它。
        """
        context = self._context
        if context is None:
            return 0
        if not line.link_table or not line.link_row_id:
            self.status_message.emit("这一行没有指向模板行 —— 没有可复制的模板")
            return 0
        kind = vm.stat_kind(line.table)
        if kind is None:
            self.status_message.emit(f"不认识这一类（{line.table}）—— 复制不了")
            return 0
        link = CellRef(line.table, line.row_id, kind.column)
        new_id, message = context.clone_row_to(line.link_table, line.link_row_id, link)
        self.status_message.emit(f"{message} —— 右栏就是新模板，直接在上面改")
        self.refresh()
        self.row_selected.emit(line.link_table, new_id)
        return new_id

    def begin_add(self, table: str) -> bool:
        """某一类抬头的「＋」：就地冒候选列表（候选 = 那张**模板表**里的行）。

        返回"有没有接下来"（False = 这一类不认，外面不用等信号）。
        """
        kind = vm.stat_kind(table)
        context = self._context
        if kind is None or context is None:
            return False
        picker = RowPicker(context.instance, kind.target, parent=self._picker_host)
        picker.picked.connect(lambda row_id, table=table: self._on_picked(table, int(row_id)))
        picker.cancelled.connect(self.close_picker)
        self.close_picker()
        self._picker = picker
        self._picker_table = table
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        picker.start()
        return True

    def close_picker(self) -> None:
        """收起候选列表（点中 / Esc / 换一块都走它）。"""
        detach(self._picker)
        self._picker = None
        self._picker_table = ""
        self._picker_host.setVisible(False)

    def _on_picked(self, table: str, target_id: int) -> None:
        """候选列表里点中了一行：先收起列表，再落库。"""
        self.close_picker()
        self.add_line(table, target_id)

    def add_line(self, table: str, target_id: int) -> int:
        """给本单位挂一行（**写库经 `EditorContext.insert_row`**）。返回新行 Id，没加成给 0。

        新行 Id 走 `viewmodel.bench_slots.new_stat_values` -> `clone.next_row_id`，
        不在这里算"最大 + 1"。
        """
        kind = vm.stat_kind(table)
        context = self._context
        if kind is None or context is None or int(target_id) <= 0:
            return 0
        values = vm.new_stat_values(
            context.data, context.unit_id, table, int(target_id), changeset=context.changeset
        )
        row_id = int(context.insert_row(table, values))
        label = row_name(context.data, kind.target, int(target_id))
        self.status_message.emit(f"已加一行{kind.category}：{label}（{table} #{row_id}）")
        self.refresh()
        return row_id

    def remove_line(self, line: StatLine) -> None:
        """行尾 ✕：删**这一行关联行**（模板行不动）。"""
        context = self._context
        if context is None:
            return
        try:
            context.delete_row(line.table, line.row_id)
        except TableError as error:
            self.status_message.emit(f"删不掉 {line.table}#{line.row_id}：{error}")
            return
        if self._selected == (line.table, line.row_id):
            self._selected = ("", 0)
        self.status_message.emit(
            f"已删掉 {line.table} #{line.row_id}（{line.label}）—— 只删了本单位这一行，"
            f"{line.link_table} 那份模板没动"
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # 读数（用例钉控件里那一份状态，不现算）
    # ------------------------------------------------------------------ #

    def rows(self) -> tuple[BenchRow, ...]:
        return tuple(row for _line, row in self._rows)

    def lines(self) -> tuple[StatLine, ...]:
        """和行一一对应（顺序 = 界面上的顺序）。"""
        return tuple(line for line, _row in self._rows)

    def selected(self) -> tuple[str, int]:
        return self._selected

    def selected_row_id(self) -> int:
        return self._selected[1]

    def selected_table(self) -> str:
        return self._selected[0]

    def add_buttons(self) -> tuple[BenchAction, ...]:
        """抬头那几颗 ＋（顺序 = 本栏那几类；拆块之后一栏一颗）。"""
        return tuple(self._add_buttons[kind.table] for kind in self._kinds)

    def kinds(self) -> tuple[vm.StatKind, ...]:
        """这一栏画的是哪几类（装配方与用例读它）。"""
        return self._kinds

    def section_title(self) -> str:
        """这一栏抬头那行字（**读控件里那一份**：装配方写完编号后，用例按它断言）。"""
        return self._section.title_text()

    def section_hint(self) -> str:
        """抬头右边那句灰字说明（与 title 一起按 kinds / 形态传）。"""
        return self._section.hint_text()

    def picker(self) -> RowPicker | None:
        return self._picker

    def picker_table(self) -> str:
        """候选列表现在挑的是哪张表（没冒出来就是空串）。"""
        return self._picker_table

    def section(self) -> BenchSection:
        """这一块那个折叠栏（用例读"默认收起 / 展没展开"，装配方也可读）。"""
        return self._section

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _is_selected(self, line: StatLine) -> bool:
        return self._selected == (line.table, line.row_id)

    def _on_clicked(self, line: StatLine) -> None:
        self._selected = (line.table, line.row_id)
        for other, row in self._rows:
            row.set_selected(self._selected == (other.table, other.row_id))
        self.row_selected.emit(line.table, line.row_id)

    def clear_selection(self) -> None:
        """把这一块的高亮清掉（**不写库、不发信号**）。

        ❗ 一屏只有一个选中：页面收到**别的块**发来的 row_selected 时调它。
        拆块之后属性五栏各是一个控件、各记各的选中 —— 连点 ③④⑤⑥ 会同时亮四行
        （用户 2026-10-02 实机原话：「为什么我能同时选中四个？」）。
        """
        if self._selected == ("", 0):
            return
        self._selected = ("", 0)
        for _line, row in self._rows:
            row.set_selected(False)


#: 块内分组抬头那颗记号（与 bench_kit.BenchSection 同一对字形 —— **单占一格**，
#: 不并进灰字抬头里：抬头文字有用例按原话断言）。
GROUP_CARET_COLLAPSED = "▸"
GROUP_CARET_EXPANDED = "▾"

#: 分组抬头那一行的高度 —— 与别的行同一档（用户 2026-10-02：「改装槽的折叠栏太小了」）。
GROUP_HEAD_HEIGHT = TOKENS.size.row_height
#: 抬头图标位的高度 / **固定宽**（宽钉住：不同图标长宽比不同时槽名会左右抖）。
GROUP_ICON_HEIGHT = 18
GROUP_ICON_WIDTH = 24


def slot_icon_mark(context, modification_id: int) -> RowMark:
    """这个改装槽抬头那个**图标位**：真图 + 没图时的兜底 emoji（`RowMark` 的口径）。

    真图走 `row_icon(instance, "Modifications", mid)` —— 它是**唯一入口**
    （族名与键在 `core/assets/families.py` 里登记：`Modifications.Type == 5` 是装甲盾牌，
    其余落到通用齿轮那张）。⛔ 界面里**不许自己拼 family/key**。

    没实例（`context is None` / 还没打开单位）时 `row_icon` 给 None —— 那不算错：
    照 `RowMark` 的口径退回**表 emoji**（Modifications 是 🔧），别留空框。
    """
    instance = getattr(context, "instance", None) if context is not None else None
    return RowMark(
        icon=row_icon(instance, vm.MODIFICATIONS_TABLE, int(modification_id)),
        emoji=table_emoji(vm.MODIFICATIONS_TABLE),
    )


class _GroupHead(QWidget):
    """块内分组的抬头（**≥2 个槽**时点它 = 展开 / 收起这一组的选项行）。

    外观口径（Lead 2026-10-02，用户报「改装槽的折叠栏太小了」）—— 它是一条**正经的行**：

    - 高度 `TOKENS.size.row_height`（26），**整条可点** + hover（role `bench-group-head`）；
    - 槽名 = `bench-group-title`（`typography.size-md` + bold + `text-secondary`）——
      **比块抬头（`bench-section-title` = text-strong）弱一档**，层级一眼看得出来；
    - 记号 ▸/▾ **单占一格**（复用 `bench-section-caret`）；
    - 右边是「N 个选项」与那句说明（借来的槽…），role `entry-count`（muted/xs）；
    - **图标位**（用户 2026-10-02：「我记得改装槽是有它的图标的」）：真图走
      `row_icon(instance, "Modifications", mid)`（族名 / 键**不在这里拼**），没图才退回
      `RowMark.emoji`（表 emoji）—— 图标位只能有一个，两样都没有就整块藏起来。

    ❗ **行尾一颗 ✕**（2026-10-02 第十轮，用户：「增删整槽也加！」）= 删掉**整个槽**
    （`Modifications` 那一行 ＋ 级联它下面全部 `Options`）—— 与"选项行那颗 ✕ 删这一条"
    是两件事，两颗按钮的 tooltip 也分开写。那颗按钮自己吃掉点击，不会顺手把这一组折一下。

    `collapsible=False`（只有 1 个槽的块）时不给记号、点了也不动：一条不套折叠
    （用户口径：「只有一条的不套折叠，纯粹的画蛇添足」）。
    """

    clicked = Signal()
    #: 点了这颗 ✕（**删掉整个槽**）—— 由宿主接（谁接谁决定写什么）。
    remove_requested = Signal()
    #: 点了这颗 ＋（**给这一个槽**加一条选项）—— 由宿主接。
    #: ❗ 用户 2026-10-02：「加选项是做在画红圈的这个地方（槽行上），不要放在原来的地方，
    #:    不然我怎么知道是给哪个槽加选项？」—— 表头那颗 ＋ 已删，改成一槽一颗。
    add_requested = Signal()
    #: 点了这颗 ⧉（**照这一整个槽复制一份**：`Modifications` ＋ 它名下的全部 `Options`）。
    copy_requested = Signal()
    #: 点了行体 —— 右栏画这条 `Modifications`（"能看见就要能改"，用户 2026-10-02 的原话：
    #: 「既然我能看到子炮塔，那我为什么不能改？」）。与 `clicked`（折一下）**两件事**。
    select_requested = Signal()

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-group-head")
        # ❗ 有背景色的 QWidget 必须开 WA_StyledBackground，否则 QSS 的 hover 底画不出来
        #    （bench_kit._SectionHead 那一条同一个道理）。
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(GROUP_HEAD_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(TOKENS.spacing.sm, 0, TOKENS.spacing.sm, 0)
        row.setSpacing(TOKENS.spacing.xs)
        self._caret = QLabel(GROUP_CARET_COLLAPSED)
        self._caret.setProperty("role", "bench-section-caret")
        # 图标位：真图（IconLabel 走 prepare_icon：族翻转 + DPR）与 emoji 兜底同位，
        # 同时只显示一个（`RowMark` 的口径：图标位只能有一个）。
        self._icon = IconLabel(GROUP_ICON_HEIGHT, width=GROUP_ICON_WIDTH)
        self._emoji = QLabel("")
        self._emoji.setProperty("role", "bench-icon")
        self._emoji.setFixedSize(GROUP_ICON_WIDTH, GROUP_ICON_HEIGHT)
        self._emoji.setAlignment(Qt.AlignCenter)
        self._title = QLabel(title)
        self._title.setProperty("role", "bench-group-title")
        self._note = QLabel("")
        self._note.setProperty("role", "entry-count")
        self._count = QLabel("")
        self._count.setProperty("role", "entry-count")
        # ❗ 用户 2026-10-02：「左卡不加，但增删整槽也加！」—— 槽抬头这一行也要能
        #    **删掉整个槽**（不是删某一条选项：那是选项行自己那颗 ✕）。同一个 role。
        self._remove = BenchAction("✕", tooltip="删掉整个槽（连它下面的选项一起）")
        self._remove.setProperty("role", "bench-row-tool")
        self._remove.clicked.connect(self.remove_requested)
        # ❗ 一槽一颗「＋」：它只给**这一行这个槽**加选项（表头那颗说不清是给谁加，已删）。
        #    摆法与 bench_kit.BenchRow 的工具同一处：**在 ✕ 左边**。
        self._add = BenchAction("＋", tooltip="给这个槽加一条选项（Options 一行）")
        self._add.setProperty("role", "bench-row-tool")
        self._add.clicked.connect(self.add_requested)
        # ❗ 用户 2026-10-02：「改装槽这里的 Options 也要能复制，也要能改」—— 槽行也要一颗 ⧉
        #    （复制 = **整个槽** ＋ 它下面的选项一起）。摆法与 bench_kit.BenchRow 一致：在 ✕ 左边。
        self._copy = BenchAction("⧉", tooltip="照这一个槽复制一份（整槽 ＋ 它下面的选项一起）")
        self._copy.setProperty("role", "bench-row-tool")
        self._copy.clicked.connect(self.copy_requested)
        row.addWidget(self._caret, 0, Qt.AlignVCenter)
        row.addWidget(self._icon, 0, Qt.AlignVCenter)
        row.addWidget(self._emoji, 0, Qt.AlignVCenter)
        row.addWidget(self._title, 0, Qt.AlignVCenter)
        # 说明那一格吃掉余量 -> 「N 个选项」永远贴右（设计稿的口径没变）。
        row.addWidget(self._note, 1, Qt.AlignVCenter)
        row.addWidget(self._count, 0, Qt.AlignVCenter)
        row.addWidget(self._add, 0, Qt.AlignVCenter)
        row.addWidget(self._copy, 0, Qt.AlignVCenter)
        row.addWidget(self._remove, 0, Qt.AlignVCenter)
        self._collapsible = False
        self._caret.setVisible(False)
        self._store = None
        self._store_hooked = False
        self._mark: RowMark | None = None
        self.refresh_icon()

    # ---- 折叠 ---------------------------------------------------------- #

    def set_collapsible(self, collapsible: bool) -> None:
        """这一条抬头能不能折（False = 不给记号、点了也不动）。"""
        self._collapsible = bool(collapsible)
        self._caret.setVisible(self._collapsible)
        self.setCursor(Qt.PointingHandCursor if self._collapsible else Qt.ArrowCursor)

    def collapsible(self) -> bool:
        return self._collapsible

    def set_collapsed(self, collapsed: bool) -> None:
        """换记号那一个字（收起 / 展开的**视觉**口径与 bench_kit.BenchSection 一致）。"""
        self._caret.setText(GROUP_CARET_COLLAPSED if collapsed else GROUP_CARET_EXPANDED)
        # ❌ 中栏不许挂 tooltip（用户 2026-10-03）—— 注释由右栏承担。

    def set_group_text(self, title: str, count: str = "", note: str = "") -> None:
        """就地换抬头三段（槽名 / 「N 个选项」/ 那句说明）—— 一个控件都不重建。

        ❗ 三段**分三格**：槽名那一格是 `bench-group-title`（md + bold + text-secondary），
        右边那两句是 `entry-count`（muted/xs）。拼成一句的话，那句「借来的槽…」会被
        染成和槽名一样重（它只是说明）。
        """
        self._title.setText(title)
        self._count.setText(count)
        self._note.setText(note)

    # ---- 图标位（真图 or emoji 兜底，**只能有一个**）-------------------- #

    def set_mark(self, mark: RowMark | None) -> None:
        """只换图标位那一份（`RowMark`）：就地重画，**一个控件都不重建**。

        槽的 `Modifications.Type` 被改掉时也走它 —— 盾牌 ↔ 齿轮要当场跟着走。
        """
        self._mark = mark
        self.refresh_icon()

    def set_icons(self, store) -> None:
        """接一份图标缓存（`SlotsBench.set_icons` 往下发）—— 就地换图，不重建。

        图是**异步**解析的（`store.loaded` 到了才拿得到）：这里自己接一次 `loaded`，
        到了再判"真图还是 emoji 兜底" —— `IconLabel` 只重画它自己的 pixmap，
        它不知道旁边还有个兜底位要跟着切。
        """
        self._store = store
        if store is not None and not self._store_hooked:
            loaded = getattr(store, "loaded", None)
            if loaded is not None:
                loaded.connect(self.refresh_icon)
                self._store_hooked = True
        self.refresh_icon()

    def refresh_icon(self) -> None:
        """重画图标位：有真图 -> 画真图；没真图 -> 画 `RowMark.emoji`；都没有 -> 藏起来。

        ❗ 取图只经 `IconLabel.set_source`（内部走 `prepare_icon`：族翻转 + DPR；
        `bench_kit` 那条口径），这里**不碰** `cache/icons` 下的 PNG。
        """
        mark = self._mark
        icon = mark.icon if mark is not None else None
        self._icon.set_source(
            self._store,
            icon.family if icon is not None else "",
            icon.key if icon is not None else "",
        )
        if self._icon.has_icon():
            self._icon.setVisible(True)
            self._emoji.setVisible(False)
            return
        fallback = mark.emoji if mark is not None else ""
        self._icon.setVisible(False)
        self._emoji.setText(fallback)
        self._emoji.setVisible(bool(fallback))

    # ---- 读数（用例钉控件里那一份状态，不现算）------------------------- #

    def title_label(self) -> QLabel:
        return self._title

    def note_label(self) -> QLabel:
        return self._note

    def count_label(self) -> QLabel:
        """右边那句「N 个选项」（用户口径：右边照旧写它）。"""
        return self._count

    def caret_label(self) -> QLabel:
        return self._caret

    def icon_label(self) -> IconLabel:
        """图标位上那个真图位。"""
        return self._icon

    def emoji_label(self) -> QLabel:
        """图标位上的 emoji 兜底位（与真图同位、同时只显示一个）。"""
        return self._emoji

    def icon_family(self) -> str:
        return self._icon.family

    def icon_key(self) -> str:
        return self._icon.key

    def has_real_icon(self) -> bool:
        """图标位上画的是**真图**吗（False = 正在用 emoji 兜底 / 两样都没有）。"""
        return self._icon.has_icon()

    def mark_emoji(self) -> str:
        """这一格登记的兜底 emoji（`RowMark.emoji`）。"""
        return self._mark.emoji if self._mark is not None else ""

    def mark_visible(self) -> bool:
        """图标位上真的有东西吗（真图 or emoji）。

        ❗ **不读 isVisible()** —— 窗口没 show 时整棵树恒 False（用例与冒烟就是那么跑的）。
        """
        return self._icon.has_icon() or bool(self._emoji.text())

    def remove_button(self) -> BenchAction:
        """这一行那颗 ✕（**删掉整个槽**）—— 读数 + 用例点它。"""
        return self._remove

    def add_button(self) -> BenchAction:
        """这一行那颗 ＋（**给这个槽**加一条选项）—— 读数 + 用例点它。"""
        return self._add

    def copy_button(self) -> BenchAction:
        """这一行那颗 ⧉（**照这一整个槽复制一份**）—— 读数 + 用例点它。"""
        return self._copy

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            # ❗ 点抬头 = 右栏画这条 `Modifications`（"能看见就要能改"）；能折的那一种**顺手**
            #    折一下 —— 折的口径一个字节都没动（不可折的块从前点了什么都不做，现在至少能选中）。
            self.select_requested.emit()
            if self._collapsible:
                self.clicked.emit()
        super().mouseReleaseEvent(event)


class SlotRow(QFrame):
    """折叠态的一行 = 一个槽：图标 │ 槽名 │ #槽Id │ N 选 1 · 当前项 · 效果 │ 费用 │
    展开候选 │ ＋ ⧉ ✕。

    出处：设计图 docs/设计稿-编成台-飞机.png 的 ②（它自己标了那句 ⚠️：31 个选项铺不下，
    所以一行一个槽、只显示当前选中项）。

    ❗ role 全部复用 qss 里已有的那几条（bench-row / bench-row-name / entry-id /
    bench-row-detail / bench-row-cost / bench-row-tool）—— 一个新 role 都不加。
    `selected=true` 在这一行上的意思是**候选正展开着**（这一行现在高亮）；
    "这个槽选中的是哪一项"由中间那句 ● 名字 表达，两件事别混。

    ❗ **行尾那颗 ✕**（2026-10-02 第十轮）= 删掉**整个槽**（`Modifications` ＋ 它下面的
    全部 `Options`），不是删某一条选项 —— 与非折叠态 `_GroupHead` 那颗同一件事。
    它自己吃掉点击，点它**不会**顺手把这一槽展开 / 收起（`activated` 不发）。
    """

    #: 点了行体（= 展开 / 收起候选，与那颗按钮同一件事）。
    activated = Signal()
    #: 点了「展开候选 / 收起候选」那颗按钮。
    toggle_requested = Signal()
    #: 点了行尾那颗 ✕（**删掉整个槽**：`Modifications` 那一行 ＋ 它下面的全部选项）。
    remove_requested = Signal()
    #: 点了行尾那颗 ＋（**给这一个槽**加一条选项）。
    add_requested = Signal()
    #: 点了行尾那颗 ⧉（**照这一整个槽复制一份**：`Modifications` ＋ 它名下的全部 `Options`）。
    copy_requested = Signal()
    #: 点了行体**顺手**发一次 —— 右栏画这条 `Modifications`（"能看见就要能改"）。
    #: 与 `activated`（展开 / 收起候选）**两件事**，点一下两样都发。
    select_requested = Signal()

    def __init__(
        self,
        title: str = "",
        *,
        id_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "bench-row")
        self.setProperty("selected", "false")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        line = QHBoxLayout(self)
        line.setContentsMargins(
            TOKENS.spacing.sm, TOKENS.spacing.xs, TOKENS.spacing.sm, TOKENS.spacing.xs
        )
        line.setSpacing(TOKENS.spacing.sm)

        self._icon = IconLabel(18)
        self._name = QLabel(title)
        self._name.setProperty("role", "bench-row-name")
        self._name.setMinimumWidth(SLOT_TITLE_MIN_WIDTH)
        # 槽那一行的 Id（#308）—— 紧跟槽名后面，与只读卡 `_option_group` 同一处摆法。
        self._id = QLabel(id_text)
        self._id.setProperty("role", "entry-id")
        self._detail = QLabel("")
        self._detail.setProperty("role", "bench-row-detail")
        self._detail.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._cost = QLabel("")
        self._cost.setProperty("role", "bench-row-cost")
        self._cost.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._toggle = BenchAction(
            EXPAND_TEXT, tooltip="就地展开这个槽的候选（不弹窗；再点一下收起）"
        )
        self._toggle.setProperty("role", "bench-row-tool")
        self._toggle.clicked.connect(self.toggle_requested)
        # ❗ 折叠态这一行也要有"删掉整个槽"（与非折叠态的 `_GroupHead` 那颗同一件事）——
        #    两个行类各一颗，别让折叠态的槽没得删。
        self._remove = BenchAction("✕", tooltip="删掉整个槽（连它下面的选项一起）")
        self._remove.setProperty("role", "bench-row-tool")
        self._remove.clicked.connect(self.remove_requested)
        # ❗ 一槽一颗「＋」（与非折叠态 `_GroupHead` 那颗同一件事）：给**这一行这个槽**加选项。
        self._add = BenchAction("＋", tooltip="给这个槽加一条选项（Options 一行）")
        self._add.setProperty("role", "bench-row-tool")
        self._add.clicked.connect(self.add_requested)
        # ❗ 用户 2026-10-02：「改装槽这里的 Options 也要能复制，也要能改」—— 折叠态这一行
        #    也要一颗 ⧉（与非折叠态 `_GroupHead` 那颗同一件事），摆法照旧：在 ✕ 左边。
        self._copy = BenchAction("⧉", tooltip="照这一个槽复制一份（整槽 ＋ 它下面的选项一起）")
        self._copy.setProperty("role", "bench-row-tool")
        self._copy.clicked.connect(self.copy_requested)
        line.addWidget(self._icon, 0, Qt.AlignVCenter)
        line.addWidget(self._name, 0, Qt.AlignVCenter)
        line.addWidget(self._id, 0, Qt.AlignVCenter)
        line.addWidget(self._detail, 1, Qt.AlignVCenter)
        line.addWidget(self._cost, 0, Qt.AlignVCenter)
        line.addWidget(self._toggle, 0, Qt.AlignVCenter)
        line.addWidget(self._add, 0, Qt.AlignVCenter)
        line.addWidget(self._copy, 0, Qt.AlignVCenter)
        line.addWidget(self._remove, 0, Qt.AlignVCenter)
        self.setFixedHeight(TOKENS.size.row_height + 2)

    # ---- 内容（就地改字：reload_values 走这几条）------------------------ #

    def set_title(self, text: str) -> None:
        self._name.setText(text)

    def set_id_text(self, text: str) -> None:
        self._id.setText(text)

    def set_detail(self, text: str) -> None:
        self._detail.setText(text)

    def set_cost(self, text: str) -> None:
        self._cost.setText(text)

    def set_toggle_text(self, text: str) -> None:
        self._toggle.setText(text)

    def set_icon(self, store, family: str, key: str) -> None:
        """图标唯一入口（族 + 键 -> store；翻转与 DPR 在 prepare_icon 里，界面不自己翻）。"""
        self._icon.set_source(store, family, key)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        restyle(self)

    @property
    def selected(self) -> bool:
        return self.property("selected") == "true"

    # ---- 只点行本身（不含两颗按钮）------------------------------------- #

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            # ❗ 点行体发**两条**：右栏画这条 `Modifications`（"能看见就要能改"）
            #    ＋ 展开 / 收起候选（从前的口径，一个字节没动）。
            self.select_requested.emit()
            self.activated.emit()
        super().mouseReleaseEvent(event)

    # ---- 读数（用例钉控件里那一份状态，不现算）------------------------- #

    def title_label(self) -> QLabel:
        return self._name

    def id_label(self) -> QLabel:
        return self._id

    def detail_label(self) -> QLabel:
        return self._detail

    def cost_label(self) -> QLabel:
        return self._cost

    def icon_label(self) -> IconLabel:
        return self._icon

    def toggle_button(self) -> BenchAction:
        """那一颗「▸ 展开候选 / ▾ 收起候选」（读数 + 用例点它）。"""
        return self._toggle

    def remove_button(self) -> BenchAction:
        """行尾那颗 ✕（**删掉整个槽**）—— 读数 + 用例点它。"""
        return self._remove

    def add_button(self) -> BenchAction:
        """行尾那颗 ＋（**给这个槽**加一条选项）—— 读数 + 用例点它。"""
        return self._add

    def copy_button(self) -> BenchAction:
        """行尾那颗 ⧉（**照这一整个槽复制一份**）—— 读数 + 用例点它。"""
        return self._copy


class SlotsBench(QWidget):
    """④ 改装槽（槽内互斥）：一个槽一组，点一项 = 选中它。

    ⚠️ **借来的槽一个字节都不许写**：那是变体单位借槽主人的槽（645 借 108 的 743），
    在它上面翻 Options.IsDefault 会一起改到槽的主人。点它只发 row_selected。
    """

    #: (表名, 行Id) —— 选项行（Options#Id）**或**槽那一行（Modifications#Id：
    #: 点槽行 / 抬头时发的，"能看见就要能改"）
    row_selected = Signal(str, int)
    #: **页面**选中的那一行（一屏只有一个高亮）—— 与 `Options.IsDefault` **两件事**：
    #: 谁生效由名字前那颗 ● / ○ 表达，整行高亮只留给"右栏正在画的那一行"。
    #: ❗ 用户 2026-10-02：「我为什么又一次性选中了这么多？」—— 三个挂载组各有 1 条生效项，
    #:   从前那三条都整行高亮，看着就像"一次选了 3 行"。
    _selected: tuple[str, int] = ("", 0)
    #: 「去看 Options 那一行的全部列」
    #: 一句人话（绝不弹窗）
    status_message = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        collapsed: bool = False,
        title: str = SLOTS_TITLE,
        hint: str = SLOTS_HINT,
    ) -> None:
        """collapsed=True = 折叠态（飞机：一行一个槽、只显示当前选中项，候选就地展开）。

        ❗ **关键字参数、默认 False** —— 默认值决定步兵 / 载具那一块的行为：不传就是
        "每个槽一行抬头 ＋ 该槽全部选项逐行列出"，与加这一手之前**逐字一致**
        （现有 tests/test_bench_slots.py 一条都不改）。

        抬头的编号按**形态**不同（设计图定：载具 / 飞机把它编成 **②**，步兵才是 **④**）——
        摆在位置 ② 却写着「④ 改装槽」会误导用户，所以装配方（bench_page）按形态传
        title / hint：步兵 = 默认值 SLOTS_TITLE / SLOTS_HINT，载具 / 飞机 = 「② 改装槽…」。
        不传 = 步兵那一份，一个字都没动。
        """
        super().__init__(parent)
        self._context = None
        self._icons = None
        #: 折叠态开关（装载方 = bench_page：载具 / 飞机传 True，步兵不传）。
        self._collapsed = bool(collapsed)
        #: 抬头那两段字（编号按形态：载具 / 飞机 ②，步兵 ④）—— 见 __init__ 的说明。
        self._section_title = str(title)
        self._section_hint = str(hint)
        self._groups: tuple[SlotGroupView, ...] = ()
        self._rows: list[tuple[int, SlotOptionView, BenchRow]] = []
        #: 每个槽那一条灰字抬头（_GroupHead）—— reload_values 就地改它的字。
        self._heads: list[tuple[int, _GroupHead]] = []
        #: **非折叠态 + ≥2 个槽**：分组抬头 / 该组选项的宿主（展开收起 = 切可见性）。
        self._group_heads: dict[int, _GroupHead] = {}
        self._group_bodies: dict[int, QWidget] = {}
        #: **非折叠态 + ≥2 个槽**：现在展开着的分组。跨 refresh **与换单位**都留着
        #: （关系树那条口径：「以后每次重画都恢复用户当时的展开态」）。
        self._open_groups: set[int] = set()
        #: **折叠态**：槽那一行（modification_id -> SlotRow）—— reload_values 就地改它的字。
        self._slot_rows: dict[int, SlotRow] = {}
        #: **折叠态**：候选的宿主控件与它的布局（展开 / 收起 = 切这两个的可见性）。
        self._candidate_hosts: dict[int, QWidget] = {}
        self._candidate_layouts: dict[int, QVBoxLayout] = {}
        #: **折叠态**：候选建过没有 —— 第一次展开才建（F-15E 31 个选项全建 = 等于没折叠）。
        self._candidate_built: set[int] = set()
        #: **折叠态**：现在展开着的槽。跨 refresh 留着，别把用户展开的那一槽吃掉。
        self._open_slots: set[int] = set()
        #: 「＋ 加选项」加到哪个槽：用户最后一次点的那个（没有就第一个）。
        self._focus_slot = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOKENS.spacing.xs)
        self._section = BenchSection(self._section_title, self._section_hint)
        # ❗ **表头不再有「＋ 加选项」**（用户 2026-10-02：「加选项是做在画红圈的这个地方
        #    （槽行上），不要放在原来的地方，不然我怎么知道是给哪个槽加选项？」）
        #    —— 改成一槽一颗 ＋，见两个行类的 `add_requested` 与 `_build_*` 里的接线。
        self._add_slot = self._section.add_action(
            "＋ 加一个槽", tooltip="给这个单位加一个空的改装槽（再用槽行上那颗 ＋ 往里加选项）"
        )
        self._add_slot.clicked.connect(lambda _=False: self.add_slot())
        root.addWidget(self._section)

    # ------------------------------------------------------------------ #
    # Lead 定的装配接口
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上 `EditorContext`（None = 清空）。"""
        self._context = context
        self._focus_slot = 0
        # 换单位 = 换了一套槽 Id：展开态跟着清掉（旧 Id 在新单位上是另一个槽）。
        self._open_slots.clear()
        self.refresh()

    def refresh(self) -> None:
        """按当前数据重画（幂等）。选中态跟着数据走：写完 IsDefault 调它一次就换过来了。

        折叠态（`collapsed=True`）走另一条路：每个槽只有一行（`_build_collapsed_group`），
        候选等第一次展开才建；展开态跨 refresh 留着（`_open_slots`）。
        """
        self._section.clear_body()
        self._rows.clear()
        self._heads.clear()
        self._slot_rows.clear()
        self._candidate_hosts.clear()
        self._candidate_layouts.clear()
        self._candidate_built.clear()
        self._group_heads.clear()
        self._group_bodies.clear()
        context = self._context
        if context is None:
            self._groups = ()
            self._section.add_widget(BenchEmpty("（还没有选中单位）"))
            return
        self._groups = vm.slot_views(context.data, context.unit_id)
        if not self._groups:
            self._section.add_widget(BenchEmpty("（这个单位没有改装槽）"))
            return
        if self._collapsed:
            for group in self._groups:
                self._build_collapsed_group(group)
            return
        # ❗ ≥2 个槽才折（用户口径：「只有一条的不套折叠，纯粹的画蛇添足」）——
        #    只 1 个槽时就是原来那条平铺的灰字抬头，记号都不给。
        folding = self.groups_are_folded()
        for group in self._groups:
            self._build_group(group, folding=folding)

    def reload_values(self) -> None:
        """**就地**把值换成最新的 —— 一个控件都不重建（用户提过几回的那条）。

        覆盖这一块显示的全部字段：● 选中态、费用、选项短名与"效果一句话"、槽名那一行灰字。
        点一项之后就走这一条（写完 `IsDefault` 值变了、行没变）—— 从前那一手是 refresh()，
        整块重建，用户看到的就是闪一下。

        结构真变了（加了 / 删了选项行）本该由外层判署名后调 `refresh()`；这里兜一道：
        行集合对不上就退回 refresh()，不然值会写到**错行**上。

        折叠态走 `_reload_values_collapsed()`（槽那一行 + 已建出来的候选行就地改字）——
        下面这一段一行都不动，就是折叠态没开时的那条路。
        """
        if self._collapsed:
            self._reload_values_collapsed()
            return
        context = self._context
        if context is None or not self._rows:
            return
        groups = vm.slot_views(context.data, context.unit_id)
        keys_after = [
            (group.modification_id, option.option_id)
            for group in groups
            for option in group.options
        ]
        if keys_after != [(mid, option.option_id) for mid, option, _row in self._rows]:
            self.refresh()
            return
        self._groups = groups
        for modification_id, head in self._heads:
            group = self._group(modification_id)
            if group is not None:
                head.set_group_text(*self._group_parts(group))
                # 槽的 Type 也可能刚被改（盾牌 ↔ 齿轮）—— 图标位跟着走，就地重画。
                head.set_mark(slot_icon_mark(context, modification_id))
        widgets = [row for _mid, _option, row in self._rows]
        self._rows = []
        for (modification_id, option), row in zip(
            [(group.modification_id, option) for group in groups for option in group.options],
            widgets,
            strict=True,
        ):
            row.set_name(f"{MARK_ON if option.selected else MARK_OFF} {option.name}")
            row.set_detail(option.detail)
            row.set_value(option.cost_text)
            row.set_icon(self._icons, OPTION_PICTURE_FAMILY, option.icon)
            row.set_selected(self._selected == (vm.OPTIONS_TABLE, option.option_id))
            self._rows.append((modification_id, option, row))

    def set_icons(self, store) -> None:
        """接一份图标缓存（族 = `option_pictures`：`Options.OptionPicture` 那一族）。

        已经有行了就**就地换图**（reload_values），别再整体重建 —— 图标晚一步到
        （`shared_icon_cache()` 是异步的）不该让中栏闪一下。
        """
        self._icons = store
        # 非折叠态那几条**分组抬头**上的图标也要换（就地换图，不重建 —— 收起态也看得见它）。
        for _mid, head in self._heads:
            head.set_icons(store)
        # 折叠态下 `_rows` 可能是空的（候选还没展开过）—— 这时要靠 `_slot_rows` 判
        # "有没有已经画出来的东西"，否则每来一批图就整块重建一次（用户看到的就是闪一下）。
        if self._rows or self._slot_rows:
            self.reload_values()
        else:
            self.refresh()

    # ------------------------------------------------------------------ #
    # 折叠态（飞机）：一行一个槽 + 候选就地展开
    # ------------------------------------------------------------------ #

    def is_collapsed(self) -> bool:
        """这一块是不是折叠态（装载方 bench_page 对载具 / 飞机传 collapsed=True）。"""
        return self._collapsed

    @property
    def collapsed(self) -> bool:
        """与 `is_collapsed()` 同一件事的属性读法（用例两种写法都认）。"""
        return self._collapsed

    def is_expanded(self, modification_id: int) -> bool:
        """这个槽的候选现在展开着吗（非折叠态恒为 False —— 那一态本来就全列着）。"""
        return self._collapsed and int(modification_id) in self._open_slots

    def expanded_slots(self) -> tuple[int, ...]:
        """现在展开着的那几个槽（顺序 = **界面上**的槽序，不是 Id 序）。"""
        return tuple(
            group.modification_id
            for group in self._groups
            if group.modification_id in self._open_slots
        )

    def toggle_slot(self, modification_id: int) -> bool:
        """展开 / 收起某个槽的候选，返回展开后的状态。**就地，不重建、不弹窗**。

        - 非折叠态什么都不做（返回 False）：那一态本来就全列着，没有"展开"这一说；
        - **第一次展开才建**候选那几行（F-15E 5 个槽 31 个选项，全建就等于没折叠）；
          建完就留着，再展开 / 收起只切可见性 —— 一个控件都不重建（AGENTS §0.7）；
        - 展开顺手把这个槽记成「＋ 加选项」的焦点（用户正在看它）。
        """
        if not self._collapsed:
            return False
        mid = int(modification_id)
        group = self._group(mid)
        host = self._candidate_hosts.get(mid)
        if group is None or host is None:
            return False
        will_open = mid not in self._open_slots
        if will_open:
            self._open_slots.add(mid)
            self._focus_slot = mid
            self._ensure_candidates(group)
        else:
            self._open_slots.discard(mid)
        host.setVisible(will_open)
        row = self._slot_rows.get(mid)
        if row is not None:
            # 展开态由最左边那颗 ⌄ / ⌃ 与**下面真的多出来那几行**表达，不再整行高亮
            # （高亮只留给页面选中的那一行）。
            row.set_toggle_text(self._toggle_text(mid))
        return will_open

    def expand_slot(self, modification_id: int) -> bool:
        """只展开（已经展开就给 True，不重复干活）。"""
        if self.is_expanded(modification_id):
            return True
        return self.toggle_slot(modification_id)

    def collapse_slot(self, modification_id: int) -> bool:
        """只收起（本来就没展开就给 False）。"""
        if not self.is_expanded(modification_id):
            return False
        return not self.toggle_slot(modification_id)

    def expand_button(self, modification_id: int = 0) -> BenchAction | None:
        """那一颗「▸ 展开候选」：给了槽 Id 就取那一个，没给（0）取**第一个槽**的。"""
        mid = int(modification_id) or (self._groups[0].modification_id if self._groups else 0)
        row = self._slot_rows.get(mid)
        return row.toggle_button() if row is not None else None

    def expand_buttons(self) -> tuple[BenchAction, ...]:
        """全部槽的「展开候选」按钮（顺序 = 界面上的槽序）。"""
        return tuple(
            self._slot_rows[group.modification_id].toggle_button()
            for group in self._groups
            if group.modification_id in self._slot_rows
        )

    def slot_rows(self) -> tuple[SlotRow, ...]:
        """折叠态那几行（一行一个槽，顺序 = 界面上的槽序）；非折叠态是空元组。"""
        return tuple(
            self._slot_rows[group.modification_id]
            for group in self._groups
            if group.modification_id in self._slot_rows
        )

    def slot_row(self, modification_id: int) -> SlotRow | None:
        """某个槽那一行（读数 / 用例点它）。"""
        return self._slot_rows.get(int(modification_id))

    def candidate_rows(self, modification_id: int) -> tuple[BenchRow, ...]:
        """这个槽**已经建出来**的候选行（没展开过 = 空元组 —— 那几行还没建）。"""
        mid = int(modification_id)
        return tuple(row for group_mid, _option, row in self._rows if group_mid == mid)

    # ------------------------------------------------------------------ #
    # 块内分组折叠（非折叠态 + ≥2 个槽）：每个槽那一条灰字抬头可点
    # ------------------------------------------------------------------ #

    def groups_are_folded(self) -> bool:
        """这一块**块内**要不要折：非折叠态 ＋ **≥ 2 个槽**。

        ❗ 1 个槽不折 —— 用户既有口径：「只有一条的不套折叠，纯粹的画蛇添足」。
        飞机那套（`collapsed=True`）也不走这条：它的展开语义是 `_open_slots`，
        两者一个字节都不许混。
        """
        return not self._collapsed and len(self._groups) >= 2

    def is_group_expanded(self, modification_id: int) -> bool:
        """这个槽那几行选项现在展开着吗。

        ❗ 判据要连"**这台车上有没有这个槽**"一起看：`_open_groups` 跨换单位留着，
        换到另一台车之后旧槽 Id 还在集合里 —— 只查集合会把"车上没这个槽"答成"展开着"。
        """
        mid = int(modification_id)
        return self.groups_are_folded() and mid in self._group_bodies and mid in self._open_groups

    def expanded_groups(self) -> tuple[int, ...]:
        """现在展开着的那几个槽（顺序 = **界面上**的槽序，不是 Id 序）。

        ❗ 不折的块恒给空元组：此刻屏幕上根本没有"展不展开"这回事（`_open_groups` 是
        跨 refresh / 换单位留着的记忆，不折的块不该拿它当读数）。
        """
        if not self.groups_are_folded():
            return ()
        return tuple(
            group.modification_id
            for group in self._groups
            if group.modification_id in self._open_groups
        )

    def toggle_group(self, modification_id: int) -> bool:
        """展开 / 收起某个槽那几行选项，返回展开后的状态。**就地，不重建、不弹窗**。

        - 不折的块（只有 1 个槽 / 飞机那套 collapsed）：什么都不做，返回 False；
        - 只切可见性 —— 一个控件都不重建（AGENTS §0.7）；
        - 展开态记在 `_open_groups` 里，跨 refresh **与换单位**都留着。
        """
        if not self.groups_are_folded():
            return False
        mid = int(modification_id)
        body = self._group_bodies.get(mid)
        if body is None:
            return False
        will_open = mid not in self._open_groups
        if will_open:
            self._open_groups.add(mid)
        else:
            self._open_groups.discard(mid)
        body.setVisible(will_open)
        head = self._group_heads.get(mid)
        if head is not None:
            head.set_collapsed(not will_open)
        return will_open

    def expand_group(self, modification_id: int) -> bool:
        """只展开（已经展开就给 True，不重复干活）。"""
        if self.is_group_expanded(modification_id):
            return True
        return self.toggle_group(modification_id)

    def collapse_group(self, modification_id: int) -> bool:
        """只收起（本来就没展开就给 False）。"""
        if not self.is_group_expanded(modification_id):
            return False
        return not self.toggle_group(modification_id)

    def group_head(self, modification_id: int) -> _GroupHead | None:
        """某个槽那一条灰字抬头（只 1 个槽的块也有 —— 只是不可折、不带记号）。"""
        return self._group_heads.get(int(modification_id))

    def group_body(self, modification_id: int) -> QWidget | None:
        """某个槽那几行选项的宿主（不折的块 / 没这个槽 = None）。"""
        return self._group_bodies.get(int(modification_id))

    def _build_group(self, group: SlotGroupView, *, folding: bool) -> None:
        """非折叠态的一个槽：灰字抬头 ＋ 该槽的选项行。

        `folding=False`（非折叠态只有 1 个槽）= 加折叠这一手之前那一态**逐字一致**：
        抬头平铺、选项行紧接着一行行铺出来，一个记号都不加。

        `folding=True`：抬头 ＋ 选项宿主装在同一个透明容器里；宿主**默认收起**，
        点抬头才 `setVisible(True)` —— 选项行在 refresh 时就建好了（收起 ≠ 不建），
        于是展开 / 收起**一个控件都不重建**（AGENTS §0.7）。
        """
        mid = group.modification_id
        head = _GroupHead(group.title)
        head.set_group_text(*self._group_parts(group))
        # ✕ = 删掉**整个槽**（`Modifications` 那一行 ＋ 级联它下面的全部 Options）。
        head.remove_requested.connect(lambda target=mid: self.remove_slot(target))
        # 槽行上那颗 ＋ = **只给这一个槽**加选项（槽号是闭包变量，不靠"焦点槽"猜）。
        head.add_requested.connect(lambda target=mid: self.add_option(target))
        # ⧉ = 照**这一整个槽**复制一份（`Modifications` ＋ 它下面的 `Options`，一条撤销）。
        head.copy_requested.connect(lambda target=mid: self.copy_slot(target))
        # 点抬头 = 右栏画这条 `Modifications`（"能看见就要能改"，与折一下两件事）。
        head.select_requested.connect(lambda target=mid: self.select_slot(target))
        # 图标位 = 游戏里那张改装按钮图（盾牌 / 齿轮），没图才退回表 emoji。
        head.set_mark(slot_icon_mark(self._context, mid))
        head.set_icons(self._icons)
        self._group_heads[mid] = head
        self._heads.append((mid, head))
        if not folding:
            self._section.add_widget(head)
            for option in group.options:
                row = self._build_option_row(group, option)
                self._section.add_widget(row)
                self._rows.append((mid, option, row))
            return
        head.set_collapsible(True)
        head.clicked.connect(lambda target=mid: self.toggle_group(target))
        container = QWidget()
        # 透明容器：这一条 role 在 qss 里就是「必须透明」（否则整块被刷成 bg-base）。
        container.setProperty("role", "bench-body")
        box = QVBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(head)
        body = QWidget()
        body.setProperty("role", "bench-body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        # ❗ 先挂进布局（拿到父控件）再谈可见性 —— 反过来隐藏控件会被映射成顶层小窗。
        box.addWidget(body)
        self._section.add_widget(container)
        self._group_bodies[mid] = body
        for option in group.options:
            row = self._build_option_row(group, option)
            body_layout.addWidget(row)
            self._rows.append((mid, option, row))
        open_now = mid in self._open_groups
        body.setVisible(open_now)
        head.set_collapsed(not open_now)

    def _build_collapsed_group(self, group: SlotGroupView) -> None:
        """折叠态的一个槽：槽那一行 ＋ 候选宿主（候选等第一次展开才建）。"""
        container = QWidget()
        # 透明容器：这一条 role 在 qss 里就是「必须透明」（否则整块被刷成 bg-base）。
        container.setProperty("role", "bench-body")
        box = QVBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        mid = group.modification_id
        row = SlotRow(group.title, id_text=id_badge_text(mid))
        row.activated.connect(lambda target=mid: self.toggle_slot(target))
        row.toggle_requested.connect(lambda target=mid: self.toggle_slot(target))
        row.remove_requested.connect(lambda target=mid: self.remove_slot(target))
        row.add_requested.connect(lambda target=mid: self.add_option(target))
        # 与非折叠态那两颗同一件事（两个行类各一颗，别让折叠态的槽没得复制 / 没得选）。
        row.copy_requested.connect(lambda target=mid: self.copy_slot(target))
        row.select_requested.connect(lambda target=mid: self.select_slot(target))
        box.addWidget(row)

        host = QWidget()
        host.setProperty("role", "bench-body")
        host_layout = QVBoxLayout(host)
        # 候选往里缩一格：一眼看出这几行属于上面那个槽。
        host_layout.setContentsMargins(TOKENS.spacing.lg, 0, 0, 0)
        host_layout.setSpacing(0)
        # ❗ 先挂进布局（拿到父控件）再谈可见性 —— 反过来隐藏控件会被映射成顶层小窗。
        box.addWidget(host)
        self._section.add_widget(container)

        self._slot_rows[mid] = row
        self._candidate_hosts[mid] = host
        self._candidate_layouts[mid] = host_layout
        self._apply_slot_row(row, group)
        open_now = mid in self._open_slots
        if open_now:
            self._ensure_candidates(group)
        host.setVisible(open_now)

    def _ensure_candidates(self, group: SlotGroupView) -> None:
        """把某个槽的候选行**建出来**（只建一次，`_candidate_built` 拦重复）。"""
        mid = group.modification_id
        if mid in self._candidate_built:
            return
        layout = self._candidate_layouts.get(mid)
        if layout is None:
            return
        self._candidate_built.add(mid)
        if not group.options:
            layout.addWidget(
                BenchEmpty("（这个槽一条选项都没有 —— 用右上角的「＋ 加选项」加一条）")
            )
            return
        for option in group.options:
            row = self._build_option_row(group, option)
            layout.addWidget(row)
            self._rows.append((mid, option, row))

    def _build_option_row(self, group: SlotGroupView, option: SlotOptionView) -> BenchRow:
        """一行选项 —— **非折叠与折叠态共用这一份**（同一套口径别写两遍）。"""
        row = BenchRow(
            name=f"{MARK_ON if option.selected else MARK_OFF} {option.name}",
            detail=option.detail,
            value=option.cost_text,
            # 这一行说的是哪一条 Options（option_id 是结构性键：就地刷不带它变，
            # 换了选项集合走 refresh()）。
            id_text=id_badge_text(option.option_id),
            # ❗ 用户 2026-10-02：「我不能自己增删挂载，所有有增删空间的，一律给我加上
            #    增删的功能」—— 这一条选项**可以删**（✕ 删的是 `Options` 那一行，
            #    槽 `Modifications` 本身不动）。
            removable=True,
            icon_height=18,
        )
        row.set_icon(self._icons, OPTION_PICTURE_FAMILY, option.icon)
        # ❗ 用户 2026-10-02：「我写 0，它变成了否，然后回来再看，它又变成了是」→
        #    「它不就是一个布尔值吗？我直接改成0或1就行了」—— 于是**点这一行不再写库**，
        #    只把它在右栏打开；"设为当前项"改由「是否默认」那一格承担（写 1 会清同槽其它项）。
        #    ❌ 但**不挂 tooltip**：用户 2026-10-03「我叫你不要在中间显示悬浮窗注释 /
        #    我要你在右边显示悬浮窗注释」—— 注释一律由右栏承担。
        # 费用那一格是**暖金**：qss 有专门的 role（tokens 里 cost_text 写死了
        # "只给成本这一个位置"，所以这一格不许借别的色）。
        cost = row.value_label()
        cost.setProperty("role", "bench-row-cost")
        restyle(cost)
        row.set_selected(self._selected == (vm.OPTIONS_TABLE, option.option_id))
        row.activated.connect(
            lambda mid=group.modification_id, oid=option.option_id: self.activate_option(mid, oid)
        )
        row.remove_requested.connect(
            lambda mid=group.modification_id, oid=option.option_id: self.remove_option(mid, oid)
        )
        # ❗ 用户 2026-10-02：「给所有的子选项都加一个复制按钮，因为很多时候我都是复制一栏
        #    再在上面进行修改的」—— ⧉ 照抄整行（46 列），只换 Id / 接槽尾 / 不抢当前项。
        copy_tool = row.add_tool("⧉", tooltip="照这一条复制一份（Options 整行照抄，只换 Id）")
        copy_tool.clicked.connect(
            lambda _=False, mid=group.modification_id, oid=option.option_id: self.copy_option(
                mid, oid
            )
        )
        return row

    def _apply_slot_row(self, row: SlotRow, group: SlotGroupView) -> None:
        """把槽那一行的字按最新数据写回去（**就地**，一个控件都不换）。"""
        selected = group.selected_option
        row.set_title(group.title)
        row.set_id_text(id_badge_text(group.modification_id))
        row.set_detail(self._slot_row_detail(group))
        row.set_cost(selected.cost_text if selected is not None else "")
        row.set_icon(
            self._icons,
            OPTION_PICTURE_FAMILY,
            selected.icon if selected is not None else "",
        )
        row.set_toggle_text(self._toggle_text(group.modification_id))

    def _apply_option_row(self, row: BenchRow, option: SlotOptionView) -> None:
        """候选行的就地改字（●/○ ＋ 效果 ＋ 费用 ＋ 图 ＋ 选中高亮）。"""
        row.set_name(f"{MARK_ON if option.selected else MARK_OFF} {option.name}")
        row.set_detail(option.detail)
        row.set_value(option.cost_text)
        row.set_icon(self._icons, OPTION_PICTURE_FAMILY, option.icon)
        row.set_selected(self._selected == (vm.OPTIONS_TABLE, option.option_id))

    def clear_selection(self) -> None:
        """页面把选中换成别的行了：把这一块的高亮全清（`BenchPage._on_row_selected` 调的）。

        从前 SlotsBench **没有**这一手（口径是"② 的 ● 是数据态、不画点选态"），
        于是高亮一直跟着 `Options.IsDefault` —— 三个槽就是三行高亮。2026-10-02 改掉。
        """
        self._selected = ("", 0)
        for _mid, _option, row in self._rows:
            row.set_selected(False)
        for row in self._slot_rows.values():
            row.set_selected(False)

    def _reload_values_collapsed(self) -> None:
        """折叠态的就地刷新：槽那一行 ＋ **已经建出来**的候选行，一个控件都不重建。

        行集合对不上（加了 / 删了选项）就退回 `refresh()` —— 那条路由外层的署名拦着，
        这里只兜一道，免得把值写到**错行**上。
        """
        context = self._context
        if context is None or not self._slot_rows:
            return
        groups = vm.slot_views(context.data, context.unit_id)
        keys_before = [
            (group.modification_id, option.option_id)
            for group in self._groups
            for option in group.options
        ]
        keys_after = [
            (group.modification_id, option.option_id)
            for group in groups
            for option in group.options
        ]
        if keys_after != keys_before:
            self.refresh()
            return
        self._groups = groups
        updated: list[tuple[int, SlotOptionView, BenchRow]] = []
        for group in groups:
            row = self._slot_rows.get(group.modification_id)
            if row is not None:
                self._apply_slot_row(row, group)
            for option in group.options:
                candidate = self._candidate_row(group.modification_id, option.option_id)
                if candidate is None:
                    continue
                self._apply_option_row(candidate, option)
                # ❗ 控件就地改字之外，`_rows` 里那个 **option 视图也要换成新的**：
                #    不换的话 `options()` / `row_for()` 这些读数会答旧值（与非折叠态
                #    那一段同一个坑 —— 那边也是先用 widgets 攒着、再拿新 option 重装）。
                updated.append((group.modification_id, option, candidate))
        self._rows = updated

    def _candidate_row(self, modification_id: int, option_id: int) -> BenchRow | None:
        for mid, option, row in self._rows:
            if mid == int(modification_id) and option.option_id == int(option_id):
                return row
        return None

    def _toggle_text(self, modification_id: int) -> str:
        """「▸ 展开候选（N 项）」/「▾ 收起候选（N 项）」—— N = 这个槽有几条选项。"""
        group = self._group(int(modification_id))
        count = group.option_count if group is not None else 0
        head = COLLAPSE_TEXT if int(modification_id) in self._open_slots else EXPAND_TEXT
        return f"{head}（{count} 项）"

    @staticmethod
    def _slot_row_detail(group: SlotGroupView) -> str:
        """折叠态那一行中间那段：`N 选 1 · ● 当前项 · 效果 [· 借来的槽那句话]`。"""
        head = f"{group.option_count} 选 1"
        selected = group.selected_option
        if selected is None:
            text = f"{head} · （这个槽一项都没选中）"
        else:
            parts = [head, f"{MARK_ON} {selected.name}"]
            if selected.detail:
                parts.append(selected.detail)
            text = " · ".join(parts)
        return f"{text} · {group.note}" if group.note else text

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #

    def activate_option(self, modification_id: int, option_id: int) -> str:
        """点一项 = **在右栏把它打开**（看它、改它的全部列）—— **一个字节都不写**。

        ❗ 2026-10-02 口径翻了（用户原话：「**它不就是一个布尔值吗？我直接改成0或1就行了**」）。
        从前这一下会写 `Options.IsDefault`（= 把当前项换成它），于是**用户手写在右栏的 0/1
        会被这一下悄悄盖回去** —— 用户报的就是「我写0，它变成了否，然后回来再看，它又变成了是」
        （实测：纯导航 / refresh / 换单位回来都不动它，只有点这一行会写）。
        现在"看"与"选"分开：点行只打开它；要把它设成当前项，在**右栏把「是否默认」写 1**
        （那一条会顺手把同槽其它项清 0，槽内互斥照旧 —— 见 bench_page._keep_slot_exclusive）。
        """
        context = self._context
        self._selected = (vm.OPTIONS_TABLE, int(option_id))
        self.row_selected.emit(vm.OPTIONS_TABLE, int(option_id))
        if context is None:
            return ""
        group = self._group(modification_id)
        if group is not None and not group.is_own_slot:
            message = (
                f"这个槽（#{modification_id}）是借来的（属于单位 #{group.owner_unit_id}）—— "
                "只给看不给改：改它会一起改到槽的主人"
            )
            self.status_message.emit(message)
            return message
        self._focus_slot = int(modification_id)
        # ❗ **一个字都不写**：点行只是"打开它"（口径见 docstring）。选中态跟着数据走，
        #    所以这里要看一眼数据里**现在**是哪一项 —— 就地换 ● 与费用即可，不许 refresh()
        #    （点一下整块重建 = 用户看到闪一下，见 reload_values 的注释）。
        label = (
            vm.slot_option_labels(context.data, modification_id).get(option_id) or f"#{option_id}"
        )
        option = context.data.option(option_id)
        cost = context.data.t(vm.OPTIONS_TABLE).i(option, "Cost") if option is not None else 0
        message = f"已打开「{label}」（{vm.amount_text(cost)}）"
        self.status_message.emit(message)
        self.reload_values()
        return message

    def remove_option(self, modification_id: int, option_id: int) -> str:
        """行尾 ✕：删掉**这一条选项**（`Options` 那一行）。返回给状态栏的那句话。

        用户 2026-10-02：「我不能自己增删挂载，所有有增删空间的，一律给我加上增删的功能」。

        三条口径：

        1. 删的是本槽的**一条选项**，`Modifications`（槽）本身不动（删整槽是另一件事，没做）；
        2. 它要是**正选着的那一项**，删完这个槽就没有选中项 —— 游戏按 `GetDefaultOptions`
           （第一条 `IsDefault`，都没有就 `Order == 0`）回退，这句话必须当场说；
        3. 借来的槽一个字节都不写（与 `activate_option` 同一条）。
        """
        context = self._context
        if context is None:
            return ""
        group = self._group(modification_id)
        if group is not None and not group.is_own_slot:
            message = (
                f"这个槽（#{modification_id}）是借来的（属于单位 #{group.owner_unit_id}）—— "
                "只给看不给删：删它会一起改到槽的主人"
            )
            self.status_message.emit(message)
            return message
        options = group.options if group is not None else ()
        option = next((item for item in options if item.option_id == int(option_id)), None)
        if option is None:
            message = f"这一条选项（Options #{option_id}）已经不在了 —— 没得删"
            self.status_message.emit(message)
            return message
        was_selected = bool(option.selected)
        name = option.name
        context.delete_row(vm.OPTIONS_TABLE, int(option_id))
        if self._selected == (vm.OPTIONS_TABLE, int(option_id)):
            self._selected = ("", 0)
        note = (
            " ⚠ 它本来是这一槽**选中的**那一项 —— 这个槽现在没有选中项，"
            "游戏会退回 Order 最小的那一条"
            if was_selected
            else ""
        )
        message = f"已删掉这一条选项：{name}（Options #{option_id}）{note} · 撤销（Ctrl+Z）"
        self.status_message.emit(message)
        self.refresh()
        return message

    def add_option(self, modification_id: int = 0) -> int:
        """「＋ 加选项」：给**焦点槽**加一条新选项。返回新选项 Id，没加成给 0。

        落点口径（Lead 只钉了按钮上的字）：加到用户最后点过的那个槽最直观；
        还没点过任何一项就加到第一个槽；新的一项是**空的、且不抢当前项**
        （`IsDefault=False`），点它才会选中。借来的槽一个字节都不写。
        """
        context = self._context
        if context is None:
            return 0
        target = int(modification_id) or self._focus_slot
        if not target:
            target = self._groups[0].modification_id if self._groups else 0
        group = self._group(target)
        if not target or group is None:
            self.status_message.emit("这个单位没有改装槽 —— 没有地方可以加选项")
            return 0
        if not group.is_own_slot:
            self.status_message.emit(
                f"槽 #{target} 是借来的（属于单位 #{group.owner_unit_id}）—— 不能往里加选项"
            )
            return 0
        values = vm.new_option_values(context.data, target, changeset=context.changeset)
        option_id = int(context.insert_row(vm.OPTIONS_TABLE, values))
        # 焦点跟着走：加过选项的那一槽就是最近动过的槽（见 activate_option 的同一口径）。
        self._focus_slot = int(target)
        # ❗ **加完当场就是它**（用户 2026-10-02：「加选项…改到 Options 上」）：
        #    新行以前只是"排在列表里"，右栏还停在上一条选中项上 —— 用户加完还得再点一下
        #    才够得着那 46 列。现在发一次 row_selected，右栏直接落到这条 **Options** 行。
        self._selected = (vm.OPTIONS_TABLE, option_id)
        self.status_message.emit(
            f"已加一条选项（{vm.OPTIONS_TABLE} #{option_id}）到槽 #{target}"
            " —— 它是空的一项，右栏就是它（给它填名字 / 效果）"
        )
        self.refresh()
        self.row_selected.emit(vm.OPTIONS_TABLE, option_id)
        return option_id

    def copy_option(self, modification_id: int, option_id: int) -> int:
        """行尾 ⧉：**照抄这一条选项**（整行 46 列，只换 Id / 接槽尾 / 不抢当前项）。

        复制的用途就是「照着改」—— 所以复制完**当场选中它**（右栏落到新行），
        与 `add_option` 同一条口径。借来的槽一个字节都不写（同 activate / remove）。
        """
        context = self._context
        if context is None:
            return 0
        group = self._group(modification_id)
        if group is None:
            self.status_message.emit(f"这个单位没有改装槽 #{modification_id} —— 复制不了")
            return 0
        if not group.is_own_slot:
            self.status_message.emit(
                f"这个槽（#{modification_id}）是借来的（属于单位 #{group.owner_unit_id}）—— "
                "只给看不给复制：复制出来的行照样挂在槽主人身上"
            )
            return 0
        values = vm.copy_option_values(
            context.data,
            int(option_id),
            modification_id=int(modification_id),
            changeset=context.changeset,
        )
        if values is None:
            self.status_message.emit(
                f"这一条选项（{vm.OPTIONS_TABLE} #{option_id}）已经不在了 —— 复制不了"
            )
            return 0
        new_id = int(context.insert_row(vm.OPTIONS_TABLE, values))
        self._focus_slot = int(modification_id)
        self._selected = (vm.OPTIONS_TABLE, new_id)
        self.status_message.emit(
            f"已复制这一条选项：{vm.OPTIONS_TABLE} #{new_id}（照 #{option_id} 整行照抄，只换 Id）"
            " —— 右栏就是它，直接在上面改"
        )
        self.refresh()
        self.row_selected.emit(vm.OPTIONS_TABLE, new_id)
        return new_id

    def select_slot(self, modification_id: int) -> None:
        """点槽行 / 抬头 = 右栏画这条 `Modifications`（"能看见就要能改"）。

        只发信号，**一个字节都不写**：借来的槽也照样能看它自己的列（改不改由右栏那条路
        自己守 —— 与"点了就是选中"那条写库路无关）。

        ❗ **一屏只有一个选中**（页面那条口径）：右栏既然换成槽那一行了，这一块里
        **选项行**的高亮就得跟着清掉 —— 不清的话屏幕上会同时亮着一条 Options 行。
        槽行自己**不画**点选态（`SlotRow.selected` 那条口径本轮没动，仍是"候选展开着"之外
        什么都不表示），要的只是"别留着旧高亮"。
        """
        self._selected = ("", 0)
        for _mid, _option, row in self._rows:
            row.set_selected(False)
        self.row_selected.emit(vm.MODIFICATIONS_TABLE, int(modification_id))

    def copy_slot(self, modification_id: int) -> int:
        """行尾 ⧉：**照抄这一整个槽**（`Modifications` 那一行 ＋ 它名下的全部 `Options`）。

        用户 2026-10-02：「改装槽这里的 Options 也要能复制，也要能改」—— 编成台里别处的行
        都有 ⧉（交接 §23–§26），只有槽行没有；而"复制一栏再在上面进行修改"正是用户
        2026-10-02 的原话用法。

        三条口径：

        1. 复制的是**整个槽**：`Modifications` 一行（`vm.copy_slot_values`：整行照抄，只换
           Id / Order，本单位已有同名槽时换个名）＋ 它下面的 `Options`（`vm.copy_slot_options`：
           46 列照抄、指到新槽、`Id` 留空由 `core_add_row` 现场逐条取）；
        2. **一条撤销**（走 `EditorContext.insert_group`：Ctrl+Z 一次整组回来）；
        3. 借来的槽**一个字节都不写**（与 activate / remove / copy_option 同一条）。
        """
        context = self._context
        if context is None:
            return 0
        mid = int(modification_id)
        group = self._group(mid)
        if group is None:
            self.status_message.emit(f"这个单位没有改装槽 #{mid} —— 复制不了")
            return 0
        if not group.is_own_slot:
            self.status_message.emit(
                f"这个槽（#{mid}）是借来的（属于单位 #{group.owner_unit_id}）—— "
                "只给看不给复制：复制出来的槽照样挂在槽主人身上"
            )
            return 0
        rows = context.data.t(vm.MODIFICATIONS_TABLE)
        source_name = rows.s(rows.by_id(mid), "Name", "")
        values = vm.copy_slot_values(context.data, mid, changeset=context.changeset)
        if values is None:
            self.status_message.emit(
                f"这个槽（{vm.MODIFICATIONS_TABLE} #{mid}）已经不在了 —— 复制不了"
            )
            return 0
        new_id = int(
            context.insert_group(
                vm.MODIFICATIONS_TABLE,
                values,
                extras=lambda new_mid: [
                    (vm.OPTIONS_TABLE, item)
                    for item in vm.copy_slot_options(context.data, mid, new_modification_id=new_mid)
                ],
            )
        )
        copied = len(group.options)
        renamed = str(values.get("Name", "")) != source_name
        # 焦点跟着走：复制件的用途就是"照着改"，下一步多半是给它加选项（与 add_slot 同一条）。
        self._focus_slot = new_id
        self._selected = (vm.MODIFICATIONS_TABLE, new_id)
        self.status_message.emit(
            f"已复制这一整个槽：{vm.MODIFICATIONS_TABLE} #{new_id}"
            f"（照 #{mid} 整行照抄，连它下面 {copied} 条选项一起）"
            + (f" · 名字改成了「{values['Name']}」（本单位不能有两个同名的槽）" if renamed else "")
            + " —— 右栏就是它，直接在上面改"
        )
        self.refresh()
        self.row_selected.emit(vm.MODIFICATIONS_TABLE, new_id)
        return new_id

    def add_slot(self) -> int:
        """「＋ 加一个槽」：给本单位加一个**空的**改装槽。返回新槽 Id，没加成给 0。

        用户 2026-10-02：「左卡不加，但增删整槽也加！」。

        - 新槽一条选项都没有（`vm.new_slot_values` 只填那 7 列能让它站住的）——
          状态栏把"接下来怎么用"说清楚（用「＋ 加选项」往里加、名字 / 类型在右栏改），
          ⛔ 不弹窗（成功类信息只写状态栏，AGENTS 的弹窗口径）；
        - 顺手把 `_focus_slot` 指到新槽 —— 用户下一步多半是往里加选项，
          不指的话「＋ 加选项」会加到上一个槽上；
        - 加的是一个**本单位**的槽（`UnitId = 本单位`），不是借来的。
        """
        context = self._context
        if context is None:
            return 0
        values = vm.new_slot_values(context.data, context.unit_id, changeset=context.changeset)
        slot_id = int(context.insert_row(vm.MODIFICATIONS_TABLE, values))
        self._focus_slot = slot_id
        # 与加选项同一条：加完右栏就地落到**这一行**（下面那句让用户"去右栏改名字 / 类型"，
        # 不切过去的话他得先在槽列表里找到它）。
        self._selected = (vm.MODIFICATIONS_TABLE, slot_id)
        message = (
            f"已加一个空的改装槽（{vm.MODIFICATIONS_TABLE} #{slot_id}）"
            f"到单位 #{context.unit_id} —— 它是空的，用「＋ 加选项」往里加；"
            "名字 / 类型在右栏改（右栏已切到它）"
        )
        self.status_message.emit(message)
        self.refresh()
        self.row_selected.emit(vm.MODIFICATIONS_TABLE, slot_id)
        return slot_id

    def remove_slot(self, modification_id: int) -> str:
        """抬头 / 槽行那颗 ✕：删掉**整个槽**。返回给状态栏的那句话。

        用户 2026-10-02：「增删整槽也加！」。

        三条口径：

        1. 删的是 `Modifications` 那一行 **＋ 级联它下面的全部 `Options`**
           （`vm.option_ids_of` 那张名单；实测全库只有 Options 有 `ModificationId` 列）；
        2. 这是**改单位结构**，不是改挂载 —— 状态栏必须把"级联了几条选项"说清楚，
           而且**一条撤销**（走 `EditorContext.delete_rows`，Ctrl+Z 一次全回来）；
        3. 借来的槽**一个字节都不写**（与 `activate_option` / `remove_option` 同一条）。
        """
        context = self._context
        if context is None:
            return ""
        group = self._group(modification_id)
        if group is None:
            message = f"这个单位没有改装槽 #{modification_id} —— 没得删"
            self.status_message.emit(message)
            return message
        if not group.is_own_slot:
            message = (
                f"这个槽（#{modification_id}）是借来的（属于单位 #{group.owner_unit_id}）—— "
                "只给看不给删：删它会一起改到槽的主人"
            )
            self.status_message.emit(message)
            return message
        option_ids = vm.option_ids_of(context.data, int(modification_id))
        context.delete_rows(
            [(vm.OPTIONS_TABLE, option_id) for option_id in option_ids]
            + [(vm.MODIFICATIONS_TABLE, int(modification_id))]
        )
        # 这个槽没了：展开态 / 焦点 / 页面选中那几份记忆都要跟着清
        # （`_open_slots` / `_open_groups` 是跨 refresh 与换单位留着的）。
        self._open_slots.discard(int(modification_id))
        self._open_groups.discard(int(modification_id))
        if self._focus_slot == int(modification_id):
            self._focus_slot = 0
        if self._selected[0] == vm.OPTIONS_TABLE and self._selected[1] in option_ids:
            self._selected = ("", 0)
        if self._selected == (vm.MODIFICATIONS_TABLE, int(modification_id)):
            self._selected = ("", 0)
        message = (
            f"已删掉整个改装槽 #{modification_id}（{vm.MODIFICATIONS_TABLE}）"
            f" ＋ 它下面 {len(option_ids)} 条选项（{vm.OPTIONS_TABLE}）"
            " · 一条撤销（Ctrl+Z）"
        )
        self.status_message.emit(message)
        self.refresh()
        return message

    # ------------------------------------------------------------------ #
    # 读数（用例钉控件里那一份状态，不现算）
    # ------------------------------------------------------------------ #

    def rows(self) -> tuple[BenchRow, ...]:
        """界面上那几行选项（顺序 = 槽序 › 槽内顺序）。"""
        return tuple(row for _mid, _option, row in self._rows)

    def groups(self) -> tuple[SlotGroupView, ...]:
        return self._groups

    def options(self) -> tuple[SlotOptionView, ...]:
        return tuple(option for _mid, option, _row in self._rows)

    def row_for(self, option_id: int) -> BenchRow | None:
        for _mid, option, row in self._rows:
            if option.option_id == int(option_id):
                return row
        return None

    def group_titles(self) -> tuple[str, ...]:
        return tuple(group.title for group in self._groups)

    def selected_option_id(self) -> int:
        """界面上带 ● 的那一项（0 = 一个槽都没选中）。

        扫的是 `self._groups`（**每个槽的完整选项**），不是 `self._rows`：折叠态下
        没展开的槽那几行根本还没建出来，扫 `_rows` 会一律给 0。
        """
        for group in self._groups:
            option = group.selected_option
            if option is not None:
                return option.option_id
        return 0

    def add_button(self, modification_id: int = 0) -> BenchAction | None:
        """某个槽行上那颗 ＋（**给这个槽**加选项）。

        不给槽号（0）= 与 `add_option()` 同一条解析：焦点槽 -> 第一个槽（老用例的写法照旧能用）。
        """
        target = int(modification_id) or self._focus_slot
        if not target:
            target = self._groups[0].modification_id if self._groups else 0
        if not target:
            return None
        head = self._group_heads.get(target)
        if head is not None:
            return head.add_button()
        row = self._slot_rows.get(target)
        return row.add_button() if row is not None else None

    def add_buttons(self) -> tuple[BenchAction, ...]:
        """**每个槽**行上那颗 ＋（顺序 = 界面上的槽序）—— 用例按它读"一槽一颗"。"""
        return tuple(
            button
            for group in self._groups
            if (button := self.add_button(group.modification_id)) is not None
        )

    def copy_button(self, modification_id: int = 0) -> BenchAction | None:
        """某个槽行上那颗 ⧉（**照这一整个槽复制一份**）。

        与 `add_button` 同一条解析：不给槽号（0）= 焦点槽 -> 第一个槽。
        """
        target = int(modification_id) or self._focus_slot
        if not target:
            target = self._groups[0].modification_id if self._groups else 0
        if not target:
            return None
        head = self._group_heads.get(target)
        if head is not None:
            return head.copy_button()
        row = self._slot_rows.get(target)
        return row.copy_button() if row is not None else None

    def copy_buttons(self) -> tuple[BenchAction, ...]:
        """**每个槽**行上那颗 ⧉（顺序 = 界面上的槽序）—— 用例按它读"一槽一颗"。"""
        return tuple(
            button
            for group in self._groups
            if (button := self.copy_button(group.modification_id)) is not None
        )

    def add_slot_button(self) -> BenchAction:
        """抬头那颗「＋ 加一个槽」（读数 + 用例点它）。"""
        return self._add_slot

    def section_title(self) -> str:
        """这一块抬头那行字（**读控件里那一份**）—— 装配方写完编号之后，用例按它断言。"""
        return self._section.title_text()

    def section_hint(self) -> str:
        """抬头右边那句灰字说明（与 title 一起按形态 / kinds 传）。"""
        return self._section.hint_text()

    def section(self) -> BenchSection:
        """这一块那个折叠栏（用例读"默认收起 / 展没展开"，装配方也可读）。"""
        return self._section

    def focus_slot(self) -> int:
        """「＋ 加选项」现在指向哪个槽。"""
        return self._focus_slot

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _group(self, modification_id: int) -> SlotGroupView | None:
        for group in self._groups:
            if group.modification_id == int(modification_id):
                return group
        return None

    def _group_parts(self, group: SlotGroupView) -> tuple[str, str, str]:
        """抬头三段：槽名 / 「N 个选项」/ 那句说明（借来的槽…）。

        ❗ 分三段而不是拼成一句：槽名那一格是 `bench-group-title`（md + bold + secondary），
        右边那两句是 `entry-count`（muted/xs）—— 拼一句会把说明也染成槽名那么重。
        """
        return (group.title, f"{len(group.options)} 个选项", group.note)


__all__ = [
    "COLLAPSE_TEXT",
    "EXPAND_TEXT",
    "GROUP_CARET_COLLAPSED",
    "GROUP_CARET_EXPANDED",
    "MARK_OFF",
    "MARK_ON",
    "SLOT_TITLE_MIN_WIDTH",
    "SLOTS_HINT",
    "SLOTS_TITLE",
    "STATS_HINT",
    "STATS_TITLE",
    "SlotRow",
    "SlotsBench",
    "StatsBench",
]
