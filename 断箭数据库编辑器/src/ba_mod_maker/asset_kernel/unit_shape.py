r"""单位 prefab 的**节点名契约**（引擎按名字找节点；纯函数，零宿主绑定）。

## 这条路解决什么

《断箭》把一个单位 prefab 挂到场景之后，UnitBuilder 会**按名字**去 Find 一堆节点：
护甲 / 附加模型 / 机动 / 光学 / 能力 / 登机点，还有 AnimationManager 的骨骼容器
（轮子 / 悬挂 / 履带 / 后坐）。**名字对不上就静默失效** —— 模型照样显示，但轮子不转、
护甲不切、登机点为空。所以「自造单位」必须把名字按契约给对。

本模块就是那张契约表（口径与出处见下），给写侧（自造/改名之后）与读侧（从真件或
我们自己的包里读回节点名之后）**同一把尺子**。

## 契约表（逐条抄自反编译字节码；原文见 _work/audit-A-code.md §4）

每条 = 名字 + 引擎的精确条件 + 读点 + 缺了会怎样：

* **Chassis** —— rootTransform.Find("Chassis")（精确名）。
  读点 InitArmors / InitUnitExtraModelByName；缺 ⇒ **静默**（护甲/附加模型都不切换）。
* **BaseArmor** —— chassis.FindChildren("BaseArmor")（InitArmors）；缺 ⇒ 静默。
* **BaseMobility** —— 名字来自 CurrentMobility.ModelFileName（InitMobility）；缺 ⇒ 静默。
* **Entrances** —— rootTransform.Find("Entrances") ＋ **其上的 BoxCollider**（InitEntrances）。
  无 collider 只 LogWarning；无节点 ⇒ 空数组（静默）。
  登机点位 = 遍历它的子节点收 (子节点名, 位置)。
* **root / body** —— x.name.ToLower() == "root" / == "body"（**大小写不敏感**）。
  读点 BoneContainer.Bake。
* **车轮** —— x.name.Length > 3 && name.Substring(0,3) == "LR0"（RR0 同形，Wheels）；缺 ⇒ 轮子不转。
* **悬挂** —— x.name.Length > 3 && x.name.Substring(0,2) == "L0"（R0 同形，Suspension）。
* **履带** —— x.name.ToLower().Contains("trackl")（trackr 同形，Tracks）；缺 ⇒ HasTracks=false。
* **后坐** —— Assets.FindTransformBySubstring(root, "recoil_" + i)，即逐子节点 name.Contains(needle)
  （SetupWeaponComponents）；缺 ⇒ 无后坐（静默）。

⚠ 两条容易写错的边界（用例里都钉住了）：
1. 车轮/悬挂的**长度判据是 > 3**，而前缀分别是 3 / 2 个字符 ⇒ 名字 LR0（长 3）**不命中**、
   L00（长 3）**也不命中**；LR00 / L001 才命中；
2. root / body 是 **ToLower 之后全等**，不是前缀、不是 Contains；而 trackl / recoil_ 是
   **Contains**（子串，且 trackl 不区分大小写）。

## 边界

* 纯函数：输入是**名字的集合**（或带路径的名字），输出是匹配结果 + 缺件清单；
  不读包、不依赖 clone/builder。
* weapon_* / shell_spawn_* **不是**代码判据（ISIL 全库 grep 0 命中，见 audit-A §4 最后一行），
  它们是资产里序列化的 PPtr ⇒ 本模块**不检查**它们。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

__all__ = [
    "CHASSIS_NAME",
    "ENTRANCES_NAME",
    "ShapeExpect",
    "UnitShape",
    "read_unit_shape",
    "recoil_index",
    "shape_problems",
    "suspension_side",
    "track_side",
    "wheel_side",
]

CHASSIS_NAME = "Chassis"
BASE_ARMOR_NAME = "BaseArmor"
BASE_MOBILITY_NAME = "BaseMobility"
ENTRANCES_NAME = "Entrances"
ROOT_NAME = "root"
BODY_NAME = "body"

#: 车轮前缀（Substring(0, 3) 比的就是这三个字符）
WHEEL_PREFIXES: Mapping[str, str] = {"LR0": "left", "RR0": "right"}
#: 悬挂前缀（Substring(0, 2)）
SUSPENSION_PREFIXES: Mapping[str, str] = {"L0": "left", "R0": "right"}


def wheel_side(name: str) -> str | None:
    """车轮：len(name) > 3 且 name[:3] 在 {LR0, RR0} ⇒ "left"/"right"；否则 None。"""
    text = str(name)
    if len(text) <= 3:
        return None
    return WHEEL_PREFIXES.get(text[:3])


def suspension_side(name: str) -> str | None:
    """悬挂：len(name) > 3 且 name[:2] 在 {L0, R0} ⇒ "left"/"right"；否则 None。"""
    text = str(name)
    if len(text) <= 3:
        return None
    return SUSPENSION_PREFIXES.get(text[:2])


def track_side(name: str) -> str | None:
    """履带：小写后 Contains("trackl") / Contains("trackr")（不区分大小写）。"""
    low = str(name).lower()
    if "trackl" in low:
        return "left"
    if "trackr" in low:
        return "right"
    return None


def recoil_index(name: str, index: int) -> bool:
    """后坐：FindTransformBySubstring(root, "recoil_" + index) = 逐子节点 name.Contains(needle)。"""
    return f"recoil_{int(index)}" in str(name)


@dataclass(frozen=True, slots=True)
class UnitShape:
    """一棵 prefab 的节点名对这组契约的匹配结果（全部来自**名字**）。"""

    names: tuple[str, ...]
    root: bool = False
    body: bool = False
    chassis: bool = False
    base_armor: bool = False
    base_mobility: bool = False
    entrances: bool = False
    wheels: tuple[str, ...] = ()
    suspensions: tuple[str, ...] = ()
    tracks: tuple[str, ...] = ()
    recoils: tuple[tuple[int, str], ...] = field(default=())

    def left_wheels(self) -> tuple[str, ...]:
        return tuple(n for n in self.wheels if wheel_side(n) == "left")

    def right_wheels(self) -> tuple[str, ...]:
        return tuple(n for n in self.wheels if wheel_side(n) == "right")

    def summary(self) -> tuple[str, ...]:
        return (
            f"root={self.root} body={self.body} Chassis={self.chassis}"
            f" BaseArmor={self.base_armor} BaseMobility={self.base_mobility}"
            f" Entrances={self.entrances}",
            f"车轮 {len(self.left_wheels())}L/{len(self.right_wheels())}R（{list(self.wheels)}）",
            f"悬挂 {len(self.suspensions)}（{list(self.suspensions)}）· 履带 {list(self.tracks)}"
            f" · 后坐 {[i for i, _n in self.recoils]}",
        )


@dataclass(frozen=True, slots=True)
class ShapeExpect:
    """「这个单位**该**有什么」—— 由调用方按单位数据给（本模块不猜）。"""

    wheels: bool = True
    suspensions: bool = True
    tracks: bool = True
    entrances: bool = False
    recoil_indices: tuple[int, ...] = ()

    @property
    def tracked(self) -> bool:
        return self.tracks


def read_unit_shape(names: Iterable[str]) -> UnitShape:
    """把一串**节点名**读成契约匹配结果（可以是 "root/body/LR001" 这种路径，取末段）。"""
    plain: list[str] = []
    for raw_name in names:
        plain.append(str(raw_name).rsplit("/", 1)[-1])
    lower = {n.lower() for n in plain}
    wheels = tuple(n for n in plain if wheel_side(n) is not None)
    suspensions = tuple(n for n in plain if suspension_side(n) is not None)
    tracks = tuple(n for n in plain if track_side(n) is not None)
    recoils: list[tuple[int, str]] = []
    for index in range(64):
        for name in plain:
            if recoil_index(name, index):
                recoils.append((index, name))
                break
    return UnitShape(
        names=tuple(plain),
        root=ROOT_NAME in lower,
        body=BODY_NAME in lower,
        chassis=CHASSIS_NAME in plain,
        base_armor=BASE_ARMOR_NAME in plain,
        base_mobility=BASE_MOBILITY_NAME in plain,
        entrances=ENTRANCES_NAME in plain,
        wheels=wheels,
        suspensions=suspensions,
        tracks=tracks,
        recoils=tuple(recoils),
    )


def shape_problems(shape: UnitShape, expect: ShapeExpect) -> tuple[str, ...]:
    """按期望与契约给出**缺件清单**（空元组 = 这条契约满足）。"""
    out: list[str] = []
    if not shape.root:
        out.append('缺 root 节点（BoneContainer 按 ToLower()=="root" 找）')
    if not shape.body:
        out.append('缺 body 节点（BoneContainer 按 ToLower()=="body" 找）')
    if not shape.chassis:
        out.append(f"缺 {CHASSIS_NAME} 节点（护甲/附加模型/机动都按这个名字找）")
    if expect.wheels:
        if not shape.left_wheels():
            out.append("左侧车轮一个都没匹配到（LR0 前缀 + 长度 > 3）")
        if not shape.right_wheels():
            out.append("右侧车轮一个都没匹配到（RR0 前缀 + 长度 > 3）")
    if expect.suspensions:
        left = sum(1 for n in shape.suspensions if suspension_side(n) == "left")
        right = sum(1 for n in shape.suspensions if suspension_side(n) == "right")
        if not left:
            out.append("左侧悬挂一个都没匹配到（L0 前缀 + 长度 > 3）")
        if not right:
            out.append("右侧悬挂一个都没匹配到（R0 前缀 + 长度 > 3）")
    if expect.tracks:
        sides = {track_side(n) for n in shape.tracks}
        if "left" not in sides:
            out.append("左履带一个都没匹配到（名字里要含 trackl，不区分大小写）")
        if "right" not in sides:
            out.append("右履带一个都没匹配到（名字里要含 trackr，不区分大小写）")
    if expect.entrances and not shape.entrances:
        out.append(f"缺 {ENTRANCES_NAME} 节点（运输类要它 + 其上的 BoxCollider）")
    have = {index for index, _n in shape.recoils}
    for index in expect.recoil_indices:
        if index not in have:
            out.append(f"缺 recoil_{index} 节点（名字里要含 recoil_{index}）")
    return tuple(out)
