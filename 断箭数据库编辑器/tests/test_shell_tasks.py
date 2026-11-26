"""后台任务与「导入」对话框不阻塞界面 —— 这条守的是**产品行为**，不是实现细节。

为什么值得单独一个文件：写回 data.unity3d 是 7.1 GB / 约 4 分钟。跑在主线程上时
事件循环被占住 —— Windows 会给窗口盖上「程序未响应」，进度条也不会动，用户只能杀进程；
而杀进程正是「游戏目录里多出一个满尺寸 .new」的来源（见 test_session 里那两条）。

这里钉死三件事：

1. 活**确实**在工作线程上跑（不是「看起来异步」）；
2. 回调**确实**回到主线程（否则碰控件就是偶发崩溃）；
3. `_on_import()` **立刻返回**，且导入期间不接受关闭窗口。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ba_mod_maker.app.shell import tasks
from ba_mod_maker.core import session
from ba_mod_maker.core.errors import UserFacingError


@pytest.fixture(scope="module")
def qapp():
    """有 Qt 才谈得上「主线程」这四个字。"""
    from PySide6.QtWidgets import QApplication

    try:
        return QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")


def _spin(qapp, predicate, timeout: float = 5.0) -> bool:
    """泵事件循环直到条件成立或超时。

    跨线程回调没有「完成信号」可等，只能这么等；5 秒是给慢机器的余量，
    正常路径在毫秒级就返回。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# --------------------------------------------------------------------------- #
# tasks.run_in_thread
# --------------------------------------------------------------------------- #


def test_work_runs_off_the_gui_thread_and_calls_back_on_it(qapp) -> None:
    from PySide6.QtCore import QObject

    owner = QObject()
    seen: dict[str, object] = {}

    def _work() -> object:
        seen["worker"] = threading.current_thread()
        return 42

    def _ok(result: object) -> None:
        seen["result"] = result
        seen["callback"] = threading.current_thread()

    tasks.run_in_thread(owner, _work, on_success=_ok, on_error=lambda _e: None)
    assert _spin(qapp, lambda: "callback" in seen), "回调没回来"
    assert seen["result"] == 42
    assert seen["worker"] is not threading.main_thread(), "活还是在主线程上跑的"
    assert seen["callback"] is threading.main_thread(), "回调没回到主线程 —— 碰控件会崩"


def test_errors_come_back_on_the_gui_thread(qapp) -> None:
    from PySide6.QtCore import QObject

    owner = QObject()
    seen: dict[str, object] = {}

    def _boom() -> object:
        raise UserFacingError("炸了")

    def _bad(exc: object) -> None:
        seen["exc"] = exc
        seen["callback"] = threading.current_thread()

    tasks.run_in_thread(owner, _boom, on_success=lambda _r: None, on_error=_bad)
    assert _spin(qapp, lambda: "exc" in seen), "异常没回到主线程"
    assert isinstance(seen["exc"], UserFacingError)
    assert str(seen["exc"]) == "炸了"
    assert seen["callback"] is threading.main_thread()


# --------------------------------------------------------------------------- #
# 「导入到 data.unity3d」对话框
# --------------------------------------------------------------------------- #


def _dialog(tmp_path, monkeypatch, *, sleep: float):
    """造一个待导入的对话框，并把真正的导入换成「睡一会儿再返回」。"""
    from ba_mod_maker.app.shell.db_import import DbImportDialog

    container_path = tmp_path / "data.unity3d"
    container_path.write_bytes(b"UnityFS" + bytes(16))
    incoming_path = tmp_path / "in.json"
    incoming_path.write_text("{}", encoding="utf-8")

    seen: dict[str, object] = {}

    def _fake_import(_path, _incoming, **_kwargs):
        seen["thread"] = threading.current_thread()
        time.sleep(sleep)  # 假装这就是那 4 分钟
        return "已写回（模拟）"

    monkeypatch.setattr(session, "import_into_container", _fake_import)
    dialog = DbImportDialog(None, container=container_path)
    dialog._incoming = Path(incoming_path)
    dialog._refresh_enabled()
    return dialog, seen


def test_import_does_not_block_the_gui_thread(qapp, tmp_path, monkeypatch) -> None:
    """`_on_import()` 必须立刻返回，真正的活在别的线程上。

    这条是「界面假死」的回归锁：一旦有人把它改回同步调用，
    下面的 elapsed 断言会立刻变红（sleep 0.3s，阈值 0.15s）。
    """
    dialog, seen = _dialog(tmp_path, monkeypatch, sleep=0.3)

    started = time.monotonic()
    dialog._on_import()
    elapsed = time.monotonic() - started

    assert elapsed < 0.15, f"_on_import() 在界面上阻塞了 {elapsed:.2f}s"
    assert _spin(qapp, lambda: "thread" in seen), "工作线程没跑起来"
    assert seen["thread"] is not threading.main_thread(), "导入跑在主线程上了"
    assert _spin(qapp, lambda: "✅" in dialog._preview.text()), "结果没回到对话框"
    assert dialog.isEnabled(), "干完了就该把对话框放开"
    assert dialog._running is False


def test_import_dialog_refuses_to_close_while_running(qapp, tmp_path, monkeypatch) -> None:
    """导入期间不接受关闭：工作线程还握着这个对话框。"""
    from PySide6.QtGui import QCloseEvent

    dialog, _seen = _dialog(tmp_path, monkeypatch, sleep=0.0)
    dialog._running = True

    refused = QCloseEvent()
    dialog.closeEvent(refused)
    assert not refused.isAccepted(), "导入还没完就把窗口关了"

    dialog._running = False
    allowed = QCloseEvent()
    dialog.closeEvent(allowed)
    assert allowed.isAccepted(), "不忙的时候应该允许关闭"


def test_import_failure_re_enables_the_dialog(qapp, tmp_path, monkeypatch) -> None:
    """失败要把界面放开（从前同步调用时，失败后对话框会一直灰着）。"""
    from ba_mod_maker.app.shell.db_import import DbImportDialog

    container_path = tmp_path / "data.unity3d"
    container_path.write_bytes(b"UnityFS" + bytes(16))
    incoming_path = tmp_path / "in.json"
    incoming_path.write_text("{}", encoding="utf-8")

    def _boom(_path, _incoming, **_kwargs):
        raise UserFacingError("没写成功（模拟）")

    class _NoBox:
        """替掉 QMessageBox：这里只关心"失败后界面放没放开"，不弹真框。"""

        @staticmethod
        def warning(*_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(session, "import_into_container", _boom)
    monkeypatch.setattr("ba_mod_maker.app.shell.db_import.QMessageBox", _NoBox)
    dialog = DbImportDialog(None, container=container_path)
    dialog._incoming = Path(incoming_path)
    dialog._refresh_enabled()

    dialog._on_import()
    assert _spin(qapp, lambda: dialog.isEnabled()), "失败后对话框还灰着"
    assert dialog._running is False
