"""字段词典窗口的守卫（旧产品有词典窗口，新产品此前只有行内 tooltip）。

分两层，**一条数据源都不需要的在前**：

1. **纯函数**（`app/dialogs/dictionary_data.py`，零 Qt）：表清单、列清单、过滤、
   取值观测的口径；
2. **窗口**（离屏构造，**不 show()**）：左栏 24 张表、搜索两处同时收窄、
   有数据源时把取值观测接上。

为什么值得守：

- 词典窗口的价值全在"**说得对**"——中文名、说明、枚举对照、取值观测。
  这些数字一旦漂了，用户拿它当依据就会改错数据；
- "24 张表 × 每张表的列"这条链上有两处**容易静默丢东西**的地方：
  表名别名（`FlyPresets` vs `PlaneFlyPresets`）与"labels + enums 两份互补"。
  所以这里用 `407` 这个总数把它们钉死（core/fields/README.md：
  391 列 + 16 列 = 407 = 全库列数）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.app.dialogs.dictionary_data import (
    MISSING_NOTE,
    ColumnEntry,
    column_entries,
    column_stats,
    dictionary_table_key,
    filter_columns,
    filter_tables,
    first_table_name,
    matches,
    table_entries,
    table_size,
    visible_columns,
)
from ba_mod_maker.core.asset.tables import TABLE_NAMES, TableNaming
from ba_mod_maker.core.data.dataset import Table

#: core/fields/README.md 的口径：391（labels）+ 16（只在 enums 里）= 全库 407 列。
ALL_COLUMNS = 407

#: AGENTS.md §0.5 与 docs/数据-表与列清单.md:975 都写着 Units 是 31 列。
UNITS_COLUMNS = 31


# --------------------------------------------------------------------------- #
# 纯函数层
# --------------------------------------------------------------------------- #


#: 纯函数用例用的假中文表名：只要两三条就够验"过滤按中文名走"，
#: 产品里真正那份由 app/screens/editor/entries_panel.py 注入（见下面的用例）。
_FAKE_TITLES = {"Units": "单位本体", "FlyPresets": "飞行包线", "Ammunitions": "弹药模板"}


def _titled_entries():
    return table_entries(title_of=lambda name: _FAKE_TITLES.get(name, name))


def test_the_table_list_is_the_24_tables_in_the_product_order() -> None:
    entries = table_entries()
    assert tuple(e.name for e in entries) == TABLE_NAMES
    assert len(entries) == 24


def test_titles_and_emoji_are_injected_not_copied_again() -> None:
    """中文名与 emoji 由调用方注入 —— 本模块不许自己再抄一份表。"""
    called: list[str] = []

    def title_of(name: str) -> str:
        called.append(name)
        return f"中文-{name}"

    def emoji_of(_name: str) -> str:
        return "★"

    entries = table_entries(title_of=title_of, emoji_of=emoji_of)
    assert called == list(TABLE_NAMES), "每张表都要问过注入的那一份"
    assert entries[0].label == "★ 中文-Units"

    # 不注入时的兜底：照实显示表名、不画 emoji（宁可不给，也不编一个）。
    plain = table_entries()
    assert plain[0].title == "Units"
    assert plain[0].label == "Units"


def test_product_titles_and_emoji_are_actually_picked_up() -> None:
    """接上产品那两个函数时，界面上就是那套中文名与 emoji（不是本模块编的）。"""
    from ba_mod_maker.app.screens.editor.entries_panel import table_emoji, table_title

    entries = table_entries(title_of=table_title, emoji_of=table_emoji)
    units = next(e for e in entries if e.name == "Units")
    assert units.title == "单位本体"
    assert units.emoji == "📦"
    assert units.label == "📦 单位本体"


def test_the_fly_preset_alias_reaches_the_dictionary() -> None:
    """`FlyPresets`（外部库那一套叫法）必须能查到 `PlaneFlyPresets` 的词典。"""
    assert dictionary_table_key("FlyPresets") == "PlaneFlyPresets"
    assert dictionary_table_key("Units") == "Units"
    columns = column_entries("FlyPresets")
    assert "MaxSpeed" in [e.column for e in columns], "别名归一没走通，这张表的词典整块丢了"


def test_all_407_columns_are_reachable() -> None:
    """两份词典互补：24 张表的列加起来必须正好是全库的 407 列。"""
    total = 0
    for entry in table_entries():
        total += len(column_entries(entry.name))
    assert total == ALL_COLUMNS, f"词典列数不对：{total} != {ALL_COLUMNS}"


def test_units_has_its_31_columns() -> None:
    """Units 的 31 列 = field_labels 的 26 列 + 只在 field_enums 里的 5 列。"""
    entries = column_entries("Units")
    assert len(entries) == UNITS_COLUMNS
    assert {e.column for e in entries} >= {"Id", "Cost", "CountryId", "Type", "Stealth"}


def test_a_documented_column_carries_its_chinese_name_and_note() -> None:
    cost = next(e for e in column_entries("Units") if e.column == "Cost")
    assert cost.cn == "基础成本"
    assert cost.missing is False
    assert "基数" in cost.doc, "说明没接上（doc 是界面右下那一句）"
    assert cost.observed_kind is None, "没有数据源时不许假装观测过"


def test_a_mask_column_lists_its_values_in_number_order() -> None:
    target = next(e for e in column_entries("Ammunitions") if e.column == "TargetType")
    assert target.kind == "mask"
    keys = [raw for raw, _ in target.values]
    assert keys == sorted(keys, key=int), "位掩码的值域要按数值升序"
    assert keys[0] == "1"
    assert target.decode_text("4") == "载具"
    assert "位掩码" in target.type_text


def test_an_unlisted_column_is_marked_instead_of_invented() -> None:
    """词典里没有的列：写「词典未收录」+ 英文列名，但仍然给取值观测。"""
    stub = _StubInstance(
        {
            "Units": Table(
                name="Units",
                columns=("Id", "Cost", "NoSuchColumn"),
                rows=[[1, 10, 5], [2, 10, 7], [3, 25, 5]],
                source_columns=("Id", "Cost", "NoSuchColumn"),
                source_path=Path("stub.json"),
            )
        }
    )
    entries = column_entries("Units", stub)
    unknown = next(e for e in entries if e.column == "NoSuchColumn")
    assert unknown.missing is True
    assert MISSING_NOTE in unknown.title
    assert unknown.column in unknown.title

    known = next(e for e in entries if e.column == "Cost")
    assert known.missing is False
    assert known.observed_kind == "int"
    assert known.stats is not None
    assert known.stats.rows == 3


def test_stats_use_the_column_inventory_definition() -> None:
    """口径出处：tools/gen_table_columns.py:112-116（docs/数据-表与列清单.md 的生成器）。"""
    stats = column_stats([1, 1, 1, 2, 3])
    assert (stats.rows, stats.distinct, stats.non_default) == (5, 3, 2)
    assert stats.samples == ("1", "2", "3"), "样例 = 出现最多的三个"
    assert "非默认 2" in stats.summary

    empty = column_stats([])
    assert (empty.rows, empty.distinct, empty.non_default) == (0, 0, 0)
    assert empty.samples == ()


def test_table_size_is_none_without_a_data_source() -> None:
    assert table_size(None, "Units") is None
    stub = _StubInstance(
        {
            "Units": Table(
                name="Units",
                columns=("Id",),
                rows=[[1], [2]],
                source_columns=("Id",),
                source_path=Path("stub.json"),
            )
        }
    )
    assert table_size(stub, "Units") == (2, 1)
    assert table_size(stub, "Weapons") is None


def test_matches_is_a_case_insensitive_substring_on_any_field() -> None:
    assert matches("", "随便") is True, "空查询 = 全都要"
    assert matches("  ", "随便") is True
    assert matches("units", "Units") is True
    assert matches("单位", "单位本体") is True
    assert matches("单位", "武器模板") is False


def test_filter_tables_accepts_chinese_english_and_table_name() -> None:
    entries = _titled_entries()
    assert len(filter_tables(entries, "")) == 24

    by_chinese = filter_tables(entries, "单位本体")
    assert [e.name for e in by_chinese] == ["Units"]

    # 过滤是**子串**匹配：名字里带 Units 的三张表都该出来（即时筛就该这样）。
    by_english = filter_tables(entries, "units")
    assert {e.name for e in by_english} == {"Units", "TurretUnits", "SensorUnits"}

    by_dict_key = filter_tables(entries, "PlaneFlyPresets")
    assert [e.name for e in by_dict_key] == ["FlyPresets"]

    assert filter_tables(entries, "没有这张表") == ()


def test_filter_tables_finds_a_table_by_one_of_its_columns() -> None:
    """搜列名也要能落到那张表上 —— 否则输「基础成本」时左栏会空掉。"""
    entries = _titled_entries()
    assert [e.name for e in filter_tables(entries, "基础成本")] == ["Units"]
    assert [e.name for e in filter_tables(entries, "TargetType")] == ["Ammunitions"]


def test_visible_columns_shows_the_whole_table_when_only_the_table_name_matched() -> None:
    columns = column_entries("Units")
    assert filter_columns(columns, "单位本体") == (), "没有哪一列的中文名含这四个字"
    assert visible_columns(columns, "单位本体") == columns, "整表显示（不然用户以为没列）"

    hit = visible_columns(columns, "基础成本")
    assert [e.column for e in hit] == ["Cost"]


def test_filter_columns_accepts_chinese_and_english_name() -> None:
    columns = column_entries("Units")
    assert len(filter_columns(columns, "")) == UNITS_COLUMNS

    by_chinese = filter_columns(columns, "基础成本")
    assert [e.column for e in by_chinese] == ["Cost"]

    by_english = filter_columns(columns, "cost")
    assert "Cost" in [e.column for e in by_english]
    # 过滤是**子串**匹配（即时筛），不是精确匹配：OriginalCost 也应当被筛出来。
    assert all("cost" in e.column.casefold() for e in by_english)

    assert filter_columns(columns, "没有这一列") == ()


def test_first_table_name_keeps_the_current_one_while_it_survives() -> None:
    entries = _titled_entries()
    assert first_table_name(entries, "Weapons") == "Weapons"

    narrowed = filter_tables(entries, "单位本体")
    assert first_table_name(narrowed, "Weapons") == "Units", "当前这张被滤掉了就退到第一张"
    assert first_table_name((), "Weapons") is None


# --------------------------------------------------------------------------- #
# 窗口层（离屏构造，不 show()）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def dictionary(qapp):
    """建一个词典窗口，用完销毁（无父窗口 = 顶层控件，不销毁会顶破 conftest 的上限）。"""
    from ba_mod_maker.app.dialogs import DictionaryWindow

    window = DictionaryWindow()
    yield window
    window.deleteLater()


def test_window_lists_the_24_tables_without_a_data_source(dictionary) -> None:
    assert dictionary._table_list.count() == 24
    assert dictionary.selected_table() == "Units", "默认停在第一张表上"
    assert dictionary._column_tree.topLevelItemCount() == UNITS_COLUMNS
    assert "没有匹配" not in dictionary._count.text()
    assert "Units" in dictionary._count.text()


def test_set_instance_none_is_allowed_and_keeps_the_dictionary(dictionary) -> None:
    """没有数据源时窗口照常能用 —— 词典是产品自带的，不依赖打开的文件。"""
    dictionary.set_instance(None)
    assert dictionary._table_list.count() == 24
    assert dictionary._column_tree.topLevelItemCount() > 0
    label = dictionary._column_stats.text()
    assert "取值观测" in label
    assert "打开数据源" in label, "要说清现在只有词典本身，别让用户以为数据没了"


def test_search_narrows_the_table_list_and_falls_back(dictionary) -> None:
    dictionary._search.setText("单位本体")
    assert dictionary._table_list.count() == 1
    assert dictionary.selected_table() == "Units"
    assert "基础成本" in [
        dictionary._column_tree.topLevelItem(i).text(0)
        for i in range(dictionary._column_tree.topLevelItemCount())
    ]

    dictionary._search.setText("没有这张表")
    assert dictionary._table_list.count() == 0
    assert dictionary.selected_table() is None
    assert "没有匹配" in dictionary._count.text()
    assert dictionary._column_tree.topLevelItemCount() == 0


def test_search_narrows_the_columns_of_the_current_table(dictionary) -> None:
    dictionary._search.setText("基础成本")
    assert dictionary._table_list.count() == 1, "搜列名时左栏要留下那张表"
    assert dictionary.selected_table() == "Units"
    assert dictionary._column_tree.topLevelItemCount() == 1
    item = dictionary._column_tree.topLevelItem(0)
    assert item.text(0) == "基础成本"
    assert item.text(1) == "Cost", "第 2 列必须是英文列名（写回文件用的就是它）"
    assert item.text(2), "类型那一格不许空着"


def test_selecting_a_column_fills_the_detail_panel(dictionary) -> None:
    target = next(
        dictionary._column_tree.topLevelItem(i)
        for i in range(dictionary._column_tree.topLevelItemCount())
        if dictionary._column_tree.topLevelItem(i).text(1) == "CountryId"
    )
    dictionary._column_tree.setCurrentItem(target)
    assert "CountryId" in dictionary._column_title.text()
    assert "Units.CountryId" in dictionary._column_place.text()
    assert "→ Countries" in dictionary._column_place.text(), "外键要写清指向哪张表"
    assert dictionary._column_doc.text()
    assert dictionary._value_tree.topLevelItemCount() >= 1


def test_window_shows_the_observed_values_with_a_real_instance(dictionary, instance) -> None:
    """有数据源时右下角是**观测**（行数 / 取值种类 / 非默认 / 样例），不是编的。"""
    dictionary.set_instance(instance)
    assert dictionary.selected_table() == "Units"
    assert dictionary._column_tree.topLevelItemCount() == UNITS_COLUMNS

    cost = next(
        dictionary._column_tree.topLevelItem(i)
        for i in range(dictionary._column_tree.topLevelItemCount())
        if dictionary._column_tree.topLevelItem(i).text(1) == "Cost"
    )
    dictionary._column_tree.setCurrentItem(cost)
    stats = dictionary._column_stats.text()
    assert stats.startswith("取值观测（本实例）："), stats
    assert "行 · 取值种类" in stats
    assert dictionary._value_tree.topLevelItemCount() >= 1
    assert "行 × 31 列" in dictionary._count.text(), dictionary._count.text()


class _StubInstance:
    """够用的假实例：词典窗口只问它"有哪些表"与"给我这张表"（别为一条用例去读磁盘）。"""

    def __init__(self, tables: dict[str, Table]) -> None:
        self._tables = tables

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(self._tables)

    def table(self, name: str) -> Table:
        return self._tables[name]


# --------------------------------------------------------------------------- #
# 与"列清单"对账：词典说的列数，得和产品自己那张表对得上
# --------------------------------------------------------------------------- #


def test_naming_table_is_the_same_one_the_dictionary_uses() -> None:
    """`FlyPresets` 与 `PlaneFlyPresets` 只能是同一张表 —— 两套名字都认。"""
    naming = TableNaming("FlyPresetsJson", "FlyPresets")
    assert naming.zip_entry == "PlaneFlyPresets.json"
    by_entry = column_entries(naming.zip_entry[: -len(".json")])
    by_table = column_entries(naming.table)
    assert [e.column for e in by_entry] == [e.column for e in by_table]
    assert {e.dict_key for e in by_entry} == {"PlaneFlyPresets"}


def test_column_entry_defaults_are_safe() -> None:
    """没观测、没说明时，属性不许炸（界面会拿它们直接 setText）。"""
    entry = ColumnEntry(
        table="Units",
        dict_key="Units",
        column="Cost",
        cn="基础成本",
        doc="",
        kind="text",
        control="text",
        values=(),
        evidence="",
        foreign=None,
        missing=False,
    )
    assert entry.type_text == "—"
    assert entry.decode_text("12") == "12"
    assert entry.title == "基础成本"
