"""CLI 入口：`ba-mod-maker` 或 `python -m ba_mod_maker.cli`。

两个用途：

1. **GUI**（默认）—— 双击 `scripts\\run.bat` 走的就是这里；
2. **无人值守的数据库操作** —— 打开 / 提取 / 导入 / 查看 / 改值。
   这一组是 GUI 的同一套 `core`，所以它同时是"能脱离界面验证链路"的手段。

四个动作与三种文件的关系见 `core/session.py` 的模块文档。

## 子命令

| 子命令 | 干什么 | 退出码 |
|---|---|---|
| `gui`（默认） | 起图形界面 | —— |
| `info` / `tables` / `rows` / `set` | 看文件、看表、看行、改一格 | 0 / 2 |
| `extract` / `import` / `patch` | 导出、写回容器、打/卸补丁 | 0 / 1 / 2 |
| **`validate`** | 跑 93 条规则校验一份数据源 | **0 无 error / 1 有 error / 2 打不开源** |
| **`find`** | 谁引用了这一行 | **0 有引用 / 1 没有 / 2 用不了** |
| **`newasset add`** | **新增一张自己的图**：新包 + catalog 两条 + 数据库一行 | 0 / 1 / 2 |
| **`newasset add-bundle`** | **只落包 ＋ 登记一条地址**（⛔ 不碰数据库 · ⛔ 不打 DLL） | 0/1/2 |
| **`newasset restore`** | 全撤：反向 Δ 逐位还原 + 删掉我们加的包（+ 本单打的 DLL） | 0 / 1 |
| **`clone-unit`** | **新增一个单位**：把 `--id` 那一行整套克隆成新单位 | 0 / 1 / 2 |
| **`render`** | **渲染闸**：AssetBundle 丢进 Unity batchmode 出图 | 0 / 1 / 2 |
| **`object`** | **资产三动词**：`object set` / `add` / `delete`（⛔ 无「换」） | 0 / 1 / 2 |
| **`texture`** | **贴图链**：PNG → BC3＋满 mip → 带进包 → 接材质槽 | 0 / 1 / 2 |
| **`address`** | **地址线（写）**：`address add` / `delete`（catalog 地址条目） | 0 / 1 / 2 |
| **`catalog`** | **地址清单（只读）**：`catalog key` / `bundle` / `path` | 0 / 1 / 2 |
| **`material`** | **材质线**：`skin add/remove/update` · `slot add/remove` | 0 / 1 / 2 |

⚠ 两个名字只差一个字母，别点错：
**`assets` = 补丁柜**（看状态 / 还原 / 丢弃那些**已经装了**的东西）；
**`newasset` = 新增一张自己的图**（包 + 地址 + 数据库那一行）。
**`assets graph <键>`** = **资产目录**（只读盘点，不写盘）：这台车用了哪些模型包 / prefab /
渲染器 / 材质 / 贴图 / VT 池，每条带可读名 ＋ pathID ＋ 出处在哪个包。

`validate` 与 `find` 的输出**统一走 `cli/emit.py`**（审查 P2-12：本文件里已经攒了 47 处
裸 `print`，新子命令不再加第 48 处）：不加 `--json` 时给人读，加了就往 stdout 只写一个
JSON 文档、人读那份改走 stderr。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ba_mod_maker.cli import (
    address_edit,
    address_inventory,
    anim,
    asset_graph,
    catalog_crc,
    diag,
    emit,
    finder,
    material_edit,
    material_visible,
    names,
    object_container,
    object_edit,
    object_node,
    render,
    row_delete,
    row_restore,
    texture,
    validator,
    vt,
    vt_bake,
)

#: 退出码约定（审查 P2-12：「退出码未成文」）。**只此一处**；
#: `validate` / `find` 各自的 `--help` 里也写着自己那一档。
EXIT_CODES_HELP = """退出码：
  0  正常完成（validate：没有 error；find：找到了引用者）
  1  结果不干净（validate：有 error；find：没有引用者）
  2  用不了（打不开源 / 表名或 Id 不对 / 参数不对）

validate 与 find：不加 --json 时给人读；加了 --json 时 stdout 上只有一份 JSON 文档，
人读那份改走 stderr。
"""


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="三种文件之一，或游戏根目录（自动找 data.unity3d）")
    p.add_argument("-o", "--out", help="输出文件")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ba-mod-maker",
        description="断箭 Mod 制作工具 · 数据表本体",
        epilog=EXIT_CODES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="store_true", help="打印版本并退出")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="启动图形界面（默认）")

    # ---- 四种文件通道 ----
    p = sub.add_parser("info", help="查看一个文件里有什么（不动数据）")
    p.add_argument("source", help="data.unity3d / DataBaseCompiled.json / BA_Mod.zip / 游戏根目录")

    p = sub.add_parser("extract", help="导出 BA_Mod.zip（从容器或 DataBaseCompiled.json）")
    _add_common(p)
    p.add_argument("-t", "--tables", help="只导这几张表（逗号分隔），默认全部 24 张")

    p = sub.add_parser("import", help="把 DataBaseCompiled.json 或 BA_Mod.zip 写进 data.unity3d")
    p.add_argument("container", help="目标 data.unity3d（或游戏根目录）")
    p.add_argument("incoming", help="来源 DataBaseCompiled.json 或 BA_Mod.zip")
    p.add_argument("--no-verify", action="store_true", help="跳过写后复核")
    p.add_argument("--no-sha256", action="store_true", help="跳过写前 sha256 核对")
    p.add_argument("--allow-running", action="store_true", help="允许游戏在跑时写入（不推荐）")
    p.add_argument("--dry-run", action="store_true", help="只算差异，不写盘")

    # ---- 查看与改值 ----
    p = sub.add_parser("tables", help="列出 24 张表与行数")
    p.add_argument("source")

    p = sub.add_parser("rows", help="打印一张表的若干行")
    p.add_argument("source")
    p.add_argument("table", help="表名，如 Units")
    p.add_argument("--limit", type=int, default=5, help="打印几行（默认 5）")
    p.add_argument("--offset", type=int, default=0, help="从第几行开始")
    p.add_argument("--columns", help="只看这几列（逗号分隔）")

    p = sub.add_parser(
        "patch",
        help="外部数据库补丁（GameAssembly.dll）：status / apply / restore / verify",
    )
    p.add_argument("action", choices=("status", "apply", "restore", "verify"), help="要做什么")
    p.add_argument("--game", help="游戏根目录（默认自动探测）")
    p.add_argument(
        "--force",
        action="store_true",
        help="已废弃：版本闸不许绕过（认不出的版本一律拒绝）。保留只为命令行兼容",
    )
    p.add_argument("--allow-running", action="store_true", help="允许游戏在跑时写入")

    # ---- 资产写回通道（ADR 0007：就地安装 + 反向 Δ，⛔ 不许拷整包当备份）----
    p = sub.add_parser(
        "assets",
        help="补丁柜：status / restore / discard · 资产目录：graph <键>（只读盘点）",
        description=(
            "补丁柜：status 看那条反向 Δ · restore 一条命令还原 · discard 只丢补丁不动文件。\n"
            "assets graph <键> = 只读资产目录盘点（不写盘）。\n"
            "\n"
            "⚠ 使用陷阱（2026-10-11 真踩）：restore 装回去的是补丁记的**「装前」那一刻**。\n"
            "   如果你已经用**语义反向动作**撤过装（material visible remove /\n"
            "   反向的 skin install），那条补丁记的就成了「中间态」——\n"
            "   先 assets status 核对 sha256，**撤装之后别再敲 restore**，\n"
            "   要么先 assets discard（⛔ discard 会丢掉退路）。\n"
            "\n"
            "restore 部分成功时会**把已经还原回去的目标从柜里剪掉**，只留还没还原的那几条。"
        ),
    )
    p.add_argument("action", choices=("status", "restore", "discard", "graph"), help="要做什么")
    p.add_argument(
        "key",
        nargs="?",
        help="graph 用的地址键 = Units.ModelFileName 的原样值（例 RU_T72B）；⛔ 不是显示名",
    )
    p.add_argument("--project-root", help="工程目录（补丁柜在 <工程目录>\\patches，默认当前目录）")
    p.add_argument("--game", help="游戏根目录（默认自动探测；只用来核对补丁柜跟游戏同盘）")
    p.add_argument("--force", action="store_true", help="restore 跳过 sha256 校验（⛔ 危险）")
    p.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="graph 用：只输出一个 JSON 文档（人读那份改走 stderr）",
    )

    p = sub.add_parser("skin", help="材质槽通道：修改 units 材质槽的指向（真机验过的那个 8 字节）")
    p.add_argument("action", choices=("slots", "plan", "install"), help="要做什么")
    p.add_argument(
        "--unit",
        help="地址键 = DB 的 Units.ModelFileName 原样值（例 RU_BMD-2\\RU_BMD_2_base）；"
        "⛔ 不是显示名（敲错会列出候选键，不做自动解析）",
    )
    p.add_argument("--material", help="材质名，例 'RU_BMD-2 1'")
    p.add_argument("--slot", help="贴图槽名，例 Layer_A97CDC25")
    p.add_argument("--texture", help="目标贴图名，例 BMD-2_3_BaseMap")
    p.add_argument("--game", help="游戏根目录（默认自动探测）")
    p.add_argument("--project-root", help="工程目录（补丁柜落点，默认当前目录）")

    p = sub.add_parser("db", help="外部库通道（图标键）：BA_Mod.zip 装/卸/撤")
    p.add_argument(
        "action",
        choices=("status", "install", "remove", "unremove", "restore"),
        help=(
            "要做什么（remove = 卸载 · unremove = 按卸载台账搬回去；"
            "restore = 按**补丁柜**还原「装之前那一份」，与 unremove 不是一回事）"
        ),
    )
    p.add_argument("--game", help="游戏根目录（默认自动探测）")
    p.add_argument("--project-root", help="工程目录（补丁柜与卸载去向的落点，默认当前目录）")
    p.add_argument("--source", help="要装进去的 BA_Mod.zip（默认 <工程目录>\\BA_Mod.zip）")
    p.add_argument("--dry-run", action="store_true", help="只算不写")
    # 卸载（remove）：⛔ 不是 restore —— restore 还原的是「装之前那一份」。
    p.add_argument(
        "--file",
        dest="remove_files",
        action="append",
        default=[],
        metavar="相对路径",
        help="remove 要额外卸的文件（相对游戏根）／unremove 要搬回哪几件（给基名也认）；可给多次",
    )

    # ---- 资产新增（一条命令：PNG -> 游戏里出现；另一条命令全撤）----
    # handler 在 _cmd_newasset；两者都**只加**，不动上面已有的 assets / skin / db / patch。
    # ⚠ 名字叫 newasset 不叫 asset：与既有 assets（补丁柜）只差一个字母，迟早点错。
    p = sub.add_parser(
        "newasset",
        help="资产新增：一条命令把 PNG 装进游戏（add / restore）；⛔ 不是 assets（那是补丁柜）",
    )
    asset = p.add_subparsers(dest="action")
    q = asset.add_parser(
        "add",
        help="把一张图装成某个单位的图标（新增：新包 + catalog 两条 + DB 一行 + DLL）",
    )
    q.add_argument("--png", required=True, help="用户的图（PNG / JPG）")
    q.add_argument(
        "--unit",
        required=True,
        help="Units.ModelFileName 的**原样值**（例 RU_BMD-2\\RU_BMD_2_base）；不唯一时要加 --id",
    )
    q.add_argument("--key", required=True, help="新的资产键（例 MY_BMD2-Label）")
    q.add_argument("--id", dest="row_id", type=int, help="ModelFileName 不唯一时用 Units.Id 指定行")
    q.add_argument(
        "--fit",
        choices=("blur", "fill", "plain"),
        default="blur",
        help="构图：blur（默认，真机验过的那一版）/ fill / plain",
    )
    q.add_argument("--game", help="游戏根目录（默认自动探测）")
    q.add_argument("--project-root", help="工程目录（补丁柜落点，默认当前目录）")
    q.add_argument("--dry-run", action="store_true", help="只算不写（游戏目录 / 工程目录零写入）")
    q.add_argument(
        "--force-dll",
        action="store_true",
        help="已废弃：版本闸不许绕过；只为与 --keep-dll 的互斥判据保留",
    )
    q = asset.add_parser(
        "add-model",
        help="新增一个单位模型（新包 + catalog 一条模型地址 + Units.ModelFileName + DLL）",
    )
    q.add_argument("--bundle", required=True, help="已造好的 UnityFS 包（本命令只搬不改）")
    q.add_argument(
        "--unit", required=True, help="Units.ModelFileName 的**原样值**（要指过去的那一行）"
    )
    q.add_argument("--key", required=True, help="新的资产键（例 RouteBUnit）")
    q.add_argument("--id", dest="row_id", type=int, help="ModelFileName 不唯一时用 Units.Id 指定行")
    q.add_argument(
        "--turret-row",
        type=int,
        default=None,
        help="按炮塔装：改 Turrets 表第 N 行（0 起，`rows <源> Turrets` 打印的下标）"
        "的 ModelFileName；"
        "给了它就不碰 Units 行（炮塔资产的动态加载入口）",
    )
    q.add_argument(
        "--asset-path",
        help="catalog 的 internalId（默认 = 包里那条唯一的 .prefab 容器地址；"
        "读不开包才退回 Assets/Resources_moved/ModelPrefabs/<键>.prefab）",
    )
    q.add_argument(
        "--dependency-source",
        # 2026-10-27 起 add-model 默认 own-only（真机验过）⇒ 这列不再是必填项，
        # 从 --help 撤显；只有 --dependency-mode auto 那一档才用得到。
        help=argparse.SUPPRESS,
    )
    q.add_argument(
        "--dependency-mode",
        dest="dependency_mode",
        choices=["auto", "own-only"],
        default="own-only",
        help="依赖声明怎么定：own-only=只依赖自己那个包（**默认**，2026-10-27 真机验过）；"
        "auto=照抄 --dependency-source 那条（只给带跨 bundle 引用的包用）",
    )
    q.add_argument(
        "--facts",
        help="装配那一步写下的 facts.json（给了就与包**对账**，对不上拒绝）",
    )
    q.add_argument("--game", help="游戏根目录（默认自动探测）")
    q.add_argument("--project-root", help="工程目录（补丁柜落点，默认当前目录）")
    q.add_argument("--dry-run", action="store_true", help="只算不写（游戏目录 / 工程目录零写入）")
    q.add_argument(
        "--force-dll",
        action="store_true",
        help="已废弃：版本闸不许绕过（认不出的版本一律拒绝）",
    )
    q = asset.add_parser(
        "add-bundle",
        help="只落包 + 只登记一条地址（⛔ 不写 BA_Mod.zip · ⛔ 不碰 Units 行 · ⛔ 不打 DLL）",
    )
    q.add_argument("--bundle", required=True, help="已造好的 UnityFS 包（本命令只搬不改）")
    q.add_argument("--key", required=True, help="新的 catalog 地址键（例 AddBundleTest）")
    q.add_argument(
        "--asset-path",
        help="catalog 的 internalId（默认 Assets/Resources_moved/ModelPrefabs/<键>.prefab）",
    )
    q.add_argument(
        "--dependency-source",
        required=True,
        help="依赖声明抄哪条**已入库**的地址键（这一档必填，例 RU_T72B）",
    )
    q.add_argument("--game", help="游戏根目录（默认自动探测）")
    q.add_argument("--project-root", help="工程目录（补丁柜落点，默认当前目录）")
    q.add_argument("--dry-run", action="store_true", help="只算不写（游戏目录 / 工程目录零写入）")
    q = asset.add_parser(
        "repoint",
        help="修改：把 Units.ModelFileName 指到一条**已登记**的 catalog 键（不新增条目）",
    )
    q.add_argument(
        "--unit", required=True, help="这一行**现在**的 ModelFileName 原样值（例 A2BR8）"
    )
    q.add_argument(
        "--key", required=True, help="要指过去的键；必须**已经在 catalog 里**（例 A2BR4）"
    )
    q.add_argument("--id", dest="row_id", type=int, help="ModelFileName 不唯一时用 Units.Id 指定行")
    q.add_argument("--game", help="游戏根目录（默认自动探测）")
    q.add_argument("--project-root", help="工程目录（补丁柜落点，默认当前目录）")
    q.add_argument("--dry-run", action="store_true", help="只算不写（游戏目录 / 工程目录零写入）")
    q = asset.add_parser(
        "restore", help="全撤：catalog / BA_Mod.zip 反向 Δ + 删掉我们加的包（+ 本单打的 DLL）"
    )
    q.add_argument("--game", help="游戏根目录（默认自动探测）")
    q.add_argument("--project-root", help="工程目录（补丁柜落点，默认当前目录）")
    # 两条 DLL 开关**互斥**（Lead 2026-10-06 定）：restore 这一侧也挂一条 --force-dll，
    # 只为让「同时给」在任何子命令上都是 argparse 报错（handler 里还会拦 restore --force-dll）。
    dll_group = q.add_mutually_exclusive_group()
    dll_group.add_argument(
        "--keep-dll",
        action="store_true",
        help="只撤文件、保留 DLL 现场（要撤 DLL 自己跑 patch restore）",
    )
    dll_group.add_argument(
        "--force-dll",
        action="store_true",
        help="与 --keep-dll 互斥（同时给就报错）；这条只在 add 上有效",
    )

    # ---- 新增一个单位（一条命令；与研究脚本 _work/route-b/routeb_newunit.py 同链）----
    # handler 在 _cmd_clone_unit。⛔ 产品不许 import _work 里的研究脚本，那份只当口径参照。
    p = sub.add_parser(
        "clone-unit",
        help="新增一个单位：把 --id 那一行整套克隆成新单位（新 Id = 库里最大 + 1）",
        epilog=(
            "退出码：\n"
            "  0  完成\n"
            "  1  写盘失败（写后复核不过 / 源被改过 / 磁盘错）\n"
            "  2  用不了（打不开源 / --id 不存在 / 没有落点）\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--source",
        required=True,
        help="三种文件之一（BA_Mod.zip / DataBaseCompiled.json / data.unity3d；⛔ 别拿真件试）",
    )
    p.add_argument(
        "--id",
        dest="row_id",
        type=int,
        required=True,
        help="要克隆的源单位 Units.Id（行下标不是 Id；新 Id 由库里的最大 Id + 1 算出来）",
    )
    p.add_argument("--name", help="新单位名（同时写 Name 与 HUDName；不给 = 继承源行）")
    p.add_argument(
        "--model-key",
        dest="model_key",
        help="新单位 Units.ModelFileName（不给 = 继承源行，即共用源行那个模型）",
    )
    p.add_argument("--out", help="另存一份 BA_Mod.zip 到这里；省略 = 写回打开的那个文件")
    p.add_argument(
        "--hidden",
        action="store_true",
        help="逃生门：新行 DisplayInArmory=false（默认 true = 新单位出现在机库）",
    )
    p.add_argument(
        "--as-modification",
        dest="as_modification",
        action="store_true",
        help="逃生门：新行 IsUnitModification=true（默认 false = 它是一个独立单位，不是改装行）",
    )
    p.add_argument("--dry-run", action="store_true", help="只算不写（源文件与磁盘零写入）")

    # ---- 装机前检查一个模型包（产品自己的三把尺子；只读）----
    p = sub.add_parser(
        "check-model",
        help="装机前检查一个模型包：出厂闸(A0~A6) + 整包 PPtr 普查 + 节点命名契约"
        " + catalog 四件套",
        epilog=(
            "这一条**只读**：不写游戏目录、不写工程目录。\n"
            "退出码：\n"
            "  0  全部通过，可以装机\n"
            "  1  有红（悬空 PPtr / 缺 MonoScript / 缺 UnitPrefabRoot / 节点命名契约 / 解析链断）\n"
            "  2  用不了（包读不开 / 找不到游戏目录）\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--bundle",
        help="要检查的 UnityFS 包（已造好、还没装的那个）；--spec --unit 那一档不需要它",
    )
    p.add_argument(
        "--spec",
        action="store_true",
        help="按**原版规格**体检（只读）：纹素密度 / UV 两条读数 / tris / 贴图档。",
    )
    p.add_argument(
        "--unit",
        help="--spec：读**游戏里那一件原版资产**（键 = Units.ModelFileName 的原样值，"
        "例 DLC2_XA180EST）",
    )
    p.add_argument(
        "--raster",
        default="2048,4096,8192",
        help="--spec：覆盖率要栅格化的档（逗号分隔；最后一档是收敛档）",
    )
    p.add_argument("--json", dest="json_out", action="store_true", help="--spec：印 JSON")
    p.add_argument(
        "--asset-path",
        help="容器里应该有的那条资产路径（省略 = 只报容器现状）",
    )
    p.add_argument(
        "--unit-prefab",
        action="store_true",
        help="按**单位 prefab** 的判据算红（根上必须有 UnitPrefabRoot、"
        "Skins 的 PPtr 不许指向包外）；"
        "默认只把 A0/A2 算红（士兵/道具这类非单位包不该按单位判）。"
        "⚠ Skins.Data 为空**不算红**（原版 288 个里 37 个就是空的）",
    )
    p.add_argument(
        "--turret-prefab",
        action="store_true",
        help="按**炮塔 prefab** 的判据判节点契约：跳过单位的 Chassis/Base*/轮/悬挂规则，"
        "改判炮塔件必须有 UnitPrefabTurretInfo（引擎 IntegrateBaseTurret 找不到就 LogError "
        "'No turret script on dynamic turret is found' 并放弃挂载）",
    )
    p.add_argument("--key", help="已经装进 catalog 的话给这条键：再核一遍整条解析链")
    p.add_argument("--game", help="游戏根目录（默认自动探测；只在给 --key 时用）")

    p = sub.add_parser("set", help="改一个单元格并写回原文件")
    p.add_argument("source")
    p.add_argument("table", help="表名")
    p.add_argument("row", type=int, help="行下标（0 起）")
    p.add_argument("column", help="列名")
    p.add_argument("value", help="新值（按列类型转换）")
    p.add_argument("--dry-run", action="store_true", help="只显示改动，不写盘")
    p.add_argument("--allow-running", action="store_true", help="允许游戏在跑时写入")

    # ---- 校验与反查引用 ----
    # 注册与实现在 cli/validator.py / cli/finder.py；这里的帮助文本只有一句，
    # 退出码写在各子命令的 --help 里（顶层那份在 EXIT_CODES_HELP）。
    validator.add_parser(sub)
    finder.add_parser(sub)
    vt.add_parser(sub)
    vt_bake.add_parser(sub)
    render.add_parser(sub)
    # ★ 2026-10-12：产品三原子动词里**缺的那一个**（删除数据表整行）。
    #   形状跟着 object 那条路：**默认干跑**，--write 才落字节，写后复核 + 强制反向 Δ。
    row_delete.add_parser(sub)
    # ★ 2026-10-12：删除的**逆操作**。Δ 是产品留的，还原也必须是产品入口
    #   （kb/19「唯一入口」）；往返逐字节的判据钉在 tests/test_cli_row_restore.py。
    row_restore.add_parser(sub)
    object_parser = object_edit.add_parser(sub)
    object_container.add_parser(object_parser)
    # ★ L7：**树的形状**（新增/删除节点、加/删组件）—— object set/add/delete 到不了这一层
    #   （那三个是对象级，container 是地址级）。四个原子动词，⛔ 没有「换」。
    object_node.add_parser(object_parser)
    address_edit.add_parser(sub)
    # ★ 用户需求⑩ H2/H3：catalog 的**地址清单**（只读）—— 键 <-> 实际路径 <-> 包。
    # ★ 2026-10-17：catalog 组里加一个**写**动作 `catalog crc`（改过的包 ⇒ 把既有登记的
    #   m_Crc / m_BundleSize 改对，并同步 aa/catalog.hash；默认干跑）。只读三动作的判定不动。
    # ⛔ 2026-10-31：`pack` 子命令（unity-build / install / restore / list）**整组撤出数据侧** ——
    #   造包与装包的编排已搬到 `断箭资产管理器\kernel\build_pipeline.py`，归资产管理器所有。
    catalog_parser = address_inventory.add_parser(sub)
    catalog_crc.add_crc_action(catalog_parser)
    texture.add_parser(sub)
    material_edit.add_parser(sub)
    names.add_parser(sub)
    # ★ D 线（animline）：动画 —— clip -> Action 规格 / 改时长（同长度）/ 自证。
    #   接不上就报（退出码 1）；⛔ 不静默产出一个空 Action。
    anim.add_parser(sub)
    # ★ Debug 模式（用户 2026-10-17「下一会话要做两个 Debug 模式」）：一行体检 ＋ 环境 ＋ 最近事件。
    #   ⛔ 不新造第二套日志格式 —— 内容全部来自 app/diag/report.py（纯函数、零 Qt）；
    #   ⛔ 本文件也不许在模块级 import 那个包，否则 `--help` 会把 Qt 拉起来（见 cli/diag.py 开头）。
    diag.add_parser(sub)

    return parser


def _fmt(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if len(value) <= 40 else value[:37] + "..."
    return str(value)


def _cmd_info(args: argparse.Namespace) -> int:
    from ba_mod_maker.core import session

    with session.open_source(args.source) as src:
        info = session.describe(src)
        print(f"文件      : {info['path']}")
        print(f"类型      : {info['label']}")
        if info["path_id"] is not None:
            print(f"pathID    : {info['path_id']}")
        print(f"表数      : {info['tables']}")
        print(f"总行数    : {info['rows']:,}")
        if src.kind is session.SourceKind.UNITY3D and src.reader is not None:
            r = src.reader
            print(f"容器      : {len(r.blocks):,} 块 / {len(r.nodes)} 节点 / {r.file_size:,} 字节")
            from ba_mod_maker.core.asset import ufs

            found = ufs.find_database_objects(r)
            print(f"库里对象  : {[p for p, _ in found]}（取 pathID 最大的那个）")
        if src.extra_entries:
            print(f"未知条目  : {list(src.extra_entries)}（保存时原样保留）")
    return 0


def _cmd_tables(args: argparse.Namespace) -> int:
    from ba_mod_maker.core import session

    with session.open_source(args.source) as src:
        print(f"{src.kind.label}  ·  {src.path.name}")
        total = 0
        for slot in src.meta.tables:
            rows = slot.json_payload()
            total += len(rows)
            mark = " *" if slot.dirty else ""
            print(f"  {slot.table:32s} {len(rows):6,d} 行{mark}")
        print(f"  {'合计':32s} {total:6,d} 行")
    return 0


def _cmd_rows(args: argparse.Namespace) -> int:
    from ba_mod_maker.core import session

    with session.open_source(args.source) as src:
        rows = src.table(args.table).json_payload()
        if not rows:
            print(f"{args.table} 是空表")
            return 0
        cols = list(rows[0].keys())
        if args.columns:
            want = [c.strip() for c in args.columns.split(",")]
            missing = [c for c in want if c not in cols]
            if missing:
                print(f"没有这些列：{missing}", file=sys.stderr)
                print(f"可用列：{cols}", file=sys.stderr)
                return 2
            cols = want
        lo = max(args.offset, 0)
        hi = min(lo + args.limit, len(rows))
        print(f"{args.table}  共 {len(rows):,} 行，显示 [{lo}, {hi})")
        for i in range(lo, hi):
            print(f"--- 行 {i} ---")
            for c in cols:
                print(f"    {c:24s} = {_fmt(rows[i].get(c))}")
    return 0


def _coerce(old: object, text: str) -> object:
    """按**原值的类型**转换输入文本。这是 GUI 里 `core.data.columns` 的简化版。

    为什么必须转：JSON 里的值有类型，而命令行敲进来永远是字符串。
    不转就会把 5500 变成 "5500" —— 游戏读到字符串会出错或**静默失效**。
    """
    if isinstance(old, bool):
        low = text.strip().lower()
        if low in ("true", "1", "yes", "y", "是"):
            return True
        if low in ("false", "0", "no", "n", "否"):
            return False
        raise ValueError(f"这是个是非列，要 true/false，收到 {text!r}")
    if isinstance(old, int):
        return int(text)
    if isinstance(old, float):
        return float(text)
    if old is None:
        # 原值是 null：猜一个。纯数字就给数字，否则给字符串。
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text
    return text


def _cmd_set(args: argparse.Namespace) -> int:
    from ba_mod_maker.core import session

    with session.open_source(args.source) as src:
        slot = src.table(args.table)
        rows = slot.json_payload()
        if not 0 <= args.row < len(rows):
            print(f"行下标 {args.row} 越界（{args.table} 共 {len(rows)} 行）", file=sys.stderr)
            return 2
        if args.column not in rows[args.row]:
            print(f"没有列 {args.column}", file=sys.stderr)
            print(f"可用列：{list(rows[args.row].keys())}", file=sys.stderr)
            return 2
        old = rows[args.row][args.column]
        try:
            new = _coerce(old, args.value)
        except ValueError as exc:
            print(f"值转换失败：{exc}", file=sys.stderr)
            return 2
        if old == new:
            print(f"没变化（{args.table}[{args.row}].{args.column} 已经是 {_fmt(old)}）")
            return 0
        rows[args.row][args.column] = new
        slot.set_payload(rows)
        print(f"{args.table}[{args.row}].{args.column}: {_fmt(old)} -> {_fmt(new)}")
        if args.dry_run:
            print("--dry-run：没有写盘")
            return 0
        print(session.save(src, check_game_running=not args.allow_running))
    return 0


def _cmd_patch(args: argparse.Namespace) -> int:
    """外部数据库补丁：装 / 卸 / 查。"""
    from ba_mod_maker.core.asset import dllpatch
    from ba_mod_maker.core.assets import game as game_mod

    root = Path(args.game) if args.game else game_mod.find_game_root_for_db()
    if root is None:
        print("找不到游戏目录，请用 --game 指定", file=sys.stderr)
        return 2

    if args.action == "status":
        st = dllpatch.status(root)
        for line in st.summary_lines():
            print(line)
        print()
        print(dllpatch.uninstall_hint())
        return 0

    if args.action == "verify":
        ok, msg = dllpatch.verify(root)
        print(f"verify: {msg}")
        return 0 if ok else 1

    if args.action == "apply":
        print(dllpatch.apply(root, force=args.force, check_game_running=not args.allow_running))
        print()
        for line in dllpatch.status(root).summary_lines():
            print(line)
        return 0

    print(dllpatch.restore(root, check_game_running=not args.allow_running))
    print()
    for line in dllpatch.status(root).summary_lines():
        print(line)
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from ba_mod_maker.core import session

    with session.open_source(args.source) as src:
        if args.tables:
            want = [t.strip() for t in args.tables.split(",")]
            from ba_mod_maker.core.asset import dbzip
            from ba_mod_maker.core.asset.tables import TABLES

            known = {t.table for t in TABLES}
            unknown = [t for t in want if t not in known]
            if unknown:
                print(f"没有这些表：{unknown}", file=sys.stderr)
                return 2
            z = dbzip.DbZip(tables={s.table: s.data for s in src.meta.tables if s.table in want})
            out = Path(args.out or (src.path.parent / session.PACKAGE_NAME))
            z.write(out)
            print(f"已导出 {len(z.tables)} 张表 -> {out}（{out.stat().st_size:,} 字节）")
            print("⚠️ 缺表的 BA_Mod.zip 是残废的：游戏对缺失的表是【不加载】而不是保留旧值")
            return 0
        out = Path(args.out or (src.path.parent / session.PACKAGE_NAME))
        result = session.extract_db_zip(src, out)
        print(f"已导出 24 张表 -> {result}（{result.stat().st_size:,} 字节）")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from ba_mod_maker.core import session

    if args.dry_run:
        with (
            session.open_source(args.container) as target,
            session.open_source(args.incoming) as inc,
        ):
            cur = {s.table: s.data for s in target.meta.tables}
            new = {s.table: s.data for s in inc.meta.tables}
            diff = [t for t in new if cur.get(t) != new[t]]
            print(f"目标：{target.path}")
            print(f"来源：{inc.path}")
            print(f"会变的表：{diff or '（无）'}")
            if not diff:
                print("没有差异，写盘会是空操作")
        return 0
    result = session.import_into_container(
        args.container,
        args.incoming,
        check_game_running=not args.allow_running,
        check_sha256=not args.no_sha256,
        verify_after=not args.no_verify,
    )
    print(result)
    return 0


def _project_root(text: str | None) -> Path:
    """工程目录口径：给了就用，没给就当前目录（补丁柜落 <工程目录>\\patches）。"""
    return Path(text) if text else Path.cwd()


def _game_root(text: str | None, *, need: bool = True) -> Path | None:
    from ba_mod_maker.core.assets import game as game_mod

    root = Path(text) if text else game_mod.find_game_root()
    if root is None and need:
        print("找不到游戏目录，请用 --game 指定", file=sys.stderr)
    return root


def _cmd_assets(args: argparse.Namespace) -> int:
    """补丁柜（ADR 0007 的反向 Δ）：看 / 还原 / 丢弃 —— 还原的唯一入口。

    `graph` 那一档**不是补丁柜**：它是**资产目录**（只读盘点：这台车用了哪些
    模型包 / prefab / 渲染器 / 材质 / 贴图 / VT 池），一个字节都不写。
    实现与帮助全在 `cli/asset_graph.py`（照 ADR 0008 的「模块化注册」形状 ——
    本文件已 1,000+ 行，只加这四句转调）。
    """
    if args.action == "graph":
        return asset_graph.run(args)
    from ba_mod_maker.asset_kernel import reverse_delta

    # 只有 restore 会写游戏目录 ⇒ 只有它需要「补丁柜跟随游戏盘」那道红线；
    # status / discard 是只读或只删自己的补丁，不该因为盘不同就打不开。
    store = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(_project_root(args.project_root)),
        game_root=_game_root(args.game, need=False) if args.action == "restore" else None,
    )
    if args.action == "status":
        for line in store.status_lines():
            print(line)
        return 0
    if args.action == "discard":
        for line in store.discard():
            print(line)
        return 0
    report = store.restore(check_sha=not args.force)
    for line in report.lines():
        print(line)
    return 0 if report.ok else 1


def _cmd_skin(args: argparse.Namespace) -> int:
    """材质槽通道：槽清单看板 + 干跑判据一行 + 真装（走反向 Δ）。"""
    from ba_mod_maker.asset_kernel import skin_install

    game_root = _game_root(args.game)
    if game_root is None:
        return 2
    if not args.unit:
        print("要 --unit（例 RU_BMD-2）", file=sys.stderr)
        return 2
    if args.action == "slots":
        if not args.material:
            names = skin_install.list_materials(game_root, args.unit)
            print(f"[材质] {args.unit}：{len(names)} 个")
            for name in names:
                print(f"  {name}")
            return 0
        table = skin_install.list_slots(game_root, args.unit, material_name=args.material)
        print(f"[贴图槽] {args.unit} · {args.material}：{len(table)} 个")
        for slot, texture in sorted(table.items()):
            print(f"  {slot} -> {texture}")
        return 0
    if not (args.material and args.slot and args.texture):
        print("plan / install 要 --material --slot --texture", file=sys.stderr)
        return 2
    plan = skin_install.plan_swap(
        game_root,
        args.unit,
        material_name=args.material,
        slot=args.slot,
        texture_name=args.texture,
    )
    if args.action == "plan":
        for line in plan.criterion_lines():
            print(line)
        print("  （干跑：一个字节都没写；上面的数字是算出来的真值）")
        return 0
    report = skin_install.install_swap(plan, project_root=_project_root(args.project_root))
    for line in report.report_lines():
        print(line)
    return 0


def _cmd_db(args: argparse.Namespace) -> int:
    """外部库通道（图标键那条）：BA_Mod.zip 装 / 卸 / 撤，可逆性同样走反向 Δ。

    `remove` 与 `restore` **不是一回事**（⛔ 别混）：`restore` 按补丁还原「**装之前**那一份」，
    而 `remove` 要的是「**这个文件不存在**」那个状态。实现与理由在
    `core/asset/external_db.py` 的 `remove()`（搬进 `<工程目录>/removed/` ＋ 记 sha256 台账）。
    """
    from ba_mod_maker.core import session
    from ba_mod_maker.core.asset import external_db

    project_root = _project_root(args.project_root)
    # restore / unremove 都**不需要**游戏根：补丁与台账里记的是绝对路径
    # （`restore` 的 game_root 只用来做同盘守卫；`unremove` 的同盘守卫看的是台账里那个落点）。
    game_root = _game_root(args.game, need=args.action not in ("restore", "unremove"))
    if args.action == "restore":
        report = external_db.restore(project_root, game_root=game_root)
        for line in report.lines():
            print(line)
        return 0 if report.ok else 1
    if args.action == "unremove":
        report = external_db.unremove(
            project_root,
            files=tuple(getattr(args, "remove_files", ()) or ()),
            dry_run=args.dry_run,
        )
        for line in report.lines():
            print(line)
        return 0 if report.ok else 1
    if game_root is None:
        return 2
    from ba_mod_maker.core.assets import game as game_mod

    if not game_mod.is_game_root_for_db(game_root):
        print(
            f"这里不像游戏根目录（要含 BrokenArrow_Data\\data.unity3d）：{game_root}",
            file=sys.stderr,
        )
        return 2
    if args.action == "status":
        for line in external_db.status(game_root).lines():
            print(line)
        return 0
    if args.action == "remove":
        report = external_db.remove(
            game_root,
            project_root=project_root,
            extra_files=tuple(getattr(args, "remove_files", ()) or ()),
            dry_run=args.dry_run,
        )
        for line in report.lines():
            print(line)
        return 0
    source = Path(args.source) if args.source else project_root / session.PACKAGE_NAME
    report = external_db.install(game_root, source, project_root=project_root, dry_run=args.dry_run)
    for line in report.lines():
        print(line)
    return 0


def _cmd_newasset(args: argparse.Namespace) -> int:
    """资产新增：一条命令 PNG -> 游戏里出现；`newasset restore` 一条命令全撤。"""
    from ba_mod_maker.core.asset import new_asset
    from ba_mod_maker.core.errors import UserFacingError

    action = getattr(args, "action", None)
    if action not in ("add", "add-model", "add-bundle", "repoint", "restore"):
        print(
            "要 newasset add / add-model / add-bundle / repoint / restore（--help 看用法）",
            file=sys.stderr,
        )
        return 2
    if action == "restore" and getattr(args, "force_dll", False):
        print("--force-dll 只在 newasset add 上有意义（restore 是把 DLL 还原）", file=sys.stderr)
        return 2
    game_root = _game_root(args.game)
    if game_root is None:
        return 2
    project_root = _project_root(args.project_root)
    if action == "restore":
        outcome = new_asset.restore(
            game_root=game_root, project_root=project_root, keep_dll=args.keep_dll
        )
        for line in outcome.lines():
            print(line)
        return 0 if outcome.ok else 1
    if action == "add-bundle":
        # 这一档**只有落包 + 登记地址**（⛔ 不写 BA_Mod.zip / ⛔ 不碰 Units 行 / ⛔ 不打 DLL）。
        # 退出码：算不出来（键不在 catalog / 包读不开 / 参数不对）= 2；算出来了写盘出事 = 1。
        from ba_mod_maker.core.errors import UserFacingError

        try:
            plan = new_asset.plan_add_bundle(
                bundle=args.bundle,
                key=args.key,
                asset_path=args.asset_path,
                dependency_source=args.dependency_source,
                game_root=game_root,
                project_root=project_root,
            )
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        if args.dry_run:
            for line in new_asset.run_add_bundle_dry(plan).lines():
                print(line)
            _print_bundle_registration_surface(plan)
            return 0
        try:
            bundle_report = new_asset.run_add_bundle(plan)
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        for line in bundle_report.lines():
            print(line)
        _print_bundle_registration_surface(plan)
        return 0 if bundle_report.verified else 1
    if action == "repoint":
        try:
            plan = new_asset.plan_repoint(
                unit=args.unit,
                key=args.key,
                row_id=args.row_id,
                game_root=game_root,
                project_root=project_root,
            )
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        if args.dry_run:
            for line in new_asset.run_repoint_dry(plan).lines():
                print(line)
            return 0
        try:
            repoint_report = new_asset.run_repoint(plan)
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        for line in repoint_report.lines():
            print(line)
        return 0 if repoint_report.verified else 1
    if action == "add-model":
        facts = None
        if getattr(args, "facts", None):
            facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        turret_row = getattr(args, "turret_row", None)
        plan: object = new_asset.plan_add_model(
            bundle=args.bundle,
            unit=args.unit,
            key=args.key,
            asset_path=args.asset_path,
            row_id=args.row_id,
            dependency_source=args.dependency_source,
            dependency_policy=getattr(args, "dependency_mode", "auto") or "auto",
            facts=facts,
            table="Turrets" if turret_row is not None else "Units",
            row_index=turret_row,
            game_root=game_root,
            project_root=project_root,
        )
    else:
        plan = new_asset.plan_add(
            png=args.png,
            unit=args.unit,
            key=args.key,
            fit=args.fit,
            row_id=args.row_id,
            game_root=game_root,
            project_root=project_root,
        )
    if args.dry_run:
        for line in new_asset.run_add_dry(plan).lines():
            print(line)
        _print_registration_surface(plan)
        return 0
    report = new_asset.run_add(plan, force_dll=args.force_dll)
    for line in report.lines():
        print(line)
    _print_registration_surface(plan)
    return 0 if report.verified else 1


def _print_registration_surface(plan: object) -> None:
    """用户需求⑩ H4：计划末尾附一张「**本次登记面清单**」。

    键 / 实际路径 / resource_type / 依赖声明 / m_Crc / m_BundleSize / m_Hash ——
    印法与 catalog key **同一个渲染器**（cli/address_inventory.py），所以两处读数永远一致。

    清单从 plan.catalog_after（本次改动之后的那份 catalog 字节）里按 registration 的条目下标取，
    **不是**把计划里的字段转手抄一遍 —— 读的是真落盘那份的形状。
    """
    registration = getattr(plan, "registration", None)
    catalog_after = getattr(plan, "catalog_after", None)
    if registration is None or catalog_after is None:  # pragma: no cover - 计划形状变了才走
        return
    rows = [int(registration.bundle_entry_index), int(registration.asset_entry_index)]
    alias = int(getattr(registration, "alias_entry_index", -1) or -1)
    if alias >= 0:
        rows.append(alias)
    try:
        lines = address_inventory.registration_lines(catalog_after, rows)
    except Exception as exc:  # noqa: BLE001
        # 这是**附赠读数**：印不出来也不许把主链路（已经算好的计划）弄崩。
        print(f"（本次登记面清单印不出来：{type(exc).__name__}: {exc}）", file=sys.stderr)
        return
    for line in lines:
        print(line)


def _print_bundle_registration_surface(plan: object) -> None:
    """add-bundle 的「登记面」附赠读数（二进制 catalog 上那份渲染器还读不动）。

    `cli/address_inventory.py` 的渲染器只认 1.2.0 的 JSON 明文 catalog —— 1.2.1 的二进制
    那份会被 `asset_kernel.catalog_edit.CatalogEdit.from_bytes` 明确拒掉（`AssetError`
    并点名 `catalog_bin_write`；2026-10-18 之前抛的是裸 `UnicodeDecodeError`）。
    `_print_registration_surface` 会吞掉但印一句「印不出来」，在 PowerShell 下还会被渲染成
    NativeCommandError，看着像失败。
    所以这里先看形状：二进制就不印那一块，改印一行**可执行的替代读数** —— ⛔ 不静默。
    """
    from ba_mod_maker.core.assets.catalog_bin import looks_like_catalog_bin

    raw = getattr(plan, "catalog_after", None)
    if isinstance(raw, bytes) and looks_like_catalog_bin(raw):
        key = getattr(plan, "key", "")
        print(f"（二进制 catalog：登记面清单改跑 catalog key {key} —— 那个渲染器只认 JSON 那份）")
        return
    _print_registration_surface(plan)


def _cmd_clone_unit(args: argparse.Namespace) -> int:
    """新增一个单位：把 `--id` 那一行**整套**克隆成新单位（新 Id = 库里最大 + 1）。

    与研究脚本 `_work/route-b/routeb_newunit.py` 是**同一条链**（那份在 _work 里，
    产品不许 import 它，只当口径参照）：`open_source` -> `dataset_from_source`
    -> `Changeset` -> `clone_unit` -> `save_instance`。所以「插了哪几张表、重指了
    哪些外键」的判据仍然只有 `core/edit/clone.py::clone_unit` 一份 —— 这里只负责
    源怎么开、字段怎么点、落点在哪、报告怎么印（AGENTS §5「逻辑只有一份」）。

    落点口径与界面一致（用户口径：「保存写回**你打开的那个文件**」）：`--out` 省略 =
    写回打开的那个文件；给了 = 另存一份 BA_Mod.zip，**原文件一个字节都不动**。
    `--dry-run` 连落点都不碰：算完全程（含字段改动）就返回，磁盘零写入。

    新行的两列是**写死的默认**（⛔ 不是继承源行）：`DisplayInArmory=true`、
    `IsUnitModification=false` —— 本命令的语义是「**新增一个能上场的单位**」，
    不是"复制一行内部数据"：继承的话，克隆一个改装行会得到一个机库里看不见的单位
    （显示侧两道门见 `docs/交接-2026-10-06-完整单位模型-验收合同.md` §2·L3）。
    要反着来只有两条**显式**逃生门：`--hidden` / `--as-modification`。

    退出码：0 完成 / 1 写盘失败 / 2 用不了（打不开源 / `--id` 不存在 / 没有落点）。
    """
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.edit.clone import clone_unit
    from ba_mod_maker.core.errors import UserFacingError

    try:
        opened = session.open_source(args.source)
    except UserFacingError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    with opened:
        try:
            dataset = dataset_from_source(opened)
            instance = dataset.instance(dataset.default_instance_id)
            changeset = Changeset(instance)
            # ❗ 顺手当「--id 在不在」的检查：row_of 找不到会抛带 hint 的 TableError。
            source_name = changeset.value("Units", args.row_id, "Name")
            source_model = changeset.value("Units", args.row_id, "ModelFileName")
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

        units_rows = instance.table("Units").row_count
        print(f"源        : {opened.path}")
        print(f"            {opened.kind.label}，Units {units_rows:,} 行")
        print(
            f"克隆自    : Units#{args.row_id} · {_fmt(source_name)}"
            f" · ModelFileName={_fmt(source_model)}"
        )

        # data.unity3d 没有"打开的那个文件"这一说（写回容器 = 7.1 GB / 约 4 分钟那条路）。
        # 干跑不需要落点，所以只有真写的时候才拦。
        if not args.dry_run and args.out is None and instance.opened_file is None:
            print(
                f"错误：这个来源没有「打开的那个文件」可以写回（{opened.kind.label}）"
                "—— 写回容器是 7.1 GB 的那条路；请用 --out 另存一份 BA_Mod.zip",
                file=sys.stderr,
            )
            return 2

        # 克隆 + 点字段放在同一个 try 里：两者抛的都是 UserFacingError（列名或 Id 对不上
        # = 「用不了」，退出码 2；⛔ 不许混进"写盘失败"那一档）。
        try:
            result = clone_unit(changeset, args.row_id)
            # 用户点的名只覆盖点上名的列（HUDName 跟着 Name 走：卡片抬头与列表用的是这两个）。
            if args.name is not None:
                changeset.set_value("Units", result.unit_id, "Name", args.name)
                changeset.set_value("Units", result.unit_id, "HUDName", args.name)
            if args.model_key is not None:
                changeset.set_value("Units", result.unit_id, "ModelFileName", args.model_key)
            changeset.set_value("Units", result.unit_id, "DisplayInArmory", not args.hidden)
            changeset.set_value(
                "Units", result.unit_id, "IsUnitModification", bool(args.as_modification)
            )
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

        columns = ("Name", "HUDName", "ModelFileName", "DisplayInArmory", "IsUnitModification")
        new_row = "  ".join(
            f"{column}={_fmt(changeset.value('Units', result.unit_id, column))}"
            for column in columns
        )
        print(f"新 Id     : {result.unit_id}")
        print(f"新行      : {new_row}")
        print(f"每张表插了几行：{len(result.rows)} 行 / {len(result.counts)} 张表")
        for table, count in result.counts:
            print(f"  {table:26s} {count:6d}")

        opened_path = str(instance.opened_file.path) if instance.opened_file else "（没有落点）"
        target = args.out or opened_path
        if args.dry_run:
            print(f"落点      : {target}（--dry-run：一个字节都没写）")
            return 0

        try:
            report = session.save_instance(instance, changeset.changed_tables(), target=args.out)
        except UserFacingError as exc:
            print(f"写盘失败：{exc}", file=sys.stderr)
            return 1
        print(f"落点      : {report.path}（{report.total_bytes:,} B）")
    return 0


def _node_contract_lines(
    bundle: Path,
    asset_path: str | None,
    *,
    unit_prefab: bool,
    turret_prefab: bool = False,
) -> tuple[list[str], list[str]]:
    """**L7 命名契约闸**：读包里那条 prefab -> 逐条判节点名（只读）。

    读不出来（这个包不是 prefab 包 / 容器里没地址）时**只记读数** —— 除非给了
    `--unit-prefab`（那说明调用方认定它就是单位件，读不出 prefab 本身就是红）。
    """
    # 2026-10-31：`build_pipeline` 搬去资产管理器了；这条判据原本就只是 node_contract 的薄封装，
    # 这里自己写完（⛔ 不再 import 那个模块，数据侧不依赖资产管理器）。
    from ba_mod_maker.asset_kernel import node_contract
    from ba_mod_maker.asset_kernel import prefab as prefab_mod

    if not asset_path:
        return (["读不到 prefab：容器里没有地址（也没给 asset_path）⇒ 本档跳过"], [])
    stem = Path(asset_path).name.rsplit(".", 1)[0]
    try:
        tree = prefab_mod.read_prefab_tree(bundle, asset_path=asset_path)
    except Exception as exc:  # noqa: BLE001 -- 读不出来是读数，不是崩溃
        note = f"读不出 prefab {asset_path}（{type(exc).__name__}: {exc}）⇒ 本档跳过"
        return ([note], [note] if unit_prefab else [])
    report = node_contract.check_tree(
        tree, name=stem, expected_root_name=stem,
        scope="turret" if turret_prefab else "unit")
    return (list(report.lines()), list(report.problems()))


def _cmd_check_model(args: argparse.Namespace) -> int:
    """装机前检查一个模型包（只读）。

    四把尺子叠起来：
      ① unit_bundle.audit_package_pptrs —— 整包 PPtr 普查（含 payload 对象）；
      ② unit_bundle.verify_unit_bundle —— 出厂闸 A0~A6（是否按单位 prefab 判由 --unit-prefab 定）；
      ③ node_contract —— **节点命名契约**（L7：真件 285 件量过的那张表，逐级点名）；
      ④ new_asset.bundle_facts_of_file —— catalog 要的四件套；
      给 --key 时再跑 verify_registered_address，核一遍**已经装进去**的那条解析链。
    """
    if getattr(args, "spec", False):
        return _cmd_spec(args)

    from ba_mod_maker.asset_kernel import unit_bundle
    from ba_mod_maker.core.asset import new_asset

    bundle = Path(args.bundle) if args.bundle else None
    if bundle is None or not bundle.is_file():
        print(f"错误：找不到包 {args.bundle}", file=sys.stderr)
        return 2
    problems: list[str] = []

    print(f"[模型包检查] {bundle}")
    # ⓪ 类型表版本：包的元数据区 vs **产品内置**那份（内置 = 当前游戏版本抽的）
    from ba_mod_maker.core.assets import unit_pack as _unit_pack

    try:
        drift = _unit_pack.schema_drift(bundle)
    except Exception as exc:  # noqa: BLE001
        print(f"⓪ 类型表版本：读不出（{type(exc).__name__}: {exc}）")
    else:
        if drift["ok"]:
            print(f"⓪ 类型表版本：{drift['types']} 个类型项，**逐字节与内置一致** ✔")
        else:
            listed = ", ".join(
                f"({d['class_id']},{d['script_index']})" for d in drift["drift"][:8]
            )
            print(f"⓪ 类型表版本：{drift['types']} 个类型项，**{len(drift['drift'])} 项与内置不同**"
                  f"（(classID,scriptIndex)：{listed}）")
            print("   ⚠ 两种可能：① 别的游戏版本造的（换版漂移）；② 本产品**写包时重编码**了类型树"
                  "（实测：合并 115 / 收窄类型表都会走 mw.build_metadata，"
                  "8/22 个 114 项逐字节不同）。判据与未结案见 kb/06 **D1**")
            problems.append(
                "类型表漂移：这个包的类型树与内置（= 当前游戏版本）逐字节不同 ⇒ "
                "要么是别的游戏版本造的，要么是本产品写包时重编码过"
            )
    try:
        audit = unit_bundle.audit_package_pptrs(bundle)
    except Exception as exc:  # noqa: BLE001
        print(f"错误：包读不开（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return 2
    print("① 整包 PPtr 普查（含 payload 对象）")
    for line in audit.lines():
        print("   " + line)
    problems += [f"悬空 PPtr：{x}" for x in audit.dangling]

    gate = unit_bundle.verify_unit_bundle(
        bundle,
        asset_path=args.asset_path,
        require_animations=bool(args.unit_prefab),
        turret_prefab=bool(getattr(args, "turret_prefab", False)),
    )
    print("② 出厂闸（A0~A6）："
          + ("按单位 prefab 判" if args.unit_prefab else "A1/A3 只记读数"))
    for line in gate.lines():
        print("   " + line)
    for item in gate.problems:
        if (
            args.unit_prefab
            or getattr(args, "turret_prefab", False)
            # ★ 2026-10-25（炮塔那次）：A10 是**运行期必炸**的一条（[SerializeReference] 里
            #   有 rid=-2 ⇒ null ⇒ AnimationHub.Init NRE ⇒ 整台单位加载失败），
            #   它跟 --unit-prefab 无关 —— 炮塔包同样会炸，所以无条件进判据。
            or item.startswith(("MonoBehaviour", "容器", "材质槽少于子网格", "AnimationHub"))
        ):
            problems.append(item)

    scope_words = "炮塔件" if getattr(args, "turret_prefab", False) else "单位件"
    print("③ 命名契约闸（L7）：节点名逐条判（表由原版 285 件量出来，能红）· " + scope_words)
    contract_lines, contract_problems = _node_contract_lines(
        bundle,
        args.asset_path or (gate.container[0][0] if gate.container else None),
        unit_prefab=bool(args.unit_prefab),
        turret_prefab=bool(getattr(args, "turret_prefab", False)),
    )
    for line in contract_lines:
        print("   " + line)
    problems += contract_problems

    facts, _data, serialized = new_asset.bundle_facts_of_file(bundle)
    print("④ catalog 四件套（登进 catalog 的就是这四个值）")
    print(f"   包名 {facts['bundle_name']}")
    print(f"   m_Hash={facts['m_hash']}  m_Crc={facts['crc']}"
          f"  m_BundleSize={facts['bundle_size']:,}  解压流 {serialized:,} B")
    print(f"   internal_name（包内真值）= {facts.get('internal_name')!r}")

    # ★ 2026-10-27：材质挂 **1 Pass 桩** ⇒ 真机上那些件「在、但平」
    #   （M1A2 SEP v3 的 M2/M240L 就是这个症状，用户真机判读定案）。
    #   造包前 `core.assets.shader_bind` 会自动把材质指回工程真件（8 Pass）；
    #   这条尺子查**包本身**，能挡住「绕过工具直接换包」。
    shape = unit_bundle.audit_shader_shapes(bundle)
    print("⑤ 材质 shader 形状闸：挂 1 Pass 桩 ⇒ 红（真机「在、但平」）")
    for line in shape.lines():
        print("   " + line)
    problems += list(shape.problems)

    if args.key:
        from ba_mod_maker.core.assets.game import find_game_root

        game = args.game or find_game_root()
        if not game:
            print("错误：找不到游戏目录（--game 显式给一个）", file=sys.stderr)
            return 2
        # ⚠ 没给 --asset-path 时要用**包内容器那条**（= 这个包对外的资产路径），
        #   不是 facts["internal_name"]（那是 AssetBundle 对象的名字 = 包名本身）——
        #   第一版拿包名当资产路径，解析链当场报两条假红。
        asset_path = args.asset_path or (gate.container[0][0] if gate.container else None)
        catalog_json = (
            Path(game) / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "catalog.json"
        )
        if not catalog_json.is_file():
            # 1.2.1 起目录是二进制 catalog.bin；本链只吃 catalog.json。
            # ⛔ 别在这里抛 FileNotFound —— 用户要的是「跳过 + 指一条能核的路」。
            print(f"⑥ 解析链（键 {args.key!r}）：跳过 —— 1.2.1 是二进制 catalog.bin")
            print("   （用 `ba-mod-maker catalog key <键>` 核：它走 catalog_bin_write）")
        else:
            chain = new_asset.verify_registered_address(
                game_root=game, key=args.key, asset_path=asset_path)
            print(f"⑥ 解析链（键 {args.key!r}）")
            for line in chain.lines():
                print("   " + line)
            problems += list(chain.problems)

    print()
    if problems:
        print(f"判据：**有红 {len(problems)} 条** —— 别装机")
        for item in problems:
            print(f"   ★ {item}")
        return 1
    print("判据：全绿 ✔ 可以装机")
    return 0


def _cmd_spec(args: argparse.Namespace) -> int:
    """`check-model --spec`：按原版规格体检（**只读**，⛔ 不是闸门）。

    两档来源（**正好给一个**）：
      * `--unit <键>` —— 读游戏里那一件**原版**资产（键 = Units.ModelFileName 原样值）；
      * `--bundle <包>` —— 读**我们自己造的**那个包（容器里那条 prefab）。

    判据（正控，必须复现）：`--unit DLC2_XA180EST` -> 纹素密度 **155.9** px/m（2048² 口径）·
    UV 岛面积之和 **143.3 %** · texel 覆盖率 **68.02 %** · LOD0 tris **25,751** ·
    贴图 **2048²**。

    退出码：0 读出来了 / 2 用不了。⚠ 没有「有红」那一档 —— 它**只报读数**：
    那些样本区间是**外包商的交付习惯**（`docs/烘焙规范.md` §8），不是游戏的硬约束。
    """
    from ba_mod_maker.asset_kernel import spec_readings

    raw = str(getattr(args, "raster", "") or "").replace("，", ",")
    rasters = tuple(int(x) for x in raw.split(",") if x.strip())
    if not rasters:
        rasters = spec_readings.DEFAULT_RASTERS

    unit = str(getattr(args, "unit", "") or "").strip()
    bundle = str(getattr(args, "bundle", "") or "").strip()
    if bool(unit) == bool(bundle):
        print(
            "错误：--spec 要**正好给一个**来源：--unit <键>（游戏里那一件）"
            " 或 --bundle <包>",
            file=sys.stderr,
        )
        return 2

    try:
        if unit:
            from ba_mod_maker.core.assets.game import find_game_root

            game = args.game or find_game_root()
            if not game:
                print("错误：找不到游戏目录（--game 显式给一个）", file=sys.stderr)
                return 2
            readings = spec_readings.readings_of_unit(game, unit, rasters=rasters)
        else:
            readings = spec_readings.readings_of_bundle(
                bundle, asset_path=args.asset_path, rasters=rasters
            )
    except spec_readings.SpecReadError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if getattr(args, "json_out", False):
        print(json.dumps(readings.document(), ensure_ascii=False, indent=1))
        return 0
    for line in readings.lines():
        print(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口。"""
    # ⛔ 必须在 argparse 之前：`--help` 的输出不经 Emitter（argparse 直接 print），
    #    帮助文本里带 ⛔/✅ 的子命令在 GBK 管道下会当场 UnicodeEncodeError
    #    （实测 `skin --help` / `assets --help`：用户只是敲个帮助就看到 traceback）。
    emit.force_utf8_streams()
    args = _build_parser().parse_args(argv)

    if args.version:
        from ba_mod_maker import __version__

        print(f"ba-mod-maker {__version__}")
        return 0

    handlers = {
        "info": _cmd_info,
        "tables": _cmd_tables,
        "rows": _cmd_rows,
        "set": _cmd_set,
        "extract": _cmd_extract,
        "import": _cmd_import,
        "patch": _cmd_patch,
        "assets": _cmd_assets,
        "skin": _cmd_skin,
        "db": _cmd_db,
        "newasset": _cmd_newasset,
        "clone-unit": _cmd_clone_unit,
        "check-model": _cmd_check_model,
        "validate": validator.run,
        "find": finder.run,
        "vt": vt.run,
        "vt-bake": vt_bake.run,
        "render": render.run,
        "object": object_node.dispatch,
        "address": address_edit.run,
        "catalog": catalog_crc.dispatch,
        "row-delete": row_delete.run,
        "row-restore": row_restore.run,
        "texture": texture.run,
        "material": material_visible.dispatch,
        "names": names.run,
        "anim": anim.run,
        "diag": diag.run,
    }
    handler = handlers.get(args.command or "")
    if handler is not None:
        try:
            return handler(args)
        except Exception as exc:
            from ba_mod_maker.core.errors import UserFacingError

            if isinstance(exc, UserFacingError):
                print(f"错误：{exc}", file=sys.stderr)
                return 1
            raise

    # GUI 是默认行为。
    from ba_mod_maker.app.main import main as gui_main

    return gui_main([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
