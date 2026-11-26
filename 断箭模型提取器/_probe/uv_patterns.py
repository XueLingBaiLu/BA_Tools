import json, collections
rows = json.load(open(r'D:\断箭模组制作\断箭模型提取器\_probe\mesh_channels2.json', encoding='utf-8'))
pat = collections.Counter()
for r in rows:
    names = [c['name'] for c in r['channels']]
    uvs = tuple(n for n in names if n.startswith('uv'))
    has_c = 'color' in names
    has_w = 'blend_weight' in names
    pat[(uvs, has_c, has_w)] += 1
for k, v in sorted(pat.items(), key=lambda x: -x[1]):
    print('%-4d uv=%-40s color=%-5s blend_weight=%s' % (v, k[0], k[1], k[2]))
print()
print('--- meshes with uv2..uv4 but missing some earlier set ---')
for r in rows:
    names = [c['name'] for c in r['channels']]
    uvs = [n for n in names if n.startswith('uv')]
    idx = [int(u[2:]) for u in uvs]
    if idx and idx != list(range(len(idx))):
        print('  GAP', r['name'], r['path_id'], r['vertex_count'], uvs)