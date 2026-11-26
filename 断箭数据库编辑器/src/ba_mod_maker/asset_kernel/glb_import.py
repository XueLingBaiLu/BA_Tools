r"""读 GLB（glTF 2.0 二进制）-> `ObjMesh`（本模块自带的数据类，见下）。

工程文件（断箭模型提取器输出）里的几何是 `model.glb`。
★ 2026-10-25：`ObjMesh` 原本住在 `asset_kernel` 的改网格内核里（那条链已按用户口径「全删」），
本模块是它**唯一还活着的用处**（全仓唯一的 GLB 读侧），所以这份纯数据类随本模块一起留下 ——
数据类本身零依赖，字段/默认值与删前逐条相同。

只认几何：POSITION / NORMAL / TEXCOORD_0 + indices（多 primitive 合并成一份）。

**坐标**：位置/法线不做变换（glTF 与 Unity 同为 Y 朝上）；
**UV 的 V 必须翻**（`v = 1 - v`）—— glTF 的 TEXCOORD_0 原点在**左上**，Unity 在**左下**。

★ 2026-10-24 实测（这是「贴图/涂装乱」的真凶，别再把这一行删掉）：
  同一条 T-72B 车体网格 —— 原样读入时与真件 UV 集合只对上 **19/44,164（0.04%）**；
  `v = 1 - v` 之后对上 **27,278/44,164（61.8%）**（其余是 half 精度与 +5 顶点的舍入差）。
  旁证：V 值域镜像 —— 真件 `0.0019..0.9971`，我们 `0.0029..0.9980`（= 1 − 真件两端）。
  而 UV 密度中位数、包围盒几乎不变 ⇒ 上一版那些「密度/包围盒一样」的检查**看不见 V 镜像**。
OBJ 那条路另说：`swap_yz=True`（Blender 导出 OBJ 是 Z 朝上）、OBJ 的 UV 本来就是左下原点。

出处：`kb/43-模型导入链（桥·LOD同槽·按槽贴图·strip-skins）.md`（读侧契约）＋
`_work/unitybuild/` 里那批 GLB↔真件对拍读数（上面 V 镜像那三行就是它们量的）。
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = ["GlbError", "ObjMesh", "read_glb", "read_glb_parts",
           "read_glb_parts_file", "read_glb_skeleton"]


@dataclass(frozen=True, slots=True)
class ObjMesh:
    """一份 OBJ 的原始内容（**不做**去重、不做坐标变换）。"""

    positions: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    #: 每个面 = 若干个角；每个角 = (v 下标, vt 下标, vn 下标)，**负数已还原成绝对下标**（0 起）。
    #: ⚠ `vt`/`vn` 为 **None** = OBJ 里**没给**这一项（`f 1//2` / `f 1`）；**0 = 真的指第一个**。
    faces: tuple[tuple[tuple[int, int | None, int | None], ...], ...]
    #: 这份几何里出现过几种 material（GLB 按 `primitive.material` 数）。
    material_count: int = 0
    #: 合成它的 primitive 条数（含没有 POSITION 被跳过的那些）。
    primitive_count: int = 0
    #: 逐面所属的材质组下标（与 `material_count` 同一次遍历里编号）。
    face_materials: tuple[int, ...] = ()

    @property
    def triangles(self) -> int:
        """扇形三角化之后的三角形数。"""
        return sum(max(0, len(face) - 2) for face in self.faces)

    def aabb(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if not self.positions:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [p[0] for p in self.positions]
        ys = [p[1] for p in self.positions]
        zs = [p[2] for p in self.positions]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

#: componentType -> (struct 格式, 每分量字节数)
_COMPONENT = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
_TYPE_SIZE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class GlbError(ValueError):
    """这个文件不是能读的 GLB。"""


def _chunks(data: bytes) -> tuple[dict, bytes]:
    if len(data) < 12 or data[:4] != b"glTF":
        raise GlbError("不是 GLB（头 4 字节不是 glTF）")
    _magic, version, length = struct.unpack_from("<4sII", data, 0)
    if version != 2:
        raise GlbError(f"只认 glTF 2.0，这个文件是 {version}")
    pos, doc, blob = 12, None, b""
    while pos + 8 <= min(length, len(data)):
        clen, ctype = struct.unpack_from("<I4s", data, pos)
        body = data[pos + 8 : pos + 8 + clen]
        if ctype == b"JSON":
            doc = json.loads(body.decode("utf-8"))
        elif ctype == b"BIN\x00":
            blob = body
        pos += 8 + clen
    if doc is None:
        raise GlbError("没有 JSON 块")
    return doc, blob


def _accessor(doc: dict, blob: bytes, index: int) -> list[tuple]:
    acc = doc["accessors"][index]
    fmt, csize = _COMPONENT[acc["componentType"]]
    n = _TYPE_SIZE[acc["type"]]
    count = int(acc["count"])
    if "bufferView" not in acc:
        return [tuple([0] * n) for _ in range(count)]
    bv = doc["bufferViews"][acc["bufferView"]]
    base = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    stride = int(bv.get("byteStride") or (csize * n))
    unpack = struct.Struct("<" + fmt * n).unpack_from
    return [tuple(unpack(blob, base + i * stride)) for i in range(count)]


def _objmesh_from(doc: dict, blob: bytes, prims: list, group_of: dict, materials: set):
    """把一串 primitive 合成一份 ObjMesh（合并 / 分件共用这一份实现）。

    返回 `(ObjMesh | None, 这串里有几个 primitive)`；没有 POSITION 或没有三角形时 ObjMesh 是 None。
    """
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[tuple[tuple[int, int | None, int | None], ...]] = []
    face_materials: list[int] = []
    primitives = 0
    for prim in prims:
        primitives += 1
        mat = int(prim["material"]) if "material" in prim else -1
        if mat not in group_of:
            group_of[mat] = len(group_of)
        group = group_of[mat]
        if "material" in prim:
            materials.add(mat)
        attrs = prim.get("attributes") or {}
        if "POSITION" not in attrs:
            continue
        pos = _accessor(doc, blob, attrs["POSITION"])
        nor = _accessor(doc, blob, attrs["NORMAL"]) if "NORMAL" in attrs else []
        uvv = _accessor(doc, blob, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else []
        if "indices" in prim:
            idx = [int(v[0]) for v in _accessor(doc, blob, prim["indices"])]
        else:
            idx = list(range(len(pos)))
        bp, bn, bu = len(positions), len(normals), len(uvs)
        positions.extend((float(p[0]), float(p[1]), float(p[2])) for p in pos)
        normals.extend((float(v[0]), float(v[1]), float(v[2])) for v in nor)
        # ★ V 翻转（见模块头实测）：glTF 原点在左上，Unity 在左下。
        uvs.extend((float(t[0]), 1.0 - float(t[1])) for t in uvv)
        for i in range(0, len(idx) - 2, 3):
            tri = (idx[i], idx[i + 1], idx[i + 2])
            faces.append(tuple(
                (bp + v, (bu + v) if uvv else None, (bn + v) if nor else None)
                for v in tri
            ))
            face_materials.append(group)
    if not positions or not faces:
        return None, primitives
    return ObjMesh(positions=tuple(positions), uvs=tuple(uvs), normals=tuple(normals),
                   faces=tuple(faces), material_count=len(materials),
                   primitive_count=primitives, face_materials=tuple(face_materials)), primitives


def read_glb(data: bytes) -> ObjMesh:
    """一份 GLB 字节 -> ObjMesh（所有 mesh/primitive 合并成一份几何）。"""
    doc, blob = _chunks(data)
    prims = [p for m in (doc.get("meshes") or []) for p in (m.get("primitives") or [])]
    if not any("POSITION" in (p.get("attributes") or {}) for p in prims):
        raise GlbError("glb 里一个 POSITION 都没有")
    mesh, _count = _objmesh_from(doc, blob, prims, {}, set())
    if mesh is None:
        raise GlbError("glb 里一个三角形都没有")
    return mesh


def _mirror_x(mesh: ObjMesh) -> ObjMesh:
    """X 取反（glb/Blender 侧 X 与 Unity 侧 X 相反）＋ 翻转三角形绕序。

    ★ 2026-10-24 实测（别删）：把提取器工程的 11 颗网格与真件包逐点比 ——
    原样比对：履带质心 `+1.374` vs 真件 `−1.374`（**左右对调**），最近点中位 2.46 m；
    **X 取反后**：履带与车体都 **中位 0.0 / 最大 0.0** 逐点重合。
    不取反的后果（真机）：权重转移投到另一半履带（519 个顶点离主骨 >1.5 m、最远 2.99 m），
    轮子一转就拉出一条几十米长的带子。
    """
    return ObjMesh(
        positions=tuple((-p[0], p[1], p[2]) for p in mesh.positions),
        uvs=mesh.uvs,
        normals=tuple((-v[0], v[1], v[2]) for v in mesh.normals) if mesh.normals else (),
        faces=tuple(tuple(reversed(tri)) for tri in mesh.faces),
        material_count=mesh.material_count,
        primitive_count=mesh.primitive_count,
        face_materials=mesh.face_materials,
    )

def read_glb_skin(data: bytes) -> dict:
    """读 glb 的**骨架与逐顶点蒙皮**（`skins` / `JOINTS_0` / `WEIGHTS_0`）。

    返回 `{joints: [(节点下标, 名字)], parts: {节点名: {joints: [...], weights: [...]}}}`；
    `joints[i]` = 该 skin 的第 i 根骨（顶点权重里的骨下标）；`parts` 的键 = 带 mesh 的节点名。

    ★ 2026-10-24 为什么要它：真机验收之后用户问「全新单位能不能导」——查了工程 glb，
    **骨架和权重本来就在**（实测 T-72B 工程：2 套 skin、12 与 31 根骨，顶点属性带
    JOINTS_0 / WEIGHTS_0），是我们的读入口只认 POSITION/NORMAL/TEXCOORD_0，把它们整段丢了
    ⇒ 过去只能借真件骨架。
    """
    doc, blob = _chunks(data)
    skins = doc.get("skins") or []
    nodes = doc.get("nodes") or []
    out: dict = {"joints": [], "parts": {}}
    if not skins:
        return out
    # 骨下标 -> 节点（以第一套 skin 为准；多套时按节点上的 skin 索引分开记）
    skin_joints: list[list[int]] = [list(map(int, s.get("joints") or [])) for s in skins]
    out["joints"] = [(int(i), str(nodes[i].get("name") or f"joint{i}"))
                     for i in skin_joints[0]]
    meshes = doc.get("meshes") or []
    for index, node in enumerate(nodes):
        if "mesh" not in node or "skin" not in node:
            continue
        name = str(node.get("name") or f"node{index}")
        joint_names = [str(nodes[j].get("name") or f"joint{j}")
                       for j in skin_joints[int(node["skin"])]]
        joints: list[tuple[int, int, int, int]] = []
        weights: list[tuple[float, float, float, float]] = []
        for prim in (meshes[int(node["mesh"])].get("primitives") or []):
            attrs = prim.get("attributes") or {}
            if "JOINTS_0" not in attrs or "WEIGHTS_0" not in attrs:
                continue
            j = _accessor(doc, blob, attrs["JOINTS_0"])
            w = _accessor(doc, blob, attrs["WEIGHTS_0"])
            joints.extend(tuple(int(v) for v in row)[:4] for row in j)  # type: ignore[misc]
            weights.extend(tuple(float(v) for v in row)[:4] for row in w)  # type: ignore[misc]
        if joints:
            out["parts"][name] = {"joints": joints, "weights": weights,
                                  "joint_names": joint_names}
    return out

def read_glb_parts(data: bytes) -> list[tuple[str, ObjMesh]]:
    """一份 GLB -> **每个带 mesh 的节点**一颗几何（节点名 -> ObjMesh），⛔ 不合并。

    为什么要有它（2026-10-24 用户口径「提取和导入互逆」）：提取器一个工程 = 一整件模型
    （T-72B 实测 11 颗网格 / 127,731 顶点）；`read_glb` 把它们合成一份，导进去就会
    「整车塞进一颗 Mesh」（实测 Chassis 44,164 → 127,731）。整件导入必须按节点分开拿。

    ⛔ **不施加节点变换**：glTF 的 mesh 顶点是节点局部坐标，包里那颗 Mesh 的渲染器
    带着同一个局部变换；把节点变换乘进去会让零件位置翻倍。
    """
    doc, blob = _chunks(data)
    out: list[tuple[str, ObjMesh]] = []
    meshes = doc.get("meshes") or []
    for index, node in enumerate(doc.get("nodes") or []):
        if "mesh" not in node:
            continue
        mesh_doc = meshes[int(node["mesh"])]
        name = str(node.get("name") or f"node{index}")
        part, _count = _objmesh_from(doc, blob, mesh_doc.get("primitives") or [], {}, set())
        if part is not None:
            out.append((name, _mirror_x(part)))
    if not out:
        raise GlbError("glb 里没有一个带几何的节点")
    return out


def read_glb_parts_file(path: str | Path) -> list[tuple[str, ObjMesh]]:
    return read_glb_parts(Path(path).read_bytes())

# --------------------------------------------------------------------------- #
# 骨架 / 蒙皮（全新单位导入侧的唯一读入口）
# --------------------------------------------------------------------------- #
#: glTF 四元数是 (x, y, z, w)，与 Unity 的 Quaternion 序列化同序 —— 下面直接照抄分量。
_IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)
_IDENTITY_VEC3 = (0.0, 0.0, 0.0)
_ONE_VEC3 = (1.0, 1.0, 1.0)


def _vec3(raw: object, default: tuple[float, float, float]) -> tuple[float, float, float]:
    """glTF 节点上的 translation/scale（缺失 = 默认值，⛔ 不当成 0）。"""
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return default
    return (float(raw[0]), float(raw[1]), float(raw[2]))


def _quat(raw: object) -> tuple[float, float, float, float]:
    """glTF 节点上的 rotation（**已经是 (x, y, z, w)**，与 Unity 同序）。"""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return _IDENTITY_QUAT
    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))


def mirror_translation(v: tuple[float, float, float]) -> tuple[float, float, float]:
    """X 取反 —— 与 :func:`_mirror_x` 同一把反射（M = diag(-1, 1, 1)）。"""
    return (-float(v[0]), float(v[1]), float(v[2]))


def mirror_rotation(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """四元数在 X 反射下的共轭：轴角 (n, θ) -> (M n, −θ) ⇒ q = (x, −y, −z, w)。

    为什么是它（别改成只取反 x）：M R M 是 **反射共轭**，绕 X 的旋转角度符号不变、
    绕 Y/Z 的角度反向 —— 只翻一个分量会让骨架与几何转反方向。
    """
    return (float(q[0]), -float(q[1]), -float(q[2]), float(q[3]))


def mirror_bind_pose(raw16: list[float] | tuple[float, ...]) -> dict[str, float]:
    """glTF 的 column-major 4x4 -> Unity `m_BindPose` 的 `e{R}{C}` 字典（**含 X 镜像**）。

    两件事一起做（少做一件都会让蒙皮错位）：
    1. 转置口径：glTF 的 `m[C*4+R]` 是第 C 列第 R 行；Unity 的 `e{R}{C}` 是第 R 行第 C 列；
    2. 反射共轭：`B' = M · B · M`，M = diag(−1, 1, 1) ⇒ 第 0 行/第 0 列（除 e00）取反。

    判据（可复算）：bind pose 下 `W · B == I`，镜像后 `W' · B' == I` —— 蒙皮结果不变。
    """
    if len(raw16) != 16:  # glTF 的 MAT4 就是 16 个 float
        raise GlbError(f"bindpose 要 16 个 float，拿到 {len(raw16)}")
    mat = [[float(raw16[c * 4 + r]) for c in range(4)] for r in range(4)]
    for r in range(4):
        for c in range(4):
            if (r == 0) != (c == 0):   # 只有一端是 0 ⇒ 反射共轭取负（e00 两端都是 0，不取负）
                mat[r][c] = -mat[r][c]
    return {f"e{r}{c}": mat[r][c] for r in range(4) for c in range(4)}


def read_glb_skeleton(data: bytes) -> dict:
    """读 glb 的**节点树 + 骨架 + 逐顶点蒙皮**（全新单位导入侧的唯一入口）。

    返回::

        {
          "nodes": [{"index","name","parent","children",
                     "translation","rotation","scale","mesh","skin"}],
          "skins": [{"joints": [节点下标], "joint_names": [名字],
                     "inverse_bind": [{"e00":…} 每骨 16 个（已镜像）]}],
          "parts": {节点名: {"node","skin","joint_names","joints","weights"}},
        }

    ★ 2026-10-25 为什么要它：工程 glb **自带骨架**（RU_T72B 实测 2 套 skin / 12 与 31 根骨、
    顶点属性带 JOINTS_0+WEIGHTS_0），而过去的读入口只认 POSITION/NORMAL/TEXCOORD_0，
    把骨架整段丢了 ⇒ 只能借真件骨架（那不是「导入」）。

    ⛔ **坐标口径与 :func:`read_glb_parts` 一致：已做 X 镜像**（平移 x 取反、旋转 (y,z) 取反、
    bindpose 走 :func:`mirror_bind_pose`）。几何与骨架必须在同一把反射下变换 ——
    只镜像一边，蒙皮就会把顶点投到另一半车上（同 2026-10-24 那次长带子的机理）。

    ⚠ `parts[名]["joints"]/["weights"]` 是**该节点 mesh 各 primitive 依次拼起来**的逐顶点表，
    顶点顺序与 :func:`read_glb_parts` 给出的 ObjMesh **完全一致**（同一套 `_objmesh_from` 顺序）。
    """
    doc, blob = _chunks(data)
    nodes_doc = doc.get("nodes") or []
    parent: dict[int, int] = {}
    for index, node in enumerate(nodes_doc):
        for child in node.get("children") or []:
            parent[int(child)] = index
    nodes: list[dict] = []
    for index, node in enumerate(nodes_doc):
        nodes.append({
            "index": index,
            "name": str(node.get("name") or f"node{index}"),
            "parent": parent.get(index),
            "children": [int(c) for c in (node.get("children") or [])],
            "translation": mirror_translation(_vec3(node.get("translation"), _IDENTITY_VEC3)),
            "rotation": mirror_rotation(_quat(node.get("rotation"))),
            "scale": _vec3(node.get("scale"), _ONE_VEC3),
            "mesh": (int(node["mesh"]) if "mesh" in node else None),
            "skin": (int(node["skin"]) if "skin" in node else None),
        })
    skins: list[dict] = []
    joint_lists: list[list[int]] = []
    for skin in (doc.get("skins") or []):
        joints = [int(j) for j in (skin.get("joints") or [])]
        joint_lists.append(joints)
        names = [nodes[j]["name"] for j in joints]
        ibm: list[dict[str, float]] = []
        if "inverseBindMatrices" in skin:
            raw = _accessor(doc, blob, int(skin["inverseBindMatrices"]))
            ibm = [mirror_bind_pose(list(row)) for row in raw]
            if len(ibm) != len(joints):
                raise GlbError(
                    f"inverseBindMatrices {len(ibm)} 条 != joints {len(joints)} 根"
                    " —— 绑定数据不完整"
                )
        skins.append({"joints": joints, "joint_names": names, "inverse_bind": ibm})
    meshes = doc.get("meshes") or []
    parts: dict[str, dict] = {}
    for node in nodes:
        # ⚠ 2026-10-25：**不再要求节点挂 skin** —— 实测 glb 里 K1 / K5 / BaseMobility /
        #   T72BA 都有 JOINTS_0+WEIGHTS_0 但 `skin` 是 None（它们的权重全指向
        #   `neutral_bone`，= 刚性件）。只收带 skin 的会把它们漏掉 ⇒ 反应装甲进不了包。
        if node["mesh"] is None:
            continue
        joints_v: list[tuple[int, ...]] = []
        weights_v: list[tuple[float, ...]] = []
        for prim in (meshes[int(node["mesh"])].get("primitives") or []):
            attrs = prim.get("attributes") or {}
            if "JOINTS_0" not in attrs or "WEIGHTS_0" not in attrs:
                continue
            raw_j = _accessor(doc, blob, attrs["JOINTS_0"])
            raw_w = _accessor(doc, blob, attrs["WEIGHTS_0"])
            joints_v.extend(tuple(int(v) for v in row) for row in raw_j)
            weights_v.extend(tuple(float(v) for v in row) for row in raw_w)
        if not joints_v:
            continue
        parts[str(node["name"])] = {
            "node": int(node["index"]),
            "skin": (int(node["skin"]) if node["skin"] is not None else None),
            "joint_names": (
                list(skins[int(node["skin"])]["joint_names"])
                if node["skin"] is not None else []
            ),
            "joints": joints_v,
            "weights": weights_v,
        }
    return {"nodes": nodes, "skins": skins, "parts": parts, "joint_lists": joint_lists}


def read_glb_skeleton_file(path: str | Path) -> dict:
    return read_glb_skeleton(Path(path).read_bytes())
