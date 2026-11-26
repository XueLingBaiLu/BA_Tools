"""null 格子的**列类型**从哪来（零 Qt、零磁盘依赖）。

钉住一条判据：一格是 null 时，列类型要退回**同一列其它行**去推，不能就此当成文本。

事故（2026-11-03 用户实测）：从 null 格起手改 `SpecializationAvailabilities.MaxAvailabilityXp0`，
写回去是 "2"（字符串）；游戏能转成 2，但产品自己的 `armory._int()` 只认 `int` ⇒ 读成 0。
根因是 `cells._kind_of_value(None) -> "unknown"` ⇒ `columns.parse_value` 对 UNKNOWN 原样存。
"""

from __future__ import annotations

from pathlib import Path

from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.data.schema import TableSchema
from ba_mod_maker.core.edit.cells import cell_at, parse_cell_text
from ba_mod_maker.core.edit.refs import CellRef

_COLUMNS = ("Id", "SpecializationId", "UnitId", "MaxAvailabilityXp0", "Note")
_NAME = "SpecializationAvailabilities"


def _instance() -> Instance:
    table = Table(
        name=_NAME,
        columns=_COLUMNS,
        rows=[
            [6, 1, 10, 100, None],
            [7, 2, 11, 200, None],
            [1232, 3, 180, None, None],  # 事故行的形状：同列其它行 int、这一行 null
        ],
        source_columns=_COLUMNS,
        source_path=Path("x.json"),
    )
    inst = Instance(
        path_id="52739",
        directory=Path("."),
        schema={_NAME: TableSchema(name=_NAME, columns=_COLUMNS)},
        manifest={},
    )
    inst._tables[_NAME] = table
    return inst


def test_null_cell_takes_its_kind_from_the_other_rows() -> None:
    """这一格是 null，但同列其它行都是 int ⇒ 用户敲的 2 必须变成 int 2。"""
    inst = _instance()
    cell = cell_at(inst, CellRef(_NAME, 1232, "MaxAvailabilityXp0"))
    assert cell is not None
    assert cell.value_kind == "int"
    value = parse_cell_text(cell, "2")
    assert value == 2
    assert isinstance(value, int), "写回字符串会让产品自己读成 0（armory._int 只认 int）"


def test_all_null_column_still_counts_as_text() -> None:
    """负控：整列都是 null 的列（真·占位）不能被猜成数字，仍按文本处理。"""
    inst = _instance()
    cell = cell_at(inst, CellRef(_NAME, 1232, "Note"))
    assert cell is not None
    assert cell.value_kind == "unknown"
    assert parse_cell_text(cell, "abc") == "abc"


def test_non_null_cell_keeps_its_own_kind() -> None:
    """正控的另一半：格子里本来就有值时，仍以**这一格**的类型为准（回退不生效）。"""
    inst = _instance()
    cell = cell_at(inst, CellRef(_NAME, 6, "MaxAvailabilityXp0"))
    assert cell is not None
    assert cell.value_kind == "int"
    assert parse_cell_text(cell, "7") == 7
