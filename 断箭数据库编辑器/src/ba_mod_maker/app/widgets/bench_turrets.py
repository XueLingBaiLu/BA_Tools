"""编成台 ①「**本单位的**炮塔 → 武器 → 弹药」的控件（载具 / 直升机 / 固定翼共用）。

出处：`docs/交接-2026-10-01-单位编成台设计.md` §3 口径 4（**一个改装 → N 座炮塔 → 武器 →
弹药** 四层层级、每层行尾 ✕、层级头 ＋）＋ 设计图 `docs/设计稿-编成台-载具.png` 的 ① 那一块。
取数与规则全在 `app/viewmodel/bench_turrets.py`（零 Qt）；这一层只做三件事：
摆控件、把意图转给 `EditorContext`、把结果写状态栏（**绝不弹窗**）。

抬头两行字：设计图逐字 → 现在的抬头（**两行都留着**，别照记忆抄）：

    设计图（4 倍放大 ＋ 生成器 `_work/design/gen_bench_vehicle.py:378-379`，两处一致）：
    ① 当前改装下的炮塔 → 武器 → 弹药      改装「ZU23」填了 3 个炮塔槽        ＋ 加一座炮塔

    ❗ 2026-10-02 改了范围（见 `TITLE` 上面那一段）：炮塔那一层从"当前改装装着的"改成
    "本单位可能装的全部炮塔"——**抬头必须跟着改**，还写"当前改装下"就是在说谎：
    ① 本单位的炮塔 → 武器 → 弹药          改装「ZU23」填了 3 个炮塔槽        ＋ 加一座炮塔

- `TITLE` 是**渲染后的完整标题**（固定一句、没有占位符 —— 用例直接做相等断言）；
- `HINT` 是**模板**，真正的文字由 `render_hint()` 在 `refresh()` 里按数据填；
  要查"那一格现在写着什么"就问 `TurretsBench.hint_text()`（读数方法，用例不拼字符串）。

四条界面上看得见的约定：

1. 🗼 / 🔫 / 💥 三个前缀与 `screens/editor/readonly_card.py:522-550` 同一套（那一处是旧卡上
   同一条四层链，用例按"以 🗼 开头那一行"找层）；
2. 层级靠**缩进**：炮塔 0 档 / 武器 1 档 / 弹药 2 档（先例
   `bench_loadout.py::_IconRow.set_indent` —— 缩进加在行**自己的**左边距上，不动别的控件）；
3. 值那一格：炮塔 =「N 件武器 · M 条弹」、武器 =「N 条弹药」、弹药 =「×数量」；
   Id 徽章 = **模板行**的 Id（Turrets / Weapons / Ammunitions）—— 文案口径只有
   `bench_kit.id_badge_text` 那一份；
4. 图标：武器 = weapon_icons / 弹药 = ammo_icons（族名与列名的唯一出处
   `core/assets/families.py::ROW_ICON_SOURCES`）；⚠️ `Turrets` **没有**图标源 ——
   炮塔行不画假图，靠 🗼 认层。

行尾只有 ＋ 与 ✕（用户 2026-10-02 撤销上一轮的 🔁 / ↑↓ / ⇄）：

    炮塔行 = ＋ 挂武器 │ ✕
    武器行 = ＋ 加弹种 │ ✕
    弹药行 = 点「×数量」就地改携弹量 │ ✕

用户原话（这一版的口径）：**「我很讨厌"换"这个功能，它本质上就是删除再新增，你给这个
功能单独做一个按钮，非常多余」**、**「改顺序我还是直接改 order 吧，不用那三个按钮了」**。
⇒ "换" = ✕ 删 + ＋ 增，或点一行到**右栏直接改那一列**（外键那格本来就是可挑行的）；
   "改顺序" = 右栏直接改 `Order` 那一格。⛔ 别再给这两件事单独加按钮。

三条口径（写在这个文件里，别散到别处去）：

1. **＋ 加**一律**就地冒候选**（RowPicker）—— ⛔ 不弹对话框（用户："我不需要这么多弹窗"）；
   被拒绝只写状态栏；
2. ＋ / ✕ 之外的一律由**右栏**（`BenchRail` 列面）承担：点一行 → 那一行的全部列 →
   点值就地改。这一层只摆 ＋ / ✕ 与那两道读数口；
3. **共用行（TurretWeapons 不带 UnitId）写之前在状态栏说人数** —— 用户口径：
   改它对别的车有影响就要当场说清（不强制克隆）；
4. **"折"的手势全页只有一条**：点**这一行的抬头 / 名字那一段** = 折 / 展开
   （块 = 抬头那一整条，炮塔 = ▸ + 🗼名字），点这一行**其余部分** = 选中它
   （右栏出它的全部列）。用户 2026-10-02：「**我不要点三角形展开，你的设计能不能统一一下？**」
   ⇒ 那颗三角只是**记号**，不是唯一入口（它仍能点，但不再是"必须瞄准"的地方）。

⚠️ 这一层**不判断形态**（载具 / 直升机 / 固定翼的差别在 `bench_kind` 那一层）：给什么
context 就画什么 —— 步兵那种"假炮塔"（负数编码）由 viewmodel 落成"没有炮塔"的空态。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel import bench_turrets as vm
from ba_mod_maker.app.viewmodel.bench_turrets import (
    AmmoLine,
    SlotGroup,
    TurretLine,
    TurretTree,
    WeaponLine,
)
from ba_mod_maker.app.viewmodel.unit_card import row_name
from ba_mod_maker.app.widgets.bench_kit import (
    CARET_HEIGHT,
    CARET_WIDTH,
    BenchAction,
    BenchEmpty,
    BenchRow,
    BenchSection,
    id_badge_text,
)
from ba_mod_maker.app.widgets.detach import detach
from ba_mod_maker.app.widgets.row_picker import RowPicker
from ba_mod_maker.core.edit.refs import CellRef

#: 抬头两行字（设计图逐字）。`TITLE` = 渲染后的完整标题；`HINT` = **模板**。
#: ❗ 2026-10-02 改了范围：从前是「**当前改装下**的炮塔」（=卡上那条链，只收 IsDefault +
#: 被选项的槽覆盖，实测单位 21 只剩 1 座），现在画**本单位可能装的全部炮塔**（名册，5 座）。
#: 用户原话：「还是把所有的炮塔都列出来吧，我毕竟要增删炮塔的」—— 列不全就没法增删。
#: 抬头那句**必须跟着改**：还写"当前改装下"就是在说谎（槽号那一小段仍然只标当前选中的那座）。
TITLE = "① 本单位的炮塔 → 武器 → 弹药"
HINT = "改装「{option}」填了 {count} 个炮塔槽"
#: 没有"当前改装选项"时那句：抬头不能把一个带花括号的模板直接画出去
#: （步兵变体 645 就走到这儿 —— 它自己 0 条 Modifications）。
HINT_NO_OPTION = "本单位没有自己的改装选项 —— 炮塔按 TurretUnits 的缺省名单画"

ADD_TURRET_TEXT = "＋ 加一座炮塔"
#: 行尾工具：两个 ＋（图标单字 + tooltip 说清）＋ 弹药行的「×数量」。
ADD_WEAPON_TEXT = "＋"
ADD_AMMO_TEXT = "＋"
TIP_ADD_WEAPON = (
    "给这座炮塔挂一门武器：就地冒候选列表（写共享的 TurretWeapons —— 会影响共用它的车）"
)
TIP_ADD_AMMO = "给这门武器加一种弹：就地冒候选（一行 = 武器 × 弹种，只写本单位这一份）"
TIP_QUANTITY = "点我改携弹量（就地改，没有确认按钮）"

NO_UNIT_TEXT = "（还没有打开单位）"
NO_TURRET_TEXT = "这个单位没有炮塔（步兵的武器挂在班组成员身上）"

#: 层级缩进一档（与 `bench_loadout.AMMO_INDENT` 同一档 = spacing.lg）。
LEVEL_INDENT = TOKENS.spacing.xl
#: 图标位高度：炮塔行不画图（没有图标源）；武器 20 / 弹药 16（与 ② 那一块同档）。
TURRET_ICON_HEIGHT = 20
WEAPON_ICON_HEIGHT = 20
AMMO_ICON_HEIGHT = 16

#: **图标槽的统一宽度** —— 三层（塔 / 武器 / 弹）都用这一档。
#:
#: ❗ 用户 2026-10-03：「它们的图标能不能**按树向右缩进**一些呢？第一眼看到的应该是图标，
#:    而图标按树缩进很关键」。当时每一层是按**自己的**图标高 × 4 钉的（塔 / 武器 80、
#:    弹 64）⇒ 名字那一列反而**往回缩**：实测弹药的 💥 在 142、它父级武器的 🔫 在 146，
#:    看着像兄弟不像父子。统一成一档之后，图标列与名字列**一起**每层右移 LEVEL_INDENT。
#: 取值 = 最高那一档图标（20）的 4 倍 —— 与 IconLabel 自己那条取图宽度同一个口径。
ICON_SLOT_WIDTH = TURRET_ICON_HEIGHT * 4

#: 整块自己的三种**处境** —— 也要进结构署名：三种处境画的是三句不同的话
#: （「还没有打开单位」/「这个单位没有炮塔」/ 一行行真数据）。
#: ❗ 光比"行署名"的话空结构 == 空结构，从"没打开单位"切到"这个单位没炮塔"时
#:    上一句会留着不换（`bench_loadout` 那边被用例当场抓过同一条）。
STATE_NO_UNIT = "·没有单位"
STATE_NO_TURRETS = "·没有炮塔"
STATE_ROWS = "·有行"


def render_hint(option: str, count: int) -> str:
    """抬头那句灰字 —— 数据（选项名 / 槽数）在**这儿**填，模板是模块常量 `HINT`。"""
    name = (option or "").strip()
    if not name:
        return HINT_NO_OPTION
    return HINT.format(option=name, count=int(count))


class QuantityEditor(InlineEditor):
    """携弹量那一格的就地编辑 —— 值转不动时**额外**说一句给状态栏。

    InlineEditor 自己会把错误画在浮层里（inline-error），但那是"就地"的：用户一点走就没了。
    编成台这一层的口径是「非法输入不写、状态栏说一句」（见模块头）—— 所以在它**唯一**的
    出错漏斗 _show_error 上再补一次 emit（inline_editor.py 里所有"值转不动"都只走这一个函数）。

    与 ② 那一块（widgets/bench_loadout.py::QuantityEditor）是同一手接法；那一份是同门兄弟，
    这里**没 import 它**：两个块各自有写入范围，跨块 import 会把两边的改动锁在一起。
    """

    #: 值没转成（参数：为什么）。**值没有写进去** —— 外层接到就写一句状态栏。
    rejected = Signal(str)

    def _show_error(self, message: str) -> None:
        super()._show_error(message)
        self.rejected.emit(str(message))


def block_key(context, tree: TurretTree | None) -> tuple:
    """整块的**结构**署名：处境 ＋ 每一行是哪张表的哪一行（见 `vm.structure_key`）。

    值变了（署名没变）→ `reload_values()` 就地换字；署名变了 → 整块重建。
    """
    if context is None:
        return (STATE_NO_UNIT,)
    if tree is None or not tree.turrets:
        return (STATE_NO_TURRETS,)
    return (STATE_ROWS, *vm.structure_key(tree))


class _FoldCaret(QLabel):
    """炮塔行**最左边**那一格 ▸/▾：点它 = 折这一座（收起它的武器 + 弹药行）。

    事件口径（用户/Lead 2026-10-02：「点记号 = 折这一座，点行体仍然是选中」）：

    - ❗ **它不是唯一入口**：点这一行的**名字**与**图标**同样折（见 `TurretRow.eventFilter`）——
      用户 2026-10-02：「我不要点三角形展开，你的设计能不能统一一下？」；
      2026-10-03 再补一句：「**我点图标也要能展开和折叠折叠栏**」；
    - role 复用 `bench-section-caret`（颜色由 QSS 给，**尺寸在代码里**：见
      `bench_kit.CARET_WIDTH/CARET_HEIGHT`）—— 用户实机报过那颗三角「很不好操作」，
      所以这一格是 24×20 的**一整格**，不是"瞄那 8px"；
    - ❗ **press 与 release 都要吃掉**：只 accept release 的话，Qt 的隐式鼠标抓取会把
      release 交给父行（press 被父行收下了）—— 于是点一下记号会**同时**折一次又选中这一行。
      先例是 `bench_kit._SectionHead`（只认"点在我自己身上"那一下）；
    - ⛔ 记号文字**不并进行名**：行名仍以 🗼 开头，有用例按文字断言。
    """

    #: 点了这一格（外层决定折哪一座、怎么隐藏）。
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("▸", parent)
        self.setProperty("role", "bench-section-caret")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(CARET_WIDTH, CARET_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 的驼峰
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ChainRow(BenchRow):
    """带图标位 + 缩进的一行（炮塔 / 武器 / 弹药共用的一半）。

    - 图标：把「族 + 键」记在控件上，换 store / 换键时能就地重取（不重建控件）；
    - 缩进：加在**行自己的左边距**上（炮塔 0 档 / 武器 1 档 / 弹药 2 档）——
      ❗ **子炮塔整体再深一档**（它 1 档、它的武器 2 档、弹药 3 档）——
      用户 2026-10-02：「把层级画出来吧」；
      `set_level` 永远从**基准左边距**重算，连着调两次不会越缩越右；
    - `nav_ref` = 点这一行右栏画谁（一般是**关联行**）；`row_id` + `table` = ✕ 删谁
      （关联行；0 = 没有可删的那一份）。
    """

    #: 这一行的**关联表**（✕ 删这张表的 `row_id`）—— 子类写死。
    table: str = ""

    def __init__(
        self,
        *,
        store,
        family: str,
        icon_key: str,
        icon_height: int,
        level: int,
        parent: QWidget | None = None,
        **kwargs,
    ) -> None:
        super().__init__(icon_height=icon_height, parent=parent, **kwargs)
        self._store = store
        self._family = str(family or "")
        self._key = str(icon_key or "")
        self._icon_height = int(icon_height)
        self._level = 0
        #: 点这一行 -> 右栏画它（`CellRef`）；`None` = 这一行没有落点。
        self.nav_ref: CellRef | None = None
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)  # bench_kit 里那一行就是 QHBoxLayout
        self._base_left = layout.contentsMargins().left()
        # 图标位**钉成同一档宽度**（ICON_SLOT_WIDTH）—— 但**只给有真图的行钉**：
        # · 钉的理由：不钉名字会跟着图标长宽比左右跳；按各自高度钉则名字列往回缩
        #   （弹药那层被坑过，见 ICON_SLOT_WIDTH 注释）；
        # · ⛔ **没图标源的行（炮塔）不许钉** —— 它靠名字前那个 🗼 认层，钉了就是白占 80px、
        #   把 🗼 推到比它下面那些武器行还靠右，行首参差（用户 2026-10-03：「你这个缩进很乱」，
        #   那一版正是 56e4f14 顺手给它钉上了）。
        if self._family:
            self.icon_label().setFixedWidth(ICON_SLOT_WIDTH)
        self.set_level(level)
        self.reload_icon()

    # ---- 图标 ---------------------------------------------------------- #

    def reload_icon(self) -> None:
        self.set_icon(self._store, self._family, self._key)

    def apply_icon_key(self, key: str) -> None:
        """就地换图标键（**值**变了那一路 —— 不重建控件）。"""
        self._key = str(key or "")
        self.reload_icon()

    def set_icons(self, store) -> None:
        """换上外面那份图标缓存（`TurretsBench.set_icons` 往下发）。"""
        self._store = store
        self.reload_icon()

    # ---- 缩进 ---------------------------------------------------------- #

    def set_level(self, level: int) -> None:
        """缩进 level 档 —— 从**基准左边距**重算（不累加，可重复调用）。"""
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        margins = layout.contentsMargins()
        left = self._base_left + max(0, int(level)) * LEVEL_INDENT
        layout.setContentsMargins(left, margins.top(), margins.right(), margins.bottom())
        self._level = int(level)

    @property
    def level(self) -> int:
        """缩进档位（0 炮塔 / 1 武器 / 2 弹药）—— 用例按它断言"层级真的画出来了"。"""
        return self._level


class TurretRow(_ChainRow):
    """一座炮塔一行（`TurretUnits` 一行；本单位没有那一行时它只是"选项的槽顶出来的"）。

    行**自己也能折**（用户 2026-10-02：「炮塔我也需要能折叠」）：最左边那格 ▸/▾ 收/放
    它下面的武器行与弹药行。**只 hide，控件一个都不销毁** —— 展开回来是原样（AGENTS §0.7）。
    收起时这一行的值仍写着「N 件武器 · M 条弹」，所以收起来也看得见里面有多少。
    """

    table = vm.TABLE_TURRET_UNITS

    #: 点了最左边那格记号（外层决定折哪一座、hide/show 哪几行）。
    fold_requested = Signal()

    def __init__(
        self, line: TurretLine, store=None, parent: QWidget | None = None, *, level: int = 0
    ) -> None:
        super().__init__(
            store=store,
            family="",  # Turrets 没有图标源 —— 靠名字前那个 🗼 认层
            icon_key="",
            icon_height=TURRET_ICON_HEIGHT,
            # 0 = 顶层炮塔；1 = 挂在父塔下面的子炮塔（`line.depth`）。
            level=level,
            name=f"🗼 {line.label}",
            detail=line.detail_text,
            value=line.value_text,
            id_text=id_badge_text(line.turret_id),
            # ❗ 用户 2026-10-02：「本来就有，那之前怎么把词条隐藏起来了，把所有没显示出来的
            #    词条显示出来」—— **删不掉的行也照画 ✕**（旧口径是"没有本单位那一份就不画"）。
            #    点了由 `_remove_turret` 在状态栏说清为什么删不了（一个字节都不写）。
            removable=True,
            parent=parent,
        )
        self.line = line
        self.turret_id = line.turret_id
        self.row_id = line.unit_row_id
        self._sync()
        #: 最左边那一格 ▸/▾（**单占一格**，在 🗼 图标/名字之前；点它 = 折这一座）。
        self._caret = _FoldCaret()
        self._caret.clicked.connect(self.fold_requested)
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        layout.insertWidget(0, self._caret, 0, Qt.AlignVCenter)
        # ❗ **点名字那一段也是折**（用户 2026-10-02：「我不要点三角形展开，你的设计能不能
        #    统一一下？」）—— 与"点块抬头就折"同一条规矩：这一行的"抬头"就是它的名字。
        #    光标 + tooltip 得跟上，否则"名字能点"只写在代码里、用户看不见。
        # ❗ **不挂 tooltip**（用户 2026-10-03：「我不要这种在中间出现的注释浮窗」——
        #    鼠标一停就盖住下面几行列表）。折的入口照旧三个，靠手型光标认，不靠浮窗。
        for entry in (self.name_label(), self.icon_label()):
            entry.setCursor(Qt.PointingHandCursor)
            entry.installEventFilter(self)
        self._folded = True  # 默认收起（与块抬头同一条口径）
        self.set_folded(True)
        #: 行尾工具（屏幕上的顺序 = ＋ ✕ —— add_tool 逐个插在 ✕ 左边）。
        self._add_weapon = self.add_tool(ADD_WEAPON_TEXT, tooltip=TIP_ADD_WEAPON)

    def _sync(self) -> None:
        self.nav_ref = self.line.ref

    def add_weapon_button(self) -> BenchAction:
        """＋ 挂一门武器到这座炮塔。"""
        return self._add_weapon

    # ---- 折这一座（记号在最左边；hide/show 那几行由宿主做） ---------------- #

    def caret_label(self) -> _FoldCaret:
        """最左边那一格 ▸/▾（用例按它点 / 读折叠态）。"""
        return self._caret

    def is_folded(self) -> bool:
        """这一座的武器/弹药行现在收着吗（**默认收起**）。"""
        return self._folded

    def set_folded(self, folded: bool) -> bool:
        """换记号文字 + tooltip，并记下状态（**只改记号**，不碰别的控件）。"""
        self._folded = bool(folded)
        self._caret.setText("▸" if self._folded else "▾")
        return self._folded

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 的驼峰
        """点**名字**或**图标**那一段 = 折这一座（与点最左边那格记号同一件事）。

        ❗ **press 与 release 都要吃掉**：只吃 release 的话，press 会冒到父行、父行因此
        拿到这一下 —— 于是"点名字"变成又折又选中（`_FoldCaret` 的注释里有实测过的同一
        机理）。用例 test_clicking_the_turret_name_folds_it_instead_of_selecting 钉着它。
        """
        if (
            obj in (self.name_label(), self.icon_label())
            and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease)
            and event.button() == Qt.LeftButton
        ):
            label = obj
            if event.type() == QEvent.Type.MouseButtonRelease and label.rect().contains(
                event.position().toPoint()
            ):
                self.fold_requested.emit()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def apply_line(self, line: TurretLine) -> None:
        """就地换值（名字 / 灰字 / 计数 / Id / tooltip）—— **一个控件都不重建**。

        （用户口径，提过几回：「我修改的时候你能不能不要全量保存一下，界面闪一下很影响观感」；
         先例 `docs/交接-2026-09-30-界面批-图标树弹药.md:76/93`。）
        """
        self.line = line
        self.turret_id = line.turret_id
        self.row_id = line.unit_row_id
        self.set_name(f"🗼 {line.label}")
        self.set_detail(line.detail_text)
        self.set_value(line.value_text)
        # ❗ Id 也要跟着走：换了炮塔不换 Id，右边就挂着上一座的号
        #    （"控件保住了、值还是旧的"同样是没做到）。
        self.set_id_text(id_badge_text(line.turret_id))
        # 同上：✕ 永远在（点了由 `_remove_turret` 说清「没得删」）。
        self.set_removable(True)
        self._sync()


class SlotGroupRow(BenchRow):
    """③ 里**改装槽分组**的抬头行（用户 2026-10-02：「炮塔和改装槽，也要有树状关系」）。

    一行 = 一个改装槽（＋它的选项挂上来的那几座塔）。最左边那格 ▸/▾ 收/放**这一组**的
    炮塔行（**只 hide，控件一个都不销毁** —— AGENTS §0.7）；点**名字**同样折
    （与 TurretRow 同一条口径：那颗三角不是唯一入口，用户 2026-10-02 点名要统一）。

    ⛔ 它**不是**任何一张表的行：没有 ✕、没有 ＋、点行体不选中（导航交给它下面那些塔行）。
    """

    #: 点了记号 / 名字（外层决定折哪一组、hide/show 哪几行）。
    fold_requested = Signal()

    def __init__(self, group: SlotGroup, parent: QWidget | None = None) -> None:
        super().__init__(
            name=f"🗂 {group.label}",
            detail=group.note,
            value=group.value_text,
            # Id 徽章 = 改装槽 Id（0 = 不属于任何改装槽 -> 不给号）
            id_text=id_badge_text(group.modification_id) if group.modification_id else "",
            removable=False,
            parent=parent,
        )
        self.group = group
        self.modification_id = int(group.modification_id)
        self._caret = _FoldCaret()
        self._caret.clicked.connect(self.fold_requested)
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        layout.insertWidget(0, self._caret, 0, Qt.AlignVCenter)
        # ❗ **不挂 tooltip**（用户 2026-10-03：「我不要这种在中间出现的注释浮窗」——
        #    鼠标一停就盖住下面几行列表）。折的入口照旧三个，靠手型光标认，不靠浮窗。
        for entry in (self.name_label(), self.icon_label()):
            entry.setCursor(Qt.PointingHandCursor)
            entry.installEventFilter(self)
        self._folded = True  # ❗ 默认**收起**（用户 2026-10-02：「我要所有折叠栏默认折叠」）
        self.set_folded(True)

    def caret_label(self) -> _FoldCaret:
        """最左边那一格 ▸/▾（用例按它点 / 读折叠态）。"""
        return self._caret

    def is_folded(self) -> bool:
        """这一组现在收着吗（**默认收起** —— 与炮塔行、块抬头同一条口径）。"""
        return self._folded

    def set_folded(self, folded: bool) -> bool:
        self._folded = bool(folded)
        self._caret.setText("▸" if self._folded else "▾")
        return self._folded

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 的驼峰
        """点**名字**或**图标**那一段 = 折这一组（与点记号同一件事；press 与 release 都要吃掉）。"""
        if (
            obj in (self.name_label(), self.icon_label())
            and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease)
            and event.button() == Qt.LeftButton
        ):
            label = obj
            if event.type() == QEvent.Type.MouseButtonRelease and label.rect().contains(
                event.position().toPoint()
            ):
                self.fold_requested.emit()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def apply_group(self, group: SlotGroup) -> None:
        """就地换这一组抬头的字（槽名 / 灰字 / 计数）—— 一个控件都不重建。"""
        self.group = group
        self.set_name(f"🗂 {group.label}")
        self.set_detail(group.note)
        self.set_value(group.value_text)


class WeaponRow(_ChainRow):
    """一门武器一行（`TurretWeapons` 一行）：缩进一档，行尾「＋ / ✕」。

    行**自己也能折**（用户 2026-10-03：「**像是这样的武器，也要有个折叠栏**」＋
    「所有的折叠栏，都默认折叠状态」）：最左边那格 ▸/▾ 收/放它下面那几条**携弹行**。
    与炮塔行同一套口径：只 hide、控件一个都不销毁；点**名字**同样折；**默认收起**。
    收起时这一行的值仍写着「N 条弹药」，所以收起来也看得见里面有几条。
    """

    table = vm.TABLE_TURRET_WEAPONS

    #: 点了最左边那格记号 / 点名字（外层决定折哪一门、hide/show 哪几行）。
    fold_requested = Signal()

    def __init__(
        self, line: WeaponLine, store=None, parent: QWidget | None = None, *, level: int = 1
    ) -> None:
        super().__init__(
            store=store,
            family=vm.WEAPON_ICON_SOURCE[0],
            icon_key=line.icon_key,
            icon_height=WEAPON_ICON_HEIGHT,
            # 1 = 顶层炮塔的武器；子塔的武器再深一档（`1 + depth`）。
            level=level,
            name=f"🔫 {line.label}",
            value=line.value_text,
            id_text=id_badge_text(line.weapon_id),
            # ❗ 同上：`TurretWeapons` 里没有这一行（它挂在模板炮塔上）也照画 ✕，
            #    点了由 `_remove_weapon` 说清。
            removable=True,
            parent=parent,
        )
        self.line = line
        self.weapon_id = line.weapon_id
        self.link_row_id = line.link_row_id
        self.source_turret_id = line.source_turret_id
        self._sync()
        #: 最左边那一格 ▸/▾（**单占一格**，在 🔫 名字之前；点它 = 折这一门）。
        self._caret = _FoldCaret()
        self._caret.clicked.connect(self.fold_requested)
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        layout.insertWidget(0, self._caret, 0, Qt.AlignVCenter)
        # 与炮塔行 / 分组抬头同一条口径：**点名字也是折**（那颗三角不是唯一入口）。
        # ❗ **不挂 tooltip**（用户 2026-10-03：「我不要这种在中间出现的注释浮窗」——
        #    鼠标一停就盖住下面几行列表）。折的入口照旧三个，靠手型光标认，不靠浮窗。
        for entry in (self.name_label(), self.icon_label()):
            entry.setCursor(Qt.PointingHandCursor)
            entry.installEventFilter(self)
        self._folded = True  # 默认收起（与块抬头 / 分组抬头 / 炮塔行同一条口径）
        self.set_folded(True)
        #: 行尾工具（屏幕上的顺序 = ＋ ✕）。
        self._add_ammo = self.add_tool(ADD_AMMO_TEXT, tooltip=TIP_ADD_AMMO)

    def add_ammo_button(self) -> BenchAction:
        """＋ 给这门武器加一种弹。"""
        return self._add_ammo

    # ---- 折这一门（记号在最左边；hide/show 那几行由宿主做） ---------------- #

    def caret_label(self) -> _FoldCaret:
        """最左边那一格 ▸/▾（用例按它点 / 读折叠态）。"""
        return self._caret

    def is_folded(self) -> bool:
        """这门武器下面的携弹行现在收着吗（**默认收起**）。"""
        return self._folded

    def set_folded(self, folded: bool) -> bool:
        """换记号文字 + tooltip，并记下状态（**只改记号**，不碰别的控件）。"""
        self._folded = bool(folded)
        self._caret.setText("▸" if self._folded else "▾")
        return self._folded

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 的驼峰
        """点**名字**或**图标**那一段 = 折这一门（与点最左边那格记号同一件事）。

        ❗ **press 与 release 都要吃掉**（理由与 TurretRow 那一份逐字相同：
        只吃 release 的话 press 会冒到父行，于是"点名字"变成又折又选中）。
        """
        if (
            obj in (self.name_label(), self.icon_label())
            and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease)
            and event.button() == Qt.LeftButton
        ):
            label = obj
            if event.type() == QEvent.Type.MouseButtonRelease and label.rect().contains(
                event.position().toPoint()
            ):
                self.fold_requested.emit()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def _sync(self) -> None:
        self.nav_ref = self.line.ref

    def apply_line(self, line: WeaponLine) -> None:
        """就地换值：名字 / 计数 / Id / 图标键 / tooltip。"""
        self.line = line
        self.weapon_id = line.weapon_id
        self.link_row_id = line.link_row_id
        self.source_turret_id = line.source_turret_id
        self.set_name(f"🔫 {line.label}")
        self.set_value(line.value_text)
        self.set_id_text(id_badge_text(line.weapon_id))
        self.apply_icon_key(line.icon_key)
        self.set_removable(True)  # 同上：✕ 永远在
        self._sync()


class AmmoRow(_ChainRow):
    """一条携弹行（`WeaponAmmunitions` 一行）：缩进两档，行尾「×数量」＋ Id 徽章。

    「×数量」那一格**点一下就改**（InlineEditor，与 ② 那一块同一个接法）——
    用户选的正是"改携弹量"。
    """

    table = vm.TABLE_WEAPON_AMMO

    #: 点了「×数量」那一格（开就地编辑）—— 这一下**不算**"选中这一行"。
    quantity_requested = Signal()

    def __init__(
        self, line: AmmoLine, store=None, parent: QWidget | None = None, *, level: int = 2
    ) -> None:
        super().__init__(
            store=store,
            family=vm.AMMO_ICON_SOURCE[0],
            icon_key=line.icon_key,
            icon_height=AMMO_ICON_HEIGHT,
            # 2 = 顶层炮塔武器的弹；子塔下面的弹再深一档（`2 + depth`）。
            level=level,
            name=f"💥 {line.label}",
            detail=line.detail_text,
            value=line.value_text,
            id_text=id_badge_text(line.ammo_id),
            removable=True,  # 携弹行永远带 UnitId —— 本单位那一份永远可删
            parent=parent,
        )
        self.line = line
        self.ammo_id = line.ammo_id
        self.row_id = line.row_id
        self.source_turret_id = line.source_turret_id
        self._sync()
        # ❗ 弹药行**自己没有折叠记号**（折它在武器那一层）—— 但那一格**必须占位**：
        #    不占的话它的图标比父级武器行还靠左（实测 x=30 vs 48），三层看起来是**倒的**
        #    （用户 2026-10-03：「你这个弹药的缩进，不符合逻辑」）。
        #    占位之后 = 塔 36 / 武器 48 / 弹 60，每一档正好 LEVEL_INDENT（12）。
        #    用例 test_the_indent_is_monotonic_on_screen 钉着这条。
        self._caret_slot = QWidget(self)
        self._caret_slot.setFixedWidth(CARET_WIDTH)
        layout = self.layout()
        assert isinstance(layout, QBoxLayout)
        layout.insertWidget(0, self._caret_slot, 0, Qt.AlignVCenter)
        #: 就地编辑浮层（挂在「×数量」那一格上；没开时是 None）。
        self._editor: QWidget | None = None
        self.value_label().setCursor(Qt.PointingHandCursor)
        self.value_label().installEventFilter(self)

    def _sync(self) -> None:
        self.nav_ref = self.line.ref

    def apply_line(self, line: AmmoLine) -> None:
        """就地换值：名字 / 卡面那句 / 「×数量」/ Id / 图标键 / tooltip。"""
        self.line = line
        self.ammo_id = line.ammo_id
        self.row_id = line.row_id
        self.source_turret_id = line.source_turret_id
        self.set_name(f"💥 {line.label}")
        self.set_detail(line.detail_text)
        self.set_value(line.value_text)
        self.set_id_text(id_badge_text(line.ammo_id))
        self.apply_icon_key(line.icon_key)
        self._sync()

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

    # ---- 就地编辑那一格（与 bench_loadout.AmmoRow.attach_editor 同一套口径） ---- #

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
        """收起就地编辑器，把原来那一格露回来。"""
        editor, self._editor = self._editor, None
        if editor is not None:
            detach(editor)
        self.value_label().setVisible(True)

    @property
    def has_editor(self) -> bool:
        return self._editor is not None

    @property
    def editor(self) -> QWidget | None:
        """挂在「×数量」那一格上的编辑控件（用例按它检查浮层）。"""
        return self._editor


class TurretsBench(QWidget):
    """编成台 ①：**本单位的**炮塔 › 武器 › 弹药（一个改装 → 名册里 N 座炮塔 → 武器 → 弹药）。

    画的是 `vm.turret_tree()`（**名册**那一份），不是卡上"当前装着的那几座" —— 只有列全了，
    「✕ 删一座 / ＋ 加一座」才够得着（用户 2026-10-02：「还是把所有的炮塔都列出来吧，
    我毕竟要增删炮塔的」）。

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
        self._tree: TurretTree | None = None
        self._rows: list[_ChainRow] = []
        self._turret_rows: list[TurretRow] = []
        self._weapon_rows: list[WeaponRow] = []
        self._ammo_rows: list[AmmoRow] = []
        self._body_widgets: list[QWidget] = []
        self._selected: tuple[str, int] = ("", 0)
        #: **建的时候**记下来的那一份结构署名（处境 + 行署名）——"现在画着什么"。
        #: ❗ 不许拿"现在的 context + 现算的树"现算：set_context 是先换 context 再 refresh 的，
        #:    现算会把"还没打开单位"那块灰字也算成"这个单位没炮塔"，于是那一句留着不换。
        self._drawn_key: tuple = ()
        self._status = ""
        self._hint = render_hint("", 0)
        self._picker: RowPicker | None = None
        #: 现在开着就地编辑器的携弹行（改携弹量；没开时 None）。
        self._editor_row: AmmoRow | None = None
        #: **展开着**的那几座炮塔（按 turret_id）—— 默认空 = 每座都收起（用户口径）。
        #: 跨 refresh / 换单位都留着：用户点开一次，就不该每次重画又给他收回去。
        self._open_turrets: set[int] = set()
        #: turret_id -> 它下面那几行（**武器行**），每次重画时重建；收起就是 hide 它们。
        #: ❗ 弹行**不在这份名单里** —— 它们收在"哪一门武器"下面（见 _fold_ammo）。
        self._fold_children: dict[int, tuple[QWidget, ...]] = {}
        #: **展开着**的那几门武器（键 = (源炮塔, 武器)）—— 默认空 = 每门都收起
        #: （用户 2026-10-03：「像是这样的武器，也要有个折叠栏」＋「所有的折叠栏，都默认折叠」）。
        #: 与 _open_turrets 一样：跨 refresh / 换单位都留着，别每次重画又给他收回去。
        self._open_weapons: set[tuple[int, int]] = set()
        #: 武器键 -> 它下面那几条携弹行（收起就是 hide 它们）。
        self._fold_ammo: dict[tuple[int, int], list[QWidget]] = {}
        #: 武器键 -> 它自己那几份行控件（同一门武器可能在好几个槽下面各有一份）。
        self._weapon_rows_at: dict[tuple[int, int], list[WeaponRow]] = {}
        #: 炮塔 -> 它下面那几门武器的键（折这一座时要把武器自己的折叠态一起算）。
        self._turret_weapons: dict[int, list[tuple[int, int]]] = {}
        #: 分组抬头（改装槽）那几行 + 每组的成员行 + "**展开着**的那几组"
        #: （默认空 = 全收起，与 `_open_turrets` 同一条口径；
        #:  用户 2026-10-02：「我要所有折叠栏默认折叠」）。
        #: 用户 2026-10-02：「炮塔和改装槽，也要有树状关系」—— 这一层就是那棵树的分组。
        self._group_rows: list[SlotGroupRow] = []
        self._fold_groups: dict[int, list[QWidget]] = {}
        self._group_turrets: dict[int, list[int]] = {}
        self._open_groups: set[int] = set()
        # ❗ 用户 2026-10-02：「既然也被使用，就给使用的那个折叠栏里也加上啊」——
        #    一座塔会**同时出现在好几个槽下面**（多对多），于是同一座塔有好几个行控件：
        #    按 `(槽, 塔)` / `(槽, 塔, 武器)` / `(槽, 塔, 弹行)` 记，就地刷新才找得准。
        self._turret_rows_at: dict[int, list[TurretRow]] = {}
        self._turret_row_at: dict[tuple[int, int], TurretRow] = {}
        self._weapon_row_at: dict[tuple[int, int, int], WeaponRow] = {}
        self._ammo_row_at: dict[tuple[int, int, int], AmmoRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._section = BenchSection(TITLE, self._hint)
        self._add = self._section.add_action(
            ADD_TURRET_TEXT,
            primary=True,
            tooltip="从 Turrets 里挑一座，挂到当前改装选项的下一个空槽（就地冒候选，不弹对话框）",
        )
        self._add.clicked.connect(lambda _checked=False: self.begin_add())
        root.addWidget(self._section)

        #: 「＋」就地冒出来的候选列表的**落脚点**：一个常驻宿主，先加进来、藏着。
        #: ❗ 宿主**在 section 主体里**（Lead 2026-10-02 判据）：挂在 root 上的话，收起 ①
        #:    这一块时它不跟着藏 —— "折起来只剩抬头一行"那条就不过（body_widgets() 之外
        #:    不许还有可见控件）。⚠️ 它**不进** _body_widgets 名单（见 _add_body）：
        #:    _clear_body() 每次重画只摘名单里那些行，宿主活过每一次重画。
        self._picker_host = QWidget()
        self._picker_host.setProperty("role", "bench-body")
        self._picker_layout = QVBoxLayout(self._picker_host)
        self._picker_layout.setContentsMargins(
            TOKENS.spacing.sm, 0, TOKENS.spacing.sm, TOKENS.spacing.sm
        )
        self._picker_layout.setSpacing(0)
        self._section.add_widget(self._picker_host)
        self._picker_host.setVisible(False)
        #: 行都贴着抬头堆：竖着撑开时别把几行摊平（stretch 永远在主体最后一项）。
        #: ❗ 必须**在宿主之后**加：stretch 在宿主前面的话，行会被插到 stretch 后面去。
        self._section.add_stretch()
        self.refresh()

    # ------------------------------------------------------------------ #
    # Lead 定的装配接口：四件套
    # ------------------------------------------------------------------ #

    def set_context(self, context) -> None:
        """接上编辑服务（`EditorContext`）；`None` = 清空、画空态（**不许炸**）。"""
        self._context = context
        if context is None:
            self._selected = ("", 0)
        self.refresh()

    def set_icons(self, store) -> None:
        """接上**外面传进来的**那一份图标缓存（别在这一层 new IconCache）。

        已经有行了就地换图（`_ChainRow.set_icons`），**不重建** —— 图标晚一步到
        （`shared_icon_cache()` 是异步的）不该让中栏闪一下。
        """
        self._store = store
        for row in self._rows:
            row.set_icons(store)

    def refresh(self) -> None:
        """按当前数据重画（幂等：连调两次不叠控件）。结构真变了才走它。

        判据见 `block_key`：署名没变时外层（BenchPage）走 `reload_values()`，
        这一条只在"插 / 删了行"时用 —— 那时重建是**必须**的（少画一行比闪一下更糟）。
        """
        self.close_picker()
        self._close_editor()
        self._clear_body()
        context = self._context
        if context is None:
            self._tree = None
            self._hint = ""
            self._section.set_hint(self._hint)
            self._add_body(BenchEmpty(NO_UNIT_TEXT))
            self._drawn_key = block_key(None, None)
            self._place_picker(None)
            self._layout_settle()
            return
        tree = vm.turret_tree(context.data, context.unit_id)
        self._tree = tree
        self._hint = render_hint(tree.option_label, tree.slot_count)
        self._section.set_hint(self._hint)
        # ❗ 有分组就**按改装槽分组**画（用户 2026-10-02：「炮塔和改装槽，也要有树状关系」）；
        #    没有分组（老调用方 / 将来别的来源）退回平铺 —— 两条路画出来的塔行是同一批。
        if tree.groups:
            for group in tree.groups:
                self._build_group(group)
        else:
            for line in tree.turrets:
                self._build_turret(line)
        if not tree.turrets:
            self._add_body(BenchEmpty(NO_TURRET_TEXT))
        self._drawn_key = block_key(context, tree)
        if self._selected not in {
            (row.nav_ref.table, row.nav_ref.row_id) for row in self._rows if row.nav_ref is not None
        }:
            self._selected = ("", 0)
        self._apply_selection()
        self._place_picker(None)
        self._layout_settle()

    def reload_values(self) -> None:
        """只是**值**变了：就地把现有控件的文字 / 图标换成最新的，**一个控件都不重建**。

        用户口径（提过几回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下
        很影响观感」。先例 `docs/交接-2026-09-30-界面批-图标树弹药.md:76/93` ——
        署名变了才整套重建，没变走 reload。

        三条边界（与 `bench_loadout.reload_values` 同一条）：

        1. 只 `setText` / 换图标键 / 换 tooltip —— 不 `clear()`、不 `deleteLater()`、
           不动布局（配不上控件的那一项**跳过**：不新建也不销毁）；
        2. **结构真变了**（插 / 删行、"有炮塔↔没炮塔"、换了一座炮塔）就退回 `refresh()`
           整块重建 —— 宁可闪一下，也不能少画一行 / 留一行旧的（外层署名没看出来时它是最后一道闸）；
        3. 还没接 context（没打开单位）时什么都不做。
        """
        context = self._context
        if context is None:
            return
        tree = vm.turret_tree(context.data, context.unit_id)
        if block_key(context, tree) != self._drawn_key:
            self.refresh()
            return
        self._tree = tree
        self._hint = render_hint(tree.option_label, tree.slot_count)
        self._section.set_hint(self._hint)
        # 分组抬头就地换字（槽名 / 灰字 / 计数）—— 结构没变才走到这儿。
        for group in tree.groups:
            head = self.row_for_group(group.modification_id)
            if head is not None:
                head.apply_group(group)
        # ❗ 键里**带上槽**：同一座塔会同时画在好几个槽下面（用户 2026-10-02 要的多对多），
        #    只按 turret_id / (炮塔, 武器) 建字典的话后一份会把前一份盖掉 —— 第一份永远刷不到新值
        #    （先例：携弹行按行 Id 建字典时，同一门枪挂在两座塔上的那两条只更新了一条）。
        for group in tree.groups:
            mid = group.modification_id
            for turret in group.turrets:
                turret_row = self._turret_row_at.get((mid, turret.turret_id))
                if turret_row is not None:
                    turret_row.apply_line(turret)
                for weapon in turret.weapons:
                    weapon_row = self._weapon_row_at.get(
                        (mid, weapon.source_turret_id, weapon.weapon_id)
                    )
                    if weapon_row is not None:
                        weapon_row.apply_line(weapon)
                    for ammo in weapon.ammo:
                        ammo_row = self._ammo_row_at.get((mid, ammo.source_turret_id, ammo.row_id))
                        if ammo_row is not None:
                            ammo_row.apply_line(ammo)
        self._apply_selection()

    # ------------------------------------------------------------------ #
    # 动作：＋ 就地冒候选（不弹对话框）
    # ------------------------------------------------------------------ #

    def begin_add(self) -> bool:
        """「＋ 加一座炮塔」：就地冒候选列表（候选 = Turrets 整表）。再点一下 = 收手。

        返回"有没有接下来"（False = 没单位 / 又点了一下收手，外面不用等信号）。
        """
        context = self._context
        if context is None:
            return False
        if self._picker is not None:
            self.close_picker()
            return False
        picker = RowPicker(context.instance, vm.TABLE_TURRETS, parent=self._picker_host)
        return self._show_picker(picker, on_pick=self.add_turret)

    def _show_picker(
        self,
        picker: RowPicker,
        *,
        after: QWidget | None = None,
        on_pick=None,
        on_pair=None,
    ) -> bool:
        """把候选列表挂到 after 那一行下面（None = 所有行下面）并露出来。

        ❗ 先把 section **展开**：抬头那颗 ＋ 在收起状态下也能点，不展开的话候选列表
        被主体一起藏掉 —— 用户看到的是"点了没反应"。
        """
        if self._picker is not None:
            # 同一时刻只留一份候选（调用方都先查过；这里是兜底，别留个孤儿顶层窗）。
            detach(picker)
            return False
        self._section.set_collapsed(False)
        self._place_picker(after)
        self._picker = picker
        self._picker_layout.addWidget(picker)
        self._picker_host.setVisible(True)
        if on_pair is not None:
            picker.pair_picked.connect(
                lambda first, second: self._pick_done_pair(first, second, on_pair)
            )
        elif on_pick is not None:
            picker.picked.connect(lambda row_id: self._pick_done(row_id, on_pick))
        picker.cancelled.connect(self.close_picker)
        picker.start()
        return True

    def _place_picker(self, after: QWidget | None) -> None:
        """把候选宿主挪到该出现的位置（after=None = 所有行下面、stretch 之前）。"""
        layout = self._section.body_layout()
        layout.removeWidget(self._picker_host)
        if after is None:
            index = max(0, layout.count() - 1)
        else:
            position = layout.indexOf(after)
            index = position + 1 if position >= 0 else max(0, layout.count() - 1)
        layout.insertWidget(index, self._picker_host)

    def _pick_done(self, row_id: int, callback) -> None:
        """单列候选里点中一行：先收起列表，再落库。"""
        self.close_picker()
        callback(int(row_id))

    def _pick_done_pair(self, first: int, second: int, callback) -> None:
        """双列候选（武器 × 弹种）点中一对：先收起列表，再落库。"""
        self.close_picker()
        callback(int(first), int(second))

    def close_picker(self) -> None:
        """收起候选列表（点中 / Esc / 换一块都走它）—— 走 detach：先 hide 再 setParent(None)。"""
        picker, self._picker = self._picker, None
        if picker is not None:
            self._picker_layout.removeWidget(picker)
            detach(picker)
        self._picker_host.setVisible(False)

    def add_turret(self, turret_id: int) -> int:
        """挑好一座炮塔之后落库（**写库经 `EditorContext`**）。返回新 TurretUnits 行 Id。

        两处写、按顺序：

        1. `context.insert_row(TurretUnits, values)` —— 本单位记一份
           （Id 走 `next_row_id`，哪几列由 `vm.plan_add_turret` 算，界面不自己推）；
        2. `context.set_value(Options#<选项>.<TurretNId>, 炮塔Id)` —— 挂到当前选项的
           **下一个空槽**（`CardData.card_turrets` 是"先缺省、再按槽覆盖"，不写这一格
           新挂的炮塔根本不出现）。

        ⚠️ 顺序与"看得见"有关：先补 TurretUnits（结构变了 → 重建），再写槽（值变了 →
        就地换字）—— 反过来的话第二步的 insert_row 会把刚画好的那一次又重建一遍。
        """
        context = self._context
        if context is None:
            return 0
        turret_id = int(turret_id)
        plan = vm.plan_add_turret(
            context.data, context.unit_id, turret_id, changeset=context.changeset
        )
        if not plan.ok:
            self._say(plan.refused)
            return 0
        # ❗ 用户 2026-10-02：「我希望我在加新炮塔时，**能自动为它的武器加上最常用的弹药**」——
        #    这门炮塔的武器是从模板（TurretWeapons）来的，而**弹是按 (单位, 武器) 记的**
        #    （WeaponAmmunitions 带 UnitId）⇒ 新挂的炮塔在本单位一条弹都没有，进游戏就是空炮。
        #    于是同一笔里把缺的补上（口径 = 全库最常用的那一对 (弹, 数量)，
        #    见 vm.common_ammo）；⛔ 本单位**已经有**这门武器弹药行的那些一律不动。
        fills = vm.ammo_fills_for_turret(context.data, context.unit_id, turret_id)

        def extras(_main_id: int):
            # ❗ 必须是**生成器**（惰性一条一条给）：一次性把整个列表算出来的话，这几条弹会拿到
            #    **同一个 Id** —— next_row_id 只看得到已经记进 changeset 的行，而那里还没有它们
            #    （实测：两条弹都拿 4182，第二条插进去当场报「已经有 Id=4182 的行」）。
            for fill in fills:
                yield (
                    vm.TABLE_WEAPON_AMMO,
                    vm.new_ammo_values(
                        context.data,
                        context.unit_id,
                        fill.weapon_id,
                        fill.ammo_id,
                        changeset=context.changeset,
                        quantity=fill.quantity,
                    ),
                )

        # ❗ 一整笔（炮塔行 ＋ 自动补的那几条弹）：一次 Ctrl+Z 该整组回来
        #    （口径同 insert_group）。
        row_id = int(context.insert_group(vm.TABLE_TURRET_UNITS, plan.values, extras=extras))
        context.set_value(CellRef(vm.TABLE_OPTIONS, plan.option_id, plan.column), turret_id)
        context.data.invalidate(vm.TABLE_OPTIONS)
        label = row_name(context.data, vm.TABLE_TURRETS, turret_id)
        tail = "（这个选项里已经挂过同一座炮塔）" if plan.duplicated else ""
        ammo_note = (" · 自动补弹：" + "、".join(fill.text for fill in fills)) if fills else ""
        dry = vm.weapons_without_any_ammo(context.data, turret_id)
        dry_note = (
            f" ⚠ {'、'.join(dry)} 在全库都没有弹药行 —— 用行尾「＋ 加弹种」自己挑一条"
            if dry
            else ""
        )
        self._say(
            f"已把 {label}（Turrets #{turret_id}）挂到「{plan.option_label}」的 "
            f"Turret{plan.slot} 槽，并在本单位补了一行 TurretUnits #{row_id}{tail}"
            f"{ammo_note}{dry_note} · 撤销（Ctrl+Z）"
        )
        self.refresh()
        return row_id

    # ------------------------------------------------------------------ #
    def _shared_note(self, others: tuple[int, ...]) -> str:
        """共用行的警告（用户口径：共用行也允许直接改，但必须当场说清影响谁）。"""
        if not others:
            return ""
        shown = "、".join(f"#{unit}" for unit in others[:4])
        more = " 等" if len(others) > 4 else ""
        return f" ⚠ 这座炮塔还被另外 {len(others)} 个单位共用（{shown}{more}）—— 改它会一起变"

    # 动作：＋ 挂武器 / ＋ 加弹种（共用行：写之前说清影响多少台车）
    # ------------------------------------------------------------------ #

    def _open_add_weapon(self, row: TurretRow) -> None:
        """＋ 这一座炮塔：就地冒 Weapons 候选（挂一行 TurretWeapons）。"""
        context = self._context
        if context is None:
            return
        if self._picker is not None:
            self.close_picker()
            return
        picker = RowPicker(context.instance, vm.TABLE_WEAPONS, parent=self._picker_host)
        self._show_picker(
            picker, after=row, on_pick=lambda weapon_id, row=row: self.add_weapon(row, weapon_id)
        )

    def add_weapon(self, row: TurretRow, weapon_id: int) -> int:
        """挂一门武器：插一行 TurretWeapons（共用行 —— 状态栏说人数）。"""
        context = self._context
        if context is None:
            return 0
        plan = vm.plan_add_weapon(
            context.data,
            context.unit_id,
            row.turret_id,
            int(weapon_id),
            changeset=context.changeset,
        )
        if not plan.ok:
            self._say(plan.refused)
            return 0
        # ❗ 与「＋ 加一座炮塔」同一条口径：挂上新武器时把**最常用的那条弹**一起补上
        #    （本单位已经有这门武器的弹药行时不动它）。
        fill = vm.ammo_fill_for(context.data, context.unit_id, int(weapon_id))
        row_id = int(
            context.insert_group(
                vm.TABLE_TURRET_WEAPONS,
                plan.values,
                extras=(
                    lambda _main: iter(
                        [
                            (
                                vm.TABLE_WEAPON_AMMO,
                                vm.new_ammo_values(
                                    context.data,
                                    context.unit_id,
                                    fill.weapon_id,
                                    fill.ammo_id,
                                    changeset=context.changeset,
                                    quantity=fill.quantity,
                                ),
                            )
                        ]
                    )
                )
                if fill is not None
                else None,
            )
        )
        tail = "（这座炮塔已经挂着同一门武器）" if plan.duplicated else ""
        ammo_note = f" · 自动补弹：{fill.text}" if fill is not None else ""
        if fill is None and vm.common_ammo(context.data, int(weapon_id)) is None:
            ammo_note = " ⚠ 这门武器在全库都没有弹药行 —— 用行尾「＋ 加弹种」自己挑一条"
        self._say(
            f"已给 {plan.turret_label}（Turrets #{plan.turret_id}）挂上 {plan.weapon_label}"
            f"（Weapons #{plan.weapon_id}）—— TurretWeapons #{row_id}，Order 排到队尾"
            f"{self._shared_note(plan.other_units)}{tail}{ammo_note} · 撤销（Ctrl+Z）"
        )
        self.refresh()
        return row_id

    def _open_add_ammo(self, row: WeaponRow) -> None:
        """＋ 这一门武器：就地冒「武器 × 弹种」双列候选（左列钉死这一门）。"""
        context = self._context
        if context is None:
            return
        if self._picker is not None:
            self.close_picker()
            return
        picker = RowPicker(
            context.instance, vm.TABLE_WEAPONS, second_table=vm.TABLE_AMMO, parent=self._picker_host
        )
        if not picker.lock_first(row.weapon_id, row.line.label):
            detach(picker)
            self._say("加弹的候选列表没出来（RowPicker 没有第二列）—— 请报给开发者")
            return
        self._show_picker(
            picker,
            after=row,
            on_pair=lambda weapon_id, ammo_id, row=row: self.add_ammo(row, weapon_id, ammo_id),
        )

    def add_ammo(self, row: WeaponRow, weapon_id: int, ammo_id: int) -> int:
        """加一种弹：插一行 WeaponAmmunitions（带 UnitId —— 只动本单位这一份）。"""
        context = self._context
        if context is None:
            return 0
        plan = vm.plan_add_ammo(
            context.data,
            context.unit_id,
            int(weapon_id),
            int(ammo_id),
            turret_id=row.source_turret_id,
            changeset=context.changeset,
        )
        if not plan.ok:
            self._say(plan.refused)
            return 0
        row_id = int(context.insert_row(vm.TABLE_WEAPON_AMMO, plan.values))
        self._say(
            f"已给 {plan.weapon_label}（Weapons #{plan.weapon_id}）加弹：{plan.ammo_label}"
            f"（Ammunitions #{plan.ammo_id}）—— WeaponAmmunitions #{row_id}"
            "（本单位这一份 · 默认 ×1，点那一行的「×数量」就地改）· 撤销（Ctrl+Z）"
        )
        self.refresh()
        return row_id

    # 动作：改携弹量（点「×数量」就地编辑）
    # ------------------------------------------------------------------ #

    def open_quantity_editor(self, row: AmmoRow) -> bool:
        """点「×数量」：就在那一格上开浮层（没有确认按钮）。"""
        context = self._context
        if context is None:
            return False
        plan = vm.plan_quantity(context.data, context.unit_id, row.row_id)
        if not plan.ok or plan.ref is None:
            self._say(plan.refused)
            return False
        self._close_editor()
        cell = context.cell(plan.ref)
        if cell is None:
            self._say(f"改不了：WeaponAmmunitions #{row.row_id} 没有 Quantity 这一列")
            return False
        editor = QuantityEditor(cell, instance=context.instance)
        editor.committed.connect(lambda value, target=row: self.apply_quantity(target, value))
        editor.rejected.connect(
            lambda message, target=row: self._say(f"携弹量没改：{message}（值没写进去）")
        )
        editor.cancelled.connect(self._close_editor)
        row.attach_editor(editor)
        self._editor_row = row
        return True

    def apply_quantity(self, row: AmmoRow, value) -> None:
        """回车：把新数量写进变更集（只进缓存，写盘在顶栏「保存」）。"""
        context = self._context
        if context is None:
            return
        old = row.line.quantity
        change = context.set_value(
            CellRef(vm.TABLE_WEAPON_AMMO, row.row_id, vm.COLUMN_QUANTITY), value
        )
        self._close_editor()
        if change is None:
            self._say(f"{row.line.label} 的携弹量本来就是 ×{value}")
        else:
            self._say(f"已改携弹量：{row.line.label} ×{old} → ×{value} · 撤销（Ctrl+Z）")
        self.reload_values()

    def _close_editor(self) -> None:
        """收起携弹量那个就地编辑器（Esc / 提交 / 重画都走它）。"""
        row, self._editor_row = self._editor_row, None
        if row is not None:
            row.close_editor()

    # ------------------------------------------------------------------ #
    # 动作：✕ 删的永远是**关联行**
    # ------------------------------------------------------------------ #

    def _remove_turret(self, row: TurretRow) -> None:
        """炮塔行「✕」：删**本单位那一行** `TurretUnits`（模板行 `Turrets` 不动）。

        ⚠️ 只看那一步还不够：当前选项的 21 个槽**压过** TurretUnits 缺省名单，不把还指着它的
        槽腾掉，删完这一行炮塔**照样画在界面上**（用户看到的就是"点了 ✕ 没反应"）。
        所以同一个动作里：先把当前选项里指着它的槽腾成 0（**只在本单位的槽上写**），
        再删那一行，并把这件事说在状态栏 —— 设计图右栏那一条正是
        「✕ 删掉这一座炮塔…这类后果也当场说」。
        """
        context = self._context
        if context is None:
            return
        plan = vm.plan_remove_turret(context.data, context.unit_id, row.turret_id)
        if not plan.removable:
            self._say(
                f"{row.line.label} 在 TurretUnits 里没有本单位那一行 —— 没得删"
                "（它是当前改装选项的槽填进来的）"
            )
            return
        cleared: list[int] = []
        for slot, column in plan.clears:
            change = context.set_value(CellRef(vm.TABLE_OPTIONS, plan.option_id, column), 0)
            if change is not None:
                cleared.append(slot)
        if cleared:
            context.data.invalidate(vm.TABLE_OPTIONS)
        context.delete_row(vm.TABLE_TURRET_UNITS, plan.unit_row_id)
        note = ""
        if plan.slots:
            if cleared:
                note = (
                    f" · 顺带把「{plan.option_label}」的 Turret{plan.slot_text} 槽腾空了"
                    "（不腾它这一行还会画出来）"
                )
            else:
                note = (
                    f" ⚠ 当前选项「{plan.option_label}」的 Turret{plan.slot_text} 槽还指着它"
                    "（借来的槽没动）—— 它还会画出来"
                )
        self._say(
            f"已删掉这一座炮塔：{row.line.label}（TurretUnits #{plan.unit_row_id}）"
            f"—— Turrets 模板行没动{note}· 撤销（Ctrl+Z）"
        )
        self.refresh()

    def _remove_weapon(self, row: WeaponRow) -> None:
        """武器行「✕」：删这条 `TurretWeapons` —— **Weapons 模板行一行不动**。

        ⚠️ `TurretWeapons` **不带 UnitId**：一条武器关联行挂在**炮塔**上，"这座炮塔被 N 个
        单位共用"时删它会一起改到别的车（实测最多 83 个单位共用一座）。这句必须当场说。
        """
        context = self._context
        if context is None:
            return
        if row.link_row_id <= 0:
            self._say(
                f"{row.line.label} 在 TurretWeapons 里没有行 —— 没得删"
                f"（它挂在模板炮塔 Turrets #{row.source_turret_id} 上）"
            )
            return
        others = [
            unit
            for unit in vm.turret_users(context.data, row.source_turret_id)
            if unit != context.unit_id
        ]
        link_row_id = row.link_row_id
        label = row.line.label
        context.delete_row(vm.TABLE_TURRET_WEAPONS, link_row_id)
        note = (
            f" ⚠ 这座炮塔（Turrets #{row.source_turret_id}）还被另外 {len(others)} 个单位共用"
            "—— 删这一行会一起改到它们（Ctrl+Z 可撤）"
            if others
            else ""
        )
        self._say(
            f"已移除武器：{label}（TurretWeapons #{link_row_id}）"
            f"—— Weapons 模板行没动 · 撤销（Ctrl+Z）{note}"
        )
        self.refresh()

    def _remove_ammo(self, row: AmmoRow) -> None:
        """弹药行「✕」：删这一行 `WeaponAmmunitions`（武器行与 Ammunitions 模板行都不动）。"""
        context = self._context
        if context is None:
            return
        label, row_id = row.line.label, row.row_id
        context.delete_row(vm.TABLE_WEAPON_AMMO, row_id)
        self._say(
            f"已移除弹种：{label}（WeaponAmmunitions #{row_id}）"
            "—— Ammunitions 模板行没动 · 撤销（Ctrl+Z）"
        )
        self.refresh()

    # ------------------------------------------------------------------ #
    # 选中 / 两个意图信号
    # ------------------------------------------------------------------ #

    def select(self, table: str, row_id: int) -> None:
        """选中某一行（只改控件里那份状态 + 高亮，**不发信号**）—— 外层同步右栏时用。"""
        self._selected = (str(table), int(row_id))
        self._apply_selection()

    def _apply_selection(self) -> None:
        table, row_id = self._selected
        for row in self._rows:
            ref = row.nav_ref
            row.set_selected(ref is not None and ref.table == table and ref.row_id == row_id)

    def clear_selection(self) -> None:
        """清掉选中（页面在**别的块**选中别的东西时调它）—— 不发信号、不写库。"""
        if self._selected == ("", 0):
            return
        self.select("", 0)

    def _on_clicked(self, row: _ChainRow) -> None:
        ref = row.nav_ref
        if ref is None:
            return
        self.select(ref.table, ref.row_id)
        self.row_selected.emit(ref.table, ref.row_id)

    # ------------------------------------------------------------------ #
    # 摆控件（主体里那几个，都是这一块自己的）
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # ⧉ 复制模板另存一份（与 ③–⑦ 属性行同一条口径，见交接 §23）
    # ------------------------------------------------------------------ #

    def _clone_template(self, target_table: str, target_id: int, link: CellRef, what: str) -> int:
        """复制 (target_table, target_id) 那一行另存一份，再把 link 指过去。"""
        context = self._context
        if context is None:
            return 0
        if not target_id or not link.row_id:
            self.status_message.emit(f"这一{what}没有落点（是选项的槽填进来的）—— 复制不了")
            return 0
        new_id, message = context.clone_row_to(target_table, int(target_id), link)
        self.status_message.emit(f"{message} —— 右栏就是新模板，直接在上面改")
        self.refresh()
        self.row_selected.emit(target_table, new_id)
        return new_id

    def clone_weapon(self, line: WeaponLine) -> int:
        """武器行 ⧉：复制 `Weapons` 那一行另存一份，本行（`TurretWeapons`）指过去。"""
        return self._clone_template(
            vm.TABLE_WEAPONS,
            line.weapon_id,
            CellRef(vm.TABLE_TURRET_WEAPONS, line.link_row_id, vm.COLUMN_WEAPON),
            "门武器",
        )

    def clone_ammo(self, line: AmmoLine) -> int:
        """弹药行 ⧉：复制 `Ammunitions` 那一行另存一份，本行（`WeaponAmmunitions`）指过去。"""
        return self._clone_template(
            vm.TABLE_AMMO,
            line.ammo_id,
            CellRef(vm.TABLE_WEAPON_AMMO, line.row_id, vm.COLUMN_AMMO),
            "条弹种",
        )

    def clone_turret(self, line: TurretLine) -> int:
        """炮塔行 ⧉：复制 `Turrets` 那一行另存一份 **＋ 它下面的 `TurretWeapons` 行**。

        ❗ 为什么带上武器：`TurretWeapons` 是**按 TurretId 挂在炮塔模板上**的
        （那张表没有 `UnitId`）—— 只复制炮塔本体的话，新塔是一座**空壳**，用户还得一门一门
        重加（实测 Ka-52 三座塔共 10 行武器）。弹（`WeaponAmmunitions`）不用抄：
        它按 (UnitId, WeaponId) 记，武器 Id 没变 ⇒ 原来的携弹行照样生效。
        """
        context = self._context
        if context is None:
            return 0
        if not line.unit_row_id:
            self.status_message.emit(
                "这一座是选项的槽填进来的（TurretUnits 里没有本单位那一行）—— 复制不了"
            )
            return 0
        link = CellRef(vm.TABLE_TURRET_UNITS, line.unit_row_id, vm.COLUMN_TURRET)
        # ❗ 武器那几行**跟着同一条撤销记录**走（extras）：从前是"克隆 2 条 ＋ 每门武器 1 条"，
        #    用户按一次 Ctrl+Z 只回来一半（用户 2026-10-02：「做吧」）。
        copied, make_copies = self._turret_weapon_copies(line.turret_id)
        new_id, message = context.clone_row_to(
            vm.TABLE_TURRETS, line.turret_id, link, extras=make_copies
        )
        extra = f"（连它下面的 {copied} 门武器一起）" if copied else "（它原来没有武器行）"
        self.status_message.emit(f"{message} {extra} —— 右栏就是新炮塔，直接在上面改")
        self.refresh()
        self.row_selected.emit(vm.TABLE_TURRETS, new_id)
        return new_id

    def _turret_weapon_copies(
        self, turret_id: int
    ) -> tuple[int, Callable[[int], list[tuple[str, dict]]]]:
        """这座炮塔模板名下的 `TurretWeapons` 行怎么照抄 —— 返回 (条数, 取新塔 Id 的函数)。

        为什么返回函数：新塔 Id 要等 `clone_row_to` 克隆完才知道，而槽里那几行的
        `TurretId` 必须写成新塔 —— 交给它回调时再算。
        行值里的 `Id` 一律**去掉**（新 Id 由 core_add_row 取，与 insert_row 同一条路）。
        """
        context = self._context
        if context is None:
            return 0, lambda _new_id: []
        rows = context.data.t(vm.TABLE_TURRET_WEAPONS)
        columns = list(context.instance.table(vm.TABLE_TURRET_WEAPONS).columns)
        # ❗ 先物化：插行会让这张表的缓存失效，边遍历边插会漏行。
        sources = list(rows.where(vm.COLUMN_TURRET, int(turret_id)))

        def make(new_turret_id: int) -> list[tuple[str, dict]]:
            out: list[tuple[str, dict]] = []
            for row in sources:
                values = {column: rows.get(row, column) for column in columns}
                values.pop(vm.COLUMN_ID, None)
                values[vm.COLUMN_TURRET] = int(new_turret_id)
                out.append((vm.TABLE_TURRET_WEAPONS, values))
            return out

        return len(sources), make

    def _build_group(self, group: SlotGroup) -> None:
        """一个改装槽一组：先抬头行，再它挂上来的那几座塔（塔下面照旧是武器 / 弹药）。"""
        head = SlotGroupRow(group)
        head.fold_requested.connect(lambda row=head: self.toggle_group_fold(row))
        self._add_body(head)
        self._group_rows.append(head)
        # ❗ **抬头自己不进这批**：折的是"它下面那几座塔"，不是它自己 —— 把它也算进来的话
        #    点一下就整行消失（用户 2026-10-02：「你那个折叠栏，点一下就直接消失了」），
        #    那个槽再也点不回来。
        members: list[QWidget] = []
        for line in group.turrets:
            # 抬头是这一组的**父节点** ⇒ 下面那几行从第 1 档起算（用户：「按树缩进」）。
            self._build_turret(line, group_id=group.modification_id, base_level=1)
            turret_row = self._turret_row_at.get((group.modification_id, line.turret_id))
            if turret_row is not None:
                members.append(turret_row)
            self._group_turrets.setdefault(group.modification_id, []).append(line.turret_id)
        # 这一组的成员 = **它自己那几行塔**（武器/弹药跟着塔的折叠态走，不在这里直接 show/hide）
        self._fold_groups[group.modification_id] = members
        self._apply_group_fold(group.modification_id)

    # ------------------------------------------------------------------ #
    # 折这一组（只 hide / show，控件不销毁、不写库）
    # ------------------------------------------------------------------ #

    def toggle_group_fold(self, row: SlotGroupRow) -> bool:
        """点了分组抬头那颗 ▸/▾：收 / 放**这一组**的炮塔行（连他们的武器/弹药一起）。"""
        mid = int(row.modification_id)
        if mid in self._open_groups:
            self._open_groups.discard(mid)
        else:
            self._open_groups.add(mid)
        return self._apply_group_fold(mid, settle=True)

    def _apply_group_fold(self, modification_id: int, *, settle: bool = False) -> bool:
        """按 `_closed_groups` 把这一组的行 show/hide，并换记号。返回是否展开。"""
        mid = int(modification_id)
        opened = mid in self._open_groups
        for widget in self._fold_groups.get(mid, ()):
            widget.setVisible(opened)
        # 组展开了还要看**每一座塔自己**收没收（两层折叠互不覆盖）
        for turret_id in self._group_turrets.get(mid, ()):
            self._apply_turret_fold(turret_id)
        row = self.row_for_group(mid)
        if row is not None:
            row.set_folded(not opened)
        if settle:
            self._layout_settle()
        return opened

    def group_rows(self) -> tuple[SlotGroupRow, ...]:
        """分组抬头那几行（顺序 = 界面上的顺序）。"""
        return tuple(self._group_rows)

    def row_for_group(self, modification_id: int) -> SlotGroupRow | None:
        for row in self._group_rows:
            if row.modification_id == int(modification_id):
                return row
        return None

    def is_group_open(self, modification_id: int) -> bool:
        """这一组现在展开着吗（**默认 False = 收起**）。"""
        return int(modification_id) in self._open_groups

    def turret_rows_for(self, turret_id: int) -> tuple[TurretRow, ...]:
        """这座塔**全部**的行控件（它被几个槽用到就有几份；用户 2026-10-02 要的多对多）。"""
        return tuple(self._turret_rows_at.get(int(turret_id), ()))

    def _build_turret(self, line: TurretLine, *, group_id: int = 0, base_level: int = 0) -> None:
        """一座炮塔 ＋ 它下面的武器 ＋ 武器下面的弹（顺序 = 界面上的顺序）。

        这一座默认**收起**：它下面那几行建完就按 _open_turrets hide 掉（只 hide，
        控件一个都不销毁 —— 展开回来是原样）。

        `base_level` = 这一棵树**从第几档起算**：画在分组抬头（🗂 改装槽）下面时是 **1**
        （抬头是父节点、塔是它的子节点）—— 用户 2026-10-03：「**按树缩进**」；
        没有分组抬头那条老路（`refresh` 的 else 支）用 0。
        """
        # ❗ 子炮塔（`Turrets.ParentTurretId`）排在父塔后面、**深一档**；它下面的
        #    武器 / 弹药也跟着再深一档 —— 不跟着深的话三层的档位就撞上了。
        depth = int(base_level) + line.depth
        turret_row = TurretRow(line, self._store, level=depth)
        turret_row.activated.connect(lambda row=turret_row: self._on_clicked(row))
        turret_row.remove_requested.connect(lambda row=turret_row: self._remove_turret(row))
        turret_row.fold_requested.connect(lambda row=turret_row: self.toggle_turret_fold(row))
        turret_row.add_weapon_button().clicked.connect(
            lambda _checked=False, row=turret_row: self._open_add_weapon(row)
        )
        # ⧉ = 复制这座炮塔的**模板**另存一份（连它下面的武器行一起）。
        # 口径与 ③–⑦ 属性行那颗同源（交接 §23）：复制模板、本单位关联行指过去，别人不动。
        clone_turret = turret_row.add_tool(
            "⧉", tooltip="复制这座炮塔另存一份（连它下面的武器一起）"
        )
        clone_turret.clicked.connect(lambda _=False, line=line: self.clone_turret(line))
        turret_row.group_id = int(group_id)  # 这一份画在哪个槽下面（多对多：同一座塔会有好几份）
        self._add_body(turret_row)
        self._turret_rows.append(turret_row)
        self._turret_rows_at.setdefault(line.turret_id, []).append(turret_row)
        self._turret_row_at[(int(group_id), line.turret_id)] = turret_row
        children: list[QWidget] = []  # 收在**这一座炮塔**下面的行 = 武器行
        weapon_keys: list[tuple[int, int]] = []
        for weapon in line.weapons:
            weapon_row = WeaponRow(weapon, self._store, level=1 + depth)
            weapon_row.activated.connect(lambda row=weapon_row: self._on_clicked(row))
            weapon_row.remove_requested.connect(lambda row=weapon_row: self._remove_weapon(row))
            weapon_row.fold_requested.connect(lambda row=weapon_row: self.toggle_weapon_fold(row))
            weapon_row.add_ammo_button().clicked.connect(
                lambda _checked=False, row=weapon_row: self._open_add_ammo(row)
            )
            clone_weapon = weapon_row.add_tool("⧉", tooltip="复制这门武器另存一份（别人不动）")
            clone_weapon.clicked.connect(lambda _=False, line=weapon: self.clone_weapon(line))
            weapon_row.group_id = int(group_id)
            self._add_body(weapon_row)
            self._weapon_rows.append(weapon_row)
            self._weapon_row_at[(int(group_id), weapon.source_turret_id, weapon.weapon_id)] = (
                weapon_row
            )
            children.append(weapon_row)
            # ❗ 弹行收在**这一门武器**下面（第三层折叠），不进炮塔那份名单。
            key = (int(weapon.source_turret_id), int(weapon.weapon_id))
            weapon_keys.append(key)
            self._weapon_rows_at.setdefault(key, []).append(weapon_row)
            ammo_children: list[QWidget] = []
            for ammo in weapon.ammo:
                ammo_row = AmmoRow(ammo, self._store, level=2 + depth)
                ammo_row.activated.connect(lambda row=ammo_row: self._on_clicked(row))
                ammo_row.remove_requested.connect(lambda row=ammo_row: self._remove_ammo(row))
                ammo_row.quantity_requested.connect(
                    lambda row=ammo_row: self.open_quantity_editor(row)
                )
                clone_ammo = ammo_row.add_tool("⧉", tooltip="复制这个弹种另存一份（别人不动）")
                clone_ammo.clicked.connect(lambda _=False, line=ammo: self.clone_ammo(line))
                ammo_row.group_id = int(group_id)
                self._add_body(ammo_row)
                self._ammo_rows.append(ammo_row)
                self._ammo_row_at[(int(group_id), ammo.source_turret_id, ammo.row_id)] = ammo_row
                ammo_children.append(ammo_row)
            self._fold_ammo.setdefault(key, []).extend(ammo_children)
        # ❗ **累积**（同一座塔的第二份不能让第一份的那些行掉出索引 —— 那会让"展开这一座"
        #    只放出最后一份的武器/弹药）。
        self._fold_children.setdefault(line.turret_id, []).extend(children)
        self._turret_weapons.setdefault(line.turret_id, []).extend(weapon_keys)
        self._apply_turret_fold(line.turret_id)

    # ------------------------------------------------------------------ #
    # 折这一座炮塔（只 hide / show，控件不销毁、不写库）
    # ------------------------------------------------------------------ #

    def toggle_turret_fold(self, row: TurretRow) -> bool:
        """点了某座炮塔最左边那颗 ▸/▾：收 / 放它下面的武器与弹药行。

        **只 hide / show**（AGENTS §0.7：不重建、不闪）＋ **一个字节都不写库**。
        返回"现在是不是展开"。
        """
        turret_id = int(row.turret_id)
        if turret_id in self._open_turrets:
            self._open_turrets.discard(turret_id)
        else:
            self._open_turrets.add(turret_id)
        return self._apply_turret_fold(turret_id, settle=True)

    def _apply_turret_fold(self, turret_id: int, *, settle: bool = False) -> bool:
        """按 _open_turrets 把这一座的武器/弹药行 show/hide，并换记号。返回是否展开。

        ❗ 同一座塔可能在**好几个槽**下面各有一份控件（用户 2026-10-02：「既然也被使用，
        就给使用的那个折叠栏里也加上啊」）—— 折叠态按 turret_id 全局一致，但**可见性要
        各算各的**：某一份所在的那个组收着时，那一份的武器/弹药不许露出来。
        """
        turret_id = int(turret_id)
        opened = turret_id in self._open_turrets
        for child in self._fold_children.get(turret_id, ()):
            owner = int(getattr(child, "group_id", 0))
            child.setVisible(opened and self.is_group_open(owner))
        # 武器行露面之后，**弹行还要看它自己那门武器收没收**（第三层折叠，互不覆盖）。
        for key in self._turret_weapons.get(turret_id, ()):
            self._apply_weapon_fold(key)
        for row in self._turret_rows_at.get(turret_id, ()):
            row.set_folded(not opened)
        if settle:
            # 收起/展开之后立刻激活布局：不激活的话这一帧还按旧几何排（AGENTS §0.7 的"空一拍"）。
            self._layout_settle()
        return opened

    def open_turrets(self) -> frozenset[int]:
        """现在**展开着**的那几座炮塔（读数口；默认空集 = 全收起）。"""
        return frozenset(self._open_turrets)

    # ------------------------------------------------------------------ #
    # 折这一门武器（第三层：只 hide / show 它下面的携弹行）
    # ------------------------------------------------------------------ #

    def toggle_weapon_fold(self, row: WeaponRow) -> bool:
        """点了某门武器最左边那颗 ▸/▾：收 / 放它下面那几条携弹行。

        **只 hide / show**（AGENTS §0.7：不重建、不闪）＋ **一个字节都不写库**。
        返回"现在是不是展开"。
        """
        key = (int(row.source_turret_id), int(row.weapon_id))
        if key in self._open_weapons:
            self._open_weapons.discard(key)
        else:
            self._open_weapons.add(key)
        return self._apply_weapon_fold(key, settle=True)

    def _apply_weapon_fold(self, key: tuple[int, int], *, settle: bool = False) -> bool:
        """按 _open_weapons 把这一门下面的携弹行 show/hide，并换记号。返回是否展开。

        ❗ **三层的可见性要一起算**：这一门自己收着、或它那座炮塔收着、或它所在的改装槽
        收着 —— 弹行都不许露出来。与 _apply_turret_fold 同一条规矩（多对多：同一门武器
        可能在好几个槽下面各有一份控件，折叠态按武器键全局一致、可见性各算各的）。
        """
        key = (int(key[0]), int(key[1]))
        opened = key in self._open_weapons
        turret_open = key[0] in self._open_turrets
        for child in self._fold_ammo.get(key, ()):
            owner = int(getattr(child, "group_id", 0))
            child.setVisible(opened and turret_open and self.is_group_open(owner))
        for row in self._weapon_rows_at.get(key, ()):
            row.set_folded(not opened)
        if settle:
            # 收起/展开之后立刻激活布局（不激活这一帧还按旧几何排 —— AGENTS §0.7 的"空一拍"）。
            self._layout_settle()
        return opened

    def open_weapons(self) -> frozenset[tuple[int, int]]:
        """现在**展开着**的那几门武器（读数口；默认空集 = 全收起）。"""
        return frozenset(self._open_weapons)

    def _add_body(self, widget: QWidget) -> None:
        """往主体尾部（stretch **之前**）加一个控件，并记下来（重画时要能摘干净）。"""
        self._section.body_layout().insertWidget(self._tail_index(), widget)
        self._body_widgets.append(widget)
        if isinstance(widget, _ChainRow):
            self._rows.append(widget)

    def _tail_index(self) -> int:
        """主体最后那个 stretch 的下标（stretch 永远是最后一项）。"""
        return max(0, self._section.body_layout().count() - 1)

    def _clear_body(self) -> None:
        """把上一次画的行全摘掉（走 `detach`：先 hide 再 setParent(None) 再 deleteLater）。"""
        layout = self._section.body_layout()
        for widget in self._body_widgets:
            layout.removeWidget(widget)
            detach(widget)
        self._body_widgets.clear()
        self._rows.clear()
        self._turret_rows.clear()
        self._weapon_rows.clear()
        self._ammo_rows.clear()
        self._group_rows.clear()
        self._fold_groups.clear()
        self._group_turrets.clear()
        self._turret_rows_at.clear()
        self._turret_row_at.clear()
        self._weapon_row_at.clear()
        self._ammo_row_at.clear()
        # 下一轮 _build_turret 会重建这四份索引（跟着控件走）：炮塔 -> 武器行、
        # 武器 -> 携弹行、武器 -> 它自己那几份行、炮塔 -> 它那几门武器的键。
        self._fold_children.clear()
        self._fold_ammo.clear()
        self._weapon_rows_at.clear()
        self._turret_weapons.clear()

    def _layout_settle(self) -> None:
        """挂完控件激活布局。

        ❗ 不激活时 `LayoutRequest` 是排队处理的，实机抓帧会看到那一块**空一拍** ——
        这就是"闪一下"的另一半机理（见 `AGENTS.md` §0.7 与
        `_work/probe_rail_editor_place.py`：`grab()` 抢在布局前就抓到空栏）。
        """
        self._section.body_layout().activate()
        layout = self.layout()
        if layout is not None:
            layout.activate()

    # ------------------------------------------------------------------ #
    # 读数（用例钉控件里那一份状态，不现算）
    # ------------------------------------------------------------------ #

    def section(self) -> BenchSection:
        return self._section

    def context(self):
        return self._context

    def tree(self) -> TurretTree | None:
        """现在画着的那一份取数结果（没单位时是 None）。"""
        return self._tree

    def hint_text(self) -> str:
        """抬头右边那句**渲染后**的文字（`HINT` 是模板，这儿才是填好的那一份）。"""
        return self._hint

    def rows(self) -> tuple[BenchRow, ...]:
        """界面上那些行，顺序 = 画出来的顺序（炮塔 → 它的武器 → 它的弹，一层层往下）。"""
        return tuple(self._rows)

    def turret_rows(self) -> tuple[TurretRow, ...]:
        return tuple(self._turret_rows)

    def weapon_rows(self) -> tuple[WeaponRow, ...]:
        return tuple(self._weapon_rows)

    def ammo_rows(self) -> tuple[AmmoRow, ...]:
        return tuple(self._ammo_rows)

    def lines(self) -> tuple[TurretLine, ...]:
        """炮塔那一层的数据（与 `turret_rows()` 一一对应）。"""
        return tuple(row.line for row in self._turret_rows)

    def row_for_turret(self, turret_id: int) -> TurretRow | None:
        return next((row for row in self._turret_rows if row.turret_id == int(turret_id)), None)

    def selected(self) -> tuple[str, int]:
        return self._selected

    def selected_table(self) -> str:
        return self._selected[0]

    def selected_row_id(self) -> int:
        return self._selected[1]

    def add_button(self) -> BenchAction:
        return self._add

    def picker(self) -> RowPicker | None:
        return self._picker

    def picker_visible(self) -> bool:
        # ❗ 用 isHidden() 不用 isVisible()：这一块自己没被 show() 过时（用例里常见）
        #    isVisible() 恒 False（父链没上台），那是假绿；isHidden() 只回答"宿主自己藏没藏"。
        return self._picker is not None and not self._picker_host.isHidden()

    def status_text(self) -> str:
        """最后说出的那句人话（与 `status_message` 发出去的是同一句）。"""
        return self._status

    def has_editor(self) -> bool:
        """现在开着携弹量就地编辑器吗。"""
        return self._editor_row is not None

    def open_ammo_row(self) -> AmmoRow | None:
        """现在开着编辑器的那一条携弹行（没开时 None）。"""
        return self._editor_row

    def editor(self) -> QWidget | None:
        """挂在「×数量」那一格上的编辑控件（用例按它检查浮层）。"""
        return self._editor_row.editor if self._editor_row is not None else None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _say(self, text: str) -> None:
        """说一句人话（状态栏）—— 成功 / 没有改动 / 解释都走它，**绝不弹窗**。"""
        self._status = str(text)
        self.status_message.emit(self._status)


__all__ = [
    "ADD_AMMO_TEXT",
    "ADD_TURRET_TEXT",
    "ADD_WEAPON_TEXT",
    "AMMO_ICON_HEIGHT",
    "HINT",
    "HINT_NO_OPTION",
    "LEVEL_INDENT",
    "NO_TURRET_TEXT",
    "NO_UNIT_TEXT",
    "STATE_NO_TURRETS",
    "STATE_NO_UNIT",
    "STATE_ROWS",
    "TITLE",
    "TURRET_ICON_HEIGHT",
    "WEAPON_ICON_HEIGHT",
    "AmmoRow",
    "QuantityEditor",
    "TurretRow",
    "TurretsBench",
    "WeaponRow",
    "block_key",
    "render_hint",
]
