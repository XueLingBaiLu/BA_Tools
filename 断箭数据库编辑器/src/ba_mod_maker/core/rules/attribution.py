"""把校验命中归到**单位**头上（零 Qt）。

为什么要这一步：RuleEngine 的命中是按表 + 行 Id 报的（Finding.table /
Finding.row_ids），而卡片是按单位说话的。中间差一次反查。

反查规则只有三条，**不做猜测**：

1. 表就是 Units 时，row_ids 就是单位 Id；
2. 该表有 UnitId 列（关联行，如 SquadMembers / WeaponAmmunitions），直接读；
3. 其它表（Weapons / Sensors 这类模板表）归不到具体单位，返回时跳过。
   宁可少标一个角标，也不要把某个武器悬空的外键扣到随便一个单位头上。
"""

from __future__ import annotations

from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.rules.engine import Finding, ValidationReport


def unit_ids_for(instance: Instance, finding: Finding) -> tuple[int, ...]:
    """一条命中涉及哪些单位（归不到就返回空元组）。"""
    # 缺失型命中自己带了单位（某单位没有某张关联表的行 —— 没有行可查）。
    if finding.unit_ids:
        return tuple(finding.unit_ids)
    if finding.table == "Units":
        return tuple(finding.row_ids)
    try:
        table = instance.table(finding.table)
    except KeyError:
        return ()
    if "UnitId" not in table.columns:
        return ()
    index = table.column_index("UnitId")
    id_index = table.column_index("Id")
    wanted = set(finding.row_ids)
    found: list[int] = []
    for row in table.rows:
        unit_id = row[index] if index < len(row) else None
        if not isinstance(unit_id, int) or isinstance(unit_id, bool):
            continue
        row_id = row[id_index] if id_index < len(row) else None
        # row_ids 为空 = 这条规则没给具体行（汇总型），那就整表都算
        if wanted and row_id not in wanted:
            continue
        if unit_id not in found:
            found.append(unit_id)
    return tuple(found)


def findings_by_unit(
    instance: Instance, report: ValidationReport
) -> dict[int, tuple[Finding, ...]]:
    """单位 Id 到落在他头上的命中（保持规则引擎给的顺序）。"""
    buckets: dict[int, list[Finding]] = {}
    for finding in report.findings:
        for unit_id in unit_ids_for(instance, finding):
            buckets.setdefault(unit_id, []).append(finding)
    return {unit_id: tuple(items) for unit_id, items in buckets.items()}


__all__ = ["findings_by_unit", "unit_ids_for"]
