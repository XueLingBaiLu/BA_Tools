import json, sys, collections
path = r'D:\断箭模组制作\断箭模型提取器\输出\_animtest2\asset\prefab_graph.json'
g = json.load(open(path, encoding='utf-8'))
print('container_key', g['container_key'], 'objects', len(g['objects']))
kinds = collections.Counter(o['__type__'] for o in g['objects'])
print('kinds', dict(kinds))
# GameObject names
go = {}
for o in g['objects']:
    if o['__type__'] != 'GameObject': continue
    tt = o.get('typetree') or {}
    go[o['path_id']] = tt.get('m_Name')
for o in g['objects']:
    t = o['__type__']
    if t not in ('SkinnedMeshRenderer','MeshRenderer'): continue
    tt = o.get('typetree') or {}
    gop = (tt.get('m_GameObject') or {}).get('m_PathID')
    mesh = (tt.get('m_Mesh') or {}).get('m_PathID')
    bones = tt.get('m_Bones') or []
    bone_ids = [b.get('m_PathID') for b in bones if isinstance(b, dict)]
    print('%-20s name=%-26s mesh=%-22s bones=%d bone_file_ids=%s' % (t, go.get(gop), mesh, len(bone_ids), sorted({b.get('m_FileID') for b in bones if isinstance(b,dict)})))