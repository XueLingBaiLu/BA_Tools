r"""包的**成员时间戳**：写回一个 zip 之后仍与源包逐字节相同（零 Qt）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_package_stamps.py -n 0 -q
      -> 「空改动重写一个 zip 后与源逐字节相同」这条从**红**变绿。

## 为什么它必须是绿的

从前 _dump 用 archive.writestr(name, blob)，每个成员的时间戳取**当时那一刻**。
后果（实测，源 = DataBaseCompiled/BA_Mod.zip）：

    同一份内容写两次 -> 长度都是 324,685 B，成员内容 / CRC / 压缩字节**全同**，
    **第一个不同的字节在偏移 0xA**（DOS 时间字段）。

于是「删 3 行再插回 3 行 ⇒ 输出与输入逐字节相同」**永远不成立** ——
而那是产品对用户承诺的可逆性（反向 Δ 还原，见 tests/test_cli_row_restore.py）。

## 这条口径的唯一实现

plan_package 在 stamps is None 时自动沿用「你打开的那个 zip」的成员时间戳
（core/edit/package.py::_path_stamps）。⛔ 调用方不用记得传 —— 那正是会漏的设计。

⚠ 用例的源 = DataBaseCompiled/BA_Mod.zip（**只读**），产物一律落 tmp_path；
⛔ 用例绝不碰游戏目录。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ba_mod_maker.core import session
from ba_mod_maker.core.data.from_source import dataset_from_source
from ba_mod_maker.core.edit.package import (
    member_stamps,
    plan_package,
    replace_package,
    write_package,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT.parent / "DataBaseCompiled" / "BA_Mod.zip"

#: 源包的时间戳（实测：24 个成员**全是**这一个时刻；产品写出来的包还会多一个别名成员，
#: 它没有对应的源时间戳 ⇒ 退回 ZipInfo 的默认 1980）。
SOURCE_STAMP = (2026, 9, 29, 21, 37, 46)
#: 故意挑一个与上面不同的时刻（1980 之后，DOS 存得下）。
OTHER_STAMP = (2020, 1, 2, 3, 4, 6)


pytestmark = pytest.mark.skipif(
    not SOURCE.is_file(), reason=f"本机没有 {SOURCE}（用例的只读源）"
)


def _open_instance(path: Path):
    """走**真实打开链路**把一份包装成 Instance（与 CLI / GUI 同一条路）。"""
    with session.open_source(str(path)) as src:
        dataset = dataset_from_source(src)
        return dataset.instance(dataset.default_instance_id)


def _rewrite(source: Path, target: Path) -> Path:
    """空改动写一趟（一份原样包）。返回落点。"""
    instance = _open_instance(source)
    report = session.save_instance(instance, (), target=str(target))
    return Path(str(report.path))


# --------------------------------------------------------------------------- #
# 1. 最硬那条：空改动重写一个 zip 之后，它与源**逐字节相同**
# --------------------------------------------------------------------------- #


def test_rewriting_a_package_is_byte_identical_to_its_source(tmp_path: Path) -> None:
    """★ 判据本体：空改动重写一个包，产物与源包**逐字节相同**（这条从前是红的）。

    ⚠ 源得是**产品写过的包**：DataBaseCompiled/BA_Mod.zip 只有 24 个成员
    （缺单数别名 PlaneFlyPreset.json），产品写一趟会补上它 —— 所以先写一趟再比。
    首差偏移在红的时候是 **0xA**（DOS 时间字段），不是长度差异。
    """
    first = _rewrite(SOURCE, tmp_path / "first.zip")
    target = tmp_path / "rewritten.zip"
    _rewrite(first, target)

    before, after = first.read_bytes(), target.read_bytes()
    assert len(after) == len(before), f"长度变了：{len(before)} -> {len(after)}"
    assert after == before, "★ 空改动重写出来的包与源包不是逐字节相同"

    # ★ 尺子必须先证明**能红**（AGENTS §0.9 动作 2）—— 正控就是下面这两句。
    #
    # ⚠ 这里踩过一次，别再踩：第一版只比"写 A 与写 B 两趟"，那**恒真** ——
    #   第一趟的时间戳是"现在"，第二趟沿用第一趟的 ⇒ 两趟永远相同，尺子红不了。
    #   要红，基准必须是**不是我们写出来的那份**：源包（或任何别处的时刻）。
    source_stamps = member_stamps(SOURCE)
    assert member_stamps(target)["Units.json"] == source_stamps["Units.json"], (
        "产物的时间戳不是沿用源包的 —— 那上面那句'逐字节相同'就没在量时间戳这一样"
    )


def test_the_first_write_completes_the_singular_alias_member(tmp_path: Path) -> None:
    """第一趟会**补齐**单数别名成员（源 24 个 -> 写出来 25 个，kb/12 那条）。

    ⚠ 这条是上面那条的**前提说明**：源包本身只有 24 个成员，所以"第一趟"必然
    多一个 —— 逐字节可比的前提是拿**产品写过的包**当源（往返用例就是这么串的）。
    """
    first = _rewrite(SOURCE, tmp_path / "first.zip")
    with zipfile.ZipFile(SOURCE) as src_zip, zipfile.ZipFile(first) as first_zip:
        assert sorted(set(first_zip.namelist()) - set(src_zip.namelist())) == [
            "PlaneFlyPreset.json"
        ]
        # 源里已有的成员，内容一个都不许变
        assert [n for n in src_zip.namelist() if src_zip.read(n) != first_zip.read(n)] == []


def test_a_second_write_is_byte_identical_to_the_first(tmp_path: Path) -> None:
    """两代也一样（逐字节相同不是"同一秒里凑巧"）：A -> B，B 与 A 逐字节相同。"""
    first = _rewrite(SOURCE, tmp_path / "first.zip")
    second = _rewrite(first, tmp_path / "second.zip")
    assert second.read_bytes() == first.read_bytes()


# --------------------------------------------------------------------------- #
# 2. 时间戳的唯一入口
# --------------------------------------------------------------------------- #


def test_member_stamps_reads_the_archives_own_time_stamps(tmp_path: Path) -> None:
    """member_stamps(path) = 那个 zip 里**每个成员的时间戳**（成员集合与 namelist 相同）。

    ⚠ 值分两种：源里有的成员沿用源时间戳；产品补出来的那个别名成员没有源时间戳
    （退回 ZipInfo 的默认 1980）—— 两边都如实读出来，函数不做任何归一。
    """
    written = _rewrite(SOURCE, tmp_path / "written.zip")
    stamps = member_stamps(written)
    with zipfile.ZipFile(written) as archive:
        assert set(stamps) == set(archive.namelist())
    assert set(stamps.values()) == {SOURCE_STAMP, (1980, 1, 1, 0, 0, 0)}
    assert stamps["Units.json"] == SOURCE_STAMP, "Units.json 的时间戳没有沿用源包"


def test_a_rewrite_keeps_every_member_time_stamp(tmp_path: Path) -> None:
    """同一个包的第二次写：每个成员的时间戳**一个都不许变**（逐个比，不只是集合相等）。"""
    first = _rewrite(SOURCE, tmp_path / "first.zip")
    second = _rewrite(first, tmp_path / "second.zip")
    assert member_stamps(second) == member_stamps(first)


# --------------------------------------------------------------------------- #
# 3. 负控：参数是真的在管用的（不调它就换不成那个时刻）
# --------------------------------------------------------------------------- #


def test_explicit_stamps_are_actually_used(tmp_path: Path) -> None:
    """★ 正控：显式给一个**不同的**时刻 ⇒ 产物确实不同，且**只**差在时间戳上。

    没有这条，上面那几条"逐字节相同"可能只是"写入器根本没用这个参数"。
    """
    instance = _open_instance(SOURCE)
    # ⚠ 基准时间戳必须**在写之前**量（这份源包是别人写的，不是我们写出来的）——
    #   写完再量就变成"自己跟自己比"，而"自己跟自己比"永远绿（本文件开头那个坑）。
    source_stamps = member_stamps(SOURCE)
    baseline = tmp_path / "baseline.zip"
    write_package(plan_package(instance, changed_tables=()), baseline)

    shifted = tmp_path / "shifted.zip"
    write_package(
        plan_package(instance, changed_tables=(), stamps={n: OTHER_STAMP for n in source_stamps}),
        shifted,
    )

    assert baseline.read_bytes() != shifted.read_bytes(), "给了别的时间戳，产物居然没变"
    with zipfile.ZipFile(baseline) as a, zipfile.ZipFile(shifted) as b:
        assert a.namelist() == b.namelist()
        assert [n for n in a.namelist() if a.read(n) != b.read(n)] == [], (
            "时间戳之外的东西也变了 —— 那就不是在量时间戳这一样"
        )


# --------------------------------------------------------------------------- #
# 4. 时间戳**不**碰那三种没有 zip 成员时间戳的来源
# --------------------------------------------------------------------------- #


def test_stamps_go_through_save_instance_to_a_new_path(tmp_path: Path) -> None:
    """save_instance(..., stamps=…) 显式传时**真的透传**（走到写盘那一步）。"""
    instance = _open_instance(SOURCE)
    target = tmp_path / "explicit.zip"
    report = session.save_instance(
        instance,
        (),
        target=str(target),
        stamps={n: OTHER_STAMP for n in member_stamps(SOURCE)},
    )
    assert report.path.is_file()
    # ⚠ 只核"我给了时间戳的那些成员"：产品补出来的别名条目（PlaneFlyPreset.json）
    #   在源包里不存在，自然取不到时间戳 ⇒ 它退回默认的 1980。这是预期，不是失败。
    # ⚠ 期望值用**常量 OTHER_STAMP**，⛔ 别回头去读产物的时间戳 —— 那是自证（恒真）。
    source_names = member_stamps(SOURCE)
    got = member_stamps(report.path)
    assert {name: got[name] for name in source_names} == dict.fromkeys(source_names, OTHER_STAMP)


def test_a_directory_source_has_no_time_stamps_to_inherit(tmp_path: Path) -> None:
    """目录来源：连"沿用"这回事都不存在（没有 opened_file）⇒ 退回默认，不许炸。"""
    instance = _open_instance(SOURCE)
    assert instance.opened_file is not None
    instance.opened_file = None  # 模拟目录来源那一路（opened_file 是 None）
    plan = plan_package(instance, changed_tables=())
    assert plan.stamps in (None, {})
    target = tmp_path / "from-directory.zip"
    write_package(plan, target)
    assert zipfile.ZipFile(target).namelist() == list(plan.entry_names)


def test_an_opened_file_that_vanished_falls_back_instead_of_exploding(tmp_path: Path) -> None:
    """打开的那个包**被删了** ⇒ 退回默认时间戳（1980），⛔ 不是抛异常。

    为一个成员时间戳挡住一次保存不值得；而且这时本来就没有"复核不过"可言。
    """
    copy = tmp_path / "copy.zip"
    copy.write_bytes(SOURCE.read_bytes())
    instance = _open_instance(copy)
    copy.unlink()
    plan = plan_package(instance, changed_tables=())
    assert plan.stamps in (None, {})
    target = tmp_path / "no-source.zip"
    write_package(plan, target)
    assert set(member_stamps(target).values()) == {(1980, 1, 1, 0, 0, 0)}


def test_replace_package_keeps_the_stamps_too(tmp_path: Path) -> None:
    """写回**原文件**那条路（replace_package）同样保时间戳（先复核、后替换不变）。"""
    work = tmp_path / "work.zip"
    work.write_bytes(_rewrite(SOURCE, tmp_path / "first.zip").read_bytes())
    before = work.read_bytes()
    instance = _open_instance(work)
    replace_package(plan_package(instance, changed_tables=()), work)
    assert work.read_bytes() == before


def test_changed_content_still_lands_while_stamps_are_kept(tmp_path: Path) -> None:
    """改一格再写回：内容真的变了，而**没动过的成员时间戳照旧**（两件事不互相吃掉）。"""
    work = tmp_path / "work.zip"
    work.write_bytes(_rewrite(SOURCE, tmp_path / "first.zip").read_bytes())
    stamps_before = member_stamps(work)

    instance = _open_instance(work)
    units = instance.table("Units")
    units.rows[0][units.columns.index("Cost")] = 123456
    target = tmp_path / "changed.zip"
    session.save_instance(instance, ("Units",), target=str(target))

    with zipfile.ZipFile(target) as archive:
        rows = json.loads(archive.read("Units.json"))
    assert rows[0]["Cost"] == 123456, "改动没落进产物"
    assert member_stamps(target) == stamps_before, "没动过的成员时间戳变了"
