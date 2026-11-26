"""LODGroup 的 screenRelativeHeight 必须严格递减（2026-10-06 真机现场）。

现场：prefab_build 把每一档都硬编码成 0.5 ⇒ 机库里坦克只占一两成屏幕 ⇒
**Unity 一档都不选 ⇒ 整个模型不画**（用户原话：「这个模型看起来是没有网格的」）。
真件 T-72B 实测 4 档：0.1 / 0.04 / 0.005 / 0.0015。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.asset_kernel import prefab_build as pb
from ba_mod_maker.core.errors import AssetError


def test_default_heights_are_strictly_decreasing() -> None:
    for count in (1, 2, 3, 4, 5):
        heights = pb.lod_heights_for(count)
        assert len(heights) == count
        assert all(b < a for a, b in zip(heights, heights[1:], strict=False)), heights


def test_default_heights_match_the_real_endpoints() -> None:
    """首档 0.1、末档 0.0015（真件那两端的值）—— 两档时正好就是真件两端。"""
    assert pb.lod_heights_for(2) == (pb.LOD_HEIGHT_FIRST, pb.LOD_HEIGHT_LAST)
    heights = pb.lod_heights_for(4)
    assert heights[0] == pytest.approx(0.1)
    assert heights[-1] == pytest.approx(0.0015)


def test_non_decreasing_heights_are_rejected() -> None:
    """全档同值 = 当年那个 bug，必须能红。"""
    with pytest.raises(AssetError):
        pb.check_lod_heights((0.5, 0.5))
    with pytest.raises(AssetError):
        pb.check_lod_heights((0.1, 0.2))
    with pytest.raises(AssetError):
        pb.check_lod_heights((0.0,))
    with pytest.raises(AssetError):
        pb.check_lod_heights((1.0,))


def test_good_heights_pass_through() -> None:
    assert pb.check_lod_heights((0.1, 0.0015)) == (0.1, 0.0015)
