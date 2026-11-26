"""单位卡片（军械库右栏）—— **只展示信息**，一个编辑入口都没有。

设计见 docs/设计-单位卡片复刻.md；数据全在 app/viewmodel/unit_card.py（零 Qt），
本文件只排版、只把"看一眼"需要的动作传出去。

外观照实机（用户口径：「学游戏 GUI，让它看起来大且清晰」）：

- **立绘**通栏（实机那张图就是顶到卡片两边的）；
- **数值行**：图标在上、数字在下，8 格一行、不画方框；
- **武器区两态**（用户口径："游戏中的这个卡，是有简略显示和详细显示模式的，复刻它们"）：

  - **简略（默认）**：一把武器一行，射程 / 携弹量 / 穿深 / 火力**并排对齐**；
  - **详细**：左竖排武器、右详情，**每一格数据一行「标签 …… 值」**
    （用户口径："要游戏里的并列显示，而不是产品 GUI 里的一块一块的"）；

- **底部「自定义选项」**：每组一行（组名 + 当前选择 + 成本），只读。

三条纪律（写在代码里，不靠自觉）：

1. **不自己算数**：卡片上任何一个数都来自 viewmodel，界面层出现算术就是错的。
2. **不填假值**：还没解出公式的详情行**不显示**，只在末尾用一行小字说明还有哪些行
   待反编译确认（未解项见 viewmodel 的 PENDING_DETAIL_ROWS）。
3. **颜色只从 tokens.py 来**（tests/test_theme.py 扫全 app/）。

❗ **这张卡不编辑任何东西**（用户明确要求："右侧的卡片就展示信息，取消掉所有的在卡片上
编辑信息的功能"；编辑另有安排）。所以这里没有输入框、没有下拉、没有加减按钮。
`set_changeset` 只剩一个用途：**别处改了数据之后把卡片刷新一遍**。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.armory.silhouettes import SilhouetteView, silhouette_index
from ba_mod_maker.app.theme.icons import IconCache, prepare_icon
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.unit_card import (
    COMPACT_COLUMNS,
    EVIDENCE_CONFIRMED,
    MAX_STATS_PER_LINE,
    ArmorZone,
    CardData,
    UnitCard,
    WeaponGroupRow,
    build_unit_card,
    compact_titles,
    slot_selected_option,
)
from ba_mod_maker.app.widgets.bench_kit import id_badge_text
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.flow_layout import FlowLayout
from ba_mod_maker.core.assets.families import (
    AMMO_ICON_FAMILY,
    OPTION_PICTURE_FAMILY,
    STAT_ICON_FAMILY,
    TARGET_ICON_FAMILY,
    UNIT_PORTRAIT_FAMILY,
    WEAPON_ICON_FAMILY,
)

#: 图标族键的**字面量只在 core/assets/families.py**（上面 import 进来的那几个）。
#: 从前这里各抄了一份字符串：抄错一个字母的症状是「这一格永远没图标」，而且不报错。
#: 立绘那一族在本文件里原来叫 PORTRAIT_FAMILY，保持这个名字不动调用点。
PORTRAIT_FAMILY = UNIT_PORTRAIT_FAMILY
#: 自定义选项那一排的图片（Options.OptionPicture，与武器图标同一批素材）。
OPTION_ICON_FAMILY = OPTION_PICTURE_FAMILY

#: 武器区的两种显示模式 —— 用户口径："游戏中的这个卡，是有简略显示和详细显示模式的，
#: 复刻它们"。简略 = 一把武器一行、列对齐；详细 = 左竖排武器 + 右侧逐条数据。
MODE_COMPACT = "compact"
MODE_DETAIL = "detail"

#: 校验角标的那两个 role。**必须与 theme/qss.py 里的选择器逐字一致** ——
#: 曾经这里是 f"badge-{worst}"，worst 只可能是 error/warning，于是常态那个角标
#: 拿到的是 `badge-warning`，而 QSS 里只有 `badge-warn`：**角标掉样式**（一个秃叹号）。
#: tests/test_ui_redesign.py::test_every_role_set_in_code_has_a_selector 守着这条。
#: （卡上那个立绘角标走另一对 role，见 editable_card.py 的 portrait-badge-*。）
_BADGE_ROLE_ERROR = "badge-error"
_BADGE_ROLE_WARN = "badge-warn"

#: 每族图标的**绘制盒**：(宽 = 高 × 这个系数, 高)，都是**逻辑**像素。
#: ❗ 不能拿一个正方形框 + KeepAspectRatio 打发：三族的原图比例差得很远 ——
#:   武器剪影 231×82（2.8:1）、弹药 174×36（4.8:1）、数值图标 ≈1.1:1。
#:   按正方形画，那把枪会变成一条 6px 高的小虫 —— 用户报的"武器图标看起来太小"就是它。
#: 盒子的高按实机量的来（实机那张卡上的剪影是 145×50 物理 = 97×33 逻辑）。
_ICON_BOX: dict[str, tuple[float, int]] = {
    WEAPON_ICON_FAMILY: (3.0, TOKENS.card.icon_weapon),
    AMMO_ICON_FAMILY: (4.9, TOKENS.card.icon_ammo),
    STAT_ICON_FAMILY: (1.5, TOKENS.card.icon_stat),
    TARGET_ICON_FAMILY: (1.0, TOKENS.card.icon_target),
    OPTION_ICON_FAMILY: (2.3, TOKENS.card.icon_option),
}

#: 族没登记时用的盒子。
_ICON_BOX_DEFAULT = (1.0, 18)

#: 「哪几族要水平翻」现在只有一份出处：core/assets/families.py 的
#: `IconFamily.mirrored` / `family_mirrors()`（武器 + 改型选项要翻，弹药别翻），
#: 界面统一走 app/theme/icons.py::prepare_icon —— 本文件不再自己抄一份。


#: 「装甲图」4 个部位压在立绘上的落位：(行, 列)。
#: 实机那张卡就是 2×2（docs/实机-04-T-72B.png）：上排 顶部|背面，下排 正面|侧面。
#: 0 = 贴左 / 贴上，1 = 贴右 / 贴下；具体坐标由 _PortraitFrame 在 resizeEvent 里给。
_ARMOR_CELLS: dict[str, tuple[int, int]] = {
    "top": (0, 0),
    "rear": (0, 1),
    "front": (1, 0),
    "side": (1, 1),
}

#: 这 4 块里，哪几块的两个数是**上下两行**（实机 3 张截图一致：正面 / 背面 上下排，
#: 顶部 / 侧面 并排 —— 与数字长短无关，2 位数的 BTR-82 也这么排）。
_ARMOR_STACKED = frozenset({"front", "rear"})

#: 「装甲图」数字的描边不透明度（0-255）。实机是 TMP 的黑色 outline：
#: 数字压在立绘照片上，亮底（天空 / 雪地）没有描边就糊成一片。
_ARMOR_OUTLINE_ALPHA = 215

#: 「放大 / 缩小」的档位（百分比）。100 = 卡片铺满整栏（最大）。
#:
#: 语义是**整张卡等比缩放**：字号（theme/qss.py 的 [right-card] 块）+ 图标盒 +
#: 内容宽度（立绘跟着 heightForWidth 一起缩）三样用同一个系数 —— 于是任何一个档位
#: 下的版式都与 100% 同构，不会缩出第二种排法。
#:
#: ❗ 只有单位编辑器那张只读卡画这排按钮（zoom_levels）；军械库那张是空的，
#: 一颗按钮都不出现（用户口径："军械库设计还是原来的好"）。
ZOOM_LEVELS: tuple[int, ...] = (100, 90, 80, 70)

#: 缩放后字号的下限（px）。70% 那一档按比例算，最小的那几号是 6px ——
#: 汉字在 6px 上就是一团墨点（实测），所以触底就停在 8px。
#: 这是**刻意**的，不是漏乘：等比缩放到读不出来就没有意义了。
ZOOM_FONT_FLOOR = 8


def icon_box(family: str) -> QSize:
    """这一族图标的绘制盒（逻辑像素）。

    **尺寸必须是固定的**：图标是异步到的，控件得先按这个盒子把位置占好，
    不然图一到货整张卡就跳一下。图在盒子里按比例居中。
    """
    width_factor, height = _ICON_BOX.get(family, _ICON_BOX_DEFAULT)
    return QSize(round(height * width_factor), height)


def link_tooltip(row) -> str:
    """「这一行是谁」的悬浮提示（表#Id + 落点）。换/克隆都按它说明。"""
    tip = f"{row.table}#{row.row_id}"
    if row.note:
        tip = f"{row.note} · {tip}"
    if getattr(row, "link", None) is not None:
        tip += f"\n本单位引用它的那一格：{row.link}"
    return tip


def id_badge(ref_id: int) -> QLabel:
    """`#Id` 小徽章（口径只有一份）。

    用户口径（2026-10-01）：「所有出现诸如单位、武器、弹药、炮塔 ID 的时候，
    都在右边显示其 ID」。写什么由 `bench_kit.id_badge_text` 定 —— 与中栏 ①②③④
    那四处**同一份实现**：取不到 / `0` 给空串，空 QLabel 不占宽。

    ❗ 只负责"造一个徽章"；**摆在哪、要不要抠掉别处的同一个 Id** 由调用方定
    （§6 那条"只出现一次"：同一个 Id 不许在同一行上出现两遍）。
    """
    badge = _label(id_badge_text(ref_id), "entry-id")
    badge.setToolTip("这一行指向的那一行的 Id")
    return badge


def card_id_badge(row) -> QLabel:
    """卡片上一行（`row` 上带 `row_id` 的那种）右边那一段 —— 三处 `_link_row` 调它。"""
    return id_badge(getattr(row, "row_id", 0))


def _label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setProperty("role", role)
    # ❗ 不换行的 QLabel 的 minimumSize 就是整段文字的宽度——一句长说明会把整个
    # 面板撑得放不进 430px 的栏（实测：待确认行的清单让面板最小宽变成 645px）。
    widget.setWordWrap(wrap)
    return widget


class CardNavLabel(QLabel):
    """卡片上一块**可点**的东西（块 4/5/8：点一层 -> 中栏换成那一行）。

    ❗ 为什么不是 QPushButton：只读卡上**一颗按钮都不许有**（显示开关除外）——
    tests/test_relation_tree.py::test_card_has_no_clickable_editing_entry_point 与
    tests/test_ui_redesign.py::test_card_is_readonly_* 都扫 findChildren(QPushButton)。
    这里用 QLabel + 现成的 role：悬浮变色那一条样式白拿，读起来也像"一行字"。

    role 由调用方给（几个都有选择器，见 theme/qss.py）：
    `detail-link` = 一行字 / `ability-chip` = 有东西的格子 / `card-note` = 空槽那种弱化字。
    """

    activated = Signal()

    def __init__(
        self,
        text: str,
        role: str,
        *,
        tooltip: str = "",
        wrap: bool = False,
        swallow: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        self.setWordWrap(wrap)
        #: True = 点中它时**吃掉**这一下（不再冒泡给宿主那一行）。见 mouseReleaseEvent。
        self._swallow = swallow
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """左键**在字上松开**才算点了它（按住拖出去松手不算 —— 与按钮同义）。

        `swallow=True`：这一下**到此为止**（accept，不再冒泡给父控件）——
        它挂在"自己也会发 activated"的宿主行里（改装槽那一行的 `↗`），
        不吞的话一次点击会同时"选中它"和"去看它"，两个动作一起发生。
        """
        inside = self.rect().contains(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and inside:
            self.activated.emit()
            if self._swallow:
                event.accept()
                return
        super().mouseReleaseEvent(event)


class CardNavRow(QFrame):
    """卡片上**整行**可点的一行（点名字、点空白、点右边那串灰字都是同一个动作）。

    同 CardNavLabel：不是 QPushButton。行内那个名字用 CardNavLabel(role=detail-link)
    —— 悬浮变蓝的那一下反馈来自它（QSS 里没有 option-row:hover 那一条）。
    """

    activated = Signal()

    def __init__(self, role: str = "option-row", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        inside = self.rect().contains(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and inside:
            self.activated.emit()
        super().mouseReleaseEvent(event)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            detach(widget)
        elif item.layout() is not None:
            _clear(item.layout())


def _tree_of(root: QWidget) -> list[QWidget]:
    """一棵控件树的**确定性**展开（自己在最前，然后按 findChildren 的顺序）。

    用途只有一个：判"两块是不是同构"（见 _same_shape）。同构才敢少动控件。
    """
    return [root, *root.findChildren(QWidget)]


def _same_shape(left: QWidget, right: QWidget) -> bool:
    """两棵控件树是不是**同构**：同样是哪些类、同样几个、同样的 role、同样的顺序。

    为什么需要它：_on_changes 要判"这一块能不能只换字、不动控件"。
    不同构（多一个少一个标签）就只能整块重来 —— 硬换字会把字写到错的那一格上，
    那是比"闪一下"更坏的 bug（与 bench_squad.reload_values 那条
    「行对不上就退回重建」同一条）。
    """
    old_tree, new_tree = _tree_of(left), _tree_of(right)
    if len(old_tree) != len(new_tree):
        return False
    return all(
        type(a) is type(b) and a.property("role") == b.property("role")
        for a, b in zip(old_tree, new_tree, strict=True)
    )


def _copy_display_state(old: QWidget, new: QWidget, *, is_root: bool = False) -> None:
    """把 new 那个控件"画出来的样子"搬到 old 上（控件本身不换）。

    ❗ **图不在这里搬**：图是异步到的，new 手上很可能还是空的，搬过来会把
    old 上已经贴好的图擦掉。图统一由调用方把"等图到货"的队列指回 old 再贴一遍
    （见 _refresh_card_in_place），那条路缓存里有就立刻贴、没有就排队等，两种都对。

    ❗ is_root=True 时**不许碰可见性**：新控件那一份是**没有父控件的顶层控件**，
    而 Qt 的 isHidden() 对"从没 show 过的顶层控件"返回 True —— 照搬就会把
    真卡上那一整块 setVisible(False) 藏起来（实测：整块 detail-card 消失，
    被"就地刷 == 整块重建"那条用例当场抓住）。
    """
    for raw_name in new.dynamicPropertyNames():
        name = str(raw_name)
        old.setProperty(name, new.property(name))
    old.setToolTip(new.toolTip())
    old.setEnabled(new.isEnabled())
    if not is_root and old.isHidden() != new.isHidden():
        old.setVisible(not new.isHidden())
    if isinstance(new, QLabel):
        old.setText(new.text())
        old.setAlignment(new.alignment())
        old.setWordWrap(new.wordWrap())
    elif isinstance(new, QPushButton):
        old.setText(new.text())
        old.setCheckable(new.isCheckable())
        old.setChecked(new.isChecked())


def _transplant(old: QWidget, new: QWidget) -> dict[int, QWidget]:
    """把 new 那棵树**画出来的样子**搬进 old 那棵树，返回 new -> old 的对应表。

    调用前必须先用 _same_shape 判过同构（不同构硬搬会把字写到错的那一格上）。
    """
    pairs = list(zip(_tree_of(old), _tree_of(new), strict=True))
    for index, (a, b) in enumerate(pairs):
        # 第 0 对是块自己（详见 _copy_display_state 的 is_root 说明）。
        _copy_display_state(a, b, is_root=index == 0)
    for row in old.findChildren(_StatRow):
        row.relayout()
    return {id(b): a for a, b in pairs}


class _StatRow(QWidget):
    """属性那一排：**放得下就两端对齐一行，放不下就折行**。

    为什么要有它（用户报的"右边的卡片显示不全、底部冒横向滚动条"）：
    8 格数值在一个 QHBoxLayout 里，最小宽 = 各格最小宽之和 —— 实测 645 是 374、
    M1A2 是 379，而只读卡的视口只有 ~384（面板收到最小宽 390 时只剩 ~355）。
    差这十几像素，整张卡就被顶出面板 -> 底部横向滚动条 + 立绘右侧那两个装甲数字被裁。
    QHBoxLayout 不会折行，所以这里自己摆：一行放得下就两端对齐（用户口径
    "希望是两端对齐，而不是左对齐"），放不下就按格间距折行。

    ❗ 军械库那张卡不走这里（它仍是原来那个 QHBoxLayout）—— 用户口径
    "军械库设计还是原来的好"，见 UnitCardPanel._stats_section。
    """

    def __init__(self, chips: list[QWidget], spacing: int) -> None:
        super().__init__()
        self.setProperty("role", "card-flow")
        for chip in chips:
            chip.setParent(self)
        self._chips = list(chips)
        self._spacing = int(spacing)
        # 先按"一行"占位，第一次 resize 时按真实宽度定下来（见 _layout_chips）。
        self._height = max((chip.sizeHint().height() for chip in self._chips), default=0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # -- 尺寸 ------------------------------------------------------------ #

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        """最小宽 = **最宽的那一格**（折行之后总放得下）。"""
        widest = max((chip.sizeHint().width() for chip in self._chips), default=0)
        return QSize(widest, self._height)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        width = sum(chip.sizeHint().width() for chip in self._chips)
        width += self._spacing * max(0, len(self._chips) - 1)
        return QSize(width, self._height)

    # -- 排法 ------------------------------------------------------------ #

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._layout_chips()

    def _rows(self, width: int) -> list[list[QWidget]]:
        """贪心折行：这一行还塞得下就塞，塞不下换行。"""
        rows: list[list[QWidget]] = []
        current: list[QWidget] = []
        used = 0
        for chip in self._chips:
            need = chip.sizeHint().width()
            if current and used + self._spacing + need > width:
                rows.append(current)
                current, used = [chip], need
                continue
            used += need if not current else need + self._spacing
            current.append(chip)
        if current:
            rows.append(current)
        return rows

    def relayout(self) -> None:
        """就地换字之后重排一次：字宽变了（"99" -> "100"）几何得跟着走。

        不重排的话 Chip 还是老宽 —— 一行里就会叠在一起（resizeEvent 不一定触发：
        外层宽度没变时 Qt 不会调它）。
        """
        self._layout_chips()

    def _layout_chips(self) -> None:
        width = self.width()
        rows = self._rows(width)
        y = 0
        for row in rows:
            heights = [chip.sizeHint().height() for chip in row]
            row_height = max(heights, default=0)
            sizes = [chip.sizeHint().width() for chip in row]
            if len(rows) == 1 and len(row) > 1:
                # 一行放得下 -> **两端对齐**：首格贴左、末格贴右、格间距均分（与老版式同观感）。
                gap = (width - sum(sizes)) / (len(row) - 1)
                x = 0.0
                for chip, size, height in zip(row, sizes, heights, strict=True):
                    chip.setGeometry(round(x), y, size, height)
                    x += size + gap
            else:
                x = 0
                for chip, size, height in zip(row, sizes, heights, strict=True):
                    chip.setGeometry(x, y, size, height)
                    x += size + self._spacing
            y += row_height
        if y != self._height:
            self._height = y
            # 高度只在真的变了时才钉（钉高度会再触发一次 resize，不判就会来回弹）。
            self.setFixedHeight(y)
            self.updateGeometry()


class _WeaponArt(QFrame):
    """武器那一格：×N 徽章 + **大剪影** + 名字（实机那张卡就是这么排的）。

    为什么不用 QPushButton：按钮会把图标和文字挤成一行、还只能按统一的小尺寸画。
    实机那把枪是 **145×50 物理**的大剪影（= 97×33 逻辑），而武器图原图是 231×82
    （2.8:1）—— 塞进一个正方形小按钮，枪就只剩一条 6px 高的虫（用户报的"武器图标太小"）。
    """

    #: 参数：weapon.group_key
    clicked = Signal(int)

    def __init__(self, panel: UnitCardPanel, weapon: WeaponGroupRow) -> None:
        super().__init__(panel)
        self._key = weapon.group_key
        self.setProperty("role", "weapon-art")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(panel.weapon_tooltip(weapon))
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        badge = _label(f"×{weapon.count}", "weapon-count")
        badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # ❗ **先 addWidget，再 setVisible** —— 顺序反了就是用户报的那个「小弹窗」。
        #    `_label()` 造出来是**无父控件**的；此刻 setVisible(True) 会让 Qt 把这一格
        #    当成真正的顶层窗映射出来（class=Qt6112QWindowIcon、202x79 —— 202x79 是
        #    「光板顶层窗」的系统最小尺寸，**不是**这一格的尺寸；落点固定在屏幕
        #    (790,279)，不跟光标走）。下一行 addWidget 才把它收进布局 ——
        #    于是用户只看到闪一下、客户区还没来得及画。
        #    实测：无父 QLabel 走一遍「setVisible(True) -> addWidget」必出这个窗；
        #    反过来走一遍（先 addWidget 再 setVisible）一个窗都不出。
        #    武器数量 >1 的单位**每次单击卡片**都会走到这里。
        box.addWidget(badge)
        badge.setVisible(weapon.count > 1)

        art = QLabel()
        art.setFixedSize(panel.icon_box(WEAPON_ICON_FAMILY))
        art.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        panel.queue_icon(art, WEAPON_ICON_FAMILY, weapon.icon)
        box.addWidget(art)

        # 只读卡（fit_to_width）里名字**允许折行**：不折的话"М2 Browning"这种长名字
        # 一个人就把武器那一列的最小宽顶到 132，整张表就放不下面板了。
        box.addWidget(_label(weapon.label, "weapon-name", wrap=panel.fit_to_width))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


class _CardComboBox(QComboBox):
    """卡片里的下拉。两件事与普通 QComboBox 不同：

    1. **滚轮不换项**（只有下拉展开时滚轮才管用）。
       自定义选项那几个下拉就长在滚动面板里，滚一下就把配置改了 —— 很容易误操作。
    2. **不按最长选项撑最小宽**。默认策略下，一个长选项（"RPG-7V · 火箭弹发射器"）
       会把最小宽顶到 212px，两个并排就把右栏顶出面板。
       标题照旧完整显示在弹出列表里，只是不再逼着布局变宽。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self.view().isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


class _PortraitFrame(QFrame):
    """立绘**通栏** + 「装甲图」压在四角 —— 实机那张卡就是这样的。

    为什么不用 QGridLayout 做重叠（试过）：立绘的高度是 `heightForWidth` 算的
    （框高 = 宽 / 定长宽比，见 silhouettes.PORTRAIT_ASPECT），而 Qt 对**跨格**子项
    不按 heightForWidth 算行高 —— 实测立绘被撑到 429px（该是 249），整张卡高了一半。
    所以这里照旧用 QVBoxLayout 排立绘，4 块装甲图**不进布局**，
    在 resizeEvent 里按角摆位（实机那 4 块也是钉在四角的）。
    """

    #: 装甲图离卡片边缘的内缩（实机也留了一点，贴着边不好看）。
    INSET = TOKENS.spacing.sm

    def __init__(self, view: SilhouetteView, corners: list[tuple[QWidget, int, int]]) -> None:
        super().__init__()
        self.setProperty("role", "detail-card")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(view)

        # 立绘先加、装甲图后 setParent —— 后加的画在上面，于是字浮在立绘上。
        #
        # ❗ 这里**只 setParent，不 show()** —— 此刻 self 自己还没有父控件，
        #    对子控件 show() 会让 Qt 把**这一帧**当成独立的顶层窗显示出来：
        #    实机表现为"点卡片时闪一下的小弹窗"（用 EnumWindows 50Hz 轮询抓到的，
        #    窗口类 Qt6112QWindowIcon、202x79、标题 BA_Mod_Maker、就出现在光标下）。
        #    子控件会在这一帧真正 show 的时候由 Qt 自动带上（见 showEvent）。
        for widget, _row, _column in corners:
            widget.setParent(self)
        self._corners: tuple[tuple[QWidget, int, int], ...] = tuple(corners)
        #: 叠在四角之上的浮层（单位编辑器的校验条用它）。同一个角落有多条时
        #: **依次往上摞** —— 载具的右下角本来就占着「侧面」那块装甲图。
        self._overlays: list[tuple[QWidget, int, int]] = []

    def add_overlay(self, widget: QWidget, *, row: int = 1, column: int = 1) -> None:
        """往某个角落再叠一块（不参与布局，跟着 resizeEvent 走）。

        这一条是**运行时**加的，此时这一帧已经长在卡片里了 —— 所以 setParent 之后
        必须 show()（否则它会保持隐藏）；上面 __init__ 那条相反，见那里的注释。
        """
        widget.setParent(self)
        self._overlays.append((widget, row, column))
        if self.isVisible():
            widget.show()
        self._place_overlays()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """这一帧真的显示出来时，把压在四角的装甲图 / 浮层一起显示。

        它们是 setParent 进来的（setParent 会隐藏控件），而构造时**不能**调 show()
        —— 那一刻这一帧还是独立的顶层窗。所以显示这件事挪到这里做。
        """
        super().showEvent(event)
        for widget, _row, _column in (*self._corners, *self._overlays):
            if not widget.isVisible():
                widget.show()

    def clear_overlays(self) -> None:
        for widget, _row, _column in self._overlays:
            detach(widget)
            widget.deleteLater()
        self._overlays = []

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """按角摆位（row/column：0 贴左/贴上，1 贴右/贴下）。"""
        super().resizeEvent(event)
        placed: dict[tuple[int, int], QWidget] = {}
        for widget, row, column in self._corners:
            size = widget.sizeHint()
            x = self.width() - size.width() - self.INSET if column else self.INSET
            y = self.height() - size.height() - self.INSET if row else self.INSET
            widget.setGeometry(x, y, size.width(), size.height())
            placed[(row, column)] = widget
        self._place_overlays(placed)

    def _place_overlays(self, placed: dict | None = None) -> None:
        """浮层放在同角落已有控件**上方**（右下角先被装甲图占了就往上让）。"""
        anchors = dict(placed or {})
        if not anchors:
            for widget, row, column in self._corners:
                anchors[(row, column)] = widget
        for widget, row, column in self._overlays:
            size = widget.sizeHint()
            x = self.width() - size.width() - self.INSET if column else self.INSET
            previous = anchors.get((row, column))
            if previous is None:
                y = self.height() - size.height() - self.INSET if row else self.INSET
            else:
                y = previous.y() - size.height() - TOKENS.spacing.xs
            widget.setGeometry(x, y, size.width(), size.height())
            anchors[(row, column)] = widget


def _may_transplant(block: QWidget) -> bool:
    """这一块能不能"只换字、不动控件"。

    ❗ 判据是**机械的**：块里只要有一个"自己带信号 / 自己存了状态"的控件，就不敢只换字 ——
    字换得对，可它身上挂的回调还捕获着**旧的 (表, 行 Id)**（卡上点一行 = 中栏换成那一行），
    点下去会走到错的那一行；改装槽那一块更糟：会把 IsDefault 写到错的选项上。
    宁可让那一块整块重建（闪一下），也绝不能点错 / 写错。

    黑名单两类：

    - **可点的**：CardNavLabel / CardNavRow / _WeaponArt / QPushButton / QComboBox；
    - **自己存状态的**：_PortraitFrame（它把立绘与四角摆位存在自己身上）。

    于是"只换字"那一档实际覆盖：属性那一排、能力、班组成员 —— 它们全是死字，
    一块可点的东西都没有。
    """
    # ❗ findChildren 只认**一个**类型（PySide6 不收元组），逐个 isinstance 判。
    for child in block.findChildren(QWidget):
        if isinstance(
            child, (CardNavLabel, CardNavRow, _WeaponArt, _PortraitFrame, QPushButton, QComboBox)
        ):
            return False
    return True


def _requeue_icons(old_entries, new_entries, mapping: dict[int, QWidget]) -> list:
    """把新那份排的"等图到货"队列指回**活着的**老控件。

    图标键换了的先把旧图擦掉：不擦的话在新图到货之前那一格还挂着上一把枪的剪影。
    """
    was = {(id(label), family): key for label, family, key in old_entries}
    out: list = []
    for label, family, key in new_entries:
        target = mapping.get(id(label), label)
        if was.get((id(target), family), key) != key:
            target.setPixmap(QPixmap())
        out.append((target, family, key))
    return out


class UnitCardPanel(QFrame):
    """右栏那张单位卡片。未选单位时显示引导语，不显示空白框。

    **卡片本身只读**（不写任何数据），但底部「自定义选项」可以像游戏里那样换 ——
    换的是**预览**：成本 / 数值 / 武器跟着重算，一个字节都不落库（见 `_on_option_picked`）。
    """

    # ❗ 这里曾经有个 shows_open_button 开关（"卡片底部那个「在表格中打开」要不要出现"），
    #    2026-10-02 用户第二次点名要它消失：「**把在表格中打开这个按钮取消掉吧**」——
    #    这次连军械库那张卡一起删（第一次只删了单位编辑器那张）。卡片只读、导航交给
    #    双击卡 / 面包屑 / 数据表页，别再给卡片加"第一入口"。

    #: 卡片按面板可用宽度自适应：内容宽度 = 视口宽 × 档位，放不下的块折行。
    #:
    #: ❗ **默认关** —— 军械库那张卡一个像素都不动（用户口径："军械库设计还是原来的好"）。
    #: 打开它的只有单位编辑器那张只读卡（readonly_card.ReadOnlyCardPanel）——
    #: 那张卡的栏更窄，用户报的"右边的卡片显示不全、要手动左右滑"就是它。
    fit_to_width: bool = False

    #: 「放大 / 缩小」的档位（百分比）。**空 = 不画这排按钮**（军械库那张就是空的）。
    zoom_levels: tuple[int, ...] = ()

    #: 卡片装配/排版失败（参数：unit_id, 原因）。界面会把原因画在卡片上，
    #: shell 接它可以顺手写状态栏。**这条存在的理由见 `_show_failure` 的注释**。
    card_failed = Signal(int, str)
    #: 用户在下拉里换了一个改型选项（参数：modification_id, option_id）。
    #: **只改预览**：卡片重算，数据不动（写库另有安排，见 AGENTS §0 与设计 §10.1 ④）。
    option_preview_requested = Signal(int, int)
    #: 用户选了一个"换成另一个单位"的改型（参数：要换成的 unit_id）—— 这是导航。
    variant_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "detail-panel")
        self.setMinimumWidth(TOKENS.size.detail_panel_width)

        self._instance = None
        self._data: CardData | None = None
        self._icons: IconCache | None = None
        self._unit_id = -1
        self._specialization_id: int | None = None
        #: 变更集（core/edit/changeset.py）。卡片**不写**它，只订阅它：
        #: 别处（表格页 / 用户自己的编辑流程）改了数据，卡片跟着刷新一遍。
        self._changeset = None
        self._unsubscribe = None
        #: 单位 Id -> 落在他头上的校验命中（core/rules 引擎 + 归属反查）。
        self._findings: dict = {}
        #: 当前展开的武器组（按首个弹种 Id）。None = 未选中（简略模式下的常态）。
        self._expanded: int | None = None
        #: 武器区的显示模式。默认简略 = 游戏那张收起态的卡（用户定的第 5 条：默认收起）。
        self._mode: str = MODE_COMPACT
        self._card: UnitCard | None = None
        self._portrait_view: SilhouetteView | None = None
        self._portrait_key: str = ""
        #: 等图到货的标签：(控件, 族, 键)。
        self._icon_labels: list[tuple[QLabel, str, str]] = []
        #: 自定义选项的**预览**选择 {modification_id: option_id}。空 = 游戏缺省。
        #: 换单位时清空（每个单位有自己的配置），同单位重画时保留。
        self._selected: dict[int, int] = {}
        #: 「卡上选了一个改装槽选项」的写盘通道；None = 只预览（军械库那张只读卡）。
        #: 由**显式**接（set_option_writer），不看 changeset —— 见那个方法的说明。
        self._option_writer = None
        #: 等图到货的下拉项：(下拉, 项下标, 族, 键)。
        self._combo_icons: list[tuple[QComboBox, int, str, str]] = []
        #: 「放大 / 缩小」当前档位（百分比）。100 = 铺满整栏。
        self._zoom: int = 100
        #: 这排按钮（只有只读卡有；军械库那张是 None）。
        self._zoom_row: QWidget | None = None
        #: (档位, 按钮)：换档时要让选中态跟上（用例与状态栏都按它读"现在哪一档"）。
        self._zoom_buttons: list[tuple[int, QPushButton]] = []
        #: 简略模式的武器区这次用的是**表格**（True）还是**一行一条**（False）。
        #: 每一次重排都按可用宽度重新判（见 _use_grid_table）。
        self._built_grid: bool = True
        #: 重排重入闸：_apply_fit 里判出形态变了会再 _render 一次。
        self._fitting: bool = False
        #: 上一次画出来的正文各块：(署名, 控件, 这一块排队的图标, 这一块排队的下拉图标)。
        #: _on_changes 靠**署名**判"这一块到底变没变" —— 没变就一个控件都不碰
        #: （口径与 bench_page._structure_signature 同一条，用户提过三回：
        #  「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」）。
        self._block_keys: list = []
        self._block_widgets: list[QWidget] = []
        self._block_icons: list[list] = []
        self._block_combos: list[list] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.lg, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.sm)

        header = QHBoxLayout()
        header.setSpacing(TOKENS.spacing.md)
        self._title = QLabel()
        self._title.setProperty("role", "detail-title")
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)
        # 校验角标：就一个感叹号（用户定的第 9 条），问题在悬浮提示里。
        self._badge = QLabel("!")
        self._badge.setProperty("role", _BADGE_ROLE_WARN)
        self._badge.setVisible(False)
        header.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)
        self._cost = QLabel()
        self._cost.setProperty("role", "card-cost")
        header.addWidget(self._cost, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._subtitle = QLabel()
        self._subtitle.setProperty("role", "muted")
        self._subtitle.setWordWrap(True)
        root.addWidget(self._subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        #: 留一份引用：fit_to_width 要知道"正文实际有多宽"（视口宽，已扣掉竖滚动条）。
        self._scroll = scroll
        self._body = QWidget()
        # 透明角色见 qss.py：没有它，卡片间隙会被刷成 bg-base 而不是面板底色。
        self._body.setProperty("role", "detail-body")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(TOKENS.spacing.md)
        scroll.setWidget(self._body)
        root.addWidget(scroll, 1)

        # 「放大 / 缩小」：只有只读卡有（zoom_levels 非空）。军械库那张拿到的是空控件，
        # 不进版式也不占位 —— 那张卡一个像素都不动。
        self._zoom_row = self._zoom_bar()
        if self._zoom_row is not None:
            root.addWidget(self._zoom_row)
        if self.zoom_levels:
            # QSS 按这个属性选档位（theme/qss.py 的 [right-card] 块）。
            self.setProperty("cardsize", str(self._zoom))

        self.clear()

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def set_instance(self, instance) -> None:
        self._instance = instance
        self._data = CardData(instance) if instance is not None else None
        self._selected = {}
        self.clear()

    def set_icons(self, icons: IconCache | None) -> None:
        self._icons = icons
        if icons is not None:
            # 立绘要从 304 MB 的包里取，必然是异步的：到了再补一次。
            icons.loaded.connect(self._refresh_icons)

    # ------------------------------------------------------------------ #
    # 按面板宽度适配 + 「放大 / 缩小」（只读卡用；军械库那张这两条路都不走）
    # ------------------------------------------------------------------ #

    @property
    def zoom(self) -> int:
        """当前档位（百分比）。军械库那张恒为 100。"""
        return self._zoom

    @property
    def scale(self) -> float:
        """整张卡的等比系数（只有只读卡会不等于 1.0）。"""
        return self._zoom / 100.0

    def icon_box(self, family: str) -> QSize:
        """这一族图标在**当前档位**下的绘制盒（100% 时与模块级 icon_box 完全一致）。"""
        box = icon_box(family)
        if self._zoom == 100:
            return box
        return QSize(
            max(1, round(box.width() * self._zoom / 100.0)),
            max(1, round(box.height() * self._zoom / 100.0)),
        )

    def _px(self, value: int) -> int:
        """把令牌里的间距按档位折算（100% 时原样返回）。"""
        if self._zoom == 100:
            return int(value)
        return max(0, int(round(value * self._zoom / 100.0)))

    def set_zoom(self, percent: int) -> None:
        """切「放大 / 缩小」档位。改的只是**显示比例**，一个字节都不写。

        三样东西用同一个系数一起缩：字号（QSS 认面板上那个 cardsize 属性）、
        图标盒、内容宽度（立绘跟着 heightForWidth 一起缩）—— 于是每一档的版式
        都与 100% 同构，不会缩出第二种排法来。
        """
        if percent not in self.zoom_levels:
            raise ValueError(f"没有这个档位：{percent!r}（可选 {self.zoom_levels}）")
        if percent == self._zoom:
            return
        self._zoom = percent
        # 选中态：autoExclusive 只在**点击**时自动换 —— set_zoom 是程序调用的，
        # 不自己同步就会出现"按钮说 100%、卡片已经缩到 70%"（界面自相矛盾）。
        for level, button in self._zoom_buttons:
            button.setChecked(level == percent)
        self.setProperty("cardsize", str(percent))
        self._repolish()
        if self._card is not None:
            self._render(self._card)
        else:
            self._apply_fit()

    def _repolish(self) -> None:
        """改了 cardsize 之后让 QSS 重新算一遍。

        ❗ 只 unpolish 面板自己不够：字号规则是**子孙**选择器
        （QFrame[role="readonly-card"][cardsize="70"] QLabel[...]），
        每一层都要重算才会换字号 —— 实测不重算的话标签还挂着上一档的字体。
        """
        for widget in (self, *self.findChildren(QWidget)):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)

    def _zoom_bar(self) -> QWidget | None:
        """卡片底下那排「卡片大小 100% / 90% / 80% / 70%」。

        当前档位就是选中那颗按钮（chip 的 :checked 态，见 qss）。
        文案里**没有省略号** —— 用户明确骂过"换…"那类写法。
        `zoom_levels` 为空（军械库那张）时返回 None：不进版式、不占位。
        """
        if not self.zoom_levels:
            return None
        host = QWidget()
        host.setProperty("role", "card-zoom")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS.spacing.xs)
        row.addWidget(_label("卡片大小", "card-zoom"))
        row.addStretch(1)
        for level in self.zoom_levels:
            button = QPushButton(f"{level}%")
            button.setProperty("role", "chip")
            button.setCheckable(True)
            # 同父控件上 autoExclusive = 单选（点已选中的那颗不会把它取消掉）。
            button.setAutoExclusive(True)
            button.setChecked(level == self._zoom)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(
                "卡片铺满整栏（最大）"
                if level == 100
                else f"整张卡等比缩到 {level}%：字与图标一起变小，一栏里看得更多"
            )
            button.clicked.connect(lambda _=False, value=level: self.set_zoom(value))
            row.addWidget(button)
            self._zoom_buttons.append((level, button))
        return host

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """栏宽变了：把卡片重新收进新宽度（只读卡专用；军械库那张直接跳过）。"""
        super().resizeEvent(event)
        if self.fit_to_width:
            self._apply_fit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """第一次显示时也要收一次：进版式之前控件宽度没有意义（构造出来是 640）。"""
        super().showEvent(event)
        if self.fit_to_width:
            self._apply_fit()

    def _available_width(self) -> int:
        """正文实际能用的宽度：控件宽 - 根布局边距 - 竖滚动条。

        ❗ **不能读滚动视口的宽度**：resizeEvent 里布局还没跑，视口宽还是**上一次**
        的值 —— 实测踩过（栏从 640 收到 419 时它仍报 616），于是表格形态被误判成
        "放得下"，横向滚动条又回来了。按控件宽度推算，量的才是这次 resize 之后的宽度。
        竖滚动条扣得保守一点（卡片总比一屏高，它实际一直占着）。
        """
        layout = self.layout()
        width = self.width()
        if layout is not None:
            margins = layout.contentsMargins()
            width -= margins.left() + margins.right()
        if self._scroll is not None:
            width -= self._scroll.verticalScrollBar().sizeHint().width()
        return max(0, width)

    def _apply_fit(self) -> None:
        """把卡片收进当前宽度里（只读卡专用）。

        两件事：

        1. **内容宽度** = 视口宽 × 档位 —— 于是立绘（heightForWidth）跟着一起等比缩，
           整张卡就是一个缩小版的自己；100% 时铺满整栏；
        2. **折行**：简略模式的武器区在放不下的宽度上换成"一行一条"
           （见 `_use_grid_table`）—— 换形态要重排一次，用 `_fitting` 挡重入。
        """
        if not self.fit_to_width or self._fitting:
            return
        available = self._available_width()
        if available <= 0:
            return
        self._fitting = True
        try:
            card = self._card
            if (
                card is not None
                and card.weapons
                and self._built_grid != self._use_grid_table(card.weapons)
            ):
                self._render(card)
            target = max(1, int(available * self._zoom / 100.0))
            for index in range(self._body_layout.count()):
                widget = self._body_layout.itemAt(index).widget()
                if widget is not None:
                    widget.setMaximumWidth(target)
        finally:
            self._fitting = False

    def _use_grid_table(self, weapons) -> bool:
        """简略模式的武器区用哪种排法。

        - **军械库那张**（fit_to_width 关）：老的单行表格，一个像素都不动；
        - **只读卡**：先造一个**临时**表格量一次它要多宽（量完就删），放得下就用表格
          （列与列对齐 —— 用户口径"要游戏里的并列显示"）；放不下就换成一把武器一块、
          值带标签且能折行的排法（`_compact_stack`）—— 用户口径
          "不要让我手动向右向左滑"，宁可折行也不能把右边的数裁掉。
        """
        if not self.fit_to_width:
            return True
        available = self._available_width()
        if available <= 0:
            return True
        # ❗ 量宽那一份会往 _icon_labels / _combo_icons 里排队等图，量完必须还原 ——
        #    不然那些标签被 deleteLater 之后，_refresh_icons 还会去碰它们。
        queued = self._icon_labels
        queued_combos = self._combo_icons
        self._icon_labels = []
        self._combo_icons = []
        probe = None
        try:
            probe = self._compact_grid(weapons)
            probe.ensurePolished()
            needed = probe.minimumSizeHint().width() + 2 * self._px(TOKENS.spacing.md)
        finally:
            self._icon_labels = queued
            self._combo_icons = queued_combos
            if probe is not None:
                probe.deleteLater()
        return needed <= available

    def set_findings(self, findings) -> None:
        """接上校验结果（单位 Id -> 命中）。没接就没有角标。"""
        self._findings = dict(findings or {})
        self._refresh_badge()

    def _refresh_badge(self) -> None:
        """角标：有命中才显示，一个感叹号，问题在 tooltip 里。"""
        findings = self._findings.get(self._unit_id, ())
        if not findings:
            self._badge.setVisible(False)
            self._badge.setToolTip("")
            return
        role = (
            _BADGE_ROLE_ERROR if any(f.severity == "error" for f in findings) else _BADGE_ROLE_WARN
        )
        self._badge.setProperty("role", role)
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)
        self._badge.setToolTip("\n".join(f"[{f.severity}] {f.message}" for f in findings[:12]))
        self._badge.setVisible(True)

    def set_changeset(self, changeset, *, follow: bool = True) -> None:
        """接上变更集（core/edit/changeset.py）—— **只订阅，不写**。

        卡片自己不编辑（用户口径："右侧的卡片就展示信息"），但得跟着别处的改动走：
        订阅之后，任何一处改了数据，这里都会丢掉缓存重排一次。

        `follow=False`：**不自动订阅**，由外层自己按节奏调 `refresh_from_changes()`。
        为什么要这个开关：订阅回调是**同步**跑的（实测一次 68ms，整张卡重排）——
        用户在编成台点一格改值时，这 68ms 就压在点击那一下上（"点空白处会卡一下"）。
        编成台改成自己防抖（`BenchPage._schedule_card_refresh`），三栏那一页照旧自动订阅。
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._changeset = changeset
        if changeset is not None and follow:
            self._unsubscribe = changeset.subscribe(self._on_changes)
        if self._card is not None:
            self._render(self._card)

    def refresh_from_changes(self) -> None:
        """按最新数据重排一次（就是订阅回调那一段）。

        给「外层自己防抖」的调用方用（编成台）—— 语义与自动订阅完全一样，
        只是**什么时候跑由外层定**，不压在用户的点击上。
        """
        self._on_changes()

    @property
    def changeset(self):
        return self._changeset

    @property
    def body_blocks(self) -> tuple[QWidget, ...]:
        """正文里**画出来的**每一块（阅读序）。缺席的块（例：载具没有班组成员）不在里面。

        用例按它验那条口径：改一格之后，只有**真的变了的那一块**可以换控件，
        其余块的控件一个都不许动（AGENTS §0.7）。
        """
        return tuple(widget for widget in self._block_widgets if widget is not None)

    def _on_changes(self) -> None:
        """变更集动了（别处改的）：丢掉卡片数据的缓存，再**把那几块真的变了的**重画。

        ❗ 不丢缓存就会读到改动前那份筛结果 —— 实测踩过：插了一行 SquadMembers，
        卡片还显示 5 人。

        ❗ **语义一个字没变**（丢缓存 / 重排 / 失败要说出来），变的只是"重排多少"：
        整块重来改成逐块比署名，没变的块一个控件都不碰（见 _refresh_card_in_place）。
        """
        if self._data is None or self._unit_id < 0:
            return
        # ❗ 丢缓存的两条路只差一步：invalidate_for 会按**这一批改动到底碰了哪几列**
        #    判共享度要不要重建（改一格携弹量不该付一次整库共享度扫描，实测 10–37ms）。
        #    没有变更集（裸控件 / 用例直接刷）就退回一律标脏那条路。
        self._data.invalidate_for(self._changeset)
        try:
            card = build_unit_card(self._data, self._unit_id, selected=self._selected or None)
        except Exception as exc:  # noqa: BLE001 - 同 show_unit：失败要说出来
            self._show_failure(self._unit_id, exc)
            return
        if card is not None:
            try:
                self._refresh_card_in_place(card)
            except Exception as exc:  # noqa: BLE001
                self._show_failure(self._unit_id, exc)

    def current_unit_id(self) -> int:
        return self._unit_id

    @property
    def card(self) -> UnitCard | None:
        """当前装配出来的卡片数据（测试与状态栏用）。"""
        return self._card

    @property
    def current_cost(self) -> int | None:
        """右卡**当前**显示的成本（含选项预览）；没有卡片时 None。

        这是成本的权威值：左墙徽章跟着它走（见 armory_screen._sync_wall_costs），
        口径与 `unit_card.card_cost` 一致 —— 用户口径是"以右边卡片为准"。
        """
        return int(self._card.cost) if self._card is not None else None

    @property
    def current_title(self) -> str | None:
        """右卡**当前**抬头那个名字（含选项的名字覆盖）；没有卡片时 None。

        与 `current_cost` 同一地位：左墙那张卡的名字跟着它走。实机里也是这样 ——
        PT-76 换成 AU-220M 后，左列表卡与右卡抬头**一起**变成 PT-76-57。
        """
        return self._card.title if self._card is not None else None

    @property
    def current_icon_key(self) -> str | None:
        """右卡**当前**的缩略图键（含选项预览）；没有卡片时 None。

        与 `current_cost` / `current_title` 同一地位：左墙那张卡画的图跟着它走
        （`armory_screen._sync_wall_labels`）。口径是 `unit_card.unit_thumbnail_key` ——
        `Options.ThumbnailOverride` 非空且不换单位的选项有 131 条。
        """
        return self._card.icon_key if self._card is not None else None

    @property
    def current_portrait_key(self) -> str | None:
        """右卡**当前**的立绘键（含选项预览）；没有卡片时 None。

        与 `current_icon_key` 同一条口径（`unit_card.unit_portrait_key`）。
        换选项时 `_render` 会重建立绘那一段（`_portrait_section`）：新键先从内存/磁盘缓存取，
        取不到就先画剪影，`IconCache.loaded` 到了再补 —— 见 `_refresh_icons`。
        """
        return self._card.portrait_key if self._card is not None else None

    @property
    def expanded_weapon(self) -> int | None:
        return self._expanded

    @property
    def selected_options(self) -> dict[int, int]:
        """当前的**预览**选择 {modification_id: option_id}（空 = 游戏缺省）。"""
        return dict(self._selected)

    def set_option_writer(self, writer=None) -> None:
        """接上「卡上选了一个改装槽选项」的写盘通道；**不接 = 只预览**。

        参数 `writer(modification_id, option_id)`：由外层做写数据那一下
        （编辑器里是 `MainEditorPage` -> `EditorContext.set_value` -> 只进缓存 +
        重画；军械库那张卡没人接，于是它一行语义都没变：只展示、只预览）。

        ❗ **判据是"有没有这个显式通道"，不是 `changeset is None`**：
        军械库也接了一份 changeset（`armory_screen.set_instance` 里那句
        `_detail.set_changeset`，用途只是"别处改了把卡片刷新一遍"），
        拿它当"能不能写"的判据会让只读卡也开始写数据 ——
        用户口径（"右侧的卡片就展示信息"）与
        `tests/test_armory_screen.py::test_card_has_no_editing_entry_points`
        都钉死了那张卡不写。
        """
        self._option_writer = writer

    def set_selected_options(self, selected: dict[int, int]) -> None:
        """设定改型选项的预览。传空字典 = 回到游戏缺省。**不写任何数据。**"""
        self._selected = dict(selected)
        if self._card is not None and self._data is not None and self._unit_id >= 0:
            card = build_unit_card(self._data, self._unit_id, selected=self._selected or None)
            if card is not None:
                self._render(card)

    def clear(self) -> None:
        self._unit_id = -1
        self._card = None
        self._expanded = None
        self._portrait_view = None
        self._portrait_key = ""
        self._icon_labels = []
        self._combo_icons = []
        # 正文空了：逐块比署名的那份基线也一起清掉（不清的话下一次 _on_changes
        # 会拿一张空卡去比，判成"一块都没变"而什么都不画）。
        self._block_keys = []
        self._block_widgets = []
        self._block_icons = []
        self._block_combos = []
        self._title.setText("未选择单位")
        self._subtitle.setText("点一张卡片查看详情。")
        self._cost.setText("")
        self._cost.setVisible(False)
        self._badge.setVisible(False)
        _clear(self._body_layout)

    def show_unit(self, unit_id: int, specialization_id: int | None = None) -> None:
        """换单位。**失败也要说话** —— 见 `_show_failure`。"""
        if self._data is None:
            self.clear()
            return
        if unit_id != self._unit_id:
            # 换单位 = 回到游戏缺省。**同一个单位重画要保留预览** ——
            # 否则改一下别处的数据（变更集刷新）就把用户选的配置抖掉了。
            self._selected = {}
        try:
            card = build_unit_card(self._data, unit_id, selected=self._selected or None)
        except Exception as exc:  # noqa: BLE001 - 兜底：绝不能让面板停在别的单位上
            self._show_failure(unit_id, exc)
            return
        if card is None:
            self.clear()
            return
        self._unit_id = unit_id
        self._specialization_id = specialization_id
        # 换单位 = 回到默认收起态（用户定的第 5 条：默认收起，点武器才展开）。
        self._expanded = None
        self._ensure_selection()
        try:
            self._render(card)
        except Exception as exc:  # noqa: BLE001 - 同上
            self._show_failure(unit_id, exc)

    def _show_failure(self, unit_id: int, exc: BaseException) -> None:
        """装配/排版炸了：**清掉旧卡**，把原因写在卡片上，并把 traceback 打到终端。

        ❗ 为什么非要有这个（用户报的"战机那一类点不开、右边还是上一个单位"）：
        Qt 的槽里抛异常**不会**让程序崩，只往 stderr 打一行，界面留在原地 ——
        于是卡片显示的是**上一个单位**，看起来像"这一类单位的卡片显示不出来"。
        宁可显示"装配失败 + 原因"，也不要显示一个不属于它的卡片。
        """
        import traceback

        from ba_mod_maker.app import diag

        diag.failure("卡片装配失败", exc, unit=unit_id)
        reason = f"{type(exc).__name__}: {exc}"
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        where = f"{frame.filename.rsplit(chr(92), 1)[-1]}:{frame.lineno}"
        self.clear()
        self._unit_id = unit_id
        self._title.setText(f"Id {unit_id} · 卡片装配失败")
        self._subtitle.setText(f"{reason} @ {where}")
        box = QFrame()
        box.setProperty("role", "detail-card")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md, TOKENS.spacing.md
        )
        inner.setSpacing(TOKENS.spacing.xs)
        inner.addWidget(_label("这一格的卡片没法装配", "detail-section"))
        inner.addWidget(_label(f"{reason}\n出在 {where}", "card-note", wrap=True))
        inner.addWidget(
            _label(
                "多半是这一行数据被改坏了（缺行 / 类型不对）。终端里有完整 traceback；"
                "点下面的「在表格中打开」能看到原始行。",
                "card-note",
                wrap=True,
            )
        )
        self._body_layout.addWidget(box)
        self._body_layout.addStretch(1)
        traceback.print_exception(exc)
        self.card_failed.emit(unit_id, reason)

    def _sync_header(self, card: UnitCard) -> None:
        """抬头那几个字（名字 / 成本 / 副标题 / 角标）—— 它们本来就只换字，**永远就地写**。"""
        self._title.setText(card.title)
        self._cost.setText(str(card.cost))
        self._cost.setVisible(True)
        self._subtitle.setText(self._subtitle_text(card))
        self._refresh_badge()

    def _render(self, card: UnitCard) -> None:
        """按当前状态**整块**重排卡片正文。换单位、换显示模式、换档位都走这里。"""
        self._card = card
        self._portrait_key = card.portrait_key
        self._portrait_view = None
        self._icon_labels = []
        self._combo_icons = []
        self._block_keys = []
        self._block_widgets = []
        self._block_icons = []
        self._block_combos = []

        self._sync_header(card)

        _clear(self._body_layout)
        for key, build in self._section_plan(card):
            icons_at, combos_at = len(self._icon_labels), len(self._combo_icons)
            widget = build()
            # ❗ 署名 / 控件 / 队列三条表**按 plan 的下标对齐**：缺席的块（例：载具没有
            #    「班组成员」那一块）也占一格、控件记 None。从前缺席的块直接 continue，
            #    于是载具卡上 len(plan)=11 而 len(_block_keys)=10 —— 逐块比署名永远对不上，
            #    每次都退回整块重排（实测单位 21：kept 17/208，与改之前一模一样）。
            self._block_keys.append(key)
            self._block_widgets.append(widget)
            self._block_icons.append(self._icon_labels[icons_at:])
            self._block_combos.append(self._combo_icons[combos_at:])
            if widget is not None:
                self._body_layout.addWidget(widget)
        self._body_layout.addStretch(1)
        # 重排之后立刻把新控件收进当前宽度（只读卡；军械库那张这里是空跑）。
        self._apply_fit()

    # ------------------------------------------------------------------ #
    # 改一格不重建：逐块比署名（AGENTS §0.7）
    # ------------------------------------------------------------------ #

    def _refresh_card_in_place(self, card: UnitCard) -> None:
        """数据变了之后**只重画真的变了的那几块**，其余块一个控件都不碰。

        用户口径（提过三回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下
        很影响观感」「这我提了几回的问题了」。判据在 AGENTS §0.7：
        **控件零重建 且 值必须是新的** —— 只满足前一条是假绿。

        口径与先例完全一致（docs/交接-2026-09-30-界面批-图标树弹药.md:76/93）：

            **块的署名没变 -> 整块跳过；署名变了 -> 只重建那一块**（不是整张卡）。

        署名 = 这一块从卡上读的那几个字段（见 _section_plan）；卡上全是 frozen
        dataclass，值相等就等于"画出来的字相同"，所以跳过是**可证的**，不是图省事。

        ❗ 为什么不把"变了的那一块"也做成逐个 setText（更彻底的就地）：
        卡上每一块的行里都挂着 activated 信号，lambda **捕获了那一行的
        (表, 行 Id)**（点它 -> 中栏换成那一行）。表 / 行 Id 变了却只换字的话，
        点下去会导航到**错的那一行**（改装槽那一块更糟：会把 IsDefault 写到错的选项上）
        —— 那是比"闪一下"坏得多的 bug。所以变了的那一块整块重建，捕获的 Id 一起更新。
        """
        plan = self._section_plan(card)
        if not self._block_keys or len(plan) != len(self._block_keys):
            # 还没画过 / 块的个数变了（步兵<->载具、有没有编成块）-> 退回整块重排。
            self._render(card)
            return

        # 抬头那几个字先就地更新（它不属于任何一块）。
        self._card = card
        self._sync_header(card)
        self._portrait_key = card.portrait_key

        dirty = [
            index for index, (key, _build) in enumerate(plan) if key != self._block_keys[index]
        ]
        if not dirty:
            # 一块都没变：正文一个控件都不碰（这正是"改一格"的常态）。
            self._apply_fit()
            return

        icons_at, combos_at = len(self._icon_labels), len(self._combo_icons)
        fresh: list[tuple[int, QWidget | None, list, list]] = []
        for index in dirty:
            before_icons, before_combos = len(self._icon_labels), len(self._combo_icons)
            widget = plan[index][1]()
            fresh.append(
                (
                    index,
                    widget,
                    self._icon_labels[before_icons:],
                    self._combo_icons[before_combos:],
                )
            )
        gone = any(widget is None for _index, widget, _icons, _combos in fresh)
        appeared = any(self._block_widgets[index] is None for index, *_rest in fresh)
        if gone or appeared:
            # 某一块的有无变了（例：步兵 <-> 载具，编成块消失 / 出现）-> 退回整块重排。
            # 先把这一趟排队等图的控件摘掉，免得 _refresh_icons 去碰半成品。
            del self._icon_labels[icons_at:]
            del self._combo_icons[combos_at:]
            self._render(card)
            return

        # 新控件排的图标 / 下拉图标是接在**队尾**的：先摘掉，等会儿按块放回去。
        del self._icon_labels[icons_at:]
        del self._combo_icons[combos_at:]
        for index, widget, icons, combos in fresh:
            old = self._block_widgets[index]
            if _may_transplant(old) and _same_shape(old, widget):
                # 同构 + 块里没有可点的东西 -> **只换字**：老控件一个都不销毁。
                mapping = _transplant(old, widget)
                detach(widget)  # 新的那一份只是拿来算字的
                self._block_icons[index] = _requeue_icons(self._block_icons[index], icons, mapping)
                self._block_combos[index] = [
                    (mapping.get(id(combo), combo), position, family, key)
                    for combo, position, family, key in combos
                ]
                for target, family, key in self._block_icons[index]:
                    self._apply_icon(target, family, key)
            else:
                # 结构变了 / 块里有可点的东西 -> 整块重建（**只有这一块**，不是整张卡）。
                self._replace_block(index, widget)
                self._block_icons[index] = icons
                self._block_combos[index] = combos
        self._block_keys = [key for key, _build in plan]
        self._rebuild_icon_queue()
        self._apply_fit()

    def _replace_block(self, index: int, widget: QWidget) -> None:
        """把正文第 index 块换成新的一份 —— **只动这一块**，别的块连布局都不惊动。

        ❗ 位置按控件在布局里的**真实下标**取（indexOf），不按 plan 的下标：
        缺席的块占 plan 的一格但不占布局 —— 拿 plan 下标去 takeAt 会换错块。
        """
        old = self._block_widgets[index]
        position = self._body_layout.indexOf(old)
        self._body_layout.takeAt(position)
        self._body_layout.insertWidget(position, widget)
        detach(old)
        self._block_widgets[index] = widget

    def _rebuild_icon_queue(self) -> None:
        """按块把"等图到货"的两条队列拼回来。

        ❗ 不重建的话，被换掉那一块的旧控件还留在队列里 —— 图一到货
        _refresh_icons 就会去碰已经 deleteLater 的标签（实测那种崩法是
        RuntimeError: Internal C++ object already deleted）。
        """
        self._icon_labels = [entry for group in self._block_icons for entry in group]
        self._combo_icons = [entry for group in self._block_combos for entry in group]

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #

    @staticmethod
    def _subtitle_text(card: UnitCard) -> str:
        parts = [card.subtitle]
        if card.degraded:
            parts.append(
                "本地化降级：没读到游戏本地化包，中文标签用产品自带的（可能与本版游戏有出入）"
            )
        return " · ".join(part for part in parts if part)

    # ------------------------------------------------------------------ #
    # 排版
    # ------------------------------------------------------------------ #

    def _frame(self, role: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setProperty("role", role)
        layout = QVBoxLayout(frame)
        margin = self._px(TOKENS.spacing.md)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(self._px(TOKENS.spacing.sm))
        return frame, layout

    def _section_plan(self, card: UnitCard) -> list[tuple[object, Callable[[], QWidget | None]]]:
        """正文每一块：**(署名, 怎么建)** —— 两件事写在同一个地方，不给它们对不上的机会。

        「署名」= 这一块从卡上读的那几个字段（值级）。卡上全是 frozen dataclass，
        所以 署名相同 == 这一块会画出**逐字一样**的控件树 -> 整块跳过是**可证**的。
        改一格之后 _on_changes 就是拿新旧署名逐块比（见 _refresh_card_in_place）。
        ❗ 加块 / 改块时**必须同时改这里的署名** —— 署名漏了一个字段的症状是
        "那一格改了卡上不动"，而用例与 ruff 都看不出来。
        """
        return [
            (
                (
                    "portrait",
                    card.portrait_key,
                    card.icon_key,
                    card.category,
                    card.kind,
                    card.armor_zones,
                ),
                lambda: self._portrait_section(card),
            ),
            (("stats", card.stats), lambda: self._stats_section(card)),
            (
                ("armor", card.armor),
                lambda: self._link_section(
                    "🛡 装甲", (card.armor,) if card.armor else (), "这个单位没有装甲模板行。"
                ),
            ),
            (
                ("mobility", card.mobility),
                lambda: self._link_section(
                    "🚗 机动", (card.mobility,) if card.mobility else (), "这个单位没有机动行。"
                ),
            ),
            (
                ("sensors", card.sensors),
                lambda: self._link_section("📡 传感器", card.sensors, "这个单位没有传感器行。"),
            ),
            (
                ("transports", card.transports),
                lambda: self._link_section("🚚 运输", card.transports, "没有运输配对行。"),
            ),
            (("abilities", card.abilities), lambda: self._abilities_section(card)),
            (
                ("weapons", card.weapons, self._mode, self._expanded),
                lambda: self._weapons_section(card),
            ),
            (("loadout", card.loadout), lambda: self._loadout_section(card)),
            (
                (
                    "options",
                    card.options,
                    self._option_writer is not None,
                    tuple(self._is_selected_group(group) for group in card.options),
                ),
                lambda: self._options_section(card),
            ),
        ]

    def _build_sections(self, card: UnitCard) -> list[QWidget | None]:
        """按当前卡片数据把正文每一块建出来（顺序 = _section_plan 的顺序）。"""
        return [build() for _key, build in self._section_plan(card)]

    # -- 机动 / 传感器 / 运输（界面重做这一轮补出来的三块）--------------- #

    def _link_section(self, title: str, rows, empty: str) -> QWidget:
        """一块「能点进去看的那一行」：一行一个名字 + 右边小字。

        用户口径（界面重做定稿 块 3）："补到卡上，每样一块"——
        机动 / 传感器 / 运输 这三块原来卡上没有。
        点它 -> 右栏换成那一行的全部列（§1 第 4 条），所以在编辑器里它们是可点的按钮。
        """
        frame, layout = self._frame("detail-card")
        layout.addWidget(_label(title, "detail-section"))
        if not rows:
            layout.addWidget(_label(empty, "empty-hint", wrap=True))
            return frame
        for row in rows:
            layout.addWidget(self._link_row(row))
        return frame

    def _link_row(self, row) -> QWidget:
        """一块里的一行。基类只展示（军械库那张卡不给点、也不给换/克隆）。

        ❗ 只读卡（fit_to_width）里这一行**允许折行**：名字里带表名与长型号
        （"Tracks M1A2 Abrams 70kph"）时，不折的 QLabel 最小宽就是整行文字，
        一个人就能把卡片顶出面板。

        ❗ 名字右边常显 `#Id`（用户口径 2026-10-01）—— 原来它只在悬浮提示里，等于"看不见"。
        容器只是为了把这一段摆到名字右边；名字仍然独占拉伸位，折行口径一个字不变。
        """
        host = QWidget()
        box = QHBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.xs)
        name = _label(row.label, "detail-link", wrap=self.fit_to_width)
        name.setToolTip(link_tooltip(row))
        box.addWidget(name, 1)
        box.addWidget(card_id_badge(row), 0, Qt.AlignTop)
        return host

    # -- 立绘 ------------------------------------------------------------ #

    def _portrait_section(self, card: UnitCard) -> QWidget:
        """立绘**通栏** + 「装甲图」压在四角 —— 实机那张卡就是这样的。

        实机（docs/实机-04-T-72B.png / 05b-BTR82A / 04b-BRDM-3 三张一致）：
        立绘占满整块，4 个装甲部位浮在它上面 —— 上排 顶部|背面、下排 正面|侧面，
        每块是"部位名小字 + 动能（白）/ 破甲（橙）两个数字"。
        用户口径：「地面载具的装甲要像游戏里显示的那样」。

        实现：`_PortraitFrame` —— 立绘照旧用 QVBoxLayout 排（它的框高是
        heightForWidth 算的，**不能进网格**），4 块装甲图不参与布局、
        在 resizeEvent 里按角摆位。实机那 4 块也是钉在四角的。
        ❗ 试过 QGridLayout 重叠（立绘跨 2×2 + 4 块落四角）：Qt 对**跨格**子项
        不按 heightForWidth 算行高，实测立绘被撑成 429px（该是 249）——整张卡高了一半。
        没有装甲图（非载具 / 8 个数全零）时就是原来那张纯立绘。

        ❗ 不再显示"模型 XXX"：`Units.ModelFileName` 是全步兵共用的占位值
        （132 与 645 都是 `US_Rangers`），挂在俄国单位下面纯属误导。
        """
        view = SilhouetteView()
        self._portrait_view = view
        portrait = None
        if self._icons is not None and card.portrait_key:
            portrait = self._icons.pixmap(PORTRAIT_FAMILY, card.portrait_key)
        if portrait is not None:
            view.set_pixmap(portrait)
        else:
            view.set_silhouette(silhouette_index(card.category, card.kind))

        corners: list[tuple[QWidget, int, int]] = []
        for zone in card.armor_zones:
            cell = _ARMOR_CELLS.get(zone.key)
            if cell is None:
                # 数据层加了新部位而界面还没给它落位：**不画**（不瞎猜位置）。
                continue
            row, column = cell
            corners.append((self._armor_zone(zone), row, column))
        return _PortraitFrame(view, corners)

    def _armor_zone(self, zone: ArmorZone) -> QWidget:
        """装甲图里的一格：部位名小字在上，动能（白）/ 破甲（橙）两个数字在下。

        两个数并排还是上下排由部位决定（实机：正面/背面 上下排，顶部/侧面 并排），
        见 `_ARMOR_STACKED`。整块**居中**；不画底不画边 —— 实机就是浮在立绘上的字。
        """
        host = QWidget()
        host.setProperty("role", "armor-zone")
        box = QVBoxLayout(host)
        box.setContentsMargins(
            self._px(TOKENS.spacing.sm),
            self._px(TOKENS.spacing.xs),
            self._px(TOKENS.spacing.sm),
            self._px(TOKENS.spacing.xs),
        )
        box.setSpacing(0)

        name = _label(zone.label, "armor-zone-name")
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(name)

        kinetic = _label(str(zone.kinetic), "armor-kinetic")
        heat = _label(str(zone.heat), "armor-heat")
        for number in (kinetic, heat):
            number.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            number.setGraphicsEffect(self._outline(number))

        if zone.key in _ARMOR_STACKED:
            box.addWidget(kinetic)
            box.addWidget(heat)
        else:
            values = QWidget()
            # 容器必须走"透明容器"那条 QSS 规则，否则会在立绘上刷出一块 bg-base。
            values.setProperty("role", "armor-values")
            row = QHBoxLayout(values)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(self._px(TOKENS.spacing.sm))
            row.addWidget(kinetic)
            row.addWidget(heat)
            box.addWidget(values)

        host.setToolTip(f"{zone.label}：动能 {zone.kinetic} / 破甲 {zone.heat}")
        return host

    @staticmethod
    def _outline(widget: QWidget) -> QGraphicsDropShadowEffect:
        """数字的黑色描边（近似实机 TMP 的 outline）。

        投影的偏移 (0, 1) + 小模糊 = 一圈描边；颜色取自 tokens（AGENTS 硬纪律：
        界面里不许出现字面色值），不透明度见 `_ARMOR_OUTLINE_ALPHA`。
        """
        outline = QGraphicsDropShadowEffect(widget)
        outline.setBlurRadius(3)
        outline.setOffset(0, 1)
        color = QColor(TOKENS.color.armor_outline)
        color.setAlpha(_ARMOR_OUTLINE_ALPHA)
        outline.setColor(color)
        return outline

    # -- 数值行 ---------------------------------------------------------- #

    def _stats_section(self, card: UnitCard) -> QWidget:
        """属性那一排：**两端对齐**（用户口径："希望是两端对齐，而不是左对齐"）。

        三种排法：

        - 只读卡（fit_to_width 开）：_StatRow —— 放得下就两端对齐一行，放不下折行；
        - 军械库那张（原样不动）：格数 ≤ 8
          （InfocardConfig.MAX_STATS_WITH_INITIAL_SPACING）时一行 + 格间 stretch，
          最后一格贴右边；
        - 超过 8 格（直升机 9 格）：FlowLayout —— 硬塞一行会被挤扁。
        """
        frame, layout = self._frame("detail-card")
        layout.addWidget(_label(f"属性（{len(card.stats)}）", "detail-section"))

        host: QWidget
        if self.fit_to_width:
            # 只读卡：**放得下就两端对齐一行，放不下就折行**（见 _StatRow）。
            # 一行那份版式在这里正是卡住的那个 —— 8 格最小宽 374~379，
            # 而只读卡的视口只有 ~384（面板收到最小宽 390 时只剩 ~355）。
            host = _StatRow(
                [self._stat_chip(stat) for stat in card.stats],
                self._px(card.stat_spacing),
            )
        elif len(card.stats) <= MAX_STATS_PER_LINE:
            host = QWidget()
            host.setProperty("role", "card-flow")
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addStretch(1)
            for stat in card.stats:
                row.addWidget(self._stat_chip(stat))
                row.addStretch(1)
        else:
            host = QWidget()
            host.setProperty("role", "card-flow")
            gap = self._px(TOKENS.spacing.xs)
            flow = FlowLayout(host, h_spacing=self._px(card.stat_spacing), v_spacing=gap)
            for stat in card.stats:
                flow.addWidget(self._stat_chip(stat))
        layout.addWidget(host)
        return frame

    def _stat_chip(self, stat) -> QWidget:
        """数值行的一格：图标在上、数字在下，**没有文字、没有框、不能点**。

        实机那一排就是这样（8 格挤一行）。文字标签在 tooltip 里。
        """
        chip = QFrame()
        chip.setProperty("role", "stat-chip")
        box = QVBoxLayout(chip)
        # 横向留 4 就够（**不画边框之后**格与格之间不需要那些内边距了）——
        # 这样 8 格才挤得进一行，与实机那一排一致。
        inset = self._px(TOKENS.spacing.xxs)
        box.setContentsMargins(inset, inset, inset, inset)
        box.setSpacing(0)
        if stat.sprite:
            icon = QLabel()
            icon.setFixedSize(self.icon_box(STAT_ICON_FAMILY))
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.queue_icon(icon, STAT_ICON_FAMILY, stat.sprite)
            box.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        else:
            # 还没对上图的那一格：**退回文字标签**（不填假值，也不留空白）。
            box.addWidget(_label(stat.label, "stat-name"), 0, Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(_label(stat.value, "stat-number"), 0, Qt.AlignmentFlag.AlignHCenter)

        tip = [f"{stat.label} {stat.value}"]
        if stat.hint:
            tip.append(stat.hint)
        if stat.note:
            tip.append(stat.note)
        if stat.value_ref is not None:
            tip.append(f"落点 {stat.value_ref}")
        if stat.link_ref is not None:
            tip.append(f"本单位引用 {stat.link_ref}")
        if stat.evidence != EVIDENCE_CONFIRMED:
            tip.append(f"证据等级：{stat.evidence}（公式读出来了，但没有截图样本对过）")
        chip.setToolTip("\n".join(tip))
        return chip

    # -- 能力 ------------------------------------------------------------ #

    def _abilities_section(self, card: UnitCard) -> QWidget:
        frame, layout = self._frame("detail-card")
        layout.addWidget(_label("能力", "detail-section"))
        if not card.abilities:
            layout.addWidget(_label("这个单位没有可显示的能力。", "empty-hint", wrap=True))
            return frame
        host = QWidget()
        host.setProperty("role", "card-flow")
        gap = self._px(TOKENS.spacing.xs)
        flow = FlowLayout(host, h_spacing=gap, v_spacing=gap)
        for chip in card.abilities:
            flow.addWidget(self._ability_chip(chip))
        layout.addWidget(host)
        return frame

    def _ability_chip(self, chip) -> QWidget:
        """一格能力。基类只展示；可写卡覆写成"点它去看 Abilities 那一行"。"""
        widget = _label(chip.label, "ability-chip")
        if chip.hint:
            widget.setToolTip(chip.hint)
        return widget

    # -- 武器（简略 / 详细两种显示模式） -------------------------------- #

    def _weapons_section(self, card: UnitCard) -> QWidget:
        """武器区。两种模式二选一，与游戏那两张卡一一对应。

        简略 = 一把武器一行、列与列对齐（游戏那张三把武器并排的）；
        详细 = 左竖排武器 + 右详情，数据按「标签 …… 值」一行一条。
        """
        frame, layout = self._frame("detail-card")
        header = QHBoxLayout()
        header.setSpacing(TOKENS.spacing.sm)
        header.addWidget(_label(f"武器（{len(card.weapons)}）", "detail-section"), 1)
        header.addWidget(self._mode_switch(), 0)
        layout.addLayout(header)
        if not card.weapons:
            layout.addWidget(_label("这个单位没有挂武器。", "empty-hint", wrap=True))
            return frame

        if self._mode == MODE_COMPACT:
            # 只读卡上按可用宽度二选一（军械库那张永远走表格那一支，版式一个像素不变）。
            self._built_grid = self._use_grid_table(card.weapons)
            layout.addWidget(
                self._compact_grid(card.weapons)
                if self._built_grid
                else self._compact_stack(card.weapons)
            )
            return frame

        columns = QHBoxLayout()
        columns.setSpacing(self._px(TOKENS.spacing.sm))
        left = QVBoxLayout()
        left.setSpacing(TOKENS.spacing.xs)
        selected = next((w for w in card.weapons if w.group_key == self._expanded), card.weapons[0])
        for weapon in card.weapons:
            left.addWidget(self._weapon_art(weapon))
        left.addStretch(1)
        columns.addLayout(left, 0)
        columns.addWidget(self._weapon_detail(selected), 1)
        layout.addLayout(columns)
        return frame

    # -- 两种显示模式（简略 / 详细） ------------------------------------- #

    @property
    def mode(self) -> str:
        """当前显示模式：MODE_COMPACT（简略）/ MODE_DETAIL（详细）。"""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """切换武器区的显示模式。**不换单位**，只重排。"""
        if mode not in (MODE_COMPACT, MODE_DETAIL):
            raise ValueError(f"未知的显示模式：{mode!r}")
        if mode == self._mode:
            return
        self._mode = mode
        self._ensure_selection()
        if self._card is not None:
            self._render(self._card)

    def _ensure_selection(self) -> None:
        """详细模式总得有一把被选中的武器，否则右半边空着。"""
        if self._mode != MODE_DETAIL or self._card is None or not self._card.weapons:
            return
        if self._expanded is None:
            self._expanded = self._card.weapons[0].group_key

    def _open_detail(self, group_key: int) -> None:
        """从简略模式点一行武器 = 切到详细模式并选中它。"""
        self._mode = MODE_DETAIL
        self._expanded = group_key
        if self._card is not None:
            self._render(self._card)

    def _mode_switch(self) -> QWidget:
        """武器标题右边那对小按钮：简略 / 详细（游戏里也是这两种显示）。"""
        host = QWidget()
        host.setProperty("role", "card-flow")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS.spacing.xxs)
        for mode, text, tip in (
            (MODE_COMPACT, "简略", "一把武器一行：射程 / 携弹量 / 穿深 / 火力并排对齐"),
            (MODE_DETAIL, "详细", "逐条列出弹种与数据，每格一行「标签 …… 值」"),
        ):
            button = QPushButton(text)
            button.setProperty("role", "chip")
            button.setCheckable(True)
            # 同父控件上 autoExclusive = 单选：点已选中的那个不会把它取消掉
            # （否则按钮看着没选中、模式却没变，界面自相矛盾）。
            button.setAutoExclusive(True)
            button.setChecked(self._mode == mode)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, value=mode: self.set_mode(value))
            row.addWidget(button)
        return host

    # -- 简略模式那张表 -------------------------------------------------- #

    def _compact_grid(self, weapons) -> QWidget:
        """一把武器一行，**列是共享的**、值右对齐 —— 用户要的"并列"就在这里。

        用 QGridLayout 而不是一排小卡片：列宽由这一列最宽的那格决定，
        于是三行武器的射程/弹量/穿深/火力**竖着对得齐**（游戏那张卡就是这样）。
        第 1 列是**弹簧列** —— 数值块整体贴右，武器那格留在左边（实机也这么排）。

        ❗ 只读卡上这一份**只在放得下时**才用（_use_grid_table 量过）：
        它的最小宽是各列最小宽之和（实测 645 是 421、M1A2 是 490），
        视口 ~384 时装不下 —— 那就换 _compact_stack（一行一条、能折行）。
        """
        host = QWidget()
        host.setProperty("role", "compact-table")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(self._px(TOKENS.spacing.md))
        grid.setVerticalSpacing(self._px(TOKENS.spacing.sm))
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        columns = range(2, len(COMPACT_COLUMNS) + 2)

        grid.addWidget(_label("武器", "compact-head"), 0, 0)
        for column, title in zip(columns, compact_titles(weapons), strict=True):
            head = _label(title, "compact-head")
            head.setAlignment(right)
            grid.addWidget(head, 0, column)

        for line, weapon in enumerate(weapons, start=1):
            grid.addWidget(self._weapon_art(weapon), line, 0)
            for column, cell in zip(columns, weapon.compact, strict=False):
                value = _label(cell.text, "compact-cell")
                value.setAlignment(right)
                if cell.tooltip:
                    value.setToolTip(cell.tooltip)
                grid.addWidget(value, line, column)
            # ❗ 这里画的是**所有弹种的并集**（`weapon.targets`），不是第一个弹种的：
            # 一把枪两种弹（AP/HE）时只取第一个会漏（用户报的"没显示全"）。
            icons = self._target_icons(weapon.targets)
            if weapon.targets:
                icons.setToolTip(self._weapon_target_tip(weapon))
            grid.addWidget(icons, line, len(COMPACT_COLUMNS) + 2, Qt.AlignmentFlag.AlignVCenter)

        grid.setColumnStretch(1, 1)
        return host

    def _compact_stack(self, weapons) -> QWidget:
        """简略模式在**窄栏**里的排法：一把武器一块，值带标签、放不下继续折行。

        什么时候走这里见 _use_grid_table：表格形态的最小宽 = 各列最小宽之和
        （实测 645 是 421、M1A2 是 490），而只读卡的视口只有 ~384 —— 装不下就
        别硬塞。用户口径是"不要让我手动向右向左滑"，更不能把右边的数裁掉。
        """
        host = QWidget()
        host.setProperty("role", "compact-table")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(self._px(TOKENS.spacing.md))
        for weapon in weapons:
            box.addWidget(self._compact_block(weapon))
        return host

    def _compact_block(self, weapon: WeaponGroupRow) -> QWidget:
        """窄栏里的一把武器：第一行 = 剪影（含 ×N 与名字）+ 能打什么，第二行 = 四个数。

        四个数从"竖着对齐的一列"改成「列名 值」一小块 —— 表格那种对齐要占满宽度，
        窄栏里没有那个宽度；带标签就没有歧义了（原来靠列标题，现在标题跟着值走）。
        """
        block = QWidget()
        block.setProperty("role", "card-flow")
        box = QVBoxLayout(block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(self._px(TOKENS.spacing.xxs))

        head = QHBoxLayout()
        head.setSpacing(self._px(TOKENS.spacing.sm))
        head.addWidget(self._weapon_art(weapon), 0)
        head.addStretch(1)
        # ❗ 这里画的是**所有弹种的并集**（weapon.targets），与表格那一份同口径。
        if weapon.targets:
            icons = self._target_icons(weapon.targets)
            icons.setToolTip(self._weapon_target_tip(weapon))
            head.addWidget(icons, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(head)

        flow_host = QWidget()
        flow_host.setProperty("role", "card-flow")
        flow = FlowLayout(
            flow_host,
            h_spacing=self._px(TOKENS.spacing.md),
            v_spacing=self._px(TOKENS.spacing.xxs),
        )
        for title, cell in zip(compact_titles((weapon,)), weapon.compact, strict=False):
            if not cell.text:
                # 这一格本来就没值（例：R-73 地面射程为 0 那一格不发射）——
                # 表格形态里它留空占位，这里不摆一个只有列名的空块。
                continue
            flow.addWidget(self._compact_chip(title, cell))
        box.addWidget(flow_host)
        return block

    def _compact_chip(self, title: str, cell) -> QWidget:
        """窄栏里的一格数据：「列名 值」（列名小字在前，值跟着它）。"""
        chip = QWidget()
        chip.setProperty("role", "card-flow")
        row = QHBoxLayout(chip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(self._px(TOKENS.spacing.xs))
        row.addWidget(_label(title, "compact-head"))
        row.addWidget(_label(cell.text, "compact-cell"))
        if cell.tooltip:
            chip.setToolTip(cell.tooltip)
        return chip

    def _weapon_art(self, weapon: WeaponGroupRow) -> QWidget:
        """武器那一格（×N 徽章 + 大剪影 + 名字）。简略模式与详细模式的左列共用它。"""
        art = _WeaponArt(self, weapon)
        art.setProperty("selected", "true" if self._expanded == weapon.group_key else "false")
        art.clicked.connect(self._open_detail)
        return art

    @staticmethod
    def _weapon_target_tip(weapon: WeaponGroupRow) -> str:
        """目标图标那一排的悬浮提示：**逐弹种**列出来（那一排是并集，得说清谁打谁）。"""
        parts = [
            f"{ammo.label}：" + ("、".join(target.label for target in ammo.targets) or "—")
            for ammo in weapon.ammo
        ]
        return "能打什么 —— " + "；".join(parts)

    @staticmethod
    def weapon_tooltip(weapon: WeaponGroupRow) -> str:
        """武器那一格的悬浮提示：多个弹种时**明说这一行只取第一个**（不填假值）。"""
        parts = [weapon.label]
        if weapon.kind:
            parts.append(weapon.kind)
        if len(weapon.ammo) > 1:
            parts.append(
                f"有 {len(weapon.ammo)} 个弹种，这一行取第一个（{weapon.ammo[0].label}）；"
                "详细模式里每个弹种各一栏"
            )
        return " · ".join(parts)

    def _weapon_detail(self, weapon: WeaponGroupRow) -> QWidget:
        frame, layout = self._frame("detail-line")
        head = QHBoxLayout()
        head.setSpacing(self._px(TOKENS.spacing.sm))
        head.addWidget(_label(weapon.label, "weapon-name", wrap=self.fit_to_width), 1)
        if weapon.kind:
            head.addWidget(_label(weapon.kind, "card-note"), 0)
        head.addWidget(_label(f"×{weapon.count}", "stat-number"), 0)
        layout.addLayout(head)

        # 武器自己的那几行（瞄准时间 / 射速 / 弹匣容量 / 装填时间）—— **一行一条**，
        # 与游戏那张卡的排法一致（不再是一格一个小方框）。
        if weapon.detail:
            layout.addWidget(self._stat_lines(weapon.detail))

        for ammo in weapon.ammo:
            layout.addWidget(self._ammo_block(weapon, ammo))

        if weapon.pending:
            layout.addWidget(
                _label(
                    "待反编译确认的详情行：" + "、".join(weapon.pending) + "（先不显示，不填假值）",
                    "card-note",
                    wrap=True,
                )
            )
        return frame

    # -- 详细模式：一行一条 ------------------------------------------------- #

    def _ammo_block(self, weapon: WeaponGroupRow, ammo) -> QWidget:
        """一个弹种一栏：弹种行 + 小字说明 + 「能打什么」 + 逐行的数据。

        栏与栏之间用一条细线分开（**不是**给每一格数据套方框 —— 用户明确不要那个）。
        只读：换弹种 / 删这一行 / 加一个弹种那一排按钮已经按用户要求撤掉了。
        """
        block = QFrame()
        block.setProperty("role", "ammo-block")
        box = QVBoxLayout(block)
        box.setContentsMargins(0, self._px(TOKENS.spacing.sm), 0, self._px(TOKENS.spacing.xs))
        box.setSpacing(self._px(TOKENS.spacing.xxs))

        line = QFrame()
        line.setProperty("role", "ammo-line")
        head = QHBoxLayout(line)
        inset = self._px(TOKENS.spacing.xxs)
        side = self._px(TOKENS.spacing.sm)
        head.setContentsMargins(side, inset, side, inset)
        head.setSpacing(self._px(TOKENS.spacing.xs))
        icon = QLabel()
        icon.setFixedSize(self.icon_box(AMMO_ICON_FAMILY))
        icon.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(icon, 0)
        self.queue_icon(icon, AMMO_ICON_FAMILY, ammo.icon)
        head.addWidget(_label(ammo.label, "weapon-name", wrap=True), 1)
        head.addWidget(_label(f"×{ammo.count}", "ammo-count"), 0, Qt.AlignmentFlag.AlignTop)
        if ammo.refs:
            line.setToolTip("落点 " + chr(10).join(str(ref) for ref in ammo.refs))
        box.addWidget(line)

        note = f"Σ{ammo.raw_total}"
        if ammo.multiplier != 1:
            note += f" × {ammo.multiplier}（HUDMultiplier）"
        if ammo.trajectory:
            note += f" · {ammo.trajectory}"
        box.addWidget(_label(note, "card-note", wrap=True))

        # 「能打什么」那排小图标（游戏里也只有图，字在 tooltip 上）。
        # TargetType 的 Ground / Ship 两位游戏里没有图标，所以这一排**可能是空的**。
        if ammo.targets:
            box.addWidget(self._target_icons(ammo.targets, flow=True))

        # 详情行：**只画解出来的那些**（数据层就只给这些），没解的列在末尾那行小字里。
        if ammo.detail:
            box.addWidget(self._stat_lines(ammo.detail))

        return block

    def _stat_lines(self, rows) -> QWidget:
        """一叠「标签 …… 值」的行（**一格一行**，不是一格一个方框）。

        用户口径："卡片里的数据，我想要游戏里的并列显示，而不是产品 GUI 里的一块一块的"。
        游戏那张详细卡就是这么排的：类型 / 有效射程 / 每秒伤害 / 穿深 / 武器散布 / 补给值
        一路竖着下来，标签贴左、值贴右。这里照它排。
        """
        host = QWidget()
        host.setProperty("role", "stat-block")
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        for row in rows:
            box.addWidget(self._stat_line(row))
        return host

    def _stat_line(self, row) -> QWidget:
        """一条详情行：标签贴左、值贴右，**同一行**（强假设的挂 tooltip）。"""
        line = QWidget()
        line.setProperty("role", "stat-line")
        box = QHBoxLayout(line)
        inset = self._px(TOKENS.spacing.xxs)
        box.setContentsMargins(0, inset, 0, inset)
        box.setSpacing(self._px(TOKENS.spacing.sm))
        # 只读卡里标签**允许折行**：不折的话一个长标签就是这一行的最小宽。
        box.addWidget(_label(row.label, "stat-line-label", wrap=self.fit_to_width), 0)
        box.addStretch(1)
        box.addWidget(_label(row.value, "stat-line-value"), 0)
        tip = [f"{row.label} {row.value}"]
        if row.hint:
            tip.append(row.hint)
        if row.evidence != EVIDENCE_CONFIRMED:
            tip.append(f"证据等级：{row.evidence}（公式读出来了，但样本还没对过）")
        line.setToolTip(chr(10).join(tip))
        return line

    def _target_icons(self, targets, *, flow: bool = False) -> QWidget:
        """弹药的「能打什么」那一排小图标。

        简略模式那一行用它（横排、不换行 —— 要跟列对齐）；详细模式里 `flow=True`
        （像文字一样排，多了会换行）。图没到货时**退回文字** —— 一排空白比多几个字更糟。
        """
        host = QWidget()
        host.setProperty("role", "card-flow")
        box = self.icon_box(TARGET_ICON_FAMILY)
        gap = self._px(TOKENS.spacing.xxs)
        if flow:
            layout = FlowLayout(host, h_spacing=gap, v_spacing=gap)
        else:
            layout = QHBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(gap)
            # ❗ 高度**钉死**：不然这几个标签会被网格拉成一条竖长条（实测踩过）。
            host.setFixedHeight(box.height())
        for target in targets:
            chip = QLabel(target.label)
            chip.setProperty("role", "target-chip")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(target.label)
            chip.setMinimumSize(box)
            # 竖向别再长：图标是方图，拉高只会画出一条长框；图到货后 `_apply_icon`
            # 会把它钉成正好一个盒子（没有图标来源时就保持文字宽度当兜底）。
            chip.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            chip.setProperty("icon-box", True)
            self.queue_icon(chip, TARGET_ICON_FAMILY, target.icon)
            layout.addWidget(chip)
        return host

    def _toggle_weapon(self, group_key: int) -> None:
        """点武器 = 选中它（顺带把模式切成详细）。**保留旧名**：测试与验收脚本按它接线。

        ❗ **不再"再点一次收起"**：详细模式里左竖排总得有一把是选中态 —— 收起会让右半边
        退回第一把、左边却一把都不高亮，界面自相矛盾。回简略模式是标题右边那个按钮的事。
        """
        self._open_detail(group_key)

    # -- 自定义选项 ------------------------------------------------------ #

    def _options_section(self, card: UnitCard) -> QWidget:
        """底部「自定义选项」：**一个改装槽一行**（图 + 槽名 + 选中态 + 成本 + 下拉）。

        两种模式，由"有没有接写盘通道"决定（`set_option_writer`）：

        - **接了**（单位编辑器那张卡）：换一项 = **选中它** —— 写 `Options.IsDefault`
          （槽内互斥，一个槽只有一项），改完只进缓存（落盘走顶栏 💾）；
          这一行画选中态（`✓ 已选中`）。
        - **没接**（军械库那张只读卡）：还是**预览** —— 按新配置重算整张卡
          （成本 / 数值 / 武器都跟着变），一个字节不落库。用户口径（2026-09-29）：
          "虽然卡片是只读了，但是卡片里的自定义选项还是要能像游戏里那样能变呀"。

        `Options.ReplaceUnitId` 指向**别的**单位的那几项是"换单位"不是换装备：
        发 `variant_requested` 让军械库切过去（两条路都是这样）。
        """
        frame, layout = self._frame("detail-card")
        layout.addWidget(_label(f"自定义选项（{len(card.options)}）", "detail-section"))
        if not card.options:
            layout.addWidget(_label("这个单位没有自定义选项。", "empty-hint", wrap=True))
            return frame

        for group in card.options:
            row = QFrame()
            row.setProperty("role", "option-row")
            # 选中态：这一组现在选中的那一项就是数据里那一条（Options.IsDefault ?? Order==0）。
            # 颜色只从 tokens 来（qss 的 option-row[selected="true"] 那一条）。
            # ❗ 只在**接了写盘通道**的那张卡上画（编辑器）：军械库那张只读卡的
            #    版式一个像素都不动（用户口径"军械库沿用现状"+ 那张卡的三条守卫）。
            if self._shows_selection(group):
                row.setProperty("selected", "true")
            box = QVBoxLayout(row)
            box.setContentsMargins(
                self._px(TOKENS.spacing.md),
                self._px(TOKENS.spacing.sm),
                self._px(TOKENS.spacing.md),
                self._px(TOKENS.spacing.sm),
            )
            box.setSpacing(self._px(TOKENS.spacing.xs))

            head = QHBoxLayout()
            head.setSpacing(self._px(TOKENS.spacing.sm))
            # 当前这一项的图片（游戏那一排也有图，取自 Options.OptionPicture）。
            icon = QLabel()
            icon.setFixedSize(self.icon_box(OPTION_ICON_FAMILY))
            icon.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.queue_icon(icon, OPTION_ICON_FAMILY, group.current_icon)
            head.addWidget(icon, 0)
            head.addWidget(_label(group.label, "weapon-name"), 1)
            if self._shows_selection(group):
                head.addWidget(_label("✓ 已选中", "option-selected"), 0)
            cost = _label(f"+{group.current_cost}" if group.current_cost else "±0", "option-cost")
            head.addWidget(cost, 0)
            box.addLayout(head)

            combo = _CardComboBox()
            for choice in group.choices:
                suffix = f"  +{choice.cost}" if choice.cost else ""
                combo.addItem(f"{choice.label}{suffix}", choice.option_id)
                self.queue_combo_icon(combo, combo.count() - 1, choice.icon)
                if choice.is_current:
                    combo.setCurrentIndex(combo.count() - 1)
            combo.currentIndexChanged.connect(
                lambda _index, g=group, c=combo: self._on_option_picked(g, c)
            )
            if self._option_writer is not None and group.is_own_slot:
                combo.setToolTip(
                    "换一项 = 选中它（写 Options.IsDefault，一个槽只有一项）；"
                    "改动只进缓存，点顶栏「💾 保存」才落盘"
                )
            else:
                combo.setToolTip("这里只是预览：换一项会把整张卡按新配置重算，不写数据")
            box.addWidget(combo)

            note = ""
            if group.from_variant:
                note = (
                    f"这一组挂在单位 {group.owner_unit_id} 上"
                    "（本单位的 Modifications 是空的，是那条替换选项把它换出来的）"
                )
                if self._option_writer is not None:
                    note += " —— 借来的槽只给看不给选"
            elif group.current_label:
                note = f"当前：{group.current_label}"
            if note:
                box.addWidget(_label(note, "card-note", wrap=True))
            layout.addWidget(row)
        return frame

    def _shows_selection(self, group) -> bool:
        """这一组的选中态画不画 —— **只画在能写的那张卡上**（编辑器那张）。

        军械库那张只读卡不画：它的下拉是"预览"，画一个会随预览消失的勾更让人糊涂，
        而且那张卡用户口径是"沿用现状，别重做"。
        """
        return self._option_writer is not None and self._is_selected_group(group)

    def _is_selected_group(self, group) -> bool:
        """这一组现在选中的那一项 = 数据里那一条吗（画选中态用）。

        判据取**数据**（`slot_selected_option`），不取 `group.current_label` ——
        后者还带着"预览"那一层（军械库换下拉时）。
        借来的槽（变体）不画：那一条不属于本单位（见 OptionGroupRow.is_own_slot）。
        """
        if self._data is None or not group.is_own_slot or group.modification_id <= 0:
            return False
        return slot_selected_option(self._data, group.modification_id) == group.selected_option_id

    def _on_option_picked(self, group, combo: QComboBox) -> None:
        """换了一个改型。三条路，按顺序判：

        1. **换成另一个单位**（`Options.ReplaceUnitId` 指向别处）：那是导航不是换装备 ——
           发 `variant_requested` 让军械库切过去（实测 645 的「小队装备配置」组里
           另外两项指向别的单位）；
        2. **接了写盘通道**（编辑器那张卡，见 `set_option_writer`）：卡上点一下
           = **真的选中**（写 `Options.IsDefault`，槽内互斥）。这一下交给外层：
           只有它手上有 EditorContext，于是"写进缓存 + 状态栏一句话"照旧；
        3. 否则还是**预览**（军械库那张只读卡）：按新配置重算整张卡，一个字节不落库。

        ❗ `option_preview_requested` **在重算完成之后才发**：军械库拿它去同步左墙那张卡的
        成本徽章，而它要读的是**重算后**的 `current_cost`。发早了拿到的是上一张卡的数字。
        """
        option_id = combo.currentData()
        if not isinstance(option_id, int):
            return
        choice = next((c for c in group.choices if c.option_id == option_id), None)
        if choice is None:
            return
        if choice.replaces_unit_id and choice.replaces_unit_id != self._unit_id:
            self.variant_requested.emit(choice.replaces_unit_id)
            return
        if self._option_writer is not None and group.is_own_slot:
            # 这一组的预览要清掉：写下去之后**数据**才是唯一依据（右栏与撤销都按数据走），
            # 留着一条旧预览，撤销之后卡片会显示成"没撤"的样子。
            self._selected.pop(group.modification_id, None)
            self._option_writer(group.modification_id, option_id)
            return
        self._selected[group.modification_id] = option_id
        if self._data is None:
            return
        try:
            card = build_unit_card(self._data, self._unit_id, selected=self._selected or None)
        except Exception as exc:  # noqa: BLE001 - 同 show_unit：失败要说出来
            self._show_failure(self._unit_id, exc)
            return
        if card is not None:
            self._render(card)
        self.option_preview_requested.emit(group.modification_id, option_id)

    def _loadout_section(self, card: UnitCard) -> QWidget | None:
        """有编成才画（只有步兵有）。列表里 None 会被过滤掉。"""
        if card.loadout is None:
            return None
        return self._squad_section(card)

    def _squad_section(self, card: UnitCard) -> QWidget:
        """步兵班的编成：每名成员一行（主武器 / 副武器）。**只读**。

        以前每行是两个下拉（能换枪），用户要求卡片只展示信息，所以改成文字。
        武器名的来源在这里查：候选表 = 本单位的 SquadWeapons（见 viewmodel）。
        """
        loadout = card.loadout
        names = {choice.weapon_id: choice.label for choice in loadout.weapon_choices}
        frame, layout = self._frame("detail-card")
        header = QHBoxLayout()
        header.setSpacing(self._px(TOKENS.spacing.sm))
        header.addWidget(_label(f"班组成员（{loadout.size}）", "detail-section"), 1)
        layout.addLayout(header)

        for member in loadout.members:
            row = QWidget()
            row.setProperty("role", "card-flow")
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(self._px(TOKENS.spacing.sm))
            box.addWidget(_label(f"#{member.index}", "card-note"), 0)
            primary = self._weapon_label(names, member.primary_weapon_id)
            # 只读卡里两个武器名都允许折行（不折的话三个名字并排就是这一行的最小宽）。
            box.addWidget(_label(primary, "weapon-name", wrap=self.fit_to_width), 1)
            box.addWidget(
                _label(
                    self._weapon_label(names, member.special_weapon_id),
                    "card-note",
                    wrap=self.fit_to_width,
                ),
                1,
            )
            layout.addWidget(row)
        return frame

    @staticmethod
    def _weapon_label(names, weapon_id: int) -> str:
        """主/副武器那一格：有名字写名字，空位写「（空）」。"""
        if not weapon_id:
            return "（空）"
        return names.get(weapon_id, f"武器 #{weapon_id}")

    def queue_icon(self, target: QLabel, family: str, key: str) -> None:
        """要点图了：先试一次，取不到就排队等（到了由 `_refresh_icons` 补）。"""
        if not key:
            return
        self._icon_labels.append((target, family, key))
        self._apply_icon(target, family, key)

    def queue_combo_icon(self, combo: QComboBox, index: int, key: str) -> None:
        """下拉里的项也要图（选项那一排游戏里就有图）。到货晚了一步就排队等。"""
        if not key:
            return
        self._combo_icons.append((combo, index, OPTION_ICON_FAMILY, key))
        self._apply_combo_icon(combo, index, OPTION_ICON_FAMILY, key)

    def _apply_combo_icon(self, combo: QComboBox, index: int, family: str, key: str) -> bool:
        """给下拉的第 index 项贴图。缩放规则与 `_apply_icon` 完全一致（DPR + 镜像）。"""
        if self._icons is None or not key:
            return False
        pixmap = self._icons.pixmap(family, key)
        if pixmap is None:
            return False
        box = self.icon_box(family)
        # 翻不翻 + DPR 缩放只有一份实现（见 prepare_icon 的说明）。
        combo.setItemIcon(index, QIcon(prepare_icon(pixmap, family, box, combo, key)))
        return True

    def _apply_icon(self, target: QLabel, family: str, key: str) -> bool:
        """把图放上去。**按 DPR 放大再交给 Qt**，否则 150% 下会被糊着拉大 1.5 倍。

        Qt 的 QPixmap 是按**设备像素**算的：33 逻辑像素高的剪影在 150% 屏上要占 50 个
        物理像素。只缩到 33 再 setPixmap，Qt 会把它拉大 1.5 倍 —— 这正是用户报的
        "模糊"。正解：缩到 `盒子 × DPR`，再 setDevicePixelRatio(DPR)，让它 1:1 贴上去。
        """
        if self._icons is None or not key:
            return False
        pixmap = self._icons.pixmap(family, key)
        if pixmap is None:
            return False
        box = self.icon_box(family)
        # ❗ 翻不翻 + DPR 缩放走**唯一**入口 prepare_icon：曾经这里自己抄了一份
        #    `_MIRRORED_FAMILIES`，于是新加的中栏抬头漏翻、用户看到武器又朝左
        #    （2026-10-01「还是老问题，武器能不能让它们朝右」）。
        target.setPixmap(prepare_icon(pixmap, family, box, target, key))
        # 图到了就把占位盒钉死：只给"最小尺寸"的话，标签原来那份文字宽度还在，
        # 格子会比图宽一截 —— 那一列就参差不齐了（实测：单个图标那格宽到 64）。
        if target.property("icon-box"):
            target.setFixedSize(box)
        return True

    @staticmethod
    def _has_icon(target: QLabel) -> bool:
        """这个标签已经有图了吗。"""
        pixmap = target.pixmap()
        return pixmap is not None and not pixmap.isNull()

    def _refresh_icons(self) -> None:
        """图标迟到时补上（只补还没有图的那些）。

        立绘要从 304 MB 的 unitportraits 包里取、武器/弹药图标来自另外两个包，
        必然是异步的：这一帧先画剪影 / 留空，图到了再换——绝不为了等图卡住界面。
        """
        if (
            self._icons is not None
            and self._portrait_view is not None
            and self._portrait_key
            and not self._portrait_view.has_pixmap()
        ):
            portrait = self._icons.pixmap(PORTRAIT_FAMILY, self._portrait_key)
            if portrait is not None:
                self._portrait_view.set_pixmap(portrait)
                self._portrait_view.update()
        for target, family, key in list(self._icon_labels):
            if self._has_icon(target):
                continue
            self._apply_icon(target, family, key)
        for combo, index, family, key in list(self._combo_icons):
            if not combo.itemIcon(index).isNull():
                continue
            self._apply_combo_icon(combo, index, family, key)


#: 旧名保留一版：armory_screen 与测试都是按这个名字接的线。
#: 卡片**替换**了原来的详情面板，但接缝不动（设计 §10.1 ③）。
UnitDetailPanel = UnitCardPanel
