"""Texture2D 的读侧入口（元数据 + .resS 定位，零第三方依赖）。

## 为什么只出「元数据 + 原始字节」，不在这里解码

解码 DXT5 / DXT5Crunched 要 texture2ddecoder / crn（Blender 自带、venv 也有，
但**不是标准库**）。而工单 §9 要的那个入口（read_texture_2d）的价值在于
**把字节正确地取出来** —— 取错了，后面解码再对也是错件。
所以本模块只保证：

1. 元数据逐字段照抄（m_Width / m_Height / m_TextureFormat / m_MipCount / m_ColorSpace）；
2. 原始字节：内联取 image data，流式走 resolve_stream（**唯一**的定位实现）；
3. 取不到时抛 StreamedTextureError，**不返回空图**。

解码交给调用方（Blender 侧用 UnityPy 的 Texture2DConverter.parse_image_data，
把本模块解出来的字节喂进去 —— 这样「取字节」与「解码」的边界是清楚的）。

## 实测（本机 units 包，2026-10-03，RU_T72B_Base 用到的 3 张）

| 贴图 | 尺寸 | format | m_ColorSpace | .resS 区间 | 与参考 PNG 逐像素 |
|---|---|---|---|---|---|
| RU_T72B_1_BaseMap | 2048² | 29 (DXT5Crunched) | 1 | @1653533008+871011 | 相同 |
| RU_Track7290_Standard_22_AlbedoTransparency | 512² | 12 (DXT5) | 1 | @2533061664+349552 | 相同 |
| RU_Track7290_Standard_22_Normal | 512² | 12 (DXT5) | 0 | @2358384112+349552 | 相同 |

⚠ 同一个名字在包里**可能有多份**（上述 AlbedoTransparency 另有 1024² 的一份）。
所以引用只能用 **PPtr 的 pathID** 定，不许按名字找。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ba_mod_maker.asset_kernel.streaming import (
    StreamingInfo,
    StreamPayload,
    parse_streaming_info,
    resolve_stream,
)
from ba_mod_maker.asset_kernel.typetree import as_typetree, path_id_of

__all__ = [
    "FORMAT_NAMES",
    "StreamedTextureError",
    "TextureInfo",
    "read_texture_2d",
]

#: 本作实测出现过的格式号（设计稿 §3.5 的分布：DXT5 483 / DXT5Crunched 1649 /
#: DXT1Crunched 479 / DXT1 109 / RGBA32 3 / RGB24 1 / BC4 1）。
FORMAT_NAMES: dict[int, str] = {
    1: "Alpha8",
    3: "RGB24",
    4: "RGBA32",
    10: "DXT1",
    12: "DXT5",
    26: "BC4",
    28: "DXT1Crunched",
    29: "DXT5Crunched",
}

#: 内联字节的字段名（Unity 的 Texture2D 类型树里叫 image data，带空格）。
_INLINE_KEYS = ("image data", "m_ImageData", "image_data")


class StreamedTextureError(ValueError):
    """贴图字节取不到（流式没解 / 内联字段为空）。"""


@dataclass(frozen=True, slots=True)
class TextureInfo:
    """一张 Texture2D 的元数据 + 原始字节。"""

    path_id: int
    name: str
    width: int
    height: int
    format: int
    mip_count: int
    color_space: int
    image_size: int
    stream: StreamingInfo | None
    payload: StreamPayload | None
    inline: bytes

    @property
    def streamed(self) -> bool:
        return self.payload is not None

    @property
    def format_name(self) -> str:
        return FORMAT_NAMES.get(self.format, f"format#{self.format}")

    @property
    def raw(self) -> bytes:
        """原始（压缩）字节：流式优先，其次内联。"""
        if self.payload is not None:
            return self.payload.data
        return self.inline

    @property
    def hash_mismatch(self) -> bool:
        return bool(self.payload is not None and self.payload.hash_mismatch)

    def describe(self) -> str:
        where = (
            self.payload.describe()
            if self.payload is not None
            else f"内联 {len(self.inline)} B"
        )
        return (
            f"{self.name} {self.width}x{self.height} {self.format_name}"
            f" cs={self.color_space} {where}"
        )


def _inline_data(tree: Mapping[str, object]) -> bytes:
    for key in _INLINE_KEYS:
        value = tree.get(key)
        if value:
            raw = value if isinstance(value, (bytes, bytearray, memoryview)) else None
            if raw is not None:
                return bytes(raw)
            if isinstance(value, (list, tuple)):
                try:
                    return bytes(value)
                except ValueError as exc:  # 元素越界 = 不是原始字节
                    raise StreamedTextureError(
                        f"内联贴图字节里有超出 0..255 的元素（字段 {key!r}）"
                    ) from exc
    return b""


def _color_space_of(tree: Mapping[str, object]) -> int:
    """`m_ColorSpace` —— **缺失 / 类型不对就抛**，⛔ 不许折成 0。

    ## 为什么单开一条（2026-10-07 修，审计 D9）

    `0` 是**合法取值**（Linear → Blender 的 Non-Color）。原来写
    `int(tree.get("m_ColorSpace", 0) or 0)` ⇒ 把"字段根本没有"与"字段等于 0"折成同一个数，
    下游 `colorspace.colorspace_of()` 再也分不出来；而那份契约的头一句就是
    「未知的 m_ColorSpace **抛错**，不回落」（`colorspace.py:22`）。
    `m_ColorSpace` 是 classID 28 类型树的**必备键**
    （`texture_build.TEXTURE_FIELDS` 的 24 个键里有它），
    所以"没有它"= 这棵树不是 Texture2D，不是"那就当 0 吧"。

    `int()` 强转也一并收紧：`"1"` / `True` 这类一律拒收（与 `colorspace_of` 的「不强转」口径一致）。
    """
    raw = tree.get("m_ColorSpace")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise StreamedTextureError(
            f"贴图 {tree.get('m_Name')!r} 的 m_ColorSpace 不是整数"
            f"（{type(raw).__name__}={raw!r}）—— 它是 Texture2D 的必备键，"
            "而 0 是合法值（Linear）⇒ ⛔ 不折成 0、不强转（见 colorspace.py 的契约与审计 D9）"
        )
    return raw


def read_texture_2d(
    obj: object,
    *,
    stream: object = None,
    bundle: object = None,
    path_id: int | None = None,
) -> TextureInfo:
    """读一张 Texture2D。

    :param obj: typetree（Mapping）或任何有 read_typetree() 的资产对象。
    :param stream: 解流式字节的回调（吃 StreamingInfo 还 bytes）。
    :param bundle: 容器读取器；给了它就直接走 resolve_stream（更省事的一种用法）。
    :param path_id: 覆盖 pathID（typetree 里没有 pathID）。
    :raises StreamedTextureError: 流式而没给 stream/bundle、内联字段是空的、
        或者 `m_ColorSpace` 缺失/不是整数（⛔ 不折成 0 —— 见 `_color_space_of`）。
    """
    tree = as_typetree(obj)
    pid = path_id if path_id is not None else path_id_of(obj)
    info = parse_streaming_info(tree.get("m_StreamData"))
    inline = _inline_data(tree)
    payload: StreamPayload | None = None
    if info is not None and info.is_streamed:
        if bundle is not None:
            payload = resolve_stream(bundle, info)
        elif stream is not None:
            payload = StreamPayload(
                info=info,
                node_path="",
                node_offset=0,
                data=bytes(stream(info)),  # type: ignore[operator]
                matched_by="caller",
                hash_mismatch=False,
            )
        else:
            raise StreamedTextureError(
                f"贴图 {tree.get('m_Name')!r} 的字节在 .resS（{info.describe()}），"
                "既没给 bundle 也没给 stream"
            )
    elif not inline:
        raise StreamedTextureError(
            f"贴图 {tree.get('m_Name')!r} 既没有 m_StreamData 也没有内联字节"
        )
    return TextureInfo(
        path_id=int(pid),
        name=str(tree.get("m_Name", "") or ""),
        width=int(tree.get("m_Width", 0) or 0),
        height=int(tree.get("m_Height", 0) or 0),
        format=int(tree.get("m_TextureFormat", 0) or 0),
        mip_count=int(tree.get("m_MipCount", 0) or 0),
        color_space=_color_space_of(tree),
        image_size=int(tree.get("m_CompleteImageSize", 0) or 0),
        stream=info,
        payload=payload,
        inline=inline,
    )
