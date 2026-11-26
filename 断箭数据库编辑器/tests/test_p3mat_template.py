r"""tests/test_p3mat_template.py —— P3-3 材质模板入口的机械断言（**正控必须绿、负控必须红**）。

**判据一行**：.venv\Scripts\python.exe -m pytest tests\test_p3mat_template.py -q
  -> 通过数从 0 变 N；其中「负控必须报红」那一段是重点（AGENTS §0.9 动作 2）。

## 本文件量什么（每条都带正 / 负控）

| 段 | 判据 | 负控 |
|---|---|---|
| A | 入口的离线自检 5 步全过，且每步都印了正控读数 + 负控读数 | 少一步 / 某步不通过 -> 断言红 |
| B | **两条指名负控**：不存在的槽名 / 槽在但 PPtr 空 | 就是它们自己（必须抛 AssetError） |
| C | 克隆的忠实度：除 m_Name 与目标槽外**逐值相同** | 改一格 m_Floats -> 对拍必须不等 |
| D | 只改一个槽 + 空改动必须抛 | 新旧指向相同 -> AssetError |
| E | **沿用原 VT item**：m_BuildTextureStacks 逐值不变 | 改一条 itemName -> 对拍必须不等 |
| F | 编号规则（族根 / 分隔符 / 最大号 + 1） | 族里没号 -> 从 1 起 |
| G | 实读证据（survey.json）自证：真件三槽 + VT = 主组 f51bbaf7… | 缺证据 -> skip |
| H | J3 文案红线：三处已落「修改材质槽的指向」 | 塞「替换」-> 必须报出 |

## 为什么 B 那一段是重点

「槽在但 PPtr 空」在**真件里就有**：CR-9037_BASE_MAT 的 _VirtualDetailNormal 槽 pathID = **0**。
把 0 当成一个合法 pathID 用，就是**改错对象**；所以这两条必须抛，⛔ 不许静默返回 0。
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY = WORK / "p3mat" / "material_template.py"
BUILD = WORK / "p3mat" / "out" / "build.json"
SURVEY = WORK / "p3mat" / "survey.json"
UNITY_TXT = WORK / "p3mat" / "probe-out" / "positive-p3mat-slot.txt"
MODULE_NAME = "p3mat_material_template_under_test"

#: J3 的六个禁词（tests/test_no_swap_wording.py 那份口径，本文件不另起一套）。
BANNED = ("换成", "换上", "替换", "更换", "swap", "replace")


def _load_entry() -> types.ModuleType:
    """按**路径**加载入口（它不在产品包里 —— 写入范围只给了 _work/p3mat/）。"""
    assert ENTRY.is_file(), (
        f"缺入口：{ENTRY}（复算：.venv\\Scripts\\python.exe {ENTRY} --selftest）"
    )
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ENTRY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


MT = _load_entry()
from ba_mod_maker.asset_kernel import skin_install, writer  # noqa: E402
from ba_mod_maker.core.errors import AssetError  # noqa: E402

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")


# --------------------------------------------------------------------------- #
# A · 入口自己的离线自检（5 步 × 正负控）
# --------------------------------------------------------------------------- #
def test_selftest_has_five_steps_with_both_readings() -> None:
    rows = MT.selftest()
    assert len(rows) == 5, [r[0] for r in rows]
    for step, judge, ok, pos, neg in rows:
        assert step and judge, (step, judge)
        assert pos, f"{step} 缺正控读数"
        assert neg, f"{step} 缺负控读数"
        assert ok, f"{step} 没过：正控={pos} 负控={neg}"


def test_selftest_rc_is_zero() -> None:
    assert MT.main(["--selftest"]) == 0


# --------------------------------------------------------------------------- #
# B · 两条指名负控（⛔ 不许静默返回 0）
# --------------------------------------------------------------------------- #
def test_missing_slot_raises() -> None:
    """负控①：不存在的槽名必须抛 —— 两条产品入口都要抛。"""
    tree = MT.demo_tree()
    with pytest.raises(AssetError):
        skin_install.slot_texture_pptr(tree, "NoSuchSlot")
    with pytest.raises(KeyError):
        writer.texture_slot_path_id(tree, "NoSuchSlot")


def test_slot_present_but_empty_pptr_raises() -> None:
    """负控②：槽在但 PPtr 空（pathID = 0）必须抛 —— ⛔ 不许拿 0 当 pathID 用。"""
    tree = MT.demo_tree(detail_pid=0)
    assert "_VirtualDetailNormal" in MT.family_slot_names(tree)
    with pytest.raises(AssetError):
        skin_install.slot_texture_pptr(tree, "_VirtualDetailNormal")
    with pytest.raises(AssetError):
        writer.texture_slot_path_id(tree, "_VirtualDetailNormal")


def test_read_family_material_raises_on_empty_albedo() -> None:
    """albedo 槽空 ⇒ 读同族这一步就必须抛（后面几步都建在它上面）。"""
    with pytest.raises(AssetError):
        MT.read_family_material(MT.demo_tree(albedo_pid=0))


def test_read_family_material_raises_on_missing_family_slot() -> None:
    """少一个族槽名 ⇒ 抛（消息里要印出可用槽名）。"""
    tree = MT.demo_tree()
    envs = [e for e in tree["m_SavedProperties"]["m_TexEnvs"] if str(e[0]) != "_Emission"]
    tree["m_SavedProperties"]["m_TexEnvs"] = envs
    with pytest.raises(AssetError) as excinfo:
        MT.read_family_material(tree)
    assert "_Emission" in str(excinfo.value)


def test_read_family_material_raises_on_empty_vt_stack() -> None:
    """没有 VT item 就没有「可沿用的 item」⇒ 抛（⛔ 不许自己造 .gtp/.gts）。"""
    tree = MT.demo_tree()
    tree["m_BuildTextureStacks"] = []
    with pytest.raises(AssetError):
        MT.read_family_material(tree)


# --------------------------------------------------------------------------- #
# C/D/E · 克隆的四条自证
# --------------------------------------------------------------------------- #
def test_clone_changes_only_one_slot() -> None:
    tree = MT.demo_tree()
    cloned = MT.clone_template(tree, new_name="DEMO 1", texture_path_id=999)
    changed = [s for s in MT.family_slot_names(tree)
               if MT._safe_pid(cloned, s) != MT._safe_pid(tree, s)]
    assert changed == [MT.ALBEDO_SLOT], changed
    assert MT._safe_pid(cloned, MT.ALBEDO_SLOT) == 999
    assert cloned["m_Name"] == "DEMO 1"
    source_albedo = tree["m_SavedProperties"]["m_TexEnvs"][0][1]["m_Texture"]["m_PathID"]
    assert source_albedo == 111, "入参（源件）被改了"


def test_clone_is_faithful_negative_control() -> None:
    """正控：克隆件除 m_Name 与目标槽外逐值相同；负控：改一格 m_Floats -> 对拍必须不等。"""
    tree = MT.demo_tree()
    cloned = MT.clone_template(tree, new_name="DEMO 1", texture_path_id=999)
    strip = MT._strip_name_and_slot
    assert strip(tree, MT.ALBEDO_SLOT) == strip(cloned, MT.ALBEDO_SLOT)
    doctored = copy.deepcopy(cloned)
    doctored["m_SavedProperties"]["m_Floats"] = [["_AlphaRemapMax", 0.5]]
    assert strip(tree, MT.ALBEDO_SLOT) != strip(doctored, MT.ALBEDO_SLOT)


def test_empty_change_raises() -> None:
    """负控：新旧指向相同 = 空改动 ⇒ 抛。"""
    tree = MT.demo_tree()
    with pytest.raises(AssetError):
        MT.clone_template(tree, new_name="DEMO 1", texture_path_id=111)


def test_vt_item_is_carried_over_negative_control() -> None:
    """正控：m_BuildTextureStacks 逐值不变；负控：改一条 itemName -> 对拍必须不等。"""
    tree = MT.demo_tree()
    cloned = MT.clone_template(tree, new_name="DEMO 1", texture_path_id=999)
    assert MT.vt_stacks_of(cloned) == MT.vt_stacks_of(tree)
    doctored = copy.deepcopy(cloned)
    doctored["m_BuildTextureStacks"] = [{"groupName": "f51bbaf7a40a0ab4909c16ff519cc9cf",
                                         "itemName": "0" * 32}]
    assert MT.vt_stacks_of(doctored) != MT.vt_stacks_of(tree)


def test_pair_shape_adapters_roundtrip() -> None:
    """形状适配器：clone 读器的 {"first","second"} 与产品入口的 [k, v] 必须能来回换。

    ⚠ 这是**实测撞出来的**：clone.read_object 的 m_TexEnvs 是 dict-pair，而
    writer.set_material_texture / skin_install.slot_texture_pptr 只认 list-pair。
    """
    clone_shape = {
        "m_Name": "M",
        "m_SavedProperties": {
            "m_TexEnvs": [
                {"first": MT.ALBEDO_SLOT, "second": {"m_Texture": {"m_FileID": 0, "m_PathID": 7}}},
                {"first": "_Emission", "second": {"m_Texture": {"m_FileID": 0, "m_PathID": 0}}},
            ]
        },
    }
    product = MT.to_pair_lists(clone_shape)
    assert MT.family_slot_names(product) == (MT.ALBEDO_SLOT, "_Emission")
    assert MT.slot_path_id(product, MT.ALBEDO_SLOT) == 7
    back = MT.to_pair_dicts(product)
    assert back["m_SavedProperties"]["m_TexEnvs"][0]["first"] == MT.ALBEDO_SLOT
    assert MT.family_slot_names(clone_shape) == (MT.ALBEDO_SLOT, "_Emission"), "dict 形状也要认得"


# --------------------------------------------------------------------------- #
# F · 编号
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "base", "sep", "serial"),
    [
        ("CR-9037_BASE_MAT", "CR-9037_BASE_MAT", "", None),
        ("BMD-2_1", "BMD-2", "_", 1),
        ("US_1M113_2 1", "US_1M113_2", " ", 1),
        ("AC-130 BaseCamo 5", "AC-130 BaseCamo", " ", 5),
    ],
)
def test_split_serial(name: str, base: str, sep: str, serial: int | None) -> None:
    assert MT.split_serial(name) == (base, sep, serial)


def test_next_material_name_positive_and_negative() -> None:
    """正控：同族最大号 + 1（含族根算 0 号）；负控：族里一个号都没有 -> 从 1 起。"""
    assert MT.next_material_name(["BMD-2_1", "BMD-2_3"], "BMD-2_1") == "BMD-2_4"
    assert MT.next_material_name(["US_1M113_2", "US_1M113_2 1"], "US_1M113_2 1") == "US_1M113_2 2"
    assert MT.next_material_name(["OTHER"], "SOMETHING") == "SOMETHING 1"
    # 阶乘：族外的名字不许把号带进来
    assert MT.next_material_name(["BMD-2_1", "BMDX_9"], "BMD-2_1") == "BMD-2_2"


# --------------------------------------------------------------------------- #
# G · 实读证据自证（真件读数）
# --------------------------------------------------------------------------- #
def test_build_evidence_matches_spec() -> None:
    """真件读数（units 包一次开包 + 造包那趟）：三槽 / VT = 主组 f51bbaf7… / 空 PPtr 真的存在。"""
    if not BUILD.is_file():
        pytest.skip(
            f"环境测不了：{BUILD} 还没生成（units 锁被别人占着时跑不出来）。"
            f"复算：.venv\\Scripts\\python.exe _work\\p3mat\\material_template.py --run"
        )
    data = json.loads(BUILD.read_text(encoding="utf-8"))
    family = data["family"]
    assert family["slots"][:3] == ["Layer_A97CDC25", "_Emission", "_VirtualDetailNormal"], family
    assert family["albedo_path_id"] != 0
    assert family["detail_normal_path_id"] == 0, "真件那个空槽不见了 —— 负控②的证据没了"
    assert family["vt_stacks"] == [
        ["f51bbaf7a40a0ab4909c16ff519cc9cf", "46009b58eec2333abf44820ca69542fc"]
    ], family["vt_stacks"]
    assert data["dangling"] == [], data["dangling"]
    host = data["host_renderer"]
    assert host["materials_after"] == host["materials_before"] + 1, host
    assert data["new_material_name"] == "CR-9037_BASE_MAT 1", data["new_material_name"]


def test_unity_probe_artifact_shows_both_slots() -> None:
    """Unity 那一票的**产物**：同一颗 prefab 上，旧底图与新底图**各有一行** 槽=Layer_A97CDC25。

    ⛔ 判据是产物内容（不是 Unity 退出码）；缺产物 -> skip 并印复算命令。
    """
    if not UNITY_TXT.is_file():
        pytest.skip(
            f"环境测不了：{UNITY_TXT} 不在（Unity 被占 / 编辑器开着时跑不出来）。"
            f"复算：.venv\\Scripts\\python.exe _work\\p3mat\\probe.py --run 然后 --judge"
        )
    body = UNITY_TXT.read_text(encoding="utf-8", errors="replace")
    new_line = [x for x in body.splitlines()
                if "wreck_wreek_BaseMap" in x and "槽=Layer_A97CDC25" in x]
    old_line = [x for x in body.splitlines()
                if "CR9037UV_blinn1_BaseMap" in x and "槽=Layer_A97CDC25" in x]
    assert new_line, body
    assert old_line, body


# --------------------------------------------------------------------------- #
# H · J3 文案红线（这三处是 Lead 点名的 sink）
# --------------------------------------------------------------------------- #
def test_skin_install_wording_is_modify_not_swap() -> None:
    """点名的三处：模块头 / summary() / CLI 的 skin 子命令短帮助 —— 都落「修改材质槽的指向」。"""
    head = (skin_install.__doc__ or "").splitlines()[0]
    assert "修改材质槽的指向" in head and "换" not in head, head

    class _P:  # 只要 summary() 用得到的字段（鸭子类型的替身，不起真 plan、不读盘）
        bundle_name = "B"
        slot = "Layer_A97CDC25"
        material_name = "M"
        old_texture = "A"
        new_texture = "B"
        changed_bytes = 8

    summary = skin_install.SkinSwapPlan.summary(_P())  # type: ignore[arg-type]
    assert "修改材质槽" in summary and "的指向" in summary and "换" not in summary, summary

    for text in (head, summary, skin_install.SkinSwapPlan.__doc__ or ""):
        for word in BANNED:
            assert word not in text.casefold(), f"{word!r} 出现在文案面：{text[:80]}"


def test_my_own_entry_carries_no_banned_wording() -> None:
    """本入口自己的文案面（模块头）也不许出现六个禁词。"""
    doc = MT.__doc__ or ""
    assert "材质模板入口" in doc
    for word in BANNED:
        assert word not in doc.casefold(), f"{word!r} 出现在入口模块头"
