r"""P3-5 · **LOD 阶梯闸门** CLI（薄封装）—— 判据逻辑在 `src/ba_mod_maker/asset_kernel/lod_gate.py`。

## 用途

一个 modder 换掉某个单位的网格之后，判「这条 LOD 链还成不成立」。
形状照本仓 CLI 的老约定：`--selftest` 跑内置夹具、`--check` 判真件、rc 说话。
**本文件只打印，不判** —— 红档/黄牌/夹具都在 kernel 那一份（改判据改那边）。

> ⚠ **2026-10-25：`--gen` / `--gen-selftest` 那半已删** —— LOD 生成链（`asset_kernel/lod_gen.py`、
> `lod_simplify`、`qem/`）随「收缩到最小产品」整体退役（网格只由 Unity 的 ModelImporter 造）。
> 本工具保留**判据侧**：`--selftest` / `--table` / `--json` / `--readings` / `--check`。

## 判据一行

    .venv\\Scripts\\python.exe tools\\lod_gate.py --selftest
      -> 违规夹具 3/3 报红 · 合法件 4/4 绿 · **rc = 0**

## 正控 / 负控（本工具按 AGENTS §0.9 动作 2 的叫法）

* **正控 = 已知违规夹具（必须红）**：R1 顶点增加 · R2 空级 · R3 阈值数 != LOD 级数
* **负控 = 合法件（必须绿，不许误伤）**：标准三级档 · 单级形态 · 步比 1.0 · bbox 不同

⚠ 老 `tools/gates.py`（2026-10-15 随插件重做下线）的表格用的是**相反**的标签
（它把「实读真件」叫正控）。这里跟 AGENTS §0.9 走。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 自检全过（违规全红 + 合法全绿）／`--check` 的红件为 0 |
| 1 | 自检不过 ／ `--check` 有红件 |
| 2 | 用不了（参数不对 / 文件读不开 / 别的东西抛了） |
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from ba_mod_maker.asset_kernel import lod_gate as _kernel  # noqa: E402
from ba_mod_maker.asset_kernel.lod_gate import (  # noqa: E402
    BAND,
    FORBIDDEN_RED_LINES,
    NEGATIVE,
    POSITIVE,
    READINGS,
    LodGroup,
    LodLevel,
    Run,
    Verdict,
    group_from_dict,
    judge,
    readings_document,
    selftest,
)

#: 薄封装：判据、夹具、读数**都从 kernel 转发**（同一个对象，⛔ 不复制第二份）
__all__ = [
    "BAND",
    "FORBIDDEN_RED_LINES",
    "NEGATIVE",
    "POSITIVE",
    "READINGS",
    "LodGroup",
    "LodLevel",
    "Run",
    "Verdict",
    "build_parser",
    "cmd_check",
    "cmd_json",
    "cmd_readings",
    "cmd_selftest",
    "cmd_table",
    "group_from_dict",
    "judge",
    "main",
    "readings_document",
    "selftest",
]

# 正文（三条禁红线 + 两条未读 + 两份 lod_gate 的关系）只有一份，在 src/ 那一份里 ——
# 这里把它接到本文件的 docstring 后面（下个会话无论从哪一头读，读到的都是同一段）。
__doc__ = f"{__doc__}\n\n{'=' * 78}\n\n{_kernel.__doc__}"


def _fix_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------- #
# 命令（只打印；判据全在 kernel）
# --------------------------------------------------------------------------- #
def cmd_selftest(args) -> int:
    run = selftest()
    print("=== P3-5 LOD 阶梯闸门 · 正负控自检 ===")
    print("    正控 = 已知违规（必须红）· 负控 = 合法件（必须绿）")
    for row_id, title, reading, ok in run.rows:
        print(f"  [{('OK ' if ok else 'BAD')}] {row_id} {title}")
        print(f"        {reading}")
    print(f"=== 汇总：正控 {run.positive_ok}/{len(POSITIVE)} 报红 · "
          f"负控 {run.negative_ok}/{len(NEGATIVE)} 绿 · rc = {run.rc} ===")
    print("rc =", run.rc, "（0 = 违规夹具全红且合法件全绿）")
    return run.rc


def cmd_table(args) -> int:
    run = selftest()
    print("| 行 | 夹具 | 类别 | 读数 | 通过 |")
    print("|---|---|---|---|---|")
    for row_id, title, reading, ok in run.rows:
        kind = "正控(违规)" if title.startswith("正控") else "负控(合法)"
        mark = "✅" if ok else "❌"
        cell = reading.replace("|", "/")
        print(f"| {row_id} | {title} | {kind} | {cell} | {mark} |")
    return run.rc


def cmd_json(args) -> int:
    run = selftest()
    payload = {
        "schema": "bamod-lod-gate/1",
        "rc": run.rc,
        "positive": {"n": len(POSITIVE), "ok": run.positive_ok},
        "negative": {"n": len(NEGATIVE), "ok": run.negative_ok},
        "rows": [
            {"id": i, "title": t, "reading": r, "ok": ok} for i, t, r, ok in run.rows
        ],
        "forbidden_red_lines": list(FORBIDDEN_RED_LINES),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return run.rc


def cmd_readings(args) -> int:
    """印原版分布参照 + 三条**不许当红档**的读数依据（读不到就如实说未读）。"""
    u = readings_document()
    if u is None:
        print(f"未读：缺 {READINGS}（先跑 _work/p3lod/probe.py --bundle … --out lod_units.json）")
        return 0
    print(f"原版参照：{READINGS.name} · bundle {u['bundle']}")
    print(f"  LODGroup {u['lod_groups']} · 网格 {u['meshes']} · 对 {u['pairs']}"
          f"（同网格 {u['pairs_same_mesh']} / 不同网格 {u['pairs_diff_mesh']}）")
    print(f"  层级数分布 {u['levels_hist']}")
    print(f"  整套阈值 Top {list(u['threshold_triples'].items())[:4]}")
    for key, v in u["step_ratios"].items():
        print(f"  步比 {key}: n={v['n']} p10={v['p10']} p50={v['p50']} p90={v['p90']}")
    print(f"  bbox 逐位相同 {u['diff_bbox_bitwise_equal']}/{u['pairs_diff_mesh']} = "
          f"{100 * u['diff_bbox_bitwise_equal_frac']:.1f}%  ← ⛔ 不许当红档")
    print("  ⛔ 不许当红档的三条：bbox 逐位相同（98.3% 会被误判）· "
          "任何 ≥0.0814 的比值线（≥10% 会被误判）· 步比 = 1.0（原版 p90）")
    print("  未读：LOD2→3 只有 1 个样本；低于 p10 的比值尾部（本文件只存 p10/p50/p90）")
    return 0


def cmd_check(args) -> int:
    payload = json.loads(Path(args.check).read_text(encoding="utf-8"))
    rows = payload.get("groups") or payload
    bad = 0
    for row in rows:
        v = judge(group_from_dict(row))
        print(f"{row.get('name', '?')} | {'PASS' if v.ok else 'FAIL'} | {v.line()}")
        bad += 0 if v.ok else 1
    print(f"=== {len(rows)} 条 LOD 链 · 红 {bad} 条 ===")
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lod_gate.py",
        description="P3-5 LOD 阶梯闸门：R1 顶点增加 / R2 空级 / R3 链断（⛔ 比值与 bbox 不当红档）",
    )
    parser.add_argument("--selftest", action="store_true",
                        help="跑内置夹具：违规 3 条必须红 · 合法 4 条必须绿（默认动作）")
    parser.add_argument("--table", action="store_true", help="打印正负控表（markdown 行）")
    parser.add_argument("--json", action="store_true", help="把自检结果打成 JSON")
    parser.add_argument("--readings", action="store_true",
                        help="印原版分布参照（三条禁红线的读数依据）")
    parser.add_argument("--check", metavar="JSON",
                        help="判真件：{groups:[{name,levels,thresholds}]}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _fix_stdout()
    args = build_parser().parse_args(argv)
    try:
        if args.table:
            return cmd_table(args)
        if args.json:
            return cmd_json(args)
        if args.readings:
            return cmd_readings(args)
        if args.check:
            return cmd_check(args)
        return cmd_selftest(args)
    except Exception as exc:  # CLI 不许静默成功（tools/** 已豁免 BLE001，别再挂 noqa）
        print(f"跑不动：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
