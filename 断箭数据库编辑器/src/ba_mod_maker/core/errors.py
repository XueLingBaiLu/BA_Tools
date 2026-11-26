"""领域层异常体系。

设计约定：

- 全部继承 `BaModMakerError`，调用方（GUI / CLI）只需捕一个根类型。
- `UserFacingError` 代表「可直接展示给用户」的错误（自带人话说明与修复建议），
  未继承它的异常则视为内部缺陷，应当上报而非静默吞掉。
- `hint` 字段给 GUI 直接展示：告诉用户下一步该做什么。
"""

from __future__ import annotations

from typing import Any


class BaModMakerError(Exception):
    """本产品全部异常的根类型。"""

    def __init__(self, message: str, *, hint: str | None = None, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.context: dict[str, Any] = context

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}（建议：{self.hint}）"
        return self.message


class UserFacingError(BaModMakerError):
    """可安全展示给用户的错误。"""


class DataSourceError(UserFacingError):
    """数据源（`DataBaseCompiled`）缺失、损坏或校验失败。"""


class TableError(UserFacingError):
    """单张表的读写、结构或主键出错。"""


class RuleError(BaModMakerError):
    """规则集自身不合法（而非数据违规）。属于内部缺陷。"""


class AssetError(UserFacingError):
    """UnityFS / catalog / bundle / `BA_Mod.zip` 层出错。"""
