"""保存：BA_Mod.zip 的格式与写后复核（零 Qt）。

两条要守的东西：

1. **格式是游戏读的那个**（平铺 24 个条目）—— 换格式游戏就不认；
2. **未动的表逐字节没变** —— 一读一写就变字节的坑实测踩过
   （Mobility 的 1.5E+08 -> 150000000.0）；
3. **源是 BA_Mod.zip / 容器时也存得下去** —— 那几种来源磁盘上没有散文件，
   未改动的表要从"打开时那份字节"（`Instance.original_bytes`）拿。
   用户实机报的"找不到 Units 的源文件"就是这条缺失时的症状。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ba_mod_maker.app.shell import db_channel
from ba_mod_maker.app.shell.edit_session import SAVE_DIR_ENV, EditSession
from ba_mod_maker.core.asset import container, dbzip
from ba_mod_maker.core.asset import tables as table_names
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.data import Dataset, Instance, find_dataset_root, load_dataset
from ba_mod_maker.core.edit.changeset import Changeset
from ba_mod_maker.core.edit.package import (
    PACKAGE_FORMAT,
    PACKAGE_NAME,
    default_save_target,
    plan_package,
    policy_for,
    read_package_rows,
    replace_package,
    verify_package,
    write_package,
)
from ba_mod_maker.core.errors import DataSourceError, UserFacingError
from ba_mod_maker.core.session import OpenedSource, SourceKind, open_source

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"

#: 别名条目（`PlaneFlyPreset.json`）**不在磁盘上** —— 它只是同一份字节的第二个名字，
#: 而游戏读的正是它（见 `tables.ZIP_ENTRY_ALIASES`）。凡按"源文件 / 源包条目"逐条比对的
#: 地方都要按它跳过；"未改动的表逐字节没变"由主条目那条守着。
ALIAS_ENTRIES = frozenset(table_names.ALIAS_ENTRY_NAMES)


def _main_entries(plan) -> list[tuple[str, bytes]]:
    """计划里的**主条目**（24 张表，不含别名条目）。"""
    return [(entry, blob) for entry, blob in plan.entries if entry not in ALIAS_ENTRIES]


def _main_entry_names(plan) -> list[str]:
    """计划里的**主条目名**（24 个，不含别名条目）。"""
    return [entry for entry in plan.entry_names if entry not in ALIAS_ENTRIES]


@pytest.fixture()
def instance():
    """每个用例一份**新加载**的实例：用例会改内存里的行，不能互相污染。"""
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


def test_plan_has_the_twenty_four_game_entries_plus_the_fly_preset_alias(instance) -> None:
    """24 张主条目 **＋ 1 个别名条目**。

    ❗ 别名条目不是冗余（2026-10-02 实机翻案）：游戏取行时按**模型类型名**
    `typeof(T).Name` 查表，飞行预设那张的类型叫 `Table<PlaneFlyPreset>`（**无 s**）——
    只写复数那一份，游戏在 `ReadTablesFromZip` 里就把它 `continue` 掉了，
    结果是飞行预设表空 → `LoadUnits.LoadMobility` NRE → **军械库一张卡都没有**。
    """
    plan = plan_package(instance)
    assert len(plan.entries) == 25
    assert plan.entry_names == table_names.package_entry_names()
    assert "PlaneFlyPresets.json" in plan.entry_names, "FlyPresets 的条目名是唯一例外"
    assert "FlyPresets.json" not in plan.entry_names
    # 别名条目：紧跟在主条目后面，**字节逐字节相同**
    by_entry = dict(plan.entries)
    assert "PlaneFlyPreset.json" in by_entry, "少写这一份 = 游戏那张表是空的"
    assert by_entry["PlaneFlyPreset.json"] == by_entry["PlaneFlyPresets.json"]
    assert plan.entry_names.index("PlaneFlyPreset.json") == (
        plan.entry_names.index("PlaneFlyPresets.json") + 1
    )


def test_untouched_tables_are_byte_identical_to_the_source(instance) -> None:
    """**这条是"未动的表逐字节没变"的机器判据。**"""
    plan = plan_package(instance)
    for entry, blob in _main_entries(plan):
        source = instance.directory / entry
        assert source.is_file(), f"源文件不在：{source}"
        assert blob == source.read_bytes(), f"{entry} 的字节变了"


def test_only_the_changed_table_is_reserialized(instance) -> None:
    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 60)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())
    assert plan.changed == ("Units",)
    assert len(plan.untouched) == 23

    by_entry = dict(plan.entries)
    rows = json.loads(by_entry["Units.json"].decode("utf-8"))
    assert next(row["Cost"] for row in rows if row["Id"] == 1) == 60
    # 其余 23 张逐字节等于源文件（别名条目不在磁盘上，按它跳过）
    for entry in _main_entry_names(plan):
        if entry == "Units.json":
            continue
        assert by_entry[entry] == (instance.directory / entry).read_bytes()


def test_a_changed_fly_presets_row_is_not_silently_dropped(instance) -> None:
    """改过 `FlyPresets` 的一行，包里必须是新值 —— 不许走「源字节照搬」那一支。

    回归（2026-10-01，P0）：`plan_package` 早先拿 `naming.table`（`FlyPresets`）去比
    `changed` 集合，而那个集合装的是 `_resolve()` 之后的**真名** —— 当时目录来源按
    文件名建表、真名是 `PlaneFlyPresets`，两边永不相等，于是这张表的改动被当成
    「没变」，走「原字节照搬」那一支：**包里还是旧值，而且没有任何提示**。

    现在两个来源的表名都归一到 `FlyPresets`（`core/data/loader.py`），两边比的是
    同一个名字；这条用例守的仍是那句话：**改过的表不许被静默丢掉**。
    """
    assert "FlyPresets" in instance.table_names
    assert "PlaneFlyPresets" not in instance.table_names, "归一之后不该再出现文件名那一套名字"

    table = instance.table("FlyPresets")
    row = table.rows[0]
    row_id = row[table.column_index("Id")]
    column = next(
        name
        for name in table.columns
        if name != "Id"
        and isinstance(row[table.column_index(name)], (int, float))
        and not isinstance(row[table.column_index(name)], bool)
    )
    old_value = row[table.column_index(column)]
    source_bytes = (instance.directory / "PlaneFlyPresets.json").read_bytes()

    changeset = Changeset(instance)
    changeset.set_value("FlyPresets", row_id, column, old_value + 3)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())

    assert plan.changed == ("FlyPresets",), "改动静默丢了：这一支把源字节照搬进了包"
    blob = dict(plan.entries)["PlaneFlyPresets.json"]
    assert blob != source_bytes, "条目还是源字节 —— 等于没保存"
    written = json.loads(blob.decode("utf-8"))
    assert next(r[column] for r in written if r["Id"] == row_id) == old_value + 3


def test_the_file_name_style_is_still_resolvable(instance) -> None:
    """旧写法（文件名那一套 `PlaneFlyPresets`）仍能被 `resolve_table_name` 认出来。

    归一之后 `Instance.table_names` 里只有规范名 `FlyPresets`，但老工具脚本 /
    老包可能还按**文件名**那一套叫它 —— `core/asset/tables.py` 的别名表两个方向
    都登记着，这条守住它（也守住"表名集合只有一套"）。
    """
    resolve = table_names.resolve_table_name

    assert resolve("PlaneFlyPresets", instance.table_names) == "FlyPresets"
    assert resolve("FlyPresets", instance.table_names) == "FlyPresets"
    assert "PlaneFlyPresets" not in instance.table_names

    table = instance.table("FlyPresets")
    row = table.rows[0]
    row_id = row[table.column_index("Id")]

    changeset = Changeset(instance)
    changeset.set_value("FlyPresets", row_id, "Name", "改过的名字")
    plan = plan_package(instance, changed_tables=("FlyPresets",))

    assert plan.changed == ("FlyPresets",)
    written = json.loads(dict(plan.entries)["PlaneFlyPresets.json"].decode("utf-8"))
    assert next(r["Name"] for r in written if r["Id"] == row_id) == "改过的名字"


def test_reverting_everything_makes_the_package_byte_identical(instance) -> None:
    """改回原值 -> 包里一张表都没变（"没有改动"就该是没有改动）。"""
    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 60)
    changeset.revert_all()
    plan = plan_package(instance, changed_tables=changeset.changed_tables())
    assert plan.changed == ()
    for entry, blob in _main_entries(plan):
        assert blob == (instance.directory / entry).read_bytes(), entry


def test_write_and_read_back(instance, tmp_path) -> None:
    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 60)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())
    target = tmp_path / PACKAGE_NAME
    write_package(plan, target)

    assert verify_package(plan, target) == ()
    assert zipfile.ZipFile(target).namelist() == list(plan.entry_names)
    rows = read_package_rows(target)
    assert next(row["Cost"] for row in rows["Units"] if row["Id"] == 1) == 60
    assert len(rows) == 24


def test_verify_catches_a_tampered_package(instance, tmp_path) -> None:
    """写后复核要真的会报错（不然就是走过场）。"""
    plan = plan_package(instance)
    target = tmp_path / PACKAGE_NAME
    with zipfile.ZipFile(target, "w") as archive:
        for name, blob in plan.entries:
            archive.writestr(name, blob if name != "Units.json" else b"[]")
    problems = verify_package(plan, target)
    assert any("Units.json" in text for text in problems)
    assert verify_package(plan, tmp_path / "不存在.zip")


def test_policy_says_only_one_instance_is_written(instance) -> None:
    """口径落到界面上的一句话：**只写这一份实例**，不假装两份都写了。"""
    policy = policy_for(instance)
    assert policy.format == PACKAGE_FORMAT
    assert policy.instance_id == instance.path_id
    assert "52085" in policy.headline
    assert "52082" not in policy.headline
    assert "一个包只能装一份实例" in policy.reason


def test_package_format_is_the_flat_game_format() -> None:
    """格式没变：条目名 = <表名>.json（游戏 ZipFile.OpenRead 按条目名分派）。"""
    assert PACKAGE_FORMAT == "flat-v1"
    assert PACKAGE_NAME == "BA_Mod.zip"


# --------------------------------------------------------------------------- #
# 源是 BA_Mod.zip / 容器：未改动的表从「打开时那份字节」来
# --------------------------------------------------------------------------- #

#: 用户手上那份源包（工作区里的「测试文件\BA_Mod.zip」）。**只读** ——
#: 用例一律先拷到 tmp_path 再动它。找不到就跳过（换台机器 / 换个布局都成立）。
USER_PACKAGE = DATASET_ROOT.parent / "测试文件" / PACKAGE_NAME if DATASET_ROOT is not None else None


def _entry_bytes(instance: Instance) -> dict[str, bytes]:
    """散文件数据源里每个条目名的原始字节（造源包 / 当基准用）。"""
    return {
        naming.zip_entry: (instance.directory / naming.zip_entry).read_bytes()
        for naming in table_names.TABLES
    }


def _zip_source_instance(
    tmp_path, monkeypatch, package: Path | None = None
) -> tuple[Path, Dataset]:
    """造好源包 -> 走**真实打开链路**（open_source + db_channel）装成 Dataset。

    与 GUI「打开 BA_Mod.zip」之后拿到的是一套东西 —— 报错就发生在这一步之后的保存里。
    源包放在自己的子目录里（真实场景是「源在 测试文件 目录下、保存写到它上一级」）。
    """
    # BA_Mod.zip 没有 pathID，db_channel 会去游戏容器里查一个；用例不该因此去读
    # 真实的 7 GB 文件，也不该随"这台机器装没装游戏"而变。
    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    source = tmp_path / "源" / PACKAGE_NAME
    source.parent.mkdir(parents=True, exist_ok=True)
    if package is None:
        by_entry = _entry_bytes(load_dataset(DATASET_ROOT).instance(INSTANCE_ID))
        dbzip.DbZip(
            tables={naming.table: by_entry[naming.zip_entry] for naming in table_names.TABLES}
        ).write(source)
    else:
        source.write_bytes(package.read_bytes())
    return source, db_channel.dataset_from_source(open_source(source))


def test_zip_source_keeps_untouched_entries_byte_identical(tmp_path, monkeypatch) -> None:
    """**用户实机那条路**：从 BA_Mod.zip 打开 -> 改一张表 -> 未改动的表与源包逐字节相同。

    这是"找不到 Units 的源文件"那个 bug 的机器判据：打开 zip 时磁盘上没有散文件，
    未改动的表只能从**打开时那个包**里拿字节。
    """
    source, dataset = _zip_source_instance(tmp_path, monkeypatch)
    with zipfile.ZipFile(source) as archive:
        before = {name: archive.read(name) for name in archive.namelist()}

    instance = dataset.instance(dataset.default_instance_id)
    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 60)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())

    assert plan.changed == ("Units",)
    assert len(plan.untouched) == 23
    by_entry = dict(plan.entries)
    for entry in _main_entry_names(plan):
        if entry == "Units.json":
            continue
        assert by_entry[entry] == before[entry], f"{entry} 与源包条目不一致"
    # Mobility 的明文里有 1.5E+08 —— json 一读一写就变成 150000000.0。
    # 这条是真的在盯"有没有被重新序列化"。
    assert b"1.5E+08" in by_entry["Mobility.json"]
    # 改过的那张确实重新序列化了，改动也在里面
    assert by_entry["Units.json"] != before["Units.json"]
    rows = json.loads(by_entry["Units.json"].decode("utf-8"))
    assert next(row["Cost"] for row in rows if row["Id"] == 1) == 60


def test_zip_source_writes_and_reads_back(tmp_path, monkeypatch) -> None:
    """整份保存：写盘 -> verify_package 通过 -> 读回来改动落进去了；源包一个字节没动。"""
    source, dataset = _zip_source_instance(tmp_path, monkeypatch)
    before = source.read_bytes()

    instance = dataset.instance(dataset.default_instance_id)
    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 60)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())

    target = tmp_path / PACKAGE_NAME  # 「源在 源\ 下、写到它上一级」那种落点
    write_package(plan, target)

    assert verify_package(plan, target) == ()
    rows = read_package_rows(target)
    assert len(rows) == 24
    assert next(row["Cost"] for row in rows["Units"] if row["Id"] == 1) == 60
    assert source.read_bytes() == before, "源包被写了 —— 保存不该碰它"


@pytest.mark.skipif(
    USER_PACKAGE is None or not USER_PACKAGE.is_file(),
    reason="本机没有用户那份 测试文件\\BA_Mod.zip",
)
def test_the_users_own_package_can_be_saved(tmp_path, monkeypatch) -> None:
    """**用户报的那个包**（只读，先拷到 tmp_path）：打开 -> 改一格 -> 未改动的表逐字节不变。

    它是唯一一份"实机现场"样本：成品包的真实形状（连用户自己改过的 Units 也在），
    比临时造的包更能代表现场。
    """
    source, dataset = _zip_source_instance(tmp_path, monkeypatch, package=USER_PACKAGE)
    with zipfile.ZipFile(source) as archive:
        before = {name: archive.read(name) for name in archive.namelist()}

    instance = dataset.instance(dataset.default_instance_id)
    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 987654)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())

    assert plan.changed == ("Units",)
    by_entry = dict(plan.entries)
    for entry in _main_entry_names(plan):
        if entry == "Units.json":
            continue
        assert by_entry[entry] == before[entry], f"{entry} 与源包条目不一致"

    target = tmp_path / PACKAGE_NAME
    write_package(plan, target)
    assert verify_package(plan, target) == ()
    assert (
        next(row["Cost"] for row in read_package_rows(target)["Units"] if row["Id"] == 1) == 987654
    )


def test_container_style_source_also_saves_from_the_opened_bytes(tmp_path) -> None:
    """容器 / DataBaseCompiled.json 打开的会话同样有字节来源（同一条 dataset_from_source）。

    现场形状：GUI 里开 data.unity3d 改一格 —— 磁盘上同样没有散文件，
    以前一样会弹"找不到 Units 的源文件"。
    """
    loose = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    by_entry = _entry_bytes(loose)
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
        path_id=52085,
        source_sha256="",
    )
    src = OpenedSource(kind=SourceKind.DBC_JSON, path=tmp_path / "DataBaseCompiled.json", meta=meta)
    dataset = db_channel.dataset_from_source(src)
    instance = dataset.instance(dataset.default_instance_id)

    changeset = Changeset(instance)
    changeset.set_value("Units", 1, "Cost", 60)
    plan = plan_package(instance, changed_tables=changeset.changed_tables())

    planned = dict(plan.entries)
    for entry in _main_entry_names(plan):
        if entry == "Units.json":
            continue
        assert planned[entry] == by_entry[entry], entry


def test_directory_source_still_reads_loose_files(instance) -> None:
    """散文件数据源**行为照旧**：字节仍旧从 directory/<条目>.json 来，新字段是 None。"""
    assert instance.original_bytes is None
    plan = plan_package(instance)
    for entry, blob in _main_entries(plan):
        assert blob == (instance.directory / entry).read_bytes(), entry


def test_loose_file_wins_over_original_bytes(instance) -> None:
    """取字节的顺序是「散文件 -> original_bytes」：散文件在就认散文件（散文件源一个字节不变）。"""
    instance.original_bytes = dict.fromkeys(table_names.ENTRY_NAMES, b"[]")
    plan = plan_package(instance)
    for entry, blob in _main_entries(plan):
        assert blob == (instance.directory / entry).read_bytes(), entry


def test_source_without_bytes_says_it_cannot_save(tmp_path) -> None:
    """两个来源都没有时：报错要说清「这个来源存不了」，不能静默重新序列化。"""
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    instance.directory = tmp_path / "空目录"
    (tmp_path / "空目录").mkdir()

    with pytest.raises(DataSourceError) as excinfo:
        plan_package(instance)

    assert "找不到" in str(excinfo.value)
    assert "存不了" in (excinfo.value.hint or "")


def test_source_missing_one_entry_names_the_entry(tmp_path) -> None:
    """来源有字节但缺这一条：报的是**条目名**（另一条分支，别混成"找不到文件"）。"""
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    instance.directory = tmp_path / "空目录"
    (tmp_path / "空目录").mkdir()
    instance.original_bytes = {}

    with pytest.raises(DataSourceError) as excinfo:
        plan_package(instance)

    assert "没有 Units.json" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 保存写回「你打开的那个文件」（用户口径 2026-09-30）
#
# 一句话：打开的是**文件**就写回那个文件本身（同一个路径，原子替换）；打开的是目录
# 就照旧写同级目录的 BA_Mod.zip；`data.unity3d` 保持写外部包（7.1 GB 的重写不能挂在
# 自动保存上）。并且**一律不生成 .bak / 任何副本**。
# --------------------------------------------------------------------------- #

#: 原包里那些**我们不认识的条目**。游戏会静默跳过它们，所以它们只能靠"我们原样带回去"。
#: ⚠️ 单数 `PlaneFlyPreset.json` **不算**这类 —— 它是我们认得、而且**必须写**的别名条目
#: （2026-10-02 实机翻案，见 `tables.ZIP_ENTRY_ALIASES`）。
EXTRAS = {
    "SomeonesExtra.json": b'{"hello":1}',
    "readme.txt": b"hi",
    "子目录/notes.txt": b"nested",
}


def _zip_source_with_extras(tmp_path, monkeypatch) -> tuple[Path, Dataset]:
    """造一个**带未知条目**的源包，走真实打开链路（open_source + db_channel）装成 Dataset。"""
    # BA_Mod.zip 没有 pathID，db_channel 会去游戏容器里查一个；用例不该因此去读
    # 真实的 7 GB 文件，也不该随"这台机器装没装游戏"而变。
    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    source = tmp_path / "我的包" / PACKAGE_NAME
    source.parent.mkdir(parents=True, exist_ok=True)
    by_entry = _entry_bytes(load_dataset(DATASET_ROOT).instance(INSTANCE_ID))
    dbzip.DbZip(
        tables={naming.table: by_entry[naming.zip_entry] for naming in table_names.TABLES},
        extra=dict(EXTRAS),
    ).write(source)
    return source, db_channel.dataset_from_source(open_source(source))


def _slots_of_loose_dataset() -> list[container.TableSlot]:
    """散文件数据源那 24 张表的字节，装成容器字段（造 json / 容器来源用）。"""
    by_entry = _entry_bytes(load_dataset(DATASET_ROOT).instance(INSTANCE_ID))
    return [
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


def _json_source(tmp_path) -> tuple[Path, Dataset]:
    """造一个 `DataBaseCompiled.json` 来源，并走真实打开链路装成 Dataset。"""
    meta = container.ObjectMeta(
        object_head=b"",
        object_tail=b"",
        tables=_slots_of_loose_dataset(),
        path_id=int(INSTANCE_ID),
        source_sha256="",
    )
    path = tmp_path / "DataBaseCompiled.json"
    path.write_text(meta.to_json_text(), encoding="utf-8")
    return path, db_channel.dataset_from_source(open_source(path))


def _autosave_session(dataset: Dataset) -> EditSession:
    """一份接好实例的写盘通道。**不落盘**，由用例决定改哪一格。"""
    instance = dataset.instance(dataset.default_instance_id)
    session = EditSession()  # QObject 不需要 QApplication：直接连接不靠事件循环
    session.set_instance(instance)
    return session


def test_autosave_writes_back_to_the_opened_package(tmp_path, monkeypatch) -> None:
    """**本轮的口径**：打开一个包改一格，保存写回**那个包**，未知条目原样在。

    四条判据一次钉死：① 落到同一个路径；② 24 个表条目按预期更新；
    ③ 未知条目还在、字节相同；④ 目录里没有多出任何 .bak / 副本。
    """
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)  # 关掉用例的临时落点接缝
    source, dataset = _zip_source_with_extras(tmp_path, monkeypatch)
    with zipfile.ZipFile(source) as archive:
        before = {name: archive.read(name) for name in archive.namelist()}

    session = _autosave_session(dataset)
    # ① 落点 = **他打开的那个文件**（不是它上一级的 BA_Mod.zip）
    assert session.default_save_path() == source

    changeset = session.changeset
    assert changeset is not None
    changeset.set_value("Units", 1, "Cost", 60)
    message = session.save()

    # 状态栏那句要能看出写到哪个文件（完整路径，不是只有文件名）
    assert str(source) in message
    # ① 还是同一个路径，而且没有第二个包 / 临时件 / .bak
    assert [p.name for p in source.parent.iterdir()] == [PACKAGE_NAME]
    # ② 24 个表条目都在，改动确实落进去了
    rows = read_package_rows(source)
    assert len(rows) == 24
    assert next(row["Cost"] for row in rows["Units"] if row["Id"] == 1) == 60
    # ③ 未知条目还在、字节相同；未改动的 23 张表也逐字节没变
    with zipfile.ZipFile(source) as archive:
        for name, blob in EXTRAS.items():
            assert archive.read(name) == blob, f"未知条目 {name} 丢了或被改了"
        assert archive.read("Units.json") != before["Units.json"]
        for entry, blob in before.items():
            if entry == "Units.json" or entry in EXTRAS:
                continue
            assert archive.read(entry) == blob, f"{entry} 被无谓地重新序列化了"
    # ④ 目录里只有那一个包（没有 .bak、没有 .tmp、没有副本）
    assert not [p for p in source.parent.iterdir() if p.name != PACKAGE_NAME]


def test_plan_carries_the_unknown_entries_and_verify_checks_them(tmp_path, monkeypatch) -> None:
    """计划里必须带上未知条目，而且**写后复核要真的会看它们**（不然"保留"没人验）。"""
    _source, dataset = _zip_source_with_extras(tmp_path, monkeypatch)
    instance = dataset.instance(dataset.default_instance_id)

    plan = plan_package(instance)
    assert plan.extra_names == tuple(EXTRAS), "默认就要把原包的未知条目带上"

    out = tmp_path / "另存"
    out.mkdir()
    target = out / PACKAGE_NAME
    write_package(plan, target)
    assert verify_package(plan, target) == ()
    with zipfile.ZipFile(target) as archive:
        for name, blob in EXTRAS.items():
            assert archive.read(name) == blob

    # 反向：把未知条目丢掉时复核必须报出来
    with zipfile.ZipFile(target, "w") as archive:
        for name, blob in plan.entries:
            archive.writestr(name, blob)
    assert any("SomeonesExtra.json" in text for text in verify_package(plan, target))


def test_replace_package_keeps_the_original_when_verify_fails(tmp_path, monkeypatch) -> None:
    """`replace_package` 是**先复核、后替换**：复核不过时原文件一个字节都没动。

    写回的是用户自己那个包 —— 没有第二份，所以顺序不能反。
    """
    _source, dataset = _zip_source_with_extras(tmp_path, monkeypatch)
    instance = dataset.instance(dataset.default_instance_id)
    opened = instance.opened_file
    assert opened is not None, "前提：这个实例是从文件打开的"
    source = opened.path
    original = source.read_bytes()

    plan = plan_package(instance)
    # 伪造成"复核不通过"：临时件里写的是别的字节
    monkeypatch.setattr(
        "ba_mod_maker.core.edit.package._dump",
        lambda _plan, path: Path(path).write_bytes(b"not a zip at all"),
    )
    with pytest.raises(UserFacingError) as excinfo:
        replace_package(plan, source)
    assert "原文件未动" in (excinfo.value.hint or "")

    assert source.read_bytes() == original, "复核不过却把原包换了"
    assert [p.name for p in source.parent.iterdir()] == [PACKAGE_NAME], "临时件没清掉"


def test_autosave_writes_back_to_the_opened_json(tmp_path, monkeypatch) -> None:
    """`DataBaseCompiled.json` 来源同样写回原文件 —— 而且**还是 json**（不是包）。"""
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    path, dataset = _json_source(tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))

    session = _autosave_session(dataset)
    assert session.default_save_path() == path
    changeset = session.changeset
    assert changeset is not None
    changeset.set_value("Units", 1, "Cost", 60)
    session.save()

    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    after = json.loads(path.read_text(encoding="utf-8"))
    was = {t["table"]: t["data"] for t in before["tables"]}
    now = {t["table"]: t["data"] for t in after["tables"]}
    assert now["Units"] != was["Units"], "改动没进去"
    for table, payload in was.items():
        if table == "Units":
            continue
        assert now[table] == payload, f"{table} 被无谓地重新序列化了"
    # 还是产品认得的那个 json（按内容识别，不按扩展名）
    with open_source(path) as again:
        rows = again.table("Units").json_payload()
    assert next(row["Cost"] for row in rows if row["Id"] == 1) == 60


def test_directory_source_still_writes_next_to_the_dataset(instance, monkeypatch) -> None:
    """目录来源：落点**不变** —— 还是数据源同级目录的 BA_Mod.zip。"""
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    session = EditSession()
    session.set_instance(instance)
    assert instance.opened_file is None
    assert default_save_target(instance) == Path(instance.directory).parent / PACKAGE_NAME
    assert session.default_save_path() == Path(instance.directory).parent / PACKAGE_NAME


def test_unity3d_source_still_saves_to_the_external_package(tmp_path, monkeypatch) -> None:
    """`data.unity3d` 来源：**不许**顺手改成写回那个 7 GB 容器。

    重写一次要 7.1 GB / 约 4 分钟，挂在"改一格就自动保存"上是灾难。
    """
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    game_like = tmp_path / "BrokenArrow_Data"
    game_like.mkdir()
    container_path = game_like / "data.unity3d"
    container_path.write_bytes(b"UnityFS-fake")

    meta = container.ObjectMeta(
        object_head=b"",
        object_tail=b"",
        tables=_slots_of_loose_dataset(),
        path_id=int(INSTANCE_ID),
        source_sha256="",
    )
    src = OpenedSource(kind=SourceKind.UNITY3D, path=container_path, meta=meta)
    dataset = db_channel.dataset_from_source(src)
    session = _autosave_session(dataset)

    assert session.instance is not None
    assert session.instance.opened_file is None, "容器来源不该有写回文件"
    assert session.default_save_path() == tmp_path / PACKAGE_NAME

    changeset = session.changeset
    assert changeset is not None
    changeset.set_value("Units", 1, "Cost", 60)
    session.save()

    out = tmp_path / PACKAGE_NAME
    assert out.is_file(), "容器来源仍然写外部包"
    assert container_path.read_bytes() == b"UnityFS-fake", "7 GB 容器被碰了"
    rows = read_package_rows(out)
    assert next(row["Cost"] for row in rows["Units"] if row["Id"] == 1) == 60


def test_second_autosave_does_not_revert_the_first_table(tmp_path, monkeypatch) -> None:
    """改两张表、存两次：第一次那笔改动**不能**被按"打开时"的字节抹掉。

    未改动的表是从**打开时那份来源**逐字节搬的，所以只带"本次改的表"去保存时，
    第二次保存会把第一张表写回原样 —— 落点是用户自己的包时，这就是静默的数据丢失。
    """
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    source, dataset = _zip_source_with_extras(tmp_path, monkeypatch)
    session = _autosave_session(dataset)
    instance = session.instance
    assert instance is not None
    changeset = session.changeset
    assert changeset is not None

    changeset.set_value("Units", 1, "Cost", 60)
    session.save()

    other = instance.table("Mobility")
    row_id = other.rows[0][other.column_index("Id")]
    column = next(
        name
        for index, name in enumerate(other.columns)
        if name != "Id"
        and isinstance(other.rows[0][index], int)
        and not isinstance(other.rows[0][index], bool)
    )
    old_value = other.rows[0][other.column_index(column)]
    changeset.set_value("Mobility", row_id, column, old_value + 7)
    session.save()

    rows = read_package_rows(source)
    assert next(row["Cost"] for row in rows["Units"] if row["Id"] == 1) == 60, (
        "第一次那笔改动在第二次保存时被抹掉了"
    )
    mobility = next(row for row in rows["Mobility"] if row["Id"] == row_id)
    assert mobility[column] == old_value + 7


def test_policy_names_the_file_we_write_back_to(tmp_path, monkeypatch) -> None:
    """界面那句话要能看出**写到哪个文件**：完整路径，不能退化成只有文件名。"""
    source, dataset = _zip_source_with_extras(tmp_path, monkeypatch)
    instance = dataset.instance(dataset.default_instance_id)
    policy = policy_for(instance)
    assert policy.target == str(source)
    assert "保存写回" in policy.headline
    assert str(source) in policy.reason
    assert str(source) in str(policy.target)
