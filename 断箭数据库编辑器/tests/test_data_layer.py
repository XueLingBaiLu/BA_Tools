"""数据层测试——直接跑在真实的 `DataBaseCompiled` 上。

为什么不用假数据：**本层要对付的就是数据侧的契约**——序列、列名、
异形表、manifest 的三套命名。换成假数据，测的就只是“我以为的契约”。

❗ **每个实例必须单独断言**。两个实例的行数本来就不同
（52085 是默认非沙盘，52082 是沙盘），拿一个去断言另一个必定报错。
"""

from __future__ import annotations

import json

import pytest

from ba_mod_maker.app.shell import db_channel
from ba_mod_maker.core.asset import container, dbzip
from ba_mod_maker.core.asset import tables as table_names
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.data import Instance, find_dataset_root, load_dataset
from ba_mod_maker.core.data.loader import read_table
from ba_mod_maker.core.data.schema import manifest_name_map
from ba_mod_maker.core.errors import DataSourceError
from ba_mod_maker.core.session import OpenedSource, SourceKind, open_source

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: ⚠ 这是**真件读数**：数据源里有哪些实例，随「抽哪一份容器」变。
#: 2026-10-15 从**当时的本体**重抽了一次（旧备份是 2026-09-14 的，比本体少一版），
#: 于是多了四个实例。实测规律：新容器里 **52461 ≡ 旧 52082**（战役形态）、
#: **52463 ≡ 旧 52085**（默认非战役形态），另加 52462（第三种形态）与 52486（最新、行数最大）。
#:
#: ★ 2026-10-18 又抽出了一份 **52460**（`_extract_report.json` = 2026-10-17 12:00）：
#: 它是**旧版本快照** —— `Units` 只有 28 列（比 `_schema.json` 少
#: `TimeModifierRefund` / `TimeModifierResurrect` / `UpkeepMultiplier`），另有 6 张表也少列。
#: `DataBaseCompiled/` 从此是**多版本并存**：旧实例缺列**是允许的**，缺了哪几列由
#: `Table.missing_columns` / `Instance.missing_columns()` 报出来（见
#: `core/data/loader.py::_column_order`）；**多出未知列**才报错。
#: ⛔ 实例集合与 LANDMARKS 必须同步改（下面按 EXPECTED_INSTANCE_IDS 逐个取 LANDMARKS）。
EXPECTED_INSTANCE_IDS = ("52082", "52085", "52460", "52461", "52462", "52463", "52486")
EXPECTED_TABLE_COUNT = 24

#: 实测锁定的路标数据（两个实例各自独立）。
#: 格式：实例 -> {表名: (行数, 源列数)}
#: 注意用的是 **source_columns**：异形表展示时是 2 列，但它的原始列数是 33。
#: 这些数字变了，说明游戏更新过，或数据被换成了另一份。
LANDMARKS: dict[str, dict[str, tuple[int, int]]] = {
    "52082": {
        "Units": (478, 31),
        "WeaponAmmunitions": (2052, 6),
        "Options": (1808, 46),
        "Ammunitions": (582, 67),
        "Turrets": (961, 10),
        "FlyPresets": (22, 33),
    },
    "52085": {
        "Units": (540, 31),
        "WeaponAmmunitions": (2357, 6),
        "Options": (2046, 46),
        "Ammunitions": (630, 67),
        "Turrets": (1055, 10),
        "FlyPresets": (23, 33),
    },
    # 52460 = 2026-10-18 新抽出的**旧版本快照**：Units 28 列（默认 31）、
    # Options 43 列（默认 46）、Ammunitions 63 列（默认 67）、Turrets 9 列（默认 10）。
    # 行数也少几行 —— 它本来就是另一版。读数是 `_work/t6_probe_landmarks.py` 跑出来的。
    "52460": {
        "Units": (475, 28),
        "WeaponAmmunitions": (2037, 6),
        "Options": (1802, 43),
        "Ammunitions": (565, 63),
        "Turrets": (958, 9),
        "FlyPresets": (22, 33),
    },
    # 下面四个 = 2026-10-15 从本体重抽（见 EXPECTED_INSTANCE_IDS 上面那段）。
    "52461": {
        "Units": (478, 31),
        "WeaponAmmunitions": (2052, 6),
        "Options": (1808, 46),
        "Ammunitions": (582, 67),
        "Turrets": (961, 10),
        "FlyPresets": (22, 33),
    },
    "52462": {
        "Units": (481, 31),
        "WeaponAmmunitions": (2050, 6),
        "Options": (1810, 46),
        "Ammunitions": (580, 67),
        "Turrets": (963, 10),
        "FlyPresets": (22, 33),
    },
    "52463": {
        "Units": (540, 31),
        "WeaponAmmunitions": (2357, 6),
        "Options": (2046, 46),
        "Ammunitions": (630, 67),
        "Turrets": (1055, 10),
        "FlyPresets": (23, 33),
    },
    "52486": {
        "Units": (542, 31),
        "WeaponAmmunitions": (2371, 6),
        "Options": (2051, 46),
        "Ammunitions": (640, 67),
        "Turrets": (1057, 10),
        "FlyPresets": (23, 33),
    },
}


@pytest.fixture(scope="module")
def dataset():
    assert DATASET_ROOT is not None
    return load_dataset(DATASET_ROOT)


@pytest.fixture(scope="module")
def instance_ids(dataset) -> tuple[str, ...]:
    return dataset.instance_ids


# --------------------------------------------------------------------------- #
# 数据源整体
# --------------------------------------------------------------------------- #


def test_instance_discovery(instance_ids) -> None:
    """实例应被自动发现，而不是硬编码 52082/52085。"""
    assert instance_ids == EXPECTED_INSTANCE_IDS
    for path_id in instance_ids:
        assert path_id.isdigit()


def test_schema_has_24_tables(dataset) -> None:
    assert len(dataset.table_names) == EXPECTED_TABLE_COUNT


def test_row_counts_match_manifest(dataset, instance_ids) -> None:
    """每张表的行/列数必须与 manifest 对得上。

    这是“数据源未被第三方改动”的基本保证，
    也是将来游戏更新后做回归的锂点。
    """
    for path_id in instance_ids:
        report = dataset.verify(path_id)
        assert report == {}, f"实例 {path_id} 与 manifest 不一致：{report}"


# --------------------------------------------------------------------------- #
# 字段级
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path_id", EXPECTED_INSTANCE_IDS)
def test_every_table_matches_schema_columns(dataset, path_id) -> None:
    """每张表的列集合 = schema 列集合 **减去它自己缺的那几列**。

    2026-10-18 改（原判据是「必须完全相等」）。为什么必须改：`DataBaseCompiled/`
    现在是**多版本快照**（7 个实例不是同一版），旧实例天然没有后来新增的列；
    拿新 schema 去卡旧快照，结果是「用户一切到旧实例那一页就直接打不开」。
    旧实例能打开**才是对的**，所以这里改成断言两条**更严**的性质：

    1. `columns ∪ missing_columns` 正好等于 schema 的列集合（不许多、不许少）；
    2. 两者不相交（同一列不许既算「有」又算「缺」）。

    ⇒ ⛔ 放宽的只有「少」这一侧：「多出未知列」在 loader 里仍然直接抛
      `DataSourceError`（负控见 `test_unknown_column_is_rejected`）。
    实测（2026-10-18，7 实例 × 24 表 = 168 张）：只有 52460 的 7 张 non-empty。
    """
    instance = dataset.instance(path_id)
    for name in instance.table_names:
        table = instance.table(name)
        expected = set(instance.schema[name].columns)
        assert set(table.source_columns) | set(table.missing_columns) == expected, (
            f"{path_id}/{name} 列集合 + 缺列 不等于 schema"
        )
        assert not set(table.source_columns) & set(table.missing_columns), (
            f"{path_id}/{name} 同一列既算有又算缺"
        )


def test_legacy_instance_reports_its_missing_columns(dataset) -> None:
    """旧快照 52460 要**读得出来**，且少的**正好是那 3 列**（读数，不是"能打开"就完）。

    真件读数（2026-10-18 实测，`_work/t6_probe_all_instances.py`）：
    `52460/Units` = 28 列、缺 `TimeModifierRefund` / `TimeModifierResurrect` /
    `UpkeepMultiplier`；`52486/Units` = 31 列、0 缺（同目录里的对照）。
    ⛔ 这两条合起来才算「不许静默」：少了列，得能查出少的是哪几列。
    """
    legacy = dataset.instance("52460")
    units = legacy.table("Units")
    assert len(units.columns) == 28
    assert units.missing_columns == (
        "TimeModifierRefund",
        "TimeModifierResurrect",
        "UpkeepMultiplier",
    )
    # 实例那一层查得到同一份读数（用户在状态栏逐个实例切，问的就是这个）
    assert legacy.missing_columns("Units") == units.missing_columns
    # 行宽跟着**它自己的**列走 —— 不为了对齐 schema 凭空补几个 None
    assert {len(row) for row in units.rows} == {28}
    # 对照（同一份数据里的最新实例）：一列不缺
    latest = dataset.instance("52486").table("Units")
    assert len(latest.columns) == 31
    assert latest.missing_columns == ()


@pytest.mark.parametrize("path_id", EXPECTED_INSTANCE_IDS)
def test_row_width_is_uniform(dataset, path_id) -> None:
    """每一行的长度必须等于列数——不齐会让渲染错位，而且很难发现。"""
    instance = dataset.instance(path_id)
    for name in instance.table_names:
        table = instance.table(name)
        widths = {len(row) for row in table.rows}
        assert widths == {table.column_count}, f"{path_id}/{name} 行宽不统一：{widths}"


# --------------------------------------------------------------------------- #
# 三个已知陷阱（每个都是真实踩过的）
# --------------------------------------------------------------------------- #


def test_column_order_is_declaration_order(dataset, instance_ids) -> None:
    """列的展示顺序应为游戏里的声明顺序，而非 `_schema.json` 的字母序。

    实测：`_schema.json` 里 24/24 张表都是 `columns == sorted(columns)`，
    而 JSON 的键顺序是声明顺序。两者列集合相同、顺序不同，
    所以展示顺序取自 JSON。该断言失败 = 有人把它改回了字母序。
    """
    instance = dataset.instance(instance_ids[0])

    units = instance.table("Units")
    assert units.columns[:3] == ("OriginalName", "OwnerInfantryID", "OriginalCost")
    assert units.columns[:3] != tuple(sorted(units.columns[:3]))

    ammunitions = instance.table("Ammunitions")
    assert ammunitions.columns[:4] == ("Id", "Name", "HUDName", "HUDIcon")

    assert set(instance.schema["Units"].columns) == set(units.columns)


def test_all_tables_are_arrays(dataset, instance_ids) -> None:
    """**24 张表全部是数组**——不存在「异形表」。

    这条断言来自一次真实的错误：早期代码里曾有一个「对象形态表归一化」分支，
    依据是某次诊断脚本把 manifest 的 rows 读成了 -1。后来逐字节复核发现，
    `PlaneFlyPresets.json` 的开头就是 `[{"Id":1,"Name":"AC130",...}]`，
    manifest 也写着 `rows: 22/23, is_list: true`。

    所以那条「异形表」结论是我自己的读数错误，对应代码已删除。
    这个测试把「全部是数组」钉住：一旦将来真的出现非数组形态，它会红。
    """
    for path_id in instance_ids:
        for name in dataset.instance(path_id).table_names:
            table = dataset.instance(path_id).table(name)
            assert table.row_count >= 0
            assert table.column_count > 0
            assert table.columns == table.source_columns


def test_fly_presets_is_an_ordinary_table(dataset, instance_ids) -> None:
    """`FlyPresets` 是一张普通表：33 列、普通行，不需要任何特殊分支。

    它唯一特殊的地方是**名字**：表名是 `FlyPresets`，而条目名（= 磁盘文件名）
    是 `PlaneFlyPresets.json`。目录来源交出来的名字已归一到表名（见
    `core/data/loader.py` 的模块说明），所以两种来源在这里表现一致。
    """
    for path_id in instance_ids:
        table = dataset.instance(path_id).table("FlyPresets")
        assert table.column_count == 33
        assert table.columns[:2] == ("Id", "Name")
        assert table.row_count > 0


def test_object_shaped_file_is_rejected(tmp_path, dataset, instance_ids) -> None:
    """若真出现对象形态的表，应当**明确报错**，而不是猜测语义后静默处理。"""
    sandbox = tmp_path / "obj"
    sandbox.mkdir()
    (sandbox / "Countries.json").write_text('{"a": {"Id": 1}}', encoding="utf-8")

    instance = dataset.instance(instance_ids[0])
    with pytest.raises(DataSourceError) as excinfo:
        read_table(sandbox, instance.schema, "Countries")
    assert "不是数组" in str(excinfo.value)
    assert excinfo.value.hint


@pytest.mark.parametrize("path_id", EXPECTED_INSTANCE_IDS)
def test_manifest_rename_is_traceable(dataset, path_id) -> None:
    """改名必须可查：`PlaneFlyPresets` 在 manifest 里叫 `FlyPresets`。

    实测：同一张表在 manifest 里有三个名字——
    `table`=`FlyPresets`、`field`=`FlyPresetsJson`、`zip_entry`=`PlaneFlyPresets.json`。
    **只有 zip_entry 的文件名干与磁盘一致**，所以 manifest 按它建键。
    """
    instance = dataset.instance(path_id)
    assert manifest_name_map(instance.directory) == {"PlaneFlyPresets": "FlyPresets"}
    for name in instance.table_names:
        assert instance.manifest_entry(name), f"{path_id}/{name} 找不到 manifest 条目"


@pytest.mark.parametrize("path_id", EXPECTED_INSTANCE_IDS)
def test_landmark_counts(dataset, path_id) -> None:
    """锁定几个硬指标。两个实例的数字**本来就不同**，所以分开写。"""
    instance = dataset.instance(path_id)
    for name, (rows, source_columns) in LANDMARKS[path_id].items():
        table = instance.table(name)
        assert table.row_count == rows, f"{path_id}/{name} 行数"
        assert len(table.source_columns) == source_columns, f"{path_id}/{name} 源列数"


def test_cell_lookup_consistency(dataset, instance_ids) -> None:
    """按列名取值 == 按下标取值；错列名必须报错。"""
    units = dataset.instance(instance_ids[0]).table("Units")
    column = units.column_index("Id")
    assert units.cell(0, column) == units.rows[0][column]

    with pytest.raises(KeyError):
        units.column_index("这个列不存在")


def test_unknown_column_is_rejected(dataset, instance_ids, tmp_path) -> None:
    """**多出 schema 不认识的列**仍然必须报错（这是真漂移，不是旧快照）。

    ★ 这条是 2026-10-18「允许实例缺列」那次改动的**负控**：放宽的只有「少」
    这一侧，**没有**放宽「多」—— 产品不认识的列按它写回就是静默丢数据。
    造一个假实例文件（`Countries` 多一列 `未来新列`），必须当场红。
    """
    instance = dataset.instance(instance_ids[0])
    sandbox = tmp_path / "fake"
    sandbox.mkdir()
    (sandbox / "Countries.json").write_text(
        json.dumps([{"Id": 1, "Name": "甲", "未来新列": 1}], ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(DataSourceError) as excinfo:
        read_table(sandbox, instance.schema, "Countries")
    assert "与 _schema.json 不一致" in str(excinfo.value)
    # 报错里要点名那一列 —— 否则用户拿不到"该删哪一列"的线索
    assert "未来新列" in excinfo.value.context.get("extra", [])


def test_missing_column_is_allowed_and_reported(dataset, instance_ids, tmp_path) -> None:
    """**缺列**不再报错：按实例自己的列读出来，缺哪几列被点名（正控）。

    故意只给 `Countries` 一列 ⇒ 读成功、行宽跟着自己的列、`missing_columns` 非空。
    这条与上一条是一对：`test_unknown_column_is_rejected`（多要红）·
    本条（少要能读、且不许静默）。
    """
    instance = dataset.instance(instance_ids[0])
    expected = set(instance.schema["Countries"].columns)
    sandbox = tmp_path / "legacy"
    sandbox.mkdir()
    (sandbox / "Countries.json").write_text(json.dumps([{"Id": 1}]), encoding="utf-8")

    table = read_table(sandbox, instance.schema, "Countries")

    assert table.columns == ("Id",)
    assert table.missing_columns, "少了列却没点名 = 静默，不许"
    assert set(table.missing_columns) == expected - {"Id"}
    assert {len(row) for row in table.rows} == {1}


def test_missing_table_file_reports_human_error(dataset, instance_ids, tmp_path) -> None:
    """缺表文件时应报人话错误，而不是裸的 FileNotFoundError。"""
    instance = dataset.instance(instance_ids[0])
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(DataSourceError) as excinfo:
        read_table(empty, instance.schema, "Units")
    assert "缺少表文件" in str(excinfo.value)
    assert excinfo.value.hint, "应当告诉用户下一步该做什么"


def test_default_is_the_largest_path_id_not_the_sort_first(dataset) -> None:
    """默认打开的实例必须是 **pathID 最大的那个**，不是「排序后的第一个」。

    ★ 2026-10-15 用户口径：「**默认指到 pathID 最大的那个**」。
    实测：目录名排序第一个是 52082（战役），最大的是 **52486**（本体最新那一版）。
    这条测试的价值在于**它区分得开这两种实现**：
    若哪天实现退化成 `instance_ids[0]`，它会立刻变红。
    ⚠ 角色（`_rules.json`）现在**只当标签**：下面两条仍然成立，但不再决定默认是哪个。
    """
    # 真件读数（2026-10-15 从本体重抽后实测；判据 = analyze_refs.py 的**引用者类名**）：
    assert dataset.instance_roles.get("52486") == "default_non_campaign"  # DefaultConfigProfile
    # 战役角色带版本号（`@` 前是基础名，界面按基础名查中文标签）
    assert dataset.instance_roles.get("52461").startswith("campaign_route")  # ConfigProfile_1.1.0.4
    assert dataset.instance_roles.get("52461").endswith("1.1.0.4")
    assert dataset.instance_roles.get("52463").endswith("1.2.0")
    # 前提：排序第一个确实不是默认的那个，否则上面的对比就没有意义
    assert dataset.instance_ids[0] == "52082", "数据布局变了，这条测试需要重写"
    assert dataset.default_instance_id == dataset.latest_instance_id
    assert dataset.default_instance_id != dataset.instance_ids[0]


def test_default_instance_survives_missing_rules(tmp_path, dataset) -> None:
    """没有 `_rules.json` 时不能崩，退回第一个实例即可。

    角色信息是**增强**不是运行前提：数据源可能只拷了表、没拷规则文件。
    """
    import shutil

    from ba_mod_maker.core.data import load_dataset as load

    clone = tmp_path / "DataBaseCompiled"
    clone.mkdir()
    shutil.copy(dataset.root / "_schema.json", clone / "_schema.json")
    for path_id in dataset.instance_ids:
        target = clone / path_id
        target.mkdir()
        shutil.copy(dataset.root / path_id / "_manifest.json", target / "_manifest.json")

    reloaded = load(clone)
    assert reloaded.instance_roles == {}
    assert reloaded.stale_role_ids == ()
    # 没有角色信息时退回**最大 pathID**，不是排序第一个。
    assert reloaded.default_instance_id == reloaded.latest_instance_id


def test_latest_instance_is_the_largest_path_id(dataset) -> None:
    """【用户实证】游戏更新后，常用的 DataBaseCompiled 是 pathID 更大的那个。

    本轮未用代码复核这一点（手头只有一个版本的实例目录），
    所以当测试仅固定「取最大」这个实现，不声称证明了规律本身。
    """
    assert dataset.latest_instance_id == max(dataset.instance_ids, key=int)
    # 真件读数：本体最新那一版是 52486（2026-10-15 重抽时实测）。
    assert dataset.latest_instance_id == "52486"


def test_default_falls_back_to_largest_when_rules_are_stale(tmp_path, dataset) -> None:
    """规则文件跟不上游戏更新时，默认必须退回 **pathID 最大**。

    真踩过的坑：早期实现用 `instance_ids[0]`，而那是排序后的**最小**值，
    正好选反——用户打开就在改他不用的那份数据。

    场景构造：目录名换成新一代 pathID（如 52176/52212），
    而 `_rules.json` 还是上一版的（写着 52085/52082）。
    """
    import json
    import shutil

    from ba_mod_maker.core.data import load_dataset as load

    clone = tmp_path / "DataBaseCompiled"
    clone.mkdir()
    shutil.copy(dataset.root / "_schema.json", clone / "_schema.json")
    # 刻意**造一份上一代**的规则：它提到的实例不会出现在下面的目录里。
    # ⛔ 不拷真文件 —— 真文件的 id 会随数据源换代，那样这条测的就成了
    #    「真文件恰好写着谁」，换个数据源就红（2026-10-15 就是这么红的）。
    (clone / "_rules.json").write_text(
        json.dumps(
            {"instances": {"default_non_campaign": 52085, "campaign_route": 52082}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    for old_id, new_id in (("52082", "52176"), ("52085", "52212")):
        target = clone / new_id
        target.mkdir()
        shutil.copy(dataset.root / old_id / "_manifest.json", target / "_manifest.json")

    reloaded = load(clone)
    assert reloaded.instance_ids == ("52176", "52212")
    assert set(reloaded.stale_role_ids) == {"52085", "52082"}, "应当发现规则文件过期"
    assert reloaded.default_instance_id == "52212", "必须是较大的那个"
    assert reloaded.default_instance_id != reloaded.instance_ids[0], (
        "必须与「排序第一个」区分开，否则这条测试没有意义"
    )


# --------------------------------------------------------------------------- #
# 重读整表 / 放弃改动：来源是 zip / 容器时也要能读回来
# --------------------------------------------------------------------------- #


def _instance_from_zip(tmp_path, monkeypatch) -> Instance:
    """把散文件数据源打成一个 BA_Mod.zip，再走**真实打开链路**装成 Instance。

    与 GUI「打开 BA_Mod.zip」得到的是同一套东西 —— `original_bytes` 就是那条链装的。
    包造在 tmp_path 里，**绝不碰用户那份**（AGENTS §2）。
    """
    # BA_Mod.zip 没有 pathID，db_channel 会去游戏容器里查一个；用例不该因此去读
    # 真实的 7 GB 文件，也不该随"这台机器装没装游戏"而变。
    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    loose_dataset = load_dataset(DATASET_ROOT)
    loose = loose_dataset.instance(loose_dataset.default_instance_id)
    package = tmp_path / "测试文件" / "BA_Mod.zip"
    package.parent.mkdir(parents=True, exist_ok=True)
    dbzip.DbZip(
        tables={
            naming.table: (loose.directory / naming.zip_entry).read_bytes()
            for naming in table_names.TABLES
        }
    ).write(package)
    dataset = db_channel.dataset_from_source(open_source(package))
    return dataset.instance(dataset.default_instance_id)


def _instance_from_json_source(tmp_path) -> Instance:
    """容器 / DataBaseCompiled.json 那类来源：磁盘上同样没有散文件。"""
    loose_dataset = load_dataset(DATASET_ROOT)
    loose = loose_dataset.instance(loose_dataset.default_instance_id)
    by_entry = {
        naming.zip_entry: (loose.directory / naming.zip_entry).read_bytes()
        for naming in table_names.TABLES
    }
    slots = [
        container.TableSlot(
            field=naming.field,
            table=naming.table,
            zip_entry=naming.zip_entry,
            data=by_entry[naming.zip_entry],
            iv=bytes(16),
            pad=0,
        )
        for naming in table_names.TABLES
    ]
    meta = container.ObjectMeta(
        object_head=b"",
        object_tail=b"",
        tables=slots,
        path_id=int(loose_dataset.default_instance_id),
        source_sha256="",
    )
    src = OpenedSource(kind=SourceKind.DBC_JSON, path=tmp_path / "DataBaseCompiled.json", meta=meta)
    return db_channel.dataset_from_source(src).instance(loose_dataset.default_instance_id)


def _assert_reload_restores(instance: Instance) -> None:
    """改一格 -> reload_table -> 回到原值，且列序（= JSON 键序）不变。"""
    units = instance.table("Units")
    column = units.column_index("Cost")
    columns_before = units.columns
    rows_before = [list(row) for row in units.rows]
    assert rows_before, "前提：Units 里有行"

    units.rows[0][column] = rows_before[0][column] + 12345
    assert units.rows[0][column] == rows_before[0][column] + 12345, "前提：改动真的落进了内存"

    reloaded = instance.reload_table("Units")

    assert reloaded is not units, "重读要换一份 Table 对象（界面靠这个刷新）"
    assert instance.table("Units") is reloaded, "缓存里也得是新那份"
    assert reloaded.columns == columns_before, "列序（JSON 键序）变了"
    assert reloaded.source_columns == columns_before
    assert reloaded.rows == rows_before, "重读没有回到打开时的原值"


def test_reload_table_on_a_zip_source_restores_the_opened_bytes(tmp_path, monkeypatch) -> None:
    """zip 来源：保存修好了，重读整表也要能读 —— 磁盘上没有散文件，靠 original_bytes。"""
    _assert_reload_restores(_instance_from_zip(tmp_path, monkeypatch))


def test_reload_table_on_a_json_source_restores_the_opened_bytes(tmp_path) -> None:
    """容器 / DataBaseCompiled.json 来源同理（同一条 dataset_from_source）。"""
    _assert_reload_restores(_instance_from_json_source(tmp_path))


def test_reload_table_on_a_directory_source_is_unchanged() -> None:
    """散文件来源语义照旧：仍旧从 directory/<表>.json 读，且丢掉内存里的改动。"""
    dataset = load_dataset(DATASET_ROOT)
    instance = dataset.instance(dataset.default_instance_id)
    assert instance.original_bytes is None

    units = instance.table("Units")
    column = units.column_index("Cost")
    original = units.rows[0][column]
    units.rows[0][column] = original + 1

    reloaded = instance.reload_table("Units")

    assert reloaded is not units
    assert reloaded.rows[0][column] == original


def test_reload_without_any_source_says_it_cannot_reload(tmp_path, monkeypatch) -> None:
    """两处都没有时：说清「重读不了」（与保存同口径）；表名不认识仍旧 KeyError。"""
    instance = _instance_from_zip(tmp_path, monkeypatch)
    instance.original_bytes = {}
    instance.directory = tmp_path / "空目录"
    (tmp_path / "空目录").mkdir()

    with pytest.raises(DataSourceError) as excinfo:
        instance.reload_table("Units")

    assert "找不到 Units 的源文件" in str(excinfo.value)
    assert "重读不了" in (excinfo.value.hint or "")

    with pytest.raises(KeyError):
        instance.reload_table("这张表不存在")
