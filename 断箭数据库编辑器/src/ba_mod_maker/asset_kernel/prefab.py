"""Prefab 层级读侧：GameObject/Transform 树 + renderer/材质/贴图清单 + LOD 分解。

## 输入是「归一化节点」，不是 UnityPy 对象

本模块**零第三方依赖**，吃的是普通 dict（见 unitypy_source.py 的契约）：

    {
      "name": str, "path_id": int, "transform_id": int, "parent_transform": int | None,
      "position": (x,y,z), "rotation": (x,y,z,w), "scale": (x,y,z),
      "components": ({"kind": str, "path_id": int, ...}, ...),
    }

这样「怎么问 UnityPy 要数据」与「树怎么算」分开：前者在 unitypy_source，后者在这里，
于是树 / 世界坐标 / LOD / 骨骼路径全都能在**产品 venv 里**用真实资产跑用例
（连 3D 主程序都不必起）。

## 为什么世界坐标要自己算

工单 §3 D2 的判据是「与游戏世界坐标比，误差 < 1e-4 m」。所以这里给出**Unity 侧**的
世界坐标（父链复合），导入时用的却是 W2 的坐标契约（coords.unity_to_blender*）＋
Blender 自己复合父链 —— 两条**机制不同**的路径，对上了才叫验证。

## LOD（本单实测，RU_T72B_Base）

LODGroup m_Size=5、**4 级**；各级里 13 个 renderer 的去重并集 = 13：

    LOD0 = Chassis, RU_TanktrakL, RU_TanktrakR, BaseMobility, T72BA, K1, K5   (7)
    LOD1 = 000_skinned_Chassis, 001_skinned_RU_TanktrakL,
           002_skinned_RU_TanktrakR, 003_skinned_BaseMobility,
           T72BA, K1, K5                        （后 3 个与 LOD0 **共用**）
    LOD2 = 000_skinned_Chassis                      (1)
    LOD3 = lod_1x1x1                                (1)
    LOD0 ∪ LOD1 ∪ LOD2 ∪ LOD3 = 13 个不同 renderer

工单 §7 坑 6 说的「别把 LOD1/2 的 renderer 也算进 13」与判据 D1 的 13 并不冲突：
13 就是**全 prefab 的 renderer 数**（= 四级 LOD 的并集），只导 LOD0 时是 7。
本模块把两个数都暴露出来（lod_members / lod_union），用例把分解也钉住。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "LOD_GROUP",
    "MESH_FILTER",
    "MESH_RENDERER",
    "SKINNED_MESH_RENDERER",
    "Node",
    "PrefabTree",
    "Renderer",
    "mat_mul",
    "read_prefab_tree",
    "trs_matrix",
    "transform_point",
]

SKINNED_MESH_RENDERER = "SkinnedMeshRenderer"
MESH_RENDERER = "MeshRenderer"
MESH_FILTER = "MeshFilter"
LOD_GROUP = "LODGroup"

#: 会被当成「一个 renderer」的组件类型（D1 数它们）。
RENDERER_KINDS = (SKINNED_MESH_RENDERER, MESH_RENDERER)

IDENTITY4: tuple[float, ...] = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


# --------------------------------------------------------------------------- #
# 小矩阵运算（行主序 16 个 float；列向量约定，与 Unity 一致）
# --------------------------------------------------------------------------- #
def trs_matrix(
    position: Sequence[float], rotation: Sequence[float], scale: Sequence[float]
) -> tuple[float, ...]:
    """Unity 的 localToParentMatrix = T * R * S。"""
    x, y, z, w = rotation[0], rotation[1], rotation[2], rotation[3]
    sx, sy, sz = scale[0], scale[1], scale[2]
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    r = (
        1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy),
        2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx),
        2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy),
    )
    # ⚠ 平移在第 **4 列**（下标 3/7/11），不是第 4 行 —— Unity 是列向量约定
    #   （M = T·R·S，p' = M·p）。本单第一版把它写进了第 4 行，于是 world_position
    #   读出来全是 (0,0,0)、D2 的误差量到 8.06 m；改成第 4 列后与 Blender 的
    #   matrix_world 对上（锚点见 tests/test_import_unit.py 的 D2）。
    return (
        r[0] * sx, r[1] * sy, r[2] * sz, position[0],
        r[3] * sx, r[4] * sy, r[5] * sz, position[1],
        r[6] * sx, r[7] * sy, r[8] * sz, position[2],
        0.0, 0.0, 0.0, 1.0,
    )


def mat_mul(a: Sequence[float], b: Sequence[float]) -> tuple[float, ...]:
    """4x4 行主序相乘（a·b，即先 b 后 a）。"""
    out = [0.0] * 16
    for i in range(4):
        for j in range(4):
            out[4 * i + j] = sum(a[4 * i + k] * b[4 * k + j] for k in range(4))
    return tuple(out)


def transform_point(m: Sequence[float], v: Sequence[float]) -> tuple[float, float, float]:
    """点变换（含平移，w=1）。"""
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


# --------------------------------------------------------------------------- #
# 树
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _Component:
    kind: str
    path_id: int
    data: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Node:
    """一个 GameObject + 它的 Transform 局部变换。"""

    name: str
    path_id: int
    transform_id: int
    parent_id: int | None
    depth: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]
    children: tuple[int, ...]
    segments: tuple[str, ...]
    kinds: tuple[str, ...]

    @property
    def path(self) -> str:
        return "/".join(self.segments)


@dataclass(frozen=True, slots=True)
class Renderer:
    """一个 renderer 组件（SMR 或 MR）。"""

    kind: str
    component_id: int
    node_id: int
    name: str
    mesh_id: int | None
    #: 网格 PPtr 的 m_FileID：0 = 本包；非 0 = 外部（实测 lod_1x1x1 指
    #: fileID=2 = "unity default resources"，本包根本没有这个网格）。
    mesh_file: int
    material_ids: tuple[int, ...]
    #: SMR.m_Bones 的槽表，**按槽号对齐**：空槽是 None（不许滤掉，会错位）。
    bone_ids: tuple[int | None, ...]


@dataclass(slots=True)
class PrefabTree:
    """一棵 prefab 的读侧视图（节点 / renderer / 材质 / LOD / 骨骼路径）。"""

    root: int
    nodes: tuple[Node, ...]
    renderers: tuple[Renderer, ...]
    lods: tuple[tuple[int, ...], ...]
    materials: Mapping[int, Mapping[str, object]] = field(default_factory=dict)
    lod_size: float | None = None
    _by_transform: dict[int, Node] = field(default_factory=dict, repr=False)
    _world: dict[int, tuple[float, ...]] = field(default_factory=dict, repr=False)

    # ---- 查询 ----
    def node(self, transform_id: int) -> Node:
        try:
            return self._by_transform[transform_id]
        except KeyError as exc:
            raise KeyError(f"prefab 里没有 transform {transform_id}") from exc

    def nodes_named(self, name: str) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if n.name == name)

    def renderer_of(self, component_id: int) -> Renderer | None:
        for r in self.renderers:
            if r.component_id == component_id:
                return r
        return None

    def material_name(self, material_id: int) -> str:
        entry = self.materials.get(material_id)
        if entry is None:
            return f"<material {material_id}>"
        return str(entry.get("name") or f"<material {material_id}>")

    def texture_ids(self) -> tuple[int, ...]:
        """本 prefab 的材质引用到的所有贴图 pathID（去重保序）。"""
        out: list[int] = []
        for renderer in self.renderers:
            for mid in renderer.material_ids:
                entry = self.materials.get(mid) or {}
                for slot in entry.get("texture_slots") or ():
                    tex = slot[1] if isinstance(slot, (tuple, list)) else None
                    if tex and int(tex) not in out:
                        out.append(int(tex))
        return tuple(out)

    def lod_members(self, level: int) -> tuple[int, ...]:
        return self.lods[level] if 0 <= level < len(self.lods) else ()

    def lod_union(self) -> tuple[int, ...]:
        out: list[int] = []
        for level in self.lods:
            for cid in level:
                if cid not in out:
                    out.append(cid)
        return tuple(out)

    def renderers_outside_lods(self) -> tuple[Renderer, ...]:
        inside = set(self.lod_union())
        return tuple(r for r in self.renderers if r.component_id not in inside)

    # ---- 世界坐标（Unity 侧） ----
    def world_matrix(self, transform_id: int) -> tuple[float, ...]:
        cached = self._world.get(transform_id)
        if cached is not None:
            return cached
        node = self.node(transform_id)
        local = trs_matrix(node.position, node.rotation, node.scale)
        if node.parent_id is None:
            world = local
        else:
            world = mat_mul(self.world_matrix(node.parent_id), local)
        self._world[transform_id] = world
        return world

    def world_position(self, transform_id: int) -> tuple[float, float, float]:
        m = self.world_matrix(transform_id)
        return (m[3], m[7], m[11])

    # ---- 骨骼路径（设计稿 §8 规则 4） ----
    def bone_path(self, transform_id: int | None, *, root_name: str = "root") -> str | None:
        """从名为 root_name 的那一段起（含它）拼出的路径；链上没有它就返回 None。"""
        if not transform_id:
            return None
        node = self._by_transform.get(transform_id)
        if node is None:
            return None
        segments = list(node.segments)
        if root_name not in segments:
            return None
        return "/".join(segments[segments.index(root_name):])

    def bone_paths(
        self, bone_ids: Sequence[int | None], *, root_name: str = "root"
    ) -> tuple[str | None, ...]:
        return tuple(
            self.bone_path(None if b is None else int(b), root_name=root_name) for b in bone_ids
        )

    # ---- 读数 ----
    def describe(self) -> str:
        kinds: dict[str, int] = {}
        for node in self.nodes:
            for kind in node.kinds:
                kinds[kind] = kinds.get(kind, 0) + 1
        return (
            f"节点 {len(self.nodes)} / renderer {len(self.renderers)} / "
            f"材质 {len(self.materials)} / 贴图 {len(self.texture_ids())} / "
            f"LOD {len(self.lods)} 级（并集 {len(self.lod_union())} 个）/ 组件 {kinds}"
        )


def _vec(raw: object, size: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if raw is None:
        return default  # type: ignore[return-value]
    seq = list(raw) if isinstance(raw, (list, tuple)) else list(raw)  # type: ignore[call-overload]
    if len(seq) < size:
        raise ValueError(f"向量只有 {len(seq)} 个分量，要 {size}")
    return tuple(float(v) for v in seq[:size])  # type: ignore[return-value]


def build_tree(
    nodes: Sequence[Mapping[str, object]],
    materials: Mapping[int, Mapping[str, object]] | None = None,
) -> PrefabTree:
    """归一化节点 -> PrefabTree（纯函数，用例直接喂造出来的节点也能跑）。"""
    if not nodes:
        raise ValueError("prefab 一个节点都没有")

    index_of: dict[int, int] = {}
    for i, raw in enumerate(nodes):
        tid = int(raw["transform_id"])  # type: ignore[arg-type]
        if tid in index_of:
            raise ValueError(f"transform {tid} 出现两次")
        index_of[tid] = i

    children: dict[int, list[int]] = {int(r["transform_id"]): [] for r in nodes}  # type: ignore[arg-type]
    roots: list[int] = []
    for raw in nodes:
        tid = int(raw["transform_id"])  # type: ignore[arg-type]
        parent = raw.get("parent_transform")
        if parent is None:
            roots.append(tid)
            continue
        parent_id = int(parent)  # type: ignore[arg-type]
        if parent_id not in children:
            raise ValueError(f"transform {tid} 的父亲 {parent_id} 不在节点表里")
        children[parent_id].append(tid)
    if len(roots) != 1:
        raise ValueError(f"prefab 应当只有一个根，实测 {len(roots)} 个：{roots}")

    order: list[int] = []
    depth_of: dict[int, int] = {}
    stack: list[tuple[int, int]] = [(roots[0], 0)]
    guard = 0
    while stack:
        tid, depth = stack.pop()
        if tid in depth_of:
            raise ValueError(f"transform {tid} 在树里出现两次（有环？）")
        depth_of[tid] = depth
        order.append(tid)
        guard += 1
        if guard > len(nodes) * 4 + 16:
            raise ValueError("层级展开超限（有环？）")
        kids = children[tid]
        kids.sort(key=lambda k: index_of[k], reverse=True)  # 用栈，倒着压
        for kid in kids:
            stack.append((kid, depth + 1))

    by_transform: dict[int, Node] = {}
    node_list: list[Node] = []
    for tid in order:
        raw = nodes[index_of[tid]]
        parent = raw.get("parent_transform")
        parent_id = None if parent is None else int(parent)  # type: ignore[arg-type]
        segments = (
            (raw["name"],)
            if parent_id is None
            else (*by_transform[parent_id].segments, str(raw["name"]))
        )
        comps: list[_Component] = []
        for comp in raw.get("components") or ():  # type: ignore[union-attr]
            kind = str(comp.get("kind", ""))
            comps.append(
                _Component(
                    kind=kind,
                    path_id=int(comp.get("path_id", 0) or 0),
                    data={k: v for k, v in comp.items() if k not in ("kind", "path_id")},
                )
            )
        node = Node(
            name=str(raw["name"]),
            path_id=int(raw["path_id"]),  # type: ignore[arg-type]
            transform_id=tid,
            parent_id=parent_id,
            depth=depth_of[tid],
            position=_vec(raw.get("position"), 3, (0.0, 0.0, 0.0)),
            rotation=_vec(raw.get("rotation"), 4, (0.0, 0.0, 0.0, 1.0)),
            scale=_vec(raw.get("scale"), 3, (1.0, 1.0, 1.0)),
            children=tuple(children[tid]),
            segments=tuple(str(s) for s in segments),
            kinds=tuple(c.kind for c in comps),
        )
        by_transform[tid] = node
        node_list.append(node)
        for comp in comps:
            _ = comp  # 组件明细在下面 renderer/LOD 一段统一处理

    # MeshRenderer 自己不带 m_Mesh（网格挂在同一个节点的 MeshFilter 上）——实测
    # lod_1x1x1 走的就是这条路。所以先做一份节点 -> MeshFilter 网格的兜底表。
    filter_mesh: dict[int, object] = {}
    for tid in order:
        raw = nodes[index_of[tid]]
        for comp in raw.get("components") or ():  # type: ignore[union-attr]
            if str(comp.get("kind", "")) == MESH_FILTER:
                filter_mesh.setdefault(tid, (comp.get("mesh"), comp.get("mesh_file", 0)))

    renderers: list[Renderer] = []
    lods: list[tuple[int, ...]] = []
    lod_size: float | None = None
    #: 已经采信的那个 LODGroup 的**深度**（None = 还没遇到）。
    #: ★ 2026-10-17 修：旧版每遇到一个 LODGroup 就 `lods = levels` **整份覆盖** ⇒
    #:     **嵌套子 prefab 的 LODGroup 赢过车体自己的**（机枪座 `m240_vech` /
    #:     飞行员 `Pilot_Helicopter` · `Pilot_Plane` · `JalavaeJagu_Driver`）⇒ 整台车的
    #:     renderer 被判「不在任何 LOD 档里 ⇒ 一律可见」。实测 9 件里 **7 件中招、89 个 renderer**
    #:     （`tools/cross_unit_gate.py --check --table`）；T-72B / US_ACV 各自只有 1 个
    #:     LODGroup，所以这条在那两件上**永远看不见**。
    #:     现在只认**最浅**那个（根上的那个）；同深度/更深的一律忽略。
    lod_depth: int | None = None
    for tid in order:
        raw = nodes[index_of[tid]]
        for comp in raw.get("components") or ():  # type: ignore[union-attr]
            kind = str(comp.get("kind", ""))
            cid = int(comp.get("path_id", 0) or 0)
            if kind in RENDERER_KINDS:
                mesh = comp.get("mesh")
                mesh_file = int(comp.get("mesh_file", 0) or 0)
                if mesh in (None, 0) and tid in filter_mesh:
                    mesh, mesh_file = filter_mesh[tid]  # type: ignore[assignment]
                renderers.append(
                    Renderer(
                        kind=kind,
                        component_id=cid,
                        node_id=tid,
                        name=str(raw["name"]),
                        mesh_id=None if mesh in (None, 0) else int(mesh),  # type: ignore[arg-type]
                        mesh_file=mesh_file,
                        material_ids=tuple(
                            int(m) for m in (comp.get("materials") or ()) if int(m)
                        ),
                        bone_ids=tuple(
                            None if b is None else int(b) for b in (comp.get("bones") or ())
                        ),
                    )
                )
            elif kind == LOD_GROUP:
                # ⛔ 只采信**最浅**那个（根上的）；`order` 是从根往下 DFS，所以根的先到。
                #    同深度再来一个也不换（先到先得）—— 见上面 `lod_depth` 那段口径。
                node_depth = depth_of[tid]
                if lod_depth is not None and node_depth >= lod_depth:
                    continue
                lod_depth = node_depth
                raw_size = comp.get("size")
                lod_size = None if raw_size is None else float(raw_size)  # type: ignore[arg-type]
                levels = []
                for level in comp.get("lods") or ():  # type: ignore[union-attr]
                    levels.append(tuple(int(c) for c in level))
                lods = levels

    return PrefabTree(
        root=roots[0],
        nodes=tuple(node_list),
        renderers=tuple(renderers),
        lods=tuple(lods),
        materials=dict(materials or {}),
        lod_size=lod_size,
        _by_transform=by_transform,
    )


def read_prefab_tree(
    source: object,
    *,
    materials: Mapping[int, Mapping[str, object]] | None = None,
    asset_path: str | None = None,
) -> PrefabTree:
    """读一棵 prefab（工单 §9 点名的入口）。

    :param source: 三种输入都认 ——
        ① 归一化节点序列（本模块的契约，用例造夹具 / unitypy_source 都产出它）；
        ② 有 read_prefab_nodes() 的对象（unitypy_source.AssetSource）；
        ③ 容器路径（str / Path）—— 这时**延迟** import unitypy_source 去打开它。
    :param asset_path: source 是容器路径时，要读的 prefab 地址键。
    """
    if isinstance(source, (str, Path)):
        # unitypy_source 顶部 import 本模块（共用组件类型常量）——这里必须延迟，
        # 否则是一个真环。⛔ 别把常量抄一份到这里（AGENTS.md §5「逻辑只有一份」）。
        from ba_mod_maker.asset_kernel import unitypy_source  # noqa: PLC0415

        if not asset_path:
            raise ValueError("source 是 bundle 路径时必须给 asset_path")
        with unitypy_source.open_source(source) as src:
            built = src.read_prefab_nodes(asset_path)
        return build_tree(built[0], built[1])
    nodes_fn = getattr(source, "read_prefab_nodes", None)
    if callable(nodes_fn):
        built = nodes_fn(asset_path)
        return build_tree(built[0], built[1])
    if isinstance(source, Sequence):
        return build_tree(source, materials)
    raise TypeError(f"认不出的 prefab 来源：{type(source).__name__}")
