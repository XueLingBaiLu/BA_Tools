"""外键：**显示名字、按名字搜**（零 Qt）。"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.foreign import (
    LOOKUP_ART_COLUMNS,
    LOOKUP_NAME_COLUMNS,
    display_name,
    find_row,
    fold_confusables,
    lookup_columns,
    lookup_rows,
    name_column,
    reference_summary,
    resolved_target,
    row_label,
    search_rows,
)
from ba_mod_maker.core.edit.refs import FOREIGN_TABLE, RefHit, referencing_rows


def test_a_link_row_is_shown_as_the_unit_it_points_at(instance) -> None:
    """⭐ 用户 2026-10-02：`TransportAvailabilities.乘客侧` 指向一张**关联表**（没有名字列），
    从前显示成 `SpecializationAvailabilities #773` —— 用户原话：「应该是单位的 HUDName，
    不应该是这么一长串」。往下走一跳：那张表 #773 的 UnitId = 277 -> `Units#277` = Scouts。

    这不是"编一个名字"：那个单位是**数据里真写着**的（同一屏 ④运输 那块也写着 Scouts）。
    """
    from ba_mod_maker.core.edit.cells import row_cells
    from ba_mod_maker.core.edit.scope import RowRef

    assert resolved_target(instance, "SpecializationAvailabilities", 773) == ("Units", 277)
    cells = {
        cell.column: cell for cell in row_cells(instance, RowRef("TransportAvailabilities", 1293))
    }
    # 那一格：显示单位名 + 记下该跳到哪一行（Units#277），而不是那张关联表。
    assert cells["SpecializationAvailabilityId"].text == "Scouts"
    assert cells["SpecializationAvailabilityId"].link == ("Units", 277)
    # ❗ `display_name` **本身**仍然如实说"这是第几行" —— 它还被挑行的下拉用着，
    #    那边要的是"这一行是什么"（拼「专精 X · 单位 Y」），不是"它指着谁"。
    assert (
        display_name(instance, "SpecializationAvailabilities", 773)
        == "SpecializationAvailabilities #773"
    )


DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"


@pytest.fixture(scope="module")
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


def test_display_name_per_table_family(instance) -> None:
    """三族命名：HUDName / UIName / Name。"""
    assert display_name(instance, "Units", 1) == "BTR-82"
    assert display_name(instance, "Armors", 1) == "Armor BTR80 Base"
    assert name_column(instance, "Units") == "HUDName"
    assert name_column(instance, "Options") == "UIName"
    assert name_column(instance, "Sensors") == "Name"


def test_link_rows_without_a_name_say_so_instead_of_inventing_one(instance) -> None:
    """关联行没有名字：显示「表 #Id」，**不编**一个假名字。"""
    text = display_name(instance, "UnitArmors", 1)
    assert text == "UnitArmors #1"


def test_not_found_is_explained_not_empty(instance) -> None:
    """空串在界面上等于"这一格是空的"，那是另一种意思 —— 所以要说清楚。"""
    assert "找不到" in display_name(instance, "Armors", 999999)
    assert "没有表" in display_name(instance, "没有这张表", 1)
    assert find_row(instance, "Armors", 999999) is None
    assert find_row(instance, "Armors", 1) is not None


def test_search_matches_part_of_a_name_case_insensitively(instance) -> None:
    hits = search_rows(instance, "Mobility", "wheels btr")
    names = [hit.name for hit in hits]
    assert "Wheels BTR82A Base" in names
    assert all("wheels btr" in name.casefold() for name in names)


def test_search_accepts_both_name_and_hudname(instance) -> None:
    """数据里 Name 与 HUDName 常常一样，但偶尔只有一个有值 —— 两个键都要认。"""
    hits = search_rows(instance, "Units", "btr-82")
    assert hits, "按 HUDName 搜不到 BTR-82"
    assert any(hit.column == "HUDName" for hit in hits)


def test_search_returns_nothing_for_a_miss(instance) -> None:
    """找不到就返回空 —— 界面照设计稿写「没找到」，不瞎猜。"""
    assert search_rows(instance, "Mobility", "zzzzzzzz") == ()
    assert search_rows(instance, "没有这张表", "x") == ()


def test_fold_confusables_only_folds_lookalikes() -> None:
    """折叠只碰“看起来一样”的字符：保留大小写、不碰数字与拉丁字母。"""
    assert fold_confusables("2А72 30mm") == "2A72 30mm"
    assert fold_confusables("2а72 30mm") == "2a72 30mm"
    assert fold_confusables("BTR-82A") == "BTR-82A"
    assert fold_confusables("") == ""


def test_search_folds_cyrillic_homoglyphs(instance) -> None:
    """⭐ 用户报的真 bug（2026-09-30）：打 2A72 搜不出来、打 72 反而能搜出来。

    真因：Weapons.HUDName 写着「2А72 30mm」，那个 «А» 是
    **西里尔 А（U+0410）**，而键盘打的是拉丁 A（U+0041）——
    本地过滤只认显示名，所以永远匹配不上。这里钉两条：**匹配时折、显示不动**，
    且检索页那条路（lookup_rows 是另一段匹配代码）也得折。
    """
    want = {2, 270, 294, 381, 382}
    for needle in ("2A72", "2a72", "2А72"):
        hits = search_rows(instance, "Weapons", needle, limit=50)
        assert {hit.row_id for hit in hits} == want, (needle, [h.row_id for h in hits])

    # ❗ 下面这两个词**只有折叠能搜到**：没有任何一列写着拉丁的 `2A72 30mm`
    #    （Name 是 "Autocannon 30 2A72 T1"、没有 " 30mm" 那一截；HUDName 里是西里尔那个 А）。
    #    变异验证靠的就是它们：把 match_text 里的折叠拆掉，这几条立刻红。
    for needle in ("2A72 30mm", "2a72 30mm"):
        hits = search_rows(instance, "Weapons", needle, limit=50)
        assert {hit.row_id for hit in hits} == want, (needle, [h.row_id for h in hits])
        result = lookup_rows(instance, needle, tables=("Weapons",))
        assert {h.row_id for h in result.hits} == want, (needle, result.total)

    # 显示名**不许**被折叠改掉 —— 折的只是匹配用的那一份文本。
    hit = next(h for h in search_rows(instance, "Weapons", "2A72") if h.row_id == 2)
    assert hit.name == "2А72 30mm", repr(hit.name)

    # 检索页那条路同样要折 —— 别只修一半。
    for needle in ("2A72", "2a72", "2А72"):
        result = lookup_rows(instance, needle, tables=("Weapons",))
        assert {h.row_id for h in result.hits} == want, (needle, result.total)

    # 反面：不该命中的仍然不命中（折叠没把搜索变成“什么都匹配”）。
    assert search_rows(instance, "Weapons", "zzzzzzzz") == ()
    assert lookup_rows(instance, "zzzzzzzz", tables=("Weapons",)).hits == ()


def test_empty_needle_lists_the_table_sorted(instance) -> None:
    """空串 = 把这张表列出来（用户还没打字），按名字排序方便挑。"""
    hits = search_rows(instance, "Armors", "", limit=10)
    assert len(hits) == 10
    names = [hit.name.casefold() for hit in hits]
    assert names == sorted(names)


def test_search_respects_the_limit(instance) -> None:
    hits = search_rows(instance, "Weapons", "", limit=5)
    assert len(hits) == 5


def test_reference_summary_pairs_name_with_the_address(instance) -> None:
    text = reference_summary(instance, "Armors", 1)
    assert text.startswith("Armor BTR80 Base")
    assert "Armors #1" in text


# --------------------------------------------------------------------------- #
# 没有名字列的表：候选退到**外键拼**的标签（2026-09-30 修）
# --------------------------------------------------------------------------- #


def test_name_less_tables_still_list_their_rows(instance) -> None:
    """整张表一个 Name/HUDName 都没有时，候选列表**不能是空的**。

    实机症状（用户：「点开一个空框」）：SpecializationAvailabilities /
    TransportAvailabilities 是关联行，`search_rows` 早先直接 return () ——
    「运输」那一块的「＋ / 换…」候选恒为 0 条。
    """
    hits = search_rows(instance, "SpecializationAvailabilities", "", limit=5)
    assert len(hits) == 5
    assert all(hit.name != f"SpecializationAvailabilities #{hit.row_id}" for hit in hits)
    assert row_label(instance, "SpecializationAvailabilities", 773) != (
        "SpecializationAvailabilities #773"
    )


def test_the_composed_label_says_what_the_row_means(instance) -> None:
    """拼出来的标签 = 列的中文短名 + 被指向那一行的名字（专精 / 单位）。"""
    text = row_label(instance, "SpecializationAvailabilities", 773)
    assert "专精" in text and "单位" in text
    # 「乘客侧」那一列也是外键：TransportAvailabilities 的行照样能拼出来
    transport = row_label(instance, "TransportAvailabilities", 1293)
    assert transport != "TransportAvailabilities #1293"


def test_search_on_a_name_less_table_matches_the_composed_label(instance) -> None:
    """筛的时候也拿拼出来的标签匹配 —— 打「Stryker」要能筛到那几行。"""
    hits = search_rows(instance, "SpecializationAvailabilities", "stryker", limit=20)
    assert hits, "拼出来的标签没参与匹配"
    assert all("stryker" in hit.name.casefold() for hit in hits)


def test_row_label_prefers_a_real_name_and_stays_honest(instance) -> None:
    """有名字的表照旧给名字（不抢 display_name 的戏）；找不到就说找不到。"""
    assert row_label(instance, "Units", 1) == "BTR-82"
    assert row_label(instance, "Armors", 1) == "Armor BTR80 Base"
    assert "找不到" in row_label(instance, "SpecializationAvailabilities", 999999)
    assert "没有表" in row_label(instance, "没有这张表", 1)


# --------------------------------------------------------------------------- #
# 检索页：一个框查名字 / HUD 名 / UIName / 美术文件名 / Id（2026-09-30 重做）
#
# 与上面 search_rows 那几条的分工：那是「一张表里挑一行」（RowPicker 在用），
# 这里是「整库找东西」（检索页在用）。两套口径都住在 foreign.py 里。
# --------------------------------------------------------------------------- #


def test_lookup_columns_are_the_agreed_set(instance) -> None:
    """搜的列就是约定那几列：名字三列 + 美术文件名（图标 / 缩略图 / 立绘）。"""
    assert LOOKUP_NAME_COLUMNS == ("Name", "HUDName", "UIName")
    assert set(LOOKUP_ART_COLUMNS) == {
        "OptionPicture",
        "ThumbnailFileName",
        "ThumbnailOverride",
        "HUDIcon",
        "PortraitFileName",
        "PortraitOverride",
    }
    # 表里没有的列自动跳过（实测 Units 没有 HUDIcon）
    assert lookup_columns(instance, "Units") == (
        "Name",
        "HUDName",
        "ThumbnailFileName",
        "PortraitFileName",
    )
    # 关联表一个名字列都没有 —— 它们本来就不给人查，"搜不到"是真没有
    assert lookup_columns(instance, "UnitArmors") == ()


def test_lookup_finds_a_unit_by_its_name(instance) -> None:
    """搜一个单位名 -> 命中 Units#…（验收判据一）。"""
    result = lookup_rows(instance, "BTR-82")
    assert result.hits, "按名字搜不到 BTR-82"
    assert result.hits[0].table == "Units"
    assert result.hits[0].row_id == 1
    assert result.hits[0].name == "BTR-82"


def test_lookup_finds_a_row_by_its_icon_or_thumbnail_file_name(instance) -> None:
    """美术文件名也要能查 —— 做图标的人问的是"这张图是哪个单位的"。

    ❗ 三条都是**现库里的真值**：设计稿举的 `US_Stryker_M1128_Icon` 库里没有
    （全库搜不到），所以用例用真值，不照抄设计稿的示例。
    """
    thumb = lookup_rows(instance, "US_STRYKER_MC-Label")  # Units.ThumbnailFileName
    assert any(hit.table == "Units" and "ThumbnailFileName" in hit.columns for hit in thumb.hits)

    portrait = lookup_rows(instance, "BTR_82A")  # Units.PortraitFileName（立绘）
    assert any("PortraitFileName" in hit.columns for hit in portrait.hits)

    icon = lookup_rows(instance, "AMMO_BULLET_SHORT")  # Ammunitions.HUDIcon
    assert any("HUDIcon" in hit.columns for hit in icon.hits)


def test_lookup_matches_pure_digits_by_id(instance) -> None:
    """搜纯数字 18 -> 命中 Id=18 的行（验收判据二）。"""
    result = lookup_rows(instance, "18")
    id_hits = [hit for hit in result.hits if "Id" in hit.columns]
    assert id_hits, "纯数字没走 Id 精确命中"
    assert all(hit.row_id == 18 for hit in id_hits)
    assert find_row(instance, id_hits[0].table, 18) is not None, "表里没有 Id=18，用例在编"
    # 第一条就该是 Id 命中：用户打 18 要的是那一行，不是"名字里带 18"
    assert "Id" in result.hits[0].columns


def test_lookup_only_reads_id_as_id_for_digits(instance) -> None:
    """非纯数字不当 Id 用（否则搜 "btr-82" 会冒出一堆 Id 恰好含 82 的噪声）。"""
    assert all("Id" not in hit.columns for hit in lookup_rows(instance, "btr-82").hits)


def test_lookup_never_invents_a_row_or_a_column(instance) -> None:
    """每条命中的 (表, Id) 在库里真实存在，且**命中的列真含关键词**（验收判据三）。"""
    for query in ("stryker", "18", "US_STRYKER_MC-Label", "ARMOR1"):
        needle = query.casefold()
        hits = lookup_rows(instance, query).hits
        assert hits, f"{query} 一条都没搜到"
        for hit in hits:
            table = instance.table(hit.table)
            row = find_row(instance, hit.table, hit.row_id)
            assert row is not None, f"{hit.table}#{hit.row_id} 不存在"
            assert hit.columns and set(hit.columns) <= set(table.columns)
            assert any(
                needle in str(row[table.column_index(column)]).casefold() for column in hit.columns
            ), f"{query} 没在 {hit.table}#{hit.row_id} 的 {hit.columns} 里"
            # Id 命中时 value 就是行 Id；其它命中时 value 是真值
            assert hit.column == hit.columns[0]
            assert hit.column_text == " · ".join(hit.columns)


def test_lookup_reports_the_real_total_and_respects_the_limit(instance) -> None:
    """最多先给 limit 条，但「共 N 条」必须是**真数**（界面就照它写）。"""
    page = lookup_rows(instance, "a", limit=10)
    assert page.count == 10
    assert page.total > 10
    assert page.truncated is True
    assert sum(len(group) for group in page.by_table().values()) == page.count


def test_lookup_with_an_empty_query_finds_nothing(instance) -> None:
    """空串不能把整库倒出来（界面照设计稿给一句提示，不倒一面墙）。"""
    for query in ("", "   "):
        page = lookup_rows(instance, query)
        assert page.count == 0
        assert page.total == 0
        assert page.truncated is False


def test_lookup_ranks_exact_hits_first(instance) -> None:
    """完全相等的排前面，再按表的原顺序 —— 谁在第一条由口径决定，不由运气决定。"""
    hits = lookup_rows(instance, "btr-82").hits
    assert hits[0].table == "Units"
    assert hits[0].row_id == 1
    assert hits[0].value == "BTR-82"


# --------------------------------------------------------------------------- #
# 查找引用：谁指着我（core/edit/refs.py::referencing_rows）
#
# 旧产品有这个功能（"哪些行引用了这一行"），新产品只有"点外键跳过去"。
# 判据写在函数的 docstring 里，这里逐条钉死。
# --------------------------------------------------------------------------- #


def test_referencing_rows_finds_every_real_hit(instance) -> None:
    """判据一：每条命中的那一列**真的**等于被查的 Id（回读校验，不许编）。"""
    hits = referencing_rows(instance, "Armors", 1)

    assert hits, "Armors#1 一定有引用者（UnitArmors.ArmorId / Options.ArmorId）"
    assert isinstance(hits[0], RefHit)
    for hit in hits:
        table = instance.table(hit.table)
        row = find_row(instance, hit.table, hit.row_id)
        assert row is not None, f"{hit.table}#{hit.row_id} 不存在"
        assert row[table.column_index(hit.column)] == 1
        assert hit.label, "每一行都要有人话标签（界面直接显示它）"
        assert "找不到" not in hit.label


def test_referencing_rows_only_uses_registered_foreign_columns(instance) -> None:
    """判据二：只认 FOREIGN_TABLE 里指向这张表的列 —— 不新造第二张映射表。"""
    hits = referencing_rows(instance, "Armors", 1)

    assert {c for c, t in FOREIGN_TABLE.items() if t == "Armors"} == {"ArmorId"}
    assert {hit.column for hit in hits} == {"ArmorId"}


def test_zero_is_not_a_reference(instance) -> None:
    """判据三：外键里的 0 = 不使用，不算引用（否则选一行就是成百上千条噪声）。"""
    assert referencing_rows(instance, "Units", 0) == ()
    assert referencing_rows(instance, "Armors", 0) == ()


def test_referencing_rows_accepts_both_table_names(instance) -> None:
    """表名按**这份数据源**的叫法归一：目录来源叫 PlaneFlyPresets，外键登记的是 FlyPresets。"""
    source_name = "PlaneFlyPresets" if "PlaneFlyPresets" in instance.table_names else "FlyPresets"

    hits = referencing_rows(instance, source_name, 1)

    assert hits, "Mobility.FlyPresetId 一定指过 1 号飞行预设"
    assert {"Mobility"} <= {hit.table for hit in hits}
    assert {hit.column for hit in hits} == {"FlyPresetId"}


def test_referencing_rows_of_a_unit_lists_its_own_rows(instance) -> None:
    """一个单位的关联行靠 UnitId 认爹 —— 反查要能看到它们（这就是"查找引用"）。"""
    hits = referencing_rows(instance, "Units", 645)
    tables = {hit.table for hit in hits}

    assert {"SquadMembers", "UnitArmors"} <= tables
    assert all(hit.row_id != 0 for hit in hits)


def test_referencing_rows_is_empty_for_a_row_nobody_points_at(instance) -> None:
    """没人引用就返回空元组（界面照设计稿写「没有」），不抛异常。"""
    assert referencing_rows(instance, "Armors", 999999) == ()
    assert referencing_rows(instance, "没有这张表", 1) == ()
