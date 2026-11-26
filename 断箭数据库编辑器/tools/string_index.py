#!/usr/bin/env python
"""字符串字面量 → 引用点索引（Broken Arrow / IL2CPP）。

判据一行（什么算"引用了这个字面量"）
------------------------------------
    在某个 .txt 的 ISIL: 段里，某条指令的操作数中出现 [0xVA]，
    且 VA - 0x180000000 等于该字面量在 stringliteral.json / script.json.ScriptString
    里的 address —— 记为一次引用（kind=addr，落在方法的元数据初始化块上）。

另设一条值形通道（kind=val）：ISIL: 段里 Move <目标>, "<字面量值>" ——
同一引用的更精确落点（真正消费这条字符串的那条指令）。值形是地址形的子集
（实测 value-only = 0），所以"有没有被引用"一律以 addr 为准。

为什么只从 ISIL 段收：Disassembly 段的 jmp near ptr …h 分不清尾调用与函数内跳转
（TailCall 与 Branch 两种都渲染成 jmp），而 ISIL 段把两者分开了。字面量地址不涉及这条，
但口径统一：地址型引用只认 ISIL 段。
（Disassembly 段的 lea rcx,[…h] / mov rdx,[…h] 用作完备性交叉核对：
--selftest --deep 会实测"只被 Disassembly 命中、ISIL 没命中"的字面量数 = 0。）

产物（gzip；用户明确讨厌占硬盘的垃圾文件）
------------------------------------------
_work/string_index/strings.tsv.gz   字面量 → RVA / VA / 引用数 / 被几个方法引用
_work/string_index/refs.tsv.gz      字面量 → 引用它的 类型.方法 + isil 文件:行
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from collections import defaultdict

IMAGE_BASE = 0x180000000

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 三个目录**可覆盖**（命令行 --isil / --dump-dir / --out，或同名环境变量），
# **默认值一字未改** —— 已有会话读的还是 1.2.0 那份旧产物。
# 1.2.1（GameAssembly.dll 换代 ⇒ 旧索引全过期）：
#   set BA_MOD_MAKER_IL2CPP_DIR=<repo>\_work\il2cpp121\il2cppdump
#   set BA_MOD_MAKER_ISIL_DIR=<repo>\_work\il2cpp121\isil\IsilDump   # 1.2.1 的 ISIL 还没挖
ISIL_DIR = os.environ.get("BA_MOD_MAKER_ISIL_DIR") or str(WORK / "isil" / "IsilDump")
DUMP_DIR = os.environ.get("BA_MOD_MAKER_IL2CPP_DIR") or str(WORK / "il2cppdump")
OUT_DIR = os.environ.get("BA_MOD_MAKER_STRING_INDEX_DIR") or str(WORK / "string_index")

STRINGLITERAL_JSON = os.path.join(DUMP_DIR, "stringliteral.json")
SCRIPT_JSON = os.path.join(DUMP_DIR, "script.json")


def _apply_dump_dir(dump_dir):
    """命令行 --dump-dir → 模块常量（build / selftest / 查询都读这几条）。"""
    global DUMP_DIR, STRINGLITERAL_JSON, SCRIPT_JSON
    DUMP_DIR = dump_dir
    STRINGLITERAL_JSON = os.path.join(dump_dir, "stringliteral.json")
    SCRIPT_JSON = os.path.join(dump_dir, "script.json")

STRINGS_HEADER = "# rva\tva\tn_refs\tn_methods\tvalue(\\t \\n \\r \\\\ 已转义)"
REFS_HEADER = "# rva\tkind\ttype\tmethod\tisil_file\tline\tvalue(\\t \\n \\r \\\\ 已转义)"

# ISIL 段里的地址操作数：[0x187D600F0]
RE_BRK = re.compile(r"\[0x([0-9A-Fa-f]+)\]")
# ISIL 段里的字面量：  170 LoadAddress rcx, [0x...]   /   594 Move rdx, "Azure"
RE_MOVE_STR = re.compile(r'^\d+ Move ([^"]*?), "(.*)"$')

_ESC = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
_UNESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'",
          "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def esc(text):
    out = []
    for ch in text:
        out.append(_ESC.get(ch, ch))
    return "".join(out)


def unesc(text):
    """把 ISIL 里 C# 风格的反斜杠转义还原成真实字符串。"""
    if "\\" not in text:
        return text
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(text[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(_UNESC.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- 输入

def load_literals(path=None):
    # ⚠ 默认值要在**调用时**取：写成 `path=STRINGLITERAL_JSON` 会在 import 时把路径定死，
    # 于是"覆盖模块常量再调"完全无效（同 tools/type_index.py::build 的坑）。
    path = path or STRINGLITERAL_JSON
    """-> list[(rva, value)]，按 rva 升序。"""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    pairs = [(int(e["address"], 16), e["value"]) for e in raw]
    pairs.sort()
    return pairs


def load_script_strings(path=None):
    path = path or SCRIPT_JSON   # 同上：默认值必须在调用时取
    """script.json 的 ScriptString 段 -> dict[rva] = value。"""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = {}
    for e in doc.get("ScriptString", []):
        out[int(e["Address"])] = e["Value"]
    return out


def iter_isil_files(root=ISIL_DIR):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.endswith(".txt"):
                yield os.path.join(dirpath, name)


# ---------------------------------------------------------------- 扫描

def scan_files(paths, addr_to_value, root=ISIL_DIR, progress=False):
    """扫 ISIL 段，收 addr 形与 val 形引用。

    -> (hits, stats)
       hits  : dict[rva] = list[(kind, type, method, relpath, lineno)]
    """
    value_to_rva = {v: k for k, v in addr_to_value.items()}
    hits = defaultdict(list)
    stats = {"files": 0, "lines": 0, "addr": 0, "val": 0, "unresolved_val": 0,
             "n_type_multi": 0}
    t0 = time.time()
    for path in paths:
        stats["files"] += 1
        if progress and stats["files"] % 4000 == 0:
            sys.stderr.write(f"\r  扫描 {stats['files']} 个文件  {time.time() - t0:.0f}s")
            sys.stderr.flush()
        rel = os.path.relpath(path, root).replace("\\", "/")
        cur_type = ""
        cur_method = ""
        in_isil = False
        seen_type = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if line.startswith("Type: "):
                    cur_type = line[6:].strip()
                    seen_type += 1
                    in_isil = False
                    continue
                if line.startswith("Method: "):
                    cur_method = line[8:].strip()
                    in_isil = False
                    continue
                if line.startswith("Disassembly:"):
                    in_isil = False
                    continue
                if line.startswith("ISIL:"):
                    in_isil = True
                    continue
                if not in_isil:
                    continue
                stats["lines"] += 1
                for m in RE_BRK.finditer(line):
                    rva = int(m.group(1), 16) - IMAGE_BASE
                    if rva in addr_to_value:
                        hits[rva].append(("addr", cur_type, cur_method, rel, lineno))
                        stats["addr"] += 1
                if '"' in line:
                    m = RE_MOVE_STR.match(line.strip())
                    if m:
                        rva = value_to_rva.get(unesc(m.group(2)))
                        if rva is not None:
                            hits[rva].append(("val", cur_type, cur_method, rel, lineno))
                            stats["val"] += 1
                        else:
                            stats["unresolved_val"] += 1
        if seen_type > 1:
            stats["n_type_multi"] += 1
    if progress:
        sys.stderr.write("\r" + " " * 60 + "\r")
    stats["secs"] = time.time() - t0
    return hits, stats


def count_kind(hits, rva, kind):
    """hits[rva] 里 kind 那一类有几行（addr 形 / val 形分开数）。"""
    return sum(1 for r in hits.get(rva, ()) if r[0] == kind)


# ---------------------------------------------------------------- 构建

def build(literals=None, root=ISIL_DIR, out_dir=OUT_DIR, progress=True):
    if literals is None:
        literals = load_literals()
    addr_to_value = dict(literals)
    if not os.path.isdir(root):
        raise SystemExit(f"找不到 ISIL 目录：{root}")
    hits, stats = scan_files(iter_isil_files(root), addr_to_value, root=root,
                             progress=progress)
    os.makedirs(out_dir, exist_ok=True)
    strings_path = os.path.join(out_dir, "strings.tsv.gz")
    refs_path = os.path.join(out_dir, "refs.tsv.gz")

    n_ref_lines = 0
    n_methods_total = 0
    with gzip.open(refs_path, "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(REFS_HEADER + "\n")
        for rva, value in literals:
            rows = hits.get(rva)
            if not rows:
                continue
            text = esc(value)
            for kind, typ, method, rel, lineno in rows:
                fh.write(f"{rva}\t{kind}\t{typ}\t{method}\t{rel}\t{lineno}\t{text}\n")
                n_ref_lines += 1

    with gzip.open(strings_path, "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(STRINGS_HEADER + "\n")
        for rva, value in literals:
            rows = hits.get(rva, ())
            n_addr = sum(1 for r in rows if r[0] == "addr")
            n_val = sum(1 for r in rows if r[0] == "val")
            methods = {(r[1], r[2]) for r in rows}
            n_methods_total += len(methods)
            va = f"0x{rva + IMAGE_BASE:X}"
            fh.write(f"{rva}\t{va}\t{n_addr + n_val}\t{len(methods)}\t{esc(value)}\n")
    stats["literals"] = len(literals)
    stats["literals_hit"] = sum(1 for rva, _v in literals if hits.get(rva))
    stats["ref_lines"] = n_ref_lines
    stats["method_refs"] = n_methods_total
    stats["strings_path"] = strings_path
    stats["refs_path"] = refs_path
    stats["strings_bytes"] = os.path.getsize(strings_path)
    stats["refs_bytes"] = os.path.getsize(refs_path)
    return stats


# ---------------------------------------------------------------- 查询

def load_index(out_dir=OUT_DIR):
    """-> list[(rva, va, n_refs, n_methods, value)]"""
    path = os.path.join(out_dir, "strings.tsv.gz")
    if not os.path.exists(path):
        return None
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            rows.append((int(parts[0]), parts[1], int(parts[2]), int(parts[3]), parts[4]))
    return rows


def iter_refs(rvas, out_dir=OUT_DIR):
    """流式过一遍 refs.tsv.gz，只产出 rva 在 rvas 里的行。"""
    want = set(rvas)
    path = os.path.join(out_dir, "refs.tsv.gz")
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            rva = int(parts[0])
            if rva in want:
                yield rva, parts[1], parts[2], parts[3], parts[4], int(parts[5]), parts[6]


def who(needle, out_dir=OUT_DIR, limit_literals=5, per_literal=5, exact=False,
        case_sensitive=False):
    """-> (n_literals, n_refs, 打印用文本)"""
    index = load_index(out_dir)
    if index is None:
        raise SystemExit("还没有索引：先跑 --build")
    key = needle if case_sensitive else needle.lower()
    matched = []
    for rva, va, n_refs, n_methods, value in index:
        hay = value if case_sensitive else value.lower()
        if (hay == key) if exact else (key in hay):
            matched.append((rva, va, n_refs, n_methods, value))
    matched.sort(key=lambda r: (-r[2], r[4]))
    total_refs = sum(r[2] for r in matched)
    by_rva = defaultdict(list)
    for row in iter_refs([r[0] for r in matched], out_dir):
        by_rva[row[0]].append(row)
    lines = [f"子串 {needle!r}  命中字面量 {len(matched)} 条 / 引用行 {total_refs} 条"]
    shown = matched[:limit_literals]
    for rva, va, n_refs, n_methods, value in shown:
        lines.append("")
        lines.append(f"-- {json.dumps(value, ensure_ascii=False)}   "
                     f"(RVA 0x{rva:X} / VA {va})  引用 {n_refs} 行 / {n_methods} 个方法")
        rows = by_rva.get(rva, [])
        seen = set()
        n = 0
        for _rva, kind, typ, method, rel, lineno, _v in rows:
            k = (kind, typ, method, rel, lineno)
            if k in seen:
                continue
            seen.add(k)
            lines.append(f"   [{kind}] {typ}::{method}   {rel}:{lineno}")
            n += 1
            if n >= per_literal:
                break
        if len(seen) < n_refs:
            lines.append(f"   ...（还有 {n_refs - n} 条，用 --per-literal N 调大）")
    if len(matched) > len(shown):
        lines.append("")
        lines.append(f"...（还有 {len(matched) - len(shown)} 条字面量命中，"
                     f"用 --limit-literals N 调大）")
    return len(matched), total_refs, "\n".join(lines)


# ---------------------------------------------------------------- 自检

def _selftest_file(root=ISIL_DIR):
    """一条已知引用的实测靶子：系统程序集 + 已知字面量 Azure。"""
    return os.path.join(root, "System.Drawing", "System", "Drawing", "KnownColorTable.txt")


def selftest(out_dir=OUT_DIR, root=ISIL_DIR, inject_fault=False, deep=False):
    checks = []

    def add(name, ok, reading):
        checks.append((name, ok, reading))
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<46} {reading}")

    literals = load_literals()
    truth = dict(literals)
    addr_to_value = dict(literals)
    value_to_rva = {v: k for k, v in literals}
    if inject_fault:
        # 故障注入（用来证明这套自检不是恒绿）：
        #   地址表整体 +1       → 正控 0 必须转红
        #   字面量查表键加哨兵   → 正控 1 必须转红
        addr_to_value = {rva + 1: v for rva, v in addr_to_value.items()}

    def look(text):
        """正控 ① 的字面量查表键；故障注入时错位，用来验证 ① 能红。"""
        return text + "\x00" if inject_fault else text

    # ---- 0) 尺子正控：同一个扫描函数，喂对的地址必须 >0（证明 ③ 不是恒真）
    tgt = _selftest_file(root)
    if not os.path.exists(tgt):
        add("0 尺子正控（靶子文件在）", False, f"缺文件 {tgt}")
        return _selftest_tail(checks)
    azure_rva = value_to_rva.get("Azure") or 0
    hits_ok, _st = scan_files([tgt], addr_to_value, root=root)
    n_ok = count_kind(hits_ok, azure_rva, "addr")
    n_ok_val = count_kind(hits_ok, azure_rva, "val")
    add("0 尺子正控 靶子内 addr 形命中 > 0", n_ok > 0,
        f"Azure RVA 0x{azure_rva:X} → addr 形 {n_ok} 行 / val 形 {n_ok_val} 行（要求 addr > 0）")

    # ---- ③ 负控（能红）：只把 Azure 这一条的地址人为 +1，其余地址照旧 ⇒ 必须 0 命中。
    #      ⚠ 不能把整张地址表 +1 —— 那样 "+1 后的地址" 恰好成了表里的一个键，
    #        扫描器当然能命中，等于把 ③ 做成"必然报红"的假读数（第一版就是这么错的）。
    probe_key = azure_rva + (1 if inject_fault else 0)
    probe = dict(addr_to_value)
    probe[probe_key + 1] = probe.pop(probe_key)
    hits_bad, _st = scan_files([tgt], probe, root=root)
    n_bad = count_kind(hits_bad, probe_key + 1, "addr")
    n_bad_val = count_kind(hits_bad, probe_key + 1, "val")
    add("3 负控 地址+1 → addr 形 0 命中", n_bad == 0,
        f"Azure RVA+1 0x{probe_key + 1:X} → addr 形 {n_bad} 行（要求 0）；"
        f"同探针里 val 形 {n_bad_val} 行（值形本来就与地址无关）")

    # ---- ③'' 值形负控：把 "Azure" 的值打成哨兵 ⇒ val 形必须 0 命中
    probe2 = dict(addr_to_value)
    probe2[probe_key] = "Azure\x00"
    hits_bad2, _st = scan_files([tgt], probe2, root=root)
    n_bad2 = count_kind(hits_bad2, probe_key, "val")
    n_keep2 = count_kind(hits_bad2, probe_key, "addr")
    add("3'' 负控 值形改成哨兵 → val 形 0 命中", n_bad2 == 0,
        f"'Azure' 换成 'Azure\\x00' 后 val 形 {n_bad2} 行（要求 0）；"
        f"addr 形 {n_keep2} 行未受影响")

    # ---- 索引
    index = load_index(out_dir)
    if index is None:
        print("   （没有索引，正在构建…）")
        build(literals=literals, root=root, out_dir=out_dir, progress=False)
        index = load_index(out_dir)
    by_value = {r[4]: r for r in index}

    # ---- ① 正控：两个已知字面量至少一个能查到引用点
    known = ["Mouse is null!", "Azure"]
    found = []
    n_found = 0
    for v in known:
        row = by_value.get(look(esc(v)))
        if row and row[2] > 0:
            n_found += 1
            found.append(f"{json.dumps(v)}={row[2]}行/{row[3]}方法")
        else:
            found.append(f"{json.dumps(v)}=0")
    add("1 正控 已知字面量有引用点", n_found >= 1,
        "; ".join(found) + "（要求至少一个 > 0）")

    # ---- ② 负控：不存在的字面量 → 0
    bogus = "zzz_no_such_literal_zzz"
    n_bogus = sum(1 for r in index if bogus in r[4])
    add("2 负控 不存在的字面量 → 0", n_bogus == 0,
        f"{bogus!r} → 索引里 {n_bogus} 条（要求 0）")

    # ---- ③' 负控：把已知字面量的 VA 人为 +1 后，用这个 VA 查索引 → 0 命中
    va_shift = {r[1]: r for r in index}
    va_readings = []
    for v in known:
        row = by_value.get(esc(v))
        if row is None:
            va_readings.append(f"{json.dumps(v)} 不在索引里")
            continue
        va = int(row[1], 16)
        got = va_shift.get(f"0x{va + 1:X}")
        va_readings.append(f"{json.dumps(v)} VA+1 0x{va + 1:X} → "
                           f"{'命中(FAIL)' if got else '0 命中'}")
    add("3' 负控 已知字面量 VA+1 查索引 → 0", all(r.endswith("0 命中") for r in va_readings),
        "; ".join(va_readings))

    # ---- ④ 与 script.json 的 ScriptString 对拍
    script = load_script_strings()
    shared = set(truth) & set(script)
    a_only = set(truth) - set(script)
    b_only = set(script) - set(truth)
    both_same = sum(1 for k in shared if script[k] == truth[k])
    add("4 对拍 stringliteral.json vs ScriptString",
        len(shared) == len(literals) == len(script) and not a_only and not b_only
        and both_same == len(shared),
        f"两边都有 {len(shared)} 条（值也一致 {both_same} 条）；"
        f"只在 stringliteral {len(a_only)} 条；只在 ScriptString {len(b_only)} 条")

    # ---- ⑤ 覆盖率读数（读数，不是判据）
    hit_lits = sum(1 for r in index if r[2] > 0)
    pct = 100.0 * hit_lits / max(1, len(index))
    print(f"[READ] 字面量总数 {len(index)}；有引用点的 {hit_lits} 条（{pct:.2f}%）；"
          f"无引用点 {len(index) - hit_lits} 条")

    # ---- ⑥ 深核对：Disassembly 段单独扫一遍，ISIL 不该漏
    if deep:
        dis_hits = scan_disassembly(iter_isil_files(root), set(truth))
        isil_set = {r[0] for r in index if r[2] > 0}
        only_dis = dis_hits - isil_set
        add("6 深核对 ISIL ⊇ Disassembly", not only_dis,
            f"Disassembly 命中 {len(dis_hits)} 条字面量；"
            f"只在 Disassembly 里出现的 {len(only_dis)} 条（要求 0）")
    else:
        print("[SKIP] 6 深核对 ISIL ⊇ Disassembly（加 --deep 跑，约 25 秒）")

    if inject_fault:
        return _selftest_fault_tail(checks, expect_red=("0 ", "1 "))
    return _selftest_tail(checks)


def _selftest_tail(checks):
    bad = [c for c in checks if not c[1]]
    print("-" * 72)
    print(f"自检：{len(checks)} 项，PASS {len(checks) - len(bad)}，FAIL {len(bad)}")
    for name, _ok, reading in bad:
        print(f"   FAIL {name} → {reading}")
    return 1 if bad else 0


def _selftest_fault_tail(checks, expect_red):
    """故障注入模式：只有"该红的真的红了"才算通过（证明这套自检不是恒绿）。"""
    red = [c[0] for c in checks if not c[1]]
    missed = [p for p in expect_red if not any(n.startswith(p) for n in red)]
    print("-" * 72)
    print(f"故障注入：预期转红的项 {list(expect_red)}")
    print(f"          实际转红的项 {red}")
    if missed:
        print(f"故障注入自检 FAIL：注入了故障但这些项没红 {missed} ⇒ 自检是恒绿的，不可信")
        return 1
    print("故障注入自检 PASS：注入了故障，正控确实转红 ⇒ 尺子能红")
    return 0


def scan_disassembly(paths, addr_set, root=ISIL_DIR):
    """只用 Disassembly 段的 [...h] 操作数扫一遍（交叉核对用）。"""
    re_dis = re.compile(r"\[(?:0x)?([0-9A-Fa-f]+)h\]")
    hit = set()
    for path in paths:
        in_dis = False
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("Type: ") or line.startswith("Method: ") \
                        or line.startswith("ISIL:"):
                    in_dis = False
                    continue
                if line.startswith("Disassembly:"):
                    in_dis = True
                    continue
                if not in_dis:
                    continue
                for m in re_dis.finditer(line):
                    rva = int(m.group(1), 16) - IMAGE_BASE
                    if rva in addr_set:
                        hit.add(rva)
    return hit


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="字符串字面量 → 引用点索引")
    ap.add_argument("--build", action="store_true", help="重建 strings.tsv.gz + refs.tsv.gz")
    ap.add_argument("--who", metavar="SUBSTR", help="查某个字面量（子串）的引用点")
    ap.add_argument("--selftest", action="store_true", help="自检（红/绿都打印读数）")
    ap.add_argument("--deep", action="store_true", help="自检附带 Disassembly 交叉核对（约 25 秒）")
    ap.add_argument("--inject-fault", action="store_true",
                    help="自检故障注入：地址表 +1 且字面量查表加哨兵，正控 0/1 必须转红")
    ap.add_argument("--out", default=OUT_DIR, help="索引输出目录")
    ap.add_argument("--isil", default=ISIL_DIR, help="IsilDump 根目录")
    ap.add_argument(
        "--dump-dir",
        default=None,
        metavar="DIR",
        help="Il2CppDumper 产物目录（默认 _work/il2cppdump；env BA_MOD_MAKER_IL2CPP_DIR）",
    )
    ap.add_argument("--limit-literals", type=int, default=5, help="--who 最多列几条字面量")
    ap.add_argument("--per-literal", type=int, default=5, help="--who 每条字面量最多列几个引用点")
    ap.add_argument("--exact", action="store_true", help="--who 只匹配完全相等的字面量")
    ap.add_argument("--case", action="store_true", help="--who 区分大小写")
    args = ap.parse_args(argv)
    if args.dump_dir:
        _apply_dump_dir(args.dump_dir)

    if args.build:
        stats = build(root=args.isil, out_dir=args.out)
        pct = 100.0 * stats["literals_hit"] / max(1, stats["literals"])
        print(f"文件 {stats['files']} 个；ISIL 指令行 {stats['lines']}；"
              f"耗时 {stats['secs']:.1f} 秒")
        print(f"字面量 {stats['literals']} 条；有引用点 {stats['literals_hit']} 条（{pct:.2f}%）；"
              f"引用行 {stats['ref_lines']}；方法×字面量 {stats['method_refs']}")
        print(f"  kind=addr {stats['addr']} 行 / kind=val {stats['val']} 行；"
              f"值形没对上 {stats['unresolved_val']} 行（多为 NotImplemented 这类非字面量）")
        print(f"  {stats['strings_path']}  {stats['strings_bytes'] / 1e6:.1f} MB")
        print(f"  {stats['refs_path']}  {stats['refs_bytes'] / 1e6:.1f} MB")
        return 0

    if args.who is not None:
        _n, _m, text = who(args.who, out_dir=args.out, limit_literals=args.limit_literals,
                           per_literal=args.per_literal, exact=args.exact,
                           case_sensitive=args.case)
        print(text)
        return 0

    if args.selftest:
        return selftest(out_dir=args.out, root=args.isil,
                        inject_fault=args.inject_fault, deep=args.deep)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
