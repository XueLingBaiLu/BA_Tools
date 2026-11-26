r"""**可达性闸门**：一次材质改动，到底会不会出现在屏幕上？

判据一行
-------
    .venv\Scripts\python.exe tools\reach_gate.py --selftest
      -> 正控 v2（改**既有**材质的槽）绿 · 负控 v1（新材质挂到第 2 个材质位）红  =>  **rc=0**

## 由来（2026-10-10 真机翻车 + **同一晚两次自我更正**）

v1 包结构全绿（check-model 五跳全绿 · 悬空 PPtr 0 · Unity 探针两行都在），
用户装机起游戏后**车身一点没变**：车身 3 个 SMR 仍用原版 \`CR-9037_BASE_MAT\`，
新材质被加到另一支渲染器的**第 2 个材质位**。

**第一版尺子把"可达"定义成"在根的 Renderers 列表里" ⇒ v1 也判绿 ⇒ 负控当场打回。**
正确的定义要加一条 **Unity 硬规则**：
**一个渲染器只有前 N 个材质会被用，N = 该网格的子网格数；多出来的材质位被忽略。**

## 机械定义

**"会出现在屏幕上" = 存在渲染器 R ∈ 根.Renderers：**
1. \`R.m_Enabled == 1\`；且
2. 存在下标 i：**i < 子网格数(R.m_Mesh)** 且 \`R.m_Materials[i]\` 的该槽指向目标贴图。
   * 子网格数**读不到**（跨包/缺类型树）时**按 1 处理** —— **最保守**，宁可判红。

⛔ **本闸门不含 LOD 判断**（"这一级 LOD 是不是当前显示的那级"要 LODGroup 阈值 + 相机距离）——
那一层仍是空洞，**不假装做了**。
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

from ba_mod_maker.asset_kernel.unitypy_source import open_source  # noqa: E402

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

ROOT_PID_HINT = 8037933264019199777


def _plist(v):
    if isinstance(v, dict):
        d = v.get("Data")
        return d if isinstance(d, list) else []
    return v if isinstance(v, list) else []


def _pid(p) -> int:
    return int((p or {}).get("m_PathID", 0) or 0) if isinstance(p, dict) else 0


def survey(bundle: Path) -> dict:
    with open_source(bundle) as src:
        root = None
        for p in src.object_ids("MonoBehaviour"):
            try:
                t = src.read_typetree(p)
            except Exception:
                continue
            if {"PrefabRoot", "Renderers", "Skins"} <= set(t.keys()):
                root = (p, t)
                if p == ROOT_PID_HINT:
                    break
        if root is None:
            raise SystemExit(f"{bundle.name}: 找不到 UnitPrefabRoot")
        mat_name, slots = {}, {}
        for p in src.object_ids("Material"):
            try:
                t = src.read_typetree(p)
            except Exception:
                continue
            mat_name[p] = str(t.get("m_Name") or "")
            got = {}
            for e in (t.get("m_SavedProperties") or {}).get("m_TexEnvs") or ():
                k = e[0] if isinstance(e, (list, tuple)) else None
                v = e[1] if isinstance(e, (list, tuple)) and len(e) > 1 else {}
                tp = _pid(v.get("m_Texture")) if isinstance(v, dict) else 0
                nm = ""
                if tp:
                    try:
                        nm = str(src.read_typetree(tp).get("m_Name") or "")
                    except Exception:
                        nm = f"<{tp}>"
                got[k] = nm
            slots[p] = got
        rows = []
        for rp in [_pid(x) for x in _plist(root[1].get("Renderers"))]:
            try:
                rt = src.read_typetree(rp)
            except Exception:
                continue
            mp = _pid(rt.get("m_Mesh"))
            n_sub = None
            if mp:
                try:
                    n_sub = len(src.read_typetree(mp).get("m_SubMeshes") or [])
                except Exception:
                    n_sub = None
            mats = [_pid(m) for m in (rt.get("m_Materials") or [])]
            rows.append({"r": rp, "enabled": rt.get("m_Enabled"), "n_sub": n_sub,
                         "mats": [mat_name.get(m, f"<{m}>") for m in mats],
                         "slots": [slots.get(m, {}) for m in mats]})
    return {"root": root[0], "rows": rows}


def judge(bundle: Path, *, slot: str, want: str) -> dict:
    s = survey(bundle)
    hits, ignored = [], []
    for row in s["rows"]:
        if not row["enabled"]:
            continue
        usable = row["n_sub"] if isinstance(row["n_sub"], int) and row["n_sub"] > 0 else 1
        for i, sl in enumerate(row["slots"]):
            if sl.get(slot) != want:
                continue
            rec = {"r": row["r"], "i": i, "usable": usable,
                   "mat": row["mats"][i] if i < len(row["mats"]) else ""}
            (hits if i < usable else ignored).append(rec)
    return {"bundle": bundle.name, "root": s["root"], "renderers": len(s["rows"]),
            "hits": hits, "ignored": ignored, "ok": bool(hits)}


def _report(tag: str, r: dict) -> None:
    print(f"  {tag}: 根 {r['root']} · 列表渲染器 {r['renderers']} · "
          f"**会被用**的命中 {len(r['hits'])} · 会被忽略的命中 {len(r['ignored'])}")
    for h in r["hits"]:
        print(f"       OK r={h['r']} 位[{h['i']}]<子网格{h['usable']} {h['mat']!r}")
    for h in r["ignored"]:
        print(f"       NG r={h['r']} 位[{h['i']}]>=可用{h['usable']} {h['mat']!r}"
              " —— Unity 忽略这一位")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="可达性闸门：这次材质改动会不会出现在屏幕上？")
    ap.add_argument("--bundle")
    ap.add_argument("--slot", default="Layer_A97CDC25")
    ap.add_argument("--tex")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        base = WORK
        want = "wreck_wreek_BaseMap"
        r2 = judge(base / "tierC/out/cr9037p3mat01_assets_all_1b00114318014f85.bundle",
                   slot=a.slot, want=want)
        r1 = judge(base / "p3mat/out/cr9037p3mat01_assets_all_1b00114318014f85.bundle",
                   slot=a.slot, want=want)
        _report("正控 v2（改既有材质的槽）", r2)
        _report("负控 v1（新材质挂到第 2 个材质位）", r1)
        ok = r2["ok"] and not r1["ok"]
        print(f"\n可达性闸门：正控绿={r2['ok']} · 负控红={not r1['ok']} => "
              f"{'尺子成立' if ok else '尺子不成立'} rc={0 if ok else 1}")
        return 0 if ok else 1
    if not a.bundle or not a.tex:
        ap.error("要么 --selftest，要么 --bundle + --tex")
    r = judge(Path(a.bundle), slot=a.slot, want=a.tex)
    _report(a.bundle, r)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
