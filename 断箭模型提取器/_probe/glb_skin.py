import json, struct, sys, collections
p = sys.argv[1]
b = open(p,'rb').read()
magic, ver, total = struct.unpack_from('<III', b, 0)
off = 12; chunks=[]
while off < total:
    clen, ctype = struct.unpack_from('<II', b, off); off += 8
    chunks.append((ctype, b[off:off+clen])); off += clen
g = json.loads(chunks[0][1].decode('utf-8'))
BIN = chunks[1][1] if len(chunks)>1 else b''
CT = {5120:('b',1),5121:('B',1),5122:('h',2),5123:('H',2),5125:('I',4),5126:('f',4)}
NC = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}
def acc(i):
    a = g['accessors'][i]
    bv = g['bufferViews'][a['bufferView']]
    fmt, sz = CT[a['componentType']]; nc = NC[a['type']]
    base = bv.get('byteOffset',0) + a.get('byteOffset',0)
    stride = bv.get('byteStride') or (sz*nc)
    out = []
    for k in range(a['count']):
        o = base + k*stride
        out.append(struct.unpack_from('<'+fmt*nc, BIN, o))
    return out
# parent map
parent = {}
for i,n in enumerate(g['nodes']):
    for c in n.get('children',[]): parent[c]=i
def path(i):
    s=[]
    while i is not None:
        s.append(g['nodes'][i].get('name')); i = parent.get(i)
    return '/'.join(reversed(s))
skins = g.get('skins',[])
skin_names = [[g['nodes'][j].get('name') for j in s['joints']] for s in skins]
for si,s in enumerate(skins):
    print('SKIN%d joints=%d:'%(si,len(s['joints'])), skin_names[si])
print()
for i,n in enumerate(g['nodes']):
    if 'mesh' not in n: continue
    m = g['meshes'][n['mesh']]
    print('=== NODE %d %s  path=%s  skin=%s' % (i, n.get('name'), path(i), n.get('skin','NONE')))
    for pi,pr in enumerate(m['primitives']):
        at = pr['attributes']
        cnt = g['accessors'][at['POSITION']]['count']
        line = '   prim%d verts=%d attrs=%s' % (pi, cnt, sorted(at))
        print(line)
        if 'JOINTS_0' in at:
            J = acc(at['JOINTS_0']); W = acc(at['WEIGHTS_0'])
            hist = collections.Counter()
            for jv, wv in zip(J, W):
                for j,w in zip(jv,wv):
                    if w>0: hist[j]+=1
            tot = sum(hist.values()) or 1
            items = sorted(hist.items(), key=lambda x:-x[1])
            print('      joint-index histogram (vertex-slots):', items[:8], '...' if len(items)>8 else '', 'distinct=%d'%len(items), 'Wsum_range=%.4f..%.4f'%(min(sum(w) for w in W), max(sum(w) for w in W)))
            for si,sn in enumerate(skin_names):
                if max(hist) < len(sn):
                    named = [(j, sn[j], c) for j,c in items[:6]]
                    print('      if mapped to SKIN%d(%d joints):'%(si,len(sn)), named)