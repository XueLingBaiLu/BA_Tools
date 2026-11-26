r"""W9-A：SerializedFile 元数据（**含对象表之后的尾部 = 设计稿 §9.8 U1**）读 → 写 → 逐字节相同。

**判据一行**：`pytest tests/test_metadata_writer.py -q -n 0` → 通过数**从 0 收集变 N passed**
（本文件新建前不存在）；核心那条读数是 `check_roundtrip(region).identical is True` 且
`diff_bytes == 0`。

| 用例 | 判据 | 通过线 |
|---|---|---|
| test_roundtrip_is_byte_identical | K1：读 → 写 → 与源逐字节比 | 差 **0 字节**（3 份真夹具） |
| test_parsed_fields_match_recorded | 模型字段 == 录下来的真读数 | 逐项相等 |
| test_layout_tiles_the_region | 字段表**逐行首尾相接**且与源字节对得上 | 无缝隙、无重叠 |
| test_header_roundtrip | 48 字节大端的逆运算 | 与源前 48 字节逐字节相同 |
| test_metadata_size_and_data_offset_are_derived | 这三个头字段是**推导**的，不是抄的 | 与源相等 |
| test_type_tree_nodes_resolve | 32 字节节点结构解出的字符串偏移全落在池内 | 全 True |
| test_enable_type_tree_false_branch | ett=False：无类型树、无 deps | 全 None |
| test_enable_type_tree_true_branch | ett=True：类型树 + deps；ref 带 3 个 cstr | 全非 None |
| test_script_id_rule_differs_between_tables | 两表 script_id 判据不同（114 vs sti>=0） | 正控分明 |
| test_reader_entry_reads_only_the_metadata_region | 只调 read_range 2 次 | 往返 0 差 |
| test_negative_model_edit_changes_bytes | 改一个模型字段 → 产物**必须**跟着变 | 能红 |
| test_negative_bad_source_raises | 坏源（改长度 / 截断 / 非零填充）→ 抛 AssetError | 能红 |
| test_negative_ruler_can_go_red | `diff_bytes` 对 1 位翻转必须报出来 | 能红 |
| test_negative_encoder_rejects_broken_type_tree | 模型被改坏时编码器不许静默 | 能红 |
| test_slow_real_bundles_roundtrip | 真游戏包（5 个小包）上同一判据（默认不跑） | 差 0 字节 |

## 夹具是**录下来的真字节**

出处：本机 77 个 bundle + `data.unity3d` 的 **139 个 SerializedFile 节点全扫**（逐字节相同
139/139），由

    .venv\Scripts\python.exe _work\w9a_gen_fixture.py     -> _work/w9a_fixture_block.py
    .venv\Scripts\python.exe _work\w9a_roundtrip.py       -> K1 + 负控 + 三方对拍
    .venv\Scripts\python.exe _work\w9a_roundtrip.py --scan -> 139/139 全扫

生成/复算。三份夹具各覆盖一条分支：`SMALL`（192 B，ett=False，无 ref）、
`REFS`（6,976 B，ett=False，**4 条 ref type**）、`CAMPAIGNS`（24,032 B，
ett=True，类型树 + **2 条带三个 cstr 的 ref type** —— U1 那条分支）。

## 为什么这样能抓住错

* **K1 不是回抄**：`build_metadata` 把 `metadata_size` / `data_offset` / 填充**推导**出来
  （`len(体)` / `align16(48 + len(体))` / 全零），体里多一个字节少一个字节都会变；
* **负控两条腿**：模型侧（改字段 → 产物必变）与源侧（坏源 → 必须抛），
  这两条腿各自都能红（AGENTS §0.9 动作 2：先喂一个已知违规的点进去）；
* **字段表拿源字节当尺子**：`test_layout_tiles_the_region` 不看我的函数，直接按表里的偏移
  回源字节里读 `type_count` / `ref_type_count`，并检查每行首尾相接。

## 这个环境测不了的那条

`enable_type_tree=True` 且 `is_stripped` 取 0/1 之外的值、`byte_size >= 2^31`
这两类**本机 139 个节点里一个都没有**（实测 `stripped_not_bool=0`、`byte_size 高位=0`），
所以那两条边界只由本文件的合成夹具（`test_script_id_rule_differs_between_tables`）覆盖，
**没有真件读数**。
"""

from __future__ import annotations

import base64
import os
import struct
import zlib
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.errors import AssetError

GAME = Path(os.environ.get("BA_MOD_MAKER_GAME", r"D:\Steam\steamapps\common\broken_arrow"))
AA = GAME / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC"

# --------------------------------------------------------------------------- #
# 录下来的真字节（2026-10-03；139/139 全扫里挑的三份）
# 重跑：.venv\Scripts\python.exe _work\w9a_gen_fixture.py
# --------------------------------------------------------------------------- #


# SMALL：data.unity3d::sharedassets1.assets
#   元数据区 192 B / metadata_size 138 / data_offset 192 / ett=False
#   types=1 objects=1 refs=0
SMALL_Z = (
    "eNpjYIADMQSToQtK34TSB2ASRgZGRnrGemZGacYMwiABRiCeBmL8/99aOHXr5Jgfv2u4Z5xeWVi3hhEqjQwkkdgocnxI"
    "bJ/MpKLEokr90rzMkkqFlNS0xNKcEoWi1OL80qLk1GJkbQA5JRzv"
)
SMALL_META: dict = {
    "bundle": "data.unity3d",
    "node": "sharedassets1.assets",
    "region_bytes": 192,
    "metadata_size": 138,
    "data_offset": 192,
    "padding": 6,
    "enable_type_tree": False,
    "unity_version": "2022.3.62f3",
    "target_platform": 19,
    "type_count": 1,
    "object_count": 1,
    "script_type_count": 0,
    "external_count": 1,
    "ref_type_count": 0,
    "user_information": '',
    "object_table_offset": 96,
    "type_tree_nodes": 0,
    "type0": (150, -1, False),
    "objects_head": [(1, 0, 25, 0)],
    "scripts_head": [],
    "external_paths": ['Library/unity default resources'],
    "ref_names": [],
    "ref_tt_nodes": [],
    "ref_script_ids": [],
    "layout": [
        ('unity_version', 48, 12),
        ('target_platform', 60, 4),
        ('enable_type_tree', 64, 1),
        ('type_count', 65, 4),
        ('types[]', 69, 23),
        ('object_count', 92, 4),
        ('objects[]', 96, 24),
        ('script_type_count', 120, 4),
        ('script_types[]', 124, 0),
        ('external_count', 124, 4),
        ('externals[]', 128, 53),
        ('ref_type_count', 181, 4),
        ('ref_types[]（U1）', 185, 0),
        ('userInformation', 185, 1),
        ('（体结束）', 186, 0),
        ('padding', 186, 6),
    ],
}

# REFS：data.unity3d::sharedassets0.assets
#   元数据区 6,976 B / metadata_size 6,921 / data_offset 6,976 / ett=False
#   types=27 objects=235 refs=4
REFS_Z = (
    "eNqN2HtYzdkaB/BfNxXlHg1ymmRkjGw1uhwRNsO4FqGIKUoxoXaXiYlyLYdDE7bGhDAThiGXVDKphAi5Sy4Vxq0zLo/c"
    "Lx32+35ta50/jv08nr32p9fav/Wud63fby9F+fBqoW+2Nte9mTqH8ede+IuTysnJ0dnRxWmSs9JM96d3/7TvG3V1CyJW"
    "Zi33f/4qrkFq6ZaI2dvaEHd9OuaG+mnEgW+6WBXuabsq1Id4u5/1xYWhHeNX5S4dtFhTVnSeOLzploofpp13jbpu22zU"
    "uLwz3xro2LxnPyU9Y1bb8qGZTYJtX3Y2oOiFN4bm3I/pk7noVerRcptLmcbEIx/4mR0ODfCNX77qpydar1vNiY3tZ95z"
    "exp753mTiWptaS/3a8Sjvf5KHmpbfTc7tM+QROP1lpU8nFHFnmGLGmWo+/lstvW+fq2K+GFB75cTV77QWiwe0iq397LE"
    "auKxxZ2WW6WE/J63yT5jU4qL0QC67rgW5vPWWrfxPqhZcivx9PfNHrx57PCOj91OG31o8e6NXQs3blr2ouyB5n0nFkpM"
    "dIPuSf7lJj6d+0Zku9f6X11TYfPG/ZZJ8VqN4tznwThdmJliZvOzz3/cWo6bf0G9cnhY9EPt6zUOm3ur5ue671VXeB+I"
    "0YXVV6q2V7l9Hqse9cvgiG71m/SfV362Q6Ljwb+jCtZfbFeX/XqZLqyBsibWZ133E9oYTYcOZzJWjdyldb1SqQrdYvM2"
    "uGdg3txHPXRhxkpdwehF3slhfZ2DbmStd1HnfLFj1zCnR0VBQZ6XM+xCa6doqEBGjh3Q6PbYjCJH19wpmZV9evqYzY1p"
    "37dXWvGIhgGXlbveurB6ymfxWyu3Vvvm+86ps8n0eNmtJr5jz4WmcY8u3Bx8xOb8xhBdmIHy+GXOJq+KoYWNur61C5jk"
    "3rTVTpdki4YV9/essC8Z7uel0oUZKXuiTnt6+800bd/6wh/Du6wbda5Vbta9lP7D0upPdk0PiKvWhZkr6zY8OzwgvqB4"
    "t6G69HLSlGdjBm5Y0KjaKrF29tdF4Ufe2OnCDJWBSR4ekVbjk8fnZe1erR7cPFTrNcdm+N0vW353yCDubKylLsxUuW5e"
    "sv3+tTk1doq/Nr++Z6eoVxm/qrYOKUsY0c40f5KXiy7MRIna3+N02m/tUsZdmuXo9cSyakPMj+X2tqqahe3NZryd39+i"
    "RqGhiq+RRvRuyJ99+XMJx/JHxdaY3o+z80flobEYb8Kew41U9nrsXtxIYzdFP5KbsSdzQCa7OXsvMzG+PnsVewCPqQHi"
    "+T/6s1uwp7On96axWiJ+EL2H7yRvyJ75mt7tTckbsZcbUWZfm5M3hluS77Ex0HkTjKsTZbyXEcU3ZT/lSO4xiLwZe+OZ"
    "5OmLyZvjekxoho6bkVuxJzTkmetHjk03YQR5uQl5S8y7H7k1X481e7g/+Qoe72fsZkHkYdxPK/bYyeRL6pG3Rnw4eSpf"
    "TxvkP4vcuBW5DfvmMnKPUspbW/Y7q02EvP2D3WEt+R1jclvkbSO5fwvyzzGuXHKzMnI7XH8SFWIxj6sd6mEpuQX3b496"
    "TiFXsbdHnrXkNw3Iv2APTSV3aEzeAfWwmfw4582BPWAH+UND8o743l3kzfl7v0T/e8l38RrthPWbTb6d/SvUG/toXrud"
    "kc8cck92R/TP3p29C+aLfRC7iv251E9Xdrdccld2pw/zQu7M7oz+2dXsX2PfyBWvpxu7ap94PS6Yl31ivCt7geRu2CDz"
    "RHdHPeSJefsne2qe+L3dUbdSvAf62S/mpweuc7+Yn57YV/eL1+OJPEv94EHK7U+xn97Is+R9kGfJ1dhXJe/Lbp0vej/U"
    "Z754nd+gzqX4/uwlkg9A/g+Qj2X/FuvigBg/EPmXfBDqXPLB2JcKRB+CeZF8KPYHyYdhH5bcC/MuuTfWY6How7EPF4p5"
    "G4FxSfE+GBd7f/aRmJcisZ9R6J99Ivto1H+R2L8v6oo9lO/hfsj/QfK+7GNQP+w5fG8fi/hicku+t/ujDiUfh/qXfDzq"
    "R/LvcB+RPADzInkgxiv5BORT8omof8mDsP9IHoy6PST6JNyPJA9BPUgein1M8smoT8mnYN1J/j3yL3kY8i/5VORf8mnI"
    "v+TTkX/Jw5F/ySOQf8k1yL/kkci/5FHIv+TRyP9h0WOQf8l/QP4lj0X+JZ+B/Es+E/mX/EfkX/I45F/yWci/5LORf8nj"
    "kX/JE5B/yecg/5LPRf4P6+8j7x+N5yFvR0Sfj/Gyh/Ez+AL0L/lC5LNE9ERcP/t89iQ8hxwV4xdhP5T8X9jPj4n9LEbe"
    "jonxS1AnpaL/G/Mi+VLUoeTLkIfj4vcm4/mfPZb9J8SfIE9mT0H/J8T+lyP+pBi/AvEnxfiViC8TXYt5kXwV5uWU6KmY"
    "F8l/Rj2cFn016lbyX7BOJU/Dujgj+hrUoeRrse7Oir4OdS55Otb1OfJUA/L1eI46L8ZvQL1dEH0j6k3yX1FvF0X/Dc8J"
    "kmfgeUDyTbgvl4u+Gc9Fkm/Bc90l0X/H/VfyrXgeqBB9G3uS5H/gvl8h1vN2zO9lcmv+bb0D+ZE8E/Mi+U6MS/JdqGfJ"
    "d+N62Lfxb/c9GO8V8hXsWaiTq+S+prTH7cV13uTfZSbk2RjXPfIk/q2fg/mtET0X+wm7B58B7EPdsl8xoDOAPOThb/Iq"
    "QzoD2I9+Hor9/4n5eiR6PtYjuzH3cwB5eCzGF2Bd14peiPqv1c/v+7OHIuTtCbk9X/9B/O57KvZTjHXN7sVnFYfQzzPR"
    "D2O87Co+wziCfemZ2H8J+4Dn5FF8tnEU8c/F+GOIf8F1wtdfivp5KcYfx7y/4t/jfEZyAvlk/4rPSE5iP2dvbEg1WoZ1"
    "/Ubs/xTuI2/JE/js5DTy9laMP4N9o070sx9O6kwFP4f1K/l57M8Gol9A/Ut+EfukoejlyIPkl1BXRqJXoK4kv4x6MBb9"
    "CvYB9vIGdIZ0FXXeWIy/hv25ieiVWF+SV2GfbCp6NepH8uuon2ai38C6lvwm9u3mov+FfUzyW1hHVqLfRj1Ifgf10EL0"
    "uyiHlqZCfd7DvLQU42swL+xYR5Z8blrJB3Hv29E2+vbrtvr2bFt9+6qdvq1x0LdXf9Te0FnfvvJRO1ulb9d91K510rfv"
    "OIvn0f/zCgmbPiEwLCRwavDUwGmBIcGaSMfAyMjgqEh9SMOPwgdPnqAJ1MzoEj1tctQM26DgSYHRYVG2muDI6dGaicGR"
    "urOwuncv5f+/dGEGnxZm+GlhRsqnvv4LDSF5Dg=="
)
REFS_META: dict = {
    "bundle": "data.unity3d",
    "node": "sharedassets0.assets",
    "region_bytes": 6976,
    "metadata_size": 6921,
    "data_offset": 6976,
    "padding": 7,
    "enable_type_tree": False,
    "unity_version": "2022.3.62f3",
    "target_platform": 19,
    "type_count": 27,
    "object_count": 235,
    "script_type_count": 13,
    "external_count": 2,
    "ref_type_count": 4,
    "user_information": '',
    "object_table_offset": 904,
    "type_tree_nodes": 0,
    "type0": (150, -1, False),
    "objects_head": [(1, 0, 853, 0), (2, 856, 200, 1), (3, 1056, 204, 1)],
    "scripts_head": [(1, 7391), (1, 7541), (1, 7932)],
    "external_paths": ['globalgamemanagers.assets', 'Library/unity default resources'],
    "ref_names": [(None, None, None), (None, None, None), (None, None, None), (None, None, None)],
    "ref_tt_nodes": [None, None, None, None],
    "ref_script_ids": [
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
        '00000000000000000000000000000000',
    ],
    "layout": [
        ('unity_version', 48, 12),
        ('target_platform', 60, 4),
        ('enable_type_tree', 64, 1),
        ('type_count', 65, 4),
        ('types[]', 69, 829),
        ('object_count', 898, 4),
        ('objects[]', 904, 5640),
        ('script_type_count', 6544, 4),
        ('script_types[]', 6548, 156),
        ('external_count', 6704, 4),
        ('externals[]', 6708, 100),
        ('ref_type_count', 6808, 4),
        ('ref_types[]（U1）', 6812, 156),
        ('userInformation', 6968, 1),
        ('（体结束）', 6969, 0),
        ('padding', 6969, 7),
    ],
}

# CAMPAIGNS：campaigns_assets_balticres01_b2ee7c0a71ae30b4b58bdf8086ba83b6.bundle::<nodes[0]>
#   元数据区 24,032 B / metadata_size 23,984 / data_offset 24,032 / ett=True
#   types=4 objects=316 refs=2
CAMPAIGNS_Z = (
    "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"
)
CAMPAIGNS_META: dict = {
    "bundle": "campaigns_assets_balticres01_b2ee7c0a71ae30b4b58bdf8086ba83b6.bundle",
    "node": "",
    "region_bytes": 24032,
    "metadata_size": 23984,
    "data_offset": 24032,
    "padding": 0,
    "enable_type_tree": True,
    "unity_version": "2022.3.62f3",
    "target_platform": 19,
    "type_count": 4,
    "object_count": 316,
    "script_type_count": 2,
    "external_count": 4,
    "ref_type_count": 2,
    "user_information": '',
    "object_table_offset": 14220,
    "type_tree_nodes": 376,
    "type0": (115, -1, False),
    "objects_head": [
        (-9139621707720168400, 232, 1500, 1),
        (-9131232138811485817, 1736, 2372, 1),
        (-9087121283714586435, 4112, 2328, 1),
    ],
    "scripts_head": [(0, -5099617712289999765), (0, 4335270729630807708)],
    "external_paths": [
        'archive:/CAB-6c8e3b2813cd18f9f8221ec306c61c90/CAB-6c8e3b2813cd18f9f8221ec306c61c90',
        'archive:/CAB-30730f9a3d46f3ac22fbc7f8e0e321b3/CAB-30730f9a3d46f3ac22fbc7f8e0e321b3',
        'archive:/CAB-286cc3017c8d7cfdc3e0f6e597519058/CAB-286cc3017c8d7cfdc3e0f6e597519058',
        'archive:/CAB-0b02653fb3c73fa2fa137429df832074/CAB-0b02653fb3c73fa2fa137429df832074',
    ],
    "ref_names": [
        ('Cinematic', 'BrokenArrow.Client.Ecs.Campaign.Data', 'BrokenArrow'),
        ('SceneTransitionDecorConfig/Hint', 'BrokenArrow.Client.Ecs.UI', 'BrokenArrow'),
    ],
    "ref_tt_nodes": [31, 9],
    "ref_script_ids": ['00000000000000000000000000000000', '00000000000000000000000000000000'],
    "layout": [
        ('unity_version', 48, 12),
        ('target_platform', 60, 4),
        ('enable_type_tree', 64, 1),
        ('type_count', 65, 4),
        ('types[]', 69, 14146),
        ('object_count', 14215, 4),
        ('objects[]', 14220, 7584),
        ('script_type_count', 21804, 4),
        ('script_types[]', 21808, 24),
        ('external_count', 21832, 4),
        ('externals[]', 21836, 416),
        ('ref_type_count', 22252, 4),
        ('ref_types[]（U1）', 22256, 1775),
        ('userInformation', 24031, 1),
        ('（体结束）', 24032, 0),
        ('padding', 24032, 0),
    ],
}


def _region(packed: str) -> bytes:
    """夹具里的字节是 zlib + base64（元数据区重复模式多，压完小一个数量级）。"""
    return zlib.decompress(base64.b64decode(packed))


CASES = [
    ("SMALL", SMALL_Z, SMALL_META),
    ("REFS", REFS_Z, REFS_META),
    ("CAMPAIGNS", CAMPAIGNS_Z, CAMPAIGNS_META),
]
CASE_IDS = [case[0] for case in CASES]


def _row(meta: mw.SerializedMetadata, name: str) -> tuple[int, int]:
    """字段表里某一行的 (偏移, 宽度)。"""
    for row in mw.layout(meta).rows:
        if row["name"] == name:
            return int(row["offset"]), int(row["size"])
    raise AssertionError(f"字段表里没有 {name!r}")


# --------------------------------------------------------------------------- K1


@pytest.mark.parametrize(("tag", "packed", "expected"), CASES, ids=CASE_IDS)
def test_roundtrip_is_byte_identical(tag: str, packed: str, expected: dict) -> None:
    """K1：读 → 写 → 与源**逐字节相同**（差 0 字节）。"""
    region = _region(packed)
    assert len(region) == expected["region_bytes"], tag
    report = mw.check_roundtrip(region, keep_built=True)
    assert report.identical, f"{tag}: {report}"
    assert report.diff_bytes == 0, tag
    assert report.first_diff is None, tag
    assert report.source_len == report.built_len == expected["region_bytes"], tag
    assert report.built == region, tag
    assert report.padding_size == expected["padding"], tag


@pytest.mark.parametrize(("tag", "packed", "expected"), CASES, ids=CASE_IDS)
def test_parsed_fields_match_recorded(tag: str, packed: str, expected: dict) -> None:
    """模型字段逐项等于**录下来的真读数**。"""
    meta = mw.parse_serialized_metadata(_region(packed))
    assert meta.header.metadata_size == expected["metadata_size"], tag
    assert meta.header.data_offset == expected["data_offset"], tag
    assert meta.header.version == 22, tag
    assert meta.header.endian_flag == 0, tag
    assert meta.unity_version == expected["unity_version"], tag
    assert meta.target_platform == expected["target_platform"], tag
    assert meta.enable_type_tree is expected["enable_type_tree"], tag
    assert len(meta.types) == expected["type_count"], tag
    assert len(meta.objects) == expected["object_count"], tag
    assert len(meta.script_types) == expected["script_type_count"], tag
    assert len(meta.externals) == expected["external_count"], tag
    assert len(meta.ref_types) == expected["ref_type_count"], tag
    assert meta.user_information == expected["user_information"], tag
    assert len(meta.padding) == expected["padding"], tag
    assert meta.padding == b"\x00" * expected["padding"], tag
    assert meta.object_table_offset == expected["object_table_offset"], tag
    head_objects = [(o.path_id, o.byte_start, o.byte_size, o.type_id) for o in meta.objects[:3]]
    assert head_objects == expected["objects_head"], tag
    head_scripts = [(s.file_index, s.local_identifier) for s in meta.script_types[:3]]
    assert head_scripts == expected["scripts_head"], tag
    assert [e.path for e in meta.externals] == expected["external_paths"], tag
    names = [(t.class_name, t.namespace, t.assembly_name) for t in meta.ref_types]
    assert names == expected["ref_names"], tag
    tt_nodes = [t.type_tree.node_count if t.type_tree else None for t in meta.ref_types]
    assert tt_nodes == expected["ref_tt_nodes"], tag
    sids = [None if t.script_id is None else t.script_id.hex() for t in meta.ref_types]
    assert sids == expected["ref_script_ids"], tag
    tree_nodes = sum(t.type_tree.node_count for t in meta.types if t.type_tree)
    assert tree_nodes == expected["type_tree_nodes"], tag
    if expected["type0"] is not None:
        class_id, script_type_index, has_script_id = expected["type0"]
        first = meta.types[0]
        got = (first.class_id, first.script_type_index, first.script_id is not None)
        assert got == (class_id, script_type_index, has_script_id), tag


@pytest.mark.parametrize(("tag", "packed", "expected"), CASES, ids=CASE_IDS)
def test_layout_tiles_the_region(tag: str, packed: str, expected: dict) -> None:
    """字段表：① 逐行首尾相接 ② 按表里的偏移回**源字节**读得到同样的数 ③ 等于录下来的表。"""
    region = _region(packed)
    meta = mw.parse_serialized_metadata(region)
    endian = meta.header.endian
    rows = mw.layout(meta).rows

    assert rows[0]["offset"] == mw.HEADER_SIZE, tag
    for prev, cur in zip(rows[:-1], rows[1:], strict=True):
        end = prev["offset"] + prev["size"]
        if prev["name"] == "object_count":
            # 对象表**每项开头 align4** ⇒ object_count 与第一项之间最多 3 字节填充
            assert cur["offset"] == mw.align_up(end, 4), (tag, prev, cur)
            assert 0 <= cur["offset"] - end <= 3, (tag, prev, cur)
        else:
            assert cur["offset"] == end, (tag, prev, cur)
    last = rows[-1]
    assert last["offset"] + last["size"] == meta.header.data_offset, tag

    for name, value in (
        ("type_count", len(meta.types)),
        ("object_count", len(meta.objects)),
        ("script_type_count", len(meta.script_types)),
        ("external_count", len(meta.externals)),
        ("ref_type_count", len(meta.ref_types)),
    ):
        offset, size = _row(meta, name)
        assert size == 4, (tag, name)
        assert struct.unpack_from(endian + "i", region, offset)[0] == value, (tag, name)

    offset, size = _row(meta, "userInformation")
    assert size == len(mw.encode_cstr(meta.user_information)), (tag, size)
    assert region[offset : offset + size] == mw.encode_cstr(meta.user_information), tag

    offset, size = _row(meta, "padding")
    assert offset == mw.HEADER_SIZE + meta.header.metadata_size, tag
    assert region[offset : offset + size] == b"\x00" * size, tag

    assert [(r["name"], r["offset"], r["size"]) for r in rows] == expected["layout"], tag


@pytest.mark.parametrize(("tag", "packed", "expected"), CASES, ids=CASE_IDS)
def test_header_roundtrip(tag: str, packed: str, expected: dict) -> None:
    """48 字节大端头的逆运算：逐字节相同。"""
    del expected
    region = _region(packed)
    header = mw.parse_header(region)
    assert mw.encode_header(header) == region[: mw.HEADER_SIZE], tag
    assert mw.parse_header(mw.encode_header(header)) == header, tag
    assert header.body_end == mw.HEADER_SIZE + header.metadata_size, tag
    assert header.derived_data_offset == header.data_offset, tag
    assert header.padding_size == mw.align_up(header.body_end, mw.DATA_ALIGN) - header.body_end, tag


@pytest.mark.parametrize(("tag", "packed", "expected"), CASES, ids=CASE_IDS)
def test_metadata_size_and_data_offset_are_derived(tag: str, packed: str, expected: dict) -> None:
    """体、metadata_size、data_offset、填充都是**推导**出来的 —— 与源相等才算对。"""
    del expected
    region = _region(packed)
    meta = mw.parse_serialized_metadata(region)
    body = mw.encode_metadata_body(meta)
    assert len(body) == meta.header.metadata_size, tag
    assert body == region[mw.HEADER_SIZE : mw.HEADER_SIZE + len(body)], tag
    built = mw.build_metadata(meta)
    built_header = mw.parse_header(built)
    assert built_header.metadata_size == len(body), tag
    assert built_header.data_offset == mw.align_up(mw.HEADER_SIZE + len(body), mw.DATA_ALIGN), tag
    assert built_header.data_offset == meta.header.data_offset, tag
    assert built == region[: meta.header.data_offset], tag


# --------------------------------------------------------------------------- 分支


def test_type_tree_nodes_resolve() -> None:
    """类型树的 32 字节节点结构：解出的字符串偏移必须全部落在池内。"""
    meta = mw.parse_serialized_metadata(_region(CAMPAIGNS_Z))
    trees = [t.type_tree for t in meta.types if t.type_tree is not None]
    assert trees, "这份夹具必须带类型树"
    assert sum(t.node_count for t in trees) == CAMPAIGNS_META["type_tree_nodes"]
    names: list[str] = []
    for tree in trees:
        assert mw.type_tree_string_offsets_ok(tree, meta.header.endian)
        nodes = list(mw.iter_type_tree_nodes(tree, meta.header.endian))
        assert len(nodes) == tree.node_count
        assert nodes[0].level == 0
        names.extend(node.type_name for node in nodes)
    assert all(names)
    assert any(not name.startswith("#") for name in names), "字符串池那条路没走通"
    assert any(name.startswith("#common(") for name in names), "公共串表那条路没走通"
    assert [n for n in mw.iter_type_tree_nodes(trees[0], meta.header.endian)][0].name


@pytest.mark.parametrize(("tag", "packed", "expected"), CASES[:2], ids=CASE_IDS[:2])
def test_enable_type_tree_false_branch(tag: str, packed: str, expected: dict) -> None:
    """ett=False（本机 62 个节点是这样）：没有类型树、没有 deps、ref type 没有三 cstr。"""
    meta = mw.parse_serialized_metadata(_region(packed))
    assert meta.enable_type_tree is False, tag
    assert meta.types, tag
    for item in [*meta.types, *meta.ref_types]:
        assert item.type_tree is None, tag
        assert item.type_dependencies is None, tag
    for item in meta.ref_types:
        assert item.class_name is None and item.namespace is None, tag
        assert item.assembly_name is None, tag
    assert len(meta.ref_types) == expected["ref_type_count"], tag


def test_enable_type_tree_true_branch() -> None:
    """ett=True：类型表带类型树 + deps；**ref type 带三个 cstr**（U1 那条分支）。"""
    meta = mw.parse_serialized_metadata(_region(CAMPAIGNS_Z))
    assert meta.enable_type_tree is True
    assert meta.types and all(t.type_tree is not None for t in meta.types)
    assert all(t.type_dependencies is not None for t in meta.types)
    assert all(t.class_name is None for t in meta.types), "类型表**没有**三 cstr，别读串了"
    assert meta.ref_types and all(t.type_tree is not None for t in meta.ref_types)
    assert all(t.type_dependencies is None for t in meta.ref_types), (
        "ref type 读的是三 cstr，不是 deps"
    )
    names = [(t.class_name, t.namespace, t.assembly_name) for t in meta.ref_types]
    assert ("Cinematic", "BrokenArrow.Client.Ecs.Campaign.Data", "BrokenArrow") in names
    assert all(t.script_id is not None for t in meta.ref_types)


def _handmade_metadata() -> bytes:
    """手搓一份最小元数据（**不经本模块的写出侧**），用来分辨两张表的 script_id 判据。

    手算（逐段相加）：体 = 12 + 4 + 1 + 4 + (4+1+2+16) + (4+1+2+16+16) + 4 + 4 + 4 + 4
    + (4+1+2+16+16) + (4+1+2+16) + 1 = **162**；48 + 162 = 210 → align16 = **224**（填充 14）。
    """
    body = bytearray()
    body += mw.encode_cstr("2022.3.62f3")          # 12
    body += struct.pack("<i", 19)                  # target_platform
    body += b"\x00"                                # enable_type_tree = False
    body += struct.pack("<i", 2)                   # type_count
    body += struct.pack("<i", 28) + b"\x00" + struct.pack("<h", 3) + b"\x22" * 16
    body += struct.pack("<i", 114) + b"\x01" + struct.pack("<h", -1) + b"\x11" * 16 + b"\x33" * 16
    body += struct.pack("<i", 0)                   # object_count
    body += struct.pack("<i", 0)                   # script_type_count
    body += struct.pack("<i", 0)                   # external_count
    body += struct.pack("<i", 2)                   # ref_type_count
    body += struct.pack("<i", -1) + b"\x00" + struct.pack("<h", 5) + b"\x44" * 16 + b"\x55" * 16
    body += struct.pack("<i", -1) + b"\x00" + struct.pack("<h", -1) + b"\x66" * 16
    body += mw.encode_cstr("")                     # userInformation
    assert len(body) == 162
    header = mw.SerializedFileHeader(
        legacy_metadata_size=0,
        legacy_file_size=0,
        version=22,
        legacy_data_offset=0,
        endian_flag=0,
        reserved=b"\x00\x00\x00",
        metadata_size=len(body),
        file_size=0,
        data_offset=mw.align_up(mw.HEADER_SIZE + len(body), mw.DATA_ALIGN),
        unknown=0,
    )
    assert header.data_offset == 224
    return mw.encode_header(header) + bytes(body) + b"\x00" * (header.data_offset - 48 - len(body))


def test_script_id_rule_differs_between_tables() -> None:
    """两张表的 script_id 判据**不同**：类型表看 `class_id == 114`，
    ref type 看 `script_type_index >= 0`。

    这份手搓件同时喂了两个反例：类型表里 `class_id=28 且 sti=3`（**不**读 16 字节），
    ref type 里 `class_id=-1 且 sti=-1`（**不**读 16 字节）。按 ufs.py 那条
    `sti >= 0` 的写法去读类型表，游标会多走 16 字节、当场落到声明的末尾之外。
    """
    blob = _handmade_metadata()
    assert len(blob) == 224
    report = mw.check_roundtrip(blob)
    assert report.identical, report
    meta = mw.parse_serialized_metadata(blob)
    assert meta.enable_type_tree is False
    assert [t.class_id for t in meta.types] == [28, 114]
    assert [t.script_type_index for t in meta.types] == [3, -1]
    assert meta.types[0].script_id is None, "class_id != 114 ⇒ 不读 script_id（哪怕 sti >= 0）"
    assert meta.types[1].script_id == b"\x11" * 16
    assert meta.types[1].is_stripped is True
    assert meta.types[0].old_type_hash == b"\x22" * 16
    assert [t.script_type_index for t in meta.ref_types] == [5, -1]
    assert meta.ref_types[0].script_id == b"\x44" * 16
    assert meta.ref_types[1].script_id is None, "sti < 0 ⇒ ref type 不读 script_id"


def test_reader_entry_reads_only_the_metadata_region() -> None:
    """`read_serialized_metadata(reader, node)`：只调 read_range 两次，且只吃元数据区。"""

    class _Node:
        offset = 0
        size = 0

    class _Reader:
        def __init__(self, blob: bytes) -> None:
            self.blob = blob
            self.calls: list[tuple[int, int]] = []

        def read_range(self, offset: int, size: int) -> bytes:
            self.calls.append((offset, size))
            return self.blob[offset : offset + size]

    region = _region(REFS_Z)
    reader = _Reader(region)
    meta = mw.read_serialized_metadata(reader, _Node())
    assert reader.calls == [(0, mw.HEADER_SIZE), (0, len(region))]
    assert len(meta.objects) == REFS_META["object_count"]
    assert mw.build_metadata(meta) == region


# --------------------------------------------------------------------------- 负控


def _flip_first(data: bytes) -> bytes:
    return bytes([data[0] ^ 1]) + data[1:]


def _m_obj_byte_size(m) -> None:
    m.objects[0].byte_size += 1


def _m_obj_path_id(m) -> None:
    m.objects[0].path_id ^= 1


def _m_obj_last_type_id(m) -> None:
    m.objects[-1].type_id += 1


def _m_script_local_id(m) -> None:
    m.script_types[0].local_identifier ^= 1


def _m_external_path(m) -> None:
    m.externals[0].path += "x"


def _m_user_information(m) -> None:
    m.user_information = "X"


def _m_target_platform(m) -> None:
    m.target_platform += 1


def _m_header_file_size(m) -> None:
    m.header.file_size += 1


def _m_type_hash(m) -> None:
    m.types[0].old_type_hash = _flip_first(m.types[0].old_type_hash)


def _m_type_class_id(m) -> None:
    m.types[0].class_id += 1


def _m_type_tree_nodes(m) -> None:
    m.types[0].type_tree.nodes = _flip_first(m.types[0].type_tree.nodes)


def _m_ref_class_name(m) -> None:
    m.ref_types[0].class_name = (m.ref_types[0].class_name or "") + "X"


def _m_ref_script_id(m) -> None:
    m.ref_types[0].script_id = _flip_first(m.ref_types[0].script_id)


def _m_ref_class_id(m) -> None:
    m.ref_types[0].class_id += 1


NEGATIVE_MUTATIONS = [
    ("objects[0].byte_size += 1", _m_obj_byte_size),
    ("objects[0].path_id ^= 1", _m_obj_path_id),
    ("objects[-1].type_id += 1", _m_obj_last_type_id),
    ("script_types[0].local_identifier ^= 1", _m_script_local_id),
    ("externals[0].path += 'x'", _m_external_path),
    ("user_information = 'X'", _m_user_information),
    ("target_platform += 1", _m_target_platform),
    ("header.file_size += 1", _m_header_file_size),
    ("types[0].old_type_hash 翻一位", _m_type_hash),
    ("types[0].class_id += 1", _m_type_class_id),
    ("types[0].type_tree.nodes 翻一位", _m_type_tree_nodes),
    ("ref_types[0].class_name += 'X'", _m_ref_class_name),
    ("ref_types[0].script_id 翻一位", _m_ref_script_id),
    ("ref_types[0].class_id += 1", _m_ref_class_id),
]
MUTATION_IDS = [name for name, _ in NEGATIVE_MUTATIONS]


@pytest.mark.parametrize(("name", "mutate"), NEGATIVE_MUTATIONS, ids=MUTATION_IDS)
def test_negative_model_edit_changes_bytes(name: str, mutate) -> None:
    """改一个模型字段，产物**必须**跟着变 —— 变了才证明写出侧不是回抄源字节。"""
    region = _region(CAMPAIGNS_Z)
    meta = mw.parse_serialized_metadata(region)
    mutate(meta)
    built = mw.build_metadata(meta)
    first, count = mw.diff_bytes(region[: meta.header.data_offset], built)
    assert count > 0, f"{name}：模型改了但产物一字未变 ⇒ 写出侧在回抄源字节"
    assert first is not None


def _corruptions(region: bytes, tag: str) -> list[tuple[str, bytes]]:
    header = mw.parse_header(region)
    plus8 = bytearray(region)
    struct.pack_into(">I", plus8, 20, header.metadata_size + 8)
    minus1 = bytearray(region)
    struct.pack_into(">I", minus1, 20, header.metadata_size - 1)
    doff = bytearray(region)
    struct.pack_into(">q", doff, 32, header.data_offset + 16)
    out = [
        (f"{tag}:metadata_size += 8", bytes(plus8)),
        (f"{tag}:metadata_size -= 1", bytes(minus1)),
        (f"{tag}:data_offset += 16", bytes(doff)),
        (f"{tag}:截到体结束 - 1", region[: header.body_end - 1]),
        (f"{tag}:截到 data_offset - 1", region[: header.data_offset - 1]),
        (f"{tag}:版本改成 21", bytes(region[:8] + struct.pack(">I", 21) + region[12:])),
    ]
    if header.padding_size > 0:
        bad = bytearray(region)
        bad[header.data_offset - 1] = 0xFF
        out.append((f"{tag}:填充区末字节改 0xFF", bytes(bad)))
    return out


NEGATIVE_SOURCES = [
    *_corruptions(_region(CAMPAIGNS_Z), "CAMPAIGNS"),
    *_corruptions(_region(SMALL_Z), "SMALL"),
]
SOURCE_IDS = [name for name, _ in NEGATIVE_SOURCES]


@pytest.mark.parametrize(("name", "bad"), NEGATIVE_SOURCES, ids=SOURCE_IDS)
def test_negative_bad_source_raises(name: str, bad: bytes) -> None:
    """坏源必须**抛** AssetError（不许静默容忍、不许写回一个坏包）。"""
    with pytest.raises(AssetError):
        mw.parse_serialized_metadata(bad)


def test_negative_ruler_can_go_red() -> None:
    """`diff_bytes` 这把尺子对 1 位翻转、对长度差都必须报出来（不能恒绿）。"""
    region = _region(SMALL_Z)
    mutant = bytearray(region)
    mutant[100] ^= 0x01
    first, count = mw.diff_bytes(region, bytes(mutant))
    assert (first, count) == (100, 1)
    first, count = mw.diff_bytes(region, region[:-1])
    assert (first, count) == (len(region) - 1, 1)
    assert mw.diff_bytes(region, region) == (None, 0)


def test_negative_encoder_rejects_broken_type_tree() -> None:
    """模型被改坏时编码器不许静默（长度与声明不符就抛）。"""
    meta = mw.parse_serialized_metadata(_region(CAMPAIGNS_Z))
    tree = next(t.type_tree for t in meta.types if t.type_tree is not None)
    good = tree.nodes
    tree.nodes = good[:-1]
    with pytest.raises(AssetError):
        mw.encode_type_tree(tree, meta.header.endian)
    tree.nodes = good
    expected = (
        struct.pack(meta.header.endian + "ii", tree.node_count, tree.string_buffer_size)
        + tree.nodes
        + tree.strings
    )
    assert mw.encode_type_tree(tree, meta.header.endian) == expected
    reserved = meta.header.reserved
    meta.header.reserved = b"\x00\x00"
    with pytest.raises(AssetError):
        mw.encode_header(meta.header)
    meta.header.reserved = reserved


# --------------------------------------------------------------------------- 真游戏包（默认不跑）


SMALL_BUNDLE_PATTERNS = [
    "sfx_assets_all_*.bundle",
    "campaigns_assets_balticres01_*.bundle",
    "ammoicons_assets_all_*.bundle",
    "lightflashes_assets_all_*.bundle",
    "mapsettings_assets_mapsettingstest_map_*.bundle",
]


@pytest.mark.slow
def test_slow_real_bundles_roundtrip() -> None:
    """真游戏包（5 个小包）上跑同一条 K1 —— 只读元数据区，不碰对象数据。

    `pytest tests/test_metadata_writer.py -q -n 0 -m slow` 才会跑（默认档排除）。
    units（3.38 GB）不在用例里：它要走 `_work/w3_heavy.lock` 串行，
    读数由 `_work/w9a_roundtrip.py` 出（4,129,040 B 元数据区 / 差 0 字节）。
    """
    from ba_mod_maker.core.asset import ufs

    if not AA.is_dir():
        pytest.skip(f"没装游戏：{AA}")
    checked = 0
    for pattern in SMALL_BUNDLE_PATTERNS:
        paths = sorted(AA.glob(pattern))
        assert paths, pattern
        with ufs.UfsReader(paths[0]) as reader:
            for node in reader.nodes:
                if node.path.endswith((".resS", ".resource")):
                    continue
                report = mw.check_roundtrip(mw.read_metadata_region(reader, node))
                assert report.identical, (paths[0].name, node.path, report)
                checked += 1
    assert checked == len(SMALL_BUNDLE_PATTERNS)
