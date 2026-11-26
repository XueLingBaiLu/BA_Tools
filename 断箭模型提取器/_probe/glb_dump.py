import json, struct, sys, collections
p = sys.argv[1]
b = open(p,'rb').read()
magic, ver, total = struct.unpack_from('<III', b, 0)
off = 12; chunks = []
while off < total:
    clen, ctype = struct.unpack_from('<II', b, off); off += 8
    chunks.append((ctype, b[off:off+clen])); off += clen
gltf = json.loads(chunks[0][1].decode('utf-8'))
bin_ = chunks[1][1] if len(chunks)>1 else b''
print('file_bytes', len(b), 'glTF', gltf.get('asset'))
print('meshes', len(gltf.get('meshes',[])), 'nodes', len(gltf.get('nodes',[])), 'skins', len(gltf.get('skins',[])))
for i,s in enumerate(gltf.get('skins',[])):
    joints = s.get('joints',[])
    names = [gltf['nodes'][j].get('name') for j in joints]
    print('SKIN', i, 'joints', len(joints), 'skeleton', s.get('skeleton'), 'names', names[:6], '...' if len(names)>6 else '')
print('--- nodes with mesh ---')
for i,n in enumerate(gltf.get('nodes',[])):
    if 'mesh' in n:
        m = gltf['meshes'][n['mesh']]
        prims = m.get('primitives',[])
        attrs = sorted(set(k for pr in prims for k in pr.get('attributes',{})))
        # vertex count = accessor count of POSITION
        pos = prims[0]['attributes'].get('POSITION')
        cnt = gltf['accessors'][pos]['count'] if pos is not None else None
        print('NODE %-3d %-28s mesh=%-2d prims=%d verts=%-6s skin=%s attrs=%s' % (i, n.get('name'), n['mesh'], len(prims), cnt, n.get('skin','NONE'), ','.join(attrs)))
print('--- all node names ---')
print([n.get('name') for n in gltf.get('nodes',[])])