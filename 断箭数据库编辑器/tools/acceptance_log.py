r"""Agent 造包的**验收台账** —— 成功率的第一本账（第 1 步 · 2026-10-11）。

## 判据一行

    .venv\Scripts\python.exe tools\acceptance_log.py --selftest
      -> 末行印「正控 4/4 命中 · 负控 3/3 干净」且 rc=0

## 为什么要有账本

kb/34 的口径：成功率 ≈ ①判据明确 × ②错了能发现 × ③发现后能归因。
②③现在有 tools/game_log.py 这把尺子了，但**没有账本就无法证明改进** ——
「这次比上次好」必须是一个可复算的数字，不是印象。

**四个指标**（都在 stats 里印出公式，全部从本账 + 现有产物算，不需要额外起游戏）：

| # | 指标 | 公式 |
|---|---|---|
| 1 | **首次通过率** | verdict=first-shot 的记录数 ÷ 有 verdict 的记录数 |
| 2 | **硬错误率** | 读过日志、且与 mod 相关命中 >0 的记录数 ÷ 读过日志的记录数 |
| 3 | **真机挡位占用** | launched=true 的记录数（＋ --with-pending 时附待真机清单条目数） |
| 4 | **返工轮数** | rework 的 mean / max |

⛔ **空账本不许报 100%**：没有分母时一律印「—（无记录）」（负控①就是这个）。

## 命令

    # 记一笔（--log auto 会自动跑 game_log 读最新一局并落签名）
    $V tools\acceptance_log.py record --goal "T-72B1 换涂装" --entry skin --artifact <包> --log auto

    $V tools\acceptance_log.py list
    $V tools\acceptance_log.py stats --with-pending
    $V tools\acceptance_log.py promote          # ≥2 次的坑 -> 该不该晋升成规则 / 知识页
    $V tools\acceptance_log.py --selftest

## 晋升判据（第 3 步）

**同一个签名在 ≥2 条记录里出现过 ⇒ 必须晋升**：进 core/rules/rulebook.json 一条规则，
或进 kb/ 一页。promote 会机械地查一遍「这个签名是不是已经在 rulebook / kb 里被提过」，
把它标成 已提及 / 待晋升。

⛔ 只写进文档不算解决 —— 文档解决「知道」，规则解决「做错」。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

LEDGER = WORK / "acceptance" / "log.tsv"
RULEBOOK = REPO_ROOT / "src" / "ba_mod_maker" / "core" / "rules" / "rulebook.json"
PENDING_DOC = REPO_ROOT / "docs" / "待真机验证清单.md"

COLUMNS = (
    "ts",
    "goal",
    "entry",
    "artifact",
    "sha8",
    "preflight",
    "verdict",
    "rework",
    "launched",
    "log_file",
    "mod_hits",
    "rare_hits",
    "sigs",
    "notes",
)

VERDICTS = ("first-shot", "rework", "failed", "unknown")


@dataclass
class Record:
    ts: str = ""
    goal: str = ""
    entry: str = ""
    artifact: str = ""
    sha8: str = ""
    preflight: str = ""
    verdict: str = "unknown"
    rework: int = 0
    launched: bool = False
    log_file: str = ""
    mod_hits: int = 0
    rare_hits: int = 0
    sigs: list[str] = field(default_factory=list)
    notes: str = ""

    def to_tsv(self) -> str:
        cells = [
            self.ts,
            self.goal,
            self.entry,
            self.artifact,
            self.sha8,
            self.preflight,
            self.verdict,
            str(self.rework),
            "1" if self.launched else "0",
            self.log_file,
            str(self.mod_hits),
            str(self.rare_hits),
            "|".join(self.sigs),
            self.notes,
        ]
        return "\t".join(c.replace("\t", " ").replace("\n", " ") for c in cells)

    @classmethod
    def from_tsv(cls, line: str) -> Record:
        cells = line.rstrip("\n").split("\t")
        cells += [""] * (len(COLUMNS) - len(cells))
        d = dict(zip(COLUMNS, cells, strict=False))
        return cls(
            ts=d["ts"],
            goal=d["goal"],
            entry=d["entry"],
            artifact=d["artifact"],
            sha8=d["sha8"],
            preflight=d["preflight"],
            verdict=d["verdict"] or "unknown",
            rework=int(d["rework"] or 0),
            launched=d["launched"] == "1",
            log_file=d["log_file"],
            mod_hits=int(d["mod_hits"] or 0),
            rare_hits=int(d["rare_hits"] or 0),
            sigs=[s for s in d["sigs"].split("|") if s],
            notes=d["notes"],
        )


def read_ledger(path: Path = LEDGER) -> list[Record]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Record.from_tsv(x) for x in lines[1:] if x.strip()]


def append_ledger(rec: Record, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text("\t".join(COLUMNS) + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(rec.to_tsv() + "\n")


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #


def metrics(records: list[Record]) -> dict:
    graded = [r for r in records if r.verdict in ("first-shot", "rework", "failed")]
    logged = [r for r in records if r.log_file]
    first = sum(1 for r in graded if r.verdict == "first-shot")
    bad = sum(1 for r in logged if r.mod_hits > 0)
    reworks = [r.rework for r in records]
    return {
        "n": len(records),
        "n_graded": len(graded),
        "n_logged": len(logged),
        "first_shot_rate": (first / len(graded)) if graded else None,
        "hard_error_rate": (bad / len(logged)) if logged else None,
        "launches": sum(1 for r in records if r.launched),
        "rework_mean": (sum(reworks) / len(reworks)) if reworks else None,
        "rework_max": max(reworks) if reworks else None,
    }


def pending_count() -> int | None:
    if not PENDING_DOC.is_file():
        return None
    text = PENDING_DOC.read_text(encoding="utf-8")
    # 模板那一行（### <编号> · <一句话症状>）不算条目
    return sum(
        1 for ln in text.splitlines() if ln.startswith("### ") and "<编号>" not in ln
    )


def fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "—（无记录）"
    return f"{value * 100:.1f}%" if pct else f"{value:.2f}"


def _show(path: Path) -> str:
    """台账路径的显示形式。

    2026-11-09 起工作根移出仓（D:\\断箭模组制作\\_work_db）⇒ relative_to(REPO_ROOT) 会抛
    ValueError；仓内路径照旧显示相对路径，仓外路径显示绝对路径。
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def print_stats(records: list[Record], *, with_pending: bool = False) -> None:
    m = metrics(records)
    print(f"台账：{_show(LEDGER)}  ·  {m['n']} 条记录")
    print()
    print("① 首次通过率  = first-shot 记录数 ÷ 有 verdict 的记录数")
    print(f"   = {fmt(m['first_shot_rate'], pct=True)}   （分母 {m['n_graded']} 条有 verdict）")
    print("② 硬错误率    = 读过日志且 mod 命中 >0 的记录数 ÷ 读过日志的记录数")
    print(f"   = {fmt(m['hard_error_rate'], pct=True)}   （分母 {m['n_logged']} 条读过日志）")
    print("③ 真机挡位占用 = launched=true 的记录数")
    tail = f"   = {m['launches']} 次起游戏"
    if with_pending:
        n = pending_count()
        tail += f"；另 docs/待真机验证清单.md 挂着 {n if n is not None else '—'} 条待真机"
    print(tail)
    print("④ 返工轮数    = rework 的 mean / max")
    print(f"   = {fmt(m['rework_mean'])} / {fmt(m['rework_max'])}")


# --------------------------------------------------------------------------- #
# 晋升
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{3,}")
_PLACEHOLDER = ("<n>", "<hex>", "<path>")


def _keywords(sig: str) -> list[str]:
    msg = sig.split(":", maxsplit=1)[-1]
    for ph in _PLACEHOLDER:
        msg = msg.replace(ph, " ")
    toks = [t for t in _TOKEN_RE.findall(msg) if not t.startswith(("http", "https"))]
    return toks[:4]


def _promotion_evidence(sig: str) -> str:
    """机械查一遍「这个签名在 rulebook / kb 里被提过吗」—— 粗筛，可能漏报。"""
    toks = _keywords(sig)
    if len(toks) < 2:
        return "（关键词太少，跳过）"
    corpus: list[tuple[str, str]] = []
    if RULEBOOK.is_file():
        corpus.append(("rulebook.json", RULEBOOK.read_text(encoding="utf-8")))
    for p in sorted((REPO_ROOT / "kb").glob("*.md")):
        corpus.append((p.name, p.read_text(encoding="utf-8", errors="replace")))
    for name, text in corpus:
        if all(t in text for t in toks[:2]):
            return f"已提及：{name}"
    return "★ 待晋升（rulebook 与 kb/ 都没提过）"


def print_promote(records: list[Record], *, min_hits: int = 2) -> None:
    counts: dict[str, int] = {}
    for r in records:
        # ⛔ 去重到「记录」粒度：同一条日志里同一句出现 6 次，只算这一条记录一次
        for sig in set(r.sigs):
            counts[sig] = counts.get(sig, 0) + 1
    hits = sorted(((n, s) for s, n in counts.items() if n >= min_hits), reverse=True)
    print(f"在 ≥{min_hits} 条记录里出现过的签名：{len(hits)} 个")
    if not hits:
        print("（没有 —— 要么还没记够，要么真的没重复踩坑）")
        return
    for n, sig in hits:
        print(f"  ×{n}  {sig[:110]}")
        print(f"       {_promotion_evidence(sig)}")


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #


def selftest() -> int:
    ok = True
    checks: list[str] = []

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        checks.append(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    fixtures = [
        Record(
            ts="2026-10-11 01:00",
            goal="A",
            entry="skin",
            verdict="first-shot",
            rework=0,
            launched=True,
            log_file="a.log",
            mod_hits=0,
            sigs=["Error: x"],
        ),
        Record(
            ts="2026-10-11 02:00",
            goal="B",
            entry="newasset",
            verdict="rework",
            rework=2,
            launched=True,
            log_file="b.log",
            mod_hits=1,
            sigs=["Error: y", "Error: x"],
        ),
        Record(
            ts="2026-10-11 03:00",
            goal="C",
            entry="assets",
            verdict="failed",
            rework=2,
            launched=False,
            preflight="FAIL",
        ),
    ]
    m = metrics(fixtures)
    check("正控① 首次通过率 = 1/3", abs((m["first_shot_rate"] or 0) - 1 / 3) < 1e-9)
    check("正控② 硬错误率 = 1/2（只数读过日志的）", abs((m["hard_error_rate"] or 0) - 0.5) < 1e-9)
    check("正控③ 真机占用 = 2 次", m["launches"] == 2)
    check("正控④ 返工 mean = 4/3 · max = 2", abs((m["rework_mean"] or 0) - 4 / 3) < 1e-9)

    empty = metrics([])
    check("负控① 空台账的比率是 None，不是 0 / 100%", empty["first_shot_rate"] is None)
    check("负控② 空台账印 —（无记录）", fmt(empty["hard_error_rate"]) == "—（无记录）")
    line = fixtures[1].to_tsv()
    back = Record.from_tsv(line)
    round_trip = back.sigs == ["Error: y", "Error: x"] and back.rework == 2
    check("负控③ TSV 往返不丢字段（sigs 两条 · rework 2）", round_trip)

    for c in checks:
        print("  " + c)
    n_pos = sum(1 for c in checks if c.startswith("PASS") and "正控" in c)
    n_neg = sum(1 for c in checks if c.startswith("PASS") and "负控" in c)
    print(("OK  " if ok else "FAIL ") + f"正控 {n_pos}/4 命中 · 负控 {n_neg}/3 干净")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _log_facts(path: str) -> tuple[str, int, list[str], int]:
    """跑 game_log 读一局 -> (文件名, mod 命中数, 罕见签名, 罕见数)。"""
    import game_log as gl

    log_dir = gl.default_log_dir()
    if log_dir is None:
        return "", 0, [], 0
    target = Path(path) if path and path != "auto" else sorted(log_dir.glob("*.log"))[-1]
    rep = gl.load_report(target)
    base = gl.load_baseline()
    mods = [e for e in gl.interesting(rep.entries) if e.classify()[0] == "mod"]
    rare = [
        e.signature()
        for e in gl.interesting(rep.entries)
        if e.classify()[0] == "unknown" and base.is_rare(e.signature(), 5)
    ]
    sigs = [e.signature() for e in mods] + rare
    return target.name, len(mods), sigs, len(rare)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent 造包的验收台账（参见 kb/34）")
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--selftest", action="store_true", help="自检（与 selftest 子命令等价）")
    sub = ap.add_subparsers(dest="cmd")

    p_rec = sub.add_parser("record", help="记一笔")
    p_rec.add_argument("--goal", required=True)
    p_rec.add_argument(
        "--entry", default="", help="走哪个产品入口：skin / newasset / patch / assets / vt ..."
    )
    p_rec.add_argument("--artifact", default="", help="产物路径（自动算 sha256 前 8 位）")
    p_rec.add_argument("--preflight", default="", help="机械尺子读数，如 PASS / FAIL / SKIP")
    p_rec.add_argument("--verdict", default="unknown", choices=VERDICTS)
    p_rec.add_argument("--rework", type=int, default=0)
    p_rec.add_argument("--launched", action="store_true", help="这一轮真的起过游戏")
    p_rec.add_argument("--log", default="none", help="auto = 读最新一局；none = 不读；或给路径")
    p_rec.add_argument("--notes", default="")
    p_rec.add_argument("--ts", default="", help="记录时间（回填历史取证用；默认现在）")

    sub.add_parser("list", help="列全部记录")
    p_st = sub.add_parser("stats", help="四个指标")
    p_st.add_argument("--with-pending", action="store_true", help="附待真机清单条目数")
    p_pr = sub.add_parser("promote", help="≥N 次的坑 -> 晋升候选")
    p_pr.add_argument("--min", type=int, default=2)
    sub.add_parser("selftest")

    args = ap.parse_args(argv)
    if args.selftest or args.cmd == "selftest":
        return selftest()
    ledger = Path(args.ledger)

    if args.cmd == "record":
        sha8 = ""
        if args.artifact:
            p = Path(args.artifact)
            if p.is_file():
                sha8 = hashlib.sha256(p.read_bytes()).hexdigest()[:8]
        log_file, mod_hits, sigs, rare_hits = "", 0, [], 0
        if args.log != "none":
            log_file, mod_hits, sigs, rare_hits = _log_facts(args.log)
        rec = Record(
            ts=args.ts or datetime.now().strftime("%Y-%m-%d %H:%M"),
            goal=args.goal,
            entry=args.entry,
            artifact=args.artifact,
            sha8=sha8,
            preflight=args.preflight,
            verdict=args.verdict,
            rework=args.rework,
            launched=args.launched,
            log_file=log_file,
            mod_hits=mod_hits,
            rare_hits=rare_hits,
            sigs=sigs,
            notes=args.notes,
        )
        append_ledger(rec, ledger)
        print(f"已记账：{_show(ledger)}")
        print(
            f"  {rec.ts} | {rec.goal} | 入口 {rec.entry or '—'} | "
            f"{rec.verdict} | 返工 {rec.rework}"
        )
        if rec.log_file:
            print(f"  日志 {rec.log_file}：mod 命中 {rec.mod_hits} · 罕见签名 {rec.rare_hits}")
        return 0

    records = read_ledger(ledger)
    if args.cmd == "stats":
        print_stats(records, with_pending=args.with_pending)
        return 0
    if args.cmd == "promote":
        print_promote(records, min_hits=args.min)
        return 0
    if args.cmd == "list":
        if not records:
            print("（台账是空的）")
            return 0
        for r in records:
            flag = "★" if r.mod_hits else " "
            launched = "是" if r.launched else "否"
            print(
                f"{flag} {r.ts} | {r.verdict:<10} | 返工 {r.rework} | 起机 {launched}"
                f" | {r.goal} [{r.entry}]"
            )
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
