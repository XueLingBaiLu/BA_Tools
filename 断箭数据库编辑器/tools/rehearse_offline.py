r"""P6-2 · **端到端离线排练**：假资产 -> 产品 build-model -> check-model 全绿。

判据一行
-------
    .venv\Scripts\python.exe tools\rehearse_offline.py
      -> 末行「build-model rc=0 · check-model rc=0 · 红 0 条 · 产物 <路径>」且 rc=0

做三件事（**全程只读游戏目录**，产物落 _work/p6/rehearse/）：
  ① 造一个**假资产**（自写的六棱柱 OBJ，12 顶点 / 8 个四边形面）—— 不是任何游戏件；
  ② 产品 \`build-model\`：借一个**真模板包**的通道布局 + 材质闭包，造出模型包；
  ③ 产品 \`check-model\`：出厂闸 A0~A5 + 整包 PPtr 普查 + catalog 四件套，必须 **零红**。

⛔ 本文件不重造任何实现：建模走 \`ba_mod_maker.cli build-model\`，判定走 \`... check-model\`。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
TEMPLATE = "ammoprefabs_assets_all_1db6cf2c07a5d1ce9b184aa7b89f23a2.bundle"
#: 必须是**非流式**网格（m_DataSize 非空）**且名字全局唯一** —— ammoprefabs 里 86 个 Mesh：
#: 非流式只有 7 个（Main_lp×2 / Body×2 / Body_LP / Main / rocket1），其中名字唯一的只有
#: `Main`（985 顶点）与 `rocket1`（3276 顶点）。这条是实测出来的，不是猜的：
#: 判据 = `_work/p6/recon3.py`（逐 Mesh 读 m_DataSize 与 m_VertexCount，按名字计数）
MESH_TEMPLATE = "Main"
MATERIAL = "ZB500ASM"
KEY = "NightRehearsal"
OUT = WORK / "p6" / "rehearse"


def fake_obj(path: Path, *, radius: float = 1.75, height: float = 3.0) -> None:
    """一个六棱柱：12 顶点 / 8 个四边形面（侧面 6 + 上下底 6 三角化的四边形）。

    刻意与游戏里任何一个件都不同形 —— 它只用来证明**产线能跑**，不是资产。
    """
    import math

    lo = [(radius * math.cos(math.pi / 3 * i), 0.0, radius * math.sin(math.pi / 3 * i))
          for i in range(6)]
    hi = [(x, height, z) for x, _y, z in lo]
    lines = [f"# P6-2 假资产：六棱柱（R={radius:.2f} H={height:.2f}）—— 不是任何游戏件"]
    for x, y, z in lo:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for x, y, z in hi:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for i in range(6):
        j = (i + 1) % 6
        lines.append(f"f {i + 1} {j + 1} {j + 7} {i + 7}")        # 侧面
    lines.append("f " + " ".join(str(i + 1) for i in range(6)))    # 下底
    lines.append("f " + " ".join(str(i + 7) for i in range(6)))    # 上底
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_cli(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-m", "ba_mod_maker.cli", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P6-2 端到端离线排练（假资产 -> 包 -> check-model）")
    ap.add_argument("--game", default=str(DEFAULT_GAME))
    ap.add_argument("--keep", action="store_true", help="保留上一次产物（默认先清空 out/）")
    args = ap.parse_args(argv)

    t0 = time.time()
    game = Path(args.game)
    tmpl = game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / TEMPLATE
    if not tmpl.is_file():
        print(f"[✗] 找不到模板包：{tmpl}")
        return 2
    out = OUT
    if not args.keep and out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    obj = out / "fake_part.obj"
    fake_obj(obj)
    n_v = sum(1 for ln in obj.read_text(encoding="utf-8").splitlines() if ln.startswith("v "))
    n_f = sum(1 for ln in obj.read_text(encoding="utf-8").splitlines() if ln.startswith("f "))
    print(f"[① 假资产] {obj}  {n_v} 顶点 / {n_f} 个四边形面（自造，不是游戏件）")

    print(f"[② build-model] 模板包 = {TEMPLATE}")
    rc1, log1 = run_cli(["build-model", "--obj", str(obj), "--template-bundle", str(tmpl),
                         "--key", KEY, "--mesh-template", MESH_TEMPLATE,
                         "--material", MATERIAL, "--out", str(out)], ROOT)
    for ln in log1.strip().splitlines()[-14:]:
        print("    " + ln)
    print(f"    rc = {rc1}")
    if rc1 != 0:
        print(f"[✗] build-model 没过（rc={rc1}）")
        return 1

    bundles = sorted(out.glob("*_assets_all_*.bundle"))
    if not bundles:
        print(f"[✗] out/ 里没有 *_assets_all_*.bundle：{sorted(p.name for p in out.iterdir())}")
        return 1
    bundle = bundles[0]
    print(f"[② b] 产物包 = {bundle.name}  {bundle.stat().st_size:,} B")

    print("[③ check-model]")
    rc2, log2 = run_cli(["check-model", "--bundle", str(bundle)], ROOT)
    tail = log2.strip().splitlines()
    for ln in tail[-22:]:
        print("    " + ln)
    print(f"    rc = {rc2}")

    # 红条数：从 check-model 自己的输出里数（不看退出码就算数）
    reds = re.findall(r"^\s*(?:✗|红|FAIL|\[FAIL\])", log2, re.M)
    stars = re.findall(r"^\s*★\s*(.+)$", log2, re.M)
    print(f"    ★ 提示 {len(stars)} 条（**不是红**，但每一条都是「装机后才会暴露」的真机风险）：")
    for s in stars:
        print(f"      - {s}")
    (OUT / "rehearsal_report.json").write_text(json.dumps({
        "fake_obj": str(obj), "obj_vertices": n_v, "obj_faces": n_f,
        "template_bundle": TEMPLATE, "mesh_template": MESH_TEMPLATE, "material": MATERIAL,
        "build_model_rc": rc1, "check_model_rc": rc2,
        "reds": len(reds), "star_notes": stars,
        "bundle": str(bundle), "bundle_bytes": bundle.stat().st_size,
        "ok": rc1 == 0 and rc2 == 0 and not reds,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    [证据] {OUT / 'rehearsal_report.json'}")
    print()
    print("=" * 78)
    print(f"判据一行：build-model rc={rc1} · check-model rc={rc2} · "
          f"红 {len(reds)} 条 · 产物 {bundle}")
    verdict = "✅ 离线排练全绿（零红）" if (rc1 == 0 and rc2 == 0 and not reds) else "❌ 有红，别装"
    print(f"    {verdict} · 总用时 {time.time() - t0:.1f}s")
    print("=" * 78)
    return 0 if (rc1 == 0 and rc2 == 0 and not reds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
