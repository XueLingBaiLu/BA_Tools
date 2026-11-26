r"""字段索引（权威版）：**类型.字段 → 偏移 → 读/写点**。

来源与分工（三份互补；**偏移以 dump.cs 为准**）：
  1) _work/il2cppdump/dump.cs         Il2CppDumper 的**权威元数据**：类型 + 字段偏移（含基类布局）+
                                      每个方法的 RVA/VA ⇒ 名字→偏移的**主源**
  2) _work/cpp2il_cs/DiffableCs       Cpp2IL 的 C# 桩：字段带 //Field offset 注释
                                      ⇒ **副源**，用于对拍
                                      ⚠️ 实测它有两处毛病：3,501 条偏移是 0（哨兵）、
                                      226 条比权威小一段基类
  3) _work/isil/IsilDump              逐方法汇编：偏移→读/写点、调用点
                                      ⚠️ 每个文件**第一行是 Type: <全名>**（嵌套类型写成 A+B）；
                                          用它对类型，
                                         别用文件名（文件名把嵌套写成 _NestedType_，会 join 不上）
  4) _work/il2cppdump/script.json     ScriptMethod（RVA→名字，328,821 个不同 RVA）⇒ 调用地址→方法名

归属规则（不写出来读数就不可信）：
  * rcx 是 Win64 的 this；mov rbx, rcx 之类的别名也认，别名被覆写即失效；
  * 第一个操作数是内存 ⇒ 写；否则 ⇒ 读（cmp/test 一律读）；
  * 自动属性字段在元数据里叫 <X>k__BackingField ⇒ 归一化成 X。

用法（BA_Mod_Maker_2.0 下、仓内 venv）：
    .venv\Scripts\python.exe tools\field_index.py --build
    .venv\Scripts\python.exe tools\field_index.py --field ModelFileName
    .venv\Scripts\python.exe tools\field_index.py --who Turrets.ModelFileName
    .venv\Scripts\python.exe tools\field_index.py --selftest
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import os
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
DUMPCS = WORK / "il2cppdump" / "dump.cs"
DIFFABLE = WORK / "cpp2il_cs" / "DiffableCs"
ISIL = WORK / "isil" / "IsilDump"
SCRIPT = WORK / "il2cppdump" / "script.json"
OUT = WORK / "field_index"
IMAGE_BASE = 0x180000000

#: 判据基线（本次实测写死；将来恶化即红）
CPP2IL_DISAGREE_MAX = 0.01        # Cpp2IL vs 权威 的不一致率上限（实测 0.40%）
CODE_AGREE_MIN = 0.90             # 代码反推 vs 权威 的一致率下限（实测见 --selftest 打印）

_NS_RE = re.compile(r"^// Namespace:\s*(.*)$")
_TYPE_RE = re.compile(r"^\s*(?:(?:public|internal|private|protected)\s+)?"
                      r"(?:(?:sealed|abstract|static|readonly)\s+)*"
                      r"(class|struct|interface|enum)\s+([\w<>\.\x60]+)")
_FIELD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|readonly|const|volatile|new)\s+)+"
    r"([\w<>\[\],\.\?]+)\s+([\w<>]+)\s*;\s*//\s*0x([0-9A-Fa-f]+)\s*$")
_CPP_FIELD_RE = re.compile(
    r"([\w<>,\[\]\.\?]+)\s+([\w<>]+)\s*(?:=\s*[^;]+)?;"
    r"\s*//\s*Field offset:\s*0x([0-9A-Fa-f]+)")
_TYPE_LINE_RE = re.compile(r"^Type:\s*(.+)$")
_MEM_RE = re.compile(r"\[(r[a-z0-9]{1,3})(?:\+([0-9A-Fa-f]+)h)?\]")
_METHOD_RE = re.compile(r"^Method:\s*(.+)$")
_SECT_RE = re.compile(r"^(Disassembly|ISIL):\s*$")
_ACC_RE = re.compile(r"\b(?:get|set)_(\w+)\s*\(")
_CALL_SYM_RE = re.compile(r"^\d+\s+(?:Call|Tailcall|Jmp)\s+([A-Za-z_\.\"][\w\.\x60<>:/\[\]]*)")
_CALL_ADDR_RE = re.compile(r"^\d+\s+(?:Call|Tailcall|Jmp)\s+(?:near ptr\s+)?(0x[0-9A-Fa-f]+)")
_INSTR_RE = re.compile(r"^\s*(?:[0-9A-Fa-f]+:\s+)?([a-z][a-z0-9]*)\s+(.*)$")
_BACKING_RE = re.compile(r"^<(.*)>k__BackingField$")


def norm_field(raw: str) -> str:
    hit = _BACKING_RE.match(raw)
    return hit.group(1) if hit else raw


def nkey(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# --------------------------------------------------------------------------- #
# 1) dump.cs —— 权威
# --------------------------------------------------------------------------- #
def load_dumpcs():
    fields: dict[str, list[dict]] = {}
    types: dict[str, dict] = {}
    ns, cur = "", None
    for line in DUMPCS.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = _NS_RE.match(line)
        if m:
            ns = m.group(1).strip()
            continue
        mt = _TYPE_RE.match(line)
        if mt and "// TypeDefIndex" in line:
            name = mt.group(2)
            cur = (ns + "." + name) if ns else name
            base = ""
            mb = re.search(r":\s*([^/]+?)\s*//\s*TypeDefIndex", line)
            if mb:
                base = mb.group(1).strip()
            tdi = re.search(r"TypeDefIndex:\s*(\d+)", line)
            types[nkey(cur)] = {"type": cur, "ns": ns, "base": base,
                                "tdi": int(tdi.group(1)) if tdi else -1}
            continue
        if cur is None:
            continue
        mf = _FIELD_RE.match(line)
        if mf:
            decl, raw, off = mf.group(1), mf.group(2), int(mf.group(3), 16)
            head = line.split(decl)[0]
            fields.setdefault(nkey(cur), []).append(
                {"type": cur, "field": norm_field(raw), "raw": raw, "offset": off,
                 "static": ("static" in head or "const" in head), "src": "dump.cs"})
    nf = sum(len(v) for v in fields.values())
    print(f"[1] dump.cs(权威): 类型 {len(types):,} · 字段 {nf:,}"
          f"（其中静态 {sum(1 for v in fields.values() for r in v if r['static']):,}）")
    return fields, types


# --------------------------------------------------------------------------- #
# 2) DiffableCs —— 副源（只用于对拍）
# --------------------------------------------------------------------------- #
def load_cpp2il() -> dict[str, list[dict]]:
    table: dict[str, list[dict]] = {}
    for path in DIFFABLE.rglob("*.cs"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "Field offset:" not in text:
            continue
        parts = list(path.relative_to(DIFFABLE).parts)
        parts[-1] = Path(parts[-1]).stem
        base = ".".join(parts[1:]) if len(parts) > 1 else parts[0]
        stack: list[tuple[str, int]] = []
        pending: list[str] = []
        depth = 0
        for line in text.splitlines():
            md = _TYPE_RE.search(line)
            if md:
                pending.append(md.group(2))
            if "Field offset:" in line and stack:
                hit = _CPP_FIELD_RE.search(line)
                if hit:
                    decl, raw, off = hit.group(1), hit.group(2), int(hit.group(3), 16)
                    head = line.split(decl)[0]
                    tname = (base if len(stack) == 1
                             else base + "." + ".".join(n for n, _d in stack[1:]))
                    table.setdefault(nkey(tname), []).append(
                        {"type": tname, "field": norm_field(raw), "raw": raw, "offset": off,
                         "static": ("static" in head or "const" in head),
                         "src": str(path.relative_to(DIFFABLE))})
            for ch in line:
                if ch == "{":
                    depth += 1
                    if pending:
                        stack.append((pending.pop(0), depth))
                elif ch == "}":
                    while stack and stack[-1][1] >= depth:
                        stack.pop()
                    depth -= 1
    print(f"[2] DiffableCs(副源): 字段 {sum(len(v) for v in table.values()):,}")
    return table


# --------------------------------------------------------------------------- #
# 3) ISIL —— 偏移 → 读/写点、调用点
# --------------------------------------------------------------------------- #
def scan_isil(primary):
    sites, callers, acc_off = [], [], {}
    addr_calls: dict[int, list[tuple[str, str]]] = {}
    st = {"files": 0, "nested_files": 0, "methods": 0, "access": 0, "attributed": 0,
          "calls_sym": 0, "calls_addr": 0, "unknown_type": 0}
    t0 = time.time()
    for path in ISIL.rglob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        st["files"] += 1
        rel = path.relative_to(ISIL)
        full = ""
        for line in lines[:4]:
            mt = _TYPE_LINE_RE.match(line)
            if mt:
                full = mt.group(1).strip()
                break
        if not full:                       # 兜底：路径（嵌套会 join 不上，计入 unknown_type）
            parts = list(rel.parts)
            parts[-1] = Path(parts[-1]).stem
            full = ".".join(parts[1:]) if len(parts) > 1 else parts[0]
            st["unknown_type"] += 1
        if "+" in full:
            st["nested_files"] += 1
        key = nkey(full)
        by_off: dict[int, list[dict]] = {}
        for row in primary.get(key, []):
            if not row["static"]:
                by_off.setdefault(row["offset"], []).append(row)
        method, section, acc = "", "", None
        n_ins = 0
        this_regs = {"rcx"}
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            hit = _METHOD_RE.match(line)
            if hit:
                method = hit.group(1).strip()
                st["methods"] += 1
                section, this_regs, n_ins = "", {"rcx"}, 0
                m = _ACC_RE.search(method)
                acc = m.group(1) if m else None
                continue
            if _SECT_RE.match(line):
                section = line.strip().rstrip(":")
                this_regs = {"rcx"}
                continue
            if section == "ISIL":
                m = _CALL_SYM_RE.match(stripped)
                if m:
                    st["calls_sym"] += 1
                    callers.append((full, method, m.group(1)))
                else:
                    m = _CALL_ADDR_RE.match(stripped)
                    if m:
                        st["calls_addr"] += 1
                        addr_calls.setdefault(int(m.group(1), 16), []).append((full, method))
                continue
            if section != "Disassembly":
                continue
            ins = _INSTR_RE.match(line)
            if not ins:
                continue
            n_ins += 1
            mnem, ops = ins.group(1), ins.group(2)
            low = mnem.lower()
            if low in ("mov", "lea") and "," in ops:
                dest, src = (x.strip() for x in ops.split(",", 1))
                dm = re.fullmatch(r"(r[a-z0-9]{1,3})", dest)
                sm = re.fullmatch(r"(r[a-z0-9]{1,3})", src)
                if dm:
                    d = dm.group(1)
                    if sm and sm.group(1) in this_regs:
                        this_regs.add(d)
                    elif d != "rcx":
                        this_regs.discard(d)
            mems = _MEM_RE.findall(ops)
            if mems:
                st["access"] += len(mems)
                first = ops.split(",", 1)[0]
                first_mem = "[" in first
                writing = first_mem and low not in ("cmp", "test")
                first_key = _MEM_RE.findall(first)[0] if first_mem else None
                for reg, off in mems:
                    if reg not in this_regs or not off:
                        continue
                    off_i = int(off, 16)
                    if acc and (key, acc) not in acc_off:
                        acc_off[(key, acc)] = (off_i, n_ins)
                    for row in by_off.get(off_i, []):
                        st["attributed"] += 1
                        sites.append((row["type"], row["field"], row["offset"],
                                      "W" if (writing and (reg, off) == first_key) else "R",
                                      method, str(rel), lineno))
    print(f"[3] ISIL: {st['files']:,} 文件（嵌套 {st['nested_files']:,}）·"
          f" {st['methods']:,} 方法 · "
          f"内存访问 {st['access']:,} · 归属 this {st['attributed']:,} · "
          f"调用（符号 {st['calls_sym']:,} / 地址 {st['calls_addr']:,}）· {time.time()-t0:.0f}s")
    return sites, callers, acc_off, addr_calls, st


def load_script():
    t0 = time.time()
    text = SCRIPT.read_text(encoding="utf-8", errors="ignore")
    methods: dict[int, list[str]] = {}
    for a, n in re.findall(r'"Address":\s*(\d+),\s*"Name":\s*"((?:[^"\\]|\\.)*)"', text):
        methods.setdefault(int(a), []).append(json.loads('"' + n + '"'))
    i0 = text.find('"ScriptString"')
    i1 = text.find('"ScriptMetadata"', i0)
    strings = {}
    _addr_pat = r'"Address":\s*(\d+),\s*"Value":\s*"((?:[^"\\]|\\.)*)"'
    for a, v in re.findall(_addr_pat, text[i0:i1] if i0 >= 0 else ""):
        strings[int(a)] = json.loads('"' + v + '"')
    print(f"[4] script.json: RVA {len(methods):,} 个"
          f"（条目 {sum(len(v) for v in methods.values()):,}）· "
          f"字符串 {len(strings):,} · {time.time()-t0:.0f}s")
    return methods, strings


# --------------------------------------------------------------------------- #
def build() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    prim, types = load_dumpcs()
    sec = load_cpp2il()

    # 主表 + 副源对拍列
    agree = conflict = sentinel = 0
    nfields = 0
    with gzip.open(OUT / "fields.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("type\tfield\traw\toffset\thex\tstatic\toffset_cpp2il\tcpp2il_agree\tsrc\n")
        for key, rows in prim.items():
            smap: dict[str, list[dict]] = {}
            for r in sec.get(key, []):
                smap.setdefault(r["field"], []).append(r)
            for r in rows:
                nfields += 1
                c = smap.get(r["field"])
                c_off, c_agr = -1, -1
                if c and not r["static"] and not c[0]["static"]:
                    c_off = c[0]["offset"]
                    if c_off == 0:
                        sentinel += 1
                    elif c_off == r["offset"]:
                        agree += 1
                        c_agr = 1
                    else:
                        conflict += 1
                        c_agr = 0
                fh.write(f"{r['type']}\t{r['field']}\t{r['raw']}\t{r['offset']}\t0x{r['offset']:X}\t"
                         f"{int(r['static'])}\t{c_off}\t{c_agr}\t{r['src']}\n")

    sites, callers, acc_off, addr_calls, st = scan_isil(prim)
    with gzip.open(OUT / "sites.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("type\tfield\toffset\thex\trw\tmethod\tfile\tline\n")
        for s in sites:
            fh.write(f"{s[0]}\t{s[1]}\t{s[2]}\t0x{s[2]:X}\t{s[3]}\t{s[4]}\t{s[5]}\t{s[6]}\n")

    # 代码反推 vs 权威
    cmp_n = cmp_ok = 0
    with gzip.open(OUT / "accessors.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("type\tfield\toffset_code\tn_ins\toffset_auth\thex_auth\tagree\n")
        for key, rows in prim.items():
            for r in rows:
                k = (key, r["field"])
                if k in acc_off and not r["static"]:
                    code_off, n_ins = acc_off[k]
                    ok = code_off == r["offset"]
                    cmp_n += 1
                    cmp_ok += ok
                    fh.write(f"{r['type']}\t{r['field']}\t{code_off}\t{n_ins}\t{r['offset']}\t"
                             f"0x{r['offset']:X}\t{int(ok)}\n")

    methods, strings = load_script()
    resolved = {a: (methods.get(a - IMAGE_BASE) or [None])[0] for a in addr_calls}
    n_ok = sum(1 for v in resolved.values() if v)
    with gzip.open(OUT / "calls.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("caller_type\tcaller\tcallee\tkind\n")
        for c in callers:
            fh.write(f"{c[0]}\t{c[1]}\t{c[2]}\tsym\n")
        for addr, lst in addr_calls.items():
            name = resolved.get(addr) or f"0x{addr:X}"
            for ctype, cm in lst:
                fh.write(f"{ctype}\t{cm}\t{name}\taddr\n")

    stats = {**st, "fields": nfields, "types": len(types), "sites": len(sites),
             "calls_sym_rows": len(callers),
             "call_addr_resolved": n_ok, "call_addr_total": len(addr_calls),
             "script_rvas": len(methods), "script_strings": len(strings),
             "cpp2il_agree": agree, "cpp2il_conflict": conflict, "cpp2il_sentinel": sentinel,
             "code_cmp": cmp_n, "code_agree": cmp_ok}
    (OUT / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[落盘] {OUT}")
    print(f"  fields={nfields:,} sites={len(sites):,}"
          f" calls={len(callers)+sum(len(v) for v in addr_calls.values()):,}")
    print(f"[对拍·权威 vs Cpp2IL] 可比 {agree+conflict:,} · 一致 {agree:,} · 不一致 {conflict:,}"
          f"（{100.0*conflict/max(1,agree+conflict):.2f}%）· 哨兵 0 有 {sentinel:,}")
    print(f"[对拍·代码 vs 权威] 可比 {cmp_n:,} · 一致 {cmp_ok:,}"
          f"（{100.0*cmp_ok/max(1,cmp_n):.1f}%）")
    print(f"[地址] Call {len(addr_calls):,} 个 · 解出名字 {n_ok:,}"
          f"（{100.0*n_ok/max(1,len(addr_calls)):.1f}%）")
    return 0


# --------------------------------------------------------------------------- #
def _rows(name: str):
    p = OUT / "fields.tsv.gz"
    if not p.exists():
        raise SystemExit(f"没有索引：先 --build（缺 {p}）")
    needle = name.lower()
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            if needle in line.split("\t")[1].lower():
                yield line.rstrip("\n").split("\t")


def query_field(name: str) -> int:
    n = 0
    for c in _rows(name):
        print(f"  {c[0]}.{c[1]} @ {c[4]} ({c[3]})  {'static' if c[5] == '1' else 'instance'}")
        n += 1
        if n >= 40:
            print("  …（更多见 _work/field_index/fields.tsv.gz）")
            break
    print(f"--field {name!r}：{n} 条")
    return 0


def query_who(spec: str) -> int:
    if "." not in spec:
        raise SystemExit("--who 要写成 类型.字段，例：Turrets.ModelFileName")
    tname, fname = spec.rsplit(".", 1)
    keys = {(c[0], c[1]) for c in _rows(fname) if c[0].lower().endswith(tname.lower())}
    if not keys:
        print(f"**红** 找不到字段 {spec}")
        return 1
    type_, field_ = sorted(keys)[0]
    print(f"=== {type_}.{field_}")
    by_m: dict[str, int] = {}
    n = 0
    with gzip.open(OUT / "sites.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if c[0] == type_ and c[1] == field_:
                by_m[f"{c[4]}  {c[5]}:{c[7]}"] = by_m.get(f"{c[4]}  {c[5]}:{c[7]}", 0) + 1
                n += 1
    print(f"  读写点 {n} 处，按方法归并 {len(by_m)} 个：")
    for k in sorted(by_m)[:25]:
        print(f"    {k}")
    acc = {f"get_{field_}", f"set_{field_}"}
    print(f"  跨类调用点（谁调了 {sorted(acc)}）：")
    m2 = 0
    with gzip.open(OUT / "calls.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if c[2] in acc or c[2].rsplit(".", 1)[-1] in acc:
                print(f"    {c[0]}.{c[1]}  ->  {c[2]}  ({c[3]})")
                m2 += 1
                if m2 >= 25:
                    break
    print(f"  调用点 {m2} 处（上限 25；全量见 calls.tsv.gz）")
    return 0


def db_columns() -> int:
    """表.列 → 模型属性偏移 → 读/写点（把"数据库那一侧"和"代码那一侧"接起来）。

    数据源：DataBaseCompiled/BA_Mod.zip（24 张表的明文）× fields.tsv.gz × sites.tsv.gz。
    用途：改数据库某一格之前先问"谁读它"；也能一眼看出**死列**（没人读）与**只有 REST 镜像读**的列。
    """
    import zipfile
    zpath = ROOT.parent / "DataBaseCompiled" / "BA_Mod.zip"
    if not zpath.exists():
        raise SystemExit(f"缺 {zpath}")
    fmap: dict[str, dict[str, tuple[int, str]]] = {}
    with gzip.open(OUT / "fields.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            fmap.setdefault(nkey(c[0]), {})[c[1]] = (int(c[3]), c[4], c[0])   # 带上完整类型名
    nread: Counter = Counter()
    nwrite: Counter = Counter()
    readers: dict[tuple[str, str], Counter] = {}
    with gzip.open(OUT / "sites.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            k = (nkey(c[0]), c[1])
            if c[4] == "W":
                nwrite[k] += 1
            else:
                nread[k] += 1
                readers.setdefault(k, Counter())[c[5]] += 1
    rows_out = []
    api_list: list[tuple] = []
    joined = dead = api_only = 0
    with zipfile.ZipFile(zpath) as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".json")):
            table = Path(name).stem
            data = json.loads(z.read(name).decode("utf-8"))
            body = data.get("Rows", data) if isinstance(data, dict) else data
            cols: list[str] = []
            for r in (body if isinstance(body, list) else []):
                if isinstance(r, dict):
                    for k in r:
                        if k not in cols:
                            cols.append(k)
            tkey = nkey("BrokenArrow.DataBase.Models." + table)
            # 表名复数、类型名可能单数（PlaneFlyPresets→PlaneFlyPreset）
            if tkey not in fmap:
                cands = [k for k in fmap
                         if k.endswith(nkey(table)) or k.endswith(nkey(table).rstrip("s"))]
                tkey = cands[0] if cands else ""
            fields = fmap.get(tkey, {})
            if tkey:
                joined += 1
            for col in cols:
                off, hexv, ft = fields.get(col, (-1, "", tkey or "-"))
                full_type = ft or (tkey or "-")
                k = (tkey, col)
                nr, nw = nread.get(k, 0), nwrite.get(k, 0)
                # ⚠️ 第一版只列前 3 个读者，结果被当成"全部读者"读
                # （队友就把 Units.Type 的 5 个读成 3 个）
                #    ⇒ 提到 8 个 + 单列 n_readers + 超出写 …+N
                rc = readers.get(k, Counter())
                shown = rc.most_common(8)
                top = " ".join(f"{m.rsplit(' ', 1)[-1]}x{n}" for m, n in shown)
                if len(rc) > len(shown):
                    top += f" …+{len(rc) - len(shown)}"
                # ⚠️ 判据要"除访问器以外"再判：每列都有自己的 get_/set_，
                # 把 getter 算进去这条永远不成立（红不了的尺子）。
                # ⚠️ 方法名那列是完整 Method: 头（结尾带 "()"）⇒ 必须先切掉括号，
                # 否则这个过滤是死代码（2026-10-07 实测踩到）
                others = [m for m in readers.get(k, {})
                          if not m.split("(", 1)[0].rsplit(".", 1)[-1].endswith(
                              ("get_" + col, "set_" + col))]
                if nr == 0:
                    dead += 1
                elif others and all(m.startswith("NetworkCommon") for m in others):
                    api_only += 1
                    api_list.append((table, col, hexv or "-", nr, sorted(set(others))[:3]))
                rows_out.append((table, col, full_type, hexv or "-", nr, nw, top, len(rc)))
    with gzip.open(OUT / "db_columns.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("table\tcolumn\tmodel_type\toffset_hex\tn_read\tn_write\ttop_readers\tn_readers\n")
        for r in rows_out:
            fh.write("\t".join(str(x) for x in r) + "\n")
    print(f"[表.列 → 读点] 表 {joined} 张 · 列 {len(rows_out):,} 个")
    print(f"  归属到读点的列 {len(rows_out)-dead:,} · 没归属到读点的 {dead:,} · "
          f"除访问器外只有 NetworkCommon 读的 {api_only:,}")
    print("  ⛔ 后两类**都不能当'死列/只进 API'判据**：归属规则只认 this(rcx 及别名)，"
          "跨类型/内联的读点会漏（反例：Units.ModelFileName 只归属到 getter，"
          "而它确实是模型加载键）")
    print("  读者最多的 5 列:")
    for r in sorted(rows_out, key=lambda x: -int(x[4]))[:5]:
        print(f"    {r[0]}.{r[1]} @{r[3]}  读 {r[4]} 写 {r[5]} · {r[6][:90]}")
    (OUT / "db_columns_stats.json").write_text(json.dumps(
        {"tables": joined, "columns": len(rows_out), "dead": dead, "api_only": api_only,
         "api_only_list": [list(x) for x in api_list]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    for t, col, off, nr, oth in api_list:
        print(f"    [只有 NetworkCommon 读] {t}.{col} @{off} 读 {nr} · 只有 {oth}")
    return 0


def selftest() -> int:
    sp = OUT / "stats.json"
    if not sp.exists():
        raise SystemExit("没有 stats.json：先 --build")
    stats = json.loads(sp.read_text(encoding="utf-8"))
    with gzip.open(OUT / "fields.tsv.gz", "rt", encoding="utf-8") as fh:
        fields = [line.split("\t") for line in fh.read().splitlines()[1:]]
    ok = True

    def check(good, text):
        nonlocal ok
        ok = ok and good
        print(("  OK   " if good else "  **红** ") + text)

    print("=== 正控 1：三个 ModelFileName 的偏移（权威 dump.cs）")
    for suffix, want in (("Units", 0x48), ("Weapons", 0x40), ("Turrets", 0x20)):
        got = {int(c[3]) for c in fields if c[1] == "ModelFileName" and c[0].endswith(suffix)}
        check(want in got,
              f"{suffix}.ModelFileName = {sorted(hex(g) for g in got)}（要 {hex(want)}）")

    print("=== 正控 2：访问器断向（Units.ModelFileName）")
    with gzip.open(OUT / "sites.tsv.gz", "rt", encoding="utf-8") as fh:
        sites = [line.split("\t") for line in fh.read().splitlines()[1:]]
    u = [c for c in sites if c[0].endswith("Units") and c[1] == "ModelFileName"]
    check(any("get_ModelFileName" in c[5] and c[4] == "R" for c in u), "get_ModelFileName 记成读")
    check(any("set_ModelFileName" in c[5] and c[4] == "W" for c in u), "set_ModelFileName 记成写")

    print("=== 负控 3：不存在的字段名")
    check(len(list(_rows("ModelFileNameXyz"))) == 0, "ModelFileNameXyz → 0 命中")

    print("=== 负控 4：偏移 +1 后应无线索（尺子能红）")
    others = [c for c in sites if c[0].endswith("Units") and int(c[2]) == 0x49]
    check(len(others) == 0, f"Units 里偏移 0x49 的归属访问 = {len(others)}（应为 0）")

    print("=== 正控 5：Call 地址 → 方法名解析率")
    rate = 100.0 * stats.get("call_addr_resolved", 0) / max(1, stats.get("call_addr_total", 1))
    check(rate >= 50.0, f"解析率 {rate:.1f}%")

    print("=== 正控 6：权威 vs Cpp2IL（副源）对拍")
    a, c = stats["cpp2il_agree"], stats["cpp2il_conflict"]
    bad = c / max(1, a + c)
    check(bad <= CPP2IL_DISAGREE_MAX,
          f"可比 {a+c:,} · 一致 {a:,} · 不一致 {c:,}"
          f"（{100*bad:.2f}% ≤ 上限 {100*CPP2IL_DISAGREE_MAX:.0f}%）"
          f" · 另有哨兵 0 {stats['cpp2il_sentinel']:,} 条不计")

    print("=== 正控 7：ISIL 代码反推 vs 权威（同一字段、平凡访问器）")
    with gzip.open(OUT / "accessors.tsv.gz", "rt", encoding="utf-8") as fh:
        acc = [line.split("\t") for line in fh.read().splitlines()[1:]]
    triv = [x for x in acc if int(x[3]) <= 3]
    good = [x for x in triv if x[6] == "1"]
    rate7 = len(good) / max(1, len(triv))
    check(rate7 >= CODE_AGREE_MIN,
          f"平凡访问器 {len(triv):,} 条 · 一致 {len(good):,}"
          f"（{100*rate7:.1f}% ≥ 下限 {100*CODE_AGREE_MIN:.0f}%）")
    ntriv = [x for x in acc if int(x[3]) > 3]
    print(f"        （非平凡 {len(ntriv):,} 条不计：那些方法开头干别的活，"
          f"'第一条访问=该字段'不成立）")

    print()
    print("field_index 自检：" + ("全绿 ✔" if ok else "**有红** ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="字段索引（权威版）：名字 -> 偏移 -> 读/写点")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--field", default="")
    ap.add_argument("--who", default="")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--db-columns", action="store_true", help="表.列 -> 模型偏移 -> 读/写点")
    a = ap.parse_args()
    if a.build:
        raise SystemExit(build())
    if a.field:
        raise SystemExit(query_field(a.field))
    if a.who:
        raise SystemExit(query_who(a.who))
    if a.selftest:
        raise SystemExit(selftest())
    if a.db_columns:
        raise SystemExit(db_columns())
    raise SystemExit("给一个动作：--build / --field X / --who T.X / --selftest")
