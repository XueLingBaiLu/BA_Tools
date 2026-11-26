"""自动重定位引擎（autoport）的测试。

快档：坏输入拒绝（负控）。
慢档（真机）：**盲复现** —— 引擎在现盘 1119 原版上必须重新推出与 BUILD_1119
逐字段相同的表（19 条尺子全 PASS）。这是引擎可信度的核心正控：
它不是「看着答案抄」，derive 只吃 DLL + 元数据两个文件。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.asset import autoport, dllpatch
from ba_mod_maker.core.asset.il2cpp_index import UnsupportedMetadataError


def test_derive_refuses_garbage(tmp_path) -> None:
    md = tmp_path / "global-metadata.dat"
    md.write_bytes(b"\x00" * 64)
    dll = tmp_path / "GameAssembly.dll"
    dll.write_bytes(b"MZ" + b"\x00" * 512)
    with pytest.raises((autoport.AutoportError, UnsupportedMetadataError)):
        autoport.derive(dll, md)


@pytest.mark.slow
def test_derive_blind_reproduces_build_1119() -> None:
    from ba_mod_maker.core.assets import game as game_mod

    root = game_mod.find_game_root_for_db()
    if root is None:
        pytest.skip("这台机器上没有游戏")
    dll = root / dllpatch.DLL_NAME
    md = root / "BrokenArrow_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    if not dll.is_file() or not md.is_file():
        pytest.skip("DLL 或元数据不在")
    if dllpatch._sha256_file(dll) != dllpatch.BUILD_1119.orig_sha256:
        pytest.skip("现盘 DLL 不是 1119 原版")
    build, rep = autoport.derive(dll, md)
    passes = [line for line in rep if line.startswith("PASS")]
    assert len(passes) >= 19, rep
    assert not [line for line in rep if line.startswith("FAIL")]
    want = dllpatch.BUILD_1119
    for field in (
        "base", "init_meta", "file_exists", "object_new", "ctor", "load_source",
        "string_new", "gchandle_new", "slot_klass", "cave_len", "orig_sha256",
    ):
        assert getattr(build, field) == getattr(want, field), field
    assert build.sites == want.sites
    assert build.byte_patches == want.byte_patches