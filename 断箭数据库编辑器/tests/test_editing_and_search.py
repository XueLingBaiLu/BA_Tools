"""编辑（类型系统）与检索的测试。

为什么要单独测这两件事：

- **类型转换错了会静默毁数据**——把 `5500` 写成 `"5500"` 存回去，
  游戏读到字符串会出错或静默失效，而界面上看不出任何异常；
- **检索是用户找东西的唯一通道**，它漏了就是真漏了。

类型与检索口径全部跑在 core 层，**不需要 Qt**；只有最后「检索页」那几条
要点一个离屏的 QApplication（控件行为不能只靠肉眼）。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import (
    ColumnKind,
    ColumnSpec,
    find_dataset_root,
    format_value,
    infer_column_specs,
    load_dataset,
    parse_value,
    search_instance,
)
from ba_mod_maker.core.errors import TableError

# --------------------------------------------------------------------------- #
# 类型推断
# --------------------------------------------------------------------------- #


def test_infers_each_kind() -> None:
    """五种类型各自能被认出。"""
    columns = ("i", "f", "b", "s", "unknown")
    rows = [[1, 1.5, True, "x", None]]
    specs = infer_column_specs(columns, rows)

    assert [s.kind for s in specs] == [
        ColumnKind.INT,
        ColumnKind.FLOAT,
        ColumnKind.BOOL,
        ColumnKind.TEXT,
        ColumnKind.UNKNOWN,
    ]


def test_bool_is_not_mistaken_for_int() -> None:
    """❗ 在 Python 里 `isinstance(True, int)` 为真。

    若先判 int，bool 列会被误判成整数列，
    于是用户会被要求填 1/0 而不是看到下拉框。
    """
    specs = infer_column_specs(("flag",), [[True], [False]])
    assert specs[0].kind == ColumnKind.BOOL


def test_null_does_not_decide_kind() -> None:
    """前几行是 None 不应影响类型判断，且应标记为可空。"""
    specs = infer_column_specs(("v",), [[None], [None], [7]])
    assert specs[0].kind == ColumnKind.INT
    assert specs[0].nullable is True


def test_mixed_types_fall_back_to_text() -> None:
    """同列出现两种类型时降级为文本——**宁可当文本，也不能猜**。

    若仍按第一个非空值的类型硬转，用户修改另一种类型的单元格时会被拒绝或被改错。
    """
    specs = infer_column_specs(("v",), [[1], ["abc"]])
    assert specs[0].kind == ColumnKind.TEXT


def test_all_null_column_is_unknown_and_nullable() -> None:
    specs = infer_column_specs(("v",), [[None], [None]])
    assert specs[0].kind == ColumnKind.UNKNOWN
    assert specs[0].nullable is True


# --------------------------------------------------------------------------- #
# 文本 -> 值
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kind", "nullable", "text", "expected"),
    [
        (ColumnKind.INT, False, "5500", 5500),
        (ColumnKind.INT, False, "-1", -1),
        (ColumnKind.INT, False, "  42  ", 42),
        (ColumnKind.FLOAT, False, "1.25", 1.25),
        (ColumnKind.FLOAT, False, "-3", -3.0),
        (ColumnKind.BOOL, False, "true", True),
        (ColumnKind.BOOL, False, "是", True),
        (ColumnKind.BOOL, False, "false", False),
        (ColumnKind.BOOL, False, "否", False),
        (ColumnKind.BOOL, False, "", False),
        (ColumnKind.TEXT, False, "RU_BTR82A", "RU_BTR82A"),
    ],
)
def test_parse_value_happy_path(kind, nullable, text, expected) -> None:
    spec = ColumnSpec(name="c", kind=kind, nullable=nullable)
    result = parse_value(spec, text)
    assert result == expected
    assert type(result) is type(expected), "类型必须一致，不能只是值相等"


def test_empty_means_none_only_when_nullable() -> None:
    """空串是否等于 null，取决于该列原数据里有没有空值。

    这是个容易被写成“一律当 null”的地方——那会让必填列静默变空。
    """
    nullable = ColumnSpec(name="c", kind=ColumnKind.INT, nullable=True)
    required = ColumnSpec(name="c", kind=ColumnKind.INT, nullable=False)

    assert parse_value(nullable, "") is None
    with pytest.raises(TableError) as excinfo:
        parse_value(required, "")
    assert excinfo.value.hint, "报错必须带修复建议"


@pytest.mark.parametrize("bad", ["abc", "5.5", "1,000", "0x10"])
def test_bad_integer_is_rejected(bad: str) -> None:
    """整数列必须拒绝非整数。注意 "5.5" 也要拒绝——静默截成 5 是更坏的行为。"""
    spec = ColumnSpec(name="c", kind=ColumnKind.INT, nullable=False)
    with pytest.raises(TableError):
        parse_value(spec, bad)


def test_bad_bool_is_rejected() -> None:
    spec = ColumnSpec(name="c", kind=ColumnKind.BOOL, nullable=False)
    with pytest.raises(TableError):
        parse_value(spec, "也许")


def test_unknown_kind_stores_text_verbatim() -> None:
    """全空列（UNKNOWN）不能把非空输入吞掉——否则用户永远填不进去东西。

    但**空输入仍然是 null**：该列原本全是 null，往里塞一个空字符串
    会改变数据形状（从“没有值”变成“有一个空字符串”）。
    """
    spec = ColumnSpec(name="c", kind=ColumnKind.UNKNOWN, nullable=True)
    assert parse_value(spec, "") is None
    assert parse_value(spec, "abc") == "abc"


# --------------------------------------------------------------------------- #
# 值 -> 文本（编辑框里的初始值）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "true"),
        (False, "false"),
        (5500, "5500"),
        (1.5, "1.5"),
        ("x", "x"),
    ],
)
def test_format_value_round_trips(value, expected) -> None:
    """❗ format 完必须能 parse 回去——否则一编辑就把值改坏了。

    尤其注意 False→"false" 而非空串：若输出空串，再次提交就会把 False 变成 None。
    """
    kind = {
        bool: ColumnKind.BOOL,
        int: ColumnKind.INT,
        float: ColumnKind.FLOAT,
        str: ColumnKind.TEXT,
    }.get(type(value), ColumnKind.UNKNOWN)
    spec = ColumnSpec(name="c", kind=kind, nullable=True)

    text = format_value(spec, value)
    assert text == expected
    assert parse_value(spec, text) == value


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #


def test_empty_query_returns_nothing() -> None:
    """空关键词不能把整库倒出来。"""
    from ba_mod_maker.core.data.search import SearchResult

    result = SearchResult(query="  ", hits=(), truncated=False, scanned_cells=0)
    assert result.count == 0


# --------------------------------------------------------------------------- #
# 真实数据上的端到端验证
# --------------------------------------------------------------------------- #

DATASET_ROOT = find_dataset_root()

pytestmark_real = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")


@pytest.fixture(scope="module")
def instance():
    if DATASET_ROOT is None:
        pytest.skip("无数据源")
    return load_dataset(DATASET_ROOT).instance("52085")


@pytestmark_real
class TestOnRealData:
    """在真实 24 张表上验证类型推断与检索。"""

    def test_column_kinds_look_right(self, instance) -> None:
        """实测：主键列是 int，名称列是文本，不会出现全表 UNKNOWN。"""
        weapon_ammo = instance.table("WeaponAmmunitions")
        specs = infer_column_specs(weapon_ammo.columns, weapon_ammo.rows)
        kinds = {spec.name: spec.kind for spec in specs}

        assert kinds["Id"] == ColumnKind.INT
        assert kinds["Quantity"] == ColumnKind.INT
        assert ColumnKind.UNKNOWN not in set(kinds.values()), "真实数据不应出现全空列"

    def test_no_text_column_is_mistyped_as_int(self, instance) -> None:
        """字符串列不能被当成整数列（否则用户修改会被拒绝）。"""
        units = instance.table("Units")
        specs = infer_column_specs(units.columns, units.rows)
        kinds = {spec.name: spec.kind for spec in specs}

        for name in ("Name", "HUDName", "ModelFileName", "AudioPreset"):
            assert kinds[name] == ColumnKind.TEXT, f"{name} 应为文本列"

    def test_search_finds_known_value(self, instance) -> None:
        """实测：BTR-82 在 52085 里命中 6 条（HUDName / Name 等）。"""
        result = search_instance(instance, "BTR-82")
        assert result.count > 0
        assert not result.truncated
        assert any(hit.value == "BTR-82" for hit in result.hits)

    def test_search_is_case_insensitive(self, instance) -> None:
        lower = search_instance(instance, "btr-82")
        upper = search_instance(instance, "BTR-82")
        assert lower.count == upper.count

    def test_search_reports_column_hits_separately(self, instance) -> None:
        """搜列名时应报“列名命中”，而不是伪造出一堆单元格命中。"""
        result = search_instance(instance, "ModelFileName")
        assert result.count > 0
        assert all(hit.is_column_hit for hit in result.hits), (
            "ModelFileName 是列名，不应出现单元格命中"
        )

    def test_search_never_lies_about_truncation(self, instance) -> None:
        """命中数超过上限时必须明说被截断。"""
        result = search_instance(instance, "0", limit=10)
        assert result.truncated is True
        assert result.count <= 10

    def test_search_closed_set_matches_manual_count(self, instance) -> None:
        """手算一个小范围，确认检索没漏。

        在 Units 的 Id 列里找 "1013"，手算出现次数后对比。
        这种“小范围人工对账”是防检索漏报的最直接手段。
        """
        units = instance.table("Units")
        column = units.column_index("Id")
        manual = sum(1 for row in units.rows if str(row[column]) == "1013")

        result = search_instance(instance, "1013", tables=("Units",))
        cell_hits = [h for h in result.hits if not h.is_column_hit and h.column == "Id"]
        assert len(cell_hits) == manual, "Id 列的命中数应与人工统计一致"


# --------------------------------------------------------------------------- #
# 检索页：一个框（界面部分，离屏 Qt）
#
# 口径（搜哪些列、怎么排、截断怎么算）由 core 的用例守着；这里只测**画出来的东西**：
# 四列表头、输入即筛、空框给提示、回车发出去的是行 Id。
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    return app


@pytest.fixture()
def search_page(qapp, instance):
    from ba_mod_maker.app.widgets.global_search import GlobalSearch

    page = GlobalSearch()
    page.set_instance(instance)
    yield page
    page.deleteLater()


def _rows(page) -> list[list[str]]:
    """结果表里现在画着什么（一行四个格）。"""
    return [
        [page._results.topLevelItem(i).text(c) for c in range(page._results.columnCount())]
        for i in range(page._results.topLevelItemCount())
    ]


def test_search_page_has_the_four_result_columns(search_page) -> None:
    """结果行 = 表 | Id | 名称 | 命中的是哪一列（Id **要显示**：用户口径）。"""
    headers = [search_page._results.headerItem().text(i) for i in range(4)]
    assert headers == ["表", "Id", "名称", "命中的是哪一列"]
    assert search_page._results.columnCount() == 4


def test_search_page_filters_as_you_type(search_page) -> None:
    """输入即筛：打完字就能出结果，不用先按回车。"""
    from ba_mod_maker.app.viewmodel.table_labels import table_emoji, table_title

    search_page._input.setText("BTR-82")
    search_page.run_search()
    rows = _rows(search_page)
    # 第一格 = **emoji + 中文表名**（设计稿块 10；从前是裸表名 "Units"）。
    assert rows[0][1] == "#1"
    assert rows[0][0].endswith(table_title("Units")), rows[0][0]
    assert table_emoji("Units") in rows[0][0]
    assert "共" in search_page._summary.text()


def test_search_page_empty_query_shows_a_hint_not_a_wall(search_page) -> None:
    """空串给一句提示，不倒一面墙（验收判据四）。"""
    search_page._input.setText("BTR-82")
    search_page.run_search()
    assert search_page._results.topLevelItemCount() > 0

    search_page._input.setText("")
    assert search_page._results.topLevelItemCount() == 0
    assert "输入" in search_page._summary.text()


def test_search_page_shows_the_id_and_the_matched_column(search_page) -> None:
    """图标 / 缩略图文件名也能查到那一行，并列清是哪一列命中的。"""
    from ba_mod_maker.app.viewmodel.table_labels import table_title

    search_page.run_search("US_STRYKER_MC-Label")
    first = _rows(search_page)[0]
    assert first[0].endswith(table_title("Units")), first[0]
    assert first[1:] == ["#5", "Stryker MC", "ThumbnailFileName"]


def test_search_page_pure_digits_hit_that_id(search_page) -> None:
    search_page.run_search("18")
    first = _rows(search_page)[0]
    assert first[1] == "#18"
    assert first[3] == "Id"
    assert "共" in search_page._summary.text()


def test_search_page_enter_opens_the_selected_row(search_page) -> None:
    """回车 = 打开那一条；发出去的第二项是**行 Id**，不是行下标。"""
    opened: list[tuple[str, int, str]] = []
    search_page.hit_activated.connect(lambda *args: opened.append(args))
    search_page.run_search("BTR-82")
    search_page._activate_current()
    assert opened == [("Units", 1, "Name")]


# --------------------------------------------------------------------------- #
# 块 10 · 右栏 = 刚点的那一行的全部列（命中格标黄 + 挂「命中」）
#
# 设计图 _work/design/block13.png。这一块 2026-10-02 才接上 —— 在那之前
# `RowEntriesPanel.set_cells(hits=…)` 与 QSS 里的 `[hit="true"]` / `role="hit-chip"`
# **全仓零调用方**（接口和样式都写好了，没人接）。
# --------------------------------------------------------------------------- #


def test_search_page_selection_fills_the_right_pane(search_page) -> None:
    """一点/一选 = 右栏铺出**这一行的全部列**（31 列一条不少）。"""
    result = search_page.run_search("US_STRYKER_MC-Label")
    hit = result.hits[0]
    rows = search_page._detail.rows
    assert len(rows) == 31, f"Units 那一行是 31 列，右栏铺了 {len(rows)} 条"
    assert rows[0].cell.ref.table == hit.table
    assert all(row.cell.ref.row_id == hit.row_id for row in rows)


def test_search_page_marks_exactly_the_matched_cells(search_page) -> None:
    """标黄的**只有命中的那几列** —— 多标一格就是在骗人。"""
    result = search_page.run_search("US_STRYKER_MC-Label")
    hit = result.hits[0]
    marked = {
        row.cell.ref.column for row in search_page._detail.rows if row.property("hit") == "true"
    }
    assert marked == set(hit.columns), f"标黄的列对不上：{marked} vs {hit.columns}"
    assert marked, "一格都没标黄 = 右栏那条路没接上"


def test_search_page_selecting_does_not_navigate_but_enter_does(search_page) -> None:
    """⭐ 交互接缝（设计稿块 10）：**选一条只填右栏**；Enter / 双击才真跳过去。

    从前单击直接跳走 —— 那样右栏永远没机会显示（block13 那一帧就是
    "左边点着、右边看着"）。
    """
    opened: list[tuple[str, int, str]] = []
    search_page.hit_activated.connect(lambda *args: opened.append(args))
    search_page.run_search("BTR-82")
    assert opened == [], "run_search 自己不该发跳转信号"
    search_page._results.setCurrentItem(search_page._results.topLevelItem(1))
    assert opened == [], "选一条只该填右栏，不该跳走"
    assert search_page._detail.rows, "选了之后右栏是空的"

    shown = search_page._current  # 右栏正显示的那一条
    assert shown is not None
    search_page._activate_current()  # = 回车
    assert len(opened) == 1, opened
    # 打开的就是**右栏正显示的那一行**（不是"永远第一条"）。
    assert (opened[0][0], opened[0][1]) == (shown.table, shown.row_id), (opened, shown)


def test_search_page_open_button_takes_the_same_route(search_page) -> None:
    """右栏那颗按钮与双击/回车**同一条路**（同一个信号），不是第二条实现。"""
    opened: list[tuple[str, int, str]] = []
    search_page.hit_activated.connect(lambda *args: opened.append(args))
    search_page.run_search("BTR-82")
    assert search_page._open_button.isVisibleTo(search_page)
    # Units 走的是**单位编辑器**那条路，按钮上就得这么写（不许写"在数据表里打开"骗人）。
    assert "单位编辑器" in search_page._open_button.text()
    search_page._open_button.click()
    assert len(opened) == 1 and opened[0][0] == "Units"


# --------------------------------------------------------------------------- #
# 块 10 · 右栏只读**但可导航**（用户 2026-10-02 定的口径）
#
# 只读的理由：检索页的职责是"找"，改归编辑器 / 数据表页。
# 但"点了没反应"是另一种坏 —— 所以点值那一格**一律有事发生**：
#   外键 -> 跳到它指向的那一行（与行尾 ↗ 同一条路）；
#   不是外键 -> 打开这一行（与右上角那颗按钮同一条路）。
# --------------------------------------------------------------------------- #


def test_search_page_foreign_key_value_goes_to_the_linked_row(search_page) -> None:
    """点一个**外键**值 = 跳到它指向的那一行（"往下走"那条路）。"""
    opened: list[tuple[str, int, str]] = []
    search_page.hit_activated.connect(lambda *args: opened.append(args))
    result = search_page.run_search("US_STRYKER_MC-Label")
    hit = result.hits[0]

    linked = [row for row in search_page._detail.rows if row.link_target is not None]
    assert linked, "样本单位应当至少有一个外键列（所属国家…）"
    row = linked[0]
    want_table, want_id = row.link_target

    row.value_button.click()
    assert opened == [(want_table, want_id, "")], opened
    assert want_table != hit.table, "外键必须指向**别的**表，否则这条用例没在测东西"


def test_search_page_plain_value_opens_the_row_itself(search_page) -> None:
    """点一个**不是外键**的值 = 打开这一行（绝不许"点了没反应"）。"""
    opened: list[tuple[str, int, str]] = []
    search_page.hit_activated.connect(lambda *args: opened.append(args))
    result = search_page.run_search("US_STRYKER_MC-Label")
    hit = result.hits[0]

    plain = [row for row in search_page._detail.rows if row.link_target is None]
    assert plain
    plain[0].value_button.click()
    assert opened == [(hit.table, hit.row_id, hit.column)], opened


def test_search_page_arrow_takes_the_same_linked_route(search_page) -> None:
    """行尾那个 ↗ 与"点值那一格"走的是同一条路（同一个出口）。"""
    opened: list[tuple[str, int, str]] = []
    search_page.hit_activated.connect(lambda *args: opened.append(args))
    search_page.run_search("US_STRYKER_MC-Label")
    search_page._detail.link_activated.emit("Countries", 2)
    assert opened == [("Countries", 2, "")], opened


def test_search_page_without_a_query_has_no_right_pane(search_page) -> None:
    """空框：右栏留白 + 那颗按钮藏起来（不显示上一条的残影）。"""
    search_page.run_search("BTR-82")
    assert search_page._detail.rows

    search_page._input.setText("")
    assert search_page._results.topLevelItemCount() == 0
    assert search_page._detail.rows == (), "清空之后右栏还留着上一条"
    assert not search_page._open_button.isVisibleTo(search_page)


def test_search_page_without_a_dataset_says_so(qapp) -> None:
    from ba_mod_maker.app.widgets.global_search import GlobalSearch

    page = GlobalSearch()
    page.set_instance(None)
    assert page.run_search("x") is None
    assert "尚未加载" in page._summary.text()


# --------------------------------------------------------------------------- #
# 数据表页的写入：必须进**同一份账本**（AGENTS §4.5 / 本轮 P0）
#
# 从前 `TableModel.setData` 直接写 `Table.rows` + 自己的 `_dirty`：
# 顶栏胶囊（只看 EditSession.change_count）看不见它 —— 💾 不亮、Ctrl+S 说"没有改动"，
# 而表其实已经被改了。现在写入经外壳注入的写通道落到那一份账本上，按行 **Id** 寻址。
# --------------------------------------------------------------------------- #


def _units_page(window):
    """把数据表页切到 Units，返回 (浏览器, 模型, 表)。"""
    from ba_mod_maker.app.shell.main_window import PAGE_TABLE

    window.show_page(PAGE_TABLE)
    browser = window._browser
    browser.select_table("Units")
    model = browser.model
    table = model.table
    assert table is not None and table.name == "Units"
    return browser, model, table


def test_a_table_cell_edit_goes_into_the_shared_account_book(window) -> None:
    """改一格：值写进表、**改动进账本**（顶栏跟着亮），而且按行 Id 记。"""
    from PySide6.QtCore import Qt

    from ba_mod_maker.core.edit.changeset import ID_COLUMN

    _browser, model, table = _units_page(window)
    id_index = table.column_index(ID_COLUMN)
    column = table.column_index("Cost")
    row_id = table.rows[0][id_index]
    before = table.rows[0][column]

    try:
        assert model.source_row(0) == 0, "前提：没过滤，可视行 0 就是原始行 0"
        assert (
            model.setData(model.index(0, column), str(before + 1), Qt.ItemDataRole.EditRole) is True
        )
        assert table.rows[0][column] == before + 1, "值没写进去"

        # ① 账本里记着，而且键是**行 Id 不是行下标**
        assert window._edit_session.change_count == 1
        change = window._edit_session.changeset.changes[0]
        assert (change.table, change.row_id, change.column) == ("Units", row_id, "Cost")
        # ② 顶栏跟着亮（从前数据表页改了它看不见）
        assert window._save_button.isEnabled(), "有改动 -> 保存按钮该亮"
        assert "1 处未保存" in window._save_pill.text()
        # ③ 只进缓存，不落盘
        assert window._edit_session.last_saved_path is None
        # ④ 黄底读的也是账本（不是模型自己记的一份）
        assert model.is_dirty(0, column)
        assert model.dirty_count == 1
    finally:
        # 这份实例是**模块级共用**的（conftest 的说明）：改完必须收干净。
        window._edit_session.revert_all()
    assert table.rows[0][column] == before
    assert window._edit_session.change_count == 0


def test_discarding_table_changes_writes_the_old_value_back(window) -> None:
    """「放弃改动」= 账本里逐条写回旧值，**不再从磁盘重读整表**。

    重读的代价：`Table` 对象被换掉（别处持有的引用当场悬空）、磁盘上本来就与内存
    不一致的地方一起被抹掉、来源是 zip / 容器时磁盘上根本没有散文件可读。
    """
    from PySide6.QtCore import Qt

    browser, model, table = _units_page(window)
    column = table.column_index("Cost")
    before = table.rows[0][column]
    rows_identity = table.rows
    table_identity = table

    assert model.setData(model.index(0, column), str(before + 7), Qt.ItemDataRole.EditRole) is True
    assert window._edit_session.change_count == 1

    browser._revert_button.click()

    assert table.rows[0][column] == before, "旧值没写回去"
    assert window._edit_session.change_count == 0
    assert model.dirty_count == 0
    assert model.table is table_identity and model.table.rows is rows_identity, (
        "整表被重读了（行列表都换了）—— 放弃改动该走账本"
    )
    assert not window._save_button.isEnabled()


def test_a_browser_without_the_write_channel_refuses_to_edit(qapp, instance) -> None:
    """没接写通道时**拒绝**写入并说明原因 —— 不许"偷偷改内存"（那正是 P0 的形状）。"""
    from PySide6.QtCore import Qt

    from ba_mod_maker.app.widgets import TableBrowser
    from ba_mod_maker.core.errors import TableError

    browser = TableBrowser()
    try:
        browser.set_instance(instance)
        browser.select_table("Units")
        model = browser.model
        table = model.table
        assert table is not None
        column = table.column_index("Cost")
        before = table.rows[0][column]
        assert not model.has_write_channel

        with pytest.raises(TableError) as excinfo:
            model.setData(model.index(0, column), str(before + 1), Qt.ItemDataRole.EditRole)

        assert "写盘通道" in str(excinfo.value), str(excinfo.value)
        assert table.rows[0][column] == before, "拒绝了却还是把内存改了"
    finally:
        browser.deleteLater()


# --------------------------------------------------------------------------- #
# 底部「被谁引用」面板（只读）
#
# 反查口径只有一份：core.edit.refs.referencing_rows()（T1 交付）。
# 面板只负责画出来 + 双击跳过去；还没接上时如实说，不画成"没有引用者"。
# --------------------------------------------------------------------------- #

#: 一个**一定有引用者**的单位（SquadMembers / UnitArmors 靠 UnitId 认爹，见 test_edit_foreign）。
REFERENCED_UNIT = 645


def _select_unit_row(window, unit_id: int = REFERENCED_UNIT):
    """在数据表页选中某个单位那一行，返回 (浏览器, 面板)。"""
    from ba_mod_maker.core.edit.changeset import ID_COLUMN

    browser, _model, table = _units_page(window)
    id_index = table.column_index(ID_COLUMN)
    row = next(i for i, values in enumerate(table.rows) if values[id_index] == unit_id)
    browser.select_row(row)
    return browser, window._refs_panel


def _expected_refs(window):
    """这一行**应该**有哪些引用者（直接问 core 那份唯一口径）。"""
    from ba_mod_maker.core.edit.refs import referencing_rows

    expected = referencing_rows(window._instance, "Units", REFERENCED_UNIT)
    assert expected, "前提：645 号单位一定有引用者"
    return expected


def test_the_refs_panel_lists_who_references_the_selected_row(window) -> None:
    """选中一行 -> 底部列出引用者（表 / Id / 列 / 名称四列）。"""
    _browser, panel = _select_unit_row(window)
    expected = _expected_refs(window)

    assert panel.hit_count == len(expected)
    assert f"Units #{REFERENCED_UNIT}" in panel._caption.text()
    first = expected[0]
    item = panel._tree.topLevelItem(0)
    assert item.text(0) == first.table
    assert item.text(1) == f"#{first.row_id}"
    assert item.text(2) == first.column
    assert item.text(3) == first.label


def test_double_clicking_a_reference_jumps_to_that_row(window) -> None:
    """双击一条引用 -> 跳到那张表那一行（面板是只读的，跳转才算用得上）。"""
    browser, panel = _select_unit_row(window)
    first = _expected_refs(window)[0]

    panel._tree.setCurrentItem(panel._tree.topLevelItem(0))
    assert panel.activate_current() is True

    assert browser.current_table_name == first.table, "没跳到那张表"
    model = browser.model
    current = browser._view.currentIndex()
    assert current.isValid(), "跳过去却没有选中任何行"
    table = model.table
    row_id = table.rows[model.source_row(current.row())][table.column_index("Id")]
    assert row_id == first.row_id


def test_the_refs_panel_says_so_when_the_lookup_is_not_wired_yet(window, monkeypatch) -> None:
    """反查没落地时写「还没接上」，**不**画成"没有引用者"（"没有"与"不知道"是两回事）。"""
    from ba_mod_maker.core.edit import refs as refs_module

    monkeypatch.delattr(refs_module, "referencing_rows", raising=False)

    _browser, panel = _select_unit_row(window)

    assert panel.hit_count == 0
    assert "还没接上" in panel._hint.text(), panel._hint.text()


# --------------------------------------------------------------------------- #
# 就地候选列表（「🔁 换…」）关掉时的摘除：必须走 detach()
# --------------------------------------------------------------------------- #


def test_closing_the_picker_detaches_it_instead_of_a_bare_setparent(window, monkeypatch) -> None:
    """关掉候选列表必须走 `detach()`（hide -> setParent(None) -> deleteLater）。

    为什么钉"调用"而不是"看得见看不见"：那一刻控件**是可见的**，可见控件被
    `setParent(None)` 会被 Qt 映射成一个顶层窗口 —— 就是用户报过三次的
    "一闪而过的小弹窗"。而离屏平台上 `setParent(None)` 自己也会把控件标成隐藏，
    **测不出**那个闪现（实测：isHidden 立即为 True）。所以这里钉的是调用路径；
    "全 app 不许出现裸 setParent(None)"由源码扫描那条守卫管。
    """
    from ba_mod_maker.app.widgets import row_entries as row_entries_module
    from ba_mod_maker.app.widgets.detach import detach as real_detach

    window._open_unit_editor(21)  # Stryker RV（与 test_ui_redesign 同一个样本）
    page = window._unit_editor.main_page
    node = page.tree.tree.group("armor").children[0]
    assert page.tree.activate_key(node.key), node.key

    panel = page.entries
    panel._open_picker()
    picker = panel._picker
    assert picker is not None

    seen: list[object] = []
    monkeypatch.setattr(row_entries_module, "detach", lambda widget: seen.append(widget))
    panel._close_picker()

    assert seen == [picker], "关候选列表没走 detach() —— 可见控件会闪成顶层小窗"
    real_detach(picker)  # 收尾：真的把它摘掉，别给下一条用例留控件


def test_the_search_entry_focuses_the_box_on_the_first_try(window) -> None:
    """顶栏「🔎 按名查 Id」：**点一次**光标就该在框里。

    用户口径 2026-10-01：「为什么我搜索栏要点两遍？」。

    根因：`show_page` 刚 setCurrentWidget，新页还没被映射出来，这时候 `setFocus()` 是空操作 ——
    第一下只切了页。所以聚焦延到下一轮事件循环（同 `InlineEditor.focus_input` 那条教训）。
    """
    from conftest import settle

    panel = window._search_panel
    window._focus_search()
    settle()

    assert window._tabs.currentWidget() is panel, "没切到检索页"
    assert panel.window().focusWidget() is panel._input, (
        "点一次之后光标没进搜索框 —— 用户还得再点一下"
    )
