r"""`core/assets/catalog_bin.py`：二进制 catalog（Addressables 2.x BinaryStorageBuffer）读器。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_catalog_bin.py -q -n 0`
-> 收集用例数 **从 0（本文件之前不存在）变 N**，全 passed；真件那几条在装了 1.2.1 的机器上跑，
没装 / 还是 1.2.0（`catalog.json`）时**跳过**（`pytest.skip`，不是「绿」）。

| 用例 | 判据 |
|---|---|
| 判别位 | `looks_like_catalog_bin`：真件头 → True；JSON 头 → False；空串/短串/未知版本 → False |
| 负控（尺子能红） | magic 改一字节 / 不是 catalog 的文件 / 缺文件 ⇒ 都是 `AssetError` |
| 正控（真件） | magic/version/locatorId/构建哈希逐个等于实测值；键 27,764 · 条目 20,614 · 错误 0 |
| 正控（真件·包集合） | catalog 引用的 `.bundle` 集合与磁盘 `aa\PC\*.bundle` **双向相等** |
| 正控（真件·四件套） | `bundle_entries()` 的 `m_BundleSize` **逐条**等于磁盘字节 |
| 负控（键） | 不存在的键 → 查不到（⛔ 不是抛异常、也不是空表） |
| 依赖链 | 资产键给出的候选包里**包含它自己**（二进制版与 JSON 版最大的口径差别） |
| 产品入口 | `AddressablesCatalog.load(aa)` 在**只有 .bin** 的目录上给出非空索引 |

⚠ 真件那几条**只读**游戏目录（一个字节都不写）；⛔ 这些用例**不开** 3.37 GB 的大包。
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path

import pytest

from ba_mod_maker.core.assets import catalog as cat
from ba_mod_maker.core.assets.catalog_bin import (
    CATALOG_BIN_MAGIC,
    bundle_entries,
    catalog_index,
    load_catalog_bin,
    looks_like_catalog_bin,
)
from ba_mod_maker.core.errors import AssetError

GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
AA = GAME / "BrokenArrow_Data" / "StreamingAssets" / "aa"
CATALOG_BIN = AA / "catalog.bin"

#: 实测读数（1.2.1 openbeta，**2026-10-18 重记**）—— 换版本时这几个数要跟着改，**别放宽**。
#:
#: ⚠ 上一组（2026-10-13）是 `build_hash 909ab880…` / 键 22,492 / 条目 17,963。
#: 2026-10-17 11:30 Steam 更新后这份 `aa/catalog.bin` 换了（大小 2,755,558 B、
#: 出厂 sha256 `6adccfb6a2b580c7e28b48e0a26b302898ba47c49168ecab67ff9625b43a45cf`），
#: 下面这组是**新出厂件**的读数；`EXPECTED_BUNDLES` 与 `EXPECTED_MAGIC/VERSION/LOCATOR_ID`
#: 没变（换的只是内容，不是格式）。
#:
#: 重记命令（在仓根跑；**只读**游戏目录）：
#:
#:     .venv\Scripts\python.exe tools\catalog_baseline.py
#:
#: 那一行会把 `catalog_bytes / catalog_sha256 / counts / bundles / units_*` 全打出来；
#: 本次读数 = 2,755,558 B · 6adccfb6… · counts 27764 27764 20614 · 76 个包 ·
#: units 3,352,484,707 B / crc 265,969,015 / hash 43d88558…
EXPECTED_MAGIC = 0x0DE38942
EXPECTED_VERSION = 2
EXPECTED_LOCATOR_ID = "AddressablesMainContentCatalog"
EXPECTED_BUILD_HASH = "6fba597dc2bba78a85f9779a35c49902"
EXPECTED_KEYS = 27764
EXPECTED_LOCATIONS = 20614
EXPECTED_BUNDLES = 76


def _need_bin() -> Path:
    if not CATALOG_BIN.is_file():
        pytest.skip("本机没有 aa/catalog.bin（没装 1.2.1，或还是 catalog.json）")
    return CATALOG_BIN


# --------------------------------------------------------------------------- #
# 判别位（不需要游戏）
# --------------------------------------------------------------------------- #


def test_discriminator_accepts_binary_head() -> None:
    head = struct.pack("<8I", CATALOG_BIN_MAGIC, 2, 36, 179976, 1, 2, 3, 4)
    assert looks_like_catalog_bin(head) is True


def test_discriminator_rejects_json_head() -> None:
    """负控：1.2.0 那份是 JSON 明文（头是左花括号）。"""
    assert looks_like_catalog_bin(b"{") is False


def test_discriminator_rejects_short_and_empty() -> None:
    assert looks_like_catalog_bin(b"") is False
    assert looks_like_catalog_bin(b"ABCDEFGH") is False


def test_discriminator_rejects_unknown_version() -> None:
    """版本比我们认的大 ⇒ 不当成这个格式（宁可报「不认识」也不猜）。"""
    head = struct.pack("<8I", CATALOG_BIN_MAGIC, 99, 36, 0, 1, 2, 3, 4)
    assert looks_like_catalog_bin(head) is False


# --------------------------------------------------------------------------- #
# 负控：尺子必须能红（不需要游戏）
# --------------------------------------------------------------------------- #


def test_load_rejects_a_file_that_is_not_a_catalog_bin(tmp_path: Path) -> None:
    bad = tmp_path / "catalog.bin"
    bad.write_bytes(b"not a catalog at all" * 4)
    with pytest.raises(AssetError):
        load_catalog_bin(bad)


def test_load_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AssetError):
        load_catalog_bin(tmp_path / "nope.bin")


def test_load_rejects_corrupted_magic(tmp_path: Path) -> None:
    """**正控的反面**：magic 改一个字节 —— 必须报红，不许「照样读出来」。"""
    src = _need_bin()
    raw = bytearray(src.read_bytes())
    raw[0] ^= 0xFF
    broken = tmp_path / "catalog.bin"
    broken.write_bytes(bytes(raw))
    with pytest.raises(AssetError) as caught:
        load_catalog_bin(broken)
    assert "magic" in str(caught.value)


# --------------------------------------------------------------------------- #
# 正控：真件（装了 1.2.1 才跑）
# --------------------------------------------------------------------------- #


def test_real_catalog_header_readings() -> None:
    p = load_catalog_bin(_need_bin())
    assert p.magic == EXPECTED_MAGIC
    assert p.version == EXPECTED_VERSION
    assert p.locator_id == EXPECTED_LOCATOR_ID
    assert p.build_hash == EXPECTED_BUILD_HASH


def test_real_catalog_counts_and_no_errors() -> None:
    p = load_catalog_bin(_need_bin())
    assert len(p.key_data) == EXPECTED_KEYS
    assert len(p.keys) == EXPECTED_KEYS
    assert len(p.location_offsets()) == EXPECTED_LOCATIONS
    assert p.errors == []


def test_real_catalog_bundle_set_matches_the_disk() -> None:
    """**双向**相等：目录里多一个、catalog 里多一个，都要报。"""
    _need_bin()
    index = catalog_index(CATALOG_BIN)
    referenced = {name for rows in index.values() for _path, name in rows}
    on_disk = {f.name for f in (AA / "PC").glob("*.bundle")}
    assert referenced == on_disk
    assert len(referenced) == EXPECTED_BUNDLES


def test_real_catalog_bundle_sizes_match_the_disk() -> None:
    """四件套里的 `m_BundleSize` **逐条**等于磁盘字节（不是抽样）。"""
    _need_bin()
    entries = bundle_entries(CATALOG_BIN)
    assert len(entries) == EXPECTED_BUNDLES
    pc = AA / "PC"
    mismatched = [
        (name, e.bundle_size, (pc / name).stat().st_size)
        for name, e in entries.items()
        if (pc / name).is_file() and (pc / name).stat().st_size != e.bundle_size
    ]
    assert mismatched == []
    # units 包在 2026-10-17 那次更新里被**重新出过一次** ⇒ 名字里的 hash、m_Hash、体积全换了
    # （重记命令见文件顶部那组常量；旧读数 = 3_374_243_605 / ebe39db669e09a9c6c2a1d717ceb0121）。
    units = next(e for name, e in entries.items() if name.startswith("units_assets_all"))
    assert units.bundle_name == (
        "units_assets_all_43d8855830ff2f7dd366a16615c4e42e.bundle"
    )
    assert units.bundle_size == 3_352_484_707
    assert units.hash_id == "43d8855830ff2f7dd366a16615c4e42e"


def test_real_catalog_negative_control_unknown_key_is_empty() -> None:
    """负控：不存在的键 → 查不到（⛔ 不是抛异常、也不是「给了点什么」）。"""
    index = catalog_index(_need_bin())
    assert index.get("THIS_KEY_DOES_NOT_EXIST_0123456789") is None


def test_real_catalog_asset_key_reaches_a_bundle_through_dependencies() -> None:
    """资产键**靠依赖链**走到包（二进制版与 JSON 版最大的口径差别）。"""
    index = catalog_index(_need_bin())
    key = "Assets/EditorSystem/Editor2/A_MissionEditor.prefab"
    rows = index.get(key)
    if rows is None:
        pytest.skip("这份 catalog 里没有那个键（版本换了就按新键改）")
    assert rows, "资产键必须至少给出一个候选包"
    assert all(path == key for path, _bundle in rows)
    assert all(bundle.endswith(".bundle") for _path, bundle in rows)


# --------------------------------------------------------------------------- #
# 产品入口：同一个类型、两条路
# --------------------------------------------------------------------------- #


def test_addressables_catalog_load_reads_the_binary_one() -> None:
    """`AddressablesCatalog.load(aa)` 在**只有 .bin** 的目录上也要给出非空索引。"""
    _need_bin()
    loaded = cat.AddressablesCatalog.load(AA)
    assert loaded is not None
    assert loaded.key_count == EXPECTED_KEYS
    assert loaded.lookups("THIS_KEY_DOES_NOT_EXIST_0123456789") == ()


#: 一份**够 JSON 通道解析**的最小 catalog（空表也要能解析）。
_EMPTY_JSON_CATALOG = {
    "m_KeyDataString": base64.b64encode(struct.pack("<i", 0)).decode(),
    "m_BucketDataString": base64.b64encode(struct.pack("<i", 0)).decode(),
    "m_EntryDataString": base64.b64encode(struct.pack("<i", 0)).decode(),
    "m_InternalIds": [],
}


def test_addressables_catalog_prefers_json_when_both_exist(tmp_path: Path) -> None:
    """两个都在时优先 JSON（那是我们能逐字节读写的那份）。"""
    import json

    (tmp_path / cat.CATALOG_FILENAME).write_text(
        json.dumps(_EMPTY_JSON_CATALOG), encoding="utf-8"
    )
    (tmp_path / cat.CATALOG_BIN_FILENAME).write_bytes(
        struct.pack("<8I", CATALOG_BIN_MAGIC, 2, 36, 0, 1, 2, 3, 4)
    )
    loaded = cat.AddressablesCatalog.load(tmp_path)
    assert loaded is not None
    assert loaded.key_count == 0  # 走的是 JSON 那份（空表）
