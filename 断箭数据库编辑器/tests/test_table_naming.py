"""表名归一：**同一份数据、两种来源，必须给同一张表同一个名字**（零 Qt，内存夹具）。

## 这条钉的是什么

同一张表有三套名字（出处：`core/asset/tables.py` 开头那张表）：

| 字段名 | **表名** | 条目名（= 磁盘文件名） |
|---|---|---|
| `FlyPresetsJson` | `FlyPresets` | `PlaneFlyPresets.json` |

全库**只有这一处**不一致，而目录数据源过去按文件干建表名，于是同一张表在两个来源里
叫两个名字：zip / 容器来源叫 `FlyPresets`，目录来源叫 `PlaneFlyPresets`。后果实测两条：

1. 规则书里 `fk.Mobility.FlyPresetId` 的目标表只能写其中一个名字 —— 另一个来源上
   **整条规则跑不了**（引擎降级成一条 warning「跑不了这条规则」）；
2. 界面上凡是拿这个名字当键的地方都要写两遍（表中文名 / emoji / 外键跳转）。

现在归一做在 `core/data/loader.py::load_dataset` 一处：交出去的表名一律是**规范名**，
磁盘文件名一个字不动（游戏认那个名字）。

## 为什么夹具是"最小目录数据源 + 最小 zip"

两条来源都要真造出来 —— 目录来源要 `_schema.json` / `_manifest.json` 与 24 个
散文件（文件干那一套名字，`PlaneFlyPresets.json` 正是关键），zip 来源走 `dbzip`。
两边都是几 KB 的内存构造，所以整份用例是毫秒级、与游戏目录无关。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import dbzip
from ba_mod_maker.core.asset import tables as table_registry
from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.data.from_source import dataset_from_source
from ba_mod_maker.core.data.loader import canonical_table_name
from ba_mod_maker.core.rules.engine import RuleEngine
from ba_mod_maker.core.session import open_source

#: 磁盘文件名与表名不一致的那一张（全库唯一一处）。
ALIAS_TABLE = "FlyPresets"
ALIAS_ENTRY = "PlaneFlyPresets.json"

#: 造出来的目录数据源里的实例目录名（数字命名 + _manifest.json = 一个实例）。
INSTANCE_ID = "52085"

#: 真实数据源（只有最后那条"卡上那一格"的用例要它 —— 那张卡是按真数据装配的）。
DATASET_ROOT = find_dataset_root()

needs_dataset = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")


def _rows() -> dict[str, list[dict]]:
    """24 张表的最小明文：每张只有 Id=1，加两条**故意**造的关系。

    - `Mobility#1.FlyPresetId = 999`：指向一张不存在的飞行预设 —— 那条外键规则
      在两种来源上都必须**报出同一条 violation**（而不是一边报"跑不了这条规则"）；
    - `FlyPresets#1`：Id=1，所以 999 确实不在里面。
    """
    rows: dict[str, list[dict]] = {naming.table: [{"Id": 1}] for naming in table_registry.TABLES}
    rows["Mobility"] = [{"Id": 1, "FlyPresetId": 999}]
    rows["FlyPresets"] = [{"Id": 1}]
    return rows


def _columns(rows: list[dict]) -> tuple[str, ...]:
    """这张表的列（取自第一行的键顺序，代码里就是按它建 Table 的）。"""
    return tuple(rows[0].keys()) if rows else ()


def _write_directory_source(root: Path) -> Path:
    """造一个最小**目录数据源**：`<root>/52085/*.json` + `_schema.json` + `_manifest.json`。

    ⚠️ 磁盘上的文件名用**条目名**那一套（`PlaneFlyPresets.json`），schema 的键也用
    文件干 —— 这正是历史形态；归一要发生在**读进来之后**，不是改磁盘。
    """
    rows = _rows()
    instance_dir = root / INSTANCE_ID
    instance_dir.mkdir(parents=True)
    schema: dict[str, list[str]] = {}
    manifest: list[dict] = []
    for naming in table_registry.TABLES:
        payload = rows[naming.table]
        (instance_dir / naming.zip_entry).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        # schema 的键 = **文件干**（这就是那个不一致的来源）。
        schema[naming.zip_entry[: -len(".json")]] = list(_columns(payload))
        manifest.append(
            {
                "field": naming.field,
                "table": naming.table,
                "zip_entry": naming.zip_entry,
                "keys": len(_columns(payload)),
                "rows": len(payload),
            }
        )
    (root / "_schema.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    (instance_dir / "_manifest.json").write_text(
        json.dumps({"tables": manifest}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _write_zip(tmp_path: Path, monkeypatch) -> Path:
    """同一份数据的**外部库**形态（`BA_Mod.zip`）。

    `BA_Mod.zip` 不带 pathID，桥会去游戏容器里查一个 —— 用例不该因此去读真实游戏目录
    （与 `test_data_layer.py::_instance_from_zip` 同一个手法）。
    """
    from ba_mod_maker.core.assets import game as game_mod

    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    package = tmp_path / "BA_Mod.zip"
    dbzip.DbZip(
        tables={
            naming.table: json.dumps(_rows()[naming.table], ensure_ascii=False).encode("utf-8")
            for naming in table_registry.TABLES
        }
    ).write(package)
    return package


def _directory_instance(root: Path):
    dataset = load_dataset(root)
    return dataset.instance(dataset.default_instance_id)


def _zip_instance(package: Path):
    dataset = dataset_from_source(open_source(package))
    return dataset.instance(dataset.default_instance_id)


# --------------------------------------------------------------------------- #
# ① 两种来源的表名集合完全一致
# --------------------------------------------------------------------------- #


def test_both_sources_agree_on_table_names(tmp_path, monkeypatch) -> None:
    root = _write_directory_source(tmp_path / "DataBaseCompiled")
    loose = _directory_instance(root)
    packed = _zip_instance(_write_zip(tmp_path, monkeypatch))

    assert set(loose.table_names) == set(packed.table_names)
    assert ALIAS_TABLE in loose.table_names
    assert ALIAS_ENTRY[: -len(".json")] not in loose.table_names, (
        "目录来源不该再交出文件干那一套名字"
    )
    # 两张来源都必须**读得到**这张表（不是名字对了、表却是空的）。
    assert loose.table(ALIAS_TABLE).row_count == packed.table(ALIAS_TABLE).row_count == 1


def test_the_disk_file_name_is_not_touched(tmp_path) -> None:
    """归一只改**读进来的名字** —— 磁盘上还得叫 `PlaneFlyPresets.json`（游戏认它）。"""
    root = _write_directory_source(tmp_path / "DataBaseCompiled")
    _directory_instance(root)
    assert (root / INSTANCE_ID / ALIAS_ENTRY).is_file()


def test_manifest_keys_are_canonical_too(tmp_path) -> None:
    """`_manifest.json` 的键取自条目名 —— 也要归一，否则那张表的行数对账被静默跳过。"""
    instance = _directory_instance(_write_directory_source(tmp_path / "DataBaseCompiled"))
    assert instance.manifest_entry(ALIAS_TABLE), "归一直做了 schema、漏了 manifest"
    assert instance.manifest_entry(ALIAS_TABLE)["zip_entry"] == ALIAS_ENTRY


def test_canonical_table_name_only_rewrites_registered_pairs() -> None:
    """归一不是"猜一个名字"：只认注册表里登记过的那对，别的原样返回。"""
    assert canonical_table_name(ALIAS_ENTRY[: -len(".json")]) == ALIAS_TABLE
    assert canonical_table_name(ALIAS_TABLE) == ALIAS_TABLE
    assert canonical_table_name("Units") == "Units"
    assert canonical_table_name("没这张表") == "没这张表"


# --------------------------------------------------------------------------- #
# ② 规则书里出现的每个表名都在规范清单里（防漂移）
# --------------------------------------------------------------------------- #


def test_rulebook_only_names_canonical_tables() -> None:
    """规则书里的表名必须是规范名 —— 写错一个名字 = 那条规则**永远跑不了**。

    这正是 `fk.Mobility.FlyPresetId` 出过的毛病：目标表写成了文件干那一套，
    zip 来源上取不到表，整条规则被降级成一条 warning。

    只扫 `table` / `target` 两个字段：它们才是**判据**。`expr` / `note` 是数据侧的
    散文、由 `tools/gen_rulebook.py` 原样搬（那句 expr 里写的仍是 `PlaneFlyPresets`），
    拿散文当判据只会得到一条假红。
    """
    from ba_mod_maker.core.rules.engine import load_rulebook

    canonical = set(table_registry.TABLE_NAMES)
    named: dict[str, str] = {}
    for rule in load_rulebook()["rules"]:
        for key in ("table", "target"):
            value = rule.get(key)
            if isinstance(value, str) and value:
                named.setdefault(value, rule["id"])

    unknown = {name: rule_id for name, rule_id in named.items() if name not in canonical}
    assert not unknown, f"规则书里出现了非规范表名：{unknown}"


# --------------------------------------------------------------------------- #
# ③ 两种来源的 validate 结果一致（这条规则真的跑了）
# --------------------------------------------------------------------------- #


def test_both_sources_report_the_same_violation(tmp_path, monkeypatch) -> None:
    """同一份数据、两种来源 -> 同一套违规计数，且那条外键**都真的跑了**。

    关键判据不是"计数相等"，而是 `fk.Mobility.FlyPresetId` 报的是
    「`Mobility.FlyPresetId = 999` 不在 `FlyPresets` 里」这条**真违规** ——
    名字对不上的时候它报的是「跑不了这条规则」（那条规则等于没跑）。
    """
    root = _write_directory_source(tmp_path / "DataBaseCompiled")
    engine = RuleEngine()

    reports = {
        "directory": engine.validate(_directory_instance(root)),
        "zip": engine.validate(_zip_instance(_write_zip(tmp_path, monkeypatch))),
    }
    counts = {kind: dict(report.counts) for kind, report in reports.items()}
    assert counts["directory"] == counts["zip"], counts

    for source, report in reports.items():
        outcome = next(o for o in report.outcomes if o.rule_id == "fk.Mobility.FlyPresetId")
        assert outcome.violations == 1, f"{source}：这条外键规则没报出那条违规（{outcome.samples}）"
        message = outcome.samples[0].message
        assert "999" in message and "FlyPresets" in message, message
        assert "跑不了这条规则" not in message, f"{source}：规则又被降级成「跑不了」了"


@pytest.mark.parametrize("table", [ALIAS_TABLE])
def test_the_rule_engine_resolves_the_alias_table(table: str) -> None:
    """规则书用规范名去取表，取到的是**同一张表**（不是靠别名凑出来的第二张）。"""
    naming = table_registry.BY_TABLE[table]
    assert naming.zip_entry == ALIAS_ENTRY
    assert naming.table == table


# --------------------------------------------------------------------------- #
# 界面上写死这个名字的地方：CellRef 里的名字必须能被变更集解开
# --------------------------------------------------------------------------- #


@needs_dataset
def test_the_card_fly_preset_cell_points_at_a_real_table() -> None:
    """卡上那两格（固定翼前进速度 / 回转半径）造的 CellRef 必须解得开。

    `app/viewmodel/unit_card.py::_T_FLY_PRESETS` 装的是**表名**，而那个名字会一路走到
    `Changeset.set_value()` -> `Instance.table(name)`：名字写错就是「点那一格直接 KeyError」。
    这里按界面那条路走一遍 —— `CardData.t()` 会做别名归一，但 `CellRef` 里那个字符串不会。
    """
    from ba_mod_maker.app.viewmodel import unit_card
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.edit.refs import CellRef

    dataset = load_dataset(DATASET_ROOT)
    if INSTANCE_ID not in dataset.instance_ids:
        pytest.skip(f"这份数据源里没有实例 {INSTANCE_ID}")
    instance = dataset.instance(INSTANCE_ID)

    # 私有常量：它就是"卡上那一格读哪张表"的唯一出处（`_T_*` 一族都在这一个地方）。
    table = unit_card._T_FLY_PRESETS
    assert table in instance.table_names, f"卡上写死的表名 {table!r} 在数据源里不存在"

    rows = instance.table(table)
    row_id = rows.rows[0][rows.column_index("Id")]
    ref = CellRef(table, row_id, "MaxSpeed")
    changeset = Changeset(instance)
    changeset.set_value(ref.table, ref.row_id, ref.column, 1.0)  # 界面就是这么调的
    assert changeset.changed_tables() == (table,)
