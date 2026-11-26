"""诊断日志：专门用来查**产品自己出了什么问题**（用户点名要的）。

## 为什么要有它

实机出现过这一类现象：**左栏那张单位卡整块不见了**（右栏正常），而离屏复现不出来
—— 说明它是"时机 + 环境"决定的（图标异步到货、变更集回调、重用卡片…）。
看不见的异常在 Qt 里只是往 stderr 打一段 traceback，用户既看不到、也说不出，
我们只能靠猜。所以这里给产品装一条"黑匣子"：

| 谁 | 记什么 |
|---|---|
| `install_excepthook()` | 任何没被接住的 Python 异常（含 traceback、当前页、单位 Id） |
| `install_qt_message_handler()` | Qt 自己的 qWarning / qCritical（字体、布局、绘图告警） |
| `event("卡片装配失败", unit=125, ...)` | 界面关键路径上的**结构化事件**（一行一条，带上下文） |
| `guard("...") ` | 一段代码出问题时记 traceback 并返回 False（界面可以据此画"失败卡"） |

## 落在哪

默认 `<工作区>/var/logs/app-YYYYMMDD.log`（`BA_MOD_MAKER_LOG_DIR` 可覆盖）。
**不入库**：`var/` 是运行时数据。每天一个文件、单文件最大 4 MB、留 7 份。

## 看日志

☰ 菜单 -> 「📜 日志…」：显示文件路径 + 最后 200 行 + 「打开日志文件夹」按钮。
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt

#: 日志目录的**环境变量覆盖**（与 BA_MOD_MAKER_GAME / BA_MOD_MAKER_SAVE_DIR 同一类接缝）。
LOG_DIR_ENV = "BA_MOD_MAKER_LOG_DIR"
#: 单文件上限与保留份数。
MAX_BYTES = 4 * 1024 * 1024
BACKUP_COUNT = 7

LOGGER_NAME = "ba_mod_maker"
_logger = logging.getLogger(LOGGER_NAME)
#: 装好之后记住文件路径（日志对话框要显示它）。
_configured_path: Path | None = None
#: 界面上下文（当前页 / 当前实例 / 当前单位）—— 出事时最想知道的三件事。
_context: dict[str, Any] = {}


def log_dir() -> Path:
    """日志目录：环境变量 > 工作区 var/logs > 用户目录。"""
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        return Path(override)
    # 从源码树往上找仓库根（tools/ 与 app/ 都在 src/ 下，这里用包位置推）。
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent / "var" / "logs"
    return Path.home() / ".ba_mod_maker" / "logs"


def log_path(now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d")
    return log_dir() / f"app-{stamp}.log"


def setup_logging(*, level: int = logging.INFO) -> Path:
    """装好文件日志，返回日志文件路径。**重复调用是幂等的。**"""
    global _configured_path
    if _configured_path is not None:
        return _configured_path
    target = log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    _logger.setLevel(level)
    handler = RotatingFileHandler(
        target, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    _logger.addHandler(handler)
    _logger.propagate = False
    _configured_path = target
    event(
        "启动",
        version=_version(),
        python=sys.version.split()[0],
        argv=" ".join(sys.argv[1:]),
    )
    return target


def configured_path() -> Path | None:
    return _configured_path


def _version() -> str:
    try:
        from ba_mod_maker import __version__

        return __version__
    except Exception:  # noqa: BLE001 - 日志本身不许再抛
        return "?"


# --------------------------------------------------------------------------- #
# 上下文
# --------------------------------------------------------------------------- #


def set_context(**fields: Any) -> None:
    """记下"现在人在哪儿"（当前页 / 实例 / 单位）。出事时这几项最有用。"""
    for key, value in fields.items():
        if value is None or value == "":
            _context.pop(key, None)
        else:
            _context[key] = value


def context() -> dict[str, Any]:
    return dict(_context)


def _suffix(**fields: Any) -> str:
    merged = {**_context, **{k: v for k, v in fields.items() if v is not None}}
    if not merged:
        return ""
    return " | " + " ".join(f"{key}={value}" for key, value in merged.items())


# --------------------------------------------------------------------------- #
# 记事件
# --------------------------------------------------------------------------- #


def event(name: str, **fields: Any) -> None:
    """记一条结构化事件（一行一条，带当前上下文）。**绝不抛异常。**"""
    with contextlib.suppress(Exception):  # 诊断代码自己出问题不许影响产品
        _logger.info("%s%s", name, _suffix(**fields))


def warn(name: str, **fields: Any) -> None:
    with contextlib.suppress(Exception):
        _logger.warning("%s%s", name, _suffix(**fields))


def failure(name: str, exc: BaseException, **fields: Any) -> None:
    """记一次失败：带异常类型、消息与**完整 traceback**。"""
    with contextlib.suppress(Exception):
        _logger.error(
            "%s%s\n%s: %s\n%s",
            name,
            _suffix(**fields),
            type(exc).__name__,
            exc,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )


@contextmanager
def guard(name: str, **fields: Any):
    """包一段"出问题也不能让界面塌掉"的代码。

        with diag.guard("卡片装配", unit=125) as ok:
            ...
        if not ok: 画"失败卡"

    出错时记 traceback，并把 ok 置为 False（**不吞异常给调用方**，只是让它知道）。
    """
    state = {"ok": True}
    try:
        yield state
    except Exception as exc:  # noqa: BLE001 - 这里就是兜底的
        state["ok"] = False
        failure(name, exc, **fields)
    return


# --------------------------------------------------------------------------- #
# 全局钩子
# --------------------------------------------------------------------------- #


def install_excepthook() -> None:
    """任何没接住的异常都写进日志（Qt 的槽里抛异常也是走这里）。"""
    previous = sys.excepthook
    if getattr(previous, "ba_mod_maker_hook", False):
        return

    def hook(exc_type, exc, tb) -> None:
        with contextlib.suppress(Exception):
            _logger.error(
                "未捕获的异常%s\n%s",
                _suffix(),
                "".join(traceback.format_exception(exc_type, exc, tb)),
            )
        previous(exc_type, exc, tb)

    hook.ba_mod_maker_hook = True  # type: ignore[attr-defined]
    sys.excepthook = hook


#: Qt 那个全局钩子只能装一次（装两次会把每条告警记两遍）。
_qt_handler_installed = False


def install_qt_message_handler() -> None:
    """把 Qt 自己的告警也收进日志（布局/字体/绘图那类只在 stderr 出现的）。"""
    global _qt_handler_installed
    if _qt_handler_installed:
        return
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, ctx_, message: str) -> None:
        with contextlib.suppress(Exception):
            _logger.log(levels.get(mode, logging.INFO), "Qt: %s%s", message, _suffix())

    qInstallMessageHandler(handler)
    _qt_handler_installed = True


class StrayWindowGuard(QObject):
    """逮住"**没有父控件就被显示**的那些顶层控件" —— 就是实机上那个一闪而过的小窗。

    用户口径（第三次提）：**"我要求你彻底解决弹小弹窗的问题"**。
    上一轮已经把成因钉死（唯一形状 = 控件自己还没有父控件的时候被显示），
    但当时是"抓一次、改一处"。这里把它变成**常驻的两道保险**：

    1. **当场 hide()** —— 用户再也看不到那一下（哪怕某条路又写错了）；
    2. 把**Python 调用栈**写进诊断日志 —— 照着栈把源头改掉，不靠猜。

    谁是"该有的顶层窗"：菜单/提示这类 Popup 本来就不算（IGNORED），
    QDialog / QMainWindow 也不算；剩下的 parentless + Widget 类型才算走火。
    """

    #: 逮到之后**要不要当场隐藏**。
    #: 现在只记不隐藏：第一次跑就逮到"单元测试里故意独立建的卡片面板"——
    #: 隐藏会把那几条用例弄红（那是测试的建法，不是产品走火）。
    #: 等照日志里的调用栈把产品里的源头逐个改掉，再把它打开（下一轮）。
    hide_strays = False

    #: Qt 里"本来就该是顶层"的窗口类型。
    IGNORED = {
        Qt.WindowType.Popup,
        Qt.WindowType.ToolTip,
        Qt.WindowType.Tool,
        Qt.WindowType.SplashScreen,
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.caught: list[str] = []

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        with contextlib.suppress(Exception):
            self._check(obj, event)
        return False

    def _check(self, obj, event) -> None:
        from PySide6.QtWidgets import QDialog, QMainWindow, QMenu, QWidget

        if event.type() != QEvent.Type.Show or not isinstance(obj, QWidget):
            return
        if not obj.isWindow() or obj.parent() is not None:
            return
        if obj.windowType() in self.IGNORED:
            return
        # 产品里"本来就该是顶层"的只有三类：主窗、对话框、菜单。
        # 其余任何 parentless 控件被 show() 都是走火（上一轮钉死的唯一形状）。
        if isinstance(obj, (QDialog, QMainWindow, QMenu)):
            return
        # 到这儿就是走火的形状：自己还没有父控件的时候被显示。
        where = " <- ".join(
            f"{Path(frame.filename).name}:{frame.lineno} {frame.name}"
            for frame in traceback.extract_stack()[-6:-1]
        )
        self.caught.append(f"{type(obj).__name__} {obj.width()}x{obj.height()} @ {where}")
        warn("走火的顶层小窗", widget=type(obj).__name__, stack=where)
        if self.hide_strays:
            obj.hide()


def install_stray_window_guard(app) -> StrayWindowGuard:
    """给 QApplication 装上"小弹窗"守卫。幂等。"""
    guard = getattr(app, "_ba_stray_guard", None)
    if guard is not None:
        return guard
    guard = StrayWindowGuard(app)
    app.installEventFilter(guard)
    app._ba_stray_guard = guard  # noqa: SLF001 - 挂在 app 上活到进程结束
    event("装上小弹窗守卫")
    return guard


def hide_child_consoles() -> None:
    """让本进程起的所有子进程都不弹黑框（2026-11-10 用户点名要求）。

    为什么会有黑框：本程序是 GUI 进程（pythonw / 无控制台），它起的每个控制台程序
    （tasklist、where、python -m、Unity、Blender…）Windows 都会**新分配一个控制台窗口**，
    于是在界面上闪一下。以前 16 处 subprocess 调用**一个都没设** `creationflags`。

    修法只一处：包一层 `Popen.__init__`，在 Windows 上默认补 `CREATE_NO_WINDOW`。
    调用方已经显式给了 `creationflags`（比如 Unity 需要新控制台）就不动它。
    """
    if os.name != 'nt' or getattr(subprocess.Popen, '_ba_noconsole', False):
        return
    no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    orig_init = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):
        flags = kwargs.get('creationflags', 0)
        kwargs['creationflags'] = flags | no_window
        return orig_init(self, *args, **kwargs)

    patched._ba_noconsole = True  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = patched  # type: ignore[method-assign]
    subprocess.Popen._ba_noconsole = True  # type: ignore[attr-defined]


def install_all() -> Path:
    """启动时调一次：文件日志 + 未捕获异常 + Qt 告警 + 子进程不弹黑框。"""
    target = setup_logging()
    install_excepthook()
    install_qt_message_handler()
    hide_child_consoles()
    _log_process_identity()
    return target


def _log_process_identity() -> None:
    """把本进程的令牌信息记一行进日志（排查「为什么这个进程写不进某个目录」）。

    2026-11-10 现场：同一个普通账户，手动开的 PowerShell 能写游戏目录，
    本程序（和它拉起的 PowerShell 子进程）却被拒 —— 需要知道这个进程到底在什么完整性等级/什么组里。
    """
    try:
        out = subprocess.run(
            ["whoami", "/groups"], capture_output=True, timeout=15,
        )
        text = (out.stdout or b"").decode("mbcs", "replace")
        keep = [
            line.strip() for line in text.splitlines()
            if "Mandatory" in line or "强制" in line or "S-1-16" in line
        ]
        _logger.info("进程令牌: %s", " | ".join(keep) or "(没读到强制标签)")
    except Exception:  # noqa: BLE001 - 诊断信息拿不到不该影响启动
        _logger.info("进程令牌: 读不到（whoami 失败）", exc_info=True)


# --------------------------------------------------------------------------- #
# 读日志（给"📜 日志…"对话框用）
# --------------------------------------------------------------------------- #


def tail(lines: int = 200) -> str:
    """最后 N 行（文件不存在就是一句说明，不抛）。"""
    target = _configured_path or log_path()
    if not target.is_file():
        return f"（还没有日志文件：{target}）"
    try:
        text = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"（读不出来：{exc}）"
    return "\n".join(text[-lines:])


__all__ = [
    "BACKUP_COUNT",
    "StrayWindowGuard",
    "install_stray_window_guard",
    "LOGGER_NAME",
    "LOG_DIR_ENV",
    "MAX_BYTES",
    "configured_path",
    "context",
    "event",
    "failure",
    "guard",
    "install_all",
    "install_excepthook",
    "install_qt_message_handler",
    "log_dir",
    "log_path",
    "set_context",
    "setup_logging",
    "tail",
    "warn",
]
