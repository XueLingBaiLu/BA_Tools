r"""W4 · Avatar(90) 解析 / 复制 / 人形判定 —— 判据（含正负控）。

**判据一行**：pytest tests/test_build_avatar.py -q -n 0 -> 通过数 0 -> N，
其中至少 7 条是**负控**（喂坏样本必须变红才算尺子，AGENTS §0.9 动作 2）。

## 为什么用冻结夹具，而不是 units 包

units 是 3.38 GB，**不许进用例**（同 tests/test_build_unit_bundle.py 的理由）。
夹具 = _work/avatar-line/fixtures/（生成脚本 _work/avatar-line/ 下的
p3_freeze.py · p5_extent.py · p9_animators.py）：

| 文件 | 里面是什么 | 谁算的 |
|---|---|---|
| avatars_payloads.bin | 16 条 Avatar 的**原始对象字节**（305,528 B） | p3（开 units，只读） |
| avatars_index.json | 16 条读数表（键集合 / 长度 / 哈希 / PPtr） | p3（**原始 API**，非产品模块） |
| avatar_field_widths.json | 每个顶层字段的字节宽度（前缀子树长度差） | p5（独立尺子） |
| animators_avatar_pptr.json | 430 个 Animator 的 m_Avatar 分布 | p9 |

类型树：_work/w10_ref/units_meta.bin（4,129,040 B，units 的元数据区冻结件）。
**夹具缺一 → skip**（与 test_build_unit_bundle.py 同一姿势），⛔ 不许悄悄跳过断言。

## 各段量什么

| 段 | 判据 |
|---|---|
| A | 16/16 往返逐字节 · 键集合 16/16 一致 · 读数与**独立**夹具表逐项相等 |
| B | 人形判定：**15 humanoid 正控 + 1 generic 负控**（WoodenBoxAvatar）；字面口径被否决可复算 |
| C | m_Avatar 内部 blob 的长度/哈希；m_AvatarSize **不等于**它（0/16，语义未定 ⇒ 两个读数都给） |
| D | 复制：fields 路自证逐字节 · payload 路字节照搬 · PPtr 重映射**正控** |
| E | 装进 unit_bundle.assemble_unit_bundle：悬空 0 · 430 个 Animator 的 m_Avatar 分布 |
| F | **负控 7 条**（结构字节 / 哈希尺子 / 悬空 PPtr / 键集合 / 拿错树 / 宽度自证 / mode 冲突） |
| G | 接缝：from_unity_avatar 必须**抛**（W1 的产物还没到） |
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import avatar_build, builder, clone, prefab_build, unit_bundle
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

_REPO = Path(__file__).resolve().parents[1]
_FIXTURES = WORK / "avatar-line" / "fixtures"
_UNITS_META = WORK / "w10_ref" / "units_meta.bin"

#: 夹具里的数值列 ↔ AvatarFacts 的属性名（逐项对，一个都不许漏）
_NUMERIC = (
    ("declared_size", "declared_size"),
    ("skeleton_nodes", "skeleton_nodes"),
    ("skeleton_ids", "skeleton_ids"),
    ("skeleton_axes", "skeleton_axes"),
    ("skeleton_pose", "skeleton_pose"),
    ("default_pose", "default_pose"),
    ("root_motion_nodes", "root_motion_nodes"),
    ("name_id_array", "name_id_array"),
    ("human_skeleton_index", "human_skeleton_index"),
    ("human_skeleton_rev_index", "human_skeleton_rev_index"),
    ("root_motion_bone_index", "root_motion_bone_index"),
    ("human_bones", "human_bones"),
    ("skeleton_bones", "skeleton_bones"),
    ("tos_entries", "tos_entries"),
    ("human_bone_index", "human_bone_index"),
    ("human_bone_mass", "human_bone_mass"),
    ("has_left_hand", "has_left_hand"),
    ("has_right_hand", "has_right_hand"),
    ("has_tdof", "has_tdof"),
)


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"缺 {path}（先跑 _work/avatar-line 下的 p3_freeze/p5_extent/p9_animators）")
    return path


def _load_json(path: Path) -> dict:
    return json.loads(_require(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def type_table() -> builder.RawTypeTable:
    meta = _require(_UNITS_META).read_bytes()
    return builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())


@pytest.fixture(scope="module")
def nodes(type_table: builder.RawTypeTable) -> tuple[clone.TypeNode, ...]:
    return type_table.entry_for(avatar_build.CLASS_AVATAR).nodes


@pytest.fixture(scope="module")
def samples(nodes) -> tuple[tuple[dict, bytes, avatar_build.AvatarFacts], ...]:
    """(夹具行, 原始字节, 产品读数) —— 顺序 = 夹具顺序（按 byte_start 排）。"""
    index = _load_json(_FIXTURES / "avatars_index.json")
    blob = _require(_FIXTURES / "avatars_payloads.bin").read_bytes()
    rows = index["rows"]
    assert len(rows) == 16, f"夹具里应当有 16 个 Avatar，实际 {len(rows)}"
    out = []
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        assert len(raw) == row["length"]
        out.append((row, raw, avatar_build.read_avatar(raw, nodes, path_id=row["path_id"])))
    return tuple(out)


def _structural_tamper(raw: bytes, nodes) -> bytes:
    """把 **m_TOS 的 size 高字节**改掉 —— 位置由**产品自己的尺子**（field_widths）算出来。

    ⚠ 为什么不是「随便改一个字节」：float 的 LSB 改了照样是一个合法 Avatar（读得出、写得回），
    那种字节**改不掉**，不该拿它当负控。结构位置的字节才是「必须红」的那一类。
    """
    widths = dict(avatar_build.field_widths(clone.read_object(raw, nodes), nodes))
    offset = widths["m_Name"] + widths["m_AvatarSize"] + widths["m_Avatar"]
    out = bytearray(raw)
    out[offset + 3] ^= 0x01
    return bytes(out)


# --------------------------------------------------------------------------- #
# A · 往返 / 键集合 / 读数对账
# --------------------------------------------------------------------------- #
def test_roundtrip_is_byte_exact_for_all_16(samples) -> None:
    bad = [row["name"] for row, _raw, facts in samples if not facts.roundtrip_equal]
    assert bad == [], f"往返不逐字节相等的 Avatar：{bad}"
    assert len(samples) == 16


def test_key_sets_match_the_real_thing_16_of_16(samples) -> None:
    assert list(avatar_build.AVATAR_TOP_KEYS) == [
        "m_Name",
        "m_AvatarSize",
        "m_Avatar",
        "m_TOS",
        "m_HumanDescription",
    ]
    # ★ 真件的 Avatar **没有 m_GameObject** —— 这条断言就是「键集合一个不多一个不少」的尺子
    assert "m_GameObject" not in avatar_build.AVATAR_TOP_KEYS
    assert len(avatar_build.AVATAR_CONSTANT_KEYS) == 12
    assert len(avatar_build.HUMAN_DESCRIPTION_KEYS) == 14
    for _row, _raw, facts in samples:
        assert facts.keys_are_canonical, facts.name
        assert set(facts.avatar_keys) == set(avatar_build.AVATAR_CONSTANT_KEYS)
        assert set(facts.human_description_keys) == set(avatar_build.HUMAN_DESCRIPTION_KEYS)
    report = avatar_build.key_set_report([f for _r, _b, f in samples])
    assert report[-1].startswith("键集合一致 16/16"), report[-1]


def test_facts_match_the_independent_fixture_table(samples) -> None:
    for row, raw, facts in samples:
        assert facts.name == row["name"]
        assert facts.path_id == row["path_id"]
        assert facts.object_size == len(raw) == row["length"]
        assert facts.payload_sha256 == row["sha256"]
        assert list(facts.top_keys) == row["top_keys"]
        assert list(facts.avatar_keys) == row["avatar_keys"]
        assert list(facts.human_description_keys) == row["human_description_keys"]
        assert facts.pptr_count == len(row["pptrs"]) == 0
        for attr, key in _NUMERIC:
            assert getattr(facts, attr) == row[key], (row["name"], attr)


# --------------------------------------------------------------------------- #
# B · 人形 / generic（正控 + 负控）
# --------------------------------------------------------------------------- #
def test_humanoid_verdict_matches_the_fixture_for_all_16(samples) -> None:
    for row, _raw, facts in samples:
        want = avatar_build.KIND_GENERIC if row["human_bones"] == 0 else avatar_build.KIND_HUMANOID
        assert facts.kind.kind == want, row["name"]
    humanoid = [f.name for _r, _b, f in samples if f.kind.is_humanoid]
    generic = [f.name for _r, _b, f in samples if not f.kind.is_humanoid]
    assert len(humanoid) == 15
    assert len(generic) == 1


def test_woodenbox_is_the_only_generic_avatar(samples) -> None:
    """★ 负控那一侧：真件 16 个里**恰好一个** generic，而且它就是那个木箱的 Avatar。"""
    generic = [
        (row["name"], row["human_bones"], row["tos_entries"])
        for row, _raw, facts in samples
        if not facts.kind.is_humanoid
    ]
    assert generic == [("WoodenBoxAvatar", 0, 1)]


def test_literal_three_way_rule_is_rejected_and_only_disagrees_on_the_box(samples) -> None:
    """字面口径（m_HumanDescription / m_TOS / m_Skeleton 三取一非空）**已否决** —— 这里让它可复算。

    它只在**一个**点上与裁定分歧：WoodenBoxAvatar（m_TOS=1 ⇒ 它说 humanoid）。
    这条断言的作用是：下一个会话想退回字面口径时，**当场红**。
    """
    disagree = [
        (f.name, f.kind.literal_rule_kind, f.kind.kind)
        for _r, _b, f in samples
        if f.kind.literal_rule_kind != f.kind.kind
    ]
    assert disagree == [("WoodenBoxAvatar", avatar_build.KIND_HUMANOID, avatar_build.KIND_GENERIC)]


def test_tos_entries_equal_skeleton_node_count_in_all_16(samples) -> None:
    """★ m_TOS 是「骨架节点 id → 路径名」的映射表，**不是人形标志**：条数恒等于骨架节点数。"""
    for _row, _raw, facts in samples:
        assert facts.tos_entries == facts.skeleton_nodes, facts.name


def test_skeleton_bones_never_disagree_with_human_bones(samples) -> None:
    """交叉校验（**不单独当判据**）：m_Skeleton 非空与 m_Human 非空在真件 16/16 同真同假。"""
    assert all(f.kind.cross_check_ok for _r, _b, f in samples)


def test_classify_reports_the_three_counts(samples) -> None:
    _row, _raw, facts = samples[0]
    kind = avatar_build.classify_avatar(facts)
    assert kind.human_bones == facts.human_bones
    assert kind.skeleton_bones == facts.skeleton_bones
    assert kind.tos_entries == facts.tos_entries
    assert kind.skeleton_nodes == facts.skeleton_nodes
    assert "人骨" in kind.line() and "TOS" in kind.line()


# --------------------------------------------------------------------------- #
# C · m_Avatar 内部 blob（长度 / 哈希）与 m_AvatarSize 的**未定**语义
# --------------------------------------------------------------------------- #
def test_avatar_blob_length_matches_the_independent_width_ruler(samples, nodes) -> None:
    widths = {w["name"]: w for w in _load_json(_FIXTURES / "avatar_field_widths.json")}
    for row, raw, facts in samples:
        assert facts.avatar_blob_length == widths[row["name"]]["w_avatar"], row["name"]
        again = avatar_build.avatar_blob_bytes(clone.read_object(raw, nodes), nodes)
        assert len(again) == facts.avatar_blob_length


def test_field_widths_sum_to_the_object_size(samples, nodes) -> None:
    for _row, raw, _facts in samples:
        widths = avatar_build.field_widths(clone.read_object(raw, nodes), nodes)
        assert [name for name, _w in widths] == list(avatar_build.AVATAR_TOP_KEYS)
        assert sum(w for _n, w in widths) == len(raw)


def test_declared_avatar_size_is_not_the_serialized_blob_length(samples) -> None:
    """★ 如实记：m_AvatarSize 的语义**本轮未定** —— 实测 **0/16** 等于 m_Avatar 的序列化字节数。

    所以复制时它**原样照搬、绝不重算**；读数里 declared_size 与 avatar_blob_length 两个都给。
    """
    equal = [f.name for _r, _b, f in samples if f.declared_size == f.avatar_blob_length]
    assert equal == [], f"两条读数在这一版真件里不相等；相等的：{equal}"
    deltas = {f.declared_size - f.avatar_blob_length for _r, _b, f in samples}
    assert len(deltas) >= 5, f"差值应当有多个档位（否则这条断言接近恒真）：{sorted(deltas)}"
    assert -120 in deltas and 2264 in deltas


# --------------------------------------------------------------------------- #
# D · 复制（照搬 + PPtr 重映射）
# --------------------------------------------------------------------------- #
def test_copy_fields_mode_reproduces_the_original_bytes(samples, nodes) -> None:
    for row, raw, _facts in samples:
        spec = avatar_build.copy_avatar(raw, nodes, path_id=row["path_id"], label=row["name"])
        assert spec.class_id == avatar_build.CLASS_AVATAR
        assert spec.fields is not None and spec.payload is None
        assert clone.write_object(dict(spec.fields), nodes) == raw, row["name"]


def test_copy_payload_mode_is_byte_identical_and_has_no_field_tree(samples, nodes) -> None:
    row, raw, _facts = samples[0]
    spec = avatar_build.copy_avatar(raw, nodes, path_id=row["path_id"], mode="payload")
    assert spec.fields is None
    assert spec.payload == raw
    # 尺子口径（模块头边界 2）：payload-only 的对象 dangling_pptrs **普查不到** —— 如实钉住
    assert builder.dangling_pptrs([spec], assetbundle_path_id=1) == ()


def test_remap_moves_a_planted_pptr(samples, nodes) -> None:
    """**正控**：真件 Avatar 的 PPtr 是 0 条 ⇒ 只能**植入**一条来证明重映射机制活着。"""
    _row, raw, _facts = samples[0]
    fields = clone.read_object(raw, nodes)
    assert list(builder.iter_pptrs(fields)) == []
    fields["m_HumanDescription"]["m_Human"][0]["m_Limit"] = {"m_FileID": 0, "m_PathID": 123456789}
    moved = avatar_build.remap_avatar_pptrs(fields, {123456789: 987654321})
    assert moved["m_HumanDescription"]["m_Human"][0]["m_Limit"]["m_PathID"] == 987654321
    # 映射表里没有的 pathID **原样保留**（⛔ 不静默改成 0）
    kept = avatar_build.remap_avatar_pptrs(fields, {1: 2})
    assert kept["m_HumanDescription"]["m_Human"][0]["m_Limit"]["m_PathID"] == 123456789


def test_avatar_has_zero_pptrs_in_the_real_thing(samples) -> None:
    assert all(f.pptr_count == 0 for _r, _b, f in samples)


def test_diff_avatars_is_empty_for_self_and_finds_the_generic_one(samples) -> None:
    facts = [f for _r, _b, f in samples]
    assert avatar_build.diff_avatars(facts[0], facts[0]) == ()
    box = next(f for f in facts if not f.kind.is_humanoid)
    lines = avatar_build.diff_avatars(facts[0], box)
    assert lines, "两个不同 Avatar 的对拍不该是空的"
    assert any("m_Avatar 长度不同" in line for line in lines)
    assert any("人形判定不同" in line for line in lines)


# --------------------------------------------------------------------------- #
# E · 装进 unit_bundle（悬空 0）· Animator 那一头
# --------------------------------------------------------------------------- #
def test_avatar_goes_into_a_unit_bundle_with_zero_dangling(samples, nodes) -> None:
    row, raw, _facts = samples[0]
    spec = avatar_build.copy_avatar(raw, nodes, path_id=0x5100_0000_0000_0007, label="avatar")
    result = unit_bundle.assemble_unit_bundle(
        meta=_require(_UNITS_META).read_bytes(),
        spec=prefab_build.PrefabNode(name="RouteAAvatarProbe"),
        extra=[spec],
        container_path="Assets/Resources_moved/ModelPrefabs/RouteA/RouteAAvatarProbe.prefab",
        bundle_name="routea_avatar_probe_0000000000000000.bundle",
        extra_class_ids=(avatar_build.CLASS_AVATAR,),
    )
    assert result.problems == ()
    assert result.ok
    assert any("悬空 0 条" in line for line in result.report), result.report
    assert (avatar_build.CLASS_AVATAR, None) in result.plan.type_keys
    # GameObject + Transform + Avatar + AssetBundle —— 四个，一个不多一个不少
    assert result.plan.object_count == 4
    assert builder.dangling_pptrs([spec], assetbundle_path_id=1) == ()
    assert result.plan.container == (
        (
            "Assets/Resources_moved/ModelPrefabs/RouteA/RouteAAvatarProbe.prefab",
            result.root_game_object,
        ),
    )


def test_animator_avatar_pptr_distribution_is_the_real_thing() -> None:
    """★ 载具不需要 Avatar：真件 430 个 Animator 里 **204 个 m_Avatar == {0,0}**（验收合同 L1）。"""
    data = _load_json(_FIXTURES / "animators_avatar_pptr.json")
    assert data["class_id"] == avatar_build.CLASS_ANIMATOR == 95
    assert "m_Avatar" in data["animator_top_keys_sample"]
    assert data["total"] == 430
    assert data["null_pptr"] == 204
    referenced = set()
    for row in data["distribution"]:
        ref = (row["file_id"], row["path_id"])
        assert avatar_build.is_null_pptr(ref) == (row["path_id"] == 0)
        assert avatar_build.animator_avatar_pptr(
            {"m_Avatar": {"m_FileID": row["file_id"], "m_PathID": row["path_id"]}}
        ) == ref
        if row["path_id"] != 0:
            assert row["avatar_name"] is not None, "非空 m_Avatar 必须指向那 16 个之一"
            assert row["file_id"] == 0
            referenced.add(str(row["path_id"]))
    non_null = [r for r in data["distribution"] if r["path_id"] != 0]
    assert len(non_null) == 16
    assert referenced == set(data["avatars"])
    assert sum(r["count"] for r in non_null) == data["total"] - data["null_pptr"]


# --------------------------------------------------------------------------- #
# F · 负控（喂坏样本必须变红）
# --------------------------------------------------------------------------- #
def test_negative_control_structural_byte_flip_is_red(samples, nodes) -> None:
    """**负控 1**：把 m_TOS 的 size 高字节改一个 bit ⇒ 16/16 必须抛。"""
    for row, raw, _facts in samples:
        with pytest.raises(AssetError):
            avatar_build.read_avatar(_structural_tamper(raw, nodes), nodes, path_id=row["path_id"])


def test_negative_control_payload_hash_ruler_catches_any_single_byte_edit(samples, nodes) -> None:
    """**负控 2**：随便哪个字节改一个 bit，payload_sha256 这把尺子**必须**变（16/16）。"""
    for row, raw, facts in samples:
        tampered = bytearray(raw)
        tampered[int(len(tampered) * 0.33)] ^= 0x01
        try:
            other = avatar_build.read_avatar(bytes(tampered), nodes, path_id=row["path_id"])
        except AssetError:
            continue  # 解析就炸了，那更是红了
        assert other.payload_sha256 != facts.payload_sha256, row["name"]
        assert other.payload_sha256 != row["sha256"], row["name"]


def test_negative_control_planted_dangling_pptr_is_reported(samples, nodes) -> None:
    """**负控 3**：植入一条指向不存在 pathID 的 PPtr ⇒ dangling_pptrs 必须报。"""
    _row, raw, _facts = samples[0]
    fields = clone.read_object(raw, nodes)
    fields["m_HumanDescription"]["m_Human"][0]["m_Limit"] = {"m_FileID": 0, "m_PathID": 123456789}
    planted = builder.ObjectSpec(
        class_id=avatar_build.CLASS_AVATAR,
        fields=fields,
        path_id=0x5100_0000_0000_0008,
        label="planted",
    )
    problems = builder.dangling_pptrs([planted], assetbundle_path_id=1)
    assert len(problems) == 1
    assert "悬空" in problems[0]
    assert "123456789" in problems[0]


def test_negative_control_broken_key_set_is_refused(samples, nodes) -> None:
    """**负控 4**：键集合少一个 / 多一个都必须在出数之前抛（不许在键集合不对的树上给读数）。"""
    _row, raw, _facts = samples[0]
    missing = clone.read_object(raw, nodes)
    del missing["m_TOS"]
    with pytest.raises(AssetError, match="顶层键集合"):
        avatar_build.avatar_facts(missing, nodes)
    extra = clone.read_object(raw, nodes)
    extra["m_GameObject"] = {"m_FileID": 0, "m_PathID": 0}
    with pytest.raises(AssetError, match="顶层键集合"):
        avatar_build.avatar_facts(extra, nodes)


def test_negative_control_foreign_type_tree_is_refused(samples, nodes, type_table) -> None:
    """**负控 5**：拿错类的类型树（GameObject）⇒ 直接抛，⛔ 不许凑合出一张读数表。"""
    _row, raw, _facts = samples[0]
    fields = clone.read_object(raw, nodes)
    with pytest.raises(AssetError, match="没有这些顶层字段"):
        avatar_build.field_widths(fields, type_table.entry_for(1).nodes)


def test_negative_control_field_widths_self_check_on_an_unknown_top_field(
    samples, type_table
) -> None:
    """**负控 6**：类型树里多出一个我们不认识的顶层字段 ⇒ 前缀拼法**不自洽** ⇒ 抛。

    （这是「游戏换版本、Avatar 多了一个字段」那一天的正确行为：⛔ 不许静默给一个宽度。）
    """
    _row, raw, _facts = samples[0]
    nodes = type_table.entry_for(avatar_build.CLASS_AVATAR).nodes
    fields = clone.read_object(raw, nodes)
    tag = next(n for n in type_table.entry_for(1).nodes if n.level == 1 and n.name == "m_Tag")
    synthetic = tuple(nodes) + (
        clone.TypeNode(
            level=1,
            type=tag.type,
            name="m_Tag",
            byte_size=tag.byte_size,
            meta_flag=0,
            type_flags=0,
            version=1,
        ),
    )
    fields["m_Tag"] = 0
    with pytest.raises(AssetError, match="前缀拼法不自洽"):
        avatar_build.field_widths(fields, synthetic)


def test_negative_control_payload_mode_refuses_a_mapping(samples, nodes) -> None:
    """**负控 7**：字节照搬 + PPtr 重映射不能同真 —— 不许偷偷二选一。"""
    _row, raw, _facts = samples[0]
    with pytest.raises(AssetError, match="不能同时给"):
        avatar_build.copy_avatar(raw, nodes, path_id=1, mode="payload", mapping={1: 2})
    with pytest.raises(AssetError, match="不认"):
        avatar_build.copy_avatar(raw, nodes, path_id=1, mode="magic")


def test_negative_control_animator_without_avatar_field_is_refused() -> None:
    with pytest.raises(AssetError, match="m_Avatar"):
        avatar_build.animator_avatar_pptr({"m_Controller": {"m_FileID": 0, "m_PathID": 1}})


# --------------------------------------------------------------------------- #
# G · 接缝：与 W1（Unity 生成 Avatar）对拍 —— 入口留着，现在必须抛
# --------------------------------------------------------------------------- #
def test_from_unity_avatar_is_not_wired_yet() -> None:
    """★ 接缝口径：W1 的产物还没到 ⇒ 入口在、但**必须抛**，⛔ 不许猜字段填一个假读数。"""
    with pytest.raises(NotImplementedError, match="W1"):
        avatar_build.from_unity_avatar({})
