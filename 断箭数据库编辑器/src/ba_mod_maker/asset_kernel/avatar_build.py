r"""W4 · Avatar（classID **90**）解析 / 复制 / 人形判定 —— 以真件为底。

## 这一模块解决什么

给「新增一个单位模型包」补齐 **Avatar 那一块**：真件里的 Avatar 对象能不能读、能不能
**原样搬**进我们自己的包、以及**怎么机械地判它是人形还是 generic**。

    AVATAR_TOP_KEYS                 真件 16/16 一致的顶层键（键集合本身就是判据之一）
    read_avatar(payload, nodes)     全部读数（键集合 / 各 vector 长度 / 哈希）+ 往返自证
    classify_avatar(facts)          humanoid / generic（判据见下，**只有一条**）
    copy_avatar(...)                复制成 builder.ObjectSpec（照搬字节 + PPtr 可重映射）
    diff_avatars(a, b)              两条读数的**对拍**（给 W1 的 Unity 生成 Avatar 用）
    from_unity_avatar(fields)       ⛔ 入口留着，现在抛 NotImplementedError（等 W1 的产物）

## 判据：人形 / generic（**2026-10-08 实测裁定**，Lead 定的口径）

    kind == humanoid  ⟺  m_HumanDescription.m_Human 非空

**为什么不是「m_HumanDescription / m_TOS / m_Skeleton 三者任一非空」**（字面口径，已否决）——
真件 units 里 16 个 Avatar 的完整对照表（复算：_work/avatar-line/p2_avatars.py，
夹具 _work/avatar-line/fixtures/avatars_index.json）：

| Avatar | m_Human（人骨） | m_Skeleton（骨） | m_TOS | m_Avatar.m_AvatarSkeleton.m_Node |
|---|---|---|---|---|
| US_SoldierAvatar | 38 | 65 | 65 | 65 |
| US_ForceReconAvatar · US_PARARESCUEMENAvatar · Driver_GunnerAvatar | 40 | 44 | 44 | 44 |
| US_PlaceholderAvatar · US_RangerAvatar · US_ScoutSniperAvatar | 40 | 44 | 44 | 44 |
| US_RangerSniperAvatar · US_MarineAvatar · US_RangerRRCAvatar | 40 | 44 | 44 | 44 |
| US_DELTA_FORCEAvatar · US_Army_EngineerAvatar · US_ArmyAvatar | 40 | 44 | 44 | 44 |
| US_Marine_RaiderAvatar | 30 | 56 | 56 | 56 |
| AvatarAvatar | 40 | 42 | 42 | 42 |
| **WoodenBoxAvatar** | **0** | **0** | **1** | **1** |

两条**可复算**的读数把这套口径钉死（16/16 无例外）：

1. **m_TOS 不是人形标志**：它的条数**恒等于** m_Avatar.m_AvatarSkeleton.m_Node 的节点数
   （65/65、44/44、42/42、56/56、1/1）⇒ 它是「骨架节点 id → 路径名」的映射表，
   **generic 的 WoodenBoxAvatar 也有 1 条**（元素形如 {first: <uint>, second: "<路径>"}）。
   ⇒ 把它算进判据，WoodenBoxAvatar 会被判成人形 —— **字面口径（三取一非空）已否决**。
2. **m_Skeleton 非空与 m_Human 非空在真件里同真同假**（这一列 16/16 与 m_Human 一致）
   ⇒ 它可以当**交叉校验**（AvatarKind.cross_check_ok），但**不单独作为判据**。

⇒ 判据只用 m_Human：真件 16/16 里 **15 个人形 + 1 个 generic（WoodenBoxAvatar）**，
用例拿这两个当**正负控**（⛔ 不许只用「能跑通」当绿）。

## m_AvatarSize 是**未定**语义（如实记，⛔ 不许猜）

m_AvatarSize（unsigned int）**不是** m_Avatar 字段在文件里的序列化字节数 ——
实测 **0/16 相等**（例：US_RangerAvatar 声明 12,524 / 实测 10,260；
WoodenBoxAvatar 声明 500 / 实测 620）。
复算：_work/avatar-line/p5_extent.py（用「前缀子树长度差」量每个顶层字段的字节宽度，
16/16 前缀和 == 对象长度 ⇒ 这把尺子自洽）。

★ **实测 0/16 相等 ⇒ 语义未定 ⇒ 只搬不算**（Lead 2026-10-08 批准的口径）：
复制时 m_AvatarSize **原样照搬、绝不重算**；读数里 declared_size（声明）
与 avatar_blob_length（实测）**两个都给**，谁都不许拿一个冒充另一个。
⛔ **后来者别「顺手把它修对」** —— 这一条是**负读数**：真件 16/16 都不相等，
说明它不是那个东西；按「它应当等于序列化长度」去改，改出来的包与真件**不同形**。

## 真件实测口径（2026-10-08，units 包 162,010 对象里的 16 个 Avatar）

* **顶层键集合 16/16 一致**，一个不多一个不少：m_Name / m_AvatarSize / m_Avatar /
  m_TOS / m_HumanDescription（**没有 m_GameObject** —— 类型树里就没有这一项）；
* **PPtr = 0 条**（16/16）⇒ Avatar 不引用包里任何其它对象；m_TOS 里的哈希是**骨架节点名**
  的哈希，不是对象引用（所以「PPtr 重映射」对真件 Avatar 是恒等变换，但机制必须留住：
  见 remap_avatar_pptrs，用例里有**植入一条 PPtr 的正控**）；
* **往返逐字节 16/16**（clone.read_object → clone.write_object == 原 payload）
  ⇒ Avatar **能被字段树读侧完整覆盖**，不必走 payload-only 那条看不清引用的路；
* **载具不需要 Avatar**：验收合同 L1 实测「载具的 Animator 一律 m_Avatar={0,0}」
  （T-72B/T-90）。本模块把它写成一条尺子（animator_avatar_pptr / is_null_pptr）：
  真件 430 个 Animator 里 **204 个是 {0,0}**，其余分别指向这 16 个之一
  （复算在 _work/avatar-line/avatars_report.txt）。

## 边界（如实写，别让它变成假绿）

1. **本模块只做「复制 + 判定」，不做「从零造人形 Avatar」** —— 人形 Avatar 的
   m_Avatar.m_Human（人骨索引/质量/关节限位）是 Unity 的 AvatarBuilder 从**人形 rig**
   算出来的，⛔ 手写等于猜。自造那条路走 W1（Unity 里生成）⇒ from_unity_avatar 是接缝。
   ★ 依据：rg -n "AvatarBuilder" _work/isil/IsilDump → **0 命中** ⇒ 生成算法**纯原生**，
   托管侧没有可读实现（Lead 2026-10-08 复核，与 _work/avatar-line/结论.md §8 同一条）。
2. **builder.dangling_pptrs 只普查有字段树的对象**：本模块默认走 fields（有树、能普查），
   mode="payload" 那条路没有树 ⇒ 悬空判据失效，只有在你**明确**要字节级照搬时才用。
3. **m_HumanDescription.m_Human 空 = generic 这条是**分类**，不是**合法性**判据** ——
   generic Avatar 完全合法（WoodenBoxAvatar 就在真件里跑着）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "AVATAR_CONSTANT_KEYS",
    "AVATAR_TOP_KEYS",
    "CLASS_ANIMATOR",
    "CLASS_AVATAR",
    "HUMAN_DESCRIPTION_KEYS",
    "KIND_GENERIC",
    "KIND_HUMANOID",
    "AvatarFacts",
    "AvatarKind",
    "animator_avatar_pptr",
    "avatar_blob_bytes",
    "avatar_facts",
    "classify_avatar",
    "copy_avatar",
    "diff_avatars",
    "field_widths",
    "from_unity_avatar",
    "is_null_pptr",
    "key_set_report",
    "read_avatar",
    "remap_avatar_pptrs",
]

#: Avatar 的 classID（Unity 内置类，**不带脚本** ⇒ 类型表只有一项）。
CLASS_AVATAR = 90
#: Animator 的 classID —— 它才是「谁在用 Avatar」的那一头（m_Avatar 是 PPtr）。
CLASS_ANIMATOR = 95

#: 真件 16/16（units 的 16 个 Avatar）一致的**顶层键**，顺序也一致。
#: ⛔ 不许加也不许减：键集合本身就是本模块的第一条判据。
AVATAR_TOP_KEYS: tuple[str, ...] = (
    "m_Name",
    "m_AvatarSize",
    "m_Avatar",
    "m_TOS",
    "m_HumanDescription",
)

#: m_Avatar（类型树里是 **AvatarConstant**，不是不透明 blob）的 12 个键 —— 真件 16/16 一致。
AVATAR_CONSTANT_KEYS: tuple[str, ...] = (
    "m_AvatarSkeleton",
    "m_AvatarSkeletonPose",
    "m_DefaultPose",
    "m_Human",
    "m_HumanSkeletonIndexArray",
    "m_HumanSkeletonReverseIndexArray",
    "m_RootMotionBoneIndex",
    "m_RootMotionBoneX",
    "m_RootMotionSkeleton",
    "m_RootMotionSkeletonIndexArray",
    "m_RootMotionSkeletonPose",
    "m_SkeletonNameIDArray",
)

#: m_HumanDescription（HumanDescription）的 14 个键 —— 真件 16/16 一致。
HUMAN_DESCRIPTION_KEYS: tuple[str, ...] = (
    "m_Human",
    "m_Skeleton",
    "m_ArmTwist",
    "m_ForeArmTwist",
    "m_UpperLegTwist",
    "m_LegTwist",
    "m_ArmStretch",
    "m_LegStretch",
    "m_FeetSpacing",
    "m_GlobalScale",
    "m_RootMotionBoneName",
    "m_HasTranslationDoF",
    "m_HasExtraRoot",
    "m_SkeletonHasParents",
)

KIND_HUMANOID = "humanoid"
KIND_GENERIC = "generic"


# --------------------------------------------------------------------------- #
# 尺子 1：顶层字段的**字节宽度**（含尾部对齐）—— 用它切出 m_Avatar 的内部 blob
# --------------------------------------------------------------------------- #
def _top_level_spans(nodes: Sequence[clone.TypeNode]) -> dict[str, tuple[clone.TypeNode, ...]]:
    """把一条类型树按 **level == 1** 切成「每个顶层字段一棵子树」。"""
    spans: dict[str, tuple[clone.TypeNode, ...]] = {}
    for index, node in enumerate(nodes):
        if node.level != 1:
            continue
        end = len(nodes)
        for probe in range(index + 1, len(nodes)):
            if nodes[probe].level <= 1:
                end = probe
                break
        spans[node.name] = tuple(nodes[index:end])
    return spans


def _sub_tree(nodes: Sequence[clone.TypeNode], names: Sequence[str]) -> tuple[clone.TypeNode, ...]:
    """root + 这些顶层字段 拼成一条**可单独序列化**的树（字段顺序 = names 的顺序）。"""
    if not nodes:
        raise AssetError("空类型树", hint="classID=90 的类型树没读到（enable_type_tree 关着？）")
    spans = _top_level_spans(nodes)
    missing = [name for name in names if name not in spans]
    if missing:
        raise AssetError(
            f"classID=90 的类型树里没有这些顶层字段：{missing}",
            hint=f"真件 16/16 的顶层键就是 {list(AVATAR_TOP_KEYS)}；树不一样先查包，别猜",
        )
    out: list[clone.TypeNode] = [nodes[0]]
    for name in names:
        out.extend(spans[name])
    return tuple(out)


def field_widths(
    fields: Mapping[str, Any], nodes: Sequence[clone.TypeNode]
) -> tuple[tuple[str, int], ...]:
    """对象字节里每个**顶层字段**各占多少字节（含各自尾部对齐）。

    做法（可复算）：按 AVATAR_TOP_KEYS 的顺序拼「前缀子树」逐个序列化，取长度差。
    **自证**：最后一条前缀的长度必须 == 整对象长度，否则抛 —— 拼法错了就不许出数
    （这条断言就是本函数的负控：漏一个字段 / 树拿错类，它立刻红）。

    为什么需要它：m_Avatar 不是不透明 blob，类型树里是 AvatarConstant；
    要给出「内部 blob 的长度与哈希」，只能靠字节宽度把它**切出来**。
    ⚠ 实测 m_AvatarSize **不等于**这个宽度（0/16，见模块头）⇒ 两个读数都要给，别互相冒充。
    """
    widths: list[tuple[str, int]] = []
    previous = 0
    for count in range(1, len(AVATAR_TOP_KEYS) + 1):
        names = AVATAR_TOP_KEYS[:count]
        sub = {name: fields[name] for name in names}
        size = len(clone.write_object(sub, _sub_tree(nodes, names)))
        widths.append((names[-1], size - previous))
        previous = size
    total = len(clone.write_object(dict(fields), nodes))
    if previous != total:
        raise AssetError(
            f"前缀拼法不自洽：前缀 {previous:,} B != 整对象 {total:,} B",
            hint="顶层键集合与 AVATAR_TOP_KEYS 不一致 —— 先对键集合，别拿这个宽度当依据",
        )
    return tuple(widths)


def avatar_blob_bytes(fields: Mapping[str, Any], nodes: Sequence[clone.TypeNode]) -> bytes:
    """把 m_Avatar 字段在对象字节里占的那一段**原样切出来**（AvatarConstant 的内部 blob）。"""
    head = AVATAR_TOP_KEYS[:2]
    up_to = AVATAR_TOP_KEYS[:3]
    full = clone.write_object(dict(fields), nodes)
    before = len(clone.write_object({n: fields[n] for n in head}, _sub_tree(nodes, head)))
    after = len(clone.write_object({n: fields[n] for n in up_to}, _sub_tree(nodes, up_to)))
    return full[before:after]


# --------------------------------------------------------------------------- #
# 尺子 2：读数表
# --------------------------------------------------------------------------- #
def _seq_len(value: Any) -> int:
    """vector / staticvector / map 的长度。

    clone 解出来的形状**三种都有**（实测，别用眼睛猜）：
      * m_TOS（map）→ **list**[{first, second}]，长度直接 len；
      * m_Human（vector<HumanBone>）→ list；
      * 有的 map 形状是 {'first': [...], 'second': [...]}（见 kb/16 §2.1 的 m_Container）。
    """
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, Mapping):
        if set(value) == {"first", "second"}:
            return len(value["first"])
        return len(value)
    return -1


def _sort_keys(keys: Any) -> tuple[str, ...]:
    return tuple(sorted(str(k) for k in keys))


@dataclass(frozen=True, slots=True)
class AvatarKind:
    """人形 / generic 的判定结果 —— **三个数一起返回**，谁都能不开包复算这个判定。"""

    kind: str
    human_bones: int
    skeleton_bones: int
    tos_entries: int
    skeleton_nodes: int

    @property
    def is_humanoid(self) -> bool:
        return self.kind == KIND_HUMANOID

    @property
    def literal_rule_kind(self) -> str:
        """**已被否决**的字面口径（m_HumanDescription / m_TOS / m_Skeleton 三取一非空）会给出什么。

        留着它是为了让「否决」这件事**可被机械复算**：真件 16/16 里它只有一个分歧点
        （WoodenBoxAvatar：m_TOS=1 ⇒ 它说 humanoid，而裁定是 generic）。
        """
        return (
            KIND_HUMANOID
            if (self.human_bones or self.skeleton_bones or self.tos_entries)
            else KIND_GENERIC
        )

    @property
    def cross_check_ok(self) -> bool:
        """真件 16/16：m_Skeleton 非空与 m_Human 非空**同真同假**（交叉校验，不单独当判据）。"""
        return bool(self.skeleton_bones) == bool(self.human_bones)

    def line(self) -> str:
        return (
            f"{self.kind}（人骨 {self.human_bones} / 骨 {self.skeleton_bones} / "
            f"TOS {self.tos_entries} / 骨架节点 {self.skeleton_nodes}）"
        )


@dataclass(frozen=True, slots=True)
class AvatarFacts:
    """一个 Avatar 的全部读数（键集合 + 各 vector 长度 + 哈希 + 往返）。"""

    path_id: int
    name: str
    object_size: int
    declared_size: int
    avatar_blob_length: int
    avatar_blob_sha256: str
    payload_sha256: str
    roundtrip_equal: bool
    top_keys: tuple[str, ...]
    avatar_keys: tuple[str, ...]
    human_description_keys: tuple[str, ...]
    skeleton_nodes: int
    skeleton_ids: int
    skeleton_axes: int
    skeleton_pose: int
    default_pose: int
    root_motion_nodes: int
    name_id_array: int
    human_skeleton_index: int
    human_skeleton_rev_index: int
    root_motion_bone_index: int
    human_bones: int
    skeleton_bones: int
    tos_entries: int
    human_bone_index: int
    human_bone_mass: int
    has_left_hand: bool
    has_right_hand: bool
    has_tdof: bool
    pptr_count: int

    @property
    def kind(self) -> AvatarKind:
        return classify_avatar(self)

    @property
    def keys_are_canonical(self) -> bool:
        """顶层键集合与真件 16/16 的口径一致（名字集合，排序后比）。"""
        return self.top_keys == _sort_keys(AVATAR_TOP_KEYS)

    def line(self) -> str:
        return (
            f"pathID {self.path_id} · {self.name!r} · 对象 {self.object_size:,} B · "
            f"m_Avatar {self.avatar_blob_length:,} B（声明 {self.declared_size:,}） · "
            f"{self.kind.line()} · PPtr {self.pptr_count} · 往返 "
            f"{'OK' if self.roundtrip_equal else '★不一致'}"
        )


def avatar_facts(
    fields: Mapping[str, Any],
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int = 0,
    payload: bytes | None = None,
) -> AvatarFacts:
    """从**已解码的字段树**出读数表。nodes 必须是 classID=90 的类型树（entry_for(90).nodes）。

    键集合按 AVATAR_TOP_KEYS **对不上就抛** —— 不许在键集合不对的树上出数（那正是猜）。
    """
    got = set(fields)
    want = set(AVATAR_TOP_KEYS)
    if got != want:
        raise AssetError(
            f"顶层键集合与真件口径不一致：缺 {sorted(want - got)} / 多 {sorted(got - want)}",
            hint=f"真件 16/16 就是 {list(AVATAR_TOP_KEYS)}；树或对象拿错了",
        )
    avatar = fields["m_Avatar"]
    human_desc = fields["m_HumanDescription"]
    human_data = avatar["m_Human"]["data"]
    skeleton = avatar["m_AvatarSkeleton"]["data"]
    skeleton_pose = avatar["m_AvatarSkeletonPose"]["data"]
    default_pose = avatar["m_DefaultPose"]["data"]
    root_motion = avatar["m_RootMotionSkeleton"]["data"]
    blob = avatar_blob_bytes(fields, nodes)
    again = clone.write_object(dict(fields), nodes)
    raw = bytes(payload) if payload is not None else again
    return AvatarFacts(
        path_id=int(path_id),
        name=str(fields["m_Name"]),
        object_size=len(raw),
        declared_size=int(fields["m_AvatarSize"]),
        avatar_blob_length=len(blob),
        avatar_blob_sha256=hashlib.sha256(blob).hexdigest(),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        roundtrip_equal=bool(again == raw),
        top_keys=_sort_keys(fields),
        avatar_keys=_sort_keys(avatar),
        human_description_keys=_sort_keys(human_desc),
        skeleton_nodes=_seq_len(skeleton["m_Node"]),
        skeleton_ids=_seq_len(skeleton["m_ID"]),
        skeleton_axes=_seq_len(skeleton["m_AxesArray"]),
        skeleton_pose=_seq_len(skeleton_pose["m_X"]),
        default_pose=_seq_len(default_pose["m_X"]),
        root_motion_nodes=_seq_len(root_motion["m_Node"]),
        name_id_array=_seq_len(avatar["m_SkeletonNameIDArray"]),
        human_skeleton_index=_seq_len(avatar["m_HumanSkeletonIndexArray"]),
        human_skeleton_rev_index=_seq_len(avatar["m_HumanSkeletonReverseIndexArray"]),
        root_motion_bone_index=int(avatar["m_RootMotionBoneIndex"]),
        human_bones=_seq_len(human_desc["m_Human"]),
        skeleton_bones=_seq_len(human_desc["m_Skeleton"]),
        tos_entries=_seq_len(fields["m_TOS"]),
        human_bone_index=_seq_len(human_data["m_HumanBoneIndex"]),
        human_bone_mass=_seq_len(human_data["m_HumanBoneMass"]),
        has_left_hand=bool(human_data["m_HasLeftHand"]),
        has_right_hand=bool(human_data["m_HasRightHand"]),
        has_tdof=bool(human_data["m_HasTDoF"]),
        pptr_count=sum(1 for _ in builder.iter_pptrs(fields)),
    )


def classify_avatar(facts: AvatarFacts) -> AvatarKind:
    """判 humanoid / generic —— **判据只有一条**：m_HumanDescription.m_Human 非空。

    为什么不是「三取一非空」：见模块头那张 16/16 的表（m_TOS 是骨架节点 id→路径名 的映射，
    generic 的 WoodenBoxAvatar 也有 1 条 ⇒ 字面口径已否决）。
    """
    return AvatarKind(
        kind=KIND_HUMANOID if facts.human_bones > 0 else KIND_GENERIC,
        human_bones=facts.human_bones,
        skeleton_bones=facts.skeleton_bones,
        tos_entries=facts.tos_entries,
        skeleton_nodes=facts.skeleton_nodes,
    )


def read_avatar(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int = 0,
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
    verify_roundtrip: bool = True,
) -> AvatarFacts:
    """读一个 Avatar 的**对象字节**，出读数表；默认**自证往返逐字节**。

    verify_roundtrip=True 时，往返不逐字节相等就抛 AssetError
    —— 真件 16/16 都相等（_work/avatar-line/p2_avatars.py），不相等说明读侧没覆盖全。
    """
    raw = bytes(payload)
    fields = clone.read_object(raw, nodes, ref_types=ref_types)
    facts = avatar_facts(fields, nodes, path_id=path_id, payload=raw)
    if verify_roundtrip and not facts.roundtrip_equal:
        raise AssetError(
            f"Avatar pathID={path_id} 往返**不逐字节相等**",
            hint="读侧没覆盖全（字段树 / 类型树对不上）；这种字节不要写回",
        )
    return facts


# --------------------------------------------------------------------------- #
# 复制：payload 照搬 + PPtr 重映射
# --------------------------------------------------------------------------- #
def remap_avatar_pptrs(fields: Mapping[str, Any], mapping: Mapping[int, int]) -> dict[str, Any]:
    """按「旧 pathID → 新 pathID」重写字段树里 m_FileID == 0 的 PPtr。

    口径**只有一份**（builder.remap_pptrs）：映射表里没有的 pathID **原样保留**，
    ⛔ 不静默改成 0 —— 漏映射要由 builder.dangling_pptrs 抓出来。

    真件实测：**16/16 个 Avatar 的 PPtr 都是 0 条** ⇒ 对真件这是恒等变换。
    机制仍然留住，因为 ① 那是 W1 的 Unity 产物可能带引用；② 用例里有「植入一条 PPtr」的正控。
    """
    return builder.remap_pptrs(dict(fields), mapping)


def copy_avatar(
    payload: bytes,
    nodes: Sequence[clone.TypeNode],
    *,
    path_id: int,
    mapping: Mapping[int, int] | None = None,
    mode: str = "fields",
    ref_types: Mapping[tuple[str, str, str], Sequence[clone.TypeNode]] | None = None,
    label: str = "",
) -> builder.ObjectSpec:
    """把一个真件 Avatar 复制成我们包里的 builder.ObjectSpec。

    mode="fields"（默认）：解码 → 重映射 → 重编码，并**自证与原字节逐字节相等**
        （真件 16/16 成立）。这条路的好处：对象**有字段树** ⇒
        builder.dangling_pptrs 普查得到它，PPtr 也能真重映射。
    mode="payload"：**原样搬字节**（ObjectSpec.payload），不解码。
        ⚠ 这条路没有字段树 ⇒ 悬空判据对它是失效的（模块头边界 2）。
        mapping 非空时**抛** —— 字节照搬与重映射不能同真，不许偷偷二选一。

    ⚠ m_AvatarSize **原样照搬，绝不重算**（它的语义未定，实测 ≠ m_Avatar 的序列化长度，0/16）。
    """
    if mode not in {"fields", "payload"}:
        raise AssetError(f"mode={mode!r} 不认", hint='只有 "fields" 与 "payload"')
    raw = bytes(payload)
    if mode == "payload":
        if mapping:
            raise AssetError(
                "mode='payload' 与 mapping 不能同时给",
                hint="字节照搬时 PPtr 改不了；要重映射就用 mode='fields'（它会自证逐字节相等）",
            )
        return builder.ObjectSpec(
            class_id=CLASS_AVATAR,
            fields=None,
            path_id=int(path_id),
            payload=raw,
            label=label or "avatar",
        )
    fields = clone.read_object(raw, nodes, ref_types=ref_types)
    if mapping:
        fields = remap_avatar_pptrs(fields, mapping)
    again = clone.write_object(fields, nodes)
    if again != raw:
        raise AssetError(
            f"复制出来的字节与原 payload 不相等（pathID={path_id}）",
            hint=f"原 {len(raw):,} B / 新 {len(again):,} B —— 读侧没覆盖全，别照搬",
        )
    return builder.ObjectSpec(
        class_id=CLASS_AVATAR,
        fields=fields,
        path_id=int(path_id),
        label=label or "avatar",
    )


# --------------------------------------------------------------------------- #
# Animator 那一头：载具的 m_Avatar 是 {0,0}
# --------------------------------------------------------------------------- #
def animator_avatar_pptr(fields: Mapping[str, Any]) -> tuple[int, int]:
    """取 Animator 的 m_Avatar PPtr，返回 (m_FileID, m_PathID)。

    ⛔ 不许猜字段名：这两个键来自 units 包里 classID=95 的内嵌类型树
    （复算：_work/avatar-line/p2_avatars.py，430 个 Animator 顶层键样例里有 m_Avatar）。
    """
    if "m_Avatar" not in fields:
        raise AssetError(
            "这个对象的字段树里没有 m_Avatar（不是 Animator？）",
            hint="classID=95 的 Animator；键名不许猜",
        )
    ref = fields["m_Avatar"]
    if not isinstance(ref, Mapping) or set(ref) != {"m_FileID", "m_PathID"}:
        raise AssetError(f"m_Avatar 不是 PPtr 形状：{ref!r}")
    return int(ref["m_FileID"]), int(ref["m_PathID"])


def is_null_pptr(ref: tuple[int, int]) -> bool:
    """{0, 0} = 空引用。★ 验收合同 L1 实测：**载具的 Animator 一律 m_Avatar={0,0}**
    （T-72B/T-90）⇒ **载具不需要 Avatar**，本模块（对载具那条线）不是必需品。"""
    return tuple(int(x) for x in ref) == (0, 0)


# --------------------------------------------------------------------------- #
# 对账 / 对拍
# --------------------------------------------------------------------------- #
def key_set_report(items: Sequence[AvatarFacts]) -> tuple[str, ...]:
    """键集合对账读数：每个 Avatar 的顶层键 + 「与真件口径一致」的条数。"""
    lines = [
        f"顶层键 {'一致' if f.keys_are_canonical else '★不一致'} · pathID {f.path_id} · {f.name!r}"
        for f in items
    ]
    ok = sum(1 for f in items if f.keys_are_canonical)
    lines.append(f"键集合一致 {ok}/{len(items)}（口径 = {list(AVATAR_TOP_KEYS)}）")
    return tuple(lines)


def diff_avatars(left: AvatarFacts, right: AvatarFacts) -> tuple[str, ...]:
    """两条 Avatar 读数的**对拍** —— 给 W1（Unity 生成）与我们搬的真件比。

    比这些（都是可复算的量）：**顶层键集合 / m_Avatar 与 m_HumanDescription 的键集合 /
    m_Avatar 内部 blob 的长度与哈希 / m_AvatarSize / 人形判定 / PPtr 条数**。
    空元组 = 逐项一致。
    """
    out: list[str] = []
    if left.top_keys != right.top_keys:
        out.append(f"顶层键不同：{left.top_keys} vs {right.top_keys}")
    if left.avatar_keys != right.avatar_keys:
        out.append(f"m_Avatar 键不同：{left.avatar_keys} vs {right.avatar_keys}")
    if left.human_description_keys != right.human_description_keys:
        out.append(
            f"m_HumanDescription 键不同："
            f"{left.human_description_keys} vs {right.human_description_keys}"
        )
    if left.avatar_blob_length != right.avatar_blob_length:
        out.append(
            f"m_Avatar 长度不同：{left.avatar_blob_length:,} vs {right.avatar_blob_length:,}"
        )
    if left.avatar_blob_sha256 != right.avatar_blob_sha256:
        out.append(
            f"m_Avatar 哈希不同：{left.avatar_blob_sha256[:16]} vs {right.avatar_blob_sha256[:16]}"
        )
    if left.declared_size != right.declared_size:
        out.append(f"m_AvatarSize 不同：{left.declared_size:,} vs {right.declared_size:,}")
    if left.kind.kind != right.kind.kind:
        out.append(f"人形判定不同：{left.kind.line()} vs {right.kind.line()}")
    if left.pptr_count != right.pptr_count:
        out.append(f"PPtr 条数不同：{left.pptr_count} vs {right.pptr_count}")
    return tuple(out)


#: W1 的 Unity 产物**还没到**（2026-10-08）：键集合 / m_Avatar 长度 / 哈希一个都还没量过。
_UNITY_PENDING = (
    "等 W1 的产物（Unity 2022.3.62f3c1 构建的 AssetBundle 里的 Avatar 对象；"
    "⚠ 那是**旧代**读数 —— 2026-10-17 起默认底座 = 6000.3.24f1，见 kb/42 §4 C11/C13）。"
    "拿到真字节再比：顶层键集合 / m_Avatar 内部 blob 的长度与哈希 / 人形判定 / PPtr 条数；"
    "⛔ 在此之前不许按「Unity 生成的应该是……」猜任何字段名或长度。"
)


def from_unity_avatar(fields: Mapping[str, Any]) -> AvatarFacts:
    """吃 **W1 在 Unity 里生成的** Avatar 的字段树，出同一张读数表（对拍用）。

    ⛔ **现在抛 NotImplementedError**：W1 的产物还没交付，键集合与 blob 长度**一个都没量过**。
    真件那一侧的口径是从 16 个真件对象上量出来的（AVATAR_TOP_KEYS 等）；
    Unity 侧的类型树是不是同一份、m_AvatarSize 是不是同一个语义 —— 都要**先量再说**。
    """
    raise NotImplementedError(f"from_unity_avatar 尚未接线：{_UNITY_PENDING}")
