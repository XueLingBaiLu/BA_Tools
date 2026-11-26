r"""取值域：**某一列在代码里被真正判断过哪些取值**（从 ISIL 的 cmp 指令抽，纯本地）。

为什么有它：产品那张《数据-表与列清单》的「说明」列现在多半是空的，而"这一列是什么语义"
最硬的来源就是**代码怎么判它** —— 例：Units.Type @0x7C 在 IsInfantry/GetInfantrySlots/GetWeight 里
被 cmp 成 2、在 IsFly 里被 cmp 成 8 与 16 ⇒ 语义（步兵/直升机/固定翼）不用猜。

判据（**动手前写死**，跑 --selftest 验）：
  1) 抽取只认两种形态，且寄存器必须是 this（rcx）或它的别名：
     * 直接式：cmp [<size> ptr] [this+OFFh], IMM
     * 载入式：mov <reg>, [this+OFFh] ... cmp <reg>, IMM（reg 被覆写即失效）
  2) 正控：Units.Type @0x7C 必须抽出 {2, 8, 16}
  3) 负控：偏移 0xFE → 0 值；乱编字段名 → 0 值
  4) 覆盖读数：有读点的 (表.列) 里有多少抽到 ≥1 个取值
⛔ 不做（**算"没抽到"，不算"没处理"**）：sub/and/or 的区间变形、switch 跳转表、
   跨类型读者、位掩码 test。
   因此"库里出现而这里没抽到"**只能当提示**：实测反例 Units.Type 的 {4,32}（载具/舰船显然有行为）。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import os
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
FI = WORK / "field_index"
ISIL = WORK / "isil" / "IsilDump"
OUT = WORK / "value_domains"

_TYPE_LINE_RE = re.compile(r"^Type:\s*(.+)$")
_METHOD_RE = re.compile(r"^Method:\s*(.+)$")
_SECT_RE = re.compile(r"^(Disassembly|ISIL):\s*$")
_INSTR_RE = re.compile(r"^\s*(?:[0-9A-Fa-f]+:\s+)?([a-z][a-z0-9]*)\s+(.*)$")
_IMM_RE = re.compile(r"^(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+h|\d+)$")
#: ⚠️ 寄存器类必须含 8/16/32 位名（eax/edx/ax/dl…）：只写 r[a-z0-9]{1,3} 会漏掉载入式
#:（实测：IsFly 是 mov edx,[rcx+7Ch] + cmp edx,10h，第一版因此只抽到直接式的 {2}）
_REG = r"(?:[re]?(?:ax|bx|cx|dx|si|di|bp|sp)|[abcd][lh]|r\d{1,2}[dwb]?)"
_CMP_MEM_RE = re.compile(
    r"^(?:(?:byte|word|dword|qword)\s+ptr\s+)?\[(" + _REG + r")\+([0-9A-Fa-f]+)h\],\s*(\S+)$")
_LOAD_MEM_RE = re.compile(
    r"^(" + _REG + r"),\s*(?:(?:dword|qword|word|byte)\s+ptr\s+)?\[(" + _REG
    + r")\+([0-9A-Fa-f]+)h\]$")
_MOV_REG_RE = re.compile(r"^(" + _REG + r"),\s*(.*)$")


def nkey(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def imm(text: str):
    t = text.strip().rstrip(",")
    if not _IMM_RE.match(t):
        return None
    if t.startswith("0x"):
        return int(t, 16)
    if t.endswith("h"):
        return int(t[:-1], 16)
    return int(t)


def load_fields():
    """权威字段表：nkey(type) -> {offset: [field, ...]}（只收实例字段）"""
    by_off: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    n = 0
    with gzip.open(FI / "fields.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if len(c) < 6 or c[5] == "1":
                continue
            by_off[nkey(c[0])][int(c[3])].append(c[1])
            n += 1
    print(f"[1] 权威字段表：{n:,} 条实例字段 · {len(by_off):,} 个类型")
    return by_off


def scan(by_off):
    """一遍扫 ISIL：抽 (类型, 字段, 偏移, 取值, 方法, 文件:行, 形态)。"""
    rows = []
    st = {"files": 0, "methods": 0, "direct": 0, "load": 0, "cmp_seen": 0}
    t0 = time.time()
    for path in ISIL.rglob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        st["files"] += 1
        full = ""
        for line in lines[:4]:
            m = _TYPE_LINE_RE.match(line)
            if m:
                full = m.group(1).strip()
                break
        if not full:
            continue
        offmap = by_off.get(nkey(full))
        if not offmap:
            continue
        rel = str(path.relative_to(ISIL))
        method = ""
        section = ""
        this_regs = {"rcx"}
        loaded: dict[str, int] = {}          # reg -> 偏移（载入式）
        for lineno, line in enumerate(lines, 1):
            hit = _METHOD_RE.match(line)
            if hit:
                method = hit.group(1).strip()
                st["methods"] += 1
                section, this_regs, loaded = "", {"rcx"}, {}
                continue
            if _SECT_RE.match(line):
                section = line.strip().rstrip(":")
                this_regs, loaded = {"rcx"}, {}
                continue
            if section != "Disassembly":
                continue
            ins = _INSTR_RE.match(line)
            if not ins:
                continue
            mnem, ops = ins.group(1), ins.group(2)
            low = mnem.lower()
            if low == "cmp":
                st["cmp_seen"] += 1
                if "," not in ops:
                    continue
                left, right = ops.split(",", 1)
                val = imm(right)
                m = _CMP_MEM_RE.match(ops.strip())
                if m and val is not None:
                    reg, off = m.group(1), int(m.group(2), 16)
                    if reg in this_regs and off in offmap:
                        st["direct"] += 1
                        for f in offmap[off]:
                            rows.append((full, f, off, val, method, rel, lineno, "direct"))
                    continue
                lm = _MOV_REG_RE.match(ops.strip())
                if lm and val is not None:
                    reg = lm.group(1)
                    if reg in loaded and loaded[reg] in offmap:
                        st["load"] += 1
                        for f in offmap[loaded[reg]]:
                            rows.append((full, f, loaded[reg], val, method, rel, lineno, "load"))
                continue
            if low in ("mov", "movsxd", "movzx", "movsx", "lea") and "," in ops:
                dest, src = (x.strip() for x in ops.split(",", 1))
                dm = re.fullmatch(_REG, dest)                # ⚠️ _REG 是非捕获组 ⇒ 只能用 group(0)
                if not dm:
                    continue
                d = dm.group(0)
                sm = re.fullmatch(_REG, src)
                lm = _LOAD_MEM_RE.match(ops.strip())
                if sm and sm.group(0) in this_regs:          # this 的别名
                    this_regs.add(d)
                    loaded.pop(d, None)
                elif lm and lm.group(2) in this_regs:        # 从 this 载入某字段 → 记住
                    loaded[d] = int(lm.group(3), 16)
                else:
                    loaded.pop(d, None)
                    if d != "rcx":
                        this_regs.discard(d)
                continue
    print(f"[2] ISIL：{st['files']:,} 文件 · 方法 {st['methods']:,} · cmp 指令 {st['cmp_seen']:,} ·"
          f" 抽到（直接 {st['direct']:,} / 载入 {st['load']:,}）· {time.time()-t0:.0f}s")
    return rows, st


def db_values():
    """表.列 -> 库里出现过的取值集合（只收小整数域；浮点/字符串跳过）。"""
    z = ROOT.parent / "DataBaseCompiled" / "BA_Mod.zip"
    out = {}
    with zipfile.ZipFile(z) as zf:
        for name in sorted(n for n in zf.namelist() if n.endswith(".json")):
            table = Path(name).stem
            body = json.loads(zf.read(name).decode("utf-8"))
            body = body.get("Rows", body) if isinstance(body, dict) else body
            if not isinstance(body, list):
                continue
            cols: dict[str, set] = defaultdict(set)
            for r in body:
                if isinstance(r, dict):
                    for k, v in r.items():
                        cols[k].add(v)
            for col, vals in cols.items():
                if not vals or len(vals) > 64:
                    continue
                if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
                    out[(table, col)] = vals
    print(f"[3] 数据库：{len(out):,} 个整数型 (表.列)")
    return out


def build() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    by_off = load_fields()
    rows, st = scan(by_off)
    with gzip.open(OUT / "value_domains.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("type\tfield\toffset\thex\tvalue\tvalue_hex\tform\tmethod\tfile\tline\n")
        for r in rows:
            fh.write(f"{r[0]}\t{r[1]}\t{r[2]}\t0x{r[2]:X}\t{r[3]}\t0x{r[3]:X}\t"
                     f"{r[7]}\t{r[4]}\t{r[5]}\t{r[6]}\n")
    # 归并：类型.字段 -> 取值集合
    per: dict[tuple[str, str], set] = defaultdict(set)
    meth: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for t, f, _o, v, m, _f2, _l, _form in rows:
        per[(t, f)].add(v)
        meth[(t, f)][m.split("(", 1)[0].rsplit(" ", 1)[-1]] += 1
    # 与库里的取值对拍
    dbv = db_values()
    with gzip.open(FI / "db_columns.tsv.gz", "rt", encoding="utf-8") as fh:
        cols = [line.split("\t") for line in fh.read().splitlines()[1:]]
    unmatched, matched = [], 0
    for c in cols:
        table, col, mtype = c[0], c[1], c[2]
        # ⚠️ db_columns 的 model_type 列可能是**归一化键**（历史原因），两边都归一化再比
        key = (mtype, col)
        if key not in per:
            key = next((k for k in per if k[0] == mtype or nkey(k[0]) == nkey(mtype)), None)
            if key is None or key[1] != col:
                continue
            continue
        dv = dbv.get((table, col))
        if dv is None:
            continue
        cv = per[key]
        only_db = sorted(v for v in dv if v not in cv)
        matched += 1
        if only_db:
            unmatched.append((table, col, c[3], sorted(dv), sorted(cv), only_db,
                              " ".join(f"{m}x{n}" for m, n in meth[key].most_common(3))))
    with gzip.open(OUT / "db_unmatched_values.tsv.gz", "wt", encoding="utf-8") as fh:
        fh.write("table\tcolumn\toffset_hex\tdb_values\tcode_values\tdb_only\ttop_methods\n")
        for r in unmatched:
            fh.write(f"{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}\t{r[5]}\t{r[6]}\n")
    stats = {**st, "fields_with_values": len(per), "value_rows": len(rows),
             "db_compared": matched, "db_unmatched_rows": len(unmatched)}
    (OUT / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[落盘] {OUT}")
    print(f"  抽到取值的 (类型.字段) {len(per):,} 个 · 取值行 {len(rows):,}")
    print(f"  与库对拍 {matched:,} 个 (表.列) · 其中『库里出现但本类型没抽到』{len(unmatched):,} 个"
          f"（⛔ 提示，不是死值：见 README 的两条假阳性机制）")
    return 0


def query_who(spec: str) -> int:
    if "." not in spec:
        raise SystemExit("--who 要写成 类型.字段（例：Units.Type）")
    tname, fname = spec.rsplit(".", 1)
    rows = []
    with gzip.open(OUT / "value_domains.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if c[1] == fname and c[0].lower().endswith(tname.lower()):
                rows.append(c)
    if not rows:
        print(f"**红** {spec}：没抽到任何取值（可能没有同类型读点，或判断形态不在覆盖范围内）")
        return 1
    print(f"=== {rows[0][0]}.{rows[0][1]} @{rows[0][3]} 代码里判过的取值：")
    per: dict[int, list[str]] = defaultdict(list)
    for c in rows:
        per[int(c[4])].append(f"{c[7].split('(', 1)[0].rsplit(' ', 1)[-1]} ({c[9]}:{c[6]})")
    for v in sorted(per):
        print(f"  {v} (0x{v:X}) ← " + " · ".join(per[v][:4]))
    print(f"  合计 {len(per)} 个取值 · {len(rows)} 处证据")
    return 0


def query_table(table: str) -> int:
    """打一张表的模型里**所有抽到取值的字段**（可直接贴进《列清单》的说明列）。"""
    with gzip.open(FI / "db_columns.tsv.gz", "rt", encoding="utf-8") as fh:
        cols = [line.split("\t") for line in fh.read().splitlines()[1:]]
    mtype = next((c[2] for c in cols if c[0] == table), None)
    if mtype is None:
        print(f"**红** 表 {table} 不在 db_columns.tsv.gz 里")
        return 1
    per: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    off: dict[str, str] = {}
    with gzip.open(OUT / "value_domains.tsv.gz", "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if nkey(c[0]) != nkey(mtype):
                continue
            per[c[1]][int(c[4])].append(f"{c[7].split('(', 1)[0].rsplit(' ', 1)[-1]}:{c[9]}")
            off[c[1]] = c[3]
    if not per:
        print(f"**红** {table}（{mtype}）没抽到任何取值")
        return 1
    print(f"=== {table}（{mtype}）代码判过的取值 · {len(per)} 个字段")
    for f in sorted(per):
        pairs = " · ".join(
            f"{v}←{','.join(sorted(set(ms))[:3])}" for v, ms in sorted(per[f].items()))
        print(f"  {f:32s} {off.get(f, ''):>5s}  {pairs}")
    return 0


def selftest() -> int:
    ok = True

    def check(good, text):
        nonlocal ok
        ok = ok and good
        print(("  OK   " if good else "  **红** ") + text)

    print("=== 正控 1：Units.Type @0x7C 必须抽出 {2, 8, 16}")
    with gzip.open(OUT / "value_domains.tsv.gz", "rt", encoding="utf-8") as fh:
        rows = [line.split("\t") for line in fh.read().splitlines()[1:]]
    t = [c for c in rows if c[1] == "Type" and c[0].endswith("Units")]
    vals = {int(c[4]) for c in t}
    check(vals == {2, 8, 16}, f"抽出 {sorted(vals)}（要 {{2, 8, 16}}）· 证据 {len(t)} 处")
    for c in t[:6]:
        print(f"        {c[4]:>4} ← {c[7].split('(', 1)[0].rsplit(' ', 1)[-1]}"
              f"  {c[6]}  {c[8]}:{c[9]}")
    print("=== 负控 2：一个字段表里不存在的偏移 → 0 值")
    # ⚠️ 第一版拿 0xFE 当"不存在"，可它本身就是合法偏移（尺子设计错）⇒ 改成 0xFFF0
    check(not [c for c in rows if int(c[2]) == 0xFFF0], "0xFFF0 0 命中")
    print("=== 负控 3：乱编字段名 → 0 值")
    check(not [c for c in rows if c[1] == "NoSuchFieldZzz"], "NoSuchFieldZzz 0 命中")
    print("=== 覆盖读数")
    per = {(c[0], c[1]) for c in rows}
    print(f"        抽到取值的 (类型.字段) {len(per):,} 个 · 取值行 {len(rows):,}")
    st = json.loads((OUT / "stats.json").read_text(encoding="utf-8"))
    print(f"        与库对拍 {st['db_compared']:,} 个 (表.列)，"
          f"其中 db_only {st['db_unmatched_rows']:,} 个（提示级）")
    print()
    print("value_domains 自检：" + ("全绿 ✔" if ok else "**有红** ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="取值域：代码里判过哪些取值")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--who", default="")
    ap.add_argument("--table", default="", help="打一张表所有抽到取值的字段（可贴进说明列）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.build:
        raise SystemExit(build())
    if a.who:
        raise SystemExit(query_who(a.who))
    if a.table:
        raise SystemExit(query_table(a.table))
    if a.selftest:
        raise SystemExit(selftest())
    raise SystemExit("给一个动作：--build / --who T.F / --selftest")
