import json, struct, collections, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
GLB = r'D:\断箭模组制作\断箭模型提取器\输出\RU_T72B_v2\model.glb'
REF = json.load(open(r'D:\断箭模组制作\断箭模型提取器\_probe\src_ref.json', encoding='utf-8'))
b = open(GLB,'rb').read()
magic, ver, total = struct.unpack_from('<III', b, 0)
off = 12; chunks = []
while off < total:
    clen, ctype = struct.unpack_from('<II', b, off); off += 8
    chunks.append((ctype, b[off:off+clen])); off += clen
g = json.loads(chunks[0][1].decode('utf-8')); BIN = chunks[1][1]
CT = {5120:('b',1),5121:('B',1),5122:('h',2),5123:('H',2),5125:('I',4),5126:('f',4)}
NC = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}
def acc(i):
    a = g['accessors'][i]; bv = g['bufferViews'][a['bufferView']]
    fmt, sz = CT[a['componentType']]; nc = NC[a['type']]
    base = bv.get('byteOffset',0) + a.get('byteOffset',0)
    stride = bv.get('byteStride') or (sz*nc)
    norm = a.get('normalized', False)
    out = []
    for k in range(a['count']):
        v = struct.unpack_from('<'+fmt*nc, BIN, base + k*stride)
        if norm and a['componentType'] != 5126:
            div = 255.0 if a['componentType'] == 5121 else (65535.0 if a['componentType'] == 5123 else 1.0)
            v = tuple(float(x)/div for x in v)
        out.append(v)
    return out, a
print('%-14s %-4s %-18s %-7s %-7s %-8s %s' % ('node','prim','attr','fmt','glb_uniq','src_uniq','glb_not_in_src / src_not_in_glb / max|dmean|'))
bad = 0
for i, n in enumerate(g['nodes']):
    if 'mesh' not in n: continue
    obj = n.get('name')
    ref = REF.get(obj)
    for pi, pr in enumerate(g['meshes'][n['mesh']]['primitives']):
        at = pr['attributes']
        for attr, key in (('COLOR_0','color'), ('TEXCOORD_2','UVMap_TexCoord2'), ('TEXCOORD_3','UVMap_TexCoord3'), ('TEXCOORD_4','UVMap_TexCoord4')):
            if attr not in at: continue
            vals, a = acc(at[attr])
            fmt = '%sx%d%s' % ({5126:'f32',5121:'u8',5123:'u16'}.get(a['componentType'], a['componentType']), NC[a['type']], 'N' if a.get('normalized') else '')
            if ref is None:
                print('%-14s %-4d %-18s %-7s (no src ref)' % (obj, pi, attr, fmt)); continue
            src = ref['color'] if key == 'color' else ref['uvs'].get(key)
            if not src:
                print('%-14s %-4d %-18s %-7s BAD: glb has it, source does not' % (obj, pi, attr, fmt)); bad += 1; continue
            R = 3 if key == 'color' else 5   # COLOR_0 是 u16 归一化，量化到 1/65535
            # glTF 的 UV 原点在左上、Unity/Blender 在左下 ⇒ 导出器把 v 翻成 1-v
            # （核对口径：glb(u,v) == src(u, 1-v)）
            if key == 'color':
                gset = set(tuple(round(float(x), R) for x in v) for v in vals)
            else:
                gset = set((round(float(v[0]), R), round(1.0 - float(v[1]), R)) for v in vals)
            sset = set(tuple(round(float(x), R) for x in v) for v in src)
            notin = gset - sset
            only_src = sset - gset
            md = 0.0
            for d in range(len(vals[0])):
                if key != 'color' and d == 1:
                    mg = sum(1.0 - float(v[d]) for v in vals)/len(vals)
                else:
                    mg = sum(float(v[d]) for v in vals)/len(vals)
                ms = sum(float(v[d]) for v in src)/len(src)
                md = max(md, abs(mg-ms))
            if notin: bad += 1
            print('%-14s %-4d %-18s %-7s %-7d %-7d %-6d %-6d %.3e%s' % (obj, pi, attr, fmt, len(gset), len(sset), len(notin), len(only_src), md, '  ⛔' if notin else ''))
print()
print('BAD', bad)