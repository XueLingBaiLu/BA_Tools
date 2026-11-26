r"""row-restore 的判据：把反向 Δ 加回去，且与 row-delete 构成**逐字节**往返。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_cli_row_restore.py -n 0 -q
      -> 收集 0 -> N；最硬那条是「A ->(row-delete)-> B ->(row-restore)-> C，
         sha256(C) == sha256(A) 且长度相等」。

## 为什么"逐字节"是唯一的验收口径

上一轮留的就是一句「成员表相同 + 内容相同」—— 用户已经否掉那条了：它不是"能不能撤"，
它是"看起来像能撤"。真的有差别的地方只有两处，而且都**不体现在成员内容上**：
zip 成员的时间戳（写包那一层治，见 tests/test_package_stamps.py）与**行序**（本文件治）。

⚠ 用例的源 = DataBaseCompiled/BA_Mod.zip（**只读**）；产物一律落 tmp_path；⛔ 绝不碰游戏目录。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ba_mod_maker.cli import row_delete, row_restore

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT.parent / "DataBaseCompiled" / "BA_Mod.zip"

pytestmark = pytest.mark.skipif(
    not SOURCE.is_file(), reason=f"本机没有 {SOURCE}（用例的只读源）"
)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids(path: Path, table: str = "Units") -> list[int]:
    with zipfile.ZipFile(path) as archive:
        return [int(row["Id"]) for row in json.loads(archive.read(f"{table}.json"))]


def _values(path: Path, table: str, row_id: int) -> dict:
    with zipfile.ZipFile(path) as archive:
        rows = json.loads(archive.read(f"{table}.json"))
    return next(row for row in rows if int(row["Id"]) == row_id)


def _write_first_package(target: Path) -> Path:
    """先空改动写一趟（源只有 24 成员，产品写会补单数别名）—— 往返的 A 必须是它。"""
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source

    with session.open_source(str(SOURCE)) as src:
        dataset = dataset_from_source(src)
        instance = dataset.instance(dataset.default_instance_id)
        report = session.save_instance(instance, (), target=str(target))
    return Path(str(report.path))


def _delete_args(source: Path, ids: list[int], out: Path) -> argparse.Namespace:
    """row-delete 的参数。refs="force"：最后 3 行**确实**被别的行引用（实测 70 行），
    默认的 --refuse 会（正确地）拒删 —— 这里要的是"删掉"，所以点名 --force。"""
    return argparse.Namespace(
        source=str(source),
        table="Units",
        row_id=[str(i) for i in ids],
        out=str(out),
        write=True,
        delta_out=None,
        allow_running=True,
        refs="force",
    )


def _restore_args(
    source: Path, delta: Path, out: Path, *, write: bool = True
) -> argparse.Namespace:
    return argparse.Namespace(
        source=str(source), delta=str(delta), out=str(out), write=write, allow_running=True
    )


def _silent(fn, *a, **kw) -> tuple[int, str]:
    """跑一条命令，把它的 stdout **和 stderr 一起**收走 —— 返回 (退出码, 两份合起来的话)。

    ⚠ 必须收 stderr：本命令的人话分两处印 —— 计划走 stdout，拒写/降级提示走 stderr。
    第一版只收 stdout，于是「v1 明说不是逐字节」那条断言量的是**空字符串**（假红）。
    """
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = fn(*a, **kw)
    return code, buffer.getvalue()


def _round_trip(tmp_path: Path, count: int = 3) -> tuple[Path, Path, Path, Path]:
    """A ->(row-delete)-> B ->(row-restore)-> C。返回 (A, B, C, delta)。"""
    a = _write_first_package(tmp_path / "A.zip")
    ids = _ids(a)[-count:]
    b = tmp_path / "B.zip"
    code, _ = _silent(row_delete.run, _delete_args(a, ids, b))
    assert code == 0, "row-delete 没成功，后面就没意义了"
    delta = Path(str(b) + ".row-delete-delta.json")
    c = tmp_path / "C.zip"
    code, _ = _silent(row_restore.run, _restore_args(b, delta, c))
    assert code == 0, "row-restore 没成功"
    return a, b, c, delta


# --------------------------------------------------------------------------- #
# 1. 注册与默认干跑
# --------------------------------------------------------------------------- #


def test_the_command_is_registered_and_defaults_to_dry_run(tmp_path: Path) -> None:
    """★ 注册得上；--write **默认关着**（⛔ 不给就只出计划）。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    added = row_restore.add_parser(sub)
    assert added is not None
    parsed = parser.parse_args(["row-restore", str(SOURCE), "d.json"])
    assert parsed.write is False, "默认必须是干跑 —— 加回一行也是改数据"
    assert parsed.out is None
    assert parsed.allow_running is False


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """★ 干跑：源 sha 一个字节不变，--out 那个文件根本不存在。"""
    a, b, _c, delta = _round_trip(tmp_path)
    before_b, before_delta = _sha(b), _sha(delta)
    out = tmp_path / "never.zip"

    code, text = _silent(row_restore.run, _restore_args(b, delta, out, write=False))

    assert code == 0
    assert not out.exists(), "干跑居然写了文件"
    assert _sha(b) == before_b and _sha(delta) == before_delta
    assert "没给 --write" in text


# --------------------------------------------------------------------------- #
# 2. 最硬那条：逐字节往返
# --------------------------------------------------------------------------- #


def test_the_round_trip_is_byte_identical(tmp_path: Path) -> None:
    """★★ 判据本体：A ->(row-delete)-> B ->(row-restore)-> C ⇒ sha256(C)==sha256(A) 且等长。

    ⛔ 不许用"成员表相同 + 内容相同"代替这一条 —— 那两样在时间戳与行序变了的前提下
    照样成立，用户已经否掉了。
    """
    a, _b, c, _delta = _round_trip(tmp_path)

    assert len(c.read_bytes()) == len(a.read_bytes()), (
        f"长度不等：A={len(a.read_bytes())} C={len(c.read_bytes())}"
    )
    assert _sha(c) == _sha(a), "sha256 不同 —— 不是逐字节往返"
    assert c.read_bytes() == a.read_bytes()

    # ★ 正控（AGENTS §0.9 动作 2）：这条尺子**能红**吗？
    #   把 C 的任何一个字节翻一下，sha 必须立刻不同 —— 否则上面那句恒真。
    tampered = tmp_path / "tampered.zip"
    blob = bytearray(a.read_bytes())
    blob[-1] ^= 0xFF
    tampered.write_bytes(bytes(blob))
    assert _sha(tampered) != _sha(a), "sha 尺子红不了（恒真）"


def test_the_row_goes_back_to_its_original_index(tmp_path: Path) -> None:
    """行序也要复原：还原之后那一行**在原下标上**（不是被追加到末尾）。

    ⚠ 这条单独立着，因为"行数对、行序错"正是最常见的那种假绿 ——
    逐字节那条红了以后，这一条告诉你红在哪。
    """
    a = _write_first_package(tmp_path / "A.zip")
    before = [int(row["Id"]) for row in json.loads(zipfile.ZipFile(a).read("Units.json"))]
    doomed = before[-3:]
    b = tmp_path / "B.zip"
    assert _silent(row_delete.run, _delete_args(a, doomed, b))[0] == 0
    delta = Path(str(b) + ".row-delete-delta.json")
    c = tmp_path / "C.zip"
    assert _silent(row_restore.run, _restore_args(b, delta, c))[0] == 0

    after = [int(row["Id"]) for row in json.loads(zipfile.ZipFile(c).read("Units.json"))]
    assert after == before, "行序与原表不同 —— 插回去的下标不对（或者按降序插了）"


def test_the_delta_carries_the_original_index(tmp_path: Path) -> None:
    """★ Δ 里必须有 index（原表下标）：没有它，"加回去"就只能追加到末尾。"""
    a, _b, _c, delta = _round_trip(tmp_path)
    doc = json.loads(Path(delta).read_text(encoding="utf-8"))
    assert doc["kind"] == "row-delete-delta"
    assert doc["version"] == row_restore.DELTA_VERSION >= 2
    group = doc["tables"][0]
    indexes = [row["index"] for row in group["rows"]]
    assert indexes == sorted(indexes), "Δ 里的 index 不是升序"
    original = [int(row["Id"]) for row in json.loads(zipfile.ZipFile(a).read("Units.json"))]
    for row in group["rows"]:
        assert original[row["index"]] == row["Id"], "index 指的不是那一行"


def test_the_values_are_the_whole_original_rows(tmp_path: Path) -> None:
    """Δ 里是**整行原值**（一个字段不少），还原靠它，不靠"补丁里记着原值"之外的旁证。"""
    a, _b, _c, delta = _round_trip(tmp_path)
    doc = json.loads(Path(delta).read_text(encoding="utf-8"))
    for row in doc["tables"][0]["rows"]:
        assert row["values"] == _values(a, "Units", int(row["Id"]))


# --------------------------------------------------------------------------- #
# 3. v1 Δ：还能读，但**明说**不是逐字节
# --------------------------------------------------------------------------- #


def test_v1_delta_is_still_readable_and_appends_at_the_end(tmp_path: Path) -> None:
    """★ 向后兼容：v1（顶层单表、行里没有 index）⇒ 追加到末尾，并明印「不是逐字节还原」。

    ⛔ 不许静默降级：用户得知道这次还原不保行序。
    """
    a = _write_first_package(tmp_path / "A.zip")
    before = [int(row["Id"]) for row in json.loads(zipfile.ZipFile(a).read("Units.json"))]
    # ⚠ 必须删**中间**那几行，不然这条用例量不出东西：删末尾两行再"追加到末尾"，
    #   行序**本来就会**与原表一样（第一版就是这么写成假绿的，实测踩过）。
    doomed = before[len(before) // 2 : len(before) // 2 + 2]

    b = tmp_path / "B.zip"
    assert _silent(row_delete.run, _delete_args(a, doomed, b))[0] == 0
    full = json.loads(Path(str(b) + ".row-delete-delta.json").read_text(encoding="utf-8"))
    rows = full["tables"][0]["rows"]

    # 造一份**真的 v1**（把 index 摘掉、顶层摊平成单表）
    v1 = tmp_path / "v1.row-delete-delta.json"
    v1.write_text(
        json.dumps(
            {
                "table": "Units",
                "rows": [{"Id": row["Id"], "values": row["values"]} for row in rows],
                "target": str(b),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    c = tmp_path / "C.zip"
    code, text = _silent(row_restore.run, _restore_args(b, v1, c))

    assert code == 0, "v1 的 Δ 加不回去（兼容性坏了）"
    assert "不是逐字节还原" in text, "v1 降级了却没说 —— 静默降级"
    after = [int(row["Id"]) for row in json.loads(zipfile.ZipFile(c).read("Units.json"))]
    assert sorted(after) == sorted(before), "行没加全"
    assert after[-len(doomed) :] == doomed, "v1 应当**追加到末尾**（行序与原表不同）"
    assert after != before, "v1 不可能把行序也还原（没有 index 可用）"
    # ⛔ 与 v2 那条对照：v2 的产物是逐字节相同的，v1 的不是 —— 这就是"明说降级"的由来。
    assert _sha(c) != _sha(a), "这份 v1 的产物居然与 A 逐字节相同？那这条用例没在量 v1"


# --------------------------------------------------------------------------- #
# 4. 用不了的那些情况：整条命令不动
# --------------------------------------------------------------------------- #


def test_a_missing_index_stops_the_whole_command(tmp_path: Path) -> None:
    """★ 负控：v2 Δ 里少一个 index ⇒ 退出 2、零写入（不许"猜一个下标"顶上）。"""
    a, b, _c, delta = _round_trip(tmp_path)
    doc = json.loads(Path(delta).read_text(encoding="utf-8"))
    doc["tables"][0]["rows"][0].pop("index")
    broken = tmp_path / "no-index.json"
    broken.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    before = _sha(b)
    out = tmp_path / "never.zip"
    code, _ = _silent(row_restore.run, _restore_args(b, broken, out))

    assert code == 2
    assert not out.exists(), "Δ 不合法却还是写了盘"
    assert _sha(b) == before


def test_an_id_that_is_still_present_stops_the_command(tmp_path: Path) -> None:
    """★ 负控：Δ 里那一行**还在表里** ⇒ 退出 2（拿错 Δ，或者删除压根没生效）。"""
    a, _b, _c, delta = _round_trip(tmp_path)
    out = tmp_path / "never.zip"
    code, text = _silent(row_restore.run, _restore_args(a, delta, out))

    assert code == 2, "Id 还在表里却让它插 —— 会插出两行同 Id"
    assert "还在表里" in text or "错误" in text
    assert not out.exists()


def test_an_out_of_range_index_stops_the_command(tmp_path: Path) -> None:
    """★ 负控：index 越界 ⇒ 退出 2（越界插出来的东西行序肯定是错的）。"""
    a, b, _c, delta = _round_trip(tmp_path)
    doc = json.loads(Path(delta).read_text(encoding="utf-8"))
    doc["tables"][0]["rows"][0]["index"] = 10**6
    broken = tmp_path / "bad-index.json"
    broken.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "never.zip"
    code, text = _silent(row_restore.run, _restore_args(b, broken, out))
    assert code == 2
    assert "越界" in text
    assert not out.exists()


def test_a_delta_that_is_not_a_delta_is_loud(tmp_path: Path) -> None:
    """★ 负控：认不出来的 JSON ⇒ 退出 2，⛔ 不许当成"空 Δ"静默放过。"""
    a = _write_first_package(tmp_path / "A.zip")
    junk = tmp_path / "junk.json"
    junk.write_text('{"hello": 1}', encoding="utf-8")
    out = tmp_path / "never.zip"
    code, text = _silent(row_restore.run, _restore_args(a, junk, out))
    assert code == 2
    assert not out.exists()
    assert "Δ" in text


def test_a_newer_delta_version_is_refused(tmp_path: Path) -> None:
    """★ 负控：version 比我认的新 ⇒ 退出 2（宁可说"我读不了"，别猜着读）。"""
    a, b, _c, delta = _round_trip(tmp_path)
    doc = json.loads(Path(delta).read_text(encoding="utf-8"))
    doc["version"] = row_restore.DELTA_VERSION + 1
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "never.zip"
    code, _ = _silent(row_restore.run, _restore_args(b, newer, out))
    assert code == 2
    assert not out.exists()


# --------------------------------------------------------------------------- #
# 5. insert_row 的下标（本模块唯一改到 core 的那一处）
# --------------------------------------------------------------------------- #


def _one_instance():
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source

    with session.open_source(str(SOURCE)) as src:
        dataset = dataset_from_source(src)
        return dataset.instance(dataset.default_instance_id)


def test_insert_row_defaults_to_appending(tmp_path: Path) -> None:
    """**向后兼容**：不给 index = 追加到末尾（与从前一模一样）。"""
    from ba_mod_maker.core.edit.changeset import Changeset

    instance = _one_instance()
    changeset = Changeset(instance)
    rows = changeset.table("Units").rows
    first_column = changeset.table("Units").columns[0]
    changeset.insert_row("Units", {"Id": 999001})
    assert len(rows) == 541
    assert rows[-1][0] == 999001 or rows[-1][changeset.table("Units").columns.index("Id")] == 999001
    assert first_column  # 只是让"列序"这件事在用例里露个面


def test_insert_row_at_an_index_puts_it_exactly_there() -> None:
    """给了 index 就插在**那个下标**上（不是"附近"）。"""
    from ba_mod_maker.core.edit.changeset import Changeset

    instance = _one_instance()
    changeset = Changeset(instance)
    table = changeset.table("Units")
    idx = table.columns.index("Id")
    before = [row[idx] for row in table.rows]

    changeset.insert_row("Units", {"Id": 999002}, index=5)

    after = [row[idx] for row in table.rows]
    assert len(after) == len(before) + 1
    assert after[5] == 999002
    assert after[:5] == before[:5] and after[6:] == before[5:], "插进去的位置不对"


def test_insert_row_refuses_an_out_of_range_index() -> None:
    """★ 负控：越界的下标**报错**，⛔ 不静默退回"追加到末尾"（那会写出错的行序）。"""
    from ba_mod_maker.core.edit.changeset import Changeset
    from ba_mod_maker.core.errors import TableError

    instance = _one_instance()
    changeset = Changeset(instance)
    size = len(changeset.table("Units").rows)
    with pytest.raises(TableError):
        changeset.insert_row("Units", {"Id": 999003}, index=size + 1)
    with pytest.raises(TableError):
        changeset.insert_row("Units", {"Id": 999004}, index=-1)
    assert len(changeset.table("Units").rows) == size, "报错了却把行插进去了"


def test_inserting_many_must_go_in_ascending_index_order() -> None:
    """★ 口径 3 的机械断言：**按 index 升序**插，插完的行序才与"删之前"一致。

    这条是往返能逐字节的前提。反面：降序插出来的是"行数对、行序错"。
    """
    from ba_mod_maker.core import session
    from ba_mod_maker.core.data.from_source import dataset_from_source
    from ba_mod_maker.core.edit.changeset import Changeset

    with session.open_source(str(SOURCE)) as src:
        dataset = dataset_from_source(src)
        original = dataset.instance(dataset.default_instance_id)
    table = original.table("Units")
    idx = table.columns.index("Id")
    before = [row[idx] for row in table.rows]
    # ⚠ 行下标 ≠ Id（这张表两者刚好差 167）。第一版把**下标**当 Id 用，
    #   于是 delete_row 删的不是那几行、insert_row 又插不上（实测踩过）。
    indexes = [len(before) - 3, len(before) - 2, len(before) - 1]
    doomed = [before[i] for i in indexes]

    with session.open_source(str(SOURCE)) as src:
        dataset = dataset_from_source(src)
        instance = dataset.instance(dataset.default_instance_id)
    changeset = Changeset(instance)
    saved = [list(changeset.table("Units").rows[i]) for i in indexes]
    for row_id in doomed:
        changeset.delete_row("Units", row_id)

    for index, row in sorted(zip(indexes, saved, strict=True), key=lambda pair: pair[0]):
        changeset.insert_row("Units", dict(zip(table.columns, row, strict=True)), index=index)

    after = [row[idx] for row in changeset.table("Units").rows]
    assert after == before, "升序插完之后行序与原表不同"


# --------------------------------------------------------------------------- #
# 6. 真进程：退出码与接线（不是直接调 run()）
# --------------------------------------------------------------------------- #


def _cli(*argv: str) -> subprocess.CompletedProcess:
    """跑**真进程**（python -m ba_mod_maker.cli …）—— 接线与退出码只有这样才能真验到。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(REPO_ROOT / "src"))
    return subprocess.run(
        [sys.executable, "-m", "ba_mod_maker.cli", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def test_the_cli_lists_row_restore_and_it_runs_dry(tmp_path: Path) -> None:
    """★ 接线：--help 里有 row-restore；真跑一次（干跑）⇒ **退出码 0**、零写入。"""
    assert "row-restore" in _cli("--help").stdout

    a, b, _c, delta = _round_trip(tmp_path)
    before_b = _sha(b)
    out = tmp_path / "never.zip"
    done = _cli("row-restore", str(b), str(delta), "--out", str(out))

    assert done.returncode == 0, done.stderr
    assert not out.exists(), "干跑写了盘"
    assert _sha(b) == before_b
    assert "没给 --write" in done.stdout


def test_the_cli_exit_code_is_two_when_it_cannot_run(tmp_path: Path) -> None:
    """★ 退出码 2 = 用不了（打不开源 / Δ 不合法）—— 真进程的读数，不是 run() 的返回值。"""
    missing = _cli("row-restore", str(tmp_path / "没有这个包.zip"), str(tmp_path / "没有.json"))
    assert missing.returncode == 2

    a, _b, _c, _delta = _round_trip(tmp_path)
    junk = tmp_path / "junk.json"
    junk.write_text("{}", encoding="utf-8")
    bad = _cli("row-restore", str(a), str(junk), "--out", str(tmp_path / "never.zip"))
    assert bad.returncode == 2

