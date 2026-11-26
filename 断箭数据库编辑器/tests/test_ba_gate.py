"""tools/ba_gate.py 的用例（只跑本文件：`pytest tests/test_ba_gate.py -q`）。

三块，每块都带**能红的负控**：

* plan：假 product 路径 / 行号越界 / 判据恒真 / 「没读」缺依据 ⇒ 必须红；真读经三行 ⇒ 绿；
* dig ：不存在的符号 ⇒ 四门 0 命中，且报告**不许出现「没有」二字**（0 命中 ≠ 没有）；
* unity：预检只给命令（⛔ 不起 Unity、不报进程状态）；since 校验按产物 **mtime** 判。

⚠️ dig / unity 预检用例会读真实语料（_work/decomp-index、kb/、_work/*.bundle），
合计约 6 秒 —— 这是「一条命令出证据包」的固有成本，不是用例写慢了。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import ba_gate as bg  # noqa: E402

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

BAD_SYMBOL = "ZqnaWobbleXyz999"


def good_payload() -> dict:
    """一条**真**读经三行＋真判据：product 与 db 都指向本仓真实存在的件。"""
    return {
        "product": "tools/ba_boot.py::build_report",
        "db": "Units.Cost",
        "dbNumber": "888",
        "decompile": "没读",
        "decompileBasis": "kb/14-本机工具清单.md",
        "judge": "tools/where.py 中栏 --live",
        "before": 96,
        "after": 0,
    }


# ── plan：正控 ────────────────────────────────────────────────────────────


def test_plan_green_echoes_four_lines():
    ok, report, fails = bg.check_plan(good_payload())
    assert ok is True
    assert fails == []
    for label in ("产品代码", "数据库", "反编译", "判据"):
        assert label in report
    assert "❌" not in report


# ── plan：负控（每一条都必须能红）────────────────────────────────────────


def test_plan_fake_product_path_is_red():
    payload = {**good_payload(), "product": "src/根本不存在的文件.py::foo"}
    ok, _report, fails = bg.check_plan(payload)
    assert ok is False
    assert any("产品代码" in row and "现在该跑" in row for row in fails)


def test_plan_product_function_not_found_is_red():
    ok, _report, _fails = bg.check_plan(
        {**good_payload(), "product": "tools/ba_boot.py::zqna_wobble_xyz_999"}
    )
    assert ok is False


def test_plan_decompile_line_out_of_range_is_red():
    ok, _report, fails = bg.check_plan({**good_payload(), "decompile": "tools/ba_boot.py:999999"})
    assert ok is False
    assert any("行号越界" in row for row in fails)


def test_plan_tautological_judge_is_red():
    ok, _report, fails = bg.check_plan({**good_payload(), "before": 1, "after": 1})
    assert ok is False
    assert any("恒真" in row for row in fails)


def test_plan_not_read_requires_real_basis():
    payload = {**good_payload(), "decompile": "没读", "decompileBasis": ""}
    ok, _report, _fails = bg.check_plan(payload)
    assert ok is False
    payload["decompileBasis"] = "kb/这份文档不存在.md"
    ok, _report, _fails = bg.check_plan(payload)
    assert ok is False


def test_plan_bad_db_column_and_dbnumber_are_red():
    ok, _report, _fails = bg.check_plan({**good_payload(), "db": "NoSuchTable.NoSuchColumn"})
    assert ok is False
    ok, _report, _fails = bg.check_plan({**good_payload(), "dbNumber": "不是数字"})
    assert ok is False


# ── --json：插件只认这一行（单一 JSON、五字段、ok 与判定一致）─────────────


def _run_json(arguments: list[str]) -> tuple[int, list[str], dict]:
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "ba_gate.py"), *arguments],
        cwd=str(TOOLS.parent), capture_output=True, timeout=180, check=False,
    )
    lines = [line for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
    assert len(lines) == 1, f"stdout 必须恰好一行 JSON，实得 {len(lines)} 行：{lines[:3]}"
    return proc.returncode, lines, json.loads(lines[0])


def test_json_mode_is_single_line_and_ok_follows_verdict():
    bad = {**good_payload(), "product": "src/根本不存在.py::foo", "judge": ""}
    rc, _lines, data = _run_json(["--kind", "plan", "--payload", json.dumps(bad), "--json"])
    assert rc == 0, "JSON 模式恒 exit 0（插件 exit≠0 会丢 JSON），判定只能靠 ok 字段"
    assert data["ok"] is False
    assert data["kind"] == "plan"
    for key in ("ok", "kind", "report", "witness", "fail"):
        assert key in data
    assert data["fail"], "不通过时 fail[] 必须有「现在该跑什么」"

    _rc, _lines, data = _run_json(
        ["--kind", "plan", "--payload", json.dumps(good_payload()), "--json"]
    )
    assert data["ok"] is True


# ── dig：0 命中不许说「没有」；证据包有上限 ─────────────────────────────


def test_dig_unknown_symbol_has_zero_hits_and_no_absence_claim():
    report, witness, fail = bg.build_dig(BAD_SYMBOL)
    assert witness["dec_hits"] == 0
    assert witness["db_hits"] == 0
    assert witness["kb_hits"] == 0
    assert witness["dec_files"] == []
    assert "没有" not in report, "0 命中时不许输出「没有」二字（task-2 逐字要求）"
    assert len(report) < 3500
    assert "读经（改代码之前）" in report
    assert fail, "0 命中也要给出「换词再挖」的下一步"


def test_dig_method_hit_points_to_real_isil_txt():
    """门 3 命中方法 ⇒ 必须有**函数体**的 .txt 路径（真实存在、绝对路径、字节数现场 stat）。

    这是 lead 验收抓到的真缺陷：只有 dump.cs 一行签名 = 还是没读到反编译。
    """
    index = WORK / "decomp-index"
    if not (index / "types.tsv").is_file():
        pytest.skip("反编译索引未建（先跑 decomp_index.py build）")
    report, witness, _fail = bg.build_dig("GetAmmoResupplyMultiplier")
    type_lines = [line for line in report.splitlines() if "[类型文件]" in line]
    assert type_lines, report[:1500]
    match = re.search(r"\[类型文件\] (\S+\.txt)  \(([\d,]+) B\)", report)
    assert match, type_lines[0]
    path = Path(match.group(1))
    assert path.is_absolute(), f"要绝对路径：{path}"
    assert path.is_file(), f"路径必须真实存在：{path}"
    assert path.stat().st_size == int(match.group(2).replace(",", "")), "字节数必须是现场 stat 读数"
    assert any(Path(row).is_file() for row in witness["dec_files"]), witness["dec_files"]


def test_cap_keeps_tail_and_respects_limit():
    capped = bg._cap("保头" + "Z" * 50000 + "\n读经（改代码之前）\n- 产品代码：占位")
    assert len(capped) <= bg.MAX_REPORT
    assert "读经（改代码之前）" in capped


# ── unity：只给命令、不查进程；mtime 是判据 ─────────────────────────────


def test_unity_precheck_gives_absolute_command_and_no_process_claim():
    ok, report, _witness, _fails = bg.check_unity({})
    assert ok is True
    assert f"{bg.PY} {bg.UNITY_RENDER}" in report
    assert "mtime" in report
    assert "Get-Process -Name BrokenArrow" in report, "进程只能请 DSH pwsh 查（AGENTS §0.17）"
    assert "不起 Unity" in report


def test_unity_since_uses_artifact_mtime():
    artifact = Path(bg.__file__).resolve()
    mtime_ms = int(artifact.stat().st_mtime * 1000)

    ok, report, witness, _fails = bg.check_unity(
        {"artifact": str(artifact), "since": mtime_ms - 5000}
    )
    assert ok is True
    assert witness["mtime"] == mtime_ms
    assert witness["artifact"]

    ok, report, _witness, fails = bg.check_unity(
        {"artifact": str(artifact), "since": mtime_ms + 10 ** 9}
    )
    assert ok is False
    assert "不算数" in report
    assert fails
