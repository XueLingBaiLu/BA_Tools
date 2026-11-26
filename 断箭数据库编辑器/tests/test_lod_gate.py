r"""tests/test_lod_gate.py —— P3-5 LOD 阶梯闸门的机械断言。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_lod_gate.py -q -n 0`
  -> 通过数从 0 变 N；其中「**违规必须红**」与「**三条禁红线必须不红**」两段是重点。

## 本文件量什么（每条都带正 / 负控）

| 段 | 判据 | 负控（必须绿） |
|---|---|---|
| A | 内置自检：**正控 3/3 报红 · 负控 4/4 绿 · rc=0** | 就是那 4 条合法件 |
| B | R1 顶点增加 / R2 空级（两种）/ R3 链断，**逐条必须红**且消息带码 | — |
| C | **三条禁红线**：bbox 逐位相同 / 比 < p10 / 步比 = 1.0 —— 必须**绿** | ★ 这一段是重点 |
| D | 边界与坏输入（0 级 / 0 顶点 / 没给阈值 / 空 dict） | 不抛、不假绿 |
| E | 黄牌：出带的比**只警告、不改 ok** | ok 仍为 True |
| F | CLI rc 口径（`--selftest` / `--json` / `--check` 好件与坏件 / 坏路径 / 坏参数） | 好件 rc=0 |

## 为什么 C 段是重点

这一晚量到的：**任何 ≥ 0.0814 的比值红线都会让 ≥10% 的游戏原版件被判红**，
**bbox 逐位相同当红档会让 98.3% 的原版件被判红**，**步比 = 1.0 在原版里合法（p90）**。
⇒ 一条红线如果会把原版件判红，那它就不是规格，是 bug。C 段就是把这句话钉成断言。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "lod_gate.py"

_spec = importlib.util.spec_from_file_location("bamod_lod_gate", CLI)
assert _spec and _spec.loader
lod = importlib.util.module_from_spec(_spec)
sys.modules["bamod_lod_gate"] = lod
_spec.loader.exec_module(lod)


def _lv(v: int, bbox=(1.0, 1.0, 1.0), renderer: bool = True):
    return lod.LodLevel(mesh="m", vertices=v, bbox=bbox, renderer=renderer)


def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=str(REPO_ROOT),
    )


# --------------------------------------------------------------------------- #
# A · 内置自检：正控 3/3 报红 · 负控 4/4 绿 · rc=0
# --------------------------------------------------------------------------- #
def test_selftest_positive_fixtures_all_go_red() -> None:
    """★ 正控 = 已知违规夹具（AGENTS §0.9 动作 2：能红才算尺子）。"""
    run = lod.selftest()
    assert run.positive_ok == len(lod.POSITIVE) == 3, [r for r in run.rows if not r[3]]
    for row_id, _title, reading, ok in run.rows[:3]:
        assert ok, (row_id, reading)
        assert reading.startswith("[RED]")


def test_selftest_negative_fixtures_all_stay_green() -> None:
    """★ 负控 = 合法件（⛔ 没有负控的判据不许进表）。"""
    run = lod.selftest()
    assert run.negative_ok == len(lod.NEGATIVE) == 4, [r for r in run.rows if not r[3]]
    for row_id, _title, reading, ok in run.rows[3:]:
        assert ok, (row_id, reading)
        assert reading.startswith("[OK ]")


def test_selftest_rc_is_zero_only_when_both_sides_pass() -> None:
    assert lod.selftest().rc == 0


# --------------------------------------------------------------------------- #
# B · 红档逐条（正控）
# --------------------------------------------------------------------------- #
def test_r1_vertex_increase_is_red() -> None:
    v = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(1200)), (0.1, 0.04)))
    assert not v.ok
    assert any(r.startswith("R1") for r in v.reds), v.reds


def test_r2_empty_level_is_red_both_flavours() -> None:
    no_renderer = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(500, renderer=False)), (0.1, 0.04)))
    zero_verts = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(0)), (0.1, 0.04)))
    assert not no_renderer.ok and any(r.startswith("R2") for r in no_renderer.reds)
    assert not zero_verts.ok and any(r.startswith("R2") for r in zero_verts.reds)


def test_r3_threshold_count_mismatch_is_red() -> None:
    short = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(400)), (0.1, 0.04, 0.001)))
    long_ = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(400), _lv(120)), (0.1, 0.04)))
    assert not short.ok and any(r.startswith("R3") for r in short.reds)
    assert not long_.ok and any(r.startswith("R3") for r in long_.reds)


def test_red_messages_name_the_rule_code() -> None:
    """读数里必须带码（R1/R2/R3）—— 否则接闸门表的人分不清是哪条。"""
    v = lod.judge(lod.LodGroup("x", (_lv(10), _lv(99)), (0.1, 0.04)))
    assert v.reds and all(r[:2] in {"R1", "R2", "R3"} for r in v.reds)


# --------------------------------------------------------------------------- #
# C · ★★ 三条禁红线（必须**不**红）
# --------------------------------------------------------------------------- #
def test_bbox_bitwise_difference_is_never_a_red_line() -> None:
    """★ 禁红线①：bbox 逐位相同 —— 原版只有 54/3248 = 1.7% 相同。

    拿它当红档 ⇒ **98.3% 的游戏原版件会被判红**。所以 bbox 只当读数，永不当红档。
    """
    same = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(440)), (0.1, 0.04)))
    diff = lod.judge(lod.LodGroup("x", (_lv(1000, bbox=(1.0, 1.0, 1.0)),
                                      _lv(440, bbox=(2.0, 2.0, 2.0))), (0.1, 0.04)))
    assert same.ok and diff.ok, (same.reds, diff.reds)


def test_a_ratio_below_p10_is_never_a_red_line() -> None:
    """★ 禁红线②：**任何 ≥0.0814 的比值红线都会让 ≥10% 的原版件被判红**。

    这里喂一个比 0.05（远低于整体 p10 = 0.0814）—— 只许出黄牌，**不许红**。
    """
    v = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(50)), (0.1, 0.04)))
    assert v.ok, v.reds
    assert v.warns, "低比要出黄牌（否则这尺子什么都不说）"


def test_ratio_of_one_is_never_a_red_line() -> None:
    """★ 禁红线③：步比 = 1.0 —— 原版 p90 = 1.0，即"没减面"在原版里合法。"""
    v = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(1000), _lv(1000)), (0.1, 0.04, 0.001)))
    assert v.ok, v.reds


def test_the_three_forbidden_lines_are_written_down_in_the_tool() -> None:
    """⛔ 这三条不许当红档的**理由**必须留在工具里（下个会话别再犯）。"""
    doc = lod.__doc__ or ""
    assert "0.0814" in doc and "10%" in doc
    assert "1.7%" in doc and "98.3%" in doc
    assert "p90 = **1.0**" in doc or "p90 = 1.0" in doc
    assert "不是规格，是 bug" in doc


# --------------------------------------------------------------------------- #
# D · 边界与坏输入
# --------------------------------------------------------------------------- #
def test_empty_group_is_red_not_pass() -> None:
    v = lod.judge(lod.LodGroup("x", (), ()))
    assert not v.ok and any(r.startswith("R2") for r in v.reds)


def test_missing_thresholds_do_not_trigger_r3() -> None:
    """没给阈值表时不许凭空判 R3（拿不到就不判，⛔ 不猜）。"""
    v = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(400)), ()))
    assert v.ok, v.reds


def test_group_from_dict_reads_the_documented_shape() -> None:
    g = lod.group_from_dict({"name": "u", "levels": [{"mesh": "a", "vertices": 10},
                                                     {"mesh": "b", "vertices": 4}],
                             "thresholds": [0.1, 0.04]})
    assert isinstance(g, lod.LodGroup) and len(g.levels) == 2 and g.thresholds == (0.1, 0.04)
    assert lod.judge(g).ok


def test_group_from_dict_defaults_renderer_true() -> None:
    g = lod.group_from_dict({"name": "u", "levels": [{"vertices": 10}]})
    assert g.levels[0].renderer is True


def test_malformed_dict_raises_instead_of_passing() -> None:
    with pytest.raises(KeyError):
        lod.group_from_dict({"name": "u"})          # 缺 levels ⇒ 抛，⛔ 不许当空表放过


# --------------------------------------------------------------------------- #
# E · 黄牌不改 ok
# --------------------------------------------------------------------------- #
def test_out_of_band_ratio_only_warns() -> None:
    v = lod.judge(lod.LodGroup("x", (_lv(1000), _lv(20)), (0.1, 0.04)))
    assert v.ok and v.warns and v.steps and abs(v.steps[0][1] - 0.02) < 1e-9


# --------------------------------------------------------------------------- #
# F · CLI 的 rc 口径
# --------------------------------------------------------------------------- #
def test_cli_selftest_rc_zero() -> None:
    r = _run_cli("--selftest")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "正控 3/3 报红" in r.stdout and "负控 4/4 绿" in r.stdout


def test_cli_json_carries_the_forbidden_lines() -> None:
    r = _run_cli("--json")
    assert r.returncode == 0
    doc = json.loads(r.stdout)
    assert doc["schema"] == "bamod-lod-gate/1"
    assert doc["positive"] == {"n": 3, "ok": 3} and doc["negative"] == {"n": 4, "ok": 4}
    assert len(doc["forbidden_red_lines"]) == 3


def test_cli_check_good_payload_is_zero(tmp_path: Path) -> None:
    p = tmp_path / "good.json"
    p.write_text(json.dumps({"groups": [
        {"name": "ok", "levels": [{"mesh": "a", "vertices": 1000}, {"mesh": "b", "vertices": 440}],
         "thresholds": [0.1, 0.04]}]}), encoding="utf-8")
    r = _run_cli("--check", str(p))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_cli_check_bad_payload_is_one(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"groups": [
        {"name": "bad", "levels": [{"mesh": "a", "vertices": 10}, {"mesh": "b", "vertices": 99}],
         "thresholds": [0.1, 0.04]}]}), encoding="utf-8")
    r = _run_cli("--check", str(p))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL" in r.stdout and "R1" in r.stdout


def test_cli_missing_file_is_two_not_zero(tmp_path: Path) -> None:
    r = _run_cli("--check", str(tmp_path / "not_here.json"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "跑不动" in r.stderr


def test_cli_bad_argument_exits_two() -> None:
    r = _run_cli("--definitely-not-a-flag")
    assert r.returncode == 2


def test_cli_readings_says_something_either_way() -> None:
    """读得到就印分布，读不到就印「未读」—— ⛔ 不许安静地返回 0 装成功。"""
    r = _run_cli("--readings")
    assert r.returncode == 0, r.stdout + r.stderr
    assert ("原版参照" in r.stdout) or ("未读" in r.stdout)


# --------------------------------------------------------------------------- #
# G · 薄封装转发正确（判据逻辑在 src/ba_mod_maker/asset_kernel/lod_gate.py，tools/ 只打印）
# --------------------------------------------------------------------------- #
def _kernel():
    from ba_mod_maker.asset_kernel import lod_gate as kernel

    return kernel


def test_wrapper_forwards_the_kernel_objects_verbatim() -> None:
    """★ 转发的是**同一个对象**，⛔ 不是第二份实现（kb/19 §1 入口只有一个）。"""
    k = _kernel()
    assert lod.judge is k.judge
    assert lod.selftest is k.selftest
    assert lod.group_from_dict is k.group_from_dict
    assert lod.LodGroup is k.LodGroup and lod.LodLevel is k.LodLevel
    assert lod.POSITIVE == k.POSITIVE and lod.NEGATIVE == k.NEGATIVE
    assert lod.FORBIDDEN_RED_LINES == k.FORBIDDEN_RED_LINES


def test_wrapper_docstring_carries_the_kernel_docstring() -> None:
    """三条禁红线 + 两条未读的**正文只有一份**（在 src/），tools/ 引用它。

    ⛔ 不复制第二份：两处各写一遍，早晚会有一处先改、另一处留在错的版本上。
    """
    k = _kernel()
    assert k.__doc__ in lod.__doc__
    for token in ("0.0814", "98.3%", "不是规格，是 bug", "LOD2→LOD3 的减面比未读"):
        assert token in k.__doc__, token
        assert token in lod.__doc__, token


def test_kernel_judges_without_any_cli() -> None:
    """kernel 自己就能判（不依赖 tools/ 与 argparse）—— 否则「逻辑搬进 src/」是假的。"""
    k = _kernel()
    v = k.judge(k.LodGroup("x", (k.LodLevel("a", 10), k.LodLevel("b", 99)), (0.1, 0.04)))
    assert not v.ok and any(r.startswith("R1") for r in v.reds)
    run = k.selftest()
    assert (run.positive_ok, run.negative_ok, run.rc) == (3, 4, 0)


def test_kernel_and_wrapper_point_at_the_same_readings_file() -> None:
    k = _kernel()
    assert lod.READINGS == k.READINGS
    doc = k.readings_document()
    if doc is None:
        pytest.skip("缺 _work/p3lod/lod_units.json（先跑 _work/p3lod/probe.py）")
    assert doc["lod_groups"] == 3103 and doc["pairs"] == 4854
