r"""`row-delete` 的判据（产品三原子动词里补的那一个「删除」）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_cli_row_delete.py -n 0 -q
      -> 收集 6 -> N；每条**先红后绿**。

## 为什么每条都要有负控

删行是**不可逆**的（对用户而言），所以本文件里「干跑不写盘」「找不到 Id 就不动」
「除了目标表以外一个成员都不许变」「游戏在跑就拒」这几条都配了负控 —— 尺子先证明自己能红。

## 引用闸那几条的尺子

判据 = 产品自己的标识 `core/edit/refs.py::referencing_rows`，⛔ 不用方位词、⛔ 不自造第二套。

| 用例 | 正控 | 负控 |
|---|---|---|
| 引用报得出来 | Specializations#3 -> 32 条（跨 1 张表） | Specializations#11 -> 0 条 |
| 闭包比直接多一层 | Units#3 -> 直接 38 / 闭包 43 | Specializations#11 -> 闭包 0 |
| 游戏在跑就拒 | `is_game_running` 打桩成 True -> 退出 2、零写入 | 打桩成 False -> 退出 0、写了 |

⚠ 用例的源 = `DataBaseCompiled/BA_Mod.zip`（**只读**，Units 540 行）；产物一律落 `tmp_path`。
⛔ 用例**绝不碰游戏目录**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from ba_mod_maker.cli import row_delete

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT.parent / "DataBaseCompiled" / "BA_Mod.zip"

#: 源里**被引用**的那一行（正控）：Specializations#3 = USMC，被 32 行指着。
REFERRED = ("Specializations", 3, 32)
#: 源里**没有任何引用**的那一行（负控）：Specializations#11 = Editor。
LONELY = ("Specializations", 11)
#: 源里引用关系最深的一行：Units#3 = BMP-3，直接引用者 38、传递闭包 43。
DEEP = ("Units", 3, 38, 43)


def _ids(path: Path, table: str = "Units.json") -> list[int]:
    with zipfile.ZipFile(path) as z:
        rows = json.loads(z.read(table))
    return [int(r["Id"]) for r in rows]


def _rows(path: Path, table: str) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(table if table.endswith(".json") else table + ".json"))


def _args(
    source, table, ids, *, out=None, write=False, refs=None, allow_running=False, delta_out=None
):
    return argparse.Namespace(
        source=str(source),
        table=table,
        row_id=[str(i) for i in ids],
        out=str(out) if out else None,
        write=write,
        delta_out=str(delta_out) if delta_out else None,
        allow_running=allow_running,
        refs=refs or row_delete.REFUSE,
    )


# --------------------------------------------------------------------------- #
# 原有 6 条：单表直删的形状、干跑、Id 找不到、成员守门
# --------------------------------------------------------------------------- #


def test_the_command_is_registered_and_defaults_to_dry_run() -> None:
    """★ 注册得上；`--write` **默认关着**；引用策略**默认是最保守的 refuse**。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    added = row_delete.add_parser(sub)
    assert added is not None
    parsed = parser.parse_args(["row-delete", str(SOURCE), "Units", "1", "2"])
    assert parsed.write is False, "默认必须是干跑 —— 删行比改一格危险得多"
    assert parsed.row_id == [1, 2], "row_id 是 int（按 Id 定位，⛔ 不是行下标）"
    assert parsed.refs == "refuse", "默认策略必须是最保守的那一档"
    # 三档互斥：同时给两个必须报错（否则「默认最保守」就成了一句空话）
    with pytest.raises(SystemExit):
        parser.parse_args(["row-delete", str(SOURCE), "Units", "1", "--refuse", "--force"])


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """★ 干跑：源 sha 一个字节不变，目标文件根本不存在。"""
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    out = tmp_path / "never.zip"
    doomed = _ids(SOURCE)[-3:]
    # 干跑**照样会**走引用闸（拒也要先说出来）；这几行有子行，所以要说 --force
    args = _args(SOURCE, "Units", doomed, out=out, refs=row_delete.FORCE)
    assert row_delete.run(args) == 0
    assert not out.exists(), "干跑居然写了文件"
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before


def test_a_missing_id_stops_the_whole_command(tmp_path: Path) -> None:
    """★ 负控（**整条命令不动**）：要删的 Id 里只要有一个不存在 ⇒ 退出 2、零写入。"""
    out = tmp_path / "never.zip"
    existing = _ids(SOURCE)[-1]
    ghost = max(_ids(SOURCE)) + 9999
    assert row_delete.run(_args(SOURCE, "Units", [existing, ghost], out=out, write=True)) == 2
    assert not out.exists(), "有 Id 找不到，却还是写了盘"
    assert not Path(str(out) + ".row-delete-delta.json").exists()


def test_write_removes_exactly_those_rows_and_keeps_every_other_member(tmp_path: Path) -> None:
    """★ 真写：行数正好 -N、那几个 Id 没了、**除目标表外逐字节没变**。

    最后一条是 **kb/12 那个坑的守门人**：这份包里有一个只在游戏根目录存在的成员
    （`PlaneFlyPreset.json`）—— 重打包时最容易把它弄丢。

    ⚠ 这几行在库里**是有子行的**（Units 540 行全部有引用），所以要说 `--force`：
    默认的 `--refuse` 会（正确地）拒掉它（见下面那条用例）。
    """
    out = tmp_path / "clean.zip"
    before_ids = _ids(SOURCE)
    doomed = before_ids[-3:]
    args = _args(SOURCE, "Units", doomed, out=out, write=True, refs=row_delete.FORCE)
    assert row_delete.run(args) == 0
    assert out.is_file()

    after_ids = _ids(out)
    assert len(after_ids) == len(before_ids) - 3
    assert [i for i in doomed if i in after_ids] == []
    assert after_ids[: len(before_ids) - 3] == before_ids[: len(before_ids) - 3]

    # ⚠ **不是**「成员表逐字相同」—— 写入器会把游戏格式要求的那个成员**补齐**
    #   （单数的 PlaneFlyPreset.json；kb/12 记的就是这条：产品必须含它）。
    #   真正要钉的是两件：① 除那一个以外**不许**多出任何成员；
    #   ② 源里已有的成员，**除目标表外逐字节不变**。
    with zipfile.ZipFile(SOURCE) as a, zipfile.ZipFile(out) as b:
        extra = sorted(set(b.namelist()) - set(a.namelist()))
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
    assert extra == ["PlaneFlyPreset.json"], f"多出来的成员不止那一个：{extra}"
    assert changed == ["Units.json"], f"只许 Units.json 变，实际变了 {changed}"


def test_the_reverse_delta_carries_the_whole_original_rows(tmp_path: Path) -> None:
    """★ 可还原：反向 Δ 里必须是**整行原值**（字段一个不少），不是只有一个 Id。"""
    out = tmp_path / "clean.zip"
    doomed = _ids(SOURCE)[-3:]
    args = _args(SOURCE, "Units", doomed, out=out, write=True, refs=row_delete.FORCE)
    assert row_delete.run(args) == 0
    delta = json.loads(Path(str(out) + ".row-delete-delta.json").read_text(encoding="utf-8"))
    assert [t["table"] for t in delta["tables"]] == ["Units"]
    rows = delta["tables"][0]["rows"]
    assert sorted(r["Id"] for r in rows) == sorted(doomed)
    with zipfile.ZipFile(SOURCE) as z:
        original = {int(r["Id"]): r for r in json.loads(z.read("Units.json"))}
    for item in rows:
        assert item["values"] == original[item["Id"]], "反向 Δ 里的行不是原值"
    assert len(rows[0]["values"]) > 20, "字段数太少，不像整行"


def test_the_member_check_can_go_red(tmp_path: Path) -> None:
    """★ 正控：「除目标表外逐字节不变」这把尺子**能红** —— 故意多写一个成员上去。"""
    out = tmp_path / "clean.zip"
    doomed = _ids(SOURCE)[-1:]
    args = _args(SOURCE, "Units", doomed, out=out, write=True, refs=row_delete.FORCE)
    assert row_delete.run(args) == 0
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("Extra.json", b"[]")
    with zipfile.ZipFile(SOURCE) as a, zipfile.ZipFile(tampered) as b:
        changed = [n for n in a.namelist() if n not in b.namelist() or a.read(n) != b.read(n)]
        assert sorted(a.namelist()) != sorted(b.namelist()), "尺子看不见多出来的成员"
        assert changed, changed


# --------------------------------------------------------------------------- #
# 新增：引用闸（G1）· 游戏在跑那道闸（G4）· --out 与原地写
# --------------------------------------------------------------------------- #


def test_refuse_is_the_default_and_blocks_a_referenced_row(tmp_path: Path, capsys) -> None:
    """★ **--refuse 必须是默认**：被引用的行 ⇒ 退出 2、零写入，且把那几行印出来。"""
    table, row_id, refs = REFERRED
    out = tmp_path / "never.zip"
    delta = tmp_path / "never.json"
    args = _args(SOURCE, table, [row_id], out=out, write=True, delta_out=delta)
    del args.refs  # ⛔ 连属性都不给：默认档必须自己兜底（老调用方就是这样）
    assert row_delete.run(args) == 2
    assert not out.exists(), "--refuse 居然写了盘"
    assert not delta.exists(), "--refuse 居然留了 Δ"
    printed = capsys.readouterr().out
    assert "引用它的行" in printed, "拒之前必须先把「谁引用它」说清楚"
    assert str(refs) in printed


def test_reference_ruler_positive_control_matches_the_raw_json() -> None:
    """★ 尺子正控：Specializations#3 报 32 条，且**逐条回读原始 JSON 校验**（不许编）。"""
    from ba_mod_maker.cli.source import load
    from ba_mod_maker.core.edit.refs import orphan_rows

    table, row_id, expected = REFERRED
    with load(str(SOURCE)) as loaded:
        hits = orphan_rows(loaded.instance, [(table, row_id)])
    assert len(hits) == expected, f"正控读数变了：{len(hits)}（应 {expected}）"
    raw = {int(r["Id"]): r for r in _rows(SOURCE, "SpecializationAvailabilities")}
    for hit in hits:
        assert hit.table == "SpecializationAvailabilities"
        assert hit.column == "SpecializationId"
        assert raw[hit.row_id]["SpecializationId"] == row_id, "报了一条并不指向它的行"


def test_reference_ruler_negative_control_reports_nothing() -> None:
    """★ 尺子负控：一个**没有任何引用**的行必须报空（⛔ 否则「有引用就拒」永远拒）。"""
    from ba_mod_maker.cli.source import load
    from ba_mod_maker.core.edit.refs import orphan_rows, reference_closure

    with load(str(SOURCE)) as loaded:
        assert orphan_rows(loaded.instance, [LONELY]) == ()
        assert reference_closure(loaded.instance, [LONELY]) == ()


def test_the_old_value_matching_ruler_is_a_different_quantity() -> None:
    """★ 废尺子正控：上一轮那把「整数值命中 Id」**不是** referencing_rows。

    上一轮拿它算闭包、只匹配到 6 行（那是现场 zip 里 Units 的 Id 上限，纯巧合）。
    这条用例把两者的差**机械地**钉住：naive 集合 ⊋ 真引用者集合 ——
    它连「哪一列算外键」都不知道，所以多报一大片。⛔ 别再把它当尺子。
    """
    from ba_mod_maker.cli.source import load
    from ba_mod_maker.core.edit.refs import orphan_rows

    table, row_id, _ = REFERRED
    with load(str(SOURCE)) as loaded:
        true = {(h.table, h.row_id) for h in orphan_rows(loaded.instance, [(table, row_id)])}
    naive: set[tuple[str, int]] = set()
    with zipfile.ZipFile(SOURCE) as z:
        for name in z.namelist():
            for row in json.loads(z.read(name)):
                if any(
                    isinstance(v, int) and not isinstance(v, bool) and v == row_id
                    for v in row.values()
                ):
                    naive.add((name.removesuffix(".json"), int(row["Id"])))
    assert true <= naive, "真引用者当然也在 naive 里"
    assert naive - true, "两个量法撞在一起了 —— 那这条废尺子的记录就白写了"


def test_cascade_goes_one_level_deeper_than_direct_references(tmp_path: Path) -> None:
    """★ 级联：删 1 行 ⇒ 实际删 1 + 闭包 43 行（直接引用者只有 38）。

    ⛔ 这条用例是「只做一层」那个实现的守门人：直接 38 != 闭包 43，
    多出来的 5 行是 Options（它们指的是这个单位的 Modifications，而 Modifications 指的是它）。
    """
    table, row_id, direct, closure = DEEP
    assert closure > direct, "直接引用者与闭包撞在一起 —— 这条用例就抓不到「只做一层」的实现"
    out = tmp_path / "casc.zip"
    delta = tmp_path / "casc.json"
    args = _args(
        SOURCE, table, [row_id], out=out, write=True, refs=row_delete.CASCADE, delta_out=delta
    )
    assert row_delete.run(args) == 0
    doc = json.loads(delta.read_text(encoding="utf-8"))
    total = sum(len(t["rows"]) for t in doc["tables"])
    assert total == 1 + closure, f"级联删了 {total} 行，应为 {1 + closure}"
    assert len(doc["tables"]) > 1, "级联怎么可能只动了一张表"
    for entry in doc["tables"]:
        before = len(_rows(SOURCE, entry["table"]))
        assert len(_rows(out, entry["table"])) == before - len(entry["rows"]), entry["table"]
    # 点名的行确实没了；被牵连的那张表也少了对得上的条数
    assert row_id not in _ids(out)
    assert len(_rows(out, "Options")) == len(_rows(SOURCE, "Options")) - 5


def test_force_deletes_only_the_named_row_and_lists_the_orphans(tmp_path: Path, capsys) -> None:
    """★ --force：只删点名的那一行，**不连子行**；并把会变孤儿的行逐条印出来。"""
    table, row_id, direct, _ = DEEP
    out = tmp_path / "force.zip"
    args = _args(SOURCE, table, [row_id], out=out, write=True, refs=row_delete.FORCE)
    assert row_delete.run(args) == 0
    assert len(_ids(out)) == len(_ids(SOURCE)) - 1
    assert len(_rows(out, "TurretUnits")) == len(_rows(SOURCE, "TurretUnits")), (
        "--force 不许连子行一起删"
    )
    printed = capsys.readouterr().out
    assert "成孤儿" in printed, "--force 必须把会变孤儿的行说清楚"
    assert str(direct) in printed
    assert "TurretUnits#" in printed, "逐条列出来 —— 光有个总数不算"


def test_the_running_game_gate_refuses_and_can_go_red(tmp_path: Path, monkeypatch) -> None:
    """★ G4：游戏在跑 ⇒ 退出 2、零写入；**正控**给 --allow-running 就放行。

    没有正控的话，「拒了」这件事可能只是别的地方坏了（AGENTS §0.9 动作 2）。
    """
    lonely = LONELY[1]
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: True)
    out = tmp_path / "running.zip"
    assert row_delete.run(_args(SOURCE, "Specializations", [lonely], out=out, write=True)) == 2
    assert not out.exists(), "游戏在跑还是写了盘"
    # 正控：闸是这道闸，不是别的错
    assert row_delete.run(
        _args(SOURCE, "Specializations", [lonely], out=out, write=True, allow_running=True)
    ) == 0
    assert out.is_file()
    # 负控：游戏没在跑时不该拦
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: False)
    out2 = tmp_path / "idle.zip"
    assert row_delete.run(_args(SOURCE, "Specializations", [lonely], out=out2, write=True)) == 0
    assert out2.is_file()


def test_out_leaves_the_source_alone_and_in_place_writes_back(tmp_path: Path) -> None:
    """★ 两种落点的区别：给 --out 源一个字节不动；不给就写回**打开的那个文件**。"""
    lonely = LONELY[1]
    before_src = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    out = tmp_path / "elsewhere.zip"
    assert row_delete.run(_args(SOURCE, "Specializations", [lonely], out=out, write=True)) == 0
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before_src, "--out 居然动了源"
    assert lonely not in _ids(out, "Specializations.json")

    copy = tmp_path / "copy.zip"
    copy.write_bytes(SOURCE.read_bytes())
    before_copy = hashlib.sha256(copy.read_bytes()).hexdigest()
    assert row_delete.run(_args(copy, "Specializations", [lonely], write=True)) == 0
    assert hashlib.sha256(copy.read_bytes()).hexdigest() != before_copy, "原地写没落盘"
    assert lonely not in _ids(copy, "Specializations.json")


def test_the_delta_is_v2_and_carries_the_original_row_index(tmp_path: Path) -> None:
    """★ Δ v2：tables 分表、每行带 index（**原表下标**）—— row-restore 逐字节还原靠它。"""
    out = tmp_path / "clean.zip"
    delta = tmp_path / "d.json"
    doomed = _ids(SOURCE)[-3:]
    args = _args(
        SOURCE, "Units", doomed, out=out, write=True, refs=row_delete.FORCE, delta_out=delta
    )
    assert row_delete.run(args) == 0
    doc = json.loads(delta.read_text(encoding="utf-8"))
    assert doc["kind"] == "row-delete-delta"
    assert doc["version"] == 2
    assert doc["mode"] == "force"
    assert [t["table"] for t in doc["tables"]] == ["Units"]
    rows = doc["tables"][0]["rows"]
    assert [r["Id"] for r in rows] == sorted(doomed)
    assert [r["index"] for r in rows] == sorted(r["index"] for r in rows), "必须按下标升序"
    original = _rows(SOURCE, "Units")
    for item in rows:
        assert original[item["index"]]["Id"] == item["Id"], "index 不是它原来的下标"
        assert item["values"] == original[item["index"]]
