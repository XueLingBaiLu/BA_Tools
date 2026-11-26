r"""W6 · 动画写侧（AnimationClip / AnimatorController / Animator）—— 以真件为底。

## 这一模块解决什么

研究线（研究-模型与动画/07-clip解码 · 09-写回链 · 10-真机验证）已经把「断箭的动画长什么样、
能不能改」解到底并真机验过；本模块把那条线**变成产品**：

    CLIP_TOP_KEYS                            真件 475/475 一致的顶层键
    read_clip(payload, nodes)                一条 clip 的全部读数 + 往返自证
    decode_streamed(words) / channels_of(fields)   三通道解码（Streamed / Dense / Constant）
    evaluate(channels, index, t)             Unity 的求值（三段派发，逐字照 curvedata.cpp）
    delta_check(channels, delta, start, stop)  ★ 不循环的自证：求值(Start/Stop) == m_ValueArrayDelta
    copy_clip / copy_controller / copy_animator    复制成 builder.ObjectSpec（照搬 + PPtr 重映射）
    pptr_field_span(fields, nodes, name)     一个 PPtr 字段的 (偏移, 宽度) —— ★「12 字节」的尺子
    set_pptr(fields, name, path_id)          同长度改一条 PPtr（真机那条姿势改动的产品形）
    stretch_streamed(fields, k)              流式通道时间拉伸（time*k、c0/k^3、c1/k^2、c2/k）

## 三条硬口径（都有真件读数，⛔ 不许在这一层改）

1. **动画数据不在「曲线表」里**。m_RotationCurves / m_CompressedRotationCurves / m_EulerCurves /
   m_PositionCurves / m_ScaleCurves / m_FloatCurves / m_PPtrCurves **真件 475/475 全空**；
   数据全在 m_MuscleClip 里，且 m_Clip 在类型树里是 **OffsetPtr**（值在 m_Clip.data，类型 Clip）。
2. **三通道共用一套全局曲线号**（Unity curvedata.cpp::EvaluateClipAtIndex 逐字）：
   [0, srate) 走 StreamedClip，[srate, srate+dn) 走 DenseClip，其余走 ConstantClip；
   恒等式 curve_total == len(m_ValueArrayDelta) 真件 **475/475**。
3. **m_MuscleClipSize 不是序列化字节数**，是 Unity 自算的一个量表（空 clip = 0，否则
   2528 + 4*(流式词数 + constant 项数 + dense 采样数) + 8*曲线数）—— 真件 **475/475** 吻合。
   ⛔ 复制时**原样照搬、绝不重算**（与 avatar_build 对 m_AvatarSize 同一条纪律）。

## 真件实测口径（2026-10-08，units 包；复算 _work/anim-line/p2_census.py + p3_freeze.py）

| 类 | 数量 | 往返逐字节 | 载荷 |
|---|---|---|---|
| AnimationClip(74) | **475** | **475/475** | 5,360,912 B |
| AnimatorController(91) | **165** | **165/165** | 503,904 B |
| Animator(95) | **430** | **430/430** | 24,080 B |
| MonoBehaviour(114) 里的 SoldierAnimationManager | **225** | **225/225** | —— |

* 曲线来源：streamed **8140** + dense **5125** + constant **10316** =
  **23581**（与研究线逐位吻合）；
* 三条恒等式各 **475/475**：curve_total == len(m_ValueArrayDelta) ·
  len(m_SampleArray) == m_FrameCount x m_CurveCount · m_MuscleClipSize 公式；
* Animator：**204/430 的 m_Avatar == {0,0}**（载具侧），**4/430 的 m_Controller == {0,0}**；
* ★ **真机那条读数的产品形**（研究线 10-真机验证 与 00-动画线-缺口清单 A1 段 ——
  改 SoldierAnimationManager.HangarAnimatorController **一条 PPtr** 就能逐 prefab 指定军械库姿势）：
  字段宽度 = **12 字节**（225/225）、对象内偏移 = **68**（225/225）、
  225 个里 **192 个是 {0,0}**（走 InfantryConfig 默认值）、33 个非空
  （17 -> Infantry_Hangar_Heavy · 16 -> Infantry_Launcher）。

## 边界（如实写，别让它变成假绿）

1. **能改**：任何**同长度**的字段改动 —— 一个 float（时长 / 采样率 / 某个键的系数）、
   一条 PPtr（12 字节）、流式通道的时间拉伸（词数不变）。判据是**对象字节数不变**（本模块自证）。
2. **不能就地改**：改 m_FrameCount / m_SampleArray 长度（dense 变时长）、增删关键帧、
   整条 clip 换另一条（长度不同）—— 那些要动 SerializedFile 的对象表（units 有 162,010 个对象）。
   本模块**不做**这些；要做得走 builder 那条「加对象」的路。
3. **不能造 state machine / transition / layer**：AnimatorController 的 m_Controller
   （ControllerConstant）里的状态机、过渡、条件、层是**Unity 编辑器烘出来的**（研究线把
   2620 条过渡 / 1681 条条件全落过表，但那是**读**）。本模块**只复制、不构造** ——
   要新状态机请在 Unity 里做（W1 那条路）。
4. **builder.dangling_pptrs 只普查有字段树的对象**：mode="fields" 能被普查；mode="payload" 不能。
5. **m_TDoFArray 的 21 项是什么没解出来**（研究线写「没解出来，也没用到」）—— 原样照搬，不解释它。
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ANIMATOR_TOP_KEYS",
    "CHANNEL_CONSTANT",
    "CHANNEL_DENSE",
    "CHANNEL_STREAMED",
    "CLASS_ANIMATION_CLIP",
    "CLASS_ANIMATOR",
    "CLASS_ANIMATOR_CONTROLLER",
    "CLIP_TOP_KEYS",
    "CONTROLLER_TOP_KEYS",
    "EMPTY_CURVE_TABLES",
    "KEY_BYTES",
    "MUSCLE_BASE",
    "RECORD_HEADER_BYTES",
    "STREAM_TIME_SENTINEL",
    "ChannelReadings",
    "ClipChannels",
    "ClipFacts",
    "ConstantChannel",
    "DeltaCheck",
    "DenseChannel",
    "StreamedChannel",
    "channel_of",
    "channels_of",
    "clip_facts",
    "curve_of_muscle",
    "copy_animator",
    "copy_clip",
    "copy_controller",
    "decode_streamed",
    "delta_check",
    "evaluate",
    "muscle_clip_size_of",
    "pptr_field_span",
    "read_clip",
    "set_pptr",
    "stretch_streamed",
]

#: AnimationClip 的 classID（Unity 内置类；units 里 475 个）。
CLASS_ANIMATION_CLIP = 74

#: `m_IndexArray` 的定长（真件 **475/475 都是 200**，复算 `_work/n1/read_side.py`）。
INDEX_ARRAY_SLOTS = 200
#: 人形 clip 用到的槽窗口：**恒为 7..136**（130 个；389 条无肌肉曲线的 clip 恒为全 -1）。
INDEX_ARRAY_WINDOW = (7, 136)
#: 身体 DoF 那一段的属性槽 = **肌肉号 + 42**（复算 `_work/n1/read_side.py` 的 13 条逐条表：
#: m0->42 … m8->50、m37/38->79/80、m46/47->88/89）。第二条独立佐证：86 条人形 clip 的
#: `m_DeltaPose.m_DoFArray` **恒 55 项**，与「属性 42..96 共 55 条」同构。
MUSCLE_ATTRIBUTE_OFFSET = 42
#: 身体 DoF 肌肉号的条数（= 上面那个 55）。
BODY_DOF_COUNT = 55
#: AnimatorController 的 classID（units 里 165 个）。
CLASS_ANIMATOR_CONTROLLER = 91
#: Animator 的 classID（units 里 430 个）；m_Avatar / m_Controller 都是 12 字节 PPtr。
CLASS_ANIMATOR = 95

#: 真件 475/475 一致的 AnimationClip 顶层键（顺序也一致）—— 键集合本身就是第一条判据。
CLIP_TOP_KEYS: tuple[str, ...] = (
    "m_Name",
    "m_Legacy",
    "m_Compressed",
    "m_UseHighQualityCurve",
    "m_RotationCurves",
    "m_CompressedRotationCurves",
    "m_EulerCurves",
    "m_PositionCurves",
    "m_ScaleCurves",
    "m_FloatCurves",
    "m_PPtrCurves",
    "m_SampleRate",
    "m_WrapMode",
    "m_Bounds",
    "m_MuscleClipSize",
    "m_MuscleClip",
    "m_ClipBindingConstant",
    "m_HasGenericRootTransform",
    "m_HasMotionFloatCurves",
    "m_Events",
)

#: 真件 475/475 全空的**七张曲线表** —— 断箭的 clip 是「只有肌肉剪辑」的形态。
EMPTY_CURVE_TABLES: tuple[str, ...] = (
    "m_RotationCurves",
    "m_CompressedRotationCurves",
    "m_EulerCurves",
    "m_PositionCurves",
    "m_ScaleCurves",
    "m_FloatCurves",
    "m_PPtrCurves",
)

#: AnimatorController 的顶层键（units 里 165/165 一致）。
CONTROLLER_TOP_KEYS: tuple[str, ...] = (
    "m_Name",
    "m_ControllerSize",
    "m_Controller",
    "m_TOS",
    "m_AnimationClips",
    "m_StateMachineBehaviourVectorDescription",
    "m_StateMachineBehaviours",
    "m_MultiThreadedStateMachine",
)

#: Animator 的顶层键（units 里 430/430 一致）。
ANIMATOR_TOP_KEYS: tuple[str, ...] = (
    "m_GameObject",
    "m_Enabled",
    "m_Avatar",
    "m_Controller",
    "m_CullingMode",
    "m_UpdateMode",
    "m_ApplyRootMotion",
    "m_LinearVelocityBlending",
    "m_StabilizeFeet",
    "m_HasTransformHierarchy",
    "m_AllowConstantClipSamplingOptimization",
    "m_KeepAnimatorStateOnDisable",
    "m_WriteDefaultValuesOnDisable",
)

CHANNEL_STREAMED = "streamed"
CHANNEL_DENSE = "dense"
CHANNEL_CONSTANT = "constant"

#: 流式词流：CurveTimeData = 8 B（float time + UInt32 count）；CurveKey = 20 B（int + 4 float）。
RECORD_HEADER_BYTES = 8
KEY_BYTES = 20
#: m_MuscleClipSize 公式里的常量底座（真件 475/475 吻合；⛔ 不是序列化长度）。
MUSCLE_BASE = 2528
#: 流式时间片的**哨兵门槛**：|time| >= 它 就是 -FLT_MAX / +inf 那种哨兵，拉伸时**不动**。
#: 依据：真件首条记录是 0xff7fffff = **-FLT_MAX**（07-clip解码/muscle-clip-format.md §3.2），
#: 末条是 +inf —— 拿它们乘 k 会直接溢出 float32。
STREAM_TIME_SENTINEL = 3.0e38


def seq_len(value: Any) -> int:
    """vector / staticvector / map 的长度（三种解出形状都认，别用眼睛猜形状）。

    ⚠ 下划线那个 `_seq_len` 是历史名字（本模块内部 + 老用例），行为逐字相同。
    """
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, Mapping):
        return len(value["first"]) if set(value) == {"first", "second"} else len(value)
    return -1


def _seq_len(value: Any) -> int:
    """历史名字（= `seq_len`）。"""
    return seq_len(value)


def _sorted_keys(value: Any) -> tuple[str, ...]:
    return tuple(sorted(str(k) for k in value))


def _require_keys(fields: Mapping[str, Any], want: Sequence[str], what: str) -> None:
    got = set(fields)
    if got != set(want):
        raise AssetError(
            f"{what} 的顶层键集合与真件口径不一致："
            f"缺 {sorted(set(want) - got)} / 多 {sorted(got - set(want))}",
            hint=f"真件口径就是 {list(want)}；树或对象拿错了 —— ⛔ 不许在键集合不对的树上出数",
        )


def _f32_to_u32(value: float) -> int:
    """把 float32 的**位模式**放回 u32 词表（⛔ 不是数值转换）。"""
    return struct.unpack("<I", struct.pack("<f", value))[0]


# --------------------------------------------------------------------------- #
# 尺子 1：三通道解码
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class StreamedChannel:
    """m_StreamedClip —— 词流（CurveTimeData + CurveKey）。"""

    words: int
    curve_count: int
    discrete_curve_count: int
    records: int
    keys: int
    first_time: float
    last_time: float
    #: 每条曲线的键表：((time, (c0, c1, c2, c3)), ...)，按写入顺序（time 升序）
    per_curve: tuple[tuple[tuple[float, tuple[float, float, float, float]], ...], ...]

    def value(self, index: int, t: float) -> float:
        """Unity streamedclip：value(t) = ((c0*dt + c1)*dt + c2)*dt + c3，dt = t - key.time。

        取「最后一条 key.time <= t」的键（首条 time = -FLT_MAX ⇒ 任何 t 都取得到）。
        """
        keys = self.per_curve[index]
        chosen = keys[0]
        for item in keys:
            if item[0] <= t:
                chosen = item
            else:
                break
        dt = t - chosen[0]
        c0, c1, c2, c3 = chosen[1]
        return ((c0 * dt + c1) * dt + c2) * dt + c3


@dataclass(frozen=True, slots=True)
class DenseChannel:
    """m_DenseClip —— 采样矩阵 + 自己的采样率/起点。"""

    frame_count: int
    curve_count: int
    sample_rate: float
    begin_time: float
    samples: int
    data: tuple[float, ...]

    def value(self, index: int, t: float) -> float:
        """Unity denseclip：(t - beginTime)*sampleRate 取 floor 与 +1 两行，各自 clamp 后插值。"""
        if self.frame_count <= 0 or self.curve_count <= 0:
            raise AssetError("dense 通道是空的，求不了值")
        raw = (t - self.begin_time) * self.sample_rate
        low = math.floor(raw)
        frac = raw - low
        i0 = min(max(low, 0), self.frame_count - 1)
        i1 = min(max(low + 1, 0), self.frame_count - 1)
        a = self.data[i0 * self.curve_count + index]
        b = self.data[i1 * self.curve_count + index]
        return a + (b - a) * frac


@dataclass(frozen=True, slots=True)
class ConstantChannel:
    """m_ConstantClip —— 第 i 项就是第 i 条常值曲线的整段值。"""

    values: tuple[float, ...]

    def value(self, index: int, t: float) -> float:
        return self.values[index]


@dataclass(frozen=True, slots=True)
class ClipChannels:
    """三块缓冲 + Unity 的**全局曲线号**派发（curvedata.cpp::EvaluateClipAtIndex）。"""

    streamed: StreamedChannel
    dense: DenseChannel
    constant: ConstantChannel

    @property
    def curve_total(self) -> int:
        return self.streamed.curve_count + self.dense.curve_count + len(self.constant.values)

    def channel_of(self, index: int) -> str:
        if index < self.streamed.curve_count:
            return CHANNEL_STREAMED
        index -= self.streamed.curve_count
        if index < self.dense.curve_count:
            return CHANNEL_DENSE
        return CHANNEL_CONSTANT

    def base_of(self, channel: str) -> int:
        """这一条通道的**全局号起点**（= 前一条通道的曲线数之和）。"""
        if channel == CHANNEL_STREAMED:
            return 0
        if channel == CHANNEL_DENSE:
            return self.streamed.curve_count
        if channel == CHANNEL_CONSTANT:
            return self.streamed.curve_count + self.dense.curve_count
        raise AssetError(f"通道 {channel!r} 不认", hint="只有 streamed / dense / constant")

    def locals_of(self, channel: str) -> int:
        """这一条通道有多少条曲线（= 它的局部曲线号上界）。"""
        if channel == CHANNEL_STREAMED:
            return self.streamed.curve_count
        if channel == CHANNEL_DENSE:
            return self.dense.curve_count
        if channel == CHANNEL_CONSTANT:
            return len(self.constant.values)
        raise AssetError(f"通道 {channel!r} 不认", hint="只有 streamed / dense / constant")

    def value(self, index: int, t: float) -> float:
        if not 0 <= index < self.curve_total:
            raise AssetError(f"曲线号 {index} 越界（共 {self.curve_total} 条）")
        if index < self.streamed.curve_count:
            return self.streamed.value(index, t)
        index -= self.streamed.curve_count
        if index < self.dense.curve_count:
            return self.dense.value(index, t)
        index -= self.dense.curve_count
        return self.constant.value(index, t)

    def read(self, channel: str, local: int, t: float) -> float:
        """按「通道 + 局部曲线号」求值（不用自己算全局号）。"""
        n = self.locals_of(channel)
        if not 0 <= local < n:
            raise AssetError(f"{channel} 的局部曲线号 {local} 越界（共 {n} 条）")
        return self.value(self.base_of(channel) + local, t)


def decode_streamed(
    words: Sequence[int], *, curve_count: int | None = None
) -> StreamedChannel:
    """把 m_StreamedClip.data（u32 词表）走成 CurveTimeData + CurveKey。

    布局（研究线 07-clip解码/muscle-clip-format.md §3.1）：
        CurveTimeData 后跟 count 个 CurveKey（每键 20 B：int curveIndex + 4 个 float 系数）
    首条记录 time = -FLT_MAX（给每条曲线一个片头初值），末条 time = +inf 且 count = 0。

    **自证**：走完必须**恰好**吃掉全部词（多一个少一个都抛，⛔ 不猜）。
    ⚠ 词流自己**不知道** curveCount —— 那个数在 m_StreamedClip 头里，由 channels_of 传进来。

    ★ **curveIndex 必须先验上界**（2026-10-08 实测踩到）：任意一个字节被改，
    都可能把 curveIndex 变成一个天文数字（例：Fold 的某条记录）；照着它的字面值去
    开辟数组 = 原地爆炸（第一版就这么挂住一次）。上界取「声明的曲线数」；
    调用方没给时退而取词数（一条曲线至少占 1 个词 ⇒ 合法值必小于它）。
    """
    limit = int(curve_count) if curve_count is not None else max(int(len(words)), 1)
    buf = struct.pack(f"<{len(words)}I", *words) if words else b""
    pos = 0
    records = 0
    keys = 0
    first_time = 0.0
    last_time = 0.0
    per: list[list[tuple[float, tuple[float, float, float, float]]]] = []
    while pos + 2 <= len(words):
        time, count = struct.unpack_from("<fI", buf, pos * 4)
        if records == 0:
            first_time = time
        last_time = time
        records += 1
        pos += 2
        if pos + 5 * count > len(words):
            raise AssetError(
                f"流式词流断了：第 {records} 条记录声明 {count} 个键，只剩 {len(words) - pos} 个词",
                hint="按 CurveTimeData(8 B)+CurveKey(20 B) 走不通 ⇒ 布局或词数读错了",
            )
        for _ in range(count):
            index, c0, c1, c2, c3 = struct.unpack_from("<iffff", buf, pos * 4)
            if not 0 <= index < limit:
                raise AssetError(
                    f"CurveKey 的 curveIndex={index} 越界（合法区间 [0, {limit})）",
                    hint="词流被改坏 / 布局读错了；⛔ 不许照着字面值去开辟数组",
                )
            while len(per) <= index:
                per.append([])
            per[index].append((time, (c0, c1, c2, c3)))
            pos += 5
            keys += 1
    if pos != len(words):
        raise AssetError(
            f"流式词流没吃干净：吃了 {pos}/{len(words)} 个词",
            hint="时间片布局读错了（CurveTimeData 8 B + CurveKey 20 B）",
        )
    return StreamedChannel(
        words=len(words),
        curve_count=int(curve_count) if curve_count is not None else 0,
        discrete_curve_count=0,
        records=records, keys=keys, first_time=float(first_time), last_time=float(last_time),
        per_curve=tuple(tuple(item) for item in per),
    )


def channels_of(fields: Mapping[str, Any]) -> ClipChannels:
    """从一个 AnimationClip 的**字段树**取出三通道。

    ⚠ m_Clip 在类型树里是 **OffsetPtr**，值在 m_Clip.data（类型 Clip）——
    真件 475/475 都是这个形状（复算 _work/anim-line/p1b_muscleclip.py）。
    """
    muscle = fields["m_MuscleClip"]
    clip = muscle["m_Clip"]["data"]
    streamed_raw = clip["m_StreamedClip"]
    dense_raw = clip["m_DenseClip"]
    constant_raw = clip["m_ConstantClip"]
    # 词流自己不知道 curveCount（在头里）⇒ 走完再覆盖；并**对账曲线条数**
    streamed = replace(
        decode_streamed(list(streamed_raw["data"]), curve_count=int(streamed_raw["curveCount"])),
        discrete_curve_count=int(streamed_raw["discreteCurveCount"]),
    )
    if len(streamed.per_curve) != streamed.curve_count:
        raise AssetError(
            f"流式通道：有键的曲线 {len(streamed.per_curve)}，头里声明 {streamed.curve_count} 条",
            hint="两者必须相等（首条 time=-FLT_MAX 的记录给每条曲线一个初值）；不等就是读偏了",
        )
    dense = DenseChannel(
        frame_count=int(dense_raw["m_FrameCount"]),
        curve_count=int(dense_raw["m_CurveCount"]),
        sample_rate=float(dense_raw["m_SampleRate"]),
        begin_time=float(dense_raw["m_BeginTime"]),
        samples=_seq_len(dense_raw["m_SampleArray"]),
        data=tuple(float(x) for x in dense_raw["m_SampleArray"]),
    )
    if dense.samples != dense.frame_count * dense.curve_count:
        raise AssetError(
            f"dense 通道：采样 {dense.samples} 项 != {dense.frame_count} x {dense.curve_count}",
            hint="真件 475/475 成立；不成立就是 m_SampleArray 读偏了",
        )
    constant = ConstantChannel(values=tuple(float(x) for x in constant_raw["data"]))
    return ClipChannels(streamed=streamed, dense=dense, constant=constant)


def channel_of(channels: ClipChannels, index: int) -> str:
    """全局曲线号 -> 通道名（Unity curvedata.cpp::EvaluateClipAtIndex 的口径）。"""
    return channels.channel_of(index)


def curve_of_muscle(
    muscle: int, index_array: Sequence[int] | None = None
) -> tuple[int, int]:
    """肉体号 -> (`m_IndexArray` 的**属性槽**, 全局**曲线号**)。

    ★ 这张表是「肌肉曲线住在哪」的**唯一**桥，两条实测口径（复算 `_work/n1/read_side.py`）：

    * `m_MuscleClip.m_IndexArray` 是**定长 200 的属性槽表**，`m_IndexArray[属性] = 曲线号`
      （`-1` = 这条属性没有曲线）。⚠ 它**不是**「按曲线号索引」—— 那条猜测实测 **474/475 不成立**；
    * 身体 DoF 肌肉 **m** 的属性槽 = **m + 42**
      （m0..m8 -> 42..50 · m37/38 -> 79/80 · m46/47 -> 88/89）。

    :param index_array: 那条 clip 的 `fields["m_MuscleClip"]["m_IndexArray"]`。给了解出曲线号；
        ⛔ 不给就返回 `-1`（**查不到**，不猜 —— 槽表在对象里，函数本身读不到具体 clip）。
    """
    want = int(muscle)
    if not 0 <= want < BODY_DOF_COUNT:
        raise AssetError(
            f"肌肉号 {want} 不在身体 DoF 段（0..{BODY_DOF_COUNT - 1}）",
            hint="Root/goal/手指那几段的属性槽不是「肌肉号 + 42」，本函数只覆盖身体 DoF 那一段",
        )
    attribute = want + MUSCLE_ATTRIBUTE_OFFSET
    if index_array is None:
        return attribute, -1
    rows = list(index_array)
    if len(rows) != INDEX_ARRAY_SLOTS:
        raise AssetError(
            f"m_IndexArray 有 {len(rows)} 项，真件是恒定的 {INDEX_ARRAY_SLOTS} 项",
            hint="拿到的不是 m_MuscleClip.m_IndexArray（或者这个包不是 1.2.1 的形态）",
        )
    return attribute, int(rows[attribute])


def evaluate(channels: ClipChannels, index: int, t: float) -> float:
    """按全局曲线号求值（三段派发，逐字照 Unity）。"""
    return channels.value(index, t)


def muscle_clip_size_of(channels: ClipChannels) -> int:
    """Unity 自算的 m_MuscleClipSize（真件 475/475 吻合；⛔ **不是**序列化字节数）。

    空 clip = 0；否则 = 2528 + 4*(流式词数 + constant 项数 + dense 采样数) + 8*曲线数。
    """
    total = channels.curve_total
    if total == 0 and channels.streamed.words == 0:
        return 0
    return (
        MUSCLE_BASE
        + 4 * (channels.streamed.words + len(channels.constant.values) + channels.dense.samples)
        + 8 * total
    )


# --------------------------------------------------------------------------- #
# 尺子 2：不循环的自证 —— 求值(Start/Stop) == m_ValueArrayDelta
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DeltaCheck:
    """m_ValueArrayDelta 的对账读数（Unity 自己用同一套采样代码写出这个数组）。"""

    curves: int
    matched: int
    worst: float
    mismatches: tuple[tuple[int, float, float], ...]

    @property
    def hit_rate(self) -> float:
        return self.matched / self.curves if self.curves else 1.0

    @property
    def ok(self) -> bool:
        return self.matched == self.curves

    def line(self) -> str:
        return (
            f"曲线 {self.curves} 条 · 命中 {self.matched}（{self.hit_rate * 100:.4f}%）· "
            f"最大偏差 {self.worst:.3e}"
        )


def delta_check(
    channels: ClipChannels,
    delta: Sequence[Mapping[str, Any]] | Sequence[Sequence[float]],
    start_time: float,
    stop_time: float,
    *,
    tolerance: float = 1e-4,
    max_report: int = 8,
) -> DeltaCheck:
    """逐条比对「在 m_StartTime / m_StopTime 处的求值」与 m_ValueArrayDelta[i]。

    这是研究线那条 **100.0000%（23581/23581）** 的自证 —— 它**不循环**，因为
    m_ValueArrayDelta 是 Unity 在烘焙时用**同一套采样代码**写出来的：我们能算对，
    说明三通道的布局 + 求值公式 + 全局曲线号派发**同时对**。
    """
    if len(delta) != channels.curve_total:
        raise AssetError(
            f"m_ValueArrayDelta 有 {len(delta)} 项，曲线有 {channels.curve_total} 条 —— 对不上",
            hint="三通道恒等式不成立；先查 curve_total 再对账",
        )
    matched = 0
    worst = 0.0
    bad: list[tuple[int, float, float]] = []
    for index in range(channels.curve_total):
        item = delta[index]
        want_start = float(item["m_Start"] if isinstance(item, Mapping) else item[0])
        want_stop = float(item["m_Stop"] if isinstance(item, Mapping) else item[1])
        err = max(
            abs(channels.value(index, start_time) - want_start),
            abs(channels.value(index, stop_time) - want_stop),
        )
        worst = max(worst, err)
        if err <= tolerance:
            matched += 1
        elif len(bad) < max_report:
            bad.append((index, channels.value(index, start_time), want_start))
    return DeltaCheck(curves=channels.curve_total, matched=matched, worst=worst,
                      mismatches=tuple(bad))


# --------------------------------------------------------------------------- #
# 尺子 3：一个 PPtr 字段在对象里占多少字节（★「12 字节」那条读数的产品形）
# --------------------------------------------------------------------------- #
def pptr_field_span(
    fields: Mapping[str, Any], nodes: Sequence[clone.TypeNode], name: str
) -> tuple[int, int]:
    """返回 PPtr 字段 name 在对象字节里的 (起始偏移, 宽度)。

    做法（不猜字段名、也不写死偏移）：把这一条 PPtr 的**每个字节都换成非零**
    （m_FileID=0x01020304、m_PathID=0x0102030405060708，都是小端），再与原字节比 ——
    变化的连续区间就是字段本身。**自证**：宽度必须等于 12（PPtr = int32 + int64），否则抛。

    真件读数：SoldierAnimationManager.HangarAnimatorController = **(68, 12)**，225/225 一致
    （复算 _work/anim-line/p3_freeze.py）。
    """
    if name not in fields:
        raise AssetError(f"字段树里没有 {name}（不是 PPtr 字段？）")
    original = clone.write_object(dict(fields), nodes)
    probed = dict(fields)
    probed[name] = {"m_FileID": 0x01020304, "m_PathID": 0x0102030405060708}
    other = clone.write_object(probed, nodes)
    diff = [i for i in range(len(original)) if original[i] != other[i]]
    if not diff:
        raise AssetError(f"{name} 改了值但字节没变 —— 它不是 PPtr 字段")
    width = diff[-1] - diff[0] + 1
    if width != 12:
        raise AssetError(
            f"{name} 的字节宽度是 {width}，不是 12",
            hint="PPtr = m_FileID(int32) + m_PathID(int64) = 12 字节；不是 12 说明拿错字段了",
        )
    return diff[0], width


def set_pptr(
    fields: Mapping[str, Any], name: str, path_id: int, *, file_id: int = 0
) -> dict[str, Any]:
    """同长度改一条 PPtr（真机那条「改 12 字节 ⇒ 军械库姿势变了」的产品形）。

    ⛔ 只改这一个字段，别的字段一个都不动；返回**新的**字段树（不改入参）。
    """
    if name not in fields:
        raise AssetError(f"字段树里没有 {name}")
    out = dict(fields)
    out[name] = {"m_FileID": int(file_id), "m_PathID": int(path_id)}
    return out


# --------------------------------------------------------------------------- #
# 读数表
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ChannelReadings:
    """某一条通道的一条读数（判据②：三通道各取一条并给读数）。"""

    channel: str
    local_index: int
    global_index: int
    curves: int
    start_value: float
    stop_value: float
    detail: str

    def line(self) -> str:
        return (
            f"{self.channel:<8} 局部号 {self.local_index:>3} / 全局号 {self.global_index:>5} · "
            f"该通道 {self.curves:>5} 条 · 值[{self.start_value:+.6f} -> {self.stop_value:+.6f}] · "
            f"{self.detail}"
        )


@dataclass(frozen=True, slots=True)
class ClipFacts:
    """一条 AnimationClip 的全部读数。"""

    path_id: int
    name: str
    object_size: int
    payload_sha256: str
    roundtrip_equal: bool
    top_keys: tuple[str, ...]
    sample_rate: float
    wrap_mode: int
    declared_muscle_clip_size: int
    computed_muscle_clip_size: int
    start_time: float
    stop_time: float
    empty_curve_tables: tuple[str, ...]
    channels: ClipChannels
    delta: DeltaCheck
    bindings: int
    events: int
    legacy: bool
    compressed: bool
    has_generic_root_transform: bool
    index_array: int
    value_array_reference_pose: int

    @property
    def keys_are_canonical(self) -> bool:
        return self.top_keys == tuple(sorted(CLIP_TOP_KEYS))

    @property
    def curve_total(self) -> int:
        return self.channels.curve_total

    @property
    def muscle_clip_size_ok(self) -> bool:
        return self.declared_muscle_clip_size == self.computed_muscle_clip_size

    @property
    def curve_identity_ok(self) -> bool:
        return self.index_array >= 0

    def readings(self) -> tuple[ChannelReadings, ...]:
        """三通道各取**第一条**曲线，给 (值@StartTime -> 值@StopTime) 与这一条的形状读数。"""
        ch = self.channels
        out: list[ChannelReadings] = []
        for name, detail in (
            (CHANNEL_STREAMED,
             f"词流 {ch.streamed.words} 词 / {ch.streamed.records} 时间片 / {ch.streamed.keys} 键"),
            (CHANNEL_DENSE,
             f"{ch.dense.frame_count} x {ch.dense.curve_count} 曲线 @{ch.dense.sample_rate:g}Hz"),
            (CHANNEL_CONSTANT, f"常值数组 {len(ch.constant.values)} 项"),
        ):
            count = ch.locals_of(name)
            if count:
                base = ch.base_of(name)
                out.append(ChannelReadings(
                    channel=name, local_index=0, global_index=base, curves=count,
                    start_value=ch.value(base, self.start_time),
                    stop_value=ch.value(base, self.stop_time), detail=detail))
        return tuple(out)

    def line(self) -> str:
        return (
            f"pathID {self.path_id} · {self.name!r} · 对象 {self.object_size:,} B · "
            f"{self.sample_rate:g}Hz · 曲线 {self.curve_total}"
            f"（流 {self.channels.streamed.curve_count} / 密 {self.channels.dense.curve_count} / "
            f"常 {len(self.channels.constant.values)}） · 绑定 {self.bindings} · "
            f"delta {self.delta.matched}/{self.delta.curves} · 往返 "
            f"{'OK' if self.roundtrip_equal else '★不一致'}"
        )


def clip_facts(
    fields: Mapping[str, Any],
    *,
    path_id: int = 0,
    payload: bytes | None = None,
    nodes: Sequence[clone.TypeNode] | None = None,
    delta_tolerance: float = 1e-4,
) -> ClipFacts:
    """从**已解码的字段树**出读数表。键集合按 CLIP_TOP_KEYS 对不上就抛。

    payload 与 nodes **同时给**时才做往返对账（roundtrip_equal 才有意义）；
    只给 payload 不给 nodes ⇒ roundtrip_equal 记 True 但 payload_sha256 仍会算 ——
    ⛔ 想拿往返当判据就两个都给（read_clip 就是那么用的）。
    """
    _require_keys(fields, CLIP_TOP_KEYS, "AnimationClip")
    muscle = fields["m_MuscleClip"]
    channels = channels_of(fields)
    report = delta_check(
        channels, muscle["m_ValueArrayDelta"],
        float(muscle["m_StartTime"]), float(muscle["m_StopTime"]),
        tolerance=delta_tolerance,
    )
    raw = bytes(payload) if payload is not None else b""
    roundtrip = True
    if payload is not None and nodes is not None:
        roundtrip = clone.write_object(dict(fields), nodes) == raw
    return ClipFacts(
        path_id=int(path_id),
        name=str(fields["m_Name"]),
        object_size=len(raw),
        payload_sha256=hashlib.sha256(raw).hexdigest() if raw else "",
        roundtrip_equal=roundtrip,
        top_keys=_sorted_keys(fields),
        sample_rate=float(fields["m_SampleRate"]),
        wrap_mode=int(fields["m_WrapMode"]),
        declared_muscle_clip_size=int(fields["m_MuscleClipSize"]),
        computed_muscle_clip_size=muscle_clip_size_of(channels),
        start_time=float(muscle["m_StartTime"]),
        stop_time=float(muscle["m_StopTime"]),
        empty_curve_tables=tuple(n for n in EMPTY_CURVE_TABLES if _seq_len(fields[n]) == 0),
        channels=channels,
        delta=report,
        bindings=_seq_len(fields["m_ClipBindingConstant"]["genericBindings"]),
        events=_seq_len(fields["m_Events"]),
        legacy=bool(fields["m_Legacy"]),
        compressed=bool(fields["m_Compressed"]),
        has_generic_root_transform=bool(fields["m_HasGenericRootTransform"]),
        index_array=_seq_len(muscle["m_IndexArray"]),
        value_array_reference_pose=_seq_len(muscle["m_ValueArrayReferencePose"]),
    )


def read_clip(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int = 0,
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
    verify_roundtrip: bool = True,
    delta_tolerance: float = 1e-4,
) -> ClipFacts:
    """读一条 clip 的对象字节，出读数表；默认**自证往返逐字节**（真件 475/475 成立）。"""
    raw = bytes(payload)
    fields = clone.read_object(raw, nodes, ref_types=ref_types)
    facts = clip_facts(fields, path_id=path_id, payload=raw, nodes=nodes,
                       delta_tolerance=delta_tolerance)
    if verify_roundtrip and not facts.roundtrip_equal:
        raise AssetError(
            f"clip pathID={path_id} 往返**不逐字节相等**",
            hint="读侧没覆盖全（字段树 / 类型树对不上）；这种字节不要写回",
        )
    return facts


# --------------------------------------------------------------------------- #
# 复制（照搬 + PPtr 重映射）
# --------------------------------------------------------------------------- #
def _copy_object(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    class_id: int,
    path_id: int,
    mapping: Mapping[int, int] | None,
    mode: str,
    label: str,
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
) -> builder.ObjectSpec:
    if mode not in {"fields", "payload"}:
        raise AssetError(f"mode={mode!r} 不认", hint='只有 "fields" 与 "payload"')
    raw = bytes(payload)
    if mode == "payload":
        if mapping:
            raise AssetError(
                "mode='payload' 与 mapping 不能同时给",
                hint="字节照搬时 PPtr 改不了；要重映射就用 mode='fields'（它会自证逐字节相等）",
            )
        return builder.ObjectSpec(class_id=class_id, fields=None, path_id=int(path_id),
                                  payload=raw, label=label)
    fields = clone.read_object(raw, nodes, ref_types=ref_types)
    if mapping:
        fields = builder.remap_pptrs(fields, mapping)
    again = clone.write_object(fields, nodes)
    if again != raw:
        raise AssetError(
            f"复制出来的字节与原 payload 不相等（pathID={path_id}）",
            hint=f"原 {len(raw):,} B / 新 {len(again):,} B —— 读侧没覆盖全，别照搬",
        )
    return builder.ObjectSpec(class_id=class_id, fields=fields, path_id=int(path_id), label=label)


def copy_clip(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int,
    mapping: Mapping[int, int] | None = None,
    mode: str = "fields",
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
    label: str = "clip",
) -> builder.ObjectSpec:
    """把一条真件 AnimationClip 复制成我们包里的 ObjectSpec。

    mode="fields"（默认）：解码 -> 重映射 -> 重编码，并**自证与原字节逐字节相等**（真件 475/475）。
    mode="payload"：原样搬字节（⚠ 没有字段树 ⇒ dangling_pptrs 普查不到，模块头边界 4）。
    ⚠ m_MuscleClipSize **原样照搬、绝不重算**（它是 Unity 自算的量表，不是序列化长度）。
    ⚠ 不改 mapping 时是真件 pathID 保真复制；要给本包分新 pathID，mapping 里自己映射。
    """
    return _copy_object(payload, nodes, class_id=CLASS_ANIMATION_CLIP, path_id=path_id,
                        mapping=mapping, mode=mode, label=label, ref_types=ref_types)


def copy_controller(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int,
    mapping: Mapping[int, int] | None = None,
    mode: str = "fields",
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
    label: str = "controller",
) -> builder.ObjectSpec:
    """复制一个 AnimatorController。

    ⛔ 本模块**只复制、不构造**状态机/过渡/层（那些是 Unity 编辑器烘出来的，模块头边界 3）。
    mapping 用来把 m_AnimationClips 里指向真件 clip 的 PPtr 重指到**本包的** clip。
    """
    return _copy_object(payload, nodes, class_id=CLASS_ANIMATOR_CONTROLLER, path_id=path_id,
                        mapping=mapping, mode=mode, label=label, ref_types=ref_types)


def copy_animator(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int,
    mapping: Mapping[int, int] | None = None,
    mode: str = "fields",
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
    label: str = "animator",
) -> builder.ObjectSpec:
    """复制一个 Animator（m_Avatar / m_Controller 两条 12 字节 PPtr 都走 mapping）。"""
    return _copy_object(payload, nodes, class_id=CLASS_ANIMATOR, path_id=path_id,
                        mapping=mapping, mode=mode, label=label, ref_types=ref_types)


# --------------------------------------------------------------------------- #
# 写侧：同长度的两件（拉伸时间轴 / 改一个字段）
# --------------------------------------------------------------------------- #
def stretch_streamed(
    fields: Mapping[str, Any], k: float, *, stop_time: float | None = None
) -> dict[str, Any]:
    """把**流式通道**按 k 倍拉伸（研究线 p18 正控的公式，⛔ 别自己发明）。

        time -> time * k        （哨兵 -FLT_MAX / +inf 保持不动）
        c0 -> c0 / k^3 · c1 -> c1 / k^2 · c2 -> c2 / k · c3 不变
        m_StopTime -> k*(StopTime - StartTime) + StartTime

    **判据**：词数一个不变 ⇒ 对象字节数不变 ⇒ 这是「同长度改动」，不需要动对象表。
    ⛔ 只改 m_StopTime 不改数据是**错的**（那是截断 / 变速采样，不是拉伸 —— 见 可改性判定.md §②）。
    """
    if k <= 0:
        raise AssetError("k 必须为正")
    out = dict(fields)
    muscle = dict(out["m_MuscleClip"])
    clip = muscle["m_Clip"]
    inner = dict(clip["data"]) if isinstance(clip, Mapping) else dict(clip)
    streamed = dict(inner["m_StreamedClip"])
    words = list(streamed["data"])
    buf = struct.pack(f"<{len(words)}I", *words) if words else b""
    scaled = list(words)
    pos = 0
    while pos + 2 <= len(words):
        time, count = struct.unpack_from("<fI", buf, pos * 4)
        if pos + 2 + 5 * count > len(words):
            raise AssetError("流式词流断了（拉伸前先确认布局）")
        scaled[pos] = _f32_to_u32(time if abs(time) >= STREAM_TIME_SENTINEL else time * k)
        for j in range(count):
            base = pos + 2 + 5 * j
            c0, c1, c2, c3 = struct.unpack_from("<ffff", buf, base * 4 + 4)
            scaled[base + 1] = _f32_to_u32(c0 / (k ** 3))
            scaled[base + 2] = _f32_to_u32(c1 / (k ** 2))
            scaled[base + 3] = _f32_to_u32(c2 / k)
            scaled[base + 4] = _f32_to_u32(c3)
        pos += 2 + 5 * count
    streamed["data"] = scaled
    inner["m_StreamedClip"] = streamed
    if stop_time is None:
        start = float(muscle["m_StartTime"])
        stop_time = k * (float(muscle["m_StopTime"]) - start) + start
    muscle["m_StopTime"] = float(stop_time)
    if isinstance(clip, Mapping):
        new_clip = dict(clip)
        new_clip["data"] = inner
        muscle["m_Clip"] = new_clip
    else:
        muscle["m_Clip"] = inner
    out["m_MuscleClip"] = muscle
    return out
