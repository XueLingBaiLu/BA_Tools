"""关系树（本轮新增）的验收：分组顺序 / 三列 / 数量写在行尾 / ID 在右列 / 点一行换中栏。

口径依据：

- 任务书 T5 的十条分组顺序（用户 2026-09-30 定）；
- 用户 2026-09-29 口径：树上「有 N / 可以 N」两列**整个删掉**（那两类语义标签没有了），
  数字改成行尾灰字；弹药每一条要一眼看得见 ×N；
- 设计图 `_work/design/overhaul3.png`（左树 ｜ 中全列 ｜ 右只读卡）；
- 用户三条硬口径：ID 全部显示、不提示共用、引用（出/入）删掉。
- ⚠️ **2026-10-01 用户改口**：「左边红圈里面的 SquadMembers #* 太占空间了，能不能只在右边
  显示 #* 编号？」→ ID 从名字列挪到**右侧单独一列**（只有 `#Id`、右对齐、弱化色），
  表名只留在 tooltip 里；「第 N 名」也改成纯序号。旧口径已作废，见 `_name_text` 的 docstring。

分成两半：**模型**（零 Qt，直接断言）与**控件**（离屏建窗口，验"点一下真的换中栏"）。
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import QComboBox, QPushButton

from ba_mod_maker.app.screens.editor.entries_panel import table_title
from ba_mod_maker.app.screens.editor.relation_tree_panel import COL_ID, COL_NAME
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 载具（改装槽在最前 / 炮塔 5 种配法 / 2 组运输配对）。
STRYKER = 21
#: 步兵（班组成员 5 名 + 班用武器 5 门；没有炮塔，装甲走 UnitArmors）。
INFANTRY = 645

#: 用户定的分组顺序（任务书 T5 第 1 条）。
EXPECTED_ORDER = (
    "mods",
    "turrets",
    "ammo",
    "armor",
    "mobility",
    "sensors",
    "abilities",
    "squad",
    "specs",
    "transports",
)

#: 每一组的数字现在写在哪：**行尾灰字**（TreeItem.note）。
#: 原来是「有 N / 可以 N」两个标签（tag / tag2）占两列，用户 2026-09-29 把两列删了。
#: 这些是 Stryker RV(21) 的**实测值** —— 写死是为了"数字算错了"能被抓住。
GROUP_NOTES_STRYKER = {
    "mods": "2 个槽 · 8 个选项",
    "turrets": "5 种配法",
    "ammo": "1 种弹",
    "armor": "1 项",
    "mobility": "1 项",
    "sensors": "1 项",
    "abilities": "3 条",
    "squad": "非步兵",
    "specs": "0 个专精",
    "transports": "2 组配对",
}

#: 「有 N / 可以 N」这种标签不许再出现在树上（任何一列的文字、任何 tooltip）。
_HAVE_CAN_LABEL = re.compile(r"(?:^|\s)(?:有|可以)\s*\d")


# 夹具（qapp / instance / window）统一在 tests/conftest.py：
#   window = **一个模块一个**主窗口，每个用例只把一份干净数据接回去
#   （为什么、怎么保证隔离，见 conftest 的模块 docstring）。


def _open(window, unit_id: int):
    """进编辑器，返回 (编辑器, 主编辑页, 关系树控件)。

    ❗ 2026-10-01：双击单位现在**默认落在编成台**（步兵形态，用户口径）。
    这一份测的是**关系树那一页**，所以进来之后显式切回去 —— 不切的话
    "用户路径改了"会被读成"关系树坏了"。
    """
    window._open_unit_editor(unit_id)
    window._unit_editor.show_main()
    page = window._unit_editor.main_page
    return window._unit_editor, page, page.tree


# --------------------------------------------------------------------------- #
# 分组：顺序 / 标题 / 两类标签
# --------------------------------------------------------------------------- #


def test_group_order_is_the_one_the_user_defined(instance) -> None:
    """改装槽**放最前**（它决定别的块生效哪一套），再到炮塔 / 弹药 / …（十条，一条不少）。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    for unit_id in (STRYKER, INFANTRY):
        tree = build_relation_tree(instance, unit_id)
        assert tree is not None
        assert tuple(group.key for group in tree.groups) == EXPECTED_ORDER, unit_id


def test_the_two_count_columns_are_gone(instance, window) -> None:
    """⭐ 用户口径："把「有 *」和「可以 *」去掉" —— 两列**整个删掉**。

    这里验的是**结构上的保证**（不只是"看着没画"）：
    1. QTreeWidget 只剩两列（名字 / 工具）；
    2. TreeItem 上不再有 tag / tag2 / tag_kind / tag2_kind 那套字段；
    3. 控件模块里不再有 COL_HAVE / COL_CAN 常量；
    4. 树上画出来的每一格文字、每一条 tooltip 里都没有「有 5 / 可以 3」这种标签。
    """
    from ba_mod_maker.app.screens.editor import relation_tree_panel as panel_module
    from ba_mod_maker.app.viewmodel.relation_tree import TreeItem, build_relation_tree

    fields = set(TreeItem.__dataclass_fields__)
    for gone in ("tag", "tag2", "tag_kind", "tag2_kind"):
        assert gone not in fields, f"TreeItem 上还留着 {gone}"
    for gone in ("COL_HAVE", "COL_CAN"):
        assert not hasattr(panel_module, gone), f"控件里还留着 {gone}"

    _editor, _page, tree = _open(window, STRYKER)
    columns = tree.widget.columnCount()
    # 名字 ｜ #Id ｜ 工具（有 / 可以 两列已整列删掉；#Id 是 2026-10-01 新加的）。
    assert columns == 3, f"树不是三列（{columns} 列）"
    for key in tree.visible_keys():
        item = tree.item(key)
        for column in range(columns):
            text = item.text(column)
            assert not _HAVE_CAN_LABEL.search(text), f"树上还有「有 N / 可以 N」：{text}"
            assert not _HAVE_CAN_LABEL.search(item.toolTip(column)), item.toolTip(column)

    # 模型那一侧同样不许有（别只在控件里没画）。
    for unit_id in (STRYKER, INFANTRY):
        model = build_relation_tree(instance, unit_id)
        assert model is not None
        for node in model.flatten():
            assert not _HAVE_CAN_LABEL.search(node.display), node.display
            assert not _HAVE_CAN_LABEL.search(node.hint), node.hint


def test_group_counts_are_written_in_the_row_tail(instance) -> None:
    """【信息不许丢】原来「有 N / 可以 N」里的数，现在写在各组的行尾灰字（note）里。

    note 是肉眼看得见的那串灰字（不要只塞进 tooltip —— 用户看不到就得悬停才有）。
    """
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    for key, expected in GROUP_NOTES_STRYKER.items():
        group = tree.group(key)
        assert group is not None, key
        assert group.note == expected, (key, group.note, expected)
    # 数就是子节点的个数（"5 种配法" = 5 个炮塔节点），别写成另一个数。
    turrets = tree.group("turrets")
    assert turrets.note == f"{len(turrets.children)} 种配法"
    ammo = tree.group("ammo")
    assert ammo.note == f"{len(ammo.children)} 种弹"
    # tooltip 里不许再说已经不存在的「绿=有、金=可以」，也不许再冒出「有 N / 可以 N」。
    for group in tree.groups:
        assert "绿=" not in group.hint and "金=" not in group.hint, group.key
        assert not _HAVE_CAN_LABEL.search(group.hint), (group.key, group.hint)


def test_infantry_has_squad_but_no_turrets(instance) -> None:
    """步兵：班组成员 / 班用武器两样都有；炮塔那一组只剩一句**看得见**的说明。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, INFANTRY)
    assert tree is not None
    squad = tree.group("squad")
    # 原来这里是「有 5」「枪 5」两个标签 —— 现在同一句话写进行尾灰字。
    assert squad.note == "5 名成员 · 5 门班用武器"
    kinds = {child.table for child in squad.children}
    assert kinds == {"SquadMembers", "SquadWeapons"}

    turrets = tree.group("turrets")
    note_rows = [child for child in turrets.children if child.kind == "note"]
    assert note_rows and "没有炮塔" in note_rows[0].label, "说明得写在树上（tooltip 不算）"
    assert "没有炮塔" in turrets.hint
    assert not [child for child in turrets.children if child.has_row]

    # 载具反过来：有炮塔、没有班组成员。
    vehicle = build_relation_tree(instance, STRYKER)
    assert vehicle is not None
    assert vehicle.group("turrets").note == "5 种配法"
    assert len(vehicle.group("turrets").children) == 5
    # 「非步兵」这条信息**肉眼看得见**（不只是 tooltip）：它决定这一组为什么是空的。
    squad_vehicle = vehicle.group("squad")
    assert squad_vehicle.note == "非步兵" and "非步兵" in squad_vehicle.hint
    assert squad_vehicle.children == ()


def test_turret_rows_mark_the_active_one(instance) -> None:
    """● 标当前生效的那一种配法（数据来源 = CardData.card_turrets，选项覆盖之后）。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree
    from ba_mod_maker.app.viewmodel.unit_card import CardData

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    active = {turret_id for turret_id in CardData(instance).card_turrets(STRYKER) if turret_id > 0}
    marked = {child.row_id for child in tree.group("turrets").children if child.selected}
    assert marked == active == {522}, "M240 塔（TurretUnits 里缺省选中的那一种）"


def test_modification_options_mark_the_selected_one(instance) -> None:
    """改装槽底下的选项：槽内互斥，**每个槽恰好一项**带 ●（Options.IsDefault）。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    slots = [child for child in tree.group("mods").children if child.has_row]
    assert len(slots) == 2, "Stryker RV 有 2 个改装槽"
    for slot in slots:
        chosen = [option for option in slot.children if option.selected]
        assert len(chosen) == 1, f"{slot.label} 里选中的项不是恰好一条：{chosen}"
        assert chosen[0].table == "Options"


def test_the_option_marked_selected_is_the_one_the_data_says(instance) -> None:
    """● 的口径 = 反编译出来的 GetDefaultOptions：**第一条 IsDefault ?? 第一条 Order==0**。

    这里**自己拿原始行算一遍**（不调 `default_option` / `slot_selected_option`）——
    验的是"树上那个 ● 与数据一致"，而不是"实现里两次调用一致"。

    借来的槽（变体路径）**不在**这条口径里：那时选中的是"指向本单位的那个选项"
    （见 `_variant_option_groups`），所以只对**本单位的槽**断言（645 借 108 的槽会被跳过）。
    """
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree
    from ba_mod_maker.app.viewmodel.unit_card import CardData

    options = instance.table("Options")
    mid_index = options.column_index("ModificationId")
    id_index = options.column_index("Id")
    flag_index = options.column_index("IsDefault")
    order_index = options.column_index("Order")
    mods = instance.table("Modifications")
    mod_unit_index = mods.column_index("UnitId")
    mod_id_index = mods.column_index("Id")

    def expected_option(modification_id: int) -> int:
        rows = [row for row in options.rows if row[mid_index] == modification_id]
        picked = next((row for row in rows if row[flag_index] is True), None)
        if picked is None:
            picked = next((row for row in rows if row[order_index] == 0), None)
        return picked[id_index] if picked is not None else 0

    data = CardData(instance)
    checked = 0
    for unit_id in (STRYKER, 287, 645, 1):
        # 变体选项所在的那个槽：选中它的是"指向本单位的选项"，不是本槽的缺省项。
        borrowed = {row[mid_index] for row in data.variant_options(unit_id)}
        tree = build_relation_tree(instance, unit_id)
        assert tree is not None
        for slot in tree.group("mods").children:
            own = next((row for row in mods.rows if row[mod_id_index] == slot.row_id), None)
            if own is None or own[mod_unit_index] != unit_id or slot.row_id in borrowed:
                continue  # 借来的槽：选中的是变体选项，不是本槽的缺省项
            marked = [option.row_id for option in slot.children if option.selected]
            want = expected_option(slot.row_id)
            assert marked == ([want] if want else []), (unit_id, slot.row_id, marked, want)
            checked += 1
    assert checked >= 3, f"只扫到 {checked} 个本单位的改装槽 —— 用例没真的跑起来"


#: F-15E（Id 287）的 `Custom_Slot_Fuselage`（Modifications #307）：实测 8 条选项，
#: 最富的那条（Options #899）占 **12 个**挂载槽（7..18），只涉及 2 座炮塔
#: （160 与 533）—— "同一座炮塔占好几个槽"就是这个形状。
F15E = 287
FUSELAGE_SLOT = 307
RICH_OPTION = 899


def test_every_turret_slot_of_an_option_is_listed(instance) -> None:
    """⭐ 选项下面把 21 个炮塔挂载槽**全列出来**：非零的一行不落，一个都不折叠。

    独立算一遍：直接读 `Options.Turret0Id..Turret20Id`（不调被测的那个函数），
    再与树上逐槽比对；**同一座炮塔占多个槽时每个槽都要有自己的行**（不去重）。
    """
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    options = instance.table("Options")
    id_index = options.column_index("Id")
    slot_indexes = [options.column_index(f"Turret{i}Id") for i in range(21)]

    def expected(option_id: int) -> tuple[tuple[int, int], ...]:
        row = next(row for row in options.rows if row[id_index] == option_id)
        return tuple((slot, row[index]) for slot, index in enumerate(slot_indexes) if row[index])

    tree = build_relation_tree(instance, F15E)
    assert tree is not None
    slot_node = tree.find("Modifications", FUSELAGE_SLOT)
    assert slot_node is not None, "F-15E 的 Custom_Slot_Fuselage 应当在树上"
    option_nodes = list(slot_node.children)
    assert len(option_nodes) == 8, "F-15E 的 Custom_Slot_Fuselage 实测 8 条选项"

    seen_slots: set[int] = set()
    for node in option_nodes:
        want = expected(node.row_id)
        got: list[tuple[int, int]] = []
        for child in node.children:
            match = re.fullmatch(r"槽 (\d+) · (.+)", child.label)
            assert match, child.label
            got.append((int(match.group(1)), child.row_id))
            assert child.table == "Turrets" and child.has_row, child.label
            assert child.id_text == f"Turrets #{child.row_id}"
            # 口径：21 个槽是「池 + 选项子集」，**不是**「替换第 N 个炮塔」——
            # 用户看得见的那串字（label）里不许出现"替换"，口径留在 hint 里。
            assert "替换" not in child.label, child.label
            assert "池 + 选项子集" in child.hint, child.hint
        assert tuple(got) == want, (node.row_id, got, want)
        seen_slots.update(slot for slot, _ in got)

    rich = next(node for node in option_nodes if node.row_id == RICH_OPTION)
    assert len(rich.children) == 12, "12 个槽要 12 行 —— 不折叠、也不按炮塔去重"
    assert [child.row_id for child in rich.children] == (
        [160] * 6 + [533] * 2 + [160] * 2 + [533] * 2
    )
    assert seen_slots >= set(range(7, 19)), sorted(seen_slots)


def test_the_slot_scanner_covers_all_21_columns() -> None:
    """21 个槽（0..20）**一个都不跳过** —— 连原版恒为 0 的 `Turret20Id` 也算一个槽位。

    真数据里槽 19/20 从来没被用过（`zero.Options.Turret20Id`：「不要用它当第 21 个炮塔槽」），
    所以这里用假行把 21 个槽全填上，验"全扫 / 按槽号升序 / 0 跳过 / 找不到行就不猜"四件事。
    """
    from ba_mod_maker.app.viewmodel.relation_tree import option_turret_slots
    from ba_mod_maker.app.viewmodel.unit_card import TURRET_SLOTS

    assert len(TURRET_SLOTS) == 21
    assert TURRET_SLOTS[0] == "Turret0Id" and TURRET_SLOTS[20] == "Turret20Id"

    full = {column: 500 + slot for slot, column in enumerate(TURRET_SLOTS)}
    assert option_turret_slots(_FakeOptionsData(full), 7) == tuple(
        (slot, 500 + slot) for slot in range(21)
    )
    # 0 = 这一条选项没占这个槽 -> 不画；剩下的仍按槽号升序。
    assert option_turret_slots(_FakeOptionsData({"Turret0Id": 9, "Turret20Id": 11}), 7) == (
        (0, 9),
        (20, 11),
    )
    # 表里没有这一行 -> 空元组（不猜一个出来）。
    assert option_turret_slots(_FakeOptionsData(full), 8) == ()


def test_a_slot_row_is_clickable_and_shows_the_slot_number(window) -> None:
    """那一行在**控件里**真的画出来了、而且点得动（点它 -> 中栏换成 Turrets 那一行）。"""
    _editor, page, tree = _open(window, F15E)
    node = tree.node_for("Options", RICH_OPTION)
    assert node is not None and node.children
    key = node.children[0].key
    text = tree.item_text(key)
    assert text.startswith("槽 7 · "), text
    # ID 在**右边自己那一列**：只有 #Id，表名不在名字列里了（2026-10-01 口径）。
    assert f"Turrets #{node.children[0].row_id}" not in text, text
    assert tree.item_text(key, COL_ID) == f"#{node.children[0].row_id}"

    assert tree.activate_key(key)
    focus = page.focus
    assert focus is not None and focus.table == "Turrets"
    assert focus.row_id == node.children[0].row_id


class _FakeOptionsRows:
    """只够 `option_turret_slots` 用的假 Rows：`by_id` + `i`（列名 -> 值）。"""

    def __init__(self, row: dict[str, int], row_id: int = 7) -> None:
        self._row = row
        self._row_id = row_id

    def by_id(self, row_id: int):
        return self._row if row_id == self._row_id else None

    def i(self, row, column: str) -> int:
        return int(row.get(column, 0))


class _FakeOptionsData:
    """把 `data.t("Options")` 换成上面那张假表（这一条用例只碰它）。"""

    def __init__(self, row: dict[str, int]) -> None:
        self._rows = _FakeOptionsRows(row)

    def t(self, name: str):
        assert name == "Options"
        return self._rows


# --------------------------------------------------------------------------- #
# 弹药：每条弹名字后面就贴着 ×N（用户："我需要能调武器使用的弹药，拥有多少弹药"）
# --------------------------------------------------------------------------- #


def test_every_ammo_row_shows_its_quantity(instance, window) -> None:
    """⭐ 左栏这一半的活：弹药**每一条**上要一眼看得见数量。

    原来 ×N 挤在行尾小字里（跟着一堆说明一起出省略号）；现在它贴着名字、排在
    「表名 #Id」之前 —— 名字与 ID 一起出省略号时，先被吃掉的也不是它。

    口径自己算一遍（不抄 relation_tree 的实现）：
        N = Σ WeaponAmmunitions.Quantity × Ammunitions.HUDMultiplier
    与卡片上那个数字（unit_card.AmmoRow.count）是同一个口径。
    """
    from ba_mod_maker.app.viewmodel.unit_card import CardData

    data = CardData(instance)
    ammo_rows = data.t("Ammunitions")
    expected: dict[int, int] = {}
    # ❗ 遍历的是 data.weapon_ids（**按人不去重**：5 个人扛 5 把 AK-74RMO 就是 5 条），
    #    这正是卡片统计携弹量的口径 —— 树上的 ×N 与卡片上那个数必须是同一个。
    for weapon_id in data.weapon_ids(INFANTRY):
        for ammo_id, quantity, _order in data.weapon_ammo(INFANTRY, weapon_id):
            row = ammo_rows.by_id(ammo_id)
            expected[ammo_id] = expected.get(ammo_id, 0) + quantity * ammo_rows.i(
                row, "HUDMultiplier"
            )
    assert expected, "样本单位一条携弹行都没有，用例没跑起来"

    _editor, _page, tree = _open(window, INFANTRY)
    ammo = tree.tree.group("ammo")
    assert len(ammo.children) == len(expected), "弹药条数与数据对不上"
    for child in ammo.children:
        want = f"×{expected[child.row_id]}"
        assert child.qty == want, (child.label, child.qty, want)
        text = tree.item_text(child.key)
        assert want in text, f"名字那一列里看不见数量：{text}"
        # 名字列 = 名字 + 数量（ID 挪到右边那一列去了，不在这条文字里）。
        assert f"Ammunitions #{child.row_id}" not in text, text
        assert tree.item_text(child.key, COL_ID) == f"#{child.row_id}"
    # 几把枪共用一个弹种时要**求和**（645：5.45x39mm = 400 × 3 = 1200）
    by_id = {child.row_id: child.qty for child in ammo.children}
    assert by_id[361] == "×1200"
    assert by_id[906] == "×3"


def test_zero_quantity_is_still_shown(instance) -> None:
    """×0 也要显示 —— "这一条弹一发都没有"是有效信息，不许按"没有就不画"处理。

    真实数据里没有 Quantity 合计为 0 的携弹行（24 张表扫过），所以把某条弹的
    HUDMultiplier 改成 0：count = Quantity 之和 × HUDMultiplier，正好是 ×0。
    """
    from ba_mod_maker.app.screens.editor.relation_tree_panel import _name_text
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree
    from ba_mod_maker.app.viewmodel.unit_card import CardData, build_unit_card

    data = CardData(instance)
    weapon = next(one for one in build_unit_card(data, INFANTRY).weapons if one.ammo)
    ammo_id = weapon.ammo[0].ammo_id
    table = instance.table("Ammunitions")
    row = next(one for one in table.rows if one[table.column_index("Id")] == ammo_id)
    row[table.column_index("HUDMultiplier")] = 0

    tree = build_relation_tree(instance, INFANTRY)
    assert tree is not None
    node = next(child for child in tree.group("ammo").children if child.row_id == ammo_id)
    assert node.qty == "×0"
    assert "×0" in _name_text(node), "×0 没画到名字那一列里"


# --------------------------------------------------------------------------- #
# ID：**右侧一列只有 `#Id`**（表名进 tooltip），而且那一行在库里真的存在
# --------------------------------------------------------------------------- #


def test_every_row_named_on_the_tree_exists_in_the_library(instance) -> None:
    """⭐ 用户硬口径：ID 全部显示（`表名 #Id`）—— 那就**一行都不许写错**。

    扫树上每一个节点的 (表, 行 Id) 与行尾内联提到的行，逐个回库里验存在。
    """
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    checked = 0
    for unit_id in (STRYKER, INFANTRY, 1, 80, 168, 287, 125, 70):
        tree = build_relation_tree(instance, unit_id)
        assert tree is not None
        for ref in tree.mentioned_rows():
            assert ref.table in instance.table_names, (unit_id, ref)
            table = instance.table(ref.table)
            index = table.column_index("Id")
            assert any(row[index] == ref.row_id for row in table.rows), (unit_id, ref)
            checked += 1
    assert checked > 500, f"扫到的行太少（{checked}）—— 用例没真的跑起来"


def test_id_text_is_written_as_table_then_hash_id(instance) -> None:
    """节点那一列写的就是 `表名 #Id`（例：`Turrets #522`、`Options #757`）。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    texts = {item.id_text for item in tree.flatten() if item.has_row}
    assert "Units #21" in texts
    assert "Turrets #522" in texts
    assert "Options #757" in texts
    assert "Armors #70" in texts
    assert "Modifications #19" in texts
    # 抬头节点与说明行没有 ID（它们不是"哪一行"）。
    assert all(
        item.id_text == ""
        for item in tree.flatten()
        if item.kind in {"group", "note", "unit"} and item.table != "Units"
    )


def test_the_id_lives_in_its_own_right_hand_column(window) -> None:
    """⭐ ID **全部显示**，但在右边自己一列：只有 `#Id`，表名不在名字列里（2026-10-01 口径）。

    用户原话：「左边红圈里面的 SquadMembers #* 太占空间了，能不能只在右边显示 #* 编号？」
    —— 这一条推翻了上一版"ID 并进名字列"（当时怕单独一列被挤出可视区；实测把它并进
    名字列反而把名字挤没了）。
    """
    _editor, _page, tree = _open(window, STRYKER)
    seen: dict[str, str] = {}
    for key in tree.visible_keys():
        item = tree.item(key)
        if item is not None:
            seen[item.text(COL_ID)] = item.text(COL_NAME)
    # ① 编号全在右列，而且**只有 # 与数字**（不带表名）。
    for number, name in seen.items():
        if number:
            assert re.fullmatch(r"#\d+", number), f"右列不是纯编号：{number!r}（行：{name!r}）"
    for wanted in ("#21", "#19", "#757", "#522", "#70"):
        assert wanted in seen, f"右列看不见 {wanted}（有：{sorted(seen)[:12]}）"
    # ② 名字列一个 `表名 #` 都不许有（撑满名字列的就是它）。
    for name in seen.values():
        assert not re.search(r"\S+ #\d+", name), f"名字列里还留着表名#Id：{name!r}"
    assert "Armor Stryker Base" in seen["#70"], seen["#70"]
    # ③ 抬头 / 说明行没有行 -> 右列留空。
    for key in ("mods", "turrets", "ammo", "armor"):
        item = tree.item(key)
        assert item is not None, key
        assert item.text(COL_ID) == "", (key, item.text(COL_ID))
    # ④ 表名没丢：它挪进了 tooltip（名字列与编号列都挂同一句）。
    root = tree.item(tree.tree.roots[0].key)
    assert root is not None and root.toolTip(COL_NAME).startswith(f"Units #{STRYKER}")
    assert root.toolTip(COL_ID) == root.toolTip(COL_NAME)


# --------------------------------------------------------------------------- #
# 控件：点一行 -> 中栏换成那一行的全部列
# --------------------------------------------------------------------------- #


def test_member_rows_are_plain_numbers_and_the_id_column_is_pure_id(window) -> None:
    """⭐ 四条判据一起钉死（用户 2026-10-01：「SquadMembers #* 太占空间了，能不能只在右边
    显示 #* 编号？」「第 1 名之类的，能不能就显示 1」）。

    ① 名字列不含 `SquadMembers #`（也不含任何 `表名 #数字`）；
    ② 右列恰好是 `#113` 这种（只有 # 与数字、没有表名）；
    ③ 班组成员行的名字列是 1/2/3…（不含「第」与「名」）；
    ④ 抬头行的右列是空串。
    """
    _editor, _page, tree = _open(window, INFANTRY)
    squad = tree.tree.group("squad")
    assert squad is not None and squad.children, "样本步兵没有班组成员行？"
    # 这一组里除了成员还有「班用武器」那几行 —— 判据只针对 SquadMembers。
    members = [node for node in squad.children if node.table == "SquadMembers"]
    assert len(members) >= 4, [node.table for node in squad.children]

    numbers: list[str] = []
    for index, node in enumerate(members, start=1):
        item = tree.item(node.key)
        assert item is not None, node.key
        name = item.text(COL_NAME)
        number = item.text(COL_ID)
        # ① 名字列不许再有 `表名 #数字`（撑满名字列的就是它）。
        assert not re.search(r"\S+ #\d+", name), name
        assert "SquadMembers" not in name, name
        # ② 右列只有 `#Id`（表名进 tooltip）。
        assert re.fullmatch(r"#\d+", number), (node.label, number)
        assert number == f"#{node.row_id}", (node.label, number)
        assert "SquadMembers" not in number, number
        # ③ 名字列是纯序号 ——「第 N 名」那两个字彻底没了（模型与控件两处一起看）。
        assert node.label == str(index), node.label
        assert "第" not in node.label and "名" not in node.label, node.label
        head = name[2:] if name.startswith("● ") else name
        assert head.split(" ")[0] == str(index), name
        numbers.append(head.split(" ")[0])
    assert numbers == [str(one) for one in range(1, len(numbers) + 1)], numbers

    # ④ 抬头行（组节点）没有行 -> 右列是空串。
    group_item = tree.item("squad")
    assert group_item is not None, "找不到班组成员那一组的抬头"
    assert group_item.text(COL_ID) == ""


def test_entering_the_editor_shows_the_unit_row_and_selects_it(window) -> None:
    """进编辑器：中栏 = Units#21 的 31 列；树上根节点被选中。"""
    _editor, page, tree = _open(window, STRYKER)
    assert len(page.entries.rows) == 31
    # 副标题是**纯文字**（中文表名 + 表#Id…）：记号在抬头那个图标位上
    # （用户口径 2026-10-01 第二轮：图标位只能有一个，emoji 只在没真图时兜底）。
    assert page.entries.subtitle_text.startswith(f"{table_title('Units')} Units#{STRYKER} · 31 列")
    assert page._left is tree, "左栏不是关系树"
    current = tree.widget.currentItem()
    assert current is not None and current.text(COL_ID) == f"#{STRYKER}"


#: 同一个 (表, 行 Id) 在树上出现两次的那个样本：改装槽选项下的槽位行（引用路径）
#: 与炮塔组里那一行（规范位置）—— 用户报的「点炮塔却展开改装件」就是它。
TURRET_ROW = 18
FUSELAGE_SLOT_KEY = "mods/19/opt/51/slot/0"


def test_find_prefers_the_canonical_place_over_a_mods_reference(instance) -> None:
    """⭐ 同一个 (表, 行 Id) 出现两次时，`find` 要给**规范位置**（不是 mods 引用路径）。

    单位 21（Stryker RV）：Turrets#18 既是改装槽选项下那条槽位行
    （`mods/19/opt/51/slot/0`，引用路径），也是炮塔组里那一行（`turrets/18`，规范位置）。
    程序化切焦点（↗ 跳转 / 检索跳转 / 进编辑器）应当落规范位置 —— 用户报的
    「我明明点的是画红圈的炮塔，为什么展开的却是上面的改装件？」就是 find 撞了前者。
    """
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    both = [
        item.key for item in tree.flatten() if item.table == "Turrets" and item.row_id == TURRET_ROW
    ]
    assert len(both) >= 2, f"这个样本不再重复了，用例前提没了：{both}"
    assert any(key.startswith("mods/") for key in both), both
    assert any(not key.startswith("mods/") for key in both), both

    node = tree.find("Turrets", TURRET_ROW)
    assert node is not None
    assert node.key == "turrets/18", f"find 撞到引用路径了：{node.key}"
    assert not node.key.startswith("mods/")
    assert tree.find("Turrets", 999999) is None


def test_find_falls_back_to_the_first_hit_when_every_hit_is_a_mods_reference() -> None:
    """两处**都在 mods 里**（没有规范位置）时退回第一个 —— 兜底必须确定，不许随机。"""
    from ba_mod_maker.app.viewmodel.relation_tree import RelationTree, TreeItem

    first = TreeItem(key="mods/1/opt/2/slot/0", kind="row", label="A", table="Turrets", row_id=7)
    second = TreeItem(key="mods/1/opt/3/slot/1", kind="row", label="B", table="Turrets", row_id=7)
    root = TreeItem(key="unit/1", kind="unit", label="U", children=(first, second))
    tree = RelationTree(unit_id=1, title="U", subtitle="", roots=(root,))

    assert tree.find("Turrets", 7) is first


def test_clicking_a_tree_row_shows_all_of_that_rows_columns(window) -> None:
    """⭐ 点树上任意一行 -> 中栏是那一行的**全部列**（列数 = 该表列数，一条不少）。"""
    _editor, page, tree = _open(window, STRYKER)
    cases = [
        (tree.tree.group("armor").children[0].key, "Armors"),
        (tree.tree.group("mobility").children[0].key, "Mobility"),
        (tree.tree.group("sensors").children[0].key, "Sensors"),
        (tree.tree.group("abilities").children[0].key, "Abilities"),
        (tree.tree.group("mods").children[0].key, "Modifications"),
        (tree.tree.group("mods").children[0].children[0].key, "Options"),
    ]
    for key, table in cases:
        assert tree.activate_key(key), key
        focus = page.focus
        assert focus is not None and focus.table == table, (key, focus)
        columns = len(window._instance.table(table).columns)
        assert len(page.entries.rows) == columns, (table, len(page.entries.rows), columns)
        assert page.entries.subtitle_text.startswith(
            f"{table_title(table)} {table}#{focus.row_id} · {columns} 列"
        )
    # 树的选中态跟着中栏走（不是各说各话）。
    assert tree.widget.currentItem().text(COL_ID) == f"#{page.focus.row_id}"


def test_middle_column_is_all_columns_with_no_two_gates(window) -> None:
    """中栏**一次铺全**：没有"关键列 / 全部列"两道门（任务书硬口径）。"""
    from ba_mod_maker.core.edit.scope import RowRef

    _editor, page, _tree = _open(window, STRYKER)
    page.set_focus(RowRef("Options", 757))
    columns = len(window._instance.table("Options").columns)
    assert len(page.entries.rows) == columns == 46
    assert page.entries.summary_text == f"{columns} 列"
    assert "关键列" not in page.entries.summary_text


# --------------------------------------------------------------------------- #
# 右栏那张卡：整块只读、不是导航面
# --------------------------------------------------------------------------- #


def _in_zoom_bar(widget, card) -> bool:
    """这颗控件是不是「放大 / 缩小」那一排（role=card-zoom）里的？

    那是**显示**开关（不写库、不改数据），不算"可点的编辑入口"。

    ❗ 同一份判据在 tests/test_relation_tree.py 与 tests/test_card_edit.py 里各有一份
    （copy，不是共享；改一处要同时改另一处）。
    """
    parent = widget.parentWidget()
    while parent is not None and parent is not card:
        if parent.property("role") == "card-zoom":
            return True
        parent = parent.parentWidget()
    return False


def test_card_has_no_clickable_editing_entry_point(window) -> None:
    """⭐ 卡上**没有任何**可点的编辑入口：QComboBox 一个都不许有，按钮只能是显示开关。

    （军械库那张卡的「简略 / 详细」与改装槽下拉在这里被覆写掉了 ——
    只读卡不接写盘通道、也不做预览，见 editor/readonly_card.py。）

    ❗ 2026-09-29 用户口径：只读卡上要给一个「放大 / 缩小」开关（role=card-zoom）——
    它只改卡片的显示大小、一个字节都不写。除它之外卡上仍然一颗按钮都不许有
    （老断言是"一颗按钮都不许有"，那是加这个开关之前的版本）。
    """
    _editor, page, _tree = _open(window, STRYKER)
    card = page.card
    assert card.card is not None and card.card.weapons, "样本单位应当有武器（那一块最容易冒出按钮）"
    buttons = card.findChildren(QPushButton)
    combos = card.findChildren(QComboBox)
    scattered = [button.text() for button in buttons if not _in_zoom_bar(button, card)]
    assert scattered == [], f"只读卡上冒出了「放大 / 缩小」以外的按钮：{scattered}"
    assert combos == [], f"只读卡上冒出了下拉：{len(combos)} 个"


def test_main_screen_no_longer_uses_the_editable_card(window) -> None:
    """`editor/editable_card.py` **不再被主屏使用**（文件先留着，删留给下一轮）。"""
    import inspect

    from ba_mod_maker.app.screens.editor import unit_editor_screen
    from ba_mod_maker.app.screens.editor.editable_card import EditableCardPanel
    from ba_mod_maker.app.screens.editor.readonly_card import ReadOnlyCardPanel

    _editor, page, _tree = _open(window, STRYKER)
    assert isinstance(page.card, ReadOnlyCardPanel)
    assert not isinstance(page.card, EditableCardPanel)
    source = inspect.getsource(unit_editor_screen)
    assert "EditableCardPanel" not in source, "主屏还引用着可写卡"
    # 只读卡上也**没有**可写卡那几条信号（点它不会写库、不会跳页）。
    for name in ("cell_activated", "row_activated", "swap_requested", "clone_requested"):
        assert not hasattr(page.card, name), name


def test_validation_badge_still_shows_up_and_goes_away(window, qapp) -> None:
    """角标还在（立绘上那个 "!"）：出问题才画，改回去就没了 —— 但它不再是可点入口。"""
    from ba_mod_maker.core.edit.refs import CellRef

    # 样本用 BTR-82A（步兵）：座位数 8 -> 2 会触发「人数与座位数不符」那条校验。
    _editor, page, _tree = _open(window, 1)
    assert window._unit_editor.validation_text == ""
    seats = window._unit_editor.context.cell(CellRef("Units", 1, "InfantrySlots"))
    assert seats is not None and seats.raw == 8
    window._unit_editor.apply_cell(seats.ref, 2)
    # 角标是**延后一轮**算的（改一格就同步跑一遍全库校验太费，见 _schedule_findings）。
    qapp.processEvents()
    assert window._unit_editor.validation_text.startswith("校验")
    window._unit_editor.apply_cell(seats.ref, 8)
    qapp.processEvents()
    assert window._unit_editor.validation_text == ""


# --------------------------------------------------------------------------- #
# 文案：不许出现「共用 / N 个单位在用 / 引用（出）/ 引用（入）」
# --------------------------------------------------------------------------- #

_FORBIDDEN = ("共用", "个单位在用", "个单位用", "引用（出）", "引用（入）", "引用(出)", "引用(入)")


def test_no_sharing_or_reference_wording_anywhere_in_the_editor(window) -> None:
    """⭐ 用户口径：卡上 / 树上**一律不出现**「N 个单位在用」，也**没有**引用（出）/（入）两组。"""
    from PySide6.QtWidgets import QLabel

    _editor, page, tree = _open(window, STRYKER)
    texts: list[str] = []
    for key in tree.visible_keys():
        item = tree.item(key)
        if item is not None:
            texts.extend(item.text(column) for column in range(tree.widget.columnCount()))
            texts.append(item.toolTip(0))
    for label in page.card.findChildren(QLabel):
        texts.append(label.text())
        texts.append(label.toolTip())
    for row in page.entries.rows:
        texts.append(row.cell.text)
        texts.append(row.value_button.toolTip())
    for text in texts:
        for word in _FORBIDDEN:
            assert word not in text, f"文案里出现了「{word}」：{text}"


def test_there_is_no_reference_group_in_the_tree(instance) -> None:
    """树里只有那十组：引用（出）/ 引用（入）已经被删掉。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    labels = [group.label for group in tree.groups]
    assert len(labels) == 10
    assert not any("引用" in label for label in labels)
    assert not any("共用" in label for label in labels)


# --------------------------------------------------------------------------- #
# 树的动作线索（＋ / ⧉ / ✕ 长在哪些节点上）
# --------------------------------------------------------------------------- #


def test_clone_is_only_offered_where_there_is_a_link(instance) -> None:
    """⧉ 要"既有要复制的那一行、又有要重指的那一格"——Units 与配对行都不给。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, STRYKER)
    assert tree is not None
    cloneable = [item for item in tree.flatten() if item.can_clone]
    assert cloneable, "一个 ⧉ 都没有"
    for item in cloneable:
        assert item.link is not None and item.clone_row is not None
        assert item.clone_row.table != "Units" and item.link.table != "Units"
    # 本体那一行与运输配对行不给克隆。
    assert tree.roots[0].can_clone is False
    for child in tree.group("transports").children:
        assert child.can_clone is False, child.label


def test_remove_is_only_offered_on_this_units_own_rows(instance) -> None:
    """✕ 只长在"本单位自己的关联行"上（模板行与选项覆盖的那一格都不给）。"""
    from ba_mod_maker.app.viewmodel.relation_tree import build_relation_tree

    tree = build_relation_tree(instance, INFANTRY)
    assert tree is not None
    removable = [item for item in tree.flatten() if item.can_remove]
    assert removable
    for item in removable:
        for ref in item.remove_refs:
            table = instance.table(ref.table)
            unit_index = table.column_index("UnitId")
            index = table.column_index("Id")
            row = next(row for row in table.rows if row[index] == ref.row_id)
            assert row[unit_index] == INFANTRY, (item.label, ref)


# --------------------------------------------------------------------------- #
# 折叠：默认全收起 / 展开态不许被刷新吃掉 / 武器下面写弹药名字
# （用户 2026-09-29 实机反馈三条，逐条守）
# --------------------------------------------------------------------------- #

#: 用户截图那台车：Id 2 = M1A2 SEP v2 TUA，挂着 M256 120mm（Weapons#266）——
#: 它的两种弹是 M830A1 HEAT ×24 / M829A3 APFSDS ×16（实测）。
TUA = 2
M256 = 266


def test_the_tree_starts_collapsed(window) -> None:
    """⭐ 用户原话：「所有折叠栏默认收起」。

    第一次进编辑器：**只展开根**（单位那一行，不展开就只剩一行字），
    它下面那十个分组全是收起的。
    """
    _editor, _page, tree = _open(window, STRYKER)
    widget = tree.widget
    root = widget.topLevelItem(0)
    assert root is not None
    assert root.isExpanded(), "根要展开 —— 否则整棵树只剩单位那一行，等于没有导航"
    assert root.childCount() == 10
    for index in range(root.childCount()):
        child = root.child(index)
        assert not child.isExpanded(), child.text(0)
    # 按钮文案跟着**真实展开态**走：根是展开的 -> 它说"收起全部"（点一下连根一起收）。
    assert tree._expand.text() == "收起全部"


def test_expanding_survives_a_refresh(window) -> None:
    """手动展开的东西不许被自动刷新顶回去 —— 改一格就会 refresh 一次。"""
    _editor, page, tree = _open(window, STRYKER)
    root = tree.widget.topLevelItem(0)
    root.child(0).setExpanded(True)  # 手动展开第一个分组（改装槽）
    assert tree._expanded, "手动展开要记下来"

    page.refresh()  # 改一格走的就是这条

    root = tree.widget.topLevelItem(0)
    assert root.isExpanded() and root.child(0).isExpanded(), "刷新后展开态要保住"
    assert not root.child(1).isExpanded(), "没展开的也别顺手全开"


def test_the_rows_tool_host_is_transparent(window) -> None:
    """⭐ 用户原话（2026-09-29）：「左边框的这个按钮，下面的背景变深太突兀」。

    行尾那几颗按钮的**宿主控件**必须带 role=row-tools：全局那条
    全局那条 QWidget 背景规则会给它刷一层比树行底色更深的方块，
    而且宿主比按钮本身高、还溢到下一行（QSS 里 [row-tools] 那条把它置透明）。
    """
    from ba_mod_maker.app.screens.editor.relation_tree_panel import COL_TOOLS

    _editor, _page, tree = _open(window, STRYKER)
    # 拿有 ＋ 的那一组（根那一行没有按钮，itemWidget 会是 None）。
    item = tree.item("mods")
    assert item is not None
    host = tree.widget.itemWidget(item, COL_TOOLS)
    assert host is not None, "「改装槽」那一行有 ＋，宿主控件应当在"
    assert host.property("role") == "row-tools"


def test_every_ammo_of_a_weapon_is_its_own_row_under_it(window) -> None:
    """⭐ 用户原话：「左边的武器下，还是没有显示它拥有的弹药」。

    武器下面**一行一种弹**（不是把弹种名塞进行尾小字 —— 那样一长就被省略号吃掉，
    实测真机看到的是 "M830A1 HEAT ×24 · …"，第二种弹根本看不见）。
    每条弹自己是一个可点的行：点它 -> 中栏换成那一条携弹行（数量在那儿改），
    行尾还有 🔁 换弹种 / 🗑 删这一条。
    """
    _editor, _page, tree = _open(window, TUA)
    node = tree.node_for("Weapons", M256)
    assert node is not None, "这台车挂着 M256 120mm（Weapons#266）"
    assert node.note == "2 种弹 · 共 40 发", node.note

    assert [child.label for child in node.children] == ["M830A1 HEAT", "M829A3 APFSDS"]
    assert [child.note for child in node.children] == ["×24", "×16"]
    for child in node.children:
        assert child.table == "WeaponAmmunitions" and child.row_id > 0
        assert child.can_remove, "删的是本单位的携弹行"
        assert child.link is not None, "🔁 换弹种改的是这一格的 AmmunitionId"
        assert child.has_row
