"""export_materials.py —— 把 .blend 里的材质导成**我们自己的**材质数据（贴图 PNG + materials.json）。

为什么：Unity 造包读不了 Blender 材质；之前靠「从真件 prefab 抄材质槽」，那是补丁（用户 2026-11-02 明确否掉）。
这里把提取器已经在 .blend 里接好的贴图（全部 packed，2048²）落成 PNG，并写一份清单，
造包时用清单新建 Unity 材质并绑这些图 —— **不再碰真件 prefab，也不再碰 AssetRipper 导出的 .mat**。

用法：blender -b <model.blend> --python export_materials.py -- <输出目录>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


def role_of(image_name: str, node_name: str) -> str:
    n = image_name.lower()
    if n.endswith('_0.png') or '_0.png' in n:
        return 'normal'
    if n.endswith('_1.png') or '_1.png' in n:
        return 'mask'
    if 'basemap' in n:
        return 'base'
    if 'emm' in n or 'emissive' in n or 'emission' in n:
        return 'emission'
    if 'soap' in n:
        return 'base'
    return 'other'


def main() -> int:
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    out = Path(argv[0]) if argv else Path(bpy.data.filepath).parent
    tex_dir = out / 'textures'
    tex_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    mats = []
    # ★ 2026-11-02：只导**真的有面在用**的材质。实测 `US_AH-1Z Viper__…_Glass` 只挂在材质槽上、
    #   7858 个面**全部 material_index=0**（0 个面用它）⇒ 那是从 AH-1Z 抄过来的死槽，不该进包。
    used: set[str] = set()
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        for poly in ob.data.polygons:
            i = poly.material_index
            if i < len(ob.material_slots):
                mm = ob.material_slots[i].material
                if mm:
                    used.add(mm.name)
    for m in bpy.data.materials:
        if m.name not in used:
            continue
        if not m.use_nodes or not m.node_tree:
            continue
        slots: dict[str, str] = {}
        for n in m.node_tree.nodes:
            if n.type != 'TEX_IMAGE' or not getattr(n, 'image', None):
                continue
            im = n.image
            role = role_of(im.name, n.name)
            key = im.name
            if key not in saved:
                # ⛔ 文件名只由图像名决定，且**只保留一个 .png 后缀**（2026-11-02：首版出过 `x.png.png` 与双份文件）
                stem = im.name
                while stem.lower().endswith('.png'):
                    stem = stem[:-4]
                safe = ''.join(c if (c.isalnum() or c in '._-') else '_' for c in stem) + '.png'
                dst = tex_dir / safe
                try:
                    im.filepath_raw = str(dst)
                    im.file_format = 'PNG'
                    im.save()
                except Exception as exc:  # noqa: BLE001
                    print('MATEXPORT warn: %s 保存失败 %s: %s' % (im.name, type(exc).__name__, exc))
                    continue
                saved[key] = str(dst.relative_to(out).as_posix())
            keymap = {'base': 'baseMap', 'normal': 'normalMap', 'mask': 'maskMap', 'emission': 'emissionMap'}
            k2 = keymap.get(role)
            if k2 and k2 not in slots:
                slots[k2] = saved[key]
        # Principled 的 metallic / roughness（提取器按游戏数据设的）——造包时用来定 _Metallic / _Smoothness
        metallic, roughness = None, None
        if m.use_nodes and m.node_tree:
            for nd in m.node_tree.nodes:
                if nd.type != 'BSDF_PRINCIPLED':
                    continue
                for sock, key in (('Metallic', 'metallic'), ('Roughness', 'roughness')):
                    inp = nd.inputs.get(sock)
                    if inp is not None and not inp.is_linked:
                        v = float(inp.default_value)
                        if key == 'metallic':
                            metallic = round(v, 4)
                        else:
                            roughness = round(v, 4)
        if slots:
            mats.append({'name': m.name, 'textures': slots,
                         'metallic': -1.0 if metallic is None else metallic,
                         'roughness': -1.0 if roughness is None else roughness})
    payload = {'source_blend': bpy.data.filepath, 'materials': mats}
    (out / 'materials.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print('MATEXPORT materials=%d textures=%d out=%s' % (len(mats), len(saved), out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())