"""武器专用页的守卫：40 列一个不少 / 装在哪 / 携弹量 / 取不到图标不留空白。

为什么用**真数据 + 真控件**：这一层要验的正是「控件和数据接对了没有」——
40 列到底是不是从 Weapons 那一行现取的、×N 到底读的是 `WeaponAmmunitions.Quantity`
还是卡片上那个 Σ×HUDMultiplier、点一下到底会不会把右栏换过去、点一格到底有没有开浮层。
假控件 + 假数据只能测出「我以为的接线」。

没有数据源 / 没有字体的平台 skip —— 与 tests/test_unit_editor.py 同一策略：
宁可不给结论，也不给假结论。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 设计稿点名的样本单位 BTR-82A。
BTR82A = 1
#: 样本武器：2A72 30mm。⚠️ 它的 HUDName 里那个「А」是**西里尔字母**，
#: 所以按 HUDIcon（ASCII 的 2A72）找，不按显示名找。
WEAPON_ICON = "2A72"


# --------------------------------------------------------------------------- #
# 夹具与工具
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

    screen = UnitEditorScreen()
    screen.set_instance(instance)
    screen.open_unit(BTR82A)
    yield screen
    screen.deleteLater()


def _open_weapon(screen, instance):
    """打开武器页，交出 (页, 武器 Id)。走真实那条路：screen.open_weapon_page。"""
    from ba_mod_maker.app.screens.editor.pages.weapon_page import WeaponPage

    weapon_id = _weapon_id(instance)
    screen.open_weapon_page(weapon_id)
    page = screen.current_page
    assert isinstance(page, WeaponPage), "screen.open_weapon_page 推出来的不是 WeaponPage"
    assert page.context is screen.context, "专用页要接上同一个 EditorContext"
    return page, weapon_id


def _weapon_id(instance) -> int:
    table = instance.table("Weapons")
    icon = table.column_index("HUDIcon")
    ident = table.column_index("Id")
    return next(row[ident] for row in table.rows if row[icon] == WEAPON_ICON)


def _buttons(page):
    from PySide6.QtWidgets import QPushButton

    left = page.left_column
    assert left is not None, "左栏还没建出来"
    return left.findChildren(QPushButton)


def _panel_title(page) -> str:
    """右栏抬头（RowEntriesPanel 里唯一一个 entries-title）。"""
    from PySide6.QtWidgets import QLabel

    titles = [
        label.text()
        for label in page.entries.findChildren(QLabel)
        if label.property("role") == "entries-title"
    ]
    assert len(titles) == 1, f"抬头应当只有一个，找到 {titles}"
    return titles[0]


def _labels(page):
    from PySide6.QtWidgets import QLabel

    left = page.left_column
    assert left is not None
    return [label.text() for label in left.findChildren(QLabel)]


def _button(page, needle: str):
    """左栏里文字含 `needle` 的那个按钮；找不到就明说，别 StopIteration。"""
    hits = [button for button in _buttons(page) if needle in button.text()]
    assert hits, f"左栏没有文字含「{needle}」的按钮，只有：{[b.text() for b in _buttons(page)]}"
    return hits[0]


def _hero(page):
    """左栏那把武器的剪影标签（按固定的英雄盒认）。"""
    from PySide6.QtWidgets import QLabel

    from ba_mod_maker.app.screens.editor.pages.weapon_page import HERO_BOX

    left = page.left_column
    assert left is not None
    hits = [label for label in left.findChildren(QLabel) if label.size() == HERO_BOX]
    assert len(hits) == 1, f"剪影标签应当只有一个，找到 {len(hits)} 个"
    return hits[0]


def _index(table) -> dict:
    return {name: position for position, name in enumerate(table.columns)}


def _mount_rows(instance, unit_id: int, weapon_id: int):
    """**独立算一遍**「这个单位在哪几个炮塔上装了它」：TurretUnits -> TurretWeapons。

    返回 [(挂载行 Id, 炮塔 Id, WeaponChannel, WeaponPriority)]，按 (炮塔, 顺序, 行 Id) 排。
    """
    turret_units = instance.table("TurretUnits")
    tu = _index(turret_units)
    turrets = {row[tu["TurretId"]] for row in turret_units.rows if row[tu["UnitId"]] == unit_id}
    mounts = instance.table("TurretWeapons")
    tw = _index(mounts)
    out = [
        (
            row[tw["Id"]],
            row[tw["TurretId"]],
            row[tw["WeaponChannel"]],
            row[tw["WeaponPriority"]],
        )
        for row in mounts.rows
        if row[tw["TurretId"]] in turrets and row[tw["WeaponId"]] == weapon_id
    ]
    return sorted(out, key=lambda item: (item[1], item[0]))


def _link_rows(instance, unit_id: int, weapon_id: int):
    """**独立算一遍**携弹记录：[(WeaponAmmunitions 行 Id, 弹种 Id, Quantity)]，按行 Id 排。"""
    links = instance.table("WeaponAmmunitions")
    wa = _index(links)
    out = [
        (row[wa["Id"]], row[wa["AmmunitionId"]], row[wa["Quantity"]])
        for row in links.rows
        if row[wa["UnitId"]] == unit_id and row[wa["WeaponId"]] == weapon_id
    ]
    return sorted(out)


def _ammo_label(instance, ammo_id: int) -> str:
    table = instance.table("Ammunitions")
    am = _index(table)
    for row in table.rows:
        if row[am["Id"]] == ammo_id:
            return row[am["HUDName"]] or row[am["Name"]]
    raise AssertionError(f"弹药表里没有 #{ammo_id}")


def _hud_multiplier(instance, ammo_id: int) -> int:
    table = instance.table("Ammunitions")
    am = _index(table)
    return next(row[am["HUDMultiplier"]] for row in table.rows if row[am["Id"]] == ammo_id)


# --------------------------------------------------------------------------- #
# ① 右栏 40 列一个不少（而且是"按功能分组"的序）
# --------------------------------------------------------------------------- #


def test_the_right_column_lists_all_40_columns(screen, instance) -> None:
    """右栏 = Weapons 那一行的 40 列：一个不少、不重复、全是这一行。"""
    page, weapon_id = _open_weapon(screen, instance)
    cells = page.entries_cells()
    columns = [cell.column for cell in cells]
    weapons = instance.table("Weapons")

    assert len(cells) == 40, f"40 列少了一个：{columns}"
    assert sorted(columns) == sorted(weapons.columns)
    assert len(set(columns)) == len(columns), "有列被列了两遍"
    assert {cell.row_id for cell in cells} == {weapon_id}
    assert {cell.table for cell in cells} == {"Weapons"}
    assert page.entries is not None
    # 右栏是一条一行（RowEntriesPanel），表的归属看每一行自己的落点
    assert {row.cell.table for row in page.entries.rows} == {"Weapons"}
    assert len(page.entries.rows) == 40
    assert "40 列" in page.entries_summary()
    assert page.entries.summary_text == page.entries_summary()


def test_the_40_columns_are_ordered_by_function(screen, instance) -> None:
    """分组**只影响显示顺序**：6 组点全 38 列，两个占位列垫在最后。"""
    from ba_mod_maker.app.screens.editor.pages.weapon_page import FIELD_GROUPS

    page, _weapon_id = _open_weapon(screen, instance)
    columns = [cell.column for cell in page.entries_cells()]
    named = [column for _title, group in FIELD_GROUPS for column in group]

    assert len(named) == 38, "分组表被改过了（应当是 6 组 38 列 + 2 个占位列）"
    assert len(set(named)) == len(named), "同一列被分进两组了"
    assert columns[:38] == named, "前 38 列的顺序就是分组表里的列序"
    assert columns[38:] == ["WeaponChannel", "WeaponPriority"], "占位列垫在最后"
    assert set(columns) == set(instance.table("Weapons").columns)

    # 每一组的列在右栏里必须是**连着的** —— 不然"分组"就只是换个说法
    for title, group in FIELD_GROUPS:
        positions = [columns.index(column) for column in group]
        assert positions == list(range(positions[0], positions[0] + len(group))), title


# --------------------------------------------------------------------------- #
# ② 装在哪：本单位的哪几个炮塔装了它（含 TurretWeapons 的通道 / 优先级）
# --------------------------------------------------------------------------- #


def test_left_column_finds_the_turret_that_mounts_it(screen, instance) -> None:
    """BTR-82A 的 2A72 应当能对到 MainTurret BTR82A（TurretWeapons#838），

    而且左栏把 TurretWeapons 的 WeaponChannel / WeaponPriority 摆出来了 ——
    Weapons 表那两列是全表同值的占位（这一条也在这里钉死）。
    """
    page, weapon_id = _open_weapon(screen, instance)
    expected = _mount_rows(instance, BTR82A, weapon_id)
    assert expected, "样本数据变了：BTR-82A 的 2A72 不在任何 TurretWeapons 行里"

    mounts = page.mounts()
    assert [mount.row_id for mount in mounts] == [item[0] for item in expected]
    mount = mounts[0]
    row_id, turret_id, channel, priority = expected[0]
    assert mount.row_id == row_id
    assert mount.turret_id == turret_id == 2
    assert mount.turret_name == "MainTurret BTR82A"
    assert (mount.channel, mount.priority) == (channel, priority)

    # 左栏真的画了：炮塔名是个可点的按钮，通道 / 优先级在小字里
    assert _button(page, "MainTurret BTR82A").text() == "MainTurret BTR82A"
    assert any("武器通道" in text and "武器优先级" in text for text in _labels(page)), _labels(page)

    # 那两列在 Weapons 表里**确实**全是 0（所以左栏显示的是 TurretWeapons 的值）
    weapons = instance.table("Weapons")
    we = _index(weapons)
    assert {row[we["WeaponChannel"]] for row in weapons.rows} == {0}
    assert {row[we["WeaponPriority"]] for row in weapons.rows} == {0}


def test_the_right_column_header_follows_the_view(screen, instance) -> None:
    """右栏抬头是设计稿那句话（「武器模板的全部词条」），换视图要跟着换。"""
    page, weapon_id = _open_weapon(screen, instance)
    assert _panel_title(page) == "武器模板的全部词条"

    _link_row, ammo_id, _quantity = _link_rows(instance, BTR82A, weapon_id)[0]
    _button(page, _ammo_label(instance, ammo_id)).click()
    assert _panel_title(page) == "弹药模板的全部词条"

    _button(page, "MainTurret BTR82A").click()
    assert _panel_title(page) == "炮塔挂载的全部词条"


def test_clicking_a_mount_row_brings_the_mount_record_to_the_right(screen, instance) -> None:
    """点炮塔名 -> 右栏换成那条 TurretWeapons（通道 / 优先级在那儿改）。"""
    page, weapon_id = _open_weapon(screen, instance)
    row_id, _turret_id, _channel, _priority = _mount_rows(instance, BTR82A, weapon_id)[0]

    _button(page, "MainTurret BTR82A").click()
    assert (page.focus_table, page.focus_row) == ("TurretWeapons", row_id)
    assert [cell.column for cell in page.entries_cells()] == list(
        instance.table("TurretWeapons").columns
    )
    assert {row.cell.table for row in page.entries.rows} == {"TurretWeapons"}
    assert "武器通道" in page.entries_summary()

    # 通道那一格点一下：浮层落在**这一行**上（不是武器自己那一行）
    _button(page, "武器通道").click()
    assert page.entries.has_editor, "点「武器通道」没有开浮层"
    assert str(page.entries.open_cell) == f"TurretWeapons#{row_id}.WeaponChannel"

    # 「← 回到武器」-> 40 列回来
    _button(page, "回到武器").click()
    assert page.focus_table == "Weapons"
    assert len(page.entries_cells()) == 40


# --------------------------------------------------------------------------- #
# ③ 携弹量 = WeaponAmmunitions.Quantity（不是弹药行上的数）
# --------------------------------------------------------------------------- #


def test_ammo_quantity_comes_from_weapon_ammunitions(screen, instance) -> None:
    """左栏的 ×N 是**携弹记录**那一行的 Quantity，不是卡片上 Σ×HUDMultiplier 那个数。"""
    page, weapon_id = _open_weapon(screen, instance)
    expected = _link_rows(instance, BTR82A, weapon_id)
    assert expected, "样本数据变了：BTR-82A 的 2A72 没有携弹记录"

    lines = page.ammo_lines()
    assert [(line.row_id, line.ammo_id, line.quantity) for line in lines] == expected

    texts = [button.text() for button in _buttons(page)]
    for _row_id, ammo_id, quantity in expected:
        assert f"×{quantity}" in texts, f"左栏没有 ×{quantity}：{texts}"
        # 卡片上那个数是 Σ携弹量 × HUDMultiplier —— 这一页**不许**显示它
        multiplier = _hud_multiplier(instance, ammo_id)
        if multiplier > 1:
            assert f"×{quantity * multiplier}" not in texts, "显示成卡片那个数了"
        # 每一行都点得到"进弹药页"的那个按钮（弹种名）
        assert _ammo_label(instance, ammo_id) in texts
        # 「落在哪」那行小字写的是**那一行**，不是弹药行
        assert any(f"武器携弹 #{_row_id}" in text for text in _labels(page))


def test_clicking_an_ammo_row_switches_the_right_column_to_the_ammo(screen, instance) -> None:
    """点弹种 -> 右栏换成弹药页：67 列一个不少 **+ 那条携弹行**（携弹量在那儿才改得动）。"""
    page, weapon_id = _open_weapon(screen, instance)
    row_id, ammo_id, _quantity = _link_rows(instance, BTR82A, weapon_id)[0]

    _button(page, _ammo_label(instance, ammo_id)).click()
    assert (page.focus_table, page.focus_row) == ("Ammunitions", ammo_id)

    cells = page.entries_cells()
    ammo_columns = [cell.column for cell in cells if cell.table == "Ammunitions"]
    link_columns = [cell.column for cell in cells if cell.table == "WeaponAmmunitions"]
    assert ammo_columns == list(instance.table("Ammunitions").columns), "67 列少了一个"
    assert len(ammo_columns) == 67
    assert link_columns == list(instance.table("WeaponAmmunitions").columns)
    assert {cell.row_id for cell in cells if cell.table == "WeaponAmmunitions"} == {row_id}
    tables = {row.cell.table for row in page.entries.rows}
    assert "Ammunitions" in tables and "WeaponAmmunitions" in tables
    assert "67 列" in page.entries_summary()
    assert "武器携弹#" in page.entries_summary()

    # 弹种那一行在左栏被标出来了（用户在哪儿一目了然）
    assert "▸" in _button(page, _ammo_label(instance, ammo_id)).text()
    assert any("回到武器" in button.text() for button in _buttons(page))


def test_clicking_the_quantity_edits_that_link_row(screen, instance) -> None:
    """点 ×N 就地改：浮层落在 WeaponAmmunitions#…Quantity 上，改了真的进数据、能撤销。"""
    page, weapon_id = _open_weapon(screen, instance)
    row_id, _ammo_id, quantity = _link_rows(instance, BTR82A, weapon_id)[0]

    _button(page, f"×{quantity}").click()
    assert page.entries.has_editor, "点 ×N 没有开浮层（右栏没换过去？）"
    ref = page.entries.open_cell
    assert str(ref) == f"WeaponAmmunitions#{row_id}.Quantity"

    page._commit(ref, quantity + 5)
    assert screen.context.cell(ref).raw == quantity + 5
    assert not page.entries.has_editor

    screen.undo()
    assert screen.context.cell(ref).raw == quantity


# --------------------------------------------------------------------------- #
# ④ 取不到图标不留空白
# --------------------------------------------------------------------------- #


def test_missing_icons_leave_text_never_a_blank(screen, instance, qapp, tmp_path) -> None:
    """剪影取不到就写图标键；有图就画图 —— 三态都验一遍。"""
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel, QPushButton

    from ba_mod_maker.app.screens.editor.pages.weapon_page import (
        AMMO_ICON_BOX,
        WEAPON_ICON_FAMILY,
    )
    from ba_mod_maker.app.theme.icons import IconCache

    class StubCache(QObject):
        """只认一个键的假图标源：验「图到了就画图、回落文字让位」。"""

        loaded = Signal()

        def pixmap(self, family, key_):
            if (family, key_) != (WEAPON_ICON_FAMILY, key):
                return None
            pixmap = QPixmap(40, 16)
            pixmap.fill(Qt.GlobalColor.white)
            return pixmap

    page, weapon_id = _open_weapon(screen, instance)
    weapons = instance.table("Weapons")
    we = _index(weapons)
    key = next(row[we["HUDIcon"]] for row in weapons.rows if row[we["Id"]] == weapon_id)
    assert key, "样本武器的 HUDIcon 是空的？"

    # ① 三个来源全断的 IconCache（没游戏 / 没旧缓存 / 没运行时缓存）
    dead = IconCache(
        tmp_path / "no-legacy",
        game_root=tmp_path / "no-game",
        runtime_cache=tmp_path / "no-runtime",
    )
    assert dead.available is False
    page.set_icons(dead)
    art = _hero(page)
    assert art.text() == key, "取不到剪影时必须写文字（图标键），不能空白"
    assert art.pixmap().isNull()
    ammo_art = [
        label for label in page.left_column.findChildren(QLabel) if label.size() == AMMO_ICON_BOX
    ]
    assert ammo_art, "弹种前面那颗小图不见了"
    assert all(label.text() for label in ammo_art), "小图取不到就写图标键，不能空白"

    # ② 干脆没有图标源
    page.set_icons(None)
    assert _hero(page).text() == key
    assert all(button.text() for button in page.left_column.findChildren(QPushButton))

    # ③ 图到了就画图
    page.set_icons(StubCache())
    art = _hero(page)
    assert not art.pixmap().isNull(), "图到了却没画上去"
    assert art.text() == "", "画了图就不该再留着回落文字"


def test_icon_families_agree_with_core() -> None:
    """族名只有一份口径（core/assets/families.py），界面别自己编。"""
    from ba_mod_maker.app.screens.editor.pages.weapon_page import (
        AMMO_ICON_FAMILY,
        WEAPON_ICON_FAMILY,
    )
    from ba_mod_maker.core.assets.families import family

    weapon = family(WEAPON_ICON_FAMILY)
    ammo = family(AMMO_ICON_FAMILY)
    assert weapon is not None and weapon.column == "Weapons.HUDIcon"
    assert ammo is not None and ammo.column == "Ammunitions.HUDIcon"
