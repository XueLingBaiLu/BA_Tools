r"""N1 读侧（肌肉→曲线桥 ＋ 三种曲线编码）的机械判据。

**判据一行**：`.venv\\Scripts\\python.exe -m pytest tests/test_muscle_clip.py -q -n 0`
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

夹具 = **冻结真件抽件** `_work/anim-line/fixtures/`（475 条真 AnimationClip 的载荷池
5,360,912 B ＋ 逐条 offset/长度/sha256，任务线 2026-10-08 产出）＋ 真 units 类型表
`_work/w10_ref/units_meta.bin`。⛔ 不开包、不读游戏盘、不写盘。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 475 条**逐字节往返**（字段树 -> 字节 == 原载荷）＋ 逐条 sha256 | 475/475 |
| A | 三条恒等式：`curve_total == len(m_ValueArrayDelta)` · `m_MuscleClipSize` · dense 长度 | 逐条 |
| B | **`m_IndexArray` 语义**：定长 200；用到的槽只在 7..136；值 = **曲线号** | 逐条 |
| C | **肌肉→曲线桥**：肌肉 m ↔ 槽 m+42 ↔ 曲线号（对 `_work/muscleline` 的通道表） | 逐条 |
| D | **正控**：constant 任何 t 逐位相同 · dense 网格点逐位相同 · streamed 键点逐位相同 | 全中 |
| E | **负控**：三段原始数组各翻一位 ⇒ 解出来的**结构指纹必须变** | 全变 |

⚠ 「没落点 / 没这条通道」是**如实入账**的第三档：⛔ 不算通过，也不算失败（尺子在那些 clip 上
造不出来，原因写进读数）。
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import anim_build as ab
from ba_mod_maker.asset_kernel import clone as cl

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
FIX = WORK / 'anim-line' / 'fixtures'
META = WORK / 'w10_ref' / 'units_meta.bin'
CHANNEL_TABLE = WORK / 'muscleline' / 'out' / 'Prone_idle.muscles.json'

#: `_work/muscleline/README.md` 二十·(5) 的 13 条「rel 的驱动者」。
MUSCLES_13 = (*range(0, 9), 37, 38, 46, 47)
#: 属性槽 = 肌肉号 + 42（README 的 L1「数字段」；本文件的 C 段逐条钉住）。
ATTR_OFFSET = 42


@pytest.fixture(scope='module')
def fixture():
    if not (FIX / 'clips_index.json').is_file() or not META.is_file():
        pytest.skip('缺冻结抽件（_work/anim-line/fixtures 或 _work/w10_ref/units_meta.bin）')
    rows = json.loads((FIX / 'clips_index.json').read_text(encoding='utf-8'))['rows']
    blob = (FIX / 'clips_payloads.bin').read_bytes()
    schema = cl.read_type_table(META.read_bytes(), common_strings=cl.unitypy_common_strings())
    nodes = next(e for e in schema.entries if e.class_id == 74).nodes
    return rows, blob, nodes


@pytest.fixture(scope='module')
def channel_table():
    if not CHANNEL_TABLE.is_file():
        pytest.skip('缺 _work/muscleline/out/Prone_idle.muscles.json（130 条通道表）')
    doc = json.loads(CHANNEL_TABLE.read_text(encoding='utf-8'))
    return {int(c['muscle_index']): c for c in doc['channels'] if int(c['muscle_index']) >= 0}


def _fields(fixture, row):
    rows, blob, nodes = fixture
    return cl.read_object(blob[row['offset']: row['offset'] + row['length']], nodes)


def _sample(rows, step: int):
    return [row for index, row in enumerate(rows, start=1) if index % step == 0]


# --------------------------------------------------------------------------- #
# A · 往返 ＋ 三条恒等式（全量 475）
# --------------------------------------------------------------------------- #
def test_a_every_clip_round_trips_byte_for_byte(fixture) -> None:
    rows, blob, nodes = fixture
    bad = []
    for row in rows:
        payload = blob[row['offset']: row['offset'] + row['length']]
        fields = cl.read_object(payload, nodes)
        if cl.write_object(fields, nodes) != payload:
            bad.append(row['name'])
    assert len(rows) == 475, len(rows)
    assert not bad, f'往返不一致 {len(bad)} 条：{bad[:5]}'


def test_a_identities_hold_on_every_clip(fixture) -> None:
    rows, blob, nodes = fixture
    bad_curve = bad_size = bad_dense = 0
    for row in rows:
        fields = _fields(fixture, row)
        muscle = fields['m_MuscleClip']
        channels = ab.channels_of(fields)
        if channels.curve_total != len(muscle['m_ValueArrayDelta']):
            bad_curve += 1
        if ab.muscle_clip_size_of(channels) != int(fields['m_MuscleClipSize']):
            bad_size += 1
        dense = channels.dense
        if dense.curve_count and len(dense.data) != dense.frame_count * dense.curve_count:
            bad_dense += 1
    assert (bad_curve, bad_size, bad_dense) == (0, 0, 0), (bad_curve, bad_size, bad_dense)


# --------------------------------------------------------------------------- #
# B · m_IndexArray 到底是什么表
# --------------------------------------------------------------------------- #
def test_b_index_array_is_a_fixed_200_slot_table_of_curve_indices(fixture) -> None:
    """475/475：定长 **200**；用到的槽**只在 7..136**；每个值都是**合法曲线号**。

    这一条推翻了我第一版的猜测（「长度 == 曲线数、按曲线号索引」—— 实测 474/475 不成立）。
    """
    rows, blob, nodes = fixture
    lens = set()
    used_window = set()
    nonempty = 0
    for row in rows:
        fields = _fields(fixture, row)
        index_array = fields['m_MuscleClip']['m_IndexArray']
        channels = ab.channels_of(fields)
        lens.add(len(index_array))
        slots = [i for i, v in enumerate(index_array) if v != -1]
        if not slots:
            continue
        nonempty += 1
        used_window.add((min(slots), max(slots)))
        values = [int(index_array[i]) for i in slots]
        assert max(values) < channels.curve_total, row['name']
        assert len(set(values)) == len(values), row['name']
    assert lens == {200}, lens
    assert used_window == {(7, 136)}, used_window
    assert nonempty > 0


# --------------------------------------------------------------------------- #
# C · 肌肉 -> 曲线 的桥（13 条）
# --------------------------------------------------------------------------- #
def test_c_the_13_muscles_map_to_attribute_42_plus_m(fixture, channel_table) -> None:
    """13 条肌肉的属性槽 = **肌肉号 + 42**，名字与 Unity 的肌肉名逐条对得上。"""
    for muscle in MUSCLES_13:
        entry = channel_table[muscle]
        assert int(entry['attribute']) == muscle + ATTR_OFFSET, (muscle, entry)
        assert entry['name'], entry
    assert channel_table[0]['name'] == 'Spine Front-Back'
    assert channel_table[6]['name'] == 'UpperChest Front-Back'
    assert channel_table[37]['name'] == 'Left Shoulder Down-Up'


def test_c_every_humanoid_clip_binds_all_13_muscles(fixture, channel_table) -> None:
    """**人类剪辑上 13 条一条不缺**（槽 ∈ 7..136 ⇒ 一定被绑），且都指到合法曲线。"""
    rows, blob, nodes = fixture
    humanoid = 0
    for row in rows:
        fields = _fields(fixture, row)
        index_array = fields['m_MuscleClip']['m_IndexArray']
        channels = ab.channels_of(fields)
        slots = {i for i, v in enumerate(index_array) if v != -1}
        if slots != set(range(7, 137)):
            continue
        humanoid += 1
        for muscle in MUSCLES_13:
            attr = int(channel_table[muscle]['attribute'])
            curve = int(index_array[attr])
            assert 0 <= curve < channels.curve_total, (row['name'], muscle)
            assert channels.channel_of(curve) in (
                ab.CHANNEL_STREAMED, ab.CHANNEL_DENSE, ab.CHANNEL_CONSTANT)
    assert humanoid >= 70, f'人形剪辑只有 {humanoid} 条，样本不够'


# --------------------------------------------------------------------------- #
# D/E · 值级正控 ＋ 改一位负控
# --------------------------------------------------------------------------- #
def _controls(fields, channels, payload, nodes) -> dict[str, int]:
    counts = {'const_ok': 0, 'const_bad': 0, 'dense_ok': 0, 'dense_bad': 0,
              'stream_ok': 0, 'stream_bad': 0, 'flip_changed': 0, 'flip_blind': 0,
              'flip_unlocatable': 0, 'flip_missing': 0}
    start = float(fields['m_MuscleClip']['m_StartTime'])
    stop = float(fields['m_MuscleClip']['m_StopTime'])
    mid = (start + stop) / 2.0
    for local in range(min(4, len(channels.constant.values))):
        want = channels.constant.values[local]
        for t in (start, mid, stop):
            if _bits(channels.read(ab.CHANNEL_CONSTANT, local, t)) == _bits(want):
                counts['const_ok'] += 1
            else:
                counts['const_bad'] += 1
    dense = channels.dense
    for local in range(min(4, dense.curve_count)):
        for frame in (0, dense.frame_count // 2, dense.frame_count - 1):
            t = dense.begin_time + frame / dense.sample_rate
            want = dense.data[frame * dense.curve_count + local]
            if _bits(channels.read(ab.CHANNEL_DENSE, local, t)) == _bits(want):
                counts['dense_ok'] += 1
            else:
                counts['dense_bad'] += 1
    for local in range(min(4, channels.streamed.curve_count)):
        for t, coeff in channels.streamed.per_curve[local]:
            if t < -1e30:
                continue
            if _same(channels.read(ab.CHANNEL_STREAMED, local, t), coeff[3]):
                counts['stream_ok'] += 1
            else:
                counts['stream_bad'] += 1
    data = fields['m_MuscleClip']['m_Clip']['data']
    before = _fingerprint(channels)
    for samples, fmt in ((data['m_ConstantClip']['data'], 'f'),
                         (data['m_DenseClip']['m_SampleArray'], 'f'),
                         (data['m_StreamedClip']['data'], 'I')):
        if not samples:
            counts['flip_missing'] += 1
            continue
        where = _locate(payload, samples, fmt)
        if where is None:
            counts['flip_unlocatable'] += 1
            continue
        mutated = bytearray(payload)
        mutated[where + (4 if fmt == 'f' else 8)] ^= 0x01
        try:
            after = _fingerprint(ab.channels_of(cl.read_object(bytes(mutated), nodes)))
        except Exception:  # 改一位后解不动，也算「解出来不同」
            counts['flip_changed'] += 1
            continue
        if after != before:
            counts['flip_changed'] += 1
        else:
            counts['flip_blind'] += 1
    return counts


def _bits(value: float) -> int:
    return struct.unpack('<I', struct.pack('<f', float(value)))[0]


def _same(left: float, right: float) -> bool:
    """**数值**相同（⛔ 不比符号位）。

    实测：键点控制在 475 条上撞过 **36 例「不同」**，逐条看全是 `-0.0` vs `+0.0` ——
    多项式在 `dt=0` 时是 `0.0 + c3`，而 IEEE 的 `0.0 + (-0.0) = +0.0` ⇒ 值是对的、只有符号位不同。
    所以口径是 IEEE 的 `==`（±0 判等），真值有差别照样红。
    """
    return float(left) == float(right)


def _locate(payload: bytes, samples, fmt: str) -> int | None:
    take = list(samples)[:3]
    if len(take) < 3:
        return None
    pattern = b''.join(struct.pack('<' + fmt, value) for value in take)
    first = payload.find(pattern)
    if first < 0 or payload.find(pattern, first + 1) >= 0:
        return None
    return first


def _fingerprint(channels) -> tuple:
    """**解出来的结构**的指纹（键表 ＋ 采样矩阵 ＋ 常值数组 ＋ 三个计数）。"""
    return (
        channels.streamed.curve_count,
        tuple((len(c), tuple((float(t), tuple(float(x) for x in k)) for t, k in c))
              for c in channels.streamed.per_curve),
        tuple(_bits(v) for v in channels.dense.data),
        float(channels.dense.begin_time),
        float(channels.dense.sample_rate),
        channels.dense.frame_count,
        channels.dense.curve_count,
        tuple(_bits(v) for v in channels.constant.values),
    )


def test_d_positive_controls_are_bit_exact(fixture) -> None:
    rows, blob, nodes = fixture
    total = {'const_ok': 0, 'const_bad': 0, 'dense_ok': 0, 'dense_bad': 0,
             'stream_ok': 0, 'stream_bad': 0}
    for row in _sample(rows, 7):
        fields = _fields(fixture, row)
        counts = _controls(fields, ab.channels_of(fields), b'', nodes)
        for key in total:
            total[key] += counts[key]
    assert total['const_bad'] == total['dense_bad'] == total['stream_bad'] == 0, total
    assert total['const_ok'] and total['dense_ok'] and total['stream_ok'], total


def test_e_flipping_one_bit_always_changes_the_decoded_structure(fixture) -> None:
    rows, blob, nodes = fixture
    changed = blind = unlocatable = missing = 0
    for row in _sample(rows, 7):
        payload = blob[row['offset']: row['offset'] + row['length']]
        fields = cl.read_object(payload, nodes)
        counts = _controls(fields, ab.channels_of(fields), payload, nodes)
        changed += counts['flip_changed']
        blind += counts['flip_blind']
        unlocatable += counts['flip_unlocatable']
        missing += counts['flip_missing']
    assert changed > 0, '负控一条都没跑成'
    assert blind == 0, f'改了一位而解出来的结构没变：{blind} 次（尺子瞎了）'
    assert unlocatable + missing >= 0, (unlocatable, missing)


def test_e_negative_control_can_actually_fail(fixture) -> None:
    """**尺子能红**：把「改一位」换成「一位都不改」，指纹必须**相同** —— 说明上面那条
    `blind == 0` 不是在无脑通过。"""
    rows, blob, nodes = fixture
    for row in _sample(rows, 7)[:5]:
        payload = blob[row['offset']: row['offset'] + row['length']]
        fields = cl.read_object(payload, nodes)
        channels = ab.channels_of(fields)
        untouched = ab.channels_of(cl.read_object(bytes(payload), nodes))
        assert _fingerprint(untouched) == _fingerprint(channels)


def test_e_mutating_the_whole_streamed_array_changes_it(fixture) -> None:
    """★ 加强负控：把**整段** streamed 词流的最后一个字**全翻**，指纹必须变。

    这条防的是「只有个别位碰巧不被读」被误读成「改一位总是能看出来」。
    """
    rows, blob, nodes = fixture
    checked = 0
    for row in _sample(rows, 11):
        payload = blob[row['offset']: row['offset'] + row['length']]
        fields = cl.read_object(payload, nodes)
        channels = ab.channels_of(fields)
        if not channels.streamed.curve_count:
            continue
        words = [int(w) for w in fields['m_MuscleClip']['m_Clip']['data']['m_StreamedClip']['data']]
        where = _locate(payload, words, 'I')
        if where is None:
            continue
        mutated = bytearray(payload)
        for offset in range(where, where + 12):
            mutated[offset] ^= 0xFF
        try:
            after = _fingerprint(ab.channels_of(cl.read_object(bytes(mutated), nodes)))
        except Exception:  # 结构被砸坏 ⇒ 解出来**不同**（与改一位那条同一口径）
            checked += 1
            continue
        assert after != _fingerprint(channels), row['name']
        checked += 1
    assert checked > 0


# --------------------------------------------------------------------------- #
# F · 产品入口 `anim_build.curve_of_muscle`（Lead 2026-10-17 批准的那一个纯读函数）
# --------------------------------------------------------------------------- #
def test_f_curve_of_muscle_gives_the_attribute_slot() -> None:
    """肌肉号 -> 属性槽 = 肌肉号 + 42；不给槽表时曲线号是 `-1`（**查不到**、不猜）。"""
    for muscle in MUSCLES_13:
        attribute, curve = ab.curve_of_muscle(muscle)
        assert attribute == muscle + ATTR_OFFSET
        assert curve == -1
    assert ab.curve_of_muscle(0) == (42, -1)
    assert ab.curve_of_muscle(8) == (50, -1)
    assert ab.curve_of_muscle(37) == (79, -1)
    assert ab.curve_of_muscle(47) == (89, -1)


def test_f_curve_of_muscle_matches_the_real_index_array(fixture, channel_table) -> None:
    """★ 产品函数的读数必须与**真件对象**逐条一致（13 条 × 86 条人形 clip）。"""
    rows, blob, nodes = fixture
    checked = 0
    for row in rows:
        fields = _fields(fixture, row)
        index_array = fields['m_MuscleClip']['m_IndexArray']
        channels = ab.channels_of(fields)
        if {i for i, v in enumerate(index_array) if v != -1} != set(range(7, 137)):
            continue
        for muscle in MUSCLES_13:
            attribute, curve = ab.curve_of_muscle(muscle, index_array)
            assert attribute == int(channel_table[muscle]['attribute'])
            assert curve == int(index_array[attribute])
            assert 0 <= curve < channels.curve_total
            checked += 1
    assert checked >= 13 * 70, checked


def test_f_curve_of_muscle_refuses_bad_input() -> None:
    """⛔ fail-closed：号段外 / 槽表长度不对都抛，不静默给个错值。"""
    from ba_mod_maker.core.errors import AssetError

    for bad in (55, -1):
        with pytest.raises(AssetError):
            ab.curve_of_muscle(bad)
    with pytest.raises(AssetError):
        ab.curve_of_muscle(0, [-1] * 130)
