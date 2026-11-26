r"""D 线 · 写回（改时长）与**反向 Δ 还原**的机械判据。

**判据一行**：`pytest tests/test_anim_write.py -q -n 0` -> 通过数 **0 -> N**，≥ **8 条负控**。

## 量什么

| 段 | 判据 | 负控 |
|---|---|---|
| A | ★ 同长度：拉伸后对象字节数 / 流式词数**一个不变** | 改 k 到 0 / 负数必须抛 |
| B | ★ `m_ValueArrayDelta` 必须同步：**开了开关自证全中、关掉必须掉下来** | `sync_delta=False` 掉 |
| C | ★ 反向 Δ 还原：`sha256 == 改前`（J6 口径，真件逐条） | 长度不同 / 记录被改过必须抛 |
| D | 保真：k 拉伸再 1/k 拉回，逐点最大偏差**量出来**（真读数 8.4e-05 级） | 不量就写不出来 |
| E | dense 判定：**396/475** 可以就地拉伸，其余 79 条如实拒 | dense 被消费 -> 抛 |

## 真读数（`_work/animline/probe_stretch.py` · 379 条流式非空的 clip · k=2.5）

```
A 同长度（对象字节数不变）      379/379
A2 流式词数不变                 379/379
B 拉伸后 delta 自证重新全中      379/379
B2 负控（sync_delta=False）掉下来 379/379
C 反向 Δ 还原 sha256 == 改前     379/379
D k=2.5 拉伸再 1/k 拉回，逐点最大偏差 = 8.372e-05（soldier reload）
分类：streamed_ok 331 / streamed_dense_bad 48 / nostream_dense_bad 31 /
      nostream_dense_ok 36 / empty 29   ⇒ **就地可拉伸 396 / 475**（拒 79）
流式非空 379 · 其中可拉伸 331 · 331 里拉伸后逐字节不变 31（m_StopTime = 0.0 那类）
```

## F 段（2026-10-13 加）· 落包：一条 clip 的等长新字节**装回包里**

上面 A~E 段量的是「对象字节算得对不对」；F 段量的是**装得回去、撤得回来**：

| # | 判据 | 负控（必须红 / 拒） |
|---|---|---|
| F1 | 干跑零写入（包 sha256 与 mtime 逐位不变 ＋ **连 patches/ 都不建**） | —— |
| F2 | 真写后读回：目标 clip 变了、曲线数 / 词数 / 时长与预期一致 | 读回还是源那份 -> 尺子红 |
| F3 | 目标之外的 clip 逐字节不变 ＋ writer 的 H3「期望改动之外 **0 B**」 | 另一条也变了 -> 尺子红 |
| F4 | 反向 Δ 补丁柜落地（无整包副本）⇒ 还原后 sha256 == 改前 | 长度不等 -> 拒（退出码 2） |
| F5 | anim stretch --out 写出的对象字节，anim apply --from 装得回去 | 名字对不上 -> 拒 |

靶子：_work 里的离线夹具**现造的小包**（2 条 AnimationClip / 27 KB），⛔ 不碰游戏目录。

## 未做（如实）

* **dense 变时长**（要改 `m_FrameCount` / `m_SampleArray` 长度 = 改对象长度）—— 不做；
* **增删关键帧 / 整条换 clip** —— 不做（要动 SerializedFile 的对象表）；
* **包级**（UnityFS 重打包 + catalog 的 `m_Crc` / `m_BundleSize`）—— 不在本模块，见可改性判定 §③。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
import sys
import types
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import anim_build, anim_write, builder, clone
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

_REPO = Path(__file__).resolve().parents[1]
_FIXTURES = WORK / "anim-line" / "fixtures"
_UNITS_META = WORK / "w10_ref" / "units_meta.bin"

T72B_HATCH_PID = 6926808325590346151
#: 真件里第一条**流式非空**的 clip（用例的"随便一条真件"）。
LAV_M_DEFAULT_PID = 76938473561867112


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"缺 {path.name}（先跑 _work/anim-line 的 p2/p3 探针）")
    return path


@pytest.fixture(scope="module")
def clip_nodes():
    meta = _require(_UNITS_META).read_bytes()
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    return table.entry_for(anim_build.CLASS_ANIMATION_CLIP).nodes


@pytest.fixture(scope="module")
def clips():
    rows = json.loads(
        _require(_FIXTURES / "clips_index.json").read_text(encoding="utf-8"))["rows"]
    blob = _require(_FIXTURES / "clips_payloads.bin").read_bytes()
    assert len(rows) == 475
    return rows, blob


@pytest.fixture(scope="module")
def stretchable_clips(clips, clip_nodes):
    """**流式非空 且 可拉伸**的那 331 条（A/B/C 三段都只对它们断言）。"""
    rows, blob = clips
    out = []
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        fields = clone.read_object(raw, clip_nodes)
        if int(fields["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["curveCount"]) == 0:
            continue
        if anim_write.stretchable(fields)[0]:
            out.append((row, raw, fields))
    assert len(out) == 331, len(out)
    return out


@pytest.fixture(scope="module")
def streamed_clips(clips):
    """流式非空的那 379 条（(行, 原字节, 字段树) 三元组）。"""
    rows, blob = clips
    out = []
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        fields = clone.read_object(raw, _NODES[0])
        if int(fields["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["curveCount"]) > 0:
            out.append((row, raw, fields))
    return out


_NODES: list = []


@pytest.fixture(autouse=True, scope="module")
def _bind_nodes(clip_nodes) -> None:
    _NODES[:] = [clip_nodes]


def test_stretchable_classification_matches_the_census(clips, clip_nodes) -> None:
    """★ 就地可拉伸 = **396 / 475**，其余 **79** 条必须拒（逐条跑一遍，不抄中间数）。

    拒的 79 条是"dense 区被绑定消费"的（例 `Prone_run` 那类人形 dense clip）：dense 的窗口由
    `m_FrameCount × m_SampleRate` 决定，只改 `m_StopTime` 会让它变成**变速采样**。
    """
    rows, blob = clips
    ok = 0
    bad = 0
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        fields = clone.read_object(raw, clip_nodes)
        can, _why = anim_write.stretchable(fields)
        if can:
            ok += 1
            anim_write.stretch_clip(fields, 2.0, payload=raw, nodes=clip_nodes)
        else:
            bad += 1
            with pytest.raises(AssetError):
                anim_write.stretch_clip(fields, 2.0, payload=raw, nodes=clip_nodes)
    assert (ok, bad) == (396, 79), (ok, bad)


def test_stretch_is_same_length_on_every_streamed_clip(stretchable_clips, clip_nodes) -> None:
    """★ A：真件 **331** 条「流式非空 且 可拉伸」的 clip，拉伸后**字节数与词数一个不变**。

    （流式非空的一共 379 条；其中 48 条的 dense 区被绑定消费 ⇒ 不可就地拉伸，见 E 段。）
    """
    checked = 0
    for row, raw, fields in stretchable_clips:
        after, rep = anim_write.stretch_clip(fields, 2.5, payload=raw, nodes=clip_nodes)
        assert rep.size_unchanged, (row["name"], rep.old_size, rep.new_size)
        assert rep.words == row["streamed_words"], row["name"]
        assert len(clone.write_object(after, clip_nodes)) == len(raw), row["name"]
        checked += 1
    assert checked == 331, checked


def test_stretch_resyncs_the_delta_snapshot(stretchable_clips, clip_nodes) -> None:
    """★ B：拉伸后 `m_ValueArrayDelta` 必须跟着改 —— **自证重新全中**。"""
    for row, raw, fields in stretchable_clips:
        _after, rep = anim_write.stretch_clip(fields, 2.5, payload=raw, nodes=clip_nodes)
        assert rep.delta_ok, (row["name"], rep.delta_hits, rep.delta_total)
        assert rep.delta_total == row["curve_total"], row["name"]


def test_negative_control_delta_goes_red_without_the_sync(stretchable_clips, clip_nodes) -> None:
    """★ 负控：`sync_delta=False` 时自证**必须掉下来**（否则那条尺子恒真）。"""
    red = 0
    for _row, raw, fields in stretchable_clips:
        _after, rep = anim_write.stretch_clip(
            fields, 3.0, payload=raw, nodes=clip_nodes, sync_delta=False)
        if rep.delta_hits < rep.delta_total:
            red += 1
    assert red == 331, red


def test_inverse_delta_restores_every_streamed_clip(stretchable_clips, clip_nodes) -> None:
    """★ C：反向 Δ 还原 —— **sha256 == 改前**（真件 379/379 · 逐字节相同）。

    ⚠ 真件里有一类 clip 拉伸后**逐字节不变**（`m_StopTime = 0.0`，乘 k 还是 0 ⇒ 没有字节可改）：
    真读数 **31 / 331**（`_work/animline/probe_same2.py`）。那不是成功也不是失败，
    是"这条 clip 没有可拉伸的时间轴"。本条把**没改的数出来**（⛔ 不静默当成通过），
    并断言"**改了的**那些必须还原回原样"。
    """
    unchanged = 0
    for row, raw, fields in stretchable_clips:
        after, _rep = anim_write.stretch_clip(fields, 2.5, payload=raw, nodes=clip_nodes)
        changed = clone.write_object(after, clip_nodes)
        if changed == raw:
            unchanged += 1
        record = anim_write.inverse_record(raw)
        back = anim_write.restore(changed, record)
        assert anim_write.clip_sha256(back) == anim_write.clip_sha256(raw), row["name"]
        assert back == raw, row["name"]
    assert unchanged == 31, unchanged


def test_t72b_hatch_stretch_readings(clips, clip_nodes) -> None:
    """靶子那一条的具体读数（改前 → 改后 → 还原）。"""
    rows, blob = clips
    row = next(r for r in rows if r["path_id"] == T72B_HATCH_PID)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, clip_nodes)
    after, rep = anim_write.stretch_clip(fields, 2.5, payload=raw, nodes=clip_nodes)
    assert rep.old_stop == pytest.approx(1.0)
    assert rep.new_stop == pytest.approx(2.5)
    assert rep.size_unchanged and rep.words == 68
    assert rep.delta_max_change == pytest.approx(0.0, abs=1e-6)
    assert rep.delta_ok and rep.delta_total == 4
    assert rep.dense.curve_count == 0, "T-72B hatch 的 dense 曲线数是 0（表里写着）"
    # m_FrameCount **不许动**（dense 残留位，没有曲线消费它）
    before_fc = fields["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_FrameCount"]
    after_fc = after["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_FrameCount"]
    assert before_fc == after_fc == 62
    # 词数不变 ⇒ 时间片结构没动
    assert len(after["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"]) == 68


def test_stretch_fidelity_after_round_trip(clips, clip_nodes) -> None:
    """★ D：k 拉伸再 1/k 拉回，逐点最大偏差 —— **量出来并在用例里钉住量级**。

    真读数（379 条里最差的一条）= **8.372e-05**（soldier reload）。
    这条不是"越接近 0 越好"：float32 的除法/乘法自己就会引入这个量级的误差
    （研究线 p18 的 8.4e-08 量的是**同一时刻**的求值，不是往返）。
    """
    rows, blob = clips
    worst = 0.0
    worst_name = ""
    for row in rows:
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        fields = clone.read_object(raw, clip_nodes)
        if not anim_write.stretchable(fields)[0]:
            continue
        if int(fields["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["curveCount"]) == 0:
            continue
        after, _rep = anim_write.stretch_clip(fields, 2.5, payload=raw, nodes=clip_nodes)
        raw_after = clone.write_object(after, clip_nodes)
        back, _rep2 = anim_write.stretch_clip(
            clone.read_object(raw_after, clip_nodes), 1.0 / 2.5,
            payload=raw_after, nodes=clip_nodes)
        ch1 = anim_build.channels_of(fields)
        ch2 = anim_build.channels_of(back)
        start = float(fields["m_MuscleClip"]["m_StartTime"])
        stop = float(fields["m_MuscleClip"]["m_StopTime"])
        for i in range(min(ch1.curve_total, ch2.curve_total)):
            for j in range(21):
                t = start + (stop - start) * j / 20.0
                delta = abs(ch1.value(i, t) - ch2.value(i, t))
                if delta > worst:
                    worst, worst_name = delta, str(row["name"])
    assert 1e-7 < worst < 1e-3, (worst, worst_name)
    assert worst_name, "至少要量到一条"


def test_negative_control_non_positive_k_is_refused(clips, clip_nodes) -> None:
    """负控：k <= 0 必须抛（0 会让整条曲线塌成一个点）。"""
    rows, blob = clips
    row = next(r for r in rows if r["path_id"] == T72B_HATCH_PID)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, clip_nodes)
    for bad in (0.0, -1.0):
        with pytest.raises(AssetError):
            anim_write.stretch_clip(fields, bad, payload=raw, nodes=clip_nodes)


def test_negative_control_dense_consumed_clip_is_refused(clips, clip_nodes) -> None:
    """负控：dense 区被消费的 clip（真件 210 条，例 humanoid 的 Prone_run）必须抛。

    ⛔ 不是"没检查"，是**逐条判过**：`m_DenseClip` 的窗口由 m_FrameCount × m_SampleRate
    决定，只改 m_StopTime 会让它变成变速采样。
    """
    rows, blob = clips
    name = "Prone_run"
    row = next(r for r in rows if r["name"] == name and r["bindings"] == 130)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, clip_nodes)
    can, why = anim_write.stretchable(fields)
    assert can is False, why
    with pytest.raises(AssetError) as exc:
        anim_write.stretch_clip(fields, 2.0, payload=raw, nodes=clip_nodes)
    assert "dense" in str(exc.value)


def test_negative_control_restore_refuses_a_length_mismatch() -> None:
    """负控：反向 Δ 记录的长度与改后字节不同 -> 必须抛（那是变长改动）。"""
    record = anim_write.inverse_record(b"abcd")
    with pytest.raises(AssetError):
        anim_write.restore(b"abcdef", record)


def test_negative_control_restore_refuses_a_tampered_record() -> None:
    """负控：记录里的字节被改过（sha256 对不上）-> 必须抛，⛔ 不许"照它还原"。"""
    record = anim_write.inverse_record(b"abcd")
    tampered = dict(record)
    tampered["bytes"] = b"abce"
    with pytest.raises(AssetError):
        anim_write.restore(b"abcd", tampered)
    record_bad_size = dict(record)
    record_bad_size["size"] = 3
    with pytest.raises(AssetError):
        anim_write.restore(b"abcd", record_bad_size)


def test_negative_control_stream_sentinels_are_not_scaled(clips, clip_nodes) -> None:
    """负控：流式的哨兵时刻（-FLT_MAX / +inf）**不许乘 k**（乘了直接溢出 float32）。

    真件首条记录是 0xff7fffff = -FLT_MAX；这条用字节级判据钉住：拉伸后首尾两片仍然哨兵。
    """
    rows, blob = clips
    row = next(r for r in rows if r["path_id"] == T72B_HATCH_PID)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, clip_nodes)
    after, _rep = anim_write.stretch_clip(fields, 2.5, payload=raw, nodes=clip_nodes)
    words = after["m_MuscleClip"]["m_Clip"]["data"]["m_StreamedClip"]["data"]
    buf = struct.pack(f"<{len(words)}I", *words)
    first = struct.unpack_from("<f", buf, 0)[0]
    assert first <= -3.0e38, first
    # 末片：最后一个 count=0 的记录，time 必须是 +inf
    pos = 0
    last_time = None
    while pos + 2 <= len(words):
        last_time, count = struct.unpack_from("<fI", buf, pos * 4)
        pos += 2 + 5 * count
        if count == 0:
            break
    assert last_time == float("inf"), last_time


def test_negative_control_hash_ruler_catches_a_single_byte_edit() -> None:
    """负控（哈希尺子本身）：改一个字节 -> sha256 必变（⛔ 不许拿一个恒真的哈希当尺子）。"""
    base = hashlib.sha256(b"x" * 64).hexdigest()
    assert hashlib.sha256(b"x" * 63 + b"y").hexdigest() != base
    assert anim_write.clip_sha256(b"x" * 64) == base


def test_negative_control_empty_clip_has_nothing_to_stretch(clips, clip_nodes) -> None:
    """负控：空 clip（0 曲线 0 绑定）拉伸**没有曲线可动** —— 如实报，不许当成功。

    ⚠ 真件里 `m_DenseClip.m_CurveCount` 可以**不为 0 而没有曲线消费它**（本例 7 条），
    所以本条断言的是：`delta_total == 0`（没有曲线）、`streamed_keys == 0`（没有键），
    并且 **除了 `m_StopTime` 那 4 个字节，对象其余部分逐字节不变**；dense 采样数组原样。
    """
    rows, blob = clips
    row = next(r for r in rows if r["path_id"] == LAV_M_DEFAULT_PID)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, clip_nodes)
    after, rep = anim_write.stretch_clip(fields, 2.0, payload=raw, nodes=clip_nodes)
    assert rep.delta_total == 0 and rep.streamed_keys == 0
    assert rep.size_unchanged
    out = clone.write_object(after, clip_nodes)
    diff = [i for i, (a, b) in enumerate(zip(raw, out, strict=True)) if a != b]
    assert len(diff) <= 4, diff[:12]
    assert (after["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_SampleArray"]
            == fields["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]["m_SampleArray"]), \
        "dense 采样数组一个字节都不许动"


# --------------------------------------------------------------------------- #
# F 段 · 落包（anim apply）：等长写回 ＋ 写后读回 ＋ 反向 Δ 还原
#
# ⛔ 全程不碰游戏目录：靶子是用 _work 里的离线夹具**现造的小包**（2 条 clip / 27 KB）；
#    units 那个 3.38 GB 的真包不在这几条用例里（包级判据不需要它）。
# --------------------------------------------------------------------------- #
_ANIM_FIX = WORK / "anim-line" / "fixtures"
_ANIM_META = WORK / "w10_ref" / "units_meta.bin"
#: 同包里第二条 clip（用它证明「目标之外逐字节不变」）。
OTHER_CLIP_PID = -9207998336256044584


def _file_sha256(path: Path) -> str:
    """一个文件的 sha256（整文件还原那把尺子）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def small_bundle(tmp_path_factory) -> Path:
    """★ 用离线夹具造一个**小包**：2 条真件 AnimationClip ＋ AssetBundle 对象。

    四条真读数（量出来钉住）：包 **27,372 B** / 3 个对象 / 1 个 CAB- 节点 /
    里面就是 T72B hatch（2,572 B）与 ATGM（3,096 B）这两条**真件**对象字节。
    """
    index = _ANIM_FIX / "clips_index.json"
    blob_path = _ANIM_FIX / "clips_payloads.bin"
    if not (index.is_file() and blob_path.is_file() and _ANIM_META.is_file()):
        pytest.skip("缺 _work 里的动画夹具（clips_index / clips_payloads / units_meta）")
    rows = json.loads(index.read_text(encoding="utf-8"))["rows"]
    blob = blob_path.read_bytes()
    specs = []
    for row in rows:
        if int(row["path_id"]) not in (T72B_HATCH_PID, OTHER_CLIP_PID):
            continue
        raw = blob[row["offset"]: row["offset"] + row["length"]]
        specs.append(builder.ObjectSpec(
            class_id=anim_build.CLASS_ANIMATION_CLIP, fields=None,
            path_id=int(row["path_id"]), payload=raw, label=str(row["name"])))
    assert len(specs) == 2, [s.path_id for s in specs]
    plan = builder.build_object_bundle(
        objects=specs,
        bundle_name="pkglineanim_assets_all_test.bundle",
        source_meta=_ANIM_META.read_bytes(),
        container=[("Assets/pkgline/anim/test.clip", T72B_HATCH_PID)],
        preload_all=True,
    )
    out = tmp_path_factory.mktemp("pkgline-anim") / "anim.bundle"
    out.write_bytes(plan.data)
    assert out.stat().st_size == len(plan.data)
    return out


@pytest.fixture
def bundle_copy(small_bundle: Path, tmp_path: Path) -> Path:
    """那把小包的**副本**（每条用例一份，⛔ 一个字节都不写原件）。"""
    target = tmp_path / "anim.bundle"
    target.write_bytes(small_bundle.read_bytes())
    return target


def _apply_args(bundle: Path, project: Path, **over: object) -> argparse.Namespace:
    """anim apply 的入参（形状与 CLI 解析出来的一致）。

    game 指向 tmp（**不是**游戏根）：⛔ 不让这条路去碰游戏目录 —— 
    _game_root 只在给了 --game 时 stat 那一个目录。
    """
    base: dict[str, object] = {
        "anim_action": "apply",
        "bundle": str(bundle),
        "clip": str(T72B_HATCH_PID),
        "k": 2.5,
        "from_file": None,
        "write": False,
        "json_out": False,
        "project_root": str(project),
        "game": str(project),
        "catalog": None,
        # 靶子是 tmp 里的副本，游戏占不到它 ⇒ 允许游戏在跑（这条**不是**放开"游戏在跑也写游戏包"）
        "allow_running": True,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _clip_payloads(bundle: Path) -> dict[int, bytes]:
    from ba_mod_maker.cli import anim as anim_cli

    store = anim_cli.ClipStore.from_bundle(bundle)
    return {record.path_id: record.payload for record, _fields in store.rows()}


def _stretched_bytes(bundle: Path) -> bytes:
    """把靶子那条 clip 拉伸 2.5 倍后的**对象字节**（就是 stretch --out 写出来的那种）。"""
    from ba_mod_maker.cli import anim as anim_cli

    store = anim_cli.ClipStore.from_bundle(bundle)
    record, fields = store.by_clip(str(T72B_HATCH_PID))
    after, _report = anim_write.stretch_clip(
        fields, 2.5, payload=record.payload, nodes=store.nodes())
    return clone.write_object(after, store.nodes())


# --------------------------------------------------------------------------- #
# F1 干跑零写入
# --------------------------------------------------------------------------- #
def test_apply_dry_run_writes_nothing_and_builds_no_patch_dir(bundle_copy, tmp_path) -> None:
    """★ F1：干跑 -> 包 sha256 与 mtime 逐位不变，且**连 patches/ 都不建**。"""
    from ba_mod_maker.cli import anim as anim_cli

    before_sha = _file_sha256(bundle_copy)
    before_mtime = bundle_copy.stat().st_mtime_ns
    code = anim_cli.run(_apply_args(bundle_copy, tmp_path))
    assert code == anim_cli.EXIT_OK
    assert _file_sha256(bundle_copy) == before_sha
    assert bundle_copy.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / "patches").exists(), "干跑建了补丁柜目录（不该写盘）"


# --------------------------------------------------------------------------- #
# F2 + F3 + F4 真写：读回 + 其它不变 + 还原
# --------------------------------------------------------------------------- #
def test_apply_write_readback_and_restore(small_bundle, bundle_copy, tmp_path) -> None:
    """★ F2/F3/F4：真写 -> 读回目标变了且形状对、其它 clip 逐字节不变、还原 sha256 回改前。"""
    from ba_mod_maker.asset_kernel import reverse_delta
    from ba_mod_maker.cli import anim as anim_cli

    before_sha = _file_sha256(bundle_copy)
    source = _clip_payloads(small_bundle)
    assert source[T72B_HATCH_PID] != source[OTHER_CLIP_PID]

    code = anim_cli.run(_apply_args(bundle_copy, tmp_path, write=True))
    assert code == anim_cli.EXIT_OK, "真写那一档没通过（写后复核 / 零写入判据）"
    assert _file_sha256(bundle_copy) != before_sha, "说写了，文件却没变"

    store = anim_cli.ClipStore.from_bundle(bundle_copy)
    record, fields = store.by_clip(str(T72B_HATCH_PID))
    shape = anim_write.shape_of(fields, record.payload)
    assert record.payload == _stretched_bytes(small_bundle), "读回来的不是 k=2.5 那份字节"
    assert (shape.curves, shape.words) == (4, 68), (shape.curves, shape.words)
    assert shape.duration == pytest.approx(2.5, abs=1e-5)
    assert shape.payload_bytes == len(source[T72B_HATCH_PID]), "等长改写：字节数必须一个不变"

    now = _clip_payloads(bundle_copy)
    ok_others, compared, bad = anim_write.other_clips_unchanged(
        source, now, T72B_HATCH_PID)
    assert ok_others and compared == 1, (compared, bad)
    assert now[T72B_HATCH_PID] != source[T72B_HATCH_PID], "目标对象没变"
    assert now[OTHER_CLIP_PID] == source[OTHER_CLIP_PID], "别的 clip 被改了"

    patch_dir = tmp_path / "patches"
    assert (patch_dir / "install.rmod.json").is_file(), \
        "没落反向 Δ 补丁（说明落进了 patch_store is None 那条拷整包旧链）"
    assert (patch_dir / "install.rmod.bin").is_file()
    assert not list(tmp_path.rglob("*.bak")), "落了整包副本 —— 那是拷整包旧链的形状"

    report = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path)).restore()
    assert report.ok, report.lines()
    assert _file_sha256(bundle_copy) == before_sha, "还原后 sha256 没回到改前"


def test_write_clip_reports_zero_bytes_outside_the_change(bundle_copy, tmp_path) -> None:
    """★ F3 那半条（整包级）：writer 的 H3「期望改动之外」必须 **0 B**。

    上面那条只比了**包里的 clip**；H3 比的是**整个解压流**（含所有 classID 的对象）——
    两个都要有，缺一个就会漏掉"别的类的对象被顺手改了"。
    """
    report = anim_write.write_clip(
        bundle_copy, T72B_HATCH_PID, _stretched_bytes(bundle_copy),
        name="T72B hatch", dry_run=False, project_root=tmp_path, game_root=None,
        allow_running_game=True, patch_label="pytest anim apply",
    )
    h3 = report.verify["H3 其它未变（平移后坐标）"]
    assert h3["ok"] is True
    assert h3["diff_bytes"] == 0, h3["detail"]
    assert report.patch is not None and report.patch.is_file()
    assert report.patch.parent == tmp_path / "patches"


# --------------------------------------------------------------------------- #
# F5 缺口本身：stretch --out 的产物装得回去
# --------------------------------------------------------------------------- #
def test_apply_from_installs_the_stretch_output(bundle_copy, tmp_path) -> None:
    """★ F5：anim stretch --out 写出来的**对象字节文件**，anim apply --from 装得回去。"""
    from ba_mod_maker.cli import anim as anim_cli

    out = tmp_path / "stretched.bin"
    out.write_bytes(_stretched_bytes(bundle_copy))

    code = anim_cli.run(_apply_args(
        bundle_copy, tmp_path, k=None, from_file=str(out), write=True))
    assert code == anim_cli.EXIT_OK
    record, fields = anim_cli.ClipStore.from_bundle(bundle_copy).by_clip(str(T72B_HATCH_PID))
    assert record.payload == out.read_bytes()
    assert anim_write.shape_of(fields, record.payload).duration == pytest.approx(2.5, abs=1e-5)


# --------------------------------------------------------------------------- #
# 负控：三条闸都要能红
# --------------------------------------------------------------------------- #
def test_negative_control_apply_refuses_a_length_change(bundle_copy, tmp_path, capsys) -> None:
    """负控：--from 那份**字节数与源不等** -> 拒（退出码 2），包与补丁柜一个字节都没动。

    样本造法是**真的改了东西**：往 m_ConstantClip.data 里加一个数 ⇒ 对象 2,572 -> 2,576 B，
    名字与曲线读数都还是合法的 —— 只有"等长"这条不成立。
    """
    from ba_mod_maker.cli import anim as anim_cli

    before_sha = _file_sha256(bundle_copy)
    store = anim_cli.ClipStore.from_bundle(bundle_copy)
    record, fields = store.by_clip(str(T72B_HATCH_PID))
    padded = dict(fields)
    muscle = dict(fields["m_MuscleClip"])
    clip = dict(muscle["m_Clip"])
    inner = dict(clip["data"])
    constant = dict(inner["m_ConstantClip"])
    constant["data"] = list(constant["data"]) + [1.0]
    inner["m_ConstantClip"] = constant
    clip["data"] = inner
    muscle["m_Clip"] = clip
    padded["m_MuscleClip"] = muscle
    out = tmp_path / "padded.bin"
    out.write_bytes(clone.write_object(padded, store.nodes()))
    assert out.stat().st_size == len(record.payload) + 4, "样本没造对（长度得真的不等）"

    code = anim_cli.run(_apply_args(
        bundle_copy, tmp_path, k=None, from_file=str(out), json_out=True))
    assert code == anim_cli.EXIT_UNUSABLE
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["ok"] is False and "不等" in doc["error"]["message"], doc
    assert _file_sha256(bundle_copy) == before_sha
    assert not (tmp_path / "patches").exists()


def test_negative_control_apply_refuses_an_unknown_clip(bundle_copy, tmp_path) -> None:
    """负控：包里没有这条 clip -> 用不了（退出码 2），一个字节都不写。"""
    from ba_mod_maker.cli import anim as anim_cli

    before_sha = _file_sha256(bundle_copy)
    code = anim_cli.run(_apply_args(bundle_copy, tmp_path, clip="99999"))
    assert code == anim_cli.EXIT_UNUSABLE
    assert _file_sha256(bundle_copy) == before_sha
    assert not (tmp_path / "patches").exists()


def test_negative_control_apply_refuses_a_name_mismatch(bundle_copy, tmp_path) -> None:
    """负控：--from 那份是**另一条** clip（名字对不上） -> 拒，不静默写到错对象上。"""
    from ba_mod_maker.cli import anim as anim_cli

    source = _clip_payloads(bundle_copy)
    out = tmp_path / "other_clip.bin"
    out.write_bytes(source[OTHER_CLIP_PID])
    code = anim_cli.run(_apply_args(bundle_copy, tmp_path, k=None, from_file=str(out)))
    assert code == anim_cli.EXIT_UNUSABLE
    assert not (tmp_path / "patches").exists()


def test_negative_control_apply_refuses_bad_arguments(bundle_copy, tmp_path) -> None:
    """负控：--k 与 --from 都不给 / 都给 / k <= 0 -> 一律用不了（退出码 2）。"""
    from ba_mod_maker.cli import anim as anim_cli

    out = tmp_path / "stretched.bin"
    out.write_bytes(_stretched_bytes(bundle_copy))
    for over in ({"k": None}, {"from_file": str(out)}, {"k": 0.0}, {"k": -2.5}):
        code = anim_cli.run(_apply_args(bundle_copy, tmp_path, **over))
        assert code == anim_cli.EXIT_UNUSABLE, over
    assert not (tmp_path / "patches").exists()


# --------------------------------------------------------------------------- #
# 正控：写后复核那把尺子，喂已知的错样本**必须红**
# --------------------------------------------------------------------------- #
def test_writeback_ruler_can_go_red(small_bundle) -> None:
    """★ 正控：check_writeback 对**对的**样本绿、对四类已知错样本**各自报红**。"""
    from ba_mod_maker.cli import anim as anim_cli

    store = anim_cli.ClipStore.from_bundle(small_bundle)
    record, fields = store.by_clip(str(T72B_HATCH_PID))
    source = anim_write.shape_of(fields, record.payload)
    after, _rep = anim_write.stretch_clip(
        fields, 2.5, payload=record.payload, nodes=store.nodes())
    new_raw = clone.write_object(after, store.nodes())
    expected = anim_write.shape_of(after, new_raw)

    def _check(readback_shape, readback_raw):
        return anim_write.check_writeback(
            path_id=record.path_id, source_shape=source, source_raw=record.payload,
            expected_shape=expected, expected_raw=new_raw,
            readback_shape=readback_shape, readback_raw=readback_raw)

    good = _check(expected, new_raw)
    assert good.ok and good.bytes_match and good.target_changed and good.same_length

    # 错样本 ①：读回来还是**源**那一份（等于没写）
    stale = _check(source, record.payload)
    assert not stale.ok and not stale.bytes_match and not stale.target_changed
    assert any("没有可改的时间轴" in note for note in stale.notes)

    # 错样本 ②：字节对，但**时长**是 k=3 那份（形状对不上）
    other, _rep2 = anim_write.stretch_clip(
        fields, 3.0, payload=record.payload, nodes=store.nodes())
    other_raw = clone.write_object(other, store.nodes())
    wrong = _check(anim_write.shape_of(other, other_raw), other_raw)
    assert not wrong.ok
    assert any("时长" in item for item in wrong.shape_bad), wrong.shape_bad

    # 错样本 ③：字节数不等（等长那条判据）
    longer = _check(anim_write.shape_of(after, new_raw + b"\x00" * 4), new_raw + b"\x00" * 4)
    assert not longer.ok and not longer.same_length

    # 错样本 ④：其它 clip 被改了 -> other_clips_unchanged 必须红（并点名是哪一条）
    ok, compared, bad = anim_write.other_clips_unchanged(
        {1: b"aa", 2: b"bb"}, {1: b"aa", 2: b"bc"}, 1)
    assert not ok and compared == 1 and bad == (2,)
    ok2, n2, bad2 = anim_write.other_clips_unchanged({1: b"aa"}, {1: b"ab", 9: b"cc"}, 1)
    assert not ok2 and n2 == 1 and bad2 == (9,)


# --------------------------------------------------------------------------- #
# G 段 · 插件那一层的纯函数（blender_addon/anim_entry.py，按路径加载 + bpy 桩）
#
# ⛔ 不 import blender_addon 包：它顶层 import bpy（只有 Blender 里有）——
#    与 tests/test_anim_panel.py 同一个姿势。
# --------------------------------------------------------------------------- #
_ADDON = _REPO / "blender_addon"
_ADDON_PKG = "bamod_anim_entry_under_test"
_ANIM_ENTRY: types.ModuleType | None = None


def _bpy_prop(**kwargs: object) -> dict[str, object]:
    """桩属性：把 kwargs 原样记下来（用例只量「注解在不在」，不量 Blender 行为）。"""
    return dict(kwargs)


def _bpy_stub() -> None:
    """最小 bpy 桩 —— 缺什么**补什么**，⛔ 不覆盖别人已经装好的。

    ⚠ 第一版这里是「bpy 已在 sys.modules 就直接 return」（2026-10-13 真踩，返工一次）：
    同进程里只要有用例**先**装过一个**只有 Panel** 的桩（tests/test_anim_panel.py:36 就是），
    本模块就会拿那个残桩去加载 anim_entry ⇒ 模块级里
    class BamodAnimScan(bpy.types.Operator) 当场 AttributeError。
    那样的绿是「单跑绿、合跑红」的**假绿** —— 读数没覆盖到那条路。
    """
    bpy = sys.modules.get("bpy")
    if bpy is None:
        bpy = types.ModuleType("bpy")
        sys.modules["bpy"] = bpy
    props = getattr(bpy, "props", None)
    if props is None:
        props = types.SimpleNamespace()
        bpy.props = props  # type: ignore[attr-defined]
    types_ns = getattr(bpy, "types", None)
    if types_ns is None:
        types_ns = types.SimpleNamespace()
        bpy.types = types_ns  # type: ignore[attr-defined]
    for prop_name in ("BoolProperty", "FloatProperty", "IntProperty", "StringProperty"):
        if not hasattr(props, prop_name):
            setattr(props, prop_name, _bpy_prop)
    # ⚠ 2026-10-15：这张顶补清单**必须跟着产品走** —— 少了哪个基类名，
    # 加载 anim_entry（链上会 import panels_common）时就当场 AttributeError。
    # 本次补的是 `PropertyGroup`：`panels_common.BAMOD_BlockSettings(bpy.types.PropertyGroup)`
    # （那七个折叠块的开关）在导入链上，而残桩只有 `Panel` ⇒ 那条顶补用例红。
    for type_name in ("Panel", "Operator", "PropertyGroup"):
        if not hasattr(types_ns, type_name):
            setattr(types_ns, type_name, type(type_name, (), {}))


def _load_anim_entry(tag: str) -> types.ModuleType:
    """真的按路径加载一次；tag 造不重名的包名（负控要能**重新**加载一遍）。"""
    _bpy_stub()
    pkg = f"{_ADDON_PKG}_{tag}"
    package = types.ModuleType(pkg)
    package.__path__ = [str(_ADDON)]  # type: ignore[attr-defined]
    sys.modules[pkg] = package
    bridge_spec = importlib.util.spec_from_file_location(
        f"{pkg}.anim_bridge", _ADDON / "anim_bridge.py")
    assert bridge_spec is not None and bridge_spec.loader is not None
    bridge = importlib.util.module_from_spec(bridge_spec)
    sys.modules[bridge_spec.name] = bridge
    bridge_spec.loader.exec_module(bridge)
    package.anim_bridge = bridge  # type: ignore[attr-defined]
    spec = importlib.util.spec_from_file_location(f"{pkg}.anim_entry", _ADDON / "anim_entry.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    package.anim_entry = module  # type: ignore[attr-defined]
    return module


def _anim_entry() -> types.ModuleType:
    """按路径加载 blender_addon/anim_entry.py（连同它的相对 import anim_bridge）。"""
    global _ANIM_ENTRY
    if _ANIM_ENTRY is None:
        _ANIM_ENTRY = _load_anim_entry("main")
    return _ANIM_ENTRY


def test_addon_writeback_guards_can_go_red(small_bundle) -> None:
    """★ 正控：等长且真改了 -> 过；没改 / 变长 / 变短 -> 三条都**抛**。"""
    entry = _anim_entry()
    raw = _clip_payloads(small_bundle)[T72B_HATCH_PID]
    flipped = raw[:-1] + bytes([raw[-1] ^ 0x01])
    entry.writeback_guards(raw, flipped)  # 正控：这条不许抛
    for bad in (raw, raw + b"\x00", raw[:-1]):
        with pytest.raises(entry.AnimationActionError):
            entry.writeback_guards(raw, bad)
    with pytest.raises(entry.AnimationActionError, match="逐字节不变"):
        entry.writeback_guards(raw, raw)
    with pytest.raises(entry.AnimationActionError, match="不等"):
        entry.writeback_guards(raw, raw + b"\x00")


def test_addon_stretched_bytes_is_the_kernel_implementation(small_bundle) -> None:
    """插件那一层算新对象字节走的是**内核同一份**实现（⛔ 不是第二套）。"""
    from ba_mod_maker.cli import anim as anim_cli

    entry = _anim_entry()
    store = anim_cli.ClipStore.from_bundle(small_bundle)
    record, fields = store.by_clip(str(T72B_HATCH_PID))
    got = entry.stretched_clip_bytes(fields, record.payload, store.nodes(), 2.5)
    after, _report = anim_write.stretch_clip(
        fields, 2.5, payload=record.payload, nodes=store.nodes())
    assert got == clone.write_object(after, store.nodes())
    assert len(got) == len(record.payload), "等长改写：字节数一个不变"
    assert got != record.payload, "k=2.5 必须真的改了字节"


def test_addon_writeback_operators_carry_the_three_idnames() -> None:
    """口径锁：三颗按钮的 idname 与默认值（__init__.py 注册的就是这三个名字）。"""
    entry = _anim_entry()
    assert entry.BamodAnimWritebackCheck.bl_idname == "bamod.anim_writeback_check"
    assert entry.BamodAnimWritebackPlan.bl_idname == "bamod.anim_writeback_plan"
    assert entry.BamodAnimWriteback.bl_idname == "bamod.anim_writeback"
    # ⚠ bpy 的属性就**住在注解里**（k: bpy.props.FloatProperty(...) 求值后不落成类属性，
    #   Blender 自己收注解）—— 所以这里量的是注解与那个常量，不是 getattr。
    assert entry.K_PROP["default"] == 2.0
    for klass in (entry.BamodAnimWritebackPlan, entry.BamodAnimWriteback):
        assert "k" in klass.__annotations__, klass.__name__
    assert "confirmed" in entry.BamodAnimWriteback.__annotations__
    assert callable(entry.BamodAnimWriteback.invoke) and callable(entry.BamodAnimWriteback.execute)
    assert callable(entry.BamodAnimWritebackPlan.invoke)
    assert callable(entry.BamodAnimWritebackCheck.execute)
    for name in ("BamodAnimWriteback", "BamodAnimWritebackCheck", "BamodAnimWritebackPlan"):
        assert name in entry.__all__, name


def test_bpy_stub_tops_up_a_partial_stub() -> None:
    """★ 负控（那条**假绿**路的回归锁）：先塞一个**只有 Panel** 的残桩，仍要加载得起来。

    2026-10-13 真踩（返工一次）：_bpy_stub 第一版是「bpy 已在 sys.modules 就 return」——
    **单跑绿、合跑红**：tests/test_anim_panel.py:36 先装的那个桩只有 Panel，
    于是加载 anim_entry 时 class BamodAnimScan(bpy.types.Operator) 当场 AttributeError。
    「跑了绿」不等于「那条路被覆盖到」—— 这条用例就是那条路的锁。
    """
    saved = sys.modules.get("bpy")
    try:
        sys.modules["bpy"] = types.SimpleNamespace(
            types=types.SimpleNamespace(Panel=type("Panel", (), {})))
        module = _load_anim_entry("partial_stub")  # ⛔ 不走缓存：要的就是**重新加载**那一次
        assert module.BamodAnimWriteback.bl_idname == "bamod.anim_writeback"
        # ⚠ 2026-10-15：`BamodAnimScan` 已随面板重做**搬进** `panels_09_anim.py:533`
        # （`anim_entry` 现在只是 `from . import panels_09_anim as pa`）⇒ 断言走新址，
        # 本用例要守的那件事没变：**残桩也要顶补得起来**。
        assert module.pa.BamodAnimScan.bl_idname == "ba_mod.anim_scan"
    finally:
        if saved is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = saved


