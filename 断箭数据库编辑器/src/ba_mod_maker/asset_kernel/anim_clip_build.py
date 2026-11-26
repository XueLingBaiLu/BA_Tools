r"""L3 路 A · **造一条我们自己写的 AnimationClip**（纯数据；⛔ 不碰 Blender 的 Python API、
不写盘）。

## 要什么

游戏里 T-72B 那条 `T72B hatch` 的**形状**已经全部解开（1 个 Transform 旋转绑定 ⇒ 4 条曲线、
68 个流式词、`-FLT_MAX` 片 + 两片 + 终止片）。本模块把「**值** + **时刻**」写成同形的字段树，
并给一个**能红的往返自证**。

## 判据一行

```
pytest tests/test_anim_clip_build.py -q -n 0
  -> 全绿；把任一个词翻一位 ⇒ test_sentinel_/test_roundtrip_ 那条**当场红**
```

## 编码口径（逐字照读侧 `anim_build`，⛔ 不另立一套）

* 取值：`value(t) = ((c0*dt + c1)*dt + c2)*dt + c3`，`dt = t - key.time`（**绝对秒**，不是归一化）；
* 选键：**最后一条** `key.time <= t`；首片 `time = -FLT_MAX`（给每条曲线一个初值）；
  末片 `time = +inf, count = 0`；
* 词布局：片头 `(t f32, count i32)` **2 词** + 每键 `(curveIndex i32, c0..c3 四个 f32)` **5 词**；
* 本模块只做**片内线性**（`c3` = 该片的值、`c2` = 斜率、`c0 = c1 = 0`），末片**保持**
  ⇒ 片时刻上取值与计划**精确相等**（不是近似）。三次那半（`c0/c1`）**未验**，最小件不需要。

## ★ 两条实测事实（都踩过，写在最前面）

1. **`m_MuscleClipSize` 是「算出来」的，不是「照搬」的** ——
   `2528 + 4*(流式词数 + constant 项数 + dense 采样数) + 8*曲线数`
   （`anim_build.muscle_clip_size_of`，真件 475/475 吻合）。
   实测：hatch 原件 68 词 / 4 曲线 ⇒ **2832**；新造的同形件也算出 **2832**，与树里那格逐位相同。
   ⛔ 「照搬」只对**复制**成立；**新造**必须按新词数算。真机若不播，**第一嫌疑就是这一格**。
2. **`-FLT_MAX` 那片只在 `t < 第一个真片时刻` 时被选中** ——
   `t >= 0` 时选中的是 `t=0` 那一片。⇒ 拿它当负控时，翻**它**里的词在 `t>=0` 上**测不出来**
   （我第一版就这么假绿过一次）。负控必须翻**被扫到的那一片**。

## 边界（⛔ 别读成万能）

* 只支持 **streamed** 通道（本作单位 clip 的主流）；dense / constant 那两条没做；
* 只支持**片内线性**；`m_ValueArrayDelta` 的同步走 `anim_write` 那条现成实现（⛔ 本模块重写一份）；
* **不落包**：本模块只出字段树 / 字节；落包走 `object add` 那条现成入口。

## ⚠ 一处**既有实现**的坑（本模块绕开它，⛔ 没有改它）

`anim_build.copy_controller(payload, nodes, mapping=…)` 的 docstring 说 mapping 用来把 PPtr 重指，
可它转调的 `_copy_object` 紧接着自证 `again == raw` —— **重指必然不等 ⇒ 当场抛**。
实测（`_work/l3/probe_remap.py`）：空 mapping OK；`mapping={hatch: 新}` 抛
`AssetError: 复制出来的字节与原 payload 不相等`。
⇒ 要重指就用 :func:`remap_copy`（它把自证改成「**写出去再读回来 == 重指后的树**」，那才是该证的）。
"""

from __future__ import annotations

import copy
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone

__all__ = [
    "END_TIME",
    "SENTINEL_TIME",
    "Slice",
    "apply_streamed",
    "clip_size",
    "expected_value",
    "f32_to_u32",
    "plan_linear_slices",
    "remap_copy",
    "words_of_slices",
]

#: `-FLT_MAX`：真件每个 clip 的首片时刻（给每条曲线一个初值）。
SENTINEL_TIME = -3.4028234663852886e+38
#: `+inf`：末片（`count = 0`，终止）。
END_TIME = float("inf")


def f32_to_u32(value: float) -> int:
    """一个 f32 -> 它的 u32 位型（词流里就是这么存的）。"""
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


@dataclass(frozen=True, slots=True)
class Slice:
    """一片：片时刻 + 这片带的键（`(曲线号, (c0, c1, c2, c3))`）。"""

    time: float
    keys: tuple[tuple[int, tuple[float, float, float, float]], ...]


def plan_linear_slices(
    times: Sequence[float],
    values: Sequence[Sequence[float]],
) -> tuple[Slice, ...]:
    """片时刻 + 每片各曲线的值 -> 片表（片内线性、末片保持；含 sentinel 片与终止片）。

    :param times: 片时刻（升序，长度 K >= 2）；
    :param values: `values[曲线号][片号]`（每条曲线 K 个值）。
    """
    count = len(values)
    if count == 0:
        raise ValueError("一条曲线都没有")
    if len(times) != len(values[0]) or len(times) < 2:
        raise ValueError("times 与每条曲线的值个数要相同，且至少两片")
    head = tuple((i, (0.0, 0.0, 0.0, float(values[i][0]))) for i in range(count))
    slices: list[Slice] = [Slice(SENTINEL_TIME, head)]
    for k in range(len(times) - 1):
        t0, t1 = float(times[k]), float(times[k + 1])
        keys = []
        for i in range(count):
            v0, v1 = float(values[i][k]), float(values[i][k + 1])
            slope = (v1 - v0) / (t1 - t0) if t1 > t0 else 0.0
            keys.append((i, (0.0, 0.0, slope, v0)))
        slices.append(Slice(t0, tuple(keys)))
    tail = tuple((i, (0.0, 0.0, 0.0, float(values[i][-1]))) for i in range(count))
    slices.append(Slice(float(times[-1]), tail))
    slices.append(Slice(END_TIME, ()))
    return tuple(slices)


def words_of_slices(slices: Sequence[Slice]) -> list[int]:
    """片表 -> `m_StreamedClip.data` 那串 u32 词。"""
    out: list[int] = []
    for item in slices:
        out.append(f32_to_u32(item.time))
        out.append(len(item.keys) & 0xFFFFFFFF)
        for index, coeffs in item.keys:
            out.append(int(index) & 0xFFFFFFFF)
            out.extend(f32_to_u32(c) for c in coeffs)
    return out


def clip_size(words: int, constant: int, dense: int, curves: int) -> int:
    """`m_MuscleClipSize` —— 与 `anim_build.muscle_clip_size_of` **同一个公式**。

    那条要一个 `ClipChannels`；这条要裸计数（编码时还没有 channels）。
    `tests/test_anim_clip_build.py` 有一条用例拿真件把两者**逐位对拍**。
    """
    return 2528 + 4 * (int(words) + int(constant) + int(dense)) + 8 * int(curves)


def apply_streamed(
    template: Mapping[str, Any],
    *,
    times: Sequence[float],
    values: Sequence[Sequence[float]],
    name: str | None = None,
) -> dict[str, Any]:
    """一份模板字段树（例 `T72B hatch`）-> **换了曲线值**的新树（deepcopy，⛔ 不改原树）。

    顺带把这几格一起写对（它们与词数**必须**自洽）：

    * `m_StreamedClip.{data, curveCount, discreteCurveCount}`；
    * `m_MuscleClipSize`（**算出来**，见模块头事实 1）；
    * `m_MuscleClip.{m_StartTime, m_StopTime}`；
    * `m_DenseClip.{m_FrameCount, m_BeginTime}`
      （真件口径：时长×采样率 + 2；hatch 62 = 1.0×60 + 2 ✔）。
    """
    slices = plan_linear_slices(times, values)
    words = words_of_slices(slices)
    out = copy.deepcopy(dict(template))
    data = out["m_MuscleClip"]["m_Clip"]["data"]
    streamed = data["m_StreamedClip"]
    streamed["data"] = words
    streamed["curveCount"] = len(values)
    streamed["discreteCurveCount"] = 0
    dense = data["m_DenseClip"]
    constant = data["m_ConstantClip"]
    out["m_MuscleClipSize"] = clip_size(
        len(words), len(constant["data"]), len(dense["m_SampleArray"]), len(values)
    )
    out["m_MuscleClip"]["m_StartTime"] = float(times[0])
    out["m_MuscleClip"]["m_StopTime"] = float(times[-1])
    sample_rate = float(out.get("m_SampleRate") or 60.0)
    dense["m_FrameCount"] = int(round((float(times[-1]) - float(times[0])) * sample_rate)) + 2
    dense["m_BeginTime"] = float(times[0])
    if name is not None:
        out["m_Name"] = str(name)
    return out


def expected_value(
    times: Sequence[float], values: Sequence[Sequence[float]], curve: int, t: float
) -> float:
    """**参照实现**（片内线性、末片保持）—— 与编码器同一条式子，但**独立写一遍**（对拍用）。"""
    ts = [float(x) for x in times]
    vs = [float(x) for x in values[curve]]
    for k in range(len(ts) - 1, -1, -1):
        if t >= ts[k]:
            if k == len(ts) - 1:
                return vs[-1]
            span = ts[k + 1] - ts[k]
            if span <= 0:
                return vs[k]
            return vs[k] + (vs[k + 1] - vs[k]) * (t - ts[k]) / span
    return vs[0]


def remap_copy(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    class_id: int,
    path_id: int,
    mapping: Mapping[int, int],
    label: str = "",
) -> builder.ObjectSpec:
    """复制一个对象并**重指它的 PPtr**（`copy_controller(mapping=…)` 那条路抛，见模块头）。

    自证改成**该证的那一条**：写出去的字节再读回来，必须与「重指后的树」**逐字段相同**。
    （`_copy_object` 证的是 `again == raw` —— mapping 一改就不成立，所以它当场抛。）
    """
    fields = clone.read_object(bytes(payload), nodes)
    remapped = builder.remap_pptrs(fields, dict(mapping)) if mapping else fields
    written = clone.write_object(remapped, nodes)
    back = clone.read_object(written, nodes)
    if dict(back) != dict(remapped):
        raise ValueError(
            f"重指后的树写出去再读回来对不上（pathID={path_id}）—— 读侧没覆盖全，别照搬"
        )
    return builder.ObjectSpec(
        class_id=int(class_id), fields=remapped, path_id=int(path_id), label=label
    )

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   docs/研究-动画-三量.md（动画三量：曲线 / 绑定 / 求值的量法与读数）
# --------------------------------------------------------------------------- #
