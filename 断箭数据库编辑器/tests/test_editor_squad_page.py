"""步兵班专用页（第 4 步 · 图 B ③）：一行一名士兵、+1 / ✕、右栏 6 列。

为什么不用假数据源：这一页要验的正是「控件和数据接对了没有」—— 点 +1 到底有没有
真往 SquadMembers 里插一行、卡片跟不跟、行尾 ✕ 删的是不是这一名、点一行右栏换不换。
假数据源测出来的只是"我以为的接线"（与 tests/test_unit_editor.py 同一个理由）。

判据（共享任务 task-1 的验收）：
① 加一名 -> 行数 +1、卡片「成员数」跟着 +1、重量 = 125 × 人数；
② 删一名 -> 行数 -1；Ctrl+Z（context.undo_last）能把整行撤回来；
③ 右栏列数 == 6（SquadMembers 的列数），换选中行时右栏跟着换；
④ 载具单位（Id 1）打开这一页不炸（它没有班组成员行）。

无字体的平台（offscreen）会 skip —— 与 test_unit_editor.py 同一策略：
宁可不给结论，也不给假结论。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 设计稿的示例步兵：VDV Shturm（Id 645，5 名成员，主武器 AK-74RMO）。
INFANTRY = 645
#: 载具样本：BTR-82A（Id 1）—— 没有班组成员行。
VEHICLE = 1
#: SquadMembers 的全部列（数据源实测 6 列，右栏要一个不少）。
SQUAD_COLUMNS = {
    "Id",
    "DeathPriority",
    "ModelFileName",
    "PrimaryWeaponId",
    "SpecialWeaponId",
    "UnitId",
}
#: 步兵重量系数（viewmodel.unit_card.INFANTRY_WEIGHT_PER_MEMBER）。
WEIGHT_PER_MEMBER = 125


# --------------------------------------------------------------------------- #
# 起控件（照 test_unit_editor.py）
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    if not QFontDatabase.families():
        pytest.skip("该平台没有可用字体，控件度量不可信")
    return app


@pytest.fixture()
def instance():
    """每个用例一份新实例：用例会改内存里的行。"""
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


@pytest.fixture()
def screen(qapp, instance):
    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    editor = UnitEditorScreen()
    editor.set_instance(instance)
    yield editor
    editor.deleteLater()


# --------------------------------------------------------------------------- #
# 左栏：一行一名士兵
# --------------------------------------------------------------------------- #


def test_left_column_is_one_row_per_soldier_showing_names_not_ids(screen) -> None:
    """左栏一行一名士兵：序号 / 主武器 / 副武器 / 模型 / 阵亡波次 —— 一律显示名字。"""
    page = _open_squad_page(screen)
    rows = page.member_rows

    assert len(rows) == page.member_count == 5
    assert [row.position for row in rows] == [1, 2, 3, 4, 5]
    # ❗ 行标签是**纯序号**（用户口径：「第 1 名之类的，能不能就显示 1」）——
    #    与关系树那条判据同源（test_relation_tree.py「名字列是纯序号」那一节）：
    #    行标签里不许再有「第」「名」；右栏那两句人话是**句子**，不动。
    #    （第一行默认选中，序号前面挂一个 ▸ —— 那是选中态，不是序号的一部分。）
    numbers = [row.index_label.text().removeprefix("▸ ") for row in rows]
    assert numbers == ["1", "2", "3", "4", "5"], numbers
    assert all(
        "第" not in row.index_label.text() and "名" not in row.index_label.text() for row in rows
    )

    first = rows[0]
    assert first.primary.text() == "AK-74RMO", "主武器那一格要显示名字，不是 Id 122"
    assert first.model.text() == "RU_VDV_officer"
    assert first.death.text() == "阵亡波次 2"
    assert first.special.text() == "副武器 GP-34"
    # 0 = 「这名士兵没有副武器」（词典 SpecialWeaponId 那条就是这么写的）
    assert rows[1].special.text() == "没有副武器"
    assert rows[2].special.text() == "副武器 RPG-7V"

    # 每一格的悬浮提示写着这一格落在哪，并说明外键是**按名字搜**
    tip = first.primary.toolTip()
    assert f"SquadMembers#{first.row_id} · PrimaryWeaponId" in tip
    assert "按名字搜" in tip


def test_page_spells_out_the_weight_rule_and_the_invariant(screen) -> None:
    """页面上写着两条口径：重量 = 125 × 人数（不是 Units.Weight）；武器不变量。"""
    page = _open_squad_page(screen)
    text = _labels(page)
    assert "125 × 人数" in text
    assert "Units.Weight" in text, "要说清楚重量不是 Units.Weight 那一列"
    assert "SquadWeapons" in text and "不变量" in text


# --------------------------------------------------------------------------- #
# ① 加一名 / ② 删一名
# --------------------------------------------------------------------------- #


def test_add_member_adds_a_row_and_the_card_follows(screen, qapp) -> None:
    """① 加一名 -> 行数 +1、**回到主屏时**卡片「成员数」跟着 +1、重量 = 125 × 人数。

    ❗ 卡片那两条断言前面为什么要先 show_main()（2026-10-01「不要全量保存」这一轮的契约变化）：
    screen.card_panel 是**主编辑页**那张卡，而这一页（专用页）盖在它上面 ——
    卡片此刻**不在屏幕上**。旧实现里卡片自己订阅变更集，于是"看不见也照样同步重排"
    （实测 58.9ms/次，见 _work/probe_edit_profile.py）；现在改成"谁在台上谁刷"，
    看不见的那一页只标脏、切回去才刷（见 UnitEditorScreen._refresh_pages）。
    用户看到的没有区别 —— 回到主屏那一帧卡就是新的，这里补的就是那一帧。
    """
    page = _open_squad_page(screen)
    before = page.member_count
    assert before == 5
    assert _card_stat(screen, "soldier_count") == str(before)

    page.add_button.click()

    assert page.member_count == before + 1
    assert len(page.member_rows) == before + 1
    assert f"成员数 {before + 1}" in page.counters_text
    assert str(WEIGHT_PER_MEMBER * (before + 1)) in page.counters_text
    assert "已加一名士兵" in page.last_message

    # 卡片跟着走：成员数 +1、重量 = 125 × 人数（与 card_weight 同一份实现）。
    # 先回主屏 —— 卡片在专用页底下时**不刷**（只标脏），这是本轮的契约（见 docstring）。
    screen.show_main()
    qapp.processEvents()
    assert _card_stat(screen, "soldier_count") == str(before + 1)
    assert _card_stat(screen, "weight") == str(WEIGHT_PER_MEMBER * (before + 1))
    # 新行是新 Id（复制末行 + 新 Id），没覆盖别人
    assert len(set(page.member_ids)) == before + 1
    # 新加进来的那一行就是当前选中行（右栏换成它的 6 列）
    assert page.selected_id == page.member_ids[-1]
    assert len(page.entries_cells()) == 6


def test_remove_member_shrinks_the_list_and_ctrl_z_restores_it(screen) -> None:
    """② 删一名 -> 行数 -1；Ctrl+Z（context.undo_last）能把整行撤回来。"""
    page = _open_squad_page(screen)
    context = screen.context
    before_ids = page.member_ids
    victim = before_ids[-1]
    before_row = _row_values(screen.context.instance, victim)
    assert before_row is not None

    page.member_rows[-1].remove.click()  # 点行尾那个红 ✕

    assert page.member_count == len(before_ids) - 1
    assert victim not in page.member_ids
    assert "已删掉一名士兵" in page.last_message

    message = context.undo_last()  # Ctrl+Z

    assert "撤销" in message
    assert page.member_count == len(before_ids)
    assert victim in page.member_ids
    assert _row_values(context.instance, victim) == before_row, "撤回来必须整行照旧"
    assert page.entries_cells(), "撤销之后右栏还得有内容"


def test_success_reports_through_the_changed_signal(screen) -> None:
    """加人 / 减人成功**不弹窗**：走 changed 信号，由外层写状态栏（AGENTS §5）。"""
    page = _open_squad_page(screen)
    seen: list[str] = []
    page.changed.connect(lambda: seen.append(page.last_message))

    page.add_button.click()
    page.member_rows[-1].remove.click()

    assert len(seen) == 2
    assert "已加一名士兵" in seen[0]
    assert "已删掉一名士兵" in seen[1]


# --------------------------------------------------------------------------- #
# ③ 右栏 6 列 + 行可选
# --------------------------------------------------------------------------- #


def test_right_panel_lists_the_six_columns_and_follows_the_selection(screen) -> None:
    """③ 右栏 = SquadMembers 的 6 列；点另一行，右栏跟着换成那一名的。"""
    page = _open_squad_page(screen)
    ids = page.member_ids
    assert len(ids) >= 2

    cells = page.entries_cells()
    assert len(cells) == 6, "右栏要列出 SquadMembers 的全部列，一个不少"
    assert {cell.column for cell in cells} == SQUAD_COLUMNS
    assert {cell.row_id for cell in cells} == {ids[0]}
    assert {row.cell.table for row in page.entries.rows} == {"SquadMembers"}
    assert page.entries_summary().startswith("选中第 1 名之后")
    # 外键那一格在右栏同样是名字
    primary = next(cell for cell in cells if cell.column == "PrimaryWeaponId")
    assert primary.text == "AK-74RMO"

    _click_row(page.member_rows[1])  # 点行体 = 选中这一名

    assert page.selected_id == ids[1]
    cells = page.entries_cells()
    assert len(cells) == 6
    assert {cell.row_id for cell in cells} == {ids[1]}
    assert page.entries_summary().startswith("选中第 2 名之后")
    # 选中那一行有「▸」，别的不该有
    assert page.member_rows[1].index_label.text().startswith("▸")
    assert not page.member_rows[0].index_label.text().startswith("▸")


def test_clicking_the_primary_weapon_opens_the_name_search(screen) -> None:
    """点主武器 = 就地改，而且是**外键按名字搜**（设计 §6：写回去的才是 Id）。"""
    from PySide6.QtWidgets import QLineEdit

    from ba_mod_maker.core.edit.refs import CellRef

    page = _open_squad_page(screen)
    second = page.member_rows[1]
    row_id = second.row_id
    second.primary.click()

    assert page.selected_id == row_id, "点第二名的枪要顺手把这一名选中"
    panel = page.entries
    assert panel is not None and panel.has_editor
    assert panel.open_cell == CellRef("SquadMembers", row_id, "PrimaryWeaponId")
    placeholders = [line.placeholderText() for line in panel.findChildren(QLineEdit)]
    assert any("按名字搜" in text for text in placeholders)
    assert any("Name 与 HUDName 都认" in text for text in placeholders)


# --------------------------------------------------------------------------- #
# ④ 载具：没有班成员行也不能炸
# --------------------------------------------------------------------------- #


def test_vehicle_unit_opens_the_page_with_no_members(screen) -> None:
    """④ 载具单位（Id 1）打开这一页不炸：它没有班组成员行，也不能加人。"""
    page = _open_squad_page(screen, VEHICLE)

    assert page.member_count == 0
    assert page.member_rows == ()
    assert page.selected_id is None
    assert page.entries_cells() == ()
    assert page.entries_summary() == "SquadMembers · 没有成员行"
    assert page.can_add is False
    assert page.add_button is not None and not page.add_button.isEnabled()
    assert "没有班组成员行" in _labels(page)

    page._add_member()  # 就算被调到也不许炸：不是步兵，什么都不做

    assert page.member_count == 0
    assert screen.changeset.is_empty, "载具上加不了人，数据一个字节都不该动"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _open_squad_page(screen, unit_id: int = INFANTRY):
    screen.open_unit(unit_id)
    screen.open_squad_page()
    return screen.current_page


def _click_row(row) -> None:
    """真的走一遍 mouseReleaseEvent（点行体 = 选中），不是直接 emit 信号。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    point = QPointF(4.0, 4.0)
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    row.mouseReleaseEvent(event)


def _card_stat(screen, key: str) -> str:
    card = screen.card_panel.card
    assert card is not None
    return next(stat.value for stat in card.stats if stat.key == key)


def _row_values(instance, row_id: int):
    """SquadMembers 里那一行的原样（撤销要把它整行放回来）。"""
    table = instance.table("SquadMembers")
    index = table.column_index("Id")
    for row in table.rows:
        if row[index] == row_id:
            return list(row)
    return None


def _labels(widget) -> str:
    from PySide6.QtWidgets import QLabel

    return "\n".join(label.text() for label in widget.findChildren(QLabel))


# --------------------------------------------------------------------------- #
# ⑦ 那句人话要真的到状态栏（本轮 T3 修的）
# --------------------------------------------------------------------------- #


def test_the_page_message_reaches_the_status_bar(screen) -> None:
    """页里那句人话必须**发出去** —— 不能只 setToolTip / setProperty。

    实踩：SquadPage._status 从前只 setToolTip + setProperty("lastMessage", ...)，
    两个都全库零读取者（grep 过）—— 于是"已加一名士兵"、"已删掉一名士兵：…
    （Ctrl+Z 能撤回来）"这些话谁也看不见：动作做成了，用户界面上却一点反馈都没有。
    现在走 status_message 信号（EditorPage -> UnitEditorScreen -> 顶栏状态栏），
    与其它三个专用页同一条线。
    """
    page = _open_squad_page(screen)
    seen: list[str] = []
    page.status_message.connect(seen.append)

    page.add_button.click()
    assert seen and "已加一名士兵" in seen[-1], f"加完人没往状态栏说：{seen}"

    seen.clear()
    page.member_rows[-1].remove.click()
    assert seen and "已删掉一名士兵" in seen[-1], f"删完人没往状态栏说：{seen}"


def test_a_non_infantry_page_says_why_it_did_nothing(screen) -> None:
    """不是步兵时点「＋」：不写数据、不弹窗 —— 但必须给一句话（走状态栏）。"""
    page = _open_squad_page(screen, VEHICLE)
    seen: list[str] = []
    page.status_message.connect(seen.append)

    page._add_member()

    assert screen.changeset.is_empty, "载具上加不了人，数据一个字节都不该动"
    assert seen and "没有班成员这一套" in seen[-1], f"没给解释：{seen}"
