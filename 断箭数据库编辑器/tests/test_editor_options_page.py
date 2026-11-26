"""改装选项专用页（Options 46 列）的机器判据。

为什么不用假数据：这一页要验的正是"**列一个不少** + **显示的是名字不是 Id** +
**换槽真的换了这一页看的行**"，这三条只有拿真实数据说话才算数
（与 tests/test_unit_editor.py 同一策略）。

样本（实例 52085，单位 BTR-82A = Units#1）：

| 选项 | 是什么 |
|---|---|
| 501 | ModificationId=190 的缺省项：OptionPicture=2A72（UIName=BTR-82A），Turret0Id=2 |
| 503 | 同一个槽（190）里的另一项：BTR-82AT，费用 +10 |
| 504 | ModificationId=191：ArmorId=1、MobilityId=89（覆盖列指着哪一行的样本） |
| 541 | ReplaceUnitId 非 0 —— core 把这一列排除在外键之外，界面要自己解名字 |

无字体的平台（offscreen）会 skip —— 与 test_armory_screen.py / test_unit_editor.py 同一条策略。
"""

from __future__ import annotations

import re

import pytest

from ba_mod_maker.core.data import find_dataset_root
from conftest import settle

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
BTR82A = 1
OPTION_DEFAULT = 501
OPTION_SIBLING = 503
OPTION_ARMOR = 504
OPTION_REPLACE_UNIT = 541
#: 变体借来的槽：单位 645（自己 0 条 Modifications）借着 108 的槽 743 ——
#: 那条 `ReplaceUnitId=645` 的选项 3129 把它换出来的（那一槽三个选项全是"换单位"）。
BORROWED_UNIT = 645
BORROWED_SLOT = 743
BORROWED_OPTION = 3129
BORROWED_SIBLING = 3128
BORROWED_OPTIONS = (3128, 3129, 3301)


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #


# 夹具（qapp / instance）统一在 tests/conftest.py。


@pytest.fixture()
def screen(qapp, instance):
    from ba_mod_maker.app.screens.editor import UnitEditorScreen

    widget = UnitEditorScreen()
    widget.set_instance(instance)
    widget.open_unit(BTR82A)
    yield widget
    widget.deleteLater()


def _open(screen, option_id: int):
    """进改装选项页（从单位编辑器那条路进，与用户双击进去是同一条）。"""
    from ba_mod_maker.app.screens.editor.pages.options_page import OptionsPage

    screen.open_options_page(option_id)
    page = screen.current_page
    assert isinstance(page, OptionsPage)
    return page


def _own_texts(widget) -> list[str]:
    """**页面自己写上去的**字（控件正文）。

    悬浮提示不算：那里面会引字段词典的原文（core/fields 的数据），
    不该拿它来判"这一页的文案怎么写"。
    """
    from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

    out: list[str] = []
    # ❗ findChildren 不收元组（PySide6 只认单个类型），所以逐个来。
    for kind in (QLabel, QPushButton, QToolButton):
        for child in widget.findChildren(kind):
            out.append(child.text())
    return out


def _texts(widget) -> list[str]:
    """页面上的全部文字：控件正文 + 悬浮提示。"""
    from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

    out = _own_texts(widget)
    for kind in (QLabel, QPushButton, QToolButton):
        for child in widget.findChildren(kind):
            if child.toolTip():
                out.append(child.toolTip())
    return out


def _entry_value_text(page, ref) -> str:
    """右栏那一格的值按钮上写的是什么（一条一行的行样式，直接按落点找）。"""
    row = page.entries.row_for(ref)
    assert row is not None, f"右栏没有这一行：{ref}"
    return row.value_text


def _click_sibling(page, screen, option_id: int) -> None:
    """点这一页那一排互斥选项里的某一条（等价于用户点它）。

    按**下标**找，不按按钮文字：按钮上写的是本地化之后的显示名
    （`Custom_Option_VMF_Assault` 会被译成中文），拿 UIName 去匹配会挑错。
    这一排的顺序就是 `data.options_of(slot)` 的顺序（页面的渲染顺序）。
    """
    context = screen.context
    slot = context.cell(_ref("Options", page.option_id, "ModificationId")).raw
    rows = context.data.t("Options")
    options = context.data.options_of(int(slot))
    index = next(i for i, row in enumerate(options) if rows.i(row, "Id") == option_id)
    page.sibling_buttons[index].click()


def _package_rows(screen, table: str = "Options") -> dict[int, dict]:
    """保存落点那个 BA_Mod.zip 里某张表的行（按 Id 索引）。

    落点由 tests/conftest.py 指到本次会话的临时目录（`BA_MOD_MAKER_SAVE_DIR`），
    所以这里读到的一定是产品自己写出来的那个包，不是数据源。

    ❗ 用户口径（2026-09-29）改成"改动只进缓存，点💾保存才写盘"之后，
    这里要先 `screen.save()` —— 以前是改一格自动落盘，现在落盘是显式动作。
    """
    import json
    import os
    import zipfile
    from pathlib import Path

    screen.save()  # 先把缓存写进包（新口径：写盘是显式动作）
    package = Path(os.environ["BA_MOD_MAKER_SAVE_DIR"]) / "BA_Mod.zip"
    assert package.exists(), "点了保存却没落盘"
    with zipfile.ZipFile(package) as archive:
        rows = json.loads(archive.read(f"{table}.json").decode("utf-8"))
    return {row["Id"]: row for row in rows}


def _option_name(screen, option_id: int) -> str:
    rows = screen.context.data.t("Options")
    row = rows.by_id(option_id)
    assert row is not None
    return rows.s(row, "UIName")


def _ref(table: str, row_id: int, column: str):
    from ba_mod_maker.core.edit.refs import CellRef

    return CellRef(table, row_id, column)


class _Signal:
    """极简信号：只做 connect / emit（本页只用得上这两下）。"""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self) -> None:
        for slot in list(self._slots):
            slot()


class _FakeIcons:
    """假图标缓存：不用 QObject，只实现页面真正用到的那两样。"""

    def __init__(self, pixmap=None) -> None:
        self._pixmap = pixmap
        self.loaded = _Signal()
        #: 页面按 (族, 键) 要过哪些图。
        self.calls: list[tuple[str, str]] = []

    def give(self, pixmap) -> None:
        self._pixmap = pixmap

    def pixmap(self, family: str, key: str):
        self.calls.append((family, key))
        return self._pixmap


# --------------------------------------------------------------------------- #
# ① 46 列一个不少
# --------------------------------------------------------------------------- #


def test_page_keeps_all_46_columns(screen, instance) -> None:
    """Options 46 列 —— 一个不少，且都是这一行的列。"""
    page = _open(screen, OPTION_DEFAULT)
    cells = page.entries_cells()
    assert len(cells) == 46
    assert {cell.column for cell in cells} == set(instance.table("Options").columns)
    assert {cell.row_id for cell in cells} == {OPTION_DEFAULT}
    # 右栏抬头按设计稿写（基类给的 entries_title 钩子）
    assert page.entries_title() == "改装选项的全部词条"
    assert "改装选项的全部词条" in _own_texts(page)


def test_groups_only_change_the_display_order(screen, instance) -> None:
    """8 个功能分组正好把 46 列分完（不重不漏），且只决定**显示顺序**。"""
    from ba_mod_maker.app.screens.editor.pages.options_page import (
        OPTION_GROUPS,
        ordered_columns,
    )

    columns = tuple(instance.table("Options").columns)
    grouped = tuple(column for group in OPTION_GROUPS for column in group.columns)
    assert len(grouped) == len(set(grouped)) == 46, "分组里有重复或漏列"
    assert set(grouped) == set(columns)

    # 分组顺序 = 右栏的显示顺序；表里的原列序只用来给"没登记的列"排序。
    assert ordered_columns(columns) == grouped
    page = _open(screen, OPTION_DEFAULT)
    assert tuple(cell.column for cell in page.entries_cells()) == grouped

    # 兜底：将来数据里多一列，只会追加在最后，不会悄悄少一列。
    assert ordered_columns((*columns, "SomeFutureColumn"))[-1] == "SomeFutureColumn"


# --------------------------------------------------------------------------- #
# ② 21 个炮塔槽：都在右栏，且不写成"替换第 N 槽"
# --------------------------------------------------------------------------- #


def test_all_21_turret_slots_are_in_the_right_panel(screen) -> None:
    from ba_mod_maker.app.screens.editor.pages.options_page import TURRET_COLUMNS

    assert len(TURRET_COLUMNS) == 21
    listed = {cell.column for cell in _open(screen, OPTION_DEFAULT).entries_cells()}
    missing = [column for column in TURRET_COLUMNS if column not in listed]
    assert not missing, f"右栏少了炮塔槽：{missing}"


def test_turret_semantics_are_pool_plus_subset(screen) -> None:
    """文案上不许出现"替换第 N 槽"这种硬编码语义；要说清是池 + 选项子集。

    只看**页面自己写的字**：悬浮提示里会引字段词典的原文（那是 core/fields 的数据），
    拿它判本页文案就判歪了。
    """
    texts = _own_texts(_open(screen, OPTION_DEFAULT))
    for pattern in (r"替换第\s*\d+\s*槽", r"第\s*\d+\s*个炮塔"):
        hit = [text for text in texts if re.search(pattern, text)]
        assert not hit, f"界面上出现了硬编码的槽位语义：{hit}"
    assert any("池 + 选项子集" in text for text in texts)
    assert any("要装的炮塔列表" in text for text in texts)


def test_non_empty_turret_slots_show_the_turret_name_and_still_edit_in_place(
    screen, instance
) -> None:
    """非空的槽：值给**炮塔名**（不是裸 Id），点开就地改，清空后左栏不再画它。"""
    from ba_mod_maker.core.edit.foreign import display_name

    page = _open(screen, OPTION_DEFAULT)
    ref = _ref("Options", OPTION_DEFAULT, "Turret0Id")
    cell = screen.context.cell(ref)
    assert cell.raw and cell.raw > 0, "样本选错了：501 没挂炮塔"
    assert "Turret0Id" in page.turret_buttons
    button = page.turret_buttons["Turret0Id"]
    assert button.text() == display_name(instance, "Turrets", cell.raw)
    assert button.text() != str(cell.raw)

    button.click()
    assert page.entries.has_editor
    assert page.entries.open_cell == ref
    page._commit(ref, 0)
    settle()  # 空槽不该再画在左栏（刷新异步，见 conftest.settle）
    assert screen.context.cell(ref).raw == 0
    assert "Turret0Id" not in page.turret_buttons, "空槽不该再画在左栏"


def test_all_empty_turret_slots_still_point_at_the_right_panel(screen) -> None:
    """一个炮塔都没挂时：说清"21 个槽仍然都在右栏的 46 列里"。"""
    page = _open(screen, OPTION_ARMOR)
    assert page.turret_buttons == {}
    texts = _texts(page)
    assert any("全空" in text for text in texts)
    assert any("21 个槽都在右栏的 46 列里" in text for text in texts)


# --------------------------------------------------------------------------- #
# ③ 互斥槽：点一项 = 选中它（写 Options.IsDefault），其余自动取消
# --------------------------------------------------------------------------- #


def test_picking_a_sibling_selects_it_and_deselects_the_others(screen, instance) -> None:
    """**槽内互斥**：点 B -> B 选中、A 不再选中 —— 而且是**数据里的事实**。

    选中 = `Options.IsDefault`（口径与出处见 options_page 模块注释）：
    同一个 ModificationId 下最多一条 true。
    """
    page = _open(screen, OPTION_DEFAULT)
    slot = screen.context.cell(_ref("Options", OPTION_DEFAULT, "ModificationId")).raw
    assert screen.context.cell(_ref("Options", OPTION_DEFAULT, "IsDefault")).raw is True
    assert page.selected_button is not None, "缺省那一项就该画成选中态"
    assert _option_name(screen, OPTION_DEFAULT) in page.selected_button.text()

    _click_sibling(page, screen, OPTION_SIBLING)

    assert screen.context.cell(_ref("Options", OPTION_SIBLING, "IsDefault")).raw is True
    assert screen.context.cell(_ref("Options", OPTION_DEFAULT, "IsDefault")).raw is False
    # 还是同一个槽、右栏还是那 46 列（21 个炮塔槽一个不少）
    assert screen.context.cell(_ref("Options", OPTION_SIBLING, "ModificationId")).raw == slot
    cells = page.entries_cells()
    assert len(cells) == 46
    assert {cell.row_id for cell in cells} == {OPTION_SIBLING}
    assert {cell.column for cell in cells} == set(instance.table("Options").columns)
    # 选中态跟着搬到它身上（颜色走 qss 的 [selected="true"]）
    assert page.selected_button is not None
    assert _option_name(screen, OPTION_SIBLING) in page.selected_button.text()
    assert page.selected_button.property("selected") == "true"
    # 右栏也跟着刷新：显示的是**刚写下去**的值，不是旧的那一份
    assert _entry_value_text(page, _ref("Options", OPTION_SIBLING, "IsDefault")) == "是"


def test_only_the_is_default_cells_are_written_and_it_is_idempotent(screen) -> None:
    """写哪几格由 viewmodel 说了算：只动 `IsDefault`，同槽其余项写回 false。

    顺带钉死两件事：**幂等**（再点一次不产生新改动）与
    **串槽保护**（不是这个槽的选项一个字节都不写）。
    """
    from ba_mod_maker.app.viewmodel.unit_card import exclusive_selection_writes

    data = screen.context.data
    slot = screen.context.cell(_ref("Options", OPTION_DEFAULT, "ModificationId")).raw
    assert exclusive_selection_writes(data, slot, OPTION_SIBLING) == (
        (OPTION_SIBLING, "IsDefault", True),
        (OPTION_DEFAULT, "IsDefault", False),
    )
    for row_id, column, value in exclusive_selection_writes(data, slot, OPTION_SIBLING):
        screen.context.set_value(_ref("Options", row_id, column), value)
    assert exclusive_selection_writes(data, slot, OPTION_SIBLING) == (), "已经选中了就不该再写"
    # 选回去也是"只写这两格"，方向相反
    assert exclusive_selection_writes(data, slot, OPTION_DEFAULT) == (
        (OPTION_DEFAULT, "IsDefault", True),
        (OPTION_SIBLING, "IsDefault", False),
    )
    # 别的槽的选项：一个字都不许写（宁可什么都不做，也不要串槽）
    assert exclusive_selection_writes(data, 191, OPTION_SIBLING) == ()


def test_picking_writes_the_package_immediately(screen) -> None:
    """**写盘**：点一下改的是缓存，`screen.save()` 之后包里就是新值。"""
    page = _open(screen, OPTION_DEFAULT)
    _click_sibling(page, screen, OPTION_SIBLING)

    rows = _package_rows(screen)
    assert rows[OPTION_SIBLING]["IsDefault"] is True
    assert rows[OPTION_DEFAULT]["IsDefault"] is False


def test_picking_never_pops_a_dialog_and_reports_through_the_status_bar(
    screen, monkeypatch
) -> None:
    """弹窗规则：本页一个弹窗都没有；成功写状态栏（AGENTS §5）。"""
    from PySide6.QtWidgets import QMessageBox

    def _boom(*_args, **_kwargs):
        raise AssertionError("专用页不许弹窗（AGENTS §5：成功写状态栏）")

    monkeypatch.setattr(QMessageBox, "warning", _boom)
    monkeypatch.setattr(QMessageBox, "critical", _boom)
    monkeypatch.setattr(QMessageBox, "information", _boom)

    from ba_mod_maker.app.screens.editor.pages import options_page

    assert not hasattr(options_page, "QMessageBox"), "本页根本不该 import 弹窗"

    page = _open(screen, OPTION_DEFAULT)
    seen: list[int] = []
    said: list[str] = []
    page.changed.connect(lambda: seen.append(page.option_id))
    page.status_message.connect(said.append)
    _click_sibling(page, screen, OPTION_SIBLING)

    assert seen[-1] == OPTION_SIBLING, "换页面要让外层刷新一次"
    assert said and "已选中" in said[-1], said


# --------------------------------------------------------------------------- #
# ③b 卡上那一下（编辑器左卡的下拉）：真选中 + 立刻写盘 + 卡片跟着变
# --------------------------------------------------------------------------- #


def test_tree_option_pick_selects_writes_and_the_card_follows(screen) -> None:
    """**行为保留、入口换掉**：改装槽里换一项 = 选中它 —— 写盘 + 卡片按新配置重算 +
    状态栏一句话。

    这是用户原话"自定义选项我还是改不了"那条路的机器判据：换一项，磁盘上的包要变。

    - 旧入口 = 卡上那个 `QComboBox`（本轮卡片整块只读，下拉已拿掉，见 editor/readonly_card.py）；
    - 新入口 = 关系树「🧩 改装槽 › 选项」节点上的「选中这一项」（`tree.select_option`，
      与右键菜单那条线是同一个方法）；
    - 选中态也从卡上（option-row[selected] + 「✓ 已选中」）搬到树上：那一条前面带 ●。
    """
    page = screen.main_page
    tree = page.tree
    panel = screen.card_panel
    cost_before = panel.current_cost
    said: list[str] = []
    screen.status_message.connect(said.append)

    node = next(
        item
        for item in tree.tree.flatten()
        if item.table == "Options" and item.row_id == OPTION_SIBLING
    )
    assert node.selected is False, "换之前它不该是选中态"
    assert tree.select_option(node.key), "树上没有「选中这一项」这条线"

    assert screen.context.cell(_ref("Options", OPTION_SIBLING, "IsDefault")).raw is True
    assert screen.context.cell(_ref("Options", OPTION_DEFAULT, "IsDefault")).raw is False
    # 卡片随页面刷新，排在下一轮事件循环（2026-10-01「不要全量保存」那一轮的契约变化：
    # 卡不再自己订阅变更集，改由 UnitEditorScreen._refresh_pages 驱动 —— 见它的说明）。
    settle()
    assert panel.current_cost == cost_before + 10, "卡片要按新配置重算（+10）"
    assert any("已选中" in text for text in said), said
    assert _package_rows(screen)[OPTION_SIBLING]["IsDefault"] is True

    # 选中态要看得出来：树上**每个槽恰好一条**带 ●（BTR-82 有两个槽，所以两条）
    settle()  # 树的选中态是下一轮事件循环才更新的（见 conftest.settle）
    marked = [item for item in tree.tree.flatten() if item.table == "Options" and item.selected]
    own_slots = [group for group in panel.card.options if group.is_own_slot]
    assert len(marked) == len(own_slots) == 2
    assert any(item.row_id == OPTION_SIBLING for item in marked), "换过去的那一项没有 ●"


def test_the_card_stays_read_only_when_nobody_attached_a_writer(qapp, instance) -> None:
    """军械库那张卡（没人接写盘通道）**一行语义都没变**：还是只预览、不写数据。

    `set_option_writer` 是显式开关 —— 不接就不写，哪怕它手上有一份 changeset
    （军械库确实会 `set_changeset`，那只是为了"别处改了刷一下卡片"）。
    """
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    options = instance.table("Options")
    before = [list(row) for row in options.rows]

    panel = UnitCardPanel()
    panel.set_instance(instance)
    panel.set_icons(None)
    panel.show_unit(1)
    panel.findChildren(QComboBox)[0].setCurrentIndex(1)  # BTR-82A -> BTR-82AT
    assert panel.selected_options == {190: OPTION_SIBLING}, "不接写盘通道 = 还是预览"
    assert [list(row) for row in options.rows] == before, "数据一个字节没动"
    panel.deleteLater()


def test_a_borrowed_variant_slot_is_read_only(qapp, instance) -> None:
    """变体借来的槽（645 借 108 的 743）**只给看不给写**。

    实测：单位 645 自己 0 条 Modifications，卡片上那一组来自 108 的槽 743
    （那条 ReplaceUnitId=645 的选项 3129 把它换出来的）。
    在这个槽上点别的选项只换这一页看的行 —— 因为它不属于 645。
    """
    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.pages.options_page import OptionsPage

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BORROWED_UNIT)
    screen.open_options_page(BORROWED_OPTION)
    page = screen.current_page
    assert isinstance(page, OptionsPage)

    slot = screen.context.cell(_ref("Options", BORROWED_OPTION, "ModificationId")).raw
    assert slot == BORROWED_SLOT
    before = {
        option_id: screen.context.cell(_ref("Options", option_id, "IsDefault")).raw
        for option_id in BORROWED_OPTIONS
    }
    said: list[str] = []
    page.status_message.connect(said.append)

    _click_sibling(page, screen, BORROWED_SIBLING)

    assert page.option_id == BORROWED_SIBLING, "看哪一行照样能换"
    after = {
        option_id: screen.context.cell(_ref("Options", option_id, "IsDefault")).raw
        for option_id in BORROWED_OPTIONS
    }
    assert after == before, "借来的槽一个字节都不许写"
    assert screen.changeset is None or screen.changeset.is_empty
    assert said and "不给改" in said[-1], said
    page.deleteLater()
    screen.deleteLater()


# --------------------------------------------------------------------------- #
# ④ 覆盖列：显示的是名字，不是裸 Id
# --------------------------------------------------------------------------- #


def test_override_columns_show_the_template_name_not_the_bare_id(screen, instance) -> None:
    from ba_mod_maker.core.edit.foreign import display_name

    page = _open(screen, OPTION_ARMOR)
    ref = _ref("Options", OPTION_ARMOR, "ArmorId")
    armor = screen.context.cell(ref)
    assert armor.raw and armor.raw > 0, "样本选错了：504 没有装甲覆盖"
    name = display_name(instance, "Armors", armor.raw)
    assert name != str(armor.raw)

    # 左栏覆盖那一组：值是模板名 + 「它指向哪一行」的小字
    assert page.override_buttons["ArmorId"].text() == name
    texts = _texts(page)
    assert f"装甲模板#{armor.raw} · ArmorId" in texts
    # 右栏那一格也是名字（RowEntriesPanel 走 core 的显示值）
    assert _entry_value_text(page, ref) == name

    mobility = screen.context.cell(_ref("Options", OPTION_ARMOR, "MobilityId"))
    assert page.override_buttons["MobilityId"].text() == display_name(
        instance, "Mobility", mobility.raw
    )


def test_replace_unit_override_shows_the_unit_name(screen, instance) -> None:
    """ReplaceUnitId 也要显示单位名、并且能跳过去（用户口径 2026-09-30 实机改的）。

    **旧口径**：core 把它排除在外键之外（「不许往里跳」），由页面层自己解名字 ——
    于是词条栏那一格显示的是裸 Id（用户截图：ReplaceUnitId = 333，
    左栏写着「替换成另一个单位」）。
    **用户原话**：「所有 ID，有对应 Name 或 HUDName 的，全部在右边显示，并可以转到那一条」。

    现在 core 用 display_table() 给它名字、也给 ↗；而 scope 走图仍然用
    foreign_table()（对指向 Units 的返回 None）—— 两件事拆成两个函数，互不影响。
    这条断言把「拆干净」钉死：显示变了，scope 那一路一个字节没变。
    """
    from ba_mod_maker.core.edit.foreign import display_name
    from ba_mod_maker.core.edit.refs import display_table, foreign_table

    page = _open(screen, OPTION_REPLACE_UNIT)
    cell = screen.context.cell(_ref("Options", OPTION_REPLACE_UNIT, "ReplaceUnitId"))
    assert cell.raw and cell.raw > 0, "样本选错了：541 不是整单位替换"

    # ① 显示：名字 + 能跳
    assert cell.text == display_name(instance, "Units", cell.raw), "显示值应当是单位名"
    assert cell.text != str(cell.raw), "不许再显示裸 Id"
    assert cell.foreign == "Units", "要有 ↗ 能转到那一条"

    # ② scope 走图**不许**跟着变（设计 §12：走进去会把被替换单位的整棵子树卷进来）
    assert foreign_table("ReplaceUnitId") is None
    assert display_table("ReplaceUnitId") == "Units"

    button = page.override_buttons["ReplaceUnitId"]
    assert button.text() == display_name(instance, "Units", cell.raw)
    assert f"单位本体#{cell.raw} · ReplaceUnitId" in _texts(page)


def test_every_override_column_is_clickable_and_edits_in_place(screen) -> None:
    """四个覆盖组一列不缺，每一列都能点开就地改（改的是那一格的数据）。"""
    from ba_mod_maker.app.screens.editor.pages.options_page import OVERRIDE_GROUPS

    page = _open(screen, OPTION_ARMOR)
    wanted = [column for group in OVERRIDE_GROUPS for column in group.columns]
    assert sorted(page.override_buttons) == sorted(wanted)

    ref = _ref("Options", OPTION_ARMOR, "MobilityId")
    before = screen.context.cell(ref).raw
    page.override_buttons["MobilityId"].click()
    assert page.entries.has_editor
    assert page.entries.open_cell == ref

    page._commit(ref, before + 1)
    assert screen.context.cell(ref).raw == before + 1


# --------------------------------------------------------------------------- #
# 图标：族 option_pictures / 键 Options.OptionPicture；取不到退回文字
# --------------------------------------------------------------------------- #


def test_icon_uses_option_pictures_family_and_the_row_key(qapp, instance) -> None:
    from PySide6.QtGui import QPixmap

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.pages.options_page import ICON_FAMILY, OptionsPage
    from ba_mod_maker.core.assets.families import family as family_of

    # 族的键来源就是那一列（不然"族键 option_pictures、键 Options.OptionPicture"是空话）
    entry = family_of(ICON_FAMILY)
    assert entry is not None and entry.column == "Options.OptionPicture"

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    key = screen.context.cell(_ref("Options", OPTION_DEFAULT, "OptionPicture")).raw
    assert key, "样本选错了：501 没有 OptionPicture"

    icons = _FakeIcons(pixmap=QPixmap(8, 8))
    page = OptionsPage(OPTION_DEFAULT, icons=icons)
    page.set_context(screen.context)

    assert (ICON_FAMILY, key) in icons.calls
    assert all(family == ICON_FAMILY for family, _key in icons.calls)
    assert page.icon_box is not None and page.icon_box.has_pixmap
    page.deleteLater()
    screen.deleteLater()


def test_icon_falls_back_to_text_and_fills_in_when_it_arrives(qapp, instance) -> None:
    from PySide6.QtGui import QPixmap

    from ba_mod_maker.app.screens.editor import UnitEditorScreen
    from ba_mod_maker.app.screens.editor.pages.options_page import OptionsPage

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    key = screen.context.cell(_ref("Options", OPTION_DEFAULT, "OptionPicture")).raw

    icons = _FakeIcons(pixmap=None)
    page = OptionsPage(OPTION_DEFAULT, icons=icons)
    page.set_context(screen.context)
    assert page.icon_box is not None
    assert not page.icon_box.has_pixmap, "取不到图时不该假装有图"
    assert page.icon_box.text == key, "退回文字：先写图标键（不编）"
    assert page.sibling_buttons[0].icon().isNull()

    # 图标是一批批到的：到货后只换图，不重建左栏。
    left = page.findChildren(type(page.icon_box))
    icons.give(QPixmap(8, 8))
    icons.loaded.emit()
    assert page.icon_box.has_pixmap
    assert page.sibling_buttons[0].icon().isNull() is False
    assert page.findChildren(type(page.icon_box)) == left, "到货不该重建控件"
    page.deleteLater()
    screen.deleteLater()


# --------------------------------------------------------------------------- #
# 身份卡：槽名 / 相对价 / 缺省项
# --------------------------------------------------------------------------- #


def test_identity_shows_the_slot_name_and_the_relative_price(screen, instance) -> None:
    from ba_mod_maker.core.edit.foreign import display_name

    page = _open(screen, OPTION_DEFAULT)
    slot = screen.context.cell(_ref("Options", OPTION_DEFAULT, "ModificationId"))
    texts = _texts(page)
    assert f"改装槽：{display_name(instance, 'Modifications', slot.raw)}" in texts
    assert f"选项 #{OPTION_DEFAULT}" in texts
    # 费用是**相对价**（加多少钱），不是"改成多少钱"（交接 §6.4）
    assert "改装件费用 0" in texts
    assert "（相对价：加多少钱，不是改成多少钱）" in texts
    assert any("缺省项：是" in text for text in texts)


def test_sibling_option_shows_its_relative_price(screen) -> None:
    page = _open(screen, OPTION_SIBLING)
    texts = _texts(page)
    assert "改装件费用 +10" in texts
    assert any("缺省项：否" in text for text in texts)
    assert any("费用 +10" in text for text in texts), "同槽那一行也要给费用"
