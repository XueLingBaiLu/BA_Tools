r"""提取器要能从**我们新造的包**（`BA_Mod`）里取模型（用户 2026-10-20 明确的需求）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_bundle_locating.py -q`
→ 5 passed（含两条关键负控：装机写的相对路径要解析到 `BA_Mod\`；裸文件名要解析到 `aa\PC`）。

## 形状（实测）

- 我们装机写的 catalog 包路径：`<aa>\..\..\..\BA_Mod\<包>` —— **相对 `aa` 解析**（三级回到游戏根）；
- 没装机时是裸文件名 ⇒ 原版包在 `aa\PC`；
- 两条都必须能定位；都定位不到 ⇒ 返回 None，调用方自己报错（⛔ 不猜）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.core.assets.game import original_bundle_name, resolve_bundle_path


def _fake_game(root: Path) -> Path:
    """造一个最小游戏目录：`aa/PC/units_x.bundle` + `BA_Mod/y.bundle`。"""
    pc = root / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC"
    pc.mkdir(parents=True)
    (pc / "units_x.bundle").write_bytes(b"U")
    mod = root / "BA_Mod"
    mod.mkdir()
    (mod / "y.bundle").write_bytes(b"Y")
    return root


def test_plain_name_resolves_to_original(tmp_path: Path) -> None:
    game = _fake_game(tmp_path)
    got = resolve_bundle_path(game, "units_x.bundle")
    assert got is not None and got.parent.name == "PC"


def test_mod_relative_path_resolves_into_ba_mod(tmp_path: Path) -> None:
    r"""★ 用户的需求：装机写下的 `..\..\..\BA_Mod\<包>` 要能定位到我们自己的包。"""
    game = _fake_game(tmp_path)
    got = resolve_bundle_path(game, "..\\..\\..\\BA_Mod\\y.bundle")
    assert got is not None
    assert got.parent.name == "BA_Mod"
    assert got.name == "y.bundle"


def test_missing_returns_none(tmp_path: Path) -> None:
    game = _fake_game(tmp_path)
    assert resolve_bundle_path(game, "nope.bundle") is None
    assert resolve_bundle_path(game, "") is None
    assert resolve_bundle_path(game, None) is None


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("units_assets_all_ab.bundle", "units_assets_all_ab.bundle"),
        ("..\\..\\..\\BA_Mod\\x.bundle", "x.bundle"),
        ("C:/g/BA_Mod/x.bundle", "x.bundle"),
    ],
)
def test_file_name_only(raw: str, want: str) -> None:
    assert original_bundle_name(raw) == want
