r"""`row-delete` 子命令：**删除一行**（产品三原子动词里缺的那一个）。

## 为什么要有它

`AGENTS.md` §0.10 定的产品操作集是 **新增 / 删除 / 修改**，而 CLI 里从前只有：

| 动词 | 入口 |
|---|---|
| 新增 | `clone-unit`（整套克隆一个单位）· `object add` · `material skin add` … |
| 修改 | `set`（改一个单元格）· `object set` · `material skin update` … |
| **删除** | ⛔ 包内对象有 `object delete`、材质槽有 `material skin remove`，
| | **数据表整行没有入口** —— 这一条就是本节补的。 |

    ba-mod-maker row-delete <源> <表> <Id> [<Id> ...] [--out <新文件>] [--write]

例（清掉 3 行测试遗留）：

    ba-mod-maker row-delete "D:\...\broken_arrow\BA_Mod.zip" Units 707 708 709
    # 默认干跑：只印计划，一个字节都不写

    ba-mod-maker row-delete "D:\...\BA_Mod.zip" Units 707 708 709 --out D:\tmp\clean.zip --write

## 七条硬口径

1. **按 Id 定位，⛔ 不按行下标** —— 行下标会漂（前面删一行，后面全体前移）；
   界面写回那条路用的也是 Id（`AGENTS.md` §4.5）。
2. **⛔ 默认干跑**，要真写必须 `--write`（与 `object` 那条路同口径）。
   ⚠ `set` 是默认就写的老口径，本命令**不跟** —— 删行比改一格危险得多。
3. **删之前先说清「谁引用它」**（G1，2026-10-12 补）。三档策略**互斥**，默认最保守：

   | 档 | 行为 |
   |---|---|
   | `--refuse`（**默认**） | 只要有**一行**指向要删的行 ⇒ 整条命令不动，退出 2，逐条印出来 |
   | `--cascade` | 把**传递闭包**一起删（指着它的、再指着那些的……一直到底） |
   | `--force` | 明知有引用也删，并把**会变孤儿的行逐条列出来** |

   尺子只有一把：`core/edit/refs.py::referencing_rows`（谁指着我：哪张表、哪些 Id、哪一列）。
   ⛔ 上一轮用「整数值命中新增 Id」算闭包 —— **只匹配到 6 行，是巧合**，那把尺子不认列，
   任何等于该 Id 的整数都算命中（废尺子清单见 kb/37）。
   ⚠ 闭包**真的不止一层**：实测 Units#3（BMP-3）直接引用者 38 行，闭包 43 行 ——
   多出来的 5 行是 Options，它们指的是这个单位的 Modifications，而 Modifications 指的是它。
   只做一层的实现会漏掉这 5 行（用例里钉着 38 != 43）。
4. **强制留反向 Δ**：删掉的每一行**整行原值 ＋ 它原来的行下标**写进
   `<落点>.row-delete-delta.json` 并印出路径。还原走 `row-restore`（唯一入口），
   往返**逐字节相同**。⛔ 不拷整包当备份（用户 2026-10-05 定的）。
5. **写后复核**（只有 `--write` 才做）：重新打开落点逐条核 ——
   ① 那几个 Id 真的没了；② 每张动过的表行数正好少了它该少的那么多。
6. **写前过「游戏在跑就别写」那道闸**（`AGENTS.md` §6 三条闸的第 2 条）：
   游戏在跑且没给 `--allow-running` ⇒ 退出 2、一个字节都不写。
7. **⛔ 查不到引用不等于没有引用**：`referencing_rows` 只认 `FOREIGN_TABLE` 里登记过的
   外键列。没登记成外键的列（例：按名字 / 字符串拼出来的关联）它看不见 —— 这是已知边界，
   不是「安全」的证明。凡 `*Id` 结尾的列都必须登记（`tests/test_edit_refs.py` 钉着）。

退出码：0 完成（含干跑）· 1 写盘失败 / 复核不过 ·
2 用不了（打不开源 / 表名或 Id 不存在 / 没落点 / 有引用且是 `--refuse` / 游戏在跑）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: 三档引用策略（互斥）。REFUSE 是**默认**：最保守的那一档。
REFUSE = "refuse"
CASCADE = "cascade"
FORCE = "force"

#: 逐条打印引用者时的上限。三档都要「看得见」；真遇到成百上千条时截断**并说清截了多少** ——
#: 静默截断会让人以为只有那么多。
MAX_LISTED = 500

#: 反向 Δ 的格式版本。v1（上一轮留的）：顶层单表、行里没有 index；
#: v2：tables 分表 ＋ 每行带 index（原表下标）—— 有了 index 才谈得上逐字节还原。
DELTA_VERSION = 2

_POLICY_LABEL = {
    REFUSE: "--refuse（默认：有引用就拒）",
    CASCADE: "--cascade（连子行一起删）",
    FORCE: "--force（明知有引用也删）",
}


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """注册 `row-delete`（⛔ 实现只有下面 `run` 一处，这里只挂参数）。"""
    p = sub.add_parser(
        "row-delete",
        help="删除数据表里的一行或多行（默认干跑；删前先报「谁引用它」，默认 --refuse 有引用就拒）",
        epilog=(
            "三档引用策略互斥，默认最保守的 --refuse：\n"
            "  --refuse    有一行指向要删的行就整条命令不动（退出 2）\n"
            "  --cascade   把传递闭包一起删（指着它的、再指着那些的……）\n"
            "  --force     明知有引用也删，并把会变孤儿的行逐条列出来\n"
            "\n"
            "退出码：\n"
            "  0  完成（含干跑）\n"
            "  1  写盘失败 / 写后复核不过\n"
            "  2  用不了（打不开源 / 表名或 Id 不存在 / 没落点 / 有引用且是 --refuse /\n"
            "     游戏在跑而没给 --allow-running）\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="BA_Mod.zip / DataBaseCompiled.json / data.unity3d")
    p.add_argument("table", help="表名（例 Units）")
    p.add_argument("row_id", nargs="+", type=int, help="要删的行 **Id**（⛔ 不是行下标）")
    p.add_argument("--out", help="删完之后另存一份到这里；省略 = 写回打开的那个文件")
    p.add_argument("--write", action="store_true", help="真写（⛔ 不给就只出计划）")
    p.add_argument("--delta-out", help="反向 Δ 的落点（默认 <落点>.row-delete-delta.json）")
    p.add_argument(
        "--allow-running",
        action="store_true",
        help="允许游戏在跑时写入（默认：游戏在跑就拒，一个字节都不写）",
    )
    policy = p.add_mutually_exclusive_group()
    policy.add_argument("--refuse", dest="refs", action="store_const", const=REFUSE,
                        help="（默认）要删的行只要被别的行引用就整条命令不动")
    policy.add_argument("--cascade", dest="refs", action="store_const", const=CASCADE,
                        help="把传递闭包一起删（指着它的、再指着那些的……）")
    policy.add_argument("--force", dest="refs", action="store_const", const=FORCE,
                        help="明知有引用也删，并把会变孤儿的行逐条列出来")
    p.set_defaults(refs=REFUSE)
    return p


def _delta_path(target: str, override: str | None) -> Path:
    """反向 Δ 落哪 —— 默认贴着落点，一处实现。"""
    return Path(override) if override else Path(str(target) + ".row-delete-delta.json")


def _count_by_table(pairs: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """[(表名, Id)] -> [(表名, 条数)]，按首次出现顺序（别用 dict 的隐式顺序去猜）。"""
    counts: dict[str, int] = {}
    for name, _ in pairs:
        counts[name] = counts.get(name, 0) + 1
    return list(counts.items())


def _print_hits(title: str, hits: tuple) -> None:
    """把「谁指向它」印成人能读的几行：先按表给个数，再逐条列。"""
    print(f"{title}：{len(hits)} 条")
    for name, group in _count_by_table([(h.table, h.row_id) for h in hits]):
        print(f"    {name:<28} {group:>5} 条")
    for hit in hits[:MAX_LISTED]:
        print(f"      {hit.table}#{hit.row_id}.{hit.column}   {hit.label}")
    if len(hits) > MAX_LISTED:
        print(f"      …… 还有 {len(hits) - MAX_LISTED} 条没印（上限 {MAX_LISTED}）")


def _game_running() -> bool:
    """游戏在不在跑 —— 唯一入口 core/assets/game.py::is_game_running。"""
    from ba_mod_maker.core.assets import game as game_mod

    return bool(game_mod.is_game_running())


def _delta_document(args, target: str, policy: str, plan: list[dict[str, Any]]) -> dict[str, Any]:
    """反向 Δ 的文档（**格式 v2**，row-restore 读的就是它；字段名已冻结，别改）。"""
    order: list[str] = []
    for item in plan:
        if item["table"] not in order:
            order.append(item["table"])
    return {
        "kind": "row-delete-delta",
        "version": DELTA_VERSION,
        "source": str(args.source),
        "target": target,
        "mode": policy,
        "tables": [
            {
                "table": name,
                # 按**原表下标升序**：row-restore 照这个顺序插回去，行序才与原表一致
                # （逐字节还原靠的就是它）。
                "rows": sorted(
                    ({"Id": it["Id"], "index": it["index"], "values": it["values"]}
                     for it in plan if it["table"] == name),
                    key=lambda row: row["index"],
                ),
            }
            for name in order
        ],
    }


def run(args: argparse.Namespace) -> int:
    """`row-delete` 的处理函数（在 `__main__._build_parser` 里注册）。"""
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.edit.refs import orphan_rows, reference_closure
    from ba_mod_maker.core.errors import UserFacingError

    ids = list(dict.fromkeys(int(v) for v in args.row_id))  # 去重、保序
    policy = getattr(args, "refs", REFUSE) or REFUSE
    try:
        opened = session.open_source(args.source)
    except UserFacingError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    with opened as src:
        try:
            # ⚠ 形状照 clone-unit：Changeset 要的是 **Instance**，不是 Dataset。
            #   少了 dataset.instance(default_instance_id) 这一步，changeset.table() 会
            #   AttributeError: 'Dataset' object has no attribute 'table' —— 实测踩过。
            dataset = dataset_from_source(src)
            instance = dataset.instance(dataset.default_instance_id)
            changeset = Changeset(instance)
            table = changeset.table(args.table)
            for row_id in ids:  # 有一条找不到 ⇒ 整条命令不动
                changeset.row_of(args.table, row_id)
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

        # ---- 口径 3：删之前先说清「谁引用它」----
        targets = [(args.table, row_id) for row_id in ids]
        named = set(targets)
        try:
            # 直接引用者里要**摘掉也在被删名单上的**：同一张表里互指的（例 Turrets.ParentTurretId）
            # 两行一起删时谁也不会变成孤儿 —— 不摘就会误拒、误报。
            referred = tuple(
                h for h in orphan_rows(instance, targets) if (h.table, h.row_id) not in named
            )
            cascaded = reference_closure(instance, targets) if policy == CASCADE else ()
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

        print(f"源        : {args.source}")
        print(f"表        : {args.table}   现有 {len(table.rows):,} 行")
        print(f"引用策略  : {_POLICY_LABEL[policy]}")
        if referred:
            _print_hits("引用它的行（删了就会成孤儿）", referred)
        else:
            print("引用它的行：0 条（没有别的行指向它）")

        if policy == REFUSE and referred:
            print(
                f"拒        : {len(referred)} 行指向要删的行 —— --refuse 是默认策略，零写入",
                file=sys.stderr,
            )
            print(
                "           要连子行一起删给 --cascade；明知有引用也要删给 --force"
                "（它会把这些行逐条列出来）",
                file=sys.stderr,
            )
            return 2

        # ---- 真的要删哪些：点名 ＋ 级联牵连 ----
        doomed: list[tuple[str, int]] = list(targets)
        if policy == CASCADE and cascaded:
            known = set(targets)
            extra = [node for node in cascaded if node not in known]
            known.update(extra)
            doomed.extend(extra)
            print(f"级联牵连  : {len(extra)} 行（点名之外的传递闭包）")
            for name, count in _count_by_table(extra):
                print(f"    {name:<28} {count:>5} 行")

        # ---- 反向 Δ 的原料：整行原值 ＋ **原表下标**（都在删之前量）----
        plan: list[dict[str, Any]] = []
        before: dict[str, int] = {}
        for name, row_id in doomed:
            source_table = changeset.table(name)
            before.setdefault(name, len(source_table.rows))
            index = changeset.row_of(name, row_id)
            plan.append(
                {
                    "table": name,
                    "Id": row_id,
                    "index": index,
                    "values": dict(
                        zip(source_table.columns, source_table.rows[index], strict=True)
                    ),
                }
            )
        removed = dict(_count_by_table(doomed))

        print(
            f"要删      : {len(doomed)} 行 -> {args.table} 剩 "
            f"{len(table.rows) - removed.get(args.table, 0):,} 行"
        )
        for row_id in ids:
            index = changeset.row_of(args.table, row_id)
            values = dict(zip(table.columns, table.rows[index], strict=True))
            shown = "  ".join(
                f"{key}={values[key]!r}"
                for key in ("Name", "HUDName", "ModelFileName")
                if key in values
            )
            print(f"  Id={row_id:<8} {shown or str(values)[:90]}")
        if len(removed) > 1:
            others = "、".join(f"{n} {c} 行" for n, c in removed.items() if n != args.table)
            print("  级联那张表也删：", others)

        opened_path = str(instance.opened_file.path) if instance.opened_file else "（没有落点）"
        target = args.out or opened_path
        delta = _delta_path(target, args.delta_out)
        print(f"落点      : {target}")
        print(f"反向 Δ    : {delta}（整行原值 ＋ 原表下标；还原走 row-restore）")
        if not args.write:
            print("（⛔ 没给 --write：一个字节都没写）")
            return 0

        if args.out is None and instance.opened_file is None:
            print(
                "错误：这个来源没有「打开的那个文件」可以写回 —— 请用 --out 另存一份",
                file=sys.stderr,
            )
            return 2
        # ---- 口径 6：写前过「游戏在跑就别写」那道闸（AGENTS §6 闸 2）----
        if not getattr(args, "allow_running", False) and _game_running():
            print("错误：游戏正在运行 —— 拒写（一个字节都没写）", file=sys.stderr)
            print(
                "           退出游戏再写；确实要在游戏在跑时写就给 --allow-running。",
                file=sys.stderr,
            )
            return 2

        for item in plan:
            changeset.delete_row(item["table"], item["Id"])
        try:
            report = session.save_instance(instance, changeset.changed_tables(), target=args.out)
        except UserFacingError as exc:
            print(f"写盘失败：{exc}", file=sys.stderr)
            return 1
        delta.parent.mkdir(parents=True, exist_ok=True)
        delta.write_text(
            json.dumps(_delta_document(args, target, policy, plan), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        # ⚠ 两个数**不是一个东西**：磁盘上是压缩后的 zip，total_bytes 是未压缩载荷。
        #   只印 total_bytes 会让人以为产物膨胀了 16 倍（本命令第一版就是这么误导的）。
        on_disk = Path(str(report.path)).stat().st_size
        print(f"已写      : {report.path}")
        print(f"            （磁盘 {on_disk:,} B · 未压缩载荷 {report.total_bytes:,} B）")
        print(f"反向 Δ 已留: {delta}（{delta.stat().st_size:,} B）")

        problems = _verify(
            str(report.path),
            [item["table"] for item in plan],
            [item["Id"] for item in plan],
            before,
            removed,
        )
        if problems:
            print("写后复核：✘ " + "；".join(problems), file=sys.stderr)
            return 1
        print(f"写后复核  : ✔ 共删 {len(plan)} 行、那些 Id 都没了、每张动过的表行数正好对上")
    return 0


def _verify(
    target: str,
    tables: list[str],
    ids: list[int],
    before: dict[str, int],
    removed: dict[str, int],
) -> list[str]:
    """写后复核（⛔ 不许省）：重新打开落点逐条核。空列表 = 通过。"""
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.errors import UserFacingError

    problems: list[str] = []
    try:
        with session.open_source(target) as check_src:
            # ⚠ 同一个坑再踩一次就会静默放过：复核这条路**也要** Dataset -> Instance。
            checked = dataset_from_source(check_src)
            checked_instance = checked.instance(checked.default_instance_id)
            check_cs = Changeset(checked_instance)
            left = []
            for table_name, row_id in zip(tables, ids, strict=True):
                try:
                    check_cs.row_of(table_name, row_id)
                    left.append(f"{table_name}#{row_id}")
                except UserFacingError:
                    pass
            if left:
                problems.append(f"这几个 Id 还在：{left}")
            for name, was in before.items():
                now = len(checked_instance.table(name).rows)
                if now != was - removed[name]:
                    problems.append(f"{name} 行数不对：{now}，应为 {was - removed[name]}")
    except Exception as exc:  # noqa: BLE001 -- 复核本身失败也算不过
        problems.append(f"复核打不开落点：{type(exc).__name__}: {exc}")
    return problems


__all__ = ["CASCADE", "DELTA_VERSION", "FORCE", "MAX_LISTED", "REFUSE", "add_parser", "run"]
