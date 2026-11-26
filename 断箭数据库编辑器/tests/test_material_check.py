r"""材质级判据 **M1–M8**（`asset_kernel/material_check.py`）的机械判据：**每条都能红**。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_material_check.py -q -n 0`
-> 通过数**从 0 收集变 N passed**（本文件新建前不存在）。

| 段 | 判据 | 通过线 |
|---|---|---|
| M1 | 新材质读回四件事（pathID / m_Name / 颜色槽 / VT 栈） | 正控绿 · 负控红 |
| M2 | 资产层加了、可见性侧没加（**两侧不一致**才是对的） | 正控绿 · 负控红 |
| M3 | 底图车族 == VT item 车族（跨族 = NCC 0.0245 现场） | 正控绿 · 负控红 |
| M4 | 删材质前引用清单**必须为空** | 正控绿 · 负控红 |
| M5 | `m_TextureFormat` 在可编码集里（BC4/BC5 必须报） | 正控绿 · 负控红 |
| M6 | 包级全绿 ＋ 新对象在预载表里 | 正控绿 · 负控红 |
| M7 | 文案里 `换成/替换/swap/replace` 0 命中 | 正控绿 · 负控红 |
| M8 | 只出机械读数 ＋ 非完整档必须有降级理由 | 正控绿 · 负控红 |
| 汇总 | `run_all`：全合规绿 · 塞一条坏的必须报出来 · 没给读数的记「跳过」不算绿 | 三条都过 |

⚠ **每条正控都用同一份输入**：正控与负控只差**一处**（用户口径：一次只改一个变量）——
所以"红了"一定是那一处造成的。
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import material_check as mc
from ba_mod_maker.asset_kernel import materials

#: 真读数（同 tests/test_materials.py 的 U4_CAMO_DESCS，从 _work/camo 那份真件读数抄来）。
U4_CAMO_DESCS = {
    "Layer_A97CDC25": "Albedo",
    "Layer_8D60137F": "Normal",
    "Layer_4C4561B0": "Mask",
    "Layer_A97CDC25_1": "Camo",
}
#: VT 绑定（m_BuildTextureStacks）的文本形式 —— 判据是**逐字符相同**。
VT_BINDING = '{"groupName": "f51bbaf7a40a0ab4909c16ff519cc9cf", "itemName": "b6d28b03"}'


class _Ref:
    """一条"谁指着这颗材质"（形状照 `renderer_slots.MaterialRef.label()`）。"""

    def __init__(self, label: str) -> None:
        self._label = label

    def label(self) -> str:
        return self._label


# --------------------------------------------------------------------------- #
# M1 · 新材质读回
# --------------------------------------------------------------------------- #
def _m1(**over: object) -> dict:
    base: dict[str, object] = {
        "path_id": 900,
        "object_ids": [1, 200, 900],
        "name": "DLC2_CV9035_paint",
        "expected_name": "DLC2_CV9035_paint",
        "color_slot": "Layer_A97CDC25",
        "color_texture_id": 111,
        "expected_texture_id": 111,
        "stacks_before": VT_BINDING,
        "stacks_after": VT_BINDING,
    }
    base.update(over)
    return base


def test_m1_positive() -> None:
    result = mc.m1_new_material_readback(**_m1())
    assert result.ok is True and result.key == "M1"
    assert "VT 栈一致" in result.lines()[0]


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"stacks_after": VT_BINDING.replace("b6d28b03", "b6d28b04")}, "逐字符相同"),
        ({"path_id": 901}, "不在对象表里"),
        ({"color_texture_id": 112}, "颜色槽"),
        ({"name": "别的东西"}, "m_Name"),
        ({"slot_pid_inside": False}, "包外"),
    ],
    ids=["vt-changed", "not-in-table", "wrong-texture", "wrong-name", "dangling"],
)
def test_m1_negative_controls(over: dict, needle: str) -> None:
    result = mc.m1_new_material_readback(**_m1(**over))
    assert result.ok is False, over
    assert needle in "\n".join(result.lines()), result.lines()


# --------------------------------------------------------------------------- #
# M2 · 资产层 / 可见性侧
# --------------------------------------------------------------------------- #
def test_m2_positive() -> None:
    result = mc.m2_visibility_pair(skin_ids=[2232, 2240], menu_ids=[2232], new_id=2240)
    assert result.ok is True
    assert "Skins.Data 有" in result.lines()[0] and "菜单侧 无" in result.lines()[0]


def test_m2_negative_when_both_sides_have_it() -> None:
    result = mc.m2_visibility_pair(skin_ids=[2232, 2240], menu_ids=[2232, 2240], new_id=2240)
    assert result.ok is False
    assert "也在" in "\n".join(result.lines())


def test_m2_negative_when_the_asset_side_is_missing() -> None:
    result = mc.m2_visibility_pair(skin_ids=[2232], menu_ids=[2232], new_id=2240)
    assert result.ok is False
    assert "不在 Skins.Data 里" in "\n".join(result.lines())


# --------------------------------------------------------------------------- #
# M3 · 成套（同族）
# --------------------------------------------------------------------------- #
def test_m3_positive() -> None:
    result = mc.m3_family_match(texture_family="T-72B", item_family="t-72b")
    assert result.ok is True and "同族" in result.lines()[0]


def test_m3_negative_on_a_cross_family_pair() -> None:
    result = mc.m3_family_match(
        texture_family="T-72B", item_family="T-90M", texture_name="RU_T72B 1", item_name="T90 item"
    )
    assert result.ok is False
    assert "跨族" in "\n".join(result.lines())


# --------------------------------------------------------------------------- #
# M4 · 删除前的引用普查
# --------------------------------------------------------------------------- #
def test_m4_positive_when_nothing_points_at_it() -> None:
    result = mc.m4_deletable_material(refs=(), material_name="孤儿材质", material_path_id=777)
    assert result.ok is True and "0 条" in result.lines()[0]


def test_m4_negative_on_a_shared_material() -> None:
    refs = [_Ref(f"MeshRenderer#{400 + i}.m_Materials[0]") for i in range(9)]
    result = mc.m4_deletable_material(refs=refs, material_name="履带料", material_path_id=501)
    assert result.ok is False
    assert "9 条引用" in result.lines()[0]
    assert "MeshRenderer#400.m_Materials[0]" in "\n".join(result.lines())


# --------------------------------------------------------------------------- #
# M5 · m_TextureFormat
# --------------------------------------------------------------------------- #
def test_m5_positive() -> None:
    result = mc.m5_texture_format(texture_format=12, texture_name="body")
    assert result.ok is True and "可编" in result.lines()[0]
    # ★ 2026-10-17（L5）：可编码集从 {10, 12} 加宽到 {10, 12, 25, 26} ——
    # BC7（25）与 BC4（26）现在**真能编**（asset_kernel/texture_encode.py，etcpak 现编）。
    # ⛔ 旧行为一条都没松：下面第三条负控里 BC5/ASTC/Crunched 照样红。
    assert mc.encodable_formats() == (10, 12, 25, 26)


def test_m5_positive_on_the_newly_encodable_formats() -> None:
    """L5 加宽的那两档（BC7 = 25 / BC4 = 26）必须**绿** —— 它们现在真能编。"""
    for fmt in (25, 26):
        result = mc.m5_texture_format(texture_format=fmt)
        assert result.ok is True, f"格式 {fmt} 现在能编，不该再报能力不足"
        assert "可编" in "\n".join(result.lines())


@pytest.mark.parametrize(
    "fmt",
    [27, 48, 28, 29],
    ids=["BC5", "ASTC", "DXT1Crunched", "DXT5Crunched"],
)
def test_m5_negative_on_unencodable_formats(fmt: int) -> None:
    """⛔ 可编码集变宽了**但没变成什么都行**：BC5 / ASTC / Crunched 照样红。"""
    result = mc.m5_texture_format(texture_format=fmt)
    assert result.ok is False
    assert "能力不足" in "\n".join(result.lines())


def test_m5_respects_an_explicit_encodable_set() -> None:
    """把可编码集收成 {10} ⇒ 12（BC3）也必须报（负控：口径是参数，不是写死在判断里）。"""
    assert mc.m5_texture_format(texture_format=12, encodable=(10,)).ok is False


# --------------------------------------------------------------------------- #
# M6 · 包级 ＋ 预载表
# --------------------------------------------------------------------------- #
def test_m6_positive() -> None:
    result = mc.m6_bundle_gate(problems=(), preload_ids=[1, 5, 900], new_path_ids=[900])
    assert result.ok is True and "没进 m_PreloadTable 的 0 个" in result.lines()[0]


def test_m6_negative_when_the_new_object_is_not_preloaded() -> None:
    result = mc.m6_bundle_gate(problems=(), preload_ids=[1, 5], new_path_ids=[900])
    assert result.ok is False and "900" in "\n".join(result.lines())


def test_m6_negative_when_the_bundle_itself_is_not_green() -> None:
    result = mc.m6_bundle_gate(
        problems=["A3 Skins.Data 里有 2 条 PPtr 指向包外"], preload_ids=[900], new_path_ids=[900]
    )
    assert result.ok is False and "包级问题" in "\n".join(result.lines())


# --------------------------------------------------------------------------- #
# M7 · 文案里没有「换」
# --------------------------------------------------------------------------- #
def test_m7_positive() -> None:
    result = mc.m7_no_swap_wording(
        texts=[
            "material skin add：新增一条皮肤",
            "material slot remove：删掉一个材质槽",
            "改一个既有字段的值",
        ]
    )
    assert result.ok is True and "0 命中" in result.lines()[0]


@pytest.mark.parametrize(
    "text,word",
    [("把旧材质替换成新的", "替换"), ("换成另一颗材质", "换成"), ("swap the material", "swap")],
    ids=["替换", "换成", "swap"],
)
def test_m7_negative_controls(text: str, word: str) -> None:
    result = mc.m7_no_swap_wording(texts=[text])
    assert result.ok is False
    assert word in "\n".join(result.lines())


# --------------------------------------------------------------------------- #
# M8 · 只出机械读数 ＋ 降级清单
# --------------------------------------------------------------------------- #
def _plan(**over: object) -> object:
    base = {
        "material_name": "RU_T72B 1",
        "coverage": materials.COVERAGE_PARTIAL,
        "notes": ("Normal/Mask 是 VT 层，静态取不到",),
    }
    base.update(over)
    return types.SimpleNamespace(**base)


def test_m8_positive() -> None:
    result = mc.m8_mechanical_readings(plans=[_plan()], coverage_lines=["RU_T72B 1：部分"])
    assert result.ok is True
    assert "降级清单 1 条" in result.lines()[0]


def test_m8_negative_on_a_subjective_word() -> None:
    result = mc.m8_mechanical_readings(
        plans=[_plan()], extra_lines=["车体看起来像游戏原版"]
    )
    assert result.ok is False
    assert "主观词" in "\n".join(result.lines())


def test_m8_negative_when_a_degraded_plan_has_no_reason() -> None:
    result = mc.m8_mechanical_readings(plans=[_plan(notes=())])
    assert result.ok is False
    assert "没写降级理由" in "\n".join(result.lines())


def test_m8_full_coverage_needs_no_degradation_note() -> None:
    result = mc.m8_mechanical_readings(
        plans=[_plan(coverage=materials.COVERAGE_FULL, notes=())]
    )
    assert result.ok is True


# --------------------------------------------------------------------------- #
# 汇总（run_all）
# --------------------------------------------------------------------------- #
def _input(**over: object) -> mc.CheckInput:
    base: dict[str, object] = {
        "m1": _m1(),
        "m2": {"skin_ids": [2232, 2240], "menu_ids": [2232], "new_id": 2240},
        "m3": {"texture_family": "T-72B", "item_family": "T-72B"},
        "m4": {"refs": ()},
        "m5": {"texture_format": 12},
        "m6": {"problems": (), "preload_ids": [900], "new_path_ids": [900]},
        "m7": {"texts": ["新增 / 删除 / 修改"]},
        "m8": {"plans": [_plan()]},
    }
    base.update(over)
    return mc.CheckInput(**base)  # type: ignore[arg-type]


def test_run_all_positive() -> None:
    report = mc.run_all(_input())
    assert report.ok is True
    assert len(report.results) == 8
    assert report.failed == () and report.skipped == ()
    text = "\n".join(report.lines())
    assert text.startswith("材质级判据 M1–M8：全绿") and "M8" in text


def test_run_all_negative_reports_the_bad_one() -> None:
    """负控：把 M5 那颗格式改成 BC5 ⇒ 汇总必须红，且点出是 M5。"""
    report = mc.run_all(_input(m5={"texture_format": 27}))
    assert report.ok is False
    assert [item.key for item in report.failed] == ["M5"]
    assert "❌ M5" in "\n".join(report.lines())


def test_run_all_counts_missing_readings_as_skipped_not_green() -> None:
    """没给读数的那条记「跳过」—— 它**不算绿**，也不算红（如实列出来）。"""
    report = mc.run_all(mc.CheckInput())
    assert report.ok is True
    assert len(report.skipped) == 8
    assert "8 条跳过" in report.lines()[0]
    assert all(item.lines()[0].startswith("跳过") for item in report.skipped)


def test_run_all_carries_extra_results() -> None:
    extra = mc.CheckResult(key="M9", title="自定义", ok=False, readings=("自报一条红",))
    report = mc.run_all(mc.CheckInput(extras=(extra,)))
    assert report.ok is False and [item.key for item in report.failed] == ["M9"]


# --------------------------------------------------------------------------- #
# 口径：可编码集只有一份
# --------------------------------------------------------------------------- #
def test_encodable_formats_comes_from_the_textures_module() -> None:
    """`encodable_formats()` 必须等于 `textures.ENCODABLE_FORMATS`（⛔ 不在这里抄第二份）。"""
    from ba_mod_maker.asset_kernel import textures

    assert mc.encodable_formats() == tuple(textures.ENCODABLE_FORMATS)
    source = (
        Path(__file__).resolve().parents[1]
        / "src" / "ba_mod_maker" / "asset_kernel" / "material_check.py"
    ).read_text("utf-8")
    assert "(10, 12)" not in source, "可编码集被抄成字面量了"
