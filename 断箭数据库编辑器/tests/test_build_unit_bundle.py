r"""W10 Lead · 从零构建**单位模型包**的判据（工单 §3 的 N1~N6 端到端）。

**判据一行**：pytest tests/test_build_unit_bundle.py -q -n 0 -> 通过数 0 -> N，
且其中至少 4 条是**负控**（喂坏样本必须变红才算尺子，AGENTS §0.9 动作 2）。

## 本文件里各段量什么

| 段 | 判据 | 怎么量 |
|---|---|---|
| A | build_object_bundle 的通用形（W10 新增） | 真小包（ammoicons）的类型树当 schema，
造 2 个 Texture2D + 容器 2 条，两条读路重开对拍 |
| A' | preload 窗口 | 同路径两条条目 -> preloadIndex/preloadSize 都是 (0, 2)（与真件同形） |
| A'' | 负控 4 条 | 容器指向不存在的 pathID / pathID 重复 / 把 AssetBundle 塞进 objects /
classID 不在源包里 —— **四条都必须抛** |
| B | 同 classID 多棵树要点名 script_index | 真包 units 的元数据区里 classID=114 有 22 项；
不点名必须抛、点名才给 |
| C | N1~N6 端到端 | 由 mesh_build / material_build / prefab_build 三块拼出真单位模型包
（各自模块的用例里已有细判据，这里只做拼装） |

## 为什么 A 段用 ammoicons 而不是 units

units 是 3.38 GB（峰值约 10 GB，要走 _work/w3_heavy.lock 串行），**不许进用例**。
ammoicons 只有 1.08 MB、含 Texture2D + Sprite + AssetBundle 三种类型树，
够验通用装配路；units 那部分走 _work/w10_ref 的冻结件，缺就 skip（与 test_contracts.py 同一姿势）。
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

SOURCE_PREFIX = "ammoicons"
#: units 包元数据区的冻结件（_work/w10_lead_ref.py 产出，4,129,040 B）。
UNITS_META = WORK / "w10_ref" / "units_meta.bin"


def _bundle_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    return bundle_dir(root) if root is not None else None


def _source_bundle() -> Path:
    base = _bundle_dir()
    hits = sorted(base.glob(f"{SOURCE_PREFIX}*.bundle")) if base is not None else []
    if not hits:
        pytest.skip(f"找不到 {SOURCE_PREFIX}*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return hits[0]


def _source_meta(path: Path) -> bytes:
    """取一个真包的**元数据区**（48 B 头 + 类型表 + … + 对齐填充）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        data_offset = struct.unpack_from(">q", head, 32)[0]
        return reader.read_range(node.offset, data_offset)


def _tex(name: str, w: int = 4, h: int = 4) -> dict:
    return builder.texture2d_fields(
        name=name, width=w, height=h, image=builder.solid_rgba(w, h, (255, 0, 255, 255))
    )


def _read_object(path: Path, class_id: int, path_id: int) -> dict:
    """从**刚落盘的包**里按 pathID 读一个对象（走产品自己的读侧，不碰内存状态）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        data_offset = struct.unpack_from(">q", head, 32)[0]
        meta = reader.read_range(node.offset, data_offset)
        table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
        for row in info.objects:
            if row[1] == path_id:
                blob = reader.read_range(node.offset + info.data_offset + row[2], row[3])
                return clone.read_object(blob, table.entry_for(class_id).nodes)
    raise AssertionError(f"包里没有 pathID={path_id}")


def _first_sprite(path: Path) -> tuple[int, dict]:
    """源包里随便找一个 Sprite 的字段树当模板（Sprite -> Texture 是包内 PPtr）。"""
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        meta = reader.read_range(node.offset, struct.unpack_from(">q", head, 32)[0])
        table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
        for row in info.objects:
            if table.entries[row[4]].class_id == builder.CLASS_SPRITE:
                blob = reader.read_range(node.offset + info.data_offset + row[2], row[3])
                fields = clone.read_object(blob, table.entry_for(builder.CLASS_SPRITE).nodes)
                return row[1], fields
    pytest.skip("源包里没有 Sprite（换个小包当 schema 来源）")


@pytest.fixture(scope="module")
def ammo_meta() -> bytes:
    return _source_meta(_source_bundle())


BUNDLE_NAME = "w10test0000000000000000000000000.bundle"


# --------------------------------------------------------------------------- #
# A · build_object_bundle 的通用形
# --------------------------------------------------------------------------- #
def test_object_bundle_two_objects_two_container_rows(ammo_meta: bytes, tmp_path: Path) -> None:
    p1, p2 = 0x5100_0000_0000_0001, 0x5100_0000_0000_0002
    paths = (
        "Assets/Resources_moved/Images/Ammunition/Icons/A.png",
        "Assets/Resources_moved/Images/Ammunition/Icons/B.png",
    )
    plan = builder.build_object_bundle(
        objects=[
            builder.ObjectSpec(class_id=28, fields=_tex("A"), path_id=p1, label="texA"),
            builder.ObjectSpec(class_id=28, fields=_tex("B"), path_id=p2, label="texB"),
        ],
        bundle_name=BUNDLE_NAME,
        source_meta=ammo_meta,
        container=[(paths[0], p1), (paths[1], p2)],
        block_type=builder.BLOCK_LZ4HC,
    )
    assert plan.object_count == 3, "2 个 Texture2D + 1 个 AssetBundle"
    assert plan.bundle_path_id == 1
    assert plan.path_id_of == (("texA", p1), ("texB", p2))
    out = tmp_path / BUNDLE_NAME
    out.write_bytes(plan.data)
    info = builder.reopen_container(out)
    # 注：info["container"] 的值是 PPtr 字典（m_FileID/m_PathID），pathID 走 container_rows
    assert {row[0]: row[1] for row in info["container_rows"]} == {paths[0]: p1, paths[1]: p2}
    assert info["object_count"] == 3
    assert info["block_types"] == [3], "本作真包 77/77 全是块类型 3（LZ4HC）"


def test_object_bundle_same_path_twice_shares_one_preload_window(
    ammo_meta: bytes, tmp_path: Path
) -> None:
    p1, p2 = 0x5100_0000_0000_0011, 0x5100_0000_0000_0012
    path = "Assets/Resources_moved/Images/Ammunition/Icons/C.png"
    plan = builder.build_object_bundle(
        objects=[
            builder.ObjectSpec(class_id=28, fields=_tex("C1"), path_id=p1),
            builder.ObjectSpec(class_id=28, fields=_tex("C2"), path_id=p2),
        ],
        bundle_name=BUNDLE_NAME,
        source_meta=ammo_meta,
        container=[(path, p1), (path, p2)],
    )
    out = tmp_path / BUNDLE_NAME
    out.write_bytes(plan.data)
    info = builder.reopen_container(out)
    rows = info["container_rows"]
    assert [r[0] for r in rows] == [path, path], "同路径两条条目（真件贴图就是这个形状）"
    assert [(r[2], r[3]) for r in rows] == [(0, 2), (0, 2)], "同路径连续条目共用一个 preload 窗口"


def test_object_bundle_copies_type_table_bytes(ammo_meta: bytes, tmp_path: Path) -> None:
    """N3 的机械判据：新包的类型表项是**源包那一段的原始字节**（不是重写的）。"""
    src_table = builder.read_raw_type_table(
        ammo_meta, common_strings=clone.unitypy_common_strings()
    )
    src_raw = src_table.entry(28).raw
    plan = builder.build_object_bundle(
        objects=[builder.ObjectSpec(class_id=28, fields=_tex("D"), path_id=0x5100_0000_0000_0021)],
        bundle_name=BUNDLE_NAME,
        source_meta=ammo_meta,
        container=[("Assets/x.png", 0x5100_0000_0000_0021)],
    )
    assert src_raw in plan.serialized.blob, "源包 classID=28 的类型表项字节必须原样出现在新包里"


# --------------------------------------------------------------------------- #
# A'' · 四条负控（**能红才算尺子**）
# --------------------------------------------------------------------------- #
def test_neg_container_points_at_missing_object(ammo_meta: bytes) -> None:
    with pytest.raises(AssetError, match="但包里没有这个对象"):
        builder.build_object_bundle(
            objects=[
                builder.ObjectSpec(class_id=28, fields=_tex("E"), path_id=0x5100_0000_0000_0031)
            ],
            bundle_name=BUNDLE_NAME,
            source_meta=ammo_meta,
            container=[("Assets/y.png", 0x5100_0000_0000_0032)],
        )


def test_neg_duplicate_path_id(ammo_meta: bytes) -> None:
    pid = 0x5100_0000_0000_0041
    with pytest.raises(AssetError, match="重复的 pathID"):
        builder.build_object_bundle(
            objects=[
                builder.ObjectSpec(class_id=28, fields=_tex("F1"), path_id=pid),
                builder.ObjectSpec(class_id=28, fields=_tex("F2"), path_id=pid),
            ],
            bundle_name=BUNDLE_NAME,
            source_meta=ammo_meta,
            container=[("Assets/z.png", pid)],
        )


def test_neg_assetbundle_must_not_be_passed_in(ammo_meta: bytes) -> None:
    with pytest.raises(AssetError, match="不要放 AssetBundle 对象"):
        builder.build_object_bundle(
            objects=[
                builder.ObjectSpec(
                    class_id=builder.CLASS_ASSETBUNDLE,
                    fields=builder.assetbundle_fields(bundle_name="x", container=[("p", 1)]),
                    path_id=0x5100_0000_0000_0051,
                )
            ],
            bundle_name=BUNDLE_NAME,
            source_meta=ammo_meta,
            container=[("Assets/w.png", 0x5100_0000_0000_0051)],
        )


def test_neg_class_id_not_in_source_table(ammo_meta: bytes) -> None:
    """ammoicons 的类型表里没有 Mesh(43) -> 必须抛，且提示不许自己写树。"""
    with pytest.raises(AssetError, match="没有 classID=43"):
        builder.build_object_bundle(
            objects=[builder.ObjectSpec(class_id=43, fields={}, path_id=0x5100_0000_0000_0061)],
            bundle_name=BUNDLE_NAME,
            source_meta=ammo_meta,
            container=[("Assets/m.mesh", 0x5100_0000_0000_0061)],
        )


# --------------------------------------------------------------------------- #
# B · 同 classID 多棵树必须点名 script_index（真 units 元数据冻结件）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not UNITS_META.is_file(), reason="缺 _work/w10_ref/units_meta.bin（先跑 _work/w10_lead_ref.py）"
)
def test_units_monobehaviour_needs_script_index() -> None:
    table = builder.read_raw_type_table(
        UNITS_META.read_bytes(), common_strings=clone.unitypy_common_strings()
    )
    hits = table.entries_for_class(114)
    assert len(hits) == 22, f"units 里 MonoBehaviour 的类型表项数 = 22，实测 {len(hits)}"
    assert len({e.script_index for e in hits}) == 22, "22 项各带不同的 script_type_index"
    with pytest.raises(AssetError, match="必须点名 script_index"):
        table.entry_for(114)
    picked = table.entry_for(114, script_index=hits[0].script_index)
    assert picked.script_index == hits[0].script_index
    with pytest.raises(AssetError, match="没有 script_type_index"):
        table.entry_for(114, script_index=9999)
    # 其余类都只有一项：不点名也能取
    for cid in (1, 4, 21, 23, 28, 33, 43, 137, 205):
        assert table.entry_for(cid).class_id == cid


# --------------------------------------------------------------------------- #
# D · N4 的尺子本身（PPtr 普查 / 重映射）—— 先喂一个已知违规的点进去，能红才算尺子
# --------------------------------------------------------------------------- #
def test_n4_ruler_catches_dangling_pptr() -> None:
    go = builder.ObjectSpec(class_id=1, label="go", path_id=200, fields={})
    mesh = builder.ObjectSpec(class_id=43, label="mesh", path_id=300, fields={})
    mf = builder.ObjectSpec(
        class_id=33,
        label="mf",
        path_id=100,
        fields={
            "m_GameObject": {"m_FileID": 0, "m_PathID": 200},
            "m_Mesh": {"m_FileID": 0, "m_PathID": 300},
        },
    )
    assert builder.dangling_pptrs([mf, go, mesh]) == (), "三条引用都落得住，尺子必须绿"

    # 正控：把 m_Mesh 指到一个不存在的 pathID —— 尺子**必须**红
    bad = builder.ObjectSpec(
        class_id=33,
        label="mf",
        path_id=100,
        fields={
            "m_GameObject": {"m_FileID": 0, "m_PathID": 200},
            "m_Mesh": {"m_FileID": 0, "m_PathID": 999},
        },
    )
    problems = builder.dangling_pptrs([bad, go, mesh])
    assert len(problems) == 1 and "999" in problems[0], f"正控没红：{problems}"

    # 空引用（pathID=0）是合法的，不许误报
    empty = builder.ObjectSpec(
        class_id=23,
        label="mr",
        path_id=101,
        fields={"m_ProbeAnchor": {"m_FileID": 0, "m_PathID": 0}},
    )
    assert builder.dangling_pptrs([empty]) == (), "空 PPtr 是合法的"


def test_n4_ruler_checks_external_file_ids() -> None:
    mat = builder.ObjectSpec(
        class_id=21,
        label="mat",
        path_id=400,
        fields={"m_Shader": {"m_FileID": 2, "m_PathID": 5}},
    )
    problems = builder.dangling_pptrs([mat])
    assert problems and "越界" in problems[0], f"没声明 external 时报不出：{problems}"
    assert builder.dangling_pptrs([mat], externals=2) == (), "声明了 2 个 external 就该绿"
    assert len(builder.dangling_pptrs([mat], externals=1)) == 1, "fileID=2 超出 1 个 external"


# --------------------------------------------------------------------------- #
# C · 端到端：**冻结产物**上的 N1 / N3 / N4 / 容器（工单 §3 的判据落在真包上）
# --------------------------------------------------------------------------- #
W10_REF = WORK / "w10_ref"
W10_OUT = WORK / "w10_out" / "PC"
CHASSIS_PID = -101627207120243360
TARGET_PATH = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"
#: 本包用到的 8 个类的源包项号（N3 逐字节照抄的判据）
WANTED = (
    (1, None),
    (4, None),
    (33, None),
    (23, None),
    (43, None),
    (21, None),
    (28, None),
    (48, None),
)


def _unit_bundle() -> Path:
    hits = sorted(W10_OUT.glob("w10unit_assets_all_*.bundle"))
    if not hits:
        pytest.skip(
            "缺 _work/w10_out/PC/w10unit_assets_all_*.bundle（先跑 _work/w10_lead_build.py）"
        )
    return hits[-1]


def _units_meta() -> bytes:
    if not (W10_REF / "units_meta.bin").is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（先跑 _work/w10_lead_ref.py）")
    return (W10_REF / "units_meta.bin").read_bytes()


def _objects_of(path: Path):
    """(table, [(pathID, classID, byte_start, byte_size)], node_offset, data_offset, meta)。"""
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        data_offset = struct.unpack_from(">q", head, 32)[0]
        meta = reader.read_range(node.offset, data_offset)
        table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
        rows = [(row[1], table.entries[row[4]].class_id, row[2], row[3]) for row in info.objects]
    return table, rows, node.offset, data_offset, meta


def test_unit_bundle_object_composition_and_container() -> None:
    """产物包的形状：20 个对象（类直方图钉死）+ 容器 1 条指向 root GameObject。"""
    path = _unit_bundle()
    table, rows, _off, _doff, _meta = _objects_of(path)
    hist: dict[int, int] = {}
    for _pid, cid, _bs, _sz in rows:
        hist[cid] = hist.get(cid, 0) + 1
    assert hist == {1: 5, 4: 5, 21: 1, 23: 1, 28: 2, 33: 1, 43: 1, 48: 3, 142: 1}, hist
    info = builder.reopen_container(path)
    assert len(info["container_rows"]) == 1, "prefab 地址的容器条目 = 1 条（真件同形）"
    cpath, cpid, pre_i, pre_s = info["container_rows"][0]
    assert cpath == TARGET_PATH
    assert (pre_i, pre_s) == (0, 1), "本包只有一个容器条目 ⇒ 窗口 (0, 1)"
    cids = {pid: cid for pid, cid, _b, _s in rows}
    assert cids[cpid] == 1, "容器指向的必须是 GameObject（resourceType=1）"
    del table


def test_n1_mesh_object_bytes_equal_real_source() -> None:
    """**N1 的最强判据**：产物包里那个 Mesh 的对象字节 == 真件 Chassis 逐字节相同。"""
    src = W10_REF / "src_objects" / f"{CHASSIS_PID}_43.bin"
    if not src.is_file():
        pytest.skip("缺真件 Chassis 抽件（先跑 _work/w10_lead_cut.py）")
    path = _unit_bundle()
    table, rows, off, data_offset, _meta = _objects_of(path)
    del table
    mesh_pids = [pid for pid, cid, _b, _s in rows if cid == 43]
    assert len(mesh_pids) == 1
    with ufs.UfsReader(path) as reader:
        row = next(r for r in rows if r[0] == mesh_pids[0])
        blob = reader.read_range(off + data_offset + row[2], row[3])
    expected = src.read_bytes()
    assert len(blob) == len(expected) == 2_892_712
    assert blob == expected, "N1：产物里的 Mesh 必须与真件对象字节逐字节相同（差 = 0）"


def test_n3_type_tables_copied_byte_for_byte() -> None:
    """N3：本包 8 个类的类型表项，逐字节来自 units 的真元数据区。"""
    meta = _units_meta()
    src_table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    path = _unit_bundle()
    with ufs.UfsReader(path) as reader:
        node = reader.nodes[0]
        head = reader.read_range(node.offset, builder.SF_HEADER_SIZE)
        blob = reader.read_range(node.offset, struct.unpack_from(">q", head, 32)[0])
    hits = 0
    for class_id, script_index in WANTED:
        entry = src_table.entry_for(class_id, script_index=script_index)
        assert entry.raw in blob, f"classID={class_id} 的类型表项不是照抄的"
        hits += 1
    assert hits == 8


def test_n4_no_dangling_pptr_in_reopened_material() -> None:
    """N4：从**产物包**里读回材质，它的每个 PPtr 都要落在包内（含 shader 与两张贴图）。"""
    path = _unit_bundle()
    table, rows, off, data_offset, _meta = _objects_of(path)
    mat_pids = [pid for pid, cid, _b, _s in rows if cid == 21]
    assert len(mat_pids) == 1
    with ufs.UfsReader(path) as reader:
        row = next(r for r in rows if r[0] == mat_pids[0])
        blob = reader.read_range(off + data_offset + row[2], row[3])
    fields = clone.read_object(blob, table.entry_for(21).nodes)
    known = {pid for pid, _c, _b, _s in rows}
    bad = [
        (p, fid, pid)
        for p, fid, pid in builder.iter_pptrs(fields)
        if fid == 0 and pid not in known and pid != 0
    ]
    assert bad == [], f"材质里有悬空 PPtr：{bad}"
    # 三张依赖对象必须真的在包里：1 个 shader + 它的两个依赖 + 两张贴图
    assert {cid for _p, cid, _b, _s in rows} >= {21, 28, 48}, (
        "Shader(48) 与 Texture2D(28) 必须与材质同包"
    )


def test_assemble_unit_bundle_library_path(tmp_path: Path) -> None:
    """走**库入口**（W11 要调的那个）：先做负控（少搬依赖必须红），再搬齐闭包（必须绿）。"""
    from ba_mod_maker.asset_kernel import (
        material_build,
        materials,
        prefab_build,
        unit_bundle,
    )

    meta_path = W10_REF / "units_meta.bin"
    manifest = W10_REF / "src_objects.json"
    if not (meta_path.is_file() and manifest.is_file()):
        pytest.skip("缺 _work/w10_ref 的抽件（先跑 w10_lead_ref.py + w10_lead_cut.py）")
    meta = meta_path.read_bytes()
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    by = {o["path_id"]: o for o in json.loads(manifest.read_text(encoding="utf-8"))["objects"]}

    def payload(pid: int) -> bytes:
        return (W10_REF / "src_objects" / by[pid]["file"]).read_bytes()

    def fields(pid: int) -> dict:
        return clone.read_object(payload(pid), table.entry_for(by[pid]["class_id"]).nodes)

    # ★ 2026-10-25：`mesh_build` 随「手写 mesh 字段」那条栈退役（用户口径「全删」）——
    #   本用例量的是**装配层**（容器条目 / 预载窗口 / 悬空 PPtr），所以不再现造 Mesh，
    #   直接拿真件 Chassis 的 Mesh 字段树当 classID=43 的样本（Mesh 无 PPtr，不影响悬空判据）。
    built_mesh = fields(CHASSIS_PID)
    mesh_pid = builder.path_id_for("w10unit|mesh")
    mat_pid = builder.path_id_for("w10unit|material")
    tbl = materials.table_for("HDRP/Lit")
    built_mat = material_build.material_fields_from_source(
        fields(7952762356820983200), table=tbl, shader="HDRP/Lit"
    )
    extra = [
        builder.ObjectSpec(43, built_mesh, mesh_pid, label="mesh"),
        builder.ObjectSpec(21, built_mat, mat_pid, label="material"),
    ]
    spec = prefab_build.PrefabNode(
        name="RU_T72B_Base",
        children=(
            prefab_build.PrefabNode(
                name="Chassis",
                kind=prefab_build.KIND_MESH,
                mesh=mesh_pid,
                materials=(mat_pid,),
                children=(prefab_build.PrefabNode(name="BaseArmor"),),
            ),
        ),
    )
    # ---- 负控（先红）：只给 Mesh+Material、**不搬** shader/贴图 ⇒ 材质必然悬空 ----
    red = unit_bundle.assemble_unit_bundle(
        meta=meta,
        spec=spec,
        extra=extra,
        container_path=TARGET_PATH,
        bundle_name="w10libbad000000000000000000000000.bundle",
    )
    assert red.problems, "少了 shader/贴图却不报悬空 —— 这把尺子假绿了"
    assert any("悬空" in line for line in red.problems), red.problems
    assert not red.ok

    # ---- 正控：把闭包搬齐（3 个 Shader 原样 + 2 张内联贴图）⇒ 必须绿 ----
    shader_pids = (7322088784485553867, 1354542265007958578, -8015457887526078469)
    tex_pids = (6513056008332064871, 5509599546982847501)
    full = [
        *(
            builder.ObjectSpec(48, None, pid, payload=payload(pid), label=f"shader{pid}")
            for pid in shader_pids
        ),
        *(
            builder.ObjectSpec(
                28,
                builder.inline_texture2d_fields(
                    fields(pid), (W10_REF / "tex" / f"{pid}.bin").read_bytes()
                ),
                pid,
                label=f"tex{pid}",
            )
            for pid in tex_pids
        ),
        *extra,
    ]
    result = unit_bundle.assemble_unit_bundle(
        meta=meta,
        spec=spec,
        extra=full,
        container_path=TARGET_PATH,
        bundle_name="w10lib0000000000000000000000000000.bundle",
    )
    assert result.problems == (), result.problems
    assert result.ok
    assert result.plan.container == ((TARGET_PATH, result.root_game_object),)
    out = tmp_path / result.bundle_name
    out.write_bytes(result.plan.data)
    info = builder.reopen_container(out)
    # ★ 2026-10-06 口径更新（a1 实测 + Lead 复核）：预载窗口 = **本资产的全部对象**，
    # 不是「只有根对象」。真件 RU_T72B_Base.prefab 的窗口是 [28116, +197)，
    # 窗口里明确列着它用到的 5 个脚本（m_PreloadTable 里 3,986 条命中 MonoScript）。
    # 旧断言 (…, 0, 1) 会把「MonoScript 在窗口外」这种错误固化成绿。
    (row,) = info["container_rows"]
    assert row[0] == TARGET_PATH
    assert row[1] == result.root_game_object
    assert row[2] == 0, row
    # 预载表**不含 AssetBundle 自己**（它就是那张表的主人）⇒ 覆盖数 = object_count − 1
    assert row[3] == result.plan.object_count - 1, (
        f"预载窗口只覆盖 {row[3]} 个对象；包内除 AssetBundle 外有 "
        f"{result.plan.object_count - 1} 个 —— 真件的窗口覆盖全部对象（含它用到的 MonoScript）"
    )
    assert info["object_count"] == result.plan.object_count
    assert builder.dangling_pptrs(full) == ()


def test_remap_pptrs_maps_only_what_is_known() -> None:
    fields = {"a": {"m_FileID": 0, "m_PathID": 5}, "b": [{"m_FileID": 0, "m_PathID": 6}], "c": 3}
    out = builder.remap_pptrs(fields, {5: 50})
    assert out["a"]["m_PathID"] == 50
    assert out["b"][0]["m_PathID"] == 6, (
        "映射表里没有的 pathID 原样保留（漏映射交给 dangling_pptrs 抓）"
    )
    assert out["c"] == 3
    assert fields["a"]["m_PathID"] == 5, "原输入不许被就地改"


def test_object_bundle_with_intra_bundle_pptr_is_not_dangling(tmp_path: Path) -> None:
    """端到端 N4：Sprite 的 m_RD.texture 是**包内 PPtr**。

    装配后无悬空，重开能读回并指着我们自己的贴图。
    """
    src = _source_bundle()
    _spr_src_pid, spr_template = _first_sprite(src)
    tex_pid, spr_pid = 0x5100_0000_0000_0081, 0x5100_0000_0000_0082
    path = "Assets/Resources_moved/Images/Ammunition/Icons/W10PROBE.png"
    sprite_fields = builder.sprite_fields_for(
        spr_template, texture_path_id=tex_pid, name="W10PROBE"
    )
    specs = [
        builder.ObjectSpec(class_id=28, fields=_tex("W10PROBE"), path_id=tex_pid, label="tex"),
        builder.ObjectSpec(class_id=213, fields=sprite_fields, path_id=spr_pid, label="sprite"),
    ]
    assert builder.dangling_pptrs(specs) == (), "Sprite -> 我们的贴图 是包内 PPtr，不该悬空"
    plan = builder.build_object_bundle(
        objects=specs,
        bundle_name=BUNDLE_NAME,
        source_meta=_source_meta(src),
        container=[(path, tex_pid), (path, spr_pid)],
        block_type=builder.BLOCK_LZ4HC,
    )
    out = tmp_path / BUNDLE_NAME
    out.write_bytes(plan.data)
    info = builder.reopen_container(out)
    assert info["object_count"] == 3, "贴图 + Sprite + AssetBundle"
    assert [(r[0], r[1]) for r in info["container_rows"]] == [(path, tex_pid), (path, spr_pid)]
    assert [(r[2], r[3]) for r in info["container_rows"]] == [(0, 2), (0, 2)], (
        "同路径两条共用一个窗口"
    )
    back = _read_object(out, 213, spr_pid)
    assert back["m_RD"]["texture"] == {"m_FileID": 0, "m_PathID": tex_pid}, (
        "读回来必须指着我们自己的贴图"
    )
    assert back["m_Name"] == "W10PROBE"
