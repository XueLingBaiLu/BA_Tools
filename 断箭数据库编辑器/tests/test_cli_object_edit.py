"""T3 ＋ 2026-10-12 变长档 · `object set` / `object add` / `object delete`：CLI 三个原子动词。

**判据一行**：`pytest tests\test_cli_object_edit.py -q -n 0`
-> 收集数 **从 0 变 N passed**（本文件新建前不存在）。三块：

1. **J4（CLI 侧）**：`object set` / `object delete` 各自跑出来的 `AssetPlan`
   **只含一个动词**（另两格必须是空元组）；
2. **J-T3-3**：`object delete` 对**不存在**的 pathID ⇒ 报错退出（**非 0**，实测 2），
   ⛔ 不许静默成功、也不许写下一份"什么都没有"的计划；
3. **J3 量程**：`object` 这个新入口必须落进**既有**那把尺子
   （`tests/test_no_swap_wording.py` 的面 ① = 递归 `format_help()`）的量程里 ——
   ⛔ 不再写第二把尺子（AGENTS §5「逻辑只有一份」）。

| 面 | 判据 | 通过线 |
|---|---|---|
| 解析器形状 | 只有 set / add / delete 三个动作；选项名里没有 swap / replace / --to | 命中数 0 |
| `parse_field_path` | `a.b` / `a[0]` / `a[key]` 三种写法 ＋ 七种坏写法 | 逐条相等 / 逐条能红 |
| `resolve_field` | 走得到 -> 值；走不到 -> AssetError 且**印出可用的键 / 名字** | 能红 |
| `coerce_field_value` | 类型照**旧值**那一格（布尔 / 整数 / 小数 / 字符串 / null） | 逐条相等 |
| J4（CLI 侧） | 一次动作 -> `AssetPlan` 只有一格非空 | 另两格 == () |
| J4（尺子正控） | 合成一份"删除＋新增"的计划 ⇒ 尺子**必须红**（并把那次红印出来） | 能红 |
| 端到端 | 合成包上 delete / set -> 退出码 ＋ Δ 计划里的 kind | 0 / 1 / 2 |

## 为什么端到端跑在**合成**包上（照 `tests/test_writeback_l1.py` 的口径）

那个文件头逐字写着：合成容器的 SerializedFile `type_count = 0`、**没有类型树**，
所以 UnityPy 解不出 typetree。于是：

* `object delete` 走 `core/asset/ufs.py`（产品自己的容器读侧，**不用 UnityPy**）
  ⇒ 合成包上是**全程真跑**（读字节 -> 记账本 -> 写 Δ 计划 -> 退出码）；
* `object set` 要 typetree ⇒ 用例把**合成 typetree** 从 `object_edit.read_typetree`
  这个**注入点**喂进去（与真实现同一个返回类型：`Mapping`）。

**真包那一条**不在 pytest 里（要读游戏目录里的文件、规模不可控）。实测读数见文件末尾那段。
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import json
import re
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import reverse_delta
from ba_mod_maker.cli import __main__ as cli_main
from ba_mod_maker.cli import finder
from ba_mod_maker.cli import object_edit as oe
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.edit.asset_changeset import AssetEdit, AssetPlan
from ba_mod_maker.core.edit.asset_plan_io import plan_path
from ba_mod_maker.core.edit.asset_refs import AssetRef, FieldPath
from ba_mod_maker.core.errors import AssetError
from test_no_swap_wording import banned_hits, iter_help_texts
from test_writeback_l1 import _blob, _build_container, _build_sf
from test_writeback_newobj import _build_container as _add_container
from test_writeback_newobj import _build_sf as _add_sf

#: `object` 子命令的**短帮助**里那一句。它是"新入口进了 J3 尺子量程"的记号：
#: 只在 `object` 挂在 parser 上时才会出现在 `format_help()` 里。
OBJECT_HELP_MARK = "修改 / 新增 / 删除包里的一个对象"

#: 一个真实材质的 typetree 形状（`"m_SavedProperties.m_TexEnvs"` 是 `skin_install._tex_envs`
#: 读的那一层；槽的值是一对 `[名字, 值]`，值里那个 `m_Texture` 就是 PPtr）。
MATERIAL_TREE: dict[str, object] = {
    "m_Name": "RU_BMD-2 1",
    "m_SavedProperties": {
        "m_TexEnvs": [
            ["Layer_A97CDC25", {"m_Texture": {"m_FileID": 0, "m_PathID": 58076}}],
            ["_BumpMap", {"m_Texture": {"m_FileID": 0, "m_PathID": 1234}}],
        ]
    },
}
#: 上面那棵树里一格的真路径（**完整**写法；设计稿 §5 把前面那一截省着写）。
SLOT_PATH = "m_SavedProperties.m_TexEnvs[Layer_A97CDC25].second.m_Texture.m_PathID"
SLOT_PID = 58076


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _object_parser() -> argparse.ArgumentParser:
    """一个只挂 `object` 的最小 parser（`__main__.py` 由 Lead 独占，不碰它）。"""
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    oe.add_parser(sub)
    return parser


def _parse(*argv: str) -> argparse.Namespace:
    return _object_parser().parse_args(list(argv))


def _synth_bundle(tmp_path: Path) -> Path:
    """三个对象的合成 UnityFS 容器（构造器借 `test_writeback_l1`，逐字段对齐）。"""
    objects = [
        (1001, _blob(b"A", 1300)),
        (1002, _blob(b"B", 2000)),
        (1003, _blob(b"C", 2000)),
    ]
    path = tmp_path / "synth_object.bundle"
    path.write_bytes(_build_container(_build_sf(objects)))
    return path


def _planned(path: Path) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return list(document["edits"])


# --------------------------------------------------------------------------- #
# ① 解析器形状：两个动词、⛔ 没有"以 B 顶替 A"的入口
# --------------------------------------------------------------------------- #
def _walk(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]:
    out = [parser]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                out.extend(_walk(child))
    return out


def _entry_strings(parser: argparse.ArgumentParser) -> list[str]:
    """这个 parser 树上**所有能敲的东西**：选项名 ＋ 子命令名（标识符面）。"""
    names: list[str] = []
    for node in _walk(parser):
        for action in node._actions:
            names.extend(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                names.extend(str(name) for name in action.choices)
    return names


def test_object_has_exactly_the_three_atomic_verbs() -> None:
    """三个原子动词都挂在 `object` 上；第四个（地址线）仍排在设计稿 §7 之后。"""
    assert _parse("object", "set", "--bundle", "b.bundle", "--path-id", "1",
                  "--field", "a", "--value", "1").object_action == "set"
    assert (
        _parse("object", "delete", "--bundle", "b.bundle", "--path-id", "1").object_action
        == "delete"
    )
    spec = _parse("object", "add", "--bundle", "b.bundle", "--from", "add.json")
    assert spec.object_action == "add"
    assert spec.spec == "add.json"

    with pytest.raises(SystemExit) as excinfo:
        _parse("object", "address", "--bundle", "b.bundle")
    assert excinfo.value.code == 2, "地址线还没定形状，不该有入口"


def test_object_offers_no_replacement_entry() -> None:
    """⛔ 不许有 `object swap` / `--replace` / `--to` 这类"以 B 顶替 A"的入口。

    量的是**标识符面**（选项名 ＋ 子命令名）—— 也正是 J3 尺子**明确不算**的那一面
    （它只量用户看得见的散文）。两边合起来才是完整的"无「换」入口"。
    """
    names = _entry_strings(_object_parser())
    banned = [name for name in names if re.search(r"swap|replace|(^|-)to$", name, re.IGNORECASE)]
    assert banned == [], f"object 上出现了顶替式入口：{banned}（全部：{sorted(set(names))}）"
    assert {"set", "add", "delete"} <= set(names), f"三个动词没挂上：{sorted(set(names))}"


# --------------------------------------------------------------------------- #
# ② `parse_field_path`：纯函数，三种写法 ＋ 坏写法必须能红
# --------------------------------------------------------------------------- #
def test_parse_field_path_reads_the_three_spellings() -> None:
    assert [(s.kind, s.value) for s in oe.parse_field_path("a.b")] == [
        ("attr", "a"),
        ("attr", "b"),
    ]
    assert [(s.kind, s.value) for s in oe.parse_field_path("a[0]")] == [
        ("attr", "a"),
        ("index", 0),
    ]
    assert [(s.kind, s.value) for s in oe.parse_field_path("a[Layer_A97CDC25]")] == [
        ("attr", "a"),
        ("key", "Layer_A97CDC25"),
    ]


def test_parse_field_path_walks_the_real_material_path() -> None:
    """设计稿 §5 那条路径的**完整**写法：四跳，一跳不差。"""
    steps = oe.parse_field_path(SLOT_PATH)
    assert [(s.kind, s.value) for s in steps] == [
        ("attr", "m_SavedProperties"),
        ("attr", "m_TexEnvs"),
        ("key", "Layer_A97CDC25"),
        ("attr", "second"),
        ("attr", "m_Texture"),
        ("attr", "m_PathID"),
    ]
    assert oe.render_steps(steps) == SLOT_PATH, "画回去必须与输入逐字相同"


def test_parse_field_path_accepts_a_quoted_key() -> None:
    assert oe.parse_field_path('a["Layer A"]')[1] == oe.Step(kind="key", value="Layer A")
    assert oe.parse_field_path("a['x.y']")[1] == oe.Step(kind="key", value="x.y")


#: 十一种坏写法（第七条 @@a['x@@ 是"引号没闭上"，第十一条是"键里还带方括号"）。
_BAD_PATHS: tuple[str, ...] = (
    "",
    "   ",
    "a..b",
    "a.",
    ".a",
    "a[",
    "a[]",
    "a[0]b",
    "a]b",
    "a['x",
    "a['x[1]']",
)


@pytest.mark.parametrize("bad", _BAD_PATHS)
def test_parse_field_path_rejects_every_malformed_spelling(bad: str) -> None:
    """七种坏写法逐条能红 —— 解析器**不许**"猜一个能用的"。"""
    with pytest.raises(AssetError):
        oe.parse_field_path(bad)


# --------------------------------------------------------------------------- #
# ③ `resolve_field`：走得到给值，走不到**印出可用的键**
# --------------------------------------------------------------------------- #
def test_resolve_field_walks_the_nested_path() -> None:
    assert oe.resolve_field(MATERIAL_TREE, oe.parse_field_path(SLOT_PATH)) == SLOT_PID


def test_resolve_field_agrees_on_index_and_key_spellings() -> None:
    """`[0]` 与 `[Layer_A97CDC25]` 说的是同一格（pair 表两种取法）。"""
    by_key = oe.resolve_field(
        MATERIAL_TREE, oe.parse_field_path("m_SavedProperties.m_TexEnvs[Layer_A97CDC25].second")
    )
    by_index = oe.resolve_field(
        MATERIAL_TREE, oe.parse_field_path("m_SavedProperties.m_TexEnvs[0].second")
    )
    assert by_key == by_index


def test_resolve_field_prints_the_available_names_when_the_slot_is_missing() -> None:
    """走不到时的**红必须可读**：消息里要有可用的槽名（否则等于让人猜）。"""
    with pytest.raises(AssetError) as excinfo:
        oe.resolve_field(
            MATERIAL_TREE, oe.parse_field_path(SLOT_PATH.replace("Layer_A97CDC25", "Nope"))
        )
    text = str(excinfo.value)
    assert "Layer_A97CDC25" in text and "_BumpMap" in text, text


def test_resolve_field_prints_the_available_keys_when_a_name_is_missing() -> None:
    with pytest.raises(AssetError) as excinfo:
        oe.resolve_field(MATERIAL_TREE, oe.parse_field_path("m_NoSuchThing"))
    assert "m_SavedProperties" in str(excinfo.value), str(excinfo.value)


def test_resolve_field_rejects_an_index_out_of_range() -> None:
    with pytest.raises(AssetError) as excinfo:
        oe.resolve_field(MATERIAL_TREE, oe.parse_field_path("m_SavedProperties.m_TexEnvs[9]"))
    assert "越界" in str(excinfo.value), str(excinfo.value)


def test_resolve_field_walks_the_pair_halves_by_name() -> None:
    """`.first` / `.second` 是 Unity pair 节点的两个半边（本模块认这组名字）。"""
    pair = [["Layer_A97CDC25", {"m_PathID": 7}]]
    assert oe.resolve_field(pair, oe.parse_field_path("[0].first")) == "Layer_A97CDC25"
    assert oe.resolve_field(pair, oe.parse_field_path("[0].second")) == {"m_PathID": 7}


# --------------------------------------------------------------------------- #
# ④ `coerce_field_value`：类型照**旧值**那一格
# --------------------------------------------------------------------------- #
def test_coerce_follows_the_old_value_type() -> None:
    assert oe.coerce_field_value(58076, "58077") == 58077
    assert oe.coerce_field_value(1.5, "2.5") == 2.5
    assert oe.coerce_field_value("BMD-2_1", "BMD-2_3") == "BMD-2_3"
    assert oe.coerce_field_value(True, "false") is False
    assert oe.coerce_field_value(False, "1") is True
    assert oe.coerce_field_value(None, "9") == 9  # null：纯数字优先（照 _coerce 的口径）
    assert oe.coerce_field_value(None, "x") == "x"


@pytest.mark.parametrize("old,text", [(58076, "Layer_A"), (1.5, "x"), (True, "maybe")])
def test_coerce_rejects_a_value_that_does_not_fit_the_cell(old: object, text: str) -> None:
    with pytest.raises(AssetError):
        oe.coerce_field_value(old, text)


def test_coerce_refuses_non_scalar_cells() -> None:
    with pytest.raises(AssetError) as excinfo:
        oe.coerce_field_value({"m_FileID": 0}, "1")
    assert "只认标量" in str(excinfo.value), str(excinfo.value)


# --------------------------------------------------------------------------- #
# ⑤ J4 尺子（纯函数）＋ **正控：合成一份"删除＋新增"的计划 ⇒ 必须红**
# --------------------------------------------------------------------------- #
def plan_verbs(plan: AssetPlan) -> tuple[str, ...]:
    """这一次动作发了哪几个原子动词（只数非空的格子）。"""
    return tuple(
        name
        for name, edits in (("set", plan.sets), ("add", plan.adds), ("delete", plan.deletes))
        if edits
    )


def composite_violations(plan: AssetPlan) -> list[str]:
    """**J4 尺子（纯函数）**：一次动作**同时**产生删除与新增 ⇒ 违规。

    违规时返回**话术**（空列表 = 合规）而不是 bool —— 红的那一次要能直接贴出来。
    依据：ADR 0008 决定 2「一次动作同时产生 `ObjectRemoval` ＋ `NewObject` ⇒ 它就是「换」」。
    """
    verbs = set(plan_verbs(plan))
    if {"add", "delete"} <= verbs:
        return [
            f"这一次动作同时发了 {sorted(verbs)}："
            "删除 ＋ 新增 = 以 B 顶替 A（ADR 0008 决定 2 禁止）"
        ]
    return []


def test_j4_ruler_goes_red_on_a_synthetic_delete_plus_add_plan() -> None:
    """**J4 正控**：先合成一个已知的"删除＋新增"计划，尺子**必须红**（把那次红印出来）。"""
    plan = AssetPlan(
        deletes=(AssetEdit(kind="delete", ref=AssetRef(Path("x.bundle"), 1002), old=b"B" * 8),),
        adds=(AssetEdit(kind="add", ref=AssetRef(Path("x.bundle"), 2001), old=None, new=b"N" * 8),),
    )
    violations = composite_violations(plan)
    print("\n[J4 正控] 合成计划（删除 1002 ＋ 新增 2001）->", violations)
    assert violations, "合成的「删除＋新增」计划没被判违规 —— 这把尺子恒绿，等于假的"


def test_j4_ruler_stays_green_on_each_single_verb_plan() -> None:
    """负控：只发一个动词的计划不许报红（否则尺子只会恒红）。"""
    only_delete = AssetPlan(
        deletes=(AssetEdit(kind="delete", ref=AssetRef(Path("x.bundle"), 1002), old=b"B"),)
    )
    only_add = AssetPlan(
        adds=(AssetEdit(kind="add", ref=AssetRef(Path("x.bundle"), 2001), old=None, new=b"N"),)
    )
    assert composite_violations(only_delete) == []
    assert composite_violations(only_add) == []


def test_the_cli_builds_a_plan_with_exactly_one_verb() -> None:
    """**J4（CLI 侧）**：set / delete 各自跑出来的 `AssetPlan` 只有一格非空。"""
    set_ref = AssetRef(Path("x.bundle"), 58076, FieldPath(SLOT_PATH))
    set_plan = oe.build_plan(verb="set", ref=set_ref, baseline=lambda _ref: SLOT_PID, value="58077")
    assert plan_verbs(set_plan) == ("set",)
    assert composite_violations(set_plan) == []
    assert set_plan.adds == () and set_plan.deletes == ()

    delete_ref = AssetRef(Path("x.bundle"), 1002)
    delete_plan = oe.build_plan(verb="delete", ref=delete_ref, baseline=lambda _ref: b"B" * 8)
    assert plan_verbs(delete_plan) == ("delete",)
    assert composite_violations(delete_plan) == []
    assert delete_plan.sets == () and delete_plan.adds == ()


def test_build_plan_refuses_a_verb_that_is_not_in_this_cut() -> None:
    with pytest.raises(AssetError):
        oe.build_plan(verb="add", ref=AssetRef(Path("x.bundle"), 1), baseline=lambda _ref: None)


# --------------------------------------------------------------------------- #
# ⑥ 端到端：合成包上真跑（delete 不用 UnityPy；set 从注入点喂合成 typetree）
# --------------------------------------------------------------------------- #
def test_delete_reads_the_payload_and_writes_a_one_verb_plan(tmp_path: Path) -> None:
    bundle = _synth_bundle(tmp_path)
    assert len(oe.read_object_bytes(bundle, 1002) or b"") == 2000
    assert oe.read_object_bytes(bundle, 9999) is None

    code = oe.run(
        _parse("object", "delete", "--bundle", str(bundle), "--path-id", "1002",
               "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    edits = _planned(plan_path(tmp_path))
    assert [edit["kind"] for edit in edits] == ["delete"]
    assert edits[0]["path_id"] == 1002
    assert edits[0]["old"]["bytes"] == 2000
    assert re.fullmatch(r"[0-9a-f]{16}", str(edits[0]["old"]["sha256_16"]))


def test_delete_of_a_missing_path_id_exits_non_zero_and_writes_nothing(tmp_path: Path) -> None:
    """**J-T3-3**：pathID 不在包里 ⇒ 非 0 退出，且**一个字节都不写**。"""
    bundle = _synth_bundle(tmp_path)
    code = oe.run(
        _parse("object", "delete", "--bundle", str(bundle), "--path-id", "9999",
               "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_UNUSABLE, code
    assert code != 0
    assert not plan_path(tmp_path).exists(), "报错了还写下一份计划"


def test_set_takes_the_baseline_from_the_injected_typetree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _synth_bundle(tmp_path)
    monkeypatch.setattr(oe, "read_typetree", lambda _bundle, _pid: MATERIAL_TREE)
    code = oe.run(
        _parse("object", "set", "--bundle", str(bundle), "--path-id", "1002",
               "--field", SLOT_PATH, "--value", "58077", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    edits = _planned(plan_path(tmp_path))
    assert [edit["kind"] for edit in edits] == ["set"]
    assert (edits[0]["old"], edits[0]["new"]) == (SLOT_PID, 58077)
    # 旧值真的来自**注入的那棵树**（不是编的）：同一个注入点换一棵树，旧值跟着变。
    monkeypatch.setattr(oe, "read_typetree", lambda _bundle, _pid: {"m_SavedProperties": {}})
    other = oe.run(
        _parse("object", "set", "--bundle", str(bundle), "--path-id", "1002",
               "--field", SLOT_PATH, "--value", "1", "--dry-run", "--project-root", str(tmp_path))
    )
    assert other == oe.EXIT_UNUSABLE, other


def test_set_to_the_same_value_is_not_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改回原值 ⇒ 不是一处改动（照 `changeset.py` 的 no-op 口径）⇒ 退出码 1、不写盘。"""
    bundle = _synth_bundle(tmp_path)
    monkeypatch.setattr(oe, "read_typetree", lambda _bundle, _pid: MATERIAL_TREE)
    code = oe.run(
        _parse("object", "set", "--bundle", str(bundle), "--path-id", "1002",
               "--field", SLOT_PATH, "--value", str(SLOT_PID), "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_NOTHING, code
    assert not plan_path(tmp_path).exists()


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _synth_bundle(tmp_path)
    monkeypatch.setattr(oe, "read_typetree", lambda _bundle, _pid: MATERIAL_TREE)
    code = oe.run(
        _parse("object", "set", "--bundle", str(bundle), "--path-id", "1002",
               "--field", SLOT_PATH, "--value", "1", "--dry-run", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    assert not plan_path(tmp_path).exists(), "--dry-run 写盘了"


def test_a_bad_field_path_exits_unusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _synth_bundle(tmp_path)
    monkeypatch.setattr(oe, "read_typetree", lambda _bundle, _pid: MATERIAL_TREE)
    code = oe.run(
        _parse("object", "set", "--bundle", str(bundle), "--path-id", "1002",
               "--field", "m_NoSuchThing", "--value", "1", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_UNUSABLE, code


def test_a_missing_bundle_exits_unusable(tmp_path: Path) -> None:
    code = oe.run(
        _parse("object", "delete", "--bundle", str(tmp_path / "nope.bundle"), "--path-id", "1")
    )
    assert code == oe.EXIT_UNUSABLE, code


def test_json_mode_keeps_stdout_to_one_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json`：stdout 上只有一个 JSON 文档（人读那份走 stderr）。"""
    bundle = _synth_bundle(tmp_path)
    code = oe.run(
        _parse("object", "delete", "--bundle", str(bundle), "--path-id", "1003",
               "--project-root", str(tmp_path), "--json")
    )
    assert code == oe.EXIT_DONE, code
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["ok"] is True and document["verb"] == "delete"
    assert document["changed"] is True and document["path_id"] == 1003
    assert "删除" in captured.err, "人读那份没走 stderr"


# --------------------------------------------------------------------------- #
# ⑦ J3 量程：新入口必须被**既有**那把尺子取到（⛔ 不再写第二把）
# --------------------------------------------------------------------------- #
def _j3_range(parser: argparse.ArgumentParser) -> str:
    """既有尺子的**取法**（`test_no_swap_wording.iter_help_texts`）—— 同一份逻辑，不抄。"""
    return "\n".join(iter_help_texts(parser))


def test_object_help_text_is_clean_and_visible_to_the_j3_ruler() -> None:
    """本模块注册出来的 `object` 帮助文本：① 能被那把取法取到；② 0 禁词。"""
    text = _j3_range(_object_parser())
    assert OBJECT_HELP_MARK in text, "object 的短帮助没被 iter_help_texts 取到 —— 注册坏了"
    assert "m_SavedProperties" in text, "字段路径的说明没进帮助文本 —— 取到的不是那一份"
    hits = banned_hits(text)
    assert hits == [], f"object 的帮助文本里有禁词：{hits}"


def test_the_j3_extraction_would_miss_object_if_it_were_not_registered() -> None:
    """**负控（能红）**：同一个取法喂一个**没挂 object** 的 parser ⇒ 记号必须取不到。

    ⛔ 没有这一条，"取到了"那句话证明不了任何东西（取法可能把什么都算进去）。
    """
    bare = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = bare.add_subparsers(dest="command")
    finder.add_parser(sub)  # 挂一个**别的**子命令：证明取法本身在干活，不是把输出扫空了

    text = _j3_range(bare)
    assert "谁引用了这一行" in text, "取法把别的子命令也漏了 —— 负控不成立"
    assert OBJECT_HELP_MARK not in text, "没挂 object 却取到了它的帮助文本 —— 那条断言是假的"


def test_object_is_wired_into_the_product_cli_parser() -> None:
    """★ 产品真入口（`cli/__main__._build_parser()`）里**必须有** `object`。

    ⚠ `src/ba_mod_maker/cli/__main__.py` 由 **Lead 独占接线**，本模块一个字都没写。
    这一条红 = 还没接上（不是尺子坏了）。要三行：

        from ba_mod_maker.cli import ..., object_edit
        object_edit.add_parser(sub)
        handlers["object"] = object_edit.run

    接上之后，既有 J3 尺子（`test_no_swap_wording.py` 里那条
    `test_cli_help_text_carries_no_banned_wording`）的量程自动覆盖这个新入口 ——
    不用再写第二把尺子。
    """
    text = _j3_range(cli_main._build_parser())
    assert OBJECT_HELP_MARK in text, (
        "object 没进 CLI 的帮助文本：__main__.py 还没接（该文件由 Lead 独占）。"
        "要的三行写在 docstring 里；本模块的注册面已由上面两条用例单独证明。"
    )


# --------------------------------------------------------------------------- #
# ⑧ 真顶层入口的**派发**：handlers 那张表把 object 接到这里（接线后才有意义）
# --------------------------------------------------------------------------- #
def test_the_product_entry_point_dispatches_to_this_module(tmp_path: Path) -> None:
    """`ba-mod-maker object delete ...` 走 `main()` -> handlers -> 本模块的 run()。

    ⛔ 只调 `oe.run()` 证明不了接线；这一条才是"用户敲的那条路真的通"。
    """
    bundle = _synth_bundle(tmp_path)
    code = cli_main.main(
        [
            "object",
            "delete",
            "--bundle",
            str(bundle),
            "--path-id",
            "1001",
            "--project-root",
            str(tmp_path),
        ]
    )
    assert code == oe.EXIT_DONE, code
    assert [edit["kind"] for edit in _planned(plan_path(tmp_path))] == ["delete"]


def test_the_product_entry_point_refuses_a_verb_this_cut_does_not_have() -> None:
    """真入口上 `object swap` 当场被 argparse 拒（退出码 2）—— 那类入口根本没注册。"""
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main(["object", "swap"])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# 真包实测（**不进 pytest**：要读游戏目录、规模不可控）
# --------------------------------------------------------------------------- #
# 2026-10-08 手跑（脚本：临时目录 + .venv\Scripts\python.exe 走 stdin），读数逐条如实：
#   真包  D:\Steam\steamapps\common\broken_arrow\BrokenArrow_Data\StreamingAssets\aa\PC\
#         156861a175ce41ea3270890166d7c57a_unitybuiltinshaders_...bundle   81,786 字节
#   对象  {'Shader': 19, 'Texture2D': 10, 'Sprite': 7, 'Material': 2, 'AssetBundle': 1}
#   路径  m_SavedProperties.m_TexEnvs[_AlphaTex].second.m_Texture.m_PathID -> 旧值 0
#   delete --path-id -7504259843138580660  -> 退出 0，计划里 kind=delete，old=304 字节
#   set    --value 1                       -> 退出 0，计划里 kind=set，old=0，new=1
#   delete --path-id 987654321（不存在）   -> 退出 2，stderr「没有 pathID=... 这个对象，删不了」
# ⚠ 这条链**只写 Δ 计划**（落在临时工程目录），游戏目录一个字节都没动。
#
# 接线后**真入口**再走一遍（`python -m ba_mod_maker.cli object ...`，2026-10-08）：
#   合成包 delete --path-id 1002   -> rc 0，判据行「删除 synth.bundle#1002（2000 字节）」
#   合成包 delete --path-id 9999   -> rc 2（J-T3-3）；加 --json 时 stdout 一份
#                                     {"schema":1,"ok":false,"error":{...}}，人读那句走 stderr
#   真包   set ... --dry-run       -> rc 0，判据行「修改 ...m_PathID（0 → 1）」
#                                     计划那行是「（--dry-run：没有写盘）」
#   真包   set --field 指到一个 list -> rc 2，一句「这一格的值是 list，--value 只认标量」

# --------------------------------------------------------------------------- #
# ⑨ 写侧纯函数：assign_field（读侧 `resolve_field` 的镜像）
# --------------------------------------------------------------------------- #
def test_assign_field_writes_the_three_spellings() -> None:
    tree = {"a": {"b": 1}, "s": [10, 20], "t": [["Layer_A", {"m_PathID": 5}]]}
    assert oe.assign_field(tree, oe.parse_field_path("a.b"), 2)["a"]["b"] == 2
    assert oe.assign_field(tree, oe.parse_field_path("s[1]"), 99)["s"] == [10, 99]
    assert oe.assign_field(tree, oe.parse_field_path("t[Layer_A].second.m_PathID"), 7)["t"][0][
        1
    ]["m_PathID"] == 7
    # 三条改的都是**副本**：原树一个字节不动
    assert tree == {"a": {"b": 1}, "s": [10, 20], "t": [["Layer_A", {"m_PathID": 5}]]}


def test_assign_field_walks_the_real_material_path() -> None:
    out = oe.assign_field(MATERIAL_TREE, oe.parse_field_path(SLOT_PATH), 58077)
    assert oe.resolve_field(out, oe.parse_field_path(SLOT_PATH)) == 58077
    assert oe.resolve_field(MATERIAL_TREE, oe.parse_field_path(SLOT_PATH)) == SLOT_PID


@pytest.mark.parametrize(
    "path",
    ["m_NoSuch", "m_SavedProperties.m_TexEnvs[NoSuch]", "m_SavedProperties.m_TexEnvs[9]"],
)
def test_assign_field_rejects_what_the_read_side_rejects(path: str) -> None:
    """读不到的地方**写不进去**（⛔ 不静默加一格）—— 三条都必须是 AssetError。"""
    with pytest.raises(AssetError):
        oe.assign_field(MATERIAL_TREE, oe.parse_field_path(path), 1)


# --------------------------------------------------------------------------- #
# ⑩ `object add --from`：规格文件（形状不对必须能红）
# --------------------------------------------------------------------------- #
def _spec_file(
    tmp_path: Path,
    *,
    payload_bytes: bytes | None = None,
    name: str = "payload.bin",
    **overrides: object,
) -> Path:
    blob = _blob(b"Z", 64) if payload_bytes is None else payload_bytes
    (tmp_path / name).write_bytes(blob)
    document: dict[str, object] = {
        "schema_version": 1,
        "kind": "asset-add",
        "path_id": 9001,
        "class_id": 28,
        "payload": name,
        "name": "NEW",
        "type_name": "Texture2D",
    }
    document.update(overrides)
    path = tmp_path / "add.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_read_add_spec_reads_the_shape(tmp_path: Path) -> None:
    spec = oe.read_add_spec(_spec_file(tmp_path))
    assert (spec.path_id, spec.class_id, spec.type_id) == (9001, 28, None)
    assert len(spec.payload) == 64
    assert (spec.name, spec.type_name) == ("NEW", "Texture2D")


def test_read_add_spec_checks_the_sha_when_it_is_given(tmp_path: Path) -> None:
    digest = hashlib.sha256(_blob(b"Z", 64)).hexdigest()
    assert oe.read_add_spec(_spec_file(tmp_path, payload_sha256=digest[:16])).path_id == 9001
    with pytest.raises(AssetError) as excinfo:
        oe.read_add_spec(_spec_file(tmp_path, payload_sha256="0" * 16))
    assert "sha256" in excinfo.value.message


@pytest.mark.parametrize(
    "overrides,needle",
    [
        ({"kind": "asset-plan"}, "kind"),
        ({"schema_version": 2}, "schema_version"),
        ({"path_id": 0}, "pathID"),
        ({"path_id": None}, "path_id"),
        ({"type_id": 12}, "正好给一个"),
        ({"class_id": None}, "正好给一个"),
        ({"payload": "nope.bin"}, "载荷"),
        ({"payload": "  "}, "payload"),
    ],
)
def test_read_add_spec_rejects_a_bad_spec(
    tmp_path: Path, overrides: dict[str, object], needle: str
) -> None:
    with pytest.raises(AssetError) as excinfo:
        oe.read_add_spec(_spec_file(tmp_path, **overrides))
    assert needle in (excinfo.value.message + str(excinfo.value.hint))


def test_resolve_type_id_is_fail_closed_on_an_ambiguous_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """class_id -> 类型表下标：唯一命中才用；0 命中 / 多命中都是**报错**（⛔ 不挑第一个）。"""
    spec = oe.read_add_spec(_spec_file(tmp_path))
    monkeypatch.setattr(oe, "read_type_class_ids", lambda _bundle: (28, 114, 114))
    assert oe.resolve_type_id(tmp_path / "x.bundle", spec) == 0
    with pytest.raises(AssetError) as excinfo:
        oe.resolve_type_id(tmp_path / "x.bundle", dataclasses.replace(spec, class_id=114))
    assert "必须先收敛" in excinfo.value.message
    with pytest.raises(AssetError):
        oe.resolve_type_id(tmp_path / "x.bundle", dataclasses.replace(spec, class_id=21))


# --------------------------------------------------------------------------- #
# ⑪ 真写：合成包（对象表 + 反向 Δ 逐字节还原）
# --------------------------------------------------------------------------- #
def _table(bundle: Path) -> list[tuple[int, int, int, int, int]]:
    with ufs.UfsReader(bundle) as reader:
        return list(ufs.read_serialized_file(reader, reader.nodes[0]).objects)


def _object_bytes(bundle: Path, path_id: int) -> bytes:
    with ufs.UfsReader(bundle) as reader:
        node = reader.nodes[0]
        info = ufs.read_serialized_file(reader, node)
        for _index, pid, stored, size, _tid in info.objects:
            if pid == path_id:
                return bytes(reader.read_range(node.offset + info.data_offset + stored, size))
    raise AssertionError(f"包里没有 pathID={path_id}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_bundle(tmp_path: Path) -> Path:
    """新增对象那条链的合成件：借 `test_writeback_newobj` 的构造器 —— 它那套的
    `data_offset == align16(48 + metadata_size)` 是**新增对象成立的条件**
    （`test_writeback_l1` 那份表后没有尾巴，`_added_meta` 会当场拒绝）。
    """
    objs = [(1001, _blob(b"A", 400)), (1002, _blob(b"B", 401)), (1003, _blob(b"C", 301))]
    path = tmp_path / "synth_add.bundle"
    path.write_bytes(_add_container(_add_sf(objs, pool=_blob(b"P", 5_000)), _blob(b"R", 200_007)))
    return path


def test_add_writes_a_one_verb_plan_and_no_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认跑法：Δ 计划一条 `add`，**包一个字节都没动**。"""
    bundle = _synth_bundle(tmp_path)
    payload = _blob(b"Z", 512)
    spec = oe.AddSpec(path_id=9001, class_id=None, type_id=7, payload=payload,
                      payload_source=tmp_path / "payload.bin")
    monkeypatch.setattr(oe, "resolve_add_spec", lambda _bundle, _spec: spec)
    before = _sha256(bundle)
    code = oe.run(
        _parse("object", "add", "--bundle", str(bundle), "--from", str(_spec_file(tmp_path)),
               "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    edits = _planned(plan_path(tmp_path))
    assert [edit["kind"] for edit in edits] == ["add"]
    assert edits[0]["new"]["bytes"] == len(payload)
    assert _sha256(bundle) == before, "默认跑法不许碰包"


def test_add_refuses_a_path_id_that_is_already_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pathID 撞了 ⇒ 退出码 2，且**不落计划、不写包**（`add_object` 的第一道守卫）。"""
    bundle = _synth_bundle(tmp_path)
    spec = oe.AddSpec(path_id=1002, class_id=None, type_id=7, payload=_blob(b"Z", 64),
                      payload_source=tmp_path / "payload.bin")
    monkeypatch.setattr(oe, "resolve_add_spec", lambda _bundle, _spec: spec)
    before = _sha256(bundle)
    code = oe.run(
        _parse("object", "add", "--bundle", str(bundle), "--from", str(_spec_file(tmp_path)),
               "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_UNUSABLE, code
    assert not plan_path(tmp_path).exists()
    assert _sha256(bundle) == before


def test_add_write_really_inserts_the_object_and_restores_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J2 + J6：对象表 N → N+1、新 pathID 严格升序、byteStart 是 8 的倍数；
    写完走反向 Δ 还原 ⇒ **整文件 sha256 == 改前**。"""
    bundle = _add_bundle(tmp_path)
    payload = _blob(b"Z", 512)
    spec = oe.AddSpec(path_id=9001, class_id=None, type_id=7, payload=payload,
                      payload_source=tmp_path / "payload.bin")
    monkeypatch.setattr(oe, "resolve_add_spec", lambda _bundle, _spec: spec)
    before = _sha256(bundle)
    rows_before = _table(bundle)
    code = oe.run(
        _parse("object", "add", "--bundle", str(bundle), "--from", str(_spec_file(tmp_path)),
               "--write", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    rows = _table(bundle)
    assert len(rows) == len(rows_before) + 1
    ids = [row[1] for row in rows]
    assert ids == sorted(set(ids)) and 9001 in ids, f"新 pathID 没进表或表不升序：{ids}"
    new_row = next(row for row in rows if row[1] == 9001)
    assert new_row[2] % 8 == 0, f"新对象 byteStart {new_row[2]} 不是 8 的倍数"
    assert new_row[3] == len(payload)
    assert new_row[4] == 7
    assert _object_bytes(bundle, 9001) == payload
    assert _sha256(bundle) != before
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path))
    report = store.restore()
    assert report.ok, "\n".join(report.lines())
    assert _sha256(bundle) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_delete_write_really_removes_the_object_and_restores(
    tmp_path: Path,
) -> None:
    """`delete --write` 走 `write_removals` 那条链：表 N → N-1，且反向 Δ 能还原。"""
    bundle = _synth_bundle(tmp_path)
    before = _sha256(bundle)
    code = oe.run(
        _parse("object", "delete", "--bundle", str(bundle), "--path-id", "1002",
               "--write", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    ids = [row[1] for row in _table(bundle)]
    assert ids == [1001, 1003], ids
    assert _sha256(bundle) != before
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path))
    assert store.restore().ok
    assert _sha256(bundle) == before


def test_delete_default_run_leaves_the_bundle_alone(tmp_path: Path) -> None:
    bundle = _synth_bundle(tmp_path)
    before = _sha256(bundle)
    assert oe.run(
        _parse("object", "delete", "--bundle", str(bundle), "--path-id", "1002",
               "--project-root", str(tmp_path))
    ) == oe.EXIT_DONE
    assert _sha256(bundle) == before


def test_dry_run_and_write_are_mutually_exclusive(tmp_path: Path) -> None:
    bundle = _synth_bundle(tmp_path)
    code = oe.run(
        _parse("object", "delete", "--bundle", str(bundle), "--path-id", "1002",
               "--dry-run", "--write", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_UNUSABLE, code
    assert not plan_path(tmp_path).exists()


# --------------------------------------------------------------------------- #
# ⑫ 真件（有类型树）上的全链：class_id -> type_id -> 载荷自证 -> NewObject / 重编码
# --------------------------------------------------------------------------- #
#: 前人留下的**小真件**（159,590 B / 3 个对象：AssetBundle ＋ Texture2D ＋ Sprite，
#: 类型表 0=Texture2D / 1=Sprite / 2=AssetBundle）。它比合成容器多一样东西：**类型树**。
import os

# 工作根：数据已移出仓（2026-11-09）。也可用 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REAL_BUNDLE = (
    WORK
    / "addr2"
    / "ourlabels_assets_all_24d7ddb3de2daf8c.bundle"
)


@pytest.fixture
def real_bundle(tmp_path: Path) -> Path:
    """真件的一份**副本**（⛔ 不动原件；一切写操作落在 tmp_path 里）。"""
    if not REAL_BUNDLE.is_file():
        pytest.skip(f"真件夹具不在：{REAL_BUNDLE}")
    import shutil

    target = tmp_path / REAL_BUNDLE.name
    shutil.copy2(REAL_BUNDLE, target)
    return target


def test_object_add_end_to_end_on_a_real_bundle(real_bundle: Path, tmp_path: Path) -> None:
    """真件全链：`class_id=28` 自己认成类型表下标、载荷自证过、新对象进表、反向 Δ 能还原。"""
    rows = _table(real_bundle)
    texture = next(row for row in rows if row[4] == 0)
    payload = _object_bytes(real_bundle, texture[1])
    assert payload, "夹具的贴图对象该有字节"
    new_pid = max(row[1] for row in rows) + 1
    spec = _spec_file(tmp_path, path_id=new_pid, class_id=28, payload_bytes=payload)
    before = _sha256(real_bundle)
    code = oe.run(
        _parse("object", "add", "--bundle", str(real_bundle), "--from", str(spec),
               "--write", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    after = _table(real_bundle)
    assert len(after) == len(rows) + 1
    ids = [row[1] for row in after]
    assert ids == sorted(set(ids)), f"新对象让对象表不升序了：{ids}"
    new_row = next(row for row in after if row[1] == new_pid)
    assert new_row[2] % 8 == 0
    assert new_row[4] == texture[4], "类型表下标认错了（class_id -> type_id）"
    assert _object_bytes(real_bundle, new_pid) == payload, "写后读回不是逐字节相同"
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path))
    assert store.restore().ok
    assert _sha256(real_bundle) == before


def test_object_set_write_reencodes_and_restores_byte_for_byte(
    real_bundle: Path, tmp_path: Path
) -> None:
    """`set --write` 真落字节（变长走 ObjectGrowth），写后读回是新值，反向 Δ 还原逐字节。"""
    from ba_mod_maker.asset_kernel import unitypy_source

    rows = _table(real_bundle)
    texture = next(row for row in rows if row[4] == 0)
    with unitypy_source.open_source(real_bundle) as source:
        old_name = str(source.read_typetree(texture[1])["m_Name"])
    before = _sha256(real_bundle)
    code = oe.run(
        _parse("object", "set", "--bundle", str(real_bundle), "--path-id", str(texture[1]),
               "--field", "m_Name", "--value", old_name + "_LONGER", "--write",
               "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    assert _sha256(real_bundle) != before
    with unitypy_source.open_source(real_bundle) as source:
        assert str(source.read_typetree(texture[1])["m_Name"]) == old_name + "_LONGER"
    # ⚠ UnityPy 那个句柄是**惰性释放**的（`AssetSource.close()` 只丢引用，不关 env）⇒
    #   同一进程里紧接着 os.replace 会 WinError 5。
    #   ★ 2026-10-12（B 线 task-5）**产品路自己扛了**：`reverse_delta.replace_with_retry()`
    #   （唯一实现，每次尝试前 `gc.collect()` ＋ 线性退避；writer 那条路转调同一份，
    #   异常类型不变）⇒ 还原不再需要调用方替它 GC。下面这行 gc.collect() 留作**测试兜底**，
    #   产品侧回归锁在 tests/test_reverse_delta_retry.py
    #   （7 条，含「拿掉 GC 必须报 WinError 5」的变异负控）。
    gc.collect()
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path))
    assert store.restore().ok
    assert _sha256(real_bundle) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_object_set_write_of_an_equal_length_value_stays_in_place(
    real_bundle: Path, tmp_path: Path
) -> None:
    """等长那一档走 `ObjectChange`（原地覆盖）：被改对象的 `(byteStart, size)` **一个都不动**。

    与上一条（变长 ⇒ `ObjectGrowth` 搬到尾部）成对：`write_edit` 是按**实际长度**选路的。
    """
    from ba_mod_maker.asset_kernel import unitypy_source

    rows = _table(real_bundle)
    texture = next(row for row in rows if row[4] == 0)
    with unitypy_source.open_source(real_bundle) as source:
        old_name = str(source.read_typetree(texture[1])["m_Name"])
    same_length = "Z" * len(old_name)
    before = _sha256(real_bundle)
    code = oe.run(
        _parse("object", "set", "--bundle", str(real_bundle), "--path-id", str(texture[1]),
               "--field", "m_Name", "--value", same_length, "--write",
               "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    after = next(row for row in _table(real_bundle) if row[1] == texture[1])
    assert (after[2], after[3]) == (texture[2], texture[3]), "等长档不该搬到尾部"
    with unitypy_source.open_source(real_bundle) as source:
        assert str(source.read_typetree(texture[1])["m_Name"]) == same_length
    gc.collect()
    store = reverse_delta.PatchStore(reverse_delta.patch_store_root(tmp_path))
    assert store.restore().ok
    assert _sha256(real_bundle) == before


def test_object_set_default_run_leaves_the_bundle_alone(real_bundle: Path, tmp_path: Path) -> None:
    rows = _table(real_bundle)
    texture = next(row for row in rows if row[4] == 0)
    before = _sha256(real_bundle)
    code = oe.run(
        _parse("object", "set", "--bundle", str(real_bundle), "--path-id", str(texture[1]),
               "--field", "m_Name", "--value", "X", "--project-root", str(tmp_path))
    )
    assert code == oe.EXIT_DONE, code
    assert _sha256(real_bundle) == before

