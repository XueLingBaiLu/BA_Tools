"""CLI 标准流契约 · `--help` 的输出**不经 Emitter**，所以掰流必须发生在 argparse 之前。

判据一行（2026-10-10）：
    ```.venv\Scripts\python.exe -m ba_mod_maker.cli skin --help```（GBK 管道）
    → 退出码 **从 1（UnicodeEncodeError）变 0**
正控：把"掰流"这一步从 `main()` 里拿掉，本文件第一条用例**必须红** —— 能红才算尺子。
"""

from __future__ import annotations

import io
import sys

import pytest

from ba_mod_maker.cli import __main__ as cli_main
from ba_mod_maker.cli import emit


class _StopError(Exception):
    """在 parse_args 处打断 main()，免得它真去分发命令。"""


def test_main_forces_utf8_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`emit.force_utf8_streams()` 必须排在 `_build_parser().parse_args()` 之前。"""
    order: list[str] = []

    def fake_force() -> None:
        order.append("force")

    class _Parser:
        def parse_args(self, argv: object) -> object:
            order.append("parse")
            raise _StopError

    monkeypatch.setattr(emit, "force_utf8_streams", fake_force)
    monkeypatch.setattr(cli_main, "_build_parser", lambda: _Parser())

    with pytest.raises(_StopError):
        cli_main.main([])

    assert order == ["force", "parse"], (
        "掰流必须发生在 argparse 之前 —— --help 的输出不走 Emitter，"
        "argparse 直接 print 到标准流；反了的话帮助文本里的符号"
        "会在 GBK 管道下当场 UnicodeEncodeError"
    )


def test_force_utf8_streams_tolerates_replaced_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """标准流被换成 StringIO（用例常见）时不许炸 —— 那不是这里的错。"""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    emit.force_utf8_streams()  # 不抛即通过
