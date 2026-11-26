"""编成台 ④ 运输配对的控件。

口径出处：设计图 `docs/设计稿-编成台-载具.png` 中栏 ④（BTR-ZD Skrezhet / `Units#206`）
＋ `docs/交接-2026-10-01-单位编成台设计.md` §3 口径 1 / 7。取数与规则全在
`app/viewmodel/bench_transport.py`（零 Qt），本文件只做三件事：
**摆控件、把意图转给 `EditorContext`、把结果写状态栏**。

## 四件事写在这里（免得下一个会话重猜）

1. **一行 = 一条 `TransportAvailabilities` 配对**：左边是**被运的那个单位**的名字，
   右边灰字「能被它搭载 · 专精 X · 上限 N」（设计图逐字），行尾 ✕（口径与 ③ 同一套）；
2. **「＋ 加一行」是一级挑**（Lead 2026-10-01 定案）：候选 = `Units` 一张表。挑中一个单位，
   由 `viewmodel.bench_transport.sa_id_of_unit` 反查它那条 `SA`（1:1，338/338）——
   承运方自己**不必**有 `SA` 行（实测 206 = 0 条），所以不能拿"本车的 SA 行"当候选列；
3. **一行上有四个不同的东西，别混**（Lead 2026-10-01 定案）：

   | 手势 | 落到 |
   |---|---|
   | 点整行 | `row_selected("TransportAvailabilities", 配对行Id)` —— 右栏换成**配对行**的全部列 |
   | 行尾 ✕ | 删 `TransportAvailabilities` 里**这条配对** |
   | 右边徽章 | **被运单位的 Id**（不是配对行自己的 Id —— 同 ② 弹药行 / ③ 装甲行的口径） |

4. **不画选中态**（不调 `set_selected`）：设计图这一块没有 ●○，行上只有 ✕。

不许写库：写入一律经 `EditorContext`（`insert_row` / `delete_row`）；颜色全在 tokens，
本文件一个色值都没有；`role` 只用 qss.py 里已存在的那些（`bench-section` / `bench-row` …）。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import bench_transport as vm
from ba_mod_maker.app.viewmodel.bench_transport import TransportLine
from ba_mod_maker.app.viewmodel.unit_card import row_name
from ba_mod_maker.app.widgets.bench_kit import (
    BenchAction,
    BenchEmpty,
    BenchRow,
    BenchSection,
    id_badge_text,
)
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import BaModMakerError

#: 抬头（与设计图逐字一致）：④ 的标题与灰字表名。
TITLE = "④ 运输"
HINT = "TransportAvailabilities"

#: 还没选中单位时那句灰字（**不是空框** —— 空框看不出是空的还是没画出来）。
NOT_IN_EDITOR = "还没有选中单位 —— 选中一个单位之后这里画出它的运输配对"

#: 行尾 value 那一格的字。设计图上每一行都写「1 行」：
#: **一行 = 一条配对**，这个数字不是数量、别去数别的行。
ROW_VALUE = "1 行"


class TransportBench(QWidget):
    """④ 运输：一行 = 一条运输配对，抬头一颗 ＋ 加一条。

    对外 = Lead 定的**四件套 + 三信号**（`set_context` / `set_icons` / `refresh` /
    `reload_values` ＋ `row_selected` / `status_message`），
    外加本文件自己的读数方法（`rows()` / `lines()` / `picker()` / `add_button()` …）。
    """

    #: (表名, 行 Id) —— 点了这一行，右栏照这个画（**点整行就发**）
    row_selected = Signal(str, int)
    #: 一句人话（成功 / 加不了 / 没找到），**绝不弹窗**。
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = None
        self._store = None
        self._lines: tuple[TransportLine, ...] = ()
        self._rows: list[tuple[TransportLine, BenchRow]] = []
        #: 主体里**本类自己**画着的控件（见 _clear_body —— 候选宿主不在此列）。
        self._body_widgets: list[QWidget] = []
        self._picker: RowPicker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOKENS.spacing.xs)

        self._section = BenchSection(TITLE, HINT)
        self._add_button = self._section.add_action(
            "＋ 加一行",
            primary=True,
            tooltip=(
                "挑一个**单位** —— 他就能被这台车搭载（挂到他能上的那个专精下，"
                "上限取自 SpecializationAvailabilities.MaxAvailabilityXp0）"
            ),
        )
        self._add_button.clicked.connect(self._begin_add)
        root.addWidget(self._section)

        # 候选列表**就地在这一块下面**冒出来（用户口径：不需要这么多弹窗）。
        # ❗ 2026-10-02（折叠）：宿主搬进 section **主体**里。从前它挂在 root 上（主体之外），
        #    "收起这一块"只 hide 主体，候选列表于是留在屏幕上（点抬头 ▸ 之后这一块并没有
        #    真的只剩抬头那一行）。主体里的行改由本类自己记账（_body_widgets + _clear_body），
        #    不再用 section.clear_body()：那一手会把宿主一起 detach 掉（下一帧就是野指针）。
        self._picker_host = QWidget()
        self._picker_host.setProperty("role", "bench-body")
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(
            TOKENS.spacing.sm, 0, TOKENS.spacing.sm, TOKENS.spacing.sm
        )
        self._picker_layout.setSpacing(0)
        self._section.body_layout().addWidget(self._picker_host)
        self._picker_host.setVisible(False)

        self.refresh()

    # ------------------------------------------------------------------ #
    # 装配接口（Lead 定的四件套 + 三信号）
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上 `EditorContext`（`None` = 画空态，不许炸）。写库一律经它。"""
        self._context = context
        self.close_picker()
        self.refresh()

    def set_icons(self, store) -> None:
        """接一份图标缓存（`theme/icons.py::shared_icon_cache()` 那份）。

        ④ 的行上**不画图标**（设计图这块只有名字 + 灰字），所以这里只做保存 ——
        留着它是因为这一层与另外几块共用同一条装配接口
        （⛔ 也不在这里 new IconCache —— 那会撞 access violation）。
        """
        self._store = store

    def refresh(self) -> None:
        """按当前数据重画（**幂等**）：数据变了就调它。

        收尾两步都不能省：`BenchSection.clear_body`（先 hide 再 setParent(None)，
        否则闪一个顶层小窗，见 `widgets/detach.py`）＋ 自己那两条表的缓存失效
        （`Rows.where` / `by_id` 都缓存过，不丢就照旧名单画）。
        """
        self._clear_body()
        self._rows.clear()
        context = self._context
        if context is None:
            self._lines = ()
            self._add_body(BenchEmpty(NOT_IN_EDITOR))
            return
        # 读之前先丢缓存：这一块读三张表（配对 / SA / 专精名），三张都要丢。
        for table in (vm.TABLE, vm.SPEC_AVAIL_TABLE, vm.UNITS_TABLE):
            context.data.invalidate(table)
        self._lines = vm.transport_lines(context.data, context.unit_id)
        if not self._lines:
            self._add_body(BenchEmpty(vm.transport_empty_text(context.unit_id)))
            return
        for line in self._lines:
            row = BenchRow(
                name=line.carried_text,
                detail=line.detail_text,
                value=ROW_VALUE,
                # ❗ 徽章写的是**这一行"说的是"的那个实体**（被运的那个单位），不是这条配对行
                #    自己的 Id —— 与 ② 弹药行（写 Ammunitions#143 而不是 WeaponAmmunitions
                #    那一行）、③ 装甲行（写 Armors#118 而不是 UnitArmors 那一行）同一条口径。
                #    配对行自己的 Id 由"点整行 -> 右栏"去看（见下一条 seam）。
                id_text=id_badge_text(line.carried_id),
            )
            row.activated.connect(lambda line=line: self._on_clicked(line))
            row.remove_requested.connect(lambda line=line: self.remove_line(line))
            # ⧉ = 复制这一行**指着的** `SpecializationAvailabilities` 另存一份、本行指过去
            #     （与 ③–⑦ 属性行同一条口径，见交接 §23）。
            clone_tool = row.add_tool("⧉", tooltip="复制这一行指着的「哪个单位能带它」另存一份")
            clone_tool.clicked.connect(lambda _=False, line=line: self.clone_line(line))
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
        """**就地**把行上的字换成最新的 —— 一个控件都不重建（用户提过几回的那条）。

        用户原话：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
        `refresh()` 会把每一行 detach 掉再重建，肉眼就是闪一下；**结构没变**（还是那几行）
        时走这一条：只写文字（名字 / 灰字 / 行尾 Id），控件、布局、滚动位置一个都不动。

        ❗ 行集合对不上（插 / 删 / 换单位）就退回 `refresh()` —— 就地写会把值写到**错行**上
        （先例同 `bench_slots.StatsBench.reload_values`）。
        """
        context = self._context
        if context is None or not self._rows:
            return
        for table in (vm.TABLE, vm.SPEC_AVAIL_TABLE, vm.UNITS_TABLE):
            context.data.invalidate(table)
        lines = vm.transport_lines(context.data, context.unit_id)
        if [line.row_id for line in lines] != [line.row_id for line, _row in self._rows]:
            self.refresh()
            return
        widgets = [row for _line, row in self._rows]
        self._rows = []
        for line, row in zip(lines, widgets, strict=True):
            row.set_name(line.carried_text)
            row.set_detail(line.detail_text)
            # ❗ Id 跟着新数据走（只刷名字不刷 Id 的话，行上写着新单位、右边还挂着旧 Id）。
            row.set_id_text(id_badge_text(line.carried_id))
            self._rows.append((line, row))
        self._lines = lines

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #

    def _begin_add(self) -> None:
        """抬头的「＋ 加一行」：就地冒候选列表（候选 = `Units` 一张表）。"""
        context = self._context
        if context is None:
            self.status_message.emit("还没有选中单位 —— 先选一个单位再加运输配对")
            return
        self.close_picker()
        picker = RowPicker(context.instance, vm.UNITS_TABLE, parent=self._picker_host)
        picker.picked.connect(self._on_picked)
        picker.cancelled.connect(self.close_picker)
        self._picker = picker
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        picker.start()

    def close_picker(self) -> None:
        """收起候选列表（点中 / Esc / 换一块都走它）。"""
        detach(self._picker)
        self._picker = None
        self._picker_host.setVisible(False)

    def _on_picked(self, unit_id: int) -> None:
        """候选列表里挑中了一个**单位**：先收起列表，再落库。"""
        self.close_picker()
        self.add_line(int(unit_id))

    def add_line(self, carried_unit_id: int) -> int:
        """加一条配对（**写库经 `EditorContext.insert_row`**）。返回新行 Id，没加成给 0。

        被运单位 -> `SA` 行是 1:1（`sa_id_of_unit`），所以这里只挑一级；
        反查不到（那个单位在 `SpecializationAvailabilities` 里没有行）就拒绝：
        **不为了凑一条去造第二张表的行**（Lead 定案）。
        """
        context = self._context
        if context is None:
            return 0
        values = vm.new_transport_values(context.data, context.unit_id, int(carried_unit_id))
        if values is None:
            label = self._unit_label(int(carried_unit_id))
            self.status_message.emit(
                f"加不了：{label} 在 {vm.SPEC_AVAIL_TABLE} 里没有行"
                f"（挑中的单位必须先能被某个专精带上场）"
            )
            return 0
        try:
            row_id = int(context.insert_row(vm.TABLE, values))
        except (BaModMakerError, KeyError) as exc:
            self.status_message.emit(f"没有加成：{exc}")
            return 0
        label = self._unit_label(int(carried_unit_id))
        self.refresh()
        self.status_message.emit(
            f"已加一条运输配对：{label}（{vm.TABLE} #{row_id}，Ctrl+Z 能撤回来）"
        )
        return row_id

    def clone_line(self, line: TransportLine) -> int:
        """⧉：复制这一行指着的 `SpecializationAvailabilities` 另存一份，本行指过去。

        口径同 ③–⑦（交接 §23）：复制的是**它指的那一行**（「哪个单位能带它」那一条），
        复制完把本行指过去 —— 新那一条随便改 specialization / 上限，别人一个字节不动。
        """
        context = self._context
        if context is None or not line.sa_id or not line.row_id:
            self.status_message.emit("这一行没有落点 —— 复制不了")
            return 0
        link = CellRef(vm.TABLE, line.row_id, vm.LINK_COLUMN)
        new_id, message = context.clone_row_to(vm.SPEC_AVAIL_TABLE, line.sa_id, link)
        self.status_message.emit(f"{message} —— 右栏就是新模板，直接在上面改")
        self.refresh()
        self.row_selected.emit(vm.SPEC_AVAIL_TABLE, new_id)
        return new_id

    def remove_line(self, line: TransportLine) -> None:
        """行尾 ✕：删**这条配对行**（`SA` 与被运单位那两行一个字节不动）。"""
        context = self._context
        if context is None:
            return
        try:
            context.delete_row(vm.TABLE, line.row_id)
        except (BaModMakerError, KeyError) as exc:
            self.status_message.emit(f"删不掉 {vm.TABLE}#{line.row_id}：{exc}")
            return
        self.refresh()
        self.status_message.emit(
            f"已删掉这条运输配对：{line.carried_text}（{vm.TABLE} #{line.row_id}）—— "
            f"只断了这一条，{vm.SPEC_AVAIL_TABLE} 与被运单位那一行都没动"
        )
        # 删完右栏不该还停在一条没了的行上：还有别的配对就让外层换过去；
        # 一条都不剩时**不发信号**（右栏停在原来那一行无所谓，发空表名会把外层搞糊涂）。
        if self._lines:
            self.row_selected.emit(vm.TABLE, self._lines[0].row_id)

    # ------------------------------------------------------------------ #
    # 读数（测试与右栏用 —— 钉控件里这一份状态，不现算）
    # ------------------------------------------------------------------ #

    def rows(self) -> tuple[BenchRow, ...]:
        """这一屏画出来的行控件（顺序 = 画出来的顺序）。"""
        return tuple(row for _line, row in self._rows)

    def lines(self) -> tuple[TransportLine, ...]:
        """与 `rows()` 一一对应同序的那几行数据。"""
        return tuple(line for line, _row in self._rows)

    def picker(self) -> RowPicker | None:
        """现在冒出来的候选列表（没冒出来 = None）。"""
        return self._picker

    @property
    def picker_visible(self) -> bool:
        return self._picker is not None

    def add_button(self) -> BenchAction:
        return self._add_button

    def section(self) -> BenchSection:
        return self._section

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _on_clicked(self, line: TransportLine) -> None:
        """点整行 = 选中它（右栏换这一行的全部列）。

        ❗ 不调 `BenchRow.set_selected`：这一块不画选中态（设计图没有 ●○）。
        发信号是必须的 —— 不发的话点一行完全没反应，用户会当成坏了。
        """
        self.row_selected.emit(vm.TABLE, line.row_id)

    def _unit_label(self, unit_id: int) -> str:
        context = self._context
        if context is None or unit_id <= 0:
            return f"{vm.UNITS_TABLE} #{unit_id}"
        return row_name(context.data, vm.UNITS_TABLE, unit_id)


__all__ = ["HINT", "NOT_IN_EDITOR", "ROW_VALUE", "TITLE", "TransportBench"]
