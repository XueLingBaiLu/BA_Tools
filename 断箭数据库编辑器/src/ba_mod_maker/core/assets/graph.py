r"""资产目录：一个地址键 -> 模型包 / prefab / 渲染器 / 材质 / 贴图 / VT 池。

## 这一层回答什么

    ba-mod-maker assets graph RU_T72B
      -> 这台车的模型包是哪个、prefab 的 pathID 是多少、
         13 个渲染器各用哪颗材质、那些材质指向哪几张贴图、材质的 VT 栈指哪个池子。

每一行都带 **可读名 + pathID + 出处（在哪个包里）** —— 三者缺一不可，
因为「这台车用了哪些资产」既有**人看**的用途（哪些是共用的、改一颗会牵动谁），
也有**机器用**的用途（pathID 才是 Unity 里的身份，kb/20 §5 坑 2：文件名不是身份）。

## 计数口径（J-T5-1 的正控就是拿它跟独立探针对读数）

    prefabs   = 1（容器里那条地址指向的那一个 prefab 根）
    renderers = prefab 树里渲染器组件的 pathID 去重数
    materials = 这些渲染器的 m_Materials 里**非空** PPtr 指向的材质，pathID 去重
    textures  = 这些材质 m_SavedProperties.m_TexEnvs 各项 m_Texture 里**非空**的，pathID 去重

⚠️ 三个数都是 **prefab 树可达**的口径，**不是「包内有几个」**：
同一个包里有 2,320 颗材质，而 RU_T72B_Base.prefab 只碰得到 3 颗
（kb/18 §5 记过同一件事：783 = 可达材质 / 2,320 = 包内材质，两个数都对、不冲突）。

## ⛔ 它不做什么

* **只读**：不开写通道、不建补丁柜、不碰 catalog，一个字节都不写；
* 不跟数据库的 `Modifications` / `Turrets` 那条线（那是「按数据库点亮零件」，另一跳）；
* 不把「名字」当身份：任何按名字定位的用途都得先过 pathID。

## 分层

本模块在 `core/` 下 —— **零 Qt**（tests/test_core_layering.py 用 AST 扫）。
真正开包的那几件（`unitypy_source` / `prefab`）是**函数内延迟 import**：
① 与 asset_kernel -> core.assets.game 那条依赖方向互不打扰；
② `--help` 不必付 UnityPy 的启动代价。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

#: 条目类别。顺序 = 输出顺序，也是从外到内的那条链：
#: 包 -> prefab -> 渲染器 -> 材质 -> 贴图 -> VT。
KINDS: tuple[str, ...] = ("bundle", "prefab", "renderer", "material", "texture", "vt")

#: 类别 -> 中文标签（人读那几行的抬头）。
KIND_LABEL: dict[str, str] = {
    "bundle": "模型包",
    "prefab": "prefab",
    "renderer": "渲染器",
    "material": "材质",
    "texture": "贴图",
    "vt": "VT 池",
}

#: VT 池文件住在哪儿（相对游戏根）——与 asset_kernel.vt_stack 同一份事实的只读投影。
STREAMING_SUBDIR: tuple[str, ...] = ("BrokenArrow_Data", "StreamingAssets")


# --------------------------------------------------------------------------- #
# 条目与图（纯数据，可离线断言）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AssetNode:
    """资产目录里的一条：**可读名 + pathID + 出处**。"""

    kind: str
    name: str
    #: Unity 的 pathID；0 = 这条没有包内对象（模型包自己 / VT 组名）。
    path_id: int = 0
    #: 出处：包文件名，或「外部(fileID=N)」，或 StreamingAssets。
    source: str = ""
    #: 附注（渲染器的组件类型 / 材质的 shader / 贴图用在哪个槽…）。
    detail: str = ""

    def line(self) -> str:
        where = f"pathID={self.path_id}" if self.path_id else "pathID=—"
        parts = [self.name, where, f"出处={self.source or '—'}"]
        if self.detail:
            parts.append(self.detail)
        return "  " + "  ".join(parts)


@dataclass(frozen=True, slots=True)
class UnitAssetGraph:
    """一个地址键的资产目录（只读视图）。"""

    key: str
    asset_path: str
    bundle_name: str
    bundle_path: str
    catalog_path: str
    nodes: tuple[AssetNode, ...]
    #: catalog 给的候选包（**未复核**的那一份；复核过的那个在 bundle_name 上）。
    candidates: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def of_kind(self, kind: str) -> tuple[AssetNode, ...]:
        return tuple(node for node in self.nodes if node.kind == kind)

    def counts(self) -> dict[str, int]:
        """每类条目数（六类恒有键，取不到就是 0）。"""
        return {kind: len(self.of_kind(kind)) for kind in KINDS}

    def criterion_lines(self) -> tuple[str, ...]:
        """判据那一段：键 / 地址 / 包 / 逐类条数 / 计数口径。"""
        counts = self.counts()
        return (
            f"键        {self.key}",
            f"地址      {self.asset_path}",
            f"模型包    {self.bundle_name}",
            "条目      " + " · ".join(f"{KIND_LABEL[k]} {counts[k]}" for k in KINDS),
            "计数口径  渲染器/材质/贴图 = prefab 树可达对象的 pathID 去重数"
            "（⛔ 不是「包内有几个」）",
        )

    def lines(self) -> tuple[str, ...]:
        """人读输出（CLI 逐行 print）。"""
        out: list[str] = list(self.criterion_lines())
        for kind in KINDS:
            rows = self.of_kind(kind)
            if not rows:
                continue
            out.append("")
            out.append(f"{KIND_LABEL[kind]}（{len(rows)}）")
            out.extend(row.line() for row in rows)
        if self.notes:
            out.append("")
            out.extend(f"⚠ {note}" for note in self.notes)
        return tuple(out)

    def document(self) -> dict[str, Any]:
        """`--json` 那一份（形状稳定：加字段不涨版本）。"""
        return {
            "key": self.key,
            "asset_path": self.asset_path,
            "bundle": {"name": self.bundle_name, "path": self.bundle_path},
            "catalog": self.catalog_path,
            "candidates": list(self.candidates),
            "counts": self.counts(),
            "criterion": "renderers/materials/textures = prefab 树可达对象的 pathID 去重数",
            "nodes": [
                {
                    "kind": node.kind,
                    "name": node.name,
                    "path_id": node.path_id,
                    "source": node.source,
                    "detail": node.detail,
                }
                for node in self.nodes
            ],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# 纯函数：一棵 prefab 树 -> 条目表（用例直接喂造出来的树也能跑）
# --------------------------------------------------------------------------- #
def _name_of(names: Mapping[int, str] | None, path_id: int, fallback: str) -> str:
    if not path_id:
        return fallback
    got = (names or {}).get(path_id)
    return got or f"<无名 pathID={path_id}>"


def nodes_from_tree(
    tree: Any,
    *,
    asset_path: str,
    prefab_path_id: int,
    bundle_name: str,
    bundle_path: str = "",
    bundle_detail: str = "",
    container_count: int = 0,
    #: 容器里**唯一**路径的条数（容器表允许同一个路径登记两次：<n 总条数）。
    container_unique: int = 0,
    texture_names: Mapping[int, str] | None = None,
    mesh_names: Mapping[int, str] | None = None,
    vt_items: Mapping[int, Sequence[tuple[str, str]]] | None = None,
    vt_pool_state: Mapping[str, str] | None = None,
) -> tuple[AssetNode, ...]:
    """**纯函数**：把已经读好的 prefab 树摊成条目表。

    :param tree: 任何有 `.nodes` / `.renderers` / `.materials` / `.texture_ids()` 的对象
        （生产上就是 `asset_kernel.prefab.PrefabTree`；
        用例里可以直接喂 `prefab.build_tree` 的产物）。
    """
    nodes: list[AssetNode] = []

    # ① 模型包（这一条没有 pathID：包本身不是包内对象）
    bundle_bits = [bundle_detail] if bundle_detail else []
    if container_count:
        dupes = (
            f"（唯一路径 {container_unique}）"
            if 0 < container_unique < container_count
            else ""
        )
        bundle_bits.append(f"容器 {container_count} 条地址{dupes}")
    nodes.append(
        AssetNode(
            kind="bundle",
            name=bundle_name,
            source=bundle_path or "—",
            detail=" · ".join(bundle_bits),
        )
    )

    # ② prefab 自己
    leaf = asset_path.replace(chr(92), "/").rsplit("/", 1)[-1]
    nodes.append(
        AssetNode(
            kind="prefab",
            name=leaf,
            path_id=int(prefab_path_id),
            source=bundle_name,
            detail=asset_path,
        )
    )

    # ③ 渲染器（顺序 = 树里的顺序，稳定可复算）
    path_of: dict[int, str] = {}
    for node in getattr(tree, "nodes", ()) or ():
        path_of[int(getattr(node, "transform_id", 0) or 0)] = str(getattr(node, "path", "") or "")
    for renderer in getattr(tree, "renderers", ()) or ():
        mesh_id = int(getattr(renderer, "mesh_id", 0) or 0)
        mesh_file = int(getattr(renderer, "mesh_file", 0) or 0)
        if mesh_file:
            mesh_text = f"mesh=<外部 fileID={mesh_file}>"
        elif mesh_id:
            mesh_text = f"mesh={_name_of(mesh_names, mesh_id, '')}#{mesh_id}"
        else:
            mesh_text = "mesh=—"
        used = [mid for mid in getattr(renderer, "material_ids", ()) or () if mid]
        nodes.append(
            AssetNode(
                kind="renderer",
                name=str(getattr(renderer, "name", "") or "<无名渲染器>"),
                path_id=int(getattr(renderer, "component_id", 0) or 0),
                source=bundle_name,
                detail=" · ".join(
                    [
                        path_of.get(int(getattr(renderer, "node_id", 0) or 0), "")
                        or f"<{getattr(renderer, 'kind', '?')}>",
                        f"{getattr(renderer, 'kind', '?')}",
                        mesh_text,
                        f"材质 {len(used)} 颗",
                    ]
                ),
            )
        )

    # ④ 材质 + ⑤ 贴图（贴图那一段顺带记「用在哪颗材质的哪个槽」）
    known: Mapping[int, Mapping[str, Any]] = getattr(tree, "materials", None) or {}
    referenced: list[int] = []
    for renderer in getattr(tree, "renderers", ()) or ():
        for mid in getattr(renderer, "material_ids", ()) or ():
            if mid and int(mid) not in referenced:
                referenced.append(int(mid))
    # ⚠️ 只列**渲染器引到的**那些 —— 口径是「这台车用得到的那几颗」，
    # 不是「读源顺手读过的那几颗」：读源多给的（以及写坏的）一律不算。
    entries_of: dict[int, Mapping[str, Any]] = {mid: known.get(mid, {}) for mid in referenced}
    slots_of_texture: dict[int, list[str]] = {}
    for material_id in sorted(entries_of, key=lambda mid: str(entries_of[mid].get("name") or "")):
        entry = entries_of[material_id]
        name = str(entry.get("name") or f"<material {material_id}>")
        shader = str(entry.get("shader") or "")
        stacks = tuple(vt_items.get(int(material_id), ())) if vt_items else ()
        detail = [f"shader={shader or '?'}"]
        if stacks:
            detail.append("VT=" + ", ".join(f"{g}/{i}" for g, i in stacks))
        nodes.append(
            AssetNode(
                kind="material",
                name=name,
                path_id=int(material_id),
                source=bundle_name,
                detail=" · ".join(detail),
            )
        )
        for slot in entry.get("texture_slots") or ():
            pair = tuple(slot)[:2] if isinstance(slot, (tuple, list)) else ()
            if len(pair) < 2 or not pair[1]:
                continue
            slots_of_texture.setdefault(int(pair[1]), []).append(f"{name}/{pair[0]}")

    texture_ids = sorted(int(t) for t in (tree.texture_ids() or ()))
    for texture_id in texture_ids:
        used_in = sorted(slots_of_texture.get(texture_id, ()))
        nodes.append(
            AssetNode(
                kind="texture",
                name=_name_of(texture_names, texture_id, f"<texture {texture_id}>"),
                path_id=texture_id,
                source=bundle_name,
                detail=("用在 " + " · ".join(used_in)) if used_in else "未被任何槽指向",
            )
        )

    # ⑥ VT 池（组名 = 32 hex；池文件住在 StreamingAssets）
    pools: dict[tuple[str, str], list[str]] = {}
    for material_id in sorted(entries_of):
        for group, item in (vt_items or {}).get(int(material_id), ()):
            pools.setdefault((group, item), []).append(
                str(entries_of[material_id].get("name") or material_id)
            )
    for (group, item), users in sorted(pools.items()):
        state = (vt_pool_state or {}).get(group, "")
        detail = "用在 " + " · ".join(sorted(set(users)))
        if state:
            detail = f"{detail} · {state}"
        nodes.append(
            AssetNode(
                kind="vt",
                name=f"{group}/{item}",
                source="/".join(STREAMING_SUBDIR),
                detail=detail,
            )
        )
    return tuple(nodes)


# --------------------------------------------------------------------------- #
# 读侧：地址键 -> catalog -> 唯一命中包 -> prefab 树 -> 六类条目
# --------------------------------------------------------------------------- #
def locate(game_root: str | Path, key: str) -> tuple[str, tuple[str, ...], str, str]:
    """地址键 -> (资产路径, 候选包, catalog 路径, bundle 目录)。

    ⛔ 取不到**不许印空表**：抛 `AssetError`，由调用方翻成退出码 2。
    口径与 `asset_kernel.writer.locate_unit_asset` 一致（两边都走 core 的 AddressablesCatalog）。
    """
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog
    from ba_mod_maker.core.assets.game import bundle_dir, catalog_file

    root = Path(game_root)
    aa_dir = bundle_dir(root)
    catalog_path = catalog_file(root)
    catalog = AddressablesCatalog.load(catalog_path.parent)
    if catalog is None:
        raise AssetError(
            f"读不到 catalog：{catalog_path}",
            hint="--game 要指向游戏根（里面有 StreamingAssets\\aa\\catalog.json）",
        )
    hits = catalog.lookups(key)
    if not hits:
        raise AssetError(
            f"没有这个键 {key!r}（catalog 共 {catalog.key_count} 个地址键）",
            hint="键要写 **Units.ModelFileName 的原样值**（例 RU_T72B），不是显示名（T-72B）",
        )
    assets = sorted({asset for asset, _bundle in hits})
    if len(assets) != 1:
        raise AssetError(
            f"地址键 {key!r} 指向 {len(assets)} 个资产，不是唯一命中：{assets}",
            hint="一个键只该有一条地址 —— 多了说明这份 catalog 被写坏过",
        )
    bundles = tuple(sorted({bundle for _asset, bundle in hits if bundle}))
    return assets[0], bundles, str(catalog_path), str(aa_dir)


def _names_of(source: Any, ids: Sequence[int]) -> dict[int, str]:
    """pathID -> 对象名（读不出给空串，⛔ 不抛也不编）。"""
    out: dict[int, str] = {}
    for path_id in ids:
        try:
            tree = source.read_typetree(int(path_id))
        except Exception:  # noqa: BLE001 -- 空/跨文件 PPtr，UnityPy 抛什么都有
            continue
        name = tree.get("m_Name") if isinstance(tree, Mapping) else None
        if isinstance(name, str) and name:
            out[int(path_id)] = name
    return out


def _vt_items_of(
    source: Any, material_ids: Sequence[int]
) -> dict[int, tuple[tuple[str, str], ...]]:
    """材质 pathID -> VT 栈的 (组, 项) 表。

    ⚠️ **故意不复用 `asset_kernel.vt_stack.stack_entries`**：那条是**写侧**的判据，
    字段不在就抛（「⛔ 不许当空栈往下写」—— 写盘前必须严格）。这里是**只读盘点**，
    非 VT 材质（例：LOD 代理用的 HDRP/Unlit）本来就没有这张表，抛在这里等于
    把一整台车的目录废掉。⇒ 读侧宽容、写侧严格，两边的严格程度不一样是**有意的**。
    """
    out: dict[int, tuple[tuple[str, str], ...]] = {}
    for material_id in material_ids:
        try:
            tree = source.read_typetree(int(material_id))
        except Exception:  # noqa: BLE001 -- 同上：读不出就当这颗料没有 VT 栈
            continue
        if not isinstance(tree, Mapping):
            continue
        entries: list[tuple[str, str]] = []
        for item in tree.get("m_BuildTextureStacks") or ():
            if not isinstance(item, Mapping):
                continue
            group = item.get("groupName")
            name = item.get("itemName")
            if isinstance(group, str) and isinstance(name, str) and group:
                entries.append((group, name))
        if entries:
            out[int(material_id)] = tuple(entries)
    return out


def pool_state_of(streaming_dir: Path, group: str) -> str:
    """这个 VT 组在 StreamingAssets 里就位了没有（**只读**：一个字节都不搬）。

    ⛔ 不复用 `vt_stack.pool_status`：那个要的是「一个池子目录」，
    这里只有**组名**（`<组>.gts` ＋ `<组>*_*.gtp` 那几个分片）。
    """
    if not streaming_dir.is_dir():
        return "StreamingAssets 不在（无法核对池子）"
    tables = sorted(streaming_dir.glob(f"{group}*.gts"))
    pages = sorted(streaming_dir.glob(f"{group}*.gtp"))
    if not tables and not pages:
        return "池子缺（游戏会 Could not load 这个组）"
    total = sum(path.stat().st_size for path in (*tables, *pages))
    return f"池子就位：{len(tables)} 个 .gts + {len(pages)} 个 .gtp = {total:,} B"


def build_unit_graph(
    game_root: str | Path,
    key: str,
    *,
    cached: Any = None,
) -> UnitAssetGraph:
    """读一遍：地址键 -> 模型包 / prefab / 渲染器 / 材质 / 贴图 / VT 池。

    :param cached: 已打开的 `unitypy_source.AssetSource`（省一次 3.38 GB 开包）；
        给了它就不再自己开包，也不再自己关。
    :raises AssetError: 键不在 catalog / 命中不唯一 / 读不出 prefab —— 都由调用方翻成退出码 2。
    """
    from ba_mod_maker.asset_kernel import prefab as prefab_mod
    from ba_mod_maker.asset_kernel import unitypy_source

    asset_path, candidates, catalog_path, aa_dir = locate(game_root, key)

    handle = None
    source = cached
    if source is None:
        try:
            handle = unitypy_source.open_unique_source(Path(aa_dir), candidates, asset_path)
            source, bundle_name = handle.__enter__()
        except FileNotFoundError as exc:
            raise AssetError(
                f"{key!r} 的资产 {asset_path!r} 在 {len(candidates)} 个候选包里没有唯一命中：{exc}",
                hint="catalog 的 group 会过度包含 —— 唯一命中由 AssetBundle.m_Container 复核",
            ) from exc
    else:
        bundle_name = Path(str(getattr(source, "path", ""))).name
    try:
        try:
            nodes_raw, materials = source.read_prefab_nodes(asset_path)
            tree = prefab_mod.build_tree(nodes_raw, materials)
            entries = source.container_entries()
            container = dict(entries)
            texture_names = _names_of(source, tree.texture_ids())
            mesh_ids = sorted(
                {int(r.mesh_id) for r in tree.renderers if r.mesh_id and not r.mesh_file}
            )
            mesh_names = _names_of(source, mesh_ids)
            vt_items = _vt_items_of(source, sorted(tree.materials))
        except (KeyError, ValueError) as exc:
            raise AssetError(
                f"{key!r} 的 prefab 读不出来（{asset_path}）：{exc}",
                hint="这棵树不符合 prefab 的形状 —— 先确认地址键没打错（打错会指到别的东西上）",
            ) from exc
    finally:
        if handle is not None:
            handle.__exit__(None, None, None)

    notes: list[str] = []
    if len(candidates) > 1:
        notes.append(
            f"catalog 给了 {len(candidates)} 个候选包，"
            f"m_Container 复核后唯一命中 {bundle_name}；候选：{list(candidates)}"
        )
    bundle_path = str(Path(aa_dir) / bundle_name)
    try:
        bundle_detail = f"{Path(bundle_path).stat().st_size:,} B"
    except OSError:  # pragma: no cover - 包不在盘上（复核已过，这里只是读数）
        bundle_detail = ""
    streaming = Path(game_root).joinpath(*STREAMING_SUBDIR)
    groups = sorted({group for items in vt_items.values() for group, _item in items})
    vt_pool_state = {group: pool_state_of(streaming, group) for group in groups}
    external = sorted({r.mesh_file for r in tree.renderers if r.mesh_file})
    if external:
        notes.append(
            f"有 {len(external)} 个外部网格 fileID={external}（不是本包里的对象，本目录不追进去）"
        )

    return UnitAssetGraph(
        key=key,
        asset_path=asset_path,
        bundle_name=bundle_name,
        bundle_path=bundle_path,
        catalog_path=catalog_path,
        nodes=nodes_from_tree(
            tree,
            asset_path=asset_path,
            prefab_path_id=int(container.get(asset_path, 0) or 0),
            bundle_name=bundle_name,
            bundle_path=bundle_path,
            bundle_detail=bundle_detail,
            container_count=len(entries),
            container_unique=len(container),
            texture_names=texture_names,
            mesh_names=mesh_names,
            vt_items=vt_items,
            vt_pool_state=vt_pool_state,
        ),
        candidates=candidates,
        notes=tuple(notes),
    )


__all__ = [
    "KINDS",
    "KIND_LABEL",
    "STREAMING_SUBDIR",
    "AssetNode",
    "UnitAssetGraph",
    "build_unit_graph",
    "locate",
    "nodes_from_tree",
    "pool_state_of",
]
