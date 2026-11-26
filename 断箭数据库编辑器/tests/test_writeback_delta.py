"""W7-A 判据：Δ（变长/变短）写回 + ① Crunched(29) -> DXT5(12) 格式转换（H1/H2/H3/H5/H7）。

**判据一行**：pytest tests/test_writeback_delta.py -q -> 通过数 **从 0 收集变 N passed**
（本文件新建前不存在）。

| 用例 | 判据（工单 §3） | 通过线 |
|---|---|---|
| test_h1_* | 读回 m_TextureFormat==12 / m_CompleteImageSize / m_StreamData.size | 三个都对 |
| test_h2_* | 对象表 byte_start/byte_size、节点表 offset/size、blocksInfo 重建 | 相等 |
| test_h3_* | **按平移后的坐标**比其它未变（§2 ④）；正控：插入点挪 1 字节必须红 | 差 = 0 |
| test_h5_* | 磁盘 CRC == catalog 的 m_Crc；m_BundleSize == 文件大小 | 双向一致 |
| test_h7_* | 故意把 m_CompleteImageSize 写错 ⇒ 读回校验必须红 | 能红 |

## 为什么在**内存里的两节点合成件**上跑（照 tests/test_writeback_l1.py 扩）

判据全落在写回器自己的不变量上（对象表 byte_start/byte_size、节点表 offset/size、
**块表怎么切**、双 CRC、catalog 的 extraOffset），这些不变量与容器多大无关；
而真件既做不了秒级回归，也不能在用例里反复写（⛔ 禁区：不许写游戏目录）。
真件的读数走 _work/w7_probe_*.py（只读；真写回由 Lead 统一执行）。

⚠ 合成件是**两节点**：node[0] = CAB-<hash>（SerializedFile）、node[1] = CAB-<hash>.resS
—— 这正是 units 包的形态（node[1] 是最后一个节点）。

⚠ 合成件的 SerializedFile 只有最小构造器（type_count = 0），**没有类型树** ⇒ 本文件
用自带的定长编解码器 _pack_texture/_unpack_texture 表达 Texture2D 的四个字段
（它解出来的 dict 就是 typetree 的形状，所以 textures.py 的纯函数直接吃它）。

## ObjectGrowth 的三条长度（Lead 2026-10-03 定的口径）

* **变长** -> 搬到 node[0] 尾部（byte_size 写大）；
* **等长** -> plan 阶段**拦下**并提示走 W6 的 ObjectChange（更便宜）；
* **变短** -> **也走追加**（byte_size 写小、旧字节留成死区、节点照样增长）。
  三条都要有用例。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import shutil
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_mod_maker.asset_kernel import textures, writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError
from test_writeback_l1 import _bundle_name, _catalog_options, _hash_of, write_catalog_sized

#: 造容器用的块大小（与产品一致：128 KiB）。
CHUNK = ufs.RECHUNK_SIZE
NODE0 = "CAB-0123456789abcdef0123456789abcdef"
NODE1 = NODE0 + ".resS"
TEXTURE_PATH = "archive:/" + NODE0 + "/" + NODE1
TEX_PID = 2002
DUMMY_A, DUMMY_B = 2001, 2003
#: .resS 的长度：**不是**块大小的整数倍，且让 node[0] 的末尾落在块 0 的中间。
RESS_SIZE = 200_007
#: 合成贴图：128x128 DXT5 / mip 8 -> 未压缩总量 21,872 B（公式值，逐级到 1x1）。
TEX_W, TEX_H, TEX_MIPS = 128, 128, 8
NEW_COMPLETE = 21_872
OLD_COMPLETE = 5_592_432
OLD_STREAM_SIZE = 3_000
RGBA = bytes(range(256)) * ((TEX_W * TEX_H * 4) // 256)


# --------------------------------------------------------------------------- #
# 合成 Texture2D 的定长编解码（解出来的 dict 就是 typetree 的形状）
# --------------------------------------------------------------------------- #
def _pack_texture(
    *,
    width: int = TEX_W,
    height: int = TEX_H,
    complete_image_size: int = OLD_COMPLETE,
    format_id: int = 29,
    mip_count: int = TEX_MIPS,
    offset: int = 64,
    size: int = OLD_STREAM_SIZE,
    path: str = TEXTURE_PATH,
    filler: int = 0,
) -> bytes:
    """布局（**path 必须是最后一个字段**，与真件实测布局同向）：

        w i | h i | complete I | format i | mips i | filler_len i | filler |
        offset Q | size I | pathlen i | path | 4 对齐填充

    ⚠ filler 是**合成件专用**的可变长字段（模拟"对象真的变长了"：内联数组更长 /
    串更长）；真件里对象变长的来源是顶点流之类的数组，机制一样。变短靠换更短的 path。
    filler 放在 m_StreamData **之前**，是为了让 path 保持"最后一个字段 + 4 字节对齐
    正好落在对象末尾"——那样 writer 的 _stream_info_of_object（真件布局自证）也能吃它。
    """
    filler_bytes = b"x" * int(filler)
    head = struct.pack("<iiIii", width, height, complete_image_size, format_id, mip_count)
    head += struct.pack("<i", len(filler_bytes)) + filler_bytes
    head += struct.pack("<QI", offset, size)
    raw = path.encode("utf-8")
    head += struct.pack("<i", len(raw)) + raw
    head += bytes((-len(head)) % 4)
    return head


def _unpack_texture(data: bytes) -> dict:
    """读回那四个字段（就是 textures.py 认的 typetree 形状）。"""
    width, height, complete, format_id, mips = struct.unpack_from("<iiIii", data, 0)
    (filler_len,) = struct.unpack_from("<i", data, 20)
    pos = 24 + filler_len
    offset, size = struct.unpack_from("<QI", data, pos)
    (path_len,) = struct.unpack_from("<i", data, pos + 12)
    path = data[pos + 16 : pos + 16 + path_len].decode("utf-8")
    return {
        "m_Name": "SYNTH_TEX",
        "m_Width": width,
        "m_Height": height,
        "m_CompleteImageSize": complete,
        "m_TextureFormat": format_id,
        "m_MipCount": mips,
        "m_StreamData": {"path": path, "offset": offset, "size": size},
    }


def _texture_object(**kwargs: object) -> bytes:
    return _pack_texture(**kwargs)  # type: ignore[arg-type]


def _blob(tag: bytes, n: int) -> bytes:
    """确定性、可压缩的一段字节（LZ4 才吃得到）。"""
    return (tag * (n // len(tag) + 1))[:n]


# --------------------------------------------------------------------------- #
# 合成件：最小 UnityFS 容器（**两个**节点）
# --------------------------------------------------------------------------- #
def _build_sf(objs: list[tuple[int, bytes]]) -> bytes:
    """最小 SerializedFile v22 节点（与 tests/test_writeback_l1.py::_build_sf 逐字段一致）。"""
    body = bytearray()
    body += b"2022.3.62f3\x00"
    body += struct.pack("<i", 19)
    body += b"\x00"
    body += struct.pack("<i", 0)
    body += struct.pack("<i", len(objs))

    table: list[tuple[int, int, int]] = []
    cursor = 0
    for pid, payload in objs:
        table.append((pid, cursor, len(payload)))
        cursor += len(payload)
    for pid, stored, size in table:
        body += bytes((-len(body)) % 4)
        body += struct.pack("<qqii", pid, stored, size, 1)

    data_offset = 48 + len(body)
    total = data_offset + cursor
    head = bytearray(48)
    struct.pack_into(">I", head, 8, 22)
    head[16] = 0
    struct.pack_into(">I", head, 20, len(body))
    struct.pack_into(">q", head, 24, total)
    struct.pack_into(">q", head, 32, data_offset)
    return bytes(head) + bytes(body) + b"".join(p for _pid, p in objs)


def _build_container(sf_blob: bytes, ress_blob: bytes) -> bytes:
    """两节点最小 UnityFS 容器（LZ4HC 分块；node[0] = CAB、node[1] = .resS）。"""
    stream = sf_blob + ress_blob
    blocks: list[ufs.Block] = []
    payload = bytearray()
    for i in range(0, len(stream), CHUNK):
        raw = stream[i : i + CHUNK]
        comp = ufs.lz4hc(raw)
        if len(comp) >= len(raw):
            payload += raw
            blocks.append(ufs.Block(len(raw), len(raw), 0))
        else:
            payload += comp
            blocks.append(ufs.Block(len(raw), len(comp), 3))
    nodes = [
        ufs.Node(offset=0, size=len(sf_blob), flags=4, path=NODE0),
        ufs.Node(offset=len(sf_blob), size=len(ress_blob), flags=4, path=NODE1),
    ]
    raw_info = ufs.serialize_blocks_info(b"\x11" * 16, blocks, nodes)
    comp_info = ufs.lz4hc(raw_info)
    header = ufs.serialize_header(6, "5.x.x", "2022.3.62f3", 0, len(comp_info), len(raw_info), 0x43)
    total = len(header) + len(comp_info) + len(payload)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", total, len(comp_info), len(raw_info), 0x43
    )
    return header + comp_info + bytes(payload)


def _objects(path: Path) -> dict[int, bytes]:
    """读回全部对象（**走产品自己的读侧**）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.node(NODE0)
        info = ufs.read_serialized_file(reader, node)
        out: dict[int, bytes] = {}
        for _i, pid, stored, size, _tid in info.objects:
            out[pid] = bytes(reader.read_range(node.offset + info.data_offset + stored, size))
        return out


def _nodes_and_blocks(path: Path) -> tuple[list[tuple[int, int]], int, int]:
    """(节点表 [(offset, size)], 块数, 各块解压长度和)。"""
    with ufs.UfsReader(path) as reader:
        return (
            [(node.offset, node.size) for node in reader.nodes],
            len(reader.blocks),
            sum(block.uncompressed for block in reader.blocks),
        )


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """两节点合成件：node[0] 很小（被插入点切成两块）、node[1] 约 200 KB（多块）。"""
    objs = [
        (DUMMY_A, _blob(b"A", 400)),
        (TEX_PID, _texture_object()),
        (DUMMY_B, _blob(b"C", 401)),
    ]
    path = tmp_path / "synth_delta.bundle"
    path.write_bytes(_build_container(_build_sf(objs), _blob(b"R", RESS_SIZE)))
    return path


def _write(path: Path, change: writer.DeltaChange, tmp_path: Path, **kwargs: object):
    return writer.write_delta(
        path,
        change,
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
        **kwargs,
    )


def _growth(payload: bytes) -> writer.DeltaChange:
    return writer.DeltaChange(
        growths=(
            writer.ObjectGrowth(
                path_id=TEX_PID, payload=payload, name="SYNTH_TEX", type_name="Texture2D"
            ),
        )
    )


# --------------------------------------------------------------------------- #
# textures.py 的公式与编码（H1 的前提，含负控）
# --------------------------------------------------------------------------- #
def test_texture_formula_matches_the_two_known_anchors() -> None:
    """H1 的前提：长度由 (格式, 宽, 高, mip) 唯一决定 —— 两个实测锚点必须对上。"""
    assert textures.dxt_payload_size(2048, 2048, 12, 12) == 5_592_432
    assert textures.dxt_payload_size(512, 512, 12, 10) == 349_552
    assert textures.dxt_payload_size(TEX_W, TEX_H, 12, TEX_MIPS) == NEW_COMPLETE
    assert textures.FORMAT_NAMES[29] == "DXT5Crunched"
    assert textures.FORMAT_NAMES[12] == "DXT5"
    # 负控：Crunched 不在"能编"的集合里（工单 §5：⛔ 不许用 crn 编码）
    with pytest.raises(ValueError):
        textures.dxt_payload_size(2048, 2048, 29, 12)


def test_encode_dxt_hits_the_formula_and_decodes_back() -> None:
    """etcpak 真的能编：逐级编到 1x1、长度 == 公式、而且解回来还是 w*h*4 字节。"""
    import etcpak  # 只在真编的时候拉起来（延迟 import 是本模块的设计）

    payload = textures.encode_dxt(RGBA, TEX_W, TEX_H, 12, TEX_MIPS)
    assert len(payload) == NEW_COMPLETE, "etcpak 编出来的长度与公式不符"
    assert textures.check_texture_streaming(
        {
            "m_Width": TEX_W,
            "m_Height": TEX_H,
            "m_CompleteImageSize": len(payload),
            "m_TextureFormat": 12,
            "m_MipCount": TEX_MIPS,
            "m_StreamData": {"path": TEXTURE_PATH, "offset": 0, "size": len(payload)},
        }
    )["ok"] is True
    # 尾级（2x2 / 1x1）必须被补成一块再编 —— etcpak 直接吃 1x1 会抛
    with pytest.raises(ValueError):
        etcpak.compress_bc3(b"\x00" * 4, 1, 1)
    # 只编一级 -> 长度必然不等于整条链（尺子能红）
    assert len(textures.encode_dxt(RGBA, TEX_W, TEX_H, 12, 1)) != NEW_COMPLETE
    with pytest.raises(ValueError):
        textures.encode_dxt(RGBA[:1024], TEX_W, TEX_H, 12, TEX_MIPS)


def test_set_texture_streaming_changes_exactly_the_four_fields() -> None:
    """纯函数：四个字段**一起**改，别的字段一个字不动，入参也不许被改。"""
    tree = _unpack_texture(_texture_object())
    out = textures.set_texture_streaming(
        tree, format_id=12, complete_image_size=NEW_COMPLETE, offset=1234, size=NEW_COMPLETE
    )
    assert out["m_TextureFormat"] == 12
    assert out["m_CompleteImageSize"] == NEW_COMPLETE
    assert out["m_StreamData"] == {"path": TEXTURE_PATH, "offset": 1234, "size": NEW_COMPLETE}
    assert out["m_Width"] == tree["m_Width"] and out["m_Height"] == tree["m_Height"]
    assert out["m_MipCount"] == tree["m_MipCount"] and out["m_Name"] == tree["m_Name"]
    # 入参不动
    assert tree["m_TextureFormat"] == 29
    assert tree["m_StreamData"]["offset"] == 64
    # 负控：没有 m_StreamData 的树不许改
    with pytest.raises(KeyError):
        textures.set_texture_streaming(
            {"m_TextureFormat": 29}, format_id=12, complete_image_size=1, offset=0, size=1
        )


# --------------------------------------------------------------------------- #
# H1：格式转换（inplace 改四个字段 + appends 追加像素，**同一次写回**）
# --------------------------------------------------------------------------- #
def test_h1_format_conversion_reads_back_as_dxt5(bundle: Path, tmp_path: Path) -> None:
    """H1：读回对象的 m_TextureFormat == 12、m_CompleteImageSize == 新值、
    m_StreamData.size == 新字节数 —— 三个都对。"""
    with ufs.UfsReader(bundle) as reader:
        node1_size = reader.nodes[1].size
        assert reader.nodes[1].offset == reader.nodes[0].size
    old_bytes = _objects(bundle)[TEX_PID]
    old_tree = _unpack_texture(old_bytes)
    assert old_tree["m_TextureFormat"] == 29
    assert textures.read_texture_fields(old_tree)["format_name"] == "DXT5Crunched"

    payload = textures.encode_dxt(RGBA, TEX_W, TEX_H, 12, TEX_MIPS)
    assert len(payload) == NEW_COMPLETE
    # ⚠ 追加落点：**先**算出来再改对象（工单 §2 ① 的要害）——
    # 口径与 writer._stream_append_offset 同一个（节点尾按 16 对齐）
    aligned = node1_size + (-node1_size) % writer.STREAM_APPEND_ALIGN
    new_tree = textures.set_texture_streaming(
        old_tree,
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=aligned,
        size=len(payload),
    )
    stream = new_tree["m_StreamData"]
    new_bytes = _pack_texture(
        complete_image_size=new_tree["m_CompleteImageSize"],
        format_id=new_tree["m_TextureFormat"],
        offset=stream["offset"],
        size=stream["size"],
        path=stream["path"],
    )
    assert len(new_bytes) == len(old_bytes), "四个字段都是定长标量 ⇒ 对象必须等长"

    report = _write(
        bundle,
        writer.DeltaChange(
            inplace=(
                writer.ObjectChange(
                    path_id=TEX_PID, payload=new_bytes, name="SYNTH_TEX", type_name="Texture2D"
                ),
            ),
            appends=(
                writer.StreamAppend(node_path=NODE1, payload=payload, owner_path_id=TEX_PID),
            ),
        ),
        tmp_path,
    )
    back = _unpack_texture(_objects(bundle)[TEX_PID])
    assert back["m_TextureFormat"] == 12, back
    assert back["m_CompleteImageSize"] == NEW_COMPLETE, back
    assert back["m_StreamData"]["size"] == len(payload), back
    assert back["m_StreamData"]["offset"] == aligned, back
    check = textures.check_texture_streaming(back)
    assert check["ok"] is True, check["problems"]
    assert report.verify["F2 读回"]["ok"] is True
    # F1 那一行里**只有一个真对象行**（追加不造行；工单 §9.10.6 的判据一行）
    assert len(report.plan.rows) == 1, report.plan.rows
    assert report.plan.rows[0].path_id == TEX_PID
    assert report.plan.rows[0].new_bytes == len(new_bytes)
    assert "节点尾追加" in "\n".join(report.plan.criterion_lines())
    # 对象**声明**的那个偏移，真的指着那块新像素（不是"长度对"就算数）
    with ufs.UfsReader(bundle) as reader:
        node1 = reader.nodes[1]
        span = back["m_StreamData"]
        got = bytes(reader.read_range(node1.offset + span["offset"], span["size"]))
        assert got == payload, "对象声明的 offset 指着的不是新像素"


# --------------------------------------------------------------------------- #
# H2：Δ 正确性（对象表 / 节点表 / blocksInfo / 只有 3 块重压 + 其后块原样拷贝）
# --------------------------------------------------------------------------- #
def test_h2_object_growth_moves_it_to_the_node_tail_and_rebuilds_tables(
    bundle: Path, tmp_path: Path
) -> None:
    """H2：对象表 byte_start/byte_size 更新、node[0].size 更新、node[1].offset 平移、
    blocksInfo 重建、UfsReader 能读回、块的解压长度和 == 新解压流长度。"""
    before_nodes, before_blocks, before_total = _nodes_and_blocks(bundle)
    before_bytes = bundle.read_bytes()
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=4242,
        size=NEW_COMPLETE,
        filler=4_000,
    )
    change = _growth(grown)
    plan = writer.plan_delta(bundle, change)
    assert plan.allow_grow is True
    assert "禁止写回" not in "\n".join(plan.criterion_lines())
    point, inserted = plan.insert_points[0]
    # 插进去的是「pad + 整个新对象」（不是"增长量"）：pad = 插入量 - 新对象长度
    pad = inserted - len(grown)
    assert 0 <= pad < 8, f"8 字节对齐的填充量 {pad} 不在 [0, 8)"
    assert point == before_nodes[0][1], "插入点必须是 node[0] 的尾部"

    report = _write(bundle, change, tmp_path)
    assert report.plan.new_file_size == bundle.stat().st_size

    with ufs.UfsReader(bundle) as reader:
        node0, node1 = reader.nodes[0], reader.nodes[1]
        info = ufs.read_serialized_file(reader, node0)
        assert node0.size == before_nodes[0][1] + inserted
        assert node1.offset == before_nodes[1][0] + inserted
        assert node1.size == before_nodes[1][1], "对象变长不该动 node[1] 的大小"
        assert len(reader.blocks) > before_blocks, "blocksInfo 没被重建（块数没变）"
        assert sum(b.uncompressed for b in reader.blocks) == reader.total_uncompressed
        assert reader.total_uncompressed == before_total + inserted
        # 对象表那一项：byte_start 8 字节对齐、byte_size == 新字节数
        found = [(stored, size) for _i, pid, stored, size, _tid in info.objects if pid == TEX_PID]
        assert len(found) == 1
        stored, size = found[0]
        assert size == len(grown), "对象表的 byte_size 没跟着变长"
        # 对齐口径（设计稿 §9.9 实测）：**表项里的 byte_start（= stored）** mod 8 == 0。
        # 真件里 data_offset 本身也是 8 的倍数 ⇒ 解压流绝对位置同样 8 对齐。
        assert stored % 8 == 0, f"对象表 byte_start={stored} 不是 8 字节对齐（工单 §6 坑 6）"
        absolute = node0.offset + info.data_offset + stored
        if (node0.offset + info.data_offset) % 8 == 0:
            assert absolute % 8 == 0
        assert bytes(reader.read_range(absolute - pad, inserted)) == (
            b"\x00" * pad + grown
        ), "搬过去的字节不对（含对齐填充）"
        blob = bytes(reader.read_range(absolute, size))
        assert blob == grown
        assert _unpack_texture(blob)["m_TextureFormat"] == 12
        # 解压流长度 = 节点表长度
        assert node0.offset + node0.size + node1.size == reader.total_uncompressed
    assert bundle.read_bytes() != before_bytes
    assert _objects(bundle)[DUMMY_A] == _blob(b"A", 400), "别的对象被动了"


def test_h2_shorter_object_also_goes_through_the_append_path(
    bundle: Path, tmp_path: Path
) -> None:
    """口径（Lead 2026-10-03 ①）：**变短也走追加** —— byte_size 写小、节点照样增长、
    旧字节留成死区。"""
    before_nodes, _blocks, before_total = _nodes_and_blocks(bundle)
    old_size = len(_objects(bundle)[TEX_PID])
    shorter = _texture_object(
        format_id=12, complete_image_size=NEW_COMPLETE, offset=8, size=NEW_COMPLETE, path="a.resS"
    )
    assert len(shorter) < old_size, "fixture 必须真的变短"
    change = _growth(shorter)
    plan = writer.plan_delta(bundle, change)
    _point, inserted = plan.insert_points[0]
    assert plan.rows[0].old_bytes == old_size
    assert plan.rows[0].new_bytes == len(shorter)
    assert plan.rows[0].level == writer.LEVEL_GROW

    _write(bundle, change, tmp_path)
    with ufs.UfsReader(bundle) as reader:
        node0, node1 = reader.nodes[0], reader.nodes[1]
        info = ufs.read_serialized_file(reader, node0)
        assert node0.size == before_nodes[0][1] + inserted, "变短也照样扩节点"
        assert node1.offset == before_nodes[1][0] + inserted
        assert reader.total_uncompressed == before_total + inserted
        found = [(stored, size) for _i, pid, stored, size, _tid in info.objects if pid == TEX_PID]
        assert len(found) == 1
        stored, size = found[0]
        assert size == len(shorter), "byte_size 没按 payload 的**实际**长度写小"
        assert size < old_size
        assert stored % 8 == 0, "变短那条路的 byte_start 也必须 8 字节对齐"
        absolute = node0.offset + info.data_offset + stored
        assert bytes(reader.read_range(absolute, size)) == shorter
        assert _unpack_texture(_objects(bundle)[TEX_PID])["m_StreamData"]["path"] == "a.resS"


def test_h2_only_three_blocks_are_recompressed_and_the_rest_are_verbatim(
    bundle: Path, tmp_path: Path
) -> None:
    """H2 的**机制读数**（工单 §2 ② / 完成信号 ③）：

    插入点落在块中间 ⇒ 只有 **3 个块**重新压（原块的头 + 新字节 + 原块的尾），
    其后所有块的**压缩字节逐字节等于原文件里的那几块**。
    """
    with ufs.UfsReader(bundle) as reader:
        point = reader.nodes[0].size
        old_block0 = reader.blocks[0].uncompressed
        old_count = len(reader.blocks)
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=999,
        size=NEW_COMPLETE,
        filler=6_000,
    )
    change = _growth(grown)
    plan = writer.plan_delta(bundle, change)
    assert point % old_block0 != 0, "fixture 必须让插入点落在块**中间**（否则拆不出 3 块）"
    assert len(plan.recompressed_blocks) == 3, (
        f"插入点落在块中间时应当只有 3 块重压，实测 {list(plan.recompressed_blocks)}"
    )
    assert len(plan.verbatim_blocks) == old_count - 1, plan.verbatim_blocks
    assert plan.insert_points[0][0] == point

    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    report = _write(bundle, change, tmp_path)
    assert report.verify["H2 只有 N 块重压"]["ok"] is True, report.verify["H2 只有 N 块重压"]
    assert report.verify["H2 只有 N 块重压"]["recompressed"] == 3

    # 独立复算（⛔ 不走 writer 的 verify）：新文件里那几块的压缩字节逐字节 == 原文件
    with ufs.UfsReader(bundle) as new_reader, ufs.UfsReader(original) as old_reader:
        indexes = list(plan.recompressed_blocks)
        head_len = new_reader.blocks[indexes[0]].uncompressed
        mid_len = new_reader.blocks[indexes[1]].uncompressed
        tail_len = new_reader.blocks[indexes[2]].uncompressed
        assert head_len + tail_len == old_block0, "原块 0 没有被切成「头 + 尾」"
        assert mid_len == plan.insert_points[0][1], "中间那块不是新字节"
        same = 0
        with bundle.open("rb") as new_file, original.open("rb") as old_file:
            for new_index, old_index in plan.verbatim_blocks:
                new_file.seek(new_reader.block_foff[new_index])
                got = new_file.read(new_reader.blocks[new_index].compressed)
                old_file.seek(old_reader.block_foff[old_index])
                want = old_file.read(old_reader.blocks[old_index].compressed)
                assert got == want, f"新块 {new_index} 不是原样拷贝（对应旧块 {old_index}）"
                same += 1
        assert same == old_count - 1, f"逐字节比过的原样块 {same} != {old_count - 1}"


def test_block_split_self_proof_without_any_rep(bundle: Path) -> None:
    """**块划分自由选**这条机制的裸自证（⛔ 不借元数据补丁的忙）：

    只给一个插入点、不给任何等长改写，断言 3 块重压 + 其后块"原样拷贝"的映射。
    """
    with ufs.UfsReader(bundle) as reader:
        point = reader.nodes[0].size
        old_sizes = [b.uncompressed for b in reader.blocks]
        old_count = len(reader.blocks)
        data = _blob(b"\xff", 5_000)
        walk = writer._delta_block_walk(reader, [], [(point, data)])
        assert walk.recompressed == [0, 1, 2], walk.recompressed
        new_sizes = [b.uncompressed for b in walk.new_blocks]
        assert new_sizes[0] == point
        assert new_sizes[1] == len(data)
        assert new_sizes[0] + new_sizes[2] == old_sizes[0], "原块没被切成「头 + 尾」"
        assert new_sizes[3:] == old_sizes[1:], "插入点之后的块边界必须一个字都不动"
        # 旧块 0 被拆成新块 0/1/2 ⇒ 旧块 j（j>=1）落到新块 j+2，且必须是"原样拷贝"
        assert walk.verbatim_from == {old + 2: old for old in range(1, old_count)}
        # 原样拷贝的块：块表三元组逐字段抄旧表
        for new_index, old_index in walk.verbatim_from.items():
            old = reader.blocks[old_index]
            new = walk.new_blocks[new_index]
            assert (new.uncompressed, new.compressed, new.flags) == (
                old.uncompressed,
                old.compressed,
                old.flags,
            )
        # 双 CRC：独立复算两边
        fresh = 0
        for index in range(old_count):
            fresh = zlib.crc32(
                reader.read_range(reader.block_dstart[index], reader.blocks[index].uncompressed),
                fresh,
            )
        assert walk.crc_old == fresh & 0xFFFFFFFF
        # crc_new == crc32(旧流[0:p) + 新字节 + 旧流[p:L))（独立复算，⛔ 不用 walk 的产物）
        independent = zlib.crc32(reader.read_range(0, point))
        independent = zlib.crc32(data, independent)
        independent = zlib.crc32(
            reader.read_range(point, reader.total_uncompressed - point), independent
        )
        assert walk.crc_new == independent & 0xFFFFFFFF


def test_h2_stream_append_lands_at_the_node_tail_and_returns_the_new_offset(
    bundle: Path, tmp_path: Path
) -> None:
    """工单 §2 ③：流式追加的插入点 = 节点尾 ⇒ node[1].size 长、node[1].offset 不动、
    node[0] 一个字节不动；返回的新节点内偏移 == 追加前 node[1].size。"""
    before_nodes, _blocks, before_total = _nodes_and_blocks(bundle)
    before = _objects(bundle)
    payload = _blob(b"\xff", 7_777)
    # owner_path_id = 0 = **显式放弃**「对象声明必须与落点一致」那条判据
    # （只想把字节挂到节点尾，不指定属主）
    change = writer.DeltaChange(
        appends=(writer.StreamAppend(node_path=NODE1, payload=payload),)
    )
    plan = writer.plan_delta(bundle, change)
    # 口径：追加起点按 STREAM_APPEND_ALIGN=16 对齐（真件实测 200/200 是 16 的倍数）
    aligned = before_nodes[1][1] + (-before_nodes[1][1]) % writer.STREAM_APPEND_ALIGN
    pad = aligned - before_nodes[1][1]
    assert plan.append_offsets == ((NODE1, aligned, len(payload)),), plan.append_offsets
    # ⛔ 追加**不造 PlanRow**（它不是对象；rows 是「对象级 delta」那条判据的输入）
    assert plan.rows == (), plan.rows
    assert "节点尾追加" in "\n".join(plan.criterion_lines())
    _write(bundle, change, tmp_path)
    after_nodes, _blocks2, after_total = _nodes_and_blocks(bundle)
    assert after_nodes[0] == before_nodes[0], "追加不该动 node[0]"
    assert after_nodes[1][0] == before_nodes[1][0], "追加不该动 node[1] 的 offset"
    assert after_nodes[1][1] == before_nodes[1][1] + pad + len(payload)
    assert after_total == before_total + pad + len(payload)
    assert _objects(bundle)[TEX_PID] == before[TEX_PID], "追加不该动 node[0] 的对象字节"
    with ufs.UfsReader(bundle) as reader:
        node1 = reader.nodes[1]
        got = bytes(reader.read_range(node1.offset + aligned, len(payload)))
        assert got == payload, "追加的那一段读回来不是新字节"
        if pad:
            filler_bytes = bytes(reader.read_range(node1.offset + before_nodes[1][1], pad))
            assert filler_bytes == b"\x00" * pad, "对齐填充必须全是 0"


def test_synthetic_object_satisfies_the_real_streamdata_layout(bundle: Path) -> None:
    """合成件的 Texture2D 必须过 writer 的**真件布局自证**（path 是最后一个字段、
    4 字节对齐正好落对象末尾、后缀是流式后缀）—— 否则这些用例证明不了真件那条链。"""
    info = writer._stream_info_of_object(_objects(bundle)[TEX_PID], path_id=TEX_PID)
    assert info.basename == NODE1
    assert info.offset == 64 and info.size == OLD_STREAM_SIZE
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=4242,
        size=NEW_COMPLETE,
        filler=4_000,
    )
    grown_info = writer._stream_info_of_object(grown, path_id=TEX_PID)
    assert grown_info.offset == 4242 and grown_info.size == NEW_COMPLETE


def test_append_landing_point_must_match_the_object_declaration(bundle: Path) -> None:
    """负控 + 正控：对象说像素在 A、我们却往 B 写（**静默错件**的入口）必须被拦下；
    把对象改成指向落点之后，同一条判据必须变绿。"""
    payload = _blob(b"\xff", 4_096)
    # 负控：对象里声明的还是老区间（offset=64 / size=3000）⇒ 必须红
    with pytest.raises(AssetError, match="m_StreamData 不符"):
        writer.plan_delta(
            bundle,
            writer.DeltaChange(
                appends=(
                    writer.StreamAppend(node_path=NODE1, payload=payload,
                                        owner_path_id=TEX_PID),
                )
            ),
        )
    # 正控：把对象改成指向落点（就是 ① 那条链的形状）⇒ 绿
    with ufs.UfsReader(bundle) as reader:
        node1_size = reader.nodes[1].size
    aligned = node1_size + (-node1_size) % writer.STREAM_APPEND_ALIGN
    obj = _pack_texture(offset=aligned, size=len(payload))
    assert len(obj) == len(_texture_object())
    plan = writer.plan_delta(
        bundle,
        writer.DeltaChange(
            inplace=(writer.ObjectChange(path_id=TEX_PID, payload=obj, name="SYNTH_TEX"),),
            appends=(
                writer.StreamAppend(node_path=NODE1, payload=payload, owner_path_id=TEX_PID),
            ),
        ),
    )
    assert plan.append_offsets[0][1] == aligned


# --------------------------------------------------------------------------- #
# H3：其它未变（**按平移后的坐标**比）+ 尺子正控
# --------------------------------------------------------------------------- #
def test_h3_other_bytes_unchanged_under_shifted_coordinates(
    bundle: Path, tmp_path: Path
) -> None:
    """H3：N[0:p) == O[0:p) 且 N[p+Δ:L+Δ) == O[p:L) —— 期望改动之外的差异 == 0。"""
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=555,
        size=NEW_COMPLETE,
        filler=3_000,
    )
    change = _growth(grown)
    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    report = _write(bundle, change, tmp_path)
    entry = report.verify["H3 其它未变（平移后坐标）"]
    assert entry["ok"] is True, entry
    assert entry["diff_bytes"] == 0

    # 独立复算（⛔ 不走 writer 的 verify）
    with ufs.UfsReader(original) as old_reader:
        internal = writer._plan_delta(old_reader, change, catalog_path=None)
    reps = [(rep.start, rep.end) for rep in internal.delta.reps]
    point, inserted = internal.delta.inserts[0][0], len(internal.delta.inserts[0][1])
    with ufs.UfsReader(original) as old_reader, ufs.UfsReader(bundle) as new_reader:
        head_old = bytes(old_reader.read_range(0, point))
        head_new = bytes(new_reader.read_range(0, point))
        tail_len = old_reader.total_uncompressed - point
        assert bytes(new_reader.read_range(point + inserted, tail_len)) == bytes(
            old_reader.read_range(point, tail_len)
        ), "平移后段（p 之后）必须逐字节相同"
        diffs = [
            index
            for index, (x, y) in enumerate(zip(head_old, head_new, strict=True))
            if x != y and not any(lo <= index < hi for lo, hi in reps)
        ]
        assert diffs == [], f"平移前段里有 {len(diffs)} 个字节变了（期望改动之外）"


def test_h3_ruler_goes_red_when_the_insertion_point_shifts_by_one_byte(
    bundle: Path, tmp_path: Path
) -> None:
    """H3 的**尺子正控**：把插入点挪 1 字节 ⇒ 同一条比对器必须报「期望之外的差异 > 0」。

    ⛔ 不能红就不算尺子（AGENTS §0.9 动作 2）。
    """
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=555,
        size=NEW_COMPLETE,
        filler=3_000,
    )
    change = _growth(grown)
    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    with ufs.UfsReader(original) as old_reader:
        internal = writer._plan_delta(old_reader, change, catalog_path=None)
    point, inserted = internal.delta.inserts[0][0], len(internal.delta.inserts[0][1])
    reps = internal.delta.reps
    _write(bundle, change, tmp_path)
    with ufs.UfsReader(original) as old_reader, ufs.UfsReader(bundle) as new_reader:
        data = bytes(new_reader.read_range(point, inserted))
        # 绿：插入点正确 ⇒ 期望改动之外的差异必须是 0
        _total_ok, outside_ok, _crc_ok = writer._delta_compare(
            old_reader, new_reader, [(point, data)], reps
        )
        assert outside_ok == 0, f"正控基线就该是 0，实测 {outside_ok}"
        # 红：把插入点挪 1 字节 ⇒ 同一条尺子必须报 > 0
        _total_bad, outside_bad, _crc_bad = writer._delta_compare(
            old_reader, new_reader, [(point + 1, data)], reps
        )
    assert outside_bad > 0, (
        "把插入点挪 1 字节之后比对器仍报 0 差异 —— 这把尺子红不了"
    )


# --------------------------------------------------------------------------- #
# H5：catalog
# --------------------------------------------------------------------------- #
def test_h5_catalog_crc_and_bundle_size_stay_consistent(bundle: Path, tmp_path: Path) -> None:
    """H5：写 Δ 之后，磁盘 crc32(逐块解压拼接) == catalog 的 m_Crc；
    m_BundleSize == 文件大小（双向一致 + 独立复核）。"""
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=1_000,
        size=NEW_COMPLETE,
        filler=2_500,
    )
    change = _growth(grown)
    plan0 = writer.plan_delta(bundle, change)
    real = tmp_path / _bundle_name(0)
    shutil.copy2(bundle, real)
    catalog = write_catalog_sized(
        tmp_path / "catalog.json", [(0, plan0.disk_crc, real.stat().st_size)]
    )
    report = _write(real, change, tmp_path, catalog_path=catalog)
    assert report.verify["F4 catalog"]["ok"] is True, report.verify["F4 catalog"]
    disk_crc = 0
    with ufs.UfsReader(real) as reader:
        for index, block in enumerate(reader.blocks):
            disk_crc = zlib.crc32(
                reader.read_range(reader.block_dstart[index], block.uncompressed), disk_crc
            )
    options = _catalog_options(catalog, _hash_of(0))
    assert options is not None
    assert options["m_Crc"] == disk_crc & 0xFFFFFFFF, "磁盘 CRC 与 catalog 里写的对不上"
    assert options["m_BundleSize"] == real.stat().st_size
    assert json.loads(catalog.read_bytes().decode("utf-8"))["m_ExtraDataString"]


# --------------------------------------------------------------------------- #
# H7：负控（读回校验必须能红）
# --------------------------------------------------------------------------- #
def test_h7_negative_control_wrong_complete_image_size_goes_red(
    bundle: Path, tmp_path: Path
) -> None:
    """H7：故意把 m_CompleteImageSize 写错 ⇒ **读回校验必须红**（能红才算尺子）。"""
    good_tree = {
        "m_Width": TEX_W,
        "m_Height": TEX_H,
        "m_CompleteImageSize": NEW_COMPLETE,
        "m_TextureFormat": 12,
        "m_MipCount": TEX_MIPS,
        "m_StreamData": {"path": TEXTURE_PATH, "offset": 0, "size": NEW_COMPLETE},
    }
    assert textures.check_texture_streaming(good_tree)["ok"] is True, "正控必须先绿"

    wrong = dict(good_tree)
    wrong["m_CompleteImageSize"] = NEW_COMPLETE + 1
    bad = textures.check_texture_streaming(wrong)
    assert bad["ok"] is False, "把 m_CompleteImageSize 写错却报绿 —— 这把尺子红不了"
    assert any("CompleteImageSize" in problem for problem in bad["problems"]), bad["problems"]

    # 真的写错一次：落盘之后**读回**校验同样必须红
    payload = textures.encode_dxt(RGBA, TEX_W, TEX_H, 12, TEX_MIPS)
    with ufs.UfsReader(bundle) as reader:
        node1_size = reader.nodes[1].size
    aligned = node1_size + (-node1_size) % writer.STREAM_APPEND_ALIGN
    written = _pack_texture(
        complete_image_size=NEW_COMPLETE + 1,
        format_id=12,
        offset=aligned,
        size=len(payload),
    )
    _write(
        bundle,
        writer.DeltaChange(
            inplace=(writer.ObjectChange(path_id=TEX_PID, payload=written, name="SYNTH_TEX",
                                         type_name="Texture2D"),),
            appends=(writer.StreamAppend(node_path=NODE1, payload=payload,
                                         owner_path_id=TEX_PID),),
        ),
        tmp_path,
    )
    back = _unpack_texture(_objects(bundle)[TEX_PID])
    assert back["m_CompleteImageSize"] == NEW_COMPLETE + 1
    assert textures.check_texture_streaming(back)["ok"] is False, (
        "写错的那个数落到盘上之后，读回校验居然还是绿的"
    )


# --------------------------------------------------------------------------- #
# 负控 / 红线 / 冻结接口
# --------------------------------------------------------------------------- #
def test_delta_rejects_equal_length_growth_and_mismatched_inplace(bundle: Path) -> None:
    """负控：**等长**走 Δ 必须被拦下并提示走 ObjectChange；
    inplace 长度不符必须被拦；空改动与指向 node[0] 的追加也必须被拦。"""
    equal = _texture_object()
    with pytest.raises(AssetError, match="等长请走"):
        writer.plan_delta(bundle, _growth(equal))
    with pytest.raises(AssetError, match="等长才走 inplace"):
        writer.plan_delta(bundle, writer.DeltaChange(
            inplace=(writer.ObjectChange(path_id=TEX_PID, payload=equal + b"x"),)
        ))
    with pytest.raises(AssetError, match="空的"):
        writer.plan_delta(bundle, writer.DeltaChange())
    with pytest.raises(AssetError, match=r"node\[0\]"):
        writer.plan_delta(bundle, writer.DeltaChange(
            appends=(writer.StreamAppend(node_path=NODE0, payload=b"\xff" * 8),)
        ))


def test_delta_post_verify_uses_h2_h3_and_rows_hold_only_real_objects(
    bundle: Path, tmp_path: Path
) -> None:
    """Δ 的写后复核走 H2 / H3，⛔ **不复用** F7「对象级 delta == 0」（变长下它天然不成立）；
    WritebackPlan.rows 里只许有**真正的对象**行。"""
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=1_234,
        size=NEW_COMPLETE,
        filler=2_000,
    )
    report = _write(bundle, _growth(grown), tmp_path)
    keys = set(report.verify)
    assert "F7 等长" not in keys, "Δ 复用 L1 的那条判据 = 把读数弄成没意义"
    assert "F7 Δ 长度" not in keys
    assert {
        "H2 块表 / 节点表",
        "H2 只有 N 块重压",
        "H2 节点增长（Δ）",
        "H3 其它未变（平移后坐标）",
    } <= keys, sorted(keys)
    assert len(report.plan.rows) == 1, report.plan.rows
    assert report.plan.rows[0].path_id == TEX_PID
    assert report.plan.rows[0].old_bytes == len(_texture_object())
    assert report.plan.rows[0].new_bytes == len(grown)
    for key in sorted(keys):
        value = report.verify[key]
        assert isinstance(value, dict) and value.get("ok") is True, (key, value)


def test_delta_dry_run_writes_nothing(bundle: Path, tmp_path: Path) -> None:
    """默认 dry_run：算得出计划，但包一个字节都没动（W5/W6 同款默认）。"""
    before = bundle.read_bytes()
    grown = _texture_object(
        format_id=12,
        complete_image_size=NEW_COMPLETE,
        offset=77,
        size=NEW_COMPLETE,
        filler=1_200,
    )
    report = writer.write_delta(
        bundle,
        _growth(grown),
        dry_run=True,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    assert report.dry_run is True
    assert bundle.read_bytes() == before
    assert not (tmp_path / "backup").exists()
    text = "\n".join(report.report_lines())
    assert "Δ 机制" in text and "干跑" in text


def test_frozen_signatures_and_dataclass_fields() -> None:
    """冻结接口逐条落地：名字、参数、字段名与顺序（B 组要照着它写网格）。"""
    assert list(inspect.signature(writer.plan_delta).parameters) == [
        "bundle_path",
        "change",
        "catalog_path",
    ]
    assert list(inspect.signature(writer.write_delta).parameters) == [
        "bundle_path",
        "change",
        "dry_run",
        "backup_dir",
        "project_root",
        "game_root",
        "catalog_path",
        "allow_running_game",
        "progress",
        "token",
        # ADR 0007：反向 Δ 那条链（patch_store 给了就不拷整包）
        "patch_store",
        "patch_label",
    ]
    assert list(inspect.signature(writer.writeback_unit_texture_format).parameters) == [
        "game_root",
        "unit_key",
        "material_name",
        "slot",
        "rgba",
        "format_id",
        "dry_run",
        "project_root",
        # ADR 0007：反向 Δ 那条链（2026-10-11 补 —— 与 writeback_unit_material 对称：
        # 这条路原来是变长写、却不记反向 Δ ⇒ 走它就没有可还原性）
        "patch_store",
        "patch_label",
    ]
    assert [f.name for f in dataclasses.fields(writer.ObjectGrowth)] == [
        "path_id",
        "payload",
        "name",
        "type_name",
    ]
    assert [f.name for f in dataclasses.fields(writer.StreamAppend)] == [
        "node_path",
        "payload",
        "owner_path_id",
    ]
    assert [f.name for f in dataclasses.fields(writer.DeltaChange)] == [
        "inplace",
        "growths",
        "appends",
        # W8-A：新增对象（新 pathID）走同一个事务 —— Lead 2026-10-03 批的接口扩展
        "added",
    ]
    for name in ("plan_delta", "write_delta", "writeback_unit_texture_format", "DeltaChange",
                 "ObjectGrowth", "StreamAppend"):
        assert name in writer.__all__, f"__all__ 里缺 {name}"


def test_same_coordinate_inserts_are_concatenated() -> None:
    """2026-10-06（N1 现场）：**同坐标的插入必须先串接**，否则每一条各开一个块。

    起因：194 个对象的「加一批对象」= 388 个插入点但**只有 2 个坐标** ——
    旧口径下重压块 392、<4 KiB 的小块 361 个（含 196 个 24 B 的纯元数据块）、块尺寸档位 83 种；
    同坐标串接之后重压块 6、小块 0、档位 8（复算：_work/n1c-block-table.py）。
    本用例锁的是**纯函数层**的这条口径（不需要真实包、不写盘）。
    """
    inserts = [
        (100, b"aaaa"),  # 同坐标三条 —— 必须串成一条
        (100, b"bb"),
        (100, b"c" * 10),
        (200, b"dddd"),  # 另一个坐标 —— 不许并进来
        (200, b"e"),
    ]
    groups, sources = writer._merge_inserts(inserts)
    assert [point for point, _data in groups] == [100, 200]
    assert [len(data) for _point, data in groups] == [16, 5], "字节总数必须守恒"
    assert sources == [(0, 1, 2), (3, 4)], "每条合并结果要记得自己由哪几条组成（行号回填靠它）"

    # 正控：坐标不同就不许合并
    assert len(writer._merge_inserts([(1, b"x"), (2, b"y")])[0]) == 2


def test_same_coordinate_inserts_do_not_open_one_block_each() -> None:
    """回归：同坐标的 194 条插入不许各开一个块（旧口径 = 194 块 / 194 个插入点）。

    ⛔ 这条用**合成块表**直接喂块划分，不开任何真实包。
    """
    blocks = [ufs.Block(uncompressed=65536, compressed=65536, flags=0) for _ in range(4)]
    reader = SimpleNamespace(
        blocks=blocks,
        block_dstart=[0, 65536, 131072, 196608],
        total_uncompressed=262144,
        nodes=[],
        # 块划分要按原样拷贝一个块的字节 —— 合成件给一个定长假的即可（本用例只数块边界）
        read_range=lambda _start, size: b"\x5a" * size,
    )
    inserts = [(100, b"z") for _ in range(194)]
    groups, _sources = writer._merge_inserts(inserts)
    assert len(groups) == 1, f"同坐标的 194 条被分成了 {len(groups)} 组"
    walk = writer._delta_block_walk_groups(reader, [], groups)
    assert len(walk.insert_block) == 1, f"同坐标插入被开成了 {len(walk.insert_block)} 个块"
    assert any(b.uncompressed >= 4096 for b in walk.new_blocks), "串接后必须出现一条 ≥4 KiB 的块"
