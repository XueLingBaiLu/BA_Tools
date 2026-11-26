import sys, json, collections
sys.path.insert(0, r'D:\断箭模组制作\BA_Mod_Maker_2.0\tools')
sys.path.insert(0, r'D:\断箭模组制作\BA_Mod_Maker_2.0\src')
from units_lock import units_lock
from ba_mod_maker.asset_kernel import unitypy_source as us
import glob, os
_c = sorted(glob.glob(r'D:\Steam\steamapps\common\broken_arrow\BrokenArrow_Data\StreamingAssets\aa\PC\units_assets_all_*.bundle'))
assert len(_c) == 1, _c
BUNDLE = _c[0]
print('BUNDLE', os.path.basename(BUNDLE), os.path.getsize(BUNDLE))
WANT = ('Chassis','K1','K5','BaseMobility','T72BA','RU_TanktrakL','RU_TanktrakR','turret')
ATTR = {0:'position',1:'normal',2:'tangent',3:'color',4:'uv0',5:'uv1',6:'uv2',7:'uv3',8:'uv4',9:'uv5',12:'blend_weight',13:'blend_index'}
FMT = {0:'Float32',1:'Float16',2:'UNorm8',3:'SNorm8',4:'UNorm16',5:'SNorm16',6:'UInt8',7:'SInt8',8:'UInt16',9:'SInt16',10:'UInt32',11:'SInt32'}
rows = []
with units_lock('extractor-fixer-channels'):
    src = us.AssetSource(BUNDLE)
    try:
        ids = src.object_ids('Mesh')
        print('MESH_OBJECTS', len(ids))
        for pid in ids:
            try:
                t = src.read_typetree(pid)
            except Exception as e:
                continue
            name = t.get('m_Name')
            if name not in WANT: continue
            vd = t.get('m_VertexData') or {}
            chans = vd.get('m_Channels') or []
            used = []
            for i, ch in enumerate(chans):
                if not isinstance(ch, dict): continue
                dim = ch.get('dimension', 0)
                if not dim: continue
                used.append((i, ATTR.get(i, 'attr%d'%i), FMT.get(ch.get('format'), ch.get('format')), dim, ch.get('offset'), ch.get('stream')))
            rows.append({'path_id': pid, 'name': name, 'vertex_count': vd.get('m_VertexCount'), 'channels': used})
            print('MESH %-14s pid=%-22s verts=%-7s channels=%s' % (name, pid, vd.get('m_VertexCount'), [u[1]+':'+str(u[2])+'x'+str(u[3]) for u in used]))
    finally:
        src.close()
open(r'D:\断箭模组制作\断箭模型提取器\_probe\mesh_channels.json','w',encoding='utf-8').write(json.dumps(rows, ensure_ascii=False, indent=1))
print('ROWS', len(rows))