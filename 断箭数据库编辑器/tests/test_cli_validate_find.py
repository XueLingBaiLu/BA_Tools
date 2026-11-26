"""CLI 的 `validate` / `find` 两个子命令（零 Qt，默认**不碰游戏目录**）。

这一层只钉三件事：

1. **JSON 契约**：`--json` 时 stdout 上只有一个 JSON 文档，人读那份走 stderr；
2. **退出码**：validate 0 / 1 / 2，find 0 / 1 / 2；
3. **口径不分叉**：find 报出来的引用者与 `core.edit.refs.referencing_rows` **逐条一致**
   （CLI 侧只许渲染，不许自己扫 24 张表 —— AGENTS §5「逻辑只有一份」）。

夹具是**内存造的小包**（24 张表各一两行），所以整份用例是毫秒级、与游戏目录无关；
只有那三条读真实 `DataBaseCompiled` 的用例例外（与 `test_rules_delta.py` 同一姿势），
数据源不在时 skip。
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from ba_mod_maker.cli import finder, validator
from ba_mod_maker.cli.__main__ import main as cli_main
from ba_mod_maker.cli.emit import Emitter, display_width, pad
from ba_mod_maker.core.asset import dbzip
from ba_mod_maker.core.asset.tables import TABLES
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.data.from_source import dataset_from_source
from ba_mod_maker.core.edit.refs import referencing_rows
from ba_mod_maker.core.rules.engine import KIND_LABELS, MAX_SAMPLES, RuleEngine
from ba_mod_maker.core.session import open_source

DATASET_ROOT = find_dataset_root()

#: 真实数据源那个「默认非战役」实例（`_rules.json` 指的就是它）。
INSTANCE_ID = "52085"

#: 三条读真实数据源的用例共用的门槛。
needs_dataset = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")


# --------------------------------------------------------------------------- #
# 夹具：24 张表的最小包（每张一两行，故意留出可预期的违规）
# --------------------------------------------------------------------------- #


def _tables(*, extra_unit: bool) -> dict[str, bytes]:
    """24 张表的最小明文。

    `extra_unit=True` 时多塞一个 `Units#2`，而它的 `UnitPropulsions` /
    `UnitArmors` 两行都不给 —— 于是 `req.UnitPropulsions` 与
    `req.UnitArmors` 各报一条 **error**（两条都是引擎里实打实的 error 规则）。
    """
    rows: dict[str, list] = {naming.table: [{"Id": 1}] for naming in TABLES}
    rows["Units"] = [{"Id": 1, "CountryId": 1, "HUDName": "BTR-82"}]
    if extra_unit:
        rows["Units"].append({"Id": 2, "CountryId": 1, "HUDName": "T-72"})
    rows["Countries"] = [{"Id": 1, "UIName": "USSR"}]
    rows["Armors"] = [{"Id": 1, "IsDefault": True}]
    # 外键里 0 = 「不使用」：这一行**不算**引用了 Mobility#1（find 有一条用例钉它）。
    rows["UnitPropulsions"] = [{"Id": 1, "UnitId": 1, "MobilityId": 0}]
    # Units#1 指着 Armors#1（IsDefault），所以默认装甲那条规则过得去。
    rows["UnitArmors"] = [{"Id": 7, "UnitId": 1, "ArmorId": 1}]
    return {
        naming.table: json.dumps(rows[naming.table], ensure_ascii=False).encode("utf-8")
        for naming in TABLES
    }


def _package(tmp_path: Path, monkeypatch, *, extra_unit: bool) -> Path:
    """造一个 BA_Mod.zip 到临时目录（**绝不碰用户那份**）。

    `BA_Mod.zip` 不带 pathID，`dataset_from_source` 会去游戏容器里查一个 ——
    用例不该因此去读真实的 7 GB 文件，也不该随「这台机器装没装游戏」而变
    （与 `test_data_layer.py::_instance_from_zip` 同一个手法）。
    """
    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    path = tmp_path / "BA_Mod.zip"
    dbzip.DbZip(tables=_tables(extra_unit=extra_unit)).write(path)
    return path


@pytest.fixture()
def clean_zip(tmp_path, monkeypatch) -> Path:
    """没有 error 的包（warning / info 照旧有一堆：那是"跑不了的规则"与孤儿武器）。"""
    return _package(tmp_path, monkeypatch, extra_unit=False)


@pytest.fixture()
def dirty_zip(tmp_path, monkeypatch) -> Path:
    """有 error 的包（两个单位只给了一个人的关联行）。"""
    return _package(tmp_path, monkeypatch, extra_unit=True)


# --------------------------------------------------------------------------- #
# 跑一次 CLI 的三个小助手
# --------------------------------------------------------------------------- #


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli_main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _json_run(capsys, *argv: str) -> tuple[int, dict, str]:
    code, out, err = _run(capsys, *argv)
    return code, json.loads(out), err


def _instance_of(path: Path) -> Instance:
    """走 CLI 那条同源的桥（`dataset_from_source`），拿一份能给 core 用的实例。"""
    dataset = dataset_from_source(open_source(path))
    return dataset.instance(dataset.default_instance_id)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# emit：统一输出层
# --------------------------------------------------------------------------- #


def test_json_mode_keeps_stdout_parseable_and_moves_human_lines_to_stderr() -> None:
    out, err = io.StringIO(), io.StringIO()
    emitter = Emitter(json_mode=True, out=out, err=err)
    emitter.field("源", "某处")
    emitter.items(["一条命中"])
    emitter.document({"tables": 24})
    assert json.loads(out.getvalue()) == {"schema": 1, "ok": True, "tables": 24}
    assert "源" in err.getvalue()
    assert "一条命中" in err.getvalue()


def test_pad_counts_display_columns_not_characters() -> None:
    """`str.ljust` 按字符个数补空格，中文会歪 —— 所以宽度得自己算。"""
    assert display_width("表 / 行") == 7
    assert display_width(pad("表 / 行", 12)) == 12


# --------------------------------------------------------------------------- #
# validate：退出码 0 / 1 / 2
# --------------------------------------------------------------------------- #


def test_validate_without_errors_exits_zero(capsys, clean_zip) -> None:
    code, out, _err = _run(capsys, "validate", str(clean_zip))
    assert code == validator.EXIT_OK
    assert "error 0" in out
    assert "没有 error" in out


def test_validate_with_errors_exits_one_carrying_the_rule_id(capsys, dirty_zip) -> None:
    code, document, _err = _json_run(capsys, "validate", str(dirty_zip), "--json")
    assert code == validator.EXIT_FOUND_ERRORS
    assert document["errors"] >= 1
    assert document["clean"] is False
    shown = {finding["rule_id"] for finding in document["findings"]}
    assert "req.UnitPropulsions" in shown, "引擎真跑了才会有这条 error"
    assert all(finding["severity"] == "error" for finding in document["findings"])


def test_validate_on_an_unreadable_source_exits_two(capsys, tmp_path) -> None:
    missing = tmp_path / "没有这个文件.zip"
    code, document, err = _json_run(capsys, "validate", str(missing), "--json")
    assert code == validator.EXIT_UNREADABLE
    assert document["ok"] is False
    assert document["error"]["message"]
    assert "错误" in err, "人话那句必须在 stderr 上"
    # 人读模式：不打退出码以外的东西到 stdout
    code2, out2, _err2 = _run(capsys, "validate", str(missing))
    assert code2 == validator.EXIT_UNREADABLE
    assert out2 == ""


def test_validate_document_reports_true_counts_and_what_was_omitted(capsys, clean_zip) -> None:
    code, document, _err = _json_run(capsys, "validate", str(clean_zip), "--json")
    assert code == validator.EXIT_OK
    assert document["schema"] == 1
    assert document["kind"] == "BA_Mod.zip"
    assert document["rules"] == RuleEngine().rule_count == 113
    assert set(document["violations"]) == {"error", "warning", "info"}
    assert document["errors"] == 0
    assert document["findings"] == [], "默认只展开 error"
    assert document["omitted"]["warning"] == document["violations"]["warning"] > 0
    assert document["sample_limit"] == MAX_SAMPLES
    assert len(document["by_kind"]) == len(KIND_LABELS), "每一类都要有汇总行"


def test_full_expands_the_warning_and_info_details(capsys, clean_zip) -> None:
    _code, lean, _err = _json_run(capsys, "validate", str(clean_zip), "--json")
    _code2, full, _err2 = _json_run(capsys, "validate", str(clean_zip), "--json", "--full")
    assert lean["omitted"] and full["omitted"] == {}
    assert not lean["findings"] and full["findings"]
    severities = {finding["severity"] for finding in full["findings"]}
    assert "warning" in severities, "最小包里也有一堆「跑不了这条规则」"
    # 人读那一侧：--full 多一张按类型的汇总
    _code3, out, _err3 = _run(capsys, "validate", str(clean_zip), "--full")
    assert "按类型：" in out


def test_validate_is_read_only(capsys, clean_zip) -> None:
    """校验与反查**一个字节都不许写**（源是用户手上那份）。"""
    before = _digest(clean_zip)
    _run(capsys, "validate", str(clean_zip))
    _run(capsys, "find", str(clean_zip), "Units", "1")
    assert _digest(clean_zip) == before


@needs_dataset
def test_validate_on_the_real_dataset_pins_the_baseline(capsys) -> None:
    """真实 52085：error 0 / warning 699 / info 1036（2026-10-01 实测）。

    warning 699 是**原版既有形态**（规则书记的是原版的样子），所以退出码仍是 0 ——
    这正是「退出码只看 error」的理由，也是这条用例钉住的东西。
    """
    code, document, _err = _json_run(capsys, "validate", str(DATASET_ROOT / INSTANCE_ID), "--json")
    if document["path_id"] != INSTANCE_ID:
        pytest.skip(f"这份数据源的默认实例是 {document['path_id']}，不是 {INSTANCE_ID}")
    assert code == validator.EXIT_OK
    assert document["violations"] == {"error": 0, "warning": 699, "info": 1036}


@needs_dataset
def test_validate_on_the_zip_source_matches_the_directory_source(capsys, tmp_path) -> None:
    """同一份数据的两种来源 -> **一模一样**的校验结果（表名归一之后）。

    这里曾经钉的是「zip 来源少跑一条规则」：`fk.Mobility.FlyPresetId` 的目标表在规则书里
    写作 `PlaneFlyPresets`，而 zip 来源叫 `FlyPresets` —— 取不到表就降级成一条 warning，
    于是目录 699 / zip 700。归一之后（`core/data/loader.py` + 规则书改规范名）两边都是
    699，且那条规则**真的跑了**：这条用例从此守「两种来源不许再分叉」。

    同一判据的**小样本版**在 `tests/test_table_naming.py`（毫秒级、与真实数据源无关）。
    """
    dataset = load_dataset(DATASET_ROOT)
    instance = dataset.instance(dataset.default_instance_id)
    if instance.path_id != INSTANCE_ID:
        pytest.skip(f"默认实例是 {instance.path_id}，不是 {INSTANCE_ID}")
    package = tmp_path / "BA_Mod.zip"
    blobs = {
        naming.table: (instance.directory / naming.zip_entry).read_bytes() for naming in TABLES
    }
    dbzip.DbZip(tables=blobs).write(package)

    _code, loose, _err = _json_run(
        capsys, "validate", str(DATASET_ROOT / INSTANCE_ID), "--json", "--full"
    )
    _code2, packed, _err2 = _json_run(capsys, "validate", str(package), "--json", "--full")
    assert packed["violations"] == loose["violations"], "两种来源的校验结果分叉了"
    assert packed["findings"] == loose["findings"]
    ids = {finding["rule_id"] for finding in packed["findings"]}
    assert "fk.Mobility.FlyPresetId" not in ids, "这条规则不许再降级成「跑不了这条规则」"


# --------------------------------------------------------------------------- #
# find：口径与 core 一致 + 退出码 0 / 1 / 2
# --------------------------------------------------------------------------- #


def test_find_reproduces_the_core_reverse_lookup(capsys, clean_zip) -> None:
    """逐条与 `core.edit.refs.referencing_rows` 对齐 —— 「别自己发明口径」的判据。"""
    code, document, _err = _json_run(capsys, "find", str(clean_zip), "Units", "1", "--json")
    assert code == finder.EXIT_FOUND
    expected = referencing_rows(_instance_of(clean_zip), "Units", 1)
    got = document["targets"][0]["references"]
    assert [(ref["table"], ref["row_id"], ref["column"]) for ref in got] == [
        (hit.table, hit.row_id, hit.column) for hit in expected
    ]
    assert {(ref["table"], ref["row_id"]) for ref in got} == {
        ("UnitArmors", 7),
        ("UnitPropulsions", 1),
    }
    assert document["total_references"] == len(expected) == 2
    assert document["targets"][0]["label"] == "BTR-82"


def test_find_ignores_the_foreign_key_zero(capsys, clean_zip) -> None:
    """`UnitPropulsions#1.MobilityId = 0` = 「不使用」—— 不算引用了 Mobility#1。"""
    code, document, _err = _json_run(capsys, "find", str(clean_zip), "Mobility", "1", "--json")
    assert code == finder.EXIT_NOTHING
    assert document["total_references"] == 0


def test_find_by_name_lists_the_rows_and_their_referrers(capsys, clean_zip) -> None:
    code, document, _err = _json_run(capsys, "find", str(clean_zip), "BTR-82", "--json")
    assert code == finder.EXIT_FOUND
    assert document["mode"] == "name"
    assert document["query"]["match_total"] >= 1
    units = [target for target in document["targets"] if target["table"] == "Units"]
    assert [target["row_id"] for target in units] == [1]
    assert units[0]["label"] == "BTR-82"
    assert len(units[0]["references"]) == 2


def test_find_bad_coordinates_exit_two(capsys, clean_zip) -> None:
    code, document, _err = _json_run(capsys, "find", str(clean_zip), "没有这个表", "1", "--json")
    assert code == finder.EXIT_UNUSABLE
    assert document["ok"] is False
    for argv in (
        ("find", str(clean_zip), "Units", "999999"),
        ("find", str(clean_zip), "Units", "不是数字"),
    ):
        assert _run(capsys, *argv)[0] == finder.EXIT_UNUSABLE, argv


def test_find_without_hits_exit_one_and_say_the_gui_sentence(capsys, clean_zip) -> None:
    """空结果那句话与界面面板同一句（`app/widgets/refs_panel.py::NO_HITS`）。"""
    code, out, _err = _run(capsys, "find", str(clean_zip), "Mobility", "1")
    assert code == finder.EXIT_NOTHING
    assert finder.NO_HITS in out


def test_find_unknown_name_exits_one(capsys, clean_zip) -> None:
    code, out, _err = _run(capsys, "find", str(clean_zip), "ZZZZ没有这个名字")
    assert code == finder.EXIT_NOTHING
    assert finder.NO_MATCH in out


@needs_dataset
def test_find_works_on_a_directory_source(capsys) -> None:
    """目录数据源那条路（`load_dataset`）也要通 —— 两条命令共用 `cli/source.py::load`。"""
    code, document, _err = _json_run(
        capsys, "find", str(DATASET_ROOT / INSTANCE_ID), "Units", "1", "--json"
    )
    assert code == finder.EXIT_FOUND
    assert document["kind"] == "directory"
    assert document["path_id"] == INSTANCE_ID
    assert document["total_references"] >= 1


# --------------------------------------------------------------------------- #
# --help：退出码成文（审查 P2-12）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["validate", "find"])
def test_subcommand_help_documents_the_exit_codes(capsys, command: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_main([command, "--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "退出码" in out
    assert "打不开源" in out


def test_top_level_help_lists_the_new_subcommands(capsys) -> None:
    with pytest.raises(SystemExit):
        cli_main(["--help"])
    out = capsys.readouterr().out
    assert "validate" in out
    assert "find" in out
    assert "退出码" in out
