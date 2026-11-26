"""UnityPy 适配层：把 bundle 变成 asset_kernel 认得的普通数据（读侧）。

## 为什么需要它，以及它为什么住在这里

te工单 §9 点名的四个读侧入口（read_prefab_tree / read_mesh / read_texture_2d /
resolve_stream）必须**只有一份实现** —— 否则插件里会长出第二份，两边慢慢分叉
（这个仓吃过这个亏，见 AGENTS.md §5「逻辑只有一份」）。

但 typetree 的解析（含内嵌类型树）今天由 UnityPy 提供（设计稿 §2 的 _vendor 就是它，
§3.8 实测 Blender 自带 Python 也能跑）。所以：

- **纯逻辑**（顶点流布局 / .resS 定位 / 树 / 世界坐标 / 骨骼哈希）住在别的模块，零第三方；
- **入口这一层**（怎么问 UnityPy 要 typetree）住在这里，UnityPy **延迟 import**；
- 于是 asset_kernel 的其余部分在没装 UnityPy 的环境里照样能 import 与跑用例。

⚠ 一个刻意的选择：**.resS 的字节由我们自己的 unityfs 容器读侧读**（不是 UnityPy 的
get_resource_data）。理由：那条路是 W6「跨节点换贴图」要改的地方，必须是我们自己的
代码 + 我们自己的读数（hash_mismatch 记账）。本模块只借用 UnityPy 的 typetree 解析。

## 实测（本机 units 包，2026-10-03）

- UnityPy.load(3.38 GB) 5.0 s；容器条目 1393 条；节点 2 个（CAB + .resS）；
- 我们自己的 UfsReader 打开同一个包：41440 块 / 解压后 5,431,544,908 B；
- 同一个 asset 地址键在 catalog 里有 **4 个候选包**，只有 units 的
  AssetBundle.m_Container 命中（工单 §7 坑 1）。

**object_at 索引化（2026-10-09）**：162,010 个对象一次遍历建 `pathID -> object` 索引（≈0.04 s）；
老实现是线性扫描，`pick_mids` 段实测 **81.2 s**（task-6）→ 索引版 **0.11 s**（task-8 复算，
含 8,955 个渲染器的 typetree 读取）。同一遍历顺序 + `setdefault` ⇒ 与"取第一个命中的对象"
逐位相同（全 162,010 个 pathID 逐个对拍 + 不存在 pathID 的正控：
`_work/vtb1/bench/probe_index_equiv.py`）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import clone as clone_mod
from ba_mod_maker.asset_kernel import mesh as mesh_mod
from ba_mod_maker.asset_kernel import prefab as prefab_mod
from ba_mod_maker.asset_kernel import texture as texture_mod
from ba_mod_maker.asset_kernel import unityfs
from ba_mod_maker.asset_kernel.container_addr import resolve_container_path
from ba_mod_maker.asset_kernel.streaming import resolve_stream

__all__ = [
    "AssetSource",
    "open_source",
    "open_unique_bundle",
    "open_unique_source",
    "require_unitypy",
]


#: 包 -> 容器地址表（★ 2026-10-18）。实测 units 3.35 GB 读一次要 **10.4~11.5 s**，
#: 而「整套（车体＋炮塔）」每次拿件都会把候选包挨个扫一遍（实测 9 次容器扫描 11.1 s）。
#: 键里带 size + mtime ⇒ 包被重打过（我们自己的产物）会自动失效。
_CONTAINERS_CACHE: dict[tuple[str, int, int], frozenset[str]] = {}


def _cache_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path), int(stat.st_size), int(stat.st_mtime_ns))


def containers_map_via_ufs(path: str | Path) -> dict[str, int]:
    """地址键 -> 资产 pathID（`m_Container` 的 pair.second.asset）。

    快走查（`_prefab_nodes_fast`）要的入口：老路靠 `AssetBundle.m_Container` 的 UnityPy
    PPtr 去 `.read()`，而那条会把 2.5 MB 的类型树整个解析一遍。
    """
    return _containers_via_ufs(path, with_ids=True)


def containers_via_ufs(path: str | Path) -> tuple[str, ...]:
    """只用**我们自己的**容器读侧拿 `AssetBundle.m_Container` 的地址键。

    ★ 2026-10-18：`asset_bundle_containers()` 原来走 UnityPy —— 它要先 `UnityPy.load()`
    整个包再解 2.5 MB 的 AssetBundle 类型树。实测 units 3.35 GB：**10.4~11.5 s/次**，
    而「拿一件」要挨个扫候选包（一次拿件 9 次扫描 = **11.6 s**，占当时 14.9 s 总时间的 78%）。

    这条只做三件事：解析 SerializedFile 元数据（几 MB）→ 找 classID=142 的那颗对象 →
    `clone.read_object(bulk_arrays=True)` 批量读它的 `m_Container`。⛔ 形状认不得就抛，
    由调用方回退 UnityPy（宁可慢，不许读不出来）。
    """
    return _containers_via_ufs(path, with_ids=False)


def _containers_via_ufs(path: str | Path, *, with_ids: bool, view: Any = None) -> Any:
    """`AssetBundle.m_Container`：给键列表，或者给 键 -> pathID。"""
    from ba_mod_maker.asset_kernel import monoscript_build as mb  # noqa: PLC0415 -- 拆环

    class_asset_bundle = 142
    if view is None:
        with mb.open_view(path) as opened:
            return _containers_via_ufs(path, with_ids=with_ids, view=opened)
    else:
        view = view
        type_ids = set(view.type_ids_of(class_asset_bundle))
        if not type_ids:
            return {} if with_ids else ()
        pid = next(
            (int(item.path_id) for item in view.meta.objects if int(item.type_id) in type_ids),
            None,
        )
        if pid is None:
            return {} if with_ids else ()
        node = pid
        tree = clone_mod.read_object(
            view.raw(node),
            view.nodes_for(int(view.entry(node).type_id)),
            little_endian=view.meta.header.endian != ">",
            bulk_arrays=True,
        )
    out: list[str] = []
    mapped: dict[str, int] = {}
    for entry in tree.get("m_Container") or ():
        # ⚠ 实测（units 1.2.1）：`m_Container` 的元素是 **pair<string, …>**，
        #   我们读出来是 `{"first": <地址键>, "second": …}`（UnityPy 那边是元组）。
        #   ⛔ 只认 `m_Asset` 会一条都取不到（那就等于把 3525 条全丢了）。
        key = None
        if isinstance(entry, Mapping):
            key = entry.get("first", entry.get("m_Asset"))
        elif isinstance(entry, (tuple, list)) and entry:
            key = entry[0]
        if isinstance(key, str):
            out.append(key)
            if with_ids:
                info = entry.get("second") if isinstance(entry, Mapping) else None
                asset = info.get("asset") if isinstance(info, Mapping) else None
                # ⚠ 没有 asset 的键也要**留着**（值 0）：这只是「这个地址不指向对象」，
                #   不是「这条地址不存在」—— 丢键会让容器表从 3525 变 3494。
                mapped[key] = (
                    int(asset.get("m_PathID", 0) or 0) if isinstance(asset, Mapping) else 0
                )
    return mapped if with_ids else tuple(out)


def _nodes_from_unitypy(root: object) -> tuple[clone_mod.TypeNode, ...]:
    """UnityPy 的 `TypeTreeNode`（树形）-> 我们的**扁平** `TypeNode`（文档顺序）。

    两条路读的是**同一份**类型树：UnityPy 把它当对象树（`m_Children`），
    `clone.read_object` 要扁平数组（`level` 表层级）。字段名/顺序一个不改 ——
    扁平化只按 `m_Level` 深度优先走一遍。
    """
    out: list[clone_mod.TypeNode] = []

    def walk(node: object) -> None:
        out.append(
            clone_mod.TypeNode(
                level=int(getattr(node, "m_Level", 0) or 0),
                type=str(getattr(node, "m_Type", "") or ""),
                name=str(getattr(node, "m_Name", "") or ""),
                byte_size=int(getattr(node, "m_ByteSize", -1) or -1),
                meta_flag=int(getattr(node, "m_MetaFlag", 0) or 0),
                type_flags=int(getattr(node, "m_TypeFlags", 0) or 0),
                version=int(getattr(node, "m_Version", 1) or 1),
            )
        )
        for child in getattr(node, "m_Children", ()) or ():
            walk(child)

    walk(root)
    if not out:
        raise ValueError("类型树是空的")
    return tuple(out)


def require_unitypy() -> Any:
    """延迟 import UnityPy（Blender 侧要先跑 runtime 的 _vendor 选择）。"""
    import UnityPy  # noqa: PLC0415  -- 延迟 import 是本模块的设计（见文件头）

    return UnityPy


# --------------------------------------------------------------------------- #
# cab 索引：跨包 PPtr 的「按 cab 名找包」
# --------------------------------------------------------------------------- #
#: 覆盖 cab 索引文件位置的环境变量。
CAB_INDEX_ENV = "BAMOD_CAB_INDEX"

#: 用户缓存目录名（与 `vt_cache` / `units_names` 同一个约定）。
_CAB_CACHE_DIRNAME = "BAModMaker"


def cab_index_path(aa_dir: str | Path) -> Path:
    """cab 索引落在哪：默认 `%LOCALAPPDATA%/BAModMaker/aa_cab_index.json`。"""
    override = os.environ.get(CAB_INDEX_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home())
    return Path(base) / _CAB_CACHE_DIRNAME / "aa_cab_index.json"


def aa_stamp(aa_dir: str | Path) -> str:
    """aa 目录指纹（包名 + 字节数）—— 换游戏版本 / 换包时索引自动失效。"""
    base = Path(aa_dir)
    parts: list[str] = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return ""
    for name in names:
        if not name.endswith(".bundle"):
            continue
        try:
            parts.append(f"{name}:{os.path.getsize(base / name)}")
        except OSError:
            continue
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def build_cab_index(aa_dir: str | Path) -> dict[str, str]:
    """cab 名（小写）-> 包文件名。

    ⚠ 只用**我们自己的** `unityfs` 读容器目录：实测 **0.02 s/包**（units 3.35 GB 也是 0.02 s，
    目录块而已），76 个包 ≈ 1.5 s；⛔ 不走 `UnityPy.load`（units 包那一步要 ≈10 GB 内存）。
    """
    base = Path(aa_dir)
    out: dict[str, str] = {}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".bundle"):
            continue
        try:
            with unityfs.open_bundle(base / name) as reader:
                for node in reader.nodes:
                    head = str(node.path).rsplit("/", 1)[-1]
                    if head.startswith("CAB-"):
                        out[head.lower()] = name
        except Exception:  # noqa: BLE001 -- 单个坏包不该废掉整份索引
            continue
    return out


def load_cab_index(aa_dir: str | Path, *, build: bool = True) -> dict[str, str]:
    """读 cab 索引；缺失 / 指纹过期时按需重建并落盘（读侧只认缓存，重建要显式给 build）。"""
    path = cab_index_path(aa_dir)
    stamp = aa_stamp(aa_dir)
    data: Any = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and data.get("stamp") == stamp:
        cabs = data.get("cabs")
        if isinstance(cabs, dict):
            return {str(k): str(v) for k, v in cabs.items()}
    if not build:
        return {}
    cabs = build_cab_index(aa_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"stamp": stamp, "cabs": cabs}, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except OSError:
        pass
    return cabs

def _path_id_of(obj: object) -> int:
    pid = getattr(obj, "path_id", None)
    if pid is None:
        pid = getattr(getattr(obj, "object_reader", None), "path_id", None)
    return int(pid) if pid is not None else 0


def _pptr_path_id(ptr: object | None) -> int | None:
    if ptr is None:
        return None
    pid = getattr(ptr, "m_PathID", None)
    if pid is None:
        return None
    return int(pid) or None


def _pptr_file_id(ptr: object | None) -> int:
    if ptr is None:
        return 0
    return int(getattr(ptr, "m_FileID", 0) or 0)


def _dict_pptr(value: object) -> int | None:
    """clone 快读器给的 PPtr（`{"m_FileID", "m_PathID"}`）-> pathID（0 当空）。"""
    if not isinstance(value, Mapping):
        return None
    pid = value.get("m_PathID")
    if pid is None:
        return None
    return int(int(pid) or 0) or None


def _dict_pptr_file(value: object) -> int:
    if not isinstance(value, Mapping):
        return 0
    return int(value.get("m_FileID", 0) or 0)


def _read(ptr: object | None) -> object | None:
    """解一个 PPtr；空指针或跨文件缺失时返回 None（不抛）。"""
    if ptr is None:
        return None
    try:
        return ptr.read()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001  -- UnityPy 对空/跨文件 PPtr 会抛各种异常
        return None


#: 进程内 **开包次数**（★ 2026-10-18：只给探针 / 用例用，⛔ 不是缓存）。
#: 判据一行：同一单位连续跑三个阶段（importer -> materials -> turret_entry）
#: ⇒ 计数必须 == **1**（整改前 == 3）。计数器只在这里，别在插件侧再记一份。
OPEN_COUNT = 0


def reset_open_count() -> int:
    """把开包计数器清零，返回清零前的值（探针在每段之前调一次）。"""
    global OPEN_COUNT
    previous = int(OPEN_COUNT)
    OPEN_COUNT = 0
    return previous


class AssetSource:
    """一个打开的 bundle：容器读侧（我们的）+ typetree 读侧（UnityPy 的）。"""

    def __init__(self, bundle_path: str | Path, *, deps: bool = False) -> None:
        global OPEN_COUNT
        OPEN_COUNT += 1
        self.path = Path(bundle_path)
        self.reader = unityfs.open_bundle(self.path)
        self._env: Any = None
        self._ids_by_type: dict[str, tuple[int, ...]] | None = None
        self._object_at: dict[int, object] = {}
        self._by_path_id: dict[int, object] = {}
        self._indexed_env: Any = None
        #: ★ 2026-10-17 跨包 PPtr：`m_FileID != 0` 的指针要按 externals 打开依赖包再重试。
        #: 实测（`_work/audit-full`）：250 个 prefab / 824 条跨包材质引用 / 132 颗外部材质，
        #: 全部指向本机已有的包；不开依赖包时 `_read()` 把它们吞成 `None`（豹2A8 履带发白）。
        self.deps = bool(deps)
        #: 读侧的一句备注（缺 cab 索引等），给上层如实报，不静默。
        self.dep_note = ""
        self._cab_index: dict[str, str] | None = None
        self._dep_loaded: set[str] = set()
        self._dep_readers: list[Any] = []
        self._dep_objects: dict[int, tuple[object, Any]] = {}
        #: ★ 2026-10-18 快读器读数：读成功几颗 / 回退几颗（⛔ 回退不静默，上层能报）。
        self.fast_mesh_reads = 0
        self.fast_mesh_fallbacks = 0
        self.fast_shader_fallbacks = 0
        self.fast_texture_fallbacks = 0
        self.fast_tree_fallbacks = 0
        self.fast_container_fallbacks = 0
        self.fast_prefab_reads = 0
        self.fast_prefab_fallbacks = 0
        #: 快走查回退的**原因**（⛔ 回退不许静默；诊断/用例能读到它）。
        self.fast_prefab_error = ""
        #: ★ 2026-10-18：**我们自己的** SerializedFile 视图（惰性建，按来源缓存）。
        #: 有它之后 `read_typetree` / `class_name_of` 都不必再让 UnityPy 把整个包 load 一遍
        #: （实测 `object_at()` 第一次调用 5.7 s = UnityPy.load(3.35 GB)）。
        self._view: Any = None
        self._class_names: dict[int, str] = {}
        self._container_map: dict[str, int] | None = None

    # ---- 生命周期 ----
    def __enter__(self) -> AssetSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        # ★ 2026-10-18（m2-geometry 实测 WinError 5 的根因）：**必须**关掉我们自己的 ufs 视图 ——
        #   它按来源缓存一次（`ufs_view()`），不关就把文件句柄留在进程里 ⇒ Windows 替换 bundle
        #   时 `PermissionError: 还有句柄占着它`，`writer._replace_with_retry` 也放不开。
        #   正控/负控：不关这一行 ⇒ `test_cli_write_then_restore_restores_sha256` 必红；补上 ⇒ 绿。
        view = getattr(self, "_view", None)
        if view is not None:
            with contextlib.suppress(Exception):  # 收尾失败不该盖住主流程
                view.close()
            self._view = None
        self.reader.close()
        for reader in self._dep_readers:
            try:
                reader.close()
            except Exception:  # noqa: BLE001 -- 收尾失败不该盖住主流程
                continue
        self._dep_readers = []
        self._dep_objects = {}
        self._dep_loaded = set()
        self._env = None
        # 索引与 env 同生共死：留着它就把那个 Environment（units 包实测 10 GB）钉在内存里。
        self._by_path_id = {}
        self._indexed_env = None

    @property
    def env(self) -> Any:
        if self._env is None:
            self._env = require_unitypy().load(str(self.path))
        return self._env

    # ---- 对象表 ----
    def _index(self) -> dict[str, tuple[int, ...]]:
        if self._ids_by_type is None:
            buckets: dict[str, list[int]] = {}
            for obj in self.env.objects:
                kind = obj.type.name
                pid = getattr(obj, "path_id", None)
                if pid is None:
                    continue
                buckets.setdefault(kind, []).append(int(pid))
            self._ids_by_type = {k: tuple(v) for k, v in buckets.items()}
        return self._ids_by_type

    def object_ids(self, kind: str) -> tuple[int, ...]:
        return self._index().get(kind, ())

    def kind_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._index().items()}

    def _objects_by_path_id(self) -> dict[int, object]:
        """一次遍历建 pathID -> object 索引（**同一遍历顺序 + setdefault**）。

        ⚡ 老实现每问一个 pathID 就把 env.objects 从头线性扫一遍（units 包实测 162,010 个
        对象）；调用方循环 8,955 个渲染器 ⇒ O(N²)，实测 pick_mids 段 **81.2 s**（task-6）/
        **85.91 s**（task-8 复算），索引版 **0.11 s**（含 8,955 次 typetree 读取）。同一遍历顺序
        + setdefault = 与"取第一个命中的对象"逐位相同
        （全 162,010 个 pathID 逐个对拍 + 正控：_work/vtb1/bench/probe_index_equiv.py）。
        索引与 env 绑定：close() / 重新 load 之后重建（索引留着会把 10 GB 的 Environment 钉住）。
        """
        env = self.env
        if self._indexed_env is not env:
            table: dict[int, object] = {}
            for obj in env.objects:
                pid = getattr(obj, "path_id", None)
                if pid is None:
                    continue
                table.setdefault(int(pid), obj)
            self._by_path_id = table
            self._indexed_env = env
        return self._by_path_id

    def object_at(self, path_id: int) -> object | None:
        if path_id in self._object_at:
            return self._object_at[path_id]
        found = self._objects_by_path_id().get(path_id)
        if found is not None:
            self._object_at[path_id] = found
        return found

    # ---- 跨包 PPtr（★ 2026-10-17） --------------------------------------
    def cab_index(self) -> dict[str, str]:
        """本包所在 aa 目录的 `cab -> 包` 索引（惰性；**只读缓存**，没有就空 dict）。

        缓存口径：索引指纹 = 「包名 + 字节数」；指纹变了就重建（换游戏版本自动失效）。
        实测建索引 **0.1 s / 145 个 cab**（只读容器目录，`unityfs` 不解数据）⇒ 允许在导入路径里
        按需重建一次；写缓存失败（沙箱）也不影响本次读取，只有读不出 aa 目录时才如实记 `dep_note`。
        """
        if self._cab_index is None:
            # build=True：实测 **0.1 s / 145 个 cab**（只读容器目录）⇒ 允许按需重建一次，
            # 用户机器上第一次导入也能自愈；写缓存失败（沙箱）不影响本次读取。
            self._cab_index = load_cab_index(self.path.parent, build=True)
            if not self._cab_index and self.deps:
                self.dep_note = (
                    "建不出 cab 索引（aa 目录里没有 .bundle？）⇒ 跨包材质仍然解不出来；"
                    "复算：.venv\\Scripts\\python.exe tools\\asset_audit.py cabindex"
                )
        return self._cab_index

    def _cab_name(self, assets_file: object | None, file_id: object) -> str:
        """`(assets_file, m_FileID)` -> externals 里那条的 cab 名（小写；拿不到给空串）。

        ★ 2026-10-19：从 `_external_cab` 里拆出来 —— 快读器拿到的是**字典形式的 PPtr**，
        没有 `assetsfile` 属性，只有把 assets_file 单独传进来才能解跨包材质。
        """
        try:
            fid = int(file_id or 0)
        except (TypeError, ValueError):
            return ""
        if fid <= 0:
            return ""
        externals = getattr(assets_file, "externals", None)
        if not externals or fid > len(externals):
            return ""
        path = str(getattr(externals[fid - 1], "path", "") or "")
        return path.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()

    def _external_cab(self, ptr: object) -> str:
        """PPtr 的 `m_FileID` -> externals 里那条的 cab 名（小写；拿不到给空串）。"""
        try:
            file_id = int(getattr(ptr, "m_FileID", 0) or 0)
        except (TypeError, ValueError):
            return ""
        # ⚠ UnityPy 1.25 的字段名是 `assets_file`（带下划线）；老代码写的 `assetsfile`
        # 恒为 None ⇒ 跨包解析**从来没触发过**（2026-10-19 实测：DLC2_M113 履带材质落成占位）。
        return self._cab_name(
            getattr(ptr, "assets_file", None) or getattr(ptr, "assetsfile", None), file_id
        )

    def _load_dependency(self, cab: str) -> bool:
        """把 cab 对应的包加载进**同一个** Environment（`PPtr.deref` 随后就能找到它）。

        ⚠ UnityPy 按路径 load 注册的是**文件名**，不是 cab 名 ⇒ 必须再 `register_cab`：
        实测 `PPtr.deref` 走的是 `env.find_file(<cab 名>)`，注册错了照样抛。
        """
        if cab in self._dep_loaded:
            return False
        name = self.cab_index().get(cab)
        if not name:
            return False
        path = self.path.parent / name
        if not path.is_file():
            return False
        self._dep_loaded.add(cab)
        try:
            env = self.env
            loaded = env.load_file(str(path), is_dependency=True)
            reader = unityfs.open_bundle(path)
            self._dep_readers.append(reader)
            files = (
                list(getattr(loaded, "files", {}).values())
                if hasattr(loaded, "files")
                else [loaded]
            )
            for sub in files:
                cab_name = str(getattr(sub, "name", "") or "")
                if cab_name:
                    env.register_cab(cab_name.lower(), sub)
                # env.objects 不该把依赖包的对象算进来（否则 pathID 可能与主包撞车）
                with contextlib.suppress(AttributeError, TypeError):
                    sub.is_dependency = True
                for pid, obj in (getattr(sub, "objects", None) or {}).items():
                    self._dep_objects.setdefault(int(pid), (obj, reader))
        except Exception:  # noqa: BLE001 -- 依赖读不进来时退回旧行为（None）
            self._dep_loaded.discard(cab)
            return False
        return True

    def read_ptr(self, ptr: object | None) -> object | None:
        """解一个 PPtr；跨包时按 externals 打开依赖包**重试一次**，仍失败给 None。

        ★ 这是「豹2A8 履带发白」的修复点：材质 `US_HeavyTrack` 在 units 包，prefab 在
        dlc_2 包，PPtr 的 `m_FileID=3` -> `externals[2]` = units 的 cab。
        """
        if ptr is None:
            return None
        try:
            return ptr.read()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 -- 空 / 跨文件 PPtr 会抛各种异常
            pass
        if not self.deps:
            return None
        cab = self._external_cab(ptr)
        if not cab or not self._load_dependency(cab):
            return None
        try:
            return ptr.read()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return None

    def _object_or_dep(self, path_id: int) -> tuple[object | None, Any]:
        """pathID -> (对象, **它所在容器的读器**)；依赖包里的对象也能命中。

        ⚠ 读器必须跟着对象走：贴图的 m_StreamData 要在**它自己的** .resS 里解，
        拿主包的读器去解依赖包的贴图会解出空字节（静默错图）。
        """
        obj = self.object_at(path_id)
        if obj is not None:
            return obj, self.reader
        found = self._dep_objects.get(int(path_id))
        if found is None:
            return None, self.reader
        return found

    def ufs_view(self) -> Any:
        """我们自己的 SerializedFile 视图（`monoscript_build.open_view`，按来源缓存一次）。"""
        if self._view is None:
            from ba_mod_maker.asset_kernel import monoscript_build as mb  # noqa: PLC0415 -- 拆环

            self._view = mb.open_view(self.path)
        return self._view

    def containers_map(self) -> dict[str, int]:
        """地址键 -> pathID（复用**同一份** ufs 视图；容器表与快走查共用）。"""
        if self._container_map is None:
            try:
                self._container_map = dict(
                    _containers_via_ufs(self.path, with_ids=True, view=self.ufs_view())
                )
            except Exception:  # noqa: BLE001 -- 读不出来 ⇒ 交给调用方回退
                self._container_map = {}
                self.fast_container_fallbacks += 1
        return self._container_map

    def class_name_of(self, path_id: int) -> str:
        """pathID -> 类名（`MonoBehaviour` / `SkinnedMeshRenderer` …），**不 load UnityPy**。

        先走我们自己的元数据（class_id -> `UnityPy.enums.ClassIDType` 的名字，纯枚举查表），
        查不到再回退 UnityPy 的对象表。
        """
        view = self.ufs_view()
        entry = view.entry(int(path_id))
        type_id = int(entry.type_id)
        class_id = int(view.meta.types[type_id].class_id)
        cached = self._class_names.get(class_id)
        if cached is not None:
            return cached
        name = ""
        try:
            name = str(require_unitypy().enums.ClassIDType(class_id).name)
        except Exception:  # noqa: BLE001 -- 枚举里没有这个 classID ⇒ 交给 UnityPy 对象表
            obj, _reader = self._object_or_dep(int(path_id))
            name = obj.type.name if obj is not None else ""
        self._class_names[class_id] = name
        return name

    def read_typetree(self, path_id: int) -> Mapping[str, object]:
        """读一个对象的字段树。

        ★ 2026-10-18：先走**我们自己的** `clone.read_object(..., bulk_arrays=True)`
        （同一份类型树 + 同一份原始字节，但原始类型数组一次读完），
        认不得的形状**回退** UnityPy 的通用解器（⛔ 宁可慢，不许读不出来）。
        """
        # ① 走**我们自己的**视图（不 load UnityPy）：raw 字节 + 同一份类型树。
        fast = self._tree_from_view(int(path_id))
        if fast is not None:
            return fast
        # ② 回退 UnityPy（依赖包里的对象 / 我们的视图认不得的形状）。
        obj, _reader = self._object_or_dep(path_id)
        if obj is None:
            raise KeyError(f"这个包里没有 pathID={path_id}")
        slow = self._fast_tree(obj)
        if slow is not None:
            return slow
        return obj.read_typetree()  # type: ignore[attr-defined]

    def _mono_script_prefix(self, path_id: int) -> int | None:
        """MonoBehaviour 的 `m_Script` —— 只读类型树**到 m_Script 为止**的前缀。

        实测（2026-10-18，T-90M）：`pid -1888913525069428057` 这颗整解析 **5.63 s**
        （占了整次 prefab 走查 5.7 s 的 98%），而我们只用到它的 `m_Script`。
        ⛔ 认不得返回 None（调用方回退整颗读）。
        """
        try:
            view = self.ufs_view()
            if not view.has(int(path_id)):
                return None
            type_id = int(view.entry(int(path_id)).type_id)
            nodes = view.nodes_for(type_id)
            start = None
            for index, node in enumerate(nodes):
                if int(node.level) == 1 and node.name == "m_Script":
                    start = index
                    break
            if start is None:
                return None
            # ⚠ 必须把 m_Script **自己的子节点**（m_FileID / m_PathID）也带上，
            #   否则 `_build_tree` 只见到一个没有子项的 PPtr 节点，读不出值。
            cut = start + 1
            while cut < len(nodes) and int(nodes[cut].level) > 1:
                cut += 1
            tree = clone_mod.read_object(
                view.raw(int(path_id)),
                nodes[:cut],
                little_endian=view.meta.header.endian != ">",
                bulk_arrays=True,
                allow_leftover=True,
            )
        except Exception:  # noqa: BLE001 -- 前缀形状不认识 ⇒ 交给调用方整颗读
            return None
        return _dict_pptr(tree.get("m_Script"))

    def _tree_from_view(self, path_id: int) -> dict[str, object] | None:
        """`monoscript_build.SerializedFileView` -> 字段树（认不得返回 None）。"""
        try:
            view = self.ufs_view()
            if not view.has(int(path_id)):
                return None
            type_id = int(view.entry(int(path_id)).type_id)
            return dict(
                clone_mod.read_object(
                    view.raw(int(path_id)),
                    view.nodes_for(type_id),
                    little_endian=view.meta.header.endian != ">",
                    bulk_arrays=True,
                )
            )
        except Exception:  # noqa: BLE001 -- 认不得 ⇒ None（调用方回退 UnityPy）
            self.fast_tree_fallbacks += 1
            return None

    def _fast_tree(self, obj: object) -> dict[str, object] | None:
        """对象 -> 字段树（`clone` 快读器）；认不得返回 None（由调用方走老路）。"""
        try:
            nodes = _nodes_from_unitypy(obj.serialized_type.node)  # type: ignore[attr-defined]
            endian = str(getattr(getattr(obj, "reader", None), "endian", "<") or "<")
            return dict(
                clone_mod.read_object(
                    obj.get_raw_data(),  # type: ignore[attr-defined]
                    nodes,
                    little_endian=endian != ">",
                    bulk_arrays=True,
                )
            )
        except Exception:  # noqa: BLE001 -- 快读器认不得 ⇒ None（调用方回退，⛔ 不静默）
            self.fast_tree_fallbacks += 1
            return None

    # ---- 流式 ----
    def stream_resolver(self) -> Any:
        """给 mesh.read_mesh / texture.read_texture_2d 用的回调（走**我们的**读侧）。"""
        reader = self.reader

        def resolve(info: object) -> bytes:
            return resolve_stream(reader, info).data

        return resolve

    # ---- 资产 ----
    def read_mesh(self, path_id: int) -> Any:
        """读一颗 Mesh。

        ★ 2026-10-18（用户「拿整套 T-90M 特别慢」）：原来走 `read_typetree()`，也就是
        UnityPy 的通用 TypeTree 解器 —— 它对 `m_VertexData.m_DataSize`（`UInt8[]`）是
        **逐元素 read_u_byte()**。实测 T-90M 单件：**88,186,568 次单字节读 / 184.2 s**
        （cProfile：read_value 59.6 s + read_u_byte 38.6 s + read 27.4 s）。
        现在改走**我们自己的** `clone.read_object(..., bulk_arrays=True)`：同一份类型树
        （UnityPy 的 TypeTreeNode → 我们的扁平 TypeNode）、同一份对象字节
        （`get_raw_data()`），但**原始类型数组一次读完**（`UInt8[]` 直接给 bytes）。

        ⚠ 保底：快读器抛任何异常（类型树里有新的怪形状 / 字节数与树对不上）就**回退**
        到 UnityPy 那条老路 —— 宁可慢，⛔ 不许读不出来。读数在 `mesh_fast_note()` 里。
        """
        return mesh_mod.read_mesh(
            self._mesh_typetree(path_id), path_id=path_id, stream=self.stream_resolver()
        )

    def _mesh_typetree(self, path_id: int) -> Mapping[str, object]:
        obj, _reader = self._object_or_dep(path_id)
        if obj is None:
            raise KeyError(f"这个包里没有 pathID={path_id}")
        try:
            nodes = _nodes_from_unitypy(obj.serialized_type.node)
            endian = str(getattr(getattr(obj, "reader", None), "endian", "<") or "<")
            tree = clone_mod.read_object(
                obj.get_raw_data(),
                nodes,
                little_endian=endian != ">",
                bulk_arrays=True,
            )
        except Exception:  # noqa: BLE001 -- 快读器认不得这颗树 ⇒ 回退老路（⛔ 不许读不出来）
            self.fast_mesh_fallbacks += 1
            return obj.read_typetree()  # type: ignore[attr-defined]
        self.fast_mesh_reads += 1
        return tree

    def read_texture(self, path_id: int) -> Any:
        """读一张 Texture2D。

        ★ 2026-10-18（用户「再快」）：**不再走 UnityPy 的对象**（那会 `UnityPy.load()` 整包，
        实测 5.7 s，而这 5.7 s 就是拿一件剩下的全部大头）—— 改为我们自己的字段树
        （`read_typetree`，快读器）+ 我们自己的 `.resS` 解流（`stream_resolver`）。
        ⛔ 认不得才回退 UnityPy。
        """
        try:
            tree = dict(self.read_typetree(int(path_id)) or {})
            if tree:
                return texture_mod.read_texture_2d(
                    tree, stream=self.stream_resolver(), path_id=int(path_id)
                )
        except Exception:  # noqa: BLE001 -- 认不得 ⇒ 回退老路（⛔ 不许读不出来）
            self.fast_texture_fallbacks += 1
        obj, reader = self._object_or_dep(path_id)
        if obj is None:
            raise KeyError(f"这个包里没有贴图 pathID={path_id}")
        return texture_mod.read_texture_2d(obj, bundle=reader)

    def decode_texture_rgba(self, path_id: int) -> bytes:
        """**我们**解出来的字节 -> RGBA（PIL 行序：上到下）。

        ⚠ 只有「解码」这一步借 UnityPy（texture2ddecoder / crn 是它带的）；字节本身
        来自 resolve_stream，所以「取字节」与「解码」的边界是清楚的。
        """
        from UnityPy.export.Texture2DConverter import parse_image_data  # noqa: PLC0415

        info = self.read_texture(path_id)
        version, platform = self._texture_version_platform()
        tree = self.read_typetree(path_id)
        image = parse_image_data(
            info.raw,
            info.width,
            info.height,
            info.format,
            version,
            platform,
            tree.get("m_PlatformBlob"),
            True,
        )
        return image.convert("RGBA").tobytes()

    def _texture_version_platform(self) -> tuple[tuple[int, int, int, int], int]:
        """引擎版本与目标平台 —— 从**我们自己的**元数据读（⛔ 不 load UnityPy）。"""
        try:
            meta = self.ufs_view().meta
            text = str(getattr(meta, "unity_version", "") or "")
            numbers = [int(part) for part in re.findall(r"\d+", text)[:4]]
            while len(numbers) < 4:
                numbers.append(0)
            platform = int(getattr(meta, "target_platform", 0) or 0)
            if numbers[0] > 0:
                return (numbers[0], numbers[1], numbers[2], numbers[3]), platform
        except Exception:  # noqa: BLE001 -- 元数据读不到 ⇒ 回退 UnityPy 的对象
            pass
        return (0, 0, 0, 0), 0

    # ---- 容器 ----
    def asset_bundle_containers(self) -> tuple[str, ...]:
        """容器地址表（带**进程内缓存**：同一个包内容不变就只解析一次）。"""
        key = _cache_key(self.path)
        cached = _CONTAINERS_CACHE.get(key)
        if cached is not None:
            return tuple(sorted(cached))
        result = tuple(sorted(set(self._asset_bundle_containers_raw())))
        if len(_CONTAINERS_CACHE) > 8:
            _CONTAINERS_CACHE.clear()
        _CONTAINERS_CACHE[key] = frozenset(result)
        return result

    def _asset_bundle_containers_raw(self) -> tuple[str, ...]:
        """AssetBundle.m_Container 的地址键（工单 §7 坑 1 的复核尺子）。

        ★ 2026-10-18：先走**我们自己的**容器读侧（`containers_via_ufs`）——
        它只解析 SerializedFile 元数据 + 那一颗 AssetBundle 对象（批量读数组），
        实测 units 3.35 GB：UnityPy 那条 **10.4~11.5 s**，这条 ~0.3 s。
        ⛔ 我们的读侧认不出来（新版本改了 AssetBundle 形状）就回退 UnityPy。
        """
        try:
            fast = tuple(self.containers_map())
        except Exception:  # noqa: BLE001 -- 我们的读侧认不得 ⇒ 回退（⛔ 不静默）
            self.fast_container_fallbacks += 1
        else:
            if fast:
                return fast
        out: list[str] = []
        for obj in self.env.objects:
            if obj.type.name != "AssetBundle":
                continue
            data = _read(obj)
            for entry in getattr(data, "m_Container", None) or ():
                path = (
                    entry[0]
                    if isinstance(entry, (tuple, list))
                    else getattr(entry, "m_Asset", None)
                )
                if isinstance(path, str):
                    out.append(path)
            break
        return tuple(out)

    def _container(self):
        """容器索引 —— **首次访问会惰性拉依赖包**，那一刻 UnityPy 自己的
        `_build_container_index()` 正在遍历 `cabs`，而拉包会往字典里加东西 ⇒
        `RuntimeError: dictionary changed size during iteration`。

        实测（2026-10-10 · `DLC2_CV9035`，它的预制体在**另一个包**里）：
        第一次抛，**重试即成功**（那时字典已经补齐）。⇒ 在这里吃掉这个坑，
        别让 UnityPy 的实现细节漏到上层（`skin_install` / `prefab` 都直接用它）。
        """
        for _ in range(4):
            try:
                return self.env.container
            except RuntimeError:
                continue
        return self.env.container            # 最后一次：还抛就让它抛，别吞异常

    def container_entries(self) -> tuple[tuple[str, int], ...]:
        """catalog 之外的第二把钥匙：容器里 (地址, pathID)。"""
        out: list[tuple[str, int]] = []
        for path, holder in self._container().items():
            pid = getattr(holder, "path_id", None)
            if pid is None:
                pid = getattr(getattr(holder, "asset", None), "path_id", None)
            out.append((str(path), int(pid) if pid is not None else 0))
        return tuple(out)

    # ---- prefab ----
    def read_prefab_nodes(
        self, asset_path: str | None
    ) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
        """把一个 prefab 走成归一化节点 + 材质表（prefab.build_tree 的输入契约）。

        ★ 2026-10-18（用户第二句「再快」）：先走 `_prefab_nodes_fast` ——
        同一个走法，但每个对象走 `read_typetree()`（clone 快读器），不再走 UnityPy 的
        `.read()`（250 个小对象 × ~24 ms = **6.0 s**）。⛔ 认不得就回退老实现。
        """
        if not asset_path:
            raise ValueError("read_prefab_nodes 需要 asset_path（容器里的地址键）")
        fast = self._prefab_nodes_fast(asset_path)
        if fast is not None:
            return fast
        return self._read_prefab_nodes_slow(asset_path)

    def _prefab_nodes_fast(
        self, asset_path: str
    ) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]] | None:
        """快走查：`m_Container` -> root GameObject -> Transform 树 -> components。

        与老实现**同一个归一化契约**（节点字段、材质表、空槽保留、重复 transform 检查）。
        """
        try:
            mapping = self.containers_map()
        except Exception:  # noqa: BLE001 -- 容器表读不出来 ⇒ 老路
            return None
        resolved = resolve_container_path(mapping.keys(), str(asset_path))
        root_pid = int(mapping[resolved]) if resolved is not None else 0
        if not root_pid:
            return None

        nodes: list[dict[str, object]] = []
        materials: dict[int, dict[str, object]] = {}
        #: ★ 2026-10-19 跨包材质：字典形式的 PPtr 没有 assetsfile，得从 prefab 根对象上取
        #: （externals 表就挂在那个 SerializedFile 上）。取不到就退回「只读主包」的老行为。
        try:
            reader = self.object_at(int(root_pid))
            asset_file = getattr(reader, "assets_file", None) or getattr(
                reader, "assetsfile", None
            )
        except Exception:  # noqa: BLE001
            asset_file = None

        def fields(pid: int | None) -> dict[str, object] | None:
            if not pid:
                return None
            try:
                return dict(self.read_typetree(int(pid)) or {})
            except Exception:  # 单颗读不出来 ⇒ 由外层回退老路
                raise

        def walk(go_pid: int, parent_transform: int | None) -> None:
            go = fields(go_pid)
            if go is None:
                raise KeyError(f"GameObject pathID={go_pid} 读不出来")
            tr_pid = _dict_pptr(go.get("m_Transform"))
            if not tr_pid:
                # ⚠ 真件的 GameObject 类型树里**没有** `m_Transform` 字段（只有 m_Component），
                #   Transform 是组件表里的第一项 ⇒ 按 classID 找（class_name_of 不 load UnityPy）。
                for pair in go.get("m_Component") or ():
                    pid = _dict_pptr((pair or {}).get("component"))
                    if pid and self.class_name_of(int(pid)) == "Transform":
                        tr_pid = pid
                        break
            tr = fields(tr_pid)
            if tr is None:
                raise KeyError(f"Transform 读不出来（GameObject {go_pid}）")
            tr_id = int(tr_pid or 0)
            comps: list[dict[str, object]] = []
            for pair in go.get("m_Component") or ():
                comp_pid = _dict_pptr((pair or {}).get("component"))
                if not comp_pid:
                    continue
                kind = self.class_name_of(int(comp_pid))
                record: dict[str, object] = {"kind": kind, "path_id": int(comp_pid)}
                # ⚠ **只在真要用字段时才解析**：MonoBehaviour 可能有几十 MB 的数组，
                #   无脑 `fields(comp_pid)` 会为了一个 m_Script 付 5.63 s。
                comp: dict[str, object] = {}
                if kind != "MonoBehaviour":
                    comp = fields(comp_pid) or {}
                if kind in prefab_mod.RENDERER_KINDS:
                    mesh_ptr = (comp or {}).get("m_Mesh")
                    record["mesh"] = _dict_pptr(mesh_ptr)
                    record["mesh_file"] = _dict_pptr_file(mesh_ptr)
                    material_ptrs = [
                        m
                        for m in ((comp or {}).get("m_Materials") or ())
                        if _dict_pptr(m)
                    ]
                    material_ids = [_dict_pptr(m) for m in material_ptrs]
                    record["materials"] = tuple(material_ids)
                    # ⚠ 空槽保留成 None（老实现就是 None）：`_dict_pptr` 对空 PPtr 返回 None，
                    #   ⛔ 别补成 0 —— 那会让 dump 与老实现差一处（骨槽按槽号索引）。
                    record["bones"] = tuple(
                        _dict_pptr(b) for b in ((comp or {}).get("m_Bones") or ())
                    )
                    for material_ptr in material_ptrs:
                        self._collect_material_by_id(
                            int(_dict_pptr(material_ptr)),
                            materials,
                            file_id=_dict_pptr_file(material_ptr),
                            assets_file=asset_file,
                        )
                elif kind == prefab_mod.MESH_FILTER:
                    mesh_ptr = (comp or {}).get("m_Mesh")
                    record["mesh"] = _dict_pptr(mesh_ptr)
                    record["mesh_file"] = _dict_pptr_file(mesh_ptr)
                elif kind == prefab_mod.LOD_GROUP:
                    record["size"] = float((comp or {}).get("m_Size") or 0.0)
                    levels = []
                    for lod in (comp or {}).get("m_LODs") or ():
                        ids = []
                        for entry in (lod or {}).get("renderers") or ():
                            pid = _dict_pptr((entry or {}).get("renderer"))
                            if pid:
                                ids.append(pid)
                        levels.append(tuple(ids))
                    record["lods"] = tuple(levels)
                elif kind == "MonoBehaviour":
                    # ⚠ 整颗 MonoBehaviour 可能有几十 MB 的数组（实测 T-90M 有一颗 **5.63 s**），
                    #   而这里只要 `m_Script` ⇒ 只读类型树的**前缀**（读到 m_Script 就停）。
                    script_pid = self._mono_script_prefix(comp_pid)
                    record["script"] = script_pid
                comps.append(record)

            pos = tr.get("m_LocalPosition") or {}
            rot = tr.get("m_LocalRotation") or {}
            scale = tr.get("m_LocalScale") or {}
            nodes.append(
                {
                    "name": str(go.get("m_Name") or ""),
                    "path_id": _dict_pptr(tr.get("m_GameObject")) or 0,
                    "transform_id": tr_id,
                    "parent_transform": parent_transform,
                    "position": (pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0)),
                    "rotation": (
                        rot.get("x", 0.0),
                        rot.get("y", 0.0),
                        rot.get("z", 0.0),
                        rot.get("w", 0.0),
                    ),
                    "scale": (scale.get("x", 0.0), scale.get("y", 0.0), scale.get("z", 0.0)),
                    "components": tuple(comps),
                }
            )
            for child in tr.get("m_Children") or ():
                child_tr = fields(_dict_pptr(child))
                if child_tr is None:
                    raise KeyError(f"子 Transform 读不出来（父 {tr_id}）")
                child_go = _dict_pptr(child_tr.get("m_GameObject"))
                if not child_go:
                    raise KeyError(f"子 Transform {_dict_pptr(child)} 没有 GameObject")
                walk(int(child_go), tr_id)

        try:
            walk(int(root_pid), None)
        except Exception as exc:  # noqa: BLE001 -- 形状变了就回退老路（⛔ 不许读不出来）
            self.fast_prefab_error = f"{type(exc).__name__}: {exc}"
            self.fast_prefab_fallbacks += 1
            return None
        seen: set[int] = set()
        for node in nodes:
            tid = int(node["transform_id"])  # type: ignore[arg-type]
            if tid in seen:
                raise ValueError(f"transform {tid} 走了两遍（层级里有重复引用？）")
            seen.add(tid)
        self.fast_prefab_reads += 1
        return nodes, materials

    def _read_prefab_nodes_slow(
        self, asset_path: str
    ) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
        """老的走法（UnityPy `.read()`）：快走查认不得时兜底。"""
        container = self._container()
        resolved = resolve_container_path(container.keys(), asset_path)
        if resolved is None:
            raise KeyError(f"容器里没有地址 {asset_path!r}")
        root_go = container[resolved].read()

        nodes: list[dict[str, object]] = []
        materials: dict[int, dict[str, object]] = {}

        def walk(go: object, parent_transform: int | None) -> None:
            tr = go.m_Transform.read()  # type: ignore[attr-defined]
            tr_id = _path_id_of(tr)
            comps: list[dict[str, object]] = []
            for pair in getattr(go, "m_Component", None) or ():
                ptr = getattr(pair, "component", None)
                comp = ptr.read() if hasattr(ptr, "read") else ptr
                if comp is None:
                    continue
                kind = type(comp).__name__
                record: dict[str, object] = {"kind": kind, "path_id": _path_id_of(comp)}
                if kind in prefab_mod.RENDERER_KINDS:
                    mesh_ptr = getattr(comp, "m_Mesh", None)
                    record["mesh"] = _pptr_path_id(mesh_ptr)
                    record["mesh_file"] = _pptr_file_id(mesh_ptr)
                    record["materials"] = tuple(
                        pid for pid in (_pptr_path_id(m) for m in (comp.m_Materials or ())) if pid
                    )
                    # ⛔ 空槽要留着：mesh 的 blend_index 是**按槽号**索引 m_Bones 的，
                    #    滤掉空槽会让 29 个骨骼错位（实测 Chassis 38 槽 / 9 个空槽）。
                    record["bones"] = tuple(
                        _pptr_path_id(b) for b in (getattr(comp, "m_Bones", None) or ())
                    )
                    for material in (comp.m_Materials or ()):
                        self._collect_material(material, materials)
                elif kind == prefab_mod.MESH_FILTER:
                    mesh_ptr = getattr(comp, "m_Mesh", None)
                    record["mesh"] = _pptr_path_id(mesh_ptr)
                    record["mesh_file"] = _pptr_file_id(mesh_ptr)
                elif kind == prefab_mod.LOD_GROUP:
                    record["size"] = float(getattr(comp, "m_Size", 0.0) or 0.0)
                    levels = []
                    for lod in getattr(comp, "m_LODs", None) or ():
                        ids = []
                        for entry in getattr(lod, "renderers", None) or ():
                            ptr = getattr(entry, "renderer", None)
                            pid = _pptr_path_id(ptr)
                            if pid:
                                ids.append(pid)
                        levels.append(tuple(ids))
                    record["lods"] = tuple(levels)
                elif kind == "MonoBehaviour":
                    record["script"] = _pptr_path_id(getattr(comp, "m_Script", None))
                comps.append(record)

            pos, rot, scale = tr.m_LocalPosition, tr.m_LocalRotation, tr.m_LocalScale
            nodes.append(
                {
                    "name": str(go.m_Name),
                    "path_id": _pptr_path_id(getattr(tr, "m_GameObject", None)) or 0,
                    "transform_id": tr_id,
                    "parent_transform": parent_transform,
                    "position": (pos.x, pos.y, pos.z),
                    "rotation": (rot.x, rot.y, rot.z, rot.w),
                    "scale": (scale.x, scale.y, scale.z),
                    "components": tuple(comps),
                }
            )
            for child in getattr(tr, "m_Children", None) or ():
                ct = child.read()
                walk(ct.m_GameObject.read(), tr_id)

        walk(root_go, None)
        seen: set[int] = set()
        for node in nodes:
            tid = int(node["transform_id"])  # type: ignore[arg-type]
            if tid in seen:
                raise ValueError(f"transform {tid} 走了两遍（层级里有重复引用？）")
            seen.add(tid)
        return nodes, materials

    def collect_material(
        self, ptr: object | None, out: dict[int, dict[str, object]]
    ) -> None:
        """公开入口：把一颗材质的「名字 + 槽 + 标量/颜色」收进 `out`（= `_collect_material`）。

        2026-10-17 加：`texture_dump` 要沿导入链同一条读法拿材质，⛔ 不该去摸私有方法。
        """
        self._collect_material(ptr, out)

    def _shader_name(self, ptr: object | None) -> str:
        """只取 Shader 的 `m_Name` —— ⛔ **不解析整颗 Shader 的类型树**。

        ★ 2026-10-18（用户「拿整套 T-90M 特别慢」）实测：单件 62 s 里 **24.4 s** 花在两颗
        Shader 上（34 MB 那颗 19.4 s、9 MB 那颗 5.0 s），原因是 `m_ParsedForm` 触发了整棵
        类型树解析（88,838,534 次 read_value 的绝大部分都在这里）—— 而我们只要它的**名字**。

        Shader 类型树的第一项就是 `m_Name`（string = i32 长度 + UTF-8 字节），
        所以直接读对象原始字节的头 4 字节就够。形状对不上（不是 string / 长度离谱）
        就**退回**老路解析 —— 宁可慢，⛔ 不许静默给空名字。
        """
        # ⛔ **不许 `read_ptr(ptr)`**：PPtr.read() 会把整颗 Shader 的类型树解析一遍
        #   （34 MB 那颗实测 19.4 s，一次拿件 3 颗合计 24.1 s）。这里只要 pathID，
        #   然后走 `read_typetree()` 的快读器。
        # ⚠ 两种 PPtr 形状都要认：UnityPy 的对象（老路）与 dict（快走查 / 材质 dict 路径）。
        shader_id = _pptr_path_id(ptr) if not isinstance(ptr, Mapping) else _dict_pptr(ptr)
        if not shader_id:
            return ""
        try:
            tree = dict(self.read_typetree(int(shader_id)) or {})
        except Exception:  # noqa: BLE001 -- 读不到 ⇒ 回退老路（⛔ 不静默给空名）
            tree = {}
        # ⚠ Shader 的**顶层 `m_Name` 是空串**（实测 29/29，见 shader_build.py 头）——
        #   真名在 `m_ParsedForm.m_Name`。
        parsed = tree.get("m_ParsedForm") or {}
        name = str((parsed if isinstance(parsed, Mapping) else {}).get("m_Name", ""))
        if name:
            return name
        self.fast_shader_fallbacks += 1
        shader = self.read_ptr(ptr)
        if shader is None:
            return ""
        parsed_form = getattr(shader, "m_ParsedForm", None)
        return str(getattr(parsed_form, "m_Name", "") or getattr(shader, "m_Name", "") or "")

    def _collect_material(self, ptr: object | None, out: dict[int, dict[str, object]]) -> None:
        mid = _pptr_path_id(ptr)
        if not mid or mid in out:
            return
        data = self.read_ptr(ptr)  # ★ 跨包时按 externals 打开依赖包再读（2026-10-17）
        self._collect_material_fields(int(mid), data, out)

    def _collect_material_dict(
        self, mid: int, tree: Mapping[str, object], out: dict[int, dict[str, object]]
    ) -> None:
        """快走查用：材质字段树（dict）-> 与老路**同形**的材质记录。"""
        props = tree.get("m_SavedProperties")
        props = props if isinstance(props, Mapping) else {}
        slots: list[tuple[str, int | None]] = []
        for pair in props.get("m_TexEnvs") or ():
            if not isinstance(pair, Mapping):
                continue
            slot_name = str(pair.get("first") or "")
            second = pair.get("second")
            tex = second.get("m_Texture") if isinstance(second, Mapping) else None
            slots.append((slot_name, _dict_pptr(tex)))
        floats: dict[str, float] = {}
        for pair in props.get("m_Floats") or ():
            if not isinstance(pair, Mapping):
                continue
            try:
                floats[str(pair.get("first"))] = float(pair.get("second"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        colors: dict[str, tuple[float, ...]] = {}
        for pair in props.get("m_Colors") or ():
            if not isinstance(pair, Mapping):
                continue
            value = pair.get("second")
            value = value if isinstance(value, Mapping) else {}
            colors[str(pair.get("first"))] = tuple(
                float(value.get(key, 0.0)) for key in ("r", "g", "b", "a")
            )
        shader_name = self._shader_name(tree.get("m_Shader"))
        stacks: list[tuple[str, str]] = []
        for entry in tree.get("m_BuildTextureStacks") or ():
            if not isinstance(entry, Mapping):
                continue
            group = str(entry.get("groupName") or "")
            item = str(entry.get("itemName") or "")
            if group and item:
                stacks.append((group, item))
        out[int(mid)] = {
            "name": str(tree.get("m_Name") or f"<material {mid}>"),
            "shader": shader_name,
            "build_texture_stacks": tuple(stacks),
            "texture_slots": tuple(slots),
            "floats": floats,
            "colors": colors,
            "valid_keywords": tuple(str(k) for k in (tree.get("m_ValidKeywords") or ())),
            "invalid_keywords": tuple(str(k) for k in (tree.get("m_InvalidKeywords") or ())),
        }

    def _dep_material(self, mid: int, file_id: int, assets_file: object | None) -> object | None:
        """跨包材质：按 `m_FileID` 打开依赖包，再按 pathID 取那颗材质对象（读不到给 None）。

        ★ 2026-10-19（用户现场：DLC2_M113 履带没贴图）：prefab 在 dlc_2 包，履带材质
        `US_LightTrack_1` 在 units 包。快走查只按 pathID 去**主包**读，读不到就落成
        `<material …>`（无贴图）。这里补的正是那一步。
        """
        cab = self._cab_name(assets_file, file_id)
        if not cab or not self._load_dependency(cab):
            return None
        found = self._dep_objects.get(int(mid))
        if found is None:
            return None
        obj, _reader = found
        try:
            return obj.read()
        except Exception:  # noqa: BLE001 -- 依赖包里的对象读不动 ⇒ 退回占位
            return None

    def _collect_material_by_id(
        self,
        mid: int,
        out: dict[int, dict[str, object]],
        *,
        file_id: int = 0,
        assets_file: object | None = None,
    ) -> None:
        """按 pathID 收一颗材质（快走查里拿到的是字典形式的 PPtr，没有 UnityPy 对象）。

        `file_id != 0` ⇒ 这颗材质在**别的包**里，先去依赖包按 pathID 取；取不到才回主包。
        """
        if not mid or int(mid) in out:
            return
        if file_id and assets_file is not None:
            data = self._dep_material(int(mid), int(file_id), assets_file)
            if data is not None:
                self._collect_material_fields(int(mid), data, out)
                return
        tree: dict[str, object] | None = None
        try:
            tree = dict(self.read_typetree(int(mid)) or {})
        except Exception:  # noqa: BLE001 -- 读不到就走「<material …>」那条（与老路同形）
            tree = None
        self._collect_material_fields(int(mid), tree, out)

    def _collect_material_fields(
        self, mid: int, data: object | None, out: dict[int, dict[str, object]]
    ) -> None:
        if isinstance(data, Mapping):
            self._collect_material_dict(int(mid), data, out)
            return
        if data is None:
            out[mid] = {
                "name": f"<material {mid}>",
                "shader": "",
                "texture_slots": (),
                "floats": {},
                "colors": {},
                "valid_keywords": (),
                "invalid_keywords": (),
                "build_texture_stacks": (),
            }
            return
        slots: list[tuple[str, int | None]] = []
        floats: dict[str, float] = {}
        colors: dict[str, tuple[float, ...]] = {}
        props = getattr(data, "m_SavedProperties", None)
        for entry in getattr(props, "m_TexEnvs", None) or ():
            slot = str(entry[0])
            tex = getattr(entry[1], "m_Texture", None)
            slots.append((slot, _pptr_path_id(tex)))
        # W4 补读：映射表要的标量（_Metallic/_Smoothness/_NormalScale/_AlphaCutoff/…）
        # 与颜色（_BaseColor/_EmissiveColor）、关键字（_ALPHATEST_ON/_MASKMAP/…）都在这里。
        # ⚠ 读侧只多做"原样搬出来"这一件事：不解释、不换算、不按常识改（见设计稿 §7 已知坑 3）。
        for entry in getattr(props, "m_Floats", None) or ():
            try:
                floats[str(entry[0])] = float(entry[1])
            except (TypeError, ValueError):  # pragma: no cover -- 坏值不该废掉整个材质
                continue
        for entry in getattr(props, "m_Colors", None) or ():
            value = entry[1]
            channels = tuple(
                float(getattr(value, key, 0.0))
                for key in ("r", "g", "b", "a")
            )
            colors[str(entry[0])] = channels
        shader_name = self._shader_name(getattr(data, "m_Shader", None))
        # ★ 2026-10-17：把 VT 的 (group, item) 原样搬出来 —— 插件要**按每颗材质**
        #   去抽它自己的法线/遮罩（旧版按「单位」取图 ⇒ 多材质单位除第一颗外全接错）。
        stacks: list[tuple[str, str]] = []
        for entry in getattr(data, "m_BuildTextureStacks", None) or ():
            group = str(getattr(entry, "groupName", "") or "")
            item = str(getattr(entry, "itemName", "") or "")
            if group and item:
                stacks.append((group, item))
        out[mid] = {
            "name": str(getattr(data, "m_Name", "") or f"<material {mid}>"),
            "shader": shader_name,
            "build_texture_stacks": tuple(stacks),
            "texture_slots": tuple(slots),
            "floats": floats,
            "colors": colors,
            "valid_keywords": tuple(str(k) for k in (getattr(data, "m_ValidKeywords", None) or ())),
            "invalid_keywords": tuple(
                str(k) for k in (getattr(data, "m_InvalidKeywords", None) or ())
            ),
        }


def open_source(bundle_path: str | Path, *, deps: bool = False) -> AssetSource:
    """打开一个 bundle 当读源（用完要 close / with）。

    :param deps: 允许**按需**把跨包 PPtr 指向的依赖包加载进同一个 Environment（★ 2026-10-17）。
    """
    return AssetSource(bundle_path, deps=deps)


def open_unique_bundle(
    aa_dir: str | Path, candidates: Sequence[str], asset_path: str, *, deps: bool = False
) -> tuple[AssetSource, str]:
    """找出**唯一**含 asset_path 的候选包并打开它（调用方负责 close）。

    :raises FileNotFoundError: 一个都没命中，或者命中多个（**不许猜**）。
    """
    base = Path(aa_dir)
    hits: list[tuple[str, AssetSource]] = []
    for name in candidates:
        path = base / name
        if not path.is_file():
            continue
        source = AssetSource(path, deps=deps)
        if resolve_container_path(source.asset_bundle_containers(), asset_path) is not None:
            hits.append((name, source))
        else:
            source.close()
    if len(hits) != 1:
        for _name, opened in hits:
            opened.close()
        raise FileNotFoundError(
            f"{asset_path!r} 在 {len(hits)} 个候选包里命中（候选 {len(candidates)} 个）："
            f"{[n for n, _ in hits]}"
        )
    name, source = hits[0]
    return source, name


@contextmanager
def open_unique_source(
    aa_dir: str | Path, candidates: Sequence[str], asset_path: str, *, deps: bool = False
) -> Iterator[tuple[AssetSource, str]]:
    """找出**唯一**含 asset_path 的候选包，并把它**保持打开**（工单 §7 坑 1）。

    catalog 的 group 字段会过度包含（实测 RU_T72B 一个键给 **4 个候选包**），所以
    必须拿 AssetBundle.m_Container 复核 —— 实测只有 units 命中（ammoprefabs 166 条
    容器 / vfx 735 条 / unitybuiltinshaders 0 条，都不含这个 prefab）。

    :raises FileNotFoundError: 一个都没命中，或者命中多个（**不许猜**）。
    """
    source, name = open_unique_bundle(aa_dir, candidates, asset_path, deps=deps)
    try:
        yield source, name
    finally:
        source.close()
