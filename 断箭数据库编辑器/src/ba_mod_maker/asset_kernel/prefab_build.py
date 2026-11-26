r"""W10-③ · prefab 构建器：从零造 GameObject / Transform / 渲染器组件。

## 这一模块解决什么

设计稿 §9.5 施工顺序第 4 步（Mesh → Material → GameObject / Transform → SkinnedMeshRenderer /
LODGroup → MonoBehaviour）里**对象与层级**那一块：不复制任何现成对象的字节，按**真包内嵌的类型树**
当 schema，把我们自己填的字段树序列化成合法对象，并把**层级 + PPtr 双向引用**接对。

    type_entries_for_classes(meta, [...])   从真包元数据区取类型表项（逐字节照抄的来源）
    default_fields(nodes)                   按类型树生成**完整**的默认字段树（一个字段不少）
    PrefabNode / PrefabSpec                 我们要造的那棵树（我们的值）
    build_prefab(spec, entries)             出对象字节 + 双向接线 + 可追溯的类型表报告
    verify_hierarchy(build)                 读回来逐条自证（父子/组件/PPtr/往返）

## 三条实测口径（2026-10-03，units 包；复算见 _work/w10_prefab_types.py / w10_prefab_contract.json）

1. **类型表项号不是按 classID 顺序排的**（units 50 项）：
   GameObject(1) → 4 / Transform(4) → 1 / MeshRenderer(23) → 13
   MeshFilter(33) → 6 / LODGroup(205) → 5 / SkinnedMeshRenderer(137) → 9
   Material(21) → 2 / Mesh(43) → 3 / AssetBundle(142) → 47。
   ⇒ 新包的 type_id **只能按照抄来的项在本包里重新排**，⛔ 不许把 units 的项号抄过来。
2. **MonoBehaviour(114) 有 22 项，且项号与 script_type_index 是置换**：
   项号 [7,10,11,15,17,18,19,20,21,23,24,26,28,30,32,37,40,41,44,45,46,49]
   ↔ script_index [4,21,19,17,14,6,5,2,16,3,20,11,13,15,8,0,9,7,12,18,1,10]。
   ⇒「按 classID 取一项」**必取错**；只能 RawTypeTable.entry_for(114, script_index=k) 点名。
3. **字段名一个都不能猜**：本作这套类型树里 GameObject 只有
   m_Component / m_Layer / m_Name / m_Tag(UInt16) / m_IsActive（**没有** m_TagString / m_Icon /
   m_NavMeshLayer / m_StaticEditorFlags），LODGroup 的 LOD 元素字段是
   screenRelativeHeight / fadeTransitionWidth / renderers。
   ⇒ 默认值一律**由类型树生成**，我们只覆盖有语义的字段。

## 硬契约节点（设计稿 §7.4 的机器可读版）

UNIT_NODE_CONTRACT 那张表把「谁在读（模块::函数）+ 匹配规则 + 反编译证据」落成数据，用例直接断言它。
本轮**实测修正**了一条关键层级：
**BaseArmor / BaseAbility / BaseMobility 是 Chassis 的直接子节点**，不是 root 的直接子节点 ——
UnitBuilder.InitArmors / InitUnitExtraModelByName 都是先 Transform.FindChild("Chassis")，
再 Assets.FindChildren(chassis, "<名字>")；
FindChildren 只遍历**直接子节点**且用 String.op_Equality。

## 边界

* 顶层只借 clone（类型树 codec，同一份逻辑）与 builder（类型表读取 + 序列化装配），不重复实现。
* **不碰 MonoBehaviour 的造法**（本单不要求）：MonoBehaviourSpec 只做到「按 script_index 点名取树 +
  字段树由类型树生成」，m_Script 必须调用方显式给（我们自己包里没有 MonoScript 对象）——
  给不出就**抛**，⛔ 不造一个悬空 PPtr 出来。
"""

from __future__ import annotations

import copy
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ba_mod_maker.asset_kernel import builder as builder_mod
from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "CLASS_GAME_OBJECT",
    "CLASS_LOD_GROUP",
    "CLASS_MESH_FILTER",
    "CLASS_MESH_RENDERER",
    "CLASS_MONO_BEHAVIOUR",
    "CLASS_NAMES",
    "CLASS_SKINNED_MESH_RENDERER",
    "CLASS_TRANSFORM",
    "ExtraComponent",
    "NodeRef",
    "KINDS",
    "KIND_EMPTY",
    "KIND_MESH",
    "KIND_SKINNED",
    "MINIMAL_UNIT_NODES",
    "UNIT_NODE_CONTRACT",
    "HierarchyReport",
    "MonoBehaviourSeam",
    "MonoBehaviourSpec",
    "NodeContractRow",
    "PrefabBuild",
    "PrefabNode",
    "PrefabObject",
    "TypeReportRow",
    "build_prefab",
    "default_fields",
    "mono_behaviour_seam",
    "node_paths",
    "plan_path_ids",
    "pptr",
    "serialize_prefab",
    "type_entries_for_classes",
    "verify_hierarchy",
]

CLASS_GAME_OBJECT = 1
CLASS_TRANSFORM = 4
CLASS_MESH_RENDERER = 23
CLASS_MESH_FILTER = 33
CLASS_MONO_BEHAVIOUR = 114
CLASS_SKINNED_MESH_RENDERER = 137
CLASS_LOD_GROUP = 205

#: 组件类 -> 类名（读数/判据一行用；不参与字节）。
CLASS_NAMES: Mapping[int, str] = {
    CLASS_GAME_OBJECT: "GameObject",
    CLASS_TRANSFORM: "Transform",
    CLASS_MESH_RENDERER: "MeshRenderer",
    CLASS_MESH_FILTER: "MeshFilter",
    CLASS_MONO_BEHAVIOUR: "MonoBehaviour",
    CLASS_SKINNED_MESH_RENDERER: "SkinnedMeshRenderer",
    CLASS_LOD_GROUP: "LODGroup",
}

#: 节点三种挂法：空节点 / 静态网格（MeshFilter + MeshRenderer）/ 蒙皮（SkinnedMeshRenderer）。
KIND_EMPTY = "empty"
KIND_MESH = "mesh"
KIND_SKINNED = "skinned"
KINDS = (KIND_EMPTY, KIND_MESH, KIND_SKINNED)

#: 会被当作「一个 renderer 组件」的两类（LOD 成员解析用）。
RENDERER_CLASSES = (CLASS_MESH_RENDERER, CLASS_SKINNED_MESH_RENDERER)

#: 渲染器（MeshRenderer(23) / SkinnedMeshRenderer(137)）里**引擎真值**的公共字段。
#:
#: ⚠ **类型树的默认值（全 0）不是引擎默认** —— 它是"这份资产从没被编辑器写过"。
#: 2026-10-08 普查真件 units 包：**6421 个 SMR + 2534 个 MeshRenderer**（逐条出处与"写错的后果"
#: 见 `_work/a2-skin-mono-spec.md` §2.1 与 §5.3）：
#:
#: | 字段 | 真值 | 写错会怎样 |
#: |---|---|---|
#: | `m_RenderingLayerMask` | **513**（SMR 513×4885 / 257×1536） | 0 = **不在任何渲染层** ⇒
#: 游戏那套光照/贴花都不认它（机库里"车体看不见"的现场就是这样） |
#: | `m_LightmapIndex` / `…Dynamic` | **65535**（= -1） | 别的值 ⇒ 去找一张**不存在**的 lightmap |
#: | `m_LightmapTilingOffset`（+Dynamic） | **{1,1,0,0}** | 全 0 ⇒ lightmap UV 缩放为 0 |
#: | `m_ReceiveShadows` / `m_DynamicOccludee` / `m_MotionVectors` /
#: | `m_RayTracingMode` | MR **2** / SMR **3** | ⛔ 两类**不能**共用同一个常量 |
#: | `m_RayTracingMode` | MR **2** / SMR **3** | ⛔ 两个类**不能**共用同一个常量 |
RENDERER_ENGINE_DEFAULTS: dict[str, object] = {
    "m_CastShadows": 1,
    "m_ReceiveShadows": 1,
    "m_DynamicOccludee": 1,
    "m_MotionVectors": 1,
    "m_LightProbeUsage": 1,
    "m_ReflectionProbeUsage": 1,
    "m_RenderingLayerMask": 513,
    "m_LightmapIndex": 65535,
    "m_LightmapIndexDynamic": 65535,
    "m_LightmapTilingOffset": {"x": 1.0, "y": 1.0, "z": 0.0, "w": 0.0},
    "m_LightmapTilingOffsetDynamic": {"x": 1.0, "y": 1.0, "z": 0.0, "w": 0.0},
}


def apply_renderer_engine_defaults(fields: dict, class_id: int) -> None:
    """把**引擎真值**写进我们自造的渲染器字段（就地改）。理由见 RENDERER_ENGINE_DEFAULTS。

    `m_RayTracingMode` 按类给：MeshRenderer **2** / SkinnedMeshRenderer **3**。
    只写**类型树里真有**的键 —— 树里没有的键写进去也会被静默丢弃（别猜别的 Unity 版本的字段）。
    """
    for key, value in RENDERER_ENGINE_DEFAULTS.items():
        if key in fields:
            fields[key] = copy.deepcopy(value)
    if "m_RayTracingMode" in fields:
        fields["m_RayTracingMode"] = (
            3 if int(class_id) == CLASS_SKINNED_MESH_RENDERER else 2
        )


#: required_type_keys() 的固定顺序（MonoBehaviour 排最后、按 script_index 升序）。
_TYPE_ORDER: tuple[TypeKey, ...] = (
    (CLASS_GAME_OBJECT, None),
    (CLASS_TRANSFORM, None),
    (CLASS_MESH_FILTER, None),
    (CLASS_MESH_RENDERER, None),
    (CLASS_SKINNED_MESH_RENDERER, None),
    (CLASS_LOD_GROUP, None),
)


# --------------------------------------------------------------------------- #
# §7.4 硬契约（机器可读）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NodeContractRow:
    """一个（或一族）节点名：谁在读 + 匹配规则 + 反编译证据。

    rule 的取值是本轮**实测**出来的几种之一：
        exact_cs      —— String.op_Equality 对**原始**名字（精确、大小写敏感）
        exact_ci      —— ToLower 之后 String.op_Equality（精确、大小写不敏感）
        prefix_cs     —— name.Length>N && Substring(0,N) == 字面量（前缀、大小写敏感）
        substring_cs  —— Assets.FindTransformBySubstring -> String.Contains
                        （直接子节点、子串、大小写敏感）
        substring_ci  —— ToLower 之后 String.Contains（子串、大小写不敏感）
        data_only     —— 代码里 0 命中，只活在 prefab 数据 / 序列化字段里
    """

    names: str
    parent: str
    rule: str
    readers: tuple[str, ...]
    evidence: tuple[str, ...]
    needed_for_minimal: bool
    #: 找不到这个节点时会怎样（null 安全 / 抛 / 未量）—— 工单 §3 明写要这一列
    if_missing: str
    note: str = ""


UNIT_NODE_CONTRACT: tuple[NodeContractRow, ...] = (
    NodeContractRow(
        names="root",
        parent="（prefab 根 GameObject）",
        rule="exact_cs / exact_ci",
        readers=(
            "UnitBuilder.UnpackRemainingTurretParts（炮塔 GameObject 的直接子节点）",
            "BoneContainer.Bake+<>c.<Bake>b__23_0（ToLower 后精确）",
        ),
        evidence=(
            "UnitBuilder.txt:29327/29336 Move rdx,\"root\"/\"body\" -> Call 0x183E49C40",
            "0x183E49C40 = System.String$$op_Equality（script.json Address 65313856）",
            "BoneContainer_NestedType___c.txt:116/175 -> String.ToLower + op_Equality",
        ),
        needed_for_minimal=False,
        if_missing="未量（本轮没读到 UnpackRemainingTurretParts 的缺节点分支）",
        note="骨骼/炮塔拆解才找它；本单最小 prefab 不做骨骼与炮塔，名字仍按契约留着更稳",
    ),
    NodeContractRow(
        names="body",
        parent="（prefab 根 GameObject）",
        rule="exact_cs / exact_ci",
        readers=("UnitBuilder.UnpackRemainingTurretParts", "BoneContainer.Bake+<>c.<Bake>b__23_1"),
        evidence=("UnitBuilder.txt:29336/29380", "BoneContainer_NestedType___c.txt:175"),
        needed_for_minimal=False,
        if_missing="未量（同 root）",
        note="同 root",
    ),
    NodeContractRow(
        names="Chassis",
        parent="prefab 根的直接子节点",
        rule="exact_cs（Transform.FindChild）",
        readers=(
            "UnitBuilder.InitArmors",
            "UnitBuilder.InitUnitExtraModelByName",
            "UnitBuilder.UnpackRemainingTurretParts",
        ),
        evidence=(
            "UnitBuilder.txt:26402/25519/29296 Call 0x18550C730 = UnityEngine.Transform$$FindChild",
            "26584/26590 InitArmors: FindChild(root,\"Chassis\")"
            " -> Assets.FindChildren(chassis,\"BaseArmor\")",
        ),
        needed_for_minimal=True,
        if_missing="未命中返回 null，调用点判空后走 Enumerable.Empty 分支"
        " ⇒ **静默跳过、不抛**（UnitBuilder.txt:26402-26408 与 25519-25536）",
        note="Transform.FindChild 只找**直接子节点**、精确、大小写敏感（不是递归）",
    ),
    NodeContractRow(
        names="BaseArmor",
        parent="Chassis 的直接子节点",
        rule="exact_cs（Assets.FindChildren 遍历直接子节点 + op_Equality）",
        readers=("UnitBuilder.InitArmors",),
        evidence=(
            "UnitBuilder.txt:26587-26590 Move rdx,\"BaseArmor\" -> Assets.FindChildren(Chassis)",
            "Assets.txt:628-745 FindChildren = Transform.GetEnumerator(直接子节点) + op_Equality",
        ),
        needed_for_minimal=True,
        if_missing="FindChildren 返回**空数组**（25632 的 Enumerable.Empty 分支）⇒ 静默跳过、不抛",
        note="★ 本轮实测修正：它挂在 **Chassis** 下，不是 root 下",
    ),
    NodeContractRow(
        names="BaseAbility",
        parent="Chassis 的直接子节点",
        rule="exact_cs（同上）",
        readers=("UnitBuilder.InitAbilities", "UnitBuilder.InitUnitExtraModelByName"),
        evidence=(
            "UnitBuilder.txt:24765 InitAbilities ->"
            " InitUnitExtraModelByName(baseName=\"BaseAbility\")",
            "UnitBuilder.txt:25183 mov r15,r9(rootTransform)"
            " -> 25223 FindChild(\"Chassis\") -> 25229 FindChildren",
        ),
        needed_for_minimal=True,
        if_missing="同 BaseArmor（空数组、静默）",
        note="★ 同上（Chassis 下）",
    ),
    NodeContractRow(
        names="BaseMobility",
        parent="Chassis 的直接子节点",
        rule="exact_cs（同上）",
        readers=("UnitBuilder.InitMobility", "UnitBuilder.InitUnitExtraModelByName"),
        evidence=(
            "UnitBuilder.txt:25129/25154",
            "同一函数体 25183/25223/25229（FindChild Chassis -> FindChildren baseName）",
        ),
        needed_for_minimal=True,
        if_missing="同 BaseArmor（空数组、静默）",
        note="★ 同上（Chassis 下）",
    ),
    NodeContractRow(
        names="turret_*",
        parent="（运行时由游戏新建并 SetParent 到炮塔位）",
        rule="substring_cs（仅用于**命名**新建对象）",
        readers=("UnitBuilder.IntegrateBaseTurret",),
        evidence=(
            "UnitBuilder.txt:27326-27329 String.Concat(\"turret_\", Int32.ToString(i))",
            "27346 Transform.SetParent / 27350 Object.set_name（是**写**名字，不是找节点）",
        ),
        needed_for_minimal=False,
        if_missing="不需要预置（游戏自己新建并用它命名）",
        note="全仓只 1 个 turret_ 字面量站点；prefab 里不需要预置",
    ),
    NodeContractRow(
        names="recoil_*",
        parent="武器/炮塔子树（直接子节点）",
        rule="substring_cs（Assets.FindTransformBySubstring -> String.Contains）",
        readers=("UnitBuilder.SetupWeaponComponents",),
        evidence=(
            "UnitBuilder.txt:16156-16161 String.Concat(\"recoil_\",i)"
            " -> Assets.FindTransformBySubstring",
            "Assets.txt:58-94 FindTransformBySubstring = childCount/GetChild"
            " + String$$Contains(0x183E47E50)",
        ),
        needed_for_minimal=False,
        if_missing="FindTransformBySubstring 返回 null，调用点有 null 检查"
        "（UnitBuilder.txt:16162-16167）⇒ 不抛",
        note="只对**带武器的炮塔**才找；底盘 prefab 不需要",
    ),
    NodeContractRow(
        names="LR0 / RR0",
        parent="底盘子树",
        rule="prefix_cs（name.Length>3 && Substring(0,3) == 字面量）",
        readers=("Wheels+<>c.<.ctor>b__10_0（LR0）", "Wheels+<>c.<.ctor>b__10_1（RR0）"),
        evidence=(
            "Wheels_NestedType___c.txt:143/229",
            "长度字段 [rax+16] 与 3 比 -> JumpIfGreater -> Substring(0,3) -> op_Equality",
        ),
        needed_for_minimal=False,
        if_missing="**未量**：Wheels 构造函数缺节点的分支本轮没读（未达，如实入账）",
        note="名字长度必须 >3；缺了只是没轮子动画",
    ),
    NodeContractRow(
        names="L0 / R0",
        parent="底盘子树",
        rule="prefix_cs（name.Length>3 && Substring(0,2) == 字面量）",
        readers=("Suspension+<>c.<.ctor>b__2_0（L0）", "Suspension+<>c.<.ctor>b__2_2（R0）"),
        evidence=(
            "Suspension_NestedType___c.txt:67-151 / 179+",
            "Substring(0,2) -> op_Equality；排序键是 localPosition.y（b__2_1）",
        ),
        needed_for_minimal=False,
        if_missing="**未量**：Suspension 构造函数缺节点的分支本轮没读（未达，如实入账）",
        note="悬挂顺序按 localPosition.y 排，不是节点顺序（设计稿 §3.9）",
    ),
    NodeContractRow(
        names="trackl / trackr",
        parent="底盘子树",
        rule="substring_ci（ToLower 之后 String.Contains）",
        readers=("Tracks+<>c.<.ctor>b__16_0（trackl）", "Tracks+<>c.<.ctor>b__16_1（trackr）"),
        evidence=(
            "Tracks_NestedType___c.txt:120/183",
            "String.ToLower(0x183E51D50) -> String$$Contains(0x183E47E50)",
        ),
        needed_for_minimal=False,
        if_missing="**未量**：Tracks 构造函数缺节点的分支本轮没读（未达，如实入账）",
        note="唯一一处**大小写不敏感 + 子串**的名字判据",
    ),
    NodeContractRow(
        names="weapon_* / shell_spawn_*",
        parent="（数据侧 PPtr）",
        rule="data_only",
        readers=(
            "无（全仓 24,059 个反编译 txt 扫描：weapon_ 13 个站点全在 UI/本地化；"
            "shell_spawn_ 0 个）",
        ),
        evidence=("_work/w10_prefab_contract.json 的 sites 计数",),
        needed_for_minimal=False,
        if_missing="代码里不读它 ⇒ 无所谓",
        note="只活在 prefab 数据与 UnitPrefabTurretInfo.Weapons[] 的 PPtr 里",
    ),
)

#: 让 UnitBuilder 认得的**最小**节点集（本轮实测口径：Base* 在 Chassis 下）。
MINIMAL_UNIT_NODES: tuple[tuple[str, str], ...] = (
    ("root", ""),
    ("Chassis", "root"),
    ("BaseArmor", "Chassis"),
    ("BaseAbility", "Chassis"),
    ("BaseMobility", "Chassis"),
)


# --------------------------------------------------------------------------- #
# 类型树 -> 默认字段树
# --------------------------------------------------------------------------- #
def _subtree_end(nodes: Sequence[clone.TypeNode], i: int) -> int:
    """节点 i 的子树结束下标（不含）。"""
    level = nodes[i].level
    j = i + 1
    while j < len(nodes) and nodes[j].level > level:
        j += 1
    return j


def _defaults_from(nodes: Sequence[clone.TypeNode], i: int) -> Any:
    """结构驱动：叶子 = 标量，Array 长子 = 数组，其余 = 结构体。

    ⚠ 这里**不查标量表**：树里「没有子节点的叶子」就是标量（int/float/bool/char…），
    于是标量的那份实现仍然只有 clone 一处（RUF100 不会因为抄表而多一条 noqa）。
    """
    node = nodes[i]
    level = node.level
    if node.type == "string":
        return ""
    if node.type == "TypelessData":
        return b""
    if node.type.startswith("PPtr<"):
        return {"m_FileID": 0, "m_PathID": 0}
    end = _subtree_end(nodes, i)
    if end == i + 1:  # 叶子 = 标量
        if node.type == "bool":
            return False
        if node.type in ("float", "double"):
            return 0.0
        return 0
    if nodes[i + 1].type == "Array" and nodes[i + 1].level == level + 1:
        return []
    out: dict[str, Any] = {}
    j = i + 1
    while j < end:
        name = nodes[j].name
        out[name] = _defaults_from(nodes, j)
        j = _subtree_end(nodes, j)
    return out


def _fix_serialize_reference_versions(fields: Any, depth: int = 0) -> None:
    """把 `[SerializeReference]` 容器的 `version` 补成 **2**（真件 608/608 都是 2）。

    口径来自实测：容器字段形如 `{"version": int, "RefIds": [...]}`；Unity 2022 写的是 2。
    类型树默认值给的是 0 —— 直接用 0 会让 Unity 按另一套布局读，读越界（症状：
    `The file ... is corrupted! [Position out of bounds!]` + 游戏侧 Debug.Break()）。
    """
    if depth > 8 or not isinstance(fields, dict):
        return
    if "version" in fields and "RefIds" in fields:
        fields["version"] = 2
    for value in fields.values():
        if isinstance(value, dict):
            _fix_serialize_reference_versions(value, depth + 1)
        elif isinstance(value, list):
            for item in value:
                _fix_serialize_reference_versions(item, depth + 1)


def default_fields(nodes: Sequence[clone.TypeNode]) -> dict[str, Any]:
    """按类型树生成**完整**的默认字段树（标量 0/False、字符串 ""、PPtr 空、数组 []、结构体递归）。

    这是「一个字段都不许自己编」的落点：字段**名字与存在性**全部来自真包的类型树，
    我们只覆盖有语义的那几个（m_Name / m_Component / m_Children / m_Father / m_Materials …）。
    """
    if not nodes:
        raise AssetError("空类型树，造不出字段树")
    value = _defaults_from(nodes, 0)
    if not isinstance(value, dict):
        raise AssetError(
            f"类型树根节点 {nodes[0].type} 不是结构体",
            hint="classID 选错了？类型表项必须指向一个结构体树",
        )
    return value


# --------------------------------------------------------------------------- #
# 类型表项：点名取树 + 可追溯报告
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TypeReportRow:
    """一个 classID（MonoBehaviour 再加 script_index）用的是**哪一个**类型表项。"""

    class_id: int
    class_name: str
    script_index: int
    type_id: int
    node_count: int
    root_type: str

    def line(self) -> str:
        return (
            f"{self.class_name}(classID {self.class_id}/script_index {self.script_index}) "
            f"-> 类型表第 {self.type_id} 项（{self.node_count} 个节点，根 {self.root_type}）"
        )


#: (classID, script_index)；script_index = None 表示「按 classID 唯一取」。
TypeKey = tuple[int, int | None]


def type_key_of(class_id: int, script_index: int) -> TypeKey:
    """RawTypeEntry.script_index（非 MonoBehaviour 是 **-1**）-> TypeKey（用的是 None）。

    ⚠ 这个换算只此一处：报错话术与 reading 都按 TypeKey 走，别在两处各判一次。
    """
    return (int(class_id), None if int(script_index) < 0 else int(script_index))


def type_entries_for_classes(
    meta: bytes | bytearray,
    wanted: Sequence[TypeKey],
    *,
    common_strings: Mapping[int, str] | None = None,
) -> tuple[
    builder_mod.RawTypeTable,
    dict[TypeKey, builder_mod.RawTypeEntry],
    tuple[TypeReportRow, ...],
]:
    """从**真包**的元数据区取这些类的类型表项（设计稿 §9.5 第 2 步：树是内嵌的，不许自己写）。

    ⚠ classID=114（MonoBehaviour）有 22 项 ⇒ **必须**点名 script_index；
    取出来的项在自己包里会重新排序，TypeReportRow.type_id 记的是**源包**里的项号（可追溯用）。
    """
    table = builder_mod.read_raw_type_table(meta, common_strings=common_strings)
    picked: dict[TypeKey, builder_mod.RawTypeEntry] = {}
    report: list[TypeReportRow] = []
    for class_id, script_index in wanted:
        key: TypeKey = (int(class_id), None if script_index is None else int(script_index))
        if key in picked:
            continue
        entry = table.entry_for(int(class_id), script_index=key[1])
        picked[key] = entry
        report.append(
            TypeReportRow(
                class_id=entry.class_id,
                class_name=CLASS_NAMES.get(entry.class_id, str(entry.class_id)),
                script_index=entry.script_index,
                type_id=entry.index,
                node_count=len(entry.nodes),
                root_type=entry.nodes[0].type if entry.nodes else "",
            )
        )
    return table, picked, tuple(report)


# --------------------------------------------------------------------------- #
# 我们要造的树（值）
# --------------------------------------------------------------------------- #
def pptr(path_id: int, file_id: int = 0) -> dict[str, int]:
    """PPtr（包内引用一律 fileID = 0）。"""
    return {"m_FileID": int(file_id), "m_PathID": int(path_id)}


@dataclass(frozen=True, slots=True)
class MonoBehaviourSpec:
    """一个 MonoBehaviour 组件要造什么（本单只做到「按 script_index 点名取树」这一步）。

    script 是 m_Script 那条 PPtr 的值：⛔ 必须调用方显式给 —— 我们自己包里**没有**
    MonoScript 对象，凭空写一个 pathID 就是造悬空引用（N4 判据会当场红）。
    """

    script_index: int
    script: dict[str, int] | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    name: str = "MonoBehaviour"


@dataclass(frozen=True, slots=True)
class NodeRef:
    """字段值 = **树上某个节点**的 PPtr（pathID 由 build_prefab 在装配时解析）。

    为什么需要它：UnitPrefabRoot 的 PrefabRoot / PrefabBody / Renderers 指向**同一棵树上的
    对象**，而 pathID 是 build_prefab 内部按 (seed, 顺序) 分配的 —— 调用方在写 spec 时
    根本不知道那些数。真件读数（RU_T72B_Base，2026-10-06）：PrefabRoot / PrefabBody 都非空、
    Renderers 9~15 条；而 a2 的规格说 PrefabBody 为空 ⇒ SetParent(x, null) 挂到场景根（静默）。

    kind：`transform`（默认）/ `gameobject` / `renderer`（该节点上的 MeshRenderer 或 SMR）。
    """

    path: str
    kind: str = "transform"


@dataclass(frozen=True, slots=True)
class ExtraComponent:
    """挂到某个节点上的**额外组件**（Animator / BoxCollider / …）。

    字段树 = 类型树的默认值 + `fields` 补丁；`m_GameObject` 自动指向该节点的 GameObject。
    ⛔ 不猜字段：`fields` 里出现树里没有的键直接抛（与 MonoBehaviourSpec 同一条口径）。
    真件落点（2026-10-06 实测 RU_T72B_Base.prefab 根）：Animator(95) 挂根、
    `Entrances` 节点上要 BoxCollider(65) —— 见 _work/v4-recon.txt。
    """

    class_id: int
    #: 挂到哪个节点（路径，如 "root/body"）；空串 = 根节点
    attach_to: str = ""
    #: 字段补丁（键必须来自类型树）
    fields: Mapping[str, Any] = field(default_factory=dict)
    #: 同一 classID 有多棵树时点名（本作只有 MonoBehaviour(114) 会用到）
    script_index: int | None = None


#: 真件 LODGroup 的 screenRelativeHeight 实测序列（T-72B：4 档；首档 0.1、末档 0.0015）。
#: 字段语义：物体屏幕高度占比 >= 该值时才画这一档；末档之下 = 剔除（所以末档必须**很小**）。
LOD_HEIGHT_FIRST = 0.1
LOD_HEIGHT_LAST = 0.0015


def lod_heights_for(count: int) -> tuple[float, ...]:
    """`count` 档的默认阈值：首档 0.1、末档 0.0015、中间按几何级数插（严格递减）。"""
    if count <= 0:
        return ()
    if count == 1:
        return (LOD_HEIGHT_LAST,)
    ratio = (LOD_HEIGHT_LAST / LOD_HEIGHT_FIRST) ** (1.0 / (count - 1))
    return tuple(LOD_HEIGHT_FIRST * ratio**i for i in range(count))


def check_lod_heights(heights: Sequence[float]) -> tuple[float, ...]:
    """阈值必须**严格递减**且为正 —— 非递减就是"有的档永远选不到"（本仓踩过一次）。"""
    values = tuple(float(h) for h in heights)
    for i, value in enumerate(values):
        if not (0.0 < value < 1.0):
            raise AssetError(f"LOD 第 {i} 档的 screenRelativeHeight={value} 不在 (0,1) 内")
        if i and value >= values[i - 1]:
            raise AssetError(
                f"LOD 阈值必须严格递减：第 {i - 1} 档 {values[i - 1]} -> 第 {i} 档 {value}",
                hint="真件是 0.1 / 0.04 / 0.005 / 0.0015；全档同值会让物体在小屏占比时整个不画",
            )
    return values


@dataclass(frozen=True, slots=True)
class PrefabNode:
    """树上的一个节点（我们的值；字段树由 build_prefab 按类型树补全）。"""

    name: str
    kind: str = KIND_EMPTY
    mesh: int | None = None
    materials: tuple[int, ...] = ()
    #: 蒙皮骨骼：写**节点路径**（本模块解析成 Transform PPtr 并按槽位保序，空槽用 None 表示）
    bones: tuple[str | None, ...] = ()
    root_bone: str | None = None
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    children: tuple[PrefabNode, ...] = ()
    mono_behaviours: tuple[MonoBehaviourSpec, ...] = ()
    #: 只有根节点填：LODGroup（levels 里写的是**节点路径**；空元组 = 不建 LODGroup）
    lod_size: float | None = None
    lod_levels: tuple[tuple[str, ...], ...] = ()
    #: 每档的 screenRelativeHeight（**必须严格递减**；省略 = `lod_heights_for(len(lod_levels))`）。
    #: 真件口径（T-72B 实测 4 档）：0.1 / 0.04 / 0.005 / 0.0015 —— 阈值是"物体屏幕高度占比
    #: 超过它才画这一档"，全档同一个值（旧实现硬编码 0.5）会让**小屏占比的物体整个不画**：
    #: 机库里坦克只占一两成屏幕 ⇒ Unity 一档都不选 ⇒ 用户看到"模型没有网格"。
    lod_heights: tuple[float, ...] = ()
    #: 额外组件（Animator / BoxCollider / …）：按 attach_to 挂到对应节点
    extra_components: tuple[ExtraComponent, ...] = ()
    #: 蒙皮渲染器的**剔除包围盒**（本作类型树里的字段名是 `m_AABB`，即 Unity 的 localBounds）。
    #: ⛔ 必填（`KIND_SKINNED` 时）：留成零尺寸 ⇒ 每帧都被视锥剔除 ⇒ **永远不画**。
    #: 2026-10-06 真机现场：用户看到"这个模型看起来是没有网格的"就是这个原因 ——
    #: 真件每个 SMR 的 m_AABB 都是真实值（例 extent (1.16, 0.26, 2.90)），我们写的是 (0,0,0)。
    local_bounds: Mapping[str, Any] | None = None


def node_paths(spec: PrefabNode, prefix: str = "") -> tuple[str, ...]:
    """这棵树里所有节点的路径（如 root/Chassis），与 build.node_ids 的键同规格。"""
    path = f"{prefix}/{spec.name}" if prefix else spec.name
    out = [path]
    for child in spec.children:
        out.extend(node_paths(child, path))
    return tuple(out)


def _walk(spec: PrefabNode, parent_path: str) -> Iterable[tuple[str, str, PrefabNode]]:
    path = f"{parent_path}/{spec.name}" if parent_path else spec.name
    yield path, parent_path, spec
    for child in spec.children:
        yield from _walk(child, path)


def plan_path_ids(
    seed: str, count: int, *, step: int = 8, base: int | None = None
) -> tuple[int, ...]:
    """确定性 pathID：以 crc32(seed) 为低 32 位、高位置 0x5100 起，步长 step。

    新包里的 pathID 只要**本包内唯一**即可（真包用的是随机大数）；用确定性值是为了
    用例与复核脚本能复算。base 给「接着已有对象往后排」用（不撞）。
    """
    if count <= 0:
        raise ValueError("count 必须为正")
    if step <= 0:
        raise ValueError("step 必须为正")
    if base is None:
        base = 0x5100_0000_0000_0000 | (zlib.crc32(seed.encode("utf-8")) & 0xFFFF_FFFF)
    ids = tuple(int(base) + i * step for i in range(count))
    if any(v <= 0 for v in ids):
        raise AssetError(f"算出来的 pathID 有非正数：{ids[:4]}")
    if len(set(ids)) != len(ids):
        raise AssetError("pathID 撞了（step 太小）")
    return ids


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PrefabObject:
    """一个造出来的对象：pathID + classID + 字段树 + 字节（+ 它的类型树，读回来要用）。"""

    path_id: int
    class_id: int
    script_index: int
    type_id: int
    name: str
    fields: dict[str, Any]
    payload: bytes
    nodes: tuple[clone.TypeNode, ...]

    @property
    def type_name(self) -> str:
        return CLASS_NAMES.get(self.class_id, str(self.class_id))

    @property
    def size(self) -> int:
        return len(self.payload)


@dataclass(frozen=True, slots=True)
class HierarchyReport:
    """层级自证（判据 b）的读数。"""

    objects: int
    game_objects: int
    transforms: int
    roundtrip_ok: int
    component_backrefs_ok: int
    components_total: int
    transform_is_first: int
    child_links_ok: int
    child_links_total: int
    father_links_ok: int
    father_links_total: int
    pptr_total: int
    pptr_null: int
    pptr_in_package: int
    pptr_dangling: int
    pptr_external: int
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def lines(self) -> tuple[str, ...]:
        out = [
            f"对象 {self.objects}（GameObject {self.game_objects} / Transform {self.transforms}）；"
            f"字节往返逐字节相同 {self.roundtrip_ok}/{self.objects}",
            f"组件回指 GameObject：{self.component_backrefs_ok}/{self.components_total}"
            f"；m_Component[0] 是 Transform 的 {self.transform_is_first}/{self.game_objects}",
            f"父子双向：m_Children {self.child_links_ok}/{self.child_links_total}"
            f"；m_Father {self.father_links_ok}/{self.father_links_total}",
            f"PPtr 共 {self.pptr_total}：包内 {self.pptr_in_package} / 空 {self.pptr_null}"
            f" / 悬空 {self.pptr_dangling} / 跨文件 {self.pptr_external}",
        ]
        out.extend(f"  x {p}" for p in self.problems)
        return tuple(out)


@dataclass(frozen=True, slots=True)
class PrefabBuild:
    """一次 prefab 构建的全部产出。"""

    root: PrefabNode
    objects: tuple[PrefabObject, ...]
    type_report: tuple[TypeReportRow, ...]
    #: 节点路径 -> (GameObject pathID, Transform pathID)
    node_ids: Mapping[str, tuple[int, int]]
    #: 节点路径 -> 该节点上所有组件对象的 pathID（顺序 = m_Component 的顺序）
    node_components: Mapping[str, tuple[int, ...]]
    root_game_object: int
    root_transform: int

    def object(self, path_id: int) -> PrefabObject:
        for item in self.objects:
            if item.path_id == int(path_id):
                return item
        raise KeyError(f"这次构建里没有 pathID={path_id}")

    def by_id(self) -> dict[int, PrefabObject]:
        return {o.path_id: o for o in self.objects}

    def object_rows(self) -> tuple[tuple[int, int, bytes], ...]:
        """给 serialize_serialized_file 的 [(pathID, classID, 字节)]（它会按 pathID 升序）。"""
        return tuple((o.path_id, o.class_id, o.payload) for o in self.objects)

    def package_ids(self) -> frozenset[int]:
        return frozenset(o.path_id for o in self.objects)

    def required_type_keys(self) -> tuple[TypeKey, ...]:
        """这次构建用到的类型表项键（**按本包 type_id 的推荐顺序**，去重）。

        集成方把 Mesh / Material / Shader / 贴图 也塞进**同一个 SerializedFile** 时，
        用这个做前缀再拼自己的键：type_id 就是那份列表的下标。

        顺序是**固定**的（不是对象创建顺序）：GameObject / Transform / MeshFilter /
        MeshRenderer / SkinnedMeshRenderer / LODGroup，然后 MonoBehaviour 按 script_index 升序。
        """
        present = {type_key_of(o.class_id, o.script_index) for o in self.objects}
        fixed = [key for key in _TYPE_ORDER if key in present]
        monos = sorted(key for key in present if key[0] == CLASS_MONO_BEHAVIOUR)
        rest = [key for key in present if key not in set(fixed) and key[0] != CLASS_MONO_BEHAVIOUR]
        return tuple([*fixed, *monos, *sorted(rest)])

    def type_ids_used(self) -> tuple[int, ...]:
        """**本包**类型表里被用到的项号（= 本模块按 wanted 顺序排出来的下标）。"""
        order = list(
            dict.fromkeys(type_key_of(o.class_id, o.script_index) for o in self.objects)
        )
        return tuple(range(len(order))) if order else ()

    def component_lines(self) -> tuple[str, ...]:
        by_id = self.by_id()
        out = []
        for path, ids in self.node_components.items():
            out.append(
                f"  {path}: " + "，".join(f"{by_id[pid].type_name}:{pid}" for pid in ids)
            )
        return tuple(out)

    def criterion_lines(self) -> tuple[str, ...]:
        go = sum(1 for o in self.objects if o.class_id == CLASS_GAME_OBJECT)
        tr = sum(1 for o in self.objects if o.class_id == CLASS_TRANSFORM)
        report = "；".join(row.line() for row in self.type_report)
        return (
            f"prefab 自造：对象 {len(self.objects)} 个（GameObject {go} / Transform {tr}）"
            f"，字节 {sum(o.size for o in self.objects):,} B"
            f"，根 GameObject pathID {self.root_game_object}",
            f"类型表（来自真包，可追溯）：{report}",
        )


def _resolve_node_refs(
    value: Any,
    node_ids: Mapping[str, tuple[int, int]],
    node_components: Mapping[str, tuple[int, ...]],
    component_class: Mapping[int, int],
    *,
    where: str,
) -> Any:
    """把字段值里的 NodeRef 递归换成真 PPtr（其它值原样返回）。

    ⛔ 路径不在树上 / renderer 不唯一 -> 抛（不静默给 0，那会变成一条悬空引用）。
    """
    if isinstance(value, NodeRef):
        if value.path not in node_ids:
            raise AssetError(
                f"{where} 里的 NodeRef 指向不存在的节点 {value.path!r}",
                hint=f"可选：{sorted(node_ids)}",
            )
        go_id, tr_id = node_ids[value.path]
        if value.kind == "transform":
            return pptr(tr_id)
        if value.kind == "gameobject":
            return pptr(go_id)
        if value.kind == "renderer":
            hits = [pid for pid in node_components[value.path]
                    if component_class.get(pid) in RENDERER_CLASSES]
            if len(hits) != 1:
                raise AssetError(
                    f"{where} 的 NodeRef({value.path!r}, renderer) 命中 {len(hits)} 个 renderer",
                    hint="要么这个节点上没有 renderer，要么有多个",
                )
            return pptr(hits[0])
        raise AssetError(
            f"{where} 的 NodeRef.kind={value.kind!r} 不认（transform/gameobject/renderer）"
        )
    if isinstance(value, Mapping):
        return {k: _resolve_node_refs(v, node_ids, node_components, component_class, where=where)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve_node_refs(v, node_ids, node_components, component_class, where=where)
                for v in value]
    return value


def _serialize_and_check(
    nodes: Sequence[clone.TypeNode], fields: Mapping[str, Any]
) -> tuple[bytes, dict]:
    """序列化 + 立刻读回 + 再写回：三步对不上就抛（本模块的正控）。"""
    payload = clone.write_object(fields, nodes)
    back = clone.read_object(payload, nodes)
    again = clone.write_object(back, nodes)
    if again != payload:
        raise AssetError("字段树 -> 字节 -> 字段树 -> 字节 不稳定（类型树与字段对不上）")
    return payload, back


# --------------------------------------------------------------------------- #
# 构建
# --------------------------------------------------------------------------- #
def build_prefab(
    spec: PrefabNode,
    entries: Mapping[TypeKey, builder_mod.RawTypeEntry],
    *,
    seed: str = "w10-prefab",
    path_ids: Sequence[int] | None = None,
    type_report: Sequence[TypeReportRow] = (),
    extra_package_ids: Iterable[int] = (),
    script_path_ids: Iterable[int] = (),
) -> PrefabBuild:
    """从零造一棵 prefab（GameObject + Transform + MeshFilter/MeshRenderer 或 SMR + LODGroup）。

    entries 是 type_entries_for_classes 的产物：键 (classID, script_index)。

    三步走（顺序不能反）：① 分 pathID ② 建字段树 + **把父子双向引用接好** ③ 逐个序列化。
    ⚠ 第 ② 步必须在序列化**之前**做完：m_Children 是序列化进字节的字段，
    先序列化再往字段 dict 里追加 = 字节里没有孩子（本单第一版就这么错过，自证当场抓到）。
    """
    walk = list(_walk(spec, ""))
    all_paths = {path for path, _parent, _node in walk}
    root_path = walk[0][0] if walk else ""
    #: 额外组件按 **attach_to 指的那个节点** 归队（`attach_to` 空 = 根节点）
    extras_at: dict[str, list[ExtraComponent]] = {}
    for _path, _parent, node in walk:
        for extra in node.extra_components:
            target = extra.attach_to or root_path
            if target not in all_paths:
                raise AssetError(
                    f"额外组件 classID={extra.class_id} 的 attach_to={target!r} 不在树上",
                    hint=f"可选：{sorted(all_paths)}",
                )
            extras_at.setdefault(target, []).append(extra)
    needed: list[TypeKey] = [(CLASS_GAME_OBJECT, None), (CLASS_TRANSFORM, None)]
    for _path, _parent, node in walk:
        if node.kind not in KINDS:
            raise AssetError(f"节点 {node.name} 的 kind={node.kind!r} 不认（只有 {KINDS}）")
        if node.kind == KIND_MESH:
            needed.extend([(CLASS_MESH_FILTER, None), (CLASS_MESH_RENDERER, None)])
        elif node.kind == KIND_SKINNED:
            needed.append((CLASS_SKINNED_MESH_RENDERER, None))
        for mono in node.mono_behaviours:
            needed.append((CLASS_MONO_BEHAVIOUR, int(mono.script_index)))
    for _path, _group in extras_at.items():
        for extra in _group:
            needed.append((int(extra.class_id), extra.script_index))
    if spec.lod_levels:
        needed.append((CLASS_LOD_GROUP, None))
    missing = [key for key in dict.fromkeys(needed) if key not in entries]
    if missing:
        raise AssetError(
            f"类型表里没有这些类的树：{missing}",
            hint="用 type_entries_for_classes(真包元数据区, wanted) 取；⛔ 不许自己写一棵树",
        )

    template = {key: default_fields(entry.nodes) for key, entry in entries.items()}

    # ---- ① 分 pathID ----
    per_node: dict[str, int] = {}
    for path, _parent, node in walk:
        count = 2
        if node.kind == KIND_MESH:
            count += 2
        elif node.kind == KIND_SKINNED:
            count += 1
        count += len(node.mono_behaviours)
        count += len(extras_at.get(path, ()))
        per_node[path] = count
    if spec.lod_levels:
        per_node[walk[0][0]] += 1
    total = sum(per_node.values())
    ids = tuple(path_ids) if path_ids is not None else plan_path_ids(seed, total)
    if len(ids) != total:
        raise ValueError(f"pathID 要 {total} 个，拿到 {len(ids)} 个")

    node_ids: dict[str, tuple[int, int]] = {}
    node_components: dict[str, tuple[int, ...]] = {}
    #: 组件 pathID -> classID（NodeRef(kind="renderer") 要在节点上挑 renderer）
    component_class: dict[int, int] = {}
    cursor = 0

    def take() -> int:
        nonlocal cursor
        value = int(ids[cursor])
        cursor += 1
        return value

    for path, _parent, node in walk:
        go_id = take()
        tr_id = take()
        component_ids: list[int] = [tr_id]
        component_class[tr_id] = CLASS_TRANSFORM
        if node.kind == KIND_MESH:
            mf_id, mr_id = take(), take()
            component_ids.extend([mf_id, mr_id])
            component_class[mf_id] = CLASS_MESH_FILTER
            component_class[mr_id] = CLASS_MESH_RENDERER
        elif node.kind == KIND_SKINNED:
            smr_id = take()
            component_ids.append(smr_id)
            component_class[smr_id] = CLASS_SKINNED_MESH_RENDERER
        for _mono in node.mono_behaviours:
            mono_id = take()
            component_ids.append(mono_id)
            component_class[mono_id] = CLASS_MONO_BEHAVIOUR
        for _extra in extras_at.get(path, ()):
            extra_id = take()
            component_ids.append(extra_id)
            component_class[extra_id] = int(_extra.class_id)
        node_ids[path] = (go_id, tr_id)
        node_components[path] = tuple(component_ids)

    lod_id: int | None = None
    if spec.lod_levels:
        lod_id = take()

    # ---- ② 建字段树（父子双向在这一步接完）----
    fields_of: dict[int, tuple[TypeKey, dict[str, Any]]] = {}
    names_of: dict[int, str] = {}

    for path, parent_path, node in walk:
        go_id, tr_id = node_ids[path]
        tr_fields = dict(template[(CLASS_TRANSFORM, None)])
        tr_fields["m_GameObject"] = pptr(go_id)
        tr_fields["m_LocalPosition"] = clone.vec3(*node.position)
        tr_fields["m_LocalRotation"] = clone.vec4(*node.rotation)
        tr_fields["m_LocalScale"] = clone.vec3(*node.scale)
        tr_fields["m_Children"] = []
        tr_fields["m_Father"] = pptr(node_ids[parent_path][1]) if parent_path else pptr(0)

        go_fields = dict(template[(CLASS_GAME_OBJECT, None)])
        go_fields["m_Name"] = node.name
        go_fields["m_IsActive"] = True

        component_ids = list(node_components[path])
        tail = component_ids[1:]

        if node.kind == KIND_MESH:
            if node.mesh is None:
                raise AssetError(
                    f"节点 {node.name} 是 mesh 但没有网格 pathID",
                    hint="kind='mesh' 时必须给 mesh（指向本包内的 Mesh 对象）",
                )
            mf_id, mr_id = tail[0], tail[1]
            tail = tail[2:]
            mf_fields = dict(template[(CLASS_MESH_FILTER, None)])
            mf_fields["m_GameObject"] = pptr(go_id)
            mf_fields["m_Mesh"] = pptr(int(node.mesh))
            fields_of[mf_id] = ((CLASS_MESH_FILTER, None), mf_fields)
            names_of[mf_id] = node.name
            mr_fields = dict(template[(CLASS_MESH_RENDERER, None)])
            mr_fields["m_GameObject"] = pptr(go_id)
            mr_fields["m_Enabled"] = True
            mr_fields["m_Materials"] = [pptr(int(m)) for m in node.materials]
            # ★ 引擎真值（见 RENDERER_ENGINE_DEFAULTS）：不写这些，机库里就是"看不见的车体"
            apply_renderer_engine_defaults(mr_fields, CLASS_MESH_RENDERER)
            fields_of[mr_id] = ((CLASS_MESH_RENDERER, None), mr_fields)
            names_of[mr_id] = node.name
        elif node.kind == KIND_SKINNED:
            smr_id = tail[0]
            tail = tail[1:]
            smr_fields = dict(template[(CLASS_SKINNED_MESH_RENDERER, None)])
            smr_fields["m_GameObject"] = pptr(go_id)
            smr_fields["m_Enabled"] = True
            smr_fields["m_Materials"] = [pptr(int(m)) for m in node.materials]
            smr_fields["m_Mesh"] = pptr(int(node.mesh)) if node.mesh else pptr(0)
            smr_fields["m_UpdateWhenOffscreen"] = False
            smr_fields["m_SkinnedMotionVectors"] = True
            # ★ 引擎真值：注意 m_RayTracingMode 对 SMR 是 **3**（MeshRenderer 才是 2）
            apply_renderer_engine_defaults(smr_fields, CLASS_SKINNED_MESH_RENDERER)
            smr_fields["m_BlendShapeWeights"] = []
            # ❗ 剔除包围盒**必须显式写**（零尺寸 = 永远不画，见 PrefabNode.local_bounds 的说明）
            if node.local_bounds is None:
                raise AssetError(
                    f"蒙皮节点 {path!r} 没给 local_bounds —— SMR 的 m_AABB"
                    " 是零尺寸就会被剔除、永远不画",
                    hint="抄真件就抄它的 m_AABB（自造几何算顶点包围盒）",
                )
            smr_fields["m_AABB"] = copy.deepcopy(dict(node.local_bounds))
            smr_fields["m_Bones"] = [
                pptr(0) if bone is None else pptr(node_ids[bone][1]) for bone in node.bones
            ]
            smr_fields["m_RootBone"] = (
                pptr(node_ids[node.root_bone][1]) if node.root_bone else pptr(0)
            )
            fields_of[smr_id] = ((CLASS_SKINNED_MESH_RENDERER, None), smr_fields)
            names_of[smr_id] = node.name

        # tail = [MonoBehaviour 的 id..., 额外组件的 id...]：两段各自的长度都由 spec 定死
        monos_tail = tail[: len(node.mono_behaviours)]
        extras_tail = tail[len(node.mono_behaviours):]
        node_extras = extras_at.get(path, ())
        for mono, mono_id in zip(node.mono_behaviours, monos_tail, strict=True):
            key: TypeKey = (CLASS_MONO_BEHAVIOUR, int(mono.script_index))
            mono_fields = dict(template[key])
            # 真件规矩（2026-10-06 实测，units 包）：
            #   * `references` 是 [SerializeReference] 的容器，**version 一律 = 2**
            #     （608/608，连 RefIds 为空的 4 个也是 2）—— 写成 0 时 Unity 走另一套解析
            #     路径，读越界 ⇒ Addressables 拿不到资产 ⇒ 游戏 Debug.Break() 崩。
            #   * `m_Enabled`：真件 5034/5035 = 1。
            _fix_serialize_reference_versions(mono_fields)
            if "m_Enabled" in mono_fields:
                mono_fields["m_Enabled"] = True
            if "m_GameObject" in mono_fields:
                mono_fields["m_GameObject"] = pptr(go_id)
            if mono.script is None:
                raise AssetError(
                    f"MonoBehaviour(script_index={mono.script_index}) 没给 m_Script",
                    hint="我们自己包里没有 MonoScript 对象；凭空写 pathID = 悬空 PPtr（N4 会红）。"
                    "要造它得先把源包 externals / script_types 的 MonoScript"
                    " 一起搬过来（本单未做）",
                )
            if "m_Script" not in mono_fields:
                raise AssetError(
                    f"script_index={mono.script_index} 的类型树里没有 m_Script 字段",
                    hint="拿错脚本的树了？MonoBehaviour 必须按 script_index 点名",
                )
            mono_fields["m_Script"] = dict(mono.script)
            for fname, raw_value in mono.fields.items():
                where = f"节点 {path} 的 MonoBehaviour({mono.script_index}).{fname}"
                value = _resolve_node_refs(raw_value, node_ids, node_components, component_class,
                                           where=where)
                if fname not in mono_fields:
                    raise AssetError(
                        f"script_index={mono.script_index} 的树里没有字段 {fname!r}",
                        hint="字段名只能来自类型树（用 default_fields(entry.nodes) 看有哪些）",
                    )
                mono_fields[fname] = value
            fields_of[mono_id] = (key, mono_fields)
            names_of[mono_id] = node.name

        for extra, extra_id in zip(node_extras, extras_tail, strict=True):
            ekey: TypeKey = (int(extra.class_id), extra.script_index)
            efields = dict(template[ekey])
            if "m_GameObject" in efields:
                efields["m_GameObject"] = pptr(go_id)
            elif extra.class_id != CLASS_LOD_GROUP:
                raise AssetError(
                    f"classID={extra.class_id} 的类型树里没有 m_GameObject",
                    hint="挂在节点上的组件都得回指 GameObject；这类对象要么不该挂节点，"
                         "要么得单独接引用（像 LODGroup 那样在别处处理）",
                )
            for fname, raw_value in extra.fields.items():
                where = f"节点 {path} 的 classID={extra.class_id}.{fname}"
                value = _resolve_node_refs(raw_value, node_ids, node_components, component_class,
                                           where=where)
                if fname not in efields:
                    raise AssetError(
                        f"classID={extra.class_id} 的树里没有字段 {fname!r}",
                        hint="字段名只能来自类型树（default_fields(entry.nodes) 看有哪些）",
                    )
                efields[fname] = value
            fields_of[extra_id] = (ekey, efields)
            names_of[extra_id] = node.name

        go_fields["m_Component"] = [{"component": pptr(pid)} for pid in component_ids]

        fields_of[tr_id] = ((CLASS_TRANSFORM, None), tr_fields)
        fields_of[go_id] = ((CLASS_GAME_OBJECT, None), go_fields)
        names_of[tr_id] = node.name
        names_of[go_id] = node.name

        if parent_path:
            parent_tr = node_ids[parent_path][1]
            fields_of[parent_tr][1]["m_Children"].append(pptr(tr_id))

    if spec.lod_levels and lod_id is not None:
        root_path = walk[0][0]
        lod_fields = dict(template[(CLASS_LOD_GROUP, None)])
        lod_fields["m_GameObject"] = pptr(node_ids[root_path][0])
        lod_fields["m_Enabled"] = True
        lod_fields["m_Size"] = float(spec.lod_size if spec.lod_size is not None else 5.0)
        class_of = {pid: key[0] for pid, (key, _f) in fields_of.items()}
        heights = check_lod_heights(
            spec.lod_heights or lod_heights_for(len(spec.lod_levels))
        )
        if len(heights) != len(spec.lod_levels):
            raise AssetError(
                f"lod_heights 给了 {len(heights)} 个，lod_levels 有 {len(spec.lod_levels)} 档"
            )
        levels = []
        for level_index, members in enumerate(spec.lod_levels):
            renderers: list[dict[str, int]] = []
            for member in members:
                if member not in node_components:
                    raise AssetError(
                        f"LOD 里点名了不存在的节点 {member!r}",
                        hint=f"可选：{sorted(node_components)}",
                    )
                hits = [
                    pid for pid in node_components[member] if class_of.get(pid) in RENDERER_CLASSES
                ]
                if not hits:
                    raise AssetError(f"LOD 成员 {member!r} 上没有 renderer 组件")
                renderers.extend(pptr(pid) for pid in hits)
            levels.append(
                {
                    "screenRelativeHeight": heights[level_index],
                    "fadeTransitionWidth": 0.0,
                    "renderers": [{"renderer": ptr} for ptr in renderers],
                }
            )
        lod_fields["m_LODs"] = levels
        fields_of[lod_id] = ((CLASS_LOD_GROUP, None), lod_fields)
        names_of[lod_id] = walk[0][2].name
        node_components[root_path] = (*node_components[root_path], lod_id)

    if cursor != total:
        raise AssetError(f"pathID 分配对不上：用了 {cursor} 个 / 计划 {total} 个")

    # ---- ③ 序列化（每个对象：写 → 读 → 再写，三步必须一致）----
    objects: list[PrefabObject] = []
    for path_id, (key, fields) in fields_of.items():
        entry = entries[key]
        payload, _back = _serialize_and_check(entry.nodes, fields)
        objects.append(
            PrefabObject(
                path_id=path_id,
                class_id=entry.class_id,
                script_index=entry.script_index,
                type_id=entry.index,
                name=names_of[path_id],
                fields=fields,
                payload=payload,
                nodes=entry.nodes,
            )
        )

    build = PrefabBuild(
        root=spec,
        objects=tuple(objects),
        type_report=tuple(type_report),
        node_ids=node_ids,
        node_components=node_components,
        root_game_object=node_ids[walk[0][0]][0],
        root_transform=node_ids[walk[0][0]][1],
    )
    report = verify_hierarchy(
        build, extra_package_ids=extra_package_ids, script_path_ids=script_path_ids
    )
    if not report.ok:
        raise AssetError(
            "造出来的层级自证没过：\n" + "\n".join(report.problems[:10]),
            hint="这是本模块的核心判据（判据 b）；不许带着悬空 PPtr 出厂",
        )
    return build


# --------------------------------------------------------------------------- #
# 自证（判据 b）：读回来逐条对
# --------------------------------------------------------------------------- #
def verify_hierarchy(
    build: PrefabBuild,
    *,
    extra_package_ids: Iterable[int] = (),
    script_path_ids: Iterable[int] = (),
) -> HierarchyReport:
    """把造出来的对象**读回来**，逐条核：往返 / 组件回指 / 父子双向 / PPtr 无悬空。

    ⚠ 走的是 clone.read_object（产品读侧）+ clone.collect_pptrs（PPtr 收集器），
    不是写侧的内存状态 —— 与 N4「无悬空 PPtr」是同一条尺子。

    extra_package_ids 是「同一次写回里会一起进包、但不归本模块造」的对象
    （Mesh / Material / Texture）：N4 的判据是**整个包**无悬空，不是只看 prefab 这一块。
    ⛔ 默认空 —— 忘了传就会红，这正是这把尺子的正控。
    """
    # ⚠ `script_path_ids`：MonoBehaviour 的 m_Script 是 `{m_FileID: 0, m_PathID: <脚本 pathID>}`，
    # 而那个 pathID **不在对象表里**（由类型表的 script_types 解析）
    # ⇒ 不传就会被当悬空（2026-10-04 实测）。
    ids = (
        build.package_ids()
        | {int(v) for v in extra_package_ids if int(v) != 0}
        | {int(v) for v in script_path_ids if int(v) != 0}
    )
    by_id = build.by_id()
    problems: list[str] = []
    roundtrip_ok = 0
    components_total = 0
    backrefs_ok = 0
    transform_first = 0
    child_ok = child_total = father_ok = father_total = 0
    pptr_total = pptr_null = pptr_in = pptr_dangling = pptr_external = 0

    read_back: dict[int, dict[str, Any]] = {}
    for obj in build.objects:
        back = clone.read_object(obj.payload, obj.nodes)
        read_back[obj.path_id] = back
        if clone.write_object(back, obj.nodes) == obj.payload:
            roundtrip_ok += 1
        else:
            problems.append(f"对象 {obj.type_name} pathID={obj.path_id} 往返字节不同")
        for ref in clone.collect_pptrs(back, obj.nodes, owner=f"{obj.type_name}:{obj.path_id}"):
            pptr_total += 1
            if ref.file_id != 0:
                pptr_external += 1
                problems.append(f"跨文件 PPtr（本单口径不允许）：{ref}")
            elif ref.path_id == 0:
                pptr_null += 1
            elif ref.path_id in ids:
                pptr_in += 1
            else:
                pptr_dangling += 1
                problems.append(f"悬空 PPtr：{ref}")

    # 组件回指 + m_Component[0]
    for obj in build.objects:
        if obj.class_id != CLASS_GAME_OBJECT:
            continue
        back = read_back[obj.path_id]
        comps = [int(pair["component"]["m_PathID"]) for pair in back["m_Component"]]
        components_total += len(comps)
        first = by_id.get(comps[0]) if comps else None
        if first is not None and first.class_id == CLASS_TRANSFORM:
            transform_first += 1
        else:
            problems.append(f"GameObject {obj.name} 的 m_Component[0] 不是自己的 Transform")
        for pid in comps:
            comp = read_back.get(pid)
            if comp is None:
                problems.append(f"GameObject {obj.name} 的组件 {pid} 不在本次构建里")
                continue
            go_ref = comp.get("m_GameObject")
            if not isinstance(go_ref, dict) or int(go_ref["m_PathID"]) != obj.path_id:
                problems.append(f"组件 {pid} 的 m_GameObject 没回指 {obj.path_id}")
            else:
                backrefs_ok += 1

    # 父子双向
    for path, (_go_id, tr_id) in build.node_ids.items():
        back = read_back[tr_id]
        father = int(back["m_Father"]["m_PathID"])
        children = [int(p["m_PathID"]) for p in back["m_Children"]]
        father_total += 1
        # 期望父亲 = 路径上**最近**的那个祖先（最长前缀）
        ancestors = [
            (len(other_path), other_tr)
            for other_path, (_go, other_tr) in build.node_ids.items()
            if other_path != path and path.startswith(other_path + "/")
        ]
        expected_father = max(ancestors)[1] if ancestors else 0
        if father == expected_father:
            father_ok += 1
        else:
            problems.append(f"Transform {path} 的 m_Father={father} != 期望 {expected_father}")
        for child in children:
            child_total += 1
            if child not in read_back:
                child_ok -= 0  # 已有 problems
                problems.append(f"Transform {path} 的 m_Children 指向不存在的 {child}")
                continue
            if int(read_back[child]["m_Father"]["m_PathID"]) != tr_id:
                problems.append(f"m_Children 里 {child} 的 m_Father 没指回 {tr_id}")
            else:
                child_ok += 1

    # 每个子节点必须在父亲的 m_Children 里
    for path, (_go, tr_id) in build.node_ids.items():
        if "/" not in path:
            continue
        parent_path = path.rsplit("/", 1)[0]
        parent_tr = build.node_ids[parent_path][1]
        if tr_id not in [int(p["m_PathID"]) for p in read_back[parent_tr]["m_Children"]]:
            problems.append(f"{path} 不在父亲 {parent_path} 的 m_Children 里")

    return HierarchyReport(
        objects=len(build.objects),
        game_objects=sum(1 for o in build.objects if o.class_id == CLASS_GAME_OBJECT),
        transforms=sum(1 for o in build.objects if o.class_id == CLASS_TRANSFORM),
        roundtrip_ok=roundtrip_ok,
        component_backrefs_ok=backrefs_ok,
        components_total=components_total,
        transform_is_first=transform_first,
        child_links_ok=child_ok,
        child_links_total=child_total,
        father_links_ok=father_ok,
        father_links_total=father_total,
        pptr_total=pptr_total,
        pptr_null=pptr_null,
        pptr_in_package=pptr_in,
        pptr_dangling=pptr_dangling,
        pptr_external=pptr_external,
        problems=tuple(problems),
    )


# --------------------------------------------------------------------------- #
# 装配到 SerializedFile（借 builder 的序列化器，不重复实现）
# --------------------------------------------------------------------------- #
def serialize_prefab(
    build: PrefabBuild,
    entries: Mapping[TypeKey, builder_mod.RawTypeEntry],
    *,
    order: Sequence[TypeKey] | None = None,
    unity_version: str | None = None,
    target_platform: int | None = None,
    script_types: Sequence[Any] = (),
):
    """把这次构建装配成一个 SerializedFilePlan（type_id = order 里的下标）。

    ✅ 2026-10-04 起：一个包里可以装**多项同 classID 的树** ——
    每个对象按它在 order（或默认顺序）里的 **下标**当 type_id 传给 builder（对象元组的第 4 项）。
    真件 units 的 classID=114 有 22 项，
    所以「多个不同脚本的 MonoBehaviour」这条以前堵死的路现在是通的。
    """
    keys = (
        list(order)
        if order is not None
        else list(dict.fromkeys(type_key_of(o.class_id, o.script_index) for o in build.objects))
    )
    type_entries = [entries[key] for key in keys]
    # 每个对象用它自己那个 key 在 keys 里的**下标**当 type_id（第 4 项）⇒ 同 classID 多项也能装。
    index_of = {key: i for i, key in enumerate(keys)}
    rows = [
        (o.path_id, o.class_id, o.payload, index_of[type_key_of(o.class_id, o.script_index)])
        for o in build.objects
    ]
    kwargs: dict[str, Any] = {"type_entries": type_entries, "objects": rows}
    if unity_version is not None:
        kwargs["unity_version"] = unity_version
    if target_platform is not None:
        kwargs["target_platform"] = target_platform
    if script_types:
        kwargs["script_types"] = script_types
    elif any(
        entry.wire is not None and entry.wire.script_type_index >= 0 for entry in type_entries
    ):
        raise AssetError(
            "照抄的类型表里有 script_type_index>=0 的项，但没给 script_types",
            hint="从源包 RawTypeTable.script_types() 一起抄（serialize_serialized_file 会断言）",
        )
    return builder_mod.serialize_serialized_file(**kwargs)


# --------------------------------------------------------------------------- #
# MonoBehaviour 那条缝：量清楚「按 script_index 选对那一项」
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MonoBehaviourSeam:
    """units 包 22 项 MonoBehaviour 的读数 + 「还缺什么才能造」。"""

    entries: tuple[tuple[int, int, int], ...]  # (type_id, script_index, node_count)
    ordered_by_script_index: bool
    duplicate_class_id_blocked: bool
    missing: tuple[str, ...]

    def lines(self) -> tuple[str, ...]:
        out = [
            f"MonoBehaviour 类型表项 {len(self.entries)} 个（type_id, script_index, 节点数）："
            + "，".join(f"({t},{s},{n})" for t, s, n in self.entries),
            f"项号顺序 == script_index 顺序？{'是' if self.ordered_by_script_index else '否'}"
            " ⇒ 按 classID 取一项必取错，只能 entry_for(114, script_index=k)",
            "同一个包里装不下两项 classID=114 的树"
            f"（builder 按 classID 索引 type_id）：{self.duplicate_class_id_blocked}",
        ]
        out.extend(f"  还缺：{item}" for item in self.missing)
        return tuple(out)


def mono_behaviour_seam(table: builder_mod.RawTypeTable) -> MonoBehaviourSeam:
    """量 units（或任何真包）里 MonoBehaviour 的类型表布局，并把「还缺什么」写清楚。"""
    rows = table.entries_for_class(CLASS_MONO_BEHAVIOUR)
    entries = tuple((e.index, e.script_index, len(e.nodes)) for e in rows)
    by_script = tuple(sorted(entries, key=lambda row: row[1]))
    return MonoBehaviourSeam(
        entries=entries,
        ordered_by_script_index=(entries == by_script),
        duplicate_class_id_blocked=len(rows) > 1,
        missing=(
            "m_Script 指向的 MonoScript 对象：源包里它在 externals（fileIndex）"
            "与 script_types（pathID）里；自造包要么把 MonoScript 对象搬进本包"
            "（连带 pathID 重映射），要么把源包 externals 抄进来",
            "每个脚本的字段默认值：22 棵树字段各不相同（12~152 个节点），"
            "要逐脚本给值才能构造合法对象",
            "ref types 里才有的 class/namespace/assembly 三个 cstr：类型表项里没有，"
            "抄类型表不足以让游戏认出一个 MonoBehaviour 的托管类型",
        ),
    )