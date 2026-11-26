import json
rows = json.load(open(r'D:\断箭模组制作\断箭模型提取器\_probe\mesh_channels.json', encoding='utf-8'))
WANT_PIDS = [-5228944484459989664, 8777308271217901230, -986072742345099936, -6737183915188210336, 8865157332721284448, 2328690627837253022, -1782545009045654176, 8932300366259982560, 295762037444113246, 2175081331415725408, -48870935749446304, -101627207120243360, -4899069262969490080, 8926657160930080096]
by = {r['path_id']: r for r in rows}
for p in WANT_PIDS:
    r = by.get(p)
    if not r:
        print('MISSING', p); continue
    ch = [c[1] for c in r['channels']]
    print('%-14s pid=%-22s verts=%-6s ch=%s' % (r['name'], p, r['vertex_count'], ch))
print()
print('--- any mesh with verts in 9600..9800 or 8850..9000 or 30500..30700 ---')
for r in rows:
    v = r['vertex_count'] or 0
    if 9600 <= v <= 9800 or 8850 <= v <= 9000 or 30500 <= v <= 30700:
        print('%-14s pid=%-22s verts=%-6s ch=%s' % (r['name'], r['path_id'], v, [c[1] for c in r['channels']]))