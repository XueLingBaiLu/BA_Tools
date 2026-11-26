r"""W8-B 判据：克隆引用图（4 个新对象）+ catalog 地址登记（六件套 + 三个计数头）。

**判据一行**：pytest tests/test_clone_object.py -q -> 通过数**从 0 收集变 N passed**
（本文件新建前不存在）。

| 用例 | 判据（工单 §3 / task-8 的验收面） | 通过线 |
|---|---|---|
| test_type_tree_* | 类型表 classID -> type_id 与真件一致；公共串表不给就抛 | 相等 + 能红 |
| test_roundtrip_* | 自己造的字节 -> 类型树读回 -> 字段逐个相等（§9.5 步骤 2） | 相等 |
| test_serializer_matches_unitypy_* | 与 UnityPy 的 write/read **逐字节/逐字段**对拍 | 一致 |
| test_clone_* | 四个新对象的 classID/typeID、m_Component 顺序、m_Father、m_Children +1 | 全对 |
| test_refs_* | 无悬空 PPtr（判据 J4）+ 负控（m_Mesh 指不存在的 pathID 必须红） | 能红 |
| test_catalog_* | 合成 catalog 追加后用产品读侧（core/assets/catalog.py）读回来核对 | 相等 |
| test_pit1..4_* | 四个坑各一条负控（字节长 / 偏移连锁 / 抄模板元组 / entry-primaryKey） | 都能红 |

## 夹具是**录下来的真字节**（不是手搓的假 schema）

出处：2026-10-03 units 包（3,381,248,409 B）+ 本机 catalog.json，由

    .venv\Scripts\python.exe _work\w8_clone_probe_schema.py   -> blobs / samples
    .venv\Scripts\python.exe _work\w8_clone_gen_fixture.py    -> 本文件下面那段常量

生成。判据 5 要的是「类型树当 schema」的往返，必须在**真类型树**上量；而游戏包
3.38 GB（峰值约 10 GB）不能在每个用例里开。录下来的一段 blob 就是同一个 schema：
TYPE_BLOBS 是四个类内嵌类型树的**原始字节**，SAMPLES 是 lod_1x1x1 那四个真对象的**原始字节**。

## 为什么这样能抓住错

* 正控 1（真字节往返）：拿真对象的字节读一遍再写一遍，必须**逐字节相同**。
* 正控 2（跨实现对拍）：同一棵类型树 + 同一份字段树，我的序列化器与 UnityPy 的
  write_typetree 必须给出**同一串字节**；再用 UnityPy 读我的字节，必须等于我的字段树。
  两条都是**不同实现**，所以不是自证。
* 负控：每条判据都喂一个已知违规的点，读数必须变红（AGENTS §0.9 动作 2）。
"""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.core.assets.catalog import AddressablesCatalog

# --------------------------------------------------------------------------- #
# 夹具（下面那段由 _work/w8_clone_gen_fixture.py 生成，整段原样抄进来）
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------
# 录下来的真字节（2026-10-03，units 包 / 本机 catalog.json）
# 重跑：.venv\Scripts\python.exe _work\w8_clone_probe_schema.py
#       .venv\Scripts\python.exe _work\w8_clone_gen_fixture.py
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 录下来的真字节（2026-10-03，units 包 / 本机 catalog.json）
# 重跑：.venv\Scripts\python.exe _work\w8_clone_probe_schema.py
#       .venv\Scripts\python.exe _work\w8_clone_gen_fixture.py
# --------------------------------------------------------------------------
TYPE_BLOBS: dict[str, str] = {
    "1": (  # GameObject 567 B
        "DwAAAE8AAAAGAAAArAAAgDcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQDVAwCAAAAAAP////8BAAAAQYAAEAAA"
        "AAAAAAAAAQACATEAAIAxAACA/////wIAAABBQAAQAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAAwAAAEEAABAAAAAA"
        "AAAAAAEAAwAMAAAAagAAgAwAAAAEAAAAQQAAEAAAAAAAAAAAAQAEACQCAIAaAAAADAAAAAUAAABBAAAQAAAAAAAA"
        "AAABAAUA3gAAgCQAAAAEAAAABgAAAEEAABAAAAAAAAAAAAEABQAuAwCALQAAAAgAAAAHAAAAQQAAEAAAAAAAAAAA"
        "AQABAKYDAIA2AAAABAAAAAgAAAAAAAAAAAAAAAAAAAABAAEASAMAgKsBAID/////CQAAAACAAAAAAAAAAAAAAAEA"
        "AgExAACAMQAAgP////8KAAAAAUAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAAAsAAAABAAAAAAAAAAAAAAABAAMA"
        "UQAAgGoAAIABAAAADAAAAAEAAAAAAAAAAAAAAAEAAQCLAwCAPgAAAAIAAAANAAAAAAAAAAAAAAAAAAAAAQABAEwA"
        "AIBEAAAAAQAAAA4AAAAAAAAAAAAAAAAAAABtX0NvbXBvbmVudABDb21wb25lbnRQYWlyAGNvbXBvbmVudABtX0Zp"
        "bGVJRABtX1BhdGhJRABtX0xheWVyAG1fVGFnAG1fSXNBY3RpdmUA"
    ),
    "4": (  # Transform 931 B
        "GgAAAFsAAAACAAAAdAMAgDcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQA0AgCAdgEAgAwAAAABAAAAQQAAAAAA"
        "AAAAAAAAAQACAN4AAIAAAAAABAAAAAIAAABBAAAAAAAAAAAAAAABAAIALgMAgAkAAAAIAAAAAwAAAEEAAAAAAAAA"
        "AAAAAAEAAQDlAgCAEgAAABAAAAAEAAAAAAAgAAAAAAAAAAAAAQACAKEAAIAiAAAABAAAAAUAAAAAACAAAAAAAAAA"
        "AAABAAIAoQAAgCQAAAAEAAAABgAAAAAAIAAAAAAAAAAAAAEAAgChAACAJgAAAAQAAAAHAAAAAAAgAAAAAAAAAAAA"
        "AQACAKEAAIAoAAAABAAAAAgAAAAAACAAAAAAAAAAAAABAAEA5QMAgCoAAAAMAAAACQAAAAAAIAAAAAAAAAAAAAEA"
        "AgChAACAIgAAAAQAAAAKAAAAAAAgAAAAAAAAAAAAAQACAKEAAIAkAAAABAAAAAsAAAAAACAAAAAAAAAAAAABAAIA"
        "oQAAgCYAAAAEAAAADAAAAAAAIAAAAAAAAAAAAAEAAQDlAwCAOgAAAAwAAAANAAAAAEAgAAAAAAAAAAAAAQACAKEA"
        "AIAiAAAABAAAAA4AAAAAACAAAAAAAAAAAAABAAIAoQAAgCQAAAAEAAAADwAAAAAAIAAAAAAAAAAAAAEAAgChAACA"
        "JgAAAAQAAAAQAAAAAAAgAAAAAAAAAAAAAQABANUDAIBHAAAA/////xEAAABBgAAAAAAAAAAAAAABAAIBMQAAgDEA"
        "AID/////EgAAAEFAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAATAAAAQQAAAAAAAAAAAAAAAQADAM4CAIBqAACA"
        "DAAAABQAAABBAAAAAAAAAAAAAAABAAQA3gAAgAAAAAAEAAAAFQAAAEEAAAAAAAAAAAAAAAEABAAuAwCACQAAAAgA"
        "AAAWAAAAQQAAAAAAAAAAAAAAAQABAM4CAIBSAAAADAAAABcAAAABAAAAAAAAAAAAAAABAAIA3gAAgAAAAAAEAAAA"
        "GAAAAAEAAAAAAAAAAAAAAAEAAgAuAwCACQAAAAgAAAAZAAAAAQAAAAAAAAAAAAAAbV9GaWxlSUQAbV9QYXRoSUQA"
        "bV9Mb2NhbFJvdGF0aW9uAHgAeQB6AHcAbV9Mb2NhbFBvc2l0aW9uAG1fTG9jYWxTY2FsZQBtX0NoaWxkcmVuAG1f"
        "RmF0aGVyAA=="
    ),
    "23": (  # MeshRenderer 2343 B
        "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"
        "BAAAADUAAAABAIAAAAAAAAAAAAABAAIALgMAgBYAAAAIAAAANgAAAAEAgAAAAAAAAAAAAE1lc2hSZW5kZXJlcgBt"
        "X0ZpbGVJRABtX1BhdGhJRABtX0Nhc3RTaGFkb3dzAG1fUmVjZWl2ZVNoYWRvd3MAbV9EeW5hbWljT2NjbHVkZWUA"
        "bV9TdGF0aWNTaGFkb3dDYXN0ZXIAbV9Nb3Rpb25WZWN0b3JzAG1fTGlnaHRQcm9iZVVzYWdlAG1fUmVmbGVjdGlv"
        "blByb2JlVXNhZ2UAbV9SYXlUcmFjaW5nTW9kZQBtX1JheVRyYWNlUHJvY2VkdXJhbABtX1JlbmRlcmluZ0xheWVy"
        "TWFzawBtX1JlbmRlcmVyUHJpb3JpdHkAbV9MaWdodG1hcEluZGV4AG1fTGlnaHRtYXBJbmRleER5bmFtaWMAbV9M"
        "aWdodG1hcFRpbGluZ09mZnNldAB4AHkAegB3AG1fTGlnaHRtYXBUaWxpbmdPZmZzZXREeW5hbWljAG1fTWF0ZXJp"
        "YWxzAFN0YXRpY0JhdGNoSW5mbwBtX1N0YXRpY0JhdGNoSW5mbwBmaXJzdFN1Yk1lc2gAc3ViTWVzaENvdW50AG1f"
        "U3RhdGljQmF0Y2hSb290AG1fUHJvYmVBbmNob3IAbV9MaWdodFByb2JlVm9sdW1lT3ZlcnJpZGUAbV9Tb3J0aW5n"
        "TGF5ZXJJRABtX1NvcnRpbmdMYXllcgBtX1NvcnRpbmdPcmRlcgBQUHRyPE1lc2g+AG1fQWRkaXRpb25hbFZlcnRl"
        "eFN0cmVhbXMAbV9FbmxpZ2h0ZW5WZXJ0ZXhTdHJlYW0A"
    ),
    "33": (  # MeshFilter 279 B
        "BwAAAC8AAAABAAAAAAAAADcAAIAYAAAAAAAAAAAAAAAAAAAAAAAAAAEAAQA0AgCAdgEAgAwAAAABAAAAQQAAAAAA"
        "AAAAAAAAAQACAN4AAIALAAAABAAAAAIAAABBAAAAAAAAAAAAAAABAAIALgMAgBQAAAAIAAAAAwAAAEEAAAAAAAAA"
        "AAAAAAEAAQAdAAAAKAAAAAwAAAAEAAAAAAAAAAAAAAAAAAAAAQACAN4AAIALAAAABAAAAAUAAAABAIAAAAAAAAAA"
        "AAABAAIALgMAgBQAAAAIAAAABgAAAAEAgAAAAAAAAAAAAE1lc2hGaWx0ZXIAbV9GaWxlSUQAbV9QYXRoSUQAUFB0"
        "cjxNZXNoPgBtX01lc2gA"
    ),
}

TYPE_IDS: dict[str, int] = {"1": 4, "4": 1, "23": 13, "33": 6}

NAMES: dict[str, str] = {
    "1": "GameObject",
    "4": "Transform",
    "23": "MeshRenderer",
    "33": "MeshFilter",
}

SAMPLES: dict[str, str] = {
    "GameObject:lod_1x1x1": (  # pathID -8087847862595026136 typeID 4 63 B
        "AwAAAAAAAAAoA3jWz3wfIQAAAAAoA8kbSzZZ4gAAAAAoA3fjIAmbfwAAAAAJAAAAbG9kXzF4MXgxAAAAAAAB"
    ),
    "Transform:lod_1x1x1": (  # pathID 2386763559627916072 typeID 1 68 B
        "AAAAACgDn2A4McKPAAAAgAAAAIAAAACAAACAPwAAAADXo7A/AACAvi4lY0CxtCNAH2/gQAAAAAAAAAAAKANbWj3U"
        "22w="
    ),
    "MeshFilter:lod_1x1x1": (  # pathID -2136616851993984216 typeID 6 24 B
        "AAAAACgDn2A4McKPAgAAANonAAAAAAAA"
    ),
    "MeshRenderer:lod_1x1x1": (  # pathID 9194953101066830632 typeID 13 156 B
        "AAAAACgDn2A4McKPAQABAQABAQECAAAAAQIAAAAAAAD/////AACAPwAAgD8AAAAAAAAAAAAAgD8AAIA/AAAAAAAA"
        "AAABAAAAAAAAAE5uF1xXCeBzAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    ),
    "Transform:Chassis": (  # pathID -4049937970479168728 typeID 1 152 B
        "AAAAACgDRllq8v8HWohotAAAAIAAAACAAACAPwAAAAAAAAAAAAAAAAAAgD8AAIA/AACAPwcAAAAAAAAAKAPnzjwQ"
        "ZNUAAAAAKAM2+2pva1gAAAAAKAOGmVzpI3UAAAAAKAPk4+QewxUAAAAAKAMNu1xMs5QAAAAAKAPUjoLhjSIAAAAA"
        "KANbWj3U22wAAAAAKAOWQXJP6Jw="
    ),
}

SAMPLE_PATH_IDS: dict[str, int] = {
    "GameObject:lod_1x1x1": -8087847862595026136,
    "Transform:lod_1x1x1": 2386763559627916072,
    "MeshFilter:lod_1x1x1": -2136616851993984216,
    "MeshRenderer:lod_1x1x1": 9194953101066830632,
    "Transform:Chassis": -4049937970479168728,
}

# T-72B prefab 里 Chassis 节点的 7 个孩子（实测，父 = -4049937970479168728）
PARENT_CHILDREN_BEFORE: tuple[int, ...] = (
    -3070311192604507352,
    6371308603117536040,
    8440846710544401192,
    1568131063679812392,
    -7731752174106115288,
    2489894119864009512,
    7844096535969858344,
)

# 真 catalog 里第一条 bundle 的 key 字节 / extra data（含 SerializedType 头）
CATALOG_KEY_BLOB = (
    "AFwAAAAxNTY4NjFhMTc1Y2U0MWVhMzI3MDg5MDE2NmQ3YzU3YV91bml0eWJ1aWx0aW5zaGFkZXJzX2ZlYTQ2ZWFi"
    "NDhiMDE2OTBiMGZmMWVhYjJlYTUyNDhiLmJ1bmRsZQ=="
)

CATALOG_EXTRA_BLOB = (  # 真 catalog 第一条 bundle 的整条 extra data（含 157 B 头）
    "B0xVbml0eS5SZXNvdXJjZU1hbmFnZXIsIFZlcnNpb249MC4wLjAuMCwgQ3VsdHVyZT1uZXV0cmFsLCBQdWJsaWNL"
    "ZXlUb2tlbj1udWxsSlVuaXR5RW5naW5lLlJlc291cmNlTWFuYWdlbWVudC5SZXNvdXJjZVByb3ZpZGVycy5Bc3Nl"
    "dEJ1bmRsZVJlcXVlc3RPcHRpb25z1AIAAHsAIgBtAF8ASABhAHMAaAAiADoAIgBmAGUAYQA0ADYAZQBhAGIANAA4"
    "AGIAMAAxADYAOQAwAGIAMABmAGYAMQBlAGEAYgAyAGUAYQA1ADIANAA4AGIAIgAsACIAbQBfAEMAcgBjACIAOgAz"
    "ADQAOQAxADYANAA5ADIAMQA2ACwAIgBtAF8AVABpAG0AZQBvAHUAdAAiADoAMAAsACIAbQBfAEMAaAB1AG4AawBl"
    "AGQAVAByAGEAbgBzAGYAZQByACIAOgBmAGEAbABzAGUALAAiAG0AXwBSAGUAZABpAHIAZQBjAHQATABpAG0AaQB0"
    "ACIAOgAtADEALAAiAG0AXwBSAGUAdAByAHkAQwBvAHUAbgB0ACIAOgAwACwAIgBtAF8AQgB1AG4AZABsAGUATgBh"
    "AG0AZQAiADoAIgAxADUANgA4ADYAMQBhADEANwA1AGMAZQA0ADEAZQBhADMAMgA3ADAAOAA5ADAAMQA2ADYAZAA3"
    "AGMANQA3AGEAXwB1AG4AaQB0AHkAYgB1AGkAbAB0AGkAbgBzAGgAYQBkAGUAcgBzACIALAAiAG0AXwBBAHMAcwBl"
    "AHQATABvAGEAZABNAG8AZABlACIAOgAwACwAIgBtAF8AQgB1AG4AZABsAGUAUwBpAHoAZQAiADoAOAAxADcAOAA2"
    "ACwAIgBtAF8AVQBzAGUAQwByAGMARgBvAHIAQwBhAGMAaABlAGQAQgB1AG4AZABsAGUAcwAiADoAdAByAHUAZQAs"
    "ACIAbQBfAFUAcwBlAFUAVwBSAEYAbwByAEwAbwBjAGEAbABCAHUAbgBkAGwAZQBzACIAOgBmAGEAbABzAGUALAAi"
    "AG0AXwBDAGwAZQBhAHIATwB0AGgAZQByAEMAYQBjAGgAZQBkAFYAZQByAHMAaQBvAG4AcwBXAGgAZQBuAEwAbwBh"
    "AGQAZQBkACIAOgBmAGEAbABzAGUAfQA="
)

CATALOG_EXTRA_OPTIONS: dict[str, object] = {
    'm_Hash': 'fea46eab48b01690b0ff1eab2ea5248b',
    'm_Crc': 3491649216,
    'm_Timeout': 0,
    'm_ChunkedTransfer': False,
    'm_RedirectLimit': -1,
    'm_RetryCount': 0,
    'm_BundleName': '156861a175ce41ea3270890166d7c57a_unitybuiltinshaders',
    'm_AssetLoadMode': 0,
    'm_BundleSize': 81786,
    'm_UseCrcForCachedBundles': True,
    'm_UseUWRForLocalBundles': False,
    'm_ClearOtherCachedVersionsWhenLoaded': False,
}

# --------------------------------------------------------------------------- #
# 共用件
# --------------------------------------------------------------------------- #
#: classID -> 本机 units 包类型表里的 type_id（实测；正控用它，⛔ 不是抄文档）
TYPE_INDEX: dict[int, int] = {
    cl.CLASS_TRANSFORM: 1,
    cl.CLASS_GAME_OBJECT: 4,
    cl.CLASS_MESH_FILTER: 6,
    cl.CLASS_MESH_RENDERER: 13,
}

#: 实测靶子（自己读的，见 _work/w8_clone_probe_types.py）
MESH_TANKTRAK_R = 2175081331415725408
MATERIAL_T72B_TRACK = 7952762356820983200
PARENT_CHASSIS = -4049937970479168728
EXISTING_IDS: tuple[int, ...] = (
    -9223088635350594277,
    -4049937970479168728,
    2386763559627916072,
    9223358220759698752,
)


def _common_strings() -> Mapping[int, str]:
    """内嵌类型树的公共串表（本机真数据里 7903 处引用它）。"""
    return cl.unitypy_common_strings()


def _nodes(class_id: int) -> tuple[cl.TypeNode, ...]:
    return cl.read_type_nodes(
        base64.b64decode(TYPE_BLOBS[str(class_id)]), common_strings=_common_strings()
    )


def _schema() -> cl.TypeSchema:
    """按实测的 type_id 拼一份 TypeSchema（type_id 真值在上面 TYPE_INDEX）。"""
    entries = tuple(
        cl.TypeEntry(
            index=TYPE_INDEX[class_id],
            class_id=class_id,
            script_index=-1,
            nodes=_nodes(class_id),
        )
        for class_id in (
            cl.CLASS_TRANSFORM,
            cl.CLASS_GAME_OBJECT,
            cl.CLASS_MESH_FILTER,
            cl.CLASS_MESH_RENDERER,
        )
    )
    return cl.TypeSchema(entries=entries)


def _sample(name: str) -> bytes:
    return base64.b64decode(SAMPLES[name])


def _templates() -> cl.CloneTemplates:
    return cl.CloneTemplates(
        transform=_sample("Transform:lod_1x1x1"),
        game_object=_sample("GameObject:lod_1x1x1"),
        mesh_filter=_sample("MeshFilter:lod_1x1x1"),
        mesh_renderer=_sample("MeshRenderer:lod_1x1x1"),
    )


def _target(**overrides: object) -> cl.CloneTarget:
    base: dict[str, object] = {
        "parent_transform": PARENT_CHASSIS,
        "mesh": MESH_TANKTRAK_R,
        "materials": (MATERIAL_T72B_TRACK,),
        "name": "RU_T72B_clone_track",
        "local_position": (0.0, 1.5, 0.0),
    }
    base.update(overrides)
    return cl.CloneTarget(**base)  # type: ignore[arg-type]


def _graph(templates: cl.CloneTemplates | None = None, **overrides: object) -> cl.CloneGraph:
    """造一次克隆图（父 = 真 Chassis 的字节；4 个 pathID 写死 101..104 便于读判据）。"""
    import dataclasses

    graph = cl.build_mesh_renderer_clone(
        _schema(),
        templates or _templates(),
        _target(**overrides),
        parent_payload=_sample("Transform:Chassis"),
        path_ids=(101, 102, 103, 104),
    )
    # 「包内」= 录下来的那几个真对象 + 靶子 mesh/material（探针里在真包的对象表里核过，
    #           units 包 162,010 个 pathID）+ 本次新增的 4 个
    known = (
        frozenset(SAMPLE_PATH_IDS.values())
        | {MESH_TANKTRAK_R, MATERIAL_T72B_TRACK}
        | {o.path_id for o in graph.objects}
    )
    return dataclasses.replace(graph, package_ids=known)


# --------------------------------------------------------------------------- #
# ① 类型表 / 类型树（类型树当 schema）
# --------------------------------------------------------------------------- #
def test_type_tree_class_ids_and_field_names_match_the_real_bundle() -> None:
    """四个类的类型树从**真 blob** 解出来；字段名逐个钉住（抄自实测，不是猜的）。"""
    expected = {
        cl.CLASS_TRANSFORM: ["m_GameObject", "m_LocalRotation", "m_LocalPosition",
                             "m_LocalScale", "m_Children", "m_Father"],
        cl.CLASS_GAME_OBJECT: ["m_Component", "m_Layer", "m_Name", "m_Tag", "m_IsActive"],
        cl.CLASS_MESH_FILTER: ["m_GameObject", "m_Mesh"],
    }
    for class_id, names in expected.items():
        nodes = _nodes(class_id)
        assert [n.name for n in nodes if n.level == 1] == names, f"classID {class_id} 字段变了"
    # MeshRenderer 的字段多（27 个），只钉住关键那三个
    mr = {n.name: n.type for n in _nodes(cl.CLASS_MESH_RENDERER)}
    assert mr["m_Materials"] == "vector"
    assert mr["m_Enabled"] == "bool"
    assert mr["m_GameObject"] == "PPtr<GameObject>"
    assert all(TYPE_IDS[str(c)] == TYPE_INDEX[c] for c in TYPE_INDEX)


def test_common_strings_are_required_and_the_ruler_can_go_red() -> None:
    """负控：不传公共串表 -> 当场抛（真件有 7903 处公共串引用，不许静默回落）。"""
    blob = base64.b64decode(TYPE_BLOBS[str(cl.CLASS_TRANSFORM)])
    with pytest.raises(Exception) as excinfo:
        cl.read_type_nodes(blob, common_strings=None)
    assert "公共串表" in str(excinfo.value)
    # 正控：给了就解出来，且根节点是 Transform
    assert _nodes(cl.CLASS_TRANSFORM)[0].type == "Transform"


# --------------------------------------------------------------------------- #
# ⑤ 序列化往返（真对象字节 -> 类型树读回 -> 逐字节写回）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_roundtrip_is_byte_exact_on_every_recorded_object(name: str) -> None:
    """判据 5 的正控：真对象字节读一遍再写一遍，必须**逐字节相同**。"""
    class_id = {"Transform": cl.CLASS_TRANSFORM, "GameObject": cl.CLASS_GAME_OBJECT,
                "MeshFilter": cl.CLASS_MESH_FILTER, "MeshRenderer": cl.CLASS_MESH_RENDERER}[
        name.partition(":")[0]
    ]
    nodes = _nodes(class_id)
    data = _sample(name)
    fields = cl.read_object(data, nodes)
    assert cl.write_object(fields, nodes) == data
    assert cl.roundtrip(data, nodes) == data
    assert cl.read_object(cl.write_object(fields, nodes), nodes) == fields


def test_serializer_agrees_with_unitypy_byte_for_byte() -> None:
    """跨实现对拍：同一棵类型树 + 同一份字段树，我的字节 == UnityPy 的字节。"""
    unitypy = pytest.importorskip("UnityPy")
    from UnityPy.helpers.TypeTreeHelper import read_typetree, write_typetree
    from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

    type_node_of = _unitypy_type_nodes()
    for name, class_id in (
        ("Transform:lod_1x1x1", cl.CLASS_TRANSFORM),
        ("GameObject:lod_1x1x1", cl.CLASS_GAME_OBJECT),
        ("MeshFilter:lod_1x1x1", cl.CLASS_MESH_FILTER),
        ("MeshRenderer:lod_1x1x1", cl.CLASS_MESH_RENDERER),
    ):
        nodes = _nodes(class_id)
        fields = cl.read_object(_sample(name), nodes)
        mine = cl.write_object(fields, nodes)
        writer = EndianBinaryWriter(endian="<")
        write_typetree(fields, type_node_of[class_id], writer)
        assert writer.bytes == mine, f"{name}: UnityPy 写的字节与我的不同"
        # 反向：UnityPy 读我的字节 == 我的字段树
        back = read_typetree(type_node_of[class_id], EndianBinaryReader(mine, "<"),
                             as_dict=True, byte_size=len(mine))
        assert back == fields
    assert unitypy is not None


def _unitypy_type_nodes() -> dict[int, object]:
    """用录下来的四个 blob 让 UnityPy 自己解一遍（**独立**于我的解析器）。"""
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    from UnityPy.streams.EndianBinaryReader import EndianBinaryReader

    out: dict[int, object] = {}
    for class_id, key in (
        (cl.CLASS_TRANSFORM, "4"),
        (cl.CLASS_GAME_OBJECT, "1"),
        (cl.CLASS_MESH_FILTER, "33"),
        (cl.CLASS_MESH_RENDERER, "23"),
    ):
        reader = EndianBinaryReader(base64.b64decode(TYPE_BLOBS[key]), "<")
        out[class_id] = TypeTreeNode.parse_blob(reader, 22)
    return out


# --------------------------------------------------------------------------- #
# ①②③④ 克隆图
# --------------------------------------------------------------------------- #
def test_clone_graph_has_four_objects_with_matching_type_ids() -> None:
    """判据 1：四个新对象的 classID / typeID 与类型表对得上，且互不相同。"""
    graph = _graph()
    assert len(graph.objects) == 4
    got = {o.class_id: o.type_id for o in graph.objects}
    assert got == TYPE_INDEX
    assert len({o.path_id for o in graph.objects}) == 4
    assert [o.type_name for o in graph.objects] == [
        "Transform", "GameObject", "MeshFilter", "MeshRenderer",
    ]


def test_new_gameobject_component_order_and_references() -> None:
    """GameObject.m_Component 必须 [Transform, MeshFilter, MeshRenderer]（顺序有语义）。"""
    graph = _graph()
    tr = graph.object(cl.CLASS_TRANSFORM)
    go = graph.object(cl.CLASS_GAME_OBJECT)
    mf = graph.object(cl.CLASS_MESH_FILTER)
    mr = graph.object(cl.CLASS_MESH_RENDERER)
    components = [c["component"]["m_PathID"] for c in go.fields["m_Component"]]
    assert components == [tr.path_id, mf.path_id, mr.path_id]
    assert all(c["component"]["m_FileID"] == 0 for c in go.fields["m_Component"])
    assert tr.fields["m_Children"] == [], "新 Transform 的 m_Children 必须空"
    assert mf.fields["m_GameObject"] == cl.pptr(go.path_id)
    assert mr.fields["m_GameObject"] == cl.pptr(go.path_id)
    assert mr.fields["m_Enabled"] is True


def test_mesh_and_material_are_in_package_references_with_file_id_zero() -> None:
    """判据 J4 的一半：Mesh / Material 都是**包内已有**对象，fileID 必须 0。"""
    graph = _graph()
    mf = graph.object(cl.CLASS_MESH_FILTER)
    mr = graph.object(cl.CLASS_MESH_RENDERER)
    assert mf.fields["m_Mesh"] == {"m_FileID": 0, "m_PathID": MESH_TANKTRAK_R}
    assert mr.fields["m_Materials"] == [{"m_FileID": 0, "m_PathID": MATERIAL_T72B_TRACK}]


def test_parent_children_gain_exactly_one_and_keep_order() -> None:
    """判据 3：父 Transform 的 m_Children 恰好 +1，且顺序保持（实测 7 个孩子）。"""
    graph = _graph()
    new_tr = graph.object(cl.CLASS_TRANSFORM).path_id
    assert graph.parent_children_before == PARENT_CHILDREN_BEFORE
    assert graph.parent_children_after == (*PARENT_CHILDREN_BEFORE, new_tr)
    assert len(graph.parent_children_after) == len(graph.parent_children_before) + 1
    # 独立读回：从父的新字节里解出来的孩子表也要一样
    fields = cl.read_object(graph.parent.payload, _nodes(cl.CLASS_TRANSFORM))
    assert tuple(p["m_PathID"] for p in fields["m_Children"]) == graph.parent_children_after
    assert graph.parent.size == len(_sample("Transform:Chassis")) + 12


def test_negative_control_parent_that_already_has_the_child_is_refused() -> None:
    """负控：父的 m_Children 里已经有新 Transform 时，构建必须当场抛（不许静默重复）。"""
    nodes = _nodes(cl.CLASS_TRANSFORM)
    fields = cl.read_object(_sample("Transform:Chassis"), nodes)
    fields["m_Children"] = [*fields["m_Children"], cl.pptr(101)]
    poisoned = cl.write_object(fields, nodes)
    with pytest.raises(Exception) as excinfo:
        cl.build_mesh_renderer_clone(
            _schema(), _templates(), _target(),
            parent_payload=poisoned, path_ids=(101, 102, 103, 104),
        )
    assert "已经有" in str(excinfo.value)


def test_new_transform_father_points_to_the_parent() -> None:
    """判据 4：新 Transform 的 m_Father 指向选定的那个真实父节点。"""
    graph = _graph()
    tr = graph.object(cl.CLASS_TRANSFORM)
    assert tr.fields["m_Father"] == {"m_FileID": 0, "m_PathID": PARENT_CHASSIS}
    assert tr.fields["m_GameObject"] == cl.pptr(graph.object(cl.CLASS_GAME_OBJECT).path_id)
    assert tr.fields["m_LocalPosition"] == {"x": 0.0, "y": 1.5, "z": 0.0}


def test_allocated_path_ids_follow_max_plus_one_then_align() -> None:
    """分配口径 = **包内最大 pathID + 1** 再向上取整到 8（与 A 组的 next_path_id 同源）。"""
    ids = cl.allocate_path_ids(EXISTING_IDS, 4)
    top = max(EXISTING_IDS)
    start = ((top + 1 + 7) // 8) * 8
    assert ids[0] == start
    assert ids == tuple(start + 8 * i for i in range(4))
    # 空包也能给（从 align 起步），⛔ 不给 0 或负数
    assert cl.allocate_path_ids((), 2) == (8, 16)


def test_path_ids_are_unique_and_aligned() -> None:
    """判据 J1：新 pathID 与包内任何已有 pathID 不撞，且全部 8 字节对齐。"""
    ids = cl.allocate_path_ids(EXISTING_IDS, 4)
    assert len(set(ids)) == 4
    assert all(v % 8 == 0 for v in ids), ids
    assert not (set(ids) & set(EXISTING_IDS))
    assert ids == tuple(sorted(ids))
    # 负控：count 不合法要抛（避免静默给 0 个 pathID 让下游空跑）
    with pytest.raises(ValueError):
        cl.allocate_path_ids(EXISTING_IDS, 0)


# --------------------------------------------------------------------------- #
# ② 引用判据（J4）：无悬空 + 负控
# --------------------------------------------------------------------------- #
def test_refs_have_no_dangling_and_no_cross_file() -> None:
    """判据 2：新对象里每个 PPtr 都能在包内解到（含本次新增的 4 个）。"""
    graph = _graph()
    report = graph.refs()
    assert report.dangling == (), report.lines()
    assert report.external == (), report.lines()
    assert report.ok is True
    # 实测读数：14 条 PPtr —— 9 条包内（含互相引用与 mesh/material）、5 条空
    assert len(report.refs) == 14
    assert len(report.in_package) == 9
    assert len(report.null) == 5
    assert all(r.field for r in report.refs)


def test_negative_control_dangling_mesh_must_go_red() -> None:
    """负控：把 m_Mesh 指到一个不存在的 pathID，判据必须红（且点名那一条）。"""
    graph = _graph()
    mf = graph.object(cl.CLASS_MESH_FILTER)
    bad = dict(mf.fields)
    bad["m_Mesh"] = cl.pptr(1234567890123456789)
    report = cl.check_refs(
        {"MeshFilter": (bad, _nodes(cl.CLASS_MESH_FILTER))}, graph.package_ids
    )
    assert report.ok is False
    assert len(report.dangling) == 1
    assert "1234567890123456789" in str(report.dangling[0])
    assert any("悬空" in line for line in report.lines())


def test_negative_control_cross_file_reference_is_not_silently_ok() -> None:
    """负控：fileID != 0（跨文件）必须被点名，不许当"没问题"。"""
    graph = _graph()
    mf = graph.object(cl.CLASS_MESH_FILTER)
    bad = dict(mf.fields)
    bad["m_Mesh"] = cl.pptr(MESH_TANKTRAK_R, file_id=2)
    report = cl.check_refs(
        {"MeshFilter": (bad, _nodes(cl.CLASS_MESH_FILTER))}, graph.package_ids
    )
    assert report.ok is False
    assert len(report.external) == 1
    assert report.dangling == ()


def test_j5_monobehaviour_is_not_in_the_graph() -> None:
    """J5 口径：克隆图里**不含** MonoBehaviour => 这一项如实写 N/A（不硬塞）。"""
    graph = _graph()
    assert set(graph.class_ids()) == {
        cl.CLASS_TRANSFORM, cl.CLASS_GAME_OBJECT, cl.CLASS_MESH_FILTER, cl.CLASS_MESH_RENDERER,
    }
    assert "MonoBehaviour" not in {o.type_name for o in graph.objects}
    assert all(r.file_id == 0 for r in graph.refs().refs)


def test_delta_change_wires_into_the_w8a_writer_api() -> None:
    """接缝：克隆图装配成 writer.DeltaChange（added = 4、growths = 1）。"""
    from ba_mod_maker.asset_kernel import writer

    if not hasattr(writer, "NewObject"):
        pytest.skip("W8-A 的 writer.NewObject 还没落地")
    graph = _graph()
    change = cl.delta_change(graph)
    assert len(change.added) == 4
    assert len(change.growths) == 1
    assert change.growths[0].path_id == PARENT_CHASSIS
    assert {o.type_id for o in change.added} == set(TYPE_INDEX.values())
    # 接缝：Preload 那条 growth（AssetBundle pid=1）由调用方塞进来，必须在**同一个**事务里
    extra = writer.ObjectGrowth(path_id=1, payload=b"\x00" * 8, name="AssetBundle")
    merged = cl.delta_change(graph, growths=(extra,))
    assert [g.path_id for g in merged.growths] == [PARENT_CHASSIS, 1]
    assert len(merged.added) == 4


def test_criterion_lines_carry_the_readouts() -> None:
    """判据一行必须带上：零件名 / 四个对象 / 父孩子数 / mesh-material 的 pathID。"""
    text = "\n".join(_graph().criterion_lines())
    assert "RU_T72B_clone_track" in text
    assert str(PARENT_CHASSIS) in text
    assert str(MESH_TANKTRAK_R) in text
    assert str(MATERIAL_T72B_TRACK) in text
    assert "7 -> 8" in text

# --------------------------------------------------------------------------- #
# catalog：六件套 + 三个计数头（设计稿 §9.6）
# --------------------------------------------------------------------------- #
NEW_BUNDLE_NAME = "newclone_assets_all_" + "a" * 32 + ".bundle"
NEW_BUNDLE_HASH = "a" * 32
NEW_ASSET = "Assets/Clones/RU_T72B_clone_track.prefab"
NEW_ADDRESS = "clone/RU_T72B_clone_track"

ALPHA_BUNDLE = "alpha_assets_all_" + "1" * 32 + ".bundle"
BETA_BUNDLE = "beta_assets_all_" + "2" * 32 + ".bundle"
ALPHA_ADDRESS = "alpha/thing"
BETA_ADDRESS = "beta/thing"


def _base_raw() -> dict:
    """手搓一份最小但合规的 catalog（**不用被测代码拼结构**，逐条照 §9.6）。"""
    return _raw_catalog(
        bundles=(
            (ALPHA_BUNDLE, "1" * 32, 111111111, 111111),
            (BETA_BUNDLE, "2" * 32, 222222222, 222222),
        ),
        addresses=(
            ("Assets/Alpha/Thing.prefab", ALPHA_ADDRESS, 0, 111, 1),
            ("Assets/Beta/Thing.prefab", BETA_ADDRESS, 1, 222, 2),
        ),
    )


def _raw_catalog(
    bundles: Sequence[tuple[str, str, int, int]],
    addresses: Sequence[tuple[str, str, int, int, int]],
) -> dict:
    internal: list[str] = []
    key_data = bytearray(struct.pack("<i", 0))
    bucket_data = bytearray(struct.pack("<i", 0))
    entry_data = bytearray(struct.pack("<i", 0))
    extra = bytearray()
    offsets: list[int] = []
    state = {"keys": 0, "buckets": 0, "entries": 0}

    def add_key(value: str) -> int:
        offsets.append(len(key_data))
        key_data.extend(cw.key_bytes(cw.KEY_UTF8, value))
        state["keys"] += 1
        return state["keys"] - 1

    def add_bucket(key_index: int, entry_index: int) -> None:
        bucket_data.extend(struct.pack("<iii", offsets[key_index], 1, entry_index))
        state["buckets"] += 1

    def add_entry(row: Sequence[int]) -> int:
        entry_data.extend(struct.pack("<7i", *(int(v) for v in row)))
        state["entries"] += 1
        return state["entries"] - 1

    for name, m_hash, crc, size in bundles:
        internal.append(cw.RUNTIME_PATH + cw.PC_SEGMENT + name)
        options = cw.CatalogBundle(
            name, m_hash, crc, size, internal_name=name[: -len(".bundle")]
        ).options()
        blob = cw.extra_data_blob(options)
        xo = len(extra)
        extra.extend(blob)
        ki = add_key(name)
        ei = add_entry((len(internal) - 1, cw.ASSET_BUNDLE_PROVIDER, -1,
                        cw.INVALID_DEPENDENCY_HASH, xo, ki, 0))
        add_bucket(ki, ei)
    for asset_path, primary, dep, dep_hash, rtype in addresses:
        internal.append(asset_path)
        ki = add_key(primary)
        ei = add_entry((len(internal) - 1, cw.BUNDLED_ASSET_PROVIDER, dep, dep_hash, -1, ki, rtype))
        add_bucket(ki, ei)
    struct.pack_into("<i", key_data, 0, state["keys"])
    struct.pack_into("<i", bucket_data, 0, state["buckets"])
    struct.pack_into("<i", entry_data, 0, state["entries"])
    return {
        "m_LocatorId": "AddressablesMainContentCatalog",
        "m_BuildResultHash": "0" * 32,
        "m_ProviderIds": [
            "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.LegacyResourcesProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
            "",
        ],
        "m_InternalIds": internal,
        "m_KeyDataString": base64.b64encode(bytes(key_data)).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bytes(bucket_data)).decode("ascii"),
        "m_EntryDataString": base64.b64encode(bytes(entry_data)).decode("ascii"),
        "m_ExtraDataString": base64.b64encode(bytes(extra)).decode("ascii"),
        "m_resourceTypes": [
            {"m_AssemblyName": "Unity.ResourceManager", "m_ClassName": "GameObject"}
        ],
        "m_InternalIdPrefixes": [],
    }


def _disk(tmp_path: Path, raw: Mapping[str, object]) -> Path:
    path = tmp_path / cw.CATALOG_FILENAME
    path.write_bytes(json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return path


def _product(tmp_path: Path) -> AddressablesCatalog | None:
    return AddressablesCatalog.load(tmp_path)


def _append_new_bundle(table: cw.CatalogTable, *, crc: int = 123456789) -> cw.BundleRegistration:
    return cw.apply_bundle(
        table,
        cw.CatalogBundle(
            NEW_BUNDLE_NAME, NEW_BUNDLE_HASH, crc, 4242, internal_name="newclone"
        ),
    )


def _append_new_address(
    table: cw.CatalogTable, reg: cw.BundleRegistration, *, key: str = NEW_ADDRESS
) -> cw.AddressRegistration:
    return cw.apply_address(
        table,
        cw.CatalogAddress(NEW_ASSET, key, resource_type=1),
        dependency_key=reg.key_index,
        dependency_hash=777,
    )


def test_catalog_six_pieces_and_product_reader_reads_it_back(tmp_path: Path) -> None:
    """六件套：追加一条 bundle + 一条地址，用**产品自己的读取口径**读回来核对。"""
    raw = _base_raw()
    table = cw.CatalogTable.from_raw(raw)
    before = table.counts
    reg = _append_new_bundle(table)
    addr = _append_new_address(table, reg)

    # ---- 六件套逐条 ----
    assert table.internal_ids[reg.internal_id_index] == (
        cw.RUNTIME_PATH + cw.PC_SEGMENT + NEW_BUNDLE_NAME
    )
    assert table.key_value(reg.key_index) == NEW_BUNDLE_NAME
    offset, entries = table.buckets()[reg.bucket_index]
    assert offset == table.key_offsets[reg.key_index] and entries == [reg.entry_index]
    assert table.entry(reg.entry_index) == (
        reg.internal_id_index, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH,
        reg.extra_offset, reg.key_index, 0,
    )
    options = table.entry_options(reg.entry_index)
    assert options is not None
    assert (options["m_Crc"], options["m_BundleSize"], options["m_Hash"]) == (
        123456789, 4242, NEW_BUNDLE_HASH,
    )
    # 第 7 件：资产地址的 entry
    assert table.entry(addr.entry_index) == (
        addr.internal_id_index, cw.BUNDLED_ASSET_PROVIDER, reg.key_index, 777, -1,
        addr.key_index, 1,
    )

    # ---- 三个计数头 +2 ----
    assert table.counts == (before[0] + 2, before[1] + 2, before[2] + 2)

    # ---- 产品读侧读回来 ----
    path = _disk(tmp_path, table.to_raw())
    catalog = _product(tmp_path)
    assert catalog is not None, f"产品读侧读不回来：{path}"
    assert catalog.lookups(NEW_ADDRESS) == ((NEW_ASSET, NEW_BUNDLE_NAME),)
    assert catalog.lookups(NEW_BUNDLE_NAME) == (
        (cw.RUNTIME_PATH + cw.PC_SEGMENT + NEW_BUNDLE_NAME, ""),
    )
    # 坑 4 的尺子（真件 21,274/21,274 成立）
    assert cw.check_pairs(table) == ()


def test_catalog_count_headers_are_the_first_i32_of_each_table(tmp_path: Path) -> None:
    """三个计数头就是三张表的头 4 字节；追加后必须同步 +N（漏一个就整张错位）。"""
    table = cw.CatalogTable.from_raw(_base_raw())
    reg = _append_new_bundle(table)
    _append_new_address(table, reg)
    raw = table.to_raw()
    for field, expected in (
        ("m_KeyDataString", table.key_count),
        ("m_BucketDataString", table.bucket_count),
        ("m_EntryDataString", table.entry_count),
    ):
        blob = base64.b64decode(raw[field])
        (count,) = struct.unpack_from("<i", blob, 0)
        assert count == expected, f"{field} 的计数头没跟上"
    assert len(raw["m_InternalIds"]) == len(table.internal_ids)


def test_catalog_existing_keys_are_untouched(tmp_path: Path) -> None:
    """追加只能往后面加：原有 key 的解析结果与偏移一个都不许动。"""
    raw = _base_raw()
    table = cw.CatalogTable.from_raw(raw)
    offsets_before = [row[4] for row in table.entries()]

    _disk(tmp_path, raw)
    before = _product(tmp_path)
    assert before is not None
    alpha_before = before.lookups(ALPHA_ADDRESS)
    beta_before = before.lookups(BETA_ADDRESS)

    reg = _append_new_bundle(table)
    _append_new_address(table, reg)
    _disk(tmp_path, table.to_raw())
    after = _product(tmp_path)
    assert after is not None
    assert after.lookups(ALPHA_ADDRESS) == alpha_before
    assert after.lookups(BETA_ADDRESS) == beta_before
    assert alpha_before == (("Assets/Alpha/Thing.prefab", ALPHA_BUNDLE),)
    # 老的 extraDataOffset 一个没动（只有新追加的才有新偏移）
    assert [row[4] for row in table.entries()][: len(offsets_before)] == offsets_before


def test_catalog_encoders_reproduce_the_real_catalog_bytes() -> None:
    """正控：key 编码与 AssetBundleRequestOptions 的字节**逐字节**复现真 catalog 里那一条。"""
    blob = base64.b64decode(CATALOG_EXTRA_BLOB)
    options = cw.entry_options(blob, 0)
    assert options == CATALOG_EXTRA_OPTIONS, "真 blob 解出来的字段与录像不一致"
    assert cw.extra_data_blob(options) == blob, "extra data 重新编码不是逐字节原样"
    bundle_name = (
        "156861a175ce41ea3270890166d7c57a_unitybuiltinshaders_" + options["m_Hash"] + ".bundle"
    )
    assert cw.key_bytes(cw.KEY_UTF8, bundle_name) == base64.b64decode(CATALOG_KEY_BLOB)


# --------------------------------------------------------------------------- #
# 坑 1：长度是字节长，不是字符数
# --------------------------------------------------------------------------- #
def test_pit1_key_length_must_be_bytes_not_characters(tmp_path: Path) -> None:
    """坑 1 负控：UTF-16 的 key 用**字符数**当长度 -> 产品读侧当场读不回来。"""
    chinese = "克隆车/履带"
    encoded = cw.key_bytes(cw.KEY_UTF16, chinese)
    assert len(chinese) == 6 and len(chinese.encode("utf-16-le")) == 12
    assert len(encoded) == 1 + 4 + 12, "UTF-16 的长度必须是字节数"

    table = cw.CatalogTable.from_raw(_base_raw())
    reg = _append_new_bundle(table)
    key_index = table.add_key(chinese, cw.KEY_UTF16)
    internal_index = table.add_internal_id("Assets/Clones/履带.prefab")
    entry_index = table.add_entry(
        (internal_index, cw.BUNDLED_ASSET_PROVIDER, reg.key_index, 777, -1, key_index, 1)
    )
    table.add_bucket(key_index, entry_index)

    _disk(tmp_path, table.to_raw())
    good = _product(tmp_path)
    assert good is not None, "正确的字节长写法必须读得回来"
    assert good.lookups(chinese) == (("Assets/Clones/履带.prefab", NEW_BUNDLE_NAME),)

    # 负控：把那格长度改成**字符数**
    struct.pack_into("<i", table.key_data, table.key_offsets[key_index] + 1, len(chinese))
    _disk(tmp_path, table.to_raw())
    assert _product(tmp_path) is None, "字符数写法必须被读侧抓住（不许静默错位）"


# --------------------------------------------------------------------------- #
# 坑 2：extraDataOffset 会连锁平移，必须全量重算
# --------------------------------------------------------------------------- #
def test_pit2_extra_offsets_are_recomputed_and_a_stale_offset_goes_red(tmp_path: Path) -> None:
    """坑 2 负控：CRC 十进制位数变了 -> 后面那条 offset 必须跟着涨；抄旧值就红。"""
    table = cw.CatalogTable.from_raw(_base_raw())
    short = _append_new_bundle(table, crc=123456789)  # 9 位
    long_ = cw.apply_bundle(
        table,
        cw.CatalogBundle("longcrc_assets_all_" + "b" * 32 + ".bundle", "b" * 32,
                         1234567890, 9, internal_name="longcrc"),  # 10 位（JSON 长 2 字节）
    )
    blob_short = cw.extra_data_blob(table.entry_options(short.entry_index) or {})
    assert long_.extra_offset == short.extra_offset + len(blob_short)
    assert (table.entry_options(short.entry_index) or {})["m_Crc"] == 123456789
    assert (table.entry_options(long_.entry_index) or {})["m_Crc"] == 1234567890

    _disk(tmp_path, table.to_raw())
    catalog = _product(tmp_path)
    assert catalog is not None
    assert catalog.lookups("longcrc_assets_all_" + "b" * 32 + ".bundle")

    # 负控：把第二条的 offset 往回挪到"旧长度"算出来的位置 -> 解不出它自己的 CRC
    # 10 位 CRC 比 9 位多 1 个字符 = UTF-16 里多 2 字节 -> "旧长度"算出来的偏移少 2
    stale = long_.extra_offset - 2
    struct.pack_into("<i", table.entry_data, 4 + long_.entry_index * 28 + 16, stale)
    broken = _read_crc_or_none(table, long_.entry_index)
    assert broken != 1234567890, "抄旧偏移必须能被尺子量出来（要么抛错、要么读到别人的值）"


def _read_crc_or_none(table: cw.CatalogTable, entry_index: int) -> object:
    from ba_mod_maker.core.errors import AssetError

    try:
        options = table.entry_options(entry_index)
    except AssetError:
        return None
    return None if options is None else options.get("m_Crc")


# --------------------------------------------------------------------------- #
# 坑 3：不许抄模板条目的 7 元组
# --------------------------------------------------------------------------- #
def test_pit3_copied_template_tuple_points_at_the_template_bundle(tmp_path: Path) -> None:
    """坑 3 负控：把模板条目的依赖键抄给新 bundle -> 新包的键解析成**模板的包**。"""
    table = cw.CatalogTable.from_raw(_base_raw())
    template_key = 0  # ALPHA 那条 bundle 的 key
    reg = _append_new_bundle(table)
    assert table.entry(reg.entry_index)[2] == -1, "新 bundle 条目的依赖键必须是 -1"
    addr = _append_new_address(table, reg)

    _disk(tmp_path, table.to_raw())
    good = _product(tmp_path)
    assert good is not None
    assert good.lookups(NEW_ADDRESS) == ((NEW_ASSET, NEW_BUNDLE_NAME),)
    assert good.lookups(NEW_BUNDLE_NAME) == (
        (cw.RUNTIME_PATH + cw.PC_SEGMENT + NEW_BUNDLE_NAME, ""),
    )

    # 负控：抄模板条目的 [2]（指向模板资产所在的那个包）
    struct.pack_into("<i", table.entry_data, 4 + reg.entry_index * 28 + 8, template_key)
    _disk(tmp_path, table.to_raw())
    bad = _product(tmp_path)
    assert bad is not None
    assert bad.lookups(NEW_BUNDLE_NAME) != (
        (cw.RUNTIME_PATH + cw.PC_SEGMENT + NEW_BUNDLE_NAME, ""),
    ), "抄来的依赖键必须让新包的键指到模板的包上"
    assert bad.lookups(NEW_BUNDLE_NAME)[0][1] == ALPHA_BUNDLE
    assert addr.entry_index >= 0


# --------------------------------------------------------------------------- #
# 坑 4：entry 与 primaryKey / 桶一对一
# --------------------------------------------------------------------------- #
def test_pit4_entry_and_primary_key_are_one_to_one(tmp_path: Path) -> None:
    """坑 4 负控：两个地址各配自己的 key/bucket；顺序错了就指到别的资产（且不报错）。"""
    table = cw.CatalogTable.from_raw(_base_raw())
    reg = _append_new_bundle(table)
    first = _append_new_address(table, reg, key="clone/A")
    second = cw.apply_address(
        table,
        cw.CatalogAddress("Assets/Clones/B.prefab", "clone/B", resource_type=1),
        dependency_key=reg.key_index,
        dependency_hash=777,
    )
    # 我的追加器：一条 key 一个 size-1 桶，桶序 == key 序
    for key_index, (offset, entries) in enumerate(table.buckets()):
        assert offset == table.key_offsets[key_index] and entries == [key_index]
    assert cw.check_pairs(table) == ()

    _disk(tmp_path, table.to_raw())
    good = _product(tmp_path)
    assert good is not None
    assert good.lookups("clone/A") == ((NEW_ASSET, NEW_BUNDLE_NAME),)
    assert good.lookups("clone/B") == (("Assets/Clones/B.prefab", NEW_BUNDLE_NAME),)

    # 负控 1：把两条 entry 的 primaryKey 对调 -> check_pairs 必须点名两条
    struct.pack_into("<i", table.entry_data, 4 + first.entry_index * 28 + 20, second.key_index)
    struct.pack_into("<i", table.entry_data, 4 + second.entry_index * 28 + 20, first.key_index)
    problems = cw.check_pairs(table)
    assert len(problems) == 2, problems

    # 负控 2：把两个桶的 entry 指针也对调 -> 产品读侧真的指到**别的资产**（不报错）
    struct.pack_into("<iii", table.bucket_data, 4 + first.bucket_index * 12,
                     table.key_offsets[first.key_index], 1, second.entry_index)
    struct.pack_into("<iii", table.bucket_data, 4 + second.bucket_index * 12,
                     table.key_offsets[second.key_index], 1, first.entry_index)
    _disk(tmp_path, table.to_raw())
    bad = _product(tmp_path)
    assert bad is not None, "桶对调之后产品读侧照样读得动 —— 这正是坑 4 会静默错的原因"
    assert bad.lookups("clone/A") == (("Assets/Clones/B.prefab", NEW_BUNDLE_NAME),)