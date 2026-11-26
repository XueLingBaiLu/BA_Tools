"""编成台 ① 「本单位的炮塔」（载具 / 舰船那一块）：名册 · 删点 · 抬头。

控件：app/widgets/bench_turrets.py::TurretsBench（只摆控件 + 发信号）
取数：app/viewmodel/bench_turrets.py 与 unit_card（零 Qt）

口径（用户 2026-10-02 原话）：

    「**还是把所有的炮塔都列出来吧，我毕竟要增删炮塔的**」

⇒ ① 画的是**名册**（unit_card.roster_turret_chain = TurretUnits 候选名单 ∪ 选项的 21 个槽），
  不是卡上"当前装着的那几座"（card_turret_chain —— 那是游戏 ApplyCurrentMods 之后的样子，
  卡只读、必须照实画）。**两份必须同时存在**：卡照实、① 列全。用户原话里那句"毕竟要增删"
  就是判据 —— 只画当前装着的，另外几座在界面上根本够不着，谈不上增删。

实测（实例 52085，下面断言里那些数字都是**跑出来**的，不是推的）：

| 单位 | 名册 | 当前改装装着 | 差 |
|---|---|---|---|
| 21 Stryker RV | 5 座（18 / 55 / 522 / 892 / 893） | 1 座（522，槽 0） | 4 |
| 206 BTR-ZD | 6 座 | 3 座 | 3 |
| 1 BTR-82A | 2 座（2 / 364） | 1 座（2） | 1 |
| 70 步兵 | 0 座（负数编码的假炮塔不算） | 0 座 | 0 |

名册里每一座都带 unit_row_id > 0（本单位那一行 TurretUnits 在），所以 ✕ 都点得动 ——
这一条是本文件最该守的：**列出来了，还要删得掉**（单位 171 是反例：它那 3 座全是当前选项的
槽填进来的、TurretUnits 里一行都没有 ⇒ **删不掉**；但 ✕ 照画、点了在状态栏说清「没得删」——
口径 2026-10-02 由用户定：「把所有没显示出来的词条显示出来」）。

2026-10-02 第二轮（用户改口径，撤销临时加过的那一批按钮）：

    「改顺序我还是直接改 order 吧，不用那三个按钮了」
    「我很讨厌"换"这个功能，它本质上就是删除再新增，你给这个功能单独做一个按钮，非常多余」

⇒ ① 的行尾只有 **↗ ＋ ✕**（弹药行 **↗ ✕**，另加"点 ×数量 就地改携弹量"）：
   ＋ 加一座炮塔 / 挂武器 / 加弹种；✕ 删关联行（含共用行人数警告、删炮塔时腾掉指着它的槽）。
   **"改"不在这里做** —— 点一行 → 右栏列出那一行的全部列 → 直接改 Order / TurretId /
   WeaponId / AmmunitionId 那一格（本文件最后一节钉的就是这条路真的写进缓存）。
   ⛔ 没有 🔁（换）、没有 ↑↓、没有 ⇄；那 23 条为它们写的用例已删，不许复活。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QWidget

from ba_mod_maker.app.viewmodel import bench_turrets as vm
from ba_mod_maker.app.viewmodel.unit_card import (
    CardData,
    candidate_turret_ids,
    card_turret_chain,
    roster_turret_chain,
)
from ba_mod_maker.app.widgets.bench_rail import MODE_COLUMNS
from ba_mod_maker.core.data import find_dataset_root
from conftest import settle

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 设计稿那张载具（Stryker RV：变体单位，当前改装只装一座 M240 塔）。
VEHICLE = 21
#: 设计稿那张 BTR-ZD（ZU23：3 个槽）
BTR_ZD = 206
#: 反例：步兵 —— 名册必须是空的（负数编码的假炮塔不算炮塔）。
INFANTRY = 70

#: 单位 21 的 M240 塔：TurretUnits#763 → Turrets#522（Bushmaster2 塔是 #892）。
M240_TURRET = 522
M240_ROW = 763
BUSHMASTER2_TURRET = 892
#: 单位 21 的 M240 武器（Weapons#27 → TurretWeapons#823）＋ 它的携弹行
#: （WeaponAmmunitions#1193，AmmunitionId 27，Quantity 400）。
M240_WEAPON = 27
M240_WEAPON_ROW = 823
M240_AMMO_ROW = 1193
M240_AMMO = 27

#: 共用炮塔样本：TurretWeapons#289（Weapons#103）挂在 Turrets#155 上 —— 实测 16 个单位共用。
SHARED_TURRET = 155
SHARED_UNIT = 7
SHARED_WEAPON_NEW = 104

#: 每座炮塔自己折叠的样本：Turrets#15 在单位 15 与单位 387 的名册里**都有**（各 3 门武器 / 4 条弹）
#: —— "按 turret_id 跨换单位记住"这条只能用它测。
FOLD_UNIT_A = 15
FOLD_UNIT_B = 387
FOLD_TURRET = 15


# --------------------------------------------------------------------------- #
# 起控件（与 tests/test_bench_squad.py 同一套）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def made(qapp):
    """建一个 TurretsBench（用例结束统一销毁）。"""
    from ba_mod_maker.app.widgets.bench_turrets import TurretsBench

    widgets: list[TurretsBench] = []

    def build(context) -> TurretsBench:
        bench = TurretsBench()
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


def _m240_ammo_row(bench):
    """单位 21 那门 M240 的携弹行（WeaponAmmunitions#1193）—— 树里第一条不是它（#21）。"""
    return next(row for row in bench.ammo_rows() if row.row_id == M240_AMMO_ROW)


def _unfold_groups(bench) -> None:
    """把③里那几组"改装槽分组"全展开。

    ❗ 分组**默认收起**（用户 2026-10-02：「我要所有折叠栏默认折叠」）—— 这些用例点的是
    **炮塔行**，而收起的组里那些行是不可见的：QTest 往隐藏控件上点，事件到不了。
    """
    for row in bench.group_rows():
        if not bench.is_group_open(row.modification_id):
            bench.toggle_group_fold(row)
    bench.layout().activate()


def _label_with_role(widget, role: str):
    from PySide6.QtWidgets import QLabel

    return next(
        (label for label in widget.findChildren(QLabel) if label.property("role") == role), None
    )


# --------------------------------------------------------------------------- #
# 取数：名册（全部）vs 卡上（当前装着的那几座）
# --------------------------------------------------------------------------- #


def test_the_block_lists_every_turret_the_unit_can_carry(instance) -> None:
    """① 的行 = **名册**，不是卡上那条链 —— 单位 21 差 4 座（就是用户说够不着的那 4 座）。"""
    data = CardData(instance)

    roster = candidate_turret_ids(data, VEHICLE)
    assert roster == (18, 55, 522, 892, 893), "样本变了：这一节的读数都对着这 5 座"

    fitted = card_turret_chain(data, VEHICLE)
    assert tuple(turret.turret_id for turret in fitted) == (522,), "卡上只装 M240 塔（槽 0）"

    tree = vm.turret_tree(data, VEHICLE)
    assert tuple(line.turret_id for line in tree.turrets) == roster
    assert len(tree.turrets) - len(fitted) == 4, "这一条就是用户的诉求：另外 4 座要看得见"


def test_only_the_fitted_turret_carries_a_slot_number(instance) -> None:
    """槽号仍按**当前选中**算：没被选项选中的那几座 slots 是空的（界面上就没有那一段）。"""
    data = CardData(instance)
    tree = vm.turret_tree(data, VEHICLE)

    assert [line.slot_text for line in tree.turrets] == ["", "", "0", "", ""]

    btr = vm.turret_tree(data, BTR_ZD)
    assert tuple(line.turret_id for line in btr.turrets) == (335, 336, 337, 160, 954, 183)
    assert [line.slot_text for line in btr.turrets] == ["2", "", "", "", "0", "1"]


def test_every_turret_on_the_roster_has_a_row_of_its_own_to_delete(instance) -> None:
    """名册里每一座都带 TurretUnits 行 Id —— 列出来只是第一步，**删得掉**才是用户要的。"""
    data = CardData(instance)

    for unit_id in (VEHICLE, BTR_ZD, 1):
        tree = vm.turret_tree(data, unit_id)
        assert tree.turrets, f"单位 {unit_id} 应该有炮塔"
        assert all(line.unit_row_id > 0 for line in tree.turrets), (
            f"单位 {unit_id}：有炮塔没有删点（✕ 会被收起来）"
        )


def test_a_turret_the_option_did_not_pick_can_still_be_deleted(instance) -> None:
    """没装上 ≠ 删不掉：删的是**本单位那份 TurretUnits 行**，选项里没有它的槽要腾。"""
    data = CardData(instance)

    # 18（M2 塔）：当前改装是 StrykerRv M240，槽里没有它 —— 删完就真的从界面上消失
    loose = vm.plan_remove_turret(data, VEHICLE, 18)
    assert loose.removable and loose.unit_row_id == 26
    assert loose.slots == () and loose.clears == ()
    assert loose.slot_text == ""

    # 522（M240 塔）：当前选项的槽 0 指着它 —— 那一个槽要一起腾掉，否则"点了 ✕ 没反应"
    fitted = vm.plan_remove_turret(data, VEHICLE, 522)
    assert fitted.unit_row_id == 763
    assert fitted.clears == ((0, "Turret0Id"),)
    assert fitted.slot_text == "0"


def test_infantry_has_no_roster_at_all(instance) -> None:
    """步兵那条路的"假炮塔"是负数编码的 —— 名册必须是空的（不许把假炮塔画出来）。"""
    data = CardData(instance)

    assert candidate_turret_ids(data, INFANTRY) == ()
    tree = vm.turret_tree(data, INFANTRY)
    assert tree.turrets == ()


# --------------------------------------------------------------------------- #
# 控件：五座都画出来、每座都有 ✕、抬头说的是"本单位的"
# --------------------------------------------------------------------------- #


def test_the_widget_draws_the_whole_roster_with_a_remove_button_on_each(made, instance) -> None:
    """画出来的、看得见的，才算数：5 行炮塔，每行一颗 ✕（不在"当前改装"里也长着）。"""
    bench = made(_context(instance, VEHICLE))

    assert [row.turret_id for row in bench.turret_rows()] == [18, 55, 522, 892, 893]
    assert all(row.remove_button().isHidden() is False for row in bench.turret_rows())
    loose = bench.row_for_turret(18)
    assert loose is not None and loose.row_id == 26, "没装上的那一座也要带着它自己那一行 Id"


def test_add_puts_the_turret_on_the_next_free_slot_and_records_a_row(made, instance) -> None:
    """「＋ 加一座炮塔」= 写当前选项的下一个空 TurretNId（槽 0 占了 -> 槽 1）＋ 本单位补一行。

    这一条补的是**增删里的"增"** —— 在它之前 ① 的 ＋ 一条用例都没有（只有 ✕ 那一面有）。
    """
    context = _context(instance, VEHICLE)
    bench = made(context)

    options = context.data.t("Options")
    assert options.i(options.by_id(757), "Turret1Id") == 0, "前置：单位 21 的选项只填了槽 0"
    links = context.data.t("TurretUnits")
    orders = [links.i(item, "Order") for item in links.where("UnitId", VEHICLE)]

    row_id = bench.add_turret(364)  # BTR82AT 塔（挑的是别台车也有的那一座，能挂）

    assert row_id > 0
    options = context.data.t("Options")
    assert options.i(options.by_id(757), "Turret1Id") == 364, "要落在**下一个空槽**（Turret1Id）"
    assert context.changeset.is_dirty("Options", 757, "Turret1Id")

    added = context.data.t("TurretUnits").by_id(row_id)
    assert added is not None, "本单位补的那一行 TurretUnits"
    links = context.data.t("TurretUnits")
    assert links.i(added, "UnitId") == VEHICLE and links.i(added, "TurretId") == 364
    assert links.i(added, "Order") == max(orders) + 1, "Order 取最大值 + 1（排到队尾）"

    # 画出来了：名册多一座、它带着自己那一行 Id（✕ 点得动），状态栏说人话
    row = bench.row_for_turret(364)
    assert row is not None and row.row_id == row_id
    assert len(bench.turret_rows()) == 6
    assert f"TurretUnits #{row_id}" in bench.status_text()


def test_the_widget_rerenders_the_roster_when_the_unit_changes(made, instance) -> None:
    """换单位 = 换名册（幂等：不许叠控件、不许留着上一台车的行）。"""
    bench = made(_context(instance, VEHICLE))
    assert len(bench.turret_rows()) == 5

    bench.set_context(_context(instance, BTR_ZD))
    assert len(bench.turret_rows()) == 6
    # ❗ 翻口径（2026-10-02，用户：「炮塔和改装槽，也要有树状关系」）：行序从"名册原序"改成
    #    **按改装槽分组**——被选项挂上来的那几座排在它那个槽下面，本车自带（没有选项挂它）
    #    的排最后的「不属于任何改装槽」组。BTR-ZD 的 160 是被选项挂的，954 / 183 是本车自带。
    assert [row.turret_id for row in bench.turret_rows()] == [335, 336, 337, 954, 183, 160]
    assert [row.modification_id for row in bench.group_rows()][-1] == 0, "本车自带那组永远排最后"

    bench.set_context(_context(instance, VEHICLE))
    assert len(bench.turret_rows()) == 5


def test_the_headline_says_the_unit_not_the_current_option(made, instance) -> None:
    """抬头必须跟着范围改 —— 画着名册却写「当前改装下的炮塔」就是在说谎。"""
    from ba_mod_maker.app.widgets.bench_turrets import TITLE

    assert TITLE == "① 本单位的炮塔 → 武器 → 弹药"

    bench = made(_context(instance, VEHICLE))
    title = _label_with_role(bench, "bench-section-title")
    assert title is not None and title.text() == TITLE
    assert "当前改装下" not in title.text()


def test_an_infantry_unit_draws_no_turret_row(made, instance) -> None:
    """步兵接上这一块也不许画行（名册空 -> 一句灰字，不是空框）。"""
    bench = made(_context(instance, INFANTRY))

    assert bench.turret_rows() == ()
    assert bench.tree() is not None and bench.tree().turrets == ()
    assert bench.section().body_widgets(), "空态该有一句话占着，不是空框"


# =========================================================================== #
# 2026-10-02（第二轮口径）· ① 的行尾 = ＋ 与 ✕；"改"走右栏直接改列
#
# 用户原话：「我要能够自由增、删、修改炮塔，只要我在中间栏位能修改的，我都要能修改。」
# 随后又撤销了"单独做按钮"那一条：
#   「改顺序我还是直接改 order 吧，不用那三个按钮了」
#   「我很讨厌"换"这个功能，它本质上就是删除再新增，你给这个功能单独做一个按钮，非常多余」
#
# ⇒ 拆掉 🔁（换炮塔 / 换武器 / 换弹种）、↑↓（炮塔 / 武器顺序）、⇄（挪挂载槽）那六颗：
#   换 = ✕ 删 + ＋ 增；改顺序 / 换外键 = 点一行到**右栏直接改那一列**（最后一节钉它）。
#
# 下面每一条的期望值都是**实跑读数**（实例 52085），不是推的：
#   - 单位 21：TurretUnits 5 行（#26/#81/#763/#1673/#1674）Order 全 0，TurretId=18/55/522/892/893；
#   - 单位 21：TurretWeapons#823 = WeaponId 27（M240）挂在 Turrets#522；
#     它的携弹行 WeaponAmmunitions#1193（AmmunitionId 27，Quantity 400）；
#   - 单位 7：Turrets#155 被 **16** 个单位共用（TurretWeapons#289 = Weapons#103 S-8）。
# =========================================================================== #


# --------------------------------------------------------------------------- #
# 一、折叠 · 行尾工具（折叠默认收起；行尾只有 ＋ 与 ✕）
# --------------------------------------------------------------------------- #


def test_every_row_carries_only_the_add_and_remove_tools(made, instance) -> None:
    """⭐ 行尾只有 ＋ 与 ✕（弹药行只有 ✕）：🔁 / ↑ / ↓ / ⇄ 一颗都不许再长出来。

    用户 2026-10-02：「不用那三个按钮了」＋「你给这个功能单独做一个按钮，非常多余」——
    所以这里连"那几颗按钮"的读数口都要**不存在**（不只是藏起来）。
    """
    bench = made(_context(instance, VEHICLE))
    bench.section().set_collapsed(False)

    turret_row = bench.row_for_turret(M240_TURRET)
    assert turret_row is not None
    # ❗ 翻口径（2026-10-02 晚）：行尾多了**一颗 ⧉**（复制模板另存一份，见交接 §23）——
    #    「只有 ＋ 与 ✕」改成「只有 ＋ / ⧉ / ✕」，那几颗 🔁 ↑ ↓ ⇄ 仍然一颗都不许回来。
    assert [button.text() for button in turret_row.tools()] == ["＋", "⧉"], "炮塔行 = ＋ / ⧉"
    assert turret_row.add_weapon_button().isHidden() is False
    assert turret_row.remove_button().isHidden() is False
    # ❗ 行尾那颗 ↗（「去看这一行」）2026-10-02 按用户要求删了 —— 炮塔行只剩 ＋ / ✕。
    assert not hasattr(turret_row, "link_button")
    for name in ("swap_button", "move_up_button", "move_down_button", "slot_button"):
        assert not hasattr(turret_row, name), f"炮塔行不该再长出 {name}"

    weapon_row = next(row for row in bench.weapon_rows() if row.weapon_id == M240_WEAPON)
    assert [button.text() for button in weapon_row.tools()] == ["＋", "⧉"], "武器行 = ＋ / ⧉"
    assert weapon_row.add_ammo_button().isHidden() is False
    assert not hasattr(weapon_row, "swap_button")

    ammo_row = _m240_ammo_row(bench)
    assert [button.text() for button in ammo_row.tools()] == ["⧉"], (
        "弹药行只剩 ⧉（×数量 是就地编辑）"
    )
    assert not hasattr(ammo_row, "swap_button")


def test_the_turret_row_clone_saves_a_new_turret_with_its_weapons(made, instance) -> None:
    """⭐ 炮塔行那颗 ⧉ = 复制 `Turrets` 模板另存一份 **＋ 它下面的 `TurretWeapons` 行**。

    ❗ 为什么必须带上武器：`TurretWeapons` 是按 `TurretId` 挂在炮塔模板上的（那张表没有
    `UnitId`）—— 只复制塔本体的话新塔是**空壳**，用户还得一门一门重加。
    实测样本：单位 21 的 M240 塔 = `Turrets#522` ← `TurretUnits#763`，它名下有 1 行武器
    `TurretWeapons#823`（`Weapons#27`）。
    """
    ctx = _context(instance, VEHICLE)
    bench = made(ctx)
    bench.section().set_collapsed(False)
    row = bench.row_for_turret(M240_TURRET)
    assert row is not None
    assert cell_of(instance, "TurretUnits", M240_ROW, "TurretId") == M240_TURRET
    old_weapons = turret_weapon_rows(instance, M240_TURRET)
    assert old_weapons, "样本变了：这座塔名下没有武器行"
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    clone_tool = row.tools()[-1]  # ⧉ 在 ✕ 左边、＋ 右边
    QTest.mouseClick(clone_tool, Qt.MouseButton.LeftButton, pos=clone_tool.rect().center())

    new_turret = cell_of(instance, "TurretUnits", M240_ROW, "TurretId")
    assert new_turret != M240_TURRET, "关联行没指到新塔"
    assert row_exists(instance, "Turrets", new_turret), "模板行没另存出来"
    copied = turret_weapon_rows(instance, new_turret)
    assert len(copied) == len(old_weapons), "武器没跟着复制"
    assert {w for _row_id, w in copied} == {w for _row_id, w in old_weapons}, "武器 Id 要照抄"
    for row_id, _weapon in old_weapons:
        assert row_exists(instance, "TurretWeapons", row_id), "别人用着的那几行不许动"
    assert seen == [("Turrets", new_turret)], "复制完右栏要落到新塔"
    # ❗ 整次复制（塔 ＋ 它名下的武器）是**一条**撤销 —— 用户 2026-10-02：「做吧」
    #    （做「一次 Ctrl+Z 把整次复制收回来」）。
    assert ctx.undo_depth == 1, "炮塔 ＋ 它的武器要打包成一条"
    assert "撤销" in ctx.undo_last()
    assert cell_of(instance, "TurretUnits", M240_ROW, "TurretId") == M240_TURRET, (
        "一次 Ctrl+Z 该全回来"
    )
    assert not row_exists(instance, "Turrets", new_turret), "新塔没撤掉"
    assert turret_weapon_rows(instance, new_turret) == [], "连带复制的武器没撤掉"
    assert turret_weapon_rows(instance, M240_TURRET) == old_weapons, "老塔的武器该原样"


def cell_of(instance, table: str, row_id: int, column: str):
    rows = instance.table(table)
    index = rows.column_index("Id")
    col = rows.column_index(column)
    for row in rows.rows:
        if row[index] == row_id:
            return row[col]
    raise AssertionError(f"{table} 里没有 #{row_id}")


def row_exists(instance, table: str, row_id: int) -> bool:
    rows = instance.table(table)
    index = rows.column_index("Id")
    return any(row[index] == row_id for row in rows.rows)


def turret_weapon_rows(instance, turret_id: int) -> list[tuple[int, int]]:
    rows = instance.table("TurretWeapons")
    i_id = rows.column_index("Id")
    i_turret = rows.column_index("TurretId")
    i_weapon = rows.column_index("WeaponId")
    return [(row[i_id], row[i_weapon]) for row in rows.rows if row[i_turret] == turret_id]


def test_the_block_is_collapsed_by_default_and_the_head_toggles_it(made, instance) -> None:
    """用户口径：折叠栏**默认收起**；点抬头空白就展开 / 再点收起（控件一个都不销毁）。"""
    bench = made(_context(instance, VEHICLE))
    section = bench.section()

    assert section.is_collapsed() is True, "默认必须是收起的"
    assert section.caret_label().text() == "▸"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton)
    assert section.is_collapsed() is False
    assert section.caret_label().text() == "▾"
    assert bench.turret_rows(), "展开之后行还在（收起只是 hide，控件没销毁）"

    QTest.mouseClick(section.head(), Qt.MouseButton.LeftButton)
    assert section.is_collapsed() is True


def test_nothing_is_left_visible_outside_the_body_when_the_block_is_folded(made, instance) -> None:
    """⭐⭐ 折起来之后**只剩抬头一行**：body_widgets() 之外不许还有可见控件。

    判据是"候选宿主在不在主体里"：挂在 root 上的话，展开→冒候选→收起，宿主仍可见
    （它不跟着 body 一起藏）—— 这条用例就是为它写的（Lead 2026-10-02 抓到的问题）。
    """
    bench = made(_context(instance, VEHICLE))
    section = bench.section()

    section.set_collapsed(False)
    assert bench.begin_add() is True, "展开状态下点抬头那颗 ＋ 该冒出候选"
    picker = bench.picker()
    assert picker is not None and bench.picker_visible() is True
    host = picker.parentWidget()
    assert host in section.body_widgets(), "候选宿主必须在 section 主体里（折起来才跟着藏）"

    section.set_collapsed(True)
    head, body = section.head(), section.body()
    strays = [
        widget
        for widget in bench.findChildren(QWidget)
        if widget.isVisibleTo(bench)
        and widget is not section
        and not (widget is head or head.isAncestorOf(widget))
        and not (widget is body or body.isAncestorOf(widget))
    ]
    assert strays == [], [type(widget).__name__ for widget in strays]


def test_clicking_the_header_add_expands_the_block_before_showing_candidates(
    made, instance
) -> None:
    """收起状态下点抬头那颗 ＋：**先展开**再冒候选 —— 不展开的话用户看到的是"点了没反应"。"""
    bench = made(_context(instance, VEHICLE))
    assert bench.section().is_collapsed() is True

    assert bench.begin_add() is True

    assert bench.section().is_collapsed() is False
    picker = bench.picker()
    assert picker is not None and picker.table == "Turrets"
    assert picker.parentWidget() in bench.section().body_widgets()


# --------------------------------------------------------------------------- #
# 二、＋ 挂武器 / ＋ 加弹种（共用行：写之前说清影响多少台车）
# --------------------------------------------------------------------------- #


def test_adding_a_weapon_inserts_a_shared_row_and_warns_how_many_units(made, instance) -> None:
    """⭐ ＋ 挂武器：插一行 TurretWeapons（Order 排到队尾）＋ 状态栏说清共用人数。"""
    context = _context(instance, SHARED_UNIT)
    bench = made(context)
    row = bench.row_for_turret(SHARED_TURRET)
    assert row is not None and row.row_id == 297

    row_id = bench.add_weapon(row, SHARED_WEAPON_NEW)

    assert row_id > 0
    links = context.data.t("TurretWeapons")
    added = links.by_id(row_id)
    assert added is not None
    assert links.i(added, "WeaponId") == SHARED_WEAPON_NEW
    assert links.i(added, "TurretId") == SHARED_TURRET
    assert links.i(added, "Order") == 1, "这座炮塔原来只有一行（Order 0）—— 新行排到队尾"
    assert links.i(added, "WeaponChannel") == 0 and links.i(added, "WeaponPriority") == 0
    status = bench.status_text()
    assert "TurretWeapons #" in status and "另外 15 个单位共用" in status
    assert any(r.weapon_id == SHARED_WEAPON_NEW for r in bench.weapon_rows())


# --------------------------------------------------------------------------- #
# 三、＋ 加弹种（带 UnitId：只动本单位这一份）
# --------------------------------------------------------------------------- #


def test_adding_an_ammo_inserts_one_row_for_this_unit_only(made, instance) -> None:
    """⭐ ＋ 加弹种：插一行 WeaponAmmunitions（UnitId=本单位、Quantity 默认 1）。"""
    context = _context(instance, VEHICLE)
    bench = made(context)
    row = next(r for r in bench.weapon_rows() if r.weapon_id == M240_WEAPON)

    row_id = bench.add_ammo(row, M240_WEAPON, 28)

    assert row_id > 0
    rows = context.data.t("WeaponAmmunitions")
    added = rows.by_id(row_id)
    assert added is not None
    assert rows.i(added, "UnitId") == VEHICLE and rows.i(added, "WeaponId") == M240_WEAPON
    assert rows.i(added, "AmmunitionId") == 28
    assert rows.i(added, "Quantity") == 1, "默认 ×1（加完点「×数量」就地改）"
    assert rows.i(added, "Order") == 1
    assert "默认 ×1" in bench.status_text()
    assert any(item.ammo_id == 28 for item in bench.ammo_rows())


def test_adding_an_ammo_that_is_already_mounted_is_refused(made, instance) -> None:
    """这门枪已经挂着同一种弹 -> 拒绝（点「×数量」改数量就行），一行都不插。"""
    context = _context(instance, VEHICLE)
    bench = made(context)
    row = next(r for r in bench.weapon_rows() if r.weapon_id == M240_WEAPON)
    rows = context.data.t("WeaponAmmunitions")
    before = len(rows.where2("UnitId", VEHICLE, "WeaponId", M240_WEAPON))

    assert bench.add_ammo(row, M240_WEAPON, M240_AMMO) == 0

    after = len(rows.where2("UnitId", VEHICLE, "WeaponId", M240_WEAPON))
    assert after == before
    assert "已经挂着" in bench.status_text()


# --------------------------------------------------------------------------- #
# 四、改携弹量（点「×数量」就地编辑）
# --------------------------------------------------------------------------- #


def test_clicking_the_quantity_opens_the_inline_editor_in_that_cell(made, instance) -> None:
    """点「×400」-> 就在那一格上开浮层，而且**不算选中这一行**（那一下被吃掉了）。"""
    bench = made(_context(instance, VEHICLE))
    bench.section().set_collapsed(False)
    ammo_row = _m240_ammo_row(bench)

    QTest.mouseClick(ammo_row.value_label(), Qt.MouseButton.LeftButton)

    assert bench.has_editor() and bench.open_ammo_row() is ammo_row
    assert ammo_row.has_editor
    assert bench.editor() is not None and bench.editor().parent() is ammo_row
    assert bench.selected_row_id() == 0, "点数量只是改值，别顺带把它选中"


def test_typing_a_new_quantity_writes_it_through_the_context(made, instance) -> None:
    """回车提交 -> set_value(WeaponAmmunitions#1193.Quantity)，行跟着变成 ×999。"""
    context = _context(instance, VEHICLE)
    bench = made(context)
    bench.section().set_collapsed(False)
    ammo_row = _m240_ammo_row(bench)
    QTest.mouseClick(ammo_row.value_label(), Qt.MouseButton.LeftButton)
    editor = bench.editor()
    assert editor is not None

    editor.input_widget.setText("999")
    QTest.keyClick(editor.input_widget, Qt.Key.Key_Return)
    settle()

    assert context.changeset.value("WeaponAmmunitions", M240_AMMO_ROW, "Quantity") == 999
    assert _m240_ammo_row(bench).value_label().text() == "×999"
    assert not bench.has_editor(), "改完要收工"
    assert "已改携弹量" in bench.status_text()
    assert "×400 → ×999" in bench.status_text()


def test_a_bad_quantity_is_not_written_and_the_status_bar_says_so(made, instance) -> None:
    """非法输入**不写库**：缓存里连一条改动都不该有，状态栏给一句人话（不弹窗）。"""
    context = _context(instance, VEHICLE)
    bench = made(context)
    bench.section().set_collapsed(False)
    ammo_row = _m240_ammo_row(bench)
    QTest.mouseClick(ammo_row.value_label(), Qt.MouseButton.LeftButton)
    editor = bench.editor()
    assert editor is not None

    editor.input_widget.setText("abc")
    QTest.keyClick(editor.input_widget, Qt.Key.Key_Return)
    settle()

    assert context.changeset.change_for("WeaponAmmunitions", M240_AMMO_ROW, "Quantity") is None
    assert "携弹量没改" in bench.status_text() and "值没写进去" in bench.status_text()


# --------------------------------------------------------------------------- #
# 五、取数：行上那串槽号（"不在挂载槽上" / TurretN）
# --------------------------------------------------------------------------- #


def test_a_turret_the_option_does_not_point_at_shows_no_slot_text(made, instance) -> None:
    """⭐ 当前选项**没有**指着它 -> 槽号是空串（行上仍是"不在挂载槽上 · 模型名"那句灰字）。

    单位 21 的槽 0 只指着 522；名册里另外 4 座都是"本单位有、当前选项没选"的那一类。
    """
    bench = made(_context(instance, VEHICLE))
    bench.section().set_collapsed(False)
    loose = bench.row_for_turret(18)
    assert loose is not None

    assert loose.line.slot_text == ""
    assert loose.line.detail_text.startswith("不在挂载槽上")
    assert "Turret" not in loose.line.detail_text


def test_clicking_the_add_buttons_opens_the_right_candidates(made, instance) -> None:
    """＋ 挂武器 / ＋ 加弹种：两颗 ＋ 各自冒**不同的**候选（武器整表 vs 武器×弹种）。"""
    bench = made(_context(instance, VEHICLE))
    bench.section().set_collapsed(False)
    turret_row = bench.row_for_turret(M240_TURRET)
    assert turret_row is not None

    QTest.mouseClick(turret_row.add_weapon_button(), Qt.MouseButton.LeftButton)
    weapon_picker = bench.picker()
    assert weapon_picker is not None and weapon_picker.table == "Weapons"
    assert weapon_picker.second_table is None
    bench.close_picker()

    weapon_row = next(r for r in bench.weapon_rows() if r.weapon_id == M240_WEAPON)
    QTest.mouseClick(weapon_row.add_ammo_button(), Qt.MouseButton.LeftButton)
    ammo_picker = bench.picker()
    assert ammo_picker is not None and ammo_picker.table == "Weapons"
    assert ammo_picker.second_table == "Ammunitions"
    assert ammo_picker.first_locked is True, "左列钉死这一门武器，只让人挑弹种"


def test_picking_an_ammo_from_the_pair_picker_inserts_the_row(made, instance) -> None:
    """＋ 加弹种：双列候选里点**第二列**那一条 -> 插一行 WeaponAmmunitions（默认 ×1）。"""
    context = _context(instance, VEHICLE)
    bench = made(context)
    bench.section().set_collapsed(False)
    weapon_row = next(r for r in bench.weapon_rows() if r.weapon_id == M240_WEAPON)
    QTest.mouseClick(weapon_row.add_ammo_button(), Qt.MouseButton.LeftButton)
    picker = bench.picker()
    assert picker is not None and picker.first_locked is True

    index = next(i for i in range(picker.second_count) if picker.second_row_id_at(i) != M240_AMMO)
    ammo_id = picker.second_row_id_at(index)
    picker.pick_second(index)  # 与"点了第二列那一条弹"同一条路

    rows = context.data.t("WeaponAmmunitions")
    added = [
        item
        for item in rows.where2("UnitId", VEHICLE, "WeaponId", M240_WEAPON)
        if rows.i(item, "AmmunitionId") == ammo_id
    ]
    assert added, f"点了弹种 {ammo_id}，WeaponAmmunitions 里没有新行"
    assert rows.i(added[0], "Quantity") == 1
    assert "默认 ×1" in bench.status_text()


# --------------------------------------------------------------------------- #
# 六、"改"的新落点：点一行 → 右栏列出**那一行的全部列** → 直接改那一格
#
# 用户 2026-10-02：「改顺序我还是直接改 order 吧，不用那三个按钮了」＋
#                「换…单独做一个按钮，非常多余」。
# ⇒ ① 不再提供任何"换 / 排序"按钮；那两件事都在**右栏列面**做：BenchRail 的列视图里
#    每一格都是可点的值（ref 来自 FieldCell），点开就地编辑，落库走 EditorContext.set_value。
# 这一节钉的就是"真的改得动、真的写进缓存" —— 不是只画了个只读标签。
# --------------------------------------------------------------------------- #


def _open_rail_for(window, table: str, row_id: int):
    """进编成台（载具 21）-> 右栏换成这一行 -> 切到列视图。返回那一页。"""
    window._open_unit_editor(VEHICLE)
    page = window._unit_editor.bench_page()
    page._on_row_selected(table, row_id)
    page.rail.set_mode(MODE_COLUMNS)
    return page


def test_every_column_of_an_association_row_is_editable_in_the_rail(window) -> None:
    """⭐ 关联行在右栏**一列都不是死的**（TurretUnits 4 列 / TurretWeapons 6 列）。

    这一条是"拆按钮"能成立的前提：右栏若对关联行只给看不给改，用户就没有任何地方能改
    Order / 外键了 —— 那种情况必须先报 Lead，不许自己再加按钮。
    """
    page = _open_rail_for(window, "TurretUnits", M240_ROW)
    assert tuple(page.rail.row_texts()) and len(page.rail.editable_labels()) == 4, (
        "TurretUnits 4 列（Id / UnitId / TurretId / Order），一列都不许只读"
    )
    assert {"行 ID（主键）", "单位 ID", "炮塔 ID", "装填顺序"} <= set(page.rail.editable_labels())

    page._on_row_selected("TurretWeapons", M240_WEAPON_ROW)
    assert len(page.rail.editable_labels()) == 6, "TurretWeapons 6 列，一列都不许只读"
    assert {"武器 ID", "武器顺序", "武器通道（按挂载点覆盖）"} <= set(page.rail.editable_labels())

    page._on_row_selected("WeaponAmmunitions", M240_AMMO_ROW)
    assert len(page.rail.editable_labels()) == 6, "WeaponAmmunitions 6 列，一列都不许只读"
    assert {"弹药 ID", "弹种顺序", "携弹量"} <= set(page.rail.editable_labels())


def test_the_turret_rows_order_can_be_edited_in_the_rail(window) -> None:
    """⭐ TurretUnits.Order：右栏那一格点开、回车 -> 真的写进缓存（用户要的"直接改 order"）。"""
    page = _open_rail_for(window, "TurretUnits", M240_ROW)

    target = page.rail.value_widget("装填顺序")
    assert target is not None and isinstance(target, QPushButton), "值那一格要是可点的按钮"
    QTest.mouseClick(target, Qt.LeftButton, pos=target.rect().center())
    assert page.rail.has_editor(), "点值之后要就地冒出编辑器"

    box = page.rail.editor().findChild(QLineEdit)
    assert box is not None, "整数列该给一个输入框"
    box.selectAll()
    QTest.keyClicks(box, "3")
    QTest.keyClick(box, Qt.Key_Return)

    assert page.context.changeset.value("TurretUnits", M240_ROW, "Order") == 3, (
        "TurretUnits#763.Order 没写进缓存"
    )
    assert not page.rail.has_editor(), "提交之后浮层要收起来"


def test_the_weapon_rows_order_can_be_edited_in_the_rail(window) -> None:
    """⭐ TurretWeapons.Order 同理：武器行的顺序也在右栏那一格直接改。"""
    page = _open_rail_for(window, "TurretWeapons", M240_WEAPON_ROW)

    target = page.rail.value_widget("武器顺序")
    assert target is not None and isinstance(target, QPushButton)
    QTest.mouseClick(target, Qt.LeftButton, pos=target.rect().center())

    box = page.rail.editor().findChild(QLineEdit)
    assert box is not None
    box.selectAll()
    QTest.keyClicks(box, "2")
    QTest.keyClick(box, Qt.Key_Return)

    assert page.context.changeset.value("TurretWeapons", M240_WEAPON_ROW, "Order") == 2, (
        "TurretWeapons#823.Order 没写进缓存"
    )


def test_the_turret_rows_foreign_key_can_be_edited_in_the_rail(window) -> None:
    """⭐ TurretUnits.TurretId（外键那一格）：点开是**可挑行的下拉**，改完写进缓存。

    这就是"换"的等价物（用户只反对单独做一颗换按钮，不反对改列值）。
    """
    page = _open_rail_for(window, "TurretUnits", M240_ROW)

    target = page.rail.value_widget("炮塔 ID")
    assert target is not None and isinstance(target, QPushButton)
    QTest.mouseClick(target, Qt.LeftButton, pos=target.rect().center())
    editor = page.rail.editor()
    assert editor is not None
    assert editor.findChild(QComboBox) is not None, "外键那一格该是个可挑行的下拉"

    cell = next(
        item
        for item in page.context.cells_of_row("TurretUnits", M240_ROW)
        if item.column == "TurretId"
    )
    page._commit_rail(cell.ref, BUSHMASTER2_TURRET)
    written = page.context.changeset.value("TurretUnits", M240_ROW, "TurretId")
    assert written == BUSHMASTER2_TURRET, "TurretUnits#763.TurretId 没写进缓存"


# --------------------------------------------------------------------------- #
# 七、每座炮塔自己可折叠（用户 2026-10-02：「炮塔我也需要能折叠」）
#
# 口径：记号 ▸/▾ 长在炮塔行**最左边**（role 复用 bench-section-caret，单占一格）；
# **"折"的手势全页只有一条**（用户 2026-10-02：「我不要点三角形展开，你的设计能不能统一一下？」）：
# 点**名字那一段**（▸ + 🗼名字）= 折这一座；点这一行**其余部分** = 选中这一行（两者不许抢事件）；
# **默认收起**、按 turret_id 跨 refresh / 换单位记住；只 hide 不销毁（AGENTS §0.7）、
# **一个字节都不写库**；武器/弹药层不再单独折。
# --------------------------------------------------------------------------- #


def test_every_turret_starts_folded_and_its_weapons_are_hidden(made, instance) -> None:
    """① 默认每座都收起：武器/弹药行不可见，行上的「N 件武器 · M 条弹」照旧看得见。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)

    rows = bench.turret_rows()
    assert rows and all(row.is_folded() for row in rows)
    assert all(row.caret_label().text() == "▸" for row in rows)
    assert bench.weapon_rows() and all(not w.isVisibleTo(bench) for w in bench.weapon_rows())
    assert bench.ammo_rows() and all(not a.isVisibleTo(bench) for a in bench.ammo_rows())
    assert bench.open_turrets() == frozenset()
    assert "件武器" in rows[0].value_label().text(), "收起时也要看得见里面有多少"


def test_clicking_the_caret_folds_one_turret_and_reuses_the_widgets(made, instance) -> None:
    """② 点记号展开、再点收起 —— **控件是同一批**（只 hide/show，不重建）。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    row = bench.row_for_turret(FOLD_TURRET)
    assert row is not None
    before = [id(w) for w in bench.rows()]
    weapons = [w for w in bench.weapon_rows() if w.source_turret_id == FOLD_TURRET]
    ammo = [a for a in bench.ammo_rows() if a.source_turret_id == FOLD_TURRET]
    assert len(weapons) == 3 and len(ammo) == 4, "样本变了：Turrets#15 该有 3 门武器 + 4 条弹"

    QTest.mouseClick(row.caret_label(), Qt.MouseButton.LeftButton)
    assert row.is_folded() is False and row.caret_label().text() == "▾"
    # ⚠️ **2026-10-03 起口径变了**：展开这一座只放出**武器行** —— 弹行要再展开那门武器
    #    （用户：「像是这样的武器，也要有个折叠栏」＋「所有的折叠栏，都默认折叠状态」）。
    #    旧断言是"武器 + 弹药行都露出来"，逐字见 git 里这一条的上一个版本。
    assert all(w.isVisibleTo(bench) for w in weapons), "展开之后武器行要露出来"
    assert all(not a.isVisibleTo(bench) for a in ammo), "武器还收着，弹行不该露"

    QTest.mouseClick(row.caret_label(), Qt.MouseButton.LeftButton)
    assert row.is_folded() is True and row.caret_label().text() == "▸"
    assert all(not w.isVisibleTo(bench) for w in weapons), "再点一下该收回去"
    assert [id(w) for w in bench.rows()] == before, "折叠不许重建控件（AGENTS §0.7）"


def test_clicking_the_row_body_still_selects_it_and_the_caret_stays_out(made, instance) -> None:
    """③ 点行体 = 选中这一行；点记号 = 折这一座 —— 两者不许互相抢事件。"""
    context = _context(instance, FOLD_UNIT_A)
    bench = made(context)
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    row = bench.row_for_turret(FOLD_TURRET)
    assert row is not None
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert seen and seen[-1] == ("TurretUnits", row.row_id), "点行体该照旧选中这一行"
    assert row.is_folded() is True, "点行体**不许**顺手把它折了"

    seen.clear()
    QTest.mouseClick(row.caret_label(), Qt.MouseButton.LeftButton)
    assert seen == [], "点记号只折这一座，不许把这一行也选中"
    assert row.is_folded() is False

    # 再直接给记号造一对真事件：release 必须被它**接受**（不接受就会冒给父行 —— 那正是
    # "点一下记号又折又选中"的根因）。这一条是上面那句 seen == [] 的非空证明。
    center = QPointF(row.caret_label().rect().center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        center,
        center,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()
    row.caret_label().mouseReleaseEvent(event)
    assert event.isAccepted(), "记号没吃掉这一下 —— 父行会跟着再发一次 activated"
    assert row.is_folded() is True, "这一下该把这一座收回去（而且只有记号收到它）"


# --------------------------------------------------------------------------- #
# 七之二、每门武器自己也可折叠（用户 2026-10-03：「像是这样的武器，也要有个折叠栏」
#         ＋「所有的折叠栏，都默认折叠状态」）
#
# 口径与炮塔那层逐字相同：记号在最左边、点名字也折、**默认收起**、按 (源炮塔, 武器)
# 跨 refresh / 换单位记住、只 hide 不销毁、一个字节都不写库。
# 差别只有一处：**弹行收在武器下面**（炮塔那层只管到武器行）。
# --------------------------------------------------------------------------- #


def test_every_weapon_starts_folded_and_its_ammo_stays_hidden(made, instance) -> None:
    """① 炮塔展开之后，武器行露面、**它下面的弹行仍收着**（两层各管各的）。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    turret = bench.row_for_turret(FOLD_TURRET)
    assert turret is not None
    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)

    weapons = [w for w in bench.weapon_rows() if w.source_turret_id == FOLD_TURRET]
    ammo = [a for a in bench.ammo_rows() if a.source_turret_id == FOLD_TURRET]
    assert len(weapons) == 3 and len(ammo) == 4, "样本变了：Turrets#15 该有 3 门武器 + 4 条弹"
    assert all(w.isVisibleTo(bench) for w in weapons), "炮塔展开了，武器行该露面"
    assert all(w.is_folded() for w in weapons), "武器行默认收起"
    assert all(w.caret_label().text() == "▸" for w in weapons)
    assert all(not a.isVisibleTo(bench) for a in ammo), "武器收着，它下面的弹行不许露出来"
    assert bench.open_weapons() == frozenset()
    assert "条弹药" in weapons[0].value_label().text(), "收起时也要看得见里面有几条"


def test_clicking_the_weapon_caret_folds_its_ammo_and_reuses_the_widgets(made, instance) -> None:
    """② 点武器那颗记号：放 / 收它下面的弹行 —— **控件是同一批**（只 hide/show）。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)
    turret = bench.row_for_turret(FOLD_TURRET)
    assert turret is not None
    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)

    # 挑一门**真的有弹**的武器（按 viewmodel 那棵树拿它的弹 id）。
    line = next(t for t in bench.tree().turrets if t.turret_id == FOLD_TURRET)
    weapon_line = next(w for w in line.weapons if w.ammo)
    weapon = next(
        w
        for w in bench.weapon_rows()
        if (w.source_turret_id, w.weapon_id)
        == (weapon_line.source_turret_id, weapon_line.weapon_id)
    )
    mine_ids = {a.ammo_id for a in weapon_line.ammo}
    mine = [
        a for a in bench.ammo_rows() if a.ammo_id in mine_ids and a.source_turret_id == FOLD_TURRET
    ]
    assert mine, "这门武器该有弹行"
    before = [id(w) for w in bench.rows()]

    QTest.mouseClick(weapon.caret_label(), Qt.MouseButton.LeftButton)
    assert weapon.is_folded() is False and weapon.caret_label().text() == "▾"
    assert all(a.isVisibleTo(bench) for a in mine), "展开之后这门武器的弹行要露出来"
    key = (weapon_line.source_turret_id, weapon_line.weapon_id)
    assert bench.open_weapons() == frozenset({key})

    QTest.mouseClick(weapon.caret_label(), Qt.MouseButton.LeftButton)
    assert weapon.is_folded() is True and weapon.caret_label().text() == "▸"
    assert all(not a.isVisibleTo(bench) for a in mine), "再点一下该收回去"
    assert [id(w) for w in bench.rows()] == before, "折叠不许重建控件（AGENTS §0.7）"


def test_folding_the_turret_hides_an_open_weapons_ammo_too(made, instance) -> None:
    """③ 三层互不覆盖：武器开着时把炮塔收起来，弹行也得跟着藏（不是只看武器那一层）。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)
    turret = bench.row_for_turret(FOLD_TURRET)
    assert turret is not None
    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)
    line = next(t for t in bench.tree().turrets if t.turret_id == FOLD_TURRET)
    weapon_line = next(w for w in line.weapons if w.ammo)
    weapon = next(
        w
        for w in bench.weapon_rows()
        if (w.source_turret_id, w.weapon_id)
        == (weapon_line.source_turret_id, weapon_line.weapon_id)
    )
    QTest.mouseClick(weapon.caret_label(), Qt.MouseButton.LeftButton)
    mine_ids = {a.ammo_id for a in weapon_line.ammo}
    mine = [a for a in bench.ammo_rows() if a.ammo_id in mine_ids]
    assert all(a.isVisibleTo(bench) for a in mine)

    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)  # 收起这一座
    assert all(not a.isVisibleTo(bench) for a in mine), "炮塔收着，弹行不许露"
    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)  # 再展开
    assert all(a.isVisibleTo(bench) for a in mine), "武器那层还开着，弹行该原样回来"


def test_clicking_the_weapon_name_folds_it_instead_of_selecting(made, instance) -> None:
    """④ 点**武器名字那一段** = 折这一门（与炮塔行同一条口径），且不顺手选中这一行。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)
    turret = bench.row_for_turret(FOLD_TURRET)
    assert turret is not None
    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)
    weapon = next(w for w in bench.weapon_rows() if w.source_turret_id == FOLD_TURRET)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    label = weapon.name_label()
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    assert weapon.is_folded() is False, "点名字没折动"
    assert seen == [], "点名字只折这一门，不许把这一行也选中"
    QTest.mouseClick(label, Qt.MouseButton.LeftButton)
    assert weapon.is_folded() is True, "再点一下名字该收回去"


#: 单位 46（Ka-52）新挂这座炮塔：它的武器在本单位原本没有弹。
KA52_NEW_TURRET = 1216
#: 单位 46 已经有弹的那座（PylonTurret S8 Heli #155）—— 反例样本。
SHARED_TURRET_KA52 = 155


def test_adding_a_turret_fills_the_most_common_ammo(made, instance) -> None:
    """⭐ 用户 2026-10-02：「我希望我在加新炮塔时，**能自动为它的武器加上最常用的弹药**」。

    病灶：炮塔的武器来自模板（TurretWeapons），而弹是按 (单位, 武器) 记的
    （WeaponAmmunitions 带 UnitId）—— 新挂的炮塔在本单位一条弹都没有，进游戏就是空炮。

    判据三条：① 新挂的炮塔那门武器**多了一条**弹，弹种与数量 = 全库最常用的那一对；
    ② 与炮塔行**同一笔**（一次 Ctrl+Z 整组回来）；③ 已经有弹的武器**一个字节都不动**。
    """
    context = _context(instance, KA52)
    bench = made(context)
    data = context.data
    fills = vm.ammo_fills_for_turret(data, KA52, KA52_NEW_TURRET)
    assert fills, "样本变了：这座炮塔该缺弹（库里有常用弹、本单位还没有）"
    fill = fills[0]
    assert vm.common_ammo(data, fill.weapon_id) == (fill.ammo_id, fill.quantity)

    before_rows = len(data.t("WeaponAmmunitions").where("UnitId", KA52))
    entries = context.changeset.count
    bench.add_turret(KA52_NEW_TURRET)

    added = data.t("WeaponAmmunitions").where2("UnitId", KA52, "WeaponId", fill.weapon_id)
    assert added, "弹没补上（新挂的炮塔在本单位还是空炮）"
    rows = data.t("WeaponAmmunitions")
    assert rows.i(added[0], "AmmunitionId") == fill.ammo_id, "补的不是最常用的那个弹种"
    assert rows.i(added[0], "Quantity") == fill.quantity, "数量不是全库最常用的那个"
    assert len(data.t("WeaponAmmunitions").where("UnitId", KA52)) == before_rows + 1
    assert context.changeset.count > entries  # 值确实写进去了（count 数的是"改了几格"）
    # ❗ 炮塔行 ＋ 自动补的弹在**同一笔**撤销里（insert_group 的口径）：连着撤两次
    #    （第 ① 次是 Options.TurretNId 那一格）之后，两条行都该不见 —— 分成两笔的话这里会剩一半。
    assert "撤销" in context.undo_last()  # ① 槽那一格
    assert "撤销" in context.undo_last()  # ② 炮塔 ＋ 自动补的弹（一笔）
    assert not data.t("TurretUnits").where2("UnitId", KA52, "TurretId", KA52_NEW_TURRET), (
        "炮塔行没跟着撤销"
    )
    assert not data.t("WeaponAmmunitions").where2("UnitId", KA52, "WeaponId", fill.weapon_id), (
        "自动补的那条弹没跟着一起撤销（说明它是单独一笔）"
    )


def test_adding_a_turret_leaves_weapons_that_already_have_ammo_alone(made, instance) -> None:
    """同一口径的另一半：本单位**已经有**这门武器弹药行的，一律不动（不重复补）。"""
    context = _context(instance, KA52)
    bench = made(context)
    data = context.data
    assert vm.ammo_fills_for_turret(data, KA52, SHARED_TURRET_KA52) == (), (
        "样本变了：这座炮塔的武器在本单位该已经有弹了"
    )
    before = len(data.t("WeaponAmmunitions").where("UnitId", KA52))
    bench.add_turret(SHARED_TURRET_KA52)
    assert len(data.t("WeaponAmmunitions").where("UnitId", KA52)) == before, "多补了弹"


def test_clicking_a_slot_group_header_never_hides_the_header(made, instance) -> None:
    """⭐ 用户 2026-10-02：「**你那个折叠栏，点一下就直接消失了**」。

    病灶（已修）：分组抬头被算进了"这一组要藏起来的那批行"里 —— 点一下连抬头自己
    一起 hide，那个改装槽再也点不回来（屏幕上凭空少一行，正是用户看到的样子）。
    判据：折完**抬头还在**（可见 + 记号变 ▸），只有它下面的塔行藏了；再点一下全回来。
    """
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    heads = bench.group_rows()
    assert heads, "样本变了：这台车一个改装槽分组都没有"
    head = heads[0]
    # 这一组的塔行：直接按分组自己带的那几条线取（不碰内部索引）
    mine = [
        row
        for line in head.group.turrets
        if (row := bench.row_for_turret(line.turret_id)) is not None
    ]
    assert mine, "这一组一座塔都没有 —— 这条用例就没有鉴别力了"

    _unfold_groups(bench)
    assert all(row.isVisibleTo(bench) for row in mine)

    bench.toggle_group_fold(head)
    assert head.isVisibleTo(bench), "点一下抬头自己没了（用户报的就是这个）"
    assert head.caret_label().text() == "▸"
    assert all(not row.isVisibleTo(bench) for row in mine), "折了之后塔行该藏起来"

    bench.toggle_group_fold(head)
    assert head.isVisibleTo(bench)
    assert head.caret_label().text() == "▾"
    assert all(row.isVisibleTo(bench) for row in mine), "再点一下该回来"


#: Ka-52（单位 46）：实测 11 座塔里 **5 座**被不止一个改装槽用到（#155 被槽 75 与 76 都用到）。
KA52 = 46
KA52_SHARED_TURRET = 155


def test_a_turret_used_by_two_slots_is_listed_under_both(made, instance) -> None:
    """⭐ 用户 2026-10-02：「**既然也被使用，就给使用的那个折叠栏里也加上啊**」。

    上一版只把这座塔画在"第一个用到它的槽"下面，另一处仅灰字提一句 —— 用户当场要的是
    **两边都列出来**。判据：同一座塔在两组下各有一行控件；两组抬头自己那份 turret 名单里都有它；
    而且**两份的行 Id 是同一行**（✕ 删的是同一份 TurretUnits 行，不是两行）。
    """
    bench = made(_context(instance, KA52))
    heads = [row for row in bench.group_rows() if row.modification_id]
    holding = [
        row for row in heads if any(t.turret_id == KA52_SHARED_TURRET for t in row.group.turrets)
    ]
    assert len(holding) >= 2, "样本变了：这座塔该被两个以上的槽用到"

    # ❗ 用户 2026-10-02 第二句：「**就别说也被***用了，都已经显示出来了，为什么还要这种
    #    多余的文字呢？**」—— 归属由"它画在哪一组下面"表达，行上不许再补那句灰字。
    said = " ".join(row.line.detail_text for row in bench.turret_rows_for(KA52_SHARED_TURRET))
    assert "也被" not in said and "用" not in said, f"行上还留着多余的话：{said!r}"

    rows = bench.turret_rows_for(KA52_SHARED_TURRET)
    assert len(rows) == len(holding), "每一组下面都该有自己的那一行"
    assert len({row.row_id for row in rows}) == 1, "两份画的是**同一行** TurretUnits（✕ 删同一个）"
    assert {row.group_id for row in rows} == {row.modification_id for row in holding}

    # 就地刷新要**两份都**换到新字（按槽分别找控件）
    line = rows[0].line
    rows[0].apply_line(line)
    assert rows[0].value_label().text() == line.value_text
    assert rows[-1].value_label().text() == line.value_text


def test_every_fold_bar_starts_folded(made, instance) -> None:
    """⭐ 用户 2026-10-02：「我要**所有折叠栏默认折叠**」。

    三层都要收着：① 块抬头（BenchSection）② 改装槽分组抬头 ③ 每一座炮塔。
    （块那一层由 test_bench_page 守着，这里补齐后两层 —— 分组那层是我新加的，
      第一版写成了默认展开，被用户当场抓出来。）
    """
    bench = made(_context(instance, FOLD_UNIT_A))
    assert bench.section().is_collapsed() is True, "块抬头默认收起"
    for head in bench.group_rows():
        assert head.is_folded() is True, f"{head.group.label} 那组默认没收起"
        assert bench.is_group_open(head.modification_id) is False
        assert head.caret_label().text() == "▸"
    for row in bench.turret_rows():
        assert row.is_folded() is True, "炮塔行默认收起"
        assert row.caret_label().text() == "▸"
    for row in bench.weapon_rows():
        assert row.is_folded() is True, "武器行默认收起（2026-10-03 补的第四层）"
        assert row.caret_label().text() == "▸"
    assert bench.open_weapons() == frozenset()


def test_the_fold_caret_is_a_comfortable_target(made, instance) -> None:
    """⑥ 炮塔那一格记号：**一整格都点得动**，不是"瞄准那一小撮"。

    用户 2026-10-02 实机报的就是这一颗（截图里圈出来的那个小三角）：「很不好操作」。
    实测改前 **8×14** —— 占炮塔行宽（570px）的 1.4%；点偏一两像素落到行体上，
    那一下是"选中这一行"（不是折），用户看到的就是"点了没反应"。

    判据两层：① 格子够大；② **点在格子边角**（不是正中）也折得动 —— 后者才是手感。
    """
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    row = bench.row_for_turret(FOLD_TURRET)
    assert row is not None
    caret = row.caret_label()
    assert caret.width() >= 20 and caret.height() >= 20, (
        f"折叠格太小：{caret.width()}x{caret.height()}（改前实测 8x14）"
    )

    for corner in (QPoint(2, 2), QPoint(caret.width() - 3, caret.height() - 3)):
        before = row.is_folded()
        QTest.mouseClick(caret, Qt.MouseButton.LeftButton, pos=corner)
        assert row.is_folded() is not before, f"点在 ({corner.x()}, {corner.y()}) 没折动"


def test_clicking_the_turret_name_folds_it_instead_of_selecting(made, instance) -> None:
    """③.5 点**名字那一段** = 折这一座（与"点块抬头就折"同一条规矩）。

    用户 2026-10-02：「**我不要点三角形展开，你的设计能不能统一一下？**」——
    折的入口不该只有最左那颗小三角；这一行的"抬头"就是它的名字。
    另一半同时钉死：这一下**不许**顺手把这一行选中（右栏不该跟着换）。
    """
    context = _context(instance, FOLD_UNIT_A)
    bench = made(context)
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    row = bench.row_for_turret(FOLD_TURRET)
    assert row is not None
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    assert row.is_folded() is True
    QTest.mouseClick(row.name_label(), Qt.MouseButton.LeftButton)
    assert row.is_folded() is False, "点名字没折动"
    assert seen == [], "点名字只折这一座，不许把这一行也选中（右栏不该换）"

    QTest.mouseClick(row.name_label(), Qt.MouseButton.LeftButton)
    assert row.is_folded() is True, "再点一下名字该收回去"
    assert seen == []


def test_clicking_the_icon_folds_it_too(made, instance) -> None:
    """用户 2026-10-03：「**我点图标也要能展开和折叠折叠栏**」。

    口径与"点名字 / 点记号"同一条：三个入口都能折，且这一下**不许**顺手把这一行选中
    （右栏不该跟着换）。三层都点一遍：分组抬头 → 炮塔行 → 武器行。
    """
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)
    seen: list[tuple[str, int]] = []
    bench.row_selected.connect(lambda table, row_id: seen.append((table, row_id)))

    # 炮塔行：点图标 = 展开这一座
    turret = bench.row_for_turret(FOLD_TURRET)
    assert turret is not None and turret.is_folded() is True
    QTest.mouseClick(turret.icon_label(), Qt.MouseButton.LeftButton)
    assert turret.is_folded() is False, "点炮塔图标没折动"
    assert seen == [], "点图标只折这一座，不许把这一行也选中"

    # 武器行：点图标 = 展开这门武器下面的携弹行
    weapon = next(w for w in bench.weapon_rows() if w.source_turret_id == FOLD_TURRET)
    assert weapon.is_folded() is True
    QTest.mouseClick(weapon.icon_label(), Qt.MouseButton.LeftButton)
    assert weapon.is_folded() is False, "点武器图标没折动"
    assert seen == []

    # 分组抬头：点图标 = 收起这一组（_unfold_groups 已经先把它展开了）
    head = bench.group_rows()[0]
    assert bench.is_group_open(head.modification_id) is True
    QTest.mouseClick(head.icon_label(), Qt.MouseButton.LeftButton)
    assert bench.is_group_open(head.modification_id) is False, "点分组图标没折动"
    assert seen == []


def test_the_fold_hint_is_not_a_tooltip(made, instance) -> None:
    """用户 2026-10-03：「**我不要这种在中间出现的注释浮窗**」。

    鼠标一停在名字 / 图标那一格就弹一句"点这一行 = 折 …"，正好盖住下面几行列表。
    折的入口照旧三个（记号 / 名字 / 图标），认得出靠 PointingHandCursor —— 不靠浮窗。
    """
    bench = made(_context(instance, FOLD_UNIT_A))
    for row in (*bench.group_rows(), *bench.turret_rows(), *bench.weapon_rows()):
        name = type(row).__name__
        assert row.name_label().toolTip() == "", f"{name} 的名字上还挂着提示浮窗"
        assert row.icon_label().toolTip() == "", f"{name} 的图标上还挂着提示浮窗"
        assert row.name_label().cursor().shape() == Qt.PointingHandCursor, (
            f"{name} 的名字没有手型光标"
        )


def test_the_open_state_is_remembered_across_refresh_and_unit_change(made, instance) -> None:
    """④ 展开态按 turret_id 记住：refresh 之后、换到另一台用车（387）之后都还开着。"""
    bench = made(_context(instance, FOLD_UNIT_A))
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    row = bench.row_for_turret(FOLD_TURRET)
    assert row is not None and row.is_folded() is True
    QTest.mouseClick(row.caret_label(), Qt.MouseButton.LeftButton)
    assert bench.open_turrets() == frozenset({FOLD_TURRET})

    bench.refresh()
    again = bench.row_for_turret(FOLD_TURRET)
    assert again is not None and again.is_folded() is False, "refresh 之后又被收回去"
    mine = [w for w in bench.weapon_rows() if w.source_turret_id == FOLD_TURRET]
    assert mine and all(w.isVisibleTo(bench) for w in mine)

    bench.set_context(_context(instance, FOLD_UNIT_B))
    other = bench.row_for_turret(FOLD_TURRET)
    assert other is not None and other.is_folded() is False, "换单位之后就忘了（该按 turret_id 记）"


def test_folding_writes_nothing_to_the_changeset(made, instance) -> None:
    """⑤ 收起 / 展开 **一个字节都不写库**，也不往状态栏说话。"""
    context = _context(instance, FOLD_UNIT_A)
    bench = made(context)
    bench.section().set_collapsed(False)
    _unfold_groups(bench)  # 分组默认收起（2026-10-02 口径）
    row = bench.row_for_turret(FOLD_TURRET)
    assert row is not None

    QTest.mouseClick(row.caret_label(), Qt.MouseButton.LeftButton)
    QTest.mouseClick(row.caret_label(), Qt.MouseButton.LeftButton)

    assert not context.changeset.row_changes, "折叠写了库"
    assert bench.status_text() == "", "折叠不该往状态栏说话"
    assert row.is_folded() is True


# --------------------------------------------------------------------------- #
# 子炮塔：`Turrets.ParentTurretId` 的层级要**画出来**
#    用户 2026-10-02：「**把层级画出来吧**」。
#    实测：1055 行里 86 行有父塔 · 41 个单位在用 · **965 行是 null**（判空认 None）· 最深 1 层；
#    子塔**也都在 TurretUnits 里**（86/86）且与父同单位 ⇒ 它表达的是「**挂在哪**」。
# --------------------------------------------------------------------------- #

# ---- 没有关联行的行也照画 ✕（用户 2026-10-02）------------------------------ #
#
# 用户原话：「本来就有，那之前怎么把词条隐藏起来了，把所有没显示出来的词条显示出来」。
# 旧口径「删不掉的行不该长一个按不动的按钮」正是把 ✕ 藏起来的那个理由 —— 这一轮撤销它。

#: 反例单位：171 —— 它那 3 座炮塔**全是当前选项的槽顶出来的**（`TurretUnits` 里一行都没有）。
NO_UNIT_ROW = 171


def test_a_turret_without_a_unit_row_still_shows_the_remove_button(made, instance) -> None:
    """⭐ 没有本单位那一行的炮塔行**也照画 ✕**：点了在状态栏说清为什么删不了，一个字节都不写。"""
    context = _context(instance, NO_UNIT_ROW)
    bench = made(context)
    assert len(bench.turret_rows()) == 3, "样本要挑那 3 座全是槽顶出来的"
    assert all(row.line.unit_row_id == 0 for row in bench.turret_rows())
    assert all(row.remove_button().isHidden() is False for row in bench.turret_rows()), "✕ 不许再藏"

    row = bench.turret_rows()[0]
    assert row.add_weapon_button().isHidden() is False, "＋ 也一直在"

    QTest.mouseClick(row.remove_button(), Qt.MouseButton.LeftButton)
    assert "没得删" in bench.status_text(), bench.status_text()
    assert context.changeset.count == 0, "没得删就不许写一个字节"
    assert len(bench.turret_rows()) == 3, "点了也不该少一座"


#: 有子炮塔的样本：单位 54（M1A1 FEP）—— 名册 6 行，其中 4 行是子塔（实例 52085 实测）。
CHILD_UNIT = 54
#: (炮塔Id, 父塔Id, 层) —— 父塔在前、**它的子塔紧跟其后**（子塔之间保持名册原顺序）。
CHILD_LAYOUT = (
    (89, 0, 0),
    (90, 89, 1),
    (91, 89, 1),
    (735, 0, 0),
    (736, 735, 1),
    (737, 735, 1),
)


def test_child_turrets_are_nested_under_their_parent(instance) -> None:
    """⭐ 子炮塔排在父塔后面、算第二层，灰字里写着「↳ 挂在「X」下」。"""
    tree = vm.turret_tree(CardData(instance), CHILD_UNIT)

    assert [(line.turret_id, line.parent_id, line.depth) for line in tree.turrets] == list(
        CHILD_LAYOUT
    )
    labels = {line.turret_id: line.label for line in tree.turrets}
    for turret_id, parent_id, depth in CHILD_LAYOUT:
        line = next(item for item in tree.turrets if item.turret_id == turret_id)
        if depth == 0:
            assert line.parent_label == ""
            assert not line.detail_text.startswith("↳")
            continue
        assert line.parent_label == labels[parent_id]
        assert line.detail_text.startswith(f"↳ 挂在「{labels[parent_id]}」下 · ")


def test_a_roster_without_children_keeps_its_own_order(instance) -> None:
    """没有子炮塔的单位（21 / 206）：顺序 = 名册原顺序，**一行都不缩进**。"""
    data = CardData(instance)
    for unit_id in (VEHICLE, BTR_ZD):
        tree = vm.turret_tree(data, unit_id)
        chain = roster_turret_chain(data, unit_id)
        assert tuple(line.turret_id for line in tree.turrets) == tuple(
            item.turret_id for item in chain
        ), unit_id
        assert all(line.depth == 0 and line.parent_id == 0 for line in tree.turrets), unit_id


def test_the_indent_is_monotonic_on_screen(made, instance) -> None:
    """缩进**在屏幕上**必须单调：父级的内容永远比子级靠左，每档正好 LEVEL_INDENT。

    ⚠️ 用户 2026-10-03 报的正是这里翻车：弹药行**没有折叠记号**，于是它的图标（实测 x=30）
    比父级武器行（x=48）还靠左 —— 三层看起来是倒的。修法 = 弹药行也**占**那一格记号位。
    """
    from itertools import pairwise

    from ba_mod_maker.app.widgets.bench_turrets import ICON_SLOT_WIDTH, LEVEL_INDENT

    bench = made(_context(instance, FOLD_UNIT_A))
    # ❗ **必须先给宽度**：控件不 show 时默认 640，行会被挤扁 —— 挤扁之后行内的间距由
    #    Qt 压缩，量出来的 x 就差一个 spacing（实测炮塔行的名字与灰字叠在一起）。
    #    真机那一行有 880 宽，这里照它量才对得上。
    bench.resize(880, 600)
    bench.section().set_collapsed(False)
    _unfold_groups(bench)
    turret = bench.row_for_turret(FOLD_TURRET)
    assert turret is not None
    QTest.mouseClick(turret.caret_label(), Qt.MouseButton.LeftButton)
    line = next(t for t in bench.tree().turrets if t.turret_id == FOLD_TURRET)
    weapon_line = next(w for w in line.weapons if w.ammo)
    weapon = next(
        w
        for w in bench.weapon_rows()
        if (w.source_turret_id, w.weapon_id)
        == (weapon_line.source_turret_id, weapon_line.weapon_id)
    )
    QTest.mouseClick(weapon.caret_label(), Qt.MouseButton.LeftButton)
    mine_ids = {a.ammo_id for a in weapon_line.ammo}
    ammo = next(
        a for a in bench.ammo_rows() if a.ammo_id in mine_ids and a.source_turret_id == FOLD_TURRET
    )
    settle()

    # 四层**从抬头算起**：🗂 抬头（父）→ 🗼 塔 → 🔫 武器 → 💥 弹（用户：「按树缩进」）。
    # 用**行的左边距**量档位（抬头没有图标位，用图标列会把它读成 0）。
    group_row = bench.group_rows()[0]
    rows = (group_row, turret, weapon, ammo)
    margins = [row.layout().contentsMargins().left() for row in rows]
    assert len(set(margins)) == 4, f"四层没排直：左边距 = {margins}"
    steps = [b - a for a, b in pairwise(margins)]
    assert steps == [LEVEL_INDENT] * 3, f"每一档该正好一步（{LEVEL_INDENT}）：{steps}"

    # 内容列（图标那一段）同样单调：抬头没图标位，就从塔开始量。
    icons = [turret.icon_label().x(), weapon.icon_label().x(), ammo.icon_label().x()]
    assert icons == sorted(icons) and len(set(icons)) == 3, f"内容列没排直：{icons}"

    # **图标槽三层同宽** —— 这一条正是那次"名字列往回缩"的根因守卫：
    # 改前按**各自**图标高 × 4 钉（塔 / 武器 80、弹 64）⇒ 弹药的名字反而比父级左移 4px
    # （真机实测 💥 142 vs 🔫 146，看着像兄弟）。同宽之后两列一起每层右移 LEVEL_INDENT：
    # 真机读数 = 图标 52 / 68 / 84、名字 138 / 154 / 170。
    # ⚠️ **只有有真图的行**钉同一档宽（武器 / 弹）；炮塔行没有图标源，它靠名字前那个 🗼
    # 认层 —— 给它也钉 80 会把 🗼 推到比子级还靠右（用户 2026-10-03：「你这个缩进很乱」）。
    for row in (weapon, ammo):
        assert row.icon_label().width() == ICON_SLOT_WIDTH, "有真图的两层图标槽不同宽"
        assert row.name_label().x() >= row.icon_label().x() + ICON_SLOT_WIDTH
    assert turret.icon_label().width() < ICON_SLOT_WIDTH, "没有图标源的行不该占那一格"
    # 行首标记（有图 = 图标列，没图 = 名字前那个 emoji）必须**跟着树往右**：
    marks = [turret.name_label().x(), weapon.icon_label().x(), ammo.icon_label().x()]
    assert marks == sorted(marks), f"行首标记没跟着树走：{marks}"


def test_the_nesting_is_drawn_with_the_indent(made, instance) -> None:
    """控件里**真的**缩进了：抬头 0 档、塔 1 档、子塔 2 档；子塔的武器 3 档、它的弹 4 档。"""
    bench = made(_context(instance, CHILD_UNIT))

    assert [row.turret_id for row in bench.turret_rows()] == [
        turret_id for turret_id, _parent, _depth in CHILD_LAYOUT
    ]
    # ⚠️ 2026-10-03 起**从第 1 档起算**：单位 54 的塔画在分组抬头（🗂 Armor）下面，
    #    抬头是父节点 ⇒ 塔 1 档、子塔 2 档（用户：「**按树缩进**」）。改前是不带抬头的 0 档。
    levels = {row.turret_id: row.level for row in bench.turret_rows()}
    assert levels == {89: 1, 90: 2, 91: 2, 735: 1, 736: 2, 737: 2}
    # 子塔下面那两行**再深一档**（武器 +1 / 弹药 +2）—— 不跟着深就与顶层塔的武器撞档
    assert {row.level for row in bench.weapon_rows() if row.line.source_turret_id == 90} == {3}
    assert {row.level for row in bench.ammo_rows() if row.line.source_turret_id == 90} == {4}
    assert {row.level for row in bench.weapon_rows() if row.line.source_turret_id == 89} == {2}
    assert {row.level for row in bench.ammo_rows() if row.line.source_turret_id == 89} == {3}
