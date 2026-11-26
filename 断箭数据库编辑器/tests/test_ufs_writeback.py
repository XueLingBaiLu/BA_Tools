r"""变长写回的机械判据（工单 W1 §3）。

三条断言 + 一条性能读数：

| 用例 | 内容 | 改前 |
|---|---|---|
| **T1 等长（正控）** | 换成同长度字节 -> 读回 == 新值；其它对象逐字节未变 | 应已绿 |
| **T2 变长** | 换成 +32 字节（N >= 16）-> 读得回来 + 目标 == 新值 + 其它逐字节未变 | **必红** |
| **T3 重叠** | 载荷落进元数据区（含块 0）-> 必须抛 AssetError，不许静默出坏包 | **必红** |

为什么在**内存里造的小容器**上跑，而不是真实 data.unity3d：

三条判据全部落在**写回器自己的不变量**上（SerializedFile 头 file_size、对象表 byte_start /
byte_size、节点表 size、块表压缩字节）—— 这些不变量与容器多大无关，小容器与 7.1 GB 的真实件
**同构**；而真实件既做不了秒级回归、也不能在用例里反复写（禁区：不许写游戏目录）。

T2 的变长样例取 **+32 字节**，不是 +4：kb/08 §3.3 实测 **PKCS#7 桶**会让「明文 +4 字节」
在 3/4 概率下密文长度**不变** —— 用 +4 可能测到的是"等长假象"，白绿一场（工单 §7 坑 2）。
"""

from __future__ import annotations

import struct

import pytest

from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

#: 造容器用的块大小（与产品一致：128 KiB）。
CHUNK = ufs.RECHUNK_SIZE
#: 写回器认的节点名（它从这里找对象表）。
NODE = "globalgamemanagers.assets"
#: 对象表项在元数据里的偏移：byte_start 在表项 +8、byte_size 在 +16（实测 stride 24）。
TARGET_PID = 1002


def _blob(tag: bytes, n: int) -> bytes:
    """造一段**可压缩**的确定性字节（重复 pattern，LZ4 才吃得到）。"""
    return (tag * (n // len(tag) + 1))[:n]


def _build_sf(objs: list[tuple[int, bytes]]) -> bytes:
    """造一个最小的 SerializedFile v22 节点。

    头字段照 ufs._read_header_v22 的实测表（version@8 / endian@16 /
    metadata_size@20 / file_size@24 / data_offset@32，**都是大端**）；
    元数据体照 ufs.read_serialized_file 的解析顺序（unity_version cstr /
    target_platform i32 / enable_type_tree u8 / type_count i32 / object_count i32 /
    每项 align4 后 24 字节：path_id i64 + stored i64 + byte_size i32 + type_id i32，
    **小端**）。

    ⚠️ 类型表项**不做对齐**（ufs 的 docstring 第 2 条）；这里 type_count = 0 绕开它。
    """
    body = bytearray()
    body += b"2022.3.62f3\x00"
    body += struct.pack("<i", 19)
    body += b"\x00"                      # enable_type_tree = False
    body += struct.pack("<i", 0)         # type_count
    body += struct.pack("<i", len(objs))

    table: list[tuple[int, int, int]] = []
    cursor = 0
    for pid, payload in objs:
        table.append((pid, cursor, len(payload)))
        cursor += len(payload)

    for pid, stored, size in table:
        body += bytes((-len(body)) % 4)          # 对齐是相对**节点**起点（48 + len(body)）
        body += struct.pack("<qqii", pid, stored, size, 1)

    data_offset = 48 + len(body)
    total = data_offset + cursor

    head = bytearray(48)
    struct.pack_into(">I", head, 8, 22)           # version
    head[16] = 0                                  # endian 标志 0 = 小端
    struct.pack_into(">I", head, 20, len(body))   # metadata_size（不含这 48 字节）
    struct.pack_into(">q", head, 24, total)       # file_size
    struct.pack_into(">q", head, 32, data_offset) # data_offset
    return bytes(head) + bytes(body) + b"".join(p for _pid, p in objs)


def _build_container(sf_blob: bytes) -> bytes:
    """把一个 SerializedFile 节点包成最小 UnityFS 容器（LZ4HC 分块 + 节点表）。"""
    blocks: list[ufs.Block] = []
    payload = bytearray()
    for i in range(0, len(sf_blob), CHUNK):
        raw = sf_blob[i : i + CHUNK]
        comp = ufs.lz4hc(raw)
        if len(comp) >= len(raw):
            payload += raw
            blocks.append(ufs.Block(len(raw), len(raw), 0))
        else:
            payload += comp
            blocks.append(ufs.Block(len(raw), len(comp), 3))

    nodes = [ufs.Node(offset=0, size=len(sf_blob), flags=4, path=NODE)]
    raw_info = ufs.serialize_blocks_info(b"\x11" * 16, blocks, nodes)
    comp_info = ufs.lz4hc(raw_info)
    # flags 0x43 = 0x40（块表与目录合体）| 3（LZ4 块，**不带长度前缀**）
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", 0, len(comp_info), len(raw_info), 0x43
    )
    total = len(header) + len(comp_info) + len(payload)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", total, len(comp_info), len(raw_info), 0x43
    )
    return header + comp_info + bytes(payload)


def _objects(path) -> dict[int, tuple[int, bytes]]:
    """读回全部对象：{path_id: (解压流绝对起点, 字节)}。

    ⚠️ 这里**必须**走 UfsReader + read_serialized_file（产品自己的读侧），
    不能拿写回器内部的状态自证 —— 那正是「自己读不回来」要抓的东西。
    """
    with ufs.UfsReader(path) as r:
        node = r.node(NODE)
        info = ufs.read_serialized_file(r, node)
        out: dict[int, tuple[int, bytes]] = {}
        for _i, pid, stored, size, _tid in info.objects:
            start = node.offset + info.data_offset + stored
            out[pid] = (start, r.read_range(start, size))
        return out


@pytest.fixture
def bundle(tmp_path):
    """一份小容器：3 个对象（130 KB / 200 KB / 200 KB），跨 5 个块。"""
    objs = [
        (1001, _blob(b"A", 130_000)),
        (1002, _blob(b"B", 200_000)),
        (1003, _blob(b"C", 200_000)),
    ]
    path = tmp_path / "in.bundle"
    path.write_bytes(_build_container(_build_sf(objs)))
    return path


def test_t1_equal_length_replacement_round_trips(bundle, tmp_path) -> None:
    """T1 等长（**正控**）：同长度替换 -> 读回 == 新值，其它对象逐字节未变。

    正控的意义：它证明**尺子本身能变绿**。没有它，T2/T3 红了也分不清
    「写回器坏了」还是「用例自己写错了」。
    """
    before = _objects(bundle)
    start, old = before[TARGET_PID]
    new = bytes([old[0] ^ 0xFF]) + old[1:]          # 同长度、内容不同
    assert len(new) == len(old)

    out = tmp_path / "t1.bundle"
    with ufs.UfsReader(bundle) as r:
        report = ufs.rewrite_container(r, [ufs.Replacement(start, start + len(old), new)], out)

    assert report.delta == 0, "等长替换的 delta 必须是 0"
    after = _objects(out)
    assert after[TARGET_PID][1] == new, "目标对象没有变成新值"
    assert after[1001][1] == before[1001][1], "前面的对象被动到了"
    assert after[1003][1] == before[1003][1], "后面的对象被动到了"


def test_t2_growing_replacement_round_trips(bundle, tmp_path) -> None:
    """T2 变长（**必红**）：+32 字节 -> 产品自己必须读得回来。

    红在哪：节点表 size 没跟着长（ufs 原 :997 传的是旧 reader.nodes），
    SerializedFile 头 file_size 与节点 size 对不上 -> read_serialized_file 当场抛
    「file_size 与节点大小不一致」；对象表那个对象的 byte_size 也没改（原 :735 只写 +8）。
    """
    before = _objects(bundle)
    start, old = before[TARGET_PID]
    grow = 32
    new = old + b" " * grow                          # JSON 尾部空白，游戏容忍（kb/08 §7.5）
    assert len(new) - len(old) == grow >= 16, "变长样例必须 >= 16 字节，绕开 PKCS#7 等长假象"

    out = tmp_path / "t2.bundle"
    with ufs.UfsReader(bundle) as r:
        report = ufs.rewrite_container(r, [ufs.Replacement(start, start + len(old), new)], out)

    assert report.delta == grow
    after = _objects(out)                            # <- 改前在这里抛 AssetError
    assert after[TARGET_PID][1] == new, "目标对象没有变成新值"
    assert len(after[TARGET_PID][1]) == len(old) + grow, "对象的 byte_size 没跟着长"
    assert after[1001][1] == before[1001][1], "前面的对象被动到了"
    assert after[1003][1] == before[1003][1], "后面的对象被动到了"


def test_t3_payload_overlapping_metadata_is_rejected(bundle, tmp_path) -> None:
    """T3 重叠（**必红**）：载荷落进元数据区 -> 必须抛错，不许静默产出坏包。

    元数据补丁是**后加**的一段替换（它覆盖 SerializedFile 头与对象表所在的那些块）。
    改前：互不相交的检查只对**调用方给的**区间跑过一次，补丁加进来之后**不再查**
    -> 载荷把补丁覆盖掉，产出一个「能生成、读不回来」的包，而且**没有异常**。
    """
    out = tmp_path / "t3.bundle"
    with ufs.UfsReader(bundle) as r:
        node = r.node(NODE)
        assert node.offset == 0, "本夹具的节点从 0 起，所以这一段正落在块 0"
        bad = ufs.Replacement(node.offset, node.offset + 16, b"Y" * 48)   # delta = +32
        with pytest.raises(AssetError):
            ufs.rewrite_container(r, [bad], out)

    assert not out.exists(), "抛错之后不许留下半成品文件"
