"""W6-B2 判据：.resS 流式区间（node[1]）的**等长**写回（G1~G5 / G7）。

**判据一行**：pytest tests/test_writeback_texture.py -> 通过数 **从 0 收集变 N passed**。

| 用例 | 判据（工单 §3） | 通过线 |
|---|---|---|
| test_g1_* | 新贴图字节数 == 原字节数（格式 + 分辨率 + mip 数不变） | 相等 |
| test_g2_* | 写回后 node[0] 逐字节未动（sha256 相等）；尺子正控必须能红 | 差 = 0 / 能红 |
| test_g3_* | 目标区间 == 新像素；区间之外逐字节未变 | 相等 / 差 = 0 |
| test_g4_* | Texture2D 对象未改（对象字节 / m_StreamData / m_CompleteImageSize） | 逐字段未变 |
| test_g5_* | 磁盘 CRC == catalog m_Crc；m_BundleSize == 文件大小 | 双向一致 |
| test_g7_* | 负控：超 1 个字节 -> 报「长度不符」并中止，且包一个字节没动 | 能红 |

## 为什么在**内存里造的小容器**上跑（与 W5 的 F 判据同一个理由）

判据全落在写回器自己的不变量上（节点表、块表、解压流区间、catalog 偏移、CRC 口径），
与容器多大无关：小容器与 3.38 GB 的真件**同构**。真件既做不了秒级回归，也不能在用例里反复写
（⛔ 禁区：不许写游戏目录）。合成件直接复用 tests/test_writeback_l1.py 的构造器
（_build_sf / _build_container 的块表口径 / write_catalog_sized / _crc_of_bundle /
_catalog_options）。

## 合成件的形状（照真件的实测读数）

units 包真件是 **2 个节点**：node[0] = CAB-<hash>（对象数据 2,526,267,644 B）、
node[1] = CAB-<hash>.resS（像素 2,905,277,264 B）。本文件照这个形状造：
node[0] 里一个**流式 Texture2D** 对象，像素整段落在 node[1] 上，
区间 = **节点内相对偏移**（== m_StreamData.offset），绝对区间 = node[1].offset + offset。

对象字节的尾部按**实测布局**（_work/w6_probe_layout2.py，units 包 400 张流式贴图 400/400）::

    ... | offset(u64) | size(u32) | pathlen(i32) | path 字节 | 4 字节对齐填充

靶子的真实读数：512x512 DXT5 全 mip 链（10 级到 1x1）Σ = **349,552 B** == m_StreamData.size，
m_StreamData = @2533061664+349552（W3 记录），绝对区间 [5,059,329,308, 5,059,678,860)，
命中块 #38599~#38602（4 块）。
"""

from __future__ import annotations

import inspect
import shutil
import struct
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

# 现成件（W5 交付，工单 §「现成件」点名直接用）：
from test_writeback_l1 import (
    CHUNK,
    _blob,
    _build_sf,
    _bundle_name,
    _catalog_options,
    _crc_of_bundle,
    _hash_of,
    write_catalog_sized,
)

#: 靶子的真实读数（W6 探针）：512x512 DXT5，10 级 mip，Σ = 349,552 B。
PIXELS = 349_552
#: 流式区间在 .resS 里的**节点内相对偏移**（真件 = 2,533,061,664）。
STREAM_OFFSET = 4096
NODE0 = "CAB-0123456789abcdef0123456789abcdef"
NODE1 = NODE0 + ".resS"
TEX_PID = 4242
RES_PATH = f"archive:/{NODE0}/{NODE1}"
#: 合成对象里「声明图像字节数」那个槽（对应真件的 m_CompleteImageSize）：头 4 B 之后。
SIZE_SLOT = 4


# --------------------------------------------------------------------------- #
# 合成件：2 节点容器（node[0] 对象数据 / node[1] .resS）
# --------------------------------------------------------------------------- #
def _texture_object_bytes(offset: int, size: int, path: str = RES_PATH) -> bytes:
    """造一个流式 Texture2D 的对象字节：尾部就是按实测布局写的 m_StreamData。

    头 8 B = [m_TextureFormat i32][声明图像字节数 u32]（对应真件的 m_CompleteImageSize），
    本文件只拿它做 G4 的「逐字段未变」读数，不假装它是一份完整 typetree。
    """
    path_bytes = path.encode("utf-8")
    head = struct.pack("<iI", 12, size)
    body = (
        struct.pack("<Q", offset)
        + struct.pack("<I", size)
        + struct.pack("<i", len(path_bytes))
        + path_bytes
    )
    pad = (-(len(head) + len(body))) % 4
    return head + body + b"\x00" * pad


def _build_container(sf_blob: bytes, ress_blob: bytes) -> bytes:
    """把 [对象节点][.resS 节点] 两段解压流包成一个最小 UnityFS 容器（块表是**全流**的）。"""
    stream = sf_blob + ress_blob
    blocks: list[ufs.Block] = []
    payload = bytearray()
    for index in range(0, len(stream), CHUNK):
        raw = stream[index : index + CHUNK]
        comp = ufs.lz4hc(raw)
        if len(comp) >= len(raw):
            payload += raw
            blocks.append(ufs.Block(len(raw), len(raw), 0))
        else:
            payload += comp
            blocks.append(ufs.Block(len(raw), len(comp), 3))
    nodes = [
        ufs.Node(offset=0, size=len(sf_blob), flags=4, path=NODE0),
        ufs.Node(offset=len(sf_blob), size=len(ress_blob), flags=0, path=NODE1),
    ]
    raw_info = ufs.serialize_blocks_info(b"\x11" * 16, blocks, nodes)
    comp_info = ufs.lz4hc(raw_info)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", 0, len(comp_info), len(raw_info), 0x43
    )
    total = len(header) + len(comp_info) + len(payload)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", total, len(comp_info), len(raw_info), 0x43
    )
    return header + comp_info + bytes(payload)


def _original_pixels() -> bytes:
    return _blob(b"P", PIXELS)


def _new_pixels() -> bytes:
    """新像素：**等长**，与原来只差**1 个字节**（G3 的总差异读数因此是干净的 1 B）。"""
    original = _original_pixels()
    return bytes([original[0] ^ 0xFF]) + original[1:]


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """合成容器：node[0] = 一个流式 Texture2D 对象；node[1] = .resS（像素全在这里）。"""
    sf = _build_sf([(TEX_PID, _texture_object_bytes(STREAM_OFFSET, PIXELS))])
    ress = _blob(b"S", STREAM_OFFSET) + _original_pixels() + _blob(b"T", 700)
    path = tmp_path / "synth.bundle"
    path.write_bytes(_build_container(sf, ress))
    return path


# --------------------------------------------------------------------------- #
# 读数小工具（都走**产品自己的读侧**）
# --------------------------------------------------------------------------- #
def _nodes(path: Path) -> list[tuple[str, int, int]]:
    with ufs.UfsReader(path) as reader:
        return [(n.path, n.offset, n.size) for n in reader.nodes]


def _node0_span(path: Path) -> tuple[int, int]:
    name, offset, size = _nodes(path)[0]
    assert not name.endswith(".resS"), name
    return offset, offset + size


def _stream_span(path: Path) -> tuple[int, int]:
    for name, offset, _size in _nodes(path):
        if name.endswith(".resS"):
            return offset + STREAM_OFFSET, offset + STREAM_OFFSET + PIXELS
    raise AssertionError("合成件里没有 .resS 节点")


def _object_bytes(path: Path) -> bytes:
    """目标对象的原始字节（对象表 -> 解压流绝对区间 -> read_range）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        for _index, pid, stored, size, _tid in info.objects:
            if pid == TEX_PID:
                start = node.offset + info.data_offset + stored
                return bytes(reader.read_range(start, size))
    raise AssertionError("合成件里没有目标对象")


def _stream_change(path: Path, payload: bytes | None = None) -> writer.StreamChange:
    with ufs.UfsReader(path) as reader:
        return writer.build_stream_change(reader, TEX_PID, payload or _new_pixels())


def _read_span(path: Path, span: tuple[int, int]) -> bytes:
    with ufs.UfsReader(path) as reader:
        return bytes(reader.read_range(span[0], span[1] - span[0]))


def _write(bundle: Path, tmp_path: Path, changes, catalog_path: Path | None = None):
    return writer.write_stream(
        bundle,
        changes,
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
        catalog_path=catalog_path,
    )


# --------------------------------------------------------------------------- #
# ① 冻结接口（③ 涂装面板照着它调，改一个名字就红）
# --------------------------------------------------------------------------- #
def test_frozen_interfaces_and_signatures() -> None:
    """① 冻结的 6 个接口逐条存在，且参数名 / 种类与 task-1 冻结的那版逐字对齐。"""
    def params(func) -> list[tuple[str, str]]:
        return [(p.name, p.kind.name) for p in inspect.signature(func).parameters.values()]

    assert [name for name, _ in params(writer.StreamChange)] == [
        "node_path",
        "offset",
        "old_size",
        "payload",
        "owner_path_id",
        "name",
        "type_name",
    ]
    assert params(writer.build_stream_change) == [
        ("reader", "POSITIONAL_OR_KEYWORD"),
        ("path_id", "POSITIONAL_OR_KEYWORD"),
        ("payload", "POSITIONAL_OR_KEYWORD"),
    ]
    assert params(writer.plan_stream_writeback) == [
        ("bundle_path", "POSITIONAL_OR_KEYWORD"),
        ("changes", "POSITIONAL_OR_KEYWORD"),
        ("catalog_path", "KEYWORD_ONLY"),
    ]
    assert params(writer.write_stream) == [
        ("bundle_path", "POSITIONAL_OR_KEYWORD"),
        ("changes", "POSITIONAL_OR_KEYWORD"),
        ("dry_run", "KEYWORD_ONLY"),
        ("backup_dir", "KEYWORD_ONLY"),
        ("project_root", "KEYWORD_ONLY"),
        ("game_root", "KEYWORD_ONLY"),
        ("catalog_path", "KEYWORD_ONLY"),
        ("allow_running_game", "KEYWORD_ONLY"),
        ("progress", "KEYWORD_ONLY"),
        ("token", "KEYWORD_ONLY"),
        # ADR 0007：反向 Δ 那条链（patch_store 给了就不拷整包）
        ("patch_store", "KEYWORD_ONLY"),
        ("patch_label", "KEYWORD_ONLY"),
    ]
    assert params(writer.texture_slot_path_id) == [
        ("tree", "POSITIONAL_OR_KEYWORD"),
        ("slot", "POSITIONAL_OR_KEYWORD"),
    ]
    assert params(writer.writeback_texture_file) == [
        ("game_root", "POSITIONAL_OR_KEYWORD"),
        ("unit_key", "POSITIONAL_OR_KEYWORD"),
        ("material_name", "KEYWORD_ONLY"),
        ("slot", "KEYWORD_ONLY"),
        ("payload", "KEYWORD_ONLY"),
        ("dry_run", "KEYWORD_ONLY"),
        ("project_root", "KEYWORD_ONLY"),
        # ADR 0007：反向 Δ 那条链（2026-10-11 补 —— 与 writeback_unit_material 对称）
        ("patch_store", "KEYWORD_ONLY"),
        ("patch_label", "KEYWORD_ONLY"),
    ]
    for name in (
        "StreamChange",
        "build_stream_change",
        "plan_stream_writeback",
        "write_stream",
        "texture_slot_path_id",
        "writeback_texture_file",
    ):
        assert name in writer.__all__, name


def test_stream_change_is_frozen_and_positional_order_matters(bundle: Path) -> None:
    """StreamChange 是 frozen + slots：字段顺序就是冻结的 7 个（③ 面板按位置也调不错）。"""
    change = _stream_change(bundle)
    assert (change.node_path, change.offset, change.old_size) == (RES_PATH, STREAM_OFFSET, PIXELS)
    assert [f.name for f in fields(change)] == [
        "node_path",
        "offset",
        "old_size",
        "payload",
        "owner_path_id",
        "name",
        "type_name",
    ]
    assert writer.StreamChange.__slots__ == (
        "node_path",
        "offset",
        "old_size",
        "payload",
        "owner_path_id",
        "name",
        "type_name",
    )
    with pytest.raises(FrozenInstanceError):
        change.offset = 0  # type: ignore[misc]


def test_texture_slot_path_id_reads_the_pointer() -> None:
    """⑤ 冻结接口：材质 typetree 的 m_TexEnvs -> 该槽 PPtr 的 pathID；没这个槽就抛。"""
    tree = {
        "m_SavedProperties": {
            "m_TexEnvs": [
                ["_BaseColorMap", {"m_FileID": 0, "m_Texture": {"m_FileID": 0, "m_PathID": 123}}],
                ["_NormalMap", {"m_FileID": 0, "m_Texture": {"m_FileID": 0, "m_PathID": 456}}],
            ]
        }
    }
    assert writer.texture_slot_path_id(tree, "_BaseColorMap") == 123
    assert writer.texture_slot_path_id(tree, "_NormalMap") == 456
    with pytest.raises(KeyError):
        writer.texture_slot_path_id(tree, "_NoSuchSlot")
    empty = {
        "m_SavedProperties": {"m_TexEnvs": [["_BaseColorMap", {"m_Texture": {"m_PathID": 0}}]]}
    }
    with pytest.raises(AssetError):
        writer.texture_slot_path_id(empty, "_BaseColorMap")


# --------------------------------------------------------------------------- #
# G1 / G7 等长与负控
# --------------------------------------------------------------------------- #
def test_g1_equal_length_build_and_plan(bundle: Path) -> None:
    """G1：新贴图字节数 == 原字节数 ⇒ L1；区间落在 .resS 上（⛔ 不是 node[0]）。"""
    change = _stream_change(bundle)
    assert len(change.payload) == change.old_size == PIXELS
    assert change.owner_path_id == TEX_PID
    assert change.type_name == "Texture2D"
    plan = writer.plan_stream_writeback(bundle, [change])
    assert plan.rows[0].level == writer.LEVEL_EQUAL
    assert plan.rows[0].stream_start == _stream_span(bundle)[0], "绝对区间 = 节点.offset + 内偏移"
    assert plan.blocked_reason == ""
    assert "L1" in "\n".join(plan.criterion_lines())


def test_g7_oversize_payload_reports_length_mismatch_and_writes_nothing(
    bundle: Path, tmp_path: Path
) -> None:
    """G7 负控：超 1 个字节 -> 必须报「长度不符」并中止，且包一个字节没动（两条路都拦）。"""
    before = bundle.read_bytes()
    # 路 1：build_stream_change（按对象里的 m_StreamData 复核长度）
    with ufs.UfsReader(bundle) as reader, pytest.raises(AssetError) as exc:
        writer.build_stream_change(reader, TEX_PID, _new_pixels() + b" ")
    assert "长度不符" in str(exc.value), str(exc.value)
    # 路 2：手写 StreamChange（绕过 build_stream_change）也必须被写回链拦住
    bad = writer.StreamChange(
        node_path=RES_PATH,
        offset=STREAM_OFFSET,
        old_size=PIXELS,
        payload=_new_pixels() + b" ",
        owner_path_id=TEX_PID,
    )
    with pytest.raises(AssetError) as exc2:
        _write(bundle, tmp_path, [bad])
    assert "长度不符" in str(exc2.value), str(exc2.value)
    assert bundle.read_bytes() == before, "被拦下了却动了包"


def test_g7_wrong_interval_is_rejected(bundle: Path) -> None:
    """G7 负控：区间与对象里的 m_StreamData 不符（改错图）必须抛，⛔ 不许静默写别处。"""
    wrong = writer.StreamChange(
        node_path=RES_PATH,
        offset=STREAM_OFFSET + 1,
        old_size=PIXELS,
        payload=_new_pixels(),
        owner_path_id=TEX_PID,
    )
    with pytest.raises(AssetError) as exc:
        writer.plan_stream_writeback(bundle, [wrong])
    assert "m_StreamData 不符" in str(exc.value), str(exc.value)
    # 目标节点写成 node[0]（对象数据）也必须红
    into_node0 = writer.StreamChange(node_path=NODE0, offset=0, old_size=64, payload=b"\x00" * 64)
    with pytest.raises(AssetError) as exc2:
        writer.plan_stream_writeback(bundle, [into_node0])
    assert "node[1]" in str(exc2.value), str(exc2.value)


# --------------------------------------------------------------------------- #
# G2 node[0] 未动（含尺子正控）
# --------------------------------------------------------------------------- #
def test_g2_node0_sha256_is_unchanged_by_a_stream_write(bundle: Path, tmp_path: Path) -> None:
    """G2：写回 .resS 之后，node[0] 区间的 sha256 必须与改前**相等**。"""
    span = _node0_span(bundle)
    with ufs.UfsReader(bundle) as reader:
        sha_before = writer._range_sha256(reader, *span)
    report = _write(bundle, tmp_path, [_stream_change(bundle)])
    assert report.verify["G2 node[0] 未动"]["ok"] is True, report.verify["G2 node[0] 未动"]
    with ufs.UfsReader(bundle) as reader:
        assert writer._range_sha256(reader, *span) == sha_before, "node[0] 被动了"
    # 备份里的那一段也必须一样（G2 的「改前」基准就是备份）
    assert report.backup is not None
    with ufs.UfsReader(report.backup) as reader:
        assert writer._range_sha256(reader, *span) == sha_before


def test_g2_ruler_can_go_red(bundle: Path, tmp_path: Path) -> None:
    """G2 的**尺子正控**：同一条 sha256 判据，在「真的改了 node[0]」时必须红。

    ⛔ 不能红就不算尺子（AGENTS §0.9 动作 2）。
    """
    span = _node0_span(bundle)
    with ufs.UfsReader(bundle) as reader:
        sha_before = writer._range_sha256(reader, *span)
    # 走**对象**那条路（write_objects 改的就是 node[0]），G2 判据必须报「变了」
    obj_before = _object_bytes(bundle)
    changed = bytes([obj_before[0] ^ 0xFF]) + obj_before[1:]
    writer.write_objects(
        bundle,
        [writer.ObjectChange(path_id=TEX_PID, payload=changed, name="SYNTH")],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    with ufs.UfsReader(bundle) as reader:
        sha_after = writer._range_sha256(reader, *span)
    assert sha_after != sha_before, "真的改了 node[0]，G2 这把尺子却报相等 —— 红不了"


# --------------------------------------------------------------------------- #
# G3 node[1] 变了（含尺子正控）
# --------------------------------------------------------------------------- #
def test_g3_target_range_equals_new_pixels_and_nothing_else_changes(
    bundle: Path, tmp_path: Path
) -> None:
    """G3：目标区间读回 == 新像素；区间之外的解压流逐字节未变（差 = 0）。"""
    span = _stream_span(bundle)
    payload = _new_pixels()
    report = _write(bundle, tmp_path, [_stream_change(bundle, payload)])
    assert _read_span(bundle, span) == payload, "目标区间不是新像素"
    assert report.verify["G3 node[1] 变了"]["ok"] is True, report.verify["G3 node[1] 变了"]
    assert report.backup is not None
    with ufs.UfsReader(report.backup) as old_reader, ufs.UfsReader(bundle) as new_reader:
        total, outside, _crc = writer._stream_compare(old_reader, new_reader, [span])
        assert total == 1, f"只翻了 1 个字节，比对器却报 {total} B 差异"
        assert outside == 0, f"区间之外还有 {outside} B 差异"
        # 尺子正控：keep 传空 ⇒ 同一批差异必须全部算作「区间之外」
        total_all, outside_all, _crc2 = writer._stream_compare(old_reader, new_reader, [])
    assert total_all == total > 0
    assert outside_all == total_all


def test_g3_empty_replacement_interval_is_rejected(bundle: Path) -> None:
    """G3 负控：空区间不许当替换（否则 G3 会「恒真」—— 没有区间就没有差异）。"""
    empty = writer.StreamChange(node_path=RES_PATH, offset=STREAM_OFFSET, old_size=0, payload=b"")
    with pytest.raises(AssetError):
        writer.plan_stream_writeback(bundle, [empty])


# --------------------------------------------------------------------------- #
# G4 对象未改
# --------------------------------------------------------------------------- #
def test_g4_texture_object_is_byte_identical(bundle: Path, tmp_path: Path) -> None:
    """G4：对象字节逐字节未变；m_StreamData(offset/size/path) 与声明字节数逐字段未变。"""
    before = _object_bytes(bundle)
    info_before = writer._stream_info_of_object(before, path_id=TEX_PID)
    size_slot_before = struct.unpack_from("<I", before, SIZE_SLOT)[0]
    _write(bundle, tmp_path, [_stream_change(bundle)])
    after = _object_bytes(bundle)
    assert after == before, "Texture2D 对象被动过了（G4）"
    info_after = writer._stream_info_of_object(after, path_id=TEX_PID)
    assert (info_after.path, info_after.offset, info_after.size) == (
        info_before.path,
        info_before.offset,
        info_before.size,
    )
    assert info_after.offset == STREAM_OFFSET and info_after.size == PIXELS
    assert struct.unpack_from("<I", after, SIZE_SLOT)[0] == size_slot_before == PIXELS


# --------------------------------------------------------------------------- #
# G5 catalog
# --------------------------------------------------------------------------- #
def test_g5_catalog_crc_and_bundle_size_round_trip(bundle: Path, tmp_path: Path) -> None:
    """G5：写回后磁盘 CRC == catalog m_Crc；m_BundleSize == 文件大小（**独立复算**）。"""
    plan0 = writer.plan_stream_writeback(bundle, [_stream_change(bundle)])
    catalog = write_catalog_sized(
        tmp_path / "catalog.json", [(0, plan0.disk_crc, bundle.stat().st_size)]
    )
    real = tmp_path / _bundle_name(0)
    shutil.copy2(bundle, real)
    report = _write(real, tmp_path, [_stream_change(real)], catalog_path=catalog)
    assert report.verify["F4 catalog"]["ok"] is True, report.verify["F4 catalog"]
    assert report.verify["G2 node[0] 未动"]["ok"] is True
    disk_crc = _crc_of_bundle(real)
    options = _catalog_options(catalog, _hash_of(0))
    assert options is not None
    assert options["m_Crc"] == disk_crc, "catalog 的 m_Crc 与磁盘独立复算不一致"
    assert options["m_BundleSize"] == real.stat().st_size
    # 独立复算的 CRC 必须就是干跑给出的那个（干跑给的是真值，不是估算）
    assert disk_crc == plan0.catalog_crc_new


def test_g5_stale_catalog_crc_blocks_the_write(bundle: Path, tmp_path: Path) -> None:
    """G5 负控：catalog 里写的是**陈旧** CRC ⇒ 写回必须被拦下，包一个字节没动。"""
    catalog = write_catalog_sized(tmp_path / "stale.json", [(0, 12345, bundle.stat().st_size)])
    real = tmp_path / _bundle_name(0)
    shutil.copy2(bundle, real)
    before = real.read_bytes()
    with pytest.raises(AssetError) as exc:
        _write(real, tmp_path, [_stream_change(real)], catalog_path=catalog)
    assert "磁盘 CRC" in str(exc.value), str(exc.value)
    assert real.read_bytes() == before


def test_g5_default_is_dry_run_and_writes_nothing(bundle: Path) -> None:
    """红线自证：write_stream 默认 dry_run=True —— 一个字节都不写（工单 §2）。"""
    before = bundle.read_bytes()
    report = writer.write_stream(bundle, [_stream_change(bundle)])
    assert report.dry_run is True
    assert report.wrote_bundle is False
    assert bundle.read_bytes() == before


def test_g5_backup_is_written_and_matches_before(bundle: Path, tmp_path: Path) -> None:
    """红线自证：真写前必须有备份，且备份 sha256 == 改前（没备份不许写）。"""
    sha_before = writer._sha256(bundle)
    report = _write(bundle, tmp_path, [_stream_change(bundle)])
    assert report.backup is not None and report.backup.is_file()
    assert writer._sha256(report.backup) == sha_before
    assert report.verify["F5 备份"]["ok"] is True
    # 「+1 B 被拦下」那条路不许留下备份副作用之外的任何写入
    assert report.verify["F7 等长"]["delta"] == 0


def test_g5_backup_on_another_drive_is_rejected(bundle: Path, tmp_path: Path) -> None:
    """红线自证（跨盘）：备份目录必须跟随游戏所在盘。"""
    other = "D:\\bamod_w6_never" if str(bundle.drive).upper() != "D:" else "C:\\bamod_w6_never"
    before = bundle.read_bytes()
    with pytest.raises(AssetError) as exc:
        writer.write_stream(
            bundle,
            [_stream_change(bundle)],
            dry_run=False,
            backup_dir=Path(other) / "backup",
            game_root=tmp_path,
        )
    assert "跟随游戏所在盘" in str(exc.value), str(exc.value)
    assert bundle.read_bytes() == before


def test_g1_file_size_and_crc_are_predicted_not_estimated(bundle: Path, tmp_path: Path) -> None:
    """干跑给的是**真值**：写回后的文件大小与 CRC 必须与干跑那一行逐位相同。"""
    change = _stream_change(bundle)
    plan = writer.plan_stream_writeback(bundle, [change])
    report = _write(bundle, tmp_path, [change])
    assert bundle.stat().st_size == plan.new_file_size, "文件大小与干跑不符"
    assert _crc_of_bundle(bundle) == plan.catalog_crc_new, "写回后的 CRC 与干跑不符"
    assert report.verify["F4 catalog"]["ok"] is True
