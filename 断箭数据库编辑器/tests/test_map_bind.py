r"""② 改 · 把磁盘上的 PNG（或内置占位图）接进 Normal / Mask —— **真 Blender** 判据。

**判据一行**：`pytest tests/test_map_bind.py -q -n 0` -> 通过数 **从 0 变 N**（新建前 = 0 收集）。

## 这条判据量的是什么

用户在 2026-10-15 问的是「**未来给自己的模型用法线**」＋「其它与材质相关的东西也要在 Blender 里
看到」—— 所以这里量的**不是读数行**，是 **Principled 那几个输入上真有链**：

* 法线走 `TexImage -> NormalMap -> Principled.Normal`；
* 金属度走 `SeparateColor.Red -> Principled.Metallic`；
* 粗糙度走 `TexImage.Alpha -> Invert -> Principled.Roughness`；
* **AO 走 `SeparateColor.Green -> Mix(MULTIPLY) -> Principled.Base Color`**（Principled 没有
  AO 输入 ⇒ 落点是乘进底色；内核 NODE_SHAPE 那条 `ignored=True` 的通道口径一个字没改）。

两条入口都量：**从磁盘挑一张 PNG**（用户自己画的图）与**用游戏原图**（`_work/vt-extract` 里
抽出来的 `<单位键>_normal_decoded.png` / `_mask_raw.png`，按文件名约定挑，⛔ 不写死 item ID）。

## 正控 / 负控（AGENTS §0.9 动作 2：尺子必须先能红）

* **正控 1（这把尺子能红）**：`BAMOD_probe_对照` 那颗**没接过图**的材质，同一个量法必须返回空 ——
  所以「接上后链非空 / 形状相等」不是恒真；
* **正控 2（内核只有一份）**：同一把 AST 扫描器对 `asset_kernel/normals.py` **必须**报出行号
  （那里真有 `math.sqrt(z2)`），而 `blender_addon/materials.py` 必须**零命中** ——
  报不出内核那侧就说明这把尺子测不出东西，不许当判据；
* **负控 6 条**：不存在的图 / 一张坏图 / 没选图也没勾占位 / 强度 ≤ 0 / 角色名不认识 /
  「法线好、Mask 坏」—— 一律 `ValueError`（面板侧 = 红），且**节点树一个字节都没动**
  （⛔ 不许静默、⛔ 不许接了一半）。

## 怎么跑

本用例起一次无头 Blender 跑 `tests/probe_map_bind.py`（**不开任何游戏包**，几秒），
再对探针打出来的 JSON 逐条断言。Blender 不在 -> skip（⛔ 不是「绿」）。

## 未做（如实）

* **不开 units 包**：这条判据量的是场景节点树；「包里那张图接得对不对」归 tests/test_materials.py；
* **判不了观感**：遮蔽 / 倒影 / 条纹三类只有用户能判（AGENTS §0.12.1）—— 这里只证明**链路通**；
* 只跑**真的从磁盘读图**那条路，不写真机截图。
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).resolve().parent / "probe_map_bind.py"
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")

#: 三种输入：一张标准 RGB 切空间法线 / 一张 RGBA Mask / 一张**没有 A 通道**的 Mask。
FIXTURE_SPECS: dict = {
    "user_normal.png": ("RGB", (128, 128, 255)),
    "user_mask.png": ("RGBA", (0, 255, 0, 128)),
    "user_mask_rgb.png": ("RGB", (0, 255, 0)),
}

#: 这一趟认的单位键（「用游戏原图」那条入口按它挑文件）。
GAME_KEY = "RU_T72B"
#: 抽图目录里的**文件名约定**（⛔ 不含任何 item ID）；像素值抄真实读数：
#: 真 Mask 是 R=0.5877（金属度）· G=0.9062（AO）· A=0.3008（光滑度）。
GAME_DIR_FILES: dict = {
    "RU_T72B_normal_decoded.png": ("RGB", (128, 128, 255)),
    "RU_T72B_mask_raw.png": ("RGBA", (150, 231, 0, 77)),
    # ★ 干扰项：字母序在 RU_T72B 前面 —— 挑不到单位键就会先撞上它（这条是**正控**）
    "RU_OTHER_normal_decoded.png": ("RGB", (128, 128, 255)),
    "RU_OTHER_mask_raw.png": ("RGBA", (10, 10, 0, 200)),
}

def _blender() -> Path | None:
    """本机的 blender.exe（`BA_BLENDER` / `BAMOD_BLENDER` 可覆盖）；没有 -> None。"""
    override = os.environ.get("BA_BLENDER") or os.environ.get("BAMOD_BLENDER")
    if override and Path(override).is_file():
        return Path(override)
    return DEFAULT_BLENDER if DEFAULT_BLENDER.is_file() else None


def _formula_sites(path: Path) -> list[int]:
    """扫这个 .py 里**法线 z 重建**那类写法（`math.sqrt(...)` 调用 / `**` 幂运算）的行号。

    ⛔ 只扫**代码**（AST）—— materials.py 的注释里写着那句公式，那是说明，不是第二份实现。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sqrt"
        ):
            hits.append(node.lineno)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            hits.append(node.lineno)
    return sorted(set(hits))


def test_kernel_has_the_z_formula_and_the_addon_does_not() -> None:
    """内核只有一份：z 重建住 asset_kernel.normals；插件里 ⛔ 不许再有第二份。

    ★ 先做**正控**：同一段扫描器对 `asset_kernel/normals.py` 必须报出行号 ——
    报不出来就说明这把尺子恒真（测不出东西），那它就不能当判据（AGENTS §0.9 动作 2）。
    """
    kernel = REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel" / "normals.py"
    addon = REPO_ROOT / "blender_addon" / "materials.py"
    kernel_hits = _formula_sites(kernel)
    addon_hits = _formula_sites(addon)
    assert kernel_hits, f"正控失败：{kernel.name} 里没扫到 z 重建公式 ⇒ 这把尺子测不出东西"
    assert addon_hits == [], f"插件里又写了一份法线公式：materials.py 第 {addon_hits} 行"


@pytest.fixture(scope="module")
def facts(tmp_path_factory) -> dict:
    """起一次无头 Blender 跑探针，收它打出来的 JSON 块。"""
    blender = _blender()
    if blender is None:
        pytest.skip("找不到 blender.exe（设 BA_BLENDER 指过去）")
    work = tmp_path_factory.mktemp("mapbind")
    targets: dict = {}
    for name, (mode, color) in FIXTURE_SPECS.items():
        target = work / name
        Image.new(mode, (64, 64), color).save(target)
        targets[name] = str(target)
    garbage = work / "broken.png"
    garbage.write_text("这不是一张图", encoding="utf-8")
    game_dir = work / "vt-extract"
    game_dir.mkdir()
    for name, (mode, color) in GAME_DIR_FILES.items():
        Image.new(mode, (64, 64), color).save(game_dir / name)
    game_empty = work / "vt-extract-empty"
    game_empty.mkdir()
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
            "--normal",
            targets["user_normal.png"],
            "--mask",
            targets["user_mask.png"],
            "--mask_rgb",
            targets["user_mask_rgb.png"],
            "--garbage",
            str(garbage),
            "--key",
            GAME_KEY,
            "--game-dir",
            str(game_dir),
            "--game-empty",
            str(game_empty),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        cwd=str(REPO_ROOT),
        check=False,
    )
    match = re.search(
        r"^BAMOD_MAPBIND_BEGIN\s*$(.*?)^BAMOD_MAPBIND_END\s*$", proc.stdout, re.M | re.S
    )
    assert match, f"探针没打印 JSON 块（退出码 {proc.returncode}）\n{(proc.stdout or "")[-3000:]}"
    data = json.loads(match.group(1))
    if proc.returncode != 0:
        raise AssertionError(
            f"Blender 退出码 {proc.returncode}\n" + str(data.get("error"))[-3000:]
        )
    assert data.get("probe_rc") == 0, str(data.get("error"))[-3000:]
    return data

def test_operator_is_registered(facts) -> None:
    """两颗算子都注册上了、面板类也在册。

    ⚠ `bpy.types.<Python 类名>` 在 5.2 上**看不见**（对照那颗老算子同样 False）——
    所以只用**真能分辨**的两把尺子：`bpy.ops.ba_mod.*` 与面板类。
    """
    assert facts["ops"]["edit_map_bind"] is True
    assert facts["ops"]["edit_map_pick"] is True
    assert facts["ops"]["panel_class_registered"] is True
    assert facts["ops"]["class_registered"] == facts["ops"]["class_control_existing"]


def test_panel_role_names_are_the_kernel_roles(facts) -> None:
    """面板传下去的 roles 与 kernel 的角色名逐字相同（⛔ 面板里不另抄一份口径）。"""
    assert facts["roles"]["panel_normal"] == [facts["roles"]["kernel_normal"]]
    assert facts["roles"]["panel_mask"] == [facts["roles"]["kernel_mask"]]
    assert facts["roles"]["panel_both"] == [
        facts["roles"]["kernel_normal"],
        facts["roles"]["kernel_mask"],
    ]


def test_positive_control_an_unbound_material_has_no_chain(facts) -> None:
    """★ 尺子能红：**没接过图**的对照材质上三条链都是空 ⇒「接上后非空」不是恒真。"""
    assert facts["control"]["normal"] == []
    assert facts["control"]["metallic"] == []
    assert facts["control"]["roughness"] == []
    assert facts["control"]["images_by_role"]["normal"] is None
    assert "没接" in facts["control"]["state_line"]


def test_normal_and_mask_are_wired_by_the_operator(facts) -> None:
    """正控：Normal / Metallic / Roughness 各有一条链，形状逐条钉死。"""
    assert facts["operator"]["result"] == ["FINISHED"]
    assert facts["bound"]["normal"] == [
        ["ShaderNodeNormalMap", "Normal", "ShaderNodeBsdfPrincipled", "Normal"],
        ["ShaderNodeTexImage", "Color", "ShaderNodeNormalMap", "Color"],
    ]
    assert facts["bound"]["metallic"] == [
        ["ShaderNodeSeparateColor", "Red", "ShaderNodeBsdfPrincipled", "Metallic"],
        ["ShaderNodeTexImage", "Color", "ShaderNodeSeparateColor", "Color"],
    ]
    assert facts["bound"]["roughness"] == [
        ["ShaderNodeInvert", "Color", "ShaderNodeBsdfPrincipled", "Roughness"],
        ["ShaderNodeTexImage", "Alpha", "ShaderNodeInvert", "Color"],
    ]
    assert facts["bound"]["normal_strength"] == facts["bound"]["asked_strength"] == 4


def test_both_images_are_non_color_and_the_base_color_survives(facts) -> None:
    """法线 / Mask 都是**数据不是颜色**（Non-Color）；顺带证明没把基色那一路清掉。"""
    assert facts["bound"]["normal_colorspace"] == "Non-Color"
    assert facts["bound"]["mask_colorspace"] == "Non-Color"
    assert facts["bound"]["normal_size"] == [64, 64]
    assert facts["bound"]["mask_size"] == [64, 64]
    assert facts["bound"]["base_color_image"] is not None
    # ★ AO 乘进 Base Color 之后，原来那条基色线接在 Mix 的 B 上（⛔ 不是被清掉）——
    #   而「基色用的是哪张图」仍然读得出来（_images_by_role 认这颗 Mix）。
    assert facts["bound"]["images_by_role"]["base_color"] == facts["bound"]["base_color_image"]
    assert ["ShaderNodeTexImage", "Color", "ShaderNodeMix", "B"] in facts["bound"]["base_color"]
    assert [
        "ShaderNodeMix",
        "Result",
        "ShaderNodeBsdfPrincipled",
        "Base Color",
    ] in facts["bound"]["base_color"]


def test_readout_says_which_route_uses_which_image(facts) -> None:
    """读数口径：逐路写清「接上了 ＋ 用的哪张图 ＋ 颜色空间 ＋ 接线形状」＋ 按实际连线读回。"""
    lines = facts["operator"]["reported"]
    assert lines, "面板没拿到读数"
    assert "接上 2 路" in lines[0]
    assert "user_normal.png" in lines[0] and "user_mask.png" in lines[0]
    assert "Non-Color" in lines[1] and "NormalMap(Strength=4)" in lines[1]
    assert any("读回（按实际连线）" in line and "MaskMap=user_mask.png" in line for line in lines)
    assert any("金属度 ← Mask.R · 粗糙度 ← Mask.A · AO ← Mask.G" in line for line in lines)
    assert facts["bound"]["user_sources"]["normal"].endswith("user_normal.png")
    assert "法线=user_normal.png" in facts["bound"]["state_line"]


def test_second_run_is_idempotent(facts) -> None:
    """幂等：再跑一次节点树 / 节点名 / 图数一个都不许变（⛔ 不许每跑一次多攒一颗节点）。"""
    assert facts["idempotent"]["tree_equal"] is True
    assert facts["idempotent"]["node_names_equal"] is True
    assert facts["idempotent"]["image_count_equal"] is True
    assert facts["idempotent"]["node_types"].count("ShaderNodeInvert") == 1


def test_placeholder_works_without_picking_a_file(facts) -> None:
    """没选任何 PNG 也能用内置占位图（读数里必须点名「占位」）。"""
    block = facts["placeholder"]
    assert block["placeholders"] == [True, True]
    assert block["images"] and all(name.startswith(block["prefix"]) for name in block["images"])
    assert "含内置占位图" in block["summary"]
    assert block["normal"] and block["metallic"] and block["roughness"]
    assert all(value.split("(")[0] == block["prefix"] for value in block["sources"].values())


def test_mask_without_alpha_is_reported(facts) -> None:
    """Mask 图没有 A 通道时**读数必须点名**（否则用户得到一片镜面而不自知）。"""
    block = facts["mask_without_alpha"]
    assert block["mask_depth"] == 24
    assert block["notes"] and "没有 A 通道" in block["notes"][0]
    assert any("没有 A 通道" in line for line in block["lines"])


def test_mask_ao_is_wired_into_base_color(facts) -> None:
    """★ Mask 的 **AO（G）**在 Blender 里有真落点：`Mix(MULTIPLY)` 乘进 Base Color。

    Principled 没有 AO 输入（内核 NODE_SHAPE 因此把这条标成 ignored）—— 乘进底色是 Blender 里
    的等价物，也是用户「在 Blender 里看得到」的那个位置。
    """
    links = facts["bound"]["base_color"]
    assert ["ShaderNodeSeparateColor", "Green", "ShaderNodeMix", "A"] in links
    assert "AO ← Mask.G" in facts["operator"]["reported"][2]
    assert any(
        "SeparateColor.Green -> Mix(MULTIPLY) -> Principled.Base Color" in line
        for line in facts["operator"]["reported"]
    )


def test_game_original_entry_picks_files_by_unit_key(facts) -> None:
    """「用游戏原图」按**文件名约定 ＋ 单位键**挑文件（⛔ 不写死 item ID）。

    ★ 正控：抽图目录里**同时**放着 `RU_OTHER_*`（字母序在 `RU_T72B` 前面）——
    挑不到单位键就会先撞上它，所以「挑中的是 RU_T72B_*」不是恒真。
    """
    block = facts["game"]
    assert block["key"] == "RU_T72B"
    assert block["paths"]["normal"].endswith("RU_T72B_normal_decoded.png"), block["paths"]
    assert block["paths"]["mask"].endswith("RU_T72B_mask_raw.png"), block["paths"]
    assert "RU_OTHER" not in block["paths"]["normal"]
    assert block["result"] == ["FINISHED"] and not block["error"], block


def test_game_original_entry_readout_names_every_route(facts) -> None:
    """读数逐路点名：法线 ← 文件（尺寸 · Non-Color）· 金属度/粗糙度/AO 各一路。"""
    lines = facts["game"]["reported"]
    assert lines[0].startswith("游戏原图（")
    assert "RU_T72B_normal_decoded.png" in lines[0] and "RU_T72B_mask_raw.png" in lines[0]
    assert "法线 ← RU_T72B_normal_decoded.png（64x64 · Non-Color" in lines[1]
    assert "金属度 ← Mask.R · 粗糙度 ← Mask.A · AO ← Mask.G" in lines[2]
    assert any("SeparateColor.Red -> Principled.Metallic" in line for line in lines)
    assert any("TexImage.Alpha -> Invert -> Principled.Roughness" in line for line in lines)
    ao_land = "SeparateColor.Green -> Mix(MULTIPLY) -> Principled.Base Color"
    assert any(ao_land in line for line in lines)
    assert facts["game"]["normal"] and facts["game"]["metallic"] and facts["game"]["roughness"]


def test_game_original_missing_reports_red_and_wires_nothing(facts) -> None:
    """负控：抽图目录空 ⇒ 报红（点名缺哪张），且材质上**一路都没接**（⛔ 不拿别的图顶上）。"""
    block = facts["game_missing"]
    assert block["paths"] == {}
    assert block["result"] == []
    assert "接不了游戏原图" in block["error"]
    assert "缺 normal、mask" in block["reported"][0]
    assert "*_normal_decoded.png" in block["note"] and "*_mask_raw.png" in block["note"]
    assert set(block["wired"]) == {"base_color"}, block["wired"]

@pytest.mark.parametrize(
    "case",
    [
        "missing_file",
        "garbage",
        "no_pick_no_placeholder",
        "bad_strength",
        "unknown_role",
        "half_bad_is_atomic",
    ],
)
def test_bad_input_reports_red_and_touches_nothing(facts, case: str) -> None:
    """负控：坏输入一律 ValueError（= 面板红），且节点树一个字节都没动（⛔ 不静默、不半接）。"""
    block = facts["negatives"][case]
    assert block["raised"] == "ValueError", block
    assert block["message"].strip(), "报错必须带人话"
    assert block["touched"] is False, "抛错之前不许已经接上图"
