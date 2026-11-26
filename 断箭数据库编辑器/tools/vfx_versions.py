r"""F2 线的唯一尺子：读 bundle 里每个 VisualEffectAsset 的版本契约 + 图编没编出来。

## 为什么有这个小工具（2026-10-12，F2「自己加特效」的瓶颈）

VisualEffectAsset 是**已编译字节码**，不是节点图。编辑器里那张图没编出来时，
打进包的资产长这样：m_RuntimeVersion=0 / m_CompilationVersion=0 / m_Systems=0。
实测根因**不是**版本号：工程跑在 **Built-in 管线**里时
VFXLibrary.currentSRPBinder == null ⇒ VFXGraphCompiledData.Compile() 第一行就
CleanRuntimeData(); return; ⇒ 资产永远是 0/0/0（旧探针工程三版实测都这样）。
工程里挂上 **HDRP**（VFXHDRPBinder）之后，同一张图同一个包：**35 / 7 / systems 2**。

## ⚠ 2026-10-16 更新（L4 实测，Unity 6 / VFX Graph 17.3.0；读数见 `_work/l4/读数.md`）

1. **契约是 39 / 7**：1.2.1 原包 313/313 都是 39；**35 只对上一代副本成立**
   （`_work/effects/vfx.bundle`，2022.3 + HDRP 14）⇒ 尺子的 `RUNTIME_EXPECTED` 已改成 39，
   而 `tests/fixtures/vfx/T4_hdrp`（35/7）**保留为「旧契约必须红」的那把负控**。
2. **杠杆仍然是 `currentSRPBinder`**（= 工程挂 HDRP），⛔ 不是反射 Compile：
   17.3 的实际签名是
   `Compile(VFXCompilationMode, bool enableShaderDebugSymbols, VFXAnalytics analytics)`
   —— 第 3 个是**类**（传 `null` 通、传 `false` 抛 `ArgumentException`）；
   Built-in 工程里调它 **0 ms 返回、包体一字节不变**（三条负控都是 0/0/0）。
3. **图对象在 17.3 不在子资产里**：`LoadAllAssetsAtPath(.vfx)` 看不到 `VFXGraph`，
   要 `Resources.FindObjectsOfTypeAll(typeof(UnityEditor.VFX.VFXGraph))` 才拿得到。

## 判据（两条，别混）

1. **版本契约**（全语料不变量）：每个资产的 m_RuntimeVersion == 39 且
   m_CompilationVersion == 7；**1.2.1 游戏原包 313/313** 都满足。任一不满足 ⇒ 红。
   ⚠ 契约随引擎走：1.2.0（Unity 2022.3）是 **35/7**，1.2.1（Unity 6000.3.24f1）是 **39/7**。
2. **图编出来了没有**（单资产）：m_Systems > 0。
   ⚠ 实测游戏原包自己也有 **1/313** 个 systems=0（Fake_VFX_Flash，名字就叫 Fake，
   是个空图）—— 所以 systems=0 **只在整包全为 0 时**判红，个别 0 只列出来提示。

判据一行（本工具）::

    .venv\Scripts\python.exe tools/vfx_versions.py <bundle> [...]
      -> 版本契约全过 且 至少一个资产 systems>0 ⇒ VERDICT OK（exit 0）
         否则 VERDICT FAIL（exit 1），并逐条点名是哪一项红了

正控（这把尺子必须读得出真值）::

    .venv\Scripts\python.exe tools/vfx_versions.py <游戏 vfx_assets_all_e7ec6789*.bundle>   # 1.2.1
      -> VisualEffectAsset 313 个 · m_RuntimeVersion {39: 313}
         · m_CompilationVersion {7: 313} · 312 个 systems>0 · VERDICT OK
    # 自证（能红的那一半）：上一代副本 35/7 ⇒ FAIL「runtime=35(应 39)」
    .venv\Scripts\python.exe tools/vfx_versions.py _work/effects/vfx.bundle

负控（必须能红）::

    .venv\Scripts\python.exe tools/vfx_versions.py <Built-in 管线工程打的包>
      -> runtime 0 / compile 0 / systems 0 ⇒ VERDICT FAIL（exit 1）
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

#: 现行契约 = **39**（《断箭》1.2.1 / Unity 6000.3.24f1）。
#: ⚠ **35 只对上一代副本成立**（`_work/effects/vfx.bundle` 69,989,457 B，2022.3 + HDRP 14）——
#: 见模块头「2026-10-16 更新」，别再把 35 当现行值。
RUNTIME_EXPECTED = 39
COMPILE_EXPECTED = 7
HEAD = ("path_id", "name", "runtime", "compile", "exposed", "systems", "exprs")


def read_assets(path: str):
    """返回 [(path_id, name, runtime, compile, exposed, systems, exprs)]。"""
    import UnityPy

    env = UnityPy.load(path)
    rows = []
    for o in env.objects:
        if o.type.name != "VisualEffectAsset":
            continue
        try:
            tt = o.read_typetree()
        except Exception as exc:  # 读不出来就点名，别装作没这条
            rows.append((o.path_id, f"READ-ERROR:{type(exc).__name__}",
                         None, None, None, None, None))
            continue
        inf = tt.get("m_Infos") or {}
        rows.append((
            o.path_id,
            tt.get("m_Name"),
            inf.get("m_RuntimeVersion"),
            inf.get("m_CompilationVersion"),
            len(inf.get("m_ExposedExpressions") or []),
            len(tt.get("m_Systems") or []),
            len((inf.get("m_Expressions") or {}).get("m_Expressions") or []),
        ))
    return rows


def judge(rows):
    """返回 (ok, problems, notes)。problems 严重到判红，notes 只提示。"""
    if not rows:
        return False, ["包里一个 VisualEffectAsset 都没有"], []

    problems = []
    notes = []
    zero = []
    for _pid, name, rt, cv, _ex, syss, _expr in rows:
        bad = []
        if rt != RUNTIME_EXPECTED:
            bad.append(f"runtime={rt}(应 {RUNTIME_EXPECTED})")
        if cv != COMPILE_EXPECTED:
            bad.append(f"compile={cv}(应 {COMPILE_EXPECTED})")
        if bad:
            problems.append(f"{name}: " + " · ".join(bad))
        if not syss:
            zero.append(str(name))

    if len(zero) == len(rows):
        problems.append(f"整包 {len(rows)} 个资产 systems 全为 0 ⇒ 图根本没编出来")
    elif zero:
        head = "、".join(zero[:6]) + ("…" if len(zero) > 6 else "")
        notes.append(f"{len(zero)}/{len(rows)} 个资产 systems=0（对得上"
                     f"游戏原包那份也有 1 个 Fake_VFX_Flash 空图）: {head}")
    return (not problems), problems, notes


def _row(cells):
    a, b, c, d, e, f, g = cells
    return (f"{str(a)[:24]:<24} {str(b)[:30]:<30} {str(c):>8} {str(d):>8} "
            f"{str(e):>8} {str(f):>8} {str(g):>8}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="读 bundle 里 VisualEffectAsset 的 39/7/systems")
    ap.add_argument("bundles", nargs="+", help="AssetBundle 路径（可多个）")
    ap.add_argument("--json", action="store_true", help="额外输出一行 JSON（给机器读）")
    ap.add_argument("--max-rows", type=int, default=10, help="每个包逐条印前 N 行")
    args = ap.parse_args(argv)

    all_ok = True
    payload = []
    for path in args.bundles:
        if not os.path.exists(path):
            print(f"!! 文件不存在: {path}")
            all_ok = False
            payload.append({"bundle": path, "error": "missing"})
            continue
        try:
            rows = read_assets(path)
        except Exception as exc:
            print(f"!! 打不开 {path}: {type(exc).__name__}: {exc}")
            all_ok = False
            payload.append({"bundle": path, "error": f"{type(exc).__name__}: {exc}"})
            continue

        ok, problems, notes = judge(rows)
        all_ok = all_ok and ok
        rv = collections.Counter(r[2] for r in rows)
        cv = collections.Counter(r[3] for r in rows)
        sh = collections.Counter(r[5] for r in rows)
        pos = sum(v for k, v in sh.items() if k)

        print("=" * 90)
        print(f"{os.path.basename(path)}  {os.path.getsize(path)} bytes  "
              f"VisualEffectAsset: {len(rows)}")
        print(_row(HEAD))
        for r in rows[: args.max_rows]:
            print(_row(r))
        if len(rows) > args.max_rows:
            print(f"   …（共 {len(rows)} 条，只印前 {args.max_rows} 条）")
        print(f"  直方图 m_RuntimeVersion: {dict(rv)}   "
              f"m_CompilationVersion: {dict(cv)}   "
              f"systems>0 的资产: {pos}/{len(rows)}")
        for n in notes:
            print(f"  注意: {n}")
        tail = "" if ok else "  <- " + " ；".join(problems[:6])
        print(f"  VERDICT: {'OK' if ok else 'FAIL'}{tail}")
        payload.append({
            "bundle": path, "count": len(rows), "ok": ok, "problems": problems,
            "notes": notes, "with_systems": pos,
            "runtime_hist": {str(k): v for k, v in rv.items()},
            "compile_hist": {str(k): v for k, v in cv.items()},
            "systems_hist": {str(k): v for k, v in sh.items()},
        })

    if args.json:
        print("JSON " + json.dumps(payload, ensure_ascii=False))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
