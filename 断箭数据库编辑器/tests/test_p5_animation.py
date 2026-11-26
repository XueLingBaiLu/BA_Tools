r"""P5 · 动画（A2 命名表/校验器 · A3 改 clip · 动画的尺子 · A4 自烘）—— 判据（含正负控）。

**判据一行**：`pytest tests/test_p5_animation.py -q -n 0` -> 通过数 0 -> N，
其中至少 6 条是**负控**（喂坏样本必须变红才算尺子，AGENTS §0.9 动作 2）。

## 数据从哪来（⛔ 不开 units）
* clip 夹具：`_work/anim-line/fixtures/clips_payloads.bin + clips_index.json`（475 条冻结件）
* 类型树：`_work/w10_ref/units_meta.bin`
* 命名表：`_work/p5/hardpoints.py` 现扫 AssetRipper 全量导出（`unity-ba-rip-all/Assets`，只读）
  ⇒ 夹具缺一就 **skip**（与 test_build_anim.py 同一姿势），⛔ 不许悄悄跳过断言。

## 各段量什么
| 段 | 判据 |
|---|---|
| A | 命名表语料非空 + 校验器正控 6/6 + **负控 4/4 能红** + 拼错闸门给建议 + 表能往返 |
| B | 拉伸 x2：时长翻倍 · 对象字节不变 · **求值器 Start/Stop 自证 100%** · k=1 是逐字节 no-op |
| C | 尺子：**已知不动**（全空 clip）读数全 0 · **已知会动**读数非 0 · 两条必须不同 |
| D | 自烘（A4）：同长度 + 求值器自证 100% + 扫掠包络塌成 0 |
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(WORK / "p5"))

import hardpoints  # noqa: E402
import probe as p5probe  # noqa: E402

from ba_mod_maker.asset_kernel import anim_build, builder, clone  # noqa: E402

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

_FIXTURES = WORK / "anim-line" / "fixtures"
_UNITS_META = WORK / "w10_ref" / "units_meta.bin"
_TABLE = WORK / "p5" / "hardpoints.json"


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"缺 {path.name}（先跑 _work/p5/probe.py / _work/anim-line/p2+p3）")
    return path


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def table() -> hardpoints.HardpointTable:
    doc = json.loads(_require(_TABLE).read_text(encoding="utf-8"))
    return hardpoints.from_json(doc)


@pytest.fixture(scope="module")
def clip_nodes():
    meta = _require(_UNITS_META).read_bytes()
    raw = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    return raw.entry_for(anim_build.CLASS_ANIMATION_CLIP).nodes


@pytest.fixture(scope="module")
def clips(clip_nodes):
    rows = json.loads(_require(_FIXTURES / "clips_index.json").read_text(encoding="utf-8"))["rows"]
    blob = _require(_FIXTURES / "clips_payloads.bin").read_bytes()
    assert len(rows) == 475, f"夹具里应当有 475 条 clip，实际 {len(rows)}"
    return tuple((r, blob[r["offset"]: r["offset"] + r["length"]]) for r in rows)


@pytest.fixture(scope="module")
def sample(clips, clip_nodes):
    """② 的样本：流式曲线最多的一条（时长 x2 用）。"""
    row, raw = max((pair for pair in clips if pair[0]["streamed_curve_count"] > 0),
                   key=lambda pair: pair[0]["curve_total"])
    fields = clone.read_object(raw, clip_nodes)
    return row, raw, fields, anim_build.channels_of(fields)


# --------------------------------------------------------------------------- #
# A · 命名表 + 校验器
# --------------------------------------------------------------------------- #
def test_table_corpus_is_not_empty(table) -> None:
    assert len(table.bones) >= 100, "骨名太少了 —— 语料扫错地方了"
    assert len(table.hosts) >= 5000, "GameObject 名太少了 —— 语料扫错地方了"
    assert len(table.families) >= 50


def test_validator_positive_control_passes(table) -> None:
    hits = table.check_many(hardpoints.POSITIVE)
    assert [h.name for h in hits if not h.ok] == []
    assert table.check("turret_3").count == 156, "turret_3 在真件里出现 156 次"
    assert table.check("WeaponPlace").count == 554


def test_validator_negative_control_goes_red(table) -> None:
    """★ 负控：拼错的 `turrent_3` 必须红，且给出建议 `turret_3`。"""
    hits = table.check_many(hardpoints.NEGATIVE)
    assert [h.name for h in hits if h.ok] == [], "负控样本里有被认下来的"
    typo = table.check("turrent_3")
    assert not typo.ok
    assert typo.tier == "miss"
    assert typo.suggestion == "turret_3"
    assert not table.check("muzzle_1").ok, "真件里只有 Muzzle，muzzle_1 必须红"
    assert not table.check("").ok
    assert not table.check("  ").ok


def test_validator_family_tier_accepts_new_index(table) -> None:
    """弱档：撞上名字族的新序号可以过（否则每加一个炮塔都得改表）。"""
    hit = table.check("turret_99")
    assert hit.ok and hit.tier == "family", hit.line()
    assert "turret_" in hit.note


def test_validator_rejects_whitespace_and_empty(table) -> None:
    assert table.check(" turret_3").tier == "miss"       # 首尾空白
    assert table.check("turret-3").tier == "miss"        # 下划线写成横杠
    # 大小写：Unity 的名字区分大小写。⚠ 这条用 WeaponPlace，不用 Turret_3 ——
    # 真件语料里**确实**存在 `Turret_N` 这一族（大写 T），所以 Turret_3 撞族是**对的**行为。
    assert table.check("WeaponPlace").ok
    assert not table.check("Weaponplace").ok
    assert not table.check("weaponplace").ok


def test_table_json_is_reversible(table) -> None:
    back = hardpoints.from_json(hardpoints.to_json(table))
    assert back.bones == table.bones
    assert back.hosts == table.hosts
    assert back.families == table.families
    with pytest.raises(ValueError):
        hardpoints.from_json({"format": 99})


# --------------------------------------------------------------------------- #
# B · A3 改 clip：时长 x2 + 求值器自证
# --------------------------------------------------------------------------- #
def test_stretch_doubles_duration_and_keeps_object_size(sample, clip_nodes) -> None:
    row, raw, fields, before = sample
    assert float(fields["m_MuscleClip"]["m_StartTime"]) == 0.0, "全库 475/475 的 StartTime 都是 0"
    after = anim_build.stretch_streamed(fields, 2.0)
    out = clone.write_object(after, clip_nodes)
    assert len(out) == len(raw), "拉伸必须是**同长度**改动（对象字节数不变）"
    ch = anim_build.channels_of(after)
    assert ch.streamed.words == before.streamed.words
    assert ch.curve_total == before.curve_total
    stop = float(after["m_MuscleClip"]["m_StopTime"])
    assert stop == pytest.approx(2.0 * float(row["stop_time"]))


def test_evaluator_self_proves_on_start_and_stop_after_stretch(sample) -> None:
    """★ 任务判据②：改一条 clip 时长 x2 -> **求值器在 Start/Stop 自证通过**。"""
    _row, _raw, fields, _before = sample
    after = anim_build.stretch_streamed(fields, 2.0)
    ch = anim_build.channels_of(after)
    m = after["m_MuscleClip"]
    check = anim_build.delta_check(ch, m["m_ValueArrayDelta"],
                                   float(m["m_StartTime"]), float(m["m_StopTime"]))
    assert check.ok, check.line()
    assert check.matched == check.curves == ch.curve_total
    assert check.worst <= 1e-4


def test_stretch_semantics_new_k_t_equals_old_t(sample) -> None:
    row, _raw, fields, before = sample
    after = anim_build.stretch_streamed(fields, 2.0)
    assert anim_build.channels_of(after).curve_total == before.curve_total
    times = [float(row["stop_time"]) * j / 8.0 for j in range(9)]
    for i in range(before.curve_total):
        for t in times:
            assert abs(after_ch_value(after, i, 2.0 * t) - before.value(i, t)) < 1e-4


def after_ch_value(fields, index, t):
    return anim_build.channels_of(fields).value(index, t)


def test_stretch_by_one_is_a_byte_level_noop(sample, clip_nodes) -> None:
    """正控：k=1 必须**逐字节**什么都不改 —— 否则「拉伸」这函数有副作用。"""
    _row, raw, fields, _before = sample
    assert clone.write_object(anim_build.stretch_streamed(fields, 1.0), clip_nodes) == raw


def test_stretched_data_with_unmoved_stop_goes_red(sample) -> None:
    """★ 负控：数据 x2 但 StopTime 不动 —— 两端点必然对不上 ⇒ 求值器自证必须红。"""
    row, _raw, fields, _before = sample
    mixed = anim_build.stretch_streamed(fields, 2.0, stop_time=float(row["stop_time"]))
    ch = anim_build.channels_of(mixed)
    m = mixed["m_MuscleClip"]
    check = anim_build.delta_check(ch, m["m_ValueArrayDelta"],
                                   float(m["m_StartTime"]), float(m["m_StopTime"]))
    assert not check.ok, "这把尺子红不了 ⇒ 不算尺子"
    assert check.worst > 1e-3


# --------------------------------------------------------------------------- #
# C · 动画的尺子：逐曲线 x 时间扫掠
# --------------------------------------------------------------------------- #
def test_ruler_reads_an_empty_clip_as_zero(clips, clip_nodes) -> None:
    """正控·**已知不动**：全空 clip（无词表无曲线）——读数必须全是 0。"""
    row, raw = next(pair for pair in clips if pair[0]["curve_total"] == 0)
    ch = anim_build.channels_of(clone.read_object(raw, clip_nodes))
    got = p5probe.sweep(ch, row["name"], 0.0, float(row["stop_time"]))
    assert got.curves == 0 and got.moving == 0
    assert got.total_width == 0.0 and got.max_width == 0.0


def test_ruler_reads_a_moving_clip_as_nonzero(sample) -> None:
    """正控·**已知会动**：曲线 > 0 的 clip —— 扫掠包络必须 > 0。"""
    row, _raw, fields, ch = sample
    got = p5probe.sweep(ch, row["name"], 0.0, float(row["stop_time"]))
    assert got.curves == ch.curve_total > 0
    assert got.moving > 0, "这条样本不动 —— 换一条"
    assert got.total_width > 0.0 and got.max_width > 0.0


def test_the_two_control_readings_differ(clips, clip_nodes, sample) -> None:
    """★ 任务判据③：「不动」与「动」两条 clip 的读数**必须不同**。"""
    erow, eraw = next(pair for pair in clips if pair[0]["curve_total"] == 0)
    still = p5probe.sweep(anim_build.channels_of(clone.read_object(eraw, clip_nodes)),
                          erow["name"], 0.0, float(erow["stop_time"]))
    row, _raw, _fields, ch = sample
    moving = p5probe.sweep(ch, row["name"], 0.0, float(row["stop_time"]))
    assert still.key() != moving.key()


def test_ruler_can_go_red_when_the_clip_is_frozen(sample) -> None:
    """★ 负控：把「会动」的那条**冻**成常量 ⇒ 读数必须跟着变（尺子能红）。"""
    row, _raw, fields, ch = sample
    before = p5probe.sweep(ch, row["name"], 0.0, float(row["stop_time"]))
    frozen_fields = p5probe.freeze_clip(fields, ch)
    frozen_ch = anim_build.channels_of(frozen_fields)
    after = p5probe.sweep(frozen_ch, row["name"] + " (冷冻)", 0.0, float(row["stop_time"]))
    assert before.key() != after.key(), "冷冻前后读数一样 ⇒ 尺子看不见"
    assert after.total_width == pytest.approx(0.0, abs=1e-9)
    assert after.moving == 0


# --------------------------------------------------------------------------- #
# D · A4 自烘（同长度）
# --------------------------------------------------------------------------- #
def test_offline_bake_is_same_length_and_self_proves(sample, clip_nodes) -> None:
    row, raw, fields, ch = sample
    baked = p5probe.freeze_clip(fields, ch)
    out = clone.write_object(baked, clip_nodes)
    assert len(out) == len(raw), "自烘必须落在**同长度**改动里"
    baked_ch = anim_build.channels_of(baked)
    assert baked_ch.streamed.words == ch.streamed.words
    assert baked_ch.curve_total == ch.curve_total
    m = baked["m_MuscleClip"]
    check = anim_build.delta_check(baked_ch, m["m_ValueArrayDelta"],
                                   float(m["m_StartTime"]), float(m["m_StopTime"]))
    assert check.ok, check.line()


def test_offline_bake_negative_control_delta_not_rebaked(sample, clip_nodes) -> None:
    """★ 负控：烘了数据却**不重烘 delta** ⇒ 求值器自证必须红。"""
    _row, _raw, fields, ch = sample
    original_delta = copy.deepcopy(fields["m_MuscleClip"]["m_ValueArrayDelta"])
    baked = p5probe.freeze_clip(fields, ch)
    baked["m_MuscleClip"]["m_ValueArrayDelta"] = original_delta   # 只坏这一处
    baked_ch = anim_build.channels_of(baked)
    m = baked["m_MuscleClip"]
    check = anim_build.delta_check(baked_ch, m["m_ValueArrayDelta"],
                                   float(m["m_StartTime"]), float(m["m_StopTime"]))
    assert not check.ok, "不重烘 delta 也必须能红 —— 否则自证是恒真的"
    assert check.worst > 1e-3


@pytest.fixture(scope="module")
def attribute_hashes(clips, clip_nodes) -> set:
    """全部 475 条 clip 的 binding `attribute` 集合（哈希尺子的语料）。"""
    seen = set()
    for _row, raw in clips:
        f = clone.read_object(raw, clip_nodes)
        for g in f["m_ClipBindingConstant"]["genericBindings"]:
            seen.add(int(g["attribute"]))
    return seen


def test_string_hash_is_crc32(attribute_hashes) -> None:
    """哈希尺子的正控：`m_IsActive` / `m_Enabled` 的 crc32 必须真的出现在 binding 表里。

    这条是「path hash 能不能反查骨名」那把尺子的**正控**：哈希函数对了，反查不到就只能是
    「候选路径字符串不全」，而不是「哈希算错了」。
    """
    import zlib
    assert (zlib.crc32(b"m_IsActive") & 0xFFFFFFFF) in attribute_hashes
    assert (zlib.crc32(b"m_Enabled") & 0xFFFFFFFF) in attribute_hashes
    # 负控：编一个名字，它的 crc32 不该在表里
    assert (zlib.crc32(b"m_NotARealProperty") & 0xFFFFFFFF) not in attribute_hashes
