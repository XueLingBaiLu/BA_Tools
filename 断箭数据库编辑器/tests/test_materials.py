"""W4 判据：材质还原（M1~M6 六张映射表、统一节点树、未还原清单）。

**判据一行**：`pytest tests/test_materials.py -q` -> 通过数 **从 0 变 N**（本文件新建前 = 0 收集）。

## E1~E5 的落点（每条都带正控/负控 —— 设计稿 §11 四条纪律之①）

- **E1 M1 全槽**：`test_e1_*`（纯 plan 一条 + 真 Blender 建树一条）。
  负控 = 少赋一个槽 => 断言"那条连线不存在"必须成立。
- **E2 MaskMap 拆分**：`test_e2_*`（参考 PNG 的均值 + 真树的 R/A/G 连线）。
  负控 = 拿基色图当 MaskMap => 均值差 20 以上（尺子能红）。
- **E3 DXT5nm**：`test_e3_*`（3 张真法线图的 (A,G) 占比 + 烘出来的图按 (R,G)）。
  负控 = 同一张图按 (R,G) 量 => 几乎 1.0。
- **E4 未还原清单**：`test_e4_*`（纯 plan_unit + 探针报告）。
  负控 = 去掉 HDRP/Lit 那一件 => 只剩 4 条。
- **E5 正控**：`test_e5_wrong_table_makes_e2_red`。
  正确表 => 失败清单为空；装错表（_MaskMap 当 albedo）=> 必须非空。

## 重活只跑一次

真 Blender + 3.38 GB units 包的读数由 `blender_addon/materials_probe.py` 产出，落盘
`_work/w4_materials_report.json`；pytest 侧只读 JSON（与 tests/test_import_unit.py 同一把
重活锁 `_work/w3_heavy.lock`，同一时刻只允许一个重活进程）。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from ba_mod_maker.asset_kernel import materials, normals

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT.parent
REFS_DIR = WORK / "blender-design" / "assets" / "textures"
PROBE = REPO_ROOT / "blender_addon" / "materials_probe.py"
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")
DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")

REPORT_JSON = WORK / "w4_materials_report.json"
HEAVY_LOCK = WORK / "w3_heavy.lock"

UNIT_KEY = "RU_T72B"

#: E2 的锚点：**实测**（channel_stats.json / 设计稿 §3.5）—— 参考 PNG 逐通道均值。
MASKMAP_ANCHORS: dict[str, tuple[str, float, float]] = {
    "Pilot_helo_low_US_Pilot_Helicopter_MaskMap": (
        "AH64D__Pilot_helo_low_US_Pilot_Helicopter_MaskMap.png",
        4.40,
        101.42,
    ),
    "low_to_tex_OCP_MaskMap": (
        "USArmyRifle__low_to_tex_OCP_MaskMap.png",
        3.06,
        94.36,
    ),
}

#: E3 的 3 张真法线图（设计稿 §5.3 的 (G,A) 列读数：0.0200 / 0.0055 / 0.0014）。
NORMAL_ANCHORS: dict[str, tuple[str, float]] = {
    "RU_Track7290_Standard_22_Normal": (
        "T72B__RU_Track7290_Standard_22_Normal.png",
        0.02,
    ),
    "Pilot_helo_low_US_Pilot_Helicopter_Normal": (
        "AH64D__Pilot_helo_low_US_Pilot_Helicopter_Normal.png",
        0.0055,
    ),
    "low_to_tex_OCP_Normal": (
        "USArmyRifle__low_to_tex_OCP_Normal.png",
        0.0014,
    ),
}

#: 均值口径的容差（参考 PNG 的均值是按 4 位小数记的）。
MEAN_TOL = 0.05
#: E3 的通过线（设计稿 §11 A3）。
UNIT_FRAC_MAX = 0.03
#: 负控下限：把 (R,G) 当 X/Y 时几乎全错。
UNIT_FRAC_MIN_NEGATIVE = 0.9

_RGB_CHANNEL_INDEX = {"R": 0, "G": 1, "B": 2, "A": 3}


# --------------------------------------------------------------------------- #
# 参考件 / 合成件
# --------------------------------------------------------------------------- #
def _reference_rgba(file_name: str) -> bytes:
    with Image.open(REFS_DIR / file_name) as handle:
        return handle.convert("RGBA").tobytes()


def _channel_mean(rgba: bytes, channel: str) -> float:
    values = rgba[_RGB_CHANNEL_INDEX[channel] :: 4]
    return sum(values) / len(values)


def _synthetic_full_slot(**overrides: object) -> dict[str, object]:
    """M1 的全槽合成件（E1 的"逐条断言"就架在它上面）。

    槽名 / 参数名全部来自**真实材质实测**（设计稿 §3.3 的表 + summary_units.json）：
    _BaseColorMap 与 _MainTex 同指一张（T-72B 履带实测如此）。
    """
    material: dict[str, object] = {
        "name": "SYNTHETIC_FULL_SLOT",
        "shader": "HDRP/Lit",
        "texture_slots": (
            ("_BaseColorMap", 11),
            ("_MainTex", 11),
            ("_NormalMap", 12),
            ("_MaskMap", 13),
            ("_EmissiveColorMap", 14),
            # ★ 2026-10-17：虹彩厚度层（68 颗 HDRP/Lit 绑了它）
            # —— E1 现在覆盖 NODE_SHAPE 的 6 条。
            ("_IridescenceThicknessMap", 15),
        ),
        "floats": {
            "_Metallic": 0.25,
            "_Smoothness": 0.75,
            "_NormalScale": 2.0,
            "_AlphaCutoff": 0.5,
            "_EmissiveIntensity": 3.0,
            "_SurfaceType": 0.0,
            "_Ior": 1.5,
            "_IridescenceThickness": 1.0,
            "_IridescenceMask": 1.0,
        },
        "colors": {
            "_BaseColor": (1.0, 1.0, 1.0, 1.0),
            "_IridescenceThicknessRemap": (0.0, 1.0, 0.0, 0.0),
        },
        "valid_keywords": ("_NORMALMAP", "_MATERIAL_FEATURE_IRIDESCENCE"),
        "invalid_keywords": (),
    }
    material.update(overrides)
    return material


def _synthetic_unitv3() -> dict[str, object]:
    """M2 的合成件：**真实读数**照抄（RU_T72B 1 的槽与浮点，见 summary_units.json）。"""
    return {
        "name": "RU_T72B 1",
        "shader": "Shader Graphs/UnitV3",
        "texture_slots": (
            ("Layer_A97CDC25", 21),
            ("_Emission", None),
            ("_VirtualDetailNormal", None),
        ),
        "floats": {
            "_Metallic": 0.0,
            "_Smoothness": 1.0,
            "_NormalScale": 1.0,
            "_AlphaCutoff": 0.5,
            "_POTATO_MOD": 0.0,
            "_DIRT": 1.0,
            "_DirtProgress": 0.0,
            "_AOStrange": 3.0,
            "_NormalStrange": 1.0,
            "_EmissiveIntensity": 1.0,
            "_EMISSONENABLE": 0.0,
        },
        "valid_keywords": ("_DIRT", "_DISABLE_SSR_TRANSPARENT"),
        "invalid_keywords": ("_EMASKMAP", "_NORMALMAP"),
    }


def _synthetic_track() -> dict[str, object]:
    """M1 的合成件：T-72B 履带（_ALPHATEST_ON / _NormalScale 4.0 / _AlphaCutoff 0.713）。"""
    return {
        "name": "RU_T72B_track",
        "shader": "HDRP/Lit",
        "texture_slots": (
            ("_BaseColorMap", 31),
            ("_MainTex", 31),
            ("_NormalMap", 32),
            ("_MaskMap", None),
            ("_EmissiveColorMap", None),
        ),
        "floats": {
            "_Metallic": 1.0,
            "_Smoothness": 0.367000013589859,
            "_NormalScale": 4.0,
            "_AlphaCutoff": 0.7129999995231628,
            "_SurfaceType": 0.0,
        },
        "valid_keywords": ("_ALPHATEST_ON", "_NORMALMAP", "_NORMALMAP_TANGENT_SPACE"),
        "invalid_keywords": (),
    }


# --------------------------------------------------------------------------- #
# E2 的机械断言（E5 的负控复用它 —— 同一条尺子）
# --------------------------------------------------------------------------- #
def _maskmap_split_failures(
    plan: materials.MaterialPlan,
    stats: dict[int, dict[str, float]],
    *,
    expected: tuple[float, float] | None = None,
) -> list[str]:
    """E2：MaskMap 拆分（R -> Metallic、A -> Invert -> Roughness、G -> AO 标注忽略）。

    :param stats: texture_id -> {"R_mean": float, "A_mean": float}
    :param expected: (R_mean, A_mean) 的锚点；给了就一起比。
    :return: 失败清单（**空 = 通过**）。E5 就断言"装错表之后这个清单非空"。
    """
    problems: list[str] = []
    links = {(link.role, link.blender_input): link for link in plan.links}
    wanted = (
        ("mask", "Metallic", "R", False, False),
        ("mask", "Roughness", "A", True, False),
        ("mask", "AO", "G", False, True),
    )
    for role, blender_input, channel, invert, ignored in wanted:
        link = links.get((role, blender_input))
        if link is None:
            problems.append(f"缺连线：mask -> {blender_input}")
            continue
        if link.channel != channel:
            problems.append(f"mask -> {blender_input} 的通道是 {link.channel!r}，应为 {channel!r}")
        if link.invert is not invert:
            problems.append(f"mask -> {blender_input} 的 invert={link.invert}，应为 {invert}")
        if link.ignored is not ignored:
            problems.append(f"mask -> {blender_input} 的 ignored={link.ignored}，应为 {ignored}")

    binding = plan.slot_of(materials.ROLE_MASK)
    if binding is None or binding.texture_id is None:
        problems.append("计划里没有已赋图的 mask 槽（R/A/G 的来源缺了）")
        return problems
    row = stats.get(int(binding.texture_id))
    if row is None:
        problems.append(f"没有 texture_id={binding.texture_id} 的通道读数（尺子量不到）")
        return problems
    if expected is not None:
        want_r, want_a = expected
        if abs(row["R_mean"] - want_r) > MEAN_TOL:
            problems.append(f"MaskMap R mean = {row['R_mean']:.4f}，锚点 {want_r}")
        if abs(row["A_mean"] - want_a) > MEAN_TOL:
            problems.append(f"MaskMap A mean = {row['A_mean']:.4f}，锚点 {want_a}")
    return problems


def _sabotaged_m1() -> materials.MaterialTable:
    """**装错映射表**：把 _MaskMap 当成 albedo 槽、MaskMap 角色整个丢掉（E5 的负控）。"""
    return replace(
        materials.TABLES["HDRP/Lit"],
        base_color_slots=("_MaskMap",),
        mask_slots=(),
        normal_slots=("_MaskMap",),
    )


# --------------------------------------------------------------------------- #
# 纯判据（不碰游戏文件、不碰 Blender）
# --------------------------------------------------------------------------- #
def test_six_mapping_tables_cover_the_design_shader_distribution() -> None:
    """六张表的 shader 名必须与实测分布逐个对上（M4 未支持；M5/M6 是 2026-10-17 补的）。

    M5 `HDRP/Unlit` / M6 `HDRP/Decal` 的槽位与标量读数写在 kernel 两张表上方的注释里；
    这条用例只守「表在、key 对、没错标 unsupported / gates / runtime_items」，
    逐条槽位断言在 test_m5_* / test_m6_* 里。
    """
    expected = {
        "HDRP/Lit": "M1",
        "Shader Graphs/UnitV3": "M2",
        "Shader Graphs/Infantry Shader": "M3",
        "Shader Graphs/UnitV4_Camo": "M4",
        "HDRP/Unlit": "M5",
        "HDRP/Decal": "M6",
    }
    assert {shader: table.key for shader, table in materials.TABLES.items()} == expected
    assert materials.TABLES["Shader Graphs/UnitV4_Camo"].unsupported is True
    assert materials.TABLES["Shader Graphs/UnitV4_Camo"].note
    for key in ("M5", "M6"):
        table = next(t for t in materials.TABLES.values() if t.key == key)
        assert table.unsupported is False, f"{key} 实测有图、能接线 ⇒ 不许标 unsupported"
        assert table.gates == {}, f"{key} 的读数里 0 不一致 ⇒ 不许加门"
        assert table.runtime_items == (), f"{key} 没量到运行期项 ⇒ 不许加"
    # 认不出来 = None（不猜）—— 换一个**表里确实没有**的 shader 当负控
    assert materials.table_for("Hidden/VFX/NotInTables") is None
    assert materials.table_for("") is None


def test_e1_hdrp_lit_slots_all_wire_to_the_unified_node_shape() -> None:
    """E1：HDRP/Lit 全槽 -> 统一节点树（§7.2 的六条连线）。"""
    plan = materials.plan_material(_synthetic_full_slot())
    assert plan.table_key == "M1"
    assert plan.coverage == materials.COVERAGE_FULL
    assert plan.alpha_mode == "OPAQUE"

    roles = {binding.slot: binding.role for binding in plan.slots}
    assert roles["_BaseColorMap"] == materials.ROLE_BASE_COLOR
    assert roles["_MainTex"] == materials.ROLE_BASE_COLOR
    assert roles["_NormalMap"] == materials.ROLE_NORMAL
    assert roles["_MaskMap"] == materials.ROLE_MASK
    assert roles["_EmissiveColorMap"] == materials.ROLE_EMISSION

    assert plan.links == materials.NODE_SHAPE
    assert plan.scalar("_NormalScale") == 2.0
    assert plan.scalar("_Metallic") == 0.25
    assert plan.scalar("_Smoothness") == 0.75
    assert plan.scalar("_EmissiveIntensity") == 3.0

    # 负控：少赋一个槽 => 那条连线必须消失（断言"缺"能成立，才算尺子）
    partial = _synthetic_full_slot(
        texture_slots=(
            ("_BaseColorMap", 11),
            ("_MainTex", 11),
            ("_NormalMap", 12),
            ("_MaskMap", 13),
        )
    )
    partial_plan = materials.plan_material(partial)
    emission_link = materials.ChannelLink(materials.ROLE_EMISSION, "Emission Color")
    assert emission_link not in partial_plan.links


def test_e1_alpha_clip_comes_from_the_keyword_and_the_cutoff() -> None:
    """E1 的标量分支：_ALPHATEST_ON => Alpha Clip；_Smoothness 要按 1-S 接到 Roughness。"""
    plan = materials.plan_material(_synthetic_track())
    assert plan.alpha_mode == "CLIP"
    assert plan.scalar("_AlphaCutoff") == pytest.approx(0.713, abs=1e-6)
    assert plan.scalar("_NormalScale") == 4.0
    binding = materials.TABLES["HDRP/Lit"].find_scalar("_Smoothness")
    assert binding is not None and binding.invert is True
    assert binding.apply(0.367) == pytest.approx(0.633, abs=1e-6)


def test_e1_maintex_and_basecolormap_pointing_apart_is_recorded() -> None:
    """_BaseColorMap 与 _MainTex 指向不同贴图时**必须留痕**（不许静默挑一个）。"""
    material = _synthetic_full_slot(
        texture_slots=(("_BaseColorMap", 11), ("_MainTex", 99)),
    )
    plan = materials.plan_material(material)
    assert any("不同贴图" in note for note in plan.notes), plan.notes
    assert any("_BaseColorMap" in note for note in plan.notes)


def test_e4_unit_runtime_items_include_the_five_named_by_the_work_order() -> None:
    """E4：UnitV3 的 5 条运行时项一条不许缺（设计稿 §4 D3 + 工单 E4）。"""
    unit = materials.plan_unit(
        UNIT_KEY,
        (_synthetic_unitv3(), _synthetic_track()),
    )
    keys = tuple(item.key for item in unit.runtime_items)
    for want in materials.RUNTIME_KEYS_UNITV3:
        assert want in keys, f"未还原清单缺 {want}（现有 {keys}）"
    assert unit.runtime_count == 5

    # 负控：去掉 HDRP/Lit 那一件 => 履带滚动那一条必须消失
    without_track = materials.plan_unit(UNIT_KEY, (_synthetic_unitv3(),))
    assert "track_uv_scroll" not in tuple(item.key for item in without_track.runtime_items)
    assert without_track.runtime_count == 4


def test_e2_reference_maskmaps_split_r_metallic_a_roughness() -> None:
    """E2：MaskMap 的 R/A 实测锚点（参考 PNG 就是 bundle 里解出来的那张图）。"""
    if not REFS_DIR.is_dir():
        pytest.skip(f"参考件目录不在：{REFS_DIR}")
    for texture_name, (file_name, want_r, want_a) in MASKMAP_ANCHORS.items():
        path = REFS_DIR / file_name
        if not path.is_file():
            pytest.skip(f"缺参考件：{path}")
        rgba = _reference_rgba(file_name)
        got_r = _channel_mean(rgba, "R")
        got_a = _channel_mean(rgba, "A")
        assert got_r == pytest.approx(want_r, abs=MEAN_TOL), texture_name
        assert got_a == pytest.approx(want_a, abs=MEAN_TOL), texture_name
        # 尺子正控：同一把尺子量另一张图（基色图）必须给出**完全不同**的数
        other = _reference_rgba("T72B__RU_T72B_1_BaseMap.png")
        assert abs(_channel_mean(other, "A") - got_a) > 20.0

    # 用它喂 E2 的断言函数：正确的表 + 正确的读数 => 空
    stats = {
        13: {
            "R_mean": _channel_mean(
                _reference_rgba(MASKMAP_ANCHORS["Pilot_helo_low_US_Pilot_Helicopter_MaskMap"][0]),
                "R",
            ),
            "A_mean": _channel_mean(
                _reference_rgba(MASKMAP_ANCHORS["Pilot_helo_low_US_Pilot_Helicopter_MaskMap"][0]),
                "A",
            ),
        }
    }
    plan = materials.plan_material(_synthetic_full_slot())
    assert _maskmap_split_failures(plan, stats, expected=(4.40, 101.42)) == []


def test_e5_wrong_table_makes_e2_red() -> None:
    """E5 正控：**装错映射表（把 MaskMap 当 albedo）⇒ E2 必须红**。"""
    if not REFS_DIR.is_dir():
        pytest.skip(f"参考件目录不在：{REFS_DIR}")
    file_name, want_r, want_a = MASKMAP_ANCHORS["Pilot_helo_low_US_Pilot_Helicopter_MaskMap"]
    if not (REFS_DIR / file_name).is_file():
        pytest.skip(f"缺参考件：{file_name}")
    rgba = _reference_rgba(file_name)
    stats = {13: {"R_mean": _channel_mean(rgba, "R"), "A_mean": _channel_mean(rgba, "A")}}

    material = _synthetic_full_slot()
    good = materials.plan_material(material)
    assert _maskmap_split_failures(good, stats, expected=(want_r, want_a)) == []

    bad = materials.plan_material(material, table=_sabotaged_m1())
    failures = _maskmap_split_failures(bad, stats, expected=(want_r, want_a))
    assert failures, "装错映射表之后 E2 居然还是绿的 —— 这条尺子不成立"
    assert any("mask" in line for line in failures)


def test_e3_three_real_normal_maps_are_unit_vectors() -> None:
    """E3：3 张真法线图按 DXT5nm (A,G) 的 x^2+y^2>1 占比 < 0.03；负控 (R,G) 几乎 1.0。"""
    if not REFS_DIR.is_dir():
        pytest.skip(f"参考件目录不在：{REFS_DIR}")
    for texture_name, (file_name, want) in NORMAL_ANCHORS.items():
        path = REFS_DIR / file_name
        if not path.is_file():
            pytest.skip(f"缺参考件：{path}")
        rgba = _reference_rgba(file_name)
        ag = normals.dxt5nm_report(rgba)
        rg = normals.rgb_report(rgba)
        assert ag.frac < UNIT_FRAC_MAX, f"{texture_name}: (A,G) frac={ag.frac}"
        assert ag.frac == pytest.approx(want, abs=0.005), texture_name
        assert rg.frac > UNIT_FRAC_MIN_NEGATIVE, f"{texture_name}: 负控 (R,G) frac={rg.frac}"


def test_e3_baked_normal_is_the_kernel_formula() -> None:
    """E3 的"重建"那一步：烘出来的 RGB 法线必须逐像素等于 reconstruct_dxt5nm 的结果。"""
    if not REFS_DIR.is_dir():
        pytest.skip(f"参考件目录不在：{REFS_DIR}")
    file_name = NORMAL_ANCHORS["RU_Track7290_Standard_22_Normal"][0]
    if not (REFS_DIR / file_name).is_file():
        pytest.skip(f"缺参考件：{file_name}")
    raw = _reference_rgba(file_name)
    baked = normals.dxt5nm_to_rgb_normal(raw)
    assert len(baked) == len(raw)

    lut = normals.dxt5nm_z_lut()
    checked = 0
    for index in range(0, len(raw), 4 * 97):  # 抽样（97 是质数，走遍各个 x/y 组合）
        x = raw[index + 3]
        y = raw[index + 1]
        z = lut[(x << 8) | y]
        assert baked[index] == x
        assert baked[index + 1] == y
        assert baked[index + 2] == int(round((z + 1.0) * 0.5 * 255.0))
        assert baked[index + 3] == 255
        checked += 1
    assert checked > 1000
    # 烘完以后按 (R,G) 量应该**与原来按 (A,G) 量完全一样**（同一份 z 公式）
    assert normals.rgb_report(baked).frac == pytest.approx(normals.dxt5nm_report(raw).frac)
    # 负控：对烘出来的图按 (A,G) 量（A 恒 255）=> 几乎全错
    assert normals.dxt5nm_report(baked).frac > UNIT_FRAC_MIN_NEGATIVE


# --------------------------------------------------------------------------- #
# 重活：真 Blender + units 包（只跑一次，落盘缓存）
# --------------------------------------------------------------------------- #
class _HeavyLock:
    """跨进程互斥（文件锁）：与 tests/test_import_unit.py 共用一把 —— 重活一个一个来。

    锁**陈旧 30 分钟自动失效**：上一次被强制停止时留下的锁不该卡死这一轮。
    """

    def __init__(self, path: Path, timeout: float = 2400.0) -> None:
        self.path = path
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> _HeavyLock:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()}\n".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if age > 1800:
                    self.path.unlink(missing_ok=True)
                    continue
                time.sleep(1.0)
        raise TimeoutError(f"等重活锁超时：{self.path}")

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.path.unlink(missing_ok=True)


def _fingerprint() -> str:
    paths = [Path(__file__).resolve(), PROBE, REPO_ROOT / "blender_addon" / "importer.py"]
    paths += sorted((REPO_ROOT / "blender_addon").glob("*.py"))
    paths += sorted((REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel").glob("*.py"))
    parts = [f"{p.name}:{p.stat().st_mtime_ns}" for p in paths if p.is_file()]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_report(fingerprint: str) -> dict | None:
    if not REPORT_JSON.is_file():
        return None
    try:
        data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if data.get("_fingerprint") == fingerprint else None


def _blender_path() -> Path | None:
    override = os.environ.get("BA_BLENDER")
    if override and Path(override).is_file():
        return Path(override)
    return DEFAULT_BLENDER if DEFAULT_BLENDER.is_file() else None


def _game_root() -> Path:
    override = os.environ.get("BA_MOD_MAKER_GAME")
    return Path(override) if override else DEFAULT_GAME


def _run_probe() -> dict:
    blender = _blender_path()
    assert blender is not None, "找不到 blender.exe（设 BA_BLENDER 指过去）"
    proc = subprocess.run(
        [
            str(blender),
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(PROBE),
            "--",
            "--game-root",
            str(_game_root()),
            "--unit",
            UNIT_KEY,
            "--refs",
            str(REFS_DIR),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, (
        f"Blender 退出码 {proc.returncode}\n{(proc.stdout or '')[-4000:]}"
        f"\n{(proc.stderr or '')[-2000:]}"
    )
    match = re.search(
        r"^BAMOD_MATERIALS_BEGIN\s*$(.*?)^BAMOD_MATERIALS_END\s*$", proc.stdout, re.M | re.S
    )
    assert match, f"探针没打印 JSON 块\n{(proc.stdout or '')[-4000:]}"
    report, _end = json.JSONDecoder().raw_decode(match.group(1).lstrip())
    assert report.get("import_ok"), f"导入失败：{report.get('import_error')}"
    return report


@functools.lru_cache(maxsize=1)
def _probe_report() -> dict:
    """W4 的现场读数：blender 探针产出 + 落盘缓存（跨 worker 共享）。"""
    # ★ 退役（2026-10-15 用户定，2026-10-17 按原文补回）：探针脚本没了就**如实 skip 并点名**。
    #   不是「糊绿」：没有数据源就是没有数据。靠探针取数的 7 条真 Blender 读数用例全走这条 skip
    #   （real_node_trees / maskmap_tree / baked_normals / probe_decoded_bytes /
    #   e4_report_unreduced / probe_import_still_sees_three）；探针一旦回来，这个 skip 自动消失。
    if not PROBE.is_file():
        pytest.skip(f"探针不在（题目没了）：{PROBE}")
    fingerprint = _fingerprint()
    cached = _read_report(fingerprint)
    if cached is not None:
        return cached
    with _HeavyLock(HEAVY_LOCK):
        cached = _read_report(fingerprint)
        if cached is not None:
            return cached
        report = _run_probe()
        report["_fingerprint"] = fingerprint
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return report


def _tree_of(report: dict, material_name: str) -> dict:
    for tree in report.get("built_trees") or ():
        if tree.get("material") == material_name:
            return tree
    names = [t.get("material") for t in (report.get("built_trees") or ())]
    raise AssertionError(f"探针里没有 {material_name!r} 的节点树（有 {names}）")


def _principled(tree: dict) -> str:
    for node in tree["nodes"]:
        if node["type"] == "ShaderNodeBsdfPrincipled":
            return str(node["name"])
    raise AssertionError(f"树里没有 Principled：{[n['type'] for n in tree['nodes']]}")


def _upstream(tree: dict, to_name: str, to_socket: str) -> dict | None:
    for link in tree["links"]:
        if link[3] == to_name and link[5] == to_socket:
            return {"name": link[0], "type": link[1], "socket": link[2]}
    return None


def _image_of(tree: dict, node_name: str) -> str | None:
    for node in tree["nodes"]:
        if node["name"] == node_name:
            return node.get("image")
    return None


def _chain_images(tree: dict, to_name: str, to_socket: str, depth: int = 6) -> list[str | None]:
    """从某个输入往回走，把沿途每跳源节点的图片名收集起来（最后一跳在最前）。"""
    out: list[str | None] = []
    cursor = (to_name, to_socket)
    for _ in range(depth):
        link = _upstream(tree, *cursor)
        if link is None:
            break
        out.append(_image_of(tree, link["name"]))
        cursor = (link["name"], link["socket"])
    return out


def test_probe_is_available_or_skip() -> None:
    if _blender_path() is None:
        pytest.skip("找不到 blender.exe")
    if not PROBE.is_file():
        pytest.skip(f"探针不在：{PROBE}")
    if not _game_root().is_dir():
        pytest.skip(f"游戏根目录不在：{_game_root()}")
    assert REFS_DIR.is_dir()


def test_probe_import_still_sees_three_materials_and_three_textures() -> None:
    """W3 的 13/3/3 不许回归（材质这一单改了材质创建路径）。"""
    report = _probe_report()
    assert report["counts"]["renderers"] == 13
    assert report["counts"]["materials"] == 3
    assert report["counts"]["textures"] == 3


def test_e1_real_node_trees_wire_the_slots_the_plan_declares() -> None:
    """E1（真 Blender）：合成全槽件的节点树逐条对上 §7.2。"""
    report = _probe_report()
    tree = report["synthetic_full_slot"]
    assert tree is not None, report.get("errors")
    principled = _principled(tree)

    # Base Color 来自 _BaseColorMap 那张图（不是 _MainTex 之外的第三张）
    base_image = _chain_images(tree, principled, "Base Color")[0]
    assert base_image is not None and base_image in set(tree["images_by_role"].values())
    assert tree["images_by_role"].get("base_color") == base_image

    # Normal：Normal Map 节点在链上，Strength == _NormalScale
    normal_chain = _chain_images(tree, principled, "Normal")
    normal_node = _upstream(tree, principled, "Normal")
    assert normal_node is not None and normal_node["type"] == "ShaderNodeNormalMap", normal_chain
    strength = None
    for node in tree["nodes"]:
        if node["name"] == normal_node["name"]:
            strength = (node.get("defaults") or {}).get("Strength")
    assert strength == pytest.approx(2.0)
    assert tree["normal_baked"] is not None and tree["normal_source"] is not None
    assert tree["normal_baked"] != tree["normal_source"]

    # Mask：R -> Metallic（经 Separate Color）；A -> Roughness（中间至少再过一个节点 = Invert）
    metallic_source = _upstream(tree, principled, "Metallic")
    assert metallic_source is not None and metallic_source["type"] == "ShaderNodeSeparateColor"
    # Separate Color 自己不持图：它上面那一跳才是 mask 贴图节点
    mask_tex = _upstream(tree, metallic_source["name"], "Color")
    assert mask_tex is not None and mask_tex["type"] == "ShaderNodeTexImage"
    assert tree["images_by_role"].get("mask") == _image_of(tree, mask_tex["name"])
    roughness_source = _upstream(tree, principled, "Roughness")
    assert roughness_source is not None
    assert roughness_source["type"] != "ShaderNodeSeparateColor", "A 必须经一个 Invert，不许直连"
    assert _chain_images(tree, principled, "Roughness")[-1] == tree["images_by_role"].get("mask")

    # Emission：图 + Strength
    assert tree["images_by_role"].get("emission") is not None
    assert _upstream(tree, principled, "Emission Color") is not None

    # G（AO）**不接任何东西**：标注忽略，不许偷偷接上
    separate = metallic_source["name"]
    for link in tree["links"]:
        if link[0] == separate and link[2] in ("Green", "G"):
            raise AssertionError(f"AO 的 G 通道被接走了：{link}")


def test_e2_real_maskmap_tree_splits_r_a_g() -> None:
    """E2（真 Blender + 真贴图）：_MaskMap -> Separate Color -> R/A/G，且均值对得上锚点。"""
    report = _probe_report()
    textures = report["textures"]
    name = "Pilot_helo_low_US_Pilot_Helicopter_MaskMap"
    assert name in textures, sorted(textures)
    row = textures[name]
    assert row["role"] == "mask"
    assert row["slot"] == "_MaskMap"
    assert row["channels"]["R"]["mean"] == pytest.approx(4.40, abs=MEAN_TOL)
    assert row["channels"]["A"]["mean"] == pytest.approx(101.42, abs=MEAN_TOL)

    # 这张图确实被某个 HDRP/Lit 材质的 mask 角色接上了
    wired = [
        tree
        for tree in report["built_trees"]
        if tree["table"] == "M1" and tree["images_by_role"].get("mask") is not None
    ]
    assert wired, "没有任何 M1 材质把 _MaskMap 接到 mask 角色"
    stats = {int(row["path_id"]): {"R_mean": row["channels"]["R"]["mean"],
                                   "A_mean": row["channels"]["A"]["mean"]}}
    plans = {tree["material"]: materials.plan_material(_raw_by_name(report, tree["material"]))
             for tree in wired}
    for tree in wired:
        plan = plans[tree["material"]]
        binding = plan.slot_of(materials.ROLE_MASK)
        if binding is None or binding.texture_id is None:
            continue
        if int(binding.texture_id) != int(row["path_id"]):
            continue
        assert _maskmap_split_failures(plan, stats, expected=(4.40, 101.42)) == []


def _raw_by_name(report: dict, name: str) -> dict:
    for raw in report["raw_materials"]:
        if str(raw.get("name")) == name:
            return raw
    raise AssertionError(f"探针的 raw_materials 里没有 {name!r}")


def test_e3_baked_normals_are_unit_vectors_and_match_the_kernel() -> None:
    """E3（真 Blender）：烘出来的 RGB 法线按 (R,G) 必须 < 0.03，且与原图 (A,G) 同值。"""
    report = _probe_report()
    trees = [
        tree
        for tree in report["built_trees"]
        if tree.get("normal_baked_images")
    ]
    assert trees, "没有任何树带烘出来的法线图"
    for tree in trees:
        for path_id, stats in (tree.get("normal_baked_stats") or {}).items():
            assert "error" not in stats, stats
            assert stats["colorspace"] == "Non-Color", stats
            assert stats["unit_frac_rg"] < UNIT_FRAC_MAX, (tree["material"], path_id, stats)
            assert stats["unit_frac_ag"] > UNIT_FRAC_MIN_NEGATIVE, (
                "负控不成立：拿 (A,G) 去量烘完的图（A 恒 255）居然还是绿的"
            )
            raw = report["textures"][tree["texture_names"][path_id]]
            assert stats["unit_frac_rg"] == pytest.approx(raw["unit_frac_ag"], abs=1e-9)


def test_e3_probe_decoded_bytes_match_the_reference_pngs() -> None:
    """出处：探针解出来的字节必须与参考 PNG 逐字节相同（E2/E3 的读数因此可信）。"""
    if not REFS_DIR.is_dir():
        pytest.skip(f"参考件目录不在：{REFS_DIR}")
    report = _probe_report()
    checked = 0
    for texture_name, (file_name, _file_r, _file_a) in MASKMAP_ANCHORS.items():
        row = report["textures"].get(texture_name)
        if row is None:
            continue
        reference = hashlib.sha256(_reference_rgba(file_name)).hexdigest()
        assert row["rgba_sha256"] == reference, texture_name
        checked += 1
    for texture_name, (file_name, _want) in NORMAL_ANCHORS.items():
        row = report["textures"].get(texture_name)
        if row is None:
            continue
        reference = hashlib.sha256(_reference_rgba(file_name)).hexdigest()
        assert row["rgba_sha256"] == reference, texture_name
        checked += 1
    assert checked >= 5, f"只核对上 {checked} 张贴图（E2 两张 MaskMap + E3 三张法线图）"


def test_probe_import_built_every_material_from_a_mapping_table() -> None:
    """集成判据：importer 报出来的 material_rows 必须与纯函数重算的结果一致。"""
    report = _probe_report()
    rows = report["report_rows"]
    assert rows, "report.material_rows 是空的 —— importer 没接上映射表"
    by_shader: dict[str, str] = {}
    for row in rows:
        by_shader[str(row["shader"])] = str(row["table"])
    assert by_shader.get("Shader Graphs/UnitV3") == "M2"
    assert by_shader.get("HDRP/Lit") == "M1"
    # ⚠ 2026-10-17：`HDRP/Unlit` 有了 M5 之后**不再**是「认不出来」的例子
    # （`LODs` 那颗现在进 M5）。「认不出来 = TABLE_NONE」这条守卫改由
    # test_six_mapping_tables_cover_the_design_shader_distribution 的负控守着。
    assert by_shader.get("HDRP/Unlit") == "M5"

    unknown = [row for row in rows if row["table"] == materials.TABLE_NONE]
    for row in unknown:
        assert row["coverage"] == materials.COVERAGE_UNKNOWN
        assert row["notes"], f"{row['material']} 未识别却没写理由"

    # 与纯函数重算对上（探针里导出的真实材质字典就是 importer 吃的那一份）
    for row in rows:
        raw = None
        for candidate in report["raw_materials"]:
            if str(row["material"]).endswith(str(candidate.get("name"))):
                raw = candidate
                break
        if raw is None:
            continue
        plan = materials.plan_material(raw)
        assert plan.table_key == row["table"], (row["material"], plan.table_key, row["table"])
        assert plan.coverage == row["coverage"], (row["material"], plan.coverage, row["coverage"])
        assert set(plan.runtime_keys) == set(row["runtime_items"])


def test_e4_report_unreduced_list_has_the_five_items() -> None:
    """E4：面板/报告上的未还原清单必须有那 5 条，一条不缺（工单 §10 的完成信号）。"""
    report = _probe_report()
    lines = list(report["report_unreduced"])
    assert report["report_runtime_count"] == 5, lines
    for key in materials.RUNTIME_KEYS_UNITV3:
        assert any(line.startswith(key) for line in lines), f"未还原清单缺 {key}：{lines}"
    # 每条都要带依据（evidence），面板上要能看见"凭什么说它不还原"
    for line in lines:
        assert "（" in line and "）" in line, line

# --------------------------------------------------------------------------- #
# E4（2026-10-11）· UnitV4_Camo 的颜色层改由 **shader 自己写的 desc** 认
# --------------------------------------------------------------------------- #
#: 真读数：2026-10-10 从 `m_ParsedForm.m_PropInfo.m_Props[]` 读出来的四条 desc
#: （`_work/camo/判据与读数.md` §1.1，队友 diag-9035；⛔ 不是我们编的名字）。
U4_CAMO_DESCS: dict[str, str] = {
    "Layer_A97CDC25": "Albedo",
    "Layer_8D60137F": "Normal",
    "Layer_4C4561B0": "Mask",
    "Layer_A97CDC25_1": "Camo",
}


def test_e4_unitv4camo_reads_color_layers_from_the_shader_descs() -> None:
    """判据：颜色层**只认 desc**；`Layer_<hash>` 那样的名字前缀**不再是依据**。"""
    table = materials.M4_UNITV4_CAMO
    assert table.base_color_descs == (materials.DESC_ALBEDO, materials.DESC_CAMO)
    assert table.base_color_prefixes == (), (
        "前缀会把 Layer_8D60137F(desc=Normal) / Layer_4C4561B0(desc=Mask) 也认成颜色层"
    )
    assert table.color_slots_from_descs(U4_CAMO_DESCS) == ("Layer_A97CDC25", "Layer_A97CDC25_1")
    # 负控：没有 desc / desc 不在表白名单里 ⇒ 一个都不认（⛔ 不许按名字猜）
    assert table.color_slots_from_descs({}) == ()
    assert table.color_slots_from_descs({"Layer_A97CDC25": "Normal"}) == ()
    assert table.color_slots_from_descs({"Anything": "Albedo"}) == ("Anything",)


def test_e4_unitv3_also_declares_its_desc() -> None:
    """UnitV3 同样有 desc（'Albedo'）—— 前缀留作**兜底**（读不到 shader 树时用）。"""
    assert materials.M2_UNITV3.base_color_descs == (materials.DESC_ALBEDO,)
    assert materials.M2_UNITV3.base_color_prefixes == ("Layer_",)
    assert materials.M2_UNITV3.color_slots_from_descs({"Layer_A97CDC25": "Albedo"}) == (
        "Layer_A97CDC25",
    )


def test_e4_plan_material_prefers_desc_over_the_slot_name() -> None:
    """`plan_material(prop_descs=…)`：desc 说 'Normal' 的槽**不算**颜色层，哪怕它叫 Layer_。"""
    material = {
        "name": "dlc2-probe",
        "shader": materials.M4_UNITV4_CAMO.shader,
        "texture_slots": [("Layer_A97CDC25", 111), ("Layer_8D60137F", 222)],
    }
    without = materials.plan_material(material)
    assert without.desc_color_slots == ()
    assert without.slot_of(materials.ROLE_BASE_COLOR) is None, "没有 desc 时不该按名字认颜色层"
    with_desc = materials.plan_material(material, prop_descs=U4_CAMO_DESCS)
    assert with_desc.desc_color_slots == ("Layer_A97CDC25",)
    base = with_desc.slot_of(materials.ROLE_BASE_COLOR)
    assert base is not None and base.slot == "Layer_A97CDC25" and base.texture_id == 111
    normal_slot = next(s for s in with_desc.slots if s.slot == "Layer_8D60137F")
    assert normal_slot.role == "", "desc=Normal 的槽不该被判成颜色层"


def test_e5_camo_layer_gets_its_own_role_instead_of_stealing_base_color() -> None:
    """★ 2026-10-17：desc='Camo' 的那一格必须是 **ROLE_CAMO**，不许挤掉底图的 ROLE_BASE_COLOR。

    真件读数（`_work/audit-full/skin123_params.py`）：Yak130 皮肤 1/2/3 的 Albedo **逐字节相同**，
    差别只在 Camo 层（贴图 + `_CamoTiling` + `_OffsetX/_OffsetY`）⇒ 两层必须能分别取到。
    """
    material = {
        "name": "yak-130 body",
        "shader": materials.M4_UNITV4_CAMO.shader,
        "texture_slots": [
            ("Layer_A97CDC25", 111),
            ("Layer_A97CDC25_1", 333),
            ("Layer_8D60137F", 222),
        ],
        "floats": {"_OffsetX": 0.6, "_OffsetY": -0.004, "_Layer_step": 0.5},
        "colors": {"_CamoTiling": (2.0, 2.0, 0.0, 0.0)},
    }
    plan = materials.plan_material(material, prop_descs=U4_CAMO_DESCS)
    base = plan.slot_of(materials.ROLE_BASE_COLOR)
    camo = plan.slot_of(materials.ROLE_CAMO)
    assert base is not None and base.slot == "Layer_A97CDC25" and base.texture_id == 111
    assert camo is not None and camo.slot == "Layer_A97CDC25_1" and camo.texture_id == 333
    # 涂装层自己的平铺/偏移必须被带出来（否则接线时贴图落点会错）
    assert plan.scalar("_OffsetX") == 0.6
    assert plan.scalar("_OffsetY") == -0.004
    assert plan.color("_CamoTiling") == (2.0, 2.0, 0.0, 0.0)
    # 权重是**显式参数**（实测 0.24，可调），⛔ 不是猜出来的 shader 公式
    assert materials.CAMO_MIX_WEIGHT == 0.24


def test_e4_ratio_on_the_recorded_census_is_zero_before_and_full_after() -> None:
    """比例读数（**真件普查件**）：改前 **0/57（0%）** → 改后 **57/57（100%）**。"""
    path = WORK / "p0census" / "slots.json"
    if not path.is_file():
        pytest.skip("缺 _work/p0census/slots.json（真件普查产物）")
    data = json.loads(path.read_text(encoding="utf-8"))
    group = next(
        item for item in data["groups"] if item["shader"] == materials.M4_UNITV4_CAMO.shader
    )
    count = int(group["material_count"])
    assert count == 57, f"真件里 UnitV4_Camo 的材质数变了：{count}"
    recognized = materials.M4_UNITV4_CAMO.color_slots_from_descs(U4_CAMO_DESCS)
    assert recognized == ("Layer_A97CDC25", "Layer_A97CDC25_1")
    # 改后：每颗材质至少一个 desc 认出的颜色层**有图**（按逐颗 rows 数）
    hits = 0
    for row in group.get("rows") or []:
        slots = row.get("slots") or {}
        bound = {
            name
            for name, info in slots.items()
            if isinstance(info, dict) and not info.get("empty") and not info.get("absent")
        }
        if bound & set(recognized):
            hits += 1
    assert hits == count == 57, f"desc 规则只认出 {hits}/{count}"
    # 改前那一版（槽位表空）能认出的：0
    empty_table = replace(materials.M4_UNITV4_CAMO, base_color_descs=(), base_color_prefixes=())
    assert empty_table.color_slots_from_descs(U4_CAMO_DESCS) == ()

# --------------------------------------------------------------------------- #
# M5 · HDRP/Unlit 与 M6 · HDRP/Decal（2026-10-17 补的两张表）
# --------------------------------------------------------------------------- #
#: **读数出处**：`_work/audit-full/materials.jsonl`（76 个包 / 5,431 颗 Material，一行一颗）。
#: 每行的 `slots` = `[槽名, 贴图 fileID, 贴图 pathID, 是否带图]`，`floats` = 白名单标量。
AUDIT_JSONL = WORK / "audit-full" / "materials.jsonl"

#: 两族的**逐槽读数**（带图数 / 出现数）—— 必须与 kernel 两张表注释里的数字逐条相同。
FAMILY_SLOT_READINGS: dict[str, dict[str, tuple[int, int]]] = {
    "HDRP/Unlit": {
        "_UnlitColorMap": (24, 26),
        "_MainTex": (24, 26),
        "_EmissiveColorMap": (0, 26),
        "_DistortionVectorMap": (0, 26),
    },
    "HDRP/Decal": {
        "_BaseColorMap": (78, 81),
        "_NormalMap": (42, 81),
        "_MaskMap": (20, 81),
        "_EmissiveColorMap": (0, 81),
    },
}

#: 两族的材质数（上面那组读数的分母，单独钉一遍）。
FAMILY_MATERIAL_COUNTS: dict[str, int] = {"HDRP/Unlit": 26, "HDRP/Decal": 81}


def _audit_rows(shader: str) -> list[dict]:
    """从 jsonl 里按 shader 取行（`__meta__` 那种一行一包的抬头要跳过）。"""
    rows: list[dict] = []
    with AUDIT_JSONL.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if set(record) == {"__meta__"}:
                continue
            if record.get("shader") == shader:
                rows.append(record)
    return rows


def _unlit_row(**overrides: object) -> dict[str, object]:
    """M5 的合成件：**真读数照抄** —— `Ping Marker Material Point` 那一颗（jsonl 原值）。

    实测：`_UnlitColorMap` 与 `_MainTex` 指向同一个 pathID（6966644717970894953）——
    24/24 颗双绑材质全都如此（`_MainTex` 是 shader 里给 GI 烘焙留的镜像槽）。
    """
    material: dict[str, object] = {
        "name": "Ping Marker Material Point",
        "shader": "HDRP/Unlit",
        "texture_slots": (
            ("_DistortionVectorMap", None),
            ("_EmissiveColorMap", None),
            ("_MainTex", 6966644717970894953),
            ("_UnlitColorMap", 6966644717970894953),
        ),
        "floats": {
            "_AORemapMax": 1.0,
            "_AORemapMin": 0.0,
            "_AlphaCutoff": 0.097,
            "_BlendMode": 0.0,
            "_CullMode": 1.0,
            "_Cutoff": 0.097,
            "_Metallic": 0.0,
            "_NormalScale": 1.0,
            "_Smoothness": 0.5,
            "_SurfaceType": 1.0,
        },
        "valid_keywords": ("_SURFACE_TYPE_TRANSPARENT",),
        "invalid_keywords": (),
    }
    material.update(overrides)
    return material


def _decal_row(**overrides: object) -> dict[str, object]:
    """M6 的合成件：**真读数照抄** —— `Decal_standart` 那一颗（基色 + 法线带图、遮罩空）。"""
    material: dict[str, object] = {
        "name": "Decal_standart",
        "shader": "HDRP/Decal",
        "texture_slots": (
            ("_BaseColorMap", -2522645430642603886),
            ("_EmissiveColorMap", None),
            ("_MaskMap", None),
            ("_NormalMap", -5133453782419346116),
        ),
        "floats": {
            "_AORemapMax": 1.0,
            "_AORemapMin": 0.0,
            "_AlphaCutoff": 0.5,
            "_BlendMode": 0.0,
            "_CullMode": 2.0,
            "_Cutoff": 0.5,
            "_Metallic": 0.0,
            "_NormalScale": 1.0,
            "_Smoothness": 0.0,
            "_SurfaceType": 0.0,
        },
        "valid_keywords": (
            "_COLORMAP",
            "_MATERIAL_AFFECTS_ALBEDO",
            "_MATERIAL_AFFECTS_MASKMAP",
            "_MATERIAL_AFFECTS_NORMAL",
            "_NORMALMAP",
        ),
        "invalid_keywords": (),
    }
    material.update(overrides)
    return material


def test_m5_unlit_wires_the_unlit_color_map_as_base_color() -> None:
    """M5：`_UnlitColorMap` / `_MainTex` -> Base Color；这一族**没有** normal / mask 槽。"""
    table = materials.table_for("HDRP/Unlit")
    assert table is not None and table.key == "M5"
    assert table.base_color_slots == ("_UnlitColorMap", "_MainTex")
    assert table.normal_slots == (), "Unlit.shader 的 Properties 块没声明 _NormalMap"
    assert table.mask_slots == (), "Unlit.shader 的 Properties 块没声明 _MaskMap"

    plan = materials.plan_material(_unlit_row())
    assert plan.table_key == "M5"
    assert plan.coverage == materials.COVERAGE_FULL
    assert plan.unmapped_slots == ()
    assert plan.alpha_mode == "BLEND", "这一颗 _SurfaceType=1（贴点标记是透明面）"
    roles = {binding.slot: binding.role for binding in plan.slots}
    assert roles["_UnlitColorMap"] == materials.ROLE_BASE_COLOR
    assert roles["_MainTex"] == materials.ROLE_BASE_COLOR
    assert roles["_EmissiveColorMap"] == materials.ROLE_EMISSION
    assert roles["_DistortionVectorMap"] == "", "畸变图不是 Principled 的输入 ⇒ 不许乱给角色"
    # 连线：只有基色那一条（自发光这一族 0/26 带图 ⇒ 不会有 emission 连线）
    assert materials.ChannelLink(materials.ROLE_BASE_COLOR, "Base Color") in plan.links
    assert {link.role for link in plan.links} == {materials.ROLE_BASE_COLOR}
    # 标量：只有实测那一条（_Metallic / _Smoothness / _NormalScale 是死数据，不许冒出来）
    assert plan.scalar("_AlphaCutoff") == pytest.approx(0.097)
    assert plan.clip_threshold == pytest.approx(0.097)
    assert plan.scalar("_Metallic") is None
    assert plan.scalar("_Smoothness") is None
    assert plan.scalar("_NormalScale") is None


def test_m5_unlit_negative_control_base_color_map_is_not_a_base_color() -> None:
    """负控：`_BaseColorMap` **不在** M5 的槽位表里 ⇒ 绑了也不许被认成基色。

    这条尺子必须能红：同一颗材质装进 M1（M1 认得 `_BaseColorMap`）就**会**变成基色。
    """
    row = _unlit_row(
        texture_slots=(
            ("_BaseColorMap", 424242),
            ("_MainTex", 6966644717970894953),
            ("_UnlitColorMap", 6966644717970894953),
        )
    )
    plan = materials.plan_material(row)
    roles = {binding.slot: binding.role for binding in plan.slots}
    assert roles["_BaseColorMap"] == "", "HDRP/Unlit 没有 _BaseColorMap ⇒ 不许按名字猜成基色"
    assert plan.unmapped_slots == ("_BaseColorMap",)
    assert plan.coverage == materials.COVERAGE_PARTIAL

    m1 = materials.TABLES["HDRP/Lit"]
    assert m1.role_of("_BaseColorMap") == materials.ROLE_BASE_COLOR
    as_m1 = materials.plan_material(row, table=m1)
    base = as_m1.slot_of(materials.ROLE_BASE_COLOR)
    assert base is not None and base.slot == "_BaseColorMap", "尺子不能红 ⇒ 上面那条断言不算数"


def test_m6_decal_wires_base_color_and_normal_from_the_real_row() -> None:
    """M6：`_BaseColorMap` -> Base Color、`_NormalMap` -> Normal；两个槽都真带图。"""
    table = materials.table_for("HDRP/Decal")
    assert table is not None and table.key == "M6"
    assert table.base_color_slots == ("_BaseColorMap",)
    assert table.normal_slots == ("_NormalMap",)
    assert table.mask_slots == ("_MaskMap",)
    assert table.emission_slots == ("_EmissiveColorMap",)

    plan = materials.plan_material(_decal_row())
    assert plan.table_key == "M6"
    assert plan.coverage == materials.COVERAGE_FULL
    assert plan.unmapped_slots == ()
    assert plan.alpha_mode == "OPAQUE", "_SurfaceType=0 且没有 _ALPHATEST_ON"
    roles = {binding.slot: binding.role for binding in plan.slots}
    assert roles["_BaseColorMap"] == materials.ROLE_BASE_COLOR
    assert roles["_NormalMap"] == materials.ROLE_NORMAL
    assert roles["_MaskMap"] == materials.ROLE_MASK
    assert roles["_EmissiveColorMap"] == materials.ROLE_EMISSION
    # 这一颗没绑遮罩 ⇒ 只有基色与法线两条连线（遮罩 20/81、自发光 0/81）
    assert {link.role for link in plan.links} == {
        materials.ROLE_BASE_COLOR,
        materials.ROLE_NORMAL,
    }
    assert plan.scalar("_Metallic") == pytest.approx(0.0)
    assert plan.scalar("_Smoothness") == pytest.approx(0.0)
    assert plan.scalar("_NormalScale") is None, "_NormalScale 是 Lit 的属性，Decal 没声明"
    assert plan.scalar("_AlphaCutoff") is None, (
        "Decal 没声明 _AlphaCutoff（81/81 有值，都是换表留下的死数据）"
    )


def test_m6_decal_mask_map_splits_like_m1() -> None:
    """`_MaskMap` 的通道口径与 M1 一致（`DecalData.hlsl:80-95`）⇒ 连线必须逐条对上 `NODE_SHAPE`。"""
    row = _decal_row(
        texture_slots=(
            ("_BaseColorMap", -2522645430642603886),
            ("_MaskMap", 777),
            ("_NormalMap", -5133453782419346116),
        )
    )
    plan = materials.plan_material(row)
    mask_links = [link for link in plan.links if link.role == materials.ROLE_MASK]
    assert mask_links, "_MaskMap 已绑却一条遮罩连线都没有"
    # 与 M1 的表无关：同一份 NODE_SHAPE 里的三条（R 金属 / A 反一次 -> 粗糙度 / G 标注忽略）
    assert mask_links == [
        link for link in materials.NODE_SHAPE if link.role == materials.ROLE_MASK
    ]
    binding = plan.slot_of(materials.ROLE_MASK)
    assert binding is not None and binding.slot == "_MaskMap"
    assert binding.texture_id == 777


def test_m6_decal_negative_control_nothing_bound_means_no_links() -> None:
    """负控：`Wet` 那颗（jsonl 原值，四个槽全空）⇒ 一条连线都不许有。"""
    wet = _decal_row(
        name="Wet",
        texture_slots=(
            ("_BaseColorMap", None),
            ("_EmissiveColorMap", None),
            ("_MaskMap", None),
            ("_NormalMap", None),
        ),
        floats={"_Metallic": 0.0, "_Smoothness": 0.931, "_SurfaceType": 0.0},
        valid_keywords=("_MATERIAL_AFFECTS_MASKMAP",),
    )
    plan = materials.plan_material(wet)
    assert plan.table_key == "M6"
    assert plan.links == ()
    assert plan.unmapped_slots == ()
    assert plan.scalar("_Smoothness") == pytest.approx(0.931)


def test_m5_m6_scalars_are_exactly_the_measured_ones() -> None:
    """标量只留**实测有读数 ＋ shader 真声明**的那几条；死数据一个都不许进表。"""
    assert tuple(s.param for s in materials.M5_HDRP_UNLIT.scalars) == ("_AlphaCutoff",)
    assert tuple(s.param for s in materials.M6_HDRP_DECAL.scalars) == ("_Metallic", "_Smoothness")

    # 负控（一）：这 5 个在 Unlit 材质里 26/26 都有值，但 Unlit.shader 没声明 ⇒ 死数据
    for param in ("_Metallic", "_Smoothness", "_NormalScale", "_AORemapMin", "_AORemapMax"):
        assert materials.M5_HDRP_UNLIT.find_scalar(param) is None, f"M5 不该有 {param}"
    # 负控（二）：`_Cutoff` 与 `_AlphaCutoff` 26/26 逐颗相同 ⇒ 绑两次没有意义
    assert materials.M5_HDRP_UNLIT.find_scalar("_Cutoff") is None
    # 负控（三）：这 6 个在 Decal 材质里 81/81 都有值，但 Decal.shader 一个都没声明 ⇒ 死数据
    dead_decal = (
        "_NormalScale",
        "_AlphaCutoff",
        "_Cutoff",
        "_CullMode",
        "_SurfaceType",
        "_BlendMode",
    )
    for param in dead_decal:
        assert materials.M6_HDRP_DECAL.find_scalar(param) is None, f"M6 不该有 {param}"
    # 负控（四）：`_AORemapMin/Max` 声明了、81/81 有值，但全是默认 0/1 且 AO 那条本来就 ignored
    for param in ("_AORemapMin", "_AORemapMax"):
        assert materials.M6_HDRP_DECAL.find_scalar(param) is None, f"M6 不该有 {param}"

    binding = materials.M6_HDRP_DECAL.find_scalar("_Smoothness")
    assert binding is not None and binding.invert is True
    assert binding.apply(0.712) == pytest.approx(0.288, abs=1e-6)


def test_m5_m6_slot_readings_match_the_audit_jsonl() -> None:
    """**读数可复算**：两张表注释里的"带图 / 出现"数字必须能从 jsonl 重新数出来。"""
    if not AUDIT_JSONL.is_file():
        pytest.skip(f"缺审计产物：{AUDIT_JSONL}")
    for shader, expected in FAMILY_SLOT_READINGS.items():
        rows = _audit_rows(shader)
        assert len(rows) == FAMILY_MATERIAL_COUNTS[shader], (
            f"{shader} 的材质数变了：{len(rows)} != {FAMILY_MATERIAL_COUNTS[shader]}"
        )
        used: Counter[str] = Counter()
        bound: Counter[str] = Counter()
        for row in rows:
            for slot, _file_id, _path_id, has_texture in row["slots"]:
                used[slot] += 1
                if has_texture:
                    bound[slot] += 1
        for slot, (want_bound, want_used) in expected.items():
            assert (bound[slot], used[slot]) == (want_bound, want_used), (
                f"{shader} {slot} 带图 {bound[slot]}/{used[slot]}，注释里写的是"
                f" {want_bound}/{want_used}"
            )
        # ⛔ 表里声明的每个槽都必须在这份读数里（不许有"表里有、数据里没量过"的槽）
        table = materials.table_for(shader)
        assert table is not None
        for slot in table.known_slots:
            assert slot in expected, f"{shader} 表里声明了 {slot}，但读数表里没记它"


# --------------------------------------------------------------------------- #
# `_BaseColor` 颜色（2026-10-17）：玻璃这类**没有基色贴图**的材质，颜色与 alpha 只在这颗里
# --------------------------------------------------------------------------- #
def test_plan_carries_base_color_and_picks_it_by_mapping_order() -> None:
    '实测读数（`_work/audit-full/materials-colors.jsonl`）：'
    '`DLC2_UH60_glass` = (0.537, 0.537, 0.467, 0.051)。'
    plan = materials.plan_material({
        "name": "DLC2_UH60_glass",
        "shader": "HDRP/Lit",
        "texture_slots": (("_BaseColorMap", None), ("_MainTex", None)),
        "floats": {"_SurfaceType": 1.0, "_AlphaCutoff": 0.0},
        "colors": {"_BaseColor": (0.537, 0.537, 0.467, 0.051), "_Color": (1.0, 0.0, 0.0, 1.0)},
        "valid_keywords": ("_SURFACE_TYPE_TRANSPARENT",),
    })
    assert plan.base_color_rgba() == pytest.approx((0.537, 0.537, 0.467, 0.051))
    assert plan.color("_Color") == pytest.approx((1.0, 0.0, 0.0, 1.0))
    assert plan.alpha_mode == "BLEND"


def test_plan_falls_back_to_unlit_color_when_there_is_no_base_color() -> None:
    plan = materials.plan_material({
        "name": "LODs",
        "shader": "HDRP/Unlit",
        "texture_slots": (("_MainTex", None),),
        "colors": {"_UnlitColor": (0.2, 0.3, 0.4, 1.0)},
    })
    assert plan.base_color_rgba() == pytest.approx((0.2, 0.3, 0.4, 1.0))


def test_plan_without_colors_has_no_base_color_rgba() -> None:
    '负控：源里没有颜色 ⇒ `None`（⛔ 不许编一个默认灰出来）。'
    plan = materials.plan_material({
        "name": "no_colors",
        "shader": "HDRP/Lit",
        "texture_slots": (("_BaseColorMap", 12345),),
        "floats": {},
    })
    assert plan.base_color_rgba() is None
    assert plan.colors == ()


def test_plan_carries_only_known_colors_and_never_invents_a_base_color() -> None:
    '`_EmissiveColor` 属于「只搬运」那一类：进 `plan.colors`，但**不**当基色。'
    plan = materials.plan_material({
        "name": "only_emissive",
        "shader": "HDRP/Lit",
        "texture_slots": (("_BaseColorMap", None),),
        "colors": {"_EmissiveColor": (1.0, 1.0, 1.0, 1.0), "_SomeOtherColor": (0.5, 0.5, 0.5, 1.0)},
    })
    assert plan.base_color_rgba() is None
    assert [name for name, _value in plan.colors] == ["_EmissiveColor"]


