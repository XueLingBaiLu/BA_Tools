# -*- coding: utf-8 -*-
"""FBX 导出参数（**唯一一份**）：`global_scale` ＋「导哪些对象」。

两条都是实测出来的（2026-10-31），别再各写一套：

1. **global_scale**：导出器缺省 `apply_scale_options='FBX_SCALE_NONE'` 会把
   `units_blender_to_fbx_factor(scene)`（METRIC/scale_length=1 ⇒ 100）× `global_scale`(默认 1)
   乘进**顶层导出对象的节点矩阵**，而顶点数据不动。
   实测（`_work/fbx_probe.py` 直接读 model.fbx 二进制）：Armored / BaseArmor / Chassis /
   US_ACV_Armature / rotor 五个 Model 的 `Lcl Scaling=100`，`UnitScaleFactor=1.0`、
   几何 max|coord|=5.48 m（米制本来就对）⇒ Unity 里整车 100 倍（1000 米级）。
   反乘 1/100 ⇒ 100×0.01=1：节点归 1、UnitScaleFactor 仍 1.0、顶点与位移逐位不变。
   ⛔ 别改成 `FBX_SCALE_ALL`：它把 UnitScaleFactor 写成 100，Unity 会按 cm 再除 100（缩成 1/100）。

2. **选择集**：MESH/ARMATURE ＋「祖先链完整、且不与骨架骨骼同名」的空物体。
   为什么：动画行为（Torque 等）挂在空物体上；空物体默认不进 FBX ⇒ `_target=rot_blade_1`
   找不到节点，行为被整条跳过（实测 9 条里 7 条被跳过，旋翼不转）。
   ⛔ 不能一把导全部 EMPTY：47 个空物体里 **27 个与骨骼同名**（root/body/apparel/…），
   全导会在树里长出重名节点，`AnimConfig` 的 FindAll 直接判二义。
   ⛔ 也不能导「祖先里有同名空物体」的那些（dust_point_left 挂在 body 下）：父级不导出时
   它们会被提到顶层，而 `BuildFromZero` 只认 US_ACV 那棵子树 ⇒ 会被丢掉。
"""
from __future__ import annotations

__all__ = ["apply", "global_scale", "pick_objects"]


def global_scale(scene) -> float:
    """让「1 Blender 单位 = 1 FBX 单位」—— 抵消导出器缺省塞进顶层节点矩阵的 ×100。"""
    try:
        k = float(scene.unit_settings.scale_length or 1.0)
    except Exception:
        k = 1.0
    return 1.0 / (100.0 * k)


def pick_objects():
    """要导出的对象：**全部** MESH / ARMATURE / EMPTY。

    ★ 2026-11-02 实测**推翻**旧规则（旧规则：跳过与骨同名的 EMPTY ＋ 祖先链里出现骨名的 EMPTY）：
      · 本工程实测 50 个对象 = 1 骨架 + 3 网格 + **46 个 EMPTY**，而 `root` / `body` 这些节点名
        **同时就是骨名**（`BoneContainer.Bake` 就是按 `ToLower()=="root"/"body"` 找骨）；
      · 旧规则把 `root` 一跳过 ⇒ 它下面整棵对象树（body / Seats / Seat / Seat (1) / Entrances /
        death / dust_point_left / dust_point_right / exhaust_right / VFXPoint_OnSmokeAbility）**全部**
        进不了 FBX —— 二进制里连字符串都搜不到（实测）；
      · 后果：prefab 里只剩骨，`UnitBuilder.InitUnitData` 在 `Seats` 上取不到组件 ⇒
        **对局里单位根本不生成**（游戏日志 NullReferenceException @ UnitBuilder.Ecs.cs:221）。

    重名（同一名字既有骨、又有占位空物体）改在**装配侧**解决：
    `UnitBuild.MergePlaceholderEmptiesIntoBones` 把占位空节点并进同名骨（子节点搬过去、删占位），
    最终树与真件同形（真件 48 个节点：骨与标记点是同一批节点）。
    """
    import bpy

    return [ob for ob in bpy.data.objects if ob.type in ('MESH', 'ARMATURE', 'EMPTY')]

def apply(scene, bpy=None):
    """选中要导的对象，返回 `export_scene.fbx(**)` 的 kwargs。"""
    if bpy is None:
        import bpy as _bpy
        bpy = _bpy
    picked = pick_objects()
    for ob in bpy.data.objects:
        try:
            ob.select_set(False)
        except Exception:
            pass
    for ob in picked:
        try:
            ob.select_set(True)
        except Exception:
            pass
    if picked:
        try:
            bpy.context.view_layer.objects.active = picked[0]
        except Exception:
            pass
    # ⚠ `object_types` 必须**同时**放开 EMPTY：只选不放开类型，导出器照样把空物体滤掉
    #   （实测：只加 use_selection 时 FBX 字节数 1,556,412 一个都没变、MODELS 仍是 32）。
    # ★ 2026-11-01（用户实测纠正「车头还是插在地里」）：**不能**让 Blender 做 Z-up→Y-up 的
    #   节点级转换。实测缺省导出会给 US_ACV / Chassis / BaseArmor / Armored / US_ACV_Armature
    #   各塞一份 `Lcl Rotation=[-90,0,0]`（而 .blend 里这些对象 rotation 全是 0）⇒ 同一份转换
    #   沿层级被重复乘，实机整车歪。
    #   现在按 **Blender 原生 Z-up** 导出（`axis_up='Z'` → GlobalSettings.UpAxis=2，节点不带转换），
    #   由 Unity 导入器把 Z-up→Y-up **烘进网格**（`ModelImporter.bakeAxisConversion = true`）⇒
    #   出来的 prefab 与真件同构：**全部节点 identity + 几何 Y 朝上**。
    #   判据（实测 probe_zup.py）：带旋转的 Model 从 12 个（含 5 个 object 节点）降到 7 个，
    #   剩下的 7 个全是**骨骼**自己的旋转（root/apparel/Shield…），object 节点一个都没有。
    return dict(
        use_selection=True,
        object_types={'ARMATURE', 'MESH', 'EMPTY'},
        global_scale=global_scale(scene),
        axis_forward='Y',
        axis_up='Z',
    )