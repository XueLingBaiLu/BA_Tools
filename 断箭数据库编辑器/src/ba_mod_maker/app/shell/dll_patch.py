"""外部数据库补丁（`GameAssembly.dll`）的界面。

为什么要有这个对话框，而不是让人去敲命令行：

补丁是**产品功能的一部分**（ADR 0005 把它定成主通道），可在此之前它只是
`tools/` 里一个孤立脚本 —— 用户在界面里根本够不着。而这个补丁决定了
"保存"是"写 7.1 GB / 3 分钟"还是"写 300 KB / 毫秒级"，
漏掉它等于漏掉产品最主要的卖点。

四条设计口径：

1. **状态先说清楚**：装没装、有没有备份、`BA_Mod.zip` 在不在。三个都是"能不能用"的前提。
2. **危险动作要二次确认**：打补丁会改 DLL，还原会覆盖 DLL。两者都问一次。
3. **卸载路径写在脸上**：最省事的"卸载"是删 `BA_Mod.zip`，不是还原 DLL。
   不写出来，用户会以为每次不用都要还原一次 DLL。
4. ⛔ **联机红线贴在脸上**（kb/26，用户拿 7 天账号换来的）：**形态决定能不能联机** ——
   进程内注入（MelonLoader / `version.dll` 劫持 / `Mods/*.dll` 加载器）⇒ **绝不联机**；
   静态文件替换（补丁打了 / `BA_Mod.zip` 在 / DLL 被改过）⇒ **可以联机、风险自负**（⚠ 非官方承诺）。
   只要现场**有注入或有改动**，对话框顶部就有一条**收不起来**的警告；
   而"打补丁 / 还原"这两个**会改游戏目录**的动作，即使现场是干净的也要在确认框里再打一次。
   ⚠ 负控是**故意**的：**没有注入也没有改动就一个字都不显示** —— 乱报一次，用户就会开始无视它。
   文案与**形态判定**的唯一口径都在 `core/asset/dllpatch.py`
   （`ONLINE_REDLINE_HEAD / _BODY / _FORM_*` ＋ `injection_reasons()`），界面不另写一份字面量。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ba_mod_maker.app.theme.fonts import data_font, ui_font
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core.asset import dllpatch
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.errors import UserFacingError



class DllPatchDialog(QDialog):
    """装 / 卸 / 查外部数据库补丁。"""

    def __init__(self, parent=None, game_root=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("外部数据库补丁")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(560)

        self._root = game_root or game_mod.find_game_root_for_db()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        title = QLabel("让游戏从 BA_Mod.zip 读数据库，并忽略换默认卡组")
        title.setFont(ui_font(TOKENS.typography.size_lg, bold=True))
        layout.addWidget(title)

        # ⛔ 联机红线（kb/26）：**没有注入也没有改动**时不显示（负控，见模块文档 4）。
        # 样式走 qss.py 已有的 QLabel[badge="error"]（危险底 + 危险边），这里不写死色值。
        self._redline_badge = QLabel(dllpatch.ONLINE_REDLINE_HEAD)
        self._redline_badge.setProperty("badge", "error")
        self._redline_badge.setFont(ui_font(TOKENS.typography.size_sm, bold=True))
        self._redline_badge.setVisible(False)
        layout.addWidget(self._redline_badge, alignment=Qt.AlignmentFlag.AlignLeft)

        self._redline = QLabel()
        self._redline.setObjectName("online-redline")
        self._redline.setWordWrap(True)
        self._redline.setFont(data_font(TOKENS.typography.size_sm))
        self._redline.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._redline.setVisible(False)
        layout.addWidget(self._redline)

        why = QLabel(
            "装上之后，保存只需写一个 ~300 KB 的 BA_Mod.zip（毫秒级），"
            "不必再重写 7.1 GB 的 data.unity3d。"
            "另外，它会跳过服务端的『换默认卡组』命令（code 72）："
            "卡组里有库里没有的单位（例如 M1A2 T）时，卡组也不会被换成默认卡组。"
        )
        why.setWordWrap(True)
        why.setFont(ui_font(TOKENS.typography.size_sm))
        layout.addWidget(why)

        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setFont(data_font(TOKENS.typography.size_sm))
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._status)

        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.setFont(ui_font(TOKENS.typography.size_sm))
        layout.addWidget(self._note)

        buttons = QHBoxLayout()
        self._apply_btn = QPushButton("打补丁")
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._restore_btn = QPushButton("还原 DLL")
        self._restore_btn.setCursor(Qt.PointingHandCursor)
        self._verify_btn = QPushButton("核对")
        self._verify_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.clicked.connect(self._on_apply)
        self._restore_btn.clicked.connect(self._on_restore)
        self._verify_btn.clicked.connect(self._on_verify)
        buttons.addWidget(self._apply_btn)
        buttons.addWidget(self._restore_btn)
        buttons.addWidget(self._verify_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.refresh()

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """重读状态并刷新按钮可用性。**每次都从磁盘现读**，不缓存。"""
        if self._root is None:
            self._status.setText("找不到游戏目录。")
            self._note.setText("请先确认游戏装在哪里；或设置环境变量 BA_MOD_MAKER_GAME。")
            self._apply_btn.setEnabled(False)
            self._restore_btn.setEnabled(False)
            self._verify_btn.setEnabled(False)
            self._set_redline(None)
            return

        st = dllpatch.status(self._root)
        self._st = st
        self._set_redline(dllpatch.online_redline(self._root))
        self._status.setText("\n".join(st.summary_lines()))
        self._note.setText(dllpatch.uninstall_hint())
        self._apply_btn.setEnabled(st.can_apply)
        self._restore_btn.setEnabled(st.can_restore)
        self._verify_btn.setEnabled(st.has_backup)

    def _set_redline(self, text: str | None) -> None:
        """把联机红线画出来 / 收起来。

        ⛔ 负控口径：**没有改动就一个字都不显示**。乱报一次，用户就会开始无视它
        （AGENTS §0.9 动作 2：报假警的尺子等于没有尺子）。
        """
        show = bool(text)
        self._redline_badge.setVisible(show)
        self._redline.setText(text or "")
        self._redline.setVisible(show)

    def _run(self, fn) -> None:
        """跑一个动作，把结果写进**对话框自己的状态区**，然后刷新。

        不弹"完成"框：状态区就在眼前，弹窗除了要你点一下没别的作用。
        错误仍然打断 —— 那是不该被错过的。

        ⚠️ 2026-11-02：文件系统错误在这里**兜一层**。`core` 已经把它包成
        `UserFacingError`；万一是别的 `OSError`（典型：游戏目录写不进去 ⇒
        `PermissionError[Errno 13]`）也不许穿到 app 的未捕获异常钩子 ——
        穿过一次，用户只看到「exited with code 1」+ 崩溃弹窗
        （`var/logs/app-20261102.log:11-28` 就是这条链）。
        """
        # ★ 2026-11-10（用户口径）：检查删光之后 apply 只剩 286 字节的读写，
        #   同步跑即可 —— 不再挂后台线程（少一层构件，就没有「永远 busy」这种状态）。
        try:
            message = fn()
        except UserFacingError as exc:
            self._say_error(str(exc))
            return
        except OSError as exc:
            self._say_error(f"读写游戏目录失败：{exc}")
            return
        if self._refresh_quietly():
            self._note.setText(message)


    def _say_error(self, text: str) -> None:
        """错误写进本对话框的状态区 —— 2026-11-10 用户口径：别再弹提示框。"""
        self._refresh_quietly()
        self._note.setText("⚠ " + text + chr(10) + dllpatch.WRITE_PERMISSION_HINT)

    def _refresh_quietly(self) -> bool:
        """刷新状态区；**连读盘都失败**时把话说在状态区里（不弹第二个框）。

        返回 False = 状态没刷新成功。调用方此时不许再用动作结果覆盖错误提示 ——
        那会把唯一能看懂的线索盖掉。
        """
        try:
            self.refresh()
        except OSError as exc:
            self._status.setText(f"读不了游戏目录：{exc}")
            self._note.setText(dllpatch.WRITE_PERMISSION_HINT)
            self._apply_btn.setEnabled(False)
            self._restore_btn.setEnabled(False)
            self._verify_btn.setEnabled(False)
            return False
        return True

    def _on_apply(self) -> None:
        if self._root is None:
            return
        # 有没有备份，风险不一样，所以话术要分开 —— 但只问一次。
        # ⛔ 备份名不许写死：2026-10-14 起备份名**带版本**（GameAssembly.dll.<sha8>.orig），
        #    写死会指向一个盘上不存在的文件；有备份时报现场那一份的名字。
        if self._st.has_backup:
            tail = f"备份已经存在（{self._st.backup.name}），可以随时还原。"
        else:
            tail = (
                "会先写一份几百字节的反向 Δ 备份（GameAssembly.dll.patchdelta），"
                "只记被改的那几段原字节，不复制整包。"
            )
        self._note.setText(tail)
        self._run(lambda: dllpatch.apply(self._root))

    def _on_restore(self) -> None:
        if self._root is None:
            return
        self._run(lambda: dllpatch.restore(self._root))

    def _on_verify(self) -> None:
        if self._root is None:
            return
        try:
            ok, message = dllpatch.verify(self._root)
        except UserFacingError as exc:
            self._say_error(str(exc))
            return
        except OSError as exc:
            self._say_error(f"读写游戏目录失败：{exc}")
            return
        if not self._refresh_quietly():
            return
        # 结果（无论通过与否）只写状态区 —— 不弹框。
        self._note.setText(("✅ " if ok else "⚠ ") + message)
