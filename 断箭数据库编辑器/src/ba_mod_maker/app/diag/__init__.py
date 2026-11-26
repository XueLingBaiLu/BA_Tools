"""诊断（用户点名要的"专门用来诊断产品"的那一套）。

    app/diag/log.py     黑匣子：文件日志 + 未捕获异常 + Qt 告警 + 结构化事件
    app/diag/ui.py      看日志 / 调试 的两个对话框（☰ -> 👁 视图）
    app/diag/report.py  Debug 模式的纯函数（一行体检 / 环境 / 最近事件 / 落盘）—— **零 Qt**

界面关键路径（卡片装配、保存、换/克隆）都要 `diag.event(...)` / `diag.guard(...)`，
不然下次再出"左栏整块不见了"这种看不见的毛病，还是只能靠猜。

## ⛔ 这个文件不许有模块级 `from ...log import ...`（2026-10-17 改；理由不是洁癖）

硬判据（`tests/test_diag_report.py::test_importing_report_does_not_pull_qt`）：

    python -c "import sys, ba_mod_maker.app.diag.report; print('PySide6' in sys.modules)"
    -> 必须是 False

为什么：Debug 模式的**用例本身就是"产品坏了"**，而产品坏掉最常见的一种是
**Qt 起不来**（缺 DLL / 显卡驱动 / 打包漏文件）。那一刻 CLI `ba-mod-maker diag`
必须还能跑 —— 否则用户连"把诊断发给我们"这件事都做不到。而 `log.py` 在模块级
`from PySide6.QtCore import ...`：这个包在 import 期只要碰它一下，诊断就跟着一起死。

所以这里用 **PEP 562 惰性 `__getattr__`**：模块级**只 import 标准库**，
`__all__` 里的名字第一次被访问时才去拉各自的子模块（`log` → PySide6）。
现有 6 处 import 点（cli/diag.py · app/diag/ui.py ×2 · shell/main_window.py ·
screens/armory/detail_panel.py · screens/editor/pages/base.py ×2）全是
`from ba_mod_maker.app.diag import 名字` / `from ba_mod_maker.app import diag`，
惰性 `__getattr__` 照样给得到 —— **一个调用方都不用改**。

⚠️ 陌生属性必须抛 `AttributeError`（⛔ 不许返回 `None` 冒充"有"）：除了别骗人，
`from ba_mod_maker.app.diag import report` 这条路**也靠它** —— 只有 `__getattr__`
先说"我不知道 report"，importlib 才会去导 `ba_mod_maker.app.diag.report` 那个子模块。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

#: 每个导出名住在哪个子模块 —— **唯一一份映射**，`__all__` 由它派生（别另抄一份）。
_EXPORTS: dict[str, str] = {
    "StrayWindowGuard": "log",
    "event": "log",
    "failure": "log",
    "guard": "log",
    "install_all": "log",
    "install_stray_window_guard": "log",
    "set_context": "log",
    "tail": "log",
    "warn": "log",
}

#: 惰性 re-export 的名单 —— 与惰性化之前**一模一样**的 9 个名字（⛔ 别缩水）。
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """第一次访问 `diag.<名字>` 时才把它从子模块拿进来（并缓存进 `globals()`）。

    ⛔ 认不出来就抛 `AttributeError` —— 见模块头最后一段（子模块导入靠它；
    返回 None 会让 `from ba_mod_maker.app.diag import report` 拿到一个假的 None）。
    """
    submodule = _EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{submodule}", __name__), name)
    globals()[name] = value  # 缓存：第二次访问不再走 __getattr__（也保证 id 稳定）
    return value


def __dir__() -> list[str]:
    """`dir(ba_mod_maker.app.diag)` 能看到全部导出名 —— 而且**一个模块都不 import**。

    连 `globals()` 一起给上：这样 `__name__` / `__all__` 这些模块该有的名字照常在（
    只回 `__all__` 会把它们从 `dir()` 里抹掉，看着不像一个正常的模块）。
    """
    return sorted(set(__all__) | set(globals()))
