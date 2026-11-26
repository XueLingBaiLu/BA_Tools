"""W5-B1 判据：L1 等长写回（F1~F7）。

**判据一行**：pytest tests/test_writeback_l1.py -q -> 通过数 **从 0 收集变 N passed**
（本文件新建前不存在）。

| 用例 | 判据（工单 §3） | 通过线 |
|---|---|---|
| test_f1_* | 执行前打印的判据一行 | 格式完整，级别 = **L1** |
| test_f2_* | UfsReader 能解析；目标对象读回 == 新值 | 相等 |
| test_f3_* | 除目标对象外，其它对象逐字节未变 | 差 = 0 |
| test_f4_* | 磁盘 crc32(逐块解压拼接) == catalog 的 m_Crc；m_BundleSize == 文件大小 | 双向一致 |
| test_f5_* | 写前备份存在，sha256 == 改前的 sha256 | 相等 |
| test_f6_* | 故意**不更新** catalog 的 m_Crc ⇒ 独立复核必须报错 | 能红 |
| test_f7_* | 改一个 float ⇒ delta = 0 | delta == 0 |

## 为什么在**内存里造的小容器 + 合成 catalog** 上跑

判据全部落在写回器自己的不变量上（对象表 byte_size、节点表 size、块表压缩字节、
catalog 的 extraDataOffset 偏移、CRC 口径）—— 这些不变量与容器多大无关，小容器与
3.38 GB 的真件**同构**；而真件既做不了秒级回归，也不能在用例里反复写
（⛔ 禁区：不许写游戏目录）。真件的判据走 _work/w5_rehearsal.py（副本彩排）与
_work/w5_verify_writeback.py（独立复核），不进 pytest。

⚠️ 合成件的 SerializedFile 只有 w1 那套最小构造器（type_count = 0），**没有类型树**：
所以本文件只测**字节层**（容器 / 对象表 / catalog / CRC），不测 UnityPy 的 typetree
往返 —— 那一条在真件彩排里量（_work/w5_rehearsal_check.py：写回后 _Metallic 0.0 -> 1.0）。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: 造容器用的块大小（与产品一致：128 KiB）。
CHUNK = ufs.RECHUNK_SIZE
#: bundle 里装 SerializedFile 的节点名（writer._serialized_node 认 node[0]）。
NODE = "CAB-0123456789abcdef0123456789abcdef"
TARGET_PID = 1002
_ASM = b"Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
_CLS = b"UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"


# --------------------------------------------------------------------------- #
# 合成件：最小 UnityFS 容器（node[0] = CAB-<hash>）
# --------------------------------------------------------------------------- #
def _blob(tag: bytes, n: int) -> bytes:
    """造一段**可压缩**的确定性字节（重复 pattern，LZ4 才吃得到）。"""
    return (tag * (n // len(tag) + 1))[:n]


def _build_sf(objs: list[tuple[int, bytes]]) -> bytes:
    """最小 SerializedFile v22 节点（照 tests/test_ufs_writeback.py 的构造器，字段逐条对齐）。"""
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


def _build_container(sf_blob: bytes) -> bytes:
    """把 SerializedFile 包成最小 UnityFS 容器（LZ4HC 分块 + 一个 CAB 节点）。"""
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
    header = ufs.serialize_header(6, "5.x.x", "2022.3.62f3", 0, len(comp_info), len(raw_info), 0x43)
    total = len(header) + len(comp_info) + len(payload)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", total, len(comp_info), len(raw_info), 0x43
    )
    return header + comp_info + bytes(payload)


def _objects(path: Path) -> dict[int, tuple[int, bytes]]:
    """读回全部对象：{path_id: (解压流绝对起点, 字节)} —— **走产品自己的读侧**。"""
    with ufs.UfsReader(path) as reader:
        node = reader.node(NODE)
        info = ufs.read_serialized_file(reader, node)
        out: dict[int, tuple[int, bytes]] = {}
        for _i, pid, stored, size, _tid in info.objects:
            start = node.offset + info.data_offset + stored
            out[pid] = (start, reader.read_range(start, size))
        return out


def _equal_change(path: Path, pid: int = TARGET_PID, *, grow: int = 0) -> writer.ObjectChange:
    """把一个对象换成同长度（或 +grow）的新字节。"""
    start, old = _objects(path)[pid]
    new = bytes([old[0] ^ 0xFF]) + old[1:] + (b" " * grow)
    return writer.ObjectChange(path_id=pid, payload=new, name="SYNTH", type_name="Material")


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """一份小容器：3 个对象（130 KB / 200 KB / 200 KB），跨若干块。"""
    objs = [
        (1001, _blob(b"A", 130_000)),
        (1002, _blob(b"B", 200_000)),
        (1003, _blob(b"C", 200_000)),
    ]
    path = tmp_path / "synth.bundle"
    path.write_bytes(_build_container(_build_sf(objs)))
    return path


# --------------------------------------------------------------------------- #
# 合成 catalog
# --------------------------------------------------------------------------- #
def _options(hash_hex: str, crc: int, size: int) -> str:
    return (
        '{"m_Hash":"' + hash_hex + '","m_Crc":' + str(crc) + ',"m_Timeout":0,'
        '"m_ChunkedTransfer":false,"m_RedirectLimit":-1,"m_RetryCount":0,'
        '"m_BundleName":"b54a3ecae2424f30d00f4dbb79449716","m_AssetLoadMode":0,'
        '"m_BundleSize":' + str(size) + ',"m_UseCrcForCachedBundles":true,'
        '"m_UseUWRForLocalBundles":false,"m_ClearOtherCachedVersionsWhenLoaded":false}'
    )


def _extra_entry(hash_hex: str, crc: int, size: int) -> bytes:
    js = _options(hash_hex, crc, size).encode("utf-16-le")
    return (
        bytes([7, len(_ASM)])
        + _ASM
        + bytes([len(_CLS)])
        + _CLS
        + struct.pack("<i", len(js))
        + js
    )


def _bundle_name(index: int) -> str:
    return f"synth{index}_assets_all_{index:032x}.bundle"


def _hash_of(index: int) -> str:
    return f"{index:032x}"


def write_catalog_sized(path: Path, rows: list[tuple[int, int, int]]) -> Path:
    """rows = [(bundle 下标, m_Crc, m_BundleSize)]。序列化口径必须与 writer 一致（逐字节正控）。"""
    blob = bytearray()
    offsets: list[int] = []
    for row_index, crc, size in rows:
        offsets.append(len(blob))
        blob += _extra_entry(_hash_of(row_index), crc, size)
    entries = struct.pack("<i", len(rows))
    for row_index, offset in zip((row[0] for row in rows), offsets, strict=True):
        entries += struct.pack("<7i", row_index, 0, -1, 17, offset, row_index, 0)
    raw = {
        "m_LocatorId": "AddressablesMainContentCatalog",
        "m_BuildResultHash": "0" * 32,
        "m_InternalIds": [
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\PC\\"
            + _bundle_name(row_index)
            for row_index, _crc, _size in rows
        ],
        "m_KeyDataString": "",
        "m_BucketDataString": "",
        "m_EntryDataString": base64.b64encode(entries).decode("ascii"),
        "m_ExtraDataString": base64.b64encode(bytes(blob)).decode("ascii"),
        "m_resourceTypes": [],
        "m_InternalIdPrefixes": [],
    }
    path.write_bytes(
        json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    return path


# --------------------------------------------------------------------------- #
# 独立复核（**不走** writer 的那条路）
# --------------------------------------------------------------------------- #
def _crc_of_bundle(path: Path) -> int:
    """独立复算：只借 UfsReader 的块表，自己逐块解压拼 crc32。"""
    crc = 0
    with ufs.UfsReader(path) as reader:
        for index, blk in enumerate(reader.blocks):
            raw = reader.read_range(reader.block_dstart[index], blk.uncompressed)
            crc = zlib.crc32(raw, crc)
    return crc & 0xFFFFFFFF


def _catalog_options(catalog_path: Path, hash_hex: str) -> dict | None:
    """独立从 catalog 明文里抠出某条 AssetBundleRequestOptions。

    与 writer.catalog_entry 走**不同**的路线：不解 m_EntryDataString，
    而是把 'm_Hash' 编成 UTF-16LE 直接在 m_ExtraDataString 的字节流里搜（kb/03 §4.2 的原话）。
    """
    raw = json.loads(catalog_path.read_bytes().decode("utf-8"))
    blob = base64.b64decode(raw["m_ExtraDataString"])
    needle = ("\"m_Hash\":\"" + hash_hex + "\"").encode("utf-16-le")
    pos = blob.find(needle)
    if pos < 0:
        return None
    start = pos - 2
    end = start
    while end + 1 < len(blob):
        if blob[end : end + 2].decode("utf-16-le", "replace") == "}":
            end += 2
            break
        end += 2
    return json.loads(blob[start:end].decode("utf-16-le"))


# --------------------------------------------------------------------------- #
# F1 判据一行
# --------------------------------------------------------------------------- #
def test_f1_criterion_line_has_every_required_field(bundle: Path, tmp_path: Path) -> None:
    """F1 正控：干跑必须打印「对象 / 原字节数 / 新字节数 / 级别 L1 / 命中块数 / 预计耗时」。"""
    plan = writer.plan_writeback(bundle, [_equal_change(bundle)])
    text = "\n".join(plan.criterion_lines())
    for token in ("对象", "原 ", "新 ", "L1", "命中块", "预计耗时"):
        assert token in text, f"判据一行缺 {token!r}：\n{text}"
    assert plan.rows[0].level == writer.LEVEL_EQUAL
    assert plan.rows[0].old_bytes == plan.rows[0].new_bytes
    assert plan.hit_blocks >= 1
    assert plan.eta_seconds > 0
    assert plan.blocked_reason == ""


def test_f1_negative_control_growing_change_is_not_l1(bundle: Path, tmp_path: Path) -> None:
    """F1 负控（**尺子必须能红**）：长度变了 ⇒ 级别不是 L1，且判据一行写着「禁止写回」。"""
    plan = writer.plan_writeback(bundle, [_equal_change(bundle, grow=32)])
    text = "\n".join(plan.criterion_lines())
    assert plan.rows[0].level == writer.LEVEL_GROW
    assert plan.rows[0].new_bytes - plan.rows[0].old_bytes == 32
    assert "禁止写回" in text
    assert plan.blocked_reason, "长度变了必须把 blocked_reason 写上（不许硬写）"
    with pytest.raises(AssetError):
        writer.write_objects(
            bundle,
            [_equal_change(bundle, grow=32)],
            dry_run=False,
            backup_dir=tmp_path / "backup",
            game_root=tmp_path,
        )


# --------------------------------------------------------------------------- #
# F2 / F7
# --------------------------------------------------------------------------- #
def test_f2_target_object_reads_back_as_new_value(bundle: Path, tmp_path: Path) -> None:
    """F2：UfsReader 能解析；目标对象读回 == 新值。"""
    change = _equal_change(bundle)
    report = writer.write_objects(
        bundle,
        [change],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    after = _objects(bundle)
    assert after[TARGET_PID][1] == change.payload, "目标对象读回来不是新值"
    assert report.verify["F2 读回"]["ok"] is True


def test_f7_equal_length_change_has_zero_delta(bundle: Path, tmp_path: Path) -> None:
    """F7：改一个 float ⇒ 对象级 delta = 0（文件级长度由块重压决定，不是对象变长）。"""
    change = _equal_change(bundle)
    before = bundle.stat().st_size
    report = writer.write_objects(
        bundle,
        [change],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    assert report.verify["F7 等长"]["ok"] is True
    assert report.verify["F7 等长"]["delta"] == 0
    assert len(change.payload) == report.plan.rows[0].old_bytes
    # 文件级长度会随块重压抖动，但**头部声明的长度 == 磁盘长度**必须成立
    assert bundle.stat().st_size == report.plan.new_file_size
    # 文件级 delta 由块重压决定（可能 +/-几个字节），⛔ 不作判据 —— 判据只认对象级 delta
    assert report.plan.old_file_size == before


# --------------------------------------------------------------------------- #
# F3 其它未变（含尺子正控）
# --------------------------------------------------------------------------- #
def test_f3_all_other_objects_are_byte_identical(bundle: Path, tmp_path: Path) -> None:
    """F3：除目标对象外，其它对象逐字节未变（差 = 0）。"""
    before = _objects(bundle)
    change = _equal_change(bundle)
    writer.write_objects(
        bundle,
        [change],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    after = _objects(bundle)
    assert after[TARGET_PID][1] == change.payload
    for pid in (1001, 1003):
        assert after[pid][1] == before[pid][1], f"对象 {pid} 被动到了"
    assert len(after) == len(before)


def test_f3_ruler_can_go_red(bundle: Path, tmp_path: Path) -> None:
    """F3 的**尺子正控**：把一个干净块改坏 ⇒ 比对器必须报「区间之外的差异 > 0」。

    ⛔ 不能红就不算尺子（AGENTS §0.9 动作 2）。
    """
    change = _equal_change(bundle)
    writer.write_objects(
        bundle,
        [change],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    backup = next((tmp_path / "backup").glob("*.bak"))
    start, payload = _objects(bundle)[TARGET_PID]
    ranges = [(start, start + len(payload))]
    with ufs.UfsReader(backup) as old_reader, ufs.UfsReader(bundle) as new_reader:
        # 绿：把被替换区间**排除在差异之外** ⇒ 区间之外必须是 0
        _total, outside, _crc = writer._stream_compare(old_reader, new_reader, ranges)
        assert outside == 0, "正控基线就该是 0"
        # 红：同一条比对器，把 keep 传空 ⇒ 全部差异都算「区间之外」，必须 > 0
        total_all, outside_all, _crc2 = writer._stream_compare(old_reader, new_reader, [])
    assert total_all > 0, "写回明明改了字节，比对器却报 0 差异 —— 这把尺子红不了"
    assert outside_all == total_all


# --------------------------------------------------------------------------- #
# F5 备份（含两条红线负控）
# --------------------------------------------------------------------------- #
def test_f5_backup_exists_and_sha_matches_before(bundle: Path, tmp_path: Path) -> None:
    """F5：写前备份存在，sha256 == 改前的 sha256。"""
    sha_before = writer._sha256(bundle)
    report = writer.write_objects(
        bundle,
        [_equal_change(bundle)],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
    )
    assert report.backup is not None and report.backup.is_file()
    assert writer._sha256(report.backup) == sha_before
    assert report.backup_sha256 == sha_before
    assert report.verify["F5 备份"]["ok"] is True


def test_f5_without_a_usable_backup_nothing_is_written(bundle: Path, tmp_path: Path) -> None:
    """F5 负控：**没备份不许写** —— 备份落点建不出来时，包必须一个字节都没动。"""
    before_bytes = bundle.read_bytes()
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("x", encoding="utf-8")  # 是个文件，mkdir 会失败
    with pytest.raises((AssetError, OSError, FileNotFoundError)):
        writer.write_objects(
            bundle,
            [_equal_change(bundle)],
            dry_run=False,
            backup_dir=blocked / "backup",
            game_root=tmp_path,
        )
    assert bundle.read_bytes() == before_bytes, "没备份成功却动了包"


def test_f5_backup_on_another_drive_is_rejected(bundle: Path, tmp_path: Path) -> None:
    """F5 负控（红线）：备份/工程目录必须跟随游戏盘 —— 跨盘必须报错。"""
    drive = str(bundle.drive).upper()
    other = "D:\\bamod_should_never_be_created" if drive != "D:" else "C:\\bamod_never"
    before_bytes = bundle.read_bytes()
    with pytest.raises(AssetError, match="跟随游戏所在盘"):
        writer.write_objects(
            bundle,
            [_equal_change(bundle)],
            dry_run=False,
            backup_dir=Path(other) / "backup",
            game_root=tmp_path,
        )
    assert bundle.read_bytes() == before_bytes


# --------------------------------------------------------------------------- #
# F4 / F6 catalog
# --------------------------------------------------------------------------- #
def test_f4_and_f6_catalog_crc_round_trip_and_stale_crc_is_red(
    bundle: Path, tmp_path: Path
) -> None:
    """F4 + F6：写回后 catalog 的 m_Crc / m_BundleSize 与磁盘一致；换成陈旧值必须能红。"""
    plan0 = writer.plan_writeback(bundle, [_equal_change(bundle)])
    catalog = write_catalog_sized(
        tmp_path / "catalog.json", [(0, plan0.disk_crc, bundle.stat().st_size)]
    )
    # 文件名必须与 catalog 里登记的 internalId 一致（writer 用 bundle.name 去定位）
    real = tmp_path / _bundle_name(0)
    shutil.copy2(bundle, real)
    change = _equal_change(real, pid=TARGET_PID)
    report = writer.write_objects(
        real,
        [change],
        dry_run=False,
        backup_dir=tmp_path / "backup",
        game_root=tmp_path,
        catalog_path=catalog,
    )
    assert report.verify["F4 catalog"]["ok"] is True

    # F4 的独立复核（不走 writer）：磁盘复算 vs catalog 明文
    disk_crc = _crc_of_bundle(real)
    options = _catalog_options(catalog, _hash_of(0))
    assert options is not None
    assert options["m_Crc"] == disk_crc, "catalog 里的 m_Crc 与磁盘复算不一致"
    assert options["m_BundleSize"] == real.stat().st_size

    # F6 负控：故意把 catalog 改回**陈旧**的 CRC ⇒ 独立复核必须不一致（能红）
    stale = write_catalog_sized(
        tmp_path / "stale.json", [(0, plan0.disk_crc ^ 0x1, real.stat().st_size)]
    )
    stale_options = _catalog_options(stale, _hash_of(0))
    assert stale_options is not None
    assert stale_options["m_Crc"] != _crc_of_bundle(real), (
        "陈旧 CRC 竟然和磁盘一致 —— 这条负控红不了"
    )


def test_catalog_offset_recomputation_when_digit_count_changes(
    bundle: Path, tmp_path: Path
) -> None:
    """工单 §7 坑 1：改 m_Crc 的十进制位数会让后面所有 extraDataOffset 平移 ⇒ 全量重算。

    这里造 **2 条** catalog 记录，把第 1 条的 CRC 改短（少一位）⇒ 第 2 条的
    extraDataOffset 必须跟着平移，而且两条都还得解得开。
    """
    catalog = write_catalog_sized(
        tmp_path / "catalog.json",
        [(0, 3_000_000_000, 111), (1, 123_456_789, 222)],
    )
    entry_before = writer.catalog_entry(catalog, _bundle_name(1))
    # 3,000,000,000（10 位）-> 123,456,789（9 位）：少 1 位 = 少 2 个 UTF-16 字节
    patch = writer._build_catalog_patch(
        catalog, _bundle_name(0), crc=123_456_789, bundle_size=333
    )
    assert patch.offset_shift == -2, (
        f"CRC 位数变少，偏移必须向左平移 2 B，实测 {patch.offset_shift}"
    )
    catalog.write_bytes(patch.after)
    entry_after = writer.catalog_entry(catalog, _bundle_name(1))
    assert entry_after.extra_offset == entry_before.extra_offset + patch.offset_shift
    assert entry_after.crc == 123_456_789
    assert entry_after.bundle_size == 222
    first = writer.catalog_entry(catalog, _bundle_name(0))
    assert first.crc == 123_456_789
    assert first.bundle_size == 333


def test_catalog_patch_is_a_noop_when_values_are_unchanged(tmp_path: Path) -> None:
    """正控：值没变时补丁后的字节**逐字节等于原样**（证明没有偷偷重排 catalog）。"""
    catalog = write_catalog_sized(
        tmp_path / "catalog.json", [(0, 2_918_958_848, 3_381_248_409)]
    )
    before = catalog.read_bytes()
    patch = writer._build_catalog_patch(
        catalog, _bundle_name(0), crc=2_918_958_848, bundle_size=3_381_248_409
    )
    assert patch.offset_shift == 0
    assert patch.after == before

# --------------------------------------------------------------------------- #
# 贴图槽 / shader 引用（判决实验逼出来的能力：结构级改动，仍然等长）
# --------------------------------------------------------------------------- #
def _fake_material_tree() -> dict:
    return {
        "m_Name": "SYNTH",
        "m_Shader": {"m_FileID": 0, "m_PathID": 7},
        "m_SavedProperties": {
            "m_Floats": [["_Metallic", 0.0]],
            "m_Colors": [["_BaseColor", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}]],
            "m_TexEnvs": [
                ["Layer_ABC", {"m_Texture": {"m_FileID": 0, "m_PathID": 111}}],
                ["_Emission", {"m_Texture": {"m_FileID": 0, "m_PathID": 0}}],
            ],
        },
    }


def test_set_material_texture_repoints_exactly_one_slot() -> None:
    """**纯函数**：只动指定的那一个槽，别的槽 / 别的组一个都不许动，入参也不许被改。"""
    tree = _fake_material_tree()
    out = writer.set_material_texture(tree, "Layer_ABC", {"m_FileID": 0, "m_PathID": 222})
    props = out["m_SavedProperties"]
    assert props["m_TexEnvs"][0][1]["m_Texture"] == {"m_FileID": 0, "m_PathID": 222}
    assert props["m_TexEnvs"][1] == tree["m_SavedProperties"]["m_TexEnvs"][1]
    assert props["m_Floats"] == tree["m_SavedProperties"]["m_Floats"]
    assert props["m_Colors"] == tree["m_SavedProperties"]["m_Colors"]
    assert out["m_Shader"] == tree["m_Shader"]
    # 入参不动
    assert tree["m_SavedProperties"]["m_TexEnvs"][0][1]["m_Texture"]["m_PathID"] == 111


def test_set_material_texture_unknown_slot_is_rejected() -> None:
    """负控：槽名写错必须报错 —— ⛔ 不许"没找到就当没事"（那会产出静默没改的包）。"""
    with pytest.raises(KeyError):
        writer.set_material_texture(
            _fake_material_tree(), "Layer_NOT_THERE", {"m_FileID": 0, "m_PathID": 222}
        )


def test_set_material_shader_replaces_only_the_reference() -> None:
    """shader 引用是 12 B PPtr ⇒ 换它仍然等长；纯函数同样不许改别的字段。"""
    tree = _fake_material_tree()
    out = writer.set_material_shader(tree, {"m_FileID": 0, "m_PathID": 99})
    assert out["m_Shader"] == {"m_FileID": 0, "m_PathID": 99}
    assert out["m_SavedProperties"] == tree["m_SavedProperties"]
    assert tree["m_Shader"] == {"m_FileID": 0, "m_PathID": 7}


# --------------------------------------------------------------------------- #
# F4 / F6 的真件版：调用**独立复核器**（_work/w5_verify_writeback.py，由 verifier 交付）
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = WORK / "w5_verify_writeback.py"
GAME = Path(os.environ.get("BA_MOD_MAKER_GAME", r"D:\Steam\steamapps\common\broken_arrow"))


def _aa_or_skip() -> Path:
    aa = GAME / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    if not (aa / "catalog.json").is_file():
        pytest.skip(f"本机没有游戏（{aa}）—— 真件判据在这里测不了")
    return aa


def _tamper_units_crc(data: bytes, hash_hex: str) -> bytes:
    """把某条 extra data 的 m_Crc 改掉（**等长**，纯字节级，不借 writer 的任何代码）。

    等长是为了不引入偏移平移 —— 这条负控只问「CRC 对不上时复核器红不红」。
    """
    raw = json.loads(data.decode("utf-8"))
    blob = base64.b64decode(raw["m_ExtraDataString"])
    head = ('{"m_Hash":"' + hash_hex + '","m_Crc":').encode("utf-16-le")
    pos = blob.find(head)
    assert pos >= 0, "catalog 里找不到那条 m_Crc"
    start = pos + len(head)
    end = start
    while blob[end : end + 2].decode("utf-16-le", "replace") != ",":
        end += 2
    old = int(blob[start:end].decode("utf-16-le"))
    new = str(old ^ 1).encode("utf-16-le")
    assert len(new) == end - start, "负控必须等长（否则会引入偏移平移，混进另一个口径）"
    raw["m_ExtraDataString"] = base64.b64encode(blob[:start] + new + blob[end:]).decode("ascii")
    return json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@pytest.mark.slow
def test_f4_independent_verifier_agrees_on_every_registered_bundle() -> None:
    """F4（真件）：独立复核器对 catalog 注册的每个 bundle 都要报一致（rc=0）。"""
    _aa_or_skip()
    assert VERIFIER.is_file(), f"独立复核器不在了：{VERIFIER}"
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--json", str(WORK / "w5_verify_test.json")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        check=False,
        timeout=1800,
    )
    assert proc.returncode == 0, (proc.stdout or "")[-3000:] + (proc.stderr or "")[-1000:]
    assert "[VERDICT] OK" in proc.stdout


@pytest.mark.slow
def test_f6_independent_verifier_flags_a_stale_catalog_crc(tmp_path: Path) -> None:
    """F6 负控（真件副本）：**故意不更新** catalog 的 m_Crc ⇒ 独立复核必须报错（能红）。

    ⛔ 只动 _work/ pytest 临时目录里的**副本**；游戏目录全程只读。
    """
    aa = _aa_or_skip()
    units = sorted((aa / "PC").glob("units_assets_all_*.bundle"))
    assert units, "找不到 units 包"
    hash_hex = units[0].name.split("_")[-1].removesuffix(".bundle")
    copy = tmp_path / "catalog_bad.json"
    copy.write_bytes(_tamper_units_crc((aa / "catalog.json").read_bytes(), hash_hex))
    proc = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--catalog",
            str(copy),
            "--bundle-dir",
            str(aa / "PC"),
            "--bundle",
            "units",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        check=False,
        timeout=1800,
    )
    assert proc.returncode == 1, (proc.stdout or "")[-3000:] + (proc.stderr or "")[-1000:]
    assert "复核结论: 不一致" in proc.stdout
    assert "[VERDICT] MISMATCH" in proc.stdout

