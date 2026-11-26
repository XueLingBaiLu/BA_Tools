"""A5 并排比对：Blender 渲染 vs 游戏截图（主判据＝图案/标记的 UV 级对应）。

## 为什么不用色相掩码（设计稿 §11 自曝它循环论证）
色相掩码自己限定了色相，再拿它去比「色相一致」是同义反复，还会把树叶圈进来。
所以主判据是**图案 / 标记的位置与形状**，且图案**只来自基色贴图**：

  1. 从 scene.json 的几何**自己光栅化**（口径 A）：得到覆盖掩码 + 每像素 UV；
  2. 在**纹理空间**算标记边缘图（3x3 Laplacian + 分位阈值）；
  3. 用每像素 UV 采样基色图 ⇒ 预测屏幕反照率 ⇒ 预测标记 pred
     （纹理空间边缘按 UV 搬过来那层 ∪ 预测反照率自己的边缘层）；
  4. 从 --game 在剪影内算观测边缘 obs（Laplacian 对单调 tonemap 近似不变）；
  5. pred 按对齐变换搬到游戏帧，pattern_f1 = F1(dilate(pred), dilate(obs))。

色度（去亮度）只作**辅助读数**，不进 verdict。

## 两条自证（不做就是假绿，工单 §7 坑 5 / 交接 §8）
- **光栅器自证**：光栅覆盖掩码 vs --render 的 alpha，读数 raster_alpha_iou；
  **>= 0.98 才允许进 pattern_f1**，不到 ⇒ INVALID 并报清卡在哪。⛔ 不许靠翻转凑。
- **反空转**：pred_mark_px == 0 或 obs_edge_px == 0 ⇒ FAIL（⛔ 不许 PASS）。

## 几何口径（Lead 冻结 2026-10-04，一条都不许自己另发明）
    world = objects[].matrix_world @ [x, y, z, 1]   # 行主序 M@v；局部坐标必须乘
    clip  = projection @ camera_matrix_world @ world
    ndc_x = clip.x/clip.w ;  ndc_y = clip.y/clip.w
    px = (ndc_x*0.5 + 0.5) * W ;  py = (0.5 - ndc_y*0.5) * H   # 左上为原点
    透视正确插值：按 1/w 加权插 u/w、v/w，再除以插值后的 1/w
    gating：w > 0 且 clip_start <= w <= clip_end；⛔ 不做背面剔除
    采样：u %= 1、v %= 1；tx = u*(tex_w-1)、ty = (1-v)*(tex_h-1)

## 相机链（实测踩过，别再踩）
Blender 的 `camera.matrix_world` 是**相机→世界**（里面带相机位置）。口径 A 的字面写法
`clip = projection @ camera.matrix_world @ world` 对真实数据会**全剔光**：
实测 `_work/w11_stage/scene.json`（RU_T72B，63,200 个三角形）→ **0/63200** 过 gating、
每个顶点 w_clip = -2.29；换成 `projection @ inverse(camera.matrix_world) @ world`
→ 63,185/63200、覆盖 122,898、**raster_alpha_iou = 0.9977**（拿 --render 的 alpha 当尺子）。
所以本工具**两条都算**，取 raster_alpha_iou 高的那条，两条读数都进 lines[] ——
⛔ 这不是「翻一下凑上去」：选错的代价是 INVALID（0.98 那道门），不是 PASS。

## CLI 与退出码
    python tools/compare_side_by_side.py --render R --scene <scene.json> --game G \
        --texture T --out-dir D [--control-texture C] [--game-box x0,y0,x1,y1] \
        [--game-mask M] [--json J]
--uv <path> 是 --scene 的旧别名（同一份 scene.json；口径 A 已废 UV pass PNG）。
stdout 末段 JSON 夹在 BAMOD_COMPARE_BEGIN / BAMOD_COMPARE_END 之间（同 selftest.py）。
0 = PASS / 1 = FAIL / 2 = INVALID。产物：<out-dir>/side_by_side.png + compare.json。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

BEGIN = "BAMOD_COMPARE_BEGIN"
END = "BAMOD_COMPARE_END"
SCHEMA = 1

#: 光栅器自证门限：不到就 INVALID（不许进 pattern_f1）。
RASTER_ALPHA_IOU_MIN = 0.98
#: 只有 best IoU 达这条线才允许判 PASS；不到 ⇒ INVALID。
ALIGN_IOU_MIN = 0.80
#: 主判据通过线。
PATTERN_F1_MIN = 0.50
#: 正控必须比本尊低这么多才算「能红」。
CONTROL_MARGIN = 0.05
#: 对齐搜索画布边长（面积归一化后）/ 画布上限 / UV 采样工作边长上限。
#: ⚠ 面积归一化后**长边会超出 192**（细长剪影），画布必须跟着放大，否则剪影被裁掉。
WORK_MASK_SIDE = 192
WORK_MASK_CAP = 512
WORK_PATTERN_SIDE = 640
#: 光栅像素上限（超了等比缩小光栅与 alpha，并在 lines[] 记下来）。
RASTER_MAX_PX = 2_400_000
#: 边缘切法用 Otsu（密度自适应）；低于 EDGE_FLOOR 算「这一侧没有结构」= 空转。
EDGE_FLOOR = 6
#: 色度辅助读数的取样边长上限 / 三联图每格高度。
CHROMA_SIDE = 320
PANEL_HEIGHT = 360
#: 只取「--texture 对应材质」的像素时，覆盖占比太低就退回整个剪影。
REGION_MIN_SHARE = 0.05
#: 游戏截图先压到这个长边再做分割/对齐/观测边缘（2560x1440 原尺寸纯 Python 太慢）。
GAME_WORK_SIDE = 1000
#: 相机反推：粗搜分辨率 / 三角形抽稀步长；细搜步长更小。
FIT_COARSE_SIDE = 160
FIT_COARSE_STRIDE = 8
FIT_REFINE_STRIDE = 2
#: 相机反推里 2D 相似搜索的画布边长。
FIT_CANVAS_SIDE = 96
#: Blender 默认 sensor_width（sensor_fit=AUTO ⇒ 长边配 36mm）。
SENSOR_WIDTH_MM = 36.0

Vec = tuple[float, float, float, float, float, float]
Mat = list[list[float]]


class InvalidInputError(Exception):
    """输入不成立：坏路径 / 空图 / 分不出剪影 / 光栅自证不过 / 对不齐 ⇒ 退出码 2。"""

    def __init__(
        self,
        message: str,
        lines: list[str] | None = None,
        raster_alpha_iou: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.lines: list[str] = list(lines or [])
        #: INVALID 也要把**量到的**读数带出去（别让面板看到 0.0 以为是没量）
        self.raster_alpha_iou = raster_alpha_iou


# --------------------------------------------------------------------------
# 基础小工具（纯 Pillow / 标准库，不依赖 numpy）
# --------------------------------------------------------------------------


def _lut_above(threshold: int) -> bytes:
    """255/0 查找表：> threshold 的算 1。"""
    return bytes(0 if value <= threshold else 255 for value in range(256))


def _lut_below(threshold: int) -> bytes:
    """255/0 查找表：< threshold 的算 1（暗部）。"""
    return bytes(255 if value < threshold else 0 for value in range(256))


def _count_on(img: Image.Image) -> int:
    return img.histogram()[255]


def _threshold_edges(
    img: Image.Image, mask: Image.Image | None, floor: int
) -> tuple[Image.Image | None, int]:
    """Otsu 切「平坦 / 边缘」；阈值低于 floor ⇒ (None, 阈值)，明写这一侧空转。

    为什么不用固定分位：基色图常常**大片平涂**（实测合成夹具 77% 的像素梯度恰为 0），
    固定 92% 分位会切出阈值 0 ⇒ 预测标记全空，就是一把空转的尺子。
    实测同一张图：分位给 0 个标记像素，Otsu 给 1183 个。
    """
    hist = img.histogram(mask) if mask is not None else img.histogram()
    value = _otsu(hist)
    if value < floor:
        return None, value
    out = img.point(_lut_above(value))
    if mask is not None:
        out = ImageChops.multiply(out, mask)
    return out, value


def _edge_map(img: Image.Image) -> Image.Image:
    """逐通道取最大的 3x3 边缘幅度：抗色偏，也不吃光照的单调变换。"""
    edges = img.convert("RGB").filter(ImageFilter.FIND_EDGES)
    red, green, blue = edges.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def _dilate(img: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return img
    return img.filter(ImageFilter.MaxFilter(2 * radius + 1))


def _erode(img: Image.Image, radius: int) -> Image.Image:
    if radius <= 0:
        return img
    return img.filter(ImageFilter.MinFilter(2 * radius + 1))


def _close(img: Image.Image, radius: int) -> Image.Image:
    return _erode(_dilate(img, radius), radius)


def _clear_border(img: Image.Image, width: int) -> Image.Image:
    """把最外 width 像素清 0。

    ⚠ FIND_EDGES 在图像边界上把越界邻居当 0 ⇒ 任何非黑背景都会在**画框边**得到一圈
    255 的假边缘（实测：它把坦克与画框连成一个连通块，game mask 的 bbox 变成整幅图，
    真值相机的对齐 IoU 从 0.95+ 掉到 0.28）。
    """
    if width <= 0 or img.width <= 2 * width or img.height <= 2 * width:
        return img
    out = Image.new("L", img.size, 0)
    out.paste(img.crop((width, width, img.width - width, img.height - width)), (width, width))
    return out


def _flood(
    src: bytes, seen: bytearray, w: int, h: int, seed: int, out: bytearray | None = None
) -> tuple[int, bytearray]:
    """4 邻域洪水填充；给了 out 就顺手写出去（省一份大列表）。"""
    total = w * h
    stack = [seed]
    seen[seed] = 1
    count = 0
    while stack:
        index = stack.pop()
        count += 1
        if out is not None:
            out[index] = 255
        x = index % w
        if x and src[index - 1] and not seen[index - 1]:
            seen[index - 1] = 1
            stack.append(index - 1)
        if x + 1 < w and src[index + 1] and not seen[index + 1]:
            seen[index + 1] = 1
            stack.append(index + 1)
        if index >= w and src[index - w] and not seen[index - w]:
            seen[index - w] = 1
            stack.append(index - w)
        if index + w < total and src[index + w] and not seen[index + w]:
            seen[index + w] = 1
            stack.append(index + w)
    return count, seen


def _largest_component(mask: Image.Image) -> Image.Image:
    """只留最大连通块：割掉背景噪声与零散假前景。"""
    w, h = mask.size
    src = mask.tobytes()
    seen = bytearray(w * h)
    best_seed, best_size = -1, 0
    index = src.find(255)
    while index != -1:
        if not seen[index]:
            size, seen = _flood(src, seen, w, h, index)
            if size > best_size:
                best_seed, best_size = index, size
        index = src.find(255, index + 1)
    if best_seed < 0:
        raise InvalidInputError("剪影为空：找不到任何前景像素")
    out = bytearray(w * h)
    _flood(src, bytearray(w * h), w, h, best_seed, out)
    return Image.frombytes("L", (w, h), bytes(out))


def _otsu(hist: list[int]) -> int:
    """Otsu 阈值；作用在「与背景统计的距离图」上，⛔ 不含色相。"""
    total = sum(hist)
    if total <= 0:
        return 0
    sum_all = sum(value * count for value, count in enumerate(hist))
    weight_bg, sum_bg = 0, 0.0
    best_value, best_variance = 0, -1.0
    for value, count in enumerate(hist):
        weight_bg += count
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += value * count
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_all - sum_bg) / weight_fg
        variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if variance > best_variance:
            best_value, best_variance = value, variance
    return best_value


# --------------------------------------------------------------------------
# 仿射（2x3）：x' = a*x + b*y + c，y' = d*x + e*y + f
# --------------------------------------------------------------------------


def _compose(outer: Vec, inner: Vec) -> Vec:
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + b1 * d2,
        a1 * b2 + b1 * e2,
        a1 * c2 + b1 * f2 + c1,
        d1 * a2 + e1 * d2,
        d1 * b2 + e1 * e2,
        d1 * c2 + e1 * f2 + f1,
    )


def _invert(m: Vec) -> Vec:
    a, b, c, d, e, f = m
    det = a * e - b * d
    if abs(det) < 1e-12:
        raise InvalidInputError("对齐变换退化（行列式 0）")
    return (e / det, -b / det, (b * f - e * c) / det, -d / det, a / det, (d * c - a * f) / det)


def _warp(img: Image.Image, forward: Vec, size: tuple[int, int], resample: int) -> Image.Image:
    """PIL 的 AFFINE 要「输出→输入」的逆映射。"""
    a, b, c, d, e, f = _invert(forward)
    return img.transform(size, Image.AFFINE, (a, b, c, d, e, f), resample=resample)


def _scale_down(size: tuple[int, int], cap: int) -> tuple[tuple[int, int], float]:
    width, height = size
    longest = max(width, height)
    if longest <= cap:
        return (width, height), 1.0
    factor = cap / longest
    return (max(1, round(width * factor)), max(1, round(height * factor))), factor


def _cap_size(size: tuple[int, int], cap: int) -> tuple[int, int]:
    width, height = size
    longest = max(width, height)
    if longest <= cap:
        return (width, height)
    factor = cap / longest
    return (max(8, round(width * factor)), max(8, round(height * factor)))


def _cap_image(img: Image.Image, cap: int) -> Image.Image:
    size = _cap_size(img.size, cap)
    return img if size == img.size else img.resize(size, Image.BILINEAR)


def _shrink_matrix(size: tuple[int, int], small: tuple[int, int]) -> Vec:
    """整帧坐标 → 缩小帧坐标（像素中心对齐的近似）。"""
    sx = small[0] / size[0]
    sy = small[1] / size[1]
    return (sx, 0.0, (sx - 1.0) * 0.5, 0.0, sy, (sy - 1.0) * 0.5)


# --------------------------------------------------------------------------
# scene.json 读取（口径：Lead 2026-10-04 冻结；字段名宽容别名，缺键明报）
# --------------------------------------------------------------------------

IDENTITY: Mat = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


@dataclass
class SceneObject:
    name: str
    material: str
    matrix: Mat
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    uvs: list[tuple[float, float]]


@dataclass
class Scene:
    objects: list[SceneObject]
    projection: Mat
    camera: Mat
    clip_start: float
    clip_end: float
    resolution: tuple[int, int] | None
    textures: dict[str, str] = field(default_factory=dict)


def _read_json(path: str, what: str) -> object:
    target = Path(path)
    if not target.is_file():
        raise InvalidInputError(f"路径不存在：{path}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise InvalidInputError(f"{what} 读不出：{path}（{type(exc).__name__}: {exc}）") from exc


def _mat4(raw: object, where: str) -> Mat:
    if isinstance(raw, dict):
        raw = raw.get("matrix") or raw.get("values") or raw.get("rows") or raw.get("data")
    if not isinstance(raw, (list, tuple)):
        raise InvalidInputError(f"scene.json 的 {where} 不是矩阵（{type(raw).__name__}）")
    if len(raw) == 4 and all(isinstance(row, (list, tuple)) for row in raw):
        rows = [[float(value) for value in row] for row in raw]
    elif len(raw) == 16:
        flat = [float(value) for value in raw]
        rows = [flat[index * 4 : index * 4 + 4] for index in range(4)]
    else:
        raise InvalidInputError(f"scene.json 的 {where} 长度 {len(raw)}，既不是 4x4 也不是 16")
    if any(len(row) != 4 for row in rows):
        raise InvalidInputError(f"scene.json 的 {where} 每行不是 4 个数")
    return rows


def _mat_apply(
    matrix: Mat, vector: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return tuple(sum(matrix[i][j] * vector[j] for j in range(4)) for i in range(4))  # type: ignore[return-value]


def _invert_mat4(matrix: Mat) -> Mat | None:
    """4x4 求逆（Gauss-Jordan + 部分主元）；奇异返回 None。"""
    rows = [list(matrix[i]) + [1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [value / scale for value in rows[col]]
        for other in range(4):
            if other != col and rows[other][col]:
                factor = rows[other][col]
                rows[other] = [
                    a - factor * b for a, b in zip(rows[other], rows[col], strict=True)
                ]
    return [row[4:] for row in rows]


def _mat_mul(left: Mat, right: Mat) -> Mat:
    return [[sum(left[i][k] * right[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _float_rows(raw: object, width: int, where: str) -> list[tuple[float, ...]]:
    """读 [x,y,z] / [u,v] 这类**浮点**行；⛔ 不许 int() —— 顶点与 UV 都是浮点。

    实测踩过：早期版本拿 int() 读，UV 0.1~0.7 全被截成 0，
    于是「每像素 UV 采样」退化成永远取贴图 (0,0) 一个纹素（假绿）。
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        raise InvalidInputError(f"scene.json 的 {where} 是空或缺")
    if isinstance(raw[0], (list, tuple)):
        rows = [tuple(float(value) for value in item) for item in raw]
    else:
        flat = [float(value) for value in raw]
        if len(flat) % width:
            raise InvalidInputError(
                f"scene.json 的 {where} 扁平长度 {len(flat)} 不是 {width} 的倍数"
            )
        rows = [tuple(flat[i : i + width]) for i in range(0, len(flat), width)]
    if any(len(row) != width for row in rows):
        raise InvalidInputError(f"scene.json 的 {where} 每行不是 {width} 个数")
    return rows


def _index_rows(raw: object, where: str) -> list[tuple[int, int, int]]:
    rows = _float_rows(raw, 3, where)
    return [(int(row[0]), int(row[1]), int(row[2])) for row in rows]


def read_scene(path: str) -> Scene:
    data = _read_json(path, "scene.json")
    if not isinstance(data, dict):
        raise InvalidInputError("scene.json 顶层不是对象")
    camera_raw = data.get("camera") or data.get("Camera") or {}
    if not isinstance(camera_raw, dict):
        camera_raw = {}
    projection_raw = camera_raw.get("projection") or camera_raw.get("projection_matrix")
    if projection_raw is None:
        projection_raw = data.get("projection") or data.get("projection_matrix")
    if projection_raw is None:
        raise InvalidInputError("scene.json 里找不到 camera.projection")
    projection = _mat4(projection_raw, "camera.projection")
    camera_matrix = camera_raw.get("matrix_world") or camera_raw.get("matrix") or IDENTITY
    camera = _mat4(camera_matrix, "camera.matrix_world")
    clip_start = float(camera_raw.get("clip_start", data.get("clip_start", 0.0)) or 0.0)
    clip_end = float(camera_raw.get("clip_end", data.get("clip_end", 1e30)) or 1e30)
    resolution_raw = (
        data.get("resolution") or camera_raw.get("resolution") or camera_raw.get("resolution_xy")
    )
    resolution = None
    if isinstance(resolution_raw, (list, tuple)) and len(resolution_raw) == 2:
        resolution = (int(resolution_raw[0]), int(resolution_raw[1]))
    objects_raw = data.get("objects") or data.get("meshes") or []
    if not isinstance(objects_raw, (list, tuple)) or not objects_raw:
        raise InvalidInputError("scene.json 里 objects 为空")
    objects: list[SceneObject] = []
    for order, raw_object in enumerate(objects_raw):
        if not isinstance(raw_object, dict):
            continue
        vertices_raw = (
            raw_object.get("vertices") or raw_object.get("verts") or raw_object.get("positions")
        )
        triangles_raw = (
            raw_object.get("triangles")
            or raw_object.get("faces")
            or raw_object.get("tris")
            or raw_object.get("indices")
        )
        if vertices_raw is None or triangles_raw is None:
            continue
        where = f"objects[{order}]"
        vertices = [
            (row[0], row[1], row[2]) for row in _float_rows(vertices_raw, 3, f"{where}.vertices")
        ]
        triangles = _index_rows(triangles_raw, f"{where}.triangles")
        uv_raw = raw_object.get("uv") or raw_object.get("uvs") or raw_object.get("uv_layers")
        if isinstance(uv_raw, dict):
            uv_raw = uv_raw.get("uv") or uv_raw.get("data") or uv_raw.get("values")
        if uv_raw is None:
            raise InvalidInputError(f"scene.json 的 {where} 没有 uv（主判据要靠它采样基色图）")
        uvs = [(row[0], row[1]) for row in _float_rows(uv_raw, 2, f"{where}.uv")]
        if len(uvs) != len(vertices):
            raise InvalidInputError(
                f"scene.json 的 {where}.uv 有 {len(uvs)} 条，与顶点数 {len(vertices)} 不等"
                "（口径：uv[i] 就是 vertices[i] 的 UV）"
            )
        matrix_raw = raw_object.get("matrix_world") or raw_object.get("matrix") or IDENTITY
        objects.append(
            SceneObject(
                name=str(raw_object.get("name", f"object{order}")),
                material=str(raw_object.get("material", "") or ""),
                matrix=_mat4(matrix_raw, f"{where}.matrix_world"),
                vertices=vertices,
                triangles=triangles,
                uvs=uvs,
            )
        )
    if not objects:
        raise InvalidInputError("scene.json 里没有任何带 vertices/triangles 的对象")
    textures_raw = data.get("textures") or {}
    textures = (
        {str(key): str(value) for key, value in textures_raw.items()}
        if isinstance(textures_raw, dict)
        else {}
    )
    return Scene(
        objects=objects,
        projection=projection,
        camera=camera,
        clip_start=clip_start,
        clip_end=clip_end,
        resolution=resolution,
        textures=textures,
    )


# --------------------------------------------------------------------------
# 光栅化（口径 A：透视正确插值 + w 深度，不剔背面）
# --------------------------------------------------------------------------


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def orbit_camera(
    azimuth_deg: float,
    elevation_deg: float,
    distance: float,
    focal_mm: float,
    resolution: tuple[int, int],
    target: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[Mat, Mat]:
    """与 blender_addon/reference_stage.py::setup_camera 同口径（已与真 scene.json 对数）。

    · 位置 = target + d*(cos(el)cos(az), cos(el)sin(az), sin(el))
    · 朝向 = to_track_quat("-Z","Y")：+Z 轴 = 位置-目标，+Y 尽量贴世界 +Z，X = YxZ
    · 投影 = calc_matrix_camera（sensor_fit=AUTO ⇒ 36mm 配长边），clip 0.1/100
      实测对齐（scene.json，focal=50 / 960x540）：P[0][0]=2.777778、P[1][1]=4.938272、
      P[2][2]=-1.002002、P[2][3]=-0.200200、P[3][2]=-1.0
    """
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    location = (
        target[0] + distance * math.cos(el) * math.cos(az),
        target[1] + distance * math.cos(el) * math.sin(az),
        target[2] + distance * math.sin(el),
    )
    z_axis = _unit((location[0] - target[0], location[1] - target[1], location[2] - target[2]))
    reference = (0.0, 0.0, 1.0) if abs(z_axis[2]) < 0.9999 else (0.0, 1.0, 0.0)
    dot = sum(reference[index] * z_axis[index] for index in range(3))
    lifted = tuple(reference[index] - dot * z_axis[index] for index in range(3))
    y_axis = _unit((lifted[0], lifted[1], lifted[2]))
    x_axis = _cross(y_axis, z_axis)
    camera: Mat = [
        [x_axis[0], y_axis[0], z_axis[0], location[0]],
        [x_axis[1], y_axis[1], z_axis[1], location[1]],
        [x_axis[2], y_axis[2], z_axis[2], location[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]
    width, height = resolution
    focal_px = max(width, height) * focal_mm / SENSOR_WIDTH_MM
    near, far = 0.1, 100.0
    projection: Mat = [
        [2.0 * focal_px / width, 0.0, 0.0, 0.0],
        [0.0, 2.0 * focal_px / height, 0.0, 0.0],
        [0.0, 0.0, -(far + near) / (far - near), -2.0 * far * near / (far - near)],
        [0.0, 0.0, -1.0, 0.0],
    ]
    return camera, projection


def _camera_candidates(scene: Scene) -> list[tuple[str, Scene]]:
    """两条候选相机链：口径 A 的字面链 + Blender 语义的逆链。

    实测（_work/w11_stage/scene.json，RU_T72B 63,200 个三角形）：
    字面链 **0/63200** 个三角形过 gating（每个顶点 w_clip = -2.29，因为 Blender 的
    matrix_world 是**相机→世界**，里面有平移 (9.30, 9.30, 4.79)）；
    逆链 raster_alpha_iou=0.9977、覆盖 122,898 vs alpha 122,632。
    哪条对**由 --render 的 alpha 自证**（门限 0.98），两条读数都进 lines[]。
    """
    literal = ("projection@matrix_world@world", scene)
    inverse_matrix = _invert_mat4(scene.camera)
    if inverse_matrix is None:
        return [literal]
    return [
        literal,
        (
            "projection@inverse(matrix_world)@world",
            Scene(
                objects=scene.objects,
                projection=scene.projection,
                camera=inverse_matrix,
                clip_start=scene.clip_start,
                clip_end=scene.clip_end,
                resolution=scene.resolution,
                textures=scene.textures,
            ),
        ),
    ]


@dataclass
class Raster:
    size: tuple[int, int]
    coverage: Image.Image
    covered: bytes
    uv_x: list[float]
    uv_y: list[float]
    group: list[int]
    groups: list[str]
    triangles_kept: int
    triangles_skipped: int


def _raster_target(size: tuple[int, int]) -> tuple[tuple[int, int], float]:
    width, height = size
    pixels = width * height
    if pixels <= RASTER_MAX_PX:
        return (width, height), 1.0
    factor = math.sqrt(RASTER_MAX_PX / pixels)
    return (max(32, int(width * factor)), max(32, int(height * factor))), factor


def rasterize(scene: Scene, size: tuple[int, int], triangle_stride: int = 1) -> Raster:
    """把 scene.json 的三角形画成覆盖掩码 + 每像素 UV + 每像素材质组。"""
    width, height = size
    total = width * height
    depth = array("f", [1e30]) * total
    uv_x = array("f", bytes(4 * total))
    uv_y = array("f", bytes(4 * total))
    group = array("H", bytes(2 * total))
    coverage = bytearray(total)
    view = _mat_mul(scene.projection, scene.camera)
    groups: list[str] = []
    group_index: dict[str, int] = {}
    kept, skipped = 0, 0
    for obj in scene.objects:
        key = obj.material or obj.name
        if key not in group_index:
            group_index[key] = len(groups)
            groups.append(key)
        gid = group_index[key]
        model_view = _mat_mul(view, obj.matrix)
        for order, tri in enumerate(obj.triangles):
            if triangle_stride > 1 and order % triangle_stride:
                continue
            if max(tri) >= len(obj.vertices):
                raise InvalidInputError(f"scene.json 的三角形下标越界：{tri}")
            projected = []
            ok = True
            for index in tri:
                vertex = obj.vertices[index]
                clip = _mat_apply(model_view, (vertex[0], vertex[1], vertex[2], 1.0))
                w = clip[3]
                if w <= 0.0 or w < scene.clip_start or w > scene.clip_end:
                    ok = False
                    break
                inverse_w = 1.0 / w
                u, v = obj.uvs[index]
                projected.append(
                    (
                        (clip[0] * inverse_w * 0.5 + 0.5) * width,
                        (0.5 - clip[1] * inverse_w * 0.5) * height,
                        inverse_w,
                        u * inverse_w,
                        v * inverse_w,
                    )
                )
            if not ok:
                skipped += 1
                continue
            (x0, y0, iw0, uow0, vow0) = projected[0]
            (x1, y1, iw1, uow1, vow1) = projected[1]
            (x2, y2, iw2, uow2, vow2) = projected[2]
            det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(det) < 1e-12:
                continue
            kept += 1
            inverse_det = 1.0 / det
            i0 = max(0, int(math.floor(min(x0, x1, x2) - 0.5)))
            i1 = min(width - 1, int(math.ceil(max(x0, x1, x2) - 0.5)))
            j0 = max(0, int(math.floor(min(y0, y1, y2) - 0.5)))
            j1 = min(height - 1, int(math.ceil(max(y0, y1, y2) - 0.5)))
            if i0 > i1 or j0 > j1:
                continue
            for j in range(j0, j1 + 1):
                yy = j + 0.5
                base = j * width
                for i in range(i0, i1 + 1):
                    xx = i + 0.5
                    l0 = ((x1 - xx) * (y2 - yy) - (x2 - xx) * (y1 - yy)) * inverse_det
                    l1 = ((x2 - xx) * (y0 - yy) - (x0 - xx) * (y2 - yy)) * inverse_det
                    l2 = 1.0 - l0 - l1
                    if l0 < -1e-6 or l1 < -1e-6 or l2 < -1e-6:
                        continue
                    index = base + i
                    weight = l0 * iw0 + l1 * iw1 + l2 * iw2
                    if weight <= 1e-12:
                        continue
                    coverage[index] = 255
                    candidate = 1.0 / weight
                    if candidate < depth[index]:
                        depth[index] = candidate
                        uv_x[index] = (l0 * uow0 + l1 * uow1 + l2 * uow2) / weight
                        uv_y[index] = (l0 * vow0 + l1 * vow1 + l2 * vow2) / weight
                        group[index] = gid
    return Raster(
        size=size,
        coverage=Image.frombytes("L", size, bytes(coverage)),
        covered=bytes(coverage),
        uv_x=uv_x,
        uv_y=uv_y,
        group=group,
        groups=groups,
        triangles_kept=kept,
        triangles_skipped=skipped,
    )



# --------------------------------------------------------------------------
# 剪影：渲染侧 = 光栅覆盖；游戏侧 = --game-mask，否则非色相分割
# --------------------------------------------------------------------------


def _req_image(path: str) -> Image.Image:
    target = Path(path)
    if not target.is_file():
        raise InvalidInputError(f"路径不存在：{path}")
    try:
        img = Image.open(target)
        img.load()
    except Exception as exc:
        raise InvalidInputError(f"读不出图：{path}（{type(exc).__name__}: {exc}）") from exc
    if img.width < 2 or img.height < 2:
        raise InvalidInputError(f"图太小（空图？）：{path} {img.size}")
    return img


def _silhouette_from_alpha(img: Image.Image) -> Image.Image | None:
    """alpha 有变化才算剪影；恒定 alpha 说明这张图没有剪影信息。"""
    if "A" not in img.getbands():
        return None
    alpha = img.getchannel("A")
    low, high = alpha.getextrema()
    if low == high:
        return None
    return alpha.point(_lut_above(127))


def _segment_foreground(rgb: Image.Image, lines: list[str]) -> Image.Image:
    """游戏侧分割：⛔ 不吃色相 —— 与四边背景统计的距离 + 局部梯度能量。"""
    w, h = rgb.size
    ring = max(2, min(6, min(w, h) // 24))
    patches = [
        rgb.crop((0, 0, ring, ring)),
        rgb.crop((w - ring, 0, w, ring)),
        rgb.crop((0, h - ring, ring, h)),
        rgb.crop((w - ring, h - ring, w, h)),
    ]
    flat: list[int] = []
    for patch in patches:
        flat.extend(patch.convert("RGB").tobytes())
    count = len(flat) // 3
    background = tuple(sorted(flat[channel::3])[count // 2] for channel in range(3))
    solid = Image.new("RGB", rgb.size, background)
    # ⛔ 用逐通道**最大**差，不要 convert("L")：亮度会把色度扔掉，
    # 绿车 vs 蓝灰地面在亮度上可以很近、在 B 通道上差 90+（实测这一条把天花板从 0.88 拉到 0.95+）。
    difference = ImageChops.difference(rgb, solid)
    distance = ImageChops.lighter(
        ImageChops.lighter(difference.getchannel("R"), difference.getchannel("G")),
        difference.getchannel("B"),
    )
    gradient = _clear_border(
        _dilate(rgb.convert("L").filter(ImageFilter.FIND_EDGES), 2), 4
    )
    otsu = max(14, _otsu(distance.histogram()))
    # ⛔ 不要用「高位分位」当能量阈值：实测 0.985 分位 = 255 时 _lut_above(255) 全 0，
    # 梯度这一项被**静默关掉**（假绿）。Otsu 不会切到 255 以上。
    energy_floor = max(24, _otsu(gradient.histogram()))
    foreground = ImageChops.lighter(
        distance.point(_lut_above(otsu)), gradient.point(_lut_above(energy_floor))
    )
    foreground = _largest_component(_close(foreground, 2))
    fraction = _count_on(foreground) / float(w * h)
    lines.append(f"game_seg bg={background} otsu={otsu} energy={energy_floor} frac={fraction:.4f}")
    if fraction < 0.004 or fraction > 0.97:
        raise InvalidInputError(f"游戏截图分不出前景（frac={fraction:.4f}）", lines)
    return foreground


def _fill_holes(mask: Image.Image) -> Image.Image:
    """从画框边界灌背景，灌不到的「内部背景」= 洞 ⇒ 填上（4 邻域）。"""
    width, height = mask.size
    src = mask.tobytes()
    outside = bytearray(width * height)
    inverted = bytes(255 - value for value in src)
    for index in range(width * height):
        on_border = (
            index < width
            or index >= width * (height - 1)
            or index % width == 0
            or index % width == width - 1
        )
        if on_border and not src[index] and not outside[index]:
            _flood(inverted, outside, width, height, index)
    out = bytearray(src)
    for index in range(width * height):
        if not src[index] and not outside[index]:
            out[index] = 255
    return Image.frombytes("L", (width, height), bytes(out))


def _seg_dark(
    rgb: Image.Image, ratio: float, box_frac: tuple[float, float, float, float]
) -> tuple[Image.Image, Image.Image, int, int]:
    """暗部口径：亮度 < ratio * 背景亮度 ∧ 中央窗口 ⇒ 最大连通块。

    返回 (闭运算前的掩码, 闭运算后的掩码, 阈值, 背景亮度)；填洞与闭运算都在返回值里，
    调用方拿两条去做「掩码自证 c)」（处理必须真的起作用）。
    """
    width, height = rgb.size
    ring = max(2, min(6, min(width, height) // 24))
    patches = [
        rgb.crop((0, 0, ring, ring)),
        rgb.crop((width - ring, 0, width, ring)),
        rgb.crop((0, height - ring, ring, height)),
        rgb.crop((width - ring, height - ring, width, height)),
    ]
    luma: list[int] = []
    for patch in patches:
        luma.extend(patch.convert("L").tobytes())
    background = sorted(luma)[len(luma) // 2]
    threshold = max(18, int(ratio * background))
    grey = rgb.convert("L")
    dark = grey.point(_lut_below(threshold))
    window = Image.new("L", rgb.size, 0)
    left, top = int(width * box_frac[0]), int(height * box_frac[1])
    right, bottom = max(left + 1, int(width * box_frac[2])), max(top + 1, int(height * box_frac[3]))
    window.paste(Image.new("L", (right - left, bottom - top), 255), (left, top))
    raw = _largest_component(ImageChops.multiply(dark, window))
    closed = _close(_fill_holes(raw), 3)
    return raw, closed, threshold, background


def _seg_detail(rgb: Image.Image, lines: list[str], window: float) -> Image.Image:
    """中央窗口内：**局部高频能量 + 暗部** ⇒ 最大连通块。⛔ 不吃色相。

    依据：坦克有履带 / 炮管 / 面板细节（高频强），车间水泥地/货架偏平；且坦克比地面暗。
    窗口外的边缘一律 0（把机库钢架 / 树 / 天空排除），免得最大连通块抓到背景。
    """
    width, height = rgb.size
    box = (
        int(width * window), int(height * window),
        int(width * (1.0 - window)), int(height * (1.0 - window)),
    )
    crop = rgb.crop(box)
    grey = crop.convert("L")
    detail = _clear_border(_dilate(grey.filter(ImageFilter.FIND_EDGES), 1), 3)
    strong, detail_threshold = _threshold_edges(detail, None, EDGE_FLOOR)
    if strong is None:
        strong = Image.new("L", crop.size, 0)
    luma_threshold = _otsu(grey.histogram())
    dark = grey.point(_lut_below(max(1, luma_threshold)))
    mask = _largest_component(_close(ImageChops.multiply(dark, _dilate(strong, 1)), 2))
    full = Image.new("L", rgb.size, 0)
    full.paste(mask, (box[0], box[1]))
    fraction = _count_on(full) / float(width * height)
    lines.append(
        f"game_seg[detail window={window:.2f} box={box} 亮度Otsu={luma_threshold}"
        f" 细节阈值={detail_threshold}] frac={fraction:.4f}"
    )
    if fraction < 0.004 or fraction > 0.97:
        raise InvalidInputError(f"detail 分割分不出前景（frac={fraction:.4f}）", lines)
    return full


# --------------------------------------------------------------------------
# 剪影对齐：面积归一化 + 相似变换搜索（旋转/尺度/平移）
# --------------------------------------------------------------------------


def _mask_geometry(
    mask: Image.Image, work_side: int = WORK_MASK_SIDE
) -> tuple[Image.Image, float, tuple[int, int], tuple[int, int]]:
    """裁到 bbox，按 sqrt(面积) 归一化 —— 让「尺度搜索」从 1.0 附近起步。"""
    bbox = mask.getbbox()
    if bbox is None:
        raise InvalidInputError("剪影为空")
    crop = mask.crop(bbox)
    area = _count_on(crop)
    if area < 32:
        raise InvalidInputError(f"剪影太小：{area} 像素")
    factor = work_side / math.sqrt(area)
    size = (max(1, round(crop.width * factor)), max(1, round(crop.height * factor)))
    return crop, factor, (bbox[0], bbox[1]), size


def _normalize_pair(
    render_mask: Image.Image, game_mask: Image.Image, work_side: int = WORK_MASK_SIDE
) -> tuple[
    Image.Image,
    Image.Image,
    tuple[float, tuple[int, int], tuple[int, int]],
    tuple[float, tuple[int, int], tuple[int, int]],
    int,
]:
    """两张剪影归一到**同一块**画布；画布边长按实际长宽比放大（上限 WORK_MASK_CAP）。

    实测踩过：只按 192 建画布时，116x70 的剪影归一化后长边 = 267 > 192，
    贴上去被**裁掉两侧**，于是对齐搜索在一个被切过的形状上找最大 IoU —— 假读数。
    """
    geo_r = _mask_geometry(render_mask, work_side)
    geo_g = _mask_geometry(game_mask, work_side)
    need = max(work_side, geo_r[3][0], geo_r[3][1], geo_g[3][0], geo_g[3][1]) + 2
    side = min(WORK_MASK_CAP, need)
    canvases = []
    pastes = []
    for crop, _factor, _origin, size in (geo_r, geo_g):
        small = crop.resize(size, Image.BILINEAR).point(_lut_above(127))
        canvas = Image.new("L", (side, side), 0)
        paste = ((side - small.width) // 2, (side - small.height) // 2)
        canvas.paste(small, paste)
        canvases.append(canvas)
        pastes.append(paste)
    return (
        canvases[0],
        canvases[1],
        (geo_r[1], geo_r[2], pastes[0]),
        (geo_g[1], geo_g[2], pastes[1]),
        side,
    )


def _align_pair(
    render_mask: Image.Image, game_mask: Image.Image, work_side: int, quick: bool
) -> tuple[float, float, float, float, float, int]:
    """归一化 + 相似搜索；返回 (iou, rot, scale, dx, dy, canvas_side)。"""
    canvas_r, canvas_g, _info_r, _info_g, side = _normalize_pair(
        render_mask, game_mask, work_side
    )
    iou, rot, scale, dx, dy = _search_align(canvas_r, canvas_g, quick)
    return iou, rot, scale, dx, dy, side


def _canvas_warp(
    mask: Image.Image, rot_deg: float, scale: float, dx: float, dy: float
) -> Image.Image:
    size = mask.width
    center = (size - 1) / 2.0
    theta = math.radians(rot_deg)
    cos, sin = math.cos(theta), math.sin(theta)
    a = cos / scale
    b = sin / scale
    c0 = center - (a * (center + dx) + b * (center + dy))
    c1 = center - (-b * (center + dx) + a * (center + dy))
    return mask.transform(
        (size, size), Image.AFFINE, (a, b, c0, -b, a, c1), resample=Image.NEAREST
    )


def _canvas_forward(side: int, rot_deg: float, scale: float, dx: float, dy: float) -> Vec:
    """同一变换的正向（画布→画布），用来往整帧坐标上复合。"""
    center = (side - 1) / 2.0
    theta = math.radians(rot_deg)
    cos, sin = math.cos(theta), math.sin(theta)
    a, b = scale * cos, -scale * sin
    d, e = scale * sin, scale * cos
    c = center + dx - (a * center + b * center)
    f = center + dy - (d * center + e * center)
    return (a, b, c, d, e, f)


def _search_align(
    canvas_render: Image.Image, canvas_game: Image.Image, quick: bool = False
) -> tuple[float, float, float, float, float]:
    """返回 (iou, rot_deg, scale, dx, dy)；粗扫全圈再逐级细化。

    quick=True 是给「相机反推」用的省时档：粗步长更大，只细化一轮。
    """
    game_area = _count_on(canvas_game)
    best = (0.0, 0.0, 1.0, 0.0, 0.0)

    def score(rot: float, scale: float, dx: float, dy: float) -> float:
        warp = _canvas_warp(canvas_render, rot, scale, dx, dy)
        area = _count_on(warp)
        if area == 0:
            return 0.0
        inter = _count_on(ImageChops.multiply(warp, canvas_game))
        union = area + game_area - inter
        return inter / union if union else 0.0

    for rot in range(0, 360, 15 if quick else 6):
        for step in range(-3 if quick else -5, (3 if quick else 5) + 1):
            scale = 1.0 + step * (0.10 if quick else 0.05)
            value = score(rot, scale, 0.0, 0.0)
            if value > best[0]:
                best = (value, float(rot), scale, 0.0, 0.0)
    _, rot0, scale0, _, _ = best
    if quick:
        for index in range(-3, 4):
            for step in range(-2, 3):
                value = score(rot0 + index * 2.0, scale0 + step * 0.04, 0.0, 0.0)
                if value > best[0]:
                    best = (value, rot0 + index * 2.0, scale0 + step * 0.04, 0.0, 0.0)
        return best
    for index in range(-6, 7):
        for step in range(-4, 5):
            value = score(rot0 + index, scale0 + step * 0.02, 0.0, 0.0)
            if value > best[0]:
                best = (value, rot0 + index, scale0 + step * 0.02, 0.0, 0.0)
    _, rot0, scale0, _, _ = best
    for index in range(-4, 5):
        for step in range(-3, 4):
            value = score(rot0 + index * 0.25, scale0 + step * 0.006, 0.0, 0.0)
            if value > best[0]:
                best = (value, rot0 + index * 0.25, scale0 + step * 0.006, 0.0, 0.0)
    _, rot0, scale0, _, _ = best
    for shift in range(-4, 5):
        for other in range(-4, 5):
            value = score(rot0, scale0, float(shift), float(other))
            if value > best[0]:
                best = (value, rot0, scale0, float(shift), float(other))
    _, rot0, scale0, dx0, dy0 = best
    for shift in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for other in (-1.0, -0.5, 0.0, 0.5, 1.0):
            value = score(rot0, scale0, dx0 + shift, dy0 + other)
            if value > best[0]:
                best = (value, rot0, scale0, dx0 + shift, dy0 + other)
    return best


# --------------------------------------------------------------------------
# 主判据：每像素 UV 采样基色图 ⇒ 预测标记
# --------------------------------------------------------------------------


def _fit_camera(
    scene: Scene,
    game_mask: Image.Image,
    raster_size: tuple[int, int],
    args: argparse.Namespace,
    lines: list[str],
) -> tuple[Scene, dict]:
    """从游戏截图反推 orbit 相机（键名与 refs/lighting.json 的 camera 逐字一致）。

    评分 = 剪影对齐 IoU。粗搜抽稀三角形（省时），细搜与复核用全几何。
    ⛔ 尺子自证：tests/test_compare_side_by_side.py::test_fit_camera_recovers_a_known_camera
    """
    base_az = float(args.fit_az)
    base_elev = float(args.fit_elev)
    base_distance = float(args.fit_distance)
    base_focal = float(args.fit_focal)

    def build(camera: Mat, projection: Mat) -> Scene:
        # ⚠ rasterize 走的是「projection @ scene.camera @ world」这条字面链，
        # 所以这里必须放**相机的逆**（世界→相机）。绕开这一点的话每个候选都 w<0 ⇒ 覆盖 0。
        view = _invert_mat4(camera)
        return Scene(
            objects=scene.objects,
            projection=projection,
            camera=view if view is not None else camera,
            clip_start=scene.clip_start,
            clip_end=scene.clip_end,
            resolution=scene.resolution,
            textures=scene.textures,
        )

    def score(
        azimuth: float,
        elevation: float,
        distance: float,
        focal: float,
        size: tuple[int, int],
        stride: int,
        canvas: int,
        quick: bool = True,
    ) -> float:
        camera, projection = orbit_camera(azimuth, elevation, distance, focal, size)
        trial = rasterize(build(camera, projection), size, stride)
        if _count_on(trial.coverage) == 0:
            return 0.0
        iou, _rot, _scale, _dx, _dy, _side = _align_pair(
            trial.coverage, game_mask, canvas, quick
        )
        return iou

    coarse = _cap_size(raster_size, FIT_COARSE_SIDE)
    # ⚠ 抽稀只对**密网格**有意义：低模（如 24 个三角形的盒子）抽稀会把形状拆掉，
    # 实测 stride=8 时全部候选 IoU=0（假红）。按总三角形数自适应。
    total_triangles = sum(len(obj.triangles) for obj in scene.objects)
    coarse_stride = max(1, min(FIT_COARSE_STRIDE, total_triangles // 2000))
    refine_stride = max(1, min(FIT_REFINE_STRIDE, total_triangles // 2000))
    lines.append(
        f"camera_fit 起步：三角形 {total_triangles} 个 ⇒ 粗搜抽稀 1/{coarse_stride}、"
        f"细搜 1/{refine_stride}；粗搜分辨率 {coarse}"
    )
    best = (0.0, base_az, base_elev, base_distance, base_focal)
    tried = 0
    for azimuth in range(0, 360, 30):
        for elevation in range(-10, 71, 15):
            iou = score(
                float(azimuth), float(elevation), base_distance, base_focal,
                coarse, coarse_stride, FIT_CANVAS_SIDE,
            )
            tried += 1
            if iou > best[0]:
                best = (iou, float(azimuth), float(elevation), base_distance, base_focal)
    lines.append(
        f"camera_fit 粗搜 {tried} 档（az 30° 全圈 x el 15°）⇒ IoU={best[0]:.4f}"
        f" az={best[1]:.0f} el={best[2]:.0f}"
    )
    for span, step, stride in ((10.0, 5.0, coarse_stride), (4.0, 2.0, refine_stride)):
        anchor = best
        steps = max(1, int(round(span / step)))
        for index in range(-steps, steps + 1):
            for other in range(-steps, steps + 1):
                elevation = anchor[2] + other * step
                if elevation < -30.0 or elevation > 88.0:
                    continue
                iou = score(
                    anchor[1] + index * step, elevation, anchor[3], anchor[4],
                    coarse, stride, FIT_CANVAS_SIDE,
                )
                tried += 1
                if iou > best[0]:
                    best = (iou, anchor[1] + index * step, elevation, anchor[3], anchor[4])
        lines.append(
            f"camera_fit 细化 ±{span:.0f}°/步 {step:.0f}° ⇒ IoU={best[0]:.4f}"
            f" az={best[1]:.1f} el={best[2]:.1f}（累计 {tried} 档）"
        )
    # 距离/焦距近乎退化（2D 相似变换能吃掉一部分）⇒ 扫宽一点，别只扫 ±25%
    for factor in (0.5, 0.65, 0.8, 1.0, 1.25):
        for focal_factor in (0.7, 0.85, 1.0, 1.2):
            distance = base_distance * factor
            focal = max(20.0, base_focal * focal_factor)
            iou = score(
                best[1], best[2], distance, focal, coarse, refine_stride, FIT_CANVAS_SIDE
            )
            tried += 1
            if iou > best[0]:
                best = (iou, best[1], best[2], distance, focal)
    lines.append(
        f"camera_fit 距离/焦距扫描 ⇒ IoU={best[0]:.4f} d={best[3]:.2f} f={best[4]:.1f}"
        f"（累计 {tried} 档）"
    )
    # ⚠ 粗分辨率与全分辨率的 IoU 不在一个尺度上（实测同参数 0.9375 vs 0.9076），
    # 所以最后要在**全分辨率上重新定标**，再在同一尺度上细化，否则细化永远赢不了。
    anchor_iou = score(
        best[1], best[2], best[3], best[4], raster_size, refine_stride, WORK_MASK_SIDE
    )
    lines.append(
        f"camera_fit 搬到全分辨率 {raster_size} 重新定标：IoU={anchor_iou:.4f}"
        f" az={best[1]:.2f} el={best[2]:.2f} d={best[3]:.2f} f={best[4]:.1f}"
    )
    best = (anchor_iou, best[1], best[2], best[3], best[4])
    for span, step in ((3.0, 1.0), (1.0, 0.5)):
        anchor = best
        steps = max(1, int(round(span / step)))
        for index in range(-steps, steps + 1):
            for other in range(-steps, steps + 1):
                elevation = anchor[2] + other * step
                if elevation < -30.0 or elevation > 88.0:
                    continue
                iou = score(
                    anchor[1] + index * step, elevation, anchor[3], anchor[4],
                    raster_size, refine_stride, FIT_CANVAS_SIDE,
                )
                tried += 1
                if iou > best[0]:
                    best = (iou, anchor[1] + index * step, elevation, anchor[3], anchor[4])
    lines.append(
        f"camera_fit 角细化到 ±1°/0.5° ⇒ IoU={best[0]:.4f} az={best[1]:.2f} el={best[2]:.2f}"
        f"（累计 {tried} 档）"
    )
    # 最后这次用**完整 2D 搜索**（quick=False）：拟合过程里的 quick 档自己也会吃掉
    # 两三个百分点的 IoU（实测 0.9431 -> 0.96+），报告出去的必须是完整搜索的数。
    full_iou = score(
        best[1], best[2], best[3], best[4], raster_size, 1, WORK_MASK_SIDE, quick=False
    )
    lines.append(f"camera_fit 全几何复核 IoU={full_iou:.4f}")
    camera, projection = orbit_camera(best[1], best[2], best[3], best[4], raster_size)
    params = {
        "target": [0.0, 0.0, 0.0],
        "distance": round(best[3], 4),
        "azimuth_deg": round(best[1] % 360.0, 3),
        "elevation_deg": round(best[2], 3),
        "focal_length_mm": round(best[4], 3),
        "ortho": False,
        "silhouette_iou": round(full_iou, 4),
        "coarse_iou": round(best[0], 4),
        "candidates_tried": tried,
        "raster_size": list(raster_size),
    }
    return build(camera, projection), params


def _match_material(scene: Scene, texture_path: str) -> str | None:
    """按文件名把 --texture 对到 scene.textures 里的某个材质（⛔ 不硬编码车名）。"""
    if not scene.textures:
        return None
    stem = Path(texture_path).stem.lower()
    name = Path(texture_path).name.lower()
    for material, value in scene.textures.items():
        candidate = Path(str(value))
        if candidate.stem.lower() == stem or candidate.name.lower() == name:
            return material
    return None


def _sample_marks(
    raster: Raster,
    stride: int,
    region: bytes | None,
    tex_edge: bytes,
    tex_rgb: bytes,
    tex_size: tuple[int, int],
) -> tuple[Image.Image, Image.Image, Image.Image]:
    """用每像素 UV 采样基色图；返回（纹理空间标记层、预测反照率、采样区域）。"""
    width, height = raster.size
    tex_w, tex_h = tex_size
    pw = max(1, width // stride)
    ph = max(1, height // stride)
    covered = raster.covered
    uv_x, uv_y = raster.uv_x, raster.uv_y
    marks = bytearray(pw * ph)
    albedo = bytearray(pw * ph * 3)
    area = bytearray(pw * ph)
    for j in range(ph):
        row = j * stride * width
        for i in range(pw):
            src = row + i * stride
            if not covered[src]:
                continue
            if region is not None and not region[src]:
                continue
            u = uv_x[src]
            v = uv_y[src]
            if u != u or v != v:
                continue
            u -= math.floor(u)
            v -= math.floor(v)
            tx = min(tex_w - 1, int(u * (tex_w - 1)))
            ty = min(tex_h - 1, int((1.0 - v) * (tex_h - 1)))
            texel = (ty * tex_w + tx) * 3
            dst = j * pw + i
            albedo[dst * 3] = tex_rgb[texel]
            albedo[dst * 3 + 1] = tex_rgb[texel + 1]
            albedo[dst * 3 + 2] = tex_rgb[texel + 2]
            marks[dst] = tex_edge[texel // 3]
            area[dst] = 255
    return (
        Image.frombytes("L", (pw, ph), bytes(marks)),
        Image.frombytes("RGB", (pw, ph), bytes(albedo)),
        Image.frombytes("L", (pw, ph), bytes(area)),
    )


def _predict(
    texture: Image.Image,
    raster: Raster,
    stride: int,
    region: bytes | None,
    lines: list[str],
    label: str,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    tex = texture.convert("RGB")
    marked, threshold = _threshold_edges(_edge_map(tex), None, EDGE_FLOOR)
    if marked is None:
        marked = Image.new("L", tex.size, 0)
    lines.append(
        f"{label} texture={tex.size} 边缘分位阈值={threshold} 纹理标记像素={_count_on(marked)}"
    )
    marks, albedo, area = _sample_marks(
        raster, stride, region, marked.tobytes(), tex.tobytes(), tex.size
    )
    # ⚠ 采样区域外是黑底，FIND_EDGES 会在**剪影边界**造出一圈假边缘（实测 470 px）。
    # 所以统计与掩码都用内缩 1 像素的 area；否则平涂贴图也会「有图案」= 尺子空转。
    frame, frame_threshold = _threshold_edges(_edge_map(albedo), _erode(area, 1), EDGE_FLOOR)
    if frame is not None:
        marks = ImageChops.lighter(marks, frame)
    lines.append(
        f"{label} 采样帧={albedo.size} 采样面积={_count_on(area)}"
        f" 反照率边缘阈值={frame_threshold} 预测标记={_count_on(marks)}"
    )
    return marks, albedo, area


def _f1(pred: Image.Image, obs: Image.Image, radius: int) -> tuple[float, float, float]:
    """对称膨胀容差的 F1：两边都膨胀 r，吃掉 1~r 像素的配准误差。"""
    pred_wide = _dilate(pred, radius)
    obs_wide = _dilate(obs, radius)
    inter = _count_on(ImageChops.multiply(pred_wide, obs_wide))
    count_pred = _count_on(pred_wide)
    count_obs = _count_on(obs_wide)
    precision = inter / count_pred if count_pred else 0.0
    recall = inter / count_obs if count_obs else 0.0
    if precision + recall <= 0:
        return 0.0, 0.0, 0.0
    return 2 * precision * recall / (precision + recall), precision, recall


def _chroma_corr(pred_albedo: Image.Image, game_rgb: Image.Image, mask: Image.Image) -> float:
    """去亮度后的色度相关（**辅助读数，不进 verdict**）：两条色度轴 Pearson 取平均。"""
    pred = pred_albedo.convert("RGB").tobytes()
    obs = game_rgb.convert("RGB").tobytes()
    guard = mask.tobytes()
    axes = [[0.0] * 6, [0.0] * 6]
    for index in range(len(guard)):
        if guard[index] < 128:
            continue
        base = index * 3
        pairs = (
            (pred[base] - pred[base + 1], obs[base] - obs[base + 1]),
            (pred[base + 1] - pred[base + 2], obs[base + 1] - obs[base + 2]),
        )
        for axis, (x, y) in enumerate(pairs):
            row = axes[axis]
            row[0] += 1.0
            row[1] += x
            row[2] += y
            row[3] += x * x
            row[4] += y * y
            row[5] += x * y
    corrs = []
    for count, sx, sy, sxx, syy, sxy in axes:
        if count < 64:
            continue
        mean_x = sx / count
        mean_y = sy / count
        cov = sxy / count - mean_x * mean_y
        var_x = sxx / count - mean_x * mean_x
        var_y = syy / count - mean_y * mean_y
        denom = math.sqrt(max(var_x, 0.0) * max(var_y, 0.0))
        corrs.append(cov / denom if denom > 1e-9 else 0.0)
    return sum(corrs) / len(corrs) if corrs else 0.0


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def _parse_box(raw: str) -> tuple[int, int, int, int]:
    parts = [item.strip() for item in raw.split(",")]
    if len(parts) != 4:
        raise InvalidInputError(f"--game-box 要 4 个数 x0,y0,x1,y1，收到 {raw!r}")
    try:
        x0, y0, x1, y1 = (int(float(item)) for item in parts)
    except ValueError as exc:
        raise InvalidInputError(f"--game-box 有非数字：{raw!r}") from exc
    if x1 <= x0 or y1 <= y0:
        raise InvalidInputError(f"--game-box 不是正矩形：{raw!r}")
    return (x0, y0, x1, y1)


def _frac_box(raw: str) -> tuple[float, float, float, float]:
    parts = [float(item) for item in raw.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--seg-box-frac 要 4 个数 x0,y0,x1,y1")
    return (parts[0], parts[1], parts[2], parts[3])


def _parse_boxes(raw: str) -> list[tuple[int, int, int, int] | None]:
    """--game-box 支持多候选：用分号隔开，逐个跑、读数全进 lines[]。"""
    boxes: list[tuple[int, int, int, int] | None] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if chunk:
            boxes.append(_parse_box(chunk))
    return boxes or [None]


def _blank_report(message: str, lines: list[str], raster_alpha_iou: float = 0.0) -> dict:
    report = _report_shell()
    report["raster_alpha_iou"] = round(raster_alpha_iou, 4)
    report["lines"] = [
        *lines,
        "pattern_f1 / pred_mark_px / obs_edge_px / chroma_corr / control"
        f" 未计算（原因：{message}）",
        f"INVALID: {message}",
    ]
    return report


def analyse(args: argparse.Namespace) -> tuple[dict, list[Image.Image]]:
    lines: list[str] = []
    render = _req_image(args.render)
    game = _req_image(args.game)
    texture = _req_image(args.texture)
    scene_path = args.scene or args.uv
    if not scene_path:
        raise InvalidInputError("几何输入缺失：请给 --scene <scene.json>（--uv 是它的旧别名）")
    if args.scene is None:
        lines.append("--uv 是 --scene 的旧别名（口径 A 已废 UV pass PNG）")
    elif args.uv:
        lines.append("同时给了 --scene 与 --uv，以 --scene 为准")
    if Path(scene_path).suffix.lower() in {".png", ".jpg", ".jpeg", ".exr", ".tga"}:
        raise InvalidInputError(
            f"--scene/--uv 要的是 scene.json，收到图片 {Path(scene_path).name}"
            "（口径 A 已废 UV pass PNG）",
            lines,
        )
    scene = read_scene(scene_path)
    if scene.resolution and scene.resolution != render.size:
        lines.append(f"scene.resolution {scene.resolution}!={render.size}，以 --render 为准")
    if scene.clip_start == 0.0 and scene.clip_end >= 1e29:
        lines.append("clip_start/clip_end 用缺省 0.0 / 1e30（scene.json 没给）")
    materials = sorted({obj.material or obj.name for obj in scene.objects})
    lines.append(f"scene objects={len(scene.objects)} 材质={materials}")
    if scene.textures:
        lines.append(f"scene.textures={len(scene.textures)} 条（用来把 --texture 对到材质）")

    alpha = _silhouette_from_alpha(render)
    if alpha is None:
        message = "--render 没有可用 alpha（film_transparent 没开）⇒ 无法自证"
        raise InvalidInputError(message, lines)
    raster_size, scale = _raster_target(render.size)
    if scale != 1.0:
        lines.append(f"光栅降到 {raster_size}（超上限 {RASTER_MAX_PX}），scale={scale:.4f}")
    if alpha.size != raster_size:
        alpha = alpha.resize(raster_size, Image.BILINEAR).point(_lut_above(127))
    alpha_px = _count_on(alpha)

    trials: list[tuple[float, str, Raster, int]] = []
    for label, candidate in _camera_candidates(scene):
        trial = rasterize(candidate, raster_size)
        covered = _count_on(trial.coverage)
        inter = _count_on(ImageChops.multiply(trial.coverage, alpha))
        union = covered + alpha_px - inter
        iou = inter / union if union else 0.0
        lines.append(
            f"相机链[{label}] raster={raster_size} 三角形="
            f"{trial.triangles_kept}/{trial.triangles_kept + trial.triangles_skipped}"
            f" 覆盖={covered} raster_alpha_iou={iou:.4f}"
        )
        trials.append((iou, label, trial, covered))
        if iou >= RASTER_ALPHA_IOU_MIN and len(trials) == 1:
            break
    raster_alpha_iou, camera_chain, raster, covered_px = max(trials, key=lambda item: item[0])
    lines.append(f"相机链取[{camera_chain}]（门限 {RASTER_ALPHA_IOU_MIN}）")
    lines.append(f"材质组={raster.groups}")
    if covered_px == 0:
        raise InvalidInputError(
            "光栅覆盖为 0：几何/相机矩阵对不上，或 gating 剔光了",
            lines,
            raster_alpha_iou=raster_alpha_iou,
        )
    if raster_alpha_iou < RASTER_ALPHA_IOU_MIN:
        raise InvalidInputError(
            f"光栅器自证不过：raster_alpha_iou={raster_alpha_iou:.4f}"
            f" < {RASTER_ALPHA_IOU_MIN}",
            lines,
            raster_alpha_iou=raster_alpha_iou,
        )

    # ---- 游戏侧：多个候选窗口 x 两种分割；每个候选的读数全写进 lines[] ----
    boxes = _parse_boxes(args.game_box) if args.game_box else [None]
    modes = (
        [args.seg_mode]
        if args.seg_mode in {"border", "detail", "dark"}
        else ["border", "detail", "dark"]
    )
    candidates: list[tuple[float, str, Image.Image, Image.Image]] = []
    if args.game_mask:
        crop = _cap_image(game.convert("RGB"), GAME_WORK_SIDE)
        mask_image = _req_image(args.game_mask)
        if mask_image.size != crop.size:
            mask_image = mask_image.resize(crop.size, Image.NEAREST)
        sil = mask_image.convert("L").point(_lut_above(127))
        lines.append(f"游戏剪影来自 --game-mask（{crop.size}）")
        candidates.append((1.0, "mask", crop, sil))
    else:
        for box in boxes:
            crop = game.convert("RGB").crop(box) if box else game.convert("RGB")
            crop = _cap_image(crop, GAME_WORK_SIDE)
            for mode in modes:
                if mode == "dark":
                    for ratio in (0.75, 0.85):
                        raw, closed, threshold, background = _seg_dark(
                            crop, ratio, args.seg_box_frac
                        )
                        box_bbox = closed.getbbox()
                        bbox_area = (
                            (box_bbox[2] - box_bbox[0]) * (box_bbox[3] - box_bbox[1])
                            if box_bbox
                            else 0
                        )
                        frame_area = float(crop.size[0] * crop.size[1])
                        area = _count_on(closed)
                        bbox_share = bbox_area / frame_area
                        fill_ratio = area / bbox_area if bbox_area else 0.0
                        iou_closed = _align_pair(raster.coverage, closed, WORK_MASK_SIDE, True)[0]
                        iou_raw = _align_pair(raster.coverage, raw, WORK_MASK_SIDE, True)[0]
                        delta = abs(iou_closed - iou_raw)
                        ok_bbox = 0.15 <= bbox_share <= 0.75
                        ok_fill = 0.45 <= fill_ratio <= 0.95
                        ok_close = delta >= 0.02
                        lines.append(
                            f"候选 box={box} mode=dark ratio={ratio} 阈值={threshold}"
                            f" 背景亮度={background} bbox占比={bbox_share:.3f}(自证a={ok_bbox})"
                            f" 填充率={fill_ratio:.3f}(自证b={ok_fill})"
                            f" IoU闭={iou_closed:.4f} IoU不闭={iou_raw:.4f}"
                            f" 差={delta:.4f}(自证c={ok_close})"
                        )
                        if ok_bbox and ok_fill and ok_close:
                            candidates.append((iou_closed, f"dark{ratio:g}", crop, closed))
                        else:
                            lines.append("  ⇒ 该候选三条掩码自证未全过，作废（不进候选池）")
                    continue
                try:
                    sil = (
                        _segment_foreground(crop, lines)
                        if mode == "border"
                        else _seg_detail(crop, lines, args.seg_window)
                    )
                except InvalidInputError as exc:
                    lines.append(f"候选 box={box} mode={mode} ⇒ 分割失败：{exc}")
                    continue
                share = _count_on(sil) / float(crop.size[0] * crop.size[1])
                iou_c = _align_pair(raster.coverage, sil, WORK_MASK_SIDE, True)[0]
                lines.append(
                    f"候选 box={box} mode={mode} 尺寸={crop.size}"
                    f" 前景占比={share:.4f} 对齐IoU={iou_c:.4f}"
                )
                candidates.append((iou_c, mode, crop, sil))
    if not candidates:
        raise InvalidInputError("所有候选窗口/分割都分不出剪影", lines)
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_iou_c, best_mode, game_crop, sil_game = candidates[0]
    lines.append(
        f"候选取 mode={best_mode}（对齐IoU={best_iou_c:.4f}；共 {len(candidates)} 个候选，"
        "全部读数见上）"
    )

    # ---- 可选：相机反推（粗搜抽稀三角形，细搜全几何）----
    camera_fit: dict | None = None
    if args.fit_camera:
        scene, camera_fit = _fit_camera(scene, sil_game, raster_size, args, lines)
        raster = rasterize(scene, raster_size)
        covered_px = _count_on(raster.coverage)
        lines.append(
            f"⚠ 拟合相机已替换原相机：raster_alpha_iou={raster_alpha_iou:.4f} 是在**原相机**上"
            "量的（alpha 不再是拟合相机的真值）"
        )
        lines.append(f"拟合后覆盖={covered_px} 材质组={raster.groups}")
        if covered_px == 0:
            raise InvalidInputError(
                "拟合相机下光栅覆盖为 0", lines, raster_alpha_iou=raster_alpha_iou
            )

    region: bytes | None = None
    matched = _match_material(scene, args.texture)
    if matched and matched in raster.groups:
        gid = raster.groups.index(matched)
        mask = bytearray(len(raster.covered))
        group = raster.group
        for index in range(len(group)):
            if raster.covered[index] and group[index] == gid:
                mask[index] = 255
        share = sum(1 for value in mask if value) / max(1, covered_px)
        if share >= REGION_MIN_SHARE:
            region = bytes(mask)
            lines.append(f"判据只取材质 {matched!r} 的像素（占覆盖 {share:.3f}）")
        else:
            lines.append(f"材质 {matched!r} 只占覆盖 {share:.3f} ⇒ 退回整个剪影")
    elif matched:
        lines.append(f"材质 {matched!r} 不在光栅材质组 {raster.groups} ⇒ 用整个剪影")
    else:
        lines.append("没能把 --texture 对到 scene.textures 的任何材质 ⇒ 判据用整个剪影")

    stride = max(1, math.ceil(max(raster_size) / WORK_PATTERN_SIDE))
    lines.append(f"UV 采样步长 stride={stride}（工作边长上限 {WORK_PATTERN_SIDE}）")
    marks, albedo, area = _predict(texture, raster, stride, region, lines, "primary")

    canvas_render, canvas_game, info_r, info_g, side = _normalize_pair(raster.coverage, sil_game)
    factor_r, origin_r, paste_r = info_r
    factor_g, origin_g, paste_g = info_g
    lines.append(f"对齐画布={side}px（面积归一化后长边超出 {WORK_MASK_SIDE} 就会放大）")
    iou, rot, scale_canvas, dx, dy = _search_align(canvas_render, canvas_game)
    scale_game = factor_r * scale_canvas / factor_g
    lines.append(
        f"对齐 IoU={iou:.4f}（门限 {ALIGN_IOU_MIN}） rot={rot:.3f}° "
        f"scale={scale_game:.4f} dx={dx / factor_g:.1f} dy={dy / factor_g:.1f} 游戏帧像素"
    )

    panels: list[Image.Image] = [render, game_crop]
    report = _report_shell()
    report["camera_fit"] = camera_fit
    report["raster_alpha_iou"] = round(raster_alpha_iou, 4)
    report["silhouette_iou"] = round(iou, 4)
    report["align"] = {
        "rot_deg": round(rot, 3),
        "scale": round(scale_game, 4),
        "dx": round(dx / factor_g, 2),
        "dy": round(dy / factor_g, 2),
    }
    if iou < ALIGN_IOU_MIN:
        lines.append(f"对不齐：IoU {iou:.4f} < {ALIGN_IOU_MIN} ⇒ INVALID（不猜、不当 PASS）")
        lines.append(
            "pattern_f1 / pred_mark_px / obs_edge_px / chroma_corr / control 未计算"
            f"（原因：对齐 IoU {iou:.4f} < 门限 {ALIGN_IOU_MIN}）"
        )
        report["lines"] = lines
        report["verdict"] = "INVALID"
        return report, panels

    a_raster = (factor_r, 0.0, paste_r[0] - origin_r[0] * factor_r, 0.0, factor_r,
                paste_r[1] - origin_r[1] * factor_r)
    s_stride = (stride, 0.0, (stride - 1) * 0.5, 0.0, stride, (stride - 1) * 0.5)
    a_game_inv = (1.0 / factor_g, 0.0, origin_g[0] - paste_g[0] / factor_g,
                  0.0, 1.0 / factor_g, origin_g[1] - paste_g[1] / factor_g)
    canvas_to_game = _canvas_forward(side, rot, scale_canvas, dx, dy)
    forward = _compose(a_game_inv, _compose(canvas_to_game, _compose(a_raster, s_stride)))

    game_area = _count_on(sil_game)
    margin = max(2, round(0.012 * math.sqrt(max(1, game_area))))
    region_game = _warp(area, forward, game_crop.size, Image.NEAREST)
    inner = _erode(region_game, margin)
    obs, obs_threshold = _threshold_edges(_edge_map(game_crop), inner, EDGE_FLOOR)
    if obs is None:
        obs = Image.new("L", game_crop.size, 0)
        lines.append(f"观测边缘分位阈值={obs_threshold} < {EDGE_FLOOR} ⇒ obs 空（反空转 ⇒ FAIL）")
    else:
        lines.append(f"观测边缘阈值={obs_threshold} obs 像素={_count_on(obs)}")

    radius = max(2, round(0.004 * max(game_crop.size)))
    pred_game = _warp(marks, forward, game_crop.size, Image.NEAREST)
    pattern_f1, precision, recall = _f1(pred_game, obs, radius)
    pred_mark_px = _count_on(pred_game)
    obs_edge_px = _count_on(obs)
    lines.append(
        f"pattern_f1={pattern_f1:.4f}（门限 {PATTERN_F1_MIN}） P={precision:.4f} R={recall:.4f}"
        f" pred={pred_mark_px} obs={obs_edge_px} 膨胀半径={radius}px"
    )

    small_game, _ = _scale_down(game_crop.size, CHROMA_SIDE)
    shrink = _shrink_matrix(game_crop.size, small_game)
    chroma = _chroma_corr(
        _warp(albedo, _compose(shrink, forward), small_game, Image.BILINEAR),
        game_crop.resize(small_game, Image.BILINEAR),
        _warp(area, _compose(shrink, forward), small_game, Image.NEAREST),
    )
    lines.append(f"chroma_corr={chroma:.4f}（辅助读数，不进 verdict）")

    control = {"texture": "", "pattern_f1": 0.0, "delta": 0.0, "red": False}
    if args.control_texture:
        control_texture = _req_image(args.control_texture)
        control_marks, _, _ = _predict(
            control_texture, raster, stride, region, lines, "control"
        )
        control_game = _warp(control_marks, forward, game_crop.size, Image.NEAREST)
        control_f1, _, _ = _f1(control_game, obs, radius)
        delta = pattern_f1 - control_f1
        control = {
            "texture": str(args.control_texture),
            "pattern_f1": round(control_f1, 4),
            "delta": round(delta, 4),
            "red": bool(control_f1 < pattern_f1 - CONTROL_MARGIN),
        }
        lines.append(
            f"正控 texture={Path(args.control_texture).name} pattern_f1={control_f1:.4f}"
            f" delta={delta:.4f} red={control['red']}（判据 {CONTROL_MARGIN}）"
        )

    if pred_mark_px == 0 or obs_edge_px == 0:
        verdict = "FAIL"
        lines.append("反空转：pred 或 obs 为空 ⇒ FAIL（⛔ 不许 PASS）")
    elif pattern_f1 < PATTERN_F1_MIN:
        verdict = "FAIL"
        lines.append(f"图案 F1 {pattern_f1:.4f} < {PATTERN_F1_MIN} ⇒ FAIL")
    elif args.control_texture and not control["red"]:
        verdict = "FAIL"
        lines.append("正控没红 ⇒ 尺子不成立（不能红 = 这把尺子在这个环境测不了）⇒ FAIL")
    else:
        verdict = "PASS"

    panels.append(_overlay(game_crop, pred_game, obs))
    report.update(
        {
            "verdict": verdict,
            "raster_alpha_iou": round(raster_alpha_iou, 4),
            "silhouette_iou": round(iou, 4),
            "pattern_f1": round(pattern_f1, 4),
            "pattern_precision": round(precision, 4),
            "pattern_recall": round(recall, 4),
            "pred_mark_px": pred_mark_px,
            "obs_edge_px": obs_edge_px,
            "chroma_corr": round(chroma, 4),
            "control": control,
            "not_evaluated": [],
            "lines": lines,
        }
    )
    return report, panels
# --------------------------------------------------------------------------
# 三联图与入口
# --------------------------------------------------------------------------


#: 还没走到图案那一步就退出的那些字段。⛔ 一律报 null，不许填 0.0 / false ——
#: 机器读 JSON 时会把 0.0 读成「图案 0 分」、把 false 读成「正控没红」，
#: 而同一份 lines 里可能正写着「预测标记=5408」。这是会骗人的报数（④ 复核点名）。
NOT_EVALUATED_WHEN_INVALID = (
    "pattern_f1",
    "pattern_precision",
    "pattern_recall",
    "pred_mark_px",
    "obs_edge_px",
    "chroma_corr",
    "control",
)


def _report_shell() -> dict:
    return {
        "schema": SCHEMA,
        "verdict": "INVALID",
        "silhouette_iou": 0.0,
        "align": {"rot_deg": 0.0, "scale": 1.0, "dx": 0.0, "dy": 0.0},
        "pattern_f1": None,
        "pattern_precision": None,
        "pattern_recall": None,
        "pred_mark_px": None,
        "obs_edge_px": None,
        "chroma_corr": None,
        "control": None,
        "raster_alpha_iou": 0.0,
        "camera_fit": None,
        "align_iou_min": ALIGN_IOU_MIN,
        "not_evaluated": list(NOT_EVALUATED_WHEN_INVALID),
        "render_label": "",
        "game_label": "",
        "lines": [],
    }


def _panel(img: Image.Image, height: int) -> Image.Image:
    factor = height / max(1, img.height)
    return img.convert("RGB").resize(
        (max(1, round(img.width * factor)), height), Image.BILINEAR
    )


def _overlay(game_rgb: Image.Image, pred: Image.Image, obs: Image.Image) -> Image.Image:
    """叠加：pred 红 / obs 绿 / 重合黄。"""
    panel = _panel(game_rgb, PANEL_HEIGHT)
    width, height = panel.size
    data = bytearray(panel.tobytes())
    pred_small = pred.convert("L").resize((width, height), Image.NEAREST).tobytes()
    obs_small = obs.convert("L").resize((width, height), Image.NEAREST).tobytes()
    for index in range(width * height):
        has_pred = pred_small[index] >= 128
        has_obs = obs_small[index] >= 128
        if not (has_pred or has_obs):
            continue
        base = index * 3
        if has_pred and has_obs:
            tint = (170, 170, 0)
        elif has_pred:
            tint = (190, 0, 0)
        else:
            tint = (0, 170, 0)
        for channel in range(3):
            data[base + channel] = min(255, data[base + channel] // 3 + tint[channel])
    return Image.frombytes("RGB", (width, height), bytes(data))


def _compose_side_by_side(
    path: Path, panels: list[Image.Image], render_label: str, game_label: str
) -> None:
    # ⚠ 标题**按实际来源**写（--game 喂的是 Unity 场景渲染时，写死 "game screenshot" 会让
    # 交接里下一个人读错 —— 这图是要当证据的）。默认就是 --render / --game 的 basename。
    labels = (render_label, game_label, "overlay: pred=red obs=green both=yellow")
    scaled = [_panel(item, PANEL_HEIGHT) for item in panels]
    gap = 8
    total = sum(item.width for item in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (total, PANEL_HEIGHT + 18), (24, 24, 28))
    draw = ImageDraw.Draw(canvas)
    x = 0
    for index, item in enumerate(scaled):
        canvas.paste(item, (x, 18))
        draw.text((x + 4, 4), labels[index] if index < len(labels) else "", fill=(230, 230, 230))
        x += item.width + gap
    canvas.save(path)


def _wrap_text(text: str, width: int) -> list[str]:
    rows: list[str] = []
    for chunk in text.split(" | "):
        while len(chunk) > width:
            rows.append(chunk[:width])
            chunk = chunk[width:]
        rows.append(chunk)
    return rows


def _write_placeholder(path: Path, message: str) -> None:
    canvas = Image.new("RGB", (980, 220), (36, 18, 18))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 12), "INVALID - side_by_side.png", fill=(240, 210, 210))
    for index, row in enumerate(_wrap_text(message, 110)[:9]):
        draw.text((12, 40 + index * 18), row, fill=(220, 190, 190))
    canvas.save(path)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A5 并排比对：图案/标记的 UV 级对应")
    parser.add_argument("--render", required=True, help="Blender 渲染 PNG（要带 alpha）")
    parser.add_argument("--scene", default=None, help="scene.json（几何 + 相机）")
    parser.add_argument("--uv", default=None, help="--scene 的旧别名（口径 A 已废 UV pass PNG）")
    parser.add_argument("--game", required=True, help="游戏截图 PNG")
    parser.add_argument("--texture", required=True, help="基色贴图 PNG")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--control-texture", default=None, help="P3 正控：另一辆车的基色图")
    parser.add_argument("--game-box", default=None, help="x0,y0,x1,y1")
    parser.add_argument("--game-mask", default=None)
    parser.add_argument("--json", default=None)
    parser.add_argument("--render-label", default=None, help="默认取 --render 的 basename")
    parser.add_argument("--game-label", default=None, help="默认取 --game 的 basename")
    parser.add_argument(
        "--seg-mode", default="auto", choices=["auto", "border", "detail", "dark"]
    )
    parser.add_argument(
        "--seg-box-frac", type=_frac_box, default=(0.05, 0.0, 1.0, 1.0),
        help="dark 模式的中央窗口 x0,y0,x1,y1（0~1 比例）",
    )
    parser.add_argument("--seg-window", type=float, default=0.12)
    parser.add_argument("--fit-camera", action="store_true", help="从游戏截图反推相机")
    parser.add_argument("--fit-az", type=float, default=45.0)
    parser.add_argument("--fit-elev", type=float, default=20.0)
    parser.add_argument("--fit-distance", type=float, default=14.0)
    parser.add_argument("--fit-focal", type=float, default=50.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / "side_by_side.png"
    try:
        report, panels = analyse(args)
    except InvalidInputError as exc:
        report = _blank_report(str(exc), exc.lines, exc.raster_alpha_iou)
        panels = []
    render_label = args.render_label or Path(args.render).name
    game_label = args.game_label or Path(args.game).name
    report["render_label"] = render_label
    report["game_label"] = game_label
    if panels:
        _compose_side_by_side(image_path, panels, render_label, game_label)
    else:
        _write_placeholder(image_path, " | ".join(report["lines"][-3:]))
    payload = json.dumps(report, ensure_ascii=True, indent=2)
    (out_dir / "compare.json").write_text(payload, encoding="utf-8")
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    print(BEGIN)
    print(json.dumps(report, ensure_ascii=True))
    print(END)
    return {"PASS": 0, "FAIL": 1, "INVALID": 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
