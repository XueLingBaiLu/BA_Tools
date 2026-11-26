"""tools/where.py 那张「规矩 → 尺子 → 断言」表**不许指空**。

由来（2026-10-03）：用户问「该怎么尽量防止你靠猜的弊端？」→ 定下 AGENTS.md §0.9 四条动作，
其中动作 2/3 要求「尺子只认产品标识、规矩一律转机械断言」。那就得防一件事：
**表里写的断言后来被改名 / 删掉，表还照样指过去** —— 下一个会话按它去找，找不到又变成猜。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "where.py"
TESTS = ROOT / "tests"

#: 表里的断言引用形如 tests/test_bench_page.py::test_xxx
REF = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)::([A-Za-z0-9_]+)")


def test_where_rules_point_at_real_tests() -> None:
    text = TOOL.read_text(encoding="utf-8")
    refs = REF.findall(text)
    assert refs, "where.py 里一条断言引用都没有 —— 表被拆了？"
    for name, case in refs:
        body = (TESTS / name).read_text(encoding="utf-8")
        assert f"def {case}(" in body, f"{name} 里没有 {case}（表指空了）"


def test_where_panes_named_by_product_not_by_direction() -> None:
    """尺子必须是**产品标识**：中栏那一行得写着容器属性，不是"中间"两个字。"""
    text = TOOL.read_text(encoding="utf-8")
    assert "BenchPage._scroll" in text, "中栏的尺子没写成产品标识"
    assert "BenchPage.rail" in text, "右栏的尺子没写成产品标识"
    assert "96 处违规" in text, "文件头要留着那次翻车：拿整页当尺子 → 96 个假阳性"
