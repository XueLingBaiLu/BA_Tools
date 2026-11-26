r"""Granite VT（.gts ＋ .gtp）读取器 —— GraniteTextureReader（C#）的纯 Python 移植。

为什么要有它：发布用的 Blender 插件不能依赖 .NET/exe，取 VT 贴图这一跳必须自己走完。
本模块**只依赖标准库**（`texture2ddecoder` 是可选的加速 / BC7 后备）。

## 容器

* `.gts`（页表）：magic `GRPG`（0x47505247），版本 5 / 6；头 192 B 里是层 / 级 / 瓦片尺寸 /
  flat 表 / rev 表 / 描述符表 / GDEX 元数据各自的位置。
* `.gtp`（页文件）：magic `GRAP`（0x50415247），版本 4。每页 `customPageSize` 字节：
  页首 4 B（**页 0 写死 0x18**）＋ `u32 × 每页瓦片数` 的索引表，索引是**页内相对偏移**。

## 一个 item 的一层 -> RGBA8（逐行对照 `GraniteProcessor.ExtractTexture`）

1. 瓦片内容边长 `content = tilePitch - 2 * border`（真件 128 = 144 - 2x8）；
2. item 声明的 `(x, y, w, h)` 落在 **level 0** 的页网格上：`tX = x // content`；
3. `level[0].tiles[layer + numLayers * (tY * nx + tX)]` 是 flat 下标，低 24 位查 flat 表
   -> `(pageFileIndex, pageIndex, tileIndex)`；**bit31 = 本格没有数据**，退到更低的级
   （C# 2026-10-09 那处补丁，见 `GraniteProcessor.cs`）；
4. `.gtp` 瓦片头 12 B = `(codec, parameterId, size)`：codec 0 = 常量色（size 2 / 4），
   4 = **裸 LZ4 块**，9 = **FastLZ 0.1.0 level 1**；1 = Granite 自有编码（C# 也直接抛）；
5. 解出来的（或直接读到的）是 `tilePitch x tilePitch` 的 BC 压缩数据，只取中间
   `content x content` 贴到输出图；BC 格式**逐层**从 `.gts` 的 `LINF` 元数据读（`TYPE`）；
6. 最后**上下翻转**（C# `image.Mutate(Flip(Vertical))`）—— 返回值与 C# 写出的 `.tga` 逐像素相同。

## 判据一行

    C:\Python314\python.exe tools\vt_read.py check
    -> 4 张 C# 基线逐像素差 = 0（正控：改 1 个像素 -> 必须报非 0）

## C# 侧写死 / 近似的地方（**照搬**，不是笔误；照抄是为了逐像素对齐）

* `MaxTileSize` **不可靠**（C# 注释点名 gbfr relink 1.1.1 的 5.gts），解压缓冲一律 `0x10000`；
* 退层放大里的 `<< 7` 是 C# 写死的（= log2(128)）；`content != 128` 时那个式子未必对；
* 页 0 的索引表起点写死 `0x18`，其余页写死 `页首 + 4`；
* 页文件的「字节数」字段（描述符 `[536]`）与盘上文件长度**对不上**（实测差 13 页），别拿它当尺子；
* `GetFlatTileData` 读了 `PackedTileInfos[tileListOffset]` 却**从没用过** —— 本模块根本不读 rev 表；
* C# 会拿 `.gts` 里那段 `PROJ` XML 覆盖 item 的宽高与瓦片步长；本项目实测无 `PROJ`，
  本模块实现它，但**找不到对应资产时退回 item 声明值**（C# 在这一步会空引用崩）。
"""

from __future__ import annotations

import os
import struct
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "GraniteVT",
    "GraniteVtError",
    "PageFile",
    "TextureEntry",
    "TileSetFile",
    "decode_bc",
    "extract_layer",
    "fastlz_decompress",
    "list_texture_entries",
    "list_textures",
    "lz4_block_decompress",
    "open_pool",
    "read_texture_layers",
    "read_tga_rgba",
]

#: `.gts` 魔数（ASCII `GRPG`）与可读版本。
TILESET_FILE_MAGIC = 0x47505247
TILESET_VERSIONS = (5, 6)
#: `.gtp` 魔数（ASCII `GRAP`）与版本。
PAGE_FILE_MAGIC = 0x50415247
PAGE_FILE_VERSION = 4
#: C# 写死的解压缓冲（`MaxTileSize` 不可靠，见模块头）。
MAX_TILE_BUFFER = 0x10000
#: 各表的步长（C# `GetSize()`）。
FLAT_TILE_BYTES = 0x0C
LEVEL_INFO_BYTES = 0x10
PAGE_FILE_INFO_BYTES = {5: 0x218, 6: 0x220}
PARAMETER_BLOCK_BYTES = 0x14
#: flat 下标的低 24 位有效；bit31 = 「本格没有数据」（C# `>> 31`）。
FLAT_INDEX_MASK = 0xFFFFFF
FLAT_EMPTY_BIT = 0x80000000

# GDEX 元数据节点类型（C# `GDEXItemType`）
GDEX_STRUCT = 1
GDEX_STRING = 2
GDEX_INT32 = 3
GDEX_INTARRAY = 8
GDEX_GUIDARRAY = 13
GDEX_FLAG_EXTENDED = 0x01


class GraniteVtError(RuntimeError):
    """读 .gts / .gtp 时的结构错误（**拒绝**继续，不静默降级）。"""


# ---------------------------------------------------------------- 标签 / 元数据
def tag_value(text: str) -> int:
    """4 字符标签 -> u32（C# `GDEXItem.StringTagToTag`：小端，短标签用空格补高位）。"""
    codes = [ord(ch) for ch in text]
    if not 1 <= len(codes) <= 4:
        raise GraniteVtError(f"标签必须 1..4 个字符：{text!r}")
    value = 0
    for index, code in enumerate(codes):
        value |= code << (8 * index)
    if len(codes) < 4:
        pad = 4 - len(codes)
        value |= int.from_bytes(b" " * pad, "little") << (8 * len(codes))
    return value


TAG_ATLAS = tag_value("ATLS")
TAG_TEXTURE_SET = tag_value("TXTS")
TAG_NAME = tag_value("NAME")
TAG_WIDTH = tag_value("WDTH")
TAG_HEIGHT = tag_value("HGHT")
TAG_X = tag_value("XXXX")
TAG_Y = tag_value("YYYY")
TAG_LAYER_INFO = tag_value("LINF")
TAG_FORMAT_TYPE = tag_value("TYPE")
TAG_PROJECT = tag_value("PROJ")


class GdexItem:
    """GDEX 元数据树上的一个节点（texture 列表 / 逐层 BC 格式都挂在它上面）。"""

    __slots__ = ("tag", "type", "flags", "value")

    def __init__(self, tag: int = 0, type_: int = 0, flags: int = 0, value: Any = None) -> None:
        self.tag = tag
        self.type = type_
        self.flags = flags
        self.value = value

    def find(self, tag: int):
        """按 u32 标签找**直接**子节点（找不到返回 None，与 C# 的索引器一致）。"""
        if not isinstance(self.value, list):
            return None
        for item in self.value:
            if item.tag == tag:
                return item
        return None

    def get(self, tag: str):
        return self.find(tag_value(tag))

    def as_list(self) -> list:
        if not isinstance(self.value, list):
            raise GraniteVtError("这个元数据节点不是结构体")
        return self.value

    def as_str(self) -> str:
        if not isinstance(self.value, str):
            raise GraniteVtError("这个元数据节点不是字符串")
        return self.value

    def as_int(self) -> int:
        if not isinstance(self.value, int):
            raise GraniteVtError("这个元数据节点不是整数")
        return int(self.value)


def parse_gdex(raw: bytes, pos: int, warnings: list | None = None):
    """解析一个 GDEX 节点，返回 `(节点, 新位置)`（C# `GDEXItem.Read` 逐行对照）。"""
    tag = struct.unpack_from("<I", raw, pos)[0]
    type_ = raw[pos + 4]
    flags = raw[pos + 5]
    pos += 6
    if flags & GDEX_FLAG_EXTENDED:
        low = struct.unpack_from("<H", raw, pos)[0]
        high = struct.unpack_from("<I", raw, pos + 2)[0]
        size = low | (high << 16)
        pos += 6
    else:
        size = struct.unpack_from("<H", raw, pos)[0]
        pos += 2
    base = pos
    end = base + size
    value: Any = None
    if type_ == GDEX_STRUCT:
        items = []
        while pos < end:
            item, pos = parse_gdex(raw, pos, warnings)
            items.append(item)
        value = items
    elif type_ == GDEX_STRING:
        stop = base
        while stop + 1 < len(raw) and (raw[stop] | (raw[stop + 1] << 8)) != 0:
            stop += 2
        value = raw[base:stop].decode("utf-16-le", "replace")
        pos = stop + 2
    elif type_ == GDEX_INT32:
        value = struct.unpack_from("<i", raw, base)[0]
    elif type_ == GDEX_INTARRAY:
        count = size // 4
        value = list(struct.unpack_from(f"<{count}i", raw, base)) if count else []
    elif type_ == GDEX_GUIDARRAY:
        value = [raw[base + 16 * i : base + 16 * (i + 1)] for i in range(size // 16)]
    else:
        if warnings is not None:
            warnings.append(f"未支持的 GDEX 类型 {type_}（tag=0x{tag:08X}，{size} B）—— 载荷跳过")
    pos = base + size
    pos = (pos + 3) & ~3
    return GdexItem(tag, type_, flags, value), pos


# ---------------------------------------------------------------- 无损解压
def lz4_block_decompress(src: bytes, max_out: int | None = None) -> bytes:
    """裸 LZ4 块（无长度前缀）—— 对齐 C# 的 `K4os...LZ4Codec.Decode`。"""
    out = bytearray()
    total = len(src)
    pos = 0
    while pos < total:
        token = src[pos]
        pos += 1
        literals = token >> 4
        if literals == 15:
            while True:
                extra = src[pos]
                pos += 1
                literals += extra
                if extra != 255:
                    break
        if literals:
            if pos + literals > total:
                raise GraniteVtError(f"LZ4 字面量越界：{pos} + {literals} > {total}")
            out += src[pos : pos + literals]
            pos += literals
        if pos >= total:
            break
        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        if offset == 0:
            raise GraniteVtError("LZ4 的匹配距离是 0")
        length = token & 15
        if length == 15:
            while True:
                extra = src[pos]
                pos += 1
                length += extra
                if extra != 255:
                    break
        length += 4
        start = len(out) - offset
        if start < 0:
            raise GraniteVtError(f"LZ4 回指越界：输出只剩 {len(out)} B，距离 {offset}")
        if offset >= length:
            out += out[start : start + length]
        else:
            for _ in range(length):
                out.append(out[start])
                start += 1
        if max_out is not None and len(out) > max_out:
            raise GraniteVtError(f"LZ4 解压超过 {max_out} B")
    return bytes(out)


def fastlz_decompress(src: bytes, max_out: int = MAX_TILE_BUFFER) -> bytes:
    """FastLZ 0.1.0 **level 1** 解压（对齐 C# 的 `DotFastLZ.FastLZ.Decompress`）。

    * 控制字节 `ctrl < 32`：字面量段，长度 = `ctrl + 1`；
    * `ctrl >= 32`：匹配段。长度 = `(ctrl >> 5) - 1`，`ctrl >> 5 == 7` 时再吃一个
      扩展字节；**拷贝长度 = 长度 + 3**（至少 3 B）。
      距离 = `((ctrl & 31) << 8) + 下一个字节 + 1`  ← ★ **存的是「距离 - 1」**；
    * 距离可以小于长度（重叠拷贝，必须逐字节拷）。

    ★「距离 = 存值 + 1」这条**不是从文档抄的，是实测出来的**：初版按「距离 = 存值」写，
    在 DotFastLZ 2024.4.1 `CompressLevel1` 造的 4 条流上只对 1 条（全零那种距离无所谓的）；
    `in_text.bin` 第一个匹配就错（存 0x1e 而正确距离是 31）。
    独立佐证：`FastLZ.MatchLevel1(len, 8192, ...)` 编出 `7f ff` / `ff 00 ff` ——
    13 位存值最大 8191，+1 之后正好是 level 1 的 `MAX_DISTANCE = 8192`。
    """
    out = bytearray()
    total = len(src)
    if total == 0:
        return b""
    pos = 0
    level = (src[0] >> 5) + 1
    if level != 1:
        raise GraniteVtError(f"FastLZ level {level} 没实现（只认 level 1，即首字节高 3 位 = 0）")
    ctrl = src[pos] & 31
    pos += 1
    looping = True
    while looping:
        if ctrl >= 32:
            length = (ctrl >> 5) - 1
            offset = (ctrl & 31) << 8
            if length == 6:
                if pos >= total:
                    raise GraniteVtError("FastLZ 长度扩展越界")
                length += src[pos]
                pos += 1
            if pos >= total:
                raise GraniteVtError("FastLZ 距离字节越界")
            offset += src[pos] + 1
            pos += 1
            start = len(out) - offset
            if start < 0:
                raise GraniteVtError(f"FastLZ 回指越界：输出只剩 {len(out)} B，距离 {offset}")
            count = length + 3
            if offset >= count:
                out += out[start : start + count]
            else:
                for _ in range(count):
                    out.append(out[start])
                    start += 1
            if len(out) > max_out:
                raise GraniteVtError(f"FastLZ 解压超过 {max_out} B")
            if pos >= total:
                break
            ctrl = src[pos]
            pos += 1
        else:
            count = ctrl + 1
            if pos + count > total:
                raise GraniteVtError(f"FastLZ 字面量越界：{pos} + {count} > {total}")
            out += src[pos : pos + count]
            pos += count
            looping = pos < total
            if looping:
                ctrl = src[pos]
                pos += 1
    return bytes(out)


# ---------------------------------------------------------------- BC 解码
_T2D: Any = None
_T2D_TRIED = False

#: BC 格式 -> `texture2ddecoder` 的函数名（**独立实现**，返回 BGRA）。
_T2D_FUNCS = {
    "BC1": "decode_bc1",
    "BC3": "decode_bc3",
    "BC4": "decode_bc4",
    "BC5": "decode_bc5",
    "BC6": "decode_bc6",
    "BC7": "decode_bc7",
}


def _texture2ddecoder():
    """按需拉起 `texture2ddecoder`（没有就返回 None，走纯 Python 后备）。"""
    global _T2D, _T2D_TRIED
    if not _T2D_TRIED:
        _T2D_TRIED = True
        try:
            import texture2ddecoder  # noqa: PLC0415 -- 只在真要解 BC 时拉起来
        except ImportError:  # pragma: no cover - 取决于环境
            _T2D = None
        else:
            _T2D = texture2ddecoder
    return _T2D


def _bgra_to_rgba(data: bytes) -> bytes:
    """`texture2ddecoder` 的输出是 **BGRA** 序（实测锚点：BC1 红色块 -> 00 00 FF FF）。"""
    buffer = bytearray(data)
    buffer[0::4], buffer[2::4] = buffer[2::4], buffer[0::4]
    return bytes(buffer)


def _expand565(value: int) -> tuple[int, int, int]:
    red = (value >> 11) & 0x1F
    green = (value >> 5) & 0x3F
    blue = value & 0x1F
    return (red << 3 | red >> 2, green << 2 | green >> 4, blue << 3 | blue >> 2)


def _color_palette(c0: int, c1: int) -> tuple:
    """BC1/BC3 的颜色调色板（`c0 > c1` 四色，否则三色 ＋ 不透明黑，实测对齐）。"""
    r0, g0, b0 = _expand565(c0)
    r1, g1, b1 = _expand565(c1)
    if c0 > c1:
        r2, g2, b2 = (2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3
        r3, g3, b3 = (r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3
    else:
        r2, g2, b2 = (r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2
        r3 = g3 = b3 = 0
    return ((r0, g0, b0, 255), (r1, g1, b1, 255), (r2, g2, b2, 255), (r3, g3, b3, 255))


def _alpha_palette(a0: int, a1: int) -> list:
    """BC3/BC4 的 alpha 调色板（`a0 > a1` 八级，否则六级 ＋ 0/255）。

    ★ 这里是**唯一**与 `texture2ddecoder` 分歧的地方：BCnEncoder（C# 用的库）对这两个
    内插值是**四舍五入**，不是截断。实测（基线 aa1f1952... 层 0，2048x2048 = 4,194,304 px）：
    截断版与 C# 差 843,668 个像素（全部只在 A 通道、`max_delta = 1`），四舍五入版差 0。
    颜色通道相反 —— 那一侧 C# 用**截断**（同一次实测里 RGB 逐位相同）。"""
    if a0 > a1:
        return [a0, a1] + [((8 - i) * a0 + (i - 1) * a1 + 3) // 7 for i in range(2, 8)]
    return [a0, a1] + [((6 - i) * a0 + (i - 1) * a1 + 2) // 5 for i in range(2, 6)] + [0, 255]


def _block_grid(width: int, height: int) -> tuple[int, int]:
    return (width + 3) // 4, (height + 3) // 4


def _decode_bc1(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    blocks_x, blocks_y = _block_grid(width, height)
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            c0 = data[pos] | (data[pos + 1] << 8)
            c1 = data[pos + 2] | (data[pos + 3] << 8)
            cbits = int.from_bytes(data[pos + 4 : pos + 8], "little")
            pos += 8
            _blit(out, width, height, bx, by, _color_palette(c0, c1), cbits, 2)
    return bytes(out)


def _decode_bc3(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    blocks_x, blocks_y = _block_grid(width, height)
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            a0 = data[pos]
            a1 = data[pos + 1]
            abits = int.from_bytes(data[pos + 2 : pos + 8], "little")
            c0 = data[pos + 8] | (data[pos + 9] << 8)
            c1 = data[pos + 10] | (data[pos + 11] << 8)
            cbits = int.from_bytes(data[pos + 12 : pos + 16], "little")
            pos += 16
            _blit_alpha(
                out,
                width,
                height,
                bx,
                by,
                _alpha_palette(a0, a1),
                abits,
                _color_palette(c0, c1),
                cbits,
            )
    return bytes(out)


def _decode_bc4(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    blocks_x, blocks_y = _block_grid(width, height)
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            palette = _alpha_palette(data[pos], data[pos + 1])
            bits = int.from_bytes(data[pos + 2 : pos + 8], "little")
            pos += 8
            _blit_channel(out, width, height, bx, by, palette, bits, 0)
    return bytes(out)


#: 本模块自带的解码器（**默认路径**；BC1/BC3/BC4/BC5 与 C# 逐像素对齐）。
_PURE_DECODERS: dict = {}


def _decode_bc5(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    blocks_x, blocks_y = _block_grid(width, height)
    span = blocks_x * blocks_y * 8
    for by in range(blocks_y):
        for bx in range(blocks_x):
            index = by * blocks_x + bx
            for channel in (0, 1):
                pos = channel * span + index * 8
                palette = _alpha_palette(data[pos], data[pos + 1])
                bits = int.from_bytes(data[pos + 2 : pos + 8], "little")
                _blit_channel(out, width, height, bx, by, palette, bits, channel)
    return bytes(out)


def _block_slice(out: bytearray, width: int, height: int, bx: int, by: int, blk: bytearray) -> None:
    """把一个 4x4 的 64 B 色块写进大图（越界像素丢掉）。"""
    x0 = bx * 4
    y0 = by * 4
    for row in range(4):
        y = y0 + row
        if y >= height:
            break
        usable = min(4, width - x0)
        start = (y * width + x0) * 4
        out[start : start + usable * 4] = blk[row * 16 : row * 16 + usable * 4]


def _blit(out, width, height, bx, by, palette, bits, bit_width) -> None:
    blk = bytearray(64)
    for i in range(16):
        r, g, b, a = palette[(bits >> (bit_width * i)) & 3]
        blk[i * 4 : i * 4 + 4] = bytes((r, g, b, a))
    _block_slice(out, width, height, bx, by, blk)


def _blit_alpha(out, width, height, bx, by, alpha_palette, abits, color_palette, cbits) -> None:
    blk = bytearray(64)
    for i in range(16):
        r, g, b, _a = color_palette[(cbits >> (2 * i)) & 3]
        a = alpha_palette[(abits >> (3 * i)) & 7]
        blk[i * 4 : i * 4 + 4] = bytes((r, g, b, a))
    _block_slice(out, width, height, bx, by, blk)


def _blit_channel(out, width, height, bx, by, palette, bits, channel) -> None:
    blk = bytearray(64)
    for i in range(16):
        value = palette[(bits >> (3 * i)) & 7]
        blk[i * 4 : i * 4 + 4] = (
            bytes((value, 0, 0, 255)) if channel == 0 else bytes((0, value, 0, 255))
        )
    _block_slice(out, width, height, bx, by, blk)


def decode_bc(data: bytes, width: int, height: int, fmt: str) -> bytes:
    """把一段 BC 数据解成 RGBA8。

    ★ BC1/BC3/BC4/BC5 一律走**本模块的实现**，⛔ 不走 `texture2ddecoder`：
    后者的 BC3 alpha 是截断，而 C# 那条链（BCnEncoder）是四舍五入 ⇒ 4,194,304 个像素里
    差 843,668 个（见 `_alpha_palette`）。只有本模块没有实现的 BC6/BC7 才借它。
    """
    name = (fmt or "").strip().upper()
    pure = _PURE_DECODERS.get(name)
    if pure is not None:
        return pure(bytes(data), int(width), int(height))
    engine = _texture2ddecoder()
    function_name = _T2D_FUNCS.get(name)
    if engine is not None and function_name is not None:
        func = getattr(engine, function_name, None)
        if func is not None:
            return _bgra_to_rgba(bytes(func(bytes(data), int(width), int(height))))
    raise GraniteVtError(
        f"BC 格式 {fmt!r} 解不了：本模块只内置 BC1/BC3/BC4/BC5，这一档要 texture2ddecoder",
    )


_PURE_DECODERS.update(
    {
        "BC1": _decode_bc1,
        "BC3": _decode_bc3,
        "BC4": _decode_bc4,
        "BC5": _decode_bc5,
    }
)


@dataclass(frozen=True, slots=True)
class TextureEntry:
    """`.gts` 里一个 texture 条目（同名会有多条，坐标可能不同）。"""

    index: int
    name: str
    width: int
    height: int
    x: int
    y: int

    @property
    def line(self) -> str:
        return f"[{self.index}] {self.name} {self.width}x{self.height} @({self.x},{self.y})"


def _int_of(item, tag: int, default: int = 0) -> int:
    child = item.find(tag) if item is not None else None
    if child is None or not isinstance(child.value, int):
        return default
    return int(child.value)


class TileSetFile:
    """一份 `.gts` 的只读视图（头 ＋ 各段原始字节 ＋ GDEX 元数据树）。

    偏移全部来自 C# `TileSetFile.InitializeV5orV6` —— v5/v6 头布局相同，只有页描述符步长不同
    （0x218 vs 0x220）。
    """

    def __init__(self, path: Path, raw: bytes) -> None:
        self.path = Path(path)
        self.raw = raw
        self.warnings: list = []
        if len(raw) < 0xC0:
            raise GraniteVtError(f"{self.path.name} 只有 {len(raw)} B，比一个 GRPG 头还短")
        magic = struct.unpack_from("<I", raw, 0)[0]
        if magic != TILESET_FILE_MAGIC:
            raise GraniteVtError(f"{self.path.name} 的魔数是 0x{magic:08X}，不是 GRPG")
        self.version = struct.unpack_from("<I", raw, 4)[0]
        if self.version not in TILESET_VERSIONS:
            raise GraniteVtError(
                f"{self.path.name} 的版本是 {self.version}，只认 {TILESET_VERSIONS}"
            )
        self.guid = raw[0x0C:0x1C]
        self.num_layers = struct.unpack_from("<I", raw, 0x1C)[0]
        self.layers_offset = struct.unpack_from("<Q", raw, 0x20)[0]
        self.num_levels = struct.unpack_from("<I", raw, 0x28)[0]
        self.levels_offset = struct.unpack_from("<Q", raw, 0x2C)[0]
        self.tile_width = struct.unpack_from("<I", raw, 0x34)[0]
        self.tile_height = struct.unpack_from("<I", raw, 0x38)[0]
        self.tile_border = struct.unpack_from("<I", raw, 0x3C)[0]
        self.max_tile_size = struct.unpack_from("<I", raw, 0x40)[0]
        self.num_flat_tiles = struct.unpack_from("<I", raw, 0x44)[0]
        self.flat_tiles_offset = struct.unpack_from("<Q", raw, 0x48)[0]
        self.num_reverse_tiles = struct.unpack_from("<I", raw, 0x58)[0]
        self.reverse_tiles_offset = struct.unpack_from("<Q", raw, 0x5C)[0]
        self.custom_page_size = struct.unpack_from("<I", raw, 0x80)[0]
        self.num_page_files = struct.unpack_from("<I", raw, 0x84)[0]
        self.page_files_offset = struct.unpack_from("<Q", raw, 0x88)[0]
        self.meta_size = struct.unpack_from("<I", raw, 0x90)[0]
        self.meta_offset = struct.unpack_from("<Q", raw, 0x94)[0]
        self.num_parameter_blocks = struct.unpack_from("<I", raw, 0x9C)[0]
        self.parameter_blocks_offset = struct.unpack_from("<Q", raw, 0xA0)[0]
        if self.tile_width < 2 * self.tile_border or self.content <= 0:
            raise GraniteVtError(f"瓦片尺寸不对：{self.tile_width} - 2x{self.tile_border}")
        self._metadata: GdexItem | None = None
        self._textures: list | None = None
        self._formats: list | None = None
        self._parameters: dict | None = None
        self._project_text: str | None = None
        self._project_parsed = False

    @classmethod
    def from_path(cls, path) -> TileSetFile:
        target = Path(path)
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise GraniteVtError(f"读不到页表 {target}：{exc}") from exc
        return cls(target, raw)

    @property
    def content(self) -> int:
        """瓦片**内容**边长（去掉两侧 border）。"""
        return self.tile_width - 2 * self.tile_border

    # ------------------------------------------------------------ 元数据
    def metadata(self) -> GdexItem:
        if self._metadata is None:
            item, _pos = parse_gdex(self.raw, self.meta_offset, self.warnings)
            self._metadata = item
        return self._metadata

    def textures(self) -> list:
        """`.gts` 里全部 texture 条目（含同名多条，顺序 = 文件里的顺序）。"""
        if self._textures is None:
            entries: list = []
            atlas = self.metadata().get("ATLS")
            texture_set = atlas.get("TXTS") if atlas is not None else None
            if texture_set is not None:
                for index, item in enumerate(texture_set.as_list()):
                    name = item.find(TAG_NAME)
                    if name is None or not isinstance(name.value, str):
                        continue
                    entries.append(
                        TextureEntry(
                            index=index,
                            name=name.value,
                            width=_int_of(item, TAG_WIDTH),
                            height=_int_of(item, TAG_HEIGHT),
                            x=_int_of(item, TAG_X),
                            y=_int_of(item, TAG_Y),
                        )
                    )
            self._textures = entries
        return self._textures

    def layer_infos(self) -> list:
        """C# `LayerInfo` 逐条：`(DataType, DefaultColor)`。"""
        out = []
        for index in range(self.num_layers):
            offset = self.layers_offset + index * 0x08
            out.append(struct.unpack_from("<2I", self.raw, offset))
        return out

    def layer_default_color(self, layer: int) -> int:
        return self.layer_infos()[layer][1]

    def layer_formats(self) -> list:
        """逐层的 BC 格式名（C# `Metadata[LINF][layer][TYPE]`）。"""
        if self._formats is None:
            formats: list = []
            linfo = self.metadata().get("LINF")
            if linfo is not None:
                for item in linfo.as_list():
                    child = item.find(TAG_FORMAT_TYPE)
                    formats.append(child.value if child is not None else "")
            self._formats = formats
        return self._formats

    def layer_format(self, layer: int) -> str:
        formats = self.layer_formats()
        if not 0 <= layer < len(formats):
            raise GraniteVtError(f"层 {layer} 超出 LINF（共 {len(formats)} 条）")
        return formats[layer]

    def parameter_blocks(self) -> dict:
        """`id -> 参数块字节`（C# `ParameterBlockInfo`）。"""
        if self._parameters is None:
            blocks: dict = {}
            for index in range(self.num_parameter_blocks):
                offset = self.parameter_blocks_offset + index * PARAMETER_BLOCK_BYTES
                block_id, _codec, size, payload = struct.unpack_from("<4I", self.raw, offset)
                blocks[block_id] = self.raw[payload : payload + size]
            self._parameters = blocks
        return self._parameters

    def project_file(self) -> str:
        if self._project_text is None:
            node = self.metadata().get("PROJ")
            self._project_text = (
                node.value if node is not None and isinstance(node.value, str) else ""
            )
        return self._project_text

    def project_override(self, name: str, layer: int):
        """`.gts` 里那段 `PROJ` XML 会覆盖 item 的宽高（C# `ExtractSingle`）。

        找不到对应资产时**退回 None**（C# 在这一步会空引用崩，本模块不照搬那次崩溃）。
        """
        text = self.project_file()
        if not text:
            return None
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            self.warnings.append(f"PROJ 不是合法 XML：{exc}")
            return None
        for asset in root.iter("Asset"):
            if asset.get("Name") != name:
                continue
            layers = list(asset.iter("Layer"))
            if layer >= len(layers):
                return None
            texture = layers[layer].find("Textures/Texture")
            if texture is None:
                return None
            try:
                width = int(texture.get("Width") or 0)
                height = int(texture.get("Height") or 0)
            except ValueError:
                return None
            if width > 0 and height > 0:
                return (width, height)
            return None
        return None

    # ------------------------------------------------------------ 各段随机访问
    def flat_tile(self, index: int) -> tuple:
        """flat 表一条：`(pageFileIndex, pageIndex, tileIndex, numTiles, tileListOffset)`。"""
        if not 0 <= index < self.num_flat_tiles:
            raise GraniteVtError(f"flat 下标 {index} 越界（共 {self.num_flat_tiles}）")
        offset = self.flat_tiles_offset + index * FLAT_TILE_BYTES
        return struct.unpack_from("<4HI", self.raw, offset)

    def level(self, index: int) -> tuple:
        """某一级：`(numTilesX, numTilesY, tilesOffset, pad)`（C# `LevelInfo`）。"""
        if not 0 <= index < self.num_levels:
            raise GraniteVtError(f"级 {index} 越界（共 {self.num_levels}）")
        offset = self.levels_offset + index * LEVEL_INFO_BYTES
        return struct.unpack_from("<4I", self.raw, offset)

    def tile_info(self, level: int, layer: int, tile_x: int, tile_y: int) -> int:
        """C# `levelInfo.TileInfos[layer + numLayers * (tY * nx + tX)]`（**带符号**读）。"""
        num_x, num_y, tiles_offset, _pad = self.level(level)
        if not (0 <= tile_x < num_x and 0 <= tile_y < num_y):
            raise GraniteVtError(f"级 {level} 的 ({tile_x},{tile_y}) 不在 {num_x}x{num_y} 网格里")
        index = layer + self.num_layers * (tile_y * num_x + tile_x)
        return struct.unpack_from("<i", self.raw, tiles_offset + 4 * index)[0]

    def page_file_descriptor(self, index: int) -> tuple:
        """描述符表一条：`(fileName, numPages, sizeInBytes)`。"""
        stride = PAGE_FILE_INFO_BYTES[self.version]
        offset = self.page_files_offset + index * stride
        blob = self.raw[offset : offset + stride]
        name = blob[:0x200].decode("utf-16-le", "replace").split(chr(0))[0]
        num_pages = struct.unpack_from("<I", blob, 0x200)[0]
        size_in_bytes = struct.unpack_from("<I", blob, 0x218)[0] if self.version >= 6 else 0
        return (name, num_pages, size_in_bytes)


class PageFile:
    """一个 `.gtp` 页文件（**只读**，按页随机访问）。"""

    def __init__(self, path, tile_set: TileSetFile) -> None:
        self.path = Path(path)
        self.tile_set = tile_set
        try:
            self._handle = open(self.path, "rb")  # noqa: SIM115 -- 生命周期由本对象持有
        except OSError as exc:
            raise GraniteVtError(f"读不到页文件 {self.path}：{exc}") from exc
        head = self._handle.read(8)
        if len(head) < 8:
            raise GraniteVtError(f"{self.path.name} 比一个 GRAP 头还短")
        magic, version = struct.unpack("<2I", head)
        if magic != PAGE_FILE_MAGIC:
            raise GraniteVtError(f"{self.path.name} 的魔数是 0x{magic:08X}，不是 GRAP")
        if version != PAGE_FILE_VERSION:
            raise GraniteVtError(f"{self.path.name} 的版本是 {version}，只认 {PAGE_FILE_VERSION}")

    def close(self) -> None:
        self._handle.close()

    def transcode_tile(self, page_index: int, tile_index: int, fmt: str) -> bytes:
        """C# `PageFile.TranscodeTile` 逐行对照，返回**整块** `tileWidth x tileHeight` 的 RGBA8。"""
        tile_set = self.tile_set
        page_size = tile_set.custom_page_size
        start = page_index * page_size
        page_offset = 0x18 if page_index == 0 else start
        self._handle.seek(page_offset + tile_index * 4 + 4)
        relative = struct.unpack("<I", self._handle.read(4))[0]
        self._handle.seek(start + relative)
        header = self._handle.read(12)
        if len(header) < 12:
            raise GraniteVtError(f"{self.path.name} 页 {page_index} 瓦片 {tile_index} 的头被截断")
        codec, parameter_id, size = struct.unpack("<3I", header)
        if parameter_id not in tile_set.parameter_blocks():
            raise GraniteVtError(
                f"参数块 {parameter_id} 不在 .gts 里（页 {page_index} 瓦片 {tile_index}）"
            )
        payload = self._handle.read(size)
        width, height = tile_set.tile_width, tile_set.tile_height
        if codec == 0:
            if size == 0x02:
                colour = bytes((payload[0], payload[1], 0, 0xFF))
            elif size == 0x04:
                colour = bytes(payload[:4])
            else:
                raise GraniteVtError(
                    f"常量瓦片长度 {size} 没定义（页 {page_index} 瓦片 {tile_index}）"
                )
            return colour * (width * height)
        if codec == 1:
            raise GraniteVtError("Granite 自有编码（codec 1）C# 也没实现")
        if codec == 4:
            data = lz4_block_decompress(payload, MAX_TILE_BUFFER)
        elif codec == 9:
            data = fastlz_decompress(payload, MAX_TILE_BUFFER)
        else:
            raise GraniteVtError(f"codec {codec} 不支持（页 {page_index} 瓦片 {tile_index}）")
        return decode_bc(data, width, height, fmt)


class GraniteVT:
    """一个 VT 池：`.gts` 页表 ＋ 按需打开的 `.gtp` 页文件。"""

    def __init__(self, gts_path) -> None:
        self.path = Path(gts_path)
        self.tile_set = TileSetFile.from_path(self.path)
        self.warnings = self.tile_set.warnings
        self._pages: dict = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 目录
    def texture_entries(self) -> list:
        return self.tile_set.textures()

    def list_textures(self) -> list:
        """item 名（**去重**，保持文件里的顺序）—— 同名多条只出一个名字。"""
        seen: dict = {}
        for entry in self.texture_entries():
            seen.setdefault(entry.name, None)
        return list(seen)

    @property
    def num_layers(self) -> int:
        return self.tile_set.num_layers

    def describe(self) -> list:
        """给人看的几行（CLI 与用例共用同一份措辞）。"""
        tile_set = self.tile_set
        nx, ny, _off, _pad = tile_set.level(0)
        lines = [
            f"{self.path.name}  {len(tile_set.raw):,} B  GRPG v{tile_set.version}"
            f"  GUID {tile_set.guid.hex()}",
            f"  瓦片 {tile_set.content}px（间距 {tile_set.tile_width}，边 {tile_set.tile_border}）"
            f"  层 {tile_set.num_layers} · 级 {tile_set.num_levels}"
            f"  页 {tile_set.custom_page_size:,} B · 分片 {tile_set.num_page_files}",
            f"  level0 网格 {nx}x{ny} 页 = {nx * tile_set.content}x{ny * tile_set.content} px",
            f"  flat 表 {tile_set.num_flat_tiles:,} 条",
            f"  逐层 BC 格式 {tile_set.layer_formats()}",
            f"  逐层默认色 {[hex(row[1]) for row in tile_set.layer_infos()]}",
            f"  条目 {len(self.texture_entries())} 个 · 唯一名 {len(self.list_textures())} 个",
        ]
        return lines

    # ------------------------------------------------------------ 页文件
    def page(self, index: int) -> PageFile:
        with self._lock:
            page = self._pages.get(index)
            if page is None:
                name, _num_pages, _size = self.tile_set.page_file_descriptor(index)
                page = PageFile(self.path.parent / name, self.tile_set)
                self._pages[index] = page
            return page

    def close(self) -> None:
        with self._lock:
            for page in self._pages.values():
                page.close()
            self._pages.clear()

    # ------------------------------------------------------------ 取一层
    def find_entry(self, name: str, index: int = -1) -> TextureEntry:
        """按名字找条目；`index < 0` = 取**最后一条**（C# `-i -1` 的「后者覆盖前者」）。"""
        matches = [entry for entry in self.texture_entries() if entry.name == name]
        if not matches:
            raise GraniteVtError(f"{self.path.name} 里没有叫 {name!r} 的 item")
        if index < 0:
            return matches[-1]
        if index >= len(matches):
            raise GraniteVtError(f"{name} 只有 {len(matches)} 条同名条目，取不到第 {index} 条")
        return matches[index]

    def extract_layer(self, item_name: str, layer: int, index: int = -1) -> tuple:
        """取 item 的某一层 -> `(width, height, RGBA8 bytes)`（与 C# 写出的 .tga 逐像素相同）。"""
        return self._assemble(self.find_entry(item_name, index), layer)

    def read_texture_layers(self, item_name: str, index: int = -1) -> list:
        """逐层取（`0..numLayers-1`），返回 `[(w, h, bytes), ...]`。"""
        entry = self.find_entry(item_name, index)
        return [self._assemble(entry, layer) for layer in range(self.tile_set.num_layers)]

    def _flat_tile_rgba(self, flat_index: int, fmt: str) -> bytes:
        page_file_index, page_index, tile_index, _num, _list = self.tile_set.flat_tile(
            flat_index & FLAT_INDEX_MASK
        )
        return self.page(page_file_index).transcode_tile(page_index, tile_index, fmt)

    def _assemble(self, entry: TextureEntry, layer: int) -> tuple:
        """C# `GraniteProcessor.ExtractTexture` 逐行对照（含 2026-10-09 的退层补丁）。"""
        tile_set = self.tile_set
        if not 0 <= layer < tile_set.num_layers:
            raise GraniteVtError(f"层 {layer} 越界（共 {tile_set.num_layers} 层）")
        fmt = tile_set.layer_format(layer)
        width, height = entry.width, entry.height
        step_x = step_y = 1
        override = tile_set.project_override(entry.name, layer)
        if override is not None:
            width, height = override
            step_x = entry.width // width if width else 1
            step_y = entry.height // height if height else 1
        content = tile_set.content
        num_x = width // content
        num_y = height // content
        ofs_x = entry.x // content
        ofs_y = entry.y // content
        if num_x <= 0 or num_y <= 0:
            raise GraniteVtError(f"{entry.name} 的 {width}x{height} 装不下一个 {content}px 瓦片")
        stride = width * 4
        default = tile_set.layer_default_color(layer)
        default_rgba = bytes(
            (default & 0xFF, (default >> 8) & 0xFF, (default >> 16) & 0xFF, (default >> 24) & 0xFF)
        )
        pixels = bytearray(default_rgba * (width * height))
        border = tile_set.tile_border
        tile_width = tile_set.tile_width
        shift = 7  # C# 写死的 << 7 = log2(128)，见模块头
        for tile_y in range(num_y):
            out_y = tile_y * content
            ty = tile_y * step_y + ofs_y
            for tile_x in range(num_x):
                out_x = tile_x * content
                tx = tile_x * step_x + ofs_x
                flat = tile_set.tile_info(0, layer, tx, ty)
                sub_level = 0
                sub_x = sub_y = 0
                if flat & FLAT_EMPTY_BIT:
                    for level in range(1, tile_set.num_levels):
                        num_x_l, num_y_l, _off, _pad = tile_set.level(level)
                        cx = tx >> level
                        cy = ty >> level
                        if cx >= num_x_l or cy >= num_y_l:
                            break
                        candidate = tile_set.tile_info(level, layer, cx, cy)
                        if not (candidate & FLAT_EMPTY_BIT):
                            flat = candidate
                            sub_level = level
                            break
                if sub_level:
                    sub_x = tx & ((1 << sub_level) - 1)
                    sub_y = ty & ((1 << sub_level) - 1)
                tile = self._flat_tile_rgba(flat, fmt)
                if sub_level == 0:
                    for row in range(content):
                        src = ((row + border) * tile_width + border) * 4
                        dst = (out_y + row) * stride + out_x * 4
                        pixels[dst : dst + content * 4] = tile[src : src + content * 4]
                else:
                    for row in range(content):
                        src_y = ((sub_y << shift) + row) >> sub_level
                        src_row = ((src_y + border) * tile_width + border) * 4
                        dst = (out_y + row) * stride + out_x * 4
                        for col in range(content):
                            src_x = ((sub_x << shift) + col) >> sub_level
                            if src_x >= content:
                                src_x = content - 1
                            offset = src_row + src_x * 4
                            pixels[dst + col * 4 : dst + col * 4 + 4] = tile[offset : offset + 4]
        flipped = bytearray(len(pixels))
        for row in range(height):
            src = (height - 1 - row) * stride
            flipped[row * stride : (row + 1) * stride] = pixels[src : src + stride]
        return width, height, bytes(flipped)


# ---------------------------------------------------------------- 模块级接口（给插件接线用）
_POOLS: dict = {}
_POOLS_LOCK = threading.Lock()


def open_pool(gts_path) -> GraniteVT:
    """按路径拿一个池（进程内缓存：同名只解析一次）。"""
    key = os.path.abspath(os.fspath(gts_path))
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = GraniteVT(key)
            _POOLS[key] = pool
        return pool


def clear_pool_cache() -> None:
    """关掉并清空进程内缓存（用例与重装池子之后调）。"""
    with _POOLS_LOCK:
        for pool in _POOLS.values():
            pool.close()
        _POOLS.clear()


def list_texture_entries(gts_path) -> list:
    """`.gts` 里**全部**条目（含同名多条）。"""
    return list(open_pool(gts_path).texture_entries())


def list_textures(gts_path) -> list:
    """item 名（**去重**，保持顺序）。"""
    return open_pool(gts_path).list_textures()


def read_texture_layers(gts_path, item_name: str, index: int = -1) -> list:
    """逐层取：`[(width, height, RGBA8 bytes), ...]`（层数由 `.gts` 决定）。"""
    return open_pool(gts_path).read_texture_layers(item_name, index)


def extract_layer(gts_path, item_name: str, layer: int, index: int = -1) -> tuple:
    """取 item 的某一层 -> `(width, height, RGBA8 bytes)`。

    `index`：同名条目取第几条（`-1` = 最后一条 = C# `-i -1` 的行为）。
    """
    return open_pool(gts_path).extract_layer(item_name, layer, index)


# ---------------------------------------------------------------- TGA（判据要用，命令行与用例共用）
def read_tga_rgba(path) -> tuple:
    """读 24/32 位 TGA（含 RLE），一律归一到**左上原点**的 RGBA8。"""
    target = Path(path)
    raw = target.read_bytes()
    if len(raw) < 18:
        raise GraniteVtError(f"{target.name} 只有 {len(raw)} B，比一个 TGA 头还短")
    id_length = raw[0]
    image_type = raw[2]
    width = struct.unpack_from("<H", raw, 12)[0]
    height = struct.unpack_from("<H", raw, 14)[0]
    depth = raw[16]
    descriptor = raw[17]
    if image_type not in (2, 10):
        raise GraniteVtError(f"{target.name} 的 TGA 类型 {image_type} 不支持（只认 2 / 10）")
    if depth not in (24, 32):
        raise GraniteVtError(f"{target.name} 的 TGA 位深 {depth} 不支持（只认 24 / 32）")
    step = depth // 8
    count = width * height
    pixels = bytearray(count * 4)
    pos = 18 + id_length
    index = 0
    while index < count:
        if image_type == 2:
            run = 1
            repeated = False
        else:
            header = raw[pos]
            pos += 1
            run = (header & 0x7F) + 1
            repeated = bool(header & 0x80)
        if repeated:
            blue, green, red = raw[pos], raw[pos + 1], raw[pos + 2]
            alpha = raw[pos + 3] if step == 4 else 255
            pos += step
            pixels[index * 4 : (index + run) * 4] = bytes((red, green, blue, alpha)) * run
            index += run
        else:
            for _ in range(run):
                blue, green, red = raw[pos], raw[pos + 1], raw[pos + 2]
                alpha = raw[pos + 3] if step == 4 else 255
                pos += step
                pixels[index * 4 : index * 4 + 4] = bytes((red, green, blue, alpha))
                index += 1
    if not (descriptor & 0x20):
        stride = width * 4
        flipped = bytearray(len(pixels))
        for row in range(height):
            src = (height - 1 - row) * stride
            flipped[row * stride : (row + 1) * stride] = pixels[src : src + stride]
        pixels = flipped
    return width, height, bytes(pixels)

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   docs/VT-item-目录.md（VT 的 .gts / .gtp 容器与 item 目录）
# --------------------------------------------------------------------------- #
