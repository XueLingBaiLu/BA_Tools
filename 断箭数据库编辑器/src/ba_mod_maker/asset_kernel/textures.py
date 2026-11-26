"""W7-A · DXT 编码 + Texture2D 四个流式字段的**纯函数**（etcpak 0.9.15）。

## 为什么单开一个模块

工单 W7 §4 点名：DXT 编码与「四个字段一起改」这两件事既要给写回链用，又要能
**在仓内 venv 里单测**（Blender 侧那份只是同一个模块的另一个宿主）。所以：

* ⛔ 模块顶层**不许 import 宿主 3D 主程序模块**（工单 §4；C5 契约连这个名字的**字面量**
  都不许出现在 asset_kernel/ 的任何一行里，注释与文档串也算）；PIL 也只在真要下采样时才延迟 import；
* 本模块只做纯函数：字节 -> 字节、typetree -> typetree，**不碰磁盘、不碰容器**。

## 为什么是 etcpak 而不是 crn

工单 §5 明令：⛔ 不许用 crn 编码（Python 生态里没有那个编码器）。实测本机
**etcpak 0.9.15 在 venv 与 Blender 双侧都可用**（_work/w7_probe_etcpak.py）：

    etcpak.compress_bc1(rgba, width, height) -> 字节（BC1 = DXT1，块 8 B）
    etcpak.compress_bc3(rgba, width, height) -> 字节（BC3 = DXT5，块 16 B）
    etcpak.compress_bc7(rgba, width, height, None) -> 字节（BC7，块 16 B；★ L5 接的这条）
    etcpak.compress_bc4(rgba, width, height) -> 字节（BC4，块 8 B；单通道图）

⚠ 两个实测口径：① width/height 必须是 **4 的倍数**（2x2 / 1x1 直接抛
"width or height not multiple of 4"）⇒ 尾级要**先补成一块**再编；
② 长度不靠猜 —— 编完必须与 dxt_payload_size 的公式值**逐字节对账**。

## m_CompleteImageSize：**非 Crunched** 才是"未压缩总量"（工单 §7 坑 3 要补一句）

- **非 Crunched**（10 DXT1 / 12 DXT5）：m_CompleteImageSize == 解码后 DXT 数据的总量
  （含全部 mip）== m_StreamData.size。W6 实测锚点：512² DXT5 + mip 10 = **349,552 B**，
  与 .resS 区间逐字节对上。
- **Crunched**（28 / 29）：⚠️ **实测它存的是 crn 流字节数，不是未压缩总量**
  （_work/w7_probe_delta.py 真件读数：RU_T72B_1_BaseMap 2048² DXT5Crunched mip 12
   的 m_CompleteImageSize = **871,011** == m_StreamData.size，而未压缩总量是 5,592,432）。
  ⇒ 工单 §7 坑 3 那句话**只对非 Crunched 成立**；文档与实测不符时**以实测为准**，
    ⛔ 不许为了对齐文档改口径。换成非 Crunched 之后，这个字段必须跟着改成公式值
    （5,592,432），否则游戏会按旧长度解一段更长的 DXT 字节。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "BLOCK_BYTES",
    "ENCODABLE_FORMATS",
    "FORMAT_NAMES",
    "ENCODER_ARGS",
    "ENCODER_NAMES",
    "dxt_payload_size",
    "encode_dxt",
    "format_name",
    "mip_bytes",
    "mip_dims",
    "check_texture_streaming",
    "read_texture_fields",
    "set_texture_streaming",
    "texture_stream_path",
]

#: 本作实测出现过的格式号（与 asset_kernel.texture.FORMAT_NAMES 同口径；
#: 设计稿 §3.5 的分布：DXT5 483 / DXT5Crunched 1649 / DXT1Crunched 479 /
#: DXT1 109 / RGBA32 3 / RGB24 1 / BC4 1）。
FORMAT_NAMES: dict[int, str] = {
    1: "Alpha8",
    3: "RGB24",
    4: "RGBA32",
    10: "DXT1",
    12: "DXT5",
    25: "BC7",
    26: "BC4",
    28: "DXT1Crunched",
    29: "DXT5Crunched",
}

#: 块字节数（块 = 4x4）：DXT1 = 8 B、DXT5 = 16 B、**BC7 = 16 B、BC4 = 8 B**。
#: （实测 etcpak 0.9.15：compress_bc7(64x64) -> 4096 B = 256 块 × 16 B；
#:  compress_bc4(64x64) -> 2048 B = 256 块 × 8 B。）
#: ★ 2026-10-17（L5）：25 / 26 加在**这一张表**里 —— dxt_payload_size /
#: check_texture_streaming / texture_build.build_streaming_texture_fields /
#: texture_install.texture_readback_problems **全都读它**，加一处就四处都通
#: （⛔ 别在别处再抄第二张块字节表）。
BLOCK_BYTES: dict[int, int] = {10: 8, 12: 16, 25: 16, 26: 8}

#: 本模块**能编码**的格式号（etcpak 直接产出的）：
#: 10 DXT1 / 12 DXT5 / **25 BC7** / **26 BC4**（单通道图那条 —— 真件 MaskMap 就走它）。
#: ⛔ Crunched（28 / 29）不在里面：Python 生态里**只有 crn 解码器、没有编码器**
#: （工单 §5），那不是能编出来的，只能换格式。
ENCODABLE_FORMATS: tuple[int, ...] = (10, 12, 25, 26)

#: 格式号 -> etcpak 的函数名（BC1 = DXT1、BC3 = DXT5、BC7 = BC7、BC4 = BC4）。
ENCODER_NAMES: dict[int, str] = {
    10: "compress_bc1",
    12: "compress_bc3",
    25: "compress_bc7",
    26: "compress_bc4",
}

#: ★ 2026-10-17（L5）：编码器签名里 (data, width, height) 之后还要补的**固定实参**。
#: 实测 etcpak 0.9.15：compress_bc7(data, w, h, params) 要**四个**位置参数
#: （bc1/bc3/bc4/bc5 都只吃三个）；params=None 就是默认质量档
#: （BC7CompressBlockParams() 那套旋钮本产品不碰）。
ENCODER_ARGS: dict[int, tuple[Any, ...]] = {25: (None,)}


def format_name(format_id: int) -> str:
    """格式号 -> 名字（认不出就给 format#N，⛔ 不猜成 DXT5）。"""
    return FORMAT_NAMES.get(int(format_id), f"format#{int(format_id)}")


# --------------------------------------------------------------------------- #
# 长度公式（与 W6 的 blender_addon/operators.py::paint_payload_size 同一个口径）
# --------------------------------------------------------------------------- #
def mip_dims(width: int, height: int, mip_count: int) -> list[tuple[int, int]]:
    """整条 mip 链的每一级尺寸（逐级降到 1x1；工单 §7 坑 3：⛔ 别只写一级）。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"贴图尺寸必须是正数：{width}x{height}")
    if mip_count <= 0:
        raise ValueError(f"mip 数必须是正数：{mip_count}")
    return [(max(1, int(width) >> i), max(1, int(height) >> i)) for i in range(int(mip_count))]


def mip_bytes(width: int, height: int, block: int = 16) -> int:
    """一级 mip 的字节数 = ceil(w/4) * ceil(h/4) * block（不足一块按一块算）。"""
    return max(1, (int(width) + 3) // 4) * max(1, (int(height) + 3) // 4) * int(block)


def dxt_payload_size(width: int, height: int, format_id: int, mip_count: int) -> int:
    """整条 mip 链的字节数 —— 由 **(格式, 宽, 高, mip 数)** 唯一决定（设计稿 §9.10.2）。

    实测锚点（工单 §2）：2048² DXT5 全 mip 链（12 级到 1x1）= **5,592,432 B**；
    512² DXT5 + mip 10 = **349,552 B**（== W6 靶子的 m_StreamData.size）。
    """
    block = BLOCK_BYTES.get(int(format_id))
    if block is None:
        raise ValueError(
            f"格式 {int(format_id)}（{format_name(format_id)}）的块字节数未知："
            f"本模块只认 {sorted(BLOCK_BYTES)}（10 DXT1 / 12 DXT5 / 25 BC7 / 26 BC4）"
        )
    return sum(mip_bytes(w, h, block) for w, h in mip_dims(width, height, mip_count))


# --------------------------------------------------------------------------- #
# 编码
# --------------------------------------------------------------------------- #
def _round_up4(value: int) -> int:
    return max(4, ((int(value) + 3) // 4) * 4)


def _pad_to_block(rgba: bytes, width: int, height: int) -> tuple[bytes, int, int]:
    """把一级 mip 补成 4 的倍数（etcpak 对 2x2 / 1x1 会直接抛）。

    补法是**边缘复制**（与 Pillow 对小于 4 的尾级同向）：多出来的那一圈
    取最靠边的那一行 / 那一列。块内多出来的像素不影响主像素的量化。
    """
    pw = _round_up4(width)
    ph = _round_up4(height)
    if pw == width and ph == height:
        return rgba, pw, ph
    out = bytearray(pw * ph * 4)
    for y in range(ph):
        src_y = min(y, height - 1)
        row = rgba[src_y * width * 4 : (src_y + 1) * width * 4]
        base = y * pw * 4
        for x in range(pw):
            src_x = min(x, width - 1)
            out[base + x * 4 : base + x * 4 + 4] = row[src_x * 4 : src_x * 4 + 4]
    return bytes(out), pw, ph


def _encode_level(rgba: bytes, width: int, height: int, format_id: int) -> bytes:
    """一级 -> 块字节（走 etcpak；补到 4 的倍数再编）。"""
    import etcpak  # noqa: PLC0415  -- 延迟 import：顶层保持零第三方（工单 §4）

    name = ENCODER_NAMES.get(int(format_id))
    if name is None:
        raise ValueError(
            f"不能编码格式 {int(format_id)}（{format_name(format_id)}）："
            f"本模块只能编 {sorted(ENCODER_NAMES)}"
        )
    padded, pw, ph = _pad_to_block(rgba, width, height)
    encoder = getattr(etcpak, name)
    # ★ L5：bc7 的签名多一个 params（ENCODER_ARGS），其余格式就是三个位置参数收尾。
    return bytes(encoder(padded, pw, ph, *ENCODER_ARGS.get(int(format_id), ())))


def encode_dxt(rgba: bytes, width: int, height: int, format_id: int, mip_count: int) -> bytes:
    """RGBA -> 整条 mip 链的 DXT 载荷（mip0 -> mipN 顺序拼接）。

    * ⛔ **逐级编到 1x1**（工单 §7 坑 3）：mip_count 给几级就编几级；
    * ⛔ 编完必须 len(out) == dxt_payload_size(...)，不等就抛（工单 §2 的口径）；
    * 下采样用 LANCZOS（与 W6 paint_encode_payload 同一手法），**从原图**逐级降。
    """
    width, height, mip_count = int(width), int(height), int(mip_count)
    data = bytes(rgba)
    if len(data) != width * height * 4:
        raise ValueError(
            f"RGBA 字节数不符：{len(data):,} B != {width}x{height}x4 = {width * height * 4:,} B"
        )
    levels = mip_dims(width, height, mip_count)
    block = BLOCK_BYTES.get(int(format_id))
    if block is None:
        raise ValueError(
            f"不能编码格式 {int(format_id)}（{format_name(format_id)}）："
            f"本模块只能编 {sorted(ENCODER_NAMES)}"
        )
    out = bytearray()
    image = None
    for index, (level_w, level_h) in enumerate(levels):
        if (level_w, level_h) == (width, height):
            level_bytes = data
        else:
            if image is None:
                from PIL import Image  # noqa: PLC0415  -- 只在真要下采样时拉起来

                image = Image.frombytes("RGBA", (width, height), data)
            level_bytes = image.resize((level_w, level_h), Image.LANCZOS).tobytes()
        out += _encode_level(level_bytes, level_w, level_h, format_id)
        got = len(out) - sum(mip_bytes(w, h, block) for w, h in levels[:index])
        want = mip_bytes(level_w, level_h, block)
        if got != want:
            raise ValueError(f"第 {index} 级 mip 编码长度不符：{got} B != 公式 {want} B")
    total = dxt_payload_size(width, height, format_id, mip_count)
    if len(out) != total:
        raise ValueError(
            f"编码长度不符：得到 {len(out):,} B != 公式 {total:,} B"
            f"（格式 {format_id} {width}x{height} mip {mip_count}）—— ⛔ 中止"
        )
    return bytes(out)


# --------------------------------------------------------------------------- #
# Texture2D 的四个字段（工单 §7 坑 2：**必须一起改**）
# --------------------------------------------------------------------------- #
def _stream_of(tree: Mapping[str, Any]) -> Mapping[str, Any]:
    stream = tree.get("m_StreamData")
    if not isinstance(stream, Mapping):
        raise KeyError(
            "这个 Texture2D 没有 m_StreamData（不是流式贴图）—— "
            "本模块只处理流式贴图（设计稿 §3.5：本作贴图 100% 流式）"
        )
    return stream


def read_texture_fields(tree: Mapping[str, Any]) -> dict[str, Any]:
    """**只读**：把这块贴图的格式 / mip / 未压缩总量 / 流式区间摊平成一个 dict。

    键：name / width / height / format / format_name / mip_count / color_space /
    complete_image_size / stream = {"path", "offset", "size"}。
    """
    stream = _stream_of(tree)
    format_id = int(tree.get("m_TextureFormat", 0) or 0)
    return {
        "name": str(tree.get("m_Name", "") or ""),
        "width": int(tree.get("m_Width", 0) or 0),
        "height": int(tree.get("m_Height", 0) or 0),
        "format": format_id,
        "format_name": format_name(format_id),
        "mip_count": int(tree.get("m_MipCount", 0) or 0),
        "color_space": int(tree.get("m_ColorSpace", 0) or 0),
        "complete_image_size": int(tree.get("m_CompleteImageSize", 0) or 0),
        "stream": {
            "path": str(stream.get("path", "") or ""),
            "offset": int(stream.get("offset", 0) or 0),
            "size": int(stream.get("size", 0) or 0),
        },
    }


def texture_stream_path(tree: Mapping[str, Any]) -> str:
    """这块贴图的 .resS 节点路径（StreamAppend.node_path 就用它）。"""
    return str(_stream_of(tree).get("path", "") or "")


#: Crunched 的格式号（设计稿 §9.9：78% 是这一类，它们的流字节数**不**等于未压缩总量）。
CRUNCHED_FORMATS: tuple[int, ...] = (28, 29)


def check_texture_streaming(tree: Mapping[str, Any]) -> dict[str, Any]:
    """**读回校验**（H7 的那把尺子）：四个字段必须互相自洽。

    判据（写成机械断言，⛔ 不靠肉眼）：

    1. 格式认得，且本模块能编它（或它是 Crunched —— 那种不参与第 3 条）；
    2. **非 Crunched** 时 m_CompleteImageSize == dxt_payload_size(w, h, 格式, mip 数)
       （它才是"未压缩总量"；Crunched 时实测存的是 crn 字节数，见模块头）；
    3. 非 Crunched 时 m_StreamData.size == m_CompleteImageSize（换格式后两者必然相等）；
    4. m_StreamData.path 非空、offset >= 0、size > 0。

    :returns: {"ok": bool, "problems": [...], "fields": {...}}。
        ⚠ 故意写错一个数就必须 ok=False —— 这条能红才算尺子（AGENTS §0.9 动作 2）。
    """
    fields = read_texture_fields(tree)
    problems: list[str] = []
    format_id = int(fields["format"])
    width, height, mips = fields["width"], fields["height"], fields["mip_count"]
    complete = int(fields["complete_image_size"])
    stream = fields["stream"]
    if format_id not in FORMAT_NAMES:
        problems.append(f"格式号 {format_id} 不认识")
    if width <= 0 or height <= 0:
        problems.append(f"尺寸不合法：{width}x{height}")
    if mips <= 0:
        problems.append(f"mip 数不合法：{mips}")
    if not problems:
        if format_id in BLOCK_BYTES:
            expect = dxt_payload_size(width, height, format_id, mips)
            if complete != expect:
                problems.append(
                    f"m_CompleteImageSize {complete:,} != 未压缩总量 {expect:,}"
                    f"（格式 {format_id} {width}x{height} mip {mips}）"
                )
            if int(stream["size"]) != complete:
                problems.append(
                    f"m_StreamData.size {int(stream['size']):,} != m_CompleteImageSize "
                    f"{complete:,}（非 Crunched 时两者必须相等）"
                )
        elif format_id not in CRUNCHED_FORMATS:
            problems.append(f"格式 {format_id} 的块字节数未知，算不出未压缩总量")
    if not str(stream["path"] or ""):
        problems.append("m_StreamData.path 是空的")
    if int(stream["offset"]) < 0:
        problems.append(f"m_StreamData.offset 是负数：{stream['offset']}")
    if int(stream["size"]) <= 0:
        problems.append(f"m_StreamData.size 不是正数：{stream['size']}")
    return {"ok": not problems, "problems": problems, "fields": fields}


def set_texture_streaming(
    tree: Mapping[str, Any],
    *,
    format_id: int,
    complete_image_size: int,
    offset: int,
    size: int,
) -> dict[str, Any]:
    """**纯函数**：把四个字段**一起**改掉，返回新的 typetree（不动入参）。

    改的是：m_TextureFormat / m_CompleteImageSize / m_StreamData.offset /
    m_StreamData.size。

    ⛔ 工单 §7 坑 2：只改 m_TextureFormat 不改编码 = 必坏
    （Unity 会按新格式去解一段 crn 字节）。所以四个字段必须落在**同一次写回**里，
    而且 .resS 里那一段新像素也要一起落位。

    ⚠ m_StreamData.path 一个字都不动（节点名带建包时的哈希，⛔ 不许按当前
    包名重拼）。m_MipCount 也不动 —— 换格式不改 mip 级数。
    """
    stream = dict(_stream_of(tree))
    if not str(stream.get("path", "") or ""):
        raise ValueError("m_StreamData.path 是空的 —— 没有落点，不许改")
    new_size = int(size)
    if new_size <= 0:
        raise ValueError(f"新的 m_StreamData.size 必须是正数：{new_size}")
    new_offset = int(offset)
    if new_offset < 0:
        raise ValueError(f"新的 m_StreamData.offset 不能是负数：{new_offset}")
    stream["offset"] = new_offset
    stream["size"] = new_size
    out = dict(tree)
    out["m_TextureFormat"] = int(format_id)
    out["m_CompleteImageSize"] = int(complete_image_size)
    out["m_StreamData"] = stream
    return out
