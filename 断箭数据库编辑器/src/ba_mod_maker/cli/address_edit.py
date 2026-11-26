"""`address` 子命令：**新增 / 删除** catalog 里的一条**资产地址**（零 Qt）。

## 这一步是什么（设计稿 `设计-资产编辑入口.md` §5 的地址线两条）

产品的资产侧只有**三个原子动作**：修改 / 新增 / 删除（ADR 0008 决定 1）。
前三个动词（`object set` / `add` / `delete`）动的是**包里的对象**；本组动的是
**catalog 的地址条目**（键 -> `Assets/...`）—— 落点完全不同：

    ba-mod-maker address add    --catalog <c> --key <地址键> --path <资产路径> --bundle <包名>
    ba-mod-maker address delete --catalog <c> --key <地址键>

⚠ **命名陷阱（照设计稿 §5 避开）**：CLI 已有 `assets`（**补丁柜**）与 `newasset`（造一张自己的图）——
本组叫 **`address`**，指的是 **Addressables 的地址条目**（键 -> `Assets/...` 那条），⛔ 不是补丁柜。

## 写路径只有一条（设计稿 §8 第 3 条）

新增地址**只能**走 `CatalogEdit.to_bytes()`（**全量重写**，写前 `self_check()`）；
`writer._build_catalog_patch` **只改既有包条目的 crc/size，不新增地址** —— 两条路不许混。

## 三种跑法

| 跑法 | 盘上会发生什么 |
|---|---|
| 默认 | **干跑**：算出读数、印判据，**一个字节都不写** |
| `--dry-run` | 与默认同义（留给"我就是要干跑"的脚本） |
| `--write` | **真写**：反向 Δ 进补丁柜（`capture_file`）-> 写后复核（读回逐字节比对）-> 印 sha256 |

`--dry-run` 与 `--write` 同时给 = 退出码 2（⛔ 不猜用户要哪个）。

## 依赖键与依赖 hash（⛔ 不瞎编）

`CatalogEdit.add_address` 要一个 `dependency_key`（依赖键下标）与一个 `dependency_hash`：

* 依赖键 = **`--bundle` 那个包在 catalog 里的键下标**（产品口径：桶里只有包自己那条）；

* 依赖 hash 默认取依赖键自己的 hash；要**抄真件模型地址**的 `entry[3]` 时给
  `--dependency-hash-from <键>`
  （走 `new_asset.dependency_hash_from`，三条 fail-closed 与模型线逐字相同）；
* ⚠ **游戏认不认这个形状 —— 没验**（`add_address` 的 docstring 原文）：真机第一批要验的形状之一。
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.asset.new_asset import (
    _write_verified,
)
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError, BaModMakerError

__all__ = [
    "ACTIONS",
    "DEFAULT_RESOURCE_TYPE",
    "EXIT_DONE",
    "EXIT_NOTHING",
    "EXIT_UNUSABLE",
    "AddressPlan",
    "add_parser",
    "plan_add",
    "plan_delete",
    "run",
    "write_catalog",
]

#: 退出码（与 finder / validator / object 同一套语义：0 完成 / 1 没改动 / 2 用不了）。
EXIT_DONE = 0
EXIT_NOTHING = 1
EXIT_UNUSABLE = 2

#: 这一组落两个原子动词（顺序 = `--help` 里的顺序）。
ACTIONS: Final[tuple[str, str]] = ("add", "delete")

#: 地址的 `resource_type` 默认值。真件读数：prefab 地址 **6021/6021 都是 (1,)**；
#: 图标（png）那条才是 (3,4) 两条 entry —— 要那种形状请显式 `--resource-type 3`。
DEFAULT_RESOURCE_TYPE = 1

#: 真件见过的 resource_type 取值（`docs/设计-资产编辑入口.md` §5 与 newasset 的三条线）。
KNOWN_RESOURCE_TYPES: Final[tuple[int, ...]] = (0, 1, 3, 4)

_DESCRIPTION = """新增 / 删除 catalog 里的一条资产地址（键 -> Assets/... 那条）。

⛔ 本组动的是 **catalog 的地址条目**，不是补丁柜 `assets`。

两种写法：
  address add    --catalog <catalog.json|.bin> --key <地址键> --path <资产路径> --bundle <包名>
  address delete --catalog <catalog.json|.bin> --key <地址键>

catalog.bin（Addressables 2.x / 1.2.1 起）走全量重编码这条路：判据是"原样重编码 == 真件，逐字节"。

默认**干跑**（一个字节都不写）；--write 才真写（反向 Δ 进补丁柜 + 写后复核）。
新增地址走 CatalogEdit.to_bytes() 全量重写（设计稿 §8 第 3 条：只有这一条路）。

退出码：
  0  算出来了 / --write 已写回
  1  没有改动可写
  2  用不了（catalog 打不开 / 键已存在 / 键不存在 / 包不在 catalog 里 / 自检不过）
"""

_EPILOG = """--resource-type  地址的 resource_type（默认 1 = prefab 地址的真件形状；图标线是 3）。
--dependency-hash-from <键>  照抄那条真件地址的**整份依赖声明**（entry[2] 依赖键 + entry[3] hash）。
                              ⛔ 不许瞎编；不给、而这条资产路径已有地址条目 ⇒ 自动照抄它那条。
--project-root   补丁柜的落点（<它>/patches/，默认当前目录）。
--game           游戏根（补丁柜跟随游戏盘那道红线用；不给就自动找）。
--allow-running-game  游戏在跑也写（默认拦下 —— 与 writer 同一条纪律）。

例：
  ba-mod-maker address add --catalog catalog.json --key MyIcon ^
      --path Assets/Mods/My/icon.png --bundle myicon_assets_all_<hash>.bundle ^
      --resource-type 3 --write --project-root D:/proj
  ba-mod-maker address delete --catalog catalog.json --key MyIcon --write
"""


# --------------------------------------------------------------------------- #
# 计划：读 catalog -> 改一条 -> 出**新字节**（纯函数，不碰盘）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AddressPlan:
    """一次地址改动的读数（`before` / `after` 是**整份 catalog 的字节**）。"""

    action: str
    key: str
    before: bytes
    after: bytes
    counts_before: tuple[int, int, int]
    counts_after: tuple[int, int, int]
    lines: tuple[str, ...] = ()
    entry_index: int = -1
    internal_id_index: int = -1
    bundle_key_index: int = -1
    asset_path: str = ""
    bundle_name: str = ""
    resource_type: int = DEFAULT_RESOURCE_TYPE
    dependency_hash: int | None = None

    @property
    def byte_delta(self) -> int:
        return len(self.after) - len(self.before)

    @property
    def sha_before(self) -> str:
        return hashlib.sha256(self.before).hexdigest()

    @property
    def sha_after(self) -> str:
        return hashlib.sha256(self.after).hexdigest()

    @property
    def is_empty(self) -> bool:
        """一个字节都没变 => 算不上一次改动（调用方据此给退出码 1）。"""
        return self.before == self.after


def _self_checked(edit: ce.CatalogEdit) -> bytes:
    """自检 + 出字节（⛔ 自检不过不许出字节 —— 这正是设计稿 §8 第 3 条那条路）。"""
    problems = edit.self_check()
    if problems:
        raise AssetError(
            "catalog 自检不过，本命令不许写：" + "；".join(str(p) for p in problems[:4]),
            hint="self_check 是 CatalogEdit 自带的尺子（to_bytes 之前必过）—— 先修好再重跑",
        )
    edit.assert_consistent()
    return bytes(edit.to_bytes())


def _dependency_of_reference(edit: ce.CatalogEdit, key: str) -> tuple[int, int]:
    """参照条的**依赖声明** `(entry[2] 依赖键, entry[3] 依赖 hash)` —— 三条 fail-closed。

    为什么不能只抄 hash（2026-10-11 真机读数）：依赖键填「包自己那条键」时，那个桶里
    只有它自己 ⇒ 跨包依赖一个都没声明 ⇒ 游戏 `Addressable prefab '<新键>' was not loaded!`。
    """
    key_index = edit.key_index_of(str(key))
    if key_index is None:
        raise AssetError(
            f"参照键不在 catalog 里：{key!r}",
            hint="要一个**已入库的真件地址键**当参照（例 DLC2_CV9035）",
        )
    members = edit.buckets[key_index]
    if not members:
        raise AssetError(
            f"参照键 {key!r} 的桶是空的 —— 抄不到依赖声明",
            hint="换一条真件地址当参照",
        )
    row = edit.entries[int(members[0])]
    if row[1] != cw.BUNDLED_ASSET_PROVIDER:
        raise AssetError(
            f"参照键 {key!r} 指的不是一条资产地址（provider={row[1]}）",
            hint=f"要抄的是 provider={cw.BUNDLED_ASSET_PROVIDER} 的地址条目",
        )
    dep_key = int(row[2])
    if not 0 <= dep_key < len(edit.keys):
        raise AssetError(
            f"参照条的依赖键 #{dep_key} 越界（catalog 只有 {len(edit.keys)} 条键）",
            hint="参照条本身不干净 —— 换一条真件地址",
        )
    return dep_key, int(row[3])


def _address_plan_from_bin(plan: cbw.BinAddressPlan) -> AddressPlan:
    """二进制那条路的读数 -> 本模块的 `AddressPlan`（字段一一对应，没有换算）。"""
    return AddressPlan(
        action=plan.action,
        key=plan.key,
        before=plan.before,
        after=plan.after,
        counts_before=plan.counts_before,
        counts_after=plan.counts_after,
        lines=plan.lines,
        entry_index=plan.entry_index,
        internal_id_index=plan.internal_id_index,
        bundle_key_index=plan.bundle_key_index,
        asset_path=plan.asset_path,
        bundle_name=plan.bundle_name,
        resource_type=plan.resource_type,
        dependency_hash=plan.dependency_hash,
    )


def plan_add(
    catalog_bytes: bytes,
    *,
    key: str,
    asset_path: str,
    bundle_name: str,
    resource_type: int = DEFAULT_RESOURCE_TYPE,
    dependency_source_key: str | None = None,
) -> AddressPlan:
    """**纯函数**：catalog 字节 -> 加一条地址之后的字节（⛔ 不碰盘）。

    四条 fail-closed 守卫：键是空的 / **键已经在 catalog 里**（入库的键不许覆盖）/
    `--bundle` 不在 catalog 里 / 自检不过。

    ⚠ 两条路（判别位 = 前 4 字节，见 `catalog_bin.looks_like_catalog_bin`）：

    * `catalog.json`（≤1.2.0）—— 走 `CatalogEdit`（**能逐字节回到原样**的那条正控路）；
    * `catalog.bin`（≥1.2.1）—— 走 `core.assets.catalog_bin_write`（全量重编码；
      正控同样是"原样重编码 == 真件，逐字节"，见 `tests/test_catalog_bin_write.py`）。

    ⛔ 两条路的守卫**同口径、不互相借道**：JSON 那条分支的代码一个字都没动。
    """
    if cbw.looks_like_catalog_bin(catalog_bytes):
        return _address_plan_from_bin(
            cbw.add_address(
                bytes(catalog_bytes),
                key=key,
                asset_path=asset_path,
                bundle_name=bundle_name,
                resource_type=resource_type,
                dependency_source_key=dependency_source_key,
            )
        )
    text_key = str(key).strip()
    text_path = str(asset_path).strip()
    bundle = str(bundle_name).strip()
    if not text_key:
        raise AssetError("--key 是空的", hint="地址键是 catalog 的主键，例 MyIcon")
    if not text_path:
        raise AssetError(
            "--path 是空的",
            hint="资产路径 = catalog 的 m_InternalIds 那条，例 Assets/Mods/My/icon.png",
        )
    if not bundle:
        raise AssetError(
            "--bundle 是空的",
            hint="新地址必须挂在一条**已登记**的包上（依赖键 = 那个包的键下标）",
        )
    if int(resource_type) not in KNOWN_RESOURCE_TYPES:
        raise AssetError(
            f"--resource-type {resource_type} 不在已知取值里（真件见过 {KNOWN_RESOURCE_TYPES}）",
            hint="prefab 地址用 1；图标线是 (3,4) 两条 entry，本命令一次只落一条 —— 要两条就跑两遍",
        )
    before = bytes(catalog_bytes)
    edit = ce.CatalogEdit.from_bytes(before)
    counts_before = tuple(edit.counts)
    if edit.key_index_of(text_key) is not None:
        raise AssetError(
            f"catalog 里已经有这个键：{text_key!r}",
            hint="入库的键不许覆盖 —— 要改就 delete 旧的再加新的（两个动作各自可撤）",
        )
    bundle_key = edit.key_index_of(bundle)
    if bundle_key is None:
        raise AssetError(
            f"catalog 里没有这个包：{bundle!r}",
            hint="先把包登记进 catalog（newasset add-model / 包登记那条链），再来加地址",
        )
    # 依赖声明（entry[2] 依赖键 + entry[3] 依赖 hash）：
    #   ① 给了 --dependency-hash-from ⇒ 照抄那条参照条目（**两个字段都抄**）；
    #   ② 没给、但这条资产路径在 catalog 里已有地址条目 ⇒ 照抄它既有那条；
    #   ③ 都没有 ⇒ 维持老口径（依赖键 = 包自己那条键）。
    # ⚠ 为什么 ①② 必须抄 entry[2]（2026-10-11 真机读数）：只抄 hash、依赖键填「包自己」时，
    #   跨包依赖（vfx / builtinshaders / ammoprefabs / dlc_2_baltic / mapsettings）全没声明 ⇒
    #   游戏报 `Addressable prefab '<新键>' was not loaded!`（真机日志 372/385 行，1.2.0.3）。
    dep_key = int(bundle_key)
    dep_hash: int | None = None
    dep_note = ""
    if dependency_source_key:
        ref = str(dependency_source_key)
        dep_key, dep_hash = _dependency_of_reference(edit, ref)
        dep_note = f"依赖声明抄自 {ref}"
    else:
        existing = edit.addresses_of_path(text_path)
        if existing:
            row = edit.entries[existing[0]]
            dep_key, dep_hash = int(row[2]), int(row[3])
            dep_note = f"依赖声明抄自同路径既有条目 entry#{existing[0]}"
    address = edit.add_address(
        cw.CatalogAddress(
            asset_path=text_path, primary_key=text_key, resource_type=int(resource_type)
        ),
        dependency_key=int(dep_key),
        dependency_hash=dep_hash,
    )
    lines = [
        f"新地址：{text_key} -> {text_path}",
        f"  包登记：{bundle}（键下标 {bundle_key}）   resource_type {int(resource_type)}",
        f"  落位：entry #{address.entry_index} · 键 #{address.key_index}"
        f" · m_InternalIds #{address.internal_id_index} · 桶 #{address.bucket_index}",
        f"  依赖键 #{dep_key}（该键桶里 {len(edit.buckets[dep_key])} 个包）"
        f"  依赖 hash：{dep_hash if dep_hash is not None else '取依赖键自己的'}",
    ]
    if dep_note:
        lines.append(f"  （{dep_note}）")
    return AddressPlan(
        action="add",
        key=text_key,
        before=before,
        after=_self_checked(edit),
        counts_before=counts_before,
        counts_after=tuple(edit.counts),
        lines=tuple(lines),
        entry_index=int(address.entry_index),
        internal_id_index=int(address.internal_id_index),
        bundle_key_index=int(bundle_key),
        asset_path=text_path,
        bundle_name=bundle,
        resource_type=int(resource_type),
        dependency_hash=dep_hash,
    )


def plan_delete(catalog_bytes: bytes, *, key: str) -> AddressPlan:
    """**纯函数**：catalog 字节 -> 删掉一条地址之后的字节（⛔ 不碰盘）。

    ⚠ 两条路的分支与 `plan_add` 同一口径（判别位 = 前 4 字节）。
    """
    if cbw.looks_like_catalog_bin(catalog_bytes):
        return _address_plan_from_bin(cbw.delete_address(bytes(catalog_bytes), key=key))
    text_key = str(key).strip()
    if not text_key:
        raise AssetError("--key 是空的", hint="要删哪一条地址就写那个键")
    before = bytes(catalog_bytes)
    edit = ce.CatalogEdit.from_bytes(before)
    if edit.key_index_of(text_key) is None:
        raise AssetError(
            f"catalog 里没有这个键：{text_key!r}",
            hint="删一个不存在的键会静默通过 —— 本命令不干那件事（先核 catalog 里那条键）",
        )
    report = edit.remove_address(text_key)
    lines = [
        f"删地址：{text_key}",
        f"  {report.summary()}",
        f"  拆掉的 entry：{report.entry_indices}"
        f"   保留：{report.kept_entry_indices}   重指：{report.repointed_entries}",
    ]
    return AddressPlan(
        action="delete",
        key=text_key,
        before=before,
        after=_self_checked(edit),
        counts_before=tuple(report.counts_before),
        counts_after=tuple(report.counts_after),
        lines=tuple(lines),
        entry_index=int(report.key_index),
    )


def write_catalog(
    plan: AddressPlan,
    *,
    catalog_path: str | Path,
    project_root: str | Path | None = None,
    game_root: str | Path | None = None,
    label: str = "",
    allow_running_game: bool = False,
) -> tuple[Path, int]:
    """**真写**：反向 Δ 进补丁柜 -> 写后复核 -> 返回 `(补丁路径, 补丁字节数)`。

    ⛔ 与 `writer` 同一条纪律：**游戏在跑就拦下**（`--allow-running-game` 才放行）；
    写盘用 `new_asset._write_verified`（临时文件 + `os.replace` + 读回逐字节核对）。
    """
    from ba_mod_maker.core.assets.game import is_game_running

    target = Path(catalog_path)
    if not target.is_file():
        raise AssetError(
            f"找不到 catalog：{target}",
            hint="--catalog 指游戏 StreamingAssets/aa/ 下那份 catalog.json / catalog.bin",
        )
    disk = target.read_bytes()
    if disk != plan.before:
        raise AssetError(
            f"{target.name} 与计划读到的基线不一致"
            f"（磁盘 {len(disk):,} B / 计划 {len(plan.before):,} B）",
            hint="catalog 被别的动作改过了 —— 重跑一次这条命令（计划永远从磁盘现状算）",
        )
    if is_game_running() and not allow_running_game:
        raise AssetError(
            "游戏正在运行，catalog 可能被占着",
            hint="先关掉《断箭》；确实要写就加 --allow-running-game（与 writer 同口径）",
        )
    root = Path(project_root) if project_root else Path.cwd()
    game = Path(game_root) if game_root else None
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(root), game_root=game)
    recorder = store.begin(label or default_label(plan))
    recorder.capture_file(target, before=plan.before, after=plan.after)
    patch = recorder.commit()
    _write_verified(target, plan.after, what="catalog")
    got = hashlib.sha256(target.read_bytes()).hexdigest()
    if got != plan.sha_after:
        raise AssetError(
            f"写后复核不通过：磁盘 sha256 {got[:16]}… != 计划 {plan.sha_after[:16]}…",
            hint="补丁柜里那条还在 —— 用 ba-mod-maker assets restore 退回装前",
        )
    return patch, int(patch.stat().st_size)


def default_label(plan: AddressPlan) -> str:
    """补丁柜里那条标签（用户还原时看得到它）。"""
    if plan.action == "add":
        return f"地址：新增 {plan.key}（{plan.asset_path}，包 {plan.bundle_name}）"
    return f"地址：删除 {plan.key}"


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `address` 子命令（`__main__.py` 只调这一句 ＋ 把 `run` 挂进 handlers）。"""
    parser = sub.add_parser(
        "address",
        help="新增 / 删除 catalog 里的一条资产地址（⛔ 不是补丁柜 assets；退出码 0 / 1 / 2）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="address_action", required=True)

    p = actions.add_parser(
        "add",
        help="新增一条地址（键 -> 资产路径，挂在一个**已登记**的包上）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_catalog(p)
    p.add_argument("--key", required=True, help="地址键（catalog 的主键；已经在 catalog 里就拒绝）")
    p.add_argument(
        "--path",
        required=True,
        help="资产路径（m_InternalIds 那条，例 Assets/Mods/My/icon.png）",
    )
    p.add_argument(
        "--bundle", required=True, help="这条地址挂在哪个包上（必须在 catalog 里已登记）"
    )
    p.add_argument(
        "--resource-type",
        dest="resource_type",
        type=int,
        default=DEFAULT_RESOURCE_TYPE,
        help="地址的 resource_type（默认 1 = prefab 的真件形状；图标线是 3）",
    )
    p.add_argument(
        "--dependency-hash-from",
        dest="dependency_hash_from",
        help="抄这条真件模型地址的 entry[3] 当依赖 hash（不给就取依赖键自己的）",
    )
    _add_output(p)

    p = actions.add_parser(
        "delete",
        help="删除一条地址（键不在 catalog 里就报错退出，不静默成功）",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_catalog(p)
    p.add_argument("--key", required=True, help="要删的那条地址键")
    _add_output(p)
    return parser


def _add_catalog(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--catalog",
        required=True,
        help="catalog.json 或 catalog.bin（游戏 StreamingAssets/aa/ 下那份；⛔ 不是 BA_Mod.zip）",
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    """两个动作共用的输出格。"""
    parser.add_argument("--project-root", help="补丁柜的落点（<它>/patches/，默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只算不写（与默认同义）")
    parser.add_argument(
        "--write",
        action="store_true",
        help="真写：反向 Δ 进补丁柜 ＋ 写后读回核对",
    )
    parser.add_argument("--game", help="--write 用：游戏根（默认自动找；补丁柜跟随游戏盘）")
    parser.add_argument("--label", help="--write 用：补丁柜里那条标签（默认按动作生成）")
    parser.add_argument(
        "--allow-running-game",
        dest="allow_running_game",
        action="store_true",
        help="游戏在跑也写（默认拦下 —— 与 writer 同一条纪律）",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    action = str(getattr(args, "address_action", "") or "")
    if action not in ACTIONS:
        emitter.failure(f"不认识的动作 {action!r}", hint=f"这一组只有 {" / ".join(ACTIONS)} 两个")
        return EXIT_UNUSABLE
    catalog = Path(str(args.catalog))
    if not catalog.is_file():
        emitter.failure(
            f"打不开 catalog {catalog}",
            hint="--catalog 指游戏 StreamingAssets/aa/ 下那份 catalog.json / catalog.bin",
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
        before = catalog.read_bytes()
        if action == "add":
            plan = plan_add(
                before,
                key=str(getattr(args, "key", "") or ""),
                asset_path=str(getattr(args, "path", "") or ""),
                bundle_name=str(getattr(args, "bundle", "") or ""),
                resource_type=int(getattr(args, "resource_type", DEFAULT_RESOURCE_TYPE)),
                dependency_source_key=getattr(args, "dependency_hash_from", None),
            )
        else:
            plan = plan_delete(before, key=str(getattr(args, "key", "") or ""))
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    except OSError as exc:
        emitter.failure(f"读不了 {catalog}：{exc}")
        return EXIT_UNUSABLE
    if plan.is_empty:
        emitter.field("catalog", catalog)
        emitter.field("结果", "没有改动（新字节与旧字节逐字节相同）")
        emitter.document(_document(plan, catalog, patch=None, patch_bytes=0, changed=False))
        return EXIT_NOTHING
    patch: Path | None = None
    patch_bytes = 0
    if write:
        try:
            patch, patch_bytes = write_catalog(
                plan,
                catalog_path=catalog,
                project_root=getattr(args, "project_root", None),
                game_root=getattr(args, "game", None),
                label=str(getattr(args, "label", "") or ""),
                allow_running_game=bool(getattr(args, "allow_running_game", False)),
            )
        except (BaModMakerError, OSError) as exc:
            emitter.failure(f"写回失败：{exc}", hint=f"{catalog}（走到这一步之前写回没动手）")
            return EXIT_UNUSABLE
    _report(emitter, plan=plan, catalog_path=catalog, patch=patch, patch_bytes=patch_bytes)
    emitter.document(_document(plan, catalog, patch=patch, patch_bytes=patch_bytes, changed=True))
    return EXIT_DONE


def _report(
    emitter: Emitter,
    *,
    plan: AddressPlan,
    catalog_path: Path,
    patch: Path | None,
    patch_bytes: int,
) -> None:
    """人读那几行：**一条改动只报一条判据**（可复算：计数 + 字节 + sha256）。"""
    for line in plan.lines:
        emitter.line(line)
    emitter.field("catalog", catalog_path)
    emitter.field("计数", f"{plan.counts_before} -> {plan.counts_after}")
    emitter.field("字节", f"{len(plan.before):,} -> {len(plan.after):,}（{plan.byte_delta:+,}）")
    emitter.field("sha256", f"{plan.sha_before[:16]}… -> {plan.sha_after[:16]}…")
    verb = "新增" if plan.action == "add" else "删除"
    emitter.line(
        f"[判据] {verb}地址 {plan.key}：catalog 计数 {plan.counts_before} -> {plan.counts_after}；"
        f" sha256 {plan.sha_before[:16]}… -> {plan.sha_after[:16]}…"
    )
    if patch is None:
        emitter.field("写盘", "（干跑：一个字节都没写；加 --write 才落盘）")
    else:
        emitter.field("反向 Δ", f"{patch}（{patch_bytes:,} B）  还原走 assets restore")


def _document(
    plan: AddressPlan, catalog: Path, *, patch: Path | None, patch_bytes: int, changed: bool
) -> dict[str, Any]:
    """`--json` 那一份（只留标量：整份 catalog 不塞进文档）。"""
    return {
        "action": plan.action,
        "catalog": str(catalog),
        "key": plan.key,
        "changed": bool(changed),
        "bundle": plan.bundle_name or None,
        "asset_path": plan.asset_path or None,
        "resource_type": int(plan.resource_type) if plan.action == "add" else None,
        "entry_index": int(plan.entry_index),
        "internal_id_index": int(plan.internal_id_index),
        "counts_before": list(plan.counts_before),
        "counts_after": list(plan.counts_after),
        "bytes_before": len(plan.before),
        "bytes_after": len(plan.after),
        "sha256_before": plan.sha_before,
        "sha256_after": plan.sha_after,
        "dependency_hash": plan.dependency_hash,
        "patch": str(patch) if patch is not None else None,
        "patch_bytes": int(patch_bytes),
    }
