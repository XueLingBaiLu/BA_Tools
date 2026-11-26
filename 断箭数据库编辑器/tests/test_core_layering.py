"""架构硬约束的自动守卫。

「core 零 GUI 依赖」不能只靠自觉——用测试把它钉死。
这个测试一旦失败，说明有人把 Qt 引进了领域层，必须在合并前修正。

同样守 viewmodel 层：主屏最核心的规则（卡片集合、行分组）住在那里，
必须是纯 Python，才能在没有显示环境的 CI 里直接断言。
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "ba_mod_maker"
CORE_DIR = _SRC / "core"
VIEWMODEL_DIR = _SRC / "app" / "viewmodel"

_FORBIDDEN_ROOTS = {"PySide6", "PyQt5", "PyQt6", "shiboken6"}


def _iter_python_files(root: Path = CORE_DIR) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _qt_violations(root: Path) -> list[str]:
    """扫一个目录下所有 .py，返回引用了 Qt 的位置描述。"""
    violations: list[str] = []
    for path in _iter_python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root_name = name.split(".")[0]
                if root_name in _FORBIDDEN_ROOTS:
                    violations.append(f"{path.relative_to(root)}:{node.lineno} -> {name}")
    return violations


def test_core_has_python_files() -> None:
    """防止路径写错导致下面的检查空跑。"""
    assert _iter_python_files(), f"在 {CORE_DIR} 下没找到任何 .py"


def test_core_never_imports_qt() -> None:
    """领域层不得出现任何 Qt 符号。"""
    violations = _qt_violations(CORE_DIR)
    assert not violations, "core 层出现了 Qt 依赖：\n" + "\n".join(violations)


def test_viewmodel_has_python_files() -> None:
    """同上：路径写错会让守卫空跑。"""
    assert _iter_python_files(VIEWMODEL_DIR), f"在 {VIEWMODEL_DIR} 下没找到任何 .py"


def test_viewmodel_never_imports_qt() -> None:
    """视图模型层不得出现任何 Qt 符号。

    理由：主屏的卡片集合、行分组、排序都是**规则**，必须是可直接断言的纯函数。
    一旦这里能 import Qt，这些规则就会滑进控件里，只能靠肉眼看。
    """
    violations = _qt_violations(VIEWMODEL_DIR)
    assert not violations, "viewmodel 层出现了 Qt 依赖：\n" + "\n".join(violations)
