#!/usr/bin/env python
"""type_index.py —— 类型/方法总表（Il2CppDumper dump.cs → 可查询索引）

来源（权威元数据）：_work/il2cppdump/dump.cs（Il2CppDumper 产出，70 MB / 1,831,684 行）
镜像基址：0x180000000（VA = RVA + 基址；全量核过，见 --selftest 第 7 项）

产物（_work/type_index/，大文件一律 gzip，gzip 头 mtime=0 可复现）：
  types.tsv.gz         type_full / namespace / bases / typedef_index / assembly
  methods.tsv.gz       rva / va / type / method / signature
                       （rva = -1 表示无地址的抽象 / 接口方法；一条 = dump.cs 里一行声明）
  fields.tsv.gz        type / field / offset / decl
                       （自动属性归一成 <X>k__BackingField → X；枚举成员无 offset）
  generic_insts.tsv.gz rva / va / type / name
                       （dump.cs 里 '/* GenericInstMethod :' 注释块：
                        一个 RVA 下可能挂多个泛型实参不同的实例名）
  meta.json            计数 + 来源指纹（字节数 / mtime / sha256），供复算

用法（仓内 venv）：
  .venv\\Scripts\\python.exe tools/type_index.py --build
  .venv\\Scripts\\python.exe tools/type_index.py --type Turrets --fields
  .venv\\Scripts\\python.exe tools/type_index.py --method get_ModelFileName
  .venv\\Scripts\\python.exe tools/type_index.py --rva 0x12b0     # 也认 VA：0x1800012b0
  .venv\\Scripts\\python.exe tools/type_index.py --selftest       # 正控/负控/独立复算/对拍

口径（写在文件里，别靠记忆）：
  * 方法 RVA 注释行三种形态：'0x… Offset: 0x… VA: 0x…'、同前带 ' Slot: N'、'RVA: -1 Offset: -1'。
  * 只认 Methods 段里的 '// RVA:' 行当声明；Fields/Properties 段一行都不当方法。
  * '|-RVA:' 行属于 '/* GenericInstMethod :' 注释块（泛型实例），与声明是两回事，分开存。
  * 类型声明以行尾 '// TypeDefIndex: N' 定位（嵌套类型在 dump.cs 里是**平铺**的一行，
    名字自带点号，例如 NodeEditorSlaveDisplay.ShowStates）。
  * 程序集由文件头 '// Image N: <dll> - <首个 TypeDefIndex>' 按区间归属。
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
IL2CPP_DIR = WORK / "il2cppdump"
DUMP_CS = IL2CPP_DIR / "dump.cs"
SCRIPT_JSON = IL2CPP_DIR / "script.json"
OUT_DIR = WORK / "type_index"

TYPES_TSV = OUT_DIR / "types.tsv.gz"
METHODS_TSV = OUT_DIR / "methods.tsv.gz"
FIELDS_TSV = OUT_DIR / "fields.tsv.gz"
GENERIC_TSV = OUT_DIR / "generic_insts.tsv.gz"
META_JSON = OUT_DIR / "meta.json"

IMAGE_BASE = 0x180000000
# script.json 指纹：实测 ScriptMethod 段 = 589,417 条 Address+Name。
# 它变了 = 换了 dump，对拍口径必须重核 —— 所以这一条要能红。
SCRIPT_METHOD_ENTRIES = 589_417

TYPE_HEADER = ("type_full", "namespace", "bases", "typedef_index", "assembly")
METHOD_HEADER = ("rva", "va", "type", "method", "signature")
FIELD_HEADER = ("type", "field", "offset", "decl")
GENERIC_HEADER = ("rva", "va", "type", "name")

TypeRow = tuple[str, str, str, str, str]
FieldRow = tuple[str, str, str, str]
GenericRow = tuple[str, str, str, str]

_RE_IMAGE = re.compile(r"^// Image (\d+): (.+?) - (\d+)\s*$")
_RE_NS = re.compile(r"^// Namespace: (.*)$")
_RE_TYPEDEF_TAIL = re.compile(r"// TypeDefIndex: (\d+)\s*$")
_RE_TD_LINE = re.compile(r"^[^ \t].*// TypeDefIndex: \d+\s*$")
_RE_DECL = re.compile(
    r"^(?:(?:public|private|protected|internal|sealed|abstract|static|readonly|partial)\s+)*"
    r"(class|struct|enum|interface)\s+(\S.*)$"
)
_RE_SECTION = re.compile(r"^\t// (Fields|Properties|Methods|Nested Types)\s*$")
_RE_RVA = re.compile(
    r"^\t// RVA: (-1|0x[0-9A-Fa-f]+) Offset: (-1|0x[0-9A-Fa-f]+)"
    r"(?: VA: (0x[0-9A-Fa-f]+))?(?: Slot: (\d+))?\s*$"
)
_RE_GI_RVA = re.compile(
    r"^\t\|-RVA: (-1|0x[0-9A-Fa-f]+) Offset: (-1|0x[0-9A-Fa-f]+)"
    r"(?: VA: (0x[0-9A-Fa-f]+))?\s*$"
)
_RE_GI_OPEN = re.compile(r"^\t/\* GenericInstMethod :\s*$")
_RE_FIELD = re.compile(r"^(?P<decl>.+?);\s*//\s*(?P<off>0x[0-9A-Fa-f]+)\s*$")
_RE_BACKING = re.compile(r"<([^<>]+)>k__BackingField$")
_RE_GENERIC = re.compile(r"<[^<>]*>")


# ---------------------------------------------------------------- 基础工具


@contextmanager
def _open_text_gz(path: Path) -> Iterator[io.TextIOWrapper]:
    """gzip 文本写句柄，头里 mtime=0（同内容同字节，便于比对）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("wb") as raw_fh,
        gzip.GzipFile(fileobj=raw_fh, mode="wb", compresslevel=9, mtime=0) as gz,
        io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as text,
    ):
        yield text


def iter_tsv(path: Path) -> Iterator[list[str]]:
    """读 gz tsv（跳表头）。"""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        next(fh, None)
        for line in fh:
            yield line.rstrip("\n").split("\t")


def _split_bases(text: str) -> tuple[str, str]:
    """把 'Name<T> : Base, IFace' 拆成 (名字, 基类/接口串)；只在泛型括号外认 ' : '。"""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == " " and depth == 0 and text[i + 1 : i + 3] == ": ":
            return text[:i].rstrip(), text[i + 3 :].strip()
    return text.rstrip(), ""


def field_name(decl: str) -> str:
    """从字段声明取字段名；自动属性归一成 <X>k__BackingField → X。"""
    d = decl.strip()
    if "=" in d:  # 枚举成员：public const X Hide = 0
        d = d.split("=", 1)[0].strip()
    m = _RE_BACKING.search(d)
    if m:
        return m.group(1)
    prev = None
    while prev != d:  # 逐层剥泛型参数（List<Weapons> Weapons → Weapons）
        prev = d
        d = _RE_GENERIC.sub("", d)
    toks = d.split()
    return toks[-1] if toks else ""


def method_name(sig: str) -> str:
    """从签名取方法名：第一个 '(' 之前最后一个词。"""
    toks = sig.split("(", 1)[0].split()
    return toks[-1] if toks else ""


def parse_rva(text: str) -> tuple[int, bool]:
    """把 --rva 的入参解析成 (RVA, 是否是 VA)。认 0x 前缀与十进制。"""
    v = int(text, 0)
    if v >= IMAGE_BASE:
        return v - IMAGE_BASE, True
    return v, False


# ---------------------------------------------------------------- 构建


def build(dump_path: Path = DUMP_CS, out_dir: Path = OUT_DIR) -> dict:
    """扫一遍 dump.cs，写四张表 + meta.json。返回 meta 字典。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    stats: Counter[str] = Counter()
    anomalies: Counter[str] = Counter()
    images: list[tuple[int, str]] = []

    def assembly_of(idx: int) -> str:
        if not images:
            return ""
        pos = bisect.bisect_right([s for s, _ in images], idx) - 1
        return images[pos][1] if pos >= 0 else ""

    ns = ""
    cur_type: str | None = None
    cur_idx = -1
    section: str | None = None
    in_body = False
    in_gi = False
    gi_rva: tuple[str, str] | None = None
    pending: tuple[str, str] | None = None

    with (
        dump_path.open(encoding="utf-8", errors="replace") as src,
        _open_text_gz(out_dir / TYPES_TSV.name) as ft,
        _open_text_gz(out_dir / METHODS_TSV.name) as fm,
        _open_text_gz(out_dir / FIELDS_TSV.name) as ff,
        _open_text_gz(out_dir / GENERIC_TSV.name) as fg,
    ):
        ft.write("\t".join(TYPE_HEADER) + "\n")
        fm.write("\t".join(METHOD_HEADER) + "\n")
        ff.write("\t".join(FIELD_HEADER) + "\n")
        fg.write("\t".join(GENERIC_HEADER) + "\n")

        for raw in src:
            line = raw.rstrip("\n")

            if pending is not None:  # RVA 注释的下一行 = 签名
                sig = line.strip()
                if not sig.endswith(("{ }", ";")):
                    anomalies["方法签名行结尾异常"] += 1
                if sig.endswith(" { }"):
                    sig = sig[: -len(" { }")].rstrip()
                else:
                    sig = sig.rstrip(";").rstrip()
                if " where " in sig:
                    stats["签名带 where"] += 1
                rva, va = pending
                fm.write("\t".join((rva, va, cur_type or "", method_name(sig), sig)) + "\n")
                stats["methods"] += 1
                stats["methods_with_addr" if rva != "-1" else "methods_without_addr"] += 1
                pending = None
                continue

            if not in_body:
                m = _RE_IMAGE.match(line)
                if m:
                    images.append((int(m.group(3)), m.group(2)))
                    continue
                m = _RE_NS.match(line)
                if m:
                    ns = m.group(1).strip()
                    continue
                if _RE_TYPEDEF_TAIL.search(line) and not line.startswith("\t"):
                    head = _RE_TYPEDEF_TAIL.sub("", line).strip()
                    idx_m = _RE_TYPEDEF_TAIL.search(line)
                    cur_idx = int(idx_m.group(1)) if idx_m else -1
                    dm = _RE_DECL.match(head)
                    if not dm:
                        anomalies["类型声明解析失败"] += 1
                        cur_type = None
                        continue
                    kind, rest = dm.group(1), dm.group(2)
                    name, bases = _split_bases(rest)
                    cur_type = f"{ns}.{name}" if ns else name
                    asm = assembly_of(cur_idx)
                    if not asm:
                        anomalies["类型无程序集归属"] += 1
                    ft.write("\t".join((cur_type, ns, bases, str(cur_idx), asm)) + "\n")
                    stats["types"] += 1
                    stats[f"kind:{kind}"] += 1
                    stats["bases_nonempty" if bases else "bases_empty"] += 1
                    continue
                if line == "{" and cur_type is not None:
                    in_body = True
                    section = None
                    continue
                if line == "{}" and cur_type is not None:  # 空类型体
                    cur_type = None
                    stats["empty_bodies"] += 1
                    continue
                if cur_type is not None and line and line not in ("{", "{}"):
                    anomalies["类型体开头异常"] += 1
                    cur_type = None
                continue

            # ---- 类型体内 ----
            if line == "}":
                if in_gi:
                    anomalies["泛型实例块没等到 */ 就遇到类型结束"] += 1
                    in_gi = False
                in_body = False
                cur_type = None
                section = None
                continue
            if not line:
                continue
            if in_gi:
                if line.strip() == "*/":
                    in_gi = False
                    gi_rva = None
                    continue
                gm = _RE_GI_RVA.match(line)
                if gm:
                    gi_rva = (gm.group(1), gm.group(3) or "")
                    stats["gi_rva_groups"] += 1
                    continue
                if line.strip() in ("|", ""):  # 组间分隔行
                    continue
                if line.startswith("\t|-"):
                    name = line[len("\t|-") :].strip()
                    if gi_rva is None:
                        anomalies["泛型实例名没有对应 RVA"] += 1
                    elif name:
                        fg.write("\t".join((gi_rva[0], gi_rva[1], cur_type or "", name)) + "\n")
                        stats["generic_insts"] += 1
                    continue
                anomalies["泛型实例块里的意外行"] += 1
                continue
            if _RE_GI_OPEN.match(line):
                in_gi = True
                gi_rva = None
                stats["gi_blocks"] += 1
                continue
            m = _RE_SECTION.match(line)
            if m:
                section = m.group(1)
                continue
            if line.startswith("\t//"):
                if section == "Methods":
                    rm = _RE_RVA.match(line)
                    if rm:
                        pending = (rm.group(1), rm.group(3) or "")
                    else:
                        anomalies["Methods 段里的非 RVA 注释"] += 1
                continue
            if line.startswith("\t["):  # 特性行
                continue
            if section == "Fields":
                body = line.strip()
                fm_ = _RE_FIELD.match(body)
                offset = fm_.group("off") if fm_ else ""
                decl = (fm_.group("decl") if fm_ else body.rstrip(";")).strip()
                if not decl:
                    anomalies["空字段声明"] += 1
                    continue
                ff.write("\t".join((cur_type or "", field_name(decl), offset, decl)) + "\n")
                stats["fields"] += 1
                stats["fields_with_offset" if offset else "fields_no_offset"] += 1
                continue
            if section == "Methods":
                anomalies["Methods 段里没有 RVA 注释的行"] += 1

    stats["assemblies"] = len(images)
    elapsed = time.time() - t0

    src_bytes = dump_path.stat().st_size
    sha = hashlib.sha256()
    with dump_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            sha.update(chunk)

    meta = {
        "tool": "tools/type_index.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "image_base": hex(IMAGE_BASE),
        "elapsed_sec": round(elapsed, 1),
        "source": {
            "dump_cs": {
                "path": str(dump_path),
                "bytes": src_bytes,
                "mtime": int(dump_path.stat().st_mtime),
                "sha256": sha.hexdigest(),
            }
        },
        "columns": {
            TYPES_TSV.name: list(TYPE_HEADER),
            METHODS_TSV.name: list(METHOD_HEADER),
            FIELDS_TSV.name: list(FIELD_HEADER),
            GENERIC_TSV.name: list(GENERIC_HEADER),
        },
        "counts": {k: v for k, v in sorted(stats.items())},
        "anomalies": {k: v for k, v in sorted(anomalies.items())},
    }
    meta_text = json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True)
    META_JSON.write_text(meta_text + "\n", encoding="utf-8")
    return meta


def load_meta() -> dict:
    return json.loads(META_JSON.read_text(encoding="utf-8"))


def index_ready() -> bool:
    return all(p.exists() for p in (TYPES_TSV, METHODS_TSV, FIELDS_TSV, GENERIC_TSV, META_JSON))


def ensure_index(auto: bool = True) -> None:
    """索引不在（或比 dump.cs 旧）就先建。"""
    if not index_ready():
        if not auto:
            raise SystemExit("索引不存在，先跑 --build")
        print("[type_index] 索引不存在，自动构建 …", file=sys.stderr)
        build()
        return
    if DUMP_CS.exists() and DUMP_CS.stat().st_mtime > META_JSON.stat().st_mtime:
        print("[type_index] dump.cs 比索引新，建议重跑 --build", file=sys.stderr)


# ---------------------------------------------------------------- 查询


def match_types(rows: Iterable[TypeRow], needle: str) -> list[TypeRow]:
    low = needle.lower()
    return [r for r in rows if low in r[0].lower()]


def match_methods(rows: Iterable[TypeRow], needle: str) -> list[TypeRow]:
    low = needle.lower()
    return [r for r in rows if low in f"{r[2]}::{r[3]}".lower()]


def match_rva(rows: Iterable[TypeRow], rva: int) -> list[TypeRow]:
    want = hex(rva).lower()
    return [r for r in rows if r[0].lower() == want]


def field_offset_of(rows: Iterable[FieldRow], type_full: str, field: str) -> str | None:
    """这把尺子同时被自检的正控和负控用（注入坏数据时必须判 False）。"""
    for t, f, off, _decl in rows:
        if t == type_full and f == field:
            return off
    return None


def query_type(needle: str, show_fields: bool, limit: int, show_all: bool) -> int:
    types = list(iter_tsv(TYPES_TSV))
    hits = match_types(types, needle)
    print(f"[--type {needle}] 命中 {len(hits)} 个类型（共 {len(types)} 个）")
    field_counts: Counter[str] = Counter()
    fields_by_type: dict[str, list[FieldRow]] = defaultdict(list)
    if hits:
        want = {h[0] for h in hits}
        for row in iter_tsv(FIELDS_TSV):
            if row[0] in want:
                field_counts[row[0]] += 1
                fields_by_type[row[0]].append((row[0], row[1], row[2], row[3]))
    shown = hits if show_all else hits[:limit]
    for t, ns, bases, idx, asm in shown:
        print(f"  {t}  [TypeDefIndex {idx}]  asm={asm}  fields={field_counts.get(t, 0)}")
        print(f"      ns={ns or '(空)'}  bases={bases or '(无)'}")
        if show_fields:
            for _t, f, off, decl in fields_by_type.get(t, []):
                print(f"      + {off or '-':>6}  {f:<34} {decl}")
    if not show_all and len(hits) > len(shown):
        print(f"  … 还有 {len(hits) - len(shown)} 个（--all 全列）")
    return len(hits)


def query_method(needle: str, limit: int, show_all: bool) -> int:
    rows = list(iter_tsv(METHODS_TSV))
    hits = match_methods(rows, needle)
    print(f"[--method {needle}] 命中 {len(hits)} 个方法（共 {len(rows)} 个）")
    shown = hits if show_all else hits[:limit]
    for rva, va, t, m, sig in shown:
        print(f"  {rva:>10}  {va or '-':>12}  {t}::{m}")
        print(f"      {sig}")
    if not show_all and len(hits) > len(shown):
        print(f"  … 还有 {len(hits) - len(shown)} 个（--all 全列）")
    return len(hits)


def query_field(needle: str, limit: int, show_all: bool) -> int:
    rows = list(iter_tsv(FIELDS_TSV))
    low = needle.lower()
    hits = [r for r in rows if low in f"{r[0]}::{r[1]}".lower()]
    print(f"[--field {needle}] 命中 {len(hits)} 个字段（共 {len(rows)} 个）")
    shown = hits if show_all else hits[:limit]
    for t, f, off, decl in shown:
        print(f"  {off or '-':>6}  {t}::{f}")
        print(f"      {decl}")
    if not show_all and len(hits) > len(shown):
        print(f"  … 还有 {len(hits) - len(shown)} 个（--all 全列）")
    return len(hits)


def query_rva(text: str, limit: int, show_all: bool) -> int:
    """RVA → 类型::方法。声明表与泛型实例表都查（崩溃取证时两者都可能命中）。"""
    rva, was_va = parse_rva(text)
    va_note = f"（入参按 VA 处理，减基址 {hex(IMAGE_BASE)}）" if was_va else ""
    want = hex(rva).lower()
    hits: list[tuple[str, str]] = []
    lo = hi = None
    for rva_col, va_col, t, m, sig in iter_tsv(METHODS_TSV):
        if rva_col == "-1":
            continue
        v = int(rva_col, 16)
        lo = v if lo is None else min(lo, v)
        hi = v if hi is None else max(hi, v)
        if rva_col.lower() == want:
            hits.append(("decl", f"  {rva_col:>10}  {va_col or '-':>12}  {t}::{m}\n      {sig}"))
    for rva_col, va_col, t, name in iter_tsv(GENERIC_TSV):
        if rva_col != "-1" and rva_col.lower() == want:
            hits.append(("ginst", f"  {rva_col:>10}  {va_col or '-':>12}  {t}  [泛型实例] {name}"))
    print(
        f"[--rva {hex(rva)}] 命中 {len(hits)} 条（声明 {sum(1 for k, _ in hits if k == 'decl')}"
        f" / 泛型实例 {sum(1 for k, _ in hits if k == 'ginst')}）{va_note}"
    )
    shown = hits if show_all else hits[:limit]
    for _kind, text_out in shown:
        print(text_out)
    if not show_all and len(hits) > len(shown):
        print(f"  … 还有 {len(hits) - len(shown)} 条（--all 全列）")
    if not hits:
        span = f"{hex(lo)}..{hex(hi)}" if lo is not None and hi is not None else "-"
        print(
            f"  0 命中：RVA {hex(rva)} 不在方法入口表里（已知入口范围 {span}）。"
            f"来自崩溃栈时先确认是 RVA 还是 VA（VA 要 >= 0x{IMAGE_BASE:x}）；"
            f"落在范围内的多半是函数中部地址或 IL2CPP 内部桩，不是方法入口。",
            file=sys.stderr,
        )
    return len(hits)


# ---------------------------------------------------------------- script.json 对拍


def count_script_methods(raw: str) -> tuple[list[tuple[int, str]], int]:
    """切片取 ScriptMethod 段，返回 [(RVA, 名字)] 与段内字面 'Address' 出现次数。"""
    keys = (
        '"ScriptMethod"',
        '"ScriptString"',
        '"ScriptMetadata"',
        '"ScriptMetadataMethod"',
        '"Addresses"',
    )
    pos = {k: raw.find(k) for k in keys}
    start = pos['"ScriptMethod"']
    if start < 0:
        return [], 0
    nxt = min((v for k, v in pos.items() if v > start), default=len(raw))
    seg = raw[start:nxt]
    entry = re.compile(r'"Address": (\d+),\s*"Name": "((?:[^"\\]|\\.)*)"')
    out: list[tuple[int, str]] = []
    for m in entry.finditer(seg):
        out.append((int(m.group(1)), json.loads('"' + m.group(2) + '"')))
    return out, seg.count('"Address":')


def script_rva_names(entries: Iterable[tuple[int, str]]) -> dict[int, set[str]]:
    """script.json 的 'A.B.Type$$Method' → RVA 只留方法名那一段。"""
    out: dict[int, set[str]] = defaultdict(set)
    for addr, name in entries:
        out[addr].add(name.split("$$")[-1])
    return out


def ginst_method_name(name: str) -> str:
    """泛型实例名 '<类型全名>.<方法名>' → 方法名（深度感知的最后一个 '.' 之后）。"""
    depth = 0
    for i in range(len(name) - 1, -1, -1):
        ch = name[i]
        if ch == ">":
            depth += 1
        elif ch == "<":
            depth -= 1
        elif ch == "." and depth == 0:
            return name[i + 1 :]
    return name


def name_key(s: str) -> str:
    """归一化到方法名粒度：剥泛型实参 → 最后一个 '.' 之后 → 去前导点（.ctor → ctor）。"""
    return _RE_GENERIC.sub("", s).rsplit(".", 1)[-1].lstrip(".")


def dump_rva_names(include_generic: bool = False) -> dict[int, set[str]]:
    """dump 侧 RVA → 方法名集合；include_generic 时把 GenericInstMethod 里的实例名也算进来。"""
    out: dict[int, set[str]] = defaultdict(set)
    for rva, _va, _t, m, _sig in iter_tsv(METHODS_TSV):
        if rva != "-1":
            out[int(rva, 16)].add(m)
    if include_generic:
        for rva, _va, _t, name in iter_tsv(GENERIC_TSV):
            if rva != "-1":
                out[int(rva, 16)].add(ginst_method_name(name))
    return out


def _classify(dn: set[str], sn: set[str]) -> str:
    """不一致的 RVA 归桶（顺序 = 从"最可能是结构性差异"到"最可能是真错"）。"""
    if len(dn) > 1 or len(sn) > 1:
        return "多名字同址（RVA 被多个方法复用）"
    if {_RE_GENERIC.sub("", x) for x in dn} & {_RE_GENERIC.sub("", x) for x in sn}:
        return "泛型参数差异（剥掉 <> 后同名）"
    if {name_key(x) for x in dn} & {name_key(x) for x in sn}:
        return "命名约定（.ctor 前导点 / 显式接口全名 / 嵌套名）"
    if any(x.startswith("<") for x in dn) or any(x.startswith("<") for x in sn):
        return "编译产物名（lambda/本地函数 <>c 之类）"
    return "其它（真不一致，要人工看）"


def compare_rvas(dump_map: dict[int, set[str]], sj_map: dict[int, set[str]]) -> dict:
    """两边都有的 RVA 上比名字：精确不一致 / 归一化后仍不一致 / 分类桶。"""
    both = set(dump_map) & set(sj_map)
    buckets: Counter[str] = Counter()
    exact = norm = 0
    for rva in both:
        dn, sn = dump_map[rva], sj_map[rva]
        if dn & sn:
            continue
        exact += 1
        buckets[_classify(dn, sn)] += 1
        if not ({name_key(x) for x in dn} & {name_key(x) for x in sn}):
            norm += 1
    return {"both": len(both), "exact": exact, "normalized": norm, "buckets": buckets}


# ---------------------------------------------------------------- 自检


class Checker:
    """自检账本：能红才算尺子（每条都留下读数）。"""

    def __init__(self) -> None:
        self.fails = 0

    def check(self, ok: bool, title: str, detail: str = "") -> bool:
        mark = "OK  " if ok else "FAIL"
        if not ok:
            self.fails += 1
        print(f"[{mark}] {title}" + (f" —— {detail}" if detail else ""))
        return ok


def _recount_dump() -> tuple[int, int, int, int]:
    """第二套实现（整行正则，不复用 build 的状态机）：独立复算 dump.cs。"""
    n_td = n_addr = n_noaddr = va_bad = 0
    with DUMP_CS.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if _RE_TD_LINE.match(line):
                n_td += 1
            if line.startswith("\t// RVA:"):
                m = _RE_RVA.match(line.rstrip("\n"))
                if not m:
                    continue
                if m.group(1) == "-1":
                    n_noaddr += 1
                else:
                    n_addr += 1
                    if m.group(3) and int(m.group(3), 16) - int(m.group(1), 16) != IMAGE_BASE:
                        va_bad += 1
    return n_td, n_addr, n_noaddr, va_bad


def selftest() -> int:
    t0 = time.time()
    c = Checker()
    print("=" * 78)
    print("type_index --selftest（正控 / 负控 / 独立复算 / script.json 对拍）")
    print("=" * 78)

    # 1 索引存在
    c.check(
        index_ready(),
        "1 索引五件套存在",
        ", ".join(p.name for p in (TYPES_TSV, METHODS_TSV, FIELDS_TSV, GENERIC_TSV, META_JSON)),
    )
    if not index_ready():
        print("索引不在，先 --build")
        return 2
    meta = load_meta()
    counts = meta["counts"]
    types = list(iter_tsv(TYPES_TSV))
    methods = list(iter_tsv(METHODS_TSV))
    fields = list(iter_tsv(FIELDS_TSV))
    generic = list(iter_tsv(GENERIC_TSV))

    # 2 正控·三个类型查得到
    for name in ("Units", "Turrets", "Weapons"):
        hits = match_types(types, name)
        c.check(
            bool(hits),
            f"2 正控·类型 {name} 查得到",
            f"命中 {len(hits)}（{hits[0][0] if hits else '-'}）",
        )

    # 3 正控·Turrets.ModelFileName 偏移 0x20
    want_type = "BrokenArrow.DataBase.Models.Turrets"
    off = field_offset_of(fields, want_type, "ModelFileName")
    c.check(off == "0x20", "3 正控·Turrets.ModelFileName 偏移 == 0x20", f"实测 {off}")

    # 4/5 负控
    neg_t = match_types(types, "ZzzNoSuchTypeZzz")
    c.check(not neg_t, "4 负控·不存在的类型 ZzzNoSuchTypeZzz", f"命中 {len(neg_t)}")
    neg_m = match_methods(methods, "ZzzNoSuchMethodZzz")
    c.check(not neg_m, "5 负控·不存在的方法 ZzzNoSuchMethodZzz", f"命中 {len(neg_m)}")

    # 6 尺子能红（把已知错的值喂进同一把尺子，必须判错）
    bad_row: FieldRow = (
        want_type,
        "ModelFileName",
        "0x48",
        "private string <ModelFileName>k__BackingField",
    )
    bad_off = field_offset_of([bad_row], want_type, "ModelFileName")
    c.check(
        bad_off != "0x20", "6a 尺子能红·偏移注入 0x48 必须判 != 0x20", f"注入后尺子读到 {bad_off}"
    )
    bad_types: list[TypeRow] = [("QqqRenamedQqq", "", "", "0", "")]
    bad_hits = match_types(bad_types, "Units")
    c.check(
        not bad_hits,
        "6b 尺子能红·把 Units 那行改名 QqqRenamedQqq 后必须 0 命中",
        f"命中 {len(bad_hits)}",
    )
    c.check(
        not match_rva(methods, 0xDEADBEEF), "6c 尺子能红·乱编 RVA 0xdeadbeef 必须 0 命中", "命中 0"
    )

    # 7 独立复算 dump.cs
    t7 = time.time()
    n_td, n_addr, n_noaddr, va_bad = _recount_dump()
    n_meth = n_addr + n_noaddr
    c.check(
        n_td == counts.get("types"),
        "7a 独立复算·类型总数与 meta 一致",
        f"复算 {n_td} vs meta {counts.get('types')}",
    )
    c.check(
        n_meth == counts.get("methods"),
        "7b 独立复算·方法总数与 meta 一致",
        f"复算 {n_meth}（有地址 {n_addr} + 无地址 {n_noaddr}）vs meta {counts.get('methods')}",
    )
    c.check(
        n_addr == counts.get("methods_with_addr")
        and n_noaddr == counts.get("methods_without_addr"),
        "7c 独立复算·有/无地址方法数一致",
        f"meta {counts.get('methods_with_addr')}/{counts.get('methods_without_addr')}",
    )
    c.check(va_bad == 0, "7d VA == RVA + 0x180000000 全量成立", f"不成立 {va_bad} 条")
    print(f"     （第 7 项耗时 {time.time() - t7:.1f}s）")

    # 8 与 script.json 对拍
    t8 = time.time()
    raw = SCRIPT_JSON.read_text(encoding="utf-8")
    entries, addr_literals = count_script_methods(raw)
    c.check(
        len(entries) == SCRIPT_METHOD_ENTRIES and len(entries) == addr_literals,
        f"8a script.json ScriptMethod 段条数 == {SCRIPT_METHOD_ENTRIES}（解析没漏）",
        f"解析 {len(entries)} / 段内 Address 字面 {addr_literals}",
    )
    sj_map = script_rva_names(entries)
    d_map = dump_rva_names()
    r8b = compare_rvas(d_map, sj_map)
    print(
        f"[----] 8b 声明侧：dump RVA {len(d_map)} 个 · script.json RVA {len(sj_map)} 个 · "
        f"两边都有 {r8b['both']} 个"
    )
    print(
        f"[----] 8b 声明侧：名字不一致 {r8b['exact']} 个 · 归一化后仍不一致 "
        f"{r8b['normalized']} 个 —— {dict(r8b['buckets']) or '（分类桶全空）'}"
    )
    c.check(r8b["exact"] == 0, "8b 声明侧 RVA 名字不一致数 == 0", f"{r8b['exact']}")

    d_map_all = dump_rva_names(include_generic=True)
    r8c = compare_rvas(d_map_all, sj_map)
    print(
        f"[----] 8c 加泛型实例：dump RVA {len(d_map_all)} 个 · 两边都有 {r8c['both']} 个 · "
        f"只在 script.json 里的 {len(set(sj_map) - set(d_map_all))} 个"
        f"（声明侧 {len(set(sj_map) - set(d_map))} 个）"
    )
    print(
        f"[----] 8c 加泛型实例：精确不一致 {r8c['exact']} 个（分类 {dict(r8c['buckets'])}）"
        f" · 归一化后仍不一致 {r8c['normalized']} 个"
    )
    c.check(
        r8c["normalized"] == 0,
        "8c 加泛型实例后·归一化不一致数 == 0",
        f"归一化 {r8c['normalized']} / 精确 {r8c['exact']}",
    )
    keys = sorted(set(sj_map) & set(d_map))  # 只注入"两边都有"的 RVA，差值才可精确预期
    sj_bad = dict(sj_map)
    for i in range(0, len(keys), 1000):
        sj_bad[keys[i]] = {"ZzzInjected$$ZzzInjected"}
    injected = len(range(0, len(keys), 1000))
    r8d = compare_rvas(d_map, sj_bad)
    c.check(
        r8d["exact"] - r8b["exact"] == injected,
        f"8d 尺子能红·注入 {injected} 个坏名字后不一致数必须涨 {injected}",
        f"注入前 {r8b['exact']} → 注入后 {r8d['exact']}",
    )
    print(f"     （第 8 项耗时 {time.time() - t8:.1f}s）")

    # 9 总数 + 解析异常
    print("-" * 78)
    print(
        f"9 总数（可复算）：类型 {len(types)} · 声明方法 {len(methods)}"
        f"（有地址 {counts.get('methods_with_addr')} /"
        f" 无地址 {counts.get('methods_without_addr')}）"
        f" · 字段 {len(fields)} · 泛型实例 {len(generic)}（{counts.get('gi_blocks')} 个块 /"
        f" {counts.get('gi_rva_groups')} 个 RVA）· 程序集 {counts.get('assemblies')}"
    )
    dump_sha = meta["source"]["dump_cs"]["sha256"][:16]
    print(
        f"  产物：{TYPES_TSV.name} / {METHODS_TSV.name} / {FIELDS_TSV.name} / "
        f"{GENERIC_TSV.name} / {META_JSON.name}"
    )
    print(f"  dump.cs sha256 {dump_sha}… 字节 {meta['source']['dump_cs']['bytes']}")
    c.check(not meta.get("anomalies"), "9b 解析零异常", f"{meta.get('anomalies')}")

    print("-" * 78)
    if c.fails:
        print(f"自检未通过：{c.fails} 项 FAIL（总耗时 {time.time() - t0:.1f}s）")
        return 1
    print(f"自检全部通过（总耗时 {time.time() - t0:.1f}s）")
    return 0


# ---------------------------------------------------------------- CLI


def _print_stats() -> None:
    if not index_ready():
        print("索引不存在，跑 --build")
        return
    meta = load_meta()
    print(
        json.dumps(
            {"source": meta["source"], "counts": meta["counts"], "anomalies": meta["anomalies"]},
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "类型/方法总表：dump.cs → types/methods/fields/generic_insts 四张 gz 表 + 查询 + 自检"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--build", action="store_true", help="重建索引（扫一遍 dump.cs）")
    ap.add_argument("--type", metavar="SUBSTR", help="按类型全名子串查（配 --fields 列字段）")
    ap.add_argument("--method", metavar="SUBSTR", help="按 类型::方法 子串查")
    ap.add_argument("--field", metavar="SUBSTR", help="按 类型::字段 子串查")
    ap.add_argument(
        "--rva", metavar="0x...", help="RVA → 类型::方法（认 VA：>=0x180000000 自动减基址）"
    )
    ap.add_argument("--fields", action="store_true", help="配合 --type：打印命中类型的字段与偏移")
    ap.add_argument("--limit", type=int, default=50, help="每条查询最多打印多少行（默认 50）")
    ap.add_argument("--all", action="store_true", help="不截断，全列")
    ap.add_argument("--stats", action="store_true", help="打印 meta.json 的计数与指纹")
    ap.add_argument(
        "--selftest", action="store_true", help="自检：正控/负控/独立复算/script.json 对拍"
    )
    args = ap.parse_args(argv)

    if args.build:
        meta = build()
        print(f"构建完成：{json.dumps(meta['counts'], ensure_ascii=False, sort_keys=True)}")
        print(f"异常：{meta['anomalies'] or '{}'}  耗时 {meta['elapsed_sec']}s")
        return 0

    if args.selftest:
        return selftest()

    if args.stats:
        _print_stats()
        return 0

    if args.type:
        ensure_index()
        query_type(args.type, args.fields, args.limit, args.all)
        return 0
    if args.method:
        ensure_index()
        query_method(args.method, args.limit, args.all)
        return 0
    if args.field:
        ensure_index()
        query_field(args.field, args.limit, args.all)
        return 0
    if args.rva:
        ensure_index()
        return 0 if query_rva(args.rva, args.limit, args.all) else 1

    ap.print_help()
    if index_ready():
        _print_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
