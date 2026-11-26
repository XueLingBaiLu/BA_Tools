r"""D 线 · 写回第一刀：**改时长**（流式时间轴整段拉伸）＋ 反向 Δ 还原。

## 这一模块解决什么

研究线 `07-clip解码/可改性判定.md` §② 与 `docs/研究-动画-三量.md` §5③ 已经把
「改时长要动哪几处」钉死（带正控：k=2.5 拉伸后逐点复核最大偏差 8.4e-08、词数 210→210）。
本模块把它变成产品的四件事：

    stretch_clip(fields, k)          整段拉伸（返回新字段树 + 读数）
    delta_recompute(channels, ...)   重算 m_ValueArrayDelta（★ 必须同步，否则自证掉下来）
    inverse_record(before)           记下「反向 Δ」（只存被改对象的原字节）
    restore(after, record)           用反向 Δ 还原 -> **sha256 必须等于改前**（J6 口径）

## ★「改时长」到底动哪几处（⛔ 一条都不能漏）

| 动哪个 | 公式 | 依据 |
|---|---|---|
| 每个 `CurveTimeData.time` | `time * k`（哨兵 -FLT_MAX / +inf **不动**） | 可改性判定 §② |
| 每个键的 `coeff[0..2]` | `c0/k³, c1/k², c2/k`（`c3` 是值，不动） | 同上（正控 8.4e-08） |
| `m_StopTime` | `k*(Stop-Start) + Start` | 同上 |
| `m_ValueArrayDelta` | **按新曲线重新求值**（Start/Stop 两点） | 可改性判定 §①第 1 条 |
| ⛔ `m_FrameCount` | **不要动** | 它是 dense 头的残留位 |
| ⛔ 增删关键帧 | 不做 | 那要动对象表（§③） |

**判据**：词数一个不变 ⇒ **对象字节数一个不变**（本模块逐次自证）⇒ 这是「同长度改动」。
只改 `m_StopTime` 不改数据是**错的**（那是截断/变速采样，不是拉伸）。

## 三条如实记的边界

1. **`m_StartTime` 真件 475/475 都是 0**（本次复算：`probe_start.py`），所以 `time*k` 与
   通用式 `Start + k*(time-Start)` 在真件上**等价**；`stretch_streamed` 用的是 `time*k`
   （研究线 p18 的公式）。⛔ 若哪天出现 `Start != 0` 的真件，这一条要重新复核（当前无读数）。
2. **dense 曲线的时长动不了**（就地）：`m_FrameCount × m_SampleRate` 决定它的采样窗口，
   改它 = 改数组长度 = 改对象长度。Unity 的 `DenseClip::Evaluate` 里那句
   `time = StartTime + frac*(StopTime-StartTime)` **只作用在 motion 曲线（全局号 0..6）**上；
   普通曲线拿的是**绝对 t**。⇒ 本模块把 `m_DenseClip` **原样不动**，并且**逐条判**
   （`dense_stretch_ok`）dense 区有没有被普通曲线消费 —— T-72B `T72B hatch` 走这条路是
   合法的（dense 曲线数 = 0），**这不是"没检查"**。
3. **只改一条 clip 的一个对象**：`stretch_clip` 只动字段树，一个字节都不写；
   落包那一档是 :func:`write_clip` —— 它把**等长**新字节交给 writer 的反向 Δ 链
   （catalog 的 `m_Crc` / `m_BundleSize` 由 writer 成对更新，与 `mesh edit` 同一条链），
   **对象表不动**（等长 ⇒ 没有新增 / 删除对象，见可改性判定 §③）。

## 复算

    .venv\Scripts\python.exe _work\animline\probe_stretch.py    # 真件逐条拉伸 → 还原 → sha256
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import anim_action, anim_build, clone, reverse_delta, writer
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "DURATION_RTOL",
    "MOTION_CURVES",
    "ClipShape",
    "DenseStretch",
    "StretchReport",
    "WritebackCheck",
    "check_writeback",
    "clip_sha256",
    "delta_recompute",
    "dense_stretch_ok",
    "inverse_record",
    "other_clips_unchanged",
    "restore",
    "shape_of",
    "stretch_clip",
    "stretchable",
    "write_clip",
]

#: Unity 的 motion 曲线（RootT 3 条 + RootQ 4 条）—— `m_DenseClip` 的
#: `StartTime + frac*(StopTime-StartTime)` 归一化**只作用在这 7 条**上（clipmuscle.cpp 口径）。
MOTION_CURVES = 7


def clip_sha256(payload: bytes) -> str:
    """一条 clip 对象字节的 sha256（J6 口径里的「整文件」在小夹具上就是它）。"""
    return hashlib.sha256(bytes(payload)).hexdigest()


def _f32(value: float) -> float:
    """过一遍 float32（真件里曲线值全是 f32；比较前统一口径）。"""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


@dataclass(frozen=True, slots=True)
class DenseStretch:
    """dense 区能不能跟着拉伸的判定（**逐条判过**，不是假设）。"""

    curve_count: int
    frame_count: int
    samples: int
    #: dense 曲线里**被普通曲线消费**的条数（全局号既不是 motion、也不是肌肉的那些）。
    non_motion: int
    #: dense 曲线里**被 motion 曲线消费**的条数（全局号 0..6）。
    #: ⛔ 这类也不能"什么都不动"：m_StopTime 一改，它的播放速度就变了（变速采样，不是拉伸）。
    motion: int
    #: dense 曲线里**被任意绑定消费**的条数（= 真正的拦路虎）。
    consumed: int

    @property
    def ok(self) -> bool:
        return self.consumed == 0


@dataclass(frozen=True, slots=True)
class StretchReport:
    """一次拉伸的读数（全部可复算）。"""

    name: str
    k: float
    old_stop: float
    new_stop: float
    start: float
    words: int
    old_size: int
    new_size: int
    streamed_keys: int
    dense: DenseStretch
    #: 重算前后 `m_ValueArrayDelta` 的最大偏差（**「必须同步」那条尺子**）。
    delta_max_change: float
    delta_hits: int
    delta_total: int
    notes: tuple[str, ...] = field(default=())

    @property
    def size_unchanged(self) -> bool:
        return self.old_size == self.new_size

    @property
    def delta_ok(self) -> bool:
        return self.delta_hits == self.delta_total

    def lines(self) -> tuple[str, ...]:
        size_note = ("（**没变** ⇒ 同长度改动）" if self.size_unchanged
                     else " ⇒ ⚠ **变了**，这不是同长度改动")
        dense_note = ("dense 区不被任何曲线消费 ⇒ 拉伸合法"
                      if self.dense.ok else "⛔ **dense 曲线被消费 ⇒ 就地拉伸不成立**（要重采样）")
        dense_line = (f"    dense 区：曲线 {self.dense.curve_count} 条 / "
                      f"帧 {self.dense.frame_count} / 采样 {self.dense.samples} 项 · "
                      f"被消费 {self.dense.consumed} 条"
                      f"（motion {self.dense.motion} / 普通 {self.dense.non_motion}）"
                      f" ⇒ {dense_note}")
        return (
            f"拉伸 {self.name} k={self.k:g}：时长 {self.old_stop - self.start:.4f} -> "
            f"{self.new_stop - self.start:.4f} s",
            f"    流式词 {self.words} 个 · 键 {self.streamed_keys} 个 · "
            f"对象字节 {self.old_size:,} -> {self.new_size:,}{size_note}",
            f"    m_ValueArrayDelta 重算：最大偏差 {self.delta_max_change:.3e} · "
            f"自证 {self.delta_hits}/{self.delta_total}",
            dense_line,
            *tuple("    " + n for n in self.notes),
        )


def dense_stretch_ok(fields: Mapping[str, Any]) -> DenseStretch:
    """dense 区能不能就地拉伸：看它的曲线里有没有**被普通曲线消费**的。"""
    muscle = fields["m_MuscleClip"]
    clip = muscle["m_Clip"]["data"]
    dense = clip["m_DenseClip"]
    n_dense = int(dense["m_CurveCount"])
    samples = anim_build.seq_len(dense["m_SampleArray"])
    index_array = [int(v) for v in muscle["m_IndexArray"]]
    bindings = list(fields["m_ClipBindingConstant"]["genericBindings"])
    curve_total = max(anim_build.seq_len(muscle["m_ValueArrayDelta"]), n_dense, 1)
    rows = anim_action.expand_curves(bindings, index_array, curve_total)
    streamed = int(clip["m_StreamedClip"]["curveCount"])
    non_motion = motion = consumed = 0
    for local in range(n_dense):
        gi = streamed + local
        # ⚠ 三通道恒等式之外（gi 越过 curve_total）的 dense 曲线：**不可能是 motion**
        #   （motion 恒在全局号 0..6），但也不该被当成"没人用它"⇒ 归到 non_motion 里 fail-closed。
        if gi >= len(rows) or rows[gi][0] is None:
            consumed += 1
            non_motion += 1
            continue
        consumed += 1
        if anim_action.is_muscle_binding(bindings[rows[gi][0]]):  # type: ignore[index]
            continue
        if gi < MOTION_CURVES:
            motion += 1
        else:
            non_motion += 1
    return DenseStretch(curve_count=n_dense, frame_count=int(dense["m_FrameCount"]),
                        samples=samples, non_motion=non_motion, motion=motion,
                        consumed=consumed)


def delta_recompute(
    channels: anim_build.ClipChannels, start: float, stop: float
) -> tuple[tuple[float, float], ...]:
    """按**当前曲线**重算 `m_ValueArrayDelta`（每条曲线在 Start/Stop 两点的值）。

    ⛔ 这是「只改 clip 数据不改 delta」那条错法的解药：`可改性判定.md` §①第 1 条写得清楚 ——
    `m_ValueArrayDelta` 是 Unity 烘焙时算好的快照，改完数据不同步它，
    走 delta 那一路的语义就自相矛盾。
    """
    out: list[tuple[float, float]] = []
    for i in range(channels.curve_total):
        out.append((_f32(channels.value(i, start)), _f32(channels.value(i, stop))))
    return tuple(out)


def _delta_rows(muscle: Mapping[str, Any]) -> list[dict[str, float]]:
    return [{"m_Start": float(r["m_Start"]), "m_Stop": float(r["m_Stop"])}
            for r in muscle["m_ValueArrayDelta"]]


def _sync_delta(after: dict[str, Any], start: float, stop: float) -> tuple[float, int, int]:
    """把 `m_ValueArrayDelta` 按新曲线同步掉，返回 (最大偏差, 命中数, 总数)。"""
    channels = anim_build.channels_of(after)
    want = delta_recompute(channels, start, stop)
    rows = _delta_rows(after["m_MuscleClip"])
    if len(rows) != len(want):
        raise AssetError(f"曲线数 {len(want)} 与 m_ValueArrayDelta 的 {len(rows)} 条对不上")
    changed = 0.0
    for row, (a, b) in zip(rows, want, strict=True):
        changed = max(changed, abs(float(row["m_Start"]) - a), abs(float(row["m_Stop"]) - b))
        row["m_Start"], row["m_Stop"] = a, b
    muscle = dict(after["m_MuscleClip"])
    muscle["m_ValueArrayDelta"] = rows
    after["m_MuscleClip"] = muscle
    check = anim_build.delta_check(channels, rows, start, stop, tolerance=1e-4)
    return changed, check.matched, check.curves


def stretchable(fields: Mapping[str, Any]) -> tuple[bool, str]:
    """这条 clip 能不能**就地**改时长（同长度改动）？回 `(能不能, 为什么)`。

    ⛔ 判据只有一条：**dense 区不被任何绑定消费**。
    （dense 的窗口由 `m_FrameCount × m_SampleRate` 决定，改不了；只改 `m_StopTime`
    会让它变成变速采样 —— 那是错法，不是拉伸。）
    真件读数（`_work/animline/probe_stretch_cls.py`）：**265 / 475** 可以。
    """
    dense = dense_stretch_ok(fields)
    if dense.ok:
        return True, (f"dense 区 {dense.curve_count} 条曲线一个绑定都没消费 ⇒ 就地拉伸合法"
                      if dense.curve_count else "没有 dense 区 ⇒ 就地拉伸合法")
    return False, (f"dense 区有 {dense.consumed} 条曲线被绑定消费"
                   f"（motion {dense.motion} / 普通 {dense.non_motion}）⇒ 就地拉伸不成立")


def stretch_clip(
    fields: Mapping[str, Any],
    k: float,
    *,
    payload: bytes | None = None,
    nodes: Sequence[clone.TypeNode] | None = None,
    sync_delta: bool = True,
) -> tuple[dict[str, Any], StretchReport]:
    """把一条 clip 的**流式时间轴**整段拉伸 k 倍（同长度改动）。

    :param payload: 原对象字节；给出来才能报老/新字节数与「同长度」判据。
    :param sync_delta: 是否同步重算 `m_ValueArrayDelta`（**默认必须开**；
        关掉只为了做负控 —— 那条尺子必须能红）。
    :raises AssetError: k 非正 / dense 区被普通曲线消费 / 曲线数与 delta 表不等。
    """
    if not k > 0:
        raise AssetError(f"拉伸系数 k={k!r} 必须为正")
    before = dict(fields)
    muscle_before = before["m_MuscleClip"]
    start = float(muscle_before["m_StartTime"])
    old_stop = float(muscle_before["m_StopTime"])
    dense = dense_stretch_ok(before)
    if not dense.ok:
        raise AssetError(
            f"这条 clip 的 dense 区有 {dense.consumed} 条曲线被绑定消费"
            f"（motion {dense.motion} / 普通 {dense.non_motion}）—— "
            "dense 的时长由 m_FrameCount × m_SampleRate 决定，就地拉伸不成立；"
            "只改 m_StopTime 会让它变成**变速采样**（不是拉伸）",
            hint="要动它就得改 m_FrameCount / m_SampleArray 的长度 = 改对象长度"
                 "（可改性判定 §③：要动 SerializedFile 的对象表）",
        )
    new_stop = k * (old_stop - start) + start
    after = dict(anim_build.stretch_streamed(before, k, stop_time=new_stop))

    notes: list[str] = []
    if sync_delta:
        changed, hits, total = _sync_delta(after, start, new_stop)
    else:
        changed, hits, total = 0.0, 0, anim_build.channels_of(after).curve_total
        notes.append("⚠ sync_delta=False（**负控口径**）：m_ValueArrayDelta 没跟着改")

    raw_new = b""
    if payload is not None and nodes is not None:
        raw_new = clone.write_object(after, nodes)
        if clone.write_object(clone.read_object(raw_new, nodes), nodes) != raw_new:
            raise AssetError("拉伸后的字段树往返不自洽（读回来再写出去就不一样了）")

    channels = anim_build.channels_of(after)
    report = StretchReport(
        name=str(after["m_Name"]), k=float(k), old_stop=old_stop, new_stop=new_stop, start=start,
        words=channels.streamed.words,
        old_size=len(payload) if payload is not None else 0,
        new_size=len(raw_new),
        streamed_keys=sum(len(c) for c in channels.streamed.per_curve),
        dense=dense, delta_max_change=changed, delta_hits=hits, delta_total=total,
        notes=tuple(notes),
    )
    return after, report


def inverse_record(before: bytes) -> dict[str, Any]:
    """反向 Δ：**只存被改对象的原始字节**（AGENTS §1 口径 —— ⛔ 不拷整包当备份）。"""
    raw = bytes(before)
    return {"kind": "object-bytes", "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": raw}


def restore(after: bytes, record: Mapping[str, Any]) -> bytes:
    """用反向 Δ 还原 —— **sha256 必须等于改前**（J6 口径）。

    :raises AssetError: 记录的字节数与 `after` 不一致（拿错记录 / 对象被换过）。
    """
    raw = bytes(after)
    original = bytes(record["bytes"])
    if int(record.get("size", -1)) != len(original):
        raise AssetError("反向 Δ 记录自己就不自洽（size 与字节数不等）")
    if len(original) != len(raw):
        raise AssetError(
            f"反向 Δ 还原不了：改后 {len(raw):,} B，记录里是 {len(original):,} B"
            "（长度都不同 ⇒ 这不是「同长度改动」那一类还原）",
            hint="变长改动要走对象表那条路（可改性判定 §③），⛔ 不是这里",
        )
    if hashlib.sha256(original).hexdigest() != str(record["sha256"]):
        raise AssetError("反向 Δ 记录里的字节与它自己的 sha256 对不上（记录被改过）")
    return original


# --------------------------------------------------------------------------- #
# 落包（anim apply）：把改后的**等长**对象字节写回包 —— 反向 Δ，⛔ 不拷整包
# --------------------------------------------------------------------------- #
#: 时长比较的**相对**容差：写进去的值过一遍 float32，读回来必然带一点舍入。
DURATION_RTOL = 1e-5

#: 对象字节里的 typeName（写回计划那一行印的就是它）。
CLIP_TYPE_NAME = "AnimationClip"


@dataclass(frozen=True, slots=True)
class ClipShape:
    """一条 clip 的**形状读数**：曲线数 / 流式词数 / 键数 / 时长 / 对象字节数。

    写后复核的两个输入（**预期** 与 **读回**）都是它 —— 比的就是这几个数
    （「曲线数 / 时长 / 词数与预期一致」那条判据的落点）。
    """

    name: str
    payload_bytes: int
    curves: int
    words: int
    keys: int
    duration: float

    def same_shape_as(self, other: ClipShape) -> tuple[bool, tuple[str, ...]]:
        """与另一份比形状 —— 回 (一不一样, 不一样的项)（⛔ 不静默当通过）。"""
        bad: list[str] = []
        if self.curves != other.curves:
            bad.append(f"曲线数 {self.curves} != {other.curves}")
        if self.words != other.words:
            bad.append(f"词数 {self.words} != {other.words}")
        if self.keys != other.keys:
            bad.append(f"键数 {self.keys} != {other.keys}")
        if abs(self.duration - other.duration) > DURATION_RTOL * max(1.0, abs(other.duration)):
            bad.append(f"时长 {self.duration:.6f} != {other.duration:.6f}")
        return (not bad), tuple(bad)

    def line(self) -> str:
        return (f"{self.name}：曲线 {self.curves} 条 · 词 {self.words} 个 · 键 {self.keys} 个 · "
                f"时长 {self.duration:.4f} s · 对象 {self.payload_bytes:,} B")


def shape_of(
    fields: Mapping[str, Any], payload: bytes | None = None, *, name: str = ""
) -> ClipShape:
    """从字段树（可选对象字节）取形状读数 —— 落包前后**同一把**尺子。"""
    channels = anim_build.channels_of(fields)
    muscle = fields["m_MuscleClip"]
    return ClipShape(
        name=str(name or fields.get("m_Name") or ""),
        payload_bytes=len(payload) if payload is not None else 0,
        curves=int(channels.curve_total),
        words=int(channels.streamed.words),
        keys=int(sum(len(c) for c in channels.streamed.per_curve)),
        duration=float(muscle["m_StopTime"]) - float(muscle["m_StartTime"]),
    )


@dataclass(frozen=True, slots=True)
class WritebackCheck:
    """写后复核：包里**读回来**的那条 clip 与「打算写进去的那一份」逐项对上。

    四条：① 读回的对象字节 == 打算写进去的字节（逐字节）；② 曲线数与预期相等；
    ③ 词数与预期相等；④ 时长与预期相等（相对容差 DURATION_RTOL）。
    另外报两条**读数**（不算失败）：

    * target_changed —— 读回与**源**逐字节不同才叫真改了（真件里有一类 clip 拉伸后
      逐字节不变：m_StopTime = 0.0 ⇒ 乘 k 还是 0；真读数 **31 / 331**）；
    * same_length —— 读回字节数 == 源字节数（等长改动；变长要动对象表，这条就不成立）。
    """

    path_id: int
    source: ClipShape
    expected: ClipShape
    readback: ClipShape
    bytes_match: bool
    target_changed: bool
    shape_bad: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def same_length(self) -> bool:
        return self.readback.payload_bytes == self.source.payload_bytes

    @property
    def ok(self) -> bool:
        return bool(self.bytes_match and not self.shape_bad and self.same_length)

    def lines(self) -> tuple[str, ...]:
        mark = "✅" if self.ok else "❌"
        return (
            f"{mark} 写后复核 pathID={self.path_id}：读回 {self.readback.payload_bytes:,} B · "
            f"与打算写进去的那份逐字节"
            f"{'相同' if self.bytes_match else '**不同**'} · "
            f"目标{'确实变了' if self.target_changed else '**没变**'}",
            f"    源  {self.source.line()}",
            f"    预期{self.expected.line()}",
            f"    读回{self.readback.line()}",
            *tuple("    ★ " + item for item in self.shape_bad),
            *tuple("    " + item for item in self.notes),
        )


def check_writeback(
    *,
    path_id: int,
    source_shape: ClipShape,
    source_raw: bytes,
    expected_shape: ClipShape,
    expected_raw: bytes,
    readback_shape: ClipShape,
    readback_raw: bytes,
) -> WritebackCheck:
    """写后复核那把尺子（**纯函数**：喂已知的错样本必须报红 —— 见用例正控）。"""
    _same, bad = readback_shape.same_shape_as(expected_shape)
    changed = bytes(readback_raw) != bytes(source_raw)
    return WritebackCheck(
        path_id=int(path_id),
        source=source_shape,
        expected=expected_shape,
        readback=readback_shape,
        bytes_match=bytes(readback_raw) == bytes(expected_raw),
        target_changed=changed,
        shape_bad=bad,
        notes=() if changed else (
            "读回与**源**逐字节相同：这条 clip 没有可改的时间轴（不是失败，但要报）",
        ),
    )


def other_clips_unchanged(
    before: Mapping[int, bytes], after: Mapping[int, bytes], path_id: int
) -> tuple[bool, int, tuple[int, ...]]:
    """**除目标外**，包里其它 clip 的对象字节逐字节未变 —— 回 (一样?, 比了几条, 变了的)。

    ⚠ 这一把只覆盖 **AnimationClip**（同一把读法读两遍）；**整包**级别的证明在 writer 的
    H3 那一条（「落在期望改动之外的字节数」必须 **0**，含所有 classID 的对象）。
    """
    target = int(path_id)
    ids = sorted((set(before) | set(after)) - {target})
    bad = tuple(i for i in ids if before.get(i) != after.get(i))
    return (not bad), len(ids), bad


def write_clip(
    bundle: str | Path,
    path_id: int,
    payload: bytes,
    *,
    name: str = "",
    dry_run: bool = True,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    allow_running_game: bool = False,
    patch_label: str = "",
) -> writer.WriteReport:
    """把一条 clip 的**等长**新对象字节写回包（干跑 / 真写只差 dry_run 一处）。

    ⚠ **永远带 patch_store**：不给它就落进 writer 里 patch_store is None 那条**拷整包**的
    旧链（用户实测 +3.4 GB / 73.6 s）—— 本函数没有关掉它的开关，⛔ 别在外面另开一条。
    ⚠ 只走 inplace（等长）：变长要动对象表（可改性判定 §③），不在这一档 ——
    调用方要在**进来之前**把长度量出来（CLI 那一层就在量）。
    """
    blob = bytes(payload)
    change = writer.DeltaChange(
        inplace=(writer.ObjectChange(
            path_id=int(path_id), payload=blob, name=str(name), type_name=CLIP_TYPE_NAME,
        ),)
    )
    root = None if project_root is None else Path(project_root)
    return writer.write_delta(
        bundle,
        change,
        dry_run=dry_run,
        project_root=root,
        game_root=game_root,
        catalog_path=catalog_path,
        allow_running_game=allow_running_game,
        patch_store=reverse_delta.PatchStore(
            reverse_delta.patch_store_root(root), game_root=game_root,
        ),
        patch_label=patch_label or f"anim apply {Path(bundle).name}#{int(path_id)}",
    )
