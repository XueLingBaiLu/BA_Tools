"""数据表页：三栏版式 + 整表行操作（加行 / 复制行 / 删行）。

为什么单独一个模块：

- 行操作是本轮新增的**写路径**（`Changeset.insert_row` / `delete_row`）。它最容易出的两个错
  都是**静默**的：
  ① 没进账本 —— 顶栏胶囊不亮、Ctrl+S 说"没有改动"，而表其实已经改了
     （`tests/test_editing_and_search.py` 那条 P0 用例守的是"改一格"，这里是行级）；
  ② 新行 Id **捡回本会话刚删掉的那个 Id** —— 表里确实没有那一行（守卫拦不住），
     但撤销删除会把旧行按原 Id 放回来，于是两行数据抢一个 Id。
  这两条都必须是断言，不能靠肉眼看。
- 三栏版式是设计稿 §3 块 9 的判据（**表 24 张、按行数降序** / 行 / 列），
  它靠"列表里有什么、什么顺序、一行几条"来断言。

口径写在 `app/widgets/table_browser.py` 的模块 docstring 里，别在这里另立一套：

| | 口径 |
|---|---|
| 新行 | **复制一行、只换 Id**；加行照抄**该表第一行**（空表才全列 None） |
| Id | 由外壳分配（`core/edit/clone.py::add_row` -> `next_row_id(表, 账本)`）： |
|  | 现有最大 +1，**本会话删过的 Id 不复用** |
| 反馈 | 不弹确认框；状态栏一句话（`status_message`）；Ctrl+Z 可撤（快捷键装在控件自己身上） |

替身通道说明：`_channel()` 按外壳那份注入的**签名**做一个替身，但落点是**真的**
`Changeset` —— 控件不许依赖 shell（见 `app/widgets/__init__.py`），
而账本是 core 的东西。所以"改动进没进账本""Id 会不会捡回刚删的"这些判据一条都没放水。
"""

from __future__ import annotations

import pytest

#: 与 conftest 一致的那个实例（24 张表都在）。
INSTANCE_ID = "52085"


def _make_browser(instance):
    """一个**没接外壳**的 TableBrowser（数据是这次用例自己那份干净实例）。"""
    from ba_mod_maker.app.widgets import TableBrowser

    browser = TableBrowser()
    browser.set_instance(instance)
    return browser


def _channel(changeset, *, rows: bool = True, undo: bool = True):
    """照外壳注入的那份写通道做一个替身，落到**真账本**上。

    行操作这一段与 `main_window` 那份实现同构：`insert_row` 内部走
    `core.edit.clone.add_row`（于是 Id 是 `next_row_id(表, 账本)`），
    `delete_row` 走 `Changeset.delete_row`。
    """
    from ba_mod_maker.app.widgets.table_model import TableWriteChannel
    from ba_mod_maker.core.edit.clone import add_row

    def insert_row(table: str, values) -> int:
        return add_row(changeset, table, dict(values))

    def delete_row(table: str, row_id: int) -> None:
        changeset.delete_row(table, row_id)

    def revert_table(table: str) -> int:
        """照 `EditSession.revert_table`：先还原列，再撤行（顺序有讲究）。"""
        column_changes = changeset.changes_of(table)
        row_changes = tuple(c for c in changeset.row_changes if c.table == table)
        for change in reversed(column_changes):
            changeset.revert(change)
        for row_change in reversed(row_changes):
            changeset.revert_row(row_change)
        return len(column_changes) + len(row_changes)

    def undo_last() -> str:
        records = changeset.row_changes
        if records:
            record = records[-1]
            changeset.revert_row(record)
            return f"已撤销：{record.summary}"
        return "没有可撤销的改动"

    return TableWriteChannel(
        write_cell=lambda t, r, c, v: changeset.set_value(t, r, c, v) is not None,
        is_dirty=lambda t, r, c: changeset.is_dirty(t, r, c),
        dirty_count=lambda t: (
            len(changeset.changes_of(t)) + sum(1 for rc in changeset.row_changes if rc.table == t)
        ),
        revert_table=revert_table,
        insert_row=insert_row if rows else None,
        delete_row=delete_row if rows else None,
        undo_last=undo_last if undo else None,
    )


def _wired(instance, **kwargs):
    """接上替身通道的浏览器 + 它背后那份真 Changeset。调用方负责 deleteLater。"""
    from ba_mod_maker.core.edit.changeset import Changeset

    changeset = Changeset(instance)
    browser = _make_browser(instance)
    browser.set_write_channel(_channel(changeset, **kwargs))
    return browser, changeset


@pytest.fixture()
def browser(qapp, instance):
    """一个**没接外壳**的 TableBrowser（数据是这次用例自己那份干净实例）。"""
    widget = _make_browser(instance)
    yield widget
    widget.deleteLater()


def _id_index(table) -> int:
    from ba_mod_maker.core.edit.changeset import ID_COLUMN

    return table.column_index(ID_COLUMN)


def _row_id(table, source_row: int) -> int:
    return table.rows[source_row][_id_index(table)]


# --------------------------------------------------------------------------- #
# 三栏：左栏（表，24 张，按行数降序）
# --------------------------------------------------------------------------- #


def test_the_left_pane_lists_every_table_by_descending_row_count(browser) -> None:
    """左栏 = 24 张表，**按行数降序**（设计稿块 9 原话），一条 = emoji + 中文名 + 英文名。"""
    names = browser.table_names_in_order
    counts = [browser._instance.table(name).row_count for name in names]

    assert len(names) == 24
    assert counts == sorted(counts, reverse=True), "左栏没有按行数降序"
    assert names[0] == "WeaponAmmunitions", "行数最多的那张（2357 行）该排第一"
    assert "数据表 24 张" in browser._table_caption.text()

    first = browser._table_list.item(0).text()
    assert "WeaponAmmunitions" in first and "武器携弹" in first
    assert "2357×6" in first, f"一条该带上「行数×列数」，实际是 {first!r}"


def test_the_table_filter_matches_chinese_and_english_names(browser) -> None:
    """左栏那个框：**中文名与英文表名都认**（用户是按"装甲"找表的）。"""
    browser._table_filter.setText("装甲")
    # 「装甲」同时命中两张表的中文名：装甲模板（Armors）与装甲归属（UnitArmors）。
    assert set(browser.table_names_in_order) == {"Armors", "UnitArmors"}

    # 英文名是一条子串匹配，所以「Armors」也命中 UnitArmors —— 这不是 bug，
    # 用户敲 "Specializations" 这种独有的才该只剩一条。
    browser._table_filter.setText("Specializations")
    assert browser.table_names_in_order == ("Specializations",)

    browser._table_filter.setText("没有这张表")
    assert browser.table_names_in_order == ()


def test_selecting_a_table_loads_it(browser) -> None:
    """选中左栏一条 -> 中栏换成那张表（对外契约：select_table + table_changed）。"""
    seen: list[str] = []
    browser.table_changed.connect(seen.append)

    browser.select_table("Units")

    assert browser.current_table_name == "Units"
    assert browser.model.table is browser._instance.table("Units")
    assert seen == ["Units"]


def test_the_right_pane_is_one_row_per_column_with_the_chinese_name(browser) -> None:
    """右栏 = 选中行的每一列**一行一条**：序号 │ 中文名 │ 值 │ 英文列名。"""
    browser.select_table("Armors")
    browser.select_row(0)

    table = browser.model.table
    assert browser._detail.columnCount() == 4
    assert browser._detail.topLevelItemCount() == table.column_count

    first = browser._detail.topLevelItem(0)
    assert first.text(0) == "01"
    assert first.text(1) == "装甲行 ID（主键）", "中文列名该来自词典"
    assert first.text(3) == "Id"
    assert first.text(2) == str(table.rows[0][0])


def test_the_middle_pane_keeps_the_filter_and_the_typed_delegate(browser) -> None:
    """三栏版式不许把老能力弄丢：**按列筛选**还在、**类型化编辑**（CellDelegate）还在。"""
    from ba_mod_maker.app.widgets.delegates import CellDelegate

    browser.select_table("Units")
    table = browser.model.table
    full = browser.model.rowCount()

    assert browser._view.model() is browser.model
    assert browser._view.model().columnCount() == table.column_count, "中栏必须是**全列**"
    assert isinstance(browser._view.itemDelegate(), CellDelegate)

    browser.set_filter("BTR")
    assert browser.model.is_filtered
    assert 0 < browser.model.rowCount() < full, "筛完该只剩命中的行"

    browser.set_filter("")
    assert browser.model.rowCount() == full


# --------------------------------------------------------------------------- #
# 行操作：加行 / 复制行 / 删行
# --------------------------------------------------------------------------- #


def test_add_row_copies_the_first_row_and_only_changes_the_id(qapp, instance) -> None:
    """＋ 加行：照抄**该表第一行**的值、只换 Id，而且新行在末尾、当场被选中。"""
    from ba_mod_maker.core.edit.clone import next_row_id

    browser, changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        table = browser.model.table
        before_rows = table.row_count
        expected_id = next_row_id(table, changeset)
        first_row = list(table.rows[0])

        messages: list[str] = []
        browser.status_message.connect(messages.append)
        assert browser.add_row() is True

        assert table.row_count == before_rows + 1
        new_row = table.rows[-1]
        assert new_row[_id_index(table)] == expected_id, "新 Id 该是「最大 +1」"
        assert [value for index, value in enumerate(new_row) if index != _id_index(table)] == [
            value for index, value in enumerate(first_row) if index != _id_index(table)
        ], "除了 Id，其余列该与第一行逐格相同"

        # 账本：一条**行级**改动（顶栏胶囊数的是它）
        assert [(rc.table, rc.row_id, rc.kind) for rc in changeset.row_changes] == [
            ("Armors", expected_id, "insert")
        ]
        # 新行被选中（用户点完就能看见它）
        current = browser._view.currentIndex()
        assert current.isValid()
        assert _row_id(table, browser.model.source_row(current.row())) == expected_id
        # 左栏那一条的「行数×列数」也跟着涨（不然它一直在说假话）
        item = browser._table_list.item(browser._list_row_of("Armors"))
        assert f"{before_rows + 1}×" in item.text()
        # 状态栏一句话（不弹窗）
        assert messages and f"#{expected_id}" in messages[-1]
        assert "Ctrl+Z 可撤" in messages[-1]
    finally:
        browser.deleteLater()


def test_copy_row_copies_the_selected_row(qapp, instance) -> None:
    """⧉ 复制行：复制**选中那一行** —— 内容照抄、Id 换新的。"""
    browser, changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        table = browser.model.table
        source_row = 5
        browser.select_row(source_row)
        source = list(table.rows[source_row])
        source_id = source[_id_index(table)]

        messages: list[str] = []
        browser.status_message.connect(messages.append)
        assert browser.copy_row() is True

        new_id = changeset.row_changes[-1].row_id
        assert new_id != source_id
        new_row = table.rows[-1]
        assert new_row[_id_index(table)] == new_id
        assert new_row[1:] == source[1:], "除了 Id，其余列该逐格相同"
        assert f"#{source_id}" in messages[-1] and f"#{new_id}" in messages[-1]
    finally:
        browser.deleteLater()


def test_delete_row_reports_who_referenced_it(qapp, instance) -> None:
    """🗑 删行：行真的没了；状态栏说清**有几行引用它**（口径只有一份：refs）。"""
    from ba_mod_maker.core.edit.refs import referencing_rows

    browser, changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        table = browser.model.table
        before_rows = table.row_count
        browser.select_row(0)
        victim = _row_id(table, 0)
        expected = len(referencing_rows(instance, "Armors", victim))
        assert expected > 0, "前提：Armors 第一行是被引用过的（否则这条用例测不到那句话）"

        messages: list[str] = []
        browser.status_message.connect(messages.append)
        assert browser.delete_row() is True

        assert table.row_count == before_rows - 1
        assert all(_row_id(table, i) != victim for i in range(table.row_count))
        assert [(rc.table, rc.row_id, rc.kind) for rc in changeset.row_changes] == [
            ("Armors", victim, "delete")
        ]
        assert f"已删除 Armors#{victim}" in messages[-1]
        assert f"有 {expected} 行引用它" in messages[-1], messages[-1]
    finally:
        browser.deleteLater()


def test_a_deleted_row_comes_back_on_undo(qapp, instance) -> None:
    """删一行 -> Ctrl+Z 放得回来（整行值都在账本里，不是"重读磁盘"）。"""
    browser, changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        table = browser.model.table
        browser.select_row(0)
        victim = _row_id(table, 0)
        whole_row = list(table.rows[0])
        before_rows = table.row_count
        assert browser.delete_row() is True
        assert table.row_count == before_rows - 1

        browser._undo_last_change()  # Ctrl+Z 那个动作（快捷键本身另有用例）

        assert table.row_count == before_rows
        restored = [row for row in table.rows if row[_id_index(table)] == victim]
        assert restored and restored[0] == whole_row, "放回来的整行值必须与删之前一致"
        assert not changeset.row_changes, "撤销之后账本里不该还留着那条删除"
    finally:
        browser.deleteLater()


def test_an_id_deleted_in_this_session_is_not_reused(qapp, instance) -> None:
    """❗删掉**最大 Id** 那一行之后再加行，新 Id 不许捡回刚删的那个。

    表里确实没有那个 Id 了（`insert_row` 的守卫拦不住），但撤销删除会把旧行按原 Id
    放回来 —— 两行数据抢一个 Id。所以 `next_row_id` 要连账本里的行级记录一起看，
    而这件事只有**拿得到账本的外壳**能做：本控件只把 Id 传 `None` 下去。
    """
    browser, changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        table = browser.model.table
        id_column = _id_index(table)
        biggest_row = max(range(table.row_count), key=lambda i: table.rows[i][id_column])
        biggest = table.rows[biggest_row][id_column]

        browser.select_row(biggest_row)
        assert browser.delete_row() is True
        assert browser.add_row() is True

        inserted = changeset.row_changes[-1]
        assert inserted.is_insert
        assert inserted.row_id == biggest + 1, "新 Id 捡回了本次会话刚删掉的那个 Id"
    finally:
        browser.deleteLater()


def test_discarding_table_changes_puts_deleted_rows_back(qapp, instance) -> None:
    """「放弃改动」也撤得回行级改动（它读的是同一份账本，见 EditSession.revert_table）。"""
    browser, _changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        table = browser.model.table
        before_rows = table.row_count
        browser.select_row(0)
        assert browser.delete_row() is True
        assert browser.model.dirty_count == 1
        assert browser._revert_button.isEnabled()

        browser._revert_button.click()

        assert table.row_count == before_rows, "行没放回来"
        assert browser.model.dirty_count == 0
        assert not browser._revert_button.isEnabled()
    finally:
        browser.deleteLater()


# --------------------------------------------------------------------------- #
# 没接通道 / 没接行操作：置灰 + 说一句，不许"偷偷改内存"
# --------------------------------------------------------------------------- #


def test_without_the_channel_the_row_buttons_are_dead_and_say_why(qapp, instance) -> None:
    """没接写通道：三个按钮置灰，动作被拒并说明原因（**拒绝**比静默改内存好）。"""
    browser = _make_browser(instance)
    try:
        browser.select_table("Armors")
        browser.select_row(0)

        assert not browser.model.has_write_channel
        assert not browser._add_button.isEnabled()
        assert not browser._copy_button.isEnabled()
        assert not browser._delete_button.isEnabled()
        assert browser.has_undo_shortcut is False, "没接 undo_last 就不该装 Ctrl+Z"

        messages: list[str] = []
        browser.status_message.connect(messages.append)
        assert browser.add_row() is False
        assert browser.copy_row() is False
        assert browser.delete_row() is False
        assert messages and "没接上行操作" in messages[0], messages
    finally:
        browser.deleteLater()


def test_a_channel_without_row_ops_degrades_instead_of_faking_it(qapp, instance) -> None:
    """外壳只接了四个口子（本轮之前的样子）时：行操作**停用**，不降级成自己改表。"""
    browser, _changeset = _wired(instance, rows=False, undo=False)
    try:
        browser.select_table("Armors")
        browser.select_row(0)
        table = browser.model.table
        before_rows = table.row_count

        assert not browser._add_button.isEnabled()
        assert not browser._delete_button.isEnabled()
        assert browser.has_undo_shortcut is False

        messages: list[str] = []
        browser.status_message.connect(messages.append)
        assert browser.delete_row() is False
        assert table.row_count == before_rows, "被拒了却还是把行删了"
        assert "没接上行操作" in messages[-1]
    finally:
        browser.deleteLater()


def test_the_undo_shortcut_is_installed_only_when_the_callback_is_there(qapp, instance) -> None:
    """Ctrl+Z 装在**控件自己**身上（窗口级那条会与单位编辑器的撞名）；没回调就不装。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence

    browser, _changeset = _wired(instance)
    try:
        assert browser.has_undo_shortcut is True
        shortcut = browser._undo_shortcut
        assert shortcut.key() == QKeySequence(QKeySequence.StandardKey.Undo)
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    finally:
        browser.deleteLater()

    browser2, _changeset2 = _wired(instance, undo=False)
    try:
        assert browser2.has_undo_shortcut is False
    finally:
        browser2.deleteLater()


def test_the_row_menu_mirrors_the_toolbar(qapp, instance) -> None:
    """右键 = 同一套行操作（与工具栏那三颗一一对应，可用性也一致）。

    查的是菜单**内容**而不是"弹出来了"：`exec` 是阻塞的，离屏平台上也不该去弹它。
    """
    browser, _changeset = _wired(instance)
    try:
        browser.select_table("Armors")
        browser.select_row(0)

        actions = [(action.text(), action.isEnabled()) for action in browser._row_menu().actions()]

        assert [text for text, _ in actions] == ["＋ 加行", "⧉ 复制行", "🗑 删行"]
        assert all(enabled for _text, enabled in actions), "接了通道就该三项都亮"
    finally:
        browser.deleteLater()


# --------------------------------------------------------------------------- #
# 端到端：真账本（EditSession）-> 顶栏胶囊 -> 保存后包里真有这一行
# --------------------------------------------------------------------------- #


def test_a_row_added_here_lights_the_pill_and_lands_in_the_package(window, tmp_path) -> None:
    """端到端（**外壳真接的那份通道** + 真 EditSession）：加一行 -> 胶囊亮 -> 保存后包里有它。

    这一条刻意不塞替身：它守的是"外壳（main_window）到底接没接上"这条接缝 ——
    没接上时 `can_edit_rows` 就是 False，这里当场红。
    """
    from ba_mod_maker.app.shell.main_window import PAGE_TABLE
    from ba_mod_maker.core.edit.package import read_package_rows

    window.show_page(PAGE_TABLE)
    browser = window._browser
    browser.select_table("Armors")

    session = window._edit_session
    assert browser.model.can_edit_rows, (
        "外壳没把「加行 / 删行」两个口子接上（见 TableWriteChannel）"
    )
    try:
        table = browser.model.table
        before_rows = table.row_count
        assert browser.add_row() is True
        new_id = session.changeset.row_changes[-1].row_id

        assert table.row_count == before_rows + 1
        # ① 顶栏：保存按钮亮 + 胶囊写清"1 处未保存"（脏态的唯一指示）
        assert session.change_count == 1
        assert window._save_button.isEnabled()
        assert "1 处未保存" in window._save_pill.text()
        # ② 只进缓存，不落盘
        assert session.last_saved_path is None
        # ③ 保存 -> 包里真有这一行，Id 就是刚分配的那个
        session.save()
        rows = read_package_rows(tmp_path / "BA_Mod.zip")["Armors"]
        assert any(row["Id"] == new_id for row in rows), "保存后包里没有这一行"
        assert session.change_count == 0
    finally:
        session.revert_all()


# --------------------------------------------------------------------------- #
# 外壳那一半（Lead 接的线）：三个回调真的接上了，而且 Ctrl+Z 是**页面级**的
# --------------------------------------------------------------------------- #


def test_the_shell_can_add_and_undo_a_table_row(window) -> None:
    """外壳侧：加一行 → 进账本（顶栏胶囊看得见）→ `_undo_table_row()` 把它撤掉。

    控件那边的替身通道在上面几条用例里；这一条钉的是**真接线**（main_window 注入的
    三个回调），以及 Ctrl+Z 撤的是**这一页自己的**行操作（不是账本里最后一条）。
    """
    changeset = window._edit_session.changeset
    assert changeset is not None, "window 夹具应当已经打开一份数据"
    table = changeset.instance.table("Armors")
    columns = tuple(table.columns)
    before = len(table.rows)
    first = dict(zip(columns, table.rows[0], strict=False))

    new_id = window._insert_table_row("Armors", first)
    assert new_id > 0
    assert len(table.rows) == before + 1
    assert window._edit_session.change_count > 0, "行操作没进账本 —— 顶栏胶囊看不见它"

    message = window._undo_table_row()
    assert "已撤销" in message, message
    assert len(table.rows) == before, "Ctrl+Z 没把那一行拿掉"


def test_undo_last_reports_honestly_when_the_page_stack_is_empty(window) -> None:
    """这一页没做过行操作时，Ctrl+Z 如实说一句（不假装撤掉了什么、也不去撤别的页）。"""
    window._table_row_undo.clear()
    assert window._undo_table_row() == "数据表页没有可撤销的行操作"
