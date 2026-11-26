"""规则引擎（`core/rules/engine.py`）的测试。

全部用**合成规则书 + 合成数据**，不读游戏文件、不依赖产品自带的那份
`rulebook.json`（它随数据侧更新而变，拿它当夹具会让用例跟着数据漂）。

钉住四件事：

1. 各 check 真的查得出问题，也真的放得过正确的数据；
2. `0 = 不使用` 这条约定（外键里 0 不是"指向 Id 0"）；
3. **基线差值**：同一份数据跑两次差值为 0 —— 这是"打开就满屏红"的解药；
4. 表/列缺失时**报一条 warning 而不是崩掉**，也不伪装成"检查通过"。
"""

from __future__ import annotations

from pathlib import Path

from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.data.schema import TableSchema
from ba_mod_maker.core.rules.engine import RuleEngine, diff_reports


def _book(*rules: dict) -> dict:
    return {"schema": 1, "rules": list(rules)}


def _fk(rid: str, table: str, column: str, target: str, severity: str = "error") -> dict:
    return {
        "id": rid,
        "kind": "foreign_key",
        "check": "foreign_key",
        "table": table,
        "column": column,
        "target": target,
        "severity": severity,
        "evidence": "",
        "note": "",
        "expr": "",
    }


def _uniq(rid: str, table: str, columns: list[str], kind: str = "unique_key") -> dict:
    return {
        "id": rid,
        "kind": kind,
        "check": "unique_key",
        "table": table,
        "columns": columns,
        "severity": "error" if kind == "unique_key" else "info",
        "evidence": "",
        "note": "",
        "expr": "",
    }


def _required(rid: str, table: str, severity: str = "warning") -> dict:
    return {
        "id": rid,
        "kind": "row_required_per_unit",
        "check": "required_per_unit",
        "table": table,
        "unit_column": "UnitId",
        "severity": severity,
        "evidence": "",
        "note": "",
        "expr": "",
    }


def _instance(tables: dict[str, tuple[tuple[str, ...], list[list]]]) -> Instance:
    inst = Instance(
        path_id="1",
        directory=Path("."),
        schema={n: TableSchema(name=n, columns=c) for n, (c, _) in tables.items()},
        manifest={},
    )
    for name, (columns, rows) in tables.items():
        inst._tables[name] = Table(
            name=name,
            columns=columns,
            rows=rows,
            source_columns=columns,
            source_path=Path(f"{name}.json"),
        )
    return inst


# --------------------------------------------------------------------------- #
# 外键
# --------------------------------------------------------------------------- #


def test_foreign_key_passes_and_reports_the_offending_row() -> None:
    inst = _instance(
        {
            "Units": (("Id", "CountryId"), [[1, 1], [2, 2], [3, 9]]),
            "Countries": (("Id", "Name"), [[1, "US"], [2, "RU"]]),
        }
    )
    engine = RuleEngine(_book(_fk("fk.Units.CountryId", "Units", "CountryId", "Countries")))
    report = engine.validate(inst)

    assert len(report.outcomes) == 1
    outcome = report.outcomes[0]
    assert outcome.checked == 3, "三个非零值都要查"
    assert outcome.violations == 1
    assert outcome.samples[0].row_ids == (3,)
    assert "9" in outcome.samples[0].message


def test_foreign_key_treats_zero_as_unused_not_as_id_zero() -> None:
    """`0` = 不使用（_rules.json 的约定）。当成"指向 Id 0"会把原版判成满盘违规。"""
    inst = _instance(
        {
            "Abilities": (("Id", "SmokeAmmunitionId"), [[1, 0], [2, 0], [3, 585]]),
            "Ammunitions": (("Id",), [[585]]),
        }
    )
    engine = RuleEngine(
        _book(
            _fk(
                "fk.Abilities.SmokeAmmunitionId",
                "Abilities",
                "SmokeAmmunitionId",
                "Ammunitions",
            )
        )
    )
    outcome = engine.validate(inst).outcomes[0]
    assert outcome.checked == 1, "两个 0 都不该被计入检查"
    assert outcome.violations == 0


# --------------------------------------------------------------------------- #
# 唯一键
# --------------------------------------------------------------------------- #


def test_unique_key_finds_duplicate_combinations() -> None:
    inst = _instance(
        {
            "Options": (("Id", "ModificationId", "Name"), [[7, 3, "a"], [8, 3, "b"], [9, 3, "a"]]),
        }
    )
    engine = RuleEngine(_book(_uniq("uniq.Options", "Options", ["ModificationId", "Name"])))
    outcome = engine.validate(inst).outcomes[0]
    assert outcome.violations == 1
    # (3, "a") 出现在行 Id 7 与 9；(3, "b") 是另一个组合，不算重复
    assert outcome.samples[0].row_ids == (7, 9)


def test_unique_key_reports_every_row_of_a_duplicate_group() -> None:
    inst = _instance({"T": (("Id", "K"), [[1, 5], [2, 5], [3, 5]])})
    engine = RuleEngine(_book(_uniq("uniq.T", "T", ["K"])))
    assert engine.validate(inst).outcomes[0].samples[0].row_ids == (1, 2, 3)


# --------------------------------------------------------------------------- #
# 必需行
# --------------------------------------------------------------------------- #


def test_required_per_unit_lists_units_without_a_row() -> None:
    inst = _instance(
        {
            "Units": (("Id",), [[1], [2], [3]]),
            "UnitPropulsions": (("Id", "UnitId", "MobilityId"), [[10, 1, 5]]),
        }
    )
    engine = RuleEngine(_book(_required("req.UnitPropulsions", "UnitPropulsions", "error")))
    outcome = engine.validate(inst).outcomes[0]
    assert outcome.checked == 3
    assert sorted(o for f in outcome.samples for o in f.row_ids) == [2, 3]


# --------------------------------------------------------------------------- #
# 默认装甲
# --------------------------------------------------------------------------- #


def test_default_flag_required_only_checks_units_that_have_links() -> None:
    """一行 UnitArmors 都没有的单位归 `req.UnitArmors` 管，两条规则各管一段。"""
    inst = _instance(
        {
            "Armors": (("Id", "IsDefault", "Name"), [[80, False, "A"], [81, True, "B"]]),
            "UnitArmors": (("Id", "UnitId", "ArmorId"), [[1, 1, 80], [2, 2, 81]]),
        }
    )
    rule = {
        "id": "req.UnitArmors.IsDefault",
        "kind": "default_flag_required",
        "check": "default_flag_required",
        "table": "UnitArmors",
        "unit_column": "UnitId",
        "target": "Armors",
        "flag_column": "IsDefault",
        "severity": "error",
        "evidence": "",
        "note": "",
        "expr": "",
    }
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]
    assert outcome.checked == 2
    assert outcome.violations == 1
    assert outcome.samples[0].row_ids == (1,)


# --------------------------------------------------------------------------- #
# 保留槽位
# --------------------------------------------------------------------------- #


def test_constant_zero_flags_any_non_zero() -> None:
    inst = _instance({"Options": (("Id", "Turret20Id"), [[1, 0], [2, 0], [3, 77]])})
    rule = {
        "id": "zero.Options.Turret20Id",
        "kind": "unused_slot",
        "check": "constant_zero",
        "table": "Options",
        "column": "Turret20Id",
        "expected": 0,
        "severity": "info",
        "evidence": "",
        "note": "",
        "expr": "",
    }
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]
    assert outcome.violations == 1
    assert outcome.samples[0].row_ids == (3,)


# --------------------------------------------------------------------------- #
# 增量与降级
# --------------------------------------------------------------------------- #


def test_table_filter_keeps_only_related_rules_plus_cross_table() -> None:
    cross = {
        "id": "inv.transport.capacity",
        "kind": "cross_table",
        "check": "cross_table",
        "severity": "warning",
        "evidence": "",
        "note": "",
        "expr": "",
    }
    engine = RuleEngine(
        _book(
            _fk("fk.Units.CountryId", "Units", "CountryId", "Countries"),
            _uniq("uniq.Options", "Options", ["ModificationId"]),
            cross,
        )
    )
    picked = {r["id"] for r in engine.rules_for("Units")}
    # 跨表规则没有 table 字段，所以永远在
    assert picked == {"fk.Units.CountryId", "inv.transport.capacity"}


def test_missing_table_reports_a_warning_instead_of_crashing() -> None:
    """数据源与规则书版本不匹配时：报一条 warning，**不能**伪装成"检查通过"。"""
    inst = _instance({"Units": (("Id", "CountryId"), [[1, 1]])})
    engine = RuleEngine(_book(_fk("fk.Units.CountryId", "Units", "CountryId", "Countries")))
    report = engine.validate(inst)

    outcome = report.outcomes[0]
    assert not outcome.ok, "跑不了 ≠ 通过"
    assert outcome.severity == "warning"
    assert "跑不了" in outcome.samples[0].message


def test_unknown_cross_table_id_is_not_silently_skipped() -> None:
    rule = {
        "id": "inv.made.up",
        "kind": "cross_table",
        "check": "cross_table",
        "severity": "error",
        "evidence": "",
        "note": "",
        "expr": "",
    }
    inst = _instance({"Units": (("Id",), [[1]])})
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]
    assert not outcome.ok, "跑不了 ≠ 通过"
    assert outcome.severity == "error", "引擎/规则书不一致是产品的错，要显眼"
    assert "跑不了" in outcome.samples[0].message


# --------------------------------------------------------------------------- #
# 基线差值（"打开就满屏红"的解药）
# --------------------------------------------------------------------------- #


def test_diff_of_the_same_data_is_zero() -> None:
    """**这条最重要**：原封不动地打开一个文件，不能报出"新增问题"。

    实测原版 52085：规则书在原版上本来就报 **1735** 条"违规"
    （Options 的 ModificationId 重复 615 组、非步兵单位没有 SquadMembers 行 346 条…）。
    不跟基线比，界面一打开就是满屏红，校验就废了。
    """
    inst = _instance(
        {
            "Units": (("Id",), [[1], [2]]),
            "UnitPropulsions": (("Id", "UnitId"), [[10, 1]]),
        }
    )
    engine = RuleEngine(_book(_required("req.UnitPropulsions", "UnitPropulsions")))
    baseline = engine.validate(inst)
    current = engine.validate(inst)

    assert baseline.outcomes[0].violations == 1, "原版就缺一行"
    deltas = diff_reports(baseline, current)
    assert len(deltas) == 1
    assert deltas[0].baseline == 1 and deltas[0].current == 1
    assert deltas[0].introduced == 0, "没改过就不该报新增"


def test_diff_reports_new_problems_and_also_fixes() -> None:
    inst = _instance(
        {
            "Units": (("Id",), [[1], [2], [3]]),
            "UnitPropulsions": (("Id", "UnitId"), [[10, 1], [11, 2]]),
        }
    )
    engine = RuleEngine(_book(_required("req.UnitPropulsions", "UnitPropulsions")))
    baseline = engine.validate(inst)  # 缺 Id 3

    inst.table("UnitPropulsions").rows.append([12, 3])
    after_adding = engine.validate(inst)
    assert diff_reports(baseline, after_adding)[0].introduced == -1, "改动也可以是修好"

    inst.table("UnitPropulsions").rows.clear()
    assert diff_reports(baseline, engine.validate(inst))[0].introduced == 3 - 1


def test_diff_handles_a_rule_that_appears_only_later() -> None:
    """基线与现在的规则集合不一致时（换了规则书），缺失的一侧按 0 算，不抛。"""
    inst = _instance({"Units": (("Id",), [[1]])})
    empty = RuleEngine(_book()).validate(inst)
    one = RuleEngine(_book(_uniq("uniq.Units", "Units", ["Id"]))).validate(inst)
    deltas = diff_reports(empty, one)
    assert len(deltas) == 1
    assert deltas[0].baseline == 0
