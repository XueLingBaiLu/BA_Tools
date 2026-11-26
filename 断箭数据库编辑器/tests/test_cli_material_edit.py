r"""材质线 CLI 判据：`material skin add|remove|update` / `material slot add|remove`。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_cli_material_edit.py -q -n 0`
-> 通过数**从 0 收集**（本文件新建前不存在）**变 N passed**。
基线正控：`python -m ba_mod_maker.cli material --help` 从 **rc=2 / invalid choice** 变 **rc=0**。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 解析器形状：两组五个原子动词；标识符里没有 swap / replace / --to | 命中数 0 |
| B | **J4** 新命令的 help 落进 J3 尺子量程（`iter_help_texts` 取得到那个记号）且禁词 0 命中 | 0 |
| C | **J1** 五个动词各一条端到端：干跑零字节 -> --write 落字节 -> 读回 -> 反向 Δ 还原 | 全过 |
| D | **J2** 默认跑法一个字节都不写（sha256 不变）；`--dry-run` 与 `--write` 同给 = rc 2 | 全过 |
| E | **J3** 负控：Id 撞 / 渲染器不在 prefab / 删槽引用未处理 / 指向包外 / 空改动 | 全 rc 2 零改动 |
| F | 出口：`--json` 只写一份文档；包打不开 / 动作不认识 / 产品入口转调 | 全过 |
| G | **尺子正控**：把写后复核弄坏 ⇒ 这一条**必须**落 rc 1（不能红的尺子不算尺子） | 能绿能红 |

夹具：合成件从 `tests/test_prefab_skins.py` 的 `_synth_blob()` **import 过来**
（与 `tests/test_renderer_slots.py` 是同一份 —— ⛔ 不在这里再造第二份）。
E 段的"渲染器不在名单"那个现场在正常件里**造不出来**（新增 / 修改都是照抄参照条，名单必然自洽），
所以那一条先**走产品自己的写侧**（`writer.write_delta`）把这份合成件的
`UnitPrefabRoot.Renderers` 摘掉一格 —— 合成件本体还是同一份，不是第二份夹具。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import types
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import prefab as pf
from ba_mod_maker.asset_kernel import prefab_skins as ps
from ba_mod_maker.asset_kernel import renderer_slots as rs
from ba_mod_maker.asset_kernel import reverse_delta, writer
from ba_mod_maker.asset_kernel import unitypy_source as us
from ba_mod_maker.cli import __main__ as cli_main
from ba_mod_maker.cli import material_edit as me
from ba_mod_maker.core.asset import new_asset
from ba_mod_maker.core.errors import AssetError
from test_catalog_edit import ALPHA, SHARED_ASSET, _bytes, _synth
from test_no_swap_wording import banned_hits, iter_help_texts
from test_prefab_skins import (
    MAT_A,
    MAT_B,
    MAT_C,
    NEW_SKIN,
    PREFAB_GO,
    RENDERER_A,
    RENDERER_B,
    SKIN_A,
    SKIN_B,
    UNITS_META,
    UROOT,
    _sha256,
    _synth_blob,
)

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: `material` 子命令短帮助里那一句（「新入口进了 J3 尺子量程」的记号）。
MATERIAL_HELP_MARK = "皮肤表 Skins.Data 与渲染器 m_Materials"
#: 合成件 prefab 的名字（写侧那一趟用它当读数标签）。
PREFAB_NAME = "RU_T72B_Base"
#: `--unit` 用的地址键（合成 catalog 里那条：`addr/one` -> `Assets/Synth/Shared.prefab`）。
UNIT_KEY = "addr/one"
#: 真件抽件（**只读**，不进游戏目录）：routea2real = T-72B 底盘那份 prefab。
REAL_A2 = WORK / "a2" / (
    "routea2real_assets_all_8746119bf64468b9.bundle"
)
REAL_A2_ASSET = "Assets/Resources_moved/ModelPrefabs/RouteA/RouteA2Real.prefab"
#: 真件 `UnitPrefabRoot` 的 pathID（kb/20 §1.6 #7 同一颗）。
REAL_A2_ROOT = 1033192610857091880


# --------------------------------------------------------------------------- #
# 夹具（与 test_renderer_slots.py 同一份合成件、同一套 pathID）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件抽件产物）")
    path = tmp_path_factory.mktemp("cli_material") / "synth.bundle"
    path.write_bytes(_synth_blob())
    return path


@pytest.fixture
def case(synth: Path, tmp_path: Path) -> Path:
    """每个用例一份自己的副本 —— 真写回只写它。"""
    path = tmp_path / "case.bundle"
    shutil.copy2(synth, path)
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    return project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    me.add_parser(sub)
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(list(argv))


def _argv(case: Path, project: Path, tail: Sequence[str]) -> list[str]:
    """`material …` 那一串：落点两格（--bundle / --project-root）在这里统一补。"""
    return ["material", *tail, "--bundle", str(case), "--project-root", str(project)]


def _run(
    case: Path, project: Path, tail: Sequence[str], **kwargs: object
) -> int:
    argv = _argv(case, project, tail)
    for flag, value in kwargs.items():
        if value:
            argv.append("--" + flag.replace("_", "-"))
    return me.run(_parse(*argv))


# --------------------------------------------------------------------------- #
# A · 解析器形状
# --------------------------------------------------------------------------- #
def test_a_material_has_exactly_the_five_atomic_verbs() -> None:
    """两组五个动词；第六个（`swap`）在解析器那一层就该被顶回去。"""
    assert _parse(
        "material", "skin", "add", "--bundle", "b", "--path-id", "1",
        "--skin-id", "2", "--reference", "3",
    ).material_action == "add"
    assert _parse(
        "material", "skin", "remove", "--bundle", "b", "--path-id", "1", "--skin-id", "2"
    ).material_action == "remove"
    assert _parse(
        "material", "skin", "update", "--bundle", "b", "--path-id", "1", "--skin-id", "2",
        "--set-material", "500=501",
    ).material_action == "update"
    assert _parse(
        "material", "slot", "add", "--bundle", "b", "--path-id", "1", "--material-id", "500"
    ).material_action == "add"
    assert _parse(
        "material", "slot", "remove", "--bundle", "b", "--path-id", "1", "--index", "0"
    ).material_action == "remove"
    with pytest.raises(SystemExit) as excinfo:
        _parse("material", "skin", "swap", "--bundle", "b", "--path-id", "1")
    assert excinfo.value.code == 2, "材质线不该有「换」那种复合动作"


def _all_names(parser: argparse.ArgumentParser) -> list[str]:
    """递归收一个 parser 下**所有**动作名与选项名（子命令里还能挂子命令 —— 本组是两层）。"""
    names: list[str] = []
    for action in parser._actions:
        names.extend(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                names.append(str(name))
                names.extend(_all_names(child))
    return names


def test_a_material_offers_no_replacement_entry() -> None:
    names = _all_names(_parser())
    banned = [n for n in names if re.search(r"swap|replace|(^|-)to$", n, re.IGNORECASE)]
    assert banned == [], f"material 上出现了顶替式入口：{banned}"
    assert {"skin", "slot", "add", "remove", "update"} <= set(names)


def test_a_the_slot_renderer_selector_exists_and_says_where_the_roster_comes_from() -> None:
    """`--renderer` 进了解析器；帮助面要写清名单的**唯一出处**（⛔ 别让用户猜为什么给 pathID）。"""
    assert _parse(
        "material", "slot", "add", "--unit", "addr/one", "--renderer", "400", "--material-id", "501"
    ).renderer == "400"
    text = "\n".join(iter_help_texts(_parser()))
    assert "--renderer" in text
    assert "UnitPrefabRoot.Renderers 名单" in text, "帮助面没说名单从哪来"
    assert "不收名字" in text, "帮助面没说为什么不收名字"


# --------------------------------------------------------------------------- #
# B · J4：新命令的 help 落进 J3 量程且 0 禁词
# --------------------------------------------------------------------------- #
def test_b_material_help_is_visible_to_the_j3_ruler_and_clean() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    assert MATERIAL_HELP_MARK in text, "material 的短帮助没被 iter_help_texts 取到"
    assert banned_hits(text) == [], f"材质线帮助里有禁词：{banned_hits(text)}"


def test_b_material_is_wired_into_the_product_cli_parser() -> None:
    text = "\n".join(iter_help_texts(cli_main._build_parser()))
    assert MATERIAL_HELP_MARK in text, "material 没进产品 CLI（cli/__main__.py 没接线）"
    assert "material" in text


def test_b_the_leaves_still_carry_the_five_invocations() -> None:
    """取法正控：五个动词的用法串真的在帮助面里（否则"0 禁词"是**扫空了**的假绿）。"""
    text = "\n".join(iter_help_texts(_parser()))
    for needle in (
        "material skin add", "material skin remove", "material skin update",
        "material slot add", "material slot remove",
    ):
        assert needle in text, f"帮助面里没有 {needle!r}"


# --------------------------------------------------------------------------- #
# C · J1 ＋ D/J2 ＋ J6：五个动词各一条端到端
# --------------------------------------------------------------------------- #
def _end_to_end(
    case: Path,
    tmp_path: Path,
    tail: Sequence[str],
    read_back: Callable[[Path], None],
    criterion: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一次动作的完整闭环：干跑零字节 -> --write 落字节 -> 读回 -> 反向 Δ 还原逐位（J6）。"""
    project = _project(tmp_path)
    before = _sha256(case)
    # ① 默认跑法（干跑）：判据印出来了，盘上**一个字节都没写**
    assert _run(case, project, tail) == me.EXIT_DONE
    assert _sha256(case) == before, "默认跑法写了盘（J2）"
    assert criterion in capsys.readouterr().out, criterion
    # ② --write：字节真的变了，且写后读回与期望逐条相同
    assert _run(case, project, tail, write=True) == me.EXIT_DONE
    assert _sha256(case) != before, "写了但字节没变"
    assert criterion in capsys.readouterr().out, criterion
    read_back(case)
    # ③ J6：反向 Δ 还原 -> 整文件 sha256 == 改前
    report = reverse_delta.PatchStore(reverse_delta.patch_store_root(project)).restore()
    assert report.ok, "\n".join(report.lines())
    assert _sha256(case) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_c_j1_skin_add(case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def read_back(path: Path) -> None:
        slots = ps.read_skins(path, UROOT)
        assert [slot.skin_id for slot in slots] == [SKIN_A, SKIN_B, NEW_SKIN]
        assert slots[2].materials == (MAT_C,), "材质指向没改成新的"
        assert slots[2].renderers == ((RENDERER_A, RENDERER_B),), "Renderers 没照抄参照条"

    _end_to_end(
        case, tmp_path,
        ("skin", "add", "--path-id", str(UROOT), "--skin-id", str(NEW_SKIN),
         "--reference", str(SKIN_A), "--set-material", f"{MAT_A}={MAT_C}"),
        read_back, "Skins.Data 条数 2 -> 3", capsys,
    )


def test_c_j1_skin_remove(case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def read_back(path: Path) -> None:
        assert [slot.skin_id for slot in ps.read_skins(path, UROOT)] == [SKIN_A]

    _end_to_end(
        case, tmp_path,
        ("skin", "remove", "--path-id", str(UROOT), "--skin-id", str(SKIN_B)),
        read_back, "Skins.Data 条数 2 -> 1", capsys,
    )


def test_c_j1_skin_update(case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def read_back(path: Path) -> None:
        slot = ps.read_skins(path, UROOT)[0]
        assert slot.materials == (MAT_B,)
        assert slot.renderers == ((RENDERER_A, RENDERER_B),), "等长档不该动 Renderers"

    _end_to_end(
        case, tmp_path,
        ("skin", "update", "--path-id", str(UROOT), "--skin-id", str(SKIN_A),
         "--set-material", f"{MAT_A}={MAT_B}"),
        read_back, "Skins.Data 条数 2 -> 2", capsys,
    )


def test_c_j1_slot_add(case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def read_back(path: Path) -> None:
        assert rs.read_slots(path, RENDERER_A) == (MAT_A, MAT_B)

    _end_to_end(
        case, tmp_path,
        ("slot", "add", "--path-id", str(RENDERER_A), "--material-id", str(MAT_B)),
        read_back, "m_Materials 条数 1 -> 2", capsys,
    )


def test_c_j1_slot_remove(case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """删槽之前先普查：`--handled` 把"除了这一格还有谁指着它"逐条交代掉。"""
    handled = [ref.label() for ref in rs.census_material(case, MAT_B).refs]
    tail = ["slot", "remove", "--path-id", str(RENDERER_B), "--index", "1"]
    for label in handled:
        tail += ["--handled", label]

    def read_back(path: Path) -> None:
        assert rs.read_slots(path, RENDERER_B) == (MAT_A,)

    _end_to_end(case, tmp_path, tuple(tail), read_back, "m_Materials 条数 2 -> 1", capsys)


def test_c_j1_slot_add_can_insert_at_an_index(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--index` 是插入位（不是追加）：`[500]` 插在 0 位 -> `[501, 500]`。"""
    def read_back(path: Path) -> None:
        assert rs.read_slots(path, RENDERER_A) == (MAT_B, MAT_A)

    _end_to_end(
        case, tmp_path,
        ("slot", "add", "--path-id", str(RENDERER_A), "--material-id", str(MAT_B), "--index", "0"),
        read_back, "m_Materials 条数 1 -> 2", capsys,
    )


# --------------------------------------------------------------------------- #
# D · J2：默认干跑 / 显式干跑 / 两个开关同给 = rc 2
# --------------------------------------------------------------------------- #
def test_d_explicit_dry_run_writes_nothing(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project,
        ("skin", "remove", "--path-id", str(UROOT), "--skin-id", str(SKIN_B)), dry_run=True,
    )
    assert code == me.EXIT_DONE, code
    assert _sha256(case) == before, "--dry-run 写了盘"
    assert not (project / "patches").exists(), "--dry-run 却建了补丁柜"


def test_d_dry_run_and_write_are_mutually_exclusive(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project,
        ("skin", "remove", "--path-id", str(UROOT), "--skin-id", str(SKIN_B)),
        dry_run=True, write=True,
    )
    assert code == me.EXIT_UNUSABLE, code
    assert _sha256(case) == before


# --------------------------------------------------------------------------- #
# E · J3：负控（每条都必须 rc 2 且盘上零改动）
# --------------------------------------------------------------------------- #
def _prefab_without_renderer_b(case: Path, project: Path) -> None:
    """把这份合成件的 `UnitPrefabRoot.Renderers` 摘掉一格（**走产品自己的写侧**）。

    ⛔ 不是第二份夹具：合成件还是 `test_prefab_skins._synth_blob()` 那一份。
    这个现场在正常件里造不出来 —— 新增 / 修改都是"照抄参照条"，名单必然自洽；
    而"皮肤引用的渲染器不在名单里"正是 J3 第三条负控要的那个形状。
    """
    obj = ps.read_package_object(case, UROOT)
    tree = dict(obj.tree)
    tree["Renderers"] = [cl.pptr(RENDERER_A)]
    payload = ps.serialize_package_tree(obj, tree)
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(project))
    report = writer.write_delta(
        case,
        writer.DeltaChange(
            growths=(writer.ObjectGrowth(path_id=UROOT, payload=payload, name=PREFAB_NAME),)
        ),
        dry_run=False,
        project_root=project,
        patch_store=store,
        patch_label="夹具：摘掉 Renderers 里的一格",
    )
    assert report.wrote_bundle, "\n".join(report.report_lines())


def test_e_j3_a_duplicate_skin_id_is_refused(case: Path, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(
        case, project,
        ("skin", "add", "--path-id", str(UROOT), "--skin-id", str(SKIN_A),
         "--reference", str(SKIN_B)),
    )
    assert code == me.EXIT_UNUSABLE, code
    assert "已经在这张表里了" in capsys.readouterr().err
    assert _sha256(case) == before, "守卫不过却改了盘"


def test_e_j3_a_renderer_outside_the_prefab_list_is_refused(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    _prefab_without_renderer_b(case, project)
    before = _sha256(case)
    code = _run(
        case, project,
        ("skin", "add", "--path-id", str(UROOT), "--skin-id", str(NEW_SKIN),
         "--reference", str(SKIN_A)),
    )
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "不在 prefab 的 Renderers 名单" in err, err
    assert str(RENDERER_B) in err, err
    assert _sha256(case) == before, "守卫不过却改了盘"


def test_e_j3_removing_a_slot_with_unhandled_references_is_refused(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`RENDERER_A.m_Materials[0]` 那颗材质还被 3 条引用指着 ⇒ 不给 `--handled` 就拒绝。"""
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(case, project, ("slot", "remove", "--path-id", str(RENDERER_A), "--index", "0"))
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "还有 3 条引用指着它" in err, err
    assert f"MeshRenderer#{RENDERER_B}.m_Materials[0]" in err, err
    assert _sha256(case) == before, "守卫不过却改了盘"


def test_e_j3_a_partial_handled_list_is_still_refused(case: Path, tmp_path: Path) -> None:
    """交代一半也不行（fail-closed）—— 这是那把尺子的**能红**一侧。"""
    project = _project(tmp_path)
    labels = [
        ref.label()
        for ref in rs.census_material(case, MAT_A).others(
            owner_path_id=RENDERER_A, field="m_Materials[0]"
        )
    ]
    before = _sha256(case)
    code = _run(
        case, project,
        ("slot", "remove", "--path-id", str(RENDERER_A), "--index", "0", "--handled", labels[0]),
    )
    assert code == me.EXIT_UNUSABLE, code
    assert _sha256(case) == before


@pytest.mark.parametrize(
    "tail,needle",
    [
        (("skin", "add", "--path-id", str(UROOT), "--skin-id", str(NEW_SKIN),
          "--reference", "9999"), "没有 Id=9999"),
        (("skin", "add", "--path-id", str(UROOT), "--skin-id", str(NEW_SKIN),
          "--reference", str(SKIN_A), "--set-material", f"{MAT_A}=999999"), "指向包外"),
        (("skin", "update", "--path-id", str(UROOT), "--skin-id", str(SKIN_A),
          "--set-material", f"{MAT_A}={MAT_A}"), "空改动"),
        (("skin", "remove", "--path-id", str(UROOT), "--skin-id", "9999"), "没有 Id=9999"),
        (("slot", "add", "--path-id", str(RENDERER_A), "--material-id", "999999"), "不在这个包里"),
        (("slot", "add", "--path-id", str(RENDERER_A), "--material-id", "0"), "不是正数"),
        (("slot", "remove", "--path-id", str(RENDERER_A), "--index", "9"), "越界"),
        (("slot", "add", "--path-id", str(UROOT), "--material-id", str(MAT_B)), "没有 m_Materials"),
    ],
    ids=["missing-reference", "outside-package", "empty-change", "remove-missing",
         "material-not-in-package", "material-zero", "index-out-of-range", "not-a-renderer"],
)
def test_e_guard_failures_exit_unusable_and_touch_nothing(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    tail: tuple[str, ...], needle: str,
) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = _run(case, project, tail)
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert needle in err, err
    assert _sha256(case) == before, "守卫不过却改了盘"


def test_e_a_bad_set_material_pair_is_refused(case: Path, tmp_path: Path) -> None:
    """`--set-material` 写歪了就拒绝（⛔ 不猜，也不静默当成"没给"）。"""
    project = _project(tmp_path)
    before = _sha256(case)
    assert _run(
        case, project,
        ("skin", "update", "--path-id", str(UROOT), "--skin-id", str(SKIN_A),
         "--set-material", "500-502"),
    ) == me.EXIT_UNUSABLE
    assert _run(
        case, project,
        ("skin", "update", "--path-id", str(UROOT), "--skin-id", str(SKIN_A),
         "--set-material", "Layer_A=502"),
    ) == me.EXIT_UNUSABLE
    assert _sha256(case) == before


# --------------------------------------------------------------------------- #
# F · 出口：--json / 包打不开 / 动作不认识 / 产品入口转调
# --------------------------------------------------------------------------- #
def test_f_json_mode_keeps_stdout_to_one_document(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    code = _run(
        case, project,
        ("skin", "remove", "--path-id", str(UROOT), "--skin-id", str(SKIN_B)), json=True,
    )
    assert code == me.EXIT_DONE, code
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["group"] == "skin" and document["verb"] == "remove"
    assert document["count_before"] == 2 and document["count_after"] == 1
    assert document["slots_after"] == [SKIN_A]
    assert document["dry_run"] is True and document["patch"] is None


def test_f_a_missing_bundle_exits_unusable(tmp_path: Path) -> None:
    project = _project(tmp_path)
    code = me.run(_parse(
        "material", "skin", "remove", "--bundle", str(tmp_path / "nope.bundle"),
        "--path-id", str(UROOT), "--skin-id", str(SKIN_B), "--project-root", str(project),
    ))
    assert code == me.EXIT_UNUSABLE, code


def test_f_the_product_entry_point_dispatches_to_this_module(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    code = cli_main.main([
        "material", "skin", "remove", "--bundle", str(case), "--path-id", str(UROOT),
        "--skin-id", str(SKIN_B), "--project-root", str(project),
    ])
    assert code == 0, code
    assert _sha256(case) == before, "产品入口那一条（干跑）动了盘"


def test_f_the_entry_point_refuses_a_verb_this_group_does_not_have() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main(["material", "slot", "swap"])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# G · 尺子正控：rc 1 那一档必须能红
# --------------------------------------------------------------------------- #
def test_g1_a_failed_readback_lands_on_exit_dirty(
    case: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**正控**：写后复核抛 ⇒ 必须落 **rc 1**（⛔ 不许吞成 rc 0，也不许当成"用不了"）。"""
    project = _project(tmp_path)

    def boom(plan: object) -> tuple[str, ...]:
        raise AssetError("写后读回不对（正控）")

    monkeypatch.setattr(me, "verify_plan", boom)
    code = _run(
        case, project,
        ("skin", "remove", "--path-id", str(UROOT), "--skin-id", str(SKIN_B)), write=True,
    )
    assert code == me.EXIT_DIRTY, code


def test_g2_a_red_writeback_row_lands_on_exit_dirty(
    case: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**正控（第二把）**：writer 的写后复核报红 ⇒ rc 1；把那一行摘掉 ⇒ 同一条命令 rc 0。"""
    project = _project(tmp_path)
    tail = ("skin", "remove", "--path-id", str(UROOT), "--skin-id", str(SKIN_B))
    seen: dict[str, dict] = {}

    def fake_install(plan: object, *, project_root: object) -> object:
        return types.SimpleNamespace(
            verify=seen["verify"], wrote_bundle=True, patch=None, patch_bytes=0,
            dry_run=False, report_lines=lambda: (),
        )

    monkeypatch.setattr(me, "install_plan", fake_install)
    monkeypatch.setattr(me, "verify_plan", lambda plan: ())
    seen["verify"] = {"bundle": {"ok": False, "detail": "正控：故意报红"}}
    assert _run(case, project, tail, write=True) == me.EXIT_DIRTY
    seen["verify"] = {"bundle": {"ok": True, "detail": "全绿"}}
    assert _run(case, project, tail, write=True) == me.EXIT_DONE


def test_g3_the_readback_ruler_goes_red_when_the_file_moved_on(
    case: Path, tmp_path: Path
) -> None:
    """**正控（第三把）**：写完再改一刀 ⇒ 旧计划的 `verify_plan` 必须红（不能绿的尺子）。"""
    project = _project(tmp_path)
    plan = ps.plan_add(case, UROOT, skin_id=NEW_SKIN, reference_id=SKIN_A)
    ps.install(plan, project_root=project)
    assert me.verify_plan(plan)[0].startswith("Skins.Data 条数 2 -> 3")
    second = ps.plan_remove(case, UROOT, skin_id=NEW_SKIN)
    ps.install(second, project_root=project)
    with pytest.raises(AssetError):
        me.verify_plan(plan)


# --------------------------------------------------------------------------- #
# H · `--unit <地址键>`：unit_key -> (包, UnitPrefabRoot 的 pathID, catalog)
# --------------------------------------------------------------------------- #
def _unit_game(tmp_path: Path) -> Path:
    """一个**合成游戏根**：`addr/one` -> `Assets/Synth/Shared.prefab`。

    三个候选包里只有 ALPHA 带容器条目 ⇒ 走的是"catalog 过度包含、`m_Container` 复核唯一命中"
    那条真路（`unitypy_source.open_unique_bundle`）。 ⛔ 全部走产品入口造件。
    与 `_work/containers/unitgame`（给人跑命令用的落盘件）是同一份构造。
    """
    aa = tmp_path / "unitgame" / "BrokenArrow_Data" / "StreamingAssets" / "aa"
    pc = aa / "PC"
    pc.mkdir(parents=True)
    (aa / "catalog.json").write_bytes(_bytes(_synth()))
    bundle = pc / ALPHA
    bundle.write_bytes(_synth_blob())
    rs.install(
        rs.plan_add_container(bundle, asset_path=SHARED_ASSET, asset_id=PREFAB_GO),
        project_root=tmp_path / "unitgame" / "proj",
    )
    # catalog 的 crc / bundle_size 必须**按真值记账**：写回器拿磁盘现状与它比，对不上就落
    # blocked_reason（⇒ rc=1「结果不干净」）—— 那是产品在如实拦，不是用例的错。
    facts, _data, _serialized = new_asset.bundle_facts_of_file(bundle)
    edit = ce.CatalogEdit.from_bytes((aa / "catalog.json").read_bytes())
    edit.update_bundle(ALPHA, crc=int(facts["crc"]), bundle_size=int(facts["bundle_size"]))
    (aa / "catalog.json").write_bytes(edit.to_bytes())
    assert edit.self_check() == ()
    return aa.parents[2]


def _unit_bundle(game: Path) -> Path:
    return game / "BrokenArrow_Data" / "StreamingAssets" / "aa" / "PC" / ALPHA


def test_h_the_unit_locator_runs_dry_then_writes_and_j6(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**判据**：`material skin remove --unit addr/one --dry-run` 跑通（rc 0）；
    干跑零字节 -> `--write` 落字节 -> 读回 -> 反向 Δ 还原逐位（J6）。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    before = _sha256(bundle)
    argv = [
        "material", "skin", "remove", "--unit", UNIT_KEY, "--skin-id", str(SKIN_B),
        "--game", str(game), "--project-root", str(tmp_path / "proj"),
    ]
    assert me.run(_parse(*argv, "--dry-run")) == me.EXIT_DONE
    assert _sha256(bundle) == before, "干跑写了盘"
    out = capsys.readouterr().out
    assert "Skins.Data 条数 2 -> 1（删除，Id [2233]）" in out
    assert ALPHA in out, "包不是从 catalog 那一跳解出来的"
    assert me.run(_parse(*argv, "--write")) == me.EXIT_DONE
    assert _sha256(bundle) != before
    assert [slot.skin_id for slot in ps.read_skins(bundle, UROOT)] == [SKIN_A]
    report = reverse_delta.PatchStore(
        reverse_delta.patch_store_root(tmp_path / "proj")
    ).restore()
    assert report.ok, report.lines()
    assert _sha256(bundle) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_h_the_unit_locator_refuses_both_locators_at_once(tmp_path: Path) -> None:
    game = _unit_game(tmp_path)
    tail = ["skin", "remove", "--unit", UNIT_KEY, "--bundle", "x.bundle", "--path-id", "1",
            "--skin-id", str(SKIN_B)]
    assert _run(_unit_bundle(game), tmp_path, tail) == me.EXIT_UNUSABLE


def test_h_the_unit_locator_refuses_path_id_together_with_unit(tmp_path: Path) -> None:
    game = _unit_game(tmp_path)
    code = me.run(_parse(
        "material", "skin", "remove", "--unit", UNIT_KEY, "--path-id", str(UROOT),
        "--skin-id", str(SKIN_B), "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code


def test_h_bundle_without_path_id_is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    code = me.run(_parse(
        "material", "skin", "remove", "--bundle", str(tmp_path / "x.bundle"),
        "--skin-id", str(SKIN_B), "--project-root", str(project),
    ))
    assert code == me.EXIT_UNUSABLE, code


def test_h_no_locator_at_all_is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    code = me.run(_parse(
        "material", "skin", "remove", "--skin-id", str(SKIN_B), "--project-root", str(project)
    ))
    assert code == me.EXIT_UNUSABLE, code


def test_h_slot_add_on_the_unit_locator_dry_then_write_then_j6(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**判据**：`material slot add --unit addr/one --renderer 400 --material-id 501` 跑通（rc 0）。

    干跑零字节 -> `--write` 落字节 -> 读回 -> 反向 Δ 还原逐位（J6）。
    """
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    project = tmp_path / "proj"
    before = _sha256(bundle)
    argv = [
        "material", "slot", "add", "--unit", UNIT_KEY, "--renderer", str(RENDERER_A),
        "--material-id", str(MAT_B), "--game", str(game), "--project-root", str(project),
    ]
    assert me.run(_parse(*argv, "--dry-run")) == me.EXIT_DONE
    assert _sha256(bundle) == before, "干跑写了盘"
    out = capsys.readouterr().out
    assert "m_Materials 条数 1 -> 2" in out
    assert f"Renderer#{RENDERER_A}" in out, "没印出名单里挑中的那一颗"
    assert "UnitPrefabRoot.Renderers 名单 2 条里挑的" in out
    assert ALPHA in out, "包不是从 catalog 那一跳解出来的"
    assert me.run(_parse(*argv, "--write")) == me.EXIT_DONE
    assert _sha256(bundle) != before
    assert rs.read_slots(bundle, RENDERER_A) == (MAT_A, MAT_B)
    report = reverse_delta.PatchStore(reverse_delta.patch_store_root(project)).restore()
    assert report.ok, report.lines()
    assert _sha256(bundle) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_h_slot_remove_on_the_unit_locator_dry_then_write_then_j6(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """删槽那一支同一条路：整包引用普查的每一条都要进 `--handled`，否则拒绝。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    project = tmp_path / "proj"
    before = _sha256(bundle)
    handled = [ref.label() for ref in rs.census_material(bundle, MAT_B).refs]
    argv = [
        "material", "slot", "remove", "--unit", UNIT_KEY, "--renderer", str(RENDERER_B),
        "--index", "1", "--game", str(game), "--project-root", str(project),
    ]
    for label in handled:
        argv += ["--handled", label]
    assert me.run(_parse(*argv, "--dry-run")) == me.EXIT_DONE
    assert _sha256(bundle) == before, "干跑写了盘"
    assert "m_Materials 条数 2 -> 1" in capsys.readouterr().out
    assert me.run(_parse(*argv, "--write")) == me.EXIT_DONE
    assert rs.read_slots(bundle, RENDERER_B) == (MAT_A,)
    report = reverse_delta.PatchStore(reverse_delta.patch_store_root(project)).restore()
    assert report.ok, report.lines()
    assert _sha256(bundle) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_h_slot_on_the_unit_locator_asks_for_a_renderer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**负控**：`--renderer` 不给 ⇒ rc 2，并把名单逐条印出来（⛔ 不自动挑）。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    before = _sha256(bundle)
    code = me.run(_parse(
        "material", "slot", "add", "--unit", UNIT_KEY, "--material-id", str(MAT_B),
        "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "--renderer 要指名一颗渲染器" in err, err
    assert f"Renderer#{RENDERER_A}" in err and f"Renderer#{RENDERER_B}" in err, err
    assert _sha256(bundle) == before, "守卫不过却改了盘"


def test_h_a_renderer_outside_the_roster_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**负控**：`--renderer` 给一个不在名单里的 pathID ⇒ rc 2 ＋ 印名单。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    before = _sha256(bundle)
    code = me.run(_parse(
        "material", "slot", "add", "--unit", UNIT_KEY, "--renderer", "999999",
        "--material-id", str(MAT_B), "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "不在这个单位的 2 条名单里" in err, err
    assert f"Renderer#{RENDERER_A}" in err, err
    assert _sha256(bundle) == before, "守卫不过却改了盘"


def test_h_a_renderer_name_is_not_a_locator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**负控**：`--renderer` 收的只有整数 pathID —— 名字（真件里会重名）一律不收。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    before = _sha256(bundle)
    code = me.run(_parse(
        "material", "slot", "add", "--unit", UNIT_KEY, "--renderer", "Chassis",
        "--material-id", str(MAT_B), "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "要的是渲染器 **pathID**（整数）" in err, err
    assert f"Renderer#{RENDERER_A}" in err, err
    assert _sha256(bundle) == before, "守卫不过却改了盘"


def test_h_a_package_renderer_outside_the_roster_is_still_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**正控**：RENDERER_B 就在包里、槽也在，但名单里摘掉它 ⇒ 仍然拒绝（名单是唯一依据）。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    _prefab_without_renderer_b(bundle, tmp_path / "proj")
    before = _sha256(bundle)
    code = me.run(_parse(
        "material", "slot", "add", "--unit", UNIT_KEY, "--renderer", str(RENDERER_B),
        "--material-id", str(MAT_B), "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "不在这个单位的 1 条名单里" in err, err
    assert str(RENDERER_A) in err, err
    assert _sha256(bundle) == before, "守卫不过却改了盘"


def _empty_the_roster(bundle: Path, project: Path) -> None:
    """把这份合成件的 `UnitPrefabRoot.Renderers` 清空（**走产品自己的写侧**，不是第二份夹具）。"""
    obj = ps.read_package_object(bundle, UROOT)
    tree = dict(obj.tree)
    tree["Renderers"] = []
    payload = ps.serialize_package_tree(obj, tree)
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(project))
    report = writer.write_delta(
        bundle,
        writer.DeltaChange(
            growths=(writer.ObjectGrowth(path_id=UROOT, payload=payload, name=PREFAB_NAME),)
        ),
        dry_run=False,
        project_root=project,
        patch_store=store,
        patch_label="夹具：清空 Renderers 名单",
    )
    assert report.wrote_bundle, "\n".join(report.report_lines())


def test_h_an_empty_roster_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**负控**：名单读不出 / 为空 ⇒ rc 2（⛔ 不许静默放过，也不许回落成「没有渲染器」）。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    _empty_the_roster(bundle, tmp_path / "proj")
    before = _sha256(bundle)
    code = me.run(_parse(
        "material", "slot", "add", "--unit", UNIT_KEY, "--renderer", str(RENDERER_A),
        "--material-id", str(MAT_B), "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert "名单是空的" in err, err
    assert _sha256(bundle) == before, "守卫不过却改了盘"


def test_h_renderer_without_the_unit_locator_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**负控**：`--renderer` 给了但 `--unit` 没给 ⇒ rc 2（`--path-id` 已经指名了渲染器）。"""
    game = _unit_game(tmp_path)
    bundle = _unit_bundle(game)
    before = _sha256(bundle)
    code = me.run(_parse(
        "material", "slot", "add", "--bundle", str(bundle), "--path-id", str(RENDERER_A),
        "--renderer", str(RENDERER_A), "--material-id", str(MAT_B),
        "--project-root", str(tmp_path / "proj"),
    ))
    assert code == me.EXIT_UNUSABLE, code
    assert "只在 --unit 那条路上用" in capsys.readouterr().err
    assert _sha256(bundle) == before, "守卫不过却改了盘"


def test_h_renderer_on_the_skin_verbs_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--renderer` 是 slot 那一支的（skin 那三个动词动的是 prefab 上的皮肤表）。"""
    game = _unit_game(tmp_path)
    code = me.run(_parse(
        "material", "skin", "remove", "--unit", UNIT_KEY, "--skin-id", str(SKIN_B),
        "--renderer", str(RENDERER_A), "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code
    assert "是 slot 那一支的" in capsys.readouterr().err


def test_h_the_roster_is_unit_prefab_root_renderers_on_a_real_prefab() -> None:
    """**真件一手**（本任务「那一跳要有依据」的尺子，⛔ 只读 `_work/a2/` 抽件，不进游戏目录）。

    判据：`--unit` 那份名单 == prefab 的 13 个渲染器（逐一对应），名字读得出来，
    而且真件里**名字会重名** —— 所以 `--renderer` 只收 pathID（A 案）。
    """
    if not REAL_A2.is_file():
        pytest.skip(f"缺真件抽件 {REAL_A2}（不是游戏目录）")
    with us.open_source(REAL_A2) as source:
        roster = rs.unit_renderer_roster(
            REAL_A2, REAL_A2_ROOT, source=source, asset_path=REAL_A2_ASSET
        )
        tree = pf.read_prefab_tree(source, asset_path=REAL_A2_ASSET)
    assert len(roster) == 13 == len(tree.renderers), "名单与 prefab 的渲染器不是一一对应"
    assert {entry.path_id for entry in roster} == {item.component_id for item in tree.renderers}
    names = [entry.name for entry in roster]
    assert names[:3] == ["Chassis", "RU_TanktrakL", "RU_TanktrakR"], names
    assert names.count("000_skinned_Chassis") == 2, "名字重名（按名字指名必然二义）"
    assert all(entry.kind in ("MeshRenderer", "SkinnedMeshRenderer") for entry in roster)
    assert all(entry.slots is not None for entry in roster)
    assert rs.resolve_unit_renderer(roster, str(roster[0].path_id)).name == "Chassis"
    for not_a_path_id in ("000_skinned_Chassis", "Chassis", "999"):
        with pytest.raises(AssetError):
            rs.resolve_unit_renderer(roster, not_a_path_id)


def test_h_an_unknown_unit_key_is_refused(tmp_path: Path) -> None:
    game = _unit_game(tmp_path)
    code = me.run(_parse(
        "material", "skin", "remove", "--unit", "no/such", "--skin-id", str(SKIN_B),
        "--game", str(game),
    ))
    assert code == me.EXIT_UNUSABLE, code


def test_h_a_missing_game_root_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """找不到游戏根 ⇒ rc 2（⛔ 不静默回落成"没有改动"）。"""
    monkeypatch.setattr("ba_mod_maker.core.assets.game.find_game_root", lambda: None)
    code = me.run(_parse(
        "material", "skin", "remove", "--unit", UNIT_KEY, "--skin-id", str(SKIN_B)
    ))
    assert code == me.EXIT_UNUSABLE, code

