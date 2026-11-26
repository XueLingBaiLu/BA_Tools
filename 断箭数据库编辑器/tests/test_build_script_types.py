r"""script_types 的**重映射**：表里我们用到的项必须指回本包的 MonoScript。

由来（2026-10-06 真机翻车）：类型表照抄真件时 script_type_index 是真件的下标，
script_types[i].local_identifier 于是指着**真件那个包**的 MonoScript pathID ——
实测本包 22 项 **0 项**命中，进机库实例化资产时崩在 UnityPlayer.dll 里
（0xC0000005 读 0x0）。见 _work/v5-scripttypes-check.py 与 _work/v5-invariants.py。
"""
from __future__ import annotations

import pytest

from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.errors import AssetError


def _entry(file_index: int, path_id: int) -> mw.ScriptTypeEntry:
    return mw.ScriptTypeEntry(file_index=file_index, local_identifier=path_id)


def test_remap_rewrites_only_named_entries() -> None:
    """点名的项换成我们的 pathID；**没点名的原样保留**（真件表里还有我们没用到的脚本）。"""
    src = (_entry(0, -11), _entry(0, -22), _entry(3, -33))
    out = builder.remap_script_types(src, {1: 0x5102_0003})
    assert [int(e.local_identifier) for e in out] == [-11, 0x5102_0003, -33]
    assert [int(e.file_index) for e in out] == [0, 0, 3]
    # 原表**不被就地改**（映射是纯函数）
    assert [int(e.local_identifier) for e in src] == [-11, -22, -33]


def test_remap_forces_file_index_zero() -> None:
    """指回本包 ⇒ file_index 必须是 0（不是 external）。"""
    out = builder.remap_script_types((_entry(5, -7),), {0: 123})
    assert int(out[0].file_index) == 0
    assert int(out[0].local_identifier) == 123


def test_remap_rejects_out_of_range_index() -> None:
    """越界的下标要**报错**：写出去就是一条 Unity 解不开的类型项。"""
    with pytest.raises(AssetError) as info:
        builder.remap_script_types((_entry(0, 1),), {2: 99})
    assert "script_types" in str(info.value)


def test_remap_with_empty_map_is_identity() -> None:
    src = (_entry(0, -11),)
    out = builder.remap_script_types(src, {})
    assert [int(e.local_identifier) for e in out] == [-11]
    assert out is not src
