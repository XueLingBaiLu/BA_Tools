r"""`material visible add|remove|update` 的机械判据（**可见性层**，产品 CLI）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_cli_material_visible.py -q -n 0`
-> 通过数**从 0 收集**（本文件新建前不存在）**变 N passed**。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 解析器形状：`visible` 挂在 `material` 组下（skin / slot 照旧）；三个动词 | 无顶替入口 |
| B | **J3** 文案尺子：帮助面取得到那个记号、禁词 0 命中 | 0 |
| C | 干跑：不带 `--write` -> 源容器**逐字节不变**；三对互斥开关同给 / `--write` 没落点 | 全 rc 2 |
| D | 真写：`--out` -> **源件逐字节不变** ＋ 产物读回 == 期望；`--in-place` -> 复核后就位 | 相等 |
| E | 负控：Id 撞 / 参照条缺 / 条目不存在 / 不给字段 / pathID 拿错 | 全 rc 2 零改动 |
| F | 出口：`--json` 只写一份文档（含反向 Δ record ＋ 原始字节读数）；动作不认识 ⇒ rc 2 | 全过 |
| G | **尺子正控**：把产物大小弄成与读数不符 ⇒ 这一条**必须**落 rc 1 | 能绿能红 |
| H | **分派**：`dispatch()` 把 visible 转本模块，其余原样转 `material_edit.run` | 两边都对 |

夹具：**合成容器**从 `tests/test_user_items.py` import 过来（`container` / `PID` /
`UNIT` / `REAL_IDS` / `_tree` / `_row` / `_payload` 那一套）——
⛔ 不造第二份，也⛔ **不碰真 data.unity3d**。

⚠ 这一层与 ③ 层不同的一条**强判据**：`install` 写的是**新文件** ⇒ 源容器**一个字节都不许动**
（`test_d_write_a_new_file_leaves_the_source_untouched` 就是它）。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.asset_kernel import user_items as ui
from ba_mod_maker.cli import material_edit as me
from ba_mod_maker.cli import material_visible as mv
from ba_mod_maker.core.asset import ufs
from test_no_swap_wording import banned_hits, iter_help_texts
from test_user_items import (
    OTHER_PID,
    PID,
    REAL_IDS,
    UNIT,
    _blob,
    _container,
    _payload,
    _row,
    _sf,
    _tree,
)

#: `visible` 子命令短帮助里那一句（「新入口进了 J3 尺子量程」的记号）。
VISIBLE_HELP_MARK = "可见性层：UserItemsConfig 的皮肤条目 新增 / 删除 / 修改"
#: 合成件里单位 9 的两条皮肤 Id（`_tree()` 的默认）。
FIRST_ID, SECOND_ID = REAL_IDS[0], REAL_IDS[1]
NEW_ID = 2240


# --------------------------------------------------------------------------- #
# 夹具与小工具
# --------------------------------------------------------------------------- #
@pytest.fixture
def container(tmp_path: Path) -> Path:
    """合成容器 —— 与 `tests/test_user_items.py` 的 `container` 夹具**同一份构造器**
    （`_sf` / `_container` / `_payload` / `_tree` —— ⛔ 不是第二份夹具）。"""
    path = tmp_path / "globalgamemanagers.unity3d"
    path.write_bytes(
        _container(_sf([(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(_tree()))]))
    )
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    """与 `__main__.py` **同一种形状**：`material` 那一支自带 `visible`
    （接线后就长这样 —— `material_edit.add_parser` 里已经挂了 `visible`）。"""
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    me.add_parser(sub)
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(list(argv))


def _all_names(parser: argparse.ArgumentParser) -> list[str]:
    names: list[str] = []
    for action in parser._actions:
        names.extend(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                names.append(str(name))
                names.extend(_all_names(child))
    return names


def _run(case: Path, tail: tuple[str, ...], **flags: object) -> int:
    argv = ["material", "visible", *tail, "--container", str(case)]
    for flag, value in flags.items():
        if value is True:
            argv.append("--" + flag.replace("_", "-"))
        elif value is not None and value is not False and value != "":
            argv += ["--" + flag.replace("_", "-"), str(value)]
    return mv.run(_parse(*argv))


def _items(path: Path, unit: int = UNIT) -> tuple[ui.SkinItem, ...]:
    return ui.read_unit_items(path, PID, unit)


def _add_tail(item_id: int = NEW_ID, reference: int = FIRST_ID) -> tuple[str, ...]:
    return ("add", "--path-id", str(PID), "--unit", str(UNIT),
            "--item-id", str(item_id), "--reference", str(reference))


def _container_with_triggers(tmp_path: Path) -> Path:
    """参照条带一条触发器的合成容器（`--drop-triggers` 那条判据要它）。"""
    tree = _tree({UNIT: [_row(FIRST_ID, triggers=(
        {"Trigger": 7, "DescriptionLocKey": "unlock_x", "Count": 3},
    )), _row(SECOND_ID)]})
    path = tmp_path / "with-triggers.unity3d"
    path.write_bytes(_container(_sf([(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(tree))])))
    return path


#: 夹具里那两行：第 0 行 grade=3 / 第 1 行 grade=7（**故意不同** —— 私有字段那把尺子的正控）。
TRIGGER_A = {"Trigger": "mission_a", "DescriptionLocKey": "loc_a", "Count": 3}
TRIGGER_B = {"Trigger": "mission_b", "DescriptionLocKey": "loc_b", "Count": 1}


def _container_with_trigger_rows(tmp_path: Path) -> Path:
    """两行、**第 1 行**（非第一行）带两条触发器：改 / 删 / 增都在它上面做。"""
    tree = _tree({UNIT: [
        _row(FIRST_ID, grade=3),
        _row(SECOND_ID, grade=7, triggers=(TRIGGER_A, TRIGGER_B)),
    ]})
    path = tmp_path / "trigger-rows.unity3d"
    path.write_bytes(_container(_sf([(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(tree))])))
    return path


def _rows(path: Path, unit: int = UNIT) -> list[dict]:
    """产物里那个单位的两行**原始行**（看私有字段用）。"""
    return ui.read_container_config(path, PID).tree["Skins"]["_valueData"][0]["Data"]


def _trigger_tail(*flags: str) -> tuple[str, ...]:
    return ("update", "--path-id", str(PID), "--unit", str(UNIT),
            "--item-id", str(SECOND_ID), *flags)


# --------------------------------------------------------------------------- #
# A · 解析器形状（挂在 material 组下 ＋ 无顶替式入口）
# --------------------------------------------------------------------------- #
def test_a_visible_is_nested_under_the_material_group() -> None:
    common = ("--container", "c", "--path-id", "1", "--unit", "9", "--item-id", "2240")
    for verb in ("remove", "update"):
        args = _parse("material", "visible", verb, *common)
        assert args.command == "material" and args.material_group == "visible"
        assert args.visible_action == verb
    added = _parse("material", "visible", "add", *common, "--reference", "2232")
    assert added.visible_action == "add" and added.reference_id == 2232
    # skin / slot 那两组逐字照旧（⛔ 本模块不改 material_edit 的动词）
    old = _parse("material", "skin", "remove", "--unit", "RU_T72B", "--skin-id", "2233")
    assert old.material_group == "skin" and old.material_action == "remove"
    with pytest.raises(SystemExit) as excinfo:
        _parse("material", "visible", "swap", "--container", "c")
    assert excinfo.value.code == 2, "可见性层不该有「换」那种动作"


def test_a_visible_offers_no_replacement_entry() -> None:
    names = _all_names(_parser())
    banned = [n for n in names if re.search(r"swap|replace|(^|-)to$", n, re.IGNORECASE)]
    assert banned == [], f"material visible 上出现了顶替式入口：{banned}"
    assert {"material", "visible", "add", "remove", "update"} <= set(names)


def test_a_the_trigger_flags_are_wired_on_both_verbs() -> None:
    """触发器那三格 ＋ 三个动作开关都挂在解析器上（动作名 = 新增 / 删除 / 修改）。"""
    common = ("--container", "c", "--path-id", "1", "--unit", "9", "--item-id", "2240")
    added = _parse("material", "visible", "add", *common, "--reference", "2232",
                   "--trigger", "t", "--trigger-loc-key", "k", "--trigger-count", "1")
    assert (added.trigger, added.trigger_loc_key, added.trigger_count) == ("t", "k", 1)
    updated = _parse("material", "visible", "update", *common, "--trigger-set", "1",
                     "--trigger-count", "5")
    assert updated.trigger_set == 1 and updated.trigger_count == 5
    assert updated.trigger_add is False and updated.trigger_remove is None
    removed = _parse("material", "visible", "update", *common, "--trigger-remove", "0")
    assert removed.trigger_remove == 0 and removed.trigger_set is None
    assert (mv.TRIGGER_ADD, mv.TRIGGER_REMOVE, mv.TRIGGER_SET) == ("新增", "删除", "修改")


# --------------------------------------------------------------------------- #
# B · J3 文案尺子
# --------------------------------------------------------------------------- #
def test_b_help_is_visible_to_the_j3_ruler_and_clean() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    assert VISIBLE_HELP_MARK in text, "visible 的短帮助没被 iter_help_texts 取到"
    assert banned_hits(text) == [], f"可见性层帮助里有禁词：{banned_hits(text)}"


def test_b_the_three_invocations_are_in_the_help_surface() -> None:
    text = "\n".join(iter_help_texts(_parser()))
    for needle in ("material visible add", "material visible remove", "material visible update"):
        assert needle in text, f"帮助面里没有 {needle!r}"


# --------------------------------------------------------------------------- #
# C · 干跑零字节 ＋ 三个互斥
# --------------------------------------------------------------------------- #
def test_c_the_default_run_writes_nothing(container: Path) -> None:
    before = _sha(container)
    assert _run(container, _add_tail()) == mv.EXIT_DONE
    assert _sha(container) == before, "默认跑法写了源容器"
    assert [item.item_id for item in _items(container)] == [FIRST_ID, SECOND_ID]


def test_c_explicit_dry_run_writes_nothing(container: Path, tmp_path: Path) -> None:
    before = _sha(container)
    out = tmp_path / "never.unity3d"
    assert _run(container, _add_tail(), dry_run=True, out=out) == mv.EXIT_DONE
    assert _sha(container) == before
    assert not out.exists(), "--dry-run 却出了产物"


def test_c_write_without_a_target_is_refused(container: Path) -> None:
    before = _sha(container)
    assert _run(container, _add_tail(), write=True) == mv.EXIT_UNUSABLE
    assert _sha(container) == before


def test_c_out_and_in_place_are_mutually_exclusive(container: Path, tmp_path: Path) -> None:
    before = _sha(container)
    code = _run(container, _add_tail(), write=True, out=tmp_path / "x.unity3d", in_place=True)
    assert code == mv.EXIT_UNUSABLE
    assert _sha(container) == before


def test_c_dry_run_and_write_are_mutually_exclusive(container: Path, tmp_path: Path) -> None:
    before = _sha(container)
    code = _run(container, _add_tail(), dry_run=True, write=True, out=tmp_path / "x.unity3d")
    assert code == mv.EXIT_UNUSABLE
    assert _sha(container) == before


# --------------------------------------------------------------------------- #
# D · 真写：新文件不碰源件 / 原地替换要显式
# --------------------------------------------------------------------------- #
def test_d_write_a_new_file_leaves_the_source_untouched(
    container: Path, tmp_path: Path
) -> None:
    """**这一层最硬的一条**：`install` 写新文件 ⇒ 源容器逐字节不变。"""
    before = _sha(container)
    out = tmp_path / "visible.unity3d"
    assert _run(container, _add_tail(), write=True, out=out,
                project_root=tmp_path) == mv.EXIT_DONE
    assert _sha(container) == before, "源容器被动了"
    got = _items(out)
    assert [item.item_id for item in got] == [FIRST_ID, SECOND_ID, NEW_ID]
    # 4 格产品字段不给 ⇒ 照抄参照条那一格（⛔ 不是造默认值）
    reference = ui.item_of(ui.read_container_config(out, PID).tree, UNIT, FIRST_ID)
    assert got[-1].skin_pack == reference.skin_pack
    assert got[-1].is_default == reference.is_default
    assert got[-1].free == reference.free
    assert got[-1].disabled == reference.disabled


def test_d_add_can_override_the_four_product_fields(container: Path, tmp_path: Path) -> None:
    out = tmp_path / "visible.unity3d"
    code = _run(container, _add_tail(), write=True, out=out, project_root=tmp_path,
                skin_pack="2 Custom", is_default=1, free=0, disabled=1)
    assert code == mv.EXIT_DONE, code
    item = _items(out)[-1]
    assert (item.skin_pack, item.is_default, item.free, item.disabled) == (
        "2 Custom", 1, 0, 1
    )


def test_d_update_changes_only_the_given_field(container: Path, tmp_path: Path) -> None:
    before = _sha(container)
    out = tmp_path / "visible.unity3d"
    code = _run(container, ("update", "--path-id", str(PID), "--unit", str(UNIT),
                            "--item-id", str(SECOND_ID), "--free", "1"),
                write=True, out=out, project_root=tmp_path)
    assert code == mv.EXIT_DONE, code
    assert _sha(container) == before, "源容器被动了"
    got = {item.item_id: item for item in _items(out)}
    assert got[SECOND_ID].free == 1
    assert got[FIRST_ID] == _items(container)[0], "别的条目被动了"


def test_d_remove_drops_exactly_one(container: Path, tmp_path: Path) -> None:
    before = _sha(container)
    out = tmp_path / "visible.unity3d"
    code = _run(container, ("remove", "--path-id", str(PID), "--unit", str(UNIT),
                            "--item-id", str(SECOND_ID)), write=True, out=out,
                            project_root=tmp_path)
    assert code == mv.EXIT_DONE, code
    assert _sha(container) == before
    assert [item.item_id for item in _items(out)] == [FIRST_ID]


def test_d_in_place_replaces_the_source_only_with_the_explicit_flag(
    container: Path, tmp_path: Path
) -> None:
    before = _sha(container)
    code = _run(container, _add_tail(), write=True, in_place=True, project_root=tmp_path)
    assert code == mv.EXIT_DONE, code
    assert _sha(container) != before, "--in-place 没落字节"
    assert [item.item_id for item in _items(container)] == [FIRST_ID, SECOND_ID, NEW_ID]
    assert not (container.parent / (container.name + mv._TEMP_SUFFIX)).exists(), "临时文件没清掉"


def test_d_drop_triggers_empties_the_trigger_table(tmp_path: Path) -> None:
    case = _container_with_triggers(tmp_path)
    reference = ui.item_of(ui.read_container_config(case, PID).tree, UNIT, FIRST_ID)
    assert len(reference.triggers) == 1, "夹具坏：参照条应当带一条触发器"
    kept = tmp_path / "kept.unity3d"
    assert _run(case, _add_tail(), write=True, out=kept,
                project_root=tmp_path) == mv.EXIT_DONE
    assert _items(kept)[-1].triggers == reference.triggers, "不给就该照抄参照条"
    dropped = tmp_path / "dropped.unity3d"
    assert _run(case, _add_tail(), write=True, out=dropped, drop_triggers=True,
                project_root=tmp_path) == mv.EXIT_DONE
    assert _items(dropped)[-1].triggers == ()


def test_d_trigger_add_set_remove_round_trip(tmp_path: Path) -> None:
    """**三个动作各自真写一次**：新增 -> 删除 -> 修改，每一步都从**上一步的产物**读。

    判据（每步读回那张表）：触发器 2 -> 3 -> 2 -> 2 条，且**私有字段一字不变**（Grade 7）。
    """
    case = _container_with_trigger_rows(tmp_path)
    before = _sha(case)
    added = tmp_path / "t-add.unity3d"
    assert _run(case, _trigger_tail("--trigger-add", "--trigger", "mission_c",
                                    "--trigger-loc-key", "loc_c", "--trigger-count", "2"),
                write=True, out=added, project_root=tmp_path) == mv.EXIT_DONE
    names = [t["Trigger"] for t in _items(added)[1].triggers]
    assert names == ["mission_a", "mission_b", "mission_c"], f"新增没追加在末尾：{names}"
    removed = tmp_path / "t-remove.unity3d"
    assert _run(added, _trigger_tail("--trigger-remove", "1"),
                write=True, out=removed, project_root=tmp_path) == mv.EXIT_DONE
    names = [t["Trigger"] for t in _items(removed)[1].triggers]
    assert names == ["mission_a", "mission_c"], f"删除删错了：{names}"
    changed = tmp_path / "t-set.unity3d"
    assert _run(removed, _trigger_tail("--trigger-set", "1", "--trigger-count", "9"),
                write=True, out=changed, project_root=tmp_path) == mv.EXIT_DONE
    got = _items(changed)[1].triggers
    assert [t["Trigger"] for t in got] == ["mission_a", "mission_c"]
    assert got[1]["Count"] == 9 and got[1]["DescriptionLocKey"] == "loc_c", got
    assert _sha(case) == before, "源件被动过了"
    for step in (added, removed, changed):
        assert _rows(step)[1]["Grade"] == 7, f"{step.name} 把非第一行的私有字段洗了"
        assert _rows(step)[0]["Grade"] == 3


def test_d_add_with_an_explicit_trigger_writes_the_three_fields(
    container: Path, tmp_path: Path
) -> None:
    """`add` 那一支：三格全给 ⇒ 新条就带那一条（⛔ 不再只能照抄 / 置空）。"""
    out = tmp_path / "with-trigger.unity3d"
    assert _run(container, _add_tail(), write=True, out=out, project_root=tmp_path,
                trigger="mission_x", trigger_loc_key="loc_x", trigger_count=4) == mv.EXIT_DONE
    assert _items(out)[-1].triggers == (
        {"Trigger": "mission_x", "DescriptionLocKey": "loc_x", "Count": 4},
    )


def test_d_add_with_a_full_trio_still_refuses_drop_triggers(
    container: Path, tmp_path: Path
) -> None:
    """**负控**：`--drop-triggers`（空表）与三格自定义互斥 —— rc 2、盘上零改动。"""
    before = _sha(container)
    out = tmp_path / "conflict.unity3d"
    assert _run(container, _add_tail(), write=True, out=out, project_root=tmp_path,
                drop_triggers=True, trigger="mission_x", trigger_loc_key="loc_x",
                trigger_count=4) == mv.EXIT_UNUSABLE
    assert _sha(container) == before and not out.exists()


# --------------------------------------------------------------------------- #
# E · 负控（每条都必须 rc 2 且盘上零改动）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tail,needle",
    [
        (("add", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--reference", str(SECOND_ID)), "已经在这张表里了"),
        (("add", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(NEW_ID),
          "--reference", "9999"), "没有 Id=9999"),
        (("add", "--path-id", str(PID), "--unit", "999", "--item-id", str(NEW_ID),
          "--reference", str(FIRST_ID)), "Skins"),
        (("remove", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", "9999"),
         "没有 Id=9999"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID)),
         "空改动"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--is-default", "1"), "空改动"),
        # —— 触发器那一支的负控（每一条都必须 rc 2 且零改动）——
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-remove", "0"), "没有下标 0"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-set", "0"), "空改动"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-set", "0", "--trigger-count", "9"), "没有下标 0"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-add", "--trigger", "t", "--trigger-loc-key", "k"), "--trigger-count"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-remove", "0", "--trigger", "t"), "只认下标"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-count", "9"), "却没给动作"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-add", "--trigger-set", "0"), "一次只许一个触发器动作"),
        (("update", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(FIRST_ID),
          "--trigger-set", "0", "--trigger-count", "9", "--free", "0"), "不能与"),
        (("add", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(NEW_ID),
          "--reference", str(FIRST_ID), "--trigger", "t"), "三格全给"),
        (("add", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(NEW_ID),
          "--reference", str(FIRST_ID), "--drop-triggers", "--trigger", "t",
          "--trigger-loc-key", "k", "--trigger-count", "1"), "互斥"),
    ],
    ids=["duplicate-id", "missing-reference", "unit-not-in-table", "remove-missing",
         "update-no-field", "update-same-value",
         "trigger-remove-missing", "trigger-set-no-field", "trigger-set-out-of-range",
         "trigger-add-partial", "trigger-remove-with-values", "trigger-value-without-action",
         "trigger-two-actions", "trigger-mixed-with-fields",
         "add-partial-trio", "add-drop-plus-trio"],
)
def test_e_guard_failures_exit_unusable_and_touch_nothing(
    container: Path, capsys: pytest.CaptureFixture[str], tail: tuple[str, ...], needle: str
) -> None:
    before = _sha(container)
    code = _run(container, tail)
    assert code == mv.EXIT_UNUSABLE, code
    err = capsys.readouterr().err
    assert needle in err, err
    assert _sha(container) == before, "守卫不过却改了盘"


def test_e_a_negative_trigger_count_is_refused(tmp_path: Path) -> None:
    """**负控**：Count 在真件类型树里是 unsigned int ⇒ 负数 rc 2、盘上零改动。

    ⚠ 这一条要**带触发器的**那个夹具：普通夹具第 0 条皮肤没有触发器，
    会先撞「下标越界」那道闸（读数就变成另一件事了）。
    """
    case = _container_with_trigger_rows(tmp_path)
    before = _sha(case)
    assert _run(case, _trigger_tail("--trigger-set", "0", "--trigger-count", "-1")) == (
        mv.EXIT_UNUSABLE
    )
    assert _sha(case) == before


def test_e_a_wrong_path_id_is_refused(container: Path) -> None:
    """`--path-id` 指到同节点里别的对象 ⇒ 拒绝（⛔ 不猜那颗 UserItemsConfig 在哪）。"""
    before = _sha(container)
    tail = ("remove", "--path-id", str(OTHER_PID), "--unit", str(UNIT), "--item-id", str(SECOND_ID))
    assert _run(container, tail) == mv.EXIT_UNUSABLE
    assert _sha(container) == before


def test_e_a_missing_container_exits_unusable(tmp_path: Path) -> None:
    tail = ("remove", "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(SECOND_ID))
    assert _run(tmp_path / "nope.unity3d", tail) == mv.EXIT_UNUSABLE


# --------------------------------------------------------------------------- #
# F · 出口
# --------------------------------------------------------------------------- #
def test_f_json_mode_keeps_stdout_to_one_document(
    container: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _sha(container)
    assert _run(container, _add_tail(), json=True) == mv.EXIT_DONE
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["group"] == "visible" and document["verb"] == "add"
    assert document["path_id"] == PID and document["unit_id"] == UNIT
    assert document["count_before"] == 2 and document["count_after"] == 3
    assert document["touched"] == [NEW_ID]
    assert document["dry_run"] is True and document["target"] is None
    assert document["reverse_delta"] is None
    assert _sha(container) == before


def test_f_json_carries_the_reverse_delta_record_and_its_honest_edge(
    container: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` 里有反向 Δ 的 record；**原始字节不在里面**（只报 size ＋ sha256）—— 如实记。"""
    out = tmp_path / "visible.unity3d"
    assert _run(container, _add_tail(), write=True, out=out, json=True,
                project_root=tmp_path) == mv.EXIT_DONE
    document = json.loads(capsys.readouterr().out)
    record = document["reverse_delta"]
    assert record["path_id"] == PID and record["unit_id"] == UNIT
    assert record["container"] == str(out) and record["origin"] == str(container)
    assert "old_bytes" not in record, "record 里出现原始字节了 —— 口径变了，核一遍"
    assert document["reverse_old_bytes_size"] == record["old_size"] > 0
    assert len(document["reverse_old_bytes_sha256"]) == 64


def test_f_an_unknown_verb_exits_unusable(container: Path) -> None:
    args = _parse("material", "visible", "remove", "--container", str(container),
                  "--path-id", str(PID), "--unit", str(UNIT), "--item-id", str(SECOND_ID))
    args.visible_action = "swap"
    assert mv.run(args) == mv.EXIT_UNUSABLE


# --------------------------------------------------------------------------- #
# G · 尺子正控：rc 1 那一档必须能红的
# --------------------------------------------------------------------------- #
def test_g_the_product_audit_goes_dirty_on_a_size_mismatch(
    container: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**正控**：把产物读数的大小改一格 ⇒ 产物核对必须红（rc 1）；不弄坏 ⇒ rc 0。"""
    out = tmp_path / "visible.unity3d"
    real = mv.ui.install

    def patched(plan: ui.UserItemsPlan, target: object, **kwargs: object) -> ui.InstallResult:
        result = real(plan, target, **kwargs)
        bogus = dataclasses.replace(result.report, new_size=result.report.new_size + 1)
        return dataclasses.replace(result, report=bogus)

    assert _run(container, _add_tail(), write=True, out=out,
                project_root=tmp_path) == mv.EXIT_DONE
    monkeypatch.setattr(mv.ui, "install", patched)
    out2 = tmp_path / "visible2.unity3d"
    assert _run(container, _add_tail(), write=True, out=out2,
                project_root=tmp_path) == mv.EXIT_DIRTY


# --------------------------------------------------------------------------- #
# H · 分派
# --------------------------------------------------------------------------- #
def test_h_dispatch_routes_visible_to_this_module(container: Path) -> None:
    args = _parse("material", "visible", "remove", "--container", str(container),
                  "--path-id", str(PID), "--unit", str(UNIT), "--item-id", "9999")
    assert mv.dispatch(args) == mv.EXIT_UNUSABLE


def test_h_dispatch_delegates_the_other_two_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(args: argparse.Namespace) -> int:
        seen["material_group"] = args.material_group
        return 5

    monkeypatch.setattr(me, "run", fake_run)
    args = _parse("material", "skin", "remove", "--unit", "RU_T72B", "--skin-id", "2233")
    assert mv.dispatch(args) == 5
    assert seen["material_group"] == "skin"



# --------------------------------------------------------------------------- #
# I · task-17：⑥ 层的 range 反向 Δ 落进补丁柜
# --------------------------------------------------------------------------- #
REPO = Path(__file__).resolve().parents[1]


def _run_subprocess(*argv: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """**另起一个进程**跑 CLI —— J1 要的就是「进程一退还撤得回来」。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "ba_mod_maker.cli", *argv],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _restore(project: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """另起进程跑**唯一还原入口** `assets restore`（⛔ 本组不另开一个 restore 动词）。

    `--game` 是给「补丁柜跟游戏同盘」那道红线用的：这里没有游戏目录，
    就拿 tmp_path 当参照物（帮助里写的就是「只用来核对补丁柜跟游戏同盘」）。
    """
    return _run_subprocess(
        "assets", "restore", "--project-root", str(project), "--game", str(tmp_path),
        cwd=tmp_path,
    )


def _patches(project: Path) -> Path:
    return project / "patches"


def test_i_write_records_a_range_patch_under_project_root(
    container: Path, tmp_path: Path
) -> None:
    """`--write --project-root` -> 补丁柜里多一条 **range** 目标（不是整容器、不是整文件）。"""
    out = tmp_path / "visible.unity3d"
    project = tmp_path / "proj"
    assert _run(container, _add_tail(), write=True, out=out,
                project_root=project) == mv.EXIT_DONE
    raw = json.loads((_patches(project) / "install.rmod.json").read_text(encoding="utf-8"))
    assert raw["format"] == reverse_delta.RANGE_FORMAT_VERSION, "含 range 该写格式 3"
    assert len(raw["targets"]) == 1
    target = raw["targets"][0]
    assert target["kind"] == "range"
    assert target["node_path"] == ui.NODE_PATH and target["path_id"] == PID
    assert target["old_size"] == len(ui.read_container_config(container, PID).payload)
    assert Path(target["path"]) == out, "range 目标指的该是产物"
    assert target["label"].startswith("可见性层新增")


def test_i_a_new_process_restores_the_product_bit_for_bit(
    container: Path, tmp_path: Path
) -> None:
    """**J1（--out）**：真写 -> 另起进程 `assets restore` -> 产物逐位 == 源件那一份。"""
    source_sha = _sha(container)
    out = tmp_path / "visible.unity3d"
    project = tmp_path / "proj"
    assert _run(container, _add_tail(), write=True, out=out,
                project_root=project) == mv.EXIT_DONE
    assert _sha(out) != source_sha
    back = _restore(project, tmp_path)
    assert back.returncode == 0, back.stdout + back.stderr
    assert _sha(out) == source_sha, "另起进程还原之后没逐位回到源件那一份"
    assert not (_patches(project) / "install.rmod.json").exists(), "还原成功该删补丁"


def test_i_a_new_process_restores_the_container_in_place_bit_for_bit(tmp_path: Path) -> None:
    """**J1（--in-place）**：真件那条链 —— 同一个文件写进去、另起进程滚回来，逐位相同。"""
    case = tmp_path / "globalgamemanagers.unity3d"
    case.write_bytes(
        _container(_sf([(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(_tree()))]))
    )
    before = _sha(case)
    project = tmp_path / "proj"
    assert _run(case, _add_tail(), write=True, in_place=True,
                project_root=project) == mv.EXIT_DONE
    assert _sha(case) != before
    raw = json.loads((_patches(project) / "install.rmod.json").read_text(encoding="utf-8"))
    assert Path(raw["targets"][0]["path"]) == case, "原地那条链必须记容器本身，不是临时文件"
    back = _restore(project, tmp_path)
    assert back.returncode == 0, back.stdout + back.stderr
    assert _sha(case) == before, "另起进程还原之后容器没逐位回到改前"


def test_i_status_prints_the_range_patch_in_plain_words(
    container: Path, tmp_path: Path
) -> None:
    """`assets status` 把这一笔印成**人话**（标签 / pathID / 单位 / 那一段 / 补丁体积）。"""
    out = tmp_path / "visible.unity3d"
    project = tmp_path / "proj"
    assert _run(container, _add_tail(), write=True, out=out,
                project_root=project) == mv.EXIT_DONE
    status = _run_subprocess("assets", "status", "--project-root", str(project), cwd=tmp_path)
    assert status.returncode == 0, status.stdout + status.stderr
    text = status.stdout
    for needle in ("可见性层新增", "pathID=" + str(PID), "单位 " + str(UNIT),
                   "那一段", "补丁存原始字节"):
        assert needle in text, f"status 里缺 {needle!r}：\n{text}"


def test_i_the_patch_is_only_the_replaced_range_not_the_container(
    container: Path, tmp_path: Path
) -> None:
    """**J3**：补丁体积 = 那一颗对象的字节数（⛔ 不是整个容器）。"""
    out = tmp_path / "visible.unity3d"
    project = tmp_path / "proj"
    assert _run(container, _add_tail(), write=True, out=out,
                project_root=project) == mv.EXIT_DONE
    blob_bytes = (_patches(project) / "install.rmod.bin").stat().st_size
    json_bytes = (_patches(project) / "install.rmod.json").stat().st_size
    payload = ui.read_container_config(container, PID).payload
    with ufs.UfsReader(container) as reader:
        node_size = reader.node(ui.NODE_PATH).size
    # ⚠ 别拿**容器文件**大小当分母：合成件的填充对象是 FFFF…，LZ4 一压就剩几百字节
    #    （容器 1 KB 而节点 200 KB）。真正的分母是**节点**（= 我们改了的那份数据）。
    assert blob_bytes == len(payload), "补丁 blob 里应当是**恰好那一段**原始字节"
    assert json_bytes < 4096, f"补丁 json {json_bytes:,} B 只该是常数级元数据"
    assert blob_bytes < node_size // 10, (
        f"补丁 {blob_bytes:,} B 快赶上整个节点 {node_size:,} B 了 —— 像把容器存了进来"
    )


def test_i_a_tampered_range_makes_restore_red_and_touch_nothing(
    container: Path, tmp_path: Path
) -> None:
    """**J2 正控**：把补丁里那段原始字节改一格（并同步 blob 的 sha256）⇒ 还原**必须红**。

    ⚠ 只改 blob、不改 `blob_sha256` 的话，读盘那一关就先拦了（那是另一条用例）。
    这里要测的是**重放自证**：补丁读得进来，但放回去的文件 sha256 对不上
    ⇒ 拒绝落地、一个字节都不动。
    """
    out = tmp_path / "visible.unity3d"
    project = tmp_path / "proj"
    assert _run(container, _add_tail(), write=True, out=out,
                project_root=project) == mv.EXIT_DONE
    installed = _sha(out)
    json_path = _patches(project) / "install.rmod.json"
    blob_path = _patches(project) / "install.rmod.bin"
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    ref = raw["targets"][0]["old_bytes"]
    blob = bytearray(blob_path.read_bytes())
    blob[ref["offset"]] ^= 0xFF
    blob_path.write_bytes(bytes(blob))
    raw["blob_sha256"] = hashlib.sha256(bytes(blob)).hexdigest()
    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")

    back = _restore(project, tmp_path)
    assert back.returncode != 0, "补丁被改过还还原成功了 —— 这把尺子是假的"
    assert _sha(out) == installed, "拒绝还原时居然动了文件"
    assert json_path.is_file(), "拒绝还原时不该把补丁删掉"


def test_i_dry_run_creates_no_patch_store(container: Path, tmp_path: Path) -> None:
    """干跑（不带 `--write`）连补丁柜都不该建出来。"""
    before = _sha(container)
    project = tmp_path / "proj"
    assert _run(container, _add_tail(), project_root=project) == mv.EXIT_DONE
    assert _sha(container) == before
    assert not _patches(project).exists(), "干跑居然建了补丁柜"


def test_i_write_refuses_when_the_patch_store_is_on_another_drive(
    container: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**红线**：补丁柜必须跟随被改的那份文件所在的盘 —— ⛔ 别等 4 分钟写完才发现。

    没有 `--project-root` 时落点是**当前目录**；pytest 跑在仓根（本机 D:），
    而合成容器在 tmp（本机 C:）⇒ 不同盘 ⇒ 当场 rc 2、零字节。
    """
    if reverse_delta._drive_of(container) == reverse_delta._drive_of(Path(__file__).parent):
        pytest.skip("这台机器上 cwd 与 tmp 同盘 —— 这条红线在这里测不了")
    before = _sha(container)
    out = container.parent / "visible.unity3d"
    assert _run(container, _add_tail(), write=True, out=out) == mv.EXIT_UNUSABLE
    assert "盘" in capsys.readouterr().err
    assert not out.exists(), "拦下了却还是写了产物"
    assert _sha(container) == before
