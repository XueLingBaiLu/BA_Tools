r"""① 拿 时**自动接 VT 图**的尺子（纯 Python：路径约定 ＋ 门控 ＋ 逐材质读数）。

**判据一行**（实测，⛔ 数字都能量）：

```
pytest tests/test_vt_auto_bind.py -o addopts= -q -n 0   -> 10 passed
pytest tests/test_vt_auto.py      -o addopts= -q -n 0   ->  6 passed（含 1 条 slow）
pytest tests/test_packed_maps.py  -o addopts= -q -n 0   -> 10 passed（task-8：打包在 .blend 里的图）
```

## 这一条量什么

用户在 2026-10-15 说：「**你不觉得这样很麻烦吗？为什么不像加载材质底图一样，拿出来就
加载上？**」—— 所以这里量的是**自动**这一跳（不需要用户点任何按钮）：

* **尺子取产品标识**：`blender_addon.materials` 的 `game_map_dir` / `game_map_files` /
  `vt_shader_table` / `vt_decisions` ＋ `blender_addon.importer._bind_vt_maps`；
* **正控（这把尺子能红）**：目录里放两份候选文件（`RU_OTHER_*` 字母序在前），单位键
  `RU_T72B` 必须挑中**它自己那对**）；`vt_shader_table` 必须先能对 `HDRP/Unlit` 报 None
  —— 报不出 None 就说明这把尺子恒真，那时下面的断言一条都不算数；
* **负控**：认不出的 shader / 已经有自己法线的材质，`_bind_vt_maps` 走完**一个字节都不许动**
  （节点数 / 法线图 / 强度 / `bamod_normal_source` 逐条比）。

## 怎么跑（两层）

1. **纯 Python**（本文件）：`blender_addon.materials` 里的那几个函数不碰 bpy 实例，
   用最小 bpy 桩按路径加载即可（与 `tests/test_unit_picker.py` 同一口径）；
2. **真 Blender**（`tests/test_vt_auto.py`）：起一次无头 Blender 跑
   `tests/probe_vt_auto.py`（**不开任何游戏包**，几秒），量**真节点树**上接没接。

## 未做（如实）

* **没开 units 包**：这条判据量的是「图在哪 / 哪颗该接 / 接线对不对」；
  「真件里那 3 颗材质接出来什么」由 `tests/test_import_unit.py` 的重活那条守；
* **判不了观感**：遮蔽 / 倒影 / 条纹三类只有用户能判（AGENTS §0.12.1）；
* ⛔ **不碰 `panels_edit.py`**：面板那一份 `game_map_dir` / `game_map_files` 的去重由 Lead 收尾。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"

#: 目录里两个单位各一对候选 —— 字母序 `RU_OTHER_*` 在 `RU_T72B_*` **前面**。
#: 这正是「会不会挑错那一对」的正控：按目录里第一份字母序挑，就会错到 RU_OTHER 上。
FIXTURE_FILES = {
    "RU_T72B_normal_decoded.png": b"normal-t72b",
    "RU_T72B_mask_raw.png": b"mask-t72b",
    "RU_OTHER_normal_decoded.png": b"normal-other",
    "RU_OTHER_mask_raw.png": b"mask-other",
}


@pytest.fixture(scope="module")
def vt_module():
    """按路径加载 `blender_addon/materials.py`（顶层 import bpy ⇒ 先塞最小桩）。

    ⛔ 只桩 **bpy 这一个名字**：本文件量的是那几个**不碰 bpy 实例**的函数
    （路径约定 ＋ shader 门控），它们在真 Blender 里与这里逐字同源。
    """
    # ⛔ 不能 `setdefault`：合跑时前面的模块可能已经留了一个**带 `bpy.data`** 的桩，
    # 本文件的前提（只有 `bpy` 这个名字）就不成立 ⇒ `test_b7` 那类断言**单跑绿、合跑红**
    # （2026-10-15 实测：6 个模块一起跑时它红了）。⇒ 自己保证前提，用完还原，别影响别人。
    saved_bpy = sys.modules.get("bpy")
    sys.modules["bpy"] = types.ModuleType("bpy")
    # ⚠ 2026-10-18（M2-A）：`materials.py` 现在模块级 `from . import session` ⇒ 必须当**包**加载，
    #   裸模块会 'attempted relative import with no known parent package'（11 条全红）。
    #   形状照 `tests/test_addon_properties.py` 的合成包：`__path__=[ADDON]`，子模块按包名找。
    package = types.ModuleType("bamod_vt_addon")
    package.__path__ = [str(ADDON)]  # type: ignore[attr-defined]
    sys.modules[package.__name__] = package
    sys.path.insert(0, str(REPO_ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.materials", ADDON / "materials.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    package.materials = module  # type: ignore[attr-defined]
    spec.loader.exec_module(module)
    yield module
    if saved_bpy is None:
        sys.modules.pop("bpy", None)
    else:
        sys.modules["bpy"] = saved_bpy


@pytest.fixture()
def vt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """一个装着两对候选的抽图目录（`BAMOD_VT_DIR` 指过去）。"""
    root = tmp_path / "vt-extract"
    root.mkdir()
    for name, payload in FIXTURE_FILES.items():
        (root / name).write_bytes(payload)
    monkeypatch.setenv("BAMOD_VT_DIR", str(root))
    return root


# --------------------------------------------------------------------------- #
# A · 路径约定（图在哪）—— 正控：挑错那一对必须能红
# --------------------------------------------------------------------------- #
def test_a1_env_override_and_repo_default(vt_dir: Path, vt_module) -> None:
    """目录 = `BAMOD_VT_DIR`；**不设**时回落到仓里 `_work/vt-extract`。"""
    assert Path(vt_module.game_map_dir()) == vt_dir
    # 正控：环境变量一撤，落点必须换一个地方 —— 否则说明这个量法读的是别的东西
    import os

    os.environ.pop("BAMOD_VT_DIR", None)
    assert Path(vt_module.game_map_dir()) == WORK / "vt-extract"


def test_a2_keyed_pick_beats_alphabetical_order(vt_dir: Path, vt_module) -> None:
    """按**单位键**挑那一对 —— 正控：目录里字母序更靠前的 `RU_OTHER_*` 不许被挑中。"""
    paths, note = vt_module.game_map_files("RU_T72B")
    assert note == "", note
    assert Path(paths["normal"]).name == "RU_T72B_normal_decoded.png"
    assert Path(paths["mask"]).name == "RU_T72B_mask_raw.png"
    # 正控（这把尺子能红）：换一个键 -> 拿到的必须是**另一对**，不是同一对
    other, _ = vt_module.game_map_files("RU_OTHER")
    assert Path(other["normal"]).name == "RU_OTHER_normal_decoded.png"
    assert other["normal"] != paths["normal"]


def test_a3_key_mismatch_refuses_and_says_why(tmp_path: Path, monkeypatch, vt_module) -> None:
    """⛔ 单位键对不上 ⇒ **一张都不接**（2026-10-17 改）。

    旧版这里无条件用「唯一候选」，实测后果（用户真机）：`_work/vt-extract/` 里只放了
    RU_T72B 那一份时，导入 **ACV / AH-1W 都被接上 T-72B 的法线与遮罩** ——
    不报错，只在 note 里写一句「按唯一候选取的」，症状是整机贴图乱。
    """
    root = tmp_path / "one"
    root.mkdir()
    (root / "SOME_UNIT_normal_decoded.png").write_bytes(b"n")
    (root / "SOME_UNIT_mask_raw.png").write_bytes(b"m")
    monkeypatch.setenv("BAMOD_VT_DIR", str(root))
    paths, note = vt_module.game_map_files("RU_T72B")
    assert paths == {}, paths
    assert "对不上单位" in note, note


def test_a3b_unique_candidate_still_ok_without_a_key(
    tmp_path: Path, monkeypatch, vt_module
) -> None:
    """**没有单位键**时「唯一候选」仍可用（note 写明「按唯一候选取的」）—— 旧口径的合法形态。"""
    root = tmp_path / "one_only"
    root.mkdir()
    (root / "SOME_UNIT_normal_decoded.png").write_bytes(b"n")
    (root / "SOME_UNIT_mask_raw.png").write_bytes(b"m")
    monkeypatch.setenv("BAMOD_VT_DIR", str(root))
    paths, note = vt_module.game_map_files("")
    assert Path(paths["normal"]).name == "SOME_UNIT_normal_decoded.png"
    assert "唯一候选" in note, note


def test_a4_many_candidates_and_no_key_says_so(tmp_path: Path, monkeypatch, vt_module) -> None:
    """多份候选又对不上单位键 ⇒ **不许猜一个**，note 里明写「分不清」。"""
    root = tmp_path / "many"
    root.mkdir()
    for name in ("A_normal_decoded.png", "B_normal_decoded.png"):
        (root / name).write_bytes(b"n")
    monkeypatch.setenv("BAMOD_VT_DIR", str(root))
    paths, note = vt_module.game_map_files("RU_T72B")
    assert "normal" not in paths, paths
    assert "分不清" in note, note


def test_a5_missing_dir_names_the_dir_and_the_env(tmp_path: Path, monkeypatch, vt_module) -> None:
    """目录不存在 ⇒ 空 dict ＋ 一句人话（点名目录与 `BAMOD_VT_DIR`）。"""
    missing = tmp_path / "nope"
    monkeypatch.setenv("BAMOD_VT_DIR", str(missing))
    paths, note = vt_module.game_map_files("RU_T72B")
    assert paths == {}
    assert str(missing) in note and "BAMOD_VT_DIR" in note, note


def test_a6_missing_file_names_what_is_missing(tmp_path: Path, monkeypatch, vt_module) -> None:
    """目录在、但缺其中一张 ⇒ 点名**缺的是哪一路 ＋ 哪个后缀**。"""
    root = tmp_path / "half"
    root.mkdir()
    (root / "RU_T72B_normal_decoded.png").write_bytes(b"n")
    monkeypatch.setenv("BAMOD_VT_DIR", str(root))
    paths, note = vt_module.game_map_files("RU_T72B")
    assert "mask" not in paths
    assert "_mask_raw.png" in note, note


# --------------------------------------------------------------------------- #
# B · shader 门控（哪几颗该动）—— 先做正控：它必须能对 Unlit 报 None
# --------------------------------------------------------------------------- #
def test_b1_gate_is_not_tautological(vt_module) -> None:
    """正控：这把尺子**必须能返回 None** —— 全 True 就说明它在瞎放行。"""
    assert vt_module.vt_shader_table("HDRP/Unlit") is None
    assert vt_module.vt_shader_table("") is None
    assert vt_module.vt_shader_table("完全不存在的 shader") is None


def test_b2_vt_families_pass_and_static_families_are_spared(vt_module) -> None:
    """读数（kernel 四张表逐表实查）：VT 族过、静态 2D 槽的族**一个都不许过**。

    * 过：`Shader Graphs/UnitV3`（`_VirtualDetailNormal` 是 VT 槽）、
      `Shader Graphs/UnitV4_Camo`（DLC2 那批车：整张表 desc 驱动，Normal/Mask 也是 VT 层）；
    * 不过：`HDRP/Lit`（履带 / 枪械：`_NormalMap` 是它**自己的** 2D 槽）、
      `Shader Graphs/Infantry Shader`（`_Normal` / `_Mask`）。
    """
    assert vt_module.vt_shader_table("Shader Graphs/UnitV3") is not None
    assert vt_module.vt_shader_table("Shader Graphs/UnitV4_Camo") is not None
    assert vt_module.vt_shader_table("HDRP/Lit") is None
    assert vt_module.vt_shader_table("Shader Graphs/Infantry Shader") is None


def test_b3_gate_never_hardcodes_shader_names(vt_module) -> None:
    """判据只能取自 kernel 的表 —— 把某张表的名字换了，门控必须跟着变（⛔ 不写死名字）。

    做法：拿真表的字段当输入，断言「VT 槽前缀」与「desc 驱动」这两条**各自**都能单独放行
    （Monkeypatch kernel.TABLES 里塞一张**没有 VT 槽也不是 desc 驱动**的表 -> 必须不过）。
    """
    from ba_mod_maker.asset_kernel import materials as kernel

    plain = kernel.MaterialTable(
        key="T9", shader="X/Plain", title="t", normal_slots=("_NormalMap",)
    )
    patched = dict(kernel.TABLES)
    patched["X/Plain"] = plain
    original = kernel.TABLES
    try:
        kernel.TABLES = patched  # type: ignore[assignment]
        assert vt_module.vt_shader_table("X/Plain") is None
    finally:
        kernel.TABLES = original  # type: ignore[assignment]



def test_b7_packed_lookup_never_needs_a_bpy_instance(vt_module) -> None:
    """★ task-8：查「打包图」不许要求 bpy 实例 —— 没有 `bpy.data` 时一律当「没有」，⛔ 不炸。

    由来：`game_map_files` 现在会顺手看**场景里有没有同名打包图**（`packed_image_by_name`）。
    而本文件量它的那几个函数时用的是**最小 bpy 桩**（只有 `bpy` 这个名字，没有 `data`）——
    那时必须干净地返回「一张都没有」，⛔ 不许 AttributeError。
    """
    stub = sys.modules["bpy"]
    assert not hasattr(stub, "data"), "本用例的前提：这个桩里没有 bpy.data"
    assert vt_module._packed_images() == []
    assert vt_module.packed_image_by_name("任意名字.png") is None
    assert vt_module.packed_map_image("normal", "RU_T72B") == (None, "")

