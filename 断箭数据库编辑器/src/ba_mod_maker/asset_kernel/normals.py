"""法线契约：DXT5nm 的通道布局与「单位向量」判据（设计稿 §3.5 / §11 A3）。

游戏里的切线空间法线是 **DXT5nm**：X 存在 **A** 通道、Y 存在 **G** 通道，Z 要重建。
（普通 RGB 法线才是 X=R / Y=G。）本机实测的正控 / 负控读数：

| 通道对 | x^2+y^2>1 占比 | 判定 |
|---|---|---|
| **(A, G)** DXT5nm | 0.0014 ~ 0.02 | 成立（< 0.03） |
| (R, G) 当成 X,Y | **1.0000** | 否（R 通道恒为 255，x 永远 = 1） |

判据本身只用到「法线必须是单位向量」这一条，不需要知道 shader 怎么用：
把两个候选通道对各自的 x^2+y^2>1 占比量出来，能过的只有一个。

## 为什么用直方图而不是逐像素

逐像素在纯 Python 里要过两百多万个点；判据只依赖**通道取值对** (x, y) 而**不依赖位置**，
所以先数一张**联合**直方图（键 = (x, y)，最多 65536 桶），再对每个取值对判一次 ——
结果与逐像素逐位相同（相等只可能发生在 (2X-255)^2+(2Y-255)^2 == 255^2 上，
而左边是两个奇数平方和 ≡ 2 (mod 4)，右边 255^2 ≡ 1 (mod 4)，**无解**，
所以不存在浮点边界歧义）。本机实测 0.086 s / 百万像素。

⛔ 别退化成两边的**边际**直方图 —— 那样等于假设两个通道独立（踩过，见函数 docstring）。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

#: DXT5nm：X 在 A 通道、Y 在 G 通道。
DXT5NM_X_CHANNEL = "A"
DXT5NM_Y_CHANNEL = "G"
#: 反例（负控）：把 (R, G) 当成 X, Y —— 本机实测占比 1.0000。
RGB_X_CHANNEL = "R"
RGB_Y_CHANNEL = "G"

#: RGBA 交错字节里的通道下标。
CHANNEL_INDEX: dict[str, int] = {"R": 0, "G": 1, "B": 2, "A": 3}


@dataclass(frozen=True, slots=True)
class UnitRangeReport:
    """一条 x^2+y^2>1 的读数。"""

    total: int
    bad: int

    @property
    def frac(self) -> float:
        return self.bad / self.total if self.total else 0.0


def channel_bytes(rgba: bytes, channel: str) -> bytes:
    """从 RGBA 交错字节里切出一个通道（保持逐行顺序，字节数 = 像素数）。"""
    index = CHANNEL_INDEX[channel.upper()]
    return rgba[index::4]


def unit_out_of_range_fraction(x: bytes, y: bytes) -> UnitRangeReport:
    """把两个 8 位通道当成 x / y（各映射到 [-1, 1]），数 x^2+y^2 > 1 的**像素**占比。

    ⚠ 这里数的是像素，条件是**逐像素**判 (X_i, Y_i) 这一对，所以必须用**联合**直方图。
    （踩过的坑：先按两边的**边际**直方图求和 —— 那等于假设两个通道独立，
    512x512 的正控会量出 frac=6170 这种不可能的数。联合直方图本机实测 0.086 s/百万像素。）
    """
    if len(x) != len(y):
        raise ValueError(f"两个通道的像素数不同：{len(x)} vs {len(y)}")
    bad = 0
    for (xv, yv), count in Counter(zip(x, y, strict=True)).items():
        fx = (xv / 255.0) * 2.0 - 1.0
        fy = (yv / 255.0) * 2.0 - 1.0
        if fx * fx + fy * fy > 1.0:
            bad += count
    return UnitRangeReport(total=len(x), bad=bad)


def reconstruct_dxt5nm(x: int, y: int) -> tuple[float, float, float]:
    """从 A 通道的 x、G 通道的 y（0..255）重建单位法线 (x, y, z)。

    z 的符号取正（切线空间法线朝外）；x^2+y^2 >= 1 时 z = 0（**不**归一化假装没事，
    那条读数由 unit_out_of_range_fraction 负责报出来）。
    """
    fx = (x / 255.0) * 2.0 - 1.0
    fy = (y / 255.0) * 2.0 - 1.0
    z2 = 1.0 - fx * fx - fy * fy
    return (fx, fy, math.sqrt(z2) if z2 > 0.0 else 0.0)


def dxt5nm_report(rgba: bytes) -> UnitRangeReport:
    """按 DXT5nm 口径量一张 RGBA 图的单位向量自洽率（X 取 A、Y 取 G）。"""
    return unit_out_of_range_fraction(
        channel_bytes(rgba, DXT5NM_X_CHANNEL), channel_bytes(rgba, DXT5NM_Y_CHANNEL)
    )


def rgb_report(rgba: bytes) -> UnitRangeReport:
    """按普通 RGB 口径量同一张图（**负控**：正确的 DXT5nm 图在这里应当几乎是 1.0）。"""
    return unit_out_of_range_fraction(
        channel_bytes(rgba, RGB_X_CHANNEL), channel_bytes(rgba, RGB_Y_CHANNEL)
    )

# --------------------------------------------------------------------------- #
# DXT5nm -> 标准 RGB 切线空间法线（W4 加：Blender 的 Normal Map 节点吃的是 RGB 法线）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def dxt5nm_z_lut() -> tuple[float, ...]:
    """(x, y) 两个字节 -> z 的查表（256x256 = 65536 项）。

    **公式只出现在 reconstruct_dxt5nm 里**；这张表只是它的一次性求值缓存
    （逐像素调上百万次那个函数没必要，读数完全相同：z 只依赖 (x, y) 两个字节）。
    """
    return tuple(reconstruct_dxt5nm(x, y)[2] for x in range(256) for y in range(256))


def dxt5nm_rgb_pixel(x: int, y: int, z: float) -> tuple[int, int, int]:
    """(x, y 为 0..255，z 为 0..1) -> 标准切线空间法线的 RGB 字节。"""
    return (x & 0xFF, y & 0xFF, int(round((z + 1.0) * 0.5 * 255.0)))


def dxt5nm_to_rgb_normal(rgba: bytes) -> bytes:
    """DXT5nm 图（X 在 A、Y 在 G）-> 标准切线空间法线 RGBA（R=x, G=y, B=(z+1)/2, A=255）。

    为什么需要它：Blender 的 Normal Map 节点吃的是**普通 RGB 法线**（R=x / G=y / B=z），
    把 DXT5nm 原图直接接进去，x 会从 B 通道读（本作实测 B 恒约 127）=> 法线全错。
    而 z 的公式**只许有一份**（就是 reconstruct_dxt5nm），所以这里做的是"按公式烘一张"，
    不是"在节点里再写一遍公式"。

    ⚠ 代价：纯 Python 逐像素，实测量级约 1 s / 百万像素（1024^2 约 1 s，2048^2 约 4 s）。
    派生图不进 round-trip（W5/W6 写回用的是**原始字节**，不是这张烘出来的图）。
    """
    if len(rgba) % 4:
        raise ValueError(f"RGBA 字节数不是 4 的倍数：{len(rgba)}")
    lut = dxt5nm_z_lut()
    out = bytearray(len(rgba))
    for index in range(0, len(rgba), 4):
        x = rgba[index + 3]  # DXT5nm：X 在 A
        y = rgba[index + 1]  # DXT5nm：Y 在 G
        out[index] = x
        out[index + 1] = y
        out[index + 2] = int(round((lut[(x << 8) | y] + 1.0) * 0.5 * 255.0))
        out[index + 3] = 255
    return bytes(out)

