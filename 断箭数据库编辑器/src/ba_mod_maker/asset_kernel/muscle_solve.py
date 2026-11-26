"""甲：纯 Python 把肌肉值算成骨骼局部旋转（复现 Unity HumanPoseHandler）。

判据一行（由 tools/muscle_reference.py 判）：
    甲 算出的 53 骨 vs Unity 烘出的 bake 列，逐骨**测地距离**（2*acos|dot|，度）
    通过 <= 1e-3 度 · 优秀 <= 1e-4 度；一律报**最差那条 clip x 时刻 x 骨**。

参数来源：**实测**，不是猜的约定 —— _work/muscleline/out/muscle_probe.json
（单通道激励探针：95 条肌肉逐条置 +0.5/-0.5/+1.0，读 53 骨）。
本模块**不 import 任何宿主模块**（Unity 与 Blender 都不碰），产品 venv 直接跑。

⚠⚠ **本模块当前状态（2026-10-12）：未通过，别以为它已经能用。**
    · 最简版（solve_pose 默认 **右乘** · 肌肉下标升序）在 3 clip x 4 时刻 x 53 骨上：
      **最差 174.94 度**（Prone_idle t=0 / LeftUpperArm）；分族最大：
      arms 133~175 · spine 57~92 · legs 30~102 · fingers 62.4 · **jaw 0.000**（对！）。
    · ⛔ **一条后来被自己推翻的结论，如实留着**：聚焦诊断
      （_work/muscleline/focus_leftarm.py，96 组）曾在 **Prone_idle t0** 上得出
      「**必须左乘**」（175 度 -> 10.80 度）。**那条是单样本表象。**
    · **更正后的读数**（_work/muscleline/variants_abc.py，在**良态骨**上重做）：
      良态骨 = LeftUpperArm @ Prone_run t2（三通道 (0.5316, -0.6053, 0.5651)，最小 |v| = 0.5316）；
      三种变体各穷举 144 组，最好解：
        A（固定轴+左乘）**26.0689 度** · B（固定轴+右乘）**25.4225 度** ·
        C（轴随当前旋转更新）**26.4325 度**
      ⇒ **A/B/C 不可区分**，「必须左乘」不成立。
    · 良态骨上 **6 种顺序极差 39.48 度** ⇒ 顺序**确实重要**；
      但 (41,40,39) 与 (41,39,40) 仍打平到 4 位小数 ⇒ 真模型**不是「三条依次乘」**。
    · ⇒ 当前状态一句话：**A/B/C 不可区分 · 残余 ~25 度 · 模型形式待定**；
      **在那之前本模块不可用于交付**。下一步 = 27 点网格（3 通道各取 -1/0/+1）把输入->输出测全。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


class MissingAvatarParametersError(RuntimeError):
    """缺探针参数 —— ⛔ 明确报错，绝不静默返回单位四元数。"""


def _norm(q):
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return [x / n for x in q]


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def qinv(q):
    n = sum(x * x for x in q) or 1.0
    return [-q[0] / n, -q[1] / n, -q[2] / n, q[3] / n]


def axis_angle(axis, deg):
    """绕 axis 转 deg 度的四元数。（axis 必须已归一）"""
    n = math.sqrt(sum(x * x for x in axis)) or 1.0
    a = [x / n for x in axis]
    half = math.radians(deg) / 2.0
    s = math.sin(half)
    return [a[0] * s, a[1] * s, a[2] * s, math.cos(half)]


@dataclass(frozen=True)
class MuscleChannel:
    index: int
    bone: str
    axis: tuple          # **主骨**的旋转轴（父系）
    deg: float           # v=+1 时**主骨**的转角
    hips_axis: tuple = (0.0, 0.0, 0.0)   # 同一次激励下 **Hips** 的旋转轴（用于符号对齐）


@dataclass
class MuscleRig:
    """从激励探针读出来的「肌肉 -> (骨 · 轴 · 度数)」＋ 全 0 基线姿态。"""

    base: dict            # 全 0 基线：骨 -> 四元数
    entries: list         # MuscleChannel[]
    rest: dict            # 真静止姿态（可选）

    @classmethod
    def from_probe(cls, probe_path, summary_path=None):
        p = Path(probe_path)
        if not p.exists():
            raise MissingAvatarParametersError(
                f"缺激励探针产物 {p}（复算：_work/muscleline/run_rig.ps1）")
        d = json.loads(p.read_text(encoding="utf-8"))
        probes = d.get("probes") or []
        if not probes:
            raise MissingAvatarParametersError(f"{p} 里没有 probes")
        base = {k: list(v) for k, v in probes[0]["bones"].items()}
        # 每条肌肉取 v=+1.0 的那次，算 q_probe * q_base^-1 -> (轴, 度)
        per = {}
        for pr in probes[1:]:
            per.setdefault(pr["m"], {})[pr["v"]] = pr["bones"]
        entries = []
        for m in sorted(per):
            bones = per[m].get(1.0) or per[m].get(0.5)
            if not bones:
                continue
            best = None
            for b, q in bones.items():
                dq = qmul(q, qinv(base[b]))
                dq = _norm(dq)
                if dq[3] < 0:
                    dq = [-x for x in dq]
                w = max(-1.0, min(1.0, dq[3]))
                ang = math.degrees(2.0 * math.acos(w))
                if best is None or ang > best[0]:
                    s = math.sqrt(max(0.0, 1.0 - w * w))
                    ax = [dq[0] / s, dq[1] / s, dq[2] / s] if s > 1e-9 else [0.0, 0.0, 0.0]
                    best = (ang, b, ax)
            if best and best[0] > 1e-9:
                ha = (0.0, 0.0, 0.0)
                hb = bones.get("Hips")
                if hb is not None:
                    hd = qmul(hb, qinv(base["Hips"])) if "Hips" in base else None
                    if hd is not None:
                        hd = _norm(hd)
                        if hd[3] < 0:
                            hd = [-x for x in hd]
                        sw = max(-1.0, min(1.0, hd[3]))
                        sv = math.sqrt(max(0.0, 1.0 - sw * sw))
                        if sv > 1e-9:
                            ha = (hd[0] / sv, hd[1] / sv, hd[2] / sv)
                entries.append(MuscleChannel(m, best[1], tuple(best[2]), best[0], ha))
        if not entries:
            raise MissingAvatarParametersError("探针里没有任何「能驱动骨骼」的肌肉")
        return cls(base=base, entries=entries, rest={})

    def channel_map(self):
        return {e.index: e for e in self.entries}


def _theta_minmax(v, mm):
    """★ 实测定的角度映射：**过零点的分段线性**（正侧按 max、负侧按 |min|）。
    mm = [min, max]（HumanTrait.GetMuscleDefaultMin/Max）。
    ⛔ 不是 lerp(min,max,(v+1)/2)：那条在 v=0 会给 (min+max)/2 != 0，
       与基线读数矛盾（已实测排除）。
    """
    return v * (mm[1] if v >= 0 else -mm[0])


# ★ 肢端 4 根（实测：handline 用 grid_hand.json + 48 场外样本解出）：
#   手/脚 = Rot(a0, θ0) ∘ tansum(a1, a2)  —— **ch0 在外左乘、ch1/ch2 切线相加在内**，
#   与脊柱/四肢（tansum(a0,a1) ∘ Rot(a2,θ2)）**正好相反**。读数：LeftHand 74.33 -> 4.76e-05 ·
#   LeftFoot 41.20 -> 3.10e-05；场外 48 样本 4.19e-05（旧律 20.808）。⛔ 只这四根，别一起改臂。
LIMB_END = ("LeftHand", "RightHand", "LeftFoot", "RightFoot")


def _tansum(e0, e1, th0, th1):
    """两条通道的切线参数向量和 -> 一个旋转。"""
    t0 = math.tan(math.radians(th0) / 2.0)
    t1 = math.tan(math.radians(th1) / 2.0)
    n = math.hypot(t0, t1)
    if n < 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    u = [(t0 * e0.axis[k] + t1 * e1.axis[k]) / n for k in range(3)]
    return axis_angle(u, math.degrees(2.0 * math.atan(n)))


def solve_pose_swing(muscles, rig: MuscleRig, minmax, body_rot=None, root="Hips"):
    """★★ 甲的正确形式（从实测反解 + 25 点验证，最大偏差 3.5e-05 度）：

      1) θi = vi>0 ? vi·max : vi·|min|                （分段线性，实测）
      2) ti = tan(θi/2)                               （Cayley / 切线参数）
      3) 摆动（前两条通道）**在切线空间里向量相加**：
             n = sqrt(t0²+t1²) · u = (t0·a0 + t1·a1)/n · φ = 2·atan(n)
             M = Rot(u, φ)
      4) twist（第三条通道，右乘、沿骨轴）：  M = M · Rot(a2, θ2)
      5) q = M ⊗ q_base
    ⛔ 单通道退化正确（n = |ti| ⇒ φ = |θi|）；⛔ 不是「依次相乘」——那个在两条同时非 0 时必错。
    ⛔ 权重是 tan(θ/2)，**不是 sinθ**（用 sin 时负侧会差 15.5 度，实测过）。
    """
    cm = rig.channel_map()
    per: dict = {}
    for m in sorted(cm):
        per.setdefault(cm[m].bone, []).append(m)
    out = {b: list(q) for b, q in rig.base.items()}
    for bone, ms in per.items():
        es = [cm[m] for m in ms]
        th = [_theta_minmax(muscles[m], minmax[m]) for m in ms]
        if len(es) == 1:
            big = axis_angle(list(es[0].axis), th[0])
        elif len(es) >= 3 and bone in LIMB_END:
            big = qmul(axis_angle(list(es[0].axis), th[0]),
                       _tansum(es[1], es[2], th[1], th[2]))
        else:
            big = _tansum(es[0], es[1], th[0], th[1])
            if len(es) >= 3:
                big = qmul(big, axis_angle(list(es[2].axis), th[2]))
        cur = out.get(bone)
        if cur is not None:
            out[bone] = qmul(big, cur)
    if body_rot is not None and root in out:
        # ★ 根骨：实测（rot_probe.json）肌肉全 0 时 hips 与 bodyRotation **逐位相同**；
        #   有肌肉时 = bodyRotation 与「只由肌肉决定的 rel」的合成（rel 尚未定出）。
        #   ⇒ 这里先接上 bodyRotation（零成本、已证），把 Hips 的 91.69 度压到「只剩 rel 那一项」。
        # ★ 根骨 = bodyRotation ⊗ rel(muscles)（rel 与 bodyRotation 无关 —— 激励段实测：
        #   肌肉全 0 时 hips 与 bodyRotation 逐位相同；rel 只由肌肉决定）。
        out[root] = qmul(list(body_rot), qmul(hips_rel(muscles, rig), out[root]))
    return out


def solve_pose(muscles, rig: MuscleRig, order=None, left_multiply=False):
    """最简版：从全 0 基线出发，按 order 逐条把 DoF 旋转**右乘**到该骨上。

    ⚠ 「三个 DoF 同时非 0 时怎么合成」（顺序 / 左乘还是右乘）**是探针没测到的量** ——
      这里给的是最简假设，正是要靠读数去否证的东西，⛔ 不是已知。
    """
    out = {b: list(q) for b, q in rig.base.items()}
    cm = rig.channel_map()
    seq = list(order) if order is not None else sorted(cm)
    for m in seq:
        v = muscles[m]
        if not v:
            continue
        e = cm.get(m)
        if e is None:
            continue
        r = axis_angle(list(e.axis), v * e.deg)
        cur = out.get(e.bone)
        if cur is None:
            continue
        out[e.bone] = qmul(r, cur) if left_multiply else qmul(cur, r)
    return out


# ★ Hips 的 rel（实测形式，见 _work/muscleline/k_form.py 与 holdout_check.py 的读数）：
#     tan(rel角/2)·axis = Σ_{脊链 9 条} k_seg · tan(θ_main/2) · **hips_axis**
#     ★★ 2026-10-13 更正（_work/hipsline/）：轴一律取 **e.hips_axis**（探针实测），
#        ⛔ 不再用主骨轴。两者 dot = −0.99487 ~ −1；用主骨轴的代价 = Hips 6.897797 → 7.535490。
#        ⛔ 仍未解的是**合成规则**（见 _work/hipsline/REPORT.md）。
#     k 由**单条激励**（v=0.5/1.0 两点平均，两点差 <=0.0099）读出，**未参与**任何多通道拟合。
#     读数（**task-11 更正后**）：三条真动画最大 **7.535490**（原 8.503151）·
#     平均 5.754（原 6.4717）；**场外** exc2 的 pair 最大 **4.371**（原 4.653）。
#     ★ 肩部 4 条（m37/38/46/47）**已并入**（task-11）。旧注释写「两种轴基准都试过，
#       26.02 / 39.60 都更差」—— 那两次用的是**主骨轴**折算，而实测「主骨轴 vs 激励它时
#       Hips 自己的轴」差 **31.6°(m38) / 50.4°(m37)**，折算必然错。改用 e.hips_axis 后：
#       场外肩部两对 2.40~4.65 → **0.03~0.22**。
#     ⛔ **残差仍随幅度增长，本形式不精确** —— 证据见 _work/handline/REPORT.md §12：
#       实测表明 Hips 的分配**不是「按条旋转的合成」**：同轴两条（m0&m3）实测
#       56.2609° > 两条单独之和 54.7728°，而绕同一根轴的两个旋转合成**不可能**超过
#       两角之和 ⇒ 缺的是**与链构型有关的机制**，不是再标定一次 k。
SPINE_SEG = {0: "Spine", 1: "Spine", 2: "Spine", 3: "Chest", 4: "Chest", 5: "Chest",
             6: "UpperChest", 7: "UpperChest", 8: "UpperChest"}
TWIST_M = (2, 5, 8)
SEG_K = {("Spine", "wing"): 0.77592, ("Spine", "twist"): 0.65387,
         ("Chest", "wing"): 0.56084, ("Chest", "twist"): 0.65387,
         ("UpperChest", "wing"): 0.35748, ("UpperChest", "twist"): 0.65730}

# ★ 肩部 4 条（task-11 并入）：激励它们时**脊链完全不动、Hips 却动** ⇒ 纯重新分配。
#   轴必须用 e.hips_axis（激励它时 Hips 自己的轴），⛔ 不是主骨轴（实测差 31.6°/50.4°）。
#   k = tan(θ_H(1)/2)/tan(θ_B(1)/2)，**单条激励**读出：
#   m37/m46 θH=1.5106°/θB=30° · m38/m47 θH=5.4182°/θB=15°。
SHOULDER_M = (37, 38, 46, 47)
SHOULDER_K = {37: 0.049208, 38: 0.359469, 46: 0.049208, 47: 0.359469}


def hips_rel(muscles, rig: MuscleRig):
    """Hips 的 rel 项（实测形式；脊链 9 条 + **肩部 4 条**，见 SHOULDER_M 处的说明）。

    ⚠ **trade-off（如实记，别藏）**：并入肩部后 12 个 clip 时刻里 **10 个变好、2 个变坏**：
    Prone_run t0 3.00 → 5.57 度 · t3 3.05 → 5.63 度（各 +2.6 度）。原因 = 下面那条**缺掉的机制**
    （Hips 的分配**不是**「按条旋转的合成」，证据见 _work/handline/REPORT.md §12.3）没补上时，
    **独立叠加肩部会过冲**。净读数仍是改善：accept.py 最差 8.503151 → 7.535490；
    场外 exc2 肩部两对 2.40~4.65 → 0.03~0.22。
    """
    cm = rig.channel_map()
    v3 = [0.0, 0.0, 0.0]
    for m, seg in SPINE_SEG.items():
        e = cm.get(m)
        if e is None:
            continue
        # ★★ 2026-10-13 修正（唯一改动）：脊链也用 **e.hips_axis**，⛔ 不再用主骨轴。
        #   判据一行（_work/hipsline/an52_axes.py）：
        #     dot(探针实测轴, ch.hips_axis) = **+1.000000**，13/13 条；
        #     而 dot(探针实测轴, ch.axis) = −0.99487 ~ −1.000000
        #     ⇒ ch.hips_axis 就是「激励它时 Hips 的旋转轴」，主骨轴只是它差一个符号的近似。
        #   用主骨轴的代价（_work/hipsline/an51_full.py，全 53 骨 accept 口径）：
        #     Hips 7.535490 → **6.897797** 度（零新增自由参数）。
        u = list(e.hips_axis) if any(e.hips_axis) else list(e.axis)
        k = SEG_K[(seg, "twist" if m in TWIST_M else "wing")]
        t = math.tan(math.radians(muscles[m] * e.deg) / 2.0)
        for i in range(3):
            v3[i] += k * t * u[i]
    for m in SHOULDER_M:  # ★ task-11：肩部 4 条，轴用 Hips 自己的
        e = cm.get(m)
        if e is None or not any(e.hips_axis):
            continue
        t = SHOULDER_K[m] * math.tan(math.radians(muscles[m] * e.deg) / 2.0)
        for i in range(3):
            v3[i] += t * e.hips_axis[i]
    n = math.sqrt(sum(x * x for x in v3))
    if n < 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    return axis_angle([x / n for x in v3], math.degrees(2.0 * math.atan(n)))


FAMILIES = (
    ("spine", ("Hips", "Spine", "Chest", "UpperChest", "Neck", "Head")),
    ("eyes", ("Eye",)),
    ("jaw", ("Jaw",)),
    ("legs", ("UpperLeg", "LowerLeg", "Foot", "Toes")),
    ("arms", ("Shoulder", "UpperArm", "LowerArm", "Hand")),
    ("fingers", ("Thumb", "Index", "Middle", "Ring", "Little")),
)


def family_of(bone: str) -> str:
    """骨名 -> 骨族（报偏差时按族分组，一眼看出是哪一族的合成假设错了）。"""
    for name, keys in FAMILIES:
        if name in ("spine", "eye", "jaw") and bone in keys:
            return name
        if name == "eyes" and "Eye" in bone:
            return name
        if name == "jaw" and bone == "Jaw":
            return name
        if name not in ("spine", "eyes", "jaw") and any(k in bone for k in keys):
            return name
    return "other"
