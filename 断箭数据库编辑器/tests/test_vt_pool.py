r"""VT 池子的**装 / 卸 / 查**（`asset_kernel.vt_pool` ＋ `cli/vt.py` 的 `pool` 那一支）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_vt_pool.py -q`
-> 收集用例数 **从 0（本文件之前不存在）变 N**，且全 passed。

| 用例 | task-3 的判据 |
|---|---|
| `test_p1_*` | **P1** 干跑 0 字节 → `--apply` 后 5 个文件**逐个 sha256 == 源**；`status` 报就位 |
| `test_p2_*` | **P2** 可撤销：`remove` 之后我们加的**一个不剩**；别人的文件不动 |
| `test_p3_*` | **P3 正控**：改 1 个字节 ⇒ **拒绝删**，并印出文件名 ＋ 期望/实际 sha256 |
| `test_p4_*` | **P4** 幂等（sha 相同连 mtime 都不动）／sha 不同 ⇒ 拒绝覆盖，`force` 才盖 |
| 负控 | 账本才是删的依据：**没记账的一个都不删**；坏账本 ⇒ **抛**（⛔ 不许当空账本） |

## 为什么不在真游戏目录里验

游戏根本来就能被 `--game` 覆盖（这一条**就是**为它留的），用例一律拿 `tmp_path` 当游戏根 ——
AGENTS 的硬约定：⛔ 不许真写游戏目录（真机上那一次由 Lead 做）。池子的字节是本文件的夹具造的：
判据只认 sha256，不认内容。

## 尺子先做正控

P3 那条（拒绝删）**先喂一个没被改过的进去看它报绿**（`refused == ()` 且 5 个都可删），
再改 1 个字节看它报不报红 —— 否则「拒绝删」可能只是它什么都不肯删（AGENTS §0.9 动作 2）。
sha256 也**由用例自己算**（`hashlib`）：⛔ 不调产品那一个，否则尺子与被量的是同一份实现。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from ba_mod_maker.asset_kernel import vt_pool
from ba_mod_maker.cli import vt as vt_cli
from ba_mod_maker.core.errors import AssetError

#: 真件那个自烘组名（kb/18 §6.2 / `_work/paint-design/vt-install-manifest.json`）——
#: 命名形状照它：`<group>.gts` ＋ `<group>0_1.gtp` ＋ `…2 / …3 / …4.gtp`。
GROUP = "243ba7869bfa79d5ab794e9cc4a8329a"
POOL_NAMES = (f"{GROUP}.gts", f"{GROUP}0_1.gtp", f"{GROUP}2.gtp", f"{GROUP}3.gtp", f"{GROUP}4.gtp")


# --------------------------------------------------------------------------- #
# 夹具：一个池子目录 ＋ 一个"游戏根"（全是 tmp_path，⛔ 不碰真游戏）
# --------------------------------------------------------------------------- #
def _body(name: str, size: int) -> bytes:
    """夹具字节：按名字派生、可复算（改一个字节就必须被抓到）。"""
    unit = hashlib.sha256(name.encode("utf-8")).digest()
    return (unit * (size // len(unit) + 1))[:size]


def _pool(tmp_path: Path) -> Path:
    """5 个池子文件（1 个 .gts ＋ 4 个 .gtp），形状照 kb/18 §6.2 的真件。"""
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / POOL_NAMES[0]).write_bytes(_body(POOL_NAMES[0], 65536))
    for index, name in enumerate(POOL_NAMES[1:], start=2):
        (pool / name).write_bytes(_body(name, index * 4096))
    return pool


def _game(tmp_path: Path, *, make_dir: bool = True) -> tuple[Path, Path]:
    """游戏根 ＋ 它的 StreamingAssets（`make_dir=False` 用来验"干跑连目录都不建"）。"""
    game = tmp_path / "game"
    streaming = game / "BrokenArrow_Data" / "StreamingAssets"
    if make_dir:
        streaming.mkdir(parents=True)
    return game, streaming


def _sha(path: Path) -> str:
    """**用例自己的** sha256（⛔ 不调产品那一个 —— 尺子不能与被量的是同一份实现）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _flip_one_byte(path: Path) -> None:
    """改**恰好一个字节**（长度不变）—— P3 最阴的那一种改法（大小这一档必须放过它）。"""
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))


def _parser() -> argparse.ArgumentParser:
    """与 `__main__.build_parser` 同一个形状（只建 `vt` 这一支）。"""
    top = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = top.add_subparsers(dest="command")
    vt_cli.add_parser(sub)
    return top


# --------------------------------------------------------------------------- #
# P1：干跑 0 字节 → 装上 5 个、逐个 sha256 == 源 → status 就位
# --------------------------------------------------------------------------- #
def test_p1_install_lands_five_files_with_the_source_sha256(tmp_path: Path) -> None:
    """P1：干跑期间**一个字节都不写**；`install` 之后 5 个文件逐个 sha256 == 源。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path, make_dir=False)

    plan = vt_pool.plan_install(pool, game)
    assert len(plan.new) == 5
    assert plan.ready == () and plan.conflicts == ()
    assert not streaming.exists(), "干跑连 StreamingAssets 目录都不建"
    assert "新增 5 个" in "\n".join(plan.criterion_lines())

    report = vt_pool.install(plan)
    assert report.added == POOL_NAMES, "5 个都要装上，一个不落"
    assert report.kept == ()
    assert report.manifest_written is True
    got = sorted(p.name for p in streaming.iterdir())
    assert got == sorted([*POOL_NAMES, vt_pool.MANIFEST_NAME])
    for name in POOL_NAMES:
        assert _sha(streaming / name) == _sha(pool / name), f"{name} 与源不是逐字节相同"

    reading = vt_pool.status(game)
    assert reading.ok and reading.missing == ()
    assert [entry.state for entry in reading.entries] == ["就位"] * 5
    assert "5/5 就位" in "\n".join(reading.criterion_lines())


def test_p1_the_rule_goes_red_on_one_changed_byte_at_the_same_size(tmp_path: Path) -> None:
    """P1 的**正控**：同长、只改一个字节 ⇒ 必须报红（大小这一档要放过它，只有 sha 抓得住）。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    vt_pool.install(vt_pool.plan_install(pool, game))

    target = streaming / POOL_NAMES[1]
    _flip_one_byte(target)
    assert target.stat().st_size == (pool / POOL_NAMES[1]).stat().st_size, "正控必须**同长**"

    reading = vt_pool.status(game)
    assert not reading.ok
    assert [entry.name for entry in reading.missing] == [POOL_NAMES[1]]
    assert reading.missing[0].state == "sha 不符"


def test_p1_the_manifest_records_name_size_and_sha256(tmp_path: Path) -> None:
    """P1 的账本：一条三样（name / size / sha256），sha 与源逐字节相同（用例自己算的）。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    vt_pool.install(vt_pool.plan_install(pool, game))

    raw = json.loads((streaming / vt_pool.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert raw["format"] == vt_pool.MANIFEST_FORMAT
    assert [row["name"] for row in raw["added"]] == list(POOL_NAMES)
    for row in raw["added"]:
        assert row["size"] == (pool / row["name"]).stat().st_size
        assert row["sha256"] == _sha(pool / row["name"])


# --------------------------------------------------------------------------- #
# P2：可撤销 —— 只删账本里记着的
# --------------------------------------------------------------------------- #
def test_p2_remove_deletes_exactly_the_recorded_files(tmp_path: Path) -> None:
    """P2：`remove` 删掉那 5 个 ＋ 账本自己；**别人放进来的文件一个不动**。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    vt_pool.install(vt_pool.plan_install(pool, game))
    stranger = streaming / "someone-else.gts"
    stranger.write_bytes(b"not-ours")

    plan = vt_pool.plan_remove(game)
    assert [entry.name for entry in plan.deletable] == list(POOL_NAMES)
    assert plan.refused == () and plan.gone == ()

    report = vt_pool.remove(plan)
    assert report.deleted == POOL_NAMES
    assert report.refused == ()
    assert report.manifest_removed, "账本记的东西一个不剩 ⇒ 账本自己也撤掉"
    assert [p.name for p in streaming.iterdir()] == ["someone-else.gts"], "别人的文件不许动"
    assert stranger.read_bytes() == b"not-ours"


# --------------------------------------------------------------------------- #
# P3：正控 —— 改一个字节必须拒绝删，且两个 sha 都印出来
# --------------------------------------------------------------------------- #
def test_p3_a_modified_file_is_refused_with_both_sha256s(tmp_path: Path) -> None:
    """P3 **正控**：改 1 个字节 ⇒ 拒绝删，并说清哪个文件 ＋ 期望 sha256 ＋ 实际 sha256。

    先喂没改过的那一份看这把尺子**报绿**（`refused == ()`、5 个都可删）——
    否则「拒绝删」可能只是它什么都不肯删（AGENTS §0.9 动作 2）。
    """
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    vt_pool.install(vt_pool.plan_install(pool, game))

    clean = vt_pool.plan_remove(game)
    assert clean.refused == (), "正控：没改过的时候不许报红"
    assert len(clean.deletable) == 5

    target = streaming / POOL_NAMES[1]
    expected = _sha(pool / POOL_NAMES[1])
    _flip_one_byte(target)
    actual = _sha(target)
    assert actual != expected

    plan = vt_pool.plan_remove(game)
    assert [entry.name for entry in plan.refused] == [POOL_NAMES[1]]
    assert len(plan.deletable) == 4, "被改过的**一个都不删**，其余的照删"
    text = "\n".join(plan.criterion_lines())
    assert POOL_NAMES[1] in text, "要说清是哪个文件"
    assert expected in text and actual in text, "期望 sha256 与实际 sha256 都要印出来"

    report = vt_pool.remove(plan)
    assert report.refused == (POOL_NAMES[1],)
    assert len(report.deleted) == 4
    assert target.exists() and _sha(target) == actual, "被改过的那个一个字节都不许动"
    assert not report.manifest_removed, "还有没删掉的 ⇒ 账本留着，可以重跑"
    assert (streaming / vt_pool.MANIFEST_NAME).is_file()

    again = vt_pool.remove(vt_pool.plan_remove(game))
    assert again.refused == (POOL_NAMES[1],) and again.deleted == ()


def test_p3_a_length_change_is_refused_too(tmp_path: Path) -> None:
    """P3 的另一半：**多一个字节**也是「不符」，照样拒绝删，两个 sha256 都印出来。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    vt_pool.install(vt_pool.plan_install(pool, game))

    target = streaming / POOL_NAMES[0]
    target.write_bytes(target.read_bytes() + b"\x00")
    plan = vt_pool.plan_remove(game)
    assert [entry.name for entry in plan.refused] == [POOL_NAMES[0]]
    assert "大小不符" in plan.refused[0].state
    text = "\n".join(plan.criterion_lines())
    assert _sha(target) in text and _sha(pool / POOL_NAMES[0]) in text


# --------------------------------------------------------------------------- #
# P4：幂等 ＋ 不许静默覆盖
# --------------------------------------------------------------------------- #
def test_p4_install_is_idempotent_and_refuses_to_overwrite(tmp_path: Path) -> None:
    """P4：sha 相同 ⇒ 已就位、**连 mtime 都不动**；sha 不同 ⇒ 拒绝覆盖，`force` 才盖。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    vt_pool.install(vt_pool.plan_install(pool, game))
    stamps = {name: (streaming / name).stat().st_mtime_ns for name in POOL_NAMES}

    plan = vt_pool.plan_install(pool, game)
    assert plan.new == () and plan.conflicts == () and len(plan.ready) == 5
    report = vt_pool.install(plan)
    assert report.added == () and len(report.kept) == 5
    assert report.manifest_written is False, "内容没变就不写账本（没有新改动不写盘）"
    assert {name: (streaming / name).stat().st_mtime_ns for name in POOL_NAMES} == stamps, (
        "已就位的不许重写"
    )

    # 同名、**同长**、不同内容 —— 最阴的那一种
    tampered = bytearray((streaming / POOL_NAMES[2]).read_bytes())
    tampered[0] ^= 0xFF
    (streaming / POOL_NAMES[2]).write_bytes(bytes(tampered))
    bad = _sha(streaming / POOL_NAMES[2])
    plan2 = vt_pool.plan_install(pool, game)
    assert [entry.name for entry in plan2.conflicts] == [POOL_NAMES[2]]
    assert plan2.conflicts[0].state == "sha 不符"
    text = "\n".join(plan2.criterion_lines())
    assert POOL_NAMES[2] in text and bad in text and _sha(pool / POOL_NAMES[2]) in text

    with pytest.raises(AssetError, match="拒绝覆盖"):
        vt_pool.install(plan2)
    assert _sha(streaming / POOL_NAMES[2]) == bad, "拒绝覆盖 = 一个字节都不许写"

    forced = vt_pool.install(plan2, force=True)
    assert forced.added == (POOL_NAMES[2],)
    assert _sha(streaming / POOL_NAMES[2]) == _sha(pool / POOL_NAMES[2])
    assert vt_pool.plan_install(pool, game).conflicts == ()


# --------------------------------------------------------------------------- #
# 负控：账本是删的唯一依据；坏账本拒绝读
# --------------------------------------------------------------------------- #
def test_the_manifest_is_the_only_authority_to_delete(tmp_path: Path) -> None:
    """负控：**没记账的文件一个都不删** —— 名字对、大小对都不算数。"""
    game, streaming = _game(tmp_path)
    mine = streaming / POOL_NAMES[0]
    mine.write_bytes(_body(POOL_NAMES[0], 4096))

    plan = vt_pool.plan_remove(game)
    assert plan.manifest is None and plan.entries == ()
    report = vt_pool.remove(plan)
    assert report.deleted == () and report.refused == ()
    assert not report.manifest_removed
    assert mine.is_file(), "账本不在 ⇒ 它不认账，也不动任何文件"
    assert not (streaming / vt_pool.MANIFEST_NAME).exists(), "读这条路不许凭空造账本"


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ("{ 这不是 JSON", "读不开"),
        ('["不是对象"]', "不是一份对象"),
        ('{"format": 99, "added": []}', "格式版本"),
        ('{"format": 1}', "没有 added"),
        ('{"format": 1, "added": ["x"]}', "不是对象"),
        ('{"format": 1, "added": [{"name": "sub/a.gts", "size": 1, "sha256": "' + "0" * 64 + '"}]}',
         "文件名不合法"),
        ('{"format": 1, "added": [{"name": "..", "size": 1, "sha256": "' + "0" * 64 + '"}]}',
         "文件名不合法"),
        ('{"format": 1, "added": [{"name": "a.gts", "size": -1, "sha256": "' + "0" * 64 + '"}]}',
         "size"),
        ('{"format": 1, "added": [{"name": "a.gts", "size": 1, "sha256": "0"}]}', "sha256"),
    ],
)
def test_a_broken_manifest_is_refused_instead_of_read_as_empty(
    tmp_path: Path, payload: str, needle: str
) -> None:
    """负控：坏账本 ⇒ **抛**（⛔ 不许当空账本 —— 那等于「没有可删的」，是最危险的假绿）。"""
    game, streaming = _game(tmp_path)
    (streaming / vt_pool.MANIFEST_NAME).write_text(payload, encoding="utf-8")
    with pytest.raises(AssetError, match=needle):
        vt_pool.plan_remove(game)


# --------------------------------------------------------------------------- #
# CLI：参数面（两条路共用一张表）＋ 退出码
# --------------------------------------------------------------------------- #
def test_the_pool_route_parses_and_still_defaults_to_a_dry_run() -> None:
    """参数面：`vt pool` 三个动作默认**也**是干跑；材质那三条必填**按分支**生效。"""
    top = _parser()
    for action in ("status", "install", "remove"):
        args = top.parse_args(["vt", "pool", action])
        assert args.unit == "pool" and args.action == action
        assert args.apply is False, "池子那条路默认也必须是干跑"
    install = top.parse_args(["vt", "pool", "install", "--pool", "P", "--force"])
    assert install.pool == "P" and install.force is True

    # 正控：同一个 parser 对象上，刚跑过池子那条路之后，材质那条路**照样**要求三条必填
    with pytest.raises(SystemExit):
        top.parse_args(["vt", "RU_T72B"])
    unit = top.parse_args(["vt", "RU_T72B", "--material", "M", "--group", "G", "--item", "I"])
    assert unit.unit == "RU_T72B" and unit.action is None and unit.group == "G"
    with pytest.raises(SystemExit):
        top.parse_args(["vt", "pool", "bogus"])


def test_the_pool_route_refuses_coordinates_it_cannot_use(tmp_path: Path, capsys: Any) -> None:
    """用不了（2）：没给动作 / 没给 --pool / 拿材质路的参数喂池子路 / 拿池子的词喂材质路。"""
    game, _ = _game(tmp_path)
    top = _parser()
    cases = [
        (["vt", "pool"], "后面要跟一个动作"),
        (["vt", "pool", "install", "--game", str(game)], "要 --pool"),
        (["vt", "pool", "status", "--material", "M", "--game", str(game)], "不吃 --material"),
        (["vt", "pool", "status", "--force", "--game", str(game)], "只对"),
        (["vt", "pool", "remove", "--pool", "P", "--game", str(game)], "不吃 --pool"),
        (["vt", "RU_T72B", "remove", "--material", "M", "--group", "G", "--item", "I"],
         "是**池子**那条路的词"),
        (["vt", "RU_T72B", "--material", "M", "--group", "G", "--item", "I", "--force"], "只对"),
    ]
    for argv, needle in cases:
        code = vt_cli.run(top.parse_args(argv))
        captured = capsys.readouterr()
        assert code == vt_cli.EXIT_UNUSABLE, (argv, code)
        assert needle in captured.err, (argv, captured.err)


def test_the_pool_route_runs_end_to_end_on_a_temp_game_root(tmp_path: Path, capsys: Any) -> None:
    """P1/P2 走**真 CLI**：干跑不写 → `--apply` 装上 5 个 → `remove` 卸干净。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    top = _parser()

    dry = vt_cli.run(
        top.parse_args(["vt", "pool", "install", "--pool", str(pool), "--game", str(game)])
    )
    assert dry == vt_cli.EXIT_OK
    assert list(streaming.iterdir()) == [], "干跑一个字节都不写"
    capsys.readouterr()  # 干跑那次的人读文本走 stdout，先倒掉（--json 那一次才会是纯 JSON）

    applied = vt_cli.run(
        top.parse_args(
            ["vt", "pool", "install", "--pool", str(pool), "--game", str(game), "--apply", "--json"]
        )
    )
    document = json.loads(capsys.readouterr().out)
    assert applied == vt_cli.EXIT_OK and document["applied"] is True
    assert [f["ok"] for f in document["files"]] == [True] * 5, "文档说的是**写后核过**的现状"
    assert sorted({f["action"] for f in document["files"]}) == ["add"]
    got = sorted(p.name for p in streaming.iterdir())
    assert got == sorted([*POOL_NAMES, vt_pool.MANIFEST_NAME])

    stat = vt_cli.run(top.parse_args(["vt", "pool", "status", "--game", str(game), "--json"]))
    document = json.loads(capsys.readouterr().out)
    assert stat == vt_cli.EXIT_OK and document["installed"] is True

    removed = vt_cli.run(
        top.parse_args(["vt", "pool", "remove", "--game", str(game), "--apply", "--json"])
    )
    document = json.loads(capsys.readouterr().out)
    assert removed == vt_cli.EXIT_OK and document["manifest_removed"] is True
    assert list(streaming.iterdir()) == []


def test_cli_reports_a_refused_delete_as_red_with_both_shas(
    tmp_path: Path, capsys: Any
) -> None:
    """P3 走真 CLI：退出码 **1**、`ok: false`、red 里点名 ＋ 两个 sha256；文件仍在。"""
    pool = _pool(tmp_path)
    game, streaming = _game(tmp_path)
    top = _parser()
    vt_cli.run(
        top.parse_args(
            ["vt", "pool", "install", "--pool", str(pool), "--game", str(game), "--apply", "--json"]
        )
    )
    capsys.readouterr()

    target = streaming / POOL_NAMES[0]
    expected = _sha(target)
    _flip_one_byte(target)
    actual = _sha(target)

    code = vt_cli.run(
        top.parse_args(["vt", "pool", "remove", "--game", str(game), "--apply", "--json"])
    )
    document = json.loads(capsys.readouterr().out)
    assert code == vt_cli.EXIT_RED
    assert document["ok"] is False
    assert document["counts"]["refused"] == 1
    assert POOL_NAMES[0] in document["red"][0]
    assert expected in document["red"][0] and actual in document["red"][0]
    assert target.exists() and _sha(target) == actual


# --------------------------------------------------------------------------- #
# J2（2026-10-11）：一次装**多套**（一个 group 一套）＋ 缺件时干跑报红 / --apply 拒绝
# --------------------------------------------------------------------------- #
#: 实测的第二套（`_work/vtb1/extreme/pool/`：两颗材质同 shader 同两层，只因 Mask 不同）
OTHER_GROUP = "48bd9cef755752eb77fc1c08e22e7912"
OTHER_NAMES = (
    f"{OTHER_GROUP}.gts",
    f"{OTHER_GROUP}0_1.gtp",
    f"{OTHER_GROUP}2.gtp",
    f"{OTHER_GROUP}3.gtp",
    f"{OTHER_GROUP}4.gtp",
)


def _pool_two(tmp_path: Path) -> Path:
    """**两套**池子放在同一个目录里（一趟烘出两套就是长这样）。"""
    pool = _pool(tmp_path)
    for index, name in enumerate(OTHER_NAMES, start=1):
        (pool / name).write_bytes(_body(name, index * 2048))
    return pool


def test_j2_two_pools_are_installed_in_one_command(tmp_path: Path, capsys: Any) -> None:
    """一次 `install --apply` 装**两套**（10 个文件）；账本逐个记下、每套各有各的账。"""
    pool = _pool_two(tmp_path)
    game, streaming = _game(tmp_path)
    top = _parser()
    code = vt_cli.run(
        top.parse_args(
            ["vt", "pool", "install", "--pool", str(pool), "--game", str(game),
             "--apply", "--json"]
        )
    )
    document = json.loads(capsys.readouterr().out)
    assert code == vt_cli.EXIT_OK
    assert document["applied"] is True
    assert [got["group"] for got in document["groups"]] == sorted([GROUP, OTHER_GROUP])
    assert all(got["ok"] for got in document["groups"])
    assert len(document["files"]) == 10
    assert sorted(path.name for path in streaming.iterdir()) == sorted(
        [*POOL_NAMES, *OTHER_NAMES, vt_pool.MANIFEST_NAME]
    )
    for name in (*POOL_NAMES, *OTHER_NAMES):
        assert _sha(streaming / name) == _sha(pool / name), name


def test_j2_a_group_missing_a_shard_is_red_on_a_dry_run_and_refused_on_apply(
    tmp_path: Path, capsys: Any
) -> None:
    """负控：某一套缺第一片 ⇒ 干跑 rc=1（并点名）· `--apply` rc=2 拒绝，一个字节都不写。"""
    pool = _pool_two(tmp_path)
    (pool / OTHER_NAMES[1]).unlink()          # 第二套缺 0_1（第一片）
    game, streaming = _game(tmp_path)
    top = _parser()

    dry = vt_cli.run(
        top.parse_args(
            ["vt", "pool", "install", "--pool", str(pool), "--game", str(game), "--json"]
        )
    )
    document = json.loads(capsys.readouterr().out)
    assert dry == vt_cli.EXIT_RED
    assert document["ok"] is False
    assert any("第一片" in item for item in document["red"]), document["red"]
    assert list(streaming.iterdir()) == [], "干跑一个字节都不写"

    applied = vt_cli.run(
        top.parse_args(
            ["vt", "pool", "install", "--pool", str(pool), "--game", str(game), "--apply"]
        )
    )
    capsys.readouterr()
    assert applied == vt_cli.EXIT_UNUSABLE
    assert list(streaming.iterdir()) == [], "--apply 也一个字节都没写"



# --------------------------------------------------------------------------- #
# L6 · 页表读数（`.gts` = GRPG v6）：`read_gts` / `gts_sentinel` / `level0_free_rects`
# --------------------------------------------------------------------------- #
# 夹具自己**手写**一份 .gts 的字节（字段位置与真件逐条对齐，见 `_work/l6/README.md`）：
# ⛔ 不用真游戏目录里的文件当夹具 —— 判据只认本文件造的字节。
GTS_MAGIC = b"GRPG"
GTS_VERSION = 6
SENTINEL = 0xFFFFFFFF
#: 夹具里的分片 id（16 B）—— 盘上那个 `.gtp` 的头 8..24 必须逐字节等于它。
SHARD_ID = bytes(range(16))


def _gts_bytes(
    *,
    magic: bytes = GTS_MAGIC,
    version: int = GTS_VERSION,
    layers: int = 1,
    grids: tuple[tuple[int, int], ...] = ((2, 2), (1, 1)),
    level_slots: tuple[tuple[int, ...], ...] = ((SENTINEL, 5, 6, 7), (8,)),
    shard_size: int = 4096,
    shard_id: bytes = SHARD_ID,
    num_page_files: int = 1,
) -> bytes:
    """造一份**最小但结构合法**的 .gts：头 192 B ＋ 各级瓦片数组 ＋ flat ＋ rev ＋ 描述符表。"""
    header = bytearray(192)
    header[0:4] = magic
    struct.pack_into("<I", header, 4, version)
    header[0x0C:0x1C] = bytes(16)
    struct.pack_into("<I", header, 0x1C, layers)
    struct.pack_into("<Q", header, 0x20, 0x40)  # layersOff（真件里指向层表；本夹具不用）
    struct.pack_into("<I", header, 0x28, len(grids))
    struct.pack_into("<Q", header, 0x2C, 192)  # levelsOff —— 级别表紧跟 192 B 的头之后
    struct.pack_into("<I", header, 0x34, 144)
    struct.pack_into("<I", header, 0x38, 144)
    struct.pack_into("<I", header, 0x3C, 8)
    struct.pack_into("<I", header, 0x40, 20736)
    num_flat = sum(1 for slots in level_slots for value in slots if value != SENTINEL)
    struct.pack_into("<I", header, 0x44, num_flat)
    struct.pack_into("<I", header, 0x80, 1 << 20)
    struct.pack_into("<I", header, 0x84, num_page_files)
    out = bytearray(header)
    level_table = bytearray()
    arrays = bytearray()
    array_off = 192 + 16 * len(grids)
    for index, (nx, ny) in enumerate(grids):
        slots = level_slots[index]
        assert len(slots) == layers * nx * ny, "夹具自证：槽数与网格×层数不符"
        level_table += struct.pack("<4I", nx, ny, array_off, 0)
        arrays += struct.pack(f"<{len(slots)}I", *slots)
        array_off += 4 * len(slots)
    out += level_table
    out += arrays
    struct.pack_into("<Q", out, 0x48, len(out))  # flatOff = 现在的位置
    out += struct.pack(f"<{num_flat}I", *range(num_flat))
    struct.pack_into("<Q", out, 0x5C, len(out))  # revOff
    struct.pack_into("<I", out, 0x58, num_flat)
    out += struct.pack(f"<{num_flat}I", *range(num_flat))
    pdb = len(out)
    struct.pack_into("<I", out, 0x88, pdb)
    for _index in range(num_page_files):
        entry = bytearray(544)
        name = GROUP.encode("utf-16-le")
        entry[0 : len(name)] = name
        struct.pack_into("<I", entry, 512, 1)
        entry[516:532] = shard_id
        struct.pack_into("<I", entry, 532, 2)
        struct.pack_into("<Q", entry, 536, shard_size)
        out += entry
    return bytes(out)


def _gtp_bytes(size: int, *, file_id: bytes = SHARD_ID, magic: bytes = b"GRAP") -> bytes:
    """一个形状合法的 `.gtp` 头（24 B）＋ 填充 —— 只为了让描述符能对上。"""
    head = bytearray(24)
    head[0:4] = magic
    head[4:8] = (4).to_bytes(4, "little")
    head[8:24] = file_id
    return bytes(head) + bytes(max(0, size - 24))


def _l6_dir(tmp_path: Path, **kwargs: Any) -> Path:
    """一个目录：一份夹具 .gts ＋ 一个与它描述符对得上的 .gtp。"""
    directory = tmp_path / "sa"
    directory.mkdir(exist_ok=True)
    (directory / f"{GROUP}.gts").write_bytes(_gts_bytes(**kwargs))
    size = kwargs.get("shard_size", 4096)
    file_id = kwargs.get("shard_id", SHARD_ID)
    (directory / f"{GROUP}0_1.gtp").write_bytes(_gtp_bytes(size, file_id=file_id))
    return directory


def test_read_gts_reads_a_synthetic_table(tmp_path: Path) -> None:
    """正控：夹具那份 .gts 读出来 —— 层/级/地址空间/非空数**逐条对上**，且 numFlat == Σ非空。"""
    directory = _l6_dir(tmp_path)
    table = vt_pool.read_gts(directory / f"{GROUP}.gts")
    assert table.num_layers == 1
    assert table.num_levels == 2
    assert table.content == 128  # 144 - 2×8
    assert table.box_pages == (2, 2)
    assert table.box_px == (256, 256)
    assert table.levels[0].nonempty == 3
    assert table.levels[1].nonempty == 1
    assert table.nonempty_total == 4
    assert table.num_flat == table.nonempty_total  # 结构自证
    assert [d.size for d in table.descriptors] == [4096]
    assert table.descriptors[0].file_id == SHARD_ID.hex()


def test_read_gts_refuses_a_bad_magic(tmp_path: Path) -> None:
    """负控：魔数改坏 ⇒ 抛（⛔ 不许读成「0 个 item」）。"""
    directory = _l6_dir(tmp_path, magic=b"XXXX")
    with pytest.raises(AssetError, match="魔数"):
        vt_pool.read_gts(directory / f"{GROUP}.gts")


def test_read_gts_refuses_an_unknown_version(tmp_path: Path) -> None:
    """负控：版本号 99 ⇒ 抛（不猜新布局）。"""
    directory = _l6_dir(tmp_path, version=99)
    with pytest.raises(AssetError, match="版本"):
        vt_pool.read_gts(directory / f"{GROUP}.gts")


def test_read_gts_refuses_a_truncated_file(tmp_path: Path) -> None:
    """负控：截断到 512 B ⇒ 抛（级别表/瓦片数组越界）。"""
    directory = _l6_dir(tmp_path)
    path = directory / f"{GROUP}.gts"
    path.write_bytes(path.read_bytes()[:512])
    with pytest.raises(AssetError, match="越界|越出"):
        vt_pool.read_gts(path)


def test_read_gts_refuses_a_level_grid_that_does_not_halve(tmp_path: Path) -> None:
    """负控：级别网格不是上一级的一半 ⇒ 抛（抓「头读对了但表读错位」）。"""
    directory = _l6_dir(
        tmp_path,
        grids=((2, 2), (3, 3)),
        level_slots=((SENTINEL, 5, 6, 7), (1, 2, 3, 4, 5, 6, 7, 8, 9)),
    )
    with pytest.raises(AssetError, match="一半"):
        vt_pool.read_gts(directory / f"{GROUP}.gts")


def test_gts_sentinel_counts_the_ffffffff_u32s(tmp_path: Path) -> None:
    """哨兵尺子：夹具里恰好一个 `FF FF FF FF` ⇒ 读数 1 / (字节数/4)。"""
    directory = _l6_dir(tmp_path)
    path = directory / f"{GROUP}.gts"
    reading = vt_pool.gts_sentinel(path)
    assert reading.sentinel == 1
    assert reading.entries == path.stat().st_size // 4
    assert 0 < reading.ratio < 0.01


def test_level0_free_rects_finds_the_one_hole(tmp_path: Path) -> None:
    """全空矩形：level0 = [空,占用,占用,占用] ⇒ 最大全空矩形 1×1 页 @(0,0)。"""
    directory = _l6_dir(tmp_path, level_slots=((SENTINEL, 5, 6, 7), (8,)))
    table = vt_pool.read_gts(directory / f"{GROUP}.gts")
    rects = vt_pool.level0_free_rects(table)
    assert rects == (vt_pool.FreeRect(x=0, y=0, w=1, h=1, content=128),)
    assert rects[0].px == (128, 128)


def test_level0_free_rects_is_empty_when_the_grid_is_full(tmp_path: Path) -> None:
    """负控：一格都不空 ⇒ **没有**矩形（与「没算」是两回事，读数必须分开）。"""
    directory = _l6_dir(tmp_path, level_slots=((1, 2, 3, 4), (5,)))
    table = vt_pool.read_gts(directory / f"{GROUP}.gts")
    assert vt_pool.level0_free_rects(table) == ()


def test_descriptor_lines_verify_the_shard_on_disk(tmp_path: Path) -> None:
    """分片对账：id＋字节数都对 ⇒ ✅；字节数不符 ⇒ ❌ 并把两边都印出来。"""
    directory = _l6_dir(tmp_path)
    table = vt_pool.read_gts(directory / f"{GROUP}.gts")
    good = vt_pool.descriptor_lines(table, vt_pool.gtp_shards(directory))
    assert len(good) == 1
    assert good[0].startswith("  ✅")
    shard = directory / f"{GROUP}0_1.gtp"
    shard.write_bytes(_gtp_bytes(2048))  # 头不变、字节数变了
    bad = vt_pool.descriptor_lines(table, vt_pool.gtp_shards(directory))
    assert len(bad) == 1
    assert bad[0].startswith("  ❌")
    assert "4,096" in bad[0]
    assert "2,048" in bad[0]


def test_gtp_shards_refuse_a_wrong_magic(tmp_path: Path) -> None:
    """负控：`.gtp` 的魔数不是 GRAP ⇒ 抛（别把 .gts 读成 .gtp）。"""
    directory = _l6_dir(tmp_path)
    (directory / f"{GROUP}0_1.gtp").write_bytes(_gtp_bytes(4096, magic=b"GRPG"))
    with pytest.raises(AssetError, match="魔数"):
        vt_pool.gtp_shards(directory)


def test_pool_stats_reads_every_gts_in_a_directory(tmp_path: Path) -> None:
    """`pool_stats` 走目录：逐组读数 ＋ 哨兵 ＋ 分片对账，全都**只读**。"""
    directory = _l6_dir(tmp_path)
    stats = vt_pool.pool_stats(directory)
    assert len(stats.tables) == 1
    assert stats.sentinels[0].sentinel == 1
    lines = stats.criterion_lines()
    assert any("哨兵 1 /" in line for line in lines)
    assert any(line.startswith("  ✅ 分片[0]") for line in lines)
    assert any("最大全空矩形 1×1 页" in line for line in lines)


def test_the_stats_route_is_read_only_and_refuses_apply(tmp_path: Path, capsys: Any) -> None:
    """CLI：`vt pool stats` 只读（rc=0）＋ `--apply` 被拒（这一档不写盘）。"""
    game, streaming = _game(tmp_path)
    directory = _l6_dir(tmp_path)
    for name in (f"{GROUP}.gts", f"{GROUP}0_1.gtp"):
        (streaming / name).write_bytes((directory / name).read_bytes())
    top = _parser()
    code = vt_cli.run(top.parse_args(["vt", "pool", "stats", "--game", str(game)]))
    captured = capsys.readouterr()
    assert code == vt_cli.EXIT_OK, captured.err
    assert "VT 页表 · 读" in captured.out
    before = sorted(p.name for p in streaming.iterdir())
    refused = vt_cli.run(
        top.parse_args(["vt", "pool", "stats", "--game", str(game), "--apply"])
    )
    capsys.readouterr()
    assert refused == vt_cli.EXIT_UNUSABLE
    assert sorted(p.name for p in streaming.iterdir()) == before, "读数那一档不许写盘"


