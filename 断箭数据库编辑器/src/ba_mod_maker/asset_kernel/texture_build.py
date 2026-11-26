r"""路线 A 的**自造内联 Texture2D**（写侧，纯函数，零宿主绑定）。

## 这条路解决什么

真件 units 包的 2,725 个 Texture2D **全是流式**（实测抽样 400/400：m_ImageData 空、
m_StreamData.size > 0，像素在 .resS 里）。

本模块给两条路，**同一个 24 键字段树**（键名只从包内类型树来）：

| 函数 | 像素放哪 | 用在哪 |
|---|---|---|
| `build_texture_fields` | 内联 `image data` | 单节点包（build_object_bundle 默认形） |
| `build_streaming_texture_fields` | `.resS`，`image data` **空** | **kb/23 §2 规格**（真件 100%） |

⚠ **2026-10-10 更正**：本节原来写「自造贴图**只能**走内联」——那条只在 builder 只造
**单节点**包时成立。`builder.build_unityfs(stream_payload=…)` 现在能造
**双节点**（`CAB-<hash>` + `CAB-<hash>.resS`，与真件同形：node[1].offset == node[0].size、
flags=0，实测 lightflashes / unitmodifications / ammoprefabs 三包都是这个形状），
所以流式那条路已经通了 —— 规格要求的正是它。内联那条仍然有效，只是**偏离真件形状**
（真件 0 例内联），交接里如实记账。

## 判据（每条都有正控 + 负控）

1. `clone.write_object(build_texture_fields(...), nodes)` 能读回，且**逐字节往返相同**；
2. `len(image data) == m_CompleteImageSize == width × height × 每像素字节`；
3. `m_StreamData == {offset: 0, size: 0, path: ""}`（「内联」的定义）；
4. 负控：像素长度对不上 / 不是 bytes / 格式不认 / 宽高非法 —— 一律抛，⛔ 不猜不静默。

## 真件读数（2026-10-06 实测，units 包）

* 类型树 **24 个顶层键**（`TEXTURE_FIELDS`，一个不能少、不能改名）——
  字段名**只从包里读出来的类型树来**；注意 `image data` 这个名字**中间带一个空格**，
  那是 Unity 自己起的（不是笔误），写错就是「字段树缺 image data」直接抛；
* 抽样格式分布 = {29 DXT5Crunched: 243, 12 DXT5: 75, 28 DXT1Crunched: 65, 10 DXT1: 17}
  —— 真件**全是块压缩**。
  ⚠ **2026-10-07 更正**：这两行的格式名原来写反了（把 28/29 当成 BC7/ASTC_RGBA_4x4）——
  Unity 的 TextureFormat 里 **28 = DXT1Crunched、29 = DXT5Crunched**
  （BC7 = 25、ASTC_RGBA_4x4 = 48）。
  与 `asset_kernel/texture.py:51` / `textures.py:61` 和设计稿 §3.5 的分布表一致；
  产品自己的用例 `tests/test_writeback_delta.py:257` 也断言的 DXT5Crunched。
  ⇒ **2026-10-09 起**：块压缩（`_BLOCK_FORMATS` = 10/12/28/29）走「**原样搬运**」那条路 ——
  不查长度、不做块编解码，`m_CompleteImageSize` 直接取 `len(pixels)`。
  ⚠ 28/29 是 **crunched 压缩流**，长度根本不由宽高决定 ⇒ `bytes_per_pixel()` 对它们
  **仍然抛**（那条负控没松：块压缩没有「每像素几字节」这个数）。
  ⚠ 「原样搬运」只解决**搬运**；要**改像素**仍得先解成无压缩（本模块不做编解码）。
  实证：把游戏真件那张 750,645 B 的 DXT5Crunched 字节原样内联，游戏里与原版**逐字节一致**；
* `m_CompleteImageSize` 含全部 mip 层：给 `mip_levels=` 就造**全 mip 链**（链头必须与 pixels
  逐字节相同、逐级减半、末级必须 1×1），没给则单 mip（`m_MipCount = 1`，这时它 == 基础层字节数）；
* ⚠ **方向契约**：Unity 的 `image data` 是 **bottom-up**（`GetPixels32()` 的 index 0 = 左下角），
  PNG 是 **top-down** ⇒ 从 PNG 来的像素**必须** `flip_vertical=True`，否则整张图上下颠倒。
  实证：`a vs b = 38.477/255`、`a vs flipud(b) = 0.002/255` ⇒ 差别**只是翻转**（见 kb/21）。

## 复算

    .venv\Scripts\python.exe _work\v3a-tex-fixture.py     # 录真件类型树 + 4 个真件贴图（只读）
    pytest tests/test_build_texture_from_scratch.py -q
"""

from __future__ import annotations

from collections.abc import Sequence

#: 流式节点的后缀（真件实测：`CAB-<hash>.resS`，与 `core/asset/ufs.py` 的 STREAM_SUFFIXES 同口径）。
#: `m_StreamData.path` 的真件形状 = `archive:/CAB-<hash>/CAB-<hash>.resS`。
STREAM_SUFFIX = ".resS"

__all__ = [
    "FORMAT_NAMES",
    "INLINE_STREAM",
    "STREAM_SUFFIX",
    "TEXTURE_FIELDS",
    "TextureBuildError",
    "build_streaming_texture_fields",
    "build_texture_fields",
    "checker_pixels",
    "bytes_per_pixel",
]

#: 真件 classID 28 类型树的 **24 个顶层键**（顺序 = 序列化顺序，一个不能少、不能改名）。
TEXTURE_FIELDS: tuple[str, ...] = (
    "m_Name",
    "m_ForcedFallbackFormat",
    "m_DownscaleFallback",
    "m_IsAlphaChannelOptional",
    "m_Width",
    "m_Height",
    "m_CompleteImageSize",
    "m_MipsStripped",
    "m_TextureFormat",
    "m_MipCount",
    "m_IsReadable",
    "m_IsPreProcessed",
    "m_IgnoreMipmapLimit",
    "m_MipmapLimitGroupName",
    "m_StreamingMipmaps",
    "m_StreamingMipmapsPriority",
    "m_ImageCount",
    "m_TextureDimension",
    "m_TextureSettings",
    "m_LightmapFormat",
    "m_ColorSpace",
    "m_PlatformBlob",
    "image data",
    "m_StreamData",
)

#: 像素放在对象里时的 m_StreamData（三项全 0）。
INLINE_STREAM: dict[str, object] = {"offset": 0, "size": 0, "path": ""}

#: 认的**无压缩**格式 -> 每像素字节数（Unity TextureFormat 枚举值）。
#: 块压缩（10 DXT1 / 12 DXT5 / 28 BC7 / 29 ASTC …）**不在这里** ⇒ 抛。
_BPP: dict[int, int] = {
    1: 1,   # Alpha8
    3: 3,   # RGB24
    4: 4,   # RGBA32
    5: 4,   # ARGB32
    62: 2,  # RG16
    63: 1,  # R8
}

#: 认的**块压缩**格式（本模块对它们只做「**原样搬运**」：不校验长度、不做块编解码）。
#: ⚠ 与 _BPP 的关键区别：块压缩**没有**「每像素几字节」这个数，而且 28/29 是 **crunched**
#: （压缩流），长度根本不由宽高决定 —— 所以 bytes_per_pixel() 对它们**必须继续抛**（负控不许松）。
#: ★ 2026-10-17（L5）：补 25（BC7）/ 26（BC4）—— 它们是**块压缩**（16 B / 8 B 每块，
#: 见 textures.BLOCK_BYTES），之前漏在表外 ⇒ `build_texture_fields(texture_format=25)` 会当成
#: 「不认的格式」抛。真件里 dlc_2 有 **105 张 BC7**，那些同样只能原样搬运。
_BLOCK_FORMATS: frozenset[int] = frozenset({10, 12, 25, 26, 28, 29})

#: 格式码 -> 真名（报错信息里用，省得下一手去查枚举）。
#: ⚠ 2026-10-07 更正：28/29 原来是 `BC7` / `ASTC_RGBA_4x4` —— **错的是这一份**：
#: Unity 里 28 = DXT1Crunched、29 = DXT5Crunched（BC7 是 **25**、ASTC_RGBA_4x4 是 **48**）。
#: 同口径的另两份在 `texture.py:51` / `textures.py:61`（那两份是对的）；
#: ⛔ 别再把这张表抄成第二份 —— 真要复用就 import，别复制。
FORMAT_NAMES: dict[int, str] = {
    1: "Alpha8",
    3: "RGB24",
    4: "RGBA32",
    5: "ARGB32",
    10: "DXT1",
    12: "DXT5",
    25: "BC7",
    26: "BC4",
    28: "DXT1Crunched",
    29: "DXT5Crunched",
    48: "ASTC_RGBA_4x4",
    62: "RG16",
    63: "R8",
}


class TextureBuildError(ValueError):
    """自造贴图的入参不合法（**不是** AssetError：这是调用方给错了，不是资产层出错）。"""


def bytes_per_pixel(texture_format: int) -> int:
    """每像素**字节数**；认不出（含全部块压缩格式）就抛。

    ⚠ 2026-10-07 改名：原来叫 `pixels_per_byte`（**名字与返回值反了** —— 返回的是
    "每像素几字节"，不是"每字节几像素"）。调用点两处，都在本模块内。
    """
    bpp = _BPP.get(int(texture_format))
    if bpp is None:
        name = FORMAT_NAMES.get(int(texture_format), "?")
        if int(texture_format) in _BLOCK_FORMATS:
            # ⛔ 这条不许松：块压缩没有「每像素几字节」。要原样搬运这种格式请走
            #   build_texture_fields(texture_format=…) —— 它不查 bpp。
            raise TextureBuildError(
                f"{texture_format}（{name}）是**块压缩**，没有「每像素几字节」这个数："
                "DXT/BC 按 4×4 块对齐，28/29 还是 crunched（压缩流，长度不由宽高决定）。"
                "要原样搬运请用 build_texture_fields(texture_format=…) —— 它不查 bpp"
            )
        raise TextureBuildError(
            f"不认的纹理格式 {texture_format}（{name}）；本模块认**无压缩**格式："
            + "、".join(f"{k}={FORMAT_NAMES[k]}" for k in sorted(_BPP))
            + "；以及**块压缩**（原样搬运，不查长度）："
            + "、".join(f"{k}={FORMAT_NAMES[k]}" for k in sorted(_BLOCK_FORMATS))
        )
    return bpp


def _flip_rows(buf: bytes, width: int, height: int) -> bytes:
    """把 RGBA32 的行序上下翻转（**PNG 是 top-down，Unity 的 image data 是 bottom-up**）。

    方向契约的落点（见 kb/21）：

    判据一行（这条能红）：造一张「上红下蓝」的 4×4 ⇒ flip_vertical=True ⇒ 读回第 0 行必须是**蓝**。
    """
    stride = width * 4
    return b"".join(buf[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1))


def build_texture_fields(
    name: str,
    width: int,
    height: int,
    pixels: bytes,
    *,
    texture_format: int = 4,
    color_space: int = 1,
    filter_mode: int = 1,
    aniso: int = 1,
    wrap: int = 0,
    readable: bool = False,
    mipmap_limit_group: str = "",
    mip_levels: Sequence[bytes] | None = None,
    flip_vertical: bool = False,
) -> dict:
    """造一个**内联** Texture2D 的字段树（键 = 包内类型树，见 TEXTURE_FIELDS）。

    口径（⛔ 别自己发明）：
      * pixels 必须是 bytes；**无压缩**时长度 == width × height × 每像素字节，
        **块压缩**（_BLOCK_FORMATS）时**不查长度** —— 28/29 是 crunched 压缩流，长度不由宽高决定；
      * mip_levels 给了就造**全 mip 链**（含 level 0，且 **level 0 必须与 pixels 逐字节相同**——
        自证），逐级减半、末级必须 1×1；没给则单 mip（m_MipCount = 1、m_MipsStripped = 0）；
      * image data = 像素；m_StreamData = 三项全 0（这就是「内联」）；
      * m_CompleteImageSize = len(image data)（**含全部 mip 层**）；
      * flip_vertical=True 把 **RGBA32** 的每一级按行上下翻转 —— 这是 **PNG(top-down) ->
        Unity image data(bottom-up)** 的方向契约。只支持 RGBA32，别的格式抛。
    """
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise TextureBuildError(f"宽高必须 >= 1，拿到 {width}×{height}")
    if width > 16384 or height > 16384:
        raise TextureBuildError(
            f"宽高过大（{width}×{height}）—— 真件最大 4096，本模块给到 16384 就够"
        )
    if not isinstance(pixels, (bytes, bytearray)):
        raise TextureBuildError(
            f"pixels 必须是 bytes，拿到 {type(pixels).__name__}（TypelessData 那一格要裸字节）"
        )
    raw0 = bytes(pixels)
    blocked = int(texture_format) in _BLOCK_FORMATS
    levels: list[bytes] | None = None
    if mip_levels is None:
        if not blocked:
            bpp = bytes_per_pixel(texture_format)
            expected = width * height * bpp
            if len(raw0) != expected:
                raise TextureBuildError(
                    f"像素长度对不上：{len(raw0):,} B != {width}×{height}×{bpp} = {expected:,} B；"
                    f"格式 {texture_format}"
                    f"（{FORMAT_NAMES.get(texture_format, '?')}）每像素 {bpp} 字节，"
                    "单 mip 时不许带 mip 层数据（要带 mip 请给 mip_levels=）"
                )
    else:
        levels = [bytes(x) for x in mip_levels]
        if not levels:
            raise TextureBuildError("mip_levels 不能是空的（要给就得含 level 0）")
        if levels[0] != raw0:
            raise TextureBuildError(
                "mip_levels[0] 必须与 pixels 逐字节相同（自证：免得链头与 pixels 说的不是一张图）"
            )
        lw, lh = width, height
        for i in range(len(levels)):
            if i:
                lw, lh = max(1, lw // 2), max(1, lh // 2)
            if not blocked:
                want = lw * lh * bytes_per_pixel(texture_format)
                if len(levels[i]) != want:
                    raise TextureBuildError(
                        f"mip_levels[{i}] 长度对不上：{len(levels[i]):,} B"
                        f" != {lw}×{lh}×bpp = {want:,} B"
                    )
        if (lw, lh) != (1, 1):
            raise TextureBuildError(
                f"mip_levels 只到 {lw}×{lh}，没到 1×1（真件 2048² 是 12 级）"
            )

    # ---- 方向契约：PNG(top-down) -> Unity image data(bottom-up) ----
    if flip_vertical:
        if blocked:
            raise TextureBuildError(
                "flip_vertical 不支持块压缩（块序不是行序）—— 先解成 RGBA32 再翻"
            )
        flip_bpp = bytes_per_pixel(texture_format)
        if flip_bpp != 4:
            raise TextureBuildError(
                f"flip_vertical 只支持 RGBA32（每像素 4 字节），拿到 {flip_bpp}"
            )
        if levels is None:
            raw0 = _flip_rows(raw0, width, height)
        else:
            lw, lh = width, height
            flipped: list[bytes] = []
            for i, lv in enumerate(levels):
                if i:
                    lw, lh = max(1, lw // 2), max(1, lh // 2)
                flipped.append(_flip_rows(lv, lw, lh))
            levels = flipped

    if levels is None:
        raw = raw0
        mip_count = 1
    else:
        raw = b"".join(levels)
        mip_count = len(levels)
    return {
        "m_Name": str(name),
        "m_ForcedFallbackFormat": 4,
        "m_DownscaleFallback": False,
        "m_IsAlphaChannelOptional": False,
        "m_Width": width,
        "m_Height": height,
        "m_CompleteImageSize": len(raw),
        "m_MipsStripped": 0,
        "m_TextureFormat": int(texture_format),
        "m_MipCount": int(mip_count),
        "m_IsReadable": bool(readable),
        "m_IsPreProcessed": False,
        "m_IgnoreMipmapLimit": False,
        "m_MipmapLimitGroupName": str(mipmap_limit_group),
        "m_StreamingMipmaps": False,
        "m_StreamingMipmapsPriority": 0,
        "m_ImageCount": 1,
        "m_TextureDimension": 2,
        "m_TextureSettings": {
            "m_FilterMode": int(filter_mode),
            "m_Aniso": int(aniso),
            "m_MipBias": 0.0,
            "m_WrapU": int(wrap),
            "m_WrapV": int(wrap),
            "m_WrapW": int(wrap),
        },
        "m_LightmapFormat": 0,
        "m_ColorSpace": int(color_space),
        "m_PlatformBlob": [],
        "image data": raw,
        "m_StreamData": dict(INLINE_STREAM),
    }


def build_streaming_texture_fields(
    name: str,
    width: int,
    height: int,
    *,
    texture_format: int,
    mip_count: int,
    size: int,
    stream_path: str,
    offset: int = 0,
    complete_image_size: int | None = None,
    color_space: int = 1,
    filter_mode: int = 1,
    aniso: int = 1,
    wrap: int = 0,
    readable: bool = True,
    mipmap_limit_group: str = "",
) -> dict:
    r"""造一个**流式** Texture2D 的字段树（像素在 `.resS`，`image data` **空**）。

    ## 规格出处（⛔ 别自己定）

    kb/23 §2：本作 **100% 贴图走 .resS 流式**（2,725/2,725，`m_ImageData` 全空）、
    **满 mip 链**（2048² 配 12 级）、**Bilinear**（`m_FilterMode = 1`）。
    真件形状实测（`_work/p3tex/recon_real.py`，三个小真包）：
    `m_StreamData.path = archive:/CAB-<hash>/CAB-<hash>.resS`、`size == m_CompleteImageSize`。

    ## 五条不许松的口径（每条都有一个负控能红）

    1. `stream_path` 非空 —— 真件形如 `archive:/CAB-<hash>/CAB-<hash>.resS`；
       空路径 = 没有落点（游戏读不到像素）；
    2. `size > 0`、`offset >= 0`；
    3. **非 crunched 块压缩**（10 DXT1 / 12 DXT5）时
       `size == dxt_payload_size(width, height, texture_format, mip_count)`
       —— 这一条**就是「满 mip 链」的机械判据**：少烘一级，字节数就与
       (尺寸, 格式, 级数) 对不上；
    4. 无压缩格式（RGBA32 等）时 `size == width × height × bpp × mip 链`；
    5. `m_CompleteImageSize == size`（**非 Crunched 时它才是未压缩总量**）；
       crunched（28/29）实测存的是 crn 流字节数 ⇒ 那种格式要显式给
       `complete_image_size=`，本函数不替它猜。

    ⚠ **crunched（28/29）不给公式判据**：它是压缩流，长度不由宽高决定
    （模块头那两条实测：2048² mip12 的 crn 是 871,011 B，而未压缩总量是 5,592,432 B）。
    """
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise TextureBuildError(f"宽高必须 >= 1，拿到 {width}×{height}")
    path_in = str(stream_path or "")
    if not path_in:
        raise TextureBuildError(
            "流式贴图必须给 stream_path（真件形如 archive:/CAB-<hash>/CAB-<hash>.resS）"
            "—— 空路径 = 没有落点，游戏读不到像素"
        )
    size_i, offset_i, mips_i = int(size), int(offset), int(mip_count)
    if size_i <= 0:
        raise TextureBuildError(f"流式字节数必须是正数，拿到 {size_i}")
    if offset_i < 0:
        raise TextureBuildError(f"流式区间偏移不能是负数，拿到 {offset_i}")
    if mips_i < 1:
        raise TextureBuildError(f"mip 级数必须 >= 1，拿到 {mips_i}")
    fmt = int(texture_format)

    from ba_mod_maker.asset_kernel.textures import (  # noqa: PLC0415 -- 公式只有一份，不抄第二份
        BLOCK_BYTES,
        dxt_payload_size,
    )

    if fmt in BLOCK_BYTES:
        want = dxt_payload_size(width, height, fmt, mips_i)
        if size_i != want:
            raise TextureBuildError(
                f"流式字节数与 mip 链对不上：{size_i:,} B != "
                f"(格式 {fmt} {width}×{height} 满 {mips_i} 级) {want:,} B"
                f"（{FORMAT_NAMES.get(fmt, '?')}）—— 少烘一级 mip 就会命中这一条"
            )
    elif fmt in _BPP:
        bpp = bytes_per_pixel(fmt)
        want = sum(
            max(1, width >> i) * max(1, height >> i) * bpp for i in range(mips_i)
        )
        if size_i != want:
            raise TextureBuildError(
                f"流式字节数与 mip 链对不上：{size_i:,} B != "
                f"(格式 {fmt} {width}×{height} {mips_i} 级 × {bpp} B/px) {want:,} B"
            )
    elif fmt not in (28, 29):
        raise TextureBuildError(
            f"不认的纹理格式 {fmt}（{FORMAT_NAMES.get(fmt, '?')}）—— 算不出流式字节数"
        )
    complete = size_i if complete_image_size is None else int(complete_image_size)
    if complete != size_i:
        raise TextureBuildError(
            f"m_CompleteImageSize {complete:,} != m_StreamData.size {size_i:,}"
            "（非 Crunched 时两者必须相等；crunched 是压缩流，要显式给 complete_image_size=）"
        )
    return {
        "m_Name": str(name),
        "m_ForcedFallbackFormat": 4,
        "m_DownscaleFallback": False,
        "m_IsAlphaChannelOptional": False,
        "m_Width": width,
        "m_Height": height,
        "m_CompleteImageSize": complete,
        "m_MipsStripped": 0,
        "m_TextureFormat": fmt,
        "m_MipCount": mips_i,
        "m_IsReadable": bool(readable),
        "m_IsPreProcessed": False,
        "m_IgnoreMipmapLimit": False,
        "m_MipmapLimitGroupName": str(mipmap_limit_group),
        "m_StreamingMipmaps": False,
        "m_StreamingMipmapsPriority": 0,
        "m_ImageCount": 1,
        "m_TextureDimension": 2,
        "m_TextureSettings": {
            "m_FilterMode": int(filter_mode),
            "m_Aniso": int(aniso),
            "m_MipBias": 0.0,
            "m_WrapU": int(wrap),
            "m_WrapV": int(wrap),
            "m_WrapW": int(wrap),
        },
        "m_LightmapFormat": 0,
        "m_ColorSpace": int(color_space),
        "m_PlatformBlob": [],
        # ★ 这一格就是「流式」的定义：像素不在这里，在 .resS 里
        "image data": b"",
        "m_StreamData": {"offset": offset_i, "size": size_i, "path": path_in},
    }


def checker_pixels(
    width: int, height: int, colors: tuple[tuple[int, int, int], ...], *, block: int = 8
) -> bytes:
    """确定性**粗格纹**像素（RGBA32）：block×block 一块，按 (x+y)//block 选色，alpha=255。

    只为了「看得见、可复算」：同一组入参永远给同一串字节，⛔ 不引入随机。
    """
    if not colors:
        raise TextureBuildError("colors 不能是空的")
    if block < 1:
        raise TextureBuildError(f"block 必须 >= 1，拿到 {block}")
    out = bytearray()
    for y in range(int(height)):
        for x in range(int(width)):
            r, g, b = colors[((x + y) // block) % len(colors)]
            out += bytes((int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF, 255))
    return bytes(out)
