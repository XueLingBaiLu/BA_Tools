r"""补顶点通道：源 Mesh 里有 `Color` / `TexCoord2..4`，而 importer 只建了 uv0/uv1。

为什么写在提取器里（而不是改 `blender_addon/importer.py` / `asset_kernel/mesh.py`）：
本单的写入范围只到 `断箭模型提取器/`。这里不改产品，只在导出前把**源里真的有的**那些
通道补进 Blender 网格，让 glTF 导出器把它们写成 `COLOR_0` / `TEXCOORD_2..4`。

口径三条（都能复算）：

1. **按 `mesh_id` 配**（`ImportReport.mesh_of`：对象名 -> 包里的 Mesh pathID），
   ⛔ 不按名字 / 顶点数猜 —— 同名同顶点数的两件在同一个包里就有（K1 / K1.001 / K1.002）。
2. **源里没有的通道不建** —— 空层会让「这块没有颜色」与「颜色是黑的」长得一模一样
   （同 `importer.uv_layer_plan` 的口径）。
3. **层序 = 通道号**：uv0 / uv1 已由 importer 建成 `UVMap` / `UVMap_Camo`，这里只往后接
   uv2/uv3/uv4 ⇒ glTF 的 `TEXCOORD_2/3/4` 与 Unity 的 `TexCoord2..4` 一一对应
   （实测全库有 uv2..uv4 的网格**都**同时有 uv0/uv1，不存在要补空层占位的缺口）。

读法（`m_Channels` 下标 = 属性号，见 `asset_kernel/mesh.py` 模块头）：
3 = Color · 4 = uv0 · 5 = uv1 · 6 = uv2 · 7 = uv3 · 8 = uv4 · 12 = blend_weight · 13 = blend_index。
"""

from __future__ import annotations

import dataclasses

from ba_mod_maker.asset_kernel import mesh as kmesh

#: 源里的颜色通道（Unity `VertexAttribute.Color`）。
ATTR_COLOR = 3
#: 源里的 blend_weight（Unity `VertexAttribute.BlendWeights`）—— 只读它来判断「这件到底能不能蒙皮」。
ATTR_BLEND_WEIGHT = 12
#: 要补的 UV：`(m_Channels 下标, Blender 图层名)`。顺序 = 层序 = glTF 的 TEXCOORD 序号。
EXTRA_UV = ((6, "UVMap_TexCoord2"), (7, "UVMap_TexCoord3"), (8, "UVMap_TexCoord4"))
#: Blender 里颜色属性的名字（导出器 `export_vertex_color="ACTIVE"` 取的就是当前激活那条）。
COLOR_NAME = "Color"


@dataclasses.dataclass(slots=True)
class Row:
    """一个网格对象的通道读数（既是日志也是 report.json 的一行）。"""

    object: str
    mesh_id: int
    src_name: str
    verts: int
    color: bool = False
    uvs: tuple[str, ...] = ()
    blend_weight: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    def line(self) -> str:
        tail = self.note or ("权重通道：有" if self.blend_weight else "权重通道：无（这件本来就不蒙皮）")
        return ("%s ← mesh %s（%s，%d 顶点）· color=%s · 补 UV=%s · %s"
                % (self.object, self.mesh_id, self.src_name, self.verts,
                   "有" if self.color else "无",
                   ",".join(self.uvs) if self.uvs else "无", tail))


def decode_channels(tree: object, resolver=None) -> dict:
    """把一个 Mesh 的 typetree 解成「我们要补的那几条通道」。

    :returns: `{"vcount": int, "color": rows|None, "uv": [(name, rows), …],
               "blend_weight": bool, "name": str, "error": str}`
    """
    out: dict = {"vcount": 0, "color": None, "uv": [], "blend_weight": False, "name": "", "error": ""}
    if not isinstance(tree, dict):
        out["error"] = "typetree 不是 dict"
        return out
    out["name"] = str(tree.get("m_Name") or "")
    vd = tree.get("m_VertexData")
    if not isinstance(vd, dict):
        out["error"] = "没有 m_VertexData"
        return out
    vcount = int(vd.get("m_VertexCount") or 0)
    out["vcount"] = vcount
    if not vcount:
        out["error"] = "m_VertexCount 是 0"
        return out
    data = kmesh.as_bytes(vd.get("m_DataSize"))
    if not data:
        info = kmesh.stream_info_of(tree)
        if info is None or not info.is_streamed or resolver is None:
            out["error"] = "顶点数据在 .resS 里，没有可用的 stream 回调"
            return out
        data = bytes(resolver(info))
        if not data:
            out["error"] = "stream 解出 0 字节"
            return out
    channels = list(vd.get("m_Channels") or [])
    strides, bases = kmesh.stream_layout(channels, vcount)
    out["blend_weight"] = kmesh.attribute_channel(channels, ATTR_BLEND_WEIGHT) is not None
    try:
        color = kmesh.decode_attribute(
            channels, data, vcount, ATTR_COLOR, strides=strides, bases=bases
        )
        if color:
            out["color"] = color
        for attr, layer_name in EXTRA_UV:
            rows = kmesh.decode_attribute(
                channels, data, vcount, attr, strides=strides, bases=bases
            )
            if rows:
                out["uv"].append((layer_name, rows))
    except (kmesh.MeshFormatError, KeyError, TypeError, ValueError) as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def _fill_mesh(mesh, decoded: dict) -> list[str]:
    """把解出来的通道写进一个 bpy Mesh；返回真写进去的图层名。"""
    import bpy  # noqa: F401  -- 只为确认在 Blender 里跑

    done: list[str] = []
    n = len(mesh.vertices)
    if decoded.get("color"):
        rows = decoded["color"]
        attr = mesh.color_attributes.get(COLOR_NAME)
        if attr is None:
            attr = mesh.color_attributes.new(
                name=COLOR_NAME, type="FLOAT_COLOR", domain="POINT"
            )
        flat: list[float] = []
        for i in range(n):
            c = rows[i]
            flat.extend((c[0], c[1], c[2], c[3] if len(c) > 3 else 1.0))
        attr.data.foreach_set("color", flat)
        index = mesh.color_attributes.find(COLOR_NAME)
        # ★ 导出器取的是 render_color_index（见 gltf2/blender/exp/primitive_extract.py:533）
        mesh.color_attributes.active_color_index = index
        mesh.color_attributes.render_color_index = index
        done.append(COLOR_NAME)
    for layer_name, rows in decoded.get("uv") or ():
        layer = mesh.uv_layers.get(layer_name)
        if layer is None:
            layer = mesh.uv_layers.new(name=layer_name)
        loop_verts = [0] * len(mesh.loops)
        mesh.loops.foreach_get("vertex_index", loop_verts)
        flat2: list[float] = []
        for vi in loop_verts:
            uv = rows[vi]
            flat2.extend((uv[0], uv[1]))
        layer.data.foreach_set("uv", flat2)
        done.append(layer_name)
    mesh.update()
    return done


def apply(bundle: str, mesh_of: dict) -> list[Row]:
    """按 `mesh_of`（对象名 -> {mesh_id, …}）给场景里的网格补通道。

    :param bundle: units 包（`unitypy_source.open_source` 认的那种路径）。
    :param mesh_of: `ImportReport.mesh_of`（unit 与 turret 两次导入的合并）。
    """
    import bpy

    from ba_mod_maker.asset_kernel import unitypy_source

    rows: list[Row] = []
    source = unitypy_source.open_source(bundle)
    try:
        resolver = source.stream_resolver()
        for object_name in sorted(mesh_of or {}):
            info = mesh_of.get(object_name) or {}
            mesh_id = int(info.get("mesh_id") or 0)
            obj = bpy.data.objects.get(object_name)
            if obj is None or obj.type != "MESH" or obj.data is None:
                rows.append(Row(object_name, mesh_id, "", 0, note="不是网格对象（跳过）"))
                continue
            if not mesh_id:
                rows.append(Row(object_name, 0, "", len(obj.data.vertices), note="没有 mesh_id（跳过）"))
                continue
            try:
                tree = source.read_typetree(mesh_id)
            except Exception as exc:  # noqa: BLE001 -- 读不到就记账，不许静默
                rows.append(Row(object_name, mesh_id, "", len(obj.data.vertices),
                                note="读不到 Mesh：%s: %s" % (type(exc).__name__, exc)))
                continue
            decoded = decode_channels(tree, resolver)
            row = Row(object_name, mesh_id, str(decoded.get("name") or ""),
                      int(decoded.get("vcount") or 0),
                      color=bool(decoded.get("color")),
                      blend_weight=bool(decoded.get("blend_weight")))
            if decoded.get("error"):
                row.note = str(decoded["error"])
                rows.append(row)
                continue
            if row.verts != len(obj.data.vertices):
                row.note = ("顶点数对不上（源 %d / Blender %d）—— 一个通道都不写"
                            % (row.verts, len(obj.data.vertices)))
                rows.append(row)
                continue
            try:
                row.uvs = tuple(_fill_mesh(obj.data, decoded))
            except Exception as exc:  # noqa: BLE001 -- 写入失败必须点名，不许静默
                row.note = "写不进 Blender：%s: %s" % (type(exc).__name__, exc)
            if row.uvs:
                # 去掉 color 那一条，只留 UV 图层名
                row.uvs = tuple(name for name in row.uvs if name != COLOR_NAME)
            rows.append(row)
    finally:
        try:
            source.close()
        except Exception:  # noqa: BLE001
            pass
    return rows
