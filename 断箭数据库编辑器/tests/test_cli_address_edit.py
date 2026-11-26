r"""地址线判据：`address add` / `address delete`（catalog 的地址条目，产品 CLI）。

**判据一行**：`pytest tests\test_cli_address_edit.py -q -n 0` -> 通过数从 0 变 N passed。

| # | 判据 | 通过线 |
|---|---|---|
| 解析器形状 | 只有 add / delete 两个动作；标识符里没有 swap / replace / --to | 命中数 0 |
| 干跑 | `run()` 不带 `--write` -> catalog **逐字节不变** | sha256 相等 |
| 真写 | `run() --write` -> 新键进 catalog（读侧读得到） | counts +1 / 键在 |
| **J6** | 写完 -> `PatchStore.restore()` -> 整文件 sha256 **== 改前** | 相等 |
| 守卫（能红） | 键已存在 / 键不存在 / 包不在 catalog / resource_type 越界 / 基线被改过 | 五条都抛 |
| J3 量程 | `address` 的 help 落进既有那把「无『换』」尺子，禁词 0 命中 | 0 |

⚠ 合成件那几条借 `tests/test_catalog_edit.py` 的构造器（`_synth` / `_bytes` / 常量）——
**同一份合成件、同一份口径**，⛔ 不另写第二份。真件那几条不用：本文件量的是 CLI 那条链。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.cli import __main__ as cli_main
from ba_mod_maker.cli import address_edit as ae
from ba_mod_maker.core.errors import AssetError
from test_catalog_edit import (
    ADDR_ONE,
    ALPHA,
    _bytes,
    _synth,
)
from test_no_swap_wording import banned_hits, iter_help_texts

#: `address` 子命令短帮助里那一句（「新入口进了 J3 尺子量程」的记号）。
ADDRESS_HELP_MARK = "新增 / 删除 catalog 里的一条资产地址"
NEW_KEY = "addr/cli-new"
NEW_PATH = "Assets/Synth/CliNew.prefab"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    ae.add_parser(sub)
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(list(argv))


def _catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_bytes(_bytes(_synth()))
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# ① 解析器形状 + J3 量程
# --------------------------------------------------------------------------- #
def test_address_has_exactly_the_two_atomic_verbs() -> None:
    assert _parse("address", "add", "--catalog", "c.json", "--key", "k",
                  "--path", "Assets/x.prefab", "--bundle", ALPHA).address_action == "add"
    assert _parse("address", "delete", "--catalog", "c.json",
                  "--key", "k").address_action == "delete"
    with pytest.raises(SystemExit) as excinfo:
        _parse("address", "swap", "--catalog", "c.json")
    assert excinfo.value.code == 2, "地址线不该有「换」那种动作"


def test_address_offers_no_replacement_entry() -> None:
    names: list[str] = []
    for action in _parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                for sub in child._actions:
                    names.extend(sub.option_strings)
                    if isinstance(sub, argparse._SubParsersAction):
                        names.extend(str(k) for k in sub.choices)
    banned = [n for n in names if re.search(r"swap|replace|(^|-)to$", n, re.IGNORECASE)]
    assert banned == [], f"address 上出现了顶替式入口：{banned}"
    assert {"add", "delete"} <= set(names)


def test_address_help_is_visible_to_the_j3_ruler_and_clean() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    assert ADDRESS_HELP_MARK in text, "address 的短帮助没被 iter_help_texts 取到"
    assert banned_hits(text) == [], f"地址线帮助里有禁词：{banned_hits(text)}"


def test_address_is_wired_into_the_product_cli_parser() -> None:
    text = "\n".join(iter_help_texts(cli_main._build_parser()))
    assert ADDRESS_HELP_MARK in text, "address 没进产品 CLI（cli/__main__.py 没接线）"


# --------------------------------------------------------------------------- #
# ② 纯函数计划（合成件）
# --------------------------------------------------------------------------- #
def test_plan_add_inserts_a_key_and_reports_the_placement() -> None:
    before = _bytes(_synth())
    plan = ae.plan_add(before, key=NEW_KEY, asset_path=NEW_PATH, bundle_name=ALPHA)
    assert plan.action == "add" and plan.key == NEW_KEY
    assert plan.counts_before == (6, 6, 5) and plan.counts_after == (7, 7, 6)
    after = ce.CatalogEdit.from_bytes(plan.after)
    assert after.key_index_of(NEW_KEY) is not None, "新键没进 catalog"
    table = after.to_table()
    assert table.entry_count == 6, table.entry_count
    assert plan.byte_delta > 0
    assert plan.is_empty is False


def test_plan_add_refuses_a_key_that_is_already_there() -> None:
    with pytest.raises(AssetError) as excinfo:
        ae.plan_add(_bytes(_synth()), key=ADDR_ONE, asset_path=NEW_PATH, bundle_name=ALPHA)
    assert "已经有这个键" in excinfo.value.message


def test_plan_add_refuses_a_bundle_that_is_not_registered() -> None:
    with pytest.raises(AssetError) as excinfo:
        ae.plan_add(_bytes(_synth()), key=NEW_KEY, asset_path=NEW_PATH,
                    bundle_name="no_such_assets_all_" + "0" * 32 + ".bundle")
    assert "没有这个包" in excinfo.value.message


@pytest.mark.parametrize("bad", [7, -1, 99])
def test_plan_add_refuses_an_unknown_resource_type(bad: int) -> None:
    with pytest.raises(AssetError) as excinfo:
        ae.plan_add(_bytes(_synth()), key=NEW_KEY, asset_path=NEW_PATH,
                    bundle_name=ALPHA, resource_type=bad)
    assert "resource-type" in excinfo.value.message


@pytest.mark.parametrize("field,value", [("key", "  "), ("asset_path", ""), ("bundle_name", "")])
def test_plan_add_refuses_an_empty_field(field: str, value: str) -> None:
    kwargs = {"key": NEW_KEY, "asset_path": NEW_PATH, "bundle_name": ALPHA}
    kwargs[field] = value
    with pytest.raises(AssetError):
        ae.plan_add(_bytes(_synth()), **kwargs)  # type: ignore[arg-type]


def test_plan_delete_removes_the_key() -> None:
    before = _bytes(_synth())
    plan = ae.plan_delete(before, key=ADDR_ONE)
    assert plan.action == "delete"
    assert plan.counts_after[0] == plan.counts_before[0] - 1
    after = ce.CatalogEdit.from_bytes(plan.after)
    assert after.key_index_of(ADDR_ONE) is None, "键没删掉"
    # 共享的那条 internalId 要留着（P1 的口径：别盲删）
    assert len(after.internal_ids) == len(ce.CatalogEdit.from_bytes(before).internal_ids)


def test_plan_delete_refuses_an_unknown_key() -> None:
    with pytest.raises(AssetError) as excinfo:
        ae.plan_delete(_bytes(_synth()), key="no/such")
    assert "没有这个键" in excinfo.value.message


# --------------------------------------------------------------------------- #
# ③ CLI 端到端：干跑不写 / 真写 + J6
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    before = _sha(catalog)
    code = ae.run(_parse("address", "add", "--catalog", str(catalog), "--key", NEW_KEY,
                         "--path", NEW_PATH, "--bundle", ALPHA, "--dry-run",
                         "--project-root", str(tmp_path)))
    assert code == ae.EXIT_DONE, code
    assert _sha(catalog) == before, "干跑动了 catalog"


def test_write_really_writes_and_restores_byte_for_byte(tmp_path: Path) -> None:
    """**J6**：真写 -> 读侧读得到 -> 反向 Δ 还原 -> sha256 == 改前。"""
    catalog = _catalog(tmp_path)
    before = _sha(catalog)
    code = ae.run(_parse("address", "add", "--catalog", str(catalog), "--key", NEW_KEY,
                         "--path", NEW_PATH, "--bundle", ALPHA, "--write",
                         "--allow-running-game",
                         "--project-root", str(tmp_path)))
    assert code == ae.EXIT_DONE, code
    assert _sha(catalog) != before
    edit = ce.CatalogEdit.from_bytes(catalog.read_bytes())
    assert edit.key_index_of(NEW_KEY) is not None, "写完了新键还不在"
    assert edit.self_check() == ()
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path))
    report = store.restore()
    assert report.ok, "\n".join(report.lines())
    assert _sha(catalog) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_write_then_delete_is_identity(tmp_path: Path) -> None:
    """两个原子动作**各自可撤**：加一条 -> 删掉它 -> 与最初逐字节相同。"""
    catalog = _catalog(tmp_path)
    before = _sha(catalog)
    assert ae.run(_parse("address", "add", "--catalog", str(catalog), "--key", NEW_KEY,
                         "--path", NEW_PATH, "--bundle", ALPHA, "--write",
                         "--allow-running-game", "--project-root", str(tmp_path))) == 0
    assert ae.run(_parse("address", "delete", "--catalog", str(catalog), "--key", NEW_KEY,
                         "--write", "--allow-running-game",
                         "--project-root", str(tmp_path))) == 0
    assert _sha(catalog) == before, "加一条再删掉之后应该与最初逐字节相同"


def test_write_catalog_refuses_a_changed_baseline(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    plan = ae.plan_add(catalog.read_bytes(), key=NEW_KEY, asset_path=NEW_PATH, bundle_name=ALPHA)
    # 计划算完之后磁盘被别的动作改过（这里手工模拟）
    catalog.write_bytes(_bytes(_synth()) + b" ")
    with pytest.raises(AssetError) as excinfo:
        ae.write_catalog(plan, catalog_path=catalog, project_root=tmp_path)
    assert "基线不一致" in excinfo.value.message


def test_json_mode_keeps_stdout_to_one_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path)
    code = ae.run(_parse("address", "delete", "--catalog", str(catalog), "--key", ADDR_ONE,
                         "--json"))
    assert code == ae.EXIT_DONE, code
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True and document["action"] == "delete"
    assert document["key"] == ADDR_ONE
    assert document["counts_after"][0] == document["counts_before"][0] - 1


def test_dry_run_and_write_are_mutually_exclusive(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    code = ae.run(_parse("address", "delete", "--catalog", str(catalog), "--key", ADDR_ONE,
                         "--dry-run", "--write"))
    assert code == ae.EXIT_UNUSABLE, code


def test_a_missing_catalog_exits_unusable(tmp_path: Path) -> None:
    code = ae.run(_parse("address", "delete", "--catalog", str(tmp_path / "nope.json"),
                         "--key", ADDR_ONE))
    assert code == ae.EXIT_UNUSABLE, code


def test_the_product_entry_point_dispatches_to_this_module(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    code = cli_main.main(["address", "add", "--catalog", str(catalog), "--key", NEW_KEY,
                          "--path", NEW_PATH, "--bundle", ALPHA, "--dry-run"])
    assert code == 0, code
    assert ce.CatalogEdit.from_bytes(catalog.read_bytes()).key_index_of(NEW_KEY) is None


def test_the_entry_point_refuses_a_verb_this_cut_does_not_have() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main(["address", "swap"])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# ★ 2026-10-11 真机那条（D-13）：依赖声明要整份抄
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_catalog() -> bytes:
    """真件 **JSON** catalog（没装游戏 / 已经是 1.2.1 的 catalog.bin 就 skip）。

    出处：2026-10-13 实测 —— 游戏升到 1.2.1 后 `aa/` 下只剩 `catalog.bin`，下面那条用例直接调
    `ce.CatalogEdit.from_bytes()`（**JSON 专用**）⇒ 会以 `UnicodeDecodeError 0x89` **假红**，
    量的其实是"一份不存在的格式"。二进制那条写侧通道有自己的模块与用例：
    `core/assets/catalog_bin_write.py` + `tests/test_catalog_bin_write.py`。
    """
    # ★ 2026-10-18：改走 `catalog_json_file()` —— 口径只有一份（prod-catalog 在本轮把他处那套
    #   「catalog_file() + 按名字判」也统一到了它）。1.2.1 起游戏只发 catalog.bin ⇒ 这里照旧 skip。
    from ba_mod_maker.core.assets.game import catalog_json_file, find_game_root

    root = find_game_root()
    path = catalog_json_file(root) if root is not None else None
    if path is None or not Path(path).is_file():
        pytest.skip("这台机器的 catalog 不是 JSON 那份（1.2.1 起是 catalog.bin）")
    return Path(path).read_bytes()


def test_add_copies_the_whole_dependency_declaration(real_catalog: bytes) -> None:
    """★ 依赖声明要**整份抄**（entry[2] 依赖键 + entry[3] hash），不能只抄 hash。

    真机读数（2026-10-11 11:43 那局，日志 372/385 行）：
    `Error: Addressable prefab 'MTEST/addr01' was not loaded!` —— 只抄 hash 时依赖键填的是
    「包自己那条键」（桶里只有它自己）⇒ 跨包依赖（vfx / builtinshaders / ammoprefabs …）
    一个都没声明 ⇒ 那颗 prefab 加载不到。

    判据一行：新条的 entry[2] / entry[3] / 依赖桶成员数 **逐项等于**参照条（且参照组 > 1 个包）。
    """
    edit = ce.CatalogEdit.from_bytes(real_catalog)
    src_key = "DLC2_CV9035"
    src = edit.key_index_of(src_key)
    if src is None:
        pytest.skip(f"真件里没有参照键 {src_key}")
    src_row = edit.entries[edit.buckets[src][0]]
    group = edit.buckets[src_row[2]]
    assert len(group) > 1, "参照条的依赖组只有一个包 —— 这条测不出东西，换一条参照"
    member = next(
        e
        for e in group
        if edit.entries[e][1] == 0 and edit.internal_ids[edit.entries[e][0]].endswith(".bundle")
    )
    bundle = edit.internal_ids[edit.entries[member][0]].replace("\\", "/").rsplit("/", 1)[-1]
    plan = ae.plan_add(
        real_catalog,
        key="probe/dep-copy",
        asset_path=edit.internal_ids[src_row[0]],
        bundle_name=bundle,
        dependency_source_key=src_key,
    )
    out = ce.CatalogEdit.from_bytes(plan.after)
    row = out.entries[out.buckets[out.key_index_of("probe/dep-copy")][0]]
    assert row[2] == src_row[2], "依赖键没抄过来 —— 就是真机那条 bug"
    assert row[3] == src_row[3], "依赖 hash 没抄过来"
    assert len(out.buckets[row[2]]) == len(group), "依赖集（桶成员）没抄全"
    assert any("依赖声明抄自" in line for line in plan.lines), plan.lines


def test_add_without_a_reference_copies_the_same_paths_existing_entry(real_catalog: bytes) -> None:
    """不给参照 ⇒ **自动**照抄同路径既有条目那一条的依赖声明（同一个资产不该有两套依赖）。"""
    edit = ce.CatalogEdit.from_bytes(real_catalog)
    src = edit.key_index_of("DLC2_CV9035")
    if src is None:
        pytest.skip("真件里没有 DLC2_CV9035")
    src_row = edit.entries[edit.buckets[src][0]]
    group = edit.buckets[src_row[2]]
    bundle = edit.internal_ids[edit.entries[group[0]][0]].replace("\\", "/").rsplit("/", 1)[-1]
    plan = ae.plan_add(
        real_catalog,
        key="probe/dep-auto",
        asset_path=edit.internal_ids[src_row[0]],
        bundle_name=bundle,
    )
    out = ce.CatalogEdit.from_bytes(plan.after)
    row = out.entries[out.buckets[out.key_index_of("probe/dep-auto")][0]]
    assert row[2] == src_row[2] and row[3] == src_row[3], "自动照抄那条没生效"

