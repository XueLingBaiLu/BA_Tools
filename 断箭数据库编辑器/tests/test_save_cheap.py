"""保存那一刀：**没有新改动 -> 一次盘都不写**（2026-10-01）。

背景（读数在 `_work/probe_save_cost.py`，存于 `_work/tmp/probe_save_cost.json`）：
一次保存 wall **≈55 ms**，其中 **zip 压缩 42–44 ms，占 75–80%** —— 用户那句
「全量保存时 GUI 会卡一下」就是它。而"改完存一次、**没有新改动**再点一次 💾"
会经 `_tables_to_write()` 把 `_written`（上次写进这个落点的那几张表）一起带出来，
把整个包重新序列化 + 重新压一遍：实测 wall **57.6 ms**（zip 压缩 42.2 ms），
盘上那份却已经是最新的 —— 纯白干。

这里钉住三条（都不许顺手放宽）：

1. 没有新改动 -> **不写盘**：连 `save_instance` 都不进，落点文件的字节与 mtime 一个字不变；
2. 有新改动 -> 照原样写，而且值要**真的落进包里**（`read_package_rows` 读回来验，
   不能只看返回值那句人话）；
3. `_tables_to_write()` 那条口径不许破坏（AGENTS §4.5）：改了第二张表再存，
   第一次那笔改动**不能**被按"打开时"的字节抹掉。

⚠️ 保存**口径**一个字没动：落点、临时文件 + 原子替换、"先复核后替换"、
`_verify_dbzip_written` 都不在这条早退的射程里 —— 这里守的只是"没有改动就别写盘"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.app.shell import db_channel
from ba_mod_maker.app.shell import edit_session as edit_session_module
from ba_mod_maker.app.shell.edit_session import SAVE_DIR_ENV, EditSession
from ba_mod_maker.core.asset import dbzip
from ba_mod_maker.core.asset import tables as table_names
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.data import Dataset, find_dataset_root, load_dataset
from ba_mod_maker.core.edit.package import PACKAGE_NAME, read_package_rows
from ba_mod_maker.core.session import open_source

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: 用例改动的那一格（真实数据里就是 60 / 61 这一档的小整数）
UNIT_ROW = 1
UNIT_COLUMN = "Cost"


def _zip_source(tmp_path, monkeypatch) -> tuple[Path, Dataset]:
    """造一个源包并走**真实打开链路**装成 Dataset（不碰游戏目录、不碰数据源目录）。"""
    # BA_Mod.zip 没有 pathID，db_channel 会去游戏容器里查一个；用例不该因此去读
    # 真实的 7 GB 文件，也不该随"这台机器装没装游戏"而变。
    monkeypatch.setenv(game_mod.ENV_VAR, str(tmp_path / "没有游戏"))
    source = tmp_path / "我的包" / PACKAGE_NAME
    source.parent.mkdir(parents=True, exist_ok=True)
    instance = load_dataset(DATASET_ROOT).instance(INSTANCE_ID)
    dbzip.DbZip(
        tables={
            naming.table: (instance.directory / naming.zip_entry).read_bytes()
            for naming in table_names.TABLES
        }
    ).write(source)
    return source, db_channel.dataset_from_source(open_source(source))


def _session(dataset: Dataset, *, save_path=None) -> EditSession:
    """一份接好实例的写盘通道（QObject 不需要 QApplication：直接连接不靠事件循环）。"""
    session = EditSession()
    session.set_instance(dataset.instance(dataset.default_instance_id))
    if save_path is not None:
        session.set_save_path(save_path)
    return session


def _save_instance_spy(monkeypatch) -> list[tuple[str, ...]]:
    """把**真正写盘那一步**换成计数包装：每写一次盘记一条 changed_tables。"""
    calls: list[tuple[str, ...]] = []
    original = edit_session_module.save_instance

    def spy(instance, changed_tables, **kwargs):
        calls.append(tuple(changed_tables))
        return original(instance, changed_tables, **kwargs)

    monkeypatch.setattr(edit_session_module, "save_instance", spy)
    return calls


def _stamp(path: Path) -> tuple[int, int]:
    """(字节数, mtime_ns) —— "一个字节都没写"要用它证明，不能只看返回值。"""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


# --------------------------------------------------------------------------- #
# ① 没有新改动 -> 一次盘都不写
# --------------------------------------------------------------------------- #


def test_saving_twice_without_new_changes_only_writes_once(tmp_path, monkeypatch) -> None:
    """改一格存一次；**没有新改动**再点一次保存 -> 不写盘、字节与 mtime 一个字不变。

    这就是用户实机能感觉到的那一下：从前第二次会整个包重压一遍（57.6 ms）。
    """
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    source, dataset = _zip_source(tmp_path, monkeypatch)
    session = _session(dataset)
    calls = _save_instance_spy(monkeypatch)
    changeset = session.changeset
    assert changeset is not None

    changeset.set_value("Units", UNIT_ROW, UNIT_COLUMN, 60)
    first = session.save()
    assert calls == [("Units",)], "第一次保存应当只写改动的那一张表"
    assert str(source) in first

    before = _stamp(source)
    again = session.save()
    assert again == "没有改动，未写盘"
    assert len(calls) == 1, "没有新改动却又写了一次盘"
    assert _stamp(source) == before, "没有新改动，落点文件的字节 / mtime 却变了"
    # 目录里也不许多出临时件（写盘那条路会先写 .tmp 再原子替换）
    assert [p.name for p in source.parent.iterdir()] == [PACKAGE_NAME]

    # 第三次、以及 silent 那条路：同样一次都不写
    assert session.save(silent=True) == ""
    assert len(calls) == 1


def test_a_session_without_any_edit_never_touches_the_disk(tmp_path, monkeypatch) -> None:
    """刚接上实例、一笔没改：保存只回一句话，**不进写盘那一步**。"""
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    source, dataset = _zip_source(tmp_path, monkeypatch)
    session = _session(dataset)
    calls = _save_instance_spy(monkeypatch)
    before = _stamp(source)

    assert session.save() == "没有改动，未写盘"
    assert session.save(silent=True) == ""
    assert calls == []
    assert _stamp(source) == before


def test_changing_the_value_back_makes_it_a_no_op_again(tmp_path, monkeypatch) -> None:
    """改了又改回原值 = **账本里没有改动** -> 再点保存不写盘。

    口径来自 `core/edit/changeset.py`：「改回原值时该条目整条消失」——
    所以这条走的是 `is_empty`，不是"猜"出来的。
    """
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    source, dataset = _zip_source(tmp_path, monkeypatch)
    session = _session(dataset)
    calls = _save_instance_spy(monkeypatch)
    changeset = session.changeset
    assert changeset is not None
    original_value = changeset.value("Units", UNIT_ROW, UNIT_COLUMN)

    changeset.set_value("Units", UNIT_ROW, UNIT_COLUMN, original_value + 5)
    changeset.set_value("Units", UNIT_ROW, UNIT_COLUMN, original_value)
    assert changeset.is_empty, "改回原值之后账本应当空了"

    before = _stamp(source)
    assert session.save() == "没有改动，未写盘"
    assert calls == []
    assert _stamp(source) == before


def test_directory_source_also_skips_the_second_save(tmp_path, monkeypatch) -> None:
    """另一条落点分支（目录数据源 -> 临时目录的 BA_Mod.zip）同样不许白干。

    这条走的是 `plan_package + write_package + verify_package`（不是写回打开的文件），
    所以早退要**在挑表之前**发生，两条分支才一起省。
    """
    monkeypatch.setenv(SAVE_DIR_ENV, str(tmp_path))
    dataset = load_dataset(DATASET_ROOT)
    target = tmp_path / PACKAGE_NAME
    session = _session(dataset, save_path=target)
    calls = _save_instance_spy(monkeypatch)
    changeset = session.changeset
    assert changeset is not None

    changeset.set_value("Units", UNIT_ROW, UNIT_COLUMN, 61)
    session.save()
    assert len(calls) == 1
    assert target.is_file()

    before = _stamp(target)
    assert session.save() == "没有改动，未写盘"
    assert len(calls) == 1
    assert _stamp(target) == before


# --------------------------------------------------------------------------- #
# ② 有新改动 -> 照原样写，而且真的落进包里
# --------------------------------------------------------------------------- #


def test_a_new_change_still_lands_in_the_package(tmp_path, monkeypatch) -> None:
    """有新改动时行为**一个字不变**：写盘 + 值读回来是新的。"""
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    source, dataset = _zip_source(tmp_path, monkeypatch)
    session = _session(dataset)
    changeset = session.changeset
    assert changeset is not None

    changeset.set_value("Units", UNIT_ROW, UNIT_COLUMN, 60)
    message = session.save()

    assert str(source) in message, "状态栏那句话要能看出写到哪个文件"
    rows = read_package_rows(source)
    assert len(rows) == 24
    assert next(r[UNIT_COLUMN] for r in rows["Units"] if r["Id"] == UNIT_ROW) == 60


# --------------------------------------------------------------------------- #
# ③ `_tables_to_write()` 的口径不许被这一刀破坏
# --------------------------------------------------------------------------- #


def test_second_save_with_a_new_change_still_carries_the_first_table(tmp_path, monkeypatch) -> None:
    """改第二张表再存：第一次那张表**必须**一起重新序列化，否则静默丢改动。

    未改动的表是从**打开时那份来源**逐字节搬的 —— 只带"本次改的表"去保存，
    第二次就会把第一张表按原样写回去（落点是用户自己的包时，这是数据丢失）。
    """
    monkeypatch.delenv(SAVE_DIR_ENV, raising=False)
    source, dataset = _zip_source(tmp_path, monkeypatch)
    session = _session(dataset)
    calls = _save_instance_spy(monkeypatch)
    instance = session.instance
    assert instance is not None
    changeset = session.changeset
    assert changeset is not None

    changeset.set_value("Units", UNIT_ROW, UNIT_COLUMN, 60)
    session.save()
    assert calls == [("Units",)]

    mobility = instance.table("Mobility")
    row_id = mobility.rows[0][mobility.column_index("Id")]
    column = next(
        name
        for index, name in enumerate(mobility.columns)
        if name != "Id"
        and isinstance(mobility.rows[0][index], int)
        and not isinstance(mobility.rows[0][index], bool)
    )
    old_value = mobility.rows[0][mobility.column_index(column)]
    changeset.set_value("Mobility", row_id, column, old_value + 7)
    session.save()

    # 直接量口径：第二次交给写盘的**是两张表**（本次改的 + 上次写进这个落点的）
    assert calls[1] == ("Mobility", "Units"), f"第二次保存没有把第一张表一起带上：{calls[1]!r}"
    # 再看用户视角：第一笔改动真的还在包里
    rows = read_package_rows(source)
    assert next(r[UNIT_COLUMN] for r in rows["Units"] if r["Id"] == UNIT_ROW) == 60
    mobility_row = next(r for r in rows["Mobility"] if r["Id"] == row_id)
    assert mobility_row[column] == old_value + 7
