r"""D 线 · **真 Blender**（无头）端到端判据：规格 -> 真 Action -> **视口里真的会动**。

**判据一行**：`pytest tests/test_anim_blender.py -q -n 0` -> 通过数 **0 -> N**。

## 为什么这条必须进用例（而不是只留在 _work 输出里）

本功能的**唯一**防回归锁就在这里：

* `anim_entry.py` 碰 bpy，**只能**在 Blender 里跑（产品 venv 里连 import 都做不到）；
* Blender 5.2 的 Action API **换过**（`Action.fcurves` 在 5.2.1 上不存在，走
  `layers[*].strips[*].channelbag(slot).fcurves`）—— 这一层一旦漂移，面板会静默不出动画；
* 「能播」不是"挂上了"，是**顶点真的动**：本用例逐帧求值，量帧 0 与帧 60 的最大顶点位移。

## 怎么跑（判别式与 _work/blender-test/*.py 那批先例同一个姿势）

```
& "D:\Blender\blender.exe" --background --factory-startup \
    --python _work\animline\blender\anim_probe.py        # 落 Action 四项读数 + 方向正控
& "D:\Blender\blender.exe" --background --factory-startup \
    --python _work\animline\blender\anim_play_probe.py   # 顶点真的动（帧 0 vs 帧 60）
```

本用例**跑这两个脚本并断言行**（Blender 不在 -> skip，⛔ 不是"绿"）。

## 夹具

`_work/anim-line/fixtures/`（475 条 clip 的原始字节）＋ `_work/w10_ref/units_meta.bin`（类型树）
＋ 研究线的 `_nodepaths.json` / `_nodeindex.json`。**缺一 -> skip**（⛔ 不静默跳过断言）。

## 未做（如实）

* **不 import 真实 units 包**（3.38 GB）：本用例走冻结夹具，与 tests/test_build_anim.py 同一姿势；
* **不写真机截图**：本用例量的是"顶点动不动"，不是"看起来对不对"（后者要人看，AGENTS §0.12.1）。
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

_REPO = Path(__file__).resolve().parents[1]
#: 本机 Blender 5.2.1（kb/38 的复算命令就是它；环境变量可覆盖）。
BLENDER = Path(os.environ.get("BAMOD_BLENDER", r"D:\Blender\blender.exe"))
PROBE_DIR = WORK / "animline" / "blender"
FIXTURES = WORK / "anim-line" / "fixtures"
UNITS_META = WORK / "w10_ref" / "units_meta.bin"
PATH_TABLE = _REPO.parent / "研究-模型与动画" / "_nodepaths.json"
NODEINDEX = _REPO.parent / "研究-模型与动画" / "_nodeindex.json"


def _prereqs() -> None:
    if not BLENDER.is_file():
        pytest.skip(f"本机没有 Blender：{BLENDER}（设 BAMOD_BLENDER 指过去）")
    for path in (FIXTURES / "clips_index.json", FIXTURES / "clips_payloads.bin",
                 UNITS_META, PATH_TABLE, NODEINDEX):
        if not path.exists():
            pytest.skip(f"缺 {path}（夹具 / 研究线产物）")


def _run_probe(name: str) -> dict[str, str]:
    """跑一个无头探针，把 `KEY value` 那些行收成 dict（同名的取第一条）。"""
    _prereqs()
    # 退役（2026-10-15 用户定）：**如实 skip ＋ 点名为什么**，⛔ 不硬凑一条假绿。
    # 题目待移植：插件重做（panels_* 归并）之后，探针 `anim_probe.py` 仍写着
    #   `from blender_addon import anim_bridge, anim_entry, panels_anim`
    # 而 `panels_anim.py` 已改名 `panels_09_anim.py`，且它用的 `panels_anim.BamodAnimPanel`
    # 在插件里**已经不存在**（只剩 `_work/gui_look_*` 旧快照里）⇒ Blender 退出码 1、ImportError。
    # 同族：`tests/test_anim_panel.py` 早就自己标了
    # 「旧模块 panels_anim.py 已在插件重做时删除 —— 待移植」。
    # ⛔ 这一条不是把用例糊绿 —— 是承认「待移植」；
    # **探针一旦不再引用那个死模块，这个 skip 自动消失**。
    stale = PROBE_DIR / "anim_probe.py"
    if stale.is_file() and "panels_anim" in stale.read_text(encoding="utf-8", errors="replace"):
        pytest.skip(
            "题目待移植：anim_probe.py 仍 import 已被删除的 panels_anim（BamodAnimPanel 也不在了）"
        )
    script = PROBE_DIR / name
    if not script.exists():
        pytest.skip(f"缺探针 {script}（它在 _work/animline/blender/ 下）")
    exe = shutil.which(str(BLENDER)) or str(BLENDER)
    proc = subprocess.run(
        [exe, "--background", "--factory-startup", "--python", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"{name} 退出码 {proc.returncode}\n{text[-3000:]}"
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)(?:\s+(.*))?$", line.strip())
        if match:
            key = match.group(1)
            value = (match.group(2) or "").strip()
            # ⚠ 同名多行的（例 OP_OK ×4）用 " | " 接起来 —— 只留第一行会让
            #   「4 个算子都注册上了」这条判据**恒真**（第一版就是这么骗过自己的）。
            out[key] = f"{out[key]} | {value}" if key in out else value
    assert "PROBE_RC" in out, f"{name} 没走到尽头\n{text[-3000:]}"
    return out


@pytest.fixture(scope="module")
def action_probe() -> dict[str, str]:
    return _run_probe("anim_probe.py")


@pytest.fixture(scope="module")
def play_probe() -> dict[str, str]:
    return _run_probe("anim_play_probe.py")


# --------------------------------------------------------------------------- #
# A · 落 Action（anim_probe.py）
# --------------------------------------------------------------------------- #
def test_action_probe_reaches_the_end(action_probe) -> None:
    """探针跑完且 rc=0（⛔ 不是"跑了"就算）。"""
    assert action_probe["PROBE_RC"] == "0"


def test_action_has_four_curves_one_bone_and_the_documented_range(action_probe) -> None:
    """★ 判据一行：曲线 **4** · 骨 **hatch** · 帧区间 **0..60** · 关键帧 **4×61**。

    （与 docs/研究-动画-三量.md §5③ 那张表逐字段相等 —— 表里 4 条曲线 / 1.0000 s / 60 Hz。）
    """
    text = action_probe["ACTION"]
    assert "\"fcurves\": \"4\"" in text, text
    assert "rotation_quaternion" in text and "hatch" in text, text
    assert "\"frame_range\": \"(0.0, 60.0)\"" in text, text
    assert "\"keys\": \"244\"" in text, text


def test_play_probe_reports_the_action_readings(play_probe) -> None:
    """第二支探针同样落到 Action 四项（两处独立跑，读数必须一致）。"""
    assert play_probe["PROBE_RC"] == "0"
    text = play_probe["ACTION"]
    assert "\"fcurves\": \"4\"" in text and "\"frame_range\": \"(0.0, 60.0)\"" in text, text


def test_quaternion_is_normalized_before_writing(action_probe) -> None:
    """★ 写进 Action 的四元数模长必须是 1（归一化后偏差 < 1e-6）。

    原始曲线的偏差是 5.866e-02（量在 `anim_action.AnimSpec.max_quat_norm_delta` 里），
    归一化的读数是 3.544e-08 —— 两个数都在，⛔ 不许用"归一化"掩盖原始偏差。
    """
    delta = float(action_probe["QUAT_NORM_MAX_DELTA"])
    assert delta < 1e-6, delta


def test_direction_control_of_the_axis_conversion(action_probe) -> None:
    """★ 方向正控：绕 Unity 的 z 轴 +90° -> Blender 里绕 **-z 轴** +90°。

    Unity (x,y,z) 经基变换映到 (x,-z,y)：x->x · y->-z · z->y ⇒ 转轴也跟着走
    （绕 z 转的，在 Blender 里是绕 -z 转）。这条是本功能唯一"方向对不对"的锁：
    改错一个符号，舱盖会朝反方向开。

    ⚠ 探针打的是**单位轴 × sin(θ/2)**（即 `(x,y,z)/|q|`，θ=90° 时 = 0.7071），
    不是单位轴的三个分量 —— 所以这里比的是 -0.7071 而不是 -1；且探针按 4 位小数打印，
    容差取 6e-4（否则量的是四舍五入，不是换算）。
    """
    text = action_probe["DIR_CONTROL"]
    match = re.search(r"angle=([0-9.]+) axis=\(([-0-9.]+), ([-0-9.]+), ([-0-9.]+)\)", text)
    assert match is not None, text
    angle = float(match.group(1))
    axis = [float(match.group(i)) for i in (2, 3, 4)]
    assert angle == pytest.approx(90.0, abs=1e-6)
    assert axis[0] == pytest.approx(0.0, abs=6e-4)
    assert axis[1] == pytest.approx(0.0, abs=6e-4)
    assert axis[2] == pytest.approx(-math.sin(math.radians(45.0)), abs=6e-4), axis


def test_negative_control_missing_bone_is_refused(action_probe) -> None:
    """★ 负控：骨架里没有 hatch -> 探针报的是 **抛**（⛔ 不许静默建一条指向空气的曲线）。"""
    text = action_probe["NEGCTL_BONE"]
    assert "抛了" in text, text
    assert "没抛" not in text, text


def test_negative_control_broken_curve_count_is_refused(action_probe) -> None:
    """★ 负控：把规格里一条四元数曲线删掉 -> 回读/建曲线必须抛。"""
    text = action_probe["NEGCTL_CURVES"]
    assert "抛了" in text and "没抛" not in text, text


# --------------------------------------------------------------------------- #
# B · ★ 能播（anim_play_probe.py）—— 顶点真的动
# --------------------------------------------------------------------------- #
def test_vertices_actually_move_when_the_action_plays(play_probe) -> None:
    """★ 「能播」的机械判据：挂上 Action 后逐帧求值，帧 0 与帧 60 的顶点位移 > 1e-3。

    真读数（本机 2026-10-13）：0 / 0.289934 / 0.955002 / 1.564783 / **1.798692** m
    （帧 0/15/30/45/60）—— 那条 clip 是舱盖转 180°，1 m 臂长转半圈约 2 m，量级吻合。
    """
    assert play_probe["PLAY_OK"] == "True"
    moved = dict(re.findall(r"(\d+): ([0-9.]+)", play_probe["MOVED"]))
    assert set(moved) == {"0", "15", "30", "45", "60"}, moved
    assert float(moved["0"]) == pytest.approx(0.0, abs=1e-9)
    assert float(moved["60"]) > 1e-3, moved


def test_movement_is_monotone_over_the_five_sampled_frames(play_probe) -> None:
    """负控的孪生：位移必须**单调增**（不是噪声/抖一下）。

    180° 那一段的角速度不是常数，但**累积位移**在这 5 个采样点上必须一直增 ——
    如果哪一帧反而变小，说明曲线或帧号接错了。
    """
    moved = dict(re.findall(r"(\d+): ([0-9.]+)", play_probe["MOVED"]))
    seq = [float(moved[k]) for k in ("0", "15", "30", "45", "60")]
    assert all(b >= a for a, b in zip(seq, seq[1:], strict=False)), seq


def test_negative_control_play_probe_refuses_a_wrong_rig(play_probe) -> None:
    """★ 负控：同一个规格挂到一个只有 body 的骨架上 -> **抛**（不是"挂上去但不动"）。"""
    text = play_probe["NEGCTL_BONE"]
    assert "抛了" in text and "没抛" not in text, text

# --------------------------------------------------------------------------- #
# C · ★ 4 个算子（Lead 裁决 3①：放 anim_entry.py；idname 固定）
# --------------------------------------------------------------------------- #
def test_four_operators_register_in_real_blender(action_probe) -> None:
    """★ 真 Blender 里 4 个算子**都能注册**（一个一个注册、注销，不是一个都不试）。"""
    assert action_probe.get("OP_COUNT") == "4", action_probe.get("OP_COUNT")


def test_operator_idnames_are_the_four_the_panel_calls(action_probe) -> None:
    """面板 `row.operator("bamod.anim_*")` 那 4 个 idname —— 逐条钉住。

    （面板那边有一条源码尺子量「调用的算子都在 ANIM_OPERATORS 里」；
    这条量的是**另一端**：那几个 idname 真的在 Blender 里注册上了。）
    """
    seen = set(re.findall(r"idname=(bamod\.anim_\w+)", action_probe.get("OP_OK", "")))
    assert seen == {"bamod.anim_scan", "bamod.anim_build",
                    "bamod.anim_apply", "bamod.anim_clear"}, seen
    labels = re.findall(r"label=(.+?)(?: \||$)", action_probe.get("OP_OK", ""))
    assert len(labels) == 4, labels
