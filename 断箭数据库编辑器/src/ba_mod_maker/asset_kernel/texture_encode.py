r"""L5 · 新图 -> **BC7 / BC4 块压缩** -> 流式 Texture2D -> 小包 -> 读回（etcpak 0.9.15）。

## 这一格接的是什么

交接 §3 L5：把 etcpak 接到**贴图编码**路径 —— 新图（PNG / Blender 图像）编成
**BC7（Unity m_TextureFormat = 25）**，写进包并让游戏认。

⛔ 别与两条老路混：

* `asset_kernel/texture_build.py` 的块压缩是**原样搬运**那条（不编解码、
  `m_CompleteImageSize = len(pixels)`）；
* `texture apply`（`texture_install.plan_texture_apply`）是**把新像素塞进一个既有件包**那条
  （今天写死 DXT5 = 12）；
* 本模块是**第三条**：从一张新图**从头造一张流式 Texture2D ＋ 装着它的小包**。
  编码与长度公式全部复用（`textures.encode_dxt` / `dxt_payload_size`），⛔ 不另写编码器。

## 口径（每条都有出处或实跑读数）

1. **格式码**：`25 = BC7`、`26 = BC4`（`texture_build.FORMAT_NAMES` 与 venv 里
   UnityPy 1.25.3 的枚举两处独立坐实）。真件在用 BC7：dlc_2 包 636 张 Texture2D 里
   **BC7 105 张**（docs/研究-断箭资产制作-方法与工具.md §3.2 的全量普查）；
2. **mip 链 = 满链**（一路降到 1x1；2048² 配 **12** 级）—— 级数取
   `texture_install.full_mip_count`（降级规则只有 `textures.mip_dims` 那一份）；
3. **全部 streaming**：`image data` 空、像素在 `.resS`（真件 2,718/2,718 全开，研究 §3.2）。
   ★ 本机实测（`_work/v3a_tex_fixture.json` 里 4 张真件 Texture2D，只读）：
   `m_StreamingMipmaps` **False 4/4** · `m_IsReadable` **False 4/4** ·
   `m_StreamingMipmapsPriority` 0 4/4 ⇒ 本模块造字段树时**显式**给 `readable=False`；
4. **块字节数**：BC7 = 16 B/块、BC4 = 8 B/块（实测 etcpak：
   `compress_bc7(64x64) -> 4096 B` = 256 块 × 16 B；`compress_bc4(64x64) -> 2048 B`）。
   ⇒ 2048² BC7 满 12 级 = **5,592,432 B**（与 DXT5 同值）、512² 满 10 级 = **349,552 B**
   （W6 锚点）。⚠ 块字节数**只有一份**（`textures.BLOCK_BYTES`），本模块不另抄。

## ⛔ 两条 landmine（本机实测，别踩）

1. **`etcpak.decompress_bc7` 是坏的**（0.9.15 实测）：64x64 直接 **0xC0000005 访问违例**
   （进程当场死，Python 层接不住）、4x4 `MemoryError`、8x8 返回垃圾字节
   （`303533d4…` vs 正确的 `1e1ec8fe…`）。
   ⇒ **解回一律走 `texture2ddecoder.decode_bc7`**（**独立实现**；venv 与插件 `_vendor` 双侧都有）。
   `tests/test_texture_encode.py` 里有一条**能红**的探针：扫本模块源码，
   `decompress_bc7` 出现 **0** 次。
2. **全部 BC 编码器都要求宽高是 4 的倍数**（`ValueError: width or height not multiple of 4`）
   ⇒ 尾级 / 非 4 倍数尺寸由 `textures._pad_to_block` 边缘复制补成一块再编（口径已在那一份里）。

## 通道序（解回时的那个坑，已实测钉死）

`texture2ddecoder` 的解码器返回 **BGRA** 序 —— UnityPy 自己就是这么用的
（`.venv/Lib/site-packages/UnityPy/export/Texture2DConverter.py:353`
 `Image.frombytes("RGBA", …, "raw", "BGRA")`）。
实测锚点（源 RGBA = (10,20,30,40) 的 4x4 常量块）：

    decode_bc7(compress_bc7(src)) -> 1e 14 0a 28 == BGRA 解出的 (10,20,30,40)  <- 逐字节相同
    decode_bc3(compress_bc3(src)) -> 18 14 08 28 == (8,20,24,40)（5:6:5 量化）
    decode_bc4(compress_bc4(src)) -> 00 00 0a ff == BGRA 里 R 那一格是 10

⇒ `decode_blocks(..., to_rgba=True)` 默认换回 RGBA；换不换由参数说话，⛔ 不靠记性。

## Crunched 族（28 / 29）

Python 生态**只有 crn 解码器、没有编码器** ⇒ 本模块对它们**明确拒绝并指名理由**
（⛔ 不静默换格式）。

★ **未来路线（Lead 2026-10-17 记）**：要真的产出 Crunched，可行的那条是**借本机 Unity 6
编辑器**（`D:/Unity/6000.3.24f1`，与游戏同版）走 importer / `BuildAssetBundles` 出 crunched
贴图 —— 形状与 L3 路 B（借 Unity 做 clip）同族，将来并进那条线。⛔ 本文件不写「做不到」。

## 两条**已知债 / 口径差异**（Lead 2026-10-17 裁定：先记账，不动老路）

* `# TODO(债)` **格式名表有两份**：`texture_build.FORMAT_NAMES` 有 5/48/62/63 但**缺 26（BC4）**；
  `textures.FORMAT_NAMES` 有 26 但没有那四个。`format_name()` 现在**两份都问**（⛔ 没抄第三份），
  真正的统一要并成一张 —— 那会动 4 个模块，**是另一笔债**，等 Lead 排期。
* ⚠ **适用范围只到「等长换格式」**（Lead 2026-10-17 裁定接受）：块字节数相同的两档才换得了
  —— DXT1(8) / DXT5(16) / BC7(16) / BC4(8) 各自成组；`RGBA32 -> BC7` 会**变长**，
  `reencode_in_bundle` **明确拒**（⛔ 不偷偷 padding 凑长度）。实测代价：`unitmodifications`
  里 **123/163 张是 RGBA32**，这条路**没覆盖**它们；
  要覆盖得接 `writer.write_delta` 的变长档（**另立一笔**）。
* ⚠ **mip 默认口径老路 / 新路不同**（**有意保持**，逐字节不变优先）：
  `texture_install.encode_png_to_payload`（`texture encode` 老路）用 `width.bit_length()`；
  本模块用 `full_mip_count`（max(w,h) 逐级到 1x1）。**width 不是最大边时两者不同**
  （例 4x1024：老路 3 级、新路 11 级）。⛔ 没动老路 —— 动了它已验过的载荷字节就变了。
## 判据（每条都能红）

    .venv/Scripts/python.exe -m ba_mod_maker.cli texture build --png 图.png --source <真包> ^
        --asset-path <容器路径> --format bc7 --out 新包.bundle --json
    pytest tests/test_texture_encode.py -q -n 0
"""

from __future__ import annotations

import hashlib
import shutil
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone, textures
from ba_mod_maker.asset_kernel import texture_build as tb
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "BC4",
    "BC7",
    "CRUNCHED_FORMATS",
    "CRUNCHED_NAMES",
    "DECODER_NAMES",
    "DEFAULT_FORMAT",
    "DXT1",
    "DXT5",
    "FORMAT_BY_NAME",
    "SINGLE_CHANNEL_FORMAT",
    "SOURCE_DECODERS",
    "UNCOMPRESSED_BPP",
    "EncodePlan",
    "EncodedTexture",
    "ReencodePlan",
    "bgra_to_rgba",
    "build_encoded_bundle",
    "channel_stats",
    "decode_blocks",
    "decode_level",
    "encode_png",
    "encode_pixels",
    "encodable_format",
    "format_name",
    "format_of_name",
    "full_mip_count",
    "level_payload",
    "level_sizes",
    "payload_size",
    "read_source_meta",
    "stream_path_for",
    "byte_delta",
    "game_root_of",
    "guard_out_path",
    "inside_game_dir",
    "decode_levels",
    "encode_levels",
    "level_byte_sizes",
    "reencode_in_bundle",
    "restore_from_delta",
    "verify_encoded_out",
    "verify_reencode",
    "write_encoded_bundle",
    "write_revert_patch",
]

#: 格式码（Unity TextureFormat 枚举值）。
DXT1 = 10
DXT5 = 12
BC7 = 25
BC4 = 26

#: L5 的默认格式：**BC7**。
DEFAULT_FORMAT = BC7
#: 单通道图（Mask / 粗糙度 / 高度）走这条 —— 研究 §3.2 的 BC4 那几张就是它。
SINGLE_CHANNEL_FORMAT = BC4
#: Unity 的 Crunched 族 —— 本模块**编不出来**（Python 生态只有解码器）。
CRUNCHED_FORMATS: tuple[int, ...] = (28, 29)

#: 命令行认的格式名 -> 格式码。
FORMAT_BY_NAME: dict[str, int] = {
    "bc1": DXT1,
    "dxt1": DXT1,
    "bc3": DXT5,
    "dxt5": DXT5,
    "bc4": BC4,
    "bc7": BC7,
}

#: Unity 的 Crunched 族**有名字可以敲**（敲了不是「不认识」，而是「编不出来」+ 指名理由）——
#: ⛔ 不静默换格式：CLI 会把这些名字放行到 `format_of_name`，由它抛那条带理由的错误。
CRUNCHED_NAMES: dict[str, int] = {
    "crunched": 29,
    "dxt5crunched": 29,
    "bc3crunched": 29,
    "dxt1crunched": 28,
    "bc1crunched": 28,
}

#: 格式码 -> texture2ddecoder 的解码函数名（⛔ 不是 etcpak 的 —— 见模块头 landmine 1）。
DECODER_NAMES: dict[int, str] = {
    DXT1: "decode_bc1",
    DXT5: "decode_bc3",
    BC7: "decode_bc7",
    BC4: "decode_bc4",
}


def format_name(format_id: int) -> str:
    """格式号 -> 名字（认不出给 format#N，⛔ 不猜）。

    ⚠ 两张名字表都在用（`texture_build.FORMAT_NAMES` 有 5/48/62/63 但没有 26；
    `textures.FORMAT_NAMES` 有 26 但没有那四个）⇒ 这里**两份都问**，⛔ 不抄第三份。
    """
    fmt = int(format_id)
    name = tb.FORMAT_NAMES.get(fmt) or textures.FORMAT_NAMES.get(fmt)
    return name or f"format#{fmt}"


def format_of_name(name: str) -> int:
    """命令行格式名 -> 格式码（认不出就抛，⛔ 不默认成 bc3）。

    ⚠ Crunched 的名字**认得出来但编不出来** —— 这里直接把那条带理由的错误抛出去
    （`encodable_format` 里的同一句话），这样敲 `--format crunched` 的人拿到的是
    「为什么不行 + 可行的那条路」，而不是 argparse 的 `invalid choice`。
    """
    key = str(name or "").strip().lower()
    if key in CRUNCHED_NAMES:
        # 复用**同一句话**：口径只有一份
        return encodable_format(CRUNCHED_NAMES[key])
    fmt = FORMAT_BY_NAME.get(key)
    if fmt is None:
        raise AssetError(
            f"不认的格式名 {name!r}",
            hint="认这几个：" + "、".join(sorted(FORMAT_BY_NAME | set(CRUNCHED_NAMES))),
        )
    return int(fmt)


def encodable_format(format_id: int) -> int:
    """能编就返回它自己；**Crunched 与不认的格式一律抛**（⛔ 不静默换格式）。"""
    fmt = int(format_id)
    if fmt in CRUNCHED_FORMATS:
        raise AssetError(
            f"格式 {fmt}（{format_name(fmt)}）编不出来："
            "**Python 生态里只有 crn 解码器、没有编码器**",
            hint="要 Crunched 贴图只能借 Unity 编辑器出（见模块头「未来路线」）；"
                 "本产品这条路上请**显式**选 bc7 / bc3 —— 那等于把格式从 Crunched "
                 "改成非 Crunched，不是偷偷换",
        )
    if fmt not in textures.ENCODABLE_FORMATS:
        allowed = "、".join(f"{k}={format_name(k)}" for k in textures.ENCODABLE_FORMATS)
        raise AssetError(
            f"格式 {fmt}（{format_name(fmt)}）编不出来：etcpak 直接能产的只有 {allowed}"
        )
    return fmt


def payload_size(width: int, height: int, format_id: int, mip_count: int) -> int:
    """整条 mip 链的字节数 —— 口径**只有一份**（`textures.dxt_payload_size`）。"""
    return textures.dxt_payload_size(
        int(width), int(height), encodable_format(format_id), int(mip_count)
    )


def full_mip_count(width: int, height: int) -> int:
    """满 mip 链的级数 —— 一路降到 1x1（2048² -> 12、1024x512 -> 11）。

    级数口径**只有一份**：懒 import `texture_install.full_mip_count`（它自己走
    `textures.mip_dims` 数）。⛔ 不在这里再写一遍降级循环。
    """
    from ba_mod_maker.asset_kernel import texture_install  # noqa: PLC0415 -- 口径只有一份

    return int(texture_install.full_mip_count(int(width), int(height)))


def level_sizes(width: int, height: int, format_id: int, mip_count: int) -> tuple[int, ...]:
    """每一级 mip 的块字节数（长度 = mip 级数）。"""
    fmt = encodable_format(format_id)
    block = textures.BLOCK_BYTES[fmt]
    return tuple(
        int(textures.mip_bytes(w, h, block))
        for w, h in textures.mip_dims(int(width), int(height), int(mip_count))
    )


def _flip_rows(pixels: bytes, width: int, height: int) -> bytes:
    """自上而下 -> 自下而上（方向契约只有一份：`texture_install.flip_rows`）。"""
    from ba_mod_maker.asset_kernel import texture_install  # noqa: PLC0415 -- 口径只有一份

    return bytes(texture_install.flip_rows(bytes(pixels), int(width), int(height)))


def channel_stats(rgba: bytes, width: int, height: int) -> dict[str, int]:
    """单通道性读数：R==G==B 的像素数 / 总像素。

    BC4 **只保 R 通道**（真件 MaskMap 走它）—— 喂一张彩色图给它，G/B 当场丢。
    所以这条读数是**给人看的门**，不是判据：CLI 在 BC4 且彩色像素 > 0 时印一行大字。
    """
    raw = bytes(rgba)
    total = int(width) * int(height)
    if len(raw) != total * 4:
        raise AssetError(
            f"RGBA 字节数不符：{len(raw):,} != {int(width)}x{int(height)}x4"
        )
    r, g, b = raw[0::4], raw[1::4], raw[2::4]
    same = sum(1 for x, y, z in zip(r, g, b, strict=True) if x == y and y == z)
    return {"same_rgb_pixels": int(same), "total": int(total)}


# --------------------------------------------------------------------------- #
# 编码（复用 textures.encode_dxt —— ⛔ 不另写编码器）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EncodedTexture:
    """一张编好的块压缩贴图（载荷 = 整条 mip 链，mip0 -> mipN 顺序拼接）。"""

    width: int
    height: int
    format: int
    mip_count: int
    payload: bytes = field(repr=False, default=b"")
    level_sizes: tuple[int, ...] = ()
    flipped: bool = True
    png: str = ""

    @property
    def payload_bytes(self) -> int:
        return len(self.payload)

    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    def lines(self) -> tuple[str, ...]:
        flip_text = "已翻成 Unity 的自下而上" if self.flipped else "**没翻**（原样）"
        block = textures.BLOCK_BYTES[int(self.format)]
        rows = [
            f"[贴图编码] {self.png or format_name(self.format)}  {self.width}x{self.height}"
            f"  格式      : {format_name(self.format)}（fmt {self.format}）"
            f" · mip {self.mip_count} 级（满链 {full_mip_count(self.width, self.height)} 级）"
            f" · 块 {block} B/块",
            f"  载荷      : {self.payload_bytes:,} B  sha256 {self.sha256()[:16]}…"
            f" · 末级 {self.level_sizes[-1] if self.level_sizes else 0} B",
            f"  行序      : {flip_text} · 像素进 .resS（image data 空）",
        ]
        return tuple(rows)


def encode_pixels(
    rgba: bytes,
    width: int,
    height: int,
    *,
    format_id: int = DEFAULT_FORMAT,
    mip_count: int | None = None,
    flip: bool = True,
    png: str = "",
) -> EncodedTexture:
    """RGBA（自上而下，PNG 行序）-> 块压缩 ＋ 满 mip 链的载荷。

    * `flip=True`（默认）先把行序翻成 Unity 的自下而上 —— 方向契约只有一份；
    * `mip_count=None` ⇒ **满链**（`full_mip_count`，2048² -> 12 级）；
    * 编完与 `payload_size` 的公式值**逐字节对账**，不等就抛。
    """
    fmt = encodable_format(format_id)
    w, h = int(width), int(height)
    if w < 1 or h < 1:
        raise AssetError(f"贴图尺寸不合法：{w}x{h}")
    raw = bytes(rgba)
    if len(raw) != w * h * 4:
        raise AssetError(
            f"RGBA 字节数不符：{len(raw):,} B != {w}x{h}x4 = {w * h * 4:,} B"
        )
    full = full_mip_count(w, h)
    mips = int(mip_count) if mip_count else full
    if mips < 1:
        raise AssetError(f"mip 级数必须 >= 1，拿到 {mips}")
    if mips > full:
        raise AssetError(
            f"mip 级数 {mips} 超过满链 {full}（{w}x{h}）—— ⛔ Unity 没有比 1x1 更小的级"
        )
    data = _flip_rows(raw, w, h) if flip else raw
    payload = textures.encode_dxt(data, w, h, fmt, mips)
    want = textures.dxt_payload_size(w, h, fmt, mips)
    if len(payload) != want:
        raise AssetError(
            f"编码出来的载荷 {len(payload):,} B != 公式值 {want:,} B"
            f"（{format_name(fmt)} {w}x{h} mip {mips}）—— ⛔ 不许硬写"
        )
    return EncodedTexture(
        width=w,
        height=h,
        format=fmt,
        mip_count=mips,
        payload=bytes(payload),
        level_sizes=level_sizes(w, h, fmt, mips),
        flipped=bool(flip),
        png=str(png or ""),
    )


def encode_png(
    png: str | Path,
    *,
    format_id: int = DEFAULT_FORMAT,
    mip_count: int | None = None,
    flip: bool = True,
) -> EncodedTexture:
    """读一张 PNG（统一 RGBA8）-> 编成块压缩载荷。**不碰任何包**。"""
    from ba_mod_maker.asset_kernel import texture_install  # noqa: PLC0415 -- 读图只有一份

    image = texture_install.read_png(png)
    return encode_pixels(
        image.pixels,
        image.width,
        image.height,
        format_id=format_id,
        mip_count=mip_count,
        flip=flip,
        png=str(png),
    )


# --------------------------------------------------------------------------- #
# 解回（**独立实现** texture2ddecoder；⛔ 不用 etcpak 的解码器 —— 见模块头 landmine 1）
# --------------------------------------------------------------------------- #
def bgra_to_rgba(buf: bytes) -> bytes:
    """BGRA -> RGBA（texture2ddecoder 的输出是 BGRA 序，实测锚点见模块头）。"""
    out = bytearray(bytes(buf))
    out[0::4], out[2::4] = out[2::4], out[0::4]
    return bytes(out)


def level_payload(
    payload: bytes, width: int, height: int, format_id: int, mip_level: int = 0
) -> bytes:
    """从整条链里切出第 `mip_level` 级的那一段（⛔ 别把整条链喂给解码器）。"""
    fmt = encodable_format(format_id)
    sizes = level_sizes(width, height, fmt, int(mip_level) + 1)
    start = sum(sizes[: int(mip_level)])
    end = start + sizes[int(mip_level)]
    data = bytes(payload)
    if len(data) < end:
        raise AssetError(
            f"载荷太短：只有 {len(data):,} B，切到第 {int(mip_level)} 级要 {end:,} B"
        )
    return data[start:end]


def decode_blocks(
    blocks: bytes,
    width: int,
    height: int,
    format_id: int,
    *,
    to_rgba: bool = True,
) -> bytes:
    """一级 mip 的块字节 -> 像素（走 `texture2ddecoder`，**独立实现**）。

    ⛔ **不许**换回 `etcpak.decompress_bc7`：本机实测它会 0xC0000005 崩进程（模块头 landmine 1）。
    `to_rgba=True`（默认）把 BGRA 换回 RGBA；BC4 解出来 R 那一格才是灰值。
    """
    fmt = int(format_id)
    name = DECODER_NAMES.get(fmt)
    if name is None:
        raise AssetError(
            f"格式 {fmt}（{format_name(fmt)}）本模块解不回来：只认 "
            + "、".join(f"{k}={format_name(k)}" for k in DECODER_NAMES)
        )
    w, h = int(width), int(height)
    need = int(textures.mip_bytes(w, h, textures.BLOCK_BYTES[fmt]))
    data = bytes(blocks)
    if len(data) != need:
        raise AssetError(
            f"块字节数不符：{len(data):,} B != {w}x{h} 的 {need:,} B（{format_name(fmt)}）"
        )
    return decode_with(name, data, w, h, to_rgba=to_rgba)


def decode_with(name: str, data: bytes, width: int, height: int, *, to_rgba: bool) -> bytes:
    """`texture2ddecoder.<name>(data, w, h)` ＋ 按需换回 RGBA（**唯一**的取解路）。"""
    import texture2ddecoder  # noqa: PLC0415 -- 只在真解码时拉起来

    bgra = bytes(getattr(texture2ddecoder, name)(data, int(width), int(height)))
    return bgra_to_rgba(bgra) if to_rgba else bgra


def decode_level(
    encoded: EncodedTexture, *, mip_level: int = 0, to_rgba: bool = True
) -> bytes:
    """正控用的**解回**：把编好的载荷切一级出来解（尺寸由 `mip_dims` 定）。"""
    w, h = textures.mip_dims(encoded.width, encoded.height, int(mip_level) + 1)[int(mip_level)]
    return decode_blocks(
        level_payload(encoded.payload, encoded.width, encoded.height, encoded.format, mip_level),
        w,
        h,
        encoded.format,
        to_rgba=to_rgba,
    )


# --------------------------------------------------------------------------- #
# 写进包：一张流式 Texture2D ＋ 装着它的小包（双节点 CAB + CAB.resS）
# --------------------------------------------------------------------------- #
def stream_path_for(node_path: str) -> str:
    """流式节点名 -> `m_StreamData.path` 的真件形状。

    真件实测（`_work/v3a_tex_fixture.json` 4/4）：
    `archive:/CAB-<hash>/CAB-<hash>.resS` —— 前缀只有一份（`builder.ARCHIVE_PREFIX`）。
    """
    node = str(node_path)
    if not node:
        raise AssetError("流式节点名是空的 —— 没有落点，不许造包")
    return f"{builder.ARCHIVE_PREFIX}{node}/{node}{builder.STREAM_NODE_SUFFIX}"


def read_source_meta(bundle: str | Path) -> bytes:
    """从**一个真包**里取元数据区（48 B 头 ＋ 类型表 ＋ 对象表）—— 类型树的唯一来源。

    ⛔ 只读：本函数一个字节都不写（连源包都不碰）。
    """
    from ba_mod_maker.core.asset import ufs  # noqa: PLC0415 -- 读容器只有一份

    path = Path(bundle)
    if not path.is_file():
        raise AssetError(f"找不到源包：{path}", hint="--source 指一个真包（它的类型树要被照抄）")
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        meta_size = int(struct.unpack_from(">I", head, 20)[0])
        blob = reader.read_range(node.offset, builder.SF_HEADER_SIZE + meta_size)
    if len(blob) != builder.SF_HEADER_SIZE + meta_size:
        raise AssetError(
            f"元数据区读回来 {len(blob):,} B != 声明的 {builder.SF_HEADER_SIZE + meta_size:,} B"
        )
    return bytes(blob)


def build_encoded_bundle(
    *,
    encoded: EncodedTexture,
    asset_path: str,
    source_meta: bytes | bytearray,
    bundle_name: str,
    name: str | None = None,
    color_space: int = 1,
    path_id: int | None = None,
    node_path: str | None = None,
    block_type: int = builder.BLOCK_NONE,
    common_strings: Mapping[int, str] | None = None,
) -> builder.ObjectBundlePlan:
    """装配「**一张流式 Texture2D ＋ 一个 AssetBundle**」的小包（＋ `.resS` 节点）。

    口径：

    * 字段树走 `texture_build.build_streaming_texture_fields`（24 键、`image data` 空、
      `m_StreamData.path` 指向本包的 `.resS`）—— ⛔ 不在这里再写一份字段表；
    * `readable=False` —— 真件 4/4 都是 False（模块头第 3 条）；
    * 类型树**从 source_meta 逐字节照抄**（`builder.build_object_bundle` 的口径）；
    * 容器路径 = catalog 的 internalId（`Assets/...`），不是裸名。
    """
    node = str(node_path or builder.node_path_for(str(bundle_name)))
    fields = tb.build_streaming_texture_fields(
        str(name or Path(str(asset_path)).stem or "L5_Texture"),
        int(encoded.width),
        int(encoded.height),
        texture_format=int(encoded.format),
        mip_count=int(encoded.mip_count),
        size=int(encoded.payload_bytes),
        stream_path=stream_path_for(node),
        offset=0,
        color_space=int(color_space),
        readable=False,
    )
    pid = int(path_id) if path_id is not None else builder.path_id_for(
        f"{bundle_name}|{asset_path}"
    )
    return builder.build_object_bundle(
        objects=[
            builder.ObjectSpec(
                class_id=builder.CLASS_TEXTURE2D,
                fields=fields,
                path_id=pid,
                label="l5:texture",
            )
        ],
        bundle_name=str(bundle_name),
        source_meta=source_meta,
        container=[(str(asset_path), pid)],
        common_strings=(
            common_strings if common_strings is not None else clone.unitypy_common_strings()
        ),
        block_type=int(block_type),
        node_path=node,
        preload_all=True,
        stream_payload=encoded.payload,
    )


def verify_encoded_out(
    bundle: str | Path,
    path_id: int,
    *,
    encoded: EncodedTexture,
    expect_stream_node_size: int | None = None,
) -> tuple[str, ...]:
    """**出包读回**：读 + 判一步到位（空元组 = 绿，非空 = 一行行问题）。

    尺子**只有一份**：`texture_install.read_texture_back` ＋ `texture_readback_problems`
    （七条判据都在那一份里）。本函数只负责把「我们要的那张图」的六个期望值喂进去：
    宽 / 高 / **格式（BC7 = 25）** / mip 级数 / 载荷逐字节 / `.resS` 节点长度。

    ★ 第 3 条就是**负控的落点**：把包里的 `m_TextureFormat` 从 25 改成 12 之后，
    这一条当场红（格式不符）；而且第 5 条（`m_CompleteImageSize` 与公式对账）也会跟着红。
    """
    from ba_mod_maker.asset_kernel import texture_install  # noqa: PLC0415 -- 尺子只有一份

    reading = texture_install.read_texture_back(bundle, int(path_id))
    return texture_install.texture_readback_problems(
        reading,
        expect_width=int(encoded.width),
        expect_height=int(encoded.height),
        expect_format=int(encoded.format),
        expect_mip_count=int(encoded.mip_count),
        expect_payload=encoded.payload,
        expect_stream_node_size=expect_stream_node_size,
    )


@dataclass(frozen=True, slots=True)
class EncodePlan:
    """一次「新图 -> 小包」的读数（**写盘由调用方决定**时 `out` 是 None）。"""

    source: Path
    asset_path: str
    bundle_name: str
    name: str
    encoded: EncodedTexture
    path_id: int
    out: Path | None = None
    bundle_bytes: int = 0
    stream_node_size: int = 0
    data: bytes = field(repr=False, default=b"")
    reading: Any = field(repr=False, default=None)
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        rows = list(self.encoded.lines())
        rows.append(
            f"  包        : {self.bundle_name}  ({self.bundle_bytes:,} B · 对象 2 "
            f"· .resS {self.stream_node_size:,} B · pathID {self.path_id})"
        )
        rows.append(f"  容器地址  : {self.asset_path}")
        if self.reading is not None:
            rows.append(
                f"  读回      : m_TextureFormat={int(self.reading.texture_format)}"
                f"（{format_name(self.reading.texture_format)}）"
                f" · {int(self.reading.width)}x{int(self.reading.height)}"
                f" · mip {int(self.reading.mip_count)}"
                f" · 流区间 [{int(self.reading.offset):,}, "
                f"{int(self.reading.offset) + int(self.reading.size):,})"
            )
            rows.append(
                "  出包复核  : "
                + ("**绿**（六个读数逐条对上）" if self.ok else f"**红 {len(self.problems)} 条**")
            )
            rows.extend(f"      X {p}" for p in self.problems)
        if self.out is not None:
            rows.append(f"  落点      : {self.out}")
        return tuple(rows)

    def document(self) -> dict[str, Any]:
        enc = self.encoded
        return {
            "action": "build",
            "source": str(self.source),
            "asset_path": self.asset_path,
            "bundle_name": self.bundle_name,
            "name": self.name,
            "width": int(enc.width),
            "height": int(enc.height),
            "format": int(enc.format),
            "format_name": format_name(enc.format),
            "mip_count": int(enc.mip_count),
            "payload_bytes": int(enc.payload_bytes),
            "payload_sha256": enc.sha256(),
            "path_id": int(self.path_id),
            "bundle_bytes": int(self.bundle_bytes),
            "stream_node_size": int(self.stream_node_size),
            "out": str(self.out) if self.out is not None else None,
            "readback": None if self.reading is None else self.reading.as_dict(),
            "problems": list(self.problems),
        }


def write_encoded_bundle(
    *,
    png: str | Path,
    source: str | Path,
    asset_path: str,
    out: str | Path | None = None,
    format_id: int = DEFAULT_FORMAT,
    mip_count: int | None = None,
    bundle_name: str | None = None,
    name: str | None = None,
    color_space: int = 1,
    block_type: int = builder.BLOCK_NONE,
) -> EncodePlan:
    """**主驱动**：PNG ＋ 一个真包（取类型树）-> 一张流式 Texture2D 小包。

    * `out=None` ⇒ **干跑**：编码 ＋ 造包（内存里），一个字节都不写盘；
    * `out=路径` ⇒ 写盘，然后**自己再开一遍出包**读回六个读数（`verify_encoded_out`），
      任何一条对不上 ⇒ `problems` 非空（CLI 会以 rc=2 退出，⛔ 不静默算绿）。
    """
    src = Path(source)
    meta = read_source_meta(src)
    enc = encode_png(png, format_id=format_id, mip_count=mip_count)
    asset = str(asset_path or "").strip()
    if not asset:
        raise AssetError("--asset-path 是空的：容器路径 = catalog 的 internalId（Assets/...）")
    bname = str(bundle_name or f"{Path(str(asset)).stem}_{int(enc.format)}.bundle")
    tex_name = str(name or Path(str(asset)).stem or "L5_Texture")
    plan = build_encoded_bundle(
        encoded=enc,
        asset_path=asset,
        source_meta=meta,
        bundle_name=bname,
        name=tex_name,
        color_space=int(color_space),
        block_type=int(block_type),
    )
    data = bytes(plan.data)
    out_path = Path(out) if out is not None else None
    if out_path is None:
        return EncodePlan(
            source=src,
            asset_path=asset,
            bundle_name=bname,
            name=tex_name,
            encoded=enc,
            path_id=int(plan.container[0][1]),
            bundle_bytes=len(data),
            stream_node_size=int(plan.stream_node_size),
            data=data,
        )
    if out_path.exists():
        raise AssetError(
            f"落点已经存在：{out_path}",
            hint="⛔ 不覆盖（旧的那份是证据）；换一个 --out 名字",
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    if out_path.stat().st_size != len(data):
        raise AssetError(
            f"写出来 {out_path.stat().st_size:,} B != 造出来 {len(data):,} B"
        )
    pid = int(plan.container[0][1])
    problems = verify_encoded_out(
        out_path,
        pid,
        encoded=enc,
        expect_stream_node_size=int(plan.stream_node_size),
    )
    from ba_mod_maker.asset_kernel import texture_install  # noqa: PLC0415 -- 尺子只有一份

    reading = texture_install.read_texture_back(out_path, pid)
    return EncodePlan(
        source=src,
        asset_path=asset,
        bundle_name=bname,
        name=tex_name,
        encoded=enc,
        path_id=pid,
        out=out_path,
        bundle_bytes=len(data),
        stream_node_size=int(plan.stream_node_size),
        data=data,
        reading=reading,
        problems=tuple(problems),
    )


# --------------------------------------------------------------------------- #
# 接进**既有资产链**：把真件包里的一张真贴图按 BC7 重编（改包；⛔ 一个字节不写游戏目录）
# --------------------------------------------------------------------------- #
#: 逐级解码器（**独立实现** texture2ddecoder；crunched 走 unpack_unity_crunch）。
#: 值 = (texture2ddecoder 的函数名, 要不要先 crn 解包)。
SOURCE_DECODERS: dict[int, tuple[str, bool]] = {
    DXT1: ("decode_bc1", False),
    DXT5: ("decode_bc3", False),
    BC7: ("decode_bc7", False),
    BC4: ("decode_bc4", False),
    28: ("decode_bc1", True),   # DXT1Crunched
    29: ("decode_bc3", True),   # DXT5Crunched
}

#: 无压缩格式 -> 每像素字节数（真件里还有 RGBA32 那一档：unitmodifications 123/163 张）。
UNCOMPRESSED_BPP: dict[int, int] = {1: 1, 3: 3, 4: 4, 5: 4, 62: 2, 63: 1}


def level_byte_sizes(width: int, height: int, format_id: int, mip_count: int) -> tuple[int, ...]:
    """整条链逐级的字节数（块压缩按块算，无压缩按像素算）。"""
    fmt = int(format_id)
    block = textures.BLOCK_BYTES.get(fmt)
    dims = textures.mip_dims(int(width), int(height), int(mip_count))
    if block is not None:
        return tuple(int(textures.mip_bytes(w, h, block)) for w, h in dims)
    bpp = UNCOMPRESSED_BPP.get(fmt)
    if bpp is not None:
        return tuple(int(w) * int(h) * bpp for w, h in dims)
    if fmt in CRUNCHED_FORMATS:
        raise AssetError(
            f"格式 {fmt}（{format_name(fmt)}）的逐级长度算不出来 —— crn 是压缩流，"
            "长度不由宽高决定（本作实测：2048² mip12 的 crn 是 871,011 B，未压缩总量 5,592,432 B）",
            hint="把整段字节按**一张**图交给 unpack_unity_crunch 解，别按级切",
        )
    raise AssetError(f"不认的格式 {fmt}（{format_name(fmt)}）—— 算不出逐级长度")


def _rgba_of_uncompressed(level: bytes, width: int, height: int, format_id: int) -> bytes:
    """无压缩的一级 -> RGBA（真件里 RGBA32 / RGB24 / Alpha8 / R8 都有）。"""
    fmt = int(format_id)
    count = int(width) * int(height)
    if fmt in (4, 5):
        return bytes(level)                       # RGBA32 / ARGB32（真件 4 是 RGBA 序）
    if fmt == 3:                                  # RGB24 -> RGBA
        out = bytearray(count * 4)
        out[0::4] = level[0::3]
        out[1::4] = level[1::3]
        out[2::4] = level[2::3]
        out[3::4] = b"\xff" * count
        return bytes(out)
    if fmt in (1, 63):                            # 单通道 -> R=G=B=灰
        out = bytearray(count * 4)
        out[0::4] = level
        out[1::4] = level
        out[2::4] = level
        out[3::4] = b"\xff" * count
        return bytes(out)
    raise AssetError(f"格式 {fmt}（{format_name(fmt)}）本模块不认（无压缩那几张只认 1/3/4/5/63）")


def decode_levels(
    blocks: bytes, width: int, height: int, format_id: int, mip_count: int
) -> list[bytes]:
    """整段纹素 -> **逐级 RGBA**（自下而上，Unity 行序）。

    ⛔ 一律走 `texture2ddecoder`（独立实现）—— `etcpak.decompress_bc7` 会崩进程
    （模块头 landmine 1）。
    crunched（28/29）先过 `unpack_unity_crunch`（crn 的解码器只有它这一份）。
    """
    fmt = int(format_id)
    dims = textures.mip_dims(int(width), int(height), int(mip_count))
    out: list[bytes] = []
    if fmt in CRUNCHED_FORMATS:
        import texture2ddecoder  # noqa: PLC0415 -- 只在真解码时拉起来

        rgba = bytes(texture2ddecoder.unpack_unity_crunch(bytes(blocks), int(width), int(height)))
        want = int(width) * int(height) * 4
        if len(rgba) != want:
            raise AssetError(
                f"crunched 解回来 {len(rgba):,} B != {int(width)}x{int(height)}x4 = {want:,} B"
            )
        return [rgba]
    sizes = level_byte_sizes(int(width), int(height), fmt, int(mip_count))
    if sum(sizes) != len(bytes(blocks)):
        raise AssetError(
            f"纹素长度 {len(bytes(blocks)):,} B != 逐级和 {sum(sizes):,} B"
            f"（{format_name(fmt)} {int(width)}x{int(height)} mip {int(mip_count)}）"
        )
    cursor = 0
    for (lw, lh), size in zip(dims, sizes, strict=True):
        level = bytes(blocks)[cursor : cursor + size]
        cursor += size
        if fmt in UNCOMPRESSED_BPP:
            out.append(_rgba_of_uncompressed(level, lw, lh, fmt))
            continue
        name = DECODER_NAMES.get(fmt)
        if name is None:
            raise AssetError(f"格式 {fmt}（{format_name(fmt)}）本模块解不回来")
        out.append(decode_with(name, level, lw, lh, to_rgba=True))
    return out


def encode_levels(
    levels: list[bytes], width: int, height: int, format_id: int = BC7
) -> bytes:
    """逐级 RGBA -> 逐级块字节（**一级一级编，⛔ 不重新下采样** —— 保住源件的 mip 链）。

    ⚠ 「不重新下采样」是刻意的：源件的 mip 链是 Unity 烘出来的，我们只换编码器，
    所以每一级都拿**源件那一级的像素**去编 —— 级数、每级尺寸、末级内容都与源件一一对应。
    """
    fmt = encodable_format(format_id)
    dims = textures.mip_dims(int(width), int(height), len(levels))
    out = bytearray()
    for (lw, lh), level in zip(dims, levels, strict=True):
        out += textures.encode_dxt(bytes(level), lw, lh, fmt, 1)
    want = sum(level_sizes(int(width), int(height), fmt, len(levels)))
    if len(out) != want:
        raise AssetError(
            f"重编出来的长度 {len(out):,} B != 逐级和 {want:,} B（{format_name(fmt)}）"
        )
    return bytes(out)


@dataclass(frozen=True, slots=True)
class ReencodePlan:
    """一次「真件包里的一张真贴图 -> 按 BC7 重编」的读数（改包；源包只读）。"""

    source: Path
    path_id: int
    name: str
    width: int
    height: int
    mip_count: int
    format_before: int
    format_after: int
    stream_before: int = 0
    stream_after: int = 0
    bundle_name: str = ""
    out: Path | None = None
    payload_before: bytes = field(repr=False, default=b"")
    payload_after: bytes = field(repr=False, default=b"")
    data: bytes = field(repr=False, default=b"")
    problems: tuple[str, ...] = ()
    others_checked: int = 0
    others_changed: tuple[str, ...] = ()
    delta: int = 0
    delta_ranges: tuple[tuple[int, int], ...] = ()
    delta_bytes: int = 0
    patch: Path | None = None
    patch_bytes: int = 0
    #: 产品那两条写后复核（F2/F3/G2/G3/F4/F5/F7/F5b）—— 两条路各一条，红一条就 overall 红。
    verify: tuple[tuple[str, bool, str], ...] = ()
    #: 两次外科手术的计划读数：{step, level, hit_blocks, size}。
    plans: tuple[dict[str, Any], ...] = ()
    source_sha256: str = ""
    out_sha256: str = ""
    catalog_crc_before: int | None = None
    catalog_crc_after: int | None = None
    catalog_size_before: int | None = None
    catalog_size_after: int | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        rows = [
            f"[贴图重编] {self.name}  {self.width}x{self.height} · mip {self.mip_count}"
            f"  格式      : {format_name(self.format_before)}（fmt {self.format_before}）"
            f" -> **{format_name(self.format_after)}（fmt {self.format_after}）**",
            f"  逐级载荷  : {self.stream_before:,} B -> {self.stream_after:,} B"
            f"（差 {self.delta:+,} B）· ρ {self.payload_after_bytes} B",
            f"  包内名    : {self.bundle_name} · 产物 {self.bundle_bytes:,} B",
            f"  其余贴图  : {self.others_checked} 张逐字节**没变**"
            if not self.others_changed
            else (
                f"  其余贴图  : ★ {len(self.others_changed)} 张跟着变了"
                f" {list(self.others_changed)[:4]}"
            ),
        ]
        if self.out is not None:
            rows.append(f"  改包落点  : {self.out}")
        for row in self.plans:
            rows.append(
                f"  外科手术  : {row['step']:<8} {row['level']} · 命中块 {row['hit_blocks']}"
                f" · {row['size'][0]:,} -> {row['size'][1]:,} B"
            )
        if self.patch is not None:
            rows.append(
                f"  反向 Δ    : {self.patch}（{self.patch_bytes:,} B"
                f" / 整包 {len(self.data):,} B = {self.patch_bytes / max(1, len(self.data)):.2%}）"
            )
        if self.catalog_crc_after is not None:
            rows.append(
                f"  catalog   : crc {self.catalog_crc_before} -> **{self.catalog_crc_after}**"
                f" · size {self.catalog_size_before:,} -> {self.catalog_size_after:,} B"
            )
        for key, ok, _detail in self.verify:
            if not ok:
                rows.append(f"      X 产品复核红：{key}")
        rows.append(
            "  出包复核  : "
            + (
                "**绿**（该变的变了 · 其余逐字节没变）"
                if self.ok
                else f"**红 {len(self.problems)} 条**"
            )
        )
        rows.extend(f"      X {p}" for p in self.problems)
        return tuple(rows)

    @property
    def payload_after_bytes(self) -> int:
        return len(self.payload_after)

    def document(self) -> dict[str, Any]:
        import hashlib  # noqa: PLC0415 -- 只在出 JSON 时算一次

        return {
            "action": "reencode",
            "source": str(self.source),
            "out": str(self.out) if self.out is not None else None,
            "path_id": int(self.path_id),
            "name": self.name,
            "width": int(self.width),
            "height": int(self.height),
            "mip_count": int(self.mip_count),
            "format_before": int(self.format_before),
            "format_after": int(self.format_after),
            "stream_before": int(self.stream_before),
            "stream_after": int(self.stream_after),
            "payload_after_sha256": hashlib.sha256(self.payload_after).hexdigest(),
            "bundle_name": self.bundle_name,
            "bundle_bytes": int(self.bundle_bytes),
            "others_checked": int(self.others_checked),
            "others_changed": list(self.others_changed),
            "delta_ranges": [list(r) for r in self.delta_ranges],
            "delta_bytes": int(self.delta_bytes),
            "patch_bytes": int(self.patch_bytes),
            "plans": list(self.plans),
            "source_sha256": self.source_sha256,
            "out_sha256": self.out_sha256,
            "catalog_crc_before": self.catalog_crc_before,
            "catalog_crc_after": self.catalog_crc_after,
            "catalog_size_before": self.catalog_size_before,
            "catalog_size_after": self.catalog_size_after,
            "verify": [{"key": k, "ok": bool(o), "detail": d} for k, o, d in self.verify],
            "patch": str(self.patch) if self.patch is not None else None,
            "problems": list(self.problems),
        }

    @property
    def bundle_bytes(self) -> int:
        return len(self.data)


def byte_delta(old: bytes, new: bytes, *, block: int = 4096) -> list[tuple[int, int]]:
    """两个字节串的差异区段 `[(offset, length)]`（相邻块合并；长度不同时尾巴整段算进去）。

    ⚠ 反向 Δ 的**唯一**口径：只存这些区段的**原始字节**，⛔ 不拷整包当备份（AGENTS §1）。
    """
    left, right = bytes(old), bytes(new)
    shared = min(len(left), len(right))
    ranges: list[list[int]] = []
    for start in range(0, shared, int(block)):
        end = min(shared, start + int(block))
        if left[start:end] == right[start:end]:
            continue
        if ranges and ranges[-1][0] + ranges[-1][1] >= start:
            ranges[-1][1] = end - ranges[-1][0]
        else:
            ranges.append([start, end - start])
    if len(left) != len(right):
        span = max(len(left), len(right)) - shared
        if ranges and ranges[-1][0] + ranges[-1][1] >= shared:
            ranges[-1][1] = max(len(left), len(right)) - ranges[-1][0]
        else:
            ranges.append([shared, span])
    return [(int(a), int(b)) for a, b in ranges]


def restore_from_delta(target: str | Path, patch: str | Path, out: str | Path) -> bytes:
    """把反向 Δ 的原始字节写回 `target` -> `out`（**逐字节回到源包**）。

    判据：`sha256(out) == patch["sha256_source"]`。⛔ 目标长度与 `sha256_target` 对不上就拒。
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415 -- 只在读柜子条目时用一次

    tgt, pj = Path(target), Path(patch)
    meta = json.loads(pj.read_text(encoding="utf-8"))
    blob = pj.with_name(str(meta["blob"])).read_bytes()
    data = bytearray(tgt.read_bytes())
    if hashlib.sha256(data).hexdigest() != str(meta["sha256_target"]):
        raise AssetError(
            "要撤的那份包与柜子条目里的 sha256 对不上 —— 它已经被别的改动动过了"
        )
    for row in meta["ranges"]:
        offset, length, blob_at = int(row["offset"]), int(row["length"]), int(row["blob_at"])
        piece = blob[blob_at : blob_at + length]
        if len(piece) != length:
            raise AssetError(f"柜子条目里的区段 {offset} 长度不够：{len(piece)} != {length}")
        if offset + length > len(data):
            raise AssetError(f"区段 [{offset}, {offset + length}) 超出目标文件 {len(data):,} B")
        data[offset : offset + length] = piece
    got = bytes(data)
    same_len = len(got) == int(meta["length_source"])
    same_sha = hashlib.sha256(got).hexdigest() == str(meta["sha256_source"])
    if not same_len or not same_sha:
        raise AssetError("撤回来的字节与源包 sha256 对不上 —— 反向 Δ 不完整")
    dst = Path(out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(got)
    return got


def _source_slices(reader: Any) -> dict[int, tuple[int, int]]:
    """`{pathID: (offset, size)}` —— 包里所有**流式**贴图的落点。"""
    out: dict[int, tuple[int, int]] = {}
    for raw in reader.ids_of_class(builder.CLASS_TEXTURE2D):
        tree = reader.fields_of(int(raw))
        stream = dict(tree.get("m_StreamData") or {})
        size = int(stream.get("size", 0) or 0)
        if size > 0:
            out[int(raw)] = (int(stream.get("offset", 0) or 0), size)
    return out


def verify_reencode(
    new_bundle: str | Path,
    source: str | Path,
    *,
    path_id: int,
    payload_after: bytes,
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    """**写后复核**（「该变的变了 · 其余逐字节没变」）：

    * 靶子那张：`m_TextureFormat` == BC7、逐级长度合公式、`.resS` 里那一段 == 我们那份载荷；
    * **其余每一张流式贴图**：它那一段字节与**源包**里那一段**逐字节相同**（⛔ 一张也不许跟着动）。

    :returns: (查了几张, 问题, 跟着变了的贴图名)。
    """
    from ba_mod_maker.asset_kernel import texture_install  # noqa: PLC0415 -- 读容器的唯一一份

    problems: list[str] = []
    changed: list[str] = []
    checked = 0

    with (
        texture_install._Reader(source) as old_rd,  # noqa: SLF001
        texture_install._Reader(new_bundle) as new_rd,  # noqa: SLF001
    ):
        old_slices = _source_slices(old_rd)
        new_slices = _source_slices(new_rd)
        victim = int(path_id)
        if victim not in new_slices:
            problems.append(f"新包里没有 pathID={victim} 的流式贴图")
        else:
            tree = new_rd.fields_of(victim)
            if int(tree["m_TextureFormat"]) != BC7:
                problems.append(
                    f"新包里靶子的 m_TextureFormat = {int(tree['m_TextureFormat'])} != 25（BC7）"
                )
            want = int(tree["m_CompleteImageSize"])
            if want != len(payload_after):
                problems.append(f"m_CompleteImageSize {want:,} != 载荷 {len(payload_after):,} B")
            off, size = new_slices[victim]
            if size != len(payload_after):
                problems.append(f"m_StreamData.size {size:,} != 载荷 {len(payload_after):,} B")
            if new_rd.stream_bytes(off, size) != bytes(payload_after):
                problems.append(".resS 里靶子那一段与我们的载荷不同")
        for pid, (off, size) in sorted(old_slices.items()):
            if pid == victim:
                continue
            if pid not in new_slices:
                changed.append(f"{pid}(没了)")
                continue
            checked += 1
            noff, nsize = new_slices[pid]
            if nsize != size:
                changed.append(f"{pid}(长度 {size:,}->{nsize:,})")
                continue
            if old_rd.stream_bytes(off, size) != new_rd.stream_bytes(noff, nsize):
                name = str(new_rd.fields_of(pid).get("m_Name") or pid)
                changed.append(name)
    if changed:
        problems.append(f"★ 有 {len(changed)} 张**不该动**的贴图跟着变了：{changed[:6]}")
    if checked != len(old_slices) - 1:
        problems.append(f"复核覆盖面不全：查了 {checked} 张，应当 {len(old_slices) - 1} 张")
    return checked, tuple(problems), tuple(changed)


def write_revert_patch(
    source: str | Path,
    out: str | Path,
    patch: str | Path,
    *,
    max_ratio: float = 0.35,
) -> tuple[tuple[tuple[int, int], ...], int]:
    """把 `source -> out` 的差异写成**反向 Δ 柜子条目**（只存差异区段的**原始**字节）。

    两个文件：`<patch>`（JSON 清单）＋ 同目录的 `<patch>.bin`（原始字节）。
    ⛔ 不拷整包当备份（AGENTS §1 的口径）。
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415 -- 只在落柜子条目时用一次

    src, dst, pj = Path(source), Path(out), Path(patch)
    old = src.read_bytes()
    new = dst.read_bytes()
    # 粗块先看：超过阈值就**换细块再量一遍**（小文件上 4096 的块会把整份都算进去），
    # 三档都超过才拒 —— 这样守卫只打「真的不是外科手术」，不打小文件。
    ranges = byte_delta(old, new)
    covered = sum(length for _offset, length in ranges)
    for fine in (256, 16):
        if not new or covered / len(new) <= float(max_ratio):
            break
        ranges = byte_delta(old, new, block=fine)
        covered = sum(length for _offset, length in ranges)
    if new and covered / len(new) > float(max_ratio):
        raise AssetError(
            f"这条改法不是外科手术：差异 {covered:,} B / 整包 {len(new):,} B"
            f" = {covered / len(new):.2%} > {float(max_ratio):.0%}"
            " —— 这种「Δ」等于拷整包当备份，⛔ AGENTS §1 不许",
            hint="整包重写的改法请用产品自己的 reverse_delta.PatchStore（按块记，不是按位置比）；"
                 "或者改成 writer.write_objects / write_stream 那条外科链",
        )
    blob = bytearray()
    rows: list[dict[str, int]] = []
    for offset, length in ranges:
        rows.append({"offset": int(offset), "length": int(length), "blob_at": len(blob)})
        blob += old[offset : offset + length]
    blob_name = pj.name + ".bin"
    meta = {
        "what": "L5 反向 Δ：把改包撤回到源包（只含差异区段的原始字节）",
        "source": str(src),
        "target": str(dst),
        "length_source": len(old),
        "length_target": len(new),
        "sha256_source": hashlib.sha256(old).hexdigest(),
        "sha256_target": hashlib.sha256(new).hexdigest(),
        "blob": blob_name,
        "ranges": rows,
        "how": "ba-mod-maker texture revert --patch <本条.json> --target <改后的包> --out <还原包>",
    }
    pj.parent.mkdir(parents=True, exist_ok=True)
    pj.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pj.with_name(blob_name).write_bytes(bytes(blob))
    return tuple(ranges), len(blob)


def game_root_of() -> Path | None:
    """游戏根（`core.assets.game.find_game_root()`）—— **只读判断**用，⛔ 不往它写任何东西。"""
    from ba_mod_maker.core.assets.game import find_game_root  # noqa: PLC0415 -- 定位只有一份

    root = find_game_root()
    return Path(root) if root is not None else None


def inside_game_dir(path: str | Path) -> tuple[bool, str]:
    """`path` 落在游戏根之内吗？（**唯一的判定**，`build` / `reencode` / `apply` 共用）

    口径（AGENTS §1「装机归 newasset add-model」，产品只写工程目录）：
    `resolve()` 之后做**相对路径**判断，⛔ 不靠字符串前缀（大小写 / `..` / 符号链接都糊不住）。
    """
    root = game_root_of()
    if root is None:
        return False, ""
    try:
        target = Path(path).resolve()
        target.relative_to(root.resolve())
    except ValueError:
        return False, str(root)
    return True, str(root)


def guard_out_path(path: str | Path, *, allow_game_dir: bool = False) -> None:
    """⛔ **装机红线**：`--out` 落在游戏根之内一律拒（rc=2），除非显式 `--i-know-game-dir`。

    「少犯错」那一类：本产品所有写盘动作都只写工程目录，装机归 `newasset add-model`。
    """
    hit, root = inside_game_dir(path)
    if hit and not allow_game_dir:
        raise AssetError(
            f"--out 落在**游戏目录**里：{Path(path).resolve()}",
            hint=f"（游戏根 {root}）⛔ 本产品只写工程目录 —— 装机归 `newasset add-model`；"
                 "**确实**要往游戏目录写，加 `--i-know-game-dir`（默认不给）",
        )


def reencode_in_bundle(
    bundle: str | Path,
    *,
    path_id: int,
    out: str | Path | None = None,
    format_id: int = BC7,
    project_root: str | Path | None = None,
    allow_game_dir: bool = False,
) -> ReencodePlan:
    """**接进既有资产链**：把真件包里的一张真贴图按 `format_id` 重编（默认 BC7）—— 等长外科手术。

    ## 走的是产品自己那条外科链（⛔ 不是整包重写）

    1. `writer.write_objects`：改 Texture2D 对象那 208 B（`m_TextureFormat` 12 -> 25），**等长**；
    2. `writer.write_stream`：换 `.resS` 里那一段纹素（DXT5 -> BC7 都是 16 B/块 ⇒ 长度不变）；
    3. 两次都带 `reverse_delta.PatchStore` ⇒ **反向 Δ 由产品柜子给出**（实测 73,324 B）。

    ⚠ 2026-10-17 改口径（**实测推翻第一版**）：第一版用 `builder.build_object_bundle`
    **整包重写**，结果是 —— ① 容器头写成 `2022.3.62f3` 而游戏真件是 `6000.3.24f1`（Unity 6）；
    ② 文件级反向 Δ 高达 **99.82%**（等于拷整包，AGENTS §1 明令不许）。
    外科路两样都治好了：头部/块表/其余块**逐字节照抄**，只重压命中的几个块。

    ⛔ 源包**只读**（`out` 是它的副本）；`out` 落在游戏目录里一律拒（见 `guard_out_path`）。
    不给 `out` = **干跑**（两条计划都算一遍，一个字节都不写）。
    """
    from ba_mod_maker.asset_kernel import reverse_delta, texture_install, writer  # noqa: PLC0415
    from ba_mod_maker.core.asset import new_asset, ufs  # noqa: PLC0415

    src = Path(bundle)
    want = encodable_format(format_id)
    reader = texture_install._Reader(src)  # noqa: SLF001 -- 读容器只有这一份
    try:
        pid = int(path_id)
        if pid not in reader.by_pid:
            raise AssetError(
                f"{src.name} 里没有 pathID={pid}",
                hint="列一遍流式贴图（texture survey）或看 --json",
            )
        cls = int(reader.class_of(pid))
        if cls != builder.CLASS_TEXTURE2D:
            raise AssetError(f"pathID={pid} 不是 Texture2D（classID {cls}）")
        tree = dict(reader.fields_of(pid))
        name = str(tree.get("m_Name") or "")
        width, height = int(tree["m_Width"]), int(tree["m_Height"])
        mips = int(tree["m_MipCount"])
        before_fmt = int(tree["m_TextureFormat"])
        stream = dict(tree.get("m_StreamData") or {})
        offset, size = int(stream.get("offset", 0) or 0), int(stream.get("size", 0) or 0)
        if size <= 0:
            raise AssetError(f"{name} 不在 .resS 里（内联的）—— 本入口只做流式贴图")
        known = set(SOURCE_DECODERS) | set(UNCOMPRESSED_BPP)
        if before_fmt not in known:
            raise AssetError(
                f"源格式 {before_fmt}（{format_name(before_fmt)}）本模块解不回来："
                "只认 " + "、".join(f"{k}={format_name(k)}" for k in sorted(known))
            )
        before = reader.stream_bytes(offset, size)
        levels = decode_levels(before, width, height, before_fmt, mips)
        after = encode_levels(levels, width, height, want)
        new_tree = dict(tree)
        new_tree["m_TextureFormat"] = want
        old_obj = reader.raw_of(pid)
        new_obj = reader.encode(pid, new_tree)
    finally:
        reader.close()
    if len(after) != len(before):
        raise AssetError(
            f"这条外科路**只做等长**：新纹素 {len(after):,} B != 原 {size:,} B"
            f"（{format_name(before_fmt)} -> {format_name(want)}）",
            hint="块字节数不同的两档（例 RGBA32 -> BC7）会变长 —— 那要走上限更高的 Δ 路，"
                 "本入口先不接（⛔ 不偷偷按 padding 凑长度）",
        )
    if len(new_obj) != len(old_obj):
        raise AssetError(f"对象字节不等长：{len(new_obj)} != {len(old_obj)} —— 外科路不许")
    catalog_before = new_asset.bundle_facts_of_file(src)[0]
    out_path = Path(out) if out is not None else None
    if out_path is not None:
        guard_out_path(out_path, allow_game_dir=allow_game_dir)
    project = Path(project_root) if project_root is not None else None
    store = reverse_delta.PatchStore(
        ((project or out_path.parent) / "patches") if (project or out_path) else Path("patches")
    )
    plans: list[dict[str, Any]] = []
    if out_path is None:
        # ---- 干跑：两条计划都算一遍，⛔ 一个字节都不写 ----
        r1 = writer.write_objects(
            src, [writer.ObjectChange(path_id=pid, payload=new_obj, name=name,
                                      type_name="Texture2D")],
            dry_run=True, project_root=project, patch_store=store, patch_label="l5-obj-fmt",
        )
        with ufs.UfsReader(src) as raw:
            change = writer.build_stream_change(raw, pid, after)
        r2 = writer.write_stream(src, [change], dry_run=True, project_root=project,
                                 patch_store=store, patch_label="l5-tex-bc7")
        plans = [{"step": "objects", "level": r1.plan.rows[0].level,
                  "hit_blocks": r1.plan.hit_blocks,
                  "size": [r1.plan.old_file_size, r1.plan.new_file_size]},
                 {"step": "stream", "level": r2.plan.rows[0].level,
                  "hit_blocks": r2.plan.hit_blocks,
                  "size": [r2.plan.old_file_size, r2.plan.new_file_size]}]
        return ReencodePlan(
            source=src, path_id=pid, name=name, width=width, height=height, mip_count=mips,
            format_before=before_fmt, format_after=want, stream_before=size,
            stream_after=len(after), bundle_name=str(catalog_before["internal_name"]),
            payload_before=before, payload_after=after, delta=len(after) - size,
            catalog_crc_before=int(catalog_before["crc"]),
            catalog_size_before=int(catalog_before["bundle_size"]),
            plans=tuple(plans),
        )
    # ---- 真写：源包 -> 副本，两步外科手术都落在这个副本上 ----
    if out_path.exists():
        raise AssetError(f"落点已经存在：{out_path}", hint="⛔ 不覆盖；换一个 --out 名字")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out_path)
    r1 = writer.write_objects(
        out_path, [writer.ObjectChange(path_id=pid, payload=new_obj, name=name,
                                       type_name="Texture2D")],
        dry_run=False, project_root=project, patch_store=store, patch_label="l5-obj-fmt",
    )
    with ufs.UfsReader(out_path) as raw:
        change = writer.build_stream_change(raw, pid, after)
    r2 = writer.write_stream(out_path, [change], dry_run=False, project_root=project,
                             patch_store=store, patch_label="l5-tex-bc7")
    checked, problems, changed = verify_reencode(out_path, src, path_id=pid,
                                                payload_after=after)
    verify: list[tuple[str, bool, str]] = []
    for step, report in (("objects", r1), ("stream", r2)):
        for key, row in dict(report.verify).items():
            verify.append((f"{step} · {key}", bool(row.get("ok")), str(row.get("detail"))))
    for key, ok, detail in verify:
        if not ok:
            problems = (*problems, f"产品写后复核红了：{key} —— {detail}")
    data = out_path.read_bytes()
    after_facts = new_asset.bundle_facts_of_file(out_path)[0]
    plans = [{"step": "objects", "level": r1.plan.rows[0].level,
              "hit_blocks": r1.plan.hit_blocks,
              "size": [r1.plan.old_file_size, r1.plan.new_file_size]},
             {"step": "stream", "level": r2.plan.rows[0].level,
              "hit_blocks": r2.plan.hit_blocks,
              "size": [r2.plan.old_file_size, r2.plan.new_file_size]}]
    patch_bytes = 0
    patch_path = None
    for report in (r1, r2):
        got = getattr(report, "patch", None)
        if got:
            patch_path = Path(got)
    if patch_path is not None and patch_path.is_file():
        # 柜子 = JSON 清单 ＋ 同名的 .bin（**只存被改块的原始字节**）—— 两件一起算。
        patch_bytes = patch_path.stat().st_size
        for sibling in patch_path.parent.glob(patch_path.stem + ".bin"):
            patch_bytes += sibling.stat().st_size
    return ReencodePlan(
        source=src, path_id=pid, name=name, width=width, height=height, mip_count=mips,
        format_before=before_fmt, format_after=want, stream_before=size,
        stream_after=len(after), bundle_name=str(catalog_before["internal_name"]),
        payload_before=before, payload_after=after, data=data, delta=len(after) - size,
        out=out_path, problems=tuple(problems), others_checked=checked,
        others_changed=changed, verify=tuple(verify), plans=tuple(plans),
        patch=patch_path, patch_bytes=patch_bytes,
        source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
        out_sha256=hashlib.sha256(data).hexdigest(),
        catalog_crc_before=int(catalog_before["crc"]),
        catalog_size_before=int(catalog_before["bundle_size"]),
        catalog_crc_after=int(after_facts["crc"]),
        catalog_size_after=int(after_facts["bundle_size"]),
    )
