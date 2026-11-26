r"""v3-② 的两个产品件用例：**PrefabNode.extra_components**（挂点由 attach_to 定）
与 **builder.copy_closure**（PPtr 依赖闭包复制 + 重映射）。

两者都用**合成类型树**（本文件自己拼 blob），不开游戏包 —— 真件那条在
_work/lead-routeA-v4.py（102 对象 / 悬空 0 / 命名契约 0 缺件）上复算。

判据一行：pytest tests/test_prefab_extras.py -q -> 通过数 从 0 变 N。
"""

from __future__ import annotations

import struct

import pytest

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.asset_kernel import prefab_build as pb
from ba_mod_maker.core.errors import AssetError

NODE_STRUCT = struct.Struct("<hBBIIiiiQ")
COMMON = clone.unitypy_common_strings()


def _blob(rows: list[tuple[int, str, str, int]]) -> bytes:
    strings = bytearray()
    offsets: dict[str, int] = {}
    body = bytearray()
    for level, typ, name, meta in rows:
        for text in (typ, name):
            if text not in offsets:
                offsets[text] = len(strings)
                strings += text.encode("utf-8") + b"\x00"
        body += NODE_STRUCT.pack(19, level, 0, offsets[typ], offsets[name], 0, 0, meta, 0)
    return struct.pack("<ii", len(rows), len(strings)) + bytes(body) + bytes(strings)


def _nodes(rows: list[tuple[int, str, str, int]]):
    return clone.read_type_nodes(_blob(rows), common_strings=COMMON)



_GO = [
    (0, "GameObject", "Base", 0x0),
    (1, "vector", "m_Component", 0x8000),
    (2, "Array", "Array", 0x4000),
    (3, "int", "size", 0x0),
    (3, "ComponentPair", "data", 0x8000),
    (4, "PPtr<Component>", "component", 0x0),
    (5, "int", "m_FileID", 0x0),
    (5, "SInt64", "m_PathID", 0x0),
    (1, "unsigned int", "m_Layer", 0x0),
    (1, "string", "m_Name", 0x8000),
    (2, "Array", "Array", 0x4001),
    (3, "int", "size", 0x1),
    (3, "char", "data", 0x1),
    (1, "string", "m_Tag", 0x8000),
    (2, "Array", "Array", 0x4001),
    (3, "int", "size", 0x1),
    (3, "char", "data", 0x1),
    (1, "bool", "m_IsActive", 0x4000),
]
_TR = [
    (0, "Transform", "Base", 0x0),
    (1, "PPtr<GameObject>", "m_GameObject", 0x0),
    (2, "int", "m_FileID", 0x0),
    (2, "SInt64", "m_PathID", 0x0),
    (1, "Quaternionf", "m_LocalRotation", 0x0),
    (2, "float", "x", 0x0), (2, "float", "y", 0x0), (2, "float", "z", 0x0), (2, "float", "w", 0x0),
    (1, "Vector3f", "m_LocalPosition", 0x0),
    (2, "float", "x", 0x0), (2, "float", "y", 0x0), (2, "float", "z", 0x0),
    (1, "Vector3f", "m_LocalScale", 0x0),
    (2, "float", "x", 0x0), (2, "float", "y", 0x0), (2, "float", "z", 0x0),
    (1, "vector", "m_Children", 0x8000),
    (2, "Array", "Array", 0x4000),
    (3, "int", "size", 0x0),
    (3, "PPtr<Transform>", "data", 0x0),
    (4, "int", "m_FileID", 0x0),
    (4, "SInt64", "m_PathID", 0x0),
    (1, "PPtr<Transform>", "m_Father", 0x0),
    (2, "int", "m_FileID", 0x0),
    (2, "SInt64", "m_PathID", 0x0),
]
_ANIMATOR = [
    (0, "Animator", "Base", 0x0),
    (1, "PPtr<GameObject>", "m_GameObject", 0x0),
    (2, "int", "m_FileID", 0x0), (2, "SInt64", "m_PathID", 0x0),
    (1, "bool", "m_Enabled", 0x4000),
    (1, "PPtr<AnimatorController>", "m_Controller", 0x0),
    (2, "int", "m_FileID", 0x0), (2, "SInt64", "m_PathID", 0x0),
    (1, "PPtr<Avatar>", "m_Avatar", 0x0),
    (2, "int", "m_FileID", 0x0), (2, "SInt64", "m_PathID", 0x0),
    (1, "int", "m_CullingMode", 0x0),
]
_BOX = [
    (0, "BoxCollider", "Base", 0x0),
    (1, "PPtr<GameObject>", "m_GameObject", 0x0),
    (2, "int", "m_FileID", 0x0), (2, "SInt64", "m_PathID", 0x0),
    (1, "bool", "m_Enabled", 0x4000),
    (1, "Vector3f", "m_Size", 0x0),
    (2, "float", "x", 0x0), (2, "float", "y", 0x0), (2, "float", "z", 0x0),
    (1, "Vector3f", "m_Center", 0x0),
    (2, "float", "x", 0x0), (2, "float", "y", 0x0), (2, "float", "z", 0x0),
    (1, "bool", "m_IsTrigger", 0x4000),
]
_NOT_A_COMPONENT = [(0, "Something", "Base", 0x0), (1, "int", "m_Value", 0x0)]


def _entries(extra: dict | None = None) -> dict:
    raw = {
        (1, None): _GO,
        (4, None): _TR,
        (95, None): _ANIMATOR,
        (65, None): _BOX,
        (7, None): _NOT_A_COMPONENT,
    }
    raw.update(extra or {})
    return {
        key: builder.RawTypeEntry(index=index, class_id=key[0], script_index=-1, start=0, end=0,
                                  raw=b"", nodes=_nodes(rows))
        for index, (key, rows) in enumerate(raw.items())
    }


def _spec(**kw) -> pb.PrefabNode:
    return pb.PrefabNode(name="Root", children=(pb.PrefabNode(name="Entrances"),), **kw)


def _build(extra_components=(), entries=None) -> pb.PrefabBuild:
    spec = _spec(extra_components=tuple(extra_components))
    return pb.build_prefab(spec, entries or _entries(), seed="extras")


# --------------------------------------------------------------------------- extra_components
def test_extra_component_lands_on_the_node_named_by_attach_to() -> None:
    """attach_to 指的节点才拿到组件 —— 声明写在根上也算数。"""
    build = _build([pb.ExtraComponent(class_id=65, attach_to="Root/Entrances",
                                      fields={"m_IsTrigger": False})])
    box = next(o for o in build.objects if o.class_id == 65)
    entrances_go = build.node_ids["Root/Entrances"][0]
    assert int(box.fields["m_GameObject"]["m_PathID"]) == entrances_go
    assert box.path_id in build.node_components["Root/Entrances"]
    root_go = build.node_ids["Root"][0]
    assert int(box.fields["m_GameObject"]["m_PathID"]) != root_go, "挂错节点了（挂到根上）"
    # 真件同形：BoxCollider 的 m_GameObject 必须落在它自己那一格
    back = clone.read_object(box.payload, _nodes(_BOX))
    assert back == box.fields


def test_attach_to_empty_means_the_root() -> None:
    build = _build([pb.ExtraComponent(class_id=95)])
    animator = next(o for o in build.objects if o.class_id == 95)
    assert int(animator.fields["m_GameObject"]["m_PathID"]) == build.node_ids["Root"][0]
    assert animator.path_id in build.node_components["Root"]


def test_extra_component_patches_fields_and_keeps_defaults() -> None:
    build = _build([pb.ExtraComponent(class_id=95, fields={
        "m_Avatar": clone.pptr(0), "m_CullingMode": 2})])
    animator = next(o for o in build.objects if o.class_id == 95)
    assert int(animator.fields["m_Avatar"]["m_PathID"]) == 0
    assert int(animator.fields["m_CullingMode"]) == 2
    assert "m_Controller" in animator.fields, "树里的其它字段要保留（默认值）"


def test_unknown_attach_to_is_rejected() -> None:
    with pytest.raises(AssetError) as excinfo:
        _build([pb.ExtraComponent(class_id=65, attach_to="Root/Nope")])
    assert "attach_to" in str(excinfo.value)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(AssetError) as excinfo:
        _build([pb.ExtraComponent(class_id=95, fields={"m_NoSuchField": 1})])
    assert "没有字段" in str(excinfo.value)


def test_component_without_gameobject_is_rejected() -> None:
    with pytest.raises(AssetError) as excinfo:
        _build([pb.ExtraComponent(class_id=7)])
    assert "m_GameObject" in str(excinfo.value)


def test_missing_type_tree_is_rejected() -> None:
    entries = _entries()
    entries.pop((65, None))
    with pytest.raises(AssetError) as excinfo:
        _build([pb.ExtraComponent(class_id=65)], entries)
    assert "类型表里没有这些类的树" in str(excinfo.value)


def test_extras_do_not_disturb_the_rest_of_the_prefab() -> None:
    """挂了额外组件之后：父子双向、组件回指、往返三条自证仍然全绿。"""
    build = _build([pb.ExtraComponent(class_id=65, attach_to="Root/Entrances"),
                    pb.ExtraComponent(class_id=95)])
    report = pb.verify_hierarchy(build)
    assert report.ok, report.lines()
    for obj in build.objects:
        back = clone.read_object(obj.payload, obj.nodes)
        assert clone.write_object(back, obj.nodes) == obj.payload


# --------------------------------------------------------------------------- copy_closure
def _graph() -> dict[int, dict]:
    """一个小图：10 -> 20/30，20 -> 30（环），30 -> 跨包(fileID=1)。"""
    return {
        10: {"m_GameObject": {"m_FileID": 0, "m_PathID": 0},
             "m_Controller": {"m_FileID": 0, "m_PathID": 20},
             "m_Name": "root"},
        20: {"m_Clips": [{"m_FileID": 0, "m_PathID": 30},
                         {"m_FileID": 1, "m_PathID": 999}],
             "m_Self": {"m_FileID": 0, "m_PathID": 10}},
        30: {"m_Inner": {"m_FileID": 0, "m_PathID": 20}},
    }


def test_copy_closure_copies_the_whole_graph_and_remaps() -> None:
    graph = _graph()
    ids = iter([1000, 1001, 1002])
    fields, mapping = builder.copy_closure([10], read_fields=graph.__getitem__,
                                           has_object=graph.__contains__, next_id=lambda: next(ids))
    assert sorted(mapping) == [10, 20, 30]
    assert sorted(mapping.values()) == [1000, 1001, 1002]
    assert int(fields[10]["m_Controller"]["m_PathID"]) == mapping[20]
    assert [int(x["m_PathID"]) for x in fields[20]["m_Clips"]] == [mapping[30], 999]
    assert int(fields[20]["m_Self"]["m_PathID"]) == mapping[10], "环也要重映射"
    assert int(fields[30]["m_Inner"]["m_PathID"]) == mapping[20]
    # 跨包那条（fileID=1）原样保留 —— 要不要声明 external 是调用方的事
    assert int(fields[20]["m_Clips"][1]["m_FileID"]) == 1


def test_copy_closure_refuses_a_missing_object() -> None:
    graph = _graph()
    del graph[20]
    with pytest.raises(AssetError) as excinfo:
        builder.copy_closure([10], read_fields=graph.__getitem__, has_object=graph.__contains__,
                             next_id=iter([1, 2, 3]).__next__)
    assert "不在这个包里" in str(excinfo.value)


def test_copy_closure_stops_at_max_objects() -> None:
    graph = _graph()
    with pytest.raises(AssetError) as excinfo:
        builder.copy_closure([10], read_fields=graph.__getitem__, has_object=graph.__contains__,
                             next_id=iter(range(100)).__next__, max_objects=2)
    assert "闭包超过" in str(excinfo.value)


def test_copy_closure_rejects_duplicate_new_ids() -> None:
    graph = _graph()
    with pytest.raises(AssetError) as excinfo:
        builder.copy_closure([10], read_fields=graph.__getitem__, has_object=graph.__contains__,
                             next_id=lambda: 7)
    assert "重复" in str(excinfo.value)


def test_copy_closure_on_no_roots_is_empty() -> None:
    graph = _graph()
    fields, mapping = builder.copy_closure(
        [], read_fields=graph.__getitem__, has_object=graph.__contains__,
        next_id=iter([1]).__next__)
    assert fields == {} and mapping == {}
