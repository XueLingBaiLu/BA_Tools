"""`material visible` 子命令：**可见性层** `UserItemsConfig.Skins[单位Id]` 的新增 / 删除 / 修改。

## 这一层收的是哪件事（设计稿 ④ 层）

皮肤有**两处**落点，两层唯一的连接键是**皮肤 Id**：

| 层 | 落点 | 管什么 | 谁做 |
|---|---|---|---|
| ③ 外观 | units 包里的 `Skins.Data` | 皮肤**长什么样** | `material skin` |
| ④ 可见性 | `data.unity3d` 的 `UserItemsConfig` | 菜单里**看不看得见** | **本组** |

    ba-mod-maker material visible add    --container <容器> --path-id <P> --unit <单位 Id> ^
        --item-id <新 Id> --reference <参照条 Id> [--skin-pack S] [--is-default N] ^
        [--free N] [--disabled N] [--drop-triggers] --out <新文件> [--write]
    ba-mod-maker material visible remove --container <容器> --path-id <P> --unit <单位 Id> ^
        --item-id <Id> --out <新文件> [--write]
    ba-mod-maker material visible update --container <容器> --path-id <P> --unit <单位 Id> ^
        --item-id <Id> [--skin-pack S] [--is-default N] [--free N] [--disabled N] ^
        --out <新文件> [--write]
    ba-mod-maker material visible update ... --item-id <Id> --trigger-set <下标> ^
        [--trigger T] [--trigger-loc-key K] [--trigger-count N] --out <新文件> [--write]
    ba-mod-maker material visible update ... --item-id <Id> --trigger-add ^
        --trigger T --trigger-loc-key K --trigger-count N --out <新文件> [--write]
    ba-mod-maker material visible update ... --item-id <Id> --trigger-remove <下标> ^
        --out <新文件> [--write]

## 三条硬口径

* **`--path-id` 必须点名**：容器里实测有两个 `UserItemsConfig`（52174 / 52209），
  `m_Name` 一模一样、同一个 MonoScript ⇒ 按名字**分不出谁是谁**，内核也明说不猜。
  主候选 **52174**（读数见 `kb/30` §4）—— 但本组**不自动回落**，要你写出来。
* **落点是 6.63 GB 的 `data.unity3d`**（实测约 4 分钟），`--write` **默认写新文件**
  （`--out`）；`--in-place` 是**显式**逃生门：先写同目录临时文件、内核写后复核通过后再
  用 `os.replace` 原子就位 —— ⛔ 用户没写 `--in-place` 就绝不原地覆盖。
* **新增照抄参照条**：`--reference` 那条既有条目是唯一来源 —— 内核照抄它那 4 个游戏语义字段
  （Grade / HideUnlockInfo / NoneElement / HideIfNotUnlocked），本组再把**产品管得着的 4 格**
  （皮肤包 / 触发器 / IsDefault / Free / Disabled）也**默认照抄它**，要改就给对应开关。
  `--drop-triggers` = 触发器置**空表**（无解锁条件）；⛔ 本组**不造**任何默认值。
* **触发器（解锁条件）是一个列表，元素没有 Id** ⇒ 唯一定位键是**下标**（从 0 起，
  顺序就是它在字节里的顺序）：`--trigger-add`（**新增**一条 · 三格全给）/
  `--trigger-remove <下标>`（**删除**第几条）/ `--trigger-set <下标>`（**修改**那一条的字段值）。
  ⛔ 一次只许一个触发器动作，⛔ 也不与那 4 个字段开关（皮肤包 / IsDefault / Free / Disabled）
  同时给 —— 每个动作**各自可撤**（AGENTS §0.10）；要连着改就分几次跑。
  `--trigger / --trigger-loc-key / --trigger-count` 三格**一律要你给**。

## 三种跑法

| 跑法 | 盘上会发生什么 |
|---|---|
| 默认 | **干跑**：算出读数、印判据，**一个字节都不写** |
| `--dry-run` | 与默认同义（留给"我就是要干跑"的脚本） |
| `--write` | **真写**：内核 `install`（写新文件 ＋ 写后复核）-> 产物核对
   -> **落补丁柜** -> 印读数 |

`--dry-run` 与 `--write` 同时给 = 退出码 2（⛔ 不猜用户要哪个）。

## 反向 Δ（range 口径）＋ 补丁柜

内核 `reverse_delta(plan)` 给的是一条**记录**（哪个节点 / 哪个 pathID / 原始字节 / 改后长度），
还原时按 pathID **重新定位**再写回。`--write` 一趟里，本组把这一笔落成补丁柜里的
**range 目标**（`reverse_delta.RangeTarget`，与包 / 整文件那两档**并列**）：

    容器路径 · 节点名 · pathID · **被替换区间的原始字节** · 原始长度（＋改后长度 / 装前装后 sha256）

⛔ **不拷整包**（AGENTS §1，用户明令）：补丁体积 = 那一颗对象的字节数
（真件 UserItemsConfig 220,220 B，而容器 6.63 GB）。落点 `<--project-root>/patches/`。

**还原走既有的唯一入口**：`ba-mod-maker assets restore --project-root <…>`
（`cli/__main__.py::_cmd_assets`）—— ⛔ 本组**不另开** restore 动词，
两个入口读同一个柜子只会让人不知道该敲哪条。`assets status` 也能一眼看到这一笔。

⚠ `--write` 落不成补丁时，内核会**连产物一起删掉**（⛔ 不留一个撤不回来的现场）。
`--project-root` 与容器必须**同一个盘**（红线，`reverse_delta.assert_same_drive`）。

## 退出码

| 码 | 什么时候 |
|---|---|
| 0 | 算出来了（干跑）/ `--write` 写完 且 内核写后复核通过 |
| 1 | **结果不干净**：产物不在盘上 / 大小与读数对不上 |
| 2 | 用不了：参数 / 容器 / pathID 拿错 / 守卫不过（单位不在表 · Id 撞 ·
   参照条缺 · 空改动）· 补丁柜落不成 |

## 接线（⛔ 不在本模块里 —— `cli/material_edit.py` / `cli/__main__.py` 归 Lead）

它是 `material` 组的**第三组**（`skin` / `slot` 之后）。三处：

    # cli/material_edit.py —— **顶层** import（⛔ 不成环：本模块只在 dispatch 里
    #   函数级 import material_edit，模块级不 import 它）
    from ba_mod_maker.cli import material_visible
    # cli/material_edit.py::add_parser 里，return parser **之前**
    material_visible.add_parser(parser)                  # 挂在 material 那一层
    # cli/__main__.py 的 handlers 里那一行
    "material": material_visible.dispatch,               # 原为 material_edit.run
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Mapping
from dataclasses import replace as _replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.asset_kernel import user_items as ui
from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import AssetError, BaModMakerError

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

#: 退出码（与 address / object container / material 同一套语义）。
EXIT_DONE = 0
#: **结果不干净**：产物不在盘上 / 大小与读数对不上（⛔ 空改动走 2 —— 内核直接抛）。
EXIT_DIRTY = 1
#: 用不了：参数 / 容器 / 守卫。
EXIT_UNUSABLE = 2

#: 本模块在 `material` 组里那一格的名字。
GROUP = "visible"
#: 三个原子动词（顺序 = `--help` 里的顺序）。
VERBS: tuple[str, ...] = ("add", "remove", "update")
#: 产品管得着的 4 格（与 `--skin-pack / --is-default / --free / --disabled` 一一对应）。
_FIELD_FLAGS: tuple[tuple[str, str], ...] = (
    ("skin_pack", "skin-pack"),
    ("is_default", "is-default"),
    ("free", "free"),
    ("disabled", "disabled"),
)
#: 触发器动作的三个名字（= 判据 / 补丁标签里那个词，也是 AGENTS §0.10 的三个动词）。
TRIGGER_ADD = "新增"
TRIGGER_REMOVE = "删除"
TRIGGER_SET = "修改"
#: 触发器那三格：`args` 上的名字 -> (形参名, --开关名)。
#: 真件类型树里 UnlockTriggersData 就这三格；⛔ 产品**不造**默认值 —— 值必须你给。
_TRIGGER_ARGS: tuple[tuple[str, str, str], ...] = (
    ("trigger", "trigger", "--trigger"),
    ("trigger_loc_key", "description_loc_key", "--trigger-loc-key"),
    ("trigger_count", "count", "--trigger-count"),
)
#: 原地就位时那个临时文件的后缀（同目录，保证 `os.replace` 是同卷原子动作）。
_TEMP_SUFFIX = ".visible-new"

_DESCRIPTION = """可见性层：UserItemsConfig.Skins[单位Id] 的新增 / 删除 / 修改。

  material visible add    --container <容器> --path-id <P> --unit <单位 Id> --item-id <新 Id>
                          --reference <参照条 Id> [--skin-pack S] [--is-default N]
                          [--free N] [--disabled N] [--drop-triggers] --out <新文件> [--write]
  material visible remove --container <容器> --path-id <P> --unit <单位 Id> --item-id <Id>
                          --out <新文件> [--write]
  material visible update --container <容器> --path-id <P> --unit <单位 Id> --item-id <Id>
                          [--skin-pack S] [--is-default N] [--free N] [--disabled N]
                          --out <新文件> [--write]
  material visible update ... --item-id <Id> --trigger-set <下标>
                          [--trigger T] [--trigger-loc-key K] [--trigger-count N]
                          --out <新文件> [--write]
  material visible update ... --item-id <Id> --trigger-add
                          --trigger T --trigger-loc-key K --trigger-count N
                          --out <新文件> [--write]
  material visible update ... --item-id <Id> --trigger-remove <下标> --out <新文件> [--write]

它管的是**菜单里看不看得见**（皮肤长什么样是 material skin 那一组；两层的连接键是皮肤 Id）。
⛔ 落点是 data.unity3d（整文件 6.63 GB，实测约 4 分钟）—— --write 默认写**新文件**（--out）；
--in-place 是显式逃生门（先写临时文件、写后复核通过再同卷改名就位）。
--path-id 必须点名（容器里两个实例 m_Name 一模一样，本组⛔不猜）。
默认**干跑**（一个字节都不写）；--write 才落字节。

退出码：
  0  算出来了 / --write 写完且内核写后复核通过
  1  结果不干净（产物不在盘上 / 大小与读数对不上）
  2  用不了（参数不对 / 容器读不开 / --path-id 拿错对象 / 守卫不过：单位不在表里 · Id 撞 ·
     参照条缺 · 条目不存在 · 触发器下标越界 · 一次给了两个触发器动作 · 空改动）
"""

_EPILOG = """--container            装着 UserItemsConfig 的那个容器：真件是 data.unity3d
  （那颗对象在节点 globalgamemanagers.assets 里）。⚠ 6.63 GB —— --write 那一趟约 4 分钟。

--path-id              UserItemsConfig 那颗对象的 pathID。容器里实测**两个**（52174 / 52209），
  m_Name 一模一样 ⇒ 按名字分不出谁是谁；主候选 **52174**（kb/30 §4），但⛔本组不自动回落。

--unit                 单位 Id（Units.Id）。必须**已经在** Skins 表里 —— ⛔ 本组不新增单位键。

--item-id              皮肤 Id（正数）。它是③④两层的**连接键**：这里写进去的 Id，
  要在 units 包里 Skins.Data 有同号的一条，玩家菜单里才看得到。

--reference            add 用：照抄哪一条**同单位**下既有的条目。产品管得着的 4 格
  （皮肤包 / 触发器 / IsDefault / Free / Disabled）默认也照抄它；要改就给对应开关。

--drop-triggers        add 用：新条目的触发器置**空表**（= 无解锁条件），⛔ 不照抄参照条那一条。

--trigger / --trigger-loc-key / --trigger-count
                       触发器那三格（真件类型树 UnlockTriggersData：Trigger / DescriptionLocKey /
  Count）。⛔ 本组**不造默认值**：add 要自定义就**三格全给**；update 的 --trigger-add 也三格全给，
  --trigger-set 至少给一格。

--trigger-add          update 用：给这条皮肤**新增**一条解锁触发器（条目数不变、触发器 +1，
  追加在末尾 ⇒ 既有下标一个都不动）。三格必须全给。
--trigger-remove <下标>
                       update 用：**删除**第 <下标> 条触发器（下标从 0 起；越界 ⇒ rc=2 并把
  现有的那条清单印出来）。只认下标 —— 给那三格说明你要的不是删除。
--trigger-set <下标>   update 用：**修改**第 <下标> 条触发器的字段值（只动给出来的那几格，
  这一条上多出来的键原样留着）。至少给一格。
  ⛔ 一次只许一个触发器动作，⛔ 也不与 --skin-pack / --is-default / --free / --disabled 同时给
  —— 每个动作**各自可撤**（AGENTS §0.10）；要连着改就分几次跑。

--skin-pack / --is-default / --free / --disabled
                       改哪一格就写哪一格。add 不给 = 照抄参照条那一格；update 至少要给一个
  （⛔ 一个都不给就是空改动，当场 rc=2）。

--out <新文件>          --write 的落点。**默认姿势**：写新文件（源件只读）。
--in-place             --write 的显式逃生门：写同目录临时文件 -> 内核写后复核 -> 同卷改名就位。
  ⛔ 与 --out 互斥；没写它就不会原地覆盖。

例：
  ba-mod-maker material visible add --container data.unity3d --path-id 52174 --unit 9 ^
      --item-id 2240 --reference 2232 --out data-visible.unity3d --project-root D:/proj
  ba-mod-maker material visible update --container data.unity3d --path-id 52174 --unit 9 ^
      --item-id 2233 --free 1 --out data-visible.unity3d --write
  ba-mod-maker material visible update --container data.unity3d --path-id 52174 --unit 9 ^
      --item-id 2233 --trigger-set 0 --trigger-count 5 --out data-visible.unity3d --write
"""


# --------------------------------------------------------------------------- #
# 注册（挂进既有的 material 组）
# --------------------------------------------------------------------------- #
def _material_parser(parent: Any) -> argparse.ArgumentParser:
    """拿到 `material` 那个 parser：给的就是它，或者从顶层 `sub.choices` 里取。"""
    if isinstance(parent, argparse.ArgumentParser):
        return parent
    found = getattr(parent, "choices", {}).get("material")
    if found is None:
        raise RuntimeError(
            "material 组还没注册：先把 material_edit.add_parser(sub) 的返回值传给本函数"
            "（本模块是 material 的子组，⛔ 不是顶层命令）"
        )
    return found


def _material_groups(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    """`material` 上那个既有的 `add_subparsers`（`material_edit` 建的那个）。"""
    for action in parser._actions:  # noqa: SLF001 -- argparse 没有公开的「取已有子命令表」的口子
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 -- 同上
            return action
    raise RuntimeError(
        "material parser 上没有子命令表 —— 它不是 material_edit.add_parser 交出来的那个"
    )


def add_parser(parent: Any) -> argparse.ArgumentParser:
    """把 `visible` 挂进 `material` 组（`material_edit.add_parser` 里调这一句）。"""
    groups = _material_groups(_material_parser(parent))
    parser = groups.add_parser(
        GROUP,
        help="可见性层：UserItemsConfig 的皮肤条目 新增 / 删除 / 修改"
        "（落点 data.unity3d；退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="visible_action", required=True)

    p = actions.add_parser(
        "add",
        help="新增一条可见性条目（照抄 --reference 那条；条目已存在 / 参照条缺都拒绝）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    p.add_argument(
        "--reference",
        dest="reference_id",
        type=int,
        required=True,
        help="照抄哪一条**同单位**下既有的条目（产品管得着的 4 格默认也照抄它）",
    )
    _add_fields(p)
    p.add_argument(
        "--drop-triggers",
        dest="drop_triggers",
        action="store_true",
        help="新条目的触发器置空表（= 无解锁条件）；不给就照抄参照条那一条"
        "（要自定义就给全 --trigger / --trigger-loc-key / --trigger-count）",
    )
    _add_trigger_fields(p)
    _add_output(p)

    p = actions.add_parser(
        "remove",
        help="删除一条可见性条目（Id 不在该单位的表里就报错退出，不静默成功）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    _add_output(p)

    p = actions.add_parser(
        "update",
        help="修改一条的既有字段值，或它的一条解锁触发器"
        "（一个字段都不给就是空改动，当场拒绝）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_locator(p)
    _add_fields(p)
    _add_trigger_fields(p)
    p.add_argument(
        "--trigger-add",
        dest="trigger_add",
        action="store_true",
        help="给这条皮肤新增一条解锁触发器（三格必须全给；追加在末尾，既有下标一个都不动）",
    )
    p.add_argument(
        "--trigger-remove",
        dest="trigger_remove",
        type=int,
        metavar="下标",
        help="删除第 <下标> 条解锁触发器（下标从 0 起；只认下标）",
    )
    p.add_argument(
        "--trigger-set",
        dest="trigger_set",
        type=int,
        metavar="下标",
        help="修改第 <下标> 条解锁触发器的字段值（三格至少给一格）",
    )
    _add_output(p)

    return parser


def _add_locator(parser: argparse.ArgumentParser) -> None:
    """三个动作共用的落点：哪个容器、哪一颗 UserItemsConfig、哪个单位、哪条皮肤。"""
    parser.add_argument(
        "--container",
        required=True,
        help="装着 UserItemsConfig 的容器（真件是 data.unity3d；"
        "那颗对象在 globalgamemanagers.assets）",
    )
    parser.add_argument(
        "--path-id",
        dest="path_id",
        type=int,
        required=True,
        help="UserItemsConfig 那颗对象的 pathID（实测两个实例 52174 / 52209；"
        "主候选 52174，⛔ 本组不猜）",
    )
    parser.add_argument(
        "--unit",
        dest="unit_id",
        type=int,
        required=True,
        help="单位 Id（Units.Id）；必须已在 Skins 表里",
    )
    parser.add_argument(
        "--item-id", dest="item_id", type=int, required=True, help="皮肤 Id（正数；③④两层的连接键）"
    )


def _add_fields(parser: argparse.ArgumentParser) -> None:
    """产品管得着的那 4 格（add 照抄参照条 / update 至少给一个）。"""
    parser.add_argument("--skin-pack", dest="skin_pack", help="皮肤包名（空串是一个合法取值）")
    parser.add_argument(
        "--is-default", dest="is_default", type=int, help="IsDefault（整数：真件里是 0 / 1）"
    )
    parser.add_argument("--free", type=int, help="Free（整数：真件里是 0 / 1）")
    parser.add_argument("--disabled", type=int, help="Disabled（整数：真件里是 0 / 1）")


def _add_trigger_fields(parser: argparse.ArgumentParser) -> None:
    """触发器那三格（UnlockTriggersData：Trigger / DescriptionLocKey / Count）。

    ⛔ **不给就是不给**：本组不造默认值 —— 要「没有解锁条件」用 `--drop-triggers`（空表），
    不是拿某个值占位；要自定义就三格全给。
    """
    parser.add_argument("--trigger", help="触发器的 Trigger（解锁条件名）")
    parser.add_argument(
        "--trigger-loc-key", dest="trigger_loc_key", help="触发器的 DescriptionLocKey"
    )
    parser.add_argument(
        "--trigger-count", dest="trigger_count", type=int, help="触发器的 Count（无符号整数）"
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    """三个动作共用的输出格。"""
    parser.add_argument(
        "--project-root",
        dest="project_root",
        help="补丁柜落点（<它>/patches/，默认当前目录）；还原走 assets restore",
    )
    parser.add_argument("--dry-run", action="store_true", help="只算不写（与默认同义）")
    parser.add_argument(
        "--write", action="store_true", help="真写：写新文件 ＋ 内核写后复核 ＋ 印反向 Δ 记录"
    )
    parser.add_argument("--out", help="--write 的落点：新文件（默认姿势；源件只读）")
    parser.add_argument(
        "--in-place",
        dest="in_place",
        action="store_true",
        help="--write 的显式逃生门：写同目录临时文件 -> 复核通过 -> 同卷改名就位"
        "（⛔ 与 --out 互斥）",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )


# --------------------------------------------------------------------------- #
# 计划（干跑）：全转 user_items 那一层，这里不自己碰字节
# --------------------------------------------------------------------------- #
def _given(args: argparse.Namespace) -> dict[str, Any]:
    """命令行**真的给了**的那几格（`None` = 没给，⛔ 与"给了 0"分开）。"""
    out: dict[str, Any] = {}
    for name, _flag in _FIELD_FLAGS:
        value = getattr(args, name, None)
        if value is not None:
            out[name] = value
    return out


def _trigger_values(args: argparse.Namespace) -> dict[str, Any]:
    """命令行**真的给了**的那几格触发器字段（`None` = 没给，⛔ 与"给了 0 / 空串"分开）。

    键是 `ui.make_trigger / ui.update_trigger` 的**形参名**（description_loc_key / count）。
    """
    out: dict[str, Any] = {}
    for name, key, _flag in _TRIGGER_ARGS:
        value = getattr(args, name, None)
        if value is not None:
            out[key] = value
    return out


def _missing_trigger_flags(given: Mapping[str, Any]) -> list[str]:
    """三格还差哪几个（报错里点名 `--xxx`，不是内部字段名）。"""
    return [flag for _name, key, flag in _TRIGGER_ARGS if key not in given]


def _new_item_triggers(args: argparse.Namespace, reference: ui.SkinItem) -> tuple[Any, ...]:
    """`add` 那一支：新条目的触发器 —— **空表** / **三格全给的自定义** / **照抄参照条**。"""
    given = _trigger_values(args)
    drop = bool(getattr(args, "drop_triggers", False))
    if drop and given:
        raise AssetError(
            "--drop-triggers（置空表）与 --trigger / --trigger-loc-key / --trigger-count 互斥",
            hint="要么空表、要么三格全给自己那一条 —— ⛔ 没有「半给」这一档",
        )
    if not given:
        return () if drop else reference.triggers
    missing = _missing_trigger_flags(given)
    if missing:
        raise AssetError(
            f"自定义触发器要三格全给，缺 {' / '.join(missing)}",
            hint="⛔ 本组不造默认值：Trigger / DescriptionLocKey / Count 都由你给；"
            "要「没有解锁条件」就用 --drop-triggers（那是空表，不是某个值）",
        )
    return (ui.make_trigger(**given),)


def _trigger_action(args: argparse.Namespace) -> tuple[str, int] | None:
    """`update` 上那三个触发器动作：**一次只许一个**（每个动作各自可撤）。

    返回 (动作名, 下标)；一个都没给就 None；给了不止一个 ⇒ 抛。
    """
    chosen: list[tuple[str, int]] = []
    if bool(getattr(args, "trigger_add", False)):
        chosen.append((TRIGGER_ADD, -1))
    for name, label in (("trigger_remove", TRIGGER_REMOVE), ("trigger_set", TRIGGER_SET)):
        value = getattr(args, name, None)
        if value is not None:
            chosen.append((label, int(value)))
    if len(chosen) > 1:
        raise AssetError(
            "一次只许一个触发器动作：" + "、".join(label for label, _ in chosen),
            hint="新增 / 删除 / 修改 是三个**各自可撤**的动作（AGENTS §0.10）"
            "—— 要连着做就分几次跑，每次都能单独撤回去",
        )
    return chosen[0] if chosen else None


def build_plan(args: argparse.Namespace) -> ui.UserItemsPlan:
    """**只读**：按 `args` 算出一份写回计划（⛔ 一个字节都没写）。"""
    verb = str(getattr(args, "visible_action", "") or "")
    container = Path(str(getattr(args, "container", "") or ""))
    path_id = int(getattr(args, "path_id", 0) or 0)
    unit_id = int(getattr(args, "unit_id", 0) or 0)
    item_id = int(getattr(args, "item_id", 0) or 0)
    changes = _given(args)
    if verb == "add":
        reference_id = int(getattr(args, "reference_id", 0) or 0)
        reference = ui.item_of(
            ui.read_container_config(container, path_id).tree, unit_id, reference_id
        )
        item = ui.SkinItem(
            item_id=item_id,
            skin_pack=changes.get("skin_pack", reference.skin_pack),
            triggers=_new_item_triggers(args, reference),
            is_default=int(changes.get("is_default", reference.is_default)),
            free=int(changes.get("free", reference.free)),
            disabled=int(changes.get("disabled", reference.disabled)),
        )
        return ui.plan_add(
            container, path_id, unit_id=unit_id, item=item, reference_id=reference_id
        )
    if verb == "remove":
        return ui.plan_remove(container, path_id, unit_id=unit_id, item_id=item_id)
    if verb == "update":
        action = _trigger_action(args)
        given_trigger = _trigger_values(args)
        if action is None:
            if given_trigger:
                raise AssetError(
                    "给了触发器那几格，却没给动作",
                    hint="配上 --trigger-add（新增一条 · 三格全给）/ --trigger-set <下标>（修改）"
                    " / --trigger-remove <下标>（删除）—— ⛔ 只说值不说做什么，本组不猜",
                )
            if not changes:
                raise AssetError(
                    f"修改 Id={item_id} 却一个字段都没给 ⇒ 空改动",
                    hint="至少给一个：--skin-pack / --is-default / --free / --disabled，"
                    "或一个触发器动作（--trigger-add / --trigger-set / --trigger-remove）",
                )
            return ui.plan_update(
                container, path_id, unit_id=unit_id, item_id=item_id, changes=changes
            )
        if changes:
            raise AssetError(
                "触发器动作不能与 --skin-pack / --is-default / --free / --disabled 同时给",
                hint="每个动作**各自可撤**（AGENTS §0.10）—— 一次只做一个；"
                "要连着改就分几次跑，每次都能单独撤回去",
            )
        label, index = action
        item = ui.item_of(
            ui.read_container_config(container, path_id).tree, unit_id, item_id
        )
        if label == TRIGGER_SET:
            if not given_trigger:
                raise AssetError(
                    f"修改 Id={item_id} 的第 {index} 条触发器却一格都没给 ⇒ 空改动",
                    hint="至少给一格：--trigger / --trigger-loc-key / --trigger-count",
                )
            changed = ui.update_trigger(item, index=index, **given_trigger)
        elif label == TRIGGER_REMOVE:
            if given_trigger:
                raise AssetError(
                    f"删除第 {index} 条触发器只认下标",
                    hint="--trigger / --trigger-loc-key / --trigger-count 对新值才有意义 ——"
                    " 删除时给它们说明你要做的不是删除（要改值就 --trigger-set）",
                )
            changed = ui.remove_trigger(item, index=index)
        else:
            missing = _missing_trigger_flags(given_trigger)
            if missing:
                raise AssetError(
                    f"新增一条触发器要三格全给，缺 {' / '.join(missing)}",
                    hint="⛔ 本组不造默认值：Trigger / DescriptionLocKey / Count 都由你给",
                )
            changed = ui.add_trigger(item, **given_trigger)
        return ui.plan_update(
            container,
            path_id,
            unit_id=unit_id,
            item_id=item_id,
            changes={"triggers": changed.triggers},
            operation=f"{label}触发器",
        )
    raise AssetError(
        f"不认识的动作 {verb!r}", hint=f"这一组只有 {' / '.join(VERBS)} 三个"
    )


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def dispatch(args: argparse.Namespace) -> int:
    """`material` 组的**入口**：`material_group == "visible"` 走本模块，
    其余原样转 `material_edit.run`。

    ⛔ 不改 `material_edit.py` 的动词：`handlers["material"]` 换成这一个函数，
    `skin` / `slot` 那五支逐字不变。
    """
    if str(getattr(args, "material_group", "") or "") == GROUP:
        return run(args)
    # ⚠ 在**函数里** import：拆环 —— material_edit 要顶层 import 本模块来注册 visible，
    #   本模块若在模块级 import 它，两边就成环（cli/** 的 PLC0415 已在 pyproject 关掉）。
    from ba_mod_maker.cli import material_edit

    return material_edit.run(args)


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    verb = str(getattr(args, "visible_action", "") or "")
    if verb not in VERBS:
        emitter.failure(f"不认识的动作 {verb!r}", hint=f"这一组只有 {' / '.join(VERBS)} 三个")
        return EXIT_UNUSABLE
    container = Path(str(getattr(args, "container", "") or ""))
    if not container.is_file():
        emitter.failure(
            f"打不开容器 {container}",
            hint="--container 给装着 UserItemsConfig 的那个文件（真件是 data.unity3d）",
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
    out_arg = str(getattr(args, "out", "") or "")
    in_place = bool(getattr(args, "in_place", False))
    if out_arg and in_place:
        emitter.failure(
            "--out 与 --in-place 不能同时给（写新文件 vs 原地覆盖）",
            hint="默认用 --out <新文件>；确实要原地再加 --in-place"
            "（它先写临时文件、复核过了才就位）",
        )
        return EXIT_UNUSABLE
    if write and not (out_arg or in_place):
        emitter.failure(
            "--write 要落点：--out <新文件>（默认姿势）或 --in-place（显式逃生门）",
            hint="⛔ 本组不会默认原地覆盖 —— 落点是 6.63 GB 的 data.unity3d",
        )
        return EXIT_UNUSABLE
    store: reverse_delta.PatchStore | None = None
    if write:
        try:
            store = _patch_store(_project_root(args), container=container)
        except BaModMakerError as exc:
            emitter.failure(exc.message, hint=exc.hint)
            return EXIT_UNUSABLE
    try:
        plan = build_plan(args)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读不了 {container}：{exc}")
        return EXIT_UNUSABLE
    result: ui.InstallResult | None = None
    dirty = ""
    if write:
        try:
            if store is None:  # 内部不变式：上面 write 那一段已经开好了
                emitter.failure("补丁柜没开成")
                return EXIT_UNUSABLE
            result, dirty = _install(
                plan, container=container, out_arg=out_arg, in_place=in_place, store=store
            )
        except (BaModMakerError, OSError) as exc:
            emitter.failure(f"写回失败：{exc}", hint=f"{container}（内核复核不过时它会删掉半成品）")
            return EXIT_UNUSABLE
    _report(emitter, plan=plan, verb=verb, result=result, dirty=dirty)
    emitter.document(_document(plan=plan, verb=verb, result=result, dirty=dirty))
    if dirty:
        return EXIT_DIRTY
    return EXIT_DONE


def _project_root(args: argparse.Namespace) -> Path:
    """工程目录口径：给了就用，没给就当前目录（补丁柜落 <工程目录>/patches）。"""
    return Path(str(getattr(args, "project_root", None) or Path.cwd()))


def _patch_store(root: Path, *, container: Path) -> reverse_delta.PatchStore:
    """补丁柜（ADR 0007 的反向 Δ）：<--project-root>/patches。⛔ 不拷整包当备份。

    ⚠ **红线**：补丁柜必须跟随被改的那份文件所在的**盘**（`reverse_delta.assert_same_drive`
    的同一口径，无豁免）—— 在这一步就拦，别等 4 分钟写完才发现补丁落错了盘。
    """
    store_root = reverse_delta.patch_store_root(root)
    reverse_delta.assert_same_drive(store_root, container)
    return reverse_delta.PatchStore(store_root)


def _install(
    plan: ui.UserItemsPlan,
    *,
    container: Path,
    out_arg: str,
    in_place: bool,
    store: reverse_delta.PatchStore,
) -> tuple[ui.InstallResult, str]:
    """写侧那一趟：**产物核对**（不在盘上 / 大小对不上 ⇒ 一条"结果不干净"的话）。

    ⚠ `--in-place` 那条链：内核写的是同目录临时文件、本函数再改名就位
    ⇒ 补丁记的落点要传 `patch_product=container`（不然它记的是那个已经不在的临时路径）。
    """
    if not in_place:
        result = ui.install(plan, Path(out_arg), patch_store=store)
        return result, _audit(result, Path(out_arg))
    temp = container.with_name(container.name + _TEMP_SUFFIX)
    temp.unlink(missing_ok=True)
    result = ui.install(plan, temp, patch_store=store, patch_product=container)
    os.replace(temp, container)
    # 产物换成了源件本身 ⇒ 反向 Δ 的 container 跟着改（内核记的是临时文件那条路径）
    moved = _replace(
        result, target=container, reverse=_replace(result.reverse, container=container)
    )
    return moved, _audit(moved, container)


def _audit(result: ui.InstallResult, target: Path) -> str:
    """**产物核对**（便宜且机械）：文件在不在、大小与内核读数是否一致。"""
    if not target.is_file():
        return f"产物不在盘上：{target}"
    got = target.stat().st_size
    want = int(getattr(result.report, "new_size", 0) or 0)
    if want and got != want:
        return f"产物大小 {got:,} B 与读数 {want:,} B 对不上（{target}）"
    return ""


def _report(
    emitter: Emitter,
    *,
    plan: ui.UserItemsPlan,
    verb: str,
    result: ui.InstallResult | None,
    dirty: str,
) -> None:
    """人读那几行：**一次动作只报一条判据**（条目数 ＋ 对象字节 ＋ 改动区间）。"""
    emitter.field("容器", plan.container)
    emitter.field("对象", f"pathID={plan.path_id}（m_Name={plan.config_name!r}）")
    label = {"add": "新增", "remove": "删除", "update": "修改"}[verb]
    emitter.field("动作", f"可见性·{label}")
    emitter.field("单位", f"{plan.unit_id}（Skins 共 {plan.unit_count} 个单位）")
    for line in plan.criterion_lines():
        emitter.line(f"  {line}")
    emitter.line(f"[判据] {plan.count_line()}")
    if result is None:
        emitter.field("写盘", "（干跑：一个字节都没写；加 --write 才落字节）")
        return
    emitter.field("产物", result.target)
    emitter.line(f"  {result.report.summary()}")
    for line in result.lines:
        emitter.line(f"  {line}")
    if dirty:
        emitter.line(f"★ 结果不干净：{dirty}")
    emitter.field("反向 Δ", result.reverse.label())
    emitter.line(f"  记录：{result.reverse.record()}")
    emitter.line(
        "  ⚠ 原始字节（"
        f"{len(result.reverse.old_bytes):,} B，sha256 {_sha16(result.reverse.old_bytes)}）"
        "只够本进程自己用；**事后还原靠补丁柜那一笔**（下一段）"
    )
    if result.patch_path is None:
        emitter.field("补丁柜", "⚠ 没落成 —— ⛔ 别拿这个产物去装机（撤不回来）")
        return
    emitter.field("补丁柜", f"{result.patch_path}（{result.patch_bytes:,} B）")
    for line in result.patch_lines:
        emitter.line(f"  {line}")
    emitter.line("  还原：ba-mod-maker assets restore --project-root <上面那个工程目录>")


def _document(
    *, plan: ui.UserItemsPlan, verb: str, result: ui.InstallResult | None, dirty: str
) -> dict[str, Any]:
    """`--json` 那一份（只留标量 ＋ 反向 Δ 的 record）。"""
    record: Mapping[str, Any] | None = None
    target: str | None = None
    wrote = False
    if result is not None:
        record = result.reverse.record()
        target = str(result.target)
        wrote = True
    return {
        "group": GROUP,
        "verb": verb,
        "operation": str(plan.operation),
        "container": str(plan.container),
        "node_path": str(plan.node_path),
        "path_id": int(plan.path_id),
        "config_name": str(plan.config_name),
        "unit_id": int(plan.unit_id),
        "unit_count": int(plan.unit_count),
        "count_before": len(plan.items_before),
        "count_after": len(plan.items_after),
        "items_before": [item.summary() for item in plan.items_before],
        "items_after": [item.summary() for item in plan.items_after],
        "touched": [int(x) for x in plan.touched],
        "delta": int(plan.delta),
        "level": str(plan.level),
        "abs_start": int(plan.abs_start),
        "dry_run": result is None,
        "target": target,
        "wrote": wrote,
        "dirty": dirty or None,
        "verify_lines": list(result.lines) if result is not None else [],
        "reverse_delta": dict(record) if record is not None else None,
        # ⚠ record() 里**没有原始字节**（只有 old_size）—— 它是「够不够还原」的诚实边界，
        # 这里补两条可复算的读数，让人一眼看出「字节存在过、但没被本组落盘」。
        "reverse_old_bytes_size": len(result.reverse.old_bytes) if result is not None else 0,
        "reverse_old_bytes_sha256": (
            hashlib.sha256(result.reverse.old_bytes).hexdigest() if result is not None else None
        ),
        # 补丁柜那一笔（range 反向 Δ）—— 它是「事后还撤得掉」的唯一来源。
        "patch_path": (
            None if result is None or result.patch_path is None else str(result.patch_path)
        ),
        "patch_bytes": 0 if result is None else int(result.patch_bytes),
        "patch_lines": [] if result is None else list(result.patch_lines),
    }


def _sha16(value: bytes) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()[:16]
