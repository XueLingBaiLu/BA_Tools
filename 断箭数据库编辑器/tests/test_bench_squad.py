"""编成台 ① 「这个班」：一格 = 一个兵（格子矩阵 + 增删）。

控件：app/widgets/bench_squad.py::SquadBench（只摆控件 + 发信号）
取数：app/viewmodel/bench_squad.py（零 Qt）

判据 = 共享任务 task-1 的九条（对着 docs/设计稿-编成台-步兵.png 与
docs/交接-2026-10-01-单位编成台设计.md §3 口径 3）：

① 一格 = SquadMembers 一行、**按 Id 升序**、序号 01..14；
② 主武器那行 = 游戏原生图标（Weapons.HUDIcon）+ 名字；
③ 副武器是 0 -> 写「没有副武器」，**不是空白**；
④ 脚注 = ModelFileName 去掉「阵营_单位_」前缀；
⑤ 点一格 = 选中（同一时刻只有一个）+ 发 row_selected("SquadMembers", 行Id)；
⑥ 格上 ✕ = EditorContext.delete_row -> 一句人话到状态栏；
⑦ 「＋ 加一名兵」= EditorContext.add_squad_member；
⑧ 「⧉ 复制这一名」= 照选中那一行整行照抄（新 Id 走 next_row_id）；没选中 -> 禁用 + tooltip 说明；
⑨ refresh() 幂等（连调两次不叠控件）；一个人都没有 -> 一句灰字，不是空框。

样本全取真数据（DataBaseCompiled/52085）：单位 70 = Marine Raiders（14 名兵 / 4 件班用武器）、
单位 645 = VDV Shturm（5 名兵 · 变体单位，验前缀规则不是钉死在 70 上的）、
单位 1 = BTR-82A（载具，不是步兵 —— 反例）。
无字体的平台会 skip —— 与 tests/test_editor_squad_page.py 同一策略：宁可不给结论，也不给假结论。

⚠ 断言钉**控件里存的那一份状态**（cells / slots / selected_row_id / 控件的文字），
不拿界面上的东西再去推算一遍 —— 那样两边一起错还测不出来。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.assets.families import WEAPON_ICON_FAMILY
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 设计稿的示例班：Marine Raiders（14 名兵 Id 113..126、4 件班用武器）。
INFANTRY = 70
#: 变体单位：VDV Shturm（5 名兵）—— 再验一遍前缀规则不是对着 70 写死的。
VARIANT = 645
#: 反例：BTR-82A（载具，没有班成员这一套）。
VEHICLE = 1
#: 单位 70 那 14 名兵的 Id（数据源实测）。
MARINE_IDS = tuple(range(113, 127))
#: 头两名的枪（数据源实测：主武器 Weapons#67 = Mk18 CQBR，副武器 Weapons#45 = M1014）。
MK18 = "Mk18 CQBR"
M1014 = "M1014"
M32 = "M32 MGL"
#: 单位 70 的模型前缀（脚注那一格要把它去掉）。
PREFIX = "US_MarineRaider_"


# --------------------------------------------------------------------------- #
# 起控件
# --------------------------------------------------------------------------- #


@pytest.fixture()
def made(qapp):
    """建一个 SquadBench（用例结束统一销毁）。

    用 conftest 的 qapp（无字体平台会 skip）与 function 级 instance（用例会改内存里的行）。
    """
    from ba_mod_maker.app.widgets.bench_squad import SquadBench

    widgets: list[SquadBench] = []

    def build(context) -> SquadBench:
        bench = SquadBench()
        widgets.append(bench)
        bench.set_context(context)
        return bench

    yield build
    for widget in widgets:
        widget.deleteLater()


def _context(instance, unit_id: int):
    """与 UnitEditorScreen 建的那一份同构（寻址 / 共享度 / 撤销栈都走 core）。"""
    from ba_mod_maker.app.screens.editor.pages.base import EditorContext
    from ba_mod_maker.app.viewmodel.unit_card import CardData
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.edit.sharing import build_sharing_index

    return EditorContext(
        instance,
        unit_id,
        Changeset(instance),
        CardData(instance),
        build_sharing_index(instance),
        lambda: None,
    )


# --------------------------------------------------------------------------- #
# ① 一格 = 一个兵（Id 升序 / 序号 01..14 / 主武器图标 + 名字）
# --------------------------------------------------------------------------- #


def test_one_cell_per_member_in_id_order_with_zero_padded_index(made, instance) -> None:
    """① 14 格、Id 升序、序号 01..14。顺序与序号都来自 viewmodel，不现算。"""
    bench = made(_context(instance, INFANTRY))
    ids = tuple(row["Id"] for row in _rows(instance, "SquadMembers", UnitId=INFANTRY))

    assert ids == MARINE_IDS, "样本变了：这一节全部读数都对着这 14 个 Id"
    assert len(bench.cells) == 14
    assert bench.member_ids() == tuple(sorted(ids)) == MARINE_IDS
    assert [cell.index for cell in bench.cells] == list(range(1, 15))
    assert [cell.row_ref.row_id for cell in bench.cells] == list(MARINE_IDS)
    assert str(bench.cells[0].row_ref) == "SquadMembers#113"
    # 序号是**两位补零**（设计稿上的 01 / 02 …）
    assert [slot.index_label().text() for slot in bench.slots()] == [
        f"{position:02d}" for position in range(1, 15)
    ]


def test_primary_weapon_line_shows_the_name_and_the_game_hudicon(made, instance) -> None:
    """② 主武器那一行 = 游戏原生图标（Weapons.HUDIcon）+ 名字。"""
    bench = made(_context(instance, INFANTRY))
    weapon = _row_by_id(instance, "Weapons", 67)  # 独立去武器表读一遍
    assert weapon is not None and weapon["HUDIcon"] == "INF_CQBR"

    icon, label = bench.slots()[0].lines()[0]
    assert label.text() == MK18
    assert bench.cells[0].primary_label == MK18
    # 族名与键都要对：族名只有一份出处（core/assets/families.py），不在界面上抄字面量
    assert icon.family == WEAPON_ICON_FAMILY
    assert icon.key == bench.cells[0].primary_key == weapon["HUDIcon"] == "INF_CQBR"

    # 03 那一名是 M32 MGL
    assert bench.slots()[2].lines()[0][1].text() == M32
    assert bench.cells[2].primary_key == "INF_M32"


def test_missing_special_weapon_says_so_instead_of_a_blank(made, instance) -> None:
    """③ 副武器是 0 -> 「没有副武器」（不是空白）；有副武器的写名字。"""
    bench = made(_context(instance, INFANTRY))

    _, first_special = bench.slots()[0].lines()[1]
    assert first_special.text() == M1014
    assert bench.cells[0].special_weapon_id == 45

    empty_icon, empty_label = bench.slots()[2].lines()[1]
    assert empty_label.text() == "没有副武器"
    assert bench.cells[2].special_weapon_id == 0
    assert empty_icon.key == "" and not empty_icon.has_icon()

    # 第 5 名（Id 117）的副武器是 M72A10 LAW —— 三种形态各一条
    assert bench.slots()[4].lines()[1][1].text() == "M72A10 LAW"


def test_footnote_is_the_model_key_without_the_unit_prefix(made, instance) -> None:
    """④ 脚注 = ModelFileName 去掉单位前缀（**独立按前缀切一遍**，不调被测函数）。"""
    from ba_mod_maker.app.viewmodel.bench_squad import member_footnote

    bench = made(_context(instance, INFANTRY))
    rows = _rows(instance, "SquadMembers", UnitId=INFANTRY)
    expected = [row["ModelFileName"].removeprefix(PREFIX) for row in rows]

    assert [cell.footnote for cell in bench.cells] == expected
    assert [slot.footnote_label().text() for slot in bench.slots()] == expected
    assert [cell.footnote for cell in bench.cells][:3] == ["officer", "radio", "rifle"]
    assert all(row["ModelFileName"].startswith(PREFIX) for row in rows)

    # 纯函数那几条边界（含「角色名自己带下划线」那种）
    assert member_footnote("US_MarineRaider_rifle") == "rifle"
    assert member_footnote("RU_VDV_rifle_1") == "rifle_1"
    assert member_footnote("US_Rangers") == "US_Rangers", "只有两段：认不出前缀就原样"
    assert member_footnote("") == ""
    assert member_footnote("US_MarineRaider_") == "US_MarineRaider_"


def test_the_footnote_rule_also_holds_on_another_unit(made, instance) -> None:
    """④ 换一个单位（645 · 变体）照样成立 —— 规则不是对着 70 写死的。"""
    bench = made(_context(instance, VARIANT))
    rows = _rows(instance, "SquadMembers", UnitId=VARIANT)

    assert len(bench.cells) == 5
    assert [cell.footnote for cell in bench.cells] == [
        row["ModelFileName"].removeprefix("RU_VDV_") for row in rows
    ]
    assert [cell.footnote for cell in bench.cells[:3]] == ["officer", "rifle_1", "Weapon"]


# --------------------------------------------------------------------------- #
# ⑤ 点一格 = 选中
# --------------------------------------------------------------------------- #


def test_clicking_a_cell_selects_only_it_and_reports(made, instance) -> None:
    """⑤ 点一格 = 选中它（同一时刻只有一个）+ 发 row_selected(表名, 行Id)。"""
    bench = made(_context(instance, INFANTRY))
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    assert bench.selected_row_id() is None, "不自动选中第一名（Lead 2026-10-01 口径）"
    assert [slot.selected for slot in bench.slots()] == [False] * 14

    _click(bench.slots()[4])  # 第 5 格

    assert bench.selected_row_id() == 117
    assert seen == [("SquadMembers", 117)]
    assert [slot.selected for slot in bench.slots()] == [index == 4 for index in range(14)], (
        "同一时刻只有一格是选中态"
    )

    _click(bench.slots()[0])

    assert bench.selected_row_id() == 113
    assert sum(1 for slot in bench.slots() if slot.selected) == 1
    assert seen[-1] == ("SquadMembers", 113)


def test_a_cell_click_never_jumps_away(made, instance) -> None:
    """格子上只有两个手势：点 = 选中、✕ = 删。

    口径（2026-10-01 定，**2026-10-02 更新**）：格子上不叠"双击跳走"的隐藏含义；
    而"跳到别的行"那一整件事（中栏行尾 ↗ / 右栏右上角 ↗）用户已经要求删了
    —— 本控件现在连那个信号都不再有。
    """
    bench = made(_context(instance, INFANTRY))

    _click(bench.slots()[3])
    _click(bench.slots()[3])

    assert not hasattr(bench, "row_activated")
    assert bench.selected_row_id() == 116


# --------------------------------------------------------------------------- #
# ⑥ 格上 ✕ = 删这个兵
# --------------------------------------------------------------------------- #


def test_remove_button_deletes_that_member_and_tells_the_status_bar(made, instance) -> None:
    """⑥ ✕ -> delete_row：表里真少一行、状态栏一句人话、选中态跟着退。"""
    context = _context(instance, INFANTRY)
    bench = made(context)
    messages: list[str] = []
    bench.status_message.connect(messages.append)
    _click(bench.slots()[2])  # 先选中第 3 名（Id 115）
    assert bench.selected_row_id() == 115

    bench.slots()[2].remove_button().click()

    assert len(bench.cells) == 13
    assert 115 not in bench.member_ids()
    assert _row_by_id(instance, "SquadMembers", 115) is None, "表里也得真没了"
    assert [slot.index_label().text() for slot in bench.slots()][:3] == ["01", "02", "03"]
    assert bench.selected_row_id() is None, "选中的那一名没了 -> 退回没选中"
    assert messages and "已删掉这个兵" in messages[-1] and "115" in messages[-1]

    # Ctrl+Z 能把整行放回来（撤销栈在 context 上）
    assert "撤销" in context.undo_last()
    bench.refresh()
    assert bench.member_ids() == MARINE_IDS
    assert _row_by_id(instance, "SquadMembers", 115)["ModelFileName"].endswith("rifle")


# --------------------------------------------------------------------------- #
# ⑦ 「＋ 加一名兵」= EditorContext.add_squad_member
# --------------------------------------------------------------------------- #


def test_add_member_copies_the_selected_one_and_selects_the_new_row(made, instance) -> None:
    """⑦ 加一名 = add_squad_member(copy_from=选中的那一行) + 自动选中新那一名。"""
    context = _context(instance, INFANTRY)
    bench = made(context)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))
    messages: list[str] = []
    bench.status_message.connect(messages.append)

    _click(bench.slots()[4])  # 第 5 名（Id 117）—— 他身上是 Mk18 CQBR + M72A10 LAW
    source = _row_by_id(instance, "SquadMembers", 117)
    bench.add_button.click()

    assert len(bench.cells) == 15
    fresh_id = bench.selected_row_id()
    assert fresh_id is not None and fresh_id not in MARINE_IDS
    fresh = _row_by_id(instance, "SquadMembers", fresh_id)
    assert fresh is not None
    assert fresh["UnitId"] == INFANTRY, "UnitId 不变 —— 新兵还是这个班的"
    # 值照抄：新 Id **不是**「行数 + 1」（Id 有大空洞，一律走 next_row_id）
    assert fresh_id == _max_id(instance, "SquadMembers") and fresh_id != 1134 + 1
    for column in ("DeathPriority", "ModelFileName", "PrimaryWeaponId", "SpecialWeaponId"):
        assert fresh[column] == source[column], f"{column} 要照抄第 5 名"
    assert bench.slots()[-1].selected and bench.slots()[-1].index_label().text() == "15"
    assert seen[-1] == ("SquadMembers", fresh_id)
    assert messages and messages[-1].startswith(f"已加一名兵：SquadMembers #{fresh_id}")


def test_add_member_without_a_selection_copies_the_last_one(made, instance) -> None:
    """⑦ 没选中 -> copy_from=0（照最后一名），不是凭空编一行。"""
    context = _context(instance, INFANTRY)
    bench = made(context)

    assert bench.selected_row_id() is None
    last = _row_by_id(instance, "SquadMembers", MARINE_IDS[-1])
    bench.add_button.click()

    fresh = _row_by_id(instance, "SquadMembers", bench.selected_row_id())
    assert fresh is not None and fresh["UnitId"] == INFANTRY
    for column in ("DeathPriority", "ModelFileName", "PrimaryWeaponId", "SpecialWeaponId"):
        assert fresh[column] == last[column]


# --------------------------------------------------------------------------- #
# ⑧ 「⧉ 复制这一名」
# --------------------------------------------------------------------------- #


def test_copy_button_is_disabled_until_a_row_is_selected(made, instance) -> None:
    """⑧ 没选中 -> ⧉ 禁用，并在 tooltip 里说明为什么。"""
    context = _context(instance, INFANTRY)
    bench = made(context)
    messages: list[str] = []
    bench.status_message.connect(messages.append)

    assert not bench.copy_button.isEnabled()
    # ❌ 按钮上**不挂** tooltip（用户 2026-10-03：「我叫你不要在中间显示悬浮窗注释 /
    #    我要你在右边显示悬浮窗注释」）—— 禁用就是禁用，理由走状态栏那一句。
    assert bench.copy_button.toolTip() == ""

    bench.copy_button.click()  # 禁用状态点了什么也不该发生
    bench._on_copy()  # 就算被程序调到：不写库、只给一句话
    assert context.changeset.is_empty
    assert messages and "没选中" in messages[-1]

    _click(bench.slots()[6])  # 第 7 名（Id 119）

    assert bench.copy_button.isEnabled()
    assert bench.copy_button.toolTip() == "", "中栏按钮不许再挂注释浮窗"


def test_copy_duplicates_the_selected_row_with_a_fresh_id(made, instance) -> None:
    """⑧ 复制当前选中那一行：**整行值照抄**、Id 走 next_row_id、UnitId 不变。"""
    context = _context(instance, INFANTRY)
    bench = made(context)
    messages: list[str] = []
    bench.status_message.connect(messages.append)
    _click(bench.slots()[4])
    source = _row_by_id(instance, "SquadMembers", 117)
    bench.copy_button.click()

    assert len(bench.cells) == 15
    fresh_id = bench.selected_row_id()
    assert fresh_id is not None and fresh_id not in MARINE_IDS
    fresh = _row_by_id(instance, "SquadMembers", fresh_id)
    assert fresh is not None
    assert {key: value for key, value in fresh.items() if key != "Id"} == {
        key: value for key, value in source.items() if key != "Id"
    }, "除了新 Id，其余每一列都要与第 5 名逐字相同"
    assert fresh_id == _max_id(instance, "SquadMembers")
    assert bench.slots()[-1].selected
    assert messages and "已复制这一名" in messages[-1] and str(117) in messages[-1]
    assert not hasattr(bench, "row_activated"), "复制不是「去看这一行」"


# --------------------------------------------------------------------------- #
# ⑨ 空态 / 幂等 / 抬头
# --------------------------------------------------------------------------- #


def test_empty_squad_says_a_sentence_instead_of_a_blank_box(made, instance) -> None:
    """⑨ 14 个兵全删光 -> 一句灰字（不是空框），而「＋」还能加回来。"""
    context = _context(instance, INFANTRY)
    bench = made(context)

    for _ in range(14):
        bench.slots()[0].remove_button().click()

    assert bench.cells == () and bench.slots() == ()
    assert _texts(bench) == ["这个班一个人都没有"]
    assert bench.add_button.isEnabled(), "步兵班就算空了也还能加人"

    bench.add_button.click()

    # 班里一个人都没有时加的那一行是**最小可用行**（只填 UnitId）——
    # 不编一个武器 Id 出来（口径在 unit_card.new_member_values 里）。
    assert len(bench.cells) == 1
    assert bench.cells[0].primary_label == ""
    assert bench.cells[0].special_label == "没有副武器"
    assert bench.cells[0].footnote == ""
    assert bench.slots()[0].index_label().text() == "01"


def test_non_infantry_says_why_instead_of_the_zero_member_sentence(made, instance) -> None:
    """反例：载具没有班成员这一套 —— 与「0 人班」不是同一句话，也不许写库。"""
    context = _context(instance, VEHICLE)
    bench = made(context)
    messages: list[str] = []
    bench.status_message.connect(messages.append)

    assert bench.cells == () and bench.member_ids() == ()
    assert _texts(bench) == ["这个单位不是步兵 —— 没有班组成员这一套"]
    assert not bench.add_button.isEnabled() and not bench.copy_button.isEnabled()

    bench._on_add()  # 就算被程序调到：一句话、不写库、不弹窗

    assert context.changeset.is_empty
    assert messages and "没有加成" in messages[-1]


def test_set_context_none_clears_the_block(made, instance) -> None:
    """没进单位（context=None）-> 清空 + 一句说明，按钮禁用。"""
    bench = made(_context(instance, INFANTRY))
    assert len(bench.cells) == 14

    bench.set_context(None)

    assert bench.cells == () and bench.slots() == () and bench.selected_row_id() is None
    assert _texts(bench) == ["还没进单位 —— 选中一个单位之后这里画出他的班"]
    assert not bench.add_button.isEnabled() and not bench.copy_button.isEnabled()


def test_refresh_twice_does_not_stack_widgets(made, instance) -> None:
    """⑨ refresh() 幂等：连调两次格数照旧，控件不叠（findChildren 数得出来）。"""
    from ba_mod_maker.app.widgets.bench_kit import BenchGrid, SlotCard

    bench = made(_context(instance, INFANTRY))
    assert len(bench.findChildren(SlotCard)) == 14

    bench.refresh()
    bench.refresh()

    assert len(bench.cells) == 14 == len(bench.slots())
    assert len(bench.findChildren(SlotCard)) == 14, "旧的格子没摘干净就会 > 14"
    assert len(bench.findChildren(BenchGrid)) == 1, "旧的矩阵没摘干净就会 > 1"
    assert len(bench.section.body_widgets()) == 1, "主体里同时只该有一个孩子"

    bench.slots()[0].remove_button().click()
    assert len(bench.findChildren(SlotCard)) == 13


def test_reload_values_updates_in_place_without_rebuilding(made, instance) -> None:
    """改一格之后：**控件的 id 集合与改之前完全相同**，值真的是新的。

    用户口径（提过几回）：「我修改的时候你能不能不要全量保存一下，不然界面闪一下很影响观感」。
    结构没变（署名一样）时外层调 reload_values() —— 就地换值，一个控件都不重建。
    """
    from PySide6.QtWidgets import QWidget

    from ba_mod_maker.core.edit.refs import CellRef

    context = _context(instance, INFANTRY)
    bench = made(context)
    _click(bench.slots()[2])  # 第 3 名（Id 115）：主武器 M32 MGL、副武器「没有副武器」

    slots_before = [id(slot) for slot in bench.slots()]
    widgets_before = [
        id(widget) for slot in bench.slots() for widget in (slot, *slot.findChildren(QWidget))
    ]
    label_ids_before = [id(label) for slot in bench.slots() for _icon, label in slot.lines()]
    assert bench.cells[2].special_label == "没有副武器"

    # 改一格：副武器 0 -> 45（M1014）——走的是真机那条写库路（context.set_value）
    context.set_value(CellRef("SquadMembers", 115, "SpecialWeaponId"), 45)
    bench.reload_values()

    assert [id(slot) for slot in bench.slots()] == slots_before, "格控件不许重建"
    assert [
        id(widget) for slot in bench.slots() for widget in (slot, *slot.findChildren(QWidget))
    ] == widgets_before, "格里的每一个子控件也不许重建"
    labels_after = [id(label) for slot in bench.slots() for _icon, label in slot.lines()]
    assert labels_after == label_ids_before
    # 值确实变了（保住控件但值没变 = 白保）
    assert bench.cells[2].special_label == "M1014"
    assert bench.slots()[2].lines()[1][1].text() == "M1014"
    assert bench.slots()[2].lines()[1][0].key == _row_by_id(instance, "Weapons", 45)["HUDIcon"]
    assert bench.selected_row_id() == 115, "换值不该动选中态"


def test_reload_values_follows_the_name_icon_key_and_footnote(made, instance) -> None:
    """名字 / 图标键 / 型号键脚注都能就地跟着变（不是只有数量那种字段）。"""
    from ba_mod_maker.core.edit.refs import CellRef

    context = _context(instance, INFANTRY)
    bench = made(context)
    slots_before = [id(slot) for slot in bench.slots()]
    assert bench.cells[2].primary_label == M32

    context.set_value(CellRef("SquadMembers", 115, "PrimaryWeaponId"), 67)  # -> Mk18 CQBR
    context.set_value(CellRef("SquadMembers", 115, "ModelFileName"), "US_MarineRaider_grenade")
    bench.reload_values()

    assert [id(slot) for slot in bench.slots()] == slots_before
    icon, label = bench.slots()[2].lines()[0]
    assert label.text() == MK18 and bench.cells[2].primary_label == MK18
    assert icon.key == "INF_CQBR" and bench.cells[2].primary_key == "INF_CQBR"
    assert bench.slots()[2].footnote_label().text() == "grenade"
    # ❌ 用户 2026-10-03：「我叫你不要在中间显示悬浮窗注释 / 我要你在右边显示悬浮窗注释」
    #    ⇒ 这一格**不再挂 tooltip**（名字与模型本身就写在行上 —— 上面那两条断言读的就是它们；
    #      同一份信息在右栏"选中这一名之后的全部列"里照样看得到）。
    assert bench.slots()[2].toolTip() == "", "中栏的行不许再挂注释浮窗"


def test_reload_values_keeps_widgets_when_the_structure_changed(made, instance) -> None:
    """结构真变了（直接删了一行）而外层还调 reload_values：**照样一个控件都不重建**。

    对不上号的那一格跳过、不为它重建 —— 真正的收拾留给外层判署名之后调 refresh()。
    """
    context = _context(instance, INFANTRY)
    bench = made(context)
    slots_before = [id(slot) for slot in bench.slots()]
    _click(bench.slots()[2])
    context.delete_row("SquadMembers", 115)  # 绕过控件直接删（模拟外层还没 refresh）

    bench.reload_values()

    assert [id(slot) for slot in bench.slots()] == slots_before, "不许重建"
    assert len(bench.slots()) == 14, "这一层不负责加/减格"
    assert bench.selected_row_id() is None, "选中的那一行没了 -> 选中态退掉"

    bench.refresh()  # 外层判完署名该走的那条路

    assert len(bench.slots()) == 13
    assert [slot.index_label().text() for slot in bench.slots()][:3] == ["01", "02", "03"]


def test_the_grid_host_never_scrolls_itself(made, instance) -> None:
    """内层不滚、高度跟内容走（Lead 2026-10-01 口径）。

    BenchPage 中栏**已经有一层** QScrollArea（四块一起滚）—— 两层滚动条同时出现是用户会骂的
    那种小问题。所以这一层：两根条一律 AlwaysOff，高度 = 当前宽度下内容要几行
    （14 格按 152 宽流式换行；宽度越窄行数越多、需要的高度越大，绝不被压成一行）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea

    from ba_mod_maker.app.widgets.bench_kit import BenchGrid
    from ba_mod_maker.app.widgets.bench_squad import SLOT_HEIGHT

    bench = made(_context(instance, INFANTRY))
    host = bench.section.body_widgets()[0]
    grid = bench.findChildren(BenchGrid)[0]

    assert isinstance(host, QScrollArea) and host.widgetResizable()
    assert host.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert host.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert host.hasHeightForWidth() is True
    # 高度就是 FlowLayout 算出来的内容高度（NoFrame -> 帧宽 0，两个数一个字节不差）
    assert host.heightForWidth(1060) == grid.heightForWidth(1060)
    assert host.heightForWidth(700) == grid.heightForWidth(700)
    # 14 格在 1060 宽下要 3 行、在 700 宽下要 4 行 —— 越窄越高（不是被压掉）
    assert host.heightForWidth(1060) >= 3 * SLOT_HEIGHT
    assert host.heightForWidth(700) > host.heightForWidth(1060)


def test_head_says_what_this_block_is_and_offers_only_plus_and_cross(made, instance) -> None:
    """抬头文案 + 两颗按钮；「换」这个动作全块清零（用户口径 1）。"""
    from PySide6.QtWidgets import QPushButton

    bench = made(_context(instance, INFANTRY))
    labels = _labels(bench)

    assert "① 这个班" in labels
    assert "SquadMembers · 一格 = 一个兵 · 图标 = 游戏原生 HUDIcon" in labels
    assert bench.add_button.text() == "＋ 加一名兵"
    assert bench.copy_button.text() == "⧉ 复制这一名"
    assert not any("换" in button.text() for button in bench.findChildren(QPushButton))


def test_icons_come_from_the_store_handed_in(made, instance) -> None:
    """图标只从外面 set_icons 传进来的那份 store 取（⛔ 不自己 new IconCache）。"""
    bench = made(_context(instance, INFANTRY))
    store = _FakeStore()

    bench.set_icons(store)

    # 每一格两行（主武器 / 副武器）里，凡是**有键的**都去问了一次，族名一律是武器族
    expected = [key for cell in bench.cells for key in (cell.primary_key, cell.special_key) if key]
    assert store.calls == [(WEAPON_ICON_FAMILY, key) for key in expected]
    assert (WEAPON_ICON_FAMILY, "INF_CQBR") in store.calls
    assert (WEAPON_ICON_FAMILY, "INF_M32") in store.calls
    assert (WEAPON_ICON_FAMILY, "") not in store.calls, "没有副武器的那一格不该去取图"
    # 键来自武器表那一列（独立读一遍）
    assert bench.cells[0].primary_key == _row_by_id(instance, "Weapons", 67)["HUDIcon"]


# --------------------------------------------------------------------------- #
# 折叠栏（用户 2026-10-02）：默认收起、点抬头展开 / 收起，**控件一个都不重建**
# --------------------------------------------------------------------------- #


def test_the_block_is_collapsed_by_default_and_toggles_without_rebuilding(made, instance) -> None:
    """⭐ 这一块默认收起（用户口径「折叠栏默认是折叠状态」）；点抬头展开、再点收起。

    判据三条（与 tests/test_bench_kit.py 那套同源，这里验的是**这一块真的吃到默认值**）：
    默认 `is_collapsed()`；点抬头空白换态 + 记号 ▸/▾；两种态下 `body_widgets()` 是
    **同一批控件**（AGENTS §0.7：收起只是 hide，一个都不许重建）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    bench = made(_context(instance, INFANTRY))
    section = bench.section

    assert section.collapsible()
    assert section.is_collapsed() is True
    assert section.caret_label().text() == "▸"
    kept = section.body_widgets()
    assert kept, "14 格矩阵该在主体里 —— 不然这条守卫是空转"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is False
    assert section.caret_label().text() == "▾"
    assert section.body_widgets() == kept, "展开不许重建控件"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton, pos=section.head().rect().center())

    assert section.is_collapsed() is True
    assert section.body_widgets() == kept, "收起同样不许重建控件"
    assert bench.cells == bench.cells_now(), "折块不该动数据那一份状态"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


class _FakeStore:
    """假装是 IconCache：只记「谁来问过哪张图」。

    ⛔ 不接真 IconCache —— 那会起加载线程、读游戏包（conftest 里那条"图标线程跨模块残留"
    的坑就是它）。这里要验的只是"族名与键传对了没有"。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def pixmap(self, family: str, key: str):
        self.calls.append((family, key))
        return None


def _click(widget) -> None:
    """真的走一遍 mouseReleaseEvent（点格体 = 选中），不是直接 emit 信号。"""
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
    widget.mouseReleaseEvent(event)


def _rows(instance, table: str, **match) -> list[dict]:
    """按列值筛行，返回**列名 -> 值**（独立于界面去读表）。"""
    data = instance.table(table)
    names = list(data.columns)
    out: list[dict] = []
    for row in data.rows:
        if all(row[names.index(column)] == value for column, value in match.items()):
            out.append({name: row[index] for index, name in enumerate(names)})
    return out


def _row_by_id(instance, table: str, row_id: int) -> dict | None:
    found = _rows(instance, table, Id=row_id)
    return found[0] if found else None


def _max_id(instance, table: str) -> int:
    return max(row["Id"] for row in _rows(instance, table))


def _labels(widget) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in widget.findChildren(QLabel) if label.text()]


def _texts(widget) -> list[str]:
    """主体里那几个孩子的文字（空态那一句就在这儿）。

    主体里要么是 BenchEmpty 本身（QLabel），要么是装着格子的 QScrollArea ——
    所以两种都要认（findChildren 找不到控件自己）。
    """
    from PySide6.QtWidgets import QLabel

    out: list[str] = []
    for child in widget.section.body_widgets():
        labels = [child] if isinstance(child, QLabel) else child.findChildren(QLabel)
        out.extend(label.text() for label in labels if label.property("role") == "bench-empty")
    return out
