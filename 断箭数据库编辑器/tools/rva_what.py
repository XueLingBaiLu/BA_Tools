r"""崩因取证的一条命令：**一个 RVA/VA → 方法（含泛型实例）+ 谁调用它 + 它读了哪些字段/数据库列**。

为什么有它：三套索引（type_index / field_index / string_index）各自能查，但崩了以后要的是
"这一行栈帧是谁、谁把它叫起来的、它在动哪一格数据"—— 那是**跨索引**的问题，本工具就把这一步接上。

用法（BA_Mod_Maker_2.0 下、仓内 venv）：
    .venv\Scripts\python.exe tools\rva_what.py 0x18612CDC0        # VA（>= 0x180000000，自动减基址）
    .venv\Scripts\python.exe tools\rva_what.py 0x612CDC0 --json   # RVA
    .venv\Scripts\python.exe tools\rva_what.py --selftest

⛔ 边界（写在最前面，免得当成 bug）：
  * 一个 RVA 可能对应**上千个名字**（0x12B0 是空 .ctor 的共享 thunk，实测 19,503 条）
    ⇒ 工具会给出条数并只打前几条；
  * "谁调用它"取自带符号名的调用点：**属性访问器/小方法常被 C++ 编译器内联**
    ⇒ 调用者列表为空**不代表没人调**；
  * "读了哪些字段"用的是 field_index 的归属规则（this=rcx 及其别名）⇒ **跨类型访问不在内**。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import os
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
FI = WORK / "field_index"
TI = WORK / "type_index"
IMAGE_BASE = 0x180000000
THUNK_HINT = 0x12B0


def _rows_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            yield head, line.rstrip("\n").split("\t")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _addr(s: str):
    """把表里的地址列解析成 int —— ⚠️ 各表进制不同：
    methods 是十进制、generic_insts 是 0x 十六进制。"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return None


def resolve(rva: int):
    hits = []
    for name, pat in (("methods.tsv.gz", "methods"), ("generic_insts.tsv.gz", "generic")):
        p = TI / name
        if not p.exists():
            continue
        head, _ = next(_rows_gz(p))
        # 列名容错：找 rva 列
        idx = next((i for i, h in enumerate(head) if h.lower() in ("rva", "address")), None)
        if idx is None:
            continue
        for _head, c in _rows_gz(p):
            if idx < len(c) and _addr(c[idx]) == rva:
                hits.append((pat, c))
    return hits


def callers(names: set[str]):
    p = FI / "calls.tsv.gz"
    if not p.exists():
        return []
    # ⚠️ 归一化集合必须在循环**外**建好：calls.tsv.gz 有 163 万行，逐行重建集合会跑到天荒地老
    last_norm = {norm(n.rsplit(".", 1)[-1]) for n in names}
    out = []
    for _head, c in _rows_gz(p):
        if len(c) < 4:
            continue
        tail = c[2].rsplit(".", 1)[-1]
        if c[2] in names or tail in names or norm(tail) in last_norm:
            out.append((c[0], c[1], c[2], c[3]))
    return out


def reads_of(method: str, owner_hint: str = ""):
    """这个方法（同名）读了哪些字段 —— 来自 field_index 的归属结果。"""
    p = FI / "sites.tsv.gz"
    if not p.exists():
        return []
    out = []
    for _head, c in _rows_gz(p):
        if len(c) < 6:
            continue
        if method and method in c[5]:
            out.append((c[0], c[1], c[4], c[3], c[5]))
    return out


def db_cols_of(types: set[str]):
    p = FI / "db_columns.tsv.gz"
    if not p.exists():
        return []
    out = []
    for _head, c in _rows_gz(p):
        if c[2] in types:
            out.append((c[0], c[1], c[3], c[4], c[6]))
    return out


def report(target: int, as_json: bool = False) -> int:
    hits = resolve(target)
    if not hits:
        print(f"**红** RVA 0x{target:X} 在方法入口表里 0 命中")
        print("  提示：崩溃栈里先确认是 RVA 还是 VA（VA >= 0x180000000 要减基址）；"
              "0x6100000–0x61A47E0 那一段是 IL2CPP 内部桩，本来就不在表里。")
        return 1
    # ⚠️ 两张表列语义不同：methods.tsv.gz = rva va type method signature；
    # generic_insts.tsv.gz = rva va type name
    types_ = {c[2] for _k, c in hits if len(c) > 2}
    methods = {c[3] for _k, c in hits if len(c) > 3}
    short = {m.rsplit(".", 1)[-1] for m in methods}
    cl = callers(short | methods)
    _ = reads_of("")  # F841：原来叫 rd，确实没人用；本单只清 lint，调用原样保留
    payload = {
        "rva": f"0x{target:X}", "hits": len(hits),
        "types": sorted(types_)[:20], "methods": sorted(methods)[:20],
        "callers": [f"{a}.{b} -> {c} ({d})" for a, b, c, d in cl[:40]], "n_callers": len(cl),
        "thunk_warning": target == THUNK_HINT or len(hits) > 20,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print(f"=== RVA 0x{target:X}（VA 0x{target + IMAGE_BASE:X}）命中 {len(hits)} 条")
    if len(hits) > 20 or target == THUNK_HINT:
        print(f"  ⚠️ 命中很多（{len(hits)} 条）—— 0x12B0 是空 .ctor 的共享 thunk 之类，"
              f"**别只看第一条**")
    for kind, c in hits[:6]:
        print(f"  [{'声明' if kind == 'methods' else '泛型实例'}] {c[2] if len(c) > 2 else '?'} :: "
              f"{c[3] if len(c) > 3 else '?'}")
    if len(hits) > 6:
        print(f"  …还有 {len(hits)-6} 条")
    print(f"=== 谁调用它（带符号名的调用点）{len(cl)} 处"
          + ("" if cl else " —— ⚠️ 空不代表没人调（内联）"))
    for a, b, _c, d in cl[:12]:
        print(f"    {a}.{b}  ({d})")
    if types_:
        cols = db_cols_of(types_)
        if cols:
            print(f"=== 它属于数据库模型 ⇒ 相关列 {len(cols)} 个（前 8）")
            for t, col, off, nr, top in cols[:8]:
                print(f"    {t}.{col} @{off} 读 {nr} · {top[:60]}")
    return 0


def selftest() -> int:
    ok = True

    def check(good, text):
        nonlocal ok
        ok = ok and good
        print(("  OK   " if good else "  **红** ") + text)

    print("=== 正控 1：Turrets.ModelFileName 的访问器能查到 RVU/方法名")
    tgt = None
    for _head, c in _rows_gz(TI / "methods.tsv.gz"):
        if len(c) > 3 and c[3].endswith("get_ModelFileName") and c[2].endswith("Turrets"):
            a = _addr(c[0])
            if a and a > 0:
                tgt = a
                break
    check(tgt is not None and tgt > 0,
          f"type_index 里找到 Turrets::get_ModelFileName @ RVA {hex(tgt) if tgt else '?'}")
    if tgt:
        hits = resolve(tgt)
        check(bool(hits), f"用该 RVA 反查命中 {len(hits)} 条（应 >= 1）")
    print("=== 负控 2：乱编的 RVA → 0 命中且 rc=1")
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(0x7FFFFF0)
    check(rc == 1 and "0 命中" in buf.getvalue(), "report(0x7FFFFF0) → rc=1 且打印 0 命中")
    print("=== 负控 3：0x12B0 必须触发『命中很多』警告（尺子能红）")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(THUNK_HINT)
    check("命中很多" in buf.getvalue() or "别只看第一条" in buf.getvalue(),
          f"0x12B0 命中 {len(resolve(THUNK_HINT))} 条并给出警告")
    print()
    print("rva_what 自检：" + ("全绿 ✔" if ok else "**有红** ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="RVA/VA → 方法 + 调用者 + 读的字段/列")
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if not a.target:
        raise SystemExit("给一个 RVA 或 VA（0x…），或 --selftest")
    t = int(a.target, 16) if a.target.lower().startswith("0x") else int(a.target)
    if t >= IMAGE_BASE:
        t -= IMAGE_BASE
    raise SystemExit(report(t, a.json))
