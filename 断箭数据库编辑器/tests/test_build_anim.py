r"""W6 · 动画写侧（AnimationClip / AnimatorController / Animator）—— 判据（含正负控）。

**判据一行**：pytest tests/test_build_anim.py -q -n 0 -> 通过数 0 -> N，
其中至少 8 条是**负控**（喂坏样本必须变红才算尺子，AGENTS §0.9 动作 2）。

## 为什么用冻结夹具，而不是 units 包

units 是 3.38 GB，**不许进用例**（同 tests/test_build_unit_bundle.py 的理由）。
夹具 = _work/anim-line/fixtures/（生成脚本在 _work/anim-line 下：
p2_census.py / p3_freeze.py / p3c_freeze_sam.py）：

| 文件 | 里面是什么 | 大小 |
|---|---|---|
| clips_payloads.bin + clips_index.json | 475 条 clip 原始字节 + 独立读数表 | 5,360,912 B |
| controllers_payloads.bin + controllers_index.json | 165 条 controller | 503,904 B |
| animators_payloads.bin + animators_index.json | 430 条 Animator | 24,080 B |
| sam_payloads.bin + sam_payloads_index.json | 225 条 SoldierAnimationManager | 18,000 B |

读数表是用 **clone/builder 原始 API** 算的（不是产品模块）⇒ 可以当**独立尺子**。
类型树：_work/w10_ref/units_meta.bin（units 的元数据区冻结件）。
**夹具缺一 → skip**（与 test_build_unit_bundle.py 同一姿势），⛔ 不许悄悄跳过断言。

## 各段量什么

| 段 | 判据 |
|---|---|
| A | 475/475 往返逐字节 · 键集合一致 · 读数与**独立**夹具表逐项相等 · 七张曲线表全空 |
| B | 三通道：全局曲线号派发 + 三条恒等式 + **三通道各一条读数** + 与研究线普查数吻合 |
| C | ★ delta 自证：求值(Start/Stop) == m_ValueArrayDelta ⇒ **23581/23581 = 100.0000%** |
| D | 复制：clip 475 / controller 165 / animator 430 的 fields 路逐字节自证 |
| E | 装进 unit_bundle.assemble_unit_bundle：悬空 0 |
| F | ★「12 字节 PPtr」：真件 225/225 字段宽度 = 12、对象内偏移 = 68（研究线真机那条改动） |
| G | 拉伸：词数不变 / 对象字节不变 / new(k*t) == old(t) / 哨兵时刻不动 |
| H | **负控 8 条**：结构字节 / 哈希尺子 / 曲线数声明 / dense 帧数 / 词表截断 /
curveIndex 越界 / 键集合 / mode 冲突 |
| I | Animator 边界：204/430 的 m_Avatar 与 4/430 的 m_Controller 是 {0,0} |
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import anim_build, builder, clone, prefab_build, unit_bundle
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

_REPO = Path(__file__).resolve().parents[1]
_FIXTURES = WORK / "anim-line" / "fixtures"
_UNITS_META = WORK / "w10_ref" / "units_meta.bin"

#: 夹具里的数值列 ↔ ClipFacts 的属性名
_NUMERIC = (
    ("sample_rate", "sample_rate"),
    ("wrap_mode", "wrap_mode"),
    ("declared_muscle_clip_size", "muscle_clip_size"),
    ("start_time", "start_time"),
    ("stop_time", "stop_time"),
    ("bindings", "bindings"),
    ("events", "events"),
    ("legacy", "legacy"),
    ("compressed", "compressed"),
    ("has_generic_root_transform", "has_generic_root_transform"),
    ("index_array", "index_array"),
    ("value_array_reference_pose", "value_array_reference_pose"),
)

#: 研究线 07-clip解码 的普查读数（我们的模块必须复算出同一组数）
RESEARCH_STREAMED = 8140
RESEARCH_DENSE = 5125
RESEARCH_CONSTANT = 10316
RESEARCH_CURVES = 23581


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"缺 {path.name}（先跑 _work/anim-line 下的 p2/p3/p3c 三个探针）")
    return path


def _load_json(name: str) -> dict:
    return json.loads(_require(_FIXTURES / name).read_text(encoding="utf-8"))


def _payloads(name: str) -> bytes:
    return _require(_FIXTURES / name).read_bytes()


@pytest.fixture(scope="module")
def type_table() -> builder.RawTypeTable:
    meta = _require(_UNITS_META).read_bytes()
    return builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())


@pytest.fixture(scope="module")
def clip_nodes(type_table: builder.RawTypeTable) -> tuple[clone.TypeNode, ...]:
    return type_table.entry_for(anim_build.CLASS_ANIMATION_CLIP).nodes


@pytest.fixture(scope="module")
def clips(clip_nodes) -> tuple[tuple[dict, bytes, anim_build.ClipFacts], ...]:
    """(夹具行, 原始字节, 产品读数) —— 顺序 = 夹具顺序。"""
    rows = _load_json("clips_index.json")["rows"]
    blob = _payloads("clips_payloads.bin")
    assert len(rows) == 475, f"夹具里应当有 475 条 clip，实际 {len(rows)}"
    out = []
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        assert len(raw) == row["length"]
        out.append((row, raw, anim_build.read_clip(raw, clip_nodes, path_id=row["path_id"])))
    return tuple(out)


def _first_diff(a: bytes, b: bytes) -> int:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    raise AssertionError("两份字节完全相同")


def _streamed_size_offset(fields, nodes) -> int:
    """流式词表 size 字段在对象里的偏移。

    定位法（⛔ 不写死偏移）：把词表**清空**再序列化，与原字节比 —— 第一处差异就是 size 字段
    （vector 的布局是 int size 后面才跟 data）。
    """
    original = clone.write_object(dict(fields), nodes)
    blanked = copy.deepcopy(fields)
    blanked["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"] = []
    return _first_diff(original, clone.write_object(blanked, nodes))


def _tamper_streamed_size(raw: bytes, fields, nodes) -> bytes:
    """把流式词表 size 的**最高字节**翻一个 bit ⇒ 声明一个天文数字的元素数。"""
    offset = _streamed_size_offset(fields, nodes)
    out = bytearray(raw)
    out[offset + 3] ^= 0x01
    return bytes(out)


def _first_of(clips, want_words: int = 1, curve_min: int = 0):
    return next((r, b, f) for r, b, f in clips
                if r["streamed_words"] >= want_words
                and f.channels.streamed.curve_count >= curve_min)


# --------------------------------------------------------------------------- #
# A · 往返 / 键集合 / 读数对账
# --------------------------------------------------------------------------- #
def test_roundtrip_is_byte_exact_for_all_475(clips) -> None:
    bad = [row["name"] for row, _raw, facts in clips if not facts.roundtrip_equal]
    assert bad == [], f"往返不逐字节相等的 clip：{bad[:8]}（共 {len(bad)}）"
    assert len(clips) == 475


def test_key_sets_match_the_real_thing_475_of_475(clips) -> None:
    assert len(anim_build.CLIP_TOP_KEYS) == 20
    assert len(anim_build.CONTROLLER_TOP_KEYS) == 8
    assert len(anim_build.ANIMATOR_TOP_KEYS) == 13
    assert "m_MuscleClip" in anim_build.CLIP_TOP_KEYS
    for _row, _raw, facts in clips:
        assert facts.keys_are_canonical, facts.name


def test_the_seven_curve_tables_are_empty_in_all_475(clips) -> None:
    """★ 断箭的 clip 是「只有肌肉剪辑、没有编辑器曲线」的形态 —— 七张表 475/475 全空。"""
    assert len(anim_build.EMPTY_CURVE_TABLES) == 7
    for _row, _raw, facts in clips:
        assert len(facts.empty_curve_tables) == 7, facts.name


def test_facts_match_the_independent_fixture_table(clips) -> None:
    for row, raw, facts in clips:
        assert facts.name == row["name"]
        assert facts.path_id == row["path_id"]
        assert facts.object_size == len(raw) == row["length"]
        assert facts.payload_sha256 == row["sha256"]
        assert list(facts.top_keys) == row["top_keys"]
        assert facts.channels.streamed.words == row["streamed_words"]
        assert facts.channels.streamed.curve_count == row["streamed_curve_count"]
        assert facts.channels.dense.frame_count == row["dense_frames"]
        assert facts.channels.dense.curve_count == row["dense_curve_count"]
        assert facts.channels.dense.samples == row["dense_samples"]
        assert len(facts.channels.constant.values) == row["constant_len"]
        assert facts.curve_total == row["curve_total"]
        for attr, key in _NUMERIC:
            assert getattr(facts, attr) == row[key], (row["name"], attr)


# --------------------------------------------------------------------------- #
# B · 三通道
# --------------------------------------------------------------------------- #
def test_three_channel_identities_hold_for_all_475(clips) -> None:
    """① curve_total == len(delta) ② samples == frames x curves ③ MuscleClipSize 公式。"""
    bad_curves = [r["name"] for r, _b, f in clips if f.curve_total != r["value_array_delta"]]
    bad_samples = [
        r["name"] for r, _b, f in clips
        if f.channels.dense.samples != f.channels.dense.frame_count * f.channels.dense.curve_count
    ]
    bad_muscle = [r["name"] for r, _b, f in clips if not f.muscle_clip_size_ok]
    assert bad_curves == []
    assert bad_samples == []
    assert bad_muscle == []
    assert all(f.index_array == 200 for _r, _b, f in clips), "m_IndexArray 真件恒为 200 项"


def test_computed_muscle_clip_size_is_recomputed_from_the_channels(clips) -> None:
    """muscle_clip_size_ok 不许是恒真：在用例里**独立按公式重算一遍**再比。

    （判据：把 computed 改成直接抄 declared，这条必须红 —— 变异自检里验过。）
    """
    for row, _raw, facts in clips:
        ch = facts.channels
        total = ch.curve_total
        if total == 0 and ch.streamed.words == 0:
            expected = 0
        else:
            expected = (
                anim_build.MUSCLE_BASE
                + 4 * (ch.streamed.words + len(ch.constant.values) + ch.dense.samples)
                + 8 * total
            )
        assert facts.computed_muscle_clip_size == expected, row["name"]
        assert facts.computed_muscle_clip_size == row["muscle_clip_size"], row["name"]


def test_muscle_clip_size_ok_is_not_tautological(clips, clip_nodes) -> None:
    """★ 正控：把 m_MuscleClipSize 改掉 ⇒ computed 必须**不动**、ok 必须变假。

    为什么需要它：真件 475/475 的 declared == computed ⇒
    「computed 直接抄 declared」这种写法在真件上是**观测不到的**（变异自检抓不住）。
    喂一个已知违规的点进去，尺子才成立。
    """
    row, raw, facts = clips[0]
    fields = clone.read_object(raw, clip_nodes)
    fields["m_MuscleClipSize"] = int(fields["m_MuscleClipSize"]) + 4
    broken = anim_build.clip_facts(fields, path_id=row["path_id"])
    assert broken.declared_muscle_clip_size == facts.declared_muscle_clip_size + 4
    assert broken.computed_muscle_clip_size == facts.computed_muscle_clip_size
    assert not broken.muscle_clip_size_ok


def test_delta_check_can_go_red(clips, clip_nodes) -> None:
    """★ 正控：把 m_ValueArrayDelta 里的一条改掉 ⇒ delta_check 必须**报出那一条**。

    为什么需要它：真件 23581/23581 全中 ⇒ 把容差放到 1e9 在真件上也是「全中」
    （变异自检抓不住）。喂一个已知违规的点进去，尺子才成立。
    """
    row, _raw, facts = _first_of(clips, want_words=1, curve_min=2)
    raw = next(b for r, b, _f in clips if r["path_id"] == row["path_id"])
    fields = clone.read_object(raw, clip_nodes)
    delta = fields["m_MuscleClip"]["m_ValueArrayDelta"]
    delta[0]["m_Start"] = float(delta[0]["m_Start"]) + 0.25
    broken = anim_build.clip_facts(fields, path_id=row["path_id"])
    assert broken.delta.curves == facts.delta.curves
    assert broken.delta.matched == facts.delta.curves - 1, "改坏的那一条必须被报出来"
    assert not broken.delta.ok
    assert broken.delta.worst >= 0.2


def test_channel_totals_match_the_research_census(clips) -> None:
    streamed = sum(f.channels.streamed.curve_count for _r, _b, f in clips)
    dense = sum(f.channels.dense.curve_count for _r, _b, f in clips)
    constant = sum(len(f.channels.constant.values) for _r, _b, f in clips)
    assert (streamed, dense, constant) == (RESEARCH_STREAMED, RESEARCH_DENSE, RESEARCH_CONSTANT)
    assert streamed + dense + constant == RESEARCH_CURVES == 23581


def test_dispatch_follows_the_unity_rule(clips) -> None:
    """全局曲线号派发：流式段 / 密集段 / 常值段 —— 每一段的起点都查一次。

    顺带钉死真件读数（研究线 muscle-clip-format.md §3.3 记「dense 非空的 79 条」「29 条空 clip」）：
    **有词表 446 · streamed 非空 379 · dense 非空 79 · constant 非空 196 · 全空 29**。
    """
    counts = {"streamed": 0, "dense": 0, "constant": 0, "empty": 0}
    for _row, _raw, facts in clips:
        ch = facts.channels
        if ch.streamed.curve_count:
            assert anim_build.channel_of(ch, 0) == anim_build.CHANNEL_STREAMED
            assert ch.base_of(anim_build.CHANNEL_STREAMED) == 0
            counts["streamed"] += 1
        if ch.dense.curve_count:
            assert anim_build.channel_of(ch, ch.streamed.curve_count) == anim_build.CHANNEL_DENSE
            assert ch.base_of(anim_build.CHANNEL_DENSE) == ch.streamed.curve_count
            counts["dense"] += 1
        if len(ch.constant.values):
            base = ch.streamed.curve_count + ch.dense.curve_count
            assert anim_build.channel_of(ch, base) == anim_build.CHANNEL_CONSTANT
            assert ch.base_of(anim_build.CHANNEL_CONSTANT) == base
            counts["constant"] += 1
        if ch.curve_total == 0:
            counts["empty"] += 1
    assert counts == {"streamed": 379, "dense": 79, "constant": 196, "empty": 29}, counts


def test_every_channel_has_a_reading_in_the_corpus(clips) -> None:
    """判据②：三通道各取一条并给读数（读数里带 值@StartTime -> 值@StopTime 与这一条的形状）。"""
    picked: dict[str, tuple[dict, anim_build.ChannelReadings]] = {}
    for row, _raw, facts in clips:
        for reading in facts.readings():
            picked.setdefault(reading.channel, (row, reading))
    assert set(picked) == {anim_build.CHANNEL_STREAMED, anim_build.CHANNEL_DENSE,
                           anim_build.CHANNEL_CONSTANT}
    for channel, (row, reading) in picked.items():
        assert reading.curves > 0, channel
        assert reading.local_index == 0
        assert reading.detail, channel
        assert row["name"] == picked[channel][0]["name"]
    # 每条通道的读数必须**真能复算**（read() 与 value() 走同一条求值路）
    for _row, _raw, facts in clips[:40]:
        for reading in facts.readings():
            again = facts.channels.read(reading.channel, reading.local_index, facts.start_time)
            assert again == pytest.approx(reading.start_value, rel=1e-9, abs=1e-12)


# --------------------------------------------------------------------------- #
# C · ★ delta 自证（研究线那把尺子）
# --------------------------------------------------------------------------- #
def test_delta_selfcheck_reproduces_the_research_reading(clips) -> None:
    """★ 求值(m_StartTime / m_StopTime) == m_ValueArrayDelta ⇒ 23581/23581 = 100.0000%。

    这条**不循环**：m_ValueArrayDelta 是 Unity 用同一套采样代码在烘焙时写出来的。
    我们算对了 ⇒ 三通道布局 + 求值公式 + 全局曲线号派发**同时对**。
    """
    curves = sum(f.delta.curves for _r, _b, f in clips)
    matched = sum(f.delta.matched for _r, _b, f in clips)
    worst = max(f.delta.worst for _r, _b, f in clips)
    assert curves == RESEARCH_CURVES == 23581
    assert matched == curves, f"命中 {matched}/{curves}"
    assert matched / curves == 1.0
    assert worst < 1e-5, f"最大偏差 {worst:.3e} 超过 1e-5"
    assert all(f.delta.ok for _r, _b, f in clips)


# --------------------------------------------------------------------------- #
# D · 复制
# --------------------------------------------------------------------------- #
def test_copy_clip_fields_mode_reproduces_bytes(clips, clip_nodes) -> None:
    for row, raw, _facts in clips:
        spec = anim_build.copy_clip(raw, clip_nodes, path_id=row["path_id"], label=row["name"])
        assert spec.class_id == anim_build.CLASS_ANIMATION_CLIP
        assert spec.fields is not None and spec.payload is None
        assert clone.write_object(dict(spec.fields), clip_nodes) == raw, row["name"]


def test_copy_controller_and_animator_roundtrip(type_table) -> None:
    ctrl_nodes = type_table.entry_for(anim_build.CLASS_ANIMATOR_CONTROLLER).nodes
    anim_nodes = type_table.entry_for(anim_build.CLASS_ANIMATOR).nodes
    rows = _load_json("controllers_index.json")["rows"]
    blob = _payloads("controllers_payloads.bin")
    assert len(rows) == 165
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        spec = anim_build.copy_controller(raw, ctrl_nodes, path_id=row["path_id"])
        assert clone.write_object(dict(spec.fields), ctrl_nodes) == raw, row["name"]
    arows = _load_json("animators_index.json")["rows"]
    ablob = _payloads("animators_payloads.bin")
    assert len(arows) == 430
    for row in arows:
        raw = ablob[row["offset"]: row["offset"] + row["length"]]
        spec = anim_build.copy_animator(raw, anim_nodes, path_id=row["path_id"])
        assert clone.write_object(dict(spec.fields), anim_nodes) == raw


def test_copy_payload_mode_has_no_field_tree(clips, clip_nodes) -> None:
    row, raw, _facts = clips[0]
    spec = anim_build.copy_clip(raw, clip_nodes, path_id=row["path_id"], mode="payload")
    assert spec.fields is None
    assert spec.payload == raw
    assert builder.dangling_pptrs([spec], assetbundle_path_id=1) == ()


# --------------------------------------------------------------------------- #
# E · 装进 unit_bundle
# --------------------------------------------------------------------------- #
def test_clip_goes_into_a_unit_bundle_with_zero_dangling(clips, clip_nodes) -> None:
    row, raw, _facts = clips[0]
    spec = anim_build.copy_clip(raw, clip_nodes, path_id=0x5100_0000_0000_0011, label="clip")
    result = unit_bundle.assemble_unit_bundle(
        meta=_require(_UNITS_META).read_bytes(),
        spec=prefab_build.PrefabNode(name="RouteAAnimProbe"),
        extra=[spec],
        container_path="Assets/Resources_moved/ModelPrefabs/RouteA/RouteAAnimProbe.prefab",
        bundle_name="routea_anim_probe_0000000000000000.bundle",
        extra_class_ids=(anim_build.CLASS_ANIMATION_CLIP,),
    )
    assert result.problems == ()
    assert result.ok
    assert any("悬空 0 条" in line for line in result.report), result.report
    assert (anim_build.CLASS_ANIMATION_CLIP, None) in result.plan.type_keys
    assert result.plan.object_count == 4
    assert builder.dangling_pptrs([spec], assetbundle_path_id=1) == ()
    assert "clip" in dict(result.plan.path_id_of)


# --------------------------------------------------------------------------- #
# F · ★「12 字节 PPtr」—— 研究线真机那条改动的产品形
# --------------------------------------------------------------------------- #
def _sam_entry(table: builder.RawTypeTable) -> builder.RawTypeEntry:
    hits = [e for e in table.entries_for_class(114)
            if any(n.level == 1 and n.name == "HangarAnimatorController" for n in e.nodes)]
    assert len(hits) == 1, f"SoldierAnimationManager 的类型树应当只有一项，实际 {len(hits)}"
    return hits[0]


def test_hangar_pptr_is_twelve_bytes_at_offset_68_on_all_225(type_table) -> None:
    """★ 真件读数：SoldierAnimationManager.HangarAnimatorController = (偏移 68, 宽度 12)，225/225。

    复算：_work/anim-line/p3_freeze.py（把这条 PPtr 的 12 个字节全换成非零来定位字段）。
    """
    entry = _sam_entry(type_table)
    rows = _load_json("sam_payloads_index.json")["rows"]
    blob = _payloads("sam_payloads.bin")
    assert len(rows) == 225
    spans = set()
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        fields = clone.read_object(raw, entry.nodes)
        spans.add(anim_build.pptr_field_span(fields, entry.nodes, "HangarAnimatorController"))
    assert spans == {(68, 12)}, f"真件 225 个都应当是 (68, 12)，实际 {sorted(spans)}"


def test_sam_fixture_matches_the_module_reading(type_table) -> None:
    entry = _sam_entry(type_table)
    assert entry.class_id == 114
    rows = _load_json("sam_payloads_index.json")["rows"]
    facts = _load_json("sam_index.json")["rows"]
    assert {r["path_id"] for r in rows} == {r["path_id"] for r in facts}
    assert all(r["pptr_field_bytes"] == 12 for r in facts)
    assert all(r["pptr_field_offset"] == 68 for r in facts)
    assert sum(1 for r in facts if r["hangar"][1] != 0) == 33
    assert sum(1 for r in facts if r["hangar"] == [0, 0]) == 192
    assert all(r["script_class"] == "SoldierAnimationManager" for r in facts)
    # 归零后**变化的字节数**是 0 / 7 / 8，**不是** 12 —— 12 是**字段宽度**，两者别混
    assert {r["zero_diff_bytes"] for r in facts} == {0, 7, 8}


def test_set_pptr_is_a_same_length_twelve_byte_change(type_table) -> None:
    anim_nodes = type_table.entry_for(anim_build.CLASS_ANIMATOR).nodes
    rows = _load_json("animators_index.json")["rows"]
    blob = _payloads("animators_payloads.bin")
    row = next(r for r in rows if r["avatar"][1] != 0)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, anim_nodes)
    offset, width = anim_build.pptr_field_span(fields, anim_nodes, "m_Avatar")
    assert width == 12
    moved = anim_build.set_pptr(fields, "m_Avatar", 0x5100_0000_0000_0021)
    before = clone.write_object(dict(fields), anim_nodes)
    after = clone.write_object(moved, anim_nodes)
    assert len(after) == len(before), "12 字节 PPtr 的改动必须是同长度改动"
    assert moved["m_Avatar"]["m_PathID"] == 0x5100_0000_0000_0021
    diff = [i for i in range(len(before)) if before[i] != after[i]]
    assert diff and diff[0] >= offset and diff[-1] < offset + width


# --------------------------------------------------------------------------- #
# G · 拉伸（研究线 p18 正控的公式）
# --------------------------------------------------------------------------- #
def test_stretch_keeps_word_count_and_object_size(clips, clip_nodes) -> None:
    k = 2.5
    checked = 0
    for row, raw, _facts in clips[:80]:
        if row["streamed_words"] == 0:
            continue
        fields = clone.read_object(raw, clip_nodes)
        before = clone.write_object(dict(fields), clip_nodes)
        stretched = anim_build.stretch_streamed(fields, k)
        after = clone.write_object(stretched, clip_nodes)
        assert len(after) == len(before), f"{row['name']}：拉伸改了对象字节数"
        ch0 = anim_build.channels_of(fields)
        ch1 = anim_build.channels_of(stretched)
        assert ch1.streamed.words == ch0.streamed.words
        assert ch1.streamed.curve_count == ch0.streamed.curve_count
        checked += 1
    assert checked >= 20, f"只量到 {checked} 条"


def test_stretch_maps_new_value_at_k_times_t(clips, clip_nodes) -> None:
    """判据：new_value(k*t) == old_value(t)（研究线 p18 正控那一档）。"""
    row, raw, facts = _first_of(clips, want_words=200, curve_min=8)
    k = 2.5
    fields = clone.read_object(raw, clip_nodes)
    ch0 = anim_build.channels_of(fields)
    ch1 = anim_build.channels_of(anim_build.stretch_streamed(fields, k))
    worst = 0.0
    curves = min(12, ch0.streamed.curve_count)
    for index in range(curves):
        for j in range(11):
            t = facts.start_time + (facts.stop_time - facts.start_time) * j / 10
            worst = max(worst, abs(ch1.value(index, k * t) - ch0.value(index, t)))
    assert curves >= 8
    assert worst < 1e-5, f"{row['name']}：拉伸后最大偏差 {worst:.3e}"


def test_stretch_leaves_sentinel_times_alone(clips, clip_nodes) -> None:
    """首条记录 time = -FLT_MAX、末条 = +inf —— 乘 k 会直接溢出 float32，必须原样保留。"""
    _row, raw, _facts = _first_of(clips, want_words=200, curve_min=8)
    fields = clone.read_object(raw, clip_nodes)
    ch0 = anim_build.channels_of(fields)
    ch1 = anim_build.channels_of(anim_build.stretch_streamed(fields, 3.0))
    assert ch1.streamed.first_time == ch0.streamed.first_time
    assert ch1.streamed.first_time <= -3.0e38, f"首条应当 -FLT_MAX，实际 {ch1.streamed.first_time}"
    assert ch1.streamed.records == ch0.streamed.records
    assert ch1.streamed.keys == ch0.streamed.keys


# --------------------------------------------------------------------------- #
# H · 负控（喂坏样本必须变红）
# --------------------------------------------------------------------------- #
def test_negative_control_structural_byte_flip_is_red(clips, clip_nodes) -> None:
    """**负控 1**：把流式词表 size 的最高字节翻一个 bit ⇒ 有词表的 **446/446 必须抛**。"""
    used = 0
    for row, raw, _facts in clips:
        if row["streamed_words"] == 0:
            continue
        used += 1
        fields = clone.read_object(raw, clip_nodes)
        with pytest.raises(AssetError):
            anim_build.read_clip(_tamper_streamed_size(raw, fields, clip_nodes), clip_nodes)
    assert used == 446, f"有词表的 clip 应当是 446 条，实际 {used}"


def test_negative_control_hash_ruler_catches_a_single_byte_edit(clips, clip_nodes) -> None:
    """**负控 2**：随便哪个字节改一个 bit，payload_sha256 这把尺子**必须**变（475/475）。"""
    for row, raw, facts in clips:
        tampered = bytearray(raw)
        tampered[int(len(tampered) * 0.4)] ^= 0x01
        try:
            other = anim_build.read_clip(bytes(tampered), clip_nodes, path_id=row["path_id"])
        except AssetError:
            continue  # 解析就炸了，那更是红了
        assert other.payload_sha256 != facts.payload_sha256, row["name"]
        assert other.payload_sha256 != row["sha256"], row["name"]


def test_negative_control_declared_curve_count_mismatch_is_red(clips, clip_nodes) -> None:
    """**负控 3**：把 m_StreamedClip.curveCount 改错 ⇒ 必须抛（键集合没变，只有那个数变了）。"""
    row, raw, _facts = _first_of(clips, want_words=1, curve_min=3)
    fields = clone.read_object(raw, clip_nodes)
    streamed = fields["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]
    streamed["curveCount"] = int(streamed["curveCount"]) + 1
    with pytest.raises(AssetError, match="曲线"):
        anim_build.clip_facts(fields, path_id=row["path_id"])


def test_negative_control_dense_frame_count_mismatch_is_red(clips, clip_nodes) -> None:
    """**负控 4**：把 m_DenseClip.m_FrameCount 改错 ⇒ 采样数对不上 ⇒ 必须抛。"""
    row, raw, facts = next((r, b, f) for r, b, f in clips if f.channels.dense.samples > 0)
    fields = clone.read_object(raw, clip_nodes)
    dense = fields["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]
    dense["m_FrameCount"] = int(dense["m_FrameCount"]) + 1
    with pytest.raises(AssetError, match="dense"):
        anim_build.clip_facts(fields, path_id=row["path_id"])


def test_negative_control_truncated_word_stream_is_red(clips, clip_nodes) -> None:
    """**负控 5**：词表少一个词 ⇒ 走不干净 ⇒ 必须抛。"""
    row, raw, _facts = _first_of(clips, want_words=10)
    fields = clone.read_object(raw, clip_nodes)
    streamed = fields["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]
    streamed["data"] = list(streamed["data"])[:-1]
    with pytest.raises(AssetError):
        anim_build.clip_facts(fields, path_id=row["path_id"])


def test_negative_control_curve_index_out_of_range_is_red() -> None:
    """**负控 6**：CurveKey 的 curveIndex 越界 ⇒ 必须**立刻**抛（不许照着字面值开辟数组）。

    ★ 这条是**实测踩出来的**：第一版 decode_streamed 没有上界校验，任意一个字节被改
    就可能把 curveIndex 变成天文数字，那个 while 循环就原地挂住（2026-10-08 真挂了一次）。
    """
    # 一条记录的布局：[time, count] + count 个 [curveIndex, c0, c1, c2, c3]
    legal = [0, 1, 2, 0, 0, 0, 0]         # time=0.0 / count=1 / curveIndex=2 / 4 个系数
    parsed = anim_build.decode_streamed(legal, curve_count=3)
    assert parsed.keys == 1
    assert parsed.records == 1
    illegal = [0, 1, 999, 0, 0, 0, 0]     # 同上，但 curveIndex=999 远超声明的 3 条
    with pytest.raises(AssetError, match="越界"):
        anim_build.decode_streamed(illegal, curve_count=3)
    # 不传 curve_count 时也要有上界（退化成词数）—— 不许退化成「照字面值开辟」
    with pytest.raises(AssetError, match="越界"):
        anim_build.decode_streamed(illegal)


def test_negative_control_broken_key_set_is_refused(clips, clip_nodes) -> None:
    """**负控 7**：键集合少一个 / 多一个都必须在出数之前抛。"""
    _row, raw, _facts = clips[0]
    missing = clone.read_object(raw, clip_nodes)
    del missing["m_MuscleClip"]
    with pytest.raises(AssetError, match="顶层键集合"):
        anim_build.clip_facts(missing)
    extra = clone.read_object(raw, clip_nodes)
    extra["m_GameObject"] = {"m_FileID": 0, "m_PathID": 0}
    with pytest.raises(AssetError, match="顶层键集合"):
        anim_build.clip_facts(extra)


def test_negative_control_payload_mode_refuses_a_mapping(clips, clip_nodes) -> None:
    """**负控 8**：字节照搬 + PPtr 重映射不能同真；mode 不认也要抛。"""
    _row, raw, _facts = clips[0]
    with pytest.raises(AssetError, match="不能同时给"):
        anim_build.copy_clip(raw, clip_nodes, path_id=1, mode="payload", mapping={1: 2})
    with pytest.raises(AssetError, match="不认"):
        anim_build.copy_clip(raw, clip_nodes, path_id=1, mode="magic")


# --------------------------------------------------------------------------- #
# I · Animator / controller 边界
# --------------------------------------------------------------------------- #
def test_animator_null_pptr_counts_are_the_real_thing() -> None:
    """★ 真件 430 个 Animator：**204 个 m_Avatar == {0,0}**、**4 个 m_Controller == {0,0}**。"""
    rows = _load_json("animators_index.json")["rows"]
    assert len(rows) == 430
    assert sum(1 for r in rows if r["avatar"] == [0, 0]) == 204
    assert sum(1 for r in rows if r["controller"] == [0, 0]) == 4
    assert all(r["roundtrip"] for r in rows)
    assert all(len(r["top_keys"]) == 13 for r in rows)


def test_controller_fixture_facts_are_the_real_thing() -> None:
    rows = _load_json("controllers_index.json")["rows"]
    assert len(rows) == 165
    assert sum(r["clips"] for r in rows) == 981, "165 个 controller 的 m_AnimationClips 条数合计"
    assert all(r["roundtrip"] for r in rows)
    assert all(r["tos"] > 0 for r in rows), "m_TOS 是键值表，真件 165/165 都非空"
    assert all(len(r["top_keys"]) == 8 for r in rows)
