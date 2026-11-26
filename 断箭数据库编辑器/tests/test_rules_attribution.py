"""校验命中归到单位头上的测试（零 Qt，用真实 DataBaseCompiled）。

这一层只做一件事：把 RuleEngine 的表级命中反查成单位级。
**归不到就返回空**，不许猜 —— 这是它唯一容易出错的地方。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.rules import RuleEngine
from ba_mod_maker.core.rules.attribution import findings_by_unit, unit_ids_for
from ba_mod_maker.core.rules.engine import Finding

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


@pytest.fixture(scope="module")
def report(instance):
    return RuleEngine().validate(instance)


def test_units_table_rows_are_unit_ids(instance) -> None:
    finding = Finding(rule_id="r", severity="error", table="Units", message="x", row_ids=(7, 9))
    assert unit_ids_for(instance, finding) == (7, 9)


def test_link_rows_are_looked_up_through_unit_id(instance) -> None:
    """关联行（SquadMembers）要按行 Id 查出 UnitId，而不是把行 Id 当单位 Id。"""
    table = instance.table("SquadMembers")
    columns = {name: i for i, name in enumerate(table.columns)}
    row = next(r for r in table.rows if r[columns["UnitId"]] == 645)
    finding = Finding(
        rule_id="r",
        severity="warning",
        table="SquadMembers",
        message="x",
        row_ids=(row[columns["Id"]],),
    )
    assert unit_ids_for(instance, finding) == (645,)


def test_template_tables_are_not_attributed(instance) -> None:
    """武器/传感器这类模板表没有 UnitId —— 归不到就返回空，不许猜。"""
    for table in ("Weapons", "Sensors", "Armors"):
        finding = Finding(rule_id="r", severity="warning", table=table, message="x", row_ids=(1,))
        assert unit_ids_for(instance, finding) == (), f"{table} 不该被归到某个单位头上"


def test_summary_findings_cover_the_whole_table(instance) -> None:
    """row_ids 为空的汇总型命中：整表都要算上（不是"没人中"）。"""
    finding = Finding(rule_id="r", severity="info", table="UnitArmors", message="x")
    assert len(unit_ids_for(instance, finding)) > 100


def test_real_report_is_attributed_to_real_units(instance, report) -> None:
    by_unit = findings_by_unit(instance, report)
    units_table = instance.table("Units")
    id_index = units_table.column_index("Id")
    units = {row[id_index] for row in units_table.rows}
    assert by_unit, "一条都没归上，反查多半断了"
    assert set(by_unit) <= units
    for items in by_unit.values():
        assert items, "空列表不该出现在映射里"
        for finding in items:
            assert finding in report.findings


def test_explicit_unit_ids_win_over_row_lookup(instance) -> None:
    """缺失型命中自己带了单位：必须按 unit_ids 归，别拿 row_ids 当行 Id 去查。

    实踩：Armor ... 那条规则报的是 table=UnitArmors、row_ids=(单位 Id,)，
    而 UnitArmors 里恰好也有同号的行 —— 按行 Id 反查会把命中归到**另一个**单位头上
    （495 被归成了 327，角标就挂错了人）。
    """
    finding = Finding(
        rule_id="r",
        severity="warning",
        table="UnitArmors",
        message="x",
        row_ids=(495,),
        unit_ids=(327,),
    )
    assert unit_ids_for(instance, finding) == (327,)


def test_real_missing_type_findings_land_on_the_unit_in_the_message(instance, report) -> None:
    """真实报告里的缺失型命中：归出来的单位必须就是消息里写的那个。"""
    missing_type = [f for f in report.findings if f.unit_ids]
    assert missing_type, "这一版数据里一条缺失型命中都没有？先把样本找回来"
    for finding in missing_type:
        units = unit_ids_for(instance, finding)
        assert units == finding.unit_ids
        for unit_id in units:
            assert f"单位 Id {unit_id}" in finding.message, finding.message


def test_attribution_keeps_the_engine_order(instance, report) -> None:
    by_unit = findings_by_unit(instance, report)
    for items in by_unit.values():
        positions = [report.findings.index(f) for f in items]
        assert positions == sorted(positions)
