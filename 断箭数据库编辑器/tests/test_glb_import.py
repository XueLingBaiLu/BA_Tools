"""GLB -> ObjMesh 的解析器（工程文件里的 `model.glb` 走它）。"""
from __future__ import annotations

import json
import struct

import pytest


def _tiny_glb() -> bytes:
    """一个三角形的合法 GLB（3 顶点 / 1 面 / 无 UV / 无法线）。"""
    positions = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    indices = struct.pack("<3H", 0, 1, 2)
    blob = positions + indices + b"\x00" * ((4 - len(indices) % 4) % 4)
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(positions) + len(indices)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
    }
    text = json.dumps(doc).encode("utf-8")
    text += b" " * ((4 - len(text) % 4) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    head = struct.pack("<4sII", b"glTF", 2, total)
    return (head + struct.pack("<I4s", len(text), b"JSON") + text
            + struct.pack("<I4s", len(blob), b"BIN\x00") + blob)


def test_reads_a_triangle() -> None:
    from ba_mod_maker.asset_kernel import glb_import

    mesh = glb_import.read_glb(_tiny_glb())

    assert len(mesh.positions) == 3
    assert len(mesh.faces) == 1
    assert mesh.triangles == 1
    assert mesh.faces[0] == ((0, None, None), (1, None, None), (2, None, None))
    lo, hi = mesh.aabb()
    assert lo == (0.0, 0.0, 0.0)
    assert hi == (1.0, 1.0, 0.0)


def test_rejects_non_glb() -> None:
    from ba_mod_maker.asset_kernel import glb_import

    with pytest.raises(glb_import.GlbError):
        glb_import.read_glb(b"not a glb at all")


def _two_part_glb() -> bytes:
    """两个节点 / 两颗网格的合法 GLB（节点名 Chassis / turret，几何各自在 z=0 / z=5）。"""
    pos_a = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    pos_b = struct.pack("<9f", 0.0, 0.0, 5.0, 2.0, 0.0, 5.0, 0.0, 2.0, 5.0)
    idx = struct.pack("<3H", 0, 1, 2)
    blob = pos_a + pos_b + idx + b"\x00" * ((4 - len(idx) % 4) % 4)
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(pos_a) + len(pos_b) + len(idx)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_a)},
            {"buffer": 0, "byteOffset": len(pos_a), "byteLength": len(pos_b)},
            {"buffer": 0, "byteOffset": len(pos_a) + len(pos_b), "byteLength": len(idx)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0}, "indices": 2}]},
            {"primitives": [{"attributes": {"POSITION": 1}, "indices": 2}]},
        ],
        "nodes": [
            {"name": "Chassis", "mesh": 0},
            {"name": "turret", "mesh": 1},
        ],
    }
    text = json.dumps(doc).encode("utf-8")
    text += b" " * ((4 - len(text) % 4) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    head = struct.pack("<4sII", b"glTF", 2, total)
    return (head + struct.pack("<I4s", len(text), b"JSON") + text
            + struct.pack("<I4s", len(blob), b"BIN\x00") + blob)


def test_parts_are_split_by_node_name() -> None:
    """整件导入要按节点分件（2026-10-24 用户口径：提取与导入互逆）。"""
    from ba_mod_maker.asset_kernel import glb_import

    parts = glb_import.read_glb_parts(_two_part_glb())

    assert [name for name, _ in parts] == ["Chassis", "turret"]
    assert len(parts[0][1].positions) == 3
    # ★ X 取反：提取器的 glb 与 Unity 的 X 相反（见 `_mirror_x` 的实测）
    assert parts[0][1].positions[1] == (-1.0, 0.0, 0.0)
    assert parts[1][1].positions[1] == (-2.0, 0.0, 5.0)
    # 合并那条路必须还是「全加起来」，两条路不能打架
    whole = glb_import.read_glb(_two_part_glb())
    assert len(whole.positions) == 6
    assert len(whole.faces) == 2
    assert sum(len(g.faces) for _, g in parts) == len(whole.faces)

