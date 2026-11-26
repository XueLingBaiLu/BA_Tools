r"""反编译那座库的**索引与检索**（门 3 的唯一实现）。

## 为什么要有它

_work 下躺着大约 1.05 GB 的反编译产物：

| 素材 | 大小 | 里面是什么 |
|---|---|---|
| _work/il2cppdump/dump.cs | 73.5 MB | **全部类型 / 字段 / 方法 / RVA**（Il2CppDumper 出的） |
| _work/isil/IsilDump/**/*.txt | 588 MB / 24,059 个 | **唯一有函数体**的地方（寄存器级 ISIL） |
| _work/il2cppdump/il2cpp.h · script.json | 391 MB | 结构体布局 · RVA→方法（不解析） |

而**门 3 原来只按文件名匹配 _work/isil**（不读内容）—— dump.cs **一个入口都没有**。
2026-10-10 读 LOD 机制那次就是现场：kb 里只有规格、没有机制，机制在 ISIL 里躺了一个月，
靠 Get-ChildItem -Recurse -Filter *LOD* **手工**找到的。缺的不是知识，是门。

## 与 kb 的分工（两个库的失效模式不同，别混）

* **kb**（tools/kb_lookup.py）= **结论 + 复算路径**；会**腐坏**（实测过两条错红线）；
* **反编译**（本工具）= **原始事实**；**不会腐坏**，但**没有结论** —— 它只负责**定位**。
⇒ 本工具的产出是「**去哪读**」（类型 / 字段 / 方法 / 哪个 txt），**不是**「结论是什么」。

## 用法

    .venv\Scripts\python.exe tools\decomp_index.py build              # 建索引（一次性）
    .venv\Scripts\python.exe tools\decomp_index.py query BaLodConfig  # 检索
    .venv\Scripts\python.exe tools\decomp_index.py --selftest         # 正控/负控（可红）

索引落在 _work/decomp-index/（派生件，可随时重建；dump.cs 更新就重跑 build）。
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
import os
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
DEFAULT_DUMP = WORK / "il2cppdump" / "dump.cs"
DEFAULT_ISIL = WORK / "isil" / "IsilDump"
INDEX_DIR = WORK / "decomp-index"

TYPES_TSV = "types.tsv"
MEMBERS_TSV = "members.tsv"
FILES_TSV = "files.tsv"
META_JSON = "meta.json"

RE_NS = re.compile(r"^// Namespace: (.*)$")
RE_TYPE = re.compile(
    r"^(?:\[[^\]]*\]\s*)*"
    r"(?:(?:public|internal|private|protected|sealed|abstract|static|partial|readonly|unsafe)\s+)*"
    r"(class|struct|enum|interface)\s+"
    r"([A-Za-z_][\w\x60<>]*)\s*"
    r"(?::\s*([^{]*?))?\s*"
    r"//\s*TypeDefIndex:\s*(\d+)\s*$"
)
RE_RVA = re.compile(r"^// RVA: (0x[0-9A-Fa-f]+)")
RE_FIELD = re.compile(
    r"^\t(?:\[[^\]]*\]\s*)*(?:public|internal|private|protected|static|readonly|const|volatile|\s)*"
    r"([\w<>\[\],.\s?]+?)\s+(\w+)\s*;\s*(?://\s*(.*))?$"
)
RE_METHOD = re.compile(
    r"^\t(?:\[[^\]]*\]\s*)*"
    r"(?:public|internal|private|protected|static|virtual|override|abstract|sealed|extern|\s)*"
    r"([\w<>\[\],.\s?]+?)\s+([\w.<>]+)\s*\(([^)]*)\)\s*(?:\{.*)?$"
)
RE_ISIL_TYPE = re.compile(r"^;;\s*Type:\s*(\S.*)$")


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_dump(dump: Path):
    ns = ""
    cur = ""
    pending_rva = ""
    with dump.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("// Namespace: "):
                ns = RE_NS.match(line).group(1).strip()
                continue
            if line.startswith("// RVA: "):
                m = RE_RVA.match(line)
                pending_rva = m.group(1) if m else ""
                continue
            if line.startswith("//"):
                continue
            if not line.startswith("\t"):
                m = RE_TYPE.match(line)
                if m:
                    kind, name, base, idx = m.groups()
                    cur = name
                    yield ("T", name, ns, kind, (base or "").strip(), idx)
                continue
            if not cur:
                continue
            m = RE_FIELD.match(line)
            if m:
                ftype, fname, off = m.groups()
                yield ("F", cur, fname, ftype.strip(), (off or "").strip())
                continue
            m = RE_METHOD.match(line)
            if m:
                rtype, mname, args = m.groups()
                yield ("M", cur, mname, rtype.strip(), pending_rva, args.strip()[:160])
                pending_rva = ""
                continue


def build(dump: Path, isil: Path, out: Path) -> int:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    n_t = n_f = n_m = 0
    last_ns = ""
    with (out / TYPES_TSV).open("w", encoding="utf-8") as ft, \
         (out / MEMBERS_TSV).open("w", encoding="utf-8") as fm:
        for row in parse_dump(dump):
            if row[0] == "T":
                _, name, ns, kind, base, idx = row
                last_ns = ns
                ft.write(f"{name}\t{ns}\t{kind}\t{base}\t{idx}\n")
                n_t += 1
            elif row[0] == "F":
                _, owner, fname, ftype, off = row
                fm.write(f"{owner}.{fname}\tfield\t{ftype}\t{off}\n")
                n_f += 1
            else:
                _, owner, mname, rtype, rva, args = row
                fm.write(f"{owner}.{mname}\tmethod\t{rtype}\t{rva}\t{args}\n")
                n_m += 1
    log(f"  dump.cs    : 类型 {n_t:,} · 字段 {n_f:,} · 方法 {n_m:,}  ({time.time()-t0:.0f}s)")
    log(f"  （最近一个命名空间 = {last_ns}）")

    n_i = 0
    t1 = time.time()
    with (out / FILES_TSV).open("w", encoding="utf-8") as fi:
        for p in sorted(isil.rglob("*.txt")):
            try:
                size = p.stat().st_size
                with p.open("r", encoding="latin-1", errors="replace") as fh:
                    head = fh.readline().strip()
            except OSError:
                continue
            m = RE_ISIL_TYPE.match(head)
            full = m.group(1).strip() if m else ""
            fi.write(f"{p.stem}\t{p.relative_to(isil).as_posix()}\t{size}\t{full}\n")
            n_i += 1
    log(f"  IsilDump   : {n_i:,} 个有函数体的类型  ({time.time()-t1:.0f}s)")

    (out / META_JSON).write_text(json.dumps({
        "dump": str(dump), "dump_bytes": dump.stat().st_size,
        "isil": str(isil), "isil_files": n_i,
        "types": n_t, "fields": n_f, "methods": n_m,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"索引 -> {out}  （{time.time()-t0:.0f}s）")
    return 0


def _read(path: Path):
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _tokens(needle: str) -> tuple:
    """(主词, 退路词)。主词 = 整串（表.列 / 命名空间前缀取末段）；退路 = 驼峰切词。

    ❗ 为什么必须分两段：BaLodConfig 一切就成 ['Ba','Lod','Config']，
    而 Ba 会命中 BattleSystemHelpers / .BaImpl / IDataBaseModel —— 满屏噪声，
    真正想找的 BrokenArrow.Lods.BaLodConfig 反而排到第 5 名（2026-10-10 实测）。
    所以**先用整串查**；整串 0 命中才退到大词（且丢掉 <4 字符的碎块）。
    """
    raw = needle.strip()
    primary = [raw]
    if "." in raw or "::" in raw:
        primary.append(raw.replace("::", ".").split(".")[-1])
    primary = [t for t in dict.fromkeys(primary) if len(t) >= 2]
    camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw).split()
    fallback = [t for t in dict.fromkeys(camel) if len(t) >= 4] or primary
    return primary, fallback


def _score(name: str, toks) -> int:
    """同名 1000 · 前缀 600 · 词边界 400 · 任意子串 100；取最高。"""
    low = name.lower()
    best = 0
    for t in toks:
        tl = t.lower()
        if not tl:
            continue
        if low == tl:
            best = max(best, 1000)
        elif low.startswith(tl):
            best = max(best, 600)
        elif re.search(r"(?<![a-z0-9_])" + re.escape(tl), low):
            best = max(best, 400)
        elif tl in low:
            best = max(best, 100)
    return best


def _scan(index: Path, toks) -> dict:
    res = {"types": [], "members": [], "files": []}
    for ln in _read(index / TYPES_TSV):
        p = ln.split("\t")
        if len(p) >= 5:
            s = _score(p[0], toks)
            if s:
                res["types"].append((s, {"name": p[0], "ns": p[1], "kind": p[2],
                                         "base": p[3], "idx": p[4]}))
    for ln in _read(index / MEMBERS_TSV):
        p = ln.split("\t")
        if len(p) >= 3:
            s = _score(p[0], toks)
            if s:
                res["members"].append((s, {"full": p[0], "kind": p[1], "detail": p[2],
                                           "extra": p[3] if len(p) > 3 else ""}))
    for ln in _read(index / FILES_TSV):
        p = ln.split("\t")
        if len(p) >= 4:
            s = max(_score(p[3], toks), _score(p[0], toks))
            if s:
                res["files"].append((s, {"name": p[0], "rel": p[1],
                                         "bytes": int(p[2] or 0), "full": p[3]}))
    return res


def query(needle: str, index: Path, limit: int = 12) -> dict:
    primary, fallback = _tokens(needle)
    toks, used_fallback = primary, False
    res = _scan(index, primary)
    if not (res["types"] or res["members"] or res["files"]) and fallback != primary:
        res = _scan(index, fallback)
        toks, used_fallback = fallback, True
    res["needle"] = needle
    res["tokens"] = toks
    res["used_fallback"] = used_fallback
    def _top(rows, key):
        return [r for _, r in sorted(rows, key=key)[:limit]]

    res["types"] = _top(res["types"], lambda sr: (-sr[0], len(sr[1]["name"])))
    res["members"] = _top(res["members"], lambda sr: (-sr[0], len(sr[1]["full"])))
    res["files"] = _top(res["files"], lambda sr: (-sr[0], -sr[1]["bytes"]))
    return res


def render(res: dict, index: Path) -> str:
    lines = []
    tail = "（整串 0 命中 ⇒ 已退到驼峰词）" if res.get("used_fallback") else ""
    lines.append(f"反编译索引（{index}）· 查「{res['needle']}」· 用词 {res['tokens']}{tail}")
    lines.append("── ① 类型（签名与字段偏移在 dump.cs；那里**没有函数体**）")
    if res["types"]:
        for t in res["types"]:
            base = f" : {t['base']}" if t["base"] else ""
            lines.append(f"   {t['ns']}.{t['name']}  [{t['kind']}{base}]  TypeDefIndex {t['idx']}")
    else:
        lines.append("   （没命中）")
    lines.append("── ② 字段 / 方法（dump.cs 里查这两样最快）")
    if res["members"]:
        for m in res["members"]:
            tag = "字段" if m["kind"] == "field" else "方法"
            off = f"  @{m['extra']}" if m["extra"] else ""
            lines.append(f"   [{tag}] {m['full']}  -> {m['detail']}{off}")
    else:
        lines.append("   （没命中）")
    lines.append("── ③ ISIL 文件（★ 只有这里**有函数体**；字节数是判据：几十 KB 的才读得动）")
    if res["files"]:
        for f in res["files"]:
            lines.append(f"   {f['rel']}   {f['bytes']:,} B   ({f['full'] or f['name']})")
    else:
        lines.append("   （没命中）")
    if not (res["types"] or res["members"] or res["files"]):
        lines.append("")
        lines.append("   0 命中。本工具**不能证明反编译里没有** —— 换一个**标识符**再试")
        lines.append("   （类名 / 字段名 / 方法名）；中文症状 → 标识符这一步是**门 0（kb）**的活。")
    else:
        lines.append("")
        lines.append("   以上是**定位**，不是结论：读出机制后要写进 kb（结论层），别只停在这一步。")
    return "\n".join(lines)


FIXTURE_DUMP = (
    "// Namespace: Fixture.Lods\n"
    "public class FixtureConfig : ScriptableObject // TypeDefIndex: 126\n"
    "{\n"
    "\t// Fields\n"
    "\tpublic float Lod0; // 0x18\n"
    "\tpublic float Lod1; // 0x1C\n"
    "\n"
    "\t// Methods\n"
    "\n"
    "\t// RVA: 0x30D90 Offset: 0x30190 VA: 0x180030D90\n"
    "\tpublic void .ctor() { }\n"
    "}\n"
)


def selftest() -> int:
    bad, ok = [], []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dump = root / "dump.cs"
        dump.write_text(FIXTURE_DUMP, encoding="utf-8")
        isil = root / "IsilDump" / "Fixture" / "Lods"
        isil.mkdir(parents=True)
        (isil / "FixtureConfig.txt").write_text(
            ";; Type: Fixture.Lods.FixtureConfig\n;; 12 instructions\n", encoding="latin-1")
        idx = root / "idx"
        build(dump, root / "IsilDump", idx)

        r = query("FixtureConfig", idx)
        if not (r["types"] and r["files"]):
            bad.append("正控 FixtureConfig 没命中"
                       f"（types={len(r['types'])} files={len(r['files'])}）")
        else:
            ok.append(f"正控 FixtureConfig：类型 {len(r['types'])}"
                      f" · 成员 {len(r['members'])} · 文件 {len(r['files'])}")
        r = query("Lod0", idx)
        if not r["members"]:
            bad.append("正控 Lod0（字段）没命中")
        else:
            ok.append(f"正控 Lod0：成员 {len(r['members'])}"
                      f"（{r['members'][0]['full']} @{r['members'][0]['extra']}）")
        r = query("ZqnaWobbleXyz999", idx)
        if r["types"] or r["members"] or r["files"]:
            bad.append("负控 ZqnaWobbleXyz999 竟然命中了")
        else:
            txt = render(r, idx)
            forbid = [w for w in ("没写过", "不存在", "知识库里没有") if w in txt]
            if forbid:
                bad.append(f"负控印了劝退话术 {forbid}（2026-10-11 明令禁止）")
            else:
                ok.append("负控 ZqnaWobbleXyz999：0 命中，且未印劝退话术")

    if (INDEX_DIR / TYPES_TSV).is_file():
        r = query("BaLodConfig", INDEX_DIR)
        if not (r["types"] and r["files"]):
            bad.append("真索引正控 BaLodConfig 没命中"
                       f"（types={len(r['types'])} files={len(r['files'])}）")
        else:
            ok.append(f"真索引正控 BaLodConfig：{r['types'][0]['ns']}.{r['types'][0]['name']} · "
                      f"{r['files'][0]['rel']} ({r['files'][0]['bytes']:,} B)")
        r = query("ForceLodZero", INDEX_DIR)
        if not r["members"]:
            bad.append("真索引正控 ForceLodZero 没命中")
        else:
            ok.append(f"真索引正控 ForceLodZero：成员 {len(r['members'])} 条")
    else:
        ok.append("（真索引还没建 ⇒ 跳过真索引正控；跑一次 build 它就会上）")

    print("反编译索引 · 自检")
    for line in ok:
        print("  ✔ " + line)
    for line in bad:
        print("  ⛔ " + line)
    print(f"结论：{'全绿' if not bad else '有红 ' + str(len(bad)) + ' 条'}")
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="反编译那座库的索引与检索（门 3 的唯一实现）")
    ap.add_argument("cmd", nargs="?", choices=["build", "query", "stats"], default="query")
    ap.add_argument("needle", nargs="*", help="标识符（类名/字段名/方法名）；中文症状先走门 0")
    ap.add_argument("--dump", type=Path, default=DEFAULT_DUMP)
    ap.add_argument("--isil", type=Path, default=DEFAULT_ISIL)
    ap.add_argument("--index", type=Path, default=INDEX_DIR)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.cmd == "build":
        if not a.dump.is_file():
            print(f"⛔ 找不到 {a.dump}（用 --dump 指定）")
            return 2
        return build(a.dump, a.isil, a.index)
    if a.cmd == "stats":
        meta = a.index / META_JSON
        if not meta.is_file():
            print(f"⛔ 索引还没建（{meta} 不存在）⇒ 先跑 build")
            return 2
        data = json.loads(meta.read_text(encoding="utf-8"))
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    needle = " ".join(a.needle).strip()
    if not needle:
        ap.print_help()
        return 2
    if not (a.index / TYPES_TSV).is_file():
        print(f"⛔ 索引还没建（{a.index}）⇒ 先跑：decomp_index.py build")
        return 2
    print(render(query(needle, a.index, limit=a.limit), a.index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
