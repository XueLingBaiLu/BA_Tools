"""基线差集：**只有本轮新冒出来的命中才算数**（零 Qt）。

用户口径（本轮 15 条硬约定第 11 条）：**原版既有违规不报、不计数**。
打开时基线就有 699 warning / 1036 info（实测 52085），而且
**540 个单位里 28 个一打开就带命中** —— BTR-82A 自己带 1 条
（req.SquadMembers：载具没有班组成员，原版形态）。

照"有命中就画角标"做，每个载具一打开就是红的。所以判据只能是**差集**：
同一个单位，改动后比改动前多出来的那几条。

## 为什么不用 diff_reports

core/rules/engine.py::diff_reports 是**按规则条数**比的（`baseline: 3 -> current: 5`），
它回答"哪条规则变多了"，不回答"哪个单位身上多了哪几条"。
立绘上的角标要的是后者，所以这里按 **Finding** 逐个做集合差
（Finding 是 frozen dataclass，可哈希）。
"""

from __future__ import annotations

from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.rules.attribution import findings_by_unit
from ba_mod_maker.core.rules.engine import Finding, ValidationReport


def new_findings(
    baseline: tuple[Finding, ...], current: tuple[Finding, ...]
) -> tuple[Finding, ...]:
    """`current` 里比 `baseline` 多的那些（保持 current 的顺序）。

    **按对象相等去重，不按 rule_id 去重**：同一条规则可以在这个单位身上
    报好几处（比如两个乘客都坐不下），少报一处等于漏报。
    """
    known = set(baseline)
    return tuple(finding for finding in current if finding not in known)


def unit_finding_delta(
    instance: Instance,
    baseline: ValidationReport,
    current: ValidationReport,
) -> dict[int, tuple[Finding, ...]]:
    """单位 Id -> **新增**的命中。没有新增的单位不出现在结果里。

    基线那一批还在（语义上应当还在 —— 用户没改那部分），只是不算数：
    它们不是这一轮改出来的。
    """
    before = findings_by_unit(instance, baseline)
    after = findings_by_unit(instance, current)
    out: dict[int, tuple[Finding, ...]] = {}
    for unit_id, findings in after.items():
        fresh = new_findings(before.get(unit_id, ()), findings)
        if fresh:
            out[unit_id] = fresh
    return out


def report_counts(report: ValidationReport) -> dict[str, int]:
    """按严重度计数（`ValidationReport.counts` 的薄封装，给状态栏用）。"""
    return dict(report.counts)


__all__ = ["new_findings", "report_counts", "unit_finding_delta"]
