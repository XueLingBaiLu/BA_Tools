r"""W3 · MonoScript(115) 自造 + MonoBehaviour(114) 的 `m_Script` 绑定公式。

## 它解决什么

一个 MonoBehaviour 组件在字节里只有一条 `m_Script: PPtr<MonoScript>`；
"这是哪个脚本"这件事**不在字段里**，而在三张表的联动上（units 包实测 5,035/5,035）：

    类型表项  (class_id=114, script_type_index=si)        ← 用【下标】找脚本
       │
    m_ScriptTypes[si]  file_index=0, local_identifier=P    ← 用【pathID】说"脚本是谁"
       │
    对象表里 pathID = P 的那颗 MonoScript(115)             ← 唯一的身份格
       │   m_ClassName / m_Namespace / m_AssemblyName = 三串真实类名
       │
    每个实例  MonoBehaviour.m_Script = PPtr(m_FileID=0, m_PathID=P)

本模块把这条公式变成产品能力：
**枚举目录 → 自造 MonoScript → 生成绑定（spec / script_type_map / m_Script）→ 校验**。

## ⛔ 写死的边界（模块级，绕不过去）

1. **自造 MonoScript 只能指向游戏里【已经存在】的类。**
   游戏是 IL2CPP 出包的，托管类表固化在 `GameAssembly.dll` + `global-metadata.dat` 里 ——
   **加不了新类**。所以本模块一个类都不"发明"：`m_ClassName / m_Namespace /
   m_AssemblyName` 只能从游戏已有的 MonoScript(115) 对象上**逐字符复制**。
   * 指向目录里没有的类 ⇒ :meth:`ScriptCatalogue.require` 当场抛 `AssetError`；
   * 已经写进包的 ⇒ :func:`verify_package` 报成 problem。
   * ⚠ **负控（能红）**：`tests/test_build_monoscript.py` 造一颗
     `m_ClassName='RouteANoSuchClass'` 的 MonoScript，:func:`verify_package` 必须报它；
     把那个类补进目录之后同一条断言必须变绿 —— 不能变绿的尺子是假绿。
2. **字段名与默认值不许猜。**
   MonoScript 的类型树只有 **6 个顶层字段**（units 实测 type_id=0 / classID=115，23/23 对象同形）：
   `m_Name / m_ExecutionOrder / m_PropertiesHash / m_ClassName / m_Namespace / m_AssemblyName`。
   :func:`check_mono_script_tree` 每次**对着真件的类型树逐键对账**，对不上就抛
   （把实测到的键列进报错里）—— 换游戏版本时它先响，而不是悄悄写错字节。
3. **`script_types` 是【整份 SerializedFile】的表。**
   照抄真件会把表里每一项都指到**真件那个包**的 pathID（实测本包 0/22 命中），
   Unity 解析脚本时取到空 ⇒ 加载期原生空指针。所以必须用
   :func:`builder.remap_script_types` 把**本包用到的**那几项改成自己包的 MonoScript pathID；
   :attr:`MonoScriptPlan.script_type_map` 就是喂给
   `builder.build_object_bundle(script_type_map=...)` 的那一份。
4. **`m_Name` 是类名，不是随便起的**：真件 23/23 都等于 `m_ClassName`（默认照抄，可显式覆盖）；
   `m_ExecutionOrder`（23/23 = 0）与 `m_PropertiesHash`（Unity 编译期算的 16 字节）
   **原样照抄源对象**，⛔ 不自己编。

## 复算（cwd = 仓库根 `D:\断箭模组制作\BA_Mod_Maker_2.0`，venv python）

    .venv\Scripts\python.exe _work\mono-line\mono-probe.py units
      -> MonoBehaviour 5035 个：一致 = 4431，不一致 = 0，读不了 = 604   （a2 那条尺子）
      -> 同一条尺子喂 clone.ref_type_pool 之后：一致 = 5035，不一致 = 0，读不了 = 0
    .venv\Scripts\python.exe _work\mono-line\mono-probe.py library
      -> 78 个 bundle / 977 颗 MonoScript / 760 条去重 (assembly, namespace, class)
    .venv\Scripts\python.exe -m pytest tests/test_build_monoscript.py -q

604 条"读不了"**不是绑定公式的问题**：那 604 个对象是 AnimationHub(script_index 17)，
它的字段树里有 `[SerializeReference]`（`ReferencedObjectData`），不喂 `ref_types` 时
`clone.read_object` 按设计抛。喂上之后 5035/5035 一致、0 读不了 —— 两条读数都记下来，
别拿"能读了"去改那条老尺子的口径。

## 边界（如实写，别让它变成假绿）

* 本模块只保证**资产字节层面**的自洽（三方绑定 + 类存在 + 无悬空）；
  "游戏运行时怎么把 `m_Script` 变成托管类"这一步**没有本机证据**（Unity 引擎在
  `UnityPlayer.dll` 原生层，无 IL2CPP 产物）—— 见 `_work/a1-script-binding.md` §3 的 **[没读]**。
* :func:`verify_package` 的"类是否存在"只能对着**我们扫过的目录**判
  （:func:`scan_library` 的产物）。目录没扫全时它是"没报"而不是"不存在"，
  所以 `known=None` 时它会明写一行"只查包内自洽"。
"""

from __future__ import annotations

import copy
import difflib
import struct
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone, unityfs
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "CLASS_MONO_BEHAVIOUR",
    "CLASS_MONO_SCRIPT",
    "MONO_SCRIPT_FIELDS",
    "BindingCensus",
    "MonoScriptBinding",
    "MonoScriptPlan",
    "MonoScriptRecord",
    "PackageCheck",
    "ScriptCatalogue",
    "ScriptIdentity",
    "ScriptLibrary",
    "ScriptTypeRow",
    "SerializedFileView",
    "bind_behaviour_script",
    "census_script_binding",
    "check_mono_script_tree",
    "make_mono_script_fields",
    "mono_script_nodes",
    "mono_script_spec",
    "open_view",
    "plan_mono_scripts",
    "read_catalogue",
    "read_mono_script",
    "scan_library",
    "verify_bundle",
    "verify_package",
]

#: MonoScript —— `script_types` 那一列指的**就是**它在对象表里的 pathID。
CLASS_MONO_SCRIPT = builder.CLASS_MONO_SCRIPT
#: MonoBehaviour —— 同 classID 有多棵树，索引键是 (114, script_type_index)。
CLASS_MONO_BEHAVIOUR = builder.CLASS_MONO_BEHAVIOUR

#: MonoScript(115) 的**全部**顶层字段，顺序 = 真件类型树的序列化顺序（units 实测）。
#: ⛔ 不是"照 Unity 文档抄的"：复算 `_work/mono-line/mono-probe.py tree`。
MONO_SCRIPT_FIELDS: tuple[str, ...] = (
    "m_Name",
    "m_ExecutionOrder",
    "m_PropertiesHash",
    "m_ClassName",
    "m_Namespace",
    "m_AssemblyName",
)

#: 报错里最多列几条候选（类名撞车时）。
_MAX_CANDIDATES = 12


# --------------------------------------------------------------------------- #
# 目录：游戏的类 -> 它在本文件里的 (script_index, pathID)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ScriptIdentity:
    """一个托管类的**三串身份**（Unity 的书写顺序：类 / 命名空间 / 程序集）。

    这三串就是 MonoScript 对象里那三个字段，逐字符相等才有意义 ——
    所以比较用整个三元组，⛔ 不许只比类名（同名不同命名空间的类在 Unity 里很常见）。

    ⛔ **不能用来"发明"类**：IL2CPP 出包后托管类表是固化的，
    这里的每一个实例都必须来自游戏已有的 MonoScript(115) 对象（模块头边界 1）。
    """

    class_name: str
    namespace: str = ""
    assembly_name: str = ""

    def __post_init__(self) -> None:
        if not str(self.class_name):
            raise AssetError(
                "ScriptIdentity 的类名不能是空串",
                hint="空类名的 MonoScript 在真件里没见过（23/23 都非空）；读到它说明解析走偏了",
            )

    @property
    def key(self) -> tuple[str, str, str]:
        """`(assembly, namespace, class)` —— 目录表的排序键（任务书里的书写顺序）。"""
        return (str(self.assembly_name), str(self.namespace), str(self.class_name))

    @property
    def qualified_name(self) -> str:
        """`命名空间.类名`（没有命名空间时就是类名）。"""
        return f"{self.namespace}.{self.class_name}" if self.namespace else str(self.class_name)

    def __str__(self) -> str:
        return f"{self.assembly_name}|{self.namespace}|{self.class_name}"

    @classmethod
    def parse(cls, text: str) -> ScriptIdentity:
        """`'程序集|命名空间|类名'` 或 `'类名'`（短名，留给 require() 去消歧）。"""
        parts = [p.strip() for p in str(text).split("|")]
        if len(parts) == 3:
            return cls(class_name=parts[2], namespace=parts[1], assembly_name=parts[0])
        if len(parts) == 1 and parts[0]:
            return cls(class_name=parts[0])
        raise AssetError(
            f"读不出脚本身份：{text!r}",
            hint='写法只有两种："类名" 或 "程序集|命名空间|类名"',
        )


@dataclass(frozen=True, slots=True)
class MonoScriptRecord:
    """游戏里**已有**的一颗 MonoScript(115) 对象（真件读数，不是我们会造的东西）。"""

    path_id: int
    identity: ScriptIdentity
    name: str
    execution_order: int
    properties_hash: bytes
    size: int
    pack: str = ""

    def line(self) -> str:
        return (
            f"pathID={self.path_id:>22} {self.identity}  "
            f"m_Name={self.name!r} order={self.execution_order} "
            f"hash={self.properties_hash.hex()} {self.size} B  [{self.pack}]"
        )


@dataclass(frozen=True, slots=True)
class ScriptTypeRow:
    """`m_ScriptTypes` 的一行 ＋ 它在本文件里连到的那颗 MonoScript。"""

    script_index: int
    file_index: int
    local_identifier: int
    identity: ScriptIdentity | None
    behaviour_type_ids: tuple[int, ...] = ()

    @property
    def in_file(self) -> bool:
        """`local_identifier` 是否落在**本文件**的对象表里（真件 22/22 成立）。"""
        return self.identity is not None

    def line(self) -> str:
        who = str(self.identity) if self.identity else "（本文件对象表里没有这颗 MonoScript）"
        return (
            f"script_index {self.script_index:>2} <- file_index {self.file_index} "
            f"local_identifier {self.local_identifier:>22}  {who}"
            f"  114 type_ids={list(self.behaviour_type_ids)}"
        )


@dataclass(frozen=True, slots=True)
class ScriptCatalogue:
    """**一个 SerializedFile** 的脚本目录：`(assembly, namespace, class) ↔ script_index`。

    `problems` 是读的时候顺手记下的不自洽（⚠ 不是"读不了"）：有它就说明这份文件的
    三方绑定不成立 —— 造包时拿它当 schema 会把这个毛病一起抄过去。
    """

    source: str
    rows: tuple[ScriptTypeRow, ...]
    mono_scripts: tuple[MonoScriptRecord, ...]
    problems: tuple[str, ...] = ()
    #: `script_types` 没指到的 MonoScript pathID（units 实测 1 条：SpawnOverDistance）。
    unreferenced: tuple[int, ...] = ()
    #: `pathID -> 真件那颗 MonoScript 的**完整字段树**（自造时的模板，逐键照抄）。
    templates: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)

    def template(self, path_id: int) -> dict[str, Any]:
        """取模板字段树（深拷贝）；没有就抛 —— ⛔ 不许凭空造字段。"""
        stored = self.templates.get(int(path_id))
        if stored is None:
            raise AssetError(
                f"{self.source} 的目录里没有 pathID={path_id} 的字段树",
                hint="目录是 read_catalogue() 读出来的才带模板（它读的就是真件那颗树）",
            )
        return copy.deepcopy(dict(stored))

    def row(self, script_index: int) -> ScriptTypeRow:
        for item in self.rows:
            if item.script_index == int(script_index):
                return item
        raise AssetError(
            f"{self.source} 的 m_ScriptTypes 里没有 script_index={script_index}",
            hint=f"这一份文件只有 {len(self.rows)} 行（0…{len(self.rows) - 1}）",
        )

    def records(self, want: ScriptIdentity | str) -> tuple[MonoScriptRecord, ...]:
        """按身份（或短类名）在本文件里找 MonoScript 对象；0 条 / 多条都**不猜**。"""
        identity = ScriptIdentity.parse(want) if isinstance(want, str) else want
        return tuple(r for r in self.mono_scripts if _same_class(r.identity, identity))

    def script_index_of(self, want: ScriptIdentity | str) -> int:
        identity = ScriptIdentity.parse(want) if isinstance(want, str) else want
        hits = [
            r for r in self.rows if r.identity is not None and _same_class(r.identity, identity)
        ]
        if not hits:
            raise AssetError(
                f"{self.source} 的 m_ScriptTypes 里没有 {identity}",
                hint=_identity_hint(want, (r.identity for r in self.rows if r.identity)),
            )
        if len(hits) > 1:
            raise AssetError(
                f"{identity} 在本文件里命中 {len(hits)} 行 "
                f"script_index={[h.script_index for h in hits]}",
                hint="身份要写全三串（程序集|命名空间|类名），别只写类名",
            )
        return int(hits[0].script_index)

    def require(self, want: ScriptIdentity | str) -> MonoScriptRecord:
        """取一颗**游戏已有**的 MonoScript；没有 / 不唯一都抛（模块头边界 1 的落点）。"""
        identity = ScriptIdentity.parse(want) if isinstance(want, str) else want
        hits = self.records(identity)
        if not hits:
            raise AssetError(
                f"游戏已有的脚本目录里没有 {identity} —— IL2CPP 加不了新类",
                hint=_identity_hint(want, (r.identity for r in self.mono_scripts)),
            )
        if len(hits) > 1:
            raise AssetError(
                f"{identity} 在本文件里命中 {len(hits)} 颗 MonoScript：{[h.path_id for h in hits]}",
                hint="身份要写全三串（程序集|命名空间|类名）",
            )
        return hits[0]

    def table(self) -> tuple[tuple[str, str, str, int], ...]:
        """`(assembly, namespace, class) ↔ script_index` 表（任务书要的那张）。"""
        out = [(*r.identity.key, int(r.script_index)) for r in self.rows if r.identity is not None]
        return tuple(sorted(out))

    def table_lines(self) -> tuple[str, ...]:
        return tuple(
            f"{asm}|{ns}|{cls}  ->  script_index {si}  "
            f"(114 type_ids {list(self.row(si).behaviour_type_ids)})"
            for asm, ns, cls, si in self.table()
        )

    def summary(self) -> str:
        return (
            f"{self.source}: m_ScriptTypes {len(self.rows)} 行 / "
            f"MonoScript(115) {len(self.mono_scripts)} 颗 / 表外 {len(self.unreferenced)} 颗 / "
            f"不自洽 {len(self.problems)} 条"
        )


def _same_class(identity: ScriptIdentity, want: ScriptIdentity) -> bool:
    """身份比较：写全了三串就逐字符比；只写了类名（短名）就比类名。"""
    if want.assembly_name and want.assembly_name != identity.assembly_name:
        return False
    if want.namespace and want.namespace != identity.namespace:
        return False
    return identity.class_name == want.class_name


def _identity_hint(want: ScriptIdentity | str, pool: Iterable[ScriptIdentity]) -> str:
    """"没有这个类"的报错尾巴：先给最接近的类名，再钉死边界。"""
    identities = list(pool)
    names = sorted({i.class_name for i in identities})
    text = want.class_name if isinstance(want, ScriptIdentity) else str(want)
    if names:
        close = difflib.get_close_matches(text, names, n=_MAX_CANDIDATES, cutoff=0.6)
        if close:
            head = f"目录里最接近的类名：{close}"
        else:
            head = f"目录里有 {len(names)} 个类名：{names[:_MAX_CANDIDATES]}"
    else:
        head = "目录里一个类都没有（这个文件的 m_ScriptTypes 是空的？）"
    return head + "。⛔ 自造 MonoScript 只能指向游戏已存在的类（IL2CPP 加不了新类），不许编一个"


# --------------------------------------------------------------------------- #
# 读组件：一个 SerializedFile 的只读视图
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SerializedFileView:
    """一个 SerializedFile 的只读视图（节点 + 元数据 + 对象表 + 按需解块）。

    ⛔ **不整包 load**：只读元数据区 + 按 pathID `read_range` 单个对象。

    ⚠ 解析走 `metadata_writer`（`parse_serialized_metadata`）这一条，
    **不走** `core.asset.ufs.read_serialized_file` 的类型表游标：2026-10-08 实测
    `prefabsgui` / `prefabsme` 两个包的 ufs 游标跑飞（要读到偏移 17,063,036，
    而元数据只有 3,583,522 B），`metadata_writer` 两条都解得开、units 上两条路
    162,010 行逐行相等（复算 `_work/mono-line/mw-vs-ufs.py`）。
    """

    source: str
    node_path: str
    node_offset: int
    data_offset: int
    meta: mw.SerializedMetadata
    reader: ufs.UfsReader | None = None
    common_strings: Mapping[int, str] | None = None
    #: 内存视图：直接给 `pathID -> 对象字节`（自造包回读 / 用例用，免开真包）。
    payloads: Mapping[int, bytes] | None = None
    _nodes: dict[int, tuple[clone.TypeNode, ...]] = field(default_factory=dict, repr=False)
    _by_pid: dict[int, mw.ObjectEntry] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_pid = {int(item.path_id): item for item in self.meta.objects}
        if len(self._by_pid) != len(self.meta.objects):
            raise AssetError(f"{self.source}: 对象表里有重复 pathID")

    # ---------------- 对象表 ----------------
    def path_ids(self) -> tuple[int, ...]:
        return tuple(sorted(self._by_pid))

    def entry(self, path_id: int) -> mw.ObjectEntry:
        item = self._by_pid.get(int(path_id))
        if item is None:
            raise AssetError(
                f"{self.source}: 对象表里没有 pathID={path_id}",
                hint=f"本文件 {len(self._by_pid)} 个对象",
            )
        return item

    def has(self, path_id: int) -> bool:
        return int(path_id) in self._by_pid

    def class_id(self, path_id: int) -> int:
        return int(self.meta.types[self.entry(path_id).type_id].class_id)

    def script_index(self, path_id: int) -> int:
        return int(self.meta.types[self.entry(path_id).type_id].script_type_index)

    def type_ids_of(self, class_id: int) -> tuple[int, ...]:
        return tuple(self.meta.find_type_ids(int(class_id)))

    def path_ids_of_class(self, class_id: int) -> tuple[int, ...]:
        wanted = set(self.type_ids_of(class_id))
        return tuple(pid for pid in self.path_ids() if int(self.entry(pid).type_id) in wanted)

    # ---------------- 字节 ----------------
    def nodes_for(self, type_id: int) -> tuple[clone.TypeNode, ...]:
        """类型表第 `type_id` 项的内嵌类型树（按需解，缓存）。"""
        key = int(type_id)
        if key not in self._nodes:
            if not 0 <= key < len(self.meta.types):
                raise AssetError(
                    f"{self.source}: type_id={type_id} 越出类型表（{len(self.meta.types)} 项）"
                )
            tree = self.meta.types[key].type_tree
            if tree is None:
                raise AssetError(
                    f"{self.source}: 类型表第 {key} 项没有类型树",
                    hint="enable_type_tree=False 的文件只有字段偏移，读不出字段名 —— 本节不解",
                )
            strings = self.common_strings
            if strings is None:
                strings = clone.unitypy_common_strings()
                self.common_strings = strings
            self._nodes[key] = clone.read_type_nodes(
                mw.encode_type_tree(tree, self.meta.header.endian), common_strings=strings
            )
        return self._nodes[key]

    def raw(self, path_id: int) -> bytes:
        """一个对象的原始字节（内存视图优先，其次按需解块）。"""
        pid = int(path_id)
        if self.payloads is not None:
            blob = self.payloads.get(pid)
            if blob is None:
                raise AssetError(f"{self.source}: 内存视图里没有 pathID={pid} 的字节")
            return bytes(blob)
        if self.reader is None:
            raise AssetError(f"{self.source}: 这个视图没有读源（reader 与 payloads 都是空）")
        item = self.entry(pid)
        start = int(self.node_offset) + int(self.data_offset) + int(item.byte_start)
        return self.reader.read_range(start, int(item.byte_size))

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None

    def __enter__(self) -> SerializedFileView:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _pick_serialized_node(reader: ufs.UfsReader, path: str) -> ufs.Node:
    """挑出容器里那个 SerializedFile 节点（⛔ 不假设它是第一个）。

    判据 = 头 48 字节解得开、且头里的 `file_size` 与节点大小**相等**
    （`core.asset.ufs.read_serialized_file` 的同一条硬对账）。
    """
    tried: list[str] = []
    for node in reader.nodes:
        if node.path.endswith(".resS") or node.path.endswith(".resource"):
            continue
        tried.append(node.path)
        try:
            header = mw.parse_header(reader.read_range(node.offset, 48))
        except (AssetError, struct.error):
            continue
        if int(header.file_size) == int(node.size):
            return node
    raise AssetError(
        f"{path}: 找不到 SerializedFile 节点（试过 {tried}）",
        hint="容器里没有解得出的 SerializedFile；换一个包，或显式给 node_path",
    )


def open_view(path: str | Path, *, node_path: str | None = None) -> SerializedFileView:
    """打开一个 UnityFS（bundle / data.unity3d）成只读视图。用 `with` 收尾。"""
    target = Path(path)
    reader = unityfs.open_bundle(target)
    ok = False
    try:
        node = reader.node(node_path) if node_path else _pick_serialized_node(reader, str(target))
        header = mw.parse_header(reader.read_range(node.offset, 48))
        # ⚠ 要读到 **data_offset**（含 0~15 字节对齐填充），不是 48 + metadata_size：
        # metadata_writer 的 verify 会核「填充区也在手上」，只读到头+体它会抛
        # 「取不到填充区」。实测 units：48 + 4,128,977 = 4,129,025 < data_offset 4,129,040。
        meta = mw.parse_serialized_metadata(reader.read_range(node.offset, header.data_offset))
        ok = True
    finally:
        if not ok:
            reader.close()
    return SerializedFileView(
        source=str(target),
        node_path=node.path,
        node_offset=int(node.offset),
        data_offset=int(header.data_offset),
        meta=meta,
        reader=reader,
    )


# --------------------------------------------------------------------------- #
# 枚举：真件的字段树对账 + 目录表
# --------------------------------------------------------------------------- #
def mono_script_nodes(view: SerializedFileView) -> tuple[clone.TypeNode, ...]:
    """MonoScript(115) 的类型树节点。classID=115 **必须唯一一项**（真件如此）。"""
    type_ids = view.type_ids_of(CLASS_MONO_SCRIPT)
    if len(type_ids) != 1:
        raise AssetError(
            f"{view.source}: classID=115 在类型表里命中 {len(type_ids)} 项 {list(type_ids)}",
            hint="MonoScript 只有一种形状（真件 units：type_id=0）；多出来说明读错了包",
        )
    return view.nodes_for(type_ids[0])


def check_mono_script_tree(nodes: Sequence[clone.TypeNode]) -> tuple[str, ...]:
    """**逐键对账**：MonoScript 的顶层字段名必须与真件实测的那 6 个逐字相等。

    对不上就抛（把两边都列出来）—— 这样"换游戏版本之后字段变了"是第一现场，
    而不是写出一颗字节看着对、字段其实错位的 MonoScript。
    """
    names = tuple(node.name for node in nodes if node.level == 1)
    if names != MONO_SCRIPT_FIELDS:
        raise AssetError(
            f"MonoScript(115) 的类型树字段与实测不符：{list(names)}",
            hint=f"实测（units，23/23 同形）是 {list(MONO_SCRIPT_FIELDS)}。"
            "换游戏版本后要重新量（_work/mono-line/mono-probe.py tree），"
            "⛔ 不许照 Unity 文档猜字段名/默认值",
        )
    return names


def _hash128_bytes(value: Any, *, where: str) -> bytes:
    """Hash128 的**两种形状**都认。

    ⚠ clone 的读侧按类型树走：m_PropertiesHash 解出来是
    {"bytes[0]": .., "bytes[15]": ..} 这样一个 16 格的映射（**不是 bytes**）——
    第一版这里写的是 bytes(fields[...])，用例当场把它抓出来
    （Hash128 m_PropertiesHash 要一个映射，拿到 bytes）。写侧同样只认映射。
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, Mapping):
        try:
            return bytes(int(value[f"bytes[{i}]"]) & 0xFF for i in range(16))
        except KeyError as exc:
            raise AssetError(f"{where}: Hash128 缺一格 {exc}") from exc
    raise AssetError(
        f"{where}: Hash128 的形状认不得（{type(value).__name__}）",
        hint="clone 读出来的是 16 格映射；bytes 只在手搓字段树时给",
    )


def read_mono_script(view: SerializedFileView, path_id: int) -> MonoScriptRecord:
    """读一颗 MonoScript(115)：字段树解出来之后**键集合再对一次账**。"""
    nodes = mono_script_nodes(view)
    check_mono_script_tree(nodes)
    blob = view.raw(path_id)
    fields = clone.read_object(blob, nodes)
    keys = set(fields)
    if keys != set(MONO_SCRIPT_FIELDS):
        raise AssetError(
            f"{view.source}: pathID={path_id} 的 MonoScript 字段是 {sorted(keys)}",
            hint=f"与实测的 {list(MONO_SCRIPT_FIELDS)} 不一致 —— 拿错类型树了？",
        )
    return MonoScriptRecord(
        path_id=int(path_id),
        identity=ScriptIdentity(
            class_name=str(fields["m_ClassName"]),
            namespace=str(fields["m_Namespace"]),
            assembly_name=str(fields["m_AssemblyName"]),
        ),
        name=str(fields["m_Name"]),
        execution_order=int(fields["m_ExecutionOrder"]),
        properties_hash=_hash128_bytes(fields["m_PropertiesHash"], where=f"pathID={path_id}"),
        size=len(blob),
        pack=view.source,
    )


def has_mono_script(view: SerializedFileView) -> bool:
    """这个文件里有没有 MonoScript(115) 这一类。

    ⚠ **没有是正常的**：全库 78 个包里 36 个是纯贴图 / 图标 / 视频包，类型表里
    根本没有 115 —— 那不是"打不开"，别把它记成错误（实测 2026-10-08：
    第一版 scan_library 把这 36 个包全报成 AssetError，读数就成了"打不开 36 个"）。
    """
    return bool(view.type_ids_of(CLASS_MONO_SCRIPT))


def read_catalogue(view: SerializedFileView) -> ScriptCatalogue:
    """枚举**一个文件**的脚本目录：`(assembly, namespace, class) ↔ script_index`。

    同时做三方对账（模块头那张图）并记进 `problems` —— 造包前先看它空不空。
    """
    nodes = mono_script_nodes(view)
    check_mono_script_tree(nodes)
    mono_type_ids = set(view.type_ids_of(CLASS_MONO_SCRIPT))
    records: list[MonoScriptRecord] = []
    templates: dict[int, dict[str, Any]] = {}
    problems: list[str] = []
    for pid in view.path_ids():
        if int(view.entry(pid).type_id) in mono_type_ids:
            records.append(read_mono_script(view, pid))
            templates[int(pid)] = clone.read_object(view.raw(pid), nodes)

    by_pid = {r.path_id: r for r in records}
    rows: list[ScriptTypeRow] = []
    used: set[int] = set()
    types = view.meta.types
    for index, item in enumerate(view.meta.script_types):
        pid = int(item.local_identifier)
        record = by_pid.get(pid)
        if record is not None:
            used.add(pid)
        elif int(item.file_index) == 0:
            problems.append(
                f"m_ScriptTypes[{index}].local_identifier={pid} 不在本文件对象表里"
                f"（file_index={item.file_index} 说它是本文件的）—— 悬空"
            )
        type_ids = tuple(
            i
            for i, t in enumerate(types)
            if int(t.class_id) == CLASS_MONO_BEHAVIOUR and int(t.script_type_index) == index
        )
        rows.append(
            ScriptTypeRow(
                script_index=index,
                file_index=int(item.file_index),
                local_identifier=pid,
                identity=record.identity if record is not None else None,
                behaviour_type_ids=type_ids,
            )
        )
    for i, t in enumerate(types):
        si = int(t.script_type_index)
        if int(t.class_id) == CLASS_MONO_BEHAVIOUR and si >= len(view.meta.script_types):
            problems.append(
                f"类型表第 {i} 项是 MonoBehaviour，但 script_type_index={si} "
                f"越出 m_ScriptTypes（{len(view.meta.script_types)} 行）"
            )
    for record in records:
        if not record.identity.class_name:
            problems.append(f"pathID={record.path_id} 的 MonoScript 类名是空串")
    return ScriptCatalogue(
        source=view.source,
        rows=tuple(rows),
        mono_scripts=tuple(sorted(records, key=lambda r: r.path_id)),
        problems=tuple(problems),
        unreferenced=tuple(sorted(set(by_pid) - used)),
        templates=templates,
    )


# --------------------------------------------------------------------------- #
# 全库目录：一个发行版里所有 bundle 的脚本身份并集
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ScriptLibrary:
    """**整份发行版**的脚本目录（78 个 bundle 的并集）—— "哪些类存在"的唯一判据。

    ⛔ 边界：目录只覆盖**扫过的那些包**。没扫到的包里的类在这里是"没报"，
    不是"不存在"（`errors` 会列出打不开的包）。
    """

    packs: tuple[ScriptCatalogue, ...] = ()
    errors: tuple[str, ...] = ()
    #: 类型表里根本没有 115 的包（纯贴图 / 视频包）—— **不是**错误，但也不静默。
    packs_without_scripts: tuple[str, ...] = ()

    def identities(self) -> tuple[ScriptIdentity, ...]:
        seen = {r.identity for pack in self.packs for r in pack.mono_scripts}
        return tuple(sorted(seen, key=lambda i: i.key))

    def records(self, want: ScriptIdentity | str) -> tuple[MonoScriptRecord, ...]:
        return tuple(r for pack in self.packs for r in pack.records(want))

    def has(self, want: ScriptIdentity | str) -> bool:
        return bool(self.records(want))

    def packs_with(self, want: ScriptIdentity | str) -> tuple[str, ...]:
        return tuple(sorted({r.pack for r in self.records(want)}))

    def require(self, want: ScriptIdentity | str) -> MonoScriptRecord:
        hits = self.records(want)
        if not hits:
            raise AssetError(
                f"整份发行版的脚本目录里没有 {want} —— IL2CPP 加不了新类",
                hint=_identity_hint(want, self.identities()),
            )
        return hits[0]

    def summary(self) -> str:
        return (
            f"全库脚本目录：{len(self.packs)} 个包有脚本 / {len(self.identities())} 条去重身份 / "
            f"{sum(len(p.mono_scripts) for p in self.packs)} 颗 MonoScript / "
            f"没脚本的包 {len(self.packs_without_scripts)} 个 / 打不开 {len(self.errors)} 个"
        )


def scan_library(
    bundle_dir: str | Path, *, pattern: str = "*.bundle", node_path: str | None = None
) -> ScriptLibrary:
    """扫一个目录下所有 bundle，产出 `:class:`ScriptLibrary`（只读；⛔ 不整包 load）。

    单个包打不开**不中止**整次扫描，而是记进 `errors` —— 但也不静默：
    `:meth:`ScriptLibrary.summary` 会把条数印出来。
    """
    root = Path(bundle_dir)
    packs: list[ScriptCatalogue] = []
    errors: list[str] = []
    without: list[str] = []
    for path in sorted(root.glob(pattern)):
        try:
            with open_view(path, node_path=node_path) as view:
                if not has_mono_script(view):
                    without.append(path.name)
                    continue
                packs.append(read_catalogue(view))
        except (AssetError, OSError, ValueError, KeyError, struct.error) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return ScriptLibrary(
        packs=tuple(packs), errors=tuple(errors), packs_without_scripts=tuple(without)
    )


# --------------------------------------------------------------------------- #
# 自造：从真件模板复制一颗 MonoScript
# --------------------------------------------------------------------------- #
def make_mono_script_fields(
    template: Mapping[str, Any], identity: ScriptIdentity | str, *, name: str | None = None
) -> dict[str, Any]:
    """把**真件那颗** MonoScript 的字段树复制成我们的：只换三串身份 ＋ `m_Name`。

    `m_ExecutionOrder` / `m_PropertiesHash` **原样照抄**（模块头边界 4）；
    `template` 的键集合必须与实测的 6 键逐字相等，否则抛 —— 不许"少一个字段也能写"。

    ⛔ `identity` 必须是**游戏已存在**的类：本函数只对字段树负责，
    "这个类在不在游戏里"由 `:meth:`ScriptCatalogue.require` / `:func:`verify_package` 判。
    """
    want = ScriptIdentity.parse(identity) if isinstance(identity, str) else identity
    keys = set(template)
    if keys != set(MONO_SCRIPT_FIELDS):
        raise AssetError(
            f"MonoScript 模板的字段是 {sorted(keys)}",
            hint=f"实测只有 {list(MONO_SCRIPT_FIELDS)}；"
            "多/少一个都说明拿到的不是真件 MonoScript 的字段树",
        )
    out = copy.deepcopy(dict(template))
    out["m_ClassName"] = want.class_name
    out["m_Namespace"] = want.namespace
    out["m_AssemblyName"] = want.assembly_name
    out["m_Name"] = want.class_name if name is None else str(name)
    return out


def mono_script_spec(
    fields: Mapping[str, Any], *, path_id: int, label: str = ""
) -> builder.ObjectSpec:
    """一颗自造 MonoScript 的 `builder.ObjectSpec`（classID=115，没有 script_index）。"""
    keys = set(fields)
    if keys != set(MONO_SCRIPT_FIELDS):
        raise AssetError(
            f"自造 MonoScript 的字段是 {sorted(keys)}",
            hint=f"实测只有 {list(MONO_SCRIPT_FIELDS)}；用 make_mono_script_fields() 造它",
        )
    return builder.ObjectSpec(
        class_id=CLASS_MONO_SCRIPT,
        fields=dict(fields),
        path_id=int(path_id),
        label=label or "mono-script",
    )


@dataclass(frozen=True, slots=True)
class MonoScriptBinding:
    """一个类的绑定：源包那颗 MonoScript ↔ 我们包里这颗。"""

    identity: ScriptIdentity
    script_index: int
    source_path_id: int
    package_path_id: int

    @property
    def type_key(self) -> tuple[int, int]:
        """类型表的索引键 —— **必须点名 script_index**（units 里 114 有 22 项）。"""
        return (CLASS_MONO_BEHAVIOUR, int(self.script_index))

    def line(self) -> str:
        return (
            f"{self.identity}  script_index {self.script_index:>2}  "
            f"真件 pathID {self.source_path_id:>22} -> 本包 pathID {self.package_path_id:>22}  "
            f"类型表键 {self.type_key}"
        )


@dataclass(frozen=True, slots=True)
class MonoScriptPlan:
    """一次"我要在本包里让这些类有脚本"的全部产物。

    * `mono_specs` ⇒ 塞进 `unit_bundle.assemble_unit_bundle(extra=...)`；
    * `script_type_map` ⇒ 喂给 `builder.build_object_bundle(script_type_map=...)`；
    * `:meth:`script_pptr` ⇒ 每个 MonoBehaviour 的 `m_Script`（也是
      `prefab_build.MonoBehaviourSpec.script` 要的那个值）。
    """

    bindings: tuple[MonoScriptBinding, ...]
    mono_specs: tuple[builder.ObjectSpec, ...]

    @property
    def script_type_map(self) -> dict[int, int]:
        """`script_type_index -> 本包 MonoScript 的 pathID`（喂 remap_script_types）。"""
        return {int(b.script_index): int(b.package_path_id) for b in self.bindings}

    @property
    def script_path_ids(self) -> tuple[int, ...]:
        """本包 MonoScript 的 pathID —— `builder.dangling_pptrs(script_path_ids=...)` 要它。"""
        return tuple(int(b.package_path_id) for b in self.bindings)

    def binding(self, want: ScriptIdentity | str) -> MonoScriptBinding:
        identity = ScriptIdentity.parse(want) if isinstance(want, str) else want
        hits = [b for b in self.bindings if _same_class(b.identity, identity)]
        if len(hits) != 1:
            raise AssetError(
                f"这份计划里 {want} 命中 {len(hits)} 条绑定",
                hint=f"计划里有：{[str(b.identity) for b in self.bindings]}",
            )
        return hits[0]

    def script_pptr(self, want: ScriptIdentity | str) -> dict[str, int]:
        """`m_Script` 的**值**：`{'m_FileID': 0, 'm_PathID': <本包 MonoScript>}`（公式 ④）。"""
        return {"m_FileID": 0, "m_PathID": int(self.binding(want).package_path_id)}

    def binding_lines(self) -> tuple[str, ...]:
        return tuple(b.line() for b in self.bindings)


def plan_mono_scripts(
    catalogue: ScriptCatalogue, requests: Mapping[ScriptIdentity | str, int]
) -> MonoScriptPlan:
    """按 `{类: 本包 pathID}` 生成 MonoScript 对象 + 绑定表。

    每一步都不猜：类不在目录里 ⇒ 抛；pathID 与同批其它对象撞车 ⇒ 抛。
    """
    bindings: list[MonoScriptBinding] = []
    specs: list[builder.ObjectSpec] = []
    seen: dict[int, str] = {}
    for want, path_id in requests.items():
        record = catalogue.require(want)
        pid = int(path_id)
        if pid in seen:
            raise AssetError(
                f"pathID={pid} 被两个类点名：{seen[pid]!r} 与 {str(want)!r}",
                hint="包内 pathID 必须唯一",
            )
        seen[pid] = str(record.identity)
        fields = make_mono_script_fields(catalogue.template(record.path_id), record.identity)
        specs.append(mono_script_spec(fields, path_id=pid, label=f"mono-script:{record.identity}"))
        bindings.append(
            MonoScriptBinding(
                identity=record.identity,
                script_index=catalogue.script_index_of(record.identity),
                source_path_id=int(record.path_id),
                package_path_id=pid,
            )
        )
    return MonoScriptPlan(bindings=tuple(bindings), mono_specs=tuple(specs))


def bind_behaviour_script(fields: Mapping[str, Any], path_id: int) -> dict[str, Any]:
    """把 MonoBehaviour 字段树里的 `m_Script` 指到本包那颗 MonoScript（公式 ④）。

    ⛔ `m_Script` 不在字段里就抛 —— 那说明拿错了类型树（MonoBehaviour 必须按
    `script_index` 点名取树），静默加一个键会被 `:func:`clone.write_object` 按树丢掉。
    """
    if "m_Script" not in fields:
        raise AssetError(
            "MonoBehaviour 的字段树里没有 m_Script",
            hint="拿错脚本的树了？classID=114 有 22 棵树，必须按 script_index 点名",
        )
    out = dict(fields)
    out["m_Script"] = {"m_FileID": 0, "m_PathID": int(path_id)}
    return out


# --------------------------------------------------------------------------- #
# 校验：a2 那条尺子（绑定普查）+ 自造包的判据（含"类不存在"负控）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BindingCensus:
    """`m_Script == m_ScriptTypes[si].local_identifier` 的普查读数（能红的尺子）。"""

    total: int
    consistent: int
    mismatch: int
    unreadable: int
    ref_types: bool = False
    failures: tuple[str, ...] = ()
    #: ★ 2026-10-18：`m_Script` 指向**本包内某个 external**（`m_FileID >= 1`）的条数。
    #: 1.2.1（Steam 2026-10-17 11:30）起 units 实测 **4431/4431** 都变成这种形状
    #: （`m_FileID=1` ⇒ externals[0] = `archive:/CAB-0ec2…`），老判据要求 `m_FileID==0`
    #: ⇒ 一致性从 4431 掉到 0，一条「尺子」把**正常的包**全判成不一致（假红）。
    external: int = 0

    def line(self) -> str:
        tail = "（喂了 ref_types）" if self.ref_types else "（未喂 ref_types）"
        extra = f"，其中跨文件引用 = {self.external}" if self.external else ""
        return (
            f"MonoBehaviour {self.total} 个{tail}：一致 = {self.consistent}，"
            f"不一致 = {self.mismatch}，读不了 = {self.unreadable}{extra}"
        )

    @property
    def ok(self) -> bool:
        return self.mismatch == 0 and self.unreadable == 0


def census_script_binding(
    view: SerializedFileView, *, ref_types: Mapping[Any, Any] | None = None, limit: int = 6
) -> BindingCensus:
    """**a2 的那条尺子**：每个 MonoBehaviour 的 `m_Script` 是否等于
    `m_ScriptTypes[script_type_index].local_identifier`（且 `m_FileID == 0`）。

    ⚠ 两条读数是**同一个公式**、不同读器，别混为一谈（模块头"复算"一节）：
    不给 `ref_types` ⇒ units 实测 一致 4431 / 不一致 0 / 读不了 604（604 个 AnimationHub
    带 `[SerializeReference]`，按设计抛）；给 `clone.ref_type_pool(meta)` ⇒ 5035/0/0。
    """
    script_types = view.meta.script_types
    externals = len(getattr(view.meta, "externals", ()) or ())
    failures: list[str] = []
    total = consistent = mismatch = unreadable = external = 0
    for pid in view.path_ids_of_class(CLASS_MONO_BEHAVIOUR):
        total += 1
        type_id = int(view.entry(pid).type_id)
        si = int(view.meta.types[type_id].script_type_index)
        expected = int(script_types[si].local_identifier) if 0 <= si < len(script_types) else None
        try:
            fields = clone.read_object(view.raw(pid), view.nodes_for(type_id), ref_types=ref_types)
            script = fields.get("m_Script") or {}
            got = int(script.get("m_PathID", 0))
            file_id = int(script.get("m_FileID", 0))
            # ⚠ `m_FileID` 合法范围：`0`（本文件）或 `1..externals`（第 n 个 external，1 起数）。
            #   2026-10-18 实测：units 变成后者的有 4431 条 —— 老判据只认 0 就会把它们全算成不一致。
            file_ok = file_id == 0 or 1 <= file_id <= externals
            if expected == got and file_ok:
                consistent += 1
                if file_id:
                    external += 1
            else:
                mismatch += 1
                if len(failures) < limit:
                    failures.append(
                        f"pathID={pid} script_index={si} 期望 local_identifier={expected}"
                        f" 实测 m_Script=({file_id}, {got})"
                    )
        except (AssetError, ValueError, KeyError, struct.error) as exc:
            unreadable += 1
            if len(failures) < limit:
                failures.append(f"pathID={pid} 读不了：{type(exc).__name__}: {exc}")
    return BindingCensus(
        total=total,
        consistent=consistent,
        mismatch=mismatch,
        unreadable=unreadable,
        ref_types=ref_types is not None,
        failures=tuple(failures),
        external=external,
    )


@dataclass(frozen=True, slots=True)
class PackageCheck:
    """一个包的脚本绑定体检（`problems` 空 = 过）。"""

    source: str
    problems: tuple[str, ...]
    lines: tuple[str, ...]
    unreadable: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        head = "过" if self.ok else f"★ {len(self.problems)} 条问题"
        return f"{self.source}: {head}（读不了的 MonoBehaviour {self.unreadable} 个）"


def verify_package(
    view: SerializedFileView,
    *,
    known: ScriptLibrary | ScriptCatalogue | Collection[ScriptIdentity] | None = None,
) -> PackageCheck:
    """自造包的判据（**能红**）：三方绑定 + 悬空 + "类到底存不存在"。

    `known` = 游戏已有的类目录（`:func:`scan_library` / `:class:`ScriptCatalogue` /
    一串 `:class:`ScriptIdentity`）。给了它才会查"这颗 MonoScript 指的类游戏里有没有"；
    ⛔ 不给时 `lines` 里会明写"只查包内自洽" —— 不许让没查的那一半看起来像查过。

    四条判据（每条都有负控用例）：

    1. 每个 `classID=114` 的类型表项，`script_type_index` 必须落在 `m_ScriptTypes` 里；
    2. `m_ScriptTypes[si].file_index == 0` 且 `local_identifier` 指向**本包**的
       MonoScript(115) 对象（照抄真件 ⇒ 指到真件那个包 ⇒ 这条红）；
    3. 每个 MonoScript 对象的三串身份必须**在 known 里**（负控：编一个类名 ⇒ 红）；
    4. 每个 MonoBehaviour 的 `m_Script` 必须是 `(fileID=0, pathID=本包某颗 MonoScript)`。
    """
    problems: list[str] = []
    lines: list[str] = []
    mono_ids = set(view.path_ids_of_class(CLASS_MONO_SCRIPT))
    mono_identities: dict[int, ScriptIdentity] = {}
    for pid in sorted(mono_ids):
        try:
            mono_identities[pid] = read_mono_script(view, pid).identity
        except (AssetError, ValueError, KeyError, struct.error) as exc:
            problems.append(f"pathID={pid} 的 MonoScript 读不了：{type(exc).__name__}: {exc}")

    script_types = view.meta.script_types
    behaviour_type_ids = set(view.type_ids_of(CLASS_MONO_BEHAVIOUR))
    for i in view.type_ids_of(CLASS_MONO_BEHAVIOUR):
        si = int(view.meta.types[i].script_type_index)
        if not 0 <= si < len(script_types):
            problems.append(
                f"类型表第 {i} 项：script_type_index={si} 越出 m_ScriptTypes（{len(script_types)}）"
            )
            continue
        entry = script_types[si]
        if int(entry.file_index) != 0:
            problems.append(
                f"m_ScriptTypes[{si}].file_index={entry.file_index} != 0：指到别的文件去了"
            )
        if int(entry.local_identifier) not in mono_ids:
            problems.append(
                f"m_ScriptTypes[{si}].local_identifier={entry.local_identifier} "
                f"不是本包的 MonoScript —— 悬空（照抄真件的典型症状）"
            )
    lines.append(
        f"类型表 114 项 {len(behaviour_type_ids)} 个 / 本包 MonoScript {len(mono_ids)} 颗 / "
        f"m_ScriptTypes {len(script_types)} 行"
    )

    if known is not None:
        known_set = {i.key for i in _identities_of(known)}
        lines.append(f"已给游戏脚本目录：{len(known_set)} 条去重身份 —— 查'这个类存不存在'")
        for pid, identity in sorted(mono_identities.items()):
            if identity.key not in known_set:
                problems.append(
                    f"pathID={pid} 的 MonoScript 指向 {identity} —— 游戏里没有这个类"
                    f"（IL2CPP 加不了新类；照抄真件的三串改一个字符就是这个形状）"
                )
    else:
        lines.append("⚠ 没给游戏脚本目录（known=None）⇒ **只查包内自洽**，没查'这个类游戏里有没有'")

    unreadable = 0
    for pid in view.path_ids_of_class(CLASS_MONO_BEHAVIOUR):
        type_id = int(view.entry(pid).type_id)
        try:
            fields = clone.read_object(view.raw(pid), view.nodes_for(type_id))
        except (AssetError, ValueError, KeyError, struct.error):
            unreadable += 1
            continue
        script = fields.get("m_Script") or {}
        file_id = int(script.get("m_FileID", 0))
        target = int(script.get("m_PathID", 0))
        if file_id != 0:
            problems.append(f"pathID={pid} 的 m_Script 的 m_FileID={file_id} != 0（不在本包内）")
        elif target not in mono_ids:
            problems.append(
                f"pathID={pid} 的 m_Script 指向 pathID={target} —— 本包没有这颗 MonoScript"
            )
    lines.append(
        f"MonoBehaviour 字段级读完：读不了 {unreadable} 个"
        "（[SerializeReference] 之类，不算绑定问题）"
    )
    return PackageCheck(
        source=view.source, problems=tuple(problems), lines=tuple(lines), unreadable=unreadable
    )


def verify_bundle(
    path: str | Path,
    *,
    known: ScriptLibrary | ScriptCatalogue | Collection[ScriptIdentity] | None = None,
    node_path: str | None = None,
) -> PackageCheck:
    """打开一个已落盘的包（任何 UnityFS）并跑 `:func:`verify_package`。"""
    with open_view(path, node_path=node_path) as view:
        return verify_package(view, known=known)


def _identities_of(
    known: ScriptLibrary | ScriptCatalogue | Collection[ScriptIdentity],
) -> tuple[ScriptIdentity, ...]:
    if isinstance(known, ScriptLibrary):
        return known.identities()
    if isinstance(known, ScriptCatalogue):
        return tuple(r.identity for r in known.mono_scripts)
    return tuple(known)
