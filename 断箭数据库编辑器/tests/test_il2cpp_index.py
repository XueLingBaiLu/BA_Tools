"""il2cpp 方法索引（metadata v39）的测试。

快档：纯函数边界 + 坏元数据拒绝（负控）。
慢档（真机）：索引在**现盘 DLL** 上必须与已验收的 BUILD_1119 读数逐字对上 ——
这是「解析器没读错」的正控（全量 140,619/140,619 对 dumper 的对照在
_work_db/patch1119/compare_index.py，这里钉补丁关心的 8 个方法）。
"""

from __future__ import annotations

import struct

import pytest

from ba_mod_maker.core.asset import dllpatch
from ba_mod_maker.core.asset.il2cpp_index import (
    TARGET_METHODS,
    Il2CppIndex,
    UnsupportedMetadataError,
    _idx_size,
)


def test_idx_size_boundaries() -> None:
    assert _idx_size(0) == 1
    assert _idx_size(0xFF) == 1
    assert _idx_size(0x100) == 2
    assert _idx_size(0xFFFF) == 2
    assert _idx_size(0x10000) == 4


def test_bad_sanity_is_refused(tmp_path) -> None:
    md = tmp_path / "global-metadata.dat"
    md.write_bytes(b"\x00" * 64)
    dll = tmp_path / "GameAssembly.dll"
    dll.write_bytes(b"MZ" + b"\x00" * 512)
    with pytest.raises(UnsupportedMetadataError):
        Il2CppIndex(dll, md)


def test_unsupported_version_is_refused(tmp_path) -> None:
    """v27（旧布局）⇒ 拒绝，⛔ 不拿 v39 的尺子量旧版。"""
    md = tmp_path / "global-metadata.dat"
    md.write_bytes(struct.pack("<Ii", 0xFAB11BAF, 27) + b"\x00" * 512)
    dll = tmp_path / "GameAssembly.dll"
    dll.write_bytes(b"MZ" + b"\x00" * 512)
    with pytest.raises(UnsupportedMetadataError):
        Il2CppIndex(dll, md)


@pytest.mark.slow
def test_real_index_matches_build_1119() -> None:
    from ba_mod_maker.core.assets import game as game_mod

    root = game_mod.find_game_root_for_db()
    if root is None:
        pytest.skip("这台机器上没有游戏")
    dll = root / dllpatch.DLL_NAME
    md = root / "BrokenArrow_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    if not dll.is_file() or not md.is_file():
        pytest.skip("DLL 或元数据不在")
    if dllpatch._sha256_file(dll) != dllpatch.BUILD_1119.orig_sha256:
        pytest.skip("现盘 DLL 不是 1119 原版（别的版本没有已验收读数可对）")
    idx = Il2CppIndex(dll, md)
    assert len(idx.starts) > 100_000
    # 8 个补丁相关方法：唯一命中 + 与验收读数逐字一致
    known = {
        "load": 0x180291BA0,
        "load_compiled": 0x180291A30,
        "load_by_json": 0x1802918E0,
        "reload": 0x180291F00,
        "load_source": 0x180290540,
        "ctor": 0x18027C230,
        "file_exists": 0x18421ABB0,
        "on_incoming_message": 0x18062D1B0,
    }
    for key, (t, m, pc) in TARGET_METHODS.items():
        assert idx.find(t, m, pc) == [known[key]], key