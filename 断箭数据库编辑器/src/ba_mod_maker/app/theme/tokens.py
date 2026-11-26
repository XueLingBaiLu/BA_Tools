"""设计令牌——主题的**唯一来源**。

设计取向：「**深色、不刺眼、适合夜间长时段做 mod**」。
具体做法：

- 底色用**蓝灰色调**（而非中性灰），避免「脏灰」观感；
- 不用纯黑（`#000`）与纯白（`#fff`），降低对比刺激；
- 文本不用纯白字，正文用 `#c9d1d9` 这一级，长时间看不酸。

❗ 修改颜色只改本文件；不允许在界面代码里写 `#RRGGBB`。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# --------------------------------------------------------------------------- #
# 色彩
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ColorTokens:
    """全部色值。命名约定：`<surface>_` 表面，`text_` 文本，`accent_` 强调。"""

    # 表面（从最底层到最高层）
    bg_base: str = "#12151b"
    bg_panel: str = "#171b22"
    bg_elevated: str = "#1d222b"
    bg_input: str = "#0f1217"
    bg_hover: str = "#232936"
    bg_active: str = "#2b3242"
    bg_selected: str = "#2d3a54"
    bg_overlay: str = "#0b0e12"

    # 文本（从最亮到最暗）
    text_primary: str = "#c9d1d9"
    text_strong: str = "#e6edf3"
    text_secondary: str = "#8b949e"
    text_muted: str = "#5d6672"
    text_disabled: str = "#454c57"
    text_on_accent: str = "#f2f5fa"
    text_on_danger: str = "#fdf2f2"

    # 边框
    border: str = "#262c37"
    border_strong: str = "#333b48"
    border_input: str = "#2c3341"
    border_focus: str = "#4c6ef5"

    # 强调（品牌/交互）—— 徽章蓝，不刷眼
    accent: str = "#4c6ef5"
    accent_hover: str = "#5c7cfa"
    accent_pressed: str = "#4263eb"
    accent_subtle: str = "#20283b"

    # 选中态（"这一项现在选中的就是它"）。
    # 用户口径 2026-10-01：「能不能别搞红框里这种蓝色的东西」——
    # 从前的选中态是 accent（徽章蓝）底 + 蓝边，现在统一成**中性**：
    # 底色借 bg_active，边用下面这一支中性亮灰。qss.py 里四条
    # [selected="true"] 规则都取这里，别再回到 accent。
    border_select: str = "#7c8899"

    # 语义色（校验结果 / 状态）
    danger: str = "#e5484d"
    warning: str = "#d29922"
    success: str = "#3fb950"
    info: str = "#539bf5"
    # 语义色的暗底（用于行背景 / 徽章）
    danger_subtle: str = "#2d1a1c"
    warning_subtle: str = "#2b2415"
    success_subtle: str = "#14251a"
    info_subtle: str = "#152438"

    # 代码 / JSON 的语法高亮（预留）
    syntax_key: str = "#7ee2a8"
    syntax_string: str = "#d3a06a"
    syntax_number: str = "#8ab4f8"
    syntax_bool: str = "#c792ea"
    syntax_punct: str = "#6b7480"

    # 军械库主屏（ADR 0002）。刻意仍是蓝灰底，只加**一抹暖金**。
    # 暖金只出现在"成本"这一个位置 —— 让它独享，才不会和语义色 warning 抢意思。
    cost_text: str = "#e0b450"
    cost_bg: str = "#2a2419"
    card_bg: str = "#1a1f28"
    card_bg_hover: str = "#222a36"
    card_bg_selected: str = "#26324a"
    card_border: str = "#2a313d"
    card_border_selected: str = "#c9a24d"
    silhouette: str = "#7f8b9c"
    silhouette_selected: str = "#dbe4f0"
    backdrop_top: str = "#161b24"
    backdrop_bottom: str = "#0d1016"
    backdrop_line: str = "#1c222c"

    # 「装甲图」那两个数字（实机右卡立绘四角那 4 个部位）：
    # 白字 = 动能装甲（用现成的 text_strong），橙字 = 破甲（HEAT）装甲 ——
    # 橙**实测自实机截图** docs/实机-04-T-72B.png 的「60 80」那一对：(255, 110, 0)。
    # 描边也是实测的（游戏用 TMP 的黑色 outline）：数字压在立绘照片上，
    # 没有它白字在亮底（天空 / 雪地）上会糊。
    armor_heat: str = "#ff6e00"
    armor_outline: str = "#000000"

    # 滚动条
    scroll_handle: str = "#333b48"
    scroll_handle_hover: str = "#414a59"

    # 阴影（半透明）
    shadow: str = "rgba(0, 0, 0, 0.45)"


# --------------------------------------------------------------------------- #
# 尺寸
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SpacingTokens:
    """间距阶梯（单位 px）。用阶梯而不用随意数字。"""

    xxs: int = 2
    xs: int = 4
    sm: int = 6
    md: int = 8
    lg: int = 12
    xl: int = 16
    xxl: int = 24
    xxxl: int = 32


@dataclass(frozen=True, slots=True)
class RadiusTokens:
    """圆角半径（单位 px）。"""

    sm: int = 3
    md: int = 5
    lg: int = 8
    pill: int = 999


@dataclass(frozen=True, slots=True)
class TypographyTokens:
    """字体。全产品 condensed 取向（见 docs/adr/0002-主屏-军械库视图.md）。

    **下面每一条都是在本机真实平台量过的**（offscreen 平台没有字体，
    量出来的全是占位值，用它得出的结论一律无效）：

    - Qt 在 Windows 上能看到 355 个字体族，Bahnschrift 的 12 个宽度实例全在；
    - Bahnschrift **数字不等宽**：'1'=6.02 / '8'=9.16，**差 52%** → 表格网格不能用；
    - Arial Narrow 数字等宽（全 9.109）且是真 condensed → 表格用它；
    - **中文推进宽度与字体无关**（所有字体「步兵」都是 40.0px @15pt）。
      中文没有 condensed 字面，只能靠 `letter_spacing_percent` 近似收紧。

    因此分三套：

    - `ui_family`：界面文本（标题、按钮、栏目）—— **游戏自己的字体**：
      拉丁 Inter、中文 Noto Serif CJK SC（出处见 core/assets/fonts.py；这两支由
      `theme.fonts.register_game_fonts()` 在启动时从游戏包里注册）；
      没装游戏时往后落到微软雅黑 —— 界面照常能用，只是字不是游戏那套；
    - `data_family`：**表格网格与数值**—— Arial Narrow 打头（等宽 + 窄）。
      这是产品自己的页面（游戏里没有表格），要的是数字对齐，不必跟游戏一致；
    - `mono_family`：日志 / 代码。
    """

    ui_family: str = '"Inter", "Noto Serif CJK SC", "Microsoft YaHei"'
    data_family: str = '"Arial Narrow", "Microsoft YaHei"'
    mono_family: str = '"Consolas", "Cascadia Mono", "NSimSun"'
    #: ❗ 字号也是**逻辑**像素（点是 pt，px = pt × 96/72 × DPI 系数），
    #: 所以和卡片尺寸一样要按 DPI 折算 —— 卡片缩了、字不缩，就成了"字太大"。
    #:
    #: 按实机截图（物理像素）量：卡片名≈13、成本数字≈14、分类标签≈21，
    #: 除以 1.5 得逻辑像素、再 × 0.75 得 pt -> 名字≈9pt / 成本≈10pt / 分类≈12pt。
    #: 于是整体从 12/13/15/18/22 收到 9/11/12/15/18（原来那套在 150% 下比实机大 1/3）。
    size_xs: int = 9
    size_sm: int = 11
    size_md: int = 12
    #: 左边关系树与中间那一栏词条的字号（用户 2026-09-30 实机：这两处看着偏小）。
    #: 比正文大 2pt、又比标题小 1pt —— 一屏 30 行的密度还留得住。
    size_body: int = 14
    size_lg: int = 15
    size_xl: int = 18
    #: 字距百分比，100 = 正常。
    #:
    #: 曾经设成 96 想"近似 condensed"，但那是个错误决定：中文本来就没有 condensed
    #: 字面，硬收只会让汉字**忽宽忽窄、看着不齐**；而拉丁那边 Bahnschrift SemiCondensed
    #: 本身就已经是窄体，根本不需要再收。
    #: 实测（docs/font-compare.png）：100% 明显比 96% 整齐清晰。
    letter_spacing_percent: int = 100
    weight_normal: int = 400
    weight_medium: int = 500
    weight_bold: int = 600


@dataclass(frozen=True, slots=True)
class SizeTokens:
    """控件尺寸（单位 px）。"""

    # ❗ 这些尺寸与字号是**耦合**的：只调字号不调它们，文字会被行高裁掉。
    # test_theme.test_row_height_fits_the_font 会在不匹配时报错。
    #
    # 实踩（换字体时）：QFontMetrics.height() **只反映主字体**。
    # ui_family 报 19.98，但含中文的行经 QTextLayout 实测要 **29.0**——差 9px。
    # 该守卫测试已改为用 QTextLayout 量中英混排，不再读 QFontMetrics。
    # 下面这组是按"实际行高 29"留出余量后的值 —— 那是 size_md=15pt 时的数。
    # 字号收到 12pt 之后实际行高约 23，于是这一组也同比收（守卫测试会验）。
    row_height: int = 26
    header_height: int = 30
    control_height: int = 28
    toolbar_height: int = 34
    #: 共用顶栏的高度（界面重做定稿 块 2：原来的 3 行 90px 压成 1 行 36px）。
    #: 单独一个令牌而不是复用 toolbar_height：那一行要塞下 ☰ + 5 个页签 +
    #: 面包屑 + 保存胶囊 + 两个按钮，34 会正好把字顶到边。
    topbar_height: int = 36
    #: 顶栏**里面**那颗按钮的高度。基础 QPushButton 规则是 min-height 28 +
    #: 上下 padding 4×2 + 边框 2 = 38 —— 比 36 的栏还高，上下各被裁掉 1px
    #: （用户报的"右上角这个打开按钮，下面被遮挡了一点"）。26 + 边框 2 = 28。
    topbar_button_height: int = 26
    statusbar_height: int = 26
    # 容得下最长的表名。
    # 实测方法：在真实控件上比较 QTreeWidget.sizeHintForColumn(0) 与 viewport().width()。
    # 356px 时差 14px，故取 380。❗ 不要用“字符数 × 字宽”去估算——
    # 会漏掉 Qt 给分支指示符留的内边距，我已经在这上面错过一次。
    sidebar_width: int = 380

    # 军械库主屏（ADR 0002）。
    # test_theme.test_armory_card_fits_its_content 会守住"名字不被裁"。
    #
    # ❗ **这里的数都是逻辑像素，而实机截图是物理像素，两者差一个 DPI 系数。**
    # 用户那台是 2560×1440 @150% -> 逻辑 1707×960（本机同配置，离线渲染能 1:1 复现）。
    # 按实机截图（2560 宽）量到：
    #   卡片格 160 × 110 **物理** = 107 × 73 逻辑；相邻卡之间没有间隙，只有选中那张一圈细边；
    #   图标宽约 125 物理（≈0.78 格宽），**浮**在场景上 —— 卡片本身没有底色也没有边框；
    #   名字是格底一行小字，成本是右上角的**金色数字**（不是色块）。
    # 早先按"160 逻辑"画，在 150% 下是 240 物理 —— 比实机大 1.5 倍，这才是"图标太大"的根。
    card_width: int = 107
    card_height: int = 73
    card_gap: int = 6
    #: 图标最多占卡片宽度的这个比例。实机约 0.78。
    card_icon_ratio: float = 0.72
    #: 分类标签那一列的宽（实机约 64 物理 = 43 逻辑）。
    category_label_width: int = 44
    #: 右栏（单位卡片）的宽度。
    #: 实机那张卡是 565 物理 = **377 逻辑**（150% 下），这里取 390：
    #: 内容的实际最小宽是 374（班组成员那两个并排下拉），刚刚够、不再溢出。
    #: 历史：430 初版 -> 内容最小宽 463 溢出 -> 先把下拉改窄（212->120），再定 390。
    detail_panel_width: int = 390
    #: 阵营旗标的绘制尺寸。素材是 272x128（2.125:1）的游戏内国旗贴图，
    #: 34x16 正好是它的 1/8 —— 整数倍缩放不会在半像素上糊边。
    flag_width: int = 34
    flag_height: int = 16
    #: 专精下拉里那个图标的绘制尺寸（Specializations.Icon 是方形贴图）。
    spec_icon: int = 18
    #: **军械库筛选行**里那两颗图标 chip 的绘制尺寸 —— 用户 2026-10-03：
    #: 「**国旗和专精的图标看起来有点小了，大一些**」。
    #: ⛔ 只给军械库这一行用：别处的 flag_width / spec_icon 各有各的口径
    #: （国家页那颗 2 倍大旗、面包屑、词条标题），别顺手一起放大。
    #: 51x24 = 34x16 的 **1.5 倍**，仍是素材 272x128 的整数分之一（整数倍缩放不糊边）。
    chip_flag_width: int = 51
    chip_flag_height: int = 24
    #: 专精图标（方形素材）在军械库那一行的尺寸。
    chip_spec_icon: int = 26
    #: 立绘框高度的**兜底值**（宽度未知时用）。
    #: 正常路径是 SilhouetteView.heightForWidth() 按 PORTRAIT_ASPECT 算——
    #: 框的长宽对所有单位**固定一致**，超出的部分按 cover 直接裁。
    portrait_height: int = 236
    #: 单位编辑器左栏（可写卡）的宽度。比军械库那张卡宽一点：
    #: 编辑器里没有卡片墙，屏幕全给它，而"点开一格"的浮层要贴在卡片里。
    editor_card_width: int = 520
    #: 单位编辑器右栏（全部词条）的最小宽度。
    #: 一行是"中文名 | 值 | 表#Id·原列名"三段，窄了就会把值挤成省略号。
    entries_panel_width: int = 600
    #: 「点开一格」那个就地编辑浮层里，值输入框的宽度。
    inline_input_width: int = 240


@dataclass(frozen=True, slots=True)
class CardTokens:
    """单位卡片（军械库右栏）专用的字号与图标高度。

    ❗ 为什么不直接用 `typography.size_*`：那一套是照着"实机卡片墙"逐格量过、
    **全应用一起缩**过的（见 docs/交接-单位卡片GUI.md §9.1b）。而右栏这张卡是
    **复刻实机**的 —— 实机卡上的字与图标比缩完的全局值大一档，用户口径
    「学游戏 GUI，让它看起来大且清晰」。所以卡片这套单独给。

    单位：字号是 QSS 的 px（= 逻辑像素），图标高度也是逻辑像素。
    """

    font_title: int = 16  # 单位名
    font_section: int = 12  # 栏目名（属性 / 能力 / 武器 / 自定义选项）
    font_body: int = 12  # 正文：武器名、详情行的标签与值
    font_number: int = 14  # 数值行的数字（实机那一排数字最大）
    font_note: int = 10  # 小字注释
    font_column: int = 10  # 简略模式那排列标题
    # 「装甲图」那 4 个部位（见 viewmodel.unit_card.armor_zones）。
    # 实机量到：数字 ≈18 物理 / 1.5 ≈ 13 逻辑，部位名 ≈11 物理 / 1.5 ≈ 9 逻辑。
    font_armor: int = 13  # 动能 / 破甲那两个数字
    font_armor_name: int = 9  # 部位名（顶部 / 背面 / 正面 / 侧面）
    icon_stat: int = 22  # 数值图标（实机 33 物理）
    icon_weapon: int = 33  # 武器剪影（实机 145×50 物理 -> 97×33 逻辑）
    icon_ammo: int = 14  # 弹药图标（原图 174×36）
    icon_target: int = 20  # 「能打什么」那排
    icon_option: int = 24  # 自定义选项那一排


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """一套完整主题。将来做亮色主题时，只需再定义一个实例。"""

    name: str
    color: ColorTokens
    spacing: SpacingTokens
    radius: RadiusTokens
    typography: TypographyTokens
    size: SizeTokens
    #: 单位卡片专用（右栏那张卡）—— 与全局那套分开，见 CardTokens 的注释。
    card: CardTokens

    def as_mapping(self) -> dict[str, str]:
        """展平成 `key -> str`，供 QSS 模板使用。

        键名规则：组名与字段名均转为连字符，例：
        `color.bg-panel`、`spacing.lg`、`radius.md`、`typography.size-sm`。
        """
        flat: dict[str, str] = {}
        for group in fields(self):
            value: Any = getattr(self, group.name)
            if group.name == "name":
                flat["theme.name"] = str(value)
                continue
            for field_info in fields(value):
                key = f"{group.name}.{field_info.name.replace('_', '-')}"
                flat[key] = str(getattr(value, field_info.name))
        return flat


TOKENS = ThemeTokens(
    name="dark",
    color=ColorTokens(),
    spacing=SpacingTokens(),
    radius=RadiusTokens(),
    typography=TypographyTokens(),
    size=SizeTokens(),
    card=CardTokens(),
)


# --------------------------------------------------------------------------- #
# 应用到 Qt
# --------------------------------------------------------------------------- #


def apply_palette(app: QApplication, tokens: ThemeTokens = TOKENS) -> None:
    """把令牌写成 QPalette，并把系统色制设为深色。

    为什么还需要 QPalette：QSS 只能管到 Qt 自己画的控件。
    原生对话框、菜单阴影、部分控件内部绘制（如 QTableView 的选区）仍读调色板。

    同时调用 `setColorScheme(Dark)`：在 Windows 11 上会把**窗口标题栏**一并压成深色，
    否则会在深色界面上残留一条刺眼的白标题栏。
    """
    colors = tokens.color
    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, QColor(colors.bg_base))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors.bg_input))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.bg_panel))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors.text_muted))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors.bg_elevated))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(colors.text_strong))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors.bg_selected))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors.text_strong))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.bg_elevated))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.Link, QColor(colors.info))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(colors.accent))

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.text_disabled))

    app.setPalette(palette)
    # 字体：**先把游戏自带的字体注册进 Qt**（没装游戏就跳过），再设应用字体。
    # 惰性 import 是为了避开环：theme.fonts 要读 TOKENS，而本模块是它的上游。
    from ba_mod_maker.app.theme.fonts import register_game_fonts  # noqa: PLC0415

    register_game_fonts()
    # 用 QFont 传族名列表（逗号分隔）交给 Qt 逐字回退：
    # 拉丁走 Inter，中文落到 Noto Serif CJK SC。
    font = QFont(tokens.typography.ui_family.replace(chr(34), ""), tokens.typography.size_md)
    # QSS **不支持 letter-spacing**（Qt 样式表子集里没有这个属性），所以字距只能走 QFont。
    # QSS 只设 font-family/font-size 时 Qt 用 resolve 合并，不会把这里的字距抹掉。
    #
    # 提示方式：小字号汉字在 Windows 上要**全提示**才笔画清晰、不糊。
    # 这条只在真实平台有意义（offscreen 无字体），所以放在这里而不是 fonts.py。
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setLetterSpacing(
        QFont.SpacingType.PercentageSpacing, tokens.typography.letter_spacing_percent
    )
    app.setFont(font)

    # Qt 6.5+ 可用；旧版本上静默跳过。
    style_hints = app.styleHints()
    if hasattr(style_hints, "setColorScheme"):
        style_hints.setColorScheme(Qt.ColorScheme.Dark)
    app.setStyle("Fusion")
