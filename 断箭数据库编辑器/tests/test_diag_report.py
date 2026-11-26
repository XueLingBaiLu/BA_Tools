"""Debug 模式那一套：`app/diag/report.py` ＋ `cli/diag.py` ＋ 「🐞 调试…」入口。

这一套的产物是**用户粘给我们的一段文字**，所以钉的不是"函数返回了什么"，
而是三件在真机上会咬人的事：

1. **一行体检真的是"一行"** —— 长了就折行，粘进聊天窗口就散架；
2. **失败不许抛** —— 这是用户出问题时的最后一根稻草，它自己再炸就什么都拿不到了；
3. **`--help` 与 `import report` 都不许把 Qt 拉起来** —— 判据是 `'PySide6' in sys.modules`
   必须 False（缺 Qt 的那台机器上，诊断是唯一还能用的东西）。

每条断言都配了**能红的正控**（§0.9 动作 2：尺子先喂一个已知违规的点，能红才算尺子）：
一行体检那条把版本号塞成 400 个字符、保存那条把目标指到一个**文件**下面、
环境块那条把 `env_lines()` 整段抽空、Qt 那条拿"该拉 Qt"的那条路验尺子本身。
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from ba_mod_maker.app.diag import report

#: 仓根与 src（子进程那条用例要能 `import ba_mod_maker`，不靠"当前目录恰好对"）。
_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"


def _run_python(code: str) -> str:
    """在一个**全新进程**里跑一段代码，回来它的 stdout（用来量 import 副作用）。

    为什么不 in-process 量：`sys.modules` 只增不减，同一个进程里跑两次根本分不出
"这次 import 带进来的"还是"上一条用例留下的"。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(_SRC), env.get("PYTHONPATH", "")]).strip(
        os.pathsep
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _cli(argv: list[str]) -> tuple[int, str, str]:
    """在进程内跑一次 CLI，回来 (退出码, stdout, stderr)。"""
    from ba_mod_maker.cli.__main__ import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# 一行体检：必须真的是"一行"
# --------------------------------------------------------------------------- #


def test_health_line_is_exactly_one_short_line() -> None:
    """一行体检是给人**粘贴**的：不许换行，长度受显示列上限约束。"""
    line = report.health_line()

    assert "\n" not in line and "\r" not in line, f"一行体检里混进了换行：{line!r}"
    assert report.display_width(line) <= report.HEALTH_LIMIT, (
        f"{report.display_width(line)} 列 > 上限 {report.HEALTH_LIMIT}：{line}"
    )
    # 四样都得在：版本 / Python / 当天日志 / 覆盖变量设没设
    assert "ba-mod-maker " in line
    assert "Python " in line
    assert "日志 " in line
    assert "覆盖 " in line


def test_a_long_version_is_clipped_never_passed_through(monkeypatch) -> None:
    """**正控（判据 a）**：版本号塞成 400 个字符，一行体检必须截断。

    这条尺子能红的证据在最后两行：那个"没截断会长成什么样"的值**确实**超了上限 ——
    所以上面那条 100 列的断言不是恒真的。
    """
    monkeypatch.setattr(report, "version", lambda: "9" * 400)

    line = report.health_line()

    assert report.display_width(line) <= report.HEALTH_LIMIT
    assert line.endswith("…"), "超长了却没截断（末尾该有 …）"
    # ← 正控：把截断拿掉会长成这样，它**确实**越过上限（所以尺子能红）
    raw = "ba-mod-maker " + "9" * 400
    assert report.display_width(raw) > report.HEALTH_LIMIT


def test_clip_counts_display_columns_not_characters() -> None:
    """中文一个字占 2 列：按字符数截出来的"100 字"在终端里宽一倍。"""
    text = "测" * 50  # 50 个汉字 = 100 列

    assert report.display_width(text) == 100
    assert report.clip(text, 100) == text, "正好压线的不许动（多截一个字也是错）"

    clipped = report.clip(text, 40)
    assert report.display_width(clipped) <= 40
    assert clipped.endswith("…")
    assert len(clipped) < len(text)


# --------------------------------------------------------------------------- #
# 环境块 / 整段诊断
# --------------------------------------------------------------------------- #


def test_env_lines_list_every_variable_plus_the_interpreter() -> None:
    """四个环境变量一个不许漏（没设也要列出来，写「未设」）。"""
    text = "\n".join(report.env_lines())

    for key in report.ENV_KEYS:
        assert key in text, f"环境块里没有 {key}：{text}"
    assert "sys.executable = " in text
    assert "__file__ = " in text
    assert report.NOT_SET in text  # 本机这几项本来就没设


def test_env_lines_show_the_real_value_when_it_is_set(monkeypatch) -> None:
    """设了的变量要报**真值**，不能再写「未设」（否则环境块等于没写）。"""
    monkeypatch.setenv("BA_MOD_MAKER_GAME", r"D:\假游戏根")

    line = next(item for item in report.env_lines() if item.startswith("BA_MOD_MAKER_GAME"))

    assert r"D:\假游戏根" in line
    assert report.NOT_SET not in line


def test_diagnostics_text_carries_the_three_sections() -> None:
    """『📋 复制诊断』要的三段：一行体检（最前）＋ 环境 ＋ 最近事件。"""
    text = report.diagnostics_text(count=0)
    lines = text.splitlines()

    assert lines[0] == f"## {report.HEALTH_HEADING}"
    assert lines[1] == report.health_line(), "标题下面必须紧接着那一行体检"
    assert f"## {report.ENV_HEADING}" in text
    assert f"## {report.TAIL_HEADING}" in text
    assert "BA_MOD_MAKER_LOG_DIR" in text


def test_the_env_section_is_really_required(monkeypatch) -> None:
    """**正控（判据 c）**：把 `env_lines()` 整段抽空，上面的断言必须变红。

    抽空之后"环境变量那一行"消失、而"环境"这个**标题还在** —— 这正好说明断言
    必须落在**内容**上：只 grep 标题的话，段被抽空了用例照样绿（假绿）。
    """
    monkeypatch.setattr(report, "env_lines", list)

    broken = report.diagnostics_text(count=0)

    assert "BA_MOD_MAKER_LOG_DIR" not in broken, "env 段没了，环境变量却还在？"
    assert "sys.executable = " not in broken
    assert f"## {report.ENV_HEADING}" in broken, "标题还在（所以断言不能只看标题）"


# --------------------------------------------------------------------------- #
# 日志尾巴
# --------------------------------------------------------------------------- #


def test_recent_lines_returns_the_tail_and_honours_the_count(tmp_path) -> None:
    """日志尾巴要真的"尾巴"，而且条数听 `count`。"""
    log = tmp_path / "app-20260101.log"
    log.write_text("\n".join(f"第{index}条" for index in range(100)), encoding="utf-8")

    assert report.recent_lines(3, path=log) == ["第97条", "第98条", "第99条"]
    assert len(report.recent_lines(100, path=log)) == 100
    assert report.recent_lines(0, path=log) == []


def test_recent_lines_survives_a_missing_log(tmp_path) -> None:
    """没有日志文件时给**一句说明**，不许抛（第一次跑产品就是没有日志）。"""
    missing = tmp_path / "还没有.log"

    lines = report.recent_lines(5, path=missing)

    assert len(lines) == 1
    assert "还没有日志文件" in lines[0]
    assert str(missing) in lines[0]


# --------------------------------------------------------------------------- #
# 落盘：成功报真实路径，失败报原因（⛔ 绝不抛）
# --------------------------------------------------------------------------- #


def test_write_dump_writes_the_exact_text_and_returns_the_real_path(tmp_path) -> None:
    """落盘的文件名 / 位置 / 内容都要对得上（中文不许变成 ?）。"""
    payload = "诊断内容 ✓\n第二行"

    result = report.write_dump(payload, directory=tmp_path)

    assert result.reason == "", result.reason
    target = Path(result.path)
    assert target.is_file()
    assert target.parent == tmp_path / report.DUMP_SUBDIR
    assert re.fullmatch(r"diag-\d{8}-\d{6}\.txt", target.name), target.name
    assert target.read_text(encoding="utf-8") == payload, "落盘的内容与传进去的不一致"


def test_write_dump_reports_the_reason_instead_of_raising(tmp_path) -> None:
    """**正控（判据 b）**：目标位置底下是个**文件**不是目录 ⇒ 返回原因，不抛。

    真机上的等价情形：日志目录设到了一个只读盘 / 一个已经存在的文件下面。
    这条路不许抛 —— 它是用户出问题时的最后一根稻草。
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")

    result = report.write_dump("x", directory=blocker)

    assert result.path == "", f"建不出来却报了路径：{result.path}"
    assert result.reason, "失败了却没给原因（用户/我们都没法排查）"
    assert "Error" in result.reason, f"原因里该带异常类型，好念出来：{result.reason}"


def test_write_dump_never_raises_even_for_a_weird_target(tmp_path) -> None:
    """再野的目标也不许抛（`\0` 这种路径连 stat 都不行）。"""
    weird = str(tmp_path / ("a" + chr(0) + "b"))
    result = report.write_dump("x", directory=weird)

    assert result.path == ""
    assert result.reason


# --------------------------------------------------------------------------- #
# 与黑匣子 log.py 对拍：两份 log_dir 不许漂移
# --------------------------------------------------------------------------- #


def test_log_dir_agrees_with_the_black_box(monkeypatch, tmp_path) -> None:
    """`report.log_dir()` 是 `log.py::log_dir` 的第二份实现（理由见 report 模块头）。

    两份算出来必须**同一个目录** —— 否则"对话框说日志在 A、日志其实在 B"。
    """
    from ba_mod_maker.app.diag import log as diag_log

    monkeypatch.delenv(report.LOG_DIR_ENV, raising=False)
    assert report.log_dir() == diag_log.log_dir(), "默认目录两边对不上"

    monkeypatch.setenv(report.LOG_DIR_ENV, str(tmp_path))
    assert report.log_dir() == diag_log.log_dir() == tmp_path, "环境变量覆盖那一档对不上"


def test_tail_default_matches_the_report_module() -> None:
    """`cli/diag.py::TAIL_DEFAULT` 与 `report.DEFAULT_TAIL` 是同一个数（抄了一份，钉住）。"""
    from ba_mod_maker.cli import diag as cli_diag

    assert cli_diag.TAIL_DEFAULT == report.DEFAULT_TAIL


# --------------------------------------------------------------------------- #
# CLI：人读 / JSON / --out
# --------------------------------------------------------------------------- #


def test_help_path_does_not_pull_qt() -> None:
    """硬线：`--help` 那条路（= `_build_parser()`）走完，PySide6 不许进 `sys.modules`。

    为什么较真：`cli/__main__.py` 在 `--help` 时就会 import 各子命令模块。若 `cli/diag.py`
    在模块级 import `app.diag`（它的 `__init__` 拉 `log.py` ⇒ 拉 PySide6），
    用户只是敲个 `--help` 就要等一次 Qt 初始化。
    """
    printed = _run_python(
        "import sys\n"
        "from ba_mod_maker.cli import __main__ as m\n"
        "m._build_parser().format_help()\n"
        "print('PySide6' in sys.modules)\n"
    )

    assert printed == "False", "--help 把 Qt 拉起来了"


def test_the_qt_ruler_can_go_red() -> None:
    """**正控**：同一把尺子（看 `sys.modules`）在**该拉 Qt** 的那条路上必须报 True。

    不然上面那条 `== "False"` 就是假绿 —— 比如尺子根本没量到东西。
    （`app/diag/__init__.py` 已于 2026-10-17 改成 PEP 562 惰性 `__getattr__`：在那之前
    它模块级 import `log.py`，于是**任何** `from ba_mod_maker.app.diag import report`
    都会顺手把 Qt 拉进来 —— 下面 `test_importing_report_does_not_pull_qt` 就是钉它的。）
    """
    printed = _run_python(
        "import sys\n"
        "import ba_mod_maker.app.diag.ui\n"
        "print('PySide6' in sys.modules)\n"
    )

    assert printed == "True", "尺子量不到已知会拉 Qt 的那条路 ⇒ 上面那条断言不可信"


def test_importing_report_does_not_pull_qt() -> None:
    """**硬判据**（Lead 2026-10-17 点头改 `app/diag/__init__.py` 的那条理由）：光拿诊断，
    一个字节的 Qt 都不许带。

    为什么不是洁癖：Debug 模式的**用例就是"产品坏了"**，而产品坏掉最常见的一种是
    **Qt 起不来**（缺 DLL / 显卡驱动 / 打包漏文件）。那一刻 `ba-mod-maker diag` 必须
    还能跑 —— 否则用户连"把诊断发给我们"这件事都做不到。
    """
    printed = _run_python(
        "import sys\n"
        "import ba_mod_maker.app.diag.report\n"
        "print('PySide6' in sys.modules)\n"
    )

    assert printed == "False", "拿 report 却把 Qt 拉起来了 —— 缺 Qt 的机器上诊断会跟着一起死"


def test_importing_log_still_pulls_qt() -> None:
    """**正控**：同一个进程里 `log.py` 必须能看到 PySide6 —— 否则上面那条是假绿
    （子进程没量到东西、或者尺子看错了名字，都会是这种形状）。
    """
    printed = _run_python(
        "import sys\n"
        "import ba_mod_maker.app.diag.log\n"
        "print('PySide6' in sys.modules)\n"
    )

    assert printed == "True", "log.py 居然没拉 Qt ⇒ 上面那条「report 不拉 Qt」不可信"


def test_unknown_attribute_raises() -> None:
    """陌生属性必须抛 `AttributeError`（⛔ 不许返回 None 冒充"有"）。

    除了别骗人，还有一条机械理由：`from ba_mod_maker.app.diag import report` 这条路
    **靠**它 —— 只有 `__getattr__` 先说"我不知道 report"，importlib 才会去导
    `ba_mod_maker.app.diag.report` 那个子模块。返回 None 会让调用方拿到一个假的 None，
    从此一律 `'NoneType' object has no attribute ...`，根因藏得死死的。
    """
    from ba_mod_maker.app import diag

    with pytest.raises(AttributeError, match="has no attribute 'no_such_thing'"):
        _ = diag.no_such_thing  # 赋值不是为了用，就是为了"取它"而触发 __getattr__

    # `getattr(x, name, 默认值)` 那条老写法也照常工作：默认值生效，不抛
    assert getattr(diag, "no_such_thing", "缺省") == "缺省"


def test_the_package_still_hands_out_the_same_nine_names() -> None:
    """惰性化之后 `from ba_mod_maker.app.diag import 名字` 必须**一个不少**。

    6 处调用方（cli/diag.py · app/diag/ui.py ×2 · shell/main_window.py ·
    screens/armory/detail_panel.py · screens/editor/pages/base.py ×2）全靠这一条 ——
    改前改后**一个调用方都不许动**。
    """
    from ba_mod_maker.app import diag
    from ba_mod_maker.app.diag import report as submodule_report

    assert set(diag.__all__) == {
        "StrayWindowGuard",
        "event",
        "failure",
        "guard",
        "install_all",
        "install_stray_window_guard",
        "set_context",
        "tail",
        "warn",
    }
    for name in diag.__all__:
        assert callable(getattr(diag, name)), f"{name} 拿不到了（惰性化把调用方弄红了）"

    # 子模块那条路：拿到的必须是**同一个模块对象**（不是另一份拷贝）
    assert submodule_report is report
    assert submodule_report.health_line()


def test_the_json_document_is_the_only_thing_on_stdout() -> None:
    """`--json`：stdout 只许有一个 JSON 文档（人读那份改走 stderr）。"""
    code, out, err = _cli(["diag", "--json", "--tail", "0"])

    assert code == 0
    document = json.loads(out)  # ← 人读那几行若混进 stdout，这里当场 JSONDecodeError
    assert out.lstrip().startswith("{"), f"stdout 前面混进了人读文本：{out[:60]!r}"
    for key in ("health", "version", "python", "log_path", "env", "tail", "wrote"):
        assert key in document, f"JSON 文档里少了 {key}：{sorted(document)}"
    assert document["tail"] == [], "--tail 0 还带了日志行"
    assert document["wrote"] == "", "没给 --out 却报了落盘路径"
    assert "体检" in err, "人读那份没走 stderr（用户在终端里就看不见了）"


def test_the_out_flag_writes_a_real_file(tmp_path) -> None:
    """`--out`：文件真的存在、字节数 > 0、内容就是那三段。"""
    target = tmp_path / "nested" / "d.txt"

    code, out, _err = _cli(["diag", "--tail", "0", "--out", str(target)])

    assert code == 0
    assert target.is_file(), "--out 说存好了，文件却不在"
    assert target.stat().st_size > 0, "文件是空的"
    text = target.read_text(encoding="utf-8")
    assert report.HEALTH_HEADING in text and report.ENV_HEADING in text
    assert str(target) in out, "落盘了却没报路径（用户不知道去哪找）"


def test_the_out_flag_reports_a_bad_target_with_exit_code_2(tmp_path) -> None:
    """`--out` 指到写不进去的地方 ⇒ 退出码 2 ＋ 一句人话，**不抛**。"""
    from ba_mod_maker.cli import diag as cli_diag

    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件", encoding="utf-8")
    target = blocker / "d.txt"

    code, _out, err = _cli(["diag", "--tail", "0", "--out", str(target)])

    assert code == cli_diag.EXIT_UNWRITABLE == 2
    assert not target.exists()
    assert "写不进" in err, err


# --------------------------------------------------------------------------- #
# 界面：👁 视图 -> 「🐞 调试…」
# --------------------------------------------------------------------------- #


def test_the_view_menu_opens_the_debug_dialog(qapp) -> None:
    """入口本体：👁 视图 里有「🐞 调试…」，点了真出窗口，详细读数**默认关**。

    与 `test_shell_menu.py::test_the_menu_opens_the_read_only_dictionary` 同一把尺子
    （拿产品自己的容器属性找菜单，不用方位词）。
    """
    from ba_mod_maker.app.shell.main_window import MainWindow

    window = MainWindow()
    try:
        top = window.menu().actions()
        view = next(a.menu() for a in top if a.text().startswith("👁 视图"))
        entry = next((action for action in view.actions() if "调试" in action.text()), None)
        assert entry is not None, f"视图菜单里没有调试入口：{[a.text() for a in view.actions()]}"
        assert entry.shortcut().isEmpty(), "调试入口不许占快捷键（F3/Ctrl+F/Ctrl+S 都名花有主）"

        entry.trigger()

        dialog = window._debug
        assert dialog is not None, "点了却没有窗口"
        assert "调试" in dialog.windowTitle()
        assert dialog.isVisible()
        assert "\n" not in dialog._health.text(), "一行体检在窗口里折行了"
        # 详细读数默认关：明细区藏着，勾上才出现（对话框本身已经 show 过，量得到）
        assert not dialog._detail.isVisible(), "详细读数默认该是关的"
        dialog._verbose.setChecked(True)
        assert dialog._detail.isVisible(), "勾上详细读数却没展开"
        assert report.HEALTH_HEADING in dialog._detail.toPlainText()
        dialog.close()
    finally:
        window.close()
        window.deleteLater()


def test_copy_succeeds_without_a_popup(qapp, monkeypatch) -> None:
    """AGENTS 弹窗规则：**成功不弹**（状态 label 报一句就够）。"""
    from ba_mod_maker.app.diag import ui as diag_ui

    popups: list[str] = []
    monkeypatch.setattr(diag_ui.QMessageBox, "warning", _record(popups))
    copied: list[str] = []
    monkeypatch.setattr(diag_ui.QGuiApplication, "clipboard", _fake_clipboard(copied))

    dialog = diag_ui.DebugDialog()
    try:
        dialog._copy()

        assert copied, "什么都没进剪贴板"
        assert report.HEALTH_HEADING in copied[0] and report.ENV_HEADING in copied[0]
        assert copied[0] == dialog.diagnostics(), "剪贴板里那份与窗口自己算的不一致"
        assert "已复制诊断" in dialog._status.text()
        assert popups == [], f"成功了还弹窗：{popups}"
    finally:
        dialog.deleteLater()


def test_save_failure_is_the_only_one_that_pops_up(qapp, monkeypatch, tmp_path) -> None:
    """失败**才弹**，而且状态 label 同时留一句（弹窗一闪而过会被错过）。"""
    from ba_mod_maker.app.diag import ui as diag_ui

    popups: list[str] = []
    monkeypatch.setattr(diag_ui.QMessageBox, "warning", _record(popups))
    monkeypatch.setattr(
        diag_ui.diag_report,
        "write_dump",
        lambda _text: report.DumpResult("", "PermissionError: 拒绝访问"),
    )

    dialog = diag_ui.DebugDialog()
    try:
        dialog._save()

        assert "保存失败" in dialog._status.text()
        assert popups == ["warning"], f"失败了没弹窗（这是唯一的弹窗场合）：{popups}"
    finally:
        dialog.deleteLater()


def test_save_success_does_not_pop_up(qapp, monkeypatch, tmp_path) -> None:
    """成功落盘也只写状态 label（路径），不弹窗。"""
    from ba_mod_maker.app.diag import ui as diag_ui

    popups: list[str] = []
    monkeypatch.setattr(diag_ui.QMessageBox, "warning", _record(popups))
    monkeypatch.setattr(
        diag_ui.diag_report,
        "write_dump",
        lambda _text: report.DumpResult(str(tmp_path / "diag-20260101-000000.txt"), ""),
    )

    dialog = diag_ui.DebugDialog()
    try:
        dialog._save()

        assert "已保存" in dialog._status.text()
        assert "diag-20260101-000000.txt" in dialog._status.text()
        assert popups == [], f"成功了还弹窗：{popups}"
    finally:
        dialog.deleteLater()


def _record(sink: list[str]):
    """造一个"记一笔就返回"的 QMessageBox.warning（不真弹，用例不许卡住）。"""

    def _warning(*_args, **_kwargs):
        sink.append("warning")

    return staticmethod(_warning)


def _fake_clipboard(sink: list[str]):
    """造一个假剪贴板：不去动用户真正的剪贴板，还能断言塞了什么。"""

    class _Clipboard:
        def setText(self, text: str) -> None:
            sink.append(text)

    return staticmethod(_Clipboard)


# --------------------------------------------------------------------------- #
# 布局：默认态（详细读数关着）三行字**不许被拉高**
#
# 真机走查抓到的画法缺陷（lead 2026-10-17）：明细区是唯一 addWidget(..., 1) 的项，
# 它 setVisible(False) 之后没有任何东西吃掉剩余高度 ⇒ QBoxLayout 把多出来的高度
# 平摊给每个子项，而 QLabel 的垂直策略是 Preferred（可长高）⇒ 三行字各自悬在
# 一大片空白正中间。修法：三行字垂直策略钉 Maximum ＋ 一条只在明细区藏着时撑开的空档。
# --------------------------------------------------------------------------- #


def test_the_text_lines_do_not_stretch_when_the_detail_is_hidden(qapp) -> None:
    """**默认态**（详细读数关着）：三行字各占自己需要的高度，剩余高度归弹性空档。

    修之前实测（真机同一形状）：体检行 height=163 / sizeHint=69、状态行 163 / 30 ——
    两行字各自悬在一大片空白正中。修之后：15 / 69 与 15 / 30（15 = heightForWidth，
    即这个宽度下真正需要的高度，**没被裁**）。
    """
    from PySide6.QtWidgets import QSizePolicy

    from ba_mod_maker.app.diag.ui import DebugDialog

    dialog = DebugDialog()
    dialog.show()
    qapp.processEvents()
    try:
        # 默认态就是**紧凑**的（真机走查：560 高时中间有 ~700px 纯空，像半成品）
        assert dialog.height() == 380, f"默认态不是紧凑高度 380：{dialog.height()}"
        assert dialog._detail.isVisible() is False, "详细读数默认该是关的"
        # 先证明这个窗口**本来就有**多余高度可分：不然下面的断言等于没量（假绿）
        spare = dialog.height() - sum(
            widget.sizeHint().height() for widget in (dialog._health, dialog._status)
        )
        assert spare > 200, f"这个窗口没有多余高度，尺子量不到东西（spare={spare}）"
        # ① 看得见的症状（尺子量的就是它）：三行字都不许高过自己的 sizeHint
        for name, widget in (("体检行", dialog._health), ("状态行", dialog._status)):
            assert widget.height() <= widget.sizeHint().height() + 2, (
                f"{name} 被拉高了：height={widget.height()} > "
                f"sizeHint={widget.sizeHint().height()}"
            )
        # ② 机理：剩余高度确实归那条空档（不然 ① 只是碰巧没红）
        assert dialog._gap.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding, (
            "明细区关着时空档必须撑开（否则剩余高度会被平摊给三行字）"
        )
    finally:
        dialog.close()
        dialog.deleteLater()


def test_turning_the_detail_on_gives_it_the_room(qapp) -> None:
    """勾上 → 窗口长到 640 且明细区拿到空间；取消 → 缩回 380 的紧凑态。

    ⚠️ 这两个高度是**固定**切换（不是自适应）：用户要的是"第一眼别是个空窗"，
    不是"跟着内容抖"。判据就是三个状态各自的 height()。
    """
    from PySide6.QtWidgets import QSizePolicy

    from ba_mod_maker.app.diag.ui import COMPACT_HEIGHT, DETAIL_HEIGHT, DebugDialog

    assert (COMPACT_HEIGHT, DETAIL_HEIGHT) == (380, 640), "两个固定高度被改过了"

    dialog = DebugDialog()
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.height() == 380, f"默认态不是紧凑高度：{dialog.height()}"

        dialog._verbose.setChecked(True)
        qapp.processEvents()

        assert dialog.height() == 640, f"勾上详细读数后窗口没长到 640：{dialog.height()}"
        assert dialog._detail.isVisible() is True, "勾上详细读数却没展开"
        assert dialog._detail.height() > 200, f"明细区没拿到高度：{dialog._detail.height()}"
        assert dialog._gap.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Minimum, (
            "明细区开着时空间该全给它（空档别再抢）"
        )
        assert dialog._health.height() <= dialog._health.sizeHint().height() + 2

        dialog._verbose.setChecked(False)
        qapp.processEvents()

        assert dialog.height() == 380, f"取消后没缩回 380：{dialog.height()}"
        assert dialog._detail.isVisible() is False, "取消后明细区该收起来"
    finally:
        dialog.close()
        dialog.deleteLater()


def test_the_stretch_ruler_can_go_red(qapp) -> None:
    """**正控**：同一把尺子（height vs sizeHint）对**修之前的布局形状**必须报"拉高"。

    造一个只有两个 wordWrap QLabel 的 QVBoxLayout，再挂一项 `addWidget(..., 1)` 然后
    hide 掉 —— 那正是修之前 DebugDialog 的形状（唯一能伸缩的明细区是藏着的）。
    这条红了才算上面两条可信：同一把尺子在旧形状上量得出拉伸。
    """
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    host = QWidget()
    column = QVBoxLayout(host)
    first = QLabel("第一行字")
    second = QLabel("第二行字")
    for label in (first, second):
        label.setWordWrap(True)
    column.addWidget(first)
    column.addWidget(second)
    hidden = QWidget()  # 假装是那个藏起来的明细区
    column.addWidget(hidden, 1)
    hidden.setVisible(False)

    host.resize(920, 560)
    host.show()
    qapp.processEvents()
    try:
        heights = [label.height() for label in (first, second)]
        hints = [label.sizeHint().height() for label in (first, second)]
        assert all(height > hint + 2 for height, hint in zip(heights, hints, strict=True)), (
            "尺子看不见拉伸 ⇒ 上面那条断言不可信："
            f"height={heights} sizeHint={hints}"
        )
    finally:
        host.close()
        host.deleteLater()
