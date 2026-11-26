import json, sys
base = json.load(open(r'D:\断箭模组制作\断箭模型提取器\_probe\fp_base.json', encoding='utf-8'))
v2 = json.load(open(r'D:\断箭模组制作\断箭模型提取器\_probe\fp_v2.json', encoding='utf-8'))
b = {(p['node'], p['prim']): p for p in base['prims']}
n = {(p['node'], p['prim']): p for p in v2['prims']}
print('nodes base/v2:', len(b), len(n), '| same keys:', set(b) == set(n))
CHECK = ['verts', 'aabb']
SHAS = ['sha_POSITION','sha_NORMAL','sha_TEXCOORD_0','sha_TEXCOORD_1','sha_JOINTS_0','sha_WEIGHTS_0']
print()
print('%-16s %-5s %-6s %-7s %s' % ('node','prim','verts','aabb','原有 attribute sha 逐条一致'))
allok = True
for k in sorted(b):
    p0, p1 = b[k], n[k]
    same_geo = (p0['verts'] == p1['verts']) and (p0['aabb'] == p1['aabb'])
    same_sha = all(p0.get(s) == p1.get(s) for s in SHAS)
    same_w = p0.get('weights_multiset') == p1.get('weights_multiset')
    newattrs = sorted(set(p1['attrs']) - set(p0['attrs']))
    gone = sorted(set(p0['attrs']) - set(p1['attrs']))
    ok = same_geo and same_sha and same_w and not gone
    allok = allok and ok
    print('%-16s %-5d %-6d %-7s %-6s weights=%-6s +%-42s -%s' % (
        k[0], k[1], p1['verts'], 'same' if same_geo else 'DIFF', 'same' if same_sha else 'DIFF',
        'same' if same_w else 'DIFF', ','.join(newattrs) or '(none)', ','.join(gone) or '(none)'))
print()
print('ALL_EXISTING_ATTRS_IDENTICAL', allok)