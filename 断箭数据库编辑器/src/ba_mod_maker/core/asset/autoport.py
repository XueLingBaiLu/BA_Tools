"""外部补丁地址表的**自动重定位**（2026-11-26「一劳永逸跟随游戏版本」）。

游戏更新后 ``GameAssembly.dll`` 的 sha256 变了，:data:`dllpatch.BUILDS` 认不出 ——
但**方法名 / 调用约定 / cave 机器码跨版本不变**。本模块从新版 DLL 自己的
元数据 + PE 导出表 + 调用点形状普查里把 12 个地址重新推出来，**全尺子通过才返回**；
任何一条不过 ⇒ :class:`AutoportError`（调用方拒绝打补丁，⛔ 不许带病上线）。

推导链（每一步都有独立判据，出处见各函数注释）
----------------------------------------------
1. ``string_new`` / ``gchandle_new`` —— PE 导出表逐字命中（最硬）。
2. ``file_exists`` / ``ctor`` / ``load_source`` + 4 个方法体 ——
   :mod:`il2cpp_index`（元数据 v39，已对 dumper 140,619/140,619 全量正控）。
3. ``init_meta`` / ``object_new`` —— 非导出运行期桩，用**调用点形状普查**：
   ``lea rcx,[rip+槽] / call X`` 的 X 计数冠军 = init_meta；
   ``mov rcx,[rip+槽] / call X`` 的冠军 = object_new（_work_db/patch1029/census.py 同口径，
   阈值按形状过滤后的计数重标）。负控：VA+0x10 的调用点数必须塌到 ~0。
4. ``slot_klass`` —— ctor 体内 ``lea rcx,[槽] / call init_meta`` 反算（ callee 路径），
   再用 **ctor 的调用方** ``mov rcx,[槽]`` 回看交叉验证（caller 路径，独立）。
5. 两处插桩点 —— Load()/LoadCompiled() 体内**唯一**指向 load_source 的 E8。
6. 两段 cave —— 死函数 LoadByJson()/Reload() 起点：16B 对齐 · 容量 ≥ cave_len ·
   e8/e9 超集零引用 · 7 个 callee ±2GB。
7. code72 单字节层 —— OnIncomingMessage 体内唯一 ``cmp sil,48h ; jne``（找不到 ⇒ 不带该层并明说）。
8. 写偏移闸 —— 每个**被写**地址必须满足 文件偏移 = RVA − 0xC00（il2cpp 节映射）；
   ⚠ 现盘 .text 节 Δ=0x1000 ≠ 0xC00，但 .text 里的目标只当调用目标、不被写，所以安全。
9. 离线装配 —— patched_image 纯内存打一遍：长度不变 · 差异只落声明区间 ·
   插桩点 e9 精确指向 cave · cave 与 build_cave() 逐字节相同。

⛔ 表错但自洽的风险（docs/操作-DLL补丁跟随游戏更新.md §5）由 1/3/4/6 的独立判据兜底：
导出名、形状普查负控、caller/callee 双路径、零引用超集 —— 不是自己验自己。
"""

from __future__ import annotations

import hashlib
import struct
from collections import Counter
from pathlib import Path

from ba_mod_maker.core.asset import dllpatch
from ba_mod_maker.core.asset.il2cpp_index import TARGET_METHODS, Il2CppIndex, Pe
from ba_mod_maker.core.errors import UserFacingError

#: 形状普查的锚点（census.py / verify_table.py 同一套字节）。
SLOT_LEA = b"\x48\x8d\x0d"  # lea rcx,[rip+disp32]
SLOT_MOV = b"\x48\x8b\x0d"  # mov rcx,[rip+disp32]

#: code72 分支的指令形状：``cmp sil,0x48`` (40 80 FE 48) + ``jne`` (0F 85)。
CODE72_PATTERN = b"\x40\x80\xfe\x48\x0f\x85"


class AutoportError(UserFacingError):
    """自动重定位失败 —— 拒绝打补丁。"""


def _exec_ranges(pe: Pe) -> list[tuple[int, int, int]]:
    return pe.exec_ranges()


def find_calls(pe: Pe, target_va: int, cap: int = 3_000_000) -> list[int]:
    """目标 VA 的全部 E8 调用点（文件偏移）。照抄 verify_table.find_calls。"""
    hits: list[int] = []
    data = pe.data
    base = pe.image_base
    for raw, size, secva in _exec_ranges(pe):
        end = raw + size
        pos = raw
        while len(hits) < cap:
            i = data.find(b"\xe8", pos)
            if i < 0 or i + 5 > end:
                break
            rel = struct.unpack_from("<i", data, i + 1)[0]
            if base + secva + (i - raw) + 5 + rel == target_va:
                hits.append(i)
            pos = i + 1
    return hits


def _shape_census(pe: Pe, anchor: bytes) -> Counter:
    """锚点指令（lea/mov rcx,[rip+槽]）后紧跟的 E8 目标计数。

    只统计「槽目标落在可写数据节」的调用点 —— 与 census.py 的 slots 集合判据同向
    （这里用节范围代替精确槽集合：槽必在 .data，负控与冠军 dominance 补强）。
    """
    data = pe.data
    base = pe.image_base
    counts: Counter = Counter()
    for raw, size, secva in _exec_ranges(pe):
        blob = data[raw : raw + size]
        pos = 0
        while True:
            i = blob.find(anchor, pos)
            if i < 0 or i + 12 > size:
                break
            pos = i + 1
            if blob[i + 7] != 0xE8:
                continue
            disp = struct.unpack_from("<i", blob, i + 3)[0]
            slot_va = base + secva + i + 7 + disp
            if not pe.writable_va_range(slot_va):
                continue
            rel = struct.unpack_from("<i", blob, i + 8)[0]
            counts[base + secva + i + 12 + rel] += 1
    return counts


def _pick_stub(pe: Pe, counts: Counter, *, min_count: int, label: str) -> int:
    """形状普查冠军 + 支配性 + 负控 + .text 节闸。"""
    ranked = counts.most_common(8)
    ranked = [(va, n) for va, n in ranked if n >= min_count]
    if not ranked:
        raise AutoportError(f"{label}: 形状普查没有 ≥{min_count} 的候选")
    if len(ranked) > 1 and ranked[1][1] * 2 > ranked[0][1]:
        raise AutoportError(
            f"{label}: 冠军不支配（{ranked[0][1]} vs {ranked[1][1]}）—— 歧义，拒绝"
        )
    va, n = ranked[0]
    secname = next(
        (s["name"] for s in pe.sections if s["va"] <= va - pe.image_base < s["va"] + s["vsize"]),
        "?",
    )
    if secname != ".text":
        raise AutoportError(f"{label}: 冠军 {va:#x} 不在 .text（在 {secname}）")
    neg = len(find_calls(pe, va + 0x10))
    budget = max(20, n // 100)
    if neg > budget:
        raise AutoportError(f"{label}: 负控塌不下来（VA+0x10 有 {neg} 调用点，预算 {budget}）")
    total = len(find_calls(pe, va))
    if total < min_count:
        raise AutoportError(f"{label}: 总调用点 {total} < {min_count}")
    return va


def _unique(idx: Il2CppIndex, key: str) -> int:
    t, m, pc = TARGET_METHODS[key]
    vas = idx.find(t, m, pc)
    if len(vas) != 1:
        raise AutoportError(
            f"{key}: {t}::{m}({pc}) 命中 {len(vas)} 个（要唯一）: {[hex(v) for v in vas]}"
        )
    return vas[0]


def _body(idx: Il2CppIndex, va: int) -> tuple[int, int]:
    end = idx.body_end(va)
    if end is None:
        raise AutoportError(f"{va:#x}: 找不到方法体终点")
    return va, end


def _e8_in(pe: Pe, start: int, end: int, target: int) -> list[int]:
    """[start,end) 体内指向 target 的 E8（VA 列表）。"""
    base = pe.image_base
    off = pe.rva_to_off(start - base)
    if off is None:
        raise AutoportError(f"方法体 {start:#x} 不在文件里")
    blob = pe.data[off : off + (end - start)]
    out = []
    pos = 0
    while True:
        i = blob.find(b"\xe8", pos)
        if i < 0 or i + 5 > len(blob):
            break
        rel = struct.unpack_from("<i", blob, i + 1)[0]
        if start + i + 5 + rel == target:
            out.append(start + i)
        pos = i + 1
    return out


def _refs_in_range(pe: Pe, lo: int, hi: int) -> int:
    """e8/e9 超集：目标落在 [lo,hi) 的引用总数（cave 零引用闸）。"""
    data = pe.data
    base = pe.image_base
    n = 0
    for raw, size, secva in _exec_ranges(pe):
        blob = data[raw : raw + size]
        for op in (b"\xe8", b"\xe9"):
            pos = 0
            while True:
                i = blob.find(op, pos)
                if i < 0 or i + 5 > size:
                    break
                rel = struct.unpack_from("<i", blob, i + 1)[0]
                t = base + secva + i + 5 + rel
                if lo <= t < hi:
                    n += 1
                pos = i + 1
    return n


def derive(
    dll: str | Path, metadata: str | Path, *, cave_len: int = 138
) -> tuple[dllpatch.Build, list[str]]:
    """从 (DLL, 元数据) 推出整张地址表。**全闸通过**才返回 (Build, 报告行)。"""
    dll = Path(dll)
    pe = Pe(dll)
    idx = Il2CppIndex(dll, metadata)
    base = pe.image_base
    rep: list[str] = []

    def ok(item: str, detail: str) -> None:
        rep.append(f"PASS {item}: {detail}")

    # 1) 导出表
    exps = pe.exports()
    targets: dict[str, int] = {}
    for key, name in (("string_new", "il2cpp_string_new"), ("gchandle_new", "il2cpp_gchandle_new")):
        if name not in exps:
            raise AutoportError(f"导出表没有 {name}")
        targets[key] = base + exps[name]
        ok(f"{key} export", f"{name} -> {targets[key]:#x}")

    # 2) 托管方法（元数据索引，唯一性闸）
    managed = {
        k: _unique(idx, k)
        for k in ("file_exists", "ctor", "load_source", "load", "load_compiled",
                  "load_by_json", "reload")
    }
    targets.update({k: managed[k] for k in ("file_exists", "ctor", "load_source")})
    for k, va in managed.items():
        t, m, pc = TARGET_METHODS[k]
        ok(f"{k} managed", f"{t}::{m}({pc}) -> {va:#x}")

    # 3) 非导出运行期桩（形状普查 + 负控）
    lea_counts = _shape_census(pe, SLOT_LEA)
    mov_counts = _shape_census(pe, SLOT_MOV)
    targets["init_meta"] = _pick_stub(pe, lea_counts, min_count=50_000, label="init_meta")
    targets["object_new"] = _pick_stub(pe, mov_counts, min_count=5_000, label="object_new")
    if targets["object_new"] == targets["init_meta"]:
        raise AutoportError("init_meta 与 object_new 撞同一个 VA")
    ok("init_meta census",
       f"{targets['init_meta']:#x} lea-shape {lea_counts[targets['init_meta']]}")
    ok("object_new census",
       f"{targets['object_new']:#x} mov-shape {mov_counts[targets['object_new']]}")

    # 4) klass 槽：**callee 路径 ∩ caller 路径**（两条独立证据的交集，唯一才算）
    #    callee：ctor 体里每个真 E8→init_meta 调用点，回看 7 字节必须是 lea rcx,[rip+槽]
    #            （ctor 会初始化它用到的**全部**类型槽 —— 实测 73 个，⛔ 不是唯一）；
    #    caller：ctor 的调用方在 call 前 4..40 字节内的 mov rcx,[rip+槽]（构造对象先 mov 槽）。
    #    只有「被构造的那个类型」的槽会同时出现在两条路径里。
    ctor = managed["ctor"]
    c_start, c_end = _body(idx, ctor)
    callee_slots: set[int] = set()
    for h in _e8_in(pe, c_start, c_end, targets["init_meta"]):
        off_h = pe.rva_to_off(h - base)
        prev = pe.data[off_h - 7 : off_h]
        if prev[:3] == SLOT_LEA:
            disp = struct.unpack_from("<i", prev, 3)[0]
            callee_slots.add(h + disp)
    if not callee_slots:
        raise AutoportError("ctor 体里没有 lea+call init_meta —— init_meta 可能认错了")
    caller_slots: set[int] = set()
    for call_off in find_calls(pe, ctor)[:3000]:
        for back in range(4, 41):
            p = call_off - back
            if p < 0 or pe.data[p : p + 3] != SLOT_MOV:
                continue
            va = pe.off_to_va(p)
            if va is None:
                continue
            disp = struct.unpack_from("<i", pe.data, p + 3)[0]
            caller_slots.add(va + 7 + disp)
    common = callee_slots & caller_slots
    if len(common) != 1:
        raise AutoportError(
            f"klass 槽双路径交集 {len(common)} 个"
            f"（callee {len(callee_slots)} ∩ caller {len(caller_slots)}，要唯一）"
        )
    slot = next(iter(common))
    if not pe.writable_va_range(slot):
        raise AutoportError(f"klass 槽 {slot:#x} 不在可写数据节")
    targets["slot_klass"] = slot
    ok("slot_klass", f"{slot:#x}（callee {len(callee_slots)} ∩ caller {len(caller_slots)} = 1）")

    # 5) 插桩点：体内唯一 E8 → load_source
    sites: list[tuple[str, int, int, int]] = []
    caves = {"Load()": managed["load_by_json"], "LoadCompiled()": managed["reload"]}
    for label, mkey in (("Load()", "load"), ("LoadCompiled()", "load_compiled")):
        s, e = _body(idx, managed[mkey])
        hits = _e8_in(pe, s, e, managed["load_source"])
        if len(hits) != 1:
            raise AutoportError(f"{label}: 体内指向 LoadSource 的 E8 有 {len(hits)} 个（要唯一）")
        steal = hits[0]
        if pe.data[pe.rva_to_off(steal - base)] != 0xE8:
            raise AutoportError(f"{label}: 插桩点字节不是 E8")
        cave = caves[label]
        sites.append((label, steal, steal + 5, cave))
        ok(f"{label} site", f"steal {steal:#x} ret {steal + 5:#x} cave {cave:#x}")

    # 6) cave 闸：真函数起点 / 16B 对齐 / 容量 / 零引用 / ±2GB
    callees = [
        targets[k]
        for k in ("string_new", "gchandle_new", "file_exists", "init_meta",
                  "object_new", "ctor", "load_source")
    ]
    for label, _steal, _ret, cave in sites:
        if cave not in idx.by_va:
            raise AutoportError(f"{label}: cave {cave:#x} 不是方法起点")
        if cave % 16 != 0:
            raise AutoportError(f"{label}: cave {cave:#x} 不是 16B 对齐")
        end = idx.body_end(cave)
        if end is None or end - cave < cave_len:
            cap = None if end is None else end - cave
            raise AutoportError(f"{label}: cave 容量 {cap} < {cave_len}")
        nrefs = _refs_in_range(pe, cave, cave + cave_len)
        if nrefs != 0:
            raise AutoportError(f"{label}: cave 区间有 {nrefs} 个 e8/e9 引用（要 0）")
        far = [t for t in callees + [_ret] if abs(t - cave) >= 2**31]
        if far:
            raise AutoportError(f"{label}: callee 超 ±2GB: {[hex(t) for t in far]}")
        ok(f"{label} cave", f"{cave:#x} 容量 {end - cave} · 零引用 · ±2GB 内")

    # 7) code72 单字节层（找不到 ⇒ 不带，明说）
    byte_patches: tuple[tuple[str, int, int, int], ...] = ()
    oim = _unique(idx, "on_incoming_message")
    s, e = _body(idx, oim)
    off = pe.rva_to_off(s - base)
    blob = pe.data[off : off + (e - s)]
    hits72 = []
    pos = 0
    while True:
        i = blob.find(CODE72_PATTERN, pos)
        if i < 0:
            break
        hits72.append(s + i + 3)
        pos = i + 1
    if len(hits72) == 1:
        site72 = hits72[0]
        if pe.data[pe.rva_to_off(site72 - base)] != 0x48:
            raise AutoportError(f"code72: {site72:#x} 原字节不是 0x48")
        byte_patches = (("忽略 ResetDeckData(code 72)", site72, 0x48, 0xFF),)
        ok("code72", f"{site72:#x} cmp sil,48h → FFh")
    elif not hits72:
        rep.append("WARN code72: OnIncomingMessage 里没有 cmp sil,48h;jne —— 本版不带卡组保护")
    else:
        raise AutoportError(f"code72: 命中 {len(hits72)} 个（要唯一）")

    # 8) 写偏移闸：被写地址必须 RVA−0xC00（il2cpp 节映射）
    orig = pe.data
    sha = hashlib.sha256(orig).hexdigest()
    write_vas = [cave for _l, _s, _r, cave in sites] + [steal for _l, steal, _r, _c in sites]
    write_vas += [va for _n, va, _o, _nw in byte_patches]
    for va in write_vas:
        rva = va - base
        if pe.rva_to_off(rva) != rva - dllpatch._SECTION_DELTA:
            raise AutoportError(
                f"写地址 {va:#x}: 节映射变了（文件偏移 {pe.rva_to_off(rva)} != RVA-0xC00）—— 拒绝"
            )
    ok("write-offset", f"{len(write_vas)} 个被写地址全部 RVA-0xC00")

    build = dllpatch.Build(
        label=f"自动重定位 ({sha[:8]})",
        orig_sha256=sha,
        base=base,
        init_meta=targets["init_meta"],
        file_exists=targets["file_exists"],
        object_new=targets["object_new"],
        ctor=targets["ctor"],
        load_source=targets["load_source"],
        string_new=targets["string_new"],
        gchandle_new=targets["gchandle_new"],
        slot_klass=targets["slot_klass"],
        sites=tuple(sites),
        cave_len=cave_len,
        backup_name=f"GameAssembly.dll.{sha[:8]}.orig",
        byte_patches=byte_patches,
    )

    # 9) 离线装配（纯内存）：长度不变 / 差异只落声明区间 / e9 精确 / cave 逐字节
    patched = dllpatch.patched_image(orig, build)
    if len(patched) != len(orig):
        raise AutoportError("离线装配改变了文件长度")
    if not dllpatch._only_patch_regions_differ(patched, orig, build):
        raise AutoportError("离线装配的差异落在声明区间外")
    for label, steal, ret, cave in sites:
        o_steal = pe.rva_to_off(steal - base)
        want_jump = b"\xe9" + struct.pack("<i", cave - (steal + 5))
        if patched[o_steal : o_steal + 5] != want_jump:
            raise AutoportError(f"{label}: 装配后插桩点不是精确 e9")
        o_cave = pe.rva_to_off(cave - base)
        if patched[o_cave : o_cave + cave_len] != dllpatch.build_cave(cave, ret, build):
            raise AutoportError(f"{label}: 装配后 cave 与 build_cave() 不一致")
    n_regions = len(dllpatch._patch_regions(build))
    ok("offline-assembly", f"长度不变 {len(orig):,} · 差异只落 {n_regions} 个声明区间")

    return build, rep