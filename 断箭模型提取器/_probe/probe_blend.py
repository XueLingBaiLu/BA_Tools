import bpy, sys, json
print('BLENDER', bpy.app.version_string)
# --- gltf exporter properties ---
rna = bpy.ops.export_scene.gltf.get_rna_type()
names = [p.identifier for p in rna.properties if p.identifier != 'rna_type']
print('GLTF_PROPS', json.dumps(names))
want = [n for n in names if any(k in n.lower() for k in ('uv','color','colour','texcoord','attribute','skin','def_bone'))]
for n in want:
    p = rna.properties[n]
    extra = ''
    if getattr(p, 'enum_items', None):
        extra = ' enum=' + ','.join(i.identifier for i in p.enum_items)
    print('  PROP %-32s type=%-8s default=%r%s' % (n, p.type, getattr(p,'default',None) if p.type!='ENUM' else getattr(p,'default',''), extra))
print('--- OBJECTS ---')
for o in sorted(bpy.data.objects, key=lambda x: x.name):
    if o.type != 'MESH': continue
    me = o.data
    mods = [(m.type, getattr(getattr(m,'object',None),'name',None)) for m in o.modifiers]
    uvs = [u.name for u in me.uv_layers]
    cols = [(a.name, a.domain, a.data_type) for a in getattr(me,'color_attributes',[])]
    attrs = [(a.name, a.domain, a.data_type) for a in getattr(me,'attributes',[])]
    # weight stats
    nvert = len(me.vertices)
    wned = 0; wmax = 0.0; gmax = 0
    gnames = [g.name for g in o.vertex_groups]
    for v in me.vertices:
        s = sum(g.weight for g in v.groups)
        if s > 0:
            wned += 1
            wmax = max(wmax, s)
    print('MESH %-24s verts=%-6d parent=%-18s mods=%s uvs=%s colors=%s vgroups=%d weighted_verts=%d wmax=%.4f' % (o.name, nvert, (o.parent.name if o.parent else ''), mods, uvs, cols, len(gnames), wned, wmax))
    print('     vgnames=%s' % (gnames,))
    print('     mesh_attrs=%s' % (attrs,))
print('--- ARMATURES ---')
for a in sorted([x for x in bpy.data.objects if x.type=='ARMATURE'], key=lambda x:x.name):
    print('ARM %-30s bones=%d %s' % (a.name, len(a.data.bones), [b.name for b in a.data.bones]))