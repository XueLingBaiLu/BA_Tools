import bpy, json
print('BLENDER', bpy.app.version_string)
for o in sorted(bpy.data.objects, key=lambda x: x.name):
    if o.type != 'MESH': continue
    me = o.data
    cols = [(a.name, a.domain, a.data_type) for a in me.color_attributes]
    print('MESH %-14s verts=%-6d uv=%-58s color=%-30s active=%-3s render=%-3s' % (
        o.name, len(me.vertices), [u.name for u in me.uv_layers], cols,
        me.color_attributes.active_color_index, me.color_attributes.render_color_index))