# pure · 纯 Python 共享池

放**与解释器版本无关**的纯 Python 包（一份，三档解释器共用）：

- UnityPy 1.25.3（0 个 .pyd/.so）
- fsspec（UnityPy 顶层硬依赖）
- attrs（UnityPy 类装饰）

⚠ 池子为空时 runtime.py 会显式回落并告警，⛔ 不静默。

W2 未放实际包：见 ../VERSIONS.md §4（挂账）。
