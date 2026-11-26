r"""W9-B · 从零构建一个最小 UnityFS 包（Texture2D + AssetBundle〔＋可选 Sprite〕）。

## 这一模块解决什么

设计稿 §9.5「从零构建全新 bundle」的落点：按 schema 自己序列化出一个合法的 UnityFS 包
（容器头 + 块表 + 节点表 + SerializedFile + 对象）。三层各自独立：

    build_unityfs()                 UnityFS 容器：头 / 块表 / 节点表 / 块负载
    serialize_serialized_file()     头 / 类型表 / 对象表 / 尾部（编码交给 metadata_writer）
    build_texture_bundle()          Texture2D + AssetBundle（＋ Stage 2 的 Sprite）

**类型树不是自己写的**：从**真包**里把该 classID 的类型表项**逐字节照抄**过来
（`enable_type_tree = True` ⇒ 树内嵌在包里，设计稿 §9.5 第 2 步），
再用 `clone.write_object()` 按这棵树序列化**我们自己填的值**（Texture2D / AssetBundle 都是）。

## 两档形态（Stage 1 / Stage 2）

| | 包内对象 | 容器条目 |
|---|---|---|
| **Stage 1** | Texture2D + AssetBundle | 1 条（路径 -> Texture2D） |
| **Stage 2** | Texture2D + **Sprite** + AssetBundle | **同一条路径 2 条** |

★ **为什么必须 Stage 2**：游戏 UI 的图是 `Image`/`Sprite`。只改 Texture2D 那条 entry 时，
游戏仍旧从老包拿 Sprite、再顺**包内 PPtr** 拿老纹理 ⇒ 什么都看不见。
把 Sprite 的 `m_RD.texture` 指到我们的 Texture2D，两条加载路就都看得见。

Stage 2 的 Sprite **照抄真 Sprite 的字段树、只改指向**（`m_RenderDataKey`、精灵网格、`uvTransform`
都没法凭空造）⇒ **我们的贴图要与原贴图同尺寸**，网格与 UV 才成立。实测 ARMOR1：
原贴图 936×360、`m_Rect` 也是 936×360；容器两条的 preloadIndex/preloadSize 都是 (130, 2)。

## 实测口径（2026-10-03，本机真包读出来的；复算见 `_work/w9b_probe_hdr.py` / `w9b_probe_ab.py`）

| 层 | 字段 | 实测值 |
|---|---|---|
| UnityFS 头 | signature / version | `UnityFS\0` / **8** |
| 同上 | player / engine | `5.x.x` / `2022.3.62f3` |
| 同上 | size / cbis / ubis / flags | size == **磁盘文件大小**；flags = **0x243** |
| 同上 | flags 拆位 | 0x200 块表前补零 + 0x40 块表与目录合体 + 低 6 位 **3** = LZ4HC |
| 同上 | 头长 / blocksInfo 落点 | 固定字段结束于 **50**，补零到 **64**（VERSION >= 7 时 16 对齐） |
| blocksInfo | 内容 | 16 B data_hash（真包**全 0**）+ i32 块数 + 块(10 B) + i32 节点数 + 节点 |
| 节点 | 字段 | `offset(解压流) / size / flags / path(cstr)`；SerializedFile 节点 flags = **4** |
| 块 | 字段 | `uncompressed/cs/flag` 10 B；真包全 131,072 一块、末块余数、类型 3 |
| 数据起点 | data_start | `align16(blocksInfo 落点 + cbis)`（0x200 开着时）；实测 336 / 960 / 3696 |
| SF 头 | 48 B **大端** | `u32 0,0,22,0` / 小端标志 0 / `metadata_size` |
| 同上 | 续 | `i64 file_size` / `i64 data_offset` / `i64 0` |
| 同上 | data_offset | `align16(48 + metadata_size)`（真包 24,816 / 18,288 / 8,368 三条都命中） |
| 类型表 | 每项 | `i32 classID + u8 stripped + i16 scriptIndex + [16 scriptID] + 16 oldTypeHash` |
| 同上 | 续 | `+ 内嵌类型树 + [i32 deps + i32*]`，**不做对齐**（ufs 的坑 2） |
| 对象表 | 每项 | `align4` 后 24 B：`i64 pathID + i64 byteStart` |
| 同上 | 续 | `+ u32 byteSize + i32 typeID`（小端） |
| 对象数据 | 对齐 | 每个对象起点 **8 字节对齐**（真包 byteStart mod 8 == 0，W8 §9.9 复现） |
| 元数据尾部 | 顺序 | `i32 scripts + i32 externals + i32 refTypes` |
| 同上 | 续 | `+ cstr userInformation`（UnityPy 1.25.3 的写侧同序） |
| CRC | catalog 口径 | `zlib.crc32(逐块解压后首尾拼接)`（77/77 命中） |

## 与真包的两处**故意不同**（都为了先跑通最小闭环，写在明面上）

1. **块负载默认不压缩**（块类型 0，设计稿 §9.8 U2 的第一步）；块表本身仍按 flags 用 LZ4HC 压
   （真包 flags = 0x243）。`block_type=BLOCK_LZ4HC` 一行就能切成真包口径，本模块两种都支持。
   ⚠ 实测口径：本作 77/77 个真包的块**全是类型 3**；真机首测用哪个留给集成方定（两个都造得出来）。
2. **单节点**：只有 `CAB-<hash>`（SerializedFile），没有 `.resS`（我们的贴图数据内联）。

## 边界

* 顶层零第三方：`lz4` 与 UnityPy 的公共串表都是**延迟 import**（没装 lz4 就回落到不压缩块表）。
* 只出**字节**，不写盘（落盘由调用方决定）；本模块不认识游戏目录。
* 读侧只借 `core.asset.ufs`（两方共用）与 `clone`（类型树 codec）；元数据编码只借
  `metadata_writer`（同一份逻辑一份实现：段间对齐只在 `metadata_writer.layout()`，这里不重算）。
"""

from __future__ import annotations

import copy
import struct
import zlib
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

#: 产品**内置**的 Unit 类型表（SerializedFile 元数据区，4,121,040 B / 50 个类型项）。
#: 从游戏 `units_assets_all_<hash>.bundle` 抽一次，之后随产品走；
#: ⛔ 调用方不需要、也不应该再去打开游戏目录。
_BUILTIN_META = Path(__file__).resolve().parent / "data" / "typetree_units.bin"


def builtin_source_meta() -> bytes:
    """产品内置类型表的**元数据区**（类型树 + script_types）。

    ⚠ 1.2.1 起 units 包里没有 115，这一份也**没有 115 项**；要写 MonoScript 时用
    :func:`metadata_with_extra_types` 从 `*_monoscripts_*.bundle` 借。
    """
    try:
        return _BUILTIN_META.read_bytes()
    except OSError as exc:
        raise AssetError(
            f"产品缺内置类型表：{_BUILTIN_META}",
            hint="它随产品分发（4.1 MB）；丢了就从游戏 units 包重抽一次",
        ) from exc

__all__ = [
    "BLOCK_LZ4HC",
    "BLOCK_NONE",
    "BLOCK_SIZE",
    "CLASS_ASSETBUNDLE",
    "CLASS_SPRITE",
    "CLASS_TEXTURE2D",
    "CONTAINER_VERSION",
    "DEFAULT_ENGINE_VERSION",
    "DEFAULT_FLAGS",
    "DEFAULT_TARGET_PLATFORM",
    "METADATA_ALIGN",
    "NODE_FLAG_SERIALIZED",
    "OBJECT_ALIGN",
    "SF_HEADER_SIZE",
    "SF_VERSION",
    "TEXTURE_FORMAT_RGBA32",
    "UNITY_PLAYER_VERSION",
    "BlockRow",
    "MinBundlePlan",
    "ObjectBundlePlan",
    "ObjectRow",
    "ObjectSpec",
    "RawTypeEntry",
    "RawTypeTable",
    "SerializedFilePlan",
    "Texture2DData",
    "UnityFsPlan",
    "ARCHIVE_PREFIX",
    "assetbundle_fields",
    "block_self_check",
    "build_object_bundle",
    "builtin_source_meta",
    "bundle_node_names",
    "check_external_path",
    "external_for_bundle",
    "build_texture_bundle",
    "build_unityfs",
    "checker_rgba",
    "copy_closure",
    "dangling_pptrs",
    "remap_script_types",
    "iter_pptrs",
    "crc32_of_stream",
    "inline_texture2d_fields",
    "metadata_with_extra_types",
    "internal_bundle_name_of",
    "node_path_for",
    "path_id_for",
    "read_raw_type_table",
    "remap_pptrs",
    "reopen_container",
    "serialize_blocks_info",
    "serialize_serialized_file",
    "serialize_unityfs_header",
    "solid_rgba",
    "sprite_fields_for",
    "texture2d_fields",
    "type_entries_for",
]

# --------------------------------------------------------------------------- #
# 常量（全部来自真包实测，见模块头那张表）
# --------------------------------------------------------------------------- #
SIGNATURE = b"UnityFS\x00"
CONTAINER_VERSION = 8
UNITY_PLAYER_VERSION = "5.x.x"
DEFAULT_ENGINE_VERSION = "2022.3.62f3"
#: 0x200 = 块表前补零到 16 / 0x40 = 块表与目录合体 / 低 6 位 = 块表压缩类型（3 = LZ4HC）。
DEFAULT_FLAGS = 0x243
#: 目标平台（真包 SF 头里那个 i32；19 = StandaloneWindows64）。
DEFAULT_TARGET_PLATFORM = 19

SF_VERSION = 22
SF_HEADER_SIZE = 48
METADATA_ALIGN = 16
OBJECT_ALIGN = 8
BLOCK_SIZE = 128 * 1024
BLOCK_NONE = 0
BLOCK_LZ4HC = 3
LZ4HC_LEVEL = 12
NODE_FLAG_SERIALIZED = 4
#: 流式数据节点（`CAB-<hash>.resS`）的 flags —— 真件实测 **0**
#: （lightflashes / unitmodifications / ammoprefabs 三包都是 4 + 0）。
NODE_FLAG_STREAM = 0
#: 流式节点的后缀（与 asset_kernel.streaming.STREAM_SUFFIXES 同口径）。
STREAM_NODE_SUFFIX = ".resS"

CLASS_TEXTURE2D = 28
CLASS_ASSETBUNDLE = 142
CLASS_SPRITE = 213
#: MonoScript —— `script_types` 表里那一列指的**就是**它在对象表里的 pathID。
CLASS_MONO_SCRIPT = 115
CLASS_MONO_BEHAVIOUR = 114
TEXTURE_FORMAT_RGBA32 = 4

#: 内联贴图的 m_StreamData（真包流式贴图这里是 (offset, size, archive:/CAB-…resS)）。
NO_STREAM_DATA: Mapping[str, Any] = {"offset": 0, "size": 0, "path": ""}


def align(value: int, n: int) -> int:
    """向上取整到 `n` 的倍数（`n` 必须是 2 的幂）。"""
    return value + (-value) % n


def _lz4hc(data: bytes) -> bytes | None:
    """LZ4HC 压一块（与 Unity 逐字节同级的那个级别）；没装 lz4 就返回 None。"""
    try:
        import lz4.block  # noqa: PLC0415 -- 可选依赖：没装 lz4 时回落到不压缩块表
    except ImportError:
        return None
    return lz4.block.compress(
        data, store_size=False, mode="high_compression", compression=LZ4HC_LEVEL
    )


# --------------------------------------------------------------------------- #
# 第一层：类型表（逐字节照抄真包的那一项）+ 元数据尾部的位置
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RawTypeEntry:
    """类型表的一项：`[start, end)` 是它在元数据区里的字节区间，`raw` 就是那段。"""

    index: int
    class_id: int
    script_index: int
    start: int
    end: int
    raw: bytes
    nodes: tuple[clone.TypeNode, ...] = ()
    #: 同一项的**字段模型**（metadata_writer 那份；序列化走它，逻辑只有一份）。
    wire: mw.SerializedType | None = None

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class RawTypeTable:
    """一个 SerializedFile 的**原始**类型表（字节区间 + 解析出来的字段树）。"""

    unity_version: str
    target_platform: int
    enable_type_tree: bool
    entries: tuple[RawTypeEntry, ...]
    object_count_offset: int
    #: 整段元数据的字段模型（metadata_writer.parse_serialized_metadata 读出来的那一份）。
    model: mw.SerializedMetadata | None = None

    def script_types(self) -> tuple[mw.ScriptTypeEntry, ...]:
        """源包的 script types。

        照抄类型表时**必须一起抄**（`script_type_index` 是它的下标），
        否则 MonoBehaviour 的 m_Script 悬空。
        """
        if self.model is None:
            return ()
        return tuple(copy.deepcopy(item) for item in self.model.script_types)

    def entry(self, class_id: int) -> RawTypeEntry:
        hits = [e for e in self.entries if e.class_id == class_id]
        if not hits:
            raise AssetError(
                f"源包的类型表里没有 classID={class_id}",
                hint="换一个带这个类的真包当 schema 来源；⛔ 不许自己写树",
            )
        if len(hits) > 1:
            raise AssetError(f"类型表里 classID={class_id} 有 {len(hits)} 项，先收敛再用")
        return hits[0]

    def entries_for_class(self, class_id: int) -> tuple[RawTypeEntry, ...]:
        """同一 classID 的**全部**类型表项（本作 units：MonoBehaviour(114) 有 **22 项**）。"""
        return tuple(e for e in self.entries if e.class_id == class_id)

    def entry_for(self, class_id: int, *, script_index: int | None = None) -> RawTypeEntry:
        """按 classID 取一项；**同 classID 多棵树时必须点名 script_index**。

        ⚠ 实测（units 包）：classID=114（MonoBehaviour）有 **22 项**，各带自己的
        script_type_index ⇒ 不点名就分不清是哪一个脚本的树；其余类都只有一项。
        这条接缝是 W10 撞出来的（W9 只用了 Texture2D/Sprite/AssetBundle，都是一项）。
        """
        hits = [e for e in self.entries if e.class_id == class_id]
        if not hits:
            raise AssetError(
                f"源包的类型表里没有 classID={class_id}",
                hint="换一个带这个类的真包当 schema 来源；⛔ 不许自己写树",
            )
        choices = sorted({e.script_index for e in hits})
        if script_index is not None:
            picked = [e for e in hits if e.script_index == script_index]
            if not picked:
                raise AssetError(
                    f"classID={class_id} 里没有 script_type_index={script_index} 的项",
                    hint=f"可选的 script_index = {choices}",
                )
            if len(picked) > 1:
                raise AssetError(
                    f"classID={class_id} / script_index={script_index} 有 {len(picked)} 项"
                )
            return picked[0]
        if len(hits) > 1:
            raise AssetError(
                f"类型表里 classID={class_id} 有 {len(hits)} 项 —— 必须点名 script_index",
                hint=f"可选的 script_index = {choices}",
            )
        return hits[0]

    def class_ids(self) -> tuple[int, ...]:
        return tuple(e.class_id for e in self.entries)


def read_raw_type_table(
    meta: bytes | bytearray, *, common_strings: Mapping[int, str] | None = None
) -> RawTypeTable:
    """把 SerializedFile 的**元数据区**（含 48 B 头）里的类型表读成「原始项 + 字段树」。

    与 `clone.read_type_table()` 解析同一段，区别只有一个：这里**额外记住每一项的字节区间**，
    因为造新包时要把这一项**逐字节照抄**（设计稿 §9.5 第 2 步：类型树是内嵌的，不许自己写）。
    """
    buf = bytes(meta)
    if len(buf) < SF_HEADER_SIZE + 8:
        raise AssetError(f"元数据区只有 {len(buf)} 字节，读不出类型表")
    version = struct.unpack_from(">I", buf, 8)[0]
    pos = SF_HEADER_SIZE

    def take_fmt(fmt: str) -> tuple[Any, ...]:
        nonlocal pos
        values = struct.unpack_from(fmt, buf, pos)
        pos += struct.calcsize(fmt)
        return values

    end = buf.index(b"\x00", pos)
    unity_version = buf[pos:end].decode("utf-8", "surrogateescape")
    pos = end + 1
    (target_platform,) = take_fmt("<i")
    enable_type_tree = bool(buf[pos])
    pos += 1
    (type_count,) = take_fmt("<i")
    if not 0 <= type_count <= 100_000:
        raise AssetError(f"类型表项数不合法：{type_count}")

    entries: list[RawTypeEntry] = []
    for index in range(type_count):
        start = pos
        (class_id,) = take_fmt("<i")
        pos += 1  # is_stripped
        (script_index,) = take_fmt("<h")
        if script_index >= 0:
            pos += 16  # script_id（Hash128）
        pos += 16  # old_type_hash
        nodes: tuple[clone.TypeNode, ...] = ()
        if enable_type_tree:
            blob_start = pos
            (node_count,) = take_fmt("<i")
            (buffer_size,) = take_fmt("<i")
            if node_count < 0 or buffer_size < 0:
                raise AssetError(f"类型树长度不合法：节点 {node_count} / 串池 {buffer_size}")
            pos += 32 * node_count + buffer_size
            nodes = clone.read_type_nodes(
                buf[blob_start:pos], common_strings=common_strings
            )
            if version >= 21:
                (deps,) = take_fmt("<i")
                if not 0 <= deps <= 4096:
                    raise AssetError(f"type_dependencies 条数不合法：{deps}")
                pos += 4 * deps
        if pos > len(buf):
            raise AssetError(f"类型表第 {index} 项越出元数据区")
        entries.append(
            RawTypeEntry(
                index=index,
                class_id=class_id,
                script_index=script_index,
                start=start,
                end=pos,
                raw=buf[start:pos],
                nodes=nodes,
            )
        )
    # 同一段再用 metadata_writer 解一遍：序列化走它的字段模型（逻辑只有一份），
    # 两个解析器的 classID 必须逐项相等 —— 不等就抛，不猜。
    # ⚠ metadata_writer 的 verify 要求把**对齐填充**也一起给它（读到 data_offset）；
    # 只给到 48 + metadata_size 也能解，但那时不校验（两条路都明写在这行上）。
    declared = mw.parse_header(buf).data_offset
    model = mw.parse_serialized_metadata(buf, verify=len(buf) >= declared)
    if len(model.types) != len(entries):
        raise AssetError(
            f"两套解析器读出的类型表项数不同：{len(model.types)} vs {len(entries)}",
            hint="元数据解析走偏了；先用 metadata_writer.check_roundtrip 对一遍",
        )
    wired: list[RawTypeEntry] = []
    for item, wire in zip(entries, model.types, strict=True):
        if item.class_id != wire.class_id:
            raise AssetError(
                f"两套解析器在第 {item.index} 项上 classID 不同：{item.class_id} vs {wire.class_id}"
            )
        wired.append(replace(item, wire=copy.deepcopy(wire)))
    return RawTypeTable(
        unity_version=unity_version,
        target_platform=target_platform,
        enable_type_tree=enable_type_tree,
        entries=tuple(wired),
        object_count_offset=pos,
        model=model,
    )


def type_entries_for(
    meta: bytes | bytearray,
    class_ids: Sequence[int] = (CLASS_TEXTURE2D, CLASS_ASSETBUNDLE),
    *,
    common_strings: Mapping[int, str] | None = None,
) -> tuple[RawTypeTable, tuple[RawTypeEntry, ...]]:
    """从真包的元数据区里取出这几个 classID 的**原始**类型表项（顺序 = 新表的 type_id 顺序）。"""
    table = read_raw_type_table(meta, common_strings=common_strings)
    return table, tuple(table.entry(cid) for cid in class_ids)


# --------------------------------------------------------------------------- #
# 第二层：SerializedFile
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ObjectRow:
    """对象表的一行（`byte_start` 是**相对 data_offset** 的偏移，与 ufs 同口径）。"""

    path_id: int
    byte_start: int
    byte_size: int
    type_id: int
    class_id: int


@dataclass(frozen=True, slots=True)
class SerializedFilePlan:
    blob: bytes
    metadata_size: int
    data_offset: int
    file_size: int
    object_rows: tuple[ObjectRow, ...]
    type_class_ids: tuple[int, ...]

    @property
    def object_count(self) -> int:
        return len(self.object_rows)


def _cstr(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape") + b"\x00"


def serialize_serialized_file(
    *,
    type_entries: Sequence[RawTypeEntry],
    objects: Sequence[tuple[int, int, bytes]],
    unity_version: str = DEFAULT_ENGINE_VERSION,
    target_platform: int = DEFAULT_TARGET_PLATFORM,
    script_types: Sequence[mw.ScriptTypeEntry] = (),
    externals: Sequence[Any] = (),
    ref_types: Sequence[Any] = (),
) -> SerializedFilePlan:
    """序列化一个 SerializedFile（version 22，小端，`enable_type_tree = True`）。

    ⚠ 编码那一段**不在这里**：交给 `metadata_writer.build_metadata()`（同一份逻辑一份实现）。
    这里只负责三件事：对象表按 pathID 升序、数据区 8 字节对齐、把两段拼起来。
    ⚠ 照抄类型表时 `script_types` 必须一起抄（`script_type_index` 是它的下标）——
    这里有断言：有 `script_type_index >= 0` 的项却给不出对应的 script types 就**抛**。

    参数
    ----
    type_entries
        类型表项（来自 :func:`type_entries_for`：原始字节 + 字段模型 + 字段树）。
        列表下标就是 `type_id`。
    objects
        `[(pathID, classID, 对象字节)]`。**按 pathID 升序**写（Unity 二分查找，W8 §2 结论 2），
        每个对象的起点在数据区里按 8 字节对齐。
    """
    if not type_entries:
        raise AssetError("类型表是空的", hint="至少要 Texture2D + AssetBundle 两项")
    # type_id = 类型表里的**下标**；同 classID 可以有多项（units 的 classID=114 有 22 项），
    # 所以索引键是 (classID, script_index) —— 见 objects 的第 4 项。
    by_key: dict[tuple[int, int | None], int] = {}
    by_class: dict[int, list[int]] = {}
    for i, entry in enumerate(type_entries):
        if entry.wire is None:
            raise AssetError(
                f"类型表第 {i} 项没有字段模型（wire）",
                hint="用 read_raw_type_table()/type_entries_for() 读出来的项才带模型",
            )
        # ★ 2026-10-23：`script_type_index < 0` 用 **-1** 当键，不再用 None ——
        #   Unity 建的包里 classID=114 可能有多棵树（实测 5 棵，含 -1 那一棵），
        #   None 会让 -1 与「没点名」混成一谈，`entry_for` 当场判定歧义。
        key = (int(entry.class_id), int(entry.script_index))
        if key in by_key:
            raise AssetError(f"类型表里有重复的 (classID, script_index) = {key}")
        by_key[key] = i
        by_class.setdefault(int(entry.class_id), []).append(i)
    needed_scripts = max((e.wire.script_type_index for e in type_entries if e.wire), default=-1)
    if needed_scripts >= 0 and len(script_types) <= needed_scripts:
        raise AssetError(
            f"类型表里有 script_type_index={needed_scripts} 的项，"
            f"但只给了 {len(script_types)} 条 script types",
            hint="照抄类型表必须连 script_types 一起抄，否则 MonoBehaviour 的 m_Script 悬空",
        )

    rows = sorted(objects, key=lambda item: item[0])
    if len({item[0] for item in rows}) != len(rows):
        raise AssetError("pathID 有重复")

    #: (pathID, classID, payload[, type_key])；type_key = (classID, script_index) 或 type_id 下标。
    picked: list[tuple[int, int, bytes, int]] = []
    for item in rows:
        pid, class_id = int(item[0]), int(item[1])
        payload = item[2]
        if len(item) >= 4:
            key = item[3]
            if isinstance(key, int):
                type_id = int(key)
                if not 0 <= type_id < len(type_entries):
                    raise AssetError(f"对象 pathID={pid} 给的 type_id={type_id} 越出类型表")
                if int(type_entries[type_id].class_id) != class_id:
                    raise AssetError(
                        f"对象 pathID={pid} 的 classID={class_id} 与 type_id={type_id} "
                        f"（classID={type_entries[type_id].class_id}）不符"
                    )
            else:
                kkey = (class_id, -1 if key[1] is None else int(key[1]))
                if kkey not in by_key:
                    raise AssetError(
                        f"对象 pathID={pid} 点名了 {kkey}，但类型表里没有这一项",
                        hint=f"表里有这些：{sorted(by_key)}",
                    )
                type_id = by_key[kkey]
        else:
            ids = by_class.get(class_id)
            if not ids:
                raise AssetError(
                    f"对象 pathID={pid} 的 classID={class_id} 不在类型表里",
                    hint="先在 type_entries 里把这一类的类型树带上",
                )
            if len(ids) > 1:
                raise AssetError(
                    f"对象 pathID={pid} 的 classID={class_id} 在类型表里有 {len(ids)} 项",
                    hint="同 classID 多棵树时必须给第 4 项 type_key（classID, script_index）",
                )
            type_id = ids[0]
        picked.append((pid, class_id, payload, type_id))

    # 先定数据区布局（byte_start 是对象表里的字段，必须在写元数据**之前**算出来）
    data = bytearray()
    object_rows: list[ObjectRow] = []
    for pid, class_id, payload, type_id in picked:
        data += b"\x00" * ((-len(data)) % OBJECT_ALIGN)
        object_rows.append(
            ObjectRow(
                path_id=pid,
                byte_start=len(data),
                byte_size=len(payload),
                type_id=type_id,
                class_id=class_id,
            )
        )
        data += payload

    # ---- 元数据：交给 metadata_writer 的字段模型（逻辑只有一份）----
    header = mw.SerializedFileHeader(
        legacy_metadata_size=0,
        legacy_file_size=0,
        version=SF_VERSION,
        legacy_data_offset=0,
        endian_flag=0,
        reserved=b"\x00" * 3,
        metadata_size=0,
        file_size=0,
        data_offset=0,
        unknown=0,
    )
    meta = mw.SerializedMetadata(
        header=header,
        unity_version=unity_version,
        target_platform=int(target_platform),
        enable_type_tree=True,
        types=[copy.deepcopy(entry.wire) for entry in type_entries],
        objects=[
            mw.ObjectEntry(
                path_id=row.path_id, byte_start=row.byte_start, byte_size=row.byte_size,
                type_id=row.type_id,
            )
            for row in object_rows
        ],
        script_types=[copy.deepcopy(item) for item in script_types],
        # ⚠ 照抄真件时这两张表**必须一起抄**（2026-10-04 实测）：漏了 externals，
        # 任何 m_FileID != 0 的 PPtr（真件会指向 archive:/CAB-… 或 Library/unity default
        # resources）就会越界 —— **游戏可能直接原生崩溃**（不是报错）。
        externals=[copy.deepcopy(item) for item in externals],
        ref_types=[copy.deepcopy(item) for item in ref_types],
        user_information="",
    )
    lay = mw.layout(meta)  # ⚠ 段间对齐规则**只在 layout 这一处**，这里不重算 align4
    metadata_size = lay.end - SF_HEADER_SIZE
    data_offset = mw.align_up(lay.end, mw.DATA_ALIGN)
    file_size = data_offset + len(data)
    meta.header.file_size = file_size
    blob = mw.build_metadata(meta)  # 头 + 体 + 全零填充；metadata_size/data_offset 由它推导
    if len(blob) != data_offset:
        raise AssetError(
            f"metadata_writer 造出的元数据段长度 {len(blob)} != layout 推出的 {data_offset}"
        )
    blob += bytes(data)
    if len(blob) != file_size:
        raise AssetError(f"自造 SerializedFile 长度 {len(blob)} != file_size {file_size}")
    return SerializedFilePlan(
        blob=blob,
        metadata_size=metadata_size,
        data_offset=data_offset,
        file_size=file_size,
        object_rows=tuple(object_rows),
        type_class_ids=tuple(e.class_id for e in type_entries),
    )


def metadata_with_extra_types(
    base_meta: bytes | bytearray,
    extra_meta: bytes | bytearray,
    class_ids: Sequence[int] = (CLASS_MONO_SCRIPT,),
    *,
    common_strings: Mapping[int, str] | None = None,
) -> bytes:
    """把 `extra_meta` 里缺的 class 类型项并进 `base_meta`，返回**元数据区**。

    ★ 2026-10-20：1.2.1 的 units 包**没有 115 对象、类型表里也没有 115 项**
    （脚本搬到独立的 `*_monoscripts_*.bundle`）。模板/造包要写 MonoScript 时，
    必须把这一项从那个包借过来；⛔ 只合**类型项**，一个对象都不写。

    `base_meta` 已经覆盖所有 `class_ids` 时**原样返回**（不重编码，逐字节保持）。
    """
    strings = (
        common_strings if common_strings is not None
        else clone.unitypy_common_strings()
    )
    base = read_raw_type_table(base_meta, common_strings=strings)
    present = {int(e.class_id) for e in base.entries}
    missing = [int(c) for c in class_ids if int(c) not in present]
    if not missing:
        return bytes(base_meta)
    extra = read_raw_type_table(extra_meta, common_strings=strings)
    entries = list(base.entries)
    for cid in missing:
        entries.append(extra.entry_for(cid))
    plan = serialize_serialized_file(
        type_entries=entries,
        objects=(),
        unity_version=base.unity_version,
        target_platform=base.target_platform,
        script_types=base.script_types(),
        externals=base.model.externals,
        ref_types=base.model.ref_types,
    )
    return bytes(plan.blob[: plan.data_offset])


# --------------------------------------------------------------------------- #
# 第三层：UnityFS 容器
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BlockRow:
    uncompressed: int
    compressed: int
    flags: int

    @property
    def compress_type(self) -> int:
        return self.flags & 0x3F


@dataclass(frozen=True, slots=True)
class UnityFsPlan:
    data: bytes
    flags: int
    blocks: tuple[BlockRow, ...]
    node_path: str
    node_offset: int
    node_size: int
    data_hash: bytes
    compressed_blocks_info_size: int
    uncompressed_blocks_info_size: int
    data_start: int
    #: ★ 2026-10-10 加（**只加不改**）：流式数据节点（`.resS`）的三项读数。
    #: 没给 `stream_payload` 时保持下面这组默认值 ⇒ 既有调用方的读数一个字不变。
    stream_node_path: str = ""
    stream_node_offset: int = -1
    stream_node_size: int = 0

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def block_count(self) -> int:
        return len(self.blocks)


def serialize_blocks_info(
    data_hash: bytes, blocks: Sequence[BlockRow], nodes: Sequence[tuple[int, int, int, str]]
) -> bytes:
    """块表 + 节点表（**未压缩**）：与 `core.asset.ufs.serialize_blocks_info` 同一口径，自己写。"""
    if len(data_hash) != 16:
        raise AssetError(f"data_hash 要 16 字节，拿到 {len(data_hash)}")
    out = bytearray(data_hash)
    out += struct.pack(">i", len(blocks))
    for blk in blocks:
        out += struct.pack(">IIH", blk.uncompressed, blk.compressed, blk.flags)
    out += struct.pack(">i", len(nodes))
    for offset, size, flags, path in nodes:
        out += struct.pack(">qq", offset, size)
        out += struct.pack(">I", flags)
        out += path.encode("utf-8") + b"\x00"
    return bytes(out)


def serialize_unityfs_header(
    *,
    size: int,
    compressed_blocks_info_size: int,
    uncompressed_blocks_info_size: int,
    flags: int,
    version: int = CONTAINER_VERSION,
    player: str = UNITY_PLAYER_VERSION,
    engine: str = DEFAULT_ENGINE_VERSION,
) -> bytes:
    """UnityFS 头（大端；version >= 7 时补零到 16）。"""
    out = bytearray(SIGNATURE)
    out += struct.pack(">I", int(version))
    out += _cstr(player) + _cstr(engine)
    out += struct.pack(
        ">qIII",
        int(size),
        int(compressed_blocks_info_size),
        int(uncompressed_blocks_info_size),
        int(flags),
    )
    if version >= 7:
        out += b"\x00" * ((-len(out)) % METADATA_ALIGN)
    return bytes(out)


def build_unityfs(
    stream: bytes,
    node_path: str,
    *,
    flags: int = DEFAULT_FLAGS,
    block_type: int = BLOCK_NONE,
    block_size: int = BLOCK_SIZE,
    stream_payload: bytes | None = None,
    #: Container header 里的引擎版本串。**重写既有包时必须传源包真值**
    #: （`SerializedFile` 的 `unity_version` 也要跟着一致，见 build_object_bundle）。
    engine: str = DEFAULT_ENGINE_VERSION,
) -> UnityFsPlan:
    """把一个解压流包成 UnityFS 容器（默认单节点；块按 `block_type` 压或原样）。

    `block_type = BLOCK_NONE` 时块负载**不压缩**（设计稿 §9.8 U2 的第一步）；
    块表本身仍按 `flags` 的低 6 位压（真包 = 3 LZ4HC）。

    ★ 2026-10-10 加（**只加不改**）：`stream_payload` 给了就造**双节点**容器 ——
    `node[0] = <node_path>`（SerializedFile，flags 4）+
    `node[1] = <node_path>.resS`（裸数据流，flags 0），`node[1].offset == len(stream)`。
    形状照抄真件（`_work/p3tex/recon_real.py` 三个小真包：resS 节点 flags **0**、
    offset 紧接 node[0].size、**不做对齐**）。
    不给 `stream_payload` 时行为与以前**逐字节相同**。
    """
    if not stream:
        raise AssetError("空流不造包")
    if block_type not in (BLOCK_NONE, BLOCK_LZ4HC):
        raise AssetError(f"块压缩类型只支持 0 / 3，拿到 {block_type}")
    if stream_payload is not None and not bytes(stream_payload):
        raise AssetError(
            "stream_payload 是空的 —— 要么别给（单节点），要么给非空",
            hint="空 .resS 节点是坏形状：m_StreamData.size > 0 而节点里 0 字节",
        )

    # ★ 加了第二个节点时，块负载覆盖**两个节点的拼接**（与真件同形：一个解压流，两个节点）
    body = stream if stream_payload is None else stream + bytes(stream_payload)
    blocks: list[BlockRow] = []
    payload = bytearray()
    for i in range(0, len(body), block_size):
        raw = body[i : i + block_size]
        if block_type == BLOCK_LZ4HC:
            comp = _lz4hc(raw)
            if comp is not None and len(comp) < len(raw):
                payload += comp
                blocks.append(BlockRow(len(raw), len(comp), BLOCK_LZ4HC))
                continue
        payload += raw
        blocks.append(BlockRow(len(raw), len(raw), BLOCK_NONE))

    nodes = [(0, len(stream), NODE_FLAG_SERIALIZED, node_path)]
    if stream_payload is not None:
        nodes.append(
            (
                len(stream),
                len(bytes(stream_payload)),
                NODE_FLAG_STREAM,
                node_path + STREAM_NODE_SUFFIX,
            )
        )
    info = serialize_blocks_info(b"\x00" * 16, blocks, nodes)
    comp_info = _lz4hc(info)
    if comp_info is not None and len(comp_info) < len(info):
        info_blob, info_type = comp_info, BLOCK_LZ4HC
    else:
        info_blob, info_type = info, BLOCK_NONE
    header_flags = (int(flags) & ~0x3F) | info_type

    header = serialize_unityfs_header(
        size=0,
        compressed_blocks_info_size=len(info_blob),
        uncompressed_blocks_info_size=len(info),
        flags=header_flags,
        engine=engine,
    )
    data_start = len(header) + len(info_blob)
    if header_flags & 0x200:
        data_start = align(data_start, METADATA_ALIGN)
    total = data_start + len(payload)
    header = serialize_unityfs_header(
        size=total,
        compressed_blocks_info_size=len(info_blob),
        uncompressed_blocks_info_size=len(info),
        flags=header_flags,
        engine=engine,
    )
    gap = b"\x00" * (data_start - len(header) - len(info_blob))
    data = header + info_blob + gap + bytes(payload)
    if len(data) != total:
        raise AssetError(f"自造容器长度 {len(data)} != 头里写的 {total}")
    return UnityFsPlan(
        data=data,
        flags=header_flags,
        blocks=tuple(blocks),
        node_path=node_path,
        node_offset=0,
        node_size=len(stream),
        data_hash=b"\x00" * 16,
        compressed_blocks_info_size=len(info_blob),
        uncompressed_blocks_info_size=len(info),
        data_start=data_start,
        stream_node_path="" if stream_payload is None else node_path + STREAM_NODE_SUFFIX,
        stream_node_offset=-1 if stream_payload is None else len(stream),
        stream_node_size=0 if stream_payload is None else len(bytes(stream_payload)),
    )


def crc32_of_stream(data: bytes) -> int:
    """catalog 里的 `m_Crc` 口径：`zlib.crc32(逐块解压后拼接)`（无符号 32 位）。"""
    return zlib.crc32(data) & 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# 对象：Texture2D / AssetBundle 的字段树（名字全部来自包内类型树，见 _work/w9b_probe_ab.py）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Texture2DData:
    """一张**内联**（非流式）Texture2D 的值。"""

    name: str
    width: int
    height: int
    image: bytes
    texture_format: int = TEXTURE_FORMAT_RGBA32
    color_space: int = 1
    filter_mode: int = 1
    aniso: int = 1
    wrap_u: int = 1
    wrap_v: int = 1
    lightmap_format: int = 6

    def fields(self) -> dict[str, Any]:
        return texture2d_fields(
            name=self.name,
            width=self.width,
            height=self.height,
            image=self.image,
            texture_format=self.texture_format,
            color_space=self.color_space,
            filter_mode=self.filter_mode,
            aniso=self.aniso,
            wrap_u=self.wrap_u,
            wrap_v=self.wrap_v,
            lightmap_format=self.lightmap_format,
        )


def solid_rgba(width: int, height: int, rgba: Sequence[int]) -> bytes:
    """纯色 RGBA32 像素（`rgba` 4 个数）。"""
    if len(rgba) != 4:
        raise AssetError(f"rgba 要 4 个数，拿到 {len(rgba)}")
    return bytes(int(v) & 0xFF for v in rgba) * (width * height)


def checker_rgba(
    width: int, height: int, a: Sequence[int], b: Sequence[int], cell: int = 4
) -> bytes:
    """棋盘格 RGBA32 像素（肉眼一眼能看出「换成我的图了」）。"""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            color = a if ((x // cell) + (y // cell)) % 2 == 0 else b
            out += bytes(int(v) & 0xFF for v in color)
    return bytes(out)


def texture2d_fields(
    *,
    name: str,
    width: int,
    height: int,
    image: bytes,
    texture_format: int = TEXTURE_FORMAT_RGBA32,
    color_space: int = 1,
    filter_mode: int = 1,
    aniso: int = 1,
    wrap_u: int = 1,
    wrap_v: int = 1,
    lightmap_format: int = 6,
) -> dict[str, Any]:
    """一张内联 Texture2D 的字段树（字段名 = 包内类型树的节点名，一个不多一个不少）。"""
    if width <= 0 or height <= 0:
        raise AssetError(f"贴图尺寸不合法：{width}x{height}")
    if not image:
        raise AssetError("内联贴图必须有像素数据（流式那条路本模块不做）")
    return {
        "m_Name": str(name),
        "m_ForcedFallbackFormat": 4,
        "m_DownscaleFallback": False,
        "m_IsAlphaChannelOptional": False,
        "m_Width": int(width),
        "m_Height": int(height),
        "m_CompleteImageSize": len(image),
        "m_MipsStripped": 0,
        "m_TextureFormat": int(texture_format),
        "m_MipCount": 1,
        "m_IsReadable": False,
        "m_IsPreProcessed": False,
        "m_IgnoreMipmapLimit": False,
        "m_MipmapLimitGroupName": "",
        "m_StreamingMipmaps": False,
        "m_StreamingMipmapsPriority": 0,
        "m_ImageCount": 1,
        "m_TextureDimension": 2,
        "m_TextureSettings": {
            "m_FilterMode": int(filter_mode),
            "m_Aniso": int(aniso),
            "m_MipBias": 0.0,
            "m_WrapU": int(wrap_u),
            "m_WrapV": int(wrap_v),
            "m_WrapW": 0,
        },
        "m_LightmapFormat": int(lightmap_format),
        "m_ColorSpace": int(color_space),
        "m_PlatformBlob": [],
        "image data": bytes(image),
        "m_StreamData": dict(NO_STREAM_DATA),
    }


def assetbundle_fields(
    *,
    bundle_name: str,
    container: Sequence[tuple[str, int]],
    preload_ids: Sequence[int] | None = None,
    dependencies: Sequence[str] = (),
    extra_preload: Sequence[tuple[int, int]] = (),
    path_flags: int | None = None,
    explicit_data_layout: int | None = None,
) -> dict[str, Any]:
    """AssetBundle 对象的字段树（U3 实测：m_MainAsset 全 0 / m_PathFlags 0 / 兼容位 1）。

    `container` 是 `[(容器路径, pathID)]` —— **容器路径 = catalog 的 m_InternalIds 那条资产路径**
    （不是 Addressables 的 key：实测 key 是裸名 'ARMOR1'、资产路径是 'Assets/…/ARMOR1.png'，
    游戏拿 internalId 去 `AssetBundle.LoadAsset()`；见 `_work/w9b_buckets.md` §3）。
    """
    entries = list(container)
    #: ★ 2026-10-19：**允许空容器** —— 「新建一个空包、再往里新增资产」是产品的正常一步
    #: （用户口径：操作集只有 新增/删除/修改，没有「换」；空包是新增的起点）。
    #: 空容器 = `m_Container: []`、preload 表空；Unity 能加载这种包（列不出任何资产）。
    # 预载表：一个容器条目一个对象；**同一条路径的连续几条共用一个窗口**
    # （真件实测：'…/ARMOR1.png' 两条容器条目的 preloadIndex/preloadSize 都是 (130, 2)）
    #: ★ 2026-10-19：`extra_preload` = **跨包**预载项 `[(fileID, pathID)]`。真件实测
    #: （`RO_T72B_Base` 的窗口里 5 个 MonoScript 是 `m_FileID=1`）—— 外部脚本必须列进
    #: 窗口，否则实例化 prefab 时取不到脚本
    #: （症状 `referenced script (Unknown) is missing`）。
    extra = [(int(fid), int(pid)) for fid, pid in extra_preload]
    if preload_ids is not None:
        # 显式给 preload 表（真件形状：窗口覆盖**该资产的对象集**，而不是只有它自己）
        preload = [{"m_FileID": 0, "m_PathID": int(pid)} for pid in preload_ids]
        preload += [{"m_FileID": fid, "m_PathID": pid} for fid, pid in extra]
        windows = [(0, len(preload)) for _path, _pid in entries]
    else:
        preload = [{"m_FileID": 0, "m_PathID": int(path_id)} for _path, path_id in entries]
        windows: list[tuple[int, int]] = []
        start = 0
        for i, (path, _pid) in enumerate(entries):
            if i and str(path) != str(entries[i - 1][0]):
                start = i
            end = i + 1
            while end < len(entries) and str(entries[end][0]) == str(path):
                end += 1
            windows.append((start, end - start))
    rows: list[dict[str, Any]] = []
    for (path, path_id), (index, size) in zip(entries, windows, strict=True):
        rows.append(
            {
                "first": str(path),
                "second": {
                    "preloadIndex": index,
                    "preloadSize": size,
                    "asset": {"m_FileID": 0, "m_PathID": int(path_id)},
                },
            }
        )
    return {
        "m_Name": f"{bundle_name}",
        "m_PreloadTable": preload,
        "m_Container": rows,
        "m_MainAsset": {
            "preloadIndex": 0,
            "preloadSize": 0,
            "asset": {"m_FileID": 0, "m_PathID": 0},
        },
        "m_RuntimeCompatibility": 1,
        "m_AssetBundleName": f"{bundle_name}",
        "m_Dependencies": [str(d).lower() for d in dependencies],
        "m_IsStreamedSceneAssetBundle": False,
        # ★ 2026-10-23：这两个**必须随源包照抄**（默认值只在新建包时用）。
        #   实测：Unity 6000 建的包里 `m_PathFlags = 7`、容器路径存的是**小写**；
        #   游戏按 catalog 的 Pascal 路径 `LoadAsset()` 能命中 <= 靠这个标志做大小写归一。
        #   我们原来硬写 0 ⇒ 同一个包（内容逐字节相同）`LoadAsset(Pascal)` 直接 MISS，
        #   游戏侧表现为「Unable to load asset … from location」。
        "m_ExplicitDataLayout": 1 if explicit_data_layout is None else int(explicit_data_layout),
        "m_PathFlags": 0 if path_flags is None else int(path_flags),
        "m_SceneHashes": [],
    }


def sprite_fields_for(
    template: Mapping[str, Any], *, texture_path_id: int, name: str | None = None
) -> dict[str, Any]:
    """把一条**真 Sprite** 的字段树改成指向**我们自己的 Texture2D**。

    Stage 2（Lead 定）：我们包里同时放 Texture2D + Sprite —— 游戏 UI 的图是 Image/Sprite，
    Sprite 对纹理的引用是**包内 PPtr**（fileID=0），只改 Texture2D 那条 entry 的话
    游戏仍旧从老包拿 Sprite 再顺着 PPtr 拿老纹理 ⇒ 看不见。

    ⛔ 只改**指向**：`m_RD.texture` 换成我们的 pathID（其余字段——`m_Rect` / `m_RD.textureRect` /
    `m_RD.m_VertexData`（精灵网格）—— 全部照原样保留）。所以我们的贴图要**与原贴图同尺寸**，
    精灵网格与 UV 才继续成立（实测 ARMOR1：原贴图 936×360，`m_Rect` 也是 936×360）。
    """
    fields = copy.deepcopy(dict(template))
    render_data = fields.get("m_RD")
    if not isinstance(render_data, dict):
        raise AssetError(
            "Sprite 的字段树里没有 m_RD（SpriteRenderData）",
            hint="字段名来自包内类型树；拿错类的树了？",
        )
    render_data["texture"] = {"m_FileID": 0, "m_PathID": int(texture_path_id)}
    if name is not None:
        fields["m_Name"] = str(name)
    return fields


def internal_bundle_name_of(asset_bundle_name: str) -> str:
    """包内 `m_Name` -> catalog 的 `m_BundleName`（实测差一个 '.bundle' 后缀，5/5 命中）。"""
    if asset_bundle_name.endswith(".bundle"):
        return asset_bundle_name[: -len(".bundle")]
    return asset_bundle_name


def path_id_for(seed: str) -> int:
    """给新对象一个**确定性**的 pathID（不与 0 / 1 冲突）。"""
    return 0x5100_0000_0000_0000 | (zlib.crc32(seed.encode("utf-8")) & 0xFFFFFFFF)


def node_path_for(bundle_name: str) -> str:
    """容器节点名（真包是 `CAB-<32 hex>`；本包只有这一个节点，没有 .resS）。"""
    import hashlib  # noqa: PLC0415 -- 只在造名字时用一次，别把 hashlib 挂进顶层

    return "CAB-" + hashlib.md5(bundle_name.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 装配：1 个 Texture2D + 1 个 AssetBundle
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MinBundlePlan:
    data: bytes
    crc32: int
    serialized_file_size: int
    node_path: str
    serialized: SerializedFilePlan
    unityfs: UnityFsPlan
    container: tuple[tuple[str, int], ...]
    texture_path_id: int
    assetbundle_path_id: int
    sprite_path_id: int | None
    type_class_ids: tuple[int, ...]

    @property
    def bundle_size(self) -> int:
        return len(self.data)

    def summary(self) -> str:
        return (
            f"最小包 {self.bundle_size:,} B（SF {self.serialized_file_size:,} B / "
            f"对象 {self.serialized.object_count} / 块 {self.unityfs.block_count} / "
            f"crc32 {self.crc32}）"
        )


def build_texture_bundle(
    *,
    asset_path: str,
    texture: Texture2DData | Mapping[str, Any],
    bundle_name: str,
    source_meta: bytes | bytearray,
    common_strings: Mapping[int, str] | None = None,
    path_id: int | None = None,
    sprite_template: Mapping[str, Any] | None = None,
    sprite_path_id: int | None = None,
    node_path: str | None = None,
    block_type: int = BLOCK_NONE,
    flags: int = DEFAULT_FLAGS,
) -> MinBundlePlan:
    """造一个合法 UnityFS 包：`1 个 Texture2D + 1 个 AssetBundle`（＋可选 `1 个 Sprite`）。

    参数
    ----
    asset_path
        **容器路径 = catalog 的 m_InternalIds 那条资产路径**（如 `Assets/…/ARMOR1.png`）。
    bundle_name
        包内 `m_Name / m_AssetBundleName`（真包形状 = `<catalog m_BundleName>.bundle`）。
    source_meta
        真包的**元数据区**（48 B 头 + 类型表…）：只用来**逐字节照抄**这几个类的类型树。
    sprite_template
        Stage 2：一条**真 Sprite 的字段树模板**（`clone.read_object` 读出来的那份）。
        builder 自己把它的 `m_RD.texture` 指到我们的 Texture2D（`texture_path_id` 只有这里知道）
        并往容器里加**同一条路径的第二条条目**（先 Texture2D 后 Sprite，与真件同形）——
        这样游戏按 Sprite 加载能拿到我们的图，按 Texture2D 加载也能。
    """
    class_ids = (
        (CLASS_TEXTURE2D, CLASS_SPRITE, CLASS_ASSETBUNDLE)
        if sprite_template is not None
        else (CLASS_TEXTURE2D, CLASS_ASSETBUNDLE)
    )
    _table, entries = type_entries_for(source_meta, class_ids, common_strings=common_strings)
    nodes_of = {entry.class_id: entry.nodes for entry in entries}
    fields = texture.fields() if isinstance(texture, Texture2DData) else dict(texture)
    tex_pid = int(path_id) if path_id is not None else path_id_for(f"{bundle_name}|{asset_path}")
    if tex_pid in (0, 1):
        raise AssetError(f"贴图的 pathID {tex_pid} 与 AssetBundle 的 1 冲突")
    ab_pid = 1
    objects: list[tuple[int, int, bytes]] = [
        (ab_pid, CLASS_ASSETBUNDLE, b""),
        (tex_pid, CLASS_TEXTURE2D, clone.write_object(fields, nodes_of[CLASS_TEXTURE2D])),
    ]
    if sprite_template is None:
        container: tuple[tuple[str, int], ...] = ((str(asset_path), tex_pid),)
        spr_pid: int | None = None
    else:
        spr_pid = (
            int(sprite_path_id)
            if sprite_path_id is not None
            else path_id_for(f"{bundle_name}|{asset_path}|sprite")
        )
        if spr_pid in (0, 1, tex_pid):
            raise AssetError(f"Sprite 的 pathID {spr_pid} 与其它对象冲突")
        patch = sprite_fields_for(sprite_template, texture_path_id=tex_pid)
        objects.append((spr_pid, CLASS_SPRITE, clone.write_object(patch, nodes_of[CLASS_SPRITE])))
        container = ((str(asset_path), tex_pid), (str(asset_path), spr_pid))
    ab_blob = clone.write_object(
        assetbundle_fields(bundle_name=bundle_name, container=container),
        nodes_of[CLASS_ASSETBUNDLE],
    )
    objects[0] = (ab_pid, CLASS_ASSETBUNDLE, ab_blob)
    serialized = serialize_serialized_file(type_entries=entries, objects=objects)
    unityfs = build_unityfs(
        serialized.blob,
        node_path or node_path_for(bundle_name),
        flags=flags,
        block_type=block_type,
    )
    return MinBundlePlan(
        data=unityfs.data,
        crc32=crc32_of_stream(serialized.blob),
        serialized_file_size=serialized.file_size,
        node_path=unityfs.node_path,
        serialized=serialized,
        unityfs=unityfs,
        container=container,
        texture_path_id=tex_pid,
        assetbundle_path_id=ab_pid,
        sprite_path_id=spr_pid,
        type_class_ids=serialized.type_class_ids,
    )


# --------------------------------------------------------------------------- #
# 装配（通用）：**任意** classID 列表 + **任意**容器条目（W10）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ObjectSpec:
    """要写进新包的一个对象：classID + **字段树的值**（键名全部来自包内类型树）。

    `script_index` 只在同一 classID 有**多棵树**时必须点名 —— 本作 units 里
    MonoBehaviour(114) 有 **22 项**（各带自己的 script_type_index），不点名只能抛。
    `label` 只是给调用方自己看的记号（"root" / "mesh" / …），会原样进 `path_id_of`。
    """

    class_id: int
    fields: Mapping[str, Any] | None
    path_id: int
    script_index: int | None = None
    label: str = ""
    #: 已经序列化好的对象字节 —— 两条用途：
    #: ① clone 解不了树的类（真件里**目前一个都没有**；⚠ 2026-10-06 更正：这里曾经写
    #:    「实测 classID 48 Shader 会抛数组 compressedBlob 个数不合法」—— 那其实是
    #:    clone 里一条 1e7 的硬上限**误杀**（真件那个 vector<UInt8> 是 2,798 万字节）；
    #:    改成「按元素最小宽度算放不下才抛」之后，units 的 29/29 个 Shader 都能读能写）；
    #: ② 想**原样**搬一个对象（不解码 -> 不改 -> 不重编码）时。
    #: 与 fields 二选一。
    #: ⚠ 这类对象没有字段树 ⇒ :func:`dangling_pptrs` 普查不到它的 PPtr，
    #: 必须交给**独立尺子**核 —— 交接里如实写这条边界。
    payload: bytes | None = None

    @property
    def key(self) -> tuple[int, int | None]:
        return (int(self.class_id), None if self.script_index is None else int(self.script_index))


# --------------------------------------------------------------------------- #
# externals：跨包引用那一跳（★ 2026-10-12 加）
#
# 真件读数（units 包，探针 _work/addr-pkg/c1_recon.py + c1_cab_owner.py）：
#   units 的 SerializedFile externals 3 条 ——
#     externals[0] fileID=1  path='archive:/CAB-4716c9c1…/CAB-4716c9c1…'  -> **vfx 包的节点名**
#     externals[1] fileID=2  path='Library/unity default resources'       -> 引擎内置（不是包）
#     externals[2] fileID=3  path='archive:/CAB-457fda64…/CAB-457fda64…' -> 内置 shader 包节点名
#   即：**PPtr.fileID -> 本包 externals[i] -> 目标包的 CAB 节点名**，而「目标包要已被加载」
#   由 catalog 的依赖组键（entry[2]）保证（那个桶里 units+vfx+ammoprefabs+unitybuiltinshaders）。
#   ⇒ 自建包也能这么干：把自己不搬的素材指回**原包**（乙方案）。
# --------------------------------------------------------------------------- #
ARCHIVE_PREFIX = "archive:/"


def bundle_node_names(bundle: str | Path) -> tuple[str, ...]:
    """一个 UnityFS 包的**全部节点名**（节点 0 = 主 SerializedFile，resS 是流式节点）。"""
    with ufs.UfsReader(str(bundle)) as reader:
        return tuple(str(node.path) for node in reader.nodes)


def check_external_path(path: str, *, what: str = "externals 的 path") -> str:
    """externals 的 path 形状闸：只认 archive:/CAB-x/CAB-x（两段逐字相同）。

    ⛔ **不静默写半条**：不是这个形状就抛。内置资源那条
    （实测真件 Library/unity default resources）**不走这里** —— 那是引擎自带的，
    要引用它请照抄源包（copy_source_tables=True），别现编。
    """
    text = str(path).strip()
    if not text.startswith(ARCHIVE_PREFIX):
        raise AssetError(
            f"{what} 不是 {ARCHIVE_PREFIX} 开头：{path!r}",
            hint="跨包引用那条固定长 archive:/CAB-x/CAB-x（真件 77/77 都是这个形状）",
        )
    parts = text[len(ARCHIVE_PREFIX) :].split("/")
    if len(parts) != 2 or not parts[0] or parts[0] != parts[1] or not parts[0].startswith("CAB-"):
        raise AssetError(
            f"{what} 形状不对：{path!r}",
            hint="要 archive:/CAB-<md5>/CAB-<md5>（两段逐字相同、都以 CAB- 起）",
        )
    return text


def external_for_bundle(bundle: str | Path, *, node: str | None = None) -> mw.ExternalRef:
    """给一个**已落盘的包**造一条 externals 项（喂 build_object_bundle 的 extra_externals）。

    node 省略 = 用那个包的节点 0（= 它的 CAB 名）；给了就必须是**那个包里真实存在**的
    节点名 —— 不是就抛（正控：传一个不存在的节点名必须拒，⛔ 不静默写一条指不到东西的引用）。

    用法（乙方案 —— 只装改过的素材、其余依赖原包）::

        extra = [builder.external_for_bundle(units_bundle)]
        plan = builder.build_object_bundle(..., copy_source_tables=True, extra_externals=extra)
        plan.extra_external_file_ids      # (4,) —— 把它填进 PPtr.m_FileID
    """
    names = bundle_node_names(bundle)
    if not names:
        raise AssetError(f"{bundle} 里一个节点都没有", hint="这不是 UnityFS 包")
    wanted = str(node) if node is not None else names[0]
    if wanted not in names:
        raise AssetError(
            f"{bundle} 里没有这个节点名：{wanted!r}",
            hint=f"这个包的节点是 {list(names)}；节点名要逐字相同（大小写敏感）",
        )
    return mw.ExternalRef(
        temp_empty="",
        guid=b"\x00" * 16,
        type=0,
        path=check_external_path(f"{ARCHIVE_PREFIX}{wanted}/{wanted}", what="本函数造的 path"),
    )

@dataclass(frozen=True, slots=True)
class ObjectBundlePlan:
    """一个自建 UnityFS 包的全部读数（写盘前先自证用）。"""

    data: bytes
    crc32: int
    serialized_file_size: int
    node_path: str
    serialized: SerializedFilePlan
    unityfs: UnityFsPlan
    container: tuple[tuple[str, int], ...]
    path_id_of: tuple[tuple[str, int], ...]
    bundle_path_id: int
    type_keys: tuple[tuple[int, int | None], ...]
    #: ★ 2026-10-12 加：本包 SerializedFile 的 externals 表（逐条 path，顺序 = fileID 顺序）。
    externals: tuple[str, ...] = ()
    #: ★ 2026-10-12 加：extra_externals 那几条的 **fileID（1 起数）** —— PPtr.m_FileID 就填它。
    extra_external_file_ids: tuple[int, ...] = ()
    #: ★ 2026-10-10 加（**只加不改**）：`.resS` 节点的三项读数（没走流式时是默认值）。
    stream_node_path: str = ""
    stream_node_offset: int = -1
    stream_node_size: int = 0

    @property
    def bundle_size(self) -> int:
        return len(self.data)

    @property
    def object_count(self) -> int:
        return self.serialized.object_count

    @property
    def type_class_ids(self) -> tuple[int, ...]:
        return self.serialized.type_class_ids

    def summary(self) -> str:
        return (
            f"自建包 {self.bundle_size:,} B（SF {self.serialized_file_size:,} B / "
            f"对象 {self.object_count} / 容器 {len(self.container)} 条 / "
            f"块 {self.unityfs.block_count} / crc32 {self.crc32}）"
        )


def build_object_bundle(
    *,
    objects: Sequence[ObjectSpec],
    bundle_name: str,
    source_meta: bytes | bytearray,
    container: Sequence[tuple[str, int]],
    common_strings: Mapping[int, str] | None = None,
    block_type: int = BLOCK_NONE,
    flags: int = DEFAULT_FLAGS,
    node_path: str | None = None,
    assetbundle_path_id: int = 1,
    preload_all: bool = False,
    copy_source_tables: bool = False,
    #: ★ 2026-10-20 加：**模板包**是离线借格式用的（Mesh/Material/Shader），
    #: 不需要可寻址的容器条目 —— 传 False 就不强求。⛔ 默认 True（普通包必须有）。
    require_container: bool = True,
    #: ★ 2026-10-19 加：AssetBundle 对象的 `m_Dependencies`（跨包 externals 的解析表）。
    #: **克隆别人的包时必须照抄原包这一串**（小写 cab 名，见 builder.dependencies_from_externals）。
    #: 空着 ⇒ Unity 解析 `archive:/CAB-…/CAB-…` 找不到依赖 ⇒ 组件全变 Missing Script
    #: （实测：装进游戏后崩在 `referenced script (Unknown) is missing`）。
    assetbundle_dependencies: Sequence[str] = (),
    #: ★ 2026-10-23：AssetBundle 对象的 `m_PathFlags` / `m_ExplicitDataLayout`。
    #: **重写既有包时必须照抄源包**（见 `assetbundle_fields` 注释）。None = 用默认值。
    path_flags: int | None = None,
    explicit_data_layout: int | None = None,
    #: ★ 2026-10-19：**跨包预载项** `[(fileID, pathID)]`（外部 MonoScript 等），写进 AB 的
    #: `m_PreloadTable` 并让容器窗口覆盖它们 —— 真件就是这么写的（窗口里 5 个脚本 fileID=1）。
    extra_preload: Sequence[tuple[int, int]] = (),
    #: ★ 2026-10-12 加：**追加** externals（跨包引用那一跳）。给了就排在照抄来的那几条**之后**，
    #: 它们的 fileID 由 plan.extra_external_file_ids 回读（⛔ 调用方不要自己数）。
    #: 用 builder.external_for_bundle(<已落盘的包>) 造，别手写 path。
    extra_externals: Sequence[mw.ExternalRef] = (),
    #: `script_type_index` -> 本包 MonoScript 的 pathID
    #: （含 MonoBehaviour 的包**必须**给；见 :func:`remap_script_types`）
    script_type_map: Mapping[int, int] | None = None,
    #: ★ 2026-10-10 加：给了就造**双节点**包（`CAB-<hash>` + `CAB-<hash>.resS`），
    #: 字节原样成为 `.resS` 节点的内容 —— 流式贴图那条路（kb/23 §2）走它。
    #: ⛔ 不给时行为与以前**逐字节相同**。
    stream_payload: bytes | None = None,
    #: ★ 2026-10-26：**照抄源包的引擎版本**（`SerializedFile` 头里的 `unity_version`）。
    #: 写死 2022.3.62f3 的包在 Unity 6 建出来的关卡里实测**静默不画**
    #: （Kord 子炮塔整块消失、游戏日志 0 条相关错误）；真包是 `6000.3.24f1`。
    #: ⛔ 不给就用 `DEFAULT_ENGINE_VERSION`（=旧行为，逐字节相同）。
    engine_version: str | None = None,
) -> ObjectBundlePlan:
    """从零装配一个 UnityFS 包：**任意** classID + **任意**容器条目。

    `preload_all=True` 时，`m_PreloadTable` 会列出**包内全部对象**、容器条目的窗口覆盖它们
    —— 与真件同形（真件 prefab 条目实测窗口 (28116, **197**) = 该 prefab 的对象集；
    默认那条路只把容器条目自己放进 preload，窗口 (0,1)）。

    与 :func:`build_texture_bundle` 的关系：那个是「Texture2D（＋可选 Sprite）+ AssetBundle」
    的特例，这里是通用形。四条口径与它一致，一条不少：

    1. **类型表项逐字节照抄真包** —— `source_meta` 必须是带这些类的真包元数据区；
       同 classID 有多棵树时用 `ObjectSpec.script_index` 点名（见 `RawTypeTable.entry_for`）；
    2. **容器路径 = catalog 的 m_InternalIds 那条资产路径**（internalId，不是裸名 key）——
       游戏拿 internalId 去 `AssetBundle.LoadAsset()`（实测 299/299 vs 0/299）；
    3. `pathID=1` 留给 AssetBundle 对象（真件形状）；
    4. `script_types` 跟着类型表一起抄，否则 MonoBehaviour 的 m_Script 悬空。

    参数
    ----
    objects
        要放进包里的对象（**不含** AssetBundle 对象 —— 那个由本函数按 `container` 生成）。
    container
        `[(容器路径, pathID)]`。**同一条路径可以出现多次**（先 Texture2D 后 Sprite 那种，
        真件实测就是这样），preload 窗口按「同路径的连续条目共用一个窗口」算。
    """
    specs = list(objects)
    #: ★ 2026-10-19：**空包**也允许 —— `objects=[]` + `container=[]` 就是「新建一个空包」，
    #: 之后用 新增/删除/修改 三个动作往里加（用户口径：没有「换」）。
    #: 空包照样有 AssetBundle 对象（下面补），Unity 能加载、只是列不出资产。
    if not container and specs and require_container:
        raise AssetError("有对象却没有容器条目", hint="容器路径 = catalog 的 internalId")

    keys: list[tuple[int, int | None]] = []
    for spec in specs:
        if spec.key not in keys:
            keys.append(spec.key)
    if CLASS_ASSETBUNDLE in {cid for cid, _si in keys}:
        raise AssetError(
            "objects 里不要放 AssetBundle 对象",
            hint="本函数按 container 自己生成那一个（pathID=1）",
        )
    keys.append((CLASS_ASSETBUNDLE, None))

    if common_strings is None:
        # 与 reopen_container 同一口径：类型树的字符串可能引用**公共串表**（格式常量），
        # 不传就按 UnityPy 那份取；两者都没有时 clone 会抛，不猜字段名。
        common_strings = clone.unitypy_common_strings()
    preload_ids: tuple[int, ...] | None = None
    if preload_all:
        preload_ids = tuple(sorted(int(spec.path_id) for spec in specs))
    table = read_raw_type_table(source_meta, common_strings=common_strings)
    entries = tuple(table.entry_for(cid, script_index=si) for cid, si in keys)
    nodes_of = {key: entry.nodes for key, entry in zip(keys, entries, strict=True)}

    own_ids = {int(spec.path_id) for spec in specs}
    if len(own_ids) != len(specs):
        raise AssetError("objects 里有重复的 pathID")
    if int(assetbundle_path_id) in own_ids:
        raise AssetError(f"AssetBundle 的 pathID {assetbundle_path_id} 与其它对象冲突")
    known = own_ids | {int(assetbundle_path_id)}
    for path, pid in container:
        if int(pid) not in known:
            raise AssetError(
                f"容器条目 {path!r} 指向 pathID={pid}，但包里没有这个对象",
                hint="容器条目的 pathID 必须是本包里某个对象的 pathID",
            )

    payloads: list[tuple[int, int, bytes]] = []
    for spec in specs:
        if spec.payload is not None:
            if spec.fields is not None:
                raise AssetError(
                    f"pathID={spec.path_id} 同时给了 fields 与 payload —— 二选一",
                    hint="payload 是给 clone 解不了树的类（如 Shader）用的",
                )
            blob = bytes(spec.payload)
        elif spec.fields is None:
            raise AssetError(f"pathID={spec.path_id} 既没有 fields 也没有 payload")
        else:
            blob = clone.write_object(dict(spec.fields), nodes_of[spec.key])
        # 第 4 项 = type_key：同 classID 多棵树时（MonoBehaviour）就靠它点名
        payloads.append((int(spec.path_id), int(spec.class_id), blob, spec.key))
    payloads.append(
        (
            int(assetbundle_path_id),
            CLASS_ASSETBUNDLE,
            clone.write_object(
                assetbundle_fields(
                    bundle_name=bundle_name,
                    container=container,
                    preload_ids=preload_ids,
                    dependencies=assetbundle_dependencies,
                    extra_preload=extra_preload,
                    path_flags=path_flags,
                    explicit_data_layout=explicit_data_layout,
                ),
                nodes_of[(CLASS_ASSETBUNDLE, None)],
            ),
            (CLASS_ASSETBUNDLE, None),
        )
    )

    ext: Sequence[Any] = ()
    refs: Sequence[Any] = ()
    if copy_source_tables and table.model is not None:
        # 照抄真件的 externals + ref_types（含「内置资源 / 同包其它 CAB」的引用）
        ext = table.model.externals
        refs = table.model.ref_types
    # ★ 2026-10-12 加：**追加**外部引用（跨包引用那一跳 —— 乙方案靠它指回原包）。
    #   三条口径：① 排在照抄来的那几条之后（原来那几条的 fileID 一个都不动）；
    #   ② fileID 由本函数回读（1 起数 = 下标 + 1），⛔ 调用方不许自己数；
    #   ③ path 过 check_external_path 形状闸、同一条 path 不许出现两次（⛔ 不静默写半条）。
    extra_ext = tuple(extra_externals or ())
    extra_file_ids: tuple[int, ...] = ()
    if extra_ext:
        copied = tuple(ext)
        seen = {str(getattr(item, "path", "")) for item in copied}
        appended: list[mw.ExternalRef] = []
        for item in extra_ext:
            path = check_external_path(str(getattr(item, "path", "")))
            if path in seen:
                raise AssetError(
                    f"externals 里已经有这条了：{path!r}",
                    hint="同一个目标包只声明一次；要它的 fileID 就从 plan.externals 里数（1 起）",
                )
            seen.add(path)
            appended.append(item)
        extra_file_ids = tuple(len(copied) + index for index in range(1, len(appended) + 1))
        ext = copied + tuple(appended)
    # script_types 的口径见 remap_script_types()：表里那几项必须指着**本包的** MonoScript，
    # 否则 Unity 解析 MonoBehaviour 时取到空（实测崩在 UnityPlayer 0xC0000005 读 0x0）。
    scripts_in_file = any(
        int(class_id) == CLASS_MONO_SCRIPT for _pid, class_id, _blob, _key in payloads
    )
    needed_scripts = {
        int(entry.wire.script_type_index)
        for entry in entries
        if entry.wire is not None and int(entry.wire.script_type_index) >= 0
    }
    if scripts_in_file and needed_scripts and not script_type_map:
        raise AssetError(
            "本包既搬了 MonoScript 对象、又有 MonoBehaviour 类型，但没给 script_type_map",
            hint="script_types 照抄真件 ⇒ 表里指着真件那个包的 pathID（实测 0/22 命中），"
                 "Unity 解析脚本时会取到空指针。"
                 "把每个 script_type_index 映射到本包 MonoScript 的 pathID。",
        )
    if script_type_map:
        missing = sorted(needed_scripts - {int(k) for k in script_type_map})
        if missing:
            raise AssetError(
                f"script_type_map 里少了 {missing}（本包类型表用到的 script_type_index）",
                hint="一个都不能少：少一个就有一条 MonoBehaviour 的脚本指不回本包",
            )
    # ★ 2026-10-26：引擎版本**一处算、两处用** —— `SerializedFile` 头的 `unity_version`
    #   与 UnityFS 头的 `engine` 必须一致，否则就是「头写 6000、元数据写 2022」的怪包。
    engine = str(engine_version or DEFAULT_ENGINE_VERSION)
    serialized = serialize_serialized_file(
        type_entries=entries,
        objects=payloads,
        unity_version=engine,
        script_types=remap_script_types(table.script_types(), script_type_map or {}),
        externals=ext,
        ref_types=refs,
    )
    unityfs = build_unityfs(
        serialized.blob,
        node_path or node_path_for(bundle_name),
        flags=flags,
        block_type=block_type,
        stream_payload=stream_payload,
        engine=engine,
    )
    return ObjectBundlePlan(
        data=unityfs.data,
        crc32=crc32_of_stream(serialized.blob),
        serialized_file_size=serialized.file_size,
        node_path=unityfs.node_path,
        serialized=serialized,
        unityfs=unityfs,
        container=tuple((str(path), int(pid)) for path, pid in container),
        path_id_of=tuple((spec.label, int(spec.path_id)) for spec in specs if spec.label),
        bundle_path_id=int(assetbundle_path_id),
        type_keys=tuple(keys),
        externals=tuple(str(getattr(item, "path", "")) for item in ext),
        extra_external_file_ids=extra_file_ids,
        stream_node_path=unityfs.stream_node_path,
        stream_node_offset=unityfs.stream_node_offset,
        stream_node_size=unityfs.stream_node_size,
    )


# --------------------------------------------------------------------------- #
# N4 的机械判据：PPtr 普查 / 重映射（悬空 = 红）
# --------------------------------------------------------------------------- #
def iter_pptrs(value: Any, path: str = "") -> Iterator[tuple[str, int, int]]:
    """递归遍历字段树里的 PPtr，产出 (字段路径, m_FileID, m_PathID)。

    PPtr 的形状由类型树与 clone 定死：恰好两个键 m_FileID / m_PathID。
    """
    if isinstance(value, Mapping):
        keys = set(value)
        if {"m_FileID", "m_PathID"} <= keys and keys <= {"m_FileID", "m_PathID"}:
            yield (path, int(value["m_FileID"]), int(value["m_PathID"]))
            return
        for key, sub in value.items():
            yield from iter_pptrs(sub, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            yield from iter_pptrs(sub, f"{path}[{i}]")


def inline_texture2d_fields(source: Mapping[str, Any], pixels: bytes) -> dict[str, Any]:
    """把一张**流式** Texture2D 的字段树变成**内联**（像素搬进对象里）——N4 搬依赖的那一步。

    口径（三条都来自真件实测）：
      * 源必须是流式的（image data 为空、m_StreamData.size > 0）；本来就是内联的**抛**，
        不静默照抄 —— 否则这条尺子永远绿；
      * pixels 的字节数必须 == 源对象的 m_CompleteImageSize（真件三个贴图 3/3 相等）；
      * 结果里 image data = pixels、m_StreamData 置空 {0,0,""}、m_CompleteImageSize 保持原值。
    """
    fields = copy.deepcopy(dict(source))
    existing = fields.get("image data") or b""
    if existing:
        raise AssetError(
            f"这张贴图本来就是内联的（image data {len(existing):,} B）——不该走内联这条路",
            hint="内联只用于把 .resS 的像素搬进对象；非流式贴图原样搬即可",
        )
    stream = fields.get("m_StreamData")
    if not isinstance(stream, Mapping) or int(stream.get("size", 0)) <= 0:
        raise AssetError(
            "源贴图没有 m_StreamData.size（既不是内联也不是流式？）——不许猜",
            hint="先确认这是 Unity 的 Texture2D 字段树（键名来自包内类型树）",
        )
    declared = int(fields.get("m_CompleteImageSize", 0))
    if declared and declared != len(pixels):
        raise AssetError(
            f"像素字节数 {len(pixels):,} != 源对象的 m_CompleteImageSize {declared:,}",
            hint=".resS 的 offset/size 读错了（真件三个贴图是逐字节相等的）",
        )
    fields["image data"] = bytes(pixels)
    fields["m_StreamData"] = dict(NO_STREAM_DATA)
    fields["m_CompleteImageSize"] = len(pixels)
    return fields


def remap_script_types(
    script_types: Sequence[mw.ScriptTypeEntry],
    mapping: Mapping[int, int],
) -> tuple[mw.ScriptTypeEntry, ...]:
    """把 `script_types` 里**我们用到的**那几项改成**本包自己的** MonoScript pathID。

    为什么必须这么做（2026-10-06 实测，`_work/v5-scripttypes-check.py`）：
    类型表照抄真件时 `SerializedType.script_type_index` 也是真件的下标，于是
    `script_types[i].local_identifier` 指着**真件那个包里的** MonoScript pathID ——
    那个对象不在本包里（实测本包 22 项 **0 项**指着本包的 MonoScript）。
    Unity 解析 MonoBehaviour 的类型/脚本时走这张表 ⇒ 取到空 ⇒ **加载期原生空指针**
    （症状：进场景实例化资产时 `UnityPlayer.dll` 里 `0xC0000005 读 0x0`）。

    ⛔ 没点名的项原样保留：真件的表里还有我们没用到的脚本，那些是纯数据、
    而「表里每一项都要能被解析」这条**没有实测支持**，不做没依据的改动。
    """
    out = [copy.deepcopy(item) for item in script_types]
    for index, path_id in mapping.items():
        i = int(index)
        if not 0 <= i < len(out):
            raise AssetError(
                f"script_type_index {i} 超出 script_types 表（本包 {len(out)} 项）",
                hint="映射的键是类型表里的 script_type_index，不是顺序号",
            )
        out[i].file_index = 0
        out[i].local_identifier = int(path_id)
    return tuple(out)


def dangling_pptrs(
    objects: Sequence[ObjectSpec],
    *,
    externals: int = 0,
    assetbundle_path_id: int = 1,
    script_path_ids: Collection[int] = (),
) -> tuple[str, ...]:
    """**N4 的尺子**：包里每个 PPtr 都要落得住，否则返回一行行问题（空元组 = 无悬空）。

    口径：
      * m_FileID == 0 ⇒ 指向**本包内**某个对象的 pathID（含 AssetBundle 自己那个），
        指向 0 = 空引用，合法；
      * m_FileID != 0 ⇒ 指向第 m_FileID 个 external（1 起数）⇒ 必须 <= externals；
      * **MonoBehaviour 的 m_Script** 实测是 `{m_FileID: 0, m_PathID: <脚本 pathID>}`，
        而那个 pathID **就是包里 MonoScript(115) 对象在对象表里的 pathID**
        （2026-10-06 实测更正，见 _work/a1-script-binding.md：22/22 对应，
         units 的 23 个 MonoScript 全在对象数据头 0…2772 B、含 -1187635630949769 那条）。
        ⇒ `script_path_ids` 的语义是**把包内 MonoScript 的 pathID 登记成已知引用**，
        ⛔ **不是**「m_Script 天然不在对象表里、所以可以不搬 MonoScript 对象」——
        后者正是 W10 第 2 轮翻车的形状（漏搬 MonoScript ⇒ 组件等于不存在 ⇒ No UnitPrefabRoot）。
        不传这个参数会**误报悬空**，那是尺子的口径问题，不是可以省事的理由。
    ⚠ 这条尺子**能红**（用例里有正控）：把一条 PPtr 的 pathID 改成不存在的值，立刻报。
    """
    known = (
        {int(spec.path_id) for spec in objects}
        | {int(assetbundle_path_id)}
        | {int(pid) for pid in script_path_ids}
    )
    problems: list[str] = []
    for spec in objects:
        if spec.fields is None:
            # payload-only（如 Shader）：没有字段树，普查不到 —— 交给独立尺子
            continue
        tag = spec.label or f"pathID={spec.path_id}"
        for path, file_id, path_id in iter_pptrs(spec.fields):
            where = f"{tag}[classID={spec.class_id}].{path}"
            if file_id == 0:
                if path_id != 0 and path_id not in known:
                    problems.append(f"{where} -> 包内 pathID {path_id} 不存在（悬空）")
            elif not 1 <= file_id <= externals:
                problems.append(
                    f"{where} -> 外部 fileID {file_id} 越界（本包只声明了 {externals} 个 external）"
                )
    return tuple(problems)


def remap_pptrs(fields: Mapping[str, Any], mapping: Mapping[int, int]) -> dict[str, Any]:
    """按「旧 pathID -> 新 pathID」重写字段树里 m_FileID == 0 的 PPtr。

    ⛔ 映射表里没有的 pathID **原样保留**（不静默改成 0）—— 漏映射要由
    dangling_pptrs() 抓出来，而不是被悄悄抹掉。
    """

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            keys = set(value)
            if {"m_FileID", "m_PathID"} <= keys and keys <= {"m_FileID", "m_PathID"}:
                out = dict(value)
                if int(out["m_FileID"]) == 0 and int(out["m_PathID"]) in mapping:
                    out["m_PathID"] = int(mapping[int(out["m_PathID"])])
                return out
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, tuple):
            return [walk(v) for v in value]
        return value

    return walk(dict(fields))


def copy_closure(
    roots: Iterable[int],
    *,
    read_fields: Callable[[int], Mapping[str, Any]],
    has_object: Callable[[int], bool],
    next_id: Callable[[], int],
    max_objects: int = 4096,
) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    """把一个/一批**包内对象**的 PPtr 闭包整个复制出来，并把闭包内的引用重映射到新 pathID。

    返回 `(旧 pathID -> 重映射后的字段树, 旧 pathID -> 新 pathID)`。

    口径（2026-10-06 定）：
      * 只跟 `m_FileID == 0` 的引用走；**跨包引用（fileID != 0）原样保留**
        —— 要不要声明 external 是调用方的事，这里不替它决定；
      * 目标是 `has_object` 说"不在包里"的 ⇒ **抛**（⛔ 不静默留一条悬空）；
      * 环（控制器里有自引用）由 visited 挡掉；`max_objects` 是兜底闸门。

    ⚠ 复制的是**字段树**，所以调用方拿到后要自己 `clone.write_object(fields, nodes)`；
    一条根都不给 ⇒ 返回空（不是错）。
    """
    order: list[int] = []
    seen: set[int] = set()
    fields_of: dict[int, Mapping[str, Any]] = {}
    queue = [int(pid) for pid in roots]
    while queue:
        pid = queue.pop(0)
        if pid in seen or pid == 0:
            continue
        seen.add(pid)
        if not has_object(pid):
            raise AssetError(
                f"闭包里的 pathID={pid} 不在这个包里",
                hint="要么它本来就跨包（那得走 external），要么读错了对象表",
            )
        fields = read_fields(pid)
        fields_of[pid] = fields
        order.append(pid)
        if len(order) > max_objects:
            raise AssetError(
                f"闭包超过 {max_objects} 个对象 —— 停下（多半是走错了根）",
                hint="看一眼 roots 是不是指到了包根/场景那种大对象上",
            )
        for _path, file_id, target in iter_pptrs(fields):
            if int(file_id) == 0 and int(target) != 0 and int(target) not in seen:
                queue.append(int(target))
    mapping = {pid: int(next_id()) for pid in order}
    if len(set(mapping.values())) != len(mapping):
        raise AssetError("next_id() 给出了重复的新 pathID —— 调用方要保证唯一")
    return {pid: remap_pptrs(fields_of[pid], mapping) for pid in order}, mapping

# --------------------------------------------------------------------------- #
# 自证：用产品自己的读侧重新打开，把 m_Container 列出来（设计稿 §9.7 的「自证」一步）
# --------------------------------------------------------------------------- #
def reopen_container(
    path: str | Path, *, common_strings: Mapping[int, str] | None = None
) -> dict[str, Any]:
    """重新打开一个刚落盘的包，列出 `m_Container`（以及对象/块/节点的读数）。

    ⛔ 走的是 `ufs.UfsReader` + `clone.read_object`（产品读侧），不是写侧的内存状态。
    内嵌类型树的字符串可能引用**公共串表**（格式常量），不传就按 UnityPy 那份取
    （与 `clone` 同一条边界）；两者都没有时**抛错**，不猜字段名。
    """
    if common_strings is None:
        common_strings = clone.unitypy_common_strings()
    target = Path(path)
    with ufs.UfsReader(target) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        head = reader.read_range(node.offset, SF_HEADER_SIZE)
        meta_size = struct.unpack_from(">I", head, 20)[0]
        data_offset = struct.unpack_from(">q", head, 32)[0]
        meta = reader.read_range(node.offset, data_offset)
        table = read_raw_type_table(meta, common_strings=common_strings)
        ab_type = next(
            (
                row
                for row in info.objects
                if table.entries[row[4]].class_id == CLASS_ASSETBUNDLE
            ),
            None,
        )
        if ab_type is None:
            raise AssetError("这个包里没有 AssetBundle 对象")
        blob = reader.read_range(
            node.offset + info.data_offset + ab_type[2], ab_type[3]
        )
        obj = clone.read_object(blob, table.entry(CLASS_ASSETBUNDLE).nodes)
        return {
            "path": str(target),
            "file_size": reader.file_size,
            "flags": reader.header.flags,
            "blocks": len(reader.blocks),
            "block_types": sorted({b.compress_type for b in reader.blocks}),
            "nodes": [(n.path, n.size) for n in reader.nodes],
            "node_path": node.path,
            "node_size": node.size,
            "node_offset": node.offset,
            "metadata_size": meta_size,
            "data_offset": info.data_offset,
            "object_count": info.object_count,
            "object_path_ids": [row[1] for row in info.objects],
            "object_rows": [
                (row[1], row[2], row[3], table.entries[row[4]].class_id) for row in info.objects
            ],
            "container": {entry["first"]: entry["second"]["asset"] for entry in obj["m_Container"]},
            # ⚠ 同一条路径可以有**多条**条目（Texture2D 一条 + Sprite 一条）——dict 会把它们并掉，
            # 所以另给一份逐条读数（真件 ARMOR1：两条，preloadIndex/preloadSize 都是 (130, 2)）
            "container_rows": [
                (
                    entry["first"],
                    entry["second"]["asset"]["m_PathID"],
                    entry["second"]["preloadIndex"],
                    entry["second"]["preloadSize"],
                )
                for entry in obj["m_Container"]
            ],
            "preload_count": len(obj["m_PreloadTable"]),
            "bundle_name": obj["m_Name"],
        }


def block_self_check(plan: UnityFsPlan) -> tuple[int, int]:
    """块表自洽：`(声明的 Σ uncompressed, 声明的 Σ compressed)`，与文件实际长度对账。"""
    total_u = sum(b.uncompressed for b in plan.blocks)
    total_c = sum(b.compressed for b in plan.blocks)
    return total_u, total_c


def bundle_size_of(plan: MinBundlePlan) -> int:
    """包的字节数（给日志/报告用；等价于 len(plan.data)）。"""
    return len(plan.data)
