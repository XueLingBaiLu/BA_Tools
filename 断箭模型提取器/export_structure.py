"""export_structure.py —— 从**真件 prefab** 导出 `structure.json`（造包唯一的"参照物"）。

为什么要有它（用户 2026-11-02 裁定）：**造包阶段不许再去游戏资产里抄任何东西**。
造包需要的三样参照 —— ① 节点路径 ② 节点本地 TRS ③ 每个节点挂哪些组件 ④ 蒙皮 bindpose ——
以前是 Unity 里现读真件 prefab；现在**提取器**一次性导成 `structure.json`，造包只读这个文件。

用法：python export_structure.py --prefab <真件.prefab> --out <输出目录>
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CLASS = {
    '1': 'GameObject', '4': 'Transform', '23': 'MeshRenderer', '33': 'MeshFilter',
    '95': 'Animator', '114': 'MonoBehaviour', '137': 'SkinnedMeshRenderer',
    '108': 'Light', '20': 'Camera', '64': 'MeshCollider', '65': 'BoxCollider',
    '136': 'CapsuleCollider', '135': 'SphereCollider', '222': 'CanvasRenderer',
}


def blocks(text: str):
    for m in re.finditer(r'--- !u!(\d+) &(-?\d+)\r?\n(.*?)(?=\r?\n--- |\Z)', text, re.S):
        yield m.group(1), m.group(2), m.group(3)


def vec(block: str, key: str):
    # 分组里不能用 [^,]+ ：它会连换行一起吞掉（首版就崩在这里）
    m = re.search(key + r': \{x: ([^,}\r\n]+), y: ([^,}\r\n]+), z: ([^,}\r\n]+)(?:, w: ([^}\r\n]+))?\}', block)
    if not m:
        return None
    vals = [float(x) for x in m.groups() if x is not None]
    return vals


def build_controller_index(assets_root: Path) -> dict[str, str]:
    """guid -> controller 资产路径（Assets 相对）。⛔ Animator 的 controller 不能靠 AddComponent 恢复。"""
    idx: dict[str, str] = {}
    for meta in assets_root.rglob('*.controller.meta'):
        try:
            text = meta.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        m = re.search(r'guid: ([0-9a-f]{32})', text)
        if m:
            rel = meta.with_suffix('').relative_to(assets_root)
            idx[m.group(1)] = 'Assets/' + str(rel).replace(chr(92), '/')
    return idx


def build_script_index(assets_root: Path) -> dict[str, str]:
    """guid -> 脚本类名（`.cs.meta` 的文件名去掉 .cs）。⛔ MonoBehaviour 只靠 class id 114 认不出类型。"""
    idx: dict[str, str] = {}
    for meta in assets_root.rglob('*.cs.meta'):
        try:
            text = meta.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        m = re.search(r'guid: ([0-9a-f]{32})', text)
        if m:
            idx[m.group(1)] = meta.stem[:-3] if meta.stem.endswith('.cs') else meta.stem
    return idx


def parse_prefab(p: Path, script_idx: dict[str, str] | None = None):
    text = p.read_text(encoding='utf-8', errors='ignore')
    go_name: dict[str, str] = {}
    go_comps: dict[str, list[str]] = {}
    tr: dict[str, dict] = {}
    comp_go: dict[str, str] = {}
    comp_class: dict[str, str] = {}
    comp_extra: dict[str, dict] = {}
    smrs: list[dict] = []
    for cls, fid, body in blocks(text):
        name = CLASS.get(cls, 'Class' + cls)
        if cls == '1':
            nm = re.search(r'm_Name: ([^\r\n]+)', body)
            go_name[fid] = nm.group(1).strip() if nm else ''
            go_comps.setdefault(fid, [])
        elif cls == '4':
            g = re.search(r'm_GameObject: \{fileID: (-?\d+)\}', body)
            fa = re.search(r'm_Father: \{fileID: (-?\d+)\}', body)
            tr[fid] = {
                'go': g.group(1) if g else None,
                'father': (fa.group(1) if fa else None),
                'pos': vec(body, 'm_LocalPosition') or [0.0, 0.0, 0.0],
                'rot': vec(body, 'm_LocalRotation') or [0.0, 0.0, 0.0, 1.0],
                'scl': vec(body, 'm_LocalScale') or [1.0, 1.0, 1.0],
            }
        else:
            g = re.search(r'm_GameObject: \{fileID: (-?\d+)\}', body)
            if g:
                comp_go[fid] = g.group(1)
                cls_name = name
                if name == 'MonoBehaviour' and script_idx:
                    sm = re.search(r'm_Script: \{fileID: -?\d+, guid: ([0-9a-f]{32})', body)
                    if sm:
                        cls_name = script_idx.get(sm.group(1), 'MonoBehaviour')
                comp_class[fid] = cls_name
                if cls_name == 'Animator':
                    cm = re.search(r'm_Controller: \{fileID: -?\d+, guid: ([0-9a-f]{32})', body)
                    if cm:
                        comp_extra[fid] = {'controller_guid': cm.group(1)}
                if name == 'SkinnedMeshRenderer':
                    mesh = re.search(r'm_Mesh: \{fileID: -?\d+, guid: ([0-9a-f]{32})', body)
                    bones = re.findall(r'- \{fileID: (-?\d+)\}', body.split('m_Bones:')[1].split('m_RootBone')[0]) if 'm_Bones:' in body else []
                    smrs.append({'go': g.group(1), 'mesh_guid': mesh.group(1) if mesh else '', 'bone_tr_ids': bones})
    go_extra: dict[str, dict] = {}
    for fid, g in comp_go.items():
        go_comps.setdefault(g, []).append(comp_class[fid])
        if fid in comp_extra:
            go_extra.setdefault(g, {}).update(comp_extra[fid])
    return go_name, go_comps, tr, smrs, go_extra


def path_of(tr_id: str, tr: dict, go_name: dict, cache: dict) -> str:
    if tr_id in cache:
        return cache[tr_id]
    t = tr.get(tr_id)
    if not t:
        return ''
    name = go_name.get(t['go'], '')
    father = t.get('father')
    p = (path_of(father, tr, go_name, cache) + '/' + name).strip('/') if father and father in tr else name
    cache[tr_id] = p
    return p


def guid_to_asset(assets_root: Path, guid: str) -> Path | None:
    mesh_dir = assets_root / 'Mesh'
    for root in ([mesh_dir] if mesh_dir.is_dir() else []) + [assets_root]:
        for meta in root.rglob('*.asset.meta'):
            try:
                if guid in meta.read_text(encoding='utf-8', errors='ignore'):
                    return meta.with_suffix('')
            except OSError:
                continue
    return None


def read_bindposes(mesh: Path):
    text = mesh.read_text(encoding='utf-8', errors='ignore')
    i = text.find('m_BindPose:')
    if i < 0:
        return []
    seg = text[i:]
    j = seg.find('m_BoneNameHashes:')
    if j > 0:
        seg = seg[:j]
    out = []
    for m in re.finditer(r'- e00: ([^\r\n]+)\r?\n\s+e01: ([^\r\n]+)\r?\n\s+e02: ([^\r\n]+)\r?\n\s+e03: ([^\r\n]+)\r?\n\s+e10: ([^\r\n]+)\r?\n\s+e11: ([^\r\n]+)\r?\n\s+e12: ([^\r\n]+)\r?\n\s+e13: ([^\r\n]+)\r?\n\s+e20: ([^\r\n]+)\r?\n\s+e21: ([^\r\n]+)\r?\n\s+e22: ([^\r\n]+)\r?\n\s+e23: ([^\r\n]+)\r?\n\s+e30: ([^\r\n]+)\r?\n\s+e31: ([^\r\n]+)\r?\n\s+e32: ([^\r\n]+)\r?\n\s+e33: ([^\r\n]+)', seg):
        out.append([float(g) for g in m.groups()])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefab', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--assets-root', default='')
    a = ap.parse_args()
    prefab = Path(a.prefab)
    out = Path(a.out)
    # prefab 在 <Assets>/Resources_moved/ModelPrefabs/<国>/<件>.prefab ⇒ Assets 在 parents[3]
    assets_root0 = Path(a.assets_root) if a.assets_root else Path(prefab).resolve().parents[3]
    script_idx = build_script_index(assets_root0) if assets_root0.is_dir() else {}
    print('脚本索引 %d 条' % len(script_idx))
    go_name, go_comps, tr, smrs, go_extra = parse_prefab(prefab, script_idx)
    ctrl_idx = build_controller_index(assets_root0) if assets_root0.is_dir() else {}
    print('controller 索引 %d 条' % len(ctrl_idx))
    cache: dict[str, str] = {}
    nodes = []
    tr_by_go = {v['go']: k for k, v in tr.items() if v.get('go')}
    for tr_id, t in tr.items():
        g = t.get('go')
        if not g:
            continue
        ex = go_extra.get(g) or {}
        cg = ex.get('controller_guid')
        nodes.append({
            'path': path_of(tr_id, tr, go_name, cache),
            'name': go_name.get(g, ''),
            'pos': t['pos'], 'rot': t['rot'], 'scl': t['scl'],
            'components': go_comps.get(g, []),
            'controller': ctrl_idx.get(cg, '') if cg else '',
        })
    # prefab 在 <Assets>/Resources_moved/ModelPrefabs/<国>/<件>.prefab ⇒ Assets 在 parents[3]（首版写成 2，于是找不到网格）
    assets_root = Path(a.assets_root) if a.assets_root else Path(prefab).resolve().parents[3]
    skins = []
    for s in smrs:
        asset = guid_to_asset(assets_root, s['mesh_guid']) if s['mesh_guid'] else None
        bps = read_bindposes(asset) if asset else []
        skins.append({
            'node_path': path_of(tr_by_go.get(s['go'], ''), tr, go_name, cache),
            'mesh_guid': s['mesh_guid'],
            'mesh_asset': str(asset) if asset else '',
            'bones': [path_of(b, tr, go_name, cache) for b in s['bone_tr_ids']],
            # ⛔ JsonUtility（Unity）不支持 float[][]，所以 bindpose 用**扁平数组**（每 16 个一组）
            'bindpose_stride': 16,
            'bindpose_count': len(bps),
            'bindposes_flat': [v for m in bps for v in m],
        })
    payload = {'prefab': str(prefab), 'root': go_name.get(next(iter(go_name), ''), prefab.stem),
               'nodes': nodes, 'skins': skins}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'structure.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print('STRUCT nodes=%d skins=%d bindposes=%s -> %s' % (
        len(nodes), len(skins), [len(s['bindposes']) for s in skins], out / 'structure.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())