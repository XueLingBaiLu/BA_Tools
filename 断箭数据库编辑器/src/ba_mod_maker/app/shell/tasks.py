"""把一段阻塞调用挪到工作线程 —— 只为了让界面别假死（零业务逻辑）。

为什么需要它：写回 `data.unity3d` 是 7.1 GB / 约 4 分钟。跑在主线程上时事件循环被占住，
Windows 会给窗口盖上「程序未响应」，进度条也不会动（没人画得了），用户只能杀进程 ——
而杀进程正是「游戏目录里多出一个满尺寸 .new 文件」的来源。
README 的硬纪律第 2 条（主线程绝不阻塞）说的就是这件事，这里是它的最小实现。

用法：

    run_in_thread(owner, work, on_success=self._done, on_error=self._failed)

`on_success` / `on_error` **一定在主线程执行**（见 `_Relay` 的说明），可以直接碰控件。

⚠️ 本模块**不做取消**。真正的取消要把 `core/runner.py` 的 `CancellationToken` 一路穿到
`ufs.rewrite_container`（它已经支持 token），再配一个取消按钮 —— 那是另一件事。
宁可不承诺，也不要给一个按了没用的按钮。

⚠️ 调用方有**两项义务**：

1. 任务跑完前不能让 owner 被销毁（窗口关了线程还在写文件）；
2. 任务跑完前不要并发动同一份数据（例如另开一个文件把 reader 关掉）。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

#: 任务存活期间，必须在 Python 侧留住的对象的引用。
#:
#: ⚠️ 这是**实测踩到**的坑，不是防御性编程：`_Job` 没有父对象（有父对象就不能
#: moveToThread），所以 `run_in_thread` 一返回，它就被 GC 回收了 —— 线程里那个
#: `run()` 于是**永远不会执行**，症状是"回调从不回来"，而且不报任何错。
#: 直到线程退出前都不能放手，所以挂在 thread.finished 上再摘。
_ALIVE: set[QObject] = set()


class _Job(QObject):
    """在工作线程里跑一段 Python 调用，把结果或异常原样发回主线程。"""

    done = Signal(object)
    failed = Signal(object)

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self._work = work

    @Slot()
    def run(self) -> None:
        try:
            result = self._work()
        except BaseException as exc:  # noqa: BLE001 —— 不在这里吞，带回主线程再报
            self.failed.emit(exc)
        else:
            self.done.emit(result)


class _Relay(QObject):
    """主线程这一端的接收器：把工作线程的结果转交给普通 Python 回调。

    为什么不能直接把信号接到普通函数/lambda：Qt 的连接类型是按**接收者的线程**判定的，
    而普通函数没有线程归属 —— 那就可能变成直接连接、回调在工作线程里跑，
    于是控件被非主线程碰，症状是偶发崩溃。绑到一个活在主线程的 QObject 上就没有歧义：
    跨线程发射必定走队列连接，回调必定在主线程。
    """

    def __init__(
        self,
        owner: QObject,
        on_success: Callable[[object], None],
        on_error: Callable[[object], None],
    ) -> None:
        super().__init__(owner)  # 父对象在界面线程 → 本对象也归属界面线程
        self._on_success = on_success
        self._on_error = on_error

    @Slot(object)
    def success(self, result: object) -> None:
        self._on_success(result)

    @Slot(object)
    def error(self, exc: object) -> None:
        self._on_error(exc)


def run_in_thread(
    owner: QObject,
    work: Callable[[], object],
    *,
    on_success: Callable[[object], None],
    on_error: Callable[[object], None],
) -> QThread:
    """把 `work` 放到新线程上跑；结束后在 `owner` 所在线程回调。

    `owner` 同时是线程对象的主人与接收器的主人：只要 owner 活着，这些东西就不会被提前回收。
    返回 QThread 只为方便测试与调试，调用方不必保存它。
    """
    thread = QThread(owner)
    job = _Job(work)
    relay = _Relay(owner, on_success, on_error)
    job.moveToThread(thread)

    _ALIVE.add(job)
    thread.finished.connect(lambda: _ALIVE.discard(job))

    thread.started.connect(job.run)
    job.done.connect(relay.success)
    job.failed.connect(relay.error)
    job.done.connect(thread.quit)
    job.failed.connect(thread.quit)
    thread.finished.connect(job.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
