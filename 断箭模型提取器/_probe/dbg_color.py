import json, struct, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
GLB = r'D:\断箭模组制作\断箭模型提取器\输出\RU_T72B_v2\model.glb'
REF = json.load(open(r'D:\断箭模组制作\断箭模型提取器\_probe\src_ref.json', encoding='utf-8'))
b = open(GLB,'rb').read(); off=12; chunks=[]
magic, ver, total = struct.unpack_from('<III', b, 0)
while off < total:
    clen, ctype = struct.unpack_from('<II', b, off); off += 8
    chunks.append((ctype, b[off:off+clen])); off += clen
g = json.loads(chunks[0][1].decode('utf-8')); BIN = chunks[1][1]
CT = {5126:('f',4),5121:('B',1),5123:('H',2)}
NC = {'VEC4':4,'VEC2':2}
def acc(i):
    a = g['accessors'][i]; bv = g['bufferViews'][a['bufferView']]
    fmt, sz = CT[a['componentType']]; nc = NC[a['type']]
    base = bv.get('byteOffset',0) + a.get('byteOffset',0); stride = bv.get('byteStride') or (sz*nc)
    return [struct.unpack_from('<'+fmt*nc, BIN, base+k*stride) for k in range(a['count'])], a
for i, n in enumerate(g['nodes']):
    if 'mesh' not in n: continue
    obj = n.get('name')
    if obj not in ('K1.001','turret'): continue
    for pi, pr in enumerate(g['meshes'][n['mesh']]['primitives']):
        at = pr['attributes']
        if 'COLOR_0' not in at: continue
        vals, a = acc(at['COLOR_0'])
        print(obj, 'prim', pi, 'componentType', a['componentType'], 'normalized', a.get('normalized'))
        print('  glb uniq:', sorted({tuple(round(float(x),4) for x in v) for v in vals})[:6])
        print('  src uniq:', sorted({tuple(round(float(x),4) for x in v) for v in REF[obj]['color']})[:6])