r"""L3 路 A · `asset_kernel.anim_clip_build` 的判据：**编出来的 clip 能不能解回原值**。

**判据一行**：`pytest tests/test_anim_clip_build.py -q -n 0` -> 全绿；
把 `m_StreamedClip.data` 里**被扫到的那一片**翻一位 ⇒ 对应的负控**当场红**。

夹具（**离线**，⛔ 不开 3.37 GB 的 units 包）：`_work/anim-line/fixtures/` 的
`clips_index.json` + `clips_payloads.bin`（真件 475 条 clip 的冻结件）
＋ `_work/w10_ref/units_meta.bin`（冻结类型表）。
模板 = `T72B hatch`（1 个 Transform 旋转绑定 / 4 条 streamed 曲线 / 68 词 / 2,572 B）。

| # | 用例 | 必须 |
|---|---|---|
| 正控 1 | 编 -> 解，片时刻上取值 | **精确相等**（0.0） |
| 正控 2 | 编 -> 解，61 点扫描 | 与参照实现差 <= 1e-6 |
| 正控 3 | 序列化往返（write_object -> read_object） | 逐值相同、名字跟着走 |
| 正控 4 | `m_MuscleClipSize` | 树里 == `muscle_clip_size_of` == `clip_size(...)`（三条一致） |
| 正控 5 | `remap_copy` 重指 controller 的 PPtr | 写出去读回来，`m_AnimationClips` 就是重指后的 |
| 负控 1 | 翻「t=0 那一片的**值**」一位 | 取值**必须**变 |
| 负控 2 | 翻「`-FLT_MAX` sentinel 片」一位 | `t=-1` **必变**、`t>=0` **不变**（把事实钉死） |
| 负控 3 | 翻片头 `count` 一位 | 解码器**必须抛**（走不干净） |
| 记录 | `copy_controller(mapping=…)` 重指时抛 | 如实断言它抛（哪天修好了 ⇒ 这条红，**那是好事**） |
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import anim_build, anim_clip_build, builder, clone
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
FIXTURES = WORK / "anim-line" / "fixtures"
META = WORK / "w10_ref" / "units_meta.bin"
HATCH = "T72B hatch"
HATCH_PID = 6926808325590346151
CONTROLLER = "RU_T72B"
NEW_CLIP_PID = 900000000000000001


def _read_fixture(kind: str, name: str) -> bytes:
    index = json.loads((FIXTURES / f"{kind}_index.json").read_text(encoding="utf-8"))
    rows = index["rows"] if isinstance(index, dict) else index
    blob = (FIXTURES / f"{kind}_payloads.bin").read_bytes()
    row = next(r for r in rows if r.get("name") == name)
    return blob[row["offset"]: row["offset"] + row["length"]]


@pytest.fixture(scope="module")
def frozen() -> dict:
    """冻结夹具：类型表 + `T72B hatch` 的字段树（都是**离线**件）。"""
    if not META.is_file() or not FIXTURES.is_dir():
        pytest.skip(f"没有离线夹具：{FIXTURES} / {META}")
    table = builder.read_raw_type_table(
        META.read_bytes(), common_strings=clone.unitypy_common_strings()
    )
    nodes = table.entry_for(anim_build.CLASS_ANIMATION_CLIP).nodes
    tree = clone.read_object(_read_fixture("clips", HATCH), nodes)
    return {"table": table, "nodes": nodes, "tree": tree}


def _plan(tree: dict, *, t0: float = 0.0, t1: float = 1.0):
    """拿真件自己的两端值做一条**片内线性**的（cubic -> linear），返回 (times, values)。"""
    channels = anim_build.channels_of(tree)
    times = (t0, t1)
    values = tuple(
        [channels.value(i, t0), channels.value(i, t1)] for i in range(channels.curve_total)
    )
    return times, values, channels


# --------------------------------------------------------------------------- #
# 正控
# --------------------------------------------------------------------------- #
def test_positive_control_roundtrip_is_exact_at_slice_times(frozen: dict) -> None:
    """正控 1：编 -> 解，**片时刻上精确相等**（不是近似）。"""
    times, values, _src = _plan(frozen["tree"])
    new_tree = anim_clip_build.apply_streamed(frozen["tree"], times=times, values=values)
    back = anim_build.channels_of(new_tree)
    for i in range(len(values)):
        for k, t in enumerate(times):
            assert back.value(i, t) == values[i][k]


def test_positive_control_sweep_matches_the_reference(frozen: dict) -> None:
    """正控 2：61 点扫描 vs **独立写的参照实现**。"""
    times, values, _src = _plan(frozen["tree"])
    new_tree = anim_clip_build.apply_streamed(frozen["tree"], times=times, values=values)
    back = anim_build.channels_of(new_tree)
    worst = max(
        abs(back.value(i, step / 60.0)
            - anim_clip_build.expected_value(times, values, i, step / 60.0))
        for i in range(len(values)) for step in range(61)
    )
    assert worst <= 1e-6


def test_positive_control_serialization_roundtrip(frozen: dict) -> None:
    """正控 3：写出去再读回来，逐值相同、名字跟着走、词数不变。"""
    times, values, _src = _plan(frozen["tree"])
    new_tree = anim_clip_build.apply_streamed(
        frozen["tree"], times=times, values=values, name="BA_L3_Test"
    )
    raw = clone.write_object(new_tree, frozen["nodes"])
    again = clone.read_object(raw, frozen["nodes"])
    assert again["m_Name"] == "BA_L3_Test"
    before = anim_build.channels_of(new_tree)
    after = anim_build.channels_of(again)
    for i in range(len(values)):
        for step in range(61):
            assert after.value(i, step / 60.0) == before.value(i, step / 60.0)
    assert len(again["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"]) == 68


def test_positive_control_muscle_clip_size_is_computed_not_copied(frozen: dict) -> None:
    """正控 4：**三条一致** —— 树里那格 == `anim_build` 的公式 == 本模块的公式（都 = 2832）。"""
    times, values, _src = _plan(frozen["tree"])
    new_tree = anim_clip_build.apply_streamed(frozen["tree"], times=times, values=values)
    words = new_tree["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"]
    channels = anim_build.channels_of(new_tree)
    assert len(words) == 68
    assert int(new_tree["m_MuscleClipSize"]) == 2832
    assert anim_build.muscle_clip_size_of(channels) == 2832
    assert anim_clip_build.clip_size(68, 0, 0, len(values)) == 2832
    assert int(frozen["tree"]["m_MuscleClipSize"]) == 2832


def test_positive_control_dense_frame_count_follows_the_duration(frozen: dict) -> None:
    """正控 4b：dense 的 `m_FrameCount` = 时长×采样率 + 2（hatch 62 = 1.0×60+2）。"""
    times, values, _src = _plan(frozen["tree"])
    new_tree = anim_clip_build.apply_streamed(frozen["tree"], times=times, values=values)
    dense = new_tree["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]
    assert dense["m_FrameCount"] == 62
    assert new_tree["m_MuscleClip"]["m_StartTime"] == 0.0
    assert new_tree["m_MuscleClip"]["m_StopTime"] == 1.0


def test_positive_control_remap_copy_retargets_the_controller(frozen: dict) -> None:
    """正控 5：`remap_copy` 能把 controller 里指向 hatch 的 PPtr 重指到我们的新 clip。"""
    nodes = frozen["table"].entry_for(anim_build.CLASS_ANIMATOR_CONTROLLER).nodes
    raw = _read_fixture("controllers", CONTROLLER)
    spec = anim_clip_build.remap_copy(
        raw, nodes, class_id=anim_build.CLASS_ANIMATOR_CONTROLLER,
        path_id=4098237354957962441, mapping={HATCH_PID: NEW_CLIP_PID}, label="ctrl",
    )
    back = clone.read_object(clone.write_object(spec.fields, nodes), nodes)
    pids = [int((c or {}).get("m_PathID") or 0) for c in back["m_AnimationClips"]]
    assert pids == [NEW_CLIP_PID, -6098664476003230484, NEW_CLIP_PID]
    assert pids.count(HATCH_PID) == 0


# --------------------------------------------------------------------------- #
# 负控（每一条都**能红**）
# --------------------------------------------------------------------------- #
def _built(frozen: dict) -> dict:
    """编一份（⛔ 不动词）—— 拿它的词当基线与负控的底。"""
    times, values, _src = _plan(frozen["tree"])
    return anim_clip_build.apply_streamed(frozen["tree"], times=times, values=values)


def _words_of(tree: dict) -> list:
    return list(tree["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"])


def _built_with_words(frozen: dict, words: list) -> dict:
    tree = _built(frozen)
    tree["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"] = list(words)
    return tree


def _word_offsets(times, values) -> dict:
    """按片表算出「sentinel 片的值词」与「首个真片的值词」在词流里的下标。"""
    slices = anim_clip_build.plan_linear_slices(times, values)
    offsets, pos = [], 0
    for item in slices:
        offsets.append(pos)
        pos += 2 + 5 * len(item.keys)
    first = next(k for k, item in enumerate(slices) if item.time == float(times[0]))
    return {
        "sentinel_value": offsets[0] + 2 + 3,
        "first_value": offsets[first] + 2 + 3,
        "first_count": offsets[first] + 1,
    }


def test_negative_control_flipping_a_used_value_word_changes_values(frozen: dict) -> None:
    """负控 1：翻「t=0 那一片的值」一位 ⇒ 取值**必须**变。"""
    times, values, _src = _plan(frozen["tree"])
    words = _words_of(_built(frozen))
    at = _word_offsets(times, values)["first_value"]
    good = anim_build.channels_of(_built_with_words(frozen, words))
    bad_words = list(words)
    bad_words[at] ^= 0x1
    bad = anim_build.channels_of(_built_with_words(frozen, bad_words))
    worst = max(abs(bad.value(i, s / 60.0) - good.value(i, s / 60.0))
                for i in range(len(values)) for s in range(61))
    assert worst > 0.0


def test_negative_control_sentinel_slice_only_feeds_t_before_the_first_slice(frozen: dict) -> None:
    """负控 2（★ 值钱的那条）：翻 sentinel 片的值 ⇒ **t=-1 处必变、t>=0 处不变**。

    这条把 Unity 的行为钉死：`-FLT_MAX` 那片只在 `t < 第一个真片时刻` 时被选中。
    ⛔ 我第一版负控翻的就是它、扫的却是 `t>=0` ⇒ **假绿**（`max diff = 0`）。
    """
    times, values, _src = _plan(frozen["tree"])
    words = _words_of(_built(frozen))
    at = _word_offsets(times, values)["sentinel_value"]
    good = anim_build.channels_of(_built_with_words(frozen, words))
    bad_words = list(words)
    bad_words[at] ^= 0x1
    bad = anim_build.channels_of(_built_with_words(frozen, bad_words))
    early = max(abs(bad.value(i, -1.0) - good.value(i, -1.0)) for i in range(len(values)))
    late = max(abs(bad.value(i, s / 60.0) - good.value(i, s / 60.0))
               for i in range(len(values)) for s in range(61))
    assert early > 0.0
    assert late == 0.0


def test_negative_control_flipping_a_count_word_breaks_the_decoder(frozen: dict) -> None:
    """负控 3：翻片头 `count` 一位 ⇒ 解码器**必须抛**（走不干净 / 越界）。"""
    times, values, _src = _plan(frozen["tree"])
    words = _words_of(_built(frozen))
    at = _word_offsets(times, values)["first_count"]
    bad_words = list(words)
    bad_words[at] ^= 0x1
    # ⚠ 解码器抛的是 `AssetError`（`BaModMakerError` 那一族，**不是** ValueError）——
    #   实测报 `CurveKey 的 curveIndex=1065353216 越界`（count 一偏，float 位型被当成下标）。
    with pytest.raises(AssetError):
        anim_build.channels_of(_built_with_words(frozen, bad_words))


def test_negative_control_bad_plan_is_refused() -> None:
    """负控 4：片数/值个数对不上、只有一片 —— 一律拒（⛔ 不猜）。"""
    with pytest.raises(ValueError):
        anim_clip_build.plan_linear_slices([0.0, 1.0], [[1.0]])
    with pytest.raises(ValueError):
        anim_clip_build.plan_linear_slices([0.0], [[1.0, 2.0]])
    with pytest.raises(ValueError):
        anim_clip_build.plan_linear_slices([0.0, 1.0], [])


def test_trap_existing_copy_controller_cannot_remap(frozen: dict) -> None:
    """记录一处**既有实现**的坑：`copy_controller(mapping=…)` 重指时**抛**（⛔ 我没改它）。

    它转调的 `_copy_object` 自证 `again == raw`，而重指必然不等 ⇒ 当场 AssetError。
    ★ 哪天这条红（= 它改成能重指了）**那是好事**：把 `remap_copy` 退休、改用现成那只手。
    """
    nodes = frozen["table"].entry_for(anim_build.CLASS_ANIMATOR_CONTROLLER).nodes
    raw = _read_fixture("controllers", CONTROLLER)
    with pytest.raises(Exception) as caught:
        anim_build.copy_controller(
            raw, nodes, path_id=4098237354957962441, mapping={HATCH_PID: NEW_CLIP_PID},
        )
    assert "不相等" in str(caught.value)
    # 空 mapping 那条路**是好的**（保真复制）
    spec = anim_build.copy_controller(raw, nodes, path_id=4098237354957962441)
    assert clone.write_object(spec.fields, nodes) == raw
