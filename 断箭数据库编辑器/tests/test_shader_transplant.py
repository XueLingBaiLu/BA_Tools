"""shader_transplant（把游戏原件 shader 移植进我们的包）的最小用例。

⭐ 口径（2026-10-26）：这里的用例只守**接线**（名字映射 / Pass 计数 / 解不开时 fail-closed），
不守「游戏里看得对不对」—— 那是真机与用户的事。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.assets import shader_transplant as st
from ba_mod_maker.core.errors import AssetError


def test_stem_roundtrip_keeps_slashes() -> None:
    """`Shader Graphs/UnitV3` 这种带斜杠的名字必须能存成文件名再还原。"""
    for name in ("HDRP/Lit", "Shader Graphs/UnitV3", "Hidden/HDRP/FallbackError"):
        stem = st.stem_of(name)
        assert "/" not in stem
        assert st.name_of_stem(stem) == name


def test_passes_of_counts_every_subshader() -> None:
    """Pass 数是「真件 vs 桩」唯一那把尺子，必须把所有 subshader 加起来。"""
    obj = {"m_ParsedForm": {"m_SubShaders": [
        {"m_Passes": [{}, {}, {}]},
        {"m_Passes": [{}, {}]},
    ]}}
    assert st.passes_of(obj) == 5
    assert st.passes_of({}) == 0
    assert st.passes_of({"m_ParsedForm": {"m_SubShaders": []}}) == 0


def test_inband_refs_fails_closed_on_garbage() -> None:
    """解不开的 payload 返回空表（⛔ 不抛、也不猜），保证移植对坏 shader 是安全的。"""
    assert st._inband_refs(b"\x00\x01\x02", [], None) == []


def test_units_bundle_of_rejects_non_game_root(tmp_path) -> None:
    """不是游戏根目录时给 AssetError（带 hint），不是 FileNotFoundError。"""
    with pytest.raises(AssetError) as exc:
        st.units_bundle_of(tmp_path)
    assert "units" in str(exc.value)
