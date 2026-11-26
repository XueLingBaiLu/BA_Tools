"""把游戏原件 shader 移植进我们自己造出来的包（2026-10-26）。

## 为什么要有这一步

AssetRipper 导出工程里的 `Assets/Shader/*.shader` 是**桩**：一条 `_MainTex * _Color` 的 Pass
（实测 `HDRP_Lit_0.shader` = 11,664 B / 0 条 `#pragma shader_feature`）。Unity 造包时会把**这颗桩**
重新编译进包，材质再指向包内那颗（`m_Shader.m_FileID = 0`）⇒ 运行时用的就是 1 个 Pass 的假
HDRP/Lit ⇒ **没有法线、没有遮罩、没有 PBR**。

游戏自己不是这么干的：它的材质和 shader 装在**同一颗包**里，shader 是 Unity **编译好的产物**
（实测 units 包里 `HDRP/Lit` = 2 subshader / 8 Pass / 9,074,064 B，`Shader Graphs/UnitV3` = 2/6 /
34,352,912 B）。所以正确做法不是让 Unity 重编译，而是**照抄游戏那颗**：PathID 保持不变 ⇒
包里所有材质的 PPtr 一个字都不用改。

## 实测口径（复算命令）

```
packing 前：_work/probe_kord_shader_passes.py <包> → KORD_SHADER_PASSES=1
移植后  ：同一命令              → KORD_SHADER_PASSES=8
```

## 边界

* 只换 `classID = 48`（Shader）对象的 payload，**不动** PathID / 容器 / 依赖；
* 认不出的名字（例如 VFX 现场生成的 `Hidden/VFX/...`）**原样留着**并回报，⛔ 不静默丢；
* 源包（游戏 units 包）**只读**，抄出来缓存在 `shader-donor/` 里，第二次起不再开大包。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import builder, clone

# 同包内核：开包/写包只有 unit_pack 这一份实现，别在这里再写一套。
from ba_mod_maker.core.assets import unit_pack
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "DONOR_DIR",
    "SHADER_CLASS",
    "extract_donors",
    "load_donors",
    "name_of_stem",
    "passes_of",
    "shader_report",
    "stem_of",
    "transplant",
    "transplant_from_game",
    "units_bundle_of",
]

#: Unity 的 Shader classID
SHADER_CLASS = 48
#: 缓存目录名（跟着包走，不进游戏目录）
DONOR_DIR = "shader-donor"
_MANIFEST = "manifest.json"
#: shader 名字里的 `/` 落到文件名上换成 `~`（Windows 文件名不吃 `/`）
_SEP = "~"
#: 缓存格式版本 —— 老缓存（没有 `deps` 那一栏）必须重抄，否则依赖仍会悬空。
CACHE_VERSION = 2


def stem_of(name: str) -> str:
    """shader 名 -> 缓存文件名（不含扩展名）。"""
    return str(name).replace("/", _SEP).replace("\\", _SEP)


def name_of_stem(stem: str) -> str:
    """缓存文件名 -> shader 名（:func:`stem_of` 的逆）。"""
    return str(stem).replace(_SEP, "/")


def passes_of(obj: Mapping[str, Any]) -> int:
    """一棵 Shader 字段树里所有 subshader 的 Pass 数之和。"""
    form = obj.get("m_ParsedForm") or {}
    subs = form.get("m_SubShaders") or []
    return sum(len(sub.get("m_Passes") or []) for sub in subs if isinstance(sub, dict))


def _form_name(obj: Mapping[str, Any]) -> str:
    form = obj.get("m_ParsedForm") or {}
    return str(form.get("m_Name") or "")


def _read_obj(pack, pid: int) -> tuple[bytes, dict]:
    o = pack.by_pid[int(pid)]
    raw = bytes(pack.raw_reader.read_range(
        pack.node_offset + pack.data_offset + o[2], o[3]))
    obj = clone.read_object(raw, pack.nodes_for(o[4]), ref_types=pack.pool)
    return raw, obj


def _read_shader(pack, pid: int) -> tuple[bytes, dict]:
    return _read_obj(pack, pid)


def _inband_refs(payload: bytes, nodes, pool) -> list[int]:
    """一段 payload 里**包内**引用的 pathID 列表（fileID=0）。"""
    try:
        obj = clone.read_object(payload, nodes, ref_types=pool)
    except Exception:  # noqa: BLE001 —— 解不开就跳过，不猜
        return []
    return [int(tpid) for _, fid, tpid in builder.iter_pptrs(obj) if fid == 0 and tpid]


def _collect_deps(pack, root_pids, *, depth: int = 3) -> dict[int, int]:
    """源包里被这些对象**包内引用**（fileID=0）的目标：{pathID: classID}。

    只走 `depth` 层（实测真件 shader 的依赖就是 3 颗 FallbackError，再往下没有）。
    """
    seen: set[int] = {int(p) for p in root_pids}
    out: dict[int, int] = {}
    frontier = [int(p) for p in root_pids]
    for _ in range(max(1, int(depth))):
        nxt: list[int] = []
        for pid in frontier:
            if pid not in pack.by_pid:
                continue
            try:
                _, obj = _read_obj(pack, pid)
            except Exception:  # noqa: BLE001 —— 解不开就跳过，不猜
                continue
            for _, fid, tpid in builder.iter_pptrs(obj):
                if fid != 0 or tpid == 0 or tpid in seen:
                    continue
                o = pack.by_pid.get(tpid)
                if o is None:
                    continue
                seen.add(tpid)
                out[tpid] = int(pack.cid.get(o[4], 0))
                nxt.append(tpid)
        frontier = nxt
        if not frontier:
            break
    return out


def units_bundle_of(game_root: str | Path) -> Path:
    """游戏根 -> `units_assets_all_*.bundle`（唯一那颗，名字带 hash）。"""
    aa = Path(game_root) / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC"
    found = sorted(aa.glob("units_assets_all_*.bundle"))
    if not found:
        raise AssetError(
            f"这里找不到 units 包（缺 {aa}\\units_assets_all_*.bundle）",
            hint="--game 指游戏安装目录；确认 BrokenArrow_Data\\StreamingAssets\\aa\\PC 里有包",
        )
    return found[0]


def extract_donors(source: str | Path, cache_dir: str | Path, *,
                   wanted: set[str] | None = None) -> dict[str, dict]:
    """从源包抄出真件 shader，存 `cache_dir/<名字>.bin` ＋ manifest；返回 {名字: 读数}。

    `wanted=None` = 全抄（units 包实测 29 颗）。同名只留第一颗（Unity 里同名就是同一颗）。
    """
    src = Path(source)
    out = Path(cache_dir)
    out.mkdir(parents=True, exist_ok=True)
    pack = unit_pack._open_pack(src, materialize=False)  # noqa: SLF001
    rows: dict[str, dict] = {}
    for pid, o in pack.by_pid.items():
        if int(pack.cid.get(o[4], 0)) != SHADER_CLASS:
            continue
        raw, obj = _read_shader(pack, int(pid))
        name = _form_name(obj)
        if not name or (wanted is not None and name not in wanted):
            continue
        if name in rows:
            continue
        (out / (stem_of(name) + ".bin")).write_bytes(raw)
        rows[name] = {"bytes": len(raw), "passes": passes_of(obj), "path_id": int(pid)}
    # ★ 2026-10-26 实测：真件 shader 的 `m_Dependencies` 指向**同包**的 3 颗 FallbackError
    #   （pathID 1354542265007958578 / -8015457887526078469 / 3819691914921817934，各 3~4 KB）。
    #   只抄 shader 不抄它们 ⇒ 我们包里就是悬空引用（check-model 当场红）。
    #   所以把「真件 shader 在源包里引用到的对象」也抄下来，路径 = 源包 pathID 原样。
    deps = _collect_deps(pack, [row["path_id"] for row in rows.values()])
    dep_rows: dict[str, dict] = {}
    # 抄到的 shader 本身也要能按**源包 pathID** 取到：它们当依赖被引用时（实测 3 颗
    # FallbackError 正是如此）必须按源包 pathID 原样落进我们包里，引用才对得上。
    for name, row in rows.items():
        dep_rows[str(row["path_id"])] = {
            "class_id": SHADER_CLASS, "bytes": row["bytes"],
            "file": stem_of(name) + ".bin",
        }
    for pid, cls in deps.items():
        o = pack.by_pid.get(pid)
        if o is None:
            continue
        if cls == 28:
            raise AssetError(
                f"真件 shader 的依赖里有流式贴图 {pid}：这条还没做（要内联像素）",
                hint="先把它抄成内联 Texture2D，再放进 deps")
        raw = bytes(pack.raw_reader.read_range(
            pack.node_offset + pack.data_offset + o[2], o[3]))
        fname = f"dep_{pid}.bin"
        (out / fname).write_bytes(raw)
        dep_rows[str(pid)] = {"class_id": int(cls), "bytes": len(raw), "file": fname}
    stat = src.stat()
    manifest = {
        "cache_version": CACHE_VERSION,
        "source": str(src),
        "source_bytes": stat.st_size,
        "source_mtime": stat.st_mtime,
        "shaders": rows,
        "deps": dep_rows,
    }
    (out / _MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return rows


def load_donors(cache_dir: str | Path) -> dict[str, bytes]:
    """读回缓存里的真件 shader（{名字: payload 字节}）。"""
    root = Path(cache_dir)
    manifest = root / _MANIFEST
    if not manifest.is_file():
        raise AssetError(
            f"还没有抄过游戏 shader：{manifest} 不存在",
            hint="先跑 extract_donors()（或 transplant_from_game 的自动抄）",
        )
    rows = json.loads(manifest.read_text(encoding="utf-8")).get("shaders") or {}
    donors: dict[str, bytes] = {}
    for name in rows:
        path = root / (stem_of(name) + ".bin")
        if path.is_file():
            donors[name] = path.read_bytes()
    return donors


def load_donor_deps(cache_dir: str | Path) -> dict[int, tuple[int, bytes]]:
    """读回缓存里的 shader 依赖对象（{pathID: (classID, payload)}）。"""
    root = Path(cache_dir)
    manifest = root / _MANIFEST
    if not manifest.is_file():
        return {}
    rows = json.loads(manifest.read_text(encoding="utf-8")).get("deps") or {}
    deps: dict[int, tuple[int, bytes]] = {}
    for key, row in rows.items():
        path = root / str(row.get("file") or "")
        if path.is_file():
            deps[int(key)] = (int(row.get("class_id", 0)), path.read_bytes())
    return deps


def shader_report(bundle: str | Path) -> list[dict]:
    """只读盘点：包里每颗 shader 的名字 / subshader 数 / Pass 数 / payload 字节。"""
    pack = unit_pack._open_pack(bundle)  # noqa: SLF001
    rows: list[dict] = []
    for pid, o in pack.by_pid.items():
        if int(pack.cid.get(o[4], 0)) != SHADER_CLASS:
            continue
        raw, obj = _read_shader(pack, int(pid))
        form = obj.get("m_ParsedForm") or {}
        rows.append({
            "name": _form_name(obj),
            "path_id": int(pid),
            "subshaders": len(form.get("m_SubShaders") or []),
            "passes": passes_of(obj),
            "bytes": len(raw),
        })
    return rows


def transplant(bundle: str | Path, donors: Mapping[str, bytes], *,
               deps: Mapping[int, tuple[int, bytes]] | None = None,
               out: str | Path | None = None) -> dict:
    """包里同名 shader 的 payload 换成真件（PathID 不动）；返回替换清单。

    没抄到的名字（VFX 现场生成的 `Hidden/VFX/...`、`Hidden/HDRP/FallbackError` 等）**原样留着**
    并记进 `kept` —— ⛔ 不静默丢。
    """
    pack = unit_pack._open_pack(bundle)  # noqa: SLF001
    specs, swapped, kept = [], [], []
    for spec in pack.specs:
        if int(spec.class_id) != SHADER_CLASS:
            specs.append(spec)
            continue
        raw, obj = _read_shader(pack, int(spec.path_id))
        name = _form_name(obj)
        blob = donors.get(name)
        if blob is None:
            kept.append(name)
            specs.append(spec)
            continue
        if blob == spec.payload:
            specs.append(spec)
            continue
        specs.append(dataclasses.replace(spec, payload=blob))
        swapped.append({"name": name, "before_passes": passes_of(obj),
                        "before_bytes": len(raw), "bytes": len(blob)})
    # ★ 真件 shader 在**源包里**引用的对象（实测 3 颗 FallbackError）必须一起搬进来，
    #   否则换完就是悬空引用（check-model 报 6 条、游戏里可能拿不到 fallback shader）。
    imported: list[dict] = []
    if deps:
        present = {int(spec.path_id) for spec in specs}
        # 用**要写进去的那段 payload**（别人的 payload 已被换成真件）去找包内引用，
        # 而不是包里旧的（旧的是桩，引用形状完全不同）。
        shader_nodes = None
        for spec in specs:
            if int(spec.class_id) == SHADER_CLASS and spec.path_id in pack.by_pid:
                shader_nodes = pack.nodes_for(pack.by_pid[spec.path_id][4])
                break
        queue: list[int] = []
        for spec in specs:
            if int(spec.class_id) != SHADER_CLASS:
                continue
            queue.extend(_inband_refs(spec.payload, shader_nodes, pack.pool))
        while queue:
            pid = int(queue.pop())
            if pid in present:
                continue
            hit = deps.get(pid)
            if hit is None:
                continue
            cls, blob = hit
            specs.append(builder.ObjectSpec(
                class_id=int(cls), fields=None, payload=blob, path_id=int(pid),
                script_index=None, label="shader-dep",
            ))
            present.add(int(pid))
            if int(cls) == SHADER_CLASS:
                queue.extend(_inband_refs(blob, shader_nodes, pack.pool))
            imported.append({"path_id": int(pid), "class_id": int(cls), "bytes": len(blob)})
    if not swapped and not imported:
        return {"bundle": str(bundle), "swapped": [], "kept": sorted(set(kept)),
                "imported": []}
    pack.specs = specs
    # ★ 重写会把 AssetBundle 的 `m_Name` / 节点名一起按 `pack.name`（= 文件名）重算 ——
    #   实测这样会把包内真值从 `ru_t72b3` 变成 `ru_t72b3_assets_all_….bundle`。
    #   包内名是 catalog 认的那个，必须**照原样**写回去（节点名变了无所谓：
    #   `_write_pack` 会把流式贴图的 `m_StreamData.path` 一起修到新节点名）。
    internal = str((pack.ab_fields or {}).get("m_AssetBundleName") or "")
    if internal:
        pack.name = builder.internal_bundle_name_of(internal)
    target = unit_pack._write_pack(pack, out=out)  # noqa: SLF001
    return {"bundle": str(target), "swapped": swapped, "kept": sorted(set(kept)),
            "imported": imported}


def link_game_shaders(bundle: str | Path, units_bundle: str | Path, *,
                      out: str | Path | None = None) -> dict:
    """乙方案：**不拷 shader**，把包里材质的 `m_Shader` 改成指向 units 包的**跨包引用**。

    为什么要有这一步（实测教训）：把游戏原件 shader 拷进我们的包，结构侧全绿、check-model 也绿，
    但真机**静默不画**（12:16 那局）。游戏自己的包是靠 externals（`archive:/CAB-…`）互相引用的
    （模板包实测 6 条），所以我们照它的形状走：材质 PPtr 的 `m_FileID` 指向新加的那条 external，
    `m_PathID` 用**源包里那颗 shader 的 pathID**，并把 units 包名写进 `m_Dependencies`。

    :param bundle: 我们的包（就地改写；`out` 给出就写别处）
    :param units_bundle: 游戏 units 包（只读，用来查 shader name -> pathID）
    :returns: ``{"bundle": .., "file_id": .., "linked": [...], "kept": [...]}``
    """
    pack = unit_pack._open_pack(bundle)  # noqa: SLF001
    src = unit_pack._open_pack(units_bundle, materialize=False)  # noqa: SLF001
    name_to_pid: dict[str, int] = {}
    for pid, o in src.by_pid.items():
        if int(src.cid.get(o[4], 0)) != SHADER_CLASS:
            continue
        _, obj = _read_obj(src, int(pid))
        name = _form_name(obj)
        if name and name not in name_to_pid:
            name_to_pid[name] = int(pid)
    if not name_to_pid:
        raise AssetError(
            f"源包里一颗 shader 都没读到：{units_bundle}",
            hint="--game 指游戏安装目录（要指向真正的 units_assets_all_*.bundle）",
        )
    external = builder.external_for_bundle(units_bundle)
    file_id = len(pack.meta.externals) + 1
    specs: list = []
    linked: list[dict] = []
    kept: list[str] = []
    for spec in pack.specs:
        if int(spec.class_id) != 21:
            specs.append(spec)
            continue
        _, mat = _read_obj(pack, int(spec.path_id))
        cur = mat.get("m_Shader") or {}
        if int(cur.get("m_FileID", 0)) != 0:
            specs.append(spec)
            continue
        shader_pid = int(cur.get("m_PathID", 0))
        name = ""
        o = pack.by_pid.get(shader_pid)
        if o is not None and int(pack.cid.get(o[4], 0)) == SHADER_CLASS:
            _, sh = _read_obj(pack, shader_pid)
            name = _form_name(sh)
        target = name_to_pid.get(name)
        if target is None:
            kept.append(name or "<无名>")
            specs.append(spec)
            continue
        mat["m_Shader"] = {"m_FileID": int(file_id), "m_PathID": int(target)}
        specs.append(builder.ObjectSpec(
            class_id=21, fields=mat, path_id=int(spec.path_id),
            script_index=spec.script_index, label="material-linked",
        ))
        linked.append({"material": str(mat.get("m_Name")), "shader": name,
                       "path_id": int(target)})
    if not linked:
        return {"bundle": str(bundle), "file_id": 0, "linked": [], "kept": sorted(set(kept))}
    pack.specs = specs
    # 包内真名照原样写回（重写会按 pack.name 重算 AssetBundle 的 m_Name）。
    internal = str((pack.ab_fields or {}).get("m_AssetBundleName") or "")
    if internal:
        pack.name = builder.internal_bundle_name_of(internal)
    deps = list(dict.fromkeys([*(pack.deps or []), Path(units_bundle).name]))
    target_path = unit_pack._write_pack(  # noqa: SLF001
        pack, deps=deps, extra_externals=(external,), out=out)
    return {"bundle": str(target_path), "file_id": int(file_id),
            "linked": linked, "kept": sorted(set(kept)), "deps": deps}


def transplant_from_game(bundle: str | Path, *, game: str | Path | None = None,
                         cache_dir: str | Path | None = None,
                         out: str | Path | None = None) -> dict:
    """端到端：没有缓存就先从游戏 units 包抄一次，再把包里同名 shader 全换成真件。"""
    cache = Path(cache_dir) if cache_dir else Path(bundle).parent / DONOR_DIR
    stale = True
    if (cache / _MANIFEST).is_file():
        try:
            head = json.loads((cache / _MANIFEST).read_text(encoding="utf-8"))
            stale = int(head.get("cache_version", 0)) < CACHE_VERSION
        except (OSError, ValueError):
            stale = True
    if stale:
        root = Path(game) if game else unit_pack.find_game()
        wanted = {row["name"] for row in shader_report(bundle)}
        extract_donors(units_bundle_of(root), cache, wanted=wanted)
    return transplant(bundle, load_donors(cache),
                      deps=load_donor_deps(cache), out=out)
