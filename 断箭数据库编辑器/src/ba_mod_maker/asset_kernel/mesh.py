"""Unity Mesh 的顶点流 / 索引缓冲解码（读侧，零第三方依赖）。

## 为什么手写而不是交给第三方

产品的 venv 与 Blender 的解释器不同（3.12 / 3.13），而顶点布局是**格式**不是**库**：
它由若干条 (stream, offset, format, dimension) 通道按流拼接而成。写成纯函数之后
两个解释器跑同一份源码，判据也就能在两边对拍。

## 四个会**静默出错**的坑（工单 §7 坑 2/3 + 本单实测）

1. read_typetree() 给出的 m_IndexBuffer 常是 **list[int]（长度 = 字节数）**。
   用 isinstance(x, bytes) 去判会拿到 b"" -> 0 条索引 -> **静默 0 网格通过**。
   本模块统一走 as_bytes()，并且对越界元素**抛错**而不是截断。
2. m_DataSize 为空 = 顶点数据**流式**存在 .resS 里。这时**不能**返回「空网格」：
   要么给 stream 回调把它解出来，要么抛 StreamedMeshError。
3. **dimension 字段要用 & 0xF**。本单实测（ammoprefabs 86 个 Mesh 全量）：
   法线通道的 dimension 读出来是 **52**（= 0x34），低半字节才是真维数 4。
   直接拿 52 算步长会得到 116 字节/顶点，而资产自己声明的是 32 —— 解出来的
   法线全是垃圾（绕序自洽率 1.0000 掉到 0.43）。UnityPy 的 MeshHelper 用的就是
   dimension & 0xF，本模块与它逐字节对拍一致。
4. **流之间要 16 字节对齐**（最后一条流不加尾部填充）。实测 ammoprefabs：
   8 个多流网格的实际长度 = 对齐后的跨度（Body 1303 顶点：52120 -> 对齐到 52128
   起第二条流 -> 57344 起第三条 -> 共 62556 B == m_DataSize）。
   一个**更正**：设计稿 §9.10.2 记的「m_DataSize 比 Σstride×Count 多 12 字节」
   是**尾部对齐填充**造成的假象；按「流间对齐、末尾不填充」算，86/86 个网格
   逐字节相等（见 tests/test_import_unit.py 的锚点）。

## 实测读数（本机，2026-10-03）

- ammoprefabs：86 个 Mesh = **7 内联 + 79 流式**；补上 .resS 之后
  **86/86 可解**，顶点 139,568 / 索引 367,056 / 三角 122,352，
  解出来的位置范围与资产自带的 m_LocalAABB **86/86 相符**（独立的自证）。
- 解码与 UnityPy 的 MeshHandler **逐顶点完全相同**（前 5000 顶点最大差异 0.0）。
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ba_mod_maker.asset_kernel.coords import reverse_winding
from ba_mod_maker.asset_kernel.streaming import StreamingInfo, parse_streaming_info

#: 顶点属性槽（m_Channels 的**下标**就是属性号，不是 offset）。
ATTRIBUTE_POSITION = 0
ATTRIBUTE_NORMAL = 1
ATTRIBUTE_TANGENT = 2
ATTRIBUTE_COLOR = 3
ATTRIBUTE_UV0 = 4
ATTRIBUTE_UV1 = 5
ATTRIBUTE_BLEND_WEIGHT = 12
ATTRIBUTE_BLEND_INDEX = 13

ATTRIBUTE_NAMES: dict[int, str] = {
    ATTRIBUTE_POSITION: "position",
    ATTRIBUTE_NORMAL: "normal",
    ATTRIBUTE_TANGENT: "tangent",
    ATTRIBUTE_COLOR: "color",
    ATTRIBUTE_UV0: "uv0",
    ATTRIBUTE_UV1: "uv1",
    6: "uv2",
    7: "uv3",
    ATTRIBUTE_BLEND_WEIGHT: "blend_weight",
    ATTRIBUTE_BLEND_INDEX: "blend_index",
}

#: Unity VertexAttributeFormat -> 每个分量的字节数。
FORMAT_SIZE: dict[int, int] = {
    0: 4,  # Float32
    1: 2,  # Float16
    2: 1,  # UNorm8
    3: 1,  # SNorm8
    4: 2,  # UNorm16
    5: 2,  # SNorm16
    6: 1,  # UInt8
    7: 1,  # SInt8
    8: 2,  # UInt16
    9: 2,  # SInt16
    10: 4,  # UInt32
    11: 4,  # SInt32
}

#: 索引格式：0 = UInt16，1 = UInt32。
INDEX_FORMAT_SIZE = {0: 2, 1: 4}

#: 顶点流之间的对齐（Unity 的硬口径，实测 = UnityPy 的 MeshHelper 同值）。
STREAM_ALIGN = 16

#: 解一个流式网格用的回调：吃 StreamingInfo，还字节。
StreamResolver = Callable[[StreamingInfo], bytes]


class StreamedMeshError(ValueError):
    """顶点数据不在对象里（m_DataSize 为空，走 m_StreamData -> .resS）。

    单独一个异常类型是有意的：调用方必须**显式**处理「这块没读」，不许当成空网格。
    """


class MeshFormatError(ValueError):
    """typetree 里的顶点布局不合法（缺通道 / 未知 format / 字节数对不上）。"""


def as_bytes(value: object) -> bytes:
    """把 read_typetree() 给出的「字节数组」统一成 bytes。

    接受 bytes / bytearray / memoryview / list[int] / tuple[int]。
    ⛔ 元素越界时**抛错**：那说明拿到的不是原始字节（例如被当成整数列表解读），
    静默截断会产出一个看起来正常的错网格。
    """
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        try:
            return bytes(value)
        except ValueError as exc:
            raise MeshFormatError(
                "字节数组里有超出 0..255 的元素：这不是原始字节，别按字节解读"
            ) from exc
    raise TypeError(f"不认识的字节数组类型：{type(value).__name__}")


def stream_info_of(typetree: Mapping[str, object]) -> StreamingInfo | None:
    """取一个 Mesh 的流式信息。

    **实测**：Mesh 的 m_StreamData 在 typetree 的**顶层**（不在 m_VertexData 里）——
    ammoprefabs 的 79 个流式网格、units 的 12 个网格都是这样。为了容错也认
    m_VertexData 里那一份（有些 Unity 版本放里面）。
    """
    info = parse_streaming_info(typetree.get("m_StreamData"))
    if info is not None:
        return info
    vertex_data = typetree.get("m_VertexData")
    if isinstance(vertex_data, Mapping):
        return parse_streaming_info(vertex_data.get("m_StreamData"))
    return None


def attribute_channel(
    channels: Sequence[Mapping[str, object]], attribute: int
) -> Mapping[str, object] | None:
    """取某个属性的通道；未使用（dimension == 0）或不存在时返回 None。"""
    if attribute < 0 or attribute >= len(channels):
        return None
    ch = channels[attribute]
    if not isinstance(ch, Mapping):
        return None
    if int(ch.get("dimension", 0) or 0) <= 0:
        return None
    return ch


def channel_stride(ch: Mapping[str, object]) -> int:
    """一条通道占多少字节 = offset + (dimension & 0xF) * 分量字节数。

    ⚠ dimension 必须 & 0xF（见模块头第 3 条坑）。
    """
    dim = int(ch.get("dimension", 0) or 0) & 0xF
    if dim <= 0:
        return 0
    fmt = int(ch.get("format", 0) or 0)
    size = FORMAT_SIZE.get(fmt)
    if size is None:
        raise MeshFormatError(f"未知的顶点属性 format={fmt}")
    return int(ch.get("offset", 0) or 0) + dim * size


def stream_layout(
    channels: Sequence[Mapping[str, object]], vertex_count: int
) -> tuple[dict[int, int], dict[int, int]]:
    """算每条流的步长与起点。

    :returns: (strides, bases)。strides[s] = 该流每个顶点的字节数；
        bases[s] = 该流在 m_DataSize 里的起点（各流按流号顺序拼接，**流间 16 字节对齐**）。
    """
    strides: dict[int, int] = {}
    for ch in channels:
        if not isinstance(ch, Mapping):
            continue
        stride = channel_stride(ch)
        if stride <= 0:
            continue
        stream = int(ch.get("stream", 0) or 0)
        strides[stream] = max(strides.get(stream, 0), stride)
    ordered = dict(sorted(strides.items()))
    bases: dict[int, int] = {}
    cursor = 0
    for stream, stride in ordered.items():
        bases[stream] = cursor
        cursor += stride * vertex_count
        cursor = (cursor + STREAM_ALIGN - 1) & ~(STREAM_ALIGN - 1)
    return ordered, bases


def layout_span(channels: Sequence[Mapping[str, object]], vertex_count: int) -> int:
    """顶点流一共该有多少字节（**末尾不加对齐填充**）。

    实测：ammoprefabs 86/86 个网格的 m_DataSize / m_StreamData.size 与它**逐字节相等**。
    """
    strides, bases = stream_layout(channels, vertex_count)
    if not strides:
        return 0
    last = max(strides)
    return bases[last] + strides[last] * vertex_count


def _unpack_one(code: int, buf: bytes, off: int) -> float:
    if code == 0:
        return struct.unpack_from("<f", buf, off)[0]
    if code == 1:
        return struct.unpack_from("<e", buf, off)[0]
    if code == 2:
        return buf[off] / 255.0
    if code == 3:
        return max(-1.0, struct.unpack_from("<b", buf, off)[0] / 127.0)
    if code == 4:
        return struct.unpack_from("<H", buf, off)[0] / 65535.0
    if code == 5:
        return max(-1.0, struct.unpack_from("<h", buf, off)[0] / 32767.0)
    if code == 6:
        return float(buf[off])
    if code == 7:
        return float(struct.unpack_from("<b", buf, off)[0])
    if code == 8:
        return float(struct.unpack_from("<H", buf, off)[0])
    if code == 9:
        return float(struct.unpack_from("<h", buf, off)[0])
    if code == 10:
        return float(struct.unpack_from("<I", buf, off)[0])
    if code == 11:
        return float(struct.unpack_from("<i", buf, off)[0])
    raise MeshFormatError(f"未知的顶点属性 format={code}")


def decode_attribute(
    channels: Sequence[Mapping[str, object]],
    data: bytes,
    vertex_count: int,
    attribute: int,
    *,
    strides: Mapping[int, int] | None = None,
    bases: Mapping[int, int] | None = None,
    components: int | None = None,
) -> list[tuple[float, ...]] | None:
    """解一个顶点属性，返回每个顶点一个元组。属性不存在时返回 None。

    :param components: 只取前几个分量（None = 全取，按 dimension & 0xF）。
    """
    ch = attribute_channel(channels, attribute)
    if ch is None:
        return None
    if strides is None or bases is None:
        strides, bases = stream_layout(channels, vertex_count)
    stream = int(ch.get("stream", 0) or 0)
    stride = strides[stream]
    start = bases[stream] + int(ch.get("offset", 0) or 0)
    fmt = int(ch.get("format", 0) or 0)
    dim = int(ch.get("dimension", 0) or 0) & 0xF
    size = FORMAT_SIZE[fmt]
    take = dim if components is None else min(dim, components)
    need = start + (vertex_count - 1) * stride + take * size
    if vertex_count and need > len(data):
        raise MeshFormatError(
            f"顶点流只有 {len(data)} 字节，按布局要 {need} 字节（格式表与数据不一致）"
        )
    out: list[tuple[float, ...]] = []
    for i in range(vertex_count):
        base = start + i * stride
        out.append(tuple(_unpack_one(fmt, data, base + d * size) for d in range(take)))
    return out


def read_indices(typetree: Mapping[str, object]) -> tuple[int, ...]:
    """读 m_IndexBuffer。

    ⛔ 坑（工单 §7 坑 2）：read_typetree() 给的常常是 list[int]，长度等于**字节数**。
    用 isinstance(x, bytes) 判会得到「0 条索引」，于是整批网格静默通过。
    """
    raw = as_bytes(typetree.get("m_IndexBuffer"))
    if not raw:
        return ()
    index_format = int(typetree.get("m_IndexFormat", 0) or 0)
    size = INDEX_FORMAT_SIZE.get(index_format)
    if size is None:
        raise MeshFormatError(f"未知的 m_IndexFormat={index_format}")
    count = len(raw) // size
    if count == 0:
        return ()
    code = "I" if size == 4 else "H"
    return struct.unpack(f"<{count}{code}", raw[: count * size])


def read_submesh_ranges(
    typetree: Mapping[str, object],
) -> tuple[tuple[int, int], ...]:
    r"""读 `m_SubMeshes` -> 每段的 **(首个三角形下标, 三角形数)**（按索引缓冲顺序）。

    ## 为什么要它（2026-10-17 实测）

    一个 renderer 可以挂**多颗材质**（`m_Materials` 有 2 条：机身 + 玻璃），而
    `m_SubMeshes` 才是「哪一段索引归第几颗材质」的**唯一**依据。旧读侧只把整条索引缓冲
    当一片面写出去 ⇒ **所有三角形都落在第 0 个材质槽**，玻璃槽 0 个多边形。
    实测（6 件单位逐个量 Blender 里的 polygon.material_index）：`RU_TU95142_glass` /
    `DLC3_Yak-130_Glass` / `DLC2_HMMV_Glass` 等**polys 全是 0**；于是「把玻璃 alpha 改成 1.0」
    与「保持 0.051」两张渲染**逐像素差 0**（576,000 像素里 0 个变）。

    ⛔ 拿不到 `m_IndexFormat` 就抛，不按 uint16 猜（猜错会把每段的偏移算歪一半）。
    """
    subs = typetree.get("m_SubMeshes") or []
    if not subs:
        return ()
    index_format = int(typetree.get("m_IndexFormat", 0) or 0)
    size = INDEX_FORMAT_SIZE.get(index_format)
    if size is None:
        raise MeshFormatError(f"未知的 m_IndexFormat={index_format}")
    out: list[tuple[int, int]] = []
    for entry in subs:
        if not isinstance(entry, Mapping):
            continue
        first_byte = int(entry.get("firstByte", 0) or 0)
        index_count = int(entry.get("indexCount", 0) or 0)
        if index_count < 3:
            out.append((0, 0))
            continue
        out.append((first_byte // size // 3, index_count // 3))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class MeshData:
    """一个可用的网格：位置 / 法线 / UV0 / UV1 / 索引（都是 Python 原生序列）。"""

    path_id: int
    name: str
    positions: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    indices: tuple[int, ...]
    uv0: tuple[tuple[float, float], ...] = ()
    #: **第二套 UV = 迷彩层**（实测 2026-10-16：作者自述「Камуфляжи реализованы через
    #: вторую UV」，实物 XA-180EST 载具 9/9 个网格有 uv1(dim2)、驾驶员 0/3）。
    #: 源里没有这条通道时是**空元组** —— ⛔ 不是"全 0"：两者必须能分辨
    #: （静默填零会让「这块没有迷彩层」和「迷彩层在 (0,0)」长得一模一样）。
    uv1: tuple[tuple[float, float], ...] = ()
    #: 蒙皮权重 / 骨骼槽号（**按槽号**索引 SMR.m_Bones，含空槽 —— 滤掉会整体错位）。
    bone_weights: tuple[tuple[float, ...], ...] = ()
    bone_indices: tuple[tuple[int, ...], ...] = ()
    #: `m_SubMeshes` 的段落：`((首三角形, 三角形数), …)`。空元组 = 源里没写（别当成 1 段）。
    #: 建面之后按它写 `polygon.material_index` —— 不写的话多材质 renderer 的副材质 0 个面。
    submeshes: tuple[tuple[int, int], ...] = ()

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3

    @property
    def vertex_count(self) -> int:
        return len(self.positions)


@dataclass(frozen=True, slots=True)
class UvChannelStats:
    """一套 UV 通道的**机械读数**（数网格 / 数顶点 / 值域 / 非零占比）。

    口径（写死在这一处，别再解释第二遍）：

    * ``meshes_with_channel`` —— 这条通道**解出来了且非空**的网格数
      （源里没有这条通道 ⇒ MeshData 那一栏是空元组 ⇒ 这里不计入，⛔ 不是「全 0」）；
    * ``nonzero_vertices`` —— u 或 v **有任一分量 != 0** 的顶点数。
      (0, 0) 是「这条通道没给值」的哨兵 ⇒ 「非零占比 0%」= 这一批里一个真值都没有；
    * 没有任何顶点时值域是 0.0（⛔ 不是 nan）—— 读不出数就该是 0，不许拿 nan 冒充读数。
    """

    attribute: int
    meshes: int
    meshes_with_channel: int
    vertices: int
    nonzero_vertices: int
    u_min: float
    u_max: float
    v_min: float
    v_max: float

    @property
    def nonzero_ratio(self) -> float:
        """非零顶点占比（一个顶点都没有时是 0.0）。"""
        return self.nonzero_vertices / self.vertices if self.vertices else 0.0

    def line(self, title: str = "") -> str:
        """一行读数，例：「载具：9/9 个网格有通道 · 45,776 顶点 · 非零 99.8% · u[…」"""
        head = f"{title}：" if title else ""
        name = ATTRIBUTE_NAMES.get(self.attribute, f"attr{self.attribute}")
        return (
            f"{head}{name} {self.meshes_with_channel}/{self.meshes} 个网格有通道 · "
            f"{self.vertices:,} 顶点 · 非零 {self.nonzero_ratio:.1%} · "
            f"u[{self.u_min:.4f},{self.u_max:.4f}] v[{self.v_min:.4f},{self.v_max:.4f}]"
        )


def uv_of(mesh_data: MeshData, attribute: int = ATTRIBUTE_UV0) -> tuple[tuple[float, float], ...]:
    """取一套 UV（目前只有 uv0 / uv1 两条）。⛔ 不认识的槽就抛，不许悄悄给空。"""
    if attribute == ATTRIBUTE_UV0:
        return mesh_data.uv0
    if attribute == ATTRIBUTE_UV1:
        return mesh_data.uv1
    raise MeshFormatError(
        f"attribute={attribute} 不是一套 UV（本模块只有 {ATTRIBUTE_UV0}=uv0 / {ATTRIBUTE_UV1}=uv1）"
    )


def uv_stats(meshes: Iterable[MeshData], attribute: int = ATTRIBUTE_UV0) -> UvChannelStats:
    """一批网格上一套 UV 的机械读数。

    本单要的那条读数就是它的两行输出（载具 vs 驾驶员）：
    「有通道的网格数 / 总网格数 ＋ 顶点数 ＋ 非零占比 ＋ 值域」。
    """
    count = 0
    with_channel = 0
    vertices = 0
    nonzero = 0
    lo_u = float("inf")
    hi_u = float("-inf")
    lo_v = float("inf")
    hi_v = float("-inf")
    for item in meshes:
        count += 1
        rows = uv_of(item, attribute)
        if rows:
            with_channel += 1
        vertices += len(rows)
        for u, v in rows:
            if u or v:
                nonzero += 1
            lo_u = min(lo_u, u)
            hi_u = max(hi_u, u)
            lo_v = min(lo_v, v)
            hi_v = max(hi_v, v)
    if not vertices:
        lo_u = hi_u = lo_v = hi_v = 0.0
    return UvChannelStats(
        attribute=attribute,
        meshes=count,
        meshes_with_channel=with_channel,
        vertices=vertices,
        nonzero_vertices=nonzero,
        u_min=lo_u,
        u_max=hi_u,
        v_min=lo_v,
        v_max=hi_v,
    )


@dataclass(slots=True)
class MeshScan:
    """一批网格的解码结果 + **没读到的那部分**（不许静默丢）。"""

    meshes: list[MeshData] = field(default_factory=list)
    streamed: list[str] = field(default_factory=list)
    malformed: list[tuple[str, str]] = field(default_factory=list)
    stream_failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def triangles(self) -> int:
        return sum(m.triangle_count for m in self.meshes)

    @property
    def vertices(self) -> int:
        return sum(m.vertex_count for m in self.meshes)


def read_mesh(
    typetree: Mapping[str, object],
    *,
    path_id: int = -1,
    stream: StreamResolver | None = None,
) -> MeshData:
    """把一个 Mesh 的 typetree 解成 MeshData。

    :param stream: 解流式顶点数据的回调（吃 StreamingInfo 还 bytes）。
        不给时，流式网格抛 StreamedMeshError。
    :raises StreamedMeshError: 顶点数据在 .resS 里而没给 stream。
    :raises MeshFormatError: 布局不合法 / 字节数不够。
    """
    name = str(typetree.get("m_Name", "") or "")
    vertex_data = typetree.get("m_VertexData")
    if not isinstance(vertex_data, Mapping):
        raise MeshFormatError(f"网格 {name!r} 没有 m_VertexData")
    data = as_bytes(vertex_data.get("m_DataSize"))
    vertex_count = int(vertex_data.get("m_VertexCount", 0) or 0)
    channels = list(vertex_data.get("m_Channels") or [])
    if not data:
        info = stream_info_of(typetree)
        if info is None or not info.is_streamed:
            raise StreamedMeshError(
                f"网格 {name!r} 的顶点数据不在对象里，也没有可用的 m_StreamData"
            )
        if stream is None:
            raise StreamedMeshError(
                f"网格 {name!r} 的顶点数据在 .resS（{info.describe()}），但没给 stream 回调"
            )
        data = bytes(stream(info))
        if not data:
            raise StreamedMeshError(f"网格 {name!r} 的 .resS 解出 0 字节（{info.describe()}）")
    if not vertex_count:
        raise MeshFormatError(f"网格 {name!r} 的 m_VertexCount 是 0")
    strides, bases = stream_layout(channels, vertex_count)
    positions = decode_attribute(
        channels, data, vertex_count, ATTRIBUTE_POSITION, strides=strides, bases=bases
    )
    normals = decode_attribute(
        channels, data, vertex_count, ATTRIBUTE_NORMAL, strides=strides, bases=bases, components=3
    )
    uv0 = decode_attribute(
        channels, data, vertex_count, ATTRIBUTE_UV0, strides=strides, bases=bases
    )
    # 第二套 UV（迷彩层）：源里没这条通道 ⇒ None ⇒ 出去是**空元组**（⛔ 不是全 0）。
    uv1 = decode_attribute(
        channels, data, vertex_count, ATTRIBUTE_UV1, strides=strides, bases=bases
    )
    if positions is None or normals is None:
        raise MeshFormatError(f"网格 {name!r} 缺位置或法线通道")
    weights = decode_attribute(
        channels, data, vertex_count, ATTRIBUTE_BLEND_WEIGHT, strides=strides, bases=bases
    )
    bone_slots = decode_attribute(
        channels, data, vertex_count, ATTRIBUTE_BLEND_INDEX, strides=strides, bases=bases
    )
    # ★ 2026-10-26：真件里有一类「硬蒙皮」网格 —— 只有 BlendIndices 通道、没有 BlendWeight。
    #   Unity 自己的读法：`GetBoneWeights()` 给每顶点 **权重 1.0**，骨索引就是 BlendIndices
    #   （实测 ProbeRealSkin：T-72B3 的 B3/K5_B3 全顶点 1.0，分别指向 turret_0 / body）。
    #   我们原先按「读不到权重就是没有」处理 ⇒ 顶点组是空的 ⇒ Blender 把整件绑到骨架根
    #   ⇒ 炮塔上的反应装甲不跟炮塔动。这里照 Unity 的口径补齐：第一个索引权重 1，其余 0。
    if bone_slots and not weights:
        weights = tuple(
            (1.0,) + (0.0,) * (len(row) - 1) if row else (0.0,)
            for row in bone_slots
        )
    indices = read_indices(typetree)
    if not indices:
        raise MeshFormatError(f"网格 {name!r} 的索引缓冲是空的")
    # 坐标契约（coords.py 模块头）：Unity->Blender 是 **det=-1 的反射**（手性要求），
    # 反射会把每个三角形的朝向翻过去 ⇒ 这里补回来，出去的就是 Blender 口径：
    # 面法线与资产自带法线一致（实测 AH-1Z 0.9996），而不是里外全反。
    indices = reverse_winding(indices)
    return MeshData(
        path_id=path_id,
        name=name,
        positions=tuple(positions),  # type: ignore[arg-type]
        normals=tuple(normals),  # type: ignore[arg-type]
        indices=indices,
        submeshes=read_submesh_ranges(typetree),
        uv0=tuple(uv0 or ()),  # type: ignore[arg-type]
        uv1=tuple(uv1 or ()),  # type: ignore[arg-type]
        bone_weights=tuple(weights or ()),  # type: ignore[arg-type]
        bone_indices=tuple(
            tuple(int(v) for v in row) for row in (bone_slots or ())
        ),
    )


def scan_meshes(
    trees: Iterable[Mapping[str, object]],
    *,
    path_ids: Iterable[int] | None = None,
    stream: StreamResolver | None = None,
) -> MeshScan:
    """批量解码，并把「流式 / 格式不对」逐条记下来。

    这就是防「静默 0 网格通过」的那道闸：读不到的网格会出现在 scan.streamed /
    scan.stream_failures / scan.malformed 里，调用方要么处理、要么把读数打出来。
    """
    scan = MeshScan()
    ids = list(path_ids) if path_ids is not None else None
    for i, tree in enumerate(trees):
        pid = ids[i] if ids is not None and i < len(ids) else -1
        name = str(tree.get("m_Name", "") or "")
        info = None
        try:
            info = stream_info_of(tree)
        except ValueError:
            info = None
        try:
            scan.meshes.append(read_mesh(tree, path_id=pid, stream=stream))
        except StreamedMeshError as exc:
            if stream is None or info is None or not info.is_streamed:
                scan.streamed.append(name)
            else:
                scan.stream_failures.append((name, f"{info.describe()}: {exc}"))
        except (MeshFormatError, TypeError, ValueError) as exc:
            scan.malformed.append((name, f"{type(exc).__name__}: {exc}"))
    return scan
