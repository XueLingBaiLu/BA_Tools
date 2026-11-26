"""`core/asset/` 的容器层测试。

分两档：

- **不需要游戏**的：用内存里构造的字节测加密原语、对象序列化、BA_Mod.zip 打包。
  这些是日常回归的主力。
- **需要游戏**的：标 `slow`，直接读 `data.unity3d`。
"""

from __future__ import annotations

import json
import struct
import zipfile

import pytest

from ba_mod_maker.core.asset import container, dbcryp, dbzip, tables
from ba_mod_maker.core.errors import DataSourceError

# --------------------------------------------------------------------------- #
# 表名清单
# --------------------------------------------------------------------------- #


def test_table_naming_is_internally_consistent() -> None:
    assert len(tables.TABLES) == 24
    assert len(tables.FIELD_NAMES) == len(set(tables.FIELD_NAMES)) == 24
    assert len(tables.TABLE_NAMES) == len(set(tables.TABLE_NAMES)) == 24
    assert len(tables.ENTRY_NAMES) == len(set(tables.ENTRY_NAMES)) == 24


def test_units_is_the_only_field_without_json_suffix() -> None:
    """实测：24 个字段里只有 `Units` 不带 `Json` 后缀。"""
    without = [t.field for t in tables.TABLES if not t.field.endswith("Json")]
    assert without == ["Units"]


def test_fly_presets_entry_name_has_the_plural_override() -> None:
    """**这条是防崩溃的**：写成 `FlyPresets.json` 会被游戏**静默忽略**。

    磁盘文件名 / 我们自己的条目名是 `PlaneFlyPresets.json`（文件干那一套）。
    """
    assert tables.BY_TABLE["FlyPresets"].zip_entry == "PlaneFlyPresets.json"
    assert tables.BY_TABLE["Units"].zip_entry == "Units.json"


def test_fly_presets_also_needs_the_singular_alias_entry() -> None:
    """❗ 飞行预设那张**必须再写一份单数** `PlaneFlyPreset.json`（内容相同）。

    2026-10-02 实机翻案：游戏取行时按**模型类型名** `typeof(T).Name` 查字典
    （ISIL `GetTableRows<T>` 5939-5962），而这个类型叫 `Table<PlaneFlyPreset>`（无 s）；
    只写复数那一份会**在 `ReadTablesFromZip` 里被 `continue` 掉** ⇒ 那张表空 ⇒
    `LoadUnits.LoadMobility` NRE ⇒ 整个 DataBaseService 起不来 ⇒ 军械库空白。
    工坊里那份已知可用的 db.zip 就是两个名字都写。
    """
    assert tables.BY_TABLE["FlyPresets"].alias_entries == ("PlaneFlyPreset.json",)
    assert tables.BY_TABLE["Units"].alias_entries == ()
    assert tables.ALIAS_ENTRY_NAMES == ("PlaneFlyPreset.json",)
    names = tables.package_entry_names()
    assert len(names) == 25 and "PlaneFlyPreset.json" in names


def test_table_of_ignores_unknown_entries() -> None:
    assert tables.table_of("Units.json") == "Units"
    assert tables.table_of("PlaneFlyPresets.json") == "FlyPresets"
    # 单数版**是我们认得的别名**（游戏也正是按这个名字查表），不再是"未知条目"
    assert tables.table_of("PlaneFlyPreset.json") == "FlyPresets"
    assert tables.table_of("NotATable.json") is None


# --------------------------------------------------------------------------- #
# 加密原语
# --------------------------------------------------------------------------- #


def test_pad_unpad_roundtrip_covers_every_remainder() -> None:
    for n in range(0, 64):
        raw = bytes(n)
        padded = dbcryp.pad(raw)
        assert len(padded) % 16 == 0
        assert len(padded) > len(raw) or (n and len(padded) == len(raw) + 16)
        assert dbcryp.unpad(padded) == raw


def test_pad_uses_a_full_block_when_already_aligned() -> None:
    """明文恰好整除 16 时补**整块** 16 —— 实测 `Mobility`(52085) 的 pad 就是 1。

    注：pad=16 出现在 52082 的 Units 上；52085 的 Mobility 是 pad=1。
    两者都在 1..16 内。
    """
    assert dbcryp.pad(b"x" * 16) == b"x" * 16 + bytes([16]) * 16


def test_unpad_rejects_bad_padding() -> None:
    with pytest.raises(DataSourceError):
        dbcryp.unpad(b"not a multiple of 16!")
    with pytest.raises(DataSourceError):
        dbcryp.unpad(b"x" * 15 + bytes([17]))


def test_encrypt_decrypt_is_byte_exact_with_reused_iv() -> None:
    """复用 IV 是**逐字节可逆**的前提。"""
    plain = json.dumps([{"Id": 1, "Name": "BTR-82"}], separators=(",", ":")).encode()
    field = dbcryp.encrypt(plain)
    iv = field[8:24]
    again = dbcryp.encrypt(plain, iv=iv)
    assert again == field
    back, got_iv = dbcryp.decrypt(field)
    assert back == plain
    assert got_iv == iv


def test_decrypt_rejects_wrong_magic() -> None:
    field = bytearray(dbcryp.encrypt(b"hello"))
    field[0] = ord("X")
    with pytest.raises(DataSourceError) as excinfo:
        dbcryp.decrypt(bytes(field))
    assert "magic" in str(excinfo.value)


def test_decrypt_rejects_too_short() -> None:
    with pytest.raises(DataSourceError):
        dbcryp.decrypt(b"short")


# --------------------------------------------------------------------------- #
# 容器对象
# --------------------------------------------------------------------------- #


def _fake_object(plain_by_table: dict[str, bytes], *, path_id: int = 999) -> bytes:
    """造一个形状合法的容器对象（头 48 B + N 个字段 + 尾 4 B）。"""
    name = b"DataBaseCompiled"
    out = bytearray()
    out += struct.pack("<qq", 0, 0)
    out += struct.pack("<i", 1)
    out += struct.pack("<q", 13249)
    out += struct.pack("<i", len(name))
    out += name
    assert len(out) == 48
    for plain in plain_by_table.values():
        b64 = dbcryp.encode_b64(dbcryp.encrypt(plain))
        out += struct.pack("<i", len(b64)) + b64
        out += bytes((-len(out)) % 4)
    assert len(out) > 0
    out += struct.pack("<i", 2)
    return bytes(out)


def _names(slots: int) -> dict[str, str]:
    real = [t for t in tables.TABLES]
    return {real[i].field: real[i].table for i in range(slots)}


def test_parse_then_serialize_is_byte_exact() -> None:
    plain = [json.dumps([{"Id": 1}], separators=(",", ":")).encode() for _ in range(3)]
    blob = _fake_object(dict(zip(_names(3), plain, strict=True)))
    meta = container.ObjectMeta.parse(blob, path_id=999)
    assert len(meta.tables) == 3
    assert meta.object_head == blob[:48]
    assert meta.object_tail == struct.pack("<i", 2)
    assert bytes(meta.to_bytes()) == blob


def test_json_roundtrip_is_byte_exact() -> None:
    """`DataBaseCompiled.json` 往返必须逐字节相同。

    这也是**为什么未编辑的表要按原样文本存**：实测 `Mobility` 里有
    `1.5E+08`，Python 的 `json` 会规范化成 `150000000.0`（长度 +10）。
    """
    plain = [json.dumps([{"Id": 1}], separators=(",", ":")).encode() for _ in range(3)]
    blob = _fake_object(dict(zip(_names(3), plain, strict=True)))
    meta = container.ObjectMeta.parse(blob, path_id=999)
    for slot, (field, table) in zip(meta.tables, _names(3).items(), strict=True):
        slot.field, slot.table = field, table
        slot.zip_entry = tables.BY_TABLE[table].zip_entry
    text = meta.to_json_text()
    back = container.ObjectMeta.from_json_text(text)
    assert bytes(back.to_bytes()) == blob


def test_edited_table_gets_new_iv_and_still_roundtrips() -> None:
    plain = [json.dumps([{"Id": 1}], separators=(",", ":")).encode() for _ in range(2)]
    blob = _fake_object(dict(zip(_names(2), plain, strict=True)))
    meta = container.ObjectMeta.parse(blob, path_id=999)
    old_iv = meta.tables[0].iv
    meta.tables[0].set_payload([{"Id": 1}, {"Id": 2}])
    assert meta.tables[0].dirty is True
    rebuilt = bytes(meta.to_bytes())
    assert rebuilt != blob  # 内容变了，长度也变了
    again = container.ObjectMeta.parse(rebuilt, path_id=999)
    assert again.tables[0].json_payload() == [{"Id": 1}, {"Id": 2}]
    assert again.tables[1].data == plain[1]  # 另一张表原样
    assert meta.tables[0].iv == old_iv  # set_payload 不动 IV；换 IV 是 dbzip 的职责


def test_parse_rejects_object_without_the_name() -> None:
    with pytest.raises(DataSourceError):
        container.ObjectMeta.parse(b"\x00" * 4096)


def test_from_json_text_rejects_foreign_format() -> None:
    with pytest.raises(DataSourceError):
        container.ObjectMeta.from_json_text('{"format": "something-else", "tables": []}')


def test_from_json_text_rejects_wrong_version() -> None:
    doc = {"format": container.FORMAT_ID, "format_version": 99, "tables": [{}]}
    with pytest.raises(DataSourceError) as excinfo:
        container.ObjectMeta.from_json_text(json.dumps(doc))
    assert "版本" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# BA_Mod.zip
# --------------------------------------------------------------------------- #


def test_dbzip_write_then_read(tmp_path) -> None:
    z = dbzip.DbZip(tables={"Units": b'[{"Id":1}]', "Weapons": b"[]"})
    path = z.write(tmp_path / "BA_Mod.zip")
    back = dbzip.DbZip.read(path)
    assert back.tables == z.tables
    assert back.extra == {}


def test_dbzip_keeps_unknown_entries(tmp_path) -> None:
    """用户拍板：未知条目**原样保留**，不清理别人的东西。"""
    path = tmp_path / "BA_Mod.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Units.json", b'[{"Id":1}]')
        zf.writestr("PlaneFlyPresets.json", b"[]")
        zf.writestr("PlaneFlyPreset.json", b"[]")  # 别名条目：**我们认得**（不是别人的东西）
        zf.writestr("Someones_extra.json", b"{}")
        zf.writestr("readme.txt", b"hi")
    z = dbzip.DbZip.read(path)
    assert set(z.tables) == {"Units", "FlyPresets"}
    assert set(z.extra) == {"Someones_extra.json", "readme.txt"}
    out = z.write(tmp_path / "out.zip")
    back = dbzip.DbZip.read(out)
    assert back.tables == z.tables
    assert back.extra == z.extra
    # 写出去的包里两个名字都在（单数那份是游戏真正会读的，见 tables.ZIP_ENTRY_ALIASES）
    with zipfile.ZipFile(out) as zf:
        assert {"PlaneFlyPresets.json", "PlaneFlyPreset.json"} <= set(zf.namelist())


def test_dbzip_rejects_zip_with_no_known_table(tmp_path) -> None:
    path = tmp_path / "BA_Mod.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("NotATable.json", b"[]")
    with pytest.raises(DataSourceError):
        dbzip.DbZip.read(path)


def test_dbzip_read_rejects_non_zip(tmp_path) -> None:
    path = tmp_path / "BA_Mod.zip"
    path.write_bytes(b"this is not a zip")
    with pytest.raises(DataSourceError):
        dbzip.DbZip.read(path)


def test_dbzip_to_object_requires_all_tables_by_default() -> None:
    plain = [json.dumps([{"Id": 1}], separators=(",", ":")).encode() for _ in range(3)]
    blob = _fake_object(dict(zip(_names(3), plain, strict=True)))
    meta = container.ObjectMeta.parse(blob, path_id=999)
    for slot, (field, table) in zip(meta.tables, _names(3).items(), strict=True):
        slot.field, slot.table, slot.zip_entry = field, table, tables.BY_TABLE[table].zip_entry
    partial = dbzip.DbZip(tables={meta.tables[0].table: plain[0]})
    with pytest.raises(DataSourceError) as excinfo:
        partial.to_object(meta)
    assert "缺" in str(excinfo.value)
    loose = partial.to_object(meta, strict=False)
    assert len(loose.tables) == 1


def test_table_name_aliases_cover_the_one_rename() -> None:
    """表名别名：`FlyPresets` <-> `PlaneFlyPresets` 两个方向都要认。

    实踩（用户报"战机侧的东西都显示不出来"）：他打开的数据源按**文件名**建表，
    那张表叫 `PlaneFlyPresets`；而 viewmodel 里写的是规范名，一查就 KeyError。
    固定翼的【前进速度】【回转半径】都挂在这一行上 —— 整张卡就没了。
    """
    from ba_mod_maker.core.asset.tables import TABLE_ALIASES, resolve_table_name

    assert TABLE_ALIASES["FlyPresets"] == ("PlaneFlyPresets",)
    assert TABLE_ALIASES["PlaneFlyPresets"] == ("FlyPresets",)
    assert resolve_table_name("FlyPresets", {"FlyPresets"}) == "FlyPresets"
    assert resolve_table_name("FlyPresets", {"PlaneFlyPresets"}) == "PlaneFlyPresets"
    assert resolve_table_name("PlaneFlyPresets", {"FlyPresets"}) == "FlyPresets"
    assert resolve_table_name("Units", {"Units"}) == "Units"
    assert resolve_table_name("没有这张表", {"Units"}) is None


def test_dbzip_entry_names_are_the_24_real_ones() -> None:
    names = dbzip.entry_names()
    assert len(names) == 24
    assert "PlaneFlyPresets.json" in names
    assert "FlyPresets.json" not in names
