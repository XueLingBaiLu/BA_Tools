"""规则引擎（零 Qt）。

规则**数据**来自产品自带的 `rulebook.json`（由 `tools/gen_rulebook.py` 从数据侧
的 `_relations.json` + `_rules.json` 派生，外加 `core/rules/range_bounds.json`
那份带出处的数值区间参照）。本模块只负责"怎么查"，
不负责"有哪些规则"——那条界线让规则可以随游戏更新重生成，而引擎不动。

条数**不写死在这里**：它是生成物（`rulebook.json::rule_count`），
读它用 `RuleEngine().rule_count`。

两种区间规则（2026-10-08 加）：

```
range        单字段区间   lo <= v <= hi（两端闭；lo / hi 任一为 null 即单边）
cross_range  同表内两行内字段的比较   Σ(系数·左列) <relation> Σ(系数·右列) + 常数
```

两者的出处、证据等级、以及**参照自己的判据与我们的差异**都写在
`core/rules/range_bounds.json` 里 —— 那不是可选的注释，是这两类规则唯一的输入。

## 为什么产品自带一份，而不是读数据源旁边的 `_rules.json`

用户可以用「打开…」直接开 `BA_Mod.zip` / `DataBaseCompiled.json` / `data.unity3d`，
**那三种来源旁边没有 `_rules.json`**，于是「实时校验」会整块消失。
规则书是产品知识，不是数据源的附属品。

## 增量的做法

每条规则都带 `table`，所以"改了一张表"只需要重跑那张表的规则，
不必 24 张表全扫。跨表不变量（`cross_table`）没有单表归属，永远跑。
这是"实时校验"能挂在每一次按键上的原因。
"""

from __future__ import annotations

import json
import operator
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.errors import RuleError

#: 一条规则最多带回来几个例子。界面只展示前几条，但**计数是真的**。
MAX_SAMPLES = 5

#: Id 列名。24 张表一致（见 `core/edit/changeset.py`）。
ID_COLUMN = "Id"

#: 外键里 `0` 表示"不使用"（`_rules.json` 的 note 原话），不是"指向 Id 0"。
UNUSED_FK_VALUE = 0

#: 分组展示用的中文名。
KIND_LABELS: dict[str, str] = {
    "foreign_key": "外键",
    "unique_key": "唯一键",
    "natural_key": "自然键",
    "row_required_per_unit": "必需行",
    "default_flag_required": "默认装甲",
    "cross_table": "不变量",
    "unused_slot": "保留槽位",
    "range": "数值区间",
    "cross_range": "跨字段区间",
}

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

#: `Armor Infantry <N>Men` —— 步兵护甲名的实测形态（33 个）。
_ARMOR_MEN_RE = re.compile(r"^Armor Infantry (\d+)Men$")

_RULEBOOK_PATH = Path(__file__).with_name("rulebook.json")


@dataclass(frozen=True, slots=True)
class Finding:
    """一条命中。带 `row_ids`，界面可以据此跳到那一行。

    ⚠️ **缺失型命中没有行可指**（例：某个单位压根没有 SensorUnits 行），
    那时 `row_ids` 里装的是**单位的 Id**，而 `table` 说的是它缺哪张关联表。
    这种命中同时把单位填进 `unit_ids` —— 卡片要按单位打角标，
    靠这一列才不用去猜 row_ids 到底是行 Id 还是单位 Id。
    """

    rule_id: str
    severity: str
    table: str
    message: str
    row_ids: tuple[int, ...] = ()
    #: 这条命中直接涉及的单位（缺失型命中必填；其它留空，由调用方按 UnitId 反查）。
    unit_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """一条规则跑完的结果。`checked` 是"看了多少个值"，不是"检查了几条规则"。"""

    rule_id: str
    kind: str
    severity: str
    table: str
    checked: int
    violations: int
    samples: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        return self.violations == 0


@dataclass(frozen=True, slots=True)
class KindSummary:
    """按 kind 汇总 —— 设计稿右栏那几行「外键 60 条 / 全部命中 / 60/60」。"""

    kind: str
    label: str
    total: int
    passed: int
    violations: int
    severity: str

    @property
    def all_passed(self) -> bool:
        return self.passed == self.total


@dataclass(frozen=True, slots=True)
class ValidationReport:
    outcomes: tuple[RuleOutcome, ...]

    @property
    def failed(self) -> tuple[RuleOutcome, ...]:
        """违反了的规则，按严重度排序（error 在前）。"""
        bad = [o for o in self.outcomes if not o.ok]
        bad.sort(key=lambda o: (_SEVERITY_ORDER.get(o.severity, 9), o.rule_id))
        return tuple(bad)

    @property
    def findings(self) -> tuple[Finding, ...]:
        out: list[Finding] = []
        for outcome in self.failed:
            out.extend(outcome.samples)
        return tuple(out)

    @property
    def counts(self) -> dict[str, int]:
        """真实**违规条数**（不是规则条数），按严重度。"""
        result = {"error": 0, "warning": 0, "info": 0}
        for outcome in self.failed:
            result[outcome.severity] = result.get(outcome.severity, 0) + outcome.violations
        return result

    @property
    def is_clean(self) -> bool:
        return all(o.ok for o in self.outcomes)

    def by_kind(self) -> tuple[KindSummary, ...]:
        groups: dict[str, list[RuleOutcome]] = {}
        for outcome in self.outcomes:
            groups.setdefault(outcome.kind, []).append(outcome)

        summaries: list[KindSummary] = []
        for kind, items in groups.items():
            severity = min((o.severity for o in items), key=lambda s: _SEVERITY_ORDER.get(s, 9))
            summaries.append(
                KindSummary(
                    kind=kind,
                    label=KIND_LABELS.get(kind, kind),
                    total=len(items),
                    passed=sum(1 for o in items if o.ok),
                    violations=sum(o.violations for o in items),
                    severity=severity,
                )
            )
        summaries.sort(key=lambda s: (_SEVERITY_ORDER.get(s.severity, 9), s.kind))
        return tuple(summaries)


@dataclass(frozen=True, slots=True)
class RuleDelta:
    """一条规则"相对打开时"的变化。

    为什么需要它：规则书里**大部分规则记录的是原版的既有形态**，不是普适真理。
    实测（原版 52085，全量 77 ms）：

        必需行  unit_required_per_unit  违规 697   ← 全是"非步兵单位没有 SquadMembers 行"这类
        唯一键  unique_key              违规 1036  ← 例：Options 的 ModificationId 本来就重复 615 组

    也就是说**打开一个原封不动的文件就会看到 1700+ 条"违规"**。
    一个一打开就满屏红的校验等于没有校验（"狼来了"）。

    正确的问法不是"有没有违反规则"，而是"**我改出来的问题有没有比打开时更多**"。
    所以基线的取法是：文件**刚打开时**跑一遍，之后每次比对差值。
    这不是把问题藏起来 —— `baseline` 与 `current` 两个数都留着，界面照实显示
    "原版既有 697 · 本次新增 0"。
    """

    rule_id: str
    kind: str
    severity: str
    table: str
    baseline: int
    current: int

    @property
    def introduced(self) -> int:
        """本次改动引入的条数。可能是负的（改动**修好**了一些）。"""
        return self.current - self.baseline


def diff_reports(baseline: ValidationReport, current: ValidationReport) -> tuple[RuleDelta, ...]:
    """把"打开时的校验"与"现在的校验"对上，按规则 id 求差。"""
    before = {o.rule_id: o for o in baseline.outcomes}
    deltas: list[RuleDelta] = []
    for outcome in current.outcomes:
        old = before.get(outcome.rule_id)
        deltas.append(
            RuleDelta(
                rule_id=outcome.rule_id,
                kind=outcome.kind,
                severity=outcome.severity,
                table=outcome.table,
                baseline=old.violations if old is not None else 0,
                current=outcome.violations,
            )
        )
    return tuple(deltas)


def load_rulebook(path: Path | None = None) -> dict[str, Any]:
    """读产品自带的规则书。读不到就是**装坏了**，不降级。"""
    target = path or _RULEBOOK_PATH
    try:
        with target.open(encoding="utf-8") as handle:
            book = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleError(
            f"读不了规则书 {target}",
            hint="它应当随产品一起分发；缺失说明安装不完整",
        ) from exc
    if not isinstance(book, dict) or not book.get("rules"):
        raise RuleError(f"规则书 {target} 形状不对", hint="顶层应当有 rules 数组")
    return book


class RuleEngine:
    """把规则书跑在一个实例上。"""

    def __init__(self, rulebook: dict[str, Any] | None = None) -> None:
        self._book = rulebook if rulebook is not None else load_rulebook()

    @property
    def rule_count(self) -> int:
        return len(self._book["rules"])

    @property
    def derived_from(self) -> dict[str, Any]:
        return self._book.get("derived_from", {})

    def rules_for(self, table: str | None = None) -> tuple[dict[str, Any], ...]:
        """`table=None` 取全部；给了表名就只取与它相关的（跨表不变量永远在）。"""
        rules = self._book["rules"]
        if table is None:
            return tuple(rules)
        return tuple(r for r in rules if r.get("table") in (None, table))

    def validate(self, instance: Instance, *, table: str | None = None) -> ValidationReport:
        """跑一遍。`table` 非空时只跑与那张表相关的规则（增量）。"""
        outcomes: list[RuleOutcome] = []
        for rule in self.rules_for(table):
            try:
                checked, findings = _CHECKS[rule["check"]](instance, rule)
            except RuleError as exc:
                # 规则书与引擎对不上（例：cross_table 的 id 没实现）—— 这是**产品的错**，
                # 不是数据的错。报成 error 让它显眼，但不让一条坏规则把另外 92 条一起弄瞎。
                outcomes.append(_broken(rule, "error", str(exc)))
                continue
            except (KeyError, IndexError) as exc:
                # 表或列不存在 —— 多半是数据源与规则书版本不匹配。
                # 不当成"检查通过"，也不让整个校验崩掉：报成一条 warning。
                outcomes.append(
                    RuleOutcome(
                        rule_id=rule["id"],
                        kind=rule["kind"],
                        severity="warning",
                        table=rule.get("table", ""),
                        checked=0,
                        violations=1,
                        samples=(
                            Finding(
                                rule_id=rule["id"],
                                severity="warning",
                                table=rule.get("table", ""),
                                message=f"跑不了这条规则：缺表或缺列（{exc}）",
                            ),
                        ),
                    )
                )
                continue

            outcomes.append(
                RuleOutcome(
                    rule_id=rule["id"],
                    kind=rule["kind"],
                    severity=rule["severity"],
                    table=rule.get("table", ""),
                    checked=checked,
                    violations=len(findings),
                    samples=tuple(findings[:MAX_SAMPLES]),
                )
            )
        return ValidationReport(outcomes=tuple(outcomes))


# --------------------------------------------------------------------------- #
# 各 check 的实现
# --------------------------------------------------------------------------- #


def _broken(rule: dict, severity: str, message: str) -> RuleOutcome:
    """一条跑不了的规则。**不伪装成"通过"**，也不静默丢掉。"""
    table = rule.get("table", "")
    return RuleOutcome(
        rule_id=rule["id"],
        kind=rule["kind"],
        severity=severity,
        table=table,
        checked=0,
        violations=1,
        samples=(
            Finding(
                rule_id=rule["id"],
                severity=severity,
                table=table,
                message=f"跑不了这条规则：{message}",
            ),
        ),
    )


def _table(instance: Instance, name: str) -> Table:
    return instance.table(name)


def _col(table: Table, name: str) -> int:
    return table.column_index(name)


def _ids(table: Table) -> list[int]:
    col = _col(table, ID_COLUMN)
    return [row[col] for row in table.rows]


def _check_foreign_key(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """`*Id` 列的值必须落在目标表的 Id 空间里。`0` = 不使用，跳过。"""
    src = _table(instance, rule["table"])
    target = _table(instance, rule["target"])
    col = _col(src, rule["column"])

    valid = set(_ids(target))
    src_ids = _ids(src)

    findings: list[Finding] = []
    checked = 0
    for row_id, row in zip(src_ids, src.rows, strict=False):
        value = row[col]
        if not isinstance(value, int) or isinstance(value, bool):
            continue  # 非数值列不该出现在外键上，见了也不猜
        if value == UNUSED_FK_VALUE:
            continue
        checked += 1
        if value not in valid:
            findings.append(
                Finding(
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    table=rule["table"],
                    message=(
                        f"{rule['table']}.{rule['column']} = {value}"
                        f" 不在 {rule['target']} 里（行 Id {row_id}）"
                    ),
                    row_ids=(row_id,),
                )
            )
    return checked, findings


def _check_unique_key(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """唯一键（含自然键）组合不得重复。"""
    table = _table(instance, rule["table"])
    cols = [_col(table, name) for name in rule["columns"]]
    id_col = _col(table, ID_COLUMN)

    seen: dict[tuple, list[int]] = defaultdict(list)
    for row in table.rows:
        seen[tuple(row[c] for c in cols)].append(row[id_col])

    combo = " + ".join(rule["columns"])
    findings = [
        Finding(
            rule_id=rule["id"],
            severity=rule["severity"],
            table=rule["table"],
            message=f"{combo} = {key} 重复了 {len(rows)} 次（行 Id {rows}）",
            row_ids=tuple(rows),
        )
        for key, rows in seen.items()
        if len(rows) > 1
    ]
    return len(table.rows), findings


def _check_required_per_unit(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """每个 `Units.Id` 都要有至少一行该关联表的记录。"""
    units = _table(instance, "Units")
    unit_ids = set(_ids(units))

    table = _table(instance, rule["table"])
    col = _col(table, rule["unit_column"])
    covered = {row[col] for row in table.rows}

    missing = sorted(unit_ids - covered)
    findings = [
        Finding(
            rule_id=rule["id"],
            severity=rule["severity"],
            table=rule["table"],
            message=f"单位 Id {unit_id} 没有 {rule['table']} 行",
            row_ids=(unit_id,),
            # 缺失型：没有行可指，装的是单位 Id（见 Finding 的注释）
            unit_ids=(unit_id,),
        )
        for unit_id in missing
    ]
    return len(unit_ids), findings


def _check_default_flag_required(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """每个单位的 UnitArmors 里至少一条指向 `IsDefault=true` 的装甲。

    只看**有 UnitArmors 行**的单位 —— 一行都没有的由 `req.UnitArmors` 负责，
    两条规则各管一段，不重复报。
    """
    armors = _table(instance, rule["target"])
    armor_id_col = _col(armors, ID_COLUMN)
    flag_col = _col(armors, rule["flag_column"])
    default_armors = {row[armor_id_col] for row in armors.rows if row[flag_col] is True}

    links = _table(instance, rule["table"])
    unit_col = _col(links, rule["unit_column"])
    armor_col = _col(links, "ArmorId")

    by_unit: dict[int, set[int]] = defaultdict(set)
    for row in links.rows:
        by_unit[row[unit_col]].add(row[armor_col])

    findings = [
        Finding(
            rule_id=rule["id"],
            severity=rule["severity"],
            table=rule["table"],
            message=f"单位 Id {unit_id} 的装甲里没有 IsDefault=true 的那一条",
            row_ids=(unit_id,),
            unit_ids=(unit_id,),
        )
        for unit_id, armor_ids in sorted(by_unit.items())
        if not (armor_ids & default_armors)
    ]
    return len(by_unit), findings


def _check_constant_zero(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """某一列在原版里恒为定值（例：`Options.Turret20Id` 恒为 0，是保留槽位）。"""
    table = _table(instance, rule["table"])
    col = _col(table, rule["column"])
    expected = rule["expected"]
    id_col = _col(table, ID_COLUMN)

    findings = [
        Finding(
            rule_id=rule["id"],
            severity=rule["severity"],
            table=rule["table"],
            message=(
                f"{rule['table']}.{rule['column']} = {row[col]!r}，"
                f"原版恒为 {expected!r}（行 Id {row[id_col]}）"
            ),
            row_ids=(row[id_col],),
        )
        for row in table.rows
        if row[col] != expected
    ]
    return len(table.rows), findings


def _is_number(value: object) -> bool:
    """数值才算 —— `bool` 是 `int` 的子类，和 `_check_foreign_key` 同一口径：见了也不猜。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_text(value: object) -> str:
    """读数写法：`0.0` 打成人读的 `0`（判据里出现的是同一个数）。"""
    return f"{float(value):g}"


def _bound_text(value: object, side: str) -> str:
    """区间的一端；`None` = 这一边不设限。"""
    if value is None:
        return "-∞" if side == "lo" else "+∞"
    return f"{float(value):g}"


def _terms_text(terms: list[tuple[str, float]]) -> str:
    """把 `[(列, 系数)]` 渲染成人读的一行：系数 1 不写，负数写成减号。"""
    parts: list[str] = []
    for index, (column, coef) in enumerate(terms):
        body = column if abs(coef) == 1 else f"{abs(coef):g}*{column}"
        if coef < 0:
            parts.append(f"-{body}" if index == 0 else f" - {body}")
        else:
            parts.append(body if index == 0 else f" + {body}")
    return "".join(parts) or "0"


def _linear(row: list, terms: list[tuple[int, float]]) -> float | None:
    """一侧的值 = 系数乘列值再求和。**碰到不认识的取值就返回 None**（调用方跳过这一行）。"""
    total = 0.0
    for col, coef in terms:
        value = row[col]
        if not _is_number(value):
            return None
        total += coef * float(value)
    return total


def _check_range(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """单字段区间：`lo <= v <= hi`，**两端都闭**；`lo` / `hi` 任一为 `None` 就是单边。

    `required` 是**出处元数据**，不参与判定（口径 C：本产品一律按闭环 `[lo, hi]` 走）。
    `sentinels` 里的值是**哨兵**：`0` / `-1` / `5.0` 这类"这一列对这一行不适用"的占位，
    命中就跳过、不算 `checked`。哨兵是**逐列枚举并带证据**的（每个值的命中行数写在
    `core/rules/range_bounds.json` 里，也抄进规则的 `note`）——
    ⛔ 不是"值为 0 就过"这种无脑分支：`sentinels` 缺失或为空的规则仍然严格判。
    """
    table = _table(instance, rule["table"])
    col = _col(table, rule["column"])
    id_col = _col(table, ID_COLUMN)
    lo = rule.get("lo")
    hi = rule.get("hi")
    if lo is None and hi is None:
        raise RuleError(
            f"区间规则 {rule['id']} 的两端都是 null",
            hint="两端都不设限的区间没有约束力 —— 规则书生成错了",
        )

    sentinels = rule.get("sentinels") or ()
    allowed = f"[{_bound_text(lo, 'lo')}, {_bound_text(hi, 'hi')}]"
    findings: list[Finding] = []
    checked = 0
    for row in table.rows:
        value = row[col]
        if not _is_number(value):
            continue  # 非数值列不该出现在区间上
        if value in sentinels:
            continue  # 哨兵 = 这一列对这一行不适用（逐列枚举，见规则的 sentinels / note）
        checked += 1
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            findings.append(
                Finding(
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    table=rule["table"],
                    message=(
                        f"{rule['table']}.{rule['column']} = {_number_text(value)}"
                        f" 不在 {allowed} 里（行 Id {row[id_col]}）"
                    ),
                    row_ids=(row[id_col],),
                )
            )
    return checked, findings


#: `cross_range` 认的比较符。**只放这四个** —— 多一个就多一处没人测的分支。
_RELATIONS = {
    "<=": operator.le,
    "<": operator.lt,
    ">=": operator.ge,
    ">": operator.gt,
}


def _max_of(row: list, cols: list[int]) -> float | None:
    """这几列取最大值。**碰到不认识的取值就返回 None**（调用方跳过这一行）。"""
    best: float | None = None
    for col in cols:
        value = row[col]
        if not _is_number(value):
            return None
        number = float(value)
        best = number if best is None else max(best, number)
    return best


def _check_cross_range(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """同一行内两侧线性式的比较，形状：

    ```
    左式 = Σ(系数·left_terms)
    右式 = Σ(系数·right_terms) + right_const + right_scale·max(right_max_of)
    判定 = 左式 <relation> 右式
    ```

    三段都可以缺省，于是同一套形状能表达三种规则：

    - `MinimalRange <= GroundRange` —— 左一项、右一项；
    - `SmokeDuration + SmokeFadeDuration <= 120` —— 左两项、右空 + 常数；
    - `MinimalRange < 0.9·max(GroundRange, LowAltRange, HighAltRange)` —— 右侧的 max 项
      （`right_max_of` + `right_scale`，列数不写死）。

    `gate_max_of` / `gate_gt` 是**前置条件**：只有 `max(gate_max_of) > gate_gt` 的行才判，
    其余行跳过（= 这一行不适用）。缺省时没有前置条件、每行都判。
    """
    table = _table(instance, rule["table"])
    id_col = _col(table, ID_COLUMN)

    left_named = [(str(name), float(coef)) for name, coef in rule.get("left_terms", [])]
    right_named = [(str(name), float(coef)) for name, coef in rule.get("right_terms", [])]
    max_named = [str(name) for name in rule.get("right_max_of", [])]
    if not left_named and not right_named and not max_named:
        raise RuleError(
            f"跨字段区间 {rule['id']} 的两侧都是空的",
            hint="两侧都没有列的规则没有约束力 —— 规则书生成错了",
        )
    relation = str(rule.get("relation", "<="))
    compare = _RELATIONS.get(relation)
    if compare is None:
        raise RuleError(
            f"跨字段区间 {rule['id']} 的比较符 {relation!r} 不认识",
            hint=f"只认 {sorted(_RELATIONS)}",
        )
    right_const = float(rule.get("right_const", 0.0))
    right_scale = float(rule.get("right_scale", 1.0))
    gate_named = [str(name) for name in rule.get("gate_max_of", [])]
    gate_gt = float(rule.get("gate_gt", 0.0))

    left_cols = [(_col(table, name), coef) for name, coef in left_named]
    right_cols = [(_col(table, name), coef) for name, coef in right_named]
    max_cols = [_col(table, name) for name in max_named]
    gate_cols = [_col(table, name) for name in gate_named]

    left_text = _terms_text(left_named) if left_named else "0"
    right_parts: list[str] = []
    if right_named:
        right_parts.append(_terms_text(right_named))
    if max_named:
        inner = f"max({', '.join(max_named)})"
        right_parts.append(inner if right_scale == 1 else f"{right_scale:g}*{inner}")
    if right_const:
        right_parts.append(f"{right_const:g}")
    right_text = " + ".join(right_parts) or "0"
    # 前置条件只写在报文**末尾**一次 —— 混在右式里会让报文出现两遍同样的括号。
    gate_text = (
        f"（仅当 max({', '.join(gate_named)}) > {gate_gt:g} 时才判这一行）" if gate_named else ""
    )

    findings: list[Finding] = []
    checked = 0
    for row in table.rows:
        if gate_cols:
            gate = _max_of(row, gate_cols)
            if gate is None or not gate > gate_gt:
                continue  # 前置条件不成立 = 这一行不适用（原文：num > 0.0 才比）
        left_value = _linear(row, left_cols)
        right_value = _linear(row, right_cols)
        max_value = _max_of(row, max_cols) if max_cols else 0.0
        if left_value is None or right_value is None or max_value is None:
            continue  # 有不认识的取值（缺值 / 非数值）—— 不猜
        checked += 1
        effective_right = right_value + right_const + right_scale * max_value
        if compare(left_value, effective_right):
            continue
        findings.append(
            Finding(
                rule_id=rule["id"],
                severity=rule["severity"],
                table=rule["table"],
                message=(
                    f"行 Id {row[id_col]}：{left_text}（= {_number_text(left_value)}）"
                    f" {relation} {right_text}（= {_number_text(effective_right)}）"
                    f" 不成立{gate_text}"
                ),
                row_ids=(row[id_col],),
            )
        )
    return checked, findings


def _check_cross_table(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """4 条跨表不变量 —— 每条形态都不同，按 id 分派。"""
    handler = _CROSS_HANDLERS.get(rule["id"])
    if handler is None:
        raise RuleError(
            f"跨表不变量 {rule['id']} 没有实现",
            hint="规则书里出现了引擎不认识的 id —— 两边版本不一致",
        )
    return handler(instance, rule)


def _cross_squadmembers_subset(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """步兵：SquadMembers 的武器必须都在**同单位**的 SquadWeapons 里（单向包含）。"""
    squad_weapons = _table(instance, "SquadWeapons")
    sw_unit = _col(squad_weapons, "UnitId")
    sw_weapon = _col(squad_weapons, "WeaponId")
    available: dict[int, set[int]] = defaultdict(set)
    for row in squad_weapons.rows:
        available[row[sw_unit]].add(row[sw_weapon])

    members = _table(instance, "SquadMembers")
    sm_unit = _col(members, "UnitId")
    sm_id = _col(members, ID_COLUMN)
    weapon_cols = [_col(members, name) for name in ("PrimaryWeaponId", "SpecialWeaponId")]

    findings: list[Finding] = []
    checked = 0
    for row in members.rows:
        unit_id = row[sm_unit]
        for col in weapon_cols:
            value = row[col]
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            if value == UNUSED_FK_VALUE:
                continue
            checked += 1
            if value not in available.get(unit_id, set()):
                findings.append(
                    Finding(
                        rule_id=rule["id"],
                        severity=rule["severity"],
                        table="SquadMembers",
                        message=(
                            f"单位 Id {unit_id} 的 SquadMembers（行 Id {row[sm_id]}）"
                            f"带武器 {value}，但该单位的 SquadWeapons 里没有它"
                        ),
                        row_ids=(row[sm_id],),
                    )
                )
    return checked, findings


def _cross_armor_men(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """步兵护甲名 `Armor Infantry <N>Men` 的 N 应等于该单位的 SquadMembers 行数。

    只对**名字匹配上**的单位生效；别的（车辆等）本来就不适用，
    报出来只会制造噪音。
    """
    members = _table(instance, "SquadMembers")
    sm_unit = _col(members, "UnitId")
    men_count: dict[int, int] = defaultdict(int)
    for row in members.rows:
        men_count[row[sm_unit]] += 1

    armors = _table(instance, "Armors")
    armor_id = _col(armors, ID_COLUMN)
    armor_name = _col(armors, "Name")
    names = {row[armor_id]: row[armor_name] for row in armors.rows}
    default_armor = {row[armor_id] for row in armors.rows if row[_col(armors, "IsDefault")] is True}

    links = _table(instance, "UnitArmors")
    ua_unit = _col(links, "UnitId")
    ua_armor = _col(links, "ArmorId")
    by_unit: dict[int, set[int]] = defaultdict(set)
    for row in links.rows:
        by_unit[row[ua_unit]].add(row[ua_armor])

    findings: list[Finding] = []
    checked = 0
    for unit_id, armor_ids in sorted(by_unit.items()):
        if unit_id not in men_count:
            continue
        chosen = armor_ids & default_armor or armor_ids
        matched = None
        for armor_id in sorted(chosen):
            name = names.get(armor_id)
            if isinstance(name, str):
                match = _ARMOR_MEN_RE.match(name)
                if match:
                    matched = (armor_id, name, int(match.group(1)))
                    break
        if matched is None:
            continue
        armor_id, name, declared = matched
        checked += 1
        actual = men_count[unit_id]
        if declared != actual:
            findings.append(
                Finding(
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    table="UnitArmors",
                    message=(
                        f"单位 Id {unit_id} 用「{name}」（声明 {declared} 人），"
                        f"但 SquadMembers 有 {actual} 行"
                    ),
                    # 这条问题出在这个单位身上，不落在某一行 UnitArmors 上：
                    # 光装 row_ids 会让卡片把它归到**另一个**单位头上（实测踩过）。
                    row_ids=(unit_id,),
                    unit_ids=(unit_id,),
                )
            )
    return checked, findings


def _cross_transport_capacity(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """载具的 `InfantrySlots` >= 乘客单位的 SquadMembers 行数。

    配对方式：`TransportAvailabilities(SpecializationAvailabilityId, UnitId)` 里
    `UnitId` 是**载具**，被指向的那条 `SpecializationAvailabilities.UnitId` 是**乘客**。
    """
    spec_avail = _table(instance, "SpecializationAvailabilities")
    sa_id = _col(spec_avail, ID_COLUMN)
    sa_unit = _col(spec_avail, "UnitId")
    passenger_of = {row[sa_id]: row[sa_unit] for row in spec_avail.rows}

    units = _table(instance, "Units")
    unit_id_col = _col(units, ID_COLUMN)
    slots_col = _col(units, "InfantrySlots")
    slots = {row[unit_id_col]: row[slots_col] for row in units.rows}

    members = _table(instance, "SquadMembers")
    sm_unit = _col(members, "UnitId")
    men_count: dict[int, int] = defaultdict(int)
    for row in members.rows:
        men_count[row[sm_unit]] += 1

    transports = _table(instance, "TransportAvailabilities")
    ta_id = _col(transports, ID_COLUMN)
    ta_spec = _col(transports, "SpecializationAvailabilityId")
    ta_unit = _col(transports, "UnitId")

    findings: list[Finding] = []
    checked = 0
    for row in transports.rows:
        passenger = passenger_of.get(row[ta_spec])
        if passenger is None:
            continue
        men = men_count.get(passenger, 0)
        if men == 0:
            continue  # 乘客不是步兵，这条不适用
        carrier = row[ta_unit]
        capacity = slots.get(carrier)
        if not isinstance(capacity, int):
            continue
        checked += 1
        if capacity < men:
            findings.append(
                Finding(
                    rule_id=rule["id"],
                    severity=rule["severity"],
                    table="TransportAvailabilities",
                    message=(
                        f"载具 Id {carrier} 只有 {capacity} 个步兵位，"
                        f"乘客 Id {passenger} 要坐 {men} 人（行 Id {row[ta_id]}）"
                    ),
                    row_ids=(row[ta_id],),
                )
            )
    return checked, findings


def _cross_weapons_no_orphan(instance: Instance, rule: dict) -> tuple[int, list[Finding]]:
    """每件武器要么走炮塔（TurretWeapons）要么走班组（SquadWeapons），不能两处都不在。"""
    weapons = _table(instance, "Weapons")
    weapon_ids = _ids(weapons)

    turret = _table(instance, "TurretWeapons")
    in_turret = {row[_col(turret, "WeaponId")] for row in turret.rows}
    squad = _table(instance, "SquadWeapons")
    in_squad = {row[_col(squad, "WeaponId")] for row in squad.rows}

    orphans = [w for w in weapon_ids if w not in in_turret and w not in in_squad]
    findings = [
        Finding(
            rule_id=rule["id"],
            severity=rule["severity"],
            table="Weapons",
            message=f"武器 Id {weapon_id} 既不在 TurretWeapons 也不在 SquadWeapons 里",
            row_ids=(weapon_id,),
        )
        for weapon_id in orphans
    ]
    return len(weapon_ids), findings


#: check 名 -> 实现。
_CHECKS = {
    "foreign_key": _check_foreign_key,
    "unique_key": _check_unique_key,
    "required_per_unit": _check_required_per_unit,
    "default_flag_required": _check_default_flag_required,
    "constant_zero": _check_constant_zero,
    "range": _check_range,
    "cross_range": _check_cross_range,
    "cross_table": _check_cross_table,
}

#: 跨表不变量：规则 id -> 实现。**必须与 tools/gen_rulebook.py 的 CROSS_TABLE_IDS 一致。**
_CROSS_HANDLERS = {
    "inv.infantry.squadmembers_subset_squadweapons": _cross_squadmembers_subset,
    "inv.infantry.armor_men": _cross_armor_men,
    "inv.transport.capacity": _cross_transport_capacity,
    "inv.weapons.no_orphan": _cross_weapons_no_orphan,
}
