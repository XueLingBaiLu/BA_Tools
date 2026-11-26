r"""原子替换的退避重试：**一份实现，两个调用面**（writer / reverse_delta）。

## 这条路解决什么（现场）

同一个进程里刚被 UnityPy 读过包之后，去 os.replace **同一个文件** ⇒ WinError 5。
根因是读句柄挂在**引用环**上：refcount 归零也不释放，只有 gc.collect() 才放得开。
实测（_work/meshline/retry_probe.py，本机 2026-10-12，21.6 MB 真包副本）：

| 步 | 条件 | 读数 |
|---|---|---|
| A | 没读过包 | OK |
| B | 刚读过、不 GC | **PermissionError winerror=5** |
| C | GC 之后再试 | OK |
| D | 再试 | OK |
| E | 还在 with 里（句柄活着） | PermissionError winerror=5（**调用方的错**，重试救不了） |

writer 那条路一直是 gc.collect() + 退避重试；**reverse_delta 的还原路原来只睡不 GC**
⇒ 症状「写得起、撤不掉」（B 线在真件用例里撞到，task-5 收口）。

## 判据（每条自带负控）

| # | 判据 | 负控（必须红） |
|---|---|---|
| 1 | 句柄挂在引用环上 ⇒ gc 之后替换成功 | 关掉 GC（collect=False）必报忙（WinError 5） |
| 2 | 句柄被正常持有、稍后放手 ⇒ 退避重试能等到 | attempts=1 时必须报忙 |
| 3 | 一直占着 ⇒ 抛 ReplaceBlockedError（带最后那个 OSError） | —— |
| 4 | 非占用类 OSError（源不存在）⇒ **不重试**、立刻报 | —— |
| 5 | writer._replace_with_retry 转调同一份实现，**异常类型不变**（PermissionError） | —— |
| 6 | 现场端到端：写回之后（同进程被 UnityPy 读过）restore 必须成功 | —— |

⚠ 平台判据：本用例只在**替换被打开的文件会失败**的平台上有效（Windows）；
POSIX 允许替换 ⇒ 自动 skip（⛔ 不假装绿）。

判据一行：pytest tests/test_reverse_delta_retry.py -q -n 0 -> 通过数 从 0 变 N。
"""

from __future__ import annotations

import gc
import os
import threading
import time
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import reverse_delta, writer

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_PC = WORK / "w10_out" / "PC"
TARGET_PID = -8847993855520474906


class _CyclicHolder:
    """把打开的句柄挂在**自环**上：refcount 永远不归零，只有 gc.collect() 放得开。

    这就是 UnityPy / fsspec 那些读源的形状（AssetSource.close() 只丢引用）。
    """

    __slots__ = ("cycle", "handle")

    def __init__(self, path: Path) -> None:
        self.handle = path.open("rb")
        self.cycle = self


@pytest.fixture(scope="module")
def replace_blocked_by_open_handle(tmp_path_factory: pytest.TempPathFactory) -> bool:
    """这个平台「替换一个被打开着的文件」会不会失败（Windows 会，POSIX 不会）。"""
    root = tmp_path_factory.mktemp("plat")
    target = root / "t.bin"
    target.write_bytes(b"a")
    tmp = root / "s.tmp"
    tmp.write_bytes(b"b")
    with target.open("rb"):
        try:
            os.replace(tmp, target)
            return False
        except PermissionError:
            return True


@pytest.fixture
def blocked(replace_blocked_by_open_handle: bool) -> bool:
    if not replace_blocked_by_open_handle:
        pytest.skip("这个平台允许替换被打开的文件（POSIX）—— 本用例测不了")
    return True


def _pair(tmp_path: Path, name: str = "t") -> tuple[Path, Path]:
    target = tmp_path / f"{name}.bin"
    target.write_bytes(b"AAAA")
    tmp = tmp_path / f"{name}.tmp"
    tmp.write_bytes(b"BBBB")
    return tmp, target


# --------------------------------------------------------------------------- #
# ① 引用环上的句柄：GC 才是那把钥匙
# --------------------------------------------------------------------------- #
def test_cyclic_handle_is_released_by_gc(tmp_path: Path, blocked: bool) -> None:
    """正控：句柄挂在引用环上 ⇒ 产品入口（reverse_delta._replace_with_retry）必须成功。

    负控（同一条用例里做成断言）：把 GC 关掉，同一份实现**必须报忙**（WinError 5）
    —— 这一条就是旧实现的行为，⛔ 不许变成永远绿。
    """
    tmp, target = _pair(tmp_path)
    gc.disable()  # ⛔ 关掉自动 GC：否则用例自己会偶发地救活旧实现（假绿）
    try:
        holder = _CyclicHolder(target)
        del holder  # 只剩自环 ⇒ 可达性没了，refcount 还在
        # 负控：不 GC 的那条路必须红（旧实现就是这样，6 次全撞 WinError 5）
        with pytest.raises(reverse_delta.ReplaceBlockedError) as bad:
            reverse_delta.replace_with_retry(tmp, target, attempts=1, pause=0.0, collect=False)
        assert getattr(bad.value.last, "winerror", None) == 5, bad.value.last
        # 正控：产品入口（还原路用的就是它）必须自己 GC 之后替换成功
        reverse_delta._replace_with_retry(tmp, target, attempts=3)
        assert target.read_bytes() == b"BBBB"
    finally:
        gc.enable()


# --------------------------------------------------------------------------- #
# ②③ 退避重试 / 重试到头
# --------------------------------------------------------------------------- #
def test_retry_waits_for_a_holder_to_let_go(tmp_path: Path, blocked: bool) -> None:
    """句柄被正常持有、0.3 s 后放手 ⇒ 退避重试必须等到它（负控 attempts=1 必红）。"""
    tmp, target = _pair(tmp_path)
    handle = target.open("rb")
    timer = threading.Timer(0.3, handle.close)
    timer.start()
    try:
        with pytest.raises(reverse_delta.ReplaceBlockedError):
            # 负控：只给一次机会、不等 ⇒ 必须报忙
            reverse_delta.replace_with_retry(tmp, target, attempts=1, pause=0.0)
        reverse_delta.replace_with_retry(tmp, target, attempts=8, pause=0.2)
        assert target.read_bytes() == b"BBBB"
    finally:
        timer.cancel()
        if not handle.closed:
            handle.close()


def test_giving_up_reports_a_friendly_error(tmp_path: Path, blocked: bool) -> None:
    """一直占着 ⇒ ReplaceBlockedError（AssetError 子类，带最后那个 OSError + 人话）。"""
    tmp, target = _pair(tmp_path)
    with target.open("rb"), pytest.raises(reverse_delta.ReplaceBlockedError) as bad:
        reverse_delta.replace_with_retry(tmp, target, attempts=2, pause=0.0)
    exc = bad.value
    assert isinstance(exc, reverse_delta.AssetError)
    assert "替换" in str(exc)
    assert getattr(exc.last, "winerror", None) == 5


def test_non_busy_error_is_not_retried(tmp_path: Path) -> None:
    """非占用类 OSError（源根本不在）⇒ **不睡不重试**，立刻抛（用时间当尺子）。"""
    target = tmp_path / "t.bin"
    target.write_bytes(b"AAAA")
    started = time.monotonic()
    with pytest.raises(OSError) as bad:
        reverse_delta.replace_with_retry(
            tmp_path / "nope.tmp", target, attempts=6, pause=1.0
        )
    elapsed = time.monotonic() - started
    assert elapsed < 0.5, f"非占用类错误不该重试（等了 {elapsed:.2f}s）"
    assert not isinstance(bad.value, PermissionError)


# --------------------------------------------------------------------------- #
# ⑤ 两个调用面的异常契约不变
# --------------------------------------------------------------------------- #
def test_writer_wrapper_keeps_the_permission_error_contract(tmp_path: Path, blocked: bool) -> None:
    """writer 那两处 except 按 isinstance(exc, PermissionError) 分流 ⇒ 转调后类型必须不变。"""
    tmp, target = _pair(tmp_path)
    with target.open("rb"), pytest.raises(PermissionError) as bad:
        writer._replace_with_retry(tmp, target, attempts=2)
    assert getattr(bad.value, "winerror", None) == 5


def test_one_implementation_only(tmp_path: Path, blocked: bool) -> None:
    """⛔ 不许两份实现：writer 那份必须**转调** reverse_delta 的（拿源码当尺子）。"""
    source = (REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel" / "writer.py").read_text(
        encoding="utf-8"
    )
    head = source.split("def _replace_with_retry(", 1)
    assert len(head) == 2, "writer 里没有 _replace_with_retry 了？"
    body = head[1].split("\n\n\n", 1)[0]
    assert "os.replace" not in body, "writer 里还留着自己的 os.replace 实现（两份了）"
    assert "reverse_delta" in body, "writer 那份没有转调 reverse_delta 的实现"


