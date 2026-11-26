r"""W10-③ · prefab 从零序列化的判据（asset_kernel/prefab_build.py）。

判据一行：pytest tests/test_build_prefab_from_scratch.py -q -> 通过数从 0 变 N，
其中至少 5 条是**负控**（喂坏的样本必须变红才算尺子，AGENTS §0.9 动作 2）。

| # | 判据 | 怎么量 |
|---|---|---|
| (a) | 类型树**来自真包**且可追溯 | 夹具 = units 真元数据区**逐节点抄录**；记源包项号 |
| (b) | 层级自证 | 读回来：字节往返 / 组件回指 / m_Component[0] / 父子双向 / PPtr 无悬空 |
| (c) | §7.4 硬契约 | UNIT_NODE_CONTRACT + MINIMAL_UNIT_NODES（Base* 挂 **Chassis** 下） |
| (d) | MonoBehaviour 那条缝 | 按 script_index 点名 vs 按 classID 取一项（会取错） |
| 负控 | 5 条 | 悬空 PPtr 会红 / 缺类型树 / kind 不认识 / mesh 没网格 / 未给 m_Script |

## 夹具为什么可以直接用（不是"自己写一棵树"）

TYPE_TREES 的每一行都是从 _work/w10_ref/units_meta.bin（真 units 包的元数据区）里
**原样抄下来的节点**（level / type / name / byte_size / meta_flag），复算入口是
_work/w10_prefab_gen_fixture.py。用例用它重建同一棵树，于是：
- 不需要开 3.38 GB 的包，秒级；
- 造出来的对象字节数必须与真包口径一致（用例里逐类钉住：GameObject 39 / Transform 68 /
  MeshFilter 24 / MeshRenderer 156 …）—— 这就是"抄得对不对"的判据。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.asset_kernel import prefab_build as pb
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: 真件 units 里 Chassis 网格的 pathID（_work/w7_lead_meshes.json 第 1 条）。
CHASSIS_MESH = -101627207120243360
#: 占位材质 pathID（同一次写回里会一起进包；只用来量"声明了就不悬空"）。
MATERIAL = 0x5EED_0000_0000_0001

#: 本单最小 prefab 用到的类（script_index = -1 表示该类只有一项树）。
MINIMAL_KEYS: tuple[tuple[int, int], ...] = (
    (pb.CLASS_GAME_OBJECT, -1),
    (pb.CLASS_TRANSFORM, -1),
    (pb.CLASS_MESH_FILTER, -1),
    (pb.CLASS_MESH_RENDERER, -1),
    (pb.CLASS_LOD_GROUP, -1),
    (pb.CLASS_SKINNED_MESH_RENDERER, -1),
    (pb.CLASS_MONO_BEHAVIOUR, 0),
    (pb.CLASS_MONO_BEHAVIOUR, 1),
)

# (class_id, script_index, source_type_id, nodes)
# nodes 每项 = (level, type, name, byte_size, meta_flag)
TYPE_TREES: dict[tuple[int, int], tuple[int, tuple[tuple[int, str, str, int, int], ...]]] = {
    # classID 1 GameObject / source_type_id 4 / script_index -1
    (1, -1): (
        4,
        (
            (0, "GameObject", "Base", -1, 32768),
            (1, "vector", "m_Component", -1, 268468289),
            (2, "Array", "Array", -1, 268451905),
            (3, "int", "size", 4, 268435521),
            (3, "ComponentPair", "data", 12, 268435521),
            (4, "PPtr<Component>", "component", 12, 268435521),
            (5, "int", "m_FileID", 4, 268435521),
            (5, "SInt64", "m_PathID", 8, 268435521),
            (1, "unsigned int", "m_Layer", 4, 0),
            (1, "string", "m_Name", -1, 32768),
            (2, "Array", "Array", -1, 16385),
            (3, "int", "size", 4, 1),
            (3, "char", "data", 1, 1),
            (1, "UInt16", "m_Tag", 2, 0),
            (1, "bool", "m_IsActive", 1, 0),
        ),
    ),
    # classID 4 Transform / source_type_id 1 / script_index -1
    (4, -1): (
        1,
        (
            (0, "Transform", "Base", -1, 32768),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "Quaternionf", "m_LocalRotation", 16, 2097152),
            (2, "float", "x", 4, 2097152),
            (2, "float", "y", 4, 2097152),
            (2, "float", "z", 4, 2097152),
            (2, "float", "w", 4, 2097152),
            (1, "Vector3f", "m_LocalPosition", 12, 2097152),
            (2, "float", "x", 4, 2097152),
            (2, "float", "y", 4, 2097152),
            (2, "float", "z", 4, 2097152),
            (1, "Vector3f", "m_LocalScale", 12, 2113536),
            (2, "float", "x", 4, 2097152),
            (2, "float", "y", 4, 2097152),
            (2, "float", "z", 4, 2097152),
            (1, "vector", "m_Children", -1, 32833),
            (2, "Array", "Array", -1, 16449),
            (3, "int", "size", 4, 65),
            (3, "PPtr<Transform>", "data", 12, 65),
            (4, "int", "m_FileID", 4, 65),
            (4, "SInt64", "m_PathID", 8, 65),
            (1, "PPtr<Transform>", "m_Father", 12, 1),
            (2, "int", "m_FileID", 4, 1),
            (2, "SInt64", "m_PathID", 8, 1),
        ),
    ),
    # classID 33 MeshFilter / source_type_id 6 / script_index -1
    (33, -1): (
        6,
        (
            (0, "MeshFilter", "Base", 24, 0),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "PPtr<Mesh>", "m_Mesh", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
        ),
    ),
    # classID 23 MeshRenderer / source_type_id 13 / script_index -1
    (23, -1): (
        13,
        (
            (0, "MeshRenderer", "Base", -1, 32768),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "bool", "m_Enabled", 1, 1),
            (1, "UInt8", "m_CastShadows", 1, 0),
            (1, "UInt8", "m_ReceiveShadows", 1, 256),
            (1, "UInt8", "m_DynamicOccludee", 1, 256),
            (1, "UInt8", "m_StaticShadowCaster", 1, 8388864),
            (1, "UInt8", "m_MotionVectors", 1, 0),
            (1, "UInt8", "m_LightProbeUsage", 1, 0),
            (1, "UInt8", "m_ReflectionProbeUsage", 1, 0),
            (1, "UInt8", "m_RayTracingMode", 1, 0),
            (1, "UInt8", "m_RayTraceProcedural", 1, 16640),
            (1, "unsigned int", "m_RenderingLayerMask", 4, 0),
            (1, "int", "m_RendererPriority", 4, 0),
            (1, "UInt16", "m_LightmapIndex", 2, 8388609),
            (1, "UInt16", "m_LightmapIndexDynamic", 2, 8388609),
            (1, "Vector4f", "m_LightmapTilingOffset", 16, 10485761),
            (2, "float", "x", 4, 10485761),
            (2, "float", "y", 4, 10485761),
            (2, "float", "z", 4, 10485761),
            (2, "float", "w", 4, 10485761),
            (1, "Vector4f", "m_LightmapTilingOffsetDynamic", 16, 10485761),
            (2, "float", "x", 4, 10485761),
            (2, "float", "y", 4, 10485761),
            (2, "float", "z", 4, 10485761),
            (2, "float", "w", 4, 10485761),
            (1, "vector", "m_Materials", -1, 32800),
            (2, "Array", "Array", -1, 16416),
            (3, "int", "size", 4, 32),
            (3, "PPtr<Material>", "data", 12, 32),
            (4, "int", "m_FileID", 4, 8388641),
            (4, "SInt64", "m_PathID", 8, 8388641),
            (1, "StaticBatchInfo", "m_StaticBatchInfo", 4, 1),
            (2, "UInt16", "firstSubMesh", 2, 1),
            (2, "UInt16", "subMeshCount", 2, 1),
            (1, "PPtr<Transform>", "m_StaticBatchRoot", 12, 1),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "PPtr<Transform>", "m_ProbeAnchor", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "PPtr<GameObject>", "m_LightProbeVolumeOverride", 12, 16384),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "int", "m_SortingLayerID", 4, 8388609),
            (1, "SInt16", "m_SortingLayer", 2, 1),
            (1, "SInt16", "m_SortingOrder", 2, 16385),
            (1, "PPtr<Mesh>", "m_AdditionalVertexStreams", 12, 1),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "PPtr<Mesh>", "m_EnlightenVertexStream", 12, 1),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
        ),
    ),
    # classID 205 LODGroup / source_type_id 5 / script_index -1
    (205, -1): (
        5,
        (
            (0, "LODGroup", "Base", -1, 32768),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "Vector3f", "m_LocalReferencePoint", 12, 2097152),
            (2, "float", "x", 4, 2097152),
            (2, "float", "y", 4, 2097152),
            (2, "float", "z", 4, 2097152),
            (1, "float", "m_Size", 4, 0),
            (1, "int", "m_FadeMode", 4, 0),
            (1, "bool", "m_AnimateCrossFading", 1, 0),
            (1, "bool", "m_LastLODIsBillboard", 1, 16384),
            (1, "vector", "m_LODs", -1, 32768),
            (2, "Array", "Array", -1, 49152),
            (3, "int", "size", 4, 0),
            (3, "LOD", "data", -1, 32768),
            (4, "float", "screenRelativeHeight", 4, 0),
            (4, "float", "fadeTransitionWidth", 4, 0),
            (4, "vector", "renderers", -1, 557056),
            (5, "Array", "Array", -1, 540672),
            (6, "int", "size", 4, 524288),
            (6, "LODRenderer", "data", 12, 524288),
            (7, "PPtr<Renderer>", "renderer", 12, 524288),
            (8, "int", "m_FileID", 4, 8912897),
            (8, "SInt64", "m_PathID", 8, 8912897),
            (1, "bool", "m_Enabled", 1, 16385),
        ),
    ),
    # classID 137 SkinnedMeshRenderer / source_type_id 9 / script_index -1
    (137, -1): (
        9,
        (
            (0, "SkinnedMeshRenderer", "Base", -1, 32768),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "bool", "m_Enabled", 1, 1),
            (1, "UInt8", "m_CastShadows", 1, 0),
            (1, "UInt8", "m_ReceiveShadows", 1, 256),
            (1, "UInt8", "m_DynamicOccludee", 1, 256),
            (1, "UInt8", "m_StaticShadowCaster", 1, 8388864),
            (1, "UInt8", "m_MotionVectors", 1, 0),
            (1, "UInt8", "m_LightProbeUsage", 1, 0),
            (1, "UInt8", "m_ReflectionProbeUsage", 1, 0),
            (1, "UInt8", "m_RayTracingMode", 1, 0),
            (1, "UInt8", "m_RayTraceProcedural", 1, 16640),
            (1, "unsigned int", "m_RenderingLayerMask", 4, 0),
            (1, "int", "m_RendererPriority", 4, 0),
            (1, "UInt16", "m_LightmapIndex", 2, 8388609),
            (1, "UInt16", "m_LightmapIndexDynamic", 2, 8388609),
            (1, "Vector4f", "m_LightmapTilingOffset", 16, 10485761),
            (2, "float", "x", 4, 10485761),
            (2, "float", "y", 4, 10485761),
            (2, "float", "z", 4, 10485761),
            (2, "float", "w", 4, 10485761),
            (1, "Vector4f", "m_LightmapTilingOffsetDynamic", 16, 10485761),
            (2, "float", "x", 4, 10485761),
            (2, "float", "y", 4, 10485761),
            (2, "float", "z", 4, 10485761),
            (2, "float", "w", 4, 10485761),
            (1, "vector", "m_Materials", -1, 32800),
            (2, "Array", "Array", -1, 16416),
            (3, "int", "size", 4, 32),
            (3, "PPtr<Material>", "data", 12, 32),
            (4, "int", "m_FileID", 4, 8388641),
            (4, "SInt64", "m_PathID", 8, 8388641),
            (1, "StaticBatchInfo", "m_StaticBatchInfo", 4, 1),
            (2, "UInt16", "firstSubMesh", 2, 1),
            (2, "UInt16", "subMeshCount", 2, 1),
            (1, "PPtr<Transform>", "m_StaticBatchRoot", 12, 1),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "PPtr<Transform>", "m_ProbeAnchor", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "PPtr<GameObject>", "m_LightProbeVolumeOverride", 12, 16384),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "int", "m_SortingLayerID", 4, 8388609),
            (1, "SInt16", "m_SortingLayer", 2, 1),
            (1, "SInt16", "m_SortingOrder", 2, 16385),
            (1, "int", "m_Quality", 4, 8388608),
            (1, "bool", "m_UpdateWhenOffscreen", 1, 0),
            (1, "bool", "m_SkinnedMotionVectors", 1, 16384),
            (1, "PPtr<Mesh>", "m_Mesh", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "vector", "m_Bones", -1, 49153),
            (2, "Array", "Array", -1, 16385),
            (3, "int", "size", 4, 1),
            (3, "PPtr<Transform>", "data", 12, 1),
            (4, "int", "m_FileID", 4, 8388609),
            (4, "SInt64", "m_PathID", 8, 8388609),
            (1, "vector", "m_BlendShapeWeights", -1, 32768),
            (2, "Array", "Array", -1, 16384),
            (3, "int", "size", 4, 0),
            (3, "float", "data", 4, 0),
            (1, "PPtr<Transform>", "m_RootBone", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "AABB", "m_AABB", 24, 0),
            (2, "Vector3f", "m_Center", 12, 2097152),
            (3, "float", "x", 4, 2097152),
            (3, "float", "y", 4, 2097152),
            (3, "float", "z", 4, 2097152),
            (2, "Vector3f", "m_Extent", 12, 2097152),
            (3, "float", "x", 4, 2097152),
            (3, "float", "y", 4, 2097152),
            (3, "float", "z", 4, 2097152),
            (1, "bool", "m_DirtyAABB", 1, 16385),
        ),
    ),
    # classID 114 MonoBehaviour / source_type_id 37 / script_index 0
    (114, 0): (
        37,
        (
            (0, "MonoBehaviour", "Base", -1, 32768),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "UInt8", "m_Enabled", 1, 16641),
            (1, "PPtr<MonoScript>", "m_Script", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "string", "m_Name", -1, 557057),
            (2, "Array", "Array", -1, 540673),
            (3, "int", "size", 4, 524289),
            (3, "char", "data", 1, 524289),
            (1, "int", "PilotID", 4, 0),
        ),
    ),
    # classID 114 MonoBehaviour / source_type_id 46 / script_index 1
    (114, 1): (
        46,
        (
            (0, "MonoBehaviour", "Base", -1, 32768),
            (1, "PPtr<GameObject>", "m_GameObject", 12, 65),
            (2, "int", "m_FileID", 4, 65),
            (2, "SInt64", "m_PathID", 8, 65),
            (1, "UInt8", "m_Enabled", 1, 16641),
            (1, "PPtr<MonoScript>", "m_Script", 12, 0),
            (2, "int", "m_FileID", 4, 8388609),
            (2, "SInt64", "m_PathID", 8, 8388609),
            (1, "string", "m_Name", -1, 557057),
            (2, "Array", "Array", -1, 540673),
            (3, "int", "size", 4, 524289),
            (3, "char", "data", 1, 524289),
        ),
    ),
}


# --------------------------------------------------------------------------- #
# 夹具 -> builder 认得的类型表项
# --------------------------------------------------------------------------- #
def _entry(class_id: int, script_index: int) -> builder.RawTypeEntry:
    source_type_id, rows = TYPE_TREES[(class_id, script_index)]
    nodes = tuple(
        clone.TypeNode(
            level=level,
            type=type_name,
            name=name,
            byte_size=size,
            meta_flag=meta,
            type_flags=0,
            version=19,
        )
        for level, type_name, name, size, meta in rows
    )
    return builder.RawTypeEntry(
        index=source_type_id,
        class_id=class_id,
        script_index=script_index,
        start=0,
        end=0,
        raw=b"",
        nodes=nodes,
        wire=None,
    )


def _entries(keys=MINIMAL_KEYS) -> dict[pb.TypeKey, builder.RawTypeEntry]:
    out: dict[pb.TypeKey, builder.RawTypeEntry] = {}
    for class_id, script_index in keys:
        key: pb.TypeKey = (class_id, None if script_index < 0 else script_index)
        out[key] = _entry(class_id, script_index)
    return out


def _spec(**kwargs) -> pb.PrefabNode:
    """最小 prefab：root -> Chassis(+MeshFilter+MeshRenderer) -> 三个空 Base* 节点。"""
    return pb.PrefabNode(
        name=kwargs.pop("name", "RU_T72B_Base"),
        children=(
            pb.PrefabNode(
                name="Chassis",
                kind=pb.KIND_MESH,
                mesh=CHASSIS_MESH,
                materials=(MATERIAL,),
                # ⚠ 甲（实测口径）：三个 Base* 挂在 **Chassis** 下，不是 root 下
                children=(
                    pb.PrefabNode(name="BaseArmor"),
                    pb.PrefabNode(name="BaseAbility"),
                    pb.PrefabNode(name="BaseMobility"),
                ),
            ),
        ),
        **kwargs,
    )


def _build(**kwargs) -> pb.PrefabBuild:
    extra = kwargs.pop("extra_package_ids", (CHASSIS_MESH, MATERIAL))
    return pb.build_prefab(
        _spec(**kwargs.pop("spec_kwargs", {})),
        _entries(kwargs.pop("keys", MINIMAL_KEYS)),
        extra_package_ids=extra,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# (a) 类型树来自真包 + 可追溯
# --------------------------------------------------------------------------- #
def test_fixture_type_trees_come_from_the_real_package() -> None:
    """夹具的源包项号与真包口径一致（units：1→4 / 4→1 / 33→6 / 23→13 / 205→5 / 137→9）。"""
    expected = {
        (pb.CLASS_GAME_OBJECT, -1): 4,
        (pb.CLASS_TRANSFORM, -1): 1,
        (pb.CLASS_MESH_FILTER, -1): 6,
        (pb.CLASS_MESH_RENDERER, -1): 13,
        (pb.CLASS_LOD_GROUP, -1): 5,
        (pb.CLASS_SKINNED_MESH_RENDERER, -1): 9,
        (pb.CLASS_MONO_BEHAVIOUR, 0): 37,
        (pb.CLASS_MONO_BEHAVIOUR, 1): 46,
    }
    for key, source_type_id in expected.items():
        assert TYPE_TREES[key][0] == source_type_id, key


def test_type_entries_are_traceable_per_object() -> None:
    """每个造出来的对象都记着"用的是源包第几项"——可追溯（判据 a）。"""
    build = _build()
    for obj in build.objects:
        assert obj.type_id == TYPE_TREES[(obj.class_id, obj.script_index)][0]
        assert obj.nodes, f"{obj.type_name} 没有类型树"
    seen = {(o.class_id, o.script_index) for o in build.objects}
    assert seen == {
        (pb.CLASS_GAME_OBJECT, -1),
        (pb.CLASS_TRANSFORM, -1),
        (pb.CLASS_MESH_FILTER, -1),
        (pb.CLASS_MESH_RENDERER, -1),
    }


def test_object_sizes_match_the_real_layout() -> None:
    """造出来的字节数逐类钉住 —— 抄错了类型树（少一个字段 / 对齐位不对）这里就红。

    读数与真包口径一致（_work/w10_prefab_build_probe.py 用真元数据区跑出来的同一批数）。
    """
    build = _build()
    sizes = sorted((o.type_name, o.size) for o in build.objects)
    assert sizes == sorted(
        [
            ("GameObject", 39),   # 根（1 个组件对）
            ("GameObject", 39),   # 三个空 Base* 节点
            ("GameObject", 39),
            ("GameObject", 39),
            ("GameObject", 59),   # Chassis（Transform + MeshFilter + MeshRenderer = 3 个组件对）
            ("Transform", 68),    # 三个空 Base* 节点（无子节点）
            ("Transform", 68),
            ("Transform", 68),
            ("Transform", 80),    # 根（1 个子节点 = 12 B）
            ("Transform", 104),   # Chassis（3 个子节点 = 36 B）
            ("MeshFilter", 24),
            ("MeshRenderer", 156),
        ]
    )


# --------------------------------------------------------------------------- #
# (b) 层级自证
# --------------------------------------------------------------------------- #
def test_hierarchy_self_check_is_green() -> None:
    build = _build()
    report = pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert report.problems == ()
    assert report.ok
    assert report.objects == 12
    assert (report.game_objects, report.transforms) == (5, 5)
    assert report.roundtrip_ok == report.objects
    assert report.component_backrefs_ok == report.components_total == 7
    assert report.transform_is_first == report.game_objects
    assert report.child_links_ok == report.child_links_total == 4
    assert report.father_links_ok == report.father_links_total == 5
    assert report.pptr_dangling == 0 and report.pptr_external == 0
    assert report.pptr_in_package == 24 and report.pptr_null == 6


def test_component_order_is_transform_filter_renderer() -> None:
    build = _build()
    by_id = build.by_id()
    ids = build.node_components["RU_T72B_Base/Chassis"]
    kinds = [by_id[pid].class_id for pid in ids]
    assert kinds == [pb.CLASS_TRANSFORM, pb.CLASS_MESH_FILTER, pb.CLASS_MESH_RENDERER]
    go = by_id[build.node_ids["RU_T72B_Base/Chassis"][0]]
    comps = [pair["component"]["m_PathID"] for pair in go.fields["m_Component"]]
    assert comps == list(ids)


def test_parent_child_pointers_are_bidirectional() -> None:
    build = _build()
    by_id = build.by_id()
    read = {pid: clone.read_object(obj.payload, obj.nodes) for pid, obj in by_id.items()}
    root_tr = build.node_ids["RU_T72B_Base"][1]
    chassis_tr = build.node_ids["RU_T72B_Base/Chassis"][1]
    assert read[root_tr]["m_Father"]["m_PathID"] == 0
    assert read[chassis_tr]["m_Father"]["m_PathID"] == root_tr
    assert [p["m_PathID"] for p in read[root_tr]["m_Children"]] == [chassis_tr]
    chassis_children = [p["m_PathID"] for p in read[chassis_tr]["m_Children"]]
    assert chassis_children == [
        build.node_ids[f"RU_T72B_Base/Chassis/{name}"][1]
        for name in ("BaseArmor", "BaseAbility", "BaseMobility")
    ]
    for child in chassis_children:
        assert read[child]["m_Father"]["m_PathID"] == chassis_tr


def test_mesh_and_material_pointers_are_in_package() -> None:
    build = _build()
    by_id = build.by_id()
    mf = by_id[build.node_components["RU_T72B_Base/Chassis"][1]]
    mr = by_id[build.node_components["RU_T72B_Base/Chassis"][2]]
    assert mf.fields["m_Mesh"] == {"m_FileID": 0, "m_PathID": CHASSIS_MESH}
    assert mr.fields["m_Materials"] == [{"m_FileID": 0, "m_PathID": MATERIAL}]
    assert mr.fields["m_Enabled"] is True
    # ★ 渲染器字段必须是**引擎真值**，不是类型树的零值 —— 2026-10-08 机库现场：
    #   我们自造的渲染器全是 0（m_RenderingLayerMask=0 等）⇒ **车体在机库里看不见**。
    #   真值来自真件普查（6421 个 SMR + 2534 个 MeshRenderer；出处 _work/a2-skin-mono-spec.md）
    assert mr.fields["m_RenderingLayerMask"] == 513, mr.fields["m_RenderingLayerMask"]
    assert mr.fields["m_LightmapIndex"] == 65535
    assert mr.fields["m_LightmapIndexDynamic"] == 65535
    assert mr.fields["m_LightmapTilingOffset"] == {"x": 1.0, "y": 1.0, "z": 0.0, "w": 0.0}
    for key in ("m_CastShadows", "m_ReceiveShadows", "m_DynamicOccludee", "m_MotionVectors",
                "m_LightProbeUsage", "m_ReflectionProbeUsage"):
        assert mr.fields[key] == 1, (key, mr.fields[key])
    assert mr.fields["m_RayTracingMode"] == 2, "MeshRenderer 是 2（SMR 才是 3）"


def test_authored_skinned_renderer_uses_the_smr_raytracing_mode() -> None:
    """⛔ 两个类的 m_RayTracingMode **不能**共用常量：MeshRenderer=2 / SMR=3。"""
    build = _build(spec_kwargs={"kind": "skinned", "local_bounds": {
        "m_Center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "m_Extent": {"x": 1.0, "y": 1.0, "z": 1.0}}})
    smr = next(o for o in build.objects if o.class_id == 137)
    assert smr.class_id == 137
    assert smr.fields["m_RayTracingMode"] == 3, smr.fields["m_RayTracingMode"]
    assert smr.fields["m_RenderingLayerMask"] == 513


# --------------------------------------------------------------------------- #
# (c) §7.4 硬契约表
# --------------------------------------------------------------------------- #
def test_minimal_node_set_is_chassis_children_per_measurement() -> None:
    """本轮实测口径：Base* 挂在 **Chassis** 下（不是 root 下）。"""
    rows = dict(pb.MINIMAL_UNIT_NODES)
    assert rows.pop("root") == ""
    assert rows == {
        "Chassis": "root",
        "BaseArmor": "Chassis",
        "BaseAbility": "Chassis",
        "BaseMobility": "Chassis",
    }


def test_contract_table_covers_every_needed_node() -> None:
    rows = {row.names: row for row in pb.UNIT_NODE_CONTRACT}
    for names, parent in pb.MINIMAL_UNIT_NODES[1:]:
        row = rows[names]
        expected = "prefab 根的直接子节点" if parent == "root" else "Chassis 的直接子节点"
        assert row.parent == expected
        assert row.needed_for_minimal is True
        assert row.readers and row.evidence
        assert row.if_missing


def test_contract_rules_are_only_the_measured_ones() -> None:
    allowed = {
        "exact_cs",
        "exact_ci",
        "prefix_cs",
        "substring_cs",
        "substring_ci",
        "data_only",
    }
    for row in pb.UNIT_NODE_CONTRACT:
        # rule 的第一段（"（"之前）是判据 token；括号里是补充说明
        tokens = {part.strip() for part in row.rule.split("（")[0].split("/")}
        assert tokens <= allowed, (row.names, row.rule)
    rules = {row.names: row.rule for row in pb.UNIT_NODE_CONTRACT}
    assert "substring_ci" in rules["trackl / trackr"]
    assert "exact_cs" in rules["root"]
    assert "data_only" in rules["weapon_* / shell_spawn_*"]


def test_every_contract_row_says_what_happens_if_the_node_is_missing() -> None:
    """工单 §3 要求这一列：读不到会怎样（null 安全 / 抛 / 未量）。每一行都必须有话说。"""
    for row in pb.UNIT_NODE_CONTRACT:
        assert row.if_missing, row.names
    rows = {row.names: row for row in pb.UNIT_NODE_CONTRACT}
    assert "静默" in rows["Chassis"].if_missing
    assert "未量" in rows["LR0 / RR0"].if_missing


def test_contract_marks_unmeasured_null_safety() -> None:
    """缺轮子/履带只是没动画 —— 是否 null 安全**本轮没读**，表里必须如实写着。"""
    rows = {row.names: row for row in pb.UNIT_NODE_CONTRACT}
    for names in ("LR0 / RR0", "L0 / R0", "trackl / trackr"):
        assert "没读" in rows[names].if_missing and "未量" in rows[names].if_missing
    assert rows["root"].needed_for_minimal is False
    assert rows["weapon_* / shell_spawn_*"].needed_for_minimal is False


# --------------------------------------------------------------------------- #
# (d) MonoBehaviour 那条缝
# --------------------------------------------------------------------------- #
def _table(
    keys=((pb.CLASS_MONO_BEHAVIOUR, 1), (pb.CLASS_MONO_BEHAVIOUR, 0)),
) -> builder.RawTypeTable:
    """默认顺序 = 真包 units 的形状：项号与 script_index 是**置换**（1 在前、0 在后）。"""
    entries = tuple(_entry(cid, si) for cid, si in keys)
    return builder.RawTypeTable(
        unity_version="2022.3.62f3",
        target_platform=19,
        enable_type_tree=True,
        entries=entries,
        object_count_offset=0,
        model=None,
    )


def test_entry_for_class_id_alone_must_fail_when_two_trees() -> None:
    """按 classID 取一项会取错 —— 所以它必须**抛**，而不是悄悄给第一项。"""
    table = _table()
    with pytest.raises(AssetError) as excinfo:
        table.entry_for(pb.CLASS_MONO_BEHAVIOUR)
    assert "script_index" in str(excinfo.value)
    assert table.entry_for(pb.CLASS_MONO_BEHAVIOUR, script_index=1).index == 46
    assert table.entry_for(pb.CLASS_MONO_BEHAVIOUR, script_index=0).index == 37
    with pytest.raises(AssetError):
        table.entry_for(pb.CLASS_MONO_BEHAVIOUR, script_index=9)


def test_mono_behaviour_seam_reads_the_permutation() -> None:
    seam = pb.mono_behaviour_seam(_table())
    assert seam.ordered_by_script_index is False
    assert seam.duplicate_class_id_blocked is True
    assert dict((row[1], row[0]) for row in seam.entries) == {0: 37, 1: 46}
    assert len(seam.missing) == 3


def test_negative_mono_behaviour_without_script_pptr_raises() -> None:
    spec = _spec(
        children=None,
    ) if False else pb.PrefabNode(
        name="RU_T72B_Base",
        mono_behaviours=(pb.MonoBehaviourSpec(script_index=0),),
    )
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert "m_Script" in str(excinfo.value)


def test_negative_mono_behaviour_script_pptr_must_be_in_package() -> None:
    spec = pb.PrefabNode(
        name="RU_T72B_Base",
        mono_behaviours=(pb.MonoBehaviourSpec(script_index=0, script=pb.pptr(0x1234)),),
    )
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert "悬空" in str(excinfo.value) or "自证没过" in str(excinfo.value)


def test_mono_behaviour_builds_when_script_pptr_is_declared() -> None:
    script_id = 0x5EED_0000_0000_0002
    spec = pb.PrefabNode(
        name="RU_T72B_Base",
        mono_behaviours=(pb.MonoBehaviourSpec(script_index=1, script=pb.pptr(script_id)),),
    )
    build = pb.build_prefab(
        spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL, script_id)
    )
    mono = [o for o in build.objects if o.class_id == pb.CLASS_MONO_BEHAVIOUR]
    assert len(mono) == 1
    assert mono[0].type_id == 46
    assert mono[0].fields["m_Script"] == {"m_FileID": 0, "m_PathID": script_id}


def test_negative_mono_behaviour_unknown_field_raises() -> None:
    spec = pb.PrefabNode(
        name="RU_T72B_Base",
        mono_behaviours=(
            pb.MonoBehaviourSpec(
                script_index=0, script=pb.pptr(0x5EED), fields={"不是字段": 1}
            ),
        ),
    )
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL, 0x5EED))
    assert "没有字段" in str(excinfo.value)


def test_two_monobehaviour_trees_of_one_class_id_fit_in_one_package() -> None:
    """✅ 2026-10-04：一个包**装得下**两项 classID=114 的树（按类型表项下标取 type_id）。

    这条以前是块死路（builder 按 classID 索引 type_id）。这里用**真 units 元数据区**：
    type 项带 wire 模型，所以能真的走完 serialize_serialized_file。
    """
    meta_path = WORK / "w10_ref" / "units_meta.bin"
    if not meta_path.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（先跑 _work/w10_lead_ref.py）")
    table = builder.read_raw_type_table(
        meta_path.read_bytes(), common_strings=clone.unitypy_common_strings()
    )
    wanted = [(1, None), (4, None), (114, 0), (114, 1)]
    entries = {key: table.entry_for(key[0], script_index=key[1]) for key in wanted}
    scripts = table.script_types()
    assert scripts, "units 的 script types 不该是空的"
    spec = pb.PrefabNode(
        name="W10TWO",
        children=(
            pb.PrefabNode(
                name="A",
                mono_behaviours=(pb.MonoBehaviourSpec(0, pb.pptr(scripts[0].local_identifier)),),
            ),
            pb.PrefabNode(
                name="B",
                mono_behaviours=(pb.MonoBehaviourSpec(1, pb.pptr(scripts[1].local_identifier)),),
            ),
        ),
    )
    build = pb.build_prefab(
        spec,
        entries,
        script_path_ids=(scripts[0].local_identifier, scripts[1].local_identifier),
    )
    monos = [o for o in build.objects if o.class_id == pb.CLASS_MONO_BEHAVIOUR]
    assert {o.script_index for o in monos} == {0, 1}
    plan = pb.serialize_prefab(build, entries, script_types=scripts)
    by_pid = {row.path_id: row for row in plan.object_rows}
    tids = {by_pid[o.path_id].type_id for o in monos}
    assert len(tids) == 2, f"两个不同脚本必须用两项类型树，实测 type_id={tids}"
    assert sum(1 for cid in plan.type_class_ids if cid == pb.CLASS_MONO_BEHAVIOUR) == 2



# --------------------------------------------------------------------------- #
# 负控：尺子必须能红
# --------------------------------------------------------------------------- #
def test_negative_dangling_pptr_is_red() -> None:
    """不声明"同包还会有的 Mesh/Material"时必须红 —— 这是无悬空那条尺子的正控。"""
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(_spec(), _entries())  # 没给 extra_package_ids -> 应该抛
    assert "悬空" in str(excinfo.value)


def test_negative_dangling_pptr_counts() -> None:
    """把 Material 从"同包声明"里拿掉 ⇒ 尺子读数必须正好 1 条悬空，且 ok 为假。"""
    build = _build()
    report = pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH,))
    assert report.pptr_dangling == 1
    assert not report.ok
    assert any("m_Materials" in problem for problem in report.problems)
    good = pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert good.pptr_dangling == 0 and good.ok


def test_negative_missing_type_tree_raises() -> None:
    keys = tuple(k for k in MINIMAL_KEYS if k != (pb.CLASS_MESH_RENDERER, -1))
    with pytest.raises(AssetError) as excinfo:
        _build(keys=keys)
    assert "类型表里没有" in str(excinfo.value)


def test_negative_unknown_kind_raises() -> None:
    spec = pb.PrefabNode(name="root", kind="terrain")
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert "kind" in str(excinfo.value)


def test_negative_mesh_kind_without_mesh_raises() -> None:
    spec = pb.PrefabNode(name="root", kind=pb.KIND_MESH, mesh=None)
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert "没有网格" in str(excinfo.value)


def test_negative_lod_member_without_renderer_raises() -> None:
    spec = pb.PrefabNode(
        name="root",
        children=(pb.PrefabNode(name="Empty"),),
        lod_levels=(("root/Empty",),),
        lod_size=5.0,
    )
    with pytest.raises(AssetError) as excinfo:
        pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert "renderer" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 另外两条形态：LODGroup 与 SkinnedMeshRenderer
# --------------------------------------------------------------------------- #
def test_lod_group_points_at_the_chassis_renderer() -> None:
    build = _build(spec_kwargs={"lod_levels": (("RU_T72B_Base/Chassis",),), "lod_size": 5.0})
    lod = [o for o in build.objects if o.class_id == pb.CLASS_LOD_GROUP]
    assert len(lod) == 1
    assert lod[0].fields["m_Size"] == 5.0
    mr_id = build.node_components["RU_T72B_Base/Chassis"][2]
    entry = lod[0].fields["m_LODs"][0]
    assert [r["renderer"]["m_PathID"] for r in entry["renderers"]] == [mr_id]
    assert pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH, MATERIAL)).ok


def test_skinned_renderer_path_keeps_bone_slots() -> None:
    spec = pb.PrefabNode(
        name="root",
        children=(
            pb.PrefabNode(name="bone"),
            pb.PrefabNode(
                name="skin",
                kind=pb.KIND_SKINNED,
                mesh=CHASSIS_MESH,
                materials=(MATERIAL,),
                bones=("root/bone", None),
                root_bone="root/bone",
                # ⚠ 2026-10-08 修：蒙皮节点的 m_AABB 是**必填**（零尺寸 = 每帧被剔除 ⇒
                # 真机上"模型没有网格"，见 prefab_build.py:1017 的说明）。本用例量的是
                # 骨骼槽位，与几何无关，给一个最小合法 AABB 即可 —— 原来的写法漏了它，
                # 于是在 A2 那轮把 local_bounds 立成必填之后**这条用例一直是红的**。
                local_bounds={"m_Center": {"x": 0.5, "y": 0.5, "z": 0.5},
                              "m_Extent": {"x": 0.5, "y": 0.5, "z": 0.5}},
            ),
        ),
    )
    build = pb.build_prefab(spec, _entries(), extra_package_ids=(CHASSIS_MESH, MATERIAL))
    smr = [o for o in build.objects if o.class_id == pb.CLASS_SKINNED_MESH_RENDERER]
    assert len(smr) == 1
    bone_tr = build.node_ids["root/bone"][1]
    assert [p["m_PathID"] for p in smr[0].fields["m_Bones"]] == [bone_tr, 0]
    assert smr[0].fields["m_RootBone"]["m_PathID"] == bone_tr
    assert smr[0].fields["m_SkinnedMotionVectors"] is True
    assert pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH, MATERIAL)).ok


def test_plan_path_ids_is_deterministic_and_positive() -> None:
    first = pb.plan_path_ids("w10-min", 12)
    assert first == pb.plan_path_ids("w10-min", 12)
    assert first != pb.plan_path_ids("other", 12)
    assert all(value > 0 for value in first)
    assert len(set(first)) == 12
    assert pb.plan_path_ids("w10-min", 12, base=1000)[0] == 1000


def test_default_fields_is_complete_for_every_recorded_class() -> None:
    for key in MINIMAL_KEYS:
        class_id, script_index = key
        entry = _entry(class_id, script_index)
        fields = pb.default_fields(entry.nodes)
        payload = clone.write_object(fields, entry.nodes)
        assert clone.read_object(payload, entry.nodes) == fields
        assert clone.write_object(clone.read_object(payload, entry.nodes), entry.nodes) == payload


def test_hierarchy_report_serialises_for_reports() -> None:
    build = _build()
    report = pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH, MATERIAL))
    assert dataclasses.asdict(report)["pptr_dangling"] == 0
    assert "悬空 0" in "\n".join(report.lines())


@pytest.mark.slow
def test_slow_real_units_meta_type_ids(tmp_path) -> None:
    r"""真元数据区跑一遍（默认档不跑）：类型表项号 + 最小 prefab 自证。

    复算：.venv\Scripts\python.exe -m pytest tests/test_build_prefab_from_scratch.py -q -m slow
    """
    from pathlib import Path

    meta_path = WORK / "w10_ref" / "units_meta.bin"
    if not meta_path.is_file():
        pytest.skip("没有 _work/w10_ref/units_meta.bin（Lead dump 的 units 元数据区）")
    _table_real, entries, report = pb.type_entries_for_classes(
        meta_path.read_bytes(),
        [
            (pb.CLASS_GAME_OBJECT, None),
            (pb.CLASS_TRANSFORM, None),
            (pb.CLASS_MESH_FILTER, None),
            (pb.CLASS_MESH_RENDERER, None),
            (pb.CLASS_LOD_GROUP, None),
            (pb.CLASS_SKINNED_MESH_RENDERER, None),
        ],
        common_strings=clone.unitypy_common_strings(),
    )
    assert {row.class_id: row.type_id for row in report} == {
        pb.CLASS_GAME_OBJECT: 4,
        pb.CLASS_TRANSFORM: 1,
        pb.CLASS_MESH_FILTER: 6,
        pb.CLASS_MESH_RENDERER: 13,
        pb.CLASS_LOD_GROUP: 5,
        pb.CLASS_SKINNED_MESH_RENDERER: 9,
    }
    build = pb.build_prefab(
        _spec(),
        entries,
        seed="w10-min-slow",
        type_report=report,
        extra_package_ids=(CHASSIS_MESH, MATERIAL),
    )
    assert len(build.objects) == 12
    assert pb.verify_hierarchy(build, extra_package_ids=(CHASSIS_MESH, MATERIAL)).ok

@pytest.mark.slow
def test_slow_prefab_packs_into_a_unityfs_bundle(tmp_path) -> None:
    r"""端到端接缝：自造 prefab + AssetBundle 装进**合法 UnityFS 包**，再用产品读侧打开。

    这条是给"集成方"用的判据：容器路径 -> 我们的 root GameObject pathID，条目数/对象数对得上。
    ⚠ 包内**不含** Mesh / Material（那两块是别的模块的产物）—— 所以这里只验结构，不验 N4/N5。
    """
    from pathlib import Path

    from ba_mod_maker.asset_kernel import builder

    meta_path = WORK / "w10_ref" / "units_meta.bin"
    if not meta_path.is_file():
        pytest.skip("没有 _work/w10_ref/units_meta.bin（Lead dump 的 units 元数据区）")
    order = [
        (pb.CLASS_GAME_OBJECT, None),
        (pb.CLASS_TRANSFORM, None),
        (pb.CLASS_MESH_FILTER, None),
        (pb.CLASS_MESH_RENDERER, None),
        (builder.CLASS_ASSETBUNDLE, None),
    ]
    _table, entries, report = pb.type_entries_for_classes(
        meta_path.read_bytes(), order, common_strings=clone.unitypy_common_strings()
    )
    build = pb.build_prefab(
        _spec(),
        entries,
        seed="w10-pack-test",
        type_report=report,
        extra_package_ids=(CHASSIS_MESH, MATERIAL),
    )
    assert build.required_type_keys() == (
        (pb.CLASS_GAME_OBJECT, None),
        (pb.CLASS_TRANSFORM, None),
        (pb.CLASS_MESH_FILTER, None),
        (pb.CLASS_MESH_RENDERER, None),
    )
    asset_path = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"
    bundle_name = "w10test0000000000000000000000000.bundle"
    ab_fields = builder.assetbundle_fields(
        bundle_name=bundle_name, container=((asset_path, build.root_game_object),)
    )
    ab_payload = clone.write_object(ab_fields, entries[(builder.CLASS_ASSETBUNDLE, None)].nodes)
    serialized = builder.serialize_serialized_file(
        type_entries=[entries[key] for key in order],
        objects=(*build.object_rows(), (1, builder.CLASS_ASSETBUNDLE, ab_payload)),
    )
    unityfs = builder.build_unityfs(
        serialized.blob,
        builder.node_path_for(bundle_name),
        flags=builder.DEFAULT_FLAGS,
        block_type=builder.BLOCK_NONE,
    )
    target = tmp_path / "w10min_prefab.bundle"
    target.write_bytes(unityfs.data)
    reopened = builder.reopen_container(target, common_strings=clone.unitypy_common_strings())
    assert [tuple(row) for row in reopened["container_rows"]] == [
        (asset_path, build.root_game_object, 0, 1)
    ]
    assert reopened["object_count"] == len(build.objects) + 1
