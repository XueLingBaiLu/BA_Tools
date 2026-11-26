r"""引擎的履带族匹配规则：`Tracks` 构造器里那两个 lambda（**唯一权威**）+ 285 件实测。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_track_rule.py -q -n 0`

引擎依据（ISIL，2026-10-23 读）：

    _work/isil_b2/IsilDump/BrokenArrow/BrokenArrow/Client/Ecs/Animations/Tracks_NestedType___c.txt
      <.ctor>b__16_0: x.name -> String.ToLower -> ">= String.Contains("trackl")
      <.ctor>b__16_1: 同形，字面量 "trackr"

| 段 | 判据 |
|---|---|
| A | 产品的 `unit_shape.track_side` 与引擎规则**逐名等价**（大小写不敏感的子串） |
| B | **真件 285 件里没有一个节点名含 `trackl`/`trackr`** ⇒ 这一族**不能进硬闸**（进闸必假红） |
| C | 真件的履带族实际写法是 `Track_L` / `L_Track`（引擎那两条 lambda 都匹配不到）—— 钉住这个事实 |
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import unit_shape

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
FROZEN = WORK / 'l7' / 'units_roots.json'


def _engine_side(name: str) -> str | None:
    low = name.lower()
    if "trackl" in low:
        return "left"
    if "trackr" in low:
        return "right"
    return None


def test_a_product_matcher_equals_the_engine_rule() -> None:
    """A：产品匹配器与引擎 lambda 逐名等价（⛔ 别在别处再写一份）。"""
    names = [
        "TrackL", "TrackR", "trackl1", "trackr9", "L_Track", "R_Track", "Track_L",
        "track_L", "RU_TanktrakL", "track", "LOD_Track", "trackled", "",
    ]
    for n in names:
        assert unit_shape.track_side(n) == _engine_side(n), n


def test_c_real_forms_do_not_match_the_engine_rule() -> None:
    """C：真件写法 `Track_L` / `L_Track` **都不含** `trackl` —— 钉住这个反直觉事实。"""
    assert unit_shape.track_side("TrackL") == "left"
    assert unit_shape.track_side("TrackR") == "right"
    assert unit_shape.track_side("Track_L") is None
    assert unit_shape.track_side("L_Track") is None
    assert unit_shape.track_side("RU_TanktrakL") is None


def test_b_285_real_units_have_zero_engine_track_matches() -> None:
    """B：285 件实测 ⇒ 0 个节点命��引擎规则；52 件有 track 字样（写法见 C）。

    这条就是「履带族不能进硬闸」的机械依据：一进闸，52 个真件会全红。
    """
    if not FROZEN.is_file():
        pytest.skip(f"冻结抽件不在：{FROZEN}")
    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    prefabs = data['prefabs']
    matched = 0
    with_track_word = 0
    for p in prefabs:
        names = [n['name'] for n in p['nodes']]
        if any('track' in n.lower() for n in names):
            with_track_word += 1
        matched += sum(1 for n in names if _engine_side(n) is not None)
    assert len(prefabs) == 285
    assert matched == 0, f'有 {matched} 个节点命中引擎规则 —— 那就可以进闸了，重测一次'
    assert with_track_word == 52
