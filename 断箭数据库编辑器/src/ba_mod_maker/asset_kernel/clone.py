"""W8-B · 克隆引用图：用包内类型树当 schema，造一个 MeshRenderer 零件（4 个新 pathID）。

## 这一模块解决什么

工单 §2 的目标形态是「克隆一个现成零件，参数改掉，游戏里能看到两份」。本模块出**克隆图**
（四个新对象的字节）与「新对象进 Preload 表」那一次 growth（`preload_growth`）；
落盘——对象表插条目 ＋ payload 搬 node[0] 尾部——由 `writer.write_delta` 做，装配见 `delta_change`：

    Transform + GameObject + MeshFilter + MeshRenderer   四个新 pathID
    Mesh / Material 都引用**包内已有对象**（fileID = 0，不声明依赖、不搬对象）
    父 Transform 的 m_Children 追加新 Transform（那是 growth，走 W7 的 ObjectGrowth）

## 口径：类型树是**从包里读出来的**，不是猜的（设计稿 §9.5 步骤 2）

    reader -> node[0] 的元数据区 -> read_type_table() -> TypeSchema（每个 classID 的字段树）
    TypeSchema + 模板对象字节 -> read_object() -> 字段树（dict）-> 改几个字段 -> write_object()

所以本模块**零字段硬编码**：Transform 有哪些字段、MeshRenderer 的 m_Materials 在哪、
数组元素多宽，全部来自包里内嵌的类型树。字段名与布局随游戏版本变也不用改一行。

## [SerializeReference]（2026-10-06 · G1）：`data` 的布局在**另一个地方**

带 `[SerializeReference]` 的类型树里，引用长这样（units 包 AnimationHub / type_id 15）：

    references: ManagedReferencesRegistry
      version: int
      RefIds: vector -> ReferencedObject      # 一条引用
        rid: SInt64                           # 引用号（状态数组按它对账）
        type: ReferencedManagedType           # 三个字符串：class / ns / asm
        data: ReferencedObjectData            # **一个子节点都没有** —— 布局不在这棵树里

`data` 的布局由同一条里先读出来的 `type.{class,ns,asm}` 去本文件的 `m_RefTypes` 里
**按三个字符串名**点一棵树决定（⛔ 不是下标）。所以读/写这类对象必须把那个池子传进来：

    pool = clone.ref_type_pool(meta)      # meta = metadata_writer 读出来的 SerializedMetadata
    fields = clone.read_object(payload, nodes, ref_types=pool)
    payload2 = clone.write_object(fields, nodes, ref_types=pool)

* 不给池子 -> 抛 `AssetError` 并**点名 ref_types**（⛔ 不许静默跳过 data）；
* 池子里找不到那个三元组 -> 抛（对不上就是「这块数据不是本文件的」）；
* 空引用（`type.class` 是空串）**一个字节都不占**，字段树里连 `data` 这个键都没有；
* 池子的造法只有一份：`ref_type_pool()`（走 metadata_writer 的 `encode_type_tree` + 本模块的
  `read_type_nodes`）—— 类型树的解析器全仓就这一份。

实测（units 包 608 个 AnimationHub）：修前 **4 读得了 / 604 抛**（`ReferencedObjectData data`）；
修后 **608 读得了**、`write_object(read_object(x)) == x` **608/608 逐字节相同**，
与 UnityPy 的字段树 / 字节两头一致（复算 `_work/g1-ref-probe.py`）。

## 实测（2026-10-03，units 包，自己读的；_work/w8_clone_probe_types.py / _probe_schema.py）

| 项 | 读数 |
|---|---|
| 类型表 | 50 项；我们的解析器与 UnityPy 的 class_id **逐项相等** |
| 类型表落点 | 215,744 = 对象表 215,748 - 4（那 4 字节就是对象数那一格） |
| type_id（classID -> 下标） | GameObject **4** / Transform **1** |
| 同上 | MeshRenderer **13** / MeshFilter **6** |
| 内嵌类型树的字符串 | **7903 处**是「公共串表」引用（高位 = 1）=> 必须传 common_strings，不许猜 |
| 模板对象字节 | pick_templates 挑的：Transform 68 / GameObject 31 |
| 同上 | MeshFilter 24 / MeshRenderer 156 |
| 录进用例的样本 | lod_1x1x1 那四个真对象 = 68 / **63** / 24 / 156 B（都逐字节往返成功） |
| roundtrip 抽检 | 每个类 48 个真对象，**192/192 逐字节相同** |
| 同上 | 读数见 _work/w8_clone_probe_plan.py 的 roundtrip_sweep |
| 四个类的节点数 | GameObject 15 / Transform 26 / MeshRenderer 55 / MeshFilter 7（含子节点） |
| 对齐位 | kAlignBytes = **0x4000**（与 UnityPy 的 metaflag_is_aligned 同口径） |

## 实测（克隆图，T-72B：父 = Chassis、mesh = RU_TanktrakR、材质 = RU_T72B_track）

| 项 | 读数 |
|---|---|
| 新对象 1 | Transform pathID 9223358220759698760（68 B） |
| 新对象 2 | GameObject pathID 9223358220759698768（71 B） |
| 新对象 3 | MeshFilter pathID 9223358220759698776（24 B） |
| 新对象 4 | MeshRenderer pathID 9223358220759698784（156 B） |
| 父 Chassis 的 m_Children | **7 -> 8**（顺序保持，新 Transform 接在末尾） |
| 父对象字节 | 152 -> 164 B（growth 走 ObjectGrowth） |
| 引用判据 J4 | 14 条 PPtr：包内 **9** / 空 5 / 跨文件 0 / 悬空 **0** |
| 判定往返（判据 5） | 四个新对象的字节都能用类型树读回、字段逐个相等 |
| **独立读回** | UnityPy 读我们造的 4 份字节，字段与我们的字段树**逐个相等**（跨实现对拍） |
| 负控 | 把 m_Mesh 指到不存在的 pathID -> 悬空 **1** 条（尺子能红） |
| J1 pathID 口径 | 与 A 组 writer.next_path_id 同源：max+1 再取整到 8（探针里逐值核过） |

## 边界

* 顶层**零第三方依赖**：类型树正文由本模块自己解析；只有「公共串表」这一张**格式常量表**
  借 UnityPy 的资源（unitypy_common_strings()，延迟 import）—— 与 unitypy_source 同一条边界。
* **不碰 SkinnedMeshRenderer**（Lead 口径）：本模块只造 MeshFilter + MeshRenderer。
* 写盘不在这里（那是 writer 的 Δ 链）；本模块**只出字节**。
"""

from __future__ import annotations

import dataclasses
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ADDRESS_ROOT_CLASS_ID",
    "ALIGN_BYTES",
    "ASSET_BUNDLE_CLASS_ID",
    "ASSET_BUNDLE_PATH_ID",
    "CLASS_GAME_OBJECT",
    "CLASS_MESH_FILTER",
    "CLASS_MESH_RENDERER",
    "CLASS_NAMES",
    "CLASS_TRANSFORM",
    "CLONE_CLASSES",
    "CloneGraph",
    "CloneObject",
    "CloneTarget",
    "CloneTemplates",
    "PreloadGrowth",
    "PptrRef",
    "RefReport",
    "TypeEntry",
    "TypeNode",
    "TypeSchema",
    "allocate_path_ids",
    "append_preload_entries",
    "build_mesh_renderer_clone",
    "check_addresses",
    "check_refs",
    "collect_pptrs",
    "delta_change",
    "object_rows",
    "path_ids_from_writer",
    "pick_templates",
    "plan_clone_from_bundle",
    "preload_growth",
    "pptr",
    "read_object",
    "read_object_payload",
    "read_type_nodes",
    "read_type_table",
    "ref_type_pool",
    "roundtrip",
    "unitypy_common_strings",
    "vec3",
    "vec4",
    "write_object",
]

# --------------------------------------------------------------------------- #
# Unity 常量（读侧口径，值与 UnityPy 的 enums.ClassIDType 一致）
# --------------------------------------------------------------------------- #
CLASS_GAME_OBJECT = 1
CLASS_TRANSFORM = 4
CLASS_MESH_RENDERER = 23
CLASS_MESH_FILTER = 33

#: 类型树节点 meta_flag 里的「读完这一项要对齐到 4」位（Unity 的 kAlignBytes）。
ALIGN_BYTES = 0x4000

#: 定长标量：Unity 类型名 -> struct 格式符（与 UnityPy 的 FUNCTION_READ_MAP 同一张表）。
_SCALARS: Mapping[str, str] = {
    "SInt8": "b",
    "UInt8": "B",
    "char": "B",
    "bool": "?",
    "short": "h",
    "SInt16": "h",
    "unsigned short": "H",
    "UInt16": "H",
    "int": "i",
    "SInt32": "i",
    "unsigned int": "I",
    "UInt32": "I",
    "Type*": "I",
    "long long": "q",
    "SInt64": "q",
    "unsigned long long": "Q",
    "UInt64": "Q",
    "FileSize": "Q",
    "float": "f",
    "double": "d",
}

#: 「值就是标量 / 字符串 / 裸字节」的叶子类型 —— 它们**有子节点**（string 底下挂着
#: Array -> int size + char data），所以判断结构时必须先看类型，⛔ 不能只看有没有子节点。
_LEAF_TYPES: frozenset[str] = frozenset(_SCALARS) | {"string", "TypelessData"}

#: [SerializeReference] 在类型树里的两个节点名（Unity 的**托管引用**序列化）：
#: `ReferencedObject` = 一条引用（`rid` + `type` 三元组 + `data`），
#: `ReferencedObjectData` = **一个子节点都没有**的那一格 —— 它的布局由同一条里
#: 先读出来的 `type.{class,ns,asm}` 去 `m_RefTypes` 里点一棵树决定。
#: 口径出处：UnityPy helpers/TypeTreeHelper.py:87-108 / :196-205 / :408-412（读与写两条分支）。
_REF_OBJECT_TYPE = "ReferencedObject"
_REF_DATA_TYPE = "ReferencedObjectData"

#: 数组元素个数的**绝对兜底上限**。真件最大的一个是 Shader.compressedBlob
#: （vector<UInt8>，**2,798 万**个）—— 老值 1,000 万把它误杀了（2026-10-06 实测：29 个 Shader 里
#: 只有它读不了）。真正的护栏是 `_least_bytes()` 那条「按剩余字节算放不下就抛」。
_MAX_ARRAY_ELEMENTS = 1_000_000_000

#: 四个类各取一个模板；低于这个字节数的对象不当模板（挡住 stripped / 截断的怪对象）。
_MIN_TEMPLATE_BYTES = 12

#: 本模块造的四个类（顺序就是对象表里 m_Component 的顺序：Transform 必须第 0 个）。
CLONE_CLASSES: tuple[int, ...] = (
    CLASS_TRANSFORM,
    CLASS_GAME_OBJECT,
    CLASS_MESH_FILTER,
    CLASS_MESH_RENDERER,
)

#: classID -> Unity 类名（只用于读数与判据一行；不参与字节）。
CLASS_NAMES: Mapping[int, str] = {
    CLASS_GAME_OBJECT: "GameObject",
    CLASS_TRANSFORM: "Transform",
    CLASS_MESH_RENDERER: "MeshRenderer",
    CLASS_MESH_FILTER: "MeshFilter",
}


# --------------------------------------------------------------------------- #
# 类型树 schema
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TypeNode:
    """类型树的一个节点（**扁平**存法，与内嵌 blob 的文档顺序一致）。"""

    level: int
    type: str
    name: str
    byte_size: int
    meta_flag: int
    type_flags: int
    version: int

    @property
    def aligned(self) -> bool:
        return bool(self.meta_flag & ALIGN_BYTES)


@dataclass(frozen=True, slots=True)
class TypeEntry:
    """类型表的一项：classID + 它的内嵌类型树。"""

    index: int
    class_id: int
    script_index: int
    nodes: tuple[TypeNode, ...]


@dataclass(frozen=True, slots=True)
class TypeSchema:
    """一个 SerializedFile 的类型表（classID -> type_id + 字段树）。"""

    entries: tuple[TypeEntry, ...]

    def entry(self, class_id: int) -> TypeEntry:
        hits = [e for e in self.entries if e.class_id == class_id]
        if not hits:
            raise AssetError(
                f"类型表里没有 classID={class_id}",
                hint="换个包了 / 游戏版本变了；不许拿别的类的类型树顶替",
            )
        if len(hits) > 1:
            raise AssetError(
                f"类型表里 classID={class_id} 有 {len(hits)} 项（下标 {[e.index for e in hits]}）",
                hint="一个 classID 对应多项时，先按对象表用到的 type_id 收敛，别猜",
            )
        return hits[0]

    def type_id(self, class_id: int) -> int:
        return self.entry(class_id).index

    def nodes(self, class_id: int) -> tuple[TypeNode, ...]:
        return self.entry(class_id).nodes

    def class_ids(self) -> tuple[int, ...]:
        return tuple(e.class_id for e in self.entries)


class _Cur:
    """元数据区的小游标（**小端**：blob 与对象表都是小端）。"""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int) -> None:
        self.buf = buf
        self.pos = pos

    def u8(self) -> int:
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def i16(self) -> int:
        (v,) = struct.unpack_from("<h", self.buf, self.pos)
        self.pos += 2
        return v

    def i32(self) -> int:
        (v,) = struct.unpack_from("<i", self.buf, self.pos)
        self.pos += 4
        return v

    def take(self, n: int) -> bytes:
        v = bytes(self.buf[self.pos : self.pos + n])
        self.pos += n
        return v

    def cstr(self) -> str:
        end = self.buf.index(b"\x00", self.pos)
        s = self.buf[self.pos : end].decode("utf-8", "surrogateescape")
        self.pos = end + 1
        return s


def unitypy_common_strings() -> Mapping[int, str]:
    """内嵌类型树的「公共串表」（字符串偏移高位 = 1 时用的那张表）。

    这是 Unity 的**格式常量**（114 条：AABB / AnimationClip / Array / float …），
    随 UnityPy 的 TPK 资源分发。本模块顶层零第三方，所以这里**延迟 import** ——
    与 unitypy_source 同一条边界口径（设计稿 §2 的 _vendor）。
    """
    from UnityPy.helpers.Tpk import get_common_strings  # noqa: PLC0415

    return get_common_strings()


def read_type_table(
    meta: bytes | bytearray,
    *,
    common_strings: Mapping[int, str] | None = None,
) -> TypeSchema:
    """把 SerializedFile 的**元数据区**（含 48 字节头）解析成 TypeSchema。

    内嵌类型树的字符串有两种存法（UnityPy 的 TypeTreeNode.parse_blob）：
    偏移高位 = 0 -> 字符串池里的 cstr；高位 = 1 -> **公共串表**（低 31 位是表内偏移）。
    实测 units 包 **7903 处**走后者 => common_strings 不给就直接抛，不猜、不回落。
    """
    buf = bytes(meta)
    if len(buf) < ufs.SF_HEADER_SIZE + 8:
        raise AssetError(f"元数据区只有 {len(buf)} 字节，读不出类型表")
    version = struct.unpack_from(">I", buf, 8)[0]
    cur = _Cur(buf, ufs.SF_HEADER_SIZE)
    cur.cstr()  # unity_version
    cur.i32()  # target_platform
    enable_type_tree = bool(cur.u8())
    type_count = cur.i32()
    if type_count < 0 or type_count > 100_000:
        raise AssetError(
            f"类型表的项数不合法：{type_count}",
            hint="元数据解析走偏了（这通常是上一个字段读错）",
        )

    entries: list[TypeEntry] = []
    for index in range(type_count):
        class_id = cur.i32()
        cur.u8()  # is_stripped
        script_index = cur.i16()
        if script_index >= 0:
            cur.take(16)  # script_id
        cur.take(16)  # old_type_hash
        nodes: tuple[TypeNode, ...] = ()
        if enable_type_tree:
            nodes = _read_type_tree_blob(cur, common_strings)
            if version >= 21:
                deps = cur.i32()
                if deps < 0 or deps > 4096:
                    raise AssetError(f"type_dependencies 的条数不合法：{deps}")
                cur.pos += 4 * deps
        entries.append(
            TypeEntry(index=index, class_id=class_id, script_index=script_index, nodes=nodes)
        )
    return TypeSchema(entries=tuple(entries))


def read_type_nodes(
    blob: bytes, *, common_strings: Mapping[int, str] | None = None
) -> tuple[TypeNode, ...]:
    """解析**一段内嵌类型树 blob**（typeCount 循环里的那一坨）。

    与 read_type_table 分开，是为了让用例能直接用**录下来的一段真 blob** 当夹具
    （不必开 3.38 GB 的包，也不必造一整份元数据区）。
    """
    return _read_type_tree_blob(_Cur(bytes(blob), 0), common_strings)


def _read_type_tree_blob(cur: _Cur, common: Mapping[int, str] | None) -> tuple[TypeNode, ...]:
    node_count = cur.i32()
    buffer_size = cur.i32()
    if node_count < 0 or buffer_size < 0:
        raise AssetError(
            f"类型树的两个长度不合法：节点数 {node_count}、字符串池 {buffer_size}",
            hint="元数据解析已经走偏",
        )
    raw_nodes = cur.take(32 * node_count)
    pool = cur.take(buffer_size)

    def resolve(value: int) -> str:
        if value & 0x80000000:
            offset = value & 0x7FFFFFFF
            if common is None:
                raise AssetError(
                    f"类型树引用了公共串表（偏移 {offset}），但没传 common_strings",
                    hint="传 unitypy_common_strings()（或等价映射）；不许猜字段名",
                )
            text = common.get(offset)
            if text is None:
                raise AssetError(f"公共串表里没有偏移 {offset}")
            return str(text)
        end = pool.index(b"\x00", value)
        return pool[value:end].decode("utf-8", "surrogateescape")

    out: list[TypeNode] = []
    for k in range(node_count):
        ver, level, flags, type_off, name_off, size, _idx, meta, _ref = struct.unpack_from(
            "<hBBIIiiiQ", raw_nodes, k * 32
        )
        out.append(
            TypeNode(
                level=level,
                type=resolve(type_off),
                name=resolve(name_off),
                byte_size=size,
                meta_flag=meta,
                type_flags=flags,
                version=ver,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# 字段树（值）—— 类型树驱动的读 / 写
# --------------------------------------------------------------------------- #
class _Tree:
    __slots__ = ("node", "children")

    def __init__(self, node: TypeNode) -> None:
        self.node = node
        self.children: list[_Tree] = []


def _build_tree(nodes: Sequence[TypeNode]) -> _Tree:
    if not nodes:
        raise AssetError("空类型树", hint="这个 classID 的类型树没读到（enable_type_tree 关着？）")
    root = _Tree(nodes[0])
    stack = [root]
    for node in nodes[1:]:
        while stack and stack[-1].node.level >= node.level:
            stack.pop()
        if not stack:
            raise AssetError(
                f"类型树的层级断了：{node.type} {node.name} (level={node.level})",
                hint="blob 解析错位；不许拿它去序列化",
            )
        child = _Tree(node)
        stack[-1].children.append(child)
        stack.append(child)
    _check_unique_names(root)
    return root


def _check_unique_names(tree: _Tree) -> None:
    """同一个结构体里的字段名必须唯一。

    字段树是 dict（名字当键）：重名会**静默丢掉一个字段** —— 那种字节能写出来、
    却少了几段（游戏侧读到别的偏移）。所以这里直接抛。
    """
    names = [child.node.name for child in tree.children]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise AssetError(
            f"类型树里同层重名：{duplicates}（在 {tree.node.type} 下）",
            hint="字段树用名字当键，重名会静默丢字段；这一版不接",
        )
    for child in tree.children:
        _check_unique_names(child)


def _layout(tree: _Tree) -> str:
    lines: list[str] = []

    def walk(node: _Tree, depth: int) -> None:
        lines.append("  " * depth + f"{node.node.type} {node.node.name}")
        for child in node.children:
            walk(child, depth + 1)

    walk(tree, 0)
    return "\n".join(lines)


def _least_bytes(tree: _Tree) -> int:
    """一个节点**至少**占多少字节（只用来判「数组声明的个数放不放得下」）。

    构造上就是下界，**绝不高估**（宁可漏检，不许误杀合法数据）：
    标量 = struct 宽度；string / TypelessData = 4（长度前缀）；没有子节点 = 0
    （`ReferencedObjectData` 空引用就吃 0 字节）；数组外壳 = 4；结构体 = 各子项之和。
    对齐填充只会让它更大，不会更小。
    """
    node = tree.node
    fmt = _SCALARS.get(node.type)
    if fmt is not None:
        return struct.calcsize(fmt)
    if node.type in ("string", "TypelessData"):
        return 4
    if not tree.children:
        return 0
    if tree.children[0].node.type == "Array":
        return 4
    return sum(_least_bytes(child) for child in tree.children)


# --------------------------------------------------------------------------- #
# [SerializeReference]：类型池（m_RefTypes）+ 一条引用怎么解
# --------------------------------------------------------------------------- #
def _ref_key(ref_object: Any, *, where: str) -> tuple[str, str, str] | None:
    """取一条 ReferencedObject 的 `(class, ns, asm)`；空引用（class 为空串）返回 None。

    空引用**一个字节都不占**（UnityPy read_value 的 `if ref_type_nodes is None: continue`）。
    """
    if not isinstance(ref_object, Mapping):
        raise AssetError(
            f"{where}：ReferencedObject 要一个映射，拿到 {type(ref_object).__name__}"
        )
    typ = ref_object.get("type")
    if not isinstance(typ, Mapping):
        raise AssetError(
            f"{where}：ReferencedObject 里的 type 不在（或不是映射）",
            hint="type 是这一条引用的判别键：类型池按它匹配。字段树里一定有它，"
                 "手搓的字段树要照 read_object 读出来的那份抄；"
                 "类型树里 data 排在 type 之前的话也解不出来（本包实测顺序 = rid, type, data）",
        )
    cls = str(typ.get("class") or "")
    if cls == "":
        return None
    return (cls, str(typ.get("ns") or ""), str(typ.get("asm") or ""))


def _ref_nodes(
    pool: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None,
    key: tuple[str, str, str],
    *,
    where: str,
) -> Sequence[TypeNode]:
    """按三元组从类型池点一棵树（**按三个字符串名精确匹配，不是下标**）。"""
    nodes = pool.get(key) if isinstance(pool, Mapping) else None
    if nodes is None:
        if not pool:
            raise AssetError(
                f"{where}：这块数据里有 [SerializeReference]（ReferencedObjectData），"
                "但没给 ref_types",
                hint="ref_types=clone.ref_type_pool(meta)；meta 是本文件的 "
                     "SerializedMetadata（或它的 ref_types 序列）",
            )
        raise AssetError(
            f"{where}：ref_types 里没有这个三元组 {key}",
            hint="类型池按 (class, ns, asm) 三个字符串名精确匹配；对不上说明这块数据 "
                 "不是本文件的（或引用了别的程序集）",
        )
    if not nodes:
        raise AssetError(
            f"{where}：ref_types 里 {key} 这一项没有类型树",
            hint="stripped / 没开 ett 的类型项没有树 —— 这种引用解不出来，不许猜布局",
        )
    return nodes


def _ref_tree(
    cache: dict[tuple[str, str, str], _Tree],
    key: tuple[str, str, str],
    nodes: Sequence[TypeNode],
) -> _Tree:
    """按三元组缓存子类型树（同一个池子在一次读/写里会被复用很多次）。"""
    tree = cache.get(key)
    if tree is None:
        tree = _build_tree(nodes)
        cache[key] = tree
    return tree


def ref_type_pool(
    source: Any, *, common_strings: Mapping[int, str] | None = None
) -> dict[tuple[str, str, str], tuple[TypeNode, ...]]:
    """造 `[SerializeReference]` 的类型池：`(class, ns, asm)` -> 那一族的类型树节点。

    `source` 可以是 `metadata_writer.SerializedMetadata`（取它的 `ref_types`），也可以是
    一串 `SerializedType`。转换走 `metadata_writer.encode_type_tree`（内嵌类型树是**小端**）
    + 本模块自己的 `read_type_nodes` —— 类型树解析器全仓只有这一份。

    实测（units 包，2026-10-06）：23 条 ref_types 全部 classID = -1、script_id 16 个 0、
    三元组两两不同；608 个 AnimationHub 用到的 23 种三元组**全在这一池子里**（0 条落空）。
    """
    from ba_mod_maker.asset_kernel import metadata_writer as mw  # noqa: PLC0415

    items = source.ref_types if hasattr(source, "ref_types") else list(source)
    pool: dict[tuple[str, str, str], tuple[TypeNode, ...]] = {}
    for item in items:
        key = (
            str(item.class_name or ""),
            str(item.namespace or ""),
            str(item.assembly_name or ""),
        )
        if key in pool:
            raise AssetError(
                f"ref_types 里有重复的三元组 {key}",
                hint="按名字匹配的池子不允许重名 —— 重名就无法判定该点哪一棵树",
            )
        tree = item.type_tree
        pool[key] = (
            read_type_nodes(mw.encode_type_tree(tree, "<"), common_strings=common_strings)
            if tree is not None
            else ()
        )
    return pool


class _ValueWriter:
    __slots__ = ("buf", "e", "ref_types", "_ref_trees")

    def __init__(
        self,
        little_endian: bool,
        ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
    ) -> None:
        self.buf = bytearray()
        self.e = "<" if little_endian else ">"
        #: [SerializeReference] 的类型池；没有它的类型树可以传 None（老路径一个字节不变）。
        self.ref_types = ref_types
        self._ref_trees: dict[tuple[str, str, str], _Tree] = {}

    def align(self, n: int = 4) -> None:
        self.buf += b"\x00" * ((-len(self.buf)) % n)

    def scalar(self, fmt: str, value: Any) -> None:
        try:
            self.buf += struct.pack(self.e + fmt, value)
        except struct.error as exc:
            raise AssetError(f"标量 {fmt} 写不下 {value!r}：{exc}") from exc

    def write(self, tree: _Tree, value: Any) -> None:
        node = tree.node
        align = node.aligned
        fmt = _SCALARS.get(node.type)
        if fmt is not None:
            self.scalar(fmt, value)
        elif node.type == "string":
            raw = str(value).encode("utf-8", "surrogateescape")
            self.scalar("i", len(raw))
            self.buf += raw
            self.align(4)
        elif node.type == "TypelessData":
            raw = bytes(value)
            self.scalar("i", len(raw))
            self.buf += raw
        elif node.type == _REF_OBJECT_TYPE:
            if not isinstance(value, Mapping):
                raise AssetError(
                    f"{node.type} {node.name} 要一个映射，拿到 {type(value).__name__}",
                    hint="字段树的形状由类型树决定：\n" + _layout(tree),
                )
            for child in tree.children:
                key = child.node.name
                if child.node.type != _REF_DATA_TYPE:
                    if key not in value:
                        raise AssetError(
                            f"字段树缺 {node.type}.{key}",
                            hint="从包里读一个同类对象当模板（read_object），别手搓整棵树",
                        )
                    self.write(child, value[key])
                    continue
                ref = _ref_key(value, where=f"{node.type} {node.name}")
                if ref is None:
                    if value.get(key) is not None:
                        raise AssetError(
                            f"{node.type} {node.name}：这一条是空引用（type.class 是空串），"
                            "却给了 data",
                            hint="空引用一个字节都不占；要么把 data 去掉，要么把 type "
                                 "三个名改成类型池里有的那一族",
                        )
                    continue
                if key not in value:
                    raise AssetError(
                        f"字段树缺 {node.type}.{key}",
                        hint="读出来的字段树里没有 data ⇒ 写回去也写不出来（空引用才没有 data）",
                    )
                nodes = _ref_nodes(self.ref_types, ref, where=f"{node.type} {node.name}")
                self.write(_ref_tree(self._ref_trees, ref, nodes), value[key])
        elif tree.children and tree.children[0].node.type == "Array":
            holder = tree.children[0]
            if holder.node.aligned:
                align = True
            items = list(value)
            self.scalar("i", len(items))
            subtype = holder.children[1] if len(holder.children) > 1 else None
            if subtype is None:
                raise AssetError(f"数组 {node.name} 没有元素节点")
            for item in items:
                self.write(subtype, item)
        elif tree.children:
            if not isinstance(value, Mapping):
                raise AssetError(
                    f"{node.type} {node.name} 要一个映射，拿到 {type(value).__name__}",
                    hint="字段树的形状由类型树决定：\n" + _layout(tree),
                )
            for child in tree.children:
                key = child.node.name
                if key not in value:
                    raise AssetError(
                        f"字段树缺 {node.type}.{key}",
                        hint="从包里读一个同类对象当模板（read_object），别手搓整棵树",
                    )
                self.write(child, value[key])
        else:
            raise AssetError(
                f"类型树里有认不得的字段：{node.type} {node.name}",
                hint="这一版的序列化器只认标量 / string / TypelessData / 数组 / 结构体",
            )
        if align:
            self.align(4)


class _ValueReader:
    __slots__ = ("buf", "pos", "e", "ref_types", "_ref_trees", "_least", "bulk_arrays")

    def __init__(
        self,
        buf: bytes,
        little_endian: bool,
        ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
        *,
        bulk_arrays: bool = False,
    ) -> None:
        self.buf = buf
        self.pos = 0
        self.e = "<" if little_endian else ">"
        #: ★ 2026-10-18：**原始类型数组批量读**（默认关 ⇒ 老路径逐元素、结果一模一样）。
        #: 只给「一次读几十 MB 顶点数据」那条路（Mesh）用：UnityPy 的 TypeTree 解器对
        #: `UInt8[]` 是**逐元素 read_u_byte()**，实测 T-90M 单件 88,186,568 次 ⇒ 84 s。
        #: 开了之后 `UInt8[]` 直接给 `bytes`（⛔ 不给 list[int]，84 M 个 int ≈ 2.3 GB），
        #: 其它原始类型给 `struct.unpack` 出来的 list（一次解完，不逐个调函数）。
        self.bulk_arrays = bulk_arrays
        #: [SerializeReference] 的类型池；没有它的类型树可以传 None（老路径一个字节不变）。
        self.ref_types = ref_types
        self._ref_trees: dict[tuple[str, str, str], _Tree] = {}
        self._least: dict[int, int] = {}

    def least_bytes(self, tree: _Tree) -> int:
        """`_least_bytes(tree)` 的按子树缓存（同一棵树在一次读里会被问很多次）。"""
        key = id(tree)
        got = self._least.get(key)
        if got is None:
            got = _least_bytes(tree)
            self._least[key] = got
        return got

    def align(self, n: int = 4) -> None:
        self.pos += (-self.pos) % n

    def need(self, n: int, *, what: str) -> None:
        """读之前先判边界：不够就抛**可读的** AssetError（⛔ 不让裸 struct.error 冒出去）。

        ⚠️ 只在**短缓冲区**这一侧加护栏：合法输入一定 pos + n <= len(buf)，
        所以正常路径一个字节都不变（判据：read_object / roundtrip 的逐字节结果与改前相同）。
        "对象字节被砍" 从前冒的是 struct.error —— 既 catch 不住（不是 BaModMakerError），
        也没有位置上下文。复算：_work/unity-factory/_diag_trunc.py。
        """
        if n < 0 or self.pos < 0 or self.pos + n > len(self.buf):
            raise AssetError(
                f"对象字节不够读 {what}：想在 {self.pos} 读 {n} 字节，"
                f"缓冲区只有 {len(self.buf)} 字节",
                hint="对象字节被截断，或类型树与字节对不上（拿错类的树？）—— 不许拿这种字节写回",
                cursor=self.pos,
                need=n,
                buffer_size=len(self.buf),
            )

    def scalar(self, fmt: str) -> Any:
        need = struct.calcsize(fmt)
        self.need(need, what=f"标量 {fmt}")
        (v,) = struct.unpack_from(self.e + fmt, self.buf, self.pos)
        self.pos += need
        return v

    def read(self, tree: _Tree) -> Any:
        node = tree.node
        align = node.aligned
        fmt = _SCALARS.get(node.type)
        if fmt is not None:
            value: Any = self.scalar(fmt)
        elif node.type == "string":
            size = self.scalar("i")
            self.need(size, what=f"字符串 {node.name} 的 {size} 字节")
            raw = self.buf[self.pos : self.pos + size]
            self.pos += size
            value = raw.decode("utf-8", "surrogateescape")
            self.align(4)
        elif node.type == "TypelessData":
            size = self.scalar("i")
            self.need(size, what=f"TypelessData {node.name} 的 {size} 字节")
            value = bytes(self.buf[self.pos : self.pos + size])
            self.pos += size
        elif node.type == _REF_OBJECT_TYPE:
            value = {}
            for child in tree.children:
                if child.node.type != _REF_DATA_TYPE:
                    value[child.node.name] = self.read(child)
                    continue
                ref = _ref_key(value, where=f"{node.type} {node.name}")
                if ref is None:
                    continue  # 空引用：一个字节都不占（与 UnityPy read_value 同口径）
                nodes = _ref_nodes(self.ref_types, ref, where=f"{node.type} {node.name}")
                value[child.node.name] = self.read(_ref_tree(self._ref_trees, ref, nodes))
        elif tree.children and tree.children[0].node.type == "Array":
            holder = tree.children[0]
            if holder.node.aligned:
                align = True
            count = self.scalar("i")
            if count < 0 or count > _MAX_ARRAY_ELEMENTS:
                raise AssetError(f"数组 {node.name} 的元素个数不合法：{count}")
            subtype = holder.children[1] if len(holder.children) > 1 else None
            if subtype is None:
                raise AssetError(f"数组 {node.name} 没有元素节点")
            room = len(self.buf) - self.pos
            least = self.least_bytes(subtype)
            if least and count * least > room:
                raise AssetError(
                    f"数组 {node.name} 声明 {count:,} 个元素，对象只剩 {room:,} 字节",
                    hint=f"每个元素至少 {least} 字节 ⇒ 这个个数与对象长度矛盾（解码走偏）",
                )
            fast = _SCALARS.get(subtype.node.type) if self.bulk_arrays else None
            if fast is not None and not subtype.children and not subtype.node.aligned:
                width = struct.calcsize(fast)
                self.need(count * width, what=f"数组 {node.name} 的 {count:,} 个元素")
                start = self.pos
                self.pos += count * width
                if fast == "B":
                    value = bytes(self.buf[start : self.pos])
                else:
                    value = list(struct.unpack_from(f"{self.e}{count}{fast}", self.buf, start))
            else:
                value = [self.read(subtype) for _ in range(count)]
        elif tree.children:
            value = {child.node.name: self.read(child) for child in tree.children}
        else:
            raise AssetError(f"类型树里有认不得的字段：{node.type} {node.name}")
        if align:
            self.align(4)
        return value


def write_object(
    value: Mapping[str, Any],
    nodes: Sequence[TypeNode],
    *,
    ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
    little_endian: bool = True,
) -> bytes:
    """按类型树序列化一个对象（字段树 -> 字节）。

    `ref_types` 只在类型树里有 `[SerializeReference]`（`ReferencedObjectData`）时才需要：
    它是 `(class, ns, asm)` -> 类型树节点的池子，用 `ref_type_pool()` 造。
    """
    writer = _ValueWriter(little_endian, ref_types)
    writer.write(_build_tree(nodes), value)
    return bytes(writer.buf)


def read_object(
    data: bytes,
    nodes: Sequence[TypeNode],
    *,
    ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
    little_endian: bool = True,
    bulk_arrays: bool = False,
    allow_leftover: bool = False,
) -> dict[str, Any]:
    """按类型树解析一个对象（字节 -> 字段树）。`ref_types` 口径同 `write_object`。

    :param bulk_arrays: ★ 2026-10-18 —— 原始类型数组**批量读**（`UInt8[]` 直接给 `bytes`）。
        默认 False = 逐元素，与改前逐字节相同；只有 Mesh 那条「几十 MB 顶点数据」的路开它。
    """
    reader = _ValueReader(bytes(data), little_endian, ref_types, bulk_arrays=bulk_arrays)
    root = _build_tree(nodes)
    value = reader.read(root)
    if allow_leftover:
        # ★ 2026-10-18：只读**前缀**（例如 MonoBehaviour 只读到 m_Script 就停）——
        #   用于「只要一个字段、不想为几十 MB 的数组付钱」的场合。
        return value  # type: ignore[return-value]
    if reader.pos != len(data):
        raise AssetError(
            f"{root.node.type} 只吃掉了 {reader.pos:,}/{len(data):,} B",
            hint="类型树与对象字节对不上（拿错类的树？）—— 这种字节不要写回",
        )
    return value  # type: ignore[return-value]


def roundtrip(
    data: bytes,
    nodes: Sequence[TypeNode],
    *,
    ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
    little_endian: bool = True,
) -> bytes:
    """读一遍再写一遍：返回的字节必须与入参**逐字节相同**（本模块的正控）。"""
    value = read_object(data, nodes, ref_types=ref_types, little_endian=little_endian)
    return write_object(value, nodes, ref_types=ref_types, little_endian=little_endian)


# --------------------------------------------------------------------------- #
# PPtr：收集 / 判据
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PptrRef:
    """一个新对象里的 PPtr 引用（判据 J4 的输入）。"""

    owner: str
    field: str
    file_id: int
    path_id: int

    def __str__(self) -> str:
        return f"{self.owner}.{self.field} -> (fileID={self.file_id}, pathID={self.path_id})"


@dataclass(frozen=True, slots=True)
class RefReport:
    """J4 的读数：新对象里的每一个 PPtr 落到哪里。"""

    refs: tuple[PptrRef, ...]
    dangling: tuple[PptrRef, ...]
    external: tuple[PptrRef, ...]
    null: tuple[PptrRef, ...]

    @property
    def in_package(self) -> tuple[PptrRef, ...]:
        return tuple(
            r
            for r in self.refs
            if r.file_id == 0 and r.path_id != 0 and r not in self.dangling
        )

    @property
    def ok(self) -> bool:
        """判据 = **无悬空**；跨文件（fileID != 0）必须显式说明，所以不算自动通过。"""
        return not self.dangling and not self.external

    def lines(self) -> tuple[str, ...]:
        out = [
            f"PPtr 共 {len(self.refs)} 条：包内 {len(self.in_package)} / 空 {len(self.null)}"
            f" / 跨文件 {len(self.external)} / 悬空 {len(self.dangling)}"
        ]
        out.extend(f"  x 悬空：{ref}" for ref in self.dangling)
        out.extend(f"  ! 跨文件：{ref}" for ref in self.external)
        return tuple(out)


def collect_pptrs(
    value: Mapping[str, Any], nodes: Sequence[TypeNode], *, owner: str
) -> tuple[PptrRef, ...]:
    """按类型树走一遍字段树，把每个 PPtr 抓出来（字段名带路径）。"""
    out: list[PptrRef] = []

    def walk(tree: _Tree, node_value: Any, path: str) -> None:
        node = tree.node
        if node.type.startswith("PPtr<"):
            if not isinstance(node_value, Mapping):
                raise AssetError(f"{owner}.{path} 是个 PPtr，但值不是映射")
            out.append(
                PptrRef(
                    owner=owner,
                    field=path,
                    file_id=int(node_value.get("m_FileID", 0) or 0),
                    path_id=int(node_value.get("m_PathID", 0) or 0),
                )
            )
            return
        if node.type in _LEAF_TYPES:
            return
        if tree.children and tree.children[0].node.type == "Array":
            holder = tree.children[0]
            subtype = holder.children[1] if len(holder.children) > 1 else None
            if subtype is None:
                return
            for index, item in enumerate(node_value or ()):
                walk(subtype, item, f"{path}[{index}]")
            return
        for child in tree.children:
            if isinstance(node_value, Mapping) and child.node.name in node_value:
                walk(child, node_value[child.node.name], f"{path}.{child.node.name}")

    walk(_build_tree(nodes), value, owner)
    return tuple(out)


def check_refs(
    objects: Mapping[str, tuple[Mapping[str, Any], Sequence[TypeNode]]],
    package_ids: Iterable[int],
) -> RefReport:
    """J4 的尺子：新对象里的 PPtr 是否都能在**包内**解到。

    * fileID == 0 且 pathID == 0  -> 空引用（合法，Unity 的 null）；
    * fileID == 0 且 pathID != 0  -> 必须在 package_ids 里，否则**悬空**（能红）；
    * fileID != 0                 -> **跨文件**（本单口径：必须显式说明，不算自动通过）。
    """
    known = {int(p) for p in package_ids}
    refs: list[PptrRef] = []
    for owner, (value, nodes) in objects.items():
        refs.extend(collect_pptrs(value, nodes, owner=owner))
    dangling = tuple(
        r for r in refs if r.file_id == 0 and r.path_id != 0 and r.path_id not in known
    )
    external = tuple(r for r in refs if r.file_id != 0)
    null = tuple(r for r in refs if r.file_id == 0 and r.path_id == 0)
    return RefReport(refs=tuple(refs), dangling=dangling, external=external, null=null)


# --------------------------------------------------------------------------- #
# 克隆图
# --------------------------------------------------------------------------- #
def pptr(path_id: int, file_id: int = 0) -> dict[str, int]:
    """PPtr 的字段树形状（12 B：i32 m_FileID + i64 m_PathID）。"""
    return {"m_FileID": int(file_id), "m_PathID": int(path_id)}


def vec3(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": float(x), "y": float(y), "z": float(z)}


def vec4(x: float, y: float, z: float, w: float) -> dict[str, float]:
    return {"x": float(x), "y": float(y), "z": float(z), "w": float(w)}


@dataclass(frozen=True, slots=True)
class CloneTarget:
    """要造的那个零件的靶子（全是**包内已有** pathID）。"""

    parent_transform: int
    mesh: int
    materials: tuple[int, ...]
    name: str = "RU_T72B_clone_track"
    #: 看得见的偏移（1.5 在 float32 里是精确值 —— 1.6 会变成 1.600000023841858）
    local_position: tuple[float, float, float] = (0.0, 1.5, 0.0)
    local_rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    local_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass(frozen=True, slots=True)
class CloneTemplates:
    """四个类各一个**真对象**当结构模板（字段与长度都照它）。"""

    transform: bytes
    game_object: bytes
    mesh_filter: bytes
    mesh_renderer: bytes


@dataclass(frozen=True, slots=True)
class CloneObject:
    """一个新对象：pathID + typeID + 字段树 + 字节。"""

    path_id: int
    class_id: int
    type_id: int
    type_name: str
    name: str
    fields: Mapping[str, Any]
    payload: bytes

    @property
    def size(self) -> int:
        return len(self.payload)


@dataclass(frozen=True, slots=True)
class CloneGraph:
    """一次克隆的**全部**产出（4 个新对象 + 父 Transform 的新字节）。"""

    schema: TypeSchema
    target: CloneTarget
    objects: tuple[CloneObject, ...]
    parent: CloneObject
    parent_children_before: tuple[int, ...]
    parent_children_after: tuple[int, ...]
    package_ids: frozenset[int] = frozenset()

    def object(self, class_id: int) -> CloneObject:
        for item in self.objects:
            if item.class_id == class_id:
                return item
        raise KeyError(f"克隆图里没有 classID={class_id}")

    def ref_objects(self) -> Mapping[str, tuple[Mapping[str, Any], Sequence[TypeNode]]]:
        """判据 J4 的输入：名字 -> (字段树, 该类的类型树)。"""
        out: dict[str, tuple[Mapping[str, Any], Sequence[TypeNode]]] = {}
        for item in self.objects:
            out[item.type_name] = (item.fields, self.schema.nodes(item.class_id))
        return out

    def refs(self) -> RefReport:
        return check_refs(self.ref_objects(), self.package_ids)

    def class_ids(self) -> tuple[int, ...]:
        return tuple(o.class_id for o in self.objects)

    def criterion_lines(self) -> tuple[str, ...]:
        return (
            f"克隆零件：{self.target.name}（父 transform {self.target.parent_transform}）",
            "新对象 "
            + str(len(self.objects))
            + " 个："
            + "，".join(
                f"{o.type_name}(classID {o.class_id} typeID {o.type_id}) pathID={o.path_id} "
                f"{o.size} B"
                for o in self.objects
            ),
            f"父 m_Children：{len(self.parent_children_before)} -> "
            f"{len(self.parent_children_after)}  {list(self.parent_children_before)} -> "
            f"{list(self.parent_children_after)}",
            f"Mesh pathID={self.target.mesh} fileID=0；Material pathID="
            f"{list(self.target.materials)} fileID=0（都在包内）",
        )


def _normalize_floats(
    tree: _Tree,
    value: Any,
    ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
) -> Any:
    """把 float 字段收成 **float32** 再比往返。

    坑：Python 的 1.6 是 float64，pack('f') 之后是 1.600000023841858 ——
    直接拿 float64 的字段树跟读回来的比，会报一个「假不一致」（本单第一次跑就撞上）。
    所以先按类型树把 float 归一到 float32，再比。
    """
    node = tree.node
    if node.type == "float":
        return struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if node.type in _LEAF_TYPES:
        return value
    if node.type == _REF_OBJECT_TYPE:
        out: dict[str, Any] = {}
        for child in tree.children:
            if child.node.type != _REF_DATA_TYPE:
                out[child.node.name] = _normalize_floats(
                    child, value[child.node.name], ref_types
                )
                continue
            ref = _ref_key(value, where=f"{node.type} {node.name}")
            if ref is None:
                continue
            nodes = _ref_nodes(ref_types, ref, where=f"{node.type} {node.name}")
            out[child.node.name] = _normalize_floats(
                _build_tree(nodes), value[child.node.name], ref_types
            )
        return out
    if tree.children and tree.children[0].node.type == "Array":
        holder = tree.children[0]
        subtype = holder.children[1] if len(holder.children) > 1 else None
        if subtype is None:
            return value
        return [_normalize_floats(subtype, item, ref_types) for item in value]
    if tree.children:
        return {
            child.node.name: _normalize_floats(child, value[child.node.name], ref_types)
            for child in tree.children
        }
    return value


def _new_object(
    schema: TypeSchema,
    class_id: int,
    path_id: int,
    name: str,
    fields: Mapping[str, Any],
    ref_types: Mapping[tuple[str, str, str], Sequence[TypeNode]] | None = None,
) -> CloneObject:
    nodes = schema.nodes(class_id)
    normalized = _normalize_floats(_build_tree(nodes), dict(fields), ref_types)
    payload = write_object(normalized, nodes, ref_types=ref_types)
    back = read_object(payload, nodes, ref_types=ref_types)
    if back != normalized:
        raise AssetError(
            f"序列化往返不一致（classID {class_id} pathID {path_id}）",
            hint="类型树当 schema 的往返是本单的判据 5；不一致不许落地",
        )
    return CloneObject(
        path_id=path_id,
        class_id=class_id,
        type_id=schema.type_id(class_id),
        type_name=CLASS_NAMES.get(class_id, str(class_id)),
        name=name,
        fields=normalized,
        payload=payload,
    )


def build_mesh_renderer_clone(
    schema: TypeSchema,
    templates: CloneTemplates,
    target: CloneTarget,
    *,
    parent_payload: bytes,
    path_ids: Sequence[int],
) -> CloneGraph:
    """造克隆图：4 个新对象 + 父 Transform 的新字节（干跑，一个字节都不落盘）。

    path_ids 的顺序 = **Transform, GameObject, MeshFilter, MeshRenderer**。
    """
    if len(path_ids) != 4:
        raise ValueError(
            "要 4 个新 pathID（Transform/GameObject/MeshFilter/MeshRenderer），"
            f"拿到 {len(path_ids)}"
        )
    new_tr, new_go, new_mf, new_mr = (int(v) for v in path_ids)

    tr_nodes = schema.nodes(CLASS_TRANSFORM)
    go_nodes = schema.nodes(CLASS_GAME_OBJECT)
    mf_nodes = schema.nodes(CLASS_MESH_FILTER)
    mr_nodes = schema.nodes(CLASS_MESH_RENDERER)

    # ---- 父 Transform：读出它现在的 m_Children（判据 3 的 before）----
    parent_fields = read_object(parent_payload, tr_nodes)
    if roundtrip(parent_payload, tr_nodes) != parent_payload:
        raise AssetError(
            f"父 Transform {target.parent_transform} 的字节读回来不一样",
            hint="类型树与对象对不上（拿错包 / 拿错类）",
        )
    children_before = tuple(int(p["m_PathID"]) for p in parent_fields["m_Children"])
    if new_tr in children_before:
        raise AssetError(f"父的 m_Children 里已经有 {new_tr} 了")

    # ---- 新 Transform ----
    tr = read_object(templates.transform, tr_nodes)
    tr["m_GameObject"] = pptr(new_go)
    tr["m_LocalRotation"] = vec4(*target.local_rotation)
    tr["m_LocalPosition"] = vec3(*target.local_position)
    tr["m_LocalScale"] = vec3(*target.local_scale)
    tr["m_Children"] = []
    tr["m_Father"] = pptr(target.parent_transform)

    # ---- 新 GameObject（m_Component 顺序：Transform 必须第 0 个）----
    go = read_object(templates.game_object, go_nodes)
    go["m_Component"] = [
        {"component": pptr(new_tr)},
        {"component": pptr(new_mf)},
        {"component": pptr(new_mr)},
    ]
    go["m_Name"] = target.name

    # ---- 新 MeshFilter：m_Mesh 必须指向**包内**网格，fileID = 0 ----
    mf = read_object(templates.mesh_filter, mf_nodes)
    mf["m_GameObject"] = pptr(new_go)
    mf["m_Mesh"] = pptr(target.mesh)

    # ---- 新 MeshRenderer：材质走 m_Materials（fileID = 0）----
    mr = read_object(templates.mesh_renderer, mr_nodes)
    mr["m_GameObject"] = pptr(new_go)
    mr["m_Materials"] = [pptr(m) for m in target.materials]
    mr["m_Enabled"] = True

    objects = (
        _new_object(schema, CLASS_TRANSFORM, new_tr, target.name, tr),
        _new_object(schema, CLASS_GAME_OBJECT, new_go, target.name, go),
        _new_object(schema, CLASS_MESH_FILTER, new_mf, target.name, mf),
        _new_object(schema, CLASS_MESH_RENDERER, new_mr, target.name, mr),
    )

    # ---- 父 Transform 的 growth：m_Children 追加（顺序保持）----
    merged = [pptr(pid) for pid in children_before] + [pptr(new_tr)]
    parent_new = dict(parent_fields)
    parent_new["m_Children"] = merged
    parent_object = CloneObject(
        path_id=int(target.parent_transform),
        class_id=CLASS_TRANSFORM,
        type_id=schema.type_id(CLASS_TRANSFORM),
        type_name="Transform",
        name=f"<parent {target.parent_transform}>",
        fields=parent_new,
        payload=write_object(parent_new, tr_nodes),
    )
    children_after = tuple(
        int(p["m_PathID"]) for p in read_object(parent_object.payload, tr_nodes)["m_Children"]
    )
    if children_after != (*children_before, new_tr):
        raise AssetError(
            f"父 m_Children 追加后对不上：{children_after} != {(*children_before, new_tr)}",
            hint="判据 3/4 是这一单的核心，对不上不许落地",
        )

    return CloneGraph(
        schema=schema,
        target=target,
        objects=objects,
        parent=parent_object,
        parent_children_before=children_before,
        parent_children_after=children_after,
    )


def _aligned_start(top: int, align: int = 8) -> int:
    """起点口径：**包内最大 pathID + 1** 再向上取整到 align。

    与 A 组的 writer.next_path_id（max+1）同一条口径 —— 只差「取整到 8」这一步，
    而工单 J1 的字面要求是「新 pathID 唯一**且 8 字节对齐**」，所以取整。
    （byte_start 的 8 字节对齐由 A 组在对象表那边管，是另一件事。）
    """
    start = (top + 1) if top > 0 else align
    return ((start + align - 1) // align) * align


def allocate_path_ids(existing: Iterable[int], count: int, *, align: int = 8) -> tuple[int, ...]:
    """给克隆图分配 count 个新 pathID（**原地**用对象表里的 pathID 集合算）。

    => (1) 全部 > 包内最大 pathID（绝不与任何已有 pathID 撞）；
       (2) 全部是 align 的倍数（工单 J1 的「8 字节对齐」）；
       (3) 严格递增（A 组按升序插对象表）。
    """
    if count <= 0:
        raise ValueError("count 必须为正")
    ids = [int(v) for v in existing]
    start = _aligned_start(max(ids) if ids else 0, align)
    return tuple(start + i * align for i in range(count))


def path_ids_from_writer(
    bundle_path: str | Path, count: int, *, align: int = 8, hint: str = ""
) -> tuple[int, ...]:
    """**A 组那条路**：调一次 writer.next_path_id(bundle) 拿基准，其余顺着往上排。

    为什么只调一次：next_path_id 自己会开一次包（units 3.38 GB 约 7 s），
    要 4 个 pathID 就开 4 次是不可接受的；而 base+align*k 天然 > max ⇒ 不会撞。
    与 allocate_path_ids(同一个包) 给的是**同一组值**（探针里逐值核过）。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    fn = getattr(writer, "next_path_id", None)
    if fn is None:
        raise AssetError(
            "writer.next_path_id 还没落地（W8-A）",
            hint="先用 clone.allocate_path_ids(对象表的 pathID 集合, 4)",
        )
    base = int(fn(bundle_path, hint=hint))
    start = _aligned_start(base - 1, align)
    return tuple(start + i * align for i in range(count))


# --------------------------------------------------------------------------- #
# 真件入口（只读）
# --------------------------------------------------------------------------- #
def object_rows(info: ufs.SerializedFileInfo) -> dict[int, tuple[int, int, int]]:
    """对象表 -> {path_id: (stored, byte_size, type_id)}。"""
    return {pid: (stored, size, tid) for _i, pid, stored, size, tid in info.objects}


def read_object_payload(
    reader: ufs.UfsReader, node: ufs.Node, info: ufs.SerializedFileInfo, path_id: int
) -> bytes:
    """读一个对象的**原始字节**（对象表 -> 节点内偏移，走产品自己的容器读侧）。"""
    row = object_rows(info).get(int(path_id))
    if row is None:
        raise AssetError(
            f"{node.path} 的对象表里没有 pathID={path_id}",
            hint="读错了包 / 这个对象不在这个节点里",
        )
    stored, size, _tid = row
    return bytes(reader.read_range(node.offset + info.data_offset + stored, size))


def pick_templates(
    reader: ufs.UfsReader,
    node: ufs.Node,
    info: ufs.SerializedFileInfo,
    schema: TypeSchema,
) -> CloneTemplates:
    """给四个类各挑一个**结构模板**：字节数最小、且读回来逐字节相同的那一个。

    为什么要 roundtrip 过滤：包里可能有 stripped / 截断的怪对象，拿它当模板会
    把怪形状复制到新对象上。这条过滤同时是**本模块的正控**（真件上量过一次）。
    """
    rows = object_rows(info)
    picked: dict[int, bytes] = {}
    for class_id in CLONE_CLASSES:
        nodes = schema.nodes(class_id)
        type_ids = {e.index for e in schema.entries if e.class_id == class_id}
        candidates = sorted(
            (size, pid)
            for pid, (_stored, size, tid) in rows.items()
            if tid in type_ids and size >= _MIN_TEMPLATE_BYTES
        )
        chosen: bytes | None = None
        last_error = ""
        for _size, pid in candidates:
            data = read_object_payload(reader, node, info, pid)
            try:
                if write_object(read_object(data, nodes), nodes) == data:
                    chosen = data
                    break
            except AssetError as exc:  # 怪对象：换下一个
                last_error = str(exc)
        if chosen is None:
            raise AssetError(
                f"classID={class_id} 挑不出模板（候选 {len(candidates)} 个）",
                hint=f"最后一个报错：{last_error}",
            )
        picked[class_id] = chosen
    return CloneTemplates(
        transform=picked[CLASS_TRANSFORM],
        game_object=picked[CLASS_GAME_OBJECT],
        mesh_filter=picked[CLASS_MESH_FILTER],
        mesh_renderer=picked[CLASS_MESH_RENDERER],
    )


def plan_clone_from_bundle(
    bundle_path: str,
    target: CloneTarget,
    *,
    templates: CloneTemplates | None = None,
    common_strings: Mapping[int, str] | None = None,
    path_ids: Sequence[int] | None = None,
) -> CloneGraph:
    """真件入口（**只读**）：开包 -> 读 schema -> 挑模板 -> 造四个对象的字节。

    :raises AssetError: 父 transform / mesh / material 不在这个包里，或类型树读不出来。
    """
    with ufs.UfsReader(bundle_path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size)
        common = common_strings
        if common is None:
            try:
                common = unitypy_common_strings()
            except ImportError as exc:  # pragma: no cover -- 只在本机没装 UnityPy 时走
                raise AssetError(
                    "类型树用了公共串表，但本环境没有 UnityPy：请显式传 common_strings",
                    hint=str(exc),
                ) from exc
        schema = read_type_table(meta, common_strings=common)
        rows = object_rows(info)
        for label, path_id in (
            ("父 transform", target.parent_transform),
            ("mesh", target.mesh),
            *[("material", m) for m in target.materials],
        ):
            if path_id not in rows:
                raise AssetError(
                    f"{label} pathID={path_id} 不在 {node.path} 的对象表里",
                    hint="本单口径：四个新对象的每个 PPtr 都必须解在**包内**",
                )
        if templates is None:
            templates = pick_templates(reader, node, info, schema)
        if path_ids is None:
            path_ids = allocate_path_ids(rows, 4)
        parent_payload = read_object_payload(reader, node, info, target.parent_transform)
        graph = build_mesh_renderer_clone(
            schema,
            templates,
            target,
            parent_payload=parent_payload,
            path_ids=path_ids,
        )
        # J4 的「包内」= 包里**已有**的对象 + **本次新增**的这 4 个（它们在同一次写回里
        # 一起进对象表）。少了后半截，新对象之间的互相引用会被误报成悬空。
        known = frozenset(rows) | {o.path_id for o in graph.objects}
        return dataclasses.replace(graph, package_ids=known)


def delta_change(
    graph: CloneGraph,
    *,
    inplace: Sequence[Any] = (),
    growths: Sequence[Any] = (),
    appends: Sequence[Any] = (),
) -> Any:
    """把克隆图装配成 writer.DeltaChange（**延迟 import** —— writer 顶层反过来 import 本模块）。

    四个新对象走 added，父 Transform 走 growths（m_Children 变长）。两类必须落在
    **同一次**写回里，否则游戏会看到「有孩子没爹」或「有爹没孩子」。

    :param growths: 额外的 growth（**接缝**）：新对象进 AssetBundle(pathID=1) 的
        m_PreloadTable 也是对 pid=1 的一次 ObjectGrowth（W8-A 的 J3 那一半）——
        调用方把它从这里塞进来，本函数负责与父 Transform 的 growth 拼在同一个事务里。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415

    if not hasattr(writer, "NewObject") or "added" not in {
        field.name for field in dataclasses.fields(writer.DeltaChange)
    }:
        raise AssetError(
            "这个 writer 没有「新增对象」的接口（NewObject / DeltaChange.added）",
            hint="产品里它 2026-10-03（W8-A）就落地并真机验过 —— 报这个错说明装的是"
                 "被裁剪 / 降级的 asset_kernel，⛔ 别拿它写盘",
        )
    return writer.DeltaChange(
        inplace=tuple(inplace),
        growths=(
            writer.ObjectGrowth(
                path_id=graph.parent.path_id,
                payload=graph.parent.payload,
                name=graph.parent.name,
                type_name=graph.parent.type_name,
            ),
            *growths,
        ),
        appends=tuple(appends),
        added=tuple(
            writer.NewObject(
                path_id=item.path_id,
                type_id=item.type_id,
                payload=item.payload,
                name=item.name,
                type_name=item.type_name,
            )
            for item in graph.objects
        ),
    )


# --------------------------------------------------------------------------- #
# 真写回的那一半：新对象进 AssetBundle(pathID=1) 的 m_PreloadTable（判据 J2/J3）
# --------------------------------------------------------------------------- #
#: AssetBundle 对象的 pathID —— Unity 恒为 1（真件 units 与我们自造的包都是；
#: 工单 §7 坑 5 与 _work/objadd_author.py 同一条口径）。
ASSET_BUNDLE_PATH_ID = 1
#: AssetBundle 的 classID（Unity 固定值；W8 探针在真件上核过 = 142）。
ASSET_BUNDLE_CLASS_ID = 142


#: 「新零件单开一条地址」时，地址指的对象类别 = 新东西的**根 GameObject**。
#: （graph.object(ADDRESS_ROOT_CLASS_ID) —— 一次克隆里恰好一个 GameObject。）
ADDRESS_ROOT_CLASS_ID = CLASS_GAME_OBJECT


@dataclass(frozen=True, slots=True)
class PreloadGrowth:
    """「新对象进 AssetBundle 的 m_PreloadTable / m_Container」那一次 ObjectGrowth ＋ 判据读数。

    growth 是 writer.ObjectGrowth（本模块**延迟** import 它，理由见 preload_growth）；
    其余几个数都是**读出来的真值**，面板逐行印它们 —— 判据 J2 的 M -> M+4 就是这里。

    addresses 空着 = **一条地址都不加**（既有行为，逐字节不变）；给了就是
    「新零件单开一条地址」：m_Container N -> N+len(addresses)，指向本次新加的那个对象。
    """

    growth: Any
    path_ids: tuple[int, ...]
    preload_before: int
    preload_after: int
    payload_before: int
    payload_after: int
    addresses: tuple[tuple[str, int], ...] = ()
    container_before: int = 0
    container_after: int = 0

    def criterion_lines(self) -> tuple[str, ...]:
        lines = [
            f"AssetBundle pathID={self.growth.path_id}：m_PreloadTable "
            f"{self.preload_before} -> {self.preload_after}（+{len(self.path_ids)}）",
            f"  本次进表的 pathID：{list(self.path_ids)}",
            f"  AssetBundle 对象字节 {self.payload_before:,} -> {self.payload_after:,} B"
            f"（走 ObjectGrowth：搬到 node[0] 尾部、8 字节对齐）",
        ]
        if self.addresses:
            lines.append(
                f"  m_Container {self.container_before} -> {self.container_after}"
                f"（+{len(self.addresses)}）："
                + "、".join(f"{path!r} -> pathID={pid}" for path, pid in self.addresses)
            )
        else:
            lines.append(
                "  m_Container 不动：新对象挂在父 Transform 的 m_Children 下，"
                "按地址取的是这个 prefab 本身（四个对象都不需要各自一条地址）"
            )
        return tuple(lines)


def check_addresses(
    graph: CloneGraph, addresses: Sequence[tuple[str, int]]
) -> tuple[tuple[str, int], ...]:
    """**纯函数**：校验「新零件单开一条地址」那张表（⛔ 四条守卫都不许静默）。

    * 每项必须是 (地址, pathID) 两元组，地址不空；
    * 地址不能给两次；pathID 也不能被两条地址点名；
    * **pathID 必须是本次新加的对象**（graph.objects 里的一个）—— 给包内**既有**对象
      加地址是另一件事（`object container add` 那条链），本链不管；
    * 地址名（最后一段）撞名由 m_Container 那半张表自己挡（renderer_slots 的守卫）——
      那半张表的逻辑只有一份，本函数不复制它。

    返回**原样顺序**的规范表（地址已 strip）。
    """
    new_ids = {int(item.path_id) for item in graph.objects}
    checked: list[tuple[str, int]] = []
    seen_paths: set[str] = set()
    seen_ids: set[int] = set()
    for index, pair in enumerate(addresses):
        try:
            asset_path, path_id = pair
        except (TypeError, ValueError) as exc:
            raise AssetError(
                f"addresses[{index}] 不是 (地址, pathID) 两元组（读到 {pair!r}）",
                hint="地址 = catalog 那条 m_InternalIds 的形状，"
                "例 Assets/Resources_moved/ModelPrefabs/My/part.prefab",
            ) from exc
        text = str(asset_path).strip()
        if not text:
            raise AssetError(
                f"addresses[{index}] 的地址是空的",
                hint="空地址取不到东西 —— 地址必须由调用方给（⛔ 本模块不造）",
            )
        if text in seen_paths:
            raise AssetError(
                f"地址 {text!r} 在这一次里给了两次",
                hint="同一条地址两条 ⇒ 游戏取到哪一个说不清；一条地址只许一条",
            )
        if (
            isinstance(path_id, bool)
            or not isinstance(path_id, (int, str))
            or (isinstance(path_id, str) and not path_id.strip().lstrip("-").isdigit())
        ):
            raise AssetError(
                f"addresses[{index}] 的 pathID 不是整数（读到 {path_id!r}）",
                hint="pathID 是包内那个对象的 SInt64 —— 给十进制整数"
                "（⛔ 不接受浮点 / 空格 / 其它写法）",
            )
        pid = int(path_id)
        if pid in seen_ids:
            raise AssetError(
                f"pathID={pid} 被两条地址点名",
                hint="一个对象一条地址就够；要两条地址指同一个对象说明地址名想错了",
            )
        if pid not in new_ids:
            raise AssetError(
                f"地址 {text!r} 指向 pathID={pid}，它不是**本次新增**的对象"
                f"（本次新增 {sorted(new_ids)}）",
                hint="这条链只管「新零件单开一条地址」；给包内既有的对象加地址"
                "走 object container add（那是另一条链，有自己的守卫）",
            )
        seen_paths.add(text)
        seen_ids.add(pid)
        checked.append((text, pid))
    return tuple(checked)


def append_preload_entries(
    preload: Sequence[Mapping[str, Any]], new_path_ids: Sequence[int]
) -> list[dict[str, int]]:
    """**纯函数**：m_PreloadTable 末尾按序追加 new_path_ids -> 新表。

    ⛔ 三条守卫（每一条都不许静默，判据 J4）：

    * 名单为空 -> 抛（少写一条就是「有爹，孩子没进预载表」）；
    * 名单里有重复 -> 抛；
    * 某个 pathID **已经在表里** -> 抛。同一张图再点一次拿到的是**新的一组 pathID**
      （allocate_path_ids = 包内最大 pathID + 1），本来就一条都不该撞 ——
      撞上说明上游把旧 pathID 又拿来用了一遍。

    只动 m_PathID 那一格，表里其余字段（m_FileID 等）原样带过去。
    """
    ids = [int(value) for value in new_path_ids]
    if not ids:
        raise AssetError(
            "要进 m_PreloadTable 的新对象是空的",
            hint="克隆图里一个对象都没有 —— 别写这一次 growth",
        )
    if len(set(ids)) != len(ids):
        raise AssetError(f"要进 m_PreloadTable 的 pathID 有重复：{ids}")
    rows = [dict(item) for item in preload]
    have = {int(item.get("m_PathID", 0) or 0) for item in rows}
    for path_id in ids:
        if path_id in have:
            raise AssetError(
                f"pathID={path_id} 已经在 m_PreloadTable 里（现有 {len(rows)} 条）",
                hint="进表前必须查重：同一组 pathID 只许进一次",
            )
        rows.append({"m_FileID": 0, "m_PathID": path_id})
        have.add(path_id)
    return rows


def _add_container_entry(
    tree: Mapping[str, Any], *, asset_path: str, path_id: int
) -> dict[str, Any]:
    """把一条**地址级条目**并进 AssetBundle 的字段树（逻辑只有一份：借 renderer_slots）。

    它同时会往 `m_PreloadTable` 尾追加一格（并把 `preloadIndex` 指向那一格）——
    所以调用方要在**追加其余新对象之前**先并容器条目（否则 preloadIndex 指错格子）。

    ⚠ 延迟 import：renderer_slots 顶层 import 本模块（它用 `clone.pptr`），
    顶层 import 它会成环（与 writer 那条同一个理由）。
    """
    from ba_mod_maker.asset_kernel import renderer_slots  # noqa: PLC0415 -- 见 docstring（成环）

    return renderer_slots.add_container_entry(tree, asset_path=asset_path, path_id=path_id)


def preload_growth(
    bundle_path: str | Path,
    graph: CloneGraph,
    *,
    asset_bundle_path_id: int = ASSET_BUNDLE_PATH_ID,
    common_strings: Mapping[int, str] | None = None,
    addresses: Sequence[tuple[str, int]] = (),
) -> PreloadGrowth:
    """**只读 ＋ 出字节（一个字节都不落盘）**：读 AssetBundle(pathID=1) -> 新对象进
    m_PreloadTable -> 出一次 writer.ObjectGrowth。

    这一次 growth 必须与父 Transform 的 m_Children 那条、以及四个 NewObject
    **落在同一次**写回里（delta_change 的 growths 参数收它）——
    少一半就是「有爹，孩子没进预载表」。口径与参考实现一致：本次新增的**每个**对象各进一条
    （_work/w8_lead_apply_clone.py:84-91 加 1 条、_work/objadd_author.py:177-196 加 2 条）。

    三条守卫（⛔ 不许静默，判据 J4）：

    * pathID=1 不在对象表里 / 它的 classID 不是 142（AssetBundle）-> 抛；
    * 新对象的 pathID 已经出现在对象表里 -> 抛（重号）；
    * AssetBundle 的字节**读一遍再写一遍不是逐字节相同** -> 抛
      （拿这种对象当生长件会把没打算动的字段一起写坏）。

    :param addresses: **地址级条目**（可选，默认空 = 一条都不加，字节与既有行为逐字节相同）。
        给 `(地址, pathID)` 就把那一条并进 `m_Container`（并让它的 preloadIndex 指向
        同一格里新加的那一项）——「新零件单开一条地址」就是它。pathID 必须是**本次新加**
        的对象（check_addresses 会挡）；地址不许重复、不许空。
        排序口径：**先并容器条目（各占 1 格预载），再追加其余的新对象**。

    ⚠ writer 只能**延迟 import**：writer 顶层反过来
    from ba_mod_maker.asset_kernel import clone（writer.py:62）—— 顶层 import 会成环。

    :raises AssetError: 上面任一条不成立，或新字节没变长（ObjectGrowth 要求长度真的变了）。
    """
    from ba_mod_maker.asset_kernel import writer  # noqa: PLC0415 -- 见 docstring（成环）

    new_ids = [item.path_id for item in graph.objects]
    with ufs.UfsReader(bundle_path) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        meta = bytes(reader.read_range(node.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        common = common_strings if common_strings is not None else unitypy_common_strings()
        schema = read_type_table(meta, common_strings=common)
        rows = object_rows(info)
        if asset_bundle_path_id not in rows:
            raise AssetError(
                f"{node.path} 的对象表里没有 pathID={asset_bundle_path_id}（AssetBundle）",
                hint="Unity 出的包都是 pathID=1；不是 1 说明取错了节点或包不是 UnityFS",
            )
        type_id = rows[asset_bundle_path_id][2]
        entry = next((item for item in schema.entries if item.index == type_id), None)
        if entry is None:
            raise AssetError(
                f"typeID={type_id} 不在类型表里（pathID={asset_bundle_path_id}）",
                hint="对象表与类型表对不上 —— 这个包别动",
            )
        if entry.class_id != ASSET_BUNDLE_CLASS_ID:
            raise AssetError(
                f"pathID={asset_bundle_path_id} 的 classID 是 {entry.class_id}，"
                f"不是 {ASSET_BUNDLE_CLASS_ID}（AssetBundle）",
                hint="预载表住在 AssetBundle 对象里；拿别的对象去改会把它的字段写坏",
            )
        clash = sorted(set(new_ids) & set(rows))
        if clash:
            raise AssetError(
                f"新对象的 pathID 与包内已有的撞了：{clash}",
                hint="新 pathID 必须大于包内最大 pathID（allocate_path_ids 管这件事）",
            )
        nodes = schema.nodes(entry.class_id)
        payload = read_object_payload(reader, node, info, asset_bundle_path_id)
        if roundtrip(payload, nodes) != payload:
            raise AssetError(
                f"pathID={asset_bundle_path_id} 的字段树往返不是逐字节相同（{len(payload):,} B）",
                hint="这种对象不能当生长件：写回去会连带改坏别的字节",
            )
        tree = read_object(payload, nodes)
        before = len(tree["m_PreloadTable"])
        container_before = len(tree.get("m_Container") or [])
        checked = check_addresses(graph, addresses)
        for asset_path, path_id in checked:
            tree = _add_container_entry(tree, asset_path=asset_path, path_id=path_id)
        # 已经被容器条目带进预载表的那些 pid 不再追加（append_preload_entries 会查重并抛）
        addressed = {pid for _path, pid in checked}
        rest = [pid for pid in new_ids if pid not in addressed]
        if rest:
            tree["m_PreloadTable"] = append_preload_entries(tree["m_PreloadTable"], rest)
        container_after = len(tree.get("m_Container") or [])
        new_payload = write_object(tree, nodes)
    if len(new_payload) <= len(payload):
        raise AssetError(
            f"AssetBundle 的新字节没变长（{len(payload):,} -> {len(new_payload):,} B）",
            hint="ObjectGrowth 要求长度真的变了；没变说明预载表那几条没进去",
        )
    growth = writer.ObjectGrowth(
        path_id=asset_bundle_path_id,
        payload=new_payload,
        name=str(tree.get("m_Name") or "AssetBundle"),
        type_name="AssetBundle",
    )
    return PreloadGrowth(
        growth=growth,
        path_ids=tuple(new_ids),
        preload_before=before,
        preload_after=before + len(new_ids),
        payload_before=len(payload),
        payload_after=len(new_payload),
        addresses=checked,
        container_before=container_before,
        container_after=container_after,
    )
