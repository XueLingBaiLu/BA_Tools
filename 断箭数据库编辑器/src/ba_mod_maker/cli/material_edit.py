"""`material` 子命令：**皮肤表 `Skins.Data`**（新增 / 删除 / 修改）与
**渲染器 `m_Materials`**（加槽 / 删槽）。

## 这一组收的是哪两件事（设计稿 `设计-材质完全自定义.md` §4 的 ⑤ 层）

    ba-mod-maker material skin add    --bundle <件包> --path-id <prefab 的 pathID> ^
        --skin-id <新 Id> --reference <参照条 Id> [--set-material 旧=新]... [--write]
    ba-mod-maker material skin remove --bundle <件包> --path-id <prefab> --skin-id <Id> [--write]
    ba-mod-maker material skin update --bundle <件包> --path-id <prefab> --skin-id <Id> ^
        --set-material 旧=新 [--set-material 旧=新]... [--write]
    ba-mod-maker material slot add    --bundle <件包> --path-id <渲染器> ^
        --material-id <pathID> [--index <下标>] [--write]
    ba-mod-maker material slot remove --bundle <件包> --path-id <渲染器> --index <下标> ^
        [--handled <引用标签>]... [--write]

**两种定位，二选一**（⛔ 同给 = 退出码 2）：

* `--bundle <包> ＋ --path-id <包里哪个对象>`：离线件 / 合成件那条路；
* `--unit <地址键>`：`unit_key -> (包, UnitPrefabRoot, catalog)` 那一跳
  （只有一份：`skin_install._open_unit`；catalog 也一并解析出来，真件包成对更新）。
  `slot` 那两支**还要指名渲染器**：`--renderer <pathID>`，它必须落在这台车的
  **`UnitPrefabRoot.Renderers`** 名单里 —— 本组**不猜**是哪一颗渲染器。

## 渲染器名单从哪来（`--unit` ＋ `slot` 那一跳）

唯一出处 = prefab 上 **`UnitPrefabRoot.Renderers`** 那一格（与 `prefab_skins` 数渲染器
用的是**同一把读法**）。真件一手读数（`_work/mat-unit/probe_roster.py`）：`RouteA2Real.prefab`
52 节点 / **13 渲染器**，名单 **13** 条、**逐一对应**（名单里不在 prefab 的 0 条、
prefab 里不在名单的 0 条）⇒「这台车有哪些渲染器」是一份**有限名单**，不是可以猜的量。

⇒ `--renderer` **必给**，收**整数 pathID**（渲染器 pathID 真件里有负数），⛔ **不收名字**：
名字只当读数印出来（真件实测 `000_skinned_Chassis` 在名单里出现 2 次 ⇒ 按名字指名
**必然有二义**）。不在名单里 / 名单读不出 ⇒ 退出码 2，并把名单逐条印出来让人照着抄。

## 三个原子动词（AGENTS §0.10：产品只有新增 / 删除 / 修改）

| 动作 | 落库 | 条数 |
|---|---|---|
| `skin add` / `skin remove` | `asset_kernel/prefab_skins.py` | N -> N+1 / N -> N-1 |
| `skin update`（只改材质指向，等长档） | 同上 | N -> N |
| `slot add` / `slot remove` | `asset_kernel/renderer_slots.py` | M -> M+1 / M -> M-1 |

⛔ 没有「换」这种复合动作：要改指向就 `skin update`，要顶替一条就 **`remove` ＋ `add`**
（两个动作各自可撤）。文案口径同样守这条（`tests/test_no_swap_wording.py` 的尺子扫全部 CLI help）。

## 三种跑法

| 跑法 | 盘上会发生什么 |
|---|---|
| 默认 | **干跑**：算出读数、印判据，**一个字节都不写**（连 install() 都不调） |
| `--dry-run` | 与默认同义（留给"我就是要干跑"的脚本） |
| `--write` | **真写**：install(dry_run=False)（Δ ＋ 反向 Δ 进补丁柜）-> verify_plan 读回核对 |

`--dry-run` 与 `--write` 同时给 = 退出码 2（⛔ 不猜用户要哪个）。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 算出来了（干跑）/ `--write` 写完且写后复核逐条相同 |
| 1 | **结果不干净**：写后复核读回与期望不同，或 `--write` 的写回计划被拦 |
| 2 | 用不了：参数 / 包 / 守卫不过（Id 撞 · 参照条缺 · 渲染器不在名单 · 指向包外 · 引用未处理） |

⚠ **空改动走 2 不走 1**：`prefab_skins` / `renderer_slots` 把"新旧字节一模一样"直接抛成
`AssetError`（fail-closed）—— 所以 CLI 这一层收到的是一条守卫报错。如实说：本组里 **1**
这一档只有"写下去了、读回却不对"能产生（⛔ 不是"没有改动可写"那个意思）。

## 落点与留白

* 写侧那一趟**只有一份**（`prefab_skins.run_write`；`renderer_slots` 也转调它）；
* 补丁落 `<--project-root>/patches/`，还原走 `ba-mod-maker assets restore`；
* `--catalog` / `--game` 只有**真件包**才要给（成对更新 `m_Crc` / `m_BundleSize`）；
  离线件 / 合成件不给 ⇒ 判据一行里写「未同步（离线件）」，游戏目录一个字节都不碰；
* `slot remove` **一定先跑整包引用普查**（`census_material`）："除了这一格还有谁指着这颗材质"
  里每一条都要出现在 `--handled` 里，否则整件事被拒绝（共享面实测：履带料 refs=9）；
* ⛔ 本组**不做可见性**（`UserItemsConfig`，设计稿 §4 的 ⑥ 层，写 `data.unity3d` 那一档）。
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import prefab_skins, renderer_slots
from ba_mod_maker.cli import material_visible
from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import AssetError, BaModMakerError

__all__ = [
    "EXIT_DIRTY",
    "EXIT_DONE",
    "EXIT_UNUSABLE",
    "GROUPS",
    "SKIN_VERBS",
    "SLOT_VERBS",
    "add_parser",
    "build_plan",
    "install_plan",
    "parse_material_pairs",
    "run",
    "verify_plan",
]

#: 退出码（与 finder / validator / object / texture 同一套语义）。
EXIT_DONE = 0
#: **结果不干净**：写后读回与期望不同，或写回计划被拦（⚠ 不是"没有改动可写"，见模块头）。
EXIT_DIRTY = 1
#: 用不了：参数 / 包 / 守卫。
EXIT_UNUSABLE = 2

#: 两组（顺序 = --help 里的顺序）。
GROUPS: tuple[str, ...] = ("skin", "slot")
#: 皮肤表那三个原子动词。
SKIN_VERBS: tuple[str, ...] = ("add", "remove", "update")
#: 渲染器槽那两个（等长档不存在：槽数组一变长度必变）。
SLOT_VERBS: tuple[str, ...] = ("add", "remove")

_GROUP_LABELS = {"skin": "皮肤表", "slot": "材质槽"}
_VERB_LABELS = {"add": "新增", "remove": "删除", "update": "修改"}

_DESCRIPTION = """材质线：皮肤表 Skins.Data（新增 / 删除 / 修改）
与渲染器 m_Materials（加槽 / 删槽）。

  material skin add    --bundle <件包> --path-id <prefab> --skin-id <新 Id>
                       --reference <参照条 Id> [--set-material 旧=新]...
  material skin remove --bundle <件包> --path-id <prefab> --skin-id <Id>
  material skin update --bundle <件包> --path-id <prefab> --skin-id <Id>
                       --set-material 旧=新 [--set-material 旧=新]...
  material slot add    --bundle <件包> --path-id <渲染器> --material-id <pathID> [--index <下标>]
  material slot remove --bundle <件包> --path-id <渲染器> --index <下标> [--handled <标签>]...

入参两种定位，二选一：--bundle <包> ＋ --path-id <包里哪个对象>（离线件那条路），
或者 --unit <地址键>（unit_key -> 包 ＋ UnitPrefabRoot ＋ catalog）。
slot 那两支走 --unit 时**还要 --renderer <pathID>**：它必须落在这台车的
UnitPrefabRoot.Renderers 名单里（⛔ 不收名字：名字只是读数，真件实测会重名）。
默认**干跑**（一个字节都不写）；--write 才落字节（Δ ＋ 反向 Δ 进补丁柜 ＋ 写后读回核对）。

退出码：
  0  算出来了 / --write 已写回且写后复核逐条相同
  1  结果不干净（写后读回与期望不同 / 写回计划被拦）
  2  用不了（参数不对 / 包读不开 / 守卫不过：Id 撞 / 参照条缺 / 渲染器不在名单 / 指向包外 /
     引用未处理 / 空改动）
"""

_EPILOG = """--set-material 旧=新   两个数都是**包里的 pathID**（皮肤表 Data._keyData 那一列）。
  旧的那个必须**在参照条里已有**（照抄参照条的口径：⛔ 不许自己发明槽位）；
  新的那个必须在包内（包外 PPtr = 游戏里取到空指针）。
  可给多次；skin add 不给 = 原样照抄参照条；skin update 必给（修改 = 改一个既有字段的值）。

--reference            照抄哪一条既有皮肤（新增只有这一条路）：Renderers 与配对结构**原样照抄** ——
  手搓一条空 Renderers 的皮肤，游戏会在 SkinStorageBridge.FillRenderers() 里就地补成另一个语义。

--handled              删槽用：整包引用普查给出的那些"除了这一格还指着这颗材质"的标签，
  形如 MeshRenderer#400.m_Materials[0]（可以给多次）。少一条 ⇒ 整件事被拒绝 ——
  两种合法走法：把那些引用**一起改**（各自一条命令），或者这次别删。

--unit <地址键>        按单位键定位：键是 Units.ModelFileName 的**原样值**（例 RU_T72B），
  ⛔ 不是显示名。这一条会用 catalog 定位唯一命中包（AssetBundle.m_Container 复核）并把
  catalog 一并接上（真件包要成对更新 m_Crc / m_BundleSize）。要求 --game 或能自动找到游戏根。
  ⛔ 与 --bundle / --path-id 互斥。slot 那两支走这一条时**必须再给 --renderer**。

--renderer <pathID>    slot 那两支 ＋ --unit 时**必给**：指名这台车的哪一颗渲染器。
  唯一出处 = prefab 上 **UnitPrefabRoot.Renderers** 那份名单（与 skin 数渲染器同一把读法；
  真件实测：RouteA2Real.prefab 52 节点 / 13 渲染器，名单 13 条、逐一对应）。
  收**整数 pathID**（真件里有负数，例 -8003744623352085720）；⛔ **不收名字** ——
  名字只当读数印出来（真件实测 000_skinned_Chassis 出现 2 次 ⇒ 按名字必然有二义）。
  不在名单里 / 名单读不出 ⇒ 退出码 2 ＋ 把名单逐条印出来（pathID ＋ 名字 ＋ 类型 ＋ 槽数）。

--catalog / --game     只有**真件包**才要给（成对更新 catalog 的 m_Crc / m_BundleSize）；
  离线件 / 合成件不给 ⇒ 判据一行里写「未同步（离线件）」，游戏目录一个字节都不碰。
--project-root         补丁柜落点（<它>/patches/，默认当前目录）。

例：
  ba-mod-maker material skin add --bundle units.bundle --path-id 8123456789 ^
      --skin-id 2234 --reference 2232 --set-material 500=502 --project-root D:/proj
  ba-mod-maker material slot add --bundle units.bundle --path-id 9194953101066830632 ^
      --material-id 7952762356820983200 --write --project-root D:/proj
  ba-mod-maker material skin remove --unit RU_T72B --skin-id 2233 --dry-run --game D:/game
"""


# --------------------------------------------------------------------------- #
# 入参：--set-material 旧=新
# --------------------------------------------------------------------------- #
def parse_material_pairs(texts: Sequence[str] | None) -> dict[int, int]:
    """**纯函数**：--set-material 旧=新 的多次出现 -> {旧: 新}（⛔ 不猜缺省）。

    形状不对（没等号 / 空的 / 不是十进制整数）一律抛 —— 一个写歪的 pathID 会变成
    "指向包外"那种守卫报错，或者更糟：把两颗材质写成一团。
    """
    out: dict[int, int] = {}
    for raw in texts or ():
        text = str(raw)
        old, sep, new = text.partition("=")
        if not sep or not old.strip() or not new.strip():
            raise AssetError(
                f"--set-material 要写成 旧=新（收到 {text!r}）",
                hint="两个数都是包里的 pathID，例：--set-material 500=502",
            )
        try:
            source, target = int(old.strip(), 10), int(new.strip(), 10)
        except ValueError:
            raise AssetError(
                f"--set-material 的两个数都要是十进制 pathID（收到 {text!r}）",
                hint="pathID 是对象表里的整数（例 7952762356820983200），⛔ 不是材质名",
            ) from None
        out[source] = target
    return out


# --------------------------------------------------------------------------- #
# 计划（干跑）：五个动词各一条 —— 全转 **两个库模块**，这里不自己碰字节
# --------------------------------------------------------------------------- #
def build_plan(args: argparse.Namespace) -> prefab_skins.SkinPlan | renderer_slots.SlotPlan:
    """**只读**：按 args 算出一份写回计划（⛔ 一个字节都没写）。

    库层已经把守卫、往返自证、写回档选择都做完了 —— 这里只做**分派**，
    ⛔ 不许在这里重算任何一条判据（"逻辑只有一份"，AGENTS §5）。
    """
    group = str(getattr(args, "material_group", "") or "")
    verb = str(getattr(args, "material_action", "") or "")
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    path_id = int(getattr(args, "path_id", 0) or 0)
    common: dict[str, Any] = {
        "catalog_path": getattr(args, "catalog", None),
        "game_root": getattr(args, "game", None),
    }
    pairs = parse_material_pairs(getattr(args, "set_material", None))
    if group == "skin":
        skin_id = int(getattr(args, "skin_id", 0) or 0)
        if verb == "add":
            return prefab_skins.plan_add(
                bundle,
                path_id,
                skin_id=skin_id,
                reference_id=int(getattr(args, "reference_id", 0) or 0),
                materials=pairs or None,
                **common,
            )
        if verb == "remove":
            return prefab_skins.plan_remove(bundle, path_id, skin_id=skin_id, **common)
        if verb == "update":
            return prefab_skins.plan_update(
                bundle, path_id, skin_id=skin_id, materials=pairs, **common
            )
    elif group == "slot":
        if verb == "add":
            return renderer_slots.plan_add_slot(
                bundle,
                path_id,
                int(getattr(args, "material_id", 0) or 0),
                index=getattr(args, "index", None),
                **common,
            )
        if verb == "remove":
            return renderer_slots.plan_remove_slot(
                bundle,
                path_id,
                int(getattr(args, "index", 0) or 0),
                handled=tuple(str(x) for x in (getattr(args, "handled", None) or ())),
                **common,
            )
    raise AssetError(
        f"不认识的动作 {group!r} {verb!r}",
        hint="这一组只有 material skin add / remove / update 与 material slot add / remove",
    )


def install_plan(
    plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan, *, project_root: str | Path
) -> Any:
    """**真写**那一趟（唯一入口）：两个库模块的 install 都转 prefab_skins.run_write。

    dry_run=False 是**显式**给的：两个 install 的默认值就是 False，
    "默认干跑"这件事由 CLI 这一层保证（不 --write 就根本不调这个函数）。
    """
    if isinstance(plan, renderer_slots.SlotPlan):
        return renderer_slots.install(plan, project_root=project_root, dry_run=False)
    return prefab_skins.install(plan, project_root=project_root, dry_run=False)


def verify_plan(
    plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan,
) -> tuple[str, ...]:
    """**写后复核·读回**：从盘上把这张表 / 这条数组读回来，逐条与期望比（不等 ⇒ 抛）。"""
    if isinstance(plan, renderer_slots.SlotPlan):
        return renderer_slots.verify_plan(plan)
    return prefab_skins.verify_plan(plan)


def _target(plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan) -> tuple[int, str]:
    """(pathID, 名字) —— 皮肤表那边是 prefab，槽那边是渲染器（两边的字段名不同）。"""
    if isinstance(plan, renderer_slots.SlotPlan):
        return int(plan.renderer_path_id), str(plan.renderer_name)
    return int(plan.prefab_path_id), str(plan.prefab_name)


def _slot_ids(plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan, *, after: bool) -> list[int]:
    """条数读数（皮肤表数 Id，槽那边数 pathID）—— 两边都不塞整条对象进 JSON。"""
    slots = plan.slots_after if after else plan.slots_before
    if isinstance(plan, renderer_slots.SlotPlan):
        return [int(value) for value in slots]
    return [int(slot.skin_id) for slot in slots]


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 material 子命令（__main__.py 只调这一句 ＋ 把 run 挂进 handlers）。"""
    parser = sub.add_parser(
        "material",
        help="材质线：皮肤表 Skins.Data 与渲染器 m_Materials 的五个原子动作"
        "（material skin / slot；退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 叶子的 --help 只挂 description（每一格的 help= 已经写清了那一格的话术）——
    # epilog 复制到 8 个 parser 上会把整个 CLI 的帮助面撑大三倍；形状与 object_edit 相同。
    groups = parser.add_subparsers(dest="material_group", required=True)

    skin = groups.add_parser(
        "skin",
        help="皮肤表 Skins.Data：新增 / 删除 / 修改（前两个变长档；修改只改材质指向、等长）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = skin.add_subparsers(dest="material_action", required=True)

    p = verbs.add_parser(
        "add",
        help="新增一条皮肤（照抄 --reference 那条：Renderers 与配对结构原样照抄）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--skin-id",
        dest="skin_id",
        type=int,
        required=True,
        help="新皮肤 Id（正数；表里已经有这个 Id 就拒绝 —— Id 是跨表连接键）",
    )
    p.add_argument(
        "--reference",
        dest="reference_id",
        type=int,
        required=True,
        help="照抄哪一条既有皮肤（它必须在这张表里；Renderers 原样照抄）",
    )
    _add_pairs(p, required=False)
    _add_output(p)

    p = verbs.add_parser(
        "remove",
        help="删除一条皮肤（Id 不在表里就报错退出，不静默成功）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--skin-id",
        dest="skin_id",
        type=int,
        required=True,
        help="要删的那条皮肤 Id（⛔ 不是行下标）",
    )
    _add_output(p)

    p = verbs.add_parser(
        "update",
        help="修改一条皮肤的材质指向（等长档：条数与 Renderers 都不动，只改指向）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument("--skin-id", dest="skin_id", type=int, required=True, help="要改的那条皮肤 Id")
    _add_pairs(p, required=True)
    _add_output(p)

    slot = groups.add_parser(
        "slot",
        help="渲染器 m_Materials：加一个槽 / 删一个槽（槽数组是变长档 —— 长度必变）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verbs = slot.add_subparsers(dest="material_action", required=True)

    p = verbs.add_parser(
        "add",
        help="给渲染器加一个材质槽（材质不在包里就拒绝；不给 --index 就追加到末尾）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--material-id",
        dest="material_id",
        type=int,
        required=True,
        help="要加进去的材质 pathID（必须在这个包里；0 = 空引用，不许）",
    )
    p.add_argument(
        "--index",
        type=int,
        default=None,
        help="插在第几位（0..现有槽数；不给 = 追加到末尾 —— 追加不动老下标）",
    )
    _add_output(p)

    p = verbs.add_parser(
        "remove",
        help="删一个材质槽（先跑整包引用普查：还有谁指着这颗材质，必须用 --handled 交代）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--index",
        type=int,
        required=True,
        help="要删的槽下标（0 起；⛔ 不许按「看起来像」的那一格删）",
    )
    p.add_argument(
        "--handled",
        action="append",
        default=None,
        help="普查里已经一并处理掉的引用标签（可给多次，形如 MeshRenderer#400.m_Materials[0]）",
    )
    _add_output(p)

    # ⑥ 可见性层（material visible add|remove|update）挂在同一层
    material_visible.add_parser(parser)

    return parser


def _add_locator(parser: argparse.ArgumentParser) -> None:
    """五个动作共用的落点：**两种定位二选一** —— `--unit`，或 `--bundle` ＋ `--path-id`。"""
    parser.add_argument(
        "--unit",
        help="按地址键定位（Units.ModelFileName 的原样值，例 RU_T72B）：包与 catalog 由 "
        "unit_key -> (资产路径, 候选包) 那一跳解析，目标 pathID = 该单位 prefab 的 UnitPrefabRoot。"
        "⛔ 与 --bundle / --path-id 互斥；slot 那两支走这一条时还要 --renderer",
    )
    parser.add_argument("--bundle", help="UnityFS 包（.bundle / data.unity3d）；与 --unit 二选一")
    parser.add_argument(
        "--path-id",
        dest="path_id",
        type=int,
        help="对象在包里的 pathID：skin 那一支给 UnitPrefabRoot，slot 那一支给渲染器"
        "（MeshRenderer / SkinnedMeshRenderer）—— ⛔ 与 --bundle 同给、或两支都缺，都报错",
    )
    parser.add_argument(
        "--renderer",
        help="slot 那两支 ＋ --unit 时**必给**：指名这台车的哪一颗渲染器，给**整数 pathID**。"
        "唯一出处 = prefab 上的 UnitPrefabRoot.Renderers 名单（真件实测 RouteA2Real.prefab "
        "13 条，与 prefab 的 13 个渲染器逐一对应）。⛔ 不收名字 —— 名字只当读数印出来"
        "（真件实测会重名：000_skinned_Chassis 出现 2 次）；不在名单里就把名单逐条印出来。"
        "⚠ 只在 --unit 那条路上用：--bundle ＋ --path-id 已经指名了渲染器",
    )


def _add_pairs(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument(
        "--set-material",
        dest="set_material",
        action="append",
        default=None,
        required=required,
        metavar="旧=新",
        help="改材质指向（两个数都是包里的 pathID，可给多次）；"
        + ("修改必给（不给就是空改动）" if required else "不给 = 原样照抄参照条的指向"),
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    """五个动作共用的输出格。"""
    parser.add_argument("--project-root", help="补丁柜的落点（<它>/patches/，默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只算不写（与默认同义）")
    parser.add_argument(
        "--write", action="store_true", help="真写：落字节 ＋ 反向 Δ 进补丁柜 ＋ 写后读回核对"
    )
    parser.add_argument(
        "--game", help="--unit 用（默认自动找）／--write 用（只有真件包才要给）：游戏根目录"
    )
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
# `--unit`：unit_key -> (包, UnitPrefabRoot 的 pathID, catalog)
# --------------------------------------------------------------------------- #
def resolve_unit(args: argparse.Namespace) -> tuple[Path, int, Path]:
    """`--unit <地址键>` -> `(包, UnitPrefabRoot 的 pathID, catalog)`。

    那一跳**只有一份**：`skin_install._open_unit`（`writer.locate_unit_asset` 定位 ＋
    `open_unique_bundle` 按 `AssetBundle.m_Container` 复核唯一命中 ＋ `_aa_paths` 的 catalog
    口径）—— 这里⛔不重写定位逻辑，只多取一个 pathID（见 `_unit_prefab_path_id`）。
    """
    from ba_mod_maker.asset_kernel import skin_install

    game_root = _unit_game_root(args)
    key = str(getattr(args, "unit", "") or "")
    with skin_install._open_unit(game_root, key) as unit:  # noqa: SLF001 -- 见 docstring
        path_id = _unit_prefab_path_id(unit)
        if not path_id:
            raise AssetError(
                f"{key!r} 的 prefab 上没有 UnitPrefabRoot（容器地址 {unit.asset_path!r}）",
                hint="skin 那三个动词动的是 UnitPrefabRoot.Skins —— 这个资产不是单位 prefab",
            )
        return Path(unit.bundle), int(path_id), Path(unit.catalog)


def _unit_game_root(args: argparse.Namespace) -> str | Path:
    """`--unit` 要的游戏根：给了就用它，没给就自动找（找不到 ⇒ 抛，⛔ 不静默回落）。"""
    game_root = getattr(args, "game", None)
    if game_root:
        return str(game_root)
    from ba_mod_maker.core.assets.game import find_game_root  # 只为找根，不拉 Qt

    found = find_game_root()
    if found is None:
        raise AssetError(
            "找不着游戏根目录（--unit 要它来读 catalog）",
            hint="给 --game <游戏根>；离线件那条路用 --bundle ＋ --path-id",
        )
    return str(found)


def resolve_unit_renderer(args: argparse.Namespace) -> tuple[Path, int, Path, str]:
    """`--unit <地址键>` ＋ `--renderer <pathID>` -> `(包, 渲染器 pathID, catalog, 读数一行)`。

    这一跳的依据**只有一份**：`renderer_slots.unit_renderer_roster` 读 prefab 上的
    **`UnitPrefabRoot.Renderers`**（跟 `skin` 数渲染器用的是同一把读法）。真件一手读数
    （`_work/mat-unit/probe_roster.py`）：52 节点 / 13 渲染器，名单 13 条、逐一对应。

    ⛔ 本函数**不挑**渲染器：`--renderer` 缺 / 不是整数 / 不在名单里 / 名单为空，一律抛
    （CLI 那一层翻成退出码 2），并且错误消息里**逐条印出名单**（pathID ＋ 名字 ＋ 类型 ＋ 槽数）
    —— 名字只是读数（真件实测会重名），⛔ 不参与定位。
    """
    from ba_mod_maker.asset_kernel import skin_install  # 与 resolve_unit 同一跳（定位只有一份）

    key = str(getattr(args, "unit", "") or "")
    with skin_install._open_unit(  # noqa: SLF001 -- 定位只有一份（见 resolve_unit）
        _unit_game_root(args), key
    ) as unit:
        uroot_pid = _unit_prefab_path_id(unit)
        if not uroot_pid:
            raise AssetError(
                f"{key!r} 的 prefab 上没有 UnitPrefabRoot（容器地址 {unit.asset_path!r}）",
                hint="渲染器名单挂在 UnitPrefabRoot.Renderers 上 —— 这个资产不是单位 prefab",
            )
        roster = renderer_slots.unit_renderer_roster(
            unit.bundle, int(uroot_pid), source=unit.source, asset_path=unit.asset_path
        )
        picked = renderer_slots.resolve_unit_renderer(roster, getattr(args, "renderer", None))
        line = (
            f"{picked.label()}（UnitPrefabRoot.Renderers 名单 {len(roster)} 条里挑的，"
            f"prefab pathID={int(uroot_pid)}）"
        )
        return Path(unit.bundle), int(picked.path_id), Path(unit.catalog), line


def _unit_prefab_path_id(unit: Any) -> int:
    """单位 prefab 上那颗 `UnitPrefabRoot` 的 pathID（走**产品自己的读侧**）。

    ① 容器那条（`(地址, 根 GameObject 的 pathID)`）由 `AssetSource.container_entries()` 给 ——
    它就是 `open_unique_bundle` 复核唯一命中用的那一把钥匙；
    ② 组件表用 `prefab_skins.read_package_object` 逐颗读：**⛔ 不走 UnityPy 的
    `read_prefab_nodes`** —— 那条要先 `go.m_Transform.read()`，而"根上有没有 Transform"与
    "UnitPrefabRoot 在不在"是两件事（合成件 / 精简件不带 Transform，前者当场 AttributeError）；
    ③ 认它靠 `skin_install.SKIN_ROOT_FIELDS` —— 与 `skin_slots` **同一套字段名**（判据只有一份）。
    """
    from ba_mod_maker.asset_kernel import skin_install

    for _asset_path, root_pid in unit.source.container_entries():
        if not root_pid:
            continue
        try:
            tree = prefab_skins.read_package_object(unit.bundle, int(root_pid)).tree
        except (AssetError, OSError):
            continue
        for item in tree.get("m_Component") or []:
            component_id = int((item.get("component") or {}).get("m_PathID", 0))
            if not component_id:
                continue
            try:
                sub = prefab_skins.read_package_object(unit.bundle, component_id).tree
            except (AssetError, OSError):
                continue
            if all(field in sub for field in skin_install.SKIN_ROOT_FIELDS):
                return component_id
    return 0


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    group = str(getattr(args, "material_group", "") or "")
    verb = str(getattr(args, "material_action", "") or "")
    if group not in GROUPS:
        emitter.failure(f"不认识的动作 {group!r}", hint=f"这一组只有 {list(GROUPS)} 两组")
        return EXIT_UNUSABLE
    allowed = SKIN_VERBS if group == "skin" else SLOT_VERBS
    if verb not in allowed:
        emitter.failure(f"不认识的动作 {group} {verb!r}", hint=f"这一组只有 {' / '.join(allowed)}")
        return EXIT_UNUSABLE
    unit_key = str(getattr(args, "unit", "") or "")
    bundle_arg = str(getattr(args, "bundle", "") or "")
    path_id_arg = getattr(args, "path_id", None)
    if unit_key and (bundle_arg or path_id_arg is not None):
        emitter.failure(
            "--unit 与 --bundle / --path-id 不能同时给（两种定位二选一）",
            hint="按地址键定点：--unit <键>；按包定点：--bundle <包> ＋ --path-id <包里的 pathID>",
        )
        return EXIT_UNUSABLE
    if not unit_key and not (bundle_arg and path_id_arg is not None):
        emitter.failure(
            "要 --unit <地址键>，或者 --bundle <包> ＋ --path-id <pathID>（两样都给全）",
            hint="例：material skin remove --unit RU_T72B --skin-id 2233"
            "（⛔ 键不是显示名）",
        )
        return EXIT_UNUSABLE
    renderer_arg = str(getattr(args, "renderer", "") or "")
    if renderer_arg and not unit_key:
        emitter.failure(
            "--renderer 只在 --unit 那条路上用（--bundle ＋ --path-id 已经指名了渲染器）",
            hint="按地址键定点：material slot add --unit RU_T72B "
            "--renderer 9194953101066830632 --material-id 7952762356820983200",
        )
        return EXIT_UNUSABLE
    if renderer_arg and group != "slot":
        emitter.failure(
            f"--renderer 是 slot 那一支的（{group} 那三个动词动的是 prefab 上的皮肤表）",
            hint="skin：--unit <地址键> --skin-id <Id> —— 渲染器名单挂在 prefab 的 "
            "UnitPrefabRoot.Renderers 上，只有 slot 那两支用它",
        )
        return EXIT_UNUSABLE
    renderer_line = ""
    if unit_key:
        try:
            if group == "slot":
                resolved = resolve_unit_renderer(args)
                resolved_bundle, resolved_pid, resolved_catalog, renderer_line = resolved
            else:
                resolved_bundle, resolved_pid, resolved_catalog = resolve_unit(args)
        except (BaModMakerError, OSError) as exc:
            emitter.failure(
                f"定位不到 {unit_key!r}：{exc}",
                hint="unit 键是 Units.ModelFileName 的原样值（例 RU_BMD-2\\RU_BMD_2_base）—— "
                "⛔ 不是显示名；离线件那条路用 --bundle ＋ --path-id"
                + (
                    "；slot 走 --unit 还要 --renderer <pathID>（名单在 prefab 的 "
                    "UnitPrefabRoot.Renderers 上）"
                    if group == "slot"
                    else ""
                ),
            )
            return EXIT_UNUSABLE
        args.bundle = str(resolved_bundle)
        args.path_id = int(resolved_pid)
        if not getattr(args, "catalog", None):
            args.catalog = str(resolved_catalog)
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
    project_root = Path(str(getattr(args, "project_root", None) or Path.cwd()))
    try:
        plan = build_plan(args)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读不了 {bundle}：{exc}")
        return EXIT_UNUSABLE
    report: Any = None
    verify_lines: tuple[str, ...] = ()
    verify_ok = True
    if write:
        try:
            report = install_plan(plan, project_root=project_root)
        except (BaModMakerError, OSError) as exc:
            emitter.failure(f"写回失败：{exc}", hint=f"{bundle}（走到这一步之前写回没动手）")
            return EXIT_UNUSABLE
        try:
            verify_lines = verify_plan(plan)
        except (BaModMakerError, OSError) as exc:
            verify_lines = (f"写后复核不过：{exc}",)
            verify_ok = False
    reds = _red_verifies(report)
    _report(
        emitter,
        plan=plan,
        group=group,
        verb=verb,
        report=report,
        verify_lines=verify_lines,
        verify_ok=verify_ok,
        reds=reds,
        renderer_line=renderer_line,
    )
    emitter.document(
        _document(
            plan=plan,
            group=group,
            verb=verb,
            report=report,
            verify_lines=verify_lines,
            verify_ok=verify_ok,
            reds=reds,
        )
    )
    return _exit_code(plan=plan, report=report, verify_ok=verify_ok, reds=reds)


def _exit_code(
    *,
    plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan,
    report: Any,
    verify_ok: bool,
    reds: tuple[str, ...],
) -> int:
    """0 / 1：写后复核有没有红 ＋ 写回计划有没有被拦（**空改动不在这里** —— 库层直接抛）。"""
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
    plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan,
    group: str,
    verb: str,
    report: Any,
    verify_lines: tuple[str, ...],
    verify_ok: bool,
    reds: tuple[str, ...],
    renderer_line: str = "",
) -> None:
    """人读那几行：**一次动作只报一条判据**（条数 ＋ 对象字节 ＋ 写回档 ＋ catalog）。"""
    emitter.field("包", plan.bundle)
    emitter.field("动作", f"{_GROUP_LABELS[group]}·{_VERB_LABELS[verb]}")
    if renderer_line:
        emitter.field("渲染器", renderer_line)
    path_id, name = _target(plan)
    emitter.field("对象", f"pathID={path_id}『{name}』")
    emitter.line(f"[判据] {plan.count_line()}")
    if report is None:
        # 干跑：判据那一块就是 writer 的计划读数（与真写**逐行相同**的那一份）。
        for line in plan.criterion_lines():
            emitter.line(f"  {line}")
        emitter.field("写盘", "（干跑：一个字节都没写；加 --write 才落字节）")
        return
    # 真写：report.report_lines() 头几行**就是**上面那同一份计划读数（⛔ 不重复印一遍）。
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
    plan: prefab_skins.SkinPlan | renderer_slots.SlotPlan,
    group: str,
    verb: str,
    report: Any,
    verify_lines: tuple[str, ...],
    verify_ok: bool,
    reds: tuple[str, ...],
) -> dict[str, Any]:
    """--json 那一份（只留标量：整条皮肤 / 槽对象不塞进文档）。"""
    path_id, name = _target(plan)
    return {
        "group": group,
        "verb": verb,
        "action": str(plan.operation),
        "bundle": str(plan.bundle),
        "bundle_name": str(plan.bundle_name),
        "path_id": path_id,
        "object_name": name,
        "level": str(plan.level),
        "count_before": len(plan.slots_before),
        "count_after": len(plan.slots_after),
        "slots_before": _slot_ids(plan, after=False),
        "slots_after": _slot_ids(plan, after=True),
        "touched": [int(x) for x in getattr(plan, "touched", ())],
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
