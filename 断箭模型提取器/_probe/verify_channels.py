import sys, json, struct, collections, hashlib, glob, os
sys.path.insert(0, r'D:\断箭模组制作\BA_Mod_Maker_2.0\tools')
sys.path.insert(0, r'D:\断箭模组制作\BA_Mod_Maker_2.0\src')
sys.path.insert(0, r'D:\断箭模组制作\断箭模型提取器')
from units_lock import units_lock
from ba_mod_maker.asset_kernel import unitypy_source as us
import extra_channels

REP = json.load(open(r'D:\断箭模组制作\断箭模型提取器\输出\RU_T72B_v2\report.json', encoding='utf-8'))
rows = REP.get('extra_channels') or []
print('report extra_channels rows:', len(rows))
for r in rows:
    print('  %-14s mesh=%-21s %-8s verts=%-6s color=%-5s uvs=%-42s blend_weight=%s %s' % (
        r['object'], r['mesh_id'], r['src_name'], r['verts'], r['color'], ','.join(r['uvs']) or '-', r['blend_weight'], r['note'] or ''))

_c = sorted(glob.glob(r'D:\Steam\steamapps\common\broken_arrow\BrokenArrow_Data\StreamingAssets\aa\PC\units_assets_all_*.bundle'))
BUNDLE = _c[0]
src_ref = {}
with units_lock('extractor-fixer-verify'):
    s = us.open_source(BUNDLE)
    try:
        res = s.stream_resolver()
        for r in rows:
            if not r['mesh_id']: continue
            tree = s.read_typetree(int(r['mesh_id']))
            d = extra_channels.decode_channels(tree, res)
            src_ref[r['object']] = {
                'mesh_id': int(r['mesh_id']), 'name': d['name'], 'vcount': d['vcount'],
                'color': [list(c) for c in (d['color'] or [])],
                'uvs': {n: [list(c) for c in rr] for n, rr in d['uv']},
                'blend_weight': d['blend_weight'], 'error': d['error'],
            }
    finally:
        s.close()
json.dump(src_ref, open(r'D:\断箭模组制作\断箭模型提取器\_probe\src_ref.json','w',encoding='utf-8'), ensure_ascii=False)
print('SRC_REF keys', list(src_ref))
for k, v in src_ref.items():
    print('  %-14s vcount=%-6s color=%s uv=%s blend_weight=%s err=%s' % (k, v['vcount'], len(v['color']) or 'none', list(v['uvs']), v['blend_weight'], v['error'] or ''))