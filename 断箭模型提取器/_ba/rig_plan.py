r"""补骨架 · **纯逻辑**（bpy-free）：折出「该建哪些骨、放在哪、谁是谁的爹」。

## 这条路解决什么

导入侧原来只把权重搬进**顶点组**，于是（用户 2026-10-12 亲手验到）：

* `Chassis` 上 38 个顶点组都在、权重一个不差 —— 但**场景里没有骨架对象**、
  网格上**没有任何修改器** ⇒ 用户按 `G` 拖 `root` / `body` 空物体，**车纹丝不动**；
* 用户当场那句「**这个 Blender 插件实际上没有做完？**」问的就是这一段。

本模块只产出**纯数据**（该建哪些骨 / 每根骨的头尾 / 父是谁 / 谁参与形变），
建 bpy 骨架那一步在 `blender_addon/armature.py`，接线在 `importer.import_unit`。

## 三条硬口径（都有实测支撑）

1. **骨名 = 顶点组名，逐字相同**。Armature 修改器就是按**名字**把顶点组接到骨上的：
   差一个字符 = 那根骨**静默不生效**（不报错，只是那块不动）。⛔ Blender 给重名会自动加
   `.001` —— 所以建完骨**必须回读名字核对**（`armature.build_armature` 里 fail-closed）。
2. **rest 姿势 = prefab 现在摆的这个姿势**（不是另算一份"绑定姿势"）。
   实测（`_work/rig-recon2.py`）：`W_i · m_BindPose[i] == 单位阵`，
   RU_T72B 的 **29 个真槽全部命中，最大偏差 7.36e-07**（float32 级）
   ⇒ **绑定姿势就是 prefab 摆的那个姿势** ⇒ 把 rest 摆成节点世界变换时，
   蒙皮在该姿势下**恒等**（顶点位移 0，实测 2.98e-09）。这就是"照抄"能成立的全部依据。
   ⚠ 反面：把 `m_BindPose` 抄进物体当绑定姿势是**另一条路**（见 `mesh_build.identity_bind_pose`
   那段教训）—— 本模块**不动** `m_BindPose`，只读 prefab 的节点变换。
3. **9 个空槽不建骨，但槽号一位都不许动**：38 槽里 9 个是 `m_PathID == 0` 的保留位
   （炮塔位），没有节点、也没有任何顶点绑在它们上面 ⇒ 建不出、也不必建。
   `RigPlan.slot_names` **原样保留 38 项**（下标 = 槽号，写回时就是它）。

## 多出来的几根"非槽骨"

`root`（以及别的、把真槽连起来的祖先节点）也建成骨，`deform=False`：
Unity 里骨槽节点的**父链**决定"转一根骨、它底下那串跟不跟着动"。
实测链是 `root → body → {LR000, RR000, LR007, RR007}` 与 `root → L00x → LR00x`
—— 少了 `root`，转 `L003` 时 `LR003`（它的子节点）**不会跟着走**，与游戏不符。

## 判据一行

    .venv\Scripts\python.exe -m pytest tests\test_blender_rig.py -q -n 0
      -> 真件抽件（`_work/w10_ref`，⛔ 不开 3.38 GB 包）折出 **30 根骨**
         （29 真槽 ＋ `root`）、9 个空槽一位不差、父子链与节点树逐条相同；
         负控：两根骨名字对调 / 槽名表两个蒙皮件不一致 -> 必须红。

## 第二个职责（2026-10-15 加）：可瞄武器的**静止朝向校正**

用户报「`Kord_turret` 枪口朝车尾」。取证结论（不是推断，读数落在
`_work/kordfix/turret_nodes.txt` / `turret_prefab_nodes.json`）：**是资产自己的静止姿势** ——
`turret_0_0` 的 `localRot=(0,-1,0,0)`（绕 +Y 180°）把整挺 Kord 摆到车尾，
而主炮那一支 `weapon_0_1`（同一个爹）是单位四元数、枪口世界 z=+5.811（车头 = Unity +z）。

本模块负责**折出该转哪些转台**（纯数据），改节点四元数那一步在 `align_aim_nodes`、
调用点在 `importer._import_from_source`（**`build_tree` 之前** ⇒ 对象 / 父链 / 骨 /
世界坐标一次性全对）。⛔ **这是校正，不是 prefab 原样** —— 读数里必须逐件点名，
`align_aimable=False` 才拿得到原样。机械规则与两道安全闸见本节末尾那段注释。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ba_mod_maker.asset_kernel import coords
from ba_mod_maker.asset_kernel import prefab as prefab_mod

__all__ = [
    "AIM_ALIGN_YAW_DEGREES",
    "AIM_REARWARD_DOT",
    "DEFAULT_LENGTH_FRACTION",
    "EMPTY_SLOT_PREFIX",
    "MAX_BONE_LENGTH",
    "MIN_BONE_LENGTH",
    "MUZZLE_NAME_PREFIXES",
    "ROOT_BONE_NAME",
    "SIDE_EPSILON",
    "AimMount",
    "AimPlan",
    "BoneSpec",
    "RigPlan",
    "align_aim_nodes",
    "missing_groups",
    "muzzle_nodes",
    "placeholder_slot_name",
    "plan_aim_alignment",
    "plan_rig",
    "quat_mul",
    "slot_names_of",
]

#: 蒙皮根的名字（`m_RootBoneNameHash = crc32("root/body")` 那一棵的根）。
ROOT_BONE_NAME = "root"

#: 空槽占位组的名字前缀（bone_slot_05 …）—— 全仓**唯一**一份拼法。
EMPTY_SLOT_PREFIX = "bone_slot_"

#: 骨长 = 这个比例 × 整棵树的包围盒对角线（再夹到下面两条之间）。
DEFAULT_LENGTH_FRACTION = 0.05
MIN_BONE_LENGTH = 0.03
MAX_BONE_LENGTH = 0.40


@dataclass(frozen=True, slots=True)
class BoneSpec:
    """一根要建的骨（全是**已换算到 Blender 坐标**的数）。"""

    name: str
    #: SMR 的骨槽下标；None = 只为把真槽连起来的祖先骨（`deform=False`）。
    slot: int | None
    transform_id: int | None
    parent: str | None
    head: tuple[float, float, float]
    tail: tuple[float, float, float]
    #: 骨的 Z 轴（Blender 坐标）—— `EditBone.align_roll` 用它把 roll 钉死。
    z_axis: tuple[float, float, float]
    deform: bool

    @property
    def length(self) -> float:
        return math.dist(self.head, self.tail)

    def line(self) -> str:
        kind = f"槽{self.slot:02d}" if self.slot is not None else "连接"
        parent = self.parent or "<根>"
        return (
            f"  {self.name:10} {kind} 爹={parent:10} "
            f"头=({self.head[0]:7.3f},{self.head[1]:7.3f},{self.head[2]:7.3f}) "
            f"长={self.length:.3f} 形变={'是' if self.deform else '否'}"
        )


@dataclass(frozen=True, slots=True)
class RigPlan:
    """一次「补骨架」的全部输入数据 + 读数。"""

    unit_key: str = ""
    bones: tuple[BoneSpec, ...] = ()
    #: **与 SMR 的 m_Bones 等长**的顶点组名（空槽是占位名 bone_slot_NN）—— 下标就是槽号。
    slot_names: tuple[str, ...] = ()
    #: 没有节点的槽号（实测 T-72B = 9 个：炮塔保留位）。它们**不建骨**，但槽号一位不许动。
    empty_slots: tuple[int, ...] = ()
    #: 不致命、但必须让用户看见的读数（⛔ 不静默吞）。
    issues: tuple[str, ...] = ()
    #: 建骨时用的骨长（米）。
    bone_length: float = 0.0

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(b.name for b in self.bones)

    @property
    def deform_names(self) -> tuple[str, ...]:
        return tuple(b.name for b in self.bones if b.deform)

    @property
    def placeholder_names(self) -> tuple[str, ...]:
        """空槽占位组的名字 —— 它们**本来就该没有骨**（9 个空槽一个顶点都不绑）。"""
        return tuple(placeholder_slot_name(i) for i in self.empty_slots)

    def by_name(self, name: str) -> BoneSpec | None:
        for bone in self.bones:
            if bone.name == name:
                return bone
        return None

    def slot_of(self, name: str) -> int | None:
        bone = self.by_name(name)
        return None if bone is None else bone.slot

    def summary(self) -> str:
        """一行读数（进 `ImportReport.summary()` 与面板状态栏）。"""
        if not self.bones:
            return "骨架：没建（" + (self.issues[0] if self.issues else "没有可建的骨") + "）"
        roots = [b.name for b in self.bones if b.parent is None]
        return (
            f"骨架 {len(self.bones)} 根骨（真槽 {len(self.deform_names)} ＋ 连接 "
            f"{len(self.bones) - len(self.deform_names)}）· 空槽 {len(self.empty_slots)} 个 · "
            f"骨长 {self.bone_length:.2f} m · 链根 {len(roots)} 个 {roots[:3]}"
            + (f" · ⚠ {len(self.issues)} 条" if self.issues else "")
        )

    def lines(self, limit: int = 40) -> tuple[str, ...]:
        """逐根骨一行（面板「原始读数」与探针用）。"""
        out = [self.summary()]
        if self.empty_slots:
            out.append("  空槽（没有节点，不建骨）：" + ", ".join(str(i) for i in self.empty_slots))
        for bone in self.bones[:limit]:
            out.append(bone.line())
        if len(self.bones) > limit:
            out.append(f"  …还有 {len(self.bones) - limit} 根")
        out.extend("  ⚠ " + item for item in self.issues)
        return tuple(out)


def placeholder_slot_name(slot: int) -> str:
    """空槽的占位组名（下标一位不差 —— 它就是槽号）。"""
    return f"{EMPTY_SLOT_PREFIX}{slot:02d}"


def slot_names_of(tree: prefab_mod.PrefabTree, renderer: prefab_mod.Renderer) -> tuple[str, ...]:
    """骨槽 -> 顶点组名（**按槽号**，空槽也要占一位，否则权重整体错位）。

    ⛔ 这是全仓**唯一**一份骨槽命名（`importer._vertex_group_names` 调它）：
    名字是合同（Armature 修改器按名字找骨），两处各写一份必然漂移。

    重名的节点（Unity 允许、Blender 不允许）按出现次序加 `_1` / `_2` 后缀 ——
    与 `importer` 从 W3 起就在用的那条规则**逐字相同**（D5 的读数依赖它）。
    """
    names: list[str] = []
    for slot, bone_id in enumerate(renderer.bone_ids):
        names.append(tree.node(bone_id).name if bone_id else placeholder_slot_name(slot))
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        if name in seen:
            seen[name] += 1
            names[i] = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
    return tuple(names)


def _column(matrix: Sequence[float], index: int) -> tuple[float, float, float]:
    """行主序 4x4 的第 index 列（= 局部轴在该矩阵空间里的像）。"""
    return (matrix[index], matrix[4 + index], matrix[8 + index])


def _normalize(
    v: Sequence[float], fallback: tuple[float, float, float]
) -> tuple[float, float, float]:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-12:
        return fallback
    return (v[0] / length, v[1] / length, v[2] / length)


def _diagonal(tree: prefab_mod.PrefabTree) -> float:
    """整棵树的包围盒对角线（Unity 单位 = 米；换基是等距的，长度不变）。"""
    points = [tree.world_position(node.transform_id) for node in tree.nodes]
    if not points:
        return 0.0
    lows = [min(p[i] for p in points) for i in range(3)]
    highs = [max(p[i] for p in points) for i in range(3)]
    return math.dist(lows, highs)


def _ancestors_to_root(
    tree: prefab_mod.PrefabTree, transform_id: int, root_name: str
) -> tuple[int, ...]:
    """从 transform_id 的**爹**往上走，直到并**含**名为 root_name 的那个节点。

    链上没有 root_name -> 返回空（那个槽就成了 Blender 里的一根根骨，并记一条读数）。
    """
    out: list[int] = []
    node = tree.node(transform_id)
    guard = 0
    while node.parent_id is not None:
        guard += 1
        if guard > 64:
            break
        parent = tree.node(node.parent_id)
        out.append(parent.transform_id)
        if parent.name == root_name:
            return tuple(out)
        node = parent
    return ()


def plan_rig(
    tree: prefab_mod.PrefabTree,
    renderers: Iterable[prefab_mod.Renderer],
    *,
    unit_key: str = "",
    root_name: str = ROOT_BONE_NAME,
    length_fraction: float = DEFAULT_LENGTH_FRACTION,
    min_length: float = MIN_BONE_LENGTH,
    max_length: float = MAX_BONE_LENGTH,
) -> RigPlan:
    """prefab 树 + 蒙皮件 -> 骨规划（纯函数，不碰 bpy）。

    :param renderers: 本次导入的 renderer 全集（非蒙皮的会被忽略）。
    """
    issues: list[str] = []
    skinned = [r for r in renderers if r.kind == prefab_mod.SKINNED_MESH_RENDERER and r.bone_ids]
    if not skinned:
        return RigPlan(
            unit_key=unit_key, issues=("没有任何带骨槽的蒙皮件（SMR.m_Bones 全空）⇒ 没有骨可建",)
        )

    tables = [slot_names_of(tree, r) for r in skinned]
    # ★ 2026-10-16 实测（`DLC2_XA180EST` · 读数 `_work/rigdict/bone-DLC2_XA180EST.json`）：
    #   一个 prefab 里可以住着**两副完全不同的骨架** —— 车体 `Chassis` 一族是 **19 槽**
    #   （`body` / `door_L` / `door_R` / `watershield` / `watershield_01` / `antenna` /
    #   `L00n+LR00n` / `R00n+RR00n`），而**嵌套进来的驾驶员** `lod_0/1/2` 是 **40 槽**
    #   （`Hips` / `Spine` / `Head` … 一副 Unity Humanoid）。
    #   原来的 `base = tables[0]` ＋ **一个全局** `槽号 -> 节点` 把它们当成一套 ⇒
    #   `base[39]` 越界 ⇒ **IndexError ⇒ 整个「拿车体」当场失败**
    #   （与 2026-10-15 CV-9035 那次是**同一个形状**：长度不同是读数，不是异常）。
    #   ⇒ 按**槽名表**分组；主组 = 蒙皮件最多的那一组（平手取先出现的那份）。
    #   每根骨的名字取自**它自己那一组**的表 —— 两副骨架各自正确，谁也不越界。
    groups: dict[tuple[str, ...], list[prefab_mod.Renderer]] = {}
    for renderer, names in zip(skinned, tables, strict=True):
        groups.setdefault(names, []).append(renderer)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), tables.index(kv[0])))
    base, base_group = ordered[0]
    for names, others in ordered[1:]:
        who = "、".join(r.name for r in others[:4])
        issues.append(
            f"这个 prefab 里有 {len(ordered)} 副**不同的骨架**：主组 {len(base_group)} 个蒙皮件"
            f"（{len(base)} 槽，以 {base_group[0].name} 那份为准），"
            f"另有 {len(others)} 个（{len(names)} 槽：{who}）—— 各按各的表建骨（⛔ 不混成一套）"
        )
    for renderer, names in zip(skinned[1:], tables[1:], strict=True):
        if names != base:
            # ★ 2026-10-15 真机（Computer Use）点出来的 bug：这里原来写 `zip(base, names,
            #   strict=True)` —— 而**长度不同正是要报出来的读数**，不是异常。
            #   实测：CV-9035（BALT_CR9037）件里两个蒙皮件的骨槽数不一样 ⇒
            #   `ValueError: zip() argument 2 is shorter than argument 1` ⇒
            #   整个「拿车体」当场失败（T-72B 没这形状，所以之前一直没撞上）。
            #   规矩：这里对齐到**短的那一份**，把长度差单独写进读数行（⛔ 不静默吞）。
            # B905：这里**故意** strict=False —— 长度不同正是要报的读数，不是异常。
            diff = [i for i, (a, b) in enumerate(zip(base, names, strict=False)) if a != b]
            issues.append(
                f"{renderer.name} 的骨槽名表与 {skinned[0].name} 不同"
                f"（长度 {len(base)} vs {len(names)}；{len(diff)} 处名字不同，"
                f"首个下标 {diff[:5]}）—— 本次以 {base_group[0].name} 那份为准"
            )

    # ---- 真槽：**每一副骨架各自**算「槽号 -> 节点」----
    #   ⛔ 跨组比槽号没有意义（车体第 0 号槽是 `body`、驾驶员第 0 号槽是 `Hips`）——
    #   所以「同一个槽指了两个节点」这条读数**只在同一组内部**判。
    wanted: dict[int, int | None] = {}
    name_of: dict[int, str] = {}
    missing_root: list[str] = []
    for names, group in ordered:
        seen_slots: dict[int, int] = {}
        for renderer in group:
            for slot, bone_id in enumerate(renderer.bone_ids):
                if not bone_id:
                    continue
                tid = int(bone_id)
                previous = seen_slots.setdefault(slot, tid)
                if previous != tid:
                    issues.append(
                        f"第 {slot} 号槽在「{group[0].name}」这一副骨架的两个蒙皮件上"
                        f"指的不是同一个节点（{previous} vs {tid}）—— 这两件绑不到同一根骨上"
                    )
                    continue
                # 名字取自**它自己那一组**的表；主组先跑 ⇒ 同一个节点优先用主组的名。
                name_of.setdefault(tid, names[slot])
                # 真槽盖过「只做连接」那条（祖先先被收进来时 slot 是 None，这里要升回真槽号）。
                if wanted.get(tid) is None:
                    wanted[tid] = slot
        for tid in sorted(seen_slots.values()):
            chain = _ancestors_to_root(tree, tid, root_name)
            if not chain:
                missing_root.append(name_of.get(tid, str(tid)))
            for ancestor in chain:
                wanted.setdefault(ancestor, None)
    if missing_root:
        issues.append(
            f"{len(missing_root)} 个槽的父链上没有名为 {root_name!r} 的节点"
            f"（{missing_root[:6]}）—— 它们在 Blender 里是根骨，不会跟着别的骨走"
        )

    # ---- 骨长：按整棵树的对角线定（与单位大小无关地"看得见"）----
    diagonal = _diagonal(tree)
    bone_length = min(max(length_fraction * diagonal, min_length), max_length)

    # ---- 造骨：按**节点树顺序**（爹一定排在儿子前面）----
    bones: list[BoneSpec] = []
    for node in tree.nodes:
        if node.transform_id not in wanted:
            continue
        slot = wanted[node.transform_id]
        world = tree.world_matrix(node.transform_id)
        head = coords.unity_to_blender(tree.world_position(node.transform_id))
        direction = _normalize(
            coords.unity_to_blender(_column(world, 1)),
            coords.unity_to_blender((0.0, 1.0, 0.0)),
        )
        z_axis = _normalize(
            coords.unity_to_blender(_column(world, 2)),
            coords.unity_to_blender((0.0, 0.0, 1.0)),
        )
        name = name_of.get(node.transform_id, node.name) if slot is not None else node.name
        bones.append(
            BoneSpec(
                name=name,
                slot=slot,
                transform_id=node.transform_id,
                parent=None,  # 下一步统一挂（骨表还没建全）
                head=head,
                tail=(
                    head[0] + direction[0] * bone_length,
                    head[1] + direction[1] * bone_length,
                    head[2] + direction[2] * bone_length,
                ),
                z_axis=z_axis,
                deform=slot is not None,
            )
        )

    # ---- 挂父子：往上走到**最近的另一根骨** ----
    spec_of_tid = {b.transform_id: b for b in bones}
    resolved: list[BoneSpec] = []
    for bone in bones:
        parent_name = None
        node = tree.node(bone.transform_id)  # type: ignore[arg-type]
        while node.parent_id is not None:
            parent = spec_of_tid.get(node.parent_id)
            if parent is not None:
                parent_name = parent.name
                break
            node = tree.node(node.parent_id)
        resolved.append(
            BoneSpec(
                name=bone.name,
                slot=bone.slot,
                transform_id=bone.transform_id,
                parent=parent_name,
                head=bone.head,
                tail=bone.tail,
                z_axis=bone.z_axis,
                deform=bone.deform,
            )
        )

    names = [b.name for b in resolved]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        issues.append(
            f"骨名重复 {duplicates} —— ⛔ Blender 会给后建的那根自动加 .001，"
            "名字就不再等于顶点组名（那根骨会静默不生效）"
        )
    empty_slots = tuple(i for i, bone_id in enumerate(skinned[0].bone_ids) if not bone_id)
    if empty_slots:
        # 空槽是 Unity 侧的保留位（炮塔），它的骨槽名是 m_Bones[i] 的空指针 —— 没东西可建。
        for renderer in skinned[1:]:
            theirs = tuple(i for i, b in enumerate(renderer.bone_ids) if not b)
            if theirs != empty_slots:
                issues.append(
                    f"空槽位置对不上：{skinned[0].name} 是 {list(empty_slots)}，"
                    f"{renderer.name} 是 {list(theirs)}"
                )
                break
    return RigPlan(
        unit_key=unit_key,
        bones=tuple(resolved),
        slot_names=tuple(base),
        empty_slots=empty_slots,
        issues=tuple(issues),
        bone_length=bone_length,
    )


def missing_groups(plan: RigPlan, group_names: Iterable[str]) -> tuple[str, ...]:
    """顶点组里有、骨里没有的名字（⛔ 这些组会静默不生效 —— 必须让用户看见）。

    ⚠ 空槽占位组（bone_slot_05 …）**不算**：那 9 个槽本来就没有节点、也没有骨
    （一个顶点都不绑在上面），它们空着是**对的**。
    """
    have = set(plan.names) | set(plan.placeholder_names)
    return tuple(sorted({str(n) for n in group_names if str(n) not in have}))


# --------------------------------------------------------------------------- #
# ★ 2026-10-15 · 可瞄武器朝向校正（**校正 ≠ 原样**；出厂默认开、可关）
#
# 现象（用户报）：Blender 里 RU_T72B 的 `Kord_turret` 枪口朝**车尾**。
#
# 取证（开 units 包 read_prefab_nodes(RU_T72B.prefab)，28 节点 / 8 renderer；
# 读数 = `_work/kordfix/turret_nodes.txt` 与 `turret_prefab_nodes.json`）：
#
# | 节点 | localRot (x,y,z,w) | 读数 |
# |---|---|---|
# | `turret_0` | (0,0,0,1) | 炮塔总成（单位四元数） |
# | `turret_0_0` | **(0,-1,0,0)** | **绕 +Y 180°** —— 底下就是整挺 Kord |
# | `weapon_0` / `Kord_turret_old` / `Kord_turret` | (0,0,0,1) | 单位四元数（没有第二个 180°） |
# | `weapon_0_1`（主炮，同一个爹 `turret_0`） | (0,0,0,1) | 枪口 `shell_spawn_0_0` z=+5.811 |
#
# 机枪那一支的枪口代理 `shell_spawn_0` 世界 z=**-2.314**。整棵 `RU_T72B_Base`
# （52 节点）除 `exhaust` 外**全是单位四元数** ⇒ **换轴没有系统性问题**。
# 两条与炮无关的方向锚点（车头 = Unity **+z**）：`VFXPoint_OnSmokeAbility` z=+8.06
# （T-72 的 902A 烟雾发射器在炮塔正面）、`exhaust` z=-1.85（排气在车尾）。
#
# ⇒ **这是资产自己的静止姿势**（炮塔件在自己局部坐标系里是朝 +z 的，prefab 用一个
# 180° 的转台把它摆成朝后）；导入侧只走 `coords.unity_to_blender_quat`，**原样搬运**。
#
# ⛔ **没有证明游戏运行时会不会自己把这个转台转回来** —— `_work/isil` 那一轮没读到
# 写这个转台四元数的调用点（见回信）。所以这里是**校正**，不是「游戏里看到的原样」。
#
# ---- 机械规则（⛔ 不猜名字、不猜方位）------------------------------------ #
#
# 1. **枪口代理** = 名字以 `MUZZLE_NAME_PREFIXES` 开头的节点（实测这一族：
#    `shell_spawn_0` / `shell_spawn_1_0` / `shell_spawn_0_0` / `fire_z` / `fire_y`）；
# 2. 从枪口往上走：**父节点子树里的枪口在父节点自己坐标系里同向**就继续往上，
#    **混向**就停在当前节点 —— 停住的这个就是**转台**（它只属于这一门武器）；
# 3. 转台自己的 +z 与**件根**的 +z 点积 < `AIM_REARWARD_DOT` ⇒ 这个转台整体朝着车尾，
#    绕垂直轴（Unity +Y）转 `AIM_ALIGN_YAW_DEGREES`。
#
# 两道安全闸（都进 `AimPlan.issues`，⛔ 不静默）：
#
# * 一路走到根都没有「混向的父节点」⇒ **不成转台、不转** —— 单武器单位（例如只有一门
#   朝后的尾炮）因此**不会被整车翻转**；
# * 转台子树里有 `SkinnedMeshRenderer` ⇒ **跳过**（转它会让 bind≠rest，破掉本模块
#   开头那条「绑定姿势 == prefab 摆的那个姿势」的不变量）。
# --------------------------------------------------------------------------- #

#: 枪口代理节点的名字前缀（实测 RU_T72B 炮塔件这一族：`shell_spawn_0` / `shell_spawn_0_0`
#: / `shell_spawn_1_0` / `fire_z`；另有 `fire_y` 挂在 `root` 底下）。
MUZZLE_NAME_PREFIXES = ("shell_spawn", "fire_")

#: 校正量：绕**垂直轴**（Unity +Y，= Blender +Z）180° —— 与 prefab 那个转台上写的是同一个角。
AIM_ALIGN_YAW_DEGREES = 180.0

#: 「朝前 / 朝后」的最小前向投影（米）：比它小的当**说不清**，不计入方向票。
SIDE_EPSILON = 1e-4

#: 转台的 +z 与**件根**的 +z 点积低于这个值 ⇒ 当**朝车尾**（-1 = 正好 180°，+1 = 与车头同向）。
#: -0.5 = 120°：转过来超过 120° 才算「朝后」，横着（90°）的不动。
AIM_REARWARD_DOT = -0.5


@dataclass(frozen=True, slots=True)
class AimMount:
    """一个「可瞄武器的转台」—— 朝向校正的**唯一落点**（转它，整串武器一起转）。"""

    name: str
    transform_id: int
    parent_id: int | None
    #: 它子树里所有枪口代理的名字（按节点树序）。
    muzzle_names: tuple[str, ...]
    #: 它子树里的 renderer 名（= 用户看得见的那些件，例如 `Kord_turret`）—— 读数逐件点名用。
    rendered: tuple[str, ...]
    #: 这个转台**原始**的局部四元数 (x,y,z,w) —— 读数要能把它原样引出来。
    local_rotation: tuple[float, float, float, float]
    #: 枪口在这个转台**自己坐标系**里的前向投影（米，Unity +z 为正）—— 它一定是正的
    #: （枪口长在自己转台的前面）。所以「朝哪」**不能**用它判，要用下面那个点积。
    muzzle_local_m: float
    #: 枪口在**件根坐标系**里的前向投影（米，+ = 车头方向）—— 判据一行量的是它。
    nose_m: float
    #: 转台自己的 +z 与件根的 +z 的点积：-1 = 正好转了 180°，+1 = 与车头同向。
    heading_dot: float
    #: 安全闸拦下来的原因（非空 = **不转**）。
    blocked: str = ""

    @property
    def rearward(self) -> bool:
        return self.heading_dot < AIM_REARWARD_DOT and not self.blocked

    def line(self) -> str:
        parent = "<根>" if self.parent_id is None else str(self.parent_id)
        if self.blocked:
            verdict = f"原样，不动（{self.blocked}）"
        elif self.rearward:
            verdict = "已按车头对齐（转 180°）"
        else:
            verdict = "原样，不动（本来就朝车头）"
        rot = "(" + ",".join(f"{v:+.4f}" for v in self.local_rotation) + ")"
        pieces = "、".join(self.rendered) or "（子树里没有 renderer）"
        return (
            f"  {pieces}：{verdict}；**资产原始静止姿势** = 转台 {self.name} "
            f"localRot={rot}，枪口在件根系 z={self.nose_m:+.3f} m"
            f"（转台自己的 +z 与车头点积 {self.heading_dot:+.3f}，tr={self.transform_id}，"
            f"爹={parent}）"
        )


@dataclass(frozen=True, slots=True)
class AimPlan:
    """一次「可瞄武器对齐车头」的输入数据 + 读数（纯数据，不碰 bpy）。"""

    unit_key: str = ""
    #: 全树的枪口代理名（空 = 这个件没有可瞄武器 ⇒ 原样）。
    muzzles: tuple[str, ...] = ()
    #: 找到的转台 —— **含不转的那些**（读数要看得见它们为什么不动）。
    mounts: tuple[AimMount, ...] = ()
    #: 必须让用户看见的读数（⛔ 不静默吞）。
    issues: tuple[str, ...] = ()

    @property
    def rotated(self) -> tuple[AimMount, ...]:
        return tuple(m for m in self.mounts if m.rearward)

    @property
    def names(self) -> tuple[str, ...]:
        """要被转 180° 的转台名（读数逐件点名用的就是它）。"""
        return tuple(m.name for m in self.rotated)

    def summary(self) -> str:
        """一行读数（进 `ImportReport.summary()` 与面板状态栏）—— 逐件点名。"""
        tail = f" · ⚠ {len(self.issues)} 条" if self.issues else ""
        if not self.muzzles:
            return "朝向校正：没有枪口代理（这个件没有可瞄武器）⇒ 原样" + tail
        if not self.rotated:
            return f"朝向校正：{len(self.mounts)} 个转台都没有朝车尾的 ⇒ 原样" + tail
        parts = []
        for mount in self.rotated:
            pieces = "、".join(mount.rendered) or mount.name
            rot = "(" + ",".join(f"{v:+.4f}" for v in mount.local_rotation) + ")"
            parts.append(
                f"{pieces}：已按车头对齐（转 180°）；"
                f"**资产原始静止姿势是朝后**（转台 {mount.name} localRot={rot}，"
                f"枪口在件根系 z={mount.nose_m:+.3f} m）"
            )
        return "⚠ 朝向校正（**这是校正，不是原样**）：" + "；".join(parts) + tail

    def lines(self, limit: int = 20) -> tuple[str, ...]:
        """逐件一行（面板「原始读数」与探针用）。"""
        out = [self.summary()]
        for mount in self.mounts[:limit]:
            out.append(mount.line())
        if len(self.mounts) > limit:
            out.append(f"  …还有 {len(self.mounts) - limit} 个转台")
        out.extend("  ⚠ " + item for item in self.issues)
        return tuple(out)


def muzzle_nodes(tree: prefab_mod.PrefabTree) -> tuple[prefab_mod.Node, ...]:
    """枪口代理节点（发射点）：名字前缀见 `MUZZLE_NAME_PREFIXES`。"""
    return tuple(n for n in tree.nodes if n.name.startswith(MUZZLE_NAME_PREFIXES))


def _quat_of(raw: object) -> tuple[float, float, float, float]:
    """归一化节点上的 rotation（list / tuple / dict）-> (x, y, z, w)。"""
    if isinstance(raw, Mapping):
        return (
            float(raw.get("x", 0.0)),
            float(raw.get("y", 0.0)),
            float(raw.get("z", 0.0)),
            float(raw.get("w", 1.0)),
        )
    values = [float(v) for v in list(raw or ())[:4]]  # type: ignore[call-overload]
    while len(values) < 4:
        values.append(1.0 if len(values) == 3 else 0.0)
    return (values[0], values[1], values[2], values[3])


def _yaw_quat(degrees: float) -> tuple[float, float, float, float]:
    """绕 **Unity +Y**（= Blender +Z，垂直轴）转 `degrees` 的四元数 (x, y, z, w)。"""
    half = math.radians(degrees) / 2.0
    return (0.0, math.sin(half), 0.0, math.cos(half))


def quat_mul(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Unity 口径的四元数乘法（Hamilton，结果 = **先 b 后 a**，与 `a * b` 同序）。"""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _world_forward(
    tree: prefab_mod.PrefabTree, node: prefab_mod.Node
) -> tuple[float, float, float]:
    """节点自己的 +z 轴在世界里的像（单位向量；+z 就是 Unity 里的"车头方向"）。"""
    return _normalize(_column(tree.world_matrix(node.transform_id), 2), (0.0, 0.0, 1.0))


def _project_on(
    tree: prefab_mod.PrefabTree,
    node: prefab_mod.Node,
    axis: tuple[float, float, float],
    point: prefab_mod.Node,
) -> float:
    """`point` 相对 `node` 的位移在给定单位轴上的投影（米）。"""
    here = tree.world_position(node.transform_id)
    away = tree.world_position(point.transform_id)
    return sum((away[i] - here[i]) * axis[i] for i in range(3))


def _subtree_maps(
    tree: prefab_mod.PrefabTree, muzzles: Sequence[prefab_mod.Node]
) -> tuple[dict[int, list[prefab_mod.Node]], dict[int, list[int]], dict[int, list[str]]]:
    """每个节点 -> (子树里的枪口, 子树里的节点号, 子树里的 renderer 名)。

    按 `tree.nodes` **倒着 fold**（build_tree 保证爹排在儿子前面）。
    """
    by_muzzle: dict[int, list[prefab_mod.Node]] = {n.transform_id: [] for n in tree.nodes}
    members: dict[int, list[int]] = {n.transform_id: [] for n in tree.nodes}
    rendered: dict[int, list[str]] = {n.transform_id: [] for n in tree.nodes}
    for muzzle in muzzles:
        by_muzzle[muzzle.transform_id].append(muzzle)
    for renderer in tree.renderers:
        rendered[renderer.node_id].append(renderer.name)
    for node in reversed(tree.nodes):
        tid = node.transform_id
        members[tid].append(tid)
        if node.parent_id is None:
            continue
        by_muzzle[node.parent_id].extend(by_muzzle[tid])
        members[node.parent_id].extend(members[tid])
        rendered[node.parent_id].extend(rendered[tid])
    return by_muzzle, members, rendered


def _pivot_of(
    tree: prefab_mod.PrefabTree,
    muzzle: prefab_mod.Node,
    sides: object,
) -> prefab_mod.Node | None:
    """从枪口往上走：第一个「子树混向」的父节点的**前一个节点**就是转台。

    走到根都没遇到混向父节点 ⇒ None（⛔ 不转 —— 单武器单位不会被整车翻转）。
    枪口自己就是那个分界（例如 `fire_y` 挂在 `root` 底下）⇒ 也 None。
    """
    node = muzzle
    while node.parent_id is not None:
        parent = tree.node(node.parent_id)
        if len(sides(parent.transform_id)) > 1:  # type: ignore[operator]
            return None if node.transform_id == muzzle.transform_id else node
        node = parent
    return None


def plan_aim_alignment(tree: prefab_mod.PrefabTree, *, unit_key: str = "") -> AimPlan:
    """prefab 树 -> 「哪些转台朝车尾、要不要转 180°」的纯数据（不碰 bpy）。"""
    muzzles = muzzle_nodes(tree)
    if not muzzles:
        return AimPlan(unit_key=unit_key)
    by_muzzle, members, rendered = _subtree_maps(tree, muzzles)
    issues: list[str] = []

    def sides(tid: int) -> set[int]:
        """这个节点的子树里，枪口在**它自己坐标系**里落在 +z 还是 -z（空 = 说不清）。"""
        out: set[int] = set()
        node = tree.node(tid)
        forward = _world_forward(tree, node)
        for muzzle in by_muzzle[tid]:
            value = _project_on(tree, node, forward, muzzle)
            if abs(value) >= SIDE_EPSILON:
                out.add(1 if value > 0 else -1)
        return out

    skinned = {r.node_id for r in tree.renderers if r.kind == prefab_mod.SKINNED_MESH_RENDERER}
    root_forward = _world_forward(tree, tree.node(tree.root))
    found: dict[int, AimMount] = {}
    for muzzle in muzzles:
        pivot = _pivot_of(tree, muzzle, sides)
        if pivot is None or pivot.transform_id in found:
            continue
        tid = pivot.transform_id
        own = by_muzzle[tid]
        local_m = max(
            (_project_on(tree, pivot, _world_forward(tree, pivot), m) for m in own),
            key=abs,
            default=0.0,
        )
        nose_m = max((_project_on(tree, pivot, root_forward, m) for m in own), key=abs, default=0.0)
        heading = sum(a * b for a, b in zip(_world_forward(tree, pivot), root_forward, strict=True))
        blocked = ""
        dirty = sorted(skinned & set(members[tid]))
        if dirty:
            blocked = (
                f"子树里有 {len(dirty)} 个蒙皮件（{dirty[:3]}）—— 转它会破掉"
                "「绑定姿势 == prefab 摆的那个姿势」那条不变量"
            )
            issues.append(f"{pivot.name}：{blocked} ⇒ 本次跳过")
        found[tid] = AimMount(
            name=pivot.name,
            transform_id=tid,
            parent_id=pivot.parent_id,
            muzzle_names=tuple(m.name for m in own),
            rendered=tuple(rendered[tid]),
            local_rotation=_quat_of(pivot.rotation),
            muzzle_local_m=local_m,
            nose_m=nose_m,
            heading_dot=heading,
            blocked=blocked,
        )
    if not found:
        issues.append(
            "每个枪口代理一路走到根都没有「混向的父节点」⇒ 找不到转台"
            "（单武器 / 全同向的单位不会被翻）"
        )
    ordered = tuple(found[n.transform_id] for n in tree.nodes if n.transform_id in found)
    return AimPlan(
        unit_key=unit_key,
        muzzles=tuple(m.name for m in muzzles),
        mounts=ordered,
        issues=tuple(issues),
    )


def align_aim_nodes(
    nodes: Sequence[Mapping[str, object]],
    *,
    unit_key: str = "",
    yaw_degrees: float = AIM_ALIGN_YAW_DEGREES,
) -> tuple[list[dict[str, object]], AimPlan]:
    """归一化节点表 -> (**改过局部四元数的**节点表, `AimPlan`)。纯数据，不碰 bpy。

    ⛔ 改的是节点自己的 `m_LocalRotation`（**左乘**绕垂直轴的 180°：结果 = 先按原旋转摆，
    再在**父坐标系**里绕垂直轴转）：于是网格对象、父链、骨、世界坐标**一次性全对**
    —— 它们都从这同一棵树折出来（`prefab.build_tree` 是唯一一份实现）。

    没有要转的转台时**原样返回**（连 dict 都不重建）。
    """
    tree = prefab_mod.build_tree(nodes)
    plan = plan_aim_alignment(tree, unit_key=unit_key)
    targets = {m.transform_id for m in plan.rotated}
    if not targets:
        return list(nodes), plan
    yaw = _yaw_quat(yaw_degrees)
    out: list[dict[str, object]] = []
    for raw in nodes:
        fixed = dict(raw)
        if int(raw["transform_id"]) in targets:  # type: ignore[arg-type]
            fixed["rotation"] = list(quat_mul(yaw, _quat_of(raw.get("rotation"))))
        out.append(fixed)
    return out, plan


# --------------------------------------------------------------------------- #
# ★ 2026-10-16 · 按车型的「应动部件词典」（外包自述 × 实物读数）
#
# 由来：`_work/mobygames/artstation-specs.tsv`（15 位作者 / 40+ 件 BA 资产）里，
# **7 件**在作品自述里**逐个点了 rig 部件**（Danila Ivankov；原文落在
# `_work/mobygames/artstation/_desc.txt`，逐件 URL 见下表）；另有 **10 件**
# （Yriy Denisenko）＋ **3 件**（Ratmir Shaiakhmetov）只写到
# 「rig / Vehicle rigging / Helicopter rigging」这一级 —— **没有部件清单**
# （照写「未证实」，⛔ 不替他们编部件）。
#
# ⛔ 两条硬口径（task 写死的，别松）：
# 1. **这不是闸门** —— 那七句是「这家外包商的交付习惯」，不是游戏的硬约束
#    （`STATUS.md` 硬边界 §2.1）。本词典只**报读数与缺失**，⛔ 绝不拦用户的件；
# 2. **每条都要能追到出处** —— 部件来自哪一句自述（URL）、落点来自哪一次实跑
#    （`_work/rigdict/bone-*.json`）。**没量到落点的写「未证实」**，⛔ 不许编骨名。
#
# 唯一一件**部件级落点量全了**的：`DLC2_XA180EST`（HUDName **XA-180** · Id=501）
# ＋ 它配对的炮塔件 `DLC2_XA180EST/DLC2_XA180EST_GMG`（TurretId 1010）——
# 自述那 6 个词**一个不少**地在骨名里找到了落点。
# ⚠ HUDName **XA-180** 全库有两行（`Id=396 BALT XA180` 与 `Id=501 DLC2_XA180EST`）
#   ⇒ 报的时候必须带 Id（AGENTS §0.13）。
# --------------------------------------------------------------------------- #

#: 件类型：一个 prefab 里可以住着两**件**（车体 / 炮塔），它们各有各的骨架。
PIECE_BODY = "body"
PIECE_TURRET = "turret"
PIECE_LABELS = {PIECE_BODY: "车体", PIECE_TURRET: "炮塔"}

#: 模式里的「一位数字」（⛔ 只认 ASCII 数字 —— 与骨名契约一个口径）。
DIGITS = "0123456789"

#: 同一个件里出现多副骨架时，主组（= `RigPlan.slot_names` 那一份）之外的那些骨架的
#: 读数行前缀 —— 面板 / 核对器都按它认（⛔ 只此一份拼法）。
EXTRA_SKELETON_TAG = "这个 prefab 里有"


@dataclass(frozen=True, slots=True)
class MovingPart:
    """一个**应动部件** —— 就是作者自述里 `Rigged: …` 那串词中的一个。

    :param key: **归一部件键**（跨车型可比：`wheels` / `suspension` / …）
    :param word: **自述原文那个英文词**（逐字，可回查那一句；⛔ 不许改写成我们的说法）
    :param label: 中文标签（给用户看的那一列）
    :param piece: 它该在**哪一件**里动（车体 / 炮塔是两件 —— `panels_take` 模块头）
    :param patterns: 骨名匹配式（`#` = 一位数字、`*` = 任意一串）；**空 = 未证实**
    :param expected: 实测该命中几根；`None` = 没量过，只报命中数、不判满
    :param measured: 落点的实测出处；空 = 没量过（面板会印「未证实」）
    """

    key: str
    word: str
    label: str
    piece: str = PIECE_BODY
    patterns: tuple[str, ...] = ()
    expected: int | None = None
    measured: str = ""

    @property
    def verified(self) -> bool:
        """落点量到过没有（⛔ 没量到**不是**缺失，是「不知道」）。"""
        return bool(self.patterns)


@dataclass(frozen=True, slots=True)
class VehicleRig:
    """一辆车（或一件）的**应动部件清单** ＋ 它的出处。"""

    key: str  # 机型键：游戏件键，或 ArtStation slug
    name: str  # 显示名（**HUDName 优先**，AGENTS §0.13）
    source: str  # 自述出处 URL（⛔ 必填 —— 没出处不许进这张表）
    parts: tuple[MovingPart, ...] = ()
    aliases: tuple[str, ...] = ()  # 还认哪些键（slug / 游戏名 / 变体）
    note: str = ""

    def part(self, key: str) -> MovingPart | None:
        for item in self.parts:
            if item.key == key:
                return item
        return None

    @property
    def verified_parts(self) -> tuple[MovingPart, ...]:
        return tuple(p for p in self.parts if p.verified)

    def parts_of(self, piece: str) -> tuple[MovingPart, ...]:
        return tuple(p for p in self.parts if p.piece == piece)


def name_matches(name: str, pattern: str) -> bool:
    """骨名匹配一条模式：`#` = **一位数字**、`*` = 任意一串（含空）。

    ⛔ **不是正则**（用户给的词直接进模式 ⇒ 不许有元字符注入）；⛔ **大小写敏感** ——
    骨名是合同（Armature 修改器按名字找骨），`door_L` 与 `door_l` 是两个东西。
    """
    i = j = 0
    star = -1
    mark = 0
    while i < len(name):
        if j < len(pattern) and pattern[j] == "*":
            star, mark = j, i
            j += 1
        elif j < len(pattern) and (
            (pattern[j] == "#" and name[i] in DIGITS) or pattern[j] == name[i]
        ):
            i += 1
            j += 1
        elif star >= 0:
            mark += 1
            i, j = mark, star + 1
        else:
            return False
    while j < len(pattern) and pattern[j] == "*":
        j += 1
    return j == len(pattern)


def match_bones(names: Iterable[str], patterns: Iterable[str]) -> tuple[str, ...]:
    """骨名表里命中这些模式的那些（**按传入顺序**，⛔ 不重排）。"""
    pats = tuple(str(p) for p in patterns)
    if not pats:
        return ()
    return tuple(str(n) for n in names if any(name_matches(str(n), p) for p in pats))


#: 实测出处的简写（下面 `measured=` 一栏用它，⛔ 不写字面长路径）。
MEASURED_DIR = "_work/rigdict/"

#: 一件里那副骨架的实测出处：**车体件**（`Chassis` 一族 9 个 SMR，骨槽表逐字相同）。
MEASURED_XA180_BODY = (
    MEASURED_DIR + "bone-DLC2_XA180EST.json"
    "（车体件 LOD0 骨槽表 **19 槽 / 18 真槽**，⛔ 与作者自述的 6 个词逐词对）"
)
#: 同一件的**炮塔件**出处（TurretUnits：UnitId 501 -> TurretId 1010 · Order 0）。
MEASURED_XA180_TURRET = (
    MEASURED_DIR + "bone-DLC2_XA180EST_DLC2_XA180EST_GMG.json"
    "（炮塔件 19 槽 / 2 真槽：body + turret_0）"
)


def _part(
    key: str,
    word: str,
    label: str,
    piece: str = PIECE_BODY,
    patterns: tuple[str, ...] = (),
    expected: int | None = None,
    measured: str = "",
) -> MovingPart:
    """造一条部件（**只在这张表里用** —— 把一长串关键字参数压成一行，好对出处）。"""
    return MovingPart(
        key=key,
        word=word,
        label=label,
        piece=piece,
        patterns=patterns,
        expected=expected,
        measured=measured,
    )


#: ★ **词典本体**。一条 = 一件作品的应动部件清单 ＋ 那一句自述的 URL。
#: ⛔ 加条目之前先问两句：① 部件**逐词抄自哪一句自述**（贴 URL）？② 落点**哪一次实跑量的**？
#: 答不出来就**别加** —— 或者加进来、把 `patterns` 留空写成「未证实」（那是允许的）。
RIG_DICT: tuple[VehicleRig, ...] = (
    # ---- 唯一一件落点量全了的：XA-180EST（车体件 ＋ 炮塔件两件都对上了）----
    VehicleRig(
        key="DLC2_XA180EST",
        name="XA-180（Id=501 · 波罗的海营）",
        source="https://www.artstation.com/artwork/BkbNd9",
        aliases=("XA-180EST", "XA180EST", "BALT_XA180", "bkbnd9"),
        note=(
            "HUDName **XA-180** 全库两行：Id=396 `BALT XA180` 与 Id=501 `DLC2_XA180EST`"
            "（AGENTS §0.13）⇒ 报的时候带 Id。炮塔是**另一件**："
            "`DLC2_XA180EST/DLC2_XA180EST_GMG`（TurretId 1010 · Order 0）。"
        ),
        parts=(
            _part(
                "wheels",
                "wheels",
                "车轮",
                patterns=("LR###", "RR###"),
                expected=6,
                measured=MEASURED_XA180_BODY,
            ),
            _part(
                "suspension",
                "suspension",
                "悬挂",
                patterns=("L###", "R###"),
                expected=6,
                measured=MEASURED_XA180_BODY,
            ),
            _part(
                "rear_doors",
                "rear doors",
                "后门",
                patterns=("door_*",),
                expected=2,
                measured=MEASURED_XA180_BODY,
            ),
            _part(
                "antennas",
                "antennas",
                "天线",
                patterns=("antenna*",),
                expected=1,
                measured=MEASURED_XA180_BODY,
            ),
            # ★ 「turret」在**炮塔件**里 —— 车体件里没有它（实测：车体 18 根真槽无 turret）。
            #   ⚠ 顺序**照自述原句**（wheels, suspension, rear doors, antennas, turret,
            #   water deflector）—— 用例里有一条逐词守着这个顺序。
            _part(
                "turret",
                "turret",
                "炮塔",
                piece=PIECE_TURRET,
                patterns=("turret_*",),
                expected=1,
                measured=MEASURED_XA180_TURRET,
            ),
            _part(
                "water_deflector",
                "water deflector",
                "水道 deflector",
                patterns=("watershield*",),
                expected=2,
                measured=MEASURED_XA180_BODY,
            ),
        ),
    ),
    # ---- 另外 6 件：自述有**逐件部件清单**，但落点**没量过**（patterns 空 = 未证实）----
    VehicleRig(
        key="8vw94w",
        name="2S40「Floks」（Russian Guard DLC）",
        source="https://www.artstation.com/artwork/8vw94w",
        aliases=("2S40", "2s40_floks", "2С40"),
        parts=(
            _part("suspension", "suspension", "悬挂"),
            _part("wheels", "wheels", "车轮"),
            _part("side_rear_platforms", "side and rear platforms", "侧/后平台"),
            _part("turret", "turret", "炮塔"),
            _part("mg_turret", "mg turret", "机枪塔"),
            _part("cannon", "cannon", "火炮"),
            _part("recoil", "recoil", "后坐"),
            _part("reloading_system", "reloading system", "装填系统"),
            _part("antennas", "antennas", "天线"),
            _part("hatches", "hatches", "舱盖"),
        ),
    ),
    VehicleRig(
        key="lGB12V",
        name="Як-130（Russian Guard DLC）",
        source="https://www.artstation.com/artwork/lGB12V",
        aliases=("Yak-130", "Як-130", "yak130"),
        note="飞机件：自述里没有炮塔/车轮这一类，全是**操纵面**。",
        parts=(
            _part("ailerons", "ailerons", "副翼"),
            _part("flaps", "flaps", "襟翼"),
            _part("elevators", "elevators", "升降舵"),
            _part("rudder", "rudder", "方向舵"),
        ),
    ),
    VehicleRig(
        key="NqdP1P",
        name="IRIS-T（波罗的海营）",
        source="https://www.artstation.com/artwork/NqdP1P",
        aliases=("IRIS-T", "IRIST"),
        parts=(
            _part("suspension", "suspension", "悬挂"),
            _part("outrigger", "outrigger", "支腿"),
            _part("wheels", "wheels", "车轮"),
            _part("launcher_elevation", "launcher elevation", "发射装置举升"),
            _part("missile_canisters", "missile canisters", "导弹箱"),
            _part("antennas", "antennas", "天线"),
        ),
    ),
    VehicleRig(
        key="QKvQ94",
        name="Type-X（波罗的海营）",
        source="https://www.artstation.com/artwork/QKvQ94",
        aliases=("Type-X", "TypeX", "RCV", "MRCV"),
        note="这一件是**只交网格**那一支（Ryzhkov 原话：不做履带）—— 后面的 rig 是 Ivankov 做的。",
        parts=(
            _part("suspension_arms", "suspension arms", "悬挂臂"),
            _part("wheels_tracks", "wheels/tracks", "车轮 / 履带"),
            _part("turret_rotation_elevation", "turret rotation/elevation", "炮塔旋转 / 俯仰"),
            _part("weapon_systems", "weapon systems", "武器系统"),
            _part("sensors", "sensors", "传感器"),
            _part("antennas", "antennas", "天线"),
        ),
    ),
    VehicleRig(
        key="gReaZE",
        name="NASAMS 3（波罗的海营）",
        source="https://www.artstation.com/artwork/gReaZE",
        aliases=("NASAMS", "NASAMS3", "nasams_3"),
        parts=(
            _part("wheels", "wheels", "车轮"),
            _part("suspension", "suspension", "悬挂"),
            _part("launcher_elevation", "launcher elevation/rotation", "发射装置举升 / 旋转"),
            _part("missile_canisters", "missile canisters", "导弹箱"),
            _part("antennas", "antenna", "天线"),
            _part("stabilizers", "stabilizers", "稳定翼"),
        ),
    ),
    VehicleRig(
        key="x3EobO",
        name="M109A5Ö（波罗的海营）",
        source="https://www.artstation.com/artwork/x3EobO",
        aliases=("M109A5O", "M109A5Ö", "M109"),
        parts=(
            _part("tracks", "tracks", "履带"),
            _part("suspension", "suspension", "悬挂"),
            _part("turret", "turret", "炮塔"),
            _part("barrel_recoil", "barrel recoil", "炮管后坐"),
            _part("antennas", "antennas", "天线"),
            _part("mg_hatch", "MG hatch", "机枪舱盖"),
        ),
    ),
    # ---- 另外 13 件：自述只到**整件级**（「rig」「Vehicle rigging」）—— 没有部件清单 ----
    #   照收录：核对器会印「这个车型没有零件清单（自述只到整件级）」，⛔ **不判红**。
    *tuple(
        VehicleRig(
            key=slug, name=label, source="https://www.artstation.com/artwork/" + slug, note=word
        )
        for slug, label, word in (
            ("aomDbR", "KA-52 / KA-52K", "create UV, textures, rig"),
            ("NyrbR5", "KA-29", "create UV, textures, rig"),
            ("dK9D6A", "Boeing B-52", "create UV, textures, rig"),
            ("VJPDKn", "UAZ-469", "rigging by myself"),
            ("qePrDN", "SU-25T", "（自述只到网格 / 贴图）"),
            ("4NPaxl", "URAL 5323", "（自述只到网格 / 贴图）"),
            ("6N9ERW", "T-14 ARMATA", "（自述只到网格 / 贴图）"),
            ("WXaONQ", "BTR80/82", "（自述只到网格 / 贴图）"),
            ("3EdJPY", "MIM-104 Patriot", "（自述只到网格 / 贴图）"),
            ("vbDkLE", "M113 及同底盘", "（自述只到网格 / 贴图）"),
            ("4199D4", "Shchuka（Russian Guard DLC）", "Vehicle rigging"),
            ("V8JJwZ", "Mi-8AMTSh（Russian Guard DLC）", "Helicopter rigging"),
            ("JwvvXR", "T-62M（Russian Guard DLC）", "Vehicle rigging"),
        )
    ),
)


def rig_dict_keys() -> tuple[str, ...]:
    """词典里所有机型键（面板下拉 / 报错提示用）。"""
    return tuple(item.key for item in RIG_DICT)


def rig_dict_for(key: str) -> VehicleRig | None:
    """按件键 / slug / 别名找那一条（**大小写不敏感**；先精确、再别名、最后子串）。

    ⛔ 找不到就是 `None` —— **不是错**（大多数件没有这份清单）⇒ 调用方印「没有词典」，
    ⛔ 不许当成「缺失」判红（这是「外包商交付习惯」，不是游戏约束）。
    """
    needle = str(key or "").strip().lower()
    if not needle:
        return None
    for item in RIG_DICT:  # ① 精确（键 / 别名）
        names = (item.key, *item.aliases)
        if any(needle == str(n).lower() for n in names):
            return item
    # ② 子串（`BALT_XA180` 那条件键 / `DLC2_XA180EST/DLC2_XA180EST_GMG` 那个炮塔件 …）
    #    ⚠ 只让**长度 ≥ 6** 的名字参与子串 —— 短别名（`M109` / `RCV`）走子串会误命中
    #    **别的车型**，而「配错词典」比「没词典」坏得多（前者报的是一份假的命中）。
    for item in RIG_DICT:
        names = (item.key, *item.aliases)
        for name in names:
            low = str(name).lower()
            if len(low) >= 6 and (low in needle or needle in low):
                return item
    return None
