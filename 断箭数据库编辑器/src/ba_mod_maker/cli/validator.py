"""`validate` 子命令：跑规则书里的全部规则，按严重度汇总（零 Qt）。

条数**不写死**在文案里：它随 `core/rules/rulebook.json` 重生成而变，
运行时读 `RuleEngine().rule_count`（JSON 文档里的 `rules` 字段就是它）。

## 退出码（也写在 `--help` 里）

| 码 | 什么时候 |
|---|---|
| 0 | 跑完了，**没有 error** |
| 1 | 跑完了，**有 error** |
| 2 | 打不开源 / 命令没跑完（路径不对、文件坏了、规则书读不了） |

warnng / info **不影响退出码** —— 原版 52085 自己就带 699 warning / 1036 info，
按它们判失败等于这个命令对谁都是红的（引擎模块 docstring 里那条「狼来了」）。

## `--full` 管什么（与旧产品那个同名开关对齐）

旧产品 `ba_db_tool.py validate --full` 是「连原版既有的基线问题一起显示」——
原版一打开就 1700+ 条命中，不筛没法看。新产品**没有第二份基线可比**（基线是界面
「打开那一刻」跑的那份，CLI 手上只有一份数据），所以等价的口径按**严重度**分档：

- 默认：**计数按 severity 全打**（一个不藏）+ 只展开 **error** 的明细 + 一句
  「另有 warning N / info M，加 --full 展开」；
- `--full`：连 warning / info 的明细与「按类型汇总」一起展开。

实测（2026-10-01，原版 52085）：`error 0 · warning 699 · info 1036` ——
原版既有那一大堆恰恰全落在 warning / info，与旧产品要筛掉的是同一批东西。
所以「默认输出干净」是有据的，不是把问题藏起来。

## JSON

`--json` 时 stdout 只有一个文档（人读那份走 stderr，见 `cli/emit.py`）。
文档里 `violations` 是**真数**（按严重度的违规条数），`findings` 每条规则最多带
`sample_limit` 条例子（引擎的 `MAX_SAMPLES`）；`omitted` 说明哪些严重度因为在
非 `--full` 模式下没展开、各自有多少条 —— 机器要全量就再加 `--full`。
"""

from __future__ import annotations

import argparse
from typing import Any

from ba_mod_maker.cli.emit import Emitter, pad
from ba_mod_maker.core.errors import BaModMakerError
from ba_mod_maker.core.rules.engine import (
    KIND_LABELS,
    MAX_SAMPLES,
    RuleOutcome,
    ValidationReport,
)

#: 退出码。**产品里只有这一处**，`__main__.py` 的帮助文本也引这几个名字。
EXIT_OK = 0
EXIT_FOUND_ERRORS = 1
EXIT_UNREADABLE = 2

#: 严重度从重到轻 —— 输出顺序与 `omitted` 的顺序都用它。
SEVERITIES = ("error", "warning", "info")

_DESCRIPTION = """跑一遍规则书里的全部规则，按严重度汇总。

退出码：
  0  没有 error
  1  有 error
  2  打不开源（路径不对 / 文件坏了 / 规则书读不了）

默认只展开 error 的明细（计数仍然按严重度全打）；--full 连 warning / info 一起展开。
"""

_EPILOG = r"""认哪些源：
  DataBaseCompiled/（或它的某个实例目录）   BA_Mod.zip（老名字 db.zip 也认）
  DataBaseCompiled.json                    data.unity3d / 游戏根目录

例：
  ba-mod-maker validate ..\DataBaseCompiled\52085
  ba-mod-maker validate BA_Mod.zip --json
"""


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """注册 `validate` 子命令（`__main__.py` 只调这一句）。"""
    parser = sub.add_parser(
        "validate",
        help="跑规则书里的全部规则校验一份数据源（退出码 0 无 error / 1 有 error / 2 打不开源）",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        help="数据源：DataBaseCompiled 目录（或它的实例目录）/ BA_Mod.zip / "
        "DataBaseCompiled.json / data.unity3d / 游戏根目录",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="只输出一个 JSON 文档（人读那份改走 stderr）",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="连 warning / info 的明细与「按类型汇总」一起展开",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """跑一遍。返回退出码（0 / 1 / 2）。"""
    from ba_mod_maker.cli.source import load

    full = bool(args.full)
    emitter = Emitter(json_mode=bool(args.json_out))
    try:
        with load(args.source) as loaded:
            report = _validate(loaded.instance)
            document = _document(loaded, report, full=full)
            errors = int(report.counts.get("error", 0))
            _report(emitter, loaded, report, full=full)
    except BaModMakerError as exc:
        emitter.failure(exc.message, hint=exc.hint)
        return EXIT_UNREADABLE
    emitter.document(document)
    return EXIT_FOUND_ERRORS if errors else EXIT_OK


def _validate(instance: Any) -> ValidationReport:
    """跑规则书。**只调**引擎，一条规则内容都不在这里（规则住在 rulebook.json）。"""
    from ba_mod_maker.core.rules.engine import RuleEngine

    return RuleEngine().validate(instance)


# --------------------------------------------------------------------------- #
# 人读
# --------------------------------------------------------------------------- #


def _report(emitter: Emitter, loaded: Any, report: ValidationReport, *, full: bool) -> None:
    counts = report.counts
    passed = sum(1 for outcome in report.outcomes if outcome.ok)
    emitter.field("源", loaded.path)
    emitter.field("类型", loaded.label)
    emitter.field("实例", loaded.path_id or "（无 pathID）")
    emitter.field("表 / 行", f"{loaded.tables} 张 / {loaded.rows:,} 行")
    emitter.field("规则", f"{len(report.outcomes)} 条 · 通过 {passed} · 违规 {len(report.failed)}")
    emitter.field("严重度", _severity_text(counts))

    if full:
        _report_kinds(emitter, report)

    shown = _shown(report, full=full)
    for outcome in shown:
        _report_rule(emitter, outcome)

    if not shown:
        if report.is_clean:
            emitter.line(f"✓ {len(report.outcomes)} 条规则全部通过（没有 error / warning / info）")
        elif counts.get("error", 0) == 0:
            emitter.line("✓ 没有 error")

    hidden = [name for name in SEVERITIES if not full and name != "error" and counts.get(name)]
    if hidden:
        detail = " / ".join(f"{name} {counts[name]}" for name in hidden)
        emitter.line(f"（另有 {detail} 未展开 —— 原版既有的大多落在这一档；加 --full 看明细）")


def _severity_text(counts: dict[str, int]) -> str:
    return " · ".join(f"{name} {counts.get(name, 0)}" for name in SEVERITIES)


def _report_kinds(emitter: Emitter, report: ValidationReport) -> None:
    emitter.blank()
    emitter.line("按类型：")
    for summary in report.by_kind():
        tail = (
            "全部命中"
            if summary.all_passed
            else f"通过 {summary.passed} · 违规 {summary.violations}"
        )
        # 中文标签的宽度得按**显示列**算（`str.ljust` / `:<10` 数的是字符个数，中文一多就歪）。
        emitter.line(f"  {pad(summary.label, 10)}{summary.total:>3} 条 · {tail}")


def _report_rule(emitter: Emitter, outcome: RuleOutcome) -> None:
    label = KIND_LABELS.get(outcome.kind, outcome.kind)
    table = outcome.table or "（跨表）"
    emitter.blank()
    emitter.line(
        f"[{outcome.severity}] {outcome.rule_id} · {table} · {outcome.violations} 条 · {label}"
    )
    emitter.items([finding.message for finding in outcome.samples], indent=4)
    if outcome.violations > len(outcome.samples):
        emitter.line(
            f"    （只列前 {len(outcome.samples)} 条，这一条规则实际违规 {outcome.violations} 条）"
        )


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #


def _shown(report: ValidationReport, *, full: bool) -> list[RuleOutcome]:
    """要**展开明细**的那几条规则：默认只有 error。

    原版既有那一大堆（实测 699 warning / 1036 info）落在另外两档 —— 这正是
    「默认输出干净」的全部机制；计数照打，所以不算把问题藏起来。
    """
    return [outcome for outcome in report.failed if full or outcome.severity == "error"]


def _document(loaded: Any, report: ValidationReport, *, full: bool) -> dict[str, Any]:
    counts = report.counts
    shown = _shown(report, full=full)
    omitted = {
        name: counts.get(name, 0)
        for name in SEVERITIES
        if name != "error" and not full and counts.get(name)
    }
    return {
        "source": str(loaded.path),
        "kind": loaded.kind,
        "label": loaded.label,
        "path_id": loaded.path_id,
        "tables": loaded.tables,
        "rows": loaded.rows,
        "rules": len(report.outcomes),
        "passed": sum(1 for outcome in report.outcomes if outcome.ok),
        "failed_rules": len(report.failed),
        # clean = 一条违规都没有（error / warning / info 全算）；退出码只看 errors。
        "clean": report.is_clean,
        "errors": int(counts.get("error", 0)),
        "violations": {name: counts.get(name, 0) for name in SEVERITIES},
        "omitted": omitted,
        "full": full,
        "sample_limit": MAX_SAMPLES,
        "by_kind": [
            {
                "kind": summary.kind,
                "label": summary.label,
                "total": summary.total,
                "passed": summary.passed,
                "violations": summary.violations,
                "severity": summary.severity,
            }
            for summary in report.by_kind()
        ],
        "findings": [_finding_document(outcome) for outcome in shown],
    }


def _finding_document(outcome: RuleOutcome) -> dict[str, Any]:
    return {
        "rule_id": outcome.rule_id,
        "kind": outcome.kind,
        "severity": outcome.severity,
        "table": outcome.table,
        "violations": outcome.violations,
        "samples": [
            {
                "message": finding.message,
                "row_ids": list(finding.row_ids),
                "unit_ids": list(finding.unit_ids),
            }
            for finding in outcome.samples
        ],
    }


__all__ = ["EXIT_FOUND_ERRORS", "EXIT_OK", "EXIT_UNREADABLE", "SEVERITIES", "add_parser", "run"]
