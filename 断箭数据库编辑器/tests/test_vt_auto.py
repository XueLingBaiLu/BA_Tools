r"""① 拿 时自动接 VT 图 —— **真 Blender** 判据（真节点树上量接没接）。

**判据一行**：`pytest tests/test_vt_auto.py -o addopts= -q -n 0` -> **6 passed**
（5 条快 ＋ 1 条 `slow` 真件；本文件新建前 = 0 收集）。

## 这一条量什么（真 Blender 里，不是读数行）

* **该接的接上**：`Shader Graphs/UnitV3` 且自己没有法线的材质 ⇒ Principled.Normal 与
  Metallic/Roughness 上**真有链**（图名 = 抽出来的那两张）；
* **负控 1（履带 / 高机那种）**：`Shader Graphs/UnitV3` **已经有自己的法线** ⇒ 走完一遍
  **节点数 / 法线图名 / 强度 / `bamod_normal_source` 逐条相同**（一个字节都没动）；
* **负控 2**：`HDRP/Lit`（履带自己的 2D 法线槽）与 `HDRP/Unlit`（认不出）⇒ 整颗不动；
* **没图**：`BAMOD_VT_DIR` 指到一个空目录 ⇒ **一颗都不接** ＋ 读数点名缺的是哪张 / 哪个目录。

## 怎么跑

起一次无头 Blender 跑 `tests/probe_vt_auto.py`（**不开任何游戏包**，几秒），
再对探针打出来的 JSON 逐条断言。Blender 不在 -> skip（⛔ 不是「绿」）。

## 未做（如实）

* **不开 units 包**：这里量的是「自动那一跳的接线」；真件（RU_T72B 那 3 颗材质）的读数
  由 `tests/test_import_unit.py` 那条重活守 —— 本文件**不**开包（重活一次只许一个人做）；
* **判不了观感**：遮蔽 / 倒影 / 条纹三类只有用户能判（AGENTS §0.12.1）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: ★ 2026-10-18 深夜（全量验证时抓到）：**这一份量的是被删掉的那套设计**。
#: 2026-10-17 18:37 `importer._bind_vt_maps` 重写成
#: `(materials, stacks_of: dict[material -> (group, item)], report, *, game_root)` ——
#: 图的来源从「开发机 `BAMOD_VT_DIR` 目录里的固定两张」变成「按每颗材质自己的
#: (group, item) 从游戏原文件现抽，落进 `%LOCALAPPDATA%\BAModMaker\vt` 缓存」。
#: `tests/probe_vt_auto.py` 仍在按**旧签名**调（`_bind_vt_maps(materials, unit_key, report)`）
#: ⇒ 整片红，而它想量的那个「按单位一对图」的行为**已经不存在了**。
#: ⛔ 不硬凑假绿：整模块如实 skip，新设计的判据在 `tests/test_vt_auto_bind.py`
#: （2026-10-17 21:25 建，量的是按材质 stacks 绑 + 缓存抽取）。
#: 要恢复本文件：先按新签名重写 probe 的 F/G/H 三段（`stacks_of` 从哪儿来、缓存怎么造），
#: 再改断言 —— 那时再删掉这一行 `pytestmark`。
pytestmark = pytest.mark.skip(
    reason="2026-10-17 重写了 VT 自动接法（按材质 stacks ＋ 缓存抽取），本文件的探针仍按旧签名"
    "调 `_bind_vt_maps`；新设计的判据见 tests/test_vt_auto_bind.py"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).resolve().parent / "probe_vt_auto.py"
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")

#: 探针那一趟认的单位键（文件名约定 = `<键>_normal_decoded.png` / `<键>_mask_raw.png`）。
UNIT_KEY = "RU_FAKE"
#: 真 Mask 的通道均值读数（R=0.5877 金属度 · G=0.9062 AO · A=0.3008 光滑度）。
#: 这里用同一组值的 8 位近似 —— 图是**我们自己造的夹具**，判据只量「接的是不是这一对」。
NORMAL_RGB = (128, 128, 255)
MASK_RGBA = (150, 231, 0, 77)


def _blender() -> Path | None:
    """本机的 blender.exe（`BA_BLENDER` / `BAMOD_BLENDER` 可覆盖）；没有 -> None。"""
    override = os.environ.get("BA_BLENDER") or os.environ.get("BAMOD_BLENDER")
    if override and Path(override).is_file():
        return Path(override)
    return DEFAULT_BLENDER if DEFAULT_BLENDER.is_file() else None


def _fixture_dir(tmp_path: Path) -> Path:
    """造那一对图（8x8）—— 与 `_work/vt-extract` 里的**同名同形**。"""
    root = tmp_path / "vt-extract"
    root.mkdir()
    Image.new("RGB", (8, 8), NORMAL_RGB).save(root / f"{UNIT_KEY}_normal_decoded.png")
    Image.new("RGBA", (8, 8), MASK_RGBA).save(root / f"{UNIT_KEY}_mask_raw.png")
    return root


def _run_probe(vt_dir: Path) -> dict:
    """跑一趟无头 Blender，把 JSON 块解回来（⛔ 不开游戏包）。"""
    blender = _blender()
    if blender is None:
        pytest.skip(f"没找到 {DEFAULT_BLENDER}（BA_BLENDER 可指到别处）")
    env = dict(os.environ)
    env["BAMOD_VT_DIR"] = str(vt_dir)
    proc = subprocess.run(
        [
            str(blender), "-b", "--factory-startup",
            "--python", str(PROBE), "--", "--vt-dir", str(vt_dir), "--key", UNIT_KEY,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=env, timeout=600, check=False,
    )
    assert proc.returncode == 0, f"Blender 退出码 {proc.returncode}\n{(proc.stderr or '')[-3000:]}"
    match = re.search(r"^BAMOD_VTAUTO_BEGIN$(.*?)^BAMOD_VTAUTO_END$", proc.stdout, re.M | re.S)
    assert match, f"探针没打 JSON 块\n{(proc.stdout or '')[-3000:]}"
    facts = json.loads(match.group(1))
    assert facts.get("ok") is True, facts.get("error")
    return facts


@pytest.fixture(scope="module")
def probe(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """一趟 Blender 的读数（整个模块共用一次；⛔ 不开包）。"""
    root = tmp_path_factory.mktemp("vt-extract-fixture")
    return _run_probe(_fixture_dir(root))


# --------------------------------------------------------------------------- #
# 正控：该接的接上（而且接的是**这一对**图）
# --------------------------------------------------------------------------- #
def test_vt_auto_binds_the_bare_vt_material(probe: dict) -> None:
    """`Shader Graphs/UnitV3` ＋ 自己没有法线 ⇒ 自动接上那两张图，链落在 Principled 上。"""
    before, after = probe["before"]["bare"], probe["after"]["bare"]
    assert before["normal"] is None and before["mask"] is None, before
    assert after["normal"] == f"{UNIT_KEY}_normal_decoded.png", after
    assert after["mask"] == f"{UNIT_KEY}_mask_raw.png", after
    # 「来源记在哪」也要对：这一路是**用户（我们）从磁盘挑的图**，不是包里的派生图
    assert after["normal_source"] == "", after
    assert after["user_sources"]["normal"].endswith(f"{UNIT_KEY}_normal_decoded.png")
    assert after["user_sources"]["mask"].endswith(f"{UNIT_KEY}_mask_raw.png")
    assert after["node_count"] > before["node_count"], (before, after)
    assert probe["bound"] == 1, probe["lines"]


def test_vt_auto_says_which_image_went_where(probe: dict) -> None:
    """逐材质读数必须**点名**：接上的是哪两张图（Mask 的 R/A/G 落点见 bind_user_maps）。"""
    bare = [line for line in probe["lines"] if line.startswith("BAMOD_probe_UnitV3裸｜")]
    assert len(bare) == 1, probe["lines"]
    assert "接上" in bare[0], bare[0]
    assert f"normal={UNIT_KEY}_normal_decoded.png" in bare[0], bare[0]
    assert f"mask={UNIT_KEY}_mask_raw.png" in bare[0], bare[0]
    assert "VT 图 1 颗" in probe["summary"], probe["summary"]


# --------------------------------------------------------------------------- #
# 负控：已经有自己法线的 / 不是 VT 族的 —— 一个字节都不许动
# --------------------------------------------------------------------------- #
def test_own_normal_is_untouched(probe: dict) -> None:
    """负控（履带 / 高机那种）：自带法线 ⇒ 整颗材质逐字段不变。

    比的是**四样**：法线图名（连线反查）/ NormalMap 强度 / `bamod_normal_source` / 节点数。
    """
    before, after = probe["before"]["own_normal"], probe["after"]["own_normal"]
    assert before["normal"] == "BAMOD_履带自己的法线"
    assert after == before, (before, after)
    assert after["normal_strength"] == 4.0, after
    line = [x for x in probe["lines"] if x.startswith("BAMOD_probe_已有法线｜")]
    assert len(line) == 1 and "自带法线" in line[0], probe["lines"]


def test_non_vt_shaders_are_untouched(probe: dict) -> None:
    """负控：`HDRP/Lit`（履带自己的 2D 法线槽）与认不出的 shader ⇒ 整颗不动。"""
    for name in ("hdrp_lit", "not_vt"):
        assert probe["after"][name] == probe["before"][name], (name, probe)
    assert probe["gate"] == {
        "UnitV3": True,
        "UnitV4_Camo": True,
        "HDRP/Lit": False,
        "Infantry": False,
        "HDRP/Unlit": False,
    }, probe["gate"]


def test_missing_maps_bind_nothing_and_name_what_is_missing(tmp_path: Path) -> None:
    """没图 ⇒ **一颗都不接**（各颗逐字段与 before 相同）＋ 读数点名缺哪张 / 哪个目录。"""
    empty = tmp_path / "empty-vt"
    empty.mkdir()
    facts = _run_probe(empty)
    assert facts["bound"] == 0, facts["lines"]
    for name in ("bare", "own_normal", "not_vt", "hdrp_lit"):
        assert facts["after"][name] == facts["before"][name], (name, facts)
    assert f"{UNIT_KEY}_normal_decoded.png" in facts["note"], facts["note"]
    assert str(empty) in facts["note"], facts["note"]
    assert "VT 图" not in facts["summary"], facts["summary"]


# --------------------------------------------------------------------------- #
# 真件（slow）：RU_T72B —— 自动接上「底图那颗」，履带那颗**一个字节都没被动过**
# --------------------------------------------------------------------------- #
#: 真件读数（2026-10-15 实测）：`units_assets_all` 里这 3 颗材质 + 各自的 shader。
REAL_UNIT_KEY = "RU_T72B"
REAL_ASSET = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"
REAL_MATERIALS = {
    "RU_T72B__RU_T72B 1": "Shader Graphs/UnitV3",
    "RU_T72B__RU_T72B_track": "HDRP/Lit",
    "RU_T72B__LODs": "HDRP/Unlit",
}
#: 履带自己那张法线的读数（⛔ 这三样是**从包里接好的**，自动接 VT 一颗都不许改）。
TRACK_NORMAL = "RU_T72B__RU_Track7290_Standard_22_Normal__NormalRGB"
TRACK_STRENGTH = 4.0
TRACK_NODES = 5


def _real_bundle() -> Path:
    """游戏根里那个 units 包（没有就 skip —— ⛔ 不是「绿」）。"""
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    base = bundle_dir(root) if root is not None else None
    hits = sorted(base.glob("units_assets_all*.bundle")) if base is not None else []
    if not hits:
        pytest.skip("找不到 units_assets_all*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return hits[0]


def _run_real_probe(no_vt: bool) -> dict:
    """真开一次那个 3.14 GB 的包（**重活**，一次只许一个人做）。"""
    blender = _blender()
    if blender is None:
        pytest.skip(f"没找到 {DEFAULT_BLENDER}（BA_BLENDER 可指到别处）")
    vt_dir = WORK / "vt-extract"
    if not (vt_dir / f"{REAL_UNIT_KEY}_normal_decoded.png").is_file():
        pytest.skip(f"{vt_dir} 里没有 {REAL_UNIT_KEY}_normal_decoded.png")
    env = dict(os.environ)
    env["BAMOD_VT_DIR"] = str(vt_dir)
    argv = [
        str(blender), "-b", "--factory-startup", "--python", str(PROBE), "--",
        "--key", REAL_UNIT_KEY, "--import-bundle", str(_real_bundle()),
        "--asset", REAL_ASSET,
    ]
    if no_vt:
        argv += ["--no-vt-maps", "1"]
    proc = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=env, timeout=3600, check=False,
    )
    assert proc.returncode == 0, f"Blender 退出码 {proc.returncode}\n{(proc.stderr or '')[-3000:]}"
    match = re.search(r"^BAMOD_VTAUTO_BEGIN$(.*?)^BAMOD_VTAUTO_END$", proc.stdout, re.M | re.S)
    assert match, f"探针没打 JSON 块\n{(proc.stdout or '')[-3000:]}"
    facts = json.loads(match.group(1))
    assert facts.get("ok") is True, facts.get("error")
    return facts


@pytest.mark.slow
def test_real_t72b_auto_bind_and_track_untouched() -> None:
    """真件：UnitV3 那颗**自动接上**；履带 / LODs 两颗**逐字段与「关掉自动接」时相同**。

    判据一行：`pytest tests/test_vt_auto.py -m slow -q -n 0` -> 两次真件导入的 3 颗材质里，
    「VT 图 1 颗」而履带的 (法线图 / 强度 / 节点数 / 来源) 两趟**逐字段相等**。

    ⚠ 两次导入是**同一个二进制**（同一份 `import_bundle`），所以逐字段比较是**差分**、不是
    背答案：谁把履带那颗动了，这一条当场红。
    """
    off = _run_real_probe(no_vt=True)
    on = _run_real_probe(no_vt=False)
    assert off["bound"] == 0 and off["lines"] == [], off["summary"]
    assert on["bound"] == 1, on["lines"]
    assert on["shaders"] == REAL_MATERIALS, on["shaders"]
    # ① 该接的接上：底图那颗（UnitV3）两路都接的是**抽出来**那两张
    body_on = on["shots"]["RU_T72B__RU_T72B 1"]
    body_off = off["shots"]["RU_T72B__RU_T72B 1"]
    assert body_off["normal"] is None and body_off["mask"] is None, body_off
    assert body_on["normal"] == "RU_T72B_normal_decoded.png", body_on
    assert body_on["mask"] == "RU_T72B_mask_raw.png", body_on
    assert body_on["normal_source"] == "", body_on
    assert body_on["node_count"] > body_off["node_count"], (body_off, body_on)
    # ② 负控：履带那颗**一个字节都没被动过**（法线图 / 强度 / 节点数 / 来源逐条比）
    track_on = on["shots"]["RU_T72B__RU_T72B_track"]
    assert track_on == off["shots"]["RU_T72B__RU_T72B_track"], (track_on, off["shots"])
    assert track_on["normal"] == TRACK_NORMAL, track_on
    assert track_on["normal_strength"] == TRACK_STRENGTH, track_on
    assert track_on["node_count"] == TRACK_NODES, track_on
    assert track_on["user_sources"] == {"normal": "", "mask": ""}, track_on
    # ③ LODs（HDRP/Unlit，认不出）也不动
    assert on["shots"]["RU_T72B__LODs"] == off["shots"]["RU_T72B__LODs"]
    # ④ 逐材质读数点名每一条结局（⛔ 不许静默跳过）
    assert len(on["lines"]) == len(REAL_MATERIALS), on["lines"]
    assert any("RU_T72B 1｜接上" in line for line in on["lines"]), on["lines"]
    assert any("_track｜不是 VT shader ⇒ 不动" in line for line in on["lines"]), on["lines"]
    assert "VT 图 1 颗" in on["summary"], on["summary"]
