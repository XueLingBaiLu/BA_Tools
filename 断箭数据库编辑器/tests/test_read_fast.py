"""快读器判据：`clone.read_object(bulk_arrays=True)` 与逐元素读**结果逐字节相同**。

## 判据一行

    pytest tests/test_read_fast.py -q   -> 4 passed

## 为什么要有这一条

2026-10-18：用户报「拿整套 T-90M 特别慢」。根因之一在**原始类型数组逐元素读**（UnityPy 对
`UInt8[]` 一个字节调一次 `read_u_byte()`）。给 `clone` 加了 `bulk_arrays=True`：`UInt8[]`
一次读成 `bytes`，其它原始类型一次 `struct.unpack`。这条钉「批量读 == 逐元素读」。
"""
from __future__ import annotations

import struct

import pytest

from ba_mod_maker.asset_kernel import clone as cl

#: Unity 数组的形状：字段(holder) -> Array -> { size, data }；对齐位挂在 **Array** 上。
ALIGN = 0x4000


def _nodes(elem: str, elem_size: int) -> tuple[cl.TypeNode, ...]:
    def node(level: int, kind: str, name: str, size: int = -1, meta: int = 0) -> cl.TypeNode:
        return cl.TypeNode(level=level, type=kind, name=name, byte_size=size,
                           meta_flag=meta, type_flags=0, version=1)

    return (
        node(0, "Root", "Base"),
        node(1, "vector", "m_Data"),
        # ⚠ 对齐位挂在 **Array** 上（与真件 Mesh 的类型树同形）；挂在元素上会变成
        #   「每个元素读完各对齐到 4」⇒ 3 个字节要吃掉 12 字节。
        node(2, "Array", "Array", -1, ALIGN),
        node(3, "int", "size", 4),
        node(3, elem, "data", elem_size),
    )


def _payload(body: bytes, count: int) -> bytes:
    raw = struct.pack("<i", count) + body
    return raw + b"\x00" * ((-len(raw)) % 4)


def test_bulk_and_scalar_reads_agree_on_uint8_array() -> None:
    data = bytes(range(16))
    raw = _payload(data, len(data))
    nodes = _nodes("UInt8", 1)
    slow = cl.read_object(raw, nodes)
    fast = cl.read_object(raw, nodes, bulk_arrays=True)
    assert slow["m_Data"] == list(data)
    assert fast["m_Data"] == data
    assert isinstance(fast["m_Data"], bytes), "批量读必须给 bytes（⛔ 不许 84 M 个 int）"


def test_bulk_reads_are_still_byte_exact_on_roundtrip() -> None:
    data = bytes(range(250))
    raw = _payload(data, len(data))
    nodes = _nodes("UInt8", 1)
    tree = cl.read_object(raw, nodes, bulk_arrays=True)
    assert cl.write_object(tree, nodes) == raw


def test_bulk_flag_is_off_by_default() -> None:
    """⛔ 老路径一个字节都不许变：默认仍是 list[int]。"""
    data = b"\x01\x02\x03"
    raw = _payload(data, len(data))
    nodes = _nodes("UInt8", 1)
    tree = cl.read_object(raw, nodes)
    assert tree["m_Data"] == [1, 2, 3]


def test_scalar_array_bulk_read_matches_element_by_element() -> None:
    values = (0.5, -1.25, 3.0)
    raw = _payload(struct.pack("<3f", *values), len(values))
    nodes = _nodes("float", 4)
    slow = cl.read_object(raw, nodes)
    fast = cl.read_object(raw, nodes, bulk_arrays=True)
    assert [pytest.approx(v) for v in slow["m_Data"]] == [pytest.approx(v) for v in fast["m_Data"]]
