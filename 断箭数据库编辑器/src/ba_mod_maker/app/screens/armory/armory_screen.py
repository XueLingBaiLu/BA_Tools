"""军械库主屏：筛选带 → 编成头 → 类别行卡片网格 → 详情面板。

数据整形全在 app/viewmodel/armory.py（零 Qt）；本文件只做两件事：
把数据画出来、把用户动作传出去。

三个作用域计数（专精 / 阵营 / 全部 / 未编入专精）**始终同时显示**：
只看专精会让人以为全游戏只有 31 个单位（ADR 0002 决定 5）。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.armory.category_row import CategoryRow
from ba_mod_maker.app.screens.armory.detail_panel import UnitDetailPanel
from ba_mod_maker.app.screens.armory.unit_card import UnitCard
from ba_mod_maker.app.theme.icons import IconCache, shared_icon_cache
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import (
    CATEGORY_LABELS,
    UNASSIGNED_TITLE,
    build_armory_sheet,
    list_countries,
    list_specializations,
    unclassified_country,
)
from ba_mod_maker.app.widgets.bench_kit import BenchSection
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.core.assets.families import FLAG_FAMILY, SPEC_ICON_FAMILY
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.rules import RuleEngine
from ba_mod_maker.core.rules.delta import unit_finding_delta
from ba_mod_maker.core.rules.engine import Finding
from ba_mod_maker.core.rules.humanize import humanize_ids

#: 类别折叠栏的抬头与灰字（口径：能折的都折、默认收起）。
CATEGORY_TITLE = "类别"
CATEGORY_HINT = "按兵种粗分（侦察 / 步兵 / 载具…）· 一行格子，折起来不占地方"

#: 专精下拉里"全部单位"那一项。
ALL_SPECIALIZATIONS = -1
#: 专精下拉里"只看未编入专精"那一项。
UNASSIGNED_ONLY = -2

#: 旗标 / 专精图标的**族名**从 core/assets/families.py 取（字面量只在那儿一份）：
#: FLAG_FAMILY 源数据是 Countries.FlagFileName、SPEC_ICON_FAMILY 是 Specializations.Icon
#: （两族同一个 bundle）。


class CardCanvas(QWidget):
    """军械库的卡片画布（流式网格的容器）。

    ❗ 高度必须**自己按内容算**，不能交给 QScrollArea 的 widgetResizable 撑。

    实踩（用户报的「下拉条太长了」）：QScrollArea 取的是
    `画布.minimumSizeHint()` 与视口尺寸的较大者，而 QWidget 默认的
    minimumSizeHint 来自 `QLayout.totalMinimumSize()`——它会拿流式布局的
    **最窄宽度**去问高度，也就是「一行只放一张卡」。395 张卡于是得出 56130px，
    而按真实宽度（1158px）排完只要 9838px：

    - 滚动条能一直往下拖 46000 多像素，底下全是空白；
    - 滑块被压到 9px（贴住 QSS 的 min-height 下限），看不出自己读到哪儿了。

    所以这里把 minimumSizeHint 改成「当前视口宽度下排完所有卡片要多少高」，
    滚动范围就与内容一一对应；内容比视口矮时，QScrollArea 取视口高度，
    滚动条自动不出现。
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(TOKENS.size.card_width, self.content_height())

    def content_height(self) -> int:
        """当前视口宽度下，排完所有卡片需要的高度。"""
        layout = self.layout()
        if layout is None:
            return TOKENS.size.card_height
        return max(TOKENS.size.card_height, layout.heightForWidth(self.canvas_width()))

    def canvas_width(self) -> int:
        """画布应当有多宽 = 所在视口的宽度。

        取**父控件**（QScrollArea 的 viewport）而不是 self.width()：
        本函数是在布局过程中被调用的，此时 self.width() 还是上一轮的值，
        用它会造成「算高度 → 改宽度 → 再算高度」的抖动。
        """
        viewport = self.parentWidget()
        if viewport is not None and viewport.width() > 0:
            return viewport.width()
        return self.width() or TOKENS.size.card_width * 6


class ArmoryScreen(QWidget):
    """军械库主屏。"""

    #: 选中了某个单位（参数：unit_id）
    unit_selected = Signal(int)
    #: 卡片装配失败（参数：unit_id, 原因）。**必须先让用户看见为什么**，
    #: 否则面板会停在上一个单位上，看起来像"这一类单位的卡片显示不出来"。
    card_failed = Signal(int, str)
    #: **双击**了一个单位 -> 进单位编辑器（参数：unit_id）。
    #: 单击仍是"选中看卡"（设计 §10 第 1 条：单击 = 选中，双击 = 进编辑器）。
    unit_activated = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 图标缓存：找不到 cache/icons/ 时会静默降级成程序化剪影，不是错误。
        # ❗ **共享**一份，不是每个屏各建一个：每建一个就多一条加载线程 +（最多）
        # 两个已打开的 bundle。见 theme/icons.py 的 shared_icon_cache()。
        self._icons = shared_icon_cache()
        self._instance = None
        self._country_id: int | None = None
        #: 「隐藏单位」开关（见 _build_filter_bar 里那颗 chip）：
        #: False = 军械库只放能上场的单位；True = 连游戏里不上屏的 / 改型一起放。
        self._include_hidden = False
        self._specialization_id: int | None = None
        self._category: int | None = None
        self._filter_text = ""
        self._selected_unit = -1
        self._rows: list[CategoryRow] = []
        #: 图标预载的延时器（成员，跟着控件一起销毁 —— 别用 QTimer.singleShot，
        #: 那样窗口已经被删掉时回调还会来碰 C++ 对象）。
        self._preload_timer: QTimer | None = None
        #: 单位卡片上的编辑走它（core/edit/changeset.py）。换数据源时重建。
        self._changeset = None
        #: 校验引擎（规则书只读一次，实例可复用）。
        self._engine: RuleEngine | None = None
        #: **打开时的校验基线**。原版既有违规不报（15 条硬约定第 11 条）：
        #: 实测基线有 699 warning / 1036 info，540 个单位里 28 个一打开就带命中
        #: （BTR-82A 自己带 1 条 req.SquadMembers）。不减基线就是"一打开一片红"。
        self._baseline = None
        self._country_buttons: dict[int | None, QPushButton] = {}
        self._category_buttons: dict[int | None, QPushButton] = {}
        #: 国旗按钮 -> 它的地址键（图标是异步来的，到了要往按钮上补）
        self._country_flag_keys: dict[QPushButton, str] = {}
        #: 专精 chip：键是 Specializations.Id，「全部专精」用 ALL_SPECIALIZATIONS、
        #: 「未编入任何专精」用 UNASSIGNED_ONLY（与 viewmodel 的两个哨兵值同一套）。
        self._spec_buttons: dict[int, QPushButton] = {}
        #: 专精按钮 -> 它的图标地址键（同样是异步到货再补）。
        self._spec_icon_keys: dict[QPushButton, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # 顶部只有一行（专精/国家/类别/计数/搜索都并进去了，见 _build_filter_bar）。
        root.addWidget(self._build_filter_bar())
        # 类别折叠栏**在筛选行下面、卡片墙上面**（"放在主界面里"）——
        # 它由上面那次 _build_filter_bar() 建出来，所以必须排在这一行之后。
        root.addWidget(self._category_section)
        root.addWidget(self._build_body(), 1)
        # 图标是异步从 bundle 里取的：迟到时把国旗补到 chip 上。
        self._icons.loaded.connect(self._refresh_flag_icons)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #

    def _build_filter_bar(self) -> QWidget:
        """顶部**一行**：专精下拉 │ 国家 │ 类别 …… 计数 + 搜索。

        为什么合成一行（用户："这一块纵向排列太占地方了"）：
        原来「专精下拉 + 表名 + 计数」独占一行（`sheet-header`，实测 **54px**），
        而那一行的表名跟下拉里的当前项**是同一句话** —— 白占一行。
        2026-10 合并到筛选行（余量够：1707 逻辑宽下内容约 1330），顶部省掉 54px。
        """
        bar = QWidget()
        bar.setProperty("role", "filter-bar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.xs, TOKENS.spacing.lg, TOKENS.spacing.xs
        )
        layout.setSpacing(TOKENS.spacing.sm)

        # 专精**不再是下拉**：用户 2026-10-03「专精不是有图标吗？**国家后面跟几个专精的图标**」
        # ⇒ 国家 chip 之后插一排**带游戏内图标**的专精 chip（见 _rebuild_spec_chips）。
        self._spec_group = QButtonGroup(self)
        self._spec_group.setExclusive(True)

        self._country_group = QButtonGroup(self)
        self._country_group.setExclusive(True)
        self._add_chip(
            layout,
            "全部",
            None,
            self._country_buttons,
            self._country_group,
            self._on_country_chip,
            checked=True,
        )

        self._country_separator = self._separator()
        layout.addWidget(self._country_separator)
        # 专精 chip 的锚点：它们插在**国家那一排之后**（用户要的顺序）。
        self._spec_separator = self._separator()
        layout.addWidget(self._spec_separator)

        # 「隐藏单位」开关 —— 用户 2026-10-03：「**我要能在产品里面，看到所有单位！**」
        # 军械库平时只放**能上场**的（DisplayInArmory=true 且不是改型）；打开这颗，
        # 连游戏里不上屏的（改型 / 隐藏 / 调试单位）也一起上墙，「全部」就是 Units 整表。
        # ⚠️ 它**不进** _country_group：那是互斥的阵营选择，这是一颗独立开关。
        self._include_hidden_button = QPushButton("隐藏单位")
        self._include_hidden_button.setProperty("role", "chip")
        self._include_hidden_button.setCheckable(True)
        self._include_hidden_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._include_hidden_button.setToolTip(
            "军械库默认只放能上场的单位（DisplayInArmory=true 且不是改型）。\n"
            "打开后把游戏里不上屏的也一起放上来 —— 就能在产品里看到整张 Units 表。"
        )
        self._include_hidden_button.toggled.connect(self._on_include_hidden)
        layout.addWidget(self._include_hidden_button)

        # ❗ 用户 2026-10-02：「**还是一行，类别放在主界面里，还是做成折叠栏**」——
        #    类别 chips 不再占筛选行（那一行只剩 国家 → 专精 → 计数 → 搜索），
        #    搬到主区顶部、做成**折叠栏**（本仓既定口径：能折的都折、默认收起）。
        #    折叠栏由 _build_filter_bar 建出来、__init__ 摆到筛选行下面。
        self._category_section = BenchSection(CATEGORY_TITLE, CATEGORY_HINT)
        category_host = QWidget()
        category_host.setProperty("role", "bench-body")
        self._category_layout = QHBoxLayout(category_host)
        self._category_layout.setContentsMargins(0, 0, 0, 0)
        self._category_layout.setSpacing(TOKENS.spacing.sm)
        self._category_section.add_widget(category_host)
        self._category_section.add_stretch()

        self._category_group = QButtonGroup(self)
        self._category_group.setExclusive(True)
        self._add_chip(
            self._category_layout,
            "全部类别",
            None,
            self._category_buttons,
            self._category_group,
            self._on_category_chip,
            checked=True,
        )
        for index, label in enumerate(CATEGORY_LABELS):
            self._add_chip(
                self._category_layout,
                label,
                index,
                self._category_buttons,
                self._category_group,
                self._on_category_chip,
            )
        self._category_layout.addStretch(1)

        layout.addStretch(1)

        self._counts = QLabel("")
        self._counts.setProperty("role", "muted")
        layout.addWidget(self._counts)

        self._search = QLineEdit()
        self._search.setPlaceholderText("在军械库里过滤单位名…")
        self._search.setFixedWidth(240)
        self._search.textChanged.connect(self._on_filter_text)
        layout.addWidget(self._search)
        return bar

    def _add_chip(
        self,
        layout: QHBoxLayout,
        text: str,
        key,
        registry: dict,
        group: QButtonGroup,
        slot,
        *,
        checked: bool = False,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", "chip")
        button.setCheckable(True)
        button.setChecked(checked)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: slot(key))
        group.addButton(button)
        layout.addWidget(button)
        registry[key] = button
        return button

    @staticmethod
    def _separator() -> QLabel:
        """分隔用的竖线（不是控件，纯视觉分组）。"""
        line = QLabel("│")
        line.setProperty("role", "muted")
        return line

    def _build_body(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._canvas = CardCanvas()
        self._canvas.setProperty("role", "card-canvas")
        self._rows_layout = QVBoxLayout(self._canvas)
        self._rows_layout.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg
        )
        self._rows_layout.setSpacing(TOKENS.spacing.lg)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setWidget(self._canvas)
        # 拖动/滚轮时让图标预加载歇一会儿：UnityPy 解析 bundle 是纯 Python 的，
        # 跟界面抢 GIL，抢出来的症状就是"滑条一卡一卡的"。
        self._scroll.verticalScrollBar().valueChanged.connect(
            lambda _value: self._icons.pause_preload()
        )
        splitter.addWidget(self._scroll)

        self._detail = UnitDetailPanel()
        self._detail.set_icons(self._icons)
        self._detail.card_failed.connect(self.card_failed)
        # 卡片底部选了"换成另一个单位"的改型（Options.ReplaceUnitId）就跳到那个单位：
        # 实测 645 的「小队装备配置」组里，另外两项指向的是别的单位，不是换装备。
        self._detail.variant_requested.connect(self._on_variant_requested)
        # 换「自定义选项」= 预览：左墙那张卡的成本徽章要**立刻**跟着变（右卡为准）。
        # 只改那一个数字，**不整屏 refresh**（用户对卡顿敏感）。
        self._detail.option_preview_requested.connect(self._on_option_preview)
        splitter.addWidget(self._detail)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1000, TOKENS.size.detail_panel_width])
        return splitter

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def set_instance(self, instance) -> None:
        self._instance = instance
        self._country_id = None
        self._include_hidden = False
        self._include_hidden_button.blockSignals(True)
        self._include_hidden_button.setChecked(False)
        self._include_hidden_button.blockSignals(False)
        self._specialization_id = None
        self._category = None
        self._selected_unit = -1
        self._detail.set_instance(instance)
        # 变更集与数据源**同生共死**：换数据源必须新建一个（旧的按 Id 寻址会指错行）。
        self._changeset = Changeset(instance) if instance is not None else None
        self._detail.set_changeset(self._changeset)
        if self._changeset is not None:
            # 改完就重跑校验（全量约 77ms）—— 角标必须跟着改动走，
            # 否则用户看到的是一个说他「没问题」的旧结果。
            self._changeset.subscribe(self._refresh_findings)
        # 基线 = **打开时**跑的那一遍（在任何改动之前）。
        # 引擎先建好：它原来是惰性建的（在 _refresh_findings 里），
        # 而基线要在订阅之前算出来，否则第一遍订阅回调会把基线当成"现状"。
        if instance is not None and self._engine is None:
            self._engine = RuleEngine()
        self._baseline = self._engine.validate(instance) if self._engine else None
        # ❗ 把刚算出来的那份**当现状传进去**：中间一行代码都没动过数据，
        #    再跑一遍全库校验（实测约 133ms）只会得到一模一样的结果
        #    （_work/probe_validate_invariant.py 实测逐字段一致）。
        self._refresh_findings(report=self._baseline)
        self._rebuild_country_chips()
        self._rebuild_spec_chips()
        self.refresh()
        self._autoselect_first()
        self._defer_preload()

    def _defer_preload(self) -> None:
        """把图标预载排到**事件循环下一轮**，别卡在"打开数据集"那一下。

        ❗ 预载本身只是**排队**（真正的取图走低优先级后台队列，见 preload_icons 的
        说明），但"排队"这一步要扫一遍全部图标键 —— 实测 **0.37s**
        （preload_all -> _plan）。开数据集时同步做，用户就是白等 0.37 秒；
        而它什么时候排上都行：排上之前卡片按需取图，本来就那样。

        ❗ 用**成员定时器**而不是 `QTimer.singleShot(0, self.preload_icons)`：
        后者在"控件已经被删掉、定时器才到点"时会去碰一个已经销毁的 C++ 对象
        （这仓里为这个形状吃过亏，见 widgets/detach.py 的说明）。
        """
        if self._preload_timer is None:
            self._preload_timer = QTimer(self)
            self._preload_timer.setSingleShot(True)
            self._preload_timer.timeout.connect(self.preload_icons)
        self._preload_timer.start(0)

    def preload_icons(self) -> int:
        """把当前实例的**全部**图标排进后台预加载。返回排进去的数量。

        为什么要预加载：不预加载的话，用户每滚到一屏新卡都要现开 bundle
        （立绘包 304 MB、DLC 包 428/724 MB），滚动会一顿一顿。
        预加载只灌磁盘缓存、走低优先级队列，界面该响还是立刻响。
        """
        if self._instance is None:
            return 0
        from ba_mod_maker.core.assets import icon_keys

        return self._icons.preload_all(icon_keys(self._instance))

    def select_country(self, country_id: int | None) -> None:
        self._country_id = country_id
        button = self._country_buttons.get(country_id)
        if button is not None:
            button.setChecked(True)
        # 换阵营后专精列表会变（未分类下是全部专精），选中的专精可能不再适用。
        self._specialization_id = None
        self._rebuild_spec_chips()
        self.refresh()

    def select_specialization(self, specialization_id: int | None) -> None:
        self._specialization_id = specialization_id
        self._check_spec_chip(specialization_id)
        self.refresh()

    def set_filter_text(self, text: str) -> None:
        self._search.setText(text)

    def refresh(self) -> None:
        """按当前的国家 / 专精 / 类别 / 过滤词重建卡片网格。"""
        if self._instance is None:
            self._write_rows([], "尚未加载数据源。")
            return

        only_unassigned = self._specialization_id == UNASSIGNED_ONLY
        specialization_id = None
        if self._specialization_id not in (None, ALL_SPECIALIZATIONS, UNASSIGNED_ONLY):
            specialization_id = self._specialization_id

        sheet = build_armory_sheet(
            self._instance,
            specialization_id=specialization_id,
            country_id=self._country_id,
            only_unassigned=only_unassigned,
            include_hidden=self._include_hidden,
        )
        # 表名不单独显示：下拉里当前那一项就是它（合并成一行时去掉的重复信息）。
        self._counts.setText(sheet.subtitle)

        rows = list(sheet.rows)
        if self._category is not None:
            rows = [row for row in rows if row.category == self._category]
        if self._filter_text:
            needle = self._filter_text.casefold()
            rows = [
                type(row)(
                    category=row.category,
                    label=row.label,
                    cards=tuple(
                        card
                        for card in row.cards
                        if needle in card.display_name.casefold()
                        or needle in card.hud_name.casefold()
                    ),
                )
                for row in rows
            ]
            rows = [row for row in rows if row.cards]

        hint = "" if rows else "当前条件下没有单位。换个专精或清掉过滤词试试。"
        self._write_rows(rows, hint)
        # 整屏重建会把徽章/卡名都刷回缺省值；右卡还停在预览上的话要**重新贴回去**，
        # 不能一刷新就把用户选的配置抖掉。
        self._sync_wall_labels()
        self._ensure_selection_valid()

    # ------------------------------------------------------------------ #
    # 只读（测试与状态栏用）
    # ------------------------------------------------------------------ #

    @property
    def row_widgets(self) -> tuple[CategoryRow, ...]:
        return tuple(self._rows)

    @property
    def sheet_title(self) -> str:
        """当前这张表的名字 —— 「全部单位」/ 某个专精名 / 「未编入任何专精」。

        专精改成 chip 之后，名字取自那颗 chip 的提示（图标 chip 上没有文字，
        tooltip 与读屏名才是它的身份，见 _insert_spec_chip）。
        """
        if self._specialization_id == UNASSIGNED_ONLY:
            return UNASSIGNED_TITLE
        if self._specialization_id in (None, ALL_SPECIALIZATIONS):
            return "全部单位"
        button = self._spec_buttons.get(self._specialization_id)
        return button.toolTip() if button is not None else "全部单位"

    @property
    def counts_text(self) -> str:
        return self._counts.text()

    @property
    def include_hidden(self) -> bool:
        """「隐藏单位」开关的当前状态（测试用）。"""
        return self._include_hidden

    @property
    def visible_card_count(self) -> int:
        return sum(len(row.armory_row.cards) for row in self._rows)

    @property
    def detail_panel(self) -> UnitDetailPanel:
        """右栏那张单位卡片（设计-单位卡片复刻.md §10.1 ③ 替换了原来的详情面板）。"""
        return self._detail

    @property
    def icons(self) -> IconCache:
        return self._icons

    def _refresh_findings(self, report=None) -> None:
        """跑一遍规则引擎，把**新增**的命中按单位归好交给卡片（卡片上只画一个感叹号）。

        ❗ 判据是**差集**不是"有没有命中"：原版既有违规不报、不计数
        （15 条硬约定第 11 条）。基线与实例同生共死，在 set_instance 里取。
        文案里的裸 Id 也在这里换成名字（用户口径：界面不给用户看 Id）。
        """
        if self._instance is None:
            self._detail.set_findings({})
            return
        if self._engine is None:
            self._engine = RuleEngine()
        # report 可以由调用方递进来（刚算过的那份），省掉一次约 133ms 的全库校验。
        # 不传就自己算 —— 变更集订阅走的就是这条路（回调无参）。
        if report is None:
            report = self._engine.validate(self._instance)
        if self._baseline is None:
            self._baseline = report
        delta = unit_finding_delta(self._instance, self._baseline, report)
        self._detail.set_findings(
            {
                unit_id: tuple(_humanized(self._instance, finding) for finding in findings)
                for unit_id, findings in delta.items()
            }
        )

    @property
    def changeset(self):
        """当前变更集（卡片上的编辑都记在它里面）。"""
        return self._changeset

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _ensure_selection_valid(self) -> None:
        """选中项已经不在当前视野里时，改选第一张卡。

        实踩：从"全部单位"切到 VDV 专精后，详情面板还停在 M1A2 SEP v2 TUA——
        而它根本不在 VDV 编制里。**面板和网格说的不是同一件事**，
        用户会以为是自己改错了数据。
        """
        visible = {card.unit_id for row in self._rows for card in row.armory_row.cards}
        if self._selected_unit in visible:
            for row in self._rows:
                row.set_selected_unit(self._selected_unit)
            return

        for row in self._rows:
            cards = row.armory_row.cards
            if cards:
                self._on_card_clicked(cards[0].unit_id)
                return

        self._selected_unit = -1
        self._detail.clear()

    def _autoselect_first(self) -> None:
        """换数据源后自动选中第一张卡。

        为什么：游戏军械库永远有东西被选中，空着的详情面板看起来像坏了。
        **只在换数据源时做一次**——过滤/翻页时自动跳选会跟用户抢。
        """
        for row in self._rows:
            cards = row.armory_row.cards
            if cards:
                self._on_card_clicked(cards[0].unit_id)
                return

    def _rebuild_country_chips(self) -> None:
        for button in self._country_buttons.values():
            if button.property("chipKey") == "country":
                self._country_group.removeButton(button)
                detach(button)
        self._country_buttons = {None: self._country_buttons[None]}
        if self._instance is None:
            return

        layout = self._country_separator.parentWidget().layout()
        position = layout.indexOf(self._country_separator) + 1
        for country in list_countries(self._instance):
            if country.hidden:
                continue
            button = self._make_country_chip(country)
            self._country_group.addButton(button)
            layout.insertWidget(position, button)
            position += 1
            self._country_buttons[country.country_id] = button

        # 「未分类」**永远排在最右**（用户口径：没有国家的单位统一放这一格）。
        # 它不在 Countries 表里 —— 那一条是 viewmodel 造的。
        # 旗子见 families.UNCLASSIFIED_FLAG_KEY（那面问号旗在表里没有键）。
        country = unclassified_country()
        button = self._make_country_chip(country)
        self._country_group.addButton(button)
        layout.insertWidget(position, button)
        self._country_buttons[country.country_id] = button

    def _make_country_chip(self, country) -> QPushButton:
        """阵营 chip：**有游戏内国旗素材就用国旗**，没有才退回文字。

        素材是游戏本体的（Countries.FlagFileName → cache/icons/flags/，
        提取见 tools/extract_icons.py 与 ADR 0002 决定 4）。
        没跑过提取的机器上 pixmap() 返回 None —— 那时必须退回文字，
        绝不允许出现一个既没图标又没字的空按钮。
        """
        button = QPushButton()
        button.setProperty("role", "chip")
        button.setProperty("chipKey", "country")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)

        name = country.display_name
        pixmap = self._icons.pixmap(FLAG_FAMILY, country.flag_key) if country.flag_key else None
        if country.flag_key:
            self._country_flag_keys[button] = country.flag_key
        if pixmap is not None:
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(TOKENS.size.chip_flag_width, TOKENS.size.chip_flag_height))
            # 只有旗子没有字：名字要靠悬浮提示和读屏补回来，
            # 否则"那面旗是哪个国家"就只能靠认旗了。
            button.setAccessibleName(name)
        else:
            button.setText(name)
        button.setToolTip(name)

        country_id = country.country_id
        button.clicked.connect(lambda _=False, cid=country_id: self._on_country_chip(cid))
        return button

    def _refresh_flag_icons(self) -> None:
        """国旗图标迟到时补上（只补还没有图标的那些 chip）。

        没有图标就退回文字——**绝不允许出现既没旗又没字的空按钮**。
        """
        for button, key in list(self._country_flag_keys.items()):
            if not button.icon().isNull():
                continue
            pixmap = self._icons.pixmap(FLAG_FAMILY, key)
            if pixmap is None:
                continue
            button.setText("")
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(TOKENS.size.chip_flag_width, TOKENS.size.chip_flag_height))
        # 专精 chip 的图标同一批到货（同一个 bundle：nations&specs）。
        for button, key in list(self._spec_icon_keys.items()):
            if not button.icon().isNull():
                continue
            pixmap = self._icons.pixmap(SPEC_ICON_FAMILY, key)
            if pixmap is None:
                continue
            button.setText("")
            button.setIcon(QIcon(pixmap))
            size = TOKENS.size.chip_spec_icon
            button.setIconSize(QSize(size, size))

    def _rebuild_spec_chips(self) -> None:
        """专精 chip 行：**带游戏内图标**，插在**国家那一排之后**。

        用户口径（2026-10-03）：「**专精不是有图标吗？国家后面跟几个专精的图标。**」

        图标来自游戏本体（`Specializations.Icon` → `cache/icons/spec_icons/`，见
        core/assets/families.py 的 FAMILIES）。与国旗同一套做法：图是**异步**到的，
        这里先建好 chip（先写字），到了再补图标（`_refresh_flag_icons`）；
        取不到就只留文字 —— 绝不允许出现一个既没图又没字的空 chip。

        三颗特殊的：头一颗「全部专精」（= 不筛专精）、尾一颗「未编入任何专精」
        （= 原来下拉里的那一项，标题用 UNASSIGNED_TITLE 的原文）。
        """
        for button in self._spec_buttons.values():
            self._spec_group.removeButton(button)
            detach(button)
        self._spec_buttons = {}
        self._spec_icon_keys = {}
        if self._instance is None:
            return

        layout = self._spec_separator.parentWidget().layout()
        position = layout.indexOf(self._spec_separator) + 1
        position = self._insert_spec_chip(layout, position, ALL_SPECIALIZATIONS, "全部专精")
        for spec in list_specializations(self._instance, country_id=self._country_id):
            position = self._insert_spec_chip(
                layout,
                position,
                spec.specialization_id,
                spec.display_name,
                icon_key=spec.icon_key,
            )
        self._insert_spec_chip(layout, position, UNASSIGNED_ONLY, UNASSIGNED_TITLE)
        self._check_spec_chip(self._specialization_id)

    def _insert_spec_chip(
        self, layout, position: int, key: int, text: str, icon_key: str = ""
    ) -> int:
        """插一颗专精 chip 并返回"下一颗该插哪"。图标取不到就退回文字。"""
        button = QPushButton(text)
        button.setProperty("role", "chip")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(text)
        button.setAccessibleName(text)
        if icon_key:
            pixmap = self._icons.pixmap(SPEC_ICON_FAMILY, icon_key)
            if pixmap is not None:
                button.setText("")
                button.setIcon(QIcon(pixmap))
                size = TOKENS.size.chip_spec_icon
                button.setIconSize(QSize(size, size))
            else:
                # 图还没到货：记下来，IconCache.loaded 到了再补。
                self._spec_icon_keys[button] = icon_key
        button.clicked.connect(lambda _=False, sid=key: self.select_specialization(sid))
        self._spec_group.addButton(button)
        layout.insertWidget(position, button)
        self._spec_buttons[key] = button
        return position + 1

    def _check_spec_chip(self, specialization_id: int | None) -> None:
        """把选中态同步到 chip 上（None 与 ALL_SPECIALIZATIONS 都表示"不筛专精"）。"""
        key = ALL_SPECIALIZATIONS if specialization_id is None else specialization_id
        button = self._spec_buttons.get(key) or self._spec_buttons.get(ALL_SPECIALIZATIONS)
        if button is not None:
            button.setChecked(True)

    def _write_rows(self, rows, hint: str) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                detach(widget)
        self._rows.clear()

        for armory_row in rows:
            widget = CategoryRow(armory_row, icons=self._icons)
            widget.card_clicked.connect(self._on_card_clicked)
            # 双击 = 进单位编辑器（单击仍是"选中看卡"）。
            widget.card_activated.connect(self._on_card_activated)
            self._rows_layout.addWidget(widget)
            self._rows.append(widget)

        if hint:
            label = QLabel(hint)
            label.setProperty("role", "empty-hint")
            self._rows_layout.addWidget(label)
        self._rows_layout.addStretch(1)

        if self._selected_unit >= 0:
            for widget in self._rows:
                widget.set_selected_unit(self._selected_unit)

    # -- 槽 ------------------------------------------------------------ #

    def _on_include_hidden(self, checked: bool) -> None:
        """「隐藏单位」开关变了 —— 整屏重算（卡片集与三个计数都得跟着换作用域）。"""
        self._include_hidden = bool(checked)
        self.refresh()

    def _on_country_chip(self, country_id) -> None:
        self.select_country(country_id)

    def _on_category_chip(self, category) -> None:
        self._category = category
        self.refresh()

    def _on_filter_text(self, text: str) -> None:
        self._filter_text = text.strip()
        self.refresh()

    def _on_variant_requested(self, unit_id: int) -> None:
        """跳到改型指向的那个单位。

        目标单位可能**不在当前视野里**（比如它属于另一个专精），那就先退到"全部单位"
        再选，而不是让面板空着。
        """
        visible = {card.unit_id for row in self._rows for card in row.armory_row.cards}
        if unit_id not in visible:
            self._specialization_id = None
            self._category = None
            self._filter_text = ""
            self._search.blockSignals(True)
            self._search.setText("")
            self._search.blockSignals(False)
            self._check_spec_chip(None)
            chip = self._category_buttons.get(None)
            if chip is not None:
                chip.setChecked(True)
            self.refresh()
        self._on_card_clicked(unit_id)

    def _on_card_activated(self, unit_id: int) -> None:
        """双击一张卡：先按单击把它选中（左边卡墙/右卡都要跟上），再请求进编辑器。"""
        self._on_card_clicked(unit_id)
        self.unit_activated.emit(unit_id)

    def _on_card_clicked(self, unit_id: int) -> None:
        self._selected_unit = unit_id
        for widget in self._rows:
            widget.set_selected_unit(unit_id)
        self._detail.show_unit(unit_id, self._specialization_id)
        # 面板换单位会把选项预览清空 —— 左边那张卡同时要回到缺省值。
        # 这一句同时覆盖"换到别的单位"与"再换回来"两种走法（右卡此刻是缺省，左边就画缺省）。
        self._sync_wall_labels()
        self.unit_selected.emit(unit_id)

    # ------------------------------------------------------------------ #
    # 左墙（徽章 + 卡名）<- 右卡（单一来源）
    # ------------------------------------------------------------------ #

    def _on_option_preview(self, _modification_id: int, _option_id: int) -> None:
        """右卡换了自定义选项：**只改左墙那一张卡的数字与名字**。

        ❗ 这里刻意不调 `refresh()`：整屏重建 395 个控件，而这件事只影响一张卡
        （用户对卡顿敏感，见 docs/交接-本轮会话状态.md §3）。
        两个参数用不上 —— 一律从面板拿（`current_cost` / `current_title`），不再算第二遍。
        """
        self._sync_wall_labels()

    def _card_widget(self, unit_id: int) -> UnitCard | None:
        """当前卡墙里那个单位的卡片控件；不在视野里就 None。"""
        for row in self._rows:
            widget = row.card_widget(unit_id)
            if widget is not None:
                return widget
        return None

    def _sync_wall_labels(self) -> None:
        """把左墙的**成本徽章、卡名与缩略图**对齐右卡。**只改这三个显示，不重建卡片。**

        口径一句话（用户："以右边卡片为准"）：

        - 面板正在显示的那个单位 —— 画 `detail_panel.current_cost` /
          `detail_panel.current_title` / `detail_panel.current_icon_key`（含选项预览）；
        - 其余每一张卡 —— 画 `build_armory_sheet` 算出来的缺省值。

        为什么名字也要跟：实机换装会改名字，而且**左列表卡跟着改** ——
        PT-76 选 AU-220M 之后左右一起变成 PT-76-57（docs/实机-03a/03b）；
        缺省态也一样（BTR-82 -> BTR-82A、T-72B -> T-72B1、PT-76 -> PT-76B）。

        为什么缩略图也要跟：`Options.ThumbnailOverride` 非空、且不换单位的选项有 131 条
        （换单位的那些由 `variant_requested` 跳到那个单位，看到的本来就是对的图）。

        三条路都走这一个函数，所以"左边跟着右边变"只有一份实现：
        换选项（`_on_option_preview`）、换单位（`_on_card_clicked`）、整屏重建（`refresh`）。
        """
        unit_id = self._detail.current_unit_id()
        cost = self._detail.current_cost
        title = self._detail.current_title
        icon_key = self._detail.current_icon_key
        show = unit_id >= 0 and cost is not None
        for row in self._rows:
            row.reset_card_labels(except_unit=unit_id if show else -1)
        if show:
            widget = self._card_widget(unit_id)
            if widget is not None:
                widget.set_cost(cost)
                if title:
                    widget.set_name(title)
                if icon_key is not None:
                    widget.set_icon_key(icon_key)


def _humanized(instance, finding: Finding) -> Finding:
    """把命中文案里的裸 Id 换成名字（用户口径：界面不给用户看 Id）。"""
    return Finding(
        rule_id=finding.rule_id,
        severity=finding.severity,
        table=finding.table,
        message=humanize_ids(instance, finding.message),
        row_ids=finding.row_ids,
        unit_ids=finding.unit_ids,
    )
