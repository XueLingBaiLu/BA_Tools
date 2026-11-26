"""皮肤（材质贴图槽）通道 · 产品化入口：**修改材质槽的指向**（8 字节，ADR 0007 的资产写回面）。

## 真机结论（依据，不是我推的）

研究-模型与动画/18-皮肤线/05-写侧-真机结论.md §1/§3：把 RU_BMD-2 1（默认皮肤）的
Layer_A97CDC25 槽的指向从 BMD-2_1_BaseMap 修改为 BMD-2_3_BaseMap（**8 字节**，解压流 abs
938,569,856）⇒ 机库里那台 BMD-2 由**纯绿变冬装**，不用点任何格子（用户实机确认）。
造包脚本 研究-模型与动画/tools/skinW_29_unitsA2.py 就是这条链的原型，本模块照它写成产品能力。

## 这一层做什么

    unit_key -> catalog 定位 -> 唯一命中包（m_Container 复核）-> 材质 typetree -> 槽 PPtr
    -> 槽 PPtr 修改为**同包另一张贴图**的 pathID -> 等长写回（L1）+ 同步 catalog（CRC 成对）
    + 反向 Δ 补丁落工程目录（ADR 0007 第 4 条，⛔ 不拷整包）

## 不做什么

* 不换像素（那是 writer.writeback_texture_file / writeback_unit_texture_format 两条链）；
* 不加新对象、不加新资产地址（ADR 0007 第 7 条的能力边界）；
* 不自造写回器 —— 写回那一趟全在 writer 里（patch_store 给了就走反向 Δ）。

## 规矩

* 干跑（plan_swap / install_swap(dry_run=True)）**一个字节都不写、不建补丁**；
* 槽名 / 贴图名 / 材质名**一律不许猜**：找不到就抛 AssetError 并把可用值印出来；
* 新旧贴图相同 = 空改动 ⇒ 拒绝（空改动不该写盘）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import materials as _mat
from ba_mod_maker.asset_kernel import reverse_delta, writer
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "SkinSwapPlan",
    "changed_spans",
    "install_swap",
    "open_unit",
    "list_materials",
    "list_slots",
    "plan_swap",
    "resolve_texture",
    "restore",
    "slot_texture_pptr",
    "swap_slot_texture",
    "swapped_material_bytes",
    "swapped_pptr",
    "texture_inventory",
]

#: 材质 typetree 里贴图槽那张表（BMD-2 那个槽 = "Layer_<hash>"，槽名由 Shader Graph 生成）。
_TEXENV_KEY = "m_TexEnvs"


# --------------------------------------------------------------------------- #
# 纯函数：树 / PPtr / 字节（零第三方依赖，可单独测）
# --------------------------------------------------------------------------- #
def _saved_properties(tree: Mapping[str, Any]) -> Mapping[str, Any]:
    props = tree.get("m_SavedProperties")
    if not isinstance(props, Mapping):
        raise AssetError(
            "这个材质没有 m_SavedProperties", hint="传进来的不是材质 typetree（照 writer 的口径）"
        )
    return props


def _tex_envs(tree: Mapping[str, Any]) -> Sequence[Any]:
    envs = _saved_properties(tree).get(_TEXENV_KEY)
    if isinstance(envs, (str, bytes)) or not isinstance(envs, Sequence):
        raise AssetError(
            "这个材质的 " + _TEXENV_KEY + " 不是序列",
            hint="typetree 形状与预期不符 —— 先看清楚再写，⛔ 不许盲改",
        )
    return envs


def _pptr_of(value: Any) -> dict[str, Any] | None:
    """槽的 m_Texture PPtr（dict 形式，原样拷一层）；空指针给 None。"""
    if not isinstance(value, Mapping):
        raise AssetError(
            "贴图槽的值不是 dict（typetree 形状与预期不符）",
            hint="与 writer.set_material_texture 同口径：先看清楚 m_TexEnvs 的形状",
        )
    pptr = value.get("m_Texture")
    if pptr is None:
        return None
    if not isinstance(pptr, Mapping):
        raise AssetError(
            "槽的 m_Texture 不是 dict（typetree 形状与预期不符）",
            hint="与 writer.set_material_texture 同口径",
        )
    return {str(key): pptr[key] for key in pptr}


def slot_texture_pptr(tree: Mapping[str, Any], slot: str) -> dict[str, Any]:
    """**纯函数**：取某个贴图槽**当前**的 PPtr。

    * 槽不在 ⇒ AssetError（消息里印出**可用槽名**）—— ⛔ 不许静默用默认值 / 第一个槽顶替；
    * 槽在但 PPtr 是空的（pathID = 0）⇒ AssetError（拿 0 当 pathID 会改错对象）。
    """
    envs = _tex_envs(tree)
    names: list[str] = []
    for entry in envs:
        if len(entry) < 2:
            continue
        name = str(entry[0])
        names.append(name)
        if name != slot:
            continue
        pptr = _pptr_of(entry[1])
        if pptr is None or not int(pptr.get("m_PathID", 0) or 0):
            raise AssetError(
                f"贴图槽 {slot!r} 存在但 PPtr 是空的（pathID = 0）",
                hint="这个槽没指任何贴图 —— ⛔ 不许拿 0 去当 pathID 用",
            )
        return pptr
    raise AssetError(
        f"材质里没有贴图槽 {slot!r}（共 {len(names)} 个：{names[:8]}）",
        hint="槽名不许猜：照 list_slots(game_root, unit_key, material_name=...) 给出的名字填",
    )


def swapped_pptr(
    pptr: Mapping[str, Any], *, path_id: int, file_id: int | None = None
) -> dict[str, Any]:
    """**纯函数**：换 PPtr 的 m_PathID（m_FileID 不传就**原样保留** —— 改的字节越少越好）。

    新旧相同 ⇒ AssetError（空改动不该写盘；真机上这一改就是 8 个字节，改了等于没改没有意义）。
    """
    out = {str(key): value for key, value in pptr.items()}
    out["m_PathID"] = int(path_id)
    if file_id is not None:
        out["m_FileID"] = int(file_id)
    if out == {str(key): value for key, value in pptr.items()}:
        raise AssetError(
            f"这个贴图槽已经指向 pathID={int(path_id)} —— 一次**空改动**",
            hint="换一张别的贴图（或换一个槽）；⛔ 空改动不该写盘",
        )
    return out


def swap_slot_texture(
    tree: Mapping[str, Any], slot: str, *, path_id: int, file_id: int | None = None
) -> dict[str, Any]:
    """**纯函数**：材质树 + 槽名 + 目标贴图 pathID ⇒ 换好槽的**新树**（入参一个字不动）。"""
    pptr = swapped_pptr(slot_texture_pptr(tree, slot), path_id=path_id, file_id=file_id)
    return writer.set_material_texture(tree, slot, pptr)


def swapped_material_bytes(
    tree: Mapping[str, Any],
    slot: str,
    *,
    path_id: int,
    save: Callable[[Mapping[str, Any]], bytes],
    old_size: int,
    file_id: int | None = None,
) -> bytes:
    """**纯函数**：材质树 + 槽名 + 目标贴图 pathID ⇒ 新的**对象字节**（**等长**）。

    save 是序列化器：真机上传 UnityPy 的 obj.save_typetree（与 writer.build_material_change
    同一个），用例里传合成编码器 —— 这样本函数零第三方依赖、单独可测，而「等长」这条判据在
    **函数里**拦死（不是靠调用方自觉；L1 是这条链的前提，长度变了必须当场报错）。
    """
    payload = bytes(save(swap_slot_texture(tree, slot, path_id=path_id, file_id=file_id)))
    if len(payload) != int(old_size):
        raise AssetError(
            f"换槽之后对象字节 {int(old_size):,} → {len(payload):,} B（不是等长）",
            hint="本模块只做 L1 等长覆盖；长度变了要走 Δ 那条链，⛔ 这里不许硬写",
        )
    return payload


def changed_spans(old: bytes, new: bytes) -> tuple[tuple[int, int], ...]:
    """**纯函数**：两串**等长**字节差在哪几段（[start, end) 闭开、按顺序、相邻已并）。

    判据用：真机那条改动是**一段 8 字节**（槽 PPtr 的 m_PathID）。
    """
    if len(old) != len(new):
        raise AssetError(
            f"两串字节长度不等：{len(old):,} != {len(new):,}",
            hint="这是变长（Δ 级）；本模块只做 L1 等长",
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


def resolve_texture(inventory: Mapping[str, Sequence[int]], texture_name: str) -> int:
    """**纯函数**：贴图名 -> pathID。

    * 找不到 ⇒ AssetError（消息里印出**可用贴图名**）；
    * 重名（一个名字命中多个 pathID）⇒ AssetError（⛔ 不猜，指名到 pathID 再说）。
    """
    wanted = str(texture_name)
    hits = [int(pid) for pid in inventory.get(wanted, ())]
    if not hits:
        available = sorted(str(name) for name in inventory)
        raise AssetError(
            f"这个包里没有贴图 {wanted!r}（共 {len(available)} 个 Texture2D：{available[:12]}）",
            hint="贴图名照 list_slots 给的名字填；⛔ 不许拿最像的那一个顶替",
        )
    if len(hits) > 1:
        raise AssetError(
            f"贴图名 {wanted!r} 在包里命中 {len(hits)} 个对象：{hits}",
            hint="重名 ⇒ ⛔ 不猜：先人工确认要换哪一个（按 pathID）",
        )
    return hits[0]


def _find_material(
    materials: Mapping[int, Mapping[str, Any]], material_name: str, unit_key: str
) -> int:
    """材质名 -> pathID（同名的多个也拒绝）。可用值印在错误消息里。"""
    available: list[str] = []
    hits: list[int] = []
    for path_id, material in materials.items():
        name = str(material.get("name") or "")
        available.append(name)
        if name == material_name:
            hits.append(int(path_id))
    if not hits:
        raise AssetError(
            f"{unit_key} 的 prefab 里没有材质 {material_name!r}"
            f"（prefab 渲染器与皮肤字典**两批都找过**，共 {len(available)} 个："
            f"{sorted(set(available))[:12]}）",
            hint="材质名照 list_materials(game_root, unit_key) 给的名字填 —— "
            "皮肤字典里的那几颗（切涂装用的）也在名单里",
        )
    if len(hits) > 1:
        raise AssetError(
            f"材质名 {material_name!r} 在 {unit_key} 的 prefab 里命中 {len(hits)} 个对象：{hits}",
            hint="重名 ⇒ ⛔ 不猜：先人工确认改哪一个（prefab 与皮肤字典两批一起算）",
        )
    return hits[0]


# --------------------------------------------------------------------------- #
# 读侧：定位 unit_key -> 唯一命中包（只读打开）
# --------------------------------------------------------------------------- #
def _aa_paths(game_root: str | Path) -> tuple[Path, Path]:
    """游戏根 -> (aa/PC, aa/catalog.json)。与 writer._aa_paths 同一口径（catalog 在 aa/ 下）。"""
    from ba_mod_maker.core.assets.game import bundle_dir, catalog_file  # noqa: PLC0415

    root = Path(game_root)
    return bundle_dir(root), catalog_file(root)


def _near_keys(game_root: str | Path, unit_key: str, *, limit: int = 8) -> tuple[str, ...]:
    """catalog 里以 unit_key 开头的地址键 —— **只用于报错提示**，不参与定位（⛔ 不猜）。

    实测口径：一个单位的 ModelFileName 可能带子资产前缀（BMD-2 是
    RU_BMD-2\\RU_BMD_2_base），用户敲显示名 RU_BMD-2 时把候选键摆出来最省事。
    """
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog  # noqa: PLC0415
    from ba_mod_maker.core.assets.game import catalog_file  # noqa: PLC0415

    catalog = AddressablesCatalog.load(catalog_file(Path(game_root)).parent)
    if catalog is None:
        return ()
    prefix = str(unit_key).lower()
    return tuple(sorted(key for key in catalog.index if key.lower().startswith(prefix))[:limit])


@dataclass(slots=True)
class _OpenUnit:
    """一个开着读源的单位资产（source 用完必须 close）。"""

    source: Any
    asset_path: str
    bundle: Path
    bundle_name: str
    catalog: Path


@contextmanager
def _open_unit(game_root: str | Path, unit_key: str) -> Iterator[_OpenUnit]:
    """unit_key -> 唯一命中包并**只读**打开它（异常一律翻成 AssetError）。"""
    from ba_mod_maker.asset_kernel.unitypy_source import open_unique_bundle  # noqa: PLC0415

    try:
        asset_path, candidates = writer.locate_unit_asset(game_root, unit_key)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        near = _near_keys(game_root, unit_key) if isinstance(exc, KeyError) else ()
        tail = f"；catalog 里以 {unit_key!r} 开头的键有 {list(near)}" if near else ""
        raise AssetError(
            f"catalog 里定位不到 {unit_key!r}：{exc}{tail}",
            hint=(
                "unit_key（CLI 的 --unit）要的是 **Units.ModelFileName 的原样值**，"
                "不是显示名：例 RU_BMD-2\\RU_BMD_2_base，而不是 RU_BMD-2"
            ),
        ) from exc
    bundle_dir_path, catalog_path = _aa_paths(game_root)
    try:
        source, bundle_name = open_unique_bundle(bundle_dir_path, candidates, asset_path)
    except FileNotFoundError as exc:
        raise AssetError(
            f"{unit_key!r} 的资产 {asset_path!r} 在 {len(candidates)} 个候选包里"
            f"没有唯一命中：{exc}",
            hint="catalog 的 group 会过度包含 —— 唯一命中由 AssetBundle.m_Container 复核",
        ) from exc
    try:
        yield _OpenUnit(
            source=source,
            asset_path=asset_path,
            bundle=bundle_dir_path / bundle_name,
            bundle_name=bundle_name,
            catalog=catalog_path,
        )
    finally:
        source.close()


def open_unit(game_root: str | Path, unit_key: str) -> AbstractContextManager[_OpenUnit]:
    """公开入口：`unit_key` -> 唯一命中包并**只读**打开它。

    与 `_open_unit` 是**同一个实现**（本函数只是不给私有名下划线）——
    产品的只读体检（`asset_kernel.spec_readings` 那条链）走这一个；
    研究探针继续用 `_open_unit`，⛔ 不另写第二份定位逻辑。
    """
    return _open_unit(game_root, unit_key)


#: 判别 UnitPrefabRoot 的两个字段（与只读探针 `_work/vtgame/g6/probe_skins.py` 同一套口径）。
SKIN_ROOT_FIELDS = ("Skins", "PrefabRoot")
#: 来路标签之一：prefab 渲染器直接引用的那些（`read_prefab_nodes` 给的就是这批）。
SOURCE_PREFAB = "prefab 渲染器"


def _pptr_id(value: Any) -> int:
    """PPtr 的一层 -> pathID（不是 dict / 空指针都给 0）。"""
    if not isinstance(value, Mapping):
        return 0
    return int(value.get("m_PathID", 0) or 0)


def _renderer_ids(value: Any) -> tuple[int, ...]:
    """`_valueData` 的一项 -> 它管的渲染器 pathID 那串。

    容两种形状：dict 里取 `Renderers`（真件那一种）/ 直接就是一串 PPtr。
    """
    if isinstance(value, Mapping) and "Renderers" in value:
        value = value["Renderers"]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return ()
    return tuple(path_id for path_id in (_pptr_id(item) for item in value) if path_id)


@dataclass(frozen=True, slots=True)
class SkinSlot:
    """皮肤字典里的一条：`Skins.Data[index].Data._keyData` 那几颗材质 ＋ 它们管的渲染器。

    形状依据 `_work/a2-skin-mono-spec.md`（`SkinStorageData{Id, Material→Renderer[]}`）
    与 `_work/audit-B-assets.md` §4.1（T-72B 底盘：`Data[0..4]` 的 `_keyData[0]` 分别指向
    `RU_T72B 1..5`，五条的 `Renderers` 逐条相同）。
    """

    index: int
    materials: tuple[int, ...]
    renderers: tuple[int, ...]


def skin_slots(source: Any, nodes: Sequence[Mapping[str, Any]]) -> tuple[SkinSlot, ...]:
    """把 prefab 上 **UnitPrefabRoot** 的皮肤字典摊开（⛔ 只读；读不通就跳过，不猜）。

    只认 `read_prefab_nodes` 走出来的那几棵子树上的 MonoBehaviour —— 一个包里可能有别的
    prefab（炮塔 / 武器）也带自己的字典，按"这颗单位自己的 prefab"取才不会串族。
    """
    out: list[SkinSlot] = []
    for node in nodes:
        for component in node.get("components") or ():
            if component.get("kind") != "MonoBehaviour":
                continue
            path_id = int(component.get("path_id") or 0)
            if not path_id:
                continue
            try:
                tree = source.read_typetree(path_id)
            except Exception:  # noqa: BLE001 -- 个别组件解不开不该废掉整张表
                continue
            if not isinstance(tree, Mapping):
                continue
            if any(field not in tree for field in SKIN_ROOT_FIELDS):
                continue
            data = (tree.get("Skins") or {}).get("Data")
            if isinstance(data, (str, bytes)) or not isinstance(data, Sequence):
                continue
            for index, entry in enumerate(data):
                inner = (entry or {}).get("Data") or {}
                keys = tuple(
                    key
                    for key in (_pptr_id(item) for item in (inner.get("_keyData") or ()))
                    if key
                )
                renderers: list[int] = []
                for value in inner.get("_valueData") or ():
                    renderers += [pid for pid in _renderer_ids(value) if pid not in renderers]
                out.append(SkinSlot(index=index, materials=keys, renderers=tuple(renderers)))
    return tuple(out)


def _skin_family(
    slots: Sequence[SkinSlot], target: SkinSlot, source: Any
) -> tuple[str, ...]:
    """**同族**：与目标落在**同一组渲染器**上的其它材质名（同一个皮肤字典里的）。

    说人话：一台车有几个涂装，每个涂装是一颗材质 —— 改其中一颗，切到别的涂装时
    `Renderer.SetMaterials` 会把整套换成那一颗（kb/18 §5）⇒ 那几颗就是"会顶掉你改动"的同族。
    渲染器读不出来时就退回**整个字典里的其它材质**（宁可多报，⛔ 不许漏报）。
    """
    peers = (
        [one for one in slots if one.renderers == target.renderers]
        if target.renderers
        else list(slots)
    )
    names: list[str] = []
    for one in peers:
        for path_id in one.materials:
            if path_id in target.materials:
                continue
            name = _texture_name_of(source, path_id)
            if name not in names:
                names.append(name)
    return tuple(sorted(names))


def _skin_label(slot: SkinSlot, path_id: int) -> str:
    """人话来路：这颗材质挂在皮肤字典的哪一格上。"""
    order = slot.materials.index(path_id) if path_id in slot.materials else 0
    return f"皮肤字典 Skins.Data[{slot.index}].Data._keyData[{order}]"


def _blank_material(name: str, sources: tuple[str, ...]) -> dict[str, Any]:
    """皮肤字典里新冒出来的材质：形状与 `unitypy_source._collect_material` 的兜底那一支一致。"""
    return {
        "name": name,
        "shader": "",
        "texture_slots": (),
        "floats": {},
        "colors": {},
        "valid_keywords": (),
        "invalid_keywords": (),
        "sources": sources,
    }


def _materials_of(unit: _OpenUnit) -> dict[int, dict[str, Any]]:
    """这个 prefab 引用到的材质表 **＋ 皮肤字典里的那几颗**（⛔ 只读；两批合并成一张表）。

    ## 为什么要合并（2026-10-11 修的那一条）

    原先只有 prefab 渲染器直接引用的那几颗 ⇒ 用户改的正是"默认涂装"那颗，而运行期一旦切涂装，
    `Renderer.SetMaterials` 会把整套材质换成**皮肤字典里的另一颗**（kb/18 §5）——
    改动被顶掉，而且从 `vt` 的输出上完全看不出来。
    实测 T-72B：prefab 直接引用的 3 颗，而皮肤字典用的是 `RU_T72B 1..5`
    （`_work/audit-B-assets.md` §4.1）。

    每条记录在原来那几样之外多三个键（老调用方读 `name` 不受影响）：

    * `sources`：来路标签（`prefab 渲染器` / `皮肤字典 Skins.Data[i].Data._keyData[j]`）；
    * `kin`：**同族**材质名（同一组渲染器上的其它颗）—— 切涂装会顶掉这一颗的就是它们；
    * `skin_index`：它在皮肤字典里的下标（不在字典里就没有这个键）。
    """
    nodes, materials = unit.source.read_prefab_nodes(unit.asset_path)
    slots = skin_slots(unit.source, nodes)
    skin_of: dict[int, list[SkinSlot]] = {}
    for slot in slots:
        for path_id in slot.materials:
            skin_of.setdefault(path_id, []).append(slot)
    for path_id, holders in skin_of.items():
        labels = tuple(_skin_label(slot, path_id) for slot in holders)
        record = materials.get(path_id)
        if record is None:
            # 读法共用 `_texture_name_of`：它读的就是对象的 m_Name（贴图 / 材质同一件事）。
            record = _blank_material(_texture_name_of(unit.source, path_id), labels)
            materials[path_id] = record
        else:
            record["sources"] = (SOURCE_PREFAB, *labels)
        record["skin_index"] = holders[0].index
        record["kin"] = _skin_family(slots, holders[0], unit.source)
    for record in materials.values():
        if "sources" not in record:
            record["sources"] = (SOURCE_PREFAB,)
    return materials


def _texture_name_of(source: Any, path_id: int) -> str:
    """pathID -> 贴图名（读不出就给一个人话占位，⛔ 不抛也不编）。"""
    try:
        tree = source.read_typetree(int(path_id))
    except KeyError:
        return f"<包内没有 pathID={int(path_id)}>"
    except Exception:  # noqa: BLE001 -- UnityPy 对空/跨文件 PPtr 会抛各种异常
        return f"<读不出 pathID={int(path_id)}>"
    return str(tree.get("m_Name") or f"<无名 pathID={int(path_id)}>")


def texture_inventory(source: Any) -> dict[str, tuple[int, ...]]:
    """整包的 Texture2D 名单：名字 -> pathID 列表。

    ⛔ 只读；重名**不合并**（留给 resolve_texture 拒绝）。只收「读得出名字」的对象：
    读不出名字的东西没法按名字指名，列进来只会误导。
    """
    buckets: dict[str, list[int]] = {}
    for path_id in source.object_ids("Texture2D"):
        try:
            name = str(source.read_typetree(int(path_id)).get("m_Name") or "")
        except Exception:  # noqa: BLE001 -- 个别对象读不出名字不该废掉整份名单
            continue
        if name:
            buckets.setdefault(name, []).append(int(path_id))
    return {name: tuple(sorted(ids)) for name, ids in buckets.items()}


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #
def list_materials(game_root: str | Path, unit_key: str) -> list[str]:
    """这个单位的 prefab 引用到的**材质名**（去重、排序）。"""
    with _open_unit(game_root, unit_key) as unit:
        materials = _materials_of(unit)
    return sorted({str(material.get("name") or "") for material in materials.values()})


def list_slots(game_root: str | Path, unit_key: str, *, material_name: str) -> dict[str, str]:
    """某个材质的**槽名 -> 当前贴图名**（空 PPtr / 读不出的贴图给人话占位）。"""
    with _open_unit(game_root, unit_key) as unit:
        material_id = _find_material(_materials_of(unit), material_name, unit_key)
        tree = unit.source.read_typetree(material_id)
        out: dict[str, str] = {}
        for entry in _tex_envs(tree):
            if len(entry) < 2:
                continue
            slot = str(entry[0])
            pptr = _pptr_of(entry[1])
            path_id = 0 if pptr is None else int(pptr.get("m_PathID", 0) or 0)
            out[slot] = "<空 PPtr>" if not path_id else _texture_name_of(unit.source, path_id)
    return out


@dataclass(frozen=True, slots=True)
class SkinSwapPlan:
    """一次「修改材质槽的指向」的干跑结果（⛔ 一个字节都没写）。"""

    game_root: Path
    unit_key: str
    bundle: Path
    bundle_name: str
    catalog_path: Path
    material_name: str
    material_path_id: int
    material_bytes: int
    slot: str
    old_texture: str
    old_texture_path_id: int
    new_texture: str
    new_texture_path_id: int
    #: 等长的新对象字节（writer.ObjectChange）—— install_swap 就把它交给 writer。
    change: writer.ObjectChange
    #: writer 的干跑计划（对象字节数 原/新/级别 + 命中块 + catalog CRC 全在里面）。
    writeback: writer.WritebackPlan
    #: 新旧对象字节的差异段（对象内相对偏移）。真机那条 = 一段 8 字节。
    changed_spans: tuple[tuple[int, int], ...] = ()

    @property
    def changed_bytes(self) -> int:
        return sum(end - start for start, end in self.changed_spans)

    def object_stream_start(self) -> int | None:
        """目标对象在**解压流**里的绝对起点（writer 那一行的 stream_start）。"""
        for row in self.writeback.rows:
            if row.path_id == self.material_path_id:
                return int(row.stream_start)
        return None

    def absolute_spans(self) -> tuple[tuple[int, int], ...]:
        """改动在解压流里的绝对区间（对得上真机记录的 abs 938,569,856 那一条）。"""
        start = self.object_stream_start()
        if start is None:
            return ()
        return tuple((start + lo, start + hi) for lo, hi in self.changed_spans)

    def criterion_lines(self) -> tuple[str, ...]:
        """**判据一行**：包名 / 材质 / 槽 / 旧→新贴图 / 字节数 / 级别（后面接 writer 的那几行）。"""
        head = (
            f"[修改材质槽的指向] {self.bundle_name}（{self.unit_key}）："
            f"材质『{self.material_name}』 pathID={self.material_path_id}  槽 {self.slot}："
            f"旧值 {self.old_texture}({self.old_texture_path_id}) → "
            f"新值 {self.new_texture}({self.new_texture_path_id})"
        )
        spans = self.absolute_spans()
        where = "、".join(f"abs {lo:,}~{hi:,}" for lo, hi in spans) or "（无差异）"
        return (
            head,
            f"  改动 {self.changed_bytes} 字节（{len(spans)} 段）：{where}"
            f"   对象字节 {self.material_bytes:,} B 原样长度（等长）",
            *self.writeback.criterion_lines(),
        )

    def summary(self) -> str:
        return (
            f"{self.bundle_name}：修改材质槽 {self.slot} 的指向（材质 {self.material_name}，"
            f"旧值 {self.old_texture} → 新值 {self.new_texture}，{self.changed_bytes} 字节）"
        )


def guard_slot_target(
    source: Any, material_tree: Mapping[str, Any], slot: str, texture_id: int
) -> str:
    r"""⛔ **别把 PBR 数据图塞进颜色槽** —— 改材质槽之前的守卫。

    ## 为什么长在这里（2026-10-10 用户点名）

    > 用户原话：「**知识不应该只在知识库里，而应该在每一个相关的地方都让你们注意，代码、脚本**」

    这条知识原先只写在 `kb/21`（贴图方向契约）· `kb/22`（UnitV3 档案）· `kb/18 §6`（渲染）里，
    而**改材质槽的那条代码路径从来不去问它** ⇒ 同一天连错三次。

    ## 判据（引擎自己标的，不靠贴图名猜）

    * **槽的角色**：`materials.MaterialTable.role_of(slot)`（表在 `materials.TABLES`）——
      `Layer_*` = `base_color` · `_VirtualDetailNormal` = `normal` · `_Emission` = `emission`。
    * **贴图的性子**：`Texture2D.m_ColorSpace` ——
      **`0` = Linear = 数据图**（法线 / 遮罩 / 粗糙度 / AO）·
      **`1` = sRGB = 颜色图**（albedo / emission）。
      实测（`_work/colorspace.py`）：`Javelin_JAVELIN_Normal` = **0**，
    其余 albedo / 涂装图 = **1**。

    ## 反面教材（真发生，代价是用户两次启动）

    `Javelin_JAVELIN_Normal`（法线图 · Linear · **平均 alpha 125.6 · 84.4% 像素 A<128**）
    被塞进 `Layer_A97CDC25`（Albedo）⇒ **车身整片被抠成透明**，只剩粉色碎片。

    ## 返回

    槽的角色（认不出返回 `""`，**不猜**）。
    """
    # ① 槽的角色 —— 只有 shader 认得出来才算数（认不出返回 ""，**不猜**）
    shader = ""
    try:
        shp = int((material_tree.get("m_Shader") or {}).get("m_PathID", 0) or 0)
        if shp:
            st = source.read_typetree(shp)
            shader = str((st.get("m_ParsedForm") or {}).get("m_Name") or st.get("m_Name") or "")
    except Exception:  # noqa: BLE001 — 读 shader 名只是「能不能判」的前置；读不到就当认不出（返回 ""，不拦）
        shader = ""
    table = _mat.table_for(shader)
    role = table.role_of(slot) if table is not None else None
    if not role:
        return ""

    # ② 贴图的性子 —— 引擎自己标的（m_ColorSpace：0=Linear 数据图 / 1=sRGB 颜色图）
    try:
        linear = source.read_typetree(texture_id).get("m_ColorSpace") == 0
    except Exception:  # noqa: BLE001 — 读不到 m_ColorSpace 就不拦；⛔ 别拿「读不到」当红判据
        return role

    if role == _mat.ROLE_BASE_COLOR and linear:
        raise AssetError(
            f"槽 {slot!r} 的角色是「颜色图」（{role}），"
            f"而目标贴图 pathID={texture_id} 的 m_ColorSpace=0（Linear）"
            "⇒ 它是**数据图**",
            hint="⛔ 别把 PBR 数据图（法线 / 遮罩 / 粗糙度 / AO）塞进颜色槽 —— "
                 "实测「Javelin_JAVELIN_Normal」（法线图 · 平均 alpha 125.6 · 84.4% 像素 A<128）"
                 "塞进「Layer_A97CDC25」会让**整个车身被抠成透明**。"
                 "颜色槽要的是 m_ColorSpace=1（sRGB）的图，例如别的车的 *_BaseMap。"
                 "依据 kb/18 §6（渲染）· kb/21（贴图方向契约）· kb/22（UnitV3 档案）",
        )
    if role == _mat.ROLE_NORMAL and not linear:
        raise AssetError(
            f"槽 {slot!r} 的角色是「法线」（{role}），"
            f"而目标贴图的 m_ColorSpace=1（sRGB）⇒ 它是**颜色图**",
            hint="⛔ 法线槽要 Linear（数据图）；塞颜色图会得到错误的着色。",
        )
    return role


def plan_swap(
    game_root: str | Path,
    unit_key: str,
    *,
    material_name: str,
    slot: str,
    texture_name: str,
) -> SkinSwapPlan:
    """**干跑**：算出「换哪几个字节」+ writer 那条写回计划的真值（⛔ 只读、不写、不建补丁）。

    链接：unit_key -> catalog -> 唯一命中包 -> prefab 的材质 -> 槽 PPtr -> 新贴图 pathID
    -> writer.build_material_change（四条自证：往返逐字节相同 / 等长 / 读回 PPtr == 新值）
    -> writer.write_objects(dry_run=True)（命中块 / 双 CRC / catalog 平移全算出来）。
    """
    root = Path(game_root)
    with _open_unit(root, unit_key) as unit:
        material_id = _find_material(_materials_of(unit), material_name, unit_key)
        tree = unit.source.read_typetree(material_id)
        old_pptr = slot_texture_pptr(tree, slot)
        old_texture_id = int(old_pptr.get("m_PathID", 0) or 0)
        new_texture_id = resolve_texture(texture_inventory(unit.source), texture_name)
        # ⛔ 知识长在路径上：颜色槽 ↔ 数据图 的错配在这里当场拦下（见 guard_slot_target）
        guard_slot_target(unit.source, tree, slot, new_texture_id)
        new_pptr = swapped_pptr(old_pptr, path_id=new_texture_id)
        obj = unit.source.object_at(material_id)
        if obj is None:
            raise AssetError(
                f"这个包里没有 pathID={material_id} 的对象",
                hint="材质表与对象表对不上 —— 重开一次；⛔ 不要拿相近的 pathID 顶替",
            )
        old_bytes = bytes(obj.get_raw_data())
        change = writer.build_material_change(unit.source, material_id, textures={slot: new_pptr})
        old_texture = _texture_name_of(unit.source, old_texture_id)
        bundle, bundle_name, catalog_path = unit.bundle, unit.bundle_name, unit.catalog
    spans = changed_spans(old_bytes, change.payload)
    report = writer.write_objects(
        bundle, [change], dry_run=True, game_root=root, catalog_path=catalog_path
    )
    return SkinSwapPlan(
        game_root=root,
        unit_key=str(unit_key),
        bundle=bundle,
        bundle_name=bundle_name,
        catalog_path=catalog_path,
        material_name=str(material_name),
        material_path_id=int(material_id),
        material_bytes=len(old_bytes),
        slot=str(slot),
        old_texture=old_texture,
        old_texture_path_id=old_texture_id,
        new_texture=str(texture_name),
        new_texture_path_id=new_texture_id,
        change=change,
        writeback=report.plan,
        changed_spans=spans,
    )


def install_swap(
    plan: SkinSwapPlan, *, project_root: str | Path, dry_run: bool = False
) -> writer.WriteReport:
    """执行一次安装（dry_run=True 只算不写）。

    * 写回那一趟全在 writer.write_objects 里：等长覆盖 + 重算 CRC + **成对**更新 catalog；
    * patch_store 给了就走 ADR 0007 的反向 Δ（⛔ 不拷整包）—— 补丁落
      <工程目录>/patches/（PatchStore 自带同盘守卫），还原走 restore()；
    * ⛔ 游戏在跑会被 writer 拦下（is_game_running），这里不再另写一份。
    """
    store = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(project_root), game_root=plan.game_root
    )
    return writer.write_objects(
        plan.bundle,
        [plan.change],
        dry_run=dry_run,
        project_root=Path(project_root),
        game_root=plan.game_root,
        catalog_path=plan.catalog_path,
        patch_store=store,
        patch_label=(
            f"皮肤：修改材质槽 {plan.slot} 的指向（{plan.unit_key} {plan.material_name}，"
            f"旧值 {plan.old_texture} → 新值 {plan.new_texture}）"
        ),
    )


def restore(
    project_root: str | Path, *, game_root: str | Path | None = None
) -> reverse_delta.RestoreReport:
    """还原上一次安装（反向 Δ 重放）：**成功即删补丁**。

    两把锁都在 PatchStore.restore 里：当前文件 sha256 != 补丁的「装后」⇒ 拒绝自动还原
    （防误伤用户后来的改动）；重放出来的旧文件 sha256 != 「装前」⇒ 一个字节都不替换。
    """
    store = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(project_root), game_root=game_root
    )
    return store.restore()
