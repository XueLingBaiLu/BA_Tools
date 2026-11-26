r"""tools/dump_diff.py 的用例：**正控**（尺子必须能红）+ 负控（同一份数据 0/0/0）。

判据一行：

    .venv\Scripts\python.exe -m pytest tests/test_dump_diff.py -q
    -> 四条正控**各自只动一个读数**：改一格值 / 翻一个 bool -> 「值不同」+1；
       删一行 -> 「只离线有」+1；加一行 -> 「只在线有」+1。
       什么都不动的负控是「表 24 / 只离线有 0 行 / 只在线有 0 行 / 值不同 0 格」。

为什么必须做正控：「两个一样的文件不报错」这件事，一把**永远绿的尺子**也能做到 ——
所以每条用例都先喂一个**已知差异**进去，看它报不报、报几条（多报一条即红）。

Lead 2026-10-10 新增的两条口径（本文件各有正控）：

1. `probe.computed_columns` 里声明的**现算列**不参与「值不同」，但必须出现在
   `[现算列]` 那一节里（排除要说出来）；字段缺失 / 空数组 -> 照常全量比对。
2. 同一列的值不同超过 20 格 -> 打印折成「列 X：N 格（前 5 例）」，计数不变。

夹具的两条口径：

1. **数据取自只读的** `DataBaseCompiled/52085`（用例一个字节都不写它）；
2. **dump 一律先落盘再读回**（`_report` 里走的就是 CLI 那条路：
   json.dumps -> read_dump -> compare），不做"直接喂内存字典"的旁路。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
#: tools/ 不在包里 —— 与 tests/ 下其他"戳脚本"的用例同款，运行时挂 sys.path。
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import dump_diff  # noqa: E402

from ba_mod_maker.core.data import find_dataset_root  # noqa: E402

INSTANCE_ID = "52085"
#: 正控用的两列（实测：Units 540 行逐行都有它们；Cost 是数字、DisplayInArmory 是 bool）。
COST_COLUMN = "Cost"
BOOL_COLUMN = "DisplayInArmory"

ZERO_LINE = "表 24 / 只离线有 0 行 / 只在线有 0 行 / 值不同 0 格"
#: 声明成「现算列」用的那一对（实测 PlaneFlyPresets 有这一列、值是 float）。
COMPUTED_TABLE = "PlaneFlyPresets"
COMPUTED_COLUMN = "CornerSpeedYaw"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def dataset_root() -> Path:
    root = find_dataset_root(REPO_ROOT)
    if root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    return root


@pytest.fixture(scope="module")
def instance_dir(dataset_root: Path) -> Path:
    return dataset_root / INSTANCE_ID


@pytest.fixture(scope="module")
def offline_full(instance_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """真数据源 24 张表（每条用例自己 deep-copy 再改，绝不改这份）。"""
    return dump_diff.read_offline_tables(dump_diff.resolve_offline(instance_dir))


def _copy(tables: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """深拷一份 —— 走 JSON，与探针落盘/读回同一条路。"""
    return json.loads(json.dumps(tables))


def _payload(
    tables: dict[str, list[dict[str, Any]]],
    failed: tuple[str, ...] = (),
    extra: dict[str, Any] | None = None,
) -> dict:
    """按契约组一份 dump；`extra` 是额外的顶层键（例：`{"probe": {...}}`）。"""
    meta: dict[str, Any] = {
        "generated_utc": "2026-10-10T00:00:00Z",
        "mod_version": "0.1.0",
        "game_source": "compiled",
        "table_count": len(tables),
        "row_counts": {name: len(rows) for name, rows in tables.items()},
    }
    if failed:
        meta["failed_tables"] = list(failed)
    payload: dict[str, Any] = {"meta": meta, "tables": tables}
    if extra:
        payload.update(extra)
    return payload


def _write_dump(
    tmp_path: Path,
    tables: dict[str, list[dict[str, Any]]],
    failed: tuple[str, ...] = (),
    name: str = "dump.json",
    extra: dict[str, Any] | None = None,
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(_payload(tables, failed, extra), ensure_ascii=False), encoding="utf-8"
    )
    return path


def _report(
    tmp_path: Path,
    offline: dict[str, list[dict[str, Any]]],
    dump_tables: dict[str, list[dict[str, Any]]],
    failed: tuple[str, ...] = (),
    extra: dict[str, Any] | None = None,
) -> dump_diff.DiffReport:
    """落盘 -> read_dump -> compare（与 CLI 同一份逻辑，不另开旁路）。"""
    path = _write_dump(tmp_path, dump_tables, failed, extra=extra)
    return dump_diff.compare(offline, dump_diff.read_dump(path))


def _mutate_computed(
    tables: dict[str, list[dict[str, Any]]], count: int = 2, delta: float = 1e-5
) -> list[tuple[Any, float, float]]:
    """在「现算列」上改 count 格（模拟 ulp 级派生差异），返回 (Id, 旧值, 新值)。"""
    rows = [r for r in tables[COMPUTED_TABLE] if isinstance(r.get(COMPUTED_COLUMN), float)]
    assert len(rows) >= count, f"{COMPUTED_TABLE}.{COMPUTED_COLUMN} 的 float 行不够做正控"
    touched = []
    for row in rows[:count]:
        before = row[COMPUTED_COLUMN]
        row[COMPUTED_COLUMN] = before + delta
        touched.append((row["Id"], before, row[COMPUTED_COLUMN]))
    return touched


def _table(report: dump_diff.DiffReport, name: str) -> dump_diff.TableDiff:
    return next(t for t in report.tables if t.name == name)


def _diff_tuples(report: dump_diff.DiffReport) -> list[tuple]:
    return [(d.table, d.row_id, d.column, d.offline, d.online) for d in report.value_diffs]


# --------------------------------------------------------------------------- #
# 负控：同一份数据必须 0/0/0
# --------------------------------------------------------------------------- #


def test_full_dataset_identical_is_silent(tmp_path, offline_full) -> None:
    """真数据源 24 张表原样做成 dump -> 三个读数全 0（能量出红的尺子的零点）。"""
    report = _report(tmp_path, offline_full, _copy(offline_full))
    assert report.table_count == 24
    assert report.conclusion_line() == ZERO_LINE
    assert report.value_diffs == []
    assert report.anomalies() == []
    assert {t.status for t in report.tables} == {dump_diff.STATUS_COMPARED}
    assert all(t.offline_rows == t.online_rows for t in report.tables)


def test_cli_identical_exits_zero(tmp_path, instance_dir, offline_full, capsys) -> None:
    """CLI 那条路：无差异 -> 退出码 0，结论行原样打出来，--json 里是同一批数字。"""
    dump_path = _write_dump(tmp_path, _copy(offline_full))
    out = tmp_path / "identical.json"
    rc = dump_diff.main([str(instance_dir), str(dump_path), "--json", str(out)])
    printed = capsys.readouterr().out
    assert rc == 0
    assert f"[结论] {ZERO_LINE}" in printed
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["conclusion"] == ZERO_LINE
    assert payload["totals"] == {
        "tables": 24,
        "only_offline_rows": 0,
        "only_online_rows": 0,
        "value_diffs": 0,
        "only_offline_columns": 0,
        "only_online_columns": 0,
        "excluded_value_diffs": 0,
        "computed_columns": 0,
        "anomalies": 0,
    }
    assert payload["offline_dir"] == str(instance_dir)


# --------------------------------------------------------------------------- #
# 正控：已知差异必须报出来，且一条都不许多
# --------------------------------------------------------------------------- #


def test_positive_control_reports_exactly_the_four_edits(tmp_path, offline_full) -> None:
    """正控：改一格值 + 翻一个 bool + 删一行 + 加一行 -> 2 格值不同 / 1+1 行差集。"""
    tables = _copy(offline_full)
    units = tables["Units"]

    old_cost = units[0][COST_COLUMN]
    cost_id = units[0]["Id"]
    assert isinstance(old_cost, (int, float)) and not isinstance(old_cost, bool)
    units[0][COST_COLUMN] = old_cost + 777

    old_bool = units[2][BOOL_COLUMN]
    bool_id = units[2]["Id"]
    assert isinstance(old_bool, bool)
    units[2][BOOL_COLUMN] = not old_bool

    deleted_id = units[1]["Id"]
    units.pop(1)

    added_id = max(row["Id"] for row in units) + 1
    added = dict(units[3])
    added["Id"] = added_id
    units.append(added)

    report = _report(tmp_path, offline_full, tables)
    assert report.table_count == 24
    assert report.conclusion_line() == "表 24 / 只离线有 1 行 / 只在线有 1 行 / 值不同 2 格"
    assert report.anomalies() == []
    assert [t.name for t in report.tables if t.value_diffs] == ["Units"]
    assert [t.name for t in report.tables if t.only_offline_ids or t.only_online_ids] == ["Units"]
    units_diff = _table(report, "Units")
    assert units_diff.only_offline_ids == [deleted_id]
    assert units_diff.only_online_ids == [added_id]
    assert units_diff.only_offline_columns == []
    assert units_diff.only_online_columns == []
    assert set(_diff_tuples(report)) == {
        ("Units", cost_id, COST_COLUMN, old_cost, old_cost + 777),
        ("Units", bool_id, BOOL_COLUMN, old_bool, not old_bool),
    }


@pytest.mark.parametrize("edit", ["改值", "翻bool", "删行", "加行"])
def test_each_edit_alone_moves_exactly_one_reading(tmp_path, offline_full, edit: str) -> None:
    """**不多报**：四个动作各自单独做一次 -> 恰好只动它该动的那一个读数。"""
    tables = _copy(offline_full)
    units = tables["Units"]
    expect_value: list[tuple] = []
    expect_offline: list[Any] = []
    expect_online: list[Any] = []

    if edit == "改值":
        old = units[0][COST_COLUMN]
        units[0][COST_COLUMN] = old + 777
        expect_value.append((units[0]["Id"], COST_COLUMN, old, old + 777))
    elif edit == "翻bool":
        old = units[0][BOOL_COLUMN]
        assert isinstance(old, bool)
        units[0][BOOL_COLUMN] = not old
        expect_value.append((units[0]["Id"], BOOL_COLUMN, old, not old))
    elif edit == "删行":
        expect_offline.append(units[0]["Id"])
        units.pop(0)
    else:
        new_id = max(row["Id"] for row in units) + 1
        row = dict(units[0])
        row["Id"] = new_id
        units.append(row)
        expect_online.append(new_id)

    report = _report(tmp_path, offline_full, tables)
    units_diff = _table(report, "Units")
    assert units_diff.only_offline_ids == expect_offline
    assert units_diff.only_online_ids == expect_online
    assert [(d.row_id, d.column, d.offline, d.online) for d in report.value_diffs] == expect_value
    assert report.only_offline_count == len(expect_offline)
    assert report.only_online_count == len(expect_online)
    assert report.only_offline_column_count == 0
    assert report.only_online_column_count == 0
    assert report.anomalies() == []
    touched = [
        t.name
        for t in report.tables
        if t.name != "Units" and (t.value_diffs or t.only_offline_ids or t.only_online_ids)
    ]
    assert touched == []
    assert report.conclusion_line() == (
        f"表 24 / 只离线有 {len(expect_offline)} 行"
        f" / 只在线有 {len(expect_online)} 行 / 值不同 {len(expect_value)} 格"
    )


# --------------------------------------------------------------------------- #
# 口径：单侧列 / 行缺列 / 类型变了 / 没读到的表 / 别名
# --------------------------------------------------------------------------- #


def test_one_sided_column_is_reported_once_not_per_row(tmp_path, offline_full) -> None:
    """列只在一侧存在 -> 只报在列级一次，不许按行刷成 N 格值不同。"""
    tables = _copy(offline_full)
    rows = tables["Countries"]
    assert rows, "Countries 不该是空表"
    for row in rows:
        row["ZZProbeOnly"] = 1

    report = _report(tmp_path, offline_full, tables)
    countries = _table(report, "Countries")
    assert countries.only_online_columns == ["ZZProbeOnly"]
    assert countries.only_offline_columns == []
    assert len(rows) > 1, "要在多行上报同一个新列，才测得出「只报一次」"
    assert report.value_diffs == []
    assert report.only_offline_count == 0
    assert report.only_online_count == 0
    assert report.anomalies() == []


def test_column_dropped_on_dump_side_is_reported_once(tmp_path, offline_full) -> None:
    """反方向：dump 少了我们读到的一列 -> 报「仅离线有的列」。"""
    dropped = "Hidden"
    assert dropped in offline_full["Countries"][0]
    tables = _copy(offline_full)
    for row in tables["Countries"]:
        row.pop(dropped)

    report = _report(tmp_path, offline_full, tables)
    countries = _table(report, "Countries")
    assert countries.only_offline_columns == [dropped]
    assert countries.only_online_columns == []
    assert report.value_diffs == []


def test_row_missing_a_common_column_is_a_value_diff(tmp_path, offline_full) -> None:
    """整列还在、只有一行缺 -> 按字段级报，缺的那侧显示记号（不许静默跳过）。"""
    tables = _copy(offline_full)
    row_id = tables["Units"][0]["Id"]
    expected = tables["Units"][0].pop("Name")

    report = _report(tmp_path, offline_full, tables)
    assert _diff_tuples(report) == [
        ("Units", row_id, "Name", expected, dump_diff.MISSING_CELL)
    ]
    assert _table(report, "Units").only_online_columns == []
    assert _table(report, "Units").only_offline_columns == []


def test_value_type_change_counts_as_a_diff(tmp_path, offline_full) -> None:
    """true -> 1：值「相等」但类型变了，也要报（dump 的意义是游戏里到底是什么）。"""
    tables = _copy(offline_full)
    row = tables["Units"][0]
    before = row[BOOL_COLUMN]
    assert isinstance(before, bool)
    row[BOOL_COLUMN] = int(before)

    report = _report(tmp_path, offline_full, tables)
    assert _diff_tuples(report) == [
        ("Units", row["Id"], BOOL_COLUMN, before, int(before))
    ]


def test_failed_table_is_not_counted_as_absent(tmp_path, offline_full) -> None:
    """dump 说「这张没读到」-> 记读失败 + 不算「只有离线有」（没读到 ≠ 没有）。"""
    dropped = "Specializations"
    tables = _copy(offline_full)
    missing_rows = len(tables.pop(dropped))
    assert missing_rows > 0

    report = _report(tmp_path, offline_full, tables, failed=(dropped,))
    item = _table(report, dropped)
    assert item.status == dump_diff.STATUS_DUMP_FAILED
    assert item.offline_rows == missing_rows
    assert item.only_offline_ids == []
    assert report.table_count == 23
    assert report.only_offline_count == 0
    assert report.conclusion_line() == "表 23 / 只离线有 0 行 / 只在线有 0 行 / 值不同 0 格"
    assert any("dump 读失败" in text for text in report.anomalies())
    assert report.has_differences() is True, "对比不完整就不许报「无差异」"


def test_table_missing_from_dump_without_failed_list_is_loud(tmp_path, offline_full) -> None:
    """dump 里整张表没了、也没列 failed_tables -> 记缺表 + 异常，仍不算离线独有。"""
    dropped = "Units"
    tables = _copy(offline_full)
    assert len(tables.pop(dropped)) > 0

    report = _report(tmp_path, offline_full, tables)
    item = _table(report, dropped)
    assert item.status == dump_diff.STATUS_DUMP_MISSING
    assert item.only_offline_ids == []
    assert report.only_offline_count == 0
    assert any("meta.failed_tables" in text for text in report.anomalies())


def test_table_only_in_dump_is_listed_but_not_counted(tmp_path, offline_full) -> None:
    """dump 多出来的表 -> 单列一条，且不混进「表 N」。"""
    tables = _copy(offline_full)
    tables["Ghost"] = [{"Id": 1, "Name": "ghost"}]

    report = _report(tmp_path, offline_full, tables)
    item = _table(report, "Ghost")
    assert item.status == dump_diff.STATUS_DUMP_ONLY
    assert item.online_rows == 1
    assert report.table_count == 24
    assert report.conclusion_line() == ZERO_LINE
    assert any("Ghost" in text for text in report.anomalies())
    assert report.has_differences() is True


def test_alias_table_name_in_dump_is_aligned_with_a_note(tmp_path, offline_full) -> None:
    """dump 写成外号（FlyPresets）-> 按别名对齐到 PlaneFlyPresets，另记一条异常。"""
    tables = _copy(offline_full)
    tables["FlyPresets"] = tables.pop("PlaneFlyPresets")

    report = _report(tmp_path, offline_full, tables)
    item = _table(report, "PlaneFlyPresets")
    assert item.status == dump_diff.STATUS_COMPARED
    assert item.offline_rows == item.online_rows > 0
    assert report.table_count == 24
    assert report.only_offline_count == 0
    assert report.only_online_count == 0
    assert any("FlyPresets" in text for text in report.anomalies())


def test_meta_row_counts_mismatch_is_loud(tmp_path, offline_full) -> None:
    """dump 自己的 meta 与实际行数对不上 -> 记异常（探针的 bug 不许被当成数据差异）。"""
    tables = _copy(offline_full)
    payload = _payload(tables)
    payload["meta"]["row_counts"]["Units"] = len(tables["Units"]) + 1
    payload["meta"]["table_count"] = len(tables) + 5
    path = tmp_path / "meta-bad.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = dump_diff.compare(offline_full, dump_diff.read_dump(path))
    texts = report.anomalies()
    assert any("row_counts" in t and "Units" in t for t in texts)
    assert any("table_count" in t for t in texts)
    assert report.value_diffs == []
    assert report.only_offline_count == 0
    assert report.only_online_count == 0
    assert report.has_differences() is True


# --------------------------------------------------------------------------- #
# 形状错了要喊，不许猜
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {}, "tables": "nope"},
        {"meta": {}, "tables": {"Units": 3}},
        {"meta": {}, "tables": {"Units": [1, 2]}},
        {"meta": {}, "tables": {"Units": [{"Id": 1}, ["不是对象"]]}},
    ],
)
def test_dump_shape_errors_are_loud(tmp_path, payload: dict) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(dump_diff.DumpFormatError):
        dump_diff.read_dump(path)


def test_missing_dump_file_is_loud(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        dump_diff.read_dump(tmp_path / "根本没有这个文件.json")


def test_offline_dir_accepts_root_or_instance(tmp_path, dataset_root, instance_dir) -> None:
    """两种给法都收；根下多实例又没指定 --instance 时必须报错，不许替用户猜。"""
    via_instance = dump_diff.resolve_offline(instance_dir)
    via_root = dump_diff.resolve_offline(dataset_root, instance=INSTANCE_ID)
    assert via_instance.instance_dir == via_root.instance_dir == instance_dir
    assert len(via_root.schema) == 24

    instances = [
        d for d in dataset_root.iterdir() if d.is_dir() and (d / "_manifest.json").is_file()
    ]
    if len(instances) != 1:
        with pytest.raises(ValueError):
            dump_diff.resolve_offline(dataset_root)


# --------------------------------------------------------------------------- #
# CLI 的两个旋钮：--limit 不改计数 / --table 只比那一张
# --------------------------------------------------------------------------- #


def test_limit_truncates_printing_only(tmp_path, instance_dir, offline_full, capsys) -> None:
    """--limit 1 只截断**打印**：屏幕上 1 条，结论行仍是 3 格。"""
    tables = _copy(offline_full)
    changed = []
    for row in tables["Units"][:3]:
        row[COST_COLUMN] = row[COST_COLUMN] + 1
        changed.append(row["Id"])

    dump_path = _write_dump(tmp_path, tables)
    out = tmp_path / "limited.json"
    rc = dump_diff.main([str(instance_dir), str(dump_path), "--limit", "1", "--json", str(out)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert len(changed) == 3
    printed_diffs = [line for line in printed.splitlines() if line.startswith("  Units[Id=")]
    assert len(printed_diffs) == 1
    assert "值不同 3 格" in printed
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["totals"]["value_diffs"] == 3
    assert len(payload["tables"][0]["value_diffs"]) == 3 or sum(
        len(t["value_diffs"]) for t in payload["tables"]
    ) == 3


def test_table_filter_compares_only_that_table(
    tmp_path, instance_dir, offline_full, capsys
) -> None:
    """--table 也认别名（FlyPresets -> PlaneFlyPresets），且只留那一张。"""
    dump_path = _write_dump(tmp_path, _copy(offline_full))
    out = tmp_path / "one.json"
    rc = dump_diff.main(
        [str(instance_dir), str(dump_path), "--table", "FlyPresets", "--json", str(out)]
    )
    capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [t["name"] for t in payload["tables"]] == ["PlaneFlyPresets"]
    assert payload["totals"]["tables"] == 1
    assert payload["conclusion"] == "表 1 / 只离线有 0 行 / 只在线有 0 行 / 值不同 0 格"


def test_unknown_table_filter_exits_two(tmp_path, instance_dir, offline_full, capsys) -> None:
    dump_path = _write_dump(tmp_path, _copy(offline_full))
    rc = dump_diff.main([str(instance_dir), str(dump_path), "--table", "没有这张表"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "离线数据源里没有表" in captured.err


# --------------------------------------------------------------------------- #
# 现算列（probe.computed_columns）：排除，但**要说出来**
# --------------------------------------------------------------------------- #


def test_computed_columns_are_excluded_but_declared(tmp_path, offline_full) -> None:
    """正控：声明了的现算列**既不在**「值不同」里，**又在**「现算列」那一节里。"""
    tables = _copy(offline_full)
    touched = _mutate_computed(tables)
    cost_row = tables["Units"][0]
    cost_id, old_cost = cost_row["Id"], cost_row[COST_COLUMN]
    cost_row[COST_COLUMN] = old_cost + 1
    declared = f"{COMPUTED_TABLE}.{COMPUTED_COLUMN}"

    report = _report(
        tmp_path, offline_full, tables, extra={"probe": {"computed_columns": [declared]}}
    )
    assert report.computed_columns == (declared,)
    assert _diff_tuples(report) == [("Units", cost_id, COST_COLUMN, old_cost, old_cost + 1)]
    assert report.conclusion_line().endswith("值不同 1 格")
    assert report.anomalies() == []

    excluded = report.excluded_diffs
    assert [(d.table, d.row_id, d.column) for d in excluded] == [
        (COMPUTED_TABLE, row_id, COMPUTED_COLUMN) for row_id, _, _ in touched
    ]
    assert [(d.offline, d.online) for d in excluded] == [
        (before, after) for _, before, after in touched
    ]
    assert report.has_differences() is True, "真差异还在（Units.Cost 那一格）"

    text = dump_diff.format_report(report)
    body = "\n".join(text)
    assert f"[现算列] 已按口径排除 {len(touched)} 格（列：{declared}）" in body
    field_block = body.split("[字段级]", 1)[1].split("[现算列]", 1)[0]
    assert COMPUTED_TABLE not in field_block, "现算列不许出现在「值不同」列表里"
    computed_block = body.split("[现算列]", 1)[1]
    for row_id, before, after in touched:
        assert f"[Id={row_id}].{COMPUTED_COLUMN}: 离线 {before} -> 在线 {after}" in computed_block


def test_only_computed_differences_exit_zero(tmp_path, instance_dir, offline_full) -> None:
    """排除后没有真差异 -> 退出码 0；但那一节仍写着排除了几格（不许静默）。"""
    tables = _copy(offline_full)
    touched = _mutate_computed(tables)
    declared = f"{COMPUTED_TABLE}.{COMPUTED_COLUMN}"
    dump_path = _write_dump(tmp_path, tables, extra={"probe": {"computed_columns": [declared]}})
    out = tmp_path / "computed-only.json"

    rc = dump_diff.main([str(instance_dir), str(dump_path), "--json", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["totals"]["value_diffs"] == 0
    assert payload["totals"]["excluded_value_diffs"] == len(touched)
    assert payload["totals"]["computed_columns"] == 1
    assert payload["conclusion"].endswith("值不同 0 格")
    assert payload["excluded_diffs"][0]["column"] == COMPUTED_COLUMN


@pytest.mark.parametrize("extra", [None, {"probe": {}}, {"probe": {"computed_columns": []}}])
def test_dump_without_computed_columns_compares_everything(tmp_path, offline_full, extra) -> None:
    """老 dump（没有 probe / 空数组）-> 照常**全量**比对，不报错。"""
    tables = _copy(offline_full)
    touched = _mutate_computed(tables)

    report = _report(tmp_path, offline_full, tables, extra=extra)
    assert report.computed_columns == ()
    assert report.excluded_diffs == []
    assert len(report.value_diffs) == len(touched)
    assert report.only_offline_count == 0
    assert report.only_online_count == 0
    body = "\n".join(dump_diff.format_report(report))
    assert "[现算列] 已按口径排除 0 格（列：无）" in body
    assert "全量比对" in body


def test_malformed_computed_columns_are_loud(tmp_path, offline_full) -> None:
    """声明里混了坏条目 -> 记异常（不静默），好的那条仍按口径排除。"""
    tables = _copy(offline_full)
    touched = _mutate_computed(tables, count=1)
    probe = {
        "computed_columns": [
            "没有点号",
            42,
            "NoSuchTable.X",
            "Units.NoSuchColumn",
            f"{COMPUTED_TABLE}.{COMPUTED_COLUMN}",
        ]
    }

    report = _report(tmp_path, offline_full, tables, extra={"probe": probe})
    assert len(report.excluded_diffs) == len(touched)
    texts = "\n".join(report.anomalies())
    assert "不是「表.列」" in texts
    assert "NoSuchTable" in texts
    assert "Units.NoSuchColumn" in texts
    assert report.has_differences() is True


def test_probe_not_an_object_is_loud(tmp_path, offline_full) -> None:
    report = _report(tmp_path, offline_full, _copy(offline_full), extra={"probe": "nope"})
    assert report.computed_columns == ()
    assert report.excluded_diffs == []
    assert any("probe 不是对象" in text for text in report.anomalies())


# --------------------------------------------------------------------------- #
# 打印折叠（同一列刷太多）+ 命令行约束
# --------------------------------------------------------------------------- #


def test_column_flood_is_folded_in_printing(tmp_path, offline_full) -> None:
    """一列 25 格 -> 折成一行 + 5 例；别的列不被吞；计数一个字不改。"""
    tables = _copy(offline_full)
    rows = tables["Units"]
    for index, row in enumerate(rows[:25]):
        row[COST_COLUMN] = row[COST_COLUMN] + 100 + index
    other = rows[30]
    other["Weight"] = other["Weight"] + 1

    report = _report(tmp_path, offline_full, tables)
    assert len(report.value_diffs) == 26
    assert report.conclusion_line().endswith("值不同 26 格")

    text = dump_diff.format_report(report)
    body = "\n".join(text)
    assert "  列 Units.Cost：25 格（前 5 例）" in body
    cost_lines = [
        line for line in text if line.startswith("  Units[Id=") and ".Cost:" in line
    ]
    assert len(cost_lines) == 5
    assert any(".Weight:" in line for line in text), "别的列不许被折叠吞掉"

    limited = [
        line
        for line in dump_diff.format_report(report, limit=3)
        if line.startswith("  Units[Id=")
    ]
    assert len(limited) == 3


def test_help_text_states_offline_dir_requirement() -> None:
    """帮助文案写死：一侧必须是离线目录，两份 dump 不能互比（这轮真踩过）。"""
    help_text = dump_diff.build_parser().format_help()
    assert "_schema.json" in help_text
    assert "不能互比" in help_text


def test_two_dumps_cannot_be_compared(tmp_path, offline_full, capsys) -> None:
    """拿两份 dump 互比 -> 退出码 2 + 说清为什么（不是「工具坏了」）。"""
    first = _write_dump(tmp_path, _copy(offline_full), name="a.json")
    second = _write_dump(tmp_path, _copy(offline_full), name="b.json")

    rc = dump_diff.main([str(first), str(second)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "_schema.json" in captured.err
    assert "两份 dump 不能互比" in captured.err
