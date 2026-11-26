"""`asset_kernel/mesh.py::read_submesh_ranges` —— 多材质 renderer 的「哪一段面归第几颗材质」。

来历（2026-10-17 实测）：旧读侧把整条索引缓冲当一片面写出去 ⇒ **所有三角形都落在第 0 个
材质槽**。实测 6 件单位：玻璃槽 `polygon.material_index` 命中 **0 个面**
（`RU_TU95142_glass` / `DLC3_Yak-130_Glass` / `DLC2_HMMWV_Glass` …），于是「玻璃 alpha 改 1.0」
与「保持 0.051」两张渲染**逐像素差 0**（576,000 像素 0 个变）。

这里只钉**纯函数**：`m_SubMeshes` -> ((首三角形, 三角形数), …)。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.asset_kernel import mesh as mesh_mod


def test_uint16_submeshes_map_byte_offsets_to_triangle_ranges() -> None:
    tree = {
        "m_IndexFormat": 0,  # uint16
        "m_SubMeshes": [
            {"firstByte": 0, "indexCount": 6},  # 2 个三角形
            {"firstByte": 12, "indexCount": 3},  # 1 个三角形（12 B = 6 索引）
        ],
    }
    assert mesh_mod.read_submesh_ranges(tree) == ((0, 2), (2, 1))


def test_uint32_submeshes_divide_by_four() -> None:
    tree = {
        "m_IndexFormat": 1,  # uint32
        "m_SubMeshes": [
            {"firstByte": 0, "indexCount": 9},
            {"firstByte": 36, "indexCount": 6},
        ],
    }
    assert mesh_mod.read_submesh_ranges(tree) == ((0, 3), (3, 2))


def test_no_submeshes_is_empty_not_one_segment() -> None:
    '没有 m_SubMeshes 与「只有一段」必须能分辨（⛔ 不猜成 1 段）。'
    assert mesh_mod.read_submesh_ranges({"m_IndexFormat": 0}) == ()


def test_degenerate_submesh_keeps_its_slot_position() -> None:
    'indexCount < 3 的段要留位置（后面那段的槽号不能往前挤）。'
    tree = {
        "m_IndexFormat": 0,
        "m_SubMeshes": [
            {"firstByte": 0, "indexCount": 0},
            {"firstByte": 12, "indexCount": 6},
        ],
    }
    assert mesh_mod.read_submesh_ranges(tree) == ((0, 0), (2, 2))


def test_unknown_index_format_is_an_error_not_a_guess() -> None:
    tree = {"m_IndexFormat": 7, "m_SubMeshes": [{"firstByte": 0, "indexCount": 3}]}
    with pytest.raises(mesh_mod.MeshFormatError):
        mesh_mod.read_submesh_ranges(tree)


def test_read_mesh_carries_submeshes() -> None:
    '合成一个最小网格：2 顶点流 + 2 段 submesh ⇒ MeshData.submeshes 要原样带出来。'
    import struct

    # 3 个顶点：位置(3 float) + 法线(3 float) = 24 B/顶点
    vertex_count = 3
    data = bytearray()
    for index in range(vertex_count):
        data += struct.pack("<3f", float(index), 0.0, 0.0)
        data += struct.pack("<3f", 0.0, 0.0, 1.0)
    channels = [
        {"stream": 0, "offset": 0, "format": 0, "dimension": 3},
        {"stream": 0, "offset": 12, "format": 0, "dimension": 3},
    ]
    tree = {
        "m_Name": "synthetic",
        "m_VertexData": {
            "m_VertexCount": vertex_count,
            "m_DataSize": bytes(data),
            "m_Channels": channels,
        },
        "m_IndexFormat": 0,
        "m_IndexBuffer": list(struct.pack("<6H", 0, 1, 2, 2, 1, 0)),
        "m_SubMeshes": [
            {"firstByte": 0, "indexCount": 3},
            {"firstByte": 6, "indexCount": 3},
        ],
    }
    got = mesh_mod.read_mesh(tree, path_id=1)
    assert got.submeshes == ((0, 1), (1, 1))
    assert got.triangle_count == 2
