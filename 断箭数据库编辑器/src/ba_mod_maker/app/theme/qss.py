"""令牌 → Qt 样式表（QSS）。

为什么用模板而不是直接写 .qss 文件：

- QSS 不支持变量，直写就会出现色值散落各处的情况；
- 用 `@{color.bg-panel}` 这种占位符可以让“改主题 = 改 tokens.py”真正成立，
  并且能被测试直接断言“没有漏开的占位符”。

占位符语法：`@{group.field-name}`，例 `@{color.accent}`、`@{spacing.lg}`。数值单位（px）需自己写。
"""

from __future__ import annotations

import re

from ba_mod_maker.app.theme.tokens import TOKENS, ThemeTokens

# 按控件分段组织：每一段都带注释，方便定位与扩展。
_QSS_TEMPLATE = """
/* ======================================================================== */
/* 基础                                                                   */
/* ======================================================================== */

QWidget {
    background-color: @{color.bg-base};
    color: @{color.text-primary};
    font-family: @{typography.ui-family};
    font-size: @{typography.size-md}px;
}

QMainWindow, QDialog {
    background-color: @{color.bg-base};
}

QWidget:disabled {
    color: @{color.text-disabled};
}

/* 布局容器不应有自己的背景，否则会遮住父面板的分层 */
QStackedWidget, QSplitter, QScrollArea, QAbstractScrollArea::corner {
    background-color: transparent;
}

/* ======================================================================== */
/* 面板与分隔                                                               */
/* ======================================================================== */

QFrame[panel="true"] {
    background-color: @{color.bg-panel};
    border: 1px solid @{color.border};
    border-radius: @{radius.lg}px;
}

QFrame[panel="elevated"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.lg}px;
}

QFrame[role="separator"] {
    background-color: @{color.border};
    max-height: 1px;
    min-height: 1px;
    border: none;
}

QSplitter::handle {
    background-color: @{color.border};
}

QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QSplitter::handle:hover { background-color: @{color.accent}; }

/* ======================================================================== */
/* 文本                                                                   */
/* ======================================================================== */

QLabel {
    background-color: transparent;
    color: @{color.text-primary};
}

QLabel[role="title"] {
    color: @{color.text-strong};
    font-size: @{typography.size-xl}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="subtitle"] {
    color: @{color.text-strong};
    font-size: @{typography.size-lg}px;
    font-weight: @{typography.weight-medium};
}

QLabel[role="caption"], QLabel[role="hint"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
}

QLabel[role="muted"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

QLabel[role="mono"] {
    font-family: @{typography.mono-family};
    font-size: @{typography.size-sm}px;
}

QLabel[tone="danger"] { color: @{color.danger}; }
QLabel[tone="warning"] { color: @{color.warning}; }
QLabel[tone="success"] { color: @{color.success}; }
QLabel[tone="info"] { color: @{color.info}; }

/* ======================================================================== */
/* 按钮                                                                   */
/* ======================================================================== */

QPushButton {
    background-color: @{color.bg-elevated};
    color: @{color.text-primary};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.md}px;
    padding: @{spacing.xs}px @{spacing.lg}px;
    min-height: @{size.control-height}px;
    font-weight: @{typography.weight-medium};
}

QPushButton:hover {
    background-color: @{color.bg-hover};
    border-color: @{color.accent};
}

QPushButton:pressed { background-color: @{color.bg-active}; }

QPushButton:disabled {
    background-color: @{color.bg-panel};
    color: @{color.text-disabled};
    border-color: @{color.border};
}

QPushButton:focus { border-color: @{color.border-focus}; }

QPushButton[variant="primary"] {
    background-color: @{color.accent};
    color: @{color.text-on-accent};
    border-color: @{color.accent};
    font-weight: @{typography.weight-bold};
}

QPushButton[variant="primary"]:hover {
    background-color: @{color.accent-hover};
    border-color: @{color.accent-hover};
}

QPushButton[variant="primary"]:pressed { background-color: @{color.accent-pressed}; }

QPushButton[variant="primary"]:disabled {
    background-color: @{color.accent-subtle};
    color: @{color.text-disabled};
    border-color: @{color.border};
}

QPushButton[variant="danger"] {
    background-color: transparent;
    color: @{color.danger};
    border-color: @{color.danger};
}

QPushButton[variant="danger"]:hover { background-color: @{color.danger-subtle}; }

QPushButton[variant="ghost"] {
    background-color: transparent;
    border-color: transparent;
}

QPushButton[variant="ghost"]:hover {
    background-color: @{color.bg-hover};
    border-color: @{color.border};
}

QPushButton[variant="ghost"]:pressed { background-color: @{color.bg-active}; }

/* ======================================================================== */
/* 输入类控件                                                               */
/* ======================================================================== */

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
    background-color: @{color.bg-input};
    color: @{color.text-primary};
    border: 1px solid @{color.border-input};
    border-radius: @{radius.md}px;
    padding: @{spacing.xs}px @{spacing.md}px;
    min-height: @{size.control-height}px;
    selection-background-color: @{color.bg-selected};
    selection-color: @{color.text-strong};
}

QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: @{color.border-strong};
}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: @{color.border-focus};
}

QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
    background-color: @{color.bg-panel};
    color: @{color.text-disabled};
}

QLineEdit[state="invalid"], QPlainTextEdit[state="invalid"] { border-color: @{color.danger}; }
QLineEdit[state="warning"] { border-color: @{color.warning}; }

QPlainTextEdit, QTextEdit {
    font-family: @{typography.mono-family};
    font-size: @{typography.size-sm}px;
}

/* ======================================================================== */
/* 下拉与复选                                                               */
/* ======================================================================== */

QComboBox {
    background-color: @{color.bg-input};
    color: @{color.text-primary};
    border: 1px solid @{color.border-input};
    border-radius: @{radius.md}px;
    padding: @{spacing.xs}px @{spacing.md}px;
    min-height: @{size.control-height}px;
}

QComboBox:hover { border-color: @{color.border-strong}; }
QComboBox:focus { border-color: @{color.border-focus}; }
QComboBox:disabled { color: @{color.text-disabled}; background-color: @{color.bg-panel}; }

QComboBox::drop-down { border: none; width: 20px; }

QComboBox QAbstractItemView {
    background-color: @{color.bg-elevated};
    color: @{color.text-primary};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.md}px;
    outline: none;
    padding: @{spacing.xxs}px;
    selection-background-color: @{color.bg-selected};
    selection-color: @{color.text-strong};
}

QCheckBox, QRadioButton {
    background-color: transparent;
    color: @{color.text-primary};
    spacing: @{spacing.sm}px;
}

QCheckBox::indicator, QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid @{color.border-strong};
    background-color: @{color.bg-input};
}

QCheckBox::indicator { border-radius: @{radius.sm}px; }
QRadioButton::indicator { border-radius: 7px; }

QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: @{color.accent}; }

QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: @{color.accent};
    border-color: @{color.accent};
}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background-color: @{color.bg-panel};
    border-color: @{color.border};
}

/* ======================================================================== */
/* 表格与树                                                               */
/* ======================================================================== */

/* 表格网格用 data-family（新宋体打头）。
   它等宽，数值列才能竖着对位；Times New Roman 会破坏这一点。 */
QTableView, QTreeView, QListView {
    font-family: @{typography.data-family};
    background-color: @{color.bg-input};
    alternate-background-color: @{color.bg-panel};
    color: @{color.text-primary};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
    gridline-color: @{color.border};
    outline: none;
    selection-background-color: @{color.bg-selected};
    selection-color: @{color.text-strong};
}

QTreeView, QListView { background-color: @{color.bg-panel}; }

QTableView::item, QTreeView::item, QListView::item {
    padding: @{spacing.xxs}px @{spacing.sm}px;
    min-height: @{size.row-height}px;
    border: none;
}

QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {
    background-color: @{color.bg-hover};
}

QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {
    background-color: @{color.bg-selected};
    color: @{color.text-strong};
}

QHeaderView { background-color: @{color.bg-panel}; border: none; }

QHeaderView::section {
    font-family: @{typography.data-family};
    background-color: @{color.bg-elevated};
    color: @{color.text-secondary};
    border: none;
    border-right: 1px solid @{color.border};
    border-bottom: 1px solid @{color.border};
    padding: @{spacing.xs}px @{spacing.md}px;
    min-height: @{size.header-height}px;
    font-weight: @{typography.weight-medium};
}

QHeaderView::section:hover {
    color: @{color.text-strong};
    background-color: @{color.bg-hover};
}

QTableView QTableCornerButton::section {
    background-color: @{color.bg-elevated};
    border: none;
    border-right: 1px solid @{color.border};
    border-bottom: 1px solid @{color.border};
}

/* ======================================================================== */
/* 标签页                                                                 */
/* ======================================================================== */

QTabWidget::pane {
    background-color: @{color.bg-panel};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
    top: -1px;
}

QTabBar { background-color: transparent; qproperty-drawBase: 0; }

QTabBar::tab {
    background-color: transparent;
    color: @{color.text-secondary};
    border: 1px solid transparent;
    border-top-left-radius: @{radius.md}px;
    border-top-right-radius: @{radius.md}px;
    padding: @{spacing.sm}px @{spacing.xl}px;
    margin-right: @{spacing.xxs}px;
}

QTabBar::tab:hover {
    color: @{color.text-strong};
    background-color: @{color.bg-hover};
}

QTabBar::tab:selected {
    background-color: @{color.bg-panel};
    color: @{color.text-strong};
    border-color: @{color.border};
    border-bottom-color: @{color.bg-panel};
}

/* ======================================================================== */
/* 滚动条                                                                 */
/* ======================================================================== */

/* 滚动条：两种朝向都必须显式给底色。
   为什么不能用 background: transparent / none：
   Qt 对 QScrollBar 的 page 区（滑轨空白）会退回原生画刷，
   在深色界面上就是一条白带。窗口小的时候看不出来，
   一旦出现横向滚动条就非常刺眼。 */
QScrollBar:vertical {
    background-color: @{color.bg-input};
    width: 11px;
    margin: 0;
    border: none;
}

QScrollBar:horizontal {
    background-color: @{color.bg-input};
    height: 11px;
    margin: 0;
    border: none;
}

QScrollBar::handle {
    background-color: @{color.scroll-handle};
    border-radius: @{radius.sm}px;
    min-height: 28px;
    min-width: 28px;
}

QScrollBar::handle:hover { background-color: @{color.scroll-handle-hover}; }

/* 箭头按钮不显示（现代风格），两种朝向都显式给底色。 */
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
    border: none;
    background-color: @{color.bg-input};
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background-color: @{color.bg-input};
}

/* ======================================================================== */
/* 菜单、工具栏、状态栏                                                   */
/* ======================================================================== */

QMenuBar {
    background-color: @{color.bg-panel};
    color: @{color.text-primary};
    border-bottom: 1px solid @{color.border};
    padding: @{spacing.xxs}px;
}

QMenuBar::item {
    background: transparent;
    padding: @{spacing.xs}px @{spacing.lg}px;
    border-radius: @{radius.sm}px;
}

QMenuBar::item:selected {
    background-color: @{color.bg-hover};
    color: @{color.text-strong};
}

QMenu {
    background-color: @{color.bg-elevated};
    color: @{color.text-primary};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.md}px;
    padding: @{spacing.xs}px;
}

QMenu::item {
    padding: @{spacing.sm}px @{spacing.xxl}px @{spacing.sm}px @{spacing.lg}px;
    border-radius: @{radius.sm}px;
}

QMenu::item:selected {
    background-color: @{color.bg-selected};
    color: @{color.text-strong};
}

QMenu::item:disabled { color: @{color.text-disabled}; }

QMenu::separator {
    height: 1px;
    background-color: @{color.border};
    margin: @{spacing.xs}px @{spacing.sm}px;
}

QToolBar {
    background-color: @{color.bg-panel};
    border: none;
    border-bottom: 1px solid @{color.border};
    padding: @{spacing.xs}px;
    spacing: @{spacing.xs}px;
    min-height: @{size.toolbar-height}px;
}

QToolBar::separator {
    background-color: @{color.border};
    width: 1px;
    margin: @{spacing.xs}px @{spacing.sm}px;
}

QToolButton {
    background-color: transparent;
    color: @{color.text-primary};
    border: 1px solid transparent;
    border-radius: @{radius.md}px;
    padding: @{spacing.xs}px @{spacing.md}px;
}

QToolButton:hover {
    background-color: @{color.bg-hover};
    border-color: @{color.border};
}

QToolButton:pressed, QToolButton:checked {
    background-color: @{color.bg-active};
    border-color: @{color.border-strong};
}

QStatusBar {
    background-color: @{color.bg-panel};
    color: @{color.text-secondary};
    border-top: 1px solid @{color.border};
    min-height: @{size.statusbar-height}px;
}

QStatusBar::item { border: none; }

QStatusBar QLabel {
    font-family: @{typography.data-family};
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
    padding: 0 @{spacing.sm}px;
}

/* ======================================================================== */
/* 进度条与工具提示                                                           */
/* ======================================================================== */

QProgressBar {
    background-color: @{color.bg-input};
    color: @{color.text-primary};
    border: 1px solid @{color.border};
    border-radius: @{radius.sm}px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    font-size: @{typography.size-xs}px;
}

QProgressBar::chunk {
    background-color: @{color.accent};
    border-radius: @{radius.sm}px;
}

QToolTip {
    background-color: @{color.bg-elevated};
    color: @{color.text-primary};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.md}px;
    padding: @{spacing.sm}px @{spacing.md}px;
}

/* ======================================================================== */
/* 徽章（校验严重度）                                                     */
/* ======================================================================== */

QLabel[badge="error"] {
    background-color: @{color.danger-subtle};
    color: @{color.danger};
    border: 1px solid @{color.danger};
    border-radius: @{radius.sm}px;
    padding: 0 @{spacing.sm}px;
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}

QLabel[badge="warning"] {
    background-color: @{color.warning-subtle};
    color: @{color.warning};
    border: 1px solid @{color.warning};
    border-radius: @{radius.sm}px;
    padding: 0 @{spacing.sm}px;
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}

QLabel[badge="info"] {
    background-color: @{color.info-subtle};
    color: @{color.info};
    border: 1px solid @{color.info};
    border-radius: @{radius.sm}px;
    padding: 0 @{spacing.sm}px;
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}

QLabel[badge="ok"] {
    background-color: @{color.success-subtle};
    color: @{color.success};
    border: 1px solid @{color.success};
    border-radius: @{radius.sm}px;
    padding: 0 @{spacing.sm}px;
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}

/* ======================================================================== */
/* 区域角色                                                               */
/* ======================================================================== */

QWidget[role="sidebar"] {
    background-color: @{color.bg-panel};
    border-right: 1px solid @{color.border};
}

QWidget[role="page-header"] {
    background-color: @{color.bg-panel};
    border-bottom: 1px solid @{color.border};
}

/* ======================================================================== */
/* 军械库主屏（见 docs/adr/0002-主屏-军械库视图.md）                        */
/* ======================================================================== */

QWidget[role="topbar"] {
    background-color: @{color.bg-panel};
    border-bottom: 1px solid @{color.border};
}

/* 导航条：纵向内边距压到 xs —— 顶部这一块整体要省地方（用户反馈）。 */
QPushButton[role="nav"] {
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: @{spacing.xs}px @{spacing.lg}px;
    color: @{color.text-secondary};
    font-size: @{typography.size-lg}px;
}

QPushButton[role="nav"]:hover {
    color: @{color.text-strong};
    background-color: @{color.bg-hover};
}

QPushButton[role="nav"]:checked {
    color: @{color.text-strong};
    border-bottom: 2px solid @{color.accent};
    font-weight: @{typography.weight-medium};
}

QWidget[role="filter-bar"] {
    background-color: @{color.bg-base};
    border-bottom: 1px solid @{color.border};
}

QPushButton[role="chip"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.sm}px;
    padding: @{spacing.xs}px @{spacing.md}px;
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
}

QPushButton[role="chip"]:hover {
    background-color: @{color.bg-hover};
    color: @{color.text-primary};
}

QPushButton[role="chip"]:checked {
    background-color: @{color.accent-subtle};
    border: 1px solid @{color.accent};
    color: @{color.text-on-accent};
    font-weight: @{typography.weight-medium};
}

/* 这里原来有 sheet-header / sheet-title 两个角色（表名独占一行）。
   2026-10 那一行并进了筛选行（表名就是专精下拉里选中的那一项），角色随之删掉 ——
   留着只会让下一个会话以为界面上还有这么一块。 */

/* 画布与画布里的容器必须透明：程序化背景画在更底层的 BackdropWidget 上，
   盖住就没纹理了。

   ❗ 这件事**不是自动的**：全局 QWidget 规则会给每个控件刷 bg-base，而
   "纯 QWidget 实例"（不是子类）会被 Qt 自动加上 WA_StyledBackground。
   QWidget 子类反而不会被自动刷——所以只有裸容器露馅：
   实踩（用户报的「这里的底部是纯色的」）CategoryRow 里的流容器把每个类别
   最后一行右边的空白刷成了一整块 bg-base，底下 BackdropWidget 的斜纹一点都透不出来。
   详情面板的滚动体同理：它会把卡片间隙刷成 bg-base，而不是面板自己的底色。 */
QWidget[role="card-canvas"],
QWidget[role="card-flow"],
QWidget[role="detail-body"],
QWidget[role="compact-table"],
QWidget[role="stat-block"],
QWidget[role="stat-line"],
QWidget[role="armor-zone"],
QWidget[role="armor-values"] {
    background-color: transparent;
}

QLabel[role="category-label"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-medium};
}

QToolButton[role="page"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.sm}px;
    color: @{color.text-secondary};
    padding: 0;
    font-size: @{typography.size-md}px;
}

QToolButton[role="page"]:hover {
    background-color: @{color.bg-hover};
    color: @{color.text-strong};
    border: 1px solid @{color.border-strong};
}

QToolButton[role="page"]:disabled {
    background-color: transparent;
    color: @{color.text-disabled};
}

QFrame[role="detail-panel"] {
    background-color: @{color.bg-panel};
    border-left: 1px solid @{color.border};
}

/* ---- 单位卡片（军械库右栏）：字号走 card.* 那一套 ---------------------- */
/* ❗ 卡片**专用**字号，不是全局 typography.size-*：全局那套是全应用一起缩过的，
   而这张卡是复刻实机的（用户口径："学游戏 GUI，让它看起来大且清晰"）。 */
QLabel[role="detail-title"] {
    color: @{color.text-strong};
    font-size: @{card.font-title}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="detail-section"] {
    color: @{color.text-primary};
    font-size: @{card.font-section}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="stat-label"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
}

QLabel[role="stat-value"] {
    color: @{color.text-primary};
    font-size: @{typography.size-md}px;
}

QFrame[role="detail-card"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
}

QLabel[role="weapon-name"] {
    color: @{color.text-strong};
    font-size: @{card.font-body}px;
}

QLabel[role="empty-hint"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

/* ======================================================================== */
/* 单位卡片（见 docs/设计-单位卡片复刻.md §10.1 ③）                          */
/* ======================================================================== */

/* 成本：暖金只出现在"成本"这一个位置（与卡片墙上的徽章同一口径）。 */
QLabel[role="card-cost"] {
    color: @{color.cost-text};
    background-color: @{color.cost-bg};
    border-radius: @{radius.sm}px;
    padding: @{spacing.xxs}px @{spacing.md}px;
    font-size: @{typography.size-lg}px;
    font-weight: @{typography.weight-bold};
}

/* 数值行的一格：上面图标、下面数字。

   ❗ **不画底、不画边、不可点** —— 实机那一排数值格就是浮在卡面上的图标 + 数字，
   方框是我们自己加的（用户口径："要游戏里的并列显示，而不是产品 GUI 里的一块一块的"），
   而卡片现在只展示信息（用户口径："取消掉所有的在卡片上编辑信息的功能"）。
   间距由卡片自己按格数给（>8 格压缩），QSS 不管。 */
QFrame[role="stat-chip"] {
    background-color: transparent;
    border: none;
    border-radius: @{radius.sm}px;
}

QLabel[role="stat-name"] {
    color: @{color.text-secondary};
    font-size: @{card.font-note}px;
}

/* 数字：实机那一排是**白色粗体**，比正文大一档 —— 这也是"看着清楚"的来源之一。 */
QLabel[role="stat-number"] {
    color: @{color.text-strong};
    font-size: @{card.font-number}px;
    font-weight: @{typography.weight-bold};
}

/* 「装甲图」：4 个部位压在立绘四角（实机那张卡就是这么叠的）。
   白字 = 动能、橙字 = 破甲 —— 橙**只在 tokens 里**，这里只是引用。
   ❗ 不画底、不画边：实机那两行就是浮在立绘上的字（描边由控件层的
   QGraphicsDropShadowEffect 出，颜色同样取自 tokens）。 */
/* ❗ 底色写死 transparent：这几个标签压在**立绘照片**上，实机那两行就是浮在图上的字。
   全局 QLabel 规则本来就是透明的，这里显式再写一遍是为了将来动全局规则时
   不至于在立绘上冒出一块方框（与 target-chip 同一习惯）。
   装这两行的容器（armor-zone / armor-values）走下面 "透明容器" 那条规则。 */
QLabel[role="armor-zone-name"] {
    background-color: transparent;
    color: @{color.text-secondary};
    font-size: @{card.font-armor-name}px;
}

QLabel[role="armor-kinetic"] {
    background-color: transparent;
    color: @{color.text-strong};
    font-size: @{card.font-armor}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="armor-heat"] {
    background-color: transparent;
    color: @{color.armor-heat};
    font-size: @{card.font-armor}px;
    font-weight: @{typography.weight-bold};
}

/* 右上那排能力（烟雾/冲刺/可空投…）。它不是强调项，别用 accent。 */
QLabel[role="ability-chip"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.sm}px;
    color: @{color.text-primary};
    font-size: @{card.font-body}px;
    padding: @{spacing.xs}px @{spacing.sm}px;
}

/* 武器那一格：×N 徽章 + **大剪影** + 名字（实机那张卡就是这三行）。
   常态不画框（游戏里也没有），详细模式左列选中的那一把给一点底色。 */
QFrame[role="weapon-art"] {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: @{radius.sm}px;
}

QFrame[role="weapon-art"]:hover {
    background-color: @{color.bg-hover};
}

/* 选中态统一成**中性**画法：底色 bg-active + 亮边 border-select。
   用户口径 2026-10-01：「能不能别搞红框里这种蓝色的东西」—— 不许再回到 accent 蓝。 */
QFrame[role="weapon-art"][selected="true"] {
    background-color: @{color.bg-active};
    border-color: @{color.border-select};
}

/* 展开态右栏的详情行：来源与算式用小字，值用大字。 */
QLabel[role="card-note"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
}

/* 弹药的「能打什么」：一排小图标（游戏里也只有图，文案在 tooltip 上）。
   没有素材时这里退成文字，所以文字的颜色与字号也要立得住。 */
/* 「能打什么」那排小图标：实机就是**没有底的白色图标**。
   ❗ 这里不能有 border/padding：标签是按族盒子**钉死**的（20×20），
   加 1px 边 + 2px 内边距会把 20px 的方图裁成 14px 宽 ——
   用户报的"可瞄准目标的图标没显示全"就是这个。 */
QLabel[role="target-chip"] {
    background-color: transparent;
    border: none;
    color: @{color.text-secondary};
    font-size: @{card.font-note}px;
    padding: 0;
}

/* 携弹数：用界面字体（游戏卡片上这个数也是正文字体），不再切到表格字体。 */
QLabel[role="ammo-count"] {
    color: @{color.text-strong};
    font-size: @{card.font-number}px;
    font-weight: @{typography.weight-bold};
}

/* ---- 简略模式那张表（用户口径：要游戏里的并列显示） ------------------ */

/* 列标题。游戏那一行用图标 + 徽章、没有文字，所以这几个是产品自己的列名。 */
QLabel[role="compact-head"] {
    color: @{color.text-secondary};
    font-size: @{card.font-column}px;
}

/* 格子里的值：右对齐（对齐这件事在控件里做，QSS 管不了）。 */
QLabel[role="compact-cell"] {
    color: @{color.text-strong};
    font-size: @{card.font-body}px;
    font-weight: @{typography.weight-medium};
}

/* 行内的 ×N（游戏里那个小徽章是绿的）。 */
QLabel[role="weapon-count"] {
    color: @{color.success};
    font-size: @{card.font-body}px;
    font-weight: @{typography.weight-bold};
}

/* ---- 详细模式：一格数据**一行** -------------------------------------- */

/* 标签贴左、值贴右，同一行。**没有方框** —— 一块一块的正是用户不要的那个。 */
QLabel[role="stat-line-label"] {
    color: @{color.text-primary};
    font-size: @{card.font-body}px;
}

QLabel[role="stat-line-value"] {
    color: @{color.text-strong};
    font-size: @{card.font-body}px;
    font-weight: @{typography.weight-medium};
}

/* 一个弹种一栏：栏与栏之间一条细线分开，弹种那一行给一点底色
   （游戏里那条深色带就是这个位置）。 */
QFrame[role="ammo-block"] {
    border-top: 1px solid @{color.border};
}

/* 军械库卡片详细模式里那个弹种行（screens/armory/detail_panel.py::_ammo_block）。
   ❗ 这一条**不是死代码，别按"看着像漏删"删掉** —— 2026-09-30 夜里删过一次，
   白改一轮：编辑器中栏那一块（screens/editor/ammo_block.py）的携弹行挂的是
   `entry-line`（跟词条行共用整行高亮），命中的不是这条；用户说的"弹药块跟上面
   不一致"也不在这条规则上。改选择器之前先 grep `setProperty("role"` 看命中了谁。 */
QFrame[role="ammo-line"] {
    background-color: @{color.bg-input};
    border-radius: @{radius.sm}px;
}

/* 详细模式右边那一栏（左竖排武器 + 右详情里的右半边）。 */
QFrame[role="detail-line"] {
    background-color: @{color.bg-input};
    border: 1px solid @{color.border};
    border-radius: @{radius.sm}px;
}

/* 底部「自定义选项」：一组一行（组名 + 当前选择 + 成本）。 */
QFrame[role="option-row"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
}

QLabel[role="option-cost"] {
    color: @{color.cost-text};
    font-size: @{card.font-body}px;
    font-weight: @{typography.weight-bold};
}

/* 改装槽的**选中态**（块 5：槽内互斥，选中的那一项要看得出来）。
   与武器那一格同一套画法：底色 + 边，色值只从 tokens 来；
   边用中性的 border-select（不再是 accent 蓝）。 */
QFrame[role="option-row"][selected="true"] {
    background-color: @{color.bg-active};
    border: 1px solid @{color.border-select};
}

QLabel[role="option-selected"] {
    color: @{color.text-strong};
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}

/* 校验角标：就一个感叹号（用户定的第 9 条）。悬浮才说问题。 */
QLabel[role="badge-warn"] {
    background-color: @{color.warning-subtle};
    border: 1px solid @{color.warning};
    border-radius: @{radius.sm}px;
    color: @{color.warning};
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
    padding: 0 @{spacing.xs}px;
}

QLabel[role="badge-error"] {
    background-color: @{color.danger-subtle};
    border: 1px solid @{color.danger};
    border-radius: @{radius.sm}px;
    color: @{color.danger};
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
    padding: 0 @{spacing.xs}px;
}

/* ======================================================================== */
/* 编成台（单位修改 GUI 重做 · docs/交接-2026-10-01-单位编成台设计.md）        */
/* ======================================================================== */

/* 中栏一块：抬头 + 主体。三张编成台（步兵/载具/飞机）共用。 */
QFrame[role="bench-section"] {
    background-color: @{color.bg-panel};
    border: 1px solid @{color.border};
    border-radius: @{radius.lg}px;
}

QWidget[role="bench-section-head"] {
    background-color: @{color.bg-elevated};
    border-bottom: 1px solid @{color.border};
    border-top-left-radius: @{radius.lg}px;
    border-top-right-radius: @{radius.lg}px;
}

QLabel[role="bench-section-title"] {
    color: @{color.text-strong};
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="bench-section-hint"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
}

/* 折叠记号 ▸/▾：**单占一格**（不并进标题文字）。
   宽度与高度由代码钉死（`bench_kit.CARET_WIDTH / CARET_HEIGHT` = 24×20）——
   它同时是炮塔行那一格的折叠槽，用户实机报过「要点这个小三角形，很不好操作」。
   ❗ 别在这儿写 min-width 去"顺手钉宽"：QSS 的尺寸会**盖掉** setFixedSize
   （先例见下面 bench-action 那条注释），两边打架时以谁为准很难推理，尺寸只留一个出处。 */
QLabel[role="bench-section-caret"] {
    color: @{color.text-muted};
    font-size: @{typography.size-md}px;
}

/* 悬浮就把那一格点亮 —— 记号本身只是个三角，这是"这一格点得动"的反馈。 */
QLabel[role="bench-section-caret"]:hover {
    color: @{color.text-strong};
    background-color: @{color.bg-hover};
    border-radius: @{radius.sm}px;
}

/* 「＋ 加一项」/「⧉ 复制」那一排。primary = 这一块的主动作。 */
QPushButton[role="bench-action"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.sm}px;
    color: @{color.text-primary};
    font-size: @{typography.size-sm}px;
    padding: 0 @{spacing.md}px;
    /* ❗ 全局 QPushButton 那条 min-height: @{size.control-height}（28）会**盖掉**
       BenchAction 的 setFixedHeight(22)：实际最小高算成 30（28 + 上下边框），
       行尾按钮于是比它所在的那一行还高 5px —— 那 5px 被下一行盖住、点不到。
       实测（_work/probe_bench_kit_metrics.py）：改前 row=28 / button=30（越界 2px + 边），
       现在高度由 QSS 定死（min=max=22），行高 28 装得下。
       ⚠️ 别只写 min-height: 0 —— 那会让 setFixedHeight 失效（polish 会把最小高改回 0），
       按钮缩成 14~18px 的小方块。这一仓的既定口径是"尺寸档位写在 QSS 里"
       （见 qss.py 里 cardsize 那段）。 */
    min-height: 22px;
    max-height: 22px;
}
QPushButton[role="bench-action"]:hover {
    background-color: @{color.bg-hover};
    border-color: @{color.border-select};
}
/* ❗ **主动作不再有另一套样式**（用户 2026-10-02 两句话定死：「这三个按钮和其它按钮保持一致，
   即框的颜色要一致」→「底色也拉平」）。改前这条是
       底 accent-subtle / 框 border-focus / 字 text-strong
   而通用那条是 bg-elevated / border-strong / text-primary ——
   （⚠️ 这里**只写令牌名、不抄十六进制值**：值只有一处出处 tokens.py，抄在这儿会静默过期；
     `tests/test_theme.py::test_no_hardcoded_colors_outside_tokens` 也不许注释里出现字面色值。）
   同一排里 ①/③/⑨ 那三颗主动作与 ②/④–⑧ 那些一眼不是一套，所以整条删掉。
   `BenchAction(primary=True)` 这个**属性留着**（"这一块的主动作"仍是个有意义的事实，
   装配方与用例按它认），但**不再影响长相**：要改外观改通用那条，别再加第二条。
   ⛔ 别再给它加回一套颜色 —— 那正是用户连着退回两次的东西。 */

/* 主体容器与格子矩阵：**必须透明**（否则把面板底色刷成 bg-base，见上面那条白名单）。 */
QWidget[role="bench-body"],
QWidget[role="bench-grid"],
QWidget[role="bench-slot-line"],
QLabel[role="bench-icon"] {
    background-color: transparent;
    border: 0;
}

/* 一行：图标 │ 名字 │ 说明 │ 值 │ ✕ │ ↗ */
QFrame[role="bench-row"] {
    background-color: @{color.bg-elevated};

/* 编成台**块内分组**那一条折叠栏（② 改装槽里每个槽）：整条可点 + hover 底。
   ❗ 与块抬头（bench-section-head）**必须分得出来** —— 它是块内部的一层，不是第二层块。 */
QWidget[role="bench-group-head"] {
    background-color: transparent;
    border-radius: @{radius.sm}px;
}

QWidget[role="bench-group-head"]:hover {
    background-color: @{color.bg-hover};
}

/* 分组抬头里的**槽名**那一格：比块抬头（text-strong）弱一档，但仍是一行的主角。 */
QLabel[role="bench-group-title"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-bold};
}
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
}
QFrame[role="bench-row"]:hover {
    background-color: @{color.bg-hover};
}
QFrame[role="bench-row"][selected="true"] {
    background-color: @{color.bg-active};
    border: 1px solid @{color.border-select};
}

QLabel[role="bench-row-name"] {
    color: @{color.text-strong};
    font-size: @{typography.size-md}px;
}
QLabel[role="bench-row-detail"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
}
QLabel[role="bench-row-value"] {
    color: @{color.text-primary};
    font-family: "@{typography.data-family}";
    font-size: @{typography.size-md}px;
}

/* 改装选项的**费用**那一格 —— 暖金（tokens 里 cost_text 就是留给"成本"的，
   别的语义色都不许抢它）。设计稿 ④ 那一列的 +10 / +25 / +55 用的就是它。 */
QLabel[role="bench-row-cost"] {
    color: @{color.cost-text};
    font-family: "@{typography.data-family}";
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-bold};
}

/* 行尾那颗小按钮（✕ / ↗）。**不加边框**，免得每行两个方块。 */
QPushButton[role="bench-row-tool"] {
    background-color: transparent;
    border: 0;
    color: @{color.text-disabled};
    font-size: @{typography.size-sm}px;
    padding: 0 @{spacing.xs}px;
    /* 16px = 行内小按钮的点击面：比 ✕ 那个字形大一圈，又不会顶破 28px 的行。 */
    min-height: 16px;
    max-height: 16px;
}
QPushButton[role="bench-row-tool"]:hover {
    color: @{color.text-primary};
}

/* 一格（步兵一格=一个兵 / 飞机一格=一个挂载点）。 */
QFrame[role="bench-slot"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
}
QFrame[role="bench-slot"]:hover {
    background-color: @{color.bg-hover};
}
QFrame[role="bench-slot"][selected="true"] {
    background-color: @{color.bg-active};
    border: 1px solid @{color.border-select};
}

QLabel[role="bench-slot-index"] {
    color: @{color.text-muted};
    font-family: "@{typography.mono-family}";
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}
QLabel[role="bench-slot-line"] {
    color: @{color.text-primary};
    font-size: @{typography.size-sm}px;
}
QLabel[role="bench-slot-foot"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
}

QLabel[role="bench-empty"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

/* 右栏：选中项的全部列 + 入口。 */
QFrame[role="bench-rail"] {
    background-color: @{color.bg-panel};
    border-left: 1px solid @{color.border};
}
QLabel[role="bench-rail-title"] {
    color: @{color.text-strong};
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-bold};
}
QLabel[role="bench-rail-key"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
}
QLabel[role="bench-rail-value"] {
    color: @{color.text-strong};
    font-family: "@{typography.data-family}";
    font-size: @{typography.size-sm}px;
}
/* 详情视图里那一组的小标题（「弹药 Javelin（Ammunitions #44）」）。 */
QLabel[role="bench-rail-group"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-xs}px;
    font-weight: @{typography.weight-bold};
}

/* ① 炮塔块那颗 ⇄ 的**就地菜单**（21 个槽逐行 `槽 N · 名字`）。
   role 主要是给用例 findChild 找那个菜单用的；外观走上面的全局 QMenu，
   这里只钉一条**最小宽度**下限 —— 槽名长短差得远（`空` vs `Bushmaster2 Jav`），
   不给下限时菜单会随最长那一条忽宽忽窄。 */
QMenu[role="bench-slot-menu"] {
    min-width: @{spacing.xxxl}px;
}

/* 详情里**只有一组**时那一行标题（2026-10-02）：那一组不套折叠栏，所以标题要
   正经一行 —— 用户原话「这种地方你就别搞折叠栏了，又小又难看」。
   比折叠栏那档 xs 大一档（sm），颜色与字重沿用同一支。 */
QLabel[role="bench-rail-group-title"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
    font-weight: @{typography.weight-bold};
}

/* 右栏分组那一整条**可点抬头**（▸ + 标题）：给一条透明规则 ——
   角色在代码里设了，QSS 里就必须有选择器（tests/test_theme.py 守着这条）。 */
QWidget[role="bench-rail-group-head"] {
    background-color: transparent;
}

/* 右栏分组的折叠记号（与编成台各块那一颗同一套手感 / 同一套颜色）。 */
QLabel[role="bench-rail-group-caret"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
    min-width: @{spacing.sm}px;
}

QLabel[role="bench-rail-sub"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
    letter-spacing: 0.5px;
}

/* 中栏的小胶囊（专精可用性 / 立绘音效 / 全 24 表浏览…）。
   ⚠️ 2026-10-02：唯一的调用方「⑤ 其他入口」整块删掉了 —— 这两条规则**暂时没有调用方**，
   故意留着（与 BenchPage.page_requested 那条件缝同一条口径：还没接的线不删）。 */
QPushButton[role="bench-chip"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.sm}px;
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
    padding: @{spacing.xxs}px @{spacing.md}px;
    min-height: 0;
}
QPushButton[role="bench-chip"]:hover {
    background-color: @{color.bg-hover};
    color: @{color.text-primary};
}

/* ======================================================================== */
/* 单位编辑器（双击军械库上的一个单位进入）                                  */
/* ======================================================================== */

/* 顶栏：← 返回 + 面包屑 + 实例说明 + 保存 */
QWidget[role="editor-header"] {
    background-color: @{color.bg-panel};
    border-bottom: 1px solid @{color.border};
}

QLabel[role="editor-crumb"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-md}px;
}

QLabel[role="editor-instance"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

/* 右栏「这个单位的全部词条」 */
QWidget[role="entries-panel"] {
    background-color: @{color.bg-panel};
}

/* 抬头左边那个**图标位**：只放一张图（真图，没真图才放 emoji）。
   它本来靠全局 QLabel 那条（background 透明）就够 —— 这里显式写一遍是因为
   tests/test_theme.py::test_every_role_set_in_code_has_a_selector 要求"设了 role
   就得有选择器"（那条守卫是 badge-warning 掉样式那次立的）。写出来同时把
   "这一格是图标位、别给它刷底色"钉成明文：树行尾按钮被全局 QWidget 刷过一次底色。 */
QLabel[role="entries-icon"] {
    background-color: transparent;
    border: none;
}

QLabel[role="entries-title"] {
    color: @{color.text-strong};
    font-size: @{typography.size-lg}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="entries-subtitle"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

QLabel[role="entries-summary"] {
    color: @{color.info};
    font-size: @{typography.size-sm}px;
}

QPushButton[role="entry-group"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
    color: @{color.text-strong};
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-bold};
    padding: @{spacing.xs}px @{spacing.sm}px;
    text-align: left;
}

QPushButton[role="entry-group"]:hover {
    background-color: @{color.bg-hover};
}

QLabel[role="entry-count"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
}

/* 树的第二级：一行（表#Id）的组头。比表头轻一档 —— 它是"表下面的东西"，
   两级一样重就分不出层次了。 */
QPushButton[role="entry-row"] {
    background-color: transparent;
    border: none;
    border-left: 2px solid @{color.border-strong};
    color: @{color.text-primary};
    font-size: @{typography.size-sm}px;
    padding: 0 @{spacing.sm}px;
    text-align: left;
}

QPushButton[role="entry-row"]:hover {
    background-color: @{color.bg-hover};
    border-left: 2px solid @{color.accent};
}

/* 左边那棵单位关系树：字号走 size-body（用户实机反馈：偏小）。 */
QTreeView[role="relation-tree-view"],
QTreeWidget[role="relation-tree-view"] {
    font-size: @{typography.size-body}px;
}

/* 一行词条：中文名 │ 值 │ 「表#Id · 原列名」 */
QLabel[role="entry-name"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-body}px;
}

QPushButton[role="entry-value"] {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: @{radius.sm}px;
    color: @{color.text-strong};
    font-size: @{typography.size-body}px;
    padding: 1px @{spacing.xs}px;
    text-align: left;
}

QPushButton[role="entry-value"]:hover {
    background-color: @{color.bg-hover};
    border: 1px solid @{color.border-strong};
}

/* ❗ 改过的那一格**不再画蓝框**（用户口径 2026-10-01：「能不能别搞红框里
   这种蓝色的东西」）。"有改动没落盘"由顶栏那颗胶囊（「N 处未保存」）说，
   不该让每一格自己顶一个蓝框 —— 新加的成员整行都是"改动过"，看上去像坏了。 */

/* 「这个槽现在选中的就是它」（改装选项页那一排互斥选项，块 5 的选中态）。
   与 dirty（"正在看这一行"）分开：一个槽里只有一项是 selected —— 正在看的那个
   可以不是它（点开一个没选中的选项看它的 46 列是允许的）。 */
QPushButton[role="entry-value"][selected="true"] {
    background-color: @{color.bg-active};
    border: 1px solid @{color.border-select};
    color: @{color.text-strong};
    font-weight: @{typography.weight-bold};
}

QLabel[role="entry-place"] {
    color: @{color.text-disabled};
    font-size: @{typography.size-xs}px;
}

/* 原始 Id / 原始值：外键显示名字、枚举显示中文，但**原始那个数要看得见**。
   做成"小徽章"而不是普通文字 —— 它是备查用的，不该跟值抢眼。 */
QLabel[role="entry-id"] {
    background-color: @{color.bg-input};
    border: 1px solid @{color.border};
    border-radius: @{radius.sm}px;
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
    padding: 0 @{spacing.xs}px;
}

/* 「点开一格」的就地编辑浮层 */
QFrame[role="inline-editor"] {
    background-color: @{color.bg-elevated};
    /* ❗ 灰边，不是强调色（用户口径 2026-09-30 实机：
       「让它看起来只有 1 个框，且这个框的边缘是灰色的」）。 */
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
}

/* ❗ 浮层里的输入控件必须是**紧凑**的。
   全局那条 QLineEdit/QComboBox 规则写着 min-height: 28px（普通表单用），
   而"一行词条"的行高只有 24px —— 不压的话输入框比行还高，上下被裁掉，
   实机看到的就是"一个只有右边有蓝边的破框"（用户报的"编辑栏显示有问题"）。
   这里只压浮层**里面**的那几个控件，不动全局那条。 */
QFrame[role="inline-editor"] QLineEdit,
QFrame[role="inline-editor"] QComboBox {
    /* ❗ **不许再画自己那层框**：全局 QLineEdit / QComboBox 各带一条边框，
       浮层再画一条，看起来就是"两个框套在一起"（用户报过两次：
       「像是有两个重叠在了一起」）。整个浮层只留外面那一层灰框。 */
    border: none;
    background: transparent;
    min-height: 0;
    padding: 0 @{spacing.xs}px;
}

QFrame[role="inline-editor"] QPushButton {
    min-height: 0;
    padding: 0 @{spacing.xs}px;
}

QFrame[role="inline-editor"] QCheckBox {
    min-height: 0;
}

QLabel[role="inline-label"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
}

QLabel[role="inline-title"] {
    color: @{color.text-strong};
    font-size: @{typography.size-lg}px;
    font-weight: @{typography.weight-bold};
}

QLabel[role="inline-place"] {
    color: @{color.text-muted};
    font-size: @{typography.size-xs}px;
}

/* 「这一行还被 N 个单位用着」——共享只在点开这一格时说 */
QLabel[role="inline-share"] {
    color: @{color.warning};
    font-size: @{typography.size-sm}px;
}

QLabel[role="inline-note"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

QLabel[role="inline-raw"] {
    color: @{color.text-disabled};
    font-size: @{typography.size-xs}px;
}

QLabel[role="inline-error"] {
    color: @{color.danger};
    font-size: @{typography.size-sm}px;
}

/* 外键搜索的候选列表 */
QListWidget[role="foreign-hits"] {
    background-color: @{color.bg-input};
    border: 1px solid @{color.border-input};
    border-radius: @{radius.sm}px;
    color: @{color.text-primary};
}

QListWidget[role="foreign-hits"]::item:selected {
    background-color: @{color.bg-selected};
    color: @{color.text-strong};
}

/* 立绘上的校验条：**只有一个**，没问题就什么都不画（用户口径） */
QLabel[role="portrait-badge-error"] {
    background-color: @{color.danger};
    border-radius: @{radius.sm}px;
    color: @{color.text-on-danger};
    font-size: @{typography.size-sm}px;
    font-weight: @{typography.weight-bold};
    padding: @{spacing.xs}px @{spacing.sm}px;
}

QLabel[role="portrait-badge-warn"] {
    background-color: @{color.warning};
    border-radius: @{radius.sm}px;
    color: @{color.bg-base};
    font-size: @{typography.size-sm}px;
    font-weight: @{typography.weight-bold};
    padding: @{spacing.xs}px @{spacing.sm}px;
}

/* 专用页四件套的左栏：一行一名士兵 / 一行一个弹种 */
QFrame[role="editor-row"] {
    background-color: @{color.bg-elevated};
    border: 1px solid @{color.border};
    border-radius: @{radius.md}px;
}

QPushButton[role="row-action"] {
    background-color: @{color.danger-subtle};
    border: 1px solid @{color.danger};
    border-radius: @{radius.sm}px;
    color: @{color.danger};
    font-size: @{typography.size-sm}px;
    padding: 1px @{spacing.xs}px;
}

QPushButton[role="row-add"] {
    background-color: @{color.success-subtle};
    border: 1px solid @{color.success};
    border-radius: @{radius.sm}px;
    color: @{color.success};
    font-size: @{typography.size-md}px;
    padding: @{spacing.xs}px @{spacing.md}px;
}

/* ======================================================================== */
/* 界面重做（2026-09-30 定稿）                                            */
/* ======================================================================== */

/* ---- 顶栏：一行 36px：☰ + 页签 + 面包屑 + 保存胶囊 + 打开/重新加载 ------ */

/* 页签字号收一档：一行里要塞下五个页签 + 面包屑 + 胶囊 + 两个按钮。 */
QPushButton[role="nav"] {
    font-size: @{typography.size-md}px;
    padding: 0 @{spacing.md}px;
    min-height: @{size.toolbar-height}px;
}

/* ☰（原「文件 / 编辑 / 视图」三条菜单收进来）。 */
QPushButton[role="menu-button"] {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: @{radius.sm}px;
    color: @{color.text-secondary};
    font-size: @{typography.size-lg}px;
    padding: 0 @{spacing.sm}px;
    min-height: @{size.toolbar-height}px;
}

QPushButton[role="menu-button"]:hover {
    background-color: @{color.bg-hover};
    color: @{color.text-strong};
}

/* 面包屑：「← 军械库 › Stryker RV › 单位」。父级蓝色可点，当前层灰白。 */
QWidget[role="breadcrumb"] {
    background-color: transparent;
}

QPushButton[role="crumb-back"],
QPushButton[role="crumb"] {
    background-color: transparent;
    border: none;
    color: @{color.info};
    font-size: @{typography.size-md}px;
    padding: 0 @{spacing.xs}px;
    min-height: @{size.row-height}px;
}

QPushButton[role="crumb"]:hover,
QPushButton[role="crumb-back"]:hover {
    color: @{color.accent-hover};
    text-decoration: underline;
}

QLabel[role="crumb-current"] {
    color: @{color.text-strong};
    font-size: @{typography.size-md}px;
    font-weight: @{typography.weight-medium};
}

QLabel[role="crumb-sep"] {
    color: @{color.text-disabled};
    font-size: @{typography.size-md}px;
}

/* 当前层前面那个图标位（QLabel，只放一张图）—— 理由同 entries-icon。 */
QLabel[role="crumb-icon"] {
    background-color: transparent;
    border: none;
}

/* 保存状态胶囊：常驻顶栏。三种状态三种颜色 —— 写盘这件事必须看得见。 */
QLabel[role="save-pill"] {
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.pill}px;
    background-color: @{color.bg-elevated};
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
    padding: @{spacing.xxs}px @{spacing.lg}px;
    min-height: @{size.row-height}px;
}

QLabel[role="save-pill"][state="saved"] {
    background-color: @{color.success-subtle};
    border-color: @{color.success};
    color: @{color.success};
}

QLabel[role="save-pill"][state="dirty"] {
    background-color: @{color.warning-subtle};
    border-color: @{color.warning};
    color: @{color.warning};
}

QLabel[role="save-pill"][state="failed"] {
    background-color: @{color.danger-subtle};
    border-color: @{color.danger};
    color: @{color.danger};
    font-weight: @{typography.weight-bold};
}

/* 卡片上「能点进去看的那一行」（装甲 / 机动 / 传感器 / 运输）：一行一个名字。
   名字那一格是**按钮**（点它 -> 右栏换成那一行的全部列），右边跟着 换… / 克隆一份。
   名字贴左、不要按钮底色 —— 它读起来还是一行文字，不是一颗按钮。 */
QLabel[role="detail-link"],
QPushButton[role="detail-link"] {
    background-color: transparent;
    border: none;
    color: @{color.text-primary};
    font-size: @{card.font-body}px;
    padding: @{spacing.xxs}px 0;
    min-height: 0;
    text-align: left;
}

QLabel[role="detail-link"]:hover,
QPushButton[role="detail-link"]:hover {
    color: @{color.info};
    background-color: transparent;
}

QWidget[role="link-row"] {
    background-color: transparent;
}

/* 块 7：块标题那一行（标题 + ＋）与"卡上的一项"（武器格 + ✕ / 能力胶囊 + ✕）的容器。
   它们都是贴在卡片上的**透明容器** —— 不挂这条规则就会被刷成 bg-base 的方块，
   在卡片间隙里看得见（同 detail-body / card-flow 那条的理由）。 */
QWidget[role="block-head"],
QWidget[role="block-item"] {
    background-color: transparent;
}

/* 「换…」/「克隆一份」：小号、克制的描边按钮（不是删除键那种红）。 */
QPushButton[role="row-tool"] {
    background-color: transparent;
    border: 1px solid @{color.border-strong};
    border-radius: @{radius.sm}px;
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
    padding: 0 @{spacing.sm}px;
    min-height: 0;
}

QPushButton[role="row-tool"]:hover {
    background-color: @{color.bg-hover};
    border-color: @{color.accent};
    color: @{color.text-strong};
}

/* 「换…」就地冒出来的候选列表（不弹对话框）。 */
QWidget[role="row-picker"] {
    background-color: @{color.bg-input};
    border: 1px solid @{color.border-input};
    border-radius: @{radius.sm}px;
    padding: @{spacing.xs}px;
}

/* ---- 一行词条：序号 │ 中文名 │ 值 │ 英文列名 ---------------------------- */

QLabel[role="entry-index"] {
    color: @{color.text-disabled};
    font-family: @{typography.data-family};
    font-size: @{typography.size-md}px;
}

QLabel[role="entry-column"] {
    color: @{color.text-muted};
    font-family: @{typography.mono-family};
    font-size: @{typography.size-md}px;
}

QPushButton[role="entry-link"] {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: @{radius.sm}px;
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
    padding: 0;
    min-height: 0;
}

QPushButton[role="entry-link"]:hover {
    background-color: @{color.bg-hover};
    border-color: @{color.border-strong};
    color: @{color.info};
}

/* 空值：「（空）」比真值轻一档，但仍然可点（点了就能填）。 */
QPushButton[role="entry-value"][empty="true"] {
    color: @{color.text-muted};
}

/* 检索命中：那一格标黄（设计块 10）。 */
QPushButton[role="entry-value"][hit="true"] {
    background-color: @{color.warning-subtle};
    border: 1px solid @{color.warning};
    color: @{color.warning};
}

QWidget[role="entry-hit"] {
    background-color: @{color.warning-subtle};
}

/* 改了但还没落盘的那一格（自动保存很快，所以它只是一闪）。 */
/* ❗ 改过的那一格**不再画蓝框**（用户口径 2026-10-01：「能不能别搞红框里
   这种蓝色的东西」）。"有改动没落盘"由顶栏那颗胶囊（「N 处未保存」）说，
   不该让每一格自己顶一个蓝框 —— 新加的成员整行都是"改动过"，看上去像坏了。 */

/* ---- 国家页 / 专精页 / 数据表页 / 检索页 -------------------------------- */

/* 整张表铺开时那一栏的标题。 */
QLabel[role="table-title"] {
    color: @{color.text-strong};
    font-size: @{typography.size-lg}px;
    font-weight: @{typography.weight-bold};
}

/* 两栏 / 三栏页的左栏列表面板。 */
QWidget[role="list-panel"] {
    background-color: @{color.bg-panel};
    border-right: 1px solid @{color.border};
}

QListWidget[role="row-list"],
QListWidget[role="table-list"] {
    background-color: @{color.bg-panel};
    border: none;
    outline: none;
}

QListWidget[role="row-list"]::item,
QListWidget[role="table-list"]::item {
    padding: @{spacing.xs}px @{spacing.md}px;
    min-height: @{size.row-height}px;
    color: @{color.text-primary};
}

QListWidget[role="row-list"]::item:hover,
QListWidget[role="table-list"]::item:hover {
    background-color: @{color.bg-hover};
}

QListWidget[role="row-list"]::item:selected,
QListWidget[role="table-list"]::item:selected {
    background-color: @{color.bg-selected};
    color: @{color.text-strong};
}

/* 检索页里那个「命中」小标签（挂在命中的那一格右边，见 widgets/row_entries.py
   EntryRow.set_hit）。⚠️ 检索**结果列表**里"命中了哪几列"的胶囊不是走这条 ——
   那是 _HitChipDelegate 用 tokens 现画的（格子里的文本仍是 "A · B"）。 */
QLabel[role="hit-chip"] {
    background-color: @{color.warning-subtle};
    border: 1px solid @{color.warning};
    border-radius: @{radius.sm}px;
    color: @{color.warning};
    font-size: @{typography.size-xs}px;
    padding: 0 @{spacing.xs}px;
}

/* 专精页那个 7x2 的小矩阵（兵种 -> 槽位数 / 点数预算）。 */
QLabel[role="matrix-head"] {
    color: @{color.text-secondary};
    font-size: @{typography.size-sm}px;
}

QLabel[role="matrix-value"] {
    color: @{color.text-strong};
    font-family: @{typography.data-family};
    font-size: @{typography.size-md}px;
}

/* ==========================================================================
   [ammo-block] 中栏武器行下面那一块「💣 弹药」
   --------------------------------------------------------------------------
   它和词条行是**两种东西**：词条行 = "这一行的列"（一条一行、值可点）；
   这一块 = "这一行的上下文"（WeaponAmmunitions 里别的行：这把枪用哪些弹、各多少发）。
   值的样式**直接复用** entry-value —— 交互是同一种（点一下就地改），
   长得不一样才是错。
   ========================================================================== */

QFrame[role="ammo-block"] {
    border-top: 1px solid @{color.border};
}

/* 携弹行**与词条行同一个 role**（entry-line）—— 整行高亮那条规则直接复用，
   不再单独写一套背景。弹种名/数量/🔁/🗑 都不许自己画 hover。 */

QPushButton[role="ammo-name"] {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: @{radius.sm}px;
    color: @{color.text-strong};
    font-size: @{typography.size-body}px;
    padding: 1px @{spacing.xs}px;
    text-align: left;
}

/* ❗ 悬停反馈由**整行**给（role=entry-line，与中栏词条行同一套）。
   自己画背景的话，亮出来的只有名字那半个格子 ——
   用户原话："中间弹药栏的高亮还是老一套"。所以这里只换文字色。 */
QPushButton[role="ammo-name"]:hover {
    background-color: transparent;
    color: @{color.accent-hover};
}

/* 数量那一格：沿用 entry-value 的底子，只把字居中（它是个短数字，
   左对齐会和弹种名连成一句）。 */
QFrame[role="ammo-line"] QPushButton[role="entry-value"] {
    text-align: center;
}

/* ==========================================================================
   [mid-panel] 一行词条的悬停：**整行一条高亮，同一时刻只有一行**（task-1）
   --------------------------------------------------------------------------
   用户口径（2026-10-01 实机）：
     "悬浮在修改栏上方的时候，这个会显示多个高亮、且高亮的显示不全。"
   选择：悬停 = 整行一条完整高亮（序号 → 英文列名），且只有一行；
         注释只在悬停「词条名」时出现（那一条挂在 row_entries.py，不在 QSS）。

   为什么高亮画在这里、而不是让值那一格自己 hover：
   Qt 的 :hover 读的是 WA_UnderMouse —— 那个旗标由 enter/leave 事件贴/清，
   **没有任何唯一性保证**：列表一重建（EntryList.clear() 走 detach()）、
   就地编辑一 hide() 值那一格，leave 就可能送不到，旗标留在原地，
   于是好几格同时亮着（用户截图上鼠标只在第 13 行、7 格亮着）。
   现在：唯一的画源是 role=entry-line 上的 hover 属性，而那个属性**只有一个
   写入口**（row_entries.py::EntryList.set_hovered_row）—— 先灭上一行再点新的，
   "只有一行"成了结构保证。行内小控件一律不再自己画 hover（下面两条），
   就算 Qt 的旗标残留了，也画不出第二个高亮。
   ========================================================================== */

QWidget[role="entry-line"][hover="true"] {
    background-color: @{color.bg-hover};
}

/* 行里的那条容器必须透明 —— 全局 QWidget 规则会给它刷 bg-base，
   盖住上面那条整行高亮（实拍过：整行都是 bg-base）。 */
QWidget[role="entry-line-body"] {
    background-color: transparent;
}

/* 值那一格：点它是就地编辑，悬停反馈由整行给 —— 自己不许再画背景/描边。
   下面三条把**别的状态**（检索命中 / 槽位选中 / 刚改过）原样还回去，
   免得被这一条按特异性压掉。 */
QWidget[role="entry-line"] QPushButton[role="entry-value"]:hover {
    background-color: transparent;
    border: 1px solid transparent;
}

QWidget[role="entry-line"] QPushButton[role="entry-value"][hit="true"]:hover {
    background-color: @{color.warning-subtle};
    border: 1px solid @{color.warning};
}

QWidget[role="entry-line"] QPushButton[role="entry-value"][selected="true"]:hover {
    background-color: @{color.bg-active};
    border: 1px solid @{color.border-select};
}

/* 「刚改过」那一格悬停时**不画框** —— 与上面那条 dirty 的说明同一口径
   （改过的格子不顶蓝框；"有几处没落盘"由顶栏胶囊说）。这里显式归零，
   免得悬停又把边框画回来。 */
QWidget[role="entry-line"] QPushButton[role="entry-value"][dirty="true"]:hover {
    border: 1px solid transparent;
}

/* ↗ 是操作口：hover 只换文字色，不在整行高亮里再点一盏灯。 */
QWidget[role="entry-line"] QPushButton[role="entry-link"]:hover {
    background-color: transparent;
    border-color: transparent;
    color: @{color.info};
}

/* ==========================================================================
   [right-card] 单位编辑器右栏那张只读卡：按栏宽自适应 + 「放大 / 缩小」档位
   --------------------------------------------------------------------------
   用户报的："右边的卡片显示不全，不要让我手动向右向左滑，这个观感很不好。"
   两件事分工明确：**折行/缩宽**在控件层（detail_panel.py 的 fit_to_width），
   **字号**在这里 —— 字号是 QSS 给的，控件层改不动（QSS 的 font-size 会盖掉
   setFont），所以档位只能在这里表达。

   档位 = 面板上的动态属性 cardsize（"100"/"90"/"80"/"70"），由
   UnitCardPanel.set_zoom() 写 + unpolish/polish 重算。
   100% **一条规则都不写** —— 那一档就是卡片原来的样子（军械库那张也只有那一档）。

   ❗ 下面这些 px 是 card.font-* / typography.size-* 那几号令牌 × 档位算出来的
      90% -> 14.4/13.5/12.6/11.7/10.8/9.9/9.0/8.1  取整
      80% -> 12.8/12.0/11.2/10.4/ 9.6/8.8/8.0/7.2  取整
      70% -> 11.2/10.5/ 9.8/ 9.1/ 8.4/7.7/7.0/6.3  取整
   下限 8px（detail_panel.ZOOM_FONT_FLOOR）：70% 那两档算出来是 6~7px，
   汉字在那个尺寸上就是一团墨点。
   ⚠️ 改 tokens 里 card.font-* / typography.size-* 之后这几组数要跟着重算 ——
      tests/test_armory_screen.py::test_zoom_fonts_follow_the_tokens 会盯着。
   ========================================================================== */

/* 「卡片大小 100% / 90% / 80% / 70%」那一行。
   容器必须透明（全局 QWidget 规则会给裸 QWidget 刷 bg-base）。
   ❗ 这一行**不跟着档位缩**：它是开关，缩了反而不好点。 */
QWidget[role="card-zoom"] {
    background-color: transparent;
}

QLabel[role="card-zoom"] {
    color: @{color.text-muted};
    font-size: @{typography.size-sm}px;
}

/* ---- 90%：16->14 15->14 14->13 13->12 12->11 11->10 10->9 9->8 ------ */
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="detail-title"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="card-cost"] {
    font-size: 14px;
}

QFrame[role="readonly-card"][cardsize="90"] QLabel[role="stat-number"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="ammo-count"] {
    font-size: 13px;
}

QFrame[role="readonly-card"][cardsize="90"] QLabel[role="armor-kinetic"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="armor-heat"] {
    font-size: 12px;
}

QFrame[role="readonly-card"][cardsize="90"] QLabel[role="detail-section"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="weapon-name"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="ability-chip"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="compact-cell"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="weapon-count"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="stat-line-label"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="stat-line-value"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="option-cost"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="detail-link"] {
    font-size: 11px;
}

QFrame[role="readonly-card"][cardsize="90"] QLabel[role="empty-hint"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="muted"] {
    font-size: 10px;
}

QFrame[role="readonly-card"][cardsize="90"] QLabel[role="stat-name"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="target-chip"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="compact-head"] {
    font-size: 9px;
}

QFrame[role="readonly-card"][cardsize="90"] QLabel[role="armor-zone-name"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="card-note"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="option-selected"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="badge-warn"],
QFrame[role="readonly-card"][cardsize="90"] QLabel[role="badge-error"] {
    font-size: 8px;
}

/* ---- 80%：16->13 15->12 14->11 13->10 12->10 11->9 10->8 9->8(下限) ---- */
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="detail-title"] {
    font-size: 13px;
}

QFrame[role="readonly-card"][cardsize="80"] QLabel[role="card-cost"] {
    font-size: 12px;
}

QFrame[role="readonly-card"][cardsize="80"] QLabel[role="stat-number"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="ammo-count"] {
    font-size: 11px;
}

QFrame[role="readonly-card"][cardsize="80"] QLabel[role="armor-kinetic"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="armor-heat"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="detail-section"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="weapon-name"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="ability-chip"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="compact-cell"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="weapon-count"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="stat-line-label"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="stat-line-value"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="option-cost"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="detail-link"] {
    font-size: 10px;
}

QFrame[role="readonly-card"][cardsize="80"] QLabel[role="empty-hint"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="muted"] {
    font-size: 9px;
}

QFrame[role="readonly-card"][cardsize="80"] QLabel[role="stat-name"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="target-chip"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="compact-head"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="armor-zone-name"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="card-note"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="option-selected"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="badge-warn"],
QFrame[role="readonly-card"][cardsize="80"] QLabel[role="badge-error"] {
    font-size: 8px;
}

/* ---- 70%：16->11 15->11 14->10 13->9 12->8 其余触到 8px 下限 ---------- */
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="detail-title"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="card-cost"] {
    font-size: 11px;
}

QFrame[role="readonly-card"][cardsize="70"] QLabel[role="stat-number"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="ammo-count"] {
    font-size: 10px;
}

QFrame[role="readonly-card"][cardsize="70"] QLabel[role="armor-kinetic"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="armor-heat"] {
    font-size: 9px;
}

QFrame[role="readonly-card"][cardsize="70"] QLabel[role="detail-section"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="weapon-name"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="ability-chip"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="compact-cell"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="weapon-count"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="stat-line-label"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="stat-line-value"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="option-cost"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="detail-link"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="empty-hint"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="muted"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="stat-name"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="target-chip"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="compact-head"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="armor-zone-name"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="card-note"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="option-selected"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="badge-warn"],
QFrame[role="readonly-card"][cardsize="70"] QLabel[role="badge-error"] {
    font-size: 8px;
}

/* ==========================================================================
   [row-tools] 关系树每一行行尾那几颗小按钮的**宿主控件**（＋ / 📄 / 🗑）
   --------------------------------------------------------------------------
   ❗ 它必须透明：全局那条 `QWidget { background-color: bg-base }` 会给它刷一层
   **比树行底色更深**的方块，而宿主比按钮本身高、还溢到下一行 —— 实机看就是
   "下面那一条背景变深，很突兀"（用户 2026-09-29 报的）。
   ========================================================================== */

QWidget[role="row-tools"] {
    background-color: transparent;
}

/* ==========================================================================
   [topbar-buttons] 顶栏里的按钮要比基础规则矮一档
   --------------------------------------------------------------------------
   顶栏是一条 **36px** 的行，而基础 QPushButton 规则是
   min-height 28 + 上下 padding 4×2 + 边框 2 = **38px** —— 比栏还高，
   布局把它居中之后上下各被裁掉 1px。实机看到的就是"打开…按钮下面被遮挡了一点"
   （用户 2026-09-29 报的）。这里只压**顶栏里的 primary 按钮**，
   别的顶栏按钮（☰ / 页签 / 按名查 Id / 重新加载）各有自己的 role 规则，一个都不动。
   ========================================================================== */

QWidget[role="topbar"] QPushButton[variant="primary"] {
    min-height: @{size.topbar-button-height}px;
    padding: 0 @{spacing.lg}px;
}
"""


_PLACEHOLDER = re.compile(r"@\{([a-z][a-z0-9]*(?:\.[a-z0-9-]+)+)\}")


def _substitute(template: str, mapping: dict[str, str]) -> str:
    """把模板里的 `@{group.field}` 换成对应的令牌值。

    为什么不用 `string.Template`：它的 `$` 转义规则会和样式表里合法的 `$` 冲突，
    逼着每写一行 QSS 都得想着转义。自定义的 `@{...}` 语法在本文件里没有歧义。

    异常
    ----
    KeyError
        模板引用了不存在的令牌。属于开发期缺陷，必须显式炸掉，不能静默留空。
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).replace(".", "-")
        try:
            return mapping[key]
        except KeyError:
            raise KeyError(f"样式表引用了不存在的令牌：{match.group(1)}") from None

    return _PLACEHOLDER.sub(replace, template)


def build_stylesheet(tokens: ThemeTokens = TOKENS) -> str:
    """渲染出最终 QSS。

    令牌占位符写作 `@{group.field}`，例：
    `@{color.bg-panel}`、`@{spacing.lg}`、`@{typography.size-sm}`。
    数值的单位（px）需自己写在占位符外面。

    异常
    ----
    KeyError
        模板里引用了不存在的令牌。属于开发期缺陷，不应静默忽略。
    """
    mapping = {key.replace(".", "-"): value for key, value in tokens.as_mapping().items()}
    return _substitute(_QSS_TEMPLATE, mapping)
