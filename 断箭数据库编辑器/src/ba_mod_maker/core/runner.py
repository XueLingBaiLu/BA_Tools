"""长任务的**进度**与**取消**——领域层侧的纯 Python 契约，不依赖 Qt。

为什么放在 core 而不是 app：

- “长任务要有进度、要能取消”是**产品行为**，不是 GUI 细节；
- CLI 同样需要它（打包时打印进度条）；
- 让它可脱离 Qt 被测试。

GUI 层只需把 `Progress` 转发到信号即可，不得在主线程直接调用耗时操作。

⚠️ 2026-10-02 清理：这里原先还有 `JobState`（作业生命周期）与 `Job`（可提交作业的
Protocol），**全仓零引用、零实现者** —— GUI 侧 app/shell/tasks.py 有自己的 `_Job(QObject)`，
CLI 直接调函数；协议留着没人实现就是误导，已删。
`CancellationToken` / `CancelledError` **保留**：app/shell/tasks.py:14 写明
"真正的取消要把它一路穿下去"，那是**还没接的线**，不是死件。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Progress:
    """一次进度回报。

    `current` / `total` 同时给出时，GUI 可展示确定进度条；
    只有 `current` 时展示不定进度（busy）。
    """

    message: str = ""
    current: int = 0
    total: int = 0
    detail: str = ""

    @property
    def fraction(self) -> float:
        """进度百分比；无总量时返回 -1.0（表示不定进度）。"""
        if self.total <= 0:
            return -1.0
        return min(1.0, max(0.0, self.current / self.total))


@dataclass(slots=True)
class CancellationToken:
    """协作式取消令牌。

    长任务必须在每个可控粒度处调用 `raise_if_cancelled()`，
    否则用户点“取消”后界面会假死。
    """

    _cancelled: bool = field(default=False, repr=False)

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def raise_if_cancelled(self) -> None:
        """已被取消则抛出 `CancelledError`。"""
        if self._cancelled:
            raise CancelledError()


class CancelledError(Exception):
    """作业被取消。属于正常流程，不应当上报为失败。"""
