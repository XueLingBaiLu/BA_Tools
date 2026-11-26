"""把控件从界面上摘掉（零业务，只有一条 Qt 的坑）。

## 为什么必须 hide() 再 setParent(None)

`setParent(None)` 会把控件**变成顶层窗口**。如果那一刻它还可见，
Windows 就会真的把它映射成一个悬浮小窗 —— 在用户眼里就是
"有时候会闪出一个小弹窗"（用户实测报的就是这个），
而它下一轮事件循环就被 `deleteLater()` 销毁了，所以极难复现、也极难定位。

所以顺序是：**先 hide()，再 setParent(None)，最后 deleteLater()**。
`deleteLater()` 会在下一轮事件循环删掉它，那次 `setParent(None)` 只是为了
让它立刻从父控件的子树里消失（布局、findChildren 都不再看见它）。

## 什么时候不用它

控件本来就没显示过（刚 new 出来还没 addWidget）时，直接用 `deleteLater()` 就行；
但那种地方也用这个函数**不会有副作用** —— 统一走一条路更不容易错。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QComboBox, QWidget


def detach(widget: QWidget | None) -> None:
    """把控件摘下来并排队销毁。**先 hide()** —— 见模块注释。"""
    if widget is None:
        return
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def close_editor_popups(editor: QWidget | None) -> None:
    """收工前把就地编辑器**弹出来的浮层**一起收掉（第二条 Qt 的坑）。

    ❗ 下拉 / 候选的浮层是 **顶层窗**（`Qt::Popup`），**不随父控件销毁而消失**。
    实测（_work/probe_rail_popup_leftover.py，编成台右栏点一个外键值）：
        弹开下拉后 → 可见顶层残窗 QWidget(1360,247,321,6)
        提交之后   → 可见顶层残窗 QWidget(1360,247,321,306)   ← 编辑器已收，这个空壳还留着
    用户看到的就是"一个黑框盖住下面几行"（2026-10-01 实机反馈）。
    凡是"收就地编辑器"的地方都调它：`BenchRail.close_editor` / `EntryRow.close_editor`。
    """
    if editor is not None:
        for combo in editor.findChildren(QComboBox):
            combo.hidePopup()
            completer = combo.completer()
            popup = completer.popup() if completer is not None else None
            if popup is not None and popup.isVisible():
                popup.hide()
    # ❗ 卷动壳**与 editor 无关**（它是 Qt 自己造的顶层窗）—— `editor is None` 也要扫。
    #    从前这里是一句早退（`if editor is None: return`），于是"编辑器早被重画摘掉、
    #    壳还留在屏幕上"那种局面之后**谁都不会再来收它**：用户 2026-10-02 截图里那个黑框
    #    就是这么留下来的（实机反馈原文：「有BUG，你看一下」）。
    hide_roll_effects()


def hide_roll_effects() -> int:
    """收掉 Qt 下拉那个**卷动特效壳**（`QRollEffect`），返回收了几个。

    ❗ 它是**第三个顶层窗**：Qt 给下拉的展开/收起做动画用的壳，类名 `QRollEffect`。
    实测（_work/probe_rail_popup_leftover.py，编成台右栏点一个外键值）：
        弹开下拉后 → 可见顶层残窗 QRollEffect(1360,247,321,6)   ← 才 6px（动画刚开始）
        提交之后   → 可见顶层残窗 QRollEffect(1360,247,321,306)  ← 编辑器收了，它**留在屏幕上**
    用户看到的就是"一个黑框盖住下面几行"（2026-10-01 实机反馈）。
    `hidePopup()` 收不掉它（那不是 QComboBox 的 view），只能按类名收。
    """
    hidden = 0
    for widget in QApplication.topLevelWidgets():
        if widget.isVisible() and widget.metaObject().className() == "QRollEffect":
            widget.hide()
            hidden += 1
    return hidden


__all__ = ["close_editor_popups", "detach", "hide_roll_effects"]
