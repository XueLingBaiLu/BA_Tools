r"""③ 第二道闸（`core/assets/game.py::is_game_running`）的机械判据 —— 2026-10-13 真机踩到的那条。

**判据一行**：`pytest tests/test_game_running.py -q -n 0` -> 修前 **1 failed**／修后 **4 passed**。

## 踩到的样子（用户真机 · Blender 5.2 · 中文 Windows）

    点 ④「写回这个参数」-> 面板报
    AttributeError: 'NoneType' object has no attribute 'lower'
      File ".../core/assets/game.py", line 127, in is_game_running
        return GAME_EXE.lower() in out.lower()

同一段日志上面几行是 `subprocess` 读线程的
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0` —— 中文 Windows 的 `tasklist`
按 **OEM 码页（cp936）** 输出，而 Blender 的 Python 按 **UTF-8** 解 ⇒ 读线程死、`stdout` 变 **None**
⇒ `.lower()` 炸 ⇒ **整条写回链在"游戏在不在跑"这一步就断了**（用户那次写回失败的总根因）。

## 四条判据（三条行为 ＋ 一条"规矩"尺子）

| # | 输入 | 期望 |
|---|---|---|
| 1 | GBK 中文输出（0xd0 打头） | **不崩**，当"没在跑" |
| 2 | ASCII 进程行（BrokenArrow.exe，正控） | True |
| 3 | stdout=None（读线程死掉的样子） | **不崩**，当"没在跑" |
| 4 | 调用参数里出现 `text=True` | **红**（规矩：这条链不许用 text 模式） |
"""
from __future__ import annotations

import subprocess
import types

from ba_mod_maker.core.assets import game


def _done(raw: bytes | None, returncode: int = 0) -> types.SimpleNamespace:
    return types.SimpleNamespace(stdout=raw, returncode=returncode)


def _patch_run(monkeypatch, done) -> list[dict]:
    calls: list[dict] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return done

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_gbk_console_output_does_not_crash(monkeypatch) -> None:
    """判据 1：中文码页输出（0xd0 打头）**不许崩** —— 这就是真机上那条报错。"""
    gbk = "信息: 没有运行的任务匹配指定标准。".encode("gbk")
    _patch_run(monkeypatch, _done(gbk))
    assert game.is_game_running() is False


def test_ascii_process_line_is_detected(monkeypatch) -> None:
    """判据 2（正控）：真跑着的那种输出必须**认出来**（否则这把尺子恒绿，等于没有）。"""
    row = "BrokenArrow.exe           1234 Console                    1    123,456 K\r\n"
    _patch_run(monkeypatch, _done(row.encode("ascii")))
    assert game.is_game_running() is True


def test_none_stdout_is_not_running(monkeypatch) -> None:
    """判据 3：读线程死掉时 `stdout` 是 None —— 也不许崩（真机就是这一条触发的）。"""
    _patch_run(monkeypatch, _done(None))
    assert game.is_game_running() is False


def test_ruler_forbids_text_mode(monkeypatch) -> None:
    """判据 4：这条链**不许**再把 `text=True` 加回来（加回来 = 回老路）。"""
    calls = _patch_run(monkeypatch, _done(b"INFO: No tasks are running"))
    game.is_game_running()
    assert calls, "subprocess.run 没被调用 —— 尺子自己坏了"
    assert not any(call.get("text") for call in calls), calls
