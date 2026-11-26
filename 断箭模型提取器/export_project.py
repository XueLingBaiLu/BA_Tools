r"""从**改过的 .blend** 重新导出工程文件：`model.fbx` + `model.glb` + `anim_config.json`。

为什么需要它：提取器只做「游戏 -> 工程」那一个方向；改完 .blend 之后没人把它变回
`model.fbx`（资产管理器要的就是这个），本脚本补这一跳。参数与提取器里那段**逐项一致**
（`add_leaf_bones=False` / `object_types={ARMATURE,MESH}` / `mesh_smooth_type=FACE` / glb 只取节点名）。

用法：blender -b "<工程>.blend" --python export_project.py -- "<输出目录>"
    （不给输出目录＝与 .blend 同目录）
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import fbx_export_options  # noqa: E402  -- 同目录共用件（global_scale ＋ 选择集，唯一一份）


def main() -> int:
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    out = Path(argv[0]) if argv else Path(bpy.data.filepath).parent
    out.mkdir(parents=True, exist_ok=True)
    fbx = out / 'model.fbx'
    # ★ 2026-10-31（实测根因，别再改回去）：`apply_scale_options` 缺省 = 'FBX_SCALE_NONE'，
    #   导出器把 `units_blender_to_fbx_factor(scene)`（METRIC/scale_length=1 ⇒ 100）
    #   × `global_scale`(默认 1) 乘进**顶层导出对象的节点矩阵**，而顶点数据不动。
    #   实测（`_work/fbx_probe.py` 直接读 model.fbx 二进制）：Armored / BaseArmor /
    #   Chassis / US_ACV_Armature / rotor 五个 Model 的 `Lcl Scaling=100`，而
    #   `UnitScaleFactor=1.0`、几何 max|coord|=5.48 m（米制，本来就对）
    #   ⇒ Unity 里整车 100 倍（1000 米级），节点 TRS 与网格对不上。
    #   反乘 1/100 ⇒ 100×0.01=1：节点归 1、UnitScaleFactor 仍 1.0、顶点与位移逐位不变。
    #   ⛔ 别改成 `apply_scale_options='FBX_SCALE_ALL'`：它把 UnitScaleFactor 写成 100，
    #      Unity 会按 cm 再除 100 ⇒ 模型缩成 1/100（实测变体 C/D）。
    fbx_opts = fbx_export_options.apply(bpy.context.scene)
    bpy.ops.export_scene.fbx(
        filepath=str(fbx),
        add_leaf_bones=False,
        bake_anim=False,
        path_mode='COPY',
        embed_textures=False,
        use_armature_deform_only=False,
        mesh_smooth_type='FACE',
        **fbx_opts,
    )
    glb = out / 'model.glb'
    bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format='GLB',
        export_texcoords=True,
        export_normals=True,
        export_vertex_color='ACTIVE',
        export_all_vertex_colors=True,
        export_attributes=False,
        export_skins=True,
        export_image_format='NONE',
    )
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import anim_props
    rc = anim_props.cmd_dump(anim_props.load_schema(), str(out / 'anim_config.json'))
    print('EXPORT_PROJECT OK fbx=%d glb=%d cfg_rc=%d' % (fbx.stat().st_size, glb.stat().st_size, rc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())