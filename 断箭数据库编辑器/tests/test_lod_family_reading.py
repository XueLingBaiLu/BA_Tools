r"""A9（LOD 同族子网格差异）**只许当读数**：真件的差异来自「玻璃只在 LOD0」。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_lod_family_reading.py -q -n 0`

实测（2026-10-24，units 包，拿锁）：

    Aircraft/RU_TU142.prefab
      LOD0  TU142      subs=2 idx=[264, 10164]  slots=2 ['RU_TU95142 0glass', 'RU_TU95142 1']
      LOD1  02_TU142   subs=1 idx=[4113]        slots=1 ['RU_TU95142 1']
      LOD2  03_TU142   subs=1 idx=[1026]        slots=1 ['RU_TU95142 1']

⇒ 少的那一档是**玻璃几何**（264 索引那块），不是漏改网格。所以 A9 不许算红。

| 段 | 判据 |
|---|---|
| A | 故意「只改主 LOD」的包：A9 **报读数**、`problems` 里**没有** A9 |
| B | 同族的差异能点名（`Chassis` 出现在读数里） |
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import unit_bundle

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
NEG = WORK / 'nu' / 'neg_lodmesh.bundle'
TURRET = (WORK / 'nu' / 'turret'
          / 'newturret_a_assets_all_e0160655c538b2ea46cd0e8e7a002728.bundle')
TURRET_PATH = 'Assets/Resources_moved/BAMod/US_M10_105.prefab'


def _a9_lines(gate: object) -> list[str]:
    return [ln for ln in gate.readings if 'A9' in ln]  # type: ignore[attr-defined]


def test_a_slot_mismatch_is_a_reading_not_a_red() -> None:
    """A：只改主 LOD 的包 —— A9 只进读数，⛔ 不许进 problems。"""
    if not NEG.is_file():
        pytest.skip(f"样本包不在：{NEG}")
    gate = unit_bundle.verify_unit_bundle(NEG)
    assert _a9_lines(gate), 'A9 读数没了'
    assert not [p for p in gate.problems if 'A9' in p]


def test_b_the_mismatch_names_the_family() -> None:
    """B：读数点名是哪一族不一致（`Chassis`），便于人看。"""
    if not NEG.is_file():
        pytest.skip(f"样本包不在：{NEG}")
    lines = _a9_lines(unit_bundle.verify_unit_bundle(NEG))
    assert 'Chassis' in lines[0]


def test_c_a_real_pack_reports_all_families_consistent() -> None:
    """C：真炮塔包 —— 同族全部同数（这条是正向读数，别被改成红）。"""
    if not TURRET.is_file():
        pytest.skip(f"炮塔包不在：{TURRET}")
    gate = unit_bundle.verify_unit_bundle(TURRET, asset_path=TURRET_PATH, turret_prefab=True)
    line = _a9_lines(gate)[0]
    assert '一致' in line
