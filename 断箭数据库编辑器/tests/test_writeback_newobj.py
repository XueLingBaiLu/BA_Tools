"""W8-A 判据：往包里**新增对象**（新 pathID）—— 对象表插入 + 新 blob 追加 + Preload（J1/J3/J7）。

**判据一行**：pytest tests/test_writeback_newobj.py -q -> 通过数 **从 0 收集变 N passed**
（本文件新建前不存在）。

| 用例 | 判据（task-7） | 通过线 |
|---|---|---|
| test_j1_* | 新 pathID 在对象表里**唯一**、byteStart **8 字节对齐**、表**仍严格升序** | 全满足 |
| test_j1_insert_* | 插到**升序位置**（含表尾 / 表头 / 中间） | 下标对 |
| test_j3_* | 新对象进 AssetBundle（pathID=1）的 preload 表（走 growth 复用它原有字节） | 在表里 |
| test_j7_* | 除被改的表项 / 新增 blob / AB 对象外，其余解压流逐段逐字节未变 | 差 = 0 |
| 负控 ①~④ | 已有 pid / 错升序位 / 漏补 data_offset / 追加不 8 对齐 | 四条都能红 |

## 为什么在**内存里的两节点合成件**上跑（照 tests/test_writeback_delta.py 扩）

判据全落在写回器自己的不变量上（对象表升序 / byteStart 对齐 / metadata_size 与 data_offset 的
Δmeta 恒等式 / 块划分 / 老对象逐字节读回），与容器多大无关；真件既做不了秒级回归，
也不能在用例里反复写（⛔ 禁区：不许写游戏目录）。真件干跑走 _work/w8_probe_added.py

⚠ 合成件的 SerializedFile **刻意长得像真件**：对象表后面**有**元数据尾巴
（script types / externals / ref types / userInformation / 一段「类型树池」池子），
且 **data_offset == align16(48 + metadata_size)** —— _work/w8_probe_layout.py 实测真件
75/75 个节点都满足这两条。少了它们，用例证明不了真件那条链。
"""

from __future__ import annotations

import dataclasses
import inspect
import shutil
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

#: 造容器用的块大小（与产品一致：128 KiB）。
CHUNK = ufs.RECHUNK_SIZE
NODE0 = "CAB-0123456789abcdef0123456789abcdef"
NODE1 = NODE0 + ".resS"

#: AssetBundle 对象（真件 units 里就是 pathID = 1）。
AB_PID = 1
DUMMY_A, DUMMY_B, DUMMY_C = 1001, 1003, 1005
AB_NAME = "b54a3ecae2424f30d00f4dbb79449716.bundle"


def _align16(value: int) -> int:
    return value + (-value) % 16


def _blob(tag: bytes, n: int) -> bytes:
    """确定性、可压缩的一段字节（LZ4 才吃得到）。"""
    return (tag * (n // len(tag) + 1))[:n]


# --------------------------------------------------------------------------- #
# 合成 AssetBundle 对象：m_PreloadTable 的最小形状
# --------------------------------------------------------------------------- #
def _pack_assetbundle(preload: list[int], *, name: str = AB_NAME) -> bytes:
    """[i32 版本][i32 preload 条数][i64 pid x n][i32 名字长][名字]。

    真件里那段 2,503,916 B 的 AssetBundle 对象装的就是 m_PreloadTable（197,538 条）；
    这里只留「条数 + pid 列表」两件事 —— 新增对象要进的就是这张表，且改它必须走 growth
    （对象变长 ⇒ 搬尾部），⛔ 不许整对象重新序列化到别处。
    """
    raw = name.encode()
    out = struct.pack("<ii", 1, len(preload))
    out += b"".join(struct.pack("<q", pid) for pid in preload)
    out += struct.pack("<i", len(raw)) + raw
    return out


def _unpack_preload(data: bytes) -> list[int]:
    version, count = struct.unpack_from("<ii", data, 0)
    assert version == 1
    return [struct.unpack_from("<q", data, 8 + 8 * index)[0] for index in range(count)]


# --------------------------------------------------------------------------- #
# 合成件：最小 UnityFS 容器（两个节点）+ **像真件**的 SerializedFile
# --------------------------------------------------------------------------- #
def _build_sf(
    objs: list[tuple[int, bytes]],
    *,
    pool: bytes = b"",
    externals: tuple[bytes, ...] = (b"Library/unity default resources",),
    type_id: int = 1,
) -> bytes:
    """最小 SerializedFile v22（type_count = 0，但没有类型树 ⇒ 只测字节层）。

    ⚠ 与 W5/W7 那个构造器**唯一**的差别（也是为了像真件）：对象表后面**有元数据尾巴**，
    且 data_offset = align16(48 + metadata_size)。新增对象的插入点在表内 ⇒ 尾巴会被平移，
    正是真件里那 24,504 B「类型树池」要过的关。
    """
    body = bytearray()
    body += b"2022.3.62f3" + bytes(1)
    body += struct.pack("<i", 19)
    body += bytes(1)  # enable_type_tree = 0
    body += struct.pack("<i", 0)  # type_count
    body += struct.pack("<i", len(objs))

    cursor = 0
    table: list[tuple[int, int, int]] = []
    for path_id, payload in objs:
        table.append((path_id, cursor, len(payload)))
        cursor += len(payload)
    for path_id, stored, size in table:
        body += bytes((-len(body)) % 4)
        body += struct.pack("<qqii", path_id, stored, size, type_id)

    # 表后的元数据尾巴（真件里恒有：script types / externals / ref types / userInformation / 池子）
    body += struct.pack("<i", 0)
    body += struct.pack("<i", len(externals))
    for ext in externals:
        body += ext + bytes(1) + bytes(range(16)) + struct.pack("<i", 0) + bytes(1)
    body += struct.pack("<i", 0)
    body += bytes(1)
    body += bytes(pool)

    metadata_size = len(body)
    data_offset = _align16(48 + metadata_size)
    data = b"".join(payload for _path_id, payload in objs)
    head = bytearray(48)
    struct.pack_into(">I", head, 8, 22)
    struct.pack_into(">I", head, 20, metadata_size)
    struct.pack_into(">q", head, 24, data_offset + len(data))
    struct.pack_into(">q", head, 32, data_offset)
    return bytes(head) + bytes(body) + bytes(data_offset - 48 - metadata_size) + data


def _build_container(sf_blob: bytes, ress_blob: bytes) -> bytes:
    """两节点最小 UnityFS 容器（node[0] = CAB、node[1] = .resS）。"""
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


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """两节点合成件：node[0] 里 4 个对象 + 表后尾巴 + 池子；node[1] 让流跨两块。"""
    objs = [
        (AB_PID, _pack_assetbundle([DUMMY_A, DUMMY_B])),
        (DUMMY_A, _blob(b"A", 400)),
        (DUMMY_B, _blob(b"B", 401)),
        (DUMMY_C, _blob(b"C", 301)),  # Σdata 特意做到 != 0 (mod 8)，负控 ④ 才有现场
    ]
    sf = _build_sf(objs, pool=_blob(b"P", 5_000))
    path = tmp_path / "synth_newobj.bundle"
    path.write_bytes(_build_container(sf, _blob(b"R", 200_007)))
    return path


def _objects(path: Path) -> dict[int, bytes]:
    """读回全部对象（走产品自己的读侧）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.node(NODE0)
        info = ufs.read_serialized_file(reader, node)
        out: dict[int, bytes] = {}
        for _index, path_id, stored, size, _type_id in info.objects:
            out[path_id] = bytes(reader.read_range(node.offset + info.data_offset + stored, size))
        return out


def _header(path: Path) -> dict:
    """SerializedFile 头里那几个数 + 对象表（全部走产品自己的读侧）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.node(NODE0)
        raw = bytes(reader.read_range(node.offset, 48))
        info = ufs.read_serialized_file(reader, node)
        return {
            "node_offset": node.offset,
            "node_size": node.size,
            "metadata_size": struct.unpack_from(">I", raw, 20)[0],
            "file_size": struct.unpack_from(">q", raw, 24)[0],
            "data_offset": struct.unpack_from(">q", raw, 32)[0],
            "object_table_offset": info.object_table_offset,
            "object_count": info.object_count,
            "objects": list(info.objects),
            "path_ids": [row[1] for row in info.objects],
        }


def _write(path: Path, change: writer.DeltaChange, tmp_path: Path, **kwargs: object):
    return writer.write_delta(
        path,
        change,
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
        **kwargs,
    )


def _new_object(
    path_id: int,
    *,
    payload: bytes | None = None,
    type_id: int = 7,
    name: str = "SYNTH_NEW",
    type_name: str = "GameObject",
) -> writer.NewObject:
    return writer.NewObject(
        path_id=path_id,
        type_id=type_id,
        payload=_blob(b"N", 512) if payload is None else payload,
        name=name,
        type_name=type_name,
    )


# --------------------------------------------------------------------------- #
# J1：新 pid 唯一 / byteStart 8 字节对齐 / 表仍严格升序
# --------------------------------------------------------------------------- #
def test_j1_new_path_id_is_unique_sorted_and_aligned(bundle: Path, tmp_path: Path) -> None:
    """J1 + 不变量：新 pathID 唯一、byteStart 8 字节对齐、表仍严格升序；
    metadata_size / data_offset 同步 +Δmeta，且 data_offset == align16(48+metadata_size)。"""
    before = _header(bundle)
    payload = _blob(b"N", 512)
    change = writer.DeltaChange(added=(_new_object(1004, payload=payload),))
    plan = writer.plan_delta(bundle, change)
    # 已有 pid = [1, 1001, 1003, 1005] ⇒ 1004 该插到第 3 条（0 基）
    assert plan.added_objects == ((1004, 3, plan.added_objects[0][2], len(payload), 7),)
    # 口径（Lead 2026-10-03 定的 B）：Δmeta = align16(24N) = 32；byteStart **一个都不改**。
    # ⚠ 2026-10-09 复推：取整到 16 是**布局成立的条件**（物理插入量必须 == Δdata_offset）。
    #   真正要守的是「那 8 个补零必须落在解析器会消费的地方」—— 现在填在 userInformation 里
    #   （见 writer._user_information_span）；填在元数据体末尾会让 Unity 拒收整个包。
    assert plan.meta_growth == 32
    assert plan.byte_start_shift == 0
    assert plan.old_metadata_size == before["metadata_size"]
    assert plan.new_metadata_size == before["metadata_size"] + 32
    assert plan.old_data_offset == before["data_offset"]
    assert plan.new_data_offset == before["data_offset"] + 32

    report = _write(bundle, change, tmp_path)
    assert report.verify["J1/J7 新增对象（表 / 对齐 / 读回）"]["ok"] is True, report.verify

    after = _header(bundle)
    ids = after["path_ids"]
    assert ids == sorted(set(ids)), "对象表不再严格升序（J1）"
    assert ids.count(1004) == 1
    assert ids.index(1004) == 3, "插到了错的格子"
    row = next(item for item in after["objects"] if item[1] == 1004)
    stored, size, type_id = row[2], row[3], row[4]
    assert stored % 8 == 0, f"新对象的 byteStart {stored} 不是 8 字节对齐（J1）"
    assert size == len(payload)
    assert type_id == 7, "typeID 必须是申报的类型表下标"
    assert _objects(bundle)[1004] == payload, "新对象的字节读回来 != payload"
    # 不变量（真件 75/75）
    assert after["data_offset"] == _align16(48 + after["metadata_size"])
    assert after["data_offset"] % 16 == 0
    assert after["metadata_size"] - before["metadata_size"] == 32
    assert after["data_offset"] - before["data_offset"] == 32
    assert after["file_size"] == after["node_size"]


def test_j1_insert_position_is_the_sorted_slot(bundle: Path) -> None:
    """插入位置是**升序格子**，不是无脑追加到表尾：表头 / 中间 / 表尾三档都量。"""
    for path_id, want in ((2, 1), (1002, 2), (1004, 3), (9999, 4)):
        plan = writer.plan_delta(
            bundle, writer.DeltaChange(added=(_new_object(path_id, payload=_blob(b"N", 64)),))
        )
        assert plan.added_objects[0][1] == want, (path_id, plan.added_objects)


# --------------------------------------------------------------------------- #
# J3：新对象进 AssetBundle 的 preload 表（走 growth 复用它原有字节）
# --------------------------------------------------------------------------- #
def test_j3_added_object_enters_the_assetbundle_preload_table(
    bundle: Path, tmp_path: Path
) -> None:
    """J3：新对象进 AssetBundle 对象（pathID=1）的 preload 表 —— 表里 197,538 → 197,539 的
    同款动作：**只把那一行加进它原有的字节里**（走 ObjectGrowth，⛔ 不整对象重排到别处）。"""
    before = _objects(bundle)
    grown = _pack_assetbundle([DUMMY_A, DUMMY_B, 1004])
    assert len(grown) > len(before[AB_PID]), "fixture 必须让 AssetBundle 对象真的变长"
    change = writer.DeltaChange(
        growths=(
            writer.ObjectGrowth(
                path_id=AB_PID, payload=grown, name="SYNTH_AB", type_name="AssetBundle"
            ),
        ),
        added=(_new_object(1004),),
    )
    plan = writer.plan_delta(bundle, change)
    assert len(plan.rows) == 2, plan.rows  # growth 一行 + 新增一行
    assert plan.added_objects[0][0] == 1004

    report = _write(bundle, change, tmp_path)
    assert report.verify["J1/J7 新增对象（表 / 对齐 / 读回）"]["ok"] is True, report.verify
    after = _objects(bundle)
    assert after[AB_PID] == grown
    assert _unpack_preload(after[AB_PID]) == [DUMMY_A, DUMMY_B, 1004], "新对象没进 preload 表"
    assert after[1004] == _blob(b"N", 512)


# --------------------------------------------------------------------------- #
# J7：其余逐段逐字节未变 + 老 byteSize 一个不变
# --------------------------------------------------------------------------- #
def test_j7_other_bytes_unchanged_and_old_byte_sizes_untouched(
    bundle: Path, tmp_path: Path
) -> None:
    """J7：老对象按**新坐标**逐字节读回相同、byteSize 一个不变、整个数据区平移后逐字节相同、
    表后的元数据尾巴原样平移 Δmeta。"""
    before = _header(bundle)
    before_objs = _objects(bundle)
    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    payload = _blob(b"N", 512)
    change = writer.DeltaChange(added=(_new_object(1004, payload=payload),))
    report = _write(bundle, change, tmp_path)
    assert report.verify["J1/J7 新增对象（表 / 对齐 / 读回）"]["ok"] is True, report.verify

    after = _header(bundle)
    delta = after["data_offset"] - before["data_offset"]
    assert delta == 32
    after_objs = _objects(bundle)
    for _index, path_id, _stored, size, _type_id in before["objects"]:
        row = next(item for item in after["objects"] if item[1] == path_id)
        assert row[3] == size, f"老 pathID={path_id} 的 byteSize 被动了"
        assert after_objs[path_id] == before_objs[path_id], f"老 pathID={path_id} 的字节变了"

    with ufs.UfsReader(original) as old_reader, ufs.UfsReader(bundle) as new_reader:
        old_node, new_node = old_reader.nodes[0], new_reader.nodes[0]
        old_sf = ufs.read_serialized_file(old_reader, old_node)
        new_sf = ufs.read_serialized_file(new_reader, new_node)
        data_size = old_node.size - old_sf.data_offset
        old_data = bytes(old_reader.read_range(old_node.offset + old_sf.data_offset, data_size))
        new_data = bytes(new_reader.read_range(new_node.offset + new_sf.data_offset, data_size))
        assert new_data == old_data, "数据区在平移后坐标下不是逐字节相同（J7）"
        old_tail_lo = (
            old_node.offset
            + old_sf.object_table_offset
            + old_sf.object_count * old_sf.object_entry_stride
        )
        old_tail_hi = old_node.offset + 48 + old_sf.metadata_size
        tail = bytes(old_reader.read_range(old_tail_lo, old_tail_hi - old_tail_lo))
        table_shift = 24 * (after["object_count"] - before["object_count"])
        # ⚠ 窗口要**多 8 字节**：补零段插在尾巴内部（userInformation 里），
        #   插点之后的字节在文件里还要再往后挪 pad_extra。
        pad_extra_probe = (after["metadata_size"] - before["metadata_size"]) - table_shift
        new_tail = bytes(
            new_reader.read_range(
                old_tail_lo + table_shift, old_tail_hi - old_tail_lo + pad_extra_probe
            )
        )
        # ⚠ 2026-10-09：那 pad_extra 个字节**不再插在尾巴之后**，而是插进尾巴里的
        #   userInformation（零结尾字符串）终结符之前 —— 插在元数据体末端会让
        #   metadata_size 比解析终点多 pad_extra，Unity 直接拒收整个包
        #   （改9/改10 两次「GUI 消失」的真因）。
        #   ⇒ 尾巴**除这一段外**仍原样平移 24N。
        pad_extra = (after["metadata_size"] - before["metadata_size"]) - table_shift
        assert pad_extra == 8, f"夹具下 Δmeta − 24N 应为 8，实为 {pad_extra}"
        pos = new_tail.find(b"#" * pad_extra)
        assert pos != -1, f"尾巴里找不到插进 userInformation 的补零段（{pad_extra} B）"
        # 判据：**去掉补零段之后，尾巴与旧的一模一样**（= 原样平移 24N ＋ 插了 pad_extra）
        assert new_tail[:pos] + new_tail[pos + pad_extra :] == tail, (
            "表后元数据尾巴在去掉补零段后不是逐字节相同的"
        )
        assert len(tail) > 16, "fixture 必须让表后真的有尾巴可量"


# --------------------------------------------------------------------------- #
# 负控：四条都必须能红
# --------------------------------------------------------------------------- #
def test_negative_control_1_existing_path_id_is_rejected(bundle: Path) -> None:
    """负控 ①：故意造一个**已存在**的 pathID ⇒ 必须抛（包内查重）。"""
    for path_id in (AB_PID, DUMMY_A, DUMMY_C):
        with pytest.raises(AssetError, match="撞了"):
            writer.plan_delta(bundle, writer.DeltaChange(added=(_new_object(path_id),)))
    with pytest.raises(AssetError, match="撞了"):
        writer.plan_delta(
            bundle,
            writer.DeltaChange(added=(_new_object(2000), _new_object(2000))),
        )


def test_negative_control_2_wrong_sorted_slot_goes_red(
    monkeypatch: pytest.MonkeyPatch, bundle: Path
) -> None:
    """负控 ②：故意把新条目插在**错的升序位置** ⇒ 升序尺子必须红。

    先给尺子做**正控**（喂一个已知不升序的表，必须抛），再把插入下标强行改成 0。
    """
    with pytest.raises(AssetError, match="严格升序"):
        writer._assert_path_ids_ascending([1, 1001, 1003, 1002], "合成表")
    monkeypatch.setattr(writer, "_sorted_insert_index", lambda _path_ids, _path_id: 0)
    with pytest.raises(AssetError, match="严格升序"):
        writer.plan_delta(bundle, writer.DeltaChange(added=(_new_object(9999),)))


def _broken_data_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 data_offset 故意写少 8 B（= 漏补/补错那条），其余口径照旧。"""
    real = writer._added_meta

    def fake(entries_bytes: int, *, metadata_size_old: int, data_offset_old: int) -> object:
        got = real(
            entries_bytes, metadata_size_old=metadata_size_old, data_offset_old=data_offset_old
        )
        return dataclasses.replace(got, data_offset_new=got.data_offset_new - 8)

    monkeypatch.setattr(writer, "_added_meta", fake)


def test_negative_control_3_missing_data_offset_patch_goes_red(
    monkeypatch: pytest.MonkeyPatch, bundle: Path, tmp_path: Path
) -> None:
    """负控 ③：故意**不补 / 补错** data_offset（或漏平移 byteStart）⇒
    「老对象按新坐标逐字节读回」那条断言必须红。"""
    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    change = writer.DeltaChange(added=(_new_object(1004),))
    _broken_data_offset(monkeypatch)
    with pytest.raises(AssetError, match="写后复核没过"):
        _write(bundle, change, tmp_path)
    plan = writer.plan_delta(original, change)
    with ufs.UfsReader(original) as old_reader, ufs.UfsReader(bundle) as new_reader:
        verdict = writer._verify_added(old_reader, new_reader, plan)
    assert verdict["ok"] is False, "data_offset 补错了，尺子却是绿的"
    assert "不一致" in verdict["detail"], verdict["detail"]


def test_negative_control_4_unaligned_append_goes_red(
    monkeypatch: pytest.MonkeyPatch, bundle: Path, tmp_path: Path
) -> None:
    """负控 ④：追加起点不 8 字节对齐 ⇒ 必须红（J1 的硬约束）。"""
    with ufs.UfsReader(bundle) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
    assert (node.size - info.data_offset) % 8 != 0, "fixture 要让「不填充就不对齐」真的出现"
    original = tmp_path / "original.bundle"
    shutil.copy2(bundle, original)
    monkeypatch.setattr(writer, "ADDED_ALIGN", 1)
    change = writer.DeltaChange(added=(_new_object(1004),))
    with pytest.raises(AssetError, match="写后复核没过"):
        _write(bundle, change, tmp_path)
    plan = writer.plan_delta(original, change)
    assert plan.added_objects[0][2] % 8 != 0, "负控的前提：计划里那个 byteStart 真的没对齐"
    with ufs.UfsReader(original) as old_reader, ufs.UfsReader(bundle) as new_reader:
        verdict = writer._verify_added(old_reader, new_reader, plan)
    assert verdict["ok"] is False, "追加起点没对齐，尺子却是绿的"
    assert "字节对齐" in verdict["detail"], verdict["detail"]


# --------------------------------------------------------------------------- #
# 冻结接口 / 口径 / 干跑 / next_path_id
# --------------------------------------------------------------------------- #
def test_frozen_signatures_and_newobj_fields() -> None:
    """冻结接口逐条落地：名字、参数、字段名与顺序（B 组要照着它写克隆图）。"""
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
    assert list(inspect.signature(writer.next_path_id).parameters) == ["bundle_path", "hint"]
    assert [field.name for field in dataclasses.fields(writer.NewObject)] == [
        "path_id",
        "type_id",
        "payload",
        "name",
        "type_name",
    ]
    assert [field.name for field in dataclasses.fields(writer.DeltaChange)] == [
        "inplace",
        "growths",
        "appends",
        "added",
    ]
    for name in ("NewObject", "next_path_id", "plan_delta", "write_delta", "DeltaChange"):
        assert name in writer.__all__, f"__all__ 里缺 {name}"
    assert writer.ADDED_ALIGN == 8
    assert writer.META_ALIGN == 16


def test_meta_shift_is_round_up_to_16() -> None:
    """Δmeta 的口径：align16(24N)，且 >= 24N。"""
    assert writer._meta_shift(24) == 32
    assert writer._meta_shift(48) == 48
    assert writer._meta_shift(72) == 80
    for entries in (24, 48, 72, 240):
        assert writer._meta_shift(entries) % 16 == 0
        assert writer._meta_shift(entries) >= entries


def test_added_rejects_bad_values(bundle: Path) -> None:
    """负控的补充：pid 非正 / type_id 负数 / payload 空 ⇒ 当场抛。"""
    with pytest.raises(AssetError, match="不是正数"):
        writer.plan_delta(bundle, writer.DeltaChange(added=(_new_object(0),)))
    with pytest.raises(AssetError, match="超出 i64 上界"):
        writer.plan_delta(
            bundle,
            writer.DeltaChange(added=(_new_object(1 << 63, payload=_blob(b"N", 64)),)),
        )
    with pytest.raises(AssetError, match="类型表下标"):
        writer.plan_delta(bundle, writer.DeltaChange(added=(_new_object(2000, type_id=-1),)))
    with pytest.raises(AssetError, match="payload 是空的"):
        writer.plan_delta(
            bundle,
            writer.DeltaChange(
                added=(writer.NewObject(path_id=2000, type_id=1, payload=b""),)
            ),
        )


def test_two_added_objects_stay_ascending_and_share_one_transaction(
    bundle: Path, tmp_path: Path
) -> None:
    """N=2：Δmeta = 48（无补零）、两条都进表、仍严格升序；传入顺序乱也不影响结果。"""
    change = writer.DeltaChange(
        added=(
            _new_object(1006, payload=_blob(b"N1", 64)),
            _new_object(1002, payload=_blob(b"N2", 65)),
        )
    )
    plan = writer.plan_delta(bundle, change)
    assert plan.meta_growth == 48
    assert plan.byte_start_shift == 0
    assert [item[0] for item in plan.added_objects] == [1002, 1006]
    report = _write(bundle, change, tmp_path)
    assert report.verify["J1/J7 新增对象（表 / 对齐 / 读回）"]["ok"] is True, report.verify
    after = _header(bundle)
    assert after["path_ids"] == sorted(after["path_ids"])
    got = _objects(bundle)
    assert got[1002] == _blob(b"N2", 65)
    assert got[1006] == _blob(b"N1", 64)


def test_dry_run_writes_nothing_with_added(bundle: Path, tmp_path: Path) -> None:
    """默认 dry_run：算得出计划、F1 行里看得见新增对象，但包一个字节都没动。"""
    before = bundle.read_bytes()
    report = writer.write_delta(
        bundle,
        writer.DeltaChange(added=(_new_object(1004),)),
        dry_run=True,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    assert report.dry_run is True
    assert bundle.read_bytes() == before
    assert not (tmp_path / "backup").exists()
    text = "\n".join(report.report_lines())
    assert "Δ 新增对象" in text, text
    assert "Δmeta" in text
    assert "干跑" in text


def test_next_path_id_is_unique_and_hint_is_a_floor(bundle: Path) -> None:
    """next_path_id：正的、递增、不与包内已有撞；十进制 hint 是**下限**不是覆盖。"""
    ids = _header(bundle)["path_ids"]
    top = max(ids)
    assert writer.next_path_id(bundle) == top + 1
    assert writer.next_path_id(bundle) not in ids
    assert writer.next_path_id(bundle, hint="9999") == 9999
    assert writer.next_path_id(bundle, hint="3") == top + 1
    assert writer.next_path_id(bundle, hint="不是数字") == top + 1


def test_next_path_id_result_can_be_written(bundle: Path, tmp_path: Path) -> None:
    """拿 next_path_id 造出来的 pid 直接写进去、读得回来（B 组的用法）。"""
    path_id = writer.next_path_id(bundle)
    report = _write(bundle, writer.DeltaChange(added=(_new_object(path_id),)), tmp_path)
    assert path_id in _objects(bundle)
    assert report.verify["J1/J7 新增对象（表 / 对齐 / 读回）"]["ok"] is True, report.verify
