"""`object container` 子命令：**地址级容器条目**（`AssetBundle.m_Container` 的新增 / 删除）。

## 这一层收的是哪件事

catalog 那半张「地址 -> 资产」的表在 `catalog_edit`；**包里这半张**是
`AssetBundle.m_Container`（`{first: 地址, second: {preloadIndex, preloadSize, asset: PPtr}}`）。
游戏取资产走的是**包这半张**（`unitypy_source.open_unique_bundle` 就是拿它当"第二把钥匙"
复核唯一命中）——所以「新增一个能被地址取到的资产」缺的正是这一格：

    ba-mod-maker object container add    --bundle <包> --path-id <资产 pathID> ^
        --asset-path <Assets/...> [--container-path-id <AssetBundle 的 pathID>] [--write]
    ba-mod-maker object container remove --bundle <包> --asset-path <Assets/...> ^
        [--container-path-id <AssetBundle 的 pathID>] [--write]

⛔ 它动的是**包里的 AssetBundle 对象**，与 `object set / add / delete` 同层（都是「包 ＋ pathID」）
—— 与 `address`（catalog 那半张）是**两半**，两边都要有才取得到。

## 三种跑法（照 `cli/address_edit.py` 的最短形状）

| 跑法 | 盘上会发生什么 |
|---|---|
| 默认 | **干跑**：算出读数、印判据，**一个字节都不写** |
| `--dry-run` | 与默认同义（留给"我就是要干跑"的脚本） |
| `--write` | **真写**：install（Δ ＋ 反向 Δ 进补丁柜）-> verify_plan 读回逐条核对 |

`--dry-run` 与 `--write` 同时给 = 退出码 2（⛔ 不猜用户要哪个）。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 算出来了（干跑）/ `--write` 写完且写后复核逐条相同 |
| 1 | **结果不干净**：写后读回与期望不同，或写回计划被拦 |
| 2 | 用不了：参数 / 包 / 守卫不过（条目已存在 · 资产名撞名 · 资产不在包里 · 地址不在容器里） |

## 接线（⛔ 不在本模块里 —— `cli/__main__.py` 归 Lead）

它是 `object` 组的**第三个子组**（不是顶层命令）。`object` 由
`object_edit.add_parser(sub)` 建；本模块的 `add_parser` 接**顶层 sub 或那个 object parser**
都行：

    object_parser = object_edit.add_parser(sub)      # 现有那一行接住返回值
    object_container.add_parser(object_parser)       # 新增这一行（必须排在 object 之后）

分派：把 `handlers` 里那一行 `"object": object_edit.run,` 换成
`"object": object_container.dispatch,` —— `dispatch` 只把
`object_action == "container"` 转给本模块，其余原样转 `object_edit.run`
（`⛔ 不改 object_edit.py`）。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import renderer_slots
from ba_mod_maker.cli import object_edit
from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import BaModMakerError

__all__ = [
    "EXIT_DIRTY",
    "EXIT_DONE",
    "EXIT_UNUSABLE",
    "GROUP",
    "VERBS",
    "add_parser",
    "build_plan",
    "dispatch",
    "run",
]

#: 退出码（与 address / material / texture 同一套语义）。
EXIT_DONE = 0
#: **结果不干净**：写后读回与期望不同 / 写回计划被拦（⛔ 空改动走 2 —— 库层直接抛）。
EXIT_DIRTY = 1
#: 用不了：参数 / 包 / 守卫。
EXIT_UNUSABLE = 2

#: 本模块在 `object` 组里那一格的名字。
GROUP = "container"
#: 两个原子动词（顺序 = `--help` 里的顺序）。
VERBS: tuple[str, ...] = ("add", "remove")

_DESCRIPTION = """地址级容器条目：AssetBundle.m_Container 的新增 / 删除。

  object container add    --bundle <包> --path-id <资产 pathID> --asset-path <Assets/...>
                          [--container-path-id <AssetBundle 的 pathID>]
  object container remove --bundle <包> --asset-path <Assets/...>
                          [--container-path-id <AssetBundle 的 pathID>]

它改的是**包里的 AssetBundle 对象**（与 object set / add / delete 同层）；
catalog 那半张「地址 -> 资产」的表见 address 那一组 —— 两边都要有，游戏才取得到。
默认**干跑**（一个字节都不写）；--write 才落字节（Δ ＋ 反向 Δ 进补丁柜 ＋ 写后读回核对）。

退出码：
  0  算出来了 / --write 已写回且写后复核逐条相同
  1  结果不干净（写后读回与期望不同 / 写回计划被拦）
  2  用不了（参数不对 / 包读不开 / 守卫不过：条目已存在 / 资产名撞名 / 资产 pathID 不在包里 /
     地址不在容器里 / 空改动）
"""

_EPILOG = """--asset-path           容器条目的 first = 地址（catalog 的 m_InternalIds 那条），
  例 Assets/Resources_moved/ModelPrefabs/My.prefab。⛔ 不是 Addressables 的 key。

--path-id              add 用：**资产自己的 pathID**（容器条目 second.asset 指过去的那一个）。
  必须在包里（包外 = 悬空 PPtr，游戏 LoadAsset 取到空）。

--container-path-id    收这条容器条目的 **AssetBundle 对象的 pathID**；不给就必须**唯一**
  （包里只有一个 AssetBundle —— 两个以上会并列报错，⛔ 不猜）。

--catalog / --game     只有**真件包**才要给（成对更新 catalog 的 m_Crc / m_BundleSize）；
  离线件 / 合成件不给 ⇒ 判据一行里写「未同步（离线件）」，游戏目录一个字节都不碰。
--project-root         补丁柜落点（<它>/patches/，默认当前目录）。

例：
  ba-mod-maker object container add --bundle units.bundle --path-id 7952762356820983200 ^
      --asset-path Assets/Resources_moved/ModelPrefabs/My.prefab --project-root D:/proj
  ba-mod-maker object container remove --bundle units.bundle ^
      --asset-path Assets/Resources_moved/ModelPrefabs/My.prefab --write
"""


# --------------------------------------------------------------------------- #
# 注册（挂进既有的 object 组）
# --------------------------------------------------------------------------- #
def _object_parser(sub: Any) -> argparse.ArgumentParser:
    """拿到 `object` 那个 parser：给的就是它，或者从顶层 `sub.choices` 里取。"""
    if isinstance(sub, argparse.ArgumentParser):
        return sub
    found = getattr(sub, "choices", {}).get("object")
    if found is None:
        raise RuntimeError(
            "object 组还没注册：先把 object_edit.add_parser(sub) 的返回值传给本函数"
            "（本模块是 object 的子组，⛔ 不是顶层命令）"
        )
    return found


def _object_verbs(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    """`object` 上那个既有的 `add_subparsers`（`object_edit` 建的那个）。"""
    for action in parser._actions:  # noqa: SLF001 -- argparse 没有公开的「取已有子命令表」的口子
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 -- 同上
            return action
    raise RuntimeError("object parser 上没有子命令表 —— 它不是 object_edit.add_parser 交出来的那个")


def add_parser(parent: Any) -> argparse.ArgumentParser:
    """把 `container` 挂进 `object` 组（`__main__.py` 只调这一句）。

    `parent` 给**顶层 sub**（会自己去 `choices["object"]` 取）或直接给
    `object_edit.add_parser(sub)` 的返回值，两种都行。
    """
    verbs = _object_verbs(_object_parser(parent))
    parser = verbs.add_parser(
        GROUP,
        help="地址级容器条目：AssetBundle.m_Container 的新增 / 删除（退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="container_action", required=True)

    p = actions.add_parser(
        "add",
        help="加一条容器条目（地址 -> 资产 pathID；条目已存在 / 资产名撞名 / 资产不在包里都拒绝）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--path-id",
        dest="path_id",
        type=int,
        required=True,
        help="**资产自己的** pathID（容器条目 second.asset 指过去的那个；必须在包里）",
    )
    _add_asset_path(p)
    _add_output(p)

    p = actions.add_parser(
        "remove",
        help="摘掉一条容器条目（地址不在容器里就报错退出，不静默成功）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    _add_asset_path(p)
    _add_output(p)

    return parser


def _add_locator(parser: argparse.ArgumentParser) -> None:
    """两个动作共用的落点：哪个包、包里哪个 AssetBundle。"""
    parser.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")


def _add_asset_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--asset-path",
        dest="asset_path",
        required=True,
        help="容器条目的地址（catalog 的 m_InternalIds 那条，例 Assets/Mods/My/icon.png）",
    )
    parser.add_argument(
        "--container-path-id",
        dest="container_path_id",
        type=int,
        default=None,
        help="收这条条目的 AssetBundle 对象的 pathID（不给就必须**唯一**；两个以上会并列拒绝）",
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    """两个动作共用的输出格（与 address / material 两组同形）。"""
    parser.add_argument("--project-root", help="补丁柜的落点（<它>/patches/，默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只算不写（与默认同义）")
    parser.add_argument(
        "--write", action="store_true", help="真写：落字节 ＋ 反向 Δ 进补丁柜 ＋ 写后读回核对"
    )
    parser.add_argument("--game", help="--write 用：游戏根（只有真件包才要给）")
    parser.add_argument(
        "--catalog",
        help="--write 用：catalog.json / catalog.bin（真件包成对更新 m_Crc / m_BundleSize）"
        "· ⚠ 它**不动** aa/catalog.hash（要两个文件一起管走 catalog crc）",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )


# --------------------------------------------------------------------------- #
# 计划（干跑）：全转 renderer_slots 那一节，这里不自己碰字节
# --------------------------------------------------------------------------- #
def build_plan(args: argparse.Namespace) -> renderer_slots.ContainerPlan:
    """**只读**：按 `args` 算出一份写回计划（⛔ 一个字节都没写）。"""
    verb = str(getattr(args, "container_action", "") or "")
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    asset_path = str(getattr(args, "asset_path", "") or "")
    container_path_id = getattr(args, "container_path_id", None)
    common: dict[str, Any] = {
        "container_path_id": container_path_id,
        "catalog_path": getattr(args, "catalog", None),
        "game_root": getattr(args, "game", None),
    }
    if verb == "add":
        return renderer_slots.plan_add_container(
            bundle,
            asset_path=asset_path,
            asset_id=int(getattr(args, "path_id", 0) or 0),
            **common,
        )
    if verb == "remove":
        return renderer_slots.plan_remove_container(bundle, asset_path=asset_path, **common)
    raise renderer_slots.AssetError(
        f"不认识的动作 {verb!r}", hint=f"这一组只有 {' / '.join(VERBS)} 两个"
    )


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def dispatch(args: argparse.Namespace) -> int:
    """`object` 组的**入口**：`object_action == "container"` 走本模块，
    其余原样转 `object_edit.run`。

    ⛔ 不改 `object_edit.py`：`handlers["object"]` 换成这一个函数，`object` 那三个
    老动词逐字不变。
    """
    if str(getattr(args, "object_action", "") or "") == GROUP:
        return run(args)
    return object_edit.run(args)


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    verb = str(getattr(args, "container_action", "") or "")
    if verb not in VERBS:
        emitter.failure(f"不认识的动作 {verb!r}", hint=f"这一组只有 {' / '.join(VERBS)} 两个")
        return EXIT_UNUSABLE
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    if not bundle.is_file():
        emitter.failure(
            f"打不开包 {bundle}", hint="--bundle 给的是一个 UnityFS 容器（.bundle / data.unity3d）"
        )
        return EXIT_UNUSABLE
    dry_run = bool(getattr(args, "dry_run", False))
    write = bool(getattr(args, "write", False))
    if dry_run and write:
        emitter.failure(
            "--dry-run 与 --write 不能同时给（一个字节都不写 vs 真写）",
            hint="先干跑看一眼判据，确认了再单独跑一次 --write",
        )
        return EXIT_UNUSABLE
    try:
        plan = build_plan(args)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读不了 {bundle}：{exc}")
        return EXIT_UNUSABLE
    project_root = Path(str(getattr(args, "project_root", None) or Path.cwd()))
    report: Any = None
    verify_lines: tuple[str, ...] = ()
    verify_ok = True
    if write:
        try:
            report = renderer_slots.install(plan, project_root=project_root, dry_run=False)
        except (BaModMakerError, OSError) as exc:
            emitter.failure(f"写回失败：{exc}", hint=f"{bundle}（走到这一步之前写回没动手）")
            return EXIT_UNUSABLE
        try:
            verify_lines = renderer_slots.verify_plan(plan)
        except (BaModMakerError, OSError) as exc:
            verify_lines = (f"写后复核不过：{exc}",)
            verify_ok = False
    reds = _red_verifies(report)
    _report(
        emitter,
        plan=plan,
        verb=verb,
        report=report,
        verify_lines=verify_lines,
        verify_ok=verify_ok,
        reds=reds,
    )
    emitter.document(
        _document(
            plan=plan,
            verb=verb,
            report=report,
            verify_lines=verify_lines,
            verify_ok=verify_ok,
            reds=reds,
        )
    )
    if not verify_ok or reds:
        return EXIT_DIRTY
    if report is not None and not bool(getattr(report, "wrote_bundle", False)):
        return EXIT_DIRTY
    if str(getattr(plan.writeback, "blocked_reason", "") or ""):
        return EXIT_DIRTY
    return EXIT_DONE


def _red_verifies(report: Any) -> tuple[str, ...]:
    """writer 的写后复核里那些**红**（ok: false）—— 空元组 = 全绿。"""
    verify = getattr(report, "verify", None)
    if not isinstance(verify, Mapping):
        return ()
    return tuple(
        str(key)
        for key, value in verify.items()
        if isinstance(value, Mapping) and not value.get("ok")
    )


def _report(
    emitter: Emitter,
    *,
    plan: renderer_slots.ContainerPlan,
    verb: str,
    report: Any,
    verify_lines: tuple[str, ...],
    verify_ok: bool,
    reds: tuple[str, ...],
) -> None:
    """人读那几行：**一次动作只报一条判据**（条数 ＋ 对象字节 ＋ 写回档 ＋ catalog）。"""
    emitter.field("包", plan.bundle)
    emitter.field("动作", f"容器条目·{'新增' if verb == 'add' else '删除'}")
    emitter.field("对象", f"AssetBundle『{plan.container_name}』 pathID={plan.container_path_id}")
    emitter.line(f"[判据] {plan.count_line()}")
    if report is None:
        # 干跑：判据那一块就是 writer 的计划读数（与真写**逐行相同**的那一份）。
        for line in plan.criterion_lines():
            emitter.line(f"  {line}")
        emitter.field("写盘", "（干跑：一个字节都没写；加 --write 才落字节）")
        return
    for line in report.report_lines():
        emitter.line(f"  {line}")
    if verify_lines:
        emitter.line("[写后复核·读回]")
        for line in verify_lines:
            emitter.line(f"  {line}")
    if reds:
        emitter.line(f"★ 结果不干净：写后复核有红 {list(reds)}")
    emitter.field("写盘", f"（{'已写回' if report.wrote_bundle else '没写'}）")
    if report.patch is not None:
        emitter.field(
            "反向 Δ", f"{report.patch}（{report.patch_bytes:,} B）  还原走 assets restore"
        )


def _document(
    *,
    plan: renderer_slots.ContainerPlan,
    verb: str,
    report: Any,
    verify_lines: tuple[str, ...],
    verify_ok: bool,
    reds: tuple[str, ...],
) -> dict[str, Any]:
    """`--json` 那一份（只留标量：整条容器不塞进文档）。"""
    return {
        "group": GROUP,
        "verb": verb,
        "operation": str(plan.operation),
        "bundle": str(plan.bundle),
        "bundle_name": str(plan.bundle_name),
        "container_path_id": int(plan.container_path_id),
        "container_name": str(plan.container_name),
        "asset_path": str(plan.asset_path),
        "path_id": int(plan.path_id),
        "level": str(plan.level),
        "count_before": len(plan.entries_before),
        "count_after": len(plan.entries_after),
        "paths_before": [entry.asset_path for entry in plan.entries_before],
        "paths_after": [entry.asset_path for entry in plan.entries_after],
        "catalog": str(plan.catalog_path) if plan.catalog_path is not None else None,
        "dry_run": report is None or bool(getattr(report, "dry_run", False)),
        "blocked_reason": str(getattr(plan.writeback, "blocked_reason", "") or ""),
        "wrote_bundle": bool(getattr(report, "wrote_bundle", False)),
        "patch": str(report.patch) if report is not None and report.patch else None,
        "patch_bytes": int(getattr(report, "patch_bytes", 0) or 0),
        "verify_ok": bool(verify_ok) and not reds,
        "verify_lines": list(verify_lines),
        "verify_reds": list(reds),
    }
