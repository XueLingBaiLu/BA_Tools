r"""`vt` 子命令：**VT 那一跳的两条路** —— 改材质里的组指向 ／ 搬池子那 5 个文件。

## 两条路（同一个形状：默认干跑，`--apply` 才写）

* **材质**：`vt <unit> --material … --group … --item …`
  —— **修改**材质里两个既有字段（等长写回），撤销靠反向 Δ（补丁柜 `<工程目录>\patches`）。
* **池子**：`vt pool status` / `install --pool <目录>` / `remove`
  —— **新增** `.gts/.gtp` 那 5 个文件到 `StreamingAssets`，撤销 = **删掉即回退**
  （账本与判据在 `asset_kernel/vt_pool.py`）。

## 材质那条路做什么

    ba-mod-maker vt <unit_key> --material "RU_T72B 1" --group <32hex> --item <32hex>

    unit_key -> catalog -> 唯一命中包 -> prefab 的材质 -> 栈表 -> 改 group / item
             -> 等长写回（L1）+ 重算 CRC + **成对**更新 catalog
             -> `--apply` 时补丁落 `<工程目录>\patches\`（反向 Δ，⛔ 不拷整包）

库那一侧全在 `asset_kernel/vt_stack.py`（四条自证 + 库/CLI 分层见那里的模块文档）；
本文件只负责：源怎么开、参数怎么点、判据怎么印、退出码怎么给（AGENTS §5「逻辑只有一份」）。

## ⛔ 它不做什么

* **材质那条路不搬池子文件**：`.gts` / `.gtp` 是「**新增文件**」（AGENTS §0.10），
  走 `vt pool install`。给了 `--pool` 时它只**读**一眼就位没有 —— 池子没就位时 ⛔ 拒绝 `--apply`，
  因为材质指过去之后游戏会**加载不到那个组**（kb/18 §6.2 的 `Could not load`）。
* **池子那条路不动材质**：它只往 `<游戏>\BrokenArrow_Data\StreamingAssets` 加 / 删**我们自己装的**
  那几个文件（判据与账本在 `asset_kernel/vt_pool.py`：`remove` 只删 sha256 相符的那些）。
* **池子目录里可以有不止一套**（一个 group 一套；实测一趟烘出两套 —— `_work/vtb1/extreme/pool/`）：
  `install` 一次把**每一套**都装上，并按 group 分别记账；**哪一套缺分片就整体拒绝**
  （装了它等于让材质指向一个加载不到的组）。分组尺子只有一份：`vt_bake.pool_sets`。
* 两条路都不碰数据库、不碰 `data.unity3d`、不新增/删除任何对象。

## 默认干跑

`--apply` 才写。不加它：一个字节都不写、不建补丁目录（上面的数字是**算出来的真值**，
不是估算 —— 见 `writer` 的 F1 判据一行）。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 干跑干净，或真装 / 真卸完成 |
| 1 | **有红**：池子没就位 ／ 拒绝覆盖 ／ 拒绝删 ／ 安装或写后复核没过 |
| 2 | 用不了：定位不到 / 参数不对 / 空改动 / 池子缺文件时想 `--apply` / 账本读不开 |
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import BaModMakerError

#: 退出码。与 `finder.py` / `validator.py` 同一套语义（0 干净 / 1 有红 / 2 用不了）。
EXIT_OK = 0
EXIT_RED = 1
EXIT_UNUSABLE = 2

#: `unit` 那一个位置参数上的**魔字**：写它就是「池子那条路」（`vt pool …`）。
POOL_ROUTE = "pool"

#: 只有**材质**那条路才必填的三样（池子那条路一落定就把它们松开，见 `_UnitOrPool`）。
_UNIT_PATH_REQUIRED = ("material", "group", "item")


class _UnitOrPool(argparse.Action):
    """`unit` 那一个位置参数：值是 `pool` ⇒ 这一趟走**池子那条路**。

    为什么需要它：`vt <单位> --material …` 与 `vt pool <动作>` 共用同一张 argparse 表，
    而 argparse 的 `required` 是**整张表**的（没有「按分支必填」这种写法）。照原样写，
    `vt pool status` 会先被「--material 是必填」顶回去。于是由**最先落地的那个位置参数**
    决定这一趟是哪一支：值是 `pool` 就松开三条必填，**否则一条都不松**
    ⇒ `vt RU_T72B`（缺 --material）**照旧报错**（`tests/test_vt_stack.py` 那条契约不变）。

    ⚠ 松开是**就地表上的**（argparse 没有「这一次解析」的钩子），而它是**每次解析都按第一个
    位置参数重算**的 —— 同一个 parser 对象上先跑 `vt pool status` 再跑 `vt RU_T72B`，
    后者照样报错（`tests/test_vt_pool.py` 里那条正控守着）。
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: Any,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        relax = values == POOL_ROUTE
        for dest in _UNIT_PATH_REQUIRED:
            _action_of(parser, dest).required = not relax


def _action_of(parser: argparse.ArgumentParser, dest: str) -> argparse.Action:
    """按 `dest` 取这张表上的一个 action（改了名字就当场红，⛔ 别静默地不松开）。"""
    for action in parser._actions:  # noqa: SLF001 -- argparse 没有公开的「按 dest 取 action」写法
        if action.dest == dest:
            return action
    raise LookupError(f"argparse 表上没有 dest={dest!r} 这个参数（是不是改过名字？）")

_DESCRIPTION = r"""VT 那一跳的两条路：改材质里的组指向 ／ 搬池子那 5 个文件。

材质（修改）：
  vt <unit_key> --material <名> --group <32hex> --item <32hex>
    -> unit_key -> catalog -> 唯一命中包 -> prefab 的材质 -> 栈表 -> 改 group / item
    -> 等长写回（L1）+ 重算 CRC + 成对更新 catalog；
    --apply 时补丁落 <工程目录>\patches（反向 Δ，不拷整包当备份）。

池子（新增文件）：
  vt pool status                 查：账本里记着的在盘上就位没有（--pool 再核一遍源）
  vt pool install --pool <目录>   装：把这 5 个 .gts/.gtp 加进 StreamingAssets ＋ 写账本
  vt pool remove                 卸：只删账本里记着、且 sha256 相符的那些

默认干跑：一个字节都不写。退出码：
  0  干跑干净 / 真装 / 真卸完成
  1  有红（池子没就位 / 拒绝覆盖 / 拒绝删 / 安装或写后复核没过）
  2  用不了（定位不到 / 参数不对 / 空改动 / 池子缺文件时想 --apply / 账本读不开）
"""

_EPILOG = r"""两个名字都是**编辑器在构建期算出来的**（kb/18 §6.3），不要手写"看起来像"的：
它们就是烘池子时产出的 <group>.gts / <group>0_1.gtp 的那个 <group>，以及它的 item。

例：
  # 干跑：只算不写（先看判据一行：改几字节、命中几块、catalog CRC 变成多少）
  ba-mod-maker vt RU_T72B --material "RU_T72B 1" --group 243ba7869bfa79d5ab794e9cc4a8329a \
      --item d81a0cdf4f975178e7379047107e318f

  # 顺手核一遍池子在不在游戏盘上（只读）
  ba-mod-maker vt RU_T72B --material "RU_T72B 1" --group <32hex> --item <32hex> \
      --pool _work\paint-design\vt-matte-out

  # 真装（走反向 Δ；补丁落 <工程目录>\patches，还原走 assets restore）
  ba-mod-maker vt RU_T72B --material "RU_T72B 1" --group <32hex> --item <32hex> --apply

  # 机器可读：stdout 上只有一份 JSON 文档，人读那份改走 stderr
  ba-mod-maker vt RU_T72B --material "RU_T72B 1" --group <32hex> --item <32hex> --json

池子那 5 个文件（1 个 .gts + 4 个 .gtp）—— 与上面同一套默认干跑：
  # 查：账本记着的在不在（没装过时报「账本不在」）
  ba-mod-maker vt pool status --game D:\Steam\steamapps\common\broken_arrow

  # 装（干跑：只算这 5 个的 sha256 与盘上现状，一个字节都不写）
  ba-mod-maker vt pool install --pool _work\paint-design\vt-player-out

  # 装（真写：加文件 ＋ 写账本；盘上同名但 sha256 不同时默认**拒绝覆盖**，要 --force）
  ba-mod-maker vt pool install --pool _work\paint-design\vt-player-out --apply

  # 卸（只删账本里记着、且 sha256 相符的那些；被改过的一个都不删）
  ba-mod-maker vt pool remove --apply
"""


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `vt` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "vt",
        help="修改材质里的 VT 组指向（m_BuildTextureStacks，等长；默认干跑，--apply 走反向 Δ）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "unit",
        action=_UnitOrPool,
        help="地址键 = DB 的 Units.ModelFileName 原样值（例 RU_T72B）；不是显示名。"
        " 写 pool 就是**池子那条路**（vt pool status|install|remove）——"
        " 那条路搬的是 .gts/.gtp 那 5 个文件，不是材质",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("status", "install", "remove", "stats"),
        metavar="{status,install,remove,stats}",
        help="只有 `vt pool` 后面才给这个词（池子：查 / 装 / 卸 / 读页表）；`vt <单位>` 那条路不给",
    )
    parser.add_argument("--material", required=True, help="材质名，例 'RU_T72B 1'（材质那条路）")
    parser.add_argument(
        "--group",
        required=True,
        help="VT 组的 groupName（编辑器烘出来的 32 位 hex；材质那条路）",
    )
    parser.add_argument("--item", required=True, help="VT 项的 itemName（同上；材质那条路）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真装（走反向 Δ）；不加它 = 干跑，一个字节都不写（默认）",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="显式干跑（默认就是它；与 --apply 一起给会报错，免得读的人以为哪个才算数）",
    )
    parser.add_argument(
        "--pool",
        help="池子目录（烘出来的那 5 个 .gts/.gtp）：`vt pool install` 拿它当**源**（必填）；"
        "`vt <单位> --pool …` 与 `vt pool status --pool …` 只**读**一眼它在 StreamingAssets"
        " 里就位没有（缺文件时干跑报「有红」、材质那条路 --apply 直接拒绝）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="只对 `vt pool install` 有用：盘上已有同名文件且 sha256 不同时也覆盖（⛔ 默认拒绝）",
    )
    parser.add_argument("--game", help="游戏根目录（默认自动探测）")
    parser.add_argument(
        "--project-root",
        help="工程目录（补丁柜落 <工程目录>\\patches，默认当前目录）",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(args.json_out))
    if args.apply and args.dry_run:
        emitter.failure(
            "--apply 与 --dry-run 互相矛盾",
            hint="默认就是干跑；要真写只写 --apply",
        )
        return EXIT_UNUSABLE
    if args.unit == POOL_ROUTE:
        return _run_pool(args, emitter)
    if args.action is not None:
        emitter.failure(
            f"`{args.action}` 是**池子**那条路的词（vt pool {args.action}）",
            hint="材质那条路没有这个词：vt <单位> --material … --group … --item …",
        )
        return EXIT_UNUSABLE
    if args.force:
        emitter.failure(
            "--force 只对 `vt pool install` 有用",
            hint="材质那条路走反向 Δ，没有「覆盖」这个动作",
        )
        return EXIT_UNUSABLE
    return _run_material(args, emitter)


def _run_material(args: argparse.Namespace, emitter: Emitter) -> int:
    """材质那条路：改 `m_BuildTextureStacks` 的两个既有字段（等长写回）。"""
    # 延迟 import：--help 与其它子命令不许为一个 VT 把 UnityPy / ufs 拉起来。
    from ba_mod_maker.asset_kernel import vt_stack

    game_root = _game_root(args.game)
    if game_root is None:
        emitter.failure(
            "找不到游戏根目录",
            hint="用 --game 显式给一个（例 ...\\steamapps\\common\\broken_arrow）",
        )
        return EXIT_UNUSABLE

    try:
        plan = vt_stack.plan_install(
            game_root, args.unit, material_name=args.material, group=args.group, item=args.item
        )
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    for line in plan.criterion_lines():
        emitter.line(line)

    pool: tuple[Any, ...] = ()
    if args.pool:
        try:
            pool = vt_stack.pool_status(args.pool, vt_stack.streaming_dir_of(game_root))
        except BaModMakerError as exc:
            emitter.failure(exc.message, hint=exc.hint)
            return EXIT_UNUSABLE
        for entry in pool:
            emitter.line(f"  {'✅' if entry.ok else '❌'} 池子 {entry.name}：{entry.state}")
    missing = [entry for entry in pool if not entry.ok]

    if not args.apply:
        emitter.line("  （干跑：一个字节都没写；上面的数字是算出来的真值）")
        if missing:
            emitter.document(
                {
                    **_document(plan, pool, applied=False),
                    "ok": False,
                    "red": [f"池子 {entry.name}：{entry.state}" for entry in missing],
                }
            )
            emitter.error(
                f"有红：池子 {len(missing)}/{len(pool)} 个文件没在 StreamingAssets 里就位",
                hint="先把这些文件拷进 <游戏>\\BrokenArrow_Data\\StreamingAssets\\ 再装材质 ——"
                "否则材质会指向一个加载不到的组（kb/18 §6.2）",
            )
            return EXIT_RED
        emitter.document(_document(plan, pool, applied=False))
        return EXIT_OK

    if missing:
        emitter.failure(
            f"池子没就位（{len(missing)}/{len(pool)} 个文件对不上）—— 拒绝 --apply",
            hint="材质指过去之后游戏会加载不到这个组；先补齐文件（那是「新增文件」那条通道）",
        )
        return EXIT_UNUSABLE

    try:
        report = vt_stack.install(plan, project_root=_project_root(args.project_root))
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_RED
    for line in report.report_lines():
        emitter.line("  " + line)
    emitter.document(_document(plan, pool, applied=True))
    return EXIT_OK


def _document(plan: Any, pool: Any, *, applied: bool) -> dict[str, Any]:
    """`--json` 的那份文档（字段名与 `--help` 里写的对得上）。"""
    return {
        "unit": plan.unit_key,
        "material": plan.material_name,
        "material_path_id": plan.material_path_id,
        #: 来路与同族（2026-10-11 加）：只改 prefab 那几颗时，切涂装会被同族顶掉
        "material_sources": list(plan.material_sources),
        "material_kin": list(plan.material_kin),
        "bundle": plan.bundle_name,
        "old": {"group": plan.old_group, "item": plan.old_item},
        "new": {"group": plan.new_group, "item": plan.new_item},
        "material_bytes": plan.material_bytes,
        "changed_bytes": plan.changed_bytes,
        "changed_spans": [list(span) for span in plan.absolute_spans()],
        "level": plan.writeback.rows[0].level if plan.writeback.rows else "",
        "hit_blocks": plan.writeback.hit_blocks,
        "file_bytes": {
            "before": plan.writeback.old_file_size,
            "after": plan.writeback.new_file_size,
        },
        "catalog_crc": {
            "before": plan.writeback.catalog_crc_old,
            "after": plan.writeback.catalog_crc_new,
        },
        "pool": [
            {
                "name": entry.name,
                "size": entry.size,
                "installed_size": entry.installed_size,
                "state": entry.state,
            }
            for entry in pool
        ],
        "applied": applied,
    }


# --------------------------------------------------------------------------- #
# 池子那条路（`vt pool …`）：`.gts/.gtp` 是「新增文件」，删掉即回退
# --------------------------------------------------------------------------- #
def _run_pool(args: argparse.Namespace, emitter: Emitter) -> int:
    """`vt pool status|install|remove` —— 形状与材质那条路一致（默认干跑）。"""
    if args.action is None:
        emitter.failure(
            "`vt pool` 后面要跟一个动作",
            hint="vt pool status ／ vt pool install --pool <目录> ／ vt pool remove",
        )
        return EXIT_UNUSABLE
    for name in _UNIT_PATH_REQUIRED:
        given = getattr(args, name)
        if given:
            emitter.failure(
                f"池子那条路不吃 --{name}（收到 {given!r}）",
                hint="--material / --group / --item 是**材质**那一跳的参数；"
                "池子这一跳只认 --pool / --game / --apply",
            )
            return EXIT_UNUSABLE
    game_root = _game_root(args.game)
    if game_root is None:
        emitter.failure(
            "找不到游戏根目录",
            hint="用 --game 显式给一个（例 ...\\steamapps\\common\\broken_arrow）",
        )
        return EXIT_UNUSABLE
    if args.action == "install":
        return _pool_install(args, emitter, game_root)
    if args.action == "remove":
        return _pool_remove(args, emitter, game_root)
    if args.action == "stats":
        return _pool_stats(args, emitter, game_root)
    return _pool_status(args, emitter, game_root)


def _pool_stats(args: argparse.Namespace, emitter: Emitter, game_root: Path) -> int:
    """读页表（**只读**，L6）：逐组的地址空间 / 哨兵 / 最大全空矩形 / 分片对账。

    `--pool <目录>` 给的就是**要读的那个目录**（烘出来的池子也能读）；不给则读游戏根的
    `StreamingAssets`。⛔ 这一档不写盘：--apply / --force 一律拒绝（免得读的人以为它写）。
    """
    from ba_mod_maker.asset_kernel import vt_pool

    if args.apply or args.force:
        emitter.failure(
            "`vt pool stats` 是**读数**，不吃 --apply / --force",
            hint="要装/卸池子文件用 `vt pool install` / `vt pool remove`；这一档一个字节都不写",
        )
        return EXIT_UNUSABLE
    target = Path(args.pool) if args.pool else game_root
    try:
        reading = vt_pool.pool_stats(target)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    for line in reading.criterion_lines():
        emitter.line(line)
    emitter.document(
        {
            "action": "pool-stats",
            "readonly": True,
            "streaming_dir": str(reading.streaming_dir),
            "groups": [
                {
                    "file": table.path.name,
                    "bytes": table.size,
                    "layers": table.num_layers,
                    "levels": table.num_levels,
                    "tile_content_px": table.content,
                    "box_pages": list(table.box_pages),
                    "box_px": list(table.box_px),
                    "num_flat_tiles": table.num_flat,
                    "nonempty_total": table.nonempty_total,
                    "sentinel": reading.sentinels[index].sentinel,
                    "sentinel_entries": reading.sentinels[index].entries,
                    "sentinel_ratio": reading.sentinels[index].ratio,
                    "free_rects": [
                        {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h, "px": list(rect.px)}
                        for rect in reading.free[index]
                    ],
                    "shards": [
                        {"index": d.index, "file_id": d.file_id, "bytes": d.size}
                        for d in table.descriptors
                    ],
                }
                for index, table in enumerate(reading.tables)
            ],
        }
    )
    return EXIT_OK


def _pool_status(args: argparse.Namespace, emitter: Emitter, game_root: Path) -> int:
    """查（**只读**）：账本 × 盘上；给了 --pool 再把源那 5 个对一遍（也只用它读数）。"""
    from ba_mod_maker.asset_kernel import vt_pool

    if args.force:
        emitter.failure("--force 只对 `vt pool install` 有用", hint="查盘是读数，没有可强制的")
        return EXIT_UNUSABLE
    try:
        reading = vt_pool.status(game_root, pool_dir=args.pool)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    for line in reading.criterion_lines():
        emitter.line(line)
    document = _pool_document(
        "status",
        applied=False,
        streaming_dir=reading.streaming_dir,
        manifest_path=reading.manifest_path,
        entries=reading.entries,
        installed=reading.ok,
        source_dir=str(reading.source_dir) if reading.source_dir else None,
        source=[
            {
                "name": row.name,
                "size": row.size,
                "installed_size": row.installed_size,
                "state": row.state,
            }
            for row in reading.source
        ],
    )
    if reading.ok:
        emitter.document(document)
        return EXIT_OK
    if reading.manifest is None:
        red = [f"账本不在（{reading.manifest_path}）：这一版没往游戏里装过池子"]
        detail = "账本不在：这一版没往游戏里装过池子"
    else:
        red = [f"{entry.name}：{entry.state}" for entry in reading.missing]
        detail = (
            f"{len(reading.missing)}/{len(reading.entries)} 个池子文件没在 StreamingAssets 里就位"
        )
    emitter.document({**document, "ok": False, "red": red})
    emitter.error(
        f"有红：{detail}",
        hint="装上它们：vt pool install --pool <烘好的池子目录> --apply ——"
        "否则材质指过去之后游戏加载不到那个组（kb/18 §6.2）",
    )
    return EXIT_RED


def _pool_install(args: argparse.Namespace, emitter: Emitter, game_root: Path) -> int:
    """装（**新增文件**）：把这 5 个 .gts/.gtp 加进 StreamingAssets ＋ 写账本。"""
    from ba_mod_maker.asset_kernel import vt_pool

    if not args.pool:
        emitter.failure(
            "`vt pool install` 要 --pool <目录>",
            hint="就是烘出来的那 5 个 .gts/.gtp 所在的目录（⛔ 不是单位键，也不是材质名）",
        )
        return EXIT_UNUSABLE
    try:
        plan = vt_pool.plan_install(args.pool, game_root)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    for line in plan.criterion_lines():
        emitter.line(line)
    # 一趟装**几套**：按 group 各自过一遍形状尺子（⛔ 尺子只有一份 —— vt_bake.pool_sets）
    sets, incomplete = _pool_sets(plan.pool_dir)
    if sets:
        emitter.field("池子套数", f"{len(sets)} 套（按 group 分开装）")
        for got in sets:
            emitter.line(f"  {'✅' if got.ok else '★ 红'} 池子组 {got.account()}")
            for one in got.problems:
                emitter.line(f"      ★ {one}")
    extra = {
        "pool_dir": str(plan.pool_dir),
        "counts": _pool_counts(plan),
        "groups": _groups_json(sets),
    }
    red = [f"拒绝覆盖：{entry.name}（{entry.state} · {entry.shas()}）" for entry in plan.conflicts]
    red += list(incomplete)
    if not args.apply:
        emitter.line("  （干跑：一个字节都没写；上面的 sha256 是算出来的真值）")
        document = _pool_document(
            "install",
            applied=False,
            streaming_dir=plan.streaming_dir,
            manifest_path=plan.manifest_path,
            entries=plan.entries,
            actions=_install_actions(
                add=[e.name for e in plan.new],
                kept=[e.name for e in plan.ready],
                conflict=[e.name for e in plan.conflicts],
            ),
            added=[],
            manifest_written=False,
            **extra,
        )
        if red:
            emitter.document({**document, "ok": False, "red": red})
            emitter.error(
                f"有红：{len(red)} 条（同名却不是这一份 / 池子不全）—— 干跑不写盘",
                hint="看清上面逐条印的两边 sha256 与 ★ 那几行；"
                "同名覆盖要 --force，缺分片要重烘（vt-bake）",
            )
            return EXIT_RED
        emitter.document(document)
        return EXIT_OK
    if incomplete:
        emitter.failure(
            f"池子不完整（{len(incomplete)} 条）—— 拒绝 --apply",
            hint="缺分片的那一套装进去 = 材质指向一个加载不到的组；先补齐（重跑 vt-bake）再装",
        )
        return EXIT_UNUSABLE

    try:
        report = vt_pool.install(plan, force=bool(args.force))
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_RED
    for line in report.report_lines():
        emitter.line(line)
    # 写后复核（AGENTS §5）：装完**再读一遍盘**，⛔ 不信刚写的那几个变量。
    try:
        after = vt_pool.plan_install(args.pool, game_root)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_RED
    if after.conflicts or not all(entry.ok for entry in after.entries):
        emitter.failure(
            "写后复核不过：装完再读一遍，盘上还有文件对不上",
            hint="先跑 `vt pool status` 看是哪几个；⛔ 这一版别把材质指过去",
        )
        return EXIT_RED
    emitter.document(
        _pool_document(
            "install",
            applied=True,
            streaming_dir=plan.streaming_dir,
            manifest_path=plan.manifest_path,
            entries=after.entries,
            actions=_install_actions(add=report.added, kept=report.kept),
            added=list(report.added),
            manifest_written=report.manifest_written,
            **extra,
        )
    )
    return EXIT_OK


def _pool_remove(args: argparse.Namespace, emitter: Emitter, game_root: Path) -> int:
    """卸：**只删账本里记着、且 sha256 相符的那些**（被改过的拒绝删）。"""
    from ba_mod_maker.asset_kernel import vt_pool

    if args.force:
        emitter.failure(
            "--force 只对 `vt pool install` 有用",
            hint="删这一步没有「强制」：被改过的**一个都不删**（那已经不是我们装的那一份）",
        )
        return EXIT_UNUSABLE
    if args.pool:
        emitter.failure(
            "`vt pool remove` 不吃 --pool",
            hint="它只按**账本**删（账本就在 StreamingAssets 里）——源目录换成哪儿都不影响要删什么",
        )
        return EXIT_UNUSABLE
    try:
        plan = vt_pool.plan_remove(game_root)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    for line in plan.criterion_lines():
        emitter.line(line)
    counts = {
        "deletable": len(plan.deletable),
        "refused": len(plan.refused),
        "gone": len(plan.gone),
    }
    red = [f"拒绝删：{entry.name}（{entry.state} · {entry.shas()}）" for entry in plan.refused]
    if plan.manifest is None:
        emitter.line("  （没装过：没有可删的 —— 一个字节都没动）")
    if plan.manifest is None or not args.apply:
        if plan.manifest is not None:
            emitter.line("  （干跑：一个字节都没删；上面的 sha256 是算出来的真值）")
        document = _pool_document(
            "remove",
            applied=False,
            streaming_dir=plan.streaming_dir,
            manifest_path=plan.manifest_path,
            entries=plan.entries,
            actions=_remove_actions(
                delete=[e.name for e in plan.deletable],
                refused=[e.name for e in plan.refused],
                gone=[e.name for e in plan.gone],
            ),
            counts=counts,
            deleted=[],
            manifest_removed=False,
        )
        if red:
            emitter.document({**document, "ok": False, "red": red})
            emitter.error(
                f"有红：{len(plan.refused)} 个文件在盘上、但不是我们装的那一份（sha256 不同）",
                hint="被改过的一个都不删；先看清是谁改的，或者手工处置",
            )
            return EXIT_RED
        emitter.document(document)
        return EXIT_OK
    try:
        report = vt_pool.remove(plan)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_RED
    for line in report.report_lines():
        emitter.line(line)
    document = _pool_document(
        "remove",
        applied=True,
        streaming_dir=plan.streaming_dir,
        manifest_path=plan.manifest_path,
            entries=plan.entries,
            actions=_remove_actions(
                delete=report.deleted,
                refused=report.refused,
                gone=[e.name for e in plan.entries if e.absent],
            ),
            counts=counts,
            deleted=list(report.deleted),
        manifest_removed=report.manifest_removed,
    )
    if report.refused:
        emitter.document({**document, "ok": False, "red": red})
        emitter.error(
            f"有红：{len(report.refused)} 个文件没删（盘上那个不是我们装的那一份）",
            hint="上面逐条印了两边的 sha256；⛔ 不改它就永远删不掉",
        )
        return EXIT_RED
    emitter.document(document)
    return EXIT_OK


def _pool_sets(pool_dir: Path) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    """池子目录里按 group 分出来的几套 ＋ 不完整的那几条。

    ⛔ 尺子只有一份（`vt_bake.pool_sets`）；这里传 `size_check=False` ——
    装那一步判的是"这一套齐不齐"，"页表该多大"是**烘**那一步的判据。
    """
    from ba_mod_maker.asset_kernel import vt_bake

    files = tuple(
        sorted(
            path
            for path in pool_dir.iterdir()
            if path.is_file() and path.suffix in vt_bake.POOL_SUFFIXES
        )
    )
    sets = vt_bake.pool_sets(files, size_check=False)
    problems = tuple(f"池子组 {got.group}：{one}" for got in sets for one in got.problems)
    return sets, problems


def _groups_json(sets: Any) -> list[dict[str, Any]]:
    """那几套池子的机器可读账（`--json` 用）。"""
    return [
        {
            "group": got.group,
            "files": [path.name for path in got.files],
            "bytes": got.size,
            "ok": got.ok,
            "problems": list(got.problems),
        }
        for got in sets
    ]


def _pool_counts(plan: Any) -> dict[str, int]:
    """装那一步的三档计数（干跑与 --json 共用一份口径）。"""
    return {"add": len(plan.new), "kept": len(plan.ready), "conflict": len(plan.conflicts)}


def _install_actions(*, add: Any = (), kept: Any = (), conflict: Any = ()) -> dict[str, str]:
    """装：每个文件这一趟对它做什么（`applied` 说明这事**做了**还是**将要做**）。"""
    out = {name: "conflict" for name in conflict}
    out.update({name: "add" for name in add})
    out.update({name: "kept" for name in kept})
    return out


def _remove_actions(*, delete: Any = (), refused: Any = (), gone: Any = ()) -> dict[str, str]:
    """卸：同上（`delete` / `refused` / `gone` 三档）。"""
    out = {name: "gone" for name in gone}
    out.update({name: "delete" for name in delete})
    out.update({name: "refused" for name in refused})
    return out


def _pool_document(
    action: str,
    *,
    applied: bool,
    streaming_dir: Path,
    manifest_path: Path,
    entries: Any,
    actions: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """池子那条路的 `--json` 文档（三条命令共用一份抬头）。"""
    return {
        "action": action,
        "applied": applied,
        "streaming_dir": str(streaming_dir),
        "manifest": str(manifest_path),
        "files": [
            {
                "name": entry.name,
                "size": entry.size,
                "sha256": entry.sha256,
                "state": entry.state,
                "installed_size": entry.installed_size,
                "installed_sha256": entry.installed_sha256,
                "ok": entry.ok,
                "action": (actions or {}).get(entry.name, ""),
            }
            for entry in entries
        ],
        **extra,
    }


def _game_root(text: str | None) -> Path | None:
    """游戏根目录：给了就用，没给就自动探测（与 `__main__._game_root` 同一口径）。"""
    from ba_mod_maker.core.assets import game as game_mod

    return Path(text) if text else game_mod.find_game_root()


def _project_root(text: str | None) -> Path:
    """工程目录口径：给了就用，没给就当前目录（补丁柜落 <工程目录>\\patches）。"""
    return Path(text) if text else Path.cwd()
