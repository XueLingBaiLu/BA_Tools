r"""L7 · prefab 组装器：**新增节点 / 删除节点 / 加组件 / 删组件**（干跑 ＋ 写 ＋ 写后复核）。

## 这一层收的是哪件事

产品操作集只有**三种原子动作**（AGENTS §0.10：新增 / 删除 / 修改；⛔ 没有「换」）。
`object set/add/delete` 收的是**对象级**（一个 pathID 的字节）；`object container` 收的是
**地址级**（`AssetBundle.m_Container`）。这两条**都到不了「树的形状」**：

    往一棵 prefab 里加一个节点  = 新增 GameObject + Transform（+ 可选 renderer）
                                 ＋ **父 Transform 的 m_Children 变长**
                                 ＋ **新对象进 AssetBundle.m_PreloadTable**
    给一个节点加一颗组件        = 新增那个组件对象 ＋ **宿主 GameObject 的 m_Component 变长**
    删掉一个节点                = 父 m_Children 变短 ＋ 子树那些对象**摘出对象表**

**这就是本模块**（L7 的第二半）。它**不新造写盘机制** —— 全部落在既有那条 Δ 链上：

* 变长 → `writer.ObjectGrowth`；新 pathID → `writer.NewObject`；两件必须**落在同一次**写回里
  （少一半就是「有爹没孩子」）；
* 装配 → `clone.delta_change`（`clone.plan_clone_from_bundle` 那条 W8-A 路已真机验过）；
* 预载表 → `clone.preload_growth`（三条守卫：pathID=1 必须是 AssetBundle / 不许重号 / 往返自证）；
* 删除 → `writer.write_removals`（`ObjectRemoval(refs='trim')` 会一并摘
  `m_Container` / `m_PreloadTable` 里的条目 —— 真机铁律：只摘对象表条目会让**整个包加载失败**）。

## 两个连在一起的闸（fail-closed）

1. **写前**：新对象里每一个 PPtr 都要在「包内已有 ＋ 本次新增」里解得到
   （`clone.check_refs` 那把尺子），自造的字段树必须**来回往返一致**；
2. **写后**：从盘上**读回**，逐条比（父 `m_Children`、新对象的 `m_Name`/`m_Father`/`m_Component`、
   预载表条数）—— 见 `verify_plan`。⛔ 干跑与真写走**同一份** `NodePlan`，读数逐行相同。

## 删除为什么是**两步**

`writer` 里「变长」与「删除」是**两条链**（`write_delta` / `write_removals`，Δ 档与等长档），
一次调用混不了。所以 `plan_delete_node` 出**两段**：

    ① growths：父 Transform 的 m_Children 摘掉这一颗；
    ② removals：子树那些对象摘出对象表（并 trim 预载表/容器的条目）。

顺序是**先 ① 后 ②**：① 落完失败在 ② 上，留下的是**没人引用的孤儿对象**（Unity 无害）；
反过来则会出现「父还指着已经摘掉的对象」（悬空 PPtr = 游戏侧取不到）。⛔ 不许反着来。

## 边界

* 本模块**只**做这四个动作，⛔ 不改材质 / 网格 / 皮肤表（各有自己的模块）；
* **不碰** `clone.py` / `writer.py` / `prefab_build.py`（共享文件，只读借用）；
* 不给「换节点」「换组件」这类复合入口 —— 要换就**删旧的 ＋ 加新的**。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError, BaModMakerError

__all__ = [
    "CLASS_GAME_OBJECT",
    "CLASS_TRANSFORM",
    "AssemblyObject",
    "AssemblyView",
    "NodePlan",
    "open_assembly",
    "plan_add_component",
    "plan_add_node",
    "plan_delete_component",
    "plan_delete_node",
    "verify_plan",
    "write_plan",
]

CLASS_GAME_OBJECT = clone.CLASS_GAME_OBJECT
CLASS_TRANSFORM = clone.CLASS_TRANSFORM
CLASS_MONO_BEHAVIOUR = 114
CLASS_NAMES = {
    CLASS_GAME_OBJECT: "GameObject",
    CLASS_TRANSFORM: "Transform",
    23: "MeshRenderer",
    33: "MeshFilter",
    65: "BoxCollider",
    CLASS_MONO_BEHAVIOUR: "MonoBehaviour",
    137: "SkinnedMeshRenderer",
    205: "LODGroup",
}

#: 新增节点时 `--kind` 只认这两种（其余一律抛；⛔ 不猜）。
KINDS = ("empty", "mesh")


def _class_name(class_id: int) -> str:
    return CLASS_NAMES.get(int(class_id), f"class{int(class_id)}")


@dataclass(frozen=True, slots=True)
class AssemblyObject:
    """包里的一个对象（读出来的字段树 ＋ 它的类型表项号）。"""

    path_id: int
    class_id: int
    type_id: int
    fields: Mapping[str, Any]

    @property
    def type_name(self) -> str:
        return _class_name(self.class_id)

    def label(self) -> str:
        name = self.fields.get("m_Name")
        return f"{self.type_name}:{self.path_id}" + (f"({name!r})" if name else "")


@dataclass(slots=True)
class AssemblyView:
    """一个包的**只读**装配视图：对象表 / 类型表 / Transform 树 / GameObject 的组件。"""

    bundle: str
    schema: Any
    rows: Mapping[int, tuple[int, int, int]]
    #: 开包那几件（由 `_AssemblyContext` 填；`with` 出去就关，⛔ 别存着用）
    reader: Any = field(default=None, repr=False)
    node: Any = field(default=None, repr=False)
    info: Any = field(default=None, repr=False)
    pool: Any = field(default=None, repr=False)
    _class_of: dict[int, int] = field(default_factory=dict, repr=False)
    _nodes_by_type: dict[int, Any] = field(default_factory=dict, repr=False)
    _cache: dict[int, AssemblyObject | None] = field(default_factory=dict, repr=False)

    def type_id(self, class_id: int) -> int:
        """classID -> 类型表项号（一个 classID 多项时**抛**，⛔ 不猜）。"""
        return int(self.schema.type_id(class_id))

    def nodes_for(self, type_id: int) -> Any:
        """**按对象表里的 typeID** 取类型树。

        ⛔ 不许按 classID 取：MonoBehaviour(114) 在真包里有 22 项、项号与 script_index 是**置换**
        （`prefab_build` 模块头第 2 条实测）—— 按 classID 取必取错，`TypeSchema.entry` 那种
        「多项就抛」正是为了挡住这一步。
        """
        tid = int(type_id)
        if tid not in self._nodes_by_type:
            entry = next((e for e in self.schema.entries if e.index == tid), None)
            if entry is None:
                raise AssetError(f"typeID={tid} 不在类型表里")
            self._nodes_by_type[tid] = entry.nodes
        return self._nodes_by_type[tid]

    # ---- 读 ----
    def class_of(self, path_id: int) -> int:
        pid = int(path_id)
        if pid not in self.rows:
            raise AssetError(
                f"包里没有 pathID={pid}",
                hint="先 object node list（或 --node 给一个真在包里的 pathID）",
            )
        if pid not in self._class_of:
            type_id = int(self.rows[pid][2])
            entry = next((e for e in self.schema.entries if e.index == type_id), None)
            if entry is None:
                raise AssetError(f"pathID={pid} 的 typeID={type_id} 不在类型表里")
            self._class_of[pid] = int(entry.class_id)
        return self._class_of[pid]

    def object_at(self, path_id: int) -> AssemblyObject:
        pid = int(path_id)
        if pid not in self._cache:
            self._cache[pid] = self._read(pid)
        got = self._cache[pid]
        if got is None:
            raise AssetError(f"pathID={pid} 读不出字段树")
        return got

    def _read(self, pid: int) -> AssemblyObject | None:
        class_id = self.class_of(pid)
        type_id = int(self.rows[pid][2])
        nodes = self.nodes_for(type_id)
        if not nodes:
            return None
        payload = clone.read_object_payload(self.reader, self.node, self.info, pid)
        fields = clone.read_object(payload, nodes, ref_types=self.pool)
        return AssemblyObject(
            path_id=pid, class_id=class_id, type_id=type_id, fields=fields
        )

    # ---- Transform 树 ----
    def transform_ids(self) -> tuple[int, ...]:
        return tuple(
            pid for pid in sorted(self.rows) if self.class_of(pid) == CLASS_TRANSFORM
        )

    def children_of(self, transform: int) -> tuple[int, ...]:
        obj = self.object_at(transform)
        return tuple(int(p["m_PathID"]) for p in (obj.fields.get("m_Children") or ()))

    def game_object_of(self, transform: int) -> int:
        return int(self.object_at(transform).fields["m_GameObject"]["m_PathID"])

    def transform_of(self, game_object: int) -> int | None:
        for pid in self.components_of(game_object):
            if self.class_of(pid) == CLASS_TRANSFORM:
                return pid
        return None

    def components_of(self, game_object: int) -> tuple[int, ...]:
        obj = self.object_at(game_object)
        return tuple(
            int(pair["component"]["m_PathID"])
            for pair in (obj.fields.get("m_Component") or ())
        )

    def subtree(self, transform: int) -> tuple[int, ...]:
        """这一颗 ＋ 它所有后代（前序；父一定排在自己的孩子前面）。"""
        out: list[int] = []
        stack = [int(transform)]
        seen: set[int] = set()
        while stack:
            tid = stack.pop()
            if tid in seen:
                raise AssetError(f"transform {tid} 在树里出现两次（有环？）")
            seen.add(tid)
            out.append(tid)
            stack.extend(reversed(self.children_of(tid)))
        return tuple(out)

    def path_of(self, transform: int) -> str:
        """从**没有父亲的那一颗**起拼路径（`US_X/root/body`）。"""
        names: list[str] = []
        cursor = int(transform)
        guard = 0
        while True:
            go = self.game_object_of(cursor)
            names.append(str(self.object_at(go).fields.get("m_Name") or ""))
            father = int(self.object_at(cursor).fields["m_Father"]["m_PathID"])
            if not father:
                break
            cursor = father
            guard += 1
            if guard > len(self.rows):
                raise AssetError(f"transform {transform} 的父链走不完（有环？）")
        return "/".join(reversed(names))

    def resolve(self, ref: Any) -> int:
        """`--node` / `--parent` 的落点：**整数 pathID** 或**路径**（`US_X/root/body`）。"""
        text = str(ref).strip()
        if text.lstrip("-").isdigit():
            pid = int(text)
            if pid not in self.rows:
                raise AssetError(f"包里没有 pathID={pid}")
            if self.class_of(pid) == CLASS_GAME_OBJECT:
                tr = self.transform_of(pid)
                if tr is None:
                    raise AssetError(f"GameObject {pid} 上没有 Transform（不是合法节点）")
                return tr
            if self.class_of(pid) != CLASS_TRANSFORM:
                raise AssetError(
                    f"pathID={pid} 是 {_class_name(self.class_of(pid))}，不是节点",
                    hint="--node / --parent 收的是 Transform 的 pathID 或节点路径",
                )
            return pid
        hits = [tid for tid in self.transform_ids() if self.path_of(tid) == text]
        if not hits:
            raise AssetError(
                f"找不到路径 {text!r}",
                hint="路径从顶层节点名起（如 US_T72B/root/body）；先 object node list 看一遍",
            )
        if len(hits) > 1:
            raise AssetError(
                f"路径 {text!r} 命中 {len(hits)} 个节点（重名兄弟）",
                hint=f"改用 pathID：{hits}（⛔ 本模块不猜你要哪一个）",
            )
        return hits[0]

    def node_lines(self) -> tuple[str, ...]:
        """`object node list` 的读数（只读）。"""
        out = []
        for tid in self.transform_ids():
            go = self.game_object_of(tid)
            comps = ", ".join(
                f"{_class_name(self.class_of(c))}:{c}" for c in self.components_of(go)
            )
            out.append(f"{tid}\t{self.path_of(tid)}\t{comps}")
        return tuple(out)


class _AssemblyContext:
    """开一次包读装配视图用（`with` 里活，出去就关）。"""

    def __init__(self, bundle: str | Path) -> None:
        self.bundle = str(bundle)

    def __enter__(self) -> AssemblyView:
        self._reader = ufs.UfsReader(self.bundle)
        reader = self._reader.__enter__()
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        blob = reader.read_range(node.offset, info.data_offset)
        common = clone.unitypy_common_strings()
        schema = clone.read_type_table(blob, common_strings=common)
        view = AssemblyView(bundle=self.bundle, schema=schema,
                            rows=clone.object_rows(info))
        view.reader = reader
        view.node = node
        view.info = info
        # ⚠ `ref_type_pool` 吃的是**解析过**的元数据（不是裸字节）—— 口径与
        #   `unit_bundle.verify_unit_bundle` 那一条完全一样（[SerializeReference] 的类型池）。
        view.pool = clone.ref_type_pool(
            _parse_metadata(blob), common_strings=common
        )
        return view

    def __exit__(self, *exc: object) -> None:
        self._reader.__exit__(*exc)


def open_assembly(bundle: str | Path) -> _AssemblyContext:
    """`with open_assembly(<包>) as view:` —— 只读的装配视图（退出时关包）。"""
    return _AssemblyContext(bundle)


#: `metadata_writer` 那条「恰好落在声明末尾」的报错前缀（**照着它**判「只是尾隙」）。
_TAIL_GAP_ERROR = "元数据解析没有落在声明的末尾"


def _parse_metadata(blob: bytes) -> Any:
    """解析元数据区；**只**容忍一种偏差：声明区**尾部有隙**（删除链留下的规整尾零）。

    为什么必须容忍：`writer._removal_entries` 的定律是「摘掉 N 条对象表条目 ⇒ 表后元数据
    整体左移 24N ⇒ **声明区尾部补 24N 个零**、声明长度不动」，而它的依据正是
    「这份格式的声明区尾部**本来就有隙**」（units 真件有 24,504 B 的类型树池不被解析器消费，
    见 `writer.py:4283`）。⇒ 对**任何删过对象的包**，`parse_serialized_metadata(verify=True)`
    的「恰好落在末尾」那条**必然**不成立（实测：差 −48 B，正好 2 条对象表条目）。

    ⛔ 只放过「解析走得比声明**短**」这一条（报错里有 `（差 -`）；越界、截断、类型表走偏
    与别的结构性异常一律照抛。
    """
    from ba_mod_maker.asset_kernel import metadata_writer  # noqa: PLC0415

    try:
        return metadata_writer.parse_serialized_metadata(blob)
    except AssetError as exc:
        if _TAIL_GAP_ERROR not in str(exc) or "（差 -" not in str(exc):
            raise
        return metadata_writer.parse_serialized_metadata(blob, verify=False)


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NodePlan:
    """一次装配动作的全部产出（干跑与真写走**同一份**）。"""

    verb: str
    bundle: str
    summary: str
    #: 一到两段写回：`stages[i] = ("delta"|"removals", 载荷)`，**按顺序**落
    stages: tuple[tuple[str, Any], ...]
    readings: tuple[str, ...]
    expect: Mapping[str, Any] = field(default_factory=dict)

    def criterion_lines(self) -> tuple[str, ...]:
        head = f"[{self.verb}] {self.summary}"
        kinds = " -> ".join(k for k, _ in self.stages)
        return (head, *self.readings, f"  写回段：{len(self.stages)} 段（{kinds}）")


def _default_fields(view: AssemblyView, type_id: int) -> dict[str, Any]:
    """按**包内那棵类型树**造一棵完整默认字段树（逻辑只有一份：`prefab_build.default_fields`）。"""
    from ba_mod_maker.asset_kernel import prefab_build  # noqa: PLC0415 -- 借它的默认值那条口径

    nodes = view.nodes_for(int(type_id))
    if not nodes:
        raise AssetError(f"类型表项 {type_id} 没有树")
    return dict(prefab_build.default_fields(nodes))


def _encode(view: AssemblyView, type_id: int, fields: Mapping[str, Any], *, what: str) -> bytes:
    """字段树 -> 字节，并**当场来回一遍**（读出来必须与写进去一致）。"""
    nodes = view.nodes_for(int(type_id))
    payload = clone.write_object(dict(fields), nodes)
    back = clone.read_object(payload, nodes)
    if dict(back) != dict(fields):
        raise AssetError(
            f"{what}：字段树 -> 字节 -> 字段树 不一致",
            hint="类型树与字段对不上（拿错包/拿错类）；⛔ 这种字节不许落地",
        )
    return bytes(payload)


def _growth(view: AssemblyView, path_id: int, new_fields: Mapping[str, Any], *, why: str) -> Any:
    """改一个**已经在表里**的对象（长度必须真的变了）。"""
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415 -- 与 clone 的循环延迟

    obj = view.object_at(path_id)
    payload = _encode(view, obj.type_id, new_fields, what=why)
    if payload == _encode(view, obj.type_id, obj.fields, what=why + "（旧值）"):
        raise AssetError(f"{why}：新字节与旧字节逐字节相同 ⇒ 这次 growth 是空动作")
    return writer.ObjectGrowth(
        path_id=int(path_id), payload=payload, name=obj.label(), type_name=obj.type_name
    )


def _new_object(
    view: AssemblyView,
    path_id: int,
    type_id: int,
    fields: Mapping[str, Any],
    name: str,
) -> Any:
    """一个 `NewObject`；**type_id 必须由调用方点名**（classID 一项多项时不许按 classID 取）。"""
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    class_id = _class_of_type(view, type_id)
    return writer.NewObject(
        path_id=int(path_id),
        type_id=int(type_id),
        payload=_encode(view, type_id, fields, what=f"新增 {_class_name(class_id)}"),
        name=name,
        type_name=_class_name(class_id),
    )


def _class_of_type(view: AssemblyView, type_id: int) -> int:
    entry = next((e for e in view.schema.entries if e.index == int(type_id)), None)
    if entry is None:
        raise AssetError(f"typeID={type_id} 不在类型表里")
    return int(entry.class_id)


def _preload_growth(view: AssemblyView, planned: Sequence[tuple[int, int, str]]) -> Any:
    """新对象进 `AssetBundle.m_PreloadTable`（借 `clone.preload_growth` 那三条守卫）。

    `planned` = 本次**新增**的那些 `(pathID, classID, 名字)`。⚠ 它们**还没进对象表**，
    所以这里不许拿 `view.class_of` / `view.object_at` 去问（那是问「包里现在有没有」）——
    第一版就是这么写的，探针当场报「包里没有 pathID=…」。
    """
    graph = clone.CloneGraph(
        schema=view.schema,
        target=clone.CloneTarget(parent_transform=0, mesh=0, materials=()),
        objects=tuple(
            clone.CloneObject(
                path_id=int(pid),
                class_id=int(class_id),
                type_id=0,
                type_name=_class_name(class_id),
                name=str(name),
                fields={},
                payload=b"",
            )
            for pid, class_id, name in planned
        ),
        parent=clone.CloneObject(
            path_id=0, class_id=CLASS_TRANSFORM, type_id=0, type_name="Transform",
            name="<none>", fields={}, payload=b"",
        ),
        parent_children_before=(),
        parent_children_after=(),
        package_ids=frozenset(view.rows) | {int(p) for p, _c, _n in planned},
    )
    return clone.preload_growth(view.bundle, graph)


def _check_refs(
    view: AssemblyView,
    objects: Mapping[str, tuple[Mapping[str, Any], Any]],
    new_ids: Sequence[int],
    what: str,
) -> str:
    """写前那把尺子：新对象里每个 PPtr 都要在「包内已有 ＋ 本次新增」里解得到。"""
    known = frozenset(view.rows) | {int(p) for p in new_ids}
    report = clone.check_refs(objects, known)
    if report.dangling:
        raise AssetError(
            f"{what}：新对象有 {len(report.dangling)} 条悬空 PPtr",
            hint="; ".join(f"{r.owner}.{r.field} -> {r.path_id}" for r in report.dangling[:4]),
        )
    if report.external:
        raise AssetError(
            f"{what}：新对象有 {len(report.external)} 条**跨文件**引用",
            hint="本模块口径：新对象之间只许包内引用（fileID=0）",
        )
    return (
        f"  写前 PPtr 普查：{len(report.refs)} 条引用，**悬空 0**、跨文件 0、"
        f"空引用 {len(report.null)}"
    )


# --------------------------------------------------------------------------- #
# ① 新增节点
# --------------------------------------------------------------------------- #
def plan_add_node(
    bundle: str | Path,
    *,
    parent: Any,
    name: str,
    kind: str = "empty",
    mesh: int | None = None,
    materials: Sequence[int] = (),
    position: Sequence[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
    scale: Sequence[float] = (1.0, 1.0, 1.0),
) -> NodePlan:
    """**新增一个节点**：GameObject ＋ Transform（`kind=mesh` 再加 MeshFilter ＋ MeshRenderer）。

    一次写回里三件一起落：新对象（`NewObject`）＋ 父 `m_Children` 变长（`ObjectGrowth`）
    ＋ 新对象进 `m_PreloadTable`（`ObjectGrowth`）。
    """
    from ba_mod_maker.asset_kernel import prefab_build, writer  # noqa: PLC0415

    if kind not in KINDS:
        raise AssetError(f"kind={kind!r} 不认（只有 {KINDS}）")
    if not str(name).strip():
        raise AssetError("新节点必须有名字（引擎按名字找节点）")
    if kind == "mesh" and not mesh:
        raise AssetError("kind=mesh 要 --mesh <包内 Mesh 的 pathID>")

    with open_assembly(bundle) as view:
        parent_tr = view.resolve(parent)
        ids = clone.allocate_path_ids(view.rows, 2 if kind == "empty" else 4)
        new_tr, new_go = int(ids[0]), int(ids[1])
        type_tr, type_go = view.type_id(CLASS_TRANSFORM), view.type_id(CLASS_GAME_OBJECT)
        #: (pathID, typeID, 字段树) —— ⛔ 一律带 typeID：classID 一项多项时不许按 classID 取
        extra: list[tuple[int, int, Mapping[str, Any]]] = []
        if kind == "mesh":
            new_mf, new_mr = int(ids[2]), int(ids[3])
            if int(mesh) not in view.rows:
                raise AssetError(f"mesh pathID={mesh} 不在包里（悬空 PPtr）")
            for m in materials:
                if int(m) not in view.rows:
                    raise AssetError(f"material pathID={m} 不在包里（悬空 PPtr）")
            mf = _default_fields(view, view.type_id(clone.CLASS_MESH_FILTER))
            mf["m_GameObject"] = clone.pptr(new_go)
            mf["m_Mesh"] = clone.pptr(int(mesh))
            extra.append((new_mf, view.type_id(clone.CLASS_MESH_FILTER), mf))
            mr = _default_fields(view, view.type_id(clone.CLASS_MESH_RENDERER))
            mr["m_GameObject"] = clone.pptr(new_go)
            mr["m_Enabled"] = True
            mr["m_Materials"] = [clone.pptr(int(m)) for m in materials]
            prefab_build.apply_renderer_engine_defaults(mr, clone.CLASS_MESH_RENDERER)
            extra.append((new_mr, view.type_id(clone.CLASS_MESH_RENDERER), mr))

        tr_fields = _default_fields(view, type_tr)
        tr_fields["m_GameObject"] = clone.pptr(new_go)
        tr_fields["m_Children"] = []
        tr_fields["m_Father"] = clone.pptr(parent_tr)
        tr_fields["m_LocalPosition"] = clone.vec3(*position)
        tr_fields["m_LocalRotation"] = clone.vec4(*rotation)
        tr_fields["m_LocalScale"] = clone.vec3(*scale)

        go_fields = _default_fields(view, type_go)
        go_fields["m_Name"] = str(name)
        if "m_IsActive" in go_fields:
            go_fields["m_IsActive"] = True
        go_fields["m_Component"] = [
            {"component": clone.pptr(new_tr)},
            *[{"component": clone.pptr(pid)} for pid, _c, _f in extra],
        ]

        added = []
        refs: dict[str, tuple[Mapping[str, Any], Any]] = {}
        for pid, type_id, fields in [
            (new_tr, type_tr, tr_fields),
            (new_go, type_go, go_fields),
            *extra,
        ]:
            added.append(_new_object(view, pid, type_id, fields, str(name)))
            refs[f"{_class_name(_class_of_type(view, type_id))}:{pid}"] = (
                fields, view.nodes_for(type_id)
            )

        parent_fields = dict(view.object_at(parent_tr).fields)
        before = tuple(int(p["m_PathID"]) for p in (parent_fields.get("m_Children") or ()))
        if new_tr in before:
            raise AssetError(f"父 {parent_tr} 的 m_Children 里已经有 {new_tr} 了")
        parent_fields["m_Children"] = [
            *[clone.pptr(p) for p in before], clone.pptr(new_tr)
        ]
        parent_growth = _growth(
            view, parent_tr, parent_fields, why=f"父 {parent_tr} 的 m_Children 加一颗"
        )
        preload = _preload_growth(
            view,
            [(int(o.path_id), _class_of_type(view, o.type_id), str(name)) for o in added],
        )
        ref_line = _check_refs(view, refs, [int(o.path_id) for o in added], "新增节点")
        readings = (
            f"父 Transform {parent_tr}（{view.path_of(parent_tr)}）："
            f"m_Children {len(before)} -> {len(before) + 1}",
            f"新对象 {len(added)} 个："
            + "，".join(
                f"{o.type_name}:{o.path_id}({len(o.payload):,} B)" for o in added
            ),
            f"新节点：{view.path_of(parent_tr)}/{name}（kind={kind}）",
            f"  预载表 {preload.preload_before} -> {preload.preload_after}"
            f"（+{len(preload.path_ids)}）",
            f"  AssetBundle 字节 {preload.payload_before:,} -> {preload.payload_after:,} B",
            ref_line,
        )
        change = writer.DeltaChange(
            growths=(parent_growth, preload.growth), added=tuple(added)
        )
        return NodePlan(
            verb="node add",
            bundle=str(bundle),
            summary=f"新增节点 {view.path_of(parent_tr)}/{name}（{kind}）",
            stages=(("delta", change),),
            readings=readings,
            expect={
                "parent": parent_tr,
                "name": str(name),
                "new_transform": new_tr,
                "new_objects": tuple(int(o.path_id) for o in added),
                "children_before": before,
                "preload_before": preload.preload_before,
            },
        )


# --------------------------------------------------------------------------- #
# ② 加组件 / 删组件
# --------------------------------------------------------------------------- #
def plan_add_component(
    bundle: str | Path,
    *,
    node: Any,
    like: int | None = None,
    class_id: int | None = None,
    type_id: int | None = None,
    name: str = "",
) -> NodePlan:
    """**给一个节点加一颗组件**（宿主 GameObject 的 `m_Component` 变长）。

    两种给法（**正好给一个**）：

    * `like=<包内某个组件的 pathID>` —— **照它造**（类型树 ＋ `m_Script` 都照抄）⇒
      这一条是 MonoBehaviour 的唯一正路：我们自己包里**没有** MonoScript 对象，凭空写一个
      pathID 就是造悬空引用（出厂闸 A0 会当场红）；
    * `class_id` / `type_id` —— 只用类型树的**默认值**。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    with open_assembly(bundle) as view:
        host_tr = view.resolve(node)
        host_go = view.game_object_of(host_tr)
        if like is not None:
            src = view.object_at(int(like))
            fields = dict(src.fields)
            tgt_class, tgt_type = src.class_id, src.type_id
        else:
            if class_id is None and type_id is None:
                raise AssetError("加组件要给 --like <包内既有组件> 或 --class-id <N>")
            if type_id is not None:
                entry = next((e for e in view.schema.entries if e.index == int(type_id)), None)
                if entry is None:
                    raise AssetError(f"类型表里没有下标 {type_id}")
                tgt_class, tgt_type = int(entry.class_id), int(type_id)
            else:
                hits = [e.index for e in view.schema.entries if int(e.class_id) == int(class_id)]
                if not hits:
                    raise AssetError(f"类型表里没有 classID={class_id}")
                if len(hits) > 1:
                    raise AssetError(
                        f"classID={class_id} 有 {len(hits)} 棵类型树（下标 {hits}）—— 必须点名",
                        hint="MonoBehaviour 就是这种；用 --type-id，或用 --like 照一个既有组件造",
                    )
                tgt_class, tgt_type = int(class_id), int(hits[0])
            fields = _default_fields(view, tgt_type)
        if tgt_class == CLASS_MONO_BEHAVIOUR and not fields.get("m_Script", {}).get("m_PathID"):
            raise AssetError(
                "这颗 MonoBehaviour 的 m_Script 给不出来（空 PPtr）",
                hint="用 --like <包内同类的既有组件> 照造 —— ⛔ 不许凭空写 pathID（A0 会红）",
            )
        if tgt_class == CLASS_TRANSFORM:
            raise AssetError("Transform 不是「加组件」能加的东西（它是节点的本体）")

        new_id = int(clone.allocate_path_ids(view.rows, 1)[0])
        fields["m_GameObject"] = clone.pptr(host_go)
        label = str(name) or str(fields.get("m_Name") or "")
        new_obj = _new_object(view, new_id, tgt_type, fields, label)

        go_fields = dict(view.object_at(host_go).fields)
        comps_before = view.components_of(host_go)
        # ⚠ `m_Component` 的元素是 **ComponentPair**（`{component: PPtr}`），不是裸 PPtr ——
        #   第一版漏了这层壳，`clone.write_object` 当场报「字段缺失 ComponentPair.component」。
        go_fields["m_Component"] = [
            *[{"component": clone.pptr(c)} for c in comps_before],
            {"component": clone.pptr(new_id)},
        ]
        host_growth = _growth(
            view, host_go, go_fields, why=f"宿主 {host_go} 的 m_Component 加一颗"
        )
        preload = _preload_growth(view, [(new_id, tgt_class, label)])
        refs = {f"{_class_name(tgt_class)}:{new_id}": (fields, view.nodes_for(tgt_type))}
        ref_line = _check_refs(view, refs, [new_id], "加组件")
        readings = (
            f"宿主节点：{view.path_of(host_tr)}（GameObject {host_go}）",
            f"m_Component {len(comps_before)} -> {len(comps_before) + 1}",
            f"新组件：{_class_name(tgt_class)}:{new_id}（{len(new_obj.payload):,} B"
            + (f"，照 {like} 造" if like is not None else "，默认值")
            + "）",
            f"  预载表 {preload.preload_before} -> {preload.preload_after}",
            ref_line,
        )
        change = writer.DeltaChange(growths=(host_growth, preload.growth), added=(new_obj,))
        return NodePlan(
            verb="component add",
            bundle=str(bundle),
            summary=f"给 {view.path_of(host_tr)} 加一颗 {_class_name(tgt_class)}",
            stages=(("delta", change),),
            readings=readings,
            expect={
                "host_game_object": host_go,
                "new_component": new_id,
                "components_before": comps_before,
            },
        )


def plan_delete_component(bundle: str | Path, *, node: Any, component: int) -> NodePlan:
    """**删掉一个节点上的一颗组件**（宿主 `m_Component` 变短 ＋ 那颗对象摘出对象表）。

    与 `plan_delete_node` 同形：也是**两段**（先 growth 后 removals）。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    with open_assembly(bundle) as view:
        host_tr = view.resolve(node)
        host_go = view.game_object_of(host_tr)
        pid = int(component)
        comps_before = view.components_of(host_go)
        if pid not in comps_before:
            raise AssetError(
                f"GameObject {host_go} 上没有组件 {pid}",
                hint=f"它现在挂着：{comps_before}",
            )
        if view.class_of(pid) == CLASS_TRANSFORM:
            raise AssetError("Transform 删不掉（删整颗节点用 object node delete）")
        keep = [c for c in comps_before if c != pid]
        go_fields = dict(view.object_at(host_go).fields)
        go_fields["m_Component"] = [{"component": clone.pptr(c)} for c in keep]
        host_growth = _growth(
            view, host_go, go_fields, why=f"宿主 {host_go} 的 m_Component 摘一颗"
        )
        removal = writer.ObjectRemoval(path_id=pid, name=_class_name(view.class_of(pid)))
        readings = (
            f"宿主节点：{view.path_of(host_tr)}（GameObject {host_go}）",
            f"m_Component {len(comps_before)} -> {len(keep)}"
            f"（摘掉 {pid} = {_class_name(view.class_of(pid))}）",
            "对象表摘条目：1 条（refs=trim：一并摘 m_PreloadTable / m_Container 里指它的条目）",
        )
        return NodePlan(
            verb="component delete",
            bundle=str(bundle),
            summary=f"从 {view.path_of(host_tr)} 摘掉 {_class_name(view.class_of(pid))}:{pid}",
            stages=(
                ("delta", writer.DeltaChange(growths=(host_growth,))),
                ("removals", (removal,)),
            ),
            readings=readings,
            expect={
                "host_game_object": host_go,
                "components_after": tuple(keep),
                "removed": (pid,),
            },
        )


# --------------------------------------------------------------------------- #
# ③ 删除节点
# --------------------------------------------------------------------------- #
def plan_delete_node(bundle: str | Path, *, node: Any) -> NodePlan:
    """**删掉一颗节点**（整棵子树）。

    两段（顺序不许反，理由见模块头）：① 父 `m_Children` 摘掉它；② 子树对象摘出对象表。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    with open_assembly(bundle) as view:
        tid = view.resolve(node)
        father = int(view.object_at(tid).fields["m_Father"]["m_PathID"])
        if not father:
            raise AssetError(
                f"{view.path_of(tid)} 没有父 Transform（它是顶层节点）",
                hint="顶层节点是 prefab 的根，⛔ 本命令不许删根（那等于删整个 prefab）",
            )
        subtree = view.subtree(tid)
        doomed: list[int] = []
        for tr in subtree:
            go = view.game_object_of(tr)
            doomed.append(go)
            doomed.extend(view.components_of(go))
        doomed = list(dict.fromkeys(doomed))
        parent_fields = dict(view.object_at(father).fields)
        before = tuple(int(p["m_PathID"]) for p in (parent_fields.get("m_Children") or ()))
        if tid not in before:
            raise AssetError(
                f"父 {father} 的 m_Children 里没有 {tid} —— 这棵树与对象表对不上，别动它"
            )
        after = tuple(c for c in before if c != tid)
        parent_fields["m_Children"] = [clone.pptr(c) for c in after]
        parent_growth = _growth(
            view, father, parent_fields, why=f"父 {father} 的 m_Children 摘一颗"
        )
        removals = tuple(
            writer.ObjectRemoval(path_id=pid, name=_class_name(view.class_of(pid)))
            for pid in doomed
        )
        listed = "，".join(f"{_class_name(view.class_of(p))}:{p}" for p in doomed[:8])
        readings = (
            f"删的节点：{view.path_of(tid)}（Transform {tid}）",
            f"父 Transform {father}（{view.path_of(father)}）："
            f"m_Children {len(before)} -> {len(after)}",
            f"子树 {len(subtree)} 个节点 ⇒ 对象表摘 {len(removals)} 条"
            "（各自 trim 预载表/容器里的条目）",
            f"  摘的是：{listed}" + (" …" if len(doomed) > 8 else ""),
        )
        return NodePlan(
            verb="node delete",
            bundle=str(bundle),
            summary=f"删掉节点 {view.path_of(tid)}",
            stages=(
                ("delta", writer.DeltaChange(growths=(parent_growth,))),
                ("removals", removals),
            ),
            readings=readings,
            expect={
                "parent": father,
                "children_after": after,
                "removed": tuple(doomed),
                "deleted_transform": tid,
            },
        )


# --------------------------------------------------------------------------- #
# 写 ＋ 写后复核
# --------------------------------------------------------------------------- #
def preflight_plan(plan: NodePlan) -> tuple[str, ...]:
    """**写前干算**：把每一段都先只读地算一遍（`plan_delta` / `plan_removals`）。

    为什么非要这一步：删除是**两段**（先 growth 后 removals），而两段各有自己的守卫
    （实测撞过：`write_removals` 会因为 `m_Container` 的 preload 区间跨过被摘的槽位而
    fail-closed）。没有这一步就会出现「第 1 段落了、第 2 段被守卫拦住」的半截状态 ——
    方向虽然良性（孤儿对象），但判据一行会说假话（「一个字节都没落」）。
    有了它：**任一段算不出来 ⇒ 本次调用一个字节都不写**。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    out: list[str] = []
    for index, (kind, payload) in enumerate(plan.stages, start=1):
        if kind == "delta":
            planned = writer.plan_delta(plan.bundle, payload)
            added = len(getattr(payload, "added", ()))
            out.append(
                f"  {index}/{len(plan.stages)} delta 段：可算 ✔"
                f"（改 {len(getattr(payload, 'growths', ()))} 个既有对象，新增 {added} 个）"
            )
        elif kind == "removals":
            planned = writer.plan_removals(plan.bundle, payload)
            count = len(payload)
            out.append(
                f"  {index}/{len(plan.stages)} removals 段：可算 ✔（摘 {count} 条对象表条目）"
            )
        else:
            raise AssetError(f"认不出的写回段 {kind!r}")
        reason = str(getattr(planned, "blocked_reason", "") or "")
        if reason:
            raise AssetError(
                f"写前干算被拦（{kind} 段）：{reason}",
                hint=f"{plan.summary} —— 本次调用**一个字节都没写**",
            )
    return tuple(out)


def write_plan(
    plan: NodePlan,
    *,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    catalog_path: str | Path | None = None,
    dry_run: bool = True,
    patch_store: Any = None,
    preflight: bool = True,
) -> tuple[Any, ...]:
    """按顺序落每一段（干跑与真写走**同一份**计划）。返回每段的 `WriteReport`。

    `preflight=True`（默认）时先 `preflight_plan` —— **全段都算得出来才动手**。
    """
    from ba_mod_maker.asset_kernel import reverse_delta, writer  # noqa: PLC0415

    if preflight and not dry_run:
        preflight_plan(plan)

    root = Path(project_root) if project_root else Path.cwd()
    game = Path(game_root) if game_root else None
    store = patch_store
    if store is None and not dry_run:
        store = reverse_delta.PatchStore(reverse_delta.patch_store_root(root), game_root=game)
    common: dict[str, Any] = {
        "dry_run": dry_run,
        "project_root": root,
        "game_root": game,
        "catalog_path": Path(catalog_path) if catalog_path else None,
        "patch_store": store,
        "patch_label": plan.summary,
    }
    out = []
    total = len(plan.stages)
    for index, (kind, payload) in enumerate(plan.stages, start=1):
        try:
            if kind == "delta":
                out.append(writer.write_delta(plan.bundle, payload, **common))
            elif kind == "removals":
                out.append(writer.write_removals(plan.bundle, payload, **common))
            else:
                raise AssetError(f"认不出的写回段 {kind!r}")
        except (BaModMakerError, OSError) as exc:
            done = index - 1
            raise AssetError(
                f"写回第 {index}/{total} 段（{kind}）失败：{exc}",
                hint=(
                    f"{plan.summary}；**前面 {done} 段已经落盘**："
                    + ("、".join(k for k, _p in plan.stages[:done]) if done else "无")
                    + " —— 还原走补丁柜（assets restore）"
                ),
            ) from exc
    return tuple(out)


def _preload_count(view: AssemblyView) -> int:
    for pid in view.rows:
        if view.class_of(pid) == clone.ASSET_BUNDLE_CLASS_ID:
            return len(view.object_at(pid).fields.get("m_PreloadTable") or ())
    raise AssetError("包里没有 AssetBundle 对象（pathID=1 那条）")


def verify_plan(plan: NodePlan) -> tuple[str, ...]:
    """**写后复核**（从盘上读回逐条比）。返回读数；对不上就抛 `AssetError`。"""
    expect = dict(plan.expect)
    out: list[str] = []
    with open_assembly(plan.bundle) as view:
        if plan.verb == "node add":
            parent = int(expect["parent"])
            kids = view.children_of(parent)
            want = (*expect["children_before"], int(expect["new_transform"]))
            if kids != want:
                raise AssetError(f"写后复核：父 m_Children {kids} != 期望 {want}")
            new_tr = int(expect["new_transform"])
            go = view.game_object_of(new_tr)
            got_name = str(view.object_at(go).fields.get("m_Name") or "")
            if got_name != expect["name"]:
                raise AssetError(f"写后复核：新节点名 {got_name!r} != {expect['name']!r}")
            father = int(view.object_at(new_tr).fields["m_Father"]["m_PathID"])
            if father != parent:
                raise AssetError(f"写后复核：m_Father {father} != {parent}")
            comps = view.components_of(go)
            if new_tr not in comps:
                raise AssetError(f"写后复核：新 GameObject 的 m_Component 里没有 {new_tr}")
            out.append(
                f"  读回 父 {parent} 的 m_Children {list(kids)} ✔；"
                f"新节点 {view.path_of(new_tr)} 组件 {len(comps)} 个 ✔"
            )
            out.append(f"  读回 AssetBundle.m_PreloadTable {_preload_count(view)} 条")
        elif plan.verb in ("component add", "component delete"):
            host = int(expect["host_game_object"])
            comps = view.components_of(host)
            if "new_component" in expect and int(expect["new_component"]) not in comps:
                raise AssetError(f"写后复核：m_Component 里没有新组件，实测 {comps}")
            if "components_after" in expect and comps != tuple(expect["components_after"]):
                raise AssetError(
                    f"写后复核：m_Component {comps} != 期望 {expect['components_after']}"
                )
            out.append(f"  读回 GameObject {host} 的 m_Component {list(comps)} ✔")
            if "new_component" in expect:
                out.append(f"  读回 AssetBundle.m_PreloadTable {_preload_count(view)} 条")
        elif plan.verb == "node delete":
            parent = int(expect["parent"])
            kids = view.children_of(parent)
            if kids != tuple(expect["children_after"]):
                raise AssetError(f"写后复核：父 m_Children {kids} != {expect['children_after']}")
            out.append(f"  读回 父 {parent} 的 m_Children {list(kids)}（已摘掉那一颗）✔")
        for pid in expect.get("removed", ()):
            if int(pid) in view.rows:
                raise AssetError(f"写后复核：pathID={pid} 还在对象表里（删除没落）")
        if expect.get("removed"):
            out.append(f"  读回 对象表：{len(expect['removed'])} 条已摘 ✔")
    return tuple(out)

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   kb/14-资产写回-新增与删除.md（产品操作集＝新增 / 删除 / 修改；⛔ 没有「换」）
# --------------------------------------------------------------------------- #
