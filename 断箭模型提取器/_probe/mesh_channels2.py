import sys, json, re, glob, os
sys.path.insert(0, r'D:\断箭模组制作\BA_Mod_Maker_2.0\tools')
sys.path.insert(0, r'D:\断箭模组制作\BA_Mod_Maker_2.0\src')
from units_lock import units_lock
from ba_mod_maker.asset_kernel import unitypy_source as us
_c = sorted(glob.glob(r'D:\Steam\steamapps\common\broken_arrow\BrokenArrow_Data\StreamingAssets\aa\PC\units_assets_all_*.bundle'))
BUNDLE = _c[0]
PAT = re.compile(r'^(K1|K5|Kord|turret|Chassis|BaseMobility|T72BA|RU_TanktrakL|RU_TanktrakR)([. _].*)?$', re.I)
ATTR = {0:'position',1:'normal',2:'tangent',3:'color',4:'uv0',5:'uv1',6:'uv2',7:'uv3',8:'uv4',9:'uv5',12:'blend_weight',13:'blend_index'}
FMT = {0:'Float32',1:'Float16',2:'UNorm8',3:'SNorm8',4:'UNorm16',5:'SNorm16',6:'UInt8',7:'SInt8',8:'UInt16',9:'SInt16',10:'UInt32',11:'SInt32'}
rows = []
with units_lock('extractor-fixer-channels2'):
    src = us.AssetSource(BUNDLE)
    try:
        ids = src.object_ids('Mesh')
        for pid in ids:
            try: t = src.read_typetree(pid)
            except Exception: continue
            name = t.get('m_Name') or ''
            if not PAT.match(name): continue
            vd = t.get('m_VertexData') or {}
            chans = vd.get('m_Channels') or []
            used = []
            for i, ch in enumerate(chans):
                if not isinstance(ch, dict): continue
                if not ch.get('dimension'): continue
                used.append({'attr': i, 'name': ATTR.get(i,'attr%d'%i), 'fmt': FMT.get(ch.get('format'), ch.get('format')), 'dim': ch.get('dimension')})
            rows.append({'path_id': pid, 'name': name, 'vertex_count': vd.get('m_VertexCount'), 'channels': used})
    finally:
        src.close()
open(r'D:\断箭模组制作\断箭模型提取器\_probe\mesh_channels2.json','w',encoding='utf-8').write(json.dumps(rows, ensure_ascii=False, indent=1))
print('ROWS', len(rows))
for r in rows:
    v = r['vertex_count'] or 0
    if 9500 <= v <= 9900 or 8800 <= v <= 9000:
        print('%-16s pid=%-21s verts=%-6s ch=%s' % (r['name'], r['path_id'], v, [c['name'] for c in r['channels']]))