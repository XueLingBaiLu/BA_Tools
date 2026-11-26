r"""L7 prefab 组装器（`asset_kernel/node_assembly.py` ＋ `cli/object_node.py`）的机械判据。

**判据一行**：`.venv\\Scripts\\python.exe -m pytest tests/test_node_assembly.py -q -n 0`
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

夹具 = **合成包**（真 units 类型表 ＋ 我们自己拼的 5 个对象），⛔ 不读游戏盘、不写游戏目录。
三个对象造得最小但完整：`AssetBundle(1)` ＋ `GameObject(100)` ＋ `Transform(101)`
＋ `MonoScript(115)` ＋ `MonoBehaviour(200)`。

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 基线：`open_assembly` 读到 1 个节点、路径 `SYNTH_UNIT` | 1 个 |
| B | `plan_add_node` 干跑读数：父 `m_Children` 0→1、2 个新对象、预载 3→5 | 逐条相等 |
| C | **真写** `node add`：writer 计划落盘 ＋ `verify_plan` 读回 ＋ 重开看到第 2 个节点 | 全绿 |
| D | `component add --like`：`m_Component` 1→2，新组件是照既有那颗造的 | 全绿 |
| E | **往返**：删掉刚加的那个节点 ⇒ 对象表/预载/`m_Children` 回到基线 | 逐条相等 |
| F | **删根被拒**（它是 prefab 的根，删了等于删整个 prefab） | 抛 |
| G | CLI 层：`--dry-run` 与 `--write` 同给 ⇒ 退出码 2；干跑不改一个字节 | 2 / 同 sha |
| H | 守卫：MonoBehaviour **没给 `m_Script`** 时 `component add --type-id` fail-closed | 抛 |

⚠ 干跑与真写走**同一份** `NodePlan`（`node_assembly.write_plan(dry_run=...)`），所以 B 段那些
读数与 C 段落盘后的读数是**逐行同源**的。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import node_assembly as na
from ba_mod_maker.cli import object_node
from ba_mod_maker.core.errors import AssetError
from test_prefab_skins import (
    UNITS_META,
    _build_container,
    _build_sf,
    _common,
    _default_tree,
    _strip_meta,
)

AB = 1
ROOT_GO = 100
ROOT_TR = 101
MONOSCRIPT = 115
UROOT_MB = 200
#: 真件类型表里 UnitPrefabRoot 那一项的项号（`test_prefab_skins.UROOT_TYPE_INDEX` 同源）。
UROOT_TYPE_INDEX = 24
UNIT_NAME = "SYNTH_UNIT"


def _schema():
    return cl.read_type_table(UNITS_META.read_bytes(), common_strings=_common())


def _synth_blob() -> bytes:
    """最小可装配的合成包：AssetBundle + 一个 GameObject/Transform 节点 + 一颗 MonoBehaviour。"""
    meta = UNITS_META.read_bytes()
    head, body0, tail = _strip_meta(meta)
    schema = _schema()
    by_class = {entry.class_id: entry.index for entry in schema.entries}

    def nodes_of(class_id: int):
        return next(item for item in schema.entries if item.class_id == class_id).nodes

    def make(class_id: int, fill) -> bytes:
        nodes = nodes_of(class_id)
        tree = _default_tree(cl._build_tree(nodes))
        fill(tree)
        payload = cl.write_object(tree, nodes)
        if cl.read_object(payload, nodes) != tree:
            raise AssertionError(f"class {class_id} 的载荷自己都往返不了 —— 夹具坏了")
        return payload

    ab = make(
        cl.ASSET_BUNDLE_CLASS_ID,
        lambda t: t.update(
            {
                "m_Name": "synth_node_assets_all_00000000000000000000000000000000.bundle",
                "m_AssetBundleName": "synth_node",
                "m_PreloadTable": [cl.pptr(ROOT_GO), cl.pptr(ROOT_TR), cl.pptr(UROOT_MB)],
                "m_Container": [],
            }
        ),
    )
    go = make(
        cl.CLASS_GAME_OBJECT,
        lambda t: t.update(
            {
                "m_Name": UNIT_NAME,
                "m_IsActive": True,
                "m_Component": [{"component": cl.pptr(ROOT_TR)}],
            }
        ),
    )
    tr = make(
        cl.CLASS_TRANSFORM,
        lambda t: t.update(
            {
                "m_GameObject": cl.pptr(ROOT_GO),
                "m_Father": cl.pptr(0),
                "m_Children": [],
            }
        ),
    )
    mono = make(
        115,
        lambda t: t.update({"m_Name": "UnitPrefabRoot", "m_ClassName": "UnitPrefabRoot"}),
    )
    mb_nodes = next(e for e in schema.entries if e.index == UROOT_TYPE_INDEX).nodes
    mb_tree = _default_tree(cl._build_tree(mb_nodes))
    mb_tree.update({"m_GameObject": cl.pptr(ROOT_GO), "m_Script": cl.pptr(MONOSCRIPT)})
    mb = cl.write_object(mb_tree, mb_nodes)
    wanted = [
        (AB, by_class[cl.ASSET_BUNDLE_CLASS_ID], ab),
        (ROOT_GO, by_class[cl.CLASS_GAME_OBJECT], go),
        (ROOT_TR, by_class[cl.CLASS_TRANSFORM], tr),
        (MONOSCRIPT, by_class[115], mono),
        (UROOT_MB, UROOT_TYPE_INDEX, mb),
    ]
    objs = sorted(wanted, key=lambda row: row[0])
    return _build_container(_build_sf(head, body0, tail, objs), bytes(4_096), "synthnode")


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件抽件产物）")
    path = tmp_path_factory.mktemp("nodeasm") / "synth.bundle"
    path.write_bytes(_synth_blob())
    return path


@pytest.fixture
def case(synth: Path, tmp_path: Path) -> Path:
    """每个用例一份自己的副本 —— 真写回只写它。"""
    path = tmp_path / "case.bundle"
    shutil.copy2(synth, path)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paths(bundle: Path) -> tuple[str, ...]:
    with na.open_assembly(bundle) as view:
        return tuple(view.path_of(tid) for tid in view.transform_ids())


def _preload(bundle: Path) -> int:
    with na.open_assembly(bundle) as view:
        return na._preload_count(view)


# --------------------------------------------------------------------------- #
# A · 基线
# --------------------------------------------------------------------------- #
def test_a_baseline_is_one_node(case: Path) -> None:
    assert _paths(case) == (UNIT_NAME,)
    assert _preload(case) == 3
    with na.open_assembly(case) as view:
        assert view.components_of(ROOT_GO) == (ROOT_TR,)


# --------------------------------------------------------------------------- #
# B · 新增节点（干跑）
# --------------------------------------------------------------------------- #
def test_b_add_node_plan_readings(case: Path) -> None:
    plan = na.plan_add_node(case, parent=ROOT_TR, name="L7_CHILD")
    assert plan.verb == "node add"
    assert [kind for kind, _p in plan.stages] == ["delta"]
    (delta,) = (payload for _k, payload in plan.stages)
    assert len(delta.added) == 2
    assert [o.type_name for o in delta.added] == ["Transform", "GameObject"]
    assert [g.path_id for g in delta.growths] == [ROOT_TR, AB]
    assert plan.expect["children_before"] == ()
    assert plan.expect["preload_before"] == 3
    joined = "\n".join(plan.criterion_lines())
    assert "m_Children 0 -> 1" in joined
    assert "预载表 3 -> 5" in joined


def test_b_dry_run_writes_nothing(case: Path) -> None:
    before = _sha(case)
    plan = na.plan_add_node(case, parent=ROOT_TR, name="L7_CHILD")
    na.write_plan(plan, dry_run=True)
    assert _sha(case) == before
    assert na.preflight_plan(plan)  # 写前干算也能单独跑


# --------------------------------------------------------------------------- #
# C · 新增节点（真写 ＋ 写后复核）
# --------------------------------------------------------------------------- #
def test_c_add_node_round_trip(case: Path, tmp_path: Path) -> None:
    plan = na.plan_add_node(case, parent=ROOT_TR, name="L7_CHILD")
    reports = na.write_plan(plan, project_root=tmp_path, dry_run=False)
    assert len(reports) == 1
    assert reports[0].wrote_bundle is True
    lines = na.verify_plan(plan)
    assert any("m_Children" in line for line in lines)
    assert _paths(case) == (UNIT_NAME, f"{UNIT_NAME}/L7_CHILD")
    assert _preload(case) == 5
    with na.open_assembly(case) as view:
        child = view.resolve(f"{UNIT_NAME}/L7_CHILD")
        assert int(view.object_at(child).fields["m_Father"]["m_PathID"]) == ROOT_TR


def test_c_verify_plan_catches_a_wrong_expectation(case: Path, tmp_path: Path) -> None:
    """**尺子必须能红**：把期望改错，`verify_plan` 必须抛（不是默默绿）。"""
    import dataclasses

    plan = na.plan_add_node(case, parent=ROOT_TR, name="L7_CHILD")
    na.write_plan(plan, project_root=tmp_path, dry_run=False)
    wrong = dataclasses.replace(plan, expect={**plan.expect, "new_transform": 999})
    with pytest.raises(AssetError):
        na.verify_plan(wrong)


# --------------------------------------------------------------------------- #
# D · 加组件
# --------------------------------------------------------------------------- #
def test_d_add_component_like(case: Path, tmp_path: Path) -> None:
    plan = na.plan_add_component(case, node=ROOT_TR, like=UROOT_MB)
    assert plan.verb == "component add"
    reports = na.write_plan(plan, project_root=tmp_path, dry_run=False)
    assert reports[0].wrote_bundle is True
    na.verify_plan(plan)
    with na.open_assembly(case) as view:
        comps = view.components_of(ROOT_GO)
        assert len(comps) == 2
        assert comps[0] == ROOT_TR
        new = view.object_at(comps[1])
        assert new.fields["m_Script"]["m_PathID"] == MONOSCRIPT
        assert new.fields["m_GameObject"]["m_PathID"] == ROOT_GO


def test_h_mono_behaviour_without_a_script_pptr_is_refused(case: Path) -> None:
    """⛔ 不许凭空写 `m_Script`：默认树给不出 ⇒ fail-closed（出厂闸 A0 会红）。"""
    with pytest.raises(AssetError) as excinfo:
        na.plan_add_component(case, node=ROOT_TR, type_id=UROOT_TYPE_INDEX)
    assert "m_Script" in str(excinfo.value)


def test_h_ambiguous_class_id_is_refused(case: Path) -> None:
    """MonoBehaviour(114) 在真类型表里有 22 棵树 ⇒ 按 classID 取必取错，必须点名。"""
    with pytest.raises(AssetError) as excinfo:
        na.plan_add_component(case, node=ROOT_TR, class_id=114)
    assert "棵类型树" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# E · 往返：加了再删
# --------------------------------------------------------------------------- #
def test_e_add_then_delete_returns_to_baseline(case: Path, tmp_path: Path) -> None:
    na.write_plan(
        na.plan_add_node(case, parent=ROOT_TR, name="L7_CHILD"),
        project_root=tmp_path,
        dry_run=False,
    )
    assert _preload(case) == 5
    plan = na.plan_delete_node(case, node=f"{UNIT_NAME}/L7_CHILD")
    assert [kind for kind, _p in plan.stages] == ["delta", "removals"]
    reports = na.write_plan(plan, project_root=tmp_path, dry_run=False)
    assert len(reports) == 2
    assert all(report.wrote_bundle for report in reports)
    na.verify_plan(plan)
    assert _paths(case) == (UNIT_NAME,)
    assert _preload(case) == 3


def test_f_deleting_the_root_is_refused(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        na.plan_delete_node(case, node=ROOT_TR)
    assert "顶层节点" in str(excinfo.value)


def test_f_unknown_path_and_ambiguous_path(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        na.plan_delete_node(case, node="没有这个节点")
    assert "找不到路径" in str(excinfo.value)
    with pytest.raises(AssetError):
        na.plan_add_node(case, parent="SYNTH_UNIT/不存在的父", name="X")


# --------------------------------------------------------------------------- #
# G · CLI 层
# --------------------------------------------------------------------------- #
def _args(case: Path, tmp_path: Path, **over: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "object_action": "node",
        "node_action": "add",
        "bundle": str(case),
        "parent": str(ROOT_TR),
        "name": "L7_CLI",
        "kind": "empty",
        "mesh": None,
        "materials": [],
        "position": "0,0,0",
        "project_root": str(tmp_path),
        "dry_run": True,
        "write": False,
        "game": None,
        "catalog": None,
        "json_out": True,
    }
    base.update(over)
    return argparse.Namespace(**base)


def test_g_dry_run_and_write_together_is_unusable(case: Path, tmp_path: Path) -> None:
    assert object_node.run(_args(case, tmp_path, write=True)) == object_node.EXIT_UNUSABLE


def test_g_dry_run_cli_writes_nothing(case: Path, tmp_path: Path) -> None:
    before = _sha(case)
    assert object_node.run(_args(case, tmp_path)) == object_node.EXIT_DONE
    assert _sha(case) == before


def test_g_write_cli_writes_and_verifies(case: Path, tmp_path: Path) -> None:
    code = object_node.run(_args(case, tmp_path, dry_run=False, write=True))
    assert code == object_node.EXIT_DONE
    assert _paths(case) == (UNIT_NAME, f"{UNIT_NAME}/L7_CLI")


def test_g_missing_bundle_is_unusable(tmp_path: Path) -> None:
    assert object_node.run(_args(tmp_path / "nope.bundle", tmp_path)) == (
        object_node.EXIT_UNUSABLE
    )


def test_g_dispatch_routes_the_other_groups() -> None:
    """`node`/`component` 归本模块；别的原样转出去（⛔ 不改 object_edit / object_container）。"""
    from ba_mod_maker.cli import object_container, object_edit

    called: list[str] = []
    monkey = pytest.MonkeyPatch()
    monkey.setattr(object_edit, "run", lambda _a: called.append("edit") or 0)
    monkey.setattr(object_container, "run", lambda _a: called.append("container") or 0)
    try:
        object_node.dispatch(argparse.Namespace(object_action="set"))
        object_node.dispatch(argparse.Namespace(object_action="container"))
    finally:
        monkey.undo()
    assert called == ["edit", "container"]


def test_g_plan_is_the_same_object_for_dry_run_and_write(case: Path, tmp_path: Path) -> None:
    """干跑与真写走同一份计划（读数逐行同源）。"""
    plan = na.plan_add_node(case, parent=ROOT_TR, name="L7_SAME")
    dry = plan.criterion_lines()
    na.write_plan(plan, project_root=tmp_path, dry_run=False)
    assert plan.criterion_lines() == dry
    na.verify_plan(plan)
    assert plan.criterion_lines() == dry
