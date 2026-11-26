"""领域层（零 GUI 依赖）。

❗ 硬约束：本包（及其子包）**不得** `import PySide6` 或任何 Qt 符号。

子包规划：

- `dbc/`   —— 24 张表明文 JSON 的读写与实例管理
- `rules/` —— 校验引擎（规则数据是**产品自带**的 `rulebook.json`，由 `tools/gen_rulebook.py` 生成）
- `asset/` —— UnityFS / catalog / bundle / `BA_Mod.zip`（将来可原样提升为共享包）

当前仅建立包结构；功能模块随首屏方向确定后逐一落地。
"""

__all__: list[str] = []
