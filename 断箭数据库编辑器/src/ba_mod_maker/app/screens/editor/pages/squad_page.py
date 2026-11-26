"""步兵班专用页：一行一名士兵（+1 / ✕），右栏是**选中那名士兵的 6 列**。

设计 §3 与图 B ③（单件大图 docs/设计稿-步兵班编辑器.png）。用户原话：
「步兵班加人减人，点 +1、减人点 ❌」。

## 版面（照设计稿）

    一个班 = 一行一名士兵   加人点「+1」；减人直接点「✕」   成员数 5 · 重量 750（125 × 5）
    ┌ 1 │ AK-74RMO │ 副武器 GP-34 │ RU_VDV_officer │ 阵亡波次 2 │ ✕ ┐
    ┌ 2 │ AK-74RMO │ 没有副武器   │ RU_VDV_rifle_1 │ 阵亡波次 2 │ ✕ ┐
    （序号列是**纯数字**；「第 N 名」只出现在右栏那句人话摘要里）
    [ +1 加一名士兵 ]
    复制最后一行、给一个新 Id —— 卡片「成员数」+1、重量 +125
    ⚠ 步兵武器那条不变量：SquadMembers 用到的每一门枪都必须在同单位的 SquadWeapons 里

右栏 = **选中那一名的 6 列**（SquadMembers 的全部列，一个不少）：
Id / DeathPriority / ModelFileName / PrimaryWeaponId / SpecialWeaponId / UnitId。

⚠ 一处与设计稿的偏差（**不改基类**）：设计稿右栏的抬头是「选中第 1 名之后」，
而右栏那个面板（widgets/row_entries.py::RowEntriesPanel）不归本页 —— 抬头与摘要
都由 `entries_title()` / `entries_summary()` 给（本页的重写在下面）。
于是那句话写在**摘要位**（entries_summary），内容一样：
「选中第 1 名之后 · SquadMembers#1466 · 6 列 · 独占 · 改它只动这个兵」。

## 界面不算数（任务书第 5 条）

「哪些行属于这个单位 / 这一格落在哪 / 这一行被几个单位共用」全部走 core：

- context.row_ids(TABLE) -> core/edit/scope.py::UnitScope
- context.cells_of_row(TABLE, id) -> core/edit/cells.py::row_cells
- context.sharing.of(TABLE, id) -> core/edit/sharing.py::SharingIndex

重量走**卡片同一份实现**（viewmodel/unit_card.py::card_weight = 125 × 人数），
界面不另写一个公式 —— 否则卡片与这一页会各说各话。

## 弹窗（AGENTS §5）

加人 / 减人**成功不弹窗**：走 changed 信号，由外层写状态栏（见 last_message）。
只有真出错（变更集拒绝这次写）才弹一次。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.screens.editor.pages.base import EditorContext, EditorPage
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.unit_card import (
    INFANTRY_WEIGHT_PER_MEMBER,
    card_weight,
    new_member_values,
    squad_loadout,
)
from ba_mod_maker.core.edit.cells import FieldCell
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.errors import BaModMakerError

#: 班成员表。24 张表之一，列是（实测 6 列）：
#: Id / DeathPriority / ModelFileName / PrimaryWeaponId / SpecialWeaponId / UnitId。
TABLE = "SquadMembers"

#: 行里那几格的宽度（序号与 ✕ 另有固定宽）。
_WEAPON_WIDTH = 132
_DEATH_WIDTH = 92
_INDEX_WIDTH = 64
_MODEL_MIN_WIDTH = 80

#: 左栏的最小宽度。
#: ❗ 为什么非要定这个数：基类把左右两栏按 1 : 2 分（base.py 的 addWidget(host, 1) /
#: addWidget(panel, 2)），1460 宽的窗口下左栏只拿到约 486px —— 而一行
#: "序号 + 主武器 + 副武器 + 模型 + 阵亡波次 + ✕" 至少要 570px。挤不下的后果不是
#: 换行，而是**行里冒出一条横向滚动条、行尾那个红 ✕ 被推到屏幕外**（实测踩过）。
#: 所以这里把左栏的最小宽定死：
#:   64 + 132 + 132 + 80（模型下限）+ 92 + 28（✕）+ 5×6（间距）+ 12（边距）= 570
#: 再留 50px 给纵向滚动条与模型键（最长 21 字符），取 620。
_LEFT_MIN_WIDTH = 620


def _positive(value: object) -> bool:
    """库里 0 = 「不使用」，不是「指向 Id 0」（关系图 §5 第 1 条）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class MemberRow(QFrame):
    """一行 = 一名士兵：序号 │ 主武器 │ 副武器 │ 模型 │ 阵亡波次 │ ✕。

    **点行体 = 选中这一名**（右栏跟着换成它的 6 列）。点行里那几个按钮不会传上来 ——
    子控件自己吃掉点击，所以「点枪就地改」与「点行选中」不打架。
    """

    #: 点了行体（参数：行 Id）—— 选中这一名。
    activated = Signal(int)
    #: 点了行里的一格（参数：行 Id, 列名）—— 就地改。
    cell_activated = Signal(int, str)
    #: 点了行尾的 ✕（参数：行 Id）—— 删这一名。
    remove_requested = Signal(int)

    def __init__(
        self,
        context: EditorContext,
        position: int,
        row_id: int,
        *,
        selected: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.row_id = row_id
        self.position = position
        self.setProperty("role", "editor-row")
        # 选中态目前靠序号前那个「▸」表达（QSS 里 editor-row 没有 [selected] 那条规则，
        # 而 qss.py 不归本页 —— 加一条角色要 lead 点头）。属性照设，将来加规则就生效。
        self.setProperty("selected", "true" if selected else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{TABLE}#{row_id} —— 点这一行的空白处 = 选中这一名（右栏换成它的 6 列）")

        line = QHBoxLayout(self)
        line.setContentsMargins(
            TOKENS.spacing.sm, TOKENS.spacing.xs, TOKENS.spacing.sm, TOKENS.spacing.xs
        )
        line.setSpacing(TOKENS.spacing.sm)

        # ❗ 行标签是**纯序号**（2026-10-01 口径：「第 1 名之类的，能不能就显示 1」，
        #    与关系树 ebfb102 同一条）。右栏那句人话「选中第 N 名之后」是**句子**，不动。
        self.index_label = QLabel(f"{'▸ ' if selected else ''}{position}")
        self.index_label.setProperty("role", "entry-name")
        self.index_label.setFixedWidth(_INDEX_WIDTH)
        line.addWidget(self.index_label)

        self.primary = self._cell_button("PrimaryWeaponId", _WEAPON_WIDTH)
        line.addWidget(self.primary)
        self.special = self._cell_button("SpecialWeaponId", _WEAPON_WIDTH)
        line.addWidget(self.special)

        model = context.cell(CellRef(TABLE, row_id, "ModelFileName"))
        self.model = QLabel(model.text if model is not None else "—")
        # 模型是**文本键**不是外键（词典：步兵真正的模型在这一列，不是 Units.ModelFileName）。
        self.model.setProperty("role", "entry-name")
        # 这一格是**可压缩的**：模型键最长 21 个字符，让它按 sizeHint 撑宽就会把
        # 后面的阵亡波次与 ✕ 挤出左栏（实测踩过）。宁可这里截断 —— 全文在悬浮提示
        # 与右栏那一列里。
        self.model.setMinimumWidth(_MODEL_MIN_WIDTH)
        self.model.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.model.setToolTip(
            f"{TABLE}#{row_id} · ModelFileName\n"
            "士兵模型键（Units.ModelFileName 那列步兵全是 US_Rangers，改它没用）"
        )
        line.addWidget(self.model, 1)

        self.death = self._cell_button("DeathPriority", _DEATH_WIDTH)
        line.addWidget(self.death)

        self.remove = QPushButton("✕")
        self.remove.setProperty("role", "row-action")
        self.remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove.setToolTip(f"删掉 {TABLE}#{row_id}（Ctrl+Z 能撤回来）")
        self.remove.clicked.connect(lambda _checked=False: self.remove_requested.emit(self.row_id))
        line.addWidget(self.remove)

    # ------------------------------------------------------------------ #

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """点行体 = 选中这一名（子控件里的按钮自己处理点击，不会走到这里）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.row_id)
        super().mouseReleaseEvent(event)

    def _cell_button(self, column: str, width: int) -> QPushButton:
        cell = self.context.cell(CellRef(TABLE, self.row_id, column))
        button = QPushButton(_cell_text(column, cell))
        button.setProperty("role", "entry-value")
        button.setFixedWidth(width)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if cell is not None:
            hint = "；外键按名字搜，不填 Id" if cell.foreign else ""
            button.setToolTip(f"{cell.place} —— 点它就地改{hint}")
        button.clicked.connect(
            lambda _checked=False, name=column: self.cell_activated.emit(self.row_id, name)
        )
        return button


def _cell_text(column: str, cell: FieldCell | None) -> str:
    """行里那一格的显示文字。**显示的一律是名字**（外键不给用户看 Id）。"""
    if cell is None:
        return "—"
    if column == "SpecialWeaponId":
        # 0 = 「这名士兵没有副武器」（词典 SpecialWeaponId 那条就这么写），
        # 不是"指向 Id 0" —— 所以不写「（不使用）」。
        return f"副武器 {cell.text}" if _positive(cell.raw) else "没有副武器"
    if column == "DeathPriority":
        return f"阵亡波次 {cell.text}"
    return cell.text


class SquadPage(EditorPage):
    """班组成员：一行一名，+1 / ✕ 真的改数据。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("班组成员", "一行一名士兵：+1 加人 · 行尾 ✕ 减人", parent)
        self._selected: int | None = None
        self._rows: list[MemberRow] = []
        self._add_button: QPushButton | None = None
        self._last_message = ""

    # ------------------------------------------------------------------ #
    # 选中 / 读
    # ------------------------------------------------------------------ #

    @property
    def selected_id(self) -> int | None:
        """当前选中的是哪一名（右栏列的就是它的 6 列）。"""
        return self._selected

    @property
    def member_ids(self) -> tuple[int, ...]:
        """这个班的成员行 Id（**阅读序 = Id 升序**，来自 core 的 scope）。"""
        return self._member_ids

    @property
    def member_count(self) -> int:
        return len(self._member_ids)

    @property
    def member_rows(self) -> tuple[MemberRow, ...]:
        """左栏那几行（测试与将来别处要点它用）。"""
        return tuple(self._rows)

    @property
    def add_button(self) -> QPushButton | None:
        return self._add_button

    @property
    def can_add(self) -> bool:
        """这个单位能不能加人。**不是步兵就不能** —— 载具的人员走炮塔与运输。"""
        return self._add_button is not None and self._add_button.isEnabled()

    @property
    def last_message(self) -> str:
        """最近一次动作给状态栏的那句话（成功不弹窗，走这里 + changed 信号）。"""
        return self._last_message

    @property
    def counters_text(self) -> str:
        """「成员数 N · 重量 N×125」—— 与卡片同一份实现算出来的（见模块注释）。"""
        count, weight, computed = self._counters()
        if computed:
            return f"成员数 {count} · 重量 {weight}（{INFANTRY_WEIGHT_PER_MEMBER} × {count}）"
        if count:
            return f"成员数 {count} · 重量 {weight}（Units.Weight 原列）"
        return f"成员数 {count}"

    def _counters(self) -> tuple[int, int, bool]:
        context = self._require_context()
        weight, computed = card_weight(context.data, context.unit_id)
        return len(self._member_ids), weight, computed

    def select(self, row_id: int) -> None:
        """选中某一名：右栏换成它的 6 列。点行体走的就是这条路。"""
        if row_id not in self._member_ids:
            return
        if self._selected == row_id:
            return
        self._selected = row_id
        self._rebuild()

    # ------------------------------------------------------------------ #
    # 进出
    # ------------------------------------------------------------------ #

    def set_context(self, context: EditorContext) -> None:
        rows = context.row_ids(TABLE)
        self._selected = rows[0] if rows else None
        super().set_context(context)

    def refresh(self) -> None:
        self._touch_squad_cache()
        self._reselect()
        super().refresh()

    def _reselect(self) -> None:
        """选中的那一行没了（删了 / 撤销了插入）就退回第一名，别指着不存在的行。"""
        rows = self._member_ids
        if self._selected not in rows:
            self._selected = rows[0] if rows else None

    def _touch_squad_cache(self) -> None:
        """丢掉 SquadMembers 那份筛结果缓存（Rows.where 会缓存）。

        ❗ 为什么本页要自己丢：context.data 这一份 CardData **不是**卡片那一份
        （卡片在 detail_panel.set_instance 里自建），变更集的订阅刷不到它 ——
        实测症状就是「插了一行成员，卡片还显示 5 人」。
        """
        context = self.context
        if context is not None:
            context.data.t(TABLE).invalidate()

    # ------------------------------------------------------------------ #
    # 左栏
    # ------------------------------------------------------------------ #

    def build_left(self) -> QWidget | None:
        # 左栏一画就要读成员数与重量（都经缓存），所以先把缓存对齐。
        self._touch_squad_cache()
        self._rows = []

        host = QWidget()
        # 见 _LEFT_MIN_WIDTH：不加这一句，行尾的 ✕ 会被 1 : 2 的分栏挤出屏幕。
        host.setMinimumWidth(_LEFT_MIN_WIDTH)
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(TOKENS.spacing.sm)

        box.addWidget(self._build_title_row())
        if self._member_ids:
            box.addWidget(self._build_member_list(), 1)
        else:
            box.addWidget(self._build_empty_hint())
            box.addStretch(1)
        box.addWidget(self._build_add_button())
        box.addWidget(_note(self._weight_note()))
        box.addWidget(
            _note(
                "⚠ 步兵武器那条不变量：SquadMembers 用到的每一门枪，都必须在同单位的"
                " SquadWeapons 里（单向包含）。违反会在立绘上冒校验角标 —— 由规则引擎守。"
            )
        )
        box.addWidget(_note("主武器 / 副武器都是外键 —— 按名字搜、不填 Id；显示的一律是名字。"))
        return host

    def _build_title_row(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS.spacing.sm)

        title = QLabel("一个班 = 一行一名士兵")
        title.setProperty("role", "entries-title")
        row.addWidget(title)

        subtitle = QLabel("加人点「+1」；减人直接点「✕」")
        subtitle.setProperty("role", "entries-subtitle")
        row.addWidget(subtitle)

        row.addStretch(1)
        counters = QLabel(self.counters_text)
        counters.setProperty("role", "entries-summary")
        counters.setToolTip(
            f"步兵重量 = {INFANTRY_WEIGHT_PER_MEMBER} × 人数。\n"
            "不是 Units.Weight 那一列 —— 步兵那一列恒为 1；\n"
            "出处：LoadUnits imul eax,[rax+18h],7Dh（0x7D = 125）。"
        )
        row.addWidget(counters)
        return bar

    def _build_member_list(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setProperty("role", "detail-body")
        lines = QVBoxLayout(inner)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(TOKENS.spacing.xs)
        context = self._require_context()
        for position, row_id in enumerate(self._member_ids, start=1):
            widget = MemberRow(context, position, row_id, selected=(row_id == self._selected))
            widget.activated.connect(self.select)
            widget.cell_activated.connect(self._edit_cell)
            widget.remove_requested.connect(self._remove_member)
            lines.addWidget(widget)
            self._rows.append(widget)
        lines.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_empty_hint(self) -> QWidget:
        hint = QLabel(
            "这个单位没有班组成员行 —— SquadMembers 里没有一行 UnitId 指向它。\n"
            "载具 / 飞机本来就没有：他们的人员走炮塔与运输，不是这一套。"
            "（数据源实测：1134 行班成员只覆盖 194 个单位，全是步兵。）"
        )
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        return hint

    def _build_add_button(self) -> QPushButton:
        context = self._require_context()
        button = QPushButton("+1 加一名士兵")
        button.setProperty("role", "row-add")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if squad_loadout(context.data, context.unit_id) is not None:
            button.setToolTip(
                "复制最后一行、给一个新 Id —— 卡片「成员数」+1、"
                f"重量 +{INFANTRY_WEIGHT_PER_MEMBER}（Ctrl+Z 能撤回来）"
            )
        else:
            button.setEnabled(False)
            button.setToolTip(
                "这个单位不是步兵（Units.Type 不是 Infantry）—— 原版只有 194 个步兵有班成员行"
            )
        button.clicked.connect(self._add_member)
        self._add_button = button
        return button

    def _weight_note(self) -> str:
        return (
            f"复制最后一行、给一个新 Id —— 卡片「成员数」+1、重量 +{INFANTRY_WEIGHT_PER_MEMBER}。"
            f"（步兵重量 = {INFANTRY_WEIGHT_PER_MEMBER} × 人数，不是 Units.Weight 那一列"
            " —— 步兵那列恒为 1）"
        )

    # ------------------------------------------------------------------ #
    # 动作（成功不弹窗：changed 信号 + last_message）
    # ------------------------------------------------------------------ #

    def _edit_cell(self, row_id: int, column: str) -> None:
        """点行里的一格：先把这一名选中（右栏换成它的 6 列），再就地打开那一格。"""
        self.select(row_id)
        context = self.context
        cell = context.cell(CellRef(TABLE, row_id, column)) if context is not None else None
        if cell is not None:
            self._open_inline_editor(cell)

    def _add_member(self) -> None:
        context = self.context
        if context is None:
            return
        # 读之前对齐缓存：要复制的是"最后那一行"，读旧了会复制错人。
        self._touch_squad_cache()
        loadout = squad_loadout(context.data, context.unit_id)
        if loadout is None:
            self._status("这个单位不是步兵，没有班成员这一套 —— 没加")
            return
        values = new_member_values(context.data, loadout)
        try:
            row_id = context.insert_row(TABLE, values)
        except BaModMakerError as exc:
            # 只有错误才弹（AGENTS §5 的弹窗规则）。
            QMessageBox.warning(self, "没有加成", str(exc))
            return
        self._selected = row_id
        self._after_change(f"已加一名士兵：{TABLE} #{row_id}")

    def _remove_member(self, row_id: int) -> None:
        context = self.context
        if context is None:
            return
        try:
            context.delete_row(TABLE, row_id)
        except (BaModMakerError, KeyError) as exc:
            QMessageBox.warning(self, "没有删掉", str(exc))
            return
        self._reselect()
        self._after_change(f"已删掉一名士兵：{TABLE} #{row_id}（Ctrl+Z 能撤回来）")

    def _after_change(self, message: str) -> None:
        self._rebuild()
        self._status(message)
        self.changed.emit()

    def _status(self, message: str) -> None:
        """把一句话交给外层写状态栏（页自己不给状态栏）。

        ❗ 从前这里只 setToolTip + setProperty("lastMessage", ...) —— 两个都
        **全仓零读取者**（grep 过），于是那两句人话（"这个单位不是步兵，没有班
        成员这一套 —— 没加"、"已加 / 已删 …（Ctrl+Z 能撤回来）"）谁也看不见：
        动作做了，用户看到的却是"没反应"。现在走 signal —— 与其它页同一条线
        （EditorPage.status_message -> UnitEditorScreen -> 顶栏状态栏）。
        _last_message 留着：last_message 属性还有用例在读。
        """
        self._last_message = message
        self.status_message.emit(message)

    def _require_context(self) -> EditorContext:
        context = self.context
        assert context is not None
        return context

    @property
    def _member_ids(self) -> tuple[int, ...]:
        context = self.context
        return () if context is None else context.row_ids(TABLE)

    # ------------------------------------------------------------------ #
    # 右栏：选中那一名的 6 列
    # ------------------------------------------------------------------ #

    def entries_cells(self) -> tuple[FieldCell, ...]:
        context = self.context
        assert context is not None
        if self._selected is None:
            return ()
        return context.cells_of_row(TABLE, self._selected)

    def entries_title(self) -> str:
        """设计稿右栏的抬头就是这句：「选中第 N 名之后」。"""
        context = self.context
        if context is None or self._selected is None:
            return "这个单位的全部词条"
        rows = self._member_ids
        position = rows.index(self._selected) + 1 if self._selected in rows else 0
        return f"选中第 {position} 名之后" if position else "这个单位的全部词条"

    def entries_order(self) -> tuple[str, ...]:
        return (TABLE,)

    def entries_summary(self) -> str:
        """右栏摘要位 —— 设计稿那句「选中第 N 名之后」就落在这里（见模块注释）。"""
        context = self.context
        assert context is not None
        if self._selected is None:
            return f"{TABLE} · 没有成员行"
        rows = self._member_ids
        position = rows.index(self._selected) + 1 if self._selected in rows else 0
        return (
            f"选中第 {position} 名之后 · {TABLE}#{self._selected} · {len(self.entries_cells())} 列"
        )


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "muted")
    label.setWordWrap(True)
    return label


__all__ = ["TABLE", "MemberRow", "SquadPage"]
