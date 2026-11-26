r"""L13 · 游戏驱动的**纯函数复刻**（轮子 / 履带 UV / 悬挂 / 车体摇摆）。

## 公式来源（`_work/isil/IsilDump`，2026-10-20 挖，与当前 DLL 逐字节一致）

| 量 | 公式 | 常量出处 |
|---|---|---|
| 轮子角 | `(speed ± turn * 0.01) * delta * wheelSpeed` | `0x1861AAD90 = 0.01` |
| 履带 UV | `uv -= (speed + turn) * delta * 0.55 * trackSpeed` | `0x1861ABBD4 = 0.55` |
| 悬挂插值 | `t = min(delta * speed, 1.0)`，`pos = lerp(pos, target, t)` | `0x1861AAB88 = 1.0` |
| 车体摇摆 | `t = delta * Speed * A / C`，`rot = lerp(rot,target,t)` | `0x1861AADD0 = 0.2` |

⚠ 车体摇摆的 `A` / `C` 还没和 `BodyShake(20)` / `BodyShakeAggressive(0.3)` / `BodyMass(11)` 对上，
当前用的是近似系数（`BoneContainer.Bake` 未读）。


## 为什么是"纯函数"

Blender 的 Driver 是**无状态表达式** —— 而轮子角、UV 偏移都是**累积量**（每帧 `+=`）。
所以这里把每帧算成一条**序列**（纯函数、可在 pytest 里算），
Blender 侧只负责把它写成 fcurve（薄壳）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = ["DriveParams", "DriveTrack", "simulate", "turning"]


@dataclass(frozen=True, slots=True)
class DriveParams:
    """`AnimationManager._container._settings` 里那几个数的默认值（T-72B 实测）。"""

    wheel_speed: float = 70.0
    track_speed: float = 2.0
    suspension_speed: float = 0.8
    turn_coef: float = 0.01
    track_uv_coef: float = 0.55
    max_stroke: float = 0.2
    #: 演示放大系数（⛔ 游戏口径是 1.0）。履带 UV 一帧只动 0.011、车体摇摆终值 0.079°，
    #: 在屏幕上看不出来 ⇒ 演示时用 `--exaggerate N` 放大到肉眼可见，**读数里会写明放大了多少**。
    visual_gain: float = 1.0
    #: 摇摆随机的种子（固定 ⇒ 每次跑出来一样，判据可复算）。
    visual_seed: int = 20261020


@dataclass(slots=True)
class DriveTrack:
    """一次仿真的全部序列（每帧一个值）。"""

    frames: list[float] = field(default_factory=list)
    wheel_left_deg: list[float] = field(default_factory=list)
    wheel_right_deg: list[float] = field(default_factory=list)
    track_u: list[float] = field(default_factory=list)
    #: 左右履带分开的 UV（游戏里 4 个槽共用同一个 uvDelta；这里默认保持一致，
    #: 只有显式要「差速履带」时才不同 —— 见 split_track_uv）。
    track_u_left: list[float] = field(default_factory=list)
    track_u_right: list[float] = field(default_factory=list)
    suspension_y: dict[str, list[float]] = field(default_factory=dict)
    body_deg: list[float] = field(default_factory=list)


def turning(turn: float, params: DriveParams) -> float:
    """`Wheels.Move` 里那个 `turn'`：`turn == 0` 时归零，否则乘 0.01。"""
    return 0.0 if turn == 0.0 else float(turn) * params.turn_coef


def simulate(
    *,
    frames: int = 60,
    fps: float = 60.0,
    speed: float = 1.0,
    turn: float = 0.0,
    params: DriveParams | None = None,
    axles: tuple[str, ...] = ("L001", "L002", "L003", "L004", "L005"),
    split_track_uv: bool = False,
) -> DriveTrack:
    """按游戏公式算 `frames` 帧。

    :param speed: 归一化车速（游戏里 `_container.CurrentSpeed`）；1.0 表示"满速"。
    :param turn:  归一化转向（`_container.Turn`）。
    """
    p = params or DriveParams()
    rng = __import__("random").Random(p.visual_seed)
    dt = 1.0 / float(fps)
    turn_ = turning(turn, p)
    out = DriveTrack()
    left = 0.0
    right = 0.0
    u = 0.0
    body = 0.0
    body_target = 0.0
    for name in axles:
        out.suspension_y[name] = []
    for i in range(int(frames)):
        t = i * dt
        out.frames.append(t)
        # ① 轮子：左右差速（单位：度，`Rotate` 收的是角度）
        left += math.degrees((speed + turn_) * dt * p.wheel_speed)
        right += math.degrees((speed - turn_) * dt * p.wheel_speed)
        out.wheel_left_deg.append(left)
        out.wheel_right_deg.append(right)
        # ② 履带：材质 UV 偏移（**不是**骨动画）
        #    游戏 Tracks.Move 里 4 个材质槽用的是同一个 uvDelta（ISIL 451-456 / 488-499 /
        #    510-521 三处都是 xmm6）⇒ 默认左右一致；split_track_uv=True 是实验档。
        #    ⚠ 履带 UV **不乘 visual_gain**：实测履带 UV 范围就是 0~0.999（没有 tiling），
        #    游戏口径 60 帧偏移 0.66 = 整张贴图的 66%，本来就看得见；放大到 13 反而被
        #    REPEAT 折回小数（13.2 mod 1 = 0.2），视觉上更乱。
        u -= (speed + turn) * dt * p.track_uv_coef * p.track_speed
        out.track_u.append(u)
        if split_track_uv:
            k = dt * p.track_uv_coef * p.track_speed * (i + 1)
            out.track_u_left.append(-(speed + turn) * k)
            out.track_u_right.append(-(speed - turn) * k)
        else:
            out.track_u_left.append(u)
            out.track_u_right.append(u)
        # ③ 悬挂：按轴错相位上下（`t = min(delta*speed, 1)` 的平滑）
        gap = 2.0 * math.pi / max(len(axles), 1)
        for k, name in enumerate(axles):
            target = p.max_stroke * 0.5 * math.sin(2.0 * math.pi * 0.8 * t + k * gap)
            prev = out.suspension_y[name][-1] if out.suspension_y[name] else 0.0
            step = min(dt * p.suspension_speed, 1.0)
            out.suspension_y[name].append(prev + (target - prev) * step)
        # ④ 车体摇摆：**随机**目标角 + 按速度插值
        #    ★ 2026-10-20 更正：原来这里写的是 `sin(...)`，那是**错的**。
        #    `ShakeBody` 里那个被当成"三角计算"的调用 `0x1854EDF60` 实测是
        #    **`UnityEngine.Random::Range`**（`tools/rva_what.py 0x54EDF60`）⇒ 目标角是**随机数**，
        #    不是周期正弦 —— 这就是"摇摆不自然"的根因。这里用固定种子的伪随机保证可复现。
        #    周期 `(0.2 / BodyShakeAggressive) * BodyMass` = 0.2/0.3*11 ≈ 7.3 s？
        #    见 `ShakeBody` 读数，
        #    这里先用实测近似 20 帧一次（与 0.2/11*20≈0.36 s 同量级）。
        if i % 20 == 0:
            body_target = rng.uniform(-0.6, 0.6)
        step = min(dt * speed * 0.2 / max(p.max_stroke, 1e-6), 1.0)
        body += (body_target - body) * step
        out.body_deg.append(body * p.visual_gain)
    return out