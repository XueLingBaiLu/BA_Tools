r"""SerializedFile 元数据读写器（version 22）—— **含对象表之后的尾部**。

`ba_mod_maker.core.asset.ufs.read_serialized_file` 只读到对象表就返回，末尾只做
「游标 <= header_size + metadata_size」的对账（ufs.py:579-587）。设计稿 §9.8 U1 把
那段没解析的尾巴记成「ref types 段，结构与本机数据不符」（按 `i32 count + count ×
SerializedType(含树)` 解析会越界）。本模块把**整段元数据**（头 48 字节 + 头部声明
的 metadata_size 字节 + 到 data_offset 的对齐填充）解成字段模型，并能**按字段重新
序列化**，判据是读 → 写 → 与源**逐字节相同**。

## 尾部到底长什么样（2026-10-03 实测；字段 = UnityPy 1.25.3 的 `SerializedType`）

对象表之后依次是四段（顺序即文件顺序；units 实测值见 `_work/w9a_u1_structure.md`）：

1. **script types**：`i32 count` + count × [`i32 fileIndex` + **align4** + `i64 localIdentifier`]
   —— 12 字节/项（units 545 项）；
2. **externals**：`i32 count` + count × [`cstr tempEmpty` + 16B guid + `i32 type` + `cstr path`]
   —— 变长（units 2 项）；
3. **ref types（U1 就是它）**：`i32 count` + count × SerializedType（见下）（units 12 项）；
4. **userInformation**：`cstr`（**不对齐**；实测 6/6 节点都是空串 = 1 字节）。

第 3 段的 SerializedType 与类型表**同一套布局**，但两处不同：

1. **script_id 的判据不同**：类型表看 `class_id == 114`（MonoBehaviour），
   ref type 看 `script_type_index >= 0`；
2. **类型树之后的尾巴不同**（version >= 21）：类型表读 `i32 deps + i32×deps`，
   ref type 读 **三个 cstr**：`m_ClassName` / `m_NameSpace` / `m_AssemblyName`。

⇒ 设计稿 §9.8 U1 说「结构与本机数据不符」，真因是**漏了那三个 cstr**：把 ref type 当
类型表读时，读完类型树会去读 deps，而那里其实是类名字符串的第一个字节，游标立刻走飞。
补上之后 **6/6 节点逐字段与 UnityPy 一致**（count、class_id、类型树节点数、类名/命名
空间/程序集名全部相等）——复算：`.venv\Scripts\python.exe _work\w9a_probe_u1.py`。

## 三处**易错的对齐**（都实测过）

- **头 48 字节是大端**（`>IIII` + endian_flag + 3 保留 + `>I` metadata_size +
  `>q` file_size + `>q` data_offset + `>q` unknown），**元数据体按头里的 endian_flag
  走**（本机全 0 = 小端）——只按头那 48 字节猜字节序会当场错；
- **类型表每一项开头不对齐**（ufs.py docstring 第 2 条），**对象表每一项开头 align4**，
  script types 的 `i64` 前也要 align4；
- `metadata_size` **不含**头 48 字节 ⇒ 元数据体结束于 `48 + metadata_size`；
  `data_offset == align16(48 + metadata_size)`（真件 75/75 + 本轮 6/6 实测），
  中间那 0~15 字节**全零**（本轮 6/6 实测；非零会被 `check_roundtrip` 判红）。

## 用法::

    from ba_mod_maker.asset_kernel.metadata_writer import (
        build_metadata, check_roundtrip, parse_serialized_metadata,
    )

    meta = parse_serialized_metadata(node_bytes[:48 + metadata_size])
    report = check_roundtrip(node_bytes)          # 读 → 写 → 逐字节
    assert report.identical, report

字段表与复算命令见 `_work/w9a_u1_structure.md`。
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

from ba_mod_maker.core.errors import AssetError

#: SerializedFile 头固定 48 字节（**大端**）。
HEADER_SIZE = 48
#: `data_offset` 的对齐（实测 75/75 + 本轮 6/6）。
DATA_ALIGN = 16
#: 对象表项步长（i64 pathID + i64 byteStart + u32 byteSize + i32 typeID）。
OBJECT_ENTRY_STRIDE = 24
#: script type 项步长（i32 fileIndex + align4 + i64 localIdentifier）。
SCRIPT_ENTRY_STRIDE = 12
#: 内嵌类型树的节点结构步长（`>hBBIIiiiQ`，version >= 19）。
TYPE_TREE_NODE_STRIDE = 32
#: MonoBehaviour 的 classID。
MONO_BEHAVIOUR_CLASS_ID = 114
#: 类型树字符串偏移的最高位：置 1 表示「公共串表下标」，否则是字符串池内偏移。
COMMON_STRING_FLAG = 0x80000000

_BE = ">"


def align_up(value: int, alignment: int) -> int:
    """向上对齐（`align_up(48 + metadata_size, 16) == data_offset`）。"""
    return (value + alignment - 1) // alignment * alignment


# --------------------------------------------------------------------------- 头


@dataclass(slots=True)
class SerializedFileHeader:
    """48 字节头。

    | 偏移 | 宽度 | 字段 |
    |---|---|---|
    | 0  | 4 | legacy_metadata_size（version >= 9 之后恒 0） |
    | 4  | 4 | legacy_file_size（恒 0） |
    | 8  | 4 | **version**（本机 22） |
    | 12 | 4 | legacy_data_offset（恒 0） |
    | 16 | 1 | **endian_flag**（0 = 小端；本机全部 0） |
    | 17 | 3 | reserved |
    | 20 | 4 | **metadata_size**（不含这 48 字节） |
    | 24 | 8 | **file_size**（== 节点大小） |
    | 32 | 8 | **data_offset**（== align16(48 + metadata_size)） |
    | 40 | 8 | unknown（本机全部 0） |
    """

    legacy_metadata_size: int
    legacy_file_size: int
    version: int
    legacy_data_offset: int
    endian_flag: int
    reserved: bytes
    metadata_size: int
    file_size: int
    data_offset: int
    unknown: int

    @property
    def endian(self) -> str:
        """元数据体的字节序：`<`（endian_flag == 0）或 `>`。"""
        return "<" if self.endian_flag == 0 else ">"

    @property
    def body_end(self) -> int:
        """元数据体结束的绝对位置 = `48 + metadata_size`。"""
        return HEADER_SIZE + self.metadata_size

    @property
    def derived_data_offset(self) -> int:
        """按「48 + metadata_size 向上 16 对齐」算出来的 data_offset。"""
        return align_up(self.body_end, DATA_ALIGN)

    @property
    def padding_size(self) -> int:
        """头 48 字节 + 元数据体之后、`data_offset` 之前的对齐填充字节数（0~15）。"""
        return self.data_offset - self.body_end


def parse_header(blob: bytes) -> SerializedFileHeader:
    """只读 48 字节头（**大端**）。`blob` 至少 48 字节。"""
    if len(blob) < HEADER_SIZE:
        raise AssetError(
            f"SerializedFile 头不足 {HEADER_SIZE} 字节（只有 {len(blob)}）",
            hint="先取节点前 48 字节；容器层取错了节点也会长这样",
        )
    legacy_meta, legacy_size, version, legacy_doff = struct.unpack_from(_BE + "IIII", blob, 0)
    return SerializedFileHeader(
        legacy_metadata_size=legacy_meta,
        legacy_file_size=legacy_size,
        version=version,
        legacy_data_offset=legacy_doff,
        endian_flag=blob[16],
        reserved=bytes(blob[17:20]),
        metadata_size=struct.unpack_from(_BE + "I", blob, 20)[0],
        file_size=struct.unpack_from(_BE + "q", blob, 24)[0],
        data_offset=struct.unpack_from(_BE + "q", blob, 32)[0],
        unknown=struct.unpack_from(_BE + "q", blob, 40)[0],
    )


def encode_header(header: SerializedFileHeader) -> bytes:
    """48 字节头的逆运算（`parse_header(encode_header(h))` 与 `h` 逐字段相等）。"""
    if len(header.reserved) != 3:
        raise AssetError(
            f"reserved 必须是 3 字节，实测 {len(header.reserved)}",
            hint="头里偏移 17..19 是 3 字节保留位",
        )
    out = bytearray()
    out += struct.pack(
        _BE + "IIII",
        header.legacy_metadata_size,
        header.legacy_file_size,
        header.version,
        header.legacy_data_offset,
    )
    out += bytes([header.endian_flag])
    out += header.reserved
    out += struct.pack(_BE + "I", header.metadata_size)
    out += struct.pack(_BE + "q", header.file_size)
    out += struct.pack(_BE + "q", header.data_offset)
    out += struct.pack(_BE + "q", header.unknown)
    return bytes(out)


# --------------------------------------------------------------------------- 游标


class _Reader:
    """按指定字节序推进的游标。**只为本模块的元数据体存在**（不借 ufs 的私有类）。"""

    __slots__ = ("buf", "endian", "pos")

    def __init__(self, buf: bytes, endian: str, pos: int = 0) -> None:
        self.buf = buf
        self.endian = endian
        self.pos = pos

    def u8(self) -> int:
        value = self.buf[self.pos]
        self.pos += 1
        return value

    def i16(self) -> int:
        (value,) = struct.unpack_from(self.endian + "h", self.buf, self.pos)
        self.pos += 2
        return value

    def i32(self) -> int:
        (value,) = struct.unpack_from(self.endian + "i", self.buf, self.pos)
        self.pos += 4
        return value

    def u32(self) -> int:
        (value,) = struct.unpack_from(self.endian + "I", self.buf, self.pos)
        self.pos += 4
        return value

    def i64(self) -> int:
        (value,) = struct.unpack_from(self.endian + "q", self.buf, self.pos)
        self.pos += 8
        return value

    def take(self, size: int) -> bytes:
        if size < 0:
            raise AssetError(f"要读负数长度 {size}", hint="上一个字段读错时会走到这里")
        value = bytes(self.buf[self.pos : self.pos + size])
        self.pos += size
        return value

    def cstr(self) -> str:
        end = self.buf.find(b"\x00", self.pos)
        if end < 0:
            raise AssetError(
                f"位置 {self.pos} 起找不到字符串结束符",
                hint="元数据解析已经走偏；这通常意味着上一个字段读错了",
            )
        value = bytes(self.buf[self.pos : end]).decode("utf-8", "surrogateescape")
        self.pos = end + 1
        return value

    def align(self, alignment: int = 4) -> None:
        self.pos += (-self.pos) % alignment


# --------------------------------------------------------------------------- 字段模型


@dataclass(slots=True)
class TypeTree:
    """一段**内嵌**类型树 blob（`TypeTreeNode.parse_blob` 口径）。

    `i32 节点数` + `i32 字符串池字节数` + `32 × 节点数` 的节点数组 + 字符串池。
    节点数组与字符串池**原样保留字节**：字符串池是带公共串表下标的偏移池，重新拼装要有
    UnityPy 那份公共串表才可能逐字节相同 —— 保住原始字节比「看起来解析了」更诚实
    （`iter_type_tree_nodes` 负责把它解成字段看）。
    """

    node_count: int
    string_buffer_size: int
    nodes: bytes
    strings: bytes

    @property
    def size(self) -> int:
        """整段 blob 的字节数（含最前面两个长度）。"""
        return 8 + len(self.nodes) + len(self.strings)


@dataclass(slots=True)
class TypeTreeNode:
    """类型树里的一个节点（`>hBBIIiiiQ`，version >= 19）。

    | 偏移 | 宽度 | 字段 |
    |---|---|---|
    | 0  | 2 | m_Version |
    | 2  | 1 | m_Level |
    | 3  | 1 | m_TypeFlags |
    | 4  | 4 | m_TypeStrOffset |
    | 8  | 4 | m_NameStrOffset |
    | 12 | 4 | m_ByteSize |
    | 16 | 4 | m_Index |
    | 20 | 4 | m_MetaFlag |
    | 24 | 8 | m_RefTypeHash |
    """

    version: int
    level: int
    type_flags: int
    type_str_offset: int
    name_str_offset: int
    byte_size: int
    index: int
    meta_flag: int
    ref_type_hash: int
    type_name: str
    name: str


@dataclass(slots=True)
class SerializedType:
    """类型表 / ref types 里的一项（布局相同，**两处判据不同** —— 见模块 docstring）。"""

    class_id: int
    is_stripped: bool
    script_type_index: int
    script_id: bytes | None
    old_type_hash: bytes
    type_tree: TypeTree | None = None
    type_dependencies: tuple[int, ...] | None = None
    class_name: str | None = None
    namespace: str | None = None
    assembly_name: str | None = None
    is_ref_type: bool = False

    @property
    def size(self) -> int:
        """这一项在线上占的字节数。"""
        total = 4 + 1 + 2 + 16
        if self.script_id is not None:
            total += 16
        if self.type_tree is not None:
            total += self.type_tree.size
            if self.type_dependencies is not None:
                total += 4 + 4 * len(self.type_dependencies)
            for text in (self.class_name, self.namespace, self.assembly_name):
                if text is not None:
                    total += len(text.encode("utf-8", "surrogateescape")) + 1
        return total


@dataclass(slots=True)
class ObjectEntry:
    """对象表一项：align4 后 24 字节。

    ⚠️ `byte_start` 是**相对 data_offset** 的存储值（UnityPy 读的时候会把
    data_offset 加上再存进 `byte_start`，本模块保留**存储值**）。
    """

    path_id: int
    byte_start: int
    byte_size: int
    type_id: int


@dataclass(slots=True)
class ScriptTypeEntry:
    """script types 一项：`i32 fileIndex` + align4 + `i64 localIdentifier`。"""

    file_index: int
    local_identifier: int


@dataclass(slots=True)
class ExternalRef:
    """externals 一项：`cstr tempEmpty` + 16B guid + `i32 type` + `cstr path`。"""

    temp_empty: str
    guid: bytes
    type: int
    path: str


@dataclass(slots=True)
class SerializedMetadata:
    """一个 SerializedFile 的**整段元数据**（头 48 字节 + 体 + 对齐填充）。"""

    header: SerializedFileHeader
    unity_version: str
    target_platform: int
    enable_type_tree: bool
    types: list[SerializedType]
    objects: list[ObjectEntry]
    script_types: list[ScriptTypeEntry]
    externals: list[ExternalRef]
    ref_types: list[SerializedType]
    user_information: str
    #: 源文件里实际的填充字节（0~15）。重建时**推导**为等长全零；两者不等说明源不正常。
    padding: bytes = b""

    @property
    def object_table_offset(self) -> int:
        """对象表第一项**对齐之后**的偏移（写回时改 byte_start 要用它）。"""
        return layout(self).object_table_offset

    @property
    def is_little_endian(self) -> bool:
        return self.header.endian_flag == 0

    def find_type_ids(self, class_id: int) -> list[int]:
        """类型表里 `class_id` 命中的下标（W9 造对象时按 classID 取类型树）。"""
        return [index for index, item in enumerate(self.types) if item.class_id == class_id]

    def overview(self) -> list[dict[str, Any]]:
        """字段表（偏移 / 宽度 / 含义 / 值）—— 文档与用例共用。"""
        head = {
            "offset": 0,
            "size": HEADER_SIZE,
            "name": "header",
            "detail": "大端 48 字节，字段见 SerializedFileHeader",
        }
        return [head, *layout(self).rows]


@dataclass(slots=True)
class Layout:
    """逐段偏移（由模型**复算**，不缓存、不抄源）。"""

    rows: list[dict[str, Any]]
    object_count_offset: int
    object_table_offset: int
    ref_type_count_offset: int
    end: int
    padding_size: int


def _row(rows: list[dict[str, Any]], offset: int, size: int, name: str, detail: str) -> None:
    rows.append({"offset": offset, "size": size, "name": name, "detail": detail})


def layout(meta: SerializedMetadata) -> Layout:
    """重走一遍元数据体的形状，给出每段的偏移与宽度。

    这是写回侧的**唯一**偏移来源：段与段之间的对齐规则只写在这一处。
    """
    endian = meta.header.endian
    version = meta.header.version
    enable_type_tree = meta.enable_type_tree
    rows: list[dict[str, Any]] = []

    def cstr_size(text: str) -> int:
        return len(text.encode("utf-8", "surrogateescape")) + 1

    pos = HEADER_SIZE
    start = pos
    pos += cstr_size(meta.unity_version)
    _row(rows, start, pos - start, "unity_version", repr(meta.unity_version))
    start = pos
    pos += 4
    _row(rows, start, 4, "target_platform", str(meta.target_platform))
    _row(rows, pos, 1, "enable_type_tree", str(enable_type_tree))
    pos += 1
    start = pos
    pos += 4
    rows.append({"offset": start, "size": 4, "name": "type_count", "detail": str(len(meta.types))})
    start = pos
    for item in meta.types:
        pos += _type_wire_size(item, enable_type_tree, version)
    rows.append(
        {
            "offset": start,
            "size": pos - start,
            "name": "types[]",
            "detail": f"{len(meta.types)} 项；**每项开头不对齐**",
        }
    )
    object_count_offset = pos
    pos += 4
    _row(rows, object_count_offset, 4, "object_count", str(len(meta.objects)))
    pos += (-pos) % 4
    object_table_offset = pos
    for _ in meta.objects:
        pos += OBJECT_ENTRY_STRIDE
        pos += (-pos) % 4
    pos -= (-pos) % 4
    rows.append(
        {
            "offset": object_table_offset,
            "size": pos - object_table_offset,
            "name": "objects[]",
            "detail": f"{len(meta.objects)} 项 × {OBJECT_ENTRY_STRIDE} B；**每项开头 align4**",
        }
    )
    start = pos
    pos += 4
    _row(rows, start, 4, "script_type_count", str(len(meta.script_types)))
    start = pos
    for _ in meta.script_types:
        pos += 4
        pos += (-pos) % 4
        pos += 8
    rows.append(
        {
            "offset": start,
            "size": pos - start,
            "name": "script_types[]",
            "detail": f"{len(meta.script_types)} 项 × {SCRIPT_ENTRY_STRIDE} B（i64 前 align4）",
        }
    )
    start = pos
    pos += 4
    _row(rows, start, 4, "external_count", str(len(meta.externals)))
    start = pos
    for ext in meta.externals:
        pos += cstr_size(ext.temp_empty) + 16 + 4 + cstr_size(ext.path)
    _row(rows, start, pos - start, "externals[]", f"{len(meta.externals)} 项")
    ref_type_count_offset = pos
    pos += 4
    _row(rows, ref_type_count_offset, 4, "ref_type_count", str(len(meta.ref_types)))
    start = pos
    for item in meta.ref_types:
        pos += _type_wire_size(item, enable_type_tree, version)
    rows.append(
        {
            "offset": start,
            "size": pos - start,
            "name": "ref_types[]（U1）",
            "detail": f"{len(meta.ref_types)} 项；每项尾随 3 个 cstr（类名/命名空间/程序集）",
        }
    )
    start = pos
    pos += cstr_size(meta.user_information)
    _row(rows, start, pos - start, "userInformation", repr(meta.user_information))
    body_end = HEADER_SIZE + meta.header.metadata_size
    rows.append(
        {
            "offset": pos,
            "size": max(0, body_end - pos),
            "name": "（体结束）",
            "detail": f"48 + metadata_size = {body_end}",
        }
    )
    rows.append(
        {
            "offset": body_end,
            "size": max(0, meta.header.data_offset - body_end),
            "name": "padding",
            "detail": f"{max(0, meta.header.data_offset - body_end)} B 全零，凑到 align16",
        }
    )
    del endian
    return Layout(
        rows=rows,
        object_count_offset=object_count_offset,
        object_table_offset=object_table_offset,
        ref_type_count_offset=ref_type_count_offset,
        end=pos,
        padding_size=max(0, meta.header.data_offset - body_end),
    )


def _type_wire_size(item: SerializedType, enable_type_tree: bool, version: int) -> int:
    """一项 SerializedType 在**线上**占的字节数。"""
    total = 4 + 1 + 2 + 16
    if item.script_id is not None:
        total += 16
    if enable_type_tree and item.type_tree is not None:
        total += item.type_tree.size
        if version >= 21:
            if item.is_ref_type:
                for text in (item.class_name, item.namespace, item.assembly_name):
                    total += len((text or "").encode("utf-8", "surrogateescape")) + 1
            else:
                total += 4 + 4 * len(item.type_dependencies or ())
    return total


# --------------------------------------------------------------------------- 解析


def parse_serialized_metadata(blob: bytes, *, verify: bool = True) -> SerializedMetadata:
    """解一段 SerializedFile 元数据。

    `blob` 至少要覆盖 `[0, 48 + metadata_size)`；多出来的对象数据不会被读。
    `verify=True` 时核对三条不变量（**不成立就抛 `AssetError`，不静默容忍**）：

    1. 元数据体必须**正好**结束在 `48 + metadata_size`（多/少一个字节都说明解析走偏）；
    2. `data_offset == align16(48 + metadata_size)`；
    3. 填充区必须是全零（否则写回会静默丢字节）。
    """
    if not isinstance(blob, bytes):
        blob = bytes(blob)
    header = parse_header(blob)
    if header.version < 22:
        raise AssetError(
            f"只支持 SerializedFile version >= 22，实测到 {header.version}",
            hint="本机游戏是 22；更老的版本对象表布局不同（byteStart 是 u32），需要另写分支",
            version=header.version,
        )
    body_end = header.body_end
    if len(blob) < body_end:
        raise AssetError(
            f"元数据体被截断：需要 {body_end} 字节，只拿到 {len(blob)}",
            hint="先按头里的 metadata_size 把整段元数据取全再解析",
            metadata_size=header.metadata_size,
            blob_size=len(blob),
        )

    endian = header.endian
    version = header.version
    reader = _Reader(blob, endian, HEADER_SIZE)
    unity_version = reader.cstr()
    target_platform = reader.i32()
    enable_type_tree = bool(reader.u8())

    type_count = reader.i32()
    if type_count < 0:
        raise AssetError(f"类型表项数为负：{type_count}", hint="游标已经走偏")
    types = [
        _read_type(reader, version, enable_type_tree, is_ref_type=False) for _ in range(type_count)
    ]

    object_count = reader.i32()
    if object_count < 0:
        raise AssetError(f"对象表项数为负：{object_count}", hint="游标已经走偏")
    objects: list[ObjectEntry] = []
    for _ in range(object_count):
        reader.align(4)
        objects.append(
            ObjectEntry(
                path_id=reader.i64(),
                byte_start=reader.i64(),
                byte_size=reader.u32(),
                type_id=reader.i32(),
            )
        )

    script_count = reader.i32()
    if script_count < 0:
        raise AssetError(f"script types 项数为负：{script_count}", hint="游标已经走偏")
    script_types: list[ScriptTypeEntry] = []
    for _ in range(script_count):
        file_index = reader.i32()
        reader.align(4)
        script_types.append(ScriptTypeEntry(file_index=file_index, local_identifier=reader.i64()))

    external_count = reader.i32()
    if external_count < 0:
        raise AssetError(f"externals 项数为负：{external_count}", hint="游标已经走偏")
    externals: list[ExternalRef] = []
    for _ in range(external_count):
        temp_empty = reader.cstr()
        guid = reader.take(16)
        external_type = reader.i32()
        path = reader.cstr()
        externals.append(
            ExternalRef(temp_empty=temp_empty, guid=guid, type=external_type, path=path)
        )

    ref_types: list[SerializedType] = []
    if version >= 20:
        ref_count = reader.i32()
        if ref_count < 0:
            raise AssetError(f"ref types 项数为负：{ref_count}", hint="游标已经走偏")
        ref_types = [
            _read_type(reader, version, enable_type_tree, is_ref_type=True)
            for _ in range(ref_count)
        ]

    user_information = reader.cstr()
    padding = bytes(blob[body_end : header.data_offset]) if len(blob) >= header.data_offset else b""

    if verify:
        if reader.pos != body_end:
            raise AssetError(
                "元数据解析没有落在声明的末尾："
                f"走到 {reader.pos}，声明 {body_end}（差 {reader.pos - body_end:+d}）",
                hint="结构与本产品的解析器不符；游戏更新后需要复核",
                enable_type_tree=enable_type_tree,
                type_count=type_count,
                object_count=object_count,
                ref_type_count=len(ref_types),
            )
        if header.data_offset != header.derived_data_offset:
            raise AssetError(
                f"data_offset 与 metadata_size 对不上：{header.data_offset}"
                f" != align16(48 + {header.metadata_size}) = {header.derived_data_offset}",
                hint="本机真件 75/75 + 本轮 6/6 都成立；不成立说明取错了节点或文件已损坏",
            )
        if len(blob) < header.data_offset:
            raise AssetError(
                f"取不到填充区：data_offset = {header.data_offset}，只拿到 {len(blob)}",
                hint="填充区（0~15 字节全零）也要读进来，否则写回会短一截",
            )
        if any(padding):
            raise AssetError(
                f"填充区里有非零字节（{sum(1 for b in padding if b)}/{len(padding)}）",
                hint="写回会按全零重建填充；源既然不是全零就不该静默写回",
                padding=padding.hex(),
            )

    return SerializedMetadata(
        header=header,
        unity_version=unity_version,
        target_platform=target_platform,
        enable_type_tree=enable_type_tree,
        types=types,
        objects=objects,
        script_types=script_types,
        externals=externals,
        ref_types=ref_types,
        user_information=user_information,
        padding=padding,
    )


def _read_type(
    reader: _Reader, version: int, enable_type_tree: bool, *, is_ref_type: bool
) -> SerializedType:
    """读一项 SerializedType（类型表与 ref types 共用，**两处判据不同**）。

    判据逐字对齐 UnityPy 1.25.3 的 `SerializedType.__init__`：

    - 类型表：`class_id == 114` 才有 16 字节 script_id；
    - ref type：`script_type_index >= 0` 才有 16 字节 script_id；
    - `enable_type_tree` 打开时才有类型树；version >= 21 时——类型表读
      `i32 deps + i32×deps`，**ref type 读三个 cstr**（类名 / 命名空间 / 程序集名）。
    """
    class_id = reader.i32()
    stripped_raw = reader.u8()
    script_type_index = reader.i16()
    has_script_id = script_type_index >= 0 if is_ref_type else class_id == MONO_BEHAVIOUR_CLASS_ID
    script_id = reader.take(16) if has_script_id else None
    old_type_hash = reader.take(16)
    type_tree = None
    type_dependencies = None
    class_name = None
    namespace = None
    assembly_name = None
    if enable_type_tree:
        node_count = reader.i32()
        string_buffer_size = reader.i32()
        if node_count < 0 or string_buffer_size < 0:
            raise AssetError(
                f"类型树的两个长度不合法：节点数 {node_count}、字符串池 {string_buffer_size}",
                hint="元数据解析已经走偏；这通常意味着上一个字段读错了",
            )
        type_tree = TypeTree(
            node_count=node_count,
            string_buffer_size=string_buffer_size,
            nodes=reader.take(TYPE_TREE_NODE_STRIDE * node_count),
            strings=reader.take(string_buffer_size),
        )
        if version >= 21:
            if is_ref_type:
                class_name = reader.cstr()
                namespace = reader.cstr()
                assembly_name = reader.cstr()
            else:
                dep_count = reader.i32()
                if dep_count < 0:
                    raise AssetError(
                        f"type_dependencies 项数为负：{dep_count}", hint="游标已经走偏"
                    )
                type_dependencies = tuple(reader.i32() for _ in range(dep_count))
    return SerializedType(
        class_id=class_id,
        is_stripped=bool(stripped_raw),
        script_type_index=script_type_index,
        script_id=script_id,
        old_type_hash=old_type_hash,
        type_tree=type_tree,
        type_dependencies=type_dependencies,
        class_name=class_name,
        namespace=namespace,
        assembly_name=assembly_name,
        is_ref_type=is_ref_type,
    )


# --------------------------------------------------------------------------- 序列化


def encode_cstr(text: str) -> bytes:
    """`str` → UTF-8 + NUL（surrogateescape 保住原来那串字节）。"""
    return text.encode("utf-8", "surrogateescape") + b"\x00"


def encode_type_tree(tree: TypeTree, endian: str) -> bytes:
    """类型树 blob：`i32 节点数` + `i32 字符串池字节数` + 节点数组 + 字符串池。"""
    if len(tree.nodes) != TYPE_TREE_NODE_STRIDE * tree.node_count:
        raise AssetError(
            f"类型树节点数组长度不对：{len(tree.nodes)} != 32 × {tree.node_count}",
            hint="节点数组必须是 32 × 节点数；对不上说明模型被改坏了",
        )
    if len(tree.strings) != tree.string_buffer_size:
        raise AssetError(
            f"类型树字符串池长度不对：{len(tree.strings)} != {tree.string_buffer_size}",
            hint="字符串池字节数必须与声明一致",
        )
    head = struct.pack(endian + "ii", tree.node_count, tree.string_buffer_size)
    return head + tree.nodes + tree.strings


def encode_serialized_type(
    item: SerializedType, endian: str, version: int, enable_type_tree: bool
) -> bytes:
    """一项 SerializedType 的逆运算。"""
    out = bytearray()
    out += struct.pack(endian + "i", item.class_id)
    out += bytes([1 if item.is_stripped else 0])
    out += struct.pack(endian + "h", item.script_type_index)
    if item.script_id is not None:
        out += item.script_id
    out += item.old_type_hash
    if enable_type_tree and item.type_tree is not None:
        out += encode_type_tree(item.type_tree, endian)
        if version >= 21:
            if item.is_ref_type:
                out += encode_cstr(item.class_name or "")
                out += encode_cstr(item.namespace or "")
                out += encode_cstr(item.assembly_name or "")
            else:
                deps = item.type_dependencies or ()
                out += struct.pack(endian + "i", len(deps))
                for dep in deps:
                    out += struct.pack(endian + "i", dep)
    return bytes(out)


def encode_metadata_body(meta: SerializedMetadata) -> bytes:
    """元数据体（48 字节头之后、填充之前的那 `metadata_size` 字节）。"""
    header = meta.header
    endian = header.endian
    version = header.version
    enable_type_tree = meta.enable_type_tree
    out = bytearray()
    out += encode_cstr(meta.unity_version)
    out += struct.pack(endian + "i", meta.target_platform)
    out += bytes([1 if enable_type_tree else 0])
    out += struct.pack(endian + "i", len(meta.types))
    for item in meta.types:
        out += encode_serialized_type(item, endian, version, enable_type_tree)
    out += struct.pack(endian + "i", len(meta.objects))
    for entry in meta.objects:
        out += b"\x00" * ((-len(out)) % 4)
        out += struct.pack(endian + "q", entry.path_id)
        out += struct.pack(endian + "q", entry.byte_start)
        out += struct.pack(endian + "I", entry.byte_size)
        out += struct.pack(endian + "i", entry.type_id)
    out += struct.pack(endian + "i", len(meta.script_types))
    for entry in meta.script_types:
        out += struct.pack(endian + "i", entry.file_index)
        out += b"\x00" * ((-len(out)) % 4)
        out += struct.pack(endian + "q", entry.local_identifier)
    out += struct.pack(endian + "i", len(meta.externals))
    for ext in meta.externals:
        out += encode_cstr(ext.temp_empty)
        out += ext.guid
        out += struct.pack(endian + "i", ext.type)
        out += encode_cstr(ext.path)
    if version >= 20:
        out += struct.pack(endian + "i", len(meta.ref_types))
        for item in meta.ref_types:
            out += encode_serialized_type(item, endian, version, enable_type_tree)
    out += encode_cstr(meta.user_information)
    return bytes(out)


def build_metadata(meta: SerializedMetadata) -> bytes:
    """按字段**重新序列化**整段元数据（头 + 体 + 对齐填充）。

    ⚠️ 这里三个头字段是**推导**出来的、不是抄的：`metadata_size = len(体)`、
    `data_offset = align16(48 + len(体))`、填充是该长度差的**全零**。
    所以体里多一个字节、少一个字节，产物都会跟着变 —— 这正是 K1
    （读 → 写 → 逐字节相同）能当尺子的原因：写出侧**不回抄源字节**。

    ⚠️ `file_size`（头偏移 24）不在本函数的职责里：它指整个节点，容器层改节点
    大小时要自己同步（writer.write 那条链已经在做）。
    """
    body = encode_metadata_body(meta)
    header = replace(
        meta.header,
        metadata_size=len(body),
        data_offset=align_up(HEADER_SIZE + len(body), DATA_ALIGN),
    )
    if len(header.reserved) != 3:
        header = replace(header, reserved=b"\x00" * 3)
    padding = b"\x00" * (header.data_offset - HEADER_SIZE - len(body))
    return encode_header(header) + body + padding


# --------------------------------------------------------------------------- 判据


@dataclass(slots=True)
class RoundtripReport:
    """K1 读数：读 → 写 → 与源逐字节比。"""

    identical: bool
    source_len: int
    built_len: int
    first_diff: int | None
    diff_bytes: int
    metadata_size: int
    data_offset: int
    object_count: int
    type_count: int
    ref_type_count: int
    padding_size: int
    built: bytes | None = None

    def __str__(self) -> str:
        if self.identical:
            verdict = "OK"
        else:
            verdict = f"DIFF（首差 @{self.first_diff}，共 {self.diff_bytes} B）"
        return (
            f"[K1] {verdict} | 元数据区 {self.source_len} B / 写出 {self.built_len} B"
            f" | metadata_size={self.metadata_size} data_offset={self.data_offset}"
            f" | types={self.type_count} objects={self.object_count}"
            f" ref_types={self.ref_type_count} | padding={self.padding_size}"
        )


def diff_bytes(left: bytes, right: bytes) -> tuple[int | None, int]:
    """返回 `(第一个不同的偏移, 不同字节数)`；长度差也计成不同。无差别时 `(None, 0)`。"""
    limit = min(len(left), len(right))
    first: int | None = None
    count = 0
    for index in range(limit):
        if left[index] != right[index]:
            if first is None:
                first = index
            count += 1
    if len(left) != len(right):
        count += abs(len(left) - len(right))
        if first is None:
            first = limit
    return first, count


def check_roundtrip(blob: bytes, *, keep_built: bool = False) -> RoundtripReport:
    """K1：`blob` → 模型 → 重新序列化 → 与 `blob[:data_offset]` 逐字节比。

    `keep_built=True` 时产物挂在 `report.built`（多占一份内存，探针用）。
    """
    meta = parse_serialized_metadata(blob)
    built = build_metadata(meta)
    source = bytes(blob[: meta.header.data_offset])
    first, count = diff_bytes(source, built)
    return RoundtripReport(
        identical=(first is None and count == 0),
        source_len=len(source),
        built_len=len(built),
        first_diff=first,
        diff_bytes=count,
        metadata_size=meta.header.metadata_size,
        data_offset=meta.header.data_offset,
        object_count=len(meta.objects),
        type_count=len(meta.types),
        ref_type_count=len(meta.ref_types),
        padding_size=len(meta.padding),
        built=built if keep_built else None,
    )


# --------------------------------------------------------------------------- 类型树视图


def iter_type_tree_nodes(tree: TypeTree, endian: str = "<") -> Iterator[TypeTreeNode]:
    """把类型树的节点数组解成字段（`>hBBIIiiiQ`，version >= 19）。

    字符串按 UnityPy 的口径解：偏移值**最高位为 1** 表示「公共串表下标」（本模块没有
    那份表，就原样给 `#common(n)`），否则是字符串池内的字节偏移。
    """
    node_struct = struct.Struct(endian + "hBBIIiiiQ")
    for index in range(tree.node_count):
        chunk = tree.nodes[index * TYPE_TREE_NODE_STRIDE : (index + 1) * TYPE_TREE_NODE_STRIDE]
        unpacked = node_struct.unpack(chunk)
        version, level, type_flags, type_off, name_off = unpacked[:5]
        byte_size, node_index, meta_flag, ref_hash = unpacked[5:]
        yield TypeTreeNode(
            version=version,
            level=level,
            type_flags=type_flags,
            type_str_offset=type_off,
            name_str_offset=name_off,
            byte_size=byte_size,
            index=node_index,
            meta_flag=meta_flag,
            ref_type_hash=ref_hash,
            type_name=_resolve_tree_string(tree, type_off),
            name=_resolve_tree_string(tree, name_off),
        )


def _resolve_tree_string(tree: TypeTree, value: int) -> str:
    if value & COMMON_STRING_FLAG:
        return f"#common({value & ~COMMON_STRING_FLAG})"
    if value >= len(tree.strings):
        return f"#out-of-pool({value})"
    end = tree.strings.find(b"\x00", value)
    if end < 0:
        return f"#unterminated({value})"
    return tree.strings[value:end].decode("utf-8", "surrogateescape")


def type_tree_string_offsets_ok(tree: TypeTree, endian: str = "<") -> bool:
    """每个节点的两个字符串偏移都要**解得到**（公共串表下标算可解）。

    这是「32 字节节点结构读对了」的机械尺子：结构错一位，偏移就会落到池外。
    """
    node_struct = struct.Struct(endian + "hBBIIiiiQ")
    if len(tree.nodes) != TYPE_TREE_NODE_STRIDE * tree.node_count:
        return False
    for index in range(tree.node_count):
        chunk = tree.nodes[index * TYPE_TREE_NODE_STRIDE : (index + 1) * TYPE_TREE_NODE_STRIDE]
        (_v, _l, _tf, type_off, name_off, _bs, _ix, _mf, _rh) = node_struct.unpack(chunk)
        for value in (type_off, name_off):
            if value & COMMON_STRING_FLAG:
                continue
            if value >= len(tree.strings):
                return False
            if tree.strings.find(b"\x00", value) < 0:
                return False
    return True


# ------------------------------------------- 读侧入口（吃 ufs 的 reader/node）


def read_metadata_region(reader: Any, node: Any) -> bytes:
    """只取元数据区 `[0, data_offset)`（含 0~15 字节对齐填充）。

    形状与 `ufs.read_serialized_file(reader, node)` 一致：传 UfsReader + Node，
    **只读元数据区**，不把整个节点（units 是 2.5 GB）拉进内存。
    """
    head = reader.read_range(node.offset, HEADER_SIZE)
    header = parse_header(head)
    return bytes(reader.read_range(node.offset, header.data_offset))


def read_serialized_metadata(reader: Any, node: Any) -> SerializedMetadata:
    """`UfsReader` + `Node` → 元数据模型（只读元数据区，不碰对象数据）。"""
    return parse_serialized_metadata(read_metadata_region(reader, node))


__all__ = [
    "COMMON_STRING_FLAG",
    "DATA_ALIGN",
    "HEADER_SIZE",
    "MONO_BEHAVIOUR_CLASS_ID",
    "OBJECT_ENTRY_STRIDE",
    "SCRIPT_ENTRY_STRIDE",
    "TYPE_TREE_NODE_STRIDE",
    "ExternalRef",
    "Layout",
    "ObjectEntry",
    "RoundtripReport",
    "ScriptTypeEntry",
    "SerializedFileHeader",
    "SerializedMetadata",
    "SerializedType",
    "TypeTree",
    "TypeTreeNode",
    "align_up",
    "build_metadata",
    "check_roundtrip",
    "diff_bytes",
    "encode_cstr",
    "encode_header",
    "encode_metadata_body",
    "encode_serialized_type",
    "encode_type_tree",
    "iter_type_tree_nodes",
    "layout",
    "parse_header",
    "parse_serialized_metadata",
    "read_metadata_region",
    "read_serialized_metadata",
    "type_tree_string_offsets_ok",
]
