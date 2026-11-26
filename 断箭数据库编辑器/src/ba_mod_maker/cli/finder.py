"""`find` 子命令：**谁引用了这一行**（零 Qt）。

两种写法：

    ba-mod-maker find <源> <表> <Id>     例：find BA_Mod.zip Units 1
    ba-mod-maker find <源> <名字>        例：find BA_Mod.zip BTR-82

第二种先在**整库**按名字找行（`core/edit/foreign.py::lookup_rows` —— 名字 / HUD 名 /
UIName / 美术文件名都认，纯数字按 Id 精确命中），再对每一行做引用反查。

## 反查口径只有一份

`core/edit/refs.py::referencing_rows` —— 界面侧那块「被谁引用」面板
（`app/widgets/refs_panel.py` + `app/shell/main_window.py::_referencing_rows`）走的就是它。
这里**不自己扫 24 张表**：那一份带着四条判据（只在 `FOREIGN_TABLE` 登记过的列里找、
`0` / `None` / bool 一律不算命中、表按数据源顺序 / 行按表内顺序、`row_id=0` 直接空），
照抄一份迟早两边不一样。

## 退出码（也写在 `--help` 里）

| 码 | 什么时候 |
|---|---|
| 0 | 找到了引用者 |
| 1 | 查询跑通了，但**没有**引用者（或那个名字一行都没命中） |
| 2 | 打不开源 / 表名不认识 / Id 不是数字 / 那一行不存在 |

## 打印的是**引用者**，不是被引用的那一行

一行一条：`表 #Id  列名  名字`。名字那一列用的是 `core/edit/foreign.py::row_label`，
与界面上候选列表同口径（关联行没有名字列，会按外键拼成「单位 BTR-82 · 装甲 Armor BTR80 Base」）。
"""

from __future__ import annotations

import argparse
from typing import Any

from ba_mod_maker.cli.emit import Emitter
from ba_mod_maker.core.errors import BaModMakerError

#: 退出码。与 `validator.py` 同一套语义（0 有结果 / 1 空结果 / 2 用不了）。
EXIT_FOUND = 0
EXIT_NOTHING = 1
EXIT_UNUSABLE = 2

#: 引用者清单的列宽（表名最长 28：SpecializationAvailabilities；列名最长 28）。
_TABLE_WIDTH = 30
_ID_WIDTH = 8
_COLUMN_WIDTH = 29

#: 空结果的提示。**与界面侧同一句**（`app/widgets/refs_panel.py::NO_HITS`）——
#: 同一件事在 CLI 与界面上说两种话，用户会以为是两回事。
NO_HITS = "没有别的行引用这一行"

#: 名字检索没命中时的提示（`lookup_rows` 的口径：空关键词不扫库）。
NO_MATCH = "整库没有找到叫这个名字的行"

_DESCRIPTION = """谁引用了这一行（外键反查，口径与界面「被谁引用」面板一致）。

两种写法：
  find <源> <表> <Id>    谁引用了 <表> 里 Id=<Id> 那一行
  find <源> <名字>       先整库按名字找到行，再列出每一行被谁引用

退出码：
  0  找到了引用者
  1  没有引用者（或那个名字一行都没命中）
  2  打不开源 / 表名不认识 / Id 不是数字 / 那一行不存在
"""

_EPILOG = r"""认哪些源（与 validate 同一份解析）：
  DataBaseCompiled/（或它的某个实例目录）   BA_Mod.zip（老名字 db.zip 也认）
  DataBaseCompiled.json                    data.unity3d / 游戏根目录

例：
  ba-mod-maker find ..\DataBaseCompiled\52085 Units 1
  ba-mod-maker find BA_Mod.zip Units 1 --json
  ba-mod-maker find BA_Mod.zip BTR-82
"""


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `find` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "find",
        help="谁引用了这一行：find <源> <表> <Id> 或 find <源> <名字>"
        "（退出码 0 有引用 / 1 没有 / 2 用不了）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        help="数据源：DataBaseCompiled 目录（或它的实例目录）/ BA_Mod.zip / "
        "DataBaseCompiled.json / data.unity3d / 游戏根目录",
    )
    parser.add_argument("first", help="表名（后面还要给 Id），或者直接给名字")
    parser.add_argument("second", nargs="?", help="行 Id（给了它，第一个参数就按表名解）")
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    from ba_mod_maker.cli.source import load

    emitter = Emitter(json_mode=bool(args.json_out))
    row_id = _row_id_of(args.second)
    if args.second is not None and row_id is None:
        emitter.failure(
            f"Id 得是一个整数，收到 {args.second!r}",
            hint="例：find BA_Mod.zip Units 1",
        )
        return EXIT_UNUSABLE

    try:
        with load(args.source) as loaded:
            if row_id is None:
                document = _by_name(emitter, loaded, str(args.first))
            else:
                document = _by_row(emitter, loaded, str(args.first), row_id)
            total = int(document["total_references"])
    except _UnusableError:
        # 坐标用不了（表名不认识 / 那一行不存在）：失败话术已经在里面出过了。
        return EXIT_UNUSABLE
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNUSABLE
    emitter.document(document)
    return EXIT_FOUND if total else EXIT_NOTHING


def _row_id_of(text: str | None) -> int | None:
    """`second` 是不是一个整数 Id（`None` 与 `"BTR-82"` 都返回 None）。"""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 两种查法（各自返回一份要发给机器的文档）
# --------------------------------------------------------------------------- #


def _by_row(emitter: Emitter, loaded: Any, table: str, row_id: int) -> dict[str, Any]:
    """`find <源> <表> <Id>`：只查一行。"""
    from ba_mod_maker.core.asset.tables import resolve_table_name
    from ba_mod_maker.core.edit.foreign import find_row, row_label
    from ba_mod_maker.core.edit.refs import referencing_rows

    instance = loaded.instance
    target = resolve_table_name(table, instance.table_names)
    if target is None:
        emitter.failure(
            f"这份数据里没有表 {table}",
            hint=f"可用的表：{'、'.join(instance.table_names)}",
        )
        raise _UnusableError

    if find_row(instance, target, row_id) is None:
        emitter.failure(
            f"{target} 里没有 Id={row_id} 的行",
            hint="Id 不是行号（行号会随插行位移）；它写在每行的 Id 列里",
        )
        raise _UnusableError

    label = row_label(instance, target, row_id)
    hits = referencing_rows(instance, target, row_id)
    emitter.field("源", loaded.path)
    emitter.field("目标", f"{target} #{row_id} · {label}")
    emitter.field("引用者", f"{len(hits)} 条")
    _print_hits(emitter, hits)
    return {
        **_header(loaded),
        "mode": "row",
        "query": {"table": target, "row_id": row_id},
        "targets": [_target_document(target, row_id, label, hits)],
        "total_references": len(hits),
    }


def _by_name(emitter: Emitter, loaded: Any, name: str) -> dict[str, Any]:
    """`find <源> <名字>`：整库找名字 -> 每一行都做一次反查。"""
    from ba_mod_maker.core.edit.foreign import LOOKUP_LIMIT, lookup_rows
    from ba_mod_maker.core.edit.refs import referencing_rows

    instance = loaded.instance
    result = lookup_rows(instance, name)
    emitter.field("源", loaded.path)
    emitter.field("名字", name or "（空）")
    emitter.field("命中", f"{result.total} 行（整库 {loaded.tables} 张表）")

    targets: list[dict[str, Any]] = []
    #: 只把**有引用者**的那几行画出来（另外那几行最后一句带过，免得一屏全是空段落）。
    listed: list[tuple[str, int, str, Any]] = []
    with_hits = 0
    for hit in result.hits:
        hits = referencing_rows(instance, hit.table, hit.row_id)
        targets.append(_target_document(hit.table, hit.row_id, hit.name, hits))
        if hits:
            with_hits += 1
            listed.append((hit.table, hit.row_id, hit.name, hits))

    emitter.field("有引用者的", f"{with_hits} 行")
    for table, row_id, label, hits in listed:
        emitter.blank()
        emitter.line(f"--- {table} #{row_id} · {label} ---")
        emitter.line(f"  引用者：{len(hits)} 条")
        _print_hits(emitter, hits, indent=4)
    empty = len(targets) - with_hits
    if empty:
        emitter.line(f"（另外 {empty} 行没有被任何行引用）")
    if not targets:
        emitter.line(NO_MATCH)
    if result.truncated:
        emitter.line(
            f"⚠️ 整库命中 {result.total} 条，超过一次能查的 {LOOKUP_LIMIT} 条上限 —— "
            f"只查了前 {len(result.hits)} 行；把名字打具体点能查全"
        )

    return {
        **_header(loaded),
        "mode": "name",
        "query": {
            "name": name,
            "match_total": result.total,
            "match_limit": len(result.hits),
            "match_truncated": result.truncated,
        },
        "targets": targets,
        "total_references": sum(len(target["references"]) for target in targets),
    }


def _header(loaded: Any) -> dict[str, Any]:
    """两种查法共用的抬头 —— 与 `validate` 的文档同一组字段。

    机器读两个子命令时不用分叉：`source` / `kind` / `label` / `path_id` /
    `tables` / `rows` 六样在两边含义完全一样。
    """
    return {
        "source": str(loaded.path),
        "kind": loaded.kind,
        "label": loaded.label,
        "path_id": loaded.path_id,
        "tables": loaded.tables,
        "rows": loaded.rows,
    }


class _UnusableError(Exception):
    """`find` 的坐标用不了（表名不认识 / 那一行不存在）—— 已经在 `run` 里变成退出码 2。

    为什么用异常：两种查法都可能在中途发现"这个坐标查不了"，走 `return` 会把
    `run` 里那一段错误处理抄两遍（AGENTS §5「逻辑只有一份」）。
    """


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #


def _print_hits(emitter: Emitter, hits: Any, *, indent: int = 2) -> None:
    if not hits:
        emitter.line("  " * (indent // 2) + NO_HITS)
        return
    prefix = " " * indent
    for hit in hits:
        table = f"{hit.table:<{_TABLE_WIDTH}}"
        row = f"#{hit.row_id:<{_ID_WIDTH - 1}}"
        column = f"{hit.column:<{_COLUMN_WIDTH}}"
        emitter.line(f"{prefix}{table}{row}{column}{hit.label}")


def _target_document(table: str, row_id: int, label: str, hits: Any) -> dict[str, Any]:
    return {
        "table": table,
        "row_id": row_id,
        "label": label,
        "references": [
            {"table": hit.table, "row_id": hit.row_id, "column": hit.column, "label": hit.label}
            for hit in hits
        ],
    }


__all__ = [
    "EXIT_FOUND",
    "EXIT_NOTHING",
    "EXIT_UNUSABLE",
    "NO_HITS",
    "NO_MATCH",
    "add_parser",
    "run",
]
