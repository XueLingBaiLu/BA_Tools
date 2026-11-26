"""资产内核（asset_kernel）—— Unity 资产层的**纯 Python 共用实现**。

本包是「游戏侧资产」的唯一实现层。三条硬契约 + 一组读侧入口：

| 契约 | 模块 | 判据 |
|---|---|---|
| 坐标 | coords | det == **-1**（反射，2026-10-03 改口径）；往返误差 0；绕序 frac == 1.0000 |
| 颜色空间 | colorspace | m_ColorSpace 1 -> sRGB / 0 -> Non-Color，逐张显式设置 |
| 法线 | normals | DXT5nm（X 在 A、Y 在 G）重建后 x^2+y^2>1 占比 < 0.03 |

读侧零件（W2 立了三件，W3 补齐流式与 prefab）：

| 要什么 | 模块 | 说明 |
|---|---|---|
| 容器读侧（UnityFS 的节点 / 解范围） | unityfs | 薄封装 ba_mod_maker.core.asset.ufs（不复制实现） |
| 流式定位 .resS | streaming | resolve_stream（节点内相对偏移 + 降级留痕） |
| 顶点流 / 索引解码 | mesh | dimension & 0xF、流间 16 字节对齐；**索引已补绕序** |
| 贴图元数据 + 原始字节 | texture | read_texture_2d（解码交给调用方，边界清楚） |
| prefab 层级 / 世界坐标 / LOD | prefab | read_prefab_tree：吃归一化节点，零第三方依赖 |
| 骨骼名哈希 | skeleton | 设计稿 §8 规则 4 的端到端核对（W3 实测 29/29 命中） |
| typetree / UnityPy | typetree · unitypy_source | 唯一 typetree 取法；UnityPy 延迟 import |

## 分层守则（由 tests/test_contracts.py::C5 守着）

本包**零 GUI 绑定、零 3D 主程序绑定**：源码里连这两个名字都不许出现
（判据是文本扫描 0 命中）。想在 Blender 里用，就把本包原样拷进去 ——
同一份源码，不需要改一行。

第三方依赖也**只在 unitypy_source 里、且是延迟 import**（typetree 解析今天借 UnityPy，
见设计稿 §2 的 _vendor；纯逻辑那几件不依赖它，于是没装 UnityPy 的环境也跑得动用例）。

## 写侧：**故意留白**

设计稿 §9.10.4 的「搬到尾部」与 W1 落地的「分段重压」是同一目标的两种实现，
W1 实测后者的性能收益已经抹平前者（111,982 块 -> 68 块）。写面**规划器**的口径
留给 W5 裁定，所以本包 v1 **只出读侧**：不进 rewrite_*、不进 Replacement，
免得下一单被一个过早的接口框住。要用写侧，直接调
ba_mod_maker.core.asset.ufs（W1 已交付、本包不改）。
"""

from ba_mod_maker.asset_kernel import (
    anim_build,
    avatar_build,
    builder,
    catalog_writer,
    clone,
    colorspace,
    coords,
    material_build,
    materials,
    mesh,
    metadata_writer,
    monoscript_build,
    normals,
    prefab,
    prefab_build,
    shader_build,
    skeleton,
    streaming,
    texture,
    texture_build,
    textures,
    typetree,
    unit_bundle,
    unityfs,
    unitypy_source,
)
from ba_mod_maker.asset_kernel.colorspace import (
    NON_COLOR,
    SRGB,
    apply_colorspace,
    colorspace_of,
    srgb_normal_map_violations,
)
from ba_mod_maker.asset_kernel.coords import (
    WindingReport,
    blender_to_unity,
    blender_to_unity_quat,
    blender_to_unity_scale,
    determinant3,
    determinant_of_unity_to_blender,
    reverse_winding,
    unity_to_blender,
    unity_to_blender_matrix,
    unity_to_blender_quat,
    unity_to_blender_scale,
    winding_agreement,
)
from ba_mod_maker.asset_kernel.materials import (
    NODE_SHAPE,
    PARAM_TO_NODE_INPUT,
    TABLES,
    ChannelLink,
    MaterialPlan,
    MaterialTable,
    RuntimeItem,
    ScalarBinding,
    SlotBinding,
    UnitPlan,
    plan_material,
    plan_unit,
    table_for,
)
from ba_mod_maker.asset_kernel.mesh import (
    MeshData,
    MeshFormatError,
    MeshScan,
    StreamedMeshError,
    as_bytes,
    layout_span,
    read_indices,
    read_mesh,
    scan_meshes,
    stream_layout,
)
from ba_mod_maker.asset_kernel.normals import (
    DXT5NM_X_CHANNEL,
    DXT5NM_Y_CHANNEL,
    UnitRangeReport,
    channel_bytes,
    dxt5nm_report,
    dxt5nm_rgb_pixel,
    dxt5nm_to_rgb_normal,
    dxt5nm_z_lut,
    reconstruct_dxt5nm,
    rgb_report,
    unit_out_of_range_fraction,
)
from ba_mod_maker.asset_kernel.prefab import (
    Node,
    PrefabTree,
    Renderer,
    build_tree,
    read_prefab_tree,
)
from ba_mod_maker.asset_kernel.skeleton import (
    BoneHashReport,
    bone_path_hash,
    read_bind_pose,
    read_bone_hashes,
    verify_bone_hashes,
)
from ba_mod_maker.asset_kernel.streaming import (
    StreamedDataError,
    StreamingInfo,
    StreamPayload,
    parse_streaming_info,
    resolve_stream,
)
from ba_mod_maker.asset_kernel.texture import (
    FORMAT_NAMES,
    StreamedTextureError,
    TextureInfo,
    read_texture_2d,
)
from ba_mod_maker.asset_kernel.typetree import as_typetree, path_id_of

__all__ = [
    "DXT5NM_X_CHANNEL",
    "DXT5NM_Y_CHANNEL",
    "FORMAT_NAMES",
    "NODE_SHAPE",
    "NON_COLOR",
    "PARAM_TO_NODE_INPUT",
    "SRGB",
    "TABLES",
    "BoneHashReport",
    "ChannelLink",
    "MaterialPlan",
    "MaterialTable",
    "MeshData",
    "MeshFormatError",
    "MeshScan",
    "Node",
    "PrefabTree",
    "Renderer",
    "RuntimeItem",
    "ScalarBinding",
    "SlotBinding",
    "StreamPayload",
    "StreamedDataError",
    "StreamedMeshError",
    "StreamedTextureError",
    "StreamingInfo",
    "TextureInfo",
    "UnitPlan",
    "UnitRangeReport",
    "WindingReport",
    "anim_build",
    "apply_colorspace",
    "as_bytes",
    "avatar_build",
    "as_typetree",
    "blender_to_unity",
    "blender_to_unity_quat",
    "blender_to_unity_scale",
    "bone_path_hash",
    "build_tree",
    "builder",
    "catalog_writer",
    "channel_bytes",
    "clone",
    "colorspace",
    "colorspace_of",
    "coords",
    "determinant3",
    "determinant_of_unity_to_blender",
    "dxt5nm_rgb_pixel",
    "dxt5nm_to_rgb_normal",
    "dxt5nm_z_lut",
    "dxt5nm_report",
    "layout_span",
    "material_build",
    "materials",
    "mesh",
    "metadata_writer",
    "monoscript_build",
    "normals",
    "parse_streaming_info",
    "path_id_of",
    "plan_material",
    "plan_unit",
    "prefab",
    "prefab_build",
    "read_bind_pose",
    "read_bone_hashes",
    "read_indices",
    "read_mesh",
    "read_prefab_tree",
    "read_texture_2d",
    "reconstruct_dxt5nm",
    "resolve_stream",
    "reverse_winding",
    "rgb_report",
    "scan_meshes",
    "shader_build",
    "skeleton",
    "srgb_normal_map_violations",
    "stream_layout",
    "streaming",
    "table_for",
    "texture",
    "texture_build",
    "textures",
    "typetree",
    "unity_to_blender",
    "unity_to_blender_matrix",
    "unity_to_blender_quat",
    "unity_to_blender_scale",
    "unit_bundle",
    "unityfs",
    "unitypy_source",
    "unit_out_of_range_fraction",
    "verify_bone_hashes",
    "winding_agreement",
]
