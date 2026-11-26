"""il2cpp 方法索引：从 ``GameAssembly.dll`` + ``global-metadata.dat`` 直接读出
**每个托管方法的 VA / 类型名 / 方法名 / 参数个数**（纯标准库，零外部工具）。

为什么需要它（2026-11-26「外部补丁一劳永逸跟随版本」）
------------------------------------------------------
外部 DLL 补丁的地址表按版本钉死；游戏一更新就认不出。但**方法名跨版本不变**——
只要能从新版 DLL 自己的元数据里重新读出方法地址，12 个地址就能自动重定位
（见 :mod:`ba_mod_maker.core.asset.autoport`）。本模块是那条路的地基。

布局来源（⛔ 不是猜的，逐条有出处）
----------------------------------
* 结构定义：Il2CppDumper（lxraa fork，**MIT**）``Il2Cpp/MetadataClass.cs`` /
  ``Metadata.cs`` / ``Il2Cpp.cs`` / ``Il2CppClass.cs`` / ``Utils/SectionHelper.cs``
  （2026-11-26 经 ghproxy 取回，与本机 ``_work_db/tools/Il2CppDumper_v39`` 同源）。
* 本机交叉验证：v39 头 31 个 (offset,size,count) 三元组的 size/count 比值全部整数
  （methods=32B · typeDefinitions=82B · parameters=12B …），且解析结果与同机 dumper
  产出的 script.json（666,688 方法）逐条对上（见 tests/test_il2cpp_index.py 的慢档）。

只支持 **metadata v38/v39**（Unity 6000.x，= 本游戏实测版本）。
别的版本 ⇒ :class:`UnsupportedMetadataError` —— 宁可拒绝，不许猜布局。

v39 与旧版（≤v35）的三处关键差异
--------------------------------
1. 头里每个段是 **12 字节三元组** (offset, size, count)，不是 8 字节对。
2. 若干索引字段是**变长**的（1/2/4 字节，按各自数组的 count 定）——
   ``typeIndexSize`` 由 parameters 段的 size/count 反推（= 结构体大小 − 8）。
3. 方法 RVA **不在元数据里**：在二进制 CodeRegistration → codeGenModules →
   每模块 methodPointers[token & 0xFFFFFF − 1]。CodeRegistration 的找法照抄
   SectionHelper.FindCodeRegistration2019（v35+ 分支）：
   ``mscorlib.dll\\0`` 字符串 → 三级指针回追 → imageCount 校验 → CR = ref − 128。
"""

from __future__ import annotations

import struct
from pathlib import Path

#: v38+ 头里 31 个段的顺序（MetadataClass.cs Il2CppGlobalMetadataHeader，Version(Min=38) 字段序）。
_SECTION_NAMES = (
    "stringLiterals", "stringLiteralData", "strings", "events", "properties", "methods",
    "parameterDefaultValues", "fieldDefaultValues", "fieldAndParameterDefaultValueData",
    "fieldMarshaledSizes", "parameters", "fields", "genericParameters",
    "genericParameterConstraints", "genericContainers", "nestedTypes", "interfaces",
    "vtableMethods", "interfaceOffsets", "typeDefinitions", "images", "assemblies",
    "fieldRefs", "referencedAssemblies", "attributeData", "attributeDataRanges",
    "unresolvedIndirectCallParameterTypes", "unresolvedIndirectCallParameterRanges",
    "windowsRuntimeTypeNames", "windowsRuntimeStrings", "exportedTypeDefinitions",
)

_IMAGE_SCN_MEM_EXECUTE = 0x20000000
_IMAGE_SCN_MEM_WRITE = 0x80000000


class UnsupportedMetadataError(ValueError):
    """元数据版本/布局不在支持范围 —— 拒绝，不猜。"""


def _idx_size(count: int) -> int:
    """Metadata.cs GetIndexSize：count ≤ 0xFF → 1B，≤ 0xFFFF → 2B，否则 4B。"""
    if count <= 0xFF:
        return 1
    if count <= 0xFFFF:
        return 2
    return 4


def _rd(buf: bytes, off: int, size: int) -> int:
    return int.from_bytes(buf[off : off + size], "little")


class Pe:
    """最小 PE64 读取器（照抄 _work_db/patch1029/pe_exports.py 的已验收实现，节分类补齐）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data = self.path.read_bytes()
        d = self.data
        e_lfanew = struct.unpack_from("<I", d, 0x3C)[0]
        if d[e_lfanew : e_lfanew + 4] != b"PE\x00\x00":
            raise UnsupportedMetadataError(f"不是 PE 文件: {self.path}")
        coff = e_lfanew + 4
        self.machine, nsec, _, _, _, optsize, _ = struct.unpack_from("<HHIIIHH", d, coff)
        opt = coff + 20
        self.magic = struct.unpack_from("<H", d, opt)[0]
        if self.magic != 0x20B:
            raise UnsupportedMetadataError("只支持 PE64（PE32+）")
        self.image_base = struct.unpack_from("<Q", d, opt + 24)[0]
        dd = opt + 112
        self.exp_rva, self.exp_size = struct.unpack_from("<II", d, dd)
        self.sections: list[dict] = []
        sec = opt + optsize
        for i in range(nsec):
            b = sec + i * 40
            name = d[b : b + 8].rstrip(b"\x00").decode("ascii", "replace")
            vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", d, b + 8)
            chars = struct.unpack_from("<I", d, b + 36)[0]
            self.sections.append(
                {"name": name, "va": va, "vsize": vsize,
                 "raw": rawptr, "rawsize": rawsize, "chars": chars}
            )

    def rva_to_off(self, rva: int) -> int | None:
        for s in self.sections:
            if s["va"] <= rva < s["va"] + max(s["vsize"], s["rawsize"]):
                return s["raw"] + (rva - s["va"])
        return None

    def off_to_va(self, off: int) -> int | None:
        for s in self.sections:
            if s["raw"] <= off < s["raw"] + min(s["vsize"], s["rawsize"]):
                return self.image_base + s["va"] + (off - s["raw"])
        return None

    def exec_ranges(self) -> list[tuple[int, int, int]]:
        """(raw, size, secva) —— 可执行节。"""
        return [
            (s["raw"], min(s["vsize"], s["rawsize"]), s["va"])
            for s in self.sections
            if s["chars"] & _IMAGE_SCN_MEM_EXECUTE and s["rawsize"]
        ]

    def data_ranges(self) -> list[tuple[int, int, int]]:
        """(raw, size, secva) —— 非执行节（.rdata/.data/…，指针回追的扫描范围）。"""
        return [
            (s["raw"], min(s["vsize"], s["rawsize"]), s["va"])
            for s in self.sections
            if not (s["chars"] & _IMAGE_SCN_MEM_EXECUTE) and s["rawsize"]
        ]

    def writable_va_range(self, va: int) -> bool:
        rva = va - self.image_base
        return any(
            s["va"] <= rva < s["va"] + max(s["vsize"], s["rawsize"])
            and s["chars"] & _IMAGE_SCN_MEM_WRITE
            for s in self.sections
        )

    def exports(self) -> dict[str, int]:
        """导出名 → RVA（照抄 pe_exports.py）。"""
        if not self.exp_rva:
            return {}
        off = self.rva_to_off(self.exp_rva)
        if off is None:
            return {}
        d = self.data
        n_name = struct.unpack_from("<I", d, off + 24)[0]
        addr_funcs, addr_names, addr_ords = struct.unpack_from("<III", d, off + 28)
        fo = self.rva_to_off(addr_funcs)
        no = self.rva_to_off(addr_names)
        oo = self.rva_to_off(addr_ords)
        out: dict[str, int] = {}
        for i in range(n_name):
            nr = struct.unpack_from("<I", d, no + i * 4)[0]
            s = self.rva_to_off(nr)
            end = d.index(b"\x00", s)
            name = d[s:end].decode("ascii", "replace")
            o = struct.unpack_from("<H", d, oo + i * 2)[0]
            out[name] = struct.unpack_from("<I", d, fo + o * 4)[0]
        return out

    def cstring(self, va: int) -> str:
        off = self.rva_to_off(va - self.image_base)
        if off is None:
            raise UnsupportedMetadataError(f"字符串指针不在任何节里: {va:#x}")
        end = self.data.index(b"\x00", off)
        return self.data[off:end].decode("utf-8", "replace")

    def qword(self, va: int) -> int:
        off = self.rva_to_off(va - self.image_base)
        if off is None:
            raise UnsupportedMetadataError(f"指针不在任何节里: {va:#x}")
        return struct.unpack_from("<Q", self.data, off)[0]

    def ptr_or_bss(self, va: int) -> int:
        """读一个指针；落在节 BSS 尾（磁盘上没有字节）⇒ 0（运行期才初始化）。"""
        rva = va - self.image_base
        for s in self.sections:
            if s["va"] <= rva < s["va"] + s["vsize"]:
                off = s["raw"] + (rva - s["va"])
                if off + 8 <= s["raw"] + s["rawsize"]:
                    return struct.unpack_from("<Q", self.data, off)[0]
                return 0
        raise UnsupportedMetadataError(f"指针不在任何节里: {va:#x}")


class Il2CppIndex:
    """一份 (DLL, 元数据) 的方法索引。

    属性
    ----
    by_va : dict[int, tuple[str, str, int]]
        VA → (类型全名, 方法名, 参数个数)。同一 VA 可能被多个方法共享
        （泛型共享 / 折叠）——这里只留第一条，找方法一律走 :meth:`find`。
    starts : list[int]
        全部非零方法指针的 VA，升序（量 cave 容量 / 方法体边界用）。
    """

    def __init__(self, dll: str | Path, metadata: str | Path) -> None:
        self.pe = Pe(dll)
        md = Path(metadata).read_bytes()
        sanity, version = struct.unpack_from("<Ii", md, 0)
        if sanity != 0xFAB11BAF:
            raise UnsupportedMetadataError(f"元数据 sanity 不对: {sanity:#x}")
        if version not in (38, 39):
            raise UnsupportedMetadataError(
                f"元数据版本 v{version} 不支持（只认 v38/v39 = Unity 6000.x）"
            )
        self.version = version
        sec: dict[str, tuple[int, int, int]] = {}
        for i, name in enumerate(_SECTION_NAMES):
            off, size, count = struct.unpack_from("<III", md, 8 + i * 12)
            sec[name] = (off, size, count)
        self.sections = sec

        # 变长索引尺寸（Metadata.cs：v38+ 分支）
        p_off, p_size, p_count = sec["parameters"]
        if p_count == 0:
            raise UnsupportedMetadataError("parameters 段为空")
        self.T = p_size // p_count - 8  # typeIndexSize
        self.D = _idx_size(sec["typeDefinitions"][2])
        self.G = _idx_size(sec["genericContainers"][2])
        self.P = _idx_size(p_count)
        if self.T not in (1, 2, 4):
            raise UnsupportedMetadataError(f"typeIndexSize 反推异常: {self.T}")

        self._md = md
        self._strings_off = sec["strings"][0]
        self._string_cache: dict[int, str] = {}

        self.by_va: dict[int, tuple[str, str, int]] = {}
        self._by_name: dict[tuple[str, str, int], list[int]] = {}
        self.starts: list[int] = []
        self._build()

    # ------------------------------------------------------------------ #

    def _str(self, index: int) -> str:
        hit = self._string_cache.get(index)
        if hit is None:
            off = self._strings_off + index
            end = self._md.index(b"\x00", off)
            hit = self._md[off:end].decode("utf-8", "replace")
            self._string_cache[index] = hit
        return hit

    def _find_code_registration(self, image_count: int) -> int:
        """SectionHelper.FindCodeRegistration2019（PE / v35+ 分支）的 Python 移植。"""
        pe = self.pe
        base = pe.image_base
        data = pe.data
        ranges = pe.data_ranges()
        cache: dict[int, list[int]] = {}

        def refs(va: int) -> list[int]:
            hit = cache.get(va)
            if hit is not None:
                return hit
            target = struct.pack("<Q", va)
            out: list[int] = []
            for raw, size, secva in ranges:
                blob = data[raw : raw + size]
                pos = 0
                while True:
                    i = blob.find(target, pos)
                    if i < 0 or i + 8 > size:
                        break
                    if i % 8 == 0:
                        out.append(base + secva + i)
                    pos = i + 1
            cache[va] = out
            return out

        feature = b"mscorlib.dll\x00"
        for raw, size, secva in ranges:
            blob = data[raw : raw + size]
            pos = 0
            while True:
                i = blob.find(feature, pos)
                if i < 0:
                    break
                pos = i + 1
                dllva = base + secva + i
                for r1 in refs(dllva):
                    for r2 in refs(r1):
                        for k in range(image_count - 1, -1, -1):
                            for r3 in refs(r2 - k * 8):
                                if pe.qword(r3 - 8) == image_count:
                                    return r3 - 16 * 8
        raise UnsupportedMetadataError("找不到 CodeRegistration（mscorlib.dll 指针链断了）")

    def _build(self) -> None:
        pe = self.pe
        md = self._md
        sec = self.sections
        tsz, dsz, gsz, psz = self.T, self.D, self.G, self.P

        td_off, td_size, td_count = sec["typeDefinitions"]
        td_stride = 68 + 3 * tsz + gsz
        if td_size // td_stride != td_count:
            raise UnsupportedMetadataError(
                f"typeDefinitions 尺寸对不上: size={td_size} stride={td_stride} count={td_count}"
            )
        m_off, m_size, m_count = sec["methods"]
        m_stride = 20 + dsz + tsz + psz + gsz
        if m_size // m_stride != m_count:
            raise UnsupportedMetadataError(
                f"methods 尺寸对不上: size={m_size} stride={m_stride} count={m_count}"
            )
        img_off, img_size, img_count = sec["images"]

        # 类型表：名字 / methodStart / method_count。
        # 嵌套类型全名走 **nestedTypes 段**（child→parent 映射）——⛔ 不走 declaringTypeIndex：
        # 那是 TypeIndex（指 Il2CppType 表，还要再解一层）；nestedTypes 段直接是
        # TypeDefinitionIndex。
        types: list[tuple[str, int, int]] = []
        base = 8 + 3 * tsz + gsz  # flags 的偏移
        ms_off = base + 8  # methodStart
        mc_off = base + 36  # method_count（8 个 u16 的第一个）
        nts_off = base + 20  # nestedTypesStart
        ntc_off = base + 36 + 8  # nested_type_count（第 5 个 u16）
        raw_names: list[tuple[str, str]] = []
        parent: dict[int, int] = {}
        nt_off, _nt_size, _nt_count = sec["nestedTypes"]
        for i in range(td_count):
            b = td_off + i * td_stride
            name = self._str(_rd(md, b, 4))
            ns = self._str(_rd(md, b + 4, 4))
            raw_names.append((name, ns))
            nts = _rd(md, b + nts_off, 4)
            ntc = _rd(md, b + ntc_off, 2)
            for k in range(ntc):
                child = _rd(md, nt_off + (nts + k) * 4, 4)
                parent[child] = i
        full_cache: dict[int, str] = {}

        def full_name(i: int) -> str:
            hit = full_cache.get(i)
            if hit is not None:
                return hit
            name, ns = raw_names[i]
            p = parent.get(i, -1)
            hit = f"{ns}.{name}" if ns else name
            if p >= 0:
                hit = f"{full_name(p)}.{name}"
            full_cache[i] = hit
            return hit

        for i in range(td_count):
            b = td_off + i * td_stride
            types.append((full_name(i), _rd(md, b + ms_off, 4), _rd(md, b + mc_off, 2)))

        # 方法表：名字 / 参数个数 / token
        methods: list[tuple[str, int, int]] = []
        pc_off = 18 + dsz + tsz + psz + gsz
        tok_off = 8 + dsz + tsz + psz + gsz
        for i in range(m_count):
            b = m_off + i * m_stride
            methods.append(
                (self._str(_rd(md, b, 4)), _rd(md, b + pc_off, 2), _rd(md, b + tok_off, 4))
            )

        # CodeRegistration → 每模块 methodPointers
        cr = self._find_code_registration(img_count)
        if pe.qword(cr + 15 * 8) != img_count:
            raise UnsupportedMetadataError("CodeRegistration.codeGenModulesCount 与 images 数不符")
        modules_ptr = pe.qword(cr + 16 * 8)
        base = pe.image_base
        module_ptrs: dict[str, list[int]] = {}
        # VA 范围按 VirtualSize（dumper CheckPointerRangeExecVa 同口径；⛔ 不是 min(vsize,rawsize)）
        exec_va = [
            (base + s["va"], base + s["va"] + s["vsize"])
            for s in pe.sections
            if s["chars"] & _IMAGE_SCN_MEM_EXECUTE
        ]

        def in_exec(va: int) -> bool:
            return any(lo <= va <= hi for lo, hi in exec_va)

        for i in range(img_count):
            mod_va = pe.qword(modules_ptr + i * 8)
            name = pe.cstring(pe.qword(mod_va))
            count = struct.unpack_from("<q", pe.data, pe.rva_to_off(mod_va - base + 8))[0]
            ptrs_va = pe.qword(mod_va + 16)
            if count < 0:
                raise UnsupportedMetadataError(f"模块 {name} 的 methodPointerCount 为负")
            if ptrs_va == 0:
                # 空模块（没有方法体）：dumper 同样兜成 zeros（Il2Cpp.cs Init 的 try/catch）
                ptrs = [0] * count
            else:
                if pe.rva_to_off(ptrs_va - base) is None:
                    raise UnsupportedMetadataError(f"模块 {name} 的 methodPointers 读不了")
                # 逐元素读：落在 BSS 尾（vsize > rawsize 的未初始化部分）的指针静态为 0
                # （dumper 对 BSS 地址 MapVATR 抛异常 → catch 兜 zeros，同一口径）。
                ptrs = [pe.ptr_or_bss(ptrs_va + j * 8) for j in range(count)]
            if any(p and not in_exec(p) for p in ptrs):
                raise UnsupportedMetadataError(f"模块 {name} 的 methodPointers 有不在代码节的值")
            module_ptrs[name] = ptrs

        # 镜像 → 类型区间 → 方法 → VA
        # ⚠ v39 实测 Il2CppImageDefinition = 32 + 2*dsz 字节（dsz=2 时 36B）：
        #   nameIndex u32@0 / assemblyIndex i32@4 / typeStart（TypeDefinitionIndex，dsz 字节）@8 /
        #   typeCount u32@(8+dsz) / exportedTypeStart（dsz 字节）/ exportedTypeCount u32 /
        #   entryPointIndex i32 / token u32 / customAttributeStart i32 / customAttributeCount u32。
        #   出处：Metadata.cs SizeOf 对 TypeDefinitionIndex 走变长分支（GetIndexSize(td_count)）；
        #   实测核对：BrokenArrow.dll typeStart=0/typeCount=4779，mscorlib typeStart=4779
        #   逐镜像 tiled（_work_db/patch1119/dbg_imgcols.py）。⛔ 不是源码字面那 10×4=40B。
        img_stride = 32 + 2 * dsz
        if img_size // img_stride != img_count:
            raise UnsupportedMetadataError(
                f"images 尺寸对不上: size={img_size} stride={img_stride} count={img_count}"
            )
        starts: set[int] = set()
        for i in range(img_count):
            b = img_off + i * img_stride
            img_name = self._str(_rd(md, b, 4))
            type_start = _rd(md, b + 8, dsz)
            type_count = _rd(md, b + 8 + dsz, 4)
            ptrs = module_ptrs.get(img_name)
            if ptrs is None:
                raise UnsupportedMetadataError(f"镜像 {img_name} 没有对应的 codeGenModule")
            for t in range(type_start, type_start + type_count):
                tname, m_start, m_cnt = types[t]
                for m in range(m_start, m_start + m_cnt):
                    mname, pc, token = methods[m]
                    rowid = token & 0x00FFFFFF
                    if rowid == 0 or rowid > len(ptrs):
                        continue
                    va = ptrs[rowid - 1]
                    if not va:
                        continue
                    starts.add(va)
                    self.by_va.setdefault(va, (tname, mname, pc))
                    self._by_name.setdefault((tname, mname, pc), []).append(va)
        self.starts = sorted(starts)

    # ------------------------------------------------------------------ #

    def find(self, type_name: str, method: str, param_count: int | None = None) -> list[int]:
        """按 (类型全名, 方法名[, 参数个数]) 找方法 VA。返回全部命中（调用方负责判唯一）。"""
        if param_count is not None:
            return list(self._by_name.get((type_name, method, param_count), []))
        out: list[int] = []
        for (t, m, _pc), vas in self._by_name.items():
            if t == type_name and m == method:
                out.extend(vas)
        return out

    def body_end(self, va: int) -> int | None:
        """方法体终点 = 下一个方法起点（索引口径，与 sites.py 同一把尺子）。"""
        import bisect

        i = bisect.bisect_right(self.starts, va)
        return self.starts[i] if i < len(self.starts) else None


#: 补丁需要的 7 个方法（类型全名, 方法名, 参数个数）—— 全部来自 1029/1119 两版索引实测。
TARGET_METHODS = {
    "load": ("BrokenArrow.Shared.Ecs.DataBaseService", "Load", 0),
    "load_compiled": ("BrokenArrow.Shared.Ecs.DataBaseService", "LoadCompiled", 2),
    "load_by_json": ("BrokenArrow.Shared.Ecs.DataBaseService", "LoadByJson", 0),
    "reload": ("BrokenArrow.Shared.Ecs.DataBaseService", "Reload", 0),
    "load_source": ("BrokenArrow.Shared.Ecs.DataBaseCompiled", "LoadSource", 0),
    "ctor": ("BrokenArrow.DataBase.DataBaseSourceData", ".ctor", 2),
    "file_exists": ("System.IO.File", "Exists", 1),
    "on_incoming_message": (
        "BrokenArrow.Client.Ecs.GNetwork.Services.Room.NetworkRoomService", "OnIncomingMessage", 2,
    ),
}


def main() -> int:
    import sys

    if len(sys.argv) < 3:
        print(
            "用法: python -m ba_mod_maker.core.asset.il2cpp_index "
            "<GameAssembly.dll> <global-metadata.dat>"
        )
        return 2
    idx = Il2CppIndex(sys.argv[1], sys.argv[2])
    print(f"metadata v{idx.version}  T={idx.T} D={idx.D} G={idx.G} P={idx.P}")
    print(f"方法起点 {len(idx.starts)} 个（by_va {len(idx.by_va)}）")
    for key, (t, m, pc) in TARGET_METHODS.items():
        vas = idx.find(t, m, pc)
        print(f"  {key:20s} {t}::{m}({pc}) -> {[hex(v) for v in vas]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())