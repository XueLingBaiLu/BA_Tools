"""[守] 每一颗 QPushButton 都要自己设成手型光标。

用户 2026-10-03：「鼠标放在按钮上面的话，有时候不会变成准备点击👆的光标」。

尺子（AST，不显示任何界面）：src 里每个 `X = QPushButton(...)`，在**它所在的那个函数**里
必须有 `X.setCursor(<...PointingHandCursor>)`。仓里 bench_kit.BenchAction 是自己 __init__ 里设的，
所以这条只管直接 new QPushButton 的地方。

为什么用 AST 而不是真机逐个悬停：真机只能量到**当前翻到的那一页** —— 首轮实测只量到 2 处，
静态全扫揪出 24 处。见 AGENTS.md §0.9 动作 2/3（尺子要能红、规矩先转机械断言）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ba_mod_maker"


class _Scan(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[ast.AST] = []
        self.missing: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        is_button = isinstance(node.func, ast.Name) and node.func.id == "QPushButton"
        if is_button:
            scope = next((s for s in reversed(self.stack) if isinstance(s, ast.FunctionDef)), None)
            label = scope.name if scope is not None else "<模块级>"
            if scope is None or not self._cursor_set(scope):
                self.missing.append(f"{label}() 里的 QPushButton@{node.lineno}")
        self.generic_visit(node)

    @staticmethod
    def _cursor_set(scope: ast.FunctionDef) -> bool:
        """这个函数里有没有任何一处 setCursor(PointingHandCursor)。"""
        for node in ast.walk(scope):
            hit = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setCursor"
                and "PointingHandCursor" in ast.unparse(node)
            )
            if hit:
                return True
        return False


def test_every_push_button_declares_the_hand_cursor() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scan = _Scan()
        scan.visit(ast.parse(path.read_text(encoding="utf-8")))
        rel = str(path.relative_to(ROOT)).replace(chr(92), "/")
        offenders += [f"{rel} → {line}" for line in scan.missing]
    assert not offenders, "这些 QPushButton 建出来没设手型光标（悬停不会变成👆）：\n" + "\n".join(
        offenders
    )
