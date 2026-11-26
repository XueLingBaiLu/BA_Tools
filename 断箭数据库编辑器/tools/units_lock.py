r"""一把锁串行：开 units 包（3.38 GB ≈ 10 GB 内存）之前，**必须先拿这把锁**。

AGENTS.md §8.1 / 通宵线 §4：⛔ 同一时刻**只许一个进程**开这个包。
本模块就是那条规矩的机械实现 —— 不是建议，是唯一入口。

用法（脚本里）
-------------
    import sys; sys.path.insert(0, r"D:\断箭模组制作\BA_Mod_Maker_2.0\tools")
    from units_lock import units_lock

    with units_lock("p0-census"):          # 拿不到就等；超时抛 UnitsLockTimeoutError
        with skin_install._open_unit(GAME, "RU_T72B") as unit:
            ...

**注意**：只包住"开包 + 读"，读完立刻释放（读出来的东西放内存/落盘）。
⛔ 不许把整段半小时的分析也圈在锁里 —— 那会把别人饿死。

用法（命令行）
-------------
    python tools/units_lock.py --status      # 现在锁在谁手上
    python tools/units_lock.py --selftest    # 正控 + 负控（必须都过）

判据一行
-------
    python tools/units_lock.py --selftest → 末行 "负控 1 条命中 / 正控 1 条命中" 且 rc=0
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = WORK / "night" / "units.lock"
#: Unity **单实例**：两个 batchmode 同时跑，后一个会**静默交接**给前一个（本机实测）
#: ⇒ 探针也必须一把锁，和 units 包同一个道理。
DEFAULT_UNITY_LOCK = WORK / "night" / "unity.lock"

#: 锁文件多久没心跳、且进程已死 ⇒ 视为陈旧锁，可以抢
STALE_SECONDS = 900.0
#: 等锁的上限（秒）
DEFAULT_TIMEOUT = 1800.0
#: 心跳间隔（秒）
HEARTBEAT = 5.0


class UnitsLockTimeoutError(RuntimeError):
    """等锁超时 —— 说明有别的进程长期占着；先 --status 看是谁。"""


def _alive(pid: int) -> bool:
    """进程还活着吗？（Windows 与 POSIX 都能用）"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_owner(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def status(lock_path: Path | None = None) -> dict:
    """读锁的现状（不改任何东西）。"""
    path = Path(lock_path or DEFAULT_LOCK)
    try:
        if not path.exists():
            return {"held": False, "path": str(path)}
        info = _read_owner(path)
        pid = int(info.get("pid") or 0)
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        # ⚠ **竞态**：exists() 与 stat() 之间持有者把锁放掉了（2026-10-11 p0-uv 实测踩到 ——
        # 排队的人**当场崩**而不是继续等，白等 20 分钟 + rc=1）。⇒ 如实报"锁是空的"，让调用方重试。
        return {"held": False, "path": str(path)}
    return {
        "held": True,
        "path": str(path),
        "owner": info.get("owner") or "?",
        "pid": pid,
        "host": info.get("host") or "?",
        "age_s": round(age, 1),
        "pid_alive": _alive(pid),
        "stale": (age > STALE_SECONDS) and not _alive(pid),
    }


@contextmanager
def units_lock(owner: str = "?", *, timeout: float = DEFAULT_TIMEOUT,
               lock_path: Path | None = None, poll: float = 0.25, announce: bool = True):
    """拿锁 → yield → 放锁。拿不到就等，超时抛 UnitsLockTimeoutError。"""
    path = Path(lock_path or DEFAULT_LOCK)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"owner": owner, "pid": os.getpid(),
                          "host": socket.gethostname(), "at": time.time()},
                         ensure_ascii=False)
    deadline = time.time() + timeout
    waited = False
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            st = status(path)
            if st.get("stale"):
                # 陈旧锁（进程已死 + 太久没心跳）⇒ 抢过来，但留一条记录
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            if not waited and announce:
                print(f"[units_lock] 等锁中：{st.get('owner')} (pid {st.get('pid')}) "
                      f"已持 {st.get('age_s')}s", file=sys.stderr, flush=True)
                waited = True
            if time.time() >= deadline:
                raise UnitsLockTimeoutError(
                    f"{timeout:.0f}s 没等到 units 锁；现在持有者 = {st.get('owner')} "
                    f"pid={st.get('pid')} age={st.get('age_s')}s。"
                    f"先跑 python tools/units_lock.py --status") from None
            time.sleep(poll)
            continue
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        break

    last = time.time()
    try:
        yield
    finally:
        # 心跳：长任务期间让 STALE 判定有意义
        try:
            if time.time() - last > HEARTBEAT:
                os.utime(path, None)
        except OSError:
            pass
        try:
            if _read_owner(path).get("pid") == os.getpid():
                path.unlink()
        except OSError:
            pass


#: ⛔ **锁纪律（2026-10-10 修订，取代"固定顺序"）**：需要同时拿两把锁时一律走 probe_locks ——
#: 它是 **all-or-nothing**（两把都非阻塞试一次，拿不齐立刻放掉已拿的再重试）。
#: ⛔ **不许"持有一把去阻塞等另一把"**：那样既可能死锁，又会把别人的 Unity 那趟一起堵死
#: （实测：先拿 unity 再等 units 8 分钟，p3-tex 的 Unity 被 p3-skin 堵住）。
#: 所以现在**不靠顺序，靠"不构成 hold-and-wait"**。
LOCK_ORDER = ("unity", "units")   #: 只给日志/自检用，不再是必须遵守的获取顺序


UNITS_BUNDLE_TOKEN = "units_assets_all"


def probe_touches_units(probe_cs) -> bool:
    """读探针源码，按字符串判它会不会 LoadFromFile 游戏 units 包。

    这是把"探针也要拿 units 锁"这条规矩**转成机械断言**的那一行（AGENTS 0.9 动作 3）：
    判据可复算、能红 —— 在 .cs 里写一句 units_assets_all 就变 True。
    """
    return UNITS_BUNDLE_TOKEN in Path(probe_cs).read_text(encoding="utf-8", errors="replace")


def probe_locks(owner: str = "?", *, probe_cs=None, timeout: float = DEFAULT_TIMEOUT):
    """Unity 探针那条路要的锁（**全拿或全不拿**，⛔ 不是"固定顺序"）。

    ⛔ 默认**两把都拿**。理由：RouteAProbe 每次都会 LoadFromFile **游戏 units 包（3.38 GB）**
    ⇒ 它跟别处的开包**抢同一份内存**（约 10 GB），所以也算"开包"。

    ✅ **唯一的豁免**：传了 probe_cs（你实际要跑的 .cs 路径）**且**它源码里
    一个 units_assets_all 都没有 ⇒ 这趟**不碰 units**，那就**只拿 unity 锁**
    （少堵一次，也让 units 锁留给真正开包的人）。
    ⇒ 豁免是机械判定的，不是"我觉得我这个不碰"：想蹭豁免就得先让源码过 probe_touches_units()。

    ⚠ **实现 = 全拿或全不拿**：两把都非阻塞试一次；拿不齐就**立刻放掉已拿的**，睡 0.5 s 重试。
    ⛔ 这样既没有死锁（不构成 hold-and-wait），也不会把 Unity 空占着等 units。
    实测踩过：先拿 unity 再阻塞等 units **8 分钟**，把 p3-tex 的 Unity 那趟一起堵死
    （2026-10-10 p3-skin 报的）。
    """
    from contextlib import ExitStack

    need_units = probe_cs is None or probe_touches_units(probe_cs)
    deadline = time.time() + timeout
    while True:
        stack = ExitStack()
        try:
            stack.enter_context(unity_lock(owner, timeout=0.0, announce=False))
            if need_units:
                stack.enter_context(units_lock(owner, timeout=0.0, announce=False))
            return stack
        except UnitsLockTimeoutError:
            stack.close()                    # 把已经拿到的那把放掉 —— 这是不死锁的关键
            if time.time() >= deadline:
                raise UnitsLockTimeoutError(
                    f"{timeout:.0f}s 没把 unity"
                    f"{' + units' if need_units else ''} 两把锁凑齐（all-or-nothing 重试中）。"
                    f"先跑 python tools/units_lock.py --status 看谁拿着") from None
            time.sleep(0.5)


def unity_lock(owner: str = "?", *, timeout: float = DEFAULT_TIMEOUT, announce: bool = True):
    """Unity 批处理探针的一把锁（**与 units 锁分开**，两把锁互不阻塞）。

    ⛔ 为什么必须有：Unity 是**单实例**，两个 batchmode 同时跑会**静默交接**
    —— 你这一轮**白跑**，而且退出码还可能是 0（判据要用**产物 mtime + 内容**）。
    用法与 units_lock 一样：with unity_lock("p3-tex"): subprocess.run([Unity, ...])。
    """
    return units_lock(owner, timeout=timeout, lock_path=DEFAULT_UNITY_LOCK, announce=announce)


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def _cmd_status() -> int:
    st = status()
    if not st["held"]:
        print(f"锁是空的（{st['path']}）—— 可以开包")
        return 0
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 1


def _cmd_selftest() -> int:
    """正控 + 负控：锁必须**能红**（被别人拿着时拿不到）也**能绿**（放掉后拿得到）。"""
    import subprocess
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="unitslock-"))
    lk = tmp / "units.lock"
    code = (
        f"import sys; sys.path.insert(0, r'{str(Path(__file__).resolve().parent)}');"
        "from units_lock import units_lock;"
        "from pathlib import Path;"
        "import time;"
        "t=time.time();"
        f"\nwith units_lock('self', timeout=2.0, lock_path=Path(r'{str(lk)}'), poll=0.05):"
        "\n    print('ACQUIRED')"
        "\nprint('ELAPSED %.2f' % (time.time()-t))"
    )

    n_hit = 0

    # ① 正控：锁空着 ⇒ 必须拿到
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok_free = "ACQUIRED" in p.stdout
    print(f"正控（锁空 -> 拿得到）: {'命中' if ok_free else '[FAIL] 没拿到'}  rc={p.returncode}")
    if not ok_free:
        print(p.stdout, p.stderr)
    n_hit += int(ok_free)

    # ② 负控：自己拿着锁，别人必须拿不到（超时）
    with units_lock("holder", lock_path=lk):
        p2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    ok_blocked = ("ACQUIRED" not in p2.stdout) and (p2.returncode != 0)
    print(f"负控（锁被占 -> 拿不到）: {'命中' if ok_blocked else '[FAIL] 居然拿到了'}")
    if not ok_blocked:
        print(p2.stdout, p2.stderr)
    n_hit += int(ok_blocked)

    # ③ 放掉之后必须又能拿到（锁没漏）
    p3 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    ok_back = "ACQUIRED" in p3.stdout
    print(f"回归（放锁后 -> 又拿得到）: {'命中' if ok_back else '[FAIL] 拿不到'}")
    n_hit += int(ok_back)

    # ④ 竞态守卫：exists() 与 stat() 之间锁被持有者放掉 ⇒ status() 必须返回 held=False，⛔ 不许抛
    import unittest.mock as _mock

    race_lock = tmp / "race.lock"
    race_lock.write_text("{}", encoding="utf-8")
    try:
        with _mock.patch.object(Path, "stat", side_effect=FileNotFoundError):
            st_race = status(race_lock)
        ok_race = st_race.get("held") is False
    except Exception as exc:
        ok_race = False
        print("  竞态守卫抛了：", type(exc).__name__, exc)
    print(f"竞态（exists 与 stat 之间被放锁）: {'命中' if ok_race else '[FAIL] 抛了'}")
    n_hit += int(ok_race)

    print(f"\n负控 1 条命中 / 正控 1 条命中 / 回归 1 条 / 竞态 1 条 => 合计 {n_hit}/4")
    return 0 if n_hit == 4 else 1


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):          # 老环境可能没有 reconfigure
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="units 包的一把锁（串行开包）")
    ap.add_argument("--status", action="store_true", help="看锁现在在谁手上")
    ap.add_argument("--selftest", action="store_true", help="正控 + 负控自检")
    ap.add_argument("--owner", default="cli", help="取锁时的署名")
    ap.add_argument("--seconds", type=float, default=0.0, help="拿到锁后占住 N 秒（试锁用）")
    args = ap.parse_args(argv)

    if args.selftest:
        return _cmd_selftest()
    if args.seconds > 0:
        with units_lock(args.owner):
            print(f"[{args.owner}] 拿到锁，占住 {args.seconds}s")
            time.sleep(args.seconds)
        print("放锁")
        return 0
    return _cmd_status()


if __name__ == "__main__":
    raise SystemExit(main())
