
r"""catalog crc 子命令：**改过的包 ⇒ 把 catalog 里那条既有登记改对**（默认干跑）。

## 这个命令解决什么

改了游戏 aa\PC 下某个既有 bundle 的字节 ⇒ 它在 catalog.bin 里登记的
m_Crc / m_BundleSize 必须跟着走，否则游戏按旧 CRC 取包，引擎串一句
CRC Mismatch（Provided x, calculated y, Will not load AssetBundle）
—— 结论与四层证据见 _work/l9/README.md（真件 76/76 命中 crc32(逐块解压拼接)）。

## 三个既有事实（本命令按它们设计）

1. 读一条既有登记：writer.catalog_entry（JSON 与二进制两份都认）；
2. 出补丁后的整份字节：writer.catalog_patch（内部走 catalog_bin_write.serialize 的
   mutate 钩子；自带正控：原样重编码必须逐字节回原样，不成立就抛）；
3. ★ aa/catalog.hash 的内容 = hash128_hex(整份 catalog.bin)（外部 oracle，真件逐字相同），
   而产品那条 --catalog 路**只写 catalog.bin、不写 aa/catalog.hash**。
   ⇒ 本命令把**这两个文件当成一对**：一起记进同一条反向 Δ、一起写、一起撤
   （口径照 core/asset/new_asset.py 那句「catalog.hash 与 catalog 是一对，别拆成两笔」）。

## 用法

    ba-mod-maker catalog crc --bundle <改过的包> [--name <catalog 键>]
                             [--catalog <catalog.bin>] [--hash <catalog.hash>]
                             [--game <游戏根>] [--project-root <工程>] [--write] [--json]

    --bundle     **已经改好的**那个包；--name 不给就用它的**文件名**当 catalog 键
                 （catalog 的包键 = bundle 文件名，一字不差）
    --write      真写；⛔ 不给就只出读数（一个字节都不写）
    --project-root  反向 Δ 的落点（<它>/patches/，与 assets restore 共用一个柜）

## 退出码（与 mesh edit / anim apply 同族）

| 码 | 什么时候 |
|---|---|
| 0 | 算出来了 / --write 已写回且写后复核通过 |
| 1 | **没有可改的**（catalog 那条登记与盘上那份包逐位相符）—— 这同时也是**复核判据**：
      装完之后再跑一次（不带 --write）**应当**得到它 |
| 2 | 用不了（文件缺 / 包名不在 catalog 里 / 原样重编码不是逐字节原样 / 参数不对） |

## 三条硬口径

1. **默认干跑**；干跑**连补丁柜目录都不建**；
2. **算法只有一份**：crc 一律 new_asset.bundle_facts_of_file、哈希一律
   catalog_bin_write.hash128_hex —— ⛔ 本命令不自己算一遍；
3. **写后自证**：重开 catalog 读出那条登记、与**盘上那份包**重算的值对拍；
   再把 hash128(新 catalog) 与盘上 catalog.hash 的内容对拍 —— 两条都过才算成。

## 已知边界（两条，如实写在这里）

**D2 · NOOP 判据只比「登记 vs 盘上那份包」，没收紧到「catalog.hash 也配对」**
⇒ 若有人先用 `mesh edit --catalog` / `object set --write`（它们会自动找游戏根的 catalog）改过
`catalog.bin`、把 `aa/catalog.hash` 留成旧值，**再**跑本命令：
登记已经与包相符 ⇒ 本命令判 NOOP（rc=1）、**不会**去补那个 hash。
本条流程不碰这个形态（三条记账都由本命令自己写、hash 由它同步）；
⚠ 收紧它要动 `run()` 里那条 NOOP 分支（把 hash 比对也算进去），留作收尾项 —— 改之前先想清
「登记相符而 hash 不配对」时该只写 hash 还是两者都重写。

**D3 · 它只管「既有登记」的 m_Crc / m_BundleSize**：⛔ 不新增登记、不删登记、不动地址层
（那三件事分别走 `newasset` / `address` / `object container`）。
"""

from __future__ import annotations

import argparse
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ba_mod_maker.cli.emit import Emitter

EXIT_OK = 0
#: 「没有可改的」—— 装完之后再跑一次应当得到它（= 记账记对了）。
EXIT_NOOP = 1
EXIT_UNUSABLE = 2

#: catalog 在游戏根里的相对位置（唯一出处 = core/assets/game.py 的 CATALOG_SUBDIR 那一组）。
AA_SUBDIR = Path("BrokenArrow_Data") / "StreamingAssets" / "aa"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _modules():
    """重活（UnityPy / catalog_bin_write）一律延迟 import —— 与 mesh_edit / anim 同口径。"""
    from ba_mod_maker.asset_kernel import (
        reverse_delta,
        writer,
    )
    from ba_mod_maker.core.asset import new_asset
    from ba_mod_maker.core.assets import catalog_bin_write as cbw

    return writer, new_asset, cbw, reverse_delta


def _resolve(args: argparse.Namespace) -> tuple:
    """参数 -> (catalog, hash 文件, 包, 键名, game_root)。

    找不到的**不在这里抛**（返回 None 让调用方自己报），⛔ 也不在这里找游戏根之外的落点。
    """
    bundle = Path(str(getattr(args, "bundle", "") or ""))
    name = str(getattr(args, "name", "") or "") or bundle.name
    explicit = str(getattr(args, "catalog", "") or "")
    game_root: Path | None = None
    if explicit:
        catalog = Path(explicit)
        hash_text = str(getattr(args, "hash", "") or "")
        hash_path = Path(hash_text) if hash_text else catalog.with_name("catalog.hash")
    else:
        from ba_mod_maker.core.assets import game as game_mod

        root_text = str(getattr(args, "game", "") or "")
        game_root = Path(root_text) if root_text else game_mod.find_game_root()
        if game_root is None:
            return None, None, bundle, name, None
        catalog = Path(game_root) / AA_SUBDIR / "catalog.bin"
        hash_path = Path(game_root) / AA_SUBDIR / "catalog.hash"
    return catalog, hash_path, bundle, name, game_root


def run(args: argparse.Namespace) -> int:
    """跑一遍（默认干跑）。返回退出码。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    writer, new_asset, cbw, reverse_delta = _modules()

    catalog, hash_path, bundle, name, game_root = _resolve(args)
    if catalog is None:
        emitter.failure(
            "找不到游戏根（也就找不到 catalog）",
            hint="给 --game <游戏根>，或直接给 --catalog <catalog.bin>",
        )
        return EXIT_UNUSABLE
    for path, what in ((catalog, "catalog"), (bundle, "bundle")):
        if not path.is_file():
            emitter.failure(f"找不到{'包' if what == 'bundle' else ' catalog'}：{path}")
            return EXIT_UNUSABLE

    emitter.field(
        "catalog",
        f"{catalog}（{catalog.stat().st_size:,} B · sha16 {_sha256_of(catalog)[:16]}）",
    )
    emitter.field(
        "catalog.hash",
        (f"{hash_path}（{hash_path.read_text(encoding='ascii').strip()}）"
         if hash_path.is_file() else f"{hash_path}（盘上没有）"),
    )
    emitter.field("包", f"{bundle}（{bundle.stat().st_size:,} B）")
    emitter.field("键", name)

    try:
        facts, _data, stream_size = new_asset.bundle_facts_of_file(bundle)
    except Exception as exc:  # noqa: BLE001 -- 读盘失败一律折成「用不了」，不让 traceback 冒给用户
        emitter.failure(f"读不动那个包：{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE
    emitter.field("盘上那份包算出来的", f"crc={facts['crc']} bundle_size={facts['bundle_size']} "
                                    f"（解压流 {stream_size:,} B · m_hash={facts['m_hash']}）")

    try:
        old = writer.catalog_entry(catalog, name)
    except Exception as exc:  # noqa: BLE001 -- 键不在 catalog / catalog 读不开都归这一档
        emitter.failure(f"读 catalog 那条登记时拒了：{type(exc).__name__}: {exc}")
        return EXIT_UNUSABLE
    emitter.field("catalog 里那条登记", f"crc={old.crc} bundle_size={old.bundle_size}")

    if int(old.crc) == int(facts["crc"]) and int(old.bundle_size) == int(facts["bundle_size"]):
        emitter.failure(
            f"{name} 这条登记与盘上那份包**逐位相符** —— 没有可改的（一个字节都不写）",
            hint="装完之后再跑一次看到它 = 记账记对了",
        )
        return EXIT_NOOP

    before = catalog.read_bytes()
    if cbw.roundtrip(before) != before:
        emitter.failure(
            "catalog 原样重编码不是逐字节原样 —— 宁可不写",
            hint="序列化口径与磁盘上那份不一致；把它交回来查",
        )
        return EXIT_UNUSABLE

    patch = writer.catalog_patch(
        catalog, name, crc=int(facts["crc"]), bundle_size=int(facts["bundle_size"])
    )
    if patch.before != before:
        emitter.failure("补丁里的 before 与刚读的 catalog 不是同一份（文件被改过）")
        return EXIT_UNUSABLE
    hash_before = hash_path.read_bytes() if hash_path.is_file() else None
    hash_after = new_asset.catalog_hash_bytes(patch.after)
    emitter.field(
        "补丁", f"{len(patch.before):,} -> {len(patch.after):,} B（Δ {patch.offset_shift:+d}）"
    )
    emitter.field("crc", f"{patch.crc_old} -> {patch.crc_new}")
    emitter.field("bundle_size", f"{patch.size_old} -> {patch.size_new}")
    emitter.field("catalog.hash", f"{_text(hash_before)} -> {hash_after.decode('ascii')}")

    if not getattr(args, "write", False):
        emitter.line("干跑：catalog 与 catalog.hash **一个字节都没写**（要写加 --write）")
        return EXIT_OK

    project = Path(str(getattr(args, "project_root", "") or "."))
    store = reverse_delta.PatchStore(project / "patches", game_root=game_root or project)
    builder = store.begin("catalog crc：既有包登记的 m_Crc / m_BundleSize ＋ catalog.hash")
    builder.capture_file(catalog, before=patch.before, after=patch.after)
    builder.capture_file(hash_path, before=hash_before, after=hash_after)
    patch_path = builder.commit()

    catalog.write_bytes(patch.after)
    hash_path.write_bytes(hash_after)

    back = writer.catalog_entry(catalog, name)
    facts2, _d2, _s2 = new_asset.bundle_facts_of_file(bundle)
    ok_entry = (int(back.crc) == int(facts2["crc"])
                and int(back.bundle_size) == int(facts2["bundle_size"]))
    want_hash = cbw.hash128_hex(catalog.read_bytes())
    got_hash = hash_path.read_text(encoding="ascii").strip()
    ok_hash = got_hash == want_hash
    emitter.field("反向 Δ", f"{patch_path}（{patch_path.stat().st_size:,} B）")
    emitter.field("写后读回", f"crc={back.crc} bundle_size={back.bundle_size}"
                            f"（盘上那份包 {facts2['crc']},{facts2['bundle_size']}）"
                            f" · {'✅ 对上了' if ok_entry else '❌ 对不上'}")
    emitter.field("hash 配对", f"盘上 {got_hash} vs hash128 {want_hash}"
                             f" · {'✅ 相等' if ok_hash else '❌ 不等'}")
    emitter.document({
        "bundle": str(bundle),
        "key": name,
        "catalog": str(catalog),
        "catalog_hash": str(hash_path),
        "crc_old": patch.crc_old,
        "crc_new": patch.crc_new,
        "size_old": patch.size_old,
        "size_new": patch.size_new,
        "catalog_bytes_before": len(patch.before),
        "catalog_bytes_after": len(patch.after),
        "catalog_hash_before": _text(hash_before),
        "catalog_hash_after": got_hash,
        "patch": str(patch_path),
        "readback_paired": bool(ok_entry),
        "hash_paired": bool(ok_hash),
    })
    if not (ok_entry and ok_hash):
        return EXIT_NOOP
    return EXIT_OK


def _text(blob: bytes | None) -> str:
    """catalog.hash 的内容做读数用（装前不存在时印「（盘上没有）」）。"""
    return blob.decode("ascii", "replace") if blob else "（盘上没有）"


def add_crc_action(catalog_parser: Any) -> None:
    """把 crc 这个动作挂到既有的 catalog 组上（__main__.py 只调这一句）。

    ⛔ 不给 catalog 组本身另起一个 parser：那一组的注册与只读三动作住在
    address_inventory.add_parser 里，本模块只**追加**一个动作，不动它原有的判定。
    """
    actions = None
    for action in catalog_parser._actions:  # noqa: SLF001 -- argparse 只给这一个口子拿子命令组
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 -- 同上
            actions = action
            break
    if actions is None:
        raise RuntimeError("catalog 组里没有子命令组 —— 注册顺序变了？")
    # 父组的抬头/收尾由 address_inventory 写（那三个动作确实只读）—— 这里**追加**一句，
    # 免得用户看到「只读」却撞上一个会写盘的动作。⛔ 不改它原来那几行。
    note = (
        "\n本组四个动作里 **key / bundle / path 只读**；"
        "**crc 是写档**（默认干跑，--write 才落字节）：\n"
        "它把 catalog.bin 里那条既有登记的 m_Crc / m_BundleSize 改对，"
        "并把 aa/catalog.hash 当一对一起改。"
    )
    catalog_parser.epilog = (catalog_parser.epilog or "") + note
    catalog_parser.description = (catalog_parser.description or "") + note
    child = actions.add_parser(
        "crc",
        help="改过的包 => 把它在 catalog 里那条既有登记的 m_Crc / m_BundleSize 改对"
             "（＋ 同步 aa/catalog.hash；默认干跑，--write 才落字节）",
        description=__doc__,
        epilog=(
            "退出码：\n"
            "  0  算出来了 / --write 已写回且写后复核通过\n"
            "  1  **没有可改的**（那条登记与盘上那份包逐位相符）—— 装完之后再跑一次应当得到它\n"
            "  2  用不了（文件缺 / 包名不在 catalog 里 / 原样重编码不是逐字节原样 / 参数不对）\n\n"
            "例：\n"
            "  ba-mod-maker catalog crc --game D:/game --bundle aa/PC/units_assets_all_x.bundle\n"
            "  ba-mod-maker catalog crc --game D:/game"
            " --bundle aa/PC/units_assets_all_x.bundle --write\n"
            "  # 撤（与 assets 共用一个补丁柜）\n"
            "  ba-mod-maker assets restore --game D:/game --project-root <工程>\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    child.add_argument(
        "--bundle", required=True, help="**已经改好的**那个包（键名默认取它的文件名）"
    )
    child.add_argument(
        "--name", help="catalog 里那条键（= bundle 文件名）；不给就用 --bundle 的文件名"
    )
    child.add_argument("--catalog", help="catalog.bin / catalog.json（不给就按 --game 推）")
    child.add_argument("--hash", help="aa/catalog.hash（默认与 --catalog 同目录）")
    child.add_argument("--game", help="游戏根目录（--catalog 省略时用它找）")
    child.add_argument(
        "--project-root",
        help="反向 Δ 的落点（<它>/patches/，与 assets restore 共用一个柜；默认当前目录）",
    )
    child.add_argument(
        "--write", action="store_true", help="真写；⛔ 不给就只出读数（一个字节都不写）"
    )
    child.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )


#: 本模块自己负责的动作名（dispatch 用）。
ACTIONS: Sequence[str] = ("crc",)


def dispatch(args: argparse.Namespace) -> int:
    """catalog 组的总分发：**crc 归本模块**，其余（只读三动作）原样交给 address_inventory。"""
    action = str(getattr(args, "catalog_action", "") or "")
    if action in ACTIONS:
        return run(args)
    from ba_mod_maker.cli import address_inventory

    return address_inventory.run(args)


__all__ = ["ACTIONS", "EXIT_NOOP", "EXIT_OK", "EXIT_UNUSABLE", "add_crc_action", "dispatch", "run"]
