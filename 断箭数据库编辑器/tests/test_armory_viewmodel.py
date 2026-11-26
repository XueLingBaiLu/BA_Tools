"""军械库视图模型的规则测试。

这一层承载主屏最核心的规则，所以把数字**钉死**：
卡片集合怎么算、按什么类别分组、行内怎么排。

用真实 DataBaseCompiled，不用假数据 —— 与 test_data_layer 同一理由：
要测的是数据侧的契约，不是"我以为的契约"。

❗ 下面断言的是 52085 的实测路标。数字变了 = 游戏更新过，或数据被换成了另一份。
❗ 两个实例行数本来就不同，本文件只锁 52085（默认非战役）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.app.viewmodel import (
    CATEGORY_LABELS,
    UNCLASSIFIED_COUNTRY_ID,
    UNCLASSIFIED_NAME,
    build_armory_sheet,
    build_counts,
    build_unit_detail,
    count_units,
    list_countries,
    list_specializations,
    unclassified_country,
)
from ba_mod_maker.app.viewmodel.unit_card import (
    CardData,
    Rows,
    _int,
    card_cost,
    unit_name,
    unit_portrait_key,
    unit_thumbnail_key,
)
from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.data.dataset import Table

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"

#: 实测路标（52085）。
LANDMARK_TOTAL = 395
LANDMARK_UNASSIGNED = 57
LANDMARK_BY_COUNTRY = {1: 199, 2: 196, 3: 0}

#: VDV Brigade 是反推卡片集合规则时用的样本（见 ADR 0002）。
VDV_SPEC_ID = 7
VDV_TOTAL = 31
#: CategoryType -> 卡片数。**键即行序**：0 侦察 / 1 步兵 / 2 载具 / 3 支援 /
#: 4 未分类 / 5 直升机 / 6 战机。这份字典同时锁住了"行序 = 枚举序"这件事。
VDV_ROWS = {0: 7, 1: 6, 2: 2, 3: 3, 5: 7, 6: 6}
ALL_ROWS = {0: 68, 1: 62, 2: 83, 3: 88, 4: 1, 5: 42, 6: 51}
UNASSIGNED_ROWS = {0: 11, 1: 1, 2: 41, 4: 1, 5: 3}


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


# --------------------------------------------------------------------------- #
# 枚举中文名
# --------------------------------------------------------------------------- #


def test_category_labels_are_in_enum_order() -> None:
    """类别名顺序就是 UnitCategoryTypeUI 的枚举顺序，不许重排。"""
    assert CATEGORY_LABELS == ("侦察", "步兵", "载具", "支援", "未分类", "直升机", "战机")


# --------------------------------------------------------------------------- #
# 阵营与专精
# --------------------------------------------------------------------------- #


def test_countries(instance) -> None:
    countries = list_countries(instance)
    assert [c.country_id for c in countries] == [1, 2, 3]
    assert [c.name for c in countries] == ["Russia", "USA", "Editor"]
    # Editor 是隐藏阵营：照常返回，由界面决定藏不藏。
    assert [c.hidden for c in countries] == [False, False, True]
    assert countries[0].max_points == 10000


def test_specializations_are_hangar_only_by_default(instance) -> None:
    """玩家真能选的专精有 12 个；Editor 专精（ShowInHangar=false）默认不给。"""
    assert len(list_specializations(instance)) == 12
    assert len(list_specializations(instance, hangar_only=False)) == 13
    vdv = next(s for s in list_specializations(instance) if s.specialization_id == VDV_SPEC_ID)
    assert vdv.name == "VDV Brigade"
    assert vdv.country_id == 1


def test_specialization_filter_by_country(instance) -> None:
    russia = list_specializations(instance, country_id=1)
    assert len(russia) == 6
    assert all(s.country_id == 1 for s in russia)


# --------------------------------------------------------------------------- #
# 计数
# --------------------------------------------------------------------------- #


def test_landmark_counts(instance) -> None:
    assert count_units(instance) == LANDMARK_TOTAL
    assert count_units(instance, only_unassigned=True) == LANDMARK_UNASSIGNED
    for country_id, expected in LANDMARK_BY_COUNTRY.items():
        assert count_units(instance, country_id=country_id) == expected


def test_counts_are_three_independent_scopes(instance) -> None:
    """三个数各自独立——只看专精会让人以为全游戏只有 31 个单位。"""
    counts = build_counts(instance, specialization_id=VDV_SPEC_ID, country_id=1)
    assert counts.specialization == VDV_TOTAL
    assert counts.country == 199
    assert counts.total == LANDMARK_TOTAL
    assert counts.unassigned == LANDMARK_UNASSIGNED


# --------------------------------------------------------------------------- #
# 卡片集合与行分组（主屏最核心的两条规则）
# --------------------------------------------------------------------------- #


def test_vdv_sheet_rows_match_category_type(instance) -> None:
    """卡片集合 = 专精直挂单位；行 = CategoryType，行序 = 枚举序。"""
    sheet = build_armory_sheet(instance, specialization_id=VDV_SPEC_ID)
    assert sheet.total == VDV_TOTAL
    assert {row.category: row.count for row in sheet.rows} == VDV_ROWS
    # 行序必须是枚举序，而不是字典序或插入序。
    assert [row.category for row in sheet.rows] == sorted(VDV_ROWS)
    assert [row.label for row in sheet.rows] == [CATEGORY_LABELS[c] for c in sorted(VDV_ROWS)]


def test_all_units_sheet(instance) -> None:
    sheet = build_armory_sheet(instance)
    assert sheet.total == LANDMARK_TOTAL
    assert {row.category: row.count for row in sheet.rows} == ALL_ROWS
    assert [row.category for row in sheet.rows] == list(range(7))


def test_unassigned_sheet_is_explicit_not_hidden(instance) -> None:
    """未编入任何专精的单位必须能**显式**看到，不许悄悄消失。"""
    sheet = build_armory_sheet(instance, only_unassigned=True)
    assert sheet.total == LANDMARK_UNASSIGNED
    assert {row.category: row.count for row in sheet.rows} == UNASSIGNED_ROWS


def test_country_filter_narrows_the_sheet(instance) -> None:
    sheet = build_armory_sheet(instance, specialization_id=VDV_SPEC_ID, country_id=2)
    # VDV 是俄罗斯专精，换成美国应当一个都不剩。
    assert sheet.total == 0
    assert sheet.rows == ()


def test_card_fields_come_from_units_columns(instance) -> None:
    """类别直取 Units 的列；**成本与缩略图走右卡口径**（见下面两条用例）。

    ❗ 原先这里断言 icon_key == Units.ThumbnailFileName 原列 —— 那是本次改掉的口径：
    缺省选项自己带 `Options.ThumbnailOverride` 的单位（全库 17 个）画的不是原列那张图。
    口径只有一处：`unit_card.unit_thumbnail_key`。
    """
    data = CardData(instance)
    sheet = build_armory_sheet(instance, specialization_id=VDV_SPEC_ID)
    for row in sheet.rows:
        for card in row.cards:
            assert card.cost == int(card_cost(data, card.unit_id))
            assert card.icon_key == unit_thumbnail_key(data, card.unit_id)
            assert card.category == row.category


def test_wall_cost_is_the_right_card_cost(instance) -> None:
    """左墙徽章 == 右卡那张卡的成本（**缺省选项**）—— 用户："以右边卡片为准"。

    以前左墙取 `Units.Cost` 原始列，于是 Su-57 写 225，而右卡是 335
    （225 + 缺省选项 50 + 60）；在预览里换过挂架时右卡 415、左墙还是 225。
    """
    data = CardData(instance)
    sheet = build_armory_sheet(instance)
    cards = {card.unit_id: card for row in sheet.rows for card in row.cards}

    # 路标（52085）：Su-57 225 -> 335；MiG-31K (S) 缺省选项 ±0，仍是 1500。
    assert cards[44].cost == 335
    assert cards[702].cost == 1500

    # 全库逐个复核：每一张卡都必须等于 card_cost(缺省)，一个都不许走原始列。
    units = instance.table("Units")
    raw = {row[units.column_index("Id")]: row[units.column_index("Cost")] for row in units.rows}
    for card in cards.values():
        assert card.cost == int(card_cost(data, card.unit_id)), card.display_name
    # 而且确实**不是**原始列：52085 里有 99 个上屏单位的缺省选项不是 ±0
    # （这条防"改了个寂寞" —— 万一卡墙又改回 Units.Cost，这里立刻红）。
    different = [uid for uid, card in cards.items() if card.cost != raw[uid]]
    assert len(different) == 99, f"徽章与原始列不同的卡有 {len(different)} 张，路标变了"


def test_cards_are_deterministically_ordered(instance) -> None:
    """两次构建必须给出完全一样的顺序，否则界面每次刷新都在跳。"""
    first = build_armory_sheet(instance, specialization_id=VDV_SPEC_ID)
    second = build_armory_sheet(instance, specialization_id=VDV_SPEC_ID)
    assert [c.unit_id for r in first.rows for c in r.cards] == [
        c.unit_id for r in second.rows for c in r.cards
    ]


def test_every_card_is_displayable_and_not_a_modification(instance) -> None:
    """可上屏的两个排除条件，逐个卡片复核一遍。"""
    table = instance.table("Units")
    id_col = table.column_index("Id")
    display_col = table.column_index("DisplayInArmory")
    mod_col = table.column_index("IsUnitModification")
    flags = {row[id_col]: (row[display_col], row[mod_col]) for row in table.rows}

    sheet = build_armory_sheet(instance)
    for row in sheet.rows:
        for card in row.cards:
            display, is_mod = flags[card.unit_id]
            assert display is True, f"{card.display_name} 不该上屏"
            assert is_mod is not True, f"{card.display_name} 是 modification，不该上屏"


# --------------------------------------------------------------------------- #
# 详情面板
# --------------------------------------------------------------------------- #


def _unit_id(instance, name: str) -> int:
    table = instance.table("Units")
    name_col = table.column_index("Name")
    id_col = table.column_index("Id")
    for row in table.rows:
        if row[name_col] == name:
            return int(row[id_col])
    raise AssertionError(f"数据里没有单位 {name}")


def test_infantry_detail_has_weapons_and_transports(instance) -> None:
    """步兵班：武器要有携行人数，运输选项要有内容。"""
    detail = build_unit_detail(
        instance, _unit_id(instance, "VDV DSh"), specialization_id=VDV_SPEC_ID
    )
    assert detail is not None
    assert detail.weapons, "步兵班不可能没有武器"
    assert any(w.quantity > 0 for w in detail.weapons), "至少一把武器要有携行人数"
    assert any(w.ammo for w in detail.weapons), "至少一把武器要挂弹药"
    assert [t.name for t in detail.transports][:2] == ["BTR-D", "BTR-MDM "]


def test_vehicle_detail_has_option_groups(instance) -> None:
    detail = build_unit_detail(
        instance, _unit_id(instance, "BTR-82"), specialization_id=VDV_SPEC_ID
    )
    assert detail is not None
    # 55 = 右卡口径（BTR-82 的缺省选项都是 ±0，所以与原始列同值）。
    assert detail.card.cost == 55
    assert detail.infantry_slots == 8
    assert [g.display_name for g in detail.option_groups] == [
        "Custom_Slot_Weapon_package",
        "Custom_Slot_Armor",
    ]


def test_detail_of_unknown_unit_is_none(instance) -> None:
    assert build_unit_detail(instance, 10_000_000) is None


def test_wall_card_name_is_the_right_card_title(instance) -> None:
    """卡墙的卡名 = 右卡抬头（缺省口径），不是 Units.HUDName 原始列。

    实机四条锚（docs/实机-*.png）：BTR-82 -> **BTR-82A**、T-72B -> **T-72B1**、
    PT-76 -> **PT-76B**；PT-76 选 AU-220M 之后是 PT-76-57（那一条在面板侧测）。
    """
    sheet = build_armory_sheet(instance)
    cards = {card.unit_id: card for row in sheet.rows for card in row.cards}
    assert cards[1].display_name == "BTR-82A"
    assert cards[9].display_name == "T-72B1"
    assert cards[230].display_name == "PT-76B"

    data = CardData(instance)
    for unit_id, card in cards.items():
        assert card.display_name == unit_name(data, unit_id)
    # 路标：卡墙里有名字被选项改写的单位（原始 HUDName != 卡名）
    units = instance.table("Units")
    at = {name: index for index, name in enumerate(units.columns)}
    by_id = {row[at["Id"]]: row for row in units.rows}
    changed = [
        uid
        for uid, card in cards.items()
        if card.hud_name != (by_id[uid][at["HUDName"]] or by_id[uid][at["Name"]])
    ]
    assert len(changed) == 53, f"缺省名被改写的卡数变了：{len(changed)}"


def test_wall_card_icon_is_the_right_card_image_by_default(instance) -> None:
    """卡墙的缩略图/立绘键 = **右卡那条口径**（缺省选项），不是 Units 原始两列。

    缺省项自己带 `Options.ThumbnailOverride` 的单位实测 **17 个**（23 / 72 / 108 / 129 …）——
    它们墙上的图以前画的是原始列那张（换了选项更不会动）。口径只有一处：
    `unit_card.unit_thumbnail_key` / `unit_card.unit_portrait_key`（与成本、卡名同一套纪律）。
    """
    sheet = build_armory_sheet(instance)
    cards = {card.unit_id: card for row in sheet.rows for card in row.cards}
    data = CardData(instance)
    units = instance.table("Units")
    at = {name: index for index, name in enumerate(units.columns)}
    by_id = {row[at["Id"]]: row for row in units.rows}

    # 两个抽查：22 的缺省项没带覆盖（照原列），23 的缺省项带了（图不一样）
    assert cards[22].icon_key == "US_STRYKER_ICV-Label"
    assert cards[23].icon_key == "RU_TYPHOON_VDV_KORD-Label"

    for unit_id, card in cards.items():
        assert card.icon_key == unit_thumbnail_key(data, unit_id)
        assert card.portrait_key == unit_portrait_key(data, unit_id)

    changed = [
        unit_id
        for unit_id, card in cards.items()
        if card.icon_key != (by_id[unit_id][at["ThumbnailFileName"]] or "")
    ]
    assert len(changed) == 17, f"缺省缩略图被改写的卡数变了：{len(changed)} -> {changed}"


def test_detail_card_image_matches_the_wall_card(instance) -> None:
    """右侧详情那份 ArmoryCard 与墙上那张走同一条口径（缺省）—— 别再各算一遍。"""
    detail = build_unit_detail(instance, 23)
    assert detail is not None
    assert detail.card.icon_key == "RU_TYPHOON_VDV_KORD-Label"
    assert detail.card.portrait_key == unit_portrait_key(CardData(instance), 23)


# --------------------------------------------------------------------------- #
# Rows 的按列索引（性能①）—— 快可以，语义一个都不许变
# --------------------------------------------------------------------------- #


def test_rows_where_matches_the_full_scan(instance) -> None:
    """`where()` 换成"按列建一次索引"之后，结果必须与逐行 `_int(...) == value` 一字不差。

    索引是**惰性**的：第一次问这一列时扫一遍建索引，之后只查字典。下面先问真实值
    （触发建索引）、再问别的值（走字典）、最后问一个**不在表里**的值 —— 三次都要与
    全表扫相同：顺序还是表内顺序，查不到还是空元组。
    """
    table = instance.table("Options")
    rows = Rows(table)
    at = {name: i for i, name in enumerate(table.columns)}
    ids = [row[at["ModificationId"]] for row in table.rows]
    ints = [value for value in ids if isinstance(value, int) and not isinstance(value, bool)]
    assert ints, "Options.ModificationId 一个整数都没有？"

    def scan(value: object) -> tuple:
        return tuple(row for row in table.rows if _int(row[at["ModificationId"]]) == value)

    # max+1 不可能是索引里的键（键只会是 0 或列里出现过的整数）
    for value in (ids[0], ids[len(ids) // 2], max(ints) + 1):
        assert rows.where("ModificationId", value) == scan(value)
    # 结果缓存照旧：同一个值再问一次，拿到的是同一个元组对象
    assert rows.where("ModificationId", ids[0]) is rows.where("ModificationId", ids[0])


def test_rows_where2_keeps_table_order_and_handles_empty_sides() -> None:
    """`where2` 现在"挑短的那边当候选、按另一列过滤" —— 结果必须仍是表内顺序。

    两个方向都要试（短的可能是 a 也可能是 b），任意一侧为空时给空元组。
    """
    table = Table(
        name="T",
        columns=("UnitId", "WeaponId"),
        rows=[[1, 7], [1, 8], [2, 7], [1, 7]],
        source_columns=("UnitId", "WeaponId"),
        source_path=Path("<mem>"),
    )
    rows = Rows(table)
    first, second, third, fourth = table.rows
    # 右侧短（1 行）-> 拿右侧当候选
    assert rows.where2("UnitId", 1, "WeaponId", 8) == (second,)
    # 左侧短（1 行）-> 拿左侧当候选，结果仍是表内顺序
    assert rows.where2("WeaponId", 7, "UnitId", 2) == (third,)
    # 两侧都不空 -> 按表内顺序（first 在 fourth 前）
    assert rows.where2("UnitId", 1, "WeaponId", 7) == (first, fourth)
    # 任意一侧为空 -> 空元组
    assert rows.where2("UnitId", 99, "WeaponId", 7) == ()
    assert rows.where2("UnitId", 1, "WeaponId", 99) == ()


def test_rows_invalidate_drops_the_column_index() -> None:
    """改过表之后 `invalidate()` 必须把**按列索引**一起丢掉。

    这是新加的那份索引最容易漏的一句：漏了它就是"改了数卡片不动"那个老毛病
    （实测踩过：插了一行 SquadMembers，卡片还显示 5 人）。用内存表测，不动真实数据源。
    """
    table = Table(
        name="T",
        columns=("Id", "UnitId"),
        rows=[[1, 10], [2, 10], [3, 20]],
        source_columns=("Id", "UnitId"),
        source_path=Path("<mem>"),
    )
    rows = Rows(table)
    assert rows.where("UnitId", 10) == (table.rows[0], table.rows[1])  # 先把索引建出来
    table.rows[1][1] = 20  # 改一行
    rows.invalidate()
    assert rows.where("UnitId", 10) == (table.rows[0],)
    assert rows.where("UnitId", 20) == (table.rows[1], table.rows[2])


# --------------------------------------------------------------------------- #
# 「未分类」那一格（用户 2026-10-02 深夜：「有些没有国家的单位，统一放在"未分类"国家里」）
# --------------------------------------------------------------------------- #


def test_unclassified_country_is_a_synthetic_row() -> None:
    """那一格不在 Countries 表里：Id 借 -1，旗子是游戏里那面橙底问号旗。"""
    from ba_mod_maker.core.assets.families import UNCLASSIFIED_FLAG_KEY

    country = unclassified_country()
    assert country.country_id == UNCLASSIFIED_COUNTRY_ID == -1
    assert country.display_name == UNCLASSIFIED_NAME == "未分类"
    assert country.flag_key == UNCLASSIFIED_FLAG_KEY == "random flag"
    assert country.hidden is False, "它自己若是隐藏的，界面那一圈会把它跳过"


def test_unclassified_collects_hidden_and_unknown_countries(instance) -> None:
    """判据 = **隐藏国家**的 Id ＋ 表里**根本没登记**的 Id（卡片集与计数共用这一份）。"""
    from ba_mod_maker.app.viewmodel.armory import _country_matches, unclassified_country_ids

    unclassified = unclassified_country_ids(instance)
    assert unclassified == {3}, "实测：Countries 3 行里只有 Editor(3) 是 Hidden"
    assert _country_matches(3, UNCLASSIFIED_COUNTRY_ID, unclassified) is True
    assert _country_matches(999, UNCLASSIFIED_COUNTRY_ID, frozenset({999})) is True, (
        "查不到的也要收"
    )
    assert _country_matches(1, UNCLASSIFIED_COUNTRY_ID, unclassified) is False
    assert _country_matches(1, 1, unclassified) is True


def test_unclassified_bucket_collects_what_the_armory_hides(instance) -> None:
    """「未分类」是**收容所**：平时被挡在门外的（`DisplayInArmory=False`）也要上墙。

    用户口径（2026-10-03 原话）：「**上不了军械库的也给我上**」。
    实测：CountryId=3 的 8 行（ZOM 全体）本来就既不上屏也不是改型
    ⇒ 收容所里正好是这 8 个（读数：_work/probe_unclassified_specs.py）。
    """
    assert count_units(instance, country_id=UNCLASSIFIED_COUNTRY_ID) == 8
    sheet = build_armory_sheet(instance, country_id=UNCLASSIFIED_COUNTRY_ID)
    assert sheet.total == 8
    assert sheet.country_id == UNCLASSIFIED_COUNTRY_ID
    assert "未分类 8" in sheet.subtitle

    cards = [card for row in sheet.rows for card in row.cards]
    assert {card.unit_id for card in cards} == set(range(490, 498)), "ZOM 490..497"
    # 行序照 CategoryType：1 步兵（Police / Civilians）在前，4 未分类（6 个僵尸）在后。
    assert [(row.category, row.count) for row in sheet.rows] == [(1, 2), (4, 6)]


def test_include_hidden_shows_the_whole_units_table(instance) -> None:
    """用户 2026-10-03：「**我要能在产品里面，看到所有单位！**」

    默认军械库只放能上场的 **395**；开关打开后 = **Units 整表 540**
    （含 78 个改型、以及"未分类"那一类原本一条都不上墙的 36 艘船等）。
    """
    assert count_units(instance) == 395
    assert count_units(instance, include_hidden=True) == 540
    assert build_armory_sheet(instance).total == 395

    sheet = build_armory_sheet(instance, include_hidden=True)
    assert sheet.total == 540
    assert "全部 540" in sheet.subtitle
    # 七个类别行全都非空（默认那 395 张里"未分类"这一类是空的）
    assert [row.count for row in sheet.rows] == [96, 125, 92, 96, 36, 44, 51]
    # 按国家筛也跟着放宽（默认 199 / 196）
    assert count_units(instance, country_id=1, include_hidden=True) == 254
    assert count_units(instance, country_id=2, include_hidden=True) == 278
    # 专精那一支同样跟着走（VDV 本来就是 31，它没有不上屏的单位）
    assert build_armory_sheet(instance, specialization_id=7, include_hidden=True).total == 31


def test_unclassified_keeps_the_specialization_list(instance) -> None:
    """「未分类」那一格**不按国家筛专精** —— 按国家筛的话下拉里一个都不剩。

    用户口径（2026-10-03 原话）：「**我说我还要分专精，你没分**」。
    实测：RU 下 **6** 个专精、未分类下 **12** 个（= 全部机库专精）。
    """
    everything = list_specializations(instance)
    unclassified = list_specializations(instance, country_id=UNCLASSIFIED_COUNTRY_ID)
    assert [s.specialization_id for s in unclassified] == [
        s.specialization_id for s in everything
    ], "未分类下要按「全部专精」给"
    assert len(list_specializations(instance, country_id=1)) == 6
    assert len(unclassified) == 12

    # 但那 8 个 ZOM 一个专精都没挂（探针实测）⇒ 点某个专精是 0 张；
    # 「全部单位」/「未编入专精」两支才是它们的家。
    vdv = build_armory_sheet(instance, country_id=UNCLASSIFIED_COUNTRY_ID, specialization_id=7)
    assert vdv.total == 0
    unassigned = build_armory_sheet(
        instance, country_id=UNCLASSIFIED_COUNTRY_ID, only_unassigned=True
    )
    assert unassigned.total == 8
