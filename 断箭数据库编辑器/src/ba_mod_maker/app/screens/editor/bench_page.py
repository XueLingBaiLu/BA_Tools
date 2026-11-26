"""编成台 —— 单位修改 GUI 重做的主界面（双击单位后默认进这里）。

设计：docs/交接-2026-10-01-单位编成台设计.md（十条口径）+ docs/设计稿-编成台-步兵.png。

    骨架统一：**左 = 单位卡（只读 + 实时预览）｜中 = 可操作编成区｜右 = 选中项 + 入口**。

三张编成台（步兵 / 载具 / 飞机）共用这套骨架，差异**只在摆哪几块**（见 _build_sections_for）：

- **步兵**：① 一格 = 一个兵 ② 班用武器与弹药 ③ 装甲 ④ 机动 ⑤ 能力 ⑥ 传感器
  ⑦ 专精可用性 ⑧ 改装槽（槽内互斥）；
- **载具**：① 挂载槽 ② 改装 → 炮塔 → 武器 → 弹药 四层层级 ③ 改装槽 ④ 装甲 ⑤ 机动
  ⑥ 能力 ⑦ 传感器 ⑧ 专精可用性 ⑨ 运输；
- **飞机**（含直升机）：① 挂载槽 ② 改装槽（**折叠选项**）③ 炮塔 → 武器 → 弹药 ④–⑧ 同上 ⑨ 运输。

❗ 用户 2026-11-11：「**让所有载具都显示这些挂载槽**」（此前只有飞机 / 直升机有）＋
「不要显示『（一个都不折叠）』，这个折叠栏的名字就叫『挂载槽』」。

❗ 用户 2026-10-02：「专精可用性单独放一栏，装甲、机动、能力、传感器，都应该单独一栏，
下面这个其它入口就可以不需要了」—— 原 ③（三类挤一块）拆成五栏、⑤ 其他入口整块删掉。
**每块抬头那个圈码按"摆第几块"自动生成**（_number_sections），块的常量里不写编号。

## 形态判据（bench_kind）

**有班成员** → 步兵；**飞得起来**（直升机 / 固定翼）→ 飞机；其余 → 载具。
实测全库分布 194 / 107 / 239（= 540，与 docs/关系图-单位数据表.md §4 的构成吻合）。

双击单位默认进**这一页**（UnitEditorScreen.land_on_bench）；
「关系树｜全列｜只读卡」退居**兜底**，顶栏那颗「🌲 关系树 / 🔧 编成台」随时切得回去。

## 分工（与三栏那套一致）

- 页面**不自己算数**：取数在 app/viewmodel/bench_*.py（零 Qt），写库在 EditorContext；
- 页面**不弹窗**：成功 / 没有改动 → status_message 写状态栏；
- 颜色只从 tokens 来；控件只用 bench_kit 的角色。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from ba_mod_maker.app.screens.editor.pages.base import EditorContext, EditorPage
from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import bench_slots as vm_slots
from ba_mod_maker.app.viewmodel.bench_detail import detail_for
from ba_mod_maker.app.viewmodel.unit_card import TYPE_AIRCRAFT, TYPE_HELICOPTER, CardData
from ba_mod_maker.app.widgets.bench_kit import BenchSection
from ba_mod_maker.app.widgets.bench_rail import BenchRail
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef

#: 单位形态（我们认得出的三类）。
KIND_SQUAD = "squad"
KIND_VEHICLE = "vehicle"
KIND_AIR = "air"


#: 抬头编号：**按"摆第几块"自动生成**（用户 2026-10-02 口径 1）。
#: 块的常量里一个字都不写编号 —— 写死就会出现"摆 ⑧ 却写着 ④"那种两个说法
#: （真机上抓到过：一屏两个 ④）。
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _section_of(widget: QWidget) -> BenchSection | None:
    """这一块暴露的那个折叠栏（SquadBench 是**属性** section，其余是**方法** section()）。"""
    inner = getattr(widget, "section", None)
    section = inner() if callable(inner) else inner
    return section if isinstance(section, BenchSection) else None


def _bare_title(text: str) -> str:
    """把块常量里那个圈码去掉（余下的才是"这一块是什么"）。"""
    text = text.strip()
    if text and text[0] in CIRCLED:
        return text[1:].lstrip()
    return text


def _number_sections(sections: tuple[QWidget, ...]) -> None:
    """给每块抬头写上它**摆第几块**那个圈码（就地 set_title，一个控件都不重建）。"""
    for index, widget in enumerate(sections):
        if index >= len(CIRCLED):
            continue
        section = _section_of(widget)
        if section is None:
            continue
        section.set_title(f"{CIRCLED[index]} {_bare_title(section.title_text())}")


#: 顶栏那颗「🔧 编成台」按钮的 tooltip —— **按形态**说清进去会看到什么。
#: 加形态时改这里（不要在 main_window 里再抄一份）。
KIND_TIPS: dict[str, str] = {
    KIND_SQUAD: (
        "进编成台：一格一个兵 / 班用武器与弹药 / 装甲·机动·能力 / 传感器 / 专精可用性 / 改装槽"
    ),
    KIND_AIR: (
        "进编成台：挂载槽 / 改装槽 / 炮塔 › 武器 › 弹种 / 装甲·机动·能力 / 传感器 / "
        "专精可用性 / 运输"
    ),
    KIND_VEHICLE: (
        "进编成台：挂载槽 / 改装 › 炮塔 › 武器 › 弹种四层 / 改装槽 / "
        "装甲·机动·能力 / 传感器 / 专精可用性 / 运输"
    ),
}


def _flies(data: CardData, unit_id: int) -> bool:
    """这个单位**飞不飞**（直升机 / 固定翼）—— 决定用飞机那套还是载具那套编成台。

    用户 2026-10-01 拍板：「**直升机 + 固定翼 = 挂载图**」。
    两条判据取**并集**（不单打独斗，理由见下）：

    - `Units.Type` 是**直升机(8) / 固定翼(16)** —— 游戏枚举，改版会漂；
    - 它的机动模板挂着一张**飞行包线**（`Mobility.FlyPresetId > 0`）—— 数据本身，
      但**只有固定翼有**：实测 Mi-8MTV-5 与 AH-64 的 FlyPresetId 都是 0，
      所以它单独用会把直升机判成载具。

    ⚠️ 为什么不按「一个选项占几个挂载槽」判（实测载具 1–3、直升机 2–4、固定翼 4–12）：
    那条边界**重叠**（Mi-8 的 4 槽 ≥ Su-25T 的 4 槽），同一兵种会掉进两种界面。
    """
    # ❗ unit_type() 收的是 **Units 那一行**（不是 id）—— 先 by_id 拿到行。
    units = data.t("Units")
    unit_row = units.by_id(unit_id)
    if unit_row is not None and units.i(unit_row, "Type") in (TYPE_HELICOPTER, TYPE_AIRCRAFT):
        return True
    props = data.t("UnitPropulsions")
    mobility = data.t("Mobility")
    for link in props.where("UnitId", unit_id):
        target = mobility.by_id(props.i(link, "MobilityId"))
        if target is not None and mobility.i(target, "FlyPresetId") > 0:
            return True
    return False


def bench_kind(data: CardData, unit_id: int) -> str:
    """这个单位该用哪一张编成台（三种形态，见 docs/设计稿-编成台-*.png）。

    - `KIND_SQUAD`   —— ① 一格一个兵（14 格格子矩阵）；
    - `KIND_AIR`     —— ① 挂载槽 ＋ ② 改装槽 ＋ ③ 炮塔 → 武器 → 弹药；
    - `KIND_VEHICLE` —— ① 挂载槽 ＋ ② 改装 → 炮塔 → 武器 → 弹药 四层层级 ＋ ③ 改装槽。

    判据用「**有没有班组成员**」而不是 Units.Type 的数字 —— Type 是游戏枚举，改版会动；
    「这个单位有没有班成员」是数据本身，也正好是步兵形态画得出来的前提。
    """
    if data is None or unit_id is None or int(unit_id) < 0:
        return ""
    if data.has_table("SquadMembers") and data.t("SquadMembers").where("UnitId", int(unit_id)):
        return KIND_SQUAD
    return KIND_AIR if _flies(data, int(unit_id)) else KIND_VEHICLE


class BenchPage(EditorPage):
    """编成台页面（UnitEditorScreen._stack 里的第二个顶层页）。"""

    #: 跳到一个单位（Units 行）—— 与 MainEditorPage.unit_requested 同一条口径：
    #: 整屏换过去要重建 context，只有外层 UnitEditorScreen 做得了。
    unit_requested = Signal(int)
    #: 请求切到另一处：tree = 关系树三栏（全库兜底）／tables = 数据表页。
    #: ⚠️ 2026-10-02：它**唯一**的发射点（⑤ 其他入口那两颗胶囊）已随那一块删掉 ——
    #: 今天没有发射点。这条件缝**故意留着**（与 CancellationToken 同一条口径：
    #: "还没接的线"不删）：关系树现在走顶栏那颗「🌲 关系树」、数据表页走页签，
    #: 两条路都还在，只是不再需要那一颗胶囊。接线方 = unit_editor_screen._on_page_requested。
    page_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        # 不自带页头：外层那条面包屑已经写着「军械库 › 单位名 › 编成台」。
        super().__init__(
            "编成台",
            "左：单位卡（只读预览）· 中：编成 · 右：选中项的全部列",
            parent,
            header=False,
        )
        self._findings: tuple = ()
        #: 中/右两栏只挂一次（_attach_entries 会被 set_context 反复叫）。
        self._body_ready = False
        self._selected: RowRef | None = None
        #: 上一次真正重挂左卡时是哪個单位（换单位才重挂；改一格不重挂）。
        self._card_unit: int = -1
        #: 上一次中栏四块画出来的"行集合"指纹（见 _structure_signature）。
        self._signature: tuple = ()
        #: 左卡的防抖刷新（见 _schedule_card_refresh）。
        self._card_timer = None
        self._card_unsub = None

        # ---- 左栏：单位卡（只读 + 实时预览）---------------------------- #
        self.card = ReadOnlyCardPanel()
        # 卡上「点改装槽那一项 = 选中它」与树上是同一条口径（写 Options.IsDefault，槽内互斥）。
        self.card.option_select_requested.connect(self._select_slot_option)
        # 卡上点任何一行 —— **在编成台上它是懒的**（设计稿左栏原话：
        # 「这张卡是实时预览……卡上不给点 —— 只读，改数据全在编成台与右栏」）。
        # 但也不许"点了没反应"：给一句状态栏，说清改数据在哪儿。
        self.open_row_requested.connect(self._on_card_navigation)

        # ---- 中栏：编成区（**按形态**建，见 _build_sections_for）-------- #
        # ❗ 这里**一块都不建**：形态要等 set_context 拿到单位才知道。
        #    给载具建步兵那 14 格矩阵是白花钱 —— 「不为没看的东西花时间与内存」
        #    （UnitEditorScreen._ensure_bench 的注释里就写着这一条）。
        self._kind = ""
        self._sections: tuple[QWidget, ...] = ()
        self._sections_cache: dict[str, tuple[QWidget, ...]] = {}
        self._icon_store = None
        center = QWidget()
        center.setProperty("role", "bench-body")
        self._center_box = QVBoxLayout(center)
        self._center_box.setContentsMargins(0, 0, 0, 0)
        self._center_box.setSpacing(TOKENS.spacing.md)
        self._center_box.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(center)

        # ---- 右栏：选中项 + 入口 -------------------------------------- #
        self.rail = BenchRail()
        # 右栏点一格的值 = 就地改（用户 2026-10-01：「右边的数据我要都能改啊」）。
        self.rail.cell_activated.connect(self._open_rail_editor)
        # 右栏那一格外键右边的小 ↗：去看**那一列指向的行**（与中栏点一行同一条路）。
        self.rail.link_activated.connect(self._open_row)

        self.rail.show_message("点中栏任意一行 / 一格，这里就换成它的全部列。")

        self._sync_rail()

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #

    #: 每个形态的中栏 8 块**按顺序**叫什么属性名 —— _apply_kind 拿它把属性绑到当前那一套上。
    #: 顺序必须与 _build_sections_for 返回的元组一致（zip 用 strict 兜着，错了当场炸）。
    _SECTION_ATTRS: dict[str, tuple[str, ...]] = {
        KIND_SQUAD: (
            "bench_squad",
            "bench_loadout",
            "bench_armor",
            "bench_mobility",
            "bench_ability",
            "bench_sensors",
            "bench_specs",
            "bench_slots",
        ),
        KIND_VEHICLE: (
            # ❗ 2026-11-11：**所有载具都显示挂载槽**（此前只有飞机 / 直升机有）。
            "bench_hardpoints",
            "bench_turrets",
            "bench_slots",
            "bench_armor",
            "bench_mobility",
            "bench_ability",
            "bench_sensors",
            "bench_specs",
            "bench_transport",
        ),
        KIND_AIR: (
            "bench_hardpoints",
            "bench_slots",
            # 飞机/直升机也要「炮塔 › 武器 › 弹种」
            "bench_turrets",
            "bench_armor",
            "bench_mobility",
            "bench_ability",
            "bench_sensors",
            "bench_specs",
            "bench_transport",
        ),
    }

    def _build_sections_for(self, kind: str) -> tuple[QWidget, ...]:
        """按**形态**建中栏那 8 块（三种形态的差异只在 **① 栏与 ⑧ 栏**）。

        用户 2026-10-01 + 2026-10-02 拍板（图：docs/设计稿-编成台-{步兵,载具,飞机}.png）：

            步兵 ① 一格一个兵 ② 班用武器与弹药 ③ 装甲 ④ 机动 ⑤ 能力 ⑥ 传感器
                 ⑦ 专精可用性 ⑧ 改装槽（槽内互斥）
            载具 ① 挂载槽 ② 炮塔 › 武器 › 弹种四层 ③ 改装槽 ④–⑧ 属性五栏 ⑨ 运输
            飞机 ① 挂载槽 ② 改装槽（**折叠选项**） ③ 炮塔层级 ④–⑧ 属性五栏 ⑨ 运输

        ❗ 用户 2026-11-11：「**让所有载具都显示这些挂载槽**」—— 载具这一支因此也有了
        HardpointsBench（摆在 ①，与飞机同一个位置）。

        ❗ 抬头编号**按位置现生成**（_number_sections）：本函数只管"摆哪几块、什么顺序"。

        ❗ 这里**延迟 import**：各块由不同会话交付，延迟到装配那一刻再 import，
        任何一块写坏了都在这里当场炸出来，而不是把整个模块变成 import 不了
        —— 排查时「谁坏了」一眼看得见。

        ❗ 飞机那块 ② 传 collapsed=True：F-15E 有 5 个槽、**共 31 个选项**，
        全列会有 31 行（设计稿上自己标着「⚠️ 抖不掉的部分」）。载具**不折叠**
        （实测 1–8 个选项，列得下）。
        """
        if kind == KIND_SQUAD:
            from ba_mod_maker.app.widgets.bench_loadout import LoadoutBench
            from ba_mod_maker.app.widgets.bench_slots import SlotsBench
            from ba_mod_maker.app.widgets.bench_squad import SquadBench

            self.bench_squad = SquadBench()
            self.bench_loadout = LoadoutBench()
            self.bench_slots = SlotsBench()
            widgets = (
                self.bench_squad,
                self.bench_loadout,
                *self._stats_blocks(),
                self.bench_slots,
            )
        elif kind == KIND_AIR:
            from ba_mod_maker.app.widgets.bench_hardpoints import HardpointsBench
            from ba_mod_maker.app.widgets.bench_slots import SlotsBench
            from ba_mod_maker.app.widgets.bench_transport import TransportBench
            from ba_mod_maker.app.widgets.bench_turrets import TurretsBench

            self.bench_hardpoints = HardpointsBench()
            # ❗ 抬头编号不在这里写了（_number_sections 按位置现生成）——
            #    从前在这里传「② 改装槽…」，块自己再存一个编号就等于两份真相。
            self.bench_slots = SlotsBench(
                collapsed=True,
                hint="Modifications / Options · 一行一个槽 · 显示当前选中项；选项多，展开再选",
            )
            # ❗ 用户 2026-10-02（截图停在「炮塔模板 #725」上）：「**在改装槽下，我还要能继续
            #    修改武器和弹药**」—— 这块 `TurretsBench` 自己写着「载具 / 直升机 / 固定翼共用」，
            #    之前只有载具那支建它，飞机形态整块没有 ⇒ 挂载图点一格只到炮塔模板就断了。
            #    摆在 ② 改装槽**之后**（用户原话"在改装槽下"）；编号按位置现生成，后面几块自动顺延。
            self.bench_turrets = TurretsBench()
            self.bench_transport = TransportBench()
            widgets = (
                self.bench_hardpoints,
                self.bench_slots,
                self.bench_turrets,
                *self._stats_blocks(),
                self.bench_transport,
            )
        elif kind == KIND_VEHICLE:
            from ba_mod_maker.app.widgets.bench_hardpoints import HardpointsBench
            from ba_mod_maker.app.widgets.bench_slots import SlotsBench
            from ba_mod_maker.app.widgets.bench_transport import TransportBench
            from ba_mod_maker.app.widgets.bench_turrets import TurretsBench

            # ❗ 用户 2026-11-11：「**让所有载具都显示这些挂载槽**」—— 与飞机同一个位置（①）。
            #    实测载具 239 台里 **174 台**的 `Options.TurretNId` 非零（BTR-ZD 3 格，
            #    Stryker / BTR-82 各 1 格），所以这块在载具上不是空壳。
            self.bench_hardpoints = HardpointsBench()
            self.bench_turrets = TurretsBench()
            # 载具的改装槽选项少（实测 1–8 个），不折叠。
            self.bench_slots = SlotsBench(
                hint="Modifications / Options · 选中哪一项决定这台车装什么",
            )
            self.bench_transport = TransportBench()
            widgets = (
                self.bench_hardpoints,
                self.bench_turrets,
                self.bench_slots,
                *self._stats_blocks(),
                self.bench_transport,
            )
        else:  # 空形态（还没进单位 / 单位 id 非法）：中栏一块都没有
            return ()
        for widget in widgets:
            # ❗ 带上**是谁发的**：页面据此把**别的块**的高亮清掉（一屏只有一个选中，
            #    见 _on_row_selected 的 docstring）。
            widget.row_selected.connect(
                lambda table, row_id, source=widget: self._on_row_selected(
                    table, row_id, source=source
                )
            )
            widget.status_message.connect(self.status_message)
            if self._icon_store is not None:
                widget.set_icons(self._icon_store)
        # ❗ 改装槽的"点了就是选中"由 SlotsBench **自己**经 EditorContext 写
        #    （口径与 pages/options_page.py::select_slot_option 一致：同一条纯函数
        #    unit_card.exclusive_selection_writes + 同一条 is_own_slot 守卫）。
        #    这里不再转发一遍 —— 两边都写 = 同一格写两次。
        # ❗ 编号放在**最后**：各块先按自己的 kinds / 常量把抬头建出来，
        #    再统一换成"摆第几块"那个圈码（就地 set_title，一个控件都不重建）。
        _number_sections(widgets)
        return widgets

    def _stats_blocks(self) -> tuple[QWidget, ...]:
        """属性这一族 = **五栏**（装甲 / 机动 / 能力 / 传感器 / 专精可用性）。

        用户 2026-10-02 原话：「专精可用性单独放一栏，装甲、机动、能力、传感器，
        都应该单独一栏」。一栏只画自己那一类 —— 取数走 StatsBench(kinds=...)
        （kinds 是 vm.stat_lines 的唯一开关）。

        ❗ 属性名与 _SECTION_ATTRS **两边一起改**（zip(strict=True) 会当场炸）。
        """
        from ba_mod_maker.app.viewmodel import bench_slots as vm
        from ba_mod_maker.app.widgets.bench_slots import StatsBench

        out: list[QWidget] = []
        for attr, kind in (
            ("bench_armor", vm.STAT_KINDS[0]),
            ("bench_mobility", vm.STAT_KINDS[1]),
            ("bench_ability", vm.STAT_KINDS[2]),
            ("bench_sensors", vm.SENSOR_KIND),
            ("bench_specs", vm.SPEC_KIND),
        ):
            block = StatsBench(kinds=(kind,))
            setattr(self, attr, block)
            out.append(block)
        return tuple(out)

    def _apply_kind(self, kind: str) -> None:
        """把中栏换成这一类形态该有的那几块（同类重复调用是**空操作**）。

        ❗ 换法是 **hide + 从布局里摘**，**不是销毁**：
          - 销毁重建会闪 —— 用户为这件事提过三回（「界面闪一下很影响观感」）；
          - 而且 set_context 每进一个单位都会调一次，销毁就等于每次进单位重画一遍。
        ❗ 放回布局之后**必须显式 show()** —— 否则藏过一次的块放回去也不会自己显示。
          这一手真踩过：_rebuild_left_only 里 hide 之后没 show，左栏整块空白。
        """
        if kind == self._kind:
            return
        for widget in self._sections:
            self._center_box.removeWidget(widget)
            widget.hide()
        if kind not in self._sections_cache:
            self._sections_cache[kind] = self._build_sections_for(kind)
        self._sections = self._sections_cache[kind]
        # ❗ **属性绑在「当前形态」上**：self.bench_slots 必须永远指**正在台上**那一个。
        #    只在 _build_sections_for 里赋值的后果（实测踩到）：切回步兵时 squad 那套已在缓存、
        #    _build_sections_for 不再执行，于是 self.bench_slots 还指着**飞机那个**实例 ——
        #    它没接过 context，options() 空，而用例读的就是这个属性。
        for name, widget in zip(self._SECTION_ATTRS.get(kind, ()), self._sections, strict=True):
            setattr(self, name, widget)
        for index, widget in enumerate(self._sections):
            self._center_box.insertWidget(index, widget)
            widget.show()
        self._kind = kind
        # 换了一批块 -> 上一次那份"行集合指纹"没有意义了。在这里清掉，
        # 否则紧接着的第一次 refresh() 会判成"结构变了"再白重建一遍。
        self._signature = ()

    def build_left(self) -> QWidget:
        """左栏 = 那张卡（跨刷新是**同一个对象**，基类 hide/show 的判据都成立）。"""
        return self.card

    def _attach_entries(self) -> None:
        """中栏插在左栏与右栏之间，右栏定宽贴在最后。"""
        if self._body_ready:
            return
        self._body.removeWidget(self._scroll)
        self._body.insertWidget(1, self._scroll, 3)
        self._body.addWidget(self.rail, 0)
        self._body_ready = True

    # ------------------------------------------------------------------ #
    # 上下文
    # ------------------------------------------------------------------ #

    def set_context(self, context: EditorContext) -> None:
        # ❗ **换单位先收右栏那一格的就地编辑器**（连它弹出来的候选浮层）：浮层是**顶层窗**，
        #    不随页面/控件藏起来而消失 —— 上一台车的那个下拉会飘在新单位头上（用户 2026-10-02
        #    截图里那个黑框就是这么一类东西）。
        self.rail.close_editor()
        self._selected = RowRef("Units", context.unit_id)
        self.card.set_instance(context.instance)
        # ❗ **不跟卡片自动订阅**：那个回调是同步跑的（68ms 整张卡重排），压在点击那一下上。
        #    这里自己防抖 —— 用户点完立刻就看得见右栏/中栏的变化，卡片随后跟上（180ms，察觉不到）。
        self.card.set_changeset(context.changeset, follow=False)
        # ❗ 换单位 = 换 context（也可能换 changeset）—— **先把上一条退掉**，
        #    否则每进一次单位就往订阅表里压一条死回调（改一格被叫 N 次）。
        if self._card_unsub is not None:
            self._card_unsub()
            self._card_unsub = None
        self._card_unsub = context.changeset.subscribe(self._schedule_card_refresh)
        self.card.show_unit(context.unit_id)
        # 卡片算出来的 value_ref -> link_ref（选项覆盖时指着生命值的是 Options#504.ArmorId）。
        context.set_links(
            {
                stat.value_ref: stat.link_ref
                for stat in self.card.card.stats
                if stat.value_ref is not None and stat.link_ref is not None
            }
            if self.card.card is not None
            else {}
        )
        # ❗ **形态要拿到单位才知道** —— 先把中栏换成这一类该有的那几块，再往下接 context。
        kind = bench_kind(context.data, context.unit_id)
        switched = kind != self._kind
        self._apply_kind(kind)
        super().set_context(context)
        # ❗ 中栏那几块**各自**要接一次 context —— 忘了这一步的实机症状是
        #    几块抬头都在、底下全写着「还没有打开单位」（2026-10-01 真机走查抓到的）。
        for section in self._sections:
            section.set_context(context)
        # ❗ 从缓存里**换回来**的那一批：里面还是**上一个单位**的内容，必须整套重画一次。
        #    不能指望后面的 reload_values() —— 它只在"结构没变"时用，**不会把旧行换掉**
        #    （实测症状：同一模块里先跑了载具用例，再跑步兵那条"四块真的接上 context 了吗"
        #    就会红，而单跑是绿的）。
        if switched:
            for section in self._sections:
                section.refresh()
        # ❗ 刚接上 context 时那几块已经**整套建好了** —— 这里立刻记下基线，
        #    否则紧接着的第一次 refresh() 会拿空签名比，判成"结构变了"再白重建一遍
        #    （实测：改第一格时 SlotCard 保留 0 个，就是被这一步吃掉的）。
        self._signature = self._structure_signature()
        self._card_unit = context.unit_id
        self.card.set_validation(self._findings)
        self._sync_rail()

    def set_findings(self, findings: tuple) -> None:
        """立绘上的唯一角标（与三栏那一页同一条口径）。"""
        self._findings = tuple(findings or ())
        self.card.set_validation(self._findings)

    def set_icons(self, store) -> None:
        self._icon_store = store
        self.card.set_icons(store)
        for section in self._sections:
            section.set_icons(store)
        # ❗ 右栏详情标题那一格的图标位也吃这份缓存（用户 2026-10-02：「给这个标题加个图标」）。
        self.rail.set_icons(store)

    def refresh_icon(self) -> None:
        self.card.set_icons(self._icon_store)
        for section in self._sections:
            section.set_icons(self._icon_store)
        self.rail.set_icons(self._icon_store)

    #: 判「结构有没有变」看这几张表的行集合（编成台画的就是它们）。
    #: ❗ 口径来自 docs/交接-2026-09-30-界面批-图标树弹药.md:76/93：
    #:   **署名变了才整套重建，没变走 reload()（改数量不重建）**。
    #:   用户为这件事提过几回：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
    _SIGNATURE_TABLES = (
        "SquadMembers",
        "SquadWeapons",
        # ❗ 载具 / 飞机的 ① 栏画的就是这两张表（炮塔候选行与"炮塔装哪些武器"）——
        #    不放进署名的话，加一座炮塔以后中栏不会重建，用户看到的还是旧的层级图。
        "TurretUnits",
        "TurretWeapons",
        "WeaponAmmunitions",
        "UnitArmors",
        "UnitPropulsions",
        "UnitAbilities",
        "SensorUnits",
        # ❗ ⑦ 专精可用性那一栏画的就是它 —— 不放进署名的话，用「＋」加一行之后
        #    中栏不会重建，用户看到的还是空的那一栏（方向同上面那两条注释）。
        "SpecializationAvailabilities",
        "Modifications",
        "Options",
    )

    #: 左卡落后多少毫秒跟上（够看出"它变了"，又不让用户觉得卡）。
    CARD_REFRESH_MS = 180

    def _schedule_card_refresh(self) -> None:
        """改动之后把左卡的刷新**排到 180ms 之后**（同一轮里改十格只刷一次）。"""
        if self._card_timer is None:
            from PySide6.QtCore import QTimer

            self._card_timer = QTimer(self)
            self._card_timer.setSingleShot(True)
            self._card_timer.timeout.connect(self._refresh_card)
        self._card_timer.start(self.CARD_REFRESH_MS)

    def _refresh_card(self) -> None:
        self.card.refresh_from_changes()

    def _structure_signature(self) -> tuple:
        """这一屏**该有哪些行**的指纹 —— 只跟"有哪些行"有关，跟值无关。"""
        context = self._context
        if context is None:
            return ()
        rows = tuple((name, context.row_ids(name)) for name in self._SIGNATURE_TABLES)
        return (context.unit_id, rows)

    def refresh(self) -> None:
        """数据变了：**能就地更新就绝不重建**（用户提过几回的那条）。

        - 左卡：它自己订阅了 changeset（ReadOnlyCardPanel.set_changeset），改一格它自己重排；
          **只有换单位**才需要把它摘下来重挂（挂一次 = 整块重画，那才是最显眼的一下）。
        - 中栏那 8 块：行集合（署名）没变 → 调它们的 reload_values()（就地改字）；
          变了（插/删行）才 refresh() 整套重建。
          队友还没交 reload_values 的那块，暂时退回 refresh() —— 宁可闪一下，也不能显示旧值。
        - 重建前后**保住两个滚动区的位置**（中栏与右栏），别让界面"向上跳"。
        """
        context = self._context
        if context is None:
            return
        center_scroll = self._scroll.verticalScrollBar().value()
        rail_scroll = self.rail.scroll_value()

        if self._card_unit != context.unit_id:
            self._card_unit = context.unit_id
            self._rebuild_left_only()

        signature = self._structure_signature()
        structural = signature != self._signature
        self._signature = signature
        for section in self._sections:
            if structural:
                section.refresh()
                continue
            reload_values = getattr(section, "reload_values", None)
            if callable(reload_values):
                reload_values()
            else:
                section.refresh()  # 队友还没交这一手 —— 先保证值是对的
        self._sync_rail()

        # 保住滚动位置：重建会让滚动条回顶，用户看到的就是"跳一下 / 闪一下"。
        self._scroll.verticalScrollBar().setValue(center_scroll)
        self.rail.set_scroll_value(rail_scroll)

    def trail_segments(self) -> tuple[str, ...]:
        """面包屑尾段（外层拼成「军械库 › 单位名 › 编成台」）。"""
        return ("编成台",)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def reset_view(self) -> None:
        """把「用户走到哪儿了」复位（跨用例的视图状态：右栏看的是哪一行）。

        与 tests/conftest.py::_reset_editor_view 同一条口径 —— 不清的话，
        上一条用例选中的那个兵会跟着进下一条（关系树那边的 _focus_ref 就是这么显形的）。
        """
        self._clear_block_selections()
        self._selected = RowRef("Units", self._context.unit_id) if self._context else None
        if self._selected is None:
            self.rail.show_message("点中栏任意一行 / 一格，这里就换成它的全部列。")
        else:
            self._sync_rail()

    def show_unit_row(self) -> None:
        """右栏回到「这个单位」那一行（← / Esc 的第一层，与三栏那一页同一条顺序）。"""
        if self._context is None:
            return
        # 右栏回到「这个单位」= 中栏那一行的高亮也该跟着灭（一屏只有一个选中）。
        self._clear_block_selections()
        self._selected = RowRef("Units", self._context.unit_id)
        self._sync_rail()
        self.focus_changed.emit()

    def _open_row(self, table: str, row_id: int) -> None:
        """去看**某一格外键指向的那一行**：编成台是入口，全部列在数据表页里看。

        ❗ 2026-10-02：行级那颗 ↗（"看当前这一行"）已按用户要求删掉 —— 右栏本来就
        把当前这一行的全部列画出来了；这里只剩"值指着的另一行"这一条路。
        """
        if table == "Units":
            self.unit_requested.emit(int(row_id))
            return
        self.open_table_row_requested.emit(str(table), int(row_id))

    def _on_card_navigation(self, table: str, row_id: int) -> None:
        """左栏那张卡在编成台上是**只读预览**：点它不发跳转，只回一句人话。

        （三栏那一页的卡是可导航的；编成台按用户定的分工把它降成纯预览 ——
        设计稿左栏写着「卡上不给点」。）
        """
        self.status_message.emit(
            f"左边这张卡是只读预览（{table} #{row_id}）——改数据在编成台里，"
            "看某一行的全部列点中栏那一行。"
        )

    def _on_row_selected(self, table: str, row_id: int, *, source: QWidget | None = None) -> None:
        """中栏选中了一行 / 一格 → 右栏换成它的全部列。

        ❗ **一屏只有一个选中**（2026-10-02）：先把**别的块**的高亮清掉，再记这一行。
        用户实机原话：「为什么我能同时选中四个？」—— 属性那一族拆成五栏之后每栏是
        独立控件、各记各的选中，连点 ③④⑤⑥ 会同时亮四行（拆块之前是一个 StatsBench 管三类）。

        - 只有「点一行 = 选中它」的块有 clear_selection()；SlotsBench 的 ● 是**数据态**
          （Options.IsDefault）、TransportBench 不画选中态 —— 两者都没有这个方法，
          这里也就不会去动它们；
        - source = 发信号的那个块（装配时绑上）；直接调（source=None）等于
          「这一行就是唯一选中」，**所有**块都清。
        """
        # ❗ **右栏那一格的就地编辑器先收工**（连它弹出来的候选浮层一起）—— 用户 2026-10-02 实机：
        #    右栏开着外键下拉、框里还是半截搜索词（`Left`）时点了中栏那一条选项 ⇒ 按既定口径
        #    "报错、不写、**不关**"，于是那个浮层（**顶层窗，编辑器不收它就不走**）就赖在右栏上，
        #    用户看到的是一个黑框盖住下面几行。上下文中栏都换了，右栏这一格该收工。
        self.rail.close_editor()
        for section in self._sections:
            if section is source:
                continue
            self._clear_section_selection(section)
        self._selected = RowRef(str(table), int(row_id))
        self.focus_changed.emit()
        self._sync_rail()

    @staticmethod
    def _clear_section_selection(section: QWidget) -> None:
        """让这一块把高亮清掉（没有这一手的块 = 不画「点选」态，跳过）。"""
        clear = getattr(section, "clear_selection", None)
        if callable(clear):
            clear()

    def _clear_block_selections(self) -> None:
        """中栏**所有**块的高亮全清（页面把选中换成「不是中栏某一行」时用）。"""
        for section in self._sections:
            self._clear_section_selection(section)

    # ------------------------------------------------------------------ #
    # 右栏就地编辑（与三栏那套同一个 InlineEditor、同一条写库路）
    # ------------------------------------------------------------------ #

    def _open_rail_editor(self, ref) -> None:
        """右栏点一格 → 就在那一行里改。

        写库这件事**一份都不另写**：取格走 `context.cell(ref)`，落库走
        `register_weapon_if_missing` + `context.set_value`（与三栏的
        EditorPage._open_inline_editor / _commit 同一条路）。
        """
        from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor

        context = self._context
        if context is None:
            return
        cell = context.cell(ref)
        if cell is None:
            self.status_message.emit(f"{ref} 这一格现在读不到了（可能刚被删掉）")
            return
        # ❗ 外键的原始 Id 现在由**右栏那一行自己**画在值右边（BenchRail._add_line），
        #    而且**编辑栏关着的时候也画**（用户口径 2026-10-01：「在没有编辑栏的时候，
        #    也在右边显示 ID」）—— 所以编辑器里不再重复一份（show_raw_id 保持默认 False，
        #    否则同一行会挨着出现两个 #67）。
        editor = InlineEditor(cell, instance=context.instance, link_ref=context.link_ref(ref))
        editor.committed.connect(lambda value, r=ref: self._commit_rail(r, value))
        editor.cancelled.connect(self.rail.close_editor)
        editor.open_place_requested.connect(self.open_table_row_requested)
        self.rail.attach_editor(editor)

    def _commit_rail(self, ref, value) -> None:
        """右栏那一格改完了：写进缓存 + 一句话 + 收起浮层（**不碰盘**）。"""
        context = self._context
        if context is None:
            return
        registered = self.register_weapon_if_missing(ref, value)
        context.set_value(ref, value)
        exclusive = self._keep_slot_exclusive(ref, value)
        self.rail.close_editor()
        self.status_message.emit(f"已改 {ref} → {value}{registered}{exclusive}")
        self.changed.emit()

    def _keep_slot_exclusive(self, ref, value) -> str:
        """手写 `Options.IsDefault = 是` 之后，把**同槽其它项**清 0（槽内互斥照旧）。

        ❗ 2026-10-02 口径翻了之后（用户：「它不就是一个布尔值吗？我直接改成0或1就行了」），
        这一格成了"设为当前项"的**唯一入口** —— 点选项行不再写它（见 bench_slots.activate_option）。
        于是互斥得由这里兜住，否则一个槽会出现两个 true（原库里就有 2 个这样的槽，实测 615 个槽里
        177 个恰好一项 / 2 个多项）。返回给状态栏补的那半句；没动就返回空串。
        """
        context = self._context
        if context is None or ref.table != "Options" or ref.column != "IsDefault":
            return ""
        if value is not True:
            return ""
        data = context.data
        option = data.option(ref.row_id)
        if option is None:
            return ""
        modification_id = int(data.t("Options").i(option, "ModificationId"))
        cleared = 0
        for row_id, column, other in vm_slots.exclusive_selection_writes(
            data, modification_id, ref.row_id
        ):
            if int(row_id) == int(ref.row_id) and column == "IsDefault":
                continue  # 这一格就是刚写的那一格
            context.set_value(CellRef("Options", row_id, column), other)
            cleared += 1
        if not cleared:
            return ""
        data.invalidate("Options")
        return f" · 同槽另外 {cleared} 项已自动取消"

    def close_editor(self) -> bool:
        """Esc 先收右栏那个浮层（与三栏那页同一条契约，见 UnitEditorScreen._on_escape）。"""
        if self.rail.has_editor():
            self.rail.close_editor()
            return True
        return False

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """这一页被切走 / 最小化时，把右栏的就地编辑器收掉（连它的候选浮层）。

        用户 2026-10-02 截图里那个「黑框」就是**顶层窗不跟着藏**：页面切走了、控件看不见了，
        而那个下拉还浮在屏幕上（里面只剩半张旧 pixmap，所以大半是黑的）。
        """
        super().hideEvent(event)
        self.rail.close_editor()

    def _select_slot_option(self, modification_id: int, option_id: int) -> None:
        """点改装槽那一项 = 选中它（写 Options.IsDefault，槽内互斥）。

        口径与 pages/options_page.py::select_slot_option 一字不差 —— 那一条是
        「卡上的 ◉ 与写盘同源」的既有实现（用户 2026-10-01 报的「点了不高亮」在那里定的案）。
        """
        from ba_mod_maker.app.screens.editor.pages.options_page import select_slot_option

        if self._context is None:
            return
        self.status_message.emit(select_slot_option(self._context, modification_id, option_id))

    # ------------------------------------------------------------------ #
    # 右栏
    # ------------------------------------------------------------------ #

    def _sync_rail(self) -> None:
        if self._context is None or self._selected is None:
            return
        table, row_id = self._selected.table, self._selected.row_id
        cells = self._context.cells_of_row(table, row_id)
        if not cells:
            self.rail.show_message(f"{table} #{row_id} 这一行没有任何列。")
            return
        # 右栏两种视图：**列**（原始列 + 值）与**详情**（这一行指向的武器/弹药本身）。
        # 详情那份由 viewmodel/bench_detail.py 现算 —— 取数用卡上那套公式，这里只负责给。
        detail = detail_for(
            self._context.data,
            self._context.unit_id,
            table,
            row_id,
            instance=self._context.instance,
        )
        self.rail.set_selection(
            table, row_id, cells, detail=detail, title=self._row_title(table, row_id)
        )

    def _row_title(self, table: str, row_id: int) -> str:
        from ba_mod_maker.app.viewmodel.table_labels import table_title
        from ba_mod_maker.app.viewmodel.unit_card import row_name

        title = table_title(table) or table
        if self._context is None:
            return f"{title} #{row_id}"
        name = row_name(self._context.data, table, row_id)
        return f"{name}（{title} #{row_id}）" if name else f"{title} #{row_id}"

    # ------------------------------------------------------------------ #
    # 给用例的读数
    # ------------------------------------------------------------------ #

    def selected_row(self) -> tuple[str, int]:
        if self._selected is None:
            return ("", 0)
        return (self._selected.table, self._selected.row_id)

    def sections(self) -> tuple[QWidget, ...]:
        return tuple(self._sections)

    @property
    def unit_id(self) -> int:
        return self._context.unit_id if self._context is not None else -1
