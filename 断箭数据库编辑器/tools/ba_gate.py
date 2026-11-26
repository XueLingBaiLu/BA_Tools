r"""tools/ba_gate.py —— 断箭模式 v2 的**证据闸**（唯一实现；DSH 插件 `ba_gate` 只是薄壳）。

为什么有这个文件（用户投诉 v1 预设「只猜只试、不读反编译」）：

    预设里「读经三行 / 判据一行 / 反编译证据」都只是**散文要求** —— 写不写、写没写对，
    上下文里没有任何机械校验。于是「没读反编译」和「读了反编译」长得一样，
    模型自然挑便宜的那条路。本文件把它们压成**一条命令 + 一个 ok 字段**：

    kind=dig    一条命令出证据包（知识库 / 数据库列文档 / 卡片行 ISIL 读点 / 反编译索引）；
    kind=plan   机械校验「读经三行＋判据一行」：任一条不成立 ⇒ ok=false + fail[] 逐条
                写清「哪一行不对 + 现在该跑什么命令」；
    kind=unity  渲染闸预检（给**绝对命令**与候选 bundle）/ 跑后按**产物 mtime** 验收。
                ⛔ 不起 Unity；⛔ 不查进程状态（进程只能用 DSH pwsh 直接查，AGENTS §0.17）。

三条硬约束（与 tools/ba_boot.py 同口径）：

    ① **只读**：只 stat / 读文本 / 跑只读子命令（dig 里调 find_card_row.py 也是只读）；
    ② report 必须 **< 3500 字符**（插件侧工具结果修剪器 8192，留余量）；
    ③ **逻辑只写这一个文件**：检索一律 import 现成实现（decomp_index / kb_lookup /
       three_doors 的列文档口径 / find_card_row），⛔ 不复制判定（AGENTS §5「逻辑只有一份」）。

CLI 契约（插件 index.js::runGate 逐字这样调）::

    ba_gate.py --kind dig   --query "<原话/表.列/函数名>" [--json]
    ba_gate.py --kind plan  --payload '<json>' [--json]
    ba_gate.py --kind unity --payload '<json>' [--json]
    ba_gate.py --selftest

--json 时 stdout **只有一行**::

    {"ok": bool, "kind": str, "report": str, "witness": {...}, "fail": ["..."]}

exit code：非 --json 时 0=通过 / 1=不通过；--json 时**恒 0** —— 理由见 _emit() 的注释
（插件 index.js::runJsonScript 在 exit≠0 时**会丢掉 stdout 里的 JSON**，判定只能靠 ok 字段）。

口径出处：AGENTS.md §0.4（读经三行）/ §0.5（先查数据库再翻反编译）/ §0.9（判据先写）/
§0.17（进程只能用 DSH pwsh 查）/ kb/19（唯一判据与唯一入口）/ task-2（逐字 CLI 契约）。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PROD = TOOLS.parent                      # 仓根 BA_Mod_Maker_2.0
ROOT = PROD.parent                       # 工作区根 D:\断箭模组制作
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

PYTHON = PROD / ".venv" / "Scripts" / "python.exe"
PY = str(PYTHON) if PYTHON.is_file() else sys.executable

GATE = TOOLS / "ba_gate.py"
DECOMP_INDEX = TOOLS / "decomp_index.py"
FIND_CARD_ROW = TOOLS / "find_card_row.py"
KB_LOOKUP = TOOLS / "kb_lookup.py"
THREE_DOORS = TOOLS / "three_doors.py"
UNITY_RENDER = ROOT / "tools" / "unity_render.py"

DECOMP_INDEX_DIR = WORK / "decomp-index"
DECOMP_META = DECOMP_INDEX_DIR / "meta.json"
DB_SCHEMA = ROOT / "DataBaseCompiled" / "_schema.json"

#: 报告上限：插件层工具结果修剪器 8192 —— 给 3400，留足余量（task-2 的逐字要求是 < 3500）。
MAX_REPORT = 3400
FIND_CARD_TIMEOUT = 20.0

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

#: tools/ 不是包 —— 三份**唯一实现**按路径 import（与 three_doors.py 同款）。
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import decomp_index  # noqa: E402
import kb_lookup  # noqa: E402
import three_doors  # noqa: E402

#: 进程内缓存（一次 CLI 调用里 dig 的门要重复用；跨进程不落盘 —— 本工具只读）。
_KB_DOCS_CACHE: list | None = None
_SCHEMA_CACHE: dict[str, list[str]] | None = None
_ISIL_CACHE: list[dict] | None = None


# ──────────────────────────────────────────────────────────────────────────
# 基础工具（只读 / 不抛 / 与 ba_boot.py 同款）
# ──────────────────────────────────────────────────────────────────────────


def _force_utf8_streams() -> None:
    """Windows 控制台默认 GBK：不钉住就会在成功路径上 UnicodeEncodeError。

    插件用 `execFile(..., encoding:'utf8')` 读 stdout ⇒ 本题必须吐 UTF-8（与 ba_boot.py 同款）。
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _rel_any(path: Path) -> str:
    """仓内/工作区内路径写成相对形式（短、可读）；外部照原样给绝对路径。"""
    with contextlib.suppress(OSError, ValueError):
        resolved = path.resolve()
        for base in (PROD, ROOT):
            with contextlib.suppress(ValueError):
                return resolved.relative_to(base).as_posix()
    return str(path)


def _abs_any(text: str) -> Path:
    """「相对仓根 / 相对工作区根 / 绝对」三种写法统一成 Path（**不保证存在**）。"""
    raw = str(text).strip().strip('"').strip("'")
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    for base in (PROD, ROOT):
        candidate = base / path
        if candidate.exists():
            return candidate
    return PROD / path


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "—"
    with contextlib.suppress(OSError, OverflowError, ValueError):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    return "—"


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _as_number(value: object) -> float | None:
    """payload 里的数字 → float；bool / None / NaN / 非数字串 ⇒ None（判据要能红）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, str) and _NUM_RE.match(value.strip()):
        return float(value.strip())
    return None


def _cap(text: str, limit: int = MAX_REPORT) -> str:
    """报告截断：**保住尾部**（读经三行模板最不能丢），从中间砍。

    尾部配额 = limit/3：单独一行超长的报告（比如一行 50000 字的假输入）
    不能把整条尾巴一起算进「尾部」，否则 head 预算变负、截断等于没截。
    """
    if len(text) <= limit:
        return text
    note = "\n…（本报告已截断；上面每条命令都能自己重跑出完整读数）\n"
    quota = limit // 3
    tail_lines: list[str] = []
    tail_len = 0
    for line in reversed(text.splitlines()):
        if tail_len + len(line) + 1 > quota:
            break
        tail_lines.append(line)
        tail_len += len(line) + 1
    tail = "\n".join(reversed(tail_lines))
    head = text[: max(limit - len(tail) - len(note) - 2, 0)]
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    return head + note + tail


def _sanitize_absence(text: str) -> str:
    """⛔ 0 命中时**不许**说「没有」二字（task-2 逐字要求）。

    检索器 render 的劝退话术（"不能证明知识库里没有"）语义是对的、字面是禁的
    ⇒ 全份报告统一换成「不存在」，意思一样但不会触发「只看两个字就判没查」。
    """
    return text.replace("没有", "不存在")


# ──────────────────────────────────────────────────────────────────────────
# 反编译索引状态 / 当前权威 ISIL 根（证据定位，不改任何东西）
# ──────────────────────────────────────────────────────────────────────────


def _decomp_meta() -> dict:
    try:
        raw = json.loads(DECOMP_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _isil_roots() -> list[dict]:
    """扫 `_work/isil*/IsilDump` → 每份 {name, rel, path, files, bytes, newest}（不抛）。"""
    global _ISIL_CACHE
    if _ISIL_CACHE is not None:
        return _ISIL_CACHE
    rows: list[dict] = []
    with contextlib.suppress(OSError):
        for directory in sorted(WORK.glob("isil*")):
            dump = directory / "IsilDump"
            if not dump.is_dir():
                continue
            files = total = 0
            newest = 0.0
            stack = [dump]
            while stack:
                current = stack.pop()
                try:
                    with os.scandir(current) as entries:
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
            rows.append(
                {"name": directory.name, "path": dump, "rel": _rel_any(dump),
                 "files": files, "bytes": total, "newest": newest}
            )
    _ISIL_CACHE = rows
    return rows


def _isil_authority() -> dict | None:
    """当前权威 ISIL 根 = 「最新文件 mtime」最大的那份（并列比字节数）；没有 ⇒ None。"""
    rows = [row for row in _isil_roots() if row["files"] > 0]
    if not rows:
        return None
    return max(rows, key=lambda row: (row["newest"], row["bytes"]))


def _index_freshness_lines() -> list[str]:
    """索引源 vs 当前权威 ISIL 根 —— 不同就明写「索引可能过期」+ 重建命令。

    ❗ 为什么 dig 必须播报这一条：索引过期时 0 命中是**假象**，
    模型会把它读成「反编译里不存在」（AGENTS §0.12「反编译代码里没有 ≠ 拿不到」）。
    """
    authority = _isil_authority()
    if authority is None:
        return []
    lines = [
        f"  - 当前权威 ISIL 根：{authority['rel']}（{authority['files']:,} 个文件，"
        f"最新 {_fmt_ts(authority['newest'])}）"
    ]
    source = str(_decomp_meta().get("isil") or "")
    if source:
        source_rel = _rel_any(Path(source))
        if Path(source).resolve() != authority["path"].resolve():
            lines.append(
                f"  - ⚠ 索引源 = {source_rel}（与权威根不同）⇒ 索引可能过期，先跑："
                f"{PY} {DECOMP_INDEX} build --isil {authority['path']}"
            )
    return lines


#: 权威 ISIL 根里「文件名(不含 .txt) → [路径]」的一次性索引（进程内缓存）。
#: ❗ 为什么要它：门 3 的索引只回答「dump.cs 里有没有这个方法」——**那没有函数体**；
#: 用户投诉的第 2 条就是「读了反编译」等于「读到 dump.cs 一行签名」。
#: 所以方法/字段命中之后必须把它**所属的 Type** 落成一个真实存在、能整篇读的 ISIL .txt。
_ISIL_NAME_MAP: dict[str, list[Path]] | None = None


def _isil_search_root() -> Path | None:
    """当前权威 ISIL 根（`_work/isil*` 最新 mtime）；扫不到才退回索引源（meta.isil）。"""
    authority = _isil_authority()
    if authority is not None:
        return Path(authority["path"])
    source = str(_decomp_meta().get("isil") or "")
    if source and Path(source).is_dir():
        return Path(source)
    return None


def _isil_name_map() -> dict[str, list[Path]] | None:
    """权威根里 `文件名(不含 .txt) → [路径]`（rglob 一次 + 进程内缓存，不按名字猜）。

    ⛔ 不从 `_work/decomp-index/files.tsv` 拿路径：审计实测索引源可能比权威根旧一代，
    嵌套类编号会变（`_d__50.txt` → `_d__44.txt`）；文件名这一步要**在权威根现场扫**。
    """
    global _ISIL_NAME_MAP
    if _ISIL_NAME_MAP is not None:
        return _ISIL_NAME_MAP
    root = _isil_search_root()
    if root is None:
        return None
    mapping: dict[str, list[Path]] = {}
    with contextlib.suppress(OSError):
        for path in root.rglob("*.txt"):
            mapping.setdefault(path.stem, []).append(path)
    for paths in mapping.values():
        paths.sort(key=str)
    _ISIL_NAME_MAP = mapping
    return mapping


def _hit_type_names(result: dict, limit: int = 5) -> list[str]:
    """命中行里的 Type 名（类型命中给 name；方法/字段命中取 `Owner.Method` 的 Owner）。"""
    names: list[str] = []
    for row in result.get("types") or []:
        name = str(row.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    for row in result.get("members") or []:
        owner = str(row.get("full") or "").split(".", 1)[0].strip()
        if owner and owner not in names:
            names.append(owner)
    return names[:limit]


def _isil_type_file_lines(result: dict) -> tuple[list[str], list[str]]:
    """按命中的 Type 回查权威根里真实存在的 .txt → (报告行, 证据绝对路径 ≤5)。

    判据（都是现场量出来的，不是猜的）：路径 `is_file()`、字节数 `stat().st_size`。
    """
    types = _hit_type_names(result)
    if not types:
        return [], []
    mapping = _isil_name_map()
    if mapping is None:
        return ([
            "  - ⚠ 权威根不可用（`_work/isil*` 里没扫到 IsilDump）⇒ "
            f"用 {PY} {DECOMP_INDEX} query <Type> 再定位",
        ], [])
    lines: list[str] = []
    evidence: list[str] = []
    missing: list[str] = []
    for name in types:
        paths = mapping.get(name)
        if not paths:
            missing.append(name)
            continue
        path = paths[0]
        try:
            size = path.stat().st_size
        except OSError:
            missing.append(name)
            continue
        more = f"（同名 {len(paths)} 个，先读这个）" if len(paths) > 1 else ""
        lines.append(f"  - [类型文件] {path}  ({size:,} B){more}")
        evidence.append(str(path))
        if len(evidence) >= 5:
            break
    if missing:
        lines.append(
            "  - ⚠ " + " / ".join(missing) + " 在权威根里查不到对应的 .txt"
            "（可能是嵌套类/编译器生成）→ "
            f"用 {PY} {DECOMP_INDEX} query <Type> 或换符号"
        )
    if evidence:
        lines.append("  - 读法：先提控制流骨架，再对调用点还原状态（别逐行猜）")
        lines.append(
            "  - ⛔ 只引用上面的路径 = **没读**；下一步用 `ba_gate --kind read` 把函数体真读进来："
            "`--payload '{\"symbol\":\"<Type>.<Method>\"}'` "
            "或 `--payload '{\"file\":\"<上面那条路径>\"}'`"
        )
    return lines, evidence


# ──────────────────────────────────────────────────────────────────────────
# kind=read：把 ISIL 函数体**真读进上下文**（只引用路径 = 没读）
# ──────────────────────────────────────────────────────────────────────────

_MAX_READ_LINES = 120
_MAX_READ_CHARS = 2400
_ISIL_METHOD_RE = re.compile(r"^Method:\s*(.+?)\s*$")
_ISIL_TYPE_RE = re.compile(r"^Type:\s*(.+?)\s*$")
_RCX_DISASM_RE = re.compile(r"\[rcx\+([0-9A-Fa-f]+)h\]", re.IGNORECASE)
_RCX_ISIL_RE = re.compile(r"\[rcx\+(\d+)\]", re.IGNORECASE)


def _rcx_offsets(body: str) -> dict[int, str]:
    """函数体里 `rcx`（= this）偏移 → {字节偏移: 原始写法}（Disassembly 十六进制 / ISIL 十进制）。
    """
    offsets: dict[int, str] = {}
    for token in _RCX_DISASM_RE.findall(body or ""):
        with contextlib.suppress(ValueError):
            value = int(token, 16)
            offsets.setdefault(value, f"0x{value:X}")
    for token in _RCX_ISIL_RE.findall(body or ""):
        with contextlib.suppress(ValueError):
            value = int(token)
            offsets.setdefault(value, f"0x{value:X}")
    return offsets


def _field_offset_map(type_name: str) -> dict[int, tuple[str, str]]:
    """类型字段偏移表 {offset: (字段名, 类型)} —— 现场查 dump.cs 索引（⛔ 不猜、不硬编码）。"""
    short = type_name.rsplit(".", 1)[-1].strip()
    names = [short]
    if "_" in short:
        names.append(short.split("_", 1)[0])
    for name in names:
        if not name:
            continue
        try:
            result = decomp_index.query(name, decomp_index.INDEX_DIR, limit=400)
        except (OSError, ValueError):
            continue
        mapping: dict[int, tuple[str, str]] = {}
        for row in result.get("members") or []:
            if str(row.get("kind") or "") != "field":
                continue
            full = str(row.get("full") or "")
            if full.split(".", 1)[0] != name:
                continue
            extra = str(row.get("extra") or "").strip()
            if not extra.lower().startswith("0x"):
                continue
            with contextlib.suppress(ValueError):
                offset = int(extra, 16)
                mapping.setdefault(
                    offset, (full.split(".", 1)[-1], str(row.get("detail") or ""))
                )
        if mapping:
            return mapping
    return {}


def _field_offset_lines(
    offsets: dict[int, str], field_map: dict[int, tuple[str, str]]
) -> list[str]:
    """`[rcx+24h]` → 字段名 的对照段（查不到就明写"查不到"，⛔ 不许猜）。"""
    if not offsets:
        return []
    lines = ["── 字段偏移对照（rcx = this；⚙ 现场查 dump.cs 索引，⛔ 别猜）──"]
    mapped = 0
    for offset in sorted(offsets):
        hit = field_map.get(offset)
        if hit:
            mapped += 1
            lines.append(f"  {offsets[offset]}（={offset}） → {hit[0]} : {hit[1]}")
        else:
            lines.append(
                f"  {offsets[offset]}（={offset}） → ⚠ 表里没有（基类/嵌套/结构偏移）"
                "⇒ 用 DiffableCs 对，别猜"
            )
    lines.append(f"  {mapped}/{len(offsets)} 个偏移查到了字段名（查不到的不许猜）")
    lines.append("")
    return lines


def _isil_method_names(text: str) -> list[tuple[int, str]]:
    """文件里所有 `Method: 签名` → [(行号, 签名)]（Method 段里含 Disassembly / ISIL）。"""
    out: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        matched = _ISIL_METHOD_RE.match(line)
        if matched:
            out.append((number, matched.group(1)))
    return out


def _pick_owner_file(
    symbol: str, name_map: dict[str, list[Path]]
) -> tuple[Path | None, str, list[Path]]:
    """从 symbol 里挑 Owner 类型 → 权威根里的 .txt（**最长后缀匹配**，不按名字瞎猜）。"""
    parts = [part for part in symbol.replace("/", ".").split(".") if part]
    for index in range(len(parts) - 1, 0, -1):
        owner = ".".join(parts[:index])
        if owner in name_map:
            return name_map[owner][0], owner, name_map[owner]
    if symbol in name_map:
        return name_map[symbol][0], symbol, name_map[symbol]
    return None, "", []


def build_read(payload: dict) -> tuple[str, dict, list[str]]:
    """读 ISIL 函数体：payload = {file?: 路径, symbol?: Type.Method, lines?: N}。

    三条硬口径：
      · ⛔ 只读 `_work/isil*` 下的反编译产物（其它文件不许借证据闸的口当"证据"）；
      · 行号 / 字符数都是**现场读出来的**，不是估的；
      · 方法找不到时**列出该文件里的方法清单**（⛔ 不许说「反编译里没有」）。
    """
    symbol = str(payload.get("symbol") or "").strip()
    file_raw = str(payload.get("file") or "").strip()
    try:
        max_lines = int(payload.get("lines") or _MAX_READ_LINES)
    except (TypeError, ValueError):
        max_lines = _MAX_READ_LINES
    max_lines = max(20, min(max_lines, 600))

    path: Path | None = None
    owner = ""
    if file_raw:
        path = _abs_any(file_raw)
        owner = path.stem
    elif symbol:
        name_map = _isil_name_map()
        if name_map is None:
            return (
                "⛔ 权威根不可用（`_work/isil*` 里没扫到 IsilDump）—— 先跑 `ba_boot` 看 2.6 块；"
                "⛔ 不许因此说「反编译里没有」",
                {},
                ["权威根不可用"],
            )
        path, owner, candidates = _pick_owner_file(symbol, name_map)
        if path is None:
            return (
                "⛔ read 定位不到类型：symbol 写成 `Type.Method`"
                "（例 `SupplyConfig.GetAmmoResupplyMultiplier`），"
                "或把 dig 报告里的 `[类型文件] …txt` 路径塞进 `payload.file`。",
                {},
                ["symbol 里没有能查到 Owner 类型的部分"],
            )
        if len(candidates) > 1:
            owner = f"{owner}（同名 {len(candidates)} 个，先读第一个）"
    else:
        return (
            "⛔ read 需要 `payload.symbol`（Type.Method）或 `payload.file`"
            "（dig 报告里的 `[类型文件] …txt` 路径）。",
            {},
            ["read 缺 symbol/file"],
        )

    try:
        resolved = path.resolve()
        resolved.relative_to(WORK)
    except (OSError, ValueError):
        return (
            f"⛔ read 只读 `_work/isil*` 下的反编译产物（拒绝：{path}）—— "
            "其它文件用 read/grep 工具，别借证据闸的口。",
            {},
            [f"路径不在 _work 下：{path}"],
        )
    if resolved.suffix.lower() != ".txt":
        return (f"⛔ read 只读 ISIL `.txt`（拒绝：{resolved.name}）。", {}, ["不是 .txt"])
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (f"⛔ 读不动 {resolved}：{exc}", {}, [f"读不动：{exc}"])

    total_lines = len(text.splitlines())
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    type_matched = _ISIL_TYPE_RE.match(first)
    type_name = type_matched.group(1) if type_matched else resolved.stem

    method_hint = ""
    if symbol:
        method_hint = symbol.rsplit(".", 1)[-1].strip() if "." in symbol else symbol.strip()

    methods = _isil_method_names(text)
    start = end = 0
    signature = ""
    if method_hint:
        for line_number, sig in methods:
            if method_hint.lower() in sig.lower():
                start, signature = line_number, sig
                break
        if not start:
            listing = "\n".join(f"  {number}: Method: {sig}" for number, sig in methods[:20])
            listing = listing or "  （本文件没有 Method 段）"
            return (
                f"# 反编译实读 · 没定位到方法\n文件：{resolved}（全文 {total_lines} 行 · "
                f"{len(methods)} 个方法）\n要找的方法：{method_hint}\n\n"
                f"── 本文件里可用的方法（挑一个，⛔ 不许当成「查无此项」）──\n{listing}",
                {"file": str(resolved), "type": type_name, "method": "", "lines": 0},
                [
                    f"方法 {method_hint} 不在 {resolved.name} 的 Method 段里"
                    f"（该文件有 {len(methods)} 个方法）"
                ],
            )
        following = [number for number, _sig in methods if number > start]
        end = (following[0] - 1) if following else len(lines)
    else:
        listing = "\n".join(f"  {number}: Method: {sig}" for number, sig in methods[:20])
        listing = listing or "  （本文件没有 Method 段）"
        start, end = 1, min(len(lines), 24)
        report = (
            f"# 反编译实读 · {type_name}（类型级）\n文件：{resolved}（全文 {total_lines} 行 · "
            f"{len(methods)} 个方法）\n行：1-{end}\n\n── 文件头 ──\n"
            + "\n".join(lines[:end])
            + f"\n\n── 本文件里的方法（要读函数体就把 symbol 填成 `{type_name}.<方法>`）──\n"
            + listing
        )
        return (
            report,
            {"file": str(resolved), "type": type_name, "method": "", "start_line": 1,
             "end_line": end, "lines": end, "chars": len(report)},
            [],
        )

    block = lines[start - 1:end]
    #: 块首那行 `Method: …` 已经在上面「方法签名」段里了 —— 别重复，省 token。
    body_lines = block[1:] if block and _ISIL_METHOD_RE.match(block[0]) else list(block)
    truncated = False
    if len(body_lines) > max_lines:
        body_lines = body_lines[:max_lines]
        truncated = True
    body = "\n".join(body_lines)
    if len(body) > _MAX_READ_CHARS:
        body = body[:_MAX_READ_CHARS]
        truncated = True

    #: ⚙ 字段偏移对照：把 `[rcx+24h]` 这种机械地对到真实字段名（来源 = dump.cs 索引）。
    offsets = _rcx_offsets(body)
    field_map = _field_offset_map(type_name)
    field_section = _field_offset_lines(offsets, field_map)
    report = "\n".join([
        f"# 反编译实读 · {type_name}",
        f"文件：{resolved}",
        f"行：{start}-{start + len(block) - 1} / 全文 {total_lines} 行 · "
        f"本块 {len(body_lines)} 行 / {len(body)} 字符"
        + ("（截断 —— 改结论前把整段读完：`--payload '{\"file\":\"…\",\"lines\":400}'`）"
           if truncated else ""),
        "",
        "── 方法签名 ──",
        f"Method: {signature}",
        "",
        body,
        "",
        *field_section,
        "── 读法（AGENTS §0.4：通读 = 打开整个文件读）──",
        "· 这里是**入口**不是全文：要下结论就把这个方法段整段读完，别只看这一屏。",
        "· 上面的字段对照是现场查表；表里没有的偏移（基类/嵌套）⇒ 拿 DiffableCs 对，⛔ 别猜。",
        "· 先提骨架（Compare / Branch / Return），再对调用点还原数据流；⛔ 别逐行猜语义。",
    ])
    witness = {
        "file": str(resolved),
        "type": type_name,
        "method": signature,
        "start_line": start,
        "end_line": start + len(block) - 1,
        "lines": len(body_lines),
        "chars": len(body),
        "fields": len(field_map),
        "offsets": len(offsets),
    }
    return report, witness, []


# ──────────────────────────────────────────────────────────────────────────
# kind=plan：读经三行＋判据一行 —— 机械校验（必须能红）
# ──────────────────────────────────────────────────────────────────────────


def _fix_product(text: str) -> str:
    return f"product 要写成 `文件::函数`（例：tools/ba_boot.py::build_report），当前是「{text}」"


def _check_product(raw: object) -> tuple[bool, str, str]:
    text = str(raw or "").strip()
    if not text or "::" not in text:
        return (False, "", _fix_product(text or "（空）") + "；现在该跑：先确定要改的那个 "
                "`文件::函数`（相对仓根或绝对路径都认）")
    file_part, _, func_part = text.partition("::")
    file_part, func_part = file_part.strip(), func_part.strip()
    if not file_part or not func_part:
        return False, "", _fix_product(text) + "；现在该跑：`::` 两边都要写"
    path = _abs_any(file_part)
    if not path.is_file():
        return (False, "", f"产品文件不存在：{_rel_any(path)}；现在该跑：核对真正要改的文件"
                f"（相对仓根 {PROD.name} 或绝对路径），或先 `{PY} {THREE_DOORS} <函数名>` 定位")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, "", (f"产品文件读不动：{type(exc).__name__}；"
                          f"现在该跑：手动打开 {_rel_any(path)}")
    name = func_part.rstrip("()").split("(")[0].strip()
    if name not in content:
        return (False, "", f"文件里搜不到函数名「{name}」（{_rel_any(path)}）；现在该跑："
                f"Select-String -Path {_rel_any(path)} -Pattern '{name}' 核对真实函数名")
    detail = (f"{_rel_any(path)}::{name}（{_count_lines(path)} 行，"
              f"命中 {content.count(name)} 次）")
    return True, detail, ""


def _db_doc_search(needle: str, per_file: int = 2, max_files: int = 3):
    """四份列文档里搜 —— 遍历口径复用 three_doors.py 的唯一实现（⛔ 不复制那份判定）。"""
    paths = tuple(three_doors._text_files(three_doors.DB_DOCS, (".md",)))  # noqa: SLF001
    return three_doors._grep_any(paths, needle, per_file=per_file, max_files=max_files)  # noqa: SLF001


def _db_schema() -> dict[str, list[str]]:
    """`DataBaseCompiled/_schema.json` = 24 张表 → 列名清单（唯一数据源口径）。"""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE
    data: dict[str, list[str]] = {}
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(DB_SCHEMA.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = {str(key): [str(col) for col in cols]
                    for key, cols in raw.items() if isinstance(cols, list)}
    _SCHEMA_CACHE = data
    return data


def _db_column_found(table: str, column: str) -> tuple[bool, str]:
    """列名在「四份文档」或「_schema.json」里能找到吗 —— 任一找到即算（task-2 逐字口径）。"""
    schema = _db_schema()
    real_table = next((name for name in schema if name.lower() == table.lower()), "")
    if real_table and any(col.lower() == column.lower() for col in schema[real_table]):
        return True, f"_schema.json（{real_table} 表共 {len(schema[real_table])} 列）"
    hits, _used = _db_doc_search(column, per_file=1, max_files=1)
    if hits:
        path = hits[0][0]
        return True, f"{_rel_any(path)}（按列名搜到）"
    return False, ""


def _check_db(db_raw: object, number_raw: object) -> tuple[bool, str, str]:
    db = str(db_raw or "").strip()
    table, sep, column = db.partition(".")
    if not sep or not table.strip() or not column.strip():
        return (False, "", f"db 要写成 `表.列`（例：Units.Cost），当前是「{db or '（空）'}」；"
                f"现在该跑：{PY} {THREE_DOORS} <列名> 先查哪张表有这一列")
    table, column = table.strip(), column.strip()
    found, where = _db_column_found(table, column)
    if not found:
        known = " / ".join(sorted(_db_schema())[:6]) or "（_schema.json 读不到）"
        return (False, "", f"列名「{column}」在四份列文档与 _schema.json 里都找不到"
                f"（表「{table}」，已登记表如：{known}…）；现在该跑："
                f"{PY} {THREE_DOORS} {column} 换列名，"
                f"或按列清单 {_rel_any(PROD / 'docs' / '数据-表与列清单.md')} 找")
    number = _as_number(number_raw)
    if number is None:
        return (False, "", f"dbNumber 不是数字：「{number_raw}」"
                f"（读经那行要一条**实跑出来**的数字，不是口号）；现在该跑："
                f"{PY} {THREE_DOORS} {db} 取一条实数，"
                "例：615 个槽 / 436 个没有 IsDefault")
    return True, f"{db} ✔（来源：{where}）· 实跑数字 {number:g}", ""


def _check_decompile(raw: object, basis_raw: object) -> tuple[bool, str, str]:
    text = str(raw or "").strip()
    if not text:
        return (False, "", "decompile 空着；写 `_work/isil/…/X.txt:行号`，"
                f"或明写「没读」+ decompileBasis；现在该跑：{PY} {DECOMP_INDEX} query <符号>")
    if text.startswith("没读"):
        basis = str(basis_raw or "").strip()
        if not basis:
            return (False, "", "写了「没读」但没给 decompileBasis —— AGENTS §0.4：缺就明写没读＋"
                    "依据是哪份文档；现在该跑：填 decompileBasis=kb/…md（真实存在的），"
                    f"或真去读：{PY} {DECOMP_INDEX} query <符号>")
        path = _abs_any(basis)
        if not path.is_file():
            return (False, "", f"依据文档不存在：{basis}；现在该跑：换成真实存在的 kb/ 或 docs/ "
                    "下的 .md（例：kb/14-本机工具清单.md）")
        rel = ""
        with contextlib.suppress(ValueError):
            rel = path.resolve().relative_to(PROD).as_posix()
        if not rel or not rel.startswith(("kb/", "docs/")):
            return (False, "", f"依据要指向 kb/ 或 docs/ 下真实存在的 .md，当前是「{basis}」；"
                    "现在该跑：换成 kb/… 或 docs/… 的一份文档")
        return True, f"没读 · 依据 {rel}（存在）", ""
    path_text, sep, line_text = text.rpartition(":")
    if not sep or not line_text.strip().isdigit():
        return (False, "", f"反编译证据要带行号 `路径:行号`（例：_work/isil/…/X.txt:123），"
                f"当前是「{text}」；现在该跑：{PY} {DECOMP_INDEX} query <符号> 定位后再填")
    path = _abs_any(path_text)
    if not path.is_file():
        return (False, "", f"反编译文件不存在：{path_text}；现在该跑："
                f"{PY} {DECOMP_INDEX} query <符号> 拿真实 isil 路径（索引 {DECOMP_INDEX_DIR}）")
    total = _count_lines(path)
    line = int(line_text.strip())
    if line < 1 or line > total:
        return (False, "", f"行号越界：{line} 不在 1..{total}（{_rel_any(path)}）；现在该跑："
                f"{PY} {DECOMP_INDEX} query <符号> 重新定位行号，别拿旧行号凑")
    return True, f"{_rel_any(path)}:{line}（文件共 {total} 行）", ""


def _check_decompile_honest(payload: dict) -> tuple[bool, str, str]:
    """反编译那一行必须与**本轮实读**对得上（★ v3.1）。

    用户 2026-11 原话：「总是喜欢改点东西，让我开游戏试，喜欢偷懒不读汇编代码」。
    v3.0 的漏洞：`decompile` 写「没读」+ 一份 kb 依据就能过闸 ⇒ 「读了」和「没读」长得一样；
    甚至 read 完还写「没读」也没人管。三条机械规则（都是**能红**的）：

      ① 本轮 `kind=read` 过 ⇒ **不许**再写「没读」（读了却写没读 = 假账）；
      ② 本轮是机制/字段类问题 ⇒ **不许**写「没读」（要么真 read，要么 `kind=waive` 留痕）；
      ③ 写了 `路径:行号` ⇒ 必须落在本轮 read 的那一段里（读 A 引 B = 没读）。
    """
    text = str(payload.get("decompile") or "").strip()
    mech = bool(payload.get("mech"))
    evidence = payload.get("readEvidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    read_file_raw = str(evidence.get("file") or "").strip()
    if text.startswith("没读"):
        if read_file_raw:
            return (
                False,
                "",
                f"本轮已经 read 过 {read_file_raw}，读经却写「没读」= 假账；现在该跑："
                "把 read 报告里的 `文件:行号` 填进 decompile（例：`…/X.txt:123`），"
                "或把 read 的结论一句话写进去",
            )
        if mech:
            return (
                False,
                "",
                "本轮是机制/字段类问题却写「没读」= 偷懒（用户 2026-11 投诉的就是这个）；"
                "现在该跑：`ba_gate.py --kind read --symbol \"<Type>.<Method>\"` 真读函数体，"
                "或者 `ba_gate.py --kind waive reason=\"…\"` 留痕豁免"
                "（收尾 STATUS 必须点名无证据）",
            )
        return True, "「没读」（非机制类；依据见上一行）", ""
    if not read_file_raw:
        #: 本轮没走 read 却给了真路径：那条路径由 _check_decompile 校验存在性与行号范围，
        #: 这里只认「本轮读过的」那一份，不做额外推断。
        return True, "", ""
    path_text, sep, line_text = text.rpartition(":")
    if not sep:
        return True, "", ""
    try:
        cited = _abs_any(path_text).resolve()
        read_abs = _abs_any(read_file_raw).resolve()
    except OSError:
        return True, "", ""
    if cited != read_abs:
        return (
            False,
            "",
            f"decompile 引的是 {cited.name}，本轮 read 的是 {read_abs.name} —— "
            "读 A 引 B = 没读；现在该跑：read 你真正要引的那个类型"
            "（或把 decompile 改成 read 的那个文件）",
        )
    number = _as_number(line_text)
    start = _as_number(evidence.get("startLine"))
    end = _as_number(evidence.get("endLine"))
    if number is None or start is None or end is None:
        return True, "", ""
    if not (int(start) <= int(number) <= int(end)):
        return (
            False,
            "",
            f"行号 {int(number)} 不在本轮 read 的 {int(start)}-{int(end)} 段里"
            f"（{read_abs.name}）—— 引的行要是你真读过的；现在该跑："
            "`ba_gate.py --kind read --symbol \"…\" --lines 400` 把那段读进来再引",
        )
    return True, f"与 read 对得上（{read_abs.name}:{int(number)} ∈ {int(start)}-{int(end)}）", ""


def _check_judge(judge_raw: object, before_raw: object, after_raw: object) -> tuple[bool, str, str]:
    judge = str(judge_raw or "").strip()
    if not judge:
        return (False, "", "judge（判据一行）空着 —— 写成「<命令> → 读数从 X 变 Y」；"
                "现在该跑：按 AGENTS §0.9 动作 1 写出可复算的判据，再回来过闸")
    before = _as_number(before_raw)
    after = _as_number(after_raw)
    if before is None or after is None:
        return (False, "", f"before/after 必须是数字"
                f"（当前 before={before_raw!r} after={after_raw!r}）；"
                "现在该跑：先量出改前/改后的两个读数（机械尺子，别写“看起来”）")
    if before == after:
        return (False, "", f"判据恒真：before == after == {before:g} ⇒ 假绿，这条尺子永远红不了；"
                "现在该跑：换一个**会变**的读数，或先做正控（喂一个已知违规的点看它红不红）")
    return True, f"{judge} → {before:g} → {after:g}（不同 ✔）", ""


_SRC_TARGET_RE = re.compile(r"(\d+)\s*[.:]\s*([A-Za-z_]\w*)")


def _check_source(payload: dict) -> tuple[bool, str, str]:
    r"""v3.3（2026-11-19）：改「某一列 / 某一个值」必须写清**它从哪来**。

    为什么要有这一格（用户当晚原话：「你怎么总是避免不了猜公式？」）：
      模型的根因是「连贯性最大化」——输入没确定就写输出，而且自己察觉不到。
      所以这一格把「触发事件 + 来源消息 + 单位」变成**必填**，并要求本轮跑过 kind=field
      把该字段在官方样本里的**分布**打出来（量纲由工具吐，不由模型判）。

    不涉及任何线上字段值的改动（纯配置 / 纯工具链），写 `不适用：<理由>` 放行 —— 留痕不封路。"""
    src = str(payload.get("source") or "").strip()
    if not src:
        return (False, "",
                "缺 source：写成「<消息码>.<字段号> = <含义>（单位：个数/点数/秒/比率）」，"
                "例：120.f2 = 阵亡单位总价值（点数），来自 214 单位摧毁事件；"
                "确实不涉及字段值就写「不适用：<理由>」")
    if src.startswith("不适用"):
        if len(src) < 10:
            return (False, "", "「不适用」后面要跟一句理由（≥6 字）")
        return (True, "不适用（" + src[3:].lstrip("：: ")[:40] + "）", "")
    m = _SRC_TARGET_RE.search(src)
    if not m:
        return (False, "", "source 里找不到 `码.字段`（例 120.f2）—— 现在写的是「" + src[:50] + "」")
    target = m.group(1) + "." + m.group(2)
    ev = payload.get("fieldEvidence")
    if not isinstance(ev, dict) or not ev:
        return (False, "",
                "本轮没跑过 kind=field：先跑 "
                + str(PY) + " " + str(GATE) + " --kind field --payload '{\"target\":\"" + target
                + "\",\"command\":\"<一条把该字段所有官方取值打出来的命令>\"}'")
    ev_target = str(ev.get("target") or "").strip()
    if ev_target != target:
        return (False, "", "field 读数是「" + (ev_target or "(空)") + "」，与 source 里的「" + target + "」不一致")
    try:
        n = int(ev.get("readings") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return (False, "", "field 读数里没有取到样本（readings=0）—— 换一份抓包，或走 waive 留痕")
    return (True, target + "（" + str(n) + " 条官方样本，min=" + str(ev.get("min")) + " max=" + str(ev.get("max")) + "）", "")


def check_plan(payload: dict) -> tuple[bool, str, list[str]]:
    """校验读经三行＋判据一行。返 (ok, report, fail[])；fail[] 每条 = ❌ + 现在该跑什么。"""
    fails: list[str] = []
    rows: list[str] = []

    checks = (
        ("产品代码", _check_product(payload.get("product"))),
        ("数据库", _check_db(payload.get("db"), payload.get("dbNumber"))),
        ("反编译", _check_decompile(payload.get("decompile"), payload.get("decompileBasis"))),
        ("反编译·实读对账", _check_decompile_honest(payload)),
        ("判据", _check_judge(payload.get("judge"), payload.get("before"), payload.get("after"))),
        ("数据来源", _check_source(payload)),
    )
    for label, (good, detail, fix) in checks:
        if good:
            rows.append(f"✅ {label}：{detail}")
        else:
            note = f"❌ {label}：{fix}"
            rows.append(note)
            fails.append(note)

    #: ★ 判据必须**实测过**（v2.2）：插件把 verify 的现场记录塞进 judgeEvidence，
    #:   对不上就红 —— 「judge 是编的 / before 是编的」在这里被挡下。
    evidence = payload.get("judgeEvidence")
    if isinstance(evidence, dict) and evidence:
        ev_cmd = str(evidence.get("command") or "").strip()
        ev_sha = str(evidence.get("sha") or "").strip()
        ev_matched = bool(evidence.get("beforeMatched"))
        def _norm(text: object) -> str:
            return re.sub(r"\s+", " ", str(text or "")).strip()

        bad = ""
        if not ev_cmd or not ev_sha:
            bad = "判据证据不完整（缺 command / sha）"
        elif _norm(ev_cmd) not in _norm(payload.get("judge")):
            bad = f"judge 与实测命令对不上（实测的是：「{ev_cmd}」）"
        elif not ev_matched:
            bad = "before 没出现在实测输出里（像是编的数字）"
        if bad:
            note = (
                f"❌ 判据（实测证据）：{bad}；现在该跑："
                f"{PY} {GATE} --kind verify --payload "
                f"'{{\"command\":\"<命令>\",\"before\":<X>}}' 重跑"
            )
            rows.append(note)
            fails.append(note)
        else:
            rows.append(f"✅ 判据（实测）：输出sha256={ev_sha}…（现场执行、before 命中输出）")

    head = ["# plan · 读经三行＋判据一行（机械校验；tools/ba_gate.py 唯一实现）"]
    if fails:
        head.append(f"共 {len(fails)} 条不过 —— 逐条修完再跑："
                    f"{PY} {GATE} --kind plan --payload '<改好的 JSON>'")
    else:
        head.append("✅ 读经三行＋判据＋实读对账全过：改前门已过"
                    "（插件会把这次记账，之后本 turn 才允许改仓内源码）")
    head.append("")
    head.append("读经（改代码之前）")
    head.extend(rows)
    return (not fails), "\n".join(head), fails




# ──────────────────────────────────────────────────────────────────────────
# kind=verify：把判据**真跑一遍**（before 必须出现在现场输出里 —— 编不过去）
# ──────────────────────────────────────────────────────────────────────────

_MAX_VERIFY_BYTES = 20000
_VERIFY_TIMEOUT_DEFAULT = 60.0


_FIELD_INT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def build_field(payload: dict) -> tuple[bool, str, dict, list[str]]:
    r"""v3.3（2026-11-19）：把**某个字段在官方样本里的分布**打出来 —— 量纲由工具吐，不由模型判。

    为什么要它（用户原话：「你怎么总是避免不了猜公式？」/「死亡是计分的！！！」）：
      同一条消息里 765/410 与 1..11 的量级差一眼就能分；问题是我从来没把分布**打在眼前**。
      这一条跑一条命令，把那堆数字抽出来算 readings / min / max，写进账本；
      plan 的「数据来源」那一格必须引用这条读数（对不上就拒）。
    命令由调用方给：它负责按 `码.字段` 解包官方抓包，把每个样本的值打一行。"""
    target = str(payload.get("target") or "").strip()
    cmd = str(payload.get("command") or "").strip()
    fails: list[str] = []
    if not target or not _SRC_TARGET_RE.search(target):
        fails.append("field 需要 target，写法 `<消息码>.<字段>`（例 120.f2）")
    if not cmd:
        fails.append("field 需要 command：一条把该字段所有官方取值打出来的只读命令")
    if fails:
        return (False, "# field · 字段分布读数\n" + "\n".join("❌ " + f for f in fails), {}, fails)

    try:
        timeout_s = float(payload.get("timeoutMs") or 0) / 1000.0 or _VERIFY_TIMEOUT_DEFAULT
    except (TypeError, ValueError):
        timeout_s = _VERIFY_TIMEOUT_DEFAULT
    timeout_s = max(5.0, min(timeout_s, 300.0))
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                              capture_output=True, timeout=timeout_s, cwd=str(PROD))
        rc = proc.returncode
        out = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        rc, out = -1, "命令跑不起来：" + str(exc)
    nums: list[float] = []
    for line in out.splitlines():
        for tok in _FIELD_INT_RE.findall(line):
            try:
                nums.append(float(tok))
            except ValueError:
                pass
    sha = hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()
    witness = {
        "target": target,
        "command": cmd,
        "exit": rc,
        "sha": sha,
        "readings": len(nums),
        "min": (min(nums) if nums else None),
        "max": (max(nums) if nums else None),
    }
    rows = ["# field · 字段分布读数（现场执行；值域由命令输出决定）",
            "字段：" + target, "命令：" + cmd,
            "退出码：" + str(rc) + " · 取值 " + str(len(nums)) + " 个 · 输出sha256=" + sha[:16],
            "值域：min=" + str(witness["min"]) + "  max=" + str(witness["max"]), ""]
    rows.append("── 原始输出（前 20 个非空行）──")
    shown = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        rows.append("  " + _cap(line.strip(), 200))
        shown += 1
        if shown >= 20:
            break
    if rc != 0:
        fails.append("命令退出码 " + str(rc) + " —— 读数不可用")
    if not nums:
        fails.append("输出里没有数字 —— 这条命令没把字段值打出来")
    rows.append("")
    rows.append("口径：这行是**现场跑**出来的；plan 的「数据来源」必须引用它（target 对不上就拒）。")
    return (not fails, "\n".join(rows), witness, fails)


def build_verify(payload: dict) -> tuple[bool, str, dict, list[str]]:
    """执行判据命令，记录**原始输出**：退出码 / sha256 / 字节数 / before 是否命中。

    为什么要有它（用户 2026-10-25 原话）：「很多绿的红的抛出的都是假信息，
    很多都是为了编一个有进展的解释」—— 旧 plan 里 `judge` 与 before/after **全是模型手打的**，
    编一条不存在的命令也能绿（实测）。verify 把「判据」变成**可复算的现场执行**：
    before 必须在命令的真实输出里出现过，否则判据就是编的。
    """
    command = str(payload.get("command") or "").strip()
    if not command:
        return (
            False,
            "⛔ verify 需要 payload.command（要跑的那条判据命令，完整命令行；例："
            f'`{PY} {FIND_CARD_ROW} 无视掩体` 或 '
            f"`Get-Content STATUS.md | Select-Object -First 3`）",
            {},
            ["verify 缺 command"],
        )
    before_raw = payload.get("before")
    before_text = "" if before_raw in (None, "") else str(before_raw).strip()
    try:
        timeout_s = float(payload.get("timeoutMs") or 0) / 1000.0 or _VERIFY_TIMEOUT_DEFAULT
    except (TypeError, ValueError):
        timeout_s = _VERIFY_TIMEOUT_DEFAULT
    timeout_s = max(5.0, min(timeout_s, 180.0))

    args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    started = time.perf_counter()
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout_s, cwd=str(PROD))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            False,
            f"⛔ verify 跑不动 / 超时（{type(exc).__name__}，{timeout_s:.0f}s）：{command}\n"
            "   —— 换一条更短的只读命令，或先手动确认它能跑。⛔ 不许把「跑不动」写成「没问题」。",
            {},
            [f"verify 跑不动：{exc}"],
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    out = (proc.stdout or b"")[:_MAX_VERIFY_BYTES].decode("utf-8", "replace")
    err = (proc.stderr or b"")[:_MAX_VERIFY_BYTES].decode("utf-8", "replace")
    combined = out + ("\n" + err if err.strip() else "")
    sha = hashlib.sha256(combined.encode("utf-8", "replace")).hexdigest()[:16]
    matched = bool(before_text) and before_text in combined
    head = [line.rstrip() for line in out.splitlines() if line.strip()][:20]
    exit_code = int(proc.returncode)

    fails: list[str] = []
    if exit_code != 0:
        fails.append(f"命令退出码 {exit_code}（不是 0）—— 这条判据不算实测；先修命令或换命令")
    if before_text and not matched:
        fails.append(
            f"before={before_text} **没出现在命令输出里** —— 这个数字是编的；"
            f"换一条真能打印出它的命令，或按真实输出改 before"
        )

    report = "\n".join(
        [
            "# 判据实测 · verify（现场执行；编不过去）",
            f"命令：{command}",
            f"退出码：{exit_code} · 耗时 {elapsed_ms:.0f} ms · "
            f"输出 {len(combined)} 字符 · 输出sha256={sha}",
            (f"before：{before_text} → " + ("✅ 在输出里命中" if matched else "⛔ 没命中"))
            if before_text
            else "before：（未给 —— 只证明命令能跑，不证明读数）",
            "",
            "── 原始输出（stdout 前 20 个非空行）──",
            *([f"  {line}" for line in head] or ["  （stdout 为空）"]),
            *(["── stderr（前 10 行）──", *[f"  {line}" for line in err.splitlines()[:10]]]
              if err.strip() else []),
            "",
            "── 口径 ──",
            "· 这行记录是本命令**现场跑**出来的：退出码 / 输出 / sha 都不是模型写的。",
            "· plan 会要求 judge 指向这条 verify、且 before 在输出里命中 ⇒ 「判据一行」不再能编。",
            "· ⛔ 别拿一条与改动无关的命令来凑数 —— plan 会比对 judge 文本与实测命令。",
        ]
    )
    witness = {
        "command": command,
        "exitCode": exit_code,
        "sha": sha,
        "bytes": len(combined),
        "elapsedMs": round(elapsed_ms, 1),
        "matched": matched,
        "before": before_text,
    }
    return (not fails), report, witness, fails

# ──────────────────────────────────────────────────────────────────────────
# kind=dig：一条命令出证据包（四个门都复用现成实现）
# ──────────────────────────────────────────────────────────────────────────


def _kb_corpus() -> list:
    global _KB_DOCS_CACHE
    if _KB_DOCS_CACHE is None:
        _KB_DOCS_CACHE = kb_lookup.load()
    return _KB_DOCS_CACHE


def _kb_hits_line(hit: dict) -> str:
    tag = "强" if hit.get("strong") else "弱"
    line_no = hit.get("line_no") or 0
    text = str(hit.get("line") or "").strip().replace("\n", " ")
    return f"  - [{tag}] {hit.get('rel', '')}:{line_no}  {text[:96]}"


def _render_strong_blocks(rendered: str, per: int = 3) -> list[str]:
    """从 kb_lookup.render 的输出里截「强命中」前 per 篇（保住标题/段落/file:line 摘录）。

    ⛔ 只裁渲染件，不重写检索判定 —— 唯一实现是 kb_lookup.search（AGENTS §5）。
    """
    lines = rendered.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("── 强命中")), None)
    if start is None:
        return []
    out: list[str] = []
    seen = 0
    for line in lines[start:]:
        if line.startswith("── 弱命中") or line.startswith(kb_lookup.VERDICT_MARK):
            break
        if re.match(r"^\s*\d+\.\s", line):
            seen += 1
            if seen > per:
                break
        if line.strip():
            out.append(line.rstrip()[:160])
    return out if seen else []


def _kb_door(query: str) -> tuple[list[str], int]:
    docs = _kb_corpus()
    result = kb_lookup.search(query, docs)
    hits = list(result.get("hits") or [])
    strong = [hit for hit in hits if hit.get("strong")]
    weak = [hit for hit in hits if not hit.get("strong")]
    lines = [f"【门0 知识库】命中 {len(hits)} 处（强命中 {len(strong)} 篇 · 弱 {len(weak)}）"]
    if strong:
        lines.extend(_render_strong_blocks(kb_lookup.render(result, query), per=3))
        lines.append(f"  - 完整报告：{PY} {KB_LOOKUP} \"{query}\"")
    elif weak:
        lines.append("  弱命中（只是同词、不代表相关；强命中 0 ⇒ 先换说法再搜）：")
        lines.extend(_kb_hits_line(hit) for hit in weak[:3])
    else:
        lines.append("  - 0 命中 = 本工具没查到（词表鸿沟是常态："
                     "你说「外观」，库里可能写「皮肤/涂装」）")
        lines.append("  - ⛔ 0 命中不能证明知识库没写过 —— 换说法，或按入口清单点："
                     f"{PY} {KB_LOOKUP} \"<换个说法>\"")
    return lines, len(hits)


def _db_door(query: str) -> tuple[list[str], int]:
    hits, used = _db_doc_search(query, per_file=2, max_files=3)
    count = sum(len(found) for _path, found in hits)
    lines = [f"【门2 数据库（先查列，再翻反编译）】四份列文档命中 {count} 处"
             + (f"（按「{used}」搜到）" if hits else "")]
    shown = 0
    for path, found in hits:
        for number, text in found:
            if shown >= 3:
                break
            lines.append(f"  - {_rel_any(path)}:{number}  {text.strip()[:100]}")
            shown += 1
        if shown >= 3:
            break
    lines.extend(_schema_evidence(query)[0])
    if count == 0:
        lines.append("  - 换**列名**再搜（Dispersion / IgnoreCover 这类一搜就有）；列清单 = "
                     f"{_rel_any(PROD / 'docs' / '数据-表与列清单.md')}")
    lines.append(f"  - 下一跳（把「这一行在哪」解出来）：{PY} {FIND_CARD_ROW} {query}")
    return lines, count


def _schema_evidence(query: str) -> tuple[list[str], int]:
    schema = _db_schema()
    if not schema:
        return [], 0
    text = query.strip()
    if "." in text:
        table, _, column = text.partition(".")
        real_table = next((name for name in schema if name.lower() == table.strip().lower()), "")
        if not real_table:
            return [f"  - _schema.json：表「{table.strip()}」不在 {len(schema)} 张表里"], 0
        columns = schema[real_table]
        if any(col.lower() == column.strip().lower() for col in columns):
            return [f"  - _schema.json：{real_table}.{column.strip()} ✔（列真实存在，"
                    f"该表共 {len(columns)} 列）"], 1
        return [f"  - _schema.json：{real_table} 的 {len(columns)} 列里查不到"
                f"「{column.strip()}」"], 0
    tables = [name for name, columns in schema.items()
              if any(col.lower() == text.lower() for col in columns)]
    if not tables:
        return [], 0
    shown = " / ".join(tables[:5])
    extra = f" 等 {len(tables)} 张表" if len(tables) > 5 else ""
    return [f"  - _schema.json：列「{text}」出现在 {shown}{extra}"], len(tables)


def _card_door(query: str) -> tuple[list[str], int]:
    head = "【卡片行 / ISIL 读点】（中文名或本地化键才走这扇门）"
    if not _CJK_RE.search(query) and not query.startswith("ui_"):
        return [head, "  - 关键词不是中文/本地化键 ⇒ 跳过 find_card_row.py；"
                      "英文标识符看门3 反编译索引"], 0
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        proc = subprocess.run(
            [sys.executable, str(FIND_CARD_ROW), query],
            cwd=str(PROD), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=FIND_CARD_TIMEOUT, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        return [head, f"  - ⚠ find_card_row.py 超时（>{FIND_CARD_TIMEOUT:.0f}s）—— "
                      "这不等于卡片行不存在；换本地化键再试"], 0
    except OSError as exc:
        return [head, f"  - ⚠ find_card_row.py 跑不动：{type(exc).__name__}；"
                      f"现在该跑：{PY} {FIND_CARD_ROW} --list 先看键表"], 0
    text = (proc.stdout or "") + (proc.stderr or "")
    output = text.splitlines()
    key_line = next((line.strip("= ") for line in output if line.startswith("===")), "")
    sites: list[tuple[str, int, str]] = []
    for index, line in enumerate(output):
        match = re.match(r"^\s+(\S+\.txt):(\d+)\s*$", line)
        if not match:
            continue
        excerpt = ""
        for follow in output[index + 1: index + 3]:
            if follow.strip():
                excerpt = follow.strip()
                break
        sites.append((match.group(1), int(match.group(2)), excerpt))
    lines = [head]
    if key_line:
        lines.append(f"  - {key_line}")
    for path, line_no, excerpt in sites[:3]:
        lines.append(f"  - {_rel_any(PROD / path)}:{line_no}  {excerpt[:96]}")
    if not sites:
        lines.append(f"  - 0 读点（rc={proc.returncode}）—— 换本地化键再试："
                     f"{PY} {FIND_CARD_ROW} --list（键 → 字段偏移全表）")
    return lines, len(sites)


def _trim_decomp_render(rendered: str, per: int = 3) -> list[str]:
    """从 decomp_index.render 的输出里每类截前 per 条（定位清单，不是结论）。"""
    out: list[str] = []
    seen = 0
    for line in rendered.splitlines():
        if line.startswith("── "):
            out.append(line.rstrip())
            seen = 0
            continue
        if line.startswith("   0 命中") or line.startswith("   以上是"):
            break
        if line.startswith("   ") and line.strip() and seen < per:
            out.append(line.rstrip()[:160])
            seen += 1
    return out


def _dec_door(query: str) -> tuple[list[str], int, list[str]]:
    if not (DECOMP_INDEX_DIR / decomp_index.TYPES_TSV).is_file():
        return ([
            "【门3 反编译】索引未建（_work/decomp-index/）",
            f"  - 先跑：{PY} {DECOMP_INDEX} build（一次性，约 12 秒）",
            "  - ⛔ 索引不在 = 还没查 —— 这不等于反编译里不存在",
        ], 0, [])
    result = decomp_index.query(query, DECOMP_INDEX_DIR)
    n_types = len(result["types"])
    n_members = len(result["members"])
    n_files = len(result["files"])
    total = n_types + n_members + n_files
    lines = [f"【门3 反编译】索引命中 {total} 处"
             f"（类型 {n_types} · 字段/方法 {n_members} · ISIL 文件 {n_files}）"]
    rendered = _trim_decomp_render(decomp_index.render(result, DECOMP_INDEX_DIR), per=3)
    #: ★ 索引只给「dump.cs 里有没有」；**有函数体的 .txt** 必须现场回查权威根。
    file_lines, isil_evidence = _isil_type_file_lines(result)
    if file_lines:
        merged: list[str] = []
        in_section3 = False
        for line in rendered:
            if line.startswith("── ③"):
                in_section3 = True
                merged.append(line)
                merged.extend(file_lines)
                continue
            if in_section3 and line.strip() == "（没命中）":
                continue  # 已经有真实 .txt 了，别再印「没命中」把人劝退
            merged.append(line)
        rendered = merged
    lines.extend(rendered)
    if total == 0:
        lines.append("  - 0 命中 = 本工具没查到（中文症状先用门0/门2 换成标识符）；"
                     "⛔ 不能证明反编译里不存在，换类名/字段名/方法名再挖")
    lines.extend(_index_freshness_lines())
    #: witness 的 dec_files：**真实存在的 ISIL .txt 绝对路径**优先（插件账本把它当证据）。
    files = list(dict.fromkeys([*isil_evidence, *[row["rel"] for row in result["files"]]]))
    return lines, total, files[:5]


def build_dig(query: str) -> tuple[str, dict, list[str]]:
    """一条命令的**证据包**：四扇门各给「命中 N 处 + 前 3 条 file:line + 摘录」+ 读经三行。"""
    text = query.strip()
    parts = [
        f"# 证据包 · dig「{text}」",
        "（只读 · 检索口径全部复用现成实现：kb_lookup / three_doors 列文档 / "
        "find_card_row / decomp_index）",
    ]
    fail: list[str] = []
    kb_lines, kb_hits = _kb_door(text)
    db_lines, db_hits = _db_door(text)
    card_lines, _card_hits = _card_door(text)
    dec_lines, dec_hits, dec_files = _dec_door(text)
    for block in (kb_lines, db_lines, card_lines, dec_lines):
        parts.extend(block)
        parts.append("")
    parts.append("⚠️ 通读 = 打开命中文件**整个读**（不是只读命中那几行）；结论写回 kb/ 才算读过。")
    parts.append("")
    parts.append("读经三行（改代码之前；缺哪样就写「没读」）：")
    parts.append(three_doors.READING_TEMPLATE)
    report = _cap(_sanitize_absence("\n".join(parts)))
    authority = _isil_authority()
    witness = {
        "dec_hits": dec_hits,
        "dec_files": dec_files[:5],
        "db_hits": db_hits,
        "kb_hits": kb_hits,
        "isil_root": authority["rel"] if authority else "",
    }
    if dec_hits == 0 and db_hits == 0 and kb_hits == 0:
        fail.append(
            "四扇门都 0 命中 —— 这不是「反编译/知识库里不存在」，是关键词还没落到标识符上；"
            f"换词再跑：{PY} {GATE} --kind dig --query \"<类名/字段名/方法名>\""
        )
    return report, witness, fail


# ──────────────────────────────────────────────────────────────────────────
# kind=unity：渲染闸预检 / 产物 mtime 验收（⛔ 不起 Unity，⛔ 不查进程）
# ──────────────────────────────────────────────────────────────────────────


def _newest_file(directory: Path) -> Path | None:
    try:
        files = [path for path in directory.iterdir() if path.is_file()]
    except OSError:
        return None
    newest: tuple[float, Path] | None = None
    for path in files:
        try:
            ts = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or ts > newest[0]:
            newest = (ts, path)
    return newest[1] if newest else None


def _bundle_candidates(limit: int = 8) -> list[tuple[Path, float, float]]:
    """`_work` 下最新 limit 个 .bundle（大小 MB + mtime）。"""
    rows: list[tuple[Path, float, float]] = []
    with contextlib.suppress(OSError):
        for path in WORK.rglob("*.bundle"):
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append((path, stat.st_size / 1e6, stat.st_mtime))
    rows.sort(key=lambda row: row[2], reverse=True)
    return rows[:limit]


def _newest_render_artifact() -> Path | None:
    """`_work` 下所有 render/ 产物目录里最新的那个文件（since 校验的兜底口径）。"""
    newest: tuple[float, Path] | None = None
    with contextlib.suppress(OSError):
        for directory in WORK.glob("**/render"):
            if not directory.is_dir():
                continue
            candidate = _newest_file(directory)
            if candidate is None:
                continue
            try:
                ts = candidate.stat().st_mtime
            except OSError:
                continue
            if newest is None or ts > newest[0]:
                newest = (ts, candidate)
    return newest[1] if newest else None


def _precheck_render(payload: dict) -> tuple[bool, str, dict, list[str]]:
    bundle_raw = str(payload.get("bundle") or "").strip()
    now_ms = int(time.time() * 1000)
    fail: list[str] = []
    bundle: Path | None = None
    lines = ["# 渲染闸预检（⛔ 本脚本**不起 Unity**，也不查进程状态）"]
    if bundle_raw:
        candidate = _abs_any(bundle_raw)
        if candidate.is_file():
            bundle = candidate
            lines.append(f"bundle：{_rel_any(bundle)} ✔（{bundle.stat().st_size / 1e6:.1f} MB）")
        else:
            lines.append(f"❌ bundle 不在：{bundle_raw}")
            fail.append(f"bundle 不在：{bundle_raw} —— 现在该跑：从下面候选里挑真实 .bundle 路径")
    else:
        lines.append("bundle：未给（下面给 _work 候选）")
    target = str(bundle) if bundle else "<bundle 路径>"
    out_dir = f"{_rel_any(bundle.parent / 'render')}" if bundle else "<bundle 父目录>\\render"
    lines.append("跑法（**绝对命令**，照抄；⛔ 别用 steam:// 起游戏）：")
    lines.append(f"  {PY} {UNITY_RENDER} {target}")
    lines.append(f"  {PY} {UNITY_RENDER} {target} --builtin     # 只看 UV/贴图（材质换 Standard）")
    lines.append(f"产物：{out_dir}\\hdrp-render.txt + hdrp-render-<视图>.png"
                 "（--builtin 前缀 = unity-render）")
    lines.append("跑完回手验产物（判据 = mtime，不是退出码）：")
    lines.append(
        f"  {PY} {GATE} --kind unity --payload "
        f"'{{\"bundle\":\"{target}\",\"since\":{now_ms},\"artifact\":\"<上面某个产物>\"}}'"
    )
    lines.append("⚠️ 跑前关掉那个工程的编辑器（Unity 单实例会静默交接 ⇒ 这轮白跑）")
    lines.append("⚠️ 判据用**产物 mtime**（batchmode 退出码不可靠，kb/14 §6）")
    lines.append("⚠️ 游戏在不在跑**只能用 DSH pwsh 直接查**："
                 "@(Get-Process -Name BrokenArrow -EA SilentlyContinue).Count "
                 "—— 本脚本测不到（AGENTS §0.17）")
    if bundle is None:
        candidates = _bundle_candidates(8)
        lines.append("候选 bundle（_work 下最新 8 个）：")
        if candidates:
            for path, mbytes, ts in candidates:
                lines.append(f"  - {_rel_any(path)}  {mbytes:.1f} MB  {_fmt_ts(ts)}")
        else:
            lines.append("  - _work 下没扫到 .bundle")
    witness = {"bundle": str(bundle) if bundle else "", "candidates": 0,
               "since": now_ms, "mtime": 0}
    ok = bundle_raw == "" or bundle is not None
    return ok, "\n".join(lines), witness, fail


def _verify_render(payload: dict) -> tuple[bool, str, dict, list[str]]:
    since_ms = int(_as_number(payload.get("since")) or 0)
    artifact_raw = str(payload.get("artifact") or "").strip()
    bundle_raw = str(payload.get("bundle") or "").strip()
    lines = ["# 渲染闸 · 产物 mtime 校验（判据 = 产物 mtime，不是退出码）"]
    candidate: Path | None = None
    origin = ""
    hint = ""
    if artifact_raw:
        path = _abs_any(artifact_raw)
        if path.is_dir():
            candidate = _newest_file(path)
            origin = f"{_rel_any(path)} 里最新文件"
            hint = f"产物目录里是空的：{_rel_any(path)}"
        elif path.is_file():
            candidate = path
            origin = _rel_any(path)
        else:
            hint = f"产物不在：{artifact_raw}（路径不存在）"
    elif bundle_raw:
        bundle = _abs_any(bundle_raw)
        out_dir = bundle.parent / "render"
        candidate = _newest_file(out_dir)
        origin = f"{_rel_any(out_dir)} 里最新产物"
        hint = f"还没渲过：{_rel_any(out_dir)} 不存在或是空的"
    else:
        candidate = _newest_render_artifact()
        origin = "_work 下最新渲染产物"
        hint = "给 artifact=<产物路径> 或 bundle=<包路径>（默认产物目录 = <包父目录>\\render）"

    if candidate is None:
        command = f"{PY} {UNITY_RENDER} {str(_abs_any(bundle_raw)) if bundle_raw else '<bundle>'}"
        fail = [f"{hint}，所以这轮渲染不算数；现在该跑："
                f"先关掉那个工程的编辑器，再 `{command}`"]
        report = "\n".join(lines + ["❌ " + fail[0]])
        witness = {"artifact": "", "mtime": 0, "since": since_ms}
        return False, report, witness, fail

    try:
        mtime_s = candidate.stat().st_mtime
    except OSError:
        fail = [f"产物 stat 不动：{_rel_any(candidate)} —— 现在该跑：重跑渲染命令"]
        return False, "\n".join(lines + ["❌ " + fail[0]]), {}, fail
    mtime_ms = int(mtime_s * 1000)
    fresh = mtime_ms > since_ms
    witness = {"artifact": _rel_any(candidate), "mtime": mtime_ms, "since": since_ms}
    lines.append(f"产物：{_rel_any(candidate)}（{origin}）")
    lines.append(f"mtime：{_fmt_ts(mtime_s)} = {mtime_ms} ms · since = {since_ms} ms")
    if fresh:
        lines.append("✅ 产物 mtime 比 since 新 —— 这轮渲染**算数**。")
        lines.append("（观感三类：遮蔽 / 倒影 / 条纹 —— 只有用户能判；"
                     "本闸只保证「渲过且产物更新」）")
        return True, "\n".join(lines), witness, []
    command = f"{PY} {UNITY_RENDER} {str(_abs_any(bundle_raw)) if bundle_raw else '<bundle>'}"
    fail = [f"产物 mtime {mtime_ms} ≤ since {since_ms} —— 这轮渲染不算数；"
            f"现在该跑：先关掉那个工程的编辑器，再 `{command}`"]
    lines.append("❌ 产物**没更新**（mtime ≤ since）⇒ 这轮渲染不算数"
                 "（可能白跑 / 编辑器没关 / 包没变）。")
    lines.append(f"现在该跑：{command}   ⚠ 先关编辑器；跑完用 "
                 f"since={int(time.time() * 1000)} 再验一次")
    return False, "\n".join(lines), witness, fail


def check_unity(payload: dict) -> tuple[bool, str, dict, list[str]]:
    """since>0 = 验产物 mtime；否则 = 预检（给命令与候选）。"""
    since = _as_number(payload.get("since")) or 0
    if since > 0:
        return _verify_render(payload)
    return _precheck_render(payload)


# ──────────────────────────────────────────────────────────────────────────
# 自检（负控必须能红 / 正控必须能绿；不依赖网络、不起 Unity）
# ──────────────────────────────────────────────────────────────────────────


def _run_json_cli(arguments: list[str]) -> tuple[int, list[str], dict | None]:
    """真跑一次本脚本的 --json 模式，返回 (rc, stdout 非空行, 解析出的 JSON)。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, str(GATE), *arguments],
        cwd=str(PROD), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, check=False, env=env,
    )
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    data: dict | None = None
    if len(lines) == 1:
        with contextlib.suppress(ValueError):
            parsed = json.loads(lines[0])
            data = parsed if isinstance(parsed, dict) else None
    return proc.returncode, lines, data


def selftest() -> int:
    """≥8 条负控/正控，逐条打印；末行 `✅ 自检全绿（N 条）`。"""
    passed: list[str] = []
    failed: list[str] = []

    def expect(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            passed.append(name)
        else:
            failed.append(f"{name} —— {detail or '不成立'}")

    def plan_of(**overrides) -> tuple[bool, str, list[str]]:
        payload = {
            "product": "tools/ba_boot.py::build_report",
            "db": "Units.Cost",
            "dbNumber": "888",
            "decompile": "没读",
            "decompileBasis": "kb/14-本机工具清单.md",
            "judge": "tools/where.py 中栏 --live",
            "before": 96,
            "after": 0,
            #: v3.3：自检这条 payload 不涉及任何线上字段值 ⇒ 按口径写「不适用」（留痕不封路）。
            "source": "不适用：自检夹具，不改任何线上字段的值",
        }
        payload.update(overrides)
        return check_plan(payload)

    # ① 正控：真读经三行＋判据 ⇒ 绿，且报告回显四行
    ok, report, fails = plan_of()
    expect("正控：真读经三行＋真判据 → 绿", ok and not fails, report[:200])
    expect(
        "正控：报告回显四行（产品代码/数据库/反编译/判据）",
        all(token in report for token in ("产品代码", "数据库", "反编译", "判据")),
        report[:200],
    )

    # ①.5 v3.3「数据来源」那一格：必须有读数支撑，且必须能红
    ok, _r, _f = plan_of(source="120.f2 = 阵亡单位总价值（点数），来自 214 单位摧毁事件")
    expect("负控：source 有但本轮没跑 field 读数 → 红", not ok)
    ok, _r, fails = plan_of(
        source="120.f2 = 阵亡单位总价值（点数）",
        fieldEvidence={"target": "120.f1", "readings": 276, "min": 410, "max": 765})
    expect("负控：field 读数 target 与 source 对不上 → 红",
           not ok and any("不一致" in row for row in fails), str(fails)[:160])
    ok, report, _f = plan_of(
        source="120.f2 = 阵亡单位总价值（点数）",
        fieldEvidence={"target": "120.f2", "readings": 276, "min": 410, "max": 765})
    expect("正控：source ＋ 对得上的 field 读数 → 绿", ok, report[:200])
    ok, _r, _f = plan_of(source="不适用：纯配置改动，不动任何字段值")
    expect("正控：source 写「不适用：理由」→ 放行（留痕不封路）", ok)
    ok, _r, fails = plan_of(source="不适用")
    expect("负控：只写「不适用」不给理由 → 红", not ok)
    #: field 闸本体：命令没吐数字 ⇒ 红（能红才算闸）
    ok, _r, _w, _f = build_field({"target": "999.x", "command": "echo no-numbers-here"})
    expect("负控：field 命令没吐数字 → 红", not ok)

    # ② 负控：假 product 路径
    ok, report, fails = plan_of(product="src/根本不存在的文件.py::foo")
    expect("负控：假 product 路径 → 红", not ok and bool(fails), report[:200])
    expect("负控：假 product 的失败项要说清「现在该跑」",
           any("现在该跑" in row for row in fails), str(fails)[:200])

    # ③ 负控：真文件但函数名搜不到
    ok, _report, _fails = plan_of(product="tools/ba_boot.py::zqna_wobble_xyz_999")
    expect("负控：文件在但函数名搜不到 → 红", not ok)

    # ④ 负控：decompile 行号越界
    ok, _report, fails = plan_of(decompile="tools/ba_boot.py:999999")
    expect("负控：decompile 行号越界 → 红", not ok and any("行号越界" in row for row in fails))

    # ⑤ 负控：判据 before == after（恒真 = 假绿）
    ok, _report, fails = plan_of(before=1, after=1)
    expect("负控：判据 before==after → 红", not ok and any("恒真" in row for row in fails))

    # ⑥ 负控：「没读」缺依据
    ok, _report, fails = plan_of(decompile="没读", decompileBasis="")
    expect("负控：「没读」缺 decompileBasis → 红", not ok and any("没读" in row for row in fails))

    # ⑦ 负控：依据文档不存在
    ok, _report, _fails = plan_of(decompile="没读", decompileBasis="kb/这份文档不存在.md")
    expect("负控：依据文档不存在 → 红", not ok)

    # ⑦b ★ v3.1：反编译那一行必须与**本轮实读**对账（用户 2026-11：「偷懒不读汇编代码」）
    ok, _report, fails = plan_of(mech=True)
    expect("负控：机制类问题写「没读」→ 红（偷懒）",
           not ok and any("偷懒" in row for row in fails), str(fails)[:300])
    self_file = str(Path(__file__).resolve())
    ok, _report, fails = plan_of(readEvidence={"file": self_file, "startLine": 1, "endLine": 60})
    expect("负控：read 过还写「没读」→ 红（假账）",
           not ok and any("假账" in row for row in fails), str(fails)[:300])
    ok, _report, fails = plan_of(
        decompile=f"{self_file}:10",
        readEvidence={"file": self_file, "startLine": 1, "endLine": 60},
    )
    expect("正控：读经引的行落在本轮 read 段里 → 该行绿", ok and not fails, str(fails)[:300])
    ok, _report, fails = plan_of(
        decompile=f"{self_file}:5000",
        readEvidence={"file": self_file, "startLine": 1, "endLine": 60},
    )
    expect("负控：行号不在本轮 read 段里 → 红",
           not ok and any("不在本轮 read" in row for row in fails), str(fails)[:300])
    ok, _report, fails = plan_of(
        decompile=f"{PROD / 'tools' / 'ba_boot.py'}:10",
        readEvidence={"file": self_file, "startLine": 1, "endLine": 60},
    )
    expect("负控：读 A 引 B → 红",
           not ok and any("读 A 引 B" in row for row in fails), str(fails)[:300])

    # ⑧ 负控：db 列找不到 / dbNumber 不是数字
    ok, _report, _fails = plan_of(db="NoSuchTable.NoSuchColumn")
    expect("负控：db 表.列找不到 → 红", not ok)
    ok, _report, _fails = plan_of(dbNumber="不是数字")
    expect("负控：dbNumber 不是数字 → 红", not ok)

    # ⑨ dig 负控：不存在的符号 → 四门 0 命中，且⛔ 不许出现「没有」二字
    dig_report, dig_witness, dig_fail = build_dig("ZqnaWobbleXyz999")
    expect(
        "dig 负控：不存在符号 → dec/db/kb 全 0 命中",
        dig_witness.get("dec_hits") == 0 and dig_witness.get("db_hits") == 0
        and dig_witness.get("kb_hits") == 0,
        str(dig_witness),
    )
    expect("dig 负控：报告不许出现「没有」二字", "没有" not in dig_report, dig_report[:200])
    expect("dig 负控：0 命中也要给「换词再挖」的下一步", bool(dig_fail), str(dig_fail)[:200])
    expect("dig：report 长度 < 3500（插件修剪器安全区）",
           len(dig_report) < 3500, str(len(dig_report)))

    # ⑩ dig 正控：真符号必须落到证据（索引在就至少一处反编译命中）
    real_report, real_witness, _real_fail = build_dig("IsDefault")
    if (DECOMP_INDEX_DIR / decomp_index.TYPES_TSV).is_file():
        expect("dig 正控：IsDefault → 反编译索引有命中", real_witness["dec_hits"] >= 1,
               str(real_witness))
    else:
        expect("dig 正控：索引未建时明确报「索引未建」", "索引未建" in real_report)
    expect("dig 正控：IsDefault → 知识库/列文档至少一门命中",
           (real_witness["kb_hits"] + real_witness["db_hits"]) >= 1, str(real_witness))

    # ⑩b 门 3 → **有函数体**的真实 .txt：方法命中必须落成权威根里存在的绝对路径
    method_report, method_witness, _method_fail = build_dig("GetAmmoResupplyMultiplier")
    expect("dig 负控：0 命中时不许编造 ISIL .txt 路径", "[类型文件]" not in dig_report)
    if (DECOMP_INDEX_DIR / decomp_index.TYPES_TSV).is_file():
        type_lines = [line for line in method_report.splitlines() if "[类型文件]" in line]
        evidence = [str(row) for row in method_witness.get("dec_files", [])
                    if str(row).lower().endswith(".txt") and Path(str(row)).is_absolute()]
        expect("dig 正控：方法命中 ⇒ 给出权威根里真实存在的 ISIL .txt 绝对路径",
               bool(type_lines) and bool(evidence)
               and all(Path(row).is_file() for row in evidence),
               f"type_lines={type_lines[:2]} dec_files={method_witness.get('dec_files')}")
        match = re.search(r"\[类型文件\] (\S+\.txt)  \(([\d,]+) B\)", method_report)
        stat_ok = bool(
            match
            and Path(match.group(1)).is_file()
            and Path(match.group(1)).stat().st_size == int(match.group(2).replace(",", ""))
        )
        expect("dig 正控：报告里的 .txt 字节数 = 现场 stat 读数", stat_ok,
               method_report[method_report.find("[类型文件]"):][:200])
    else:
        expect("dig 正控：索引未建时明确报「索引未建」且不给假 .txt 路径",
               "索引未建" in method_report and "[类型文件]" not in method_report)

    # ⑪ 报告长度上限（构造超长输入也必须截住，且保住尾部读经模板）
    capped = _cap("保头" + "Z" * 50000 + "\n读经（改代码之前）\n- 产品代码：占位")
    expect("报告上限：超长输入 ≤ 3400 且保住尾部", len(capped) <= MAX_REPORT
           and "读经（改代码之前）" in capped, str(len(capped)))

    # ⑫ unity 预检：给绝对命令 + mtime 判据 + 进程只能 pwsh 查的提醒；⛔ 不伪装查过进程
    ok, report, _witness, _fails = check_unity({})
    expect("unity 预检：ok 且给可读报告", ok and len(report) > 20, report[:200])
    expect("unity 预检：给绝对命令 unity_render.py",
           f"{PY} {UNITY_RENDER}" in report, report[:300])
    expect("unity 预检：判据写 mtime、进程只能 DSH pwsh 查",
           "mtime" in report and "Get-Process -Name BrokenArrow" in report, report[:300])

    # ⑬ unity since 负控 / 正控（拿本文件当假产物，mtime 真实可算）
    file_mtime_ms = int(GATE.stat().st_mtime * 1000)
    ok, report, _witness, _fails = check_unity(
        {"artifact": str(GATE), "since": file_mtime_ms + 10 ** 9})
    expect("unity since 负控：产物没更新 → 红", (not ok) and "不算数" in report, report[:200])
    ok, _report, witness, _fails = check_unity(
        {"artifact": str(GATE), "since": file_mtime_ms - 5000})
    expect("unity since 正控：产物比 since 新 → 绿", ok and witness.get("mtime") == file_mtime_ms,
           str(witness))

    # ⑭ --json：单行合法 JSON、ok 字段与判定一致、JSON 模式恒 exit 0（插件据此解析）
    bad_payload = {
        "product": "src/根本不存在的文件.py::foo", "db": "NoSuchTable.NoSuchColumn",
        "dbNumber": "不是数字", "decompile": "没读", "decompileBasis": "",
        "judge": "", "before": 1, "after": 1,
    }
    rc, lines, data = _run_json_cli(["--kind", "plan", "--payload",
                                     json.dumps(bad_payload, ensure_ascii=True), "--json"])
    expect("--json 负控：stdout 恰好一行", len(lines) == 1, f"lines={len(lines)}")
    expect("--json 负控：单行是合法 JSON 且 ok=false", bool(data) and data.get("ok") is False,
           str(data)[:200])
    expect("--json：五字段齐全（ok/kind/report/witness/fail）",
           bool(data) and all(key in data for key in ("ok", "kind", "report", "witness", "fail")),
           str(sorted(data or {}))[:200])
    expect("--json：红 plan 也 exit 0（插件 exit≠0 会丢 JSON）", rc == 0, f"rc={rc}")
    good_payload = json.loads(json.dumps(bad_payload))
    good_payload.update({
        "product": "tools/ba_boot.py::build_report", "db": "Units.Cost", "dbNumber": "888",
        "decompile": "没读", "decompileBasis": "kb/14-本机工具清单.md",
        "judge": "tools/where.py 中栏 --live", "before": 96, "after": 0,
        "source": "不适用：自检夹具，不改任何线上字段的值",
    })
    _rc, _lines, data = _run_json_cli(["--kind", "plan", "--payload",
                                       json.dumps(good_payload, ensure_ascii=True), "--json"])
    expect("--json 正控：真 plan → ok=true", bool(data) and data.get("ok") is True,
           str(data)[:200])

    # ⑮ kind=read：把反编译**函数体**真读进来（正控）+ 三条负控（⛔ 只引用路径 = 没读）
    read_report, read_witness, read_fail = build_read(
        {"symbol": "SupplyConfig.GetAmmoResupplyMultiplier"})
    read_file = Path(str(read_witness.get("file") or ""))
    expect("read 正控：按 symbol 定位到真实 ISIL 文件 + 方法块",
           (not read_fail) and "GetAmmoResupplyMultiplier" in read_report and read_file.is_file(),
           str(read_witness)[:220])
    expect("read 正控：函数体是真内容（Disassembly/ISIL），不是空壳",
           ("ISIL:" in read_report) and ("Divide" in read_report or "Multiply" in read_report),
           read_report[:300])
    expect("read 正控：`[rcx+..]` 机械对到真实字段名（契合游戏逻辑，不靠猜）",
           all(name in read_report for name in
               ("AmmoCostMultiplier", "SupplyExchangeRate", "SupplyValueBought")),
           read_report[:500])
    expect("read 正控：行号 / 字符数是现场读数",
           int(read_witness.get("lines") or 0) > 0 and int(read_witness.get("chars") or 0) > 50,
           str(read_witness)[:220])
    bad_file_report, _bw, bad_file_fail = build_read({"file": str(GATE)})
    expect("read 负控：非 `_work/isil*` 的文件拒绝（不许借证据闸读别的）",
           bool(bad_file_fail) and "只读" in bad_file_report, bad_file_report[:200])
    miss_report, _mw, miss_fail = build_read({"symbol": "SupplyConfig.ZzzNoSuchMethod999"})
    expect("read 负控：方法不存在 → 红并列出该文件的方法（⛔ 不说「反编译里没有」）",
           bool(miss_fail) and "Method:" in miss_report and "反编译里没有" not in miss_report,
           miss_report[:300])
    type_report, type_witness, type_fail = build_read({"file": str(read_file)})
    expect("read 正控：只给 file → 类型头 + 方法清单",
           (not type_fail) and "Method:" in type_report and int(type_witness.get("lines") or 0) > 0,
           type_report[:200])
    expect("read：报告长度 ≤ 上限（插件修剪器之前）",
           len(read_report) <= MAX_REPORT, f"len={len(read_report)}")

    # ⑯ kind=verify：判据必须**真跑过**（before 要在现场输出里；编的不给绿）
    okv, repv, witv, _fv = build_verify({"command": "echo 96", "before": 96})
    expect("verify 正控：命令真跑、before 命中输出",
           okv and bool(witv.get("matched")) and int(witv.get("exitCode", -1)) == 0, repv[:240])
    expect("verify 正控：记了 sha / 字节数 / 耗时",
           bool(witv.get("sha")) and int(witv.get("bytes") or 0) > 0
           and float(witv.get("elapsedMs") or 0) > 0, str(witv)[:240])
    okv, repv, _w, _f = build_verify({"command": "echo 96", "before": 9999})
    expect("verify 负控：before 是编的（不在输出里）⇒ 红",
           (not okv) and "编" in repv, repv[:300])
    okv, repv, _w, _f = build_verify({})
    expect("verify 负控：缺 command ⇒ 红", not okv, repv[:160])
    okv, repv, _w, _f = build_verify({"command": "nonexistent_cmd_zzz_qq"})
    expect("verify 负控：命令不存在（退出码≠0）⇒ 红", not okv, repv[:240])

    print("ba_gate 自检 · 负控必须能红 / 正控必须能绿")
    for name in passed:
        print(f"  ✔ {name}")
    for name in failed:
        print(f"  ⛔ {name}")
    if failed:
        print(f"❌ 自检失败 {len(failed)} 条")
        return 1
    print(f"✅ 自检全绿（{len(passed)} 条）")
    return 0


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────


def _emit(kind: str, ok: bool, report: str, witness: dict | None,
          fail: list[str] | None, as_json: bool) -> int:
    """统一出口：--json 只打一行；人读模式先打正文再列失败项。

    ⚠ exit code 口径（task-2）：非 --json 时 0=通过 / 1=不通过；
    --json 时**恒 0** —— 插件 `index.js::runJsonScript` 在进程 exit≠0 时
    会把 stdout 里的 JSON 丢掉（`if (r.error) done({json: undefined})`），
    判定只能靠 JSON 里的 ok 字段。这是「插件读 JSON、不靠退出码」的落地姿势。
    """
    text = _cap(str(report or ""))
    if as_json:
        payload = {
            "ok": bool(ok),
            "kind": str(kind),
            "report": text,
            "witness": dict(witness or {}),
            "fail": [str(row) for row in (fail or [])],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    print(text)
    if fail:
        print("")
        print("失败项：")
        for row in fail:
            print(f"  - {row}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(
        description=(
            "断箭模式 v2 · 证据闸（dig 证据包 / read 反编译实读 / verify 判据实测 / "
            "plan 读经判据校验 / unity 预检）"
        ),
    )
    parser.add_argument("--kind", default="", help="dig / read / verify / plan / unity")
    parser.add_argument("--query", default="", help="dig：原话 / 表.列 / 函数名 / 类型名")
    parser.add_argument("--payload", default="", help="read / plan / unity 的 JSON 参数")
    parser.add_argument("--json", action="store_true", help='只打一行 {"ok":…,"kind":…,…}')
    parser.add_argument("--selftest", action="store_true", help="自检（负控必须能红）")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    kind = str(args.kind or "").strip().lower()
    if not kind:
        parser.print_help()
        print("")
        print("例：")
        print(f"  {PY} {GATE} --kind dig --query \"无视掩体\"")
        print(f"  {PY} {GATE} --kind read --payload "
              f"'{{\"symbol\":\"SupplyConfig.GetAmmoResupplyMultiplier\"}}'")
        print(f"  {PY} {GATE} --kind plan --payload '{{\"product\":\"<文件>::<函数>\",…}}'")
        print(f'  {PY} {GATE} --kind unity --payload "{{}}"')
        return 2

    payload: dict = {}
    if args.payload.strip():
        try:
            parsed = json.loads(args.payload)
        except ValueError as exc:
            return _emit(kind, False, f"⛔ --payload 不是合法 JSON：{exc}",
                         {}, [f"--payload 不是合法 JSON：{exc}"], args.json)
        if not isinstance(parsed, dict):
            return _emit(kind, False, "⛔ --payload 必须是 JSON 对象（{…}）",
                         {}, ["--payload 必须是 JSON 对象"], args.json)
        payload = parsed

    if kind == "dig":
        query = str(args.query or payload.get("query") or "").strip()
        if len(query) < 2:
            return _emit("dig", False,
                         "⛔ dig 需要 --query（原话 / 表.列 / 函数名 / 类型名）",
                         {}, ["dig 需要 query"], args.json)
        report, witness, fail = build_dig(query)
        return _emit("dig", True, report, witness, fail, args.json)
    if kind == "read":
        report, witness, fail = build_read(payload)
        return _emit("read", not fail, report, witness, fail, args.json)
    if kind == "field":
        ok, report, witness, fail = build_field(payload)
        return _emit("field", ok, report, witness, fail, args.json)
    if kind == "verify":
        ok, report, witness, fail = build_verify(payload)
        return _emit("verify", ok, report, witness, fail, args.json)
    if kind == "plan":
        ok, report, fail = check_plan(payload)
        return _emit("plan", ok, report, {}, fail, args.json)
    if kind == "unity":
        ok, report, witness, fail = check_unity(payload)
        return _emit("unity", ok, report, witness, fail, args.json)
    return _emit(kind, False,
                 f"⛔ 不认识的 kind：「{kind}」（只认 dig / read / verify / plan / unity；"
                 "status / waive 在插件 node 侧，不经过本脚本）",
                 {}, [f"不认识的 kind：{kind}"], args.json)


if __name__ == "__main__":
    raise SystemExit(main())
