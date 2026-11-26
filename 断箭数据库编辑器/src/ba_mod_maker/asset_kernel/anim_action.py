r"""D 线 · clip → 「Action 规格」纯数据（**零宿主模块**）。

## 这一模块解决什么

产品已经有「三通道解码 + 求值 + delta 自证」（`anim_build`），但**没有**把曲线挂到骨上的那一层：
一条 clip 有 N 条曲线、M 条绑定，**哪条曲线是哪根骨的哪个分量** —— 研究线解过
（`clip_decode.py`），产品里一直没有入口。本模块就是这一层，输出**纯数据**
（不碰宿主模块、不碰盘），Blender 侧（`blender_addon/anim_entry.py`）拿它造真 Action。

    build_action_spec(...)      clip 字段树 -> AnimSpec（骨 / 通道 / 关键帧 + 全部读数）
    AnimSpec.summary()          一行读数（曲线数 / 时长 / 采样率 / 骨名）
    unity_to_blender_quat()     Unity 左手 Y-up -> Blender 右手 Z-up 的四元数换算

## 三条硬口径

1. **曲线号 → 分量**逐字照 `07-clip解码/humanoid-decode.md` §4：
   ① 肌肉绑定（`typeID=95, customType=8, attribute=7..136`）的全局曲线号 =
   `m_IndexArray[attribute]`；② 其余绑定按**绑定顺序**展开分量，依次填进**没人用的槽位**（升序）。
   研究线自证：14 条混合 clip 上「自由槽位数 == 通用分量数」**14/14 相等**；
   本模块另有一条正控 —— 曲线名必须与独立实现（研究线 `clip_decode.py` 的产物）**逐条相同**。
2. **绑定解析不出来 ⇔ 那条曲线进不了 Action**，而且**必须留在读数里**
   （`AnimSpec.stats.unresolved_crcs`），⛔ 不静默丢。`n_resolved == 0` 的 clip 就是「接不上」。
3. **四元数要归一化，并且把偏差量出来**：流式通道的三次多项式在中间点上**不保证模长 1**
   （实测 T-72B `T72B hatch`：61 帧里 55 帧偏离超过 1e-3）—— Unity 自己会在施加到
   Transform 时归一化，所以 Blender 侧也得归一化；但**读数要报**
   （`max_quat_norm_delta`），别让「归一化」变成静默改数。

## 坐标（⛔ 不是随手转的）

Unity 是**左手 / Y-up / Z-forward**，Blender 是**右手 / Z-up / Y-forward**，
两者差一个绕 X 轴 180° 的基变换：

    R = (w=0, x=1, y=0, z=0)          # 绕 X 轴 180°
    Q_blender = R * Q_unity * R^-1     # 展开即 (w, x, -y, -z)
    p_blender = (x, -z, y)

`unity_to_blender_quat` / `unity_to_blender_vec3` 就是这两个式子；
**旋转方向在换算后仍然正确**（相似变换保持旋转角），读数见 tests/test_anim_action.py。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.asset_kernel import anim_build, anim_path
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ATTR_BLENDER_CHANNEL",
    "ATTR_COMPONENTS",
    "ATTR_ROOT_ROTATION",
    "CHANNEL_EULER",
    "CHANNEL_POSITION",
    "CHANNEL_QUATERNION",
    "CHANNEL_SCALE",
    "CHANNEL_VALUES",
    "MUSCLE_ATTR_MAX",
    "MUSCLE_ATTR_MIN",
    "MUSCLE_CUSTOM_TYPE",
    "MUSCLE_TYPE_ID",
    "ROOT_ROTATION_COMPONENTS",
    "AnimBone",
    "AnimCurve",
    "AnimSpec",
    "AnimSpecError",
    "BindingStats",
    "MusclePoseCurve",
    "binding_stats",
    "build_action_spec",
    "components_of",
    "expand_curves",
    "is_muscle_binding",
    "unity_to_blender_quat",
    "unity_to_blender_vec3",
]

#: 人形肌肉绑定的两个判别字段（研究线 07-clip解码/humanoid-decode.md §4）。
MUSCLE_TYPE_ID = 95
MUSCLE_CUSTOM_TYPE = 8

#: attribute -> 分量名（同 `clip_decode.py::ATTR_COMPONENTS`）。
ATTR_COMPONENTS: dict[int, tuple[str, ...]] = {
    1: ("x", "y", "z"),           # localPosition
    2: ("x", "y", "z", "w"),      # localRotation（四元数）
    3: ("x", "y", "z"),           # localScale
    4: ("x", "y", "z"),           # localEulerAngles（⚠ customType=4）
}

CHANNEL_POSITION = "position"
CHANNEL_QUATERNION = "rotation_quaternion"
CHANNEL_SCALE = "scale"
CHANNEL_EULER = "euler"

#: (attribute, customType) -> Blender 的 pose bone 通道名。
ATTR_BLENDER_CHANNEL: dict[tuple[int, int], str] = {
    (1, 0): CHANNEL_POSITION,
    (2, 0): CHANNEL_QUATERNION,
    (3, 0): CHANNEL_SCALE,
    (4, 0): CHANNEL_EULER,
    (4, 4): CHANNEL_EULER,
}

CHANNEL_VALUES = (
    CHANNEL_POSITION, CHANNEL_QUATERNION, CHANNEL_SCALE, CHANNEL_EULER,
)

_COMPONENT_INDEX = {"x": 0, "y": 1, "z": 2, "w": 3}


# --------------------------------------------------------------------------- #
# 坐标换算（纯数学，无依赖）
# --------------------------------------------------------------------------- #
def unity_to_blender_quat(
    x: float, y: float, z: float, w: float
) -> tuple[float, float, float, float]:
    """Unity 左手 Y-up 四元数 -> Blender 右手 Z-up 四元数（顺序：w, x, y, z）。

    推导：两者差一个绕 X 轴 180° 的基变换 R=(0,1,0,0)（把 Unity 的 (x,y,z) 映到 (x,-z,y)），
    作用在旋转上是相似变换 `R·Q·R^-1`，展开即 `(w, x, -y, -z)`。
    ⛔ **不在这里归一化**：归一化会掩盖「多项式在中间点不保证模长 1」这条读数。
    """
    return (float(w), float(x), -float(y), -float(z))


def unity_to_blender_vec3(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Unity 的 (x, y, z) -> Blender 的 (x, -z, y)（同一条基变换作用在点上）。"""
    return (float(x), -float(z), float(y))


# --------------------------------------------------------------------------- #
# 读数与规格
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BindingStats:
    """一条 clip 的绑定解析读数（**「接不上」这条判据的落点**）。"""

    clip: str
    path_id: int
    n_bindings: int
    n_generic_bindings: int
    n_muscle_bindings: int
    n_resolved: int
    unresolved_crcs: tuple[int, ...]
    #: 每条 generic 绑定一行：`(绑定号, path_crc, 路径 | None, 口径)`（miss 的也在里面）。
    rows: tuple[tuple[int, int, str | None, str], ...] = ()

    def row_of(self, binding_index: int) -> tuple[int, str | None, str] | None:
        for bi, crc, path, kind in self.rows:
            if bi == binding_index:
                return (crc, path, kind)
        return None

    @property
    def resolved_ratio(self) -> float:
        if not self.n_generic_bindings:
            return 1.0
        return self.n_resolved / self.n_generic_bindings

    @property
    def partial(self) -> bool:
        return 0 < self.n_resolved < self.n_generic_bindings

    @property
    def dead(self) -> bool:
        """**接不上**：有 generic 绑定，但一条路径都解析不出来。"""
        return self.n_generic_bindings > 0 and self.n_resolved == 0

    @property
    def empty(self) -> bool:
        """**空 clip**：一条绑定都没有（真件 29 条，例 `T72B default`）—— 没东西可导。"""
        return self.n_bindings == 0

    def line(self) -> str:
        head = (f"{self.clip}（pathID={self.path_id}）：解析出 **{self.n_resolved} / "
                f"{self.n_generic_bindings}** 条 generic 绑定"
                f"（肌肉绑定 {self.n_muscle_bindings} 条不在此列）")
        if self.empty:
            return f"{self.clip}（pathID={self.path_id}）：**空 clip**（0 绑定 / 0 曲线）"
        if self.dead:
            return head + f" ⇒ **接不上**：{len(self.unresolved_crcs)} 个 path_crc 全没命中"
        if self.partial:
            return head + f" ⇒ ⚠ 部分命中，跳过的 path_crc：{list(self.unresolved_crcs)}"
        return head + " ⇒ 全部命中"


@dataclass(frozen=True, slots=True)
class AnimCurve:
    """一条曲线 → Action 上的一根 FCurve。"""

    #: Unity 的全局曲线号（`anim_build.ClipChannels` 的口径）。
    #: ★ **肌肉曲线那一族是合成出来的**（见 `MusclePoseCurve`）—— 它们**没有**全局曲线号
    #: ⇒ 记 `-(骨在解算结果里的下标) - 1`（**负数 = 不是从通道里读来的**；
    #: ⛔ 不拿 0 冒充一个真曲线号）。
    curve_index: int
    #: 绑定的序号（`m_ClipBindingConstant.genericBindings` 的下标）。
    binding_index: int
    #: 该绑定内的分量槽位（0 起）。
    slot: int
    #: crc32 路径哈希（⛔ 解析不出来时只留它，不拿它冒充骨名）。
    path_crc: int
    #: 解析出来的节点路径；`None` = 接不上。
    bone_path: str | None
    kind: str
    type_id: int
    custom_type: int
    attribute: int
    #: Blender 的通道名（position / rotation_quaternion / scale / euler）。
    channel: str | None
    component: str
    unit: str
    #: 采样出来的关键帧：((t, value), …)，t 是 **clip 内绝对秒**。
    keys: tuple[tuple[float, float], ...]

    @property
    def fcurve_key(self) -> tuple[str, str, int]:
        """Blender 侧定位一条 FCurve 的三元组：`(骨路径, 通道, 分量号)`。"""
        return (str(self.bone_path), str(self.channel),
                _COMPONENT_INDEX.get(self.component, 0))


@dataclass(frozen=True, slots=True)
class AnimBone:
    """一根骨在 Action 里占的曲线（按通道分组）。"""

    path: str
    path_crc: int
    curves: tuple[int, ...]
    channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnimSpec:
    """一条 clip 的「Action 规格」—— 纯数据，Blender 侧照着造 Action。"""

    name: str
    path_id: int
    sample_rate: float
    start_time: float
    stop_time: float
    wrap_mode: int
    #: 采样出的**时刻表**（秒，clip 内绝对时间）。
    times: tuple[float, ...]
    curves: tuple[AnimCurve, ...]
    bones: tuple[AnimBone, ...]
    stats: BindingStats
    #: 归一化前的四元数模长最大偏差（0 = 本来就在单位球上）。
    max_quat_norm_delta: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def n_curves(self) -> int:
        return len(self.curves)

    @property
    def duration(self) -> float:
        return self.stop_time - self.start_time

    @property
    def n_samples(self) -> int:
        return len(self.times)

    def curve_at(self, bone_path: str, channel: str, component: str) -> AnimCurve | None:
        for c in self.curves:
            if c.bone_path == bone_path and c.channel == channel and c.component == component:
                return c
        return None

    def summary(self) -> tuple[str, ...]:
        bones = tuple(b.path for b in self.bones)
        return (
            f"Action 规格：{self.name} —— 曲线 **{self.n_curves}** 条 · "
            f"时长 **{self.duration:.4f}** s · 采样率 **{self.sample_rate:g}** Hz · "
            f"采样 **{self.n_samples}** 帧",
            f"    骨（{len(bones)} 根）：{list(bones)}",
            f"    {self.stats.line()}",
            f"    四元数归一化最大偏差 = {self.max_quat_norm_delta:.3e}",
            *tuple("    " + n for n in self.notes),
        )


# --------------------------------------------------------------------------- #
# 曲线号 ↔ 绑定的展开（逐字照 humanoid-decode.md §4）
# --------------------------------------------------------------------------- #
#: 肌肉 attribute 的合法区间（真件里 72 条纯人形 clip 用的就是这一段）。
MUSCLE_ATTR_MIN = 7
MUSCLE_ATTR_MAX = 136


def is_muscle_binding(binding: Mapping[str, Any]) -> bool:
    """是不是**人形肌肉**绑定（研究线 `clip_decode.py::Clip._is_muscle` 的口径）。"""
    return (
        int(binding["typeID"]) == MUSCLE_TYPE_ID
        and int(binding["customType"]) == MUSCLE_CUSTOM_TYPE
        and MUSCLE_ATTR_MIN <= int(binding["attribute"]) <= MUSCLE_ATTR_MAX
    )


def _is_muscle(binding: Mapping[str, Any]) -> bool:
    """本模块内部的短名字（= `is_muscle_binding`）。"""
    return is_muscle_binding(binding)


def components_of(binding: Mapping[str, Any]) -> tuple[str, ...]:
    """一条绑定展开成几个分量（研究线 `clip_decode.py::Clip._components` 的口径）。"""
    if int(binding["typeID"]) == MUSCLE_TYPE_ID:
        return ("m",)
    if int(binding["customType"]) == 0 and int(binding["attribute"]) in ATTR_COMPONENTS:
        return ATTR_COMPONENTS[int(binding["attribute"])]
    if int(binding["customType"]) == 4 and int(binding["attribute"]) == 4:
        return ATTR_COMPONENTS[4]
    return ("v",)


def expand_curves(
    bindings: Sequence[Mapping[str, Any]],
    index_array: Sequence[int],
    curve_total: int,
) -> tuple[tuple[int | None, int], ...]:
    """每条全局曲线号 -> `(绑定号, 分量槽)`（长度 = `curve_total`）。

    `binding_index is None` = 这条曲线**没有任何绑定认领它**（真件上不该出现，出现就是读偏了）。
    """
    rows: list[tuple[int | None, int]] = [(None, 0) for _ in range(curve_total)]
    filled: set[int] = set()

    # ① 肌肉绑定：全局号 = m_IndexArray[attribute]
    for bi, b in enumerate(bindings):
        if not _is_muscle(b):
            continue
        attribute = int(b["attribute"])
        gi = int(index_array[attribute]) if attribute < len(index_array) else -1
        if 0 <= gi < curve_total and gi not in filled:
            rows[gi] = (bi, 0)
            filled.add(gi)

    # ② 非肌肉绑定：按绑定顺序展开分量，填进升序的自由槽
    if filled:
        free = [i for i in range(curve_total) if i not in filled]
        k = 0
        for bi, b in enumerate(bindings):
            if _is_muscle(b):
                continue
            for slot, _name in enumerate(components_of(b)):
                if k < len(free):
                    rows[free[k]] = (bi, slot)
                    k += 1
    else:
        k = 0
        for bi, b in enumerate(bindings):
            for slot, _name in enumerate(components_of(b)):
                if k < curve_total:
                    rows[k] = (bi, slot)
                    k += 1
    return tuple(rows)


def binding_stats(
    clip_name: str,
    path_id: int,
    bindings: Sequence[Mapping[str, Any]],
    table: anim_path.PathTable,
    *,
    chain_source: Mapping[str, Any] | None = None,
) -> BindingStats:
    """一条 clip 的绑定解析读数（**「接不上」这条判据的落点**）。"""
    n_muscle = 0
    resolved = 0
    unresolved: list[int] = []
    rows: list[tuple[int, int, str | None, str]] = []
    n_generic = 0
    for bi, b in enumerate(bindings):
        if _is_muscle(b):
            n_muscle += 1
            continue
        n_generic += 1
        crc = int(b["path"]) & 0xFFFFFFFF
        hit = table.explain(crc, chain_source)
        rows.append((bi, crc, hit.path, hit.kind))
        if hit.ok:
            resolved += 1
        else:
            unresolved.append(crc)
    return BindingStats(
        clip=clip_name, path_id=int(path_id), n_bindings=len(bindings),
        n_generic_bindings=n_generic, n_muscle_bindings=n_muscle,
        n_resolved=resolved, unresolved_crcs=tuple(unresolved), rows=tuple(rows),
    )


# --------------------------------------------------------------------------- #
# 造规格
# --------------------------------------------------------------------------- #
class AnimSpecError(AssetError):
    """clip 接不上 / 字段不合规时抛它（⛔ 不静默产出一个空 Action）。"""


def _sample_times(start: float, stop: float, rate: float) -> tuple[float, ...]:
    if not rate > 0:
        raise AnimSpecError(f"采样率 {rate!r} 不合法", hint="m_SampleRate 必须为正")
    span = stop - start
    if span < 0:
        raise AnimSpecError(f"时长是负的：start={start!r} stop={stop!r}")
    steps = int(round(span * rate))
    if steps <= 0:
        return (float(start),)
    return tuple(start + i / rate for i in range(steps + 1))


def _quat_norm_delta(
    channels: anim_build.ClipChannels,
    expand: Sequence[tuple[int | None, int]],
    bindings: Sequence[Mapping[str, Any]],
    times: Sequence[float],
) -> float:
    """四元数**原始值**（未归一化）的模长最大偏差 —— 「要不要归一化」的读数。"""
    groups: dict[int, list[int]] = {}
    for gi, (bi, _slot) in enumerate(expand):
        if bi is None:
            continue
        b = bindings[bi]
        if int(b["attribute"]) != 2 or int(b["customType"]) != 0:
            continue
        groups.setdefault(bi, []).append(gi)
    worst = 0.0
    for gis in groups.values():
        if len(gis) != 4:
            continue
        for t in times:
            q = [channels.value(gi, t) for gi in gis]
            worst = max(worst, abs(math.sqrt(sum(v * v for v in q)) - 1.0))
    return worst


def _unit_of(channel: str) -> str:
    if channel == CHANNEL_POSITION:
        return "m"
    if channel == CHANNEL_EULER:
        return "deg"
    if channel == CHANNEL_QUATERNION:
        return "quat"
    return "1"


def _as_table(bones: Mapping[int, str]) -> anim_path.PathTable:
    """把 `{crc: 路径}` 包成一张最小表（`per_root` 只有一条合成的根）。"""
    return anim_path.PathTable(
        roots={"(inline)": 0},
        per_root={"(inline)": {str(v): int(k) for k, v in bones.items()}},
        by_hash={int(k): str(v) for k, v in bones.items()},
    )




# --------------------------------------------------------------------------- #
# ★ 人形肌肉曲线（Hips 之外那 52 根的姿势来源）
# --------------------------------------------------------------------------- #
#: clip 里**根旋转**那四个通道的 attribute（实测：`rawFrames[0][3:7]` 与
#: `humanPoses[0].bodyRotation` **逐位相同** ⇒ 单位元就是 Unity 的 (x, y, z, w)）。
ATTR_ROOT_ROTATION: tuple[int, int, int, int] = (10, 11, 12, 13)
#: 根旋转的四个分量顺序（Unity 的 `Quaternion` 字段序）。
ROOT_ROTATION_COMPONENTS: tuple[str, ...] = ("x", "y", "z", "w")


@dataclass(frozen=True, slots=True)
class MusclePoseCurve:
    """**人形肌肉曲线 → 骨骼旋转**这一层（`build_action_spec` 的可选输入）。

    为什么是**可选的输入**而不是自己读文件：这一层要两样本模块拿不到的东西 ——
    ① 单通道激励探针的通道参数（`muscle_solve.MuscleRig`）；
    ② `HumanTrait.GetMuscleDefaultMin/Max` 那 95 对上下限。
    两样都由**调用方**喂进来，本模块只负责「按 attribute 取肌肉值 → 解算 → 合成四元数曲线」。
    ⛔ 本模块**不猜**它们的来源，也**不碰盘**。

    :param channel_names: `attribute -> 肌肉名`（真件里就是 `Spine Front-Back` 那一族）。
    :param muscle_index: 肌肉名 -> `HumanTrait` 的 0..94 下标（⛔ 不是用眼睛数的，是查表）。
    :param solve: `(肌肉值 dict, 根旋转四元数 | None) -> {骨名: (x, y, z, w)}`；
        真实现是 `muscle_solve.solve_pose_swing`（⭐ 该模块的 `base` 四元数就是这个分量序）。
    :param unit_to_bone: 解算出来的骨名 -> Action 上的骨路径（两套命名空间不一致时用它桥；
        不给就同名）。

    ## 读数（三条，`notes` 里逐条印出来 —— ⛔ 不许静默丢）

    * `unmapped` —— **有肌肉绑定但名字对不上表**的 attribute（那几条会静默变成 0 ⇒ 必须报）；
    * `unmatched` —— 解算结果里的骨名在 `unit_to_bone` 的值域里**找不到**（会整根丢掉）；
    * `unnamed_bones` —— 解算结果里**没有名字**的骨（也整根丢）。
    """

    channel_names: Mapping[int, str]
    muscle_index: Mapping[str, int]
    solve: Any
    n_muscles: int = 95
    unit_to_bone: Mapping[str, str] | None = None
    root_rotation_attributes: tuple[int, int, int, int] = ATTR_ROOT_ROTATION
    label: str = "人形肌肉"

    def muscle_of(self, attribute: int) -> int | None:
        """这个 attribute 是哪条肌肉（名字查不到 -> `None`，⛔ 不猜下标）。"""
        name = self.channel_names.get(int(attribute))
        if name is None:
            return None
        index = self.muscle_index.get(str(name))
        return None if index is None else int(index)


def _solved_quats(
    channels: Any,
    attribute_of: Mapping[int, int],
    muscles: MusclePoseCurve,
    times: Sequence[float],
    notes: list[str],
) -> tuple[dict[str, list[tuple[float, tuple[float, float, float, float]]]], tuple[str, ...]]:
    """逐时刻把 130 条通道解成 53 根骨的**局部旋转**。

    :return: `({骨路径: [(t, (x, y, z, w)), …]}, 逐根骨的名字清单)`；
        名字清单是**解算结果自己的顺序**（合成 `curve_index` 要用它的下标）。
    """
    attribute_to_global = dict(attribute_of)
    unmapped: list[int] = []
    for attribute in sorted(muscles.channel_names):
        # ⚠ 根旋转那几个 attribute（`ATTR_ROOT_ROTATION`）**本来就不是肌肉**，⛔ 别误报成
        #   「名字对不上肌肉表」—— 它们走 `body_rot` 那条路（下面 have_root）。
        if int(attribute) in (int(a) for a in muscles.root_rotation_attributes):
            continue
        if attribute not in attribute_to_global:
            continue
        if muscles.muscle_of(attribute) is None:
            unmapped.append(int(attribute))

    root_attrs = tuple(int(a) for a in muscles.root_rotation_attributes)
    have_root = all(a in attribute_to_global for a in root_attrs)
    out: dict[str, list[tuple[float, tuple[float, float, float, float]]]] = {}
    order: list[str] = []
    renamed: dict[str, str] = {}
    unmatched: list[str] = []
    for t in times:
        values: dict[int, float] = {}
        for attribute, global_index in attribute_to_global.items():
            index = muscles.muscle_of(attribute)
            if index is None:
                continue
            values[index] = float(channels.value(int(global_index), t))
        body_rot = None
        if have_root:
            body_rot = [float(channels.value(int(attribute_to_global[a]), t)) for a in root_attrs]
        solved = muscles.solve(values, body_rot)
        for unit_name, quat in solved.items():
            path = str(unit_name)
            if muscles.unit_to_bone is not None:
                mapped = muscles.unit_to_bone.get(path)
                if mapped is None:
                    if path not in unmatched:
                        unmatched.append(path)
                    continue
                renamed[path] = str(mapped)
                path = str(mapped)
            if path not in out:
                out[path] = []
                order.append(path)
            out[path].append((float(t), (float(quat[0]), float(quat[1]),
                                          float(quat[2]), float(quat[3]))))

    if unmapped:
        notes.append(f"⚠ 肌肉通道里有 {len(unmapped)} 条的**名字对不上肌肉表**（attribute="
                     f"{unmapped[:6]}）⇒ 那几条按 0 参与解算 —— ⛔ 别把它当「都对」")
    if unmatched:
        notes.append(f"⚠ 解算出来的 {len(unmatched)} 根骨**在骨名映射里找不到**（{unmatched[:6]}）"
                     "⇒ 这些骨整根没进 Action")
    return out, tuple(order)

def _muscle_curves(
    channels: Any,
    bindings: Sequence[Mapping[str, Any]],
    index_array: Sequence[int],
    muscles: MusclePoseCurve,
    times: Sequence[float],
    notes: list[str],
) -> list[AnimCurve]:
    """肌肉那一族 -> 每根骨 4 条 `rotation_quaternion` 曲线（raw 值，⛔ 不在这里换算坐标）。

    曲线号：肌肉绑定自己**没有**全局曲线号（真件上 `m_IndexArray[attribute]` 与曲线表无关），
    ⇒ 合成的 4 条记 `-2 - 4 * 骨下标 - 分量号`（**负数** = 不是从通道里读来的）。
    """
    if not muscles.channel_names:
        notes.append("⚠ 给了肌肉参数，但通道名表是空的 ⇒ 肌肉这一族没进 Action")
        return []
    attribute_of: dict[int, int] = {}
    for gi, (bi, _slot) in enumerate(expand_curves(bindings, index_array, channels.curve_total)):
        if bi is None:
            continue
        b = bindings[bi]
        if not _is_muscle(b):
            continue
        attribute_of[int(b["attribute"])] = int(gi)
    if not attribute_of:
        notes.append("⚠ 有肌肉绑定，但一条都没认领到全局曲线号 ⇒ 肌肉这一族没进 Action")
        return []

    solved, order = _solved_quats(channels, attribute_of, muscles, times, notes)
    out: list[AnimCurve] = []
    for bone_index, path in enumerate(order):
        rows = solved[path]
        for slot, component in enumerate(ROOT_ROTATION_COMPONENTS):
            out.append(AnimCurve(
                curve_index=-2 - 4 * bone_index - slot, binding_index=-1, slot=slot,
                path_crc=0, bone_path=path, kind=ROOT_ROTATION_COMPONENTS[slot],
                type_id=MUSCLE_TYPE_ID, custom_type=MUSCLE_CUSTOM_TYPE, attribute=-1,
                channel=CHANNEL_QUATERNION, component=component, unit="quat",
                keys=tuple((t, quat[slot]) for t, quat in rows),
            ))
    notes.append(
        f"   {muscles.label}曲线：**{len(order)} 根骨 x 4 分量 = {len(out)} 条**"
        f"（源曲线 {len(attribute_of)} 条 · 采样 {len(times)} 帧）；"
        "这批曲线是**合成**出来的（肌肉 -> 骨骼旋转），不是从通道里逐条读来的")
    return out


def build_action_spec(
    fields: Mapping[str, Any],
    bones: Mapping[int, str] | anim_path.PathTable,
    *,
    path_id: int = 0,
    sample_rate: float | None = None,
    allow_partial: bool = False,
    chain_source: Mapping[str, Any] | None = None,
    muscles: MusclePoseCurve | None = None,
) -> AnimSpec:
    """clip 字段树 -> `AnimSpec`。

    :param bones: `PathTable`，或已经解析好的 `{path_crc: 路径}` 映射。
    :param sample_rate: 采样率；不给就用 clip 自己声明的 `m_SampleRate`。
    :param allow_partial: 部分命中（`0 < resolved < total`）时是否继续；`False` ⇒ 抛。
    :param muscles: ★ **可选** —— 给了解算参数才把「人形肌肉」那一族也解成骨旋转；
        **不给 = 默认路径，行为与本参数出现之前逐位相同**（肌肉曲线一条都不出）。
    :raises AnimSpecError: ① 一条路径都解析不出来（**接不上**）；
        ② 部分命中且没开 `allow_partial`。
    """
    channels = anim_build.channels_of(fields)
    bindings = list(fields["m_ClipBindingConstant"]["genericBindings"])
    muscle = fields["m_MuscleClip"]
    index_array = [int(v) for v in muscle["m_IndexArray"]]
    name = str(fields["m_Name"])
    start = float(muscle["m_StartTime"])
    stop = float(muscle["m_StopTime"])
    rate = float(sample_rate if sample_rate is not None else fields["m_SampleRate"])

    table = bones if isinstance(bones, anim_path.PathTable) else _as_table(bones)
    stats = binding_stats(name, path_id, bindings, table, chain_source=chain_source)
    if stats.dead:
        raise AnimSpecError(
            stats.line(),
            hint="这个 prefab 的绑定路径在本包内一条都解析不出来"
                 "（研究线实测 US_LAV_M 就是这一类）—— Blender 侧接不到任何骨上；"
                 "⛔ 不给「看起来成功」的空 Action",
        )
    if stats.partial and not allow_partial:
        raise AnimSpecError(
            stats.line(),
            hint="部分命中要显式开 allow_partial（读数里会带 x/y 与跳过的 path_crc）",
        )
    if stats.empty:
        raise AnimSpecError(
            f"{stats.line()} ⇒ **空 clip**（0 绑定 / 0 曲线）",
            hint="真件里这是 Unity 烘出来的空壳（29 条，例 T72B default）—— "
                 "⛔ 不造一个空 Action 冒充成功",
        )

    expand = expand_curves(bindings, index_array, channels.curve_total)
    times = _sample_times(start, stop, rate)
    notes: list[str] = []
    curves: list[AnimCurve] = []
    for gi, (bi, slot) in enumerate(expand):
        if bi is None:
            continue
        b = bindings[bi]
        if _is_muscle(b):
            continue
        crc = int(b["path"]) & 0xFFFFFFFF
        path = table.resolve(crc)
        if path is None:
            continue
        comps = components_of(b)
        component = comps[slot] if slot < len(comps) else f"c{slot}"
        channel = ATTR_BLENDER_CHANNEL.get((int(b["attribute"]), int(b["customType"])))
        if channel is None:
            continue
        values = [channels.value(gi, t) for t in times]
        row = stats.row_of(bi)
        kind = row[2] if row is not None and row[1] is not None else anim_path.HIT_MISS
        curves.append(AnimCurve(
            curve_index=gi, binding_index=bi, slot=slot, path_crc=crc, bone_path=path,
            kind=kind, type_id=int(b["typeID"]), custom_type=int(b["customType"]),
            attribute=int(b["attribute"]), channel=channel, component=component,
            unit=_unit_of(channel), keys=tuple(zip(times, values, strict=True)),
        ))
    # ★ 人形肌肉那一族：**只有在调用方给了 `muscles` 时**才合成（不给 = 默认路径不变）
    if muscles is not None:
        curves.extend(_muscle_curves(
            channels, bindings, index_array, muscles, times, notes))

    by_bone: dict[str, list[AnimCurve]] = {}
    for c in curves:
        by_bone.setdefault(str(c.bone_path), []).append(c)
    bone_rows: list[AnimBone] = []
    for path, rows in by_bone.items():
        chans: list[str] = []
        for r in rows:
            if r.channel is not None and r.channel not in chans:
                chans.append(str(r.channel))
        bone_rows.append(AnimBone(
            path=path, path_crc=rows[0].path_crc,
            curves=tuple(r.curve_index for r in rows), channels=tuple(chans),
        ))

    unclaimed = [i for i, r in enumerate(expand) if r[0] is None]
    if unclaimed:
        notes.append(f"⚠ 有 {len(unclaimed)} 条曲线**没有**绑定认领"
                     f"（真件上不该出现）：{unclaimed[:8]}")
    dropped = sorted({
        (int(bindings[bi]["typeID"]), int(bindings[bi]["customType"]),
         int(bindings[bi]["attribute"]))
        for bi, _s in expand
        if bi is not None and not _is_muscle(bindings[bi]) and ATTR_BLENDER_CHANNEL.get(
            (int(bindings[bi]["attribute"]), int(bindings[bi]["customType"]))) is None
    })
    if dropped:
        notes.append(f"⚠ 有曲线绑的是**非 Transform 通道**（typeID/customType/attribute = "
                     f"{dropped[:6]}）—— 本研究线**没解**它们对应 Blender 的什么属性，"
                     "⛔ 本模块不猜、不导入")
    if stats.n_muscle_bindings and muscles is None:
        notes.append(f"⚠ 有 {stats.n_muscle_bindings} 条**人形肌肉**绑定 —— "
                     "**默认不导入**：要解它们得给解算参数（`build_action_spec(muscles=…)`），"
                     "面板上是「建肌肉 Action」那颗（`bamod.anim_build_muscle`）；"
                     "⛔ 给了参数之外的地方仍然一条都不出（generic 那部分照常导入）")

    return AnimSpec(
        name=name, path_id=int(path_id), sample_rate=rate, start_time=start, stop_time=stop,
        wrap_mode=int(fields["m_WrapMode"]), times=times, curves=tuple(curves),
        bones=tuple(bone_rows), stats=stats,
        max_quat_norm_delta=_quat_norm_delta(channels, expand, bindings, times),
        notes=tuple(notes),
    )

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   docs/研究-动画-三量.md（Action 规格 ← 那条 clip 的曲线 / 绑定解析）
# --------------------------------------------------------------------------- #
