"""全量资产审计（**只读**）：材质面 ＋ 跨包引用 ＋ cab 索引，给「接线」提供实测输入。

为什么要有这个工具（2026-10-17 实测）：豹2A8 的履带材质 `US_HeavyTrack` 与 LOD 材质 `LODs`
都在 **units 包**，而 prefab 在 **dlc_2 包**；PPtr 的 `m_FileID=3` -> `externals[2] =
archive:/CAB-1e53370f…/`，产品的 `unitypy_source._read()` 跨包抛 `FileNotFoundError` 并被吞成
`None` -> 插件拿到 `{'name': '<material …>', 'shader': '', 'texture_slots': ()}` -> 履带落到
Blender 默认白。全库实测：250 个 prefab / 824 条跨包材质引用 / 132 颗外部材质（读侧 2026-10-17
已改为按 externals 打开依赖包再读）。

三条子命令：

    python tools/asset_audit.py materials --out _work/audit-full/materials.jsonl
    python tools/asset_audit.py cabindex  [--out 文件]
    python tools/asset_audit.py summary   --in _work/audit-full/materials.jsonl

⛔ 一律 `tools/units_lock.py` 串行；一次只开一个包；不写游戏目录。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _c in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

#: 只留这些**颜色**（玻璃/无图材质的底色与 alpha 就在这里；`_BaseColor` 是 HDRP/Lit 的）。
WHITELIST_COLORS = (
    "_BaseColor",
    "_Color",
    "_UnlitColor",
    "_EmissiveColor",
    "_BaseColorMap_MipInfo",
    # 虹彩厚度重映射（HDRP 是 Vector，落在 m_Colors 里）：(min, max, 0, 0)
    "_IridescenceThicknessRemap",
)

#: 只留这些标量（接线/观感判据要用的），别把 100 多个 HDRP 默认值全存下来。
WHITELIST_FLOATS = (
    "_Metallic", "_Metal", "_Smoothness", "_AlphaCutoff", "_Cutoff",
    "_NormalScale", "_SurfaceType", "_BlendMode", "_CullMode", "_AORemapMin", "_AORemapMax",
    # ★ 2026-10-17 第二轮（「能接的都接上」）：透射 / IOR / 虹彩 / alpha remap
    "_Ior",
    "_TransmissionEnable",
    "_TransmissionMask",
    "_IridescenceThickness",
    "_IridescenceMask",
    "_AlphaRemapMin",
    "_AlphaRemapMax",
    "_EmissiveIntensity",
    "_CoatMask",
)


def aa_pc() -> str:
    from ba_mod_maker.core.assets.game import aa_dir, find_game_root

    root = find_game_root()
    if root is None:
        raise SystemExit("找不到游戏根目录（可用 BA_MOD_MAKER_GAME 覆盖）")
    return str(aa_dir(root) / "PC")


def _pptr(value: Any) -> tuple[int, int]:
    if not isinstance(value, dict):
        return (0, 0)
    try:
        return (int(value.get("m_FileID", 0) or 0), int(value.get("m_PathID", 0) or 0))
    except (TypeError, ValueError):
        return (0, 0)


def _entry_pair(entry: Any) -> tuple[str, Any]:
    """`m_TexEnvs` 等在 typetree 里是 {first, second}；老版本是二元组。"""
    if isinstance(entry, dict):
        return (str(entry.get("first") or ""), entry.get("second"))
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return (str(entry[0]), entry[1])
    return ("", None)


def _shader_name(tree: dict[str, Any]) -> str:
    parsed = tree.get("m_ParsedForm") or {}
    name = ""
    if isinstance(parsed, dict):
        name = str(parsed.get("m_Name") or "")
    if not name:
        name = str(tree.get("m_Name") or "")
    return name


def _material_record(
    obj: Any, bundle: str, shader_cache: dict[int, str], object_at: Any
) -> dict[str, Any]:
    tree = obj.read_typetree()
    props = tree.get("m_SavedProperties") or {}
    slots: list[list[Any]] = []
    for entry in props.get("m_TexEnvs") or []:
        slot, data = _entry_pair(entry)
        if not slot:
            continue
        if not isinstance(data, dict):
            slots.append([slot, 0, 0, False])
            continue
        file_id, path_id = _pptr(data.get("m_Texture"))
        slots.append([slot, file_id, path_id, bool(path_id)])
    floats: dict[str, float] = {}
    for entry in props.get("m_Floats") or []:
        key, value = _entry_pair(entry)
        if key in WHITELIST_FLOATS:
            try:
                floats[key] = round(float(value), 6)
            except (TypeError, ValueError):
                continue
    colors: dict[str, list[float]] = {}
    for entry in props.get("m_Colors") or []:
        key, value = _entry_pair(entry)
        if key not in WHITELIST_COLORS or not isinstance(value, dict):
            continue
        try:
            colors[key] = [
                round(float(value.get(ch, 0.0) or 0.0), 4) for ch in ("r", "g", "b", "a")
            ]
        except (TypeError, ValueError):
            continue
    stacks: list[list[str]] = []
    for entry in tree.get("m_BuildTextureStacks") or []:
        if not isinstance(entry, dict):
            continue
        group = str(entry.get("groupName") or "")
        item = str(entry.get("itemName") or "")
        if group and item:
            stacks.append([group, item])
    shader_file, shader_id = _pptr(tree.get("m_Shader"))
    shader = shader_cache.get(shader_id)
    if shader is None:
        shader = ""
        if shader_file == 0 and shader_id:
            try:
                shader_obj = object_at(shader_id)
                if shader_obj is not None:
                    shader = _shader_name(shader_obj.read_typetree())
            except Exception:
                shader = ""
        shader_cache[shader_id] = shader
    return {
        "bundle": bundle,
        "path_id": int(getattr(obj, "path_id", 0) or 0),
        "name": str(tree.get("m_Name") or ""),
        "shader": shader,
        "shader_pptr": [shader_file, shader_id],
        "slots": slots,
        "floats": floats,
        "colors": colors,
        "keywords": [str(k) for k in (tree.get("m_ValidKeywords") or [])],
        "stacks": stacks,
        "render_queue": int(tree.get("m_CustomRenderQueue") or 0),
    }


def scan_bundle(bundle: str, aa: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """扫一个包里所有 Material；返回 (逐颗读数, 该包元信息)。"""
    from ba_mod_maker.asset_kernel import unitypy_source

    path = os.path.join(aa, bundle)
    meta: dict[str, Any] = {"bundle": bundle, "bytes": os.path.getsize(path)}
    rows: list[dict[str, Any]] = []
    t0 = time.time()
    with unitypy_source.open_source(path) as source:
        env = source.env
        asset = env.assets[0] if getattr(env, "assets", None) else None
        meta["cab"] = str(getattr(asset, "name", "") or "")
        meta["externals"] = [
            str(getattr(e, "path", "") or e) for e in (getattr(asset, "externals", None) or ())
        ]
        shader_cache: dict[int, str] = {}
        for obj in env.objects:
            if obj.type.name != "Material":
                continue
            try:
                rows.append(_material_record(obj, bundle, shader_cache, source.object_at))
            except Exception as exc:  # 单颗坏掉不许废掉整包
                rows.append({
                    "bundle": bundle,
                    "path_id": int(getattr(obj, "path_id", 0) or 0),
                    "error": repr(exc),
                })
        meta["materials"] = len(rows)
    meta["seconds"] = round(time.time() - t0, 1)
    return rows, meta


def cmd_materials(args: argparse.Namespace) -> int:
    from units_lock import units_lock

    aa = aa_pc()
    bundles = sorted(f for f in os.listdir(aa) if f.endswith(".bundle"))
    if args.only:
        wanted = {b.strip() for b in args.only.split(",") if b.strip()}
        bundles = [b for b in bundles if b in wanted]
    done: set[str] = set()
    if args.resume and os.path.isfile(args.out):
        with open(args.out, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    done.add(json.loads(raw)["bundle"])
                except Exception:
                    continue
    todo = [b for b in bundles if b not in done]
    print(f"BUNDLES {len(bundles)}  TODO {len(todo)}", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with units_lock("asset_audit:materials"):
        for n, bundle in enumerate(todo, 1):
            try:
                rows, meta = scan_bundle(bundle, aa)
            except Exception as exc:
                rows, meta = [], {"bundle": bundle, "error": repr(exc)}
            with open(args.out, "a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + chr(10))
                fh.write(json.dumps({"__meta__": meta}, ensure_ascii=False) + chr(10))
                fh.flush()
                os.fsync(fh.fileno())
            print(
                f"[{n}/{len(todo)}] {bundle[:74]:<74} materials={meta.get('materials', 0):<6} "
                f"{meta.get('seconds', 0)}s",
                flush=True,
            )
    print("DONE", flush=True)
    return 0


def cmd_cabindex(args: argparse.Namespace) -> int:
    """建 cab -> 包 索引（跨包 PPtr 解析要用；只读容器目录，实测 0.1 s / 76 包）。"""
    from ba_mod_maker.asset_kernel import unitypy_source

    aa = aa_pc()
    t0 = time.time()
    index = unitypy_source.load_cab_index(aa, build=True)
    target = args.out or str(unitypy_source.cab_index_path(aa))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(
                {"stamp": unitypy_source.aa_stamp(aa), "cabs": index},
                fh,
                ensure_ascii=False,
                indent=0,
            )
    print(f"CABS {len(index)}  {time.time() - t0:.1f}s  WROTE {target}")
    return 0


def load_records(path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            if "__meta__" in rec:
                metas.append(rec["__meta__"])
            else:
                rows.append(rec)
    return rows, metas


def summarize(rows: list[dict[str, Any]], metas: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter, defaultdict

    errors = [r for r in rows if "error" in r]
    empty_name = [r for r in rows if "error" not in r and not r["name"]]
    no_shader = [r for r in rows if "error" not in r and not r["shader"]]
    by_shader: dict[str, dict[str, Any]] = {}
    slot_tex: dict[str, Counter] = defaultdict(Counter)
    slot_use: dict[str, Counter] = defaultdict(Counter)
    ext_tex = 0
    ext_shader = 0
    no_texture: list[dict[str, Any]] = []
    for r in rows:
        if "error" in r:
            continue
        shader = r["shader"] or "<unresolved>"
        rec = by_shader.setdefault(shader, {"materials": 0, "with_vt": 0, "no_texture": 0})
        rec["materials"] += 1
        if r["stacks"]:
            rec["with_vt"] += 1
        textured = 0
        for slot, file_id, _path_id, has_tex in r["slots"]:
            slot_use[shader][slot] += 1
            if has_tex:
                slot_tex[shader][slot] += 1
                textured += 1
                if file_id:
                    ext_tex += 1
        if r["shader_pptr"][0]:
            ext_shader += 1
        if not textured:
            rec["no_texture"] += 1
            no_texture.append({
                "bundle": r["bundle"],
                "path_id": r["path_id"],
                "name": r["name"],
                "shader": r["shader"],
            })
    per_shader = {}
    for shader, rec in sorted(by_shader.items(), key=lambda kv: -kv[1]["materials"]):
        per_shader[shader] = {
            "materials": rec["materials"],
            "with_vt_stack": rec["with_vt"],
            "no_textured_slot": rec["no_texture"],
            "slots_textured": dict(slot_tex[shader].most_common()),
            "slots_present": dict(slot_use[shader].most_common()),
        }
    return {
        "bundles": len(metas),
        "bundle_errors": [m for m in metas if "error" in m],
        "materials": len(rows),
        "material_errors": len(errors),
        "empty_name": len(empty_name),
        "unresolved_shader": len(no_shader),
        "external_shader_ref": ext_shader,
        "external_texture_ref": ext_tex,
        "materials_without_texture": len(no_texture),
        "per_shader": per_shader,
        "samples": {
            "empty_name": [
                {"bundle": r["bundle"], "path_id": r["path_id"]} for r in empty_name[:10]
            ],
            "unresolved_shader": [
                {"bundle": r["bundle"], "path_id": r["path_id"], "name": r["name"]}
                for r in no_shader[:10]
            ],
            "no_texture": no_texture[:20],
        },
    }


def cmd_summary(args: argparse.Namespace) -> int:
    rows, metas = load_records(args.infile)
    report = summarize(rows, metas)
    print(f"BUNDLES {report['bundles']} (错误 {len(report['bundle_errors'])})")
    print(
        f"MATERIALS {report['materials']}  读错 {report['material_errors']}  "
        f"空名字 {report['empty_name']}  shader解不出 {report['unresolved_shader']}"
    )
    print(
        f"跨包贴图引用 {report['external_texture_ref']}  "
        f"跨包 shader 引用 {report['external_shader_ref']}  "
        f"无带图槽的材质 {report['materials_without_texture']}"
    )
    print()
    print(f"{'shader':<42} {'materials':>8} {'VT栈':>8} {'无图':>8}")
    for shader, rec in report["per_shader"].items():
        print(
            f"{shader:<42} {rec['materials']:>8d} {rec['with_vt_stack']:>8d} "
            f"{rec['no_textured_slot']:>8d}"
        )
    print()
    print("按 shader 的「带图槽名」——接线的输入：")
    for shader, rec in report["per_shader"].items():
        print(f"  {shader}")
        for slot, count in rec["slots_textured"].items():
            print(f"     {slot:<28} {count}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print()
        print(f"WROTE {args.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="全量资产审计（只读）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_mat = sub.add_parser("materials", help="逐颗材质记 shader / 槽名 / 跨包引用")
    p_mat.add_argument("--out", required=True, help="JSONL 输出路径")
    p_mat.add_argument("--only", default="", help="只扫这些包（逗号分隔）")
    p_mat.add_argument("--resume", action="store_true", help="跳过 JSONL 里已扫过的包")
    p_mat.set_defaults(func=cmd_materials)
    p_cab = sub.add_parser("cabindex", help="cab 名 -> 包文件（跨包解析要用）")
    p_cab.add_argument("--out", default="", help="JSON 输出路径（默认写用户缓存）")
    p_cab.set_defaults(func=cmd_cabindex)
    p_sum = sub.add_parser("summary", help="按 shader / 槽名汇总")
    p_sum.add_argument("--in", dest="infile", required=True, help="materials 的 JSONL")
    p_sum.add_argument("--json", default="", help="汇总也写一份 JSON")
    p_sum.set_defaults(func=cmd_summary)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
