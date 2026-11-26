r"""地址**解析链**核对 (\`new_asset.verify_registered_address\`) 的判据。

**判据一行**：pytest tests/test_address_chain.py -q -n 0 -> 通过数 0 -> N，其中 **3 条是负控**。

夹具 = **真游戏目录的 catalog 副本** ＋ 我们自己那两个小包（搬到临时"假游戏目录"里），
所以不需要动游戏、也不需要 units（3.4 GB）。缺游戏/缺包就整组 skip。

| # | 喂什么 | 必须 |
|---|---|---|
| 正控 | 副本 catalog + 真包文件 | \`ok is True\`（五跳读数齐全） |
| 负控 1 | 包文件**不在** \`aa\\PC\` | 报 \`包文件不在\` |
| 负控 2 | 包**被改过一个字节** | 报 \`m_Crc 与磁盘不符\` |
| 负控 3 | \`asset_path\` 与容器不符 | 报 \`包内容器里没有\` |
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ba_mod_maker.core.asset.new_asset import verify_registered_address
from ba_mod_maker.core.assets.game import find_game_root

KEY = "RouteBZero"
ASSET = "Assets/Resources_moved/ModelPrefabs/RouteA/RouteBZero.prefab"
BUNDLE = "routebzero_assets_all_17d58dc0450597d1.bundle"


@pytest.fixture()
def fake_game(tmp_path: Path) -> Path:
    root = find_game_root()
    if root is None:
        pytest.skip("没找到游戏目录（BA_MOD_MAKER_GAME / 注册表都没有）")
    aa = Path(root) / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    catalog = aa / "catalog.json"
    source = aa / "PC" / BUNDLE
    if not catalog.is_file() or not source.is_file():
        pytest.skip(f"缺 {catalog} 或 {source}（先装一次我们的包）")
    fake = tmp_path / "game"
    (fake / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC").mkdir(parents=True)
    shutil.copy2(catalog, fake / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "catalog.json")
    shutil.copy2(source, fake / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / BUNDLE)
    return fake


def test_chain_resolves(fake_game: Path) -> None:
    rep = verify_registered_address(game_root=fake_game, key=KEY, asset_path=ASSET)
    assert rep.ok, rep.problems
    assert rep.bundle_name == BUNDLE
    assert len(rep.steps) == 5, rep.steps


def test_chain_flags_missing_bundle_file(fake_game: Path) -> None:
    (fake_game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / BUNDLE).unlink()
    rep = verify_registered_address(game_root=fake_game, key=KEY, asset_path=ASSET)
    assert not rep.ok
    assert any("包文件不在" in p for p in rep.problems), rep.problems


def test_chain_flags_tampered_bundle(fake_game: Path) -> None:
    path = fake_game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / BUNDLE
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF                      # 改一个字节
    path.write_bytes(bytes(data))
    rep = verify_registered_address(game_root=fake_game, key=KEY, asset_path=ASSET)
    assert not rep.ok
    assert any("m_Crc 与磁盘不符" in p for p in rep.problems), rep.problems


def test_chain_flags_asset_path_not_in_container(fake_game: Path) -> None:
    rep = verify_registered_address(
        game_root=fake_game, key=KEY,
        asset_path="Assets/Resources_moved/ModelPrefabs/RouteA/Nope.prefab")
    assert not rep.ok
    assert any("容器里没有" in p for p in rep.problems), rep.problems
