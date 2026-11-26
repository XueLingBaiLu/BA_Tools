"""顶栏那个**常驻保存状态胶囊**（界面重做定稿 块 1）。

用户口径：

    删掉保存按钮。改成顶栏常驻状态胶囊；写入失败时变红（错误仍要弹）。

为什么要有它：改一格就自动写盘，写盘这件事**必须看得见** —— 否则用户不知道
"我这一下到底存了没有"。所以胶囊有三种状态，字都不一样：

| 状态 | 长什么样 | 什么时候 |
|---|---|---|
| saved | ✓ 已存 BA_Mod.zip · 12:03:45 | 上一次写盘成功（带时间） |
| dirty | ● 有改动… | 正在写 / 写之前 |
| failed | ⚠ 没写成功 | 写失败（同时**仍然弹窗**，见 AGENTS §5） |

时间用 24 小时制的 HH:MM:SS —— 用户看的是"刚才那一下存没存"，不是日期。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

STATE_SAVED = "saved"
STATE_DIRTY = "dirty"
STATE_FAILED = "failed"


class SavePill(QLabel):
    """顶栏那个胶囊：一行字，随时说清"存了没有"。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "save-pill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state = ""
        self.set_state(STATE_DIRTY, "● 尚未打开数据源")

    # ------------------------------------------------------------------ #

    def set_state(self, state: str, text: str, *, tip: str = "") -> None:
        self._state = state
        self.setText(text)
        self.setToolTip(tip or text)
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def mark_saved(self, package_name: str, *, when: datetime | None = None, tip: str = "") -> None:
        moment = (when or datetime.now()).strftime("%H:%M:%S")
        self.set_state(STATE_SAVED, f"✓ 已存 {package_name} · {moment}", tip=tip)

    def mark_dirty(self, text: str = "● 有改动…", *, tip: str = "") -> None:
        self.set_state(STATE_DIRTY, text, tip=tip)

    def mark_failed(self, text: str = "⚠ 没写成功", *, tip: str = "") -> None:
        self.set_state(STATE_FAILED, text, tip=tip)

    @property
    def state(self) -> str:
        return self._state


__all__ = ["STATE_DIRTY", "STATE_FAILED", "STATE_SAVED", "SavePill"]
