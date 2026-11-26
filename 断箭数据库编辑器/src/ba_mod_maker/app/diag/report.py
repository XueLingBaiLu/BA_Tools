"""Debug 模式的一行体检 / 环境 / 最近事件 / 落盘 —— **纯函数、零 Qt、零副作用**。

## 为什么单独一个模块

产品已经有黑匣子（`app/diag/log.py`）和看日志的对话框（`app/diag/ui.py`），
但两样都**只在界面里**才拿得到。用户报"界面不对"时我们最想拿到的其实是一段
**能整段粘过来的文字**：一行体检 ＋ 环境变量 ＋ 最近事件。这段文字既要在对话框里
复制得出来，也要在**没起界面**时从命令行拿得到（`ba-mod-maker diag`）。

⇒ 拼这段文字的逻辑只能有一份，而且**不许碰 Qt**：

* `app/diag/log.py` 在模块级 `from PySide6.QtCore import ...` —— 一 import 就把 Qt 拉起来；
* `cli/__main__.py` 在 `--help` 时就会 import 各子命令模块 —— 这里若也拉 Qt，
  用户只是敲个 `ba-mod-maker --help` 就要等一次 Qt 初始化，没有 Qt 的机器上还会当场崩。

所以本模块**只 import 标准库**；需要日志路径时**自己按同一套规则算**（`log_dir()`，
规则与 `log.py::log_dir` 逐条相同）。两边不许漂移 ——
`tests/test_diag_report.py::test_log_dir_agrees_with_the_black_box` 拿真 log.py 对拍。

## 五个纯函数（都是"给个字符串回来"，不读全局状态、不写盘）

| 函数 | 回来什么 |
|---|---|
| `health_line()` | **一行**体健：版本 · Python · 当天日志 · 覆盖变量设了哪几个 |
| `env_lines()` | 环境块：四个环境变量 ＋ 解释器 ＋ 本文件位置 |
| `recent_lines(n)` | 日志尾部 n 行（没日志 / 读不动都给一句说明） |
| `diagnostics_text()` | 『📋 复制诊断』要的整段：一行体检 ＋ 环境 ＋ 最近事件 |
| `write_dump(text)` | 落盘的**真实路径**；失败给 `("", 原因)`，⛔ 绝不抛 |

## 一行体检为何要截断

"一行"是硬要求：它要能被整段粘进聊天窗口而不折行。路径一长（用户把日志搬到别的盘），
整行会膨胀到两三百列，所以 `HEALTH_LIMIT` 是**显示列**上限（中文算 2 列），超了截断补 `…`。
"""

from __future__ import annotations

import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

#: 日志目录的**环境变量覆盖**（与 `app/diag/log.py::LOG_DIR_ENV` 同一个名字、同一条规则）。
LOG_DIR_ENV = "BA_MOD_MAKER_LOG_DIR"

#: 环境块里逐个列的环境变量（顺序 = `env_lines()` 的顺序）。
#: 后三个是**覆盖**：设了就会改产品行为，所以一行体检里还要单独报它们设没设。
ENV_KEYS = (
    "LOCALAPPDATA",
    LOG_DIR_ENV,
    "BA_MOD_MAKER_GAME",
    "BA_MOD_MAKER_SAVE_DIR",
)

#: 三个覆盖变量的**短名**（一行体检里只放得下短名）。
OVERRIDE_SHORT = {
    LOG_DIR_ENV: "LOG_DIR",
    "BA_MOD_MAKER_GAME": "GAME",
    "BA_MOD_MAKER_SAVE_DIR": "SAVE_DIR",
}

#: 一行体检最多占多少**显示列**（中文算 2 列）。超了就截断 —— 见模块头最后一节。
HEALTH_LIMIT = 160

#: 默认带多少行日志尾巴（够看清"刚才那一下"，又不至于粘不动）。
DEFAULT_TAIL = 40

#: 落盘子目录与前缀：`<日志目录>/diag/diag-YYYYmmdd-HHMMSS.txt`。
DUMP_SUBDIR = "diag"
DUMP_PREFIX = "diag-"

#: 环境变量没设时显示的占位（用户一眼就能分清"空"与"没设"）。
NOT_SET = "（未设）"

#: 环境块与最近事件块的标题 —— 用例按它们断言"这一块还在"。
ENV_HEADING = "环境"
TAIL_HEADING = "最近事件"
HEALTH_HEADING = "一行体检"


class DumpResult(NamedTuple):
    """落盘结果：成功时 `path` 有值、`reason` 为空；失败**反过来**（⛔ 不抛）。

    拆成两个字段而不是只回一个字符串：调用方要同时回答"存哪了"与"为什么没存上"，
    而后者必须能原样念给用户听（`WinError 5: 拒绝访问` 这种）。
    """

    path: str
    reason: str


def display_width(text: str) -> int:
    """这段文字在终端里占几列（东亚宽字符算 2 列，其余算 1）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def clip(text: str, limit: int = HEALTH_LIMIT) -> str:
    """按**显示列**截断（末尾补一个 `…`）。

    为什么不按字符数截：中文一个字占 2 列，按字符数截出来的行在终端里要宽一倍。
    """
    if display_width(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + step > limit - 1:  # 留一列给末尾那个 …
            break
        kept.append(char)
        used += step
    return "".join(kept) + "…"


def version() -> str:
    """产品版本号（读不到就给 `?` —— 体检本身不许因为读版本而失败）。"""
    try:
        from ba_mod_maker import __version__
    except Exception:  # noqa: BLE001 - 体检自己不许再抛
        return "?"
    return str(__version__)


def log_dir() -> Path:
    """日志目录：环境变量 > 工作区 var/logs > 用户目录（与 log.py 同规则）。

    ⚠️ 这是 `app/diag/log.py::log_dir` 的**第二份实现**，故意的：那个模块一 import
    就把 Qt 拉起来（见模块头）。两份不许漂移 —— `tests/test_diag_report.py` 里有一条
    对拍用例（同一组环境变量下两边必须算出同一个目录）。
    """
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent / "var" / "logs"
    return Path.home() / ".ba_mod_maker" / "logs"


def log_path(now: datetime | None = None) -> Path:
    """当天的日志文件（与 `log.py::log_path` 同规则：一天一个文件）。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d")
    return log_dir() / f"app-{stamp}.log"


def _is_set(key: str) -> bool:
    """环境变量设了没有（空白算没设 —— 与 log.py 取覆盖值时的判断一致）。"""
    return bool(os.environ.get(key, "").strip())


def _override_flags() -> str:
    """`LOG_DIR ✓ GAME ✗ SAVE_DIR ✗` —— 一行体检里那半截。

    为什么要报"设没设"而不是报值：值可能是一整条路径（太长），而"设了没有"
    恰好是判"用户看到的是他自己的环境还是默认环境"最关键的一格。
    """
    return " ".join(
        f"{OVERRIDE_SHORT[key]} {'✓' if _is_set(key) else '✗'}" for key in OVERRIDE_SHORT
    )


def _short(path: Path) -> str:
    """体检里给个短路径：在**当前目录**下面就用相对路径，否则用全路径。"""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def health_line(*, now: datetime | None = None) -> str:
    """**一行**体检：版本 · Python · 当天日志文件 · 覆盖变量设了哪几个。

    永远是一行、长度受 `HEALTH_LIMIT` 约束（显示列），末尾不换行。
    """
    parts = [
        f"ba-mod-maker {version()}",
        f"Python {sys.version.split()[0]}",
        f"日志 {_short(log_path(now))}",
        f"覆盖 {_override_flags()}",
    ]
    return clip(" · ".join(parts))


def env_lines() -> list[str]:
    """环境块：四个环境变量（没设也列，写「未设」）＋ 解释器 ＋ 本文件位置。

    这几项是判"用户那台机器和我这台机器差在哪"的第一现场：解释器不同（venv 没激活）、
    日志被环境变量搬走了、游戏根目录指到别处 —— 全在这一块里。
    """
    lines = [f"{key} = {os.environ.get(key, '').strip() or NOT_SET}" for key in ENV_KEYS]
    lines.append(f"sys.executable = {sys.executable}")
    lines.append(f"__file__ = {Path(__file__).resolve()}")
    return lines


def recent_lines(count: int = DEFAULT_TAIL, *, path: Path | None = None) -> list[str]:
    """日志尾部 count 行（没日志文件 / 读不动都给**一句说明**，⛔ 绝不抛）。

    `path` 是给用例与"看看别的日子那份日志"留的接缝；产品侧不传，用当天的。
    """
    if count <= 0:
        return []
    target = path or log_path()
    if not target.is_file():
        return [f"（还没有日志文件：{target}）"]
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"（读不出来：{type(exc).__name__}: {exc}）"]
    return text.splitlines()[-count:]


def diagnostics_text(
    *, count: int = DEFAULT_TAIL, now: datetime | None = None, path: Path | None = None
) -> str:
    """『📋 复制诊断』/ 落盘用的整段：一行体检 ＋ 环境 ＋ 最近事件。

    三段各带一个 `## ` 标题：用户把这段粘进聊天窗口后，我们要能一眼分清
"这是环境不对"还是"这是刚才那一下出错了"。⛔ 别把段与段之间的顺序改掉 ——
    用例与人的阅读习惯都按这个顺序钉住了（体检 在最先，它决定要不要往下看）。
    """
    sections = [
        f"## {HEALTH_HEADING}",
        health_line(now=now),
        "",
        f"## {ENV_HEADING}",
        *env_lines(),
        "",
        f"## {TAIL_HEADING}（日志尾部 {count} 行）",
        *(recent_lines(count, path=path) or ["（没有）"]),
        "",
    ]
    return "\n".join(sections)


def write_dump(
    text: str, *, directory: Path | str | None = None, now: datetime | None = None
) -> DumpResult:
    """把诊断落成 `<日志目录>/diag/diag-YYYYmmdd-HHMMSS.txt`，回来**真实路径**。

    失败时返回 `DumpResult("", 原因)` 而**不抛**：这条路是用户出问题时的最后一根
    稻草 —— 它自己再炸，就连"把东西发给助手"都做不到了。`directory` 是给用例
    （与"存到别处"）留的接缝；不传就用当天的日志目录。
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    root = Path(directory) if directory is not None else log_dir()
    target = root / DUMP_SUBDIR / f"{DUMP_PREFIX}{stamp}.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except (OSError, ValueError) as exc:
        return DumpResult("", f"{type(exc).__name__}: {exc}")
    return DumpResult(str(target), "")


__all__ = [
    "DEFAULT_TAIL",
    "DUMP_PREFIX",
    "DUMP_SUBDIR",
    "DumpResult",
    "ENV_HEADING",
    "ENV_KEYS",
    "HEALTH_HEADING",
    "HEALTH_LIMIT",
    "LOG_DIR_ENV",
    "NOT_SET",
    "OVERRIDE_SHORT",
    "TAIL_HEADING",
    "clip",
    "diagnostics_text",
    "display_width",
    "env_lines",
    "health_line",
    "log_dir",
    "log_path",
    "recent_lines",
    "version",
    "write_dump",
]
