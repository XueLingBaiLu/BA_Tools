"""BA_Mod_Maker 2.0 · 数据表本体。

分层：

- `core` —— 领域层，**零 GUI 依赖**。可脱离 PySide6 独立测试、可被 CLI 驱动。
- `app`  —— 表示层，PySide6。只做展示与交互，不写业务逻辑。
- `cli`  —— 同一套 core 的无人值守入口，用于回归与自动化。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
