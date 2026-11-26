r"""按原版规格体检（只读）：把一件资产的「外包交付规格」量成数字。

## 这一层回答什么

    ba-mod-maker check-model --spec --unit DLC2_XA180EST

    -> 纹素密度 155.9 px/m（运行 2048² 口径）· UV 岛面积之和 143.3 % ·
       texel 覆盖率 68.02 % · LOD0 tris 25,751 · 贴图 2048²

## ★★ 两条口径（2026-10-16 与外包作者自述对撞后钉死，⛔ 别再合成一个数）

1. **纹素密度** = `sqrt(UV面积 / 世界面积) × 贴图边长`，单位 px/m —— **两个口径差 2 倍**：
   * **运行口径**：用资产里**真实那张贴图的边长**（XA-180EST 是 2048² ⇒ **155.9** px/m）；
   * **交付口径**：外包交付母版那一档（同一件 4096² ⇒ **311.7** px/m）。
   ⛔ 引「外包交付密度」时必须写明是哪一个 —— 作者自述「游戏内 ~162 px/m」
   对上的**只有运行口径**（−3.8 %）。

2. **「UV 填充」不是一条读数，是两条**（混用会得出**相反**的结论）：
   * `uv_island_sum`：Σ 各三角形 UV 面积，重叠**重复计** —— **可以 >100 %**
     （XA-180EST **143.3 %**）；
   * `uv_coverage`：栅格化数被覆盖的 texel，重叠**只算一次** —— **永远 ≤100 %**
     （同一件 **68.02 %**）。
   143.3 % 挤进 68.02 % 的空间 ⇒ **至少 75 % 的 UV 面积是重叠 / 镜像**的（第一次实物兑现）。

## ⛔ 它不是什么

* **不是闸门**：只印读数与「离原版样本多远」，⛔ **不拦用户的件**。那些区间是
  **某几家外包商的交付习惯**（样本与出处见 `docs/烘焙规范.md` §8），**不是游戏的硬约束**
  —— 用户可能就是要把件做得更大更细；
* **不写盘**：只读（开包一次 + 读网格 / 贴图，一个字节都不写）。

## 正控（必须复现；独立探针与用例都拿它对）

    DLC2_XA180EST -> TD **155.9** px/m（2048² 口径）· UV 岛面积和 **143.3 %** ·
                    覆盖率 **68.02 %**（8192² 收敛）· LOD0 tris **25,751** · 贴图 **2048²**
    逐级 tris **25,751 / 10,300 / 2,575 / 0**（4 级 LOD）

    出处：`_work/mobygames/xa180_td2.py`（独立探针）
          ＋ `_work/mobygames/拆件-XA180EST.md` §6.1
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CONTROL_KEY",
    "CONTROL_TD_RUNTIME",
    "CONTROL_TEXTURE_EDGE",
    "CONTROL_TRIS_LOD0",
    "CONTROL_TRI_LEVELS",
    "CONTROL_UV_COVERAGE",
    "CONTROL_UV_ISLAND_SUM",
    "DEFAULT_RASTERS",
    "DELIVERY_FACTOR",
    "SAMPLE_SIZE_TIERS",
    "SAMPLE_SOURCE",
    "SAMPLE_TD_PX_M",
    "SAMPLE_TRIS",
    "SAMPLE_UV_FILL_PCT",
    "MeshReading",
    "SpecReadError",
    "SpecReadings",
    "TextureReading",
    "mesh_uv_area",
    "mesh_world_area",
    "raster_coverage",
    "readings_of_bundle",
    "readings_of_unit",
    "texel_density",
    "uv_triangles",
]

#: 覆盖率要栅格化的几档（8192² 是收敛档 —— 实测 2048/4096/8192 = 69.71 / 68.57 / 68.02）。
DEFAULT_RASTERS: tuple[int, ...] = (2048, 4096, 8192)

#: 交付档 = 运行档 × 这个系数（实物：作者交 4K、游戏里 2048²，正好一半）。
#: ⚠ 这是**那几家外包商的交付习惯**（`docs/烘焙规范.md` §8），⛔ 不是游戏的硬约束。
DELIVERY_FACTOR = 2.0

#: ★ 原版样本区间（4 位作者 / 40+ 件一手自述）—— **只用来对照，⛔ 不做闸门**。
SAMPLE_SOURCE = "docs/烘焙规范.md §8（4 位作者 / 40+ 件 · 一手自述 + 逐件 URL）"
SAMPLE_TD_PX_M = (133.0, 551.0)
SAMPLE_UV_FILL_PCT = (128.0, 471.0)
SAMPLE_TRIS = (1874, 73823)
#: ⛔ 面数**按车 / 机两档**（「一张档打天下」已被实测推翻：飞机显著更低）。
SAMPLE_TRIS_VEHICLE = (27683, 38511)
SAMPLE_TRIS_AIRCRAFT = (9943, 16519)
SAMPLE_SIZE_TIERS = "武器 / 挂件 1K · 飞机与小件 2K · 载具 1×4K（中件）或 2 套 4K＋2K（大件）"

#: 正控（必须复现）。独立探针 = `_work/mobygames/xa180_td2.py`。
CONTROL_KEY = "DLC2_XA180EST"
CONTROL_TD_RUNTIME = 155.9
CONTROL_UV_ISLAND_SUM = 143.3
CONTROL_UV_COVERAGE = 68.02
CONTROL_TRIS_LOD0 = 25751
CONTROL_TRI_LEVELS: tuple[int, ...] = (25751, 10300, 2575, 0)
CONTROL_TEXTURE_EDGE = 2048

#: 覆盖率跟正控比时允许的差（栅格化是**确定性**的，这里只留给浮点）。
COVERAGE_TOLERANCE = 0.05


class SpecReadError(ValueError):
    """读不出规格读数（键定位不到 / 根上没有 LODGroup / 网格读不出来）。"""


# --------------------------------------------------------------------------- #
# 纯函数：几何量（用例直接喂造出来的三角形也能跑）
# --------------------------------------------------------------------------- #
def mesh_world_area(
    positions: Sequence[Sequence[float]], indices: Sequence[int]
) -> float:
    """三角形面积之和（世界单位 m²）—— 顶点表本身就是世界坐标。"""
    total = 0.0
    for i in range(0, len(indices) - 2, 3):
        a = positions[indices[i]]
        b = positions[indices[i + 1]]
        c = positions[indices[i + 2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx = uy * vz - uz * vy
        cy = uz * vx - ux * vz
        cz = ux * vy - uy * vx
        total += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
    return total


def mesh_uv_area(
    uv: Sequence[Sequence[float]], indices: Sequence[int]
) -> float:
    """**UV 岛面积之和**：Σ 各三角形 UV 面积，重叠**重复计**（⇒ 可以 >1 个 UV 单位）。

    ⛔ 与 `raster_coverage` 是**两条不同的读数**，别合成一个「UV 填充率」。
    """
    total = 0.0
    for i in range(0, len(indices) - 2, 3):
        p0 = uv[indices[i]]
        p1 = uv[indices[i + 1]]
        p2 = uv[indices[i + 2]]
        total += abs(
            (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (p1[1] - p0[1])
        ) * 0.5
    return total


def texel_density(uv_area: float, world_area: float, texture_edge: float) -> float:
    """纹素密度（px/m）= `sqrt(UV面积 / 世界面积) × 贴图边长`。

    ⚠ `texture_edge` 给哪一档决定这是**运行口径**还是**交付口径**（两者差 2 倍）。
    三个输入任一 ≤ 0 时返回 0.0（**不是**异常：空网格是合法状态）。
    """
    if world_area <= 0.0 or uv_area <= 0.0 or texture_edge <= 0.0:
        return 0.0
    return math.sqrt(uv_area / world_area) * float(texture_edge)


def uv_triangles(
    uv: Sequence[Sequence[float]], indices: Sequence[int]
) -> list[tuple[Sequence[float], Sequence[float], Sequence[float]]]:
    """逐个三角形产出 UV 三个角（栅格化用）。"""
    out = []
    for i in range(0, len(indices) - 2, 3):
        out.append((uv[indices[i]], uv[indices[i + 1]], uv[indices[i + 2]]))
    return out


def raster_coverage(
    triangles: Iterable[Sequence[Sequence[float]]], size: int
) -> float:
    """texel 覆盖率（%）：把 UV 栅格化，重叠**只算一次**（⇒ 永远 ≤100 %）。

    ⚠ 实现与独立探针 `_work/mobygames/xa180_td2.py` **逐句同形**（同一套栅格语义），
    否则 **68.02 %** 这个正控复现不出来 —— 改这里就等于换尺子，必须重跑正控。
    """
    from PIL import Image, ImageDraw  # noqa: PLC0415 -- 只在真要栅格化时拉 Pillow

    n = int(size)
    img = Image.new("1", (n, n), 0)
    draw = ImageDraw.Draw(img)
    for tri in triangles:
        draw.polygon(
            [(float(p[0]) * n, (1.0 - float(p[1])) * n) for p in tri], fill=1
        )
    covered = sum(img.histogram()[1:])
    del draw, img
    return covered / float(n * n) * 100.0


# --------------------------------------------------------------------------- #
# 读数
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MeshReading:
    """一个网格的几何读数（LOD0 那一批用）。"""

    name: str
    path_id: int
    triangles: int
    vertices: int
    world_area: float
    uv_area: float
    #: 这个网格**真带数据**的顶点通道名（`m_Channels` 里 dimension > 0 的那些）。
    channels: tuple[str, ...] = ()
    #: 与 `channels` **一一对应**的维度（读数里那个 `uv1(dim2)` 的 2）。
    channel_dims: tuple[int, ...] = ()

    @property
    def has_uv1(self) -> bool:
        """有没有第二套 UV（迷彩层 —— 实物口径：载具 9/9 有、驾驶员 0/3）。"""
        return "uv1" in self.channels

    def channel_text(self) -> str:
        """通道 ＋ 维度的读数串（例 `uv0(dim2) uv1(dim2)`）。"""
        pairs = zip(self.channels, self.channel_dims, strict=False)
        text = " ".join(f"{name}(dim{dim})" for name, dim in pairs)
        return text or "—"

    def line(self) -> str:
        chans = self.channel_text()
        return (
            f"  {self.name:<12} {self.triangles:>7,} {self.vertices:>9,}"
            f" {self.world_area:>13.3f} {self.uv_area:>10.5f}   {chans}"
        )


@dataclass(frozen=True, slots=True)
class TextureReading:
    """一张贴图的档位读数。"""

    path_id: int
    name: str
    width: int
    height: int
    fmt: int
    mip_count: int
    #: 它挂在哪些槽上（`材质名/槽名`）。
    slots: tuple[str, ...] = ()

    @property
    def edge(self) -> int:
        """贴图档（取长边 —— 真件绝大多数是方图）。"""
        return max(int(self.width), int(self.height))

    def line(self) -> str:
        where = " · ".join(self.slots) or "未被任何槽指向"
        return (
            f"  {self.width}x{self.height:<11} fmt {self.fmt:<3} mips {self.mip_count:<3}"
            f" {self.name}  用在 {where}"
        )

@dataclass(slots=True)
class SpecReadings:
    """一件资产的规格读数（`readings_of_unit` 的产物）。"""

    unit_key: str
    asset_path: str = ""
    bundle_name: str = ""
    #: 逐级 renderer 数（根节点那个 LODGroup 的分解）。
    lod_counts: tuple[int, ...] = ()
    #: 逐级 tris 合计。
    lod_tris: tuple[int, ...] = ()
    meshes: tuple[MeshReading, ...] = ()
    textures: tuple[TextureReading, ...] = ()
    #: 拿哪一张当「底图」（纹素密度的分母口径就靠它）。
    base_color_edge: int = 0
    base_color_where: str = ""
    #: 栅格 -> 覆盖率（%）。
    coverage: dict[int, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    # ---- 合计（LOD0） ----
    @property
    def world_area(self) -> float:
        return sum(m.world_area for m in self.meshes)

    @property
    def uv_area(self) -> float:
        return sum(m.uv_area for m in self.meshes)

    @property
    def triangles(self) -> int:
        return sum(m.triangles for m in self.meshes)

    @property
    def vertices(self) -> int:
        return sum(m.vertices for m in self.meshes)

    @property
    def uv1_meshes(self) -> int:
        """LOD0 里有第二套 UV 的网格数（迷彩层 —— 实物：载具 9/9、驾驶员 0/3）。"""
        return sum(1 for m in self.meshes if m.has_uv1)

    # ---- ★ 两条口径 ----
    @property
    def td_runtime(self) -> float:
        """**运行口径**纹素密度（用资产里真实那张底图的边长）。"""
        return texel_density(self.uv_area, self.world_area, self.base_color_edge)

    def td_delivery(self, factor: float = DELIVERY_FACTOR) -> float:
        """**交付口径**纹素密度（母版档 = 运行档 × factor）。"""
        return texel_density(self.uv_area, self.world_area, self.base_color_edge * factor)

    @property
    def uv_island_sum(self) -> float:
        """UV 岛面积之和（%）：重叠**重复计** ⇒ 可以 >100 %。"""
        return self.uv_area * 100.0

    @property
    def uv_coverage(self) -> float:
        """**收敛档**的 texel 覆盖率（%）：重叠**只算一次** ⇒ 永远 ≤100 %。

        ⚠ 取的是**栅格最大**那一档，⛔ **不是**「最大值」：这条曲线随栅格变大而**单调下降**
        （实测 2048² 69.71 · 4096² 68.57 · 8192² 68.02 —— 小栅格会在岛缝上多算），
        所以收敛值是**最后一档**；正控 = **68.02 %**。
        """
        if not self.coverage:
            return 0.0
        return self.coverage[max(self.coverage)]

    @property
    def uv_mirror_floor(self) -> float:
        """至少有多少 UV 面积是重叠 / 镜像的（% = 岛面积之和 − 覆盖率）。"""
        return max(0.0, self.uv_island_sum - self.uv_coverage)

    # ---- 输出 ----
    def lines(self) -> tuple[str, ...]:
        """人读输出（CLI 逐行 print）。"""
        out: list[str] = ["[按原版规格体检] " + self.unit_key]
        if self.asset_path:
            out.append("  地址      " + self.asset_path)
        if self.bundle_name:
            out.append("  模型包    " + self.bundle_name)
        if self.lod_counts:
            out.append(
                f"  LOD       {len(self.lod_counts)} 级（逐级渲染器 "
                + " / ".join(str(c) for c in self.lod_counts)
                + "）"
            )
        if self.lod_tris:
            out.append("  逐级 tris " + " / ".join(format(t, ",") for t in self.lod_tris))
        out.append("")
        out.append(f"外观（LOD0，{len(self.meshes)} 个网格）")
        out.append("  名称             tris     verts      世界面积 m²    UV 面积   通道")
        out.extend(m.line() for m in self.meshes)
        out.append(
            f"  合计          {self.triangles:>8,} {self.vertices:>9,}"
            f" {self.world_area:>13.3f} {self.uv_area:>10.5f}"
        )
        if self.meshes:
            out.append(
                f"  有 uv1（迷彩层）的网格  {self.uv1_meshes} / {len(self.meshes)}"
            )
        out.append("")
        out.append("★ 纹素密度（两个口径差 2 倍，⛔ 引用时必须写明是哪一个）")
        if self.base_color_edge:
            out.append(
                f"  运行 {self.base_color_edge:>4}² 口径  {self.td_runtime:>7.1f} px/m"
                f"   （底图 {self.base_color_where}）"
            )
            out.append(
                f"  交付 {int(self.base_color_edge * DELIVERY_FACTOR):>4}² 口径"
                f"  {self.td_delivery():>7.1f} px/m   （= 运行档 × {DELIVERY_FACTOR:g}；"
                "那几家外包商的交付习惯，⛔ 不是游戏的硬约束）"
            )
            out.append(
                f"  样本区间      {SAMPLE_TD_PX_M[0]:g}–{SAMPLE_TD_PX_M[1]:g} px/m"
                f"   （{SAMPLE_SOURCE}）"
            )
        else:
            out.append("  读不出底图档 ⇒ 这一条算不了（贴图表在下面）")
        out.append("")
        out.append("★ UV 两条读数（混用会得出相反的结论）")
        out.append(
            f"  UV 岛面积之和   {self.uv_island_sum:7.1f} %   （重叠重复计 ⇒ 可以 >100 %）"
        )
        if self.coverage:
            detail = " · ".join(f"{k}² {v:.2f}" for k, v in sorted(self.coverage.items()))
            out.append(
                f"  texel 覆盖率    {self.uv_coverage:7.2f} %   （重叠只算一次；{detail}）"
            )
            out.append(f"  ⇒ 至少 {self.uv_mirror_floor:.1f} % 的 UV 面积是**重叠 / 镜像**的")
        out.append("")
        out.append("★ 面数与贴图档")
        out.append(
            f"  LOD0 tris     {self.triangles:>8,}   （样本 地面车"
            f" {SAMPLE_TRIS_VEHICLE[0]:,}–{SAMPLE_TRIS_VEHICLE[1]:,} · 飞机"
            f" {SAMPLE_TRIS_AIRCRAFT[0]:,}–{SAMPLE_TRIS_AIRCRAFT[1]:,}）"
        )
        out.append("  贴图档位      " + SAMPLE_SIZE_TIERS)
        out.extend(t.line() for t in self.textures)
        if self.notes:
            out.append("")
            out.extend("⚠ " + n for n in self.notes)
        out.append("")
        out.append(
            "⛔ 这不是闸门：只报读数与离原版样本多远 —— "
            "那些数字是外包商的交付习惯，不是游戏的硬约束。"
        )
        return tuple(out)

    def document(self) -> dict[str, Any]:
        """`--json` 那一份（形状稳定：加字段不涨版本）。"""
        return {
            "unit_key": self.unit_key,
            "asset_path": self.asset_path,
            "bundle": self.bundle_name,
            "lod_counts": list(self.lod_counts),
            "lod_tris": list(self.lod_tris),
            "world_area_m2": round(self.world_area, 4),
            "uv_area": round(self.uv_area, 6),
            "uv_island_sum_pct": round(self.uv_island_sum, 2),
            "uv_coverage_pct": round(self.uv_coverage, 2),
            "coverage_by_raster": {
                str(k): round(v, 3) for k, v in sorted(self.coverage.items())
            },
            "td_runtime_px_m": round(self.td_runtime, 2),
            "td_delivery_px_m": round(self.td_delivery(), 2),
            "delivery_factor": DELIVERY_FACTOR,
            "triangles_lod0": self.triangles,
            "vertices_lod0": self.vertices,
            "uv1_meshes": self.uv1_meshes,
            "texture_edge": self.base_color_edge,
            "texture_edge_where": self.base_color_where,
            "sample_source": SAMPLE_SOURCE,
            "sample_ranges": {
                "td_px_m": list(SAMPLE_TD_PX_M),
                "uv_fill_pct": list(SAMPLE_UV_FILL_PCT),
                "tris": list(SAMPLE_TRIS),
                "size_tiers": SAMPLE_SIZE_TIERS,
            },
            "meshes": [
                {
                    "name": m.name,
                    "path_id": m.path_id,
                    "triangles": m.triangles,
                    "vertices": m.vertices,
                    "world_area_m2": round(m.world_area, 4),
                    "uv_area": round(m.uv_area, 6),
                    "channels": list(m.channels),
                    "has_uv1": m.has_uv1,
                }
                for m in self.meshes
            ],
            "textures": [
                {
                    "path_id": t.path_id,
                    "name": t.name,
                    "width": t.width,
                    "height": t.height,
                    "format": t.fmt,
                    "mip_count": t.mip_count,
                    "slots": list(t.slots),
                }
                for t in self.textures
            ],
            "notes": list(self.notes),
            "gate": False,
        }

# --------------------------------------------------------------------------- #
# 读一件（只读）
# --------------------------------------------------------------------------- #
class _SourceReader:
    """把已经开着的 `AssetSource` 适配成 `texture_install.color_layers` 认的那个形状。

    只实现它能用到的那一个方法（`fields_of`）—— ⛔ 别再写一遍「读 shader 自写 desc」
    的逻辑，那是 `texture_install.color_layers` 那**一份**（kb/19 §1 唯一入口）。
    """

    def __init__(self, source: Any) -> None:
        self._source = source

    def fields_of(self, path_id: int) -> Mapping[str, Any]:
        return self._source.read_typetree(int(path_id))


def _root_lod_levels(root: Mapping[str, Any] | None) -> tuple[tuple[int, ...], ...]:
    """根节点上那个 LODGroup 的分解（取不到给空元组）。"""
    from ba_mod_maker.asset_kernel import prefab as prefab_mod  # noqa: PLC0415

    for comp in (root or {}).get("components") or ():
        if str(comp.get("kind", "")) == prefab_mod.LOD_GROUP:
            return tuple(tuple(int(c) for c in lv) for lv in (comp.get("lods") or ()))
    return ()


def _channels_of(typetree: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """这个网格**真带数据**的顶点通道：`(名字, 维度)` 两份一一对应的元组。

    ⚠ 只数 `dimension > 0` 的那些 —— 真件里 `m_Channels` 的槽位是**定长 14 个**，
    没用的那条 dimension 是 0（按槽位全数会得出「每个网格都有 14 条通道」的假读数）。
    """
    from ba_mod_maker.asset_kernel.mesh import ATTRIBUTE_NAMES  # noqa: PLC0415

    vertex_data = typetree.get("m_VertexData")
    raw = vertex_data.get("m_Channels") if isinstance(vertex_data, Mapping) else None
    names: list[str] = []
    dims: list[int] = []
    for index, chan in enumerate(raw or ()):
        if not isinstance(chan, Mapping):
            continue
        dim = int(chan.get("dimension", 0) or 0)
        if dim <= 0:
            continue
        names.append(ATTRIBUTE_NAMES.get(index) or f"channel{index}")
        dims.append(dim)
    return tuple(names), tuple(dims)


def _mesh_of(source: Any, path_id: int, cache: dict[int, Any]) -> Any | None:
    """读一个网格（带缓存）；读不出来给 None（⛔ 不静默当成空网格）。"""
    key = int(path_id)
    if key in cache:
        return cache[key]
    try:
        data = source.read_mesh(key)
    except Exception:  # noqa: BLE001 -- 流式缺失 / 布局坏：都只该影响这一条读数
        data = None
    cache[key] = data
    return data


def readings_of_unit(
    game_root: str | Path,
    unit_key: str,
    *,
    rasters: Sequence[int] = DEFAULT_RASTERS,
) -> SpecReadings:
    """读一件资产的规格读数（**只读**：开包一次，一个字节都不写）。

    :param game_root: 游戏根目录。
    :param unit_key: `Units.ModelFileName` 的**原样值**（例 DLC2_XA180EST）。
    :param rasters: 覆盖率要栅格化的几档（默认 2048/4096/8192，最后一档是收敛档）。
    :raises SpecReadError: 键定位不到 / 包读不出来。
    """
    from ba_mod_maker.asset_kernel import (  # noqa: PLC0415
        prefab as prefab_mod,
    )
    from ba_mod_maker.asset_kernel import (  # noqa: PLC0415
        skin_install,
        texture_install,
    )

    try:
        with skin_install.open_unit(game_root, unit_key) as unit:
            opened = _Opened(
                source=unit.source,
                asset_path=str(unit.asset_path),
                bundle_name=str(unit.bundle_name),
            )
            return _readings_within(opened, unit_key, rasters, prefab_mod, texture_install)
    except SpecReadError:
        raise
    except Exception as exc:
        raise SpecReadError(
            f"读 {unit_key!r} 的规格读数时出错：{type(exc).__name__}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class _Opened:
    """一条读侧通道：一个**已经开着**的源 ＋ 它那条资产地址 ＋ 包名。"""

    source: Any
    asset_path: str
    bundle_name: str


def readings_of_bundle(
    bundle_path: str | Path,
    *,
    asset_path: str | None = None,
    rasters: Sequence[int] = DEFAULT_RASTERS,
) -> SpecReadings:
    """读**一个 UnityFS 包**里那条 prefab 的规格读数（只读）。

    用来体检**我们自己造的**件（`check-model --spec --bundle`）：装之前先看它离原版样本多远。
    `asset_path` 不给时要求容器里**只有一条** `.prefab` 地址 —— ⛔ 多条不猜，点名拒。
    """
    from ba_mod_maker.asset_kernel import (  # noqa: PLC0415
        prefab as prefab_mod,
    )
    from ba_mod_maker.asset_kernel import (  # noqa: PLC0415
        texture_install,
        unitypy_source,
    )

    path = Path(bundle_path)
    if not path.is_file():
        raise SpecReadError(f"找不到包 {path}")
    try:
        with unitypy_source.open_source(path) as source:
            opened = _Opened(
                source=source,
                asset_path=str(asset_path or _sole_prefab(source)),
                bundle_name=path.name,
            )
            return _readings_within(
                opened, opened.asset_path.rsplit("/", 1)[-1] or path.stem,
                rasters, prefab_mod, texture_install,
            )
    except SpecReadError:
        raise
    except Exception as exc:
        raise SpecReadError(
            f"读 {path.name} 的规格读数时出错：{type(exc).__name__}: {exc}"
        ) from exc


def _sole_prefab(source: Any) -> str:
    """包里那条**唯一**的 prefab 地址（0 条 / 多条都点名拒，⛔ 不猜）。"""
    entries = [p for p, _pid in source.container_entries() if str(p).endswith(".prefab")]
    if len(entries) == 1:
        return str(entries[0])
    raise SpecReadError(
        f"这个包的容器里有 {len(entries)} 条 .prefab 地址 ⇒ 必须显式给 --asset-path；"
        f"候选：{'、'.join(str(p) for p in entries[:6]) or '（一条都没有）'}"
    )


def _readings_within(
    unit: _Opened,
    unit_key: str,
    rasters: Sequence[int],
    prefab_mod: Any,
    texture_install: Any,
) -> SpecReadings:
    """源已经开着：prefab 树 -> 网格 -> 贴图（上面两条入口的实现段）。"""
    source = unit.source
    nodes, materials = source.read_prefab_nodes(unit.asset_path)
    tree = prefab_mod.build_tree(nodes, materials)

    notes: list[str] = []
    root = next((n for n in nodes if n.get("parent_transform") is None), None)
    lod_levels = _root_lod_levels(root)
    if not lod_levels:
        lod_levels = tuple(tuple(int(c) for c in lv) for lv in tree.lods)
        notes.append(
            "根节点上没有 LODGroup ⇒ 退回树里**最后一个** LODGroup 的分解"
            "（可能不是车体那一棵）"
        )
    if not lod_levels:
        lod_levels = (tuple(int(r.component_id) for r in tree.renderers),)
        notes.append("整棵树里没有 LODGroup ⇒ 把全部渲染器当成一级")

    cache: dict[int, Any] = {}
    lod_counts = tuple(len(lv) for lv in lod_levels)
    lod_tris = tuple(_level_tris(tree, lv, source, cache) for lv in lod_levels)

    meshes: list[MeshReading] = []
    uv_tris: list[tuple[Sequence[float], Sequence[float], Sequence[float]]] = []
    material_ids: list[int] = []
    seen: set[int] = set()
    for cid in lod_levels[0]:
        renderer = tree.renderer_of(int(cid))
        if renderer is None:
            continue
        for mid in renderer.material_ids:
            if int(mid) and int(mid) not in material_ids:
                material_ids.append(int(mid))
        if renderer.mesh_id is None:
            continue
        mesh_id = int(renderer.mesh_id)
        if mesh_id in seen:
            continue
        seen.add(mesh_id)
        data = _mesh_of(source, mesh_id, cache)
        if data is None:
            notes.append(f"网格 {mesh_id} 读不出来（流式缺失 / 布局坏）⇒ 不计入合计")
            continue
        channels = _channels_of(source.read_typetree(mesh_id))
        meshes.append(
            MeshReading(
                name=str(getattr(data, "name", "") or renderer.name),
                path_id=mesh_id,
                triangles=int(data.triangle_count),
                vertices=int(data.vertex_count),
                world_area=mesh_world_area(data.positions, data.indices),
                uv_area=mesh_uv_area(data.uv0, data.indices),
                channels=channels[0],
                channel_dims=channels[1],
            )
        )
        uv_tris.extend(uv_triangles(data.uv0, data.indices))

    coverage = {int(size): raster_coverage(uv_tris, int(size)) for size in rasters}
    textures, base_edge, base_where = _textures_of(
        tree, source, material_ids, texture_install
    )

    return SpecReadings(
        unit_key=unit_key,
        asset_path=str(unit.asset_path),
        bundle_name=str(unit.bundle_name),
        lod_counts=lod_counts,
        lod_tris=lod_tris,
        meshes=tuple(meshes),
        textures=textures,
        base_color_edge=base_edge,
        base_color_where=base_where,
        coverage=coverage,
        notes=tuple(notes),
    )


def _level_tris(tree: Any, level: Sequence[int], source: Any, cache: dict[int, Any]) -> int:
    """一级 LOD 的 tris 合计（同级里同一个网格只算一次）。"""
    seen: set[int] = set()
    total = 0
    for cid in level:
        renderer = tree.renderer_of(int(cid))
        if renderer is None or renderer.mesh_id is None:
            continue
        mesh_id = int(renderer.mesh_id)
        if mesh_id in seen:
            continue
        seen.add(mesh_id)
        data = _mesh_of(source, mesh_id, cache)
        if data is not None:
            total += int(data.triangle_count)
    return total


def _textures_of(
    tree: Any, source: Any, material_ids: Sequence[int], texture_install: Any
) -> tuple[tuple[TextureReading, ...], int, str]:
    """LOD0 材质用到的那几张贴图 ＋ 「底图」是哪一张（读 shader 自写 desc 认 Albedo）。"""
    slots_of: dict[int, list[str]] = {}
    for mid in material_ids:
        entry = tree.materials.get(int(mid)) or {}
        name = str(entry.get("name") or f"<material {mid}>")
        for slot in entry.get("texture_slots") or ():
            pair = tuple(slot)[:2] if isinstance(slot, (tuple, list)) else ()
            if len(pair) < 2 or not pair[1]:
                continue
            slots_of.setdefault(int(pair[1]), []).append(f"{name}/{pair[0]}")

    out: list[TextureReading] = []
    edges: dict[int, int] = {}
    for tid, where in slots_of.items():
        try:
            info = source.read_texture(int(tid))
        except Exception:  # noqa: BLE001 -- 读不出的贴图只影响它自己那一行
            continue
        width = int(getattr(info, "width", 0) or 0)
        height = int(getattr(info, "height", 0) or 0)
        out.append(
            TextureReading(
                path_id=int(tid),
                name=str(getattr(info, "name", "") or f"<texture {tid}>"),
                width=width,
                height=height,
                fmt=int(getattr(info, "format", 0) or 0),
                mip_count=int(getattr(info, "mip_count", 0) or 0),
                slots=tuple(sorted(set(where))),
            )
        )
        edges[int(tid)] = max(width, height)

    primary = str(getattr(texture_install, "PRIMARY_DESC", "Albedo"))
    reader = _SourceReader(source)
    base_edge = 0
    base_where = ""
    fallback_edge = 0
    fallback_where = ""
    for mid in material_ids:
        entry = tree.materials.get(int(mid)) or {}
        name = str(entry.get("name") or f"<material {mid}>")
        pid_of: dict[str, int] = {}
        for slot in entry.get("texture_slots") or ():
            pair = tuple(slot)[:2] if isinstance(slot, (tuple, list)) else ()
            if len(pair) >= 2 and pair[1]:
                pid_of[str(pair[0])] = int(pair[1])
        for slot_name, tex_id in pid_of.items():
            edge = edges.get(tex_id, 0)
            if edge > fallback_edge:
                fallback_edge = edge
                fallback_where = f"{name}/{slot_name}"
        try:
            layers = texture_install.color_layers(reader, source.read_typetree(int(mid)))
        except Exception:  # noqa: BLE001 -- desc 读不出 = 认不出，⛔ 不拿它当红判据
            layers = ()
        for slot_name, desc in layers:
            if str(desc) != primary:
                continue
            edge = edges.get(pid_of.get(str(slot_name), 0), 0)
            if edge > base_edge:
                base_edge = edge
                base_where = f"{name}/{slot_name}({desc})"
    if not base_edge and fallback_edge:
        base_edge = fallback_edge
        base_where = fallback_where + "（desc 读不出 ⇒ 回退成这一级里最大的一张）"
    return tuple(out), base_edge, base_where


