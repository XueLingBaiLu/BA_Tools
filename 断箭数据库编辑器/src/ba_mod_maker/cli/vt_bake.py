r"""vt-bake 子命令：**一条命令把 N 组源图烘成 VT 池子**（一个 group 下 N 个 item）。

## 它做什么

    ba-mod-maker vt-bake --project <Unity 工程> --normal <png> --mask <png> --out <目录>
    # N 颗材质：--normal / --mask **成对重复**（顺序即材质顺序）
    ba-mod-maker vt-bake --project <工程> --normal a_n.png --mask a_m.png \
                         --normal b_n.png --mask b_m.png --out <目录>

    N 组源图 -> 工程临时件（Assets/<work>/：带 [TextureStack._Textures(0/1)] 的**一颗** shader
           + N 组源图 + N 颗材质（同一个 shader）+ 含 N 个 cube 的场景 + 两个编辑器脚本）
         -> **一趟 Unity 会话**按序做完三站：开 VT + 对齐 HDRP（原值先存盘）+ 写材质/场景
            -> BuildPlayer（池子**只在出 Player 时**烘）-> 按快照复原（写在 finally 里）
         -> 从 <player>_Data\StreamingAssets\ 捞 5 个文件到 --out
         -> 删掉工程临时件、VT 开关回原值、核 Unity 无残留
    最后打印 **group / item（N 颗就是 N 个 item）** —— 正是 `vt` 子命令要的那两个参数。

**为什么要一次烘 N 颗**：一辆车用多颗材质（T-90M 的 `RU_T90M 1` + `RU_T72B 1`），
而**一个 group 只有一套池子** —— 一次只烘一颗，换一颗就把别的 item 挤掉（kb/18 §6.3）。
N 颗材质**共用同一颗 shader**（group 由栈配置决定），同一次构建就写进同一个 group。

库那一侧全在 `asset_kernel/vt_bake.py`（判据与两个硬事实写在它的模块文档里）；
本文件只负责：参数怎么点、判据怎么印、退出码怎么给（AGENTS §5「逻辑只有一份」）。

## 它不做什么

* ⛔ 不写游戏目录、不碰数据库、不动 `Assets/VTPlayer/**`；
* ⛔ 不装池子、不改材质指向 —— 那是 `vt` 子命令（A1）与文件通道的事。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 5 个池子文件都产出了，且 group/item 拿到了、收尾干净 |
| 1 | **有红**：超时 / 日志里有 error CS / 读数 VERDICT 不对 / 池子形状不对 / Unity 有残留 |
| 2 | 用不了：源图不在了或解不开、工程不像 Unity 工程、找不到 Unity.exe |
"""

from __future__ import annotations

import argparse
from typing import Any

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import AssetError, BaModMakerError

#: 退出码。与 `vt.py` / `finder.py` / `validator.py` 同一套语义。
EXIT_OK = 0
EXIT_RED = 1
EXIT_UNUSABLE = 2

_DESCRIPTION = r"""把 N 组源图烘成一个 VT 池子（一个 group 下 N 个 item），并打印 group / item。

干什么：N 组 (normal, mask) -> 工程临时件（一颗 shader + N 颗材质（共用它）+ N 个 cube 的场景）
        -> **一趟** Unity 会话按序做完 setup（开 VT + 对齐 HDRP）+ build（出 Player，
           池子只在出 Player 时烘）+ restore（按快照复原；写在 finally 里，出事也照样复原）
        -> 从 <player>_Data\StreamingAssets\ 捞池子文件到 --out
        -> 清掉工程临时件 + 核 Unity 无残留。
        这一趟要是没复原成（死在半路 / 复原本身失败）-> 兜底再起一趟专跑复原。

N 怎么写：--normal / --mask **成对重复**，第 i 对 = 第 i 颗材质（顺序即材质顺序）。
  ba-mod-maker vt-bake --project <工程> --normal a_n.png --mask a_m.png \
                       --normal b_n.png --mask b_m.png --out <目录>

判据（每条都能红）：产出的 .gts 文件名 == 读数 group · 读数 VERDICT == 期望 ·
                  **N 个 item 齐且两两不同** · 日志无 error CS · 收尾后 Get-Process Unity = 0。
                  ⛔ 不认 batchmode 的退出码（kb/14 §6）—— 看产物 mtime 与 VERDICT。
⛔ 不认 batchmode 的退出码（kb/14 §6）。

不写游戏目录、不装池子、不改材质指向（那是 vt 子命令）。
退出码：
  0  池子文件都产出了，group/item 拿到了，收尾干净
  1  有红（超时 / error CS / VERDICT 不对 / 池子形状不对 / item 不全或重复 / Unity 有残留）
  2  用不了（源图不在或解不开 / --normal 与 --mask 张数不等 / 工程不像 Unity 工程 /
          找不到 Unity.exe）
"""

_EPILOG = r"""例：
  # 一条命令烘一套（热工程约 20~25 秒：**一趟**编辑器会话，其中一段是完整的 BuildPlayer）
  ba-mod-maker vt-bake --project D:\断箭模组制作\unity-armory ^
      --normal _work\paint-design\vt_norm_flat.png ^
      --mask   _work\paint-design\vt_mask_matte.png ^
      --out    _work\vtbake\run1

  # N 颗材质 -> 同一个 group 下 N 个 item（一辆车多颗材质就靠这个）
  ba-mod-maker vt-bake --project D:\断箭模组制作\unity-armory ^
      --normal _work\vtbake\src\car_n.png --mask _work\vtbake\src\car_m.png ^
      --normal _work\vtbake\src\era_n.png --mask _work\vtbake\src\era_m.png ^
      --out    _work\vtbake\run-multi

  # 三站分开跑（老写法；A/B 对照与排障用 —— 要起三次 Unity，比默认慢一倍）
  ba-mod-maker vt-bake ... --split

  # 只跑其中一站（调试用；Unity 一次启动十几秒，别整条重跑）
  ba-mod-maker vt-bake ... --only setup|build|restore

  # 不清场（调试用）：保留 Assets\<work>\ 与 VT 开关，⛔ 收工前必须手动复原
  ba-mod-maker vt-bake ... --keep

产物：--out 下 **一套**池子（<group>.gts + <group>0_1/2/3/4.gtp，1 颗材质时就是这 5 个文件）
—— N 颗材质仍然是**一套**（同名池子会互相覆盖），只是 .gts/.gtp 里装着 N 个 item；
证据（读数/日志/快照）在 --out\_vtbake\。
Player 落点默认 <SystemDrive>:\vtbake_player —— **必须纯 ASCII**（非 ASCII 路径下 Granite 打不开
烘好的池子，tools/unity_vtplayer.py 文件头那条 A/B 实测）。
"""


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `vt-bake` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "vt-bake",
        help="烘 VT 池子：源图 + Unity 工程 -> 5 个 .gts/.gtp 文件 + group/item（走 BuildPlayer）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", required=True,
                        help="Unity 工程根（要含 Assets\\ 与 ProjectSettings\\）")
    #: N 颗材质 = --normal / --mask **成对重复**，第 i 对就是第 i 颗（顺序即材质顺序）
    parser.add_argument("--normal", required=True, action="append", metavar="PNG",
                        help="法线源图（PNG/JPG）—— 进 _Layer0；N 颗材质就重复 N 次")
    parser.add_argument("--mask", required=True, action="append", metavar="PNG",
                        help="遮罩源图（PNG/JPG）—— 进 _Layer1；与 --normal 一一对应")
    parser.add_argument("--out", required=True,
                        help="产物目录：5 个池子文件落这里；证据落 <out>\\_vtbake\\")
    parser.add_argument(
        "--unity", default=None,
        help="Unity.exe（默认 6000.3.24f1 —— 与游戏同代；kb/42 C13）")
    parser.add_argument("--player-dir", default=None,
                        help="Player 落点（默认 <SystemDrive>:\\vtbake_player；必须纯 ASCII）")
    parser.add_argument("--timeout-setup", type=float, default=600.0,
                        help="setup 那一站的硬超时秒")
    parser.add_argument("--timeout-build", type=float, default=1800.0,
                        help="build 那一站的硬超时秒")
    parser.add_argument("--timeout-restore", type=float, default=600.0,
                        help="restore 那一站的硬超时秒")
    parser.add_argument("--only", choices=("all", "setup", "build", "restore"), default="all",
                        help="只跑这一站（调试用；默认 all = 三站一趟会话做完）")
    #: 三合一（默认）把三站挤进**一趟** Unity 会话：省下两次进程启动 + 两次工程加载。
    #: --split 是它的 A/B 对照（老的三趟写法），也是"C# 站内主体还对不对"的排障入口。
    parser.add_argument("--split", action="store_true",
                        help="三站各起一趟 Unity（老写法；⛔ 慢一倍，只给 A/B 对照与排障）")
    parser.add_argument("--allow-levels", dest="allow_levels", action="store_true",
                        help="放行源图档位体检的红（Mask 的 R 越出游戏自用档位时默认**不烘**；"
                             "确实要烘越界的那一套才加它）")
    parser.add_argument("--keep", action="store_true",
                        help="不清场（调试用）：保留工程临时件与 VT 开关，⛔ 收工前自己复原")
    parser.add_argument("--json", dest="json_out", action="store_true",
                        help="只输出一个 JSON 文档（人读那份改走 stderr）")
    return parser


def _as_list(value: object) -> list[str]:
    """--normal / --mask 收成一张表。

    给一个字符串也认（用例与"直接调 run"的老调用方给的就是字符串）——
    ⛔ 不认的话字符串会被当成一串字符逐个迭代，那是静默的错。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]  # type: ignore[union-attr]


def _pairs(normals: object, masks: object) -> tuple[tuple[str, str], ...]:
    """两张**平行**的表 -> 有序的 (normal, mask) 清单。

    ⛔ 这里**不许用 zip** —— zip 会把多出来的那张**静默丢掉**，
    而"只给一组却期待两组"恰恰是必须报错的那种输入（退出码 2，且不产出任何池子文件）。
    """
    got_normal = _as_list(normals)
    got_mask = _as_list(masks)
    if len(got_normal) != len(got_mask):
        raise AssetError(
            f"--normal 给了 {len(got_normal)} 张，--mask 给了 {len(got_mask)} 张 —— 源图必须成对",
            hint="一组源图 = 一张 --normal + 一张 --mask；N 颗材质就各给 N 次（顺序即材质顺序）",
        )
    if not got_normal:
        raise AssetError("一组源图都没有：要 --normal 与 --mask",
                         hint="至少给一组：--normal <png> --mask <png>")
    return tuple((got_normal[i], got_mask[i]) for i in range(len(got_normal)))


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    # 延迟 import：--help 与其它子命令不许为一个 VT 把 PIL 拉起来。
    from ba_mod_maker.asset_kernel import vt_bake

    emitter = Emitter(json_mode=bool(args.json_out))
    try:
        pairs = _pairs(args.normal, args.mask)
        plan = vt_bake.plan_bake(
            project=args.project,
            pairs=pairs,
            out_dir=args.out,
            unity=args.unity,
            player_dir=args.player_dir,
            only=args.only,
            merged=not getattr(args, "split", False),
            keep=args.keep,
            timeout_setup=args.timeout_setup,
            timeout_build=args.timeout_build,
            timeout_restore=args.timeout_restore,
            strict_levels=not getattr(args, "allow_levels", False),
        )
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE

    emitter.field("工程", plan.project)
    if plan.material_count == 1:
        # N=1：与从前逐字相同的两行
        size_normal, size_mask = plan.image_sizes
        emitter.field("源图 normal", f"{plan.normal}  {size_normal[0]}x{size_normal[1]}")
        emitter.field("源图 mask", f"{plan.mask}  {size_mask[0]}x{size_mask[1]}")
    else:
        emitter.field("材质颗数", plan.material_count)
        for source in plan.sources:
            emitter.field(
                f"源图[{source.index}]",
                f"{source.normal} {source.size_normal[0]}x{source.size_normal[1]}"
                f"  ·  {source.mask} {source.size_mask[0]}x{source.size_mask[1]}",
            )
    emitter.field("产物目录", plan.out_dir)
    emitter.field("Player 落点", plan.player_dir)
    emitter.field("编辑器", " ".join(plan.unity_argv))
    emitter.line("")

    # 源图档位体检（kb/22 §5）：先印读数，红了就**一趟 Unity 都不起**（那 3 分钟白跑）。
    for got in plan.levels:
        for line in got.lines():
            emitter.line(line)
    if plan.levels:
        emitter.line("")
    if plan.level_problems:
        red = list(plan.level_problems)
        emitter.document({
            "project": str(plan.project),
            "out": str(plan.out_dir),
            "levels": _levels_json(plan),
            "ok": False,
            "red": red,
        })
        emitter.error(
            "有红：" + "；".join(red),
            hint="这是**源图档位体检**（kb/22 §5）：Mask 的 R（金属度）写高了整片发暗/发黑。"
            "照抄游戏自用档位（漆面 ~0.05 / 裸金属 ~0.45）再烘；"
            "确实要烘越界的那一套，才在命令上加 --allow-levels",
        )
        return EXIT_RED

    outcome = vt_bake.bake(plan, log=emitter.line)
    for line in outcome.lines():
        emitter.line(line)
    emitter.line("")

    if outcome.ok:
        emitter.field("group", outcome.group)
        emitter.field("item", outcome.item)
        # 一趟可能烘出**不止一套**池子（group 是逐材质的）—— 每套自己的账印出来
        if len(outcome.pools) > 1:
            emitter.field("池子套数", f"{len(outcome.pools)} 套（按 group 分开收）")
            for got in outcome.pools:
                emitter.line("  ✅ 池子组 " + got.account())
        if len(outcome.items) > 1:
            for index, value in enumerate(outcome.items):
                name = plan.material_path(index).name
                emitter.field(f"item[{index}]", f"{name} -> {value}")
        emitter.line("  下一步（把材质指过去；默认干跑，--apply 才写）：")
        if len(outcome.items) == 1:
            emitter.line(
                '    ba-mod-maker vt <unit_key> --material "<材质名>" '
                f"--group {outcome.group} --item {outcome.item}"
            )
        else:
            for index, value in enumerate(outcome.items):
                name = plan.material_path(index).name
                emitter.line(
                    f'    ba-mod-maker vt <unit_key> --material "<{name} 对应的那颗材质>" '
                    f"--group {_group_of(outcome, index)} --item {value}"
                )
        emitter.document(_document(plan, outcome))
        return EXIT_OK

    emitter.document({**_document(plan, outcome), "ok": False,
                      "red": list(outcome.problems)})
    reasons = "；".join(outcome.problems) if outcome.problems else "没产出池子"
    emitter.error("有红：" + reasons,
                  hint="看上面的读数与 ★ 红；⛔ 别拿 batchmode 的退出码当判据（kb/14 §6）")
    return EXIT_RED


def _group_of(outcome: Any, index: int) -> str:
    """第 index 颗材质的 group（读数里逐材质那串；缺了就退回老字段 `group`）。"""
    if index < len(outcome.material_groups):
        return outcome.material_groups[index]
    return outcome.group


def _levels_json(plan: Any) -> list[dict[str, Any]]:
    """源图档位体检的读数（与 `MaskLevels.lines()` 印的是同一批数字）。"""
    return [
        {
            "label": got.label,
            "path": str(got.path),
            "size": [got.size[0], got.size[1]],
            "has_alpha": got.has_alpha,
            "r50": got.r50,
            "r95": got.r95,
            "rmax": got.rmax,
            "r95_value": round(got.r95 / 255, 4),
            "over_share": round(got.over_share, 6),
            "a50": got.a50,
            "a95": got.a95,
            "a_max": got.amax,
            "over": got.over,
            "limit_byte": got.limit_byte,
            "limit_value": round(got.limit_value, 4),
        }
        for got in plan.levels
    ]


def _document(plan: Any, outcome: Any) -> dict[str, Any]:
    """`--json` 的那份文档（字段名与 `--help` 里写的对得上）。"""
    return {
        "levels": _levels_json(plan),
        "project": str(plan.project),
        #: 三站是否挤进**一趟** Unity 会话（--split 时是 false）
        "merged": plan.merged,
        # normal / mask 是**第 0 组**（老字段，N=1 时就是全部）；N 组的完整清单在 sources
        "normal": str(plan.normal),
        "mask": str(plan.mask),
        "sources": [
            {
                "index": source.index,
                "material": plan.material_path(source.index).name,
                "normal": str(source.normal),
                "mask": str(source.mask),
                "size_normal": list(source.size_normal),
                "size_mask": list(source.size_mask),
            }
            for source in plan.sources
        ],
        "out": str(plan.out_dir),
        "player_dir": str(plan.player_dir),
        "group": outcome.group,
        "item": outcome.item,
        "items": list(outcome.items),
        "materials": [
            {"name": plan.material_path(index).name, "item": value}
            for index, value in enumerate(outcome.items)
        ],
        "group_pool": outcome.group_pool,
        #: 收下来的每一套池子（一趟可能不止一套）＋ 每颗材质自己的 group
        "pools": [
            {
                "group": got.group,
                "files": [path.name for path in got.files],
                "bytes": got.size,
                "problems": list(got.problems),
            }
            for got in outcome.pools
        ],
        "material_groups": list(outcome.material_groups),
        "files": [{"name": path.name, "bytes": path.stat().st_size} for path in outcome.files],
        "stages": [
            {
                "stage": stage.stage,
                "method": stage.method,
                #: 三合一的三个阶段共用同一个会话标识（"one"）；空 = 一趟进程只干这一站
                "session": stage.session,
                "rc": stage.rc,
                "seconds": round(stage.seconds, 1),
                "timed_out": stage.timed_out,
                "reading_updated": stage.reading_updated,
                "verdict": stage.reading.verdict if stage.reading is not None else None,
                "cs_errors": list(stage.cs_errors),
            }
            for stage in outcome.stages
        ],
        "unity_left": outcome.unity_left,
        "assets_removed": list(outcome.assets_removed),
        "notes": list(outcome.notes),
        "problems": list(outcome.problems),
    }
