"""四个文件通道动作的测试（打开 / 保存 / 提取 / 导入）。

全部用临时目录里的文件，**不碰游戏目录**。只有最后一条标了 slow（要真读 6.6 GB 容器）。
"""

from __future__ import annotations

import json
import types
import zipfile
from pathlib import Path

import pytest

from ba_mod_maker.core import session
from ba_mod_maker.core.asset import container, dbzip, tables
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.errors import DataSourceError, UserFacingError

# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class _FakeCtx:
    """假的 UfsReader：只支持 with 协议，解析结果由 find_database_objects 那侧给出。"""

    def __init__(self, _path) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeReader:
    """只提供三道闸会碰到的那两样：路径与 close。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# 造测试数据
# --------------------------------------------------------------------------- #


def _tiny_meta(path_id: int | None = 52085) -> container.ObjectMeta:
    """造一个 24 张表都有的最小容器对象（每张表一行）。"""
    slots = []
    for naming in tables.TABLES:
        plain = json.dumps([{"Id": 1}], separators=(",", ":")).encode()
        slots.append(
            container.TableSlot(
                field=naming.field,
                table=naming.table,
                zip_entry=naming.zip_entry,
                data=plain,
                iv=bytes(range(16)),
                pad=0,
            )
        )
    name = b"DataBaseCompiled"
    head = (
        bytes(16)
        + bytes(4)
        + (13249).to_bytes(8, "little")
        + len(name).to_bytes(4, "little")
        + name
    )
    assert len(head) == 48
    return container.ObjectMeta(
        object_head=head, object_tail=bytes(4), tables=slots, path_id=path_id
    )


def _write_dbc_json(path: Path, meta: container.ObjectMeta) -> Path:
    path.write_text(meta.to_json_text(), encoding="utf-8")
    return path


def _write_dbzip(path: Path, meta: container.ObjectMeta) -> Path:
    dbzip.DbZip.from_object(meta).write(path)
    return path


# --------------------------------------------------------------------------- #
# 识别三种文件
# --------------------------------------------------------------------------- #


def test_detect_kind_by_content_not_extension(tmp_path) -> None:
    meta = _tiny_meta()
    j = _write_dbc_json(tmp_path / "whatever.bin", meta)
    z = _write_dbzip(tmp_path / "other.dat", meta)
    assert session.detect_kind(j) is session.SourceKind.DBC_JSON
    assert session.detect_kind(z) is session.SourceKind.DB_ZIP


def test_both_names_are_accepted(tmp_path) -> None:
    """改名前后的**两种名字都要认** —— 用户手上可能还留着老的 db.zip。

    判据是**内容**（PK 头），不是文件名：同一份内容叫哪个名字都得落到 DB_ZIP。
    成品名只有一处口径：~session.PACKAGE_NAME~。
    """
    meta = _tiny_meta()
    assert session.PACKAGE_NAME.endswith(".zip")
    for name in (session.PACKAGE_NAME, "db.zip"):
        p = _write_dbzip(tmp_path / name, meta)
        assert session.detect_kind(p) is session.SourceKind.DB_ZIP


def test_detect_kind_rejects_garbage(tmp_path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"not any of the three")
    with pytest.raises(DataSourceError):
        session.detect_kind(p)


def test_detect_kind_reports_missing_file(tmp_path) -> None:
    with pytest.raises(DataSourceError) as excinfo:
        session.detect_kind(tmp_path / "nope.zip")
    assert "找不到" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 打开 / 保存
# --------------------------------------------------------------------------- #


def test_open_and_save_dbc_json(tmp_path) -> None:
    meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", meta)
    with session.open_source(p) as src:
        assert src.kind is session.SourceKind.DBC_JSON
        assert len(src.meta.tables) == 24
        assert src.meta.path_id == 52085
        src.table("Units").set_payload([{"Id": 1}, {"Id": 2}])
        msg = session.save(src)
    assert "已写回" in msg
    with session.open_source(p) as again:
        assert len(again.table("Units").json_payload()) == 2


def test_open_and_save_dbzip_keeps_unknown_entries(tmp_path) -> None:
    """用户拍板：未知条目**原样保留**。"""
    p = tmp_path / "db.zip"
    with zipfile.ZipFile(p, "w") as zf:
        for naming in tables.TABLES:
            zf.writestr(naming.zip_entry, b'[{"Id":1}]')
        zf.writestr("SomeonesExtra.json", b'{"hello":1}')
        zf.writestr("readme.txt", b"hi")
    with session.open_source(p) as src:
        assert set(src.extra_entries or {}) == {"SomeonesExtra.json", "readme.txt"}
        src.table("Units").set_payload([{"Id": 9}])
        session.save(src)
    with session.open_source(p) as again:
        assert again.table("Units").json_payload() == [{"Id": 9}]
        assert set(again.extra_entries or {}) == {"SomeonesExtra.json", "readme.txt"}


def test_open_dbzip_missing_table_is_loud(tmp_path) -> None:
    """缺表的 BA_Mod.zip 是残废的，必须**报错**而不是凑合。"""
    p = tmp_path / "db.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("Units.json", b"[]")
    with pytest.raises(DataSourceError) as excinfo:
        session.open_source(p)
    assert "没有" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 提取 / 导入
# --------------------------------------------------------------------------- #


def test_extract_db_zip_from_json_channel(tmp_path) -> None:
    meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", meta)
    out = tmp_path / "out.zip"
    with session.open_source(p) as src:
        session.extract_db_zip(src, out)
    z = dbzip.DbZip.read(out)
    assert len(z.tables) == 24
    names = set(zipfile.ZipFile(out).namelist())
    assert "PlaneFlyPresets.json" in names
    assert "FlyPresets.json" not in names


def test_merge_into_template_uses_template_shape() -> None:
    """导入时**形状用目标容器的**，只取数据。"""
    template = _tiny_meta(path_id=52085)
    incoming = _tiny_meta(path_id=99999)
    incoming.tables[0].set_payload([{"Id": 42}])
    merged = session.merge_into_template(template, incoming)
    assert merged.path_id == 52085
    assert merged.object_head == template.object_head
    assert merged.tables[0].json_payload() == [{"Id": 42}]
    assert merged.tables[0].dirty is True
    assert merged.tables[1].iv == template.tables[1].iv


def test_merge_into_template_keeps_tables_absent_from_incoming() -> None:
    template = _tiny_meta()
    partial = _tiny_meta()
    partial.tables = partial.tables[:1]
    merged = session.merge_into_template(template, partial)
    assert len(merged.tables) == 24
    assert merged.tables[5].data == template.tables[5].data


# --------------------------------------------------------------------------- #
# 三道闸
# --------------------------------------------------------------------------- #


def test_save_refuses_when_source_changed_underneath(tmp_path) -> None:
    """闸 1：写前核对 sha256 —— 文件被改过就拒绝写。"""
    meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", meta)
    src = session.OpenedSource(
        kind=session.SourceKind.UNITY3D,
        path=p,
        meta=meta,
        reader=_FakeReader(p),
        file_sha256="deadbeef",
    )
    with pytest.raises(UserFacingError) as excinfo:
        session.save(src, check_game_running=False)
    assert "sha256" in str(excinfo.value)
    src.close()


def test_save_refuses_when_game_running(tmp_path, monkeypatch) -> None:
    """闸 2：游戏在跑就拒绝写（实测占着文件时覆盖必失败）。"""
    meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", meta)
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: True, raising=True)
    src = session.OpenedSource(
        kind=session.SourceKind.UNITY3D,
        path=p,
        meta=meta,
        reader=_FakeReader(p),
    )
    with pytest.raises(UserFacingError) as excinfo:
        session.save(src)
    assert "游戏正在运行" in str(excinfo.value)
    src.close()


def test_save_says_nothing_to_do_when_unchanged(tmp_path, monkeypatch) -> None:
    """没改动就不该写盘（容器写一次 4 分钟，不能白跑）。"""
    meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", meta)
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    blob = bytes(meta.to_bytes())
    src = session.OpenedSource(
        kind=session.SourceKind.UNITY3D,
        path=p,
        meta=meta,
        reader=_FakeReader(p),
    )
    monkeypatch.setattr(
        "ba_mod_maker.core.asset.ufs.find_database_objects",
        lambda _r: [(52085, blob)],
    )
    msg = session.save(src, check_sha256=False)
    assert "没有改动" in msg
    src.close()


# --------------------------------------------------------------------------- #
# 真容器（慢）
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_real_container_open_and_extract(tmp_path) -> None:
    """真读游戏容器：打开 + 导出 BA_Mod.zip。**只读，不写游戏目录。**"""
    from ba_mod_maker.core.assets import game as game_mod

    root = game_mod.find_game_root_for_db()
    if root is None:
        pytest.skip("这台机器上没有游戏")
    with session.open_source(game_mod.data_file(root)) as src:
        assert src.kind is session.SourceKind.UNITY3D
        assert len(src.meta.tables) == 24
        assert src.meta.path_id is not None
        units = src.table("Units")
        rows = units.json_payload()
        assert len(rows) > 100
        out = tmp_path / "BA_Mod.zip"
        session.extract_db_zip(src, out)
        z = dbzip.DbZip.read(out)
        assert len(z.tables) == 24
        assert z.tables["Units"] == units.data


# --------------------------------------------------------------------------- #
# 闸 3 的回归测试（这个坑踩过一次，必须钉死）
# --------------------------------------------------------------------------- #


def test_gate3_is_not_tautological(tmp_path, monkeypatch) -> None:
    """闸 3 的基准必须是**改前的字节**，不能是 src.meta。

    踩过的坑：`_verify_rewritten` 原先把 `src.meta`（**已经被改过**）当基准去比新文件，
    于是"目标 == 源"恒成立、判据恒真 —— 表现为"写后复核：新文件里没有任何改动"，
    而实际上新文件是对的。等于没有校验。

    这条测试用"重写其实什么都没改"来验证判据真的会响。
    """
    src_meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", src_meta)
    old_blob = bytes(src_meta.to_bytes())

    # 编辑：Units 从 [{Id:1}] 变成 [{Id:1},{Id:2}]
    src_meta.tables[0].set_payload([{"Id": 1}, {"Id": 2}])

    # 伪造成"重写根本没生效"：临时件里放的还是改前的对象
    tmp = tmp_path / "fake.unity3d"
    fake = container.ObjectMeta.parse(old_blob)
    session._label_slots(fake, session._NAMES)
    tmp.write_bytes(b"placeholder")  # 内容不重要：下面 monkeypatch 掉解析

    def _fake_find(_reader):
        return [(52085, old_blob)]

    monkeypatch.setattr("ba_mod_maker.core.asset.ufs.find_database_objects", _fake_find)
    monkeypatch.setattr("ba_mod_maker.core.asset.ufs.UfsReader", _FakeCtx)

    src = session.OpenedSource(kind=session.SourceKind.UNITY3D, path=p, meta=src_meta)
    with pytest.raises(UserFacingError) as excinfo:
        session._verify_rewritten(tmp, src, old_blob)
    assert "改动没进新文件" in str(excinfo.value) or "逐字节相同" in str(excinfo.value)
    src.close()


def test_gate3_passes_on_a_correct_rewrite(tmp_path, monkeypatch) -> None:
    """反向：重写正确时不能误报。"""
    src_meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", src_meta)
    old_blob = bytes(src_meta.to_bytes())
    src_meta.tables[0].set_payload([{"Id": 1}, {"Id": 2}])
    new_blob = bytes(src_meta.to_bytes())

    tmp = tmp_path / "ok.unity3d"
    tmp.write_bytes(b"placeholder")
    monkeypatch.setattr(
        "ba_mod_maker.core.asset.ufs.find_database_objects", lambda _r: [(52085, new_blob)]
    )
    monkeypatch.setattr("ba_mod_maker.core.asset.ufs.UfsReader", _FakeCtx)

    src = session.OpenedSource(kind=session.SourceKind.UNITY3D, path=p, meta=src_meta)
    session._verify_rewritten(tmp, src, old_blob)  # 不该抛
    src.close()


# --------------------------------------------------------------------------- #
# 失败时不留垃圾：中间文件是**满尺寸**的，必须清掉
# --------------------------------------------------------------------------- #

_PLACEHOLDER = b"placeholder"


class _FakeNode:
    """假的节点表条目：_save_unity3d 只用到 offset。"""

    offset = 0


class _FakeInfo:
    """假的对象表解析结果：只用到 data_offset 与 objects。"""

    data_offset = 0
    objects = [(0, 52085, 0)]


class _FakeReport:
    """假的 WriteReport：只用来拼那句人话摘要。"""

    old_size = 1
    new_size = 2
    seconds = 0.0


def _fake_container_source(tmp_path, monkeypatch):
    """搭一个「像容器」的最小环境，让 _save_unity3d 一路跑到重写那一步。

    重写被换成「往目标路径写一个小文件」—— 真容器那一步会写出 7.1 GB，
    测试只关心**它在失败后还在不在**，不关心多大。
    """
    meta = _tiny_meta()
    container_path = tmp_path / "data.unity3d"
    container_path.write_bytes(_PLACEHOLDER)
    old_blob = bytes(meta.to_bytes())
    meta.tables[0].set_payload([{"Id": 1}, {"Id": 2}])  # 有改动，避免走「没有改动」早退
    new_blob = bytes(meta.to_bytes())
    assert new_blob != old_blob

    reader = _FakeReader(container_path)
    reader.node = lambda _name: _FakeNode()
    monkeypatch.setattr(
        "ba_mod_maker.core.asset.ufs.find_database_objects", lambda _r: [(52085, old_blob)]
    )
    monkeypatch.setattr(
        "ba_mod_maker.core.asset.ufs.read_serialized_file", lambda _r, _n: _FakeInfo()
    )
    written: list[Path] = []

    def _fake_rewrite(_reader, _repls, target, **_kwargs):
        Path(target).write_bytes(new_blob)
        written.append(Path(target))
        return _FakeReport()

    monkeypatch.setattr("ba_mod_maker.core.asset.ufs.rewrite_container", _fake_rewrite)
    return meta, container_path, reader, written


def test_failed_verify_removes_the_giant_tmp_file(tmp_path, monkeypatch) -> None:
    """闸 3 不通过时，满尺寸的中间文件必须被删掉。

    为什么必须钉死：那个 `.new` 是**满尺寸**的（真容器约 7.1 GB）。留在游戏目录里
    既撑爆磁盘，又长得像备份 —— 被误当备份用就是灾难。
    """
    meta, container_path, reader, written = _fake_container_source(tmp_path, monkeypatch)

    def _boom(*_args, **_kwargs):
        raise UserFacingError("写后复核：模拟不通过")

    monkeypatch.setattr(session, "_verify_rewritten", _boom)
    tmp = container_path.with_suffix(container_path.suffix + ".new")

    src = session.OpenedSource(
        kind=session.SourceKind.UNITY3D, path=container_path, meta=meta, reader=reader
    )
    with pytest.raises(UserFacingError):
        session.save(src, check_sha256=False, check_game_running=False)

    assert written, "重写没被调用，这条测试没覆盖到目标路径"
    assert not tmp.exists(), "失败后把满尺寸中间文件留在原地了"
    assert container_path.read_bytes() == _PLACEHOLDER, "原文件不该被动"
    src.close()


def test_replace_failure_is_human_and_also_cleans_up(tmp_path, monkeypatch) -> None:
    """替换被拒（WinError 5 那一类）时：翻成人话，并同样清掉中间文件。"""
    meta, container_path, reader, _written = _fake_container_source(tmp_path, monkeypatch)
    monkeypatch.setattr(session, "_verify_rewritten", lambda *_a, **_k: None)

    def _refuse(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    # 只换掉 session 眼里的 os.replace，不动全局 os（别的用例还要用）。
    monkeypatch.setattr(session, "os", types.SimpleNamespace(replace=_refuse))
    tmp = container_path.with_suffix(container_path.suffix + ".new")

    src = session.OpenedSource(
        kind=session.SourceKind.UNITY3D, path=container_path, meta=meta, reader=reader
    )
    with pytest.raises(UserFacingError) as excinfo:
        session.save(src, check_sha256=False, check_game_running=False)

    assert "没法替换" in str(excinfo.value)
    assert "重新打开" in str(excinfo.value.hint or "")
    assert not tmp.exists(), "替换失败后同样不能留下中间文件"
    assert container_path.read_bytes() == _PLACEHOLDER


# --------------------------------------------------------------------------- #
# 写回**原文件**：同一个路径、未知条目原样、不留任何 .bak / 副本（用户口径）
# --------------------------------------------------------------------------- #


def test_save_dbzip_writes_back_to_the_same_path_without_copies(tmp_path) -> None:
    """外部库那条路：落点是**打开的那个包本身**，未知条目逐字节不变，不留副本。"""
    p = tmp_path / "BA_Mod.zip"
    extras = {"SomeonesExtra.json": b'{"hello":1}', "readme.txt": b"hi"}
    with zipfile.ZipFile(p, "w") as zf:
        for naming in tables.TABLES:
            zf.writestr(naming.zip_entry, b'[{"Id":1}]')
        for name, blob in extras.items():
            zf.writestr(name, blob)

    with session.open_source(p) as src:
        src.table("Units").set_payload([{"Id": 9}])
        message = session.save(src)

    # 那句话里是**完整路径**（用户要能看出写到哪个文件了）
    assert str(p) in message
    # 目录里只有那一个包：没有 .bak、没有 .tmp、没有副本
    assert [x.name for x in tmp_path.iterdir()] == ["BA_Mod.zip"]
    with zipfile.ZipFile(p) as zf:
        for name, blob in extras.items():
            assert zf.read(name) == blob, f"未知条目 {name} 变了"
    with session.open_source(p) as again:
        assert again.table("Units").json_payload() == [{"Id": 9}]


def test_save_dbc_json_writes_back_to_the_same_path_without_copies(tmp_path) -> None:
    """`DataBaseCompiled.json` 那条路：同样写回原文件本身，不留副本。"""
    meta = _tiny_meta()
    p = _write_dbc_json(tmp_path / "DataBaseCompiled.json", meta)

    with session.open_source(p) as src:
        src.table("Units").set_payload([{"Id": 1}, {"Id": 2}])
        message = session.save(src)

    assert str(p) in message
    assert [x.name for x in tmp_path.iterdir()] == ["DataBaseCompiled.json"]
    with session.open_source(p) as again:
        assert len(again.table("Units").json_payload()) == 2


def test_save_instance_without_a_target_is_loud() -> None:
    """既不是从文件打开的、又没说存到哪：**报错**，不许随便挑个地方写。"""
    instance = Instance(path_id="52085", directory=Path("x"), schema={}, manifest={})
    with pytest.raises(UserFacingError) as excinfo:
        session.save_instance(instance, ("Units",))
    assert "没有落点" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 闸 1 的哈希是**惰性**的（打开 data.unity3d 不再白算 6.6 GB 的 sha256）
#
# 实测：整个文件算一遍 sha256 要 5.3 秒，而多数打开（看一眼 / preview_import /
# CLI info|tables）根本不会写盘 —— 那笔钱是白付的。所以改成"第一次要走闸时才算"，
# 结果缓存进 OpenedSource。
# --------------------------------------------------------------------------- #


def _open_a_fake_container(meta: container.ObjectMeta, monkeypatch) -> None:
    """让 `open_source` 以为 path 是个 UnityFS 容器（不真读那个文件）。"""
    monkeypatch.setattr(session.ufs, "UfsReader", lambda p: _FakeReader(Path(p)))
    monkeypatch.setattr(
        session.ufs,
        "find_database_objects",
        lambda _reader: [(52085, bytes(meta.to_bytes()))],
    )


def _count_sha256_calls(monkeypatch) -> list[Path]:
    """把 `_file_sha256` 包一层，记下每次算的是哪个文件。"""
    calls: list[Path] = []
    real = session._file_sha256

    def _spy(path):
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(session, "_file_sha256", _spy)
    return calls


def test_unity3d_open_does_not_hash_the_whole_file(tmp_path, monkeypatch) -> None:
    """打开时**不算**整文件 sha256；第一次要走闸（= 保存）才算，且只算一遍。"""
    meta = _tiny_meta()
    p = tmp_path / "data.unity3d"
    p.write_bytes(b"UnityFS-fake")
    _open_a_fake_container(meta, monkeypatch)
    calls = _count_sha256_calls(monkeypatch)

    with session.open_source(p) as src:
        assert src.kind is session.SourceKind.UNITY3D
        assert calls == [], "打开时就算了一遍整文件 sha256（6.6 GB / 5.3 秒白付）"
        assert src.file_sha256 == ""

        src.verify_unchanged()  # = 写前那道闸：第一次算，算完缓存
        assert calls == [p]
        baseline = src.file_sha256
        assert baseline != "", "算出来的基准要缓存在 OpenedSource 上"

        src.verify_unchanged()  # 第二次是真比对：再算一遍"当前"，但基准复用
        assert calls == [p, p]
        assert src.file_sha256 == baseline, "基准不能被覆盖掉"


def test_gate1_still_catches_a_file_changed_after_open(tmp_path, monkeypatch) -> None:
    """惰性之后闸 1 **不能**退化成「自己跟自己比」（那等于没校验）。

    判据：打开之后把文件换掉，写前那道闸必须响。
    （没有 opened_stamp 的话，基准哈希是保存那一刻才算的 —— 与"当前哈希"恒等，
    这条用例就会静默通过。）
    """
    meta = _tiny_meta()
    p = tmp_path / "data.unity3d"
    p.write_bytes(b"UnityFS-fake")
    _open_a_fake_container(meta, monkeypatch)

    with session.open_source(p) as src:
        assert src.file_sha256 == "", "前提：打开时没算过"
        p.write_bytes(b"UnityFS-fake-and-then-someone-else-touched-it")

        with pytest.raises(UserFacingError) as excinfo:
            src.verify_unchanged()

        assert "改过" in str(excinfo.value)


def test_a_save_hashes_the_container_exactly_once(tmp_path, monkeypatch) -> None:
    """一次保存只算一遍 sha256（基准与当前是同一个值，不重复付 5.3 秒）。"""
    meta, container_path, reader, written = _fake_container_source(tmp_path, monkeypatch)
    monkeypatch.setattr(session, "_verify_rewritten", lambda *_a, **_k: None)
    calls = _count_sha256_calls(monkeypatch)

    src = session.OpenedSource(
        kind=session.SourceKind.UNITY3D, path=container_path, meta=meta, reader=reader
    )
    session.save(src, check_game_running=False)

    assert written, "重写没被调用，这条用例没覆盖到保存路径"
    assert calls == [container_path], "闸 1 应当只算一次（不是基准一次 + 当前一次）"
