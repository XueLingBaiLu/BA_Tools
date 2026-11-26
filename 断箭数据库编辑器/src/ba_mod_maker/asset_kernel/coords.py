"""坐标契约：Unity(Y-up) <-> Blender(Z-up) 的**唯一**实现（设计稿 §4 D4 / §11 A2）。

## 口径（2026-10-03 改：**反射**，不是纯旋转）

Unity 是**左手系**（x 右 / y 上 / z 前），Blender 是**右手系**（x 右 / y 里 / z 上）。
把同一件实物放进这两套约定里，换算矩阵**必然是 det == -1 的反射** —— 用 det == +1
的纯旋转会把模型**镜像**：外形近似对称看不出来，只有文字/标识/舷号反
（US_AH-1Z Viper 尾梁的 "MARINES" 会读成 ƧƎИIЯAM）。

**反射会把每个三角形的朝向翻过去** ⇒ 读侧必须同时把绕序补回来
（reverse_winding()，接在 mesh.read_mesh 里），否则模型里外全反。
两件事缺一不可，判据是同一个绕序自洽率：只做反射 ⇒ frac 从 0.9996 掉到 0.0004；
反射 + 补绕序 ⇒ frac 仍是 0.9996，而文字正读。

## 判据

1. det(UNITY_TO_BLENDER) == **-1**（反射族）；
2. 互为逆：往返误差 **恰好 0.0**（实现里只有换位与取负，没有任何浮点运算）；
3. 真实网格绕序自洽率 frac == 1.0000（**在 read_mesh 补过绕序之后**），见 winding_agreement()。

## 怎么在被真数据否证之前站住（这段是这次踩出来的）

- 「det 该是 +1 还是 −1」**不能靠"左右手系"这类推导单独决定**，也不能只看绕序
  （绕序对镜像完全免疫）；要**两条一起量**：det 的符号 ＋ 真机上文字/标识读不读得正。
- 旧知识包 旧知识包/知识/.re-kb/tools/chirality-mirror-probes.md 记过同一个病，结论
  与本次实测一致；同目录 mirror-and-chirality-probes.md 曾用"面签"否定它 —— 那次是
  **只翻坐标没补绕序**，量到的"里外反"是补一半的症状，不是反射本身错了。
- 甲/乙式命名（沿用旧文）：**乙式 = (x, -z, y)** det=+1（镜子，本模块 2026-10-03 前用的）；
  **甲式 = (-x, -z, y)** det=−1（现行契约）。两者只差一个 X 号。

零第三方依赖：只用标准库。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Unity 坐标里的三个基向量。
_BASIS: tuple[tuple[float, float, float], ...] = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


# --------------------------------------------------------------------------- #
# 位置
# --------------------------------------------------------------------------- #
def unity_to_blender(v: Sequence[float]) -> tuple[float, float, float]:
    """Unity 位置 -> Blender 位置：(x, y, z) -> (-x, -z, y)（甲式，det == -1）。

    轴的角色仍与从前一致：y 上 -> z 上、z 前 -> -y 前；差别只在 **x 取负** ——
    正是这一号把"镜像模型"变成"实物"。⛔ 改它的同时必须保留读侧的绕序补正。
    """
    x, y, z = v[0], v[1], v[2]
    return (-x, -z, y)


def blender_to_unity(v: Sequence[float]) -> tuple[float, float, float]:
    """Blender 位置 -> Unity 位置：(x, y, z) -> (-x, z, -y)（上一条的精确逆）。"""
    x, y, z = v[0], v[1], v[2]
    return (-x, z, -y)


# --------------------------------------------------------------------------- #
# 四元数（Unity 存 (x, y, z, w)；Blender 存 (w, x, y, z)）
# --------------------------------------------------------------------------- #
def unity_to_blender_quat(q: Sequence[float]) -> tuple[float, float, float, float]:
    """Unity 四元数 (x, y, z, w) -> Blender 四元数 (w, x, z, -y)。

    怎么来的：换基 M_b = C · M_u · C⁻¹；四元数的**矢量部分是赝矢量**，
    在 det(C) == -1 的 C 下要多一个负号 —— 所以矢量部分是 −C·(x,y,z)，
    代入 C = 甲式（e_x→-e_x、e_y→e_z、e_z→-e_y）就得到 (x, z, -y)，w 不变。
    """
    x, y, z, w = q[0], q[1], q[2], q[3]
    return (w, x, z, -y)


def blender_to_unity_quat(q: Sequence[float]) -> tuple[float, float, float, float]:
    """Blender 四元数 (W, X, Y, Z) -> Unity 四元数 (x, y, z, w) = (X, -Z, Y, W)。

    ⚠ 别把这条写成和上一条同一个式子 —— 那个映射**不是对合**：
    上面给的是 (x,y,z,w) -> (w,x,z,-y)，所以要解出 (x,y,z,w) = (X, -Z, Y, W)。
    （W2 第一版就是抄了同一条式子，往返误差量出 1.997。）
    """
    w, x, y, z = q[0], q[1], q[2], q[3]
    return (x, -z, y, w)


# --------------------------------------------------------------------------- #
# 缩放
# --------------------------------------------------------------------------- #
def unity_to_blender_scale(s: Sequence[float]) -> tuple[float, float, float]:
    """Unity 缩放 -> Blender 缩放：(sx, sy, sz) -> (sx, sz, sy)。

    ⚠ 换成反射之后**这一条没变**（也别去"顺手"补个负号）：缩放矩阵是共轭 C·S·C⁻¹，
    C 是有符号置换（每列只有一个 ±1），负号在两边各出现一次、平方掉。
    实测：负缩放的节点（AH-1Z 的 turret_5 = (-1,1,1)）换基后仍是 (-1,1,1)。
    """
    sx, sy, sz = s[0], s[1], s[2]
    return (sx, sz, sy)


def blender_to_unity_scale(s: Sequence[float]) -> tuple[float, float, float]:
    """Blender 缩放 -> Unity 缩放（与上一条相同：只换位，自逆）。"""
    sx, sy, sz = s[0], s[1], s[2]
    return (sx, sz, sy)


# --------------------------------------------------------------------------- #
# 矩阵与行列式
# --------------------------------------------------------------------------- #
def unity_to_blender_matrix() -> tuple[tuple[float, float, float], ...]:
    """3x3 矩阵，**列** = 三个 Unity 基向量在 Blender 里的像（行主序返回）。"""
    cols = [unity_to_blender(e) for e in _BASIS]
    return tuple(tuple(cols[c][r] for c in range(3)) for r in range(3))


def determinant3(m: Sequence[Sequence[float]]) -> float:
    """3x3 行列式。"""
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def determinant_of_unity_to_blender() -> float:
    """**实算**出来的行列式（不是写死的 +1）。"""
    return determinant3(unity_to_blender_matrix())


# --------------------------------------------------------------------------- #
# 绕序
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class WindingReport:
    """一次绕序自洽性扫描的读数。

    frac 的定义：所有「非退化」三角形里，面法线（三条边的叉积）与**首顶点法线**
    同向的比例。退化三角形（叉积长度 0）与索引越界的三角形不计入分母。
    """

    meshes: int
    checked: int
    positive: int
    negative: int
    degenerate: int
    out_of_range: int

    @property
    def frac(self) -> float:
        return self.positive / self.checked if self.checked else 0.0


def _sub(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def winding_agreement(
    meshes: Iterable[object],
    *,
    transform: object | None = None,
    reverse: bool = False,
) -> WindingReport:
    """扫一批网格，量「面法线 vs 顶点法线」的自洽率。

    :param meshes: 每项要有 positions / normals / indices（见
        ba_mod_maker.asset_kernel.mesh.MeshData）。
    :param transform: 可选的坐标变换（默认 unity_to_blender，det == -1 的反射）。
        ⚠ 传进来的网格索引**要已经是补正过的**（read_mesh 出来就是这样）：反射 +
        补绕序两者相消，frac 才是 ~1.0000；只做反射会把 frac 打成 ~0.0000。
    :param reverse: True 时把每个三角形的后两个索引对调（**负控用**：正确的资产
        应当从 1.0000 直接翻成 0.0000）。

    面法线取 cross(p1 - p0, p2 - p0)，顶点法线取**首顶点** p0 的那一条 ——
    与设计稿 §11 A2 的探针同法，好让读数可以直接对拍。
    """
    fn = unity_to_blender if transform is None else transform
    n_meshes = 0
    positive = negative = degenerate = out_of_range = 0
    for mesh in meshes:
        positions = mesh.positions  # type: ignore[attr-defined]
        normals = mesh.normals  # type: ignore[attr-defined]
        indices = mesh.indices  # type: ignore[attr-defined]
        if len(positions) != len(normals):
            raise ValueError("positions 与 normals 长度不一致，顶点流解错了")
        mapped_p = [fn(p) for p in positions]
        mapped_n = [fn(n) for n in normals]
        n_meshes += 1
        for t in range(len(indices) // 3):
            a, b, c = indices[3 * t], indices[3 * t + 1], indices[3 * t + 2]
            if reverse:
                b, c = c, b
            if max(a, b, c) >= len(mapped_p) or min(a, b, c) < 0:
                out_of_range += 1
                continue
            face = _cross(_sub(mapped_p[b], mapped_p[a]), _sub(mapped_p[c], mapped_p[a]))
            length = math.sqrt(face[0] ** 2 + face[1] ** 2 + face[2] ** 2)
            if length < 1e-14:
                degenerate += 1
                continue
            normal = mapped_n[a]
            dot = (face[0] * normal[0] + face[1] * normal[1] + face[2] * normal[2]) / length
            if dot > 0:
                positive += 1
            else:
                negative += 1
    return WindingReport(
        meshes=n_meshes,
        checked=positive + negative,
        positive=positive,
        negative=negative,
        degenerate=degenerate,
        out_of_range=out_of_range,
    )


def reverse_winding(indices: Sequence[int]) -> tuple[int, ...]:
    """把每个三角形的后两个索引对调。

    两处用途，都是真用途：

    * **读侧补正**（mesh.read_mesh 用它）：契约是 det=-1 的反射，反射会把每个三角形
      的朝向翻过去，补回来面法线才朝外；
    * **负控夹具**：测绕序判据能不能红（倒一下，frac 必须从 1.0000 翻到 0.0000）。
    """
    out = list(indices)
    for t in range(len(out) // 3):
        out[3 * t + 1], out[3 * t + 2] = out[3 * t + 2], out[3 * t + 1]
    return tuple(out)
