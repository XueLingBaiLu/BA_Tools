"""`asset_kernel/mesh.py::read_mesh` —— 「硬蒙皮」网格（只有 BlendIndices、没有 BlendWeight）。

来历（2026-10-26 实测）：真件 T-72B3 的炮塔反应装甲（B3 / K5_B3 …）就是这种网格。
Unity 自己的读法（Unity 侧探针 ProbeRealSkin）：`mesh.GetBoneWeights()` 给每顶点**权重 1.0**，
骨索引就是 BlendIndices —— 实测 B3 全 16,286 顶点指向 turret_0，K5_B3 全 8,414 指向 body。
内核原先按「没有权重通道 = 没有权重」处理 ⇒ Blender 顶点组是空的 ⇒ 整件绑到骨架根 ⇒
炮塔转、反应装甲不跟着转。这里钉死两条：

  ① 没有 BlendWeight 但有 BlendIndices ⇒ 第一个索引权重 1.0（照 Unity）；
  ② 有 BlendWeight ⇒ 原样用它（⛔ 不许被 ① 覆盖）。
"""
from __future__ import annotations

import struct

from ba_mod_maker.asset_kernel import mesh as mesh_mod


def _slots(*used: tuple[int, dict]) -> list[dict]:
    'm_Channels 是**按属性号**定位的（长度 14），没用到的槽 dimension=0。'
    channels = [{'stream': 0, 'offset': 0, 'format': 0, 'dimension': 0} for _ in range(14)]
    for index, spec in used:
        channels[index] = spec
    return channels


def _tree(*, with_weights: bool, indices: list[tuple[int, int, int, int]]) -> dict:
    count = len(indices)
    data = bytearray()
    for i in range(count):
        data += struct.pack('<3f', float(i), 0.0, 0.0)      # Position
        data += struct.pack('<3f', 0.0, 0.0, 1.0)          # Normal
        data += struct.pack('<4B', *indices[i])            # BlendIndices (UInt8 ×4)
        if with_weights:
            data += struct.pack('<4f', 0.5, 0.5, 0.0, 0.0)  # BlendWeight
    used = [
        (mesh_mod.ATTRIBUTE_POSITION, {'stream': 0, 'offset': 0, 'format': 0, 'dimension': 3}),
        (mesh_mod.ATTRIBUTE_NORMAL, {'stream': 0, 'offset': 12, 'format': 0, 'dimension': 3}),
        (mesh_mod.ATTRIBUTE_BLEND_INDEX, {'stream': 0, 'offset': 24, 'format': 6, 'dimension': 4}),
    ]
    if with_weights:
        used.append(
            (mesh_mod.ATTRIBUTE_BLEND_WEIGHT,
             {'stream': 0, 'offset': 28, 'format': 0, 'dimension': 4}))
    return {
        'm_Name': 'synthetic',
        'm_VertexData': {
            'm_VertexCount': count,
            'm_DataSize': bytes(data),
            'm_Channels': _slots(*used),
        },
        'm_IndexFormat': 0,
        'm_IndexBuffer': list(struct.pack('<6H', 0, 1, 2, 2, 1, 0)),
        'm_SubMeshes': [{'firstByte': 0, 'indexCount': 6}],
    }


def test_index_only_mesh_gets_weight_one_on_its_first_slot() -> None:
    '硬蒙皮：没 BlendWeight ⇒ 第一个索引权重 1（Unity 口径），否则顶点组是空的。'
    idx = [(2, 0, 0, 0), (0, 0, 0, 0), (2, 0, 0, 0)]
    got = mesh_mod.read_mesh(_tree(with_weights=False, indices=idx), path_id=1)
    assert got.bone_indices == ((2, 0, 0, 0), (0, 0, 0, 0), (2, 0, 0, 0))
    assert got.bone_weights == ((1.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))


def test_real_weights_are_not_overwritten() -> None:
    '负控：有 BlendWeight 的网格必须原样带出自己的权重（0.5/0.5）。'
    idx = [(2, 1, 0, 0), (0, 0, 0, 0), (2, 1, 0, 0)]
    got = mesh_mod.read_mesh(_tree(with_weights=True, indices=idx), path_id=1)
    assert got.bone_weights[0] == (0.5, 0.5, 0.0, 0.0)
    assert got.bone_indices[0] == (2, 1, 0, 0)
