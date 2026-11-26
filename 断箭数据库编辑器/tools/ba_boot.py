r"""tools/ba_boot.py —— 断箭开发模式 · 开工包（v3：现场 + 五条硬路由 + **产线入口路由**
+ 工具箱现场）。

★ v3（2026-11-03）：产品侧的资产线（提取器 → 造包 → 渲染闸 → 登记 catalog）已经跑起来，
但有三个「新会话看不见」的缺口，本版补上：
    · §2.5b **产线入口路由**：每类活（提取 / 造包 / 登记地址 / 数据表 / 汉化 / 工具维护 / 崩溃）
      给唯一入口的绝对命令与判据 —— 光有「五条硬路由」不够，模型仍不知道「这活走哪个工具」；
    · §2.6 工具箱改成「**分组活体检查**」：跨会话尺子 / 检修 / 产品入口 / 取证各一行，
      + 指向「自有工具总账」（`工具总账.md` / `tools/tool_catalog.py`）与外部环境清单
      （`tool_inventory.py`）；
    · §0 现场段兼容**新代 STATUS 格式**（`## ✅ …`）。旧版只认 `> ##`，实测 2026-11-03 起已经抓空
      （报「没有现场段」但其实顶部有一大段）。

用法::

    .venv\Scripts\python.exe tools\ba_boot.py            # markdown 开工包（给人 / 给 ba_boot 工具）
    .venv\Scripts\python.exe tools\ba_boot.py --json     # {"ok": bool, "report": str}
    .venv\Scripts\python.exe tools\ba_boot.py --selftest # 自检（负控/正控，必须能红）

v2 加的三块（用户投诉：「给的下一条命令是散文、且部分路径过期，模型照着做撞空」）：
    · 2.5 五条硬路由（机械版）：每条 = 触发条件 + **绝对路径命令** + 判据一行；
    · 2.6 本模式工具箱现场：逐条 stat ✅/❌ + 自动挑当前**权威 ISIL 根** + 插件心跳；
    · 3   NEXT_STEPS 重写成五条硬路由的压缩版（保留红线那一段）。
    老的四块（STATUS 顶部 / 接手体检摘要 / 成功率台账 / NEXT_STEPS）全部保留。

为什么有这个脚本（AGENTS §0.14「有功能但不用 = 没有」）：
    本工作区积累的知识/工具太多，开工时「顾此失彼」的根因是**入口太多**而不是缺文档。
    这个脚本把「开工必读的现场」压成一条命令；DSH 插件 `dsh-ba-mode` 的 `ba_boot` 工具
    只是**薄壳**，判定与拼装全在这里（逻辑只有一份，照 kb-lookup 的规矩）。

三条硬约束：
    ① **只读**：只 stat/读文本、跑只读体检命令；不写盘、不起游戏、不跑 pytest；
    ② 输出 **< 7800 字符**（DSH 工具结果修剪器阈值 8192，超了会被砍头砍尾）；
    ③ 每条子命令都限时 + 任何一块跑不动只降级不抛：体检跑不动也要照常给出其余块。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PROD = TOOLS.parent
ROOT = PROD.parent

#: 工具结果修剪器阈值 8192 —— 留 ~400 字符余量。
MAX_CHARS = 7800
STATUS_MAX_LINES = 26
#: STATUS 现场块再压一道**字符**上限：顶部 h1 块可能很长（实测一轮交接 20+ 行），
#: 不压会把 2.5/2.6 块挤出 7800 上限（2026-10-25 修 STATUS 提取时当场量到 7771）。
STATUS_MAX_CHARS = 2000
HANDOFF_TIMEOUT_S = 180.0
SUCCESS_TIMEOUT_S = 60.0

#: 打印「绝对命令」用的 python：优先仓内 venv；不存在才退回当前解释器。
PYTHON = PROD / ".venv" / "Scripts" / "python.exe"
PY = str(PYTHON) if PYTHON.exists() else sys.executable

BA_GATE = TOOLS / "ba_gate.py"
DECOMP_INDEX = TOOLS / "decomp_index.py"
FIND_CARD_ROW = TOOLS / "find_card_row.py"
KB_LOOKUP = TOOLS / "kb_lookup.py"
UNITY_RENDER = ROOT / "tools" / "unity_render.py"
TOOL_INVENTORY = ROOT / "tools" / "tool_inventory.py"
UNITY_EDITOR = Path(r"D:\Unity\6000.3.24f1")
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
HDRP17 = WORK / "hdrp17"
DECOMP_META = WORK / "decomp-index" / "meta.json"
HEARTBEAT = WORK / "ba-mode-heartbeat.json"

#: ★ v3 新增：产品线入口 + 自有工具台账（外部环境工具仍归根 tools/tool_inventory.py，不重复）。
TOOL_CATALOG = ROOT / "tools" / "tool_catalog.py"
TOOL_LEDGER = ROOT / "工具总账.md"
BA_PRESET_CHECK = TOOLS / "ba_preset_check.py"
HANDOFF_CHECK = ROOT / "tools" / "handoff_check.py"
EXTRACTOR = ROOT / "断箭模型提取器"
ASSET_MANAGER = ROOT / "断箭资产管理器"
PRODUCT_CLI = PROD / ".venv" / "Scripts" / "ba-mod-maker.exe"
GAME_ROOT = Path(
    os.environ.get("BA_MOD_MAKER_GAME") or r"D:\Steam\steamapps\common\broken_arrow"
)
GAME_LOGS = GAME_ROOT / "GameLogs"
ROOT_TOOLS = ROOT / "tools"

NEXT_STEPS = r"""## 3 · 本模式下一步（六条路由 · 压缩版）

1. **游戏逻辑 / 反编译** ⇒ 2.5 ①（`ba_gate.py --kind dig` → **必须** `--kind read`）；
   索引不新鲜按 2.6 给的重建命令跑。⛔ 索引 0 命中 ≠「反编译里没有」。
2. **数据字段 / 先查再猜** ⇒ `tools\kb_lookup.py` ＋ `docs\数据-表与列清单.md` ＋
   `tools\find_card_row.py`；
   都空**再**反编译。
3. **资产线 / Unity 6** ⇒ **先看 2.5b 产线入口表**（提取 / 造包 / 登记 / 汉化各一条唯一入口）；
   观感判据 = 根 `tools\unity_render.py` 的**产物 mtime 变新**；造包判据 = report.json `ok=True`。
4. **改源码前** ⇒ `ba_gate.py --kind verify`（判据真跑，before 必须命中）→ `--kind plan`；不过就红。
   硬门 v3 覆盖：数据侧仓 · 资产管理器 · 提取器 · 根 tools。⛔ 手打数字 / 编命令过不了闸。
5. **工具维护 / 台账** ⇒ `工具总账.md` ＋ `tools\tool_catalog.py --check`；预设本身用
   `tools\ba_preset_check.py`。加/改工具后**重生成台账**（`--check` 能红才算接上）。
6. **两块独立活** ⇒ `team_task_create` + `spawn_teammate`；单块在 4 的 plan 里写「拆不动：<理由>」。

**收尾** ⇒ 更新 `STATUS.md` 顶部 ＋ `tools\acceptance_log.py record`（成功也要记）。

⛔ 红线：不跑全量 pytest / 不跑 git / 装机·改游戏目录·删产物先问 / 注入类绝不联机 /
开 units 包先拿 `tools\units_lock.py` 的锁 / 游戏在跑不开 Unity /
**真机请求只准走 `ask_user_question`**（硬门要 ①verify 真尺子 ②资产类 unity ③机制类 read；
`echo` 不算尺子）。"""


# ──────────────────────────────────────────────────────────────────────────
# 基础工具（只读 / 不抛）
# ──────────────────────────────────────────────────────────────────────────


def _force_utf8_streams() -> None:
    """Windows 控制台默认 GBK：不钉住就会在成功路径上 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _read_text(path: Path) -> str:
    with contextlib.suppress(OSError, UnicodeError):
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _rel(path: Path) -> str:
    """仓内路径写成 `_work/…`（短、可读）；仓外照原样给绝对路径。"""
    with contextlib.suppress(ValueError):
        return path.relative_to(PROD).as_posix()
    return str(path)


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "—"
    with contextlib.suppress(OSError, OverflowError, ValueError):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    return "—"


def _fmt_mb(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


def run_cmd(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    """跑一条**只读**子命令；超时/缺程序都降级成一行说明，不抛。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"<跑不动：{type(exc).__name__}: {exc}>"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ──────────────────────────────────────────────────────────────────────────
# 四块老内容的解析函数（口径不动）
# ──────────────────────────────────────────────────────────────────────────


#: 现场块标题，**两种世代都认**：
#:   · 新代（2026-11-03 实测）：`# STATUS` 之后直接是 Markdown 原生 `## ✅ 日期 …`；
#:   · 旧代（2026-10 及更早）：引用块 `> ## …` / `> # …`（顶部用连续 h1 堆叠）。
_STATUS_PLAIN_H2 = re.compile(r"^## ")
_STATUS_PLAIN_H1 = re.compile(r"^# ")
_STATUS_HEADING = re.compile(r"^> (##?) ")
_STATUS_H2 = re.compile(r"^> ## ")


def _clip_status_lines(out: list[str], max_chars: int) -> str:
    """行数已截 → 再压字符上限；两条截断都要在正文里留一句「全文去哪读」。"""
    while out and not out[-1].strip():
        out.pop()
    section_text = "\n".join(out)
    if len(section_text) > max_chars:
        tail_note = "\n…（字符截断 —— 全文读 D:\\断箭模组制作\\STATUS.md 顶部）"
        section_text = section_text[:max_chars].rstrip() + tail_note
    return section_text


def extract_status_section(
    text: str,
    max_lines: int = STATUS_MAX_LINES,
    max_chars: int = STATUS_MAX_CHARS,
) -> str:
    """取 STATUS.md 顶部**最新现场块**。

    ★ 2026-11-03 修（实测抓空）：STATUS.md 顶部改成 Markdown 原生标题
      （`## ✅ 2026-11-03 13:13 …`），
      旧版只认引用块 `> ##` / `> #` ⇒ 开工包第 0 段永远报「没有现场段」，模型看不到本轮现场。
      现在先按**新代**取：第一个 `## ` 标题起，到下一个 `## ` / `# ` 前；取不到再退回
      **旧代**引用块逻辑。

    行数 + 字符**双上限**：这一块给大了会把后面的 2.5/2.5b/2.6 硬路由块挤出 7800 上限。
    """
    lines = text.splitlines()

    # ── 新代：`## …` 顶格标题 ────────────────────────────────────────────
    plain = next((i for i, line in enumerate(lines) if _STATUS_PLAIN_H2.match(line)), None)
    if plain is not None:
        out = [lines[plain].rstrip()]
        for line in lines[plain + 1 :]:
            if _STATUS_PLAIN_H2.match(line) or _STATUS_PLAIN_H1.match(line):
                break
            out.append(line.rstrip())
            if len(out) >= max_lines:
                out.append("…（行数截断 —— 全文读 D:\\断箭模组制作\\STATUS.md 顶部）")
                break
        return _clip_status_lines(out, max_chars)

    # ── 旧代：引用块 `> #` / `> ##` ───────────────────────────────────────
    start = next((i for i, line in enumerate(lines) if _STATUS_HEADING.match(line)), None)
    if start is None:
        return (
            "（STATUS.md 顶部没有 `## …` / `> ## …` 现场段 —— 先按 AGENTS §0.8 补写本轮现场）"
        )
    first_is_h1 = lines[start].startswith("> # ")
    out = []
    for line in lines[start:]:
        if out:
            if first_is_h1:
                if _STATUS_H2.match(line):
                    break
            elif _STATUS_HEADING.match(line):
                break
        out.append(line.rstrip())
        if len(out) >= max_lines:
            out.append("> …（行数截断 —— 全文读 D:\\断箭模组制作\\STATUS.md 顶部）")
            break
    return _clip_status_lines(out, max_chars)


def extract_handoff_summary(text: str, max_lines: int = 14) -> list[str]:
    """从 handoff_check --brief 里只取**结论行**（顶格的 OK/⚠/❌/★），丢掉 51 条工具明细。"""
    keys = ("OK ", "⚠ ", "❌ ", "★ ")
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith((" ", "\t")):
            continue
        if line.startswith(keys) or "可以干净接手" in line:
            out.append(line[:220])
            if len(out) >= max_lines:
                break
    return out


def extract_success_digest(text: str, max_lines: int = 8) -> list[str]:
    """success_guard --status 的前几行（心跳 / 台账四指标 / 最新一局记没记）。"""
    out = [line.rstrip()[:220] for line in text.splitlines() if line.strip()]
    return out[:max_lines]


def latest_game_version(logs: Path = GAME_LOGS) -> str:
    """最新一局 Gamelog 的 `Version:` 行（只读，最多读 8 行）；读不到返回空串。

    为什么进开工包（2026-11-03）：本机游戏已经从 1.2.1 走到 **1.2.2**，而 kb/40 / 多处口径
    还写着 1.2.1 —— 版本一变，catalog 出厂读数、Unity 代际、地址都会跟着变。
    一个会话开工先知道「现在跑的是哪版游戏」，比事后猜省一轮。
    """
    try:
        logs = sorted(logs.glob("Gamelog__*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return ""
    if not logs:
        return ""
    try:
        with logs[-1].open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""


def cap_text(text: str, limit: int = MAX_CHARS) -> str:
    """超长截断保头保尾（DOM 式截断比只留头更有用：下一步与红线在尾部）。"""
    if len(text) <= limit:
        return text
    head = limit * 3 // 4
    tail = limit - head - 48
    return text[:head] + "\n\n…（超长截断，全文读源文件）…\n\n" + text[-tail:]


# ──────────────────────────────────────────────────────────────────────────
# 2.5 五条硬路由（静态文本，路径**全绝对**）
# ──────────────────────────────────────────────────────────────────────────


def build_routing_block() -> str:
    """五条硬路由：触发条件 + 绝对路径命令 + 判据一行。

    为什么这里写死绝对路径：用户投诉「给的下一条命令是散文、部分路径过期」——
    模型自己拼 `tools/xxx.py` 会撞空（如 `unity_render.py` 在**根** tools、不在仓内）。
    """
    lines = [
        "## 2.5 · 五条硬路由（机械版；照抄绝对命令，别自己拼相对路径）",
        "⏱ **没有时间预算**（用户 2026-10-25）：「宁可慢 10 分钟去读 ISIL / 跑渲染闸 / 查日志，"
        "也不许凭概率下结论」—— **抢时间 = 猜**。",
        "🧠 **没有上下文预算**（用户 2026-11-14 原话：「严！禁！考虑上下文！上下文是我考虑的事，"
        "不该由AI考虑！」）—— 上下文长了 / 快满了**不是跳过、缩短、截断任何一步的理由**"
        "（读代码 / ISIL / 渲染闸 / verify / 记账）；**开不开新会话由用户决定**。",
        "🧾 **证据分级**：**A 级**＝外部对照（sha/行号/数据库实跑数字/日志行/"
        "`kind=verify` 现场输出）→ **只有 A 级能当结论**；**B 级**＝自检全绿（只算接线）；"
        "**C 级**＝复述/推断（禁止当结论）。",
        "",
        "**① 游戏逻辑 / 反编译**（为什么这样 / 这函数在哪）",
        f'  `{PY} {BA_GATE} --kind dig --query "<原话>"` → 报告给出真实 ISIL `.txt` 后'
        " **必须** `--kind read`（`symbol=Type.Method` 或 `file=<dig 给的路径>`）",
        f"  · 反编译根 / 索引新鲜度见 2.6；检索入口 `{DECOMP_INDEX} query <符号>`；"
        "⛔ 只引用路径 = 没读；⛔ 0 命中 ≠「反编译里没有」。",
        "",
        "**② 数据 / 字段**（这字段在哪 / 卡片这行哪来）",
        f'  `{PY} {KB_LOOKUP} "<原话>"`（知识库唯一检索入口）＋ `docs\\数据-表与列清单.md` ＋ '
        f'`{PY} {FIND_CARD_ROW} "<中文名>"` → 拿 `表.列 / 偏移 / 读点`，**再**走 ①；'
        "判据 = 给出 `表.列` ＋ 实跑数字。",
        "",
        "**③ Unity 6**（看起来对不对 / 材质·UV·网格变了没）",
        f"  `{PY} {UNITY_RENDER} <bundle> [--builtin]` ← ⚠ 在**根** tools 目录，不在仓内；"
        "判据 = **产物 mtime 变新**；⛔ 跑前关掉那个工程的编辑器。",
        "",
        "**④ 防猜 / 实测**（改源码之前）",
        f'  `{PY} {BA_GATE} --kind verify --payload '
        f'\'{{"command":"<要跑的命令>","before":<改前读数>}}\'`'
        "（**判据真跑**：退出码＋输出 sha256，before 必须在输出里）→ "
        "`--kind plan`（before≠after）；",
        "⛔ 手打数字 / 编命令过不了闸。",
        "",
        "**⑤ 团队**（两块以上彼此独立的活）先 `team_task_create`（一块一个，写清 write_scopes）"
        "再 `spawn_teammate`；单块也要在 ④ 的 plan 里记一句「拆不动：<理由>」。",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 2.5b 产线入口路由（v3 新增：光有「五条硬路由」不够 —— 模型不知道「这活走哪个入口」）
# ──────────────────────────────────────────────────────────────────────────


def build_entry_routes_block() -> str:
    """产线入口路由：每条 = 活儿 / 唯一入口（绝对命令） / 判据。

    为什么加（2026-11-03 实测）：2026-10-31 起资产线（提取器 → 造包 → 渲染闸 → 登记 catalog）
    已经跑通并改过十几轮，但一套 preset 里**没有任何一句**指向它 —— 新会话只会去翻旧文档。
    """
    lines = [
        "## 2.5b · 产线入口路由（问「这活走哪个入口 / 拿哪把尺子」先看这张表）",
        "| 活儿 | 唯一入口 | 判据（怎么算做完） |",
        "|---|---|---|",
        f"| 提取模型/动画 | GUI `{_rel2(EXTRACTOR / '提取器.pyw')}` · "
        "CLI `ba_extract.py check/list/info/extract`（`contract` = 节点契约速查） | "
        "输出夹有 model.fbx/glb + anim_config.json；节点契约对得上 |",
        f"| 造包（工程→bundle） | GUI `{_rel2(ASSET_MANAGER / '资产管理器.pyw')}`"
        "（造包页）· kernel `断箭资产管理器\\kernel\\build_pipeline.py::run_build_chain` · "
        f'CLI `"{PRODUCT_CLI}" render/check-model/pack` | report.json `ok=True` ＋ '
        "渲染闸产物 **mtime 变新** ＋ check-model 全绿 |",
        f'| 登记地址 / 还原 | GUI 看包页 · CLI `"{PRODUCT_CLI}" address add/delete`；装完必验用根 '
        "`tools\\install_bundle_safe.py` | 写后**读回 key 命中** ＋ catalog.bin 有可还原反向 Δ |",
        f'| 数据表编辑 | GUI `_work\\gui_live.py` · CLI `"{PRODUCT_CLI}" <子命令>`（全表 '
        "`docs\\产品功能清单.md`） | 当次改动的 pytest 模块 ＋ 写后复核读数 |",
        "| 汉化 / 本地化 | `_work\\loc\\`（dump_loc → report → make_report） | "
        "`_work\\loc\\out\\` 的统计报告与 CSV 读数 |",
        "| 工具维护 / 台账 | `工具总账.md`（重生成 `tools/tool_catalog.py`）· "
        "`tools\\tool_inventory.py` · `handoff_check.py --brief` · "
        "`gen_capabilities.py --check` | tool_catalog `--check` rc=0 ＋ "
        "体检末行「可以干净接手」 |",
        f"| 预设 / 插件本身 | `{_rel(TOOLS / 'dsh-plugin-ba-mode')}`"
        "（改 cordis.patch.yml 后 plugin_manager remove_bundle → install_bundle） | "
        "`node selftest.mjs` ＋ `ba_preset_check.py` 全绿 ＋ 新会话**负控**"
        "（写产品源码被硬门拒一次） |",
        "| 崩溃 | `<游戏>\\GameLogs\\Gamelog__*.log` → `_work\\v5-crashdump.py` → "
        "`v5-crashstack3.py` | 异常码 ＋ 活帧方法 |",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 2.6 工具箱现场（自动检测：只 stat / 只读）
# ──────────────────────────────────────────────────────────────────────────


def _rel2(path: Path) -> str:
    """工作区内路径写成短名（`工具总账.md` / `断箭模型提取器/提取器.pyw`）；仓外照原样。"""
    for base in (ROOT, PROD):
        with contextlib.suppress(ValueError):
            return path.relative_to(base).as_posix()
    return str(path)


def toolbox_items() -> list[dict]:
    """本模式工具箱的**权威清单**（v3：分组活体检查；加工具就加这里，报告会自动 ✅/❌）。

    一条可以是单文件（`path`）或一组（`paths`）——组只在「全在」时打一行，缺任何一条都点名。
    ⛔ 这里只放**开工就要用**的入口：完整自有工具清单在 `工具总账.md`（`tools/tool_catalog.py`
    生成），外部环境工具在 `tools/tool_inventory.py`，两处不重复收。
    """
    return [
        {"label": "证据闸 ba_gate（dig/read/verify/plan/unity）", "path": BA_GATE, "kind": "file"},
        {
            "label": "ISIL 检索（decomp_index+meta）",
            "paths": [DECOMP_INDEX, DECOMP_META],
            "kind": "file",
        },
        {
            "label": "知识库/卡片行（kb_lookup+find_card_row）",
            "paths": [KB_LOOKUP, FIND_CARD_ROW],
            "kind": "file",
        },
        {
            "label": "症状三扇门 + HUDName（three_doors/hud_name）",
            "paths": [TOOLS / "three_doors.py", TOOLS / "hud_name.py"],
            "kind": "file",
        },
        {"label": "Unity 6 渲染闸（根 tools）", "path": UNITY_RENDER, "kind": "file"},
        {
            "label": "Unity 6 编辑器 + HDRP17 工程",
            "paths": [UNITY_EDITOR, HDRP17],
            "kind": "dir",
        },
        {
            "label": "根 tools 跨会话尺子",
            "paths": sorted(ROOT_TOOLS.glob("*.py")),
            "kind": "file",
        },
        {
            "label": "体检/台账（handoff_check+tool_catalog+ba_preset_check）",
            "paths": [HANDOFF_CHECK, TOOL_CATALOG, BA_PRESET_CHECK],
            "kind": "file",
        },
        {"label": "自有工具总账（工具总账.md）", "path": TOOL_LEDGER, "kind": "file"},
        {
            "label": "成功率三件套（game_log+success_guard+acceptance_log）",
            "paths": [
                TOOLS / "game_log.py",
                TOOLS / "success_guard.py",
                TOOLS / "acceptance_log.py",
            ],
            "kind": "file",
        },
        {
            "label": "资产线入口（提取器/资产管理器/CLI）",
            "paths": [
                EXTRACTOR / "提取器.pyw",
                EXTRACTOR / "ba_extract.py",
                ASSET_MANAGER / "资产管理器.pyw",
                ASSET_MANAGER / "kernel" / "build_pipeline.py",
                PRODUCT_CLI,
            ],
            "kind": "file",
        },
        {
            "label": "取证实录（prefab/anim斜率/VT/USN）",
            "paths": [
                ROOT_TOOLS / "prefab_contract_diff.py",
                ROOT_TOOLS / "fix_anim_tangents.py",
                ROOT_TOOLS / "unity_vtplayer.py",
                ROOT_TOOLS / "usn_trace.py",
            ],
            "kind": "file",
        },
    ]


def probe_tool(item: dict) -> tuple[bool, str]:
    """stat 一条（单文件或一组）→ (全在吗, 一行 markdown)。只 stat，不抛。"""
    label = str(item.get("label") or "?")
    kind = str(item.get("kind") or "file")
    raw_paths = item.get("paths")
    if not raw_paths:
        raw = item.get("path")
        if raw is None or str(raw) == "":
            return False, f"- ❌ {label} —— **未配置路径**"
        raw_paths = [raw]
    try:
        paths = [Path(str(p)) for p in raw_paths]
    except TypeError:
        return False, f"- ❌ {label} —— **路径列表不是序列**"
    if not paths:
        return False, f"- ❌ {label} —— **路径列表为空**"

    missing: list[str] = []
    total_bytes = 0
    for path in paths:
        try:
            if not path.exists():
                missing.append(str(path))
                continue
            if kind == "dir" and not path.is_dir():
                missing.append(f"{path}（不是目录）")
                continue
            if path.is_file():
                total_bytes += path.stat().st_size
        except OSError:
            missing.append(str(path))
    if missing:
        preview = _rel2(Path(missing[0].split("（")[0]))
        return False, f"- ❌ {label} —— 缺 {len(missing)}/{len(paths)}（{preview}）"
    single = paths[0]
    if len(paths) == 1:
        suffix = f" · {total_bytes:,} B" if total_bytes else (" · 目录" if kind == "dir" else "")
        return True, f"- ✅ {label} —— {_rel2(single)}{suffix}"
    return True, f"- ✅ {label} —— {len(paths)} 条都在"


def render_toolbox(items: list[dict] | None = None) -> tuple[list[str], int, int]:
    """逐条 probe → (行列表, 在的条数, 总条数)。items 可注入（selftest 用假工具箱）。"""
    rows = list(toolbox_items() if items is None else items)
    lines: list[str] = []
    present = 0
    for item in rows:
        good, line = probe_tool(item)
        present += 1 if good else 0
        lines.append(line)
    if present == len(rows):
        head = f"**工具箱：{present}/{len(rows)} 组在**"
    else:
        head = (
            f"**工具箱：{present}/{len(rows)} 组在** —— ❌ 项要么还没建、要么路径变了："
            "先按 2.5 / 2.5b 的绝对路径核对，别自己猜"
        )
    lines.append(
        "- 📒 完整清单：**自有** `工具总账.md`（`tools/tool_catalog.py`，`--check` 能红）· "
        "**外部环境** `tools/tool_inventory.py`"
    )
    return [head, *lines], present, len(rows)


def walk_stats(root: Path) -> tuple[int, int, float]:
    """递归数文件数 / 总字节 / 最新文件 mtime（只 scandir+stat，OSError 跳过）。"""
    files = 0
    total = 0
    newest = 0.0
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            files += 1
                            total += stat.st_size
                            newest = max(newest, stat.st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return files, total, newest


def scan_isil_roots(work: Path = WORK) -> list[dict]:
    """扫 `_work/isil*` 目录 → 每份 {name, dump, is_dump, files, bytes, max_mtime}。不抛。"""
    rows: list[dict] = []
    try:
        dirs = sorted(p for p in work.glob("isil*") if p.is_dir())
    except OSError:
        return rows
    for directory in dirs:
        dump = directory / "IsilDump"
        row: dict = {
            "name": directory.name,
            "dump": dump,
            "is_dump": dump.is_dir(),
            "files": 0,
            "bytes": 0,
            "max_mtime": 0.0,
        }
        if row["is_dump"]:
            with contextlib.suppress(OSError):
                row["files"], row["bytes"], row["max_mtime"] = walk_stats(dump)
        rows.append(row)
    return rows


def pick_isil_authority(rows: list[dict]) -> dict | None:
    """按「最新文件 mtime」挑权威根（并列时比字节数）；空/无 dump ⇒ None（降级，不抛）。"""
    candidates = [r for r in rows if r.get("is_dump") and int(r.get("files") or 0) > 0]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda r: (float(r.get("max_mtime") or 0.0), int(r.get("bytes") or 0)),
    )


def read_decomp_meta(path: Path = DECOMP_META) -> dict | None:
    """读 `_work/decomp-index/meta.json`（UTF-8 JSON）；读不到/坏格式 ⇒ None。"""
    raw = _read_text(path)
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def render_isil_lines(rows: list[dict], meta: dict | None = None) -> list[str]:
    """ISIL 根现场：权威根（名字/文件数/最新 mtime/字节数）+ 历史对拍件 + 索引新鲜度。"""
    authority = pick_isil_authority(rows)
    if authority is None:
        return [
            "- ❌ 扫不到 `_work/isil*/IsilDump`（反编译产物缺失：先按 kb/14 查根，或重挖约 11.5 s）"
        ]
    dump = authority["dump"]
    lines = [
        f"- ✅ **当前权威 ISIL 根**：`{authority['name']}/IsilDump` · "
        f"{authority['files']:,} 文件 · {_fmt_mb(authority['bytes'])} · "
        f"最新文件 {_fmt_ts(authority['max_mtime'])}",
    ]
    others = [r for r in rows if r is not authority and r.get("is_dump")]
    if others:
        shown = []
        for row in others:
            if int(row.get("files") or 0) > 0:
                shown.append(f"`{row['name']}`({row['files']:,}/{_fmt_mb(row['bytes'])})")
            else:
                shown.append(f"`{row['name']}`(空)")
        lines.append(
            "- 历史对拍件（旧构建/局部重挖：地址对不上 ⇒ 别引用它的 file:line）："
            + " · ".join(shown)
        )
    skipped = [r for r in rows if not r.get("is_dump")]
    if skipped:
        lines.append(
            "- 非 dump 目录（工具，不算反编译根）：" + " · ".join(f"`{r['name']}`" for r in skipped)
        )
    if meta is None:
        lines.append(
            "- ⚠ 反编译索引 meta.json 读不到 ⇒ 用 `file:line` 前先 `decomp_index.py build`"
            "（⛔ 不许因 0 命中就说「反编译里没有」）"
        )
        return lines
    meta_isil = str(meta.get("isil") or "")
    built_at = str(meta.get("built_at") or "?")
    if not meta_isil:
        lines.append("- ⚠ 索引 meta.json 里没有 `isil` 字段 ⇒ 索引来源不明，先 build")
        return lines
    same = os.path.normcase(os.path.abspath(meta_isil)) == os.path.normcase(
        os.path.abspath(str(dump))
    )
    if same:
        same_count = int(meta.get("isil_files") or 0) == int(authority.get("files") or 0)
        mark = "✅" if same_count else "⚠"
        lines.append(
            f"- {mark} 索引 `_work/decomp-index/` 指向权威根（built {built_at} · "
            f"{meta.get('isil_files')} 文件）"
            + ("" if same_count else "，但文件数对不上 ⇒ 先 build 再引 `file:line`")
        )
    else:
        rebuild_cmd = (
            f'"{PY}" "{DECOMP_INDEX}" build --isil "{dump}"'
        )
        lines.append(
            f"- ⚠ **索引过期**：`_work/decomp-index/meta.json` 指向 `{_rel(Path(meta_isil))}`"
            f"（built {built_at}）≠ 权威根 `{authority['name']}/IsilDump` ⇒ "
            "引 `file:line` 前先重建："
        )
        lines.append(f"  `{rebuild_cmd}`")
    return lines


def _fmt_heartbeat_ts(value: object) -> str:
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:  # 毫秒时间戳
            seconds /= 1000.0
        with contextlib.suppress(OSError, OverflowError, ValueError):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))
    if isinstance(value, str) and value.strip():
        text = value.strip()
        #: 插件写的是 ISO8601（`2026-11-03T08:35:44.691Z`）—— 转本地时间给人看；
        #: 转不动就原样给（至少不是 `?`）。
        with contextlib.suppress(ValueError):
            from datetime import datetime

            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        return text
    return str(value) if value is not None else "?"


def format_heartbeat(raw: str | None, path: Path = HEARTBEAT) -> str:
    """插件心跳 → 一行。raw=None = 不存在/读不到 ⇒ 点名「心跳缺失」（dsh-ba-mode 可能没加载）。"""
    if raw is None:
        return (
            f"- ⛔ **插件心跳缺失** = dsh-ba-mode 可能没加载（`{_rel(path)}` 不存在；"
            "插件薄壳在 tools/dsh-plugin-ba-mode/）"
        )
    text = raw.strip()
    if not text:
        return f"- ⚠ 插件心跳文件在但是空的（`{_rel(path)}`）"
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return f"- ⚠ 插件心跳文件在但不是 JSON（`{_rel(path)}`）：{text[:100]}"
    if not isinstance(data, dict):
        return f"- ⚠ 插件心跳不是 JSON 对象（`{_rel(path)}`）"
    #: ★ 2026-11-03 修：插件实际写的键是 `mountAt`（index.js 的 beat({mountAt})）——
    #:   旧列表里没有它 ⇒ 开工包长期显示「挂载于 ?」（版本能读到，时间读不到）。
    stamp = next(
        (
            data[k]
            for k in ("ts", "mountAt", "mountedAt", "mounted_at", "at", "time", "bootedAt")
            if k in data
        ),
        None,
    )
    version = next(
        (data[k] for k in ("version", "ver", "pluginVersion", "heartbeatVersion") if k in data),
        None,
    )
    version_text = "?" if version is None else version
    return f"- ✅ dsh-ba-mode 挂载于 {_fmt_heartbeat_ts(stamp)} / 版本 {version_text}"


def heartbeat_line() -> str:
    """读心跳文件（只读）→ 一行；缺失/读不动都降级成「心跳缺失」。"""
    try:
        if not HEARTBEAT.exists():
            return format_heartbeat(None)
        raw = HEARTBEAT.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return format_heartbeat(None)
    return format_heartbeat(raw)


def build_toolbox_block() -> str:
    """2.6 整块：自动检测，任何一条跑不动只降级成一行（不抛、不影响其余块）。"""
    lines = ["## 2.6 · 本模式工具箱现场（自动检测）"]
    try:
        tool_lines, _present, _total = render_toolbox()
        lines.extend(tool_lines)
    except Exception as exc:  # 块内只降级：报告不能因为一条检测坏掉整个开工包
        lines.append(f"- ❌ 工具箱检测跑不动（{type(exc).__name__}）—— 降级，不影响其余块")
    lines.append("")
    try:
        lines.extend(render_isil_lines(scan_isil_roots(), read_decomp_meta()))
    except Exception as exc:  # 同上：ISIL 根检测坏掉也要照常出其余块
        lines.append(f"- ❌ ISIL 根检测跑不动（{type(exc).__name__}）—— 降级，不影响其余块")
    lines.append("")
    try:
        lines.append(heartbeat_line())
    except Exception:  # 同上：心跳读不动 ⇒ 按「心跳缺失」处理
        lines.append(format_heartbeat(None))
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 拼装
# ──────────────────────────────────────────────────────────────────────────


def _finalize(parts: list[str], started: float, rc_handoff: int, rc_success: int) -> str:
    """拼正文 + 尾部读数（**实测整份长度并打印**），再做 MAX_CHARS 截断。

    长度自己进正文 ⇒ 用不动点算：n = cap_text(正文+footer(n)) 的长度；数字位数稳定后收敛。
    """
    body = "\n\n".join(parts)
    elapsed = time.perf_counter() - started
    size = 0
    text = body
    for _ in range(5):
        footer = (
            f"—— 耗时 {elapsed:.2f} s · 体检 rc={rc_handoff} · 成功率 rc={rc_success} · "
            f"本报告 {size} 字符（上限 {MAX_CHARS}，超了会被工具结果修剪器砍头砍尾）"
        )
        text = cap_text(body + "\n\n" + footer)
        if len(text) == size:
            break
        size = len(text)
    return text


def build_report() -> str:
    """拼开工包。任何一块跑不动都只降级、不抛。"""
    started = time.perf_counter()
    parts: list[str] = [
        "# 断箭开发模式 · 开工包 v3",
        f"生成：{time.strftime('%Y-%m-%d %H:%M:%S')} · 只读（不写盘 / 不起游戏 / 不跑 pytest）"
        " · 唯一实现 = 本脚本（插件只是薄壳）",
    ]

    status = _read_text(ROOT / "STATUS.md")
    parts.append("## 0 · 现场（STATUS.md 顶部那一段）")
    parts.append(extract_status_section(status) if status else "（读不到 STATUS.md）")

    rc_handoff, out = run_cmd(
        [sys.executable, str(ROOT / "tools" / "handoff_check.py"), "--brief"],
        ROOT,
        HANDOFF_TIMEOUT_S,
    )
    summary = extract_handoff_summary(out)
    parts.append("## 1 · 接手体检（tools/handoff_check.py --brief 摘要）")
    if summary:
        parts.append("\n".join(summary))
    else:
        parts.append(f"（体检跑不动：rc={rc_handoff}）\n{out[:600]}")

    rc_success, out = run_cmd(
        [sys.executable, str(PROD / "tools" / "success_guard.py"), "--status"],
        PROD,
        SUCCESS_TIMEOUT_S,
    )
    digest = extract_success_digest(out)
    parts.append("## 2 · 成功率闸（tools/success_guard.py --status）")
    if digest:
        parts.append("\n".join(digest))
    else:
        parts.append(f"（没有状态输出：rc={rc_success}）\n{out[:600]}")
    version_text = latest_game_version()
    if version_text:
        parts.append(
            f"- 🎮 **当前游戏版本：{version_text}**（最新 Gamelog 的 `Version:` 行）—— "
            "版本变了先 `tools/catalog_baseline.py` 重记出厂读数，再判「改坏了没有」"
        )

    try:
        parts.append(build_routing_block())
    except Exception as exc:  # 静态块也不许把整个开工包带崩
        parts.append(f"## 2.5 · 五条硬路由（机械版）\n（本块拼装跑不动：{type(exc).__name__}）")

    try:
        parts.append(build_entry_routes_block())
    except Exception as exc:  # 同上
        parts.append(f"## 2.5b · 产线入口路由\n（本块拼装跑不动：{type(exc).__name__}）")

    try:
        parts.append(build_toolbox_block())
    except Exception as exc:  # 本条已是只降级实现，这里只兜底
        parts.append(
            f"## 2.6 · 本模式工具箱现场（自动检测）\n（本块检测跑不动：{type(exc).__name__}）"
        )
    parts.append(NEXT_STEPS)
    return _finalize(parts, started, rc_handoff, rc_success)


# ──────────────────────────────────────────────────────────────────────────
# 自检（负控能红 + 正控能绿 + 真拼一份）
# ──────────────────────────────────────────────────────────────────────────


def selftest() -> int:
    """自检：老 6 条保留 + 新增 ≥5 条（工具箱/ISIL/心跳/长度/三词防丢/集成），末行报条数。"""
    passed: list[str] = []
    failures: list[str] = []

    def ok(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            passed.append(name)
        else:
            failures.append(f"{name} —— {detail or '不通过'}")

    # ── ① STATUS 顶部现场（老 1、2）────────────────────────────────────
    fake_status = "\n".join(
        [
            "# STATUS",
            "",
            "> ## 第一条现场",
            "> 读数 A",
            "> ",
            "> ## 第二条现场（不该出现）",
            "> 读数 B",
        ]
    )
    section = extract_status_section(fake_status)
    ok(
        "status：只取第一段",
        "第一条现场" in section and "读数 A" in section,
        repr(section[:80]),
    )
    ok("status：第二段没混进来", "第二条现场" not in section, repr(section[:80]))
    ok("status：空输入有降级说明", "现场段" in extract_status_section(""))
    #: 2026-11-03 负控（真踩）：STATUS.md 顶部已改成 Markdown 原生 `## ✅ …`，旧检测器只认 `> ##`
    #: ⇒ 开工包第 0 段永远报「没有现场段」。这里固定住新格式必须抓到、且不越过下一条 `## `。
    fake_plain = "\n".join(
        [
            "# STATUS",
            "",
            "## ✅ 2026-11-03 13:13 · 最新现场",
            "读数：A=1",
            "### 一、小节（h3 不算边界）",
            "读数：B=2",
            "",
            "## ✅ 2026-11-02 旧现场（不该出现）",
            "读数：C=3",
        ]
    )
    plain_section = extract_status_section(fake_plain)
    ok(
        "status：新代 `## …` 现场段能抓到",
        "最新现场" in plain_section and "A=1" in plain_section,
        repr(plain_section[:120]),
    )
    ok(
        "status：新代不越过下一个 `## `（h3 不当边界）",
        "旧现场" not in plain_section and "B=2" in plain_section,
        repr(plain_section[-120:]),
    )
    #: 2026-10-25 负控：顶部最新块是 `> #`（h1）堆叠，必须把连续几段 h1 收进来、遇 `> ##` 停。
    fake_h1 = "\n".join(
        [
            "# STATUS",
            "",
            "> # 🚩 最新交接块",
            "> 读数：A=1",
            ">",
            "> # 🧨 次新块",
            "> 读数：B=2",
            ">",
            "> ## 旧细分块（不该出现）",
            "> 读数：C=3",
        ]
    )
    h1_section = extract_status_section(fake_h1)
    ok(
        "status：h1 堆叠取全（最新两块）",
        "🚩 最新交接块" in h1_section and "🧨 次新块" in h1_section,
        repr(h1_section[:120]),
    )
    ok(
        "status：h1 块遇 h2 停（旧细分块没混进来）",
        "C=3" not in h1_section,
        repr(h1_section[-80:]),
    )

    # ── ② 体检摘要（老 3、4）──────────────────────────────────────────
    fake_handoff = "\n".join(
        [
            "handoff_check --brief · 根 X",
            "OK ① 入口件：齐",
            "  【工具清单（缩进明细，不该出现）】",
            "    OK 某工具  D:\\x",
            "⚠ ③ 工作树：有未提交",
            "OK 可以干净接手：全绿",
        ]
    )
    summary = "\n".join(extract_handoff_summary(fake_handoff))
    ok(
        "handoff：结论行取全",
        "① 入口件" in summary and "可以干净接手" in summary,
        summary[:120],
    )
    ok(
        "handoff：缩进明细没漏进来",
        "缩进明细" not in summary and "某工具" not in summary,
        summary[:120],
    )

    # ── ③ 成功率摘要（老 5）───────────────────────────────────────────
    digest = extract_success_digest("心跳：x\n台账：y\n\n最新一局：已记账\n")
    ok(
        "success：摘要行数/内容",
        len(digest) == 3 and "已记账" in digest[-1],
        f"{len(digest)} 行",
    )

    # ── ④ 截断 / 短文本（老 6）────────────────────────────────────────
    long_text = "头" * 10000 + "尾" * 10000
    capped = cap_text(long_text, 7800)
    ok("cap：截断后 ≤ 7900", len(capped) <= 7900, f"{len(capped)} 字符")
    ok("cap：同时保住头尾", capped.startswith("头") and capped.endswith("尾"))
    ok("cap：短文本不被改动", cap_text("短文本") == "短文本")

    # ── ⑤ 工具箱：假 dict，缺 ba_gate.py 必须 ❌ ──────────────────────
    missing = Path(tempfile.gettempdir()) / "ba_boot_selftest_missing_ba_gate.py"
    fake_items = [
        {"label": "ba_gate.py", "path": missing, "kind": "file"},
        {"label": "ba_boot.py（真文件）", "path": Path(__file__).resolve(), "kind": "file"},
    ]
    tool_lines, tool_ok, tool_all = render_toolbox(fake_items)
    joined = "\n".join(tool_lines)
    ok(
        "工具箱：缺项必须 ❌、有项必须 ✅",
        tool_ok == 1 and tool_all == 2 and "❌ ba_gate.py" in joined and "✅ ba_boot.py" in joined,
        joined[:160],
    )

    # ── ⑥ ISIL 挑根（正控：新 mtime 胜出；负控：空列表降级）──────────
    fake_rows = [
        {
            "name": "isil_old",
            "dump": Path("old/IsilDump"),
            "is_dump": True,
            "files": 10,
            "bytes": 1000,
            "max_mtime": 1000.0,
        },
        {
            "name": "isil_new",
            "dump": Path("new/IsilDump"),
            "is_dump": True,
            "files": 20,
            "bytes": 2000,
            "max_mtime": 2000.0,
        },
        {
            "name": "isil-tools",
            "dump": Path("tools/IsilDump"),
            "is_dump": False,
            "files": 0,
            "bytes": 0,
            "max_mtime": 0.0,
        },
    ]
    authority = pick_isil_authority(fake_rows)
    ok(
        "ISIL：按最新 mtime 挑权威根",
        isinstance(authority, dict) and authority.get("name") == "isil_new",
        repr(authority.get("name") if isinstance(authority, dict) else authority),
    )
    empty_lines = render_isil_lines([])
    ok(
        "ISIL：空列表给降级说明而不是抛",
        len(empty_lines) == 1 and "扫不到" in empty_lines[0],
        repr(empty_lines[:1]),
    )

    # ── ⑦ 插件心跳：缺失 ⇒ 点名；存在（假 JSON）⇒ 挂载于 + 版本 ───────
    ok("心跳：缺失必须出现「心跳缺失」", "心跳缺失" in format_heartbeat(None))
    beat = format_heartbeat('{"ts": 1760000000000, "version": "2.0.0"}')
    ok("心跳：在 ⇒ 挂载于 + 版本", "挂载于" in beat and "2.0.0" in beat, beat[:120])
    #: 2026-11-03 负控（真踩）：插件写的键是 `mountAt`，旧列表没有它 ⇒ 长期显示「挂载于 ?」。
    beat_mount = format_heartbeat('{"mountAt": "2026-11-03T08:35:44.691Z", "version": "3.0.0"}')
    ok(
        "心跳：mountAt（插件实际写的键）⇒ 挂载时间，不许是 ?",
        "2026-11-03" in beat_mount and "挂载于 ?" not in beat_mount,
        beat_mount[:140],
    )

    # ── ⑦b 游戏版本 / 产线路由（v3 新增）──────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        logs = Path(tmp)
        (logs / "Gamelog__2026_11_03__13_19.log").write_text(
            "# log\nVersion: 1.2.2\n", encoding="utf-8"
        )
        ver = latest_game_version(logs)
        ok("版本：从 Gamelog 头抓 `Version:`", ver == "1.2.2", repr(ver))
        ok("版本：空目录降级成空串不抛", latest_game_version(logs / "nope") == "")
    routes = build_entry_routes_block()
    missing_routes = [
        w
        for w in (
            "2.5b", "提取器", "资产管理器", "登记地址", "数据表编辑", "汉化", "工具总账", "崩溃",
        )
        if w not in routes
    ]
    ok("2.5b：产线路由关键入口防丢", not missing_routes, f"丢了：{missing_routes}")

    # ── ⑧ 关键入口防丢（2.5 + 2.5b + 3 三块合起来查；以后被改掉就红）──────────
    combined = NEXT_STEPS + "\n" + build_routing_block() + "\n" + build_entry_routes_block()
    lost = [
        w
        for w in (
            "ba_gate.py", "unity_render.py", "team_task_create", "kind read", "没有时间预算",
            "没有上下文预算", "kind verify", "A 级", "产线入口", "工具总账",
        )
        if w not in combined
    ]
    ok("2.5/2.5b/3：关键入口防丢", not lost, f"丢了：{lost}")

    # ── ⑨ 报告长度上限（构造超长输入）────────────────────────────────
    huge = _finalize(["保头" + "Z" * 30000 + "保尾"], time.perf_counter(), 0, 0)
    ok(
        "报告：超长输入仍 ≤ 7900 且保住尾部读数",
        len(huge) <= 7900 and "截断" in huge,
        f"{len(huge)} 字符",
    )

    # ── ⑩ 集成：真拼一份（含 2.5 / 2.5b / 2.6 / 3，且 ≤ MAX_CHARS）──────────
    try:
        report = build_report()
        pockets = [head for head in ("## 2.5", "## 2.5b", "## 2.6", "## 3") if head not in report]
        ok(
            "集成：真报告含 2.5/2.5b/2.6/3 且 ≤ 上限",
            not pockets and len(report) <= MAX_CHARS,
            f"缺 {pockets} / {len(report)} 字符",
        )
    except (OSError, ValueError, TypeError) as exc:
        failures.append(f"集成：build_report 抛了 {type(exc).__name__}: {exc}")
    if failures:
        print("❌ 自检失败：")
        for row in failures:
            print(f"   · {row}")
        return 1
    print(
        f"✅ 自检全绿（{len(passed)} 条：STATUS 两段/空降级 · 体检结论 · 成功率摘要 · "
        "截断/短文本 · 工具箱缺项 ❌ · ISIL 挑根/空降级 · 心跳缺失/在 · "
        "NEXT_STEPS 三词 · 报告长度 · 集成真拼）"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(description="断箭开发模式 · 开工包（只读）")
    parser.add_argument("--json", action="store_true", help='输出 {"ok": bool, "report": str}')
    parser.add_argument("--selftest", action="store_true", help="自检：负控必须能红")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    report = build_report()
    if args.json:
        print(json.dumps({"ok": True, "report": report}, ensure_ascii=False))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
