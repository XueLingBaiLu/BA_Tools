"""造包流水线（核心）：**桩化 → 造 job → 起 Unity → 体检 → 装机**。

为什么要有这一层：**造包归资产管理器** —— 编排就住在本文件（`断箭资产管理器/kernel/build_pipeline.py`），
界面只摆控件与日志，判定全在这里。
⛔ 2026-10-31：造包从数据侧**整体搬出**：数据侧 GUI 的造包页、CLI 的 pack 子命令、本模块全部撤掉。
本模块下面用到的 `ba_mod_maker.asset_kernel` / `core.assets.*` 是**共用底层库**，不是造包功能。

口径（都是 2026-10-25 ~ 10-27 真机踩出来的，别凭印象改）：

1. **桩化先行**：真件 prefab 里指向被移走 DLL 的组件要改指工程桩，否则 Unity 存 prefab 时
   `missing script` 直接拒（`SaveAsPrefabAsset` 返回 null）；依赖件（`Destroyed_*`）一并跟随。
2. **材质 shader 绑定在 `build_job` 里自动跑**（`shader_bind`）：挂在 AssetRipper 桩上的材质
   指回工程真件 ⇒ 包里那颗 `HDRP/Lit` 才是 8 Pass。
3. **体检 = 四把尺子**：整包 PPtr 普查 · 出厂闸 A0~A10 · 命名契约 L7 · 材质 shader 形状闸。
   有红就**不许**装机（`install_bundle` 入口自己也会再挡一次）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

__all__ = [
    "build_job", "check_bundle", "install_bundle", "node_contract_lines",
    "run_unity_and_report", "stub_repoint",
]

#: 造包默认落点（与 CLI 一致；GUI 里也允许手改）
DEFAULT_JOB_DIR = Path("_work") / "unitybuild" / "jobs"


def stub_repoint(unity: str | Path, prefab: str | Path, *, skip: bool = False) -> str:
    """把真件 prefab（连同它引用的工程内 prefab）里指向被移走 DLL 的组件改指到桩。

    返回一行读数。⛔ 主件认不出就抛（不半改）；依赖件认不出的只报不改。
    """
    from ba_mod_maker.core.assets import unity_stub_repoint as sr

    if skip:
        return "跳过（no-stub-repoint）"
    try:
        plan = sr.plan_prefab_tree(project_root=Path(unity), prefab=Path(unity) / Path(prefab))
    except sr.StubRepointError as exc:
        raise AssetError("桩化读不了： " + str(exc),
                         hint=str(getattr(exc, "hint", "") or "")) from exc
    main = plan['prefabs'][0]
    if main['unmatched']:
        names = []
        for item in main['unmatched']:
            cand = '/'.join(item.get('candidates') or ['(桩目录里没有同字段集的类)'])
            names.append(str(item.get('dll') or '?') + ' -> ' + cand)
        raise AssetError(
            "真件 prefab 有 " + str(len(main["unmatched"])) + " 个组件认不出该指哪个桩，拒绝半改",
            hint="先补桩类（同一程序集、同名类、字段集覆盖）再重跑；现场：" + "；".join(names),
        )
    dep_note = ""
    if plan['dependencies']:
        dep_note = ("·依赖件 " + str(len(plan['dependencies'])) + " 个"
                    + ("（⚠ 撞上限，没跟完）" if plan['truncated'] else ""))
    to_drop = int(plan.get('to_drop') or 0)
    if not plan['to_change'] and not to_drop:
        return "已全是桩（" + str(plan["already_stubbed"]) + " 处原样跳过）" + dep_note
    try:
        res = sr.apply_tree(plan)
    except sr.StubRepointError as exc:
        raise AssetError("桩化改不动： " + str(exc),
                         hint=str(getattr(exc, "hint", "") or "")) from exc
    classes = "、".join(sorted({c["stub_class"] for item in plan["prefabs"]
                                for c in item["components"]}))
    deps = [row for row in res['prefabs'] if row.get('kind') == 'dependency']
    note = (str(res["rewritten"]) + " 个组件改指到桩（" + classes + "）"
            + ("· 删掉 " + str(res.get("dropped") or 0) + " 个占位类组件" if res.get("dropped") else "")
            + "· 备份 " + str(res["backup_dir"]))
    if deps:
        note += (" · 其中依赖件 " + str(len(deps)) + " 个（"
                 + "、".join(Path(r["prefab"]).name for r in deps) + "）")
    if res['skipped']:
        note += (" · ⚠ 依赖件 " + str(len(res['skipped'])) + " 个有认不出的组件、整份没改")
    return note


def node_contract_lines(bundle: str | Path, asset_path: str | None, *,
                        unit_prefab: bool, turret_prefab: bool = False,
                        ) -> tuple[list[str], list[str]]:
    """L7 命名契约闸：读包里那条 prefab → 逐条判节点名（只读）。"""
    from ba_mod_maker.asset_kernel import node_contract
    from ba_mod_maker.asset_kernel import prefab as prefab_mod

    if not asset_path:
        return (["读不到 prefab：容器里没有地址（也没给 asset_path）⇒ 本档跳过"], [])
    stem = Path(asset_path).name.rsplit(".", 1)[0]
    try:
        tree = prefab_mod.read_prefab_tree(bundle, asset_path=asset_path)
    except Exception as exc:  # noqa: BLE001 -- 读不出来是读数，不是崩溃
        note = f"读不出 prefab {asset_path}（{type(exc).__name__}: {exc}）⇒ 本档跳过"
        return ([note], [note] if unit_prefab else [])
    report = node_contract.check_tree(
        tree, name=stem, expected_root_name=stem,
        scope="turret" if turret_prefab else "unit")
    return (list(report.lines()), list(report.problems()))


def build_job(from_dir: str | Path, *, base_unit: str = "", out_dir: str | Path,
              bundle_out_dir: str | Path | None = None,
              unity_project: str | Path | None = None, skip_stub_repoint: bool = False,
              create_missing: bool = False, create_parent: str = "",
              material_from: str = "", prefab_path: str = "", new_root: str = "",
              material_source: str = "",
              ) -> dict[str, Any]:
    """桩化 + 造 job（⛔ 不起 Unity；起 Unity 走 :func:`run_unity_and_report`）。"""
    from ba_mod_maker.core.assets import unity_prefab

    unity = unity_prefab.unity_project_path(unity_project)
    # ★ 2026-10-30：**制造**只有一个入口 ——
    #   · 给 base_unit / prefab_path ⇒ 在**已有**的 prefab 上造（= 改装，老路，一字不改）；
    #   · 都不给、只给 new_root ⇒ **从零**造（根 = new_root，节点全由 FBX 建）。
    _base = str(base_unit or "").strip()
    _prefab_arg = str(prefab_path or "").strip()
    _root = str(new_root or "").strip()
    if _base or _prefab_arg:
        prefab = _prefab_arg or unity_prefab.find_prefab(unity, _base)
        note = stub_repoint(unity, prefab, skip=skip_stub_repoint)
        bundle_name = Path(prefab).stem.lower()
    elif _root:
        prefab = ""
        note = "从零制造：没有底包 prefab，跳过硬编码重指"
        bundle_name = _root.lower()
    else:
        raise unity_prefab.UnityJobError(
            "既没给 base_unit / prefab_path、也没给 new_root —— 不知道要造什么")
    # 行为类桩覆盖检查不在这里：它在 unity_prefab.build_job（CLI / 导入器 / 管理器共用的那一层）。
    res = unity_prefab.build_job(
        from_dir, prefab_path=prefab, out_dir=out_dir,
        bundle_name=bundle_name, unity_project=unity,
        new_root=_root,
        bundle_out_dir=bundle_out_dir,
        create_missing=create_missing, create_parent=create_parent,
        material_from=material_from,
        # ★ 2026-11-01：从零造时的材质来源（同单位真件 prefab 资产路径）——
        #   不给的话渲染器留着 FBX 的空材质，真机就是「白模」（实测绑定贴图 0 张）。
        material_source=material_source,
    )
    res["stub_note"] = note
    res["unity"] = str(unity)
    res["command"] = unity_prefab.unity_command(unity, res["job"])
    return res


def contract_diff(root: str, unity_project: str, structure_json: str) -> dict[str, Any]:
    """★ 2026-11-02：结构契约自检 = **structure.json vs 我们刚造的 prefab**。

    ⛔ 不读真件 prefab（用户裁定：造包链一个游戏资产字节都不许读）。
    参照物 = 提取器导出的 `structure.json`（节点路径 / 本地 TRS / 组件数）＋ 我们自己的必填引用自检。
    失败**不抛**（构建已经成功，自检只留痕）。
    """
    import glob, json, os, re
    try:
        base = Path(unity_project) / 'Assets' / '_unitbuild'
        cands = sorted(glob.glob(str(base / '*' / (root + '.prefab'))), key=os.path.getmtime)
        if not cands:
            return {'ok': None, 'note': '没有找到刚落盘的 prefab（%s）' % base}
        mine = cands[-1]
        st = json.loads(Path(structure_json).read_text(encoding='utf-8'))
        text = Path(mine).read_text(encoding='utf-8', errors='ignore')
        go_name, tr, comps = {}, {}, {}
        for m in re.finditer(r'--- !u!(\d+) &(\d+)\r?\n(.*?)(?=\r?\n--- |\Z)', text, re.S):
            cls, fid, body = m.group(1), m.group(2), m.group(3)
            if cls == '1':
                nm = re.search(r'm_Name: ([^\r\n]+)', body)
                go_name[fid] = (nm.group(1).strip() if nm else '')
                comps.setdefault(go_name[fid], 0)
            elif cls == '4':
                go = re.search(r'm_GameObject: \{fileID: (\d+)\}', body)
                fa = re.search(r'm_Father: \{fileID: (\d+)\}', body)
                def v(k, body=body):
                    mm = re.search(k + r': \{x: ([^,}\r\n]+), y: ([^,}\r\n]+), z: ([^,}\r\n]+)(?:, w: ([^}\r\n]+))?\}', body)
                    return [float(x) for x in mm.groups() if x is not None] if mm else None
                if go:
                    tr[fid] = {'go': go.group(1), 'father': fa.group(1) if fa else None,
                               'pos': v('m_LocalPosition'), 'rot': v('m_LocalRotation'), 'scl': v('m_LocalScale')}
            else:
                go = re.search(r'm_GameObject: \{fileID: (\d+)\}', body)
                if go:
                    nm = go_name.get(go.group(1), '')
                    if nm:
                        comps[nm] = comps.get(nm, 0) + 1
        def path_of(fid, cache):
            if fid in cache:
                return cache[fid]
            t = tr.get(fid)
            if not t:
                return ''
            nm = go_name.get(t['go'], '')
            fa = t.get('father')
            p = (path_of(fa, cache) + '/' + nm).strip('/') if fa and fa in tr else nm
            cache[fid] = p
            return p
        cache, mine_by_path = {}, {}
        for fid, t in tr.items():
            mine_by_path[path_of(fid, cache)] = t
        nodes = st.get('nodes') or []
        ref_paths = {n['path'] for n in nodes if n.get('path')}
        missing = sorted(p for p in ref_paths if p not in mine_by_path)
        extra = sorted(p for p in mine_by_path if p and p not in ref_paths)
        lines = ['① 节点路径: 参照 %d · 我们 %d · 缺 %d · 多 %d'
                 % (len(ref_paths), len(mine_by_path), len(missing), len(extra))]
        bad = 0
        if missing:
            bad += len(missing)
            lines.append('   缺: ' + ', '.join(missing[:6]))
        if extra:
            lines.append('   多（我们自己加的件，允许）: ' + ', '.join(extra[:6]))
        trs_bad = 0
        for n in nodes:
            t = mine_by_path.get(n.get('path'))
            a, b = (t or {}).get('pos'), n.get('pos')
            if not a or not b:
                continue
            if max(abs(x - y) for x, y in zip(a, b)) > 0.02:
                trs_bad += 1
        lines.append('② 本地 TRS: 超差 %d 个节点（阈值 0.02）' % trs_bad)
        bad += trs_bad
        # 只比我们**确实要复制**的组件：渲染器/LODGroup/Transform 是我们自己的（刻意不抄）
        SKIP = {'SkinnedMeshRenderer', 'MeshRenderer', 'MeshFilter', 'LODGroup',
                'Transform', 'GameObject'}
        comp_bad = 0
        for n in nodes:
            want_list = [c for c in (n.get('components') or []) if c not in SKIP]
            nm = n.get('name')
            if not nm or not want_list:
                continue
            if comps.get(nm, 0) < len(want_list):
                comp_bad += 1
        lines.append('③ 组件: 少于 structure 声明的节点 %d 个' % comp_bad)
        bad += comp_bad
        ok_ctrl = not re.search(r'm_Controller: \{fileID: 0\}', text)
        ok_cont = re.search(r'_container:\r?\n\s+_root: \{fileID: 0\}', text) is None
        lines.append('④ 自检: Animator.controller=%s · _container._root/_body=%s'
                     % ('set' if ok_ctrl else 'NULL', 'set' if ok_cont else 'NULL'))
        if not ok_ctrl:
            bad += 1
        if not ok_cont:
            bad += 1
        lines.append('结果: ' + ('全绿' if bad == 0 else '有差异 %d 处' % bad))
        return {'ok': bad == 0, 'rc': 0 if bad == 0 else 1, 'prefab': mine, 'lines': lines}
    except Exception as exc:  # noqa: BLE001 — 自检绝不能把构建搞崩
        return {'ok': None, 'note': 'contract_diff 异常: %r' % (exc,)}


def run_unity_and_report(res: dict[str, Any], *, unity: str | Path | None = None,
                         timeout_s: float | None = None) -> dict[str, Any]:
    """起 Unity 等它退，读 `report.json`，按 `AssetFileHash` 归一包名。**不判红绿**。"""
    from ba_mod_maker.core.assets import unity_build

    project = Path(unity or res["unity"])
    outcome = unity_build.run(
        res["job"], project=project,
        timeout_s=float(timeout_s or unity_build.DEFAULT_TIMEOUT_S))
    report = unity_build.read_report(res["report"])
    bundle = ""
    if report.get("ok") and report.get("bundle"):
        norm = unity_build.normalise_bundle(report["bundle"])
        report["bundle"] = norm["path"]
        bundle = norm["path"]
    # ★ 2026-11-02：结构契约自检 = structure.json vs 我们造的 prefab（⛔ 不再读真件 prefab）
    contract = None
    job = res.get("job")
    try:
        import json as _json
        if isinstance(job, str):
            job = _json.loads(Path(job).read_text(encoding='utf-8'))
        if isinstance(job, dict) and job.get("root"):
            fbx = job.get("fbx_path") or ""
            st_json = str(Path(fbx).parent / "structure.json") if fbx else ""
            if st_json and Path(st_json).is_file():
                contract = contract_diff(job["root"], str(project), st_json)
            else:
                contract = {"ok": None, "note": "没有 structure.json，跳过契约自检（不拿真件兜底）"}
    except Exception as exc:  # noqa: BLE001
        contract = {"ok": None, "note": "自检跳过: %r" % (exc,)}
    return {"outcome": outcome, "report": report, "bundle": bundle, "contract": contract,
            "lines": list(unity_build.report_lines(report))}


def check_bundle(bundle: str | Path, *, asset_path: str | None = None,
                 unit_prefab: bool = True, turret_prefab: bool = False,
                 ) -> dict[str, Any]:
    """四把尺子叠起来体检一个包（**只读**）；返回 `{ok, lines, problems, facts}`。"""
    from ba_mod_maker.asset_kernel import unit_bundle
    from ba_mod_maker.core.asset import new_asset
    from ba_mod_maker.core.assets import unit_pack

    src = Path(bundle)
    lines: list[str] = []
    problems: list[str] = []
    drift = unit_pack.schema_drift(src)
    lines.append("⓪ 类型表：" + (f"{drift['types']} 项逐字节一致" if drift["ok"]
                                 else f"{len(drift['drift'])} 项与内置不同"))
    if not drift["ok"]:
        problems.append("类型表漂移：与内置（= 当前游戏版本）逐字节不同")
    audit = unit_bundle.audit_package_pptrs(src)
    lines.extend("① " + row for row in audit.lines())
    problems += [f"悬空 PPtr：{row}" for row in audit.dangling]
    gate = unit_bundle.verify_unit_bundle(
        src, asset_path=asset_path, require_animations=unit_prefab,
        turret_prefab=turret_prefab)
    lines.extend("② " + row for row in gate.lines())
    for item in gate.problems:
        if unit_prefab or turret_prefab or item.startswith(
                ("MonoBehaviour", "容器", "材质槽少于子网格", "AnimationHub")):
            problems.append(item)
    contract_lines, contract_problems = node_contract_lines(
        src, asset_path or (gate.container[0][0] if gate.container else None),
        unit_prefab=unit_prefab, turret_prefab=turret_prefab)
    lines.extend("③ " + row for row in contract_lines)
    problems += contract_problems
    shape = unit_bundle.audit_shader_shapes(src)
    lines.extend("⑤ " + row for row in shape.lines())
    problems += list(shape.problems)
    facts, _data, serialized = new_asset.bundle_facts_of_file(src)
    lines.append(f"④ 包名 {facts['bundle_name']} · m_Hash={facts['m_hash']}"
                 f" · {facts['bundle_size']:,} B")
    return {"ok": not problems, "lines": lines, "problems": problems,
            "facts": facts, "bundle": str(src), "serialized": serialized}


def install_bundle(*, game: str | Path, project_root: str | Path, bundle: str | Path,
                   unit: str, key: str, asset_path: str,
                   dependency_source: str | None = None,
                   row_id: int | None = None,
                   run_check: bool = True) -> dict[str, Any]:
    """装机（走 `unit_pack.install_pack`）。`run_check` 为真时**先体检**，有红就拒。"""
    from ba_mod_maker.core.assets import unit_pack

    if run_check:
        verdict = check_bundle(bundle, asset_path=asset_path, unit_prefab=True)
        if not verdict["ok"]:
            raise AssetError("体检有红，拒绝装机（" + str(len(verdict["problems"])) + " 条）",
                             hint="；".join(verdict["problems"][:4]))
    return unit_pack.install_pack(
        game=Path(game), project_root=Path(project_root), bundle=Path(bundle),
        unit=unit, key=key, asset_path=asset_path,
        dependency_source=dependency_source, row_id=row_id)
