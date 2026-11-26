"""方法体视图：符号名 → 当前版本的可读方法体（地址 + 源码行 + 指令 + 字段名）。

为什么需要它：IL2CPP 的发行包里没有 IL，任何 `.cs` 反编译产物都是空壳；
方法体只能从 `GameAssembly.dll` 的机器码里取。本工具把四份现成素材拼起来：

    script.json  (dump 产物)  → 方法名 ↔ RVA
    .pdata       (dll 自带)   → 函数区间 begin..end（447,715 条，12 B/条）
    capstone                  → x64 反汇编
    il2cpp.usym  (游戏自带)   → 该地址属于原 C# 的 哪个文件:第几行
    dump.cs      (dump 产物)  → [rcx+0xNN] 是哪个字段

用法：

    python tools/method_body.py BrokenArrow.DataBase.Models.Units.GetWeaponById
    python tools/method_body.py Units.GetWeaponById --game <游戏根> --dump <dump.cs 所在目录>
    python tools/method_body.py --addr 0x343750

判据（能红）：

    ① 输出里必须出现 `.pdata` 给出的 begin..end；
    ② 每条指令要么带 `文件:行`，要么明确标 `<no-source>`；
    ③ 已知正控：`Units.get_Id` 必须反汇编成 `mov eax, [rcx + 0x10]` + `ret`，
       且 0x10 必须被标成 `Id`（`--selftest` 跑这条）。

⛔ 边界：字段名是按「声明类型的偏移表」推出来的，没有做数据流验证 ——
   基址寄存器不一定是 `this`（也可能是别的指针恰好同偏移），所以输出里一律写 `~字段名`。
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import struct
import subprocess
import sys
from array import array
from pathlib import Path

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
DEFAULT_DUMP = WORK / "il2cpp1029" / "dump"
CACHE = WORK / "mb" / "cache"

# Ghidra 车道（2026-11-09 建）。⚠ 安装路径必须是 ASCII：
# 中文路径下 log4j 的 include 解析失败（SAXParseException / No logging configuration），
# 所以这里指向软链 D:\ghidra12 → 真实安装目录。工程、脚本、产物也一律 ASCII。
GHIDRA_HOME = Path(r"D:\ghidra12")
GHIDRA_PROJ = Path(r"D:\ba_ghidra\proj")
GHIDRA_SCRIPTS = Path(r"D:\ba_ghidra\scripts")
GHIDRA_OUT = Path(r"D:\ba_ghidra\out")
GHIDRA_PROJECT = "BA1029"
# `cmd /c analyzeHeadless.bat` 起不来时会弹「JDK 21+ could not be found」——它不认 PATH，认 JAVA_HOME。
# 本机 JDK 位置固定；要换机器就设环境变量 BA_GHIDRA_JAVA_HOME。
GHIDRA_JAVA_HOME = Path(os.environ.get("BA_GHIDRA_JAVA_HOME",
                                         r"C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"))

NO_PARENT = 0xFFFFFFFF


# ---------------------------------------------------------------- PE / .pdata

def parse_pe(buf: bytes) -> dict:
    """读 PE 头：镜像基址 + 节表（name, va, vsize, rawsize, rawptr）。"""
    if buf[:2] != b"MZ":
        raise SystemExit("不是 PE 文件（缺 MZ）")
    e_lfanew = struct.unpack_from("<I", buf, 0x3C)[0]
    if buf[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise SystemExit("不是 PE 文件（缺 PE 签名）")
    nsec = struct.unpack_from("<H", buf, e_lfanew + 6)[0]
    sizeopt = struct.unpack_from("<H", buf, e_lfanew + 20)[0]
    opt = e_lfanew + 24
    magic = struct.unpack_from("<H", buf, opt)[0]
    imagebase = struct.unpack_from("<Q", buf, opt + 24)[0] if magic == 0x20B else 0
    sections = []
    p = opt + sizeopt
    for _ in range(nsec):
        name = buf[p:p + 8].rstrip(b"\x00").decode("latin1")
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", buf, p + 8)
        sections.append((name, va, vsize, rawsize, rawptr))
        p += 40
    return {"imagebase": imagebase, "sections": sections}


def rva_to_off(pe: dict, rva: int) -> int | None:
    for _name, va, vsize, rawsize, rawptr in pe["sections"]:
        if va <= rva < va + max(vsize, rawsize):
            return rva - va + rawptr
    return None


def load_pdata(buf: bytes, pe: dict) -> tuple[list[int], list[int]]:
    """`.pdata` → (begins, ends)，按 begin 升序；每条 12 B。"""
    for name, _va, vsize, rawsize, rawptr in pe["sections"]:
        if name == ".pdata":
            break
    else:
        raise SystemExit("找不到 .pdata 节")
    n = vsize // 12
    begins = [0] * n
    ends = [0] * n
    for i in range(n):
        b, e, _u = struct.unpack_from("<III", buf, rawptr + i * 12)
        begins[i] = b
        ends[i] = e
    order = sorted(range(n), key=begins.__getitem__)
    return [begins[i] for i in order], [ends[i] for i in order]


def pdata_extent(begins: list[int], ends: list[int], rva: int) -> tuple[int, int] | None:
    i = bisect.bisect_right(begins, rva) - 1
    if i >= 0 and begins[i] <= rva < ends[i]:
        return begins[i], ends[i]
    return None


# ---------------------------------------------------------------- script.json

METHOD_RE = re.compile(rb'"Address": (\d+),\s*"Name": "([^"]*)"')


def load_methods(dump_dir: Path) -> list[tuple[int, str]]:
    """方法表 (rva, name)；先读缓存，没有就把 script.json 流式扫一遍再写缓存。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    script = dump_dir / "script.json"
    cache = CACHE / f"methods_{script.stat().st_size}.tsv"
    rows: list[tuple[int, str]] = []
    if cache.exists():
        with cache.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                addr_s, _tab, name = line.partition("\t")
                if name:
                    rows.append((int(addr_s, 16), name.rstrip("\n")))
        rows.sort()
        return rows
    if not script.exists():
        raise SystemExit(f"缺 script.json：{script}")
    seen: dict[int, str] = {}
    with script.open("rb") as fh:
        carry = b""
        while True:
            chunk = fh.read(1 << 24)
            if not chunk:
                break
            data = carry + chunk
            for m in METHOD_RE.finditer(data):
                seen[int(m.group(1))] = m.group(2).decode("utf-8", "replace")
            carry = data[-512:]
    rows = sorted(seen.items())
    with cache.open("w", encoding="utf-8") as fh:
        for addr, name in rows:
            fh.write(f"{addr:x}\t{name}\n")
    return rows


def _variants(needle: str) -> list[str]:
    """方法名口径：script.json 里 `命名空间.类型$$方法`，人习惯写 `类型.方法`。"""
    out = [needle]
    if "." in needle:
        head, _dot, tail = needle.rpartition(".")
        out.append(head + "$$" + tail)
    return out


def _short_key(symbol: str) -> tuple[str, str]:
    """(类型短名, 方法短名) —— 把命名空间和显式接口前缀都剥掉，用于宽松匹配。"""
    owner = symbol.split("$$")[0].rsplit(".", 1)[-1]
    body = symbol.split("$$", 1)[1] if "$$" in symbol else symbol
    return owner, body.rsplit(".", 1)[-1]


def resolve(rows: list[tuple[int, str]], needle: str) -> tuple[int, str]:
    """按全名 / 后缀 / 子串找方法；命中多个时优先精确，其次最短名字。"""
    variants = _variants(needle)
    for v in variants:
        exact = [r for r in rows if r[1] == v]
        if exact:
            return exact[0]
    tail = [r for r in rows
            if any(r[1].endswith("." + v) or r[1].endswith("$$" + v) for v in variants)]
    # 还认「类型短名 + 方法名」：`Units$$get_HasSpecialization`
    # 要能对上 `BrokenArrow.DataBase.Models.Units$$BrokenArrow.DataBase.Models.IReadOnlyUnits.get_HasSpecialization`
    # （C# 的显式接口实现会把接口全名塞进方法名，光靠后缀匹配对不上）。
    pool = tail
    if not pool:
        want = _short_key(needle)
        pool = [r for r in rows if _short_key(r[1]) == want]
    if not pool:
        pool = [r for r in rows if needle in r[1]]
    if not pool:
        raise SystemExit(f"方法表里找不到：{needle}")
    pool.sort(key=lambda r: (len(r[1]), r[1]))
    return pool[0]


# ---------------------------------------------------------------- usym

def load_usym(usym: Path) -> dict:
    """usym v2：24 B header + N*24 B 记录 + 字符串区。地址数组缓存成 .bin。"""
    buf = usym.read_bytes()
    if buf[:4] != b"sym-":
        raise SystemExit("usym magic 不是 sym-")
    version, count, _ident, _name, _os = struct.unpack_from("<5I", buf, 4)
    if version != 2:
        raise SystemExit(f"usym 版本不是 2：{version}")
    base = 24 + count * 24
    CACHE.mkdir(parents=True, exist_ok=True)
    addrs_bin = CACHE / f"usym_addrs_{count}.bin"
    if addrs_bin.exists():
        addrs = array("Q")
        addrs.frombytes(addrs_bin.read_bytes())
    else:
        addrs = array("Q", bytes(count * 8))
        unpack = struct.Struct("<Q").unpack_from
        for i in range(count):
            addrs[i] = unpack(buf, 24 + i * 24)[0]
        tmp = addrs_bin.with_suffix(".tmp")
        tmp.write_bytes(addrs.tobytes())
        tmp.replace(addrs_bin)
    return {"buf": buf, "count": count, "base": base, "addrs": addrs}


def usym_line(us: dict, addr: int) -> tuple[str, int] | None:
    """地址 → (源文件, 行号)。同址多条时优先非内联那条。"""
    addrs = us["addrs"]
    i = bisect.bisect_left(addrs, addr)
    best = None
    while i < us["count"] and addrs[i] == addr:
        _a, _f1, foff, line, parent = struct.unpack_from("<QIIII", us["buf"], 24 + i * 24)
        if line != 0xFFFFFFFF:
            cand = (foff, line, parent)
            if best is None or (best[2] != NO_PARENT and parent == NO_PARENT):
                best = cand
        i += 1
    if best is None:
        return None
    foff, line, _parent = best
    end = us["buf"].find(b"\x00", us["base"] + foff)
    path = us["buf"][us["base"] + foff:end].decode("latin1")
    if path == "<no-source>" or not path:
        return None
    return path, line


# ---------------------------------------------------------------- dump.cs

FIELD_RE = re.compile(r"^\s*(?:private|public|internal|protected).*?([A-Za-z_<>][A-Za-z0-9_<>, ]*?);\s*//\s*0x([0-9a-fA-F]+)\s*$")


def load_fields(dump_dir: Path, type_name: str) -> dict[int, str]:
    """在 dump.cs 里找 `class <Short>` 的字段块 → {偏移: 字段名}。

    dump.cs 的版式是固定的：`class X` → `// Fields` → 字段行（带 `// 0xNN`）→ `// Methods`。
    用这两个注释当边界，比数花括号稳（`Units` 里有嵌套的 `<>c__DisplayClass` 会打乱深度）。
    """
    dump = dump_dir / "dump.cs"
    if not dump.exists():
        return {}
    short = type_name.rsplit(".", 1)[-1]
    # 注意：Il2CppDumper 把嵌套类也顶格写成 `class Units.<>c__DisplayClass191_0`，
    # 所以短名后面必须是空白/冒号/行尾，否则会把嵌套类当成外层类。
    head = re.compile(
        rf"^(?:public|internal|private|protected).*\b(?:class|struct)\s+{re.escape(short)}(?=\s|:|$)"
    )
    out: dict[int, str] = {}
    state = 0  # 0=找类 1=找 // Fields 2=收字段
    with dump.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if state == 0:
                if head.match(line):
                    state = 1
                continue
            if state == 1:
                if stripped == "// Fields":
                    state = 2
                elif stripped == "// Methods":
                    break
                continue
            if stripped == "// Methods":
                break
            m = FIELD_RE.match(line)
            if m:
                name = m.group(1).strip().split()[-1]
                name = name.replace("<", "").replace(">", "").replace("k__BackingField", "")
                out.setdefault(int(m.group(2), 16), name or "?")
    return out


# ---------------------------------------------------------------- 反汇编

MEM_RE = re.compile(r"\[(\w+)(?:\s*([+-])\s*(0x[0-9a-fA-F]+))?\]")


def annotate(text: str, fields: dict[int, str], trusted: set[str]) -> str:
    """`[rcx + 0x30]` → 追加 `+0x30=CountryId`。

    只在基址寄存器属于 `trusted`（当前认定为 `this` 或 `this` 的别名）时才标注：
    `[rax+0x18]` 这种 `rax` 是从别的对象里 load 出来的，按 `this` 的偏移表解释就是假阳性。
    """
    tags = []
    for m in MEM_RE.finditer(text):
        if not m.group(3):
            continue
        if m.group(2) == "-":
            continue
        if m.group(1).lower() not in trusted:
            continue
        off = int(m.group(3), 16)
        name = fields.get(off)
        if name:
            tags.append(f"+0x{off:x}={name}")
    tag_txt = ", ".join(tags)
    return ("  ; " + tag_txt) if tags else ""


def track_this(insn, trusted: set[str]) -> set[str]:
    """轻量寄存器追踪：`mov dst, src` 且 src 是 this 别名 ⇒ dst 也是。其余写入即失效。"""
    try:
        _reads, writes = insn.regs_access()
    except Exception:  # noqa: BLE001 - capstone 细节不可用时退化成不追踪
        return trusted
    is_mov = insn.mnemonic == "mov" and len(insn.operands) == 2
    src_alias = False
    if is_mov:
        a, b = insn.operands
        if a.type == 1 and b.type == 1:  # X86_OP_REG == 1
            src_alias = insn.reg_name(b.reg) in trusted
    out = set(trusted)
    for w in writes:
        name = insn.reg_name(w)
        if name is None:
            continue
        out.discard(name)
        if src_alias:
            out.add(name)
    return out


def disassemble(game: Path, dump_dir: Path, needle: str | None, addr: int | None, limit: int) -> list[str]:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64

    dll = game / "GameAssembly.dll"
    if not dll.exists():
        raise SystemExit(f"缺 GameAssembly.dll：{dll}")
    buf = dll.read_bytes()
    pe = parse_pe(buf)
    begins, ends = load_pdata(buf, pe)

    name = ""
    rva = addr
    if rva is None:
        rows = load_methods(dump_dir)
        rva, name = resolve(rows, needle or "")
    ext = pdata_extent(begins, ends, rva)
    if ext is None:
        raise SystemExit(f"0x{rva:x} 不在 .pdata 的任何函数区间里（不是托管方法入口？）")
    start, stop = ext
    off = rva_to_off(pe, start)
    if off is None:
        raise SystemExit("RVA → 文件偏移失败")

    us = load_usym(game / "BrokenArrow_Data" / "il2cpp_data" / "il2cpp.usym")
    owner = name.split("$$", 1)[0] if name else ""
    fields = load_fields(dump_dir, owner) if owner else {}

    lines: list[str] = []
    src = usym_line(us, start)
    lines.append(f"target   : {name or hex(rva)}")
    lines.append(f"rva      : 0x{start:X} .. 0x{stop:X}   ({stop - start} 字节, 来源 .pdata)")
    lines.append(f"source   : {src[0] if src else '<no-source>'}" + (f":{src[1]}" if src else ""))
    lines.append(f"fields   : {len(fields)} 个（按声明类型的偏移表推）")
    lines.append("-" * 96)

    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    trusted: set[str] = {"rcx"}  # 保守假设：入口 rcx 是 this（静态方法会有假阳性，见文末注）
    cur_src = src
    n = 0
    for insn in md.disasm(buf[off:off + (stop - start)], start):
        if n >= limit:
            lines.append(f"... （已截断，--limit {limit}）")
            break
        hit = usym_line(us, insn.address)
        if hit:
            cur_src = hit
        text = f"{insn.mnemonic:<8}{insn.op_str}"
        where = f"{Path(cur_src[0]).name}:{cur_src[1]}" if cur_src else "<no-source>"
        lines.append(f"0x{insn.address:08X}  {text:<52} ; {where}{annotate(text, fields, trusted)}")
        trusted = track_this(insn, trusted)
        n += 1
    lines.append("-" * 96)
    lines.append(f"指令 {n} 条 · 注：`~字段` / `+0xNN=名字` 是按偏移表推的，未做数据流验证")
    return lines


# ---------------------------------------------------------------- Ghidra 伪代码

def ghidra_plan(dump_dir: Path, needle: str | None, addr: int | None,
                out: Path | None = None) -> tuple[int, str, Path, str]:
    r"""算出「要反编译哪个函数、产物写哪、该跑哪条命令」，**不执行**。

    为什么不在这里 subprocess 起 Ghidra —— 实测两次都红：
    从 Python 子进程起 `analyzeHeadless.bat` 时，Ghidra 要写
    `%APPDATA%\ghidra\ghidra_12.1.3_PUBLIC\java_home.save`，**被拒绝访问**：

        java.io.FileNotFoundException: …\java_home.save (拒绝访问)
        ⇒ JDK 21+ could not be found ⇒ rc=1

    同一个 bat 由 DSH 的 pwsh 直接起就正常（AGENTS §0.17 那条：子进程的权限与桌面 shell 不同）。
    所以分工是：本函数只做定位/命名/拼命令，**执行交给 pwsh**，产物再回来读。
    """
    if not (GHIDRA_HOME / "support" / "analyzeHeadless.bat").exists():
        raise SystemExit(f"缺 Ghidra：{GHIDRA_HOME / 'support' / 'analyzeHeadless.bat'}")
    # Ghidra 的工程是 `<name>.gpr` + `<name>.rep` 两个东西，别只查目录名。
    if not (GHIDRA_PROJ / f"{GHIDRA_PROJECT}.gpr").exists():
        raise SystemExit(f"缺 Ghidra 工程：{GHIDRA_PROJ / GHIDRA_PROJECT}.gpr（先 -import 一次）")
    name = ""
    rva = addr
    if rva is None:
        rva, name = resolve(load_methods(dump_dir), needle or "")
    tag = name.split("$$")[0].rsplit(".", 1)[-1] if name else "func"
    leaf = name.split("$$", 1)[1] if "$$" in name else f"0x{rva:x}"
    leaf = leaf.rsplit(".", 1)[-1]  # 只取方法名，别把命名空间塞进文件名
    leaf = re.sub(r"[^0-9A-Za-z_]+", "_", leaf)
    target = out or (GHIDRA_OUT / f"{tag}_{leaf}.c")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f"& '{GHIDRA_HOME / 'support' / 'analyzeHeadless.bat'}' '{GHIDRA_PROJ}' {GHIDRA_PROJECT} "
        f"-process GameAssembly.dll -noanalysis -scriptPath '{GHIDRA_SCRIPTS}' "
        f"-postScript DecompOne.java {hex(rva)} '{target}'"
    )
    return rva, name, target, command


# ---------------------------------------------------------------- 方法体索引

INDEX_COLUMNS = ("rva", "begin", "end", "srcfile", "srcline", "name")


def build_index(game: Path, dump_dir: Path, out: Path) -> tuple[int, int, int]:
    """把「方法 → RVA / 区间 / 源文件:行」全表落成一份 TSV（一次建、以后查）。"""
    dll = game / "GameAssembly.dll"
    if not dll.exists():
        raise SystemExit(f"缺 GameAssembly.dll：{dll}")
    buf = dll.read_bytes()
    pe = parse_pe(buf)
    begins, ends = load_pdata(buf, pe)
    rows = load_methods(dump_dir)
    us = load_usym(game / "BrokenArrow_Data" / "il2cpp_data" / "il2cpp.usym")
    out.parent.mkdir(parents=True, exist_ok=True)
    total = with_ext = with_src = 0
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(INDEX_COLUMNS) + "\n")
        for rva, name in rows:
            ext = pdata_extent(begins, ends, rva)
            src = usym_line(us, rva)
            total += 1
            with_ext += 1 if ext else 0
            with_src += 1 if src else 0
            fh.write("\t".join([
                f"{rva:x}",
                f"{ext[0]:x}" if ext else "",
                f"{ext[1]:x}" if ext else "",
                src[0] if src else "",
                str(src[1]) if src else "",
                name,
            ]) + "\n")
    tmp.replace(out)
    return total, with_ext, with_src


def find_in_index(out: Path, pattern: str, limit: int) -> int:
    """在索引里按子串查，打印命中行；返回命中数。"""
    if not out.exists():
        raise SystemExit(f"索引不存在，先跑 --build-index：{out}")
    n = 0
    with out.open("r", encoding="utf-8", errors="replace") as fh:
        next(fh, None)
        for line in fh:
            if pattern in line:
                if n < limit:
                    print(line.rstrip("\n"))
                n += 1
    print(f"# 命中 {n} 条（只打印前 {limit} 条）")
    return n


# ---------------------------------------------------------------- selftest

def selftest(game: Path, dump_dir: Path) -> int:
    """正控（结构性）+ 负控（找不到必须红）。

    刻意不断言具体地址/偏移 —— 换版本那些数就会变，断言写死只会变成假红。
    断言的是「这条链有没有真的接上」：

        ① .pdata 给出区间   ② 指令 ≥ 10 条   ③ 每条都有标注位
        ④ 至少一条带 `Units.cs:行号`（usym 真的命中了）
        ⑤ 字段表 ≥ 20 条（dump.cs 真的读到了）
        ⑥ 负控：编一个不存在的方法名，必须非零退出
    """
    target = "BrokenArrow.DataBase.Models.Units$$GetWeaponById"
    out = disassemble(game, dump_dir, target, None, 60)
    body = "\n".join(out)
    instrs = [ln for ln in out if re.match(r"^0x[0-9A-F]{8}\s", ln)]
    fields = load_fields(dump_dir, "BrokenArrow.DataBase.Models.Units")
    checks = [
        ("① .pdata 给出区间", any("来源 .pdata" in ln for ln in out)),
        ("② 指令 ≥ 10 条", len(instrs) >= 10),
        ("③ 每条都有标注位", all(" ; " in ln for ln in instrs) if instrs else False),
        ("④ 至少一条 Units.cs:行号", bool(re.search(r"Units\.cs:\d+", body))),
        ("⑤ 字段表 ≥ 20 条", len(fields) >= 20),
    ]
    try:
        resolve(load_methods(dump_dir), "Zzz.NoSuch.Type$$Zzz_NoSuchMethod__")
        neg_ok = False
    except SystemExit:
        neg_ok = True
    checks.append(("⑥ 负控：不存在的方法名必须红", neg_ok))

    print(body[:4000])
    print()
    for label, ok in checks:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}")
    bad = sum(0 if ok else 1 for _label, ok in checks)
    print("SELFTEST", "PASS" if bad == 0 else f"FAIL({bad})")
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="方法体视图（当前版本，非空壳）")
    ap.add_argument("symbol", nargs="?", help="Type.Method / Type$$Method / 子串")
    ap.add_argument("--addr", type=lambda s: int(s, 0), help="直接给方法 RVA")
    ap.add_argument("--game", type=Path, default=DEFAULT_GAME)
    ap.add_argument("--dump", type=Path, default=DEFAULT_DUMP)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--build-index", action="store_true", help="建全表索引（方法→RVA/区间/源文件:行）")
    ap.add_argument("--index", type=Path, default=CACHE / "method_index.tsv")
    ap.add_argument("--find", help="在索引里按子串查")
    ap.add_argument("--ghidra", action="store_true", help="出 C 伪代码（走已导入的 Ghidra 工程，约 9 秒/函数）")
    ap.add_argument("--ghidra-out", type=Path, help="伪代码产物路径")
    ap.add_argument("--force", action="store_true", help="已有产物也重新给命令")
    ap.add_argument("--ghidra-plan", action="store_true",
                    help="只输出 TSV：rva / 产物 / 工程目录 / 工程名 / 脚本目录 / analyzeHeadless 绝对路径（给 tools\\body.ps1 用）")
    args = ap.parse_args(argv)

    if args.ghidra_plan:
        if not args.symbol and args.addr is None:
            ap.error("--ghidra-plan 也要给 symbol 或 --addr")
        rva, _name, path, _cmd = ghidra_plan(args.dump, args.symbol, args.addr, args.ghidra_out)
        print("\t".join([
            hex(rva), str(path), str(GHIDRA_PROJ), GHIDRA_PROJECT,
            str(GHIDRA_SCRIPTS), str(GHIDRA_HOME / "support" / "analyzeHeadless.bat"),
        ]))
        return 0
    if args.ghidra:
        if not args.symbol and args.addr is None:
            ap.error("--ghidra 也要给 symbol 或 --addr")
        rva, name, path, command = ghidra_plan(args.dump, args.symbol, args.addr, args.ghidra_out)
        print(f"// {name or hex(rva)}  ·  RVA 0x{rva:X}")
        print(f"// 产物 {path}")
        if path.exists() and not args.force:
            print(f"// 已有产物（--force 重跑）：")
            print(path.read_text(encoding="utf-8", errors="replace"))
            return 0
        print("// 用 pwsh 跑这一条（约 9 秒），跑完再执行本命令就会打印 C：")
        print(command)
        return 0
    if args.build_index:
        total, ext, src = build_index(args.game, args.dump, args.index)
        print(f"索引已写：{args.index}")
        print(f"方法 {total} 条 · 有 .pdata 区间 {ext}（{ext * 100 // max(total, 1)}%）"
              f" · 有 源文件:行 {src}（{src * 100 // max(total, 1)}%）")
        return 0
    if args.find:
        find_in_index(args.index, args.find, args.limit)
        return 0
    if args.selftest:
        return selftest(args.game, args.dump)
    if not args.symbol and args.addr is None:
        ap.error("要么给 symbol，要么给 --addr")
    try:
        for line in disassemble(args.game, args.dump, args.symbol, args.addr, args.limit):
            print(line)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底，如实报错
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
