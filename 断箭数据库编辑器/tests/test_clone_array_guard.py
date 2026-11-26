r"""clone 的数组护栏：**元素个数按对象剩余字节判**，不再有一条拍脑袋的硬上限。

## 这条修的是真事（2026-10-06 实测）

units 包 29 个 Shader 里，**最大的那个 28,333,744 B 读不了**：它的 compressedBlob 是
vector<UInt8>，真件 **27,982,200** 个字节 —— 而 _ValueReader.read 里写死
「count > 10_000_000 就抛」。28 个小 Shader 全能读，只有它被这条上限误杀。

**修法**：上限抬到 1e9 只当兜底；真正的护栏换成
「元素个数 × 元素**最小**字节数 > 对象剩余字节 ⇒ 抛」（_least_bytes 构造上就是下界，
**绝不高估**：标量 = struct 宽度、string/TypelessData = 4、无子节点 = 0、数组外壳 = 4、
结构体 = 各子项之和），并按子树缓存（一次读里同一棵树会被问很多次）。

**实测读数**（_work/v3a-shader-census.py，只读开 units）：

| | 修前 | 修后 |
|---|---|---|
| Shader 读通 | 28/29（那个 28 MB 的：compressedBlob 个数不合法） | **29/29** |
| 往返逐字节相同 | 28/28 | **29/29** |
| 最大那个 | — | 读 8.9 s / 写 7.3 s |

⚠ units 是**重活**（一把锁串行）⇒ 真件那条**不进用例**，读数在 _work 里复算
（同 tests/test_metadata_writer.py 对 units 的口径）。本文件只用合成类型树量护栏本身。

## 这个环境测不了的那条

「一条 1,200 万个元素的合法数组能读」**没有便宜的用例**：读出来就是 1,200 万个 Python int
（约 400 MB）—— 太重。所以本文件只钉住「上限 >= 真件最大那个」（27,982,200）+
护栏的边界与负控；真实 29/29 的读数是 _work 脚本跑的。

判据一行：pytest tests/test_clone_array_guard.py -q -> 通过数 从 0 变 N。
"""

from __future__ import annotations

import struct

import pytest

from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.core.errors import AssetError

COMMON = clone.unitypy_common_strings()
NODE_STRUCT = struct.Struct("<hBBIIiiiQ")

#: 真件最大的数组（units 的 Shader.compressedBlob，vector<UInt8>）—— 2026-10-06 实测
REAL_BIGGEST_ARRAY = 27_982_200


def _synthetic_blob(rows: list[tuple[int, str, str, int]]) -> bytes:
    """按 (level, type, name, meta_flag) 拼一段内嵌类型树 blob（字段名走字符串池）。"""
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


def _tree(rows: list[tuple[int, str, str, int]]) -> tuple[clone.TypeNode, ...]:
    return clone.read_type_nodes(_synthetic_blob(rows), common_strings=COMMON)


#: vector<UInt8>（数组外壳带对齐位，与真件 compressedBlob 同形）
BYTE_VECTOR = [
    (0, "vector", "data", 0x8000),
    (1, "Array", "Array", 0x4000),
    (2, "int", "size", 0x0),
    (2, "UInt8", "data", 0x0),
]
#: vector<unsigned int>
UINT_VECTOR = [
    (0, "vector", "data", 0x8000),
    (1, "Array", "Array", 0x4000),
    (2, "int", "size", 0x0),
    (2, "unsigned int", "data", 0x0),
]
#: vector<string>
STRING_VECTOR = [
    (0, "vector", "data", 0x8000),
    (1, "Array", "Array", 0x4000),
    (2, "int", "size", 0x0),
    (2, "string", "data", 0x8000),
]


def test_the_cap_covers_the_biggest_real_array() -> None:
    """上限必须罩得住真件最大那个数组（修前 1e7 ⇒ 这条会红）。"""
    assert clone._MAX_ARRAY_ELEMENTS >= REAL_BIGGEST_ARRAY


def test_a_byte_vector_reads_and_roundtrips() -> None:
    """正控：vector<UInt8> 读 → 写逐字节相同；边界 = 个数正好等于剩余字节。"""
    nodes = _tree(BYTE_VECTOR)
    for count in (0, 1, 4, 64, 4096):
        body = bytes(range(256)) * (count // 256) + bytes(count % 256)
        payload = struct.pack("<i", count) + body
        payload += b"\x00" * ((-len(payload)) % 4)   # 数组外壳带对齐位：读完补齐到 4
        fields = clone.read_object(payload, nodes)
        assert fields == list(body), f"count={count}"
        assert clone.write_object(fields, nodes) == payload


def test_count_that_cannot_fit_is_rejected() -> None:
    """负控：声明的个数按元素**最小**宽度都放不下 ⇒ 当场抛（不是等 struct 报错）。"""
    # 1 字节元素：声明 100 个，对象只剩 90
    with pytest.raises(AssetError) as excinfo:
        clone.read_object(struct.pack("<i", 100) + bytes(90), _tree(BYTE_VECTOR))
    assert "对象只剩 90 字节" in str(excinfo.value)
    assert "每个元素至少 1 字节" in str(excinfo.value)
    # 4 字节元素：声明 100 个（要 400 B），对象只剩 120
    with pytest.raises(AssetError) as excinfo:
        clone.read_object(struct.pack("<i", 100) + bytes(120), _tree(UINT_VECTOR))
    assert "每个元素至少 4 字节" in str(excinfo.value)
    # 边界：正好放得下就必须放行（上面那条不是「宁可错杀」）
    payload = struct.pack("<i", 120) + bytes(120)
    assert len(clone.read_object(payload, _tree(BYTE_VECTOR))) == 120


def test_count_beyond_the_absolute_cap_is_rejected() -> None:
    """负控：兜底上限（1e9）之外直接抛 —— 走偏时不许去分配一个巨大的列表。"""
    with pytest.raises(AssetError) as excinfo:
        clone.read_object(struct.pack("<i", 1_000_000_001), _tree(BYTE_VECTOR))
    assert "元素个数不合法" in str(excinfo.value)
    with pytest.raises(AssetError):
        clone.read_object(struct.pack("<i", -5), _tree(BYTE_VECTOR))


def test_least_bytes_is_a_lower_bound() -> None:
    """_least_bytes 的语义：只认能当**下界**用的信号，认不出就给 0（= 不做这条检查）。"""
    cases: list[tuple[list[tuple[int, str, str, int]], int]] = [
        ([(0, "int", "a", 0x0)], 4),
        ([(0, "UInt8", "a", 0x0)], 1),
        ([(0, "SInt64", "a", 0x0)], 8),
        ([(0, "string", "a", 0x8000), (1, "Array", "Array", 0x4001),
          (2, "int", "size", 0x1), (2, "char", "data", 0x1)], 4),
        ([(0, "TypelessData", "a", 0x4001), (1, "int", "size", 0x1),
          (1, "UInt8", "data", 0x1)], 4),
        ([(0, "ReferencedObjectData", "data", 0x1)], 0),          # 无子节点 = 可能吃 0 字节
        ([(0, "vector", "a", 0x8000), (1, "Array", "Array", 0x4000),
          (2, "int", "size", 0x0), (2, "unsigned int", "data", 0x0)], 4),   # 数组外壳 = 4
        ([(0, "S", "a", 0x8000), (1, "int", "x", 0x0), (1, "UInt8", "y", 0x0)], 5),  # 结构体 = 和
    ]
    for rows, want in cases:
        got = clone._least_bytes(clone._build_tree(_tree(rows)))
        assert got == want, f"{rows[0][1]} -> {got}，期望 {want}"


def test_variable_width_arrays_are_not_falsely_rejected() -> None:
    """正控：元素宽度不定的数组（vector<string>）不许被这条护栏误杀。"""
    nodes = _tree(STRING_VECTOR)
    body = b"".join(struct.pack("<i", len(s)) + s + b"\x00" * ((-len(s)) % 4)
                    for s in (b"abc", b"", b"de"))
    payload = struct.pack("<i", 3) + body
    assert clone.read_object(payload, nodes) == ["abc", "", "de"]
    # 真正放不下时仍然要抛（3 个 string 至少 12 字节，这里只剩 11）
    with pytest.raises(AssetError):
        clone.read_object(struct.pack("<i", 3) + bytes(11), nodes)
