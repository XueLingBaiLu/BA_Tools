"""tools/gen_table_columns.py 的「读点」列 —— 渲染与降级。

只测**纯函数与读语料那一层**：不读 _work（语料在 tmp_path 里现造）、不生成整篇文档
（生成物那句由 tools/gen_table_columns.py 自己跑）。跑法：

    pytest tests/test_gen_table_columns.py -q
"""

from __future__ import annotations

import gzip
import importlib.util
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
PKG = "bamod_gen_table_columns_under_test"

#: 语料表头 —— 由 tools/field_index.py --db-columns 写出（别在这里改顺序，改了就得同步那边）。
CORPUS_HEAD = ("table", "column", "model_type", "offset_hex", "n_read", "n_write", "top_readers")


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(PKG, TOOLS / "gen_table_columns.py")
    assert spec is not None and spec.loader is not None, (
        f"加载不了 {TOOLS / 'gen_table_columns.py'}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


GTC = _load()


def _write_corpus(path: Path, rows: list[tuple[str, ...]]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\t".join(CORPUS_HEAD) + "\n")
        for row in rows:
            handle.write("\t".join(row) + "\n")
    return path


def _row(table: str, column: str, readers: str, n_read: str = "1") -> tuple[str, ...]:
    return (
        table,
        column,
        "brokenarrowdatabasemodels" + table.lower(),
        "0x10",
        n_read,
        "1",
        readers,
    )


# --------------------------------------------------------------------------- #
# 渲染：语料那一格长这样 "IsMissile()x6 IsBomb()x2 get_TrajectoryType()x1"
# --------------------------------------------------------------------------- #
def test_render_readers_keeps_counts_and_drops_the_parens() -> None:
    got = GTC.render_readers("IsMissile()x6 IsBomb()x2 get_TrajectoryType()x1")
    assert got == "IsMissile×6 · IsBomb×2 · get_TrajectoryType", "N=1 不标 ×N，名字末尾的 () 去掉"


def test_render_readers_accepts_the_missing_left_paren_in_the_corpus() -> None:
    """语料里 Armors 那一族实测长这样（缺个左括号），别整条当名字。"""
    assert GTC.render_readers("get_ArmorValue()x1 armorType)x1 GetRestData()x1") == (
        "get_ArmorValue · armorType · GetRestData"
    )


def test_render_readers_keeps_a_name_that_ends_with_x_and_a_digit() -> None:
    """正控：不能见到 x<数字> 就切 —— 名字本身带 x2 的要原样留下。"""
    assert GTC.render_readers("foo_x2") == "foo_x2"
    assert GTC.render_readers("Bar()x2") == "Bar×2"


def test_render_readers_is_empty_without_readers() -> None:
    assert GTC.render_readers("") == ""


# --------------------------------------------------------------------------- #
# 一格：两种"没有"必须分开（没有归属读点 ≠ 语料不在）
# --------------------------------------------------------------------------- #
def test_cell_is_a_dash_when_this_column_has_no_reader() -> None:
    assert GTC.PLACEHOLDER == "—"
    table = {("Units", "Type"): "IsInfantry×3"}
    assert GTC.readpoint_cell(table, "Units", "Type") == "IsInfantry×3"
    assert GTC.readpoint_cell(table, "Units", "Cost") == "—"


def test_cell_is_empty_when_the_whole_corpus_is_missing() -> None:
    """⛔ 语料不在时写成「每一列都没有读点」就是撒谎（负控量的正是这个）。"""
    assert GTC.readpoint_cell(None, "Units", "Cost") == ""


# --------------------------------------------------------------------------- #
# 读语料：语料不在 / 坏了 / 表头不认识 —— 一律降级成 None + 一句 stdout 提示
# --------------------------------------------------------------------------- #
def test_load_readpoints_degrades_when_the_corpus_is_missing(tmp_path, capsys) -> None:
    assert GTC.load_readpoints(tmp_path / "不在这里" / "db_columns.tsv.gz") is None
    text = capsys.readouterr().out
    assert "降级" in text and "读点语料读不到" in text


def test_load_readpoints_degrades_on_a_corrupt_file(tmp_path, capsys) -> None:
    broken = tmp_path / "db_columns.tsv.gz"
    broken.write_text("这不是 gzip", encoding="utf-8")
    assert GTC.load_readpoints(broken) is None
    assert "降级" in capsys.readouterr().out


def test_load_readpoints_degrades_on_an_unknown_header(tmp_path, capsys) -> None:
    path = tmp_path / "db_columns.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("这不是表头\n1\t2\n")
    assert GTC.load_readpoints(path) is None
    assert "降级" in capsys.readouterr().out


def test_load_readpoints_parses_and_uses_table_names_not_entry_names(tmp_path) -> None:
    """语料按**条目名**建（PlaneFlyPresets），清单里写的是**规范表名**（FlyPresets）。"""
    path = _write_corpus(
        tmp_path / "db_columns.tsv.gz",
        [
            _row("Units", "Type", "get_Type()x1 IsInfantry()x3 IsFly()x1", n_read="5"),
            _row("PlaneFlyPresets", "MaxSpeed", "get_MaxSpeed()x1 Other()x1", n_read="2"),
        ],
    )
    assert GTC.load_readpoints(path) == {
        ("Units", "Type"): "get_Type · IsInfantry×3 · IsFly",
        ("FlyPresets", "MaxSpeed"): "get_MaxSpeed · Other",
    }


def test_readpoints_are_keyed_by_the_declared_corpus_table_name() -> None:
    """语料里那张表的原始拼写就是 PlaneFlyPresets —— 归一由 loader 负责（全仓只此一处）。"""
    assert GTC.CORPUS.name == "db_columns.tsv.gz"
    assert GTC.CORPUS.parent.name == "field_index"


# --------------------------------------------------------------------------- #
# 「代码判值」—— 渲染 / 合一格 / 降级（第二份语料 _work/value_domains）
# --------------------------------------------------------------------------- #
VALUE_HEAD = (
    "type",
    "field",
    "offset",
    "hex",
    "value",
    "value_hex",
    "form",
    "method",
    "file",
    "line",
)


def _write_values(path: Path, rows: list[tuple[str, ...]]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("	".join(VALUE_HEAD) + "\n")
        for row in rows:
            handle.write("	".join(row) + "\n")
    return path


def _vrow(mtype: str, field: str, value: int, method: str) -> tuple[str, ...]:
    return (
        mtype,
        field,
        "168",
        "0xA8",
        str(value),
        hex(value),
        "direct",
        f"System.Boolean {method}()",
        "X.txt",
        "1344",
    )


def test_render_value_semantics_groups_by_method_then_sorts_by_value() -> None:
    got = GTC.render_value_semantics(
        [
            (410, {"IsBomb"}),
            (100, {"IsMissile"}),
            (110, {"IsMissile"}),
            (300, {"IsMissile", "IsPrecisionAmmo"}),
        ]
    )
    assert got == "100/110←IsMissile；300←IsMissile/IsPrecisionAmmo；410←IsBomb"


def test_merge_note_keeps_the_hand_written_part_first() -> None:
    assert GTC.merge_note("手写", "[代码判值] X") == "手写 ｜ [代码判值] X"
    assert GTC.merge_note("", "[代码判值] X") == "[代码判值] X"
    assert GTC.merge_note("手写", "") == "手写"


def test_value_cell_is_empty_when_the_corpus_is_missing() -> None:
    """⛔ 语料不在时写「没有判值」就是撒谎 —— 与读点列同一口径。"""
    assert GTC.value_cell(None, "Units", "Type") == ""
    assert GTC.value_cell({}, "Units", "Type") == ""
    hit = GTC.value_cell({("Units", "Type"): "2←IsInfantry"}, "Units", "Type")
    assert hit == "[代码判值] 2←IsInfantry"


def test_load_value_semantics_joins_both_corpora_and_canonicalises_the_table(tmp_path) -> None:
    corpus = _write_corpus(
        tmp_path / "db_columns.tsv.gz",
        [
            (
                "PlaneFlyPresets",
                "MaxSpeed",
                "BrokenArrow.DataBase.Models.PlaneFlyPreset",
                "0x30",
                "1",
                "1",
                "get_MaxSpeed()x1",
            )
        ],
    )
    values = _write_values(
        tmp_path / "value_domains.tsv.gz",
        [
            _vrow("BrokenArrow.DataBase.Models.PlaneFlyPreset", "MaxSpeed", 0, "get_MaxSpeed"),
            _vrow("BrokenArrow.DataBase.Models.PlaneFlyPreset", "MaxSpeed", 5, "IsFast"),
        ],
    )
    assert GTC.load_value_semantics(corpus, values) == {
        ("FlyPresets", "MaxSpeed"): "0←get_MaxSpeed；5←IsFast"
    }


def test_load_value_semantics_degrades_when_the_values_corpus_is_missing(tmp_path, capsys) -> None:
    corpus = _write_corpus(tmp_path / "db_columns.tsv.gz", [_row("Units", "Type", "get_Type()x1")])
    assert GTC.load_value_semantics(corpus, tmp_path / "缺.tsv.gz") is None
    assert "降级" in capsys.readouterr().out
