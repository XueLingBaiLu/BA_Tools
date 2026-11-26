"""顶栏面包屑：「← 军械库 › Stryker RV › 单位」（界面重做定稿 块 2 / 块 4）。

用户口径（块 4）：

    「回去的路两条都要」—— 面板头一个「⬅ 回上一层」，面包屑的父级可点。

所以这里画两样东西：

- 最左边一个返回箭头（回**上一层**，与面板头那个按钮同一个动作）；
- 后面一串「父级 › … › 当前」：**父级可点、当前层不可点**（它已经在看的就是这一层）。

不画面包屑时（军械库 / 数据表这些顶层页）整条控件是空的 —— 不占宽度、不占高度。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ba_mod_maker.app.theme.icons import prepare_icon
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.app.viewmodel.row_icon import RowMark
from ba_mod_maker.app.widgets.detach import detach

#: 分隔符：游戏与产品里都用这一个（别换成 "/" —— 路径感不对）。
SEPARATOR = "›"

#: 段上那张**真图标**的高度（宽度按原图比例）。从 token 取，不写死像素。
CRUMB_ICON_HEIGHT = TOKENS.size.spec_icon
#: 图标最宽到高度的几倍（单位缩略图是宽的，别把面包屑顶爆）。
CRUMB_ICON_MAX_RATIO = 3


class BreadcrumbBar(QWidget):
    """一串父级 + 当前层。父级可点，最后一个不可点。"""

    #: 点了返回箭头（= 回上一层，与面板头那个按钮同义）。
    back_clicked = Signal()
    #: 点了第 index 个父级（0 = 最靠左的那个，即"军械库"）。
    segment_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "breadcrumb")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(TOKENS.spacing.xs)

        self._back = QPushButton("←")
        self._back.setProperty("role", "crumb-back")
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.setToolTip("回上一层（Esc 同效）")
        self._back.clicked.connect(self.back_clicked)
        layout.addWidget(self._back)

        self._host = QWidget()
        self._host.setProperty("role", "breadcrumb")
        self._row = QHBoxLayout(self._host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(TOKENS.spacing.xs)
        layout.addWidget(self._host)
        layout.addStretch(1)
        self._segments: tuple[str, ...] = ()
        #: 一段一个**图标位**（与 _segments 一一对应；None = 这一段没有图标位）。
        self._marks: tuple[RowMark | None, ...] = ()
        self._show_back = False
        #: 每一段**真的画上真图**没有。独立记一份，不读 isVisible() ——
        #: 窗口没 show 时整棵树都不可见（用例与冒烟就是这么跑的）。
        self._drawn: tuple[bool, ...] = ()
        #: 每一段图标位上的**兜底字**（有真图时是空串）。
        self._fallbacks: tuple[str, ...] = ()
        #: 唯一的图标缓存（main_window 的 self._armory.icons）；None = 一个都不画。
        self._icon_store = None
        self.set_trail(())

    # ------------------------------------------------------------------ #

    def set_icons(self, store) -> None:
        """接上**唯一**那份图标缓存（main_window 的 self._armory.icons）。

        store 为 None = 没装游戏 / 测试构造：图标位退回 emoji 兜底，文字照旧。
        """
        self._icon_store = store
        if self._segments:
            self.set_trail(self._segments, back=self._show_back, marks=self._marks)

    def _pixmap_of(self, mark: RowMark | None) -> QPixmap | None:
        """段上那张真图 —— **只走 IconStore.pixmap**（bundle 直读，见 ADR 0003）。

        ❗ 这里**不碰** cache/icons 下的 PNG：那是没装游戏时的兜底，而且旧提取把
        151 张取成了 outline 空心版（用户报过的 Konkurs）—— ADR 0003 §决定 4。
        """
        icon = mark.icon if mark is not None else None
        if icon is None or self._icon_store is None or not icon.key:
            return None
        pixmap = self._icon_store.pixmap(icon.family, icon.key)
        return pixmap if pixmap is not None and not pixmap.isNull() else None

    def _scaled(self, pixmap: QPixmap, family: str, key: str, target) -> QPixmap:
        """这一段的图标：**按族（+键）翻 + 按 DPR 缩放**，统一走 prepare_icon。

        ❗ 直接 scaled(逻辑尺寸) 在 150% 屏上会被 Qt 再放大 1.5 倍 → 图标发虚
        （用户 2026-09-30 实机报「模糊」）；而武器 / 改型选项那两族原图**朝左**、
        界面要翻（用户 2026-10-01：「还是老问题，武器能不能让它们朝右」）——
        两件事都别在这里自己写，prepare_icon 是唯一入口。
        """
        return prepare_icon(
            pixmap,
            family,
            QSize(CRUMB_ICON_HEIGHT * CRUMB_ICON_MAX_RATIO, CRUMB_ICON_HEIGHT),
            target,
            key,
        )

    def set_trail(
        self, segments: Sequence[str], *, back: bool = True, marks: Sequence = ()
    ) -> None:
        """重画。segments 的最后一个是**当前层**（不可点），前面的都可点。

        back=False 时连箭头都不画（顶层页：军械库 / 国家 / 专精 / 数据表 / 检索）。
        marks 与 segments 一一对应（`UnitEditorScreen.trail_marks()` 给的 RowMark）：
        **图标位只能有一个** —— 有真图画真图，没真图才画它的 emoji 兜底，
        两样都没有就只画文字（绝不留空框）。
        """
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # ❗ 必须走 detach（先 hide 再 setParent(None)）：这一段原来只写
                #    setParent(None)，而**可见**控件被摘下来会被 Qt 直接映射成一个
                #    真顶层窗 —— 用户看到的就是"闪一下的小弹窗"。而这条路径每次换行
                #    （set_focus -> trail_changed）都走一遍，所以点一行就闪一串。
                #    实测（_work/gui_opt/probe_stray.py）：修前一次换行 5 个
                #    QPushButton/QLabel 走火，修后 0 个。
                detach(widget)
        self._segments = tuple(segments)
        self._marks = tuple(marks)
        self._show_back = back and len(self._segments) > 1
        self._back.setVisible(self._show_back)
        drawn: list[bool] = []
        falls: list[str] = []
        if not self._segments:
            self._drawn = ()
            self._fallbacks = ()
            self._host.setVisible(False)
            return
        self._host.setVisible(True)
        for index, text in enumerate(self._segments):
            if index:
                sep = QLabel(SEPARATOR)
                sep.setProperty("role", "crumb-sep")
                self._row.addWidget(sep)
            seg_mark = self._marks[index] if index < len(self._marks) else None
            pixmap = self._pixmap_of(seg_mark)
            icon_family = (
                seg_mark.icon.family if seg_mark is not None and seg_mark.icon is not None else ""
            )
            # ❗ 键也要一路传到底：option_pictures 是**逐资产**判翻不翻的
            #    （families.family_mirrors(name, key)）—— 漏传这一处就按族默认翻，
            #    面包屑上的改装图又跟游戏反了。
            icon_key = (
                seg_mark.icon.key if seg_mark is not None and seg_mark.icon is not None else ""
            )
            emoji = seg_mark.emoji if seg_mark is not None else ""
            drawn.append(pixmap is not None)
            falls.append("" if pixmap is not None else emoji)
            last = index == len(self._segments) - 1
            if last:
                # 当前层是 QLabel（不可点）：图标位单起一个小 QLabel 画在它前面。
                if pixmap is not None or emoji:
                    slot = QLabel()
                    slot.setProperty("role", "crumb-icon")
                    slot.setFont(self.font())
                    if pixmap is not None:
                        slot.setPixmap(self._scaled(pixmap, icon_family, icon_key, slot))
                    else:
                        slot.setText(emoji)
                    self._row.addWidget(slot)
                label = QLabel(text)
                label.setProperty("role", "crumb-current")
                self._row.addWidget(label)
            else:
                button = QPushButton(text)
                button.setProperty("role", "crumb")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip(f"回到「{text}」")
                if pixmap is not None:
                    button.setIcon(QIcon(self._scaled(pixmap, icon_family, icon_key, button)))
                    button.setIconSize(QSize(CRUMB_ICON_HEIGHT, CRUMB_ICON_HEIGHT))
                elif emoji:
                    # 按钮放不下子控件：emoji 兜底就前缀进文字（段名本身不变）。
                    button.setText(f"{emoji} {text}")
                button.clicked.connect(lambda _=False, i=index: self.segment_clicked.emit(i))
                self._row.addWidget(button)
        self._drawn = tuple(drawn)
        self._fallbacks = tuple(falls)

    @property
    def segments(self) -> tuple[str, ...]:
        return self._segments

    @property
    def icon_drawn(self) -> tuple[bool, ...]:
        """每一段真的画上**真图**没有（与 segments 一一对应）。"""
        return self._drawn

    @property
    def mark_texts(self) -> tuple[str, ...]:
        """每一段图标位上的**兜底字**（有真图时是空串；"" = 这一段没有图标位）。"""
        return self._fallbacks

    @property
    def trail_text(self) -> str:
        return f" {SEPARATOR} ".join(self._segments)

    @property
    def back_visible(self) -> bool:
        return self._back.isVisible()


__all__ = ["SEPARATOR", "BreadcrumbBar"]
