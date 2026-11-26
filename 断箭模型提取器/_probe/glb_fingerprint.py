import json, struct, sys, hashlib, collections
p = sys.argv[1]
b = open(p,'rb').read()
magic, ver, total = struct.unpack_from('<III', b, 0)
off = 12; chunks = []
while off < total:
    clen, ctype = struct.unpack_from('<II', b, off); off += 8
    chunks.append((ctype, b[off:off+clen])); off += clen
g = json.loads(chunks[0][1].decode('utf-8'))
BIN = chunks[1][1] if len(chunks) > 1 else b''
CT = {5120:('b',1),5121:('B',1),5122:('h',2),5123:('H',2),5125:('I',4),5126:('f',4)}
NC = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}
def raw(i):
    a = g['accessors'][i]; bv = g['bufferViews'][a['bufferView']]
    fmt, sz = CT[a['componentType']]; nc = NC[a['type']]
    base = bv.get('byteOffset',0) + a.get('byteOffset',0)
    stride = bv.get('byteStride') or (sz*nc)
    n = a['count']
    if stride == sz*nc:
        return a, BIN[base:base+n*sz*nc]
    out = bytearray()
    for k in range(n): out += BIN[base+k*stride: base+k*stride+sz*nc]
    return a, bytes(out)
def acc(i):
    a, blob = raw(i)
    fmt, sz = CT[a['componentType']]; nc = NC[a['type']]
    return [struct.unpack_from('<'+fmt*nc, blob, k*sz*nc) for k in range(a['count'])]
parent = {}
for i,n in enumerate(g['nodes']):
    for c in n.get('children',[]): parent[c] = i
def path(i):
    s = []
    while i is not None:
        s.append(g['nodes'][i].get('name')); i = parent.get(i)
    return '/'.join(reversed(s))
result = []
for i,n in enumerate(g['nodes']):
    if 'mesh' not in n: continue
    m = g['meshes'][n['mesh']]
    for pi, pr in enumerate(m['primitives']):
        at = pr['attributes']
        row = {'node': n.get('name'), 'node_index': i, 'path': path(i), 'prim': pi,
               'skin': n.get('skin'), 'attrs': sorted(at)}
        pos = acc(at['POSITION'])
        row['verts'] = len(pos)
        xs = [p[0] for p in pos]; ys = [p[1] for p in pos]; zs = [p[2] for p in pos]
        row['aabb'] = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]
        sha = hashlib.sha256()
        for key in ('POSITION','NORMAL','TEXCOORD_0','TEXCOORD_1','COLOR_0','TEXCOORD_2','TEXCOORD_3','TEXCOORD_4','JOINTS_0','WEIGHTS_0'):
            if key in at:
                a, blob = raw(at[key])
                row['sha_'+key] = hashlib.sha256(blob).hexdigest()[:16]
                row['fmt_'+key] = [a['componentType'], a['type'], a.get('normalized', False)]
        if 'WEIGHTS_0' in at:
            W = acc(at['WEIGHTS_0']); J = acc(at['JOINTS_0'])
            pairs = collections.Counter()
            for jv, wv in zip(J, W):
                for j, w in zip(jv, wv):
                    if w > 0: pairs[(int(j), round(float(w), 6))] += 1
            row['weights_multiset'] = sorted(pairs.items())
        result.append(row)
out = {'file': p, 'bytes': len(b), 'nodes_with_mesh': len(result), 'prims': result}
json.dump(out, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('WROTE', sys.argv[2], 'prims', len(result))