r"""`catalog` 子命令：catalog 的**地址清单**（只读）—— 用户需求⑩ 的 H2 / H3。

     ba-mod-maker catalog key    RU_T72B     # 正查：键 -> 实际路径/包/类型/依赖/crc-size-hash
     ba-mod-maker catalog bundle units_assets_all_… # 反查：包 -> 它支撑哪些键
     ba-mod-maker catalog path   Assets/…/x.prefab  # 反查：实际路径 -> 哪些键指着它

## 为什么要有它（用户原话）

「难道你没发现，数据库里填的地址，是**映射地址**，而不是实际地址吗？」·
「那我给新的 bundle 包加新模型、新图标的时候，也要能知道这些素材的**映射地址**，
不然我不好改数据库。」

数据库里那一格填的是**键**（`Units.ModelFileName` / `ThumbnailFileName` …），
**实际路径只写在 catalog 的 `m_InternalIds` 里** —— 本命令就是把这两头连起来的那条只读入口。

## 一条记录印什么（= catalog 的一条 entry）

| 字段 | 从哪来 |
|---|---|
| 键 | 桶里那条 entry 的 `primaryKey`（`m_KeyDataString` 解出来的键值） |
| 实际路径 | `m_InternalIds[entry[0]]`（＋它的下标） |
| provider | `m_ProviderIds[entry[1]]` |
| resource_type | `m_resourceTypes[entry[6]]` —— **图标是 3（＋类型 4 别名一条）、prefab 是 1** |
| 依赖声明 | `entry[2]` 依赖键（**桶里是一整组包**）＋ `entry[3]` 依赖 hash |
| crc / size / hash | 那些**包条目**自己的 `m_ExtraDataString`（`AssetBundleRequestOptions`） |

## 三条硬口径

1. **只读**：本组不开写通道、不建补丁柜、不动 catalog 一个字节（写地址走 `address` 那组）；
2. **不许静默返回空**：键 / 包 / 路径不在 catalog 里 ⇒ **退出码 1 ＋ 点名**（⛔ 不是印一张空表）；
3. 键**不是**主键：一个键的桶里可以有多条 entry（图标线就是 (3,4) 两条）⇒ 一条不落地全印。

## 退出码（与 finder 同族：0 有 / 1 没有 / 2 用不了）

| 码 | 什么时候 |
|---|---|
| 0 | 命中（≥1 条） |
| 1 | catalog 打开了，但**没有**这个键 / 包 / 路径（点名） |
| 2 | 用不了（catalog 打不开 / 找不到游戏目录 / 参数不对） |

⚠ 本组**不动** `address`（那是写线）：`address add` / `delete` 是「新增 / 删除」在地址层的落点；
本组只**读**同一份 catalog，读法与 `address_edit` 共用 `asset_kernel/catalog_edit.py` 那一份解析。
"""

from __future__ import annotations

import argparse
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.cli.emit import Emitter, pad
from ba_mod_maker.core.assets import catalog_bin as cb
from ba_mod_maker.core.errors import AssetError, BaModMakerError

__all__ = [
    "ACTIONS",
    "EXIT_FOUND",
    "EXIT_MISSING",
    "EXIT_UNUSABLE",
    "add_parser",
    "entry_record",
    "registration_lines",
    "render_records",
    "run",
]

#: 退出码（与 finder 同一套语义：0 有 / 1 没有 / 2 用不了）。
EXIT_FOUND = 0
EXIT_MISSING = 1
EXIT_UNUSABLE = 2

#: 这一组三个**只读**动作（顺序 = `--help` 里的顺序）。
ACTIONS: Final[tuple[str, str, str]] = ("key", "bundle", "path")

#: 人读那几行的标签宽度（与 emit.LABEL_WIDTH 同一套显示宽度算法）。
_LABEL_WIDTH = 13

_DESCRIPTION = """地址清单（只读）：catalog 的 键 <-> 实际路径 <-> 包。

数据库里填的是**映射地址（键）**，实际路径只写在 catalog 的 m_InternalIds 里 —— 这里把两头连起来。

  catalog key    <键>     正查：键 -> 实际路径 ＋ 包 ＋ resource_type ＋ 依赖声明 ＋ crc/size/hash
  catalog bundle <包名>   反查：这个包直接支撑哪些键（＋ 通过依赖组间接能点到它的键）
  catalog path   <路径>   反查：这条 m_InternalIds 路径有哪些键指着它

⛔ 只读；写地址走 address add / delete（那是另一组）。

退出码：
  0  命中（至少一条）
  1  打开了 catalog，但没有这个键 / 包 / 路径（点名，不印空表）
  2  用不了（catalog 打不开 / 找不到游戏目录 / 参数不对）
"""

_EPILOG = """--catalog  指 catalog.json；省略 = 自动找游戏里那份（aa/catalog.json）。
--game     游戏根（--catalog 省略时用它找；也可以只用它）。
--json     只输出一个 JSON 文档（人读那份改走 stderr）。

例：
  ba-mod-maker catalog key RU_T72B
  ba-mod-maker catalog key RU_T72B-Label
  ba-mod-maker catalog bundle units_assets_all_1e6c04ce42984f32a0891b92fab010e8.bundle
  ba-mod-maker catalog path Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab
"""


# --------------------------------------------------------------------------- #
# 读：一条 entry -> 一条记录（正查 / 反查 / 登记面清单**共用**这一个渲染器）
# --------------------------------------------------------------------------- #
def _short(text: object) -> str:
    """程序集全名只留第一段（`UnityEngine.GameObject, UnityEngine.CoreModule, …`）。"""
    return str(text).split(",", 1)[0].strip()


def _leaf(text: object) -> str:
    """命名空间全名只留最后一段（`…ResourceProviders.AssetBundleProvider`）。"""
    return _short(text).rsplit(".", 1)[-1]


def _labels(edit: ce.CatalogEdit, field: str, *, leaf: bool) -> list[str]:
    """`m_ProviderIds` 是字符串数组；`m_resourceTypes` 是 `{m_AssemblyName, m_ClassName}` 数组。"""
    values = edit.raw.get(field)
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            text = str(item.get("m_ClassName") or item.get("m_AssemblyName") or item)
        else:
            text = str(item)
        out.append(_leaf(text) if leaf else _short(text))
    return out


def view(catalog_bytes: bytes) -> tuple[ce.CatalogEdit, cw.CatalogTable]:
    """字节 -> （可重编号视图, 读侧表）。两个都建一次，别在循环里反复建。"""
    edit = ce.CatalogEdit.from_bytes(bytes(catalog_bytes))
    return edit, edit.to_table()


def _key_value(table: cw.CatalogTable, index: int) -> Any:
    if not 0 <= int(index) < table.key_count:
        return f"<键下标越界 {index}>"
    return table.key_value(int(index))


def bundle_info(edit: ce.CatalogEdit, entry_index: int) -> dict[str, Any]:
    """一条**包条目**的读数（包文件名 ＋ 它自己的 m_ExtraDataString）。"""
    row = edit.entries[int(entry_index)]
    internal = str(edit.internal_ids[row[ce.ENTRY_INTERNAL_ID]])
    name = internal.replace(chr(92), "/").rsplit("/", 1)[-1]
    options: Mapping[str, Any] | None = None
    offset = int(row[ce.ENTRY_EXTRA_OFFSET])
    if offset >= 0:
        try:
            options = cw.entry_options(edit.extra_data, offset)
        except AssetError:
            options = None
    return {
        "entry_index": int(entry_index),
        "name": name,
        "internal_id": internal,
        "m_Hash": (str(options.get("m_Hash")) if options else None),
        "m_Crc": (int(options.get("m_Crc")) if options else None),
        "m_BundleSize": (int(options.get("m_BundleSize")) if options else None),
        "m_BundleName": (str(options.get("m_BundleName")) if options else None),
    }


def entry_record(
    edit: ce.CatalogEdit, table: cw.CatalogTable, entry_index: int
) -> dict[str, Any]:
    """一条 entry 的全部登记面读数（键 / 实际路径 / 类型 / 依赖 / crc / size / hash）。"""
    row = edit.entries[int(entry_index)]
    is_bundle = edit.is_bundle_entry(int(entry_index))
    provider = _labels(edit, "m_ProviderIds", leaf=True)
    types = _labels(edit, "m_resourceTypes", leaf=False)
    dep_key = int(row[ce.ENTRY_DEPENDENCY_KEY])
    dependency: dict[str, Any] | None = None
    if dep_key >= 0:
        members = list(edit.buckets[dep_key]) if 0 <= dep_key < len(edit.buckets) else []
        dependency = {
            "key_index": dep_key,
            "key_value": _key_value(table, dep_key),
            "hash": int(row[ce.ENTRY_DEPENDENCY_HASH]),
            "member_count": len(members),
            "bundles": [
                bundle_info(edit, m) for m in members if edit.is_bundle_entry(int(m))
            ],
        }
    type_index = int(row[ce.ENTRY_RESOURCE_TYPE])
    return {
        "entry_index": int(entry_index),
        "role": "bundle" if is_bundle else "asset",
        "primary_key_index": int(row[ce.ENTRY_PRIMARY_KEY]),
        "key": _key_value(table, int(row[ce.ENTRY_PRIMARY_KEY])),
        "asset_path": str(edit.internal_ids[row[ce.ENTRY_INTERNAL_ID]]),
        "internal_id_index": int(row[ce.ENTRY_INTERNAL_ID]),
        "provider_index": int(row[ce.ENTRY_PROVIDER]),
        "provider": (provider[int(row[ce.ENTRY_PROVIDER])]
                     if int(row[ce.ENTRY_PROVIDER]) < len(provider) else "?"),
        "resource_type_index": type_index,
        "resource_type": (types[type_index] if 0 <= type_index < len(types) else "?"),
        "dependency": dependency,
    }


def bundle_name_note(info: Mapping[str, Any]) -> str:
    """包那条的尾巴：包内真名（`m_BundleName`）—— 与装到盘上的文件名**不是一回事**。"""
    name = info.get("m_BundleName")
    return f"  (m_BundleName {name})" if name else ""


def render_records(records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """把记录印成人读那几行（H2 / H3 / H4 **共用**这一个渲染器）。"""
    out: list[str] = []
    for record in records:
        role = "包登记" if record.get("role") == "bundle" else "资产地址"
        out.append(f"entry#{record['entry_index']}  [{role}]")
        out.append(f"  {pad('键', _LABEL_WIDTH)}: {record['key']}")
        out.append(f"  {pad('实际路径', _LABEL_WIDTH)}: {record['asset_path']}")
        out.append(f"  {pad('m_InternalIds', _LABEL_WIDTH)}: #{record['internal_id_index']}")
        out.append(f"  {pad('provider', _LABEL_WIDTH)}: {record['provider_index']} "
                   f"{record['provider']}")
        out.append(f"  {pad('resource_type', _LABEL_WIDTH)}: "
                   f"{record['resource_type_index']} {record['resource_type']}")
        dependency = record.get("dependency")
        if dependency is None:
            out.append(f"  {pad('依赖声明', _LABEL_WIDTH)}: 无（entry[2] = -1）")
            continue
        bundles = list(dependency.get("bundles") or [])
        out.append(f"  {pad('依赖声明', _LABEL_WIDTH)}: 键#{dependency['key_index']} "
                   f"（桶里 {dependency['member_count']} 条，其中包 {len(bundles)} 个）"
                   f"  entry[3] hash {dependency['hash']}")
        for info in bundles:
            out.append(f"      · {info['name']}")
            out.append(f"        m_Hash {info['m_Hash']} · m_Crc {info['m_Crc']}"
                       f" · m_BundleSize {info['m_BundleSize']}"
                       + bundle_name_note(info))
    return tuple(out)


def tsv_block(records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """可抄的那一块：**制表符分隔**，一行一条 entry（表头 ＋ 行）。"""
    header = ("键\t实际路径\tresource_type\t依赖键\t依赖hash\t所在包\tm_Crc\tm_BundleSize\tm_Hash")
    lines = ["可抄（制表符分隔）：", header]
    for record in records:
        dependency = record.get("dependency") or {}
        bundles = list(dependency.get("bundles") or [])
        first = bundles[0] if bundles else {}
        lines.append("\t".join(str(x) for x in (
            record["key"],
            record["asset_path"],
            f"{record['resource_type_index']} {record['resource_type']}",
            dependency.get("key_index", "-"),
            dependency.get("hash", "-"),
            "|".join(str(b.get("name")) for b in bundles) or "-",
            first.get("m_Crc", "-"),
            first.get("m_BundleSize", "-"),
            first.get("m_Hash", "-"),
        )))
    return tuple(lines)


# --------------------------------------------------------------------------- #
# 三个动作各自的取数
# --------------------------------------------------------------------------- #
def key_records(
    edit: ce.CatalogEdit, table: cw.CatalogTable, key: str
) -> tuple[dict[str, Any], ...]:
    """正查：键 -> 它桶里每一条 entry 的登记面。"""
    index = edit.key_index_of(str(key))
    if index is None:
        return ()
    return tuple(entry_record(edit, table, int(e)) for e in edit.buckets[index])


def path_records(
    edit: ce.CatalogEdit, table: cw.CatalogTable, asset_path: str
) -> tuple[dict[str, Any], ...]:
    """反查：这条实际路径（m_InternalIds）上的每一条 entry。"""
    wanted = str(asset_path)
    hits = [
        index
        for index, row in enumerate(edit.entries)
        if str(edit.internal_ids[row[ce.ENTRY_INTERNAL_ID]]) == wanted
    ]
    return tuple(entry_record(edit, table, int(i)) for i in hits)


def bundle_report(
    edit: ce.CatalogEdit, table: cw.CatalogTable, bundle: str
) -> dict[str, Any]:
    """反查：一个包 -> ① 它自己的登记 ② 直接依赖它的键 ③ 通过依赖组间接点到它的键。"""
    index = edit.key_index_of(str(bundle))
    if index is None:
        return {}
    owned = [e for e in edit.buckets[index] if edit.is_bundle_entry(int(e))]
    own = entry_record(edit, table, int(owned[0])) if owned else None
    direct = sorted({
        str(_key_value(table, int(edit.entries[e][ce.ENTRY_PRIMARY_KEY])))
        for e in edit.dependent_entries(index)
    })
    groups: list[dict[str, Any]] = []
    indirect: set[str] = set()
    if owned:
        owner_map = edit.owners_of_entries()
        for group_index in owner_map.get(int(owned[0]), []):
            if group_index == index:
                continue
            members = list(edit.buckets[group_index])
            if not all(edit.is_bundle_entry(int(m)) for m in members):
                continue
            keys = sorted({
                str(_key_value(table, int(edit.entries[e][ce.ENTRY_PRIMARY_KEY])))
                for e in edit.dependent_entries(int(group_index))
            })
            groups.append({
                "key_index": int(group_index),
                "key_value": _key_value(table, int(group_index)),
                "member_count": len(members),
                "keys": keys,
            })
            indirect.update(keys)
    return {
        "bundle": str(bundle),
        "key_index": int(index),
        "is_bundle_entry": bool(owned),
        "own": own,
        "direct_keys": direct,
        "groups": groups,
        "indirect_keys": sorted(indirect),
    }


def registration_lines(
    catalog_bytes: bytes,
    entry_indices: Sequence[int],
    *,
    title: str = "本次登记面清单",
) -> tuple[str, ...]:
    """H4：`newasset add` / `add-model` 的计划末尾那张清单（与正查**同一个**渲染器）。"""
    edit, table = view(catalog_bytes)
    records = [
        entry_record(edit, table, int(i))
        for i in entry_indices
        if 0 <= int(i) < len(edit.entries)
    ]
    if not records:
        return (f"{title}：（本次没有登记新的 catalog 条目）",)
    out = [f"{title}（{len(records)} 条；印法与 `catalog key` 同源）"]
    out.extend(render_records(records))
    out.extend(tsv_block(records))
    return tuple(out)


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `catalog` 子命令（`__main__.py` 只调这一句 ＋ 把 `run` 挂进 handlers）。"""
    parser = sub.add_parser(
        "catalog",
        help="地址清单（只读）：键 -> 实际路径/包/类型/依赖/crc，或 包/路径 -> 键",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="catalog_action", required=True)
    for name, payload, helptext in (
        ("key", "键", "正查：这个键指向哪条实际路径、在哪些包里、依赖声明与 crc/size/hash"),
        ("bundle", "包名", "反查：这个包支撑哪些键（直接 ＋ 通过依赖组间接）"),
        ("path", "资产路径", "反查：这条 m_InternalIds 路径有哪些键指着它"),
    ):
        child = actions.add_parser(
            name, help=helptext, description=_DESCRIPTION,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        child.add_argument("value", metavar=payload, help=helptext)
        child.add_argument(
            "--catalog",
            help="catalog.json（省略 = 自动找游戏里那份 StreamingAssets/aa/catalog.json）",
        )
        child.add_argument("--game", help="游戏根目录（--catalog 省略时用它找）")
        child.add_argument(
            "--json",
            dest="json_out",
            action="store_true",
            help="只输出一个 JSON 文档（人读那份改走 stderr）",
        )
    return parser


def _catalog_path(args: argparse.Namespace) -> Path | None:
    """catalog 落点：--catalog 优先；否则从游戏根找（找不到返回 None —— **不是异常**）。"""
    explicit = getattr(args, "catalog", None)
    if explicit:
        path = Path(str(explicit))
        return path if path.is_file() else None
    from ba_mod_maker.core.assets import game as game_mod

    text = getattr(args, "game", None)
    root = Path(text) if text else game_mod.find_game_root()
    if root is None:
        return None
    found = game_mod.catalog_file(root)
    return found if found is not None and Path(found).is_file() else None


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 有 / 1 没有 / 2 用不了）。"""
    emitter = Emitter(json_mode=bool(getattr(args, "json_out", False)))
    action = str(getattr(args, "catalog_action", "") or "")
    if action not in ACTIONS:
        emitter.failure(f"不认识的动作 {action!r}", hint="这一组只有 " + " / ".join(ACTIONS))
        return EXIT_UNUSABLE
    catalog = _catalog_path(args)
    if catalog is None:
        emitter.failure(
            "找不到 catalog（catalog.json / catalog.bin 都没有）",
            hint="给 --catalog 显式指一份，或用 --game 指游戏根（StreamingAssets/aa/）",
        )
        return EXIT_UNUSABLE
    blob = catalog.read_bytes()
    if catalog.name == "catalog.bin" or cb.looks_like_catalog_bin(blob):
        # ★ 1.2.1：二进制那份走**同一套渲染器**（`BinaryCatalogView` 给出与 JSON 同形的记录）。
        bview = BinaryCatalogView.load(catalog)
        counts = bview.counts()
        emitter.field("catalog", catalog)
        emitter.field("格式", "二进制 BinaryStorageBuffer v2（Addressables 2.x）")
        emitter.field("计数头", f"键 {counts[0]:,} / 桶 {counts[1]:,} / 条目 {counts[2]:,}")
        emitter.field("distinct 内部 id", f"{bview.internal_ids_count:,} 条")
        value = str(getattr(args, "value", "") or "")
        if action == "key":
            return _run_binary_key(emitter, bview, catalog, value)
        if action == "bundle":
            return _run_binary_bundle(emitter, bview, catalog, value)
        return _run_binary_path(emitter, bview, catalog, value)
    try:
        edit, table = view(blob)
    except (BaModMakerError, OSError) as exc:
        emitter.failure(f"读不了 {catalog}：{exc}")
        return EXIT_UNUSABLE
    counts = edit.counts
    emitter.field("catalog", catalog)
    emitter.field("计数头", f"键 {counts[0]:,} / 桶 {counts[1]:,} / 条目 {counts[2]:,}")
    emitter.field("m_InternalIds", f"{len(edit.internal_ids):,} 条")

    value = str(getattr(args, "value", "") or "")
    if action == "key":
        return _run_key(emitter, edit, table, catalog, value)
    if action == "bundle":
        return _run_bundle(emitter, edit, table, catalog, value)
    return _run_path(emitter, edit, table, catalog, value)


def _run_key(
    emitter: Emitter, edit: ce.CatalogEdit, table: cw.CatalogTable, catalog: Path, key: str
) -> int:
    text = key.strip()
    if not text:
        emitter.failure("要给一个键", hint="ba-mod-maker catalog key <键>（例 RU_T72B）")
        return EXIT_UNUSABLE
    records = key_records(edit, table, text)
    if not records:
        emitter.failure(
            f"catalog 里没有这个键：{text!r}",
            hint="键要逐字相同（大小写敏感）。反查包里的键：catalog bundle <包名>",
        )
        return EXIT_MISSING
    for line in render_records(records):
        emitter.line(line)
    for line in tsv_block(records):
        emitter.line(line)
    emitter.line(f"[判据] 键 {text!r} 命中 {len(records)} 条 entry")
    emitter.document({
        "action": "key",
        "catalog": str(catalog),
        "key": text,
        "counts": list(counts_tuple(edit)),
        "internal_ids": len(edit.internal_ids),
        "records": records,
    })
    return EXIT_FOUND


def _run_bundle(
    emitter: Emitter, edit: ce.CatalogEdit, table: cw.CatalogTable, catalog: Path, bundle: str
) -> int:
    text = bundle.strip()
    if not text:
        emitter.failure("要给一个包名", hint="ba-mod-maker catalog bundle <包名 = catalog 里的键>")
        return EXIT_UNUSABLE
    report = bundle_report(edit, table, text)
    if not report:
        emitter.failure(
            f"catalog 里没有这个包（键）：{text!r}",
            hint="包名 = catalog 的 m_KeyDataString 里那条键（例 units_assets_all_<hash>.bundle）",
        )
        return EXIT_MISSING
    if not report["is_bundle_entry"]:
        emitter.line(f"★ {text!r} 在 catalog 里存在，但**不是一条包登记**（不是 .bundle 条目）")
    own = report.get("own")
    if own is not None:
        for line in render_records([own]):
            emitter.line(line)
    direct = list(report["direct_keys"])
    groups = list(report["groups"])
    indirect = list(report["indirect_keys"])
    emitter.line(f"直接依赖这个包的键：{len(direct)} 条")
    for name in direct[:40]:
        emitter.line(f"    · {name}")
    if len(direct) > 40:
        emitter.line(f"    …（还有 {len(direct) - 40} 条，完整清单见 --json）")
    emitter.line(f"包在 {len(groups)} 个依赖组里；经这些组间接能点到它的键：{len(indirect)} 条")
    for group in groups[:12]:
        emitter.line(f"    组键#{group['key_index']} {group['key_value']!r}"
                     f"（桶里 {group['member_count']} 个包）-> {len(group['keys'])} 条键")
    if len(groups) > 12:
        emitter.line(f"    …（还有 {len(groups) - 12} 个组，完整清单见 --json）")
    for name in indirect[:20]:
        emitter.line(f"    · {name}")
    if len(indirect) > 20:
        emitter.line(f"    …（还有 {len(indirect) - 20} 条，完整清单见 --json）")
    emitter.line(f"[判据] 包 {text!r}：直接键 {len(direct)} 条 / 组 {len(groups)} 个 / "
                 f"间接键 {len(indirect)} 条")
    emitter.document({
        "action": "bundle",
        "catalog": str(catalog),
        "bundle": text,
        "is_bundle_entry": bool(report["is_bundle_entry"]),
        "own": own,
        "direct_keys": direct,
        "groups": groups,
        "indirect_keys": indirect,
    })
    return EXIT_FOUND


def _run_path(
    emitter: Emitter, edit: ce.CatalogEdit, table: cw.CatalogTable, catalog: Path, asset_path: str
) -> int:
    text = asset_path.strip()
    if not text:
        emitter.failure(
            "要给一条实际路径",
            hint="例 Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab（要逐字相同）",
        )
        return EXIT_UNUSABLE
    records = path_records(edit, table, text)
    if not records:
        emitter.failure(
            f"catalog 的 m_InternalIds 里没有这条路径：{text!r}",
            hint="路径要逐字相同（含大小写与扩展名）；也可以先 catalog key <键> 看它的实际路径",
        )
        return EXIT_MISSING
    for line in render_records(records):
        emitter.line(line)
    emitter.line(f"[判据] 路径 {text!r} 上有 {len(records)} 条 entry")
    emitter.document({
        "action": "path",
        "catalog": str(catalog),
        "asset_path": text,
        "records": records,
    })
    return EXIT_FOUND


# --------------------------------------------------------------------------- #
# ★ 2026-10-13：**二进制 catalog 的同一套视图**（1.2.1 起游戏换成 catalog.bin）
# --------------------------------------------------------------------------- #
#
# 口径差别（写在 kb/40 §2.2）：二进制里**只有 76 个包名键直连 .bundle**；
# 资产键的条目 internal_id **就是资产路径**，它的 dependencies 才是"这资产可能落在的那串包"。
# 本节的取数逻辑就按这个形状写：
#   · 资产条目的「所在包」= 它 dependency 集合里的**包条目**；
#   · 资产条目的「依赖键」= 成员恰好等于该集合、且成员全是包条目的那个键（= JSON 的"依赖组键"）；
#   · 包条目的「直接依赖它的键」= 依赖集合里含这个包条目的资产条目的 primary_key。


@dataclass(slots=True)
class BinaryCatalogView:
    """二进制 catalog 的登记面视图（与 :class:`ce.CatalogEdit` 给出**同形**的记录）。"""

    path: Path
    parsed: cb.CatalogBin
    provider_labels: tuple[str, ...] = ()
    _key_by_offset: dict[int, int] = field(default_factory=dict)
    _key_members: tuple[tuple[int, ...], ...] = ()
    _offset_to_member_index: dict[int, int] = field(default_factory=dict)
    _entry_by_offset: dict[int, dict[str, Any]] = field(default_factory=dict)
    _bundle_entries_of_group: dict[int, dict[str, Any]] = field(default_factory=dict)
    _extra_cache: dict[int, dict[str, Any] | None] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> BinaryCatalogView:
        parsed = cb.load_catalog_bin(path)
        offsets: list[tuple[int, int]] = []  # (key_index, location_offset)
        members: list[tuple[int, ...]] = []
        for index, key in enumerate(parsed.keys):
            if key is None:
                members.append(())
                continue
            _name, location_set = parsed.key_data[index]
            if location_set == 0xFFFFFFFF:
                members.append(())
                continue
            raw = parsed.sized(location_set)
            ids = tuple(
                off for off in struct.unpack_from(f"<{len(raw) // 4}I", raw) if off != 0xFFFFFFFF
            )
            members.append(ids)
            for off in ids:
                offsets.append((index, off))
        key_by_offset = {off: index for index, off in offsets}
        # 一个 offset 在**多个键**的桶里时取键下标最小的那个（确定性）。
        for index, off in offsets:
            key_by_offset.setdefault(off, index)
        member_index = {off: i for i, off in enumerate(sorted(set(key_by_offset)))}
        # 包名 -> provider 标签（二进制里没有 provider 数组，按 76 个包条目的 provider 去重收集）
        providers: list[str] = []
        for off in sorted(set(key_by_offset)):
            loc = parsed.location(off)
            name = _leaf(loc.provider or "") if loc else ""
            if name and name not in providers:
                providers.append(name)
        return cls(
            path=path,
            parsed=parsed,
            provider_labels=tuple(providers),
            _key_by_offset=key_by_offset,
            _key_members=tuple(members),
            _offset_to_member_index=member_index,
        )

    # ---------------- 基础 ----------------
    @property
    def key_count(self) -> int:
        return len(self.parsed.key_data)

    def counts(self) -> tuple[int, int, int]:
        """与 JSON 版同形的计数头：键 / 桶（二进制里与键同一个数组）/ 条目。"""
        return (len(self.parsed.key_data), len(self.parsed.key_data), len(self.parsed.locations))

    @property
    def internal_ids_count(self) -> int:
        """distinct 内部 id（= 资产路径 / 包路径）条数。"""
        seen = {
            (loc.internal_id or "")
            for loc in self.parsed.locations.values()
            if loc.internal_id is not None
        }
        return len(seen)

    def key_value(self, index: int) -> object:
        if 0 <= index < len(self.parsed.keys):
            return self.parsed.keys[index]
        return f"<键下标越界 {index}>"

    def key_index_of(self, key: str) -> int | None:
        for index, value in enumerate(self.parsed.keys):
            if str(value) == key:
                return index
        return None

    @property
    def member_offsets(self) -> tuple[int, ...]:
        return tuple(sorted(self._offset_to_member_index))

    def group_bundle_entries(self, key_index: int) -> dict[tuple[int, ...], dict[str, Any]]:
        """键下标 -> {成员偏移元组: 包条目记录}（**只有成员全是包条目**的那些键才算"依赖组"）。"""
        if key_index in self._bundle_entries_of_group:
            return {(): self._bundle_entries_of_group[key_index]}
        out: dict[tuple[int, ...], dict[str, Any]] = {}
        for index, members in enumerate(self._key_members):
            if not members:
                continue
            locs = [self.parsed.location(off) for off in members]
            if any(loc is None or loc.bundle_name is None for loc in locs):
                continue
            out[tuple(members)] = self._bundle_group_record(index, members)
        self._bundle_entries_of_group[key_index] = out.get(self._key_members[key_index], {})
        return {key: value for key, value in out.items() if key == self._key_members[key_index]}

    def _bundle_group_record(self, index: int, members: tuple[int, ...]) -> dict[str, Any]:
        return {
            "key_index": index,
            "key_value": self.key_value(index),
            "member_count": len(members),
            "member_offsets": members,
        }

    def key_records(self, key: str) -> tuple[dict[str, Any], ...]:
        index = self.key_index_of(key)
        if index is None:
            return ()
        return tuple(self.entry_record(off, key_index=index) for off in self._key_members[index])

    def path_records(self, asset_path: str) -> tuple[dict[str, Any], ...]:
        wanted = str(asset_path)
        out = []
        for off in self.member_offsets:
            loc = self.parsed.location(off)
            if loc is not None and str(loc.internal_id or "") == wanted:
                out.append(self.entry_record(off))
        return tuple(out)

    def entry_record(self, offset: int, *, key_index: int | None = None) -> dict[str, Any]:
        cached = self._entry_by_offset.get(offset)
        if cached is not None:
            return cached
        loc = self.parsed.location(offset)
        if loc is None:
            return {}
        members = self._key_members[key_index] if key_index is not None else ()
        deps = list(loc.dependency_offsets)
        dependency: dict[str, Any] | None = None
        if deps:
            # ★ 二进制版**没有 JSON 那种"依赖组键"这一层**：每个依赖**自己就是一条包键**
            #   （实测：units 条目 offset 208059 只出现在键#73 的桶里，而键#73 的桶 = [208059]）。
            #   ⇒ 这里把依赖翻译成"包键清单"，`key_index` 报**第一个**包键。
            bundle_keys: list[dict[str, Any]] = []
            bundles: list[dict[str, Any]] = []
            for d in deps:
                dep_loc = self.parsed.location(d)
                if dep_loc is None:
                    continue
                owner = self._owner_key_of(d)
                if dep_loc.bundle_name:
                    bundles.append(self._bundle_record(d))
                bundle_keys.append({
                    "key_index": owner if owner is not None else -1,
                    "key_value": (
                        self.key_value(owner) if owner is not None else dep_loc.primary_key
                    ),
                    "is_bundle": bool(dep_loc.bundle_name),
                    "internal_id": dep_loc.internal_id,
                })
            first_key = next((k["key_index"] for k in bundle_keys if k["key_index"] >= 0), -1)
            dependency = {
                "key_index": first_key,
                "key_value": self.key_value(first_key) if first_key >= 0 else None,
                "hash": loc.dependency_hash,
                "member_count": len(deps),
                "bundles": bundles,
                "bundle_keys": bundle_keys,
                "schema": "binary-dependency-list",
            }
        elif members and all(
            (self.parsed.location(m) or None) and self.parsed.location(m).bundle_name
            for m in members
        ):
            dependency = {
                "key_index": key_index if key_index is not None else -1,
                "key_value": self.key_value(key_index) if key_index is not None else None,
                "hash": 0,
                "member_count": len(members),
                "bundles": [self._bundle_record(m) for m in members],
            }
        provider = _leaf(loc.provider or "")
        record = {
            "entry_index": offset,
            "role": "bundle" if loc.bundle_name else "asset",
            "primary_key_index": key_index if key_index is not None else -1,
            "key": (self.key_value(key_index) if key_index is not None else loc.primary_key),
            "asset_path": str(loc.internal_id or ""),
            "internal_id_index": -1,
            "provider_index": (
                self.provider_labels.index(provider) if provider in self.provider_labels else -1
            ),
            "provider": provider or "?",
            "resource_type_index": -1,
            "resource_type": _short(loc.type_class or "?"),
            "dependency": dependency,
            "location_offset": offset,
        }
        self._entry_by_offset[offset] = record
        return record

    def _bundle_record(self, offset: int) -> dict[str, Any]:
        loc = self.parsed.location(offset)
        if loc is None:
            return {}
        entry = self.parsed.read_bundle_entry(loc)
        return {
            "entry_index": offset,
            "name": loc.bundle_name,
            "internal_id": loc.internal_id,
            "m_Hash": entry.hash_id if entry else None,
            "m_Crc": entry.crc if entry else None,
            "m_BundleSize": entry.bundle_size if entry else None,
            "m_BundleName": entry.internal_name if entry else None,
        }

    def _group_key_of(self, members: tuple[int, ...]) -> int | None:
        wanted = set(members)
        for index, got in enumerate(self._key_members):
            if got and set(got) == wanted:
                return index
        return None

    def _owner_key_of(self, offset: int) -> int | None:
        for index, members in enumerate(self._key_members):
            if offset in members:
                return index
        return None

    def bundle_report(self, bundle: str) -> dict[str, Any]:
        index = self.key_index_of(str(bundle))
        if index is None:
            return {}
        members = self._key_members[index]
        def _is_bundle(off: int) -> bool:
            return (self.parsed.location(off) or None) is not None and bool(
                self.parsed.location(off).bundle_name
            )

        owned = [m for m in members if _is_bundle(m)]
        # 要 **entry_record 那个形状**（render_records 认它）；`_bundle_record` 只是包四件套。
        own = self.entry_record(owned[0], key_index=index) if owned else None
        own_offsets = set(owned)
        direct: set[str] = set()
        groups: list[dict[str, Any]] = []
        indirect: set[str] = set()
        for m in owned:
            for other_index, other_members in enumerate(self._key_members):
                if other_index == index or m not in other_members:
                    continue
                # `other_index` 是"依赖组"（成员全是包条目）时，依赖它的资产键就是间接键。
                if other_members and all(
                    (self.parsed.location(x) or None) and self.parsed.location(x).bundle_name
                    for x in other_members
                ):
                    keys = self._dependent_keys(set(other_members))
                    groups.append({
                        "key_index": other_index,
                        "key_value": self.key_value(other_index),
                        "member_count": len(other_members),
                        "keys": sorted(keys),
                    })
                    indirect.update(keys)
        direct = self._dependent_keys(own_offsets)
        return {
            "bundle": str(bundle),
            "key_index": index,
            "is_bundle_entry": bool(owned),
            "own": own,
            "direct_keys": sorted(direct),
            "groups": groups,
            "indirect_keys": sorted(indirect),
        }

    def _dependent_keys(self, bundle_offsets: set[int]) -> set[str]:
        """哪些**资产键**的条目依赖集合里含这些包条目。"""
        out: set[str] = set()
        for index, members in enumerate(self._key_members):
            if not members or index not in self._key_by_offset.values():
                continue
            for m in members:
                loc = self.parsed.location(m)
                if loc is None or loc.bundle_name is not None:
                    continue
                if bundle_offsets & set(loc.dependency_offsets):
                    out.add(str(self.key_value(index)))
                    break
        return out


def _run_binary_key(
    emitter: Emitter, bview: BinaryCatalogView, catalog: Path, key: str
) -> int:
    text = key.strip()
    if not text:
        emitter.failure("要给一个键", hint="ba-mod-maker catalog key <键>（例 RU_T72B）")
        return EXIT_UNUSABLE
    records = bview.key_records(text)
    if not records:
        emitter.failure(
            f"catalog 里没有这个键：{text!r}",
            hint="键要逐字相同（大小写敏感）。反查包里的键：catalog bundle <包名>",
        )
        return EXIT_MISSING
    for line in render_records(records):
        emitter.line(line)
    for line in tsv_block(records):
        emitter.line(line)
    emitter.line(f"[判据] 键 {text!r} 命中 {len(records)} 条 entry")
    emitter.document({
        "action": "key",
        "catalog": str(catalog),
        "format": "catalog.bin",
        "key": text,
        "counts": list(bview.counts()),
        "records": records,
    })
    return EXIT_FOUND


def _run_binary_bundle(
    emitter: Emitter, bview: BinaryCatalogView, catalog: Path, bundle: str
) -> int:
    text = bundle.strip()
    if not text:
        emitter.failure("要给一个包名", hint="ba-mod-maker catalog bundle <包名 = catalog 里的键>")
        return EXIT_UNUSABLE
    report = bview.bundle_report(text)
    if not report:
        emitter.failure(
            f"catalog 里没有这个包（键）：{text!r}",
            hint="包名 = catalog 的键（例 units_assets_all_<hash>.bundle）",
        )
        return EXIT_MISSING
    if not report["is_bundle_entry"]:
        emitter.line(f"★ {text!r} 在 catalog 里存在，但**不是一条包登记**（不是 .bundle 条目）")
    own = report.get("own")
    if own is not None:
        for line in render_records([own]):
            emitter.line(line)
    direct = list(report["direct_keys"])
    groups = list(report["groups"])
    indirect = list(report["indirect_keys"])
    emitter.line(f"直接依赖这个包的键：{len(direct)} 条")
    for name in direct[:40]:
        emitter.line(f"    · {name}")
    if len(direct) > 40:
        emitter.line(f"    …（还有 {len(direct) - 40} 条，完整清单见 --json）")
    emitter.line(f"包在 {len(groups)} 个依赖组里；经这些组间接能点到它的键：{len(indirect)} 条")
    for group in groups[:12]:
        emitter.line(
            f"    组键#{group['key_index']} {group['key_value']!r}"
            f"（桶里 {group['member_count']} 个包）-> {len(group['keys'])} 条键"
        )
    if len(groups) > 12:
        emitter.line(f"    …（还有 {len(groups) - 12} 个组，完整清单见 --json）")
    for name in indirect[:20]:
        emitter.line(f"    · {name}")
    if len(indirect) > 20:
        emitter.line(f"    …（还有 {len(indirect) - 20} 条，完整清单见 --json）")
    emitter.line(
        f"[判据] 包 {text!r}：直接键 {len(direct)} / 组 {len(groups)} / 间接键 {len(indirect)}"
    )
    emitter.document({
        "action": "bundle",
        "catalog": str(catalog),
        "format": "catalog.bin",
        "bundle": text,
        "is_bundle_entry": bool(report["is_bundle_entry"]),
        "own": own,
        "direct_keys": direct,
        "groups": groups,
        "indirect_keys": indirect,
    })
    return EXIT_FOUND


def _run_binary_path(
    emitter: Emitter, bview: BinaryCatalogView, catalog: Path, asset_path: str
) -> int:
    text = asset_path.strip()
    if not text:
        emitter.failure(
            "要给一条实际路径",
            hint="例 Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab（要逐字相同）",
        )
        return EXIT_UNUSABLE
    records = bview.path_records(text)
    if not records:
        emitter.failure(
            f"catalog 里没有这条实际路径：{text!r}",
            hint="路径要逐字相同（含大小写与扩展名）；也可以先 catalog key <键> 看它的实际路径",
        )
        return EXIT_MISSING
    for line in render_records(records):
        emitter.line(line)
    emitter.line(f"[判据] 路径 {text!r} 上有 {len(records)} 条 entry")
    emitter.document({
        "action": "path",
        "catalog": str(catalog),
        "format": "catalog.bin",
        "asset_path": text,
        "records": records,
    })
    return EXIT_FOUND


def counts_tuple(edit: ce.CatalogEdit) -> tuple[int, int, int]:
    """`--json` 用的计数头（与 `CatalogEdit.counts` 同一个数，只是显式一层）。"""
    return tuple(int(x) for x in edit.counts)  # type: ignore[return-value]