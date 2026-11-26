r"""方案 A：`model.blend` 比 `model.glb` 新时**自动导出**（找不到 Blender / 导出失败都报红）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_blender_bridge.py -q`
→ 4 passed（含两条负控：blend 更新时 `auto=False` 必须**报红**而不是静默用旧 glb）。

## 为什么

用户 2026-10-20 定：导入器读 `model.glb`，但用户在 Blender 里改的是 `model.blend`；
改完忘了导出时，旧行为是**静默用旧几何**（最坏的一种错）。现在改成自动导一次。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from ba_mod_maker.core.assets.blender_bridge import ensure_glb_fresh, find_blender
from ba_mod_maker.core.errors import AssetError


def _proj(tmp_path: Path, *, blend_newer: bool) -> Path:
    blend = tmp_path / "model.blend"
    glb = tmp_path / "model.glb"
    blend.write_bytes(b"BLEND")
    glb.write_bytes(b"GLB")
    if blend_newer:
        os.utime(glb, (1_000_000, 1_000_000))
    else:
        os.utime(blend, (1_000_000, 1_000_000))
    return tmp_path


def test_glb_newer_is_a_noop(tmp_path: Path) -> None:
    proj = _proj(tmp_path, blend_newer=False)
    res = ensure_glb_fresh(proj)
    assert res.exported is False
    assert "不比 blend 旧" in res.reason


def test_blend_newer_raises_when_auto_is_off(tmp_path: Path) -> None:
    """负控：改动没导出时必须**报红**，⛔ 不许静默用旧 glb。"""
    proj = _proj(tmp_path, blend_newer=True)
    with pytest.raises(AssetError):
        ensure_glb_fresh(proj, auto=False)


def test_no_blend_is_a_noop(tmp_path: Path) -> None:
    res = ensure_glb_fresh(tmp_path)
    assert res.exported is False
    assert "没有 model.blend" in res.reason


def test_find_blender_finds_something_or_none() -> None:
    """本机有 Blender（否则会返回 None）—— 这条只证明函数能跑、不抛。"""
    got = find_blender()
    assert got is None or got.is_file()
