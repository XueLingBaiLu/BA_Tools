"""「所有出现单位 / 武器 / 弹药 / 炮塔 ID 的地方，都在右边显示其 ID」—— 四处新面的守卫。

用户原话（2026-10-01）：

    「我需要的是，在**没有编辑栏**的时候，也在右边显示 ID；还有，我希望**所有**出现
      诸如单位、武器、弹药、炮塔 ID 的时候，都在右边显示其 ID」。

上一手（`docs/交接-2026-10-01-外键Id显示.md` §6）只做了右栏 + 中栏词条行 + 弹药行；
§7.2 把剩下两处挂给用户拍板。用户点头之后，就是这一份守的东西：

① 编成台中栏 ① 兵格（`SquadBench` —— SquadMembers.member_id）
② 编成台中栏 ② 武器行 / 弹药行（Weapons.weapon_id / Ammunitions.ammo_id）
③ 编成台中栏 ③ 装甲·机动·能力行（**模板行** link_row_id —— 不是关联行 row_id）
④ 编成台中栏 ④ 改装槽选项行（Options.option_id）
⑤ 左卡（`ReadOnlyCardPanel` —— 单位编辑器里那张，`detail-link` 那几行）

四条纪律，每条都对着一个**真会坏**的地方：

1. 写什么由 `bench_kit.id_badge_text` **一份实现**定：`>0` 才写字，
   `0` / 取不到是空串（空 QLabel 不占宽 —— 没有 Id 的行还是原来那个样子）；
2. **哪一行的 Id**：③ 取 `link_row_id`（模板行 Armors）而不是 `row_id`（关联行
   UnitArmors）；② 弹药行取 `Ammunitions` 而不是携弹行 `WeaponAmmunitions` ——
   这两对**实测就不是一个数**，顺手换成另一个就是"数看着有、其实指错行"；
3. **跨面一致**：同一行实体在左卡与中栏 ③ 上显示的必须是**同一个数**
   （`Armor Infantry Elite 14Men` = `Armors#118`，两张面各画一次）；
4. 多出来那一段**不许把行撑出横向滚动条**（第五手刚为横向溢出吃过亏）。

⚠️ 样本全取真数据（`DataBaseCompiled` / 实例 52085），Id 是 `_work/probe_bench_ids.py`
跑出来钉死的；改数据源要跟着改。断言钉**控件里存的那一份**（`id_label().text()`），
不拿界面上的东西再推算一遍 —— 那样两边一起错还测不出来。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget

from ba_mod_maker.app.viewmodel import bench_loadout as vm_loadout
from ba_mod_maker.app.viewmodel import bench_slots as vm_slots
from ba_mod_maker.app.viewmodel import bench_squad as vm_squad
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.app.widgets.bench_kit import id_badge_text
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 步兵样本：① ② ③ ④ 四块都有行（载具的 ① ② 是空的）。
UNIT = 70
#: 单位 70 的实测读数（`_work/probe_bench_ids.py`，实例 52085）。
MEMBER_IDS = tuple(range(113, 127))
MK18_ID = 67  # Weapons#67 = "Mk18 CQBR"
AMMO_556_ID = 143  # Ammunitions#143 = "5.56x45mm"（携弹行是 WeaponAmmunitions#2859，**不是**它）
ARMOR_LINK = 118  # Armors#118 = "Armor Infantry Elite 14Men"
ARMOR_LABEL = "Armor Infantry Elite 14Men"
MOBILITY_LINK = 190
OPT_BASE, OPT_LONG = 1520, 1521


def _context(instance, unit_id: int):
    """与 UnitEditorScreen 建的那一份同构（寻址 / 共享度 / 撤销栈都走 core）。"""
    from ba_mod_maker.app.screens.editor.pages.base import EditorContext
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


@pytest.fixture()
def bench(qapp, instance):
    """建一个编成台控件、接上真数据；用例收尾统一销毁（conftest 对顶层控件数有钉子）。"""
    made = []

    def build(cls, unit_id: int = UNIT):
        widget = cls()
        made.append(widget)
        widget.set_context(_context(instance, unit_id))
        return widget

    yield build
    for widget in made:
        widget.deleteLater()


def _badge(row) -> str:
    """这一行右边那一段写的是什么（控件里存的那一份，不现算）。"""
    return row.id_label().text()


def _badges_under(widget) -> list[str]:
    return [c.text() for c in widget.findChildren(QLabel) if c.property("role") == "entry-id"]


# --------------------------------------------------------------------------- #
# ⓪ 写什么：一份实现
# --------------------------------------------------------------------------- #


def test_the_badge_text_has_exactly_one_implementation() -> None:
    """`>0` 才写字；`0` / 负数 / 取不到 / 不是整数一律空串（空 QLabel 不占宽）。

    右栏那一条（`bench_rail._id_text`）走的也是这里 —— 同一行在两面上显示的
    必须是同一个数、同一个写法，不许各写一份 `f"#{n}"`。
    """
    from ba_mod_maker.app.widgets.bench_rail import _id_text

    assert id_badge_text(118) == "#118"
    assert id_badge_text(1) == "#1"
    for nothing in (0, -1, None, "", True, False, 1.0):
        assert id_badge_text(nothing) == "", f"{nothing!r} 不该画出 Id"
    # 右栏那一份是**同一个**实现，不是复制一份。
    assert [_id_text(n) for n in (118, 0, None)] == [id_badge_text(n) for n in (118, 0, None)]


# --------------------------------------------------------------------------- #
# ① 兵格 = SquadMembers 那一行
# --------------------------------------------------------------------------- #


def test_every_squad_card_shows_the_member_id_it_is(bench, instance) -> None:
    """① 14 格、每格右上 `#113`…`#126` —— 一格就是 SquadMembers 里的一行。"""
    from ba_mod_maker.app.widgets.bench_squad import SquadBench

    widget = bench(SquadBench)
    cells = vm_squad.squad_cells(CardData(instance), UNIT)
    assert len(cells) == 14, "样本变了：这一节全部读数都对着这 14 名兵"
    assert [_badge(slot) for slot in widget.slots()] == [f"#{c.member_id}" for c in cells]
    assert _badge(widget.slots()[0]) == f"#{MEMBER_IDS[0]}"


# --------------------------------------------------------------------------- #
# ② 武器行 / 弹药行 = 它说的那**一门枪 / 一种弹**
# --------------------------------------------------------------------------- #


def test_the_weapon_and_ammo_rows_show_the_entity_they_name(bench, instance) -> None:
    """② 武器行给 `Weapons` 的 Id、弹药行给 `Ammunitions` 的 Id。

    ❗ 弹药行**不是**携弹行自己的 Id：实测 `Ammunitions#143`（5.56x45mm）对
    `WeaponAmmunitions#2859` —— 顺手换成 `row_id` 就指到另一行去了。
    """
    from ba_mod_maker.app.widgets.bench_loadout import LoadoutBench

    widget = bench(LoadoutBench)
    data = CardData(instance)
    weapons = vm_loadout.loadout_lines(data, UNIT)
    assert [_badge(row) for row in widget.weapon_rows()] == [f"#{w.weapon_id}" for w in weapons]
    assert _badge(widget.weapon_rows()[0]) == f"#{MK18_ID}"

    ammo = [a for w in weapons for a in w.ammo]
    assert [_badge(row) for row in widget.ammo_rows()] == [f"#{a.ammo_id}" for a in ammo]
    first = widget.ammo_rows()[0]
    assert _badge(first) == f"#{AMMO_556_ID}"
    assert first.row_id != AMMO_556_ID, "携弹行与弹种行本来就是两个 Id —— 钉住这一条"
    # 至少真的画了 Id（防上面那两条循环空转）。
    assert any(_badge(row) for row in widget.ammo_rows())


def test_a_row_that_is_not_a_database_row_never_shows_an_id(qapp) -> None:
    """②「缺弹 · 空枪」那行**不是**库里的一行（`row_id = 0`）—— 不许给它编一个 Id。

    同一段还带一条正控（真有一行时必须显示）—— 否则"整个功能没接上"也能过。
    """
    from ba_mod_maker.app.widgets.bench_kit import BenchRow
    from ba_mod_maker.app.widgets.bench_loadout import MissingAmmoRow

    placeholder = MissingAmmoRow(0, None)
    control = BenchRow(name="正控", id_text=id_badge_text(118))
    try:
        assert _badge(placeholder) == ""
        assert _badge(control) == "#118"
    finally:
        placeholder.deleteLater()
        control.deleteLater()


# --------------------------------------------------------------------------- #
# ③ 装甲 / 机动 / 能力行 = 它指向的**模板行**
# --------------------------------------------------------------------------- #


def test_the_stat_rows_show_the_template_row_they_point_at(bench, instance) -> None:
    """③ 一行 = 一个关联行，右边给的却是它**指向的模板行**（Armors / Mobility / Abilities）。

    ❗ 实测 `Armor Infantry Elite 14Men` 就是 `Armors#118`，**不是** `UnitArmors#605`。
    """
    from ba_mod_maker.app.widgets.bench_slots import StatsBench

    widget = bench(StatsBench)
    lines = vm_slots.stat_lines(CardData(instance), UNIT)
    rows = widget.rows()
    assert len(rows) == len(lines) == 3, "样本变了：这一节全部读数都对着这 3 行"
    for row, line in zip(rows, lines, strict=True):
        assert row.name_label().text() == line.label
        want = f"#{line.link_row_id}" if line.link_row_id > 0 else ""
        assert _badge(row) == want, f"{line.label}：右边写着 {_badge(row)!r}，按规则该是 {want!r}"

    armor = next(row for row in rows if row.name_label().text() == ARMOR_LABEL)
    assert _badge(armor) == f"#{ARMOR_LINK}"
    # 再钉一条别的类别（不是只有装甲那一行对）——机动走的是同一条规则、另一个模板表。
    mobility = rows[[i for i, line in enumerate(lines) if line.table == "UnitPropulsions"][0]]
    assert _badge(mobility) == f"#{MOBILITY_LINK}"


# --------------------------------------------------------------------------- #
# ④ 改装槽选项行 = Options 那一行
# --------------------------------------------------------------------------- #


def test_the_option_rows_show_the_option_id(bench, instance) -> None:
    """④ 一行 = 一条 `Options` —— 右边给 `option_id`。"""
    from ba_mod_maker.app.widgets.bench_slots import SlotsBench

    widget = bench(SlotsBench)
    options = [o for g in vm_slots.slot_views(CardData(instance), UNIT) for o in g.options]
    assert [_badge(row) for row in widget.rows()] == [f"#{o.option_id}" for o in options]
    assert [_badge(row) for row in widget.rows()] == [f"#{OPT_BASE}", f"#{OPT_LONG}"]


# --------------------------------------------------------------------------- #
# ⑤ 左卡：原来只在悬浮提示里的那个数，现在在右边
# --------------------------------------------------------------------------- #


def _card_panel(cls, instance, unit_id: int = UNIT, width: int = 419):
    """离屏建一张卡并画好某个单位。

    419 = 用户那台机器上右栏的实际逻辑宽度（实机截图 628 物理 / 150%）。
    """
    panel = cls()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.set_instance(instance)
    panel.set_icons(None)
    panel.resize(width, 1200)
    panel.show()
    panel.show_unit(unit_id)
    return panel


def _armor_row_badges(panel) -> list[str]:
    label = next(
        lb
        for lb in panel.findChildren(QLabel)
        if lb.property("role") == "detail-link" and lb.text() == ARMOR_LABEL
    )
    return _badges_under(label.parent())


def test_the_left_card_shows_the_id_that_used_to_live_only_in_the_tooltip(qapp, instance) -> None:
    """左卡「🛡 装甲」那一行右边必须有 `#118` —— 它原来**只**在悬浮提示里。

    这个数与中栏 ③ 那一行的 `#118` 必须是**同一个**（同一行实体、两张面各画一次），
    所以这里不现算，直接钉死 118。
    """
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    panel = _card_panel(ReadOnlyCardPanel, instance)
    try:
        assert _armor_row_badges(panel) == [f"#{ARMOR_LINK}"], (
            f"左卡「🛡 装甲」右边该是 #{ARMOR_LINK}（与中栏 ③ 同一个数）"
        )
    finally:
        panel.deleteLater()


def test_the_base_card_panel_paints_the_same_badge(qapp, instance) -> None:
    """基类 `UnitCardPanel._link_row` 与只读卡画的是同一个数（§7.2 点名的就是这一处）。

    基类现在只被用例直接建（主屏走的是只读卡），但它是口径的出处 ——
    两处漂了，卡片就会"两种样子"。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = _card_panel(UnitCardPanel, instance)
    try:
        assert _armor_row_badges(panel) == [f"#{ARMOR_LINK}"]
    finally:
        panel.deleteLater()


def test_the_new_badge_never_pushes_the_card_into_a_horizontal_scrollbar(qapp, instance) -> None:
    """多出来那一段字不许把卡撑出横向滚动条（第五手刚为横向溢出吃过亏）。"""
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    panel = _card_panel(ReadOnlyCardPanel, instance)
    try:
        areas = panel.findChildren(QScrollArea)
        assert areas, "这张卡应当有一块滚动区"
        overflow = {
            id(area): area.horizontalScrollBar().maximum()
            for area in areas
            if area.horizontalScrollBar().maximum() > 0
        }
        assert not overflow, f"横向溢出了（hbar max）：{overflow}"
    finally:
        panel.deleteLater()


# --------------------------------------------------------------------------- #
# ⑥ 载具 / 直升机 / 战机 / 舰船：卡上**另外**几块（这一轮补的第二批）
# --------------------------------------------------------------------------- #
#
# ❗ 这一组**有自己的编成台**（载具 = 炮塔四层 / 飞行 = 21 格挂载图，见 bench_page.bench_kind），
# 但下面的用例**不经界面**、直接建 `ReadOnlyCardPanel`（`_card_panel`），所以落页与本组无关；
# 左卡仍是同一张 ReadOnlyCardPanel —— 守的就是那张卡上剩下的几块。
# （⚠️ 2026-10-02 订正：旧注释说"这几类没有编成台、land_on_bench() 只放 KIND_SQUAD 过" ——
#  那道临时闸已拆，载具/飞行双击进来**默认就落在编成台**。）
#
#: (单位 Id, 中文名)：载具 BTR-82 / 直升机 Mi-8MTV-5 / 战机 Su-57 / 舰船 Zubr。
NON_INFANTRY = ((1, "载具"), (7, "直升机"), (44, "战机"), (139, "舰船"))


@pytest.mark.parametrize(("unit_id", "cn"), NON_INFANTRY)
def test_the_option_rows_on_the_card_show_their_option_id(qapp, instance, unit_id, cn) -> None:
    """改装槽**选项行**（◉ BTR-82A / ○ BTR-82AT）：费用右边给 Options#N。

    ❗ 从前它**只有 tooltip 里有** —— 这是这一轮补的第一处。
    """
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    panel = _card_panel(ReadOnlyCardPanel, instance, unit_id)
    try:
        groups = panel.card.card_options
        want = [row.option_id for group in groups for row in group.options]
        if not want:
            # 舰船（Zubr）这一类**卡上没有改装槽** —— 这一条对它没有可守的东西。
            # 正控在另外三类上（载具 / 直升机 / 战机），所以这里 skip 不是"漏测"。
            pytest.skip(f"{cn}（Units#{unit_id}）卡上没有改装槽")
        got: list[str] = []
        for host in panel.findChildren(QWidget):
            if host.property("role") == "option-row":
                got.extend(_badges_under(host))
        assert got == [f"#{oid}" for oid in want], f"{cn}：选项行的 Id 对不上（{got}）"
    finally:
        panel.deleteLater()


@pytest.mark.parametrize(("unit_id", "cn"), NON_INFANTRY)
def test_the_turret_chain_shows_each_id_exactly_once(qapp, instance, unit_id, cn) -> None:
    """炮塔 › 武器 › 弹种 四层：**每个 Id 只出现一次**。

    这一轮把 Id 从 note（灰字）挪到了 entry-id 徽章上 —— 所以两头都要查：
    徽章**必须有**，而 card-note 里**不许**再留一个 #N（§6「只出现一次」：
    同一个 Id 在同一行上出现两遍就不算数）。
    """
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    panel = _card_panel(ReadOnlyCardPanel, instance, unit_id)
    try:
        rows = [
            lb
            for lb in panel.findChildren(QLabel)
            if lb.property("role") == "detail-link" and lb.text()[:1] in "🚙🗼🔫💥"
        ]
        assert rows, f"{cn} 卡上应当有炮塔 › 武器 › 弹种那几行"
        for lab in rows:
            badges = _badges_under(lab.parent())
            assert len(badges) == 1 and badges[0].startswith("#"), (
                f"{cn}：{lab.text()} 右边应当**恰好一个** Id 徽章，实得 {badges}"
            )
            notes = [
                s.text()
                for s in lab.parent().findChildren(QLabel)
                if s.property("role") == "card-note"
            ]
            assert not any("#" in n for n in notes), (
                f"{cn}：{lab.text()} 的 note 里还留着一个 #Id（同一个 Id 出现两遍）：{notes}"
            )
    finally:
        panel.deleteLater()


@pytest.mark.parametrize(("unit_id", "cn"), NON_INFANTRY)
def test_the_occupied_turret_slot_chips_carry_their_turret_id(qapp, instance, unit_id, cn) -> None:
    """21 格那一排：**占用的格子**把 Turrets#N 并进格子文字（小格子没有"右边"）。

    空槽（TurretNId == 0）**不许**加 —— 它没有对应的行（§6：主键 / 不使用不显示）。
    """
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    panel = _card_panel(ReadOnlyCardPanel, instance, unit_id)
    try:
        chips = panel.slot_chips
        if not chips:
            # 同上：没有改装槽就没有 21 格那一排。
            pytest.skip(f"{cn}（Units#{unit_id}）卡上没有改装槽 -> 没有 21 格那一排")
        assert len(chips) % 21 == 0, f"{cn}：21 格那一排该是 21 的整数倍（实得 {len(chips)}）"
        for chip in chips:
            text = chip.text()
            if chip.property("role") == "card-note":
                # ❗ 空槽**只写槽号**（2026-10-02，不再写「N 空」）—— 判据用 role，别认文字。
                assert text.isdigit(), f"{cn}：空槽那一格该只写槽号：{text}"
                assert "#" not in text, f"{cn}：空槽不该有 Id：{text}"
            else:
                assert "#" in text, f"{cn}：占用格应当并进 Turrets#N：{text}"
                assert text.rsplit("#", 1)[1].isdigit(), f"{cn}：格子里那个不是 Id：{text}"
    finally:
        panel.deleteLater()


def test_the_armory_card_keeps_its_old_face(qapp, instance) -> None:
    """**军械库那张卡一个字节都不许跟着变**（用户口径：「军械库设计还是原来的好」）。

    这一条盯着这一轮最容易漏的地方：_ability_chip / _option_row / _slot_chip
    都**覆写在只读卡上**，为的就是不动基类。基类那张卡上：能力 chip 不带 #、
    也没有 21 格那一排 / 选项行（那几块是只读卡独有的）。
    """
    from ba_mod_maker.app.screens.armory.detail_panel import UnitCardPanel

    panel = _card_panel(UnitCardPanel, instance, 1)
    try:
        chips = [lb for lb in panel.findChildren(QLabel) if lb.property("role") == "ability-chip"]
        assert chips, "样本单位应当有能力 chip"
        assert not any("#" in lb.text() for lb in chips), (
            "军械库那张卡的能力 chip 被这一轮的改动带上了 Id：" + str([c.text() for c in chips])
        )
        # 军械库那张卡的改装槽**确实**也用 option-row 这层壳（它的下拉在壳里），
        # 所以这里不能断言"没有 option-row"；要守的是**这一轮别把徽章漏进基类**。
        leaked = [
            badge
            for host in panel.findChildren(QWidget)
            if host.property("role") == "option-row"
            for badge in _badges_under(host)
        ]
        assert not leaked, f"军械库那张卡的选项行被带上了 Id 徽章：{leaked}"
    finally:
        panel.deleteLater()
