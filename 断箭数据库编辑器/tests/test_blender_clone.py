r"""⑥ 克隆真写回（gap 3）的机械判据：干跑 = 真写 · 对象表 / 预载表读数 · 还原 · 守卫 · 面板。

**判据一行**：.venv\Scripts\python.exe -m pytest tests\test_blender_clone.py -q -n 0
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 合成件正控：真类型表 + 录下来的真对象，7/7 **逐字节往返**、对象表严格升序 | 全过 |
| B | append_preload_entries 纯函数：按序追加；空 / 重复 / 已在表里三条负控 | 三条都能红 |
| C | preload_growth：M -> M+4、字节变长；没有 AssetBundle / classID 不对 / pid 撞了 | 全抛 |
| D | **J1** 干跑 vs 真写：同一条链，计划读数逐行相等，干跑一个字节都没写 | 相等 |
| E | **J2** 真写：对象表 N -> N+4、pathID 升序、byteStart 是 8 的倍数、预载表 M -> M+4 | 全对 |
| F | **J3** 反向 Δ：PatchStore.restore() 之后整文件 sha256 == 改前 | 相等 |
| G | **J4** 再点一次 = **再加一组**（N+8 / M+8 / m_Children 9），文案如实写出来 | 读数 + 文案 |
| H | 算子层守卫：引用判据 / 父不在候选 / 真写回必须先确认 | 都能红 |
| I | **J5** 面板：真画一遍，bamod.clone_execute 必须在按钮里（尺子自带正控） | 能绿能红 |

## 夹具从哪来（⛔ 不开 units 包 3.38 GB、⛔ 不碰游戏目录）

_work/w10_ref/units_meta.bin（真件 units 的**元数据区**：48 B 头 + 50 项类型表 +
162,010 条对象表 + 25,037 B 尾巴）＋ _work/w10_ref/src_objects/*.bin（7 个真对象字节）。
把类型表与尾巴**原样**搬进一个新 SerializedFile，对象表只留这 7 条（**按 pathID 升序** ——
writer 的升序尺子会挡），AssetBundle(pathID=1) 的载荷照**真类型树**自己序列化 ——
合成件约 98 KB，7 个对象逐字节往返，clone 的 pick_templates / 引用判据 / 写回链全都能跑。

⚠ 合成件的预载表 M=1，与真件的 197,538 条不是一个量级 —— 判据量的是**增量**（M -> M+4）。
⚠ 每个用例拿到的是**自己的副本**（tmp_path），真写回只写它。
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import shutil
import struct
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import reverse_delta, writer
from ba_mod_maker.core.asset import ufs

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"
W10_REF = WORK / "w10_ref"
UNITS_META = W10_REF / "units_meta.bin"
PKG = "bamod_clone_under_test"

#: 真件读数（_work/w10_ref/src_objects.json 里抽出来的那 7 个对象）。
AB_PID = 1
CHASSIS = -4049937970479168728
GAMEOBJECT = -8087847862595026136
MESHFILTER = -2136616851993984216
MESHRENDERER = 9194953101066830632
MESH = 2175081331415725408
MATERIAL = 7952762356820983200
AB_BUNDLE_NAME = "synth_clone_assets_all_00000000000000000000000000000000.bundle"
#: 合成件的基线读数：7 个对象 / 预载表 1 条 / Chassis 的 m_Children 7 个。
SYNTH_OBJECTS = 7
SYNTH_PRELOAD = 1
CHASSIS_CHILDREN = 7


def _common() -> object:
    """内嵌类型树的公共串表（延迟取：没有 UnityPy 时模块仍能被收集）。"""
    return cl.unitypy_common_strings()


# --------------------------------------------------------------------------- #
# 合成件（真类型表 + 真对象字节；⛔ 不读游戏盘）
# --------------------------------------------------------------------------- #
def _strip_meta(meta: bytes) -> tuple[bytes, bytes, bytes]:
    """真件元数据区 -> (48 B 头, 版本串..类型表末尾, 对象表之后那截尾巴)。

    走的是**产品自己的**解析器（clone._Cur / _read_type_tree_blob），
    只是把「类型表在哪结束」这一步的游标位置取出来。
    """
    cur = cl._Cur(meta, ufs.SF_HEADER_SIZE)
    cur.cstr()
    cur.i32()
    enable = bool(cur.u8())
    for _ in range(cur.i32()):
        cur.i32()
        cur.u8()
        script_index = cur.i16()
        if script_index >= 0:
            cur.take(16)
        cur.take(16)
        if enable:
            cl._read_type_tree_blob(cur, _common())
            deps = cur.i32()
            cur.pos += 4 * deps
    end = cur.pos
    count = struct.unpack_from("<i", meta, end)[0]
    table = end + 4
    table += (-table) % 4
    span = ufs.SF_HEADER_SIZE + struct.unpack_from(">I", meta, 20)[0]
    return (
        meta[: ufs.SF_HEADER_SIZE],
        meta[ufs.SF_HEADER_SIZE : end],
        meta[table + 24 * count : span],
    )


def _default_tree(tree: object) -> object:
    """照类型树造一棵**空**字段树（AssetBundle 的载荷要自己拼）。"""
    node = tree.node  # type: ignore[attr-defined]
    if node.type in cl._SCALARS:
        return 0
    if node.type == "string":
        return ""
    if node.type == "TypelessData":
        return b""
    children = tree.children  # type: ignore[attr-defined]
    if children and children[0].node.type == "Array":
        return []
    return {child.node.name: _default_tree(child) for child in children}


def _build_sf(head: bytes, body0: bytes, tail: bytes, objs: list[tuple[int, int, bytes]]) -> bytes:
    """最小 SerializedFile：头 + 类型表 + 新对象表（升序）+ 原样的尾巴 + 数据区。"""
    body = bytearray(body0)
    body += struct.pack("<i", len(objs))
    cursor = 0
    table: list[tuple[int, int, int, int]] = []
    for path_id, type_id, payload in objs:
        table.append((path_id, type_id, cursor, len(payload)))
        cursor += len(payload)
    for path_id, type_id, stored, size in table:
        body += bytes((-len(body)) % 4)
        body += struct.pack("<qqii", path_id, stored, size, type_id)
    body += tail
    metadata_size = len(body)
    data_offset = len(head) + metadata_size
    data_offset += (-data_offset) % 16
    data = b"".join(item[2] for item in objs)
    out = bytearray(head)
    struct.pack_into(">I", out, 8, 22)
    struct.pack_into(">I", out, 20, metadata_size)
    struct.pack_into(">q", out, 24, data_offset + len(data))
    struct.pack_into(">q", out, 32, data_offset)
    out += body
    out += bytes(data_offset - len(head) - metadata_size)
    out += data
    return bytes(out)


def _build_container(sf_blob: bytes, ress: bytes, name: str) -> bytes:
    """两节点最小 UnityFS 容器（node[0] = CAB、node[1] = .resS）。"""
    stream = sf_blob + ress
    blocks: list[ufs.Block] = []
    payload = bytearray()
    for index in range(0, len(stream), ufs.RECHUNK_SIZE):
        raw = stream[index : index + ufs.RECHUNK_SIZE]
        comp = ufs.lz4hc(raw)
        if len(comp) >= len(raw):
            payload += raw
            blocks.append(ufs.Block(len(raw), len(raw), 0))
        else:
            payload += comp
            blocks.append(ufs.Block(len(raw), len(comp), 3))
    nodes = [
        ufs.Node(offset=0, size=len(sf_blob), flags=4, path="CAB-" + name),
        ufs.Node(offset=len(sf_blob), size=len(ress), flags=4, path="CAB-" + name + ".resS"),
    ]
    raw_info = ufs.serialize_blocks_info(b"\x11" * 16, blocks, nodes)
    comp_info = ufs.lz4hc(raw_info)
    header = ufs.serialize_header(6, "5.x.x", "2022.3.62f3", 0, len(comp_info), len(raw_info), 0x43)
    total = len(header) + len(comp_info) + len(payload)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", total, len(comp_info), len(raw_info), 0x43
    )
    return header + comp_info + bytes(payload)


def _object_file(path_id: int, class_id: int) -> Path:
    return W10_REF / "src_objects" / f"{path_id}_{class_id}.bin"


@functools.lru_cache(maxsize=1)
def _synth_blob() -> bytes:
    """合成包的字节（整个用例模块只造一次）。"""
    meta = UNITS_META.read_bytes()
    head, body0, tail = _strip_meta(meta)
    schema = cl.read_type_table(meta, common_strings=_common())
    ab_nodes = schema.nodes(cl.ASSET_BUNDLE_CLASS_ID)
    ab_tree = _default_tree(cl._build_tree(ab_nodes))
    ab_tree["m_Name"] = AB_BUNDLE_NAME
    ab_tree["m_AssetBundleName"] = "synth_clone"
    ab_tree["m_PreloadTable"] = [cl.pptr(MESH)]
    ab_tree["m_Container"] = []
    ab_payload = cl.write_object(ab_tree, ab_nodes)
    if cl.read_object(ab_payload, ab_nodes) != ab_tree:
        raise AssertionError("AssetBundle 载荷自己都往返不了 —— 夹具坏了")
    wanted = [
        (AB_PID, cl.ASSET_BUNDLE_CLASS_ID, ab_payload),
        (CHASSIS, 4, _object_file(CHASSIS, 4).read_bytes()),
        (GAMEOBJECT, 1, _object_file(GAMEOBJECT, 1).read_bytes()),
        (MESHFILTER, 33, _object_file(MESHFILTER, 33).read_bytes()),
        (MESHRENDERER, 23, _object_file(MESHRENDERER, 23).read_bytes()),
        (MESH, 43, _object_file(MESH, 43).read_bytes()),
        (MATERIAL, 21, _object_file(MATERIAL, 21).read_bytes()),
    ]
    objs = sorted(
        ((path_id, schema.type_id(class_id), payload) for path_id, class_id, payload in wanted),
        key=lambda row: row[0],
    )
    return _build_container(_build_sf(head, body0, tail, objs), bytes(200_007), "synth")


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件抽件产物）")
    path = tmp_path_factory.mktemp("clone") / "synth.bundle"
    path.write_bytes(_synth_blob())
    return path


@pytest.fixture
def case(synth: Path, tmp_path: Path) -> Path:
    """每个用例一份自己的副本 —— 真写回只写它。"""
    path = tmp_path / "case.bundle"
    shutil.copy2(synth, path)
    return path


# --------------------------------------------------------------------------- #
# 共用的读回 / 计划 / 写回（全走产品入口）
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _readback(path: Path) -> dict[str, object]:
    """独立读回：对象表、新对象坐标、AssetBundle 的预载表。"""
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = bytes(reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = cl.read_type_table(meta, common_strings=_common())
        payload = cl.read_object_payload(reader, node, info, AB_PID)
        tree = cl.read_object(payload, schema.nodes(cl.ASSET_BUNDLE_CLASS_ID))
    return {
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "object_count": info.object_count,
        "path_ids": [row[1] for row in info.objects],
        "rows": {row[1]: (row[2], row[3], row[4]) for row in info.objects},
        "preload": [int(item["m_PathID"]) for item in tree["m_PreloadTable"]],
        "container": [
            (str(entry["first"]), int(entry["second"]["asset"]["m_PathID"]))
            for entry in tree["m_Container"]
        ],
    }


def _target(**over: object) -> object:
    base: dict[str, object] = {
        "parent_transform": CHASSIS,
        "mesh": MESH,
        "materials": (MATERIAL,),
        "name": "RU_T72B_clone_track",
    }
    base.update(over)
    return cl.CloneTarget(**base)  # type: ignore[arg-type]


def _plan(path: Path, **over: object) -> tuple[object, object, object]:
    """克隆图 -> 预载表 growth -> 一次写回的 DeltaChange（与面板那条链同款）。"""
    graph = cl.plan_clone_from_bundle(str(path), _target(**over), common_strings=_common())
    preload = cl.preload_growth(path, graph, common_strings=_common())
    return graph, preload, cl.delta_change(graph, growths=(preload.growth,))


def _write(path: Path, change: object, store_dir: Path, *, dry_run: bool) -> object:
    store = reverse_delta.PatchStore(store_dir, game_root=path.parent)
    return writer.write_delta(
        path,
        change,  # type: ignore[arg-type]
        dry_run=dry_run,
        game_root=path.parent,
        patch_store=store,
        patch_label="用例：⑥ 克隆真写回",
    )


# --------------------------------------------------------------------------- #
# A · 合成件正控
# --------------------------------------------------------------------------- #
def test_synth_bundle_roundtrips_every_object_and_its_table_is_sorted(synth: Path) -> None:
    """正控：7 个真对象**逐个**逐字节往返；对象表严格升序（writer 的升序尺子先过）。"""
    with ufs.UfsReader(synth) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = bytes(reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = cl.read_type_table(meta, common_strings=_common())
        ids = [row[1] for row in info.objects]
        assert info.object_count == SYNTH_OBJECTS, info.object_count
        assert ids == sorted(set(ids)), f"夹具的对象表不是严格升序：{ids}"
        classes = {}
        for _index, path_id, _stored, _size, type_id in info.objects:
            class_id = next(item.class_id for item in schema.entries if item.index == type_id)
            payload = cl.read_object_payload(reader, node, info, path_id)
            assert cl.roundtrip(payload, schema.nodes(class_id)) == payload, f"pathID={path_id}"
            classes[path_id] = class_id
    assert classes[AB_PID] == cl.ASSET_BUNDLE_CLASS_ID


def test_synth_bundle_baseline_readings(case: Path) -> None:
    """基线读数：对象表 7 条 / 预载表 1 条 / Chassis 的 m_Children 7 个。"""
    before = _readback(case)
    assert before["object_count"] == SYNTH_OBJECTS
    assert len(before["preload"]) == SYNTH_PRELOAD
    graph, _preload, _change = _plan(case)
    assert len(graph.parent_children_before) == CHASSIS_CHILDREN  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# B · append_preload_entries（纯函数 + 三条负控）
# --------------------------------------------------------------------------- #
def _preload_rows(count: int) -> list[dict[str, int]]:
    return [{"m_FileID": 0, "m_PathID": 1000 + index} for index in range(count)]


def test_append_preload_entries_keeps_order_and_the_old_fields() -> None:
    """正控：末尾按序追加，原有条目（含 m_FileID）一个都不动。"""
    rows = cl.append_preload_entries(_preload_rows(3), [7, 8])
    assert [item["m_PathID"] for item in rows] == [1000, 1001, 1002, 7, 8]
    assert all(item["m_FileID"] == 0 for item in rows)
    assert [item["m_PathID"] for item in _preload_rows(3)] == [1000, 1001, 1002], "原表被就地改了"


@pytest.mark.parametrize(
    "preload,new_ids,needle",
    [
        (_preload_rows(2), [], "空的"),
        (_preload_rows(2), [7, 7], "有重复"),
        (_preload_rows(3), [1001], "已经在 m_PreloadTable 里"),
    ],
    ids=["empty", "duplicated", "already-there"],
)
def test_append_preload_entries_negative_controls(
    preload: list[dict[str, int]], new_ids: list[int], needle: str
) -> None:
    """负控：空 / 重复 / 已在表里 —— 三条都必须抛，⛔ 不许静默。"""
    with pytest.raises(cl.AssetError) as excinfo:
        cl.append_preload_entries(preload, new_ids)
    assert needle in str(excinfo.value), str(excinfo.value)


# --------------------------------------------------------------------------- #
# C · preload_growth（合成件上真读）
# --------------------------------------------------------------------------- #
def test_preload_growth_reads_m_to_m_plus_four(case: Path) -> None:
    """正控：四个新对象各进一条，AssetBundle 的字节真的变长（ObjectGrowth 的前提）。"""
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    preload = cl.preload_growth(case, graph, common_strings=_common())
    assert (preload.preload_before, preload.preload_after) == (
        SYNTH_PRELOAD,
        SYNTH_PRELOAD + len(graph.objects),
    )
    assert preload.payload_after > preload.payload_before
    assert preload.growth.path_id == AB_PID
    assert preload.growth.type_name == "AssetBundle"
    assert preload.path_ids == tuple(item.path_id for item in graph.objects)
    text = "\n".join(preload.criterion_lines())
    assert "m_PreloadTable 1 -> 5" in text
    assert "m_Container 不动" in text


def test_preload_growth_refuses_a_bundle_without_an_assetbundle(case: Path) -> None:
    """负控：pid 不是 AssetBundle 的对象 -> 抛（⛔ 不许拿别的对象去改）。"""
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    with pytest.raises(cl.AssetError) as excinfo:
        cl.preload_growth(case, graph, asset_bundle_path_id=999, common_strings=_common())
    assert "没有 pathID=999" in str(excinfo.value)
    with pytest.raises(cl.AssetError) as excinfo2:
        cl.preload_growth(case, graph, asset_bundle_path_id=MESH, common_strings=_common())
    assert "classID 是 43" in str(excinfo2.value)


def test_preload_growth_refuses_a_clashing_path_id(case: Path) -> None:
    """负控（J4 第 1 条）：新对象的 pathID 与包内已有对象撞了 -> 抛。"""
    import dataclasses

    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    first = dataclasses.replace(graph.objects[0], path_id=CHASSIS)
    poisoned = dataclasses.replace(graph, objects=(first, *graph.objects[1:]))
    with pytest.raises(cl.AssetError) as excinfo:
        cl.preload_growth(case, poisoned, common_strings=_common())
    assert "撞了" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# C2 · 地址级条目（m_Container）：**不给**与既有行为逐字节相同；**给**则单开一条
# --------------------------------------------------------------------------- #
#: 用例里那条地址（形状 = catalog 的 m_InternalIds 那一条）。
ADDR = "Assets/Resources_moved/ModelPrefabs/My/part.prefab"


def _recomputed_preload_payload(path: Path, graph: object) -> bytes:
    """独立重算（⛔ 不调 preload_growth）：读 AssetBundle 树 -> 只追加预载表 -> 序列化。

    它是「不给 addresses ⇒ 字节与既有行为逐字节相同」这条判据的**另一半**。
    """
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = bytes(reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = cl.read_type_table(meta, common_strings=_common())
        payload = cl.read_object_payload(reader, node, info, AB_PID)
        nodes = schema.nodes(cl.ASSET_BUNDLE_CLASS_ID)
        tree = cl.read_object(payload, nodes)
    tree["m_PreloadTable"] = cl.append_preload_entries(
        tree["m_PreloadTable"], [item.path_id for item in graph.objects]  # type: ignore[attr-defined]
    )
    return cl.write_object(tree, nodes)


def test_preload_growth_without_addresses_is_byte_identical(case: Path) -> None:
    """**判据（字节级）**：不传 addresses ⇒ AssetBundle 新字节 == 独立重算的那一串。

    这条是「本单没改既有行为」的机械证明 —— 不是「看起来一样」。
    """
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    preload = cl.preload_growth(case, graph, common_strings=_common())
    assert preload.addresses == ()
    assert (preload.container_before, preload.container_after) == (0, 0)
    assert preload.growth.payload == _recomputed_preload_payload(case, graph)
    assert "m_Container 不动" in "\n".join(preload.criterion_lines())


def test_preload_growth_with_an_address_adds_one_container_entry(case: Path) -> None:
    """**判据一行**：m_Container **0 -> 1**、m_PreloadTable 1 -> 5。

    那条指向新零件的根 GameObject（`ADDRESS_ROOT_CLASS_ID`）。
    """
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    root = graph.object(cl.ADDRESS_ROOT_CLASS_ID)
    assert cl.ADDRESS_ROOT_CLASS_ID == cl.CLASS_GAME_OBJECT
    preload = cl.preload_growth(case, graph, common_strings=_common(),
                                addresses=[(ADDR, root.path_id)])
    assert (preload.container_before, preload.container_after) == (0, 1)
    assert (preload.preload_before, preload.preload_after) == (SYNTH_PRELOAD, SYNTH_PRELOAD + 4)
    assert preload.addresses == ((ADDR, root.path_id),)
    tree = _ab_tree_of(preload.growth.payload, case)
    assert tree["m_Container"] == [
        {
            "first": ADDR,
            "second": {"preloadIndex": 1, "preloadSize": 1, "asset": cl.pptr(root.path_id)},
        }
    ]
    assert [int(item["m_PathID"]) for item in tree["m_PreloadTable"]][1] == root.path_id
    text = "\n".join(preload.criterion_lines())
    assert "m_Container 0 -> 1" in text and ADDR in text
    assert "m_Container 不动" not in text, "给了地址还说「不动」—— 文案在撒谎"


def _ab_tree_of(payload: bytes, source: Path) -> dict:
    """从 AssetBundle 的**新字节**读回字段树（类型表取自 source —— 独立读法）。"""
    with ufs.UfsReader(source) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = bytes(reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = cl.read_type_table(meta, common_strings=_common())
    return cl.read_object(payload, schema.nodes(cl.ASSET_BUNDLE_CLASS_ID))


@pytest.mark.parametrize(
    "addresses,needle",
    [
        ([(ADDR, MESH)], "不是**本次新增**的对象"),
        ([(ADDR, "x")], "pathID 不是整数"),
        ([("not-a-pair",)], "不是 (地址, pathID) 两元组"),
        ([(ADDR, 1.5)], "pathID 不是整数"),
        ([("   ", None)], "地址是空的"),
    ],
    ids=["points-at-an-existing-object", "pid-not-an-integer", "not-a-pair",
         "pid-is-a-float", "blank"],
)
def test_check_addresses_negative_controls(
    case: Path, addresses: list, needle: str
) -> None:
    """**负控**：指到既有对象 / 不是两元组 / 空地址 —— 三条都必须抛（⛔ 不许静默）。"""
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    with pytest.raises(cl.AssetError) as excinfo:
        cl.check_addresses(graph, addresses)
    assert needle in str(excinfo.value), str(excinfo.value)


def test_check_addresses_refuses_duplicates(case: Path) -> None:
    """**负控**：同一条地址两次 / 同一个 pathID 两条地址 —— 两条都要抛。"""
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    go, mf = graph.object(cl.CLASS_GAME_OBJECT), graph.object(cl.CLASS_MESH_FILTER)
    with pytest.raises(cl.AssetError) as excinfo:
        cl.check_addresses(graph, [(ADDR, go.path_id), (ADDR + ".2", go.path_id)])
    assert "被两条地址点名" in str(excinfo.value)
    with pytest.raises(cl.AssetError) as excinfo2:
        cl.check_addresses(graph, [(ADDR, go.path_id), (ADDR, mf.path_id)])
    assert "给了两次" in str(excinfo2.value)


# --------------------------------------------------------------------------- #
# D/E/F · 全链：J1 干跑 = 真写 · J2 读数 · J3 还原
# --------------------------------------------------------------------------- #
def test_j1_dry_run_equals_the_real_write_and_writes_nothing(case: Path) -> None:
    """**J1**：同一条链的干跑与真写，写回计划的判据行**逐行相等**；干跑一个字节都没动。"""
    before = _readback(case)
    _graph, _preload, change = _plan(case)
    dry = _write(case, change, case.parent / "patches", dry_run=True)
    assert dry.dry_run is True
    assert _readback(case)["sha256"] == before["sha256"], "干跑动了文件"
    real = _write(case, change, case.parent / "patches", dry_run=False)
    assert list(dry.plan.criterion_lines()) == list(real.plan.criterion_lines())
    assert dry.plan.added_objects == real.plan.added_objects
    assert dry.plan.rows == real.plan.rows
    # 干跑那份也得印出「Δ 新增对象」与 Δmeta 两行（判据 J1 的落点）
    text = "\n".join(dry.plan.criterion_lines())
    assert "Δ 新增对象" in text and "Δmeta" in text


def test_j2_object_table_and_preload_readings(case: Path) -> None:
    """**J2**：对象表 N -> N+4、新 pathID 严格升序、byteStart 是 8 的倍数、预载表 M -> M+4。"""
    before = _readback(case)
    graph, preload, change = _plan(case)
    planned = [item.path_id for item in graph.objects]
    report = _write(case, change, case.parent / "patches", dry_run=False)
    assert report.verify["J1/J7 新增对象（表 / 对齐 / 读回）"]["ok"] is True, report.verify
    after = _readback(case)

    assert after["object_count"] == before["object_count"] + 4
    assert after["path_ids"] == sorted(after["path_ids"]), "对象表不再严格升序"
    new_ids = [pid for pid in after["path_ids"] if pid not in before["path_ids"]]
    assert new_ids == planned, (new_ids, planned)
    assert all(pid % 8 == 0 for pid in new_ids), new_ids
    assert all(pid > max(before["path_ids"]) for pid in new_ids), "新 pathID 不是严格更大"
    for pid in new_ids:
        stored, size, _type_id = after["rows"][pid]
        assert stored % 8 == 0, f"pathID={pid} 的 byteStart {stored} 不是 8 的倍数"
        assert size == next(item.size for item in graph.objects if item.path_id == pid)
    assert len(after["preload"]) == len(before["preload"]) + 4
    assert after["preload"] == [*before["preload"], *planned]
    assert (preload.preload_before, preload.preload_after) == (
        len(before["preload"]),
        len(before["preload"]) + 4,
    )


def test_j3_reverse_delta_restores_the_whole_file(case: Path) -> None:
    """**J3**：写完 -> 反向 Δ 还原 ⇒ 整文件 sha256 == 改前（这条最硬的判据）。"""
    before = _readback(case)
    _graph, _preload, change = _plan(case)
    report = _write(case, change, case.parent / "patches", dry_run=False)
    assert report.patch, "真写回必须留下一条可还原的补丁"
    after = _readback(case)
    assert after["sha256"] != before["sha256"], "写回没改文件？"
    restored = reverse_delta.PatchStore(case.parent / "patches", game_root=case.parent).restore()
    assert restored.ok is True, restored.lines()
    back = _readback(case)
    assert back["sha256"] == before["sha256"]
    assert back["bytes"] == before["bytes"]
    assert back["object_count"] == SYNTH_OBJECTS


# --------------------------------------------------------------------------- #
# G · J4：再点一次 = 再加一组（不说清楚就是静默）
# --------------------------------------------------------------------------- #
def test_j4_second_run_adds_another_group_and_restores_to_the_original(case: Path) -> None:
    """**J4**：同一张图再点一次是**再加一组**（N+8 / M+8 / m_Children 9），⛔ 不幂等；
    两次合并成一条反向 Δ，还原之后仍是改前的 sha256。"""
    before = _readback(case)
    store_dir = case.parent / "patches"
    graph_one, _preload_one, change_one = _plan(case)
    _write(case, change_one, store_dir, dry_run=False)
    graph_two, preload_two, change_two = _plan(case)
    _write(case, change_two, store_dir, dry_run=False)

    assert len(graph_two.parent_children_before) == CHASSIS_CHILDREN + 1
    second_ids = [item.path_id for item in graph_two.objects]
    first_ids = [item.path_id for item in graph_one.objects]
    assert all(pid > max(first_ids) for pid in second_ids), "第二组不是严格更大的新 pathID"
    assert preload_two.preload_before == SYNTH_PRELOAD + 4
    after = _readback(case)
    assert after["object_count"] == before["object_count"] + 8
    assert len(after["preload"]) == len(before["preload"]) + 8
    assert after["preload"][-4:] == second_ids

    restored = reverse_delta.PatchStore(store_dir, game_root=case.parent).restore()
    assert restored.ok is True, restored.lines()
    assert _readback(case)["sha256"] == before["sha256"]


# --------------------------------------------------------------------------- #
# H · 算子层（按路径加载 + bpy 桩；合成包上跑真链）
# --------------------------------------------------------------------------- #
def _bpy_stub() -> types.ModuleType:
    """最小 bpy 桩（与 tests/test_paint_panel.py 同款）。"""
    existing = sys.modules.get("bpy")
    if existing is not None:
        return existing
    bpy = types.ModuleType("bpy")

    def _prop(**kwargs: object) -> dict[str, object]:
        return dict(kwargs)

    bpy.props = types.SimpleNamespace(  # type: ignore[attr-defined]
        StringProperty=_prop,
        BoolProperty=_prop,
        FloatProperty=_prop,
        IntProperty=_prop,
        EnumProperty=_prop,
        PointerProperty=_prop,
        CollectionProperty=_prop,
    )
    bpy.types = types.SimpleNamespace(  # type: ignore[attr-defined]
        Panel=type("Panel", (), {}),
        Operator=type("Operator", (), {}),
        PropertyGroup=type("PropertyGroup", (), {}),
        # 0.15.0：选择器换成了 UIList（panels.BamodUnitList 的基类）。
        UIList=type("UIList", (), {}),
    )
    sys.modules["bpy"] = bpy
    return bpy


@functools.lru_cache(maxsize=1)
def _addon() -> dict[str, types.ModuleType]:
    """按路径加载 panels / operators（合成包里两个空模块顶掉相对 import）。"""
    _bpy_stub()
    package = types.ModuleType(PKG)
    package.__path__ = []  # type: ignore[attr-defined]
    sys.modules[PKG] = package
    for stub_name in ("importer", "runtime"):
        stub = types.ModuleType(f"{PKG}.{stub_name}")
        sys.modules[stub.__name__] = stub
        setattr(package, stub_name, stub)
    out: dict[str, types.ModuleType] = {}
    # ★ unit_picker 用**真的那一份**（它 bpy-free）：面板的行高常量只能有一个出处。
    # ★ task-9（2026-10-11）拆包：⑦ 面板在 panels_clone.py，它 `from .panels_common import …`
    #   ⇒ 先把共用件挂进包里（⛔ 保持 `__path__ = []`：不去盘上找兄弟模块）。
    for name in ("unit_picker", "panels_common", "panels_clone", "operators"):
        spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", ADDON / f"{name}.py")
        assert spec is not None and spec.loader is not None, f"加载不了 {name}.py"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(package, name, module)
        spec.loader.exec_module(module)
        out[name] = module
    # ⚠ 本文件里 PANELS 必须**就是 ⑦ 面板那个模块**：有两处 `monkeypatch.setattr(PANELS, …)`
    #   打的是 draw() 里真去查的**全局名**（走汇总壳打不着 —— 那是另一个模块的命名空间）。
    #   `UNIT_LIST_TYPE` 住在共用件里，这里把它接到 ⑦ 面板模块上（⛔ 断言一个字没动）。
    out["panels_clone"].UNIT_LIST_TYPE = out["panels_common"].UNIT_LIST_TYPE
    return out


if not (ADDON / "panels_clone.py").is_file():
    pytest.skip(
        "旧模块 panels_clone.py 已在插件重做时删除 —— 这份用例待移植",
        allow_module_level=True,
    )
OPS = _addon()["operators"]
PANELS = _addon()["panels_clone"]
PICKER = _addon()["unit_picker"]


class _Settings:
    """Scene.bamod 的最小替身（只放链上真用得到的那几个字段）。"""

    def __init__(self, **kw: object) -> None:
        self.game_root = str(kw.get("game_root", ""))
        self.project_root = str(kw.get("project_root", ""))
        self.unit_key = str(kw.get("unit_key", "RU_T72B"))
        self.clone_parent_transform = str(kw.get("parent", CHASSIS))
        self.clone_mesh = str(kw.get("mesh", MESH))
        self.clone_material = str(kw.get("material", MATERIAL))
        self.clone_name = str(kw.get("name", "RU_T72B_clone_track"))
        self.clone_offset_x = 0.0
        self.clone_offset_y = 0.0
        self.clone_offset_z = 0.0
        self.clone_level = str(kw.get("level", "Δ"))
        #: 地址级条目（可选）：真 Blender 里这是 Scene.bamod 上的一个属性；
        #: ⚠ 本单**没在 properties.py 里注册它**（不在写入范围）—— 属性不存在时
        #: _clone_addresses 走 getattr 默认值 ⇒ 一条地址都不加（见下面那条用例）。
        self.clone_address = str(kw.get("address", ""))
        self.clone_path_ids = ""
        self.clone_report_json = ""
        #: 「筛选单位」那一格（① 与 ⑦ 共用）—— ⑦ 里必须也画出来（易用性判据 2）。
        self.unit_filter = str(kw.get("unit_filter", ""))
        #: 0.15.0 的选择器：行集合 / 高亮行 / 两行读数（面板只印不算）。
        #: ⚠ 这里给 None：本文件不挂 properties.py（面板那一层读到 None 会退化成
        #:   「已选：<内部键>（它不在当前列表里）」），真集合那半在 tests/test_unit_picker.py。
        self.unit_rows = None
        self.unit_row_index = 0
        self.unit_reading = str(kw.get("unit_reading", ""))
        self.unit_note = str(kw.get("unit_note", ""))
        self.unit_rows_stamp = ""
        #: 「自动填建议地址」开关（默认关：⛔ 不替用户往包里加地址条目）。
        self.clone_use_suggested_address = bool(kw.get("use_suggested_address", False))
        self.status = ""


class _FakeSource:
    """只读侧的替身：只回答 _clone_candidates 要的两件事。"""

    def read_prefab_nodes(self, asset_path: str) -> tuple[list[dict], dict]:
        nodes = [
            {
                "name": "Chassis",
                "transform_id": CHASSIS,
                "parent_transform": None,
                "components": [{"kind": "MeshFilter", "mesh": MESH, "mesh_file": 0}],
            }
        ]
        return nodes, {MATERIAL: {"name": "RU_T72B_track"}}

    def read_typetree(self, path_id: int) -> dict:
        return {"m_Name": "RU_TanktrakR", "m_VertexData": {"m_VertexCount": 1289}}


def _operator_settings(case_dir: Path, **kw: object) -> _Settings:
    return _Settings(
        game_root=str(case_dir),
        project_root=str(case_dir / "proj"),
        **kw,
    )


@contextmanager
def _fake_open(aa_dir: object, candidates: object, asset_path: str):
    yield _FakeSource(), "case.bundle"


def _patch_the_readers(monkeypatch: pytest.MonkeyPatch, case_dir: Path) -> None:
    """把「开包列候选」与「catalog 在哪」两处换成合成件上的替身（链的其余部分都是真的）。"""
    from ba_mod_maker.asset_kernel import unitypy_source
    from ba_mod_maker.core.assets import game as game_mod

    monkeypatch.setattr(OPS, "_clone_locate", lambda root, key: (case_dir, "Assets/x.prefab", ()))
    monkeypatch.setattr(unitypy_source, "open_unique_source", _fake_open)
    monkeypatch.setattr(game_mod, "catalog_file", lambda root: None)


def test_operator_dry_run_equals_the_real_write(
    case: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**J1（算子层）**：_run_clone_write 的干跑与真写，面板那几行读数逐条相等。"""
    before = _readback(case)
    _patch_the_readers(monkeypatch, case.parent)
    dry = OPS._run_clone_write(_operator_settings(case.parent), dry_run=True)
    assert _readback(case)["sha256"] == before["sha256"], "干跑动了文件"
    real = OPS._run_clone_write(_operator_settings(case.parent), dry_run=False)
    assert dry["path_ids"] == real["path_ids"]
    dry_lines = [str(line) for line in dry["lines"]]
    real_lines = [str(line) for line in real["lines"]]
    assert any("一个字节都没写" in line for line in dry_lines), dry_lines
    assert list(dry["report"].plan.criterion_lines()) == list(
        real["report"].plan.criterion_lines()
    )

    def _readings(lines: list[str]) -> list[str]:
        """共享段 = 抬头那行之后、到「再加一组」那句之前（克隆图 + 引用 + 预载表）。

        干跑多一句「一个字节都没写」的提示 —— 那不是读数，比的时候剔掉。
        """
        out: list[str] = []
        for line in lines[1:]:
            if "再加一组" in line:
                break
            if "一个字节都没写" in line:
                continue
            out.append(line)
        return out

    shared = _readings(dry_lines)
    assert shared == _readings(real_lines), (shared, _readings(real_lines))
    assert len(shared) >= 8, shared
    assert any("m_PreloadTable 1 -> 5" in line for line in dry_lines)
    after = _readback(case)
    assert after["object_count"] == before["object_count"] + 4
    assert after["preload"][-4:] == dry["path_ids"]


def test_clone_addresses_reads_the_optional_property(case: Path) -> None:
    """⑥ 的地址透传：**属性不存在** / 空 ⇒ 一条都不加；给了 ⇒ 指向新零件的根 GameObject。"""
    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    assert OPS._clone_addresses(type("NoProps", (), {})(), graph) == ()
    # 真默认值就是这个：properties.py 里 clone_address 的 default 是空串
    assert OPS._clone_addresses(types.SimpleNamespace(clone_address=""), graph) == ()
    assert OPS._clone_addresses(_Settings(), graph) == ()
    assert OPS._clone_addresses(_Settings(address="   "), graph) == ()
    assert OPS._clone_addresses(_Settings(address=ADDR), graph) == (
        (ADDR, graph.object(cl.CLASS_GAME_OBJECT).path_id),
    )


def test_operator_writes_the_container_entry_when_an_address_is_given(
    case: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**端到端**：给了地址 ⇒ 真写之后 m_Container 0 -> 1，那条指向新 GameObject。"""
    _patch_the_readers(monkeypatch, case.parent)
    before = _readback(case)
    assert before["container"] == []
    result = OPS._run_clone_write(_operator_settings(case.parent, address=ADDR), dry_run=False)
    after = _readback(case)
    go = result["path_ids"][1]  # 顺序 = Transform, GameObject, MeshFilter, MeshRenderer
    assert after["container"] == [(ADDR, go)]
    assert after["object_count"] == before["object_count"] + 4
    assert sorted(after["preload"][-4:]) == sorted(result["path_ids"])


def test_operator_without_an_address_leaves_the_container_alone(
    case: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**负控的另一半**：不给地址 ⇒ m_Container 一条都不加（⛔ 不许「顺手加一条」）。"""
    _patch_the_readers(monkeypatch, case.parent)
    OPS._run_clone_write(_operator_settings(case.parent), dry_run=False)
    assert _readback(case)["container"] == []


def test_operator_says_out_loud_that_the_second_run_adds_another_group(case: Path) -> None:
    """**J4**：面板文本必须把「再点一次 = 再加一组」写出来（⛔ 不许静默）。"""
    graph, preload, _change = _plan(case)
    lines = OPS._clone_write_lines(
        graph, preload, graph.refs(), "REPORT", "case.bundle", dry_run=True
    )
    text = "\n".join(str(line) for line in lines)
    assert "再加一组" in text and "不幂等" in text, text
    assert "不真写" not in text, "过期文案还在"


def test_operator_refuses_a_foreign_write_level(
    case: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """负控：写面等级不是 Δ+ -> 抛（⛔ 不许拿别的等级蒙混）。"""
    _patch_the_readers(monkeypatch, case.parent)
    with pytest.raises(ValueError) as excinfo:
        OPS._run_clone_write(_operator_settings(case.parent, level="L1"), dry_run=True)
    assert "L1" in str(excinfo.value) and "不支持" in str(excinfo.value)


def test_clone_ref_gate_goes_red_on_a_dangling_pptr(case: Path) -> None:
    """**J4**：引用判据不过 -> 当场抛；正控：干净的图必须过。"""
    import dataclasses

    graph = cl.plan_clone_from_bundle(str(case), _target(), common_strings=_common())
    assert OPS._clone_ref_gate(graph) is not None
    mesh_filter = graph.object(cl.CLASS_MESH_FILTER)
    broken = dataclasses.replace(
        mesh_filter,
        fields={**dict(mesh_filter.fields), "m_Mesh": cl.pptr(1234567890123456789)},
    )
    poisoned = dataclasses.replace(
        graph,
        objects=tuple(
            broken if item.class_id == cl.CLASS_MESH_FILTER else item for item in graph.objects
        ),
    )
    with pytest.raises(ValueError) as excinfo:
        OPS._clone_ref_gate(poisoned)
    assert "引用判据没过" in str(excinfo.value)
    assert "1234567890123456789" in str(excinfo.value)


def test_clone_build_target_refuses_a_parent_outside_the_candidates() -> None:
    """**J4**：靶子不在这棵 prefab 的候选里 -> 抛；正控：候选里的父必须能造出靶子。"""
    candidates = {
        "asset_path": "Assets/x.prefab",
        "node_count": 1,
        "parents": [("Chassis", CHASSIS, None)],
        "parent_count": 1,
        "meshes": [{"path_id": MESH, "name": "RU_TanktrakR", "vertices": 1289, "readable": True}],
        "mesh_count": 1,
        "materials": [{"path_id": MATERIAL, "name": "RU_T72B_track"}],
        "material_count": 1,
    }
    good = OPS._clone_build_target(_Settings(), candidates)
    assert good.parent_transform == CHASSIS
    assert good.mesh == MESH and good.materials == (MATERIAL,)
    with pytest.raises(ValueError) as excinfo:
        OPS._clone_build_target(_Settings(parent=12345), candidates)
    assert "不在这棵 prefab 的候选里" in str(excinfo.value)


class _Reporter:
    """self.report 的替身：算子报了什么都留着。"""

    def __init__(self) -> None:
        self.calls: list[tuple[set[str], str]] = []

    def __call__(self, kind: set[str], message: str) -> None:
        self.calls.append((set(kind), str(message)))

    def errors(self) -> list[str]:
        return [message for kind, message in self.calls if "ERROR" in kind]


def _operator(cls: type, settings: _Settings) -> tuple[object, _Reporter]:
    op = cls.__new__(cls)
    reporter = _Reporter()
    op.report = reporter  # type: ignore[method-assign]
    return op, reporter


def test_clone_execute_refuses_without_the_confirmation(case: Path) -> None:
    """**J4**：没点确认就直接 execute -> 必须拒绝，且盘上不动。"""
    before = _readback(case)
    op, reporter = _operator(OPS.BamodCloneExecute, _operator_settings(case.parent))
    op.confirmed = False
    settings = _operator_settings(case.parent)
    context = types.SimpleNamespace(scene=types.SimpleNamespace(bamod=settings))
    assert op.execute(context) == {"CANCELLED"}
    assert any("先确认" in message for message in reporter.errors()), reporter.calls
    assert _readback(case)["sha256"] == before["sha256"]


def test_clone_execute_invoke_asks_for_the_confirmation(case: Path) -> None:
    """确认框：invoke 必须把 confirmed 置真并交给 window_manager 弹一次确认。"""
    seen: dict[str, object] = {}

    class _WindowManager:
        def invoke_confirm(self, op: object, event: object) -> set[str]:
            seen["op"] = op
            seen["event"] = event
            return {"RUNNING_MODAL"}

    op, _reporter = _operator(OPS.BamodCloneExecute, _operator_settings(case.parent))
    op.confirmed = False
    context = types.SimpleNamespace(
        scene=types.SimpleNamespace(bamod=_operator_settings(case.parent)),
        window_manager=_WindowManager(),
    )
    assert op.invoke(context, "EVENT") == {"RUNNING_MODAL"}
    assert op.confirmed is True and seen["event"] == "EVENT"


def test_clone_write_chain_uses_the_frozen_product_entries() -> None:
    """接缝：这条链真的走 plan_clone_from_bundle / preload_growth / delta_change / write_delta。"""
    import ast

    source = (ADDON / "operators.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    chain = ""
    execute = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_clone_write":
            chain = ast.get_source_segment(source, node) or ""
        if isinstance(node, ast.ClassDef) and node.name == "BamodCloneExecute":
            execute = ast.get_source_segment(source, node) or ""
    for needle in (
        "clone_mod.plan_clone_from_bundle(",
        "clone_mod.preload_growth(",
        "clone_mod.delta_change(",
        "write_delta(",
        "patch_store=",
        "dry_run=dry_run",
    ):
        assert needle in chain, f"_run_clone_write 里没有 {needle}"
    assert 'bl_idname = "bamod.clone_execute"' in execute
    assert "dry_run=False" in execute
    assert "invoke_confirm" in execute


# --------------------------------------------------------------------------- #
# I · J5 面板（bpy 桩；尺子自带正控）
# --------------------------------------------------------------------------- #
class _LayoutRecorder:
    """layout 的替身：只记「画了哪些按钮 / 哪些输入格 / 写了哪些字」。"""

    def __init__(self) -> None:
        self.operators: list[str] = []
        self.labels: list[str] = []
        #: 画出来的**输入格**（prop(settings, "xxx")）—— ②/⑥ 面板那一格就用它量。
        self.props: list[str] = []
        #: UIList：template_list(listtype_name, list_id, dataptr, propname, ...) 的
        #: (类名, list_id, 集合名) —— 判据 ①「画了 UIList」量的就是它。
        self.lists: list[tuple[str, str, str]] = []
        #: 每个 UIList 要了几行高（判据：固定 MAX_LIST_ROWS 行，⛔ 不是撑爆面板）。
        self.list_rows: list[int] = []
        #: 画东西的**先后**（(种类, 名字)）—— 「搜索框在列表上面」那条靠它量。
        self.calls: list[tuple[str, str]] = []

    def box(self) -> _LayoutRecorder:
        return self

    def row(self, **_kw: object) -> _LayoutRecorder:
        return self

    def column(self, *_a: object, **_k: object) -> _LayoutRecorder:
        # ★ v0.16.0：视觉层（panels_common 的 begin/heading/report_box/…）会用这些容器。
        return self

    def split(self, *_a: object, **_k: object) -> _LayoutRecorder:
        return self

    def separator(self, *_a: object, **_k: object) -> None:
        return None

    def label(self, **kw: object) -> None:
        text = str(kw.get("text", ""))
        self.labels.append(text)
        self.calls.append(("label", text))

    def prop(self, *_args: object, **_kw: object) -> None:
        # 第一个位置参数是 settings，第二个才是属性名（layout.prop(settings, "clone_address")）
        if len(_args) >= 2:
            self.props.append(str(_args[1]))
            self.calls.append(("prop", str(_args[1])))

    def operator(self, idname: str, **_kw: object) -> None:
        self.operators.append(str(idname))
        self.calls.append(("operator", str(idname)))

    def template_list(self, *args: object, **kw: object) -> None:
        """UIList（0.15.0 的选择器本体）：listtype_name / list_id / dataptr / propname / ..."""
        name = str(args[3]) if len(args) >= 4 else ""
        self.lists.append(
            (str(args[0]) if args else "", str(args[1]) if len(args) > 1 else "", name)
        )
        self.list_rows.append(int(kw.get("rows", 0) or 0))
        self.calls.append(("list", name))


def _panel_buttons(panel_class: type, settings: object) -> _LayoutRecorder:
    """**尺子**：把面板画一遍，收集它真的画出来的按钮名（同一个函数量所有面板）。"""
    layout = _LayoutRecorder()
    panel = panel_class.__new__(panel_class)
    panel.layout = layout  # type: ignore[attr-defined]
    context = types.SimpleNamespace(scene=types.SimpleNamespace(bamod=settings))
    panel.draw(context)  # type: ignore[attr-defined]
    return layout


class _PanelWithoutTheExecuteButton:
    """尺子的**正控**：一个没有那个按钮的面板 —— 同一把尺子必须量出「没有」。"""

    def draw(self, context: object) -> None:
        self.layout.operator("bamod.clone_plan")  # type: ignore[attr-defined]


def test_j5_clone_panel_draws_the_execute_button() -> None:
    """**J5**：bpy 桩上真画一遍，bamod.clone_execute 必须出现在画出来的按钮里。"""
    layout = _panel_buttons(PANELS.BamodClonePanel, _Settings())
    assert "bamod.clone_execute" in layout.operators, layout.operators
    assert {"bamod.clone_export", "bamod.clone_plan"} <= set(layout.operators)
    text = "\n".join(layout.labels)
    assert "不真写" not in text, "过期文案还画在面板上"
    assert "不幂等" in text


def test_j5_the_ruler_can_report_a_missing_button() -> None:
    """尺子正控：喂一个**已知没有**那个按钮的面板，同一把尺子必须量出「没有」。"""
    layout = _panel_buttons(_PanelWithoutTheExecuteButton, _Settings())
    assert layout.operators == ["bamod.clone_plan"]
    assert "bamod.clone_execute" not in layout.operators


# --------------------------------------------------------------------------- #
# ⑦ 克隆面板那一格：地址（可选）—— properties.py 的声明 ＋ 面板真的画出来
# --------------------------------------------------------------------------- #
def _addon_source(name: str) -> str:
    """blender_addon/<name> 的源码。

    ⚠ 走 **AST/文本**而不是 import：properties.py 顶层 import bpy，venv 里 import 不了
    （原先与 tests/test_mesh_write.py::_fields 同一口径；那个文件 2026-10-25 随 mesh 栈删除）。
    """
    return (ADDON / name).read_text(encoding="utf-8")


def _settings_fields() -> dict[str, ast.AnnAssign]:
    for node in ast.walk(ast.parse(_addon_source("properties.py"))):
        if isinstance(node, ast.ClassDef) and node.name == "BamodSettings":
            return {
                stmt.target.id: stmt
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError("properties.py 里没找到 BamodSettings")


def test_properties_declare_clone_address_with_an_empty_default() -> None:
    """**判据一行**：BamodSettings.clone_address 存在，且 **default = ""**。

    ⛔ 预填一个地址就是替用户造值（AGENTS §0.9 动作 1 / 本单「不造默认值」那条）。
    """
    fields = _settings_fields()
    assert "clone_address" in fields, f"没有这个字段：{sorted(fields)}"
    # ⚠ 这个仓的写法是「注解就是那个调用」：x: bpy.props.StringProperty(...)（没有 = 号）
    node = fields["clone_address"]
    call = next(
        (item for item in (node.annotation, node.value) if isinstance(item, ast.Call)), None
    )
    assert call is not None, "clone_address 不是 bpy.props.StringProperty(...) 形式"
    defaults = [kw.value for kw in call.keywords if kw.arg == "default"]
    assert len(defaults) == 1, "clone_address 没有 default"
    assert ast.literal_eval(defaults[0]) == "", "默认值不是空串 —— ⛔ 那就是替用户造了一个地址"


def test_clone_panel_draws_the_address_field() -> None:
    """**面板上真有一格**：把面板画一遍，收它调过的 prop 名（⛔ 不是文本搜索）。"""
    layout = _panel_buttons(PANELS.BamodClonePanel, _Settings())
    assert "clone_address" in layout.props, layout.props
    # 正控：同一把尺子在**别的**面板上量得到别的东西（不是恒真）
    assert "clone_name" in layout.props, layout.props


def test_the_field_ruler_can_report_a_missing_field() -> None:
    """**尺子正控**：喂一个没有那一格的面板 ⇒ 同一把尺子必须量出「没有」。"""
    layout = _panel_buttons(_PanelWithoutTheExecuteButton, _Settings())
    assert layout.props == [], layout.props


def test_every_bamod_operator_is_registered_in_the_addon_classes() -> None:
    """整类全扫：operators.py 里每个 Bamod* 算子都必须进 __init__.py 的 _CLASSES。

    面板画得出按钮 != 算子注册得上：draw() 里那句 operator("bamod.x") 只是**文本**，
    真机上点得动要的是 register() 注册过的那个类；漏登记 = 点下去 unknown operator
    （Lead 2026-10-11 在 task-3 的接缝里点出的那件事）。这一条就是它的机械尺子。

    本文件交付时 BamodCloneExecute **还没**进 _CLASSES（Lead 统一接线）——
    所以这一条会红到接线那一刻为止，红了就是「⑥ 面板那颗按钮还点不动」。
    """
    import ast

    defined = sorted(
        node.name
        for node in ast.walk(ast.parse((ADDON / "operators.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name.startswith("Bamod")
    )
    assert len(defined) >= 15, f"只扫到 {len(defined)} 个算子 —— 取法坏了：{defined}"
    registry = (ADDON / "__init__.py").read_text(encoding="utf-8")
    missing = [name for name in defined if f"operators.{name}" not in registry]
    assert not missing, f"这些算子没进 __init__.py 的 _CLASSES：{missing}"
    # 尺子正控：喂一个**已知没登记**的名字，同一把尺子必须报出来
    assert "operators.BamodDefinitelyNotRegistered" not in registry
    checked = [*defined, "BamodDefinitelyNotRegistered"]
    assert [name for name in checked if f"operators.{name}" not in registry] == [
        "BamodDefinitelyNotRegistered"
    ]


def test_clone_panel_lines_state_the_level_and_the_repeat() -> None:
    """面板纯函数：第一行永远是级别；报告为空时给一句人话（⛔ 不是空列表）。"""
    lines = PANELS.clone_panel_lines("", "Δ")
    assert lines[0] == "级别 Δ 新增对象 + 父节点 growth（Δ+）", lines[0]
    assert lines[1] == PANELS.CLONE_EMPTY_HINT
    report = ["[⑥ 克隆干跑] 包 case.bundle", "  m_PreloadTable 1 -> 5"]
    shown = PANELS.clone_panel_lines(json_dumps(report), "Δ")
    assert shown[1:] == report
    # 认不出的等级**原样报出来**，⛔ 不许悄悄当成 Δ
    assert PANELS.clone_panel_lines("", "L1")[0] == "级别 L1（不认识的等级，⛔ 不执行）"
    assert PANELS.clone_panel_lines("{不是 JSON", "Δ")[1].startswith(PANELS.CLONE_JSON_ERROR_HINT)


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# ⑦ 易用性五条（用户 2026-10-11：「你到底懂不懂关于软件、GUI 设计的易用性？」）
#
# 1 认识 > 回忆   —— 要用户给标识的格必须能**从列表选**，选单里是他在游戏里看到的名字
# 2 单面板闭环   —— ⑦ 从零到「干跑」不许切面板（用户原话：「我很讨厌绕」）
# 3 默认值/建议值 —— ⓐ 空筛选第一条＝判据单位 ⓑ 建议地址 ⓒ 撞名/空值当场给一句能照做的话
# 4 术语一致     —— 面板标题 / 方框标题对同一个东西只用**一个**名字（不许「⑦ 里面叫 ⑥」）
# 5 提示在操作点 —— 那几格的 tooltip 写清「填什么 / 从哪来 / 例子」（tooltip 正文在
#                   tests/test_unit_picker.py 里按运行时真值量，这里量「画不画得出来」）
# --------------------------------------------------------------------------- #
def _draw_clone_panel(**over: object) -> tuple[_LayoutRecorder, _Settings]:
    """把 ⑦ 面板真画一遍（同一把 _LayoutRecorder 尺子，与 J5 那几条同款）。"""
    layout = _LayoutRecorder()
    panel = PANELS.BamodClonePanel.__new__(PANELS.BamodClonePanel)
    panel.layout = layout  # type: ignore[attr-defined]
    settings = _Settings(**over)
    panel.draw(types.SimpleNamespace(scene=types.SimpleNamespace(bamod=settings)))  # type: ignore[attr-defined]
    return layout, settings


def _panel_numbers(labels: list[str]) -> set[str]:
    """面板文本里出现过的圈码（①..⑨）—— **只有这一处**取法，正负控共用它。"""
    circles = "①②③④⑤⑥⑦⑧⑨"
    return {label[0] for label in labels if label and label[0] in circles}


def test_clone_panel_is_a_single_panel_loop() -> None:
    """★ 判据 ①（0.15.0 的形态）：**真搜索框 ＋ UIList ＋ 三颗按钮**全在 ⑦ 这一格里。"""
    layout, _settings = _draw_clone_panel()
    assert ("prop", "unit_filter") in layout.calls, layout.calls
    assert ("list", "unit_rows") in layout.calls, layout.calls
    assert {"bamod.clone_export", "bamod.clone_plan", "bamod.clone_execute"} <= set(
        layout.operators
    ), layout.operators
    # 画的就是选择器那个集合，行高固定（⛔ 不撑爆面板）
    assert layout.lists == [(PANELS.UNIT_LIST_TYPE, "clone", "unit_rows")], layout.lists
    assert layout.list_rows == [PICKER.MAX_LIST_ROWS], layout.list_rows
    # ★ v0.17.2：标识符改成 Blender 要求的 `_UL_` 形态（不改的话控制台会打警告）。
    #   类名仍是 `BamodUnitList`，只是 `template_list` 认的是标识符。
    assert PANELS.UNIT_LIST_TYPE == "BAMOD_UL_units", PANELS.UNIT_LIST_TYPE
    # ⛔ EnumProperty 那条路整条没了：unit_key 不再是「画一格下拉」，而是列表选出来的值
    assert "unit_key" not in layout.props, "目标键还是当枚举格画的"


class _PanelWithoutTheUnitList:
    """尺子的**正控**：有搜索框、有三颗按钮，**就是没有 UIList**。"""

    def draw(self, context: object) -> None:
        box = self.layout.box()  # type: ignore[attr-defined]
        box.prop(context.scene.bamod, "unit_filter")
        row = box.row(align=True)
        row.operator("bamod.clone_export")
        row.operator("bamod.clone_plan")
        row.operator("bamod.clone_execute")


def test_the_single_panel_form_ruler_can_go_red() -> None:
    """★ 尺子正控：喂一个**少了 UIList** 的替身面板 ⇒ 同一把尺子必须报缺（⛔ 不恒绿）。"""
    layout = _panel_buttons(_PanelWithoutTheUnitList, _Settings())
    assert not layout.lists, layout.lists
    assert ("list", "unit_rows") not in layout.calls
    # 同一遍里别的东西量得到 ⇒ 这把尺子没瞎
    assert ("prop", "unit_filter") in layout.calls, layout.calls
    assert {"bamod.clone_export", "bamod.clone_plan", "bamod.clone_execute"} <= set(
        layout.operators
    ), layout.operators


def test_the_single_panel_ruler_can_go_red() -> None:
    """尺子正控：喂一个**没有**那几格的面板，同一把尺子必须量出「没有」。"""
    layout = _panel_buttons(_PanelWithoutTheExecuteButton, _Settings())
    assert "unit_filter" not in layout.props
    assert layout.props == []


def test_clone_panel_uses_one_number_for_the_panel_and_its_box() -> None:
    """★ 判据 4：面板体里的圈码**只许是自己那一个**（⑦），⛔ 不许一个 ⑦ 一个 ⑥。

    ## ★ 2026-10-11 晚 · 口径**有意更正**（不是放松）

    **旧口径**：`_panel_numbers(labels) == {"⑦"}` 且 `CLONE_PANEL_TITLE in labels` ——
    即要求面板体里**必须再写一遍**面板名（方框抬头）。
    **为什么错**：`CLONE_PANEL_TITLE == bl_label == "⑦ 增 · 新零件"` ⇒ 那条与 v0.16.0 的
    L2／U1「面板体里不许重复面板名」**不可能同时绿**，而它正是真机上「每块面板里都有一行
    和标题一模一样的字」的来源（用户截图里 ④⑤⑥ 各有一行）。
    **保留的意图**是「同一个编号不许在屏幕上写成两个」—— 现在写成**上界**：面板体里的圈码
    只能 ⊆ {⑦}。**正控** `test_the_numbering_ruler_can_go_red`（⑦＋⑥ ⇒ 必须报两个）**一条没改**。
    """
    layout, _settings = _draw_clone_panel()
    # ★ C-02（2026-10-11）：标题照设计稿 §2.2 —— ⑦ 增 · 新零件（旧「⑦ 克隆面板」）。
    assert PANELS.BamodClonePanel.bl_label == "⑦ 增 · 新零件"
    assert PANELS.CLONE_PANEL_TITLE.startswith("⑦"), PANELS.CLONE_PANEL_TITLE
    assert _panel_numbers(layout.labels) <= {"⑦"}, layout.labels
    # 面板名（＝ CLONE_PANEL_TITLE）⛔ 不许在面板体里再写一遍（U1）
    assert PANELS.CLONE_PANEL_TITLE not in layout.labels, layout.labels


class _PanelWithTwoNumbers:
    """判据 4 尺子的**正控**：一个「面板 ⑦ / 方框 ⑥」的假面板 —— 同一把尺子必须判红。"""

    def draw(self, context: object) -> None:
        self.layout.label(text="⑦ 克隆面板")  # type: ignore[attr-defined]
        self.layout.label(text="⑥ 克隆面板（新增对象）")  # type: ignore[attr-defined]


def test_the_numbering_ruler_can_go_red() -> None:
    """尺子正控：假面板上写着两个编号 ⇒ _panel_numbers 必须报出两个（不是恒等于 ⑦）。"""
    layout = _panel_buttons(_PanelWithTwoNumbers, _Settings())
    assert _panel_numbers(layout.labels) == {"⑥", "⑦"}, layout.labels


def test_clone_panel_shows_the_suggested_address_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 判据 3b（面板那一半）：建议地址 ＋ 一个**点得到**的「自动填建议地址」开关。"""
    monkeypatch.setattr(PANELS, "clone_suggestion_text", lambda _s: "建议值：Assets/x.prefab")
    layout, settings = _draw_clone_panel()
    assert "clone_use_suggested_address" in layout.props, layout.props
    assert "建议值：Assets/x.prefab" in layout.labels, layout.labels
    # 默认必须是**关**的：那一格决定要不要往包里加地址条目（⛔ 不替用户打开）
    assert _Settings().clone_use_suggested_address is False
    assert settings.clone_address == ""


def test_clone_panel_prints_doable_sentences_for_empty_values() -> None:
    """★ 判据 3c：空值**当场**给一句能照做的话（不是等算子抛 raw 异常）。"""
    layout, _settings = _draw_clone_panel(name="", parent="")
    assert PANELS.CLONE_HINT_EMPTY_NAME in layout.labels, layout.labels
    assert PANELS.CLONE_HINT_NO_PARENT in layout.labels, layout.labels
    assert PANELS.CLONE_HINT_EMPTY_NAME.startswith("「新零件名」不能空")
    assert "例：" in PANELS.CLONE_HINT_EMPTY_NAME, "提示里必须带一个能照抄的例子"


def test_clone_panel_says_nothing_when_the_values_are_fine() -> None:
    """负控：值都齐了 ⇒ 那两句提示**一个都不许出现**（⛔ 不是恒挂着的唠叨）。"""
    layout, _settings = _draw_clone_panel()
    assert PANELS.CLONE_HINT_EMPTY_NAME not in layout.labels
    assert PANELS.CLONE_HINT_NO_PARENT not in layout.labels


def test_clone_panel_reports_a_colliding_address_on_the_spot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 判据 3c（撞名那一半）：地址已在包里 ⇒ 面板当场给一句能照做的话。"""
    monkeypatch.setattr(PANELS, "_address_taken", lambda _s: True)
    layout, _settings = _draw_clone_panel(address=ADDR)
    expected = PANELS.CLONE_HINT_ADDRESS_TAKEN.format(address=ADDR)
    assert expected in layout.labels, layout.labels
    assert "换个名字" in expected


def test_the_collision_hint_is_off_when_the_address_is_free() -> None:
    """负控：地址不撞（不给 address_taken）⇒ 撞名那句不许出现。"""
    layout, _settings = _draw_clone_panel(address=ADDR)
    assert PANELS.CLONE_HINT_ADDRESS_TAKEN.format(address=ADDR) not in layout.labels


def test_clone_panel_puts_the_search_box_above_the_list() -> None:
    """★ 判据 ① 的顺序：搜索框必须在**列表上面**（用户在这一格里从打字开始）。"""
    layout, _settings = _draw_clone_panel()
    order = [name for kind, name in layout.calls if kind in ("prop", "list")]
    assert "unit_filter" in order and "unit_rows" in order, order
    assert order.index("unit_filter") < order.index("unit_rows"), order


# --------------------------------------------------------------------------- #
# 0.15.0 的三条新判据：高级折叠（⑤）· 一句人话摘要 · 文案里没有「请先…再…」（⑥）
# --------------------------------------------------------------------------- #
#: 判据 ⑥ 的尺子：哪些行是「请先去做 X 再回来 / 切回另一个面板」这类话（空列表 = 干净）。
CROSS_PANEL_WORDS: tuple[str, ...] = ("请先", "再回来", "切回", "切去")


def cross_panel_lines(labels: list[str]) -> list[str]:
    """画出来的文案里，**跨面板支使**用户的那几行（⛔ 用户原话：「我很讨厌绕」）。"""
    return [line for line in labels if any(word in line for word in CROSS_PANEL_WORDS)]


def test_the_cross_panel_ruler_can_go_red() -> None:
    """★ 判据 ⑥ 的尺子正控：喂两句**已知违规**的文案 ⇒ 同一把尺子必须报出来。"""
    bad = ["请先在 ① 资源面板里筛好单位再回来执行", "切回 ① 面板选目标键"]
    assert cross_panel_lines(bad) == bad
    assert cross_panel_lines(["在搜索框里打 t-72，点它那一行"]) == []
    assert cross_panel_lines([PANELS.CLONE_HINT_EMPTY_NAME]) == []
    assert cross_panel_lines([PANELS.CLONE_HINT_NO_PARENT]) == [], PANELS.CLONE_HINT_NO_PARENT


def test_clone_panel_has_no_cross_panel_nagging() -> None:
    """★ 判据 ⑥：⑦ 画出来的文案里一个「请先…再…／切回…」都没有。"""
    layout, _settings = _draw_clone_panel(name="", parent="")
    assert cross_panel_lines(layout.labels) == [], layout.labels


def test_the_three_path_ids_live_in_a_collapsed_advanced_sub_panel() -> None:
    """★ 判据 ⑤：三个 pathID 收进**默认折叠**的「高级」子面板，折叠外只见一句 ✓/✗ 摘要。"""
    layout, _settings = _draw_clone_panel()
    for field in ("clone_parent_transform", "clone_mesh", "clone_material"):
        assert field not in layout.props, f"{field} 还摆在主面板上：{layout.props}"
    # 「收进折叠」用的是 Blender 的正规件（子面板），不是自己画的假折叠
    assert PANELS.BamodCloneAdvancedPanel.bl_parent_id == "BAMOD_PT_clone"
    assert "DEFAULT_CLOSED" in PANELS.BamodCloneAdvancedPanel.bl_options
    advanced = _panel_buttons(PANELS.BamodCloneAdvancedPanel, _Settings())
    # ★ C-09（Lead 2026-10-11 裁决）：三个裸 pathID 的**可编辑格删掉** ⇒ 折叠里只剩
    # **1 格临时入口**（`clone_parent_transform`；C-19 候选下拉落地时换成下拉并删掉它），
    # 另外那两格一个都不许画（J1：面板里不许出现裸 pathID 格）。
    # ★ C-19（Lead 2026-10-11）：临时入口已换成**候选下拉** `clone_pick`（C-19 第 3 格）
    assert advanced.props == ["clone_pick"], advanced.props
    # 候选由**只读**算子「列出候选」填（⛔ 它不写盘）
    assert "bamod.clone_parent_candidates" in advanced.operators, advanced.operators
    for gone in ("clone_parent_transform", "clone_mesh", "clone_material", "clone_path_ids"):
        assert gone not in advanced.props, advanced.props
    # 折叠外的摘要行照旧（人话，不是 19 位数字）
    assert PANELS.clone_ids_summary(_Settings()) in advanced.labels, advanced.labels
    # C-19 说法住在 properties.py 的 description 上（layout.prop 覆盖不了 tooltip）
    props_src = (ADDON / "properties.py").read_text(encoding="utf-8")
    assert "TEMP_ENTRY_TOOLTIP" in props_src and "C-19" in props_src
    # 折叠外那一行：三个都填好了就三个 ✓（人话，不是 19 位数字）
    full = PANELS.clone_ids_summary(_Settings())
    assert full == "父 Transform ✓ · Mesh ✓ · Material ✓", full
    empty = PANELS.clone_ids_summary(_Settings(parent="", mesh="", material=""))
    assert empty.count("✗") == 3 and "还没填" in empty, empty


def test_the_advanced_fold_ruler_can_go_red() -> None:
    """★ 尺子正控：一个把三格**摆回主面板**的替身 ⇒ 同一把 props 尺子必须量得到。"""
    class _PanelWithRawIds:
        def draw(self, context: object) -> None:
            box = self.layout.box()  # type: ignore[attr-defined]
            for field in ("clone_parent_transform", "clone_mesh", "clone_material"):
                box.prop(context.scene.bamod, field)

    layout = _panel_buttons(_PanelWithRawIds, _Settings())
    assert "clone_mesh" in layout.props, layout.props
