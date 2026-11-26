"""unity_humanpose_bake —— 人形肌肉乙路：把 clip 的 130 条肌肉曲线烘成骨骼局部旋转。

判据一行（可复算、单调方向）：

    .venv/Scripts/python.exe tools/unity_humanpose_bake.py <副本包> <clip>
      -> 逐骨最大偏差 maxDev：从「无读数」变成 **< 1e-5**（VERDICT OK，exit 0）

三控（先证明尺子，再谈结论）：

    * 正控     Kneel_aim / Prone_run / Prone_idle（72 条纯人形里的三条）
               -> 各 130 条肌肉曲线，maxDev < 1e-5（exit 0）
    * 负控     generic clip（ATGM，pid -9207998336256044584）
               -> 报「这条不含肌肉」＋ exit 2（⛔ 不是静默返回空）
    * 尺子正控 --perturb 0 0.05（只扰烘制侧一根骨）
               -> maxDev 必须 > 1e-3（尺子能红）

通道口径（attribute 7..136 = 130 条）。证据两份：
  * 研究-模型与动画/07-clip解码/humanoid-decode.md（解码线结论）；
  * 本机落盘读数 _work/anim-recon/out/m1_clip_classify.json —— 72/72 条纯人形绑定的
    attribute **恰好**是 7..136、typeID 全 95、customType 全 8，0 条例外。

      7..9    RootT.x/y/z
     10..13   RootQ.x/y/z/w
     14..41   四个 goal（LeftFoot / RightFoot / LeftHand / RightHand）x (T3 + Q4)
     42..96   55 条身体 DoF 肌肉  = HumanTrait.MuscleName[0..54]
     97..116  左手 20 条          = HumanTrait.MuscleName[55..74]
    117..136  右手 20 条          = HumanTrait.MuscleName[75..94]

⛔ 已知**装不下**的一环（不藏，写在这里）：C# 的 HumanPose 只有
   {bodyPosition, bodyRotation, muscles[95]}，**没有 4 个 goal**（attribute 14..41）。
   烘制侧因此对 goal 无输入；若某条 clip 的 goal 明显偏离静止位姿，逐骨偏差会被这一项顶起来 ——
   那是真值尺子**该看见**的东西，不是要藏的东西。

m_IndexArray 的真身（本机 72 条读数定的；复算 _work/muscleline/test_hypothesis_H.py）：

    全局曲线号是**按存储块**编的 —— 0..s-1 = streamed · s..s+dn-1 = dense · 其余 = constant
    （与 curvedata.cpp::EvaluateClipAtIndex 的三块顺序一致）。
    m_IndexArray[attribute] 就是「这条肌肉通道的数据落到了哪个全局号」。
    Unity 的压缩器是**逐曲线**选块的 ⇒ **同一根骨的 4 个分量可以落在不同的块**：
    Prone_idle 的 RootQ.xyzw = 全局 [2, 3, 58, 68] = streamed / streamed / dense / constant。

    ⇒ 所以「4 个分量在全局号上必须连续」这个直觉是**错的**；
      任何靠「连续 4 连窗 + 模长==1」去找四元数的尺子，在非恒等 clip 上**注定判不了**。
    ⇒ 72/72 条：indexMap 是 0..129 的双射；72/72 条：肌肉通道的 S/D/C 计数**恰好**等于
      这条 clip 的 (streamed, dense, constant)。两条都进了 tests/test_anim_humanoid.py。
    ⇒「44 种不同排列」= 44 种**压缩布局**，不是 44 个待破的谜题。
      Lead 的假设 H（排列由 Avatar 决定）在**计数上**就被证伪：44 种 > 15 个人形 Avatar
      （units 包里只有 16 个 Avatar 对象）。

验收分两层（Lead 2026-10-12 改判；⛔ 只报 L2 全绿 = 无效）：

    L1 映射层（独立真值，需要 Unity **一次哨兵式运行**）
       曲线名 -> attribute 号，必须与 Unity 自己序列化的 .anim YAML 里 attribute: 字段**逐条相等**。
       Unity 是权威，不是我们推的。
       ⚠ 为什么必须有这一层：72 条纯人形里 **66 条**的 m_IndexArray[7:137] 是**非恒等排列**
       （44 种不同排列；复算 _work/muscleline/dump_index_maps.py -> out/index_maps_72.json），
       所以「attribute = 曲线号 + 7」这条例在 66/72 条上**是错的**。
    L2 数值层（本工具主命令）
       L1 通过之后，烘出的骨骼旋转 vs Animator 采样同一时刻，逐骨 maxDev < 1e-5。
       ⚠ L2 两侧吃的是**同一份通道映射** ⇒ 映射错了 L2 照样全绿（假绿）。

乙路的边界（写死，别让下一个人以为 72 条能整条 1:1 还原）：
    **覆盖 muscle 通道（attribute 42..136，95 条）；goal 通道（attribute 14..41）无输入** ——
    因为 C# 的 HumanPose 只有 {bodyPosition, bodyRotation, muscles[95]}。
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import re
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = REPO.parent                                   # D:\断箭模组制作
RESEARCH = BASE / "研究-模型与动画"
DECODER_DIR = RESEARCH / "07-clip解码"
LOCKS_DIR = WORK / "locks"
import os
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
OUT = WORK / "out"
PROJ = WORK / "proj"
UNITY_EXE = Path(r"D:\Unity\2022.3.62f3c1\Editor\Unity.exe")
UNITY_METHOD = "MuscleLine.Run"

# ---------------------------------------------------------------- 通道表
HUMAN_BODY_BONES: tuple[str, ...] = (
    # 下标 == UnityEngine.HumanBodyBones 的枚举值（0..54），顺序即 55 根人形骨
    "Hips", "LeftUpperLeg", "RightUpperLeg", "LeftLowerLeg", "RightLowerLeg",
    "LeftFoot", "RightFoot",
    "Spine", "Chest", "UpperChest", "Neck", "Head",
    "LeftShoulder", "RightShoulder", "LeftUpperArm", "RightUpperArm",
    "LeftLowerArm", "RightLowerArm", "LeftHand", "RightHand",
    "LeftToes", "RightToes", "LeftEye", "RightEye", "Jaw",
    "LeftThumbProximal", "LeftThumbIntermediate", "LeftThumbDistal",
    "LeftIndexProximal", "LeftIndexIntermediate", "LeftIndexDistal",
    "LeftMiddleProximal", "LeftMiddleIntermediate", "LeftMiddleDistal",
    "LeftRingProximal", "LeftRingIntermediate", "LeftRingDistal",
    "LeftLittleProximal", "LeftLittleIntermediate", "LeftLittleDistal",
    "RightThumbProximal", "RightThumbIntermediate", "RightThumbDistal",
    "RightIndexProximal", "RightIndexIntermediate", "RightIndexDistal",
    "RightMiddleProximal", "RightMiddleIntermediate", "RightMiddleDistal",
    "RightRingProximal", "RightRingIntermediate", "RightRingDistal",
    "RightLittleProximal", "RightLittleIntermediate", "RightLittleDistal",
)
BONE_INDEX = {name: i for i, name in enumerate(HUMAN_BODY_BONES)}

# 手指骨的三段名（HumanBodyBones 枚举里是 LeftThumbProximal，HumanTrait.BoneName 里是
# "Left Thumb Proximal" —— 带空格）。证据：游戏 15 个人形 Avatar 的
# m_HumanDescription.m_Human 用的就是带空格那套（_work/anim-recon/out/m1b_avatars.json）。
FINGER_BONE_STEMS: tuple[str, ...] = ("Proximal", "Intermediate", "Distal")


def bone_display_name(enum_name: str) -> str:
    """HumanBodyBones 成员名 -> Unity HumanTrait.BoneName 的显示名（只有手指带空格）。"""
    for side in ("Left", "Right"):
        if enum_name.startswith(side):
            rest = enum_name[len(side):]
            for finger in ("Thumb", "Index", "Middle", "Ring", "Little"):
                if rest.startswith(finger):
                    pos = rest[len(finger):]
                    if pos in FINGER_BONE_STEMS:
                        return f"{side} {finger} {pos}"
    return enum_name


# 手指骨（30 根）：它们只被 muscles[55..94] 驱动，而那 40 条**无数字 attribute**
# ⇒ Animator 采样自造 clip 时**看不见**它们 ⇒ L2 的比对必须把它们排掉（并要求各自都交代）。
FINGER_BONES: tuple[str, ...] = HUMAN_BODY_BONES[25:55]

HUMAN_BONE_DISPLAY: tuple[str, ...] = tuple(bone_display_name(b) for b in HUMAN_BODY_BONES)
BONE_DISPLAY_INDEX = {name: i for i, name in enumerate(HUMAN_BONE_DISPLAY)}

GOALS: tuple[str, ...] = ("LeftFoot", "RightFoot", "LeftHand", "RightHand")

# 55 条身体 DoF 肌肉名（= Unity human::MuscleName[0..54]；与
# 研究-模型与动画/07-clip解码/clip_decode.py::MUSCLE_NAMES 逐字相同）
BODY_MUSCLE_NAMES: tuple[str, ...] = (
    "Spine Front-Back", "Spine Left-Right", "Spine Twist Left-Right",
    "Chest Front-Back", "Chest Left-Right", "Chest Twist Left-Right",
    "UpperChest Front-Back", "UpperChest Left-Right", "UpperChest Twist Left-Right",
    "Neck Nod Down-Up", "Neck Tilt Left-Right", "Neck Turn Left-Right",
    "Head Nod Down-Up", "Head Tilt Left-Right", "Head Turn Left-Right",
    "Left Eye Down-Up", "Left Eye In-Out", "Right Eye Down-Up", "Right Eye In-Out",
    "Jaw Close", "Jaw Left-Right",
    "Left Upper Leg Front-Back", "Left Upper Leg In-Out",
    "Left Upper Leg Twist In-Out",
    "Left Lower Leg Stretch", "Left Lower Leg Twist In-Out",
    "Left Foot Up-Down", "Left Foot Twist In-Out", "Left Toes Up-Down",
    "Right Upper Leg Front-Back", "Right Upper Leg In-Out",
    "Right Upper Leg Twist In-Out",
    "Right Lower Leg Stretch", "Right Lower Leg Twist In-Out",
    "Right Foot Up-Down", "Right Foot Twist In-Out", "Right Toes Up-Down",
    "Left Shoulder Down-Up", "Left Shoulder Front-Back",
    "Left Arm Down-Up", "Left Arm Front-Back", "Left Arm Twist In-Out",
    "Left Forearm Stretch", "Left Forearm Twist In-Out",
    "Left Hand Down-Up", "Left Hand In-Out",
    "Right Shoulder Down-Up", "Right Shoulder Front-Back",
    "Right Arm Down-Up", "Right Arm Front-Back", "Right Arm Twist In-Out",
    "Right Forearm Stretch", "Right Forearm Twist In-Out",
    "Right Hand Down-Up", "Right Hand In-Out",
)

FINGERS: tuple[str, ...] = ("Thumb", "Index", "Middle", "Ring", "Little")
FINGER_DOFS: tuple[str, ...] = ("1 Stretched", "Spread", "2 Stretched", "3 Stretched")

# 手指 DoF(0..3) -> 人形骨（Unity：1=Proximal, 2=Intermediate, 3=Distal）
FINGER_DOF_BONE: tuple[tuple[str, int], ...] = (
    ("Proximal", 0), ("Proximal", 1), ("Intermediate", 0), ("Distal", 0),
)


def _finger_muscle_names(hand: str) -> list[str]:
    return [f"{hand}.{f}.{d}" for f in FINGERS for d in FINGER_DOFS]


MUSCLE_NAMES: tuple[str, ...] = (
    BODY_MUSCLE_NAMES
    + tuple(_finger_muscle_names("LeftHand"))
    + tuple(_finger_muscle_names("RightHand"))
)

# 55 条身体 DoF：(人形骨名, dofIndex)
BODY_DOF_BONES: tuple[tuple[str, int], ...] = (
    ("Spine", 0), ("Spine", 1), ("Spine", 2),
    ("Chest", 0), ("Chest", 1), ("Chest", 2),
    ("UpperChest", 0), ("UpperChest", 1), ("UpperChest", 2),
    ("Neck", 0), ("Neck", 1), ("Neck", 2),
    ("Head", 0), ("Head", 1), ("Head", 2),
    ("LeftEye", 0), ("LeftEye", 1), ("RightEye", 0), ("RightEye", 1),
    ("Jaw", 0), ("Jaw", 1),
    ("LeftUpperLeg", 0), ("LeftUpperLeg", 1), ("LeftUpperLeg", 2),
    ("LeftLowerLeg", 0), ("LeftLowerLeg", 1),
    ("LeftFoot", 0), ("LeftFoot", 1), ("LeftToes", 0),
    ("RightUpperLeg", 0), ("RightUpperLeg", 1), ("RightUpperLeg", 2),
    ("RightLowerLeg", 0), ("RightLowerLeg", 1),
    ("RightFoot", 0), ("RightFoot", 1), ("RightToes", 0),
    ("LeftShoulder", 0), ("LeftShoulder", 1),
    ("LeftUpperArm", 0), ("LeftUpperArm", 1), ("LeftUpperArm", 2),
    ("LeftLowerArm", 0), ("LeftLowerArm", 1),
    ("LeftHand", 0), ("LeftHand", 1),
    ("RightShoulder", 0), ("RightShoulder", 1),
    ("RightUpperArm", 0), ("RightUpperArm", 1), ("RightUpperArm", 2),
    ("RightLowerArm", 0), ("RightLowerArm", 1),
    ("RightHand", 0), ("RightHand", 1),
)


def _finger_dof_bones(hand: str) -> list[tuple[str, int]]:
    return [(hand + finger + stem, dof) for finger in FINGERS for stem, dof in FINGER_DOF_BONE]


# 95 条肌肉：(人形骨名, dofIndex)；下标 == HumanTrait.MuscleName 的下标
MUSCLE_BONES: tuple[tuple[str, int], ...] = (
    BODY_DOF_BONES
    + tuple(_finger_dof_bones("Left"))
    + tuple(_finger_dof_bones("Right"))
)

ATTR_FIRST = 7
ATTR_LAST = 136
N_CHANNELS = ATTR_LAST - ATTR_FIRST + 1          # 130
ATTR_ROOT_T = 7
ATTR_ROOT_Q = 10
ATTR_GOAL_FIRST = 14
ATTR_MUSCLE_FIRST = 42                            # == ATTR_GOAL_FIRST + 4 * 7


@dataclass(frozen=True)
class Channel:
    """一条肌肉通道：attribute -> 语义。"""

    attribute: int
    kind: str            # rootT / rootQ / goalT / goalQ / muscle
    name: str            # 人读的名字（RootT.x / LeftFootQ.w / Spine Front-Back ...）
    muscle_index: int    # 肌肉号 0..94；非肌肉 = -1
    goal: str            # LeftFoot / RightFoot / LeftHand / RightHand；非 goal = ""
    comp: str            # x / y / z / w / ""（肌肉无分量）

    def as_dict(self) -> dict:
        return {"attribute": self.attribute, "kind": self.kind, "name": self.name,
                "muscle_index": self.muscle_index, "goal": self.goal,
                "comp": self.comp}


def build_channels() -> tuple[Channel, ...]:
    """attribute 7..136 逐条摊开（130 条），顺序 = attribute 升序。"""
    ch: list[Channel] = []
    for comp in "xyz":
        ch.append(Channel(ATTR_ROOT_T + "xyz".index(comp), "rootT",
                          "RootT." + comp, -1, "", comp))
    for comp in "xyzw":
        ch.append(Channel(ATTR_ROOT_Q + "xyzw".index(comp), "rootQ",
                          "RootQ." + comp, -1, "", comp))
    a = ATTR_GOAL_FIRST
    for goal in GOALS:
        for comp in "xyz":
            ch.append(Channel(a, "goalT", f"{goal}T.{comp}", -1, goal, comp))
            a += 1
        for comp in "xyzw":
            ch.append(Channel(a, "goalQ", f"{goal}Q.{comp}", -1, goal, comp))
            a += 1
    for m, name in enumerate(MUSCLE_NAMES):
        ch.append(Channel(ATTR_MUSCLE_FIRST + m, "muscle", name, m, "", ""))
    ch.sort(key=lambda c: c.attribute)
    assert len(ch) == N_CHANNELS, len(ch)
    assert [c.attribute for c in ch] == list(range(ATTR_FIRST, ATTR_LAST + 1))
    return tuple(ch)


CHANNELS: tuple[Channel, ...] = build_channels()
CHANNEL_BY_ATTR = {c.attribute: c for c in CHANNELS}
MUSCLE_ATTR = {c.muscle_index: c.attribute for c in CHANNELS if c.muscle_index >= 0}

# ---------------------------------------------------------------- 分类器
class NotHumanoidClipError(ValueError):
    """这条 clip 不含肌肉曲线 —— 负控必须走这里（⛔ 不许静默返回空）。"""


MUSCLE_TYPE_ID = 95
MUSCLE_CUSTOM_TYPE = 8


def is_muscle_binding(b: dict) -> bool:
    """人形肌肉绑定：typeID=95(Animator) + customType=8 + attribute 落在 7..136。"""
    return (int(b.get("typeID", 0)) == MUSCLE_TYPE_ID
            and int(b.get("customType", 0)) == MUSCLE_CUSTOM_TYPE
            and ATTR_FIRST <= int(b.get("attribute", -1)) <= ATTR_LAST)


def classify_bindings(bindings: list[dict]) -> list[dict]:
    """把 bindings 里的肌肉挑出来；一条都没有 -> 抛 NotHumanoidClipError。"""
    muscles = [b for b in bindings if is_muscle_binding(b)]
    if not muscles:
        raise NotHumanoidClipError(
            f"这条不含肌肉：{len(bindings)} 条绑定里 "
            "typeID=95/customType=8/attribute 7..136 的有 0 条")
    seen: dict[int, int] = {}
    for b in muscles:
        a = int(b["attribute"])
        seen[a] = seen.get(a, 0) + 1
    clash = sorted(k for k, v in seen.items() if v > 1)
    if clash:
        raise NotHumanoidClipError(f"肌肉 attribute 重复：{clash[:8]}")
    have = sorted(seen)
    if have != list(range(ATTR_FIRST, ATTR_LAST + 1)):
        miss = sorted(set(range(ATTR_FIRST, ATTR_LAST + 1)) - set(have))
        raise NotHumanoidClipError(
            f"肌肉通道不是完整的 130 条：缺 {len(miss)} 条（{miss[:8]}）")
    return muscles


# ---------------------------------------------------------------- 解码器接缝
def decoder_module():
    """延迟取 研究-模型与动画/07-clip解码/clip_decode.py（研究线解码件，本单复用）。"""
    for p in (str(DECODER_DIR), str(RESEARCH / "tools")):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module("clip_decode")


def list_clips(bundle_path: Path) -> list[tuple[int, str]]:
    """包里所有 AnimationClip 的 (pathID, 名字)。"""
    dec = decoder_module()
    with dec.Bundle.open(str(bundle_path)) as b:
        return [(o.path_id, b.typetree(o.path_id).get("m_Name") or "")
                for o in b.by_class("AnimationClip")]


def choose_clip(clips: list[tuple[int, str]], key: str) -> int:
    """按名字或 pathID 选一条；名字重名时**必须报出全部候选**（不许静默挑一条）。"""
    if re.fullmatch(r"-?\d+", key):
        pid = int(key)
        if any(p == pid for p, _ in clips):
            return pid
        raise SystemExit(f"包里没有 pathID={pid} 的 clip")
    hits = [p for p, n in clips if n == key]
    if not hits:
        near = sorted({n for _, n in clips if key.lower() in n.lower()})[:8]
        raise SystemExit(f"包里没有名为 {key!r} 的 clip（相近的有 {near}）")
    if len(hits) > 1:
        print(f"[warn] 名字 {key!r} 有 {len(hits)} 条候选 {hits} —— "
              "取 pathID 最大的一条；要别的就传 pathID")
    return max(hits)


@dataclass
class ClipRequest:
    """给 Unity 的一张「肌肉请求单」。"""

    bundle: str
    pid: int
    name: str
    sample_rate: float
    start: float
    stop: float
    times: list[float]
    frames: list[list[float]]        # frames[t][k] = attribute (7+k) 在该时刻的值
    index_array_identity: bool
    delta_hits: str
    curve_kinds: str
    index_scheme: str                 # A_attr_minus_7 / B_index_array（由四元数模长自证选出）
    index_map: list[int]              # attribute 7..136 -> 全局曲线号 g
    index_detail: dict                # 两个候选的 worst_norm_err / unit_hits + verdict

    def human_poses(self) -> list[dict]:
        """把 130 条通道折成 C# HumanPose（bodyPosition / bodyRotation / muscles[95]）。

        goal（attribute 14..41）**装不进** HumanPose —— 原样留在 goals 里，供报告如实列出。
        """
        poses = []
        for k, t in enumerate(self.times):
            v = self.frames[k]
            pos = [v[i - ATTR_FIRST] for i in range(ATTR_ROOT_T, ATTR_ROOT_T + 3)]
            rot = [v[i - ATTR_FIRST] for i in range(ATTR_ROOT_Q, ATTR_ROOT_Q + 4)]
            muscles = [v[MUSCLE_ATTR[m] - ATTR_FIRST] for m in range(len(MUSCLE_NAMES))]
            goals: dict[str, dict[str, list[float]]] = {}
            for c in CHANNELS:
                if c.kind not in ("goalT", "goalQ"):
                    continue
                size = 3 if c.kind == "goalT" else 4
                slots = "xyz" if c.kind == "goalT" else "xyzw"
                bucket = goals.setdefault(c.goal, {}).setdefault(c.kind, [0.0] * size)
                bucket[slots.index(c.comp)] = v[c.attribute - ATTR_FIRST]
            poses.append({"time": t, "bodyPosition": pos, "bodyRotation": rot,
                          "muscles": muscles, "goals": goals})
        return poses

    def as_dict(self) -> dict:
        return {"bundle": self.bundle, "pid": self.pid, "name": self.name,
                "sampleRate": self.sample_rate, "start": self.start, "stop": self.stop,
                "times": self.times,
                "channels": [c.as_dict() for c in CHANNELS],
                "muscleNames": list(MUSCLE_NAMES),
                "muscleBones": [{"bone": b, "dof": d, "bodyBone": BONE_INDEX.get(b, -1)}
                                for b, d in MUSCLE_BONES],
                "rawFrames": self.frames,
                "humanPoses": self.human_poses(),
                "indexArrayIdentity": self.index_array_identity,
                "indexScheme": self.index_scheme,
                "indexMap": self.index_map,
                "indexDetail": self.index_detail,
                "deltaCheck": self.delta_hits,
                "curveKinds": self.curve_kinds,
                "unityPayload": self.unity_payload()}

    def unity_payload(self) -> dict:
        """Unity 侧吃的**扁平** JSON（JsonUtility 不认 Dictionary / 嵌套 List）。"""
        n_ch, n_t, n_m = N_CHANNELS, len(self.times), len(MUSCLE_NAMES)
        return {
            "clipName": self.name, "pid": self.pid,
            # ★ 2026-10-13 补（bakeline task-13，Lead 批准）：载荷里补上 clip 自己的采样率。
            #   ⚠ 只是**标签修复**：采样时刻一直是 Python 按真 sample_rate 算好放在 times 里传的，
            #     帧从来没错过；补这个键是为了让 C# 那边的产物不再写死一个假值。
            "sampleRate": self.sample_rate,
            "times": list(self.times),
            "muscleNames": list(MUSCLE_NAMES),
            "muscles": [self.frames[t][MUSCLE_ATTR[m] - ATTR_FIRST]
                        for t in range(n_t) for m in range(n_m)],
            "raw": [self.frames[t][k] for t in range(n_t) for k in range(n_ch)],
            "bodyPos": [self.frames[t][i - ATTR_FIRST]
                        for t in range(n_t) for i in range(ATTR_ROOT_T, ATTR_ROOT_T + 3)],
            "bodyRot": [self.frames[t][i - ATTR_FIRST]
                        for t in range(n_t) for i in range(ATTR_ROOT_Q, ATTR_ROOT_Q + 4)],
            "candidateNames": [c.name for c in CHANNELS],
            "candidateAttrs": [c.attribute for c in CHANNELS],
        }


# 5 个四元数组的起始 attribute（RootQ + 4 个 goalQ）；模长必须 == 1
# 位置 = 7k+3（k=0..4）=> 10 / 17 / 24 / 31 / 38，与
# 研究-模型与动画/07-clip解码/humanoid-decode.md §1 证据 B 的实测位置逐条对上
QUAT_GROUP_ATTRS: tuple[int, ...] = (ATTR_ROOT_Q, 17, 24, 31, 38)


def _quat_norm(clip, g: int, t: float) -> float | None:
    if g < 0 or g + 3 >= clip.n_curves:
        return None
    v = [clip.curves[g + i].value(t) for i in range(4)]
    return math.sqrt(sum(x * x for x in v))


def mapping_verdict(err_a: float, err_b: float, identity: bool,
                    tol: float = 1e-3) -> tuple[str, str]:
    """选哪个候选 + **这个选择有多可信**（⛔ 不许静默挑一个当已知用）。

    硬口径（本机 72 条读数定的）：m_IndexArray[7:137] 在 **72/72** 条纯人形 clip 上都是
    0..129 的排列，其中 **66 条不是恒等**（44 种不同排列）。候选 A 就是「当它是恒等」，
    ⇒ 非恒等时 A **构造上就错**，不是"哪个更准"的问题。

    于是：
      * 恒等      -> A（此时 A 与 B 逐位相同）
      * 非恒等    -> B（Unity 源码 GetHumanPose 的取值法：values[m_IndexArray[curveIndex]]）
    可信度：
      settled    = 选中的那个 worst_norm_err < tol（四元数模长真的 1）
      unverified = 没到 1.0。⚠ **这不等于映射错** —— Unity 的 GetHumanPose 做的是
                   normalize(load(value))，即**存的四元数本来就不必是单位长**，
                   所以"模长==1"这把尺子只能证伪、不能证实。要证实只有 L1（Unity .anim YAML）。
    """
    if identity:
        scheme = "A_attr_minus_7"
        return scheme, ("settled" if err_a < tol else "unverified")
    # ★ 2026-10-13 改（bakeline task-13，Lead 批准）：恒等时仍走 A；**非恒等一律走 B**。
    #   原来这里是 err_b < tol or err_b <= err_a 才走 B，**否则回落到 A** ——
    #   与本文件头以及本函数自己的 docstring（「恒等 -> A · 非恒等 -> B」）自相矛盾：
    #   A 就是「当它是恒等」，非恒等时**构造上就错**，不是"哪个更准"的问题。
    #   而且判据本身也不该用 err_b：那把尺子取的是「连续 4 连窗」，
    #   非恒等时同一根骨的 4 个分量本来就落在不连续的全局号上 ⇒ 它**结构上判不了**（文件头已写明）。
    #   实测证据（_work/bakeline/probe_quat_norm.py，86 条人形，按各自真实分量位置取值）：
    #     A 最大 |n-1| = 2.068752（79/80 条非恒等过不去）· B 最大 |n-1| = 0.026878。
    return "B_index_array", ("settled" if err_b < tol else "unverified")


def resolve_curve_index(clip) -> tuple[str, list[int], dict]:
    """定 attribute(7..136) -> 全局曲线号 g 的映射；**自证**，不循环。

    两个候选：
      A = attribute - 7          （解码文档 §4 里那句"纯人形 clip 上的巧合"）
      B = m_IndexArray[attribute]（解码文档 §4 里说的"正解"）

    判据（数据自己说的）：RootQ + 4 个 goalQ 共 **5 个四元数**，模长必须 == 1.000000，
    而且 4 个分量在全局曲线号上必须**连续**。取 5 个都过的那个候选。
    两个都过 / 都不过 -> 返回 A 并标注（不许静默挑一个）。
    """
    ts = [clip.start_time, (clip.start_time + clip.stop_time) / 2.0, clip.stop_time]
    detail: dict = {}
    for label in ("A_attr_minus_7", "B_index_array"):
        worst, unit, breaks = 0.0, 0, 0
        for attr in QUAT_GROUP_ATTRS:
            gs = ([attr - ATTR_FIRST] if label.startswith("A")
                  else [int(clip.index_array[a]) if a < len(clip.index_array) else -1
                        for a in range(attr, attr + 4)])
            if gs != list(range(gs[0], gs[0] + 4)):
                breaks += 1
            for t in ts:
                n = _quat_norm(clip, gs[0], t)
                if n is None:
                    worst = 9.99
                    continue
                worst = max(worst, abs(n - 1.0))
                if abs(n - 1.0) < 1e-4:
                    unit += 1
        detail[label] = {"worst_norm_err": round(worst, 9), "unit_hits": unit,
                         "noncontiguous": breaks, "of": len(QUAT_GROUP_ATTRS) * len(ts)}
    err_a = detail["A_attr_minus_7"]["worst_norm_err"]
    err_b = detail["B_index_array"]["worst_norm_err"]
    ident = list(clip.index_array[ATTR_FIRST:ATTR_LAST + 1]) == list(range(N_CHANNELS))
    best, verdict = mapping_verdict(err_a, err_b, ident)
    detail["chosen"] = best
    detail["verdict"] = verdict
    g_map = [(int(clip.index_array[a]) if a < len(clip.index_array) else -1)
             if best.startswith("B") else a - ATTR_FIRST
             for a in range(ATTR_FIRST, ATTR_LAST + 1)]
    return best, g_map, detail


def read_request_from(bundle, bundle_path: Path, key: str,
                      times: list[float], frames_n: int) -> ClipRequest:
    """在**已开好**的包上读一条 clip 的 130 条肌肉曲线值（省得反复开关同一个大文件）。"""
    dec = decoder_module()
    clips = [(o.path_id, bundle.typetree(o.path_id).get("m_Name") or "")
             for o in bundle.by_class("AnimationClip")]
    pid = choose_clip(clips, key)
    clip = dec.decode_clip(bundle, pid, dec.load_pathmap())
    classify_bindings([dict(g) for g in clip.bindings])
    fps = clip.sample_rate or 24.0
    if not times:
        times = [clip.start_time + i / fps for i in range(frames_n)]
    times = [min(max(t, clip.start_time), clip.stop_time) for t in times]
    scheme, g_map, detail = resolve_curve_index(clip)
    rows = [[clip.curves[g_map[k]].value(t) if 0 <= g_map[k] < clip.n_curves else 0.0
             for k in range(N_CHANNELS)] for t in times]
    bad = clip.delta_check()
    kinds: dict[str, int] = {}
    for c in clip.curves:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    return ClipRequest(
        bundle=str(bundle_path), pid=pid, name=clip.name or key,
        sample_rate=float(fps), start=float(clip.start_time), stop=float(clip.stop_time),
        times=list(times), frames=rows,
        index_array_identity=(list(clip.index_array[ATTR_FIRST:ATTR_LAST + 1])
                              == list(range(N_CHANNELS))),
        delta_hits=f"{clip.n_curves - len(bad)}/{clip.n_curves}",
        curve_kinds=" ".join(f"{k}={v}" for k, v in sorted(kinds.items())),
        index_scheme=scheme, index_map=g_map, index_detail=detail)


def read_request(bundle_path: Path, key: str, times: list[float], frames_n: int) -> ClipRequest:
    """从**副本**包里读出一条 clip 的 130 条肌肉曲线值（自己开关包）。"""
    dec = decoder_module()
    with dec.Bundle.open(str(bundle_path)) as b:
        return read_request_from(b, bundle_path, key, times, frames_n)

# ---------------------------------------------------------------- L1 判据（重定义版）
def crc32_u32(name: str) -> int:
    """Unity 给**通用组件属性**曲线用的数字 attribute = zlib crc32（uint32）。

    证据两条，互相独立：
      * L1 哨兵实测（Unity 2022.3.62f3c1）：40 条手指名 **40/40** 命中；
      * 11-attribute表 §1.1 早先读数 2086281974 = crc32("m_IsActive")、
        3305885265 = crc32("m_Enabled")。
    反证：fnv1a（Animator.StringToHash 那一族）0/40 · Mono String.GetHashCode 0/40。
    """
    return zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF


def expected_l1_attribute(name: str, muscle_names: list[str]) -> int | None:
    """这条候选曲线名**应当**拿到的数字 attribute（不在表里 = None）。

    55 条身体 DoF 肌肉：下标 + 42；root/goal 那 35 条：按本表 7..41。
    ⚠ 手指 40 条（MuscleName[55..94]）**本来就不该有数字 attribute**。
    """
    if name in muscle_names:
        i = muscle_names.index(name)
        return i + 42 if i < 55 else None
    for c in CHANNELS:
        if c.name == name and c.kind in ("rootT", "rootQ", "goalT", "goalQ"):
            return c.attribute
    return None


def l1_verdict(pairs: list, muscle_names: list[str]) -> dict:
    """**L1 判据（2026-10-12 重定义；fail-closed，覆盖全部 130 条）。**

    每条曲线**必须**落进两类之一，出现第三类 ⇒ 整个判 FAIL（⛔ 不许忽略不认识的那几条）：
      * NUMERIC：attribute == 该名字应得的数字 attribute（肌肉 下标+42 · root+goal 7..41）
      * HASH   ：attribute == crc32(名字)（Unity 把手指名当普通组件属性那条路）

    ⚠ **为什么这不是放松**：原判据把「手指 40 条」算进分母，那是**范畴错误** ——
    那 40 条**根本没有 attribute 映射**（Unity 不把它们当肌肉曲线）。新判据
    **覆盖全部 130 条**且**不允许第三类**：分母没变小、出口更严 ⇒ 强度只增不减。
    """
    numeric, hashed, third = [], [], []
    for name, attr in pairs:
        n, a = str(name), int(attr)
        exp = expected_l1_attribute(n, muscle_names)
        if exp is not None and a == exp:
            numeric.append((n, a))
        elif a == crc32_u32(n):
            hashed.append((n, a))
        else:
            third.append({"name": n, "attribute": a, "expected": exp, "crc32": crc32_u32(n)})
    total = len(numeric) + len(hashed) + len(third)
    ok = (total == N_CHANNELS) and not third
    return {"verdict": "L1_OK" if ok else "L1_FAIL", "total": total,
            "numeric": len(numeric), "hashed": len(hashed),
            "third_class": third, "numericNames": numeric, "hashedNames": hashed}


# ---------------------------------------------------------------- Unity 侧
def unity_processes() -> list[str]:
    """Unity 进程清单（跑前跑后的残留检查都用它）。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Unity.exe", "/NH"],
                             capture_output=True, text=True, timeout=60, check=False).stdout
    except Exception as exc:
        return [f"<tasklist 失败: {exc}>"]
    return [ln.strip() for ln in out.splitlines() if ln.strip() and "Unity.exe" in ln]


def run_unity(request_path: Path, log: Path, hold_seconds: float = 1800.0) -> tuple[bool, str]:
    """拿 unity 锁 -> 跑 batchmode -> 判据用**产物 mtime**（退出码不可靠，vfxline 实测）。

    ok 的口径 = bake 产物存在 且 mtime 比「启动时刻与旧 mtime 的较大者」新。
    """
    if not UNITY_EXE.exists():
        return False, f"Unity 可执行不存在：{UNITY_EXE}"
    if not PROJ.exists():
        return False, f"探针工程不存在：{PROJ}（需从 08-Unity真播/unity-play 复制模板）"
    running = unity_processes()
    if running:
        return False, f"已经有 Unity 在跑（单实例会静默交接 ⇒ 白跑）：{running[:3]}"
    sys.path.insert(0, str(LOCKS_DIR))
    lock = importlib.import_module("lock")
    base = re.sub(r"\.(muscles|unity_payload)\.json$", "", request_path.name)
    res = OUT / f"{base}.bake.json"
    before = res.stat().st_mtime if res.exists() else 0.0
    started = time.time()
    with lock.hold("unity", ttl=hold_seconds, wait=900.0, note="muscleline HumanPose bake"):
        # ⛔ 不加 -quit：探针在 executeMethod 里挂 EditorApplication.update，等编译完再干活；
        #    加了 -quit，Unity 在 executeMethod 返回后立刻退，tick 永远不跑
        #    （2026-10-12 实测：日志里一句 MUSCLELINE 都没有，
        #      却打印 Exiting batchmode successfully now!）。
        #    收尾由探针自己 EditorApplication.Exit()，所以这里必须有超时兜底。
        # ⚠ 实测（2026-10-12）：探针 EditorApplication.Exit() 之后 Unity **会卡在关闭阶段**
        #   （日志已出现 Input System Shutdown / Licensing 断连，
        #     进程却又活 14 分钟、烧 1690 s CPU）。
        #   => 判据不看它退没退：**轮询产物**，产物一稳就收工并杀掉它。
        # ⛔⛔ 此路不通，已弃用（2026-10-12，Lead 裁决）：这条 subprocess.Popen 启动路
        #   **既不产生 -logFile、也不起 Unity 进程**
        #   （实测：产物 0、日志文件不存在、tasklist 无 Unity），
        #   **原因未查明**。已经两次证明能跑通的是 PowerShell 那条：
        #     _work/muscleline/run_l1.ps1（L1 哨兵）·  _work/muscleline/run_l2.ps1（L2 烘制）
        #   —— 要跑 Unity 请走那两条；这里留着只为了不改函数签名（compat），⛔ 别再用它。
        proc = subprocess.Popen([str(UNITY_EXE), "-batchmode", "-projectPath", str(PROJ),
                                 "-executeMethod", UNITY_METHOD, "-muscleRequest",
                                 str(request_path), "-logFile", str(log)])
        deadline = time.time() + hold_seconds
        while time.time() < deadline:
            time.sleep(3.0)
            if proc.poll() is not None:
                break
            if res.exists() and res.stat().st_mtime > started:
                time.sleep(4.0)
                break
        if proc.poll() is None:
            print("[收工] 产物已出，杀掉卡在关闭阶段的 Unity")
            proc.kill()
            proc.wait(timeout=60)
    after = res.stat().st_mtime if res.exists() else 0.0
    floor = max(before, started - 5)
    if after <= floor:
        return False, (f"产物没更新（mtime {after:.1f} <= 起点 {floor:.1f}）"
                       f"⇒ 这轮白跑，看日志 {log}")
    left = unity_processes()
    if left:
        print(f"[warn] 跑完还有 Unity 残留进程：{left[:3]}")
    return True, f"产物已更新：{res}（mtime {after:.1f}）"


# ---------------------------------------------------------------- 比对尺子
def compare_bones(bake: dict, sampled: dict, exclude: tuple[str, ...] = ()) -> dict:
    """烘出的骨骼局部旋转 vs Animator 采样值 —— 逐骨最大偏差（四元数先符号对齐）。

    exclude 里的骨**不参与**比对并单独列出（L2 用 FINGER_BONES：尺子看不见它们，
    因为驱动它们的那 40 条手指肌肉没有数字 attribute ⇒ 造不出真值）。
    """
    bb, sb = bake.get("bones", {}), sampled.get("bones", {})
    skip = set(exclude)
    worst, worst_bone, missing = 0.0, "", []
    per_bone: dict[str, float] = {}
    excluded: list[str] = []
    for n in sorted(set(bb) | set(sb)):
        if n in skip:
            excluded.append(n)
            continue
        if n not in bb or n not in sb:
            missing.append(n)
            continue
        a, b = bb[n], sb[n]
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        s = -1.0 if dot < 0 else 1.0
        d = max(abs(x - s * y) for x, y in zip(a, b, strict=True))
        per_bone[n] = d
        if d > worst:
            worst, worst_bone = d, n
    return {"maxDev": worst, "worstBone": worst_bone, "nBones": len(per_bone),
            "missing": missing, "perBone": per_bone, "excluded": excluded}


def apply_bone_perturb(bake: dict, index: int, delta: float) -> str:
    """尺子正控：只把烘制侧某一根骨的旋转挪一点 ⇒ 比对**必须**变红。"""
    bones = bake.get("bones") or {}
    if not bones:
        return ""
    name = sorted(bones)[index % len(bones)]
    q = list(bones[name])
    q[0] += delta
    bones[name] = q
    return name


# ---------------------------------------------------------------- 入口
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="人形肌肉乙路：clip 的 130 条肌肉曲线 -> HumanPoseHandler -> 骨骼局部旋转",
        epilog="判据：逐骨 maxDev < 1e-5。负控：generic clip 报「这条不含肌肉」并 exit 2。")
    ap.add_argument("bundle", nargs="?", help="**副本** units 包路径（⛔ 不许是游戏目录）")
    ap.add_argument("clip", nargs="?", help="clip 名或 pathID")
    ap.add_argument("--list", action="store_true", help="只列出包里所有 clip 后退出")
    ap.add_argument("--l1-verdict", metavar="JSON",
                    help="对 L1 哨兵的 muscleline_l1.json 重算判据（fail-closed，覆盖 130 条）")
    ap.add_argument("--times", default="", help="逗号分隔的采样时刻（秒）；空 = 取前 N 帧")
    ap.add_argument("--frames", type=int, default=3, help="--times 为空时取几帧（默认 3）")
    ap.add_argument("--request", help="直接吃一张已落盘的 muscles.json（不再开包）")
    ap.add_argument("--run-unity", action="store_true", help="真的跑 Unity batchmode")
    ap.add_argument("--perturb", nargs=2, metavar=("I", "D"),
                    help="尺子正控：把烘制侧第 I 根骨的四元数 x 加 D（只扰一侧 ⇒ 必须变红）")
    ap.add_argument("--max-dev", type=float, default=1e-5, help="判据阈值（默认 1e-5）")
    args = ap.parse_args(argv)

    if args.l1_verdict:
        doc = json.loads(Path(args.l1_verdict).read_text(encoding="utf-8"))
        rep = l1_verdict(doc["nameToAttribute"], doc["muscleNames"])
        print(f"L1 判据（重定义·fail-closed）：{rep['verdict']}  总 {rep['total']} 条 = "
              f"数字段 {rep['numeric']} + 哈希段 {rep['hashed']} "
              f"+ 第三类 {len(rep['third_class'])}")
        for row in rep["third_class"][:8]:
            print(f"  第三类: {row}")
        print(f"  哨兵自报的**原始**判据 = {doc.get('verdict')}  <- 保留，不许抹掉历史")
        print(f"  哨兵的 FBX 边界读数：Unity 自己产出的 humanoid 曲线名 = "
              f"{doc.get('fbxHumanoidCurveNames')} 条")
        return 0 if rep["verdict"] == "L1_OK" else 8

    if args.list:
        if not args.bundle:
            print("--list 需要 <包>")
            return 6
        for pid, name in sorted(list_clips(Path(args.bundle)), key=lambda x: x[1]):
            print(f"{pid:>22}  {name}")
        return 0

    if args.request:
        req = json.loads(Path(args.request).read_text(encoding="utf-8"))
    else:
        if not args.bundle or not args.clip:
            print("要么给 <包> <clip>，要么给 --request <muscles.json>")
            return 6
        if "steamapps" in args.bundle.lower():
            print(f"[拒绝] {args.bundle} 在游戏目录里 —— 本单只许用副本")
            return 5
        times = [float(x) for x in args.times.split(",") if x.strip()] if args.times else []
        try:
            req = read_request(Path(args.bundle), args.clip, times, args.frames).as_dict()
        except NotHumanoidClipError as exc:
            print(f"[负控] 这条不含肌肉 -> {exc}")
            return 2

    OUT.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", str(req.get("name") or req.get("pid")))
    request_path = OUT / f"{stem}.muscles.json"
    request_path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    upath = OUT / f"{stem}.unity_payload.json"
    upath.write_text(json.dumps(req["unityPayload"], ensure_ascii=False), encoding="utf-8")
    print(f"请求单 -> {request_path}")
    print(f"Unity 载荷 -> {upath}")
    print(f"  clip={req.get('name')} pid={req.get('pid')} 帧={len(req.get('times') or [])} "
          f"采样率={req.get('sampleRate')} 通道={len(req.get('channels') or [])} "
          f"肌肉={len(req.get('muscleNames') or [])} 曲线={req.get('curveKinds')} "
          f"delta自证={req.get('deltaCheck')} 通道序={req.get('indexScheme')} "
          f"可信度={req.get('indexDetail', {}).get('verdict')} "
          f"序==恒等:{req.get('indexArrayIdentity')}")

    if not args.run_unity:
        print("（未跑 Unity —— 要跑加 --run-unity；Unity 那半按 Lead 令暂停中）")
        return 0

    log = OUT / f"{stem}.unity.log"
    ok, why = run_unity(upath, log)
    print(("Unity OK: " if ok else "Unity 失败: ") + why)
    if not ok:
        return 3
    bake = json.loads((OUT / f"{stem}.bake.json").read_text(encoding="utf-8"))
    sampled = bake.get("animatorSample") or {}
    baked = bake.get("humanPoseBake") or {}
    if args.perturb:
        i, d = int(args.perturb[0]), float(args.perturb[1])
        hit = apply_bone_perturb(baked, i, d)
        print(f"[尺子正控] 只扰烘制侧的 {hit}，四元数 x += {d:.4f}")
    cmp = compare_bones(baked, sampled, exclude=FINGER_BONES)
    print(f"逐骨最大偏差 maxDev = {cmp['maxDev']:.3e}（最差骨 {cmp['worstBone']}，"
          f"比了 {cmp['nBones']} 根，缺 {len(cmp['missing'])} 根）")
    print(f"  手指骨 {len(cmp['excluded'])} 根**不参与**"
          "（无真值：驱动它们的 40 条肌肉没有数字 attribute）")
    for n in cmp["missing"][:6]:
        print(f"  缺读数：{n}")
    verdict = "OK" if cmp["maxDev"] < args.max_dev else "FAIL"
    sign = "<" if verdict == "OK" else ">="
    print(f"VERDICT {verdict}  maxDev {cmp['maxDev']:.3e} {sign} {args.max_dev:.3e}")
    (OUT / f"{stem}.compare.json").write_text(
        json.dumps(cmp, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if verdict == "OK" else 4


if __name__ == "__main__":
    raise SystemExit(main())


