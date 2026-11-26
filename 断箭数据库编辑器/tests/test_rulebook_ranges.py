"""`range` / `cross_range` 两类区间规则的用例。

被测两样东西：

- `core/rules/engine.py` 的 `_CHECKS["range"]` / `_CHECKS["cross_range"]`；
- `tools/gen_rulebook.py` 从 `core/rules/range_bounds.json` 生成的那 20 条规则。

钉住四件事：

1. **正控 —— 尺子必须能红**：造已知违规的实例，断言规则**确实报红**。只证明
   "干净数据不报错"等于没测 —— 那种尺子永远红不了（AGENTS `0.9 动作 2）；
2. **两端闭合 + 单边**：`[lo, hi]` 两个端点都算通过；`lo` / `hi` 任一为 `None` 是单边；
3. **坏规则不伪装成通过**：两端都是 null、比较符不认识 -> 一条「跑不了这条规则」的 error；
4. **负控读数**：对真实数据源跑一遍，把**读到的违规数**钉下来（含逐条分解）。
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from ba_mod_maker.core.asset.tables import TABLE_NAMES
from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.data.schema import TableSchema
from ba_mod_maker.core.rules.engine import KIND_LABELS, RuleEngine, load_rulebook

#: 两类新区间规则的 kind（别处按它分流"旧 93 条"与"新的"）。
NEW_KINDS = frozenset({"range", "cross_range"})

#: `range` / `cross_range` 的唯一输入 —— 由 `tools/gen_rulebook.py` 读，
#: **不在数据源里**（出处见文件自己的 `source` 段）。
RANGE_BOUNDS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "ba_mod_maker"
    / "core"
    / "rules"
    / "range_bounds.json"
)

#: 与 `tests/test_cli_validate_find.py` 同一口径：真实数据源 + 默认非战役实例。
DATASET_ROOT = find_dataset_root()
INSTANCE_ID = "52085"
needs_dataset = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")


# --------------------------------------------------------------------------- #
# 夹具：合成规则书 + 合成实例（不读游戏文件，毫秒级）
# --------------------------------------------------------------------------- #


def _book(*rules: dict) -> dict:
    return {"schema": 1, "rules": list(rules)}


def _instance(tables: dict[str, tuple[tuple[str, ...], list[list]]]) -> Instance:
    """内存造实例 —— 与 `tests/test_rules_engine.py` 同一姿势。"""
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


def _range_rule(
    *,
    rid: str = "range.Ammunitions.MuzzleVelocity",
    table: str = "Ammunitions",
    column: str = "MuzzleVelocity",
    lo: float | None = 50.0,
    hi: float | None = 2000.0,
    sentinels: list | None = None,
) -> dict:
    return {
        "id": rid,
        "kind": "range",
        "severity": "warning",
        "evidence": "mod-authored",
        "note": "",
        "expr": "",
        "check": "range",
        "table": table,
        "column": column,
        "lo": lo,
        "hi": hi,
        "required": True,
        # 缺省空 = **严格判**（口径 A：哨兵是逐列枚举的，不是"值为 0 就过"）。
        "sentinels": sentinels if sentinels is not None else [],
    }


def _cross_rule(
    *,
    rid: str = "cross.Ammunitions.MinimalRange_le_GroundRange",
    left: list | None = None,
    right: list | None = None,
    const: float = 0.0,
    relation: str = "<=",
    max_of: list | None = None,
    scale: float = 1.0,
    gate_of: list | None = None,
    gate_gt: float = 0.0,
) -> dict:
    return {
        "id": rid,
        "kind": "cross_range",
        "severity": "warning",
        "evidence": "task-3",
        "note": "",
        "expr": "",
        "check": "cross_range",
        "table": "Ammunitions",
        "left_terms": left if left is not None else [["MinimalRange", 1.0]],
        "right_terms": right if right is not None else [["GroundRange", 1.0]],
        "right_const": const,
        "right_max_of": max_of if max_of is not None else [],
        "right_scale": scale,
        "gate_max_of": gate_of if gate_of is not None else [],
        "gate_gt": gate_gt,
        "relation": relation,
    }


# --------------------------------------------------------------------------- #
# 正控：尺子必须能红
# --------------------------------------------------------------------------- #


def test_range_flags_a_value_above_hi() -> None:
    """正控 ①：`MuzzleVelocity = 99999`（上限 2000）必须红，且只红那一行。"""
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 900], [2, 99999]])})
    outcome = RuleEngine(_book(_range_rule())).validate(inst).outcomes[0]

    assert outcome.violations == 1, "99999 越了上限 2000，这条规则没红 = 尺子坏了"
    assert outcome.checked == 2, "两个非空数值都要看"
    assert outcome.samples[0].row_ids == (2,)
    assert "99999" in outcome.samples[0].message
    assert "[50, 2000]" in outcome.samples[0].message


def test_range_flags_a_value_below_lo() -> None:
    """正控 ②：下界方向也要能红（`MuzzleVelocity = 5` < 50）。"""
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 5]])})
    outcome = RuleEngine(_book(_range_rule())).validate(inst).outcomes[0]

    assert outcome.violations == 1
    assert outcome.samples[0].row_ids == (1,)


def test_cross_range_flags_a_known_violation() -> None:
    """正控 ③：`MinimalRange(9000) > GroundRange(5000)` 必须红。"""
    inst = _instance(
        {"Ammunitions": (("Id", "MinimalRange", "GroundRange"), [[1, 3000, 5000], [2, 9000, 5000]])}
    )
    outcome = RuleEngine(_book(_cross_rule())).validate(inst).outcomes[0]

    assert outcome.violations == 1, "第 2 行的最小射程大于射程，这条规则没红 = 尺子坏了"
    assert outcome.checked == 2
    assert outcome.samples[0].row_ids == (2,)
    assert "MinimalRange" in outcome.samples[0].message
    assert "GroundRange" in outcome.samples[0].message


def test_cross_range_flags_the_sum_form() -> None:
    """正控 ④：`SmokeDuration + SmokeFadeDuration <= 120`（右侧是常数）。"""
    rule = _cross_rule(
        rid="cross.Ammunitions.SmokeDuration_plus_SmokeFadeDuration_le_120",
        left=[["SmokeDuration", 1.0], ["SmokeFadeDuration", 1.0]],
        right=[],
        const=120.0,
    )
    columns = ("Id", "SmokeDuration", "SmokeFadeDuration")
    inst = _instance({"Ammunitions": (columns, [[1, 100, 20], [2, 100, 21]])})
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]

    assert outcome.violations == 1, "100 + 21 = 121 > 120，这条规则没红 = 尺子坏了"
    assert outcome.samples[0].row_ids == (2,)
    assert "121" in outcome.samples[0].message, "读数要写**和**，不是只写左项"


# --------------------------------------------------------------------------- #
# 负控 / 边界：不该红的别红
# --------------------------------------------------------------------------- #


def test_range_passes_at_both_bounds() -> None:
    """`[lo, hi]` 两端都闭：50 与 2000 都算通过。"""
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 50], [2, 2000], [3, 900]])})
    outcome = RuleEngine(_book(_range_rule())).validate(inst).outcomes[0]

    assert outcome.violations == 0, "端点值被判成越界 = 区间写成了开区间"
    assert outcome.checked == 3


def test_range_can_be_one_sided() -> None:
    """单边：`lo=None` 只查上限，`hi=None` 只查下限。"""
    upper = _range_rule(column="MaxSpeed", lo=None, hi=2700.0)
    inst = _instance({"Ammunitions": (("Id", "MaxSpeed"), [[1, 2701], [2, 2700]])})
    outcome = RuleEngine(_book(upper)).validate(inst).outcomes[0]
    assert outcome.violations == 1, "只有 2701 越了上限；2700 是端点，通过"
    assert outcome.samples[0].row_ids == (1,)
    assert "-∞" in outcome.samples[0].message

    lower = _range_rule(column="Acceleration", lo=1.0, hi=None)
    inst2 = _instance({"Ammunitions": (("Id", "Acceleration"), [[1, 0], [2, 3000]])})
    outcome2 = RuleEngine(_book(lower)).validate(inst2).outcomes[0]
    assert outcome2.violations == 1
    assert outcome2.samples[0].row_ids == (1,)
    assert "+∞" in outcome2.samples[0].message


def test_range_skips_cells_that_are_not_numbers() -> None:
    """非数值 / 缺值**不猜**：跳过，且不算进 checked（与外键那条同一口径）。"""
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, None], [2, "900"], [3, 900]])})
    outcome = RuleEngine(_book(_range_rule())).validate(inst).outcomes[0]

    assert outcome.checked == 1, "只有第 3 行是数值"
    assert outcome.violations == 0


def test_range_sentinels_are_skipped_per_column() -> None:
    """**口径 A 的正控**：哨兵值跳过，但**同一个数只要不在哨兵表里就照旧判**。

    尺子必须能红：30 / 40 都在 21~49 那段"没有真实值的空档"里，不是哨兵 -> 必须红。
    """
    rule = _range_rule(sentinels=[0, 5.0, 20.0])
    inst = _instance(
        {"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 0], [2, 5], [3, 20], [4, 30], [5, 40]])}
    )
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]

    assert outcome.checked == 2, "5 个值里 3 个是哨兵，只有 30 / 40 进判定"
    assert outcome.violations == 2, "30 / 40 小于下界 50 且不是哨兵 —— 不红就是哨兵表写成了下界豁免"
    assert {finding.row_ids[0] for finding in outcome.samples} == {4, 5}


def test_range_without_sentinels_stays_strict() -> None:
    """⛔ 哨兵缺失 / 为空**不是**「值为 0 就过」：0 越小界照样红。"""
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 0]])})

    assert RuleEngine(_book(_range_rule())).validate(inst).outcomes[0].violations == 1
    empty = _range_rule(sentinels=[])
    assert RuleEngine(_book(empty)).validate(inst).outcomes[0].violations == 1


def test_cross_range_max_of_form_flags_the_90pct_case() -> None:
    """**口径 B 的正控**：`MinimalRange >= 0.9 × max(...)` 报红（原文 decompiled.cs:503149）。"""
    gate = ["GroundRange", "LowAltRange", "HighAltRange"]
    rule = _cross_rule(
        rid="cross.Ammunitions.MinimalRange_lt_90pct_of_max_range",
        max_of=gate,
        scale=0.9,
        right=[],
        relation="<",
        gate_of=gate,
    )
    columns = ("Id", "MinimalRange", "GroundRange", "LowAltRange", "HighAltRange")
    inst = _instance(
        {
            "Ammunitions": (
                columns,
                [
                    [1, 1000, 5000, 0, 0],  # 1000 < 4500 -> 通过
                    [2, 4500, 5000, 0, 0],  # 恰好 0.9×5000 -> "不小于" -> 红（端点）
                    [3, 4999, 5000, 0, 0],  # 红
                ],
            )
        }
    )
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]

    assert outcome.violations == 2, "90% 那条线必须能红（含端点）"
    assert {finding.row_ids[0] for finding in outcome.samples} == {2, 3}
    assert "0.9*max(GroundRange, LowAltRange, HighAltRange)" in outcome.samples[0].message


def test_cross_range_gate_skips_rows_without_a_range() -> None:
    """**口径 B 的前置条件**：`max(...) > 0` 不成立的行跳过（原文的 `num > 0.0`）。"""
    gate = ["GroundRange", "LowAltRange", "HighAltRange"]
    rule = _cross_rule(
        rid="cross.Ammunitions.MinimalRange_lt_90pct_of_max_range",
        max_of=gate,
        scale=0.9,
        right=[],
        relation="<",
        gate_of=gate,
    )
    columns = ("Id", "MinimalRange", "GroundRange", "LowAltRange", "HighAltRange")
    inst = _instance(
        {
            "Ammunitions": (
                columns,
                [
                    [1, 1000, 0, 0, 0],  # max = 0 -> 不适用，跳过
                    [2, 1000, 0, 0, 500],  # max = 500 -> 1000 < 450 不成立 -> 红
                ],
            )
        }
    )
    outcome = RuleEngine(_book(rule)).validate(inst).outcomes[0]

    assert outcome.checked == 1, "第一行被前置条件挡掉了，不该进 checked"
    assert outcome.violations == 1
    assert outcome.samples[0].row_ids == (2,)


def test_cross_range_passes_at_equality() -> None:
    """`<=` 在相等处通过（`<` 才是开区间）。"""
    inst = _instance({"Ammunitions": (("Id", "MinimalRange", "GroundRange"), [[1, 5000, 5000]])})
    assert RuleEngine(_book(_cross_rule())).validate(inst).outcomes[0].violations == 0

    strict = _cross_rule(relation="<")
    assert RuleEngine(_book(strict)).validate(inst).outcomes[0].violations == 1


# --------------------------------------------------------------------------- #
# 坏规则：报 error，**不伪装成通过**
# --------------------------------------------------------------------------- #


def test_range_with_two_open_ends_is_a_broken_rule() -> None:
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 900]])})
    outcome = (
        RuleEngine(_book(_range_rule(lo=None, hi=None))).validate(inst).outcomes[0]
    )

    assert outcome.severity == "error"
    assert outcome.violations == 1
    assert "跑不了这条规则" in outcome.samples[0].message


def test_cross_range_with_an_unknown_relation_is_a_broken_rule() -> None:
    inst = _instance({"Ammunitions": (("Id", "MinimalRange", "GroundRange"), [[1, 1, 1]])})
    outcome = RuleEngine(_book(_cross_rule(relation="~="))).validate(inst).outcomes[0]

    assert outcome.severity == "error"
    assert "跑不了这条规则" in outcome.samples[0].message


def test_a_missing_column_is_a_warning_not_a_pass() -> None:
    """规则书与数据对不上（列不在）-> 一条 warning，**不是"检查通过"**。"""
    inst = _instance({"Ammunitions": (("Id", "MuzzleVelocity"), [[1, 900]])})
    outcome = (
        RuleEngine(_book(_range_rule(column="没有这一列"))).validate(inst).outcomes[0]
    )

    assert outcome.severity == "warning"
    assert outcome.violations == 1
    assert outcome.checked == 0
    assert "跑不了这条规则" in outcome.samples[0].message


# --------------------------------------------------------------------------- #
# 生成物：`rulebook.json` 里那 20 条真的落地、引擎真的认得
# --------------------------------------------------------------------------- #

#: 合成实例只用这三张有区间规则的表 —— 别的表一律不造。
_AMMO_COLUMNS = (
    "Id",
    "GroundRange",
    "LowAltRange",
    "HighAltRange",
    "MinimalRange",
    "MaxSeekerDistance",
    "SeadProjectileRangeOverride",
    "NonSeadProjectileRangeOverride",
    "MuzzleVelocity",
    "MaxSpeed",
    "Acceleration",
    "BurnTime",
    "SmokeRadius",
    "SmokeDuration",
    "SmokeFadeDuration",
)
#: 干净的一行（每个值都贴着区间，端点也算过）。
_CLEAN_AMMO = [1, 5000, 0, 1000, 1000, 0, 0, 0, 900, 800, 100, 5, 50, 60, 30]
#: 干净的第二行：每个值仍在区间里，但换一处取值（免得两行一模一样）。
_CLEAN_AMMO_2 = [2, 8000, 0, 2000, 500, 0, 0, 0, 1200, 1500, 200, 10, 80, 90, 20]
#: 脏的一行（三处）：MinimalRange 9000 >= 0.9×max(5000,0,1000)、MuzzleVelocity 99999、
#: 烟雾 100+30 > 120。max 用 GroundRange/LowAltRange/HighAltRange 三列算，前置条件 max>0 成立。
_DIRTY_AMMO_2 = [2, 5000, 0, 1000, 9000, 0, 0, 0, 99999, 800, 100, 5, 50, 100, 30]
#: 另两张表：干净版 / 脏版（-1 越下界 0）。
_CLEAN_SENSORS_2 = [2, 5000, 5000, 5000]
_DIRTY_SENSORS_2 = [2, 0, 0, -1]
_CLEAN_ABILITY_2 = [2, 100]
_DIRTY_ABILITY_2 = [2, -1]

#: 拿产品自带那份规则书跑上面那两行，**点得出名字**的 5 条命中。
_EXPECTED_RED = {
    "range.Ammunitions.MuzzleVelocity",
    "range.Sensors.OpticsHighAltitude",
    "range.Abilities.LaserMaxRange",
    "cross.Ammunitions.MinimalRange_lt_90pct_of_max_range",
    "cross.Ammunitions.SmokeDuration_plus_SmokeFadeDuration_le_120",
}


def _bounds_instance(*, clean: bool) -> Instance:
    """三张有区间规则的表，两行。§clean=True§ 时**每一行每个值**都贴着区间。"""
    return _instance(
        {
            "Ammunitions": (
                _AMMO_COLUMNS,
                [_CLEAN_AMMO, _CLEAN_AMMO_2 if clean else _DIRTY_AMMO_2],
            ),
            "Sensors": (
                ("Id", "OpticsGround", "OpticsLowAltitude", "OpticsHighAltitude"),
                [[1, 0, 0, 100000], _CLEAN_SENSORS_2 if clean else _DIRTY_SENSORS_2],
            ),
            "Abilities": (
                ("Id", "LaserMaxRange"),
                [[1, 0], _CLEAN_ABILITY_2 if clean else _DIRTY_ABILITY_2],
            ),
        }
    )


def test_the_shipped_rulebook_carries_the_two_new_kinds() -> None:
    """生成物里两类都得到齐：18 条 `range` + 2 条 `cross_range`，表名全是规范名。"""
    book = load_rulebook()
    kinds = Counter(rule["kind"] for rule in book["rules"])

    assert kinds["range"] == 18, f"`range` 条数变了：{kinds['range']}"
    assert kinds["cross_range"] == 2, f"`cross_range` 条数变了：{kinds['cross_range']}"
    assert book["rule_count"] == len(book["rules"])

    ids = [rule["id"] for rule in book["rules"]]
    assert len(ids) == len(set(ids)), "规则 id 撞了 —— 差值/角标会串台"

    for rule in book["rules"]:
        if rule["kind"] not in NEW_KINDS:
            continue
        assert rule["table"] in TABLE_NAMES, f"{rule['id']}：表名不是规范名"
        assert rule["severity"] == "warning", (
            f"{rule['id']}：区间规则**不许**判成 error —— 参照是 mod 作者自定的合理区间"
        )
        assert rule["evidence"], f"{rule['id']}：没写出处"
        assert "原版 52085 实测" in rule["note"], f"{rule['id']}：note 里没有实跑读数"

        if rule["kind"] == "range":
            # 口径 A：哨兵**逐列枚举**，0 是 18 列通用那一枚；缺了它原版就会满屏红（2753 条）。
            assert 0 in rule["sentinels"], f"{rule['id']}：哨兵表里没有 0"
            assert "行）" in rule["note"], f"{rule['id']}：note 里没有写出哨兵的实测命中行数"
        elif "MinimalRange_lt_90pct_of_max_range" in rule["id"]:
            # 口径 B：**0.9 × max(...) 与前置条件 num > 0 都要显式写在规则里**（别写死三列）。
            assert rule["right_max_of"] == ["GroundRange", "LowAltRange", "HighAltRange"]
            assert rule["right_scale"] == 0.9
            assert rule["gate_max_of"] == rule["right_max_of"], "前置条件的列要与右操作数一致"
            assert rule["gate_gt"] == 0.0, "原文的前置条件是 num > 0.0"
            assert rule["relation"] == "<", "原文判的是 num2 >= 0.9*num ⇒ 违反 < 才是红"


def test_every_kind_in_the_rulebook_has_a_chinese_label() -> None:
    """新加的 kind 必须同时进 `KIND_LABELS` —— 不然界面右栏会露出英文 kind。"""
    kinds = {rule["kind"] for rule in load_rulebook()["rules"]}
    assert kinds <= set(KIND_LABELS), f"没有中文名的 kind：{sorted(kinds - set(KIND_LABELS))}"


def test_every_bounds_entry_landed_in_the_rulebook() -> None:
    """`range_bounds.json` 里有几条，规则书里就得有几条 —— 少一条 = 那个约束形同不存在。"""
    bounds = json.loads(RANGE_BOUNDS_PATH.read_text(encoding="utf-8"))
    shipped = {rule["id"] for rule in load_rulebook()["rules"] if rule["kind"] in NEW_KINDS}
    expected = {f"range.{e['table']}.{e['column']}" for e in bounds["ranges"]}
    expected |= {f"cross.{e['table']}.{e['name']}" for e in bounds["cross_ranges"]}

    assert shipped == expected
    assert len(shipped) == len(bounds["ranges"]) + len(bounds["cross_ranges"])


def test_generated_rules_go_red_end_to_end() -> None:
    """**正控的端到端版**：产品自带那份规则书 + 一个已知违规的合成实例。

    这条才是"生成物与引擎对得上"的尺子 —— 前面几条正控用的是手写的规则 dict，
    证明得了引擎，证明不了 `rulebook.json` 里那 20 条真的被跑起来。
    """
    report = RuleEngine().validate(_bounds_instance(clean=False))
    red = {
        outcome.rule_id: outcome.violations
        for outcome in report.outcomes
        if outcome.kind in NEW_KINDS and outcome.violations
    }

    assert set(red) == _EXPECTED_RED, f"该红没红 / 红了别的：{sorted(set(red) ^ _EXPECTED_RED)}"
    assert set(red.values()) == {1}, "每一处都只该报一条"


def test_generated_rules_stay_green_on_clean_rows() -> None:
    """同一个实例、把脏的那行换成干净的 -> 新区间规则一条都不报。

    ⚠️ 这一条**单独不算数**（干净的尺子永远绿）—— 它必须和上面那条正控一起看。
    """
    report = RuleEngine().validate(_bounds_instance(clean=True))
    red = [o.rule_id for o in report.outcomes if o.kind in NEW_KINDS and o.violations]

    assert red == [], f"干净数据也报红：{red}"


# --------------------------------------------------------------------------- #
# 负控：真实数据上的读数
# --------------------------------------------------------------------------- #


@needs_dataset
def test_the_real_dataset_reads_zero_hits_after_sentinels() -> None:
    """**负控 —— 对当前真实数据跑一遍，把读到的违规数钉下来**（task-3 + 口径 A）。

    2026-10-08 实测（原版 52085，产品自带规则书 113 条）：

    ===========================================  =======
    读数                                          条数
    ===========================================  =======
    旧 93 条（error / warning / info）            0 / 699 / 1036
    `range` 18 条 · **加哨兵前**                  2753
    `range` 18 条 · **加哨兵后**                  **0**
    旧 cross 形状 `MinimalRange <= GroundRange`   58
    新 cross 形状 `< 0.9×max(...)` ＋ 前置条件     **0**
    ===========================================  =======

    前后两个读数不是抄来的：下面两条用例用**同一份数据**把「剥掉 sentinels 的规则书」
    与「旧 cross 形状」各跑一遍 —— 那就是上面的 2753 与 58（口径 A / B 的效果审计）。

    加哨兵前那 2811 条**全是「这一项不适用」的哨兵值**撞上了参照。逐列证据
    （每个哨兵值在原版命中多少行、`-1` 为什么只给 HighAltRange、`5.0/20.0` 为什么算占位）
    都在 `core/rules/range_bounds.json` 的 `sentinels` 里，也抄进了每条规则的 `note`。
    """
    dataset = load_dataset(DATASET_ROOT)
    instance = dataset.instance(dataset.default_instance_id)
    if instance.path_id != INSTANCE_ID:
        pytest.skip(f"默认实例是 {instance.path_id}，不是 {INSTANCE_ID}")

    report = RuleEngine().validate(instance)

    left = {
        o.rule_id: o.violations
        for o in report.outcomes
        if o.kind in NEW_KINDS and o.violations
    }
    assert left == {}, "新区间规则在原版上一条都不该报"

    old: Counter = Counter()
    for outcome in report.outcomes:
        if outcome.kind not in NEW_KINDS:
            old[outcome.severity] += outcome.violations
    assert dict(old) == {"error": 0, "warning": 699, "info": 1036}, "旧 93 条的读数被动过"
    assert dict(report.counts) == {"error": 0, "warning": 699, "info": 1036}, (
        "全库读数必须与加这两类规则**之前一模一样**"
    )

    # 每条都真的看过值（不是"跑不了这条规则"降级）；唯一的例外是整列都是哨兵那一列。
    checked = {o.rule_id: o.checked for o in report.outcomes if o.kind in NEW_KINDS}
    all_sentinel = "range.Ammunitions.NonSeadProjectileRangeOverride"
    assert checked[all_sentinel] == 0, "这一列 630/630 行都是哨兵 0 —— 原版上一行都没得判"
    assert all(n > 0 for rid, n in checked.items() if rid != all_sentinel), checked


@needs_dataset
def test_sentinels_are_what_turns_2753_into_zero() -> None:
    """**口径 A 的效果审计**：同一份数据、同一批规则，只差 `sentinels` 一项。"""
    dataset = load_dataset(DATASET_ROOT)
    instance = dataset.instance(dataset.default_instance_id)
    if instance.path_id != INSTANCE_ID:
        pytest.skip(f"默认实例是 {instance.path_id}，不是 {INSTANCE_ID}")

    stripped = copy.deepcopy(load_rulebook())
    for rule in stripped["rules"]:
        if rule["kind"] == "range":
            rule.pop("sentinels", None)

    def range_hits(report) -> int:
        return sum(o.violations for o in report.outcomes if o.kind == "range")

    assert range_hits(RuleEngine(stripped).validate(instance)) == 2753, "加哨兵前的读数"
    assert range_hits(RuleEngine().validate(instance)) == 0, "加哨兵后的读数"


@needs_dataset
def test_the_old_cross_shape_read_58_and_the_new_one_reads_zero() -> None:
    """**口径 B 的效果审计**：旧形状 58 条 / 新形状 0 条，同一份数据。"""
    dataset = load_dataset(DATASET_ROOT)
    instance = dataset.instance(dataset.default_instance_id)
    if instance.path_id != INSTANCE_ID:
        pytest.skip(f"默认实例是 {instance.path_id}，不是 {INSTANCE_ID}")

    old_rule = _cross_rule(
        rid="cross.Ammunitions.MinimalRange_le_GroundRange",
        left=[["MinimalRange", 1.0]],
        right=[["GroundRange", 1.0]],
        relation="<=",
    )
    assert RuleEngine(_book(old_rule)).validate(instance).outcomes[0].violations == 58

    new_id = "cross.Ammunitions.MinimalRange_lt_90pct_of_max_range"
    shipped = next(rule for rule in load_rulebook()["rules"] if rule["id"] == new_id)
    assert RuleEngine(_book(shipped)).validate(instance).outcomes[0].violations == 0
