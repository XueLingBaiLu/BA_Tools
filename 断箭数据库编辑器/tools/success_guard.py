r"""成功率闸 —— 把尺子接到 Agent **每轮必经的那条路**上（2026-10-11）。

## 判据一行

    .venv\Scripts\python.exe tools\success_guard.py --selftest
      -> 末行印「正控 3/3 报警 · 负控 3/3 安静」且 rc=0

## 它解决什么

用户原话：「**你能不能让 Agent 自己会用，而不是有这个功能但不用**」。
答案不是再写一条规矩 —— 本仓 AGENTS §0.4 的教训就是「**规则写了 ≠ 规则会触发**」。
所以这里做的是**接线**，不是文档：

| 路 | 谁触发 | 机制 |
|---|---|---|
| **推送** | 插件每轮跑 `success_guard.py --inject` | **有信号才注入**（无信号则空串） |
| **路标** | systemPrompt 静态区一行 | 告诉 Agent「看到 🚦 就该动手」，零重复成本 |

## 什么算「信号」（⛔ 不是每轮唠叨）

**检测到一局新日志没进台账** —— 也就是：游戏的 `GameLogs` 里出现了比台账最后一条更新的那份，
说明「装完 → 起过一局 → **没人读它**」。这时才输出，而且输出里直接给两条可粘贴的命令。

## 命令

    $V tools\success_guard.py --inject     # 给插件用：有信号打一块，没信号打空串
    $V tools\success_guard.py --status     # 给人看：台账 + 最新一局 + 四指标一行
    $V tools\success_guard.py --selftest

## 心跳（静默失败必须留痕）

每次被调用都写 `_work/success-guard-heartbeat.json`（lastCallAt / signal / chars）。
理由：**「该静默」与「根本没被调用」在输出上一模一样** —— 有了心跳才分得清。
判据一行：`tools/success_guard.py --status` 里那行「心跳：<时间>」应当随每一轮更新。

## 测试钩子（只给自检用，⛔ 别在别处设）

    BA_GUARD_LOG_DIR   改指夹具日志目录（不碰真 GameLogs）
    BA_GUARD_LEDGER    改指夹具台账（不碰真 _work/acceptance/log.tsv）
    BA_GUARD_HEARTBEAT 改指夹具心跳（⛔ 不设它而跑自检 = 把真心跳写脏）

插件的 selftest.mjs 就是靠这两个变量造「有信号 / 没信号」两档，
**所以用例与真实语料无关**（kb/29 §6 那条教训：用例只要依赖语料，文档一引用它就失效）。

## 边界

1. 它**只提醒**，不代替判断：读数与分类口径全在 tools/game_log.py（逻辑只有一份）。
2. 台账里没记过 = 报警；记过 = 安静（**哪怕那一局是坏的** —— 坏不坏由台账那一条负责）。
3. 卡在「注入太多 ⇒ 模型当噪声」这条坑上（2026-10-11 实测过一次）：所以**内容去重**
   交给插件，这里只管「有没有信号」。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

#: 台账落点。**测试钩子**：BA_GUARD_LEDGER 可改指夹具（插件自检用它，不碰真账本）。
LEDGER = Path(os.environ.get("BA_GUARD_LEDGER") or (WORK / "acceptance" / "log.tsv"))

#: 心跳：**每次被调用**都落一份 —— 把「该静默」与「根本没被调用」区分开。
#: （与插件 kb-lookup 的 _work/kb-lookup-heartbeat.json 同一个理由：静默失败必须留痕。）
#: ⛔ 心跳路径**必须可覆盖**：自检写了真心跳，--status 就会把"自检跑过"误报成"插件调用过"
#: （kb 插件踩过同一个坑，见 tools/dsh-plugin-kb-lookup/selftest.mjs:46）。
HEARTBEAT = Path(
    os.environ.get("BA_GUARD_HEARTBEAT") or (WORK / "success-guard-heartbeat.json")
)

MAX_RARE_SHOWN = 3


def _beat(**extra: object) -> None:
    """落心跳。⛔ 心跳自己不许把闸弄挂。"""
    try:
        cur: dict = {}
        if HEARTBEAT.is_file():
            cur = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        cur.update(
            {
                "lastCallAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "pid": os.getpid(),
            }
        )
        cur.update(extra)
        HEARTBEAT.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # 心跳是旁路：任何异常都不许影响判定
        pass


def resolve_log_dir(log_dir: Path | None = None) -> Path | None:
    """日志目录：显式参数 > BA_GUARD_LOG_DIR 夹具 > 真游戏目录（不猜）。"""
    if log_dir is not None:
        return log_dir
    env = os.environ.get("BA_GUARD_LOG_DIR")
    if env:
        return Path(env)
    import game_log as gl

    return gl.default_log_dir()


def _newest_log(log_dir: Path) -> Path | None:
    logs = sorted(log_dir.glob("*.log"))
    return logs[-1] if logs else None


def _recorded_logs(ledger: Path) -> set[str]:
    import acceptance_log as al

    return {r.log_file for r in al.read_ledger(ledger) if r.log_file}


def build_inject(*, log_dir: Path | None = None, ledger: Path = LEDGER) -> str:
    """有信号 -> 一块可粘贴的提醒；没信号 -> 空串。"""
    import game_log as gl

    if log_dir is None:
        log_dir = resolve_log_dir()
    if log_dir is None or not log_dir.is_dir():
        return ""
    newest = _newest_log(log_dir)
    if newest is None:
        return ""
    if newest.name in _recorded_logs(ledger):
        return ""

    rep = gl.load_report(newest)
    base = gl.load_baseline()
    hits = gl.interesting(rep.entries)
    kinds = {"mod": 0, "noise": 0, "unknown": 0}
    for e in hits:
        kinds[e.classify()[0]] += 1
    rare: list[str] = []
    for e in hits:
        if e.classify()[0] == "unknown":
            sig = e.signature()
            if base.is_rare(sig, 5) and sig not in rare:
                rare.append(sig)

    lines = [
        "🚦 成功率闸（自动）：**检测到一局新日志没进台账** —— 装完起过一局，但没人读它。",
        f"· 最新：{newest.name}",
        f"· 读数：与 mod 相关 {kinds['mod']} · 已知噪声 {kinds['noise']}"
        f" · 未归类 {kinds['unknown']}（★全库罕见 {len(rare)}）",
    ]
    for sig in rare[:MAX_RARE_SHOWN]:
        lines.append(f"  ★ {sig[:110]}   {base.label(sig)}")
    lines += [
        f"下一动作（在 {Path(__file__).resolve().parent.parent.name} 下，先设一次 $V）：",
        "    $V = .venv\\Scripts\\python.exe",
        "    $V tools\\game_log.py --new-only",
        "    $V tools\\acceptance_log.py record --goal \"…\" --entry <入口> --log auto",
        "（口径见 kb/34；⛔ 不算「做过」——只有记进台账才算）",
    ]
    return "\n".join(lines)


def build_status(*, log_dir: Path | None = None, ledger: Path = LEDGER) -> str:
    import acceptance_log as al

    records = al.read_ledger(ledger)
    m = al.metrics(records)
    if log_dir is None:
        log_dir = resolve_log_dir()
    newest = _newest_log(log_dir) if log_dir and log_dir.is_dir() else None
    recorded = _recorded_logs(ledger)
    if HEARTBEAT.is_file():
        beat = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        print(f"心跳：{beat.get('lastCallAt', '?')}（上一次被调用；signal={beat.get('signal')}）")
    print(f"台账：{ledger.relative_to(REPO_ROOT) if ledger.is_relative_to(REPO_ROOT) else ledger}")
    print(f"  记录 {m['n']} 条 · 首次通过率 {al.fmt(m['first_shot_rate'], pct=True)}"
          f" · 硬错误率 {al.fmt(m['hard_error_rate'], pct=True)}"
          f" · 起机 {m['launches']} 次 · 返工 {al.fmt(m['rework_mean'])}")
    if newest is None:
        print("  最新一局：找不到 GameLogs")
    else:
        flag = "已记账" if newest.name in recorded else "★ 未记账"
        print(f"  最新一局：{newest.name}  {flag}")
    return ""


# --------------------------------------------------------------------------- #
# 自检（正控 = 已知该报警；负控 = 已知该安静）
# --------------------------------------------------------------------------- #

_FAKE_LOG_A = (
    "[2026-10-11 09:47:55.584]\n"
    "Error: Could not load the baked VT stack 537f6111,30f2108d\n"
    "UnityEngine.Rendering.RenderPipelineManager:"
    "DoRenderLoop_Internal(RenderPipelineAsset, IntPtr, Object)\n"
    "\n"
    "[2026-10-11 09:47:55.585]\n"
    "Error: Error assigning 2D texture to 2DArray texture property "
    "'unity_Lightmaps': Dimensions must match\n"
)
_FAKE_LOG_B = "[2026-10-11 10:00:00.000]\nLog: nothing wrong here\n"


def selftest() -> int:
    import tempfile

    ok = True
    checks: list[str] = []

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        checks.append(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log_dir = tmp / "GameLogs"
        log_dir.mkdir()
        ledger = tmp / "log.tsv"

        # 正控①：台账空 + 有一局 -> 必须报警，且点名那份日志
        (log_dir / "Gamelog__2026_10_11__09_46.log").write_text(_FAKE_LOG_A, encoding="utf-8")
        out = build_inject(log_dir=log_dir, ledger=ledger)
        named = "Gamelog__2026_10_11__09_46.log" in out
        check("正控① 未记账的一局 -> 报警且点名", bool(out) and named)
        check("正控② 罕见签名被带上", "unity_Lightmaps" in out)
        # 正控③：多一局新的 -> 仍然报警（指向最新那份）
        (log_dir / "Gamelog__2026_10_11__10_00.log").write_text(_FAKE_LOG_B, encoding="utf-8")
        out2 = build_inject(log_dir=log_dir, ledger=ledger)
        check("正控③ 更新的一局 -> 改指最新那份", "10_00" in out2)

        # 负控①：把最新那份记进台账 -> 必须安静
        import acceptance_log as al

        al.append_ledger(
            al.Record(ts="2026-10-11 11:00", goal="x", entry="skin", verdict="first-shot",
                      log_file="Gamelog__2026_10_11__10_00.log"),
            ledger,
        )
        check("负控① 已记账的最新一局 -> 空串", build_inject(log_dir=log_dir, ledger=ledger) == "")
        # 负控②：没有 GameLogs 目录 -> 空串（不崩、不假警）
        check("负控② 没有日志目录 -> 空串", build_inject(log_dir=tmp / "nope", ledger=ledger) == "")
        # 负控③：空目录 -> 空串
        empty = tmp / "GameLogsEmpty"
        empty.mkdir()
        check("负控③ 空日志目录 -> 空串", build_inject(log_dir=empty, ledger=ledger) == "")

    for c in checks:
        print("  " + c)
    n_pos = sum(1 for c in checks if c.startswith("PASS") and "正控" in c)
    n_neg = sum(1 for c in checks if c.startswith("PASS") and "负控" in c)
    print(("OK  " if ok else "FAIL ") + f"正控 {n_pos}/3 报警 · 负控 {n_neg}/3 安静")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="成功率闸（参见 kb/34）")
    ap.add_argument("--inject", action="store_true", help="给插件用：有信号打一块，没信号打空串")
    ap.add_argument("--status", action="store_true", help="给人看的一行读数")
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    log_dir = Path(args.log_dir) if args.log_dir else None
    ledger = Path(args.ledger)
    if args.status:
        build_status(log_dir=log_dir, ledger=ledger)
        return 0
    text = build_inject(log_dir=log_dir, ledger=ledger)
    _beat(
        signal=bool(text),
        chars=len(text),
        logDir=str(resolve_log_dir(log_dir) or ""),
        ledger=str(ledger),
    )
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
