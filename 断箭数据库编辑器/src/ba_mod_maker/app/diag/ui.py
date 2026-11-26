"""看日志的小窗口（☰ 菜单 -> 「📜 日志…」）。

用户口径：**"我建议你做个log系统，专门用来诊断产品"**。所以这一页不是给开发者看的
命令行，而是产品里的一个入口：出事时用户点开就能把"产品自己说了什么"拿给助手看。

三样东西：日志文件路径（可复制）+ 最后 200 行 + 「📂 打开日志文件夹」「🔄 刷新」。
**不自动滚动到底**：出错那几行往往就在末尾，但用户在读别处时被拽走更烦。

## 两个对话框，管两件不同的事

| 窗口 | 入口 | 给什么 |
|---|---|---|
| `LogDialog` | ☰ -> 👁 视图 -> 「📜 日志…」 | 原始日志尾部（我要排查时看） |
| `DebugDialog` | ☰ -> 👁 视图 -> 「🐞 调试…」 | **一段能整段粘给助手的话**（用户要报问题的时候） |

两者的分工别搞混：日志是**给我们的原料**，诊断是**给用户的快递箱** ——
一行体检 ＋ 环境变量 ＋ 最近事件，复制/落盘一次到位，不需要用户知道日志文件在哪。
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ba_mod_maker.app.diag import log as diag_log
from ba_mod_maker.app.diag import report as diag_report
from ba_mod_maker.app.theme.tokens import TOKENS

#: 一次显示多少行（够看清"刚才那一下"，也不至于把对话框撑爆）。
TAIL_LINES = 200

#: 调试对话框带的日志尾巴（= 复制/落盘那一段里的「最近事件」）。
#: 与 `report.DEFAULT_TAIL` 是同一个数 —— 这里引用它，别写两份 40。
DIAG_TAIL = diag_report.DEFAULT_TAIL

#: 窗口高度：默认态（明细区藏着，整个窗口只有两行字）要**紧凑**，
#: 勾上详细读数才撑到看得见明细的高度。⛔ 只在两个固定值之间切（不自适应、不做动画）——
#: 自适应要在 resize/字数/DPI 三处同时判，容易变成"跟着鼠标抖"的那一类花活。
#: 数值来自真机走查（2026-10-17）：560 高的默认态中间有 ~700px 纯空，像半成品。
COMPACT_HEIGHT = 380
DETAIL_HEIGHT = 640


class LogDialog(QDialog):
    """日志查看器：路径 + 尾部内容 + 打开文件夹 + 刷新。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("📜 诊断日志")
        self.resize(980, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.lg, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.sm)

        self._path = QLabel("")
        self._path.setProperty("role", "mono")
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._path)

        hint = QLabel(
            "产品出问题时，把这几行连同下面那段一起发给助手就够定位了"
            "（未捕获异常、Qt 告警、卡片装配/写盘这些关键动作都在里面）。"
        )
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._text, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(TOKENS.spacing.sm)
        refresh = QPushButton("🔄 刷新")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.reload)
        buttons.addWidget(refresh)

        copy = QPushButton("📋 复制路径")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(self._copy_path)
        buttons.addWidget(copy)

        reveal = QPushButton("📂 打开日志文件夹")
        reveal.setCursor(Qt.PointingHandCursor)
        reveal.setProperty("variant", "primary")
        reveal.clicked.connect(self._reveal)
        buttons.addWidget(reveal)

        buttons.addStretch(1)
        close = QPushButton("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.reload()

    # ------------------------------------------------------------------ #

    def reload(self) -> None:
        target = diag_log.configured_path() or diag_log.log_path()
        self._path.setText(f"日志文件：{target}")
        self._text.setPlainText(diag_log.tail(TAIL_LINES))

    def _copy_path(self) -> None:
        target = diag_log.configured_path() or diag_log.log_path()
        QGuiApplication.clipboard().setText(str(target))
        self._path.setText(f"已复制路径：{target}")

    def _reveal(self) -> None:
        """打开日志所在文件夹（**选中的是日志文件本身**）。"""
        target = diag_log.configured_path() or diag_log.log_path()
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target.parent)])
        except OSError as exc:
            self._path.setText(f"打不开文件夹（{exc}）；路径：{target}")


class DebugDialog(QDialog):
    """『🐞 调试…』：一行体检 ＋ 一键复制诊断 ＋ 落盘 ＋ 「详细读数」开关。

    用户 2026-10-17 点名要的两个 Debug 模式之一，四件事都在这一个窗口里：

    ① **一行体检**（版本 / Python / 当天日志 / 覆盖变量设没设）—— 一眼看完，不用滚；
    ② **📋 复制诊断** —— 一行体检 ＋ 环境 ＋ 最近事件，整段进剪贴板，用户直接粘给我们；
    ③ **详细读数**（默认**关**）—— 勾上才在下面展开那一段；关着也能复制/落盘。
       这个开关只管"**看不看**"，不管"**发不发**"（少一个会让人误会的中间状态）；
    ④ **💾 保存到文件** —— 落 `diag-YYYYmmdd-HHMMSS.txt`，路径写在状态 label 上。

    弹窗规则（AGENTS）：**成功不弹**（状态 label 一句话就够），**失败才弹**。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🐞 调试")
        self.resize(920, COMPACT_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.lg, TOKENS.spacing.md
        )
        root.setSpacing(TOKENS.spacing.sm)

        self._health = QLabel("")
        self._health.setProperty("role", "mono")
        self._health.setWordWrap(True)
        self._health.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._health)

        # ⚠️ 三行字（体检 / 提示 / 状态）的垂直策略必须是 **Maximum**：它们不该长高。
        # 不钉的话默认的 Preferred 会让 QBoxLayout 把剩余高度平摊给它们 —— 这就是
        # 真机走查抓到的那个缺陷（默认态三行字各自悬在一大片空白里）的一半成因；
        # 另一半是下面那条 `self._gap`（明细区藏着时没人吃空间）。

        hint = QLabel(
            "产品出问题时点「📋 复制诊断」，把剪贴板里那一段整段粘给助手就够定位了"
            "（一行体检 ＋ 环境变量 ＋ 最近事件）。"
        )
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 明细区默认**不显示**：一行体检已经回答了八成的问题，勾『详细读数』才展开。
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._detail.setVisible(False)
        root.addWidget(self._detail, 1)

        #: 弹性空档：明细区**藏着**时由它吃掉剩余高度（真机走查抓到的画法缺陷，2026-10-17）。
        #: 不撑开的话 QBoxLayout 会把多出来的高度平摊给三行字 ⇒ 它们各自悬在一大片空白里
        #: （实测：体检行 163px / sizeHint 69px）。
        #: ⚠️ 这里**不能**写 `self._gap = root.addStretch(1)` —— PySide6 的 addStretch()
        #: 返回 **None**（实测），拿不到那个 QSpacerItem 就没法切它的垂直策略。
        self._gap = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        root.addItem(self._gap)

        self._status = QLabel("")
        self._status.setProperty("role", "muted")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        # 三行字只许占自己的 sizeHint（见上面那条注释）：Preferred 的垂直策略会让它们
        # 跟着剩余高度长高 —— 那正是"文字悬在一片空白正中间"的另一半成因。
        for text_line in (self._health, hint, self._status):
            text_line.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        buttons = QHBoxLayout()
        buttons.setSpacing(TOKENS.spacing.sm)
        self._verbose = QCheckBox("详细读数")
        self._verbose.setCursor(Qt.PointingHandCursor)
        self._verbose.setToolTip(
            "勾上就在下面展开完整读数；复制与保存不跟着它变，永远是完整那一段"
        )
        self._verbose.toggled.connect(self._on_verbose)
        buttons.addWidget(self._verbose)
        buttons.addStretch(1)

        copy = QPushButton("📋 复制诊断")
        copy.setCursor(Qt.PointingHandCursor)
        copy.setProperty("variant", "primary")
        copy.clicked.connect(self._copy)
        buttons.addWidget(copy)

        save = QPushButton("💾 保存到文件")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        buttons.addWidget(save)

        close = QPushButton("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.refresh()
        # 开局先给一句"该点哪里"：空着的状态栏会让人以为这个窗口没反应。
        self._status.setText("点「📋 复制诊断」把整段发给助手；「💾 保存到文件」留在本机。")

    # ------------------------------------------------------------------ #

    def diagnostics(self) -> str:
        """『📋 复制诊断』/『💾 保存到文件』拼的那一段。

        **与 `ba-mod-maker diag` 同一份纯函数**（`app/diag/report.py`）——
        用户粘过来的那段和我这边跑出来的必须是同一种东西，⛔ 不许各拼一套。
        """
        return diag_report.diagnostics_text(count=DIAG_TAIL)

    def refresh(self) -> None:
        """重算一行体检（日志目录可能刚被环境变量改过 —— 每次打开都现算）。"""
        self._health.setText(diag_report.health_line())
        if self._verbose.isChecked():
            self._detail.setPlainText(self.diagnostics())

    def _on_verbose(self, checked: bool) -> None:
        """『详细读数』开关：只管展开/收起，**不影响**复制与保存的内容。

        ⚠️ 它同时是**布局**的开关（真机走查抓到的画法缺陷，2026-10-17）：明细区藏着时
        整个窗口只剩三行字，必须有东西吃掉剩余高度，否则 QBoxLayout 会把它平摊给每个
        子项 —— 三行字各自悬在一大片空白里（实测体检行 163px vs sizeHint 69px）。
        """
        self._detail.setVisible(checked)
        # 空间归谁：开着 → 全给明细区（空档收成 Minimum）；关着 → 空档撑开吃掉剩余高度。
        self._gap.changeSize(
            0,
            0,
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Minimum if checked else QSizePolicy.Policy.Expanding,
        )
        self.layout().invalidate()  # changeSize() 不会自己通知布局，这里手动失效一次
        # 窗口也跟着长/缩：默认态紧凑（只有两行字），勾上才把明细区露出来。
        # ⚠️ 宽度照用当前值（用户可能拉宽过），只切高度。
        self.resize(self.width(), DETAIL_HEIGHT if checked else COMPACT_HEIGHT)
        if checked:
            self._detail.setPlainText(self.diagnostics())
            self._status.setText(f"详细读数已展开（日志尾部 {DIAG_TAIL} 行）")
        else:
            self._status.setText("详细读数已收起（复制与保存不受影响，仍是完整那一段）")

    def _copy(self) -> None:
        """整段进剪贴板。成功**不弹窗** —— 状态 label 报字符数就够。"""
        text = self.diagnostics()
        QGuiApplication.clipboard().setText(text)
        self._status.setText(f"已复制诊断（{len(text)} 个字符）—— 直接粘给助手就行")

    def _save(self) -> None:
        """落盘到 `<日志目录>/diag/diag-<时间戳>.txt`。**失败才弹窗**。"""
        result = diag_report.write_dump(self.diagnostics())
        if result.reason:
            self._status.setText(f"保存失败：{result.reason}")
            QMessageBox.warning(self, "保存失败", f"诊断没存上：{result.reason}")
            return
        self._status.setText(f"已保存：{result.path}")


__all__ = ["DIAG_TAIL", "TAIL_LINES", "DebugDialog", "LogDialog"]
