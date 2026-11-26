r"""材质槽数组 `Renderer.m_Materials`（J1~J4）＋ 材质级引用普查 的机械判据。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_renderer_slots.py -q -n 0`
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

夹具（合成件）与 ``UROOT``/``RENDERER_A`` 那套 pathID 从 `tests/test_prefab_skins.py`
**import 过来**（同一份夹具 —— ⛔ 不在这里再造第二份）：

| 段 | 判据 | 通过线 |
|---|---|---|
| A | 合成件基线：两个渲染器的槽数组 == [500] / [500, 501] | 相等 |
| B | 纯函数：读 / 加槽（末尾、指定下标）/ 删槽；越界与非法材质都抛 | 全过 |
| C | **普查**：整包 9 个对象全部走过；MAT_A 被 4 条指着；MAT_C 0 条 | 能绿能红 |
| D | **J1** 干跑 vs 真写：`criterion_lines()` 逐行相等；干跑不改一个字节 | 相等 |
| E | **J2** 读数：`m_Materials` 条数 **M -> M+1**（删则 M-1）；写后复核逐条相同 | 全对 |
| F | **J3** 反向 Δ：`PatchStore.restore()` 之后整文件 sha256 == 改前 | 相等 |
| G | **J4** 删槽守卫：还有别的引用指着这颗材质 ⇒ **必须一起改或拒绝**（两种都测） | 全红/全绿 |
| H | 结构：写侧那一趟只有 `prefab_skins.run_write` 一处（本模块不另开写盘的路） | 一处 |

⚠ **本夹具只造了一个 prefab** ⇒ 「材质被**别的 prefab**" 共享"这一档在这里造不出来（那是 8 台车
共用履带料的现场，`kb/20` §1.1）。能造的三种引用都造了：**同一 prefab 的两个渲染器**、
**同一渲染器的两个下标**（B/G 段造出来）、**皮肤表的 `_keyData`**。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone as cl
from ba_mod_maker.asset_kernel import prefab_skins as ps
from ba_mod_maker.asset_kernel import renderer_slots as rs
from ba_mod_maker.core.errors import AssetError
from test_prefab_skins import (
    AB_PID,
    MAT_A,
    MAT_B,
    MAT_C,
    RENDERER_A,
    RENDERER_B,
    SYNTH_OBJECTS,
    UNITS_META,
    UROOT,
    _store,
    _synth_blob,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """合成件本体（与 test_prefab_skins 的 synth 同一个 @@_synth_blob@@ 缓存）。"""
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件抽件产物）")
    path = tmp_path_factory.mktemp("slots") / "synth.bundle"
    path.write_bytes(_synth_blob())
    return path


@pytest.fixture
def case(synth: Path, tmp_path: Path) -> Path:
    """每个用例一份自己的副本 —— 真写回只写它。"""
    path = tmp_path / "case.bundle"
    shutil.copy2(synth, path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: 合成件的 AssetBundle 名（`_synth_blob()` 里写死的那一串）。
SYNTH_AB_NAME = "synth_skins_assets_all_" + "0" * 32 + ".bundle"
#: I 段用的容器地址（与 `test_catalog_edit` 的 SHARED_ASSET 同一条串）。
CONTAINER_PATH = "Assets/Synth/Shared.prefab"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    return project


# --------------------------------------------------------------------------- #
# A · 合成件基线
# --------------------------------------------------------------------------- #
def test_a_baseline_slot_arrays(synth: Path) -> None:
    assert rs.read_slots(synth, RENDERER_A) == (MAT_A,)
    assert rs.read_slots(synth, RENDERER_B) == (MAT_A, MAT_B)


def test_a_a_non_renderer_has_no_slots_field(case: Path) -> None:
    """负控：拿非渲染器去读槽数组 ⇒ 抛（⛔ 不许静默当空数组）。"""
    with pytest.raises(AssetError) as excinfo:
        rs.read_slots(case, UROOT)
    assert "没有 m_Materials" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# B · 纯函数
# --------------------------------------------------------------------------- #
def test_b_add_appends_at_the_end_by_default() -> None:
    tree = {"m_Materials": [cl.pptr(MAT_A)]}
    appended = rs.add_slot(tree, MAT_B)
    assert rs.material_slots(appended) == (MAT_A, MAT_B)
    assert rs.material_slots(tree) == (MAT_A,), "入参树被就地改了"


def test_b_add_can_insert_at_an_index() -> None:
    tree = {"m_Materials": [cl.pptr(MAT_A), cl.pptr(MAT_B)]}
    assert rs.material_slots(rs.add_slot(tree, MAT_C, index=1)) == (MAT_A, MAT_C, MAT_B)
    assert rs.material_slots(rs.add_slot(tree, MAT_C, index=0)) == (MAT_C, MAT_A, MAT_B)
    assert rs.material_slots(rs.add_slot(tree, MAT_C, index=2)) == (MAT_A, MAT_B, MAT_C)


def test_b_untouched_rows_are_copied_verbatim() -> None:
    """没动过的槽**原样照抄**（深拷旧行）—— 新槽才按 clone.pptr 拼。"""
    original = {"m_FileID": 0, "m_PathID": MAT_A, "m_Extra": 7}
    tree = {"m_Materials": [original]}
    appended = rs.add_slot(tree, MAT_B)
    assert appended["m_Materials"][0] == original
    assert appended["m_Materials"][0] is not original
    assert appended["m_Materials"][1] == cl.pptr(MAT_B)


@pytest.mark.parametrize(
    "material,index,needle",
    [
        (0, None, "不是正数"),
        (-3, None, "不是正数"),
        (MAT_B, 5, "越界"),
        (MAT_B, -1, "越界"),
    ],
    ids=["zero", "negative", "index-too-big", "index-negative"],
)
def test_b_add_slot_negative_controls(material: int, index: int | None, needle: str) -> None:
    tree = {"m_Materials": [cl.pptr(MAT_A)]}
    with pytest.raises(AssetError) as excinfo:
        rs.add_slot(tree, material, index=index)
    assert needle in str(excinfo.value), str(excinfo.value)


def test_b_remove_slot_refuses_while_other_references_exist() -> None:
    tree = {"m_Materials": [cl.pptr(MAT_A), cl.pptr(MAT_B)]}
    with pytest.raises(AssetError) as excinfo:
        rs.remove_slot(tree, 0, shared_with=["MeshRenderer#9.m_Materials[0]"])
    text = str(excinfo.value)
    assert "还有 1 条引用" in text and "一起改" in text


def test_b_remove_slot_goes_through_when_every_reference_is_declared() -> None:
    tree = {"m_Materials": [cl.pptr(MAT_A), cl.pptr(MAT_B)]}
    handled = ["MeshRenderer#9.m_Materials[0]"]
    assert rs.material_slots(rs.remove_slot(tree, 0, shared_with=handled, handled=handled)) == (
        MAT_B,
    )


@pytest.mark.parametrize("index", [2, -1], ids=["too-big", "negative"])
def test_b_remove_slot_index_out_of_range(index: int) -> None:
    tree = {"m_Materials": [cl.pptr(MAT_A)]}
    with pytest.raises(AssetError) as excinfo:
        rs.remove_slot(tree, index)
    assert "越界" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# C · 材质级引用普查（整包 PPtr）
# --------------------------------------------------------------------------- #
def test_c_census_walks_the_whole_package(case: Path) -> None:
    """整包普查：9 个对象**全部走过**（不是只看 prefab 闭包）。"""
    census = rs.census_material(case, MAT_A)
    assert census.objects == SYNTH_OBJECTS
    assert census.decoded + sum(count for _cls, count in census.undecodable) == census.objects
    assert census.ok, census.undecodable
    assert census.in_package is True
    assert census.material_name == "RU_T72B 1"


def test_c_census_finds_all_four_references_of_the_shared_material(case: Path) -> None:
    census = rs.census_material(case, MAT_A)
    labels = sorted(ref.label() for ref in census.refs)
    assert labels == [
        f"MeshRenderer#{RENDERER_A}.m_Materials[0]",
        f"MeshRenderer#{RENDERER_B}.m_Materials[0]",
        f"MonoBehaviour#{UROOT}.Skins.Data[0].Data._keyData[0]",
        f"MonoBehaviour#{UROOT}.Skins.Data[1].Data._keyData[0]",
    ], labels
    assert census.owners == (UROOT, RENDERER_A, RENDERER_B)
    assert census.refs[0].index == 0


def test_c_census_is_not_a_tautology(case: Path) -> None:
    """**负控（尺子能红）**：一颗没人用的材质 ⇒ 0 条；不存在的 pathID ⇒ 不在包里。"""
    unused = rs.census_material(case, MAT_C)
    assert unused.refs == ()
    assert unused.in_package is True
    missing = rs.census_material(case, 999)
    assert missing.refs == ()
    assert missing.in_package is False
    assert "不在包里" in "\n".join(missing.lines())


def test_c_others_excludes_exactly_one_reference(case: Path) -> None:
    census = rs.census_material(case, MAT_A)
    rest = census.others(owner_path_id=RENDERER_A, field="m_Materials[0]")
    # 普查按对象表顺序走（pathID 升序）⇒ 这里排序后再比（顺序本身在 C 段另有断言）
    assert sorted(ref.label() for ref in rest) == sorted(
        [
            f"MeshRenderer#{RENDERER_B}.m_Materials[0]",
            f"MonoBehaviour#{UROOT}.Skins.Data[0].Data._keyData[0]",
            f"MonoBehaviour#{UROOT}.Skins.Data[1].Data._keyData[0]",
        ]
    )


# --------------------------------------------------------------------------- #
# D · J1 干跑 vs 真写
# --------------------------------------------------------------------------- #
def test_d_j1_dry_run_and_real_write_agree_line_by_line(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    plan = rs.plan_add_slot(case, RENDERER_A, MAT_B)
    assert plan.count_line() == "m_Materials 条数 1 -> 2（新增，材质 pathID=501）"
    dry = rs.install(plan, project_root=project, dry_run=True)
    assert dry.plan.criterion_lines() == plan.writeback.criterion_lines()
    assert _sha256(case) == before, "干跑写了盘"
    real = rs.install(plan, project_root=project)
    assert real.plan.criterion_lines() == dry.plan.criterion_lines()
    assert _sha256(case) != before
    assert "m_Materials 条数 1 -> 2" in "\n".join(plan.criterion_lines())


def test_d_plan_readings_are_real_numbers(case: Path) -> None:
    plan = rs.plan_add_slot(case, RENDERER_A, MAT_B)
    assert plan.level == "ObjectGrowth"
    assert plan.size_before() == len(ps.read_package_object(case, RENDERER_A).payload)
    assert len(plan.change.payload) == plan.size_before() + 12, "一个 PPtr 是 12 字节"
    assert plan.writeback.rows[0].path_id == RENDERER_A


# --------------------------------------------------------------------------- #
# E · J2 读数（M -> M+1 / M-1）＋ 写后复核
# --------------------------------------------------------------------------- #
def test_e_j2_add_one_slot(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = rs.plan_add_slot(case, RENDERER_A, MAT_B)
    assert (len(plan.slots_before), len(plan.slots_after)) == (1, 2)
    rs.install(plan, project_root=project)
    assert rs.read_slots(case, RENDERER_A) == (MAT_A, MAT_B)
    assert rs.verify_plan(plan)[0] == "m_Materials 条数 1 -> 2（读回 2，逐条与期望相同）"


def test_e_j2_remove_one_slot(case: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    census = rs.census_material(case, MAT_B)
    handled = [ref.label() for ref in census.refs]
    plan = rs.plan_remove_slot(case, RENDERER_B, 1, handled=handled)
    assert (len(plan.slots_before), len(plan.slots_after)) == (2, 1)
    assert plan.census is not None and plan.census.material_path_id == MAT_B
    rs.install(plan, project_root=project)
    assert rs.read_slots(case, RENDERER_B) == (MAT_A,)
    assert rs.verify_plan(plan)[0] == "m_Materials 条数 2 -> 1（读回 1，逐条与期望相同）"


def test_e_verify_plan_goes_red_when_the_file_moved_on(case: Path, tmp_path: Path) -> None:
    """**正控**：写完再改一刀 ⇒ 旧计划复核必须红。"""
    project = _project(tmp_path)
    plan = rs.plan_add_slot(case, RENDERER_A, MAT_B)
    rs.install(plan, project_root=project)
    rs.verify_plan(plan)
    second = rs.plan_add_slot(case, RENDERER_A, MAT_C)
    rs.install(second, project_root=project)
    with pytest.raises(AssetError) as excinfo:
        rs.verify_plan(plan)
    assert "写后读回" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# F · J3 反向 Δ ⇒ 整文件 sha256 == 改前
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("verb", ["add", "remove"])
def test_f_j3_reverse_delta_restores_the_whole_file(
    case: Path, tmp_path: Path, verb: str
) -> None:
    project = _project(tmp_path)
    before = _sha256(case)
    if verb == "add":
        plan = rs.plan_add_slot(case, RENDERER_A, MAT_B)
    else:
        census = rs.census_material(case, MAT_B)
        plan = rs.plan_remove_slot(
            case, RENDERER_B, 1, handled=[ref.label() for ref in census.refs]
        )
    report = rs.install(plan, project_root=project)
    assert report.patch is not None, "没留反向 Δ 补丁"
    assert _sha256(case) != before
    restore = _store(project, case.parent).restore()
    assert restore.ok, restore.lines()
    assert _sha256(case) == before


# --------------------------------------------------------------------------- #
# G · J4 删槽守卫：**必须一起改，否则拒绝**
# --------------------------------------------------------------------------- #
def test_g1_the_same_renderers_other_index_blocks_the_removal(case: Path) -> None:
    """同一个渲染器的**别的下标**指着同一颗材质 ⇒ 删一个槽必须被拦。"""
    census = rs.census_material(case, MAT_A)
    assert f"MeshRenderer#{RENDERER_B}.m_Materials[0]" in [ref.label() for ref in census.refs]
    with pytest.raises(AssetError) as excinfo:
        rs.plan_remove_slot(case, RENDERER_A, 0)
    text = str(excinfo.value)
    assert "还有 3 条引用指着它" in text, text
    assert f"MeshRenderer#{RENDERER_B}.m_Materials[0]" in text


def test_g2_another_index_of_the_same_renderer_blocks_too(case: Path, tmp_path: Path) -> None:
    """**同一个数组的另一个下标**：先把 RENDERER_A 变成 [500, 500]，再删第 0 格。"""
    project = _project(tmp_path)
    rs.install(rs.plan_add_slot(case, RENDERER_A, MAT_A), project_root=project)
    assert rs.read_slots(case, RENDERER_A) == (MAT_A, MAT_A)
    with pytest.raises(AssetError) as excinfo:
        rs.plan_remove_slot(case, RENDERER_A, 0)
    assert f"MeshRenderer#{RENDERER_A}.m_Materials[1]" in str(excinfo.value)


def test_g3_declaring_every_reference_lets_the_removal_through(case: Path, tmp_path: Path) -> None:
    """把普查给出的每一条都放进 `handled` ⇒ 放行，且写后复核通过。"""
    project = _project(tmp_path)
    census = rs.census_material(case, MAT_A)
    handled = [
        ref.label()
        for ref in census.others(owner_path_id=RENDERER_A, field="m_Materials[0]")
    ]
    assert len(handled) == 3
    plan = rs.plan_remove_slot(case, RENDERER_A, 0, handled=handled)
    assert plan.slots_after == ()
    text = "\n".join(plan.criterion_lines())
    assert "引用普查（整包）" in text and "其中已声明一并处理（handled）3 条" in text
    rs.install(plan, project_root=project)
    assert rs.read_slots(case, RENDERER_A) == ()
    assert rs.verify_plan(plan)[0] == "m_Materials 条数 1 -> 0（读回 0，逐条与期望相同）"


def test_g4_a_partial_declaration_is_still_refused(case: Path) -> None:
    census = rs.census_material(case, MAT_A)
    labels = [
        ref.label()
        for ref in census.others(owner_path_id=RENDERER_A, field="m_Materials[0]")
    ]
    with pytest.raises(AssetError) as excinfo:
        rs.plan_remove_slot(case, RENDERER_A, 0, handled=labels[:1])
    assert "还有 2 条引用" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# H · 结构：写侧那一趟只有一处（在 prefab_skins.run_write 里）
# --------------------------------------------------------------------------- #
def test_h_this_module_has_no_write_call_of_its_own() -> None:
    """本模块**不**自己调 write_delta / write_objects —— 写侧只有 prefab_skins.run_write 一份。"""
    import ast

    source = (
        REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel" / "renderer_slots.py"
    ).read_text("utf-8")
    tree = ast.parse(source)
    found = [
        getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") in {"write_delta", "write_objects"}
    ]
    assert found == [], found
    assert "run_write" in source

# --------------------------------------------------------------------------- #
# I · 地址级容器条目（`AssetBundle.m_Container`）—— 「能被地址取到」的另一半
# --------------------------------------------------------------------------- #
def test_i_a_the_synthetic_bundle_has_an_empty_container(case: Path) -> None:
    """基线：合成件的 AssetBundle **有预载表、容器是空的** —— 所以它是 I 段最干净的起点。"""
    assert rs.container_hosts(case) == ((AB_PID, SYNTH_AB_NAME),)
    tree = ps.read_package_object(case, AB_PID).tree
    assert rs.container_entries(tree) == ()
    assert rs.container_paths(tree) == ()
    assert len(tree[rs.PRELOAD_TABLE_FIELD]) == 3


def test_i_b_add_and_remove_are_pure_functions() -> None:
    """纯函数：加一条 -> 读回；摘掉 -> 回到原样；**入参树一个字节不动**。"""
    tree: dict = {"m_Container": [], "m_PreloadTable": []}
    added = rs.add_container_entry(tree, asset_path=CONTAINER_PATH, path_id=UROOT)
    entries = rs.container_entries(added)
    assert [(e.asset_path, e.path_id, e.preload_index, e.preload_size) for e in entries] == [
        (CONTAINER_PATH, UROOT, 0, 1)
    ]
    assert added[rs.PRELOAD_TABLE_FIELD] == [cl.pptr(UROOT)], "预载表没跟着追加"
    assert tree["m_Container"] == [], "入参树被就地改了"
    back, removed = rs.remove_container_entry(added, asset_path=CONTAINER_PATH)
    assert rs.container_entries(back) == ()
    assert removed.path_id == UROOT and removed.asset_path == CONTAINER_PATH
    assert added[rs.PRELOAD_TABLE_FIELD] == [cl.pptr(UROOT)], "摘条目动了预载表"


def test_i_b_add_appends_to_the_preload_table_without_renumbering() -> None:
    """既有条目的 preloadIndex 是前缀下标 —— 追加**不许**动它们。"""
    tree: dict = {
        "m_Container": [
            {
                "first": "Assets/Synth/One.prefab",
                "second": {"preloadIndex": 7, "preloadSize": 2, "asset": cl.pptr(UROOT)},
            }
        ],
        "m_PreloadTable": [cl.pptr(1), cl.pptr(2), cl.pptr(3)],
    }
    added = rs.add_container_entry(tree, asset_path=CONTAINER_PATH, path_id=RENDERER_A)
    entries = rs.container_entries(added)
    assert [(e.preload_index, e.preload_size) for e in entries] == [(7, 2), (3, 1)]
    assert len(added[rs.PRELOAD_TABLE_FIELD]) == 4


@pytest.mark.parametrize(
    "asset_path,path_id,needle",
    [
        (CONTAINER_PATH, 0, "不是正数"),
        (CONTAINER_PATH, -5, "不是正数"),
        ("   ", UROOT, "地址是空的"),
        ("Assets/Mods/My/icon.png", UROOT, "撞名"),
    ],
    ids=["zero-id", "negative-id", "blank-path", "duplicate-basename"],
)
def test_i_b_add_negative_controls(asset_path: str, path_id: int, needle: str) -> None:
    """负控四条：pathID 非正 / 地址空 / **资产名与既有条目撞名**。"""
    tree: dict = {
        "m_Container": [
            {
                "first": "Assets/Other/icon.png",
                "second": {"preloadIndex": 0, "preloadSize": 1, "asset": cl.pptr(UROOT)},
            }
        ],
        "m_PreloadTable": [cl.pptr(UROOT)],
    }
    with pytest.raises(AssetError) as excinfo:
        rs.add_container_entry(tree, asset_path=asset_path, path_id=path_id)
    assert needle in str(excinfo.value), str(excinfo.value)


def test_i_b_add_refuses_a_path_that_is_already_there() -> None:
    tree: dict = {"m_Container": [], "m_PreloadTable": []}
    once = rs.add_container_entry(tree, asset_path=CONTAINER_PATH, path_id=UROOT)
    with pytest.raises(AssetError) as excinfo:
        rs.add_container_entry(once, asset_path=CONTAINER_PATH, path_id=UROOT)
    assert "已经有这条地址" in str(excinfo.value)


def test_i_b_remove_refuses_an_unknown_path() -> None:
    with pytest.raises(AssetError) as excinfo:
        rs.remove_container_entry(
            {"m_Container": [], "m_PreloadTable": []}, asset_path="Assets/Nope.prefab"
        )
    assert "没有这条地址" in str(excinfo.value)


def test_i_b_a_non_assetbundle_has_no_container_field(case: Path) -> None:
    """负控：拿 Material 去读容器 ⇒ 抛（⛔ 不许静默当空表）。"""
    with pytest.raises(AssetError) as excinfo:
        rs.container_entries(ps.read_package_object(case, MAT_A).tree)
    assert "没有 m_Container" in str(excinfo.value)


def test_i_c_resolve_host_refuses_a_path_id_that_is_not_an_assetbundle(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        rs.resolve_container_host(case, UROOT)
    assert "不是" in str(excinfo.value) and "AssetBundle" in str(excinfo.value)


def test_i_c_the_plan_refuses_an_asset_id_outside_the_package(case: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        rs.plan_add_container(case, asset_path="Assets/Synth/Shared2.prefab", asset_id=999_999)
    assert "不在这个包里" in str(excinfo.value)


def test_i_d_j1_dry_run_and_real_write_agree_and_j6_restores(
    case: Path, tmp_path: Path
) -> None:
    """**J1/J2/J6**：干跑零字节 -> 真写落字节 -> 读回逐条相同 -> 反向 Δ 还原逐位。"""
    project = _project(tmp_path)
    before = _sha256(case)
    plan = rs.plan_add_container(case, asset_path=CONTAINER_PATH, asset_id=UROOT)
    assert plan.count_line() == f"m_Container 条数 0 -> 1（新增，地址 {CONTAINER_PATH!r}）"
    assert plan.level == "ObjectGrowth"
    dry = rs.install(plan, project_root=project, dry_run=True)
    assert dry.plan.criterion_lines() == plan.writeback.criterion_lines()
    assert _sha256(case) == before, "干跑写了盘"
    real = rs.install(plan, project_root=project)
    assert real.wrote_bundle is True
    assert _sha256(case) != before
    assert rs.verify_plan(plan)[0] == "m_Container 条数 0 -> 1（读回 1，逐条与期望相同）"
    assert rs.container_paths(ps.read_package_object(case, AB_PID).tree) == (CONTAINER_PATH,)
    assert len(ps.read_package_object(case, AB_PID).tree[rs.PRELOAD_TABLE_FIELD]) == 4
    restore = _store(project, case.parent).restore()
    assert restore.ok, restore.lines()
    assert _sha256(case) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_i_d_add_then_remove_empties_it_again(case: Path, tmp_path: Path) -> None:
    """两个原子动作各自可撤：加一条 -> 再摘掉它 -> **容器读回 0 条**。

    ⚠ 如实记：**字节不会回到原样** —— 两趟都走 Δ（`ObjectGrowth`：对象搬到 node[0] 尾部、
    旧字节留在数据区成死区）。逐位还原那条路是**反向 Δ 补丁柜**（J6），不是"反向做一遍"。
    """
    project = _project(tmp_path)
    before = _sha256(case)
    add = rs.plan_add_container(case, asset_path=CONTAINER_PATH, asset_id=UROOT)
    rs.install(add, project_root=project)
    assert _sha256(case) != before
    drop = rs.plan_remove_container(case, asset_path=CONTAINER_PATH)
    assert drop.count_line() == f"m_Container 条数 1 -> 0（删除，地址 {CONTAINER_PATH!r}）"
    assert drop.level == "ObjectGrowth"
    rs.install(drop, project_root=project)
    assert rs.container_paths(ps.read_package_object(case, AB_PID).tree) == ()
    assert rs.verify_plan(drop)[0] == "m_Container 条数 1 -> 0（读回 0，逐条与期望相同）"
    # 预载表：新增时尾部加的那一格**留着**（摘条目一个字节都不动）
    assert len(ps.read_package_object(case, AB_PID).tree[rs.PRELOAD_TABLE_FIELD]) == 4
    # 逐位还原走反向 Δ（同一条补丁里记着两趟的原始字节），不是"反向做一遍"
    restore = _store(project, case.parent).restore()
    assert restore.ok, restore.lines()
    assert _sha256(case) == before, "反向 Δ 还原后不是逐字节相同（J6）"


def test_i_e_verify_plan_goes_red_when_the_file_moved_on(case: Path, tmp_path: Path) -> None:
    """**正控**：写完再改一刀 ⇒ 旧计划的读回必须红。"""
    project = _project(tmp_path)
    plan = rs.plan_add_container(case, asset_path=CONTAINER_PATH, asset_id=UROOT)
    rs.install(plan, project_root=project)
    assert rs.verify_plan(plan)
    rs.install(rs.plan_remove_container(case, asset_path=CONTAINER_PATH), project_root=project)
    with pytest.raises(AssetError) as excinfo:
        rs.verify_plan(plan)
    assert "写后读回" in str(excinfo.value)

