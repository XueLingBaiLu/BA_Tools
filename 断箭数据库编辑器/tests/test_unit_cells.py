"""「这一格落在哪」的守卫（零 Qt）。

任务书点名的三件必须有纯函数的事，这是第三件：
「哪些行属于这个单位」（test_unit_scope.py）· 「一行被几个单位共用」（test_sharing.py）
· **「这一格落在哪」（本文件）**。

界面只做展示，所以这里的断言就是评审看的东西：
寻址对不对、中文名对不对、外键显示的是不是**名字**、共享度有没有带上。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.cells import (
    cell_at,
    display_value,
    group_by_table,
    row_cells,
    table_columns,
    unit_cells,
)
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef, unit_scope
from ba_mod_maker.core.edit.sharing import build_sharing_index

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


@pytest.fixture(scope="module")
def sharing(instance):
    return build_sharing_index(instance)


@pytest.fixture(scope="module")
def cells(instance, sharing):
    return unit_cells(instance, 1, sharing=sharing)


def _find(cells, table: str, column: str):
    for cell in cells:
        if cell.table == table and cell.column == column:
            return cell
    raise AssertionError(f"{table}.{column} 不在这个单位的格子里")


def test_cell_count_matches_the_scope(cells) -> None:
    assert len(cells) == 1180


def test_every_cell_carries_its_address(cells) -> None:
    """「落在哪」= CellRef(表, 行 Id, 列名)，编辑按它写。"""
    cell = _find(cells, "Units", "Cost")
    assert cell.ref == CellRef("Units", 1, "Cost")
    assert cell.place == "Units#1 · Cost"
    assert cell.raw == 55


def test_values_are_shown_in_chinese(cells) -> None:
    assert _find(cells, "Units", "Cost").cn == "基础成本"
    assert _find(cells, "Ammunitions", "TargetType").text in ("地面 + 步兵 + 载具 + 直升机 + 舰船",)
    assert _find(cells, "Units", "DisplayInArmory").text == "是"


def test_foreign_keys_show_the_name_not_the_id(cells) -> None:
    """用户口径：外键一律显示名字，界面不给用户看 Id。"""
    armor = _find(cells, "UnitArmors", "ArmorId")
    assert armor.foreign == "Armors"
    assert armor.raw == 1
    assert armor.text == "Armor BTR80 Base"
    assert armor.text != str(armor.raw), "外键显示的必须是名字，不是 Id"
    assert armor.raw_text == "1"  # 原始值仍在（备查）


def test_zero_foreign_key_means_unused(cells) -> None:
    """0 = 「不使用」，不是「指向 Id 0」。"""
    zero = [c for c in cells if c.foreign and c.raw == 0]
    if not zero:
        pytest.skip("这个单位没有空外键")
    for cell in zero:
        assert cell.text == "（不使用）"


def test_sharing_is_attached_to_every_cell(cells, sharing) -> None:
    armor = _find(cells, "Armors", "MaxHealthPoints")
    assert armor.shared_with == 4
    assert armor.is_shared is True
    assert armor.shared_with == sharing.of("Armors", armor.row_id)


def test_exclusive_rows_report_one_not_zero(cells) -> None:
    """独占行（关联行）共享度是 1，不是 0 —— 0 在界面上是"查不到"。"""
    link = _find(cells, "UnitArmors", "ArmorId")
    assert link.shared_with == 1
    assert link.is_shared is False


def test_row_label_names_the_row(cells) -> None:
    assert _find(cells, "Armors", "MaxHealthPoints").row_label == "Armor BTR80 Base"


def test_not_shared_when_no_index_is_given(instance) -> None:
    """不传共享索引 = 不带共享信息（0），**不是**"独占"（1）。"""
    plain = unit_cells(instance, 1)
    assert all(cell.shared_with == 0 for cell in plain)


def test_cell_at_reads_one_cell(instance, sharing) -> None:
    cell = cell_at(instance, CellRef("Units", 1, "Cost"), sharing=sharing)
    assert cell is not None
    assert cell.text == "55"
    assert cell.shared_with == 0  # Units 不是共享模板


def test_cell_at_returns_none_instead_of_raising(instance) -> None:
    """行被删掉 / 列不存在 —— 界面要能显示"这一格没了"，不是报错卡。"""
    assert cell_at(instance, CellRef("Units", 999999, "Cost")) is None
    assert cell_at(instance, CellRef("Units", 1, "没有这一列")) is None
    assert cell_at(instance, CellRef("没有这张表", 1, "Cost")) is None
    assert row_cells(instance, RowRef("Units", 999999)) == ()


def test_row_cells_gives_every_column_of_one_row(instance) -> None:
    row = row_cells(instance, RowRef("Units", 1))
    assert len(row) == len(instance.table("Units").columns)


def test_grouping_keeps_the_scope_order(cells) -> None:
    groups = group_by_table(cells)
    assert [name for name, _ in groups] == list(unit_scope(_instance(), 1).tables)
    assert sum(len(items) for _, items in groups) == len(cells)


def test_table_columns_gives_the_full_column_list(instance) -> None:
    """四个专用页按它渲染，列数一个不少（设计 §4 的 40 / 67 / 46 / 6）。"""
    assert len(table_columns(instance, "Weapons")) == 40
    assert len(table_columns(instance, "Ammunitions")) == 67
    assert len(table_columns(instance, "Options")) == 46
    assert len(table_columns(instance, "SquadMembers")) == 6
    assert table_columns(instance, "没有这张表") == ()


def test_display_value_decodes_a_standalone_value() -> None:
    """ "改完之后是什么"要能单独解（界面就地回显用）。"""
    assert display_value(_instance(), "Ammunitions", "TargetType", 36) == "载具 + 舰船"
    assert display_value(_instance(), "Units", "Cost", 60) == "60"


def _instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
