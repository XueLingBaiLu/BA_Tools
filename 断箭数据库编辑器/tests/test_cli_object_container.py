r"""`object container add|remove` 的机械判据（**地址级容器条目**，产品 CLI）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_cli_object_container.py -q -n 0`
-> 通过数**从 0 收集**（本文件新建前不存在）**变 N passed**。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 解析器形状：`container` 挂在 `object` 组下；只有 add / remove；老三动词照旧 | 无顶替入口 |
| B | **J3** 文案尺子：帮助面取得到那个记号、禁词 0 命中 | 0 |
| C | 干跑：不带 `--write` -> 包**逐字节不变**；`--dry-run` ⊕ `--write` 同给 ⇒ rc 2 | 全过 |
| D | 真写：`--write` -> 容器读回得到 -> **J6** 反向 Δ 还原 ⇒ 整文件 sha256 == 改前 | 相等 |
| E | 负控：条目已存在 / 资产不在包里 / 撞名 / 地址不在容器里 / 非 AssetBundle | 全 rc 2 零改动 |
| F | 出口：`--json` 只写一份文档；包打不开 / 动作不认识 ⇒ rc 2 | 全过 |
| G | **尺子正控**：写后复核坏了 ⇒ 这一条**必须**落 rc 1（不能红的尺子不算尺子） | 能红 |
| H | **分派**：`dispatch()` 把 container 转本模块，其余原样转 `object_edit.run` | 两边都对 |

夹具：合成件从 `tests/test_prefab_skins.py` 的 `_synth_blob()` **import 过来**（⛔ 不造第二份）——
它的 `AssetBundle.m_Container` 是**空的**、预载表 3 格，正是这一组最干净的起点。

⛔ 本文件**不查** `cli/__main__.py`（接线归 Lead）：解析器按 Lead 将要接的**同一种形状**
自己搭一份（`object_edit.add_parser(sub)` 先、`oc.add_parser(sub)` 后）。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import prefab_skins as ps
from ba_mod_maker.asset_kernel import renderer_slots as rs
from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.cli import object_container as oc
from ba_mod_maker.cli import object_edit
from ba_mod_maker.core.errors import AssetError
from test_no_swap_wording import banned_hits, iter_help_texts
from test_prefab_skins import (
    AB_PID,
    MAT_A,
    MAT_B,
    UNITS_META,
    UROOT,
    _sha256,
    _synth_blob,
)

#: `container` 子命令短帮助里那一句（「新入口进了 J3 尺子量程」的记号）。
CONTAINER_HELP_MARK = "地址级容器条目：AssetBundle.m_Container"
#: 两条用于守卫的地址（前者成功、后者与它**撞名**）。
CONTAINER_PATH = "Assets/Synth/Shared.prefab"
CLASH_PATH = "Assets/Other/Shared2.prefab"


# --------------------------------------------------------------------------- #
# 夹具（与 test_cli_material_edit.py 同一份合成件）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件抽件产物）")
    path = tmp_path_factory.mktemp("object_container") / "synth.bundle"
    path.write_bytes(_synth_blob())
    return path


@pytest.fixture
def case(synth: Path, tmp_path: Path) -> Path:
    path = tmp_path / "case.bundle"
    shutil.copy2(synth, path)
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    return project


def _parser() -> argparse.ArgumentParser:
    """与 `__main__.py` 将要接的**同一种形状**：object 先、container 后。"""
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    object_edit.add_parser(sub)
    oc.add_parser(sub)
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(list(argv))


def _all_names(parser: argparse.ArgumentParser) -> list[str]:
    """递归收所有动作名与选项名（子命令里还能挂子命令 —— 本组是三层）。"""
    names: list[str] = []
    for action in parser._actions:
        names.extend(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                names.append(str(name))
                names.extend(_all_names(child))
    return names


def _run(
    case: Path, project: Path, tail: tuple[str, ...], *, write: bool = False, **flags: object
) -> int:
    argv = ["object", "container", *tail, "--bundle", str(case), "--project-root", str(project)]
    if write:
        argv.append("--write")
    for flag, value in flags.items():
        if value:
            argv.append("--" + flag.replace("_", "-"))
    return oc.run(_parse(*argv))


def _paths(case: Path) -> tuple[str, ...]:
    return rs.container_paths(ps.read_package_object(case, AB_PID).tree)


# --------------------------------------------------------------------------- #
# A · 解析器形状（挂在 object 组下 ＋ 无顶替式入口）
# --------------------------------------------------------------------------- #
def test_a_container_is_nested_under_the_object_group() -> None:
    args = _parse(
        "object", "container", "add", "--bundle", "b", "--path-id", "7",
        "--asset-path", "Assets/X.p",
    )
    assert args.command == "object" and args.object_action == "container"
    assert args.container_action == "add" and args.path_id == 7
    assert args.container_path_id is None
    drop = _parse("object", "container", "remove", "--bundle", "b", "--asset-path", "Assets/X.p")
    assert drop.container_action == "remove"
    # 老三动词逐字照旧（本模块⛔不改 object_edit）
    old = _parse(
        "object", "set", "--bundle", "b", "--path-id", "1", "--field", "m_Name", "--value", "x"
    )
    assert old.object_action == "set"
    with pytest.raises(SystemExit) as excinfo:
        _parse("object", "container", "swap", "--bundle", "b", "--asset-path", "Assets/X.p")
    assert excinfo.value.code == 2, "容器条目不该有「换」那种动作"


def test_a_container_offers_no_replacement_entry() -> None:
    names = _all_names(_parser())
    banned = [n for n in names if re.search(r"swap|replace|(^|-)to$", n, re.IGNORECASE)]
    assert banned == [], f"object container 上出现了顶替式入口：{banned}"
    assert {"object", "container", "add", "remove"} <= set(names)


# --------------------------------------------------------------------------- #
# B · J3 文案尺子
# --------------------------------------------------------------------------- #
def test_b_container_help_is_visible_to_the_j3_ruler_and_clean() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    assert CONTAINER_HELP_MARK in text, "container 的短帮助没被 iter_help_texts 取到"
    assert banned_hits(text) == [], f"容器条目帮助里有禁词：{banned_hits(text)}"


def test_b_the_two_invocations_are_in_the_help_surface() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    for needle in ("object container add", "object container remove"):
        assert needle in text, f"帮助面里没有 {needle!r}"


# --------------------------------------------------------------------------- #
# C/D · 干跑零字节 · 真写读回 · J6 还原逐位
# --------------------------------------------------------------------------- #
def test_c_the_default_run_writes_nothing(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH))
    assert code == oc.EXIT_DONE, code
    assert _sha256(case) == before, "默认跑法写了盘"
    assert _paths(case) == (), "干跑居然把条目写进去了"


def test_c_explicit_dry_run_writes_nothing_and_makes_no_patch(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project,
        ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH),
        dry_run=True,
    )
    assert code == oc.EXIT_DONE, code
    assert _sha256(case) == before
    assert not (project / "patches").exists(), "--dry-run 却建了补丁柜"


def test_c_dry_run_and_write_are_mutually_exclusive(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH),
        dry_run=True, write=True,
    )
    assert code == oc.EXIT_UNUSABLE, code
    assert _sha256(case) == before


def test_d_write_reads_back_and_j6_restores(case: Path, tmp_path: Path) -> None:
    """**J1/J2/J6**：真写落字节 -> 读回得到 -> 反向 Δ 还原 ⇒ 整文件 sha256 == 改前。"""
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH), write=True
    )
    assert code == oc.EXIT_DONE, code
    assert _sha256(case) != before
    assert _paths(case) == (CONTAINER_PATH,)
    tree = ps.read_package_object(case, AB_PID).tree
    assert len(tree[rs.PRELOAD_TABLE_FIELD]) == 4, "预载表没跟着尾部追加一格"
    report = reverse_delta.PatchStore(reverse_delta.patch_store_root(project)).restore()
    assert report.ok, report.lines()
    assert _sha256(case) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_d_add_then_remove_empties_the_container(case: Path, tmp_path: Path) -> None:
    """加一条 -> 再摘掉它 -> 容器读回 0 条（⚠ 字节不回到原样是 Δ 的正常形态）。"""
    project = _project(tmp_path)
    assert _run(
        case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH), write=True
    ) == oc.EXIT_DONE
    assert _paths(case) == (CONTAINER_PATH,)
    code = _run(case, project, ("remove", "--asset-path", CONTAINER_PATH), write=True)
    assert code == oc.EXIT_DONE, code
    assert _paths(case) == ()
    assert len(ps.read_package_object(case, AB_PID).tree[rs.PRELOAD_TABLE_FIELD]) == 4


# --------------------------------------------------------------------------- #
# E · 负控（每条都必须 rc 2 且盘上零改动）
# --------------------------------------------------------------------------- #
def test_e_an_existing_entry_is_refused(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert _run(
        case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH), write=True
    ) == oc.EXIT_DONE
    before = _sha256(case)
    code = _run(case, project, ("add", "--path-id", str(MAT_A), "--asset-path", CONTAINER_PATH))
    assert code == oc.EXIT_UNUSABLE, code
    assert _sha256(case) == before, "守卫不过却改了盘"


def test_e_an_asset_id_outside_the_package_is_refused(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(case, project, ("add", "--path-id", "999999", "--asset-path", CONTAINER_PATH))
    assert code == oc.EXIT_UNUSABLE, code
    assert _sha256(case) == before


def test_e_a_duplicate_basename_is_refused(case: Path, tmp_path: Path) -> None:
    """资产名（地址最后一段）与既有条目撞名 ⇒ 拒绝（LoadAsset 按**文件名**匹配）。"""
    project = _project(tmp_path)
    assert _run(
        case, project, ("add", "--path-id", str(MAT_A), "--asset-path", CLASH_PATH), write=True
    ) == oc.EXIT_DONE
    before = _sha256(case)
    code = _run(
        case, project,
        ("add", "--path-id", str(MAT_B), "--asset-path", "Assets/Mods/My/Shared2.prefab"),
    )
    assert code == oc.EXIT_UNUSABLE, code
    assert _sha256(case) == before


def test_e_removing_an_unknown_address_is_refused(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(case, project, ("remove", "--asset-path", "Assets/Nope.prefab"))
    assert code == oc.EXIT_UNUSABLE, code
    assert _sha256(case) == before


def test_e_a_path_id_that_is_not_an_assetbundle_is_refused(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project,
        ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH,
         "--container-path-id", str(UROOT)),
    )
    assert code == oc.EXIT_UNUSABLE, code
    assert _sha256(case) == before


# --------------------------------------------------------------------------- #
# F · 出口
# --------------------------------------------------------------------------- #
def test_f_json_mode_keeps_stdout_to_one_document(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    code = _run(
        case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH), json=True
    )
    assert code == oc.EXIT_DONE, code
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["group"] == "container" and document["verb"] == "add"
    assert document["count_before"] == 0 and document["count_after"] == 1
    assert document["paths_after"] == [CONTAINER_PATH]
    assert document["container_path_id"] == AB_PID
    assert document["dry_run"] is True and document["patch"] is None


def test_f_a_missing_bundle_exits_unusable(tmp_path: Path) -> None:
    code = oc.run(_parse(
        "object", "container", "remove", "--bundle", str(tmp_path / "nope.bundle"),
        "--asset-path", CONTAINER_PATH,
    ))
    assert code == oc.EXIT_UNUSABLE, code


def test_f_an_unknown_verb_exits_unusable(case: Path) -> None:
    args = _parse(
        "object", "container", "add", "--bundle", str(case), "--path-id", str(UROOT),
        "--asset-path", CONTAINER_PATH,
    )
    args.container_action = "swap"
    assert oc.run(args) == oc.EXIT_UNUSABLE


# --------------------------------------------------------------------------- #
# G · 尺子正控：rc 1 那一档必须能红
# --------------------------------------------------------------------------- #
def test_g_a_failed_readback_lands_on_exit_dirty(
    case: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)

    def boom(plan: object) -> tuple[str, ...]:
        raise AssetError("写后读回不对（正控）")

    monkeypatch.setattr(oc.renderer_slots, "verify_plan", boom)
    code = _run(
        case, project, ("add", "--path-id", str(UROOT), "--asset-path", CONTAINER_PATH), write=True
    )
    assert code == oc.EXIT_DIRTY, code


# --------------------------------------------------------------------------- #
# H · 分派：container 走本模块，其余原样转 object_edit
# --------------------------------------------------------------------------- #
def test_h_dispatch_routes_the_container_verb_to_this_module(case: Path) -> None:
    args = _parse(
        "object", "container", "remove", "--bundle", str(case), "--asset-path", "Assets/Nope.prefab"
    )
    assert oc.dispatch(args) == oc.EXIT_UNUSABLE


def test_h_dispatch_delegates_the_three_old_verbs(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_run(args: argparse.Namespace) -> int:
        seen["object_action"] = args.object_action
        return 7

    monkeypatch.setattr(oc.object_edit, "run", fake_run)
    args = _parse(
        "object", "set", "--bundle", "b", "--path-id", "1", "--field", "m_Name", "--value", "x"
    )
    assert oc.dispatch(args) == 7
    assert seen["object_action"] == "set"
