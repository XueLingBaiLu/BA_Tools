"""W10 · 把「从零造的 Mesh / Material / prefab」装配成一个**能当单位模型用**的 UnityFS 包。

这一层是**集成层**：material_build / prefab_build 各自只造自己那一类对象
（★ 2026-10-25：`mesh_build` 已随「手写 mesh 字段」那条栈退役 ——
网格改由提取器 + Unity 的 ModelImporter 造），
本模块负责把它们 ＋ 依赖（Shader / Texture2D）＋ AssetBundle 装进**同一个
SerializedFile**，
并按 catalog 的**资产路径**（internalId）给出**一条**容器条目（真件 prefab 地址就是这个形状）。

## 三条硬口径（都有实测依据，⛔ 不许在这一层改）

1. **容器只有一条**：路径 = catalog 的 internalId
   （Assets/Resources_moved/ModelPrefabs/…/<名>.prefab），
   指向**根 GameObject**。真件 RU_T72B_Base 实测：同路径 **1 条**、classID=1（GameObject）、
   preload 窗口 (28116, 197) —— 与 W9 的贴图地址（同路径两条 Texture2D+Sprite）**形状不同**。
2. **类型表列表下标 = type_id**，顺序定下就不许再改；同 classID **只能有一项树**
   （builder.serialize_serialized_file 按 classID 索引，
   见 prefab_build.serialize_prefab 的接缝说明）。
3. **依赖默认沿用源 pathID 搬**：调用方把 shader / 贴图的 pathID **保持真件原值**，
   于是材质的 m_Shader / m_TexEnvs 与 Shader 内部的 m_Dependencies 一条都不用重映射
   （独立尺子实测：track 链闭包外引用 **0 条**）。

## N4 的边界（如实写在模块头，别让它变成假绿）

builder.dangling_pptrs 只能普查**有字段树**的对象（我们新造的 Mesh/Material/贴图）。
Shader 与 prefab 对象走的是 **payload**（clone 解不了 Shader 的树），
⇒ 这两类的 PPtr 必须由 prefab_build.verify_hierarchy（本模块里会跑）与**独立尺子**（UnityPy）核。
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone, metadata_writer, prefab_build
from ba_mod_maker.asset_kernel.container_addr import resolve_container_path
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "PptrAudit",
    "UnitBundleResult",
    "UnitGateReport",
    "assemble_unit_bundle",
    "audit_package_pptrs",
    "verify_unit_bundle",
]

#: 造最小单位 prefab 一定要用的类（prefab_build 会在缺树时抛）
#: A10 要读的五个 `[SerializeReference] IAnimationBehaviour[]` 字段（顺序同真件声明）。
ANIM_HUB_STATE_FIELDS = ("_universalState", "_demoState", "_gameState",
                         "_preDeathState", "_deathState")

#: A7（材质槽 vs 子网格）要用的三个类号。
CLASS_MESH = 43
CLASS_MESH_RENDERER = 23
CLASS_SKINNED_MESH_RENDERER = 137
CLASS_LOD_GROUP = 205


def _shape_fingerprint(nodes: object) -> str:
    """类型树逐节点 (level,type,name) -> ``N 节点/sha xxxxxxxx``（只用于读数，⛔ 不判红）。"""
    rows = tuple((n.level, n.type, n.name) for n in nodes)  # type: ignore[attr-defined]
    return f"{len(rows)} 节点/sha {hashlib.sha1(repr(rows).encode()).hexdigest()[:8]}"


#: 内置类型表（= 当前游戏版本 units 包）里 classID=114 的**全部**类型树，惰性算一次。
#: 键和值都是逐节点 ``(level, 类型, 字段名)`` 元组（与 A6 的比法同一口径）。
_BUILTIN_114_TREES: dict[tuple, tuple] | None = None


def _builtin_114_trees() -> dict[tuple, tuple]:
    """真件那 23 棵 MonoBehaviour 类型树（读产品内置类型表，⛔ 不碰游戏目录）。

    为什么 A6 要用它当兜底基线：``unit_script_shapes.UNIT_SCRIPT_SHAPES`` 只登记了 7 个
    我们**自己造的桩**类；游戏自己还有一批类（例 ``ContainerSeatInitializer``）没登记，
    而它们的类型树在 units 包里**逐字节存在**。没登记就只报读数 ⇒ 桩把字段类型写错时
    静默通过（2026-10-26 M113A3 现场：包内 ``PPtr<$MonoBehaviour>`` / 真件 ``UInt8``）。
    """
    global _BUILTIN_114_TREES
    if _BUILTIN_114_TREES is None:
        table = builder.read_raw_type_table(
            builder.builtin_source_meta(), common_strings=clone.unitypy_common_strings()
        )
        trees: dict[tuple, tuple] = {}
        for entry in table.entries:
            if int(entry.class_id) != builder.CLASS_MONO_BEHAVIOUR:
                continue
            key = tuple((int(n.level), str(n.type), str(n.name)) for n in entry.nodes)
            trees[key] = key
        _BUILTIN_114_TREES = trees
    return _BUILTIN_114_TREES


def _closest_builtin_tree(got: tuple, pool: Sequence[tuple]) -> tuple | None:
    """在真件那批树里挑一棵「最像」的：先按 level-1 字段名序列，再按公共前缀长度。

    只用于**把红说清楚**（第几个节点、包内什么 ≠ 真件什么），不参与判定。
    """
    def top_names(tree: tuple) -> tuple:
        return tuple(node[2] for node in tree if int(node[0]) == 1)

    if not pool:
        return None
    same_fields = [tree for tree in pool if top_names(tree) == top_names(got)]
    cands = same_fields or list(pool)

    def common(tree: tuple) -> int:
        hit = 0
        for left, right in zip(got, tree, strict=False):
            if left != right:
                break
            hit += 1
        return hit

    return max(cands, key=common)

PREFAB_CLASS_KEYS: tuple[tuple[int, int | None], ...] = (
    (1, None),  # GameObject
    (4, None),  # Transform
    (33, None),  # MeshFilter
    (23, None),  # MeshRenderer
    (137, None),  # SkinnedMeshRenderer（用不到也得给，build_prefab 缺树会抛）
    (205, None),  # LODGroup
)


@dataclass(frozen=True, slots=True)
class UnitBundleResult:
    """一次单位模型包的装配结果（写盘由调用方决定 —— 本模块一个字节都不写）。"""

    plan: builder.ObjectBundlePlan
    bundle_name: str
    container_path: str
    root_game_object: int
    prefab: prefab_build.PrefabBuild
    report: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def assemble_unit_bundle(
    *,
    meta: bytes | bytearray,
    spec: prefab_build.PrefabNode,
    extra: Sequence[builder.ObjectSpec],
    container_path: str,
    bundle_name: str,
    seed: str = "w10unit",
    block_type: int = builder.BLOCK_LZ4HC,
    extra_class_ids: Sequence[int] = (43, 21, 28, 48),
    common_strings: Mapping[int, str] | None = None,
    preload_all_objects: bool = True,
    script_type_map: Mapping[int, int] | None = None,
    copy_source_tables: bool = False,
    path_ids: Sequence[int] | None = None,
    stream_payload: bytes | None = None,
) -> UnitBundleResult:
    """把 prefab + extra 里的对象装成一个 UnityFS 包，并**在函数内部自证**。

    参数
    ----
    meta
        真包的元数据区（类型树唯一来源）。
    spec
        prefab 形状（节点名 / mesh pathID / material pathID 由调用方定）。
    extra
        prefab 之外的对象（我们的 Mesh / Material / 搬进来的 Shader / 内联贴图）。
        我们的对象给 fields；**clone 解不了树的类**（Shader）给 payload。
    container_path
        catalog 的资产路径（internalId）—— 容器那一条的路径。
    preload_all_objects
        预载窗口是否覆盖**本资产的全部对象**（默认 True）。
        ★ 2026-10-06 实测口径：真件就是这么写的 —— RU_T72B_Base.prefab 的窗口是
        [28116, +197)，里面明确列着它用到的 5 个脚本
        （a1 实测：m_PreloadTable 的 197,538 条里有 3,986 条命中 MonoScript）。
        注：预载表**不含 AssetBundle 自己** ⇒ 覆盖数 = object_count − 1。
        旧口径只放**根对象**（窗口 (0,1)）⇒ 我们包里 4 个 MonoScript 全在窗口外（Lead 实测 v2 包）。
    script_type_map
        `script_type_index -> 本包 MonoScript 的 pathID`。**spec 里带 MonoBehaviour 时必须给**
        （2026-10-08 补，a3 的 A4 缺口）：不给的话 `m_ScriptTypes` 会照抄源包的 localIdentifier，
        而那个 pathID 在我们包里不存在 ⇒ Unity 解析脚本时取到空。`monoscript_build.MonoScriptPlan
        .script_type_map` 就是喂这里的值。
    copy_source_tables
        把源包的 `externals` / `ref_types` 一起抄过来（a3 的 A4）。**MonoBehaviour 的字段树里
        一旦有 `m_FileID != 0` 的引用就必须开**；开了之后 `dangling_pptrs` 的 `externals=`
        也要跟着给（本函数已自动处理）。默认 False = 与既有调用方行为一致。
    stream_payload
        .resS 的字节（块压缩贴图的纹素）。给了 ⇒ 造**双节点**容器（主包 ＋ 流式节点），
        贴图的 m_StreamData 指过去；不给 ⇒ 单节点，与既有调用方**逐字节相同**。
        空 bytes 会被拒绝（要么别给、要么给非空）。
    path_ids
        本包 pathID 显式指定（a3 的 A6，透传给 `prefab_build.build_prefab`）。省略 = 按 seed 推导。
    """
    if not extra:
        raise AssetError("extra 是空的：装配一个单位包至少要 Mesh + Material")
    if common_strings is None:
        # 与 builder.build_object_bundle / reopen_container 同一口径：类型树的字符串可能引用
        # **公共串表**（格式常量），不传就按 UnityPy 那份取；两者都没有时 clone 会抛。
        common_strings = clone.unitypy_common_strings()
    # 2026-10-06（a3 实测的最小改动之一）：MonoBehaviour 的类型表项**必须点名 script_index**，
    # 只给 classID 会让 entry_for(114) 抛（units 里 114 有 22 项）。所以这里从 spec 递归收集
    # 每个 Mono 的 (114, script_index)，再补上 extra 里各对象的 (classID, script_index)。
    wanted = list(PREFAB_CLASS_KEYS)
    for class_id in extra_class_ids:
        key = (int(class_id), None)
        if key not in wanted:
            wanted.append(key)

    def _collect(node: prefab_build.PrefabNode) -> None:
        for mono in node.mono_behaviours:
            key = (prefab_build.CLASS_MONO_BEHAVIOUR, int(mono.script_index))
            if key not in wanted:
                wanted.append(key)
        # 2026-10-08：`extra_components`（BoxCollider(65) / Animator(95)…）**也从 spec 自己推** ——
        # 只给 extra_class_ids 的话，spec 上挂一个 Collider 就会在 entry_for(65) 抛
        # 「类型表里没有这些类的树」（今天 routeb_zero 实跑踩到）。
        for extra in node.extra_components:
            key = (int(extra.class_id), extra.script_index)
            if key not in wanted:
                wanted.append(key)
        for child in node.children:
            _collect(child)

    _collect(spec)
    _table, entries, type_report = prefab_build.type_entries_for_classes(
        meta, wanted, common_strings=common_strings
    )
    extra_ids = [int(item.path_id) for item in extra]
    # ⚠ `m_Script` 指向的是 **extra 里那颗 MonoScript**（不在这棵 prefab 树里）⇒ 必须显式告诉
    #   build_prefab 哪些 pathID 是"包内已知的脚本"，否则它按"树内对象"判悬空、当场抛
    #   （2026-10-08 实跑：4 个 MonoBehaviour 全被报悬空 —— 与下面那把悬空尺子同一个口径）。
    script_ids = [int(item.path_id) for item in extra if int(item.class_id) == 115]
    pbuild = prefab_build.build_prefab(spec, entries, seed=seed, extra_package_ids=extra_ids,
                                       path_ids=path_ids, script_path_ids=script_ids)

    specs = list(extra)
    specs += [
        builder.ObjectSpec(
            class_id=int(item.class_id),
            fields=None,
            path_id=int(item.path_id),
            payload=item.payload,
            # ⚠ 2026-10-06：script_index 一定要往下传。
            # 否则 build_object_bundle 会按 (114, None) 推类型表，直接抛
            # 「classID=114 有 22 项 —— 必须点名 script_index」（a3 探针复现过）。
            script_index=(int(item.script_index) if int(item.class_id) == 114 else None),
            label=f"prefab:{item.type_name}",
        )
        for item in pbuild.objects
    ]
    # ⚠ m_Script 的 pathID **就是包内 MonoScript 对象的 pathID**（a1 实测 22/22 对应，
    # 见 _work/a1-script-binding.md）⇒ 这里传的是「把包内 MonoScript 登记成已知引用」，
    # ⛔ 不是「m_Script 天然不在对象表里」——后者是 W10 第 2 轮漏搬 MonoScript 的翻车形状。
    # 不传会把每个 MonoBehaviour 都误报成悬空。
    # ⚠ 开了 copy_source_tables 之后，包内会有 externals（源包抄来的）⇒ 尺子必须知道有几条，
    #   否则 `extra` 里带 `m_FileID != 0` 的对象会被**误报**成悬空（a3 的 A5）。
    externals = len(metadata_writer.parse_serialized_metadata(bytes(meta)).externals) \
        if copy_source_tables else 0
    dangling = builder.dangling_pptrs(
        specs,
        assetbundle_path_id=1,
        externals=externals,
        script_path_ids=script_ids,
    )
    # ★ 2026-10-06（a1 实测 + Lead 复核）：真件的**预载窗口 = 该资产的全部对象**
    #（RU_T72B_Base.prefab 是 [28116, +197)，窗口里明确列着它用到的 5 个脚本）。
    # 旧口径 preload_all=False 只把**根对象**放进窗口 ⇒ 我们包里的 MonoScript 全在窗口外
    #（实测 v2 包：m_PreloadTable 1 条、4 个 MonoScript 一个都不在窗口里）。
    # ⇒ 必须 preload_all=True，否则「窗口覆盖全部对象」这条判据不成立。
    plan = builder.build_object_bundle(
        objects=specs,
        bundle_name=bundle_name,
        source_meta=meta,
        container=[(str(container_path), pbuild.root_game_object)],
        common_strings=common_strings,
        block_type=block_type,
        preload_all=preload_all_objects,
        script_type_map=script_type_map,
        copy_source_tables=copy_source_tables,
        # ★ 2026-10-10 加：**透传**流式载荷（.resS 的字节）。
        #   原来这里没往下传 ⇒ 走流式贴图的包根本装不出来（G7B 的 v3 贴图就是块压缩＋流式，
        #   真件 2,725/2,725 张全走这条路）。给了就造双节点容器，不给则与以前逐字节相同。
        stream_payload=stream_payload,
    )

    problems: list[str] = list(dangling)
    report: list[str] = [
        f"对象 {plan.object_count} 个 / 容器 1 条 -> {container_path}"
        f" (pathID {pbuild.root_game_object})",
        f"包 {plan.bundle_size:,} B（SF {plan.serialized_file_size:,} B"
        f" / 块 {plan.unityfs.block_count}） crc32 {plan.crc32}",
    ]
    report.extend("prefab · " + line for line in pbuild.criterion_lines())
    for row in type_report:
        report.append("类型树 · " + row.line())
    report.append(
        "N4 悬空 0 条（有字段树的那部分；Shader/prefab 走 payload，由独立尺子核）"
        if not dangling
        else f"★ N4 悬空 {len(dangling)} 条（有字段树的那部分）"
    )
    if plan.container != ((str(container_path), int(pbuild.root_game_object)),):
        problems.append(f"容器条目形状不对：{plan.container}")
    return UnitBundleResult(
        plan=plan,
        bundle_name=bundle_name,
        container_path=str(container_path),
        root_game_object=int(pbuild.root_game_object),
        prefab=pbuild,
        report=tuple(report),
        problems=tuple(problems),
    )


# --------------------------------------------------------------------------- #
# 出厂闸：把「游戏加载时会抛 / 会静默失效」的那几条对一遍（A0~A3 + A5 + A6 + 名字契约）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class UnitGateReport:
    """一次出厂闸的结果。``problems`` 非空 = **不要装机**。"""

    bundle: str
    container: tuple[tuple[str, int], ...]
    root_game_object: int
    objects: int
    classes: Mapping[int, int]
    mono_scripts: tuple[tuple[int, str], ...]
    readings: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        return (*self.readings, *(f"★ {p}" for p in self.problems))


def verify_unit_bundle(
    path: str | Path,
    *,
    asset_path: str | None = None,
    common_strings: Mapping[int, str] | None = None,
    require_animations: bool = False,
    turret_prefab: bool = False,
) -> UnitGateReport:
    """**出厂闸**：一个已经落盘的包，能不能当单位模型用？

    六条硬条件（验收合同 §2·L0；不满足就是游戏里抛 / Debug.Break）：

    * **A0** 每个 MonoBehaviour(114) 的 ``m_Script`` 必须指到**本包**的一颗 MonoScript(115)
      —— 指到包外 = 游戏里 Missing Script（2026-10-08 实测：从零装配第一版漏了这步，
      4 个组件全成 Missing ⇒ ``UnitPrefabRoot`` 不存在）；
    * **A1** 根 GameObject 上必须有 ``UnitPrefabRoot``（按 **MonoScript 的 m_ClassName** 认，
      不按 script_index —— 索引随包变）；
    * **A2** 容器那条 → 根 GameObject，且该对象在包内、类型是 GameObject；
    * **A3** ``UnitPrefabRoot.Skins`` 内部的 PPtr（材质 / Renderers）**全在包内**。
      ⚠ **``Skins.Data`` 为空不算红**（2026-10-10 更正）：原版 units 包里 **288 个
      UnitPrefabRoot 有 37 个（12.8%）是空的**、游戏不崩 ⇒ **空 Data ≠ null 引用**
      （更准的解释 10-06 就写在 ``_work/a2-skin-mono-spec.md:66``）。
      但**必须显式报出读数** —— 引擎对"没有皮肤覆盖"这一档是静默的。

    **代码级依据（2026-10-08 读，不是只引文档）**：
    `_work/isil/IsilDump/BrokenArrow/BrokenArrow/Client/Ecs/Spawn/UnitBuilder_NestedType__CreateUnitModel_d__50.txt`
    的 IL 里 `Call UnitSkinStorage.Initialize`（第 459 行，前面三条 null 检查）与
    `Call ArgumentException..ctor`（第 675 行）—— 前者就是"根上必须有东西喂给它"，
    后者是"取不到模型就抛"的形状；`…/Spawn/UnitPrefabRoot.txt` 里该类只有
    `FillRenderers()` / `DisposeSpawnTimeObjects()` / `.ctor()` 三个方法（21 KB）。
    ⚠ 这份读法是**部分**的（只追到调用点与异常构造，没有把整个状态机走完）—— 如实记，别当"全读过"。

    * **A5** 每张流式贴图的纹素必须落在**本包**的 .resS 里（指向源包的 archive:/CAB-… ⇒
      装机/渲染时取不到 ⇒ **白图**，而引擎对丢纹素是静默的）；
    * **A6** 每个 MonoBehaviour 的**脚本字段形状**（类型树逐节点 level/类型/字段名）必须与
      ``unit_script_shapes.UNIT_SCRIPT_SHAPES`` 里登记的**真件形状逐字相同**。
      ⚠ 这条补的是 A0~A5 的盲区：它们只查 PPtr「**在不在包里**」，
      不查「**指向的对象是什么类型**」——
      2026-10-13 现场：我们的桩把 ``PrefabRoot``/``PrefabBody`` 写成 ``GameObject``（真件是
      ``Transform``），A0~A5 **全绿**也照样穿透。**只有基线里登记了的类才判红**；没登记的点名报读数
      （第三方组件 HDRP/UI/TMP 的字段跟着包里的版本走，登记进来会假红 —— DecalProjector 在
      HDRP 14→17 就多了一个 ``m_Version``）。

    名字契约（``unit_shape``）只写进 readings —— 那是"会静默失效"那一档，且**真件自己也可能不匹配**
    （实测 T-72B 的履带节点叫 ``RU_TanktrakL``，不含引擎要的 ``trackl``）⇒ 不当 hard problem。
    """
    from ba_mod_maker.asset_kernel import (  # noqa: PLC0415
        monoscript_build,
        unit_script_shapes,
        unit_shape,
    )
    from ba_mod_maker.core.asset import ufs  # noqa: PLC0415

    src = Path(path)
    blob_common = common_strings or clone.unitypy_common_strings()
    problems: list[str] = []
    readings: list[str] = []
    container: list[tuple[str, int]] = []
    root_pid = 0

    with ufs.UfsReader(src) as r:
        node0 = r.nodes[0]
        info = ufs.read_serialized_file(r, node0)
        meta = metadata_writer.parse_serialized_metadata(
            r.read_range(node0.offset, info.data_offset))
        types = list(meta.types)
        cid = {i: t.class_id for i, t in enumerate(types)}
        pool = clone.ref_type_pool(meta, common_strings=blob_common)
        cache: dict[int, Any] = {}

        def nodes_for(tid: int):
            if tid not in cache:
                tt = types[tid].type_tree
                cache[tid] = clone.read_type_nodes(
                    struct.pack("<ii", tt.node_count, tt.string_buffer_size)
                    + tt.nodes + tt.strings, common_strings=blob_common)
            return cache[tid]

        objs = {int(o[1]): o for o in info.objects}

        def read(pid: int) -> dict:
            o = objs[pid]
            return clone.read_object(
                bytes(r.read_range(node0.offset + info.data_offset + o[2], o[3])),
                nodes_for(o[4]), ref_types=pool)

        classes: dict[int, int] = {}
        for o in info.objects:
            c = int(cid.get(o[4], -1))
            classes[c] = classes.get(c, 0) + 1

        #: ★ 2026-10-23：`m_PathFlags` —— Unity 靠它做容器地址的**大小写归一**。
        #: Unity 6000 建的包写 7 且容器存小写；写包器若硬写 0，游戏按 catalog 的
        #: Pascal 路径 `LoadAsset()` 就取不到（症状：`Unable to load asset … from location`）。
        path_flags = 0
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_ASSETBUNDLE:
                continue
            _ab = read(int(o[1]))
            path_flags = int(_ab.get("m_PathFlags") or 0)
            for pair in (_ab.get("m_Container") or []):
                p = str((pair or {}).get("first") or "")
                pid = int(((pair or {}).get("second") or {}).get("asset", {}).get("m_PathID", 0))
                if p:
                    container.append((p, pid))
        if not container:
            problems.append("容器是空的：这个包在 catalog 里取不到任何资产（A2）")
        elif asset_path is not None and resolve_container_path(
            (p for p, _ in container), asset_path
        ) is None:
            problems.append(
                f"容器里没有地址 {asset_path!r}（A2）：实际 {[p for p, _ in container]}")
        elif (
            asset_path is not None
            and container
            and not any(p == asset_path for p, _ in container)
            and path_flags == 0
        ):
            problems.append(
                f"容器地址与 {asset_path!r} 只差大小写，但 `m_PathFlags=0`（A2b）："
                f"Unity 靠这个标志做大小写归一，写 0 会让游戏 `LoadAsset()` 取不到"
                f"（实测症状：Unable to load asset … from location）。"
                f"实际 {[p for p, _ in container]}"
            )

        mono_class: dict[int, str] = {}
        #: ★ 2026-10-26：MonoScript 的**命名空间**也要读 —— A6 用它区分「游戏自己的类」
        #: （BrokenArrow.*）与第三方桩（HDRP / FMOD / TMP）。前者必须用真件那批类型树。
        mono_ns: dict[int, str] = {}
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_MONO_SCRIPT:
                continue
            _ms = read(int(o[1]))
            mono_class[int(o[1])] = str(_ms.get("m_ClassName") or "")
            mono_ns[int(o[1])] = str(_ms.get("m_Namespace") or _ms.get("m_NameSpace") or "")
        mono_rows = tuple(sorted(mono_class.items()))
        if not mono_rows:
            problems.append(
                "包里一颗 MonoScript(115) 都没有（A0）：任何 MonoBehaviour 都会变成 Missing Script")

        bad: list[tuple[int, int]] = []
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_MONO_BEHAVIOUR:
                continue
            s = read(int(o[1])).get("m_Script") or {}
            if int(s.get("m_FileID", 0)) != 0 or int(s.get("m_PathID", 0)) not in mono_class:
                bad.append((int(o[1]), int(s.get("m_PathID", 0))))
        if bad:
            problems.append(f"MonoBehaviour 的 m_Script 指向包外：{bad[:4]}（A0）")
        readings.append(
            f"A0 包内 MonoScript {len(mono_class)} 颗；m_Script 指向包外 {len(bad)} 条")

        root_pid = container[0][1] if container else 0
        root = read(root_pid) if root_pid in objs else {}
        if root_pid not in objs or cid.get(objs[root_pid][4]) != prefab_build.CLASS_GAME_OBJECT:
            problems.append(f"容器指向的 pathID {root_pid} 不在对象表里 / 不是 GameObject（A2）")
        comps = [int(((c or {}).get("component") or {}).get("m_PathID", 0))
                 for c in (root.get("m_Component") or [])]
        # ⚠ 认类名要**两步**：114 的 pathID -> 它的 m_Script -> MonoScript 的 m_ClassName。
        #   直接拿 115 的 pathID 去比会永远匹配不上（2026-10-08 第一版就这么假红过）。
        behaviour_class: dict[int, str] = {}
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_MONO_BEHAVIOUR:
                continue
            s = read(int(o[1])).get("m_Script") or {}
            behaviour_class[int(o[1])] = mono_class.get(int(s.get("m_PathID", 0)), "")

        # ---- A10 [SerializeReference] 解析（2026-10-25 炮塔那次）----
        # AnimationHub 的五个状态数组是 `[SerializeReference] IAnimationBehaviour[]`。
        # **工程里缺某个行为类的桩**时，Unity 只能把它写成 `rid=-2`（type{class:'',ns:'',asm:''}）
        # ⇒ 运行期是 **null 成员** ⇒ `AnimationHub.Init` 的
        # `AllStates().ForEach(x => x.OnInit(this))` 对 null 调用 ⇒ NRE ⇒ `CreateUnitModel` 中断
        # ⇒ 真机「Couldn't load unit」（军械库里该单位没模型没卡片）。
        # 实测：坏炮塔包 5 个槽指 -2 / 修好后 0 个；单位包 0 个。
        hub_count = 0
        bad_slots: list[str] = []
        for pid, cls in behaviour_class.items():
            if cls != "AnimationHub":
                continue
            hub_count += 1
            try:
                hub_fields = read(pid)
            except Exception:  # noqa: BLE001 -- 解不开的对象不在这里判，A0/①已经报过
                continue
            for field_name in ANIM_HUB_STATE_FIELDS:
                arr = hub_fields.get(field_name) or []
                hit = [i for i, ent in enumerate(arr)
                       if isinstance(ent, dict) and int(ent.get("rid", 0)) == -2]
                if hit:
                    bad_slots.append(f"{field_name}{hit}")
        if bad_slots:
            problems.append(
                f"AnimationHub 的 [SerializeReference] 数组里有 {len(bad_slots)} 处指向 rid=-2"
                "（= 工程里缺行为类的桩）⇒ 运行期 null ⇒ AnimationHub.Init 会 NRE"
                f"⇒ 整台单位加载失败（A10）：{bad_slots}")
        if hub_count:
            readings.append(
                f"A10 AnimationHub {hub_count} 个：[SerializeReference] 未解析 {len(bad_slots)} 处")
        uroot_pid = next((p for p in comps if behaviour_class.get(p) == "UnitPrefabRoot"), 0)
        if not uroot_pid and not turret_prefab:
            problems.append(
                "根 GameObject 上没有 UnitPrefabRoot（A1）——"
                " 引擎 CreateUnitModel 会抛 ArgumentException")
        elif uroot_pid:
            readings.append(f"A1 根上组件 {len(comps)} 个；UnitPrefabRoot pathID={uroot_pid}")
        if turret_prefab:
            # ★ 2026-10-23 读出来的：IntegrateBaseTurret 找不到炮塔脚本就
            #   Debug.LogError("No turret script on dynamic turret is found, turret model name: …
            #   Turret index: …") 然后放弃挂载（UnitBuilder.txt:27115-27169）。
            #   认类名走同一条两步链（114 -> m_Script -> 115.m_ClassName）。
            turret_nodes = [p for p, cls in behaviour_class.items()
                            if cls == "UnitPrefabTurretInfo"]
            if turret_nodes:
                readings.append(
                    f"A1·炮塔 脚本 UnitPrefabTurretInfo {len(turret_nodes)} 颗"
                    f"（根上组件 {len(comps)} 个）")
            else:
                problems.append(
                    "包里没有 UnitPrefabTurretInfo（A1·炮塔）——"
                    " 引擎 IntegrateBaseTurret 会 LogError"
                    " 'No turret script on dynamic turret is found, turret model name: …"
                    " Turret index: …' 并放弃挂载")
        if require_animations:
            # ★ 2026-10-21：用户现场「车进来了、没有动画」——根上只有 UnitPrefabRoot
            #   时 A0~A5 全绿，但 UnitBuilder.InitAnimations 直接 return，整条动画/
            #   事件链缺失（验收合同 B 级）。这一条只在 `--unit-prefab` 下判红。
            root_classes = {behaviour_class.get(p, "") for p in comps}
            if ("AnimationManager" not in root_classes
                    and "AnimationHub" not in root_classes):
                problems.append(
                    "根上没有 AnimationManager / AnimationHub（A1）——"
                    " UnitBuilder.InitAnimations 会直接 return，整条动画/事件链缺失")

        if uroot_pid:
            f = read(uroot_pid)
            entries = ((f.get("Skins") or {}).get("Data")) or []
            refs: list[int] = []
            for e in entries:
                d = (e or {}).get("Data") or {}
                refs += [int((k or {}).get("m_PathID", 0)) for k in (d.get("_keyData") or [])]
                for v in (d.get("_valueData") or []):
                    refs += [int((x or {}).get("m_PathID", 0))
                             for x in ((v or {}).get("Renderers") or [])]
            refs = [x for x in refs if x]
            outside = [x for x in refs if x not in objs]
            if not entries:
                # 2026-10-10 更正（口径见 verify_unit_bundle docstring 的 A3 一条）：
                # 空 Data **不是** null 引用 —— 原版 37/288 个 UnitPrefabRoot 就是空的、游戏不崩。
                # 降为读数（★ = 看得见但不拦），因为引擎对「没有皮肤覆盖」这一档是静默的。
                readings.append(
                    "★ A3 Skins.Data 为空 —— 皮肤覆盖不会发生（外观由 prefab 上的材质决定）；"
                    "实测原版 288 个 UnitPrefabRoot 里 37 个（12.8%）如此，游戏不崩")
            if outside:
                problems.append(f"Skins 里有 {len(outside)} 条 PPtr 指向包外（A3）：{outside[:4]}")
            readings.append(
                f"A3 Skins.Data {len(entries)} 条 / PPtr {len(refs)} 条 / 包外 {len(outside)} 条")
            for field in ("PrefabRoot", "PrefabBody"):
                v = f.get(field) or {}
                pid = int(v.get("m_PathID", 0))
                readings.append(
                    f"   {field} -> pathID {pid}（{'在包内' if pid in objs else '**不在包里**'}）")
            rends = [int((x or {}).get("m_PathID", 0)) for x in (f.get("Renderers") or [])]
            miss = [x for x in rends if x and x not in objs]
            readings.append(f"   Renderers {len(rends)} 条 / 包外 {len(miss)} 条")
            if miss:
                problems.append(f"UnitPrefabRoot.Renderers 有 {len(miss)} 条指向包外：{miss[:4]}")

        # ---- A7 材质槽 vs 子网格（2026-10-23 读出来的）----
        # `UnitSkinStorage.SetSkin` 最后一步是 `Renderer.SetMaterials(renderer, materials)`
        # （ISIL 101 / dump 行 1566）—— 它是**整组替换**：皮肤给的数组比子网格短，
        # 多出来的子网格就没有材质 ⇒ 游戏里**整块不画**（10-22「负重轮消失」同源）。
        mesh_subs: dict[int, int] = {}
        mesh_names: dict[int, str] = {}
        for o in info.objects:
            if cid.get(o[4]) != CLASS_MESH:
                continue
            mf = read(int(o[1]))
            mesh_subs[int(o[1])] = len(mf.get("m_SubMeshes") or [])
            mesh_names[int(o[1])] = str(mf.get("m_Name") or "")
        short: list[tuple[int, str, int, int]] = []
        renderer_count = 0
        for o in info.objects:
            if cid.get(o[4]) not in (CLASS_MESH_RENDERER, CLASS_SKINNED_MESH_RENDERER):
                continue
            renderer_count += 1
            rf = read(int(o[1]))
            mid = int((rf.get("m_Mesh") or {}).get("m_PathID", 0))
            subs = mesh_subs.get(mid, 0)
            slots = len(rf.get("m_Materials") or [])
            if subs and slots < subs:
                short.append((int(o[1]), str(rf.get("m_Name") or ""), slots, subs))
        # ---- A9（读数，不进硬闸）LOD 同族子网格数一致性 ----
        # 「同族」= 网格名去掉前导 `NN_` 之后相同（真件：Chassis / 02_Chassis / 03_Chassis）。
        # 换网格时若只改了一档（`pack set-mesh --no-lod`），同族就会 1/2 混着 ——
        # 远处切 LOD 时那块几何会「变回旧形状」，而 A7 只看单个渲染器，抓不到。
        # ⚠ **只记读数**（进硬闸前先在真件上量过）：2026-10-23 在 units 包全量量了
        #   **1,355 件 / 4,611 族 → 不一致 1 件**：`Aircraft/RU_TU142.prefab` 的 `TU142` 族是
        #   **[1, 2]**（真件自己就这样）⇒ 进硬闸会假红这 1 件，所以维持读数。
        #   **为什么合法（2026-10-24 读清楚了，别再想去进闸）**：TU142 的 `TU142` 族
        #   在 LOD0 是 **2 子网格**（`[264, 10164]` 索引）＋ 2 个材质槽
        #   `['RU_TU95142 0glass', 'RU_TU95142 1']`；LOD1/2 是 **1 子网格** ＋ 1 槽
        #   `['RU_TU95142 1']` —— **玻璃（座舱盖）只在 LOD0 单独成一块**，远景那两档
        #   根本没有玻璃几何。⇒ 「同族各档」本来就是**不同渲染器 / 不同网格 / 不同材质分组**，
        #   子网格数是**逐档设计的**，不是「漏改一档」的指纹。所以 A9 永远只是读数。
        families: dict[str, set[int]] = {}
        for o in info.objects:
            if cid.get(o[4]) not in (CLASS_MESH_RENDERER, CLASS_SKINNED_MESH_RENDERER):
                continue
            rf = read(int(o[1]))
            mid = int((rf.get("m_Mesh") or {}).get("m_PathID", 0))
            name = mesh_names.get(mid)
            subs = mesh_subs.get(mid)
            if not name or subs is None:
                continue
            families.setdefault(re.sub(r"^\d+_", "", name), set()).add(subs)
        mixed = {k: sorted(v) for k, v in families.items() if len(v) > 1}
        if mixed:
            readings.append(
                "★ A9 LOD 同族子网格不一致（读数，**不进硬闸**）："
                + "；".join(f"{k} {v}" for k, v in list(mixed.items())[:4]))
        else:
            readings.append(
                f"A9 LOD 同族子网格一致：{len(families)} 个族全部同数")

        if short:
            problems.append(
                "材质槽少于子网格（A7）："
                + "；".join(f"{n or pid} 槽{s}<子网格{t}" for pid, n, s, t in short[:4])
                + " —— 多出来的子网格没有材质 ⇒ 游戏里整块不画"
                "（`Renderer.SetMaterials` 是整组替换，依据 UnitSkinStorage.SetSkin ISIL 101）")
        else:
            readings.append(
                f"A7 材质槽 vs 子网格：{renderer_count} 个渲染器全部 槽 ≥ 子网格")

            # ---- A8 LOD 档的渲染器表（★ 2026-10-23）----
        # LODGroup 每一档都有一张 renderers 表；表里出现 0/包外的 PPtr 时，那一档
        # 对应的零件**整块不画**（引擎按表逐个取，取不到就跳过；不抛、不警告）。
        # 只判表的结构，不判「每档必须有几个渲染器」—— 实测真件 4 档是 7/7/1/1，
        # 档与档数量本来就不同。
        lod_levels = 0
        lod_rows: list[str] = []
        lod_bad: list[str] = []
        for o in info.objects:
            if cid.get(o[4]) != CLASS_LOD_GROUP:
                continue
            lf = read(int(o[1]))
            lods = lf.get("m_LODs") or []
            counts = []
            for level_i, level in enumerate(lods):
                rends = (level or {}).get("renderers") or []
                counts.append(len(rends))
                for j, rp in enumerate(rends):
                    # 实测真件每格是 {'renderer': {m_FileID, m_PathID}}（键名是单数），
                    # 不是直接把 PPtr 摊在格子上 —— 2026-10-23 第一版按 rp.m_PathID 读，
                    # 在**绿包**上报了 4 条假红。
                    pptr = (rp or {}).get("renderer") if isinstance(rp, dict) else None
                    if not isinstance(pptr, dict):
                        pptr = rp if isinstance(rp, dict) else {}
                    pid = int(pptr.get("m_PathID", 0) or 0)
                    if pid == 0:
                        lod_bad.append(f"档{level_i}.renderers[{j}] 是空 PPtr")
                    elif pid not in objs:
                        lod_bad.append(f"档{level_i}.renderers[{j}] -> pathID {pid} 在包外")
            lod_levels += len(lods)
            lod_rows.append("/".join(str(c) for c in counts))
        if lod_bad:
            problems.append(
                "LODGroup 的渲染器表里有空/包外条目（A8）：" + "；".join(lod_bad[:4])
                + " —— 那一档对应的零件整块不画（引擎取不到就跳过，不抛不警告）")
        else:
            readings.append(
                f"A8 LOD 档：{len(lod_rows)} 组 / {lod_levels} 档 · "
                f"每档渲染器 [{', '.join(lod_rows)}] · 空/包外 0")

        # A5：**贴图的纹素必须落在本包里**（内联，或本包 .resS 覆盖到那段区间）。
        #   2026-10-08 现场：#711 的可研副本是从真件包"搬对象"来的，纹素留在源包的 .resS 里
        #   （m_StreamData.path = 'archive:/CAB-<源包的 hash>'）⇒ 装机/渲染时取不到 ⇒ **白图**。
        #   ⛔ 这条尺子是"渲染成白板"那类缺陷的机械判据（引擎对丢纹素是**静默**的）。
        node_names = " ".join(str(getattr(n, "path", "") or "") for n in r.nodes)
        tex_total = tex_inline = tex_stream = tex_foreign = 0
        foreign_names: list[str] = []
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_TEXTURE2D:
                continue
            tex_total += 1
            tf = read(int(o[1]))
            sd = tf.get("m_StreamData") or {}
            size = int(sd.get("size", 0) or 0)
            img = tf.get("image data")
            inline_len = len(img) if isinstance(img, (bytes, bytearray)) else 0
            if size <= 0:
                tex_inline += 1 if inline_len else 0
                continue
            tex_stream += 1
            path = str(sd.get("path", "") or "")
            cab = path.rsplit("/", 1)[-1] if path else ""
            if not cab or cab not in node_names:
                tex_foreign += 1
                foreign_names.append(str(tf.get("m_Name") or "?"))
        readings.append(
            f"A5 贴图 {tex_total} 张：内联 {tex_inline} / 流式 {tex_stream}"
            f" / **指向本包外 {tex_foreign}**")
        if tex_foreign:
            problems.append(
                f"{tex_foreign} 张流式贴图的纹素不在本包里（A5）：{foreign_names[:4]}"
                " —— 引擎对丢纹素是静默的，装机后就是白图")

        # A6：**脚本字段形状**必须与真件基线逐字相同。
        #   为什么要它：PPtr 的**目标类型**写在类型树里，而 A0~A5 只查 PPtr「在不在包里」——
        #   桩把 PrefabRoot 写成 GameObject（真件是 Transform）时，A0~A5 全绿也照样穿透
        #   （2026-10-13 现场；判据 = PPtr 目标 classID，真件 4(Transform) / 桩 1(GameObject)）。
        #   基线 = unit_script_shapes.UNIT_SCRIPT_SHAPES（生成物，见 tools/gen_script_shapes.py）。
        #   ⚠ 只有**基线里登记了的类**才判红；没登记的点名报读数 —— 第三方组件（HDRP/UI/TMP）
        #   的字段跟着**包里那个版本**走，登记进来会假红（DecalProjector 在 HDRP 14→17
        #   就多了一个 m_Version）。
        by_name = {k.rsplit("|", 1)[-1]: k for k in unit_script_shapes.UNIT_SCRIPT_SHAPES}
        shape_ok = 0
        shape_bad: list[str] = []
        shape_unregistered: list[str] = []
        shape_found: set[str] = set()   # 这个包里**实际挂上**的基线类名
        shape_seen: set[int] = set()
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_MONO_BEHAVIOUR:
                continue
            tid = int(o[4])
            if tid in shape_seen:          # 同一个类型条目只比一次
                continue
            shape_seen.add(tid)
            s = read(int(o[1])).get("m_Script") or {}
            if int(s.get("m_FileID", 0)) != 0:
                continue                   # 跨包脚本（1.2.1 游戏自己的形态）：本包抽不到形状
            cname = mono_class.get(int(s.get("m_PathID", 0)), "")
            if not cname:
                continue
            key = by_name.get(cname)
            ns = mono_ns.get(int(s.get("m_PathID", 0)), "")
            if key is None:
                # ★ 2026-10-26（类型表漂移那条）：**游戏自己的类**（namespace 以 BrokenArrow
                #   起头）即使没进 UNIT_SCRIPT_SHAPES，也必须用真件那批 114 树之一。
                #   为什么：运行期 Unity 按**编译好的类**读 MonoBehaviour，而类型树里写着每个
                #   字段的**类型**（PPtr / UInt8 / …）。桩把字段类型写错 ⇒ Unity 按错的类型
                #   打包 ⇒ 真件那 1 字节的枚举被写成 12 字节的 PPtr，值静默丢成 0。
                #   实测（M113A3 · ContainerSeatInitializer）：包内 15 节点、UnitSeatAnimation
                #   是 PPtr<$MonoBehaviour>；真件 13 节点、是 UInt8（枚举 SeatAnimation 的
                #   value__ 是 byte —— dump.cs:75892）⇒ 真件里那个 Sit(2) 读出来是 0(None)。
                #   ⛔ 不是放宽判据：这一支只会**多红**，并且点名到类、到第几个节点。
                #   ⚠ 第三方桩（HDRP / FMOD / TMP）**照旧只报指纹** —— 它们的字段跟着包里那个
                #   库版本走，判红必假（DecalProjector 在 HDRP 14→17 多一个 m_Version）。
                if ns.startswith("BrokenArrow"):
                    got_u = tuple((n.level, n.type, n.name) for n in nodes_for(tid))
                    trees = _builtin_114_trees()
                    if got_u not in trees:
                        near = _closest_builtin_tree(got_u, tuple(trees.values()))
                        at = next(
                            (i for i in range(max(len(got_u), len(near or ())))
                             if (got_u[i] if i < len(got_u) else None)
                             != (near[i] if near and i < len(near) else None)),
                            None,
                        )
                        shape_bad.append(
                            f"{cname}（{ns}）：包内 {len(got_u)} 节点 / "
                            f"真件 {0 if near is None else len(near)} 节点"
                            + ("" if at is None else (
                                f"；第一处不同在第 {at} 个节点：包内 "
                                f"{got_u[at] if at < len(got_u) else '(无)'}"
                                f" ≠ 真件 "
                                f"{near[at] if near and at < len(near) else '(无)'}"))
                            + " —— 字段类型写错 ⇒ 游戏按自己的类读，这个字段的值会静默丢"
                        )
                        continue
                # 未登记（第三方 / 选挂桥接类）：**不判红**，但报**形状指纹** ——
                # 同一台机器上「这个类的字段形状变了」（例 HDRP 升级多一个 m_Version）
                # 从此看得见：指纹变了就是变了，不用再靠肉眼比。
                shape_unregistered.append(f"{cname}（{_shape_fingerprint(nodes_for(tid))}）")
                continue
            shape_found.add(cname)
            got = tuple((n.level, n.type, n.name) for n in nodes_for(tid))
            want = unit_script_shapes.UNIT_SCRIPT_SHAPES[key]
            if got == want:
                shape_ok += 1
                continue
            at = next((i for i in range(max(len(got), len(want)))
                       if (got[i] if i < len(got) else None)
                       != (want[i] if i < len(want) else None)), None)
            shape_bad.append(
                f"{cname}（包内 {len(got)} 节点 / 真件 {len(want)} 节点）"
                + ("" if at is None else
                   f"；第一处不同在第 {at} 个节点：包内 {got[at] if at < len(got) else '(无)'}"
                   f" ≠ 真件 {want[at] if at < len(want) else '(无)'}"))
        readings.append(
            f"A6 脚本字段形状：登记 {len(by_name)} 个类 · 逐字相同 {shape_ok} · "
            f"**与真件不同 {len(shape_bad)}** · 未登记 {len(shape_unregistered)}"
            + (f" {shape_unregistered[:4]}" if shape_unregistered else ""))
        # ⚠ **对称面**（队友 census-comp 报的缺口）：基线登记了、**这个包里却没挂**的那些类 ——
        #   改前是**静默**的（循环只遍历包里的 MonoBehaviour）。⛔ 这里**不判红**：
        #   基线里的 AnimationHub / AnimationManager / EffectsSpawnPoint 都是**选挂**
        #   （调用点都有 null 护栏 —— 见 _work/lead_material/census/report.md §4），判红会误红。
        #   ⇒ 照 A3「空 Data」的先例：**报读数**，把它从静默变成看得见。
        shape_absent = sorted(set(by_name) - shape_found)
        if shape_absent:
            readings.append(
                f"   A6 未出现（基线登记了、这个包里没挂）：{shape_absent}")
        for item in shape_bad:
            problems.append(
                f"脚本字段形状与真件不同（A6）：{item} —— PPtr 的目标类型就写在这里；"
                "形状不对 = 游戏里那一格拿不到东西（A0~A5 看不见它）")

        names = [str(read(pid).get("m_Name") or "")
                 for pid, o in objs.items() if cid.get(o[4]) == prefab_build.CLASS_GAME_OBJECT]
        if names:
            shape = unit_shape.read_unit_shape(names)
            readings.append("名字契约：" + " · ".join(shape.summary()))
        _ = monoscript_build  # 显式引用（口径：身份按 m_ClassName 认，不按 script_index）
    return UnitGateReport(
        bundle=str(src), container=tuple(container), root_game_object=int(root_pid),
        objects=len(info.objects), classes=dict(sorted(classes.items())),
        mono_scripts=mono_rows, readings=tuple(readings), problems=tuple(problems),
    )


# --------------------------------------------------------------------------- #
# 整包 PPtr 普查：把「payload 对象没人核」这个已知的洞补上
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PptrAudit:
    """**整包**逐对象 PPtr 普查的读数（含 payload 对象 —— 它们以前是尺子的盲区）。"""

    bundle: str
    objects: int
    decoded: int
    undecodable: tuple[tuple[int, int], ...]
    pptr_total: int
    in_package: int
    null: int
    external: tuple[tuple[int, int], ...]
    dangling: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.dangling

    def lines(self) -> tuple[str, ...]:
        out = [
            f"对象 {self.objects} 个：能解码 {self.decoded}"
            + (f"；解不了 {list(self.undecodable)}" if self.undecodable else "（全部可解码）"),
            f"PPtr {self.pptr_total} 条：包内 {self.in_package} / 空 {self.null}"
            f" / 跨文件 {sum(n for _f, n in self.external)}"
            f" / **悬空 {len(self.dangling)}**",
        ]
        if self.external:
            out.append(f"  跨文件 fileID 分布：{list(self.external)}")
        out.extend(f"★ {line}" for line in self.dangling[:10])
        return tuple(out)


def audit_package_pptrs(
    path: str | Path,
    *,
    common_strings: Mapping[int, str] | None = None,
) -> PptrAudit:
    """**整包**普查：每一个对象（含 Mesh / Material / Shader / 贴图 这些走 payload 的）
    的 PPtr 是不是都落在包里。

    为什么要有它：`builder.dangling_pptrs` 只普查**有字段树**的那些对象（我们新造的），
    `prefab_build.verify_hierarchy` 只普查 prefab 那一棵 —— **payload 对象（Shader 等）
    一直是盲区**，
    模块头里也如实写着"由独立尺子核"。这就是那把独立尺子，而且是**产品件**。

    判据：`fileID == 0` 且 `pathID` 既不等于 0 也不在对象表里 = **悬空**（游戏那边取到空指针）。
    解不了树的对象**单独列出来**（不静默当成"没有 PPtr"）—— 那是这把尺子诚实性的边界。
    """
    from ba_mod_maker.core.asset import ufs  # noqa: PLC0415

    src = Path(path)
    common = common_strings or clone.unitypy_common_strings()
    dangling: list[str] = []
    undecodable: dict[int, int] = {}
    external: dict[int, int] = {}
    total = in_pkg = nulls = 0
    decoded = 0

    with ufs.UfsReader(src) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
        meta = metadata_writer.parse_serialized_metadata(
            reader.read_range(node0.offset, info.data_offset))
        types = list(meta.types)
        cid = {i: t.class_id for i, t in enumerate(types)}
        pool = clone.ref_type_pool(meta, common_strings=common)
        cache: dict[int, Any] = {}

        def nodes_of(tid: int):
            if tid not in cache:
                tt = types[tid].type_tree
                cache[tid] = clone.read_type_nodes(
                    struct.pack("<ii", tt.node_count, tt.string_buffer_size)
                    + tt.nodes + tt.strings, common_strings=common)
            return cache[tid]

        ids = {int(o[1]) for o in info.objects}
        for o in info.objects:
            pid = int(o[1])
            payload = bytes(reader.read_range(node0.offset + info.data_offset + o[2], o[3]))
            try:
                obj = clone.read_object(payload, nodes_of(o[4]), ref_types=pool)
            except Exception:  # noqa: BLE001
                cls = int(cid.get(o[4], -1))
                undecodable[cls] = undecodable.get(cls, 0) + 1
                continue
            decoded += 1
            try:
                refs = list(clone.collect_pptrs(obj, nodes_of(o[4]),
                                                owner=f"class{cid.get(o[4], -1)}:{pid}"))
            except Exception:  # noqa: BLE001
                cls = int(cid.get(o[4], -1))
                undecodable[cls] = undecodable.get(cls, 0) + 1
                decoded -= 1
                continue
            for ref in refs:
                total += 1
                if int(ref.file_id) != 0:
                    external[int(ref.file_id)] = external.get(int(ref.file_id), 0) + 1
                elif int(ref.path_id) == 0:
                    nulls += 1
                elif int(ref.path_id) in ids:
                    in_pkg += 1
                else:
                    dangling.append(f"{ref}（宿主 class{cid.get(o[4], -1)}:{pid}）")
    return PptrAudit(
        bundle=str(src), objects=len(info.objects), decoded=decoded,
        undecodable=tuple(sorted(undecodable.items())), pptr_total=total,
        in_package=in_pkg, null=nulls, external=tuple(sorted(external.items())),
        dangling=tuple(dangling),
    )


# --------------------------------------------------------------------------- #
# 材质挂的是不是**桩 shader**（1 Pass）：真机「在、但平」的机械判据
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ShaderShapeAudit:
    """包里「材质 → shader」的形状读数。

    2026-10-27 真机（M1A2 SEP v3 的 M2/M240L）：AssetRipper 的桩 shader 只有 1 个 Pass
    （贴图直乘、不受光）⇒ 单位里**不被皮肤覆盖**的件会「在、但平」。工程里同名真件
    （HDRP 17.3.0 的 `Lit.shader`）编出来是 8 个 Pass；造包前由 `core.assets.shader_bind`
    把材质指回真件。这道尺子查的是**包本身**，能挡住「绕过工具直接换包」：
    材质挂 1 Pass、而包里还有同名多 Pass 的 shader ⇒ 红。
    """

    bundle: str
    shaders: int
    materials: int
    stub_bound: tuple[str, ...]
    cross_file: int
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        out = [f"shader {self.shaders} 个 · 材质 {self.materials} 颗"
               f" · 挂 1 Pass 桩 {len(self.stub_bound)} 颗"
               f" · 材质 shader 跨文件 {self.cross_file} 颗"]
        out.extend("   ⚠ " + row for row in self.stub_bound[:6])
        if len(self.stub_bound) > 6:
            out.append(f"   … 共 {len(self.stub_bound)} 颗")
        return tuple(out)


def audit_shader_shapes(path: str | Path,
                        *, common_strings: Mapping[int, str] | None = None,
                        ) -> ShaderShapeAudit:
    """读这个包里每个材质的 shader；判据 = **1 Pass 挂载 + 包内同名有多 Pass 版本**。

    ⛔ 跨文件引用（`m_FileID != 0`）单独计数、不判红：那是另一种形态（2026-10-27 真机实测
    跨包引用 shader 会让件整条不见，但那是 `--shader link` 的事，不在本尺子范围）。
    """
    from ba_mod_maker.core.asset import ufs  # noqa: PLC0415

    src = Path(path)
    common = common_strings or clone.unitypy_common_strings()
    shaders: dict[int, tuple[str, int]] = {}
    mats: list[tuple[str, int, int]] = []
    with ufs.UfsReader(src) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
        meta = metadata_writer.parse_serialized_metadata(
            reader.read_range(node0.offset, info.data_offset))
        types = list(meta.types)
        cid = {i: t.class_id for i, t in enumerate(types)}
        pool = clone.ref_type_pool(meta, common_strings=common)
        cache: dict[int, Any] = {}

        def nodes_of(tid: int):
            if tid not in cache:
                tt = types[tid].type_tree
                cache[tid] = clone.read_type_nodes(
                    struct.pack("<ii", tt.node_count, tt.string_buffer_size)
                    + tt.nodes + tt.strings, common_strings=common)
            return cache[tid]

        for o in info.objects:
            cls = int(cid.get(o[4], -1))
            if cls not in (21, 48):
                continue
            payload = bytes(reader.read_range(
                node0.offset + info.data_offset + o[2], o[3]))
            try:
                obj = clone.read_object(payload, nodes_of(o[4]), ref_types=pool)
            except Exception:  # noqa: BLE001
                continue
            if cls == 48:
                parsed = obj.get("m_ParsedForm") or {}
                name = str(parsed.get("m_Name") or obj.get("m_Name") or "?")
                subs = parsed.get("m_SubShaders") or []
                passes = sum(len(s.get("m_Passes") or []) for s in subs)
                shaders[int(o[1])] = (name, passes)
            else:
                ptr = obj.get("m_Shader") or {}
                mats.append((str(obj.get("m_Name") or "?"),
                             int(ptr.get("m_FileID") or 0), int(ptr.get("m_PathID") or 0)))

    multi: dict[str, int] = {}
    for name, passes in shaders.values():
        if passes > 1:
            multi[name] = max(multi.get(name, 0), passes)
    stub_bound: list[str] = []
    cross = 0
    for name, file_id, path_id in mats:
        if file_id != 0:
            cross += 1
            continue
        hit = shaders.get(path_id)
        if hit is None:
            continue
        shader_name, passes = hit
        if passes <= 1 and shader_name in multi:
            stub_bound.append(
                f"{name} · {shader_name}（1 Pass；包内同类 {multi[shader_name]} Pass）")
    problems: list[str] = []
    if stub_bound:
        names = sorted({row.split(" · ")[1].split("（")[0] for row in stub_bound})
        problems.append(
            "材质挂 1 Pass 桩、包内同名 shader 有多 Pass 版本："
            + "、".join(names) + f"（{len(stub_bound)} 颗）"
            " ⇒ 真机上这些件会「在、但平」；造包前跑 `pack shader-bind`"
            "（已接进 `pack unity-build`）")
    return ShaderShapeAudit(
        bundle=str(src), shaders=len(shaders), materials=len(mats),
        stub_bound=tuple(stub_bound), cross_file=cross, problems=tuple(problems),
    )


