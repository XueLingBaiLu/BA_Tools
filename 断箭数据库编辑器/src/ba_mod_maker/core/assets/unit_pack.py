"""单位模型包：**新建空包 / 新增 / 删除 / 修改**（没有「换」）。

产品操作集只有三个原子动作（AGENTS §0.10）：

    新增   create_empty_bundle()（空包）· add_texture()
    删除   delete_object()（按名字删一个对象；指着它的容器条目一起删）
    修改   modify_scale()（改根 Transform 的 m_LocalScale）

⛔ 2026-10-25 用户口径「全删」：**改几何那条内核已退役** —— `add_unit_model` /
`modify_mesh_from_project` / `import_parts_from_project` / `generate_lod_chain` 连同
`asset_kernel` 里那条改网格内核（969 行）一起删了（产品面只留 提取 / 打包 / 验 / 装）。
要改网格就走提取器 + Unity 的 ModelImporter（`pack unity-build`），⛔ 不再自己手写顶点流。

装机口径（2026-10-25 块 B 起）：**落点只有一处** —— `new_asset.install_landing()` =
`<游戏>\\BA_Mod\\`；catalog 的包路径与它**同一处**（`..\\..\\..\\BA_Mod\\`），`aa\\PC` 保持原版。
`install_pack` / `restore_pack` 只转调 `newasset add-model` / `newasset restore`
（⛔ 不再搬包 / 不再 repoint / 不再重记柜子：那是落点不一致时代的补丁）。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone, streaming, texture_build
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.core.asset import new_asset, ufs
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "builtin_source_meta", "schema_drift", "create_empty_bundle",
    "add_texture",
    "update_texture", "update_textures_from_dir", "set_slot_textures", "asset_readings",
    "delete_object", "modify_scale",
    "list_objects", "list_assets", "install_pack",
    "restore_pack", "list_units", "find_game", "bundle_path_of", "resolve_source",
]

AA_PC = Path("BrokenArrow_Data/StreamingAssets/aa/PC")
CATALOG = Path("BrokenArrow_Data/StreamingAssets/aa/catalog.bin")
CATALOG_HASH = Path("BrokenArrow_Data/StreamingAssets/aa/catalog.hash")
#: 我们自己的包在游戏根下的目录名 —— **唯一口径在 `new_asset.MOD_DIR_NAME`**（⛔ 别各写一份）。
MOD_DIR = new_asset.MOD_DIR_NAME

#: 走闭包时可以当**叶子**的类：里面没有我们需要的跨对象引用，解字段树只是白花时间
#: （Mesh 的顶点通道几十 MB，解一次就够呛）。
_LEAF_CLASSES = frozenset({43, 28, 48, 115})

#: 改几何 / 补槽用到的 classID（builder 里没有 Mesh / SMR / Transform / Material 的常量）。
_CLASS_MATERIAL = 21
_CLASS_SHADER = 48
_CLASS_TRANSFORM = 4
_CLASS_MESH = 43
_CLASS_SKINNED_MESH_RENDERER = 137
_CLASS_MESH_RENDERER = 23


def find_game() -> Path:
    """自动探测游戏根（复用产品的候选表与环境变量）。"""
    from ba_mod_maker.core.assets.game import find_game_root

    root = find_game_root()
    if root is None:
        raise AssetError("找不到游戏根目录（可用 BA_MOD_MAKER_GAME 或 --game 指定）")
    return Path(root)


def _game_paths(game: str | Path) -> tuple[Path, Path, Path]:
    root = Path(game)
    return root, root / CATALOG, root / AA_PC


def bundle_path_of(game: str | Path, bundle_name: str) -> Path:
    """包在哪儿：先看 `BA_Mod\\`（我们的家），再看 `aa\\PC`（原版）。"""
    root, _cat, pc = _game_paths(game)
    ours = root / MOD_DIR / bundle_name
    if ours.is_file():
        return ours
    return pc / bundle_name


@dataclasses.dataclass(slots=True)


class _Pack:
    """一个已落盘的 UnityFS 包在内存里的样子（读进来 → 改 → 写回去）。"""

    path: Path
    name: str
    meta_blob: bytes
    meta: Any
    types: list
    cid: dict[int, int]
    pool: Any
    common: Any
    container: list[tuple[str, int]]
    deps: list[str]
    specs: list[builder.ObjectSpec]
    script_map: dict[int, int]
    script_file: dict[int, int]
    nodes_for: Any
    raw_reader: Any
    node_offset: int = 0
    data_offset: int = 0
    by_pid: dict = field(default_factory=dict)
    #: ★ 2026-10-23：源包 AssetBundle 对象的原始字段树（重写时 `m_PathFlags` /
    #: `m_ExplicitDataLayout` 必须照抄，否则按名字取资产会 MISS —— 见 builder 注释）。
    ab_fields: dict | None = None
    #: ★ 2026-10-24：本包 `.resS` 节点的原始字节（流式贴图那条路）。
    #: 打开既有包时**读回来**；写回时原样再写，否则后面任何一次重写都会把 .resS 丢掉
    #: （实测：add-model 先建流式贴图、紧接着 set-mesh 重写 ⇒ 包只剩一个节点、贴图全空）。
    stream_blob: bytes = b""


def _open_pack(path: str | Path, *, materialize: bool = True) -> _Pack:
    """读一个包：元数据区、容器条目、对象表、依赖与脚本映射。

    `materialize=False` 时**不读 payload**（只读对象表）—— 大包（units 16 万个对象）
    逐对象读 payload 会把人卡死；只有真要写回去时才需要 payload。
    """
    bundle = Path(path)
    r = ufs.UfsReader(bundle)
    nodes = list(r.nodes)
    node0 = nodes[0]
    stream_blob = b""
    if len(nodes) > 1 and str(getattr(nodes[1], "path", "")).endswith(builder.STREAM_NODE_SUFFIX):
        stream_blob = bytes(r.read_range(nodes[1].offset, nodes[1].size))
    info = ufs.read_serialized_file(r, node0)
    meta_blob = bytes(r.read_range(node0.offset, info.data_offset))
    meta = mw.parse_serialized_metadata(meta_blob)
    types = list(meta.types)
    cid = {i: t.class_id for i, t in enumerate(types)}
    common = clone.unitypy_common_strings()
    pool = clone.ref_type_pool(meta, common_strings=common)
    cache: dict[int, Any] = {}

    def nodes_for(tid: int):
        if tid not in cache:
            tt = types[tid].type_tree
            cache[tid] = clone.read_type_nodes(
                struct.pack("<ii", tt.node_count, tt.string_buffer_size) + tt.nodes + tt.strings,
                common_strings=common,
            )
        return cache[tid]

    def raw_of(pid: int) -> bytes:
        o = info.objects[pid] if isinstance(info.objects, dict) else None
        if o is None:
            o = next(x for x in info.objects if x[1] == pid)
        return bytes(r.read_range(node0.offset + info.data_offset + o[2], o[3]))

    by_pid = {o[1]: o for o in info.objects}
    data_offset = int(info.data_offset)
    node_offset = int(node0.offset)
    container: list[tuple[str, int]] = []
    deps: list[str] = []
    ab_fields: dict | None = None
    for pid, o in by_pid.items():
        if cid.get(o[4]) != builder.CLASS_ASSETBUNDLE:
            continue
        ab = clone.read_object(raw_of(pid), nodes_for(o[4]), ref_types=pool)
        for e in ab.get("m_Container") or []:
            if isinstance(e, dict):
                second = e.get("second") or {}
                container.append(
                    (str(e.get("first")), int((second.get("asset") or {}).get("m_PathID", 0)))
                )
        deps = [str(x).lower() for x in (ab.get("m_Dependencies") or [])]
        ab_fields = ab
        break
    specs: list[builder.ObjectSpec] = []
    for pid, o in (by_pid.items() if materialize else ()):
        cls = cid.get(o[4])
        if cls == builder.CLASS_ASSETBUNDLE:
            continue
        si = int(types[o[4]].script_type_index) if cls == builder.CLASS_MONO_BEHAVIOUR else None
        # ★ 2026-10-23：`script_type_index == -1` 必须**原样留着** —— 抹成 None 之后，
        #   写包器遇到「同一 classID=114 有多棵类型树」（Unity 建的包就是这样，实测 5 棵）
        #   就无法定点，`builder.entry_for` 当场以「必须点名 script_index」拒绝。
        #   `_type_id_of` 用的是 `int(spec.script_index or -1)`，留着 -1 正好命中那一棵。
        specs.append(
            builder.ObjectSpec(
                class_id=cls, fields=None, payload=raw_of(pid), path_id=pid,
                script_index=si, label="payload",
            )
        )
    script_map, script_file = {}, {}
    for si, st in enumerate(meta.script_types):
        script_map[int(si)] = int(st.local_identifier)
        script_file[int(si)] = int(st.file_index) + 1
    return _Pack(
        path=bundle, name=bundle.name, meta_blob=meta_blob, meta=meta, types=types, cid=cid,
        pool=pool, common=common, container=container, deps=deps, specs=specs,
        script_map=script_map, script_file=script_file, nodes_for=nodes_for, raw_reader=r,
        node_offset=node_offset, data_offset=data_offset, by_pid=by_pid, ab_fields=ab_fields,
        stream_blob=stream_blob,
    )


def _meta_covers(meta_blob: bytes, class_ids: set[int]) -> bool:
    """这份元数据区的类型表**覆盖**这些 classID 吗（不看 scriptIndex 的细分）。"""
    try:
        table = builder.read_raw_type_table(
            meta_blob, common_strings=clone.unitypy_common_strings()
        )
    except AssetError:
        return False
    return {int(e.class_id) for e in table.entries} >= set(class_ids)


def _engine_version_of(meta_blob: bytes, fallback: Any = None) -> str | None:
    """元数据区里的引擎版本串（`SerializedFile` 的 `unity_version`）。

    2026-10-26 加：重写包必须**照抄源包**的版本串。`builder` 里写死的是 2022.3.62f3，
    而 Unity 6（6000.3.24f1）建出来的包是 6000.x —— 重写后的包顶着 2022 的版本串，
    实测真机**静默不画**（Kord 子炮塔整块消失、游戏日志 0 条相关错误）。
    """
    try:
        ver = str(getattr(mw.parse_serialized_metadata(meta_blob), 'unity_version', '') or '')
    except Exception:  # noqa: BLE001 —— 解不开就退回包里已解析的那份，再不行交给 builder 兜底
        ver = str(getattr(fallback, 'unity_version', '') or '')
    return ver.strip() or None


def _write_pack(pack: _Pack, *, container: Sequence[tuple[str, int]] | None = None,
                deps: Sequence[str] | None = None, extra_preload: Sequence[tuple[int, int]] = (),
                extra_externals: Sequence = (),
                out: str | Path | None = None,
                source_meta: bytes | None = None,
                script_type_map: dict[int, int] | None = None,
                stream_payload: bytes | None = None,
                require_container: bool = True) -> Path:
    """把内存里的包写回去（容器/依赖可覆盖）。

    ★ 2026-10-19：**空包本身没有类型表**（它只写了自己用到的那一个 AssetBundle 项）。
    所以往空包里加东西（例如第一张贴图）时必须换一份覆盖得到的类型表 ——
    先看包自己的，覆盖不了就用**产品内置的**那份，仍覆盖不了才报错。
    """
    dangling = builder.dangling_pptrs(
        pack.specs,
        externals=len(pack.meta.externals) + len(extra_externals),
        script_path_ids=sorted(pack.script_map.values()),
    )
    if dangling:
        raise AssetError("包里有悬空引用：" + "; ".join(list(dangling)[:3]))
    # ★ 2026-10-19：包里一旦**同时有 MonoScript(115) 与 MonoBehaviour(114)**，
    #   重写时必须给 script_type_map（否则 build_object_bundle 直接拒）。
    #   默认按包自己的 script_map 取，且**只留本包内真有的 pathID**。
    if script_type_map is None and pack.script_map:
        own_ids = {int(spec.path_id) for spec in pack.specs}
        derived = {
            int(si): int(pid)
            for si, pid in pack.script_map.items()
            if int(pid) in own_ids
        }
        script_type_map = derived or None
    # ★ 2026-10-26：重写会**换容器节点名**（`CAB-md5(bundle_name)`）⇒ 流式贴图的
    #   `m_StreamData.path` 还指着旧节点 ⇒ 引擎找不到纹素（check-model A5 当场红：49/49）。
    #   `.resS` 是逐字节照搬的（offset/size 不变），所以只要把 path 指到新节点名就行。
    if pack.stream_blob or any(int(spec.class_id) == 28 for spec in pack.specs):
        node = builder.node_path_for(pack.name)
        archive = f"archive:/{node}/{node}{builder.STREAM_NODE_SUFFIX}"
        fixed: list[builder.ObjectSpec] = []
        for spec in pack.specs:
            if int(spec.class_id) != 28 or spec.fields is not None:
                fixed.append(spec)
                continue
            o = pack.by_pid.get(int(spec.path_id))
            if o is None:
                fixed.append(spec)
                continue
            try:
                fields = clone.read_object(
                    spec.payload, pack.nodes_for(o[4]), ref_types=pack.pool)
            except Exception:  # noqa: BLE001 —— 解不开就原样放行，不猜
                fixed.append(spec)
                continue
            stream = fields.get("m_StreamData") if isinstance(fields, dict) else None
            if not isinstance(stream, dict) or int(stream.get("size", 0)) <= 0:
                fixed.append(spec)
                continue
            stream["path"] = archive
            fixed.append(builder.ObjectSpec(
                class_id=int(spec.class_id), fields=fields, path_id=int(spec.path_id),
                script_index=spec.script_index, label=spec.label,
            ))
        pack.specs = fixed
    meta_blob = source_meta if source_meta is not None else pack.meta_blob
    need = {int(spec.class_id) for spec in pack.specs} | {builder.CLASS_ASSETBUNDLE}
    if not _meta_covers(meta_blob, need):
        builtin = builtin_source_meta()
        if _meta_covers(builtin, need):
            meta_blob = builtin
    # ★ 2026-10-26：引擎版本照抄**写进去的那份元数据**（见 _engine_version_of）。
    engine_version = _engine_version_of(meta_blob, pack.meta)
    abf = pack.ab_fields or {}
    plan = builder.build_object_bundle(
        objects=pack.specs, bundle_name=pack.name, source_meta=meta_blob,
        # ★ 2026-10-23：重写既有包时照抄这两个 —— 不抄会让 `LoadAsset(Pascal)` MISS。
        path_flags=abf.get("m_PathFlags") if abf else None,
        explicit_data_layout=abf.get("m_ExplicitDataLayout") if abf else None,
        container=list(pack.container if container is None else container),
        common_strings=pack.common, block_type=builder.BLOCK_LZ4HC,
        preload_all=True, copy_source_tables=True,
        assetbundle_dependencies=list(pack.deps if deps is None else deps),
        extra_preload=tuple(extra_preload),
        script_type_map=script_type_map,
        # ★ 2026-10-26：「只装改过的素材、其余依赖原包」那一跳（乙方案）。
        #   给了就在照抄来的 externals 之后**追加**；它们的 fileID 由 plan 回读。
        extra_externals=tuple(extra_externals),
        stream_payload=stream_payload if stream_payload is not None else (pack.stream_blob or None),
        require_container=require_container,
        engine_version=engine_version,
    )
    target = Path(out) if out else pack.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(plan.data))
    return target

def _asset_path_of_key(catalog_bytes: bytes, key: str) -> str | None:
    """键 -> 资产路径；实现只有一份（`catalog_bin_write.asset_path_of_key`）。"""
    return cbw.asset_path_of_key(catalog_bytes, key)


def _deps_of_key(catalog_bytes: bytes, key: str) -> tuple[str, ...]:
    model = cbw.read_model(catalog_bytes)
    by_offset = {e.loc_offset: e for e in model.entries}
    for entry in model.entries:
        if str(getattr(entry, "primary_key", "")) != key:
            continue
        names: list[str] = []
        for off in entry.src_dep_offsets or ():
            dep = by_offset.get(int(off))
            if dep is not None and dep.internal_id:
                names.append(Path(dep.internal_id.replace("\\", "/")).name)
        return tuple(names)
    return ()


def _units_bundle(game: Path) -> Path:
    """找装着单位模型的真包（当类型树捐赠者）。"""
    root, _cat, pc = _game_paths(game)
    cands = sorted((root / MOD_DIR).glob("units_assets_all_*.bundle"))
    cands += sorted(pc.glob("units_assets_all_*.bundle"))
    if not cands:
        raise AssetError("游戏里找不到 units_assets_all_*.bundle（类型树来源）")
    return cands[0]


def _monoscripts_bundle(game: Path) -> Path | None:
    """1.2.1 起 MonoScript 不在 units 包里，而在独立的 `*_monoscripts_*.bundle`。

    实测（2026-10-20）：该包 40,133 B / 759 对象 / **758 颗 class 115**，
    包内 CAB 名 = `CAB-0ec21893…`，units 包的 `m_Dependencies` 里逐字有它。
    """
    _root, _cat, pc = _game_paths(game)
    cands = sorted(pc.glob("*_monoscripts_*.bundle"))
    return cands[0] if cands else None


def _meta_with_monoscripts(source: _Pack, ms: _Pack | None) -> bytes:
    """把 units 的类型表加上 monoscripts 包的 **115 类型项**，合成一份元数据区。

    ⛔ 1.2.1 的 units 包**没有 115 对象、类型表里也没有 115 项**（实测 28 类，无 115）；
    模板里要放 MonoScript，就必须把这一项从 monoscripts 包借过来。
    只借**类型项**，对象字节仍由调用方从 monoscripts 包里按 pathID 取。
    """
    table = builder.read_raw_type_table(source.meta_blob, common_strings=source.common)
    if builder.CLASS_MONO_SCRIPT in {int(e.class_id) for e in table.entries}:
        return bytes(source.meta_blob)
    if ms is None:
        raise AssetError(
            "units 包类型表里没有 MonoScript，又找不到 *_monoscripts_*.bundle",
            hint="1.2.1 起脚本在独立的 monoscripts 包；确认 aa/PC 里有这个文件",
        )
    return builder.metadata_with_extra_types(
        source.meta_blob, ms.meta_blob, (builder.CLASS_MONO_SCRIPT,),
        common_strings=source.common,
    )


def _find_root_pid(pack: _Pack, asset_path: str) -> int | None:
    for path, pid in pack.container:
        if path == asset_path:
            return int(pid)
    return None


def _fields_of(pack: _Pack, pid: int):
    """按 pathID 解一棵字段树（用于改名/改缩放/列举）。"""
    for spec in pack.specs:
        if spec.path_id != pid:
            continue
        nodes = pack.nodes_for(_type_id_of(pack, spec))
        return clone.read_object(spec.payload, nodes, ref_types=pack.pool)
    return None


def _type_id_of(pack: _Pack, spec: builder.ObjectSpec) -> int:
    for i, t in enumerate(pack.types):
        if t.class_id != spec.class_id:
            continue
        if spec.class_id != builder.CLASS_MONO_BEHAVIOUR:
            return i
        if int(getattr(t, "script_type_index", -1)) == int(spec.script_index or -1):
            return i
    raise AssetError(f"类型表里没有 classID={spec.class_id}（script_index={spec.script_index}）")


def _tree_key(entry) -> tuple[tuple[int, str, str], ...]:
    """类型表一项的**字段树指纹**：逐节点 `(level, 类型, 字段名)`。

    与 A6 基线同一口径；**不含** `byte_size` / `meta_flag` ——
    那是序列化细节，同一份 C# 类在不同 Unity 小版本上也可能不同。
    """
    return tuple((int(n.level), str(n.type), str(n.name)) for n in entry.nodes)


def schema_drift(bundle: str | Path) -> dict[str, Any]:
    """这个包的类型表与**产品内置那份**（= 当前游戏版本）逐项比，返回漂移项。

    ⛔ 为什么需要：游戏换版后 units 包的类型树会变（实测 1.2.0 ↔ 1.2.1 差 8,000 B）。
    用旧版类型表造的包，在新版里解码会错位（实测崩在 `SerializedPass.m_EditorDataHash`）。
    这里只读**包的元数据区**与产品内置文件，**不碰游戏目录**。
    """
    pack = _open_pack(bundle, materialize=False)
    try:
        got = builder.read_raw_type_table(
            bytes(pack.meta_blob), common_strings=clone.unitypy_common_strings()
        )
    finally:
        pack.raw_reader.close()
    want = builder.read_raw_type_table(
        builtin_source_meta(), common_strings=clone.unitypy_common_strings()
    )
    # ★ 2026-10-23（用户裁定：「我确定 1.2.0 版本不会再回来了」）：**114 项改按字段树比**。
    #   旧口径按 (classID, scriptIndex) 逐字节比 —— 而 scriptIndex 只是「本包里第几个脚本」：
    #   Unity 造的包只有我们自己的 4~5 个类，同槽位装的是**别的类**，比出来的红全是假的。
    #   新口径仍然能红：任何一条 114 项的**字段树**在内置那 22 棵里找不到 ⇒ 换版 / 写错树。
    want_map = {
        (int(e.class_id), int(e.script_index)): e.raw
        for e in want.entries
        if int(e.class_id) != builder.CLASS_MONO_BEHAVIOUR
    }
    want_trees = {
        _tree_key(e)
        for e in want.entries
        if int(e.class_id) == builder.CLASS_MONO_BEHAVIOUR
    }
    drift: list[dict[str, int]] = []
    for entry in got.entries:
        cid = int(entry.class_id)
        if cid == builder.CLASS_MONO_SCRIPT:
            # ★ 1.2.1 起 115 在独立的 monoscripts 包，内置表（从 units 抽的）本来就没有它；
            #   它不是「游戏换版漂移」，⛔ 别把合法的 MonoScript 项判成红。
            continue
        if cid == builder.CLASS_MONO_BEHAVIOUR:
            # 内置表里一棵 114 树都读不出来时**不放行**（fail-closed）。
            if not want_trees or _tree_key(entry) not in want_trees:
                drift.append({"class_id": cid, "script_index": int(entry.script_index)})
            continue
        key = (cid, int(entry.script_index))
        if want_map.get(key) != entry.raw:
            drift.append({"class_id": cid, "script_index": key[1]})
    return {"types": len(got.entries), "drift": drift, "ok": not drift}

def builtin_source_meta() -> bytes:
    """产品**内置**的类型表 —— 建包时唯一的「格式来源」（⛔ 不读游戏包）。

    返回 SerializedFile 的元数据区（类型树 + script_types）。它来自一次抽取，
    之后随产品分发；调用方不需要、也不应该再去打开游戏目录。
    """
    return builder.builtin_source_meta()


def create_empty_bundle(*, key: str, out_dir: str | Path | None = None,
                        source_meta: bytes | None = None,
                        game: str | Path | None = None) -> dict[str, Any]:
    """**新增一个空包**：只有 AssetBundle 对象、容器 0 条。之后用新增动作往里加。

    ⛔ 2026-10-19 起**一个字节都不读游戏目录**：类型表来自产品内置的那一份
    （:func:`builtin_source_meta`），`source_meta` 可覆盖。
    `game` 只为兼容老调用方保留 —— **不再使用**。
    """
    meta_blob = source_meta if source_meta is not None else builtin_source_meta()
    slug = "".join(c if c.isalnum() else "_" for c in key).strip("_").lower() or "bamod"
    digest = hashlib.md5(f"empty|{key}".encode()).hexdigest()
    name = f"{slug}_assets_all_{digest}.bundle"
    plan = builder.build_object_bundle(
        objects=[], bundle_name=name, source_meta=meta_blob,
        container=[], common_strings=clone.unitypy_common_strings(),
        block_type=builder.BLOCK_LZ4HC,
        preload_all=False, copy_source_tables=False,
    )
    out = Path(out_dir) if out_dir else (WORK / "pack")
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_bytes(bytes(plan.data))
    return {"bundle": str(path), "bundle_name": name, "size": len(plan.data),
            "crc32": int(plan.crc32), "objects": 0, "container": 0}


def list_assets(bundle: str | Path) -> list[str]:
    """容器里登记的资产路径（catalog 的 internalId 就写这个）。"""
    pack = _open_pack(bundle, materialize=False)
    try:
        return [p for p, _pid in pack.container]
    finally:
        pack.raw_reader.close()


def list_objects(bundle: str | Path) -> list[dict[str, Any]]:
    """列出包里的对象（名字 + classID + pathID）。素材/网格/贴图都能看见。"""
    pack = _open_pack(bundle)
    rows: list[dict[str, Any]] = []
    try:
        for spec in pack.specs:
            name = ""
            if int(spec.class_id) in _LEAF_CLASSES:
                rows.append(
                    {"path_id": int(spec.path_id), "class_id": int(spec.class_id), "name": ""}
                )
                continue
            try:
                fields = _fields_of(pack, spec.path_id)
                if isinstance(fields, dict):
                    name = str(fields.get("m_Name", ""))
            except AssetError:
                name = ""
            rows.append(
                {"path_id": int(spec.path_id), "class_id": int(spec.class_id), "name": name}
            )
    finally:
        pack.raw_reader.close()
    return rows

def resolve_source(game: str | Path, key: str) -> tuple[str, Path, int]:
    r"""键 → (资产路径, 装着它的包, 根 pathID)。

    ★ 2026-10-19：**不再硬依赖 `catalog.bin`**。游戏换版后 `aa\` 下可能只有
    `catalog.json`（1.2.0 形态）—— 那时 catalog 读不动，就直接扫 units 包的
    **容器条目**找 prefab（路径里含这个键的那一条），⛔ 不猜路径。
    """
    root, catalog_path, _pc = _game_paths(game)
    asset_path: str | None = None
    names: list[str] = []
    if catalog_path.is_file():
        catalog_bytes = catalog_path.read_bytes()
        asset_path = _asset_path_of_key(catalog_bytes, key)
        if asset_path is None:
            raise AssetError(f"catalog 里没有这个键：{key}")
        names.extend(_deps_of_key(catalog_bytes, key))
    units = _units_bundle(root)
    names.append(units.name)
    names += [p.name for p in sorted((root / AA_PC).glob("*.bundle"))]
    want = key.lower()
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = bundle_path_of(root, name)
        if not path.is_file():
            continue
        pack = _open_pack(path, materialize=False)
        try:
            if asset_path is None:
                paths = [p for p, _pid in pack.container if p.endswith(".prefab")]
                # 先找**末段逐字相等**的（`RU_T72B.prefab`），
                # 找不到才退回包含匹配 —— 否则 `RU_T72B` 会先命中 `RU_T72B3`（实测踩过）。
                hit = next(
                    (p for p in paths
                     if p.rsplit("/", 1)[-1][: -len(".prefab")].lower() == want),
                    None,
                )
                if hit is None:
                    hit = next((p for p in paths if want in p.lower()), None)
                if hit is None:
                    continue
                asset_path = hit
            pid = _find_root_pid(pack, asset_path)
        finally:
            pack.raw_reader.close()
        if pid:
            return asset_path, path, int(pid)
    raise AssetError(
        f"没找到装着 {asset_path or key} 的包",
        hint="catalog 缺失时会扫 units 包的容器条目；键要能在 prefab 路径里出现（例 RU_T72B）",
    )


def _fields_of(pack: _Pack, pid: int):
    """按 pathID 解一棵字段树（用包自己的类型表）。"""
    o = pack.by_pid.get(int(pid))
    if o is None:
        return None
    raw = bytes(pack.raw_reader.read_range(pack.node_offset + pack.data_offset + o[2], o[3]))
    return clone.read_object(raw, pack.nodes_for(o[4]), ref_types=pack.pool)


#: 模板包只抄这些**格式件**（常量定义见下）—— ⛔ 不抄 GameObject/Transform 树：
#: 模板包是给 `add-model --from` 借「通道布局 / 材质闭包 / 脚本引用」的，不是可实例化的 prefab。
_TEMPLATE_CLASSES: tuple[int, ...] = (
    21,    # Material
    28,    # Texture2D
    43,    # Mesh
    48,    # Shader
    49,    # TextAsset
    74,    # AnimationClip
    115,   # MonoScript（m_Script 要指着本包里的那颗）
)


def make_template_pack(*, game: str | Path | None = None, source_key: str,
                       out_dir: str | Path | None = None,
                       bundle: str | Path | None = None) -> dict[str, Any]:
    """★ 2026-10-20（用户定「按需抄」）：从 units 包抄**一个单位**的格式模板。

    为什么不再用一张"通用模板包"：同一个单位包里 23 个 Mesh 就有 **6 种顶点通道布局**
    （`03_Chassis` 是 0,1,2,4,12,13；`engine1` 是 0,1,2,4,5,6,7,8,13）⇒ 借错模板 = 顶点属性对不上。
    所以模板要**按目标单位抄它自己的**。

    :return: ``{"bundle", "source", "meshes", "materials", "textures", "shaders",
        "monoscripts", "template_scripts", "textures_inlined"}``
    """
    game_root = Path(game) if game else find_game()
    asset_path, src_path, root_pid = resolve_source(game_root, source_key)
    stem = f"tpl_{str(source_key).replace(chr(47), chr(95))}"
    out_parent = (
        Path(out_dir) if out_dir
        else (Path(bundle).parent if bundle is not None and Path(bundle).parent.is_dir()
              else Path.cwd())
    )
    created = create_empty_bundle(key=stem, out_dir=out_parent)
    target_path = Path(created["bundle"])
    source = _open_pack(src_path, materialize=False)
    ms_path = _monoscripts_bundle(game_root)
    ms = _open_pack(ms_path, materialize=False) if ms_path is not None else None
    target = _open_pack(target_path)
    try:
        # 1) 从 prefab 根遍历闭包
        closure: list[int] = []
        queue = [int(root_pid)]
        while queue:
            pid = queue.pop()
            if pid in closure or pid not in source.by_pid:
                continue
            closure.append(pid)
            cls = int(source.cid[source.by_pid[pid][4]])
            if cls in _LEAF_CLASSES:
                continue
            fields = _fields_of(source, pid)

            def visit(v, q=queue):
                if isinstance(v, dict):
                    if "m_PathID" in v and "m_FileID" in v:
                        if (
                            int(v.get("m_FileID", 0)) == 0
                            and int(v.get("m_PathID", 0)) in source.by_pid
                        ):
                            q.append(int(v["m_PathID"]))
                        return
                    for x in v.values():
                        visit(x, q)
                elif isinstance(v, list):
                    for x in v:
                        visit(x, q)

            if fields is not None:
                visit(fields)
        # 2) 只留格式件 ＋ **闭包里所有 MonoScript**
        #    ⚠ MonoScript 只被 MonoBehaviour 的 m_Script 引用，而 MonoBehaviour 不在
        #    _TEMPLATE_CLASSES 里 ⇒ 不放行会得到 0 颗，`add-model` 造包时就没脚本可指。
        keep = [
            p for p in closure
            if int(source.cid[source.by_pid[p][4]]) in _TEMPLATE_CLASSES
            or int(source.cid[source.by_pid[p][4]]) == builder.CLASS_MONO_SCRIPT
        ]
        counts: dict[int, int] = {}
        for pid in keep:
            cls = int(source.cid[source.by_pid[pid][4]])
            counts[cls] = counts.get(cls, 0) + 1
        # 3) 空包直接采纳源包类型表（它带齐所有类）
        target.meta_blob = source.meta_blob
        target.meta = source.meta
        target.types = source.types
        target.cid = source.cid
        target.pool = source.pool
        target.common = source.common
        target.nodes_for = source.nodes_for
        target.script_map = dict(source.script_map)
        target.script_file = dict(source.script_file)
        inlined_textures = 0
        for pid in keep:
            cls = int(source.cid[source.by_pid[pid][4]])
            si = (int(source.types[source.by_pid[pid][4]].script_type_index)
                  if cls == builder.CLASS_MONO_BEHAVIOUR else None)
            raw = bytes(source.raw_reader.read_range(
                source.node_offset + source.data_offset + source.by_pid[pid][2],
                source.by_pid[pid][3],
            ))
            if cls == 28:
                # ★ 2026-10-20：1.2.1 的贴图 100% 流式（像素在源包 .resS）。
                #   模板是离线借格式用的，必须**把像素内联进对象**，否则新包 A5
                #   会红（纹素不在本包）。用 builder 现成的内联入口，不自己写字段。
                fields = clone.read_object(
                    raw, source.nodes_for(source.by_pid[pid][4]), ref_types=source.pool,
                )
                stream = fields.get("m_StreamData") if isinstance(fields, dict) else None
                if isinstance(stream, dict) and int(stream.get("size", 0)) > 0:
                    payload = streaming.resolve_stream(source.raw_reader, stream).data
                    target.specs.append(builder.ObjectSpec(
                        class_id=28,
                        fields=builder.inline_texture2d_fields(fields, payload),
                        path_id=pid, label="template-texture-inline",
                    ))
                    inlined_textures += 1
                    continue
            target.specs.append(builder.ObjectSpec(
                class_id=cls, fields=None, payload=raw, path_id=pid,
                script_index=si, label="template",
            ))
        # ★ 2026-10-20：1.2.1 的 MonoScript **不在 units 包里**（units 0 颗 115），
        #   而在独立的 `*_monoscripts_*.bundle`（实测 758 颗 / CAB-0ec21893…）。
        #   按 source 的 script_types 逐条把 115 抄进模板（pathID 不变）——
        #   `build-model` 才能把 UnitPrefabRoot 的 m_Script 指回本包。
        script_count = 0
        own_path_ids = {int(spec.path_id) for spec in target.specs}
        if ms is not None:
            for st in source.meta.script_types:
                pid = int(st.local_identifier)
                if pid in own_path_ids:
                    continue
                o = ms.by_pid.get(pid)
                if o is None or int(ms.cid[o[4]]) != builder.CLASS_MONO_SCRIPT:
                    continue
                raw = bytes(ms.raw_reader.read_range(
                    ms.node_offset + ms.data_offset + o[2], o[3]
                ))
                target.specs.append(builder.ObjectSpec(
                    class_id=builder.CLASS_MONO_SCRIPT, fields=None, payload=raw,
                    path_id=pid, script_index=None, label="template-script",
                ))
                own_path_ids.add(pid)
                counts[115] = counts.get(115, 0) + 1
                script_count += 1
        script_map = {
            int(si): int(pid)
            for si, pid in source.script_map.items()
            if int(pid) in own_path_ids
        }
        merged_meta = _meta_with_monoscripts(source, ms)
        out = _write_pack(
            target, container=[],
            deps=list(dict.fromkeys([*target.deps, *source.deps])),
            extra_preload=(), source_meta=merged_meta,
            script_type_map=script_map or None,
            require_container=False,
        )
        return {
            "bundle": str(out),
            "source": str(src_path),
            "asset_path": str(asset_path),
            "objects": len(keep) + script_count,
            "meshes": counts.get(43, 0),
            "materials": counts.get(21, 0),
            "textures": counts.get(28, 0),
            "shaders": counts.get(48, 0),
            "monoscripts": counts.get(115, 0),
            "template_scripts": script_count,
            "textures_inlined": inlined_textures,
        }
    finally:
        target.raw_reader.close()
        source.raw_reader.close()
        if ms is not None:
            ms.raw_reader.close()


def _lod_group_pids(pack: _Pack) -> list[int]:
    """包里全部 LODGroup(205) 的 pathID。

    ⚠ `materialize=False` 打开的包 `pack.specs` 是**空的**（那一刻只读了容器/对象表）
    ⇒ 退回 `pack.by_pid + cid` 读。两条路给的是同一批对象（同一条规则的两种读法），
    所以「同槽位 LOD」这条规则仍然只有下面那一份实现。
    """
    if pack.specs:
        return [int(s.path_id) for s in pack.specs if int(s.class_id) == 205]
    return [int(pid) for pid, obj in pack.by_pid.items() if int(pack.cid[obj[4]]) == 205]


def _lod_sibling_meshes(
    pack: _Pack, owners: list[tuple[int, dict]], chosen: int,
) -> list[int]:
    """同一个 LODGroup 里、**同一 renderers 槽位**的其它档用的是哪个 Mesh。

    规则（T-72B 实测）：LOD0 的 7 个渲染器与 LOD1 的 7 个**同序**；LOD2 只有 1 个（车体）。
    所以按「该档 renderers[同一槽位]」取；不是 SkinnedMeshRenderer（例 lod_1x1x1 那个
    内置 Cube 代理）就跳过 —— ⛔ 不换代理盒。
    """
    owner_pids = {int(pid) for pid, _f in owners}
    if not owner_pids:
        return []
    found: list[int] = []
    for group_pid in _lod_group_pids(pack):
        group = _fields_of(pack, group_pid)
        if not isinstance(group, dict):
            continue
        lods = list(group.get("m_LODs") or [])
        pos: tuple[int, int] | None = None
        for li, lod in enumerate(lods):
            for ri, r in enumerate(lod.get("renderers") or []):
                rp = int(((r or {}).get("renderer") or {}).get("m_PathID", 0))
                if rp in owner_pids:
                    pos = (li, ri)
                    break
            if pos is not None:
                break
        if pos is None:
            continue
        _li0, ri0 = pos
        for _li, lod in enumerate(lods):
            rends = lod.get("renderers") or []
            if ri0 >= len(rends):
                continue
            rp = int(((rends[ri0] or {}).get("renderer") or {}).get("m_PathID", 0))
            obj = pack.by_pid.get(rp)
            if obj is None or int(pack.cid[obj[4]]) != _CLASS_SKINNED_MESH_RENDERER:
                continue
            rf = _fields_of(pack, rp)
            mp = int((rf.get("m_Mesh") or {}).get("m_PathID", 0)) if isinstance(rf, dict) else 0
            if mp and mp != int(chosen) and mp not in found:
                found.append(mp)
    return found


def import_materials_from_unit(
    *,
    game: str | Path | None = None,
    bundle: str | Path,
    source_key: str,
    names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """**修改**：把真件单位用到的材质（连同贴图 / Shader 闭包）抄进本包，
    并用**同名**覆盖本包里那一颗材质的内容（本包 pathID 不变 ⇒ 渲染器引用不断）。

    为什么要这条（2026-10-23 实测）：Unity 重建路线里，导出工程 `.mat` 指向的是
    AssetRipper **反编译出来的 shader 文本**，关键字声明丢了 ⇒ 建包后
    `m_ValidKeywords` 全空（`_NORMALMAP` / `_ALPHATEST_ON` 被判无效）⇒ 履带没有
    法线细节。真件材质 + 真件 Shader 一起搬，关键字就跟着回来（A 路线真机验过：
    `RU_T72B_track` valid=4）。

    ⛔ 只抄闭包（材质 → 贴图 / Shader → Shader 的 m_Dependencies），不碰层级。
    """
    game_root = Path(game) if game else find_game()
    _asset_path, src_path, root_pid = resolve_source(game_root, source_key)
    target = _open_pack(bundle)
    source = _open_pack(src_path, materialize=False)
    wanted = {str(n) for n in names} if names else None

    def _walk_closure(start: int, *, stop_at_leaf: bool = True) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        stack = [int(start)]
        while stack:
            pid = stack.pop()
            if pid in seen or pid not in source.by_pid:
                continue
            seen.add(pid)
            out.append(pid)
            if stop_at_leaf and source.cid[source.by_pid[pid][4]] in _LEAF_CLASSES:
                continue
            fields = _fields_of(source, pid)
            if not isinstance(fields, dict):
                continue
            for tp in _pptr_targets(fields):
                if tp in source.by_pid and tp not in seen:
                    stack.append(tp)
        return out

    try:
        # ⓪ 先把本包里的**流式贴图**内联 —— 写包器会换掉 CAB 名，原来的
        #    `archive:/CAB-…/CAB-….resS` 路径就悬空（实测 4 张贴图 decode 失败）。
        inlined = 0
        for spec in list(target.specs):
            if int(spec.class_id) != builder.CLASS_TEXTURE2D:
                continue
            fields = _fields_of(target, int(spec.path_id))
            stream = fields.get("m_StreamData") if isinstance(fields, dict) else None
            if not isinstance(stream, dict) or int(stream.get("size", 0)) <= 0:
                continue
            pixels = streaming.resolve_stream(target.raw_reader, stream).data
            target.specs = [s for s in target.specs if int(s.path_id) != int(spec.path_id)]
            target.specs.append(
                builder.ObjectSpec(
                    class_id=builder.CLASS_TEXTURE2D,
                    fields=builder.inline_texture2d_fields(fields, pixels),
                    path_id=int(spec.path_id),
                    label="modified:texture-inline",
                )
            )
            inlined += 1
        prefab_pids = _walk_closure(int(root_pid))
        src_mats: dict[str, int] = {}
        for pid in prefab_pids:
            if source.cid[source.by_pid[pid][4]] != _CLASS_MATERIAL:
                continue
            fields = _fields_of(source, int(pid))
            if not isinstance(fields, dict):
                continue
            name = str(fields.get("m_Name") or "")
            if name and (wanted is None or name in wanted):
                src_mats.setdefault(name, int(pid))
        if not src_mats:
            raise AssetError(
                f"源单位 {source_key!r} 的 prefab 闭包里没找到要抄的材质",
                hint="--names 给的名字要和 prefab 上材质同名；不给就是全抄",
            )
        tgt_mats: dict[str, int] = {}
        for pid in target.by_pid:
            o = target.by_pid[pid]
            if target.cid.get(o[4]) != _CLASS_MATERIAL:
                continue
            fields = _fields_of(target, int(pid))
            if isinstance(fields, dict):
                tgt_mats[str(fields.get("m_Name") or "")] = int(pid)
        pairs = [(n, p) for n, p in src_mats.items() if n in tgt_mats]
        if not pairs:
            raise AssetError(
                "源材质与本包材质**没有一个同名** —— 抄进来也没人引用",
                hint=f"源侧 {sorted(src_mats)[:5]} / 本包 {sorted(tgt_mats)[:5]}",
            )
        copy_pids: list[int] = []
        seen_copy: set[int] = set()
        for _n, pid in pairs:
            for q in _walk_closure(pid):
                if q not in seen_copy:
                    seen_copy.add(q)
                    copy_pids.append(q)
        # ★ Shader(48) 的 m_Dependencies 在 `_walk_closure` 里到不了（闭包在 48 处收脚），
        #   不补就会留悬空 PPtr（实测 5 条：真件 shader 依赖另外两颗 shader）。
        dep_stack = [
            p for p in list(copy_pids)
            if source.cid[source.by_pid[p][4]] == _CLASS_SHADER
        ]
        while dep_stack:
            dep_pid = dep_stack.pop()
            dep_fields = _fields_of(source, int(dep_pid))
            deps_list = dep_fields.get("m_Dependencies") if isinstance(dep_fields, dict) else None
            if not isinstance(deps_list, list):
                continue
            for dep in deps_list:
                if not isinstance(dep, dict) or int(dep.get("m_FileID", 0) or 0) != 0:
                    continue
                tp = int(dep.get("m_PathID", 0))
                if tp in seen_copy or tp not in source.by_pid:
                    continue
                seen_copy.add(tp)
                copy_pids.append(tp)
                if source.cid[source.by_pid[tp][4]] == _CLASS_SHADER:
                    dep_stack.append(tp)
        own = set(target.by_pid)
        bad = [pid for pid in copy_pids if pid in own]
        if bad:
            raise AssetError(
                f"源包的 {len(bad)} 个对象 pathID 与本包撞了：{bad[:3]}",
                hint="抄进本包时 pathID 必须唯一；撞了就先处理本包那一颗",
            )
        added = 0
        for pid in copy_pids:
            o = source.by_pid[pid]
            cls = source.cid[o[4]]
            raw = bytes(
                source.raw_reader.read_range(
                    source.node_offset + source.data_offset + o[2], o[3]
                )
            )
            if cls == builder.CLASS_TEXTURE2D:
                fields = _fields_of(source, int(pid))
                stream = fields.get("m_StreamData") if isinstance(fields, dict) else None
                if isinstance(stream, dict) and int(stream.get("size", 0)) > 0:
                    pixels = streaming.resolve_stream(source.raw_reader, stream).data
                    target.specs.append(
                        builder.ObjectSpec(
                            class_id=cls,
                            fields=builder.inline_texture2d_fields(fields, pixels),
                            path_id=pid,
                            label="added:texture-inline",
                        )
                    )
                    added += 1
                    continue
            target.specs.append(
                builder.ObjectSpec(
                    class_id=cls, fields=None, payload=raw, path_id=pid, label="added",
                )
            )
            added += 1
        replaced: list[str] = []
        for name, src_pid in pairs:
            fields = _fields_of(source, int(src_pid))
            if not isinstance(fields, dict):
                continue
            # 同一 pathID 只许一条 spec（原 payload 那条要摘掉，否则写包器报「重复 pathID」）
            target.specs = [s for s in target.specs if int(s.path_id) != int(tgt_mats[name])]
            target.specs.append(
                builder.ObjectSpec(
                    class_id=_CLASS_MATERIAL,
                    fields=fields,
                    path_id=int(tgt_mats[name]),
                    label="modified:material-from-game",
                )
            )
            replaced.append(name)
        # ⛔ 不要把源包的 `m_Dependencies` 并进来：那是**真件 units 包**的依赖（cab-… 名字），
        #   我们的包里没有对应的 externals，并进来只会让游戏去找不存在的包。
        deps = list(dict.fromkeys(target.deps))
        out = _write_pack(target, container=target.container, deps=deps, extra_preload=())
        return {
            "bundle": str(out),
            "textures_inlined": inlined,
            "materials_in_source": len(src_mats),
            "materials_replaced": replaced,
            "objects_copied": added,
            "dependencies": len(deps),
        }
    finally:
        target.raw_reader.close()
        source.raw_reader.close()


def _pptr_targets(value: object) -> list[int]:
    """把一棵字段树里所有**包内** PPtr 目标捞出来（m_FileID == 0）。"""
    out: list[int] = []

    def visit(v):
        if isinstance(v, dict):
            if "m_PathID" in v and "m_FileID" in v:
                if int(v.get("m_FileID", 0) or 0) == 0:
                    out.append(int(v["m_PathID"]))
                return
            for x in v.values():
                visit(x)
        elif isinstance(v, list):
            for x in v:
                visit(x)

    visit(value)
    return out

def modify_scale(
    *, bundle: str | Path, value: float, asset_path: str | None = None
) -> dict[str, Any]:
    """**修改**：改这个包根 Transform 的 `m_LocalScale`（三轴同值）。"""
    pack = _open_pack(bundle)
    try:
        if not pack.container:
            raise AssetError("这个包里没有容器条目（空包没有根）")
        root_pid = pack.container[0][1] if not asset_path else next(
            (pid for p, pid in pack.container if p == asset_path), pack.container[0][1]
        )
        root_fields = _fields_of(pack, int(root_pid)) or {}
        tf_pid = int(root_fields["m_Component"][0]["component"]["m_PathID"])
        tf = _fields_of(pack, tf_pid)
        old = dict(tf.get("m_LocalScale") or {})
        tf["m_LocalScale"] = {"x": float(value), "y": float(value), "z": float(value)}
        pack.specs = [s for s in pack.specs if s.path_id != tf_pid]
        # classID 4 = Transform（builder 里没有这个常量）
        pack.specs.append(
            builder.ObjectSpec(
                class_id=4, fields=tf, path_id=tf_pid, label="modified:scale"
            )
        )
        out = _write_pack(pack)
        return {"bundle": str(out), "before": old, "after": tf["m_LocalScale"]}
    finally:
        pack.raw_reader.close()


def add_texture(*, bundle: str | Path, name: str, png: str | Path) -> dict[str, Any]:
    """**新增**：往包里新增一张贴图对象（新 pathID、新名字；不碰任何既有引用）。"""
    from PIL import Image

    from ba_mod_maker.core.asset.new_asset import flip_rows

    pack = _open_pack(bundle)
    try:
        with Image.open(png) as img:
            rgba = img.convert("RGBA")
            width, height = rgba.size
            raw = rgba.tobytes()
        fields = texture_build.build_texture_fields(
            name, width, height, flip_rows(raw, width, height), texture_format=4
        )
        pid = builder.path_id_for(f"texture|{name}|{width}x{height}")
        pack.specs.append(
            builder.ObjectSpec(class_id=builder.CLASS_TEXTURE2D, fields=fields, path_id=pid,
                               label="added:texture")
        )
        container = list(pack.container)
        if not container:
            # ★ 2026-10-19：**空包**加贴图 —— 包里没有任何容器条目时 `_write_pack` 会拒
            #   「有对象却没有容器条目」。Unity 里每张贴图也各有一条资产路径，
            #   这里按 BAMod 惯例补一条（与 add-model 新 prefab 那条同族）。
            container = [(f"Assets/Resources_moved/BAMod/Textures/{name}.png", int(pid))]
        out = _write_pack(pack, container=container)
        return {"bundle": str(out), "path_id": pid, "name": name, "size": f"{width}x{height}"}
    finally:
        pack.raw_reader.close()


def update_textures_from_dir(*, bundle: str | Path, tex_dir: str | Path) -> dict[str, Any]:
    """**修改**：把工程目录 `textures/*.png` 里与包里**同名** Texture2D 的像素换上去。

    ⛔ 这不是「换」：对象的 pathID / 名字 / 材质里那些指向都不动，改的是
    **一个既有对象自己的字段**（AGENTS §0.10 推论 2 点名的「贴图四字段」= 修改）。

    名字对不上的一律**跳过并报出来**（⛔ 不猜、不按顺序硬配）。
    """
    from PIL import Image

    from ba_mod_maker.core.asset.new_asset import flip_rows

    folder = Path(tex_dir)
    pack = _open_pack(bundle)
    try:
        by_name: dict[str, int] = {}
        for spec in pack.specs:
            if int(spec.class_id) != builder.CLASS_TEXTURE2D:
                continue
            fields = _fields_of(pack, spec.path_id)
            if isinstance(fields, dict):
                name = str(fields.get("m_Name", ""))
                if name:
                    by_name[name] = int(spec.path_id)
        updated: list[str] = []
        skipped: list[str] = []
        sizes: dict[str, str] = {}
        replaced: dict[int, dict[str, Any]] = {}
        if folder.is_dir():
            for png in sorted(folder.glob("*.png")):
                name = png.stem
                pid = by_name.get(name)
                if pid is None:
                    skipped.append(name)
                    continue
                with Image.open(png) as img:
                    rgba = img.convert("RGBA")
                    width, height = rgba.size
                    raw = rgba.tobytes()
                replaced[pid] = texture_build.build_texture_fields(
                    name, width, height, flip_rows(raw, width, height), texture_format=4
                )
                updated.append(name)
                sizes[name] = f"{width}x{height}"
        if not updated:
            return {"bundle": str(bundle), "updated": 0, "skipped": skipped, "sizes": {}}
        pack.specs = [
            builder.ObjectSpec(
                class_id=spec.class_id,
                fields=replaced.get(int(spec.path_id), spec.fields),
                path_id=spec.path_id,
                script_index=spec.script_index,
                label=spec.label,
                payload=(None if int(spec.path_id) in replaced else spec.payload),
            )
            for spec in pack.specs
        ]
        out = _write_pack(pack)
        return {"bundle": str(out), "updated": len(updated), "updated_names": updated,
                "skipped": skipped, "sizes": sizes}
    finally:
        pack.raw_reader.close()


def update_texture(*, bundle: str | Path, name: str, png: str | Path) -> dict[str, Any]:
    """**修改**：单张 —— 把包里那张同名 Texture2D 的像素换成这张 PNG。"""
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        staged = Path(tmp) / f"{name}.png"
        shutil.copyfile(Path(png), staged)
        res = update_textures_from_dir(bundle=bundle, tex_dir=tmp)
    if not res.get("updated"):
        raise AssetError(
            f"包里没有叫 {name} 的贴图",
            hint="对不上就先 add-texture 新增一张；或先 list-objects --json 看包内真名",
        )
    return {"bundle": res["bundle"], "name": name, "size": res["sizes"].get(name, "")}

def delete_object(*, bundle: str | Path, name: str) -> dict[str, Any]:
    """**删除**：按 `m_Name` 删一个对象；指着它的容器条目一并删。"""
    pack = _open_pack(bundle)
    try:
        doomed: list[int] = []
        for spec in pack.specs:
            try:
                fields = _fields_of(pack, spec.path_id)
            except AssetError:
                continue
            if isinstance(fields, dict) and str(fields.get("m_Name", "")) == name:
                doomed.append(int(spec.path_id))
        if not doomed:
            raise AssetError(f"包里没有叫 {name} 的对象")
        pack.specs = [s for s in pack.specs if s.path_id not in doomed]
        before = len(pack.container)
        pack.container = [(p, pid) for p, pid in pack.container if pid not in doomed]
        out = _write_pack(pack, container=pack.container)
        return {"bundle": str(out), "deleted_objects": len(doomed),
                "deleted_container": before - len(pack.container)}
    finally:
        pack.raw_reader.close()

#: 材质主贴图的字段名优先级
#: （真件实测两种：HDRP lit 用 `Layer_<hash>`，glass 用 `_IridescenceThicknessMap`）
_PRIMARY_TEXTURE_KEYS = ("_BaseColorMap", "_BaseMap", "_MainTex")


def _next_free_pid(pack: _Pack) -> int:
    """下一个没被占用的 pathID（负数，真件惯例）。

    ⚠ 把**本次新加的 spec** 也算进来 —— 只看 `pack.by_pid` 时，连着加两个对象会撞成同一个 pid。
    """
    used = {int(pid) for pid in pack.by_pid}
    used.update(int(s.path_id) for s in pack.specs)
    pid = -1
    while pid in used:
        pid -= 1
    return pid


def _material_texture_entries(pack: _Pack, material_pid: int) -> list[tuple[str, int]]:
    """材质 -> `[(TexEnv 名, Texture2D pathID)]`（只给**非空**的，保持 `m_TexEnvs` 原序）。"""
    fields = _fields_of(pack, int(material_pid)) or {}
    saved = fields.get("m_SavedProperties") or {}
    out: list[tuple[str, int]] = []
    for entry in saved.get("m_TexEnvs") or []:
        if not isinstance(entry, dict):
            continue
        tex = (entry.get("second") or {}).get("m_Texture") or {}
        pid = int(tex.get("m_PathID", 0) or 0)
        if pid:
            out.append((str(entry.get("first") or ""), pid))
    return out


def _primary_texture_of(pack: _Pack, material_pid: int) -> tuple[str, int] | None:
    """材质的主贴图：优先 `_BaseColorMap`/`_BaseMap`/`_MainTex`，其次 `Layer_*`，否则第一张非空。"""
    entries = _material_texture_entries(pack, material_pid)
    if not entries:
        return None
    for want in _PRIMARY_TEXTURE_KEYS:
        for name, pid in entries:
            if name == want:
                return name, pid
    for name, pid in entries:
        if name.lower().startswith("layer_"):
            return name, pid
    return entries[0]


def _smr_owners_of_mesh(pack: _Pack, mesh_name: str) -> list[tuple[int, dict]]:
    """指着包里**唯一**那颗叫 `mesh_name` 的 Mesh 的 SMR（0 颗 / 多颗同名 Mesh 都点名拒）。"""
    hits = [
        int(pid) for pid, obj in pack.by_pid.items()
        if int(pack.cid[obj[4]]) == _CLASS_MESH
        and str((_fields_of(pack, int(pid)) or {}).get("m_Name")) == mesh_name
    ]
    if len(hits) != 1:
        raise AssetError(
            f"包里叫 {mesh_name!r} 的 Mesh 有 {len(hits)} 颗（要恰好 1 颗）",
            hint="用 --mesh 点名；`pack list --what objects --bundle <包> --json` 看包内真名",
        )
    out: list[tuple[int, dict]] = []
    for pid, obj in pack.by_pid.items():
        if int(pack.cid[obj[4]]) != _CLASS_SKINNED_MESH_RENDERER:
            continue
        sf = _fields_of(pack, int(pid))
        if int((sf.get("m_Mesh") or {}).get("m_PathID", 0)) == hits[0]:
            out.append((int(pid), sf))
    if not out:
        raise AssetError(f"没有 SMR 指向 {mesh_name!r}（材质槽挂在 SMR 上）")
    return out


def _retarget_material_texture(
    fields: dict, entry_name: str, new_texture_pid: int
) -> None:
    """把材质里**那一条** TexEnv 的贴图 PPtr 改成 `new_texture_pid`（只动这一格）。"""
    saved = fields.get("m_SavedProperties") or {}
    for entry in saved.get("m_TexEnvs") or []:
        if isinstance(entry, dict) and str(entry.get("first") or "") == entry_name:
            target = entry.setdefault("second", {}).setdefault("m_Texture", {})
            target["m_PathID"] = int(new_texture_pid)
            return
    raise AssetError(
        f"材质里没有贴图字段 {entry_name!r}（不该发生：上一步刚读过它）",
        hint="先 `pack list --what objects --bundle <包>` 看材质，再把这份读数报上来",
    )


def set_slot_textures(
    *, bundle: str | Path, mesh_name: str, tex_dir: str | Path
) -> dict[str, Any]:
    """**修改**：按材质槽换贴图 —— 目录里放 `slot<N>_<任意名>.png`（N 从 0 起）。

    为什么需要它（2026-10-22，P3）：编辑器桥 `--add-slots` 补出来的槽是**克隆材质**，
    克隆件与原材质指向**同一张 Texture2D** ⇒ 两个槽显示同一张图（实测：
    `bridged2_…bundle` 的 Chassis 两槽都指 `RU_T72B_1_BaseMap` pid 1084419494171551337）。
    这里逐槽：① 找该槽材质的**主贴图**（`_BaseColorMap`/`_BaseMap`/`_MainTex`，其次
    `Layer_*`，否则第一张非空）；② 这张贴图**还被别的槽用着**就先克隆一份（新 pathID、
    名字加 `_slot<N>`）再把本槽改指向克隆件；③ 把像素换成给的 PNG
    （复用 `update_textures_from_dir` 那套 `build_texture_fields`，⛔ 不另写一份编码）。

    ⛔ 不是「换」：槽位顺序、材质对象、字段名都不动；改的是「贴图对象自己的像素」
    与「这一个槽指向哪张贴图」（AGENTS §0.10 的**修改**）。
    """
    import re as _re

    from PIL import Image

    from ba_mod_maker.core.asset.new_asset import flip_rows

    folder = Path(tex_dir)
    files: dict[int, Path] = {}
    for png in sorted(folder.glob("*.png")):
        got = _re.match(r"slot(\d+)_(.+)$", png.stem)
        if not got:
            continue
        idx = int(got.group(1))
        if idx in files:
            raise AssetError(
                f"slot{idx} 有两张图：{files[idx].name} / {png.name}",
                hint="一个槽只配一张图；命名按 `slot<N>_<原名>.png`",
            )
        files[idx] = png
    if not files:
        raise AssetError(
            f"{folder} 里没有 `slot<N>_<原名>.png`",
            hint="例：slot0_RU_T72B_1_BaseMap.png / slot1_RU_T72B_1_BaseMap.png",
        )
    pack = _open_pack(bundle)
    try:
        owners = _smr_owners_of_mesh(pack, mesh_name)
        materials = list(owners[0][1].get("m_Materials") or [])
        usage: dict[int, list[int]] = {}
        primary: dict[int, tuple[str, int]] = {}
        for slot, ptr in enumerate(materials):
            mat_pid = int((ptr or {}).get("m_PathID", 0))
            got = _primary_texture_of(pack, mat_pid)
            if got is None:
                continue
            primary[slot] = got
            usage.setdefault(got[1], []).append(slot)
        replaced: dict[int, dict] = {}
        rows: list[dict[str, Any]] = []
        for slot in sorted(files):
            if slot >= len(materials):
                raise AssetError(
                    f"slot{slot} 越界：{mesh_name} 只有 {len(materials)} 个材质槽",
                    hint="槽号从 0 起；槽不够先用编辑器桥 `--add-slots` 补",
                )
            if slot not in primary:
                raise AssetError(
                    f"槽 {slot} 的材质一张贴图都没有（没有可「改像素」的对象）",
                    hint="这一档只改**既有**贴图；要新增贴图用内核 "
                         "unit_pack.add_texture（CLI 面已收缩，没有对应子命令）",
                )
            entry_name, tex_pid = primary[slot]
            mat_pid = int((materials[slot] or {}).get("m_PathID", 0))
            png = files[slot]
            with Image.open(png) as img:
                rgba = img.convert("RGBA")
                width, height = rgba.size
                raw = rgba.tobytes()
            others = [s for s in usage.get(tex_pid, []) if s != slot]
            src_fields = _fields_of(pack, tex_pid) or {}
            src_name = str(src_fields.get("m_Name") or f"texture_{tex_pid}")
            if others:
                new_pid = _next_free_pid(pack)
                new_name = f"{src_name}_slot{slot}"
                pack.specs.append(
                    builder.ObjectSpec(
                        class_id=builder.CLASS_TEXTURE2D,
                        fields=texture_build.build_texture_fields(
                            new_name, width, height, flip_rows(raw, width, height),
                            texture_format=4,
                        ),
                        path_id=new_pid,
                        label=f"added:texture-slot{slot}",
                    )
                )
                mat_fields = _fields_of(pack, mat_pid)
                _retarget_material_texture(mat_fields, entry_name, new_pid)
                replaced[mat_pid] = mat_fields
                usage[tex_pid] = others
                after_pid, after_name, cloned = new_pid, new_name, True
            else:
                replaced[tex_pid] = texture_build.build_texture_fields(
                    src_name, width, height, flip_rows(raw, width, height),
                    texture_format=4,
                )
                after_pid, after_name, cloned = tex_pid, src_name, False
            rows.append({
                "slot": slot,
                "material": str((_fields_of(pack, mat_pid) or {}).get("m_Name")),
                "entry": entry_name,
                "texture_pid_before": tex_pid,
                "texture_pid_after": after_pid,
                "texture_after": after_name,
                "cloned": cloned,
                "png": png.name,
                "size": f"{width}x{height}",
                "rgba_sha256": hashlib.sha256(raw).hexdigest(),
            })
        pack.specs = [
            builder.ObjectSpec(
                class_id=spec.class_id,
                fields=replaced.get(int(spec.path_id), spec.fields),
                path_id=spec.path_id,
                script_index=spec.script_index,
                label=spec.label,
                payload=(None if int(spec.path_id) in replaced else spec.payload),
            )
            for spec in pack.specs
        ]
        out = _write_pack(pack)
        return {"bundle": str(out), "mesh": mesh_name, "slots": rows,
                "owners": [pid for pid, _f in owners]}
    finally:
        pack.raw_reader.close()


def _skins_count(pack: _Pack) -> int:
    """包里 `UnitPrefabRoot.Skins.Data` 的条数（没有那个组件 / 空 = 0）。

    ⚠ 运行期这条链**按原来的材质槽数**给渲染器赋材质（见 kb/43 坑 1）：多子网格时后半截
    没材质可赋 ⇒ **整块不画**（真机症状：负重轮消失）。所以多子网格必须先 `--strip-skins`。
    """
    for spec in pack.specs:
        if int(spec.class_id) != builder.CLASS_MONO_BEHAVIOUR:
            continue
        fields = _fields_of(pack, int(spec.path_id))
        if isinstance(fields, dict) and "Skins" in fields and "PrefabRoot" in fields:
            skins = fields.get("Skins") or {}
            return len(skins.get("Data") or []) if isinstance(skins, dict) else 0
    return 0

def asset_readings(bundle: str | Path) -> dict[str, Any]:
    """一个包的**内容读数**（GUI 资产页 / CLI 共用；⛔ 不下判断）。

    返回：`mesh`（最大那颗 SMR 的 Mesh 名）· `slots`（SMR 材质槽数）· `submeshes`（Mesh 子网格数）·
    `skins`（`UnitPrefabRoot.Skins.Data` 条数）·
    `lod`（同槽位各档 `[(名, 顶点数)]`，主 Mesh 排第一）·
    `drift`（`schema_drift` 的漂移项数；-1 = 读不出）。

    ⚠ 真开包（几十 MB，约 1~3 s）。
    """
    path = Path(bundle)
    try:
        drift = len(schema_drift(path)["drift"])
    except Exception:  # noqa: BLE001 -- 读数，读不出就 -1（⛔ 不许把页面弄崩）
        drift = -1
    pack = _open_pack(path)
    try:
        best: tuple[int, str, dict, dict, int] | None = None
        for pid, obj in pack.by_pid.items():
            if int(pack.cid[obj[4]]) != _CLASS_SKINNED_MESH_RENDERER:
                continue
            sf = _fields_of(pack, int(pid))
            mp = int((sf.get("m_Mesh") or {}).get("m_PathID", 0))
            mf = _fields_of(pack, mp) if mp else None
            if not isinstance(mf, dict):
                continue
            verts = int((mf.get("m_VertexData") or {}).get("m_VertexCount", 0) or 0)
            if best is None or verts > best[0]:
                best = (verts, str(mf.get("m_Name") or ""), sf, mf, int(mp))
        skins = _skins_count(pack)
        if best is None:
            return {"name": path.name, "mesh": "", "slots": 0, "submeshes": 0,
                    "skins": skins, "lod": [], "drift": drift}
        verts, mesh_name, sf, mf, mesh_pid = best
        lod: list[tuple[str, int]] = [(mesh_name, verts)]
        try:
            owners = _smr_owners_of_mesh(pack, mesh_name)
        except AssetError:
            owners = []
        if owners:
            for sib in _lod_sibling_meshes(pack, owners, mesh_pid):
                smf = _fields_of(pack, int(sib)) or {}
                lod.append((
                    str(smf.get("m_Name") or ""),
                    int((smf.get("m_VertexData") or {}).get("m_VertexCount", 0) or 0),
                ))
        return {
            "name": path.name,
            "mesh": mesh_name,
            "slots": len(sf.get("m_Materials") or []),
            "submeshes": len(mf.get("m_SubMeshes") or []),
            "skins": skins,
            "lod": lod,
            "drift": drift,
        }
    finally:
        pack.raw_reader.close()

def asset_check(bundle: str | Path) -> dict[str, Any]:
    """体检（GUI 资产页 / CLI 共用）：类型表漂移 ＋ 悬空 PPtr ＋ 出厂闸 A0~A6。

    ⛔ 只给读数、不下判断；`ok` 只表示「漂移 0 且悬空 0」，装机可否仍按 CLI 的判据。
    """
    from ba_mod_maker.asset_kernel import unit_bundle as _ub

    drift = schema_drift(bundle)["drift"]
    audit = _ub.audit_package_pptrs(bundle)
    gate = _ub.verify_unit_bundle(bundle, require_animations=True)
    return {
        "drift": list(drift),
        "dangling": list(audit.dangling),
        "gate_lines": list(gate.lines()),
        "ok": not drift and not audit.dangling,
    }


def prefab_asset_path(bundle: str | Path) -> str:
    """包里那条 prefab 资产路径（装机时 `--asset-path` 的默认值）。"""
    pack = _open_pack(bundle)
    try:
        for path, _pid in pack.container:
            if str(path).endswith(".prefab"):
                return str(path)
        return str(pack.container[0][0]) if pack.container else ""
    finally:
        pack.raw_reader.close()


def patch_cabinet_dir() -> Path:
    """补丁柜的**默认**落点（GUI / CLI 同一口径；`BA_MOD_MAKER_PATCH_DIR` 可覆盖）。

    ⚠ 撤装/重指向必须与装机用**同一个**目录，否则找不到补丁柜条目。
    """
    import os

    override = os.environ.get("BA_MOD_MAKER_PATCH_DIR")
    return Path(override) if override else Path.home() / ".ba_mod_maker" / "patch"

def _run_cli(project_root: Path, args: Sequence[str]) -> str:
    cmd = [sys.executable, "-m", "ba_mod_maker.cli", *args]
    done = subprocess.run(
        cmd, cwd=str(project_root), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = (done.stdout or "") + (done.stderr or "")
    if done.returncode != 0:
        raise AssetError(f"CLI 失败（exit {done.returncode}）：{out.strip()[-800:]}")
    return out


def _catalog_bundle_internal_id(catalog_path: Path, bundle_name: str) -> str:
    """从**盘上那份 catalog** 读回这个包的 internalId（⛔ 不按代码推一遍 —— 读盘才是证据）。

    2026-10-25 块 B 起落点只有一处（`new_asset.install_landing`：`<游戏根>\\BA_Mod\\`），
    这一条只在 `pack install` 收尾时读回来对账用。
    """
    model = cbw.read_model(catalog_path.read_bytes())
    for entry in model.entries:
        internal_id = entry.internal_id or ""
        if internal_id.endswith(bundle_name):
            return internal_id
    raise AssetError(
        f"catalog 里没有这个包的条目：{bundle_name}",
        hint="`newasset add-model` 应当已经登记过；没有它这一单等于没装，先别继续",
    )


def _check_cabinet_matches_disk(*, project: Path, root: Path) -> dict[str, str]:
    """读回补丁柜：柜里记的「装后」必须与盘上**逐字节**对得上（⛔ 不认内存里的值）。

    只读核对（**不重记**）。2026-10-25 块 B 删掉了 `install_pack` 里的搬包 / repoint /
    recapture 三段之后，catalog 只被写**一次** ⇒ 旧那笔「repoint 之后要重记柜子」的账不存在了；
    这一条留着守的是**另一件事**：柜里记的必须就是盘上的 —— 不然下一次 `pack restore`
    会报「catalog.bin 的 sha256 与补丁的『装后』不符」。
    """
    from ba_mod_maker.asset_kernel import reverse_delta as rd

    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    pending = store.pending()
    if pending is None:
        raise AssetError(
            f"补丁柜里没有这一单的记录：{store.json_path}",
            hint="`newasset add-model` 应当已经写过柜；没有它这一单就没有回退的路，先别继续",
        )
    # 按**解析后的绝对路径**比对：柜里记的是那次调用当时的 path 写法（相对 / 绝对都可能）。
    after_by_path = {Path(target.path).resolve(): target.sha_after for target in pending.targets}
    out: dict[str, str] = {}
    for path in (root / CATALOG, root / CATALOG_HASH):
        want = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        got = after_by_path.get(Path(path).resolve(), "")
        if got != want:
            raise AssetError(
                f"补丁柜里的「装后」与盘上对不上：{path.name} "
                f"（柜 {got[:16] or '（没有这一条）'}… / 盘 {want[:16] or '（盘上没有）'}…）",
                hint="柜里的账不可信 ⇒ 先别装、也别信 restore；把这一单的补丁柜条目查清楚再继续。",
            )
        out[path.name] = want
    return out


def install_pack(*, game: str | Path | None = None, project_root: str | Path,
                 bundle: str | Path, unit: str, key: str, asset_path: str,
                 dependency_source: str | None = None,
                 dependency_mode: str = "own-only",
                 row_id: int | None = None) -> dict[str, Any]:
    """装 = **只转调** `newasset add-model`（落盘 / catalog / 补丁柜全在它里面做完）。

    2026-10-25 块 B：**搬包 / repoint / recapture 三段已删**。它们存在的唯一理由是
    「`newasset` 把包落在 `aa\\PC`，而装机要落到 `BA_Mod`」—— 现在落点规则只有一处
    （`new_asset.install_landing`：`<游戏根>\\BA_Mod\\`），包与 catalog 那一条**一步**就写对
    ⇒ 不需要第二次写盘，也就没有「柜里记的装后当场过期」那类账。

    `dependency_source` 默认 = `unit`（新单位那一档）；**修一个已经指过去的行**时
    那一行现在的键可能已经不在 catalog 里（例：`T72BRoute2`），要显式给一条
    已入库的真件键（例 `RU_T72B`）抄依赖。`row_id` 给 `Units.Id`，不唯一时必填。
    """
    root = Path(game) if game else find_game()
    # ⚠ 必须是**绝对**路径：`_run_cli` 用 `cwd=project` 跑子进程，再把 `--project-root`
    #   （同一个相对串）传给 CLI ⇒ CLI 按新 cwd 再拼一次，柜子落到 `_work/x/_work/x/patches`。
    project = Path(project_root).resolve()
    project.mkdir(parents=True, exist_ok=True)
    # ⚠ 2026-10-26：`--bundle` **也必须绝对路径** —— `_run_cli` 的 cwd 就是上面那个 project，
    #   相对串会被子进程按新 cwd 再解析一次（症状原话：「找不到要新增的包：_work\unitybuild\…」）。
    bundle = Path(bundle).resolve()
    args = [
        "newasset", "add-model", "--bundle", str(bundle), "--unit", unit, "--key", key,
        "--asset-path", asset_path,
        "--dependency-source", dependency_source or unit,
        "--dependency-mode", str(dependency_mode),
        "--game", str(root), "--project-root", str(project),
    ]
    if row_id is not None:
        args.extend(["--id", str(int(row_id))])
    out = _run_cli(project, args)
    name = bundle.name
    return {
        "bundle": str(root / MOD_DIR / name),
        "internal_id": _catalog_bundle_internal_id(root / CATALOG, name),
        "cabinet": _check_cabinet_matches_disk(project=project, root=root),
        "cli": out.strip()[-400:],
    }


def restore_pack(*, game: str | Path | None = None, project_root: str | Path) -> dict[str, Any]:
    """撤 = **只转调** `newasset restore`（补丁柜逐位还原；我们加的包是「新文件」⇒ 还原 = 删掉）。

    2026-10-25 块 B：**先搬回 `aa\\PC` 再删**那一段已删 —— 它存在的理由同样是落点不一致；
    现在包就落在 `BA_Mod\\`、补丁柜记的也是那条路径 ⇒ 一条命令删干净（不再留孤儿包）。
    """
    root = Path(game) if game else find_game()
    project = Path(project_root).resolve()
    # 与 install_pack 同口径：`_run_cli` 的 cwd 就是它，不存在时子进程直接起不来。
    project.mkdir(parents=True, exist_ok=True)
    out = _run_cli(
        project, ["newasset", "restore", "--game", str(root), "--project-root", str(project)]
    )
    return {"moved_back": None, "cli": out.strip()[-400:]}


def list_units(game: str | Path | None = None) -> list[dict[str, Any]]:
    import zipfile

    root = Path(game) if game else find_game()
    with zipfile.ZipFile(root / "BA_Mod.zip") as zf:
        table = json.loads(zf.read("Units.json").decode("utf-8"))
    rows = table["Rows"] if isinstance(table, dict) and "Rows" in table else table
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        out.append({
            "row": i, "id": row.get("Id"),
            "hud": row.get("HUDName") or row.get("Name"),
            "model": row.get("ModelFileName"),
        })
    return out