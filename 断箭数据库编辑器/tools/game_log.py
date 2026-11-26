r"""游戏日志的唯一读入口 —— 把「手工 Select-String 1,376 份 Gamelog」变成一条命令。

## 判据一行

    .venv\Scripts\python.exe tools\game_log.py --selftest
      -> 末行印「正控 5/5 报红 · 负控 4/4 干净」且 rc=0

## 为什么要它（第 2 步 · 2026-10-11）

成功率 ≈ ①判据明确 × ②**错了能发现** × ③发现后能归因（见 kb/34）。
②一直是三档验证里最贵的一档。以前只有两条路：人肉 Select-String（本仓手工用过 20+ 次，
证据散在 kb/18 · kb/26 · kb/30 · docs/交接-2026-10-04-W10-完整包.md），
或者「请用户再起一次游戏看对不对」。本工具把前者机械化。

## ★ 尺子的核心不是规则表，是**全库基线**

⛔ **不许靠「我觉得这条像噪声」把行划掉** —— 那是猜。本工具读 --corpus 的全库频次：

    基线 = 这条签名在 1,376 份历史日志里出现过多少份 · 首见是哪天

于是「装进去的东西坏了没坏」变成一个**可复算**的读数：

| 读数 | 含义 |
|---|---|
| 基线 1,320/1,376 · 首见 2026-03-30 | 游戏本来就这样，**不是这次装坏的** |
| 基线 2/1,376 · 首见 2026-10-11 | ★ **新出现的** —— 这才要人看 |
| 显式 mod 规则命中 | 本仓已结案签名，**要处理** |

判据一行（VT 那两条就是这么定案的）：

    $V tools\game_log.py --corpus --rebuild
      -> Error: Could not load the baked VT stack <hex>,<hex> 基线 370/1376 · **首见 2026-03-22**
      -> 早于我们任何 mod 工作 ⇒ 基线噪声，**不是这次装坏的**

## 三类 + 一条注解

| 档 | 含义 |
|---|---|
| **mod** | 本仓已结案的「装进去的东西坏了」签名（每条带出处） |
| **noise** | 显式噪声规则（每条带依据） |
| **unknown** | 剩下的全部 Error/Warning —— **要人或 Agent 看一眼**；每条带全库基线读数 |
| [基线] | 注解：该签名在 ≥10% 的历史日志里出现过（**不是类别**，不许当免检） |

⛔ **unknown 不许自动升级成 noise**：那正是「静默失败」的来源。

## 命令

    $V tools\game_log.py                     # 最新一局
    $V tools\game_log.py --latest 5          # 最近 5 局
    $V tools\game_log.py --since 2026-10-08  # 某个日期之后的所有局
    $V tools\game_log.py --new-only          # 只列「全库罕见」的条目（默认 ≤5 份）
    $V tools\game_log.py --corpus --rebuild  # 全库 1,376 份：签名频次表（约 10 秒）
    $V tools\game_log.py --json
    $V tools\game_log.py --strict            # rc=1 当且仅当有 mod 命中（给闸门用）
    $V tools\game_log.py --selftest

## 边界（写死，别当 bug）

1. **只认硬错误**：游戏自己 Log 出来的。**静默错值**（LOD 序列化名写错之类）游戏不报，
   本工具**看不见** —— 那类仍要真机或用户（kb/19 §3 的第③档）。
2. **归因尽力而为**：栈帧 → ISIL 文件按「类型名」匹配；⛔ 命不中不等于不存在。
3. **异常类签名带「肇事帧」**：NullReferenceException 这类正文恒定，光看正文会把 448 份
   日志的 NRE 全并成一条 ⇒ 一律附上**第一个非框架栈帧**（写法：@ BrokenArrow.Xxx:Method）。
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

ISIL_ROOT = WORK / "isil" / "IsilDump"
CACHE_DIR = WORK / "game-log"
CORPUS_CACHE = CACHE_DIR / "corpus.tsv.gz"


def _display_path(path: Path) -> str:
    """路径 → 可读展示串。**不再假定它一定在仓内**（2026-11-09 `_work` 移出仓后
    `path.relative_to(REPO_ROOT)` 会直接抛 ValueError —— game_log 当时漏改，见下）。

    优先相对**工作区根**（`WORK.parent`，默认 `D:\\断箭模组制作`），其次仓根，
    两者都不是祖先 ⇒ 绝对路径。
    """
    for base in (WORK.parent, REPO_ROOT):
        try:
            return str(path.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
    return str(path)

#: 签名口径的版本号。改了 signature() 就要 +1，缓存会自己发现过期。
SIG_VERSION = 3

#: 一份日志块的头是它自己的时间戳行；块内第一行才是等级行（实测 1,376 份格式一致）。
TS_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*$")
ENTRY_RE = re.compile(r"^(?P<sev>Log|Warning|Error|Assert|Assertion|Exception):\s*(?P<msg>.*)$")
#: 托管栈帧形如 Namespace.Type:Method(args)（实测：分隔符是冒号，不是点）。
FRAME_RE = re.compile(
    r"^(?P<type>[A-Za-z_][A-Za-z0-9_.+<>]*(?:\.[A-Za-z0-9_+<>]+)*)"
    r":(?P<method>[A-Za-z0-9_<>]+)\((?P<args>.*)\)$"
)
HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
NUM_RE = re.compile(r"\d+")
QUOTED_PATH_RE = re.compile(r"'[^']*[\\/][^']*'")
BARE_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/][^\s,;')\]]*")
WS_RE = re.compile(r"\s+")

SEVERITIES = ("Warning", "Error", "Assert", "Assertion", "Exception")

#: 正文恒定的异常类 ⇒ 签名要带上「肇事帧」，否则全库的 NRE 会并成一条。
GENERIC_EXC_RE = re.compile(r"Exception|NullReference|Argument")
#: 这些命名空间下的帧**不算肇事者**（框架 / 引擎 / 第三方）。
FRAMEWORK_PREFIX = (
    "UnityEngine.",
    "Unity.",
    "System.",
    "Cysharp.",
    "Microsoft.",
    "Mono.",
    "Newtonsoft.",
)
#: 这些帧是**打日志的管道**，异常报告里人人都带一条 —— 实测：不排掉它们，
#: 全库 618 份带 NRE 的日志会全部并到 @ Sentry.Unity...:LogException 一条上（判据见 kb/34）。
PLUMBING_RE = re.compile(
    r"^(UnityEngine\.Debug|Sentry\.|BrokenArrow\.Shared\.Ecs\.FilteredLogHandler|BrokenArrow\.LogRelay)"
)

#: 已知噪声。**每条带依据**；自动基线（--corpus）是主力，这里是给最常见的几条配人话理由。
NOISE_RULES: tuple[tuple[str, str], ...] = (
    (r"POST https://api\.brokenarrowgame\.[a-z]+/auth/", "登录被拒（离线 / 未登录）——与 mod 无关"),
    (r"\[AUTH\] Rest: Login failed: Forbidden", "同上：REST 登录失败"),
    (r"GET /monitoring/masterOnline Offline", "官方服务器在线状态——与 mod 无关"),
    (r"DontDestroyOnLoad only works for root GameObjects", "Unity 引擎警告（基线 1,320/1,376）"),
    (r"Waited on RT fence before destroying", "Unity 渲染警告（基线 355/1,376）"),
    (r"Anti-cheat client not available", "离线局不加载 EAC（预期姿势）"),
    (r"\[EOS\] AntiCheat is not active", "同上（基线 897/1,376）"),
    (r"Overlay will not load, because it was explicitly disabled", "EOS Overlay 关闭（预期）"),
    (r"Color primaries .* is unknown or unsupported", "WMF 色彩主色回退（基线 956/1,376）"),
    (r"\[FMOD\] Please add an 'FMOD Studio Listener'", "FMOD 场景提示（基线 669/1,376）"),
    (
        r"Could not load the baked VT stack",
        "VT 烘焙栈缺失 —— 基线 370/1,376 · 首见 2026-03-22（早于全部 mod 工作）",
    ),
    (r"Virtual texturing error Graphine::Granite", "同上，Granite 侧报同一件事（基线 351/1,376）"),
    (r"Not found service of type:", "服务表缺项（基线 374/1,376）"),
    (r"Map does not have a road graph", "地图没有道路图（基线 398/1,376）"),
    (r"Something got wrong when loading road navigation graph", "同上"),
    (r"GET https://api\.steampowered\.com/ISteamNews/", "Steam 新闻拉取失败（基线 482/1,376）"),
    (r"\[REST_PINGER\].*timed out", "官方网关超时（基线 169/1,376）"),
    (r"\[AdaptiveRouting\] Proxy ping", "自适应路由探测（基线 171/1,376）"),
)

#: 已知与「装进去的东西」相关的签名。**每条带出处 + 全库实测**（不许凭印象加）。
#:
#: ⛔ 2026-10-11 实测推翻了其中两条（教训写在 kb/34 §校准）：
#:   * 「referenced script … is missing」全库 **468 份 / 首见 2026-03-13** ⇒ 基线，
#:     当 mod 规则会天天误报；
#:   * 「Couldn't load unit」全库 **33 份 / 首见 2026-07-18**
#:     （Spetsnaz VMF / M1A2 SEP v3 / BMP-2 …）
#:     ⇒ 是游戏自身行为。**判据：命中的单位名是不是我们自己造的**，光看句子分不出来。
#: 两条都已移出本表；它们仍可作为**负控**用（「本次 0 条」是好消息），但不能当阳性判据。
MOD_RULES: tuple[tuple[str, str], ...] = (
    (r"Couldn't load skin", "kb/30:535 —— 皮肤没加载（全库仅 2 份 · 只在 2026-10-08）"),
    (
        r"No UnitPrefabRoot",
        "docs/交接-2026-10-04-W10-完整包.md:134 —— 根 prefab 缺 UnitPrefabRoot（全库 11 份）",
    ),
    (r"Skin data is invalid", "_work/armory/01-结论.md:330 —— 皮肤数据非法（全库 0 份）"),
    (
        r"Animator not found in",
        "全库 3 份 · 首见 2026-10-08 —— 只在自建单位 RouteB_Zero / G7BRoot 上",
    ),
    (
        r"expected .* stacks but serialized material",
        "全库 4 份 · 2026-10-08/09 —— 序列化材质的 VT 栈数与 shader 不符",
    ),
)


def default_log_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import find_game_root

    root = find_game_root()
    if root is None:
        return None
    d = root / "GameLogs"
    return d if d.is_dir() else None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


@dataclass
class Entry:
    """一条日志条目：等级 + 正文 + 块内其余行（HTTP 头 / 托管栈）。"""

    ts: str
    sev: str
    msg: str
    detail: list[str] = field(default_factory=list)
    source: str = ""
    line_no: int = 0

    @property
    def raw(self) -> str:
        return f"{self.sev}: {self.msg}"

    def frames(self) -> list[str]:
        out = []
        for line in self.detail:
            m = FRAME_RE.match(line.strip())
            if m:
                out.append(f"{m.group('type')}:{m.group('method')}")
        return out

    def culprit_frame(self) -> str:
        """第一个**既非框架、也非打日志管道**的栈帧 —— 异常类签名的判别位。"""
        fs = self.frames()
        for fr in fs:
            if fr.startswith(FRAMEWORK_PREFIX) or PLUMBING_RE.match(fr):
                continue
            return fr
        return fs[0] if fs else ""

    def signature(self) -> str:
        """归一化签名：hex / 数字 / 路径 全折叠 ⇒ 跨局可比、可计数。"""
        s = QUOTED_PATH_RE.sub("'<path>'", self.msg)
        s = BARE_PATH_RE.sub("<path>", s)
        s = HEX_RE.sub("<hex>", s)
        s = NUM_RE.sub("<n>", s)
        s = WS_RE.sub(" ", s).strip()
        sig = f"{self.sev}: {s}"[:180]
        if GENERIC_EXC_RE.search(self.msg):
            cf = self.culprit_frame()
            if cf:
                sig += f"  @ {cf}"
        return sig[:240]

    def classify(self) -> tuple[str, str]:
        """-> (kind, reason)：kind ∈ {mod, noise, unknown}。"""
        for pat, why in MOD_RULES:
            if re.search(pat, self.raw):
                return "mod", why
        for pat, why in NOISE_RULES:
            if re.search(pat, self.raw):
                return "noise", why
        return "unknown", ""


def parse_entries(text: str, source: str = "") -> list[Entry]:
    """一份日志 -> 条目列表。块 = 时间戳行 + 其后的所有行（实测格式）。"""
    entries: list[Entry] = []
    cur: Entry | None = None
    for i, line in enumerate(text.splitlines(), start=1):
        m = TS_RE.match(line)
        if m:
            cur = Entry(ts=m.group("ts"), sev="", msg="", source=source, line_no=i)
            entries.append(cur)
            continue
        if cur is None or not line.strip():
            continue
        if not cur.sev:
            em = ENTRY_RE.match(line)
            if em:
                cur.sev = em.group("sev")
                cur.msg = em.group("msg").strip()
            else:
                cur.detail.append(line)
            continue
        cur.detail.append(line)
    return [e for e in entries if e.sev]


# --------------------------------------------------------------------------- #
# 归因：栈帧 -> ISIL
# --------------------------------------------------------------------------- #


def _isil_index() -> dict[str, list[str]]:
    """类型短名（小写）-> ISIL 文件相对路径。一次目录遍历，进程内复用。"""
    idx: dict[str, list[str]] = {}
    if not ISIL_ROOT.is_dir():
        return idx
    for p in ISIL_ROOT.rglob("*.txt"):
        idx.setdefault(p.stem.lower(), []).append(_display_path(p))
    return idx


_ISIL: dict[str, list[str]] | None = None


def isil_paths(frame_type: str) -> list[str]:
    global _ISIL
    if _ISIL is None:
        _ISIL = _isil_index()
    full = frame_type.split(":", maxsplit=1)[0]
    nested = re.search(r"^(?P<parent>.*)\.<(?P<inner>[^>]+)>d__(?P<num>\d+)$", full)
    if nested:
        outer = nested.group("parent").rsplit(".", maxsplit=1)[-1]
        key = f"{outer}_NestedType__{nested.group('inner')}_d__{nested.group('num')}"
    else:
        key = full.rsplit(".", maxsplit=1)[-1]
    return _ISIL.get(key.lower(), [])[:3]


# --------------------------------------------------------------------------- #
# 全库基线
# --------------------------------------------------------------------------- #


@dataclass
class Baseline:
    """全库基线表：这条签名在历史日志里出现过多少份 / 首见哪天。"""

    n_logs: int = 0
    rows: dict[str, dict] = field(default_factory=dict)

    def get(self, sig: str) -> dict | None:
        return self.rows.get(sig)

    def label(self, sig: str, *, threshold_pct: float = 10.0) -> str:
        row = self.get(sig)
        if row is None:
            return f"基线 0/{self.n_logs} · ★ 全库未见过"
        pct = 100.0 * row["logs"] / max(self.n_logs, 1)
        tag = " [基线]" if pct >= threshold_pct else " ★罕见"
        return f"基线 {row['logs']}/{self.n_logs} · 首见 {row['first']}{tag}"

    def is_rare(self, sig: str, max_logs: int) -> bool:
        row = self.get(sig)
        return row is None or row["logs"] <= max_logs


def load_baseline() -> Baseline:
    if not CORPUS_CACHE.is_file():
        return Baseline()
    with gzip.open(CORPUS_CACHE, "rt", encoding="utf-8") as fh:
        raw_rows = [json.loads(line) for line in fh if line.strip()]
    meta = raw_rows[0] if raw_rows and raw_rows[0].get("__meta__") else {}
    if meta and meta.get("sig_version") != SIG_VERSION:
        print(
            f"警告：基线表是旧口径（v{meta.get('sig_version')} != v{SIG_VERSION}）——"
            "请跑 tools/game_log.py --corpus --rebuild",
            file=sys.stderr,
        )
    rows = {row["sig"]: row for row in raw_rows if not row.get("__meta__")}
    return Baseline(n_logs=meta.get("n_logs", 0), rows=rows)


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #


def _day_of(path: Path) -> str:
    m = re.search(r"(\d{4})_(\d{2})_(\d{2})", path.name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _iter_logs(log_dir: Path, since: str = "", until: str = "") -> list[Path]:
    out = []
    for p in sorted(log_dir.glob("*.log")):
        day = _day_of(p)
        if since and day and day < since:
            continue
        if until and day and day > until:
            continue
        out.append(p)
    return out


@dataclass
class Report:
    path: Path
    entries: list[Entry]

    def counts(self) -> dict[str, int]:
        """只数 Error/Warning 档（⛔ 别把 Log 行算进来）。"""
        c = {"mod": 0, "noise": 0, "unknown": 0}
        for e in interesting(self.entries):
            c[e.classify()[0]] += 1
        return c


def load_report(path: Path) -> Report:
    text = path.read_text(encoding="utf-8", errors="replace")
    return Report(path=path, entries=parse_entries(text, source=path.name))


def interesting(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if e.sev in SEVERITIES]


def _group(hits: list[Entry], kind: str) -> dict[str, list[Entry]]:
    """同一条签名在该局里重复 N 次 -> 只占一行，标 ×N（⛔ 不丢读数：N 就是次数）。"""
    out: dict[str, list[Entry]] = {}
    for e in hits:
        if e.classify()[0] == kind:
            out.setdefault(e.signature(), []).append(e)
    return out


def print_report(
    rep: Report,
    *,
    baseline: Baseline | None = None,
    show_unknown: int = 10,
    show_isil: bool = True,
    new_only_max: int = 0,
) -> None:
    hits = interesting(rep.entries)
    c = rep.counts()
    size_kb = rep.path.stat().st_size / 1024 if rep.path.is_file() else 0.0
    print(f"游戏日志：{rep.path.name}（{size_kb:.1f} KB）")
    print(
        f"  Error/Warning 条目 {len(hits)} 条："
        f"与 mod 相关 {c['mod']} · 已知噪声 {c['noise']} · 未归类 {c['unknown']}"
    )
    if c["mod"]:
        print("\n★ 与 mod 相关（要处理）：")
        for sig, group in _group(hits, "mod").items():
            head = f"  [{group[0].ts}]" + (f" ×{len(group)}" if len(group) > 1 else "")
            print(f"{head} {sig[:150]}")
            print(f"      依据：{group[0].classify()[1]}")
    unknown = [e for e in hits if e.classify()[0] == "unknown"]
    if new_only_max and baseline is not None:
        unknown = [e for e in unknown if baseline.is_rare(e.signature(), new_only_max)]
    if unknown:
        groups = _group(unknown, "unknown")
        print(f"\n未归类 {len(groups)} 类 / {len(unknown)} 条（⛔ 不许默认当噪声，要人看）：")
        for sig, group in list(groups.items())[:show_unknown]:
            ts = group[0].ts + (f" ×{len(group)}" if len(group) > 1 else "")
            tail = f"  {baseline.label(sig)}" if baseline is not None else ""
            print(f"  [{ts}] {sig[:150]}{tail}")
            for fr in group[0].frames()[:3]:
                line = f"      栈：{fr}"
                if show_isil:
                    paths = isil_paths(fr)
                    line += f"  → ISIL: {paths[0]}" if paths else "  → ISIL: （未命中）"
                print(line)
        if len(groups) > show_unknown:
            print(f"  …另 {len(groups) - show_unknown} 类（--json 看全部）")


# --------------------------------------------------------------------------- #
# 全库语料（基线表的原料）
# --------------------------------------------------------------------------- #


def corpus_scan(log_dir: Path, since: str = "") -> list[dict]:
    """全库扫一遍：每个签名 -> 出现行数 / 命中日志份数 / 首末日期。"""
    paths = _iter_logs(log_dir, since=since)
    stats: dict[str, dict] = {}
    for p in paths:
        day = _day_of(p) or "?"
        rep = load_report(p)
        seen: set[str] = set()
        for e in interesting(rep.entries):
            sig = e.signature()
            kind, _why = e.classify()
            st = stats.setdefault(
                sig,
                {"sig": sig, "lines": 0, "logs": 0, "first": day, "last": day, "kind": kind},
            )
            st["lines"] += 1
            if sig not in seen:
                seen.add(sig)
                st["logs"] += 1
            st["first"] = min(st["first"], day)
            st["last"] = max(st["last"], day)
            if st["kind"] == "unknown" and kind != "unknown":
                st["kind"] = kind
    rows = sorted(stats.values(), key=lambda d: (-d["logs"], -d["lines"]))
    meta = {
        "__meta__": True,
        "sig_version": SIG_VERSION,
        "n_logs": len(paths),
        "n_sigs": len(rows),
        "since": since,
    }
    return [meta] + rows


# --------------------------------------------------------------------------- #
# 自检（正控 = 已知阳性必须报红；负控 = 已知阴性必须干净）
# --------------------------------------------------------------------------- #

_FIXTURE_MOD = (
    "[2026-10-08 19:00:00.000]\n"
    "Error: Couldn't load skin id: 1 for unit T-72B1\n"
    "BrokenArrow.Client.Ecs.Skin.SkinService:LoadSkin(Int32, String)\n"
)
_FIXTURE_NOISE = (
    "[2026-10-11 09:47:12.592]\n"
    "Error: POST https://api.brokenarrowgame.store/auth/v2/steam, "
    "ResponseCode: 403, Status: Forbidden, DownloadedBytes: 175, Elapsed: 1206 ms\n"
    "BrokenArrow.Shared.Ecs.FilteredLogHandler:LogFormat(LogType, Object, String, Object[])\n"
    "\n"
    "[2026-10-11 09:47:37.582]\n"
    "Warning: GET /monitoring/masterOnline Offline\n"
    "\n"
    "[2026-10-11 09:47:11.076]\n"
    "Warning: DontDestroyOnLoad only works for root GameObjects "
    "or components on root GameObjects.\n"
    "\n"
    "[2026-10-11 09:47:55.584]\n"
    "Warning: Waited on RT fence before destroying. "
    "(Could indicate a performance problem if logged every frame).\n"
)
_FIXTURE_CLEAN = (
    "[2026-10-11 09:47:11.060]\n"
    "Log: BrokenArrow startup info:\n"
    "Version: 1.2.0.3\n"
    "\n"
    "[2026-10-11 09:47:11.061]\n"
    "Log: [service] reg service SettingsService.\n"
)
_FIXTURE_NRE_A = (
    "[2026-10-02 15:18:00.000]\n"
    "Exception: NullReferenceException: Object reference not set to an instance of an object.\n"
    "UnityEngine.Debug:LogException(Exception)\n"
    "BrokenArrow.Client.Ecs.Database.LoadUnits:LoadMobility(Units)\n"
)
_FIXTURE_NRE_B = (
    "[2026-10-02 15:19:00.000]\n"
    "Exception: NullReferenceException: Object reference not set to an instance of an object.\n"
    "UnityEngine.Debug:LogException(Exception)\n"
    "BrokenArrow.Client.Ecs.UI.CardPanel:ApplyFilter()\n"
)


def selftest() -> int:
    ok = True
    checks: list[str] = []

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        checks.append(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    mod_rep = Report(Path("<fixture-mod>"), parse_entries(_FIXTURE_MOD))
    noise_rep = Report(Path("<fixture-noise>"), parse_entries(_FIXTURE_NOISE))
    clean_rep = Report(Path("<fixture-clean>"), parse_entries(_FIXTURE_CLEAN))

    kinds = [e.classify()[0] for e in interesting(mod_rep.entries)]
    check("正控① 已知 mod 签名判 mod", kinds == ["mod"])
    a = parse_entries(_FIXTURE_NRE_A)[0]
    b = parse_entries(_FIXTURE_NRE_B)[0]
    check("正控② 两条 NRE 肇事帧不同 ⇒ 签名不同（不并成一条）", a.signature() != b.signature())
    check("正控③ 肇事帧取到 BrokenArrow 那一帧", a.culprit_frame().startswith("BrokenArrow."))
    check("正控④ 托管栈帧认出 ≥1 条", len(mod_rep.entries[0].frames()) >= 1)

    bad = [
        (e.raw[:40], e.classify()[0])
        for e in interesting(noise_rep.entries)
        if e.classify()[0] != "noise"
    ]
    check("负控① 4 条已知噪声全部判 noise", not bad)
    check("负控② 干净日志 0 条 Error/Warning", not interesting(clean_rep.entries))
    kept = any("Version: 1.2.0.3" in d for d in clean_rep.entries[0].detail)
    check("负控③ 多行正文块被完整保留", kept)
    fresh = parse_entries(_FIXTURE_NRE_A)[0].classify()[0]
    check("负控④ 全新异常判 unknown（不自动降级成 noise）", fresh == "unknown")

    #: ★ 2026-11-14 补的回归：`_work` 2026-11-09 移出仓后，ISIL 展示路径还在按仓根
    #: `relative_to(REPO_ROOT)` 算 ⇒ 一进归因就 ValueError（--new-only 直接 traceback）。
    isil_show = _display_path(ISIL_ROOT / "Assembly-CSharp" / "ExampleType.txt").replace("\\", "/")
    check(
        "正控⑤ 展示路径不再假定在仓内（数据移出仓那次回归）",
        isil_show.endswith("isil/IsilDump/Assembly-CSharp/ExampleType.txt"),
    )

    for c in checks:
        print("  " + c)
    n_pos = sum(1 for c in checks if c.startswith("PASS") and "正控" in c)
    n_neg = sum(1 for c in checks if c.startswith("PASS") and "负控" in c)
    total_pos = sum(1 for c in checks if "正控" in c)
    total_neg = sum(1 for c in checks if "负控" in c)
    print(
        ("OK  " if ok else "FAIL ")
        + f"正控 {n_pos}/{total_pos} 报红 · 负控 {n_neg}/{total_neg} 干净"
    )
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="游戏日志的唯一读入口（参见 kb/34）")
    ap.add_argument("--latest", type=int, default=1, help="读最新 N 局（默认 1）")
    ap.add_argument("--file", help="指定一份日志")
    ap.add_argument("--since", default="", help="只读该日期（YYYY-MM-DD）之后的局")
    ap.add_argument("--until", default="", help="只读该日期之前的局")
    ap.add_argument("--corpus", action="store_true", help="全库签名频次表（基线表的原料）")
    ap.add_argument("--rebuild", action="store_true", help="--corpus 强制重扫（不吃缓存）")
    ap.add_argument(
        "--new-only", nargs="?", const=5, type=int, default=0, help="只列基线份数 ≤N 的条目"
    )
    ap.add_argument("--show", type=int, default=10, help="未归类最多列几条（默认 10）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--strict", action="store_true", help="rc=1 当且仅当有 mod 命中")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-isil", action="store_true", help="不做栈帧归因")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    log_dir = default_log_dir()
    if log_dir is None:
        print("找不到游戏目录（可用 BA_MOD_MAKER_GAME 指定）", file=sys.stderr)
        return 2

    if args.corpus:
        if CORPUS_CACHE.is_file() and not args.rebuild:
            base = load_baseline()
            rows = [{"__meta__": True, "n_logs": base.n_logs, "n_sigs": len(base.rows)}] + list(
                base.rows.values()
            )
            print(f"（缓存 {_display_path(CORPUS_CACHE)}；--rebuild 重扫）")
        else:
            rows = corpus_scan(log_dir, since=args.since)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with gzip.open(CORPUS_CACHE, "wt", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"（本次全扫 {len(_iter_logs(log_dir, since=args.since))} 份 · 约 10 秒）")
        meta, body = rows[0], rows[1:]
        if args.json:
            print(json.dumps(body, ensure_ascii=False, indent=1))
            return 0
        print(f"全库 {meta['n_logs']} 份日志 · {meta['n_sigs']} 个签名")
        print(f"{'份数':>5} {'行数':>6}  {'首':<10} {'末':<10} 签名")
        for row in body[:40]:
            head = f"{row['logs']:>5} {row['lines']:>6}  {row['first']:<10} {row['last']:<10} "
            print(head + row["sig"][:100])
        return 0

    paths = (
        [Path(args.file)]
        if args.file
        else _iter_logs(log_dir, since=args.since, until=args.until)
    )
    newest = paths if args.file else paths[-args.latest :]
    if not newest:
        print("没有匹配的日志", file=sys.stderr)
        return 2

    baseline = load_baseline()
    reports = [load_report(p) for p in newest]
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "file": r.path.name,
                        "entries": [
                            {
                                "ts": e.ts,
                                "sev": e.sev,
                                "sig": e.signature(),
                                "kind": e.classify()[0],
                                "reason": e.classify()[1],
                                "baseline": baseline.get(e.signature()),
                                "frames": e.frames(),
                            }
                            for e in interesting(r.entries)
                        ],
                    }
                    for r in reports
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for r in reports:
            print_report(
                r,
                baseline=baseline,
                show_unknown=args.show,
                show_isil=not args.no_isil,
                new_only_max=args.new_only,
            )
            print()

    mod_hits = sum(r.counts()["mod"] for r in reports)
    return 1 if (args.strict and mod_hits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
