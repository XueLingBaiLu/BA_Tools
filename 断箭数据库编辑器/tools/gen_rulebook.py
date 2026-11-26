"""把数据侧的读数蒸馏成**产品自带**的规则书（core/rules/rulebook.json）。

## 为什么要这一步

数据侧交付的 _rules.json（93 条）确实"机器可读"，但**可读的是策略，不是结构**：
每条规则只有 {id, kind, table, expr, severity, evidence, note}，而 expr 是中文散文
（"CountryId -> Countries"、"唯一键 = UnitId + AbilityId"）。真正的结构在另一个文件
_relations.json 里（fk[].name_guess、joins[].combo）。

还有第二个问题：_rules.json 住在**数据源目录**里（DataBaseCompiled/_rules.json），
而用户可以用「打开…」直接开 BA_Mod.zip / DataBaseCompiled.json / data.unity3d ——
那三种来源旁边没有这个文件，于是「实时校验」会整块消失。

所以：本脚本按 id 把两份文件对上、把结构展开，产出一份**产品自带**的规则书。
产品运行时只读自己这份，不再依赖数据源旁边的文件。

还有第三份输入：**数值区间参照** `core/rules/range_bounds.json`（`range` /
`cross_range` 两类规则的唯一来源）。它**不在数据源里** —— 出处是 BARO 5.9.2 的
`RealStats.Bounds`（见那个文件自己的 `source` 段：文件、行号、证据等级、逐条实测读数）。
它跟 `_rules.json` 一样是**生成器的输入**，不是运行时读的东西。

## 不静默丢规则

数据侧几条进就几条出。任何一条既没有结构、也不在"引擎按 id 实现"的白名单里，
**直接报错退出**。少一条的后果是"某个约束形同不存在"，而它不会以任何方式表现出来。
区间规则同理：`range_bounds.json` 里每一条都必须在产物里出现，id 撞了也报错。

## 用法

    python tools/gen_rulebook.py                    # 自动找 DataBaseCompiled
    python tools/gen_rulebook.py --source <目录>
    python tools/gen_rulebook.py --check            # 只校验不写盘（CI/回归用）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from ba_mod_maker.core.asset.tables import TABLE_NAMES, resolve_table_name  # noqa: E402
from ba_mod_maker.core.data.loader import find_dataset_root  # noqa: E402

#: 引擎**按 id 实现**的规则 —— 它们没有可以展开的通用结构。
#: 每一条都要在 core/rules/engine.py 里有对应的分支，否则校验时会抛。
CROSS_TABLE_IDS = frozenset(
    {
        "inv.infantry.squadmembers_subset_squadweapons",
        "inv.infantry.armor_men",
        "inv.transport.capacity",
        "inv.weapons.no_orphan",
    }
)

#: 结构不在 _relations.json 里的那一条（列名不含表名，靠数值判定出来的）。
#:
#: ⚠️ 值写成**数据集侧**的写法（`PlaneFlyPresets`）没关系 —— 下面 `_fk_target()` 会
#: 经 `resolve_table_name` 归一到 `core/asset/tables.py` 的规范表名（`FlyPresets`）。
#: 规则书里**只许出现规范名**：写文件名那一套会让这条规则在一半来源上永远跑不了
#: （实测 2026-10-01：目录来源 699 warning、zip 来源 700，差的就是它）。
MANUAL_FK = {("Mobility", "FlyPresetId"): "PlaneFlyPresets"}

DEFAULT_OUT = REPO / "src" / "ba_mod_maker" / "core" / "rules" / "rulebook.json"

#: 数值区间参照（**带出处**）—— `range` / `cross_range` 两类规则唯一的输入。
#: 与 `_relations.json` / `_rules.json` 同级：**生成器的输入**，不是运行时读的文件。
BOUNDS_PATH = REPO / "src" / "ba_mod_maker" / "core" / "rules" / "range_bounds.json"

#: 区间规则的严重度。⛔ **不许改成 error**：参照是 **mod 作者自定的合理区间**，
#: 不是游戏官方限制，而**原版数据本身就大面积在区间外**（实测 52085：18 条区间里
#: 2753 行越界、2 条跨字段里 58 行不成立）。判成 error 会让 `validate` 在
#: **一个字节都没改过的原版**上退出码 1 —— 那正是 engine.py 里 `RuleDelta` 那段
#: 「一打开就满屏红的校验等于没有校验」说的病。
BOUNDS_SEVERITY = "warning"


class GenError(Exception):
    """规则书生成失败。带可读的原因，不做静默降级。"""


def _load(path: Path) -> object:
    if not path.is_file():
        raise GenError(f"缺少输入文件：{path}")
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise GenError(f"读不了 {path}：{exc}") from exc


def _fk_target(relations: dict, table: str, column: str) -> str | None:
    """这条外键指向哪张表 —— **返回值一定是规范表名**。

    数据侧（`_relations.json` 的 `name_guess`、`_schema.json` 的键）用的是**文件干**
    那一套叫法，那张唯一改过名的表在那里叫 `PlaneFlyPresets`；规则书要用规范名
    `FlyPresets`。归一交给 `core/asset/tables.py::resolve_table_name`（别名表只有那一份），
    认不出来就**报错**而不是把野名字写进规则书 —— 那种规则在运行时只会静默降级成
    一条「跑不了这条规则」的 warning（实测踩过）。
    """
    manual = MANUAL_FK.get((table, column))
    if manual:
        guess: object = manual
    else:
        guess = None
        for entry in relations.get("fk", []):
            if entry.get("table") == table and entry.get("column") == column:
                guess = entry.get("name_guess")
                break
    if not guess:
        return None
    target = resolve_table_name(str(guess), TABLE_NAMES)
    if target is None:
        raise GenError(f"{table}.{column}：目标表 {guess!r} 不在 core/asset/tables.py 的权威清单里")
    return target


def _enrich(rule: dict, relations: dict) -> dict:
    """把一条散文规则展开成结构化规则。展开不了就抛，不返回 None。"""
    rid = rule["id"]
    kind = rule["kind"]
    out = {
        "id": rid,
        "kind": kind,
        "severity": rule["severity"],
        "evidence": rule.get("evidence", ""),
        "note": rule.get("note", ""),
        # `expr` / `note` 是**数据侧的散文，原样搬**（不归一）：里面可能写着数据集侧的
        # 表名（例：fk.Mobility.FlyPresetId 的 expr 里是 `PlaneFlyPresets`），
        # 而 `target` 只出现规范名。散文不当判据用 —— 判据是 `table` / `target`
        # （tests/test_table_naming.py 就只扫这两个字段）。
        "expr": rule.get("expr", ""),
    }

    if kind == "foreign_key":
        _, table, column = rid.split(".", 2)
        target = _fk_target(relations, table, column)
        if target is None:
            raise GenError(f"{rid}：展开不出目标表（_relations.json 里没有 name_guess）")
        out |= {"check": "foreign_key", "table": table, "column": column, "target": target}

    elif kind == "unique_key":
        table = rid.split(".", 1)[1]
        join = relations.get("joins", {}).get(table)
        if not join or not join.get("combo"):
            raise GenError(f"{rid}：_relations.json 里没有 {table} 的唯一键组合")
        out |= {"check": "unique_key", "table": table, "columns": list(join["combo"])}

    elif kind == "natural_key":
        body = rid.split(".", 1)[1]
        table, columns_text = body.rsplit(".", 1)
        columns = columns_text.split("_")
        # 自校验：拆出来的列必须是 _relations 里某条 ok=true 的组合。
        # （列名若含下划线，这里会拆错 —— 与其静默错，不如在这里炸。）
        combos = [
            e["combo"] for e in relations.get("natural_key", {}).get(table, []) if e.get("ok")
        ]
        if columns not in combos:
            raise GenError(f"{rid}：拆出的列 {columns} 不在 {table} 的实测自然键 {combos} 里")
        out |= {"check": "unique_key", "table": table, "columns": columns}

    elif kind == "row_required_per_unit":
        table = rid.split(".", 1)[1]
        out |= {"check": "required_per_unit", "table": table, "unit_column": "UnitId"}

    elif kind == "default_flag_required":
        out |= {
            "check": "default_flag_required",
            "table": "UnitArmors",
            "unit_column": "UnitId",
            "target": "Armors",
            "flag_column": "IsDefault",
        }

    elif kind == "unused_slot":
        _, table, column = rid.split(".", 2)
        out |= {"check": "constant_zero", "table": table, "column": column, "expected": 0}

    elif kind == "cross_table":
        if rid not in CROSS_TABLE_IDS:
            raise GenError(f"{rid}：cross_table 规则没有在引擎里实现")
        out |= {"check": "cross_table"}

    else:
        raise GenError(f"{rid}：未知的 kind {kind!r}")

    return out


def _canonical(table: str, rid: str) -> str:
    """表名归一 —— 与 `_fk_target` 同一口径：**只许规范名进规则书**。"""
    target = resolve_table_name(table, TABLE_NAMES)
    if target is None:
        raise GenError(f"{rid}：表 {table!r} 不在 core/asset/tables.py 的权威清单里")
    return target


def _bound_text(value: object, side: str) -> str:
    if value is None:
        return "-∞" if side == "lo" else "+∞"
    return f"{float(value):g}"


def _terms_text(terms: list) -> str:
    """把 [(列, 系数)] 渲染成人读的一行。

    ⚠️ 这只是 `expr`（给人看的散文），**判据在
    `core/rules/engine.py::_check_cross_range`** —— 与 `_enrich` 里
    "散文不当判据用"同一条界线：渲染可以有两份，判断只有一处。
    """
    parts: list[str] = []
    for index, (column, coef) in enumerate(terms):
        body = str(column) if abs(float(coef)) == 1 else f"{abs(float(coef)):g}*{column}"
        if float(coef) < 0:
            parts.append(f"-{body}" if index == 0 else f" - {body}")
        else:
            parts.append(body if index == 0 else f" + {body}")
    return "".join(parts) or "0"


def _bounds_rules(bounds: dict) -> list[dict]:
    """`range_bounds.json` -> 规则。**逐条都要出**，出不来就报错，不静默少一条。"""
    source = bounds.get("source", {})
    evidence = str(source.get("evidence_level", "mod-authored"))
    origin = (
        f"{source.get('mod', '?')} {source.get('version', '?')}"
        f" 的 RealStats.Bounds（decompiled.cs:{source.get('lines', '?')}）"
    )

    rules: list[dict] = []

    for entry in bounds.get("ranges", []):
        table = _canonical(str(entry["table"]), f"range.{entry['table']}.{entry['column']}")
        column = str(entry["column"])
        lo, hi = entry.get("lo"), entry.get("hi")
        rid = f"range.{table}.{column}"
        if lo is None and hi is None:
            raise GenError(f"{rid}：区间两端都是 null —— 这条规则没有约束力")
        hits = entry.get("vanilla_hits_52085", "?")
        after = entry.get("vanilla_hits_after_sentinels_52085", "?")
        sentinels = entry.get("sentinels", [])
        note = (
            f"出处 {origin}；原版 52085 实测越界 {hits} 行（**原版既有形态**，不是改动引入的），"
            f"跳过哨兵后 {after} 行。"
        )
        if sentinels:
            listed = "、".join(
                f"{float(s['value']):g}（{s.get('rows_52085', '?')} 行）" for s in sentinels
            )
            note += f"哨兵（逐列枚举，括号里是原版命中行数）：{listed}。"
        if not entry.get("required", True):
            note += (
                " required=false：BARO 原判据还把下界当开区间，"
                "本产品按 [lo, hi] 闭环执行（口径 C）。"
            )
        rules.append(
            {
                "id": rid,
                "kind": "range",
                "severity": BOUNDS_SEVERITY,
                "evidence": evidence,
                "note": note,
                "expr": f"{column} 必须落在 [{_bound_text(lo, 'lo')}, {_bound_text(hi, 'hi')}] 里",
                "check": "range",
                "table": table,
                "column": column,
                "lo": lo,
                "hi": hi,
                "required": bool(entry.get("required", True)),
                # 哨兵是**逐列枚举**的；缺省 = 空 = 这一列严格判（不是"值为 0 就过"）。
                "sentinels": [s["value"] for s in sentinels],
            }
        )

    for entry in bounds.get("cross_ranges", []):
        table = _canonical(str(entry["table"]), f"cross.{entry['table']}.{entry['name']}")
        rid = f"cross.{table}.{entry['name']}"
        left = [list(term) for term in entry.get("left_terms", [])]
        right = [list(term) for term in entry.get("right_terms", [])]
        const = float(entry.get("right_const", 0.0))
        max_of = [str(name) for name in entry.get("right_max_of", [])]
        scale = float(entry.get("right_scale", 1.0))
        gate_of = [str(name) for name in entry.get("gate_max_of", [])]
        gate_gt = float(entry.get("gate_gt", 0.0))
        if not left and not right and not max_of:
            raise GenError(f"{rid}：两侧都没有列 —— 这条规则没有约束力")
        # 写了不生效的字段 = 以为加了约束其实没有 —— 与其静默，不如在这里炸。
        if not max_of and scale != 1.0:
            raise GenError(f"{rid}：写了 right_scale 却没有 right_max_of —— 那个系数不会生效")
        if not gate_of and gate_gt:
            raise GenError(f"{rid}：写了 gate_gt 却没有 gate_max_of —— 那个前置条件不会生效")

        right_parts: list[str] = []
        if right:
            right_parts.append(_terms_text(right))
        if max_of:
            inner = f"max({', '.join(max_of)})"
            right_parts.append(inner if scale == 1 else f"{scale:g}*{inner}")
        if const:
            right_parts.append(f"{const:g}")
        right_text = " + ".join(right_parts) or "0"
        left_text = _terms_text(left) if left else "0"
        relation = str(entry.get("relation", "<="))
        if gate_of:
            right_text += f"（仅当 max({', '.join(gate_of)}) > {gate_gt:g} 时判）"

        prose = str(entry.get("evidence_note", "")).strip()
        hits = entry.get("vanilla_hits_52085", "?")
        gated = entry.get("gated_rows_52085")
        tail = f"原版 52085 实测不成立 {hits} 行。"
        if gated is not None:
            tail += f"过了前置条件的 {gated} 行里违 {hits} 行。"
        rules.append(
            {
                "id": rid,
                "kind": "cross_range",
                "severity": BOUNDS_SEVERITY,
                "evidence": str(entry.get("evidence_level", evidence)),
                "note": (f"{prose} " if prose else "") + tail,
                "expr": f"{left_text} {relation} {right_text}",
                "check": "cross_range",
                "table": table,
                "left_terms": left,
                "right_terms": right,
                "right_const": const,
                "right_max_of": max_of,
                "right_scale": scale,
                "gate_max_of": gate_of,
                "gate_gt": gate_gt,
                "relation": relation,
            }
        )

    return rules


def build(source: Path) -> dict:
    relations = _load(source / "_relations.json")
    rules_doc = _load(source / "_rules.json")
    if not isinstance(relations, dict) or not isinstance(rules_doc, dict):
        raise GenError("输入文件顶层不是对象")

    instances = relations.get("instances", {})
    # 结构取自**默认非战役实例**（_rules.json 的 instances 段给出是哪个）。
    meta = rules_doc.get("instances", {})
    default_id = str(meta.get("default_non_campaign", ""))
    if default_id not in instances:
        raise GenError(f"_relations.json 里没有实例 {default_id!r}；有：{sorted(instances)}")
    inst = instances[default_id]

    rules_in = rules_doc.get("rules", [])
    rules_out = [_enrich(rule, inst) for rule in rules_in]

    if len(rules_out) != len(rules_in):
        raise GenError("规则条数在展开过程中变了 —— 这不该发生")

    bounds = _load(BOUNDS_PATH)
    if not isinstance(bounds, dict):
        raise GenError(f"{BOUNDS_PATH} 顶层不是对象")
    extra = _bounds_rules(bounds)
    clash = sorted({r["id"] for r in extra} & {r["id"] for r in rules_out})
    if clash:
        raise GenError(f"区间规则的 id 与数据侧规则撞了：{clash}")
    rules_out.extend(extra)

    baseline = rules_doc.get("baseline", {})
    return {
        "schema": 1,
        "derived_from": {
            "relations": "_relations.json",
            "rules": "_rules.json",
            "bounds": "core/rules/range_bounds.json",
            "instance": default_id,
            "note": (
                "由 tools/gen_rulebook.py 生成，**不要手改**。游戏更新后重跑。"
                f"前 {len(rules_in)} 条来自数据侧 _rules.json；"
                f"后 {len(extra)} 条（range / cross_range）来自 core/rules/range_bounds.json。"
            ),
        },
        "instances": meta,
        "baseline_rows": baseline.get("rows_52085", {}),
        "rule_count": len(rules_out),
        "rules": rules_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成产品自带的规则书")
    parser.add_argument("--source", type=Path, default=None, help="DataBaseCompiled 目录")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出路径")
    parser.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = parser.parse_args()

    source = args.source
    if source is None:
        found = find_dataset_root(REPO)
        if found is None:
            print("找不到 DataBaseCompiled 目录，请用 --source 指定", file=sys.stderr)
            return 2
        source = found

    try:
        book = build(source)
    except GenError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1

    from collections import Counter

    kinds = Counter(r["kind"] for r in book["rules"])
    checks = Counter(r["check"] for r in book["rules"])
    print(f"规则 {book['rule_count']} 条 / 来源实例 {book['derived_from']['instance']}")
    print("  kind :", dict(sorted(kinds.items())))
    print("  check:", dict(sorted(checks.items())))

    text = json.dumps(book, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        old = args.out.read_text(encoding="utf-8") if args.out.is_file() else ""
        if old != text:
            print(f"规则书与产物不一致：{args.out}", file=sys.stderr)
            return 1
        print("规则书与产物一致（--check）")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print("写出", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
