r"""row-restore 子命令：**把 row-delete 删掉的行按原下标加回去**（逆操作）。

    ba-mod-maker row-restore <源> <delta.json> [--out <新文件>] [--write] [--allow-running]

例（把上一次删掉的 3 行加回去）：

    ba-mod-maker row-restore "D:\...\clean.zip" "D:\...\clean.zip.row-delete-delta.json"
    # 默认干跑：只印计划，一个字节都不写

    ba-mod-maker row-restore clean.zip clean.zip.row-delete-delta.json --out back.zip --write

## 为什么要有它

AGENTS.md §0.10 定的产品操作集是 **新增 / 删除 / 修改**，而"**撤销一次删除**"从前
**没有入口** —— row-delete 会强制留一份反向 Δ（<落点>.row-delete-delta.json），
但要把行加回去得手写脚本。这一条补的就是那个逆操作：
**Δ 是产品的产物，还原也必须是产品的入口**（kb/19 的「唯一入口」）。

## 六条硬口径

1. **落点是显式的**：不给 --out ⇒ 落点就是源本身，但**默认干跑**（要真写回得 --write）；
   给了 --out（与源不同）就是"另存一份"，与 row-delete／object 那条路同口径。
2. **⛔ 默认干跑**：没有 --write 时一个字节都不写（连 --out 都不创建）。
3. **插入按 index 升序逐条来**：index 量的是**删之前那一版数组**的 0 起下标；
   升序插入时前一次插入不会挪动后面要用的下标 ⇒ 插完行序与原表一致（这是逐字节还原的前提）。
   ⛔ 降序插出来的是"行数对、行序错"，用例里钉着。
4. **往返逐字节**：A →（row-delete）→ B →（row-restore）→ C 要求 **sha256(C) == sha256(A)
   且长度相等**。唯一会让它不成立的是 zip 成员时间戳（写入器从前默认写"现在"）——
   那一条已经**在写包那一层**治掉了：plan_package 在没显式给 stamps 时自动沿用
   「你打开的那个 zip」的成员时间戳（core/edit/package.py::_path_stamps）。
   所以本文件**不碰时间戳**：⛔ 别在这里再读一份（"每个入口各自记得传"正是会漏的那种设计）。
5. **向后兼容 v1 Δ**：上一轮留的 v1 是 {"table": …, "rows": [{"Id": …, "values": {…}}]}
   （顶层单表、行里**没有** index）。那种 Δ 只能**追加到末尾** ⇒ 行序变了，
   因此会**明印一句「这次不是逐字节还原」** —— ⛔ 不许静默降级。
6. **写后复核**（只有真写才做）：重开落点逐个 Id 都在、每张表行数 = Δ 声明的那个数。

退出码：0 完成（含干跑）· 1 写盘失败 / 写后复核不过 ·
2 用不了（打不开源 / Δ 不合法 / 该 Id 还在表里 / 下标越界 / 游戏在跑 / 没落点）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: 反向 Δ 的格式标识与版本。**与 cli/row_delete.py 的 DELTA_VERSION 同一个数** ——
#: 那份是写方，本文件是读方；两边对不上就会在"还原不了"的时候才被发现。
DELTA_KIND = "row-delete-delta"
DELTA_VERSION = 2


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """注册 row-restore（⛔ 实现只有下面 run 一处，这里只挂参数）。"""
    p = sub.add_parser(
        "row-restore",
        help="把 row-delete 删掉的行按原下标加回去（默认干跑；与 row-delete 构成逐字节往返）",
        epilog=(
            "落点口径：\n"
            "  给了 --out（与源不同）  -> 另存一份，写那里\n"
            "  没给 --out             -> 落点就是源本身，但**默认干跑**，要写回得 --write\n"
            "\n"
            "退出码：\n"
            "  0  完成（含干跑）\n"
            "  1  写盘失败 / 写后复核不过\n"
            "  2  用不了（打不开源 / Δ 不合法 / 该 Id 还在表里 / 下标越界 /\n"
            "     游戏在跑而没给 --allow-running）\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="被删过的那份包（BA_Mod.zip / DataBaseCompiled.json）")
    p.add_argument("delta", help="row-delete 留的反向 Δ（<落点>.row-delete-delta.json）")
    p.add_argument("--out", help="还原结果另存到这里；省略 = 落点就是源本身（默认干跑）")
    p.add_argument("--write", action="store_true", help="真写（⛔ 不给就只出计划）")
    p.add_argument(
        "--allow-running",
        action="store_true",
        help="允许游戏在跑时写入（默认：游戏在跑就拒，一个字节都不写）",
    )
    return p


def _game_running() -> bool:
    """游戏在不在跑 —— 唯一入口 core/assets/game.py::is_game_running（与 row-delete 同一把）。"""
    from ba_mod_maker.core.assets import game as game_mod

    return bool(game_mod.is_game_running())


def _normalize_delta(raw: Any) -> tuple[bool, list[tuple[str, list[dict[str, Any]]]]]:
    """把 Δ 文档收成一个形状：(是不是 v2, [(表名, [行, …]), …])。

    - v2（冻结格式）：{"kind","version","source","target","mode","tables":[{"table","rows"}]}
      —— 行是 {"Id","index","values"}；
    - v1（上一轮留的）：{"table": …, "rows": [{"Id","values"}]} —— 顶层单表、行里没有 index。

    ⛔ 认不出来的形状**当场抛**（ValueError），别猜、别静默当成空 Δ
    —— "以为还原了、其实一行没加"是最坏的那种静默。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"Δ 的顶层必须是对象，实际是 {type(raw).__name__}")
    if "tables" in raw:
        if raw.get("kind") != DELTA_KIND:
            raise ValueError(f"Δ 的 kind 不是 {DELTA_KIND}（实际 {raw.get('kind')!r}）")
        version = raw.get("version")
        if not isinstance(version, int) or version > DELTA_VERSION:
            raise ValueError(f"Δ 的 version={version!r} 我读不了（本命令认 <= {DELTA_VERSION}）")
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        for item in raw["tables"]:
            if not isinstance(item, dict) or not isinstance(item.get("table"), str):
                raise ValueError(f"tables[] 里这一项没有表名：{item!r}")
            rows = item.get("rows")
            if not isinstance(rows, list):
                raise ValueError(f"表 {item['table']} 的 rows 不是数组")
            groups.append((item["table"], rows))
        return True, groups
    if "table" in raw and "rows" in raw:
        if not isinstance(raw["table"], str) or not isinstance(raw["rows"], list):
            raise ValueError("v1 Δ 的 table / rows 形状不对")
        return False, [(raw["table"], raw["rows"])]
    raise ValueError("这个 JSON 既不是 v2（没有 tables）也不是 v1（没有 table+rows）")


def _col_values(table, column: str) -> set:
    """一列的全部值（列名不在表里就返回空集 —— 24 张表都有 Id，这是防御）。"""
    if column not in table.columns:
        return set()
    return {row[table.columns.index(column)] for row in table.rows}


def _row_problem(
    table: str, columns: tuple, item: Any, *, v2: bool, rows_now: int
) -> tuple[str | None, int, Any, dict]:
    """一条 Δ 行值不值得插：返回 (问题, index, Id, values)。

    index 的来源分两路（**别拿 -1 当哨兵** —— 那会让 v1 的每一行都判越界，
    实测过：v1 的 Δ 一条都加不回去）：

    - v2：行里**必须**有 int index（量的是删之前那一版数组的下标）；
    - v1：行里没有 index 这回事，**补成 rows_now**（= 追加到末尾）。
      加了 `gone` 的话每一条都落在同一个位置上（先插的在后面），
      与"v1 一律追加到末尾"的口径一致。
    """
    if not isinstance(item, dict):
        return (f"{table} 的 rows[] 里这一项不是对象：{item!r}", rows_now, None, {})
    values = item.get("values")
    index = item.get("index")
    row_id = item.get("Id")
    if not isinstance(values, dict):
        return (f"{table}#{row_id} 的 values 不是对象", rows_now, row_id, {})
    if set(values) != set(columns):
        return (
            f"{table}#{row_id} 的列与表对不上（Δ {len(values)} 列 / 表 {len(columns)} 列）",
            rows_now,
            row_id,
            values,
        )
    if index is None:
        if v2:
            return (f"{table}#{row_id} 没有 index 字段（v1 Δ 与 v2 混用？）", rows_now, row_id, {})
        return (None, rows_now, row_id, values)
    if not isinstance(index, int) or isinstance(index, bool):
        return (f"{table}#{row_id} 的 index={index!r} 不是整数", rows_now, row_id, {})
    return (None, index, row_id, values)


def _validate(cs, groups, *, v2: bool, problems: list[str]) -> tuple[list[tuple], dict[str, int]]:
    """逐条核 Δ 能不能插：返回 (要插的条目, 每表还原后行数)。

    校验**全部做完再动手**：只要有一条不成立，这条命令就**一个字节都不写**
    （与 row-delete 的"整条命令不动"同一个口径）。
    """
    expected: dict[str, int] = {}
    pending: list[tuple] = []
    for table, rows in groups:
        try:
            tbl = cs.table(table)
        except Exception as exc:  # noqa: BLE001 -- 表名不存在，报人话
            problems.append(f"Δ 里的表 {table} 打不开：{exc}")
            continue
        columns = tuple(tbl.columns)
        present = _col_values(tbl, "Id")
        gone = 0
        for item in rows:
            problem, index, row_id, values = _row_problem(
                table, columns, item, v2=v2, rows_now=len(tbl.rows) + gone
            )
            if problem:
                problems.append(problem)
                continue
            if not isinstance(row_id, int) or isinstance(row_id, bool):
                problems.append(f"{table} 的 Id={row_id!r} 不是整数")
                continue
            if row_id in present:
                problems.append(f"{table}#{row_id} 还在表里 —— 这份 Δ 对应的删除没有真的生效？")
                continue
            if not 0 <= index <= len(tbl.rows) + gone:
                # 越界在 v2 里意味着 Δ 与这份包**不是一对**（拿错 Δ 会静默加错位置）。
                # v1 的 index 是上面补出来的，恒在这个范围内 —— 走到这儿一定是 v2。
                problems.append(
                    f"{table}#{row_id} 的原下标 {index} 越界（现有 {len(tbl.rows)} 行，"
                    f"可用 0..{len(tbl.rows) + gone}）—— Δ 与这份包是一对吗？"
                )
                continue
            gone += 1
            pending.append((table, index, row_id, values, bool(v2)))
        if gone:
            expected[table] = len(tbl.rows) + gone
    return pending, expected


def _verification_problems(
    target: str, ids: list[tuple[str, Any]], expected: dict[str, int]
) -> list[str]:
    """写后复核（⛔ 不许省）：重开落点逐个 Id 都应在、每张表行数 = Δ 声明的那个数。"""
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.errors import UserFacingError

    problems: list[str] = []
    try:
        with session.open_source(target) as check_src:
            # ⚠ 复核这条路**也要** Dataset -> Instance（row-delete 在这里踩过一次）。
            checked = dataset_from_source(check_src)
            inst = checked.instance(checked.default_instance_id)
            for table, count in expected.items():
                got = len(inst.table(table).rows)
                if got != count:
                    problems.append(f"{table} 行数不对：{got}，应为 {count}")
            for table, row_id in ids:
                if row_id not in _col_values(inst.table(table), "Id"):
                    problems.append(f"{table}#{row_id} 没插进去")
    except UserFacingError as exc:
        problems.append(f"复核打不开落点：{exc}")
    except Exception as exc:  # noqa: BLE001 -- 复核本身失败也算不过
        problems.append(f"复核打不开落点：{type(exc).__name__}: {exc}")
    return problems


def run(args: argparse.Namespace) -> int:
    """row-restore 的处理函数（在 __main__._build_parser 里注册）。"""
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.errors import UserFacingError

    # ---- ① Δ 先读进来（读不动就退出 2，一个字节都不写）----
    delta_path = Path(args.delta)
    try:
        raw = json.loads(delta_path.read_text(encoding="utf-8"))
        is_v2, groups = _normalize_delta(raw)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误：读不了反向 Δ（{delta_path}）：{exc}", file=sys.stderr)
        return 2
    if not is_v2:
        print(
            "⚠ 这是 v1 的反向 Δ（没有 index）：行只能**追加到末尾**"
            " ⇒ **这次不是逐字节还原**（行序与原表不同）",
            file=sys.stderr,
        )
    total = sum(len(rows) for _, rows in groups)
    if total == 0:
        print(f"错误：这份 Δ 里一行都没有（{delta_path}）", file=sys.stderr)
        return 2

    # ---- ② 打开源 ----
    try:
        opened = session.open_source(args.source)
    except UserFacingError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    with opened as src:
        try:
            dataset = dataset_from_source(src)
            instance = dataset.instance(dataset.default_instance_id)
            changeset = Changeset(instance)
        except UserFacingError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

        # ---- ③ 校验（全做完再动手）----
        problems: list[str] = []
        pending, expected = _validate(changeset, groups, v2=is_v2, problems=problems)
        if problems:
            print("错误：这份 Δ 加不回去 ——", file=sys.stderr)
            for line in problems[:20]:
                print(f"  {line}", file=sys.stderr)
            if len(problems) > 20:
                print(f"  …… 还有 {len(problems) - 20} 条（上限 20）", file=sys.stderr)
            print("（一个字节都没写）", file=sys.stderr)
            return 2

        opened_path = str(instance.opened_file.path) if instance.opened_file else "（没有落点）"
        if args.out is None and instance.opened_file is None:
            print(
                "错误：这个来源没有「打开的那个文件」可以写回 —— 请用 --out 另存一份",
                file=sys.stderr,
            )
            return 2
        target = args.out or opened_path

        print(f"源        : {args.source}")
        print(f"反向 Δ    : {delta_path}（格式 v{DELTA_VERSION if is_v2 else 1}）")
        for table, rows in groups:
            before = len(changeset.table(table).rows)
            print(
                f"表        : {table}   现有 {before:,} 行 -> 加回 {len(rows)} 行"
                f" -> {expected[table]:,} 行"
            )
            for item in rows:
                index = item.get("index", "（无 index：追加到末尾）")
                values = item.get("values") or {}
                shown = "  ".join(
                    f"{key}={values[key]!r}"
                    for key in ("Name", "HUDName", "ModelFileName")
                    if key in values
                )
                print(f"  Id={item.get('Id')!r:<8} 原下标={index}  {shown or str(values)[:70]}")
        print(f"落点      : {target}")
        if not args.write:
            print("（⛔ 没给 --write：一个字节都没写）")
            return 0

        # ---- ④ 写前过「游戏在跑就别写」那道闸（AGENTS §6 闸 2）----
        if not getattr(args, "allow_running", False) and _game_running():
            print("错误：游戏正在运行 —— 拒写（一个字节都没写）", file=sys.stderr)
            print(
                "           退出游戏再写；确实要在游戏在跑时写就给 --allow-running。",
                file=sys.stderr,
            )
            return 2

        # ---- ⑤ 插入：**按 index 升序**逐条（v1 的 index 视为末尾）----
        ordered = sorted(pending, key=lambda entry: (entry[0], entry[1]))
        try:
            for table, index, _row_id, values, _is_v2 in ordered:
                changeset.insert_row(table, values, index=index)
        except UserFacingError as exc:
            print(f"错误：插行失败：{exc}", file=sys.stderr)
            return 2

        # 时间戳**不在这里传**：写包那一层（plan_package）在没显式给 stamps 时
        # 自动沿用"你打开的那个 zip"的成员时间戳 —— 这是"与源包逐字节相同"的唯一前提，
        # 也是它唯一该在的地方（见模块 docstring 口径 4）。
        try:
            report = session.save_instance(
                instance, changeset.changed_tables(), target=args.out
            )
        except UserFacingError as exc:
            print(f"写盘失败：{exc}", file=sys.stderr)
            return 1
        on_disk = Path(str(report.path)).stat().st_size
        print(f"已写      : {report.path}")
        print(f"            （磁盘 {on_disk:,} B · 未压缩载荷 {report.total_bytes:,} B）")

        # ---- ⑥ 写后复核 ----
        problems = _verification_problems(
            str(report.path),
            [(table, row_id) for table, _index, row_id, _values, _is_v2 in ordered],
            expected,
        )
        if problems:
            print("写后复核：✘ " + "；".join(problems), file=sys.stderr)
            return 1
        print(f"写后复核  : ✔ 加回 {len(ordered)} 行、它们的 Id 都回来了、每张表行数正好对上")
    return 0


__all__ = ["DELTA_KIND", "DELTA_VERSION", "add_parser", "run"]
