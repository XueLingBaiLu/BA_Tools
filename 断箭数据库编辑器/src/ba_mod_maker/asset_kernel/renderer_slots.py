"""`Renderer.m_Materials` 的**加槽 / 删槽**（变长档）＋ **材质级引用普查**（整包 PPtr）。

## 这一层收的是哪两件事

**① 渲染器材质槽数组**。`MeshRenderer`（classID 23）与 `SkinnedMeshRenderer`（137）的
字段树里都有一条：

    m_Materials : vector< PPtr<Material> >        <- **变长数组**（槽就是数组元素）

一个槽 = 一个子网格的材质。数组长度一变 ⇒ **长度必变** ⇒ 走 `writer.ObjectGrowth`
（Δ：对象搬到 node[0] 尾部、8 字节对齐、只改它那一条表项）。⛔ 不走等长覆盖。

**② 材质级引用普查**（缺口 B1 / 判据 M4）。`core/edit/refs.py::referencing_rows` 只管
**数据库行**；"谁指向这颗材质"在资产侧**没有尺子** —— 本模块补上：
`census_material` 把**整包每一个对象**解一遍、抓出所有 `(fileID=0, pathID=目标)` 的 PPtr，
连同**宿主 + 字段路径 + 数组下标**一起报回来。

⚠ **⛔ 别只看 prefab 闭包**：材质可能被渲染器槽、皮肤表 `_keyData`、别的 prefab 一起指着
（实测先例：履带料 **refs=9（8 台车）**，`kb/20` §1.1）—— 只看 prefab 那一棵会漏掉共享面。

**③ 地址级容器条目**（`AssetBundle.m_Container`）。catalog 那半张表在 `catalog_edit`，
包里这半张在 `m_Container` —— 「**新增一个能被地址取到的资产**」缺的就是这一格
（catalog 有、容器没有 ⇒ 地址解析得到包、`LoadAsset` 取不到东西）。见文件末那一节。

**④ 单位 prefab 的渲染器清单**（`unit_renderer_roster`）：`--unit <地址键>` 那条路上
「这台车有哪些渲染器」的**唯一出处** = prefab 上的 `UnitPrefabRoot.Renderers`。
真件一手读数（`_work/mat-unit/probe_roster.py`）：52 节点 / **13 渲染器**，名单 13 条、
逐一对应。⇒「改哪一颗渲染器」是一次**选择**：`resolve_unit_renderer` 只收名单里的
**整数 pathID**（⛔ 不收名字 —— 真件实测 `000_skinned_Chassis` 在名单里出现 2 次，
按名字指名必然二义）。名字 / 类型 / 槽只是**读数**。

## 删槽前的守卫（fail-closed：**必须一起改，否则拒绝**）

`remove_slot` 要求调用方把"**除了这一个槽之外**，还有哪些引用指着这颗材质"（`shared_with`，
就是普查给的那些引用）摆出来；每一条要么出现在 `handled` 里（= 调用方声明"这些已经一并处理"），
要么**整件事被拒绝**。三种情形都会被点出来：

* 同一个渲染器的**别的下标**指向同一颗材质（`m_Materials[2]` 与 `m_Materials[5]` 同理）；
* **别的渲染器 / 别的 prefab** 的槽指着它（共享面）；
* **皮肤表** `Skins.Data[..]._keyData[j]` 指着它（那半张表也要跟着改，见 `prefab_skins`）。

## 加了槽之后 / 删了槽之后的读数（J1 / J2）

* `SlotPlan.count_line()` = `m_Materials 条数 M -> M+1`（删则 M-1）；
* `verify_plan()` 从盘上读回数组**逐条**与期望比（写后复核）；
* 干跑与真写走**同一个** `writer` 计划（`plan_delta`），所以读数逐行相同。

## 落点与留白

* 写侧那一趟**只有一份**：`prefab_skins.run_write`（变长 -> `write_delta`，
  等长 -> `write_objects`）；本模块只出 `writer.ObjectGrowth`；
* ⛔ 不删材质对象（删对象要走 `ObjectRemoval` 那条链，并且**先**把引用清干净 —— 本模块给的是
  "谁指着它"这把尺子，不是删除那条路）；
* ⚠ **子网格数**这条约束（`m_Materials` 条数 vs Mesh 的 `m_SubMeshes`）本模块**不看**：
  Mesh 与渲染器的对应关系没有实测过（`kb/18` §9.9 的 LOD 切换策略也还是空洞）——
  留白写在这里，⛔ 不拿没量过的东西当判据。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone, prefab_skins, writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ASSETBUNDLE_CLASS_ID",
    "CONTAINER_FIELD",
    "MATERIALS_FIELD",
    "PRELOAD_TABLE_FIELD",
    "ContainerEntry",
    "ContainerPlan",
    "MaterialCensus",
    "MaterialRef",
    "RendererEntry",
    "SlotPlan",
    "add_container_entry",
    "add_slot",
    "census_material",
    "container_entries",
    "container_hosts",
    "container_paths",
    "install",
    "material_slots",
    "plan_add_container",
    "plan_add_slot",
    "plan_remove_container",
    "plan_remove_slot",
    "read_slots",
    "remove_container_entry",
    "remove_slot",
    "resolve_container_host",
    "resolve_unit_renderer",
    "roster_hint",
    "unit_renderer_roster",
    "verify_plan",
]

#: 渲染器上那条材质槽数组（`MeshRenderer` / `SkinnedMeshRenderer` 同名字段）。
MATERIALS_FIELD = "m_Materials"

#: `--renderer` 收的**只有**整数 pathID（渲染器 pathID 有负数）—— 名字一律不收（见
#: `resolve_unit_renderer` 的 A 案口径）。
_ID_TEXT = re.compile(r"-?\d+")

#: classID -> 类名（只进读数与判据一行，⛔ 不参与字节）。
_CLASS_NAMES: Mapping[int, str] = {
    1: "GameObject",
    4: "Transform",
    21: "Material",
    23: "MeshRenderer",
    28: "Texture2D",
    33: "MeshFilter",
    43: "Mesh",
    114: "MonoBehaviour",
    115: "MonoScript",
    137: "SkinnedMeshRenderer",
    142: "AssetBundle",
    213: "Sprite",
}


def _class_name(class_id: int) -> str:
    return _CLASS_NAMES.get(int(class_id), f"class{int(class_id)}")


# --------------------------------------------------------------------------- #
# 纯函数层：槽数组（零第三方依赖，可单独测）
# --------------------------------------------------------------------------- #
def material_slots(tree: Mapping[str, Any]) -> tuple[int, ...]:
    """**纯函数**：读一个渲染器的 `m_Materials`（pathID 元组，顺序就是槽序）。

    字段不在 / 不是序列 ⇒ 抛 `AssetError`，⛔ **不静默当空数组** —— 把"读法坏了"
    写成"这个渲染器没有材质"，下一步就是拿一条假的空数组去写盘（那是另一个语义）。
    """
    raw = tree.get(MATERIALS_FIELD)
    if raw is None:
        raise AssetError(
            f"这个对象没有 {MATERIALS_FIELD}（不是 MeshRenderer / SkinnedMeshRenderer？）",
            hint="槽数组是 Renderer 才有的字段 —— 读到的对象 classID 不对就别往下写",
        )
    seq = prefab_skins._sequence_of(  # noqa: SLF001 -- PPtr 读法只有一份（见模块头）
        raw, where=MATERIALS_FIELD, what="PPtr<Material>[]"
    )
    return tuple(
        prefab_skins._pptr_id(  # noqa: SLF001 -- 同上
            item, where=f"{MATERIALS_FIELD}[{index}]"
        )
        for index, item in enumerate(seq)
    )


def set_material_slots(tree: Mapping[str, Any], slots: Sequence[int]) -> dict[str, Any]:
    """**纯函数**：把槽数组换成 `slots`（入参 `tree` **不被改**）。

    没动过的槽**原样照抄**（深拷旧行）；新增 / 改了的那一格才按 `clone.pptr` 重拼 ——
    这样一次加槽的字节差就是那一格。
    """
    raw = tree.get(MATERIALS_FIELD)
    if raw is None:
        raise AssetError(f"这个对象没有 {MATERIALS_FIELD}", hint="同 material_slots")
    pool: dict[int, list[Mapping[str, Any]]] = {}
    for value, row in zip(
        material_slots(tree),
        prefab_skins._sequence_of(  # noqa: SLF001 -- 同一把读法
            raw, where=MATERIALS_FIELD, what="PPtr<Material>[]"
        ),
        strict=True,
    ):
        pool.setdefault(int(value), []).append(row)
    rows: list[dict[str, Any]] = []
    for value in slots:
        bucket = pool.get(int(value))
        rows.append(copy.deepcopy(dict(bucket.pop())) if bucket else clone.pptr(int(value)))
    new_tree = dict(tree)
    new_tree[MATERIALS_FIELD] = rows
    return new_tree


def add_slot(tree: Mapping[str, Any], material: int, *, index: int | None = None) -> dict[str, Any]:
    """**纯函数**：加一个槽 —— 条数 M -> M+1。

    `index=None` 追加到末尾（真件里子网格顺序就是槽序，追加不动老下标）；
    给了 `index` 就插在那一位（0..M 都合法）。
    """
    if int(material) <= 0:
        raise AssetError(
            f"要加的材质 pathID={int(material)} 不是正数",
            hint="槽里放的是 PPtr<Material> 的 pathID；0 = 空引用"
            "（那是「没有材质」，不是加一个槽）",
        )
    current = material_slots(tree)
    at = len(current) if index is None else int(index)
    if at < 0 or at > len(current):
        raise AssetError(
            f"插入位置 {at} 越界（现有 {len(current)} 个槽，合法范围 0..{len(current)}）",
            hint="槽位是数组下标 —— 越界插入会把顺序写乱",
        )
    new = (*current[:at], int(material), *current[at:])
    return set_material_slots(tree, new)


def remove_slot(
    tree: Mapping[str, Any],
    index: int,
    *,
    shared_with: Sequence[str] = (),
    handled: Sequence[str] = (),
) -> dict[str, Any]:
    """**纯函数**：删一个槽 —— 条数 M -> M-1。

    `shared_with` = 引用普查给出的「**除这一个槽之外**，还指着这颗材质的那些引用」；
    只要有一条没出现在 `handled` 里 ⇒ **整件事被拒绝**（fail-closed）：
    那些引用要么一起改，要么这次别删。

    ⚠ 删掉下标 `i` 之后，`i` 之后的所有槽**整体前移一位** —— 引用清单里的下标是**删之前**的，
    调用方要按删之后的顺序重算（本函数只负责"这一格没了"）。
    """
    current = material_slots(tree)
    at = int(index)
    if at < 0 or at >= len(current):
        raise AssetError(
            f"要删的槽下标 {at} 越界（现有 {len(current)} 个槽：{list(current)}）",
            hint="槽位是数组下标（0 起）—— ⛔ 不许按「看起来像」的那一格删",
        )
    missing = [text for text in shared_with if str(text) not in {str(x) for x in handled}]
    if missing:
        raise AssetError(
            f"删第 {at} 个槽（材质 pathID={current[at]}）之前，还有 {len(missing)} 条引用"
            f"指着它：{missing[:6]}",
            hint="这些引用必须**一起改**（放进 handled）或这次别删 —— 只删槽会把它们指向一个"
            "这个渲染器不再渲染的材质（共享面：实测履带料 refs=9）",
        )
    new = (*current[:at], *current[at + 1 :])
    return set_material_slots(tree, new)


# --------------------------------------------------------------------------- #
# 单位 prefab 的渲染器清单：`--unit` 那一跳（unit_key -> 渲染器 pathID）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RendererEntry:
    """单位 prefab 的**一个渲染器**：`UnitPrefabRoot.Renderers` 里的一条。

    `name` / `kind` / `slots` 只是**读数**（名字 = 渲染器所在 GameObject 的名，
    走产品自己的读侧 `prefab.read_prefab_tree`）。合成件 / 精简件没有 Transform 树 ⇒
    这三个字段**读不出来**（`name` / `kind` 空、`slots=None`）——
    ⛔ 定位只用 `path_id`（名单给的），**不拿名字当依据**。
    """

    path_id: int
    name: str = ""
    kind: str = ""
    #: 当前 `m_Materials`；`None` = **读不出**（与「真的是 0 个槽」不是一件事）。
    slots: tuple[int, ...] | None = None

    def label(self) -> str:
        """人话：`SkinnedMeshRenderer#-8003744623352085720『Chassis』`。"""
        kind = self.kind or "Renderer"
        name = f"『{self.name}』" if self.name else "『名字读不出』"
        return f"{kind}#{self.path_id}{name}"

    def summary(self) -> str:
        slots = "读不出" if self.slots is None else str(len(self.slots))
        return f"{self.label()}  槽={slots}"


def _prefab_renderer_details(source: Any, asset_path: str | None) -> dict[int, RendererEntry]:
    """**只读**：尽力从 prefab 树读每个渲染器的名字 / 类型 / 槽（读不出 ⇒ 空表，⛔ 不抛）。

    走产品自己的读侧 `prefab.read_prefab_tree`（⛔ 不另写一遍遍历 —— 「逻辑只有一份」）。
    真件一手读数（`_work/mat-unit/probe_renderers.py`，routea2real 37,892,170 B）：
    52 节点 / 13 渲染器，名字就是 GameObject 名（Chassis / RU_TanktrakL / … / lod_1x1x1）。

    读不出**只影响读数**：合成件与精简件没有 Transform 树，这条路会抛；
    那时名字留空、`slots=None`，定位照样按名单里的 pathID 走。
    """
    if source is None or not asset_path:
        return {}
    from ba_mod_maker.asset_kernel import prefab  # noqa: PLC0415 -- 与 unitypy_source 共用一个环

    try:
        tree = prefab.read_prefab_tree(source, asset_path=str(asset_path))
    except Exception:  # noqa: BLE001 -- 名字是读数：读不出不许把定位一起废掉
        return {}
    return {
        int(item.component_id): RendererEntry(
            path_id=int(item.component_id),
            name=str(item.name or ""),
            kind=str(item.kind or ""),
            slots=tuple(int(m) for m in item.material_ids),
        )
        for item in tree.renderers
    }


def unit_renderer_roster(
    bundle_path: str | Path,
    uroot_path_id: int,
    *,
    source: Any = None,
    asset_path: str | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> tuple[RendererEntry, ...]:
    """**只读**：「这个单位有哪些渲染器」的**唯一出处** —— `UnitPrefabRoot.Renderers`。

    顺序照名单原样。它就是要动的那颗 prefab 自己的名单，所以「改哪一颗渲染器」是一次
    **选择**，不是可以猜的量：

    * 真件一手读数（`_work/mat-unit/probe_roster.py`，routea2real 37,892,170 B）：
      prefab 52 节点 / **13 渲染器**，名单 **13** 条、**逐一对应**（名单里不在 prefab 的 0 条、
      prefab 里不在名单的 0 条）——与 `kb/20` §1.6 #7 的读数一致；
    * 名字那一列是**读数**：真件里**会重名**（`000_skinned_Chassis` 在名单里出现 2 次）⇒
      名字**不参与定位**（`--renderer` 只收 pathID，见 `resolve_unit_renderer` 的 A 案口径）。
    """
    obj = prefab_skins.read_package_object(
        bundle_path, int(uroot_path_id), common_strings=common_strings
    )
    ids = prefab_skins._prefab_renderers(  # noqa: SLF001 -- 名单读法只有一份（prefab_skins）
        obj.tree
    )
    details = _prefab_renderer_details(source, asset_path)
    return tuple(details.get(int(pid), RendererEntry(path_id=int(pid))) for pid in ids)


def roster_hint(roster: Sequence[RendererEntry]) -> str:
    """候选名单一行（最多 8 条 ＋ 总数）—— 守卫报错一律把它摆到眼前。"""
    shown = "；".join(entry.summary() for entry in roster[:8])
    tail = f"；…共 {len(roster)} 条" if len(roster) > 8 else ""
    return f"这台车的渲染器名单（{len(roster)} 条）：{shown}{tail}"


def resolve_unit_renderer(
    roster: Sequence[RendererEntry], selector: str | int | None = None
) -> RendererEntry:
    """**纯函数**：从名单里挑出**被指名**的那一个渲染器（⛔ 一条也不猜）。

    口径（2026-10-11 **A 案**，Lead 定）：

    * **必须指名**：`--renderer <pathID>` 必给，整数 pathID（渲染器 pathID **有负数**，
      真件实测 `-8003744623352085720`）—— ⛔ 没有「名单只有一条就自动挑」那条回落；
    * **⛔ 不收名字**：名字只是**读数**。真件实测名单里 `000_skinned_Chassis` 出现 2 次 ⇒
      按名字指名**必然有二义**，而产品口径是「二义就拒」——那还不如不给这个名字入口；
      收到非整数一律抛，并把名单摆出来让人照着抄 pathID；
    * 不在名单里 ⇒ 抛（`UnitPrefabRoot.Renderers` 空 / 读不出，同样抛）。
    """
    if not roster:
        raise AssetError(
            "这个单位的 UnitPrefabRoot.Renderers 名单是空的 ⇒ 没有可指名的渲染器",
            hint="名单为空 = 这颗 prefab 没登记渲染器（合成件 / 精简件常见）——"
            " 要动渲染器就指名它：--bundle <包> ＋ --path-id <渲染器 pathID>",
        )
    if selector is None or not str(selector).strip():
        raise AssetError(
            "--renderer 要指名一颗渲染器（这台车的渲染器不是唯一的量）",
            hint=roster_hint(roster),
        )
    text = str(selector).strip()
    if not _ID_TEXT.fullmatch(text):
        raise AssetError(
            f"--renderer 要的是渲染器 **pathID**（整数），收到 {text!r}",
            hint="名字只当读数印出来（真件实测会重名：000_skinned_Chassis ×2）——"
            " 照名单里的 pathID 填。" + roster_hint(roster),
        )
    wanted = int(text, 10)
    hit = next((entry for entry in roster if entry.path_id == wanted), None)
    if hit is None:
        raise AssetError(
            f"渲染器 pathID={wanted} 不在这个单位的 {len(roster)} 条名单里",
            hint=roster_hint(roster),
        )
    return hit


# --------------------------------------------------------------------------- #
# 材质级引用普查（整包 PPtr）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MaterialRef:
    """一条「谁指着这颗材质」：宿主 ＋ 字段路径（含数组下标）。"""

    owner_path_id: int
    owner_class_id: int
    owner_class: str
    owner_name: str
    field: str
    file_id: int
    path_id: int

    @property
    def index(self) -> int:
        """数组下标（不在数组里就是 -1）—— 从字段路径的尾巴上读。"""
        head, sep, tail = self.field.rpartition("[")
        if sep and tail.endswith("]"):
            try:
                return int(tail[:-1])
            except ValueError:
                return -1
        return -1

    def label(self) -> str:
        """稳定的一行（`handled` 里就写它）—— 宿主标识 ＋ 字段路径。"""
        return f"{self.owner_class}#{self.owner_path_id}.{self.field}"

    def summary(self) -> str:
        name = f"『{self.owner_name}』" if self.owner_name else ""
        return f"{self.label()}{name} -> 材质 pathID={self.path_id}"


@dataclass(frozen=True, slots=True)
class MaterialCensus:
    """**整包**逐对象 PPtr 普查里、指向目标材质的那一份读数。"""

    bundle: str
    bundle_name: str
    material_path_id: int
    material_name: str
    in_package: bool
    objects: int
    decoded: int
    undecodable: tuple[tuple[int, int], ...]
    refs: tuple[MaterialRef, ...]

    @property
    def ok(self) -> bool:
        """解不了树的对象为 0 才算干净 —— 那是这把尺子**诚实性的边界**。"""
        return not self.undecodable

    @property
    def owners(self) -> tuple[int, ...]:
        return tuple(sorted({ref.owner_path_id for ref in self.refs}))

    def others(
        self, *, owner_path_id: int | None = None, field: str | None = None
    ) -> tuple[MaterialRef, ...]:
        """排除指定那一条（= "除了我这一格，还有谁指着它"）。"""
        return tuple(
            ref
            for ref in self.refs
            if not (ref.owner_path_id == owner_path_id and ref.field == field)
        )

    def lines(self) -> tuple[str, ...]:
        where = "在包里" if self.in_package else "**不在包里**（这个包没有这颗材质）"
        out = [
            f"材质 pathID={self.material_path_id}『{self.material_name}』{where}"
            f"；被 {len(self.refs)} 条 PPtr 指着（宿主 {len(self.owners)} 个）",
            f"  整包普查：对象 {self.objects} 个，能解码 {self.decoded}"
            + (f"；解不了 {list(self.undecodable)}" if self.undecodable else "（全部可解码）"),
        ]
        out.extend(f"  ← {ref.summary()}" for ref in self.refs[:12])
        if len(self.refs) > 12:
            out.append(f"  …（还有 {len(self.refs) - 12} 条）")
        return tuple(out)


def census_material(
    bundle_path: str | Path,
    material_path_id: int,
    *,
    common_strings: Mapping[int, str] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> MaterialCensus:
    """**只读**：整包逐对象普查 —— 谁指着 `material_path_id` 这颗材质。

    口径照 `unit_bundle.audit_package_pptrs`（同一把整包尺子：**每一个**对象都解，
    包括 payload 对象），差别是本函数**按目标材质收敛**并保留**宿主的字段路径**。

    ⚠ 代价：真件 units 包有 16 万个对象 ⇒ 这是一次**全量扫描**（分钟级）。
    它是「删槽 / 删材质」的前置动作，也是"这颗材质是不是共享面"的唯一判据 ——
    `progress(done, total)` 给了就按 512 个一报，方便长跑时看得见。
    """
    from ba_mod_maker.asset_kernel import metadata_writer  # noqa: PLC0415 -- 只有普查要它

    src = Path(bundle_path)
    common = common_strings or clone.unitypy_common_strings()
    target = int(material_path_id)
    refs: list[MaterialRef] = []
    undecodable: dict[int, int] = {}
    decoded = 0
    material_name = ""
    with ufs.UfsReader(src) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
        meta = metadata_writer.parse_serialized_metadata(
            reader.read_range(node0.offset, info.data_offset)
        )
        types = list(meta.types)
        class_of = {index: entry.class_id for index, entry in enumerate(types)}
        pool = clone.ref_type_pool(meta, common_strings=common)
        nodes_cache: dict[int, Any] = {}

        def nodes_of(type_id: int):
            if type_id not in nodes_cache:
                tree = types[type_id].type_tree
                import struct  # noqa: PLC0415 -- 只为拼那 8 个字节的表头

                nodes_cache[type_id] = clone.read_type_nodes(
                    struct.pack("<ii", tree.node_count, tree.string_buffer_size)
                    + tree.nodes
                    + tree.strings,
                    common_strings=common,
                )
            return nodes_cache[type_id]

        ids = {int(row[1]) for row in info.objects}
        total = len(info.objects)
        for done, row in enumerate(info.objects):
            if progress is not None and done % 512 == 0:
                progress(done, total)
            path_id = int(row[1])
            class_id = int(class_of.get(row[4], -1))
            # collect_pptrs 把 owner 当成**字段路径的前缀**（它的 field = owner + "." + 路径），
            # 所以这里先把前缀切掉 —— 不切就会得到
            # "MeshRenderer#400.MeshRenderer:400.m_Materials[0]" 这种双份宿主
            # （而且 others() 的字段比对会因此永远配不上）。
            prefix = f"{_class_name(class_id)}:{path_id}."
            payload = bytes(reader.read_range(node0.offset + info.data_offset + row[2], row[3]))
            try:
                obj = clone.read_object(payload, nodes_of(row[4]), ref_types=pool)
                found = clone.collect_pptrs(
                    obj, nodes_of(row[4]), owner=f"{_class_name(class_id)}:{path_id}"
                )
            except Exception:  # noqa: BLE001 -- 解不了的对象单独计数，⛔ 不当成「没有 PPtr」
                class_id = int(class_of.get(row[4], -1))
                undecodable[class_id] = undecodable.get(class_id, 0) + 1
                continue
            decoded += 1
            name = str(obj.get("m_Name") or "")
            if path_id == target:
                material_name = name
            for item in found:
                if int(item.file_id) != 0 or int(item.path_id) != target:
                    continue
                raw_field = item.field
                field_path = (
                    raw_field[len(prefix) :] if raw_field.startswith(prefix) else raw_field
                )
                refs.append(
                    MaterialRef(
                        owner_path_id=path_id,
                        owner_class_id=class_id,
                        owner_class=_class_name(class_id),
                        owner_name=name,
                        field=field_path,
                        file_id=int(item.file_id),
                        path_id=int(item.path_id),
                    )
                )
    return MaterialCensus(
        bundle=str(src),
        bundle_name=node0.path,
        material_path_id=target,
        material_name=material_name,
        in_package=target in ids,
        objects=total,
        decoded=decoded,
        undecodable=tuple(sorted(undecodable.items())),
        refs=tuple(refs),
    )


# --------------------------------------------------------------------------- #
# 包级计划（干跑）：加槽 / 删槽
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SlotPlan:
    """一次材质槽改动的干跑结果（⛔ 一个字节都没写）。"""

    operation: str
    bundle: Path
    bundle_name: str
    renderer_path_id: int
    renderer_name: str
    material: int
    slots_before: tuple[int, ...]
    slots_after: tuple[int, ...]
    #: 槽数组长度必变 ⇒ 恒为 `writer.ObjectGrowth`（留成联合类型只为与 `ChangePlan` 对齐）。
    change: writer.ObjectChange | writer.ObjectGrowth
    writeback: writer.WritebackPlan
    census: MaterialCensus | None = None
    handled: tuple[str, ...] = ()
    guard_lines: tuple[str, ...] = ()
    game_root: Path | None = None
    catalog_path: Path | None = None

    @property
    def level(self) -> str:
        return self.change.__class__.__name__

    def size_before(self) -> int:
        """改前对象字节数（从 writer 的计划行里读回来 —— 那是**真值**）。"""
        for row in self.writeback.rows:
            if row.path_id == self.renderer_path_id:
                return int(row.old_bytes)
        return 0

    def count_line(self) -> str:
        """**判据一行**（J2）：`m_Materials` 条数 M -> M+1（删则 M-1）。"""
        return (
            f"m_Materials 条数 {len(self.slots_before)} -> {len(self.slots_after)}"
            f"（{self.operation}，材质 pathID={self.material}）"
        )

    def criterion_lines(self) -> tuple[str, ...]:
        catalog = self.catalog_path if self.catalog_path is not None else "未同步（离线件）"
        return (
            f"[材质槽{self.operation}] {self.bundle_name}：Renderer『{self.renderer_name}』"
            f" pathID={self.renderer_path_id}   {self.count_line()}",
            f"  槽数组 {list(self.slots_before)} -> {list(self.slots_after)}",
            f"  对象字节 {self.size_before():,} -> {len(self.change.payload):,} B"
            f"（{len(self.change.payload) - self.size_before():+,}）   写回档：{self.level}"
            "（变长 ⇒ Δ 搬到 node[0] 尾部）",
            f"  catalog：{catalog}",
            *self.guard_lines,
            *self.writeback.criterion_lines(),
        )

    def summary(self) -> str:
        return (
            f"{self.bundle_name}：材质槽{self.operation}（{len(self.slots_before)} -> "
            f"{len(self.slots_after)} 个，材质 {self.material}，{self.level}）"
        )

    def patch_label(self) -> str:
        return (
            f"材质槽{self.operation}：{self.bundle_name} {self.renderer_name} "
            f"pathID={self.renderer_path_id}（{len(self.slots_before)} -> {len(self.slots_after)}）"
        )


def _package_ids(bundle: Path) -> tuple[int, ...]:
    with ufs.UfsReader(bundle) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
    return tuple(sorted({int(row[1]) for row in info.objects}))


def _make_slot_plan(
    bundle_path: str | Path,
    path_id: int,
    *,
    operation: str,
    apply: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    material: int,
    guard: Callable[[Mapping[str, Any], Path], tuple[str, ...]],
    catalog_path: str | Path | None,
    game_root: str | Path | None,
    common_strings: Mapping[int, str] | None,
    census: MaterialCensus | None = None,
    handled: Sequence[str] = (),
) -> SlotPlan:
    """干跑：读对象 -> 纯函数改树 -> ②③④ 自证 -> 守卫 -> `writer` 那条写回计划（⛔ 只读）。"""
    root = Path(game_root) if game_root is not None else None
    catalog = prefab_skins._resolve_catalog(root, catalog_path)  # noqa: SLF001 -- 同一份口径
    obj = prefab_skins.read_package_object(
        bundle_path, path_id, common_strings=common_strings, catalog_path=catalog
    )
    before = material_slots(obj.tree)
    new_tree = apply(obj.tree)
    after = material_slots(new_tree)
    guard_lines = guard(new_tree, obj.bundle)
    payload = prefab_skins.serialize_package_tree(obj, new_tree)
    got = material_slots(clone.read_object(payload, obj.nodes))
    if got != after:
        raise AssetError(
            f"写出来的字节读回去是 {len(got)} 个槽，期望 {len(after)} 个",
            hint="序列化没把新数组写进去 —— 这一版不许写回",
        )
    if payload == obj.payload:
        raise AssetError(
            f"改完的字节与原字节**一模一样** ⇒ 空改动（{operation}）",
            hint="空改动不该写盘：检查一下是不是把同一件事做了两遍",
        )
    if len(payload) == len(obj.payload):
        raise AssetError(
            f"槽数组变过、对象字节却没变长（{len(payload):,} B）—— 走错了档",
            hint="槽数组是变长数组：增删必然改长度；等长说明改的不是这条数组（⛔ 别硬写）",
        )
    change = writer.ObjectGrowth(
        path_id=obj.path_id, payload=payload, name=obj.name or obj.type_name,
        type_name=obj.type_name,
    )
    writeback = writer.plan_delta(
        obj.bundle, writer.DeltaChange(growths=(change,)), catalog_path=catalog
    )
    return SlotPlan(
        operation=operation,
        bundle=obj.bundle,
        bundle_name=obj.bundle_name,
        renderer_path_id=obj.path_id,
        renderer_name=obj.name or obj.type_name,
        material=int(material),
        slots_before=before,
        slots_after=after,
        change=change,
        writeback=writeback,
        census=census,
        handled=tuple(str(text) for text in handled),
        guard_lines=guard_lines,
        game_root=root,
        catalog_path=catalog,
    )


def _guard_add(tree: Mapping[str, Any], bundle: Path, material: int) -> tuple[str, ...]:
    ids = _package_ids(bundle)
    if int(material) not in set(ids):
        raise AssetError(
            f"要加的材质 pathID={int(material)} 不在这个包里（共 {len(ids)} 个对象）",
            hint="槽里放包外 pathID = 悬空 PPtr（游戏取到空指针）—— 先把这个材质加进包，再加槽",
        )
    slots = material_slots(tree)
    lines = [f"引用守卫：材质 pathID={int(material)} 在包内；这个渲染器有 {len(slots)} 个槽"]
    if not slots:
        lines.append("⚠ 这个渲染器一个材质槽都不剩（渲染行为未验证，如实记）")
    return tuple(lines)


def plan_add_slot(
    bundle_path: str | Path,
    path_id: int,
    material: int,
    *,
    index: int | None = None,
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> SlotPlan:
    """**干跑**：给渲染器加一个材质槽 —— 条数 M -> M+1。

    ⛔ 不做整包普查（加槽不会让别的引用失效）；要看"这颗材质被谁用"就单独跑
    `census_material`（那是分钟级的全量扫描）。
    """
    return _make_slot_plan(
        bundle_path,
        path_id,
        operation="新增",
        apply=lambda tree: add_slot(tree, material, index=index),
        material=int(material),
        guard=lambda tree, bundle: _guard_add(tree, bundle, int(material)),
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
    )


def plan_remove_slot(
    bundle_path: str | Path,
    path_id: int,
    index: int,
    *,
    handled: Sequence[str] = (),
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> SlotPlan:
    """**干跑**：删一个材质槽 —— 条数 M -> M-1。

    ⚠ **先普查**：这一次调用**一定**先跑 `census_material`（删槽的前置，⛔ 不许跳），
    把「除了这一格，还有谁指着这颗材质」摆进 `shared_with` ——
    那些引用没被 `handled` 覆盖就**拒绝**（fail-closed，见 `remove_slot`）。
    """
    obj = prefab_skins.read_package_object(bundle_path, path_id, common_strings=common_strings)
    current = material_slots(obj.tree)
    at = int(index)
    if at < 0 or at >= len(current):
        raise AssetError(
            f"要删的槽下标 {at} 越界（现有 {len(current)} 个槽：{list(current)}）",
            hint="槽位是数组下标（0 起）—— ⛔ 不许按'看起来像'的那一格删",
        )
    target = int(current[at])
    census = census_material(obj.bundle, target, common_strings=common_strings)
    this_field = f"{MATERIALS_FIELD}[{at}]"
    shared = [
        ref.label()
        for ref in census.others(owner_path_id=obj.path_id, field=this_field)
    ]

    def guard(tree: Mapping[str, Any], bundle: Path) -> tuple[str, ...]:
        lines = [
            f"引用普查（整包）：材质 pathID={target}『{census.material_name}』被 "
            f"{len(census.refs)} 条 PPtr 指着（宿主 {len(census.owners)} 个，"
            f"解不了的对象 {sum(n for _c, n in census.undecodable)} 个）",
            f"  除 m_Materials[{at}] 之外还有 {len(shared)} 条：{shared[:4] or '（无）'}",
        ]
        if shared:
            lines.append(f"  其中已声明一并处理（handled）{len(handled)} 条")
        return tuple(lines)

    return _make_slot_plan(
        bundle_path,
        path_id,
        operation="删除",
        apply=lambda tree: remove_slot(tree, at, shared_with=shared, handled=handled),
        material=target,
        guard=guard,
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
        census=census,
        handled=handled,
    )


# --------------------------------------------------------------------------- #
# 写侧（共用 prefab_skins.run_write 那一趟）＋ 写后复核
# --------------------------------------------------------------------------- #
def install(
    plan: SlotPlan | ContainerPlan, *, project_root: str | Path, dry_run: bool = False
) -> writer.WriteReport:
    """执行一次材质槽 / 容器条目改动（`dry_run=True` 只算不写）—— 写侧口径见
    `prefab_skins.run_write`（与皮肤表**共用同一趟**，⛔ 不另开写盘的路）。"""
    return prefab_skins.run_write(plan, project_root=project_root, dry_run=dry_run)


def read_slots(bundle_path: str | Path, path_id: int) -> tuple[int, ...]:
    """**只读**：从盘上把 `m_Materials` 读回来（写后复核 / J2 读数用）。"""
    return material_slots(prefab_skins.read_package_object(bundle_path, path_id).tree)


def verify_plan(plan: SlotPlan | ContainerPlan) -> tuple[str, ...]:
    """**写后复核**：从盘上读回这条数组，逐条与期望比（不等 ⇒ 抛）。

    `ContainerPlan` 走的是同一把尺子的**另一个落点**（`m_Container` 那一条）。
    """
    if isinstance(plan, ContainerPlan):
        got_entries = container_entries(
            prefab_skins.read_package_object(plan.bundle, plan.container_path_id).tree
        )
        if got_entries != plan.entries_after:
            raise AssetError(
                f"写后读回 {len(got_entries)} 条容器条目，期望 {len(plan.entries_after)} 条",
                hint="读回：" + str([entry.summary() for entry in got_entries]),
            )
        return (
            f"m_Container 条数 {len(plan.entries_before)} -> {len(plan.entries_after)}"
            f"（读回 {len(got_entries)}，逐条与期望相同）",
            *[f"  {entry.summary()}" for entry in got_entries],
        )
    got = read_slots(plan.bundle, plan.renderer_path_id)
    if got != plan.slots_after:
        raise AssetError(
            f"写后读回 {len(got)} 个槽 {list(got)}，期望 {len(plan.slots_after)} 个 "
            f"{list(plan.slots_after)}",
            hint="写回没落对 —— 先看 writer 的写后复核那一块",
        )
    return (
        f"m_Materials 条数 {len(plan.slots_before)} -> {len(plan.slots_after)}"
        f"（读回 {len(got)}，逐条与期望相同）",
        f"  读回槽数组：{list(got)}",
    )


# --------------------------------------------------------------------------- #
# 整包级（**地址层**）：`AssetBundle.m_Container` 的新增 / 删除
# --------------------------------------------------------------------------- #
#: 容器表（地址 -> 资产 PPtr）—— catalog 那半张表在 `catalog_edit`，这一半在包里。
CONTAINER_FIELD = "m_Container"
#: 预载表（包加载时顺带载入哪些对象）—— 与容器表**成对**存在。
PRELOAD_TABLE_FIELD = "m_PreloadTable"
#: `AssetBundle` 的 classID。
ASSETBUNDLE_CLASS_ID = 142


@dataclass(frozen=True, slots=True)
class ContainerEntry:
    """`m_Container` 的一条：**地址** -> (资产 pathID ＋ 预载窗口)。"""

    asset_path: str
    path_id: int
    preload_index: int
    preload_size: int

    def summary(self) -> str:
        return (
            f"{self.asset_path!r} -> pathID={self.path_id}"
            f"（preload [{self.preload_index}, {self.preload_index + self.preload_size})）"
        )


def _container_rows(tree: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = tree.get(CONTAINER_FIELD)
    if raw is None:
        raise AssetError(
            f"这个对象没有 {CONTAINER_FIELD}（不是 AssetBundle？）",
            hint="容器条目挂在 AssetBundle（classID 142）上 —— ⛔ 不是 prefab / Material",
        )
    seq = prefab_skins._sequence_of(  # noqa: SLF001 -- 序列读法只有一份（见模块头）
        raw, where=CONTAINER_FIELD, what="AssetBundleEntry[]"
    )
    return tuple(seq)


def container_entries(tree: Mapping[str, Any]) -> tuple[ContainerEntry, ...]:
    """**纯函数**：读一条 `AssetBundle.m_Container`（形状不对就抛，⛔ 不静默当空表）。

    形状取自真件与 `builder.assetbundle_fields`（产品自己造包时用的那一份）：
    `{first: 地址, second: {preloadIndex, preloadSize, asset: PPtr}}`。
    """
    out: list[ContainerEntry] = []
    for index, row in enumerate(_container_rows(tree)):
        where = f"{CONTAINER_FIELD}[{index}]"
        entry = prefab_skins._mapping_of(  # noqa: SLF001 -- 映射读法只有一份
            row, where=where, what="AssetBundleEntry"
        )
        path = entry.get("first")
        if not isinstance(path, str) or not path:
            raise AssetError(
                f"{where}.first 不是非空字符串（读到 {path!r}）",
                hint="first = 地址（catalog 的 m_InternalIds 那条），例 Assets/Mods/My/icon.png",
            )
        info = prefab_skins._mapping_of(  # noqa: SLF001 -- 同一把读法
            entry.get("second"), where=f"{where}.second", what="AssetInfo"
        )
        out.append(
            ContainerEntry(
                asset_path=path,
                path_id=prefab_skins._pptr_id(  # noqa: SLF001 -- PPtr 读法只有一份
                    info.get("asset"), where=f"{where}.second.asset"
                ),
                preload_index=int(info.get("preloadIndex", 0) or 0),
                preload_size=int(info.get("preloadSize", 0) or 0),
            )
        )
    return tuple(out)


def container_paths(tree: Mapping[str, Any]) -> tuple[str, ...]:
    """**纯函数**：容器里那几条地址（顺序照原样）。"""
    return tuple(entry.asset_path for entry in container_entries(tree))


def add_container_entry(
    tree: Mapping[str, Any], *, asset_path: str, path_id: int
) -> dict[str, Any]:
    """**纯函数**：加一条地址级容器条目（末尾追加 ＋ 预载表尾追加一格）。

    三条 fail-closed 守卫：

    * **地址已经在容器里** ⇒ 抛（同一条地址两条 = 游戏取到哪一个说不清）；
    * **资产名（地址最后一段）与既有条目撞名** ⇒ 抛 —— `AssetBundle.LoadAsset(name)`
      是按**文件名**匹配的（真件形状：catalog 里的 key 是裸名、资产路径是 `Assets/…`），
      两条同名条目 ⇒ 取到哪一个是未定义的；
    * 地址是空的 / `path_id` 不是正数 ⇒ 抛。

    ⛔ 只往 `m_PreloadTable` **尾部追加**一格：既有条目的 `preloadIndex` 是
    前缀下标，追加不动它们（⛔ 不重编号 —— 那是 `writer._clean_ab_refs` 摘元素时才做的事）。
    """
    text = str(asset_path).strip()
    if not text:
        raise AssetError(
            "地址是空的",
            hint="容器条目的 first = 地址（catalog 的 m_InternalIds 那条），"
            "例 Assets/Mods/My/icon.png",
        )
    if int(path_id) <= 0:
        raise AssetError(
            f"资产 pathID={int(path_id)} 不是正数",
            hint="容器条目指向包里的一个对象；0 = 空引用（那是「没有资产」，不是加一条）",
        )
    entries = container_entries(tree)
    if any(entry.asset_path == text for entry in entries):
        raise AssetError(
            f"容器里已经有这条地址：{text!r}（现有 {len(entries)} 条）",
            hint="同一条地址两条 ⇒ 游戏取到哪一个说不清；要改指向请**先删再增**"
            "（两个动作各自可撤）",
        )
    taken = {entry.asset_path.rpartition("/")[2]: entry.asset_path for entry in entries}
    name = text.rpartition("/")[2]
    if name in taken:
        raise AssetError(
            f"资产名 {name!r} 与既有条目撞名：{taken[name]!r}",
            hint="AssetBundle.LoadAsset 按**文件名**匹配 —— 两条同名条目在游戏里取到哪一个是"
            "未定义的；要加就换一个文件名（或先删掉旧的那条）",
        )
    preload = [dict(item) if isinstance(item, Mapping) else item
               for item in (tree.get(PRELOAD_TABLE_FIELD) or [])]
    new_tree = dict(tree)
    new_tree[CONTAINER_FIELD] = [
        *_container_rows(tree),
        {
            "first": text,
            "second": {
                "preloadIndex": len(preload),
                "preloadSize": 1,
                "asset": clone.pptr(int(path_id)),
            },
        },
    ]
    new_tree[PRELOAD_TABLE_FIELD] = [*preload, clone.pptr(int(path_id))]
    return new_tree


def remove_container_entry(
    tree: Mapping[str, Any], *, asset_path: str
) -> tuple[dict[str, Any], ContainerEntry]:
    """**纯函数**：摘掉一条地址级容器条目（返回 新树 ＋ 被摘掉的那条）。

    ⛔ **`m_PreloadTable` 一个字节都不动**：预载表是"包加载时顺带载入哪些对象"的
    优化，摘了容器条目之后它就是一条没人引用的预载项（多载一个对象，⛔ 不是悬空引用）。
    反过来重编号会动到**别人**的 `preloadIndex` —— 那是 `writer._clean_ab_refs`
    删对象时才敢做的事（它自带"跨过被摘槽位就抛"的 fail-closed）。
    """
    text = str(asset_path).strip()
    entries = container_entries(tree)
    hits = [index for index, entry in enumerate(entries) if entry.asset_path == text]
    if not hits:
        raise AssetError(
            f"容器里没有这条地址：{text!r}（现有 {len(entries)} 条："
            f"{[entry.asset_path for entry in entries][:6]}）",
            hint="删一条不存在的地址会静默通过 —— 本函数不干那件事（先核容器里那几条）",
        )
    rows = list(_container_rows(tree))
    removed = entries[hits[0]]
    del rows[hits[0]]
    new_tree = dict(tree)
    new_tree[CONTAINER_FIELD] = rows
    return new_tree, removed


# --------------------------------------------------------------------------- #
# 包级：找那个 AssetBundle 对象 ＋ 干跑计划（走与材质槽**同一条**写侧）
# --------------------------------------------------------------------------- #
def container_hosts(bundle_path: str | Path) -> tuple[tuple[int, str], ...]:
    """**只读**：包里所有 `AssetBundle`（classID 142）对象的 `(pathID, m_Name)`。"""
    src = Path(bundle_path)
    common = clone.unitypy_common_strings()
    out: list[tuple[int, str]] = []
    with ufs.UfsReader(src) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
        meta = bytes(reader.read_range(node0.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = clone.read_type_table(meta, common_strings=common)
        class_of = {item.index: item.class_id for item in schema.entries}
        nodes_of = {item.index: item.nodes for item in schema.entries}
        for path_id, row in sorted(clone.object_rows(info).items()):
            type_id = int(row[2])
            if class_of.get(type_id) != ASSETBUNDLE_CLASS_ID:
                continue
            payload = clone.read_object_payload(reader, node0, info, int(path_id))
            tree = clone.read_object(payload, nodes_of[type_id])
            out.append((int(path_id), str(tree.get("m_Name") or "")))
    return tuple(out)


def resolve_container_host(
    bundle_path: str | Path, path_id: int | None = None
) -> tuple[int, str]:
    """哪个 `AssetBundle` 对象收这条容器条目：给了 pathID 就用它，没给就必须**唯一**。"""
    hosts = container_hosts(bundle_path)
    if path_id is not None:
        hit = next((item for item in hosts if item[0] == int(path_id)), None)
        if hit is None:
            raise AssetError(
                f"pathID={int(path_id)} 不是 {Path(bundle_path).name} 里的 AssetBundle 对象"
                f"（包里 {len(hosts)} 个：{list(hosts[:6])}）",
                hint="容器条目挂在 AssetBundle（classID 142）上 —— 用这一格指名它",
            )
        return hit
    if len(hosts) != 1:
        raise AssetError(
            f"{Path(bundle_path).name} 里的 AssetBundle 对象有 {len(hosts)} 个：{list(hosts[:6])}",
            hint="⛔ 不猜是哪一个 —— 给 container_path_id 指名",
        )
    return hosts[0]


def _container_guard(
    bundle: Path, entries: Sequence[ContainerEntry]
) -> tuple[str, ...]:
    """守卫：每一条容器条目的资产 pathID 都必须**在这个包里**（包外 = 悬空 PPtr）。"""
    ids = set(_package_ids(bundle))
    outside = sorted({entry.path_id for entry in entries if entry.path_id not in ids})
    if outside:
        raise AssetError(
            f"容器条目的资产 pathID 有 {len(outside)} 条不在这个包里：{outside[:8]}",
            hint="容器条目指向包外 = 悬空 PPtr（游戏 LoadAsset 取到空）—— 先把这个对象加进包",
        )
    return (f"容器守卫：{len(entries)} 条资产全在包内（{len(ids)} 个对象）",)


@dataclass(frozen=True, slots=True)
class ContainerPlan:
    """一次**地址级容器条目**改动的干跑结果（⛔ 一个字节都没写）。"""

    operation: str
    bundle: Path
    bundle_name: str
    container_path_id: int
    container_name: str
    asset_path: str
    path_id: int
    entries_before: tuple[ContainerEntry, ...]
    entries_after: tuple[ContainerEntry, ...]
    #: 容器表一变长度必变 ⇒ 恒为 `writer.ObjectGrowth`。
    change: writer.ObjectGrowth
    writeback: writer.WritebackPlan
    guard_lines: tuple[str, ...] = ()
    game_root: Path | None = None
    catalog_path: Path | None = None

    @property
    def level(self) -> str:
        return self.change.__class__.__name__

    def size_before(self) -> int:
        for row in self.writeback.rows:
            if row.path_id == self.container_path_id:
                return int(row.old_bytes)
        return 0

    def count_line(self) -> str:
        """**判据一行**：`m_Container` 条数 N -> M。"""
        return (
            f"m_Container 条数 {len(self.entries_before)} -> {len(self.entries_after)}"
            f"（{self.operation}，地址 {self.asset_path!r}）"
        )

    def criterion_lines(self) -> tuple[str, ...]:
        catalog = self.catalog_path if self.catalog_path is not None else "未同步（离线件）"
        return (
            f"[容器条目{self.operation}] {self.bundle_name}：AssetBundle『{self.container_name}』"
            f" pathID={self.container_path_id}   {self.count_line()}",
            f"  资产 pathID={self.path_id}（地址 {self.asset_path!r}）"
            "   预载表：新增时尾部加一格 / 删除时一个字节都不动",
            f"  对象字节 {self.size_before():,} -> {len(self.change.payload):,} B"
            f"（{len(self.change.payload) - self.size_before():+,}）   写回档：{self.level}"
            "（变长 ⇒ Δ 搬到 node[0] 尾部）",
            f"  catalog：{catalog}",
            *self.guard_lines,
            *self.writeback.criterion_lines(),
        )

    def summary(self) -> str:
        return (
            f"{self.bundle_name}：容器条目{self.operation} {self.asset_path!r}"
            f"（{len(self.entries_before)} -> {len(self.entries_after)} 条，{self.level}）"
        )

    def patch_label(self) -> str:
        return (
            f"容器条目{self.operation}：{self.bundle_name} {self.container_name} "
            f"{self.asset_path!r}（{len(self.entries_before)} -> {len(self.entries_after)}）"
        )


def _make_container_plan(
    bundle_path: str | Path,
    *,
    operation: str,
    apply: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    asset_path: str,
    container_path_id: int | None,
    catalog_path: str | Path | None,
    game_root: str | Path | None,
    common_strings: Mapping[int, str] | None,
) -> ContainerPlan:
    """干跑：读 AssetBundle -> 纯函数改树 -> ②③④ 自证 -> 守卫 -> `writer` 那条写回计划（只读）。"""
    root = Path(game_root) if game_root is not None else None
    catalog = prefab_skins._resolve_catalog(root, catalog_path)  # noqa: SLF001 -- 同一份口径
    host_pid, host_name = resolve_container_host(bundle_path, container_path_id)
    obj = prefab_skins.read_package_object(
        bundle_path, host_pid, common_strings=common_strings, catalog_path=catalog
    )
    before = container_entries(obj.tree)
    new_tree = apply(obj.tree)
    after = container_entries(new_tree)
    guard_lines = _container_guard(obj.bundle, after)
    payload = prefab_skins.serialize_package_tree(obj, new_tree)
    got = container_entries(clone.read_object(payload, obj.nodes))
    if got != after:
        raise AssetError(
            f"写出来的字节读回去是 {len(got)} 条容器条目，期望 {len(after)} 条",
            hint="序列化没把新容器写进去 —— 这一版不许写回",
        )
    if payload == obj.payload:
        raise AssetError(
            f"改完的字节与原字节**一模一样** ⇒ 空改动（{operation}）",
            hint="空改动不该写盘：检查一下是不是把同一件事做了两遍",
        )
    if len(payload) == len(obj.payload):
        raise AssetError(
            f"容器变过、对象字节却没变长（{len(payload):,} B）—— 走错了档",
            hint="地址串与预载指针都是变长的：增删必然改长度（⛔ 别硬写）",
        )
    known = {entry.asset_path for entry in before}
    fresh = [entry for entry in after if entry.asset_path not in known]
    gone = [entry for entry in before if entry.asset_path not in {e.asset_path for e in after}]
    touched = (fresh or gone or [None])[0]
    change = writer.ObjectGrowth(
        path_id=obj.path_id, payload=payload, name=obj.name or obj.type_name,
        type_name=obj.type_name,
    )
    writeback = writer.plan_delta(
        obj.bundle, writer.DeltaChange(growths=(change,)), catalog_path=catalog
    )
    return ContainerPlan(
        operation=operation,
        bundle=obj.bundle,
        bundle_name=obj.bundle_name,
        container_path_id=obj.path_id,
        container_name=host_name or (obj.name or obj.type_name),
        asset_path=str(asset_path),
        path_id=int(touched.path_id) if touched is not None else 0,
        entries_before=before,
        entries_after=after,
        change=change,
        writeback=writeback,
        guard_lines=guard_lines,
        game_root=root,
        catalog_path=catalog,
    )


def plan_add_container(
    bundle_path: str | Path,
    *,
    asset_path: str,
    asset_id: int,
    container_path_id: int | None = None,
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> ContainerPlan:
    """**干跑**：给 `AssetBundle.m_Container` 加一条地址级条目（条数 N -> N+1）。

    这是「**新增一个能被地址取到的资产**」缺的那一格：catalog 里登记了
    `<资产路径> -> <包>`，而游戏真正拿资产走的是 `AssetBundle.m_Container`
    （`unitypy_source.open_unique_bundle` 就是拿它当"第二把钥匙"复核的）——
    catalog 有、容器没有 ⇒ 地址解析得到包、`LoadAsset` 取不到东西。
    """
    return _make_container_plan(
        bundle_path,
        operation="新增",
        apply=lambda tree: add_container_entry(tree, asset_path=asset_path, path_id=asset_id),
        asset_path=str(asset_path),
        container_path_id=container_path_id,
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
    )


def plan_remove_container(
    bundle_path: str | Path,
    *,
    asset_path: str,
    container_path_id: int | None = None,
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> ContainerPlan:
    """**干跑**：摘掉一条地址级条目（条数 N -> N-1；地址不在容器里 ⇒ 抛）。"""
    return _make_container_plan(
        bundle_path,
        operation="删除",
        apply=lambda tree: remove_container_entry(tree, asset_path=asset_path)[0],
        asset_path=str(asset_path),
        container_path_id=container_path_id,
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
    )
