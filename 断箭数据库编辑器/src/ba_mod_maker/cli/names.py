"""`names` 子命令：**单位 / 炮塔名字表**（用户需求 A8 + B5 的底座）。

## 为什么要有这条命令（而不是让 Blender 插件自己读容器）

用户 2026-10-12 选定：名字表**读游戏本体** `<游戏根>/BrokenArrow_Data/data.unity3d`
（⛔ 不是 zip —— 原话「你别用这种压缩包文件当名字表！」）。

但**Blender 自带的 Python 读不了它**：实测 Blender 5.2.1 / CPython 3.13.13 里
`HAS_CRYPTO False`，而容器里 24 张表全是 AES-256-CBC（`core/asset/dbcryp.py` 顶层就要
pycryptodome）。⇒ 分工：

    产品（有 Crypto，读一次 **1.47 s**）  --export-->  一份几十 KB 的**明文** JSON 缓存
                                                      |
    Blender 插件  <-----------------------------------+

缓存里记下来源的 `(size, mtime_ns)` 指纹 ⇒ 游戏一更新就自动作废重读（一次 `stat`，微秒级）。
⛔ 缓存**不落游戏目录**（往游戏目录塞文件会触发联机红线，`kb/26` §7：注入类**绝不联机**／
静态替换**可以联机、风险自负**；一个缓存不值这个代价）。

## 用法

    ba-mod-maker names export --game <游戏根>            # 读一次，落缓存（默认用户级目录）
    ba-mod-maker names export --game <游戏根> --dry-run  # 只量，不写
    ba-mod-maker names status --game <游戏根>            # 缓存新不新（只 stat）

退出码：
  0  成功 / 缓存是新的
  1  `status`：缓存没有或已过期（**要重新 export**）
  2  找不到 `data.unity3d` / 读不动
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ba_mod_maker.core.asset import unit_names as names_mod
from ba_mod_maker.core.errors import BaModMakerError

EXIT_OK = 0
EXIT_STALE = 1
EXIT_UNREADABLE = 2

_DESCRIPTION = "单位 / 炮塔名字表：从 data.unity3d 读一次，落一份明文缓存给 Blender 插件用"

_EPILOG = r"""例：
  ba-mod-maker names export --game "D:\Steam\steamapps\common\broken_arrow"
  ba-mod-maker names export --game <游戏根> --out D:\某处\units_names.json
  ba-mod-maker names status --game <游戏根> --json
"""


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `names` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "names",
        help="名字表：export 读一次 data.unity3d 落缓存 / status 看缓存新不新"
        "（退出码 0 新 / 1 过期或没有 / 2 读不动）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="names_action", required=True)

    read = actions.add_parser(
        "export",
        help="读 <游戏根>/BrokenArrow_Data/data.unity3d（实测 1.47 s）并写明文缓存",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    read.add_argument("--game", required=True, help="游戏根目录（BrokenArrow_Data 的上一级）")
    read.add_argument("--out", help="缓存落点（默认用户级 BAModMaker/units_names.json）")
    read.add_argument("--dry-run", action="store_true", help="只读只量，一个字节都不写")
    read.add_argument("--json", dest="json_out", action="store_true", help="只输出一个 JSON 文档")

    pairs = actions.add_parser(
        "pairs",
        help="车体 × 炮塔 配对（B5 的底座）：给单位键就只印它挂的炮塔",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pairs.add_argument("--game", required=True, help="游戏根目录")
    pairs.add_argument("--unit", help="单位键（Units.ModelFileName，例 RU_T72B）；不给就印汇总")
    pairs.add_argument("--cache", help="缓存位置（默认同 export）")
    pairs.add_argument("--json", dest="json_out", action="store_true", help="只输出一个 JSON 文档")

    status = actions.add_parser(
        "status",
        help="缓存有没有 / 新不新（只 stat 来源，不读内容）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    status.add_argument("--game", required=True, help="游戏根目录")
    status.add_argument("--cache", help="缓存位置（默认同 export）")
    status.add_argument("--json", dest="json_out", action="store_true", help="只输出一个 JSON 文档")
    return parser


def _cache_path(args: argparse.Namespace) -> Path:
    given = getattr(args, "out", None) or getattr(args, "cache", None)
    return Path(given) if given else names_mod.default_cache_path()


def _export(args: argparse.Namespace) -> int:
    import json
    import time

    game_root = Path(args.game)
    container = names_mod.source_file(game_root)
    if not container.is_file():
        print(f"错误：找不到 {container}", flush=True)
        return EXIT_UNREADABLE
    target = _cache_path(args)
    started = time.time()
    payload = names_mod.build_name_table(game_root)
    seconds = time.time() - started
    counts = payload["counts"]
    if not args.dry_run:
        names_mod.save_name_table(payload, target)
    report = {
        "action": "export",
        "dry_run": bool(args.dry_run),
        "source": payload["source"],
        "cache": str(target),
        "written": not args.dry_run,
        "seconds": round(seconds, 2),
        "counts": counts,
        "sample": {k: payload["units"][k] for k in list(payload["units"])[:3]},
        "multi_row_example": next(
            (f"{k} -> {v}" for k, v in payload["units"].items() if " 等 " in v), ""
        ),
    }
    if args.json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"来源      : {payload['source']['path']}")
    src = payload["source"]
    print(f"指纹      : size={src['size']:,} mtime_ns={src['mtime_ns']}")
    print(f"Units     : {counts['units_rows']} 行 -> {counts['model_keys']} 个模型键"
          f"（同名多行 {counts['multi_row_keys']} 个）")
    print(f"Turrets   : {counts['turret_rows']} 行 -> {counts['turret_keys']} 个炮塔键")
    print(f"配对      : {counts['turret_links']} 条 -> 覆盖 {counts['paired_units']} 个单位键")
    print(f"耗时      : {seconds:.2f} s")
    print(f"缓存      : {target}" + ("（--dry-run：没写）" if args.dry_run else "（已写）"))
    return EXIT_OK


def _status(args: argparse.Namespace) -> int:
    import json

    game_root = Path(args.game)
    target = _cache_path(args)
    payload = names_mod.load_name_table(target)
    fresh = names_mod.is_fresh(payload, game_root)
    report = {
        "action": "status",
        "cache": str(target),
        "exists": target.is_file(),
        "format_ok": payload is not None,
        "fresh": fresh,
        "counts": (payload or {}).get("counts", {}),
        "recorded_source": (payload or {}).get("source", {}),
    }
    try:
        report["current_source"] = names_mod.fingerprint_of(names_mod.source_file(game_root))
    except BaModMakerError as exc:
        report["current_source"] = None
        report["error"] = str(exc)
    if args.json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK if fresh else EXIT_STALE
    print(f"缓存      : {target}")
    print(f"在不在    : {'在' if report['exists'] else '不在'}")
    print(f"能用      : {'能' if report['format_ok'] else '不能（没写过 / 版本不对 / 坏了）'}")
    print(f"新不新    : {'新' if fresh else '过期或没有'}（比的是来源文件的 size + mtime_ns）")
    if report["counts"]:
        print(f"里面      : {report['counts']}")
    if not fresh:
        print("下一手    : ba-mod-maker names export --game <游戏根>")
    return EXIT_OK if fresh else EXIT_STALE


def _pairs(args: argparse.Namespace) -> int:
    """车体 × 炮塔 配对（只读缓存，不解容器）。

    ★ 这条直接回答用户需求 B5 的一半：「**能指定某个单位挂哪个炮塔**」。
    ⚠ 真件读数：`TurretUnits` **1,597 行**，但只有 **1,126 条**两边都能落到
    `ModelFileName` 上（其余指向的行 `ModelFileName` 是空的 —— 那种炮塔没有独立模型，
    是数据定义的）。所以「挂了几条」与「有几个炮塔模型」是两个数，⛔ 别混着报。
    """
    import json

    payload = names_mod.load_name_table(_cache_path(args))
    if payload is None:
        print(f"错误：没有名字表缓存（{_cache_path(args)}）—— 先跑 names export")
        return EXIT_UNREADABLE
    wanted = str(getattr(args, "unit", "") or "").strip()
    pairs = payload.get("pairs") or {}
    if wanted:
        rows = pairs.get(wanted)
        if rows is None:
            print(f"错误：缓存里没有单位键 {wanted!r}（共 {len(pairs)} 个有炮塔的单位键）")
            return EXIT_STALE
        report = {
            "unit": wanted,
            "display": names_mod.display_name(payload, wanted),
            "cache": str(_cache_path(args)),
            "turrets": [
                {
                    "turret": row["turret"],
                    "display": names_mod.display_name(
                        {"units": payload.get("turrets") or {}}, row["turret"]
                    ),
                    "order": row["order"],
                    "turret_id": row["turret_id"],
                }
                for row in rows
            ],
        }
        if args.json_out:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return EXIT_OK
        print(f"单位      : {report['display']}（{wanted}）")
        print(f"炮塔      : {len(report['turrets'])} 条有独立模型")
        for row in report["turrets"]:
            print(
                f"  order={row['order']}  {row['turret']}  "
                f"({row['display']})  TurretId={row['turret_id']}"
            )
        return EXIT_OK
    counts = payload.get("counts", {})
    report = {
        "units_with_turrets": len(pairs),
        "links": counts.get("turret_links"),
        "turret_keys": counts.get("turret_keys"),
        "cache": str(_cache_path(args)),
    }
    if args.json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"有炮塔的单位键 : {report['units_with_turrets']}")
    print(f"配对条数       : {report['links']}（能落到炮塔模型上的）")
    print(f"炮塔模型键     : {report['turret_keys']}")
    print("看某一个单位   : --unit <单位键>（例 RU_T72B）")
    return EXIT_OK


def run(args: argparse.Namespace) -> int:
    """跑一遍，返回退出码（0 / 1 / 2）。"""
    try:
        if args.names_action == "export":
            return _export(args)
        if args.names_action == "pairs":
            return _pairs(args)
        return _status(args)
    except BaModMakerError as exc:
        print(f"错误：{exc}", flush=True)
        return EXIT_UNREADABLE
