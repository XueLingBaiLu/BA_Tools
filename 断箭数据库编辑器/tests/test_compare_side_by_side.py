"""A5 并排比对工具的**尺子自证**：合成夹具，不依赖 Blender、不依赖游戏文件。

为什么这样测（设计稿 §11 四条纪律 + AGENTS §0.9 动作 2）：
  · 正控：自造网格 + UV + 基色图，按**已知相似变换**造一张 fake 游戏截图 ⇒ 判据必须 PASS；
  · 负控 1：基色图换成另一张 ⇒ control.red 必须 true（P3）；
  · 负控 2：坏路径 / 无 alpha / 几何在 clip 外 ⇒ verdict 不许 PASS；
  · 负控 3：大角度 / 换形状 ⇒ 要么真对齐回来，要么 INVALID，⛔ 不许瞎 PASS；
  · 反假绿：把**本尊**当正控喂进去，control.red 必须 false（否则这条正控永远红 = 空转）；
  · 光栅器自证：夹具用**独立实现**（逐像素反解 M@[x,y,z,1] + 交叉数）算覆盖与反照率，
    与工具的 clip 空间 + 重心坐标两条路对拍 ⇒ raster_alpha_iou 必须 ≥ 0.98。
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "compare_side_by_side.py"
BEGIN = "BAMOD_COMPARE_BEGIN"
END = "BAMOD_COMPARE_END"
ALIGN_IOU_MIN = 0.80
RASTER_ALPHA_IOU_MIN = 0.98
CONTROL_MARGIN = 0.05

SHAPE = [(-60.0, -30.0), (30.0, -30.0), (55.0, -12.0), (55.0, 20.0), (30.0, 34.0), (-60.0, 34.0)]
XMIN, XMAX = min(v[0] for v in SHAPE), max(v[0] for v in SHAPE)
YMIN, YMAX = min(v[1] for v in SHAPE), max(v[1] for v in SHAPE)
U0, V0, U1, V1 = 0.10, 0.10, 0.70, 0.70
BACKGROUND = (168, 182, 196)
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


# --------------------------------------------------------------------------
# 夹具：独立实现（不 import 被测工具）
# --------------------------------------------------------------------------


def _uv_of(x: float, y: float) -> tuple[float, float]:
    u = U0 + (U1 - U0) * (x - XMIN) / (XMAX - XMIN)
    v = V0 + (V1 - V0) * (y - YMIN) / (YMAX - YMIN)
    return u, v


def _mat_of(flat: list[float]) -> list[list[float]]:
    return [flat[0:4], flat[4:8], flat[8:12], flat[12:16]]


def _mat_mul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [[sum(left[i][k] * right[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _rot_y_flat(deg: float) -> list[float]:
    t = math.radians(deg)
    return [
        math.cos(t), 0.0, math.sin(t), 0.0,
        0.0, 1.0, 0.0, 0.0,
        -math.sin(t), 0.0, math.cos(t), 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def _ortho_projection(width: int, height: int) -> list[float]:
    return [
        2.0 / width, 0, 0, 0,
        0, -2.0 / height, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]


def _perspective_projection(width: int, height: int, fov_deg: float = 45.0) -> list[float]:
    aspect = width / float(height)
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    near, far = 1.0, 5000.0
    return [
        f / aspect, 0, 0, 0,
        0, f, 0, 0,
        0, 0, (far + near) / (near - far), 2 * far * near / (near - far),
        0, 0, -1, 0,
    ]


def _solve_local(
    m: list[list[float]], ndc_x: float, ndc_y: float
) -> tuple[float, float, float] | None:
    """解 M @ [x, y, 0, 1] = w * [ndc_x, ndc_y, *, 1] 里的 (x, y, w)。"""
    a11 = m[0][0] - ndc_x * m[3][0]
    a12 = m[0][1] - ndc_x * m[3][1]
    b1 = ndc_x * m[3][3] - m[0][3]
    a21 = m[1][0] - ndc_y * m[3][0]
    a22 = m[1][1] - ndc_y * m[3][1]
    b2 = ndc_y * m[3][3] - m[1][3]
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-12:
        return None
    x = (b1 * a22 - a12 * b2) / det
    y = (a11 * b2 - b1 * a21) / det
    return x, y, m[3][0] * x + m[3][1] * y + m[3][3]


def _inside(poly: list[tuple[float, float]], x: float, y: float) -> bool:
    hit = False
    count = len(poly)
    for index in range(count):
        x0, y0 = poly[index]
        x1, y1 = poly[(index + 1) % count]
        if (y0 > y) != (y1 > y):
            cross = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < cross:
                hit = not hit
    return hit


def _make_texture(path: Path, variant: str) -> None:
    img = Image.new("RGB", (128, 128), (112, 112, 112))
    draw = ImageDraw.Draw(img)
    if variant == "primary":
        draw.rectangle([16, 44, 96, 52], fill=(232, 232, 232))
        draw.rectangle([16, 44, 96, 46], fill=(26, 26, 26))
        draw.rectangle([30, 60, 38, 92], fill=(26, 26, 26))
        draw.rectangle([48, 60, 56, 92], fill=(26, 26, 26))
        draw.rectangle([78, 66, 96, 96], fill=(176, 40, 34))
        draw.line([(20, 106), (60, 78)], fill=(64, 64, 64), width=3)
    elif variant == "control":
        draw.ellipse([24, 60, 96, 118], outline=(40, 92, 52), width=8)
        draw.line([(10, 20), (110, 110)], fill=(52, 122, 72), width=10)
        draw.rectangle([68, 18, 110, 40], fill=(198, 198, 64))
    img.save(path)


def _write_scene(
    path: Path,
    width: int,
    height: int,
    projection: list[float],
    camera: list[float],
    obj_matrix: list[float],
    texture: Path,
    clip: tuple[float, float],
    z_shift: float = 0.0,
) -> None:
    triangles: list[int] = []
    for index in range(1, len(SHAPE) - 1):
        triangles.extend([0, index, index + 1])
    moved = list(obj_matrix)
    moved[11] = moved[11] + z_shift
    scene = {
        "camera": {
            "matrix_world": camera,
            "projection": projection,
            "clip_start": clip[0],
            "clip_end": clip[1],
        },
        "resolution": [width, height],
        "textures": {"BODY": str(texture)},
        "objects": [
            {
                "name": "body",
                "material": "BODY",
                "matrix_world": moved,
                "vertices": [[x, y, 0.0] for x, y in SHAPE],
                "triangles": triangles,
                "uv": [list(_uv_of(x, y)) for x, y in SHAPE],
            }
        ],
    }
    path.write_text(json.dumps(scene, ensure_ascii=True, indent=1), encoding="utf-8")


def _raycast(
    width: int,
    height: int,
    projection: list[float],
    view: list[float],
    obj_matrix: list[float],
    texture: Path,
    clip: tuple[float, float],
) -> Image.Image:
    """独立实现：逐像素反解 + 交叉数 + 仿射 UV 采样。"""
    m = _mat_mul(_mat_mul(_mat_of(projection), _mat_of(view)), _mat_of(obj_matrix))
    texels = Image.open(texture).convert("RGB").tobytes()
    coverage = bytearray(width * height)
    data = bytearray(width * height * 3)
    for py in range(height):
        ndc_y = 1.0 - 2.0 * (py + 0.5) / height
        for px in range(width):
            ndc_x = 2.0 * (px + 0.5) / width - 1.0
            solved = _solve_local(m, ndc_x, ndc_y)
            if solved is None:
                continue
            x, y, w = solved
            if w <= 0.0 or w < clip[0] or w > clip[1]:
                continue
            if not _inside(SHAPE, x, y):
                continue
            u, v = _uv_of(x, y)
            tx = min(127, max(0, int(u * 127)))
            ty = min(127, max(0, int((1.0 - v) * 127)))
            texel = (ty * 128 + tx) * 3
            index = py * width + px
            coverage[index] = 255
            data[index * 3] = texels[texel]
            data[index * 3 + 1] = texels[texel + 1]
            data[index * 3 + 2] = texels[texel + 2]
    render = Image.frombytes("RGB", (width, height), bytes(data)).convert("RGBA")
    render.putalpha(Image.frombytes("L", (width, height), bytes(coverage)))
    return render


def _similarity_inverse(
    rot: float, scale: float, dx: float, dy: float, cx: float, cy: float
) -> tuple[float, ...]:
    theta = math.radians(rot)
    cos, sin = math.cos(theta), math.sin(theta)
    a, b = cos / scale, sin / scale
    c0 = cx - (a * (cx + dx) + b * (cy + dy))
    c1 = cy - (-b * (cx + dx) + a * (cy + dy))
    return (a, b, c0, -b, a, c1)


def _build_game(
    render: Image.Image,
    path: Path,
    size: tuple[int, int],
    rot: float,
    scale: float,
    dx: float,
    dy: float,
    seed: int = 7,
) -> None:
    width, height = size
    if render.mode == "L":
        # ⚠ 两个坑（都实测踩过）：
        # ① L→RGBA 会把 alpha 填成 255（不是"用亮度当 alpha"）⇒ 整幅图都吃光照，
        #    背景变成带噪声的暗块，分割把 66% 的像素当前景；
        # ② 车身用**白色**时，光照暗侧 (255*0.72) 与浅色背景的亮度差只有 ~7
        #    ⇒ 40% 的车身被分割丢掉。所以这里用暗色车身。
        source = render
        render = Image.new("RGB", source.size, FIT_TANK).convert("RGBA")
        render.putalpha(source)
    warped = render.transform(
        size,
        Image.AFFINE,
        _similarity_inverse(rot, scale, dx, dy, render.width / 2.0, render.height / 2.0),
        resample=Image.NEAREST,
    )
    base = Image.new("RGB", size, BACKGROUND)
    # ⚠ paste(im, box, mask) 拿 RGBA 当 mask 时用的是**亮度**不是 alpha：
    # 车身色均匀 ⇒ 整幅图都被按同一个比例掺进来，背景不再是背景（分割随即失真）。
    base.paste(warped, (0, 0), warped.getchannel("A"))
    data = bytearray(base.tobytes())
    alpha = warped.getchannel("A").tobytes()
    rng = random.Random(seed)
    for index in range(width * height):
        if alpha[index] < 40:
            continue
        light = 0.72 + 0.9 * ((index % width) / float(width))
        base_index = index * 3
        for channel in range(3):
            value = min(255.0, data[base_index + channel] * light)
            value = 255.0 * (value / 255.0) ** 1.1 + rng.gauss(0.0, 3.5)
            data[base_index + channel] = max(0, min(255, int(round(value))))
    Image.frombytes("RGB", size, bytes(data)).save(path)


# --------------------------------------------------------------------------
# 跑工具 + 缓存（同一档只跑一次）
# --------------------------------------------------------------------------

_CACHE: dict[str, tuple[int, dict, Path]] = {}


def _run(shop: Path, tag: str, **overrides: str) -> tuple[int, dict, Path]:
    if tag in _CACHE:
        return _CACHE[tag]
    args = {
        "render": str(shop / "render_ortho.png"),
        "render_label": "",
        "scene": str(shop / "scene_ortho.json"),
        "game": str(shop / "game_ortho.png"),
        "game_label": "",
        "texture": str(shop / "tex_primary.png"),
        "control_texture": str(shop / "tex_control.png"),
    }
    args.update(overrides)
    out_dir = shop / f"out_{tag}"
    command = [sys.executable, str(TOOL)]
    for key, value in args.items():
        if value:
            command += [f"--{key.replace('_', '-')}", value]
    command += ["--out-dir", str(out_dir)]
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    payload = ""
    if BEGIN in done.stdout:
        payload = done.stdout.split(BEGIN)[1].split(END)[0].strip()
    report = json.loads(payload) if payload else {}
    _CACHE[tag] = (done.returncode, report, out_dir)
    return _CACHE[tag]


@pytest.fixture(scope="module")
def shop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("w11_compare")
    _make_texture(work / "tex_primary.png", "primary")
    _make_texture(work / "tex_control.png", "control")
    Image.new("RGB", (128, 128), (110, 110, 110)).save(work / "tex_flat.png")

    width, height = 256, 192
    projection = _ortho_projection(width, height)
    _write_scene(
        work / "scene_ortho.json", width, height, projection, IDENTITY, IDENTITY,
        work / "tex_primary.png", (0.1, 5000.0),
    )
    render = _raycast(
        width, height, projection, IDENTITY, IDENTITY, work / "tex_primary.png",
        (0.1, 5000.0),
    )
    render.save(work / "render_ortho.png")
    _build_game(render, work / "game_ortho.png", (320, 280), 17.0, 1.15, 20.0, -12.0)
    _build_game(render, work / "game_bigrot.png", (420, 420), 100.0, 1.9, 30.0, -20.0)

    pw, ph = 384, 288
    persp = _perspective_projection(pw, ph)
    camera = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 300, 0, 0, 0, 1]
    view = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -300, 0, 0, 0, 1]
    obj = _rot_y_flat(35.0)
    _write_scene(
        work / "scene_persp.json", pw, ph, persp, camera, obj, work / "tex_primary.png",
        (50.0, 5000.0),
    )
    prender = _raycast(
        pw, ph, persp, view, obj, work / "tex_primary.png", (50.0, 5000.0)
    )
    prender.save(work / "render_persp.png")
    _build_game(prender, work / "game_persp.png", (460, 380), -12.0, 1.25, 15.0, 8.0)

    # 负控：几何被推到**相机背后**（w < 0）⇒ 覆盖 0，⛔ 不许 PASS
    _write_scene(
        work / "scene_behind.json", pw, ph, persp, camera, obj, work / "tex_primary.png",
        (50.0, 5000.0), z_shift=5000.0,
    )
    # 负控：render 没有 alpha
    render.convert("RGB").save(work / "render_noalpha.png")
    # 负控：alpha 与几何完全不符（一个圆盘，面积故意接近）
    bad = render.copy()
    disc = Image.new("L", (width, height), 0)
    ImageDraw.Draw(disc).ellipse([60, 40, 200, 150], fill=255)
    bad.putalpha(disc)
    bad.save(work / "render_badalpha.png")
    # 负控：换形状（细长斜条）当游戏截图
    wrong = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    long_sliver = [(10, 20), (240, 150), (240, 158), (10, 28)]
    ImageDraw.Draw(wrong).polygon(long_sliver, fill=(120, 120, 120, 255))
    _build_game(wrong, work / "game_wrongshape.png", (320, 280), 0.0, 1.0, 0.0, 0.0)
    return work


# --------------------------------------------------------------------------
# 正控
# --------------------------------------------------------------------------


def test_ortho_fixture_passes_and_control_turns_red(shop: Path) -> None:
    code, report, _ = _run(shop, "ortho")
    assert report["raster_alpha_iou"] >= RASTER_ALPHA_IOU_MIN, report["lines"]
    assert report["silhouette_iou"] >= ALIGN_IOU_MIN, report["lines"]
    assert report["pattern_f1"] >= 0.50, report["lines"]
    assert report["control"]["red"] is True, report["lines"]
    assert report["control"]["pattern_f1"] < report["pattern_f1"] - CONTROL_MARGIN
    assert report["pred_mark_px"] > 0 and report["obs_edge_px"] > 0
    assert code == 0 and report["verdict"] == "PASS", report["lines"]


def test_perspective_interpolation_matches_independent_oracle(shop: Path) -> None:
    """1/w 透视插值那一支：夹具自己求逆，工具必须挑逆链并把 IoU 做到 0.98 以上。"""
    code, report, _ = _run(
        shop,
        "persp",
        render=str(shop / "render_persp.png"),
        scene=str(shop / "scene_persp.json"),
        game=str(shop / "game_persp.png"),
    )
    assert report["raster_alpha_iou"] >= RASTER_ALPHA_IOU_MIN, report["lines"]
    assert any("inverse(matrix_world)" in line for line in report["lines"]), report["lines"]
    assert code == 0 and report["verdict"] == "PASS", report["lines"]
    assert report["control"]["red"] is True


# --------------------------------------------------------------------------
# 负控 1（P3 正控能红）—— 以及反假绿：本尊当正控必须**不**红
# --------------------------------------------------------------------------


def test_control_with_the_real_texture_must_not_redden(shop: Path) -> None:
    """把本尊当正控喂进去 ⇒ delta 0 ⇒ red=false ⇒ 判 FAIL。

    没有这一条，「control.red 恒 true」也能算通过 —— 那就是一把永远红的尺子。
    """
    code, report, _ = _run(
        shop, "selfcontrol", control_texture=str(shop / "tex_primary.png")
    )
    assert report["control"]["red"] is False, report["lines"]
    assert code == 1 and report["verdict"] == "FAIL", report["lines"]


def test_control_flag_is_optional(shop: Path) -> None:
    code, report, _ = _run(shop, "nocontrol", control_texture="")
    assert code == 0 and report["verdict"] == "PASS", report["lines"]
    assert report["control"]["red"] is False


# --------------------------------------------------------------------------
# 负控 2：坏路径 / 无 alpha / 几何在 clip 外 —— ⛔ 不许 PASS
# --------------------------------------------------------------------------


def test_missing_game_path_is_invalid(shop: Path) -> None:
    code, report, _ = _run(shop, "badpath", game=str(shop / "nope.png"))
    assert code == 2 and report["verdict"] == "INVALID", report["lines"]


def test_render_without_alpha_is_invalid(shop: Path) -> None:
    code, report, _ = _run(
        shop, "noalpha", render=str(shop / "render_noalpha.png")
    )
    assert code == 2 and report["verdict"] == "INVALID", report["lines"]
    assert any("alpha" in line for line in report["lines"]), report["lines"]


def test_geometry_behind_the_camera_is_invalid(shop: Path) -> None:
    code, report, _ = _run(
        shop,
        "behind",
        render=str(shop / "render_persp.png"),
        scene=str(shop / "scene_behind.json"),
        game=str(shop / "game_persp.png"),
    )
    assert code == 2 and report["verdict"] == "INVALID", report["lines"]


def test_raster_alpha_gate_can_redden(shop: Path) -> None:
    """尺子自证那条门限自己也要能红：alpha 换个形状 ⇒ raster_alpha_iou 掉下来。"""
    code, report, _ = _run(
        shop, "badalpha", render=str(shop / "render_badalpha.png")
    )
    assert report["raster_alpha_iou"] < RASTER_ALPHA_IOU_MIN, report["lines"]
    assert code == 2 and report["verdict"] == "INVALID", report["lines"]


def test_flat_texture_fails_on_anti_idle(shop: Path) -> None:
    """基色图没有图案 ⇒ 预测标记为空 ⇒ 反空转判 FAIL（⛔ 不许 PASS）。"""
    code, report, _ = _run(shop, "flat", texture=str(shop / "tex_flat.png"))
    assert report["pred_mark_px"] == 0, report["lines"]
    assert code == 1 and report["verdict"] == "FAIL", report["lines"]


# --------------------------------------------------------------------------
# 负控 3：大角度 / 换形状 —— 要么真对齐，要么 INVALID
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["bigrot", "wrongshape"])
def test_never_pass_on_a_bad_game_frame(shop: Path, tag: str) -> None:
    code, report, _ = _run(shop, tag, game=str(shop / f"game_{tag}.png"))
    if report["verdict"] == "PASS":
        # 只有**真的**对齐上去了才允许 PASS，且读数必须过门限
        assert report["silhouette_iou"] >= ALIGN_IOU_MIN, report["lines"]
        assert code == 0
    else:
        assert report["verdict"] in {"INVALID", "FAIL"}, report["lines"]
        assert code in (1, 2)



# --------------------------------------------------------------------------
# 相机反推（--fit-camera）的尺子自证：用一个**已知相机**的 3D 盒子
# --------------------------------------------------------------------------

FIT_TRUTH = {"azimuth_deg": 25.0, "elevation_deg": 18.0, "distance": 9.0, "focal_length_mm": 50.0}
FIT_RES = (320, 240)
FIT_GAME = (400, 320)
FIT_HALF = (2.0, 0.6, 0.8)
#: 偏心量要够大：盒子若近似点对称，az 与其镜像的剪影只差一个 180° 面内旋转
#: （那在相似变换的搜索空间里）⇒ 拟合会落进镜像盆地（实测 az 25 -> 325）。
FIT_OFFSET = (1.20, -0.35, 0.95)
#: 车身色要够暗：不然光照暗侧与浅色背景分不开（夹具的可分性，不是判据的松紧）。
FIT_TANK = (74, 92, 62)


def _fit_orbit(azimuth_deg: float, elevation_deg: float, distance: float, focal_mm: float, res):
    """**独立**再写一遍相机构造（测试要能抓住工具那侧写错）。"""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    width, height = res
    location = (
        distance * math.cos(el) * math.cos(az),
        distance * math.cos(el) * math.sin(az),
        distance * math.sin(el),
    )
    length = math.sqrt(sum(v * v for v in location))

    def unit(vector):
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector]

    z_axis = unit(location)
    dot = z_axis[2]
    y_axis = unit([-dot * z_axis[0], -dot * z_axis[1], 1.0 - dot * z_axis[2]])
    x_axis = [
        y_axis[1] * z_axis[2] - y_axis[2] * z_axis[1],
        y_axis[2] * z_axis[0] - y_axis[0] * z_axis[2],
        y_axis[0] * z_axis[1] - y_axis[1] * z_axis[0],
    ]
    camera = [
        [x_axis[0], y_axis[0], z_axis[0], location[0]],
        [x_axis[1], y_axis[1], z_axis[1], location[1]],
        [x_axis[2], y_axis[2], z_axis[2], location[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]
    focal_px = max(width, height) * focal_mm / 36.0
    near, far = 0.1, 100.0
    projection = [
        [2.0 * focal_px / width, 0.0, 0.0, 0.0],
        [0.0, 2.0 * focal_px / height, 0.0, 0.0],
        [0.0, 0.0, -(far + near) / (far - near), -2.0 * far * near / (far - near)],
        [0.0, 0.0, -1.0, 0.0],
    ]
    assert length > 0.0
    return camera, projection


def _view_of(camera):
    """相机的刚性逆（c2w → w2c）：R^T | -R^T t。"""
    flipped = [[camera[i][j] for i in range(3)] for j in range(3)]
    moved = [-sum(flipped[i][k] * camera[k][3] for k in range(3)) for i in range(3)]
    return [
        [flipped[0][0], flipped[0][1], flipped[0][2], moved[0]],
        [flipped[1][0], flipped[1][1], flipped[1][2], moved[1]],
        [flipped[2][0], flipped[2][1], flipped[2][2], moved[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _box_vertices():
    """⚠ 盒子必须**偏心**：正中、且点对称的盒子有 (az,el) == (az+180,-el) 的真歧义，
    实测那个歧义把拟合引到 az=39 / el=-18.5（真值 25 / 18）—— 是夹具的锅，不是搜索的锅。
    """
    return [
        (sx * FIT_HALF[0] + FIT_OFFSET[0], sy * FIT_HALF[1] + FIT_OFFSET[1],
         sz * FIT_HALF[2] + FIT_OFFSET[2])
        for sx in (-1, 1)
        for sy in (-1, 1)
        for sz in (-1, 1)
    ]


def _box_triangles():
    def index(ix, iy, iz):
        return 4 * ix + 2 * iy + iz

    tris = []
    for ix in (0, 1):
        for iy in (0, 1):
            tris += [(index(ix, iy, 0), index(ix, iy, 1), index(1 - ix, iy, 0))]
            tris += [(index(1 - ix, iy, 0), index(ix, iy, 1), index(1 - ix, iy, 1))]
    for ix in (0, 1):
        for iz in (0, 1):
            tris += [(index(ix, 0, iz), index(ix, 1, iz), index(ix, 0, 1 - iz))]
            tris += [(index(ix, 0, 1 - iz), index(ix, 1, iz), index(ix, 1, 1 - iz))]
    for iy in (0, 1):
        for iz in (0, 1):
            tris += [(index(0, iy, iz), index(1, iy, iz), index(0, iy, 1 - iz))]
            tris += [(index(0, iy, 1 - iz), index(1, iy, iz), index(1, iy, 1 - iz))]
    return tris


def _convex_hull(points):
    """盒子有 2 个投影到内部的角度 ⇒ 角度排序会自交，必须真凸包。"""
    pts = sorted(set(points))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    if len(pts) <= 2:
        return pts
    lower = []
    for point in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _box_silhouette(camera, projection, size):
    view = _view_of(camera)
    matrix = _mat_mul(projection, view)
    width, height = size
    points = []
    for vertex in _box_vertices():
        clip = [
            sum(matrix[i][j] * (vertex + (1.0,))[j] for j in range(4)) for i in range(4)
        ]
        points.append(
            (
                (clip[0] / clip[3] * 0.5 + 0.5) * width,
                (0.5 - clip[1] / clip[3] * 0.5) * height,
            )
        )
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(_convex_hull(points), fill=255)
    return mask


def _box_uv(vertex, uv_scale):
    u = 0.15 + 0.6 * (vertex[0] / uv_scale[0] + 0.5)
    v = 0.15 + 0.6 * (vertex[2] / uv_scale[1] + 0.5)
    return [u, v]


def _fit_scene(path, camera, projection, texture, size):
    triangles = []
    for tri in _box_triangles():
        triangles += list(tri)
    uv_scale = (2 * FIT_HALF[0], 2 * FIT_HALF[2])
    scene = {
        "camera": {
            "matrix_world": [value for row in camera for value in row],
            "projection": [value for row in projection for value in row],
            "clip_start": 0.1,
            "clip_end": 100.0,
        },
        "resolution": list(size),
        "textures": {"BODY": str(texture)},
        "objects": [
            {
                "name": "box",
                "material": "BODY",
                "matrix_world": IDENTITY,
                "vertices": [list(v) for v in _box_vertices()],
                "triangles": triangles,
                "uv": [_box_uv(v, uv_scale) for v in _box_vertices()],
            }
        ],
    }
    path.write_text(json.dumps(scene, ensure_ascii=True, indent=1), encoding="utf-8")


def _fit_shop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("w11_fit")
    _make_texture(work / "tex.png", "primary")
    camera, projection = _fit_orbit(
        FIT_TRUTH["azimuth_deg"], FIT_TRUTH["elevation_deg"], FIT_TRUTH["distance"],
        FIT_TRUTH["focal_length_mm"], FIT_RES,
    )
    _fit_scene(work / "scene.json", camera, projection, work / "tex.png", FIT_RES)
    mask = _box_silhouette(camera, projection, FIT_RES)
    render = Image.new("RGB", FIT_RES, (120, 120, 120)).convert("RGBA")
    render.putalpha(mask)
    render.save(work / "render.png")
    _build_game(mask, work / "game.png", FIT_GAME, 12.0, 1.35, 20.0, -14.0, seed=11)
    disc = Image.new("L", FIT_RES, 0)
    ImageDraw.Draw(disc).ellipse([40, 30, 210, 150], fill=255)
    _build_game(disc, work / "game_disc.png", FIT_GAME, 12.0, 1.35, 20.0, -14.0, seed=11)
    # 相机反推那把尺子只考**搜索**：喂**精确**的游戏侧掩码，别把分割的保真度混进来
    # （实测分割把剪影缩了 ~8%，会让"最优相机"不是真值相机 —— 那是分割的锅）。
    for name, source in (("mask.png", mask), ("mask_disc.png", disc)):
        _warp_mask(source, work / name, FIT_GAME, 12.0, 1.35, 20.0, -14.0)
    return work


def _warp_mask(
    source: Image.Image, path: Path, size, rot: float, scale: float, dx: float, dy: float
):
    warped = source.transform(
        size, Image.AFFINE,
        _similarity_inverse(rot, scale, dx, dy, source.width / 2.0, source.height / 2.0),
        resample=Image.NEAREST,
    )
    warped.save(path)


def _run_fit(shop: Path, tag: str, game: str, mask: str) -> dict:
    out_dir = shop / f"fit_{tag}"
    command = [
        sys.executable, str(TOOL),
        "--render", str(shop / "render.png"),
        "--scene", str(shop / "scene.json"),
        "--game", str(shop / game),
        "--game-mask", str(shop / mask),
        "--texture", str(shop / "tex.png"),
        "--out-dir", str(out_dir),
        "--fit-camera", "--fit-az", "120", "--fit-elev", "5",
        "--fit-distance", "14", "--fit-focal", "50",
    ]
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    payload = ""
    if BEGIN in done.stdout:
        payload = done.stdout.split(BEGIN)[1].split(END)[0].strip()
    return json.loads(payload) if payload else {}


@pytest.fixture(scope="module")
def fit_shop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _fit_shop(tmp_path_factory)


@pytest.mark.xfail(strict=True, reason="如实红：相机反推尺子没自证过 —— 见下")
def test_fit_camera_recovers_a_known_camera(fit_shop: Path) -> None:
    """尺子先自证：已知相机造 fake 游戏图 ⇒ 必须把相机搜回来（角度差 ≤8° + IoU ≥ 0.95）。

    ⚠ 2026-10-04 实测**没过**，如实留在这里（xfail strict：真过了会变红提醒重评）：
      真值 (az 25, el 18, d 9, f 50)；搜回 (az 325.5, el -2.0, d 9.1, f 42.5) ⇒ az 差 60°。
      但**搜索本身没坏**：同一张精确掩码下，搜回那条 IoU=0.9461，真值那条只有 0.8683
      —— 搜索找到的是「更贴掩码」的相机，而真值相机不是这张合成掩码的最优点
      （盒子剪影在这组角度附近仍有近简并，我 4 次改夹具都没消掉）。
    ⇒ 按 Lead 口径：**--fit-camera 降级为辅助读数**，不许拿它拟合真实截图、
      不许用它宣称对齐成立。要重新启用，先把夹具的简并消掉或换非盒子几何。
    """
    report = _run_fit(fit_shop, "truth", "game.png", "mask.png")
    fitted = report.get("camera_fit")
    assert fitted is not None, report.get("lines")
    delta_az = abs((fitted["azimuth_deg"] - FIT_TRUTH["azimuth_deg"] + 180.0) % 360.0 - 180.0)
    delta_el = abs(fitted["elevation_deg"] - FIT_TRUTH["elevation_deg"])
    assert delta_az <= 8.0, fitted
    assert delta_el <= 8.0, fitted
    assert fitted["silhouette_iou"] >= 0.95, fitted


def test_fit_camera_gate_can_redden(fit_shop: Path) -> None:
    """门限能红：游戏侧换成**另一个形状**（圆盘）⇒ 拟合 IoU 必须掉到 0.95 以下。"""
    report = _run_fit(fit_shop, "disc", "game_disc.png", "mask_disc.png")
    fitted = report.get("camera_fit")
    assert fitted is not None, report.get("lines")
    assert fitted["silhouette_iou"] < 0.95, fitted

# --------------------------------------------------------------------------
# 契约（③ 的⑥面板按这一份解析）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["badpath", "noalpha", "behind", "badalpha", "noscene"])
def test_invalid_paths_report_null_not_zero(shop: Path, tag: str) -> None:
    """INVALID 时**没算**的字段必须是 null（⛔ 不是 0.0 / false）。

    不然机器读 JSON 会把 pattern_f1=0.0 读成「图案 0 分」、把 control.red=false 读成「正控没红」，
    而同一份 lines 里可能正写着「预测标记=5408」—— 那是会骗人的报数（④ 复核点名）。
    """
    overrides = {
        "badpath": {"game": str(shop / "nope.png")},
        "noalpha": {"render": str(shop / "render_noalpha.png")},
        "behind": {
            "render": str(shop / "render_persp.png"),
            "scene": str(shop / "scene_behind.json"),
            "game": str(shop / "game_persp.png"),
        },
        "badalpha": {"render": str(shop / "render_badalpha.png")},
        "noscene": {"scene": "", "uv": ""},
    }[tag]
    code, report, _ = _run(shop, tag, **overrides)
    assert code == 2 and report["verdict"] == "INVALID", report.get("lines")
    for key in (
        "pattern_f1", "pattern_precision", "pattern_recall",
        "pred_mark_px", "obs_edge_px", "chroma_corr", "control",
    ):
        assert report[key] is None, (key, report[key])
    assert {"pattern_f1", "pred_mark_px", "control"} <= set(report["not_evaluated"])
    assert any("未计算" in line for line in report["lines"]), report["lines"]


def test_json_contract_between_markers(shop: Path) -> None:
    code, report, out_dir = _run(shop, "ortho")
    required = {
        "schema", "verdict", "silhouette_iou", "align", "pattern_f1", "pattern_precision",
        "pattern_recall", "pred_mark_px", "obs_edge_px", "chroma_corr", "control", "lines",
    }
    assert required <= set(report), sorted(set(report))
    assert set(report["align"]) == {"rot_deg", "scale", "dx", "dy"}
    assert set(report["control"]) == {"texture", "pattern_f1", "delta", "red"}
    assert report["schema"] == 1
    assert report["not_evaluated"] == []
    assert report["control"] is not None
    assert report["raster_alpha_iou"] >= RASTER_ALPHA_IOU_MIN
    assert report["align_iou_min"] == ALIGN_IOU_MIN
    assert {"PASS": 0, "FAIL": 1, "INVALID": 2}[report["verdict"]] == code
    assert (out_dir / "side_by_side.png").is_file()
    assert (out_dir / "compare.json").is_file()


def test_side_by_side_is_a_three_panel_image(shop: Path) -> None:
    _, _, out_dir = _run(shop, "ortho")
    image = Image.open(out_dir / "side_by_side.png")
    assert image.width > image.height > 300


def test_panel_titles_come_from_the_actual_sources(shop: Path) -> None:
    """标题必须按 --render / --game 的**真实来源**写，⛔ 不许写死 "game screenshot"。

    由来：Lead 拿 Unity 2022.3 同版本渲的图当 --game 喂进来，A5 PASS，
    可并排图中间那格却写着 "game screenshot" —— 这图要进交接当证据，标签会骗到人。
    """
    unity = shop / "unity_lit.png"
    unity.write_bytes((shop / "game_ortho.png").read_bytes())
    _, report, out_dir = _run(shop, "unitylabel", game=str(unity))
    assert report["game_label"] == "unity_lit.png", report["game_label"]
    assert report["render_label"] == "render_ortho.png", report["render_label"]
    assert "game screenshot" not in json.dumps(report, ensure_ascii=False)
    assert "blender render" not in json.dumps(report, ensure_ascii=False)
    # 标题区确实随文件名变（不是"换成别的写死字面量"）
    _, _, ortho_dir = _run(shop, "ortho")
    # ⚠ 要整条标题带一起比：第一格是 render 标题（两次跑的是同一个文件，本来就一样），
    # game 标题在第二格，起点 x ≈ 第一格宽度 + 间隔。
    unity_image = Image.open(out_dir / "side_by_side.png")
    ortho_image = Image.open(ortho_dir / "side_by_side.png")
    strip = (0, 0, min(unity_image.width, ortho_image.width), 18)
    top_unity = unity_image.crop(strip).tobytes()
    top_ortho = ortho_image.crop(strip).tobytes()
    assert top_unity != top_ortho


def test_explicit_labels_win(shop: Path) -> None:
    _, report, _ = _run(
        shop, "labels", game_label="unity_unlit", render_label="blender_ref"
    )
    assert report["game_label"] == "unity_unlit"
    assert report["render_label"] == "blender_ref"


def test_uv_alias_reads_a_scene_json(shop: Path) -> None:
    """--uv 是 --scene 的旧别名：③ 面板按冻结 CLI 传的话也得能跑。"""
    code, report, _ = _run(
        shop, "uvalias", scene="", uv=str(shop / "scene_ortho.json")
    )
    assert code == 0 and report["verdict"] == "PASS", report["lines"]
    assert any("旧别名" in line for line in report["lines"]), report["lines"]


def test_neither_scene_nor_uv_is_invalid(shop: Path) -> None:
    code, report, _ = _run(shop, "noscene", scene="", uv="")
    assert code == 2 and report["verdict"] == "INVALID", report["lines"]


def test_a_uv_pass_png_is_refused_with_a_clear_message(shop: Path) -> None:
    """③ 面板若还按老冻结行传 --uv <png>，要拿到一句人话，⛔ 不是 PASS。"""
    code, report, _ = _run(shop, "uvpng", scene="", uv=str(shop / "render_ortho.png"))
    assert code == 2 and report["verdict"] == "INVALID", report["lines"]
    assert any("scene.json" in line for line in report["lines"]), report["lines"]
