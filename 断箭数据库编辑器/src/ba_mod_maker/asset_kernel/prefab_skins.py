"""皮肤表 `UnitPrefabRoot.Skins.Data` 的**新增 / 删除 / 修改**（产品 API）。

## 这一层收的是哪件事

`UnitPrefabRoot` 是 classID 114（MonoBehaviour）上的一颗脚本，它的字段树里有一张皮肤表：

    Skins : UnitSkinStorage
      Data : SkinStorageData[]                  <- **数组，一条 = 一个皮肤**
        SkinStorageData {
          Id : int
          Data : SkinDataDictionary {
            _keyData   : PPtr<Material>[]
            _valueData : SkinData[] { Renderers : PPtr<Renderer>[] } } }

形状不是推的：type entry 24（script_index 20）的**真件类型树**就是这么长的，来源
`_work/w10_ref/units_meta.bin`（真件 units 包的元数据区冻结件）；用例里逐字复算。
`_keyData` / `_valueData` 是**平行数组**（Unity 的自定义字典序列化）——下标一一对应；
运行时 `SkinStorageData.GetRendererDict()` 把它们翻成 `Dictionary<Renderer, List<Material>>`
（ISIL：`_work/isil/IsilDump/…/Ecs/Customization/Skins/SkinStorageData.txt:3`）。

## 为什么必须**照抄参照条**（⛔ 不许手搓默认值）

`SkinStorageBridge.FillRenderers()`（ISIL 同目录 `SkinStorageBridge.txt:499`）只给
**Renderers 为空**的那些条补上"全部渲染器" ⇒ 一条手搓的「空 Renderers」皮肤，在游戏里会被
**就地补成另一个语义**，与我们以为写进去的东西不是同一件东西。
所以本模块的**新增**只有一条路：给一条**参照条**（既有皮肤）＋ 新 Id（＋ 可选的新材质指向）；
`Renderers` 与配对结构**原样照抄**。参照条不存在 ⇒ **拒绝**（⛔ 不回落、不造默认值）。

## 三个动词（AGENTS §0.10：产品只有新增 / 删除 / 修改）

| 动词 | 本模块 | Data 条数 | 走哪条写回 |
|---|---|---|---|
| **新增** | `add_skin` / `plan_add` | **N -> N+1** | `writer.ObjectGrowth`（Δ） |
| **删除** | `remove_skin` / `plan_remove` | **N -> N-1** | `writer.ObjectGrowth` |
| **修改** | `update_skin` / `plan_update` | 等长（只换材质 PPtr） | `writer.ObjectChange`（L1） |

> Δ 档 = 对象搬到 node[0] 尾部（8 字节对齐）；L1 档 = 原地覆盖、对象表一个字节都不动。

⛔ 没有「换」：要改材质指向 = **修改**这一条，或者 **删除旧条 ＋ 新增新条**（两个动作）。

## 守卫（fail-closed，⛔ 一条都不许静默）

* **Id 撞**（既有条里已有这个 Id）⇒ 抛，并把既有 Id 列表印出来；
* **参照条不存在** ⇒ 抛，并把既有 Id 列表印出来；
* **材质不在参照条里**（想改的那颗材质参照条没引用）⇒ 抛（那是"自己发明槽位"）；
* **渲染器 / 材质不在包里**（`package_ids`）⇒ 抛（`unit_bundle` 的 A3：皮肤表 PPtr 必须在包内）；
* **渲染器不在 prefab 的 Renderers 名单里**（名单非空就查）⇒ 抛；
* **空改动**（新旧字节一模一样）⇒ 抛（空改动不该写盘）。

## 自证（都在 `_make_plan` 里做，任何一条不成立就抛）

① 原对象 typetree 往返**逐字节相同**（`read_package_object`）；
② 改完长度**真的变了** ⇒ 走 Δ；**确实等长** ⇒ 走 L1（档位错了两边都会拦）；
③ 从**新字节读回**的皮肤表 == 期望（`skin_slots(payload) == after`）；
④ 新旧对象字节相同 ⇒ 抛。

## 落点与留白

* 写回那一趟全在 `writer` 里（`write_delta` / `write_objects`）：Δ 搬尾 / 重算 CRC /
  **成对**更新 catalog；`patch_store` 给了就走 ADR 0007 的**反向 Δ**（⛔ 不拷整包）；
* ⛔ 本模块**不做可见性**（`UserItemsConfig.Skins[单位Id]`，设计稿 §1 的第 ④ 层，
  写 `data.unity3d` ≈ 4 分钟）—— 那半张表见 `docs/设计-涂装贴图皮肤.md` §2.3 的第二步；
* ⛔ 不新增 / 删除材质对象（那是 `writer.NewObject` 那一档，见 `clone.delta_change`）；
* 皮肤 **Id 是跨两张表的连接键**：这里写进去的 Id，将来要在 `UserItemsConfig` 里有同号的一条，
  玩家菜单里才看得见（设计稿 §1.1 的实测）。
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ba_mod_maker.asset_kernel import clone, reverse_delta, writer
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "KEY_FIELD",
    "RENDERERS_FIELD",
    "SKINS_FIELD",
    "SKIN_DATA_FIELD",
    "SKIN_ID_FIELD",
    "VALUE_FIELD",
    "ChangePlan",
    "PackageObject",
    "SkinPlan",
    "SkinSlot",
    "add_skin",
    "clone_skin",
    "guard_targets",
    "install",
    "plan_add",
    "plan_remove",
    "plan_update",
    "read_package_object",
    "read_skins",
    "remove_skin",
    "run_write",
    "serialize_package_tree",
    "set_skins",
    "skin_slots",
    "slot_of",
    "update_skin",
    "verify_plan",
]

# --------------------------------------------------------------------------- #
# 字段名（typetree 里的原样拼写 —— 一个字符都不许改）
# --------------------------------------------------------------------------- #
#: 皮肤表那张表（`UnitPrefabRoot` 上的 `UnitSkinStorage`）。
SKINS_FIELD = "Skins"
#: 皮肤表里的那条数组。
SKIN_DATA_FIELD = "Data"
#: 一条皮肤的 Id（**跨表连接键**：`UserItemsConfig.Skins[单位Id]` 里同号才看得见）。
SKIN_ID_FIELD = "Id"
#: `SkinDataDictionary` 的平行数组：材质 PPtr 列表。
KEY_FIELD = "_keyData"
#: 同上：每条对应一个 `SkinData`，里面是渲染器 PPtr 列表。
VALUE_FIELD = "_valueData"
#: `SkinData` 里的渲染器列表。
RENDERERS_FIELD = "Renderers"


# --------------------------------------------------------------------------- #
# 纯函数层：一条皮肤的数据形状（零第三方依赖，可单独测）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SkinSlot:
    """皮肤表里的一条：`Id` ＋ 材质 -> 渲染器**平行**配对。

    `materials[i]` 与 `renderers[i]` 是一对（`_keyData[i]` / `_valueData[i]`）——
    两个元组的长度必须相等，⛔ 不许只对一边动手。
    """

    skin_id: int
    materials: tuple[int, ...]
    renderers: tuple[tuple[int, ...], ...]

    def check(self, *, where: str) -> SkinSlot:
        """形状自检：Id 是正数 ＋ 平行数组长度相等。"""
        if self.skin_id <= 0:
            raise AssetError(
                f"{where}：皮肤 Id={self.skin_id} 不是正数",
                hint="Id 是跨表连接键（UserItemsConfig.Skins）；0 / 负数不可能是真件里的值",
            )
        if len(self.materials) != len(self.renderers):
            raise AssetError(
                f"{where}：材质 {len(self.materials)} 条 / 渲染器条目 {len(self.renderers)} 条"
                " —— 平行数组长度不等",
                hint="_keyData 与 _valueData 是**配对**的（SkinDataDictionary）——"
                "只改一边就是写坏这张表，⛔ 不许按'看起来对'补",
            )
        return self

    def pairs(self) -> tuple[tuple[int, tuple[int, ...]], ...]:
        return tuple(zip(self.materials, self.renderers, strict=True))

    def summary(self) -> str:
        pairs = "；".join(
            f"材质 {material} -> 渲染器 {list(renders)}" for material, renders in self.pairs()
        )
        return f"皮肤 Id={self.skin_id}（{len(self.materials)} 对）：{pairs}"


def _mapping_of(value: Any, *, where: str, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssetError(
            f"{where} 不是映射（读到 {type(value).__name__}）",
            hint=f"{what} 的字段树形状与真件不符 —— 先看清楚再写，⛔ 不许盲改",
        )
    return value


def _sequence_of(value: Any, *, where: str, what: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AssetError(
            f"{where} 不是序列（读到 {type(value).__name__}）",
            hint=f"{what} 是一条数组（vector）—— 形状不对说明读的不是这张表",
        )
    return value


def _pptr_id(value: Any, *, where: str) -> int:
    """一个 PPtr 的 pathID（`m_FileID != 0` 直接抛：皮肤表的引用必须落在**本包**）。"""
    ptr = _mapping_of(value, where=where, what="PPtr")
    file_id = int(ptr.get("m_FileID", 0) or 0)
    if file_id != 0:
        raise AssetError(
            f"{where} 的 m_FileID={file_id}（跨文件引用）",
            hint="unit_bundle 的 A3：Skins 里的 PPtr 必须落在本包（fileID=0）",
        )
    return int(ptr.get("m_PathID", 0) or 0)


def _raw_rows(tree: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """皮肤表的**原始行**（不翻译；形状不对就抛）。"""
    skins = _mapping_of(tree.get(SKINS_FIELD), where=SKINS_FIELD, what="UnitSkinStorage")
    rows = _sequence_of(
        skins.get(SKIN_DATA_FIELD),
        where=f"{SKINS_FIELD}.{SKIN_DATA_FIELD}",
        what="SkinStorageData",
    )
    return tuple(rows)


def skin_slots(tree: Mapping[str, Any]) -> tuple[SkinSlot, ...]:
    """**纯函数**：读一张皮肤表。

    形状不对（字段不在 / 不是序列 / 平行数组长度不等）一律抛 `AssetError`，⛔ **不静默当空** ——
    把"读法坏了"写成"这张表是空的"，下一步就是拿一棵假的空表去写盘。
    """
    out: list[SkinSlot] = []
    rows = _raw_rows(tree)
    for index, row in enumerate(rows):
        where = f"{SKINS_FIELD}.{SKIN_DATA_FIELD}[{index}]"
        entry = _mapping_of(row, where=where, what="SkinStorageData")
        skin_id = entry.get(SKIN_ID_FIELD)
        if not isinstance(skin_id, int) or isinstance(skin_id, bool):
            raise AssetError(
                f"{where}.{SKIN_ID_FIELD} 不是整数（读到 {type(skin_id).__name__}）",
                hint="Id 是 int32（真件类型树）—— 类型不对说明读的不是这条表",
            )
        data = _mapping_of(
            entry.get(SKIN_DATA_FIELD), where=f"{where}.Data", what="SkinDataDictionary"
        )
        materials = tuple(
            _pptr_id(item, where=f"{where}.Data.{KEY_FIELD}[{i}]")
            for i, item in enumerate(
                _sequence_of(
                    data.get(KEY_FIELD), where=f"{where}.Data.{KEY_FIELD}", what="PPtr<Material>[]"
                )
            )
        )
        values = _sequence_of(
            data.get(VALUE_FIELD), where=f"{where}.Data.{VALUE_FIELD}", what="SkinData[]"
        )
        renderers: list[tuple[int, ...]] = []
        for vi, value in enumerate(values):
            vwhere = f"{where}.Data.{VALUE_FIELD}[{vi}]"
            item = _mapping_of(value, where=vwhere, what="SkinData")
            renderers.append(
                tuple(
                    _pptr_id(ptr, where=f"{vwhere}.{RENDERERS_FIELD}[{ri}]")
                    for ri, ptr in enumerate(
                        _sequence_of(
                            item.get(RENDERERS_FIELD),
                            where=f"{vwhere}.{RENDERERS_FIELD}",
                            what="PPtr<Renderer>[]",
                        )
                    )
                )
            )
        out.append(
            SkinSlot(
                skin_id=int(skin_id), materials=materials, renderers=tuple(renderers)
            ).check(where=where)
        )
    return tuple(out)


def slot_of(tree: Mapping[str, Any], skin_id: int) -> SkinSlot:
    """按 Id 取一条；不存在 ⇒ 抛（并把既有 Id 列出来）。"""
    slots = skin_slots(tree)
    for slot in slots:
        if slot.skin_id == int(skin_id):
            return slot
    ids = [s.skin_id for s in slots]
    raise AssetError(
        f"皮肤表里没有 Id={int(skin_id)} 这一条（既有 {len(slots)} 条：{ids}）",
        hint="参照条必须来自**这张表**（照抄参照条的口径，见模块头）—— ⛔ 不许凭空造一条",
    )


def _row_of(slot: SkinSlot) -> dict[str, Any]:
    """按真件字段树拼回一行（只在我们**新造**一条时用）。"""
    return {
        SKIN_ID_FIELD: int(slot.skin_id),
        SKIN_DATA_FIELD: {
            KEY_FIELD: [clone.pptr(m) for m in slot.materials],
            VALUE_FIELD: [
                {RENDERERS_FIELD: [clone.pptr(r) for r in renders]} for renders in slot.renderers
            ],
        },
    }


def set_skins(tree: Mapping[str, Any], slots: Sequence[SkinSlot]) -> dict[str, Any]:
    """**纯函数**：把一张皮肤表换成 `slots`（入参 `tree` **不被改**）。

    没动过的行**原样照抄**（深拷旧行），只有新条 / 改了的那条才按字段树重拼 ——
    这样「加一条」的字节差就是那一条，不会顺手把别的行重排。
    """
    original = skin_slots(tree)
    raw = list(_raw_rows(tree))
    by_slot = {slot: row for slot, row in zip(original, raw, strict=True)}
    rows: list[dict[str, Any]] = []
    for slot in slots:
        slot.check(where="set_skins")
        old = by_slot.get(slot)
        rows.append(copy.deepcopy(dict(old)) if old is not None else _row_of(slot))
    skins = dict(
        _mapping_of(tree.get(SKINS_FIELD), where=SKINS_FIELD, what="UnitSkinStorage")
    )
    skins[SKIN_DATA_FIELD] = rows
    new_tree = dict(tree)
    new_tree[SKINS_FIELD] = skins
    return new_tree


# --------------------------------------------------------------------------- #
# 纯函数层：三个动词 ＋ 守卫
# --------------------------------------------------------------------------- #
def _map_materials(
    reference: SkinSlot, materials: Mapping[int, int] | None, *, where: str
) -> tuple[int, ...]:
    """按「旧材质 -> 新材质」重指参照条的材质（⛔ 只许改**参照条里已有**的那些）。"""
    if not materials:
        return reference.materials
    unknown = [int(old) for old in materials if int(old) not in reference.materials]
    if unknown:
        raise AssetError(
            f"{where}：要改的材质 {unknown} 参照条 Id={reference.skin_id} 里没有"
            f"（它有 {list(reference.materials)}）",
            hint="口径是**照抄参照条、只改材质指向** —— 参照条没引用的材质"
            "说明这不是同一张皮肤的槽位",
        )
    return tuple(int(materials.get(m, m)) for m in reference.materials)


def clone_skin(
    reference: SkinSlot, *, new_id: int, materials: Mapping[int, int] | None = None
) -> SkinSlot:
    """**纯函数**：照抄参照条 —— 只改 Id 与（可选的）材质指向。

    `Renderers` 与平行配对结构**原样照抄** —— 这是 `FillRenderers()` 那条口径的机械落实
    （见模块头：手搓空 Renderers 会被游戏就地补成"全部渲染器"，语义就变了）。
    """
    if int(new_id) <= 0:
        raise AssetError(
            f"新皮肤 Id={int(new_id)} 不是正数",
            hint="Id 是 int32 的连接键（UserItemsConfig.Skins）—— ⛔ 不许拿 0 / 负数当新 Id",
        )
    materials_new = _map_materials(reference, materials, where="clone_skin")
    return SkinSlot(
        skin_id=int(new_id), materials=materials_new, renderers=reference.renderers
    ).check(where="clone_skin")


def guard_targets(
    slots: Sequence[SkinSlot],
    *,
    package_ids: Sequence[int] | None = None,
    renderers: Sequence[int] | None = None,
) -> tuple[str, ...]:
    """**纯函数**：引用守卫（越界一律抛），返回读数行。

    * `package_ids` 给了：每条材质 / 渲染器（非 0）必须在这个包里 ——
      `unit_bundle` 的 A3（皮肤表 PPtr 指向包外 = 游戏那边取到空指针）；
    * `renderers` 给了：每条渲染器必须落在这份**名单**里（= prefab 的 `Renderers`）——
      "渲染器不在 prefab 里"这条守卫的机械形式。
    """
    lines: list[str] = []
    known = {int(p) for p in package_ids} if package_ids is not None else None
    allowed = {int(p) for p in renderers} if renderers is not None else None
    mats = sorted({m for slot in slots for m in slot.materials if m})
    rends = sorted({r for slot in slots for renders in slot.renderers for r in renders if r})
    if known is not None:
        outside = [p for p in (*mats, *rends) if p not in known]
        if outside:
            raise AssetError(
                f"皮肤表指向包外 {len(outside)} 条 PPtr：{outside[:8]}",
                hint="A3 判据：Skins 里的 PPtr（材质 / 渲染器）必须落在本包 ——"
                " 包外引用在游戏里取到空指针",
            )
        lines.append(
            f"引用守卫：材质 {len(mats)} 颗 / 渲染器 {len(rends)} 个，全在包内"
            f"（{len(known)} 个对象）"
        )
    if allowed is not None:
        stray = [p for p in rends if p not in allowed]
        if stray:
            raise AssetError(
                f"皮肤表引用了 {len(stray)} 个**不在 prefab 的 Renderers 名单**里的渲染器："
                f"{stray[:8]}",
                hint=f"UnitPrefabRoot.Renderers 只有 {len(allowed)} 个：{sorted(allowed)[:8]} ——"
                " 皮肤只能指向这个 prefab 自己的渲染器",
            )
        lines.append(
            f"渲染器守卫：{len(rends)} 个渲染器全在 prefab 的 Renderers 名单里（{len(allowed)} 个）"
        )
    return tuple(lines)


def add_skin(
    tree: Mapping[str, Any],
    *,
    skin_id: int,
    reference_id: int,
    materials: Mapping[int, int] | None = None,
    package_ids: Sequence[int] | None = None,
    renderers: Sequence[int] | None = None,
) -> dict[str, Any]:
    """**纯函数**：新增一条（照抄参照条）—— 表尾追加，条数 N -> N+1。"""
    slots = skin_slots(tree)
    existing = [slot.skin_id for slot in slots]
    if int(skin_id) in existing:
        raise AssetError(
            f"皮肤 Id={int(skin_id)} 已经在这张表里了（既有 {len(existing)} 条：{existing}）",
            hint="Id 是跨表连接键，撞号 ⇒ 两条皮肤在游戏里是同一件东西；"
            "要新加就先想一个新号（真件是连续编号）",
        )
    reference = slot_of(tree, int(reference_id))
    slot = clone_skin(reference, new_id=int(skin_id), materials=materials)
    guard_targets((slot,), package_ids=package_ids, renderers=renderers)
    return set_skins(tree, (*slots, slot))


def remove_skin(tree: Mapping[str, Any], *, skin_id: int) -> dict[str, Any]:
    """**纯函数**：删除一条 —— 条数 N -> N-1（Id 不存在 ⇒ 抛）。"""
    slots = skin_slots(tree)
    target = int(skin_id)
    hits = [index for index, slot in enumerate(slots) if slot.skin_id == target]
    if not hits:
        ids = [s.skin_id for s in slots]
        raise AssetError(
            f"皮肤表里没有 Id={target} 这一条（既有 {len(slots)} 条：{ids}）",
            hint="删除的对象必须是**这张表里真有**的那一条 —— ⛔ 不许按行下标猜",
        )
    index = hits[0]
    return set_skins(tree, (*slots[:index], *slots[index + 1 :]))


def update_skin(
    tree: Mapping[str, Any],
    *,
    skin_id: int,
    materials: Mapping[int, int],
    package_ids: Sequence[int] | None = None,
    renderers: Sequence[int] | None = None,
) -> dict[str, Any]:
    """**纯函数**：修改一条的**材质指向**（等长档：只换 PPtr 的值，条数与结构都不动）。"""
    slots = skin_slots(tree)
    target = int(skin_id)
    current = slot_of(tree, target)
    if not materials:
        raise AssetError(
            f"修改 Id={target} 却一个材质指向都没给 ⇒ 空改动",
            hint="修改 = 改一个**既有字段的值**（AGENTS §0.10）；什么也不改的动作不该生成写回计划",
        )
    changed = clone_skin(current, new_id=target, materials=materials)
    guard_targets((changed,), package_ids=package_ids, renderers=renderers)
    if changed == current:
        raise AssetError(
            f"修改 Id={target} 之后逐字段**没有任何变化** ⇒ 空改动",
            hint=f"现在就是 {current.summary()}；新旧相同不该写盘",
        )
    index = next(i for i, slot in enumerate(slots) if slot.skin_id == target)
    new_slots = (*slots[:index], changed, *slots[index + 1 :])
    return set_skins(tree, new_slots)


# --------------------------------------------------------------------------- #
# 包级读侧：一个对象 -> (typetree, 类型树, 原字节) ＋ 往返自证
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PackageObject:
    """一个真件对象的读侧快照（`bundle` 里 `path_id` 那一个）。"""

    bundle: Path
    bundle_name: str
    path_id: int
    type_id: int
    class_id: int
    type_name: str
    name: str
    tree: Mapping[str, Any]
    nodes: tuple[clone.TypeNode, ...]
    payload: bytes
    catalog_path: Path | None = None

    @property
    def size(self) -> int:
        return len(self.payload)


def read_package_object(
    bundle_path: str | Path,
    path_id: int,
    *,
    common_strings: Mapping[int, str] | None = None,
    catalog_path: str | Path | None = None,
) -> PackageObject:
    """**只读**：开包 -> 定位对象 -> 按它自己的类型树解成字段树。

    ① 自证：`write_object(read_object(payload)) == payload`（逐字节）——
    往返不可复现的对象一律拒绝（拿它写回会把没打算动的字段一起写坏）。
    """
    src = Path(bundle_path)
    common = common_strings or clone.unitypy_common_strings()
    with ufs.UfsReader(src) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
        meta = bytes(reader.read_range(node0.offset, ufs.SF_HEADER_SIZE + info.metadata_size))
        schema = clone.read_type_table(meta, common_strings=common)
        rows = clone.object_rows(info)
        row = rows.get(int(path_id))
        if row is None:
            raise AssetError(
                f"{node0.path} 的对象表里没有 pathID={int(path_id)}",
                hint=f"这个包有 {len(rows)} 个对象；读错了包 / 拿错了 pathID"
                "（⛔ 不要拿相近的顶替）",
            )
        _stored, _size, type_id = row
        entry = next((item for item in schema.entries if item.index == type_id), None)
        if entry is None:
            raise AssetError(
                f"pathID={int(path_id)} 的 typeID={type_id} 不在类型表里",
                hint="对象表与类型表对不上 —— 这个包别动",
            )
        payload = clone.read_object_payload(reader, node0, info, int(path_id))
        tree = clone.read_object(payload, entry.nodes)
        if clone.write_object(tree, entry.nodes) != payload:
            raise AssetError(
                f"pathID={int(path_id)} 的 typetree 往返不是逐字节相同",
                hint="这个对象的序列化不可复现 ⇒ 写回会改坏别的字段，本模块不许动它",
            )
        label = str(tree.get("m_Name") or "")
        # 组件自己的 m_Name 常常是空的（MonoBehaviour 就空着）⇒ 往上找它挂的那个
        # GameObject 的名字。这是**读数**（判据一行里好看），不参与字节。
        owner = tree.get("m_GameObject")
        owner_id = int((owner or {}).get("m_PathID", 0) or 0) if isinstance(owner, Mapping) else 0
        owner_row = rows.get(owner_id) if owner_id else None
        if owner_row is not None:
            owner_entry = next(
                (item for item in schema.entries if item.index == owner_row[2]), None
            )
            if owner_entry is not None:
                owner_payload = clone.read_object_payload(reader, node0, info, owner_id)
                owner_tree = clone.read_object(owner_payload, owner_entry.nodes)
                label = str(owner_tree.get("m_Name") or label)
    return PackageObject(
        bundle=src,
        bundle_name=node0.path,
        path_id=int(path_id),
        type_id=int(type_id),
        class_id=int(entry.class_id),
        type_name=clone.CLASS_NAMES.get(entry.class_id, f"class{entry.class_id}"),
        name=label,
        tree=tree,
        nodes=entry.nodes,
        payload=payload,
        catalog_path=Path(catalog_path) if catalog_path is not None else None,
    )


def serialize_package_tree(obj: PackageObject, tree: Mapping[str, Any]) -> bytes:
    """按**同一个类型树**把新字段树序列化成字节。

    ③ 的机械形式：新字节能读回、且**读回再写一遍逐字节相同**（序列化可复现）。
    """
    payload = clone.write_object(tree, obj.nodes)
    back = clone.read_object(payload, obj.nodes)
    if clone.write_object(back, obj.nodes) != payload:
        raise AssetError(
            f"pathID={obj.path_id} 的新字节能读回、但读回再写一遍不是同一串",
            hint="序列化不可复现 ⇒ 这一版不许写回",
        )
    return payload


def read_skins(bundle_path: str | Path, path_id: int) -> tuple[SkinSlot, ...]:
    """**只读**：从盘上把这张皮肤表读回来（写后复核 / J2 读数用）。"""
    return skin_slots(read_package_object(bundle_path, path_id).tree)


# --------------------------------------------------------------------------- #
# 包级计划（干跑）：三个动词各一条
# --------------------------------------------------------------------------- #
def _resolve_catalog(game_root: str | Path | None, catalog_path: str | Path | None) -> Path | None:
    """catalog 落点：显式给的优先；给了游戏根就按它推（**推不到就抛**，⛔ 不静默跳过）。

    ⛔ 真件包必须成对更新 catalog（`m_Crc` / `m_BundleSize`）：跳过它游戏不认这个包。
    合成件 / 离线件没有游戏根、也不显式给 ⇒ 返回 `None`（判据一行里会写"未同步 catalog"）。
    """
    if catalog_path is not None:
        path = Path(catalog_path)
        if not path.is_file():
            raise AssetError(
                f"指定的 catalog 不在：{path}",
                hint="catalog.json 在 aa/ 下（不是 aa/PC/）—— 路径给错就是不同步，游戏不认这个包",
            )
        return path
    if game_root is None:
        return None
    from ba_mod_maker.core.assets.game import catalog_file  # noqa: PLC0415 -- 只为拼路径，不拉 Qt

    path = catalog_file(Path(game_root))
    if not path.is_file():
        raise AssetError(
            f"{game_root} 下没有 aa/catalog.json（{path}）",
            hint="改真件包必须成对更新 catalog —— 要离线改就给 catalog_path=None 且不给 game_root",
        )
    return path


def _prefab_renderers(tree: Mapping[str, Any]) -> tuple[int, ...]:
    """`UnitPrefabRoot.Renderers` 名单（没有就以空元组回落 —— 那时不查这条守卫）。"""
    raw = tree.get(RENDERERS_FIELD)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(_pptr_id(item, where=f"{RENDERERS_FIELD}[{i}]") for i, item in enumerate(raw))


def _changed_spans(old: bytes, new: bytes) -> tuple[tuple[int, int], ...]:
    """**纯函数**：两串**等长**字节差在哪几段（[start, end)、按顺序、相邻已并）。"""
    if len(old) != len(new):
        raise AssetError(
            f"两串字节长度不等：{len(old):,} != {len(new):,}",
            hint="这是变长（Δ 级）；只有等长档才比段",
        )
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(old, new, strict=True)):
        if left != right:
            if start is None:
                start = index
        elif start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(old)))
    return tuple(spans)


def _ids_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[int, ...]:
    """两次读表里**变过**的 Id（新增 / 删除 / 修改都会落在里面）。"""
    old = {slot.skin_id: slot for slot in skin_slots(before)}
    new = {slot.skin_id: slot for slot in skin_slots(after)}
    keys = sorted(set(old) | set(new))
    return tuple(key for key in keys if old.get(key) != new.get(key))


@dataclass(frozen=True, slots=True)
class SkinPlan:
    """一次皮肤表改动的干跑结果（⛔ 一个字节都没写）。"""

    operation: str
    bundle: Path
    bundle_name: str
    prefab_path_id: int
    prefab_name: str
    slots_before: tuple[SkinSlot, ...]
    slots_after: tuple[SkinSlot, ...]
    touched: tuple[int, ...]
    #: 等长档是 `writer.ObjectChange`；变长档（新增 / 删除）是 `writer.ObjectGrowth`。
    change: writer.ObjectChange | writer.ObjectGrowth
    writeback: writer.WritebackPlan
    changed_spans: tuple[tuple[int, int], ...] = ()
    guard_lines: tuple[str, ...] = ()
    game_root: Path | None = None
    catalog_path: Path | None = None

    @property
    def level(self) -> str:
        return self.change.__class__.__name__

    @property
    def changed_bytes(self) -> int:
        return sum(end - start for start, end in self.changed_spans)

    def size_before(self) -> int:
        """改前对象字节数（从 writer 的计划行里读回来 —— 那是**真值**）。"""
        for row in self.writeback.rows:
            if row.path_id == self.prefab_path_id:
                return int(row.old_bytes)
        return 0

    def count_line(self) -> str:
        """**判据一行**（J2）：条数 N -> M。"""
        return (
            f"Skins.Data 条数 {len(self.slots_before)} -> {len(self.slots_after)}"
            f"（{self.operation}，Id {list(self.touched)}）"
        )

    def criterion_lines(self) -> tuple[str, ...]:
        """判据一行 ＋ writer 那几行（命中块 / 预期文件大小 / catalog）。"""
        catalog = self.catalog_path if self.catalog_path is not None else "未同步（离线件）"
        if isinstance(self.change, writer.ObjectGrowth):
            detail = "变长档：不比段（旧字节留在数据区成死区）"
        else:
            spans = "、".join(f"{lo}~{hi}" for lo, hi in self.changed_spans) or "（无差异段）"
            detail = f"改动 {self.changed_bytes} 字节（{len(self.changed_spans)} 段）：{spans}"
        return (
            f"[皮肤表{self.operation}] {self.bundle_name}：UnitPrefabRoot『{self.prefab_name}』"
            f" pathID={self.prefab_path_id}   {self.count_line()}",
            f"  对象字节 {self.size_before():,} -> {len(self.change.payload):,} B"
            f"（{len(self.change.payload) - self.size_before():+,}）   {detail}",
            f"  写回档：{self.level}"
            + (
                "（变长 ⇒ Δ 搬到 node[0] 尾部）"
                if isinstance(self.change, writer.ObjectGrowth)
                else "（等长 ⇒ 原地覆盖）"
            ),
            f"  catalog：{catalog}",
            *self.guard_lines,
            *self.writeback.criterion_lines(),
        )

    def summary(self) -> str:
        return (
            f"{self.bundle_name}：皮肤表{self.operation} Id {list(self.touched)}"
            f"（{len(self.slots_before)} -> {len(self.slots_after)} 条，{self.level}）"
        )

    def patch_label(self) -> str:
        return (
            f"皮肤表{self.operation}：{self.bundle_name} {self.prefab_name} "
            f"Id {list(self.touched)}（{len(self.slots_before)} -> {len(self.slots_after)}）"
        )


def _guarded(tree: Mapping[str, Any], obj: PackageObject) -> tuple[str, ...]:
    """包级守卫：皮肤表里的 PPtr 全在包内 ＋ 渲染器在 prefab 的名单里。"""
    slots = skin_slots(tree)
    with ufs.UfsReader(obj.bundle) as reader:
        node0 = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node0)
        ids = sorted({int(row[1]) for row in info.objects})
    names = _prefab_renderers(tree)
    lines = list(guard_targets(slots, package_ids=ids, renderers=names or None))
    if not names:
        lines.append("渲染器守卫：这个 prefab 的 Renderers 名单是空的 ⇒ 只查了「在包内」那一条")
    return tuple(lines)


def _make_plan(
    bundle_path: str | Path,
    path_id: int,
    *,
    operation: str,
    apply: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    catalog_path: str | Path | None,
    game_root: str | Path | None,
    common_strings: Mapping[int, str] | None,
) -> SkinPlan:
    """干跑：读对象 -> 纯函数改树 -> ②③④ 自证 -> 守卫 -> `writer` 那条写回计划（⛔ 只读）。"""
    root = Path(game_root) if game_root is not None else None
    catalog = _resolve_catalog(root, catalog_path)
    obj = read_package_object(
        bundle_path, path_id, common_strings=common_strings, catalog_path=catalog
    )
    before = skin_slots(obj.tree)
    new_tree = apply(obj.tree)
    after = skin_slots(new_tree)
    guard_lines = _guarded(new_tree, obj)
    payload = serialize_package_tree(obj, new_tree)
    # ③ 从**新字节**读回：皮肤表必须恰好是期望的那张
    got = skin_slots(clone.read_object(payload, obj.nodes))
    if got != after:
        raise AssetError(
            f"写出来的字节读回去是 {len(got)} 条，期望 {len(after)} 条",
            hint="序列化没把新表写进去 —— 这一版不许写回",
        )
    # ④ 空改动
    if payload == obj.payload:
        raise AssetError(
            f"改完的字节与原字节**一模一样** ⇒ 空改动（{operation}）",
            hint="空改动不该写盘：检查一下是不是把同一件事做了两遍",
        )
    name = obj.name or obj.type_name
    change: writer.ObjectChange | writer.ObjectGrowth
    if len(payload) != len(obj.payload):
        change = writer.ObjectGrowth(
            path_id=obj.path_id, payload=payload, name=name, type_name=obj.type_name
        )
        writeback = writer.plan_delta(
            obj.bundle, writer.DeltaChange(growths=(change,)), catalog_path=catalog
        )
        spans: tuple[tuple[int, int], ...] = ()
    else:
        change = writer.ObjectChange(
            path_id=obj.path_id, payload=payload, name=name, type_name=obj.type_name
        )
        writeback = writer.plan_writeback(obj.bundle, [change], catalog_path=catalog)
        spans = _changed_spans(obj.payload, payload)
    return SkinPlan(
        operation=operation,
        bundle=obj.bundle,
        bundle_name=obj.bundle_name,
        prefab_path_id=obj.path_id,
        prefab_name=name,
        slots_before=before,
        slots_after=after,
        touched=_ids_diff(obj.tree, new_tree),
        change=change,
        writeback=writeback,
        changed_spans=spans,
        guard_lines=guard_lines,
        game_root=root,
        catalog_path=catalog,
    )


def plan_add(
    bundle_path: str | Path,
    path_id: int,
    *,
    skin_id: int,
    reference_id: int,
    materials: Mapping[int, int] | None = None,
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> SkinPlan:
    """**干跑**：新增一条皮肤（照抄参照条）—— 条数 N -> N+1。"""
    return _make_plan(
        bundle_path,
        path_id,
        operation="新增",
        apply=lambda tree: add_skin(
            tree, skin_id=skin_id, reference_id=reference_id, materials=materials
        ),
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
    )


def plan_remove(
    bundle_path: str | Path,
    path_id: int,
    *,
    skin_id: int,
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> SkinPlan:
    """**干跑**：删除一条皮肤 —— 条数 N -> N-1。"""
    return _make_plan(
        bundle_path,
        path_id,
        operation="删除",
        apply=lambda tree: remove_skin(tree, skin_id=skin_id),
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
    )


def plan_update(
    bundle_path: str | Path,
    path_id: int,
    *,
    skin_id: int,
    materials: Mapping[int, int],
    catalog_path: str | Path | None = None,
    game_root: str | Path | None = None,
    common_strings: Mapping[int, str] | None = None,
) -> SkinPlan:
    """**干跑**：修改一条皮肤的材质指向（等长档）。"""
    return _make_plan(
        bundle_path,
        path_id,
        operation="修改",
        apply=lambda tree: update_skin(tree, skin_id=skin_id, materials=materials),
        catalog_path=catalog_path,
        game_root=game_root,
        common_strings=common_strings,
    )


# --------------------------------------------------------------------------- #
# 写侧：install（唯一入口）＋ 写后复核
# --------------------------------------------------------------------------- #
class ChangePlan(Protocol):
    """写侧那一趟要的四样东西 ＋ 一个补丁标签（两个模块的计划都满足它）。

    `run_write` 只认这个形状 —— ⛔ 别在两个模块里各写一份 `write_delta / write_objects` 的分派。
    """

    bundle: Path
    change: writer.ObjectChange | writer.ObjectGrowth
    game_root: Path | None
    catalog_path: Path | None

    def patch_label(self) -> str:
        """反向 Δ 补丁的标签（人读的那一行）。"""
        ...


def run_write(
    plan: ChangePlan, *, project_root: str | Path, dry_run: bool = False
) -> writer.WriteReport:
    """**写侧唯一的一趟**（`renderer_slots` 也调它 —— 写回口径只有一份）。

    * 变长档走 `writer.write_delta`（Δ：对象搬 node[0] 尾部、只改它那一条表项）；
    * 等长档走 `writer.write_objects`（原地覆盖，对象表一个字节都不动）；
    * `patch_store` 给了就走 ADR 0007 的**反向 Δ**（⛔ **不拷整包**），补丁落
      `<工程目录>/patches/`，还原走 `assets restore`；
    * ⛔ 游戏在跑会被 writer 拦下（`is_game_running`），这里不再另写一份。
    """
    store = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(project_root), game_root=plan.game_root
    )
    common: dict[str, Any] = {
        "dry_run": dry_run,
        "project_root": Path(project_root),
        "game_root": plan.game_root,
        "catalog_path": plan.catalog_path,
        "patch_store": store,
        "patch_label": plan.patch_label(),
    }
    if isinstance(plan.change, writer.ObjectGrowth):
        return writer.write_delta(
            plan.bundle, writer.DeltaChange(growths=(plan.change,)), **common
        )
    return writer.write_objects(plan.bundle, [plan.change], **common)


def install(
    plan: SkinPlan, *, project_root: str | Path, dry_run: bool = False
) -> writer.WriteReport:
    """执行一次皮肤表改动（`dry_run=True` 只算不写）—— 落点见 `run_write`。"""
    return run_write(plan, project_root=project_root, dry_run=dry_run)


def verify_plan(plan: SkinPlan) -> tuple[str, ...]:
    """**写后复核**：从盘上把这张皮肤表读回来，逐条与 `plan.slots_after` 比。

    判据（J2 的读数就是它）：`Skins.Data 条数 N -> M（读回 M）`；读回的表与期望不等 ⇒ 抛。
    """
    got = read_skins(plan.bundle, plan.prefab_path_id)
    if got != plan.slots_after:
        raise AssetError(
            f"写后读回 {len(got)} 条，期望 {len(plan.slots_after)} 条",
            hint="读回：" + str([s.summary() for s in got]),
        )
    lines = [
        f"Skins.Data 条数 {len(plan.slots_before)} -> {len(plan.slots_after)}"
        f"（读回 {len(got)}，逐条与期望相同）"
    ]
    lines.extend(f"  {slot.summary()}" for slot in got)
    return tuple(lines)
