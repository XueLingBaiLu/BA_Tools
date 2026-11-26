"""主题层的纯函数测试（无需 GUI 窗口）。

这里验证三件事：

1. "令牌 → QSS"这一步的正确性；
2. "颜色只能来自令牌"这条硬纪律；
3. **字号与行高的耦合**——这条在换字体时是最容易静默出错的地方。

注意：本文件会 import Qt 字体类，但**不创建可见窗口**，
所以可在无显示环境下跑（无字体的平台会 skip 而不是给假结论）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ba_mod_maker.app.theme.qss import _QSS_TEMPLATE, _substitute, build_stylesheet
from ba_mod_maker.app.theme.tokens import TOKENS

# QSS 里允许出现的非令牌颜色：只有半透明黑影。
_ALLOWED_LITERAL_COLORS = {"rgba(0, 0, 0, 0.45)"}

#: 界面代码里**不许**出现的字面色值（tokens.py 除外）。
#: 字面色值的形态：十六进制，或 rgb()/rgba() 字面量。
#: ❗ 前面加 (?<!\.) 是**必须的**：Qt 里 QColor.rgb() / .rgba() 是再常见不过的
#: 合法调用（缓存的 key 就要用它），不加会把它们全判成违规（实测误报过一次）。
_COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{6}\b|(?<!\.)rgba?\(")

_APP_DIR = Path(__file__).resolve().parents[1] / "src" / "ba_mod_maker" / "app"
_TOKENS_FILE = _APP_DIR / "theme" / "tokens.py"

#: 行高探针：**必须同时含中文、拉丁、数字、符号**。
#: 只测拉丁会完全测不到回退字体的行高（那是这条守卫存在的唯一理由）。
_MIXED_PROBE = "侦察 BTR-82 ×5"


# --------------------------------------------------------------------------- #
# 令牌 -> QSS
# --------------------------------------------------------------------------- #


def test_no_unreplaced_placeholders() -> None:
    """渲染结果里不得残留任何 @{...} 占位符。"""
    qss = build_stylesheet()
    assert "@{" not in qss, "还有未替换的占位符"


def test_unknown_token_raises() -> None:
    """引用不存在的令牌必须直接报错，而不是静默留空。"""
    with pytest.raises(KeyError):
        _substitute("color: @{color.does-not-exist};", {"color-bg-base": "#000000"})


def test_template_has_no_hardcoded_colors() -> None:
    """扫**模板**，确认所有颜色都来自令牌。

    注意扫的是模板而不是 build_stylesheet() 的结果：
    渲染结果里本来就应当有色值，那是令牌的值；只有模板里出现字面色值才是违规。
    """
    found = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", _QSS_TEMPLATE))
    found |= set(re.findall(r"rgba?([^)]*)", _QSS_TEMPLATE))
    assert found <= _ALLOWED_LITERAL_COLORS, f"模板里发现硬编码颜色：{sorted(found)}"


def test_no_hardcoded_colors_outside_tokens() -> None:
    """除 tokens.py 外，界面代码里不得出现字面色值。

    为什么需要这条：QSS 模板已经被上一条守住了，但**自绘控件**（QPainter）
    绕开了 QSS，色值可以直接写进 paintEvent。军械库主屏的卡片就是自绘的，
    所以在这里把口子一起堵上。
    """
    violations: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        if path == _TOKENS_FILE:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = _COLOR_LITERAL.search(line)
            if match and match.group(0) not in _ALLOWED_LITERAL_COLORS:
                violations.append(f"{path.relative_to(_APP_DIR)}:{number}: {line.strip()}")
    assert not violations, "界面代码里出现了硬编码色值（应当写进 tokens.py）：\n" + "\n".join(
        violations
    )


def test_semantic_colors_are_distinct() -> None:
    """error / warning / info / ok 四色必须两两不同，否则用户分不出严重度。"""
    colors = TOKENS.color
    semantic = {colors.danger, colors.warning, colors.success, colors.info}
    assert len(semantic) == 4


def test_dark_theme_contrast_is_sane() -> None:
    """正文与底色的对比度要高，但不得是纯白对纯黑。"""
    colors = TOKENS.color
    assert _contrast_ratio(colors.text_primary, colors.bg_base) >= 7.0
    assert _contrast_ratio(colors.text_secondary, colors.bg_base) >= 3.5
    assert colors.text_primary.lower() != "#ffffff"
    assert colors.bg_base.lower() != "#000000"


def _relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    light, dark = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


# --------------------------------------------------------------------------- #
# 字号与行高的耦合
# --------------------------------------------------------------------------- #


def _require_fonts():
    """无字体的平台（如 offscreen）度量不可信，**跳过而不给假结论**。"""
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    try:
        QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    if not QFontDatabase.families():
        pytest.skip("该平台没有可用字体，字体度量不可信")


def _layout_height(family: str, size: int, text: str) -> float:
    """一段文本**真实渲染**后的行高。

    为什么不用 QFontMetrics：它只反映**主字体**。
    ui_family 的 QFontMetrics.height() 报 19.98，而含中文的行实际要 29.0
    ——因为中文落到回退字体 Noto Sans SC，QFontMetrics 看不见它。
    """
    from PySide6.QtGui import QFont, QTextLayout

    layout = QTextLayout(text, QFont(family.replace('"', ""), size))
    layout.beginLayout()
    line = layout.createLine()
    if line.isValid():
        line.setLineWidth(10_000)
    layout.endLayout()
    return layout.boundingRect().height()


def test_metrics_would_miss_the_fallback() -> None:
    """把这条坑钉死：QFontMetrics 测不到回退字体的行高。

    这个测试不是在测产品代码，而是在**证明下面那条守卫必须用 QTextLayout**。
    哪天两者相等了，说明探针失效（回退没发生），该换探针而不是删守卫。
    """
    _require_fonts()
    from PySide6.QtGui import QFont, QFontMetricsF

    family = TOKENS.typography.ui_family
    metrics = QFontMetricsF(QFont(family.replace('"', ""), TOKENS.typography.size_md)).height()
    laid_out = _layout_height(family, TOKENS.typography.size_md, _MIXED_PROBE)
    assert laid_out > metrics, (
        f"混排行高 {laid_out} 不大于 QFontMetrics 的 {metrics}——"
        "说明中英混排没有触发回退，本探针已失效"
    )


def test_row_height_fits_the_font() -> None:
    """行高、表头高、控件高必须容得下当前字号。

    这是**字号与布局的耦合守卫**，也是换字体时唯一能拦住"静默裁字"的东西。

    实踩两次：

    1. 只把 tokens.typography.size_md 调大、不动 tokens.size.row_height，
       文字会被行高裁掉；且在 125% / 150% 缩放下裁得不一样，在自己机器上看不出来。
    2. 换字体时，QFontMetrics 报的行高比真实渲染**低 9px**（见上一条测试）。
       若在读 QFontMetrics，这个守卫会判"够了"、测试变绿而界面裁字。
    """
    _require_fonts()

    worst = max(
        _layout_height(TOKENS.typography.ui_family, TOKENS.typography.size_md, _MIXED_PROBE),
        _layout_height(TOKENS.typography.data_family, TOKENS.typography.size_md, _MIXED_PROBE),
    )

    assert TOKENS.size.row_height >= worst + 2, (
        f"行高 {TOKENS.size.row_height}px 装不下字号 {TOKENS.typography.size_md}pt "
        f"的实际行高 {worst}px"
    )
    assert TOKENS.size.header_height >= worst + 4, "表头高不够"
    assert TOKENS.size.control_height >= worst + 4, "控件高不够（输入框会裁字）"
    assert TOKENS.size.statusbar_height >= worst + 2, "状态栏高不够"
    assert TOKENS.size.toolbar_height >= worst + 4, "工具栏高不够"


def test_entry_row_fits_its_inline_editor() -> None:
    """一行词条的高度必须装得下它自己的**就地编辑浮层**。

    为什么单独立一条：上面那条守卫只看 token，而"一行词条"的高度一度是
    row_entries.py 里**另一个硬编码的 24**（不受任何守卫保护）——
    于是 token 说行高 26 够用，实机上那一行的浮层只有 22，
    而中英混排实测行高正好 22.0，再叠上 1px 上下边框就被裁了。
    用户报过两次：「这个编辑栏显示有问题」「还是有遮挡 / 像两个叠在一起」。

    判据：浮层减去它自己的上下边框，还要装得下行高 + 2px 余量。
    """
    _require_fonts()

    from ba_mod_maker.app.widgets.row_entries import (
        _VALUE_MIN_HEIGHT,
        EDITOR_HEIGHT,
        ROW_HEIGHT,
    )

    worst = max(
        _layout_height(TOKENS.typography.ui_family, TOKENS.typography.size_md, _MIXED_PROBE),
        _layout_height(TOKENS.typography.data_family, TOKENS.typography.size_md, _MIXED_PROBE),
    )
    assert worst + 2 <= EDITOR_HEIGHT - 2, (
        f"就地编辑浮层 {EDITOR_HEIGHT}px 去掉 2px 边框只剩 {EDITOR_HEIGHT - 2}px，"
        f"装不下实测行高 {worst}px + 2px 余量 —— 实机上框的上下边会被裁掉"
    )
    assert ROW_HEIGHT > EDITOR_HEIGHT, "行高必须比浮层高（浮层要有落脚的地方）"
    assert TOKENS.size.row_height <= ROW_HEIGHT, (
        f"一行词条 {ROW_HEIGHT}px 比 token 里的标准行高 {TOKENS.size.row_height}px 还矮"
    )
    # ❗ 2026-10-02：**要装下的不只是浮层，还有"值那一格"自己** ——
    #    它是个 QPushButton，原生 style 的 minimumSizeHint 实测 32（见 row_entries.py
    #    的 `_VALUE_MIN_HEIGHT`），而 stylesheet 在 polish 时把那个最小值**写进控件**、
    #    压过 setFixedHeight。行高比它矮时多出来的几 px 会探到下一行底下**被盖住** ——
    #    用户原话："它下面又被遮挡了一部分"（同族症状第三次；前两次见上面那条的说明）。
    assert ROW_HEIGHT > _VALUE_MIN_HEIGHT, (
        f"行高 {ROW_HEIGHT}px 装不下值那一格（原生最小高度实测 {_VALUE_MIN_HEIGHT}px）——"
        ' 多出来的部分会探到下一行底下被盖住，看着像"框的下边被切掉"'
    )


def test_armory_card_fits_its_content() -> None:
    """军械库卡片必须装得下：图标 + 成本 + 单位名。

    ⚠️ 名字是**省略号截断**（`ElideRight`），不是折行 —— 所以"多窄算太窄"的判据是
    "图标还画得下"，不是"名字折得开"（早先那条 120px 的判据是按折行写的，已作废）。
    尺寸本身照实机：160×110 **物理** @150% = 107×73 逻辑。
    """
    _require_fonts()
    line = _layout_height(TOKENS.typography.ui_family, TOKENS.typography.size_sm, _MIXED_PROBE)
    assert TOKENS.size.card_height >= line * 2, "卡片太矮，名字会被裁"
    assert TOKENS.size.card_width >= 90, "卡片太窄，名字只剩一两个字"
    icon_width = TOKENS.size.card_width * TOKENS.size.card_icon_ratio
    assert icon_width >= 60, f"图标位只剩 {icon_width:.0f}px，看不清是什么单位"


# --------------------------------------------------------------------------- #
# 字体：必须是**游戏自己的字体**（不是"看着差不多"的系统字体）
# --------------------------------------------------------------------------- #


def test_ui_font_names_the_game_fonts_first() -> None:
    """界面字体链 = 游戏那两支，后面才是系统兜底。

    出处 core/assets/fonts.py：读 TMP 的 m_SourceFontFile / m_FallbackFontAssetTable 实测——
    拉丁每个 Inter-* SDF 的来源都是 Inter-<字重>.ttf，中文落到 NotoSerifCJK(-sc)。
    字体文件从游戏包里**现取**（50 MB，不进仓库），没装游戏时后面的族就是兜底。
    """
    family = TOKENS.typography.ui_family
    assert family.startswith('"Inter"'), family
    assert "Noto Serif CJK SC" in family, family
    assert "Microsoft YaHei" in family, "没有游戏时要有系统兜底"


def test_game_fonts_degrade_without_a_game(tmp_path) -> None:
    """没装游戏 -> 一个字体都取不到，**不抛异常**（界面回落系统字体）。"""
    from ba_mod_maker.core.assets.fonts import ensure_fonts, find_fonts_bundle

    assert ensure_fonts(game_root=tmp_path / "no-game", cache_dir=tmp_path / "cache") == ()
    assert find_fonts_bundle(tmp_path / "no-game") is None


@pytest.mark.slow
def test_game_fonts_come_out_of_the_bundle(tmp_path) -> None:
    """真读一次游戏包：要的 6 支字体都得抽出来，并且**第二次走缓存**。

    实测：冷 2.7s / 50.4 MB，热 1ms。
    """
    from ba_mod_maker.core.assets.fonts import WANTED, ensure_fonts, find_fonts_bundle

    if find_fonts_bundle() is None:
        pytest.skip("本机没装游戏")
    paths = ensure_fonts(cache_dir=tmp_path / "fonts")
    names = {path.name for path in paths}
    assert names == {f"{name}.ttf" for name in WANTED}, names
    assert all(path.stat().st_size > 100_000 for path in paths), "抽出来的不该是空壳"
    assert (tmp_path / "fonts" / "_manifest.json").is_file(), "manifest 是缓存完整的标记"
    again = ensure_fonts(cache_dir=tmp_path / "fonts")
    assert [p.name for p in again] == [p.name for p in paths]


def test_letter_spacing_is_within_readable_range() -> None:
    """中文字距收紧是**近似** condensed 的手段，收紧过头会粘连。

    实测：低于 90 汉字开始粘连。上限 100 表示不收紧。
    """
    percent = TOKENS.typography.letter_spacing_percent
    assert 90 <= percent <= 100, f"字距 {percent}% 超出可读范围"


# --------------------------------------------------------------------------- #
# 选中态：不许再用 accent 蓝（用户口径 2026-10-01）
# --------------------------------------------------------------------------- #


def _qss_rule_blocks(template: str) -> list[str]:
    """把模板切成一块块「选择器 { 声明 }」。

    ❗ 不能简单 split("}")：令牌占位符本身长这样 —— `@{color.bg-active}` ——
       里面的 `}` 会把一块规则劈成两半（本用例第一版就栽在这上面）。
       所以按花括号配对走，遇到 `@{` 先整个跳过。
    """
    blocks: list[str] = []
    depth = 0
    block_start = 0
    index = 0
    while index < len(template):
        if template.startswith("@{", index):
            end = template.find("}", index)
            index = len(template) if end < 0 else end + 1
            continue
        char = template[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append(template[block_start : index + 1])
                block_start = index + 1
        index += 1
    return blocks


def test_selected_state_is_not_accent_blue() -> None:
    """选中态**不许**是蓝底蓝边。

    用户口径（2026-10-01）：「能不能别搞红框里这种蓝色的东西」。
    四条 `[selected="true"]` 规则 + 「改过那一格」的悬停边框全部换成中性令牌
    （底色 bg-active、边 border-select）。判据直接读 **模板**：那几块里
    accent / bg-selected 一个都不许出现。
    """
    blocks = [block for block in _qss_rule_blocks(_QSS_TEMPLATE) if '[selected="true"]' in block]
    assert len(blocks) >= 4, f"少了选中态规则？只找到 {len(blocks)} 块"
    for block in blocks:
        assert "@{color.accent}" not in block, f"选中态还在画 accent 蓝：\n{block}"
        assert "@{color.bg-selected}" not in block, f"选中态还在用蓝底：\n{block}"
        assert "@{color.border-select}" in block, f"选中态没有中性边：\n{block}"

    dirty = [block for block in _qss_rule_blocks(_QSS_TEMPLATE) if '[dirty="true"]:hover' in block]
    assert dirty, "改过那一格的悬停规则不见了"
    for block in dirty:
        assert "@{color.accent}" not in block, f"「刚改过」那一格悬停又画蓝框了：\n{block}"


def test_the_select_border_token_reaches_the_stylesheet() -> None:
    """中性选中边只有一份出处（tokens.py），且真的进了渲染出来的 QSS。"""
    value = TOKENS.color.border_select
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), value
    assert value in build_stylesheet(), "令牌没被 QSS 用上（加了等于没加）"


# --------------------------------------------------------------------------- #
# role 交叉扫描：代码里 setProperty 的 role 必须在 qss.py 里有选择器
# --------------------------------------------------------------------------- #

_FORWARD_SCAN_DEPTH = 6


def _role_values(node, scope, depth: int = 0) -> list[str]:
    """把一个表达式**证明**成有限几个字符串；证不出来返回 []（跳过，不做有罪推定）。

    支持：字面量 / 模块级或函数内常量（含 `a if c else b` 两个分支）/ f-string。
    """
    import ast

    if depth > _FORWARD_SCAN_DEPTH:
        return []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return _role_values(scope[node.id], scope, depth + 1) if node.id in scope else []
    if isinstance(node, ast.IfExp):
        return _role_values(node.body, scope, depth + 1) + _role_values(
            node.orelse, scope, depth + 1
        )
    if isinstance(node, ast.JoinedStr):
        parts: list[list[str]] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant):
                parts.append([str(piece.value)])
            elif isinstance(piece, ast.FormattedValue):
                got = _role_values(piece.value, scope, depth + 1)
                if not got:
                    return []
                parts.append(got)
            else:
                return []
        out = [""]
        for choices in parts:
            out = [prefix + choice for prefix in out for choice in choices]
        return out
    return []


def _roles_in_source(source: str, filename: str = "<scan>") -> tuple[set[str], int]:
    """扫一段源码，返回（能证明的 role 值集合，证不出来的处数）。

    "转发"也算：`_label(text, role)` 里 setProperty 用的是参数时，把**同一文件里**
    调用点那个位置的字符串实参一起收进来 —— 否则 `_label(x, "option-selected")`
    这类写法全漏（实测漏 20 个）。
    """
    import ast

    tree = ast.parse(source, filename=filename)
    consts: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value

    functions = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    # ① 哪些函数的**参数**被直接当 role 用（名字 -> 第几个位置参数）
    forwarders: dict[str, set[int]] = {}
    for function in functions:
        params = [arg.arg for arg in function.args.args]
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setProperty"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "role"
                and isinstance(node.args[1], ast.Name)
                and node.args[1].id in params
            ):
                forwarders.setdefault(function.name, set()).add(params.index(node.args[1].id))

    found: set[str] = set()
    unresolved = 0
    for function in functions:
        scope = dict(consts)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        scope[target.id] = node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                scope[node.target.id] = node.value
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Attribute) and node.func.attr == "setProperty":
                if (
                    len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "role"
                ):
                    values = _role_values(node.args[1], scope)
                    unresolved += 0 if values else 1
                    found.update(values)
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in forwarders or any(isinstance(a, ast.Starred) for a in node.args):
                continue
            for index in forwarders[name]:
                if index >= len(node.args):
                    continue
                values = _role_values(node.args[index], scope)
                unresolved += 0 if values else 1
                found.update(values)
    return found, unresolved


#: 故意写成**修之前**那个样子：worst 只可能是 error/warning，而 QSS 里叫 badge-warn。
_BADGE_TYPO_SAMPLE = """
def _refresh_badge(self, findings):
    worst = "error" if any(f.severity == "error" for f in findings) else "warning"
    self._badge.setProperty("role", f"badge-{worst}")
"""


def test_the_role_scan_catches_the_badge_typo() -> None:
    """先证明这条守卫抓得住它要抓的东西 —— 否则它只是一条常绿的摆设。

    这一段的形状就是本轮修掉的那个 bug（detail_panel 上过线）：角标掉样式。
    """
    found, _ = _roles_in_source(_BADGE_TYPO_SAMPLE, "sample.py")
    assert "badge-error" in found, f"扫描器没解出 f-string 拼出来的 role：{sorted(found)}"
    assert "badge-warning" in found, "扫描器没解出那个不存在的 role（守卫会漏掉这个 bug）"
    known = set(re.findall(r'role="([A-Za-z0-9_-]+)"', _QSS_TEMPLATE))
    assert "badge-warning" not in known, "QSS 里本来就没有 badge-warning —— 这正是那条 bug"


def test_every_role_set_in_code_has_a_selector() -> None:
    """代码里 `setProperty("role", ...)` 的每一个 role，QSS 里都要有选择器。

    为什么需要：本轮的真实 bug 是 `f"badge-{worst}"` 拼出 `badge-warning`，
    而 qss.py 里只有 `badge-warn` —— 角标掉样式，肉眼只看到"一个秃叹号"。
    这条把"设了 role 却没有选择器"变成一条用例（反方向不管：QSS 里备着没用的
    选择器是无害的）。

    ❗ 只判**能证明**的写法（见 _roles_in_source）；运行时才算出来的 role 跳过。
    """
    known = set(re.findall(r'role="([A-Za-z0-9_-]+)"', _QSS_TEMPLATE))
    resolved: dict[str, list[str]] = {}
    for path in sorted(_APP_DIR.rglob("*.py")):
        got, _ = _roles_in_source(path.read_text(encoding="utf-8"), str(path))
        for value in got:
            resolved.setdefault(value, []).append(str(path.relative_to(_APP_DIR)))

    missing = sorted(value for value in resolved if value not in known)
    detail = "\n".join(
        f"  {value}  <- {', '.join(sorted(set(resolved[value])))}" for value in missing
    )
    assert not missing, (
        "这些 role 在代码里设了、qss.py 里却没有选择器"
        "（要么补选择器，要么删掉那个 setProperty）：\n" + detail
    )
    for must in ("entry-value", "badge-warn", "badge-error", "option-row"):
        assert must in resolved, f"扫描器没扫到 {must} —— 守卫可能是条摆设"
    assert len(resolved) >= 60, f"只扫到 {len(resolved)} 个 role，守卫成了摆设"
