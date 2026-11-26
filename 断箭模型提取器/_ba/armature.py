r"""补骨架（**bpy 那一层**）：`rig_plan` 的纯数据 -> 真骨架对象 ＋ 蒙皮件上的 Armature 修改器。

## 判据一行（真机，无头也能跑）

    & "D:\Blender\blender.exe" --background --factory-startup \
        --python _work\blender-test\armature_probe.py
      -> REST_MAX_DELTA ~1e-9（rest 姿势下一个顶点都不动）
         转 LR003 30° -> 只有 Chassis 上绑 LR003 的那些顶点动（其余件 0 个）
         转 body  30° -> Chassis 上绑 body 的 28,278 个顶点动

## 三条硬口径（代码里都 fail-closed）

1. **骨名必须逐字等于规划里的名字**。Blender 给重名会自动加 `.001`，而 Armature 修改器
   是按**名字**找骨的 ⇒ 加了后缀 = 那根骨**静默不生效**（不报错、只是那块不动）。
   ⇒ 建完骨**回读** `data.bones` 逐个核对，对不上抛 `RigBuildError`（⛔ 不静默带过）。
2. **rest 姿势保持恒等**：骨的头尾直接取规划里的数（= prefab 节点世界变换）⇒ 补了骨架之后
   模型**不会**挪位（合成夹具实测 2.98e-09）。`rest_max_delta` 就是量这一条的尺子。
3. **不改父链**：网格对象**不**挂到骨架对象下面。它们一旦被挂过去，prefab 的节点层级就毁了
   （"出"那条通道按对象父链还原节点树），而 Armature 修改器**不需要**父子关系照样生效。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import bpy

from . import rig_plan

__all__ = [
    "ARMATURE_MODIFIER_NAME",
    "RigBuildError",
    "attach_modifiers",
    "build_armature",
    "rest_max_delta",
]

#: 修改器的名字 —— `attach_modifiers` 按它判「已经挂过了」，别改（老场景里就叫这个）。
ARMATURE_MODIFIER_NAME = "Armature"


class RigBuildError(RuntimeError):
    """骨建出来跟规划对不上（名字被改 / 少了骨）—— fail-closed，⛔ 不许静默带过。"""


def build_armature(
    plan: rig_plan.RigPlan,
    *,
    name: str,
    collection: bpy.types.Collection,
    show_in_front: bool = True,
) -> bpy.types.Object:
    """按规划建一个骨架对象（链进 collection），返回它。

    ⛔ 建骨必须在 EDIT 模式（Blender 的 API 就这么设计的）；`--background` 下同样可用
    （实测 BLENDER 5.2.1：`MODE_BEFORE OBJECT -> MODE_EDIT_OK EDIT`）。

    :raises RigBuildError: 骨名被 Blender 改过 / 骨少了 / 父骨找不到。
    """
    if not plan.bones:
        raise RigBuildError("规划里一根骨都没有 —— 没什么可建的")

    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)

    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    obj.select_set(True)
    view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            for spec in plan.bones:
                bone = data.edit_bones.new(spec.name)
                bone.head = spec.head
                bone.tail = spec.tail
                bone.align_roll(spec.z_axis)
                bone.use_deform = spec.deform
            for spec in plan.bones:
                if not spec.parent:
                    continue
                child = data.edit_bones.get(spec.name)
                parent = data.edit_bones.get(spec.parent)
                if child is None or parent is None:
                    raise RigBuildError(
                        f"挂父子失败：{spec.name!r} -> {spec.parent!r}"
                        f"（child 在={child is not None} / parent 在={parent is not None}）"
                    )
                child.parent = parent

            # ★ 名字核对（本模块头第 1 条）：Blender 给重名自动加 .001，而名字就是合同。
            want = [spec.name for spec in plan.bones]
            got = [bone.name for bone in data.edit_bones]
            if got != want:
                drifted = [(w, g) for w, g in zip(want, got, strict=False) if w != g]
                raise RigBuildError(
                    f"骨名被 Blender 改过 {drifted[:4]}（要 {len(want)} 根、实得 {len(got)} 根）"
                    " —— 名字是合同（修改器按名字找骨），改过的那根会静默不生效"
                )
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        # 建到一半失败：把半成品从场景里撤掉，别留一个名字不对的骨架在用户眼前。
        bpy.data.objects.remove(obj, do_unlink=True)
        raise
    finally:
        view_layer.objects.active = previous_active

    obj.show_in_front = show_in_front  # 车体里的骨也看得见（用户在车里找骨）
    obj.select_set(False)
    return obj


def attach_modifiers(
    armature_object: bpy.types.Object,
    objects: Iterable[bpy.types.Object],
    *,
    modifier_name: str = ARMATURE_MODIFIER_NAME,
) -> tuple[str, ...]:
    """给这些网格对象挂 Armature 修改器（**幂等**：同一个名字已经有了就跳过）。

    :returns: 真挂上去的对象名（按传入顺序）。
    """
    touched: list[str] = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        existing = obj.modifiers.get(modifier_name)
        if existing is not None and existing.type == "ARMATURE":
            existing.object = armature_object
            continue
        modifier = obj.modifiers.new(name=modifier_name, type="ARMATURE")
        modifier.object = armature_object
        modifier.use_vertex_groups = True  # 权重只认顶点组（⛔ 不碰骨包络）
        modifier.use_bone_envelopes = False
        touched.append(obj.name)
    return tuple(touched)


def rest_max_delta(objects: Sequence[bpy.types.Object]) -> float:
    """rest 姿势下，求值后的顶点与原始顶点之间的**最大位移**（米）。

    这就是"补骨架不许挪模型"那把尺子：**必须 ~0**（合成夹具 2.98e-09；float32 级）。
    拿不到求值网格的对象（没有网格 / 没进视图层）跳过。
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    worst = 0.0
    for obj in objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
        except RuntimeError:
            continue
        try:
            if len(mesh.vertices) != len(obj.data.vertices):
                return float("inf")  # 顶点数都变了 —— 比"挪了一点"严重得多
            for base, moved in zip(obj.data.vertices, mesh.vertices, strict=True):
                delta = (moved.co - base.co).length
                if delta > worst:
                    worst = delta
        finally:
            evaluated.to_mesh_clear()
    return worst
