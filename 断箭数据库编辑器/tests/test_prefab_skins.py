r"""皮肤表 `UnitPrefabRoot.Skins.Data` 的机械判据（J1~J4）＋ 合成件夹具。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_prefab_skins.py -q -n 0`
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 合成件正控：9 个对象逐个逐字节往返、对象表升序、Skins 字段链与真件类型树一致 | 全过 |
| B | 纯函数三动词（照抄参照条 / 追加 / 删除 / 改材质指向）＋ 形状坏就抛 | 全过 |
| C | **J1** 干跑 vs 真写：`criterion_lines()` 逐行相等；干跑不改一个字节 | 相等 |
| D | **J2** 读数：`Skins.Data` 条数 **N -> N+1**（删则 N-1）；新 Id 不撞既有条 | 全对 |
| E | **J3** 反向 Δ：`PatchStore.restore()` 之后整文件 sha256 == 改前 | 相等 |
| F | 写后复核 `verify_plan`；**正控**：写完再改一刀 ⇒ 它必须红 | 能绿能红 |
| G | **J4** 守卫：Id 撞 / 参照条缺 / 材质不在参照条 / 渲染器不在 prefab / 包外 / 空改动 | 全红 |

## 夹具从哪来（⛔ 不开 units 包 3.38 GB、⛔ 不碰游戏目录）

`_work/w10_ref/units_meta.bin`（真件 units 的**元数据区**：48 B 头 ＋ 50 项类型表 ＋
162,010 条对象表 ＋ 尾巴）＋ `_work/w10_ref/src_objects/*.bin`（真件对象字节）。
把类型表与尾巴**原样**搬进一个新 SerializedFile，对象表只留 9 条（**按 pathID 升序** ——
writer 的升序尺子会挡）：

    pathID=1    AssetBundle（142）       预载表 3 条
    pathID=100  GameObject（1）          『RU_T72B_Base』
    pathID=200  **UnitPrefabRoot**（114，type entry **24**）  Skins.Data 2 条 + Renderers 2 个
    pathID=300  MonoScript（115）        m_ClassName="UnitPrefabRoot"
    pathID=400  MeshRenderer（23）       m_Materials=[500]
    pathID=401  MeshRenderer（23）       m_Materials=[500, 501]
    pathID=500/501/502  Material（21）   『RU_T72B 1/2/3』

⚠ 这个 MonoBehaviour 的**载荷是我们自己按真类型树序列化的**（真件那个对象不在 w10_ref 里），
所以判据只能落在「同一棵树读得回、写得回、字段名与配对结构与真件类型树一致」上 ——
`test_a_shapes_match_the_real_type_tree` 就是拿类型表**逐字**核这件事。
⚠ 合成件约 58 KB：够跑通整条 Δ 写回链（命中块 4 个），又不必付 3.38 GB 的代价。
"""

from __future__ import annotations

import functools
import hashlib
import shutil
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import prefab_skins as ps
from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
W10_REF = WORK / "w10_ref"
UNITS_META = W10_REF / "units_meta.bin"
REAL_RENDERER = W10_REF / "src_objects" / "9194953101066830632_23.bin"
REAL_MATERIAL = W10_REF / "src_objects" / "7952762356820983200_21.bin"

#: 合成件的 pathID 表（与夹具构造函数共享）。
AB_PID = 1
PREFAB_GO = 100
UROOT = 200
MONOSCRIPT = 300
RENDERER_A = 400
RENDERER_B = 401
MAT_A = 500
MAT_B = 501
MAT_C = 502
SKIN_A = 2232
SKIN_B = 2233
NEW_SKIN = 2234
#: 真件类型表里 UnitPrefabRoot 那一项的 type entry 下标（复算见本文件 A 段）。
UROOT_TYPE_INDEX = 24
#: 合成件的基线读数。
SYNTH_OBJECTS = 9
SYNTH_SKINS = 2
SYNTH_MATERIALS = 3
SYNTH_SKINS_MONO = {SKIN_A, SKIN_B}


# --------------------------------------------------------------------------- #
# 合成件（真类型表 + 真对象字节；⛔ 不读游戏盘）
# --------------------------------------------------------------------------- #
def _common() -> object:
    """内嵌类型树的公共串表（延迟取：没有 UnityPy 时模块仍能被收集）。"""
    return cl.unitypy_common_strings()


def _strip_meta(meta: bytes) -> tuple[bytes, bytes, bytes]:
    """真件元数据区 -> (48 B 头, 版本串..类型表末尾, 对象表之后那截尾巴)。

    走的是**产品自己的**解析器（`clone._Cur` / `clone._read_type_tree_blob`），
    只是把"类型表在哪结束"这一步的游标位置取出来。
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
    """照类型树造一棵**空**字段树（我们自己拼载荷时用）。"""
    node = tree.node
    if node.type in cl._SCALARS:
        return 0
    if node.type == "string":
        return ""
    if node.type == "TypelessData":
        return b""
    children = tree.children
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


def skin_rows() -> list[dict]:
    """两条皮肤：A 一条材质对两个渲染器；B 两条材质各对一个渲染器。"""
    return [
        {
            "Id": SKIN_A,
            "Data": {
                "_keyData": [cl.pptr(MAT_A)],
                "_valueData": [{"Renderers": [cl.pptr(RENDERER_A), cl.pptr(RENDERER_B)]}],
            },
        },
        {
            "Id": SKIN_B,
            "Data": {
                "_keyData": [cl.pptr(MAT_A), cl.pptr(MAT_B)],
                "_valueData": [
                    {"Renderers": [cl.pptr(RENDERER_A)]},
                    {"Renderers": [cl.pptr(RENDERER_B)]},
                ],
            },
        },
    ]


def uroot_nodes() -> tuple:
    """UnitPrefabRoot 那一项的类型树节点（type entry 24）。"""
    schema = cl.read_type_table(UNITS_META.read_bytes(), common_strings=_common())
    entry = next(item for item in schema.entries if item.index == UROOT_TYPE_INDEX)
    return entry.nodes


@functools.lru_cache(maxsize=1)
def _synth_blob() -> bytes:
    """合成包的字节（整个用例模块只造一次）。"""
    meta = UNITS_META.read_bytes()
    head, body0, tail = _strip_meta(meta)
    schema = cl.read_type_table(meta, common_strings=_common())
    by_class = {entry.class_id: entry.index for entry in schema.entries}

    def nodes_of(class_id: int):
        return next(item for item in schema.entries if item.class_id == class_id).nodes

    def make(class_id: int, fill) -> bytes:
        nodes = nodes_of(class_id)
        tree = _default_tree(cl._build_tree(nodes))
        fill(tree)
        payload = cl.write_object(tree, nodes)
        if cl.read_object(payload, nodes) != tree:
            raise AssertionError(f"class {class_id} 的载荷自己都往返不了 —— 夹具坏了")
        return payload

    ab = make(
        cl.ASSET_BUNDLE_CLASS_ID,
        lambda t: t.update(
            {
                "m_Name": "synth_skins_assets_all_00000000000000000000000000000000.bundle",
                "m_AssetBundleName": "synth_skins",
                "m_PreloadTable": [cl.pptr(UROOT), cl.pptr(RENDERER_A), cl.pptr(RENDERER_B)],
                "m_Container": [],
            }
        ),
    )
    go = make(
        cl.CLASS_GAME_OBJECT,
        lambda t: t.update(
            {"m_Name": "RU_T72B_Base", "m_Component": [{"component": cl.pptr(UROOT)}]}
        ),
    )
    nodes = uroot_nodes()
    uroot_tree = _default_tree(cl._build_tree(nodes))
    uroot_tree.update(
        {
            "m_GameObject": cl.pptr(PREFAB_GO),
            "m_Script": cl.pptr(MONOSCRIPT),
            "m_Name": "",
            "PrefabRoot": cl.pptr(0),
            "PrefabBody": cl.pptr(0),
            "SpawnTimeObjects": [],
            "Renderers": [cl.pptr(RENDERER_A), cl.pptr(RENDERER_B)],
            "Skins": {"Data": skin_rows()},
        }
    )
    uroot = cl.write_object(uroot_tree, nodes)
    if cl.read_object(uroot, nodes) != uroot_tree:
        raise AssertionError("UnitPrefabRoot 载荷往返不了 —— 夹具坏了")
    mono = make(
        115,
        lambda t: t.update(
            {
                "m_Name": "UnitPrefabRoot",
                "m_ClassName": "UnitPrefabRoot",
                "m_Namespace": "BrokenArrow.Client.Ecs.Customization.Skins",
                "m_AssemblyName": "BrokenArrow",
            }
        ),
    )
    renderer_nodes = nodes_of(23)
    template = REAL_RENDERER.read_bytes()
    if cl.roundtrip(template, renderer_nodes) != template:
        raise AssertionError("真件 MeshRenderer 模板往返不了")
    renderers = []
    for materials in ([MAT_A], [MAT_A, MAT_B]):
        tree = cl.read_object(template, renderer_nodes)
        tree["m_GameObject"] = cl.pptr(PREFAB_GO)
        tree["m_Materials"] = [cl.pptr(m) for m in materials]
        renderers.append(cl.write_object(tree, renderer_nodes))
    material_nodes = nodes_of(21)
    template = REAL_MATERIAL.read_bytes()
    if cl.roundtrip(template, material_nodes) != template:
        raise AssertionError("真件 Material 模板往返不了")
    materials = []
    for name in ("RU_T72B 1", "RU_T72B 2", "RU_T72B 3"):
        tree = cl.read_object(template, material_nodes)
        tree["m_Name"] = name
        materials.append(cl.write_object(tree, material_nodes))

    # (pathID, **type_id**, 载荷) —— type_id 就是对象表里那一列，⛔ 不是 classID。
    wanted = [
        (AB_PID, by_class[cl.ASSET_BUNDLE_CLASS_ID], ab),
        (PREFAB_GO, by_class[cl.CLASS_GAME_OBJECT], go),
        (UROOT, UROOT_TYPE_INDEX, uroot),
        (MONOSCRIPT, by_class[115], mono),
        (RENDERER_A, by_class[23], renderers[0]),
        (RENDERER_B, by_class[23], renderers[1]),
        (MAT_A, by_class[21], materials[0]),
        (MAT_B, by_class[21], materials[1]),
        (MAT_C, by_class[21], materials[2]),
    ]
    objs = sorted(wanted, key=lambda row: row[0])
    return _build_container(_build_sf(head, body0, tail, objs), bytes(200_007), "synthskins")


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件抽件产物）")
    path = tmp_path_factory.mktemp("skins") / "synth.bundle"
    path.write_bytes(_synth_blob())
    return path


@pytest.fixture
def case(synth: Path, tmp_path: Path) -> Path:
    """每个用例一份自己的副本 —— 真写回只写它。"""
    path = tmp_path / "case.bundle"
    shutil.copy2(synth, path)
    return path


# --------------------------------------------------------------------------- #
# 共用小工具（全走产品入口）
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _store(project_root: Path, game_root: Path) -> reverse_delta.PatchStore:
    return reverse_delta.PatchStore(
        reverse_delta.patch_store_root(project_root), game_root=game_root
    )


def _restore(project_root: Path, case: Path):
    return _store(project_root, case.parent).restore()


def _tree_of(bundle: Path, path_id: int) -> dict:
    """独立读回：`ps.read_package_object` 解出来的字段树。"""
    return dict(ps.read_package_object(bundle, path_id).tree)


# --------------------------------------------------------------------------- #
# A · 合成件正控
# --------------------------------------------------------------------------- #
def test_a_synth_roundtrips_every_object_and_its_table_is_sorted(synth: Path) -> None:
    """正控：9 个对象**逐个**逐字节往返；对象表严格升序（writer 的升序尺子先过）。"""
    with ufs.UfsReader(synth) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = bytes(reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = cl.read_type_table(meta, common_strings=_common())
        ids = [row[1] for row in info.objects]
        assert info.object_count == SYNTH_OBJECTS, info.object_count
        assert ids == sorted(set(ids)), f"夹具的对象表不是严格升序：{ids}"
        for _index, path_id, _stored, _size, type_id in info.objects:
            # ⚠ 认类型树要按**对象表里的 typeID**（classID=114 在真件里有 22 项 ——
            #   schema.nodes(114) 会直接抛），这也是产品侧 read_package_object 的口径。
            entry = next(item for item in schema.entries if item.index == type_id)
            payload = cl.read_object_payload(reader, node, info, path_id)
            assert cl.roundtrip(payload, entry.nodes) == payload, f"pathID={path_id}"


def test_a_shapes_match_the_real_type_tree() -> None:
    """正控：`Skins.Data` 那条字段链**逐字**与真件类型树一致（不是我们编的）。"""
    names = [node.name for node in uroot_nodes()]
    assert "Skins" in names, "type entry 24 里没有 Skins —— 类型表换了 / 下标变了"
    # 只看 **Skins 那一棵子树**（它前面还有一个同名的 UnitPrefabRoot.Renderers，
    # 那不是皮肤表里的东西 —— 第一版就把它误收进来了）。
    wanted = {"Skins", "Data", "Id", "_keyData", "_valueData", "Renderers"}
    chain = [name for name in names[names.index("Skins") :] if name in wanted]
    assert chain == ["Skins", "Data", "Id", "Data", "_keyData", "_valueData", "Renderers"], chain
    # 真件类型树里那两个字段是 PPtr<Material>[] / PPtr<Renderer>[]（不是裸 int）
    types = {node.name: node.type for node in uroot_nodes()}
    assert types["_keyData"] == "vector"
    assert types["Renderers"] == "vector"


def test_a_baseline_readings(synth: Path) -> None:
    """基线读数：9 个对象 / Skins.Data 2 条 / 3 颗材质 / 两个渲染器各 1、2 个槽。"""
    assert ps.read_skins(synth, UROOT) == (
        ps.SkinSlot(SKIN_A, (MAT_A,), ((RENDERER_A, RENDERER_B),)),
        ps.SkinSlot(SKIN_B, (MAT_A, MAT_B), ((RENDERER_A,), (RENDERER_B,))),
    )
    slots = ps.read_package_object(synth, UROOT).tree["Skins"]["Data"]
    assert len(slots) == SYNTH_SKINS


# --------------------------------------------------------------------------- #
# B · 纯函数：读 / 三个动词 / 照抄参照条
# --------------------------------------------------------------------------- #
def _tree() -> dict:
    return {"Skins": {"Data": skin_rows()}}


def test_b_skin_slots_reads_pairs_in_order() -> None:
    slots = ps.skin_slots(_tree())
    assert [slot.skin_id for slot in slots] == [SKIN_A, SKIN_B]
    assert slots[0].pairs() == ((MAT_A, (RENDERER_A, RENDERER_B)),)
    assert slots[1].pairs() == ((MAT_A, (RENDERER_A,)), (MAT_B, (RENDERER_B,)))


@pytest.mark.parametrize(
    "broken,needle",
    [
        ({}, "不是映射"),
        ({"Skins": {"Data": "x"}}, "不是序列"),
        ({"Skins": {"Data": [{"Id": "2232", "Data": {}}]}}, "不是整数"),
        (
            {
                "Skins": {
                    "Data": [
                        {
                            "Id": 1,
                            "Data": {
                                "_keyData": [{"m_FileID": 0, "m_PathID": 5}],
                                "_valueData": [],
                            },
                        }
                    ]
                }
            },
            "平行数组长度不等",
        ),
    ],
    ids=["no-skins", "data-not-list", "id-not-int", "uneven-pairs"],
)
def test_b_skin_slots_negative_controls(broken: dict, needle: str) -> None:
    """负控：形状坏掉就抛（⛔ 不许把"读法坏了"静默成"这张表是空的"）。"""
    with pytest.raises(AssetError) as excinfo:
        ps.skin_slots(broken)
    assert needle in str(excinfo.value), str(excinfo.value)


def test_b_clone_skin_copies_the_reference_row_verbatim() -> None:
    """照抄参照条：只改 Id / 材质指向，Renderers 与配对结构**一字不差**。"""
    reference = ps.skin_slots(_tree())[1]
    same = ps.clone_skin(reference, new_id=NEW_SKIN)
    assert same.renderers == reference.renderers
    assert same.materials == reference.materials
    assert same.skin_id == NEW_SKIN
    retargeted = ps.clone_skin(reference, new_id=NEW_SKIN, materials={MAT_B: MAT_C})
    assert retargeted.materials == (MAT_A, MAT_C)
    assert retargeted.renderers == reference.renderers


def test_b_three_verbs_change_only_what_they_say() -> None:
    tree = _tree()
    added = ps.add_skin(tree, skin_id=NEW_SKIN, reference_id=SKIN_A, materials={MAT_A: MAT_C})
    assert [slot.skin_id for slot in ps.skin_slots(added)] == [SKIN_A, SKIN_B, NEW_SKIN]
    assert ps.skin_slots(added)[0] == ps.skin_slots(tree)[0], "老行被改了"
    removed = ps.remove_skin(added, skin_id=SKIN_B)
    assert [slot.skin_id for slot in ps.skin_slots(removed)] == [SKIN_A, NEW_SKIN]
    updated = ps.update_skin(tree, skin_id=SKIN_A, materials={MAT_A: MAT_C})
    assert ps.skin_slots(updated)[0].materials == (MAT_C,)
    assert ps.skin_slots(updated)[0].renderers == ((RENDERER_A, RENDERER_B),)
    assert ps.skin_slots(tree)[0].materials == (MAT_A,), "入参树被就地改了"


def test_b_set_skins_reuses_untouched_rows() -> None:
    """没动过的行**原样照抄**（新条按字段树拼）—— 判据是行对象不是同一个、但内容相同。"""
    tree = _tree()
    added = ps.add_skin(tree, skin_id=NEW_SKIN, reference_id=SKIN_A)
    assert added["Skins"]["Data"][0] == tree["Skins"]["Data"][0]
    assert added["Skins"]["Data"][0] is not tree["Skins"]["Data"][0]
    assert added["Skins"]["Data"][2] == ps._row_of(ps.skin_slots(added)[2])

# --------------------------------------------------------------------------- #
# C · J1 干跑 vs 真写
# --------------------------------------------------------------------------- #
def test_c_j1_dry_run_and_real_write_agree_line_by_line(case: Path, tmp_path: Path) -> None:
    """同一次改动：`plan_delta` 的读数、干跑的读数、真写的读数**逐行相等**。"""
    project = tmp_path / "proj"
    project.mkdir()
    before = _sha256(case)
    plan = ps.plan_add(
        case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A, materials={MAT_A: MAT_C}
    )
    assert plan.count_line() == "Skins.Data 条数 2 -> 3（新增，Id [2234]）"
    dry = ps.install(plan, project_root=project, dry_run=True)
    assert dry.dry_run is True
    assert dry.plan.criterion_lines() == plan.writeback.criterion_lines()
    assert _sha256(case) == before, "干跑写了盘"
    real = ps.install(plan, project_root=project)
    assert real.dry_run is False
    assert real.plan.criterion_lines() == dry.plan.criterion_lines()
    assert _sha256(case) != before
    text = "\n".join(plan.criterion_lines())
    assert "Skins.Data 条数 2 -> 3" in text
    assert "ObjectGrowth" in text and "catalog：未同步（离线件）" in text


def test_c_plan_readings_are_real_numbers(case: Path) -> None:
    """读数不是算出来的：`size_before` 从 writer 的计划行里读真值，新长度是字节数。"""
    plan = ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A)
    assert plan.level == "ObjectGrowth"
    assert plan.size_before() == len(ps.read_package_object(case, UROOT).payload)
    assert len(plan.change.payload) > plan.size_before()
    assert plan.writeback.rows[0].path_id == UROOT
    assert plan.writeback.rows[0].old_bytes == plan.size_before()


# --------------------------------------------------------------------------- #
# D · J2 读数（条数 / 长度 / 新旧值）
# --------------------------------------------------------------------------- #
def test_d_j2_add_grows_the_table_by_one(case: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ids_before = [slot.skin_id for slot in ps.read_skins(case, UROOT)]
    assert ids_before == [SKIN_A, SKIN_B]
    assert NEW_SKIN not in ids_before, "夹具的新 Id 与该表既有条撞了 —— 换一个号"
    plan = ps.plan_add(
        case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A, materials={MAT_A: MAT_C}
    )
    assert (len(plan.slots_before), len(plan.slots_after)) == (2, 3)
    ps.install(plan, project_root=project)
    after = ps.read_skins(case, UROOT)
    assert [slot.skin_id for slot in after] == [SKIN_A, SKIN_B, NEW_SKIN]
    assert after[2].materials == (MAT_C,), "材质指向没改成新的"
    assert after[2].renderers == ((RENDERER_A, RENDERER_B),), "Renderers 没照抄参照条"


def test_d_j2_remove_shrinks_the_table_by_one(case: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    plan = ps.plan_remove(case, UROOT, skin_id=SKIN_B)
    assert (len(plan.slots_before), len(plan.slots_after)) == (2, 1)
    assert plan.touched == (SKIN_B,)
    ps.install(plan, project_root=project)
    assert [slot.skin_id for slot in ps.read_skins(case, UROOT)] == [SKIN_A]


def test_d_j2_update_is_the_equal_length_lane(case: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    plan = ps.plan_update(case, UROOT, skin_id=SKIN_A, materials={MAT_A: MAT_B})
    assert plan.level == "ObjectChange"
    assert len(plan.change.payload) == plan.size_before(), "改一条 PPtr 不该改长度"
    assert plan.changed_bytes == 1, f"500 -> 501 只该差一个字节，实测 {plan.changed_spans}"
    assert (len(plan.slots_before), len(plan.slots_after)) == (2, 2)
    ps.install(plan, project_root=project)
    assert ps.read_skins(case, UROOT)[0].materials == (MAT_B,)


# --------------------------------------------------------------------------- #
# E · J3 反向 Δ ⇒ 整文件 sha256 == 改前
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("verb", ["add", "remove", "update"])
def test_e_j3_reverse_delta_restores_the_whole_file(case: Path, tmp_path: Path, verb: str) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    before = _sha256(case)
    plan = {
        "add": lambda: ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A),
        "remove": lambda: ps.plan_remove(case, UROOT, skin_id=SKIN_B),
        "update": lambda: ps.plan_update(case, UROOT, skin_id=SKIN_A, materials={MAT_A: MAT_B}),
    }[verb]()
    report = ps.install(plan, project_root=project)
    assert report.patch is not None, "没留反向 Δ 补丁"
    assert _sha256(case) != before
    restore = _restore(project, case)
    assert restore.ok, restore.lines()
    assert _sha256(case) == before, "还原之后整文件 sha256 不等于改前"


# --------------------------------------------------------------------------- #
# F · 写后复核（＋ 尺子的正控：它必须能红）
# --------------------------------------------------------------------------- #
def test_f_verify_plan_agrees_with_what_was_written(case: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    plan = ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A)
    ps.install(plan, project_root=project)
    lines = ps.verify_plan(plan)
    assert lines[0] == "Skins.Data 条数 2 -> 3（读回 3，逐条与期望相同）"
    assert len(lines) == 4, lines


def test_f_verify_plan_goes_red_when_the_file_moved_on(case: Path, tmp_path: Path) -> None:
    """**正控**：写完再改一刀 ⇒ 旧计划复核必须红（不能红的尺子不算尺子）。"""
    project = tmp_path / "proj"
    project.mkdir()
    plan = ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A)
    ps.install(plan, project_root=project)
    ps.verify_plan(plan)  # 绿
    second = ps.plan_remove(case, UROOT, skin_id=NEW_SKIN)
    ps.install(second, project_root=project)
    with pytest.raises(AssetError) as excinfo:
        ps.verify_plan(plan)
    assert "写后读回" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# G · J4 守卫 fail-closed（每条一个用例）
# --------------------------------------------------------------------------- #
def test_g1_a_duplicate_id_is_refused(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        ps.plan_add(case, UROOT, skin_id=SKIN_A, reference_id=SKIN_B)
    text = str(excinfo.value)
    assert "已经在这张表里了" in text and "2232" in text


def test_g2_a_missing_reference_row_is_refused(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=9999)
    text = str(excinfo.value)
    assert "没有 Id=9999" in text and "[2232, 2233]" in text


def test_g3_a_material_outside_the_reference_row_is_refused(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A, materials={MAT_C: MAT_B})
    assert "参照条 Id=2232 里没有" in str(excinfo.value)


def test_g3_a_renderer_outside_the_prefab_list_is_refused(case: Path) -> None:
    """渲染器不在 prefab 的 `Renderers` 名单里 —— **计划级**守卫（走的是同一条函数）。"""
    obj = ps.read_package_object(case, UROOT)
    stray = dict(obj.tree)
    stray["Skins"] = {
        "Data": [
            {
                "Id": SKIN_A,
                "Data": {
                    "_keyData": [cl.pptr(MAT_A)],
                    "_valueData": [{"Renderers": [cl.pptr(MAT_C)]}],
                },
            }
        ]
    }
    with pytest.raises(AssetError) as excinfo:
        ps._guarded(stray, obj)
    assert "不在 prefab 的 Renderers 名单" in str(excinfo.value)


def test_g3_a_pptr_outside_the_package_is_refused() -> None:
    slot = ps.SkinSlot(SKIN_A, (MAT_A,), ((RENDERER_A,),))
    with pytest.raises(AssetError) as excinfo:
        ps.guard_targets((slot,), package_ids=(MAT_A,))
    assert "指向包外" in str(excinfo.value)
    assert ps.guard_targets((slot,), package_ids=(MAT_A, RENDERER_A))  # 都在包里 ⇒ 绿


def test_g3_an_empty_change_is_refused(case: Path) -> None:
    """空改动 ⇒ 抛。

    ⚠ **变异自检读数（2026-10-11）**：把 @@_make_plan@@ 里那道 **plan 级**空改动闸换成
    @@if False@@，这条用例**还是绿的** —— 因为纯函数 @@update_skin@@ 先拦住了。
    两层守卫是**故意**的（plan 级那道防的是"树变了、字节却没变"这种还没见过的形状），
    但如实记：这条红灯量到的是**纯函数那一层**，plan 级那一层在本夹具下造不出独立红灯。
    """
    with pytest.raises(AssetError) as excinfo:
        ps.plan_update(case, UROOT, skin_id=SKIN_A, materials={MAT_A: MAT_A})
    assert "空改动" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# H · 结构判据：产品 API = 纯函数 ＋ **一个** install 入口
# --------------------------------------------------------------------------- #
def test_h_write_side_has_exactly_one_entrypoint() -> None:
    """写侧只许有一处（`run_write`）：⛔ 别在模块里另开一条写盘的路。"""
    import ast

    source = (REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel" / "prefab_skins.py").read_text(
        "utf-8"
    )
    tree = ast.parse(source)
    write_calls: list[str] = []
    owners: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") in {
                "write_delta",
                "write_objects",
            }:
                write_calls.append(inner.func.attr)
                owners.append(node.name)
    assert sorted(write_calls) == ["write_delta", "write_objects"], write_calls
    assert set(owners) == {"run_write"}, owners
    funcs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {"install", "run_write"} <= funcs

