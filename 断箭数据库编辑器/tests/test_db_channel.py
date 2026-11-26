"""GUI 数据库通道桥接（`app/shell/db_channel.py`）的测试。

这一层是"能用的产品"与"只有 CLI 能用"的分界，钉住三件事：

1. **桥接出来的 Dataset 全在内存里**（三种文件来源在磁盘上都没有散文件）；
2. **打开本身不改动任何表的字节**（否则"没编辑也变了"）；
3. **"写回哪个文件"只由来源的种类决定**（`opened_file_of`）——
   `BA_Mod.zip` / `DataBaseCompiled.json` 写回它本身，`data.unity3d` 不写回。

⚠️ 从前这里还测"改动清单"（`changed_tables` / `apply_dataset_to_source`）——
那套"按内容判等"的基线随**保存通道收口**删了（GUI 只走 `EditSession` 账本）。
那条实测知识（`Mobility` 的 `1.5E+08` 会被 json 写成 `150000000.0`，所以不能按字节判等）
搬进了 `app/shell/db_channel.py` 的模块 docstring。

全部内存构造，不碰游戏文件。
"""

from __future__ import annotations

from pathlib import Path

from ba_mod_maker.app.shell import db_channel as chan
from ba_mod_maker.core.asset import container, dbzip
from ba_mod_maker.core.asset.tables import TABLES
from ba_mod_maker.core.data.dataset import FORMAT_JSON, FORMAT_ZIP
from ba_mod_maker.core.session import OpenedSource, SourceKind, open_source

#: 一张含**非规范浮点写法**的表（`Mobility` 的真实形状）：
#: 打开 / 桥接都不许改动它的字节（json 往返会把它写成别的写法，见 db_channel 的模块说明）。
_FLOATY = b'[{"Id":1,"LoiteringTime":1.5E+08,"AfterBurningLoiteringTime":1E+08}]'


def _slot(field: str, table: str, entry: str, data: bytes) -> container.TableSlot:
    return container.TableSlot(
        field=field, table=table, zip_entry=entry, data=data, iv=bytes(16), pad=0
    )


def _source_with_floaty() -> OpenedSource:
    """两个表的假来源：Units（普通）+ Mobility（带 1.5E+08）。"""
    names = {t.table: t for t in TABLES}
    slots = [
        _slot(names["Units"].field, "Units", names["Units"].zip_entry, b'[{"Id":1,"Cost":55}]'),
        _slot(
            names["Mobility"].field,
            "Mobility",
            names["Mobility"].zip_entry,
            _FLOATY,
        ),
    ]
    meta = container.ObjectMeta(
        object_head=bytes(48), object_tail=bytes(4), tables=slots, path_id=52085
    )
    return OpenedSource(kind=SourceKind.UNITY3D, path=Path("fake.unity3d"), meta=meta)


def test_dataset_from_source_loads_all_tables_without_disk() -> None:
    """桥接出来的 Dataset 必须**全部在内存里**，不去读磁盘（没有 _schema.json 可读）。"""
    src = _source_with_floaty()
    ds = chan.dataset_from_source(src)
    inst = ds.instance(ds.default_instance_id)
    assert set(ds.schema) == {"Units", "Mobility"}
    assert set(inst._tables) == {"Units", "Mobility"}
    # 惰加载缓存已预填：取表不会抛（磁盘上根本没有那些文件）
    assert inst.table("Units").row_count == 1
    assert inst.table("Mobility").row_count == 1


def test_describe_source_handles_missing_path_id() -> None:
    """BA_Mod.zip 没有 pathID —— 副标题要明说，不能瞎编一个。"""
    src = _source_with_floaty()
    src.meta.path_id = None
    _title, subtitle = chan.describe_source(src)
    assert "无 pathID" in subtitle


def test_fallback_instance_id_is_usable_by_the_dataset_model() -> None:
    """没有 pathID 的来源（BA_Mod.zip）必须给一个**数字**实例 id。

    踩过的坑：原先占位成 "dbzip"，而 `Dataset.latest_instance_id` 会做
    `max(instance_ids, key=int)` —— 直接 `ValueError: invalid literal for int()`，
    表现是"加载 BA_Mod.zip 时界面构建到一半崩掉"。

    这条守住"占位值必须是模型能吃的"。
    """
    src = _source_with_floaty()
    src.meta.path_id = None
    ds = chan.dataset_from_source(src)
    pid = ds.default_instance_id  # 会走 latest_instance_id -> max(..., key=int)
    assert pid.isdigit(), f"实例 id 必须是数字，收到 {pid!r}"
    # 而且真的能用
    assert ds.instance(pid).table("Units").row_count == 1


def test_dataset_from_source_does_not_re_serialize_on_open() -> None:
    """打开本身不该改动任何表的字节（否则"没编辑也变了"）。"""
    src = _source_with_floaty()
    before = [slot.data for slot in src.meta.tables]
    chan.dataset_from_source(src)
    assert [slot.data for slot in src.meta.tables] == before


# --------------------------------------------------------------------------- #
# 「保存写回你打开的那个文件」：这一层决定哪种来源有写回文件
# --------------------------------------------------------------------------- #


def _all_tables_source(kind: SourceKind, path: Path | None = None) -> OpenedSource:
    """24 张表都有的假来源（每张一行）——「打开的是哪一种文件」由 `kind` 决定。"""
    slots = []
    for naming in TABLES:
        data = b'[{"Id":1,"Cost":55}]' if naming.table == "Units" else b'[{"Id":1}]'
        slots.append(_slot(naming.field, naming.table, naming.zip_entry, data))
    meta = container.ObjectMeta(
        object_head=bytes(48), object_tail=bytes(4), tables=slots, path_id=52085
    )
    return OpenedSource(kind=kind, path=path or Path("fake"), meta=meta)


def test_unity3d_source_has_no_write_back_file() -> None:
    """容器来源**不**写回那个文件：重写一次 7.1 GB / 约 4 分钟，不能挂在自动保存上。

    （落点仍是同级目录的 `BA_Mod.zip` —— 那由 edit_session 算，见 test_edit_package。）
    """
    src = _all_tables_source(SourceKind.UNITY3D)
    assert chan.opened_file_of(src) is None
    ds = chan.dataset_from_source(src)
    assert ds.instance(ds.default_instance_id).opened_file is None


def test_zip_source_remembers_the_file_and_its_unknown_entries(tmp_path) -> None:
    """打开一个包：记住"写回它本身" + 包里那些**我们不认识的条目**（原样带回去）。"""
    first = _all_tables_source(SourceKind.DB_ZIP)
    path = tmp_path / "我的包.zip"
    dbzip.DbZip(
        tables={slot.table: slot.data for slot in first.meta.tables},
        extra={"Someone.json": b'{"a":1}'},
    ).write(path)

    src = open_source(path)  # 真实打开链路（按内容认，不看文件名）
    opened = chan.opened_file_of(src)
    assert opened is not None
    assert opened.path == path
    assert opened.format == FORMAT_ZIP
    assert opened.extra == {"Someone.json": b'{"a":1}'}
    assert opened.meta is None, "zip 来源写回包里没有容器对象"

    # 装进界面模型的那一份也带着它（写盘通道就是从这里拿落点与未知条目）
    ds = chan.dataset_from_source(src)
    inst = ds.instance(ds.default_instance_id)
    assert inst.opened_file is not None
    assert inst.opened_file.path == path
    assert inst.opened_file.format == FORMAT_ZIP
    assert inst.opened_file.extra == {"Someone.json": b'{"a":1}'}


def test_json_source_remembers_the_container_object(tmp_path) -> None:
    """json 来源：写回要用**打开时那个容器对象**（头/尾 + 每张表的 IV）。"""
    src = _all_tables_source(SourceKind.DBC_JSON, tmp_path / "DataBaseCompiled.json")
    opened = chan.opened_file_of(src)
    assert opened is not None
    assert opened.path == src.path
    assert opened.format == FORMAT_JSON
    assert opened.meta is src.meta, "写回 json 就靠它序列化"
    assert opened.extra == {}, "json 没有“额外条目”这一说"

    ds = chan.dataset_from_source(src)
    inst = ds.instance(ds.default_instance_id)
    assert inst.opened_file is not None
    assert inst.opened_file.path == src.path
    assert inst.opened_file.format == FORMAT_JSON
    assert inst.opened_file.meta is src.meta, "写回 json 就靠它序列化"
