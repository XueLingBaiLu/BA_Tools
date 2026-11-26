r"""W3 · monoscript_build 的用例（三部分）。

1. **纯逻辑**（不碰真包）：身份解析 / 目录对账 / 逐键对账 / 负控 —— 日常档就跑；
2. **内存端到端**：用真件的**三棵类型树**（base64 内嵌，见文末常量）造一份小元数据区，
   走 builder.build_object_bundle **真造一个包**（自造 MonoScript + 绑定好的 MonoBehaviour），
   落临时文件后**独立读回**（open_view + verify_package + 逐字节比对）；
3. **slow**：真 units 包 —— 复算 a2 的 4431/0/604 与 5035/0/0，并逐颗回读真件 MonoScript。

夹具来源（复算，⛔ 不是手搓的树）：

    .venv\Scripts\python.exe _work\mono-line\mono-probe.py dump

它把那三棵树按「i32 节点数 + i32 字符串池 + 节点数组 + 字符串池」打成 base64，
本文件用产品自己的 metadata_writer.encode_type_tree 还原成节点 —— 走的是**产品代码**。

判据一行（slow 部分）：
    pytest tests/test_build_monoscript.py -q -m slow
      -> 一致 = 4431，不一致 = 0，读不了 = 604（a2 那条尺子的原读数）
      -> 喂 ref_types 之后：一致 = 5035，不一致 = 0，读不了 = 0
"""

from __future__ import annotations

import base64
import contextlib
import os
import struct
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.asset_kernel import monoscript_build as mb
from ba_mod_maker.core.errors import AssetError

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 重活锁（工单：开 units 包 3.38 GB，必须串行）。口径与 _work/locks/README.md 一致。
LOCK_DIR = WORK / "locks"
LOCK_WAIT_SECONDS = float(os.environ.get("BA_MOD_MAKER_MONO_LOCK_WAIT", "900"))
UNITS_PREFIX = "units_assets_all"

if str(LOCK_DIR) not in sys.path:
    sys.path.insert(0, str(LOCK_DIR))

# --------------------------------------------------------------------------- #
# 真件夹具（units 包，classID 115 / 114 / 142 各一棵树）
# --------------------------------------------------------------------------- #
MONO_SCRIPT_115_TYPE_ID = 0
MONO_SCRIPT_115_TREE_B64 = (
    "IwAAAN8AAAAHAAAAFQEAgDcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQBIAwCAqwEAgP////8BAAAAAYAIAAAA"
    "AAAAAAAAAQACATEAAIAxAACA/////wIAAAABQAgAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAAwAAAAEACAAAAAAA"
    "AAAAAAEAAwBRAACAagAAgAEAAAAEAAAAAQAIAAAAAAAAAAAAAQABAN4AAIAAAAAABAAAAAUAAAAQAAAAAAAAAAAA"
    "AAABAAEAiQQAgBEAAAAQAAAABgAAABAAAAAAAAAAAAAAAAEAAgCgAwCAIgAAAAEAAAAHAAAAEAAAAAAAAAAAAAAA"
    "AQACAKADAIArAAAAAQAAAAgAAAAQAAAAAAAAAAAAAAABAAIAoAMAgDQAAAABAAAACQAAABAAAAAAAAAAAAAAAAEA"
    "AgCgAwCAPQAAAAEAAAAKAAAAEAAAAAAAAAAAAAAAAQACAKADAIBGAAAAAQAAAAsAAAAQAAAAAAAAAAAAAAABAAIA"
    "oAMAgE8AAAABAAAADAAAABAAAAAAAAAAAAAAAAEAAgCgAwCAWAAAAAEAAAANAAAAEAAAAAAAAAAAAAAAAQACAKAD"
    "AIBhAAAAAQAAAA4AAAAQAAAAAAAAAAAAAAABAAIAoAMAgGoAAAABAAAADwAAABAAAAAAAAAAAAAAAAEAAgCgAwCA"
    "cwAAAAEAAAAQAAAAEAAAAAAAAAAAAAAAAQACAKADAIB8AAAAAQAAABEAAAAQAAAAAAAAAAAAAAABAAIAoAMAgIYA"
    "AAABAAAAEgAAABAAAAAAAAAAAAAAAAEAAgCgAwCAkAAAAAEAAAATAAAAEAAAAAAAAAAAAAAAAQACAKADAICaAAAA"
    "AQAAABQAAAAQAAAAAAAAAAAAAAABAAIAoAMAgKQAAAABAAAAFQAAABAAAAAAAAAAAAAAAAEAAgCgAwCArgAAAAEA"
    "AAAWAAAAEAAAAAAAAAAAAAAAAQABAEgDAIC4AAAA/////xcAAAAQgAAAAAAAAAAAAAABAAIBMQAAgDEAAID/////"
    "GAAAABFAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAAZAAAAEQAAAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAABoA"
    "AAARAAAAAAAAAAAAAAABAAEASAMAgMQAAAD/////GwAAABCAAAAAAAAAAAAAAAEAAgExAACAMQAAgP////8cAAAA"
    "EUAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAAB0AAAARAAAAAAAAAAAAAAABAAMAUQAAgGoAAIABAAAAHgAAABEA"
    "AAAAAAAAAAAAAAEAAQBIAwCA0AAAAP////8fAAAAEIAAAAAAAAAAAAAAAQACATEAAIAxAACA/////yAAAAARQAAA"
    "AAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAIQAAABEAAAAAAAAAAAAAAAEAAwBRAACAagAAgAEAAAAiAAAAEQAAAAAA"
    "AAAAAAAAbV9FeGVjdXRpb25PcmRlcgBtX1Byb3BlcnRpZXNIYXNoAGJ5dGVzWzBdAGJ5dGVzWzFdAGJ5dGVzWzJd"
    "AGJ5dGVzWzNdAGJ5dGVzWzRdAGJ5dGVzWzVdAGJ5dGVzWzZdAGJ5dGVzWzddAGJ5dGVzWzhdAGJ5dGVzWzldAGJ5"
    "dGVzWzEwXQBieXRlc1sxMV0AYnl0ZXNbMTJdAGJ5dGVzWzEzXQBieXRlc1sxNF0AYnl0ZXNbMTVdAG1fQ2xhc3NO"
    "YW1lAG1fTmFtZXNwYWNlAG1fQXNzZW1ibHlOYW1lAA=="
)
#: 真件实测的顶层字段（逐键对账的期望值）—— units 23/23 颗 MonoScript 同形。
MONO_SCRIPT_115_FIELDS = [
    "m_Name",
    "m_ExecutionOrder",
    "m_PropertiesHash",
    "m_ClassName",
    "m_Namespace",
    "m_AssemblyName",
]

MONO_BEHAVIOUR_114_TYPE_ID = 19
MONO_BEHAVIOUR_114_TREE_B64 = (
    "DAAAABIAAAABAAAABwEAgDcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQA0AgCAdgEAgAwAAAABAAAAQQAAAAAA"
    "AAAAAAAAAQACAN4AAIAAAAAABAAAAAIAAABBAAAAAAAAAAAAAAABAAIALgMAgAkAAAAIAAAAAwAAAEEAAAAAAAAA"
    "AAAAAAEAAQCgAwCAXQEAgAEAAAAEAAAAAUEAAAAAAAAAAAAAAQABAGgCAIDqAQCADAAAAAUAAAAAAAAAAAAAAAAA"
    "AAABAAIA3gAAgAAAAAAEAAAABgAAAAEAgAAAAAAAAAAAAAEAAgAuAwCACQAAAAgAAAAHAAAAAQCAAAAAAAAAAAAA"
    "AQABAEgDAICrAQCA/////wgAAAABgAgAAAAAAAAAAAABAAIBMQAAgDEAAID/////CQAAAAFACAAAAAAAAAAAAAEA"
    "AwDeAACAGwMAgAQAAAAKAAAAAQAIAAAAAAAAAAAAAQADAFEAAIBqAACAAQAAAAsAAAABAAgAAAAAAAAAAABtX0Zp"
    "bGVJRABtX1BhdGhJRAA="
)
#: 这棵树 = DestroyInBattleMarker（units 里脚本名最少的那棵 114 树，12 节点）。
MONO_BEHAVIOUR_114_FIELDS = ["m_GameObject", "m_Enabled", "m_Script", "m_Name"]

ASSET_BUNDLE_142_TREE_B64 = (
    "OgAAAPIAAAADAAAAAAAAADcAAID/////AAAAAACAAAAAAAAAAAAAAAEAAQBIAwCAqwEAgP////8BAAAAAYAIAAAA"
    "AAAAAAAAAQACATEAAIAxAACA/////wIAAAABQAgAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAAwAAAAEACAAAAAAA"
    "AAAAAAEAAwBRAACAagAAgAEAAAAEAAAAAQAIAAAAAAAAAAAAAQABANUDAIAMAAAA/////wUAAAAAgAAAAAAAAAAA"
    "AAABAAIBMQAAgDEAAID/////BgAAAABAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAAHAAAAAAAAAAAAAAAAAAAA"
    "AQADAHkCAIBqAACADAAAAAgAAAAAAAAAAAAAAAAAAAABAAQA3gAAgBsAAAAEAAAACQAAAAEAgAAAAAAAAAAAAAEA"
    "BAAuAwCAJAAAAAgAAAAKAAAAAQCAAAAAAAAAAAAAAQABAPEAAIAtAAAA/////wsAAAAAgAAAAAAAAAAAAAABAAIB"
    "MQAAgDEAAID/////DAAAAACAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAANAAAAAAAAAAAAAAAAAAAAAQADAB8C"
    "AIBqAACA/////w4AAAAAgAAAAAAAAAAAAAABAAQASAMAgJsAAID/////DwAAAACAAAAAAAAAAAAAAAEABQExAACA"
    "MQAAgP////8QAAAAAUAAAAAAAAAAAAAAAQAGAN4AAIAbAwCABAAAABEAAAABAAAAAAAAAAAAAAABAAYAUQAAgGoA"
    "AIABAAAAEgAAAAEAAAAAAAAAAAAAAAEABAA5AAAACgMAgBQAAAATAAAAAAAAAAAAAAAAAAAAAQAFAN4AAIBDAAAA"
    "BAAAABQAAAAAAAAAAAAAAAAAAAABAAUA3gAAgFAAAAAEAAAAFQAAAAAAAAAAAAAAAAAAAAEABQB5AgCAXAAAAAwA"
    "AAAWAAAAAAAAAAAAAAAAAAAAAQAGAN4AAIAbAAAABAAAABcAAAABAIAAAAAAAAAAAAABAAYALgMAgCQAAAAIAAAA"
    "GAAAAAEAgAAAAAAAAAAAAAEAAQA5AAAAYgAAABQAAAAZAAAAAAAAAAAAAAAAAAAAAQACAN4AAIBDAAAABAAAABoA"
    "AAAAAAAAAAAAAAAAAAABAAIA3gAAgFAAAAAEAAAAGwAAAAAAAAAAAAAAAAAAAAEAAgB5AgCAXAAAAAwAAAAcAAAA"
    "AAAAAAAAAAAAAAAAAQADAN4AAIAbAAAABAAAAB0AAAABAIAAAAAAAAAAAAABAAMALgMAgCQAAAAIAAAAHgAAAAEA"
    "gAAAAAAAAAAAAAEAAQCmAwCAbgAAAAQAAAAfAAAAAAAAAAAAAAAAAAAAAQABAEgDAICFAAAA/////yAAAAAAgAAA"
    "AAAAAAAAAAABAAIBMQAAgDEAAID/////IQAAAAFAAAAAAAAAAAAAAAEAAwDeAACAGwMAgAQAAAAiAAAAAQAAAAAA"
    "AAAAAAAAAQADAFEAAIBqAACAAQAAACMAAAABAAAAAAAAAAAAAAABAAEA1QMAgJcAAAD/////JAAAAACAAAAAAAAA"
    "AAAAAAEAAgExAACAMQAAgP////8lAAAAAMAAAAAAAAAAAAAAAQADAN4AAIAbAwCABAAAACYAAAAAAAAAAAAAAAAA"
    "AAABAAMASAMAgGoAAID/////JwAAAACAAAAAAAAAAAAAAAEABAExAACAMQAAgP////8oAAAAAUAAAAAAAAAAAAAA"
    "AQAFAN4AAIAbAwCABAAAACkAAAABAAAAAAAAAAAAAAABAAUAUQAAgGoAAIABAAAAKgAAAAEAAAAAAAAAAAAAAAEA"
    "AQBMAACApgAAAAEAAAArAAAAAEAAAAAAAAAAAAAAAQABAN4AAIDDAAAABAAAACwAAAAAAAAAAAAAAAAAAAABAAEA"
    "3gAAgNgAAAAEAAAALQAAAAAAAAAAAAAAAAAAAAEAAQDxAACA5AAAAP////8uAAAAAIAAAAAAAAAAAAAAAQACATEA"
    "AIAxAACA/////y8AAAAAgAAAAAAAAAAAAAABAAMA3gAAgBsDAIAEAAAAMAAAAAAAAAAAAAAAAAAAAAEAAwAfAgCA"
    "agAAgP////8xAAAAAIAAAAAAAAAAAAAAAQAEAEgDAICbAACA/////zIAAAAAgAAAAAAAAAAAAAABAAUBMQAAgDEA"
    "AID/////MwAAAAFAAAAAAAAAAAAAAAEABgDeAACAGwMAgAQAAAA0AAAAAQAAAAAAAAAAAAAAAQAGAFEAAIBqAACA"
    "AQAAADUAAAABAAAAAAAAAAAAAAABAAQASAMAgAoDAID/////NgAAAACAAAAAAAAAAAAAAAEABQExAACAMQAAgP//"
    "//83AAAAAUAAAAAAAAAAAAAAAQAGAN4AAIAbAwCABAAAADgAAAABAAAAAAAAAAAAAAABAAYAUQAAgGoAAIABAAAA"
    "OQAAAAEAAAAAAAAAAAAAAEFzc2V0QnVuZGxlAG1fUHJlbG9hZFRhYmxlAG1fRmlsZUlEAG1fUGF0aElEAG1fQ29u"
    "dGFpbmVyAEFzc2V0SW5mbwBwcmVsb2FkSW5kZXgAcHJlbG9hZFNpemUAYXNzZXQAbV9NYWluQXNzZXQAbV9SdW50"
    "aW1lQ29tcGF0aWJpbGl0eQBtX0Fzc2V0QnVuZGxlTmFtZQBtX0RlcGVuZGVuY2llcwBtX0lzU3RyZWFtZWRTY2Vu"
    "ZUFzc2V0QnVuZGxlAG1fRXhwbGljaXREYXRhTGF5b3V0AG1fUGF0aEZsYWdzAG1fU2NlbmVIYXNoZXMA"
)

#: 夹具里的类（三串 = 真件 DestroyInBattleMarker 的值）。
KNOWN = mb.ScriptIdentity(
    class_name="DestroyInBattleMarker",
    namespace="BrokenArrow.Client.Ecs.Spawn",
    assembly_name="BrokenArrow",
)
#: 夹具里那颗真件 MonoScript 的 pathID（随手取的负数，只要不与新对象撞车）。
SOURCE_MONO_PATH_ID = -777000000000000001
#: 本包那颗自造 MonoScript 的 pathID。
PACKAGE_MONO_PATH_ID = 7788990011223344
CONTAINER_PATH = "Assets/Resources_moved/ModelPrefabs/RouteA/mono-fixture.prefab"

COMMON = cl.unitypy_common_strings()


# --------------------------------------------------------------------------- #
# 夹具构造
# --------------------------------------------------------------------------- #
def _tree(b64: str) -> mw.TypeTree:
    """base64 的真件类型树 blob -> mw.TypeTree（照 test_clone_reference.py 的 _tree_of）。"""
    blob = base64.b64decode(b64)
    node_count, size = struct.unpack_from("<ii", blob, 0)
    return mw.TypeTree(
        node_count=node_count,
        string_buffer_size=size,
        nodes=blob[8 : 8 + 32 * node_count],
        strings=blob[8 + 32 * node_count :],
    )


def _nodes(b64: str) -> tuple[cl.TypeNode, ...]:
    """走**产品代码**把 blob 解成节点（与 SerializedFileView.nodes_for 同一条路）。"""
    return cl.read_type_nodes(mw.encode_type_tree(_tree(b64), "<"), common_strings=COMMON)


def _type(class_id: int, tree_b64: str, script_index: int = -1) -> mw.SerializedType:
    return mw.SerializedType(
        class_id=class_id,
        is_stripped=False,
        script_type_index=script_index,
        # 类型表里只有 classID=114 带 script_id（metadata_writer._read_type 的判据）。
        script_id=(b"\x11" * 16 if class_id == mb.CLASS_MONO_BEHAVIOUR else None),
        old_type_hash=b"\x00" * 16,
        type_tree=_tree(tree_b64),
        type_dependencies=(0,),
    )


@pytest.fixture(scope="module")
def nodes_115() -> tuple[cl.TypeNode, ...]:
    return _nodes(MONO_SCRIPT_115_TREE_B64)


def _template_fields() -> dict[str, object]:
    """真件那颗 MonoScript 的字段树（值照 DestroyInBattleMarker 真件读数）。"""
    return {
        "m_Name": KNOWN.class_name,
        "m_ExecutionOrder": 0,
        # ⚠ Hash128 在 clone 的字段树里是 16 格映射（不是 bytes）—— 真件读出来就长这样。
        "m_PropertiesHash": {f"bytes[{i}]": i for i in range(16)},
        "m_ClassName": KNOWN.class_name,
        "m_Namespace": KNOWN.namespace,
        "m_AssemblyName": KNOWN.assembly_name,
    }


def _source_meta() -> tuple[mw.SerializedMetadata, bytes]:
    """一份**小元数据区**：115 + 114(si=0) + 142 三棵树 + 一颗真件 MonoScript 对象。"""
    fields = _template_fields()
    blob = cl.write_object(fields, _nodes(MONO_SCRIPT_115_TREE_B64))
    meta = mw.SerializedMetadata(
        header=mw.SerializedFileHeader(0, 0, 22, 0, 0, b"\x00" * 3, 0, 0, 0, 0),
        unity_version="2022.3.62f3",
        target_platform=19,
        enable_type_tree=True,
        types=[
            _type(mb.CLASS_MONO_SCRIPT, MONO_SCRIPT_115_TREE_B64),
            _type(mb.CLASS_MONO_BEHAVIOUR, MONO_BEHAVIOUR_114_TREE_B64, script_index=0),
            _type(builder.CLASS_ASSETBUNDLE, ASSET_BUNDLE_142_TREE_B64),
        ],
        objects=[
            mw.ObjectEntry(
                path_id=SOURCE_MONO_PATH_ID, byte_start=0, byte_size=len(blob), type_id=0
            )
        ],
        script_types=[mw.ScriptTypeEntry(file_index=0, local_identifier=SOURCE_MONO_PATH_ID)],
        externals=[],
        ref_types=[],
        user_information="",
    )
    # build_metadata 的产物长度 == data_offset（含对齐填充）⇒ 再接上对象数据就是**整份**文件。
    return meta, mw.build_metadata(meta) + blob


@pytest.fixture(scope="module")
def source() -> tuple[mw.SerializedMetadata, bytes]:
    """(解析回来的元数据, 整份 SerializedFile 的字节) —— 与真包同形（先编码再解析）。"""
    _meta, region = _source_meta()
    return mw.parse_serialized_metadata(region), region


def _view_from_serialized(
    source_name: str, meta: mw.SerializedMetadata, blob: bytes
) -> mb.SerializedFileView:
    """把一份 SerializedFile 的字节（元数据区 + 数据区）包成**内存视图**。

    对象字节按对象表切出来 —— 与真包走 read_range 拿到的是同一段（只是不落盘）。
    """
    start = int(meta.header.data_offset)
    return mb.SerializedFileView(
        source=source_name,
        node_path="CAB-fixture",
        node_offset=0,
        data_offset=start,
        meta=meta,
        payloads={
            int(item.path_id): blob[
                start + item.byte_start : start + item.byte_start + item.byte_size
            ]
            for item in meta.objects
        },
    )


@pytest.fixture(scope="module")
def source_view(source: tuple[mw.SerializedMetadata, bytes]) -> mb.SerializedFileView:
    meta, region = source
    return _view_from_serialized("fixture-source", meta, region)


@pytest.fixture(scope="module")
def catalogue(source_view: mb.SerializedFileView) -> mb.ScriptCatalogue:
    return mb.read_catalogue(source_view)


def _build_package(
    source: tuple[mw.SerializedMetadata, bytes],
    mono_fields: dict[str, object],
    *,
    mono_path_id: int = PACKAGE_MONO_PATH_ID,
    behaviour_path_id: int = -424242,
    script_type_map: dict[int, int] | None = None,
    with_script_type_map: bool = True,
) -> builder.ObjectBundlePlan:
    """真造一个包：自造 MonoScript ＋ 绑定好它的 MonoBehaviour（＋ AssetBundle）。

    with_script_type_map=False 用来做**负控**：本包搬了 MonoScript 却没给映射时，
    builder 必须当场抛（否则 m_ScriptTypes 会指着真件那个包的 pathID）。
    """
    _meta, region = source
    mono = mb.mono_script_spec(mono_fields, path_id=mono_path_id)
    behaviour = builder.ObjectSpec(
        class_id=mb.CLASS_MONO_BEHAVIOUR,
        fields={
            "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
            "m_Enabled": 1,
            "m_Script": {"m_FileID": 0, "m_PathID": mono_path_id},
            "m_Name": "",
        },
        path_id=behaviour_path_id,
        script_index=0,
        label="mono-behaviour",
    )
    kwargs: dict[str, object] = {}
    if with_script_type_map:
        kwargs["script_type_map"] = (
            {0: mono_path_id} if script_type_map is None else dict(script_type_map)
        )
    return builder.build_object_bundle(
        objects=[mono, behaviour],
        bundle_name="mono-fixture",
        source_meta=region,
        container=[(CONTAINER_PATH, mono_path_id)],
        preload_all=True,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 1 · 纯逻辑
# --------------------------------------------------------------------------- #
def test_script_identity_roundtrip() -> None:
    identity = mb.ScriptIdentity.parse("BrokenArrow|BrokenArrow.Client.Ecs.Spawn|UnitPrefabRoot")
    assert str(identity) == "BrokenArrow|BrokenArrow.Client.Ecs.Spawn|UnitPrefabRoot"
    assert identity.key == ("BrokenArrow", "BrokenArrow.Client.Ecs.Spawn", "UnitPrefabRoot")
    assert identity.qualified_name == "BrokenArrow.Client.Ecs.Spawn.UnitPrefabRoot"
    short = mb.ScriptIdentity.parse("UnitPrefabRoot")
    assert short.class_name == "UnitPrefabRoot"
    assert (short.namespace, short.assembly_name) == ("", "")
    with pytest.raises(AssetError):
        mb.ScriptIdentity.parse("a|b")
    with pytest.raises(AssetError):
        mb.ScriptIdentity(class_name="")


def test_measured_mono_script_fields_are_reconciled(nodes_115: tuple[cl.TypeNode, ...]) -> None:
    """逐键对账的**真件锚点**：解出来的 6 个字段名与常量逐字相等。"""
    assert mb.check_mono_script_tree(nodes_115) == mb.MONO_SCRIPT_FIELDS
    assert list(mb.MONO_SCRIPT_FIELDS) == MONO_SCRIPT_115_FIELDS
    level1 = [n.name for n in nodes_115 if n.level == 1]
    assert level1 == MONO_SCRIPT_115_FIELDS


def test_check_mono_script_tree_can_go_red(nodes_115: tuple[cl.TypeNode, ...]) -> None:
    """负控：把一个字段改名（或删掉），逐键对账必须报出来。"""
    tampered = [
        cl.TypeNode(
            level=node.level,
            type=node.type,
            name="m_ClassNameX" if node.name == "m_ClassName" else node.name,
            byte_size=node.byte_size,
            meta_flag=node.meta_flag,
            type_flags=node.type_flags,
            version=node.version,
        )
        for node in nodes_115
    ]
    with pytest.raises(AssetError, match="类型树字段与实测不符"):
        mb.check_mono_script_tree(tampered)
    with pytest.raises(AssetError, match="类型树字段与实测不符"):
        mb.check_mono_script_tree([n for n in nodes_115 if n.level <= 0])


def test_catalogue_enumerates_the_source_identity(
    source_view: mb.SerializedFileView, catalogue: mb.ScriptCatalogue
) -> None:
    assert catalogue.problems == ()
    assert len(catalogue.rows) == 1
    assert len(catalogue.mono_scripts) == 1
    record = catalogue.mono_scripts[0]
    assert record.path_id == SOURCE_MONO_PATH_ID
    assert record.identity == KNOWN
    assert catalogue.table() == ((*KNOWN.key, 0),)
    assert catalogue.script_index_of(KNOWN) == 0
    assert catalogue.script_index_of("DestroyInBattleMarker") == 0
    assert catalogue.rows[0].behaviour_type_ids == (1,)
    assert catalogue.template(SOURCE_MONO_PATH_ID) == _template_fields()
    # 逐字节：目录里的模板写回去 == 真件那颗对象的字节
    assert (
        cl.write_object(catalogue.template(SOURCE_MONO_PATH_ID), _nodes(MONO_SCRIPT_115_TREE_B64))
        == source_view.raw(SOURCE_MONO_PATH_ID)
    )


def test_require_rejects_a_class_that_does_not_exist(catalogue: mb.ScriptCatalogue) -> None:
    """⛔ 边界 1 的负控：指向不存在的类必须抛（并把最接近的类名给出来）。"""
    with pytest.raises(AssetError, match="IL2CPP 加不了新类") as info:
        catalogue.require("RouteANoSuchClass")
    assert "DestroyInBattleMarker" in str(info.value)
    with pytest.raises(AssetError, match="IL2CPP 加不了新类"):
        catalogue.require(
            mb.ScriptIdentity("DestroyInBattleMarker", "Wrong.Namespace", "BrokenArrow")
        )


def test_make_mono_script_fields_reconciles_keys(catalogue: mb.ScriptCatalogue) -> None:
    template = catalogue.template(SOURCE_MONO_PATH_ID)
    made = mb.make_mono_script_fields(template, KNOWN)
    assert set(made) == set(mb.MONO_SCRIPT_FIELDS)
    assert made["m_Name"] == KNOWN.class_name
    # m_ExecutionOrder / m_PropertiesHash 原样照抄（⛔ 不自己编）
    assert made["m_ExecutionOrder"] == template["m_ExecutionOrder"]
    assert made["m_PropertiesHash"] == template["m_PropertiesHash"]
    with pytest.raises(AssetError, match="模板的字段是"):
        mb.make_mono_script_fields({k: v for k, v in template.items() if k != "m_Namespace"}, KNOWN)
    with pytest.raises(AssetError, match="模板的字段是"):
        mb.make_mono_script_fields({**template, "m_Extra": 1}, KNOWN)
    with pytest.raises(AssetError, match="字段是"):
        mb.mono_script_spec({"m_ClassName": "x"}, path_id=1)


def test_bind_behaviour_script_needs_m_script() -> None:
    fields = {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
        "m_Script": {"m_FileID": 0, "m_PathID": 0},
    }
    bound = mb.bind_behaviour_script(fields, 12345)
    assert bound["m_Script"] == {"m_FileID": 0, "m_PathID": 12345}
    assert fields["m_Script"] == {"m_FileID": 0, "m_PathID": 0}  # 不改入参
    with pytest.raises(AssetError, match="没有 m_Script"):
        mb.bind_behaviour_script({"m_GameObject": 0}, 1)


def test_plan_mono_scripts_binds_identity_to_package_path(
    catalogue: mb.ScriptCatalogue,
) -> None:
    plan = mb.plan_mono_scripts(catalogue, {KNOWN: PACKAGE_MONO_PATH_ID})
    assert plan.script_type_map == {0: PACKAGE_MONO_PATH_ID}
    assert plan.script_path_ids == (PACKAGE_MONO_PATH_ID,)
    assert plan.script_pptr(KNOWN) == {"m_FileID": 0, "m_PathID": PACKAGE_MONO_PATH_ID}
    binding = plan.binding("DestroyInBattleMarker")
    assert binding.source_path_id == SOURCE_MONO_PATH_ID
    assert binding.type_key == (mb.CLASS_MONO_BEHAVIOUR, 0)
    spec = plan.mono_specs[0]
    assert spec.class_id == mb.CLASS_MONO_SCRIPT
    assert spec.path_id == PACKAGE_MONO_PATH_ID
    assert spec.fields is not None and spec.fields["m_ClassName"] == KNOWN.class_name
    with pytest.raises(AssetError, match="IL2CPP 加不了新类"):
        mb.plan_mono_scripts(catalogue, {"RouteANoSuchClass": 5})
    with pytest.raises(AssetError, match="被两个类点名"):
        mb.plan_mono_scripts(catalogue, {KNOWN: 5, "DestroyInBattleMarker": 5})


# --------------------------------------------------------------------------- #
# 2 · 内存端到端：真造包 -> 落盘 -> 独立读回
# --------------------------------------------------------------------------- #
def test_build_and_read_back_the_package(
    source: tuple[mw.SerializedMetadata, bytes], tmp_path: Path
) -> None:
    mono_fields = mb.make_mono_script_fields(_template_fields(), KNOWN)
    plan = _build_package(source, mono_fields)
    target = tmp_path / "mono-fixture.bundle"
    target.write_bytes(plan.data)

    with mb.open_view(target) as view:
        check = mb.verify_package(view, known=[KNOWN])
        assert check.problems == ()
        assert check.unreadable == 0
        # ① 对象逐字节（独立读回 == 写出去的那份）
        assert view.raw(PACKAGE_MONO_PATH_ID) == cl.write_object(
            mono_fields, _nodes(MONO_SCRIPT_115_TREE_B64)
        )
        # ② script_types 指本包 MonoScript（不是真件那个包）
        assert [int(e.local_identifier) for e in view.meta.script_types] == [PACKAGE_MONO_PATH_ID]
        assert [int(e.file_index) for e in view.meta.script_types] == [0]
        # ③ 那颗 MonoBehaviour 的 m_Script 指回本包
        behaviour = cl.read_object(view.raw(-424242), view.nodes_for(view.entry(-424242).type_id))
        assert behaviour["m_Script"] == {"m_FileID": 0, "m_PathID": PACKAGE_MONO_PATH_ID}
        # ④ 目录/绑定公式在**产物**上重算一遍
        built = mb.read_catalogue(view)
        assert built.problems == ()
        assert built.table() == ((*KNOWN.key, 0),)
        assert built.mono_scripts[0].path_id == PACKAGE_MONO_PATH_ID
        census = mb.census_script_binding(view)
        assert census.total == 1
        assert census.consistent == 1
        assert census.mismatch == 0
        assert census.unreadable == 0
        assert census.ok
        # ⑤ 独立尺子 verify_bundle 也过
        assert mb.verify_bundle(target, known=[KNOWN]).ok


def test_verify_package_reports_a_class_that_does_not_exist(
    source: tuple[mw.SerializedMetadata, bytes], tmp_path: Path
) -> None:
    """⛔ 边界 1 的**能红负控**：编一个类名写进包，verify_package 必须报出来。"""
    bogus = mb.make_mono_script_fields(_template_fields(), KNOWN)
    bogus["m_ClassName"] = "RouteANoSuchClass"
    plan = _build_package(source, bogus)
    target = tmp_path / "bogus.bundle"
    target.write_bytes(plan.data)

    check = mb.verify_bundle(target, known=[KNOWN])
    assert not check.ok
    assert any(
        "游戏里没有这个类" in line and "RouteANoSuchClass" in line for line in check.problems
    )

    # 正控的另一半：把那个类补进目录 ⇒ 同一条断言必须变绿（否则尺子是假绿）
    patched = mb.ScriptIdentity("RouteANoSuchClass", KNOWN.namespace, KNOWN.assembly_name)
    assert mb.verify_bundle(target, known=[KNOWN, patched]).ok
    # 不给目录时它必须**明写**没查那一半，而不是假装查过
    loose = mb.verify_bundle(target)
    assert loose.ok
    assert any("只查包内自洽" in line for line in loose.lines)


def test_build_refuses_to_copy_real_script_types(
    source: tuple[mw.SerializedMetadata, bytes],
) -> None:
    """⛔ 边界 3 的负控：搬了 MonoScript 却不给 script_type_map ⇒ builder 当场抛。"""
    with pytest.raises(AssetError, match="script_type_map"):
        _build_package(
            source,
            mb.make_mono_script_fields(_template_fields(), KNOWN),
            with_script_type_map=False,
        )


def test_verify_package_catches_a_dangling_script_pointer(
    source: tuple[mw.SerializedMetadata, bytes], tmp_path: Path
) -> None:
    """⛔ 判据 2 的负控：m_ScriptTypes 指到本包没有的 pathID ⇒ 必须报悬空。"""
    plan = _build_package(
        source,
        mb.make_mono_script_fields(_template_fields(), KNOWN),
        script_type_map={0: PACKAGE_MONO_PATH_ID + 1},
    )
    target = tmp_path / "dangling.bundle"
    target.write_bytes(plan.data)
    check = mb.verify_bundle(target, known=[KNOWN])
    assert not check.ok
    assert any("不是本包的 MonoScript" in line for line in check.problems)


def test_dangling_pptrs_ruler_accepts_our_script(
    source: tuple[mw.SerializedMetadata, bytes], catalogue: mb.ScriptCatalogue
) -> None:
    """N4 的尺子：登记的 script_path_ids 必须让 m_Script 落得住；不登记就是悬空。"""
    plan = mb.plan_mono_scripts(catalogue, {KNOWN: PACKAGE_MONO_PATH_ID})
    specs = list(plan.mono_specs)
    specs.append(
        builder.ObjectSpec(
            class_id=mb.CLASS_MONO_BEHAVIOUR,
            fields={
                "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
                "m_Enabled": 1,
                "m_Script": plan.script_pptr(KNOWN),
                "m_Name": "",
            },
            path_id=-424242,
            script_index=0,
        )
    )
    assert builder.dangling_pptrs(specs, script_path_ids=plan.script_path_ids) == ()
    # 正控（能红）：把 MonoScript 对象从包里摘掉、又不登记它 ⇒ 同一条尺子必须报悬空
    without_mono = [spec for spec in specs if spec.class_id != mb.CLASS_MONO_SCRIPT]
    problems = builder.dangling_pptrs(without_mono)
    assert len(problems) == 1
    assert "悬空" in problems[0]
    assert builder.dangling_pptrs(without_mono, script_path_ids=plan.script_path_ids) == ()


# --------------------------------------------------------------------------- #
# 3 · slow：真 units 包
# --------------------------------------------------------------------------- #
def _find_bundle(prefix: str) -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    if root is None:
        return None
    hits = sorted(bundle_dir(root).glob(f"{prefix}*.bundle"))
    return hits[0] if hits else None


@contextlib.contextmanager
def _units_lock() -> Iterator[None]:
    """重活锁：拿不到就**失败**（⛔ 不 skip —— 静默跳过的绿不算绿）。

    优先用仓内口径 _work/locks/lock.py（与队友共用同一把锁）；它不在时退化成
    同目录一个 O_EXCL 文件锁，别让用例因为缺一个 helper 就静默放行。
    """
    if LOCK_DIR.joinpath("lock.py").is_file():
        # _work 不在包里：只能运行时挂 sys.path（tests/** 已豁免 PLC0415）
        from lock import acquire, release

        try:
            acquire("units", ttl=3600, wait=LOCK_WAIT_SECONDS, note="pytest test_build_monoscript")
        except TimeoutError as exc:
            pytest.fail(f"units 锁等超过 {LOCK_WAIT_SECONDS:.0f}s：{exc}")
        try:
            yield
        finally:
            with contextlib.suppress(PermissionError):
                release("units")
        return
    path = LOCK_DIR / "units.mono-fallback.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + LOCK_WAIT_SECONDS
    handle: int | None = None
    while handle is None:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.time() > deadline:
                pytest.fail(f"重活锁 {path} 被占着超过 {LOCK_WAIT_SECONDS:.0f}s")
            time.sleep(1.0)
    try:
        yield
    finally:
        os.close(handle)
        path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def units_view() -> Iterator[mb.SerializedFileView]:
    """units 包的只读视图（整个模块只开一次，整段持有重活锁）。"""
    bundle = _find_bundle(UNITS_PREFIX)
    if bundle is None:
        pytest.skip("找不到 units_assets_all*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    with _units_lock(), mb.open_view(bundle) as view:
        yield view


@pytest.mark.slow
def test_real_units_catalogue(units_view: mb.SerializedFileView) -> None:
    if not mb.has_mono_script(units_view):
        # ★ 2026-10-18：Steam 2026-10-17 11:30 更新后，units 包**只剩一个 SerializedFile**
        #   （CAB-1e53…，50 类 / 161,525 对象），`classID=115 (MonoScript)` 不在里面；
        #   MonoBehaviour 的 `m_Script` 变成 `{m_FileID: 1, m_PathID: local_identifier}`，
        #   即脚本搬进了 `externals[0]`（`archive:/CAB-0ec21893…`）那个**外部** SerializedFile，
        #   而它不在本 bundle 的节点表里（要顺着 catalog 的依赖去别的包找）。
        #   ⛔ 不猜、不跨包硬读：本工具还没有这条解析 ⇒ 如实 skip，缺口记在 STATUS。
        pytest.skip(
            "1.2.1 起 units 的 MonoScript 搬到了外部 CAB（m_Script.m_FileID=1），"
            "跨包解析未实现 —— 脚本目录这一格在 1.2.1 上用不了（见 STATUS 2026-10-18）"
        )
    catalogue = mb.read_catalogue(units_view)
    assert catalogue.problems == ()
    assert len(catalogue.rows) == 22
    assert len(catalogue.mono_scripts) == 23
    assert catalogue.unreferenced == (3798737241128378908,)
    root = mb.ScriptIdentity("UnitPrefabRoot", "BrokenArrow.Client.Ecs.Spawn", "BrokenArrow")
    record = catalogue.require(root)
    assert record.path_id == 7283893410243736558
    assert catalogue.script_index_of(root) == 20
    assert catalogue.row(20).behaviour_type_ids == (24,)
    # 23 颗逐字节回读（目录里的模板写回去 == 真件字节）
    nodes = mb.mono_script_nodes(units_view)
    for item in catalogue.mono_scripts:
        assert cl.write_object(catalogue.template(item.path_id), nodes) == units_view.raw(
            item.path_id
        )


@pytest.mark.slow
def test_real_units_binding_census_reproduces_a2(units_view: mb.SerializedFileView) -> None:
    """a2 那条尺子的读数：4431 / 0 / 601（未喂 ref_types），其中 4431 条是**跨文件引用**。

    ★ 2026-10-18 重记：Steam 2026-10-17 11:30 更新过游戏 ⇒ total 5035 → **5032**、
      unreadable 604 → **601**；且 **`m_FileID` 从 0 变成 1**（MonoScript 搬进了 externals[0]
      那个 CAB）⇒ 尺子一度从「4431 一致」掉成「0 一致」——那是判据过窄，不是包坏：
      `census_script_binding` 现已把 `1..externals` 的 fileID 认作合法并单独计入 `external`。
    """
    census = mb.census_script_binding(units_view)
    assert (census.total, census.consistent, census.mismatch, census.unreadable) == (
        5032,
        4431,
        0,
        601,
    )
    assert census.external == 4431, census.line()
    assert not census.ok
    assert "读不了" in census.line()
    pool = cl.ref_type_pool(units_view.meta, common_strings=COMMON)
    fixed = mb.census_script_binding(units_view, ref_types=pool)
    assert (fixed.total, fixed.consistent, fixed.mismatch, fixed.unreadable) == (5032, 5032, 0, 0)
    assert fixed.ok
