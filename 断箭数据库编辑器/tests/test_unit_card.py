"""单位卡片的公式测试（零 Qt，用真实 DataBaseCompiled）。

这里钉的是**截图复核过的那几个数**，不是实现细节：每个数字都能在
docs/研究-单位卡片公式.md 的样本表里找到对应行。

为什么不用假数据：卡片的坑全在"沿选项图合成"上（传感器被改写、成本含选项、
变体单位的改型挂在父单位上）—— 假数据里没有这张图，测出来的只是我以为的图。

❗ 下面的数字是 52085 的实测路标。变了 = 游戏更新过，或数据被换了。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.app.viewmodel.unit_card import (
    INFANTRY_WEIGHT_PER_MEMBER,
    MAX_STATS_PER_LINE,
    STAT_SPACING_TIGHT,
    STAT_SPACING_WIDE,
    STAT_SPECS,
    CardData,
    ammo_resupply,
    ammo_target_traits,
    armor_zones,
    build_unit_card,
    card_cost,
    card_weight,
    effective_sensors,
    fmt_time,
    get_infantry_slots,
    new_member_values,
    optics_triple,
    purchase_delay,
    squad_loadout,
    unit_name,
    unit_portrait_key,
    unit_thumbnail_key,
    weapon_groups,
)
from ba_mod_maker.core.assets.families import TARGET_ICON_KEYS
from ba_mod_maker.core.assets.localization import BUILTIN_LABELS, Localization
from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"

#: 截图样本：单位 Id -> [(数值行的 key, 截图上的值)]
#: 出处：docs/设计-单位卡片复刻.md §4.1/§8.1 与 研究-单位卡片公式.md §3.5。
STAT_SAMPLES: dict[int, dict[str, str]] = {
    645: {  # VDV Shturm（收起态截图，Cost 60）
        "armor": "10",
        "health": "25",
        "soldier_count": "5",
        "optics": "1400",
        "stealth": "1.75",
        "speed_forward": "18",
        "weight": "625",
        "refund": "6:30",
    },
    111: {  # Desantniki（Cost 80）
        "armor": "8",
        "health": "50",
        "soldier_count": "10",
        "optics": "1400",
        "stealth": "1.75",
        "speed_forward": "18",
        "weight": "1250",
        "refund": "7:00",
    },
    112: {  # VDV AGS-30（Cost 50）
        "armor": "8",
        "health": "25",
        "soldier_count": "5",
        "optics": "1400",
        "speed_forward": "18",
        "weight": "625",
        "refund": "4:15",
    },
    374: {  # Su-25T（固定翼 8 格）
        "armor": "30",
        "health": "20",
        "optics": "6000",
        "stealth": "1",
        "speed_forward": "540",
        "turn_radius": "510",
        "fuel": "270",
        "refund": "13:17",
    },
    7: {  # Mi-8MTV-5（**9 格**，超过 8 -> 间距压缩）
        "armor": "10",
        "health": "15",
        "heavy_lift": "4000",
        "seats": "21",
        "optics": "1200",
        "stealth": "1.25",
        "speed_forward": "230",
        "agility": "100",
    },
    90: {  # BMD-2（ArmorValue=0 -> 第一格是生命，不是护甲）
        "health": "14",
        "seats": "5",
        "optics": "1200",
        "stealth": "1",
        "speed_forward": "60",
        "speed_back": "20",
        "weight": "8500",
        "refund": "5:52",
    },
    92: {  # 9K51 Grad（**7 格**：没有护甲、没有座位）
        "health": "10",
        "optics": "1000",
        "stealth": "1",
        "speed_forward": "80",
        "speed_back": "20",
        "weight": "14000",
        "refund": "16:07",
    },
}


@pytest.fixture(scope="module")
def data():
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    # 用产品自带的降级词典：标签与枚举中文都是**本版游戏实测值**，
    # 而且不依赖本机装没装游戏（测试要可复现，不能靠游戏包在不在）。
    return CardData(instance, Localization(entries=BUILTIN_LABELS))


# --------------------------------------------------------------------------- #
# 数值行：与截图逐格对照
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("unit_id", sorted(STAT_SAMPLES))
def test_stat_values_match_the_screenshots(data, unit_id: int) -> None:
    card = build_unit_card(data, unit_id)
    got = {stat.key: stat.value for stat in card.stats}
    for key, want in STAT_SAMPLES[unit_id].items():
        assert got.get(key) == want, f"[{unit_id}] {key}: 算得 {got.get(key)}，截图 {want}"


def test_grid_size_is_decided_per_stat_not_per_unit_type(data) -> None:
    """格数由逐条条件决定 —— 同为载具，BMD-2 是 8 格、Grad 是 7 格。

    Grad 是**压根没有"座位"这一格**（GetInfantrySlots() == 0），不是"值为 0 就不显示"。
    """
    assert len(build_unit_card(data, 90).stats) == 8
    assert len(build_unit_card(data, 92).stats) == 7
    assert "seats" not in {stat.key for stat in build_unit_card(data, 92).stats}
    # Grad 的 ArmorValue = 0 -> 没有护甲行，第一格是生命
    assert [stat.key for stat in build_unit_card(data, 92).stats][0] == "health"


def test_helicopter_has_nine_cells_and_compressed_spacing(data) -> None:
    """Mi-8 有 9 格，超过 InfocardConfig.MAX_STATS_WITH_INITIAL_SPACING=8 -> 间距 14 压到 4。"""
    card = build_unit_card(data, 7)
    assert len(card.stats) == 9 > MAX_STATS_PER_LINE
    assert card.stat_spacing == STAT_SPACING_TIGHT
    assert build_unit_card(data, 645).stat_spacing == STAT_SPACING_WIDE


def test_stat_order_follows_set_unit_statistic(data) -> None:
    """顺序照抄 UnitInfoCard.SetUnitStatistic：护甲 -> 生命 -> ... -> 撤回。"""
    keys = [stat.key for stat in build_unit_card(data, 645).stats]
    assert keys == [
        "armor",
        "health",
        "soldier_count",
        "optics",
        "stealth",
        "speed_forward",
        "weight",
        "refund",
    ]


# --------------------------------------------------------------------------- #
# 视野：选项改写之后再取 Max
# --------------------------------------------------------------------------- #


def test_optics_is_rewritten_by_the_default_option(data) -> None:
    """BMD-2 的两条传感器是 600 / 700，但卡片是 **1200** 不是 1400：
     选项 183 的 MainSensorId=2 把 unit.Sensors **整个替换**成 [600]。

    "取第一条"在只有一条传感器时与 Max 同解 —— 上一轮就是被这个巧合骗了。
    """
    assert optics_triple(data, 90)[0] == 1200.0
    assert len(effective_sensors(data, 90)) == 1
    # Su-25T：基础表是高空 3000 / 6000，选项 1661 换成 [3000]，CategoryType=6 取高空 -> 6000
    assert optics_triple(data, 374)[2] == 6000.0
    # 645 没有选项，基础表原样 -> 700 × 2
    assert optics_triple(data, 645)[0] == 1400.0


def test_optics_multiplier_comes_from_config(data) -> None:
    """×2 是 InfocardConfig.EffectiveRangeMultiplier，不是硬编码的 2。"""
    from ba_mod_maker.app.viewmodel.unit_card import EFFECTIVE_RANGE_MULTIPLIER

    assert EFFECTIVE_RANGE_MULTIPLIER == 2.0
    assert optics_triple(data, 645)[0] == 700.0 * EFFECTIVE_RANGE_MULTIPLIER


# --------------------------------------------------------------------------- #
# 重量：步兵是算出来的
# --------------------------------------------------------------------------- #


def test_infantry_weight_is_125_times_the_squad(data) -> None:
    """LoadUnits 里 imul eax,[rax+18h],7Dh：Weight = 班成员数 × 125。

    数据库里步兵的 Weight 列是 **1**，卡片上的 625 是这里算出来的 ——
    拿 Units.Weight 直读会得到 1（_work/verify_card_formulas.py 之前就是这么算的）。
    """
    assert INFANTRY_WEIGHT_PER_MEMBER == 125
    assert card_weight(data, 645) == (625, True)
    assert card_weight(data, 111) == (1250, True)
    assert data.t("Units").i(data.unit(645), "Weight") == 1  # 原列确实是 1
    # 载具直读原列
    assert card_weight(data, 90) == (8500, False)


# --------------------------------------------------------------------------- #
# 座位：条件函数不是"值非零"
# --------------------------------------------------------------------------- #


def test_infantry_slots_condition(data) -> None:
    assert get_infantry_slots(data, 90) == 5
    assert get_infantry_slots(data, 7) == 21
    assert get_infantry_slots(data, 92) == 0  # 压根没这一格
    assert get_infantry_slots(data, 645) == 0  # 步行兵先被 Type==2 挡掉
    assert get_infantry_slots(data, 374) == 0


# --------------------------------------------------------------------------- #
# 弹药数：Σ × HUDMultiplier
# --------------------------------------------------------------------------- #


def test_ammo_display_is_sum_times_hud_multiplier(data) -> None:
    """AK-74RMO 那一格：5 个人各 80 发 -> Σ=400，HUDMultiplier=3 -> 1200。

    ceil(MagazineSize/10) 那套是**拟合**（MagazineSize=30 恰好也等于 3），已证伪：
    Grad 的 MagazineSize=40，旧式子给 160，真值 40。
    """
    groups = weapon_groups(data, 645)
    ak = next(group for key, group in groups.items() if key == 361)
    card = build_unit_card(data, 645)
    row = next(w for w in card.weapons if w.group_key == 361)
    assert len(ak) == 5  # 5 个人各一把
    assert row.count == 5  # 行内的 x5
    assert row.ammo[0].raw_total == 400
    assert row.ammo[0].multiplier == 3
    assert row.ammo[0].count == 1200


def test_grad_ammo_is_not_the_ceil_formula(data) -> None:
    """反例：Grad 122mm 的 MagazineSize=40，显示值是 40（Σ=40 × HUDMultiplier 1）。"""
    card = build_unit_card(data, 92)
    row = card.weapons[0]
    assert row.ammo[0].count == 40
    assert row.ammo[0].multiplier == 1


def test_weapon_kind_uses_the_real_enum_mapping(data) -> None:
    """WeaponType -> ui_enum_weapontype_* 是跳转表，不是成员名的小写。"""
    from ba_mod_maker.app.viewmodel.unit_card import WEAPON_TYPE_KEYS

    assert WEAPON_TYPE_KEYS[30] == "autorifle"  # Autorifle=30
    assert WEAPON_TYPE_KEYS[62] == "rpg"  # RPGReloadable=62
    assert WEAPON_TYPE_KEYS[60] == "gl"  # GrenadeLauncher=60
    assert WEAPON_TYPE_KEYS[26] == "machinegun_light"  # LightMachineGun=26


# --------------------------------------------------------------------------- #
# 撤回三格
# --------------------------------------------------------------------------- #


def test_refund_times_match_the_tooltip(data) -> None:
    """645 的 tooltip 三格：毫发无损 0:33 / 弹药耗尽 0:33 / 阵亡后 6:30。

    ❗ 上一轮把毫发无损读成 0:34 并去找"隐藏 +1"，那是**读图错误**（用户复核）。
    """
    cost = card_cost(data, 645)
    assert fmt_time(purchase_delay(data, 645, 2, cost=cost)) == "0:33"
    assert fmt_time(purchase_delay(data, 645, 4, cost=cost)) == "0:33"
    assert fmt_time(purchase_delay(data, 645, 1, cost=cost)) == "6:30"
    # 111 与 645 的补给剖面完全不同却都落在同一档 —— 因为那一项被乘了 0
    cost111 = card_cost(data, 111)
    assert fmt_time(purchase_delay(data, 111, 2, cost=cost111)) == "0:34"
    assert fmt_time(purchase_delay(data, 112, 4, cost=card_cost(data, 112))) == "0:33"


def test_resupply_uses_deduplicated_fake_turrets(data) -> None:
    """补给的 count 集合是 **unit.Turrets**（步兵=按武器去重），不是"每人一把"。

    卡片上那排武器的弹药数是"按人"的，这一条却是"按武器去重"的 ——
    两者取的是不同对象（SquadMembers vs unit.Turrets），反直觉但已核实。
    """
    max_resupply, total_cost, _planes = ammo_resupply(data, 645)
    assert round(total_cost, 3) == 170.0  # AK 80 + RPG 3+3 + GP34 24
    assert max_resupply > 0


# --------------------------------------------------------------------------- #
# 成本与选项图（含变体单位）
# --------------------------------------------------------------------------- #


def test_cost_includes_the_selected_options(data) -> None:
    """成本 = OriginalCost + Σ(已选选项 Cost)。

    Mi-8 数据库 70，截图那一局选了「2xGSh23L」(+25) -> 95。
    """
    assert card_cost(data, 645) == 60.0
    assert card_cost(data, 374) == 295.0  # 65 + 0+80+100+25+25
    assert card_cost(data, 92) == 125.0  # 100 + 25
    assert card_cost(data, 7) == 70.0  # 缺省是"空挂架"
    assert card_cost(data, 7, {89: 248}) == 95.0  # 选了 2xGSh23L 才是截图那个 95


def test_variant_unit_borrows_the_parent_modification_group(data) -> None:
    """645 自己的 Modifications 是**空的**，它的「小队装备配置」来自单位 108 的 743。

    机制：option 3129 的 ReplaceUnitId = 645 —— 玩家在 108 上选"温压火箭弹"，
    单位就被换成 645。编辑器不做这条图，变体单位的卡片就是空的。
    """
    assert data.modifications_of(645) == ()
    card = build_unit_card(data, 645)
    assert len(card.options) == 1
    group = card.options[0]
    assert group.modification_id == 743
    assert group.owner_unit_id == 108
    assert group.from_variant is True
    current = next(choice for choice in group.choices if choice.is_current)
    assert current.option_id == 3129  # 温压火箭弹
    assert current.cost == 0
    # 同组的其它几项是别的单位（改型 = 换单位，不是换装备）
    assert any(choice.replaces_unit_id for choice in group.choices)


def test_options_carry_their_loc_keys(data) -> None:
    """Modifications.UIName 是本地化键（驼峰），要按大小写不敏感去查。"""
    mods = data.t("Modifications")
    assert mods.s(mods.by_id(743), "UIName") == "Custom_Slot_Squad_loadout"


# --------------------------------------------------------------------------- #
# 未解的留钩子，不填假值
# --------------------------------------------------------------------------- #


def test_pending_detail_rows_are_empty_now(data) -> None:
    """待解清单**现在是空的** —— 上一轮挂着的四条都解出来了（见各行的用例）。

    钩子本身留着：以后遇到解不出来的行，仍走 `PENDING_DETAIL_ROWS`（卡片末尾一行小字
    说明"还没解、先不显示"），**不许填假值**。
    """


def test_card_carries_the_edit_targets(data) -> None:
    """每一格都带落点：编辑要按 CellRef 写（core/edit/changeset.py 按 Id 寻址）。"""
    card = build_unit_card(data, 645)
    stats = {stat.key: stat for stat in card.stats}
    assert str(stats["weight"].note) != ""  # 算出来的值要说明是怎么来的
    assert str(stats["optics"].value_ref) == "Sensors#23.OpticsGround"
    assert str(stats["optics"].link_ref) == "SensorUnits#1401.SensorId"
    assert str(stats["armor"].value_ref) == "Armors#281.ArmorValue"
    assert str(stats["armor"].link_ref) == "UnitArmors#1185.ArmorId"
    assert str(stats["seats"].value_ref) if "seats" in stats else True
    # 卡片上那个金色数字来自 base_cost()：OriginalCost 非 0 时才用它，否则用 Cost。
    # 实测全 540 行 OriginalCost 都是 0（占位列），所以落点是 **Cost** ——
    # 指到 OriginalCost 会让编辑器改了一格而卡片纹丝不动（单位编辑器本轮发现）。
    assert str(card.cost_ref) == "Units#645.Cost"


def test_title_uses_hud_name(data) -> None:
    """卡片抬头用 HUDName：645 的 Name 是 VDV Dsh 2，游戏卡片上写的是 VDV Shturm。"""
    assert build_unit_card(data, 645).title == "VDV Shturm"


def test_missing_unit_returns_none(data) -> None:
    assert build_unit_card(data, 999999) is None


# --------------------------------------------------------------------------- #
# 编成与携弹的编辑素材（加枪 / 换弹种 / 加人减人，零 Qt 那一半）
# --------------------------------------------------------------------------- #


def test_squad_loadout_lists_members_and_the_weapon_pool(data) -> None:
    card = build_unit_card(data, 645)
    loadout = card.loadout
    assert loadout is not None
    assert loadout.size == 5
    assert [m.index for m in loadout.members] == [1, 2, 3, 4, 5]
    # 候选是该单位 SquadWeapons 那一批（不是 804 种武器的全表）
    assert {c.weapon_id for c in loadout.weapon_choices} == {32, 122, 129, 137, 162}
    assert str(loadout.members[0].ref("PrimaryWeaponId")) == "SquadMembers#1466.PrimaryWeaponId"
    assert str(loadout.members[0].row_ref) == "SquadMembers#1466.Id"


def test_non_infantry_has_no_loadout(data) -> None:
    """载具的人员走炮塔与运输，不是班组成员那一套。"""
    assert build_unit_card(data, 90).loadout is None
    assert squad_loadout(data, 374) is None


def test_new_member_copies_the_last_member(data) -> None:
    """加一名成员：复制最后一名的装备，Id 留给 add_row 去取。"""
    card = build_unit_card(data, 645)
    values = new_member_values(data, card.loadout)
    assert values["UnitId"] == 645
    assert values["PrimaryWeaponId"] == 122
    assert values["SpecialWeaponId"] == 0
    assert values["Id"] is None
    assert "DeathPriority" in values


def test_ammo_choices_are_limited_to_what_the_weapon_uses(data) -> None:
    """换弹种的候选 = 这把武器在库里用过的弹种（去掉库外的，免得"改了不显示"）。"""
    card = build_unit_card(data, 645)
    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    assert {c.ammo_id for c in rpg.ammo_choices} == {104, 906}
    assert all(c.owned for c in rpg.ammo_choices)
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    assert {c.ammo_id for c in ak.ammo_choices} == {361}


def test_ammo_refs_point_at_both_columns(data) -> None:
    """两个落点分开：换弹种写 AmmunitionId、改携弹量写 Quantity（写错科目是踩过的坑）。"""
    card = build_unit_card(data, 645)
    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    line = next(a for a in rpg.ammo if a.ammo_id == 104)
    assert [ref.column for ref in line.refs] == ["AmmunitionId"]
    assert [ref.column for ref in line.quantity_refs] == ["Quantity"]
    assert line.refs[0].row_id == line.quantity_refs[0].row_id


# --------------------------------------------------------------------------- #
# 展开态的详情行（这一轮解出来的那几条）
# --------------------------------------------------------------------------- #


def test_ammo_detail_rows_are_the_solved_ones(data) -> None:
    """AK-74RMO 的 5.45x39mm：类型 / 有效射程 / 补给值（**没有**伤害）。"""
    card = build_unit_card(data, 645)
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    rows = {row.key: row for row in ak.ammo[0].detail}
    assert rows["trajectory"].value == "直射弹药"
    assert rows["trajectory"].evidence == "已确认"
    # GroundRange 250 × 2（InfocardConfig.EffectiveRangeMultiplier）
    assert rows["range"].value == "500 m"
    assert rows["supply_cost"].value == "0.1 kg"
    # 步枪在白名单里 -> 游戏显示的是【每秒伤害】而不是【伤害】（两者互斥）
    assert "damage" not in rows
    # 实机那张卡上就是这两行：每秒伤害 0.4/s、武器散布 V: 2m / H: 9 m
    assert rows["damage_per_second"].value == "0.4/s"
    assert rows["dispersion"].value == "V: 2m / H: 9 m"
    assert rows["dispersion"].evidence == "已确认"


def test_damage_row_only_for_non_dps_weapons(data) -> None:
    """【伤害】与【每秒伤害】互斥（ISIL 366-427）：榴弹发射器有伤害、步枪没有。"""
    from ba_mod_maker.app.viewmodel.unit_card import DPS_WEAPON_TYPES

    assert 1 in DPS_WEAPON_TYPES and 60 not in DPS_WEAPON_TYPES
    card = build_unit_card(data, 645)
    gp34 = next(w for w in card.weapons if w.weapon_id == 137)
    rows = {row.key: row for row in gp34.ammo[0].detail}
    # GP-34：Damage 2 -> RoundToNearestMultiple(2, 0.05)
    assert rows["damage"].value == "2"


def test_damage_per_second_matches_the_game_screenshots(data) -> None:
    """每秒伤害 = RoundToNearestMultiple(RateOfFireSecond × Damage, 0.05) + "/s"。

    样本就是用户给的那两张 VDV Metis-M（单位 132）的实机卡：
    AK-74RMO 实机 **0.4/s**、PKP-M 实机 **1.1/s** —— 逐字相同。
    上一轮判它"输入列对不上、先不实现"，根因是**漏了 0.05 那一步取整**：
    0.375 / 0.05 = 7.5 -> 取整 8 -> 0.4（PKP 是 1.089 -> 21.78 -> 22 -> 1.1）。
    """
    card = build_unit_card(data, 132)
    ak = next(w for w in card.weapons if w.label == "AK-74RMO")
    pkp = next(w for w in card.weapons if w.label == "PKP-M")
    assert {row.key: row.value for row in ak.ammo[0].detail}["damage_per_second"] == "0.4/s"
    assert {row.key: row.value for row in pkp.ammo[0].detail}["damage_per_second"] == "1.1/s"
    # 不在白名单里的武器给【伤害】：实机 Metis-M 那一格是 11
    metis = next(w for w in card.weapons if w.label == "Metis-M")
    metis_rows = {row.key: row.value for row in metis.ammo[0].detail}
    assert metis_rows["damage"] == "11"
    assert "damage_per_second" not in metis_rows


def test_dispersion_is_the_two_radii_times_two(data) -> None:
    """武器散布 = "V: {垂直 × 2}m / H: {水平 × 2} m"。

    实机 AK-74RMO 那一行是 `V: 2m / H: 9 m`，而库里 5.45x39mm 正是 (1.0, 4.5) ——
    两个半径都走 InfocardConfig.EffectiveRangeMultiplier（和射程同一个 ×2）。
    """
    card = build_unit_card(data, 132)
    ak = next(w for w in card.weapons if w.label == "AK-74RMO")
    row = {r.key: r for r in ak.ammo[0].detail}["dispersion"]
    assert row.value == "V: 2m / H: 9 m"
    assert row.evidence == "已确认"


def test_compact_cells_match_the_game_compact_card(data) -> None:
    """简略模式那 4 列必须与实机**简略卡**逐格一致（单位 132 那三行）。

    实机：AK-74RMO 500m / x360 / 7 / 0.4-s；PKP-M 600m / x1200 / 10 / 1.1-s；
    Metis-M 1100m / x5 / 800 / 11。
    """
    from ba_mod_maker.app.viewmodel.unit_card import COMPACT_COLUMNS

    card = build_unit_card(data, 132)
    assert [weapon.label for weapon in card.weapons] == ["AK-74RMO", "PKP-M", "Metis-M"]
    assert [[cell.text for cell in weapon.compact] for weapon in card.weapons] == [
        ["500 m", "×360", "7", "0.4/s"],
        ["600 m", "×1200", "10", "1.1/s"],
        ["1100 m", "×5", "800", "11"],
    ]
    for weapon in card.weapons:
        assert [cell.key for cell in weapon.compact] == list(COMPACT_COLUMNS), "列序必须固定"
        assert all(cell.tooltip for cell in weapon.compact), "每一格都要有悬浮说明"


def test_weapon_targets_are_the_union_of_its_ammo(data) -> None:
    """武器那一排目标图标是**所有弹种的并集**，不是第一个弹种的。

    只取第一个会漏：BMD-2（90）的 2А42-30mm 是 AP 只有【载具】、HE 有【步兵/载具/直升机】，
    只画第一个那张卡就写着"打不了步兵"（用户报的"可瞄准目标的图标没显示全"）。
    """
    card = build_unit_card(data, 90)
    cannon = next(w for w in card.weapons if "2А42" in w.label or "2A42" in w.label)
    assert [t.key for t in cannon.ammo[0].targets] == ["vehicle"], "样本变了，这条用例要重挑"
    assert [t.key for t in cannon.targets] == ["infantry", "vehicle", "helicopter"]
    # 顺序按游戏那排来（步兵 -> 载具 -> 直升机 -> 固定翼 -> 导弹）
    assert [t.key for t in cannon.targets] == sorted(
        (t.key for t in cannon.targets),
        key=lambda k: ("infantry", "vehicle", "helicopter", "aircraft", "missiles").index(k),
    )


def test_compact_cells_reuse_the_detail_rows(data) -> None:
    """简略模式只允许"取末段 / 取首个数字"这种削法，值必须与详细模式同源。"""
    card = build_unit_card(data, 645)
    checked = 0
    for weapon in card.weapons:
        if not weapon.compact or not weapon.ammo:
            continue
        by_key = {row.key: row for row in weapon.ammo[0].detail}
        cells = {cell.key: cell for cell in weapon.compact}
        range_row = by_key.get("range") or by_key["range_ground"]
        assert cells["range"].text == range_row.value.split(" - ")[-1]
        penetration = by_key["penetration"].value.split(" mm")[0].split(" - ")[0]
        assert cells["penetration"].text == penetration
        assert cells["ammo"].text == f"×{weapon.ammo[0].count}"
        power = by_key.get("damage_per_second") or by_key["damage"]
        assert cells["power"].text == power.value
        checked += 1
    assert checked >= 3, "样本太少"


def test_weapon_detail_rows(data) -> None:
    """武器自己那几行：瞄准时间（都出）/ 弹匣容量·装填时间（只走那一支的武器出）。"""
    card = build_unit_card(data, 645)
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    rows = {row.key: row.value for row in ak.detail}
    assert rows["aim_time"] == "1 sec"  # AimTimeMin == AimTimeMax -> 一个数
    # 步枪走的是【射速】那一支（弹匣容量/装填时间不发射）
    assert "magazine" not in rows and "reload_time" not in rows
    # RateOfFire(weapon,1)=0.25/s ×60 = 15 rpm，×第一个弹种的 HUDMultiplier(3) = 45，按 5 取整
    assert rows["rate_of_fire"] == "45"

    gp34 = next(w for w in card.weapons if w.weapon_id == 137)
    gp = {row.key: row.value for row in gp34.detail}
    assert gp["aim_time"] == "2 - 3 sec"  # min != max -> 区间
    assert "magazine" not in gp and "reload_time" not in gp, "GL(60) 走射速那一支"

    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    rp = {row.key: row.value for row in rpg.detail}
    assert rp["aim_time"] == "1 - 2 sec"
    assert rp["reload_time"] == "5 - 6 sec"  # MagazineSize = 1 -> 只有装填时间
    assert "magazine" not in rp


def test_magazine_row_only_for_the_other_branch(data) -> None:
    """维赫尔(Vikhr)：MagazineSize 8、首个弹种是导弹（非直射）-> 出弹匣容量。"""
    card = build_unit_card(data, 374)
    vikhr = next(w for w in card.weapons if w.label == "Vikhr")
    rows = {row.key: row.value for row in vikhr.detail}
    assert rows["magazine"] == "8"
    # 固定翼上装填时间只给航炮（116），维赫尔是 ATGM -> 不出
    assert "reload_time" not in rows


def test_float32_default_format_matches_dotnet() -> None:
    """String.Format("{0} sec", float) 走的是默认格式（最短可往返），不是 "0.##"。"""
    from ba_mod_maker.app.viewmodel.unit_card import fmt_float32

    assert fmt_float32(1.0) == "1"
    assert fmt_float32(0.2) == "0.2"
    assert fmt_float32(0.6666667) == "0.6666667"
    assert fmt_float32(2.5) == "2.5"


def test_zero_ranges_are_skipped(data) -> None:
    """R-73 只有低空/高空射程（GroundRange = 0 那一格不发射）。"""
    card = build_unit_card(data, 374)
    r73 = next(w for w in card.weapons if w.label == "R-73")
    keys = {row.key for row in r73.ammo[0].detail}
    assert "range_low" in keys and "range_high" in keys
    assert "range_ground" not in keys


def test_range_prefix_comes_from_minimal_range(data) -> None:
    """GP-34：MinimalRange=6 -> 前缀 "12 - "，GroundRange=200 -> "400 m"。"""
    card = build_unit_card(data, 645)
    gp34 = next(w for w in card.weapons if w.weapon_id == 137)
    rows = {row.key: row for row in gp34.ammo[0].detail}
    assert rows["range"].value == "12 - 400 m"


def test_anti_air_ammo_gets_three_range_rows(data) -> None:
    """能打空中的弹药（TargetType 含 Helicopter/Aircraft）分三条射程，且标成强假设。"""
    card = build_unit_card(data, 645)
    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    rows = {row.key: row for row in rpg.ammo[0].detail}
    assert rows["range_ground"].value == "500 m"
    assert rows["range_low"].value == "150 m"
    assert rows["range_low"].evidence == "强假设"
    assert "range" not in rows, "分三条的时候不该再有一条合并的有效射程"


def test_resupply_time_only_shows_above_one_second(data) -> None:
    """补给时间只在 ResupplyTime > 1.0 时发射（AK 那条是 0.4 -> 不显示）。"""
    card = build_unit_card(data, 645)
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    assert "resupply_time" not in {row.key for row in ak.ammo[0].detail}
    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    assert "resupply_time" in {row.key for row in rpg.ammo[0].detail}


def test_aircraft_does_not_get_the_supply_value_row(data) -> None:
    """补给值那一行在固定翼分支里换成了别的（值没钉死），所以固定翼上不给。"""
    card = build_unit_card(data, 374)
    if not card.weapons or not card.weapons[0].ammo:
        pytest.skip("样本单位这一版没有弹药")
    keys = {row.key for row in card.weapons[0].ammo[0].detail}
    assert "supply_cost" not in keys


def test_round_to_nearest_multiple() -> None:
    from ba_mod_maker.app.viewmodel.unit_card import round_to_multiple

    assert round_to_multiple(1.5) == 1.5
    assert round_to_multiple(1.53) == 1.55
    assert round_to_multiple(1.52) == 1.5
    assert round_to_multiple(0) == 0


def test_the_four_formerly_pending_rows_are_real_now(data) -> None:
    """那四条以前挂在 PENDING_DETAIL_ROWS 里，现在都画得出来（各自的用例在后面）。"""
    card = build_unit_card(data, 645)
    labels = {row.label for w in card.weapons for a in w.ammo for row in a.detail}
    solved = ("类型", "有效射程", "每秒伤害", "伤害", "穿深", "武器散布", "补给值", "补给消耗")
    for label in solved:
        assert label in labels


def test_card_works_when_the_source_names_the_table_like_dbzip(tmp_path) -> None:
    """数据源把那张表叫 `FlyPresets`（db.zip 那一套）时，固定翼卡片照样能装配。

    实踩（用户报"战机侧的东西都显示不出来"）：他打开的数据源里**没有** `PlaneFlyPresets`
    （按文件名建表的数据源才有这个名字），于是 `fly_preset` 一查就 KeyError ——
    而固定翼的速度与回转半径两格都挂在那一行上，**整张战机卡片**就装配不出来，
    界面停在"上一个单位"上。

    这里就地把数据源造成"db.zip 那种命名"：表文件改名 + schema 换键，
    再要求两种数据源装出来的卡片**逐格一致**。
    """
    import json
    import shutil

    source = DATASET_ROOT / INSTANCE_ID
    target = tmp_path / INSTANCE_ID
    target.mkdir()
    for path in source.iterdir():
        if not path.is_file():
            continue
        name = "FlyPresets.json" if path.name == "PlaneFlyPresets.json" else path.name
        shutil.copy2(path, target / name)
    schema = json.loads((DATASET_ROOT / "_schema.json").read_text(encoding="utf-8"))
    schema["FlyPresets"] = schema.pop("PlaneFlyPresets")
    (tmp_path / "_schema.json").write_text(json.dumps(schema), encoding="utf-8")

    dataset = load_dataset(tmp_path)
    alias_instance = dataset.instance(INSTANCE_ID)
    assert "PlaneFlyPresets" not in alias_instance.table_names, "样本没造对"
    assert "FlyPresets" in alias_instance.table_names

    real = build_unit_card(CardData(load_dataset(DATASET_ROOT).instance(INSTANCE_ID)), 44)  # Su-57
    alias = build_unit_card(CardData(alias_instance), 44)
    assert real is not None and alias is not None
    real_stats = [(s.key, s.value) for s in real.stats]
    assert [(s.key, s.value) for s in alias.stats] == real_stats
    assert [(w.label, w.count) for w in alias.weapons] == [(w.label, w.count) for w in real.weapons]
    speed = {s.key: s.value for s in alias.stats}
    assert speed["speed_forward"] != "0", "固定翼那一格要真的从飞行预设里读出来"

    # 反过来的那种目录源也要能读：schema 用**规范名**、文件却仍是**条目名**
    # （`PlaneFlyPresets.json`）——读取时按条目名回退。
    flipped = tmp_path / "flipped"
    (flipped / INSTANCE_ID).mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, flipped / INSTANCE_ID / path.name)
    (flipped / "_schema.json").write_text(json.dumps(schema), encoding="utf-8")
    flipped_instance = load_dataset(flipped).instance(INSTANCE_ID)
    assert "FlyPresets" in flipped_instance.table_names
    assert not (flipped / INSTANCE_ID / "FlyPresets.json").exists(), "样本要按条目名放文件"
    assert flipped_instance.table("FlyPresets").row_count > 0, "按条目名回退那一步没生效"
    flipped_card = build_unit_card(CardData(flipped_instance), 44)
    assert [(s.key, s.value) for s in flipped_card.stats] == real_stats


def test_resupply_cost_row(data) -> None:
    """补给消耗 = SupplyCost × AmmoResupplyModifier + " pts"，格式照实机（"0.##"）。

    AmmoResupplyModifier = supplyConfig.GetAmmoResupplyMultiplier()（= 0.04）；
    形式上是 `Single.ToString(值, "0.##")`，所以 0.1 kg 的步枪弹会显示成 **0 pts** ——
    那是实机的写法，精确值在 tooltip 里。
    """
    card = build_unit_card(data, 645)
    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    pg7 = {row.key: row for row in rpg.ammo[0].detail}
    assert pg7["supply_cost"].value == "25 kg"
    assert pg7["resupply_cost"].value == "1 pts"  # 25 × 0.04 = 1.0
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    ak_rows = {row.key: row for row in ak.ammo[0].detail}
    assert ak_rows["resupply_cost"].value == "0 pts"  # 0.1 × 0.04 = 0.004 -> 两位小数
    assert "0.004" in ak_rows["resupply_cost"].hint, "精确值要留在悬浮提示里"


def test_ignore_cover_row(data) -> None:
    """无视掩体 = IgnoreCover × 100 + "%"，只在 > 0 时发。"""
    card = build_unit_card(data, 2)
    m256 = next(w for w in card.weapons if w.weapon_id == 266)
    heat = {row.key: row for row in m256.ammo[0].detail}
    assert heat["ignore_cover"].value == "20%"  # IgnoreCover 0.2
    # 步枪弹不无视掩体 -> 一条都没有
    rifle = build_unit_card(data, 645).weapons[0]
    assert "ignore_cover" not in {row.key for row in rifle.ammo[0].detail}


def test_aps_target_row(data) -> None:
    """可被主动防御系统拦截：【值是 "+"】，且**只有 CanBeIntercepted 为真**才发。

    这一列是 bool —— 用 `rows.i()` 读会永远拿 0，这一行就永远不出现（实测踩过）。
    """
    card = build_unit_card(data, 15)  # M2A2 Bradley，带 TOW-2A
    tow = next(w for w in card.weapons if w.label.startswith("TOW-2"))
    rows = {row.key: row for row in tow.ammo[0].detail}
    assert rows["aps_target"].value == "+"
    ak = build_unit_card(data, 645).weapons[0]
    assert "aps_target" not in {row.key for row in ak.ammo[0].detail}


def test_salvo_length_row(data) -> None:
    """齐射时长：只对榴弹炮(111)/迫击炮(112)/多管火箭炮(113)发，值是"短 / 中 / 长"三档发数。"""
    grad = build_unit_card(data, 92).weapons[0]
    assert grad.label == "Grad"
    assert {row.key: row for row in grad.detail}["salvo_length"].value == "10 / 20 / 40"
    rifle = build_unit_card(data, 645).weapons[0]
    assert "salvo_length" not in {row.key for row in rifle.detail}


def test_supply_value_comes_before_resupply_time(data) -> None:
    """行序照实机反汇编：【补给值】在【补给时间】之前（ISIL 3992 vs 4075）。"""
    rpg = next(w for w in build_unit_card(data, 645).weapons if w.weapon_id == 32)
    keys = [row.key for row in rpg.ammo[0].detail]
    assert keys.index("supply_cost") < keys.index("resupply_time")


def test_penetration_row_carries_the_armour_type(data) -> None:
    """穿深 = ground（≠min 时打区间）+ mm + 装甲类型后缀。

    动能/爆炸伤害**不是单独的行** —— 它们是这一格的类型后缀。
    """
    card = build_unit_card(data, 645)
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    rows = {row.key: row.value for row in ak.ammo[0].detail}
    # 5.45x39：PenetrationAtGroundRange=7 / PenetrationAtMinRange=15，装甲类型 Kinetic
    assert rows["penetration"] == "7 - 15 mm 动能伤害"

    rpg = next(w for w in card.weapons if w.weapon_id == 32)
    pg7 = {row.key: row.value for row in rpg.ammo[0].detail}
    # PG-7VL：500mm 破甲（现实里也是这个量级），两个穿深相同 -> 不打区间
    assert pg7["penetration"] == "500 mm 爆炸伤害"


def test_blast_radius_only_when_positive(data) -> None:
    """爆炸半径 = HealthAOERadius × 2 + " m"，为 0 就不发这一格。"""
    card = build_unit_card(data, 645)
    ak = next(w for w in card.weapons if w.weapon_id == 122)
    assert "blast_radius" not in {row.key for row in ak.ammo[0].detail}
    gp34 = next(w for w in card.weapons if w.weapon_id == 137)
    rows = {row.key: row.value for row in gp34.ammo[0].detail}
    assert rows["blast_radius"] == "12 m"  # HealthAOERadius 6 × 2


# --------------------------------------------------------------------------- #
# 全量扫描：1018 个单位都不许崩、落点都要真的存在
# --------------------------------------------------------------------------- #


def test_every_unit_builds_a_card_without_blowing_up() -> None:
    """两个实例的**全部**单位都过一遍。

    这不是"再跑几个样本"—— 7 个截图样本只能证明公式；这一条证明的是
    **任何一张卡都不会崩**（缺装甲、缺传感器、缺班组成员、Type 说不清的单位都有）。
    顺带把耗时钉住：整表 1018 个约 4 秒，不能退化成秒级/个。
    """
    import time

    dataset = load_dataset(DATASET_ROOT)
    localization = Localization(entries=BUILTIN_LABELS)
    checked = 0
    start = time.perf_counter()
    for path_id in ("52085", "52082"):
        data = CardData(dataset.instance(path_id), localization)
        units = data.t("Units")
        id_index = units.index_of("Id")
        kind_index = units.index_of("Type")
        for row in units.all():
            unit_id = row[id_index]
            card = build_unit_card(data, unit_id)
            checked += 1
            assert card is not None, f"[{path_id}] #{unit_id} 装配不出来"
            assert card.title.strip(), f"[{path_id}] #{unit_id} 没标题"
            assert card.stats, f"[{path_id}] #{unit_id}（Type={row[kind_index]}）一个数值格都没有"
            assert 5 <= len(card.stats) <= 10, f"[{path_id}] #{unit_id} 有 {len(card.stats)} 格"
            assert card.cost >= 0
            keys = [stat.key for stat in card.stats]
            assert keys == [spec.key for spec in STAT_SPECS if spec.key in set(keys)], (
                f"[{path_id}] #{unit_id} 数值行顺序与 STAT_SPECS 不一致：{keys}"
            )
            if card.loadout is not None:
                assert card.loadout.size == int(
                    next(s.value for s in card.stats if s.key == "soldier_count")
                ), f"[{path_id}] #{unit_id} 班组成员数与人数格对不上"
    assert checked > 1000, f"只扫到 {checked} 个单位，数据源变了？"
    assert time.perf_counter() - start < 60.0


def test_every_edit_target_actually_exists() -> None:
    """卡片上每个"落点"都必须真的存在 —— 编辑是按它写的。

    落点错了，用户在卡片上改一格会拿到 TableError（或更糟：改到别人的行上）。
    这一条把全部 1018 个单位的所有落点都验一遍。
    """
    dataset = load_dataset(DATASET_ROOT)
    data = CardData(dataset.instance("52085"), Localization(entries=BUILTIN_LABELS))
    units = data.t("Units")
    id_index = units.index_of("Id")

    def check(ref, where: str) -> None:
        table = data.t(ref.table)
        assert table.has(ref.column), f"{where}：{ref} 的列不存在"
        assert table.by_id(ref.row_id) is not None, f"{where}：{ref} 的行不存在"

    checked = 0
    for row in units.all():
        unit_id = row[id_index]
        card = build_unit_card(data, unit_id)
        for stat in card.stats:
            if stat.value_ref is not None:
                check(stat.value_ref, f"#{unit_id} {stat.key}")
                checked += 1
            if stat.link_ref is not None:
                check(stat.link_ref, f"#{unit_id} {stat.key} 关联行")
                checked += 1
        for weapon in card.weapons:
            for ammo in weapon.ammo:
                for ref in (*ammo.refs, *ammo.quantity_refs):
                    check(ref, f"#{unit_id} 携弹行")
                    checked += 1
        if card.loadout is not None:
            for member in card.loadout.members:
                for column in ("PrimaryWeaponId", "SpecialWeaponId"):
                    check(member.ref(column), f"#{unit_id} 成员武器")
                    checked += 1
    assert checked > 5000, f"只验了 {checked} 个落点"


def test_ship_speed_is_marked_as_an_assumption(data) -> None:
    """船的前进速度**没有截图样本**：值取 MaxSpeedWater，但标成强假设。

    设计文档的数值行来源表里根本没有舰船那一行（只到载具/直升机/固定翼），
    所以这一格是"从数据语义推的"，不是"读出来的" —— 卡片上要能看出来。
    """
    zubr = build_unit_card(data, 139)  # Zubr：气垫船，水面 100
    speed = next(stat for stat in zubr.stats if stat.key == "speed_forward")
    assert speed.value == "100"
    assert speed.evidence == "强假设"

    # 载具那一格是截图复核过的
    bmd = build_unit_card(data, 90)
    assert next(s for s in bmd.stats if s.key == "speed_forward").evidence == "已确认"


def test_every_stat_carries_an_evidence_level(data) -> None:
    """每一格都有证据等级；全库扫一遍只允许舰船那几格是强假设。"""
    dataset = load_dataset(DATASET_ROOT)
    data2 = CardData(dataset.instance("52085"), Localization(entries=BUILTIN_LABELS))
    units = data2.t("Units")
    id_index = units.index_of("Id")
    kind_index = units.index_of("Type")
    assumed: list[int] = []
    for row in units.all():
        card = build_unit_card(data2, row[id_index])
        for stat in card.stats:
            assert stat.evidence in ("已确认", "强假设")
            if stat.evidence == "强假设":
                assumed.append(row[id_index])
                assert stat.key == "speed_forward" and row[kind_index] == 32, (
                    f"#{row[id_index]} 的 {stat.key} 标成了强假设，但只有舰船速度该这样"
                )
    assert assumed, "强假设那条断言没被执行到（舰船样本没了？）"


def test_rate_of_fire_row(data) -> None:
    """射速 = RoundToNearestMultiple(RateOfFire(weapon,1) × 第一个弹种的 HUDMultiplier, 5)。

    重武器那几个值（405 / 3125）量级是对的 —— 这条公式链是"发/分"。
    """
    card = build_unit_card(data, 90)  # BMD-2：2A42 + PKT
    bmd = {w.label: {r.key: r for r in w.detail} for w in card.weapons}
    assert bmd["2А42-30mm"]["rate_of_fire"].value == "260"
    assert bmd["PKT"]["rate_of_fire"].value == "405"
    assert bmd["PKT"]["rate_of_fire"].evidence == "强假设", "还没有截图样本对过，要标出来"

    su25 = build_unit_card(data, 374)
    gsh = next(w for w in su25.weapons if w.label == "GSh-2-30")
    assert {r.key: r.value for r in gsh.detail}["rate_of_fire"] == "3125"


def test_rate_of_fire_and_magazine_are_mutually_exclusive(data) -> None:
    """射速那一支与「弹匣容量+装填时间」那一支**不会同时出现**（ISIL 380-395）。"""
    for unit_id in (645, 90, 374, 92):
        card = build_unit_card(data, unit_id)
        for weapon in card.weapons:
            keys = {row.key for row in weapon.detail}
            assert not ({"rate_of_fire"} <= keys and {"magazine", "reload_time"} & keys), (
                f"#{unit_id} {weapon.label} 两支同时出现了：{keys}"
            )


def test_stealth_follows_the_option_override(data) -> None:
    """隐蔽值会被 Options.StealthOverride 盖掉 —— 这一列在数据库里，别只读 Units。

    全库 20 条选项带这个覆盖，其中 5 个单位的**默认**配置就生效：
    #21 Stryker RV（0.8 -> 0.666）、#177 Green Berets（0.333 -> 0.4）、
    #209/#215/#217（0.8 -> 1.0）。漏了它的后果是卡片上的隐蔽是错的。
    """
    card = build_unit_card(data, 21)
    stealth = next(stat for stat in card.stats if stat.key == "stealth")
    assert stealth.value == "1.5"  # 1 / 0.666 = 1.5015 -> 0.##
    assert stealth.value_ref is not None
    assert stealth.value_ref.table == "Options", "落点应该是那条选项，不是 Units"
    assert stealth.value_ref.column == "StealthOverride"

    # 没有覆盖的单位照旧读 Units.CurrentStealth
    plain = build_unit_card(data, 645)
    plain_stealth = next(stat for stat in plain.stats if stat.key == "stealth")
    assert plain_stealth.value == "1.75"
    assert plain_stealth.value_ref.table == "Units"
    assert plain_stealth.value_ref.column == "CurrentStealth"


# --------------------------------------------------------------------------- #
# 弹药的目标类型：那排小图标（AmmoInfoExpansionPanel.SetTargetTraits）
# --------------------------------------------------------------------------- #


def _ammo_targets(data, ammo_id: int):
    row = data.t("Ammunitions").by_id(ammo_id)
    assert row is not None, f"数据库里没有弹药 #{ammo_id}"
    return ammo_target_traits(data, row)


def test_ammo_targets_follow_the_target_type_bits(data) -> None:
    """位 -> (图标, 文案)。四个样本都是库里的真行（Ammunitions.TargetType）。

    | 弹药 | TargetType | 位 | 那排图标 |
    |---|---|---|---|
    | #1 120mm AP | 36 | Vehicle + Ship | 载具（**Ship 那位没有图标**） |
    | #2 120mm HEAT | 39 | Ground + Infantry + Vehicle + Ship | 步兵 + 载具 |
    | #18 Stinger | 24 | Helicopter + Aircraft | 直升机 + 飞机 |
    | #138 S-300V | 1936 | Aircraft + 四种导弹 | 飞机 + 导弹（全中合成一条） |

    ❗ 位名照 dump.cs TargetType：**1 是 Ground、2 才是步兵** —— 卡片上不画 Ground 与 Ship，
    因为 SetTargetTraits 里压根没有这两段 test（判据：那两处只有 4 + 4 个 test bit）。
    """
    cases = {
        1: (["vehicle"], ["目标 - 载具"]),
        2: (["infantry", "vehicle"], ["目标 - 步兵", "目标 - 载具"]),
        18: (["helicopter", "aircraft"], ["目标 - 直升机", "目标 - 飞机"]),
        138: (["aircraft", "missiles"], ["目标 - 飞机", "目标 - 导弹"]),
    }
    for ammo_id, (keys, labels) in cases.items():
        targets = _ammo_targets(data, ammo_id)
        assert [t.key for t in targets] == keys, f"#{ammo_id}"
        assert [t.label for t in targets] == labels, f"#{ammo_id}"


def test_ground_and_ship_bits_draw_nothing(data) -> None:
    """Ground(1) 与 Ship(32) 游戏里不画图标 —— 不是「漏了」，是那两段汇编不存在。

    #4（155mm 榴弹，TargetType == 1）与 #1（36 = Vehicle + Ship）是判据。
    """
    assert _ammo_targets(data, 4) == ()
    assert [t.key for t in _ammo_targets(data, 1)] == ["vehicle"], "Ship 那一位不该多出一个图标"


def test_missile_bits_merge_only_when_all_four_are_set(data) -> None:
    """四位全中 -> 一条【导弹】；只中一部分 -> 逐条列、共用导弹图标（ISIL 168-266）。"""
    merged = _ammo_targets(data, 138)
    missiles = next(t for t in merged if t.key == "missiles")
    assert missiles.label == "目标 - 导弹"

    partial = next(t for t in _ammo_targets(data, 465) if t.key == "missiles")
    assert partial.label.split(chr(10)) == ["目标 - 防空压制", "目标 - 巡航导弹"], partial.label
    assert partial.icon == missiles.icon, "两条走的是同一个 sprite（0x1E8）"


def test_every_ammo_target_uses_one_of_the_five_game_sprites(data) -> None:
    """全库扫一遍：每个图标都必须是那 5 个地址之一（别的都是拼错的路径）。"""
    ammo = data.t("Ammunitions")
    seen: set[str] = set()
    for row in ammo.all():
        for target in ammo_target_traits(data, row):
            assert target.icon in TARGET_ICON_KEYS.values(), target.icon
            assert target.label.strip(), f"弹药 #{ammo.i(row, chr(73) + chr(100))} 有一条没有文案"
            seen.add(target.key)
    assert seen == set(TARGET_ICON_KEYS), f"有族没被用到：{set(TARGET_ICON_KEYS) - seen}"


def test_card_ammo_rows_carry_the_targets(data) -> None:
    """卡片上真的挂上了（不是只有那个纯函数能算）：645 的 RPG-7V、92 的 Grad。"""
    card = build_unit_card(data, 645)
    rpg = next(w for w in card.weapons if w.label == "RPG-7V")
    pg7 = next(a for a in rpg.ammo if a.label == "PG-7VL")
    assert [t.key for t in pg7.targets] == ["vehicle", "helicopter"]
    thermo = next(a for a in rpg.ammo if "Thermobaric" in a.label)
    assert [t.key for t in thermo.targets] == ["infantry", "vehicle", "helicopter"]

    # 榴弹炮的 122mm 榴弹 TargetType == 1（只有 Ground）-> 那排是空的，与游戏一致
    grad = build_unit_card(data, 92)
    assert grad.weapons, "#92 9K51 Grad 应该有武器"
    assert all(a.targets == () for w in grad.weapons for a in w.ammo)


# --------------------------------------------------------------------------- #
# 数值行的图标：每格都要能指到实机那张 sprite
# --------------------------------------------------------------------------- #


def test_every_stat_carries_a_game_sprite(data) -> None:
    """数值行每一格都要有**实机的图标**（InfocardConfig 的 sprite）。

    用户要求"向实机那张卡的密度看齐"：实机是「图标在上、数字在下、没有文字标签」，
    8 格才挤得进一行。图标对不上的格子界面会退回文字标签 —— 所以这里要保证**没有**这种格子。
    """
    from ba_mod_maker.app.viewmodel.unit_card import STAT_SPECS
    from ba_mod_maker.core.assets.families import STAT_ICON_KEYS

    # 每条 STAT_SPECS 的图标名（含按单位类型分支的那两条）都要在表里
    for spec in STAT_SPECS:
        names = {spec.icon} if isinstance(spec.icon, str) else None
        if names is None:
            # 分支型（生命值/前进速度）：两个分支都得有图
            probes = {
                "health": ("Health", "HealthInfantry"),
                "speed_forward": (
                    "SpeedForwardInfantry",
                    "SpeedForwardVehicles",
                    "SpeedForwardHelicopters",
                    "SpeedForwardAircrafts",
                ),
            }.get(spec.key, ())
            assert probes, f"{spec.key} 的图标是函数但没登记分支：{spec.icon}"
            for name in probes:
                assert name in STAT_ICON_KEYS, f"{spec.key} 的分支 {name} 没有图"
            continue
        for name in names:
            assert name in STAT_ICON_KEYS, f"{spec.key} 的 {name} 没有图"


def test_infantry_card_stats_match_the_game_screenshot(data) -> None:
    """645（步兵）那 8 格必须与实机截图的图标**逐格一致**（顺序 + 图）。

    实机：盾 / 心+十字 / 三个人 / 望远镜 / 划掉的眼睛 / 奔跑的士兵 / 砝码 / 骷髅时钟。
    """
    card = build_unit_card(data, 645)
    assert [stat.key for stat in card.stats] == [
        "armor",
        "health",
        "soldier_count",
        "optics",
        "stealth",
        "speed_forward",
        "weight",
        "refund",
    ]
    want = [
        "Armor.png",
        "Health points.inf.png",
        "group.png",
        "Optics.png",
        "Stealth.png",
        "Forward speed.inf.png",
        "Weight.png",
        "DeathTimer.png",
    ]
    for stat, leaf in zip(card.stats, want, strict=True):
        assert stat.sprite.endswith(leaf), f"{stat.key} -> {stat.sprite}"


# --------------------------------------------------------------------------- #
# 武器那一排必须跟着「自定义选项」重算（Options.Turret0Id..Turret20Id）
# --------------------------------------------------------------------------- #


def _weapon_rows(card) -> list[tuple[int, str, int, tuple[tuple[int, int], ...]]]:
    """(武器 Id, 名字, 行内 xN, ((弹种 Id, 显示弹量), ...)) —— 全取自 WeaponGroupRow 的真实字段。"""
    return [
        (
            row.weapon_id,
            row.label,
            row.count,
            tuple((ammo.ammo_id, ammo.count) for ammo in row.ammo),
        )
        for row in card.weapons
    ]


def test_aircraft_weapon_row_follows_the_selected_pylon(data) -> None:
    """Su-57 把内侧挂架换成 2xKh59MK2 后，武器那一排必须跟着变。

    根因（本轮修）：CardData.weapon_ids() 调 card_turrets 时**漏传 selected** ——
    炮塔槽位（Options.Turret1Id=868）已经生效，武器那一排却还按缺省挂架
    （242 = R-77-1）画，所以「改了自定义选项，武器不变」。

    数据：unit 44，mod 340 缺省 1101（3xR77）-> 非缺省 1104（2xKh59MK2，弹种 477）。
    """
    assert _weapon_rows(build_unit_card(data, 44)) == [
        (120, "GSh-30-1", 1, ((90, 150),)),
        (244, "R-77-1", 3, ((159, 3),)),
        (437, "R-37", 2, ((312, 2),)),
        (243, "R-73", 2, ((158, 2),)),
    ]
    assert _weapon_rows(build_unit_card(data, 44, selected={340: 1104})) == [
        (120, "GSh-30-1", 1, ((90, 150),)),
        (633, "Kh-59MK2", 2, ((477, 2),)),
        (437, "R-37", 2, ((312, 2),)),
        (243, "R-73", 2, ((158, 2),)),
    ]


def test_weapon_groups_take_the_selected_pylon(data) -> None:
    """纯函数那一层也要收 selected（界面与测试都直接用 weapon_groups / weapon_ids）。"""
    assert weapon_groups(data, 44) == {
        90: (120,),
        159: (244, 244, 244),
        312: (437, 437),
        158: (243, 243),
    }
    assert weapon_groups(data, 44, selected={340: 1104}) == {
        90: (120,),
        477: (633, 633),
        312: (437, 437),
        158: (243, 243),
    }
    assert data.weapon_ids(44) == (120, 244, 244, 244, 437, 437, 243, 243)
    assert data.weapon_ids(44, {340: 1104}) == (120, 633, 633, 437, 437, 243, 243)


def test_vehicle_weapon_row_follows_the_selected_turret(data) -> None:
    """不是只对战机成立：BTR-82 换 BTR-82AT 炮塔（option 503）后多一门 Konkurs-M。"""
    assert _weapon_rows(build_unit_card(data, 1)) == [
        (2, "2А72 30mm", 1, ((207, 210), (208, 90))),
        (7, "PKT", 1, ((9, 2000),)),
    ]
    assert _weapon_rows(build_unit_card(data, 1, selected={190: 503})) == [
        (305, "2А42-30mm", 1, ((205, 200), (206, 100))),
        (7, "PKT", 1, ((9, 2000),)),
        (518, "Konkurs-M", 1, ((96, 2),)),
    ]


def test_infantry_weapon_row_is_not_affected_by_option_slots(data) -> None:
    """步兵那条**假炮塔**分支不动：武器来自 SquadMembers（按人），与 TurretNId 无关。

    63（Assaultmen SMAW）的两条选项都是「换单位」（ReplaceUnitId），
    换哪一条都不该动这一排 —— 8 支 M16A4 + 2 具 SMAW 是按 8 名成员数出来的。
    """
    default = _weapon_rows(build_unit_card(data, 63))
    assert [row[1] for row in default] == ["M16A4", "SMAW"]
    assert [row[2] for row in default] == [8, 2]
    assert _weapon_rows(build_unit_card(data, 63, selected={399: 1472})) == default
    assert _weapon_rows(build_unit_card(data, 63, selected={399: 1473})) == default


def test_option_time_modifiers_are_float_columns(data) -> None:
    """Options.TimeModifierRefund / Resurrect 是 **Single**（dump.cs Options+0x50 / +0x54）——
    读它们必须用 f()。

    改前用 i()：_int 对 float 一律返回 default 0，于是全库 12 / 7 条选项的
    撤回时间修正被**静默丢掉**，症状与「换了选项卡片不变」完全一样（这一格就是撤回时间）。
    样本：293 B-1B Lancer 的 Custom_Option_B61_Nuke（option 1026）Refund=2000 / Resurrect=-4140。
    """
    options = data.t("Options")
    option = options.by_id(1026)
    assert options.f(option, "TimeModifierRefund") == 2000.0
    assert options.f(option, "TimeModifierResurrect") == -4140.0
    assert options.i(option, "TimeModifierRefund") == 0  # 旧读法：float 列一律 0

    selected = {325: 1026}
    cost = card_cost(data, 293, selected)
    assert fmt_time(purchase_delay(data, 293, 1, cost=cost, selected=selected)) == "46:00"
    assert fmt_time(purchase_delay(data, 293, 2, cost=cost, selected=selected)) == "34:20"

    # 把这两列临时清零，卡片必须变回「没带修正」的那一档 —— 证明这两个数真的进了公式。
    index = options.index_of("TimeModifierResurrect")
    saved = option[index]
    option[index] = 0.0
    try:
        assert fmt_time(purchase_delay(data, 293, 1, cost=cost, selected=selected)) == "115:00"
    finally:
        option[index] = saved
    index = options.index_of("TimeModifierRefund")
    saved = option[index]
    option[index] = 0.0
    try:
        assert fmt_time(purchase_delay(data, 293, 2, cost=cost, selected=selected)) == "1:00"
    finally:
        option[index] = saved


# --------------------------------------------------------------------------- #
# 护甲 / 机动：Options.ArmorId / MobilityId 覆盖（两组实机锚 + 落点 + 回归）
# --------------------------------------------------------------------------- #


def _stats(card) -> dict[str, str]:
    """数值行 -> {key: 显示值}（与 build_unit_card 同一个口径）。"""
    return {stat.key: stat.value for stat in card.stats}


def test_armor_override_is_used_even_at_default(data) -> None:
    """BRDM-3 缺省就该是 14 —— 实机 docs/实机-04b-BRDM-3.png 那一排第一个数。

    实机：14 / 4 / 1750 / 1 / 100 / 25 / 16000 / 6:22；我们按 UnitArmors 算过的是 16。
    差别来自缺省选项 508 带的 Options.ArmorId=1：ApplyCurrentMods 把
    unit.CurrentArmor 改写成 Armors#1（MaxHealthPoints=14），
    而 UnitArmors 那条链指到 Armors#167（16）—— 实机取的是前者。
    """
    card = build_unit_card(data, 217)
    stats = {stat.key: stat for stat in card.stats}
    assert stats["health"].value == "14"
    assert str(stats["health"].value_ref) == "Armors#1.MaxHealthPoints"
    assert str(stats["health"].link_ref) == "Options#508.ArmorId"
    # 同一排另外 7 格仍是原列（说明只换了该换的那一格，不是整行换了一套）
    assert _stats(card) == {
        "health": "14",
        "seats": "4",
        "optics": "1750",
        "stealth": "1",
        "speed_forward": "100",
        "speed_back": "25",
        "weight": "16000",
        "refund": "6:22",
    }


def test_armor_override_follows_the_selected_option(data) -> None:
    """BRDM-3 换 option 509（Custom_Option_Uparmored，ArmorId=167）-> 生命回到 16。

    mod 193：缺省 508 = Custom_Option_Default（ArmorId=1 -> 14），
    非缺省 509 = Custom_Option_Uparmored（ArmorId=167 -> 16）。
    落点也必须跟着走到 Options#509.ArmorId（编辑 / 另存为新模板按它寻址）。
    """
    card = build_unit_card(data, 217, selected={193: 509})
    stats = {stat.key: stat for stat in card.stats}
    assert stats["health"].value == "16"
    assert str(stats["health"].value_ref) == "Armors#167.MaxHealthPoints"
    assert str(stats["health"].link_ref) == "Options#509.ArmorId"


def test_mobility_override_matches_the_real_game(data) -> None:
    """T-72B 缺省实机是 60 / 15（docs/实机-04-T-72B.png），我们原来是 70 / 20。

    实机那一排：16 / 1200 / 1 / **60 / 15** / 45000 / 8:15 —— 只有前进、倒车两格不同。
    缺省选项 626（Custom_Option_Default_Engine）带 MobilityId=112（60 / 15），
    而 UnitPropulsions 那条链指向 Mobility#113（70 / 20）。
    """
    card = build_unit_card(data, 9)
    assert _stats(card) == {
        "health": "16",
        "optics": "1200",
        "stealth": "1",
        "speed_forward": "60",
        "speed_back": "15",
        "weight": "45000",
        "refund": "8:15",
    }
    stats = {stat.key: stat for stat in card.stats}
    assert str(stats["speed_forward"].value_ref) == "Mobility#112.MaxSpeedRoad"
    assert str(stats["speed_back"].value_ref) == "Mobility#112.MaxSpeedReverse"
    assert str(stats["speed_forward"].link_ref) == "Options#626.MobilityId"


def test_mobility_override_follows_the_selected_option(data) -> None:
    """T-72B 换 option 627（Custom_Option_Upgraded_engine，MobilityId=113）-> 70 / 20。

    （这一项自己还带 Cost，所以撤回时间会顺带从 8:15 变成 8:37 —— 那是成本那一格的事，
    与机动无关；这里连它一起钉住，免得以后有人以为机动把撤回也改了。）
    """
    card = build_unit_card(data, 9, selected={239: 627})
    stats = {stat.key: stat for stat in card.stats}
    assert stats["speed_forward"].value == "70"
    assert stats["speed_back"].value == "20"
    assert stats["refund"].value == "8:37"
    assert str(stats["speed_forward"].value_ref) == "Mobility#113.MaxSpeedRoad"
    assert str(stats["speed_forward"].link_ref) == "Options#627.MobilityId"


def test_amphibious_chip_follows_the_mobility_override(data) -> None:
    """两栖那一位读 Mobility.IsAmphibious —— 也是被 MobilityId 覆盖的那个对象。

    BTR-82 缺省选项 504 带 MobilityId=89（IsAmphibious=True），而 UnitPropulsions
    那条链是 Mobility#3（False）—— 所以缺省就该画两栖（BTR-82 本来就是两栖车）。
    """
    assert "amphibious" in {chip.key for chip in build_unit_card(data, 1).abilities}
    # 反向：BRDM-2M 换 345（Mobility#72，IsAmphibious=False）会把两栖去掉
    assert "amphibious" in {chip.key for chip in build_unit_card(data, 173).abilities}
    selected = {127: 345}
    assert "amphibious" not in {
        chip.key for chip in build_unit_card(data, 173, selected=selected).abilities
    }


def test_ship_static_default_comes_from_the_option(data) -> None:
    """LHA-6 那组「Static / Mobile」：缺省是 Static（Mobility#173，水面速度 0）。

    选项名与 Mobility 行名是成对的 —— Custom_Option_Static -> Mobility#173 Name=
    "Ship Carrier/Destroyer static"（Water=0）、Custom_Option_Mobile -> Mobility#241（Water=35）。
    这是"选项覆盖真的上卡"的旁证；缺省就不再是 35。
    （船这一格读 MaxSpeedWater 本身还是**强假设**，见 _t_speed_forward 的注释。）
    """
    assert _stats(build_unit_card(data, 330))["speed_forward"] == "0"
    assert _stats(build_unit_card(data, 330, selected={588: 2088}))["speed_forward"] == "35"


def test_default_selection_is_the_same_path_as_selected_none(data) -> None:
    """缺省不许算成另一套：不传 selected 与 selected=None 必须逐格一致（全 540 个单位）。

    覆盖口径是「选中的选项 ?? 原来的链」，缺省选项本身就是被选中的那一项 ——
    两条路必须落在同一个函数上，否则缺省局与预览局会各画一套。
    """
    units = data.t("Units")
    for row in units.all():
        unit_id = units.i(row, "Id")
        default = [
            (s.key, s.value, s.value_ref, s.link_ref) for s in build_unit_card(data, unit_id).stats
        ]
        explicit = [
            (s.key, s.value, s.value_ref, s.link_ref)
            for s in build_unit_card(data, unit_id, selected=None).stats
        ]
        assert default == explicit, f"unit {unit_id}"


#: 7 个截图样本单位的**整行**数值（本轮改动前实测；STAT_SAMPLES 只钉了其中几格）。
#: 出处：docs/研究-单位卡片公式.md §3.5 的样本表；护甲/机动的选项覆盖不许把它们带偏。
SCREENSHOT_STAT_ROWS: dict[int, list[tuple[str, str]]] = {
    645: [
        ("armor", "10"),
        ("health", "25"),
        ("soldier_count", "5"),
        ("optics", "1400"),
        ("stealth", "1.75"),
        ("speed_forward", "18"),
        ("weight", "625"),
        ("refund", "6:30"),
    ],
    111: [
        ("armor", "8"),
        ("health", "50"),
        ("soldier_count", "10"),
        ("optics", "1400"),
        ("stealth", "1.75"),
        ("speed_forward", "18"),
        ("weight", "1250"),
        ("refund", "7:00"),
    ],
    112: [
        ("armor", "8"),
        ("health", "25"),
        ("soldier_count", "5"),
        ("optics", "1400"),
        ("stealth", "1.75"),
        ("speed_forward", "18"),
        ("weight", "625"),
        ("refund", "4:15"),
    ],
    374: [
        ("armor", "30"),
        ("health", "20"),
        ("optics", "6000"),
        ("stealth", "1"),
        ("speed_forward", "540"),
        ("turn_radius", "510"),
        ("fuel", "270"),
        ("refund", "13:17"),
    ],
    7: [
        ("armor", "10"),
        ("health", "15"),
        ("heavy_lift", "4000"),
        ("seats", "21"),
        ("optics", "1200"),
        ("stealth", "1.25"),
        ("speed_forward", "230"),
        ("agility", "100"),
        ("refund", "6:15"),
    ],
    90: [
        ("health", "14"),
        ("seats", "5"),
        ("optics", "1200"),
        ("stealth", "1"),
        ("speed_forward", "60"),
        ("speed_back", "20"),
        ("weight", "8500"),
        ("refund", "5:52"),
    ],
    92: [
        ("health", "10"),
        ("optics", "1000"),
        ("stealth", "1"),
        ("speed_forward", "80"),
        ("speed_back", "20"),
        ("weight", "14000"),
        ("refund", "16:07"),
    ],
}


@pytest.mark.parametrize("unit_id", sorted(SCREENSHOT_STAT_ROWS))
def test_screenshot_sample_stat_rows_are_unchanged(data, unit_id: int) -> None:
    """7 个截图样本的数值行逐格钉死（护甲/机动接了选项覆盖之后也不许变）。

    374 / 7 / 90 的缺省选项确实带 MobilityId=159 / ArmorId=7 / Ability1Id=4，
    但那些覆盖指向的行在卡片用到的列上与原来完全相同 —— 所以整行必须一模一样。
    """
    card = build_unit_card(data, unit_id)
    assert [(s.key, s.value) for s in card.stats] == SCREENSHOT_STAT_ROWS[unit_id]


# --------------------------------------------------------------------------- #
# 卡名：选项的名字覆盖（实机四条锚）
# --------------------------------------------------------------------------- #

#: 单位 Id -> (缺省卡名, [(modification_id, option_id, 该选项定下的卡名), ...])
#: 锚全部来自实机截图：docs/实机-05b-BTR82A.png（BTR-82A）、实机-04-T-72B.png（T-72B1）、
#: 实机-03a/03b（PT-76B -> PT-76-57）；Stryker / BRDM 两组是数据侧的同类样本
#: （ReplaceUnitName 整换、ConcatenateWithUnitName 原样拼，两种写法都要有）。
NAME_SAMPLES: dict[int, tuple[str, tuple[tuple[int, int, str], ...]]] = {
    1: ("BTR-82A", ((190, 503, "BTR-82AT"),)),
    9: ("T-72B1", ((237, 623, "T-72BA"),)),
    230: ("PT-76B", ((204, 535, "PT-76-30"), (204, 536, "PT-76-57"))),
    217: ("BRDM-3", ((192, 512, "PRP-5K"),)),
    22: ("Stryker ICV", ((4, 11, "Stryker ICV Javelin"), (4, 15, "M1296 Dragoon"))),
}


@pytest.mark.parametrize("unit_id", sorted(NAME_SAMPLES))
def test_card_title_follows_the_option_name_override(data, unit_id: int) -> None:
    """抬头 = HUDName 再套选项的名字覆盖 —— **缺省也要走这条口径**。

    实机里这三张卡写的是 BTR-82A / T-72B1 / PT-76B，而我们的列里只有 BTR-82 / T-72B / PT-76：
    名字靠的是 Options.ReplaceUnitName（整个换，BTR-82 -> BTR-82A）与
    Options.ConcatenateWithUnitName（原样拼，PT-76 的缺省项拼 "B"，选项 536 拼 "-57"）。
    """
    want_default, cases = NAME_SAMPLES[unit_id]
    assert unit_name(data, unit_id) == want_default
    assert build_unit_card(data, unit_id).title == want_default
    for modification_id, option_id, want in cases:
        selected = {modification_id: option_id}
        assert unit_name(data, unit_id, selected) == want
        assert build_unit_card(data, unit_id, selected=selected).title == want


def test_name_override_concatenates_verbatim_and_stacks_in_order(data) -> None:
    """拼接是**原样**的（值里带不带空格就是游戏里的样子），多组覆盖按选中顺序依次套。

    反例警示：自己插一个空格会把 "LAV-25" + "A1" 拼成 "LAV-25 A1"（实机是 LAV-25A1），
    而 " Javelin" 自带空格，再补一个就成了两个空格。
    """
    assert unit_name(data, 29, {7: 19}) == "LAV-25A2"  # 值 "A2"，无空格
    assert unit_name(data, 22, {4: 11}) == "Stryker ICV Javelin"  # 值 " Javelin"，自带空格
    assert unit_name(data, 22, {4: 11, 3: 1894}) == "Stryker ICV Javelin Trophy"
    # 先替换后拼接：两个组一起选时，顺序就是 selected_options 的顺序
    assert unit_name(data, 22, {4: 15, 3: 1894}) == "M1296 Dragoon Trophy"


def test_default_name_equals_no_selection_and_only_override_units_change(data) -> None:
    """不传 selected 与 selected=None 逐单位一致；其余单位的名字一个字都不许变。

    ❗ 540 个单位里 **53 个**缺省就带名字覆盖（BTR-82 -> BTR-82A、T-72B -> T-72B1、
    Pantsir S1 -> 96K6 Pantsir-S1、Humvee -> Humvee M240 …）。这个数字是路标：
    变了说明覆盖读漏了或读多了（实机锚见 NAME_SAMPLES）。
    """
    units = data.t("Units")
    changed = 0
    for unit in units.all():
        unit_id = units.i(unit, "Id")
        plain = units.s(unit, "HUDName") or units.s(unit, "Name")
        assert unit_name(data, unit_id) == unit_name(data, unit_id, None)
        if unit_name(data, unit_id) != plain:
            changed += 1
    assert changed == 53, f"缺省名被选项改写的单位数变了：{changed}"


# --------------------------------------------------------------------------- #
# 缩略图 / 立绘：选项的图覆盖（上一轮"名字"那条的姊妹项）
# --------------------------------------------------------------------------- #

#: 单位 Id -> (缺省缩略图键, [(modification_id, option_id, 选中后的键), ...])
#: 全部是 52085 的真数据：这几条 `Options.ThumbnailOverride` 非空、且 ReplaceUnitId == 0
#: （不换单位 —— 换单位的那些由界面"跳到那个单位"处理，看到的本来就是对的图）。
THUMBNAIL_SAMPLES: dict[int, tuple[str, tuple[tuple[int, int, str], ...]]] = {
    1: ("RU_BTR82-Label", ((190, 503, "RU_BTR82_AT-Label"),)),  # BTR-82 -> BTR-82AT(+10)
    22: ("US_STRYKER_ICV-Label", ((4, 15, "US_ICV_DRAGON-Label"),)),  # Stryker ICV -> M1296 Dragoon
    90: ("RU_BMD_2-Label", ((64, 184, "RU_BMD2_KORNET-Label"),)),  # BMD-2 -> BMD-2M(+10)
}

#: 立绘样本：23 条 PortraitOverride **全部**带 ReplaceUnitId，这些是"选了才换图"的那批。
PORTRAIT_SAMPLES: dict[int, tuple[str, tuple[tuple[int, int, str], ...]]] = {
    513: (
        "DLC2/JALAVAE_JAGU/JALAVAE_JAGU",
        ((602, 2140, "DLC2/GELEZINIS_VILKAS/GELEZINIS_VILKAS"),),
    ),
    519: (
        "DLC2/MILAN_TEAM/MILAN_TEAM",
        ((605, 2148, "DLC2/JAVELIN_LITHUANIAN/JAVELIN_LITHUANIAN"),),
    ),
}

#: 7 个截图样本单位（docs/实机-*.png 那批）的 (缩略图键, 立绘键)。
#: 本次改动的**回归线**：这 7 个单位的缺省选项都没带图覆盖，两个键必须一个字符都不变。
SCREENSHOT_KEYS: dict[int, tuple[str, str]] = {
    645: ("RU_VDV_TBG-Label", "RU\\VDV_ASSAULT\\VDV_ASSAULT"),
    111: ("RU_VDV_PARA-Label", "RU\\VDV_PARA\\VDV_PARA"),
    112: ("RU_AGS_30-Label", "RU\\VDV_AGS30\\VDV_AGS30"),
    374: ("RU_SU25-Label", "RU\\SU_25T\\SU_25T"),
    7: ("RU_MI8_MTV5-Label", "RU\\MI8MTV5_CARGO\\MI8MTV5_CARGO"),
    90: ("RU_BMD_2-Label", "RU\\BMD2\\BMD2"),
    92: ("RU_BM_21-Label", "RU\\GRAD\\GRAD"),
}


@pytest.mark.parametrize("unit_id", sorted(THUMBNAIL_SAMPLES))
def test_thumbnail_key_follows_the_option_override(data, unit_id: int) -> None:
    """缩略图键 = 第一个非空 ThumbnailOverride ?? Units.ThumbnailFileName，**缺省也走这条口径**。

    `Options.ThumbnailOverride` 非空 224 条，其中 131 条不换单位 —— 这 131 条就是原来
    漏掉的那批（卡上画的还是 Units 原列那张图）。与 unit_name 同一套纪律：缺省项自己带覆盖
    时，缺省态也要跟着走（见下面的 17 个单位）。
    """
    want_default, cases = THUMBNAIL_SAMPLES[unit_id]
    assert unit_thumbnail_key(data, unit_id) == want_default
    assert build_unit_card(data, unit_id).icon_key == want_default

    options = data.t("Options")
    for modification_id, option_id, want in cases:
        selected = {modification_id: option_id}
        option = data.option(option_id)
        # 数据侧的底（不是实现细节）：这条选项确实带覆盖，而且不换单位
        assert options.s(option, "ThumbnailOverride") == want
        assert options.i(option, "ReplaceUnitId") == 0
        assert want != want_default, "样本必须是'换了图'，不然测不出东西"
        assert unit_thumbnail_key(data, unit_id, selected) == want
        assert build_unit_card(data, unit_id, selected=selected).icon_key == want


def test_default_thumbnail_equals_no_selection_and_17_units_change(data) -> None:
    """不传 selected 与 selected=None 逐单位一致；**17 个单位**缺省缩略图键就被改掉。

    ❗ 17 是路标（540 个单位里，缺省选项自己带 ThumbnailOverride 的那些：
    23 / 72 / 108 / 129 …）。数字变了说明覆盖读漏了或读多了 —— 与名字那条 53 同理。
    """
    units = data.t("Units")
    changed: list[int] = []
    for unit in units.all():
        unit_id = units.i(unit, "Id")
        assert unit_thumbnail_key(data, unit_id) == unit_thumbnail_key(data, unit_id, None)
        assert unit_thumbnail_key(data, unit_id), f"单位 {unit_id} 的缩略图键是空的"
        if unit_thumbnail_key(data, unit_id) != units.s(unit, "ThumbnailFileName"):
            changed.append(unit_id)
    assert len(changed) == 17, f"缺省缩略图被改写的单位数变了：{len(changed)} -> {changed[:20]}"
    assert {23, 72, 108, 129} <= set(changed), "这四个单位缺省项就带覆盖（抽查，防全表读漏）"


def test_thumbnail_override_ledger(data) -> None:
    """路标：ThumbnailOverride 非空 **224** 条，其中 **131** 条不换单位（ReplaceUnitId == 0）。"""
    options = data.t("Options")
    rows = [row for row in options.all() if options.s(row, "ThumbnailOverride")]
    assert len(rows) == 224
    assert sum(1 for row in rows if options.i(row, "ReplaceUnitId") == 0) == 131


@pytest.mark.parametrize("unit_id", sorted(PORTRAIT_SAMPLES))
def test_portrait_key_follows_the_option_override(data, unit_id: int) -> None:
    """立绘键 = 第一个非空 PortraitOverride ?? Units.PortraitFileName（与缩略图同一口径）。"""
    want_default, cases = PORTRAIT_SAMPLES[unit_id]
    assert unit_portrait_key(data, unit_id) == want_default
    assert build_unit_card(data, unit_id).portrait_key == want_default

    options = data.t("Options")
    for modification_id, option_id, want in cases:
        selected = {modification_id: option_id}
        assert options.s(data.option(option_id), "PortraitOverride") == want
        assert want != want_default
        assert unit_portrait_key(data, unit_id, selected) == want
        assert build_unit_card(data, unit_id, selected=selected).portrait_key == want


def test_default_portrait_override_changes_nothing_yet(data) -> None:
    """路标：PortraitOverride 非空 **23** 条且**全部**带 ReplaceUnitId；缺省立绘 0 个单位变。

    8 个 DLC2 单位的缺省项自己就带覆盖，但值恰好等于 `Units.PortraitFileName`
    （513 的缺省项 2138 就是 "DLC2/JALAVAE_JAGU/JALAVAE_JAGU"）——
    所以这条口径现在不改任何一张图，它只是把路铺对：将来出现"不换单位、只换立绘"的
    条目时，缺省态也会自己生效（这就是 unit_name 那条纪律的意义）。
    """
    units = data.t("Units")
    options = data.t("Options")
    rows = [row for row in options.all() if options.s(row, "PortraitOverride")]
    assert len(rows) == 23
    assert all(options.i(row, "ReplaceUnitId") != 0 for row in rows)

    changed = [
        units.i(unit, "Id")
        for unit in units.all()
        if unit_portrait_key(data, units.i(unit, "Id")) != units.s(unit, "PortraitFileName")
    ]
    assert changed == [], f"缺省立绘被改写的单位数不再是 0：{changed[:20]}"
    # 但"选了就换图"这条路是通的
    assert unit_portrait_key(data, 513, {602: 2140}) == "DLC2/GELEZINIS_VILKAS/GELEZINIS_VILKAS"


@pytest.mark.parametrize("unit_id", sorted(SCREENSHOT_KEYS))
def test_screenshot_sample_keys_are_unchanged(data, unit_id: int) -> None:
    """7 个截图样本单位的 icon_key / portrait_key 一个字符都不许变（回归线）。"""
    want_thumb, want_portrait = SCREENSHOT_KEYS[unit_id]
    units = data.t("Units")
    unit = data.unit(unit_id)
    card = build_unit_card(data, unit_id)

    assert (card.icon_key, card.portrait_key) == (want_thumb, want_portrait)
    # 与原始列逐字一致 —— 这几个单位的缺省选项没带图覆盖，变了就说明覆盖读多了
    assert (want_thumb, want_portrait) == (
        units.s(unit, "ThumbnailFileName"),
        units.s(unit, "PortraitFileName"),
    )


# --------------------------------------------------------------------------- #
# 装甲图（地面载具立绘四角那 4 块）
# --------------------------------------------------------------------------- #

#: 实机截图逐格读出来的四个部位。三张截图（docs/实机-04-T-72B.png、05b-BTR82A、04b-BRDM-3）
#: 与 M1A2 那张（用户给的）**全部命中**，所以这里钉的是实机值，不是我们的实现。
#: 顺序 = 实机排布：上排 顶部|背面，下排 正面|侧面。
#: 每格 = (部位键, 部位名, 动能, 破甲)。
ARMOR_SAMPLES: dict[int, tuple[tuple[str, str, int, int], ...]] = {
    2: (  # M1A2 SEP v2 TUA（用户给的截图：顶部 60/60、背面 100/100、正面 750/1300、侧面 120/250）
        ("top", "顶部", 60, 60),
        ("rear", "背面", 100, 100),
        ("front", "正面", 750, 1300),
        ("side", "侧面", 120, 250),
    ),
    9: (  # T-72B1（docs/实机-04-T-72B.png，缺省 K1 反应装甲）
        ("top", "顶部", 60, 80),
        ("rear", "背面", 100, 100),
        ("front", "正面", 550, 800),
        ("side", "侧面", 100, 250),
    ),
    217: (  # BRDM-3（docs/实机-04b-BRDM-3.png）
        ("top", "顶部", 20, 20),
        ("rear", "背面", 20, 20),
        ("front", "正面", 30, 30),
        ("side", "侧面", 20, 20),
    ),
    1: (  # BTR-82A（docs/实机-05b-BTR82A.png）
        ("top", "顶部", 20, 20),
        ("rear", "背面", 20, 20),
        ("front", "正面", 30, 30),
        ("side", "侧面", 20, 20),
    ),
}


@pytest.mark.parametrize("unit_id", sorted(ARMOR_SAMPLES))
def test_armor_zones_match_the_real_screenshots(data, unit_id: int) -> None:
    """4 个单位 × 8 个数逐格钉死：Kin 一列、Heat 一列，部位名与顺序也要对。

    这 8 个数来自 `Armors` 表的 KinArmorFront/Sides/Top/Rear 与 HeatArmor*，
    取行走 `CardData.armor()`（Options.ArmorId 的覆盖，缺省也走）。
    """
    got = tuple(
        (zone.key, zone.label, zone.kinetic, zone.heat) for zone in armor_zones(data, unit_id)
    )
    assert got == ARMOR_SAMPLES[unit_id]


def test_unit_card_carries_the_armor_zones(data) -> None:
    """装配进 UnitCard（界面只排版、不再算）—— 卡片上的那块与函数口径一致。"""
    card = build_unit_card(data, 2)
    assert card.armor_zones == armor_zones(data, 2)
    assert [zone.key for zone in card.armor_zones] == ["top", "rear", "front", "side"]
    # 非载具：字段是空元组（界面据此不画）
    assert build_unit_card(data, 645).armor_zones == ()


def test_armor_zones_follow_the_armor_option(data) -> None:
    """BRDM-3 换「装甲组件」默认 -> Uparmored：这块图必须跟着变。

    两条链都在 `Options` 表里：modification 193 的选项 508（缺省，ArmorId=1）
    与 509（Uparmored，+10，ArmorId=167）。**必须**走 CardData.armor()，
    绕过它自己查 UnitArmors 就永远读 1 号那一行。
    """
    default = tuple((z.kinetic, z.heat) for z in armor_zones(data, 217))
    uparmored = tuple((z.kinetic, z.heat) for z in armor_zones(data, 217, {193: 509}))
    assert default == ((20, 20), (20, 20), (30, 30), (20, 20))
    assert uparmored == ((20, 20), (30, 120), (60, 150), (40, 120))
    assert uparmored != default
    # 缺省那一项（508）显式选上 == 不选：同一条口径
    assert armor_zones(data, 217, {193: 508}) == armor_zones(data, 217)


def test_armor_zones_only_exist_for_ground_vehicles(data) -> None:
    """步兵 / 直升机 / 战机 / 舰船**一个都不出**；8 个数全零的载具也不出。

    全库路标（52085）：Type=4 共 234 个、其中 226 个有非零装甲；
    其余四类（步兵 194 / 直升机 45 / 战机 62 / 舰船 5）一个非零都没有。
    """
    units = data.t("Units")
    drawn: list[int] = []
    for unit in units.all():
        unit_id = units.i(unit, "Id")
        zones = armor_zones(data, unit_id)
        if not zones:
            continue
        drawn.append(unit_id)
        assert units.i(unit, "Type") == 4, f"{unit_id} 不是载具却画了装甲图"
        assert len(zones) == 4
        assert all(zone.kinetic or zone.heat for zone in zones), unit_id
    assert len(drawn) == 226, f"能画装甲图的单位从 226 变成了 {len(drawn)}"
    # 五个非载具类各挑一个样本：明确不出（步兵 645 / 战机 44 / 直升机 7 / 舰船 139）
    for unit_id in (645, 44, 7, 139):
        assert armor_zones(data, unit_id) == ()
    # 全零的载具（BMD-2 的兄弟 171 等 8 个）也不出 —— 与 _t_armor「0 不出格」同一风格
    assert armor_zones(data, 171) == ()


def test_armor_zone_labels_come_from_the_game_localization(data) -> None:
    """部位名走游戏的本地化键（实测 keys/chi 里有 ui_infocard_armor_top/rear/front/side）。

    缺游戏时退到产品自带的中文，两者逐字相同（顶部/背面/正面/侧面），
    所以这条用例既钉住了键名，也钉住了走本地化这条路。
    """
    from ba_mod_maker.core.assets.localization import Localization as Loc

    fake = Loc(
        entries={
            "ui_infocard_armor_top": "顶上（假键）",
            "ui_infocard_armor_rear": "后头（假键）",
            "ui_infocard_armor_front": "前头（假键）",
            "ui_infocard_armor_side": "侧边（假键）",
        }
    )
    card = build_unit_card(data.instance, 2, localization=fake)
    assert [zone.label for zone in card.armor_zones] == [
        "顶上（假键）",
        "后头（假键）",
        "前头（假键）",
        "侧边（假键）",
    ]
