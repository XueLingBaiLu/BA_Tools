r"""W2 · 搬运真件 Shader 的机械判据（产品件 = `asset_kernel/shader_build.py»）。

**判据一行**：`pytest tests/test_build_shader_from_scratch.py -q -n 0» -> 通过数 0 -> N，
其中负控 M 条（喂坏样本必须变红才算尺子，AGENTS §0.9 动作 2）。

## 这把尺子量什么

| 段 | 判据 |
|---|---|
| A | 类型树当 schema：11 个顶层键 + `m_NonModifiableTextures» 的 pair 形状（从树里读出来） |
| B | 正控（真件字节）：5 个冻结 Shader 解码 + 往返逐字节，读数与录的一致 |
| C | 名字索引：`m_Name» 全是空串、真名在 `m_ParsedForm.m_Name»；JSON 往返；查不到名字要抛且列候选 |
| D | 依赖闭包 + PPtr 重映射：`HDRP/Lit» -> 2 个 => 闭包 3 个；悬空 0；blob 逐段解得开 |
| E | 两条 pathID 口径：`renumber»（默认）/ `preserve» |
| F | 端到端：`assemble_unit_bundle(extra=...)» -> 落包 -> **独立读回逐字节** + 全包悬空 0 |
| G | 负控：篡改一个字节 / 长度 / 偏移 / 一条依赖 PPtr / 一条非可变贴图，对应尺子必须红 |

## ⛔ 两个尺子的分工（实测，别把 blob 尺子当万能）

`compressedBlob» 是 LZ4 **块**格式，**没有校验和**。实测（`_work/shader-line/probe_neg.py»）：
把某一段**中间的一个字面量字节**改掉，`check_shader_blob» **照样绿**（解出来的长度不变）；
只有改到**段首 token 字节**、或改`offsets/compressedLengths/decompressedLengths»三者之一才会红。
=> 字节级的判据只能是**逐字节读回**（F 段），blob 尺子只管「结构 + 长度」。
本文件把这两个读数都钉住（`test_lz4_block_has_no_checksum_...» / `test_neg_...»）。

## 夹具从哪来（用例**不开包**）

`_work/w10_ref/units_meta.bin»（真件类型表 4,129,040 B）+ `src_objects/<pid>_48.bin»（真件 Shader
对象字节）。本文件用它们**现造一个真的 UnityFS 源包**
（`builder.build_object_bundle» + `BLOCK_NONE»）—— 读侧走的是产品的真实开包路
（ufs + 类型表），而 3.38 GB 的 units 一次都不开。

⚠ 最大的那个 Shader（`Shader Graphs/UnitV3»，28,333,744 B）**不进用例**（造夹具 + 跑用例都太贵）；
它的读数是 `_work/shader-line/结论.md» 里那次真件全量端到端。
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import (
    builder,
    clone,
    material_build,
    materials,
    prefab_build,
    unit_bundle,
)
from ba_mod_maker.asset_kernel import shader_build as sb
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
REF = WORK / "w10_ref"
META_BIN = REF / "units_meta.bin"
SRC = REF / "src_objects"

# ---- 真件读数（_work/shader-line/probe-shaders.json；29 个里冻结了 6 个，这里用 5 个） ----
LIT = 7322088784485553867
HDRP_FB = 1354542265007958578
CORE_FB = -8015457887526078469
SG_FB = 3819691914921817934
UNLIT = 5951657029987726660
#: 最大的那个（28,333,744 B）不进用例 —— 见文件头。
UNITV3 = 2703859142610330766

NAMES = {
    LIT: "HDRP/Lit",
    HDRP_FB: "Hidden/HDRP/FallbackError",
    CORE_FB: "Hidden/Core/FallbackError",
    SG_FB: "Hidden/Shader Graph/FallbackError",
    UNLIT: "HDRP/Unlit",
}
SIZES = {LIT: 5_617_428, HDRP_FB: 3_608, CORE_FB: 3_240, SG_FB: 3_400, UNLIT: 35_908}
DEPS = {
    LIT: ((0, HDRP_FB), (0, CORE_FB)),
    HDRP_FB: ((0, CORE_FB),),
    CORE_FB: (),
    SG_FB: (),
    UNLIT: ((0, HDRP_FB), (0, CORE_FB)),
}
BLOBS = {LIT: 5_526_729, HDRP_FB: 675, CORE_FB: 525, SG_FB: 525, UNLIT: 14_331}
GROUPS = {LIT: (4,), HDRP_FB: (1,), CORE_FB: (1,), SG_FB: (1,), UNLIT: (1,)}
CLOSURE_OF_LIT = (LIT, HDRP_FB, CORE_FB)
FIXTURE_IDS = (LIT, HDRP_FB, CORE_FB, SG_FB, UNLIT)

#: 端到端用的真件 Mesh / 材质（同一批冻结件）
MESH_SRC = -8847993855520474906
MAT_SRC = 7952762356820983200

BUNDLE_NAME = "w2shadersrc000000000000000000000000.bundle"
CONTAINER = "Assets/Resources_moved/Shader/W2HdrpLit.shader"
PROBE_PATH = "Assets/Resources_moved/ModelPrefabs/W2/W2ShaderProbe.prefab"


def _missing() -> list[str]:
    want = [META_BIN]
    want += [SRC / f"{pid}_48.bin" for pid in FIXTURE_IDS]
    want += [SRC / f"{MESH_SRC}_43.bin", SRC / f"{MAT_SRC}_21.bin"]
    return [p.name for p in want if not p.is_file()]


_MISSING = _missing()
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"缺 _work/w10_ref 的冻结件：{_MISSING}（跑 w10_lead_ref.py / w10_lead_cut.py）",
)


# --------------------------------------------------------------------------- #
# 夹具与独立尺子（都不碰被测模块的内部状态）
# --------------------------------------------------------------------------- #
@cache
def _meta() -> bytes:
    return META_BIN.read_bytes()


@cache
def _nodes_of(class_id: int) -> tuple[clone.TypeNode, ...]:
    """类型树**只从包里读**（⛔ 不自己写树）；缓存住 —— units 那棵树有 2,324 个节点。"""
    table = builder.read_raw_type_table(_meta(), common_strings=clone.unitypy_common_strings())
    return table.entry_for(class_id).nodes


def _tree() -> tuple[clone.TypeNode, ...]:
    return _nodes_of(48)


@cache
def _raw(path_id: int, class_id: int = 48) -> bytes:
    return (SRC / f"{path_id}_{class_id}.bin").read_bytes()


def _fields(path_id: int, class_id: int = 48) -> dict:
    nodes = _tree() if class_id == 48 else _nodes_of(class_id)
    return clone.read_object(_raw(path_id, class_id), nodes)


def _flip(data: bytes, at: int) -> bytes:
    out = bytearray(data)
    out[at] ^= 0x01
    return bytes(out)


def _build_source(
    out: Path,
    *,
    patches: Mapping[int, Callable[[dict], None]] | None = None,
    ids: Sequence[int] = FIXTURE_IDS,
) -> Path:
    """用冻结的真件字节现造一个**真的 UnityFS 源包**（读侧走产品的真实开包路）。

    块类型用 BLOCK_NONE：这个包只是夹具，不压缩省时间；读侧与压缩与否无关。
    """
    specs = []
    for pid in ids:
        raw = _raw(pid)
        patch = (patches or {}).get(pid)
        if patch is not None:
            fields = clone.read_object(raw, _tree())
            patch(fields)
            raw = clone.write_object(fields, _tree())
        specs.append(builder.ObjectSpec(48, None, pid, payload=raw, label=NAMES[pid]))
    plan = builder.build_object_bundle(
        objects=specs,
        bundle_name=BUNDLE_NAME,
        source_meta=_meta(),
        container=[(CONTAINER, LIT)],
        block_type=builder.BLOCK_NONE,
    )
    out.write_bytes(plan.data)
    return out


def _write_bundle(path: Path, specs: Sequence[builder.ObjectSpec], *, container_pid: int) -> Path:
    plan = builder.build_object_bundle(
        objects=list(specs),
        bundle_name=path.name,
        source_meta=_meta(),
        container=[(CONTAINER, int(container_pid))],
        block_type=builder.BLOCK_NONE,
    )
    path.write_bytes(plan.data)
    return path


def _dump(path: Path) -> tuple[builder.RawTypeTable, dict[int, tuple[int, bytes]]]:
    """**独立读回**：只走 ufs + 类型表 + clone，一个字节都不经过被测模块。"""
    out: dict[int, tuple[int, bytes]] = {}
    with ufs.UfsReader(path) as reader:
        node = next(n for n in reader.nodes if not str(n.path).endswith(".resS"))
        info = ufs.read_serialized_file(reader, node)
        meta = reader.read_range(node.offset, info.data_offset)
        table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
        for row in info.objects:
            pid = int(row[1])
            blob = bytes(
                reader.read_range(
                    node.offset + info.data_offset + int(row[2]), int(row[3])
                )
            )
            out[pid] = (table.entries[int(row[4])].class_id, blob)
    return table, out


def _payload_mismatches(path: Path, expected: Mapping[int, bytes]) -> list[str]:
    """payload 对象：产物里的**对象字节**必须逐字节 == 源。"""
    _table, got = _dump(path)
    out: list[str] = []
    for pid, want in expected.items():
        hit = got.get(int(pid))
        if hit is None:
            out.append(f"pathID={pid} 不在产物包里")
            continue
        class_id, blob = hit
        if class_id != 48:
            out.append(f"pathID={pid} 的 classID={class_id}，不是 Shader")
        if blob != want:
            pairs = zip(blob, want, strict=False)
            first = next((i for i, (a, b) in enumerate(pairs) if a != b), min(len(blob), len(want)))
            out.append(
                f"pathID={pid} 对象字节与源**不逐字节相同**："
                f"{len(blob)} B vs 源 {len(want)} B，首个不同在第 {first} 字节"
            )
    return out


def _blob_mismatches(path: Path, expected: Mapping[int, bytes]) -> list[str]:
    """fields 对象：产物里的 **compressedBlob** 必须逐字节 == 源（重映射只动 PPtr，不动 blob）。"""
    table, got = _dump(path)
    out: list[str] = []
    for pid, want in expected.items():
        hit = got.get(int(pid))
        if hit is None:
            out.append(f"pathID={pid} 不在产物包里")
            continue
        fields = clone.read_object(hit[1], table.entry_for(48).nodes)
        mine = bytes(fields["compressedBlob"])
        if mine != want:
            out.append(
                f"pathID={pid} 的 compressedBlob 与源**不逐字节相同**："
                f"{len(mine):,} vs {len(want):,} B"
            )
    return out


def _dangling(path: Path) -> list[str]:
    """全包悬空普查（独立实现：只认对象表 + 类型树）。"""
    table, got = _dump(path)
    known = set(got) | {1}
    out: list[str] = []
    for pid, (class_id, blob) in got.items():
        fields = clone.read_object(blob, table.entry_for(class_id).nodes)
        for where, file_id, target in builder.iter_pptrs(fields):
            if file_id == 0 and target != 0 and target not in known:
                out.append(f"pathID={pid}.{where} -> 包内 pathID {target} 不存在")
            elif file_id != 0:
                out.append(f"pathID={pid}.{where} -> 外部 fileID={file_id}（本包没声明 external）")
    return out


@pytest.fixture(scope="module")
def source_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_source(tmp_path_factory.mktemp("w2shadersrc") / BUNDLE_NAME)


# --------------------------------------------------------------------------- #
# A · 类型树当 schema
# --------------------------------------------------------------------------- #
def test_shader_type_tree_top_level_keys() -> None:
    """classID 48 的 11 个顶层键（顺序 = 序列化顺序）—— 一个不能少、不能改名。"""
    got = [n.name for n in _tree() if n.level == 1]
    assert got == [
        "m_Name",
        "m_ParsedForm",
        "platforms",
        "offsets",
        "compressedLengths",
        "decompressedLengths",
        "compressedBlob",
        "stageCounts",
        "m_Dependencies",
        "m_NonModifiableTextures",
        "m_ShaderIsBaked",
    ]
    assert len(got) == 11


def test_non_modifiable_textures_is_a_map_of_pairs() -> None:
    """`m_NonModifiableTextures» 的形状**从类型树读出来**：map -> Array<pair<first, second>>。

    ⛔ 不是猜的 —— 这条断言就是本文件那条非可变贴图负控的施工依据。
    """
    nodes = list(_tree())
    start = next(
        i for i, n in enumerate(nodes) if n.level == 1 and n.name == "m_NonModifiableTextures"
    )
    sub = []
    for n in nodes[start + 1 :]:
        if n.level <= 1:
            break
        sub.append((n.level, n.name, n.type))
    assert (2, "Array", "Array") in sub
    assert (3, "data", "pair") in sub
    assert (4, "first", "string") in sub
    assert (4, "second", "PPtr<Texture>") in sub
    # 施工：写一棵带一条非可变贴图的树，必须能读回来（形状对不上就会抛）
    fields = _fields(CORE_FB)
    fields["m_NonModifiableTextures"] = [
        {"first": "Layer_00000000", "second": {"m_FileID": 0, "m_PathID": 12345}}
    ]
    back = clone.read_object(clone.write_object(fields, _tree()), _tree())
    assert back["m_NonModifiableTextures"] == fields["m_NonModifiableTextures"]
    assert sb.non_modifiable_texture_pptrs_of(back) == ((0, 12345),)


# --------------------------------------------------------------------------- #
# B · 正控（真件字节）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path_id", FIXTURE_IDS, ids=[NAMES[p].rsplit("/", 1)[-1] for p in FIXTURE_IDS]
)
def test_recorded_shaders_roundtrip_and_readings(path_id: int) -> None:
    """真件 Shader 读一遍写一遍逐字节相同，读数与录的一致（先自证地基再谈搬运）。"""
    raw = _raw(path_id)
    assert len(raw) == SIZES[path_id]
    fields = clone.read_object(raw, _tree())
    assert clone.write_object(fields, _tree()) == raw
    assert fields["m_Name"] == "", "真件 29/29 的 m_Name 都是空串（这就是要找 m_ParsedForm 的原因）"
    facts = sb.ShaderFacts(
        path_id=path_id,
        parsed_name=str(fields["m_ParsedForm"]["m_Name"]),
        plain_name=str(fields["m_Name"]),
        byte_size=len(raw),
        dependency_pptrs=sb.dependency_pptrs_of(fields),
    )
    assert facts.parsed_name == NAMES[path_id]
    assert facts.dependency_pptrs == DEPS[path_id]
    assert facts.non_modifiable_pptrs == ()
    assert len(fields["compressedBlob"]) == BLOBS[path_id]
    assert tuple(len(row) for row in fields["offsets"]) == GROUPS[path_id]


def test_shader_name_matches_material_build() -> None:
    """名字判读**逻辑只有一份**：`material_build.shader_name_of» 与本模块给的是同一个值。"""
    for pid in FIXTURE_IDS:
        fields = _fields(pid)
        assert material_build.shader_name_of(fields) == NAMES[pid]


# --------------------------------------------------------------------------- #
# C · 名字索引
# --------------------------------------------------------------------------- #
def test_inventory_reads_names_from_parsed_form(source_bundle: Path) -> None:
    inv = sb.read_shader_inventory(source_bundle)
    assert len(inv.facts) == len(FIXTURE_IDS)
    assert inv.resolve("HDRP/Lit").path_id == LIT
    assert sorted(inv.names()) == sorted(NAMES.values())
    for facts in inv.facts:
        assert facts.parsed_name == NAMES[facts.path_id]
        assert facts.plain_name == ""
        assert facts.dependency_pptrs == DEPS[facts.path_id]
        assert facts.blob_bytes == BLOBS[facts.path_id]
        assert facts.group_segment_counts == GROUPS[facts.path_id]
        assert facts.cross_package_dependency_pptrs == ()


def test_inventory_json_roundtrip(source_bundle: Path) -> None:
    inv = sb.read_shader_inventory(source_bundle)
    again = sb.ShaderInventory.from_json(inv.to_json())
    assert again.bundle == inv.bundle
    assert again.facts == inv.facts
    assert again.resolve("HDRP/Lit") == inv.resolve("HDRP/Lit")


def test_neg_unknown_name_lists_candidates(source_bundle: Path) -> None:
    inv = sb.read_shader_inventory(source_bundle)
    with pytest.raises(AssetError, match="没有解析名") as excinfo:
        inv.resolve("No/Such Shader")
    assert "HDRP/Lit" in str(excinfo.value), "报错里要列出包内认得的名字（⛔ 不给「我猜是它」）"


def test_neg_inventory_from_another_bundle(source_bundle: Path, tmp_path: Path) -> None:
    inv = sb.read_shader_inventory(source_bundle)
    other = _build_source(tmp_path / "other.bundle")
    with pytest.raises(sb.ShaderBuildError, match="不是"):
        sb.copy_shaders_from_bundle(other, names=["HDRP/Lit"], inventory=inv)


# --------------------------------------------------------------------------- #
# D · 依赖闭包 + PPtr 重映射
# --------------------------------------------------------------------------- #
def test_copy_closure_renumber(source_bundle: Path) -> None:
    """`HDRP/Lit» 的闭包 = 3 个（自己 + 两个 FallbackError），一条悬空都不许有。"""
    got = sb.copy_shaders_from_bundle(source_bundle, names=["HDRP/Lit"])
    assert got.problems == (), got.problems
    assert got.ok
    assert tuple(f.path_id for f in got.facts) == CLOSURE_OF_LIT
    assert set(got.new_id_of) == set(CLOSURE_OF_LIT)
    for old in CLOSURE_OF_LIT:
        assert got.new_id_of[old] == builder.path_id_for("shader|" + NAMES[old]), (
            "新 pathID 的算法是口径的一部分"
        )
    assert got.root_path_ids == (got.new_id_of[LIT],)
    assert got.roots == (("HDRP/Lit", got.new_id_of[LIT]),)
    # payload / fields 的分工：自己不带包内引用的那条走 payload（逐字节搬）
    assert got.encoding_of(HDRP_FB) == "fields", "它的 m_Dependencies 指到了被重编号的 CORE_FB"
    assert got.encoding_of(LIT) == "fields", "它的 m_Dependencies 指到了两个被重编号的 shader"
    assert got.encoding_of(CORE_FB) == "payload", "它一条包内引用都没有 => 原样搬字节"
    assert [b.ok for b in got.blobs] == [True] * 3
    assert all("悬空普查" in line for line in got.report if "悬空" in line)


def test_remapped_dependencies_point_inside_the_new_closure(source_bundle: Path) -> None:
    got = sb.copy_shaders_from_bundle(source_bundle, names=["HDRP/Lit"])
    produced = set(got.new_path_ids)
    for spec in got.objects:
        fields = spec.fields
        if fields is None:
            # payload：它必须**一条包内引用都没有** —— 这正是「payload 安全」的判据
            pid = next(old for old, new in got.new_id_of.items() if new == int(spec.path_id))
            others = _fields(pid)
            assert sb.dependency_pptrs_of(others) == ()
            assert sb.non_modifiable_texture_pptrs_of(others) == ()
            continue
        for _where, file_id, target in builder.iter_pptrs(fields):
            assert file_id == 0
            assert target == 0 or target in produced, f"重映射漏了一条：{target}"


def test_copy_by_path_ids_does_not_scan(source_bundle: Path) -> None:
    """点名 pathID 只解那几个 —— 全扫才有 on_progress 回调（省掉 29 个都解一遍的 23 s）。"""
    seen: list[tuple[int, int, str]] = []
    got = sb.copy_shaders_from_bundle(
        source_bundle, path_ids=[CORE_FB], on_progress=lambda *a: seen.append(a)
    )
    assert seen == [], "给了 path_ids 就不该去全扫"
    assert len(got.objects) == 1
    assert got.encoding_of(CORE_FB) == "payload"
    assert got.roots == (("Hidden/Core/FallbackError", got.new_id_of[CORE_FB]),)


def test_scan_reports_progress(source_bundle: Path) -> None:
    seen: list[tuple[int, int, str]] = []
    sb.copy_shaders_from_bundle(
        source_bundle, names=["HDRP/Lit"], on_progress=lambda *a: seen.append(a)
    )
    assert [n for n, _total, _name in seen] == [1, 2, 3, 4, 5]
    assert all(total == 5 for _n, total, _name in seen)


# --------------------------------------------------------------------------- #
# E · 两条 pathID 口径
# --------------------------------------------------------------------------- #
def test_preserve_policy_keeps_source_ids_and_all_payload(source_bundle: Path) -> None:
    got = sb.copy_shaders_from_bundle(source_bundle, names=["HDRP/Lit"], id_policy="preserve")
    assert got.problems == (), got.problems
    assert got.new_id_of == {pid: pid for pid in CLOSURE_OF_LIT}
    assert [int(o.path_id) for o in got.objects] == list(CLOSURE_OF_LIT)
    assert {how for _pid, how in got.encodings} == {"payload"}, "恒等映射 => 一个字节都不重编码"
    assert all(o.payload is not None and o.fields is None for o in got.objects)
    for spec in got.objects:
        old = int(spec.path_id)
        assert spec.payload == _raw(old), "沿用源 pathID 时 payload 必须与源对象字节逐字节相同"


def test_neg_bad_policy_and_empty_selection(source_bundle: Path) -> None:
    with pytest.raises(sb.ShaderBuildError, match="id_policy"):
        sb.copy_shaders_from_bundle(source_bundle, path_ids=[LIT], id_policy="换一个")
    with pytest.raises(sb.ShaderBuildError, match="至少要给一个"):
        sb.copy_shaders_from_bundle(source_bundle)


def test_neg_reserved_id_collision(source_bundle: Path) -> None:
    new_lit = builder.path_id_for("shader|HDRP/Lit")
    with pytest.raises(sb.ShaderBuildError, match="撞了"):
        sb.copy_shaders_from_bundle(
            source_bundle, names=["HDRP/Lit"], reserved_path_ids=[new_lit]
        )
    with pytest.raises(sb.ShaderBuildError, match="撞了"):
        sb.copy_shaders_from_bundle(
            source_bundle, path_ids=[LIT], id_policy="preserve", reserved_path_ids=[LIT]
        )


def test_neg_path_id_that_is_not_a_shader(source_bundle: Path) -> None:
    with pytest.raises(AssetError, match="不是 Shader"):
        sb.copy_shaders_from_bundle(source_bundle, path_ids=[1])
    with pytest.raises(AssetError, match="不在"):
        sb.copy_shaders_from_bundle(source_bundle, path_ids=[424242424242])


# --------------------------------------------------------------------------- #
# F · 端到端：extra -> assemble_unit_bundle -> 落包 -> 独立读回
# --------------------------------------------------------------------------- #
def test_end_to_end_assemble_and_independent_readback(source_bundle: Path, tmp_path: Path) -> None:
    got = sb.copy_shaders_from_bundle(source_bundle, names=["HDRP/Lit"])
    assert got.ok, got.problems
    mesh_pid = builder.path_id_for("w2probe|mesh")
    mat_pid = builder.path_id_for("w2probe|material")
    # 材质：贴图槽一律置空（本用例的主体是 shader 闭包，贴图另有模块）
    mat_src = _fields(MAT_SRC, 21)
    slots = {
        str(row["first"]): 0 for row in (mat_src["m_SavedProperties"]["m_TexEnvs"] or ())
    }
    mat_fields = material_build.material_fields_from_source(
        mat_src,
        table=materials.table_for("HDRP/Lit"),
        shader="HDRP/Lit",
        shader_path_id=got.new_id_of[LIT],
        texture_path_ids=slots,
    )
    spec = prefab_build.PrefabNode(
        name="W2ShaderProbe",
        children=(
            prefab_build.PrefabNode(
                name="Chassis",
                kind=prefab_build.KIND_MESH,
                mesh=mesh_pid,
                materials=(mat_pid,),
            ),
        ),
    )
    extra = [
        builder.ObjectSpec(43, None, mesh_pid, payload=_raw(MESH_SRC, 43), label="mesh"),
        builder.ObjectSpec(21, mat_fields, mat_pid, label="material"),
        *got.objects,
    ]
    res = unit_bundle.assemble_unit_bundle(
        meta=_meta(),
        spec=spec,
        extra=extra,
        container_path=PROBE_PATH,
        bundle_name="w2shaderprobe00000000000000000000.bundle",
        block_type=builder.BLOCK_NONE,
    )
    assert res.problems == (), res.problems
    assert res.ok
    out = tmp_path / res.bundle_name
    out.write_bytes(res.plan.data)

    # ① payload 的 shader：对象字节逐字节 == 源
    payload_expect = {
        got.new_id_of[old]: _raw(old)
        for old, how in got.encodings
        if how == "payload"
    }
    assert payload_expect, "本闭包里必须至少有一条走 payload（CORE_FB）"
    assert _payload_mismatches(out, payload_expect) == []

    # ② fields 的 shader：compressedBlob 逐字节 == 源（重映射只动 PPtr）
    blob_expect = {
        got.new_id_of[old]: bytes(_fields(old)["compressedBlob"])
        for old, how in got.encodings
        if how == "fields"
    }
    assert blob_expect
    assert _blob_mismatches(out, blob_expect) == []

    # ③ 全包悬空 0
    assert _dangling(out) == []

    # ④ 材质指着我们的新 shader；shader 的 m_Dependencies 指着闭包内的新 pathID
    table, objs = _dump(out)
    mat = clone.read_object(objs[mat_pid][1], table.entry_for(21).nodes)
    assert mat["m_Shader"] == {"m_FileID": 0, "m_PathID": got.new_id_of[LIT]}
    for old, how in got.encodings:
        if how != "fields":
            continue
        fields = clone.read_object(objs[got.new_id_of[old]][1], table.entry_for(48).nodes)
        deps = [(int(d["m_FileID"]), int(d["m_PathID"])) for d in fields["m_Dependencies"]]
        assert deps == [(0, got.new_id_of[target]) for _f, target in DEPS[old]]
    assert {cid for cid, _blob in objs.values()} >= {1, 21, 43, 48, 142}


# --------------------------------------------------------------------------- #
# G · 负控（喂坏样本必须红）
# --------------------------------------------------------------------------- #
def test_neg_one_flipped_byte_makes_the_readback_ruler_red(
    source_bundle: Path, tmp_path: Path
) -> None:
    """payload 的**一个字节**被改 -> 逐字节读回必须红（先正控：原样搬是绿的）。"""
    got = sb.copy_shaders_from_bundle(source_bundle, path_ids=[CORE_FB], id_policy="preserve")
    want = {CORE_FB: _raw(CORE_FB)}
    good = _write_bundle(tmp_path / "w2good.bundle", got.objects, container_pid=CORE_FB)
    assert _payload_mismatches(good, want) == [], "正控不绿 -> 尺子有问题"

    spec = got.objects[0]
    assert spec.payload is not None
    bad_spec = builder.ObjectSpec(
        48, None, int(spec.path_id), payload=_flip(spec.payload, 10), label="tampered"
    )
    bad = _write_bundle(tmp_path / "w2bad.bundle", [bad_spec], container_pid=CORE_FB)
    red = _payload_mismatches(bad, want)
    assert red and "不逐字节相同" in red[0], f"改了一个字节却没红：{red}"


def test_lz4_block_has_no_checksum(
    source_bundle: Path, tmp_path: Path
) -> None:
    """⚠ **实测口径**：LZ4 块没有校验和 => 段中间的字面量字节被改，blob 尺子**照样绿**。

    所以字节级判据只能是**逐字节读回**（下一条用例证明它抓得到）。这条用例把两个读数都钉住 ——
    哪天把 blob 换成带校验和的格式，它会红，那时该改的是这条注释与判据的分工。
    """
    fields = _fields(LIT)
    off = int(fields["offsets"][0][0])
    clen = int(fields["compressedLengths"][0][0])
    mid = (off + clen) // 2
    tampered = copy.deepcopy(fields)
    tampered["compressedBlob"][mid] ^= 0x01
    assert sb.check_shader_blob(tampered).ok, "实测：段中间的字节改了，LZ4 照样解出同样长度"
    # 段首那个 token 字节就不一样了（改它必红）
    head = copy.deepcopy(fields)
    head["compressedBlob"][off] ^= 0x01
    assert not sb.check_shader_blob(head).ok
    del tmp_path


def test_neg_blob_byte_flip_is_caught_by_readback(source_bundle: Path, tmp_path: Path) -> None:
    """同一个「改一个字节」：blob 尺子看不见，**逐字节读回**抓得到。"""
    got = sb.copy_shaders_from_bundle(source_bundle, path_ids=[LIT])
    spec = next(o for o in got.objects if int(o.path_id) == got.new_id_of[LIT])
    assert spec.fields is not None
    fields = dict(spec.fields)
    blob = list(fields["compressedBlob"])
    mid = (int(fields["offsets"][0][0]) + int(fields["compressedLengths"][0][0])) // 2
    blob[mid] ^= 0x01
    fields["compressedBlob"] = blob
    bad = _write_bundle(
        tmp_path / "w2badblob.bundle",
        [builder.ObjectSpec(48, fields, int(spec.path_id), label="tampered")],
        container_pid=int(spec.path_id),
    )
    src_blob = bytes(_fields(LIT)["compressedBlob"])
    red = _blob_mismatches(bad, {got.new_id_of[LIT]: src_blob})
    assert red and "不逐字节相同" in red[0], f"blob 被改了一个字节却没红：{red}"


def _patch_decompressed_plus_one(fields: dict) -> None:
    fields["decompressedLengths"][0][0] = int(fields["decompressedLengths"][0][0]) + 1


def _patch_compressed_plus_one(fields: dict) -> None:
    fields["compressedLengths"][0][0] = int(fields["compressedLengths"][0][0]) + 1


def _patch_offset_plus_one(fields: dict) -> None:
    fields["offsets"][0][0] = int(fields["offsets"][0][0]) + 1


def _patch_offset_out_of_range(fields: dict) -> None:
    fields["offsets"][0][0] = len(fields["compressedBlob"])


def _patch_offset_negative(fields: dict) -> None:
    fields["offsets"][0][0] = -1


def _patch_blob_truncated(fields: dict) -> None:
    fields["compressedBlob"] = list(fields["compressedBlob"])[:100]


def _patch_blob_head_byte(fields: dict) -> None:
    fields["compressedBlob"][0] = int(fields["compressedBlob"][0]) ^ 0x01


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        (_patch_decompressed_plus_one, "!="),
        (_patch_compressed_plus_one, "解不开"),
        (_patch_offset_plus_one, "解不开"),
        (_patch_offset_out_of_range, "越界"),
        (_patch_offset_negative, "越界"),
        (_patch_blob_truncated, "越界"),
        (_patch_blob_head_byte, "!="),
    ],
    ids=[
        "decompressed+1",
        "compressed+1",
        "offset+1",
        "offset=len",
        "offset=-1",
        "blob截断",
        "段首字节^1",
    ],
)
def test_neg_blob_ruler_catches_tampering(patch: Callable[[dict], None], needle: str) -> None:
    """改长度 / 改偏移 / 截断 blob：blob 尺子必须红（这一族**都能红**，逐条实测过）。"""
    fields = _fields(LIT)
    patch(fields)
    got = sb.check_shader_blob(fields, path_id=LIT, name="HDRP/Lit")
    assert not got.ok, f"{patch.__name__} 改了却没红"
    assert any(needle in line for line in got.failures), got.failures


@pytest.mark.parametrize(
    "patch",
    [
        lambda f: f["compressedLengths"][0].pop(),
        lambda f: f["offsets"].clear(),
        lambda f: f.pop("compressedBlob"),
        lambda f: f.__setitem__("offsets", 3),
    ],
    ids=["少一段", "offsets清空", "删blob", "offsets不是数组"],
)
def test_neg_blob_structure_contradiction_raises(patch: Callable[[dict], None]) -> None:
    """三条数组自相矛盾 / 根本不是数组 -> **抛**（⛔ 不静默绿）。"""
    fields = _fields(LIT)
    patch(fields)
    with pytest.raises(sb.ShaderBuildError):
        sb.check_shader_blob(fields, path_id=LIT, name="HDRP/Lit")


def test_neg_dependency_outside_the_package_raises(tmp_path: Path) -> None:
    def patch(fields: dict) -> None:
        fields["m_Dependencies"] = [{"m_FileID": 0, "m_PathID": 999_999_999_999}]

    src = _build_source(tmp_path / "w2baddep.bundle", patches={CORE_FB: patch})
    with pytest.raises(AssetError, match="不在这个包里"):
        sb.copy_shaders_from_bundle(src, path_ids=[CORE_FB])


def test_neg_cross_package_dependency_is_reported(tmp_path: Path) -> None:
    def patch(fields: dict) -> None:
        fields["m_Dependencies"] = [{"m_FileID": 1, "m_PathID": 424242}]

    src = _build_source(tmp_path / "w2crossdep.bundle", patches={CORE_FB: patch})
    got = sb.copy_shaders_from_bundle(src, path_ids=[CORE_FB])
    assert not got.ok
    assert any("跨包" in line for line in got.problems), got.problems
    assert any("外部 fileID" in line for line in got.problems), got.problems


def test_neg_non_modifiable_textures_are_reported(tmp_path: Path) -> None:
    def patch(fields: dict) -> None:
        fields["m_NonModifiableTextures"] = [
            {"first": "Layer_00000000", "second": {"m_FileID": 0, "m_PathID": 12345}}
        ]

    src = _build_source(tmp_path / "w2nonmod.bundle", patches={CORE_FB: patch})
    got = sb.copy_shaders_from_bundle(src, path_ids=[CORE_FB])
    assert not got.ok, "带非可变贴图的 shader 搬进新包必然悬空，必须红"
    assert any("m_NonModifiableTextures" in line for line in got.problems), got.problems
    assert any("12345" in line for line in got.problems), got.problems
    assert got.facts[0].non_modifiable_pptrs == ((0, 12345),)


# --------------------------------------------------------------------------- #
# W1 接缝的只读探针（在我们自己造的 UnityFS 包上验；真实 Unity 产物待 W1 出包）
# --------------------------------------------------------------------------- #
def test_from_unity_bundle_probe(source_bundle: Path) -> None:
    probe = sb.from_unity_bundle(source_bundle, "HDRP/Lit")
    assert probe.facts.path_id == LIT
    assert probe.facts.parsed_name == "HDRP/Lit"
    assert probe.blob.ok and probe.ok
    assert len(probe.inventory.facts) == len(FIXTURE_IDS)
    assert any("HDRP/Lit" in line for line in probe.lines())
    with pytest.raises(AssetError, match="没有解析名"):
        sb.from_unity_bundle(source_bundle, "No/Such Shader")


def test_neg_bundle_without_shaders_raises(tmp_path: Path) -> None:
    """包里一个 classID 48 都没有 -> 抛，且报出「认得的名字是空」而不是硬报一个名字。"""
    mesh_pid = builder.path_id_for("w2probe|onlymesh")
    spec = builder.ObjectSpec(43, None, mesh_pid, payload=_raw(MESH_SRC, 43), label="mesh")
    only = _write_bundle(tmp_path / "w2onlymesh.bundle", [spec], container_pid=mesh_pid)
    with pytest.raises(AssetError, match="没有解析名"):
        sb.from_unity_bundle(only, "HDRP/Lit")
