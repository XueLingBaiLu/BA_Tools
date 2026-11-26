r"""出厂闸 unit_bundle.verify_unit_bundle 的判据（A0~A3 + A6）。

**判据一行**：pytest tests/test_unit_bundle_gate.py -q -n 0 -> 通过数 0 -> N，其中 **3 条是负控**
（喂坏样本必须变红才算尺子，AGENTS §0.9 动作 2）：

| # | 喂什么 | 必须 |
|---|---|---|
| 正控 | 最小单位包（根上有 UnitPrefabRoot + Skins 非空 + 115 在包里） | \`ok is True\` |
| 负控 A0 | **把 MonoScript(115) 从包里拿掉**（#711 第一版的形状） | 报 \`一颗 MonoScript 都没有\` |
| 负控 A1 | MonoScript 的类名不是 \`UnitPrefabRoot\` | 报 \`根 GameObject 上没有 UnitPrefabRoot\` |
| 负控 A3 | \`Skins.Data\` 空 | 报 \`Skins 是空的\` |
| 基线自检 A6 | （无夹具） | 基线里 \`PrefabRoot\`/\`PrefabBody\` 必须是 \`PPtr<$Transform>\` |
| 负控 A6 | \`_work/a6_ref/phase1box_gameobject_wrong.bundle\` | 报 A6，且 A0/A3 都不红 |

夹具来源 = \`_work/w10_ref/units_meta_121.bin\`
（1.2.1 units 元数据区 + monoscripts 的 115 类型项，245,824 B）；缺就整组 skip。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import (
    builder,
    clone,
    monoscript_build,
    prefab_build,
    unit_bundle,
    unit_script_shapes,
)

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

W10_REF = WORK / "w10_ref"
UNITS_META = W10_REF / "units_meta_121.bin"
ASSET = "Assets/Resources_moved/ModelPrefabs/RouteA/GateProbe.prefab"
#: 1.2.1 的 script_types 里 UnitPrefabRoot 是第 21 项（1.2.0 是 20）—— 实测见
#: `_work/check121/probe_a6.py`。
SI_UNIT_ROOT = 21


@pytest.fixture(scope="module")
def meta() -> bytes:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta_121.bin（1.2.1 元数据区冻结件）")
    return UNITS_META.read_bytes()


def _mini(tmp_path: Path, meta: bytes, *, with_script: bool = True,
          class_name: str = "UnitPrefabRoot", skins: int = 1,
          skins_outside: bool = False) -> Path:
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    go_pid = builder.path_id_for("gate|go")
    tr_pid = builder.path_id_for("gate|tr")
    mono_pid = builder.path_id_for("gate|mono")
    beh_pid = builder.path_id_for("gate|beh")

    go = prefab_build.default_fields(table.entry_for(1).nodes)
    go["m_Name"] = "GateRoot"
    # ⚠ m_Component 要列出**这个 GameObject 上的全部组件**（含 MonoBehaviour）——
    # 第一版只写了 Transform，于是出厂闸的 A1 按"根上的组件"找 UnitPrefabRoot 找不到（假红）。
    components = [{"component": prefab_build.pptr(tr_pid)}]
    if with_script:
        components.append({"component": prefab_build.pptr(beh_pid)})
    go["m_Component"] = components
    tr = prefab_build.default_fields(table.entry_for(4).nodes)
    tr["m_GameObject"] = prefab_build.pptr(go_pid)
    tr["m_Children"] = []
    tr["m_Father"] = prefab_build.pptr(0)

    objects = [
        builder.ObjectSpec(1, go, go_pid, label="go"),
        builder.ObjectSpec(4, tr, tr_pid, label="tr"),
    ]
    script_map = None
    if with_script:
        template = prefab_build.default_fields(table.entry_for(115).nodes)
        ms = monoscript_build.make_mono_script_fields(
            template, f"BrokenArrow|BrokenArrow.Client.Ecs.Spawn|{class_name}")
        objects.append(monoscript_build.mono_script_spec(ms, path_id=mono_pid, label="mono"))
        beh = prefab_build.default_fields(table.entry_for(114, script_index=SI_UNIT_ROOT).nodes)
        beh["m_GameObject"] = prefab_build.pptr(go_pid)
        beh["m_Script"] = prefab_build.pptr(mono_pid)
        beh["PrefabRoot"] = prefab_build.pptr(tr_pid)
        beh["PrefabBody"] = prefab_build.pptr(tr_pid)
        beh["Renderers"] = []
        if skins:
            target = 999_999 if skins_outside else tr_pid   # 包外 PPtr（A3 正控用）
            # ★ 1.2.1 的 SkinStorageData 多了 `skinsSerializedRefs`（80 节点形状；
            #   1.2.0 是 53 节点）—— 少了这个字段 clone.write_object 会当场拒。
            beh["Skins"] = {"Data": [{
                "Id": 1,
                "Data": {
                    "_keyData": [prefab_build.pptr(target)],
                    "_valueData": [{"Renderers": [prefab_build.pptr(target)]}],
                },
                "skinsSerializedRefs": {"_keyData": [], "_valueData": []},
            }]}
        else:
            beh["Skins"] = {"Data": []}
        objects.append(builder.ObjectSpec(
            114, beh, beh_pid, script_index=SI_UNIT_ROOT, label="beh"))
        script_map = {SI_UNIT_ROOT: mono_pid}

    plan = builder.build_object_bundle(
        objects=objects, bundle_name="gateprobe_assets_all_0000000000000001.bundle",
        source_meta=meta, container=[(ASSET, go_pid)], script_type_map=script_map,
        preload_all=True)
    out = tmp_path / "gateprobe.bundle"
    out.write_bytes(bytes(plan.data))
    return out


def test_gate_passes_on_minimal_unit(tmp_path: Path, meta: bytes) -> None:
    report = unit_bundle.verify_unit_bundle(_mini(tmp_path, meta), asset_path=ASSET)
    assert report.ok, report.problems
    assert report.root_game_object != 0
    assert report.container == ((ASSET, report.root_game_object),), report.container
    assert report.classes.get(115) == 1, report.classes
    assert any("包内 MonoScript 1 颗" in line for line in report.readings), report.readings
    assert any("UnitPrefabRoot pathID" in line for line in report.readings), report.readings


def test_gate_flags_scripts_outside_the_package(tmp_path: Path, meta: bytes) -> None:
    """负控 A0：把 115 拿掉 —— 游戏里就是 Missing Script（#711 第一版的形状）。"""
    report = unit_bundle.verify_unit_bundle(_mini(tmp_path, meta, with_script=False))
    assert not report.ok
    assert any("一颗 MonoScript(115) 都没有" in p for p in report.problems), report.problems
    # ⚠ 附带读数：没有 115 时那条 114 根本进不了包（builder 的 script_types 前提不成立）
    assert report.classes.get(114, 0) == 0, report.classes


def test_gate_flags_missing_animation_chain(tmp_path: Path, meta: bytes) -> None:
    """负控：根上只有 UnitPrefabRoot（没有 AnimationManager/Hub）⇒ 整条动画链缺失。

    用户 2026-10-21 现场：「车进来了、没有动画」——A0~A5 全绿，只有这条能红。
    """
    report = unit_bundle.verify_unit_bundle(_mini(tmp_path, meta), require_animations=True)
    assert not report.ok
    assert any(
        "AnimationManager" in p or "AnimationHub" in p
        for p in report.problems
    ), report.problems


def test_gate_flags_missing_unit_prefab_root(tmp_path: Path, meta: bytes) -> None:
    """负控 A1：类名不对（不是 UnitPrefabRoot）⇒ 引擎那条 ArgumentException。"""
    report = unit_bundle.verify_unit_bundle(
        _mini(tmp_path, meta, class_name="NotAUnitRoot"))
    assert not report.ok
    assert any("没有 UnitPrefabRoot" in p for p in report.problems), report.problems


def test_empty_skins_is_reported_but_not_red(tmp_path: Path, meta: bytes) -> None:
    """**口径更正（2026-10-10）**：`Skins.Data` 为空 ⇒ **基数读数**，**不是红**。

    原判据说「空 ⇒ Skins.Initialize 会 NullReferenceException」，实测反证：
    原版 units 包 **288 个 UnitPrefabRoot 里 37 个（12.8%）是空的**、游戏不崩
    （`_work/a3_check.py`）；更准的解释见 `_work/a2-skin-mono-spec.md:66`「空 Data ≠ null 引用」。
    但**必须显式报出** —— 引擎对「没有皮肤覆盖」这一档是静默的。
    """
    report = unit_bundle.verify_unit_bundle(_mini(tmp_path, meta, skins=0))
    assert report.ok, report.problems
    assert any("Skins.Data 为空" in line for line in report.readings), report.readings


def test_gate_flags_skins_pptr_outside(tmp_path: Path, meta: bytes) -> None:
    """**正控 A3**：Skins 里的 PPtr 指向**包外** ⇒ 必须红（这条才是真问题）。"""
    report = unit_bundle.verify_unit_bundle(
        _mini(tmp_path, meta, skins_outside=True), asset_path=ASSET)
    assert not report.ok, report.readings
    assert any("指向包外" in p for p in report.problems), report.problems

# --- A6：脚本字段形状（2026-10-13 新增）------------------------------------- #
A6_REF = WORK / "a6_ref"
A6_WRONG = A6_REF / "phase1box_gameobject_wrong.bundle"


def test_a6_baseline_says_the_two_roots_are_transforms() -> None:
    """**基线自检**：真件里 PrefabRoot / PrefabBody 是 **Transform**，不是 GameObject。

    2026-10-13 现场：我们的桩写成了 GameObject ⇒ 出厂闸 A0~A5 **全绿**，只有 A6 抓得住。
    CurrentId **不在**类型树里 —— 它只能是 [NonSerialized]（Unity 6 文档
    Manual/script-serialization-rules：public 且非 readonly 才序列化）。
    """
    rows = unit_script_shapes.UNIT_SCRIPT_SHAPES[
        "BrokenArrow|BrokenArrow.Client.Ecs.Spawn|UnitPrefabRoot"]
    types = {name: tp for _lv, tp, name in rows}
    assert types["PrefabRoot"] == "PPtr<$Transform>", types["PrefabRoot"]
    assert types["PrefabBody"] == "PPtr<$Transform>", types["PrefabBody"]
    assert "CurrentId" not in types, "CurrentId 不该进类型树（[NonSerialized]）"


def test_a6_flags_gameobject_where_the_real_thing_is_a_transform() -> None:
    """**负控 A6**：夹具 = 2022.3 造的那个阶段 1 包（两个根都写成了 GameObject）。

    这条夹具的价值在于**同一次跑里**两件事都成立：A6 红，而 A0/A3 不红 ——
    那正是「A0~A5 只查 PPtr 在不在包里、不查目标类型」这个盲区的证据本身。
    """
    if not A6_WRONG.is_file():
        pytest.skip(f"缺 A6 负控夹具 {A6_WRONG}（759,202 B，见同目录 README.txt）")
    report = unit_bundle.verify_unit_bundle(A6_WRONG)
    a6 = [p for p in report.problems if "（A6）" in p]
    assert a6, report.problems
    assert "PPtr<$Transform>" in a6[0], a6
    blind = [p for p in report.problems if "（A0）" in p or "（A3）" in p]
    assert blind == [], f"这条夹具本该证明 A0/A3 看不见它，却报了：{blind}"

