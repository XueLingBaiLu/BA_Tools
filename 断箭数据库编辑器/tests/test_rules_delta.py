"""基线差集：**原版既有违规不报、不计数**（零 Qt）。

这条口径是"立绘上只有有问题才画角标"能不能成立的关键 ——
打开时基线有 699 warning / 1036 info，540 个单位里 28 个一打开就带命中。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.rules.delta import new_findings, unit_finding_delta
from ba_mod_maker.core.rules.engine import RuleEngine

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


@pytest.fixture(scope="module")
def engine():
    return RuleEngine()


def test_opening_a_unit_reports_nothing(instance, engine) -> None:
    """**刚打开时一个角标都不该有** —— 哪怕这个单位原版就带着命中。

    实测：BTR-82A（Id 1）基线里就有 1 条（req.SquadMembers：载具没有班组成员）。
    照"有命中就画"做，每个载具一打开就是红的。
    """
    baseline = engine.validate(instance)
    delta = unit_finding_delta(instance, baseline, engine.validate(instance))
    assert delta == {}


def test_a_real_change_shows_up_and_reverting_clears_it(instance, engine) -> None:
    """把座位数改成 2 -> 冒 5 条；改回 8 -> 一条不剩。"""
    baseline = engine.validate(instance)
    units = instance.table("Units")
    id_index = units.column_index("Id")
    column = units.column_index("InfantrySlots")
    row = next(r for r in units.rows if r[id_index] == 1)
    original = row[column]

    row[column] = 2
    changed = unit_finding_delta(instance, baseline, engine.validate(instance))
    assert set(changed) == {1}
    assert len(changed[1]) == 5
    assert {finding.rule_id for finding in changed[1]} == {"inv.transport.capacity"}

    row[column] = original
    assert unit_finding_delta(instance, baseline, engine.validate(instance)) == {}


def test_delta_is_per_unit_not_global(instance, engine) -> None:
    """改动只算在**被改的那个单位**头上。"""
    baseline = engine.validate(instance)
    units = instance.table("Units")
    id_index = units.column_index("Id")
    column = units.column_index("InfantrySlots")
    row = next(r for r in units.rows if r[id_index] == 1)
    original = row[column]
    row[column] = 1
    try:
        delta = unit_finding_delta(instance, baseline, engine.validate(instance))
        assert 1 in delta
        assert all(unit_id == 1 for unit_id in delta)
    finally:
        row[column] = original


def test_new_findings_keeps_duplicates_of_the_same_rule() -> None:
    """同一条规则可以在一个单位上报好几处（两个乘客都坐不下）—— 不许去重。"""
    from ba_mod_maker.core.rules.engine import Finding

    first = Finding("inv.transport.capacity", "warning", "TransportAvailabilities", "a", (1,))
    second = Finding("inv.transport.capacity", "warning", "TransportAvailabilities", "b", (2,))
    assert new_findings((), (first, second)) == (first, second)
    assert new_findings((first,), (first, second)) == (second,)
    assert new_findings((first, second), (first, second)) == ()
