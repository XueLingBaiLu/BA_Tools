r"""L13 · 游戏驱动公式的纯函数复刻（轮 / 履带 UV / 悬挂 / 车体摇摆）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_anim_drive_sim.py -q -n 0`
→ 5 passed（含两条负控：左右轮**必须不等**、UV **必须朝负方向**）。

公式出处见 `asset_kernel/anim_drive_sim.py` 文件头与 STATUS 2026-10-20 08:1x。
"""
from __future__ import annotations

import math

from ba_mod_maker.asset_kernel.anim_drive_sim import DriveParams, simulate, turning


def test_frame_count_and_time_axis() -> None:
    d = simulate(frames=30, fps=60.0)
    assert len(d.frames) == 30
    assert len(d.wheel_left_deg) == 30
    assert abs(d.frames[1] - 1.0 / 60.0) < 1e-9


def test_wheels_turn_by_formula() -> None:
    """轮角 = (speed ± turn*0.01) * delta * wheelSpeed 的**累积**。"""
    p = DriveParams()
    d = simulate(frames=60, fps=60.0, speed=1.0, turn=0.0, params=p)
    want = math.degrees(1.0 * 1.0 * p.wheel_speed)      # 60 帧 × delta = 1 秒
    assert abs(d.wheel_left_deg[-1] - want) < 1e-6


def test_left_and_right_differ_when_turning() -> None:
    """负控：转向时左右轮**必须不等**（差速）。"""
    d = simulate(frames=60, speed=1.0, turn=1.0)
    assert abs(d.wheel_left_deg[-1] - d.wheel_right_deg[-1]) > 1.0


def test_track_uv_moves_negative() -> None:
    """负控：履带 UV 必须往**负**方向累积（游戏里是 `U -= ...`）。"""
    d = simulate(frames=60, speed=1.0, turn=0.0)
    assert d.track_u[-1] < 0.0


def test_turning_zero_is_zero() -> None:
    assert turning(0.0, DriveParams()) == 0.0
    assert turning(2.0, DriveParams()) == 2.0 * 0.01