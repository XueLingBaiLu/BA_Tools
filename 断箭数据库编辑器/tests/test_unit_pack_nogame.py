"""建包这条路**不许碰游戏目录**（2026-10-19 用户口径：导入器不加载任何游戏的 bundle）。"""
from __future__ import annotations

from pathlib import Path


def test_empty_bundle_never_opens_a_game_bundle(monkeypatch, tmp_path: Path) -> None:
    from ba_mod_maker.core.assets import unit_pack

    def boom(*_a, **_k):
        raise AssertionError("建空包不许读游戏目录 / 游戏包")

    monkeypatch.setattr(unit_pack, "find_game", boom)
    monkeypatch.setattr(unit_pack, "_units_bundle", boom)
    monkeypatch.setattr(unit_pack, "_open_pack", boom)

    res = unit_pack.create_empty_bundle(key="NoGame", out_dir=tmp_path)

    bundle = Path(res["bundle"])
    assert bundle.is_file()
    assert res["objects"] == 0
    assert res["container"] == 0
    assert 500 < res["size"] < 4096


def test_builtin_typetree_is_shipped() -> None:
    from ba_mod_maker.core.assets import unit_pack

    blob = unit_pack.builtin_source_meta()
    assert len(blob) > 100_000, "内置类型表太小，肯定不对"
    assert b"UnityFS" not in blob[:8]  # 这是元数据区，不是容器头
