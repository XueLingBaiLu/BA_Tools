r"""W2 五条契约的机械判据（工单 §3 那张表）。

**C1 坐标**：det(乙式) == +1；互为逆往返误差 0；真实网格绕序自洽率。
通过线 = frac 1.0000（7 网格 6622 三角，**扫全量**）；
负控 = 倒绕序 -> 0/6622，以及换 det=-1 的甲式 -> 0/6622。

**C2 颜色空间**：m_ColorSpace 1 -> sRGB、0 -> Non-Color，逐张**显式**设置。
通过线 = 11 张真实贴图逐张一致 + 3 张法线不是 sRGB；
负控 = 把法线图标成 sRGB -> 判据**点名**那张；未知取值 -> 抛错。

**C3 法线**：DXT5nm 重建（X 在 A、Y 在 G）后 x^2+y^2>1 占比。
通过线 = 3 张真实法线图 < 0.03；负控 = 把 (R,G) 当 X,Y -> 占比 1.0000。

**C4 注册探针**：Blender -b 下算子 / 面板 / PropertyGroup 都能看到。
通过线 = 三个都 True；负控 = 用没注册的名字时 dir() 说 False 而 hasattr 说 True
（这就是它被禁的原因），且拿 Operator 类名查 bpy.types 也查不到。

**C5 分层守**：asset_kernel/ 里 bpy 与 PySide6 0 命中。
通过线 = 导入扫描 + 文本扫描都 0 命中；负控 = 临时目录种一行 import bpy -> 两条尺子当场红。

判据一行：**pytest tests/test_contracts.py -q -> 通过数 从 0 变 5**。

三条纪律（AGENTS §0.9 动作 2 / 设计稿 §11 四条纪律）：

1. **每条判据自带负控** —— 喂一个已知违规的点进去，读数必须变红；
   负控就在同一个用例里，红不动就说明尺子是坏的。
2. **尺子只用产品自己的标识**（模块::函数 / 容器属性），不用方位词。
3. **计数类判据扫全量**（没有 --limit 这种只用于显示的参数）。

为什么用真实资产而不是纯造夹具：绕序 / 颜色空间 / 法线这三条的口径都是**对游戏实测**
定下来的（设计稿 §11 A2/A3）。拿自造的假网格去验「绕序自洽」是自证 —— 造的时候按什么
绕序写，量出来就是什么，尺子永远不会红。真实件很便宜：ammoprefabs 包 0.2 s 打开，
units 包 5~6 s 打开一次（C2 / C3 共用）。

为什么没走 tests/test_ufs_writeback.py 里的 _build_sf / _build_container：那是**容器层**的
最小夹具（W1 的判据落在 SerializedFile 不变量上，所以够用）。本单的三条契约落在
**游戏资产的真实数值**上，小夹具里没有真实网格，也没有 m_ColorSpace。写侧/读侧的容器
不变量仍由 W1 那三条用例守着，本文件不重复。
"""

from __future__ import annotations

import ast
import functools
import json
import os
import random
import re
import subprocess
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import colorspace, coords, mesh, normals

# --------------------------------------------------------------------------- #
# 现场与共用件
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
KERNEL_DIR = REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel"
SELFTEST = REPO_ROOT / "blender_addon" / "selftest.py"

#: 本机 Blender（不在 PATH；工单 §0 的本机环境一段）。
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")

#: 真实件（bundle 名字里的哈希随游戏更新会变，所以按前缀找）。
AMMO_PREFIX = "ammoprefabs"
UNITS_PREFIX = "units_assets_all"

#: 设计稿 §11 A2 记下的锚点读数：7 个内联网格 / 6622 个三角。
EXPECTED_MESHES = 7
EXPECTED_TRIANGLES = 6622
#: 同一批网格里 .resS 流式的个数（本版不解，见 mesh.py 的 docstring）。
EXPECTED_STREAMED = 79

#: 11 张真实贴图：名字 / Unity m_ColorSpace / 槽位 / 材质。
#: 出处在 _work/blender-design/assets/textures/channel_stats.json（export_textures.py 生成），
#: 与设计稿 §3.3/§3.5 一致；本单的用例会**再从 bundle 现场读一遍**做交叉核对。
TEXTURE_TABLE: tuple[tuple[str, int, str, str], ...] = (
    ("RU_T72B_1_BaseMap", 1, "Layer_A97CDC25", "RU_T72B 1"),
    ("RU_Track7290_Standard_22_AlbedoTransparency", 1, "_BaseColorMap", "RU_T72B_track"),
    ("RU_Track7290_Standard_22_Normal", 0, "_NormalMap", "RU_T72B_track"),
    ("Pilot_helo_low_US_Pilot_Helicopter_BaseMap", 1, "_BaseColorMap", "US_Pilot_Helicopter"),
    ("Pilot_helo_low_US_Pilot_Helicopter_MaskMap", 1, "_MaskMap", "US_Pilot_Helicopter"),
    ("Pilot_helo_low_US_Pilot_Helicopter_Normal", 0, "_NormalMap", "US_Pilot_Helicopter"),
    ("US_AH64_longbow003_AH_64_Green_BaseMap", 1, "Layer_A97CDC25", "US_AH_64_Green"),
    ("Soap_T", 0, "_IridescenceThicknessMap", "US_AH_64_Glass"),
    ("low_to_tex_OCP_BaseMap", 1, "_Albedo", "US_Army1"),
    ("low_to_tex_OCP_MaskMap", 1, "_Mask", "US_Army1"),
    ("low_to_tex_OCP_Normal", 0, "_Normal", "US_Army1"),
)

#: 3 张真实法线图（设计稿 §11 A3）。
NORMAL_MAP_NAMES: tuple[str, ...] = (
    "RU_Track7290_Standard_22_Normal",
    "Pilot_helo_low_US_Pilot_Helicopter_Normal",
    "low_to_tex_OCP_Normal",
)


def _bundle_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    return bundle_dir(root) if root is not None else None


def _find_bundle(prefix: str) -> Path | None:
    base = _bundle_dir()
    if base is None:
        return None
    hits = sorted(base.glob(f"{prefix}*.bundle"))
    return hits[0] if hits else None


def _require_bundle(prefix: str) -> Path:
    path = _find_bundle(prefix)
    if path is None:
        pytest.skip(f"找不到 {prefix}*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return path


@functools.lru_cache(maxsize=1)
def _ammo_mesh_scan() -> mesh.MeshScan:
    """ammoprefabs 包里**全部** Mesh 的解码结果（含流式那几个的名单）。"""
    import UnityPy

    env = UnityPy.load(str(_require_bundle(AMMO_PREFIX)))
    trees = [obj.read_typetree() for obj in env.objects if obj.type.name == "Mesh"]
    return mesh.scan_meshes(trees)


@functools.lru_cache(maxsize=1)
def _units_textures() -> dict[str, object]:
    """units 包里本单要用的 11 张 Texture2D（按名字命中，一次扫完）。"""
    import UnityPy

    wanted = {row[0] for row in TEXTURE_TABLE}
    env = UnityPy.load(str(_require_bundle(UNITS_PREFIX)))
    found: dict[str, object] = {}
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        data = obj.read()
        if data.m_Name in wanted:
            found[data.m_Name] = data
    if len(found) != len(wanted):
        pytest.skip(f"units 包里缺贴图：{sorted(wanted - set(found))}")
    return found


# --------------------------------------------------------------------------- #
# C1 · 坐标
# --------------------------------------------------------------------------- #
def test_c1_coordinate_contract_and_real_mesh_winding() -> None:
    """det == −1（反射）、往返误差 0、真实网格绕序 frac == 1.0000（**扫全量**）。

    2026-10-03 改口径：原来是 det=+1 的"乙式"纯旋转。真机症状是**导入的是镜像模型**
    （US_AH-1Z 尾梁 "MARINES" 读成 ƧƎИIЯAM，两舷都反），而 Unity 左手系 → Blender
    右手系的换算**必须**是 det=−1 的反射；反射会把三角形朝向翻过去，所以读侧
    （mesh.read_mesh）同时补了绕序 —— 下面**两条一起**才是这份契约的全部：
    ① det=−1（不然是镜像模型）；② 补绕序（不然里外全反）。
    """
    # ---- 1. 行列式：实算，不是写死的 -1 ----
    matrix = coords.unity_to_blender_matrix()
    det = coords.determinant_of_unity_to_blender()
    assert det == -1.0, f"现行口径（甲式）的行列式必须是 −1（反射），实测 {det!r}：{matrix}"
    # 正交性：三列互相垂直且都是单位长（det=−1 之外再钉一次"不是缩放/斜切"）
    for i in range(3):
        assert matrix[0][i] ** 2 + matrix[1][i] ** 2 + matrix[2][i] ** 2 == 1.0

    # ---- 2. 互为逆：往返误差恰好 0.0 ----
    samples = list(coords._BASIS)  # 基向量是这张尺子的定义域
    rng = random.Random(20261003)
    samples += [tuple(rng.uniform(-1000.0, 1000.0) for _ in range(3)) for _ in range(2000)]
    worst_pos = max(
        max(
            abs(a - b)
            for a, b in zip(v, coords.blender_to_unity(coords.unity_to_blender(v)), strict=True)
        )
        for v in samples
    )
    worst_scale = max(
        max(
            abs(a - b)
            for a, b in zip(
                v, coords.blender_to_unity_scale(coords.unity_to_blender_scale(v)), strict=True
            )
        )
        for v in samples
    )
    quats = [tuple(rng.uniform(-1.0, 1.0) for _ in range(4)) for _ in range(2000)]
    worst_quat = max(
        max(
            abs(a - b)
            for a, b in zip(
                q, coords.blender_to_unity_quat(coords.unity_to_blender_quat(q)), strict=True
            )
        )
        for q in quats
    )
    assert (worst_pos, worst_scale, worst_quat) == (0.0, 0.0, 0.0), (
        f"往返误差必须是 0（只有换位与取负，没有浮点运算），实测 "
        f"pos={worst_pos!r} scale={worst_scale!r} quat={worst_quat!r}"
    )

    # ---- 3. 真实网格绕序（扫全量：包里所有内联网格，不设上限） ----
    scan = _ammo_mesh_scan()
    assert (len(scan.meshes), scan.triangles, len(scan.streamed)) == (
        EXPECTED_MESHES,
        EXPECTED_TRIANGLES,
        EXPECTED_STREAMED,
    ), (
        "真实件读数与设计稿 §11 A2 的锚点不一致（游戏更新过？先复核再改锚点）："
        f"meshes={len(scan.meshes)} triangles={scan.triangles} streamed={len(scan.streamed)}"
        f" malformed={scan.malformed[:3]}"
    )

    report = coords.winding_agreement(scan.meshes)
    assert report.checked == EXPECTED_TRIANGLES, "分母必须是全量三角数（负控要有相同的分母）"
    assert report.out_of_range == 0 and report.degenerate == 0
    assert report.frac == 1.0, (
        f"真实网格绕序自洽率必须是 1.0000，实测 {report.positive}/{report.checked}"
    )

    # ---- 负控 1：倒绕序 -> 0/6622 ----
    reversed_report = coords.winding_agreement(scan.meshes, reverse=True)
    assert (reversed_report.positive, reversed_report.negative) == (0, EXPECTED_TRIANGLES)

    # ---- 负控 2：换回旧的乙式（det=+1 的镜子）-> 0/6622 ----
    # 这条把"为什么必须是一对"钉死：read_mesh 已经补过绕序，此时**只有**配 det=−1
    # 的反射才对得上；换回 +1 的旋转，绕序整体翻过去（= 里外全反）。
    yi = lambda v: (v[0], -v[2], v[1])  # noqa: E731  -- 乙式就一行，负控专用
    yi_matrix = tuple(tuple(yi(b)[r] for b in coords._BASIS) for r in range(3))
    assert coords.determinant3(yi_matrix) == 1.0, "乙式必须是纯旋转（det=+1）才算负控"
    mirrored = coords.winding_agreement(scan.meshes, transform=yi)
    assert (mirrored.positive, mirrored.negative) == (0, EXPECTED_TRIANGLES), (
        "乙式（det=+1）配上补过绕序的索引必然把绕序全部打反 —— 打不反说明尺子没咬合"
    )
    # 反向自证：反射 + 补绕序 两者**都**在场时才是 1.0000（各去掉一个都红，见上两条负控）
    assert coords.reverse_winding(coords.reverse_winding(scan.meshes[0].indices)) == tuple(
        scan.meshes[0].indices
    )


# --------------------------------------------------------------------------- #
# C2 · 颜色空间
# --------------------------------------------------------------------------- #
class _FakeImage:
    """假图像：默认颜色空间就是 Blender 新建图像的那个 sRGB（这就是陷阱所在）。"""

    def __init__(self) -> None:
        self.colorspace_settings = type("_CS", (), {"name": colorspace.SRGB})()


def test_c2_colorspace_mapping_and_explicit_assignment() -> None:
    """1 -> sRGB / 0 -> Non-Color；11 张真实贴图逐张一致；**逐张显式设置**。"""
    # ---- 1. 映射本身（字面量，不是从被测字典里读回来的） ----
    assert colorspace.colorspace_of(1) == "sRGB"
    assert colorspace.colorspace_of(0) == "Non-Color"
    assert (colorspace.SRGB, colorspace.NON_COLOR) == ("sRGB", "Non-Color")

    # ---- 2. 11 张真实贴图：现场读 m_ColorSpace，与记录表交叉核对 ----
    textures = _units_textures()
    live = {name: int(textures[name].m_ColorSpace) for name, _cs, _s, _m in TEXTURE_TABLE}  # type: ignore[attr-defined]
    mismatched = [name for name, cs, _s, _m in TEXTURE_TABLE if live[name] != cs]
    assert not mismatched, f"现场读到的 m_ColorSpace 与记录表不一致：{mismatched}"
    for name, cs, _slot, _mat in TEXTURE_TABLE:
        assert colorspace.colorspace_of(live[name]) == ("sRGB" if cs == 1 else "Non-Color")

    # ---- 3. 显式设置：默认是 sRGB，法线图必须被**改掉** ----
    for name, cs, _slot, _mat in TEXTURE_TABLE:
        fake = _FakeImage()
        assert fake.colorspace_settings.name == "sRGB", "前提变了：Blender 默认不再是 sRGB"
        written = colorspace.apply_colorspace(fake, live[name])
        assert fake.colorspace_settings.name == written
        assert written == colorspace.colorspace_of(cs)

    # ---- 4. 法线槽不得是 sRGB（按**字段**判，不是按名字判） ----
    entries = [(name, cs, slot) for name, cs, slot, _m in TEXTURE_TABLE]
    assert len(entries) == 11, "输入条数要断言：空列表也能让下面那句变绿"
    assert colorspace.srgb_normal_map_violations(entries) == [], (
        "法线槽上有 sRGB —— 这一条会**点名**违规的那张"
    )
    # 正控：Soap_T 也是 cs=0（但不是法线槽）—— 证明这条规则不看名字
    assert live["Soap_T"] == 0 and "Soap_T" in [r[0] for r in TEXTURE_TABLE]

    # ---- 负控 1：把一张法线图标成 sRGB -> 必须点名它 ----
    for victim in NORMAL_MAP_NAMES:
        tampered = [
            (name, 1 if name == victim else cs, slot) for name, cs, slot, _m in TEXTURE_TABLE
        ]
        bad = colorspace.srgb_normal_map_violations(tampered)
        assert len(bad) == 1 and victim in bad[0], f"把 {victim} 标成 sRGB 却没被点名：{bad}"

    # ---- 负控 2：未知取值必须抛错，不许回落 ----
    for bogus in (2, -1, None, "1"):
        with pytest.raises(ValueError):
            colorspace.colorspace_of(bogus)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# C3 · 法线
# --------------------------------------------------------------------------- #
def test_c3_dxt5nm_normal_reconstruction() -> None:
    """DXT5nm（X 在 A、Y 在 G）的 x^2+y^2>1 占比 < 0.03；把 (R,G) 当 X,Y 则是 1.0000。"""
    # ---- 0. 先钉住通道切片本身（不然量错了通道也在"通过"） ----
    rgba = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    assert normals.channel_bytes(rgba, "R") == bytes([1, 5])
    assert normals.channel_bytes(rgba, "G") == bytes([2, 6])
    assert normals.channel_bytes(rgba, "A") == bytes([4, 8])
    assert (normals.DXT5NM_X_CHANNEL, normals.DXT5NM_Y_CHANNEL) == ("A", "G")

    textures = _units_textures()
    measured: dict[str, tuple[float, float]] = {}
    for name in NORMAL_MAP_NAMES:
        data = textures[name]
        assert int(data.m_ColorSpace) == 0, f"{name} 不是 cs=0，槽位写错了？"  # type: ignore[attr-defined]
        pixels = data.image.tobytes()  # type: ignore[attr-defined]
        good = normals.dxt5nm_report(pixels)
        bad = normals.rgb_report(pixels)
        measured[name] = (good.frac, bad.frac)
        assert good.total > 0
        assert good.frac < 0.03, f"{name}：DXT5nm(A,G) 占比 {good.frac:.4f} 没到 0.03 以下"
        # ---- 负控：把 (R,G) 当 X,Y ----
        assert bad.frac > 0.99, (
            f"{name}：(R,G) 口径必须几乎全违规（设计稿记的是 1.0000），实测 {bad.frac:.4f}"
        )
    assert len(measured) == 3

    # ---- 重建函数本身：平坦法线 (A=255 -> x=1, G=127 -> y≈0) 与越界不出 NaN ----
    x, y, z = normals.reconstruct_dxt5nm(255, 127)
    assert abs(x - 1.0) < 1e-12 and abs(y) < 1e-2
    assert 0.0 <= z <= 1.0
    assert normals.reconstruct_dxt5nm(0, 0)[2] == 0.0, "越界时必须给 0，不许 NaN"


# --------------------------------------------------------------------------- #
# C4 · 注册探针（真的起 Blender）
# --------------------------------------------------------------------------- #
def _blender_path() -> Path | None:
    override = os.environ.get("BA_BLENDER")
    if override:
        path = Path(override)
        return path if path.is_file() else None
    return DEFAULT_BLENDER if DEFAULT_BLENDER.is_file() else None


#: 2026-10-15「五格归并」之后侧栏就是这五格（唯一出处 = `blender_addon/__init__._CLASSES`）。
EXPECTED_PANELS: tuple[str, ...] = (
    "VIEW3D_PT_ba_take",
    "VIEW3D_PT_ba_edit",
    "VIEW3D_PT_ba_verify",
    "VIEW3D_PT_ba_install",
    "VIEW3D_PT_ba_ledger",
)
#: 每格至少要有的一颗（⛔ **不写全名单**：加算子不该让这条红）。
EXPECTED_OPS: tuple[str, ...] = (
    "ba_mod.take_unit",
    "ba_mod.take_rigcheck",
    "ba_mod.edit_xform",
    "ba_mod.verify_run",
    "ba_mod.install_apply",
)


def test_c4_registration_probe_under_blender() -> None:
    """算子 / 面板 / Scene 上的设置组在 `blender -b` 下都能看到（用 `dir()`，⛔ 不用 `hasattr`）。

    读数由 `blender_addon/selftest.py` 产出（JSON 夹在 `BAMOD_SELFTEST_BEGIN/END` 之间）。

    ⚠ **2026-10-16 重写**：旧版硬码着 `propertygroup_bl_rna == "BamodSettings"` 与一份
    `blender_addon/selftest.py` —— 那两样都在 **2026-10-15「插件重做」**里没了
    （PropertyGroup 现在是 `Scene.bamod_{blocks,take,edit,out,verify}` 五挂），
    于是这条一直红着、且**红得没有意义**（它量的是一个不存在的形态）。
    现在量的是**当前形态**，并多了一条最要紧的正控：`self_measured`。
    """
    blender = _blender_path()
    if blender is None:
        pytest.skip("找不到 blender.exe（设 BA_BLENDER 指过去）")
    proc = subprocess.run(
        [
            str(blender),
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(SELFTEST),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, f"Blender 退出码 {proc.returncode}\n{proc.stdout[-3000:]}"
    match = re.search(r"^BAMOD_SELFTEST_BEGIN$(.*?)^BAMOD_SELFTEST_END$", proc.stdout, re.M | re.S)
    assert match, f"探针没打印 JSON 块\n{proc.stdout[-3000:]}"
    report = json.loads(match.group(1))

    # ---- ★ 正控 0：量的**就是自己那一份**（⛔ 这条最容易被假绿骗过去）----
    assert report["self_measured"] is True, (
        f"量到的不是隔壁那一份插件：{report['module_file']} —— "
        "%APPDATA%\\…\\scripts\\addons\\ 底下可能还装着另一份（2026-10-16 实测踩过："
        "第一趟量的是那份旧的，却一路报绿）"
    )
    assert report["register"] == "ok", report.get("fatal", report)
    assert report["unregister"] == "ok"

    # ---- 正控 1：五格面板一个不缺（顺序的唯一出处 = __init__._CLASSES）----
    assert report["panels"] == list(EXPECTED_PANELS), report["panels"]
    assert report["panels_missing"] == [], f"面板没注册上：{report['panels_missing']}"

    # ---- 正控 2：算子 ＋ Scene 上那五挂设置 ----
    assert report["operators"] >= 55, f"算子只剩 {report['operators']} 个"
    assert report["operators_missing"] == [], f"算子没注册上：{report['operators_missing']}"
    for bl_idname in EXPECTED_OPS:
        assert bl_idname in report["operator_names"], f"{bl_idname} 没注册上"
    assert report["propertygroup_dir"] is True
    for name in ("bamod_blocks", "bamod_take", "bamod_edit", "bamod_out", "bamod_verify"):
        assert name in report["scene_props"], f"Scene.{name} 没挂上：{report['scene_props']}"

    # ---- 正控 3：共用内核导得进来（设计稿 §4 D2）----
    assert report["kernel_import_ok"] is True, (
        "共用内核在 Blender 的解释器里导不进来 —— 设计稿 §4 D2 的"
        f"「内核共用」当场不成立：{report['kernel_where']}"
    )

    # ---- 负控 1：dir() 对没注册的名字说 False（尺子能说不） ----
    assert report["absent_op_dir"] is False
    assert report["absent_type_dir"] is False
    # ---- 负控 2：同一件事 hasattr 说 True —— 这就是它被禁的原因 ----
    assert report["hasattr_false_positive"] is True, (
        "本机 Blender 上 hasattr(bpy.ops.X, 假名字) 不再恒真 —— "
        "工单 §7 坑 3 的结论需要重新实测，别照抄"
    )
    assert report["types_hasattr_python_classname"] is False, (
        "Operator 类名不该出现在 bpy.types 里；这条反控证明「拿类名去查」会假阴性"
    )

    # ---- 启动自检（设计稿 §4 D2 义务 3）：来源必须打印出来 ----
    lines = report["runtime_lines"]
    assert any(line.startswith("运行时来源：") for line in lines)
    assert any(line.startswith("解释器：cp") for line in lines)


# --------------------------------------------------------------------------- #
# C5 · 分层守
# --------------------------------------------------------------------------- #
_FORBIDDEN_IMPORT_ROOTS = frozenset({"bpy", "PySide6"})
_FORBIDDEN_TOKENS = ("bpy", "PySide6")


def _import_violations(root: Path) -> list[str]:
    """AST 扫 import / from-import 的根模块名。"""
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                    out.append(f"{path.name}:{node.lineno} -> {name}")
    return out


def _token_violations(root: Path) -> list[str]:
    """文本扫同两个名字（连 import 语句之外的提及都不放过：字符串 import、注释、文档）。"""
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for token in _FORBIDDEN_TOKENS:
                if token in line:
                    out.append(f"{path.name}:{lineno} -> {token}")
    return out


def test_c5_asset_kernel_carries_no_host_bindings(tmp_path: Path) -> None:
    """asset_kernel/ 里 bpy 与 PySide6 **0 命中**（导入扫描 + 文本扫描两道）。"""
    modules = sorted(KERNEL_DIR.rglob("*.py"))
    assert len(modules) >= 5, f"资产内核只有 {len(modules)} 个文件 —— 判据会空跑"
    assert _import_violations(KERNEL_DIR) == [], "asset_kernel 里出现了宿主绑定"
    assert _token_violations(KERNEL_DIR) == [], "asset_kernel 里出现了 bpy / PySide6 字样"

    # ---- 负控 1：种一行 import bpy -> 两条尺子当场红 ----
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "bad_import.py").write_text("import bpy\n", encoding="utf-8")
    assert any("bad_import" in v for v in _import_violations(planted))
    assert any("bad_import" in v for v in _token_violations(planted))

    # ---- 负控 2：只有字符串没有 import -> AST 看不见，文本扫描必须抓到 ----
    planted_only_string = tmp_path / "planted_string"
    planted_only_string.mkdir()
    (planted_only_string / "bad_string.py").write_text(
        'MODULE = "PySide6.QtWidgets"\n', encoding="utf-8"
    )
    assert _import_violations(planted_only_string) == [], "字符串不该被 AST 当成 import"
    assert any("bad_string" in v for v in _token_violations(planted_only_string))
