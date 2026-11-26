"""规则书与校验引擎（零 Qt）。

- `engine` —— 规则书的执行器（条数见 `rulebook.json::rule_count`，别写死在这里）。
- `rulebook.json` —— **产品自带**的规则数据，由 `tools/gen_rulebook.py` 派生，不要手改。
- `range_bounds.json` —— 数值区间参照（带出处），那两类区间规则**唯一**的输入。
- `attribution` —— 把命中按单位归好（单位卡片上的角标要用）。
"""

from ba_mod_maker.core.rules.attribution import findings_by_unit, unit_ids_for
from ba_mod_maker.core.rules.engine import (
    Finding,
    KindSummary,
    RuleDelta,
    RuleEngine,
    RuleOutcome,
    ValidationReport,
    diff_reports,
    load_rulebook,
)

__all__ = [
    "Finding",
    "KindSummary",
    "RuleDelta",
    "RuleEngine",
    "RuleOutcome",
    "ValidationReport",
    "diff_reports",
    "findings_by_unit",
    "load_rulebook",
    "unit_ids_for",
]
