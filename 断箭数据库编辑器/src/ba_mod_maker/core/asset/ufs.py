"""UnityFS 容器（`data.unity3d`）的选择性读取（零 Qt）。

## 为什么不用 UnityPy 读它

`UnityPy.BundleFile.read_fs()` 会做 `b"".join(decompress_data(b) for b in m_BlocksInfo)`
—— 把**整个解压流**塞进内存。实测 `data.unity3d` 解压后是 **13.672 GiB**，不可能。

所以这里手写一个**按需解块**的读取器：只解压覆盖目标区间的那些块，
每块约 128 KiB，解完就丢。

## 实测读数（本机当前版本）

| 项 | 值 |
|---|---|
| 文件 | 7,122,751,635 B（6.634 GiB） |
| 块数 | 112,000 |
| 解压流总长 | 14,680,047,698 B（13.672 GiB） |
| 压缩率 | 0.485（其中 11,336 块是原样存储） |
| 数据起点 | 367,712 |

`globalgamemanagers.assets` 节点在 **块 18..5141**，`DataBaseCompiled` 对象落在**块 5036**
（文件偏移约 413 MB 处）—— 即"未压缩流的 4.5%"。这个位置决定了写回的成本（见 kb/11）。
"""

from __future__ import annotations

import bisect
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ba_mod_maker.core.errors import AssetError

#: 大端，UnityFS 的所有头部字段都是大端。
BE = ">"


@dataclass(slots=True)
class Block:
    """一个块。`uncompressed`/`compressed` 是字节数，`flags` 低 6 位是压缩类型。"""

    uncompressed: int
    compressed: int
    flags: int

    @property
    def compress_type(self) -> int:
        return self.flags & 0x3F


@dataclass(slots=True)
class Node:
    """容器里的一个节点（相当于"文件"）。`offset`/`size` 是**解压流**坐标。"""

    offset: int
    size: int
    flags: int
    path: str
    #: 覆盖它的块区间（闭区间），由 :meth:`UfsReader._index_nodes` 填。
    blk0: int = 0
    blk1: int = 0


@dataclass(slots=True)
class UfsHeader:
    """UnityFS 文件头 + blocksInfo 的摘要。"""

    signature: str
    version: int
    version_player: str
    version_engine: str
    declared_size: int
    compressed_blocks_info_size: int
    uncompressed_blocks_info_size: int
    flags: int
    blocks_info_offset: int
    data_start: int
    data_end: int
    data_hash: bytes

    @property
    def compress_type(self) -> int:
        return self.flags & 0x3F

    @property
    def blocks_info_at_end(self) -> bool:
        return bool(self.flags & 0x80)

    @property
    def blocks_info_pad_start(self) -> bool:
        return bool(self.flags & 0x200)


class UfsReader:
    """只读的 UnityFS 读取器。

    用法::

        with UfsReader(path) as r:
            node = r.node("globalgamemanagers.assets")
            blob = r.read_range(node.offset, node.size)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._fh = self.path.open("rb")
        except FileNotFoundError:
            raise AssetError(
                f"找不到容器文件：{self.path}",
                hint="确认路径；database 通道要的是 <游戏根>/BrokenArrow_Data/data.unity3d",
            ) from None
        self.file_size = self.path.stat().st_size
        #: ★ 2026-10-18：**最近解压过的块**（LRU）。
        #: 实测：拿一件（T-90M 车体）要读 ~250 个小对象，每个 `read_range` 都会把它所在那
        #: 一块**重新解压一遍** ⇒ 5~6 s 全耗在这里（块解压是 lz4，单块没问题，问题在重复）。
        #: 上限 32 块（每块 ≤128 KB）≈ 最多 4 MB 内存。
        self._block_cache: dict[int, bytes] = {}
        try:
            self._read_header()
            self._read_blocks_info()
            self._index_blocks()
            self._index_nodes()
        except Exception:
            self._fh.close()
            raise

    # ---------------- 上下文管理 ----------------

    def __enter__(self) -> UfsReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    # ---------------- 低层 ----------------

    def _read_at(self, offset: int, size: int) -> bytes:
        self._fh.seek(offset)
        data = self._fh.read(size)
        if len(data) != size:
            raise AssetError(
                f"读取越界：在 {offset} 处想要 {size} 字节，只拿到 {len(data)}",
                hint="文件可能被截断",
            )
        return data

    def _cstr(self, offset: int) -> tuple[str, int]:
        """读一个以 NUL 结尾的 UTF-8 串。"""
        self._fh.seek(offset)
        buf = bytearray()
        while True:
            ch = self._fh.read(1)
            if not ch or ch == b"\x00":
                break
            buf += ch
        return buf.decode("utf-8", "surrogateescape"), offset + len(buf) + 1

    @staticmethod
    def align(pos: int, n: int = 16) -> int:
        return pos + ((n - pos % n) % n)

    # ---------------- 头部 ----------------

    def _read_header(self) -> None:
        signature, pos = self._cstr(0)
        if signature != "UnityFS":
            raise AssetError(
                f"不是 UnityFS 容器：签名是 {signature!r}",
                hint="这个文件不是 data.unity3d；或者它是压缩/加密过的另一种容器",
            )
        (version,) = struct.unpack(BE + "I", self._read_at(pos, 4))
        pos += 4
        version_player, pos = self._cstr(pos)
        version_engine, pos = self._cstr(pos)
        declared_size, cbis, ubis, flags = struct.unpack(BE + "qIII", self._read_at(pos, 20))
        pos += 20

        self.header = UfsHeader(
            signature=signature,
            version=version,
            version_player=version_player,
            version_engine=version_engine,
            declared_size=declared_size,
            compressed_blocks_info_size=cbis,
            uncompressed_blocks_info_size=ubis,
            flags=flags,
            blocks_info_offset=0,
            data_start=0,
            data_end=0,
            data_hash=b"",
        )
        if version >= 7:
            self.header.blocks_info_offset = self.align(pos, 16)
        else:
            self.header.blocks_info_offset = pos
        if self.header.blocks_info_at_end:
            # blocksInfo 在文件末尾：从文件尾倒推。
            self.header.blocks_info_offset = self.file_size - cbis

    # ---------------- blocksInfo ----------------

    def decompress(self, data: bytes, out_size: int, ctype: int) -> bytes:
        if ctype == 0:
            return data
        if ctype in (2, 3):
            import lz4.block

            return lz4.block.decompress(data, out_size)
        if ctype == 1:
            import lzma

            props, dict_size = struct.unpack("<BI", data[:5])
            lc = props % 9
            rem = props // 9
            pb = rem // 5
            lp = rem % 5
            dec = lzma.LZMADecompressor(
                format=lzma.FORMAT_RAW,
                filters=[
                    {
                        "id": lzma.FILTER_LZMA1,
                        "dict_size": dict_size,
                        "lc": lc,
                        "lp": lp,
                        "pb": pb,
                    }
                ],
            )
            return dec.decompress(data[5:])
        raise AssetError(
            f"不支持的压缩类型 {ctype}",
            hint="实测本游戏的块只用了 0(原样)/2,3(LZ4)/1(LZMA)",
        )

    def _read_blocks_info(self) -> None:
        h = self.header
        raw = self._read_at(h.blocks_info_offset, h.compressed_blocks_info_size)
        info = self.decompress(raw, h.uncompressed_blocks_info_size, h.compress_type)

        pos = 0
        h.data_hash = info[pos : pos + 16]
        pos += 16
        (n_blocks,) = struct.unpack_from(BE + "i", info, pos)
        pos += 4
        blocks: list[Block] = []
        for _ in range(n_blocks):
            ucs, cs, fl = struct.unpack_from(BE + "IIH", info, pos)
            pos += 10
            blocks.append(Block(ucs, cs, fl))
        (n_nodes,) = struct.unpack_from(BE + "i", info, pos)
        pos += 4
        nodes: list[Node] = []
        for _ in range(n_nodes):
            off, size = struct.unpack_from(BE + "qq", info, pos)
            pos += 16
            (fl,) = struct.unpack_from(BE + "I", info, pos)
            pos += 4
            end = info.index(b"\x00", pos)
            name = info[pos:end].decode("utf-8", "surrogateescape")
            pos = end + 1
            nodes.append(Node(offset=off, size=size, flags=fl, path=name))

        self.blocks = blocks
        self.nodes = nodes
        self.blocks_info_parsed_bytes = pos
        self.blocks_info_raw_size = len(info)

        dstart = h.blocks_info_offset + h.compressed_blocks_info_size
        if h.blocks_info_pad_start:
            dstart = self.align(dstart, 16)
        h.data_start = dstart

    # ---------------- 索引 ----------------

    def _index_blocks(self) -> None:
        """算出每个块的解压流起点与文件偏移。"""
        starts: list[int] = []
        file_offs: list[int] = []
        stream_pos = 0
        file_pos = self.header.data_start
        for blk in self.blocks:
            starts.append(stream_pos)
            file_offs.append(file_pos)
            stream_pos += blk.uncompressed
            file_pos += blk.compressed
        self.block_dstart = starts
        self.block_foff = file_offs
        self.total_uncompressed = stream_pos
        self.header.data_end = file_pos

    def _index_nodes(self) -> None:
        for node in self.nodes:
            i0 = bisect.bisect_right(self.block_dstart, node.offset) - 1
            i1 = (
                bisect.bisect_left(self.block_dstart, node.offset + node.size) - 1
                if node.size
                else i0
            )
            node.blk0 = max(i0, 0)
            node.blk1 = max(i1, i0, 0)

    # ---------------- 选择性读取 ----------------

    def read_range(self, offset: int, size: int) -> bytes:
        """读**解压流**的 `[offset, offset+size)`，只解压覆盖到的块。"""
        if size <= 0:
            return b""
        if offset < 0 or offset + size > self.total_uncompressed:
            raise AssetError(
                f"区间越界：想要 [{offset}, {offset + size})，解压流只有 {self.total_uncompressed}",
                hint="节点表的 offset/size 与块表不一致，文件可能损坏",
            )
        out = bytearray()
        starts = self.block_dstart
        i = max(bisect.bisect_right(starts, offset) - 1, 0)
        need_end = offset + size
        pos = offset
        while pos < need_end:
            if i >= len(self.blocks):
                raise AssetError("读到解压流末尾但还没读够", hint="块表与节点表不一致")
            blk = self.blocks[i]
            bstart = starts[i]
            bend = bstart + blk.uncompressed
            if bend <= pos:
                i += 1
                continue
            dec = self._decompress_block(i)
            take_end = min(bend, need_end)
            out += dec[pos - bstart : take_end - bstart]
            pos = take_end
            i += 1
        return bytes(out)

    def _decompress_block(self, index: int) -> bytes:
        cached = self._block_cache.get(index)
        if cached is not None:
            return cached
        blk = self.blocks[index]
        raw = self._read_at(self.block_foff[index], blk.compressed)
        dec = self.decompress(raw, blk.uncompressed, blk.compress_type)
        if len(dec) != blk.uncompressed:
            raise AssetError(
                f"块 {index} 解压后是 {len(dec)} 字节，块表声明 {blk.uncompressed}",
                hint="文件可能损坏",
            )
        if len(self._block_cache) >= 32:
            # 只保留最近 32 块：拿一件时读的小对象是**聚簇**的，这个量足够挡住重复解压。
            self._block_cache.pop(next(iter(self._block_cache)))
        self._block_cache[index] = dec
        return dec

    # ---------------- 节点 ----------------

    def node(self, path: str) -> Node:
        """按路径取节点。找不到抛 `AssetError`。"""
        for node in self.nodes:
            if node.path == path:
                return node
        raise AssetError(
            f"容器里没有节点 {path!r}",
            hint=f"共有 {len(self.nodes)} 个节点；DataBaseCompiled 在 globalgamemanagers.assets 里",
        )

    def node_bytes(self, node: Node) -> bytes:
        """把一个节点的全部字节读出来（解压后）。"""
        return self.read_range(node.offset, node.size)

    def phys_offset(self, stream_offset: int) -> int:
        """解压流偏移 -> **文件**偏移（近似：块内线性）。

        只用于给用户看"这个对象在文件的百分之几处"，不用于寻址。
        """
        i = max(bisect.bisect_right(self.block_dstart, stream_offset) - 1, 0)
        return self.block_foff[i] + (stream_offset - self.block_dstart[i])

    def summary(self) -> dict[str, object]:
        """给人看的摘要（诊断与日志用）。"""
        from collections import Counter

        kinds = Counter(b.compress_type for b in self.blocks)
        return {
            "path": str(self.path),
            "file_size": self.file_size,
            "version": self.header.version,
            "version_player": self.header.version_player,
            "version_engine": self.header.version_engine,
            "flags": self.header.flags,
            "blocks": len(self.blocks),
            "nodes": len(self.nodes),
            "block_compression": dict(kinds),
            "uncompressed_size": self.total_uncompressed,
            "data_start": self.header.data_start,
        }


# --------------------------------------------------------------------------- #
# SerializedFile 层：在节点里找 DataBaseCompiled 对象
# --------------------------------------------------------------------------- #

#: `DataBaseCompiled` 的 MonoScript m_PathID（**1.2.0 实测**，两个实例一致）。
#: ⚠️ 游戏更新后**会变**（1.2.1 实测 = 13535，对象头 48 → 52 字节）。
#: 所以**不把它当搜索条件**，只当交叉验证的参考值。
DATABASE_SCRIPT_PATH_ID = 13249

#: 对象名字符串。用来认定"这个对象是不是数据库"。
DATABASE_OBJECT_NAME = b"DataBaseCompiled"

#: 找名字时往后看的窗口。头只有几十字节，给足余量；⛔ 别用 48 ——
#: 1.2.1 的头是 52 字节，名字落在 [36:52]，正好被 48 切掉一半（见 _name_is_length_prefixed）。
DATABASE_NAME_SCAN = 1024


def _name_is_length_prefixed(blob: bytes) -> bool:
    """`blob` 的头里有一个**带长度前缀**的 `DataBaseCompiled` 字符串吗（= 这是个数据库对象）。

    **为什么不写死 `in blob[:48]`**：2026-10-13 实测 1.2.1 的对象头是 **52** 字节、
    名字落在 [36:52] ⇒ `blob[:48]` 把它切成 `\x08\x00\x00\x00DataBaseCo`，**判据恒为假**
    ⇒ 容器里 5 个对象一个都找不到 ⇒ 产品报「对象里找不到任何加密表字段」。
    这里只做**粗筛**（真伪由 `container.ObjectMeta.parse` 定），所以判据放宽到"名字带对长度前缀"。
    """
    name_len = len(DATABASE_OBJECT_NAME)
    at = blob.find(DATABASE_OBJECT_NAME, 0, DATABASE_NAME_SCAN)
    while at >= 0:
        if at >= 4 and struct.unpack_from("<i", blob, at - 4)[0] == name_len:
            return True
        at = blob.find(DATABASE_OBJECT_NAME, at + 1, DATABASE_NAME_SCAN)
    return False


@dataclass(slots=True)
class SerializedFileInfo:
    """一个 SerializedFile 节点被解析出来的结构摘要。"""

    node: Node
    data_offset: int
    object_count: int
    objects: list[tuple[int, int, int, int, int]]
    """`[(表项下标, path_id, 节点内偏移 stored, byte_size, type_id)]`。

    ⚠️ `stored` 是**相对 data_offset** 的偏移；对象的绝对流位置是
    `节点.offset + data_offset + stored`。
    """

    metadata_size: int
    #: 对象表第一项在**元数据字节**里的偏移。写回时要用它改 byte_start。
    object_table_offset: int
    #: 对象表项步长（实测 24：i64 path_id + i64 byte_start + u32 byte_size + i32 type_id）。
    object_entry_stride: int


def _read_header_v22(blob: bytes) -> dict[str, int]:
    """SerializedFile 头（**大端**，固定 48 字节）。

    实测布局（与 `_work/sfmeta.py` 的 `parse_header` 逐字段一致）：

    | 偏移 | 宽度 | 含义 |
    |---|---|---|
    | 0  | 4 | legacy 元数据大小 |
    | 4  | 4 | legacy 文件大小 |
    | 8  | 4 | **version**（本机 = 22） |
    | 12 | 4 | legacy 数据偏移 |
    | 16 | 1 | **endian 标志**（0 = 小端） |
    | 17 | 3 | 保留 |
    | 20 | 4 | **metadata_size**（实测**不含**这 48 字节的头） |
    | 24 | 8 | **file_size**（应等于节点大小） |
    | 32 | 8 | **data_offset**（对象数据的起点） |
    | 40 | 8 | unknown |

    ⚠️ "metadata_size 含不含头"是个**歧义点**：本机数据里
    头部声明 `1,601,993`，而对象表解析结束时落在 `48 + 1,601,993 = 1,602,041`。
    所以**解析区间 = header_size + metadata_size**（参考实现也是这么算的）。
    """
    if len(blob) < 48:
        raise AssetError(f"SerializedFile 头不足 48 字节（只有 {len(blob)}）")
    version = struct.unpack_from(BE + "I", blob, 8)[0]
    endian_flag = blob[16]
    return {
        "version": version,
        "little": "<" if endian_flag == 0 else ">",
        "metadata_size": struct.unpack_from(BE + "I", blob, 20)[0],
        "file_size": struct.unpack_from(BE + "q", blob, 24)[0],
        "data_offset": struct.unpack_from(BE + "q", blob, 32)[0],
        "header_size": 48,
    }


class _Cursor:
    """按指定字节序推进的小游标。只为读 SerializedFile 元数据体存在。

    ⚠️ **每一次读都先判边界**（_need）：越界抛 AssetError，带上「游标在哪 / 要读多少 /
    缓冲区多大」。从前这里是直接 struct.unpack_from —— 元数据被截断时，冒到调用方的是
    一条裸 struct.error（"unpack_from requires a buffer of at least N bytes"），
    既没有上下文、也 catch 不住（它不是 BaModMakerError 的子类）。
    负控见 tests/test_ufs_types_cursor.py::test_truncated_metadata_raises_readable_asset_error。
    """

    __slots__ = ("buf", "endian", "pos")

    def __init__(self, buf: bytes, endian: str, pos: int = 0) -> None:
        self.buf = buf
        self.endian = endian
        self.pos = pos

    def _need(self, n: int) -> None:
        """确认接下来 n 字节在缓冲区内；不在就抛**可读的** AssetError。"""
        if n < 0 or self.pos < 0 or self.pos + n > len(self.buf):
            raise AssetError(
                f"元数据游标越界：想在 {self.pos} 读 {n} 字节，缓冲区只有 {len(self.buf)} 字节",
                hint="元数据被截断，或布局与本解析器不符 —— 先确认节点选对了、字节没被砍",
                cursor=self.pos,
                need=n,
                buffer_size=len(self.buf),
            )

    def u8(self) -> int:
        self._need(1)
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def i16(self) -> int:
        self._need(2)
        (v,) = struct.unpack_from(self.endian + "h", self.buf, self.pos)
        self.pos += 2
        return v

    def i32(self) -> int:
        self._need(4)
        (v,) = struct.unpack_from(self.endian + "i", self.buf, self.pos)
        self.pos += 4
        return v

    def i64(self) -> int:
        self._need(8)
        (v,) = struct.unpack_from(self.endian + "q", self.buf, self.pos)
        self.pos += 8
        return v

    def take(self, n: int) -> bytes:
        self._need(n)
        v = bytes(self.buf[self.pos : self.pos + n])
        self.pos += n
        return v

    def cstr(self) -> str:
        end = self.buf.find(b"\x00", self.pos)
        if end < 0:
            raise AssetError(
                f"字符串没找到结尾的 0 字节：从 {self.pos} 一直找到缓冲区末尾（{len(self.buf)}）",
                hint="元数据被截断，或布局与本解析器不符",
                cursor=self.pos,
                buffer_size=len(self.buf),
            )
        s = self.buf[self.pos : end].decode("utf-8", "surrogateescape")
        self.pos = end + 1
        return s

    def align(self, n: int = 4) -> None:
        self._need((-self.pos) % n)
        self.pos += (-self.pos) % n


#: MonoBehaviour —— 类型表里**唯一**无条件带 16 字节 script_id 的类。
#: ⚠️ 判据是 **classID**，不是 `script_type_index >= 0`：2026-10-08 全库 79 包实测，
#: 有 **6 项** `classID=114 且 script_type_index = -1`（prefabsgui 2 / prefabsme 4），
#: 反方向的（classID != 114 且 index >= 0）**0 项**。
#: 与 `asset_kernel/metadata_writer.py::_read_type`（对齐 UnityPy 1.25.3 的
#: SerializedType.__init__）同口径。
MONO_BEHAVIOUR_CLASS_ID = 114


def _walk_type_table(cur: _Cursor, version: int, enable_type_tree: bool) -> int:
    """走一遍类型表（游标停在表之后），返回项数。

    格式（每一项）：

        i32 class_id · u8 is_stripped · i16 script_type_index
        [16 B script_id]        # ⚠️ 只在 class_id == 114 时存在（见 MONO_BEHAVIOUR_CLASS_ID）
        16 B old_type_hash
        [内嵌类型树]            # 只在 enable_type_tree 时存在
        [i32 deps + 4×deps]     # 只在 version >= 21 时存在

    ⛔ 每一项的开头**不做对齐**（见 read_serialized_file 的坑 2）。
    """
    type_count = cur.i32()
    if type_count < 0 or type_count > 100_000:
        raise AssetError(
            f"类型表项数不合法：{type_count}",
            hint="元数据解析已经走偏；这通常意味着上一个字段读错了",
        )
    for _ in range(type_count):
        class_id = cur.i32()
        cur.u8()  # is_stripped
        cur.i16()  # script_type_index
        if class_id == MONO_BEHAVIOUR_CLASS_ID:
            cur.take(16)  # script_id
        cur.take(16)  # old_type_hash
        if enable_type_tree:
            _skip_type_tree(cur, version)
            if version >= 21:
                deps = cur.i32()
                if deps < 0 or deps > 4096:
                    raise AssetError(
                        f"type_dependencies 条数不合法：{deps}",
                        hint="元数据解析已经走偏；这通常意味着上一个字段读错了",
                    )
                cur.take(4 * deps)
    return type_count

def read_serialized_file(reader: UfsReader, node: Node) -> SerializedFileInfo:
    """读一个 SerializedFile 节点的对象表。

    字段布局**逐字段照抄** `_work/sfmeta.py`（它按 UnityPy 1.25.3 写，跑通过本机
    全部 91 个节点），并且用"同一个 R 游标跑同一段代码"的方式逐项对拍过。

    ## 踩过的坑（都记在这里，别再走）

    1. **SerializedFile 头是 48 字节，但字段偏移不是顺序直觉**：
       version 在 **8**、metadata_size 在 **20**、file_size 在 **24**、data_offset 在 **32**。
    2. **类型表的每一项开头【不做对齐】**。
       这一条最坑：加上 `align(4)` 会稳定地少走 **1,951 字节**（591 项 × 平均 3.3 字节），
       而末尾那条"必须落在 header_size + metadata_size"的硬对账**恰好抓得住**它 ——
       这正是那条对账存在的意义。
    3. **类型树 blob 是"长度前缀字符串池"，不是偏移量表**：
       `[i32 节点数][i32 字符串池字节数][32×节点数 的节点数组][字符串池]`，
       节点 struct 是 `>hBBIIiiiQ`（version>=19）。
    4. **`enable_type_tree == False` 时没有类型树，也没有 type_dependencies**。
       本机的 `globalgamemanagers.assets` 就是这种情况。
    5. **对齐只出现在**：对象项的**开头**（每项 `align4` 后再读 24 字节）。
       对象数那一格之前**不对齐**。
    6. **`script_id` 的判据是 `classID == 114`，不是 `script_type_index >= 0`**
       （2026-10-08 更正，本文件最隐蔽的一处）。全库 79 包实测：有 **6 项**
       `classID=114 且 script_type_index = -1`（prefabsgui 2 / prefabsme 4），
       反方向的 `classID != 114 且 index >= 0` **0 项**。按后者判会在这 6 项上**少读**
       16 字节 ⇒ 游标从此错位 ⇒ 下一项的 node_count 读成垃圾 ⇒ 一路跑到 17,063,036
       （元数据只有 3,583,522 B），最后冒一条裸 `struct.error`。
       复算：`_work/mono-line/ufs-cursor-diag.py` / `ufs-types-census.py`；
       口径与 `asset_kernel/metadata_writer.py::_read_type` 一致（它对齐 UnityPy 1.25.3）。
    """
    head = reader.read_range(node.offset, 48)
    hdr = _read_header_v22(head)
    if hdr["version"] < 22:
        raise AssetError(
            f"只支持 SerializedFile version >= 22，实测到 {hdr['version']}",
            hint="本机游戏是 22；更老的版本对象表布局不同，需要另写分支",
        )
    if hdr["file_size"] != node.size:
        raise AssetError(
            "SerializedFile 的 file_size 与节点大小不一致",
            hint="实测两者必须相等；不等说明取错了节点或文件已损坏",
            sf_file_size=hdr["file_size"],
            node_size=node.size,
        )

    span = hdr["header_size"] + hdr["metadata_size"]
    meta = reader.read_range(node.offset, span)
    cur = _Cursor(meta, hdr["little"], hdr["header_size"])
    version = hdr["version"]

    cur.cstr()  # unity_version
    cur.i32()  # target_platform
    enable_type_tree = bool(cur.u8())
    type_count = _walk_type_table(cur, version, enable_type_tree)

    object_count = cur.i32()
    objects: list[tuple[int, int, int, int, int]] = []
    first_entry = -1
    stride = 0
    for i in range(object_count):
        cur.align(4)
        if first_entry < 0:
            first_entry = cur.pos
        elif stride == 0:
            stride = cur.pos - first_entry
        path_id = cur.i64()
        stored = cur.i64()
        byte_size = cur.i32()
        type_id = cur.i32()
        objects.append((i, path_id, stored, byte_size, type_id))

    expect = hdr["header_size"] + hdr["metadata_size"]
    if cur.pos > expect:
        raise AssetError(
            f"元数据解析越界：走到 {cur.pos}，声明结束于 {expect}（多 {cur.pos - expect:+d}）",
            hint="SerializedFile 结构与本产品的解析器不符；游戏更新后需要复核",
            enable_type_tree=enable_type_tree,
            type_count=type_count,
            object_count=object_count,
        )
    return SerializedFileInfo(
        node=node,
        data_offset=hdr["data_offset"],
        object_count=object_count,
        objects=objects,
        metadata_size=hdr["metadata_size"],
        object_table_offset=first_entry,
        object_entry_stride=stride or 24,
    )


def _skip_type_tree(cur: _Cursor, version: int) -> None:
    """跳过一段内嵌类型树。

    格式（UnityPy `TypeTreeNode.parse_blob`，实测对齐）：

        int32  node_count
        int32  stringbuffer_size        # 字节数
        32 * node_count 字节的节点数组   # struct ">hBBIIiiiQ"，version>=19
        stringbuffer_size 字节的字符串池

    ⚠️ 只有 `enable_type_tree` 打开时才有这段。本机的
    `globalgamemanagers.assets` 实测它是**关的**，所以这条分支在本机没被执行过；
    写在这里是为了换游戏版本 / 换节点时不至于崩。
    """
    node_count = cur.i32()
    buffer_size = cur.i32()
    if node_count < 0 or buffer_size < 0:
        raise AssetError(
            f"类型树的两个长度不合法：节点数 {node_count}、字符串池 {buffer_size}",
            hint="元数据解析已经走偏；这通常意味着上一个字段读错了",
        )
    cur.take(32 * node_count + buffer_size)  # 越界由 _Cursor 报可读的 AssetError


def find_database_objects(reader: UfsReader) -> list[tuple[int, bytes]]:
    """在 `globalgamemanagers.assets` 里找出所有 `DataBaseCompiled` 对象。

    返回 `[(path_id, 对象字节)]`，**按 pathID 升序**。

    ⚠️ 实测同一个容器里有**两个**：52082（战役）与 52085（默认非战役）。
    它们**不是同一份数据的两个版本，是已经分叉的两份库** ——
    共享的 478 个单位里有 **200 个 `Cost` 不同**（本轮实测）。
    产品的口径是**取 pathID 最大的那个**（用户拍板），另一个原样不动。
    """
    node = reader.node("globalgamemanagers.assets")
    info = read_serialized_file(reader, node)
    found: list[tuple[int, bytes]] = []
    for _i, path_id, stored, byte_size, _type_id in info.objects:
        if byte_size < 1024:
            continue
        blob = reader.read_range(node.offset + info.data_offset + stored, byte_size)
        if _name_is_length_prefixed(blob):
            found.append((path_id, blob))
    found.sort(key=lambda pair: pair[0])
    if not found:
        raise AssetError(
            "容器里找不到 DataBaseCompiled 对象",
            hint="它应当在 globalgamemanagers.assets 里；游戏版本可能变了",
        )
    return found


# --------------------------------------------------------------------------- #
# 写侧：变长重写
# --------------------------------------------------------------------------- #

#: 重切块时每块的目标大小。实测 Unity 用的就是 131072（128 KiB）。
RECHUNK_SIZE = 128 * 1024

#: LZ4HC 压缩级别。实测 12 级与 Unity 的块压缩器**逐字节相同**（kb/08）。
#: 换级别会让"未改动的块"也不再逐字节相等，所以别动。
LZ4HC_LEVEL = 12

#: SerializedFile 头长度（实测 48，见 _read_header_v22）。
#: **元数据区 = 这个 48 + 头部声明的 metadata_size** ——
#: 不是「节点大小 - data_offset」，那个是**对象数据区**的长度。
SF_HEADER_SIZE = 48

#: 一次重切块批的大小（喂给线程池）。
_CHUNK_BATCH = 512


def lz4hc(data: bytes) -> bytes:
    """Unity 用的块压缩：LZ4HC 12 级，不写长度前缀。"""
    import lz4.block

    return lz4.block.compress(
        data, store_size=False, mode="high_compression", compression=LZ4HC_LEVEL
    )


def serialize_blocks_info(data_hash: bytes, blocks: list[Block], nodes: list[Node]) -> bytes:
    """把块表 + 节点表序列化回 blocksInfo（未压缩）。"""
    out = bytearray(data_hash)
    out += struct.pack(BE + "i", len(blocks))
    for blk in blocks:
        out += struct.pack(BE + "IIH", blk.uncompressed, blk.compressed, blk.flags)
    out += struct.pack(BE + "i", len(nodes))
    for nd in nodes:
        out += struct.pack(BE + "qq", nd.offset, nd.size)
        out += struct.pack(BE + "I", nd.flags)
        out += nd.path.encode("utf-8") + b"\x00"
    return bytes(out)


def serialize_header(
    version: int,
    version_player: str,
    version_engine: str,
    size: int,
    compressed_blocks_info_size: int,
    uncompressed_blocks_info_size: int,
    flags: int,
) -> bytes:
    """序列化 UnityFS 头（大端；version>=7 时补齐到 16）。"""
    out = bytearray()
    out += b"UnityFS\x00" + struct.pack(BE + "I", version)
    out += version_player.encode() + b"\x00" + version_engine.encode() + b"\x00"
    out += struct.pack(
        BE + "qIII",
        size,
        compressed_blocks_info_size,
        uncompressed_blocks_info_size,
        flags,
    )
    if version >= 7:
        out += bytes((-len(out)) % 16)
    return bytes(out)


def _patch_metadata(
    meta: bytearray, node: Node, info: SerializedFileInfo, changes: list[tuple[int, int, int]]
) -> bytes:
    """改元数据里的**三**处，都在同一长度内原地改（所以不会移动任何东西）：

    1. SerializedFile 头里的 file_size（= 节点的新大小，跟着节点一起长）；
    2. 对象表里每个对象的 byte_start —— 在它之前的插入点之后的都要平移；
    3. 对象表里**被整段换掉的那个对象**的 byte_size。

    ⚠️ 第 3 条是工单 W1 §7 坑 3：旧实现只写了表项的 +8（byte_start），
    漏了 +16（byte_size）⇒ 目标对象变长之后读回来还是**旧长度**。

    参数
    ----
    changes
        [(解压流绝对起点, 绝对终点, 长度增量)]。
        **只有整段落在某个对象区间内**才算「这个对象变了」——
        那才该改它的 byte_size。
    """
    patched = bytearray(meta)
    node_lo, node_hi = node.offset, node.offset + node.size
    inside = sum(d for s, e, d in changes if s >= node_lo and e <= node_hi)
    patched[24:32] = struct.pack(BE + "q", struct.unpack_from(BE + "q", patched, 24)[0] + inside)

    base = node.offset + info.data_offset
    for i, _path_id, stored, byte_size, _tid in info.objects:
        abs_start = base + stored
        abs_end = abs_start + byte_size
        shift = sum(d for s, e, d in changes if e <= abs_start)
        grow = sum(d for s, e, d in changes if s >= abs_start and e <= abs_end)
        at = info.object_table_offset + i * info.object_entry_stride
        if shift:
            patched[at + 8 : at + 16] = struct.pack("<q", stored + shift)
        if grow:
            patched[at + 16 : at + 20] = struct.pack("<i", byte_size + grow)
    return bytes(patched)


@dataclass(slots=True)
class Replacement:
    """要替换的一段**解压流**区间，以及它的新内容。

    实测（kb/08）要求 `start` 落在**块边界**上，否则要处理"块内偏移"的两套坐标，
    白白增加出错面。上层不必操心：`UfsWriter.write` 会把区间向外扩展到块边界。
    """

    start: int
    end: int
    payload: bytes

    @property
    def delta(self) -> int:
        return len(self.payload) - (self.end - self.start)


@dataclass(slots=True)
class WriteReport:
    """一次写回的结果读数。给三道闸的第三道与日志用。"""

    target: Path
    old_size: int
    new_size: int
    old_blocks: int
    new_blocks: int
    verbatim_blocks: int
    recompressed_blocks: int
    seconds: float
    replacements: int
    delta: int

    def summary(self) -> str:
        return (
            f"{self.target.name}: {self.old_size:,} -> {self.new_size:,} B  "
            f"块 {self.old_blocks:,} -> {self.new_blocks:,}（原样 {self.verbatim_blocks:,} / "
            f"重压 {self.recompressed_blocks:,}）  耗时 {self.seconds:.1f}s"
        )


class _SpliceReader:
    """按**新流坐标**顺序读出"原样段"的解压字节。

    ⚠️ 为什么不能用普通的顺序读：
    原样段在新流里**不连续** —— 被替换的区间是空洞，必须跳过。
    普通顺序读会从错的位置继续，产出的文件"看起来能生成、其实内容错位"。
    参考实现（`_work/e10_e1.py`）也专门写了这个跳转读。
    """

    def __init__(self, reader: UfsReader, src, pos: int) -> None:
        self.reader = reader
        self.fh = src
        self.pos = pos
        self.i = max(bisect.bisect_right(reader.block_dstart, pos) - 1, 0)
        self._load()

    def _load(self) -> None:
        i = self.i
        blk = self.reader.blocks[i]
        self.fh.seek(self.reader.block_foff[i])
        raw = self.fh.read(blk.compressed)
        self.cur = (
            raw
            if blk.compress_type == 0
            else self.reader.decompress(raw, blk.uncompressed, blk.compress_type)
        )
        self.bstart = self.reader.block_dstart[i]
        self.bend = self.bstart + blk.uncompressed

    def seek_to(self, pos: int) -> None:
        if self.bstart <= pos < self.bend:
            self.pos = pos
            return
        self.pos = pos
        self.i = max(bisect.bisect_right(self.reader.block_dstart, pos) - 1, 0)
        self._load()

    def read(self, n: int) -> bytes:
        out = bytearray()
        while n > 0:
            if self.pos >= self.bend:
                self.i += 1
                self._load()
            take = min(n, self.bend - self.pos)
            off = self.pos - self.bstart
            out += self.cur[off : off + take]
            self.pos += take
            n -= take
        return bytes(out)


def _block_of(reader: UfsReader, pos: int) -> int:
    """解压流位置 -> 覆盖它的块下标。"""
    return max(bisect.bisect_right(reader.block_dstart, pos) - 1, 0)


def _assert_disjoint(reps: list[Replacement], what: str) -> None:
    """区间必须互不相交。重叠是「能生成、读不回来」这类事故的入口（W1 §7 坑 5）。"""
    for i in range(1, len(reps)):
        a, b = reps[i - 1], reps[i]
        if b.start < a.end:
            raise AssetError(
                f"{what}重叠：[{a.start}, {a.end}) 与 [{b.start}, {b.end})",
                hint="区间必须互不相交；静默重叠会产出一个读不回来的包",
            )


def _recompute_nodes(nodes: list[Node], changes: list[tuple[int, int, int]]) -> list[Node]:
    """按长度增量重算节点表。

    ⚠️ W1 §7 坑 4：旧实现把**旧的** reader.nodes 原样写回 blocksInfo。
    布局变了而节点表不变 ⇒ 节点 size 与 SerializedFile 头 file_size 对不上，
    产品自己读不回来。

    节点的 offset / size 都是**解压流**坐标，所以：
    - 落在节点**之前**的增量 ⇒ 偏移平移；
    - 落在节点**之内**的增量 ⇒ 大小变化。
    """
    out: list[Node] = []
    for nd in nodes:
        shift = sum(d for s, e, d in changes if e <= nd.offset)
        grow = sum(d for s, e, d in changes if s >= nd.offset and e <= nd.offset + nd.size)
        out.append(Node(nd.offset + shift, nd.size + grow, nd.flags, nd.path))
    return out


def _read_run(
    reader: UfsReader, fh, span0: int, span1: int, reps: list[Replacement]
) -> bytes:
    """拼出一个 dirty run 的**新内容**：原样段读源文件，替换段用新载荷。"""
    out = bytearray()
    splice = _SpliceReader(reader, fh, span0)
    cur = span0
    for rep in reps:
        if rep.start > cur:
            splice.seek_to(cur)
            out += splice.read(rep.start - cur)
        out += rep.payload
        cur = rep.end
    if cur < span1:
        splice.seek_to(cur)
        out += splice.read(span1 - cur)
    return bytes(out)


def block_start_of(reader: UfsReader, pos: int) -> int:
    """`pos` 落在哪一块的**头**（解压流坐标）—— 与内部那把 `_block_of` 同一把尺子的公开版。"""
    return reader.block_dstart[_block_of(reader, pos)]


def rewrite_run(reader: UfsReader, rep: Replacement, *, delta: int = 0) -> tuple[int, int]:
    """一段替换在写回时会被**重切**到的那一段（解压流坐标，**写完**之后的那一套）。

    算式与 `rewrite_container` 里算 dirty run 的那两行**逐字一致**：重切从「第一块的头」
    到「最后一块的尾」，尾巴再按这次替换带来的长度变化（delta）挪到新坐标。

    为什么要它：range 反向 Δ 把这对数记进补丁，还原时用 `extra_runs` 复现**同一条重切边界**。
    不复现的话，装后文件的**块边界**与原始文件不同（多一个尾巴块 / 少一个），
    整文件 sha256 永远回不去 —— 2026-10-11 实机 BUG-2（`data.unity3d` 撤不掉）根因就在这。
    """
    a = _block_of(reader, rep.start)
    b = _block_of(reader, rep.end - 1) if rep.end > rep.start else a
    return reader.block_dstart[a], reader.block_dstart[b] + reader.blocks[b].uncompressed + delta


def rewrite_container(
    reader: UfsReader,
    replacements: list[Replacement],
    target: str | Path,
    *,
    progress=None,
    token=None,
    extra_runs: Sequence[tuple[int, int]] = (),
) -> WriteReport:
    """把 replacements 应用到容器上，写出一个**新文件** target。

    **不动源文件** —— 覆盖是调用方的事（这样「写好再换」才成立）。
    因此本函数可以随便重跑，最坏结果是多一个临时文件。

    ## 这一版改了什么（工单 W1）

    | 坑 | 改前 | 改后 |
    |---|---|---|
    | 3 | 对象表只写 byte_start（+8），漏 byte_size（+16） | 两个都写 |
    | 4 | blocksInfo 原样传旧 reader.nodes | _recompute_nodes 平移偏移 / 扩缩大小 |
    | 5 | 元数据补丁加进来之后**不再查**重叠 | 加之前查一次，加完之后**再查一次** |
    | 6 | 从第一个受影响块**一路重切到 EOF** | **只重切被碰到的块**，中间的干净块原样搬 |

    元数据区也从「节点大小 - data_offset」（那其实是**对象数据区**）改成
    「48 + 元数据大小」—— 前者把整段对象数据卷进替换，白读几百 MB。

    参数
    ----
    reader
        已打开的容器读取器。
    replacements
        [(解压流区间, 新内容)]，区间互不相交、且**不许压到元数据区**上。
    target
        输出路径。**不能等于源文件**。
    progress
        core.runner.Progress -> None。会报「重切块 N/M」。
    token
        core.runner.CancellationToken。每个批次检查一次。

    返回
    ----
    WriteReport
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    target = Path(target)
    if target == reader.path:
        raise AssetError(
            "输出路径与源文件相同",
            hint="重写必须写到别的文件，再由调用方原子替换；否则失败会把游戏文件毁掉",
        )
    if not replacements:
        raise AssetError("没有任何替换内容", hint="至少要给一个 Replacement")

    t0 = time.time()
    ordered = sorted(replacements, key=lambda r: r.start)
    _assert_disjoint(ordered, "替换区间")

    node = reader.node("globalgamemanagers.assets")
    info = read_serialized_file(reader, node)
    # ⚠️ 元数据区**只有**「SerializedFile 头 + 元数据体」那么长。
    #    旧实现写的是 node.size - data_offset —— 那是**对象数据区**的长度
    #    （实测 530,000 vs 148），会把整段对象数据卷进替换，白读几百 MB。
    meta_span = SF_HEADER_SIZE + info.metadata_size
    meta_lo, meta_hi = node.offset, node.offset + meta_span

    for rep in ordered:
        if rep.start < meta_hi and rep.end > meta_lo:
            raise AssetError(
                f"替换区间压到元数据区上了：[{rep.start}, {rep.end}) 撞 [{meta_lo}, {meta_hi})",
                hint="元数据补丁要在同一长度内原地改 SerializedFile 头与对象表；重叠 = 静默坏包",
                replacement=(rep.start, rep.end),
                metadata=(meta_lo, meta_hi),
            )

    changes = [(r.start, r.end, r.delta) for r in ordered]
    meta_orig = reader.read_range(meta_lo, meta_span)
    patched = _patch_metadata(bytearray(meta_orig), node, info, changes)

    planned = list(ordered)
    if patched != meta_orig:
        planned.append(Replacement(meta_lo, meta_hi, patched))
        planned.sort(key=lambda r: r.start)
        # ⚠️ W1 §7 坑 5：补丁是**后加**的，加完必须重查一次。
        _assert_disjoint(planned, "替换区间与元数据补丁")

    changes = [(r.start, r.end, r.delta) for r in planned]
    total_delta = sum(d for _s, _e, d in changes)
    new_nodes = _recompute_nodes(reader.nodes, changes)

    # ---------- 1) 哪些块被碰到了 ----------
    dirty = [False] * len(reader.blocks)
    for rep in planned:
        a = _block_of(reader, rep.start)
        b = _block_of(reader, rep.end - 1) if rep.end > rep.start else a
        for k in range(a, b + 1):
            dirty[k] = True
    # ⚠ extra_runs：**额外**要按同一条边界重切的段（range 反向 Δ 还原专用）。
    #    段尾必须落在当前文件的块边界上（调用方用 rewrite_run 记下来的那一对）；
    #    这里按「覆盖到的块」把范围扩到块尾 —— 与不传时相比，只是多标了几块脏。
    for lo, hi in extra_runs:
        if hi <= lo:
            continue
        a = _block_of(reader, lo)
        b = _block_of(reader, hi - 1)
        for k in range(a, b + 1):
            dirty[k] = True

    runs: list[tuple[int, int]] = []
    k = 0
    while k < len(reader.blocks):
        if not dirty[k]:
            k += 1
            continue
        a = k
        while k < len(reader.blocks) and dirty[k]:
            k += 1
        runs.append((a, k - 1))

    # ---------- 2) 只重切 dirty run；中间的干净块按压缩字节原样搬 ----------
    new_blocks: list[Block] = []
    locate: list[tuple[int, int]] = []          # (0, 源块下标) / (1, 临时文件偏移)
    verbatim_blocks = 0
    recompressed_blocks = 0
    tmp_pos = 0

    src = reader.path.open("rb")
    tmp_blocks = target.with_suffix(target.suffix + ".blocks.tmp")
    try:
        with tmp_blocks.open("wb") as tw, ThreadPoolExecutor(8) as pool:
            done = 0
            emitted = 0
            for a, b in runs:
                for idx in range(emitted, a):
                    blk = reader.blocks[idx]
                    new_blocks.append(Block(blk.uncompressed, blk.compressed, blk.flags))
                    locate.append((0, idx))
                    verbatim_blocks += 1
                emitted = b + 1

                span0 = reader.block_dstart[a]
                span1 = reader.block_dstart[b] + reader.blocks[b].uncompressed
                inside = [r for r in planned if r.start >= span0 and r.end <= span1]
                raw = _read_run(reader, src, span0, span1, inside)
                expect = (span1 - span0) + sum(r.delta for r in inside)
                if len(raw) != expect:
                    raise AssetError(
                        f"重切段读出 {len(raw)} 字节，应当是 {expect}",
                        hint="原样段的读取漏了或多跳了块 —— 这会让产出的文件内容错位",
                    )
                n_chunks = (len(raw) + RECHUNK_SIZE - 1) // RECHUNK_SIZE
                step = _CHUNK_BATCH * RECHUNK_SIZE
                for off in range(0, len(raw), step):
                    if token is not None:
                        token.raise_if_cancelled()
                    window = raw[off : off + step]
                    batch = [
                        window[i : i + RECHUNK_SIZE] for i in range(0, len(window), RECHUNK_SIZE)
                    ]
                    for chunk, comp in zip(batch, pool.map(lz4hc, batch), strict=True):
                        if len(comp) >= len(chunk):
                            tw.write(chunk)
                            new_blocks.append(Block(len(chunk), len(chunk), 0))
                        else:
                            tw.write(comp)
                            new_blocks.append(Block(len(chunk), len(comp), 3))
                        locate.append((1, tmp_pos))
                        tmp_pos += new_blocks[-1].compressed
                        recompressed_blocks += 1
                    done += len(batch)
                    if progress is not None:
                        from ba_mod_maker.core.runner import Progress

                        progress(Progress(message="重切块", current=done, total=n_chunks))

            for idx in range(emitted, len(reader.blocks)):
                blk = reader.blocks[idx]
                new_blocks.append(Block(blk.uncompressed, blk.compressed, blk.flags))
                locate.append((0, idx))
                verbatim_blocks += 1

        # ---------- 3) 拼装 ----------
        data_hash = reader.header.data_hash
        new_bi = serialize_blocks_info(data_hash, new_blocks, new_nodes)
        new_comp_bi = lz4hc(new_bi)
        new_bi_offset = reader.header.blocks_info_offset
        new_data_start = new_bi_offset + len(new_comp_bi)
        if reader.header.blocks_info_pad_start:
            new_data_start = reader.align(new_data_start, 16)
        total = new_data_start + sum(b.compressed for b in new_blocks)
        header = serialize_header(
            reader.header.version,
            reader.header.version_player,
            reader.header.version_engine,
            total,
            len(new_comp_bi),
            len(new_bi),
            reader.header.flags,
        )
        with target.open("wb") as out, tmp_blocks.open("rb") as tw:
            out.write(header)
            out.write(new_comp_bi)
            gap = new_data_start - len(header) - len(new_comp_bi)
            if gap:
                out.write(bytes(gap))
            with reader.path.open("rb") as fin:
                for blk, (kind, where) in zip(new_blocks, locate, strict=True):
                    if kind == 0:
                        fin.seek(reader.block_foff[where])
                        piece = fin.read(blk.compressed)
                        if len(piece) != blk.compressed:
                            raise AssetError(
                                f"源文件第 {where} 个块只读到 {len(piece)} 字节，"
                                f"块表声明 {blk.compressed}",
                                hint="源文件可能被截断",
                            )
                    else:
                        tw.seek(where)
                        piece = tw.read(blk.compressed)
                    out.write(piece)
        actual = target.stat().st_size
        if actual != total:
            raise AssetError(
                f"写出的文件是 {actual:,} 字节，头部声明 {total:,}",
                hint="拼装长度算错了；这个文件不要用",
            )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        src.close()
        tmp_blocks.unlink(missing_ok=True)

    return WriteReport(
        target=target,
        old_size=reader.file_size,
        new_size=total,
        old_blocks=len(reader.blocks),
        new_blocks=len(new_blocks),
        verbatim_blocks=verbatim_blocks,
        recompressed_blocks=recompressed_blocks,
        seconds=time.time() - t0,
        replacements=len(planned),
        delta=total_delta,
    )
