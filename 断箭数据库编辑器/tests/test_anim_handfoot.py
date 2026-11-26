"""手/脚两根骨的**真实结构**（task-10 的结论落成用例；推导与全部读数见 _work/handline/REPORT.md）。

结构（从 27 点 3 通道网格反解、再在 3 个真 clip 上做场外验证）：

    肢端（LeftHand / RightHand / LeftFoot / RightFoot）：
        M = Rot(a0, θ0) ∘ tansum(a1, a2)        q = M ∘ q_base
    手臂（LeftUpperArm / RightUpperArm）——**不动**：
        M = tansum(a0, a1) ∘ Rot(a2, θ2)        q = M ∘ q_base
    θi = vi>0 ? vi·max_i : vi·|min_i|            （HumanTrait 默认上下限，实测复核）

尺子自证（三控齐全，缺一个都不算尺子）：

    * 正控  臂 27 点网格（_work/muscleline/out/grid_probe.json）上 **旧律** 必须命中（<= 1e-3 度）
    * 负控  同一张臂网格上 **新律** 必须变红（>= 10 度）
    * 尺子  手/脚 27 点网格（_work/handline/out/grid_hand3.json）上旧律必须变红（>= 10 度）
    * 场外  3 个真 clip x 4 时刻 x 4 根肢端骨：新律 <= 1e-3 度；旧律最大 >= 1 度（臂上正好反过来）

⛔ 本模块不写盘、不开 Unity、不碰游戏目录；只读 _work/ 下的落盘读数。
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import muscle_solve as ms

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
ML = WORK / "muscleline" / "out"
HL = WORK / "handline" / "out"

PROBE_JSON = ML / "muscle_probe.json"
RIG_DUMP = ML / "rig_dump.json"
GRID_ARM3 = ML / "grid_probe.json"
GRID_HAND = ML / "grid_hand.json"
GRID_HAND3 = HL / "grid_hand3.json"
GRID_GAP = HL / "grid_gap.json"
GAP_BONES = (
    "LeftMiddleProximal",
    "RightMiddleProximal",
    "LeftLittleProximal",
    "RightLittleProximal",
    "Jaw",
)
CLIPS = ("Kneel_aim", "Prone_idle", "Prone_run")
LIMB_BONES = ("LeftHand", "RightHand", "LeftFoot", "RightFoot")

REGEN = (
    "复算：见 _work/handline/REPORT.md §9（新网格 = powershell -ExecutionPolicy Bypass "
    "-File _work\\handline\\run_grid3.ps1）"
)

IDENT = [0.0, 0.0, 0.0, 1.0]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"载不进 {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ★ 与甲线同一把尺子（tools/muscle_reference.py::geodesic_deg），⛔ 不自己另写一份
_REF = _load(REPO / "tools" / "muscle_reference.py", "muscle_reference_handfoot")
geodesic_deg = _REF.geodesic_deg


# --------------------------------------------------------------------- 两条律
def _theta(mm, m: int, v: float) -> float:
    return ms._theta_minmax(v, mm[m])


def _tan(mm, e, v: float) -> float:
    return math.tan(math.radians(_theta(mm, e.index, v)) / 2.0)


def _tansum(pairs) -> list[float]:
    """切线空间向量相加 —— 与 grid_hand.py / solve_pose_swing 逐字同一写法。"""
    (ax0, t0), (ax1, t1) = pairs
    n = math.hypot(t0, t1)
    if n < 1e-12:
        return list(IDENT)
    u = [(t0 * ax0[k] + t1 * ax1[k]) / n for k in range(3)]
    return ms.axis_angle(u, math.degrees(2.0 * math.atan(n)))


def law_limb(es, v, mm) -> list[float]:
    """现状（脊柱/四肢）：1 条 = 单转 · 2 条 = 切线相加 · 3 条 = 前两条切线相加 + 第三条右乘。"""
    if len(es) == 1:
        return ms.axis_angle(list(es[0].axis), _theta(mm, es[0].index, v[0]))
    sw = _tansum([(es[0].axis, _tan(mm, es[0], v[0])), (es[1].axis, _tan(mm, es[1], v[1]))])
    if len(es) >= 3:
        sw = ms.qmul(sw, ms.axis_angle(list(es[2].axis), _theta(mm, es[2].index, v[2])))
    return sw


def law_handfoot(es, v, mm) -> list[float]:
    """★ 手/脚：2 条与 limb 同形（切线相加）；3 条 = ch0 在外（左乘）+ ch1/ch2 切线相加在内。"""
    if len(es) == 1:
        return ms.axis_angle(list(es[0].axis), _theta(mm, es[0].index, v[0]))
    if len(es) == 2:
        return _tansum([(es[0].axis, _tan(mm, es[0], v[0])), (es[1].axis, _tan(mm, es[1], v[1]))])
    inner = _tansum([(es[1].axis, _tan(mm, es[1], v[1])), (es[2].axis, _tan(mm, es[2], v[2]))])
    return ms.qmul(ms.axis_angle(list(es[0].axis), _theta(mm, es[0].index, v[0])), inner)


def law_seq(es, v, mm) -> list[float]:
    """对照律：逐条右乘（⛔ 只在 2 通道盲区那几条用例里当「尺子要能红」的对照）。"""
    q = list(IDENT)
    for i, e in enumerate(es):
        t = _theta(mm, e.index, v[i])
        if t:
            q = ms.qmul(q, ms.axis_angle(list(e.axis), t))
    return q


# --------------------------------------------------------------------- 夹具
@pytest.fixture(scope="module")
def rig():
    if not PROBE_JSON.exists():
        pytest.fail(f"缺单肌肉激励探针 {PROBE_JSON}（这把尺子读真数据，不许跳）。{REGEN}")
    return ms.MuscleRig.from_probe(PROBE_JSON)


@pytest.fixture(scope="module")
def cm(rig):
    return rig.channel_map()


@pytest.fixture(scope="module")
def mm():
    if not RIG_DUMP.exists():
        pytest.fail(f"缺 {RIG_DUMP}（HumanTrait 默认上下限）。{REGEN}")
    return json.loads(RIG_DUMP.read_text(encoding="utf-8"))["muscleDefaultMinMax"]


def _worst(grid_path: Path, bone: str, law, cm, rig, mm, *, with_base: bool = True) -> float:
    """with_base=True：与 q_base 合成后再比（本文件其余用例的口径）。

    with_base=False：**grid_hand.py 的原口径**（相对量 M 直接与绝对 q 比）——
    只留给那条复现任务书 X 的用例。
    """
    g = json.loads(grid_path.read_text(encoding="utf-8"))
    grp = next((x for x in g["groups"] if x["bone"] == bone), None)
    if grp is None:
        pytest.fail(f"{grid_path.name} 里没有 {bone} 组。{REGEN}")
    es = [cm[m] for m in grp["channels"]]
    q0 = rig.base[bone]
    worst = 0.0
    for p in grp["points"]:
        v = [float(x) for x in p["v"]] + [0.0] * (3 - len(p["v"]))
        pred = law(es, v, mm)
        if with_base:
            pred = ms.qmul(pred, q0)
        worst = max(worst, geodesic_deg(pred, p["q"]))
    return worst


# --------------------------------------------------------------------- 正控 / 负控：臂
@pytest.mark.parametrize("bone", ["LeftUpperArm", "RightUpperArm"])
def test_arm_old_law_is_exact(bone, cm, rig, mm) -> None:
    """正控：臂 27 点网格上**旧律**必须命中（<= 1e-3 度）—— 尺子必须能绿。"""
    if not GRID_ARM3.exists():
        pytest.fail(f"缺臂网格 {GRID_ARM3}。{REGEN}")
    worst = _worst(GRID_ARM3, bone, law_limb, cm, rig, mm)
    assert worst <= 1e-3, f"{bone} 旧律在臂网格上 = {worst:.6f} 度（应 <= 1e-3）"


@pytest.mark.parametrize("bone", ["LeftUpperArm", "RightUpperArm"])
def test_arm_new_law_goes_red(bone, cm, rig, mm) -> None:
    """负控：同一张臂网格上**新律**必须变红（>= 10 度）—— 尺子必须能红。"""
    if not GRID_ARM3.exists():
        pytest.fail(f"缺臂网格 {GRID_ARM3}。{REGEN}")
    worst = _worst(GRID_ARM3, bone, law_handfoot, cm, rig, mm)
    assert worst >= 10.0, f"{bone} 新律在臂网格上只差 {worst:.4f} 度（应 >= 10）—— 尺子没分辨力"


# --------------------------------------------------------------------- 手 / 脚
@pytest.mark.parametrize("bone", ["LeftHand", "LeftFoot"])
def test_hand_foot_new_law_is_exact(bone, cm, rig, mm) -> None:
    """★ 结论：手/脚 27 点网格上新律命中（<= 1e-3 度）。"""
    if not GRID_HAND3.exists():
        pytest.fail(
            f"缺 handline 自产的 3 通道网格 {GRID_HAND3}"
            f"（判据 = 产物 mtime / 跑完 Unity 进程 0）。{REGEN}"
        )
    worst = _worst(GRID_HAND3, bone, law_handfoot, cm, rig, mm)
    assert worst <= 1e-3, f"{bone} 新律 = {worst:.6f} 度（应 <= 1e-3）"


@pytest.mark.parametrize("bone", ["LeftHand", "LeftFoot"])
def test_hand_foot_old_law_goes_red(bone, cm, rig, mm) -> None:
    """现状律在手/脚上必须变红 —— 这正是 task-10 要解的那个偏差。"""
    if not GRID_HAND3.exists():
        pytest.fail(f"缺 {GRID_HAND3}。{REGEN}")
    worst = _worst(GRID_HAND3, bone, law_limb, cm, rig, mm)
    assert worst >= 10.0, f"{bone} 旧律只差 {worst:.4f} 度（应 >= 10）"


def test_hand_foot_25point_old_law_reproduces_the_task_number(cm, rig, mm) -> None:
    """复现任务书上的 X（25 点网格 + grid_hand.py 的原口径）：LeftHand 74.3316 · LeftFoot 41.2018。

    ⚠ 那条口径把相对量 M 与绝对量 q 直接比、少了 ∘ q_base ⇒ 任何正确模型在它下面
      也只能到 |q_base|（LeftHand 28.3685 · LeftFoot 7.3903）。本用例只做**锚点复现**，
      ⛔ 不是拿它当验收判据。
    """
    if not GRID_HAND.exists():
        pytest.fail(f"缺 {GRID_HAND}。{REGEN}")
    got = {
        b: _worst(GRID_HAND, b, law_limb, cm, rig, mm, with_base=False)
        for b in ("LeftHand", "LeftFoot")
    }
    assert got["LeftHand"] == pytest.approx(74.3316, abs=5e-4), got
    assert got["LeftFoot"] == pytest.approx(41.2018, abs=5e-4), got


# --------------------------------------------------------------------- 场外：真 clip
def _clip_cases(cm, rig, mm):
    rd = json.loads(RIG_DUMP.read_text(encoding="utf-8"))
    cases = []
    for clip in CLIPS:
        ppath = ML / "rigpayloads" / f"{clip}.unity_payload.json"
        if not ppath.exists() or clip not in rd.get("poses", {}):
            pytest.fail(f"缺 {clip} 的 bake 载荷 {ppath} 或 rig_dump.poses。{REGEN}")
        pl = json.loads(ppath.read_text(encoding="utf-8"))
        for bone in LIMB_BONES:
            chans = sorted(m for m, e in cm.items() if e.bone == bone)
            assert len(chans) >= 3, f"{bone} 的通道不足 3 条：{chans}"
            es = [cm[m] for m in chans]
            q0 = rig.base[bone]
            for ti, sample in enumerate(rd["poses"][clip]):
                mus = pl["muscles"][ti * 95 : (ti + 1) * 95]
                cases.append(
                    (clip, ti, bone, es, [mus[m] for m in chans], q0, sample["bones"][bone])
                )
    return cases


def test_real_clips_out_of_sample(cm, rig, mm) -> None:
    """★ 场外：3 clip x 4 时刻 x 4 根肢端骨 —— 新律 <= 1e-3 度，旧律最大 >= 1 度。

    ⛔ 不回代：结构从 grid_hand3 反解、轴与角从单肌肉探针读，本处的 muscles 是**第三份**数据
    （且用的是烘 bake 时逐时刻喂进去的那份，同源同序、无插值）。
    """
    cases = _clip_cases(cm, rig, mm)
    assert len(cases) == len(CLIPS) * 4 * len(LIMB_BONES), len(cases)
    new_worst = 0.0
    old_worst = 0.0
    for _clip, _ti, _bone, es, v, q0, tgt in cases:
        new_worst = max(new_worst, geodesic_deg(ms.qmul(law_handfoot(es, v, mm), q0), tgt))
        old_worst = max(old_worst, geodesic_deg(ms.qmul(law_limb(es, v, mm), q0), tgt))
    assert new_worst <= 1e-3, f"新律在真 clip 上最大 {new_worst:.6f} 度"
    assert old_worst >= 1.0, f"旧律在真 clip 上只差 {old_worst:.6f} 度（应 >= 1）"


# --------------------------------------------------------------------- 角度映射
@pytest.mark.parametrize("bone", ["LeftHand", "LeftFoot"])
def test_single_channel_angle_matches_human_trait(bone, cm, rig, mm) -> None:
    """单通道读数必须等于 θ = v>0 ? v·max : v·|min|（在 grid_hand3 的三个轴上各取 ±1）。"""
    if not GRID_HAND3.exists():
        pytest.fail(f"缺 {GRID_HAND3}。{REGEN}")
    g = json.loads(GRID_HAND3.read_text(encoding="utf-8"))
    grp = next(x for x in g["groups"] if x["bone"] == bone)
    ch = grp["channels"]
    pts = {tuple(round(float(x), 3) for x in p["v"]): p["q"] for p in grp["points"]}
    q0 = rig.base[bone]
    for i, m in enumerate(ch):
        for vv in (1.0, -1.0):
            key = tuple(vv if k == i else 0.0 for k in range(3))
            rel = ms._norm(ms.qmul(pts[key], ms.qinv(q0)))
            ang = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, abs(rel[3])))))
            want = abs(_theta(mm, m, vv))
            assert ang == pytest.approx(want, abs=1e-3), f"{bone} m{m} v={vv}: {ang} vs {want}"


# ------------------------------------------------- 盲区（振幅 = 0 的 5 根 2 通道骨）
@pytest.mark.parametrize("bone", GAP_BONES)
def test_gap_bones_are_covered_by_tangent_sum(bone, cm, rig, mm) -> None:
    """★ 真 clip 里振幅 = 0（因而任何律都「拟合」）的 5 根 2 通道骨 —— 这是全模型的**唯一盲区**。

    在它们各自的 5x5 网格上：「切线相加」必须命中（<= 1e-3），而「逐条右乘」必须变红（>= 0.5）
    —— 后一条是尺子在这 5 根上的**正控**（证明这张网格不是恒绿的）。
    """
    if not GRID_GAP.exists():
        pytest.fail(f"缺盲区网格 {GRID_GAP}（5 根 x 25 点）。{REGEN}")
    got_tan = _worst(GRID_GAP, bone, law_limb, cm, rig, mm)
    got_seq = _worst(GRID_GAP, bone, law_seq, cm, rig, mm)
    assert got_tan <= 1e-3, f"{bone} 切线相加 = {got_tan:.6f} 度（应 <= 1e-3）"
    assert got_seq >= 0.5, f"{bone} 对照律只差 {got_seq:.6f} 度（应 >= 0.5）—— 尺子没分辨力"


# ------------------------------------------------- 穷扫：有没有第三族
def _all_bone_samples(cm, rig):
    """把 3 个真 clip 的逐时刻 muscle 向量与烘出来的骨骼旋转配成样本（同源同序，无插值）。"""
    rd = json.loads(RIG_DUMP.read_text(encoding="utf-8"))
    by_bone: dict = {}
    for m, e in sorted(cm.items()):
        by_bone.setdefault(e.bone, []).append(m)
    samples = []
    for clip in CLIPS:
        ppath = ML / "rigpayloads" / f"{clip}.unity_payload.json"
        if not ppath.exists() or clip not in rd.get("poses", {}):
            pytest.fail(f"缺 {clip} 的 bake 载荷 {ppath} 或 rig_dump.poses。{REGEN}")
        pl = json.loads(ppath.read_text(encoding="utf-8"))
        for ti, s in enumerate(rd["poses"][clip]):
            samples.append((pl["muscles"][ti * 95 : (ti + 1) * 95], s["bones"]))
    return by_bone, samples


def test_no_third_family_among_exercised_bones(cm, rig, mm) -> None:
    """★ 穷扫有通道的骨：凡振幅 > 1 度的骨，必须落在「律 L」或「律 H」的 1e-3 度内。

    ⇒ 「有没有第三族」的机械化答案。振幅 <= 1 度的骨在这批数据上**无信息**（任何律都拟合）；
    本仓实测 13 根（小指/中指 12 根 + Jaw），其中 5 根 2 通道骨已由 grid_gap.json 单独补齐。
    """
    by_bone, samples = _all_bone_samples(cm, rig)
    exercised, blind, bad = 0, [], []
    for bone, chans in sorted(by_bone.items()):
        if bone not in rig.base:
            continue
        es = [cm[m] for m in chans]
        q0 = rig.base[bone]
        amp = wl = wh = 0.0
        for mus, bones in samples:
            if bone not in bones:
                continue
            v = [mus[m] for m in chans]
            tgt = bones[bone]
            amp = max(amp, geodesic_deg(q0, tgt))
            wl = max(wl, geodesic_deg(ms.qmul(law_limb(es, v, mm), q0), tgt))
            wh = max(wh, geodesic_deg(ms.qmul(law_handfoot(es, v, mm), q0), tgt))
        if amp <= 1.0:
            blind.append(bone)
            continue
        exercised += 1
        if min(wl, wh) > 1e-3:
            bad.append((bone, round(wl, 5), round(wh, 5), round(amp, 2)))
    assert exercised >= 30, (
        f"有振幅的骨只有 {exercised} 根 —— 数据把尺子架空了（无信息 {len(blind)} 根）"
    )
    assert not bad, f"两族都不中的骨（骨, 律L, 律H, 振幅）：{bad}"
