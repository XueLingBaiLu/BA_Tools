"""W5-B1 · L1 等长写回器：替换一个区间 → 重算 CRC → 更新 catalog（设计稿 §9.1 的一趟）。

## 为什么另起一个写回器，而不是复用 core/asset/ufs.py::rewrite_container

ufs.py（W1 交付）里的 rewrite_container 是 **data.unity3d 专用**：它内部写死了

    node = reader.node("globalgamemanagers.assets")     # ufs.py:960
    info = read_serialized_file(reader, node)           # 并据此打元数据补丁

bundle 的节点叫 CAB-<hash>（units = CAB-1e53370fe5e57c593d6b445e251b8d1c），
那条路当场 AssetError。本单的禁区是「⛔ 不许改 ufs.py」（工单 §5），所以这里**复用它的构件**
（Block / UfsReader / lz4hc / serialize_blocks_info / serialize_header），
把容器拼装这一段自己写 —— 而 **L1 让这一段短得多**：

    delta = 0  ⇒  节点表不动、对象表不动、SerializedFile 头不动
              ⇒  要改的只有「命中块的压缩字节 + blocksInfo + 文件头」

## L1 的四道自证（都是**实测**出来的，不是推的）

1. **对象字节可复现**：typetree 往返对 units 包三个真材质**逐字节相同**
   （_work/w5_probe_material.py：4844 / 5456 / 4684 B，roundtrip_equal=True）
   ⇒ 改一个 float 之后新字节数**仍等于**原字节数（F7：delta == 0）；
2. **命中块可算**：对象在解压流里的区间由 SerializedFileInfo.objects 给
   （节点.offset + data_offset + stored），bisect 一下就是块号；实测 RU_T72B 1 落在**单块 #2812**；
3. **CRC 可预知**：m_Crc = crc32(逐块解压拼接)（kb/03 §3.1，77/77 实测命中），
   L1 下新流 = 旧流把那一段换掉 ⇒ **不写文件就能算出写回后的 m_Crc 真值**（干跑给的是真值，
   不是估算）；
4. **文件大小可预知**：新大小 = 文件头 + 新 blocksInfo 压缩长 + Σ每块压缩长；
   脏块重压一次就准（LZ4HC 可逐字节复现，设计稿 §9.10.3 实测 3/3）。

## catalog 那一边（工单 §7 坑 1 / kb/09 §2）

m_Crc / m_BundleSize 藏在 **m_ExtraDataString** 里的一条 AssetBundleRequestOptions
（UTF-16LE 紧凑 JSON），而 m_EntryDataString 每条的第 [4] 个 int32 是**字节偏移**
extraDataOffset 指向它 ⇒ 改一个数的**十进制位数**就会让后面所有偏移平移。
所以：⛔ 不许字符串替换，**全量重算偏移**。本模块两条自带正控：

* 整份 catalog.json 重新序列化（separators=(",", ":")、ensure_ascii=False）**逐字节回到原样**
  （实测 4,016,273 B 成立）；
* 单条 AssetBundleRequestOptions 重新序列化**逐字节回到原样**（实测 724 B 成立）。
  两条不成立就直接抛，绝不写。
"""

from __future__ import annotations

import base64
import contextlib
import gc
import hashlib
import json
import shutil
import struct
import time
import zlib
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone, reverse_delta
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.asset_kernel.streaming import (
    STREAM_SUFFIXES,
    StreamingInfo,
    parse_streaming_info,
)
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "LEVEL_EQUAL",
    "LEVEL_GROW",
    "CatalogEntry",
    "CatalogPatch",
    "DeltaChange",
    "NewObject",
    "ObjectChange",
    "ObjectRemoval",
    "ObjectGrowth",
    "PlanRow",
    "StreamAppend",
    "StreamChange",
    "WriteReport",
    "WritebackPlan",
    "build_material_change",
    "build_stream_change",
    "catalog_entry",
    "catalog_patch",
    "default_backup_dir",
    "locate_unit_asset",
    "material_float_names",
    "next_path_id",
    "plan_delta",
    "plan_removals",
    "plan_stream_writeback",
    "plan_writeback",
    "set_material_float",
    "texture_slot_path_id",
    "write_delta",
    "write_objects",
    "write_removals",
    "write_stream",
    "writeback_texture_file",
    "writeback_unit_material",
    "writeback_unit_texture_format",
]

#: 流式后缀的小写形式（比对两边都转小写 —— 「.resS」那个大写 S 是真踩过的坑）。
_STREAM_SUFFIX_LOWER: tuple[str, ...] = tuple(s.lower() for s in STREAM_SUFFIXES)

#: 写面等级：**L1 = 等长**（原地覆盖）。级别的判据是「新字节数 == 原字节数」，不是资产类型。
LEVEL_EQUAL = "L1"
#: 长度变了。本单一律**报错退出**（工单 §7 坑 5：第一版只会 L 级，不许硬写）。
LEVEL_GROW = "Δ"

#: 预计耗时的口径（设计稿 §9.10.3 实测）：重压一块 ≈ 13 ms；全量 CRC（解压 5.43 GB）≈ 3 s。
RECOMPRESS_SECONDS_PER_BLOCK = 0.013
FULL_CRC_SECONDS = 3.0

#: Addressables 的 RuntimePath 占位符（m_InternalIds 里的前缀，实测 77 条全是它 + \PC\）。
RUNTIME_PATH = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"

#: catalog.json 的序列化口径（实测能逐字节回到原样的那一组参数）。
_CATALOG_JSON_KW: dict[str, Any] = {"separators": (",", ":"), "ensure_ascii": False}


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ObjectChange:
    """一个要替换的对象：**等长**新字节 + 定位用的 path_id。"""

    path_id: int
    payload: bytes
    name: str = ""
    type_name: str = ""


@dataclass(frozen=True, slots=True)
class ObjectRemoval:
    """**删除一个对象**（2026-10-06 用户口径：产品只有「新增」与「删除」两种操作）。

    ⚠ 只摘**对象表条目**：被删对象的字节留在数据区成死区 —— 这是**安全档**，任何老对象的
    坐标都不动（连字节一起收回去是另一档，见 _removal_entries 的说明）。

    `refs` —— **同时摘引用**（2026-10-06 · #6c，真机铁律：只摘条目、把 AssetBundle 里指它的
    m_Container / m_PreloadTable 留着 ⇒ **进游戏 UI 整块消失**，包加载失败）：

    * `trim`（默认）**摘元素**：把命中的 m_Container 条目与 m_PreloadTable 项**摘掉**
      （同一 pathID 出现多次就全摘），并把后面条目的 preloadIndex 重编号；
      对象因此变短 ⇒ **原地写更短的 payload（余下留死区）＋ 改它自己那条表项的 byteSize**
      ⇒ 仍然落在**等长档**（解压流长度 / data_offset 一个字节不动）。
    * `null` **置空**：把命中的 PPtr 改成 (m_FileID=0, m_PathID=0)，数组**长度不变** ⇒ 等长；
      ⚠ 但包里会留下「有名字、没有资产」的空条目（游戏认不认 null 未验）。
    * `keep` 老行为（只摘对象表条目，不动引用）—— 只给对照/负控用，⛔ 产品默认不许用。

    同一次调用里多个 ObjectRemoval 的 `refs` 必须一致（引用清理是**整个包**的一件事）。
    """

    path_id: int
    name: str = ""
    refs: str = "trim"


@dataclass(frozen=True, slots=True)
class StreamChange:
    """一个要替换的**流式区间**（node[1] = CAB-*.resS 里的像素字节），**等长**。

    ⚠️ `offset` 是**节点内相对偏移**（== m_StreamData.offset），⛔ 不是文件偏移、
    也不是解压流偏移 —— 解压流绝对区间 = 目标节点.offset + offset（节点按 `basename` 匹配）。
    `payload` 的字节数必须 == `old_size`（G1）；不等时 plan 判成 Δ 级并拦住。
    """

    node_path: str
    offset: int
    old_size: int
    payload: bytes
    owner_path_id: int = 0
    name: str = ""
    type_name: str = "Texture2D"


@dataclass(frozen=True, slots=True)
class PlanRow:
    """判据一行里的「对象」那行。"""

    path_id: int
    name: str
    type_name: str
    old_bytes: int
    new_bytes: int
    level: str
    block_first: int
    block_last: int
    stream_start: int


@dataclass(frozen=True, slots=True)
class WritebackPlan:
    """干跑结果：F1 那一行要的每个数都在这里，且全是**真值**（不是估算）。"""

    bundle: Path
    bundle_name: str
    rows: tuple[PlanRow, ...]
    block_runs: tuple[tuple[int, int], ...]
    hit_blocks: int
    eta_seconds: float
    old_file_size: int
    new_file_size: int
    #: 磁盘现状的 crc32(逐块解压拼接) —— F4 的「改前」那一侧（与 catalog 里写的值比）。
    disk_crc: int
    catalog_bundle_name: str
    catalog_crc_old: int | None
    catalog_crc_new: int
    catalog_size_old: int | None
    catalog_extra_offset: int | None
    #: True = 这条计划来自 **Δ（变长）链**（plan_delta）：长度变了是**预期**，不拦。
    allow_grow: bool = False
    #: Δ：解压流里的插入点 —— [(旧流坐标 p, 插入字节数)]。
    insert_points: tuple[tuple[int, int], ...] = ()
    #: Δ：新块表里**重新压过**的块下标（工单 §2 ②：插入点落在块中间时只有 3 块）。
    recompressed_blocks: tuple[int, ...] = ()
    #: Δ：**原样拷贝压缩字节**的块 —— [(新块下标, 旧块下标)]（证明没有重压）。
    verbatim_blocks: tuple[tuple[int, int], ...] = ()
    #: Δ：节点尾追加的落点 —— [(节点 basename, 节点内新偏移, 字节数)]。
    append_offsets: tuple[tuple[str, int, int], ...] = ()
    #: W8：**新增对象**的读数 —— [(pathID, 表内插入下标, byteStart, byteSize, typeID)]。
    added_objects: tuple[tuple[int, int, int, int, int], ...] = ()
    #: 2026-10-06：**删除对象**的读数 —— [(pathID, 被删掉的那条在旧表里的下标)]。
    #: 删除走**等长档**：只摘对象表条目，数据区一个字节不动（见 _removal_entries）。
    removed_objects: tuple[tuple[int, int], ...] = ()
    #: 2026-10-06 · #6c：删对象时顺带做的**引用清理**读数
    #: —— [(模式, AssetBundle 的 pathID, 旧字节数, 新字节数, 处理掉的引用条数)]。
    #: 真机铁律：只摘条目、把 AssetBundle 里指它的 m_Container / m_PreloadTable 留着
    #: ⇒ 进游戏 **UI 整块消失**（包加载失败）。
    ref_cleanup: tuple[tuple[str, int, int, int, int], ...] = ()
    #: W8：Δmeta —— metadata_size / data_offset 的增量（含 16 对齐的补零）。
    meta_growth: int = 0
    #: W8：老表项 byteStart 的增量（B 口径恒为 0；见 _added_meta 的恒等式）。
    byte_start_shift: int = 0
    old_metadata_size: int | None = None
    new_metadata_size: int | None = None
    old_data_offset: int | None = None
    new_data_offset: int | None = None
    blocked_reason: str = ""

    def block_labels(self) -> str:
        if not self.block_runs:
            return "—"
        parts = [f"#{a}" if a == b else f"#{a}~#{b}" for a, b in self.block_runs]
        return "、".join(parts)

    def criterion_lines(self) -> tuple[str, ...]:
        """**F1 判据一行**（设计稿 §9.10.6 的强制格式）。

        必须含：对象 / 原字节数 / 新字节数 / 级别 / 命中块数 / 预计耗时。缺一个都不算数。
        """
        lines = [f"[写回计划] 目标: {self.bundle_name}"]
        for row in self.rows:
            if row.level == LEVEL_EQUAL:
                note = "等长（原地覆盖）"
            elif self.allow_grow:
                note = "长度变了 → Δ 搬到尾部（尾部追加 + 只改它那一条表项）"
            else:
                note = "长度变了（本单只支持 L1，禁止写回）"
            lines.append(
                f"  对象 {row.type_name or 'Object'}『{row.name}』 pathID={row.path_id}"
                f"   原 {row.old_bytes:,} B   新 {row.new_bytes:,} B   → {row.level} {note}"
            )
        if self.allow_grow:
            lines.append(
                f"  Δ 机制: 插入点 {[p for p, _d in self.insert_points]}"
                f"（{len(self.insert_points)} 处，共 {sum(d for _p, d in self.insert_points):,} B）"
                f"   重压块 {list(self.recompressed_blocks)}"
                f"   原样拷贝块 {len(self.verbatim_blocks)} 个"
            )
            if self.append_offsets:
                lines.append(
                    "  节点尾追加: "
                    + "；".join(
                        f"{name}@{offset:,}+{size:,}" for name, offset, size in self.append_offsets
                    )
                )
        if self.added_objects:
            lines.append(
                "  Δ 新增对象: "
                + "；".join(
                    f"pathID={pid}（表内第 {index + 1:,} 条，byteStart {stored:,}，"
                    f"{size:,} B，typeID {tid}）"
                    for pid, index, stored, size, tid in self.added_objects
                )
            )
            lines.append(
                f"  Δmeta: metadata_size {self.old_metadata_size:,} → "
                f"{self.new_metadata_size:,}（+{self.meta_growth:,}）；data_offset "
                f"{self.old_data_offset:,} → {self.new_data_offset:,}；"
                f"byteStart 平移 {self.byte_start_shift:+,} B"
            )
        if self.ref_cleanup:
            lines.append(
                "  引用清理（#6c）: "
                + "；".join(
                    f"{mode} —— AssetBundle(pathID={pid}) 对象 {old:,} → {new:,} B"
                    f"（处理 {cleared} 条指向被删对象的引用）"
                    for mode, pid, old, new, cleared in self.ref_cleanup
                )
            )
        lines.append(
            f"  命中块: {self.block_labels()}（{self.hit_blocks} 块）"
            f"   重压 {self.hit_blocks} 块 ≈ "
            f"{self.hit_blocks * RECOMPRESS_SECONDS_PER_BLOCK * 1000:.0f} ms   "
            f"全量 CRC ≈ {FULL_CRC_SECONDS:.0f} s   预计耗时 ≈ {self.eta_seconds:.1f} s"
        )
        delta = self.new_file_size - self.old_file_size
        catalog_tail = ""
        if self.catalog_crc_old is not None:
            catalog_tail = (
                f"，catalog m_Crc {self.catalog_crc_old} → {self.catalog_crc_new}"
                f" / m_BundleSize {self.catalog_size_old} → {self.new_file_size}"
            )
        lines.append(
            f"  预期: 文件 {self.old_file_size:,} → {self.new_file_size:,} B"
            f"（{delta:+,}）{catalog_tail}"
        )
        if self.blocked_reason:
            lines.append(f"  ⛔ 不许执行：{self.blocked_reason}")
        return tuple(lines)

    def summary(self) -> str:
        first = self.rows[0] if self.rows else None
        what = (
            f"{first.type_name}『{first.name}』 pathID={first.path_id}"
            if first
            else "（无对象）"
        )
        return (
            f"{self.bundle_name}：{what} → {first.level if first else '—'}   "
            f"命中块 {self.hit_blocks}   文件 {self.old_file_size:,} → {self.new_file_size:,} B"
        )


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """catalog.json 里某条 bundle 的登记（三张表 + m_ExtraDataString 上的落点）。"""

    bundle_name: str
    internal_id: str
    internal_id_index: int
    entry_index: int
    extra_offset: int
    crc: int | None
    bundle_size: int | None
    json_bytes: int


@dataclass(frozen=True, slots=True)
class CatalogPatch:
    """catalog.json 的一次**内容改动**（还没落盘）。"""

    path: Path
    before: bytes
    after: bytes
    entry: CatalogEntry
    crc_old: int | None
    crc_new: int
    size_old: int | None
    size_new: int
    offset_shift: int
    #: ★ 2026-10-15：这份 catalog 是 1.2.1 起那份**二进制**的（读数是另一套话术）。
    binary: bool = False

    def lines(self) -> tuple[str, ...]:
        head = (
            f"catalog: m_Crc {self.crc_old} → {self.crc_new}   "
            f"m_BundleSize {self.size_old} → {self.size_new}   "
        )
        if self.binary:
            return (
                head + "（catalog.bin 全量重编码，无字符串替换）",
                f"catalog 文件 {len(self.before):,} → {len(self.after):,} B",
            )
        return (
            head + f"extraDataOffset 平移 {self.offset_shift:+d} B（全量重算，无字符串替换）",
            f"catalog 文件 {len(self.before):,} → {len(self.after):,} B",
        )


@dataclass(slots=True)
class WriteReport:
    """一次写回（干跑或真写）的结果读数。"""

    bundle: Path
    bundle_name: str
    dry_run: bool
    plan: WritebackPlan
    verify: dict[str, object] = field(default_factory=dict)
    backup: Path | None = None
    backup_sha256: str = ""
    catalog_backup: Path | None = None
    wrote_bundle: bool = False
    wrote_catalog: bool = False
    #: 写的时候游戏在不在跑（在线写回时它会是 True —— 见 allow_running_game 的说明）。
    game_was_running: bool = False
    seconds: float = 0.0
    #: 反向 Δ 那条链：补丁落在工程目录（ADR 0007）—— ⛔ 不再是整包副本。
    patch: Path | None = None
    patch_bytes: int = 0

    def summary(self) -> str:
        head = "干跑" if self.dry_run else "已写回"
        return f"[{head}] {self.plan.summary()}   耗时 {self.seconds:.1f}s"

    def report_lines(self) -> tuple[str, ...]:
        lines = list(self.plan.criterion_lines())
        if self.dry_run:
            lines.append("  （干跑：一个字节都没写；上面的 CRC / 文件大小是算出来的真值）")
            return tuple(lines)
        if self.backup is not None:
            lines.append(f"  备份: {self.backup}   sha256 {self.backup_sha256[:16]}…")
        if self.catalog_backup is not None:
            lines.append(f"  catalog 备份: {self.catalog_backup}")
        if self.patch is not None:
            lines.append(
                f"  反向 Δ: {self.patch}（{self.patch_bytes:,} B）  "
                f"装前 sha256 {self.backup_sha256[:16]}…  还原走补丁柜"
            )
        if self.game_was_running:
            lines.append("  ⚠ 写的时候游戏在跑（实测文件未被占用）；要**重启游戏**才看得到")
        lines.append("[写后复核]")
        for key, value in self.verify.items():
            if not isinstance(value, Mapping):
                lines.append(f"  {key}: {value}")
                continue
            mark = "✅" if value.get("ok") else "❌"
            lines.append(f"  {mark} {key}: {value.get('detail', '')}")
        return tuple(lines)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 6) -> None:
    """原子替换带 GC 抖动 + 退避重试 —— **转调** reverse_delta.replace_with_retry。

    口径同 _assert_same_drive：本模块在 reverse_delta 的**上游**，而它反过来 import 本模块
    ⇒ 实现只能住那边，⛔ 这里不许再留第二份（两份就会有一份忘了 GC）。

    ⚠ 异常类型**保持不变**：下面两处调用点按 isinstance(exc, PermissionError) 分流
    （写回失败时的话术 + 回滚），所以把 ReplaceBlockedError 拆回它带的那个原始 OSError。
    """
    try:
        reverse_delta.replace_with_retry(src, dst, attempts=attempts)
    except reverse_delta.ReplaceBlockedError as exc:
        if exc.last is None:  # pragma: no cover -- 占用类错误必有 last
            raise
        raise exc.last from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _align16(value: int) -> int:
    return value + (-value) % 16


def _hit_blocks(reader: ufs.UfsReader, start: int, end: int) -> tuple[int, int]:
    """解压流区间 [start, end) 覆盖到的块区间（闭区间，下标）。"""
    if end <= start:
        raise AssetError("空区间", hint="替换区间必须是非空的")
    a = max(bisect_right(reader.block_dstart, start) - 1, 0)
    b = max(bisect_right(reader.block_dstart, end - 1) - 1, 0)
    return a, b


def _merge_runs(pairs: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    if not pairs:
        return ()
    ordered = sorted(pairs)
    out = [ordered[0]]
    for a, b in ordered[1:]:
        la, lb = out[-1]
        if a <= lb + 1:
            out[-1] = (la, max(lb, b))
        else:
            out.append((a, b))
    return tuple(out)


def _node_basename(path: str) -> str:
    """节点的 basename（两种斜杠都认）—— 节点名里带 CAB 哈希，所以按 basename 匹配。"""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _range_sha256(reader: ufs.UfsReader, start: int, end: int) -> str:
    """解压流 [start, end) 的 sha256。

    ⚠️ **逐块**喂哈希，不用 read_range：units 的 node[0] 是 2,526,267,644 B，
    一次读进来就是 2.5 GB 的 bytes（G2 判据要算它两次）。
    """
    digest = hashlib.sha256()
    starts = reader.block_dstart
    index = max(bisect_right(starts, start) - 1, 0)
    pos = start
    while pos < end:
        if index >= len(reader.blocks):
            raise AssetError(f"读到解压流末尾还没到 {end:,}（起点 {start:,}）")
        bstart = starts[index]
        bend = bstart + reader.blocks[index].uncompressed
        if bend <= pos:
            index += 1
            continue
        raw = _block_bytes(reader, index)
        take_end = min(bend, end)
        digest.update(raw[pos - bstart : take_end - bstart])
        pos = take_end
        index += 1
    return digest.hexdigest()


def _stream_info_of_object(obj_bytes: bytes, *, path_id: int = 0) -> StreamingInfo:
    """从 Texture2D 的对象字节里取 m_StreamData —— **实测布局 + 末尾自证**，零第三方依赖。

    为什么不读 typetree：本函数的入参只有容器的 UfsReader（冻结签名），而
    core/asset/ufs.py 里没有类型树解析器（⛔ 本单不许改它）；再 UnityPy.load 一遍
    3.38 GB 的包会多占约 10 GB 内存，不可接受。

    实测布局（_work/w6_probe_layout2.py：units 包 400 张流式贴图抽样）::

        ... | offset(u64) | size(u32) | pathlen(i32) | path 字节 | 4 字节对齐填充

    即 **path 是最后一个字段**，对象末尾按 4 字节对齐（87 B 的 path 补 1 B）。
    实测读数：400/400 张「命中唯一 + 对齐正好落在对象末尾 + offset/size 与 UnityPy
    typetree 逐字段相同」。三条判据缺一不可，否则抛（⛔ 不许猜）：

    ① 对象字节里 archive:/… 的候选**唯一**；
    ② 它按 4 字节对齐后**正好落在对象末尾**；
    ③ path 以流式后缀结尾、size > 0。
    """
    needle = b"archive:/"
    hits: list[StreamingInfo] = []
    pos = obj_bytes.find(needle)
    while pos >= 16:
        (path_len,) = struct.unpack_from("<i", obj_bytes, pos - 4)
        if path_len > 0 and pos + path_len <= len(obj_bytes):
            (size,) = struct.unpack_from("<I", obj_bytes, pos - 8)
            (offset,) = struct.unpack_from("<Q", obj_bytes, pos - 16)
            path = obj_bytes[pos : pos + path_len].decode("utf-8", "replace")
            aligned_end = (pos + path_len + 3) // 4 * 4
            if aligned_end == len(obj_bytes) and path.lower().endswith(_STREAM_SUFFIX_LOWER):
                info = parse_streaming_info(
                    {"path": path, "offset": int(offset), "size": int(size)}
                )
                if info is not None and info.is_streamed:
                    hits.append(info)
        pos = obj_bytes.find(needle, pos + 1)
    if len(hits) != 1:
        raise AssetError(
            f"pathID={path_id} 的对象字节（{len(obj_bytes):,} B）里，"
            f"贴在对象末尾的 m_StreamData 命中 {len(hits)} 个（应为 1）",
            hint="m_StreamData 是 Texture2D 的最后一个字段（_work/w6_probe_layout2.py 实测）"
            "；不成立就说明这个对象不是流式贴图、或序列化布局变了 —— ⛔ 不许猜",
        )
    return hits[0]


def _patch_bytes(raw: bytes, bstart: int, reps: Sequence[ufs.Replacement]) -> bytes:
    """把一个块的解压字节里、落在替换区间上的部分盖成新载荷（等长 ⇒ 长度不变）。"""
    out: bytearray | None = None
    bend = bstart + len(raw)
    for rep in reps:
        lo = max(rep.start, bstart)
        hi = min(rep.end, bend)
        if hi <= lo:
            continue
        if out is None:
            out = bytearray(raw)
        out[lo - bstart : hi - bstart] = rep.payload[lo - rep.start : hi - rep.start]
    return raw if out is None else bytes(out)


def _block_bytes(reader: ufs.UfsReader, index: int) -> bytes:
    """一个块的解压字节 —— 走**公开**的 read_range，不戳 ufs 的私有方法。"""
    return reader.read_range(reader.block_dstart[index], reader.blocks[index].uncompressed)


def _patched_block(reader: ufs.UfsReader, index: int, reps: Sequence[ufs.Replacement]) -> bytes:
    return _patch_bytes(_block_bytes(reader, index), reader.block_dstart[index], reps)


def _dual_crc(reader: ufs.UfsReader, reps: Sequence[ufs.Replacement]) -> tuple[int, int]:
    """一趟解压整包，同时算出「磁盘现状」与「写回后」两个 m_Crc。"""
    crc_old = 0
    crc_new = 0
    starts = reader.block_dstart
    for index in range(len(reader.blocks)):
        raw = _block_bytes(reader, index)
        crc_old = zlib.crc32(raw, crc_old)
        crc_new = zlib.crc32(_patch_bytes(raw, starts[index], reps), crc_new)
    return crc_old & 0xFFFFFFFF, crc_new & 0xFFFFFFFF


def _plan_new_blocks(
    reader: ufs.UfsReader, reps: Sequence[ufs.Replacement]
) -> tuple[list[ufs.Block], dict[int, bytes]]:
    """只重压命中块；其余块的压缩字节**原样搬**（设计稿 §9.10.3 实测可逐字节复现）。"""
    dirty: set[int] = set()
    for rep in reps:
        a, b = _hit_blocks(reader, rep.start, rep.end)
        dirty.update(range(a, b + 1))
    blocks: list[ufs.Block] = []
    payloads: dict[int, bytes] = {}
    for index, blk in enumerate(reader.blocks):
        if index not in dirty:
            blocks.append(ufs.Block(blk.uncompressed, blk.compressed, blk.flags))
            continue
        raw = _patched_block(reader, index, reps)
        comp = ufs.lz4hc(raw)
        if len(comp) >= len(raw):
            blocks.append(ufs.Block(len(raw), len(raw), 0))
            payloads[index] = raw
        else:
            blocks.append(ufs.Block(len(raw), len(comp), 3))
            payloads[index] = comp
    return blocks, payloads


@dataclass(slots=True)
class _Assembly:
    """blocksInfo / 数据起点 / 新文件大小 —— 干跑与真写共用同一份计算。"""

    new_blocks: list[ufs.Block]
    dirty_payloads: dict[int, bytes]
    blocks_info: bytes
    compressed_blocks_info: bytes
    data_start: int
    total: int
    #: Δ 专用：**新**块表里哪些块是原样拷贝 —— {新块下标: 旧块下标}。
    #: None = L1 那条链（新块与旧块一一对应，走旧行为）。
    verbatim_from: dict[int, int] | None = None


def _assemble(
    reader: ufs.UfsReader,
    new_blocks: list[ufs.Block],
    nodes: list[ufs.Node] | None = None,
) -> tuple[bytes, bytes, int, int]:
    blocks_info = ufs.serialize_blocks_info(
        reader.header.data_hash, new_blocks, reader.nodes if nodes is None else nodes
    )
    compressed = ufs.lz4hc(blocks_info)
    data_start = reader.header.blocks_info_offset + len(compressed)
    if reader.header.blocks_info_pad_start:
        data_start = _align16(data_start)
    total = data_start + sum(b.compressed for b in new_blocks)
    return blocks_info, compressed, data_start, total


def _write_container(
    reader: ufs.UfsReader, assembly: _Assembly, target: Path, new_blocks: list[ufs.Block]
) -> None:
    """按新块表拼出文件：脏块写重压结果，干净块从源文件**原样拷压缩字节**。"""
    header = ufs.serialize_header(
        reader.header.version,
        reader.header.version_player,
        reader.header.version_engine,
        assembly.total,
        len(assembly.compressed_blocks_info),
        len(assembly.blocks_info),
        reader.header.flags,
    )
    with target.open("wb") as out, reader.path.open("rb") as fin:
        out.write(header)
        out.write(assembly.compressed_blocks_info)
        gap = assembly.data_start - len(header) - len(assembly.compressed_blocks_info)
        if gap < 0:
            raise AssetError(
                f"数据起点 {assembly.data_start} 落在文件头里面（头 {len(header)} + blocksInfo "
                f"{len(assembly.compressed_blocks_info)}）",
                hint="blocksInfo 变长的幅度超出了原布局能容纳的范围",
            )
        if gap:
            out.write(b"\x00" * gap)
        for index, blk in enumerate(new_blocks):
            piece = assembly.dirty_payloads.get(index)
            if piece is None:
                # L1：新块与旧块一一对应；Δ：按 verbatim_from 找它原来在哪一块
                # （插入点之后的块在新表里的下标与旧表不同）。
                source = index if assembly.verbatim_from is None else assembly.verbatim_from[index]
                fin.seek(reader.block_foff[source])
                piece = fin.read(blk.compressed)
                if len(piece) != blk.compressed:
                    raise AssetError(
                        f"源文件第 {index} 个块只读到 {len(piece)} 字节，块表声明 {blk.compressed}",
                        hint="源文件可能被截断",
                    )
            out.write(piece)
    actual = target.stat().st_size
    if actual != assembly.total:
        raise AssetError(
            f"写出的文件是 {actual:,} 字节，头部声明 {assembly.total:,}",
            hint="拼装长度算错了；这个文件不要用",
        )


# --------------------------------------------------------------------------- #
# catalog：定位那一条 + 生成补丁
# --------------------------------------------------------------------------- #
def _entry_table(raw: Mapping[str, Any]) -> tuple[list[tuple[int, ...]], int, int]:
    """解 m_EntryDataString -> (entries, stride, fields)。"""
    blob = base64.b64decode(raw["m_EntryDataString"])
    (count,) = struct.unpack_from("<i", blob, 0)
    if count <= 0:
        raise AssetError("catalog 的 m_EntryDataString 是空的")
    stride = (len(blob) - 4) // count
    fields = stride // 4
    entries = [struct.unpack_from(f"<{fields}i", blob, 4 + i * stride) for i in range(count)]
    return entries, stride, fields


def _bundle_name_of(internal_id: str) -> str:
    return internal_id.replace("\\", "/").rsplit("/", 1)[-1]


def _json_span(blob: bytes, offset: int) -> tuple[int, int]:
    """从 extraDataOffset 起跳过 **SerializedType 头**，量出那条 UTF-16LE 紧凑 JSON 的 [起, 止)。

    实测布局（kb/09 §9.1 / 设计稿 §9.6 第 5 条；本机 77/77 条逐条验过）::

        [1B 类型标记 0x07][1B 程序集名长 + ASCII 名][1B 类名长 + ASCII 名]
        [i32 字节长][UTF-16LE 紧凑 JSON]

    ⚠️ **extraDataOffset 指的是整条 extra data 的开头，不是 JSON 的开头** ——
    units 那条是 62664，而 JSON 从 62821 起（差 157 B 的头）。按「偏移就是 JSON」去解，
    会在每一条上都错这 157 B。
    """
    if offset < 0 or offset + 4 > len(blob):
        raise AssetError(f"extraDataOffset {offset} 越出 m_ExtraDataString（{len(blob)} B）")
    pos = offset + 1
    pos += 1 + blob[pos]
    if pos >= len(blob):
        raise AssetError(f"extraDataOffset {offset} 的程序集名越界")
    pos += 1 + blob[pos]
    (byte_len,) = struct.unpack_from("<i", blob, pos)
    pos += 4
    start = pos
    end = start + byte_len
    if byte_len <= 0 or end > len(blob):
        raise AssetError(f"extraDataOffset {offset} 声明的 JSON 字节长 {byte_len} 越界")
    if blob[start : start + 2] != b"{\x00" or blob[end - 2 : end] != b"}\x00":
        raise AssetError(
            f"extraDataOffset {offset} 解出来的不是一条 JSON（两端不是花括号）",
            hint="m_ExtraDataString 的布局与 kb/09 §9.1 记的不符，先别写",
        )
    return start, end


def _is_catalog_bin(blob: bytes) -> bool:
    """这份 catalog 是不是 1.2.1 起那份**二进制**的（catalog.bin）。"""
    from ba_mod_maker.core.assets import catalog_bin  # noqa: PLC0415

    return bool(catalog_bin.looks_like_catalog_bin(blob))


def _catalog_entry_bin(catalog_path: str | Path, bundle_name: str) -> CatalogEntry:
    """二进制 catalog.bin 里那条包登记（★ 2026-10-15 真机修）。

    ⛔ 原来这条路只认 catalog.json：`Path(...).read_bytes().decode("utf-8")` 撞上 1.2.1 的
    catalog.bin（magic `42 89 e3 0d`）当场 `UnicodeDecodeError: byte 0x89 in position 1`
    —— 真机上就是这一句把「写回」挡住的（黑匣子 last-op.txt 原文）。
    二进制那份**已经有只读视图**（core/assets/catalog_bin.py 的 `bundle_entries()`），这里只借它。
    """
    from ba_mod_maker.core.assets import catalog_bin  # noqa: PLC0415

    path = Path(catalog_path)
    found = catalog_bin.bundle_entries(path)
    hit = found.get(bundle_name)
    if hit is None:
        raise AssetError(
            f"catalog.bin 里没有 {bundle_name!r} 这条登记（共 {len(found)} 条包）",
            hint="包名要写 catalog 里那条键（= bundle 文件名）",
        )
    return CatalogEntry(
        bundle_name=bundle_name,
        internal_id=str(hit.internal_id or ""),
        internal_id_index=-1,
        entry_index=-1,
        extra_offset=int(hit.location_offset or 0),
        crc=int(hit.crc),
        bundle_size=int(hit.bundle_size),
        json_bytes=0,
    )


def catalog_entry(catalog_path: str | Path, bundle_name: str) -> CatalogEntry:
    """在 catalog 里定位一条 bundle 的登记（JSON 与二进制两份都认）。"""
    blob_bytes = Path(catalog_path).read_bytes()
    if _is_catalog_bin(blob_bytes):
        return _catalog_entry_bin(catalog_path, bundle_name)
    raw = json.loads(blob_bytes.decode("utf-8"))
    internal_ids = raw["m_InternalIds"]
    hits = [
        index
        for index, value in enumerate(internal_ids)
        if isinstance(value, str) and _bundle_name_of(value) == bundle_name
    ]
    if len(hits) != 1:
        raise AssetError(f"m_InternalIds 里 {bundle_name!r} 命中 {len(hits)} 条（应为 1）：{hits}")
    internal_index = hits[0]
    entries, _stride, fields = _entry_table(raw)
    if fields < 5:
        raise AssetError(f"entry 只有 {fields} 个 int32，读不到 extraDataOffset")
    matched = [i for i, entry in enumerate(entries) if entry[0] == internal_index]
    if len(matched) != 1:
        raise AssetError(f"internalId #{internal_index} 对应 {len(matched)} 条 entry（应为 1）")
    entry_index = matched[0]
    extra_offset = entries[entry_index][4]
    blob = base64.b64decode(raw["m_ExtraDataString"])
    start, end = _json_span(blob, extra_offset)
    options = json.loads(blob[start:end].decode("utf-16-le"))
    return CatalogEntry(
        bundle_name=bundle_name,
        internal_id=str(internal_ids[internal_index]),
        internal_id_index=internal_index,
        entry_index=entry_index,
        extra_offset=extra_offset,
        crc=options.get("m_Crc"),
        bundle_size=options.get("m_BundleSize"),
        json_bytes=end - start,
    )


def catalog_patch(
    catalog_path: str | Path, bundle_name: str, *, crc: int, bundle_size: int
) -> CatalogPatch:
    """把**既有**包登记的 `m_Crc` / `m_BundleSize` 改成给的两个值，返回补丁。

    补丁对象带 `.before` / `.after`（两份整文件字节）与 `.crc_old` / `.crc_new` 那几个读数。

    这是本模块**唯一**的「改一条既有登记」入口（JSON 与二进制两份各一条路，按 magic 判别）。
    自带正控：**原样重编码必须逐字节回原样**，不成立就抛
    （⛔ 宁可不写，也不要把这份 catalog 重排坏）。

    ⚠ **它只算字节，不写盘、也不碰 `aa/catalog.hash`**：`aa/catalog.hash` 的内容是
    `hash128_hex(整份 catalog)`，与 catalog 是**一对**（`core/asset/new_asset.py` 的口径）。
    要两个文件一起管（一起记进同一条反向 Δ、一起写、一起撤）走 CLI：

        ba-mod-maker catalog crc --bundle <改好的包> [--write]

    （`mesh edit` / `anim apply` / `object set --write` 那些 `--catalog` 只写 catalog.bin 本身，
    它们的帮助文本里已如实写明这一条。）
    """
    return _build_catalog_patch(catalog_path, bundle_name, crc=crc, bundle_size=bundle_size)


def _build_catalog_patch(
    catalog_path: str | Path, bundle_name: str, *, crc: int, bundle_size: int
) -> CatalogPatch:
    """生成 catalog 补丁：改 m_Crc / m_BundleSize，并**全量重算** extraDataOffset。"""
def _build_catalog_patch_bin(
    path: Path, before: bytes, bundle_name: str, *, crc: int, bundle_size: int
) -> CatalogPatch:
    """二进制 catalog.bin 的补丁：走 `catalog_bin_write` 的**全量重编码**。

    正控与 JSON 那条一样严：**不改任何字段重编码必须逐字节回原样**（`roundtrip`），
    不成立就抛 —— ⛔ 宁可不写，也不要把这份 catalog 重排坏。
    """
    from ba_mod_maker.core.assets import catalog_bin_write as cbw  # noqa: PLC0415

    entry = _catalog_entry_bin(path, bundle_name)
    if cbw.roundtrip(before) != before:
        raise AssetError(
            "catalog.bin 原样重编码不是逐字节原样",
            hint="序列化口径与磁盘上那份不一致；宁可不写，也不要把 catalog 重排坏",
        )
    model = cbw.read_model(before)

    def mutate(inner) -> None:
        for item in inner.entries:
            if item.bundle_name == bundle_name and item.extra is not None:
                item.extra.crc = int(crc)
                item.extra.bundle_size = int(bundle_size)

    after = cbw.serialize(model, mutate=mutate)
    return CatalogPatch(
        path=path,
        before=before,
        after=after,
        entry=entry,
        crc_old=entry.crc,
        crc_new=int(crc),
        size_old=entry.bundle_size,
        size_new=int(bundle_size),
        offset_shift=len(after) - len(before),
        binary=True,
    )


def _build_catalog_patch(
    catalog_path: str | Path, bundle_name: str, *, crc: int, bundle_size: int
) -> CatalogPatch:
    """生成 catalog 补丁：改 m_Crc / m_BundleSize（JSON 与二进制两份各一条路）。"""
    path = Path(catalog_path)
    before = path.read_bytes()
    if _is_catalog_bin(before):
        return _build_catalog_patch_bin(path, before, bundle_name, crc=crc, bundle_size=bundle_size)
    raw = json.loads(before.decode("utf-8"))

    # 正控 1：整份重新序列化必须逐字节回到原样（实测成立；不成立就直接抛，绝不写）
    if json.dumps(raw, **_CATALOG_JSON_KW).encode("utf-8") != before:
        raise AssetError(
            "catalog.json 重新序列化不是逐字节原样",
            hint="序列化口径与磁盘上的那份不一致；宁可不写，也不要把 catalog 重排坏",
        )

    entry = catalog_entry(path, bundle_name)
    blob = base64.b64decode(raw["m_ExtraDataString"])
    start, end = _json_span(blob, entry.extra_offset)
    old_chunk = blob[start:end]
    options = json.loads(old_chunk.decode("utf-16-le"))
    crc_old = options.get("m_Crc")
    size_old = options.get("m_BundleSize")

    # 正控 2：单条 AssetBundleRequestOptions 重新序列化必须逐字节回到原样（实测成立）
    if json.dumps(options, **_CATALOG_JSON_KW).encode("utf-16-le") != old_chunk:
        raise AssetError(
            "catalog 里那条 AssetBundleRequestOptions 重新序列化不是逐字节原样",
            hint="字段顺序/格式与磁盘不符；不许整条重写",
        )

    options["m_Crc"] = int(crc)
    options["m_BundleSize"] = int(bundle_size)
    new_chunk = json.dumps(options, **_CATALOG_JSON_KW).encode("utf-16-le")
    shift = len(new_chunk) - (end - start)
    new_blob = bytearray(blob[:start] + new_chunk + blob[end:])

    if shift:
        # ① **声明长度**就在 JSON 前面 4 字节 —— 漏了它，Addressables 读这条 extra data 时
        #    会按旧长度读过头，把下一条的 SerializedType 头当作 JSON 的尾巴。
        #    （这一处是写用例时抓出来的真 bug：CRC 位数不变时 shift = 0，永远碰不到。）
        struct.pack_into("<i", new_blob, start - 4, len(new_chunk))
        # ② 再把后面每一条 extraDataOffset 平移（**全量重算**，⛔ 不许字符串替换）
        entry_blob = bytearray(base64.b64decode(raw["m_EntryDataString"]))
        entries, stride, _fields = _entry_table(raw)
        for index, row in enumerate(entries):
            if row[4] >= end:
                struct.pack_into("<i", entry_blob, 4 + index * stride + 4 * 4, row[4] + shift)
        raw["m_EntryDataString"] = base64.b64encode(bytes(entry_blob)).decode("ascii")
        # 机械复核：平移之后每一条 extraDataOffset 都还得指着一条**完整**的 JSON
        for index, _row in enumerate(entries):
            new_offset = struct.unpack_from("<i", entry_blob, 4 + index * stride + 16)[0]
            if new_offset >= 0:
                _json_span(bytes(new_blob), new_offset)

    raw["m_ExtraDataString"] = base64.b64encode(bytes(new_blob)).decode("ascii")
    after = json.dumps(raw, **_CATALOG_JSON_KW).encode("utf-8")
    return CatalogPatch(
        path=path,
        before=before,
        after=after,
        entry=entry,
        crc_old=crc_old,
        crc_new=int(crc),
        size_old=size_old,
        size_new=int(bundle_size),
        offset_shift=shift,
    )


# --------------------------------------------------------------------------- #
# 材质浮点：纯函数 + 借 UnityPy 序列化出「新对象字节」
# --------------------------------------------------------------------------- #
def material_float_names(tree: Mapping[str, Any]) -> tuple[str, ...]:
    """这个材质**真有**的 m_Floats 名（排序）。⛔ 不按常识补默认值（W4 §2.1 第 3 条）。"""
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        return ()
    floats = props.get("m_Floats")
    if not isinstance(floats, Sequence):
        return ()
    return tuple(sorted(str(entry[0]) for entry in floats if len(entry) >= 2))


def set_material_float(tree: Mapping[str, Any], param: str, value: float) -> dict[str, Any]:
    """**纯函数**：把一个浮点改成新值，返回新的 typetree（不动入参）。

    找不到这个参数就抛 KeyError —— 宁可报错，也不要往材质里凭空塞一个游戏没有的参数。
    """
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        raise KeyError("这个材质没有 m_SavedProperties")
    floats = props.get("m_Floats")
    if not isinstance(floats, Sequence):
        raise KeyError("这个材质没有 m_Floats")
    found = False
    new_floats: list[Any] = []
    for entry in floats:
        if len(entry) >= 2 and str(entry[0]) == param:
            pair: Any = [str(entry[0]), float(value)]
            with contextlib.suppress(TypeError):
                # 保住原容器的类型（tuple / list）
                pair = type(entry)(pair)
            new_floats.append(pair)
            found = True
        else:
            new_floats.append(entry)
    if not found:
        names = material_float_names(tree)
        raise KeyError(f"材质里没有浮点 {param!r}（共 {len(names)} 个，例如 {names[:6]}）")
    out = dict(tree)
    new_props = dict(props)
    new_props["m_Floats"] = new_floats
    out["m_SavedProperties"] = new_props
    return out


def _typetree_node(obj: Any) -> Any:
    """对象的 typetree 节点 —— 走**公开**字段，不戳 ObjectReader 的私有方法。"""
    node = getattr(getattr(obj, "serialized_type", None), "node", None)
    if node is None:
        # 兜底：UnityPy 的类型树池（ObjectReader 自己也是这么取的）。
        from UnityPy.helpers.Tpk import get_typetree_node  # noqa: PLC0415

        node = get_typetree_node(obj.class_id, obj.version)
    return node


def set_material_color(
    tree: Mapping[str, Any], param: str, rgba: Sequence[float]
) -> dict[str, Any]:
    """**纯函数**：把一个颜色（m_Colors 里的 r/g/b/a）改成新值。Color 是 4 个 float ⇒ 仍然等长。"""
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        raise KeyError("这个材质没有 m_SavedProperties")
    colors = props.get("m_Colors")
    if not isinstance(colors, Sequence):
        raise KeyError("这个材质没有 m_Colors")
    red, green, blue, alpha = (float(x) for x in rgba)
    found = False
    new_colors: list[Any] = []
    for entry in colors:
        if len(entry) >= 2 and str(entry[0]) == param:
            value = entry[1]
            if isinstance(value, Mapping):
                new_value: Any = dict(value)
                new_value.update({"r": red, "g": green, "b": blue, "a": alpha})
            else:
                new_value = value
                for channel, channel_value in (
                    ("r", red), ("g", green), ("b", blue), ("a", alpha)
                ):
                    with contextlib.suppress(AttributeError):
                        setattr(new_value, channel, channel_value)
            pair: Any = [str(entry[0]), new_value]
            with contextlib.suppress(TypeError):
                pair = type(entry)(pair)
            new_colors.append(pair)
            found = True
        else:
            new_colors.append(entry)
    if not found:
        raise KeyError(f"材质里没有颜色 {param!r}")
    out = dict(tree)
    new_props = dict(props)
    new_props["m_Colors"] = new_colors
    out["m_SavedProperties"] = new_props
    return out


def set_material_texture(
    tree: Mapping[str, Any], slot: str, pptr: Mapping[str, Any]
) -> dict[str, Any]:
    """**纯函数**：把某个贴图槽的 PPtr 换成另一个对象（i32 fileID + i64 pathID = 12 B ⇒ 仍等长）。

    这是 UnitV3 那类 Shader Graph 的**真正可见输入**（今天实测：它的序列化浮点/颜色不驱动画面，
    而 Layer_<hash> 贴图槽驱动）。W6「跨节点换贴图」换的是**像素字节**，这里换的是**引用**。
    """
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        raise KeyError("这个材质没有 m_SavedProperties")
    tex_envs = props.get("m_TexEnvs")
    if not isinstance(tex_envs, Sequence):
        raise KeyError("这个材质没有 m_TexEnvs")
    found = False
    new_envs: list[Any] = []
    for entry in tex_envs:
        if len(entry) >= 2 and str(entry[0]) == slot:
            value = entry[1]
            if not isinstance(value, Mapping):
                raise AssetError(
                    f"贴图槽 {slot!r} 的值不是 dict（typetree 形状与预期不符）",
                    hint="先看清楚 m_TexEnvs 的形状再写，⛔ 不许盲改",
                )
            new_value = dict(value)
            new_value["m_Texture"] = dict(pptr)
            pair: Any = [str(entry[0]), new_value]
            with contextlib.suppress(TypeError):
                pair = type(entry)(pair)
            new_envs.append(pair)
            found = True
        else:
            new_envs.append(entry)
    if not found:
        names = [str(e[0]) for e in tex_envs if len(e) >= 2]
        raise KeyError(f"材质里没有贴图槽 {slot!r}（有 {names}）")
    out = dict(tree)
    new_props = dict(props)
    new_props["m_TexEnvs"] = new_envs
    out["m_SavedProperties"] = new_props
    return out


def set_material_shader(tree: Mapping[str, Any], pptr: Mapping[str, Any]) -> dict[str, Any]:
    """**纯函数**：换掉材质的 shader 引用（PPtr = fileID i32 + pathID i64 = 12 B ⇒ 仍然等长）。

    ⚠️ 这是**结构级**改动，不是调参 —— 只在"判定这个材质到底被不被渲染器使用"时当探针用。
    """
    current = tree.get("m_Shader")
    if not isinstance(current, Mapping):
        raise KeyError("这个材质没有 m_Shader（PPtr）")
    out = dict(tree)
    out["m_Shader"] = dict(pptr)
    return out


def _read_texture_from_bytes(obj: Any, data: bytes, slot: str) -> tuple[int, int] | None:
    """从新字节里读回那个贴图槽的 PPtr（fileID, pathID）。"""
    props = _tree_from_bytes(obj, data).get("m_SavedProperties") or {}
    for entry in props.get("m_TexEnvs") or ():
        if len(entry) >= 2 and str(entry[0]) == slot:
            value = entry[1]
            pptr = value.get("m_Texture") if isinstance(value, Mapping) else None
            if isinstance(pptr, Mapping):
                return int(pptr.get("m_FileID", 0) or 0), int(pptr.get("m_PathID", 0) or 0)
            return None
    return None


def _tree_from_bytes(obj: Any, data: bytes) -> Mapping[str, Any]:
    """把一串对象字节按**同一个** typetree 解回来（语义自证用）。"""
    # UnityPy 是可选依赖（Blender 侧自带一份），延迟 import 是本模块的设计。
    from UnityPy.helpers.TypeTreeHelper import read_typetree  # noqa: PLC0415
    from UnityPy.streams import EndianBinaryReader  # noqa: PLC0415

    node = _typetree_node(obj)
    reader = EndianBinaryReader(data, endian=obj.reader.endian)
    return read_typetree(
        node,
        reader,
        as_dict=True,
        byte_size=len(data),
        check_read=False,
        assetsfile=obj.assets_file,
    )


def _read_float_from_bytes(obj: Any, data: bytes, param: str) -> float | None:
    """从新字节里读回那个浮点。"""
    props = _tree_from_bytes(obj, data).get("m_SavedProperties") or {}
    for entry in props.get("m_Floats") or ():
        if len(entry) >= 2 and str(entry[0]) == param:
            return float(entry[1])
    return None


def _read_color_from_bytes(
    obj: Any, data: bytes, param: str
) -> tuple[float, float, float, float] | None:
    """从新字节里读回那个颜色（r/g/b/a）。"""
    props = _tree_from_bytes(obj, data).get("m_SavedProperties") or {}
    for entry in props.get("m_Colors") or ():
        if len(entry) >= 2 and str(entry[0]) == param:
            value = entry[1]
            if isinstance(value, Mapping):
                return tuple(float(value.get(k, 0.0)) for k in ("r", "g", "b", "a"))  # type: ignore[return-value]
            return tuple(float(getattr(value, k, 0.0)) for k in ("r", "g", "b", "a"))  # type: ignore[return-value]
    return None


def build_material_change(
    source: Any,
    path_id: int,
    *,
    param: str | None = None,
    value: float | None = None,
    floats: Mapping[str, float] | None = None,
    colors: Mapping[str, Sequence[float]] | None = None,
    textures: Mapping[str, Mapping[str, Any]] | None = None,
    shader: Mapping[str, Any] | None = None,
    name: str = "",
) -> ObjectChange:
    """把「某个材质上的若干浮点 / 颜色改成新值」变成**等长**的新对象字节。

    source 是 asset_kernel.unitypy_source.AssetSource（要它已经打开 UnityPy 那一侧）。
    兼容两种叫法：@param= / value=@（一个浮点，⑤ 面板走这条）或 @floats= / colors=@。
    四条自证都在这里做，任何一条不成立都抛（⛔ 不许硬写）：
    * 不改动的 typetree 往返必须**逐字节相同**（L1 的前提）；
    * 改完之后新字节数必须等于原字节数（F7：delta == 0）；
    * 从新字节**读回来**的每个浮点必须等于新值；
    * 从新字节读回来的每个颜色必须等于新值。
    """
    wanted_floats: dict[str, float] = {str(k): float(v) for k, v in (floats or {}).items()}
    if param is not None:
        if value is None:
            raise ValueError("给了 param 就必须给 value")
        wanted_floats[str(param)] = float(value)
    wanted_colors: dict[str, tuple[float, ...]] = {
        str(k): tuple(float(x) for x in v) for k, v in (colors or {}).items()
    }
    wanted_textures: dict[str, Mapping[str, Any]] = {
        str(k): dict(v) for k, v in (textures or {}).items()
    }
    if not wanted_floats and not wanted_colors and not wanted_textures and shader is None:
        raise ValueError("至少要改一个浮点、颜色、贴图槽或 shader")

    obj = source.object_at(path_id)
    if obj is None:
        raise KeyError(f"这个包里没有 pathID={path_id} 的对象")
    original = obj.get_raw_data()
    tree = obj.read_typetree()
    if obj.save_typetree(tree) != original:
        raise AssetError(
            f"pathID={path_id} 的 typetree 往返不是逐字节相同",
            hint="这个对象的序列化不可复现 ⇒ 等长覆盖会改坏别的字段，本单不许写",
        )
    target_name = name or str(tree.get("m_Name") or f"<material {path_id}>")
    new_tree: Mapping[str, Any] = tree
    for key, new_value in wanted_floats.items():
        new_tree = set_material_float(new_tree, key, new_value)
    for key, rgba in wanted_colors.items():
        new_tree = set_material_color(new_tree, key, rgba)
    for slot, pptr in wanted_textures.items():
        new_tree = set_material_texture(new_tree, slot, pptr)
    if shader is not None:
        new_tree = set_material_shader(new_tree, shader)
    payload = obj.save_typetree(new_tree)
    if len(payload) != len(original):
        raise AssetError(
            f"改 {sorted(wanted_floats) + sorted(wanted_colors)} 之后对象长度 "
            f"{len(original):,} → {len(payload):,} B（不是 L1）",
            hint="本单只支持等长（L1）；长度变了必须报错退出（工单 §7 坑 5）",
        )
    for key, new_value in wanted_floats.items():
        want = struct.unpack("<f", struct.pack("<f", new_value))[0]
        got = _read_float_from_bytes(obj, payload, key)
        if got != want:
            raise AssetError(
                f"写出来的字节读回去 {key} = {got!r}，期望 {want!r}",
                hint="序列化没把新值写进去 —— 这一版不许写回",
            )
    for key, rgba in wanted_colors.items():
        want_color = tuple(struct.unpack("<f", struct.pack("<f", x))[0] for x in rgba)
        got_color = _read_color_from_bytes(obj, payload, key)
        if got_color != want_color:
            raise AssetError(
                f"写出来的字节读回去 {key} = {got_color!r}，期望 {want_color!r}",
                hint="序列化没把新颜色写进去 —— 这一版不许写回",
            )
    for slot, pptr in wanted_textures.items():
        got_pptr = _read_texture_from_bytes(obj, payload, slot)
        want_pptr = (int(pptr.get("m_FileID", 0) or 0), int(pptr.get("m_PathID", 0) or 0))
        if got_pptr != want_pptr:
            raise AssetError(
                f"写出来的字节读回去 {slot} 的 PPtr = {got_pptr}，期望 {want_pptr}",
                hint="序列化没把新引用写进去 —— 这一版不许写回",
            )
    return ObjectChange(path_id=path_id, payload=payload, name=target_name, type_name="Material")


# --------------------------------------------------------------------------- #
# 计划（干跑与真写共用）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _InternalPlan:
    plan: WritebackPlan
    replication_of: list[ufs.Replacement]
    assembly: _Assembly
    object_ranges: list[tuple[int, int]]
    serialized_node_path: str
    #: node[0]（对象数据节点）在解压流里的区间 —— G2 判据（写回后必须一个字节都没动）。
    serialized_range: tuple[int, int] = (0, 0)
    #: "objects" = write_objects（改的就是 node[0]）；"stream" = write_stream（改 node[1]）；
    #: "delta" = write_delta（变长：散列替换 + 自由块划分）。
    kind: str = "objects"
    #: Δ 专用：块划分 / 插入点 / 新节点表 —— 写后复核的 H2 / H3 从这里取口径。
    delta: _DeltaLayout | None = None


@dataclass(slots=True)
class _ReplacementPlan:
    """_plan_replacements 的产出：对象区间与流式区间**共用**同一段规划与拼装。"""

    plan: WritebackPlan
    assembly: _Assembly
    rows: list[PlanRow]
    reps: list[ufs.Replacement]
    ranges: list[tuple[int, int]]


def _serialized_node(reader: ufs.UfsReader) -> ufs.Node:
    """bundle 里装 SerializedFile 的那个节点（units 实测 = node[0] = CAB-<hash>）。"""
    if not reader.nodes:
        raise AssetError("容器里一个节点都没有")
    node = reader.nodes[0]
    if node.path.endswith(".resS"):
        raise AssetError(
            f"node[0] 是 .resS（{node.path}）——不是本单的 bundle 形态",
            hint="bundle 的 node[0] 应当是 CAB-<hash>；node[1] 才是 .resS（W6 换贴图用）",
        )
    return node


def _plan(
    reader: ufs.UfsReader,
    changes: Sequence[ObjectChange],
    *,
    catalog_path: Path | None,
) -> _InternalPlan:
    if not changes:
        raise AssetError("没有任何要改的对象", hint="至少要给一个 ObjectChange")
    node = _serialized_node(reader)
    info = ufs.read_serialized_file(reader, node)
    by_id = {pid: (stored, size) for _i, pid, stored, size, _tid in info.objects}

    entries: list[tuple[PlanRow, ufs.Replacement, tuple[int, int]]] = []
    for change in changes:
        found = by_id.get(change.path_id)
        if found is None:
            raise KeyError(f"{node.path} 的对象表里没有 pathID={change.path_id}")
        stored, size = found
        start = node.offset + info.data_offset + stored
        end = start + size
        a, b = _hit_blocks(reader, start, end)
        entries.append(
            (
                PlanRow(
                    path_id=change.path_id,
                    name=change.name or f"<{change.path_id}>",
                    type_name=change.type_name,
                    old_bytes=size,
                    new_bytes=len(change.payload),
                    level=LEVEL_EQUAL if len(change.payload) == size else LEVEL_GROW,
                    block_first=a,
                    block_last=b,
                    stream_start=start,
                ),
                ufs.Replacement(start, end, change.payload),
                (start, end),
            )
        )
    planned = _plan_replacements(
        reader,
        entries,
        catalog_path=catalog_path,
        meta_lo=node.offset,
        meta_hi=node.offset + ufs.SF_HEADER_SIZE + info.metadata_size,
    )
    return _InternalPlan(
        plan=planned.plan,
        replication_of=planned.reps,
        assembly=planned.assembly,
        object_ranges=planned.ranges,
        serialized_node_path=node.path,
        serialized_range=(node.offset, node.offset + node.size),
        kind="objects",
    )


def _serialized_node_info(reader: ufs.UfsReader) -> tuple[ufs.Node, ufs.SerializedFileInfo]:
    """node[0]（装对象数据那个节点）与它的 SerializedFile 摘要。"""
    node = _serialized_node(reader)
    return node, ufs.read_serialized_file(reader, node)


def _stream_node(reader: ufs.UfsReader, node_path: str) -> ufs.Node:
    """按 **basename** 找流式节点（⛔ 不按当前 bundle 名拼哈希：工单 §7 坑 4）。"""
    base = _node_basename(node_path)
    hits = [n for n in reader.nodes if _node_basename(n.path) == base]
    if len(hits) != 1:
        names = [_node_basename(n.path) for n in reader.nodes]
        raise AssetError(
            f"按 basename {base!r} 命中 {len(hits)} 个节点（应为 1）：{names}",
            hint="一个包里有多个 .resS 时不许猜（会改错图；工单 §7 坑 4）",
        )
    return hits[0]


def _check_stream_change_against_object(
    reader: ufs.UfsReader,
    node0: ufs.Node,
    info: ufs.SerializedFileInfo,
    by_id: Mapping[int, tuple[int, int]],
    change: StreamChange,
) -> None:
    """复核：StreamChange 的 (path, offset, size) 与该 pathID 对象里的 m_StreamData 一致。

    防的是「区间属于另一张图 / 手写错了」—— 错区间会**静默改坏别的图**的像素。
    """
    found = by_id.get(change.owner_path_id)
    if found is None:
        raise AssetError(
            f"owner_path_id={change.owner_path_id} 不在 {node0.path} 的对象表里",
            hint="StreamChange 要带它自己那张贴图的 pathID，才复核得了",
        )
    stored, size = found
    obj_bytes = bytes(reader.read_range(node0.offset + info.data_offset + stored, size))
    current = _stream_info_of_object(obj_bytes, path_id=change.owner_path_id)
    if (
        _node_basename(current.path) != _node_basename(change.node_path)
        or current.offset != change.offset
        or current.size != change.old_size
    ):
        raise AssetError(
            f"StreamChange 与 pathID={change.owner_path_id} 的 m_StreamData 不符："
            f"给的是 {_node_basename(change.node_path)}@{change.offset}+{change.old_size}，"
            f"对象里是 {_node_basename(current.path)}@{current.offset}+{current.size}",
            hint="区间写错会静默改坏另一张图的像素 —— 先 build_stream_change 再写",
        )


def _plan_replacements(
    reader: ufs.UfsReader,
    entries: list[tuple[PlanRow, ufs.Replacement, tuple[int, int]]],
    *,
    catalog_path: Path | None,
    meta_lo: int,
    meta_hi: int,
) -> _ReplacementPlan:
    """**对象区间与流式区间完全通用的一段**：重叠守卫 + 元数据守卫 + 双 CRC + 重压 + 拼装。

    _hit_blocks / _dual_crc / _patch_bytes 吃的是**解压流绝对偏移**，所以
    「对象在 node[0]」「贴图在 node[1]」对这一段没有区别。

    ⚠️ 一处相对 W5 的修正：entries 按 start **成对排序**。W5 只排了 reps、ranges 留在原顺序，
    于是「多个改动 + 输入乱序」时 F2 会把区间和载荷配错对（两者等长时还会静默配对）。
    单改动的行为一字未变。
    """
    entries = sorted(entries, key=lambda item: item[1].start)
    reps = [item[1] for item in entries]
    rows = [item[0] for item in entries]
    ranges = [item[2] for item in entries]
    for earlier, later in zip(reps, reps[1:], strict=False):
        if later.start < earlier.end:
            raise AssetError(
                f"替换区间重叠：[{earlier.start}, {earlier.end}) 与 [{later.start}, {later.end})",
                hint="静默重叠会产出一个读不回来的包（W1 工单 §7 坑 5）",
            )
    for rep in reps:
        if rep.start < meta_hi and rep.end > meta_lo:
            raise AssetError(
                f"替换区间压到元数据区上了：[{rep.start}, {rep.end}) 撞 [{meta_lo}, {meta_hi})",
                hint="SerializedFile 的元数据区不许覆盖",
            )

    crc_old, crc_new = _dual_crc(reader, reps)
    new_blocks, dirty_payloads = _plan_new_blocks(reader, reps)
    blocks_info, compressed_bi, data_start, total = _assemble(reader, new_blocks)
    runs = _merge_runs([(r.block_first, r.block_last) for r in rows])
    hit_blocks = sum(b - a + 1 for a, b in runs)

    blocked: list[str] = []
    grows = [row for row in rows if row.level != LEVEL_EQUAL]
    if grows:
        blocked.append(f"有 {len(grows)} 处长度不符（新字节数 != 原字节数 ⇒ 级别不是 L1）")

    catalog_name = reader.path.name
    crc_catalog_old: int | None = None
    size_catalog_old: int | None = None
    extra_offset: int | None = None
    if catalog_path is not None:
        entry = catalog_entry(catalog_path, catalog_name)
        crc_catalog_old = entry.crc
        size_catalog_old = entry.bundle_size
        extra_offset = entry.extra_offset
        if entry.crc is not None and entry.crc != crc_old:
            blocked.append(
                f"磁盘 CRC {crc_old} != catalog 里写的 m_Crc {entry.crc}"
                "（这个包已经被别的工具改过，先别写）"
            )
        if entry.bundle_size is not None and entry.bundle_size != reader.file_size:
            blocked.append(
                f"磁盘大小 {reader.file_size:,} != catalog 里写的 "
                f"m_BundleSize {entry.bundle_size:,}"
            )

    plan = WritebackPlan(
        bundle=reader.path,
        bundle_name=catalog_name,
        rows=tuple(rows),
        block_runs=runs,
        hit_blocks=hit_blocks,
        eta_seconds=hit_blocks * RECOMPRESS_SECONDS_PER_BLOCK + FULL_CRC_SECONDS,
        old_file_size=reader.file_size,
        new_file_size=total,
        disk_crc=crc_old,
        catalog_bundle_name=catalog_name,
        catalog_crc_old=crc_catalog_old,
        catalog_crc_new=crc_new,
        catalog_size_old=size_catalog_old,
        catalog_extra_offset=extra_offset,
        blocked_reason="；".join(blocked),
    )
    assembly = _Assembly(
        new_blocks=new_blocks,
        dirty_payloads=dirty_payloads,
        blocks_info=blocks_info,
        compressed_blocks_info=compressed_bi,
        data_start=data_start,
        total=total,
    )
    return _ReplacementPlan(plan=plan, assembly=assembly, rows=rows, reps=reps, ranges=ranges)


def build_stream_change(reader: ufs.UfsReader, path_id: int, payload: bytes) -> StreamChange:
    """按 Texture2D 的 pathID 造一个**等长** StreamChange（G4：对象本身一个字节都不改）。

    定位链：node[0] 的 SerializedFile 对象表 -> 该 pathID 的 (stored, size) -> 读对象字节
    -> 取 m_StreamData。len(payload) != m_StreamData.size 时抛 AssetError，消息含「长度不符」。

    ⚠️ reader 可以是 ufs.UfsReader，也可以是 asset_kernel.unitypy_source.AssetSource
    （它自带 .reader 与 read_typetree）—— 后者多一层**权威口径**复核：UnityPy 解出来的
    m_StreamData 必须与对象字节里实测的那份逐字段相同，否则抛（两把尺子互相印证）。
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError(f"payload 必须是字节，收到 {type(payload).__name__}")
    data = bytes(payload)
    container: ufs.UfsReader = getattr(reader, "reader", reader)
    node, info = _serialized_node_info(container)
    found: tuple[int, int] | None = None
    for _index, pid, stored, size, _tid in info.objects:
        if pid == path_id:
            found = (stored, int(size))
            break
    if found is None:
        raise KeyError(f"{node.path} 的对象表里没有 pathID={path_id}")
    stored, size = found
    obj_bytes = bytes(container.read_range(node.offset + info.data_offset + stored, size))
    if len(obj_bytes) != size:
        raise AssetError(
            f"pathID={path_id} 的对象只读到 {len(obj_bytes):,} B，对象表里声明 {size:,} B"
        )
    stream = _stream_info_of_object(obj_bytes, path_id=path_id)
    tree_reader = getattr(reader, "read_typetree", None)
    if callable(tree_reader):
        tree = tree_reader(path_id)
        authoritative = parse_streaming_info(tree.get("m_StreamData"))
        if authoritative is None or not authoritative.is_streamed:
            raise AssetError(f"pathID={path_id} 的 typetree 里没有可用的 m_StreamData")
        if (authoritative.path, authoritative.offset, authoritative.size) != (
            stream.path,
            stream.offset,
            stream.size,
        ):
            raise AssetError(
                "typetree 与对象字节里解出来的 m_StreamData 不一致："
                f"typetree={authoritative.describe()}；字节={stream.describe()}",
                hint="两把尺子必须互相印证；不一致就不许写（⛔ 不猜）",
            )
        stream = authoritative
    if len(data) != stream.size:
        raise AssetError(
            f"长度不符：新字节 {len(data):,} B != 原 {stream.size:,} B"
            f"（差 {len(data) - stream.size:+,} B）",
            hint="本单只做等长（L1）：格式 / 分辨率 / mip 数都不变时字节数才相等"
            "（DXT1 / DXT5 的字节数由这三者唯一决定）",
        )
    return StreamChange(
        node_path=stream.path,
        offset=stream.offset,
        old_size=stream.size,
        payload=data,
        owner_path_id=int(path_id),
        type_name="Texture2D",
    )


def _plan_stream(
    reader: ufs.UfsReader,
    changes: Sequence[StreamChange],
    *,
    catalog_path: Path | None,
) -> _InternalPlan:
    """流式区间（node[1] = CAB-*.resS）的规划：绝对区间 = 目标节点.offset + change.offset。"""
    if not changes:
        raise AssetError("没有任何要改的流式区间", hint="至少要给一个 StreamChange")
    node0, info = _serialized_node_info(reader)
    by_id = {pid: (stored, size) for _i, pid, stored, size, _tid in info.objects}
    entries: list[tuple[PlanRow, ufs.Replacement, tuple[int, int]]] = []
    for change in changes:
        node = _stream_node(reader, change.node_path)
        if node.path == node0.path:
            raise AssetError(
                f"目标节点 {node.path} 就是装对象数据的 node[0] —— ⛔ 本单只改 node[1]（.resS）",
                hint="对象数据一个字节都不许动（工单 §5 / G2）",
            )
        if change.offset < 0 or change.old_size <= 0:
            raise AssetError(
                f"流式区间不合法：offset={change.offset} old_size={change.old_size}"
            )
        if change.offset + change.old_size > node.size:
            raise AssetError(
                f"流式区间越界：节点内 [{change.offset:,}, "
                f"{change.offset + change.old_size:,}) 超出 {node.path}（{node.size:,} B）",
                hint="offset 是**节点内**相对偏移（== m_StreamData.offset），不是文件偏移",
            )
        if change.owner_path_id:
            _check_stream_change_against_object(reader, node0, info, by_id, change)
        start = node.offset + change.offset
        end = start + change.old_size
        a, b = _hit_blocks(reader, start, end)
        entries.append(
            (
                PlanRow(
                    path_id=change.owner_path_id,
                    name=change.name or f"<{change.owner_path_id or 'stream'}>",
                    type_name=change.type_name,
                    old_bytes=change.old_size,
                    new_bytes=len(change.payload),
                    level=LEVEL_EQUAL if len(change.payload) == change.old_size else LEVEL_GROW,
                    block_first=a,
                    block_last=b,
                    stream_start=start,
                ),
                ufs.Replacement(start, end, change.payload),
                (start, end),
            )
        )
    planned = _plan_replacements(
        reader,
        entries,
        catalog_path=catalog_path,
        meta_lo=node0.offset,
        meta_hi=node0.offset + ufs.SF_HEADER_SIZE + info.metadata_size,
    )
    return _InternalPlan(
        plan=planned.plan,
        replication_of=planned.reps,
        assembly=planned.assembly,
        object_ranges=planned.ranges,
        serialized_node_path=node0.path,
        serialized_range=(node0.offset, node0.offset + node0.size),
        kind="stream",
    )


def plan_stream_writeback(
    bundle_path: str | Path,
    changes: Sequence[StreamChange],
    *,
    catalog_path: str | Path | None = None,
) -> WritebackPlan:
    """**干跑**：算出流式区间替换的 F1 那一行，一个字节都不写。"""
    with ufs.UfsReader(bundle_path) as reader:
        internal = _plan_stream(
            reader,
            changes,
            catalog_path=Path(catalog_path) if catalog_path is not None else None,
        )
        return internal.plan


def plan_writeback(
    bundle_path: str | Path,
    changes: Sequence[ObjectChange],
    *,
    catalog_path: str | Path | None = None,
) -> WritebackPlan:
    """**干跑**：算出 F1 那一行要的每个数，一个字节都不写。"""
    with ufs.UfsReader(bundle_path) as reader:
        internal = _plan(
            reader,
            changes,
            catalog_path=Path(catalog_path) if catalog_path is not None else None,
        )
        return internal.plan


# --------------------------------------------------------------------------- #
# Δ（变长）写回链：搬到尾部 + 流式节点尾追加 + **解压流块划分自由选**
# --------------------------------------------------------------------------- #
# 为什么不是"把后半段重压一遍"（设计稿 §9.10.4）
#
# 块表每一项是 (uncompressed, compressed, flags) —— **块就是解压流的连续切片**，
# 切在哪里是**自由**的，不是原包定死的。于是「在插入点 p 新开一个块边界」把原块 k
# 拆成 [S_k, p) 与 [p, S_{k+1})，中间插承载新字节的块：
#
#   原块 k-1 | 头 [S_k,p) | 新字节 | 尾 [p,S_{k+1}) | 原块 k+1 | ... | 原块 n
#   原样拷贝    重压        重压       重压              原样拷贝        原样拷贝
#
# ⇒ S_{k+1} 之后的块**解压内容与块边界都没变**（只是解压起点平移了 Δ），
#   它们的压缩字节可以**逐字节原样拷贝** —— 只有 3 个块要重新压。
#   这就是「旧产品 splice 要 9.5 分钟、本设计毫秒级」的那条差别（设计稿 §9.10.3/4）。
#
# 节点表这边只动两处（工单 §2 ①/③，口径照 ufs.py 的 _patch_metadata / _recompute_nodes）：
#   ① 对象长度变（变长/变短都算）-> node[0].size += Δ、node[1].offset += Δ、头 [24:32]
#      的 file_size += Δ（⛔ 不 import 那两个私有函数，也不改 ufs.py；这里自己算）；
#   ③ 流式追加 -> 目标节点.size += Δ（它自己的尾巴长了），它后面的节点才平移。


@dataclass(frozen=True, slots=True)
class ObjectGrowth:
    """一个**长度变了**的对象：新字节一律搬到 node[0] 的尾部（8 字节对齐），只改它那一条表项。

    * **变长 / 变短都走这条**（口径统一：对象搬到尾部、旧字节留成死区、节点照样增长、
      byte_size 按 payload 的**实际**长度写 —— 变短就写小）；
    * **等长**请走 W6 的 ObjectChange（那条更便宜：原地覆盖，对象表与节点表都不动）。

    ⚠ 为什么变短也走追加而不是原地：① 机制统一（一条链、一套用例）；
    ② 追加路径才有「8 字节对齐」这个点（设计稿 §9.9 的硬约束），H4 要检它。
    """

    path_id: int
    payload: bytes
    name: str = ""
    type_name: str = ""


@dataclass(frozen=True, slots=True)
class NewObject:
    """往包里**新增**一个对象（新 pathID）—— W8 的唯一新机制。

    与 ObjectGrowth 的差别：ObjectGrowth 改的是**已经在表里**的对象（pathID 不变，
    只改它那一条表项的 byte_start/byte_size）；NewObject 是**表里还没有**的对象，
    要 ① 在对象表里**插一条**（保持 pathID 严格升序）② 把 payload 追加到 node[0] 尾部。

    * `path_id`  ⛔ 不许与包内任何已有 pathID 撞（plan 阶段查重，撞了就抛）；
      也**不许**与同一次写回里别的 NewObject 撞。
    * `type_id`  对象表里的 **typeID**（类型表下标，⛔ 不是 classID）。
    * `payload`  该对象的序列化字节（byte_size 按它的**实际**长度写）。
    * `name` / `type_name`  只是判据一行跟写后复核里的**可读名**，不参与字节。

    ⚠ 新对象的 byteStart 一律 **8 字节对齐**（真件实测：表里 4,000/4,000 个 byte_start
    都是 8 的倍数）—— 对齐填充是 payload **前面**的零字节，byteStart 指向 payload 本体。
    """

    path_id: int
    type_id: int
    payload: bytes
    name: str = ""
    type_name: str = ""


#: 流式数据（.resS）里每一条的起点对齐。**实测**（_work/w7_probe_delta.py，真件 units 包）：
#: 抽 200 张流式贴图的 m_StreamData.offset，**200/200 都是 16 的倍数**
#: （W6 那两张也一样：2,533,061,664 / 2,358,384,112）。所以节点尾追加也按 16 对齐。
#: ⚠ 真件里 node[1].size = 2,905,277,264 本来就 16 对齐 ⇒ 本例填充 0 B，读数不变；
#: 这条只是把"追加一定落在这条实测规律上"变成机械保证。
STREAM_APPEND_ALIGN = 16


@dataclass(frozen=True, slots=True)
class StreamAppend:
    """把 payload 追加到某个流式节点（.resS）的**尾部**，并返回它的新节点内偏移。"""

    node_path: str
    payload: bytes
    owner_path_id: int = 0


@dataclass(frozen=True, slots=True)
class DeltaChange:
    """一次 Δ 写回要做的四类改动（可并存 —— 但必须落在**同一次**写回里）。

    工单 §7 坑 2：Texture2D 的四个字段与 .resS 里那一段新像素必须一起落位；
    「只改 m_TextureFormat」= 必坏。

    W8 的 `added`（**新 pathID**）与 `growths`（改已有对象）可以同时给：
    两条链都往 node[0] 尾部追加，**顺序由插入点排序 + 稳定序**保证一致
    （见 _plan_delta 的 ①/④ 两段）。J3 的「新对象进 AssetBundle 的 m_PreloadTable」
    就是 growth（改 pathID=1 那个对象）+ added（新对象）落在同一次 DeltaChange 里。
    """

    inplace: tuple[ObjectChange, ...] = ()
    growths: tuple[ObjectGrowth, ...] = ()
    appends: tuple[StreamAppend, ...] = ()
    #: W8：**表里还没有**的对象（新 pathID）—— 对象表插条目 + payload 追加到 node[0] 尾部。
    added: tuple[NewObject, ...] = ()


@dataclass(slots=True)
class _Walk:
    """_delta_block_walk 的产出：新块表 + 谁重压 / 谁原样拷贝 + 双 CRC。"""

    new_blocks: list[ufs.Block]
    dirty_payloads: dict[int, bytes]
    verbatim_from: dict[int, int]
    recompressed: list[int]
    insert_block: list[int]
    old_to_new: dict[int, list[int]]
    crc_old: int
    crc_new: int


@dataclass(slots=True)
class _DeltaLayout:
    """Δ 计划里写后复核要用的每一件事（H2 / H3 的全套口径）。"""

    inserts: list[tuple[int, bytes]]
    owners: list[ufs.Node]
    reps: list[ufs.Replacement]
    new_nodes: list[ufs.Node]
    walk: _Walk
    sf_growth: int
    append_offsets: list[tuple[str, int, int]]
    #: 每个**变了 size** 的节点 —— [(节点 basename, size 增量)]（写后复核 H2 的输入）。
    node_growth: list[tuple[str, int]]


def _merge_owners(owners: Sequence[object], sources: Sequence[tuple[int, ...]]) -> list[object]:
    """把 owners 按**同一份**合并分组收敛（`_delta_nodes` 要求 inserts 与 owners 一一对应）。

    口径：同坐标的插入只有一个 owner（它们本来就指向同一个节点），所以取该组第一个即可；
    顺带做一条断言 —— 同组内 owner 不一致就抛（那说明插入点分组分错了）。
    """
    out: list[object] = []
    for group in sources:
        first = owners[group[0]]
        for source in group[1:]:
            if owners[source] is not first:
                raise AssetError(
                    f"同坐标插入的第 {source} 条 owner 与第 {group[0]} 条不同 —— 串接的前提被破坏",
                    hint="插入点分组算错了；这种计划不要写",
                )
        out.append(first)
    return out


def _merge_inserts(
    inserts: Sequence[tuple[int, bytes]],
) -> tuple[list[tuple[int, bytes]], list[tuple[int, ...]]]:
    """把**同坐标**的插入条目**按序串接**成一条（块划分的输入口径）。

    2026-10-06（N1 现场）：`_delta_block_walk` 对**每一条**插入各开一个新块，于是
    194 个对象的「加一批对象」会被碎成 **388 个插入点 / 392 块重压**（含 196 个 24 B 的
    纯元数据块，<4 KiB 的小块 361 个、块尺寸档位 83 种）。而真件的块网格是**均匀 128 KiB**。
    实测：同坐标串接之后 **重压块 392 → 6、小块 361 → 0、档位 83 → 8**，
    新解压流总长与插入字节总数**逐字节不变**（`_work/n1c-block-table.py`）。

    ⛔ 只串接**坐标相同**的条目：坐标一旦不同就不能合并（否则旧流里两段之间的字节顺序会被改掉）。
    ⛔ 顺序必须保持原样（稳定合并）—— 否则对象表会不再严格升序。

    :return: (合并后的插入表, 每条合并结果原本由哪些下标组成)
    """
    groups: list[tuple[int, bytes]] = []
    sources: list[list[int]] = []
    for index, (point, data) in enumerate(inserts):
        if groups and groups[-1][0] == point:
            groups[-1] = (point, groups[-1][1] + data)
            sources[-1].append(index)
        else:
            groups.append((point, data))
            sources.append([index])
    return groups, [tuple(row) for row in sources]


def _delta_block_walk(
    reader: ufs.UfsReader,
    reps: Sequence[ufs.Replacement],
    inserts: Sequence[tuple[int, bytes]],
) -> _Walk:
    """同坐标先串接，再交给块划分（见 :func:`_merge_inserts`）。"""
    groups, _sources = _merge_inserts(inserts)
    return _delta_block_walk_groups(reader, reps, groups)


def _delta_block_walk_groups(
    reader: ufs.UfsReader,
    reps: Sequence[ufs.Replacement],
    inserts: Sequence[tuple[int, bytes]],
) -> _Walk:
    """按「插入点处新开块边界」重排块表：只重压受影响的块，其余**原样拷贝压缩字节**。

    一趟同时算出 crc_old（旧流）与 crc_new（新流）—— 判据 F4 要的是**真值**，
    不是估算（与 W5 的 _dual_crc 同一个口径）。
    """
    starts = reader.block_dstart
    count = len(reader.blocks)
    reps_by_block: dict[int, list[ufs.Replacement]] = {}
    for rep in reps:
        first, last = _hit_blocks(reader, rep.start, rep.end)
        for index in range(first, last + 1):
            reps_by_block.setdefault(index, []).append(rep)

    new_blocks: list[ufs.Block] = []
    dirty: dict[int, bytes] = {}
    verbatim: dict[int, int] = {}
    recompressed: list[int] = []
    insert_block: list[int] = []
    old_to_new: dict[int, list[int]] = {}
    state = {"crc_old": 0, "crc_new": 0, "old": 0, "new": 0}

    def _bump(before: bytes, after: bytes) -> None:
        state["crc_old"] = zlib.crc32(before, state["crc_old"])
        state["crc_new"] = zlib.crc32(after, state["crc_new"])
        state["old"] += len(before)
        state["new"] += len(after)

    def _push(new_raw: bytes) -> int:
        comp = ufs.lz4hc(new_raw)
        index = len(new_blocks)
        if len(comp) >= len(new_raw):
            new_blocks.append(ufs.Block(len(new_raw), len(new_raw), 0))
            dirty[index] = new_raw
        else:
            new_blocks.append(ufs.Block(len(new_raw), len(comp), 3))
            dirty[index] = comp
        recompressed.append(index)
        return index

    def _emit_verbatim(old_index: int) -> None:
        raw = _block_bytes(reader, old_index)
        _bump(raw, raw)
        blk = reader.blocks[old_index]
        verbatim[len(new_blocks)] = old_index
        new_blocks.append(ufs.Block(blk.uncompressed, blk.compressed, blk.flags))
        old_to_new.setdefault(old_index, []).append(len(new_blocks) - 1)

    def _emit_piece(old_index: int, lo: int, hi: int) -> None:
        if hi <= lo:
            return
        base = starts[old_index]
        raw = _block_bytes(reader, old_index)
        patched = _patch_bytes(raw, base, reps)
        before = raw[lo - base : hi - base]
        after = patched[lo - base : hi - base]
        _bump(before, after)
        old_to_new.setdefault(old_index, []).append(_push(after))

    cursor = 0
    index = 0
    partial = False
    for point, data in inserts:
        # 完全落在插入点之前的块
        while index < count:
            end = starts[index] + reader.blocks[index].uncompressed
            if end > point:
                break
            if partial:
                _emit_piece(index, cursor, end)
                partial = False
            elif index in reps_by_block:
                _emit_piece(index, starts[index], end)
            else:
                _emit_verbatim(index)
            cursor = end
            index += 1
        # 插入点所在块的**头**
        if cursor < point:
            _emit_piece(index, cursor, point)
            cursor = point
            partial = True
        insert_block.append(len(new_blocks))
        _bump(b"", data)
        _push(data)
    # 插入点之后的块：边界与内容都没变 ⇒ 压缩字节原样拷贝
    while index < count:
        end = starts[index] + reader.blocks[index].uncompressed
        if partial or index in reps_by_block:
            _emit_piece(index, cursor, end)
            partial = False
        else:
            _emit_verbatim(index)
        cursor = end
        index += 1
    if state["old"] != reader.total_uncompressed:
        raise AssetError(
            f"Δ 块划分吃掉的旧流字节 {state['old']:,} != 全流 {reader.total_uncompressed:,}",
            hint="插入点或块边界算错了；⛔ 这种文件不要写",
        )
    return _Walk(
        new_blocks=new_blocks,
        dirty_payloads=dirty,
        verbatim_from=verbatim,
        recompressed=recompressed,
        insert_block=insert_block,
        old_to_new=old_to_new,
        crc_old=state["crc_old"] & 0xFFFFFFFF,
        crc_new=state["crc_new"] & 0xFFFFFFFF,
    )


def _delta_nodes(
    nodes: Sequence[ufs.Node],
    inserts: Sequence[tuple[int, bytes]],
    owners: Sequence[ufs.Node],
) -> list[ufs.Node]:
    """按「新字节属于哪个节点」重算节点表（口径照 ufs.py 的 _recompute_nodes）。

    * 新字节**属于**的节点：size += Δ，offset 不动；
    * 在插入点**之后**的其它节点：offset += Δ，size 不动。

    ⚠ 「插入点正好落在节点末尾」是个**二义点**（既可以说长在它里面、也可以说
    挤在下一个前面）—— 所以这里不靠坐标判，而是让调用方**指名** owner。
    """
    out: list[ufs.Node] = []
    for node in nodes:
        shift = 0
        grow = 0
        for (point, data), owner in zip(inserts, owners, strict=True):
            if owner is node:
                if not (node.offset <= point <= node.offset + node.size):
                    raise AssetError(
                        f"插入点 {point:,} 不在节点 {_node_basename(node.path)} 的 "
                        f"[{node.offset:,}, {node.offset + node.size:,}) 里"
                    )
                grow += len(data)
            elif point <= node.offset:
                shift += len(data)
        out.append(ufs.Node(node.offset + shift, node.size + grow, node.flags, node.path))
    return out


def _insert_shift(inserts: Sequence[tuple[int, bytes]], position: int) -> int:
    """旧流坐标 position **之后**（含该点）的累计插入量。"""
    return sum(len(data) for point, data in inserts if point <= position)


def _delta_runs(
    inserts: Sequence[tuple[int, bytes]], total_old: int
) -> list[tuple[str, int, int, object]]:
    """把新解压流切成两类 run：

    * ("map", 新起点, 旧起点, 长度) —— 老内容平移；
    * ("new", 新起点, 0, 字节)     —— 插进去的新字节。
    """
    points = sorted({point for point, _data in inserts})
    bounds: list[int] = []
    for value in [0, *points, total_old]:
        if not bounds or value > bounds[-1]:
            bounds.append(value)
    runs: list[tuple[str, int, int, object]] = []
    for index, lo in enumerate(bounds):
        hi = bounds[index + 1] if index + 1 < len(bounds) else None
        group = b"".join(data for point, data in inserts if point == lo)
        shift = _insert_shift(inserts, lo)
        if group:
            runs.append(("new", lo + shift - len(group), 0, group))
        if hi is not None and hi > lo:
            runs.append(("map", lo + shift, lo, hi - lo))
    return runs


def _delta_segments(
    inserts: Sequence[tuple[int, bytes]], total_old: int
) -> list[tuple[int, int, int, int]]:
    """H3 报告用：[(旧流起点, 旧流终点, 平移量, 新流起点)]（只含老内容段）。"""
    out: list[tuple[int, int, int, int]] = []
    for kind, new_pos, old_pos, length in _delta_runs(inserts, total_old):
        if kind == "map":
            out.append((old_pos, old_pos + int(length), new_pos - old_pos, new_pos))
    return out


#: Δ 逐段比对的分块大小（units 的 node[0] 是 2.5 GB，⛔ 不能一次读进来）。
_DELTA_CHUNK = 8 << 20


def _merged_ranges(pairs: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """把任意区间的**并集**整理成互不相交的升序区间表（只用于「某点在不在里面」）。

    语义与「逐条 any(lo <= pos < hi)」逐位相同 —— 只是把 162k 条区间压成几十段。
    """
    out: list[list[int]] = []
    for lo, hi in sorted(pairs):
        if hi <= lo:
            continue
        if out and lo <= out[-1][1]:
            if hi > out[-1][1]:
                out[-1][1] = hi
            continue
        out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def _in_ranges(
    ranges: Sequence[tuple[int, int]], starts: Sequence[int], pos: int
) -> bool:
    """pos 是否落在（已排序、互不相交的）ranges 的某一段里。"""
    index = bisect_right(starts, pos) - 1
    return index >= 0 and pos < ranges[index][1]

def _delta_compare(
    old_reader: ufs.UfsReader,
    new_reader: ufs.UfsReader,
    inserts: Sequence[tuple[int, bytes]],
    reps: Sequence[ufs.Replacement],
) -> tuple[int, int, int]:
    """H3：**按平移后的坐标**比其它未变 —— 返回 (总差异, 期望改动之外的差异, 新流 crc)。

    口径（工单 §2 ④）：旧流 O（长 L）、新流 N（长 L+Δ）、插入点 p
    ⇒ 要求 N[0:p) == O[0:p)（被原地改写的那几段除外）
    且 N[p+Δ:L+Δ) == O[p:L)。⛔ 不能拿"整条流逐字节比"当判据 —— 长度变了，
    那样比必然全红（这就是为什么要按平移后的坐标比）。
    """
    total_diff = 0
    outside = 0
    crc = 0
    # ⚠ 老写法是 any(lo <= absolute < hi for lo, hi in ranges)：ranges 有**每条改动一条**。
    # W8 要给 162,010 条表项各补 8 B（差异字节 ≈ 1.3e6）⇒ 1.3e6 × 1.62e5 ≈ 2e11 次比较，
    # 真件永远跑不完。这里先把区间**排序 + 合并**，再用 bisect 判归属 ——
    # 语义与老写法**逐位相同**（并集语义、只回答「在不在里面」），
    # 只是把「建表」变成 O(R log R)、「查询」变成 O(log R)。
    ranges = _merged_ranges([(rep.start, rep.end) for rep in reps])
    range_starts = [lo for lo, _hi in ranges]
    for kind, new_pos, old_pos, payload in _delta_runs(inserts, old_reader.total_uncompressed):
        if kind == "new":
            data = payload if isinstance(payload, bytes) else b""
            got = new_reader.read_range(new_pos, len(data))
            crc = zlib.crc32(got, crc)
            if got != data:
                for x, y in zip(got, data, strict=True):
                    if x != y:
                        total_diff += 1
                        outside += 1
            continue
        length = int(payload)
        done = 0
        while done < length:
            take = min(_DELTA_CHUNK, length - done)
            before = old_reader.read_range(old_pos + done, take)
            after = new_reader.read_range(new_pos + done, take)
            crc = zlib.crc32(after, crc)
            if before != after:
                for offset, (x, y) in enumerate(zip(before, after, strict=True)):
                    if x == y:
                        continue
                    total_diff += 1
                    absolute = old_pos + done + offset
                    if not _in_ranges(ranges, range_starts, absolute):
                        outside += 1
            done += take
    return total_diff, outside, crc & 0xFFFFFFFF


def _check_append_against_object(
    reader: ufs.UfsReader,
    node0: ufs.Node,
    info: ufs.SerializedFileInfo,
    by_id: Mapping[int, tuple[int, int, int]],
    rewritten: Mapping[int, bytes],
    append: StreamAppend,
    node: ufs.Node,
    offset: int,
    size: int,
) -> None:
    """复核：追加的落点与该 pathID 对象里声明的 m_StreamData **一致**。

    防的是「对象说像素在 A、我们却写到 B」—— 那种包能生成、也能被解析，
    但游戏读到的是**别的字节**（静默错件）。口径照 W6 的
    _check_stream_change_against_object：owner_path_id == 0 = 调用方**显式放弃**
    这条判据（例如只想把字节挂到节点尾、不指定属主）。

    rewritten 是「这次写回里被改写过的对象字节」（inplace / growth），
    优先拿它来查 —— 那才是写回之后对象里**真正**会有的 m_StreamData。
    """
    found = by_id.get(append.owner_path_id)
    if found is None:
        raise AssetError(
            f"append.owner_path_id={append.owner_path_id} 不在 {node0.path} 的对象表里",
            hint="owner_path_id 要带它自己那个对象的 pathID，才复核得了",
        )
    _index, stored, obj_size = found
    payload = rewritten.get(append.owner_path_id)
    if payload is None:
        payload = bytes(reader.read_range(node0.offset + info.data_offset + stored, obj_size))
    current = _stream_info_of_object(payload, path_id=append.owner_path_id)
    if (
        _node_basename(current.path) != _node_basename(node.path)
        or current.offset != offset
        or current.size != size
    ):
        raise AssetError(
            f"append 与 pathID={append.owner_path_id} 的 m_StreamData 不符："
            f"给的是 {_node_basename(node.path)}@{offset:,}+{size:,}，"
            f"对象里是 {_node_basename(current.path)}@{current.offset:,}+{current.size:,}",
            hint="落点写错会静默让游戏读到别的字节 —— 必须**先**算追加落点、**再**改对象"
            "（工单 §2 ① 的要害）",
        )


# --------------------------------------------------------------------------- #
# W8 · 新增对象（新 pathID）：表条目 / Δmeta / 升序尺子
# --------------------------------------------------------------------------- #
#: 对象表一条表项的布局（小端）：i64 pathID + i64 byteStart + i32 byteSize + i32 typeID。
#: **实测** 75/75 个 SerializedFile 节点的 stride 都是 24（_work/w8_probe_layout.py）。
_OBJECT_ENTRY = struct.Struct("<qqii")

#: 新对象 byteStart 的对齐。**实测**（_work/w7_probe_delta.py，真件 units）：
#: 表里 4,000/4,000 个 byte_start 都是 8 的倍数。
ADDED_ALIGN = 8

#: Δmeta 的对齐口径。**实测** 75/75 个节点都满足
#: data_offset == align16(48 + metadata_size) 且 data_offset % 16 == 0
#: （UnityPy 的写侧同样如此：data_offset = align16(header_size + metadata_size)）。
#: ⇒ 对象表插入的字节数向上取到 16 的倍数；多出来的零补在**元数据尾部**，
#: 写回后 data_offset 仍等于 align16(48+metadata_size)、gap 与每条 byteStart 都不动。
META_ALIGN = 16


def _meta_shift(entries_bytes: int, *, align: int = META_ALIGN) -> int:
    """对象表插入 entries_bytes 字节之后，**Δmeta（= metadata_size / data_offset 的增量）**。

    为什么取整到 align：老不变量是 data_offset = align16(48 + metadata_size)。
    对象表插 24N 字节后元数据内容长 24N；再在**元数据尾部**塞
    extra = (-24N) % 16 个零字节，就有 Δmeta = 24N + extra ≡ 0 (mod 16)，且
    data_offset + Δmeta == align16(48 + metadata_size + Δmeta) 仍然成立、
    gap（data_offset - (48 + metadata_size)）**一个字节都不变**。
    代数与三条断言见 _added_meta。
    """
    if entries_bytes < 0:
        raise AssetError(f"对象表插入量不能是负数：{entries_bytes}")
    return entries_bytes + (-entries_bytes) % align


def _object_count_offset(
    reader: ufs.UfsReader, node0: ufs.Node, info: ufs.SerializedFileInfo
) -> int:
    """对象表 count（i32，小端）在**解压流绝对坐标**里的位置。

    ⛔ 不能写死成「表首 - 4」：类型表每一项开头**不做对齐**
    （ufs.read_serialized_file 的踩坑 2），所以 count 字段结束到表首之间可能有
    0~3 字节的对齐填充。实测（_work/w8_probe_layout.py，77 个 bundle / 75 个节点）：
    在表首往前 4~7 字节里**唯一**命中一条 —— pad 分布 {0:2, 1:34, 2:26, 3:13}
    （units = pad 0，count @215,744）。

    判据（两条同时成立才算命中）：① 那 4 个字节按小端读 == 表里声明的对象数；
    ② 它到表首之间的填充字节全是 0。命中不是**恰好一条**就抛（⛔ 不许猜一个就改）。
    """
    base = info.object_table_offset
    lo = base - 8
    if lo < 0:
        raise AssetError(f"对象表首 {base} 太靠前，读不到它的 count 字段")
    window = bytes(reader.read_range(node0.offset + lo, 8))
    hits: list[int] = []
    for pad in range(4):
        pos = base - 4 - pad
        if pos < lo:
            continue
        off = pos - lo
        if struct.unpack_from("<i", window, off)[0] != info.object_count:
            continue
        if any(window[off + 4 :]):
            continue
        hits.append(pos)
    if len(hits) != 1:
        raise AssetError(
            f"对象表 count 字段在表首往前 4~7 字节里有 {len(hits)} 个候选（{hits}）",
            hint="对象表布局与产品的解析器不符；⛔ 不许猜一个位置就改",
        )
    return node0.offset + hits[0]


def _user_information_span(
    reader: ufs.UfsReader, node0: ufs.Node, info: ufs.SerializedFileInfo
) -> tuple[int, int]:
    """`userInformation` 字段在**元数据体**里的 (起点, 宽度含终结符)。

    偏移取自产品自己的 `metadata_writer.layout()`（写回侧的**唯一**偏移来源），⛔ 不自己数。
    """
    blob = bytes(reader.read_range(node0.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
    # ⛔ 必须 verify=False：真件与夹具的元数据区尾部都有解析器**不消费**的「类型树池」
    #   （units 实测 24,504 B；夹具 5,000 B），严格校验会在这里误报「走到 ≠ 声明」。
    #   本函数只要 userInformation 的**偏移**，不需要那条校验 ——
    #   校验由 ufs.read_serialized_file 负责。
    lay = mw.layout(mw.parse_serialized_metadata(blob, verify=False))
    for row in lay.rows:
        if row.get("name") == "userInformation":
            return int(row["offset"]), int(row["size"])
    raise AssetError(
        "元数据里没有 userInformation 字段 —— 新增对象时无处安放对齐补零",
        hint="没有这个字段就保证不了 metadata_size == 解析终点；⛔ 不许改回「填在体末尾」",
    )


def _sorted_insert_index(path_ids: Sequence[int], path_id: int) -> int:
    """新 pathID 该插进**严格升序**对象表的第几条（0 = 插到最前）。

    实测 units 的 162,010 条 pathID **严格升序**；插到错的格子会让表不再升序
    ⇒ 按 pathID 二分查找的读者直接找不到对象（**静默错件**）。所以这是机械判据。
    """
    return bisect_left(path_ids, path_id)


def _assert_path_ids_ascending(path_ids: Sequence[int], what: str) -> None:
    """尺子：pathID 必须**严格升序**（判据 J1）。不升序就抛 —— 负控 ② 靠它变红。"""
    bad = [
        (before, after)
        for before, after in zip(path_ids, path_ids[1:], strict=False)
        if after <= before
    ]
    if bad:
        head = "；".join(f"{a} → {b}" for a, b in bad[:4])
        raise AssetError(
            f"{what}的 pathID 不是严格升序（{len(bad)} 处）：{head}",
            hint="新条目插到错的升序位置 ⇒ 读者二分找不到对象（静默错件）",
        )

@dataclass(frozen=True, slots=True)
class _AddedMeta:
    """新增对象时「元数据区怎么长」的四个数（口径 = ADDED_META_MODE）。"""

    #: metadata_size(@20) 的增量 Δmeta。
    metadata_growth: int
    #: 补在元数据尾部的**零字节**数（== Δmeta − 24N）。
    pad_extra: int
    #: 新的 data_offset(@32)。
    data_offset_new: int
    #: **每条老表项 byteStart 的增量**（B 下 = 0）。
    byte_start_shift: int


def _added_meta(
    entries_bytes: int,
    *,
    metadata_size_old: int,
    data_offset_old: int,
) -> _AddedMeta:
    """新增对象之后「元数据区 / 数据区」怎么摆：**代数**（Lead 2026-10-03 定的 B）。

    记 D = data_offset、M = metadata_size、A = 48 + M、gap = D − A、
    S = **数据区之前实际插入的字节数**（纯插入的块划分 ⇒ 数据区物理平移 S）。

    恒等式（两边都在工单与 ufs.py 里测过：对象绝对位置 = node.offset + data_offset + byteStart）::

        node.offset + D_new + bs_new == node.offset + D_old + bs_old + S
        ⇒ **bs_new = bs_old + (S − Δ)**，Δ = D_new − D_old        （⛔ 不是 +Δ！）

    口径（Lead 2026-10-03 定的 B）：
        Δmeta = align16(24N)、S = Δmeta（只多插 Δmeta − 24N 个零字节在元数据尾部）
        ⇒ D_new = D_old + Δmeta、M_new = M_old + Δmeta ⇒ **Δ ≡ S ⇒ bs_new == bs_old**
        （一条表项都不用改）。且两边同余 ⇒ gap 与 D_new == align16(48+M_new) 自动保持。

    为什么「补零」不是发明内部填充：设计稿 §9.8 **U1** 实测 —— units 的元数据区尾部本来就有
    24,504 B 解析器**不消费**的「类型树池」（externals 结束于 4,104,521、metadata_end = 4,129,025）
    ⇒ 「声明区尾部有零字节」是这份格式的常态。

    ⚠ `byte_start_shift` 仍然**按恒等式算**（不是写死 0）：写后复核那条断言
    「新 byteStart == 旧 byteStart + (S − Δ)」因此与口径无关 —— 将来谁换了摆法，
    同一条尺子照样成立（B 下它就是 0）。
    """
    if entries_bytes < 0:
        raise AssetError(f"对象表插入量不能是负数：{entries_bytes}")
    if data_offset_old != _align16(ufs.SF_HEADER_SIZE + metadata_size_old):
        raise AssetError(
            f"原包的 data_offset {data_offset_old:,} != align16(48 + metadata_size "
            f"{metadata_size_old:,})",
            hint="实测真件 75/75 都满足这条；不满足时「新增对象」的位移代数不成立 ⇒ ⛔ 不许写",
        )
    # 2026-10-09 修正（实测：N=1 写出「走到 4129049、声明 4129057、差 -8」的坏包，
    # Unity 报 LoadFromFile → null、UI 全没；N=2 因为 24N 恰好是 16 的倍数而蒙对）：
    # 解析器每插入一条表项只多消费 **24 字节**（align(4) + i64+i64+u32+i32），
    # 所以 metadata_size 必须**恰好**涨 entries_bytes —— ⛔ 不能取整到 16。
    # 取整多出来的那几字节只能落在解析器**不消费**的元数据尾部 ⇒ 「走到 ≠ 声明」。
    # 对齐由 `data_offset = align16(48 + metadata_size)` 那个**体外间隙**吸收；
    # 代价：Δmeta ≠ Δdata_offset ⇒ 必须按 ④ 把老表项的 byteStart 平移 shift。
    # 口径（2026-10-03 定的 B）：**Δmeta = align16(24N)**。
    # ⚠ 为什么必须取整到 16（2026-10-09 复推）：物理插入 24N + pad 字节 ⇒ 数据区前移
    #   (24N + pad)；而铁律 data_offset == align16(48 + metadata_size) 要求它前移 Δmeta。
    #   两者相等 ⟺ Δmeta ≡ 0 (mod 16)（加 16 的倍数与 align16 可交换）。
    #   ⇒ pad **不是可选的美化，而是布局成立的条件**。
    # ⚠⚠ 但 pad 必须落在**解析器会消费**的地方 —— 见下面 ③ 里对 userInformation 的插入。
    metadata_growth = _meta_shift(entries_bytes)
    pad_extra = metadata_growth - entries_bytes
    data_offset_new = _align16(ufs.SF_HEADER_SIZE + metadata_size_old + metadata_growth)
    shift = entries_bytes + pad_extra - (data_offset_new - data_offset_old)
    out = _AddedMeta(
        metadata_growth=metadata_growth,
        pad_extra=pad_extra,
        data_offset_new=data_offset_new,
        byte_start_shift=shift,
    )
    # ---- 三条机械断言（Lead 2026-10-03 要求；⛔ 不许省） ----
    if out.data_offset_new != _align16(
        ufs.SF_HEADER_SIZE + metadata_size_old + out.metadata_growth
    ):
        raise AssetError(
            f"新 data_offset {out.data_offset_new} != align16(48 + {metadata_size_old} + "
            f"{out.metadata_growth})",
            hint="设计稿 §9.5 实测 75/75：data_offset == align16(48+metadata_size)",
        )
    if out.data_offset_new % 16:
        raise AssetError(f"新 data_offset {out.data_offset_new} 不是 16 的倍数")
    if out.metadata_growth % 16 or out.metadata_growth < entries_bytes:
        raise AssetError(
            f"Δmeta={out.metadata_growth} 不满足 16 的倍数且 >= 对象表插入量 {entries_bytes}"
            "（Δmeta 必须是 16 的倍数：物理插入量要等于 Δdata_offset，两者相等 ⟺ Δmeta % 16 == 0）"
        )
    if out.pad_extra != out.metadata_growth - entries_bytes:
        raise AssetError(
            f"补的零字节 {out.pad_extra} != Δmeta − 24N = {out.metadata_growth - entries_bytes}"
        )
    if (data_offset_new - data_offset_old) % 8:
        raise AssetError(
            f"data_offset 增量 {data_offset_new - data_offset_old} 不是 8 的倍数",
            hint="对象 byteStart 的 8 字节对齐（设计稿 §9.9 硬约束）会被破坏",
        )
    return out

def _plan_delta(
    reader: ufs.UfsReader,
    change: DeltaChange,
    *,
    catalog_path: Path | None,
) -> _InternalPlan:
    """Δ 计划：**干跑与真写共用**（与 W5 的 _plan / W6 的 _plan_stream 同一个位置）。"""
    if not (change.inplace or change.growths or change.appends or change.added):
        raise AssetError(
            "DeltaChange 是空的",
            hint="四类改动（inplace/growths/appends/added）至少给一个",
        )
    node0, info = _serialized_node_info(reader)
    data_base = node0.offset + info.data_offset
    header = reader.read_range(node0.offset, ufs.SF_HEADER_SIZE)
    old_file_size = struct.unpack_from(">q", header, 24)[0]
    by_id = {pid: (index, stored, size) for index, pid, stored, size, _tid in info.objects}
    # ⛔ 一个 pathID 在一次写回里许被改一次：重复会让两处补丁互相盖掉（静默错件）
    targets = [item.path_id for item in change.inplace] + [item.path_id for item in change.growths]
    duplicates = sorted({pid for pid in targets if targets.count(pid) > 1})
    if duplicates:
        raise AssetError(
            f"DeltaChange 里同一个 pathID 被改了不止一次：{duplicates}",
            hint="一处对象只能有一条改动；重复的补丁会互相盖掉",
        )

    rewritten: dict[int, bytes] = {
        item.path_id: bytes(item.payload) for item in change.inplace
    }
    rewritten.update({item.path_id: bytes(item.payload) for item in change.growths})

    ins_point: list[int] = []
    ins_data: list[bytes] = []
    ins_owner: list[ufs.Node] = []
    ins_row: list[int] = []
    reps: list[ufs.Replacement] = []
    row_specs: list[PlanRow] = []
    append_offsets: list[tuple[str, int, int]] = []
    append_owners: list[ufs.Node] = []
    inplace_spans: list[tuple[int, int, int]] = []
    growth_starts: list[int] = []

    # ① 对象长度变化（变长 / 变短）：一律搬到 node[0] 尾部（8 字节对齐），只改它那一条表项
    tail_point = node0.offset + node0.size
    node_local_end = node0.size
    inserted_at_tail = 0
    # 只有「新增对象」那条路会改动它（Δmeta ≠ 0 时）；没有新增对象时恒为 0。
    # ⛔ 必须在这里初始化：growth 循环也用同一个量，否则纯 growth 的调用会 UnboundLocalError。
    byte_start_shift = 0
    for growth in change.growths:
        found = by_id.get(growth.path_id)
        if found is None:
            raise KeyError(f"{node0.path} 的对象表里没有 pathID={growth.path_id}")
        entry_index, _stored, size = found
        payload = bytes(growth.payload)
        if len(payload) == size:
            raise AssetError(
                f"ObjectGrowth pathID={growth.path_id} 的新字节 {len(payload):,} B "
                f"**正好等于**原 {size:,} B —— 等长请走 W6 的 ObjectChange（原地覆盖，"
                "更便宜、不动表），⛔ 别走 Δ",
            )
        stored = node_local_end - info.data_offset
        pad = (-stored) % 8
        data = b"\x00" * pad + payload
        # 原地改**那一条**表项（byte_start i64 + byte_size i32 = 12 B）——
        # 只补这 12 个字节，别整段重写元数据（否则 32 个块要重压，白干）
        at = info.object_table_offset + entry_index * info.object_entry_stride
        reps.append(
            ufs.Replacement(
                node0.offset + at + 8,
                node0.offset + at + 20,
                # ⚠ 同样要带 shift（byteStart 是相对**新** data_offset 的）：少这一下，
                #   老对象的绝对位置会差 (S−Δ)——写后复核的「逐字节读回」必红。
                struct.pack("<qi", stored + pad + byte_start_shift, len(payload)),
            )
        )
        ins_point.append(tail_point)
        ins_data.append(data)
        ins_owner.append(node0)
        ins_row.append(len(row_specs))
        growth_starts.append(tail_point + inserted_at_tail + pad)
        row_specs.append(
            PlanRow(
                path_id=growth.path_id,
                name=growth.name or f"<{growth.path_id}>",
                type_name=growth.type_name or "Object",
                old_bytes=size,
                new_bytes=len(payload),
                level=LEVEL_GROW,
                block_first=-1,
                block_last=-1,
                stream_start=growth_starts[-1],
            )
        )
        inserted_at_tail += len(data)
        node_local_end += len(data)

    # ------------------------------------------------------------------ #
    # ④（W8）**新增对象**（新 pathID）：一套事务四件事
    #    ① 新 blob 按 pathID 升序追加到 node[0] 尾部（byteStart **8 字节对齐**）；
    #    ② 对象表插条目（保持 pathID **严格升序**）；
    #    ③ SF 头三处同步：metadata_size(@20) / data_offset(@32) / file_size(@24)；
    #    ④ 每条老表项的 byteStart 按「数据区物理平移量 − data_offset 增量」平移。
    #
    #    ⚠ 「老对象有没有错位」的**唯一**判据是「按新坐标逐字节读回」（写后复核 J1/J7 那条），
    #      不是「byteStart 必须 += data_offset 的增量」—— 两者只有在数据区**没有**整体平移
    #      时才是同一个数。口径与代数见 _added_meta 的 docstring（Lead 2026-10-03 定的 B）。
    # ------------------------------------------------------------------ #
    added_layout: list[tuple[NewObject, int, int]] = []
    meta_growth = 0
    byte_start_shift = 0
    data_offset_new: int | None = None
    metadata_size_new: int | None = None
    data_offset_old: int | None = None
    if change.added:
        existing_ids = [row[1] for row in info.objects]
        _assert_path_ids_ascending(existing_ids, f"{node0.path} 的对象表（改前）")
        seen = set(existing_ids)
        for item in change.added:
            if item.path_id <= 0:
                raise AssetError(f"NewObject.path_id={item.path_id} 不是正数")
            if item.path_id >= 1 << 63:
                # 真件 units 里最大的 pathID 已经接近 i64 上界（9,223,358,220,759,698,752），
                # 所以这条不是纸上规矩：溢出的值 pack 不进 i64，会静默错件。
                raise AssetError(
                    f"NewObject.path_id={item.path_id} 超出 i64 上界（pathID 是 i64）"
                )
            if item.type_id < 0:
                raise AssetError(
                    f"NewObject pathID={item.path_id} 的 type_id={item.type_id} 不是类型表下标",
                    hint="type_id 是对象表里的**类型表下标**，⛔ 不是 classID",
                )
            if not item.payload:
                raise AssetError(f"NewObject pathID={item.path_id} 的 payload 是空的")
            if item.path_id in seen:
                raise AssetError(
                    f"NewObject.path_id={item.path_id} 与包里已有（或本次新增的）pathID 撞了",
                    hint="新对象的 pathID 必须唯一；用 writer.next_path_id(bundle) 造一个",
                )
            seen.add(item.path_id)
        ordered = sorted(change.added, key=lambda item: item.path_id)
        entries_bytes = info.object_entry_stride * len(ordered)
        meta = _added_meta(
            entries_bytes,
            metadata_size_old=info.metadata_size,
            data_offset_old=info.data_offset,
        )
        meta_growth = meta.metadata_growth
        byte_start_shift = meta.byte_start_shift
        data_offset_new = meta.data_offset_new
        metadata_size_new = info.metadata_size + meta_growth
        data_offset_old = info.data_offset

        for item in ordered:
            payload = bytes(item.payload)
            index = _sorted_insert_index(existing_ids, item.path_id)
            # ⚠ 新对象的 byteStart 是**相对新 data_offset** 的：老对象整体 +shift，
            #   新对象也必须带上同一个 shift —— 少这一下，F2「读回那片字节」必红
            #   （2026-10-09 实测：S=24、Δ=16 ⇒ 新对象差 8 字节）。
            stored = node_local_end - info.data_offset + byte_start_shift
            pad = (-stored) % ADDED_ALIGN
            data = b"\x00" * pad + payload
            # ② 对象表：24 字节插到**升序位置**（表项起点仍是 24 的倍数 ⇒ align4 不破）
            ins_point.append(
                node0.offset + info.object_table_offset + index * info.object_entry_stride
            )
            ins_data.append(
                _OBJECT_ENTRY.pack(item.path_id, stored + pad, len(payload), item.type_id)
            )
            ins_owner.append(node0)
            ins_row.append(-1)
            # ① 新 blob：追加到 node[0] 尾部（对齐填充在 payload **前面**）
            ins_point.append(tail_point)
            ins_data.append(data)
            ins_owner.append(node0)
            ins_row.append(len(row_specs))
            row_specs.append(
                PlanRow(
                    path_id=item.path_id,
                    name=item.name or f"<{item.path_id}>",
                    type_name=item.type_name or "Object",
                    old_bytes=0,
                    new_bytes=len(payload),
                    level=LEVEL_GROW,
                    block_first=-1,
                    block_last=-1,
                    stream_start=tail_point + inserted_at_tail + pad,
                )
            )
            added_layout.append((item, stored + pad, index))
            inserted_at_tail += len(data)
            node_local_end += len(data)

        # ④ 老表项 byteStart 平移（2026-10-09 新增口径：Δmeta = 24N ≠ Δdata_offset）
        #    恒等式：对象绝对位置 = node.offset + D + bs，插入 S 字节 ⇒ bs_new = bs_old + (S − Δ)。
        #    ⚠ 做法必须是**等长替换整张老表**：_patch_bytes 是原地补丁，Replacement 放不下变长；
        #      而新表项的插入点落在这张表的区间内 —— 块划分把「补丁后的字节」按插入点切片，
        #      所以「先补丁、再在补丁区间内插入」能正确复合（见 _delta_block_walk_groups）。
        if byte_start_shift:
            table_at = node0.offset + info.object_table_offset
            table_len = info.object_entry_stride * info.object_count
            old_tbl = reader.read_range(table_at, table_len)
            patched = bytearray(old_tbl)
            # ⛔ 被 growth 改过的表项**跳过**：那一条由上面 ① 的 12 字节 Replacement 负责
            #   （byte_start + byte_size 一起写，且已带上 shift）。整表替换若把它盖回去，
            #   写后复核就会报「老 pathID=N 的 byte_size X != Y」（2026-10-09 实测真现场）。
            grown_ids = {item.path_id for item in change.growths}
            moved = 0
            for i in range(info.object_count):
                # 表项布局：pathID(8) 后就是 byteStart(8)
                off = i * info.object_entry_stride + 8
                pid = struct.unpack_from("<q", old_tbl, off - 8)[0]
                if pid in grown_ids:
                    continue
                bs = struct.unpack_from("<q", old_tbl, off)[0]
                # ⚠ 无条件平移（含 byteStart==0 的无数据对象）—— 写后复核的不变量是
                #   「每条老表项都满足 bs_new == bs_old + (S − Δ)」，加例外会让它变红。
                struct.pack_into("<q", patched, off, bs + byte_start_shift)
                moved += 1
            reps.append(
                ufs.Replacement(table_at, table_at + table_len, bytes(patched))
            )

        # ③ SF 头三处（同一事务里改）
        if meta.pad_extra:
            # ⚠ 2026-10-09 修正：pad **不能**填在元数据体末尾 —— 解析器（Unity 也一样）在那里
            #   就停止消费了，于是 metadata_size 比解析终点多 pad_extra ⇒ **Unity 拒收整个包**
            #   （LoadFromFile → null，报 "not built with the right version or build target"；
            #    症状极隐蔽：世界照常加载，只有 UI 全没 —— 改9/改10 两次「GUI 消失」就是它。
            #    复现读数：N=1 →「走到 4129049、声明 4129057、差 −8」。）
            #   改放进 userInformation（零结尾字符串）的**终结符之前**：解析器按 cstr 读，
            #   会把这 pad_extra 个非零字节一并吃进去 ⇒ 解析终点正好 == 48 + metadata_size。
            ui_start, ui_size = _user_information_span(reader, node0, info)
            ins_point.append(node0.offset + ui_start + ui_size - 1)   # 终结符之前
            ins_data.append(b"#" * meta.pad_extra)                     # ⛔ 非零：零会被当成终结符
            ins_owner.append(node0)
            ins_row.append(-1)
        reps.append(
            ufs.Replacement(
                node0.offset + 20,
                node0.offset + 24,
                struct.pack(">I", info.metadata_size + meta_growth),
            )
        )
        reps.append(
            ufs.Replacement(
                node0.offset + 32,
                node0.offset + 40,
                struct.pack(">q", meta.data_offset_new),
            )
        )
        count_at = _object_count_offset(reader, node0, info)
        reps.append(
            ufs.Replacement(
                count_at, count_at + 4, struct.pack("<i", info.object_count + len(ordered))
            )
        )
        # ④ 老表项的 byteStart：**一条都不用改**（B 口径下 Δ ≡ S ⇒ bs_new == bs_old）。
        #    ⛔ 这不是「忘了写」：「bs_new == bs_old + (S − Δ)」那条恒等式由**写后复核**
        #    _verify_added 逐条机械守住（它从文件里读回新表、从计划里取 S 与 Δ），
        #    并且 _added_meta 已经断言了原包满足 data_offset == align16(48+metadata_size)。
        # 2026-10-09：口径已从「Δmeta = align16(24N)」改成「Δmeta = 24N」，
        # 于是 Δmeta ≠ Δdata_offset、byte_start_shift ≠ 0 成为**常态** ——
        # 老表项的 byteStart 由下面 ④ 的整表替换统一平移。
        # 升序尺子（负控 ② 靠它变红）：按**真实插入顺序**（旧坐标插入点升序、同点保持插入顺序）
        # 回放成新对象表，再断言严格升序。⛔ 不许用 sorted() 造期望表 —— 那是同义反复。
        replay = sorted(range(len(added_layout)), key=lambda k: (added_layout[k][2], k))
        new_ids = list(existing_ids)
        for offset, key in enumerate(replay):
            new_ids.insert(added_layout[key][2] + offset, added_layout[key][0].path_id)
        _assert_path_ids_ascending(new_ids, f"{node0.path} 写回后的对象表")
    sf_growth = inserted_at_tail + meta_growth

    # ② 流式追加：节点尾（工单 §2 ③，本包 node[1] 就是最后一个节点）
    per_node: dict[int, int] = {}
    for append in change.appends:
        payload = bytes(append.payload)
        if not payload:
            raise AssetError("StreamAppend 的 payload 是空的")
        node = _stream_node(reader, append.node_path)
        if node is node0:
            raise AssetError(
                f"StreamAppend 指向 node[0]（{node.path}）—— 那是对象数据节点，"
                "⛔ 不许往它尾部乱塞（要改对象用 ObjectGrowth）",
            )
        if node.size <= 0:
            raise AssetError(f"节点 {node.path} 的 size 是 0，没有可追加的尾部")
        extra = per_node.get(id(node), 0)
        raw_offset = node.size + extra
        pad = (-raw_offset) % STREAM_APPEND_ALIGN
        offset = raw_offset + pad
        data = b"\x00" * pad + payload
        point = node.offset + node.size
        ins_point.append(point)
        ins_data.append(data)
        ins_owner.append(node)
        # ⛔ 追加**不造 PlanRow**：它不是「一个对象」，而 WritebackPlan.rows 是
        # 「对象级 delta」那条判据的输入 —— 掺一行 0 -> 5.6 MB 的"假对象"会让那个读数
        # 失去意义。追加的读数单独出（criterion_lines 的「Δ 机制 / 节点尾追加」两行）。
        ins_row.append(-1)
        append_owners.append(node)
        append_offsets.append((_node_basename(node.path), offset, len(payload)))
        if append.owner_path_id:
            _check_append_against_object(
                reader,
                node0,
                info,
                by_id,
                rewritten,
                append,
                node,
                offset,
                len(payload),
            )
        per_node[id(node)] = extra + len(data)

    # ③ 等长原地覆盖（Texture2D 的四个字段都是定长标量 ⇒ 对象本身等长）
    for item in change.inplace:
        found = by_id.get(item.path_id)
        if found is None:
            raise KeyError(f"{node0.path} 的对象表里没有 pathID={item.path_id}")
        _entry_index, stored, size = found
        payload = bytes(item.payload)
        if len(payload) != size:
            raise AssetError(
                f"DeltaChange.inplace 里 pathID={item.path_id} 的长度 {len(payload):,} B "
                f"!= 原 {size:,} B —— 等长才走 inplace；变长请用 ObjectGrowth"
            )
        start = data_base + stored
        reps.append(ufs.Replacement(start, start + size, payload))
        inplace_spans.append((len(row_specs), start, start + size))
        row_specs.append(
            PlanRow(
                path_id=item.path_id,
                name=item.name or f"<{item.path_id}>",
                type_name=item.type_name or "Object",
                old_bytes=size,
                new_bytes=size,
                level=LEVEL_EQUAL,
                block_first=-1,
                block_last=-1,
                stream_start=start,
            )
        )

    # 元数据区：SerializedFile 头 [24:32] 的 file_size（节点长了）—— 同样只补这 8 个字节
    if sf_growth:
        reps.append(
            ufs.Replacement(
                node0.offset + 24,
                node0.offset + 32,
                struct.pack(">q", old_file_size + sf_growth),
            )
        )

    order = sorted(range(len(ins_point)), key=lambda key: ins_point[key])
    raw_inserts = [(ins_point[key], ins_data[key]) for key in order]
    owners = [ins_owner[key] for key in order]
    row_of_insert = [ins_row[key] for key in order]
    # 2026-10-06：**同坐标先串接**再进块划分（否则 194 个对象 = 388 个插入点 / 392 块重压，
    # 与真件「均匀 128 KiB 网格」完全不像）。串接只改**块划分的入口**：
    # 字节总数、顺序、以及写后复核的 _delta_runs 语义都不变，所以 layout.inserts 用同一份。
    inserts, merged_from = _merge_inserts(raw_inserts)
    owners = _merge_owners(owners, merged_from)
    for point, _data in inserts:
        if point < 0 or point > reader.total_uncompressed:
            raise AssetError(
                f"插入点 {point:,} 越出解压流 [0, {reader.total_uncompressed:,}]"
            )

    new_nodes = _delta_nodes(reader.nodes, inserts, owners)
    walk = _delta_block_walk(reader, reps, inserts)
    assert len(walk.insert_block) == len(merged_from), (
        f"合并后的插入点 {len(merged_from)} 个，但块划分给了 {len(walk.insert_block)} 个块 —— "
        "两者的口径必须一一对应（串接只许合并同坐标）"
    )
    for key, block_index in enumerate(walk.insert_block):
        for source in merged_from[key]:
            row_index = row_of_insert[source]
            if row_index >= 0:
                row_specs[row_index] = replace(
                    row_specs[row_index], block_first=block_index, block_last=block_index
                )
    for row_index, start, end in inplace_spans:
        first, last = _hit_blocks(reader, start, end)
        mapped = [
            new_index
            for old_index in range(first, last + 1)
            for new_index in walk.old_to_new.get(old_index, [])
        ]
        if mapped:
            row_specs[row_index] = replace(
                row_specs[row_index], block_first=min(mapped), block_last=max(mapped)
            )
    _blocks_info, compressed_bi, data_start, total = _assemble(
        reader, walk.new_blocks, new_nodes
    )

    catalog_name = reader.path.name
    crc_catalog_old: int | None = None
    size_catalog_old: int | None = None
    extra_offset: int | None = None
    blocked: list[str] = []
    if catalog_path is not None:
        entry = catalog_entry(catalog_path, catalog_name)
        crc_catalog_old = entry.crc
        size_catalog_old = entry.bundle_size
        extra_offset = entry.extra_offset
        if entry.crc is not None and entry.crc != walk.crc_old:
            blocked.append(
                f"磁盘 CRC {walk.crc_old} != catalog 里写的 m_Crc {entry.crc}"
                "（这个包已经被别的工具改过，先别写）"
            )
        if entry.bundle_size is not None and entry.bundle_size != reader.file_size:
            blocked.append(
                f"磁盘大小 {reader.file_size:,} != catalog 里写的 "
                f"m_BundleSize {entry.bundle_size:,}"
            )

    hit_runs = _merge_runs([(index, index) for index in walk.recompressed])
    plan = WritebackPlan(
        bundle=reader.path,
        bundle_name=catalog_name,
        rows=tuple(row_specs),
        block_runs=hit_runs,
        hit_blocks=len(walk.recompressed),
        eta_seconds=len(walk.recompressed) * RECOMPRESS_SECONDS_PER_BLOCK + FULL_CRC_SECONDS,
        old_file_size=reader.file_size,
        new_file_size=total,
        disk_crc=walk.crc_old,
        catalog_bundle_name=catalog_name,
        catalog_crc_old=crc_catalog_old,
        catalog_crc_new=walk.crc_new,
        catalog_size_old=size_catalog_old,
        catalog_extra_offset=extra_offset,
        allow_grow=True,
        insert_points=tuple((point, len(data)) for point, data in inserts),
        recompressed_blocks=tuple(walk.recompressed),
        verbatim_blocks=tuple(sorted(walk.verbatim_from.items())),
        append_offsets=tuple(append_offsets),
        added_objects=tuple(
            (item.path_id, index, stored, len(item.payload), item.type_id)
            for item, stored, index in added_layout
        ),
        meta_growth=meta_growth,
        byte_start_shift=byte_start_shift,
        old_metadata_size=info.metadata_size if added_layout else None,
        new_metadata_size=metadata_size_new,
        old_data_offset=data_offset_old,
        new_data_offset=data_offset_new,
        blocked_reason="；".join(blocked),
    )
    assembly = _Assembly(
        new_blocks=walk.new_blocks,
        dirty_payloads=walk.dirty_payloads,
        blocks_info=_blocks_info,
        compressed_blocks_info=compressed_bi,
        data_start=data_start,
        total=total,
        verbatim_from=walk.verbatim_from,
    )

    # F2 读回的口径：**新流坐标**下的目标区间 + 期望字节（成对，顺序一致）
    # ⚠ W8：元数据区的插入（对象表 24N + 对齐零）会把**其后的一切**（含 node[0] 尾部的
    # growth blob、数据区里原地改的对象、node[1]）整体平移 S = meta_growth ——
    # 所以 growth_starts / inplace 的坐标（都是**旧流坐标**）必须 +S 才是新流坐标。
    # ⛔ 不补这一下 = F2 读回读到别的字节（J3 的 growth 现场就是这么被抓出来的）。
    pairs: list[tuple[tuple[int, int], ufs.Replacement]] = []
    for growth, start in zip(change.growths, growth_starts, strict=True):
        span = (start + meta_growth, start + meta_growth + len(growth.payload))
        pairs.append((span, ufs.Replacement(span[0], span[1], bytes(growth.payload))))
    new_offset_of = {id(node): new_nodes[index] for index, node in enumerate(reader.nodes)}
    for append, owner, (_name, offset, size) in zip(
        change.appends, append_owners, append_offsets, strict=True
    ):
        base = new_offset_of[id(owner)].offset + offset
        span = (base, base + size)
        pairs.append((span, ufs.Replacement(span[0], span[1], bytes(append.payload))))
    for item, (_row, start, end) in zip(change.inplace, inplace_spans, strict=True):
        span = (start + meta_growth, end + meta_growth)
        pairs.append((span, ufs.Replacement(span[0], span[1], bytes(item.payload))))
    for item, stored, _index in added_layout:
        # 新对象的读回坐标 = node0.offset + **新** data_offset + byteStart（表里就是这么写的）
        head = node0.offset + (data_offset_new or info.data_offset) + stored
        span = (head, head + len(item.payload))
        pairs.append((span, ufs.Replacement(span[0], span[1], bytes(item.payload))))
    pairs.sort(key=lambda pair: pair[0][0])
    ranges = [span for span, _rep in pairs]
    payloads = [rep for _span, rep in pairs]

    layout = _DeltaLayout(
        inserts=inserts,
        owners=owners,
        reps=reps,
        new_nodes=new_nodes,
        walk=walk,
        sf_growth=sf_growth,
        append_offsets=append_offsets,
        node_growth=[
            (_node_basename(new.path), new.size - old.size)
            for new, old in zip(new_nodes, reader.nodes, strict=True)
            if new.size != old.size
        ],
    )
    return _InternalPlan(
        plan=plan,
        replication_of=payloads,
        assembly=assembly,
        object_ranges=ranges,
        serialized_node_path=node0.path,
        serialized_range=(node0.offset, node0.offset + node0.size),
        kind="delta",
        delta=layout,
    )


def plan_delta(
    bundle_path: str | Path,
    change: DeltaChange,
    *,
    catalog_path: str | Path | None = None,
) -> WritebackPlan:
    """**干跑**：算出 Δ（变长 / 流式追加 / **新增对象**）写回的 F1 那一行，一个字节都不写。

    新增对象时 F1 里多两行：「Δ 新增对象」（pathID / 表内第几条 / byteStart / typeID）与
    「Δmeta」（metadata_size 与 data_offset 的旧→新、byteStart 平移量）。
    """
    with ufs.UfsReader(bundle_path) as reader:
        internal = _plan_delta(
            reader,
            change,
            catalog_path=Path(catalog_path) if catalog_path is not None else None,
        )
        return internal.plan


def write_delta(
    bundle_path: str | Path,
    change: DeltaChange,
    *,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    allow_running_game: bool = False,
    progress: Any = None,
    token: Any = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """W7-A/W8-A 的链：**变长**（对象搬尾部 / 流式节点尾追加 / **新增对象**）→ 自由块划分
    → 重算 CRC → catalog。

    W8 的 `added`（新 pathID）四件事（见 _plan_delta 与 _added_meta）：
    新 blob 追加（8 字节对齐）+ 对象表插条目（严格升序）+ SF 头三处同步
    （metadata_size / data_offset / file_size）+ 老表项 byteStart 按恒等式平移
    （B 口径下恒为 0 ⇒ **一条都不改**）。写后复核多一条 J1/J7（_verify_added）。

    与 write_objects / write_stream **同一条** _write_bundle：三道红线
    （没备份不许写 / 备份与工程目录跟随游戏盘 / 游戏在跑拦住）与拼装、替换、写后复核
    全部复用，⛔ 不是另开一套。
    """
    return _write_bundle(
        bundle_path,
        lambda reader, catalog: _plan_delta(reader, change, catalog_path=catalog),
        dry_run=dry_run,
        backup_dir=backup_dir,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        allow_running_game=allow_running_game,
        progress=progress,
        token=token,
        patch_store=patch_store,
        patch_label=patch_label,
    )


def next_path_id(bundle_path: str | Path, *, hint: str = "") -> int:
    """给克隆用：造一个**唯一**的 pathID（正的、递增、不与包内任何已有 pathID 撞）。

    口径（Lead 2026-10-03 定）：

    * 取对象表里**最大的 pathID + 1**（实测 units 的 162,010 条严格升序 ⇒ 这个值是新的最大值，
      插到表尾 = 插入点最靠后、重压的块最少）；
    * `hint` 是**纯十进制串**时当作起始**下限**：candidate = max(max_id + 1, int(hint))；
      非数字串只当诊断文案（不影响返回值）；
    * 返回值**先查重**：真的与包内已有 pathID 相同就原地 +1 直到不撞，撞穿了就抛。
      （正常永远不该发生；这条是防「表不升序 / 解析走偏」的机械闸。）
    """
    text = (hint or "").strip()
    floor = int(text) if text.isdigit() else 0
    with ufs.UfsReader(bundle_path) as reader:
        _node0, info = _serialized_node_info(reader)
        ids = {row[1] for row in info.objects}
        top = max(ids, default=0)
    candidate = max(top + 1, floor, 1)
    while candidate in ids:
        candidate += 1
    if candidate >= 1 << 63:
        raise AssetError(
            f"包里的最大 pathID 已经是 {top}（i64 上界），max+1 放不进 pathID",
            hint="这个包没法再分配更大的 pathID；要新增对象得先在对象表里找空档",
        )
    if candidate in ids or candidate <= 0:
        raise AssetError(
            f"next_path_id 造出的 {candidate} 与包内已有 pathID 撞了（或不是正数）",
            hint="包里的 pathID 必须先查重再用；撞了说明对象表被改坏了",
        )
    return candidate

# --------------------------------------------------------------------------- #
# 备份目录（红线：跟随游戏所在盘）
# --------------------------------------------------------------------------- #
def default_backup_dir(game_root: str | Path, project_root: str | Path | None = None) -> Path:
    """备份柜落点：有工程目录就用它，否则用**游戏根的同级** bamod_backup/backup。

    ⛔ 红线（工单 §5 / 设计稿 §9.2）：备份与工程目录**必须跟随游戏所在盘**，不许落 C 盘
    （历史教训：白占 6.95 GB）。_assert_same_drive 会机械地挡住跨盘。
    """
    base = Path(project_root) if project_root else Path(game_root).parent / "bamod_backup"
    return base / "backup"


def _assert_same_drive(target: Path, game_root: str | Path) -> None:
    """⛔ 备份 / 工程目录跟随游戏所在盘 —— 实现只有一份，住在 reverse_delta 里。

    本模块反过来 import reverse_delta（反向 Δ 是它的事）⇒ 守卫只能住那边，这里转调。
    """
    reverse_delta.assert_same_drive(target, game_root)


# --------------------------------------------------------------------------- #
# 写后复核
# --------------------------------------------------------------------------- #
def _stream_compare(
    old_reader: ufs.UfsReader,
    new_reader: ufs.UfsReader,
    keep: Sequence[tuple[int, int]],
) -> tuple[int, int, int]:
    """逐块比对两条解压流。返回 (总差异字节, 落在保留区间之外的差异字节, 新流 crc)。"""
    if len(old_reader.blocks) != len(new_reader.blocks):
        raise AssetError("新旧块数不一致（等长写回不该发生）")
    total_diff = 0
    outside = 0
    crc = 0
    for index, (a, b) in enumerate(zip(old_reader.blocks, new_reader.blocks, strict=True)):
        if a.uncompressed != b.uncompressed:
            raise AssetError(f"第 {index} 块解压长度变了（等长写回不该发生）")
        old_raw = _block_bytes(old_reader, index)
        new_raw = _block_bytes(new_reader, index)
        crc = zlib.crc32(new_raw, crc)
        if old_raw == new_raw:
            continue
        bstart = new_reader.block_dstart[index]
        for offset, (x, y) in enumerate(zip(old_raw, new_raw, strict=True)):
            if x == y:
                continue
            total_diff += 1
            absolute = bstart + offset
            if not any(lo <= absolute < hi for lo, hi in keep):
                outside += 1
    return total_diff, outside, crc & 0xFFFFFFFF


def _verify_added(
    old_reader: ufs.UfsReader, new_reader: ufs.UfsReader, plan: WritebackPlan
) -> dict[str, object]:
    """写后复核（W8）：新增对象那套事务的四条机械判据。

    * **J1 新 pid**：新 pathID 唯一、byteStart **8 字节对齐**、对象表**仍严格升序**；
    * **不变量**：data_offset == align16(48 + metadata_size)、data_offset % 16 == 0、
      metadata_size 与 data_offset 的增量都 == Δmeta、**Δmeta % 16 == 0 且 >= 24N**
     （⛔ 别改成「恰好 24N」：那样物理插入量 ≠ Δdata_offset，布局当场不成立）；
    * **J7 老对象**：每条老表项的 byteStart 新值 == 旧值 + (S − Δ)（B 口径下 = 原值）
      —— ⛔ 这条写成**口径无关**的形式，将来换摆法同一条尺子照样成立；
      老 byteSize 一个不变（被 growth/inplace 改的那几条除外）；
    * **总闸**：老对象**按新坐标逐字节读回** == 改前（漏平移/平移错时这条必红）。
    """
    old_node = old_reader.nodes[0]
    new_node = new_reader.nodes[0]
    sf_old = ufs.read_serialized_file(old_reader, old_node)
    sf_new = ufs.read_serialized_file(new_reader, new_node)
    old_rows = list(sf_old.objects)
    by_new = {row[1]: row for row in sf_new.objects}
    added_ids = [item[0] for item in plan.added_objects]
    problems: list[str] = []
    try:
        _assert_path_ids_ascending([row[1] for row in old_rows], "改前的对象表")
        _assert_path_ids_ascending([row[1] for row in sf_new.objects], "写回后的对象表")
    except AssetError as exc:
        problems.append(str(exc))
    duplicates = sorted({pid for pid in added_ids if added_ids.count(pid) > 1})
    if duplicates:
        problems.append(f"新增 pathID 有重复：{duplicates}")
    for path_id, _index, stored, size, type_id in plan.added_objects:
        row = by_new.get(path_id)
        if row is None:
            problems.append(f"新 pathID={path_id} 不在写回后的对象表里")
            continue
        if row[2] != stored:
            problems.append(f"新 pathID={path_id} 的 byteStart {row[2]:,} != 计划的 {stored:,}")
        if row[3] != size:
            problems.append(f"新 pathID={path_id} 的 byte_size {row[3]:,} != payload {size:,}")
        if row[4] != type_id:
            problems.append(f"新 pathID={path_id} 的 typeID {row[4]} != 申报的 {type_id}")
        if stored % 8:
            # J1 的判据字面就是 **8 字节对齐**（设计稿 §9.9 硬约束）—— 所以这里写死 8，
            # ⛔ 不跟着 ADDED_ALIGN 走：那条常量是**计划侧**的填充口径，尺子不该被它带偏。
            problems.append(f"新 pathID={path_id} 的 byteStart {stored} 不是 8 字节对齐")
    # 恒等式：bs_new == bs_old + (S − Δ)。B 口径下 S == Δ ⇒ 平移量恒为 0。
    shift = plan.meta_growth - ((plan.new_data_offset or 0) - (plan.old_data_offset or 0))
    touched = {
        row.path_id: row.new_bytes for row in plan.rows if row.path_id not in set(added_ids)
    }
    checked = 0
    for _i, path_id, stored, size, _tid in old_rows:
        row = by_new.get(path_id)
        if row is None:
            problems.append(f"老 pathID={path_id} 在写回后不见了")
            continue
        want_size = touched.get(path_id, size)
        if row[3] != want_size:
            problems.append(f"老 pathID={path_id} 的 byte_size {row[3]:,} != {want_size:,}")
        # ⚠ 被 growth **搬过**的老对象（byteSize 也变了的那几条）byteStart 是重写的，
        #    ⛔ 不套恒等式；其余每一条都必须满足 bs_new == bs_old + (S − Δ)。
        if want_size == size and row[2] != stored + shift:
            problems.append(
                f"老 pathID={path_id} 的 byteStart {row[2]:,} != 旧 {stored:,} + "
                f"(S−Δ)={shift:+,}"
            )
        checked += 1
    # 总闸：老对象按新坐标逐字节读回（抽 8 个；含首/末/最大）
    targets = {row.path_id for row in plan.rows}
    candidates = [row for row in old_rows if row[1] not in targets]
    picks: list[tuple[int, int, int, int, int]] = []
    if candidates:
        picks = [candidates[0], candidates[-1], max(candidates, key=lambda row: row[3])]
        step = max(len(candidates) // 4, 1)
        picks += [candidates[key] for key in range(0, len(candidates), step)]
    seen: set[int] = set()
    bad_reads: list[int] = []
    for _i, path_id, stored, size, _tid in picks:
        if _i in seen:
            continue
        seen.add(_i)
        row = by_new.get(path_id)
        if row is None:
            continue
        before = bytes(
            old_reader.read_range(old_node.offset + sf_old.data_offset + stored, size)
        )
        after = bytes(
            new_reader.read_range(new_node.offset + sf_new.data_offset + row[2], row[3])
        )
        if after != before:
            bad_reads.append(path_id)
    aligned_ok = sf_new.data_offset == _align16(ufs.SF_HEADER_SIZE + sf_new.metadata_size)
    aligned_text = "是" if aligned_ok else "否"
    growth_meta = sf_new.metadata_size - sf_old.metadata_size
    growth_offset = sf_new.data_offset - sf_old.data_offset
    invariants_ok = (
        aligned_ok
        and sf_new.data_offset % 16 == 0
        and growth_meta == plan.meta_growth
        # Δmeta = align16(24N) 是**布局成立的条件**（物理插入量必须 == Δdata_offset，
        # 而后者由 data_offset == align16(48+metadata_size) 决定 ⇒ 两者相等 ⟺ Δmeta % 16 == 0）。
        and plan.meta_growth % 16 == 0
        and plan.meta_growth >= 24 * len(added_ids)
        and growth_offset == plan.meta_growth
        and plan.meta_growth - growth_offset == plan.byte_start_shift
    )
    if not invariants_ok:
        problems.append(
            f"不变量不成立：data_offset {sf_new.data_offset:,} vs align16(48+metadata_size)="
            f"{_align16(ufs.SF_HEADER_SIZE + sf_new.metadata_size):,}；metadata_size 增量 "
            f"{growth_meta:,}、data_offset 增量 {growth_offset:,}（Δmeta {plan.meta_growth:,}"
            f"、应为 24N={24 * len(added_ids):,}、byteStart 平移 "
            f"{plan.meta_growth - growth_offset:+,}）"
        )
    return {
        "ok": not problems and not bad_reads,
        "detail": (
            f"对象表 {sf_old.object_count:,} → {sf_new.object_count:,} 条（新增 {len(added_ids)}："
            f"{added_ids}）；metadata_size {sf_old.metadata_size:,} → {sf_new.metadata_size:,}"
            f"（Δmeta {plan.meta_growth:+,}，补零 {plan.meta_growth - 24 * len(added_ids)} B）；"
            f"data_offset {sf_old.data_offset:,} → {sf_new.data_offset:,}"
            f"（== align16(48+metadata_size)：{aligned_text}）；"
            f"byteStart 平移（S−Δ）{shift:+,}；老表项逐条核对 {checked:,} 条；"
            f"抽样逐字节读回 {len(seen)} 个老对象（不一致 {len(bad_reads)} 个）"
            + ("；问题：" + "；".join(problems[:6]) if problems else "")
        ),
    }

def _verify_removed(
    old_reader: ufs.UfsReader,
    new_reader: ufs.UfsReader,
    plan: WritebackPlan,
) -> dict[str, object]:
    """**删除对象**的写后复核（2026-10-06 · 等长档的五条机械判据）。

    R1：① 新表 == 旧表**去掉那几条**（其余逐项：path_id / byteStart / byteSize / typeID 全等）；
        ② 老对象**按原坐标**逐字节读回 == 改前（坐标不变是本档的全部承诺）；
        ③ 被删 pathID：旧表 1 命中 / 新表 0 命中；
        ④ Δmetadata_size == Δdata_offset == Δfile_size == 0；
        ⑤ 元数据区规整式：new[start:] == old[start+24N:] + b"\x00"*24N（把「左移 + 补零」钉死）。
    """
    old_node, new_node = old_reader.nodes[0], new_reader.nodes[0]
    sf_old = ufs.read_serialized_file(old_reader, old_node)
    sf_new = ufs.read_serialized_file(new_reader, new_node)
    drop = {path_id for path_id, _index in plan.removed_objects}
    problems: list[str] = []
    kept = [row for row in sf_old.objects if row[1] not in drop]
    if len(sf_new.objects) != len(kept):
        problems.append(
            f"新表 {len(sf_new.objects):,} 条 != 旧表去掉 {len(drop)} 条后的 {len(kept):,} 条"
        )
    if sf_new.object_count != sf_old.object_count - len(drop):
        problems.append(
            f"表头对象数没跟着减：{sf_old.object_count:,} → {sf_new.object_count:,}"
            f"（应减 {len(drop)}）"
        )
    # ⚠ #6c：引用清理动过的那几条（AssetBundle）**允许 byteSize 变**（trim 会把对象写短）——
    #   只许这一格变，且新值必须等于引用清理自己记的那个数（下面 ref 那一段还会再核一次）。
    ref_rows = {
        pid: (old_bytes, new_bytes) for _m, pid, old_bytes, new_bytes, _c in plan.ref_cleanup
    }
    for old_row, new_row in zip(kept, sf_new.objects, strict=False):
        _i0, pid0, st0, sz0, t0 = old_row
        _i1, pid1, st1, sz1, t1 = new_row
        if pid0 in ref_rows:
            old_bytes, new_bytes = ref_rows[pid0]
            if (pid0, st0, t0) != (pid1, st1, t1) or sz0 != old_bytes or sz1 != new_bytes:
                problems.append(
                    f"引用清理动过的那一行不对：旧 ({pid0}, {st0:,}, {sz0:,}, {t0})"
                    f" vs 新 ({pid1}, {st1:,}, {sz1:,}, {t1})"
                    f"（期望 byteSize {old_bytes:,} → {new_bytes:,}）"
                )
                break
            continue
        if (pid0, st0, sz0, t0) != (pid1, st1, sz1, t1):
            problems.append(
                f"表项逐项不等：旧 ({pid0}, {st0:,}, {sz0:,}, {t0})"
                f" vs 新 ({pid1}, {st1:,}, {sz1:,}, {t1})"
            )
            break
    for path_id in sorted(drop):
        if any(row[1] == path_id for row in sf_new.objects):
            problems.append(f"被删 pathID={path_id} 还在新表里")
        if not any(row[1] == path_id for row in sf_old.objects):
            problems.append(f"被删 pathID={path_id} 在旧表里就找不到（尺子自己错了）")
    if sf_new.metadata_size != sf_old.metadata_size:
        problems.append(
            f"metadata_size 变了：{sf_old.metadata_size:,} → "
            f"{sf_new.metadata_size:,}（等长档不许变）"
        )
    if sf_new.data_offset != sf_old.data_offset:
        problems.append(
            f"data_offset 变了：{sf_old.data_offset:,} → {sf_new.data_offset:,}（等长档不许变）"
        )
    # ⚠ 容器文件大小**可以**变（重压那几块出来的长度会差几个字节，见 F7 的注释）——
    # 等长档要守的是**解压流长度**与节点大小，不是容器字节数。
    if new_reader.total_uncompressed != old_reader.total_uncompressed:
        problems.append(
            f"解压流长度变了：{old_reader.total_uncompressed:,} → "
            f"{new_reader.total_uncompressed:,}（等长档不许变）"
        )
    stride = sf_old.object_entry_stride
    count_off = old_node.offset + sf_old.object_table_offset - 4
    end = old_node.offset + ufs.SF_HEADER_SIZE + sf_old.metadata_size
    # ⚠ 这一条是**表后区**的整段比（不是"只看尾部是不是零" —— 那样抓不到左移错位）。
    #   ⚠ 锚点必须是**各自的表末**：新表末 = 表首 + 24×(新条数)，比旧表末**早 24N**；
    #   用旧表末去读新文件会读成长度差 24N（本轮实测踩到，内容其实都对）。
    #   ⛔ 它**不覆盖**「对象数」那一格（那格在表首之前）：对象数由上面那条断言兜
    #   （ruler 2026-10-06 指出：④b 绿 ≠ 对象数对）。
    old_table_end = old_node.offset + sf_old.object_table_offset + stride * sf_old.object_count
    new_table_end = new_node.offset + sf_new.object_table_offset + stride * sf_new.object_count
    tail_old = bytes(old_reader.read_range(old_table_end, end - old_table_end))
    want_tail = tail_old + b"\x00" * (stride * len(drop))
    got_tail = bytes(new_reader.read_range(new_table_end, end - new_table_end))
    rule_ok = got_tail == want_tail
    if not rule_ok:
        problems.append(
            f"表后区不是「旧 {len(tail_old)} B ＋ 补零 {stride * len(drop)} B」"
            f"（实得 {len(got_tail)} B）"
        )
    if new_node.size != old_node.size:
        problems.append(
            f"节点大小变了：{old_node.size:,} → {new_node.size:,}（等长档不许变；彻底档才允许）"
        )
    node_lo, node_hi = old_node.offset, old_node.offset + old_node.size
    if not node_lo <= count_off < end <= node_hi:
        problems.append("区间自检失败：计数字段 / 元数据末尾不在节点内")
    reads = 0
    bad_reads: list[int] = []
    candidates = [row for row in kept]
    picks = (
        [candidates[0], candidates[-1], max(candidates, key=lambda row: row[3])]
        if candidates
        else []
    )
    step = max(len(candidates) // 4, 1)
    picks += [candidates[key] for key in range(0, len(candidates), step)]
    seen: set[int] = set()
    for _i, path_id, stored, size, _t in picks:
        if path_id in seen:
            continue
        if path_id in ref_rows:
            continue                       # #6c：引用清理动过的对象由下面那段专门核
        seen.add(path_id)
        one = bytes(old_reader.read_range(old_node.offset + sf_old.data_offset + stored, size))
        two = bytes(new_reader.read_range(new_node.offset + sf_new.data_offset + stored, size))
        reads += 1
        if one != two:
            bad_reads.append(path_id)

    # ---- #6c：引用清理的写后复核（AssetBundle 里不许再有指向被删对象的 PPtr）----
    ref_note = ""
    if plan.ref_cleanup:
        meta_new = bytes(new_reader.read_range(new_node.offset, sf_new.metadata_size))
        try:
            schema_new = clone.read_type_table(
                meta_new, common_strings=clone.unitypy_common_strings()
            )
        except Exception as exc:  # noqa: BLE001 - 解不开就是红，别让复核自己炸掉
            problems.append(f"引用清理复核：新包的元数据解不开（{type(exc).__name__}: {exc}）")
            schema_new = None
        by_pid_new = {row[1]: row for row in sf_new.objects}
        cleaned_total = 0
        for mode, ab_pid, old_bytes, new_bytes, cleared in plan.ref_cleanup:
            cleaned_total += cleared
            row = by_pid_new.get(ab_pid)
            if row is None:
                problems.append(f"引用清理：AssetBundle(pathID={ab_pid}) 不在新表里")
                continue
            if row[3] != new_bytes:
                problems.append(
                    f"引用清理：AssetBundle(pathID={ab_pid}) 的 byteSize 是 {row[3]:,}，"
                    f"应当写 {new_bytes:,}"
                )
            if mode == "trim" and cleared and row[3] >= old_bytes:
                problems.append(
                    f"引用清理（trim）：对象没变短（{old_bytes:,} → {row[3]:,}）"
                )
            if mode == "null" and row[3] != old_bytes:
                problems.append(
                    f"引用清理（null）：对象长度变了（{old_bytes:,} → {row[3]:,}）"
                )
            if mode == "trim" and row[3] < old_bytes:
                # ⚠ 原地写短了：对象老区间里、新字节之后的那一段必须是**零**（死区）
                dead = bytes(
                    new_reader.read_range(new_node.offset + sf_new.data_offset + row[2] + row[3],
                                          old_bytes - row[3])
                )
                if any(dead):
                    problems.append(
                        f"引用清理（trim）：对象老区间里余下的 {old_bytes - row[3]:,} B 不是零"
                        f"（死区必须补零，否则读侧可能把旧字节当成对象的一部分）"
                    )
            if schema_new is None:
                continue
            payload = bytes(
                new_reader.read_range(new_node.offset + sf_new.data_offset + row[2], row[3])
            )
            nodes = schema_new.entries[row[4]].nodes
            tree = clone.read_object(payload, nodes)
            left = [
                ref
                for ref in clone.collect_pptrs(tree, nodes, owner=f"AssetBundle(pathID={ab_pid})")
                if int(ref.file_id) == 0 and int(ref.path_id) in drop
            ]
            if left:
                problems.append(
                    f"引用清理没做干净：还剩 {len(left)} 条指向被删对象（例：{left[0]}）"
                )
        ref_note = (
            f"；引用清理 {plan.ref_cleanup[0][0]}：处理 {cleaned_total} 条引用，"
            f"AssetBundle " + "、".join(
                f"{old:,}→{new:,} B" for _m, _p, old, new, _c in plan.ref_cleanup
            )
        )
    return {
        "ok": not problems and not bad_reads,
        "detail": (
            f"对象表 {sf_old.object_count:,} → {sf_new.object_count:,} 条（删 {len(drop)}："
            f"{sorted(drop)}）；metadata_size {sf_old.metadata_size:,}（不动）、data_offset "
            f"{sf_old.data_offset:,}（不动）、文件 {old_reader.file_size:,} B（不动）；"
            f"元数据区规整式（左移 {stride * len(drop):,} B + 尾零）"
            f"{'成立' if rule_ok else '**不成立**'}；老对象按原坐标抽样读回 {reads} 个"
            f"（不一致 {len(bad_reads)}）"
            + ref_note
            + ("；问题：" + "；".join(problems[:6]) if problems else "")
        ),
    }


def _post_verify(
    bundle: Path,
    old_reader: ufs.UfsReader,
    internal: _InternalPlan,
    plan: WritebackPlan,
    *,
    backup: Path | None,
    sha_before: str,
    catalog_path: Path | None,
    expected_crc: int,
    catalog_written: bool = True,
    bundle_name: str | None = None,
) -> dict[str, object]:
    """写后复核（F2~F7 / Δ 的 H2、H3、J1、J7）。

    backup=None 走**反向 Δ 那条链**（ADR 0007）：此时本函数在 os.replace **之前**被调用，
    比的是「旧文件 vs 临时文件」，catalog 也还没落盘（catalog_written=False），F5 改成 Δ 自证。
    """
    verify: dict[str, object] = {}
    ranges = internal.object_ranges
    layout = internal.delta
    with ufs.UfsReader(bundle) as new_reader:
        # F2 读回：目标对象逐字节 == 我们打算写进去的那份
        back_ok = True
        detail = []
        for span, rep in zip(ranges, internal.replication_of, strict=True):
            got = new_reader.read_range(span[0], span[1] - span[0])
            same = got == rep.payload
            back_ok = back_ok and same
            detail.append(f"[{span[0]:,}, {span[1]:,}) {'==' if same else '!='} 新值")
        verify["F2 读回"] = {
            "ok": back_ok,
            "detail": "UfsReader 能解析；" + "；".join(detail),
        }
        if layout is not None:
            # H3：**按平移后的坐标**比（工单 §2 ④）—— 长度变了，逐字节比整条流必然全红
            total_diff, outside, crc_disk = _delta_compare(
                old_reader, new_reader, layout.inserts, layout.reps
            )
            segments = _delta_segments(layout.inserts, old_reader.total_uncompressed)
            shown = []
            for lo, hi, shift, new_lo in segments[:4]:
                first, last = _hit_blocks(old_reader, lo, hi)
                shown.append(
                    f"旧流[{lo:,}, {hi:,}) 块#{first}~#{last} "
                    f"→ 新流[{new_lo:,}, {new_lo + (hi - lo):,})（平移 +{shift:,}）"
                )
            if len(segments) > 4:
                shown.append(f"…共 {len(segments)} 段")
            verify["H3 其它未变（平移后坐标）"] = {
                "ok": outside == 0,
                "diff_bytes": outside,
                "detail": (
                    f"逐段比对新旧解压流：总差异 {total_diff:,} B，其中落在"
                    f"「期望改动之外」的 {outside:,} B（要求 0）；"
                    + "；".join(shown)
                ),
            }
            expect_len = old_reader.total_uncompressed + sum(
                len(data) for _point, data in layout.inserts
            )
            new_len = sum(block.uncompressed for block in new_reader.blocks)
            nodes_ok = len(new_reader.nodes) == len(layout.new_nodes) and all(
                a.offset == b.offset and a.size == b.size and a.path == b.path
                for a, b in zip(new_reader.nodes, layout.new_nodes, strict=True)
            )
            verify["H2 块表 / 节点表"] = {
                "ok": (
                    new_len == expect_len
                    and new_reader.total_uncompressed == expect_len
                    and nodes_ok
                ),
                "detail": (
                    f"块表 {len(old_reader.blocks):,} → {len(new_reader.blocks):,} 块；"
                    f"块的解压长度和 {new_len:,} B == 旧流 {old_reader.total_uncompressed:,} + "
                    f"插入 {expect_len - old_reader.total_uncompressed:,} = {expect_len:,} B；"
                    f"节点表 {len(new_reader.nodes)} 个（偏移/大小/名字逐项一致"
                    f"={'是' if nodes_ok else '否'}）"
                ),
            }
            same_all = True
            checked = 0
            with old_reader.path.open("rb") as old_file, bundle.open("rb") as new_file:
                for new_index, old_index in sorted(layout.walk.verbatim_from.items()):
                    new_file.seek(new_reader.block_foff[new_index])
                    got = new_file.read(new_reader.blocks[new_index].compressed)
                    old_file.seek(old_reader.block_foff[old_index])
                    want = old_file.read(old_reader.blocks[old_index].compressed)
                    checked += 1
                    if got != want:
                        same_all = False
            verify["H2 只有 N 块重压"] = {
                "ok": same_all and checked == len(layout.walk.verbatim_from),
                "recompressed": len(layout.walk.recompressed),
                "verbatim": checked,
                "detail": (
                    f"新块表 {len(new_reader.blocks):,} 块：**重压 "
                    f"{len(layout.walk.recompressed)} 块**"
                    f"{list(layout.walk.recompressed)}，其余 {checked:,} 块的压缩字节"
                    f"逐字节 {'==' if same_all else '!='} 原文件里的那几块"
                ),
            }
            expected_growth = dict(layout.node_growth)
            actual_growth = {
                _node_basename(node.path): node.size - old.size
                for node, old in zip(new_reader.nodes, old_reader.nodes, strict=True)
                if node.size != old.size
            }
            object_delta = sum(row.new_bytes - row.old_bytes for row in plan.rows)
            verify["H2 节点增长（Δ）"] = {
                "ok": expected_growth == actual_growth,
                "detail": (
                    f"节点 size 增量 实测={actual_growth} 期望={expected_growth}；"
                    f"对象级 delta（**只算真正的对象行**）= {object_delta:+,} B"
                    f"（⛔ Δ 下不复用「对象级 delta == 0」那条 L1 判据 —— 它天然不成立）"
                ),
            }
            if plan.added_objects:
                verify["J1/J7 新增对象（表 / 对齐 / 读回）"] = _verify_added(
                    old_reader, new_reader, plan
                )
        else:
            # F3 其它未变：两条解压流逐块比，差异必须**全部**落在被替换区间里
            total_diff, outside, crc_disk = _stream_compare(old_reader, new_reader, ranges)
            verify["F3 其它未变"] = {
                "ok": outside == 0,
                "diff_bytes": outside,
                "detail": (
                    f"逐块比对解压流：总差异 {total_diff:,} B，其中落在这 {len(ranges)} 个"
                    f"被替换区间之外的 {outside:,} B（要求 0）"
                ),
            }
        if plan.removed_objects:
            verify["R1 删除对象"] = _verify_removed(old_reader, new_reader, plan)
        # G2 / G3：只有**流式区间**写回才成立（write_objects 改的就是 node[0]，这两条会红）
        if internal.kind == "stream":
            node_lo, node_hi = internal.serialized_range
            sha_node_old = _range_sha256(old_reader, node_lo, node_hi)
            sha_node_new = _range_sha256(new_reader, node_lo, node_hi)
            verify["G2 node[0] 未动"] = {
                "ok": sha_node_old == sha_node_new,
                "sha256_before": sha_node_old,
                "sha256_after": sha_node_new,
                "detail": (
                    f"node[0]（{internal.serialized_node_path}）[{node_lo:,}, {node_hi:,}) "
                    f"sha256 {sha_node_old[:16]}… "
                    f"{'==' if sha_node_old == sha_node_new else '!='} {sha_node_new[:16]}…"
                ),
            }
            verify["G3 node[1] 变了"] = {
                "ok": back_ok and outside == 0,
                "detail": (
                    f"目标区间逐字节 {'==' if back_ok else '!='} 新像素；"
                    f"区间之外的差异 {outside:,} B（要求 0）"
                ),
            }
        # F4 catalog：磁盘复算 == catalog 里写的值，双向
        size_disk = bundle.stat().st_size
        if catalog_path is None:
            crc_catalog = None
            size_catalog = None
        else:
            entry = catalog_entry(catalog_path, bundle_name or bundle.name)
            crc_catalog = entry.crc
            size_catalog = entry.bundle_size
        catalog_same = catalog_written and (
            (crc_catalog is None or crc_catalog == crc_disk)
            and (size_catalog is None or size_catalog == size_disk)
        )
        verify["F4 catalog"] = {
            "ok": crc_disk == expected_crc
            and size_disk == plan.new_file_size
            and (catalog_same or not catalog_written),
            "crc_disk": crc_disk,
            "crc_catalog": crc_catalog,
            "size_disk": size_disk,
            "size_catalog": size_catalog,
            "detail": (
                f"磁盘 crc32(逐块解压拼接) = {crc_disk}（期望 {expected_crc}）；"
                f"磁盘 {size_disk:,} B；"
                + (
                    f"catalog 里写的 m_Crc = {crc_catalog}；m_BundleSize = "
                    f"{size_catalog if size_catalog is None else format(size_catalog, ',')}"
                    if catalog_written
                    else "catalog 还没落盘 ⇒ 落盘后另有一条 F4b 复查"
                )
            ),
        }
    if backup is None:
        # F5'（ADR 0007）：可逆性不再靠整包副本，靠反向 Δ —— 落盘补丁后由调用方补 F5b 读数。
        verify["F5 反向 Δ"] = {
            "ok": True,
            "sha256_before": sha_before,
            "detail": "不拷整包：可逆性由工程目录里的反向 Δ 承担（落盘后见 F5b）",
        }
    else:
        sha_backup = _sha256(backup) if backup.is_file() else ""
        verify["F5 备份"] = {
            "ok": backup.is_file() and sha_backup == sha_before,
            "sha256_before": sha_before,
            "sha256_backup": sha_backup,
            "detail": f"{backup}；sha256 {'==' if sha_backup == sha_before else '!='} 改前",
        }
    # F7：**只有 L1 那条链**有这条判据（对象级 delta == 0）。
    # Δ 链的写后复核走 H2（节点增长 / 块表 / 只有 N 块重压）与 H3（平移后坐标比），
    # ⛔ 不复用 F7 —— 变长下「对象级 delta == 0」天然不成立，复用等于把读数弄成没意义。
    file_delta = plan.new_file_size - plan.old_file_size
    object_delta = sum(row.new_bytes - row.old_bytes for row in plan.rows)
    if layout is None:
        verify["F7 等长"] = {
            "ok": object_delta == 0,
            "delta": object_delta,
            "detail": (
                f"对象级 delta = {object_delta}（要求 0）；文件级 {plan.old_file_size:,} → "
                f"{plan.new_file_size:,} B（{file_delta:+,}，块重压造成的长度变化，不是对象变长）"
            ),
        }
    return verify


# --------------------------------------------------------------------------- #
# 写回
# --------------------------------------------------------------------------- #
def _write_bundle(
    bundle_path: str | Path,
    planner: Callable[..., _InternalPlan],
    *,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    allow_running_game: bool = False,
    progress: Any = None,
    token: Any = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """write_objects / write_stream **共用**的一趟：
    三道红线（没备份不许写 / 备份与工程目录跟随游戏盘 / 游戏在跑拦住）+ 拼装 + 替换 + 写后复核。

    **patch_store 给了就走 ADR 0007 那条链**（反向 Δ，⛔ 不拷整包；见 _write_bundle_delta）；
    没给才走旧行为（整包副本）—— 旧行为只留给还没搬过来的用例。

    planner 只决定「改哪一段」（对象表 or 流式区间），后面几步完全一样。

    ⚠️ allow_running_game（2026-10-03 真件实测）：
    实测**游戏跑着时对这个包做独占打开（CreateFile share=0）= LOCKED**，
    关掉游戏之后 = OK ⇒ **默认拦是对的**（Unity 加载 AssetBundle 时确实占着文件）。
    这个开关只留给"独立测过文件此刻确实自由"的场合（别的加载方式 / 别的机型），
    ⛔ 不许凭"游戏在跑但大概没事"就打开它 —— 要打开就必须先跑一次独占打开测出来。
    """
    started = time.time()
    bundle = Path(bundle_path)
    catalog = Path(catalog_path) if catalog_path is not None else None
    staged: _StagedDelta | None = None
    with ufs.UfsReader(bundle) as reader:
        internal = planner(reader, catalog)
        plan = internal.plan
        if dry_run:
            return WriteReport(
                bundle=bundle,
                bundle_name=plan.bundle_name,
                dry_run=True,
                plan=plan,
                seconds=time.time() - started,
            )
        if plan.blocked_reason:
            raise AssetError(
                f"写回被拦下：{plan.blocked_reason}",
                hint="干跑这一行必须先干净（级别 L1 + 磁盘 CRC 与 catalog 一致）",
            )
        if token is not None:
            token.raise_if_cancelled()

        # ⛔ 第三道红线：游戏进程占着文件时覆盖必失败（WinError 5），先机械地拦住
        # 只有真写这一步才需要它（干跑不碰游戏进程）。
        from ba_mod_maker.core.assets.game import is_game_running  # noqa: PLC0415

        game_running = bool(is_game_running())
        if game_running and not allow_running_game:
            raise AssetError(
                "游戏正在运行，bundle 可能被占着",
                hint="先关掉《断箭》（Steam 点退出）。实测：游戏跑着时这个包独占打开 = LOCKED，"
                "关掉 = OK ⇒ 必须关游戏",
            )

        if patch_store is not None:
            staged = _stage_delta_write(
                bundle=bundle,
                catalog=catalog,
                reader=reader,
                internal=internal,
                plan=plan,
                store=patch_store,
                label=patch_label or plan.bundle_name,
                started=started,
                game_running=game_running,
            )

        if patch_store is None:
            target_dir = (
                Path(backup_dir)
                if backup_dir
                else default_backup_dir(game_root or bundle.parent, project_root)
            )
            # ⛔ 第二道红线：备份 / 工程目录跟随游戏所在盘
            _assert_same_drive(target_dir, game_root or bundle.parent)
            target_dir.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d-%H%M%S")
            sha_before = _sha256(bundle)
            backup = target_dir / f"{bundle.name}.{stamp}.bak"
            shutil.copy2(bundle, backup)
            if _sha256(backup) != sha_before:
                raise AssetError(f"备份 sha256 与改前不一致：{backup}", hint="备份不可信，别继续写")

            catalog_patch: CatalogPatch | None = None
            catalog_backup: Path | None = None
            if catalog is not None:
                catalog_patch = _build_catalog_patch(
                    catalog, bundle.name, crc=plan.catalog_crc_new, bundle_size=plan.new_file_size
                )
                catalog_backup = target_dir / f"{catalog.name}.{stamp}.bak"

            tmp = bundle.with_name(bundle.name + ".baw.tmp")
            tmp.unlink(missing_ok=True)
            _write_container(reader, internal.assembly, tmp, internal.assembly.new_blocks)
        # ⚠️ 读源必须先**全部关掉**再替换：Windows 上还有句柄开着时 os.replace 直接 WinError 5
        #    （彩排实测踩过一次）。UnityPy 那一侧的句柄由调用方先关（见 writeback_unit_material）。
    if staged is not None:
        return _finish_delta_write(staged, progress=progress)
    try:
        gc.collect()  # 先把 UnityPy 那一侧的惰性句柄抖掉（见 _replace_with_retry）
        _replace_with_retry(tmp, bundle)
        if catalog is not None and catalog_patch is not None and catalog_backup is not None:
            shutil.copy2(catalog, catalog_backup)
            catalog.write_bytes(catalog_patch.after)
    except BaseException as exc:
        tmp.unlink(missing_ok=True)
        shutil.copy2(backup, bundle)
        if catalog_backup is not None and catalog is not None and catalog_backup.is_file():
            shutil.copy2(catalog_backup, catalog)
        if isinstance(exc, PermissionError):
            raise AssetError(
                f"替换 {bundle.name} 时被拒绝访问（WinError 5）：还有句柄占着它",
                hint="① 游戏在跑？② 调用方没关掉 UnityPy 的读源？两者都要先关掉再写",
            ) from exc
        raise
    if progress is not None:
        # 进度回调是可选的，没给就不该 import core.runner。
        from ba_mod_maker.core.runner import Progress  # noqa: PLC0415

        progress(Progress(message="写后复核", current=1, total=1))

    # 写后复核的「改前」基准就用**备份**（它就是改前那份；旧读源这时已经关了）
    with ufs.UfsReader(backup) as old_reader:
        verify = _post_verify(
            bundle,
            old_reader,
            internal,
            plan,
            backup=backup,
            sha_before=sha_before,
            catalog_path=catalog,
            expected_crc=plan.catalog_crc_new,
        )
        failed = [
            f"{key} {value.get('detail')}"
            for key, value in verify.items()
            if isinstance(value, Mapping) and not value.get("ok")
        ]
        if failed:
            raise AssetError(
                "写后复核没过：" + "；".join(failed),
                hint=f"备份在 {backup}，可以还原",
            )
        return WriteReport(
            bundle=bundle,
            bundle_name=plan.bundle_name,
            dry_run=False,
            plan=plan,
            verify=verify,
            backup=backup,
            backup_sha256=sha_before,
            catalog_backup=catalog_backup,
            wrote_bundle=True,
            wrote_catalog=catalog is not None,
            game_was_running=game_running,
            seconds=time.time() - started,
        )


def _catalog_check(
    catalog_path: Path, bundle_name: str, *, crc_disk: int, size_disk: int
) -> dict[str, object]:
    """catalog 落盘之后的复查（F4b）：磁盘真值 == catalog 里写的值，双向。"""
    entry = catalog_entry(catalog_path, bundle_name)
    ok = (entry.crc is None or entry.crc == crc_disk) and (
        entry.bundle_size is None or entry.bundle_size == size_disk
    )
    return {
        "ok": ok,
        "crc_disk": crc_disk,
        "crc_catalog": entry.crc,
        "size_disk": size_disk,
        "size_catalog": entry.bundle_size,
        "detail": (
            f"catalog 里 m_Crc = {entry.crc}；m_BundleSize = {entry.bundle_size}；"
            f"磁盘 {crc_disk} / {size_disk:,} B"
        ),
    }


def _rollback_from_patch(store: reverse_delta.PatchStore) -> str:
    """替换之后出事：拿刚落的补丁把文件撤回装前。

    ⚠ 撤销本身失败要**吼出来**（补丁留着，用户可以手工再撤），⛔ 不许吞掉。
    实现在 PatchStore.rollback（**一份**，外部库通道那条链共用）。
    """
    _ok, line = store.rollback()
    return line


@dataclass(slots=True)
class _StagedDelta:
    """Δ 那条链的中间态：**已经备好、还没替换**。

    为什么非得分两段：⛔ os.replace 要求旧文件的句柄**全部关掉**
    （Windows WinError 5，本轮实测踩到），所以「复核 + 落补丁」留在 with UfsReader(bundle) 里，
    「替换」挪到外面（_finish_delta_write）。
    """

    bundle: Path
    catalog: Path | None
    plan: WritebackPlan
    tmp: Path
    verify: dict[str, object]
    store: reverse_delta.PatchStore
    catalog_patch: CatalogPatch | None
    sha_before: str
    sha_after: str
    new_size: int
    patch_path: Path
    patch_bytes: int
    game_running: bool
    started: float


def _stage_delta_write(
    *,
    bundle: Path,
    catalog: Path | None,
    reader: ufs.UfsReader,
    internal: _InternalPlan,
    plan: WritebackPlan,
    store: reverse_delta.PatchStore,
    label: str,
    started: float,
    game_running: bool,
) -> _StagedDelta:
    """ADR 0007 那一趟的前半段：**反向 Δ** 取代整包副本，而且**先复核、后替换**。

    顺序（每一步出事，盘上都还是原样）：
    ① 记基线：原 [0, data_start) ＋ 这次会被重压的那些块的原始压缩字节；
    ② 结构自证：原头 ＋ 逐块必须首尾相接铺满整个旧文件（不成立就抛，一个字节都不写）；
    ③ 写临时文件（新容器）并算它的 sha256；
    ④ 拿**旧文件的读源**跟临时文件比（F2/F3/F4/F7，Δ 链还有 H2/H3/J1/J7）—— 不过就删临时文件走人；
    ⑤ 落盘补丁（工程目录 patches/，覆盖式；有旧补丁就把基线并进来）。
    ⇒ 替换与 catalog 落盘在 _finish_delta_write（那时旧文件的读源已经关了）。
    """
    assembly = internal.assembly
    size_before = bundle.stat().st_size
    sha_before = reverse_delta.sha256_of(bundle)
    builder = store.begin(label)
    placement = reverse_delta.verbatim_placement(
        len(reader.blocks),
        dirty=assembly.dirty_payloads,
        verbatim_from=assembly.verbatim_from,
    )
    target = builder.capture_bundle(
        reader, placement=placement, sha_before=sha_before, size_before=size_before
    )
    builder.self_check(target)

    catalog_patch: CatalogPatch | None = None
    if catalog is not None:
        catalog_patch = _build_catalog_patch(
            catalog, bundle.name, crc=plan.catalog_crc_new, bundle_size=plan.new_file_size
        )
        builder.capture_file(catalog, before=catalog_patch.before, after=catalog_patch.after)

    tmp = bundle.with_name(bundle.name + ".baw.tmp")
    tmp.unlink(missing_ok=True)
    _write_container(reader, assembly, tmp, assembly.new_blocks)
    sha_after = _sha256(tmp)
    new_size = tmp.stat().st_size
    verify = _post_verify(
        tmp,
        reader,
        internal,
        plan,
        backup=None,
        sha_before=sha_before,
        catalog_path=catalog,
        expected_crc=plan.catalog_crc_new,
        catalog_written=False,
        bundle_name=bundle.name,
    )
    failed = [
        f"{key} {value.get('detail')}"
        for key, value in verify.items()
        if isinstance(value, Mapping) and not value.get("ok")
    ]
    if failed:
        tmp.unlink(missing_ok=True)
        raise AssetError(
            "写后复核没过（**盘上什么都没动**）：" + "；".join(failed),
            hint="Δ 记录没落盘；修好再跑一次",
        )
    builder.set_after(target, sha_after=sha_after, size_after=new_size)
    patch_path = builder.commit()
    patch_bytes = patch_path.stat().st_size + store.blob_path.stat().st_size
    return _StagedDelta(
        bundle=bundle,
        catalog=catalog,
        plan=plan,
        tmp=tmp,
        verify=verify,
        store=store,
        catalog_patch=catalog_patch,
        sha_before=sha_before,
        sha_after=sha_after,
        new_size=new_size,
        patch_path=patch_path,
        patch_bytes=patch_bytes,
        game_running=game_running,
        started=started,
    )


def _finish_delta_write(staged: _StagedDelta, *, progress: Any = None) -> WriteReport:
    """Δ 那一趟的后半段：**读源已经关了**才替换；替换完写 catalog；出事用刚落的补丁撤回。"""
    bundle = staged.bundle
    catalog = staged.catalog
    verify = staged.verify
    try:
        gc.collect()  # 先把 UnityPy 那一侧的惰性句柄抖掉（见 _replace_with_retry）
        _replace_with_retry(staged.tmp, bundle)
        if catalog is not None and staged.catalog_patch is not None:
            catalog.write_bytes(staged.catalog_patch.after)
    except BaseException as exc:
        staged.tmp.unlink(missing_ok=True)
        undone = _rollback_from_patch(staged.store)
        if isinstance(exc, PermissionError):
            raise AssetError(
                f"替换 {bundle.name} 时被拒绝访问（WinError 5）：还有句柄占着它。{undone}",
                hint="① 游戏在跑？② 调用方没关掉读源？两者都要先关掉再写",
            ) from exc
        raise AssetError(f"写回中途失败：{exc}。{undone}", hint="看上一句撤销结果") from exc

    verify["F5b 反向 Δ"] = {
        "ok": staged.patch_path.is_file(),
        "patch": str(staged.patch_path),
        "patch_bytes": staged.patch_bytes,
        "detail": (
            f"补丁 {staged.patch_path}（{staged.patch_bytes:,} B；对照整包 "
            f"{staged.plan.old_file_size:,} B）；还原 = PatchStore.restore()"
            f"（先验 sha256 == 装后 {staged.sha_after[:16]}…）"
        ),
    }
    if catalog is not None:
        catalog_verify = _catalog_check(
            catalog, bundle.name, crc_disk=staged.plan.catalog_crc_new, size_disk=staged.new_size
        )
        verify["F4b catalog 已落盘"] = catalog_verify
        if not catalog_verify["ok"]:
            undone = _rollback_from_patch(staged.store)
            raise AssetError(f"catalog 落盘后复查没过。{undone}", hint="CRC 必须成对更新")
    if progress is not None:
        # 进度回调是可选的，没给就不该 import core.runner。
        from ba_mod_maker.core.runner import Progress  # noqa: PLC0415

        progress(Progress(message="写后复核", current=1, total=1))
    return WriteReport(
        bundle=bundle,
        bundle_name=staged.plan.bundle_name,
        dry_run=False,
        plan=staged.plan,
        verify=verify,
        backup=None,
        backup_sha256=staged.sha_before,
        catalog_backup=None,
        wrote_bundle=True,
        wrote_catalog=catalog is not None,
        game_was_running=staged.game_running,
        seconds=time.time() - staged.started,
        patch=staged.patch_path,
        patch_bytes=staged.patch_bytes,
    )


def write_objects(
    bundle_path: str | Path,
    changes: Sequence[ObjectChange],
    *,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    allow_running_game: bool = False,
    progress: Any = None,
    token: Any = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """W5 的那条链：**替换一个对象区间 → 重算 CRC → 更新 catalog**（W6 复用）。

    默认 dry_run=True（工单 §2：默认干跑）。真写时依次过三道红线：
    **⛔ 没备份不许写；⛔ 备份/工程目录跟随游戏盘；⛔ 写前确认游戏进程没占着文件**。
    """
    return _write_bundle(
        bundle_path,
        lambda reader, catalog: _plan(reader, changes, catalog_path=catalog),
        dry_run=dry_run,
        backup_dir=backup_dir,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        allow_running_game=allow_running_game,
        progress=progress,
        token=token,
        patch_store=patch_store,
        patch_label=patch_label,
    )


def write_stream(
    bundle_path: str | Path,
    changes: Sequence[StreamChange],
    *,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    allow_running_game: bool = False,
    progress: Any = None,
    token: Any = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """W6-B2 的链：**替换 .resS 里的一段等长像素 → 重算 CRC → 更新 catalog**。

    与 write_objects **同款三道红线**（没备份不许写 / 备份与工程目录跟随游戏盘 /
    游戏在跑拦住），外加两条只有它才有的写后复核：

    * **G2 node[0] 未动**：node[0] 区间在备份与写回后文件上的 sha256 必须相等；
    * **G3 node[1] 变了**：目标区间 == 新像素，且区间之外的差异 = 0。

    ⛔ 本单只做**非 Crunched**（format 10 DXT1 / 12 DXT5）；长度变了（Δ 级）一律报错退出。
    """
    return _write_bundle(
        bundle_path,
        lambda reader, catalog: _plan_stream(reader, changes, catalog_path=catalog),
        dry_run=dry_run,
        backup_dir=backup_dir,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        allow_running_game=allow_running_game,
        progress=progress,
        token=token,
        patch_store=patch_store,
        patch_label=patch_label,
    )


#: 删对象时怎么处理 AssetBundle 里指向它的引用（2026-10-06 · #6c）
REF_MODES = ("trim", "null", "keep")

#: AssetBundle 的 classID（真件 unitlabels / units 都是 142）。
CLASS_ASSETBUNDLE = 142


def _ref_mode_of(removals: Sequence[ObjectRemoval]) -> str:
    """一次删除里所有 ObjectRemoval 的 refs 必须一致；非法值直接抛。"""
    modes = {item.refs for item in removals}
    if len(modes) > 1:
        raise AssetError(
            f"同一次删除里 refs 不一致：{sorted(modes)}",
            hint="引用清理是**整个包**的一件事（AssetBundle 只有一个），⛔ 不许混着来",
        )
    mode = modes.pop() if modes else "trim"
    if mode not in REF_MODES:
        raise AssetError(
            f"ObjectRemoval.refs 只认 {REF_MODES}，拿到 {mode!r}",
            hint="trim=摘元素（默认）/ null=置空 / keep=老行为（只给对照用）",
        )
    return mode


def _clean_ab_refs(
    tree: Mapping[str, Any], nodes: Sequence[Any], drop: set[int], mode: str, *, owner: str
) -> tuple[dict[str, Any], int, list[str]]:
    """把 AssetBundle 字段树里指向 `drop` 的引用处理掉（返回 新树 / 处理了几条 / 说明）。

    两处（工单 #6c 点名的）：

      * `m_PreloadTable[j]`：m_FileID=0 且 m_PathID ∈ drop 的项 —— **同一 pathID 出现多次就全摘**；
      * `m_Container[i][1].asset`（AssetInfo 的 PPtr）—— 命中的**整条容器条目**摘掉（trim）
        或把它的 asset 置空（null）。

    trim 还要把**后面条目的 preloadIndex 重编号**（摘了元素，下标就变了）；
    最后用 clone.collect_pptrs **自证**：这棵树里不许再有指向 drop 的 PPtr（漏一处就抛）。
    """
    if mode == "keep":
        return dict(tree), 0, ["refs=keep：不动引用（负控/对照用）"]
    notes: list[str] = []
    cleared = 0
    out = dict(tree)
    preload = [
        dict(item) if isinstance(item, Mapping) else item
        for item in (tree.get("m_PreloadTable") or [])
    ]
    container = [dict(entry) for entry in (tree.get("m_Container") or [])]

    def hit(item: Any) -> bool:
        data = item or {}
        return (
            int(data.get("m_FileID", 0) or 0) == 0
            and int(data.get("m_PathID", 0) or 0) in drop
        )

    if mode == "trim":
        keep_idx = [i for i, item in enumerate(preload) if not hit(item)]
        dropped_idx = [i for i in range(len(preload)) if i not in set(keep_idx)]
        shift = {old: new for new, old in enumerate(keep_idx)}
        removed_any = bool(dropped_idx)
        new_container: list[Any] = []
        for entry in container:
            info = dict(entry.get("second") or {})
            if hit(info.get("asset")):
                cleared += 1
                continue                      # 整条容器条目跟着摘掉
            old_index = int(info.get("preloadIndex", 0) or 0)
            size = int(info.get("preloadSize", 0) or 0)
            if removed_any and old_index not in shift:
                raise AssetError(
                    f"{owner} 的容器条目 preloadIndex={old_index} 指向被摘掉的 preload 槽位",
                    hint="这条目的资产也应当被删；这种形状本版不敢猜 ⇒ 先别用 trim",
                )
            if removed_any:
                info["preloadIndex"] = shift[old_index]
                if size and (
                    shift.get(old_index + size - 1, info["preloadIndex"]) - info["preloadIndex"]
                ) != size - 1:
                    raise AssetError(
                        f"{owner} 的容器条目 preload 区间 [{old_index}, {old_index + size})"
                        " 跨过了被摘的槽位",
                        hint="重编号会把它指到别的资产上；本版 fail-closed",
                    )
            entry = dict(entry)
            entry["second"] = info
            new_container.append(entry)
        out["m_PreloadTable"] = [preload[i] for i in keep_idx]
        out["m_Container"] = new_container
        cleared += len(dropped_idx)
        notes.append(
            f"trim：摘掉 preload {len(dropped_idx)} 项 / 容器 {cleared - len(dropped_idx)} 条"
        )
    else:  # null
        for index, item in enumerate(preload):
            if hit(item):
                preload[index] = {"m_FileID": 0, "m_PathID": 0}
                cleared += 1
        new_container_null: list[Any] = []
        for entry in container:
            info = dict(entry.get("second") or {})
            if hit(info.get("asset")):
                info["asset"] = {"m_FileID": 0, "m_PathID": 0}
                entry = dict(entry)
                entry["second"] = info
                cleared += 1
            else:
                entry = dict(entry)
            new_container_null.append(entry)   # ⚠ 必须写回新列表（局部重绑定会把改动丢掉）
        out["m_PreloadTable"] = preload
        out["m_Container"] = new_container_null
        notes.append(f"null：置空 {cleared} 条引用")

    leftovers = [
        ref
        for ref in clone.collect_pptrs(out, nodes, owner=owner)
        if int(ref.file_id) == 0 and int(ref.path_id) in drop
    ]
    if leftovers:
        raise AssetError(
            f"{owner} 里还有 {len(leftovers)} 条指向被删对象的引用："
            + "；".join(str(ref) for ref in leftovers[:4]),
            hint="AssetBundle 的 m_Container / m_PreloadTable 必须与被删对象一起摘（#6c）",
        )
    return out, cleared, notes


def _ab_ref_entries(
    reader: ufs.UfsReader,
    node: ufs.Node,
    info: ufs.SerializedFileInfo,
    drop: set[int],
    mode: str,
) -> tuple[
    list[tuple[PlanRow, ufs.Replacement, tuple[int, int]]],
    list[tuple[int, int, int, int, int]],
]:
    """引用清理：返回（额外替换条目，[(ab_pid, 旧表下标, 旧字节, 新字节, 处理条数)]）。

    trim 下对象**变短**：原地写更短的 payload ＋ 余下补零（**等长**）；
    它自己那条表项的 byteSize 由 _removal_entries 折进元数据那段 payload 里改
    （⚠ 不能单独发一个 Replacement —— 那一格落在元数据区里，会与删除那段重叠）。
    """
    if mode == "keep":
        return [], []
    meta = bytes(reader.read_range(node.offset, info.metadata_size))
    schema = clone.read_type_table(meta, common_strings=clone.unitypy_common_strings())
    out: list[tuple[PlanRow, ufs.Replacement, tuple[int, int]]] = []
    infos: list[tuple[int, int, int, int, int]] = []
    for index, path_id, stored, size, type_id in info.objects:
        # ⚠ 合成夹具常常 type_count = 0（没有类型表）却把表项 typeID 写成 1 ⇒ 会越界。
        #   类型表里没有这一项 ⇒ 它的类我们根本不知道 ⇒ 不可能是 AssetBundle，跳过。
        if type_id >= len(schema.entries):
            continue
        if schema.entries[type_id].class_id != CLASS_ASSETBUNDLE:
            continue
        start = node.offset + info.data_offset + stored
        payload = bytes(reader.read_range(start, size))
        nodes = schema.entries[type_id].nodes
        try:
            tree = clone.read_object(payload, nodes)
        except Exception as exc:  # 解不开就别写，报清楚是哪个对象
            raise AssetError(
                f"AssetBundle(pathID={path_id}) 的对象字节按类型树解不开："
                f"{type(exc).__name__}: {exc}",
                hint="对象表里的 typeID 与类型表对不上，或者这个包本来就不干净；⛔ 不许猜着改",
            ) from exc
        new_tree, cleared, notes = _clean_ab_refs(
            tree, nodes, drop, mode, owner=f"AssetBundle(pathID={path_id})"
        )
        if not cleared:
            continue
        new_payload = clone.write_object(new_tree, nodes)
        if mode == "null" and len(new_payload) != len(payload):
            raise AssetError(
                f"置空模式改动了对象长度（{len(payload):,} → {len(new_payload):,}）",
                hint="置空的定义就是「长度不变」；变了说明这条链选错了",
            )
        if mode == "trim":
            if len(new_payload) > len(payload):
                raise AssetError(
                    f"摘元素之后反而变长（{len(payload):,} → {len(new_payload):,}）",
                    hint="等长档装不下 ⇒ 这种形状得另想一档",
                )
            body = new_payload + b"\x00" * (len(payload) - len(new_payload))
        else:
            body = new_payload
        out.append(
            (
                PlanRow(
                    path_id=path_id,
                    name=f"AssetBundle 引用清理（{mode}：{'；'.join(notes)}）",
                    type_name="AssetBundle",
                    old_bytes=size,
                    new_bytes=size,          # ⚠ 替换区间是**等长**的（余下留死区）
                    level=LEVEL_EQUAL,
                    block_first=-1,
                    block_last=-1,
                    stream_start=start,
                ),
                ufs.Replacement(start, start + size, body),
                (start, start + size),
            )
        )
        infos.append((path_id, index, size, len(new_payload), cleared))
    return out, infos


def _removal_entries(
    reader: ufs.UfsReader,
    node: ufs.Node,
    info: ufs.SerializedFileInfo,
    removals: Sequence[ObjectRemoval],
) -> tuple[
    list[tuple[PlanRow, ufs.Replacement, tuple[int, int]]],
    tuple[tuple[int, int], ...],
    tuple[tuple[str, int, int, int, int], ...],
]:
    """「删除对象」= **等长**重写元数据区的尾巴。

    定律（三步，长度不变）：表里摘掉那 N 条 ⇒ 表后的元数据（script types / externals / ref types /
    尾隙）**整体左移** 24N ⇒ 声明区尾部 [48+M−24N, 48+M) **补 24N 个零**。

    为什么可以这么干（两条实测依据）：
    * ufs.read_serialized_file 的硬对账只查**越界**
      （cur.pos 超过 48+metadata_size 才报错，见 ufs.py:580）；
    * 这份格式的声明区尾部**本来就有隙** —— units 真件有 24,504 B 的「类型树池」不被解析器消费。

    于是删除**完全落在元数据区里**：整条流等长（L1）、data_offset 不变、所有老对象的
    byteStart 与数据区**一个字节都不用动**。被删对象的字节留成死区（安全档）。
    """

    if not removals:
        raise AssetError("没有任何要删的对象", hint="至少要给一个 ObjectRemoval")
    stride = info.object_entry_stride
    by_pid: dict[int, int] = {}
    for index, path_id, _stored, _size, _type_id in info.objects:
        by_pid.setdefault(path_id, index)
    rows_to_drop: list[int] = []
    wanted: list[int] = []
    for item in removals:
        index = by_pid.get(item.path_id)
        if index is None:
            raise KeyError(f"{node.path} 的对象表里没有 pathID={item.path_id}（没得删）")
        wanted.append(item.path_id)
        rows_to_drop.append(index)
    pairs = tuple(sorted(zip(wanted, rows_to_drop, strict=True), key=lambda item: item[1]))
    rows_unique = sorted({index for _pid, index in pairs})
    # ⛔ 不许把对象删空（2026-10-06 由 objadd 的负控刨出来）：plan 阶段就拦，
    #    别等 write 阶段被一个与语义无关的越界断言偶然挡住（那时补丁与临时文件已经写出去了）。
    if len(rows_unique) >= info.object_count:
        raise AssetError(
            f"要把对象全删光（{len(rows_unique)}/{info.object_count} 条）",
            hint="对象表空了就没有文件层入口了；要删就删到至少剩一条",
        )
    # ⚠ 锚点必须落在**对象数那个 int32 字段**上（对象表第一条之前 4 字节）：
    # 只删行、不减这个计数 ⇒ 读者照旧按旧条数读，把后面的字节当表项（本轮实测踩到）。
    # 计数字段就在表首之前，但中间可能有 0~3 字节的 4 字节对齐填充（解析器 cur.align(4)），
    # 而且它是**小端**（SF 头是大端，头之后的 body 按头里的 endian 标志走）。
    flag = bytes(reader.read_range(node.offset + 16, 1))[0]
    if flag != 0:
        raise AssetError(
            f"这个 SerializedFile 的 endian 标志是 {flag}（非小端）",
            hint="本档的等长删除只在实测过的小端件上成立 ⇒ ⛔ 不许写",
        )
    first_row_at = node.offset + info.object_table_offset
    count_off = -1
    for back in range(4, 8):
        candidate = first_row_at - back
        if candidate < node.offset:
            continue
        (value,) = struct.unpack("<I", bytes(reader.read_range(candidate, 4)))
        if value == info.object_count:
            count_off = candidate
            break
    if count_off < 0:
        raise AssetError(
            f"表首之前 4~7 字节里找不到等于 {info.object_count} 的计数字段",
            hint="表首形状与解析器不符；等长定律不成立 ⇒ ⛔ 不许写",
        )
    head_len = first_row_at - count_off  # 计数(4) + 对齐填充(0..3)，原样保留
    end = node.offset + ufs.SF_HEADER_SIZE + info.metadata_size
    region = bytes(reader.read_range(count_off, end - count_off))
    keep = bytearray(region[:head_len])
    cursor = head_len
    for index in rows_unique:
        at = head_len + index * stride
        keep += region[cursor:at]
        cursor = at + stride
    keep += region[cursor:]
    start = count_off

    # ---- #6c：AssetBundle 里指向被删对象的引用（m_Container / m_PreloadTable）----
    mode = _ref_mode_of(removals)
    drop = {path_id for path_id, _index in pairs}
    ref_entries, ref_infos = _ab_ref_entries(reader, node, info, drop, mode)

    packed = bytearray(
        struct.pack("<I", info.object_count - len(rows_unique))
        + bytes(keep[4:])
        + b"\x00" * (stride * len(rows_unique))
    )
    ref_cleanup: list[tuple[str, int, int, int, int]] = []
    for ab_pid, ab_index, old_size, new_size, cleared in ref_infos:
        if new_size != old_size:
            # trim：对象变短 ⇒ 就地改它自己那条表项的 byteSize。
            # ⚠ 这一格落在元数据区里 ⇒ 折进这一段 payload 改；⛔ 不许另发一个 Replacement
            #   （会与这一段重叠 ⇒ _plan_replacements 直接抛「替换区间重叠」）。
            new_index = ab_index - sum(1 for index in rows_unique if index < ab_index)
            struct.pack_into("<i", packed, head_len + new_index * stride + 16, new_size)
        ref_cleanup.append((mode, ab_pid, old_size, new_size, cleared))
    payload = bytes(packed)
    if len(payload) != end - start:
        raise AssetError(
            f"删除后的元数据尾巴 {len(payload):,} B != 原区间 {end - start:,} B",
            hint="等长定律被破坏；⛔ 不许写",
        )
    block_first, block_last = _hit_blocks(reader, start, end)
    row = PlanRow(
        path_id=pairs[0][0],
        name=f"删除 {len(rows_unique)} 个对象",
        type_name="ObjectTable",
        old_bytes=end - start,
        new_bytes=len(payload),
        level=LEVEL_EQUAL,
        block_first=block_first,
        block_last=block_last,
        stream_start=start,
    )
    return [
        (row, ufs.Replacement(start, end, payload), (start, end)),
        *ref_entries,
    ], pairs, tuple(ref_cleanup)


def _plan_removals(
    reader: ufs.UfsReader,
    removals: Sequence[ObjectRemoval],
    *,
    catalog_path: Path | None,
) -> _InternalPlan:
    node = _serialized_node(reader)
    info = ufs.read_serialized_file(reader, node)
    entries, pairs, ref_cleanup = _removal_entries(reader, node, info, removals)
    planned = _plan_replacements(
        reader,
        entries,
        catalog_path=catalog_path,
        # ⚠ 元数据守卫这里**故意给空区间**：删除动的就是元数据区，而且它由 _removal_entries
        # 自证等长（长度变了会当场抛）。其余替换仍然受守卫保护（这条链只服务删除）。
        meta_lo=0,
        meta_hi=0,
    )
    plan = replace(planned.plan, removed_objects=pairs, ref_cleanup=ref_cleanup)
    return _InternalPlan(
        plan=plan,
        replication_of=planned.reps,
        assembly=planned.assembly,
        object_ranges=planned.ranges,
        serialized_node_path=node.path,
        serialized_range=(node.offset, node.offset + node.size),
        kind="objects",
    )


def plan_removals(
    bundle_path: str | Path,
    removals: Sequence[ObjectRemoval],
    *,
    catalog_path: str | Path | None = None,
) -> WritebackPlan:
    """**干跑**：算出「删除对象」那一行（一个字节都不写）。"""
    with ufs.UfsReader(bundle_path) as reader:
        internal = _plan_removals(
            reader,
            removals,
            catalog_path=Path(catalog_path) if catalog_path is not None else None,
        )
        return internal.plan


def write_removals(
    bundle_path: str | Path,
    removals: Sequence[ObjectRemoval],
    *,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    allow_running_game: bool = False,
    progress: Any = None,
    token: Any = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """**删除对象**那条链（等长档）：与 write_objects **同一条** _write_bundle（三道红线、

    拼装、替换、写后复核全部复用），只多一条 R1 复核（见 _verify_removed）。
    """
    return _write_bundle(
        bundle_path,
        lambda reader, catalog: _plan_removals(reader, removals, catalog_path=catalog),
        dry_run=dry_run,
        backup_dir=backup_dir,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        allow_running_game=allow_running_game,
        progress=progress,
        token=token,
        patch_store=patch_store,
        patch_label=patch_label,
    )


# --------------------------------------------------------------------------- #
# ⑤ 写回面板的唯一入口：目标键 -> 材质 -> 一个浮点 -> 干跑 / 执行
# --------------------------------------------------------------------------- #
def _aa_paths(game_root: str | Path) -> tuple[Path, Path]:
    # core 的路径口径只在真正开包时才要（asset_kernel 的其它部分零 core 依赖）。
    from ba_mod_maker.core.assets.game import bundle_dir, catalog_file  # noqa: PLC0415

    root = Path(game_root)
    return bundle_dir(root), catalog_file(root)


def locate_unit_asset(game_root: str | Path, unit_key: str) -> tuple[str, tuple[str, ...]]:
    """地址键 -> (资产路径, 候选包)。口径与 blender_addon/importer.py::locate_unit **一致**
    （两边都走 core 的 AddressablesCatalog；插件侧不开包，这里也不开）。"""
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog  # noqa: PLC0415

    _bundle_dir, catalog_path = _aa_paths(game_root)
    # ⚠ catalog.json 住在 aa/ 下，**不在 aa/PC/** 下（game.py 的注释：实测踩过一次）。
    catalog = AddressablesCatalog.load(catalog_path.parent)
    if catalog is None:
        raise FileNotFoundError(f"读不到 catalog：{catalog_path}")
    hits = catalog.lookups(unit_key)
    if not hits:
        raise KeyError(f"catalog 里没有地址键 {unit_key!r}（共 {catalog.key_count} 个键）")
    assets = sorted({asset for asset, _bundle in hits})
    if len(assets) != 1:
        raise ValueError(f"地址键 {unit_key!r} 指向 {len(assets)} 个资产：{assets}")
    bundles = tuple(sorted({bundle for _asset, bundle in hits if bundle}))
    return assets[0], bundles


def writeback_unit_material(
    game_root: str | Path,
    unit_key: str,
    *,
    material_name: str,
    param: str,
    value: float,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """⑤ 写回面板的唯一入口：目标键 + 材质名 + 浮点名 + 新值 -> 干跑 / 真写。

    链接：unit_key -> catalog 定位 -> 唯一命中包（m_Container 复核）-> prefab 树 -> 材质
    -> 新对象字节（等长）-> writer 写回。
    """
    # UnityPy 是可选依赖；这一层只在用户真的点「干跑 / 执行」时才拉起来。
    from ba_mod_maker.asset_kernel.unitypy_source import open_unique_source  # noqa: PLC0415

    asset_path, candidates = locate_unit_asset(game_root, unit_key)
    bundle_dir_path, catalog_path = _aa_paths(game_root)
    with open_unique_source(bundle_dir_path, candidates, asset_path) as (source, bundle_name):
        _nodes, materials = source.read_prefab_nodes(asset_path)
        target_id: int | None = None
        available: list[str] = []
        for path_id, material in materials.items():
            name = str(material.get("name") or "")
            available.append(name)
            if name == material_name:
                target_id = int(path_id)
        if target_id is None:
            raise KeyError(f"{unit_key} 的材质里没有 {material_name!r}；有 {sorted(available)}")
        change = build_material_change(source, target_id, param=param, value=value)
    # ⚠️ UnityPy 的读源必须先关掉（with 退出）再写：句柄开着时 os.replace 会 WinError 5。
    return write_objects(
        bundle_dir_path / bundle_name,
        [change],
        dry_run=dry_run,
        backup_dir=backup_dir,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        patch_store=patch_store,
        patch_label=patch_label,
    )


# --------------------------------------------------------------------------- #
# ③ 涂装面板的唯一入口：目标键 -> 材质 -> 贴图槽 -> 换 .resS 里的像素字节
# --------------------------------------------------------------------------- #
def texture_slot_path_id(tree: Mapping[str, Any], slot: str) -> int:
    """材质 typetree 的 m_TexEnvs -> 该槽 PPtr 的 pathID（③ 面板定位贴图的唯一入口）。

    * 没有这个槽 -> KeyError（与 set_material_texture 同口径，⛔ 不许静默返回 0）；
    * 槽在但 PPtr 是空的（pathID = 0）-> AssetError（拿 0 当 pathID 会改错对象）。
    """
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        raise AssetError("这个材质没有 m_SavedProperties", hint="传进来的不是材质 typetree")
    tex_envs = props.get("m_TexEnvs")
    if not isinstance(tex_envs, Sequence):
        raise AssetError("这个材质没有 m_TexEnvs")
    names: list[str] = []
    for entry in tex_envs:
        if len(entry) < 2:
            continue
        key = str(entry[0])
        names.append(key)
        if key != slot:
            continue
        value = entry[1]
        pptr = (
            value.get("m_Texture")
            if isinstance(value, Mapping)
            else getattr(value, "m_Texture", None)
        )
        if isinstance(pptr, Mapping):
            path_id = int(pptr.get("m_PathID", 0) or 0)
        elif pptr is None:
            path_id = 0
        else:
            path_id = int(getattr(pptr, "m_PathID", 0) or 0)
        if not path_id:
            raise AssetError(
                f"贴图槽 {slot!r} 存在但 PPtr 是空的（pathID = 0）",
                hint="这个槽没指任何贴图 —— ⛔ 不许拿 0 去当 pathID 用",
            )
        return path_id
    raise KeyError(f"材质里没有贴图槽 {slot!r}（共 {len(names)} 个：{names[:8]}）")


def writeback_texture_file(
    game_root: str | Path,
    unit_key: str,
    *,
    material_name: str,
    slot: str,
    payload: bytes,
    dry_run: bool = True,
    project_root: str | Path | None = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """③ 涂装面板的唯一入口（与 W5 的 writeback_unit_material 对称）。

    链接：unit_key -> catalog 定位 -> 唯一命中包（m_Container 复核）-> 材质 typetree
    -> texture_slot_path_id 取槽的 pathID -> 读该 Texture2D 的 m_StreamData
    -> build_stream_change（长度不符就抛）-> write_stream。
    """
    # UnityPy 是可选依赖；这一层只在用户真的点「干跑 / 执行」时才拉起来。
    from ba_mod_maker.asset_kernel.unitypy_source import open_unique_source  # noqa: PLC0415

    asset_path, candidates = locate_unit_asset(game_root, unit_key)
    bundle_dir_path, catalog_path = _aa_paths(game_root)
    with open_unique_source(bundle_dir_path, candidates, asset_path) as (source, bundle_name):
        _nodes, materials = source.read_prefab_nodes(asset_path)
        target_id: int | None = None
        available: list[str] = []
        for path_id, material in materials.items():
            name = str(material.get("name") or "")
            available.append(name)
            if name == material_name:
                target_id = int(path_id)
        if target_id is None:
            raise KeyError(f"{unit_key} 的材质里没有 {material_name!r}；有 {sorted(available)}")
        texture_id = texture_slot_path_id(source.read_typetree(target_id), slot)
        texture_name = str(source.read_typetree(texture_id).get("m_Name") or "")
        # reader 传 **source**（不是 source.reader）：多一层权威口径复核（见 build_stream_change）。
        change = replace(build_stream_change(source, texture_id, payload), name=texture_name)
    # ⚠️ UnityPy 的读源必须先关掉（with 退出）再写：句柄开着时 os.replace 会 WinError 5。
    return write_stream(
        bundle_dir_path / bundle_name,
        [change],
        dry_run=dry_run,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        patch_store=patch_store,
        patch_label=patch_label,
    )


# --------------------------------------------------------------------------- #
# ① W7-A 的唯一入口：Texture2D 换格式（29 DXT5Crunched -> 12 DXT5，**变长**）
# --------------------------------------------------------------------------- #
def _stream_append_offset(bundle_path: str | Path, node_path: str) -> int:
    """**先把追加落位**：算出该流式节点尾追加的**节点内新偏移**（一个字节都不写）。

    ⚠ 这个顺序是硬要求（工单 §2 ① 的要害）：m_StreamData.offset 必须指向
    「追加之后」的那个位置，所以必须**先**知道落点、**再**改对象字节。

    口径与 _plan_delta 的追加分支**同一个**（都按 STREAM_APPEND_ALIGN = 16 对齐）：
    这条入口一次只追加**一个** payload，所以落点 = 对齐后的 node.size。
    """
    with ufs.UfsReader(bundle_path) as reader:
        node = _stream_node(reader, node_path)
        if node.size <= 0:
            raise AssetError(f"节点 {node.path} 的 size 是 0，没有可追加的尾部")
        return node.size + (-node.size) % STREAM_APPEND_ALIGN


def writeback_unit_texture_format(
    game_root: str | Path,
    unit_key: str,
    *,
    material_name: str,
    slot: str,
    rgba: bytes,
    format_id: int,
    dry_run: bool = True,
    project_root: str | Path | None = None,
    patch_store: reverse_delta.PatchStore | None = None,
    patch_label: str = "",
) -> WriteReport:
    """① 的唯一入口（面板 / 命令行都走它）：把一个贴图槽换成**另一种格式**。

    链接（与 W6 的 writeback_texture_file 对称，但这次是**变长**）：
    unit_key -> catalog 定位 -> 唯一命中包 -> 材质 -> 槽 -> Texture2D
    -> encode_dxt（etcpak，逐级编到 1x1）-> 先算追加落点 -> 改四个字段
    -> DeltaChange（inplace 改对象 + appends 追加像素）-> write_delta。

    ⚠ 工单 §7 坑 2：**四个字段 + .resS 里那一段必须落在同一次写回里** ——
    所以 inplace 与 appends 在同一个 DeltaChange 里，⛔ 不许分两次写。
    """
    from ba_mod_maker.asset_kernel import textures  # noqa: PLC0415
    from ba_mod_maker.asset_kernel.unitypy_source import open_unique_source  # noqa: PLC0415

    asset_path, candidates = locate_unit_asset(game_root, unit_key)
    bundle_dir_path, catalog_path = _aa_paths(game_root)
    with open_unique_source(bundle_dir_path, candidates, asset_path) as (source, bundle_name):
        _nodes, materials = source.read_prefab_nodes(asset_path)
        target_id: int | None = None
        available: list[str] = []
        for path_id, material in materials.items():
            name = str(material.get("name") or "")
            available.append(name)
            if name == material_name:
                target_id = int(path_id)
        if target_id is None:
            raise KeyError(f"{unit_key} 的材质里没有 {material_name!r}；有 {sorted(available)}")
        texture_id = texture_slot_path_id(source.read_typetree(target_id), slot)
        obj = source.object_at(texture_id)
        if obj is None:
            raise KeyError(f"这个包里没有 pathID={texture_id} 的贴图对象")
        original = obj.get_raw_data()
        tree = obj.read_typetree()
        if obj.save_typetree(tree) != original:
            raise AssetError(
                f"pathID={texture_id} 的 Texture2D typetree 往返不是逐字节相同",
                hint="这个对象的序列化不可复现 ⇒ 改它会连带改坏别的字段，本单不许写",
            )
        fields = textures.read_texture_fields(tree)
        width, height, mips = fields["width"], fields["height"], fields["mip_count"]
        raster = bytes(rgba)
        expect_rgba = width * height * 4
        if len(raster) != expect_rgba:
            raise ValueError(
                f"rgba 字节数不符：{len(raster):,} B != {width}x{height}x4 = {expect_rgba:,} B"
            )
        payload = textures.encode_dxt(raster, width, height, int(format_id), mips)
        # m_CompleteImageSize 是**未压缩总量**（工单 §7 坑 3）：换成非 Crunched 之后
        # 它就等于整条 mip 链的 DXT 字节数（== 新的 m_StreamData.size）。
        complete = textures.dxt_payload_size(width, height, int(format_id), mips)
        stream_path = fields["stream"]["path"]
        if not stream_path:
            raise AssetError(f"贴图 pathID={texture_id} 没有 m_StreamData.path —— 不是流式贴图")
        # ⚠ 先算追加落点，再改对象（m_StreamData.offset 用新的节点内偏移）
        new_offset = _stream_append_offset(bundle_dir_path / bundle_name, stream_path)
        new_tree = textures.set_texture_streaming(
            tree,
            format_id=int(format_id),
            complete_image_size=complete,
            offset=new_offset,
            size=len(payload),
        )
        new_bytes = obj.save_typetree(new_tree)
        if len(new_bytes) != len(original):
            raise AssetError(
                f"改四个字段之后对象长度 {len(original):,} → {len(new_bytes):,} B（不是等长）",
                hint="四个字段都是定长标量，长度必须不变；变了说明 typetree 写坏了",
            )
        texture_name = str(tree.get("m_Name") or f"<texture {texture_id}>")
        change = DeltaChange(
            inplace=(
                ObjectChange(
                    path_id=texture_id,
                    payload=new_bytes,
                    name=texture_name,
                    type_name="Texture2D",
                ),
            ),
            appends=(
                StreamAppend(
                    node_path=stream_path,
                    payload=payload,
                    owner_path_id=int(texture_id),
                ),
            ),
        )
    # ⚠️ UnityPy 的读源必须先关掉（with 退出）再写：句柄开着时 os.replace 会 WinError 5。
    return write_delta(
        bundle_dir_path / bundle_name,
        change,
        dry_run=dry_run,
        project_root=project_root,
        game_root=game_root,
        catalog_path=catalog_path,
        patch_store=patch_store,
        patch_label=patch_label,
    )

