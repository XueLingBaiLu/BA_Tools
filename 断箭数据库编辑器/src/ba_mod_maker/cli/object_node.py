r"""`object node` / `object component` 子命令：**树形状**的新增 / 删除（干跑 ＋ 写 ＋ 写后复核）。

## 这一层收的是哪件事

`object set/add/delete` 收**对象级**（一个 pathID 的字节）；`object container` 收**地址级**
（`AssetBundle.m_Container`）。**树的形状**（有几个节点、每个节点挂什么组件）两者都到不了 ——
本模块补的就是这一格，四个原子动作：

    ba-mod-maker object node list      --bundle <包>
    ba-mod-maker object node add       --bundle <包> --parent <节点|pathID> --name <名字>
                                       [--kind empty|mesh] [--mesh <pathID>] [--material <pathID> …]
    ba-mod-maker object node delete    --bundle <包> --node <节点|pathID>
    ba-mod-maker object component add  --bundle <包> --node <节点|pathID>
        (--like <包内既有组件 pathID> 或 --class-id <N> [--type-id <N>])
    ba-mod-maker object component delete --bundle <包> --node <节点|pathID> --component <pathID>

⛔ **没有「换节点」「换组件」** —— 要换就 `delete` 旧的 ＋ `add` 新的（AGENTS §0.10：产品只有
新增 / 删除 / 修改三种原子动作）。

## 三种跑法

| 跑法 | 盘上会发生什么 |
|---|---|
| 默认 | **干跑**：算读数、印判据，**一个字节都不写** |
| `--dry-run` | 与默认同义（留给「我就是要干跑」的脚本） |
| `--write` | **真写**：按 `NodePlan.stages` 逐段落字节 ＋ **写后读回逐条核对** |

`--dry-run` 与 `--write` 同时给 = 退出码 2（⛔ 不猜用户要哪个）。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 算出来了（干跑）/ `--write` 写完且写后复核逐条相同 |
| 1 | **结果不干净**：写后读回与期望不同 / writer 的写后复核有红 / 计划被拦 |
| 2 | 用不了：参数 / 包 / 守卫不过（节点路径命中多个 / peer 不在包里 / 根删不得 / 悬空 PPtr） |

## 接线（⛔ 不在本模块里 —— `cli/__main__.py` 归 Lead）

    object_parser = object_edit.add_parser(sub)
    object_container.add_parser(object_parser)
    object_node.add_parser(object_parser)          # 新增这一行

分派：`"object": object_node.dispatch` —— 它认 `node` / `component` 两组，
`container` 转 `object_container.run`，其余原样转 `object_edit.run`。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import node_assembly
from ba_mod_maker.cli import object_container, object_edit
from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import BaModMakerError

__all__ = [
    "EXIT_DIRTY",
    "EXIT_DONE",
    "EXIT_UNUSABLE",
    "GROUPS",
    "VERBS",
    "add_parser",
    "build_plan",
    "dispatch",
    "run",
]

EXIT_DONE = 0
#: **结果不干净**：写后读回与期望不同 / writer 的写后复核有红。
EXIT_DIRTY = 1
#: 用不了：参数 / 包 / 守卫。
EXIT_UNUSABLE = 2

#: 本模块在 `object` 组里占的两格。
GROUPS: tuple[str, ...] = ("node", "component")
#: 四个原子动词（顺序 = `--help` 里的顺序）。
VERBS: tuple[str, ...] = ("list", "add", "delete")

_DESCRIPTION = """树形状：新增 / 删除节点与组件（干跑 + 写 + 写后复核）。

  object node list   --bundle <包>
  object node add    --bundle <包> --parent <节点|pathID> --name <名字> [--kind empty|mesh]
  object node delete --bundle <包> --node <节点|pathID>
  object component add    --bundle <包> --node <节点|pathID> --like <包内既有组件 pathID>
  object component delete --bundle <包> --node <节点|pathID> --component <pathID>

节点落点两种写法都认：**路径**（`RU_T72B_Base/root/body`，从顶层节点名起）或 **pathID**。
默认**干跑**（一个字节都不写）；--write 才落字节（Δ ＋ 反向 Δ 进补丁柜 ＋ 写后读回核对）。
⛔ 没有「换」：要换就 delete 旧的 + add 新的。

退出码：
  0  算出来了 / --write 已写回且写后复核逐条相同
  1  结果不干净（写后读回与期望不同 / 写回计划被拦）
  2  用不了（参数不对 / 包读不开 / 守卫不过：路径命中多个 / 对象不在包里 / 要删根 / 悬空 PPtr）
"""

_EPILOG = """--node / --parent      节点落点：路径或 pathID。路径从**顶层节点名**起
   （例 RU_T72B_Base/root/body/RR001）—— 先 `object node list` 看一遍。
   路径命中多个（重名兄弟）时**并列报错**，⛔ 不猜。

--kind                empty = 只造 GameObject + Transform；mesh = 再加 MeshFilter + MeshRenderer
   （--mesh / --material 都要是**包内**的 pathID，包外就是悬空 PPtr，当场拒）。

--like                照包内**既有那颗组件**造（类型树 ＋ m_Script 都照抄）——
   MonoBehaviour 只能走这条：我们自己包里没有 MonoScript，凭空写 pathID 会让
   出厂闸 A0（Missing Script）当场红。

--class-id / --type-id  只用类型树的默认值；一个 classID 有多棵类型树时必须点名 --type-id。

--write               真写：按计划逐段落字节 ＋ 写后读回逐条核对（读回不过 = 退出码 1）。
--project-root        补丁柜落点（<它>/patches/，默认当前目录）。

例：
  ba-mod-maker object node list --bundle units.bundle
  ba-mod-maker object node add --bundle units.bundle --parent RU_T72B_Base/root ^
      --name L7_TEST --kind empty
  ba-mod-maker object component add --bundle units.bundle --node RU_T72B_Base/root ^
      --like -8283895740826320088
  ba-mod-maker object node delete --bundle units.bundle --node RU_T72B_Base/root/R004 --write
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


def _add_output(parser: argparse.ArgumentParser) -> None:
    """四个动作共用的输出格（与 `object container` 同形）。"""
    parser.add_argument("--project-root", help="补丁柜的落点（<它>/patches/，默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只算不写（与默认同义）")
    parser.add_argument(
        "--write", action="store_true", help="真写：落字节 ＋ 反向 Δ 进补丁柜 ＋ 写后读回核对"
    )
    parser.add_argument("--game", help="--write 用：游戏根（只有真件包才要给）")
    parser.add_argument(
        "--catalog", help="--write 用：catalog.json（真件包成对更新 m_Crc / m_BundleSize）"
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )


def add_parser(parent: Any) -> argparse.ArgumentParser:
    """把 `node` / `component` 两组挂进 `object`（`__main__.py` 只调这一句）。"""
    verbs = _object_verbs(_object_parser(parent))
    node = verbs.add_parser(
        "node",
        help="树的形状：节点的列出 / 新增 / 删除（退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = node.add_subparsers(dest="node_action", required=True)

    p = actions.add_parser("list", help="列出这棵 prefab 的节点（只读）")
    p.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    _add_output(p)

    p = actions.add_parser(
        "add", help="新增一个节点（GameObject ＋ Transform；--kind mesh 再加两个渲染器件）"
    )
    p.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    p.add_argument("--parent", required=True, help="挂到哪个节点下（路径或 pathID）")
    p.add_argument("--name", required=True, help="新节点的名字（引擎按名字找节点，必有）")
    p.add_argument(
        "--kind",
        choices=list(node_assembly.KINDS),
        default="empty",
        help="empty = 空节点；mesh = 静态网格节点（要 --mesh）",
    )
    p.add_argument("--mesh", type=int, help="--kind mesh：包内 Mesh 的 pathID")
    p.add_argument(
        "--material",
        dest="materials",
        type=int,
        action="append",
        default=[],
        help="--kind mesh：包内 Material 的 pathID（可给多次，顺序 = 槽序）",
    )
    p.add_argument("--position", default="0,0,0", help="局部坐标 x,y,z（默认 0,0,0）")
    _add_output(p)

    p = actions.add_parser("delete", help="删掉一颗节点（整棵子树；根删不得）")
    p.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    p.add_argument("--node", required=True, help="要删的节点（路径或 pathID）")
    _add_output(p)

    comp = verbs.add_parser(
        "component",
        help="节点上挂的组件：新增 / 删除（退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cverbs = comp.add_subparsers(dest="component_action", required=True)

    p = cverbs.add_parser("add", help="给一个节点加一颗组件（--like 照既有那颗造）")
    p.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    p.add_argument("--node", required=True, help="挂到哪个节点上（路径或 pathID）")
    p.add_argument("--like", type=int, help="照**包内这颗既有组件**造（MonoBehaviour 只能走这条）")
    p.add_argument("--class-id", dest="class_id", type=int, help="只用类型树默认值：classID")
    p.add_argument("--type-id", dest="type_id", type=int, help="类型表项号（一项多项时点名它）")
    p.add_argument("--name", default="", help="可读名（只进判据一行，不参与字节）")
    _add_output(p)

    p = cverbs.add_parser("delete", help="从节点上摘掉一颗组件（对象表条目一并摘）")
    p.add_argument("--bundle", required=True, help="UnityFS 包（.bundle / data.unity3d）")
    p.add_argument("--node", required=True, help="组件挂在哪个节点上（路径或 pathID）")
    p.add_argument("--component", type=int, required=True, help="那一颗组件的 pathID")
    _add_output(p)

    return node


# --------------------------------------------------------------------------- #
# 计划（干跑）：全转 node_assembly，这里不自己碰字节
# --------------------------------------------------------------------------- #
def build_plan(args: argparse.Namespace) -> node_assembly.NodePlan | None:
    """**只读**：按 `args` 算出一份写回计划（⛔ 一个字节都没写）。`list` 返回 None。"""
    action = str(getattr(args, "object_action", "") or "")
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    if action == "node":
        verb = str(getattr(args, "node_action", "") or "")
        if verb == "list":
            return None
        if verb == "add":
            return node_assembly.plan_add_node(
                bundle,
                parent=args.parent,
                name=args.name,
                kind=str(args.kind),
                mesh=args.mesh,
                materials=tuple(int(m) for m in (args.materials or ())),
                position=_floats(args.position, 3, "--position"),
            )
        if verb == "delete":
            return node_assembly.plan_delete_node(bundle, node=args.node)
        raise BaModMakerError(f"不认识的动作 {verb!r}", hint="这一组只有 list / add / delete")
    verb = str(getattr(args, "component_action", "") or "")
    if verb == "add":
        return node_assembly.plan_add_component(
            bundle,
            node=args.node,
            like=getattr(args, "like", None),
            class_id=getattr(args, "class_id", None),
            type_id=getattr(args, "type_id", None),
            name=str(getattr(args, "name", "") or ""),
        )
    if verb == "delete":
        return node_assembly.plan_delete_component(
            bundle, node=args.node, component=int(args.component)
        )
    raise BaModMakerError(f"不认识的动作 {verb!r}", hint="这一组只有 add / delete")


def _floats(text: str, count: int, what: str) -> tuple[float, ...]:
    raw = [piece for piece in str(text).replace("，", ",").split(",") if piece.strip()]
    if len(raw) != count:
        raise BaModMakerError(
            f"{what} 要 {count} 个小数（逗号分隔），拿到 {len(raw)} 个",
            hint="例：--position 0,1.5,0",
        )
    try:
        return tuple(float(piece) for piece in raw)
    except ValueError as exc:
        raise BaModMakerError(f"{what} 里有不是小数的东西：{text!r}") from exc


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def dispatch(args: argparse.Namespace) -> int:
    """`object` 组的**入口**：`node` / `component` 走本模块，其余转出去。

    `container` 转 `object_container.run`，其余原样转 `object_edit.run`。

    ⛔ 不改 `object_edit.py` / `object_container.py`：`handlers["object"]` 换成这一个函数，
    那几个老动词逐字不变。
    """
    action = str(getattr(args, "object_action", "") or "")
    if action in GROUPS:
        return run(args)
    if action == object_container.GROUP:
        return object_container.run(args)
    return object_edit.run(args)


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
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
    if str(getattr(args, "object_action", "")) == "node" \
            and str(getattr(args, "node_action", "")) == "list":
        return _do_list(emitter, bundle)
    try:
        plan = build_plan(args)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读不了 {bundle}：{exc}")
        return EXIT_UNUSABLE
    if plan is None:  # pragma: no cover -- list 已经在上面分流了
        emitter.failure("这个动作没有计划")
        return EXIT_UNUSABLE
    project_root = Path(str(getattr(args, "project_root", None) or Path.cwd()))
    report: Any = None
    verify_lines: tuple[str, ...] = ()
    verify_ok = True
    if write:
        try:
            reports = node_assembly.write_plan(
                plan,
                project_root=project_root,
                game_root=getattr(args, "game", None),
                catalog_path=getattr(args, "catalog", None),
                dry_run=False,
            )
        except (BaModMakerError, OSError) as exc:
            hint = getattr(exc, "hint", "") or f"{bundle}"
            emitter.failure(f"写回失败：{exc}", hint=hint)
            return EXIT_UNUSABLE
        report = reports[-1] if reports else None
        try:
            verify_lines = node_assembly.verify_plan(plan)
        except (BaModMakerError, OSError) as exc:
            verify_lines = (f"写后复核不过：{exc}",)
            verify_ok = False
    reds = _red_verifies(report)
    _report(
        emitter,
        plan=plan,
        report=report,
        verify_lines=verify_lines,
        verify_ok=verify_ok,
        reds=reds,
        project_root=project_root,
    )
    emitter.document(
        _document(
            plan=plan,
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
    return EXIT_DONE


def _do_list(emitter: Emitter, bundle: Path) -> int:
    """`object node list`：只读，把节点路径与组件逐行印出来。"""
    try:
        with node_assembly.open_assembly(bundle) as view:
            lines = view.node_lines()
    except (BaModMakerError, OSError) as exc:
        emitter.failure(f"读不了 {bundle}：{exc}")
        return EXIT_UNUSABLE
    emitter.field("包", bundle)
    emitter.field("节点数", len(lines))
    for line in lines:
        emitter.line(line)
    emitter.document({"group": "node", "verb": "list", "bundle": str(bundle),
                      "nodes": list(lines)})
    return EXIT_DONE


def _preflight_lines(plan: node_assembly.NodePlan) -> tuple[str, ...]:
    """干跑时把「每一段都算得出来」也印出来（与 --write 开头那一步同一条尺子）。"""
    try:
        return ("[写前干算]", *node_assembly.preflight_plan(plan))
    except (BaModMakerError, OSError) as exc:
        return (f"[写前干算] ★ 算不出来：{exc}",)


def _red_verifies(report: Any) -> tuple[str, ...]:
    """writer 的写后复核里那些**红**（ok: false）—— 空元组 = 全绿。"""
    verify = getattr(report, "verify", None)
    if not isinstance(verify, Mapping):
        return ()
    return tuple(
        str(key) for key, value in verify.items()
        if isinstance(value, Mapping) and not value.get("ok")
    )


def _report(
    emitter: Emitter,
    *,
    plan: node_assembly.NodePlan,
    report: Any,
    verify_lines: tuple[str, ...],
    verify_ok: bool,
    reds: tuple[str, ...],
    project_root: Path,
) -> None:
    """人读那几行：一次动作只报一条判据（计划读数 ＋ 写后复核 ＋ 反向 Δ）。"""
    emitter.field("包", plan.bundle)
    emitter.field("动作", plan.verb)
    emitter.field("摘要", plan.summary)
    tail = "（干跑：一个字节都没写；加 --write 才落字节）" if report is None else ""
    emitter.line("[判据]" + tail)
    for line in plan.criterion_lines():
        emitter.line(f"  {line}")
    if report is None:
        for line in _preflight_lines(plan):
            emitter.line(line)
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
    emitter.field("工程目录", str(project_root))


def _document(
    *,
    plan: node_assembly.NodePlan,
    report: Any,
    verify_lines: tuple[str, ...],
    verify_ok: bool,
    reds: tuple[str, ...],
) -> dict[str, Any]:
    """`--json` 那一份（只留标量）。"""
    return {
        "group": plan.verb.split()[0],
        "verb": plan.verb,
        "bundle": str(plan.bundle),
        "summary": plan.summary,
        "stages": [kind for kind, _payload in plan.stages],
        "new_path_ids": list(_new_ids(plan)),
        "dry_run": report is None or bool(getattr(report, "dry_run", False)),
        "wrote_bundle": bool(getattr(report, "wrote_bundle", False)),
        "patch": str(report.patch) if report is not None and report.patch else None,
        "patch_bytes": int(getattr(report, "patch_bytes", 0) or 0),
        "verify_ok": bool(verify_ok) and not reds,
        "verify_lines": list(verify_lines),
        "verify_reds": list(reds),
    }


def _new_ids(plan: node_assembly.NodePlan) -> tuple[int, ...]:
    out: list[int] = []
    for kind, payload in plan.stages:
        if kind == "delta":
            out.extend(int(item.path_id) for item in payload.added)
    return tuple(out)
