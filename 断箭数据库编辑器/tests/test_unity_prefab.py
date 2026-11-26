"""`core/assets/unity_prefab.py` 的用例（⛔ 不起 Unity、⛔ 不读游戏目录）。

夹具全部临时目录现造：一份最小 GLB（glTF 2.0 二进制：4 顶点 / 2 三角形 / 两套蒙皮权重）
＋ 一份最小 prefab（YAML 文本：1 个 LODGroup + LOD0 两个 SMR + LOD1 一个 SMR）
＋ 一个占位 `model.fbx`（Python 侧**不解析**它，只把它写进 job.json）。

要点（都是这轮真件上量出来的口径，改坏了要能红）：
  · **网格只有 FBX 一条路**：工程里没有 `model.fbx` ⇒ 抛（⛔ 不降级、不留兼容 shim）；
  · `job.json` 字段齐全（规格 §2），且 Python 侧**不写任何网格字节**（`bytes == 0`）；
  · 只发 LOD0；配不上的件进 `skipped` 且原因非空；
  · `parts[]` 里**没有**旧路那套 bin / 骨名 / 通道字段（手写网格那条路已整体退役）。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import glb_import
from ba_mod_maker.core.assets import unity_prefab as up

PREFAB_ASSET = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"
POSITIONS = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
UVS = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
#: 只有 mesh 0（Chassis / LOD1 / turret）带 TEXCOORD_1；K1 用 mesh 1 ⇒ 没有 uv1
UV1 = [(0.25, 0.75), (0.5, 0.5), (0.75, 0.25), (0.1, 0.9)]
INDICES = [0, 1, 2, 0, 2, 3]
#: 蒙皮件（Chassis / LOD1 / turret）：body / body+LR000 / LR000 / neutral_bone
SKIN_JOINTS = [(1, 0, 0, 0), (1, 2, 0, 0), (2, 0, 0, 0), (3, 0, 0, 0)]
SKIN_WEIGHTS = [(1.0, 0.0, 0.0, 0.0), (0.5, 0.5, 0.0, 0.0),
                (1.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)]
#: 刚性件（K1）：100% 绑在 neutral_bone（真件包没有这根骨）
RIGID_JOINTS = [(3, 0, 0, 0)] * 4
RIGID_WEIGHTS = [(1.0, 0.0, 0.0, 0.0)] * 4
JOINT_NAMES = ("root", "body", "LR000", "neutral_bone")


def _glb_bytes(joints_rows=None, weights_rows=None) -> bytes:
    """最小 GLB：两条 mesh（蒙皮件 / 刚性件）＋ 1 套 4 根骨的 skin。"""
    skin_joints = joints_rows or SKIN_JOINTS
    skin_weights = weights_rows or SKIN_WEIGHTS
    blob = bytearray()
    views = []

    def add(data: bytes) -> int:
        while len(blob) % 4:
            blob.append(0)
        offset = len(blob)
        blob.extend(data)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data)})
        return len(views) - 1

    def floats(values):
        return add(struct.pack("<" + str(len(values)) + "f", *values))

    def ushorts(values):
        return add(struct.pack("<" + str(len(values)) + "H", *values))

    flat_pos = [c for p in POSITIONS for c in p]
    flat_nor = [c for _p in POSITIONS for c in (0.0, 0.0, 1.0)]
    flat_uv = [c for t in UVS for c in t]
    v_pos = floats(flat_pos)
    v_nor = floats(flat_nor)
    v_uv = floats(flat_uv)
    v_j = ushorts([c for row in skin_joints for c in row])
    v_w = floats([c for row in skin_weights for c in row])
    v_idx = ushorts(INDICES)
    v_j_rigid = ushorts([c for row in RIGID_JOINTS for c in row])
    v_w_rigid = floats([c for row in RIGID_WEIGHTS for c in row])
    v_uv1 = floats([c for row in UV1 for c in row])
    accessors = [
        {"bufferView": v_pos, "componentType": 5126, "count": 4, "type": "VEC3"},
        {"bufferView": v_nor, "componentType": 5126, "count": 4, "type": "VEC3"},
        {"bufferView": v_uv, "componentType": 5126, "count": 4, "type": "VEC2"},
        {"bufferView": v_j, "componentType": 5123, "count": 4, "type": "VEC4"},
        {"bufferView": v_w, "componentType": 5126, "count": 4, "type": "VEC4"},
        {"bufferView": v_idx, "componentType": 5123, "count": len(INDICES), "type": "SCALAR"},
        {"bufferView": v_j_rigid, "componentType": 5123, "count": 4, "type": "VEC4"},
        {"bufferView": v_w_rigid, "componentType": 5126, "count": 4, "type": "VEC4"},
        {"bufferView": v_uv1, "componentType": 5126, "count": 4, "type": "VEC2"},
    ]

    def primitive(j_accessor: int, w_accessor: int, with_uv1: bool = False) -> dict:
        attrs = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2,
                 "JOINTS_0": j_accessor, "WEIGHTS_0": w_accessor}
        if with_uv1:
            attrs["TEXCOORD_1"] = 8
        return {"attributes": attrs, "indices": 5}

    joint_nodes = list(range(1, 1 + len(JOINT_NAMES)))
    nodes = [{"name": "Root", "children": []}]
    for name in JOINT_NAMES:
        nodes.append({"name": name})
        nodes[0]["children"].append(len(nodes) - 1)
    for name, mesh_index in (("Chassis", 0), ("K1", 1),
                             ("000_skinned_Chassis", 0), ("turret", 0)):
        node = {"name": name, "mesh": mesh_index}
        if name != "K1":
            node["skin"] = 0
        nodes.append(node)
        nodes[0]["children"].append(len(nodes) - 1)
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": [{"primitives": [primitive(3, 4, with_uv1=True)]},
                   {"primitives": [primitive(6, 7)]}],
        "skins": [{"joints": joint_nodes}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    bin_bytes = bytes(blob)
    while len(bin_bytes) % 4:
        bin_bytes += b"\x00"
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    return b"".join([
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(json_bytes), b"JSON"), json_bytes,
        struct.pack("<I4s", len(bin_bytes), b"BIN\x00"), bin_bytes,
    ])


def _prefab_text(*, bones=("body", "LR000"), with_lod=True, duplicate=False) -> str:
    """最小 prefab（YAML 文本）：根 + LODGroup + LOD0 两个 SMR + LOD1 一个 SMR。"""
    lines = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"]

    def game_object(fid: int, name: str) -> None:
        lines.extend([
            "--- !u!1 &" + str(fid),
            "GameObject:",
            "  serializedVersion: 6",
            "  m_Component:",
            "  - component: {fileID: " + str(fid + 3000) + "}",
            "  m_Name: " + name,
            "  m_IsActive: 1",
        ])

    def transform(fid: int, go: int, father: int) -> None:
        lines.extend([
            "--- !u!4 &" + str(fid),
            "Transform:",
            "  m_ObjectHideFlags: 0",
            "  m_GameObject: {fileID: " + str(go) + "}",
            "  m_Father: {fileID: " + str(father) + "}",
            "  m_Children: []",
        ])

    def skinned_renderer(fid: int, go: int, bone_fids) -> None:
        root_bone = bone_fids[0] if bone_fids else 0
        lines.extend([
            "--- !u!137 &" + str(fid),
            "SkinnedMeshRenderer:",
            "  m_ObjectHideFlags: 0",
            "  m_GameObject: {fileID: " + str(go) + "}",
            "  m_Enabled: 1",
            "  m_Materials:",
            "  - {fileID: 2100000, guid: aaaabbbbccccddddeeeeffff00001111, type: 2}",
            "  m_Mesh: {fileID: 4300000, guid: 11112222333344445555666677778888, type: 0}",
            "  m_Bones:",
        ])
        lines.extend("  - {fileID: " + str(b) + "}" for b in bone_fids)
        lines.extend(["  m_BlendShapeWeights: []",
                      "  m_RootBone: {fileID: " + str(root_bone) + "}"])

    game_object(1000, "RU_T72B_Base")
    transform(4000, 1000, 0)
    bone_fids = [4100 + i for i in range(len(bones))]
    for i, name in enumerate(bones):
        game_object(1100 + i, name)
        transform(bone_fids[i], 1100 + i, 4000)
    game_object(1001, "Chassis")
    transform(4001, 1001, 4000)
    skinned_renderer(7001, 1001, bone_fids)
    game_object(1002, "K1")
    transform(4002, 1002, 4000)
    skinned_renderer(7002, 1002, bone_fids)
    game_object(1005, "Level02")
    transform(4005, 1005, 4000)
    game_object(1006, "000_skinned_Chassis")
    transform(4006, 1006, 4005)
    skinned_renderer(7003, 1006, bone_fids)
    if duplicate:
        game_object(1007, "000_skinned_Chassis")
        transform(4007, 1007, 4000)
        skinned_renderer(7004, 1007, bone_fids)
    if with_lod:
        lines.extend([
            "--- !u!205 &9000",
            "LODGroup:",
            "  m_ObjectHideFlags: 0",
            "  m_GameObject: {fileID: 1000}",
            "  m_LODs:",
            "  - screenRelativeHeight: 0.1",
            "    fadeTransitionWidth: 0",
            "    renderers:",
            "    - renderer: {fileID: 7001}",
            "    - renderer: {fileID: 7002}",
            "  - screenRelativeHeight: 0.04",
            "    fadeTransitionWidth: 0",
            "    renderers:",
            "    - renderer: {fileID: 7003}",
            "  m_Enabled: 1",
        ])
    return "\n".join(lines) + "\n"


def _fixture(tmp_path: Path, *, joints_rows=None, weights_rows=None,
             bones=("body", "LR000"), with_lod=True, duplicate=False,
             fbx=True) -> dict:
    """造一份工程 + 一份导出工程（只含那一个 prefab），返回各处路径。

    `fbx=False` 造的是「只有 glb、没有 model.fbx」的工程（旧提取器输出）——
    它必须被拒（网格只有 FBX 一条路）。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "model.glb").write_bytes(_glb_bytes(joints_rows, weights_rows))
    if fbx:
        # Python 侧不解析 FBX（只是把路径写进 job），所以占位字节就够；
        # 真正的导入在 C# 侧由 Unity 的 ModelImporter 做。
        (proj / "model.fbx").write_bytes(b"placeholder fbx")
    unity = tmp_path / "unity"
    prefab_file = unity / PREFAB_ASSET
    prefab_file.parent.mkdir(parents=True)
    prefab_file.write_text(_prefab_text(bones=bones, with_lod=with_lod, duplicate=duplicate),
                           encoding="utf-8")
    return {"proj": proj, "unity": unity, "prefab_file": prefab_file,
            "out": tmp_path / "jobs", "bundle_out": tmp_path / "out"}


def _build(fx: dict, **kwargs) -> dict:
    params = dict(prefab_path=PREFAB_ASSET, out_dir=fx["out"], bundle_name="fixture_bundle",
                  unity_project=fx["unity"], bundle_out_dir=fx["bundle_out"])
    params.update(kwargs)
    return up.build_job(fx["proj"], **params)


def _part(rows, node: str) -> dict:
    hits = [row for row in rows if row["node"] == node]
    assert len(hits) == 1, "件 " + node + " 不在结果里"
    return hits[0]


def test_missing_model_fbx_raises(tmp_path: Path) -> None:
    """工程里没有 model.fbx ⇒ 抛（⛔ 不退回 Python 手写网格那条旧路、不降级）。"""
    fx = _fixture(tmp_path, fbx=False)
    with pytest.raises(up.UnityJobError) as exc:
        _build(fx)
    msg = str(exc.value)
    assert "model.fbx" in msg
    assert "glb / OBJ 不行" in msg
    assert not fx["out"].exists() or not any(fx["out"].iterdir())


def test_job_fields_complete(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    res = _build(fx)
    job = json.loads(Path(res["job"]).read_text(encoding="utf-8"))
    # 2026-10-26：job 多了 `material_keywords`（真件材质用到的关键字，C# 侧据此把材质开关写回）
    #             和 `material_values`（材质数值对齐的读数：改了哪几颗 / 几处）。
    assert set(job) == {"prefab_path", "fbx_path", "out_dir", "work_asset_dir",
                        "bundle_name", "report", "parts", "skipped",
                        "material_keywords", "material_values"}
    assert job["prefab_path"] == PREFAB_ASSET
    assert Path(job["fbx_path"]).name == "model.fbx"
    assert Path(job["fbx_path"]).is_file()
    assert job["bundle_name"] == "fixture_bundle"
    assert job["work_asset_dir"] == "Assets/_unitbuild"
    assert job["out_dir"].endswith("/out") and "\\" not in job["out_dir"]
    assert job["report"].endswith("/report.json")
    assert len(job["parts"]) == 2
    for part in job["parts"]:
        assert set(part) == {"node", "lod", "path"}   # ⛔ 没有 bin / bone_names / 通道字段
        assert part["lod"] is True
        assert part["path"].startswith("RU_T72B_Base/")


def test_python_writes_no_mesh_bytes(tmp_path: Path) -> None:
    """网格由 Unity 造 ⇒ Python 侧只写 job.json 一个文件、写盘字节数 0。"""
    fx = _fixture(tmp_path)
    res = _build(fx)
    assert res["mode"] == "fbx"
    assert res["bytes"] == 0
    written = sorted(p.relative_to(fx["out"]).as_posix()
                     for p in fx["out"].rglob("*") if p.is_file())
    assert written == ["job.json"]
    for row in res["parts"]:
        assert set(row) == {"node", "lod", "path"}


def test_only_lod0_nodes_and_skips_are_explained(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    res = _build(fx)
    nodes = sorted(row["node"] for row in res["parts"])
    assert nodes == ["Chassis", "K1"]
    skipped = {row["node"]: row["reason"] for row in res["skipped"]}
    assert set(skipped) == {"000_skinned_Chassis", "turret"}
    assert "LOD" in skipped["000_skinned_Chassis"]
    assert "没有同名渲染器" in skipped["turret"]
    assert all(reason.strip() for reason in skipped.values())
    # 每一件都要有交代（进 job 或进 skipped，⛔ 不静默丢）
    glb_nodes = {name for name, _m in glb_import.read_glb_parts_file(fx["proj"] / "model.glb")}
    assert glb_nodes == set(nodes) | set(skipped)


def test_missing_prefab_raises(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    with pytest.raises(up.UnityJobError) as exc:
        _build(fx, prefab_path="Assets/nope/Nope.prefab")
    assert "没有这个 prefab" in str(exc.value)


def test_parts_filter_missing_node_raises(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    with pytest.raises(up.UnityJobError) as exc:
        _build(fx, parts=["Nope"])
    assert "Nope" in str(exc.value)


def test_parts_filter_keeps_subset_and_marks_the_rest(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    res = _build(fx, parts=["K1"])
    assert [row["node"] for row in res["parts"]] == ["K1"]
    reasons = {row["node"]: row["reason"] for row in res["skipped"]}
    assert "未选中" in reasons["Chassis"]


def test_lod_false_is_recorded_per_part(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    res = _build(fx, lod=False)
    assert all(row["lod"] is False for row in res["parts"])
    job = json.loads(Path(res["job"]).read_text(encoding="utf-8"))
    assert all(part["lod"] is False for part in job["parts"])


def test_unity_command_is_the_spec_call(tmp_path: Path) -> None:
    fx = _fixture(tmp_path)
    res = _build(fx)
    line = up.unity_command(fx["unity"], res["job"])
    assert "-batchmode -quit" in line
    assert "-executeMethod UnitBuild.Run" in line
    assert str(fx["unity"]) in line
    assert str(Path(res["job"]).with_name("unity.log")) in line


def test_same_name_nodes_without_lodgroup_are_refused(tmp_path: Path) -> None:
    # 没有 LODGroup，且两个 GameObject 同名 ⇒ 按名字无法定位，进 skipped 并说明
    fx = _fixture(tmp_path, with_lod=False, duplicate=True)
    res = _build(fx)
    assert [row["node"] for row in res["parts"]] == ["Chassis", "K1"]
    reasons = [row["reason"] for row in res["skipped"] if row["node"] == "000_skinned_Chassis"]
    assert reasons and "同名" in reasons[0]
