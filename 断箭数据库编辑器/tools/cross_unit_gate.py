r"""跨单位回归尺子 CLI（薄封装）—— 判据逻辑在 `src/ba_mod_maker/asset_kernel/cross_unit_gate.py`。

## 用途

回答「换个模型就坏」里的 **B 类**：代码读着通用、只在某一个件上验证过的近似。
本工具**不碰 bpy** —— 采集端（无头 Blender 探针）自己跑，本工具只读那份 JSON 下判。

## 判据一行

    .venv\Scripts\python.exe tools\cross_unit_gate.py --selftest --json <读数字典>
      （默认 _work\audit-hardcode\cross-unit.json）
      -> 注入的红 5/5 报出 · 真件 RU_T72B 绿 · rc = 0

## 三步（Blender 一律由 shell 工具直接起，⛔ 不从 Python subprocess 起）

    .venv\Scripts\python.exe tools\cross_unit_gate.py --emit-probe
    .venv\Scripts\python.exe tools\cross_unit_gate.py --plan
    .venv\Scripts\python.exe tools\cross_unit_gate.py --check --table

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 自检全过 / `--check` 红件为 0 |
| 1 | 自检不过 / `--check` 有红件 |
| 2 | 用不了（参数不对 / 读不到 catalog / 别的东西抛了） |
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from ba_mod_maker.asset_kernel import cross_unit_gate as kernel  # noqa: E402

AUDIT_DIR = WORK / "audit-hardcode"
DEFAULT_JSON = AUDIT_DIR / "cross-unit.json"
PROBE_PATH = AUDIT_DIR / "probe_cross_unit.py"
BLENDER = r"D:\Blender\blender.exe"
GAME_DEFAULT = r"D:\Steam\steamapps\common\broken_arrow"

#: 这次的样本（覆盖：履带车 / 轮式 / 直升机 / 固定翼 / DLC 新代 shader）
SAMPLE: tuple[str, ...] = (
    "RU_T72B",
    "US_M1A2",
    "US_LAV25",
    "US_ACV",
    "US_AH-1W Super_Cobra",
    "US_AH-1Z Viper",
    "RU_KA52",
    "DLC2_XA180EST",
    "DLC3_MIG31",
)

#: 自检里当「已知绿件」用的那一个（今天的面是正常的）。
GREEN_UNIT = "RU_T72B"

_HASH_KEY = re.compile(r"^[0-9a-fA-F]{32}$")

#: `ModelPrefabs/` 下这些顶层目录是**零件**（步兵 / 武器 / 杂物 / 非玩家 / 僵尸），
#: 不是「别的单位」—— 车上的 M240L 机枪、飞行员本来就来自它们自己的 prefab。
#: ⛔ 实测（2026-10-17）：不过滤的话 US_LAV25 / US_M1A2 会被自己的机枪图判红。
NON_VEHICLE_DIRS = (
    "Infantry",
    "Infantry Weapons",
    "Weapon",
    "Common Weapon prefabs",
    "Misc",
    "NonPlayable",
    "Zombie",
)


def known_unit_keys() -> tuple[str, ...]:
    """catalog 里所有「像名字的」ModelPrefabs 地址键（R3 要用它判「别的单位」）。"""
    from ba_mod_maker.core.assets.catalog import AddressablesCatalog
    from ba_mod_maker.core.assets.game import aa_dir, find_game_root

    root = find_game_root()
    if root is None:
        return ()
    catalog = AddressablesCatalog.load(aa_dir(root))
    if catalog is None:
        return ()
    keys: list[str] = []
    for key in catalog.index:
        hits = catalog.lookups(key)
        if not hits:
            continue
        asset = hits[0][0].replace("\\", "/")
        if not (asset.endswith(".prefab") and "/ModelPrefabs/" in asset):
            continue
        if "/" in key or "\\" in key or _HASH_KEY.match(key):
            continue
        folder = asset.split("/ModelPrefabs/", 1)[1].rsplit("/", 1)[0]
        if folder.split("/", 1)[0] in NON_VEHICLE_DIRS:
            continue
        keys.append(key)
    return tuple(sorted(set(keys)))


def _sample_probe_command(unit: str, json_path: Path) -> str:
    return (f"& '{BLENDER}' --background --factory-startup --python "
            f"'{PROBE_PATH}' -- '{unit}' --out '{json_path}'")


def cmd_emit_probe(args: argparse.Namespace) -> int:
    out = Path(args.probe_out) if args.probe_out else PROBE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PROBE_SOURCE, encoding="utf-8")
    print(f"探针写好：{out}（{len(PROBE_SOURCE)} 字节）")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    units = [u for u in (args.units.split(",") if args.units else SAMPLE) if u]
    json_path = Path(args.json) if args.json else DEFAULT_JSON
    print("# 逐件串行（一次只开一件；units 锁由探针自己拿）")
    for unit in units:
        print(_sample_probe_command(unit, json_path))
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    keys = known_unit_keys()
    records: dict = {}
    json_path = Path(args.json) if args.json else DEFAULT_JSON
    if json_path.is_file():
        records = kernel.load_records(json_path)
    unit = args.selftest_unit or GREEN_UNIT
    good = {unit: records[unit]} if unit in records else None
    run = kernel.selftest(known_keys=keys, good_records=good)
    for row_id, title, line, ok in run.rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {row_id} {title}")
        print(f"        {line}")
    print(f"注入的红 {run.positive_ok}/{run.positive_total} 报出 · "
          f"真绿件 {run.negative_ok}/{run.negative_total} 绿 · "
          f"catalog 键 {len(keys)} 个 · 基线 {'真件' if good else '合成夹具'}")
    return run.rc


def cmd_check(args: argparse.Namespace) -> int:
    json_path = Path(args.json) if args.json else DEFAULT_JSON
    if not json_path.is_file():
        print(f"读不到 {json_path} —— 先跑 --emit-probe 再用 Blender 逐件采数", file=sys.stderr)
        return 2
    keys = known_unit_keys()
    records = kernel.load_records(json_path)
    verdicts = kernel.evaluate_all(records, known_keys=keys)
    for verdict in verdicts:
        print(verdict.line())
    reds = [v for v in verdicts if not v.ok]
    if args.table:
        print()
        print(kernel.markdown_table(verdicts))
    print()
    print(f"共 {len(verdicts)} 件 · 红 {len(reds)} 件 · 绿 {len(verdicts) - len(reds)} 件")
    print(f"catalog 键 {len(keys)} 个")
    for verdict in reds:
        codes = sorted({r.rule for r in verdict.reds})
        print(f"  红：{verdict.unit} -> {'、'.join(codes)}")
    return 1 if reds else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跨单位回归尺子（B 类：换个模型就坏）")
    parser.add_argument("--emit-probe", action="store_true", help="把无头 Blender 探针写到磁盘")
    parser.add_argument("--probe-out", default="", help="探针路径（默认 _work/audit-hardcode/）")
    parser.add_argument("--plan", action="store_true", help="打印逐件采集的 pwsh 命令")
    parser.add_argument("--units", default="", help="--plan 的件名（逗号分隔）")
    parser.add_argument("--selftest", action="store_true", help="正控 + 负控自检")
    parser.add_argument("--selftest-unit", default="", help="已知绿件名（默认 RU_T72B）")
    parser.add_argument("--check", action="store_true", help="判真件读数")
    parser.add_argument("--table", action="store_true", help="--check 时另印 Markdown 表")
    parser.add_argument("--json", default="", help="读数 JSON（默认见 --plan）")
    args = parser.parse_args(argv)
    if args.emit_probe:
        return cmd_emit_probe(args)
    if args.plan:
        return cmd_plan(args)
    if args.selftest:
        return cmd_selftest(args)
    if args.check:
        return cmd_check(args)
    parser.print_help()
    return 2


PROBE_SOURCE = "\n".join([
    "# -*- coding: utf-8 -*-",
    "# 跨单位回归尺子的采集端（无头 Blender）。由 tools/cross_unit_gate.py --emit-probe 生成。",
    "# ⛔ 别手改这一份 —— 改 tools/cross_unit_gate.py 里的 PROBE_SOURCE 再重新 emit。",
    "#",
    "# 用法（Blender 必须由 shell 工具**直接**起，⛔ 不许从 Python 的 subprocess 起）：",
    "#   & 'D:/Blender/blender.exe' --background --factory-startup --python <本文件> -- RU_T72B",
    "#",
    "# 三条规矩：",
    "#   1. units 包（3.4 GB ~ 10 GB 内存）必须走 tools/units_lock.py —— 本探针自己拿锁；",
    "#   2. 一次只开一件（起进程、读完、退出，内存不留）；",
    "#   3. 逐件把读数并进 _work/audit-hardcode/cross-unit.json（件名 -> 读数）。",
    "import json",
    "import os",
    "import sys",
    "import time",
    "",
    "ROOT = r'D:/断箭模组制作/BA_Mod_Maker_2.0'",
    "for _c in (ROOT, ROOT + '/src', ROOT + '/tools'):",
    "    if _c not in sys.path:",
    "        sys.path.insert(0, _c)",
    "",
    "import bpy",
    "import blender_addon",
    "from blender_addon import importer, rig_plan",
    "from units_lock import units_lock",
    "",
    "GAME = os.environ.get('BA_MOD_MAKER_GAME') or r'D:/Steam/steamapps/common/broken_arrow'",
    "OUT = ROOT + '/_work/audit-hardcode/cross-unit.json'",
    "",
    "argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []",
    "UNIT = argv[0] if argv else 'RU_T72B'",
    "if '--out' in argv:",
    "    OUT = argv[argv.index('--out') + 1]",
    "",
    "#: 抓 prefab 树（import_unit 内部只把树折出来自用，不返回）—— 这是读数的一半来源。",
    "captured = {}",
    "_orig_build_tree = importer.prefab_mod.build_tree",
    "",
    "",
    "def _spy(*args, **kwargs):",
    "    tree = _orig_build_tree(*args, **kwargs)",
    "    captured['tree'] = tree",
    "    captured['nodes'] = args[0] if args else []",
    "    return tree",
    "",
    "",
    "def _bbox(obj):",
    "    m = obj.matrix_world",
    "    pts = [m @ v.co for v in obj.data.vertices]",
    "    if not pts:",
    "        return [0.0, 0.0, 0.0]",
    "    lo = [min(p[k] for p in pts) for k in range(3)]",
    "    hi = [max(p[k] for p in pts) for k in range(3)]",
    "    return [round(hi[k] - lo[k], 6) for k in range(3)]",
    "",
    "",
    "def _walk(col):",
    "    for obj in col.objects:",
    "        yield obj",
    "    for child in col.children:",
    "        yield from _walk(child)",
    "",
    "",
    "def _record(rep, tree, seconds):",
    "    levels = [list(lv) for lv in tree.lods]",
    "    groups = []",
    "    for raw in captured.get('nodes') or []:",
    "        for comp in raw.get('components') or []:",
    "            if str(comp.get('kind')) != 'LODGroup':",
    "                continue",
    "            lv = [[int(c) for c in l] for l in (comp.get('lods') or [])]",
    "            groups.append({",
    "                'node': str(raw.get('name')),",
    "                'counts': [len(l) for l in lv],",
    "                'union': len({c for l in lv for c in l}),",
    "                'ids': sorted({c for l in lv for c in l}),",
    "            })",
    "    slots_of = {int(r.component_id): len(r.bone_ids) for r in tree.renderers}",
    "    obj_of_cid = {}",
    "    for name, meta in rep.mesh_of.items():",
    "        obj_of_cid[int(meta.get('component_id') or 0)] = name",
    "    rends = []",
    "    for r in tree.renderers:",
    "        cid = int(r.component_id)",
    "        rends.append({",
    "            'component_id': cid,",
    "            'name': str(r.name),",
    "            'kind': str(r.kind),",
    "            'node': str(tree.node(r.node_id).name) if r.node_id else '',",
    "            'mesh_id': int(r.mesh_id or 0),",
    "            'mesh_file': int(r.mesh_file or 0),",
    "            'slots': len(r.bone_ids),",
    "            'empty_slots': sum(1 for b in r.bone_ids if b is None),",
    "            'object': obj_of_cid.get(cid, ''),",
    "        })",
    "    col = bpy.data.collections.get(rep.collection_name)",
    "    lod_names = set()",
    "    if col is not None:",
    "        for child in col.children:",
    "            for o in child.objects:",
    "                lod_names.add(o.name)",
    "    objects = []",
    "    lo_all = [1e30, 1e30, 1e30]",
    "    hi_all = [-1e30, -1e30, -1e30]",
    "    if col is not None:",
    "        for obj in _walk(col):",
    "            entry = {",
    "                'name': obj.name,",
    "                'type': obj.type,",
    "                'in_lod_collection': obj.name in lod_names,",
    "                'hide_render': bool(obj.hide_render),",
    "                'parent': obj.parent.name if obj.parent else None,",
    "                'world_scale': [round(float(v), 6) for v in obj.matrix_world.decompose()[2]],",
    "                'local_scale': [round(float(v), 6) for v in obj.scale],",
    "                'chain': [],",
    "                'materials': [],",
    "                'dims': None,",
    "                'verts': 0,",
    "                'vgroups': 0,",
    "                'slots': 0,",
    "                'component_id': 0,",
    "                'armature_mod': any(m.type == 'ARMATURE' for m in obj.modifiers),",
    "            }",
    "            node = obj",
    "            chain = []",
    "            while node is not None:",
    "                sc = [round(float(v), 6) for v in node.scale]",
    "                chain.append({'name': node.name, 'scale': sc})",
    "                node = node.parent",
    "            entry['chain'] = chain",
    "            if obj.type == 'MESH':",
    "                entry['verts'] = len(obj.data.vertices)",
    "                entry['dims'] = _bbox(obj)",
    "                entry['vgroups'] = len(obj.vertex_groups)",
    "                entry['materials'] = [",
    "                    s.material.name if s.material else None for s in obj.material_slots",
    "                ]",
    "                meta = rep.mesh_of.get(obj.name) or {}",
    "                cid = int(meta.get('component_id') or 0)",
    "                entry['component_id'] = cid",
    "                entry['slots'] = slots_of.get(cid, 0)",
    "                m = obj.matrix_world",
    "                for v in obj.data.vertices:",
    "                    p = m @ v.co",
    "                    for k in range(3):",
    "                        lo_all[k] = min(lo_all[k], p[k])",
    "                        hi_all[k] = max(hi_all[k], p[k])",
    "            objects.append(entry)",
    "    if col is None:",
    "        unit_bbox = [0.0, 0.0, 0.0]",
    "    else:",
    "        unit_bbox = [round(hi_all[k] - lo_all[k], 6) for k in range(3)]",
    "    mats = []",
    "    for name in rep.material_names:",
    "        mat = bpy.data.materials.get(name)",
    "        if mat is None or not mat.use_nodes:",
    "            continue",
    "        rows = []",
    "        for node in mat.node_tree.nodes:",
    "            if node.type == 'TEX_IMAGE' and node.image:",
    "                rows.append({",
    "                    'name': node.image.name,",
    "                    'colorspace': node.image.colorspace_settings.name,",
    "                    'size': [int(node.image.size[0]), int(node.image.size[1])],",
    "                })",
    "        mats.append({'name': name, 'images': rows})",
    "    plan = rig_plan.plan_rig(tree, list(tree.renderers), unit_key=UNIT)",
    "    fields = ('nodes', 'non_renderer_objects', 'renderers', 'mesh_objects',",
    "              'placeholder_objects', 'materials', 'textures', 'lod_levels',",
    "              'lod0_members', 'lod_union', 'lod_outside', 'lod_hidden',",
    "              'bones', 'bones_deform', 'bones_matched', 'bones_unmatched',",
    "              'bone_empty_slots', 'armature', 'armature_rest_delta',",
    "              'rig_summary', 'rig_error', 'aim_summary', 'aim_rearward',",
    "              'aim_mounts', 'aim_issues', 'vt_map_bound', 'vt_map_note',",
    "              'material_runtime_count', 'collection_name', 'asset_path',",
    "              'bundle', 'bundle_path', 'unresolved', 'errors')",
    "    report = {}",
    "    for key in fields:",
    "        value = getattr(rep, key, None)",
    "        if isinstance(value, tuple):",
    "            value = list(value)",
    "        report[key] = value",
    "    return {",
    "        'unit_key': UNIT,",
    "        'seconds': round(float(seconds), 2),",
    "        'blender': bpy.app.version_string,",
    "        'report': report,",
    "        'lod': {'levels': levels, 'renderers': rends},",
    "        'lod_groups': groups,",
    "        'objects': objects,",
    "        'unit_bbox': unit_bbox,",
    "        'materials': mats,",
    "        'images': sorted(img.name for img in bpy.data.images),",
    "        'counts': {",
    "            'renderer_objects': len(rep.mesh_of),",
    "            'mesh_objects': sum(1 for o in objects if o['type'] == 'MESH'),",
    "            'collection_objects': len(objects),",
    "            'in_lod_collection': len(lod_names),",
    "        },",
    "        'rig': {",
    "            'slots': len(plan.slot_names),",
    "            'empty_slots': len(plan.empty_slots),",
    "            'issues': [str(x) for x in plan.issues],",
    "            'bones_total': len(plan.bones),",
    "        },",
    "    }",
    "",
    "",
    "def _merge(record, path):",
    "    doc = {}",
    "    if os.path.isfile(path):",
    "        try:",
    "            with open(path, encoding='utf-8') as fh:",
    "                doc = json.load(fh)",
    "        except Exception:",
    "            doc = {}",
    "    doc[record['unit_key']] = record",
    "    os.makedirs(os.path.dirname(path), exist_ok=True)",
    "    with open(path, 'w', encoding='utf-8') as fh:",
    "        json.dump(doc, fh, ensure_ascii=False, indent=1)",
    "    return len(doc)",
    "",
    "",
    "def main():",
    "    started = time.time()",
    "    blender_addon.register()",
    "    importer.prefab_mod.build_tree = _spy",
    "    try:",
    "        with units_lock('cross-unit-gate:' + UNIT):",
    "            rep = importer.import_unit(GAME, UNIT)",
    "    except Exception as exc:",
    "        record = {'unit_key': UNIT, 'seconds': round(time.time() - started, 2),",
    "                  'probe_error': '%s: %s' % (type(exc).__name__, exc)}",
    "        _merge(record, OUT)",
    "        print('CROSS_UNIT_JSON %s PROBE_ERROR %s' % (UNIT, record['probe_error']))",
    "        return 1",
    "    finally:",
    "        importer.prefab_mod.build_tree = _orig_build_tree",
    "    tree = captured.get('tree')",
    "    if tree is None:",
    "        print('CROSS_UNIT_JSON %s NO_TREE' % UNIT)",
    "        return 1",
    "    record = _record(rep, tree, time.time() - started)",
    "    total = _merge(record, OUT)",
    "    lod = record['lod']",
    "    union = len({int(c) for lv in lod['levels'] for c in lv})",
    "    print('CROSS_UNIT_JSON %s renderer=%d lod=%d union=%d outside=%d hidden=%d'",
    "          % (UNIT, record['report']['renderers'], len(lod['levels']), union,",
    "             record['report']['lod_outside'], record['report']['lod_hidden']))",
    "    print('    bbox=%s errs=%d total=%d'",
    "          % (record['unit_bbox'], len(record['report']['errors']), total))",
    "    return 0",
    "",
    "",
    "if __name__ == '__main__':",
    "    sys.exit(main())",
])


if __name__ == "__main__":
    sys.exit(main())


