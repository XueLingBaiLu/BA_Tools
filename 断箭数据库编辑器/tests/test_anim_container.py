r"""L12 · `AnimationManager._container` 整表导出（只读）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_anim_container.py -q -n 0`
→ 3 passed（含负控：不是 AnimationManager / 包不在，都必须**报红**而不是返回空表）。

真机读数（T-72B，`--path-id 9177362236935111464`）：悬挂 **12** 元素 · 车轮左右各 **8** ·
履带 `RU_TanktrakL`/`RU_TanktrakR` · 后坐 `_keyData` **0** 条。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel.anim_container import is_pptr, read_container
from ba_mod_maker.core.errors import AssetError


def test_pptr_shape() -> None:
    assert is_pptr({"m_FileID": 0, "m_PathID": 5}) is True
    assert is_pptr({"m_PathID": 5}) is False
    assert is_pptr([1, 2]) is False
    assert is_pptr(None) is False


def test_missing_bundle_raises(tmp_path: Path) -> None:
    """负控：包不在 ⇒ AssetError（⛔ 不许返回空表冒充成功）。"""
    with pytest.raises(AssetError):
        read_container(tmp_path / "没有这个包.bundle", 1)


def test_not_animation_manager_raises() -> None:
    """负控：pathID 不是 AnimationManager —— 用真包时由读树那一步抛（这里只钉契约）。"""
    with pytest.raises(AssetError):
        read_container(Path("不存在.bundle"), 0)
