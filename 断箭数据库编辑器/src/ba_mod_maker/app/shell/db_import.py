"""把 `DataBaseCompiled.json` / `BA_Mod.zip` **导入** `data.unity3d` 的界面。

为什么要有它：其余三个动作（打开 / 保存 / 提取）都是「一个文件进出」，
而"导入"天然需要**两个**文件 —— 来源与目标。所以它不能塞进「打开文件…」，
得单独一个对话框。

三条设计口径（与补丁对话框一致）：

1. **先预演再动手**。导入要理解成"把这 24 张表覆盖到容器里"，
   所以最有用的信息是"会改动哪几张表"。这一步几十秒内出结果，可以让用户先看清楚。
   预演与真正导入走 **同一套** `session.load_incoming / merge_into_template`，
   不是各写一套 —— 那种分叉是最难查的 bug。
2. **代价写在按钮旁边**。容器写回是 7.1 GB / 约 4 分钟，且必须关游戏。
3. **三道闸保持默认全开**，但给用户一个关掉的开关（高级用），
   关掉时明说风险。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ba_mod_maker.app.shell import tasks
from ba_mod_maker.app.theme.fonts import data_font, ui_font
from ba_mod_maker.app.theme.tokens import TOKENS
from ba_mod_maker.core import session
from ba_mod_maker.core.assets import game as game_mod
from ba_mod_maker.core.errors import UserFacingError


class DbImportDialog(QDialog):
    """选来源 + 选目标 + 预演 + 导入。"""

    def __init__(self, parent=None, *, container: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入到 data.unity3d")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(620)

        root = game_mod.find_game_root_for_db()
        self._container = container or (game_mod.data_file(root) if root else None)
        self._incoming: Path | None = None
        #: 是否已经出过一次预演结果。有它才不会把预演结论被状态刷新覆盖掉。
        self._previewed = False
        #: 导入正在进行（7.1 GB / 约 4 分钟）。期间不许关窗，见 closeEvent。
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        title = QLabel("把外部数据写进游戏容器")
        title.setFont(ui_font(TOKENS.typography.size_lg, bold=True))
        layout.addWidget(title)

        why = QLabel(
            "来源可以是 DataBaseCompiled.json 或 BA_Mod.zip；目标是 data.unity3d。"
            "容器里那 24 张表会被来源整体覆盖。"
        )
        why.setWordWrap(True)
        why.setFont(ui_font(TOKENS.typography.size_sm))
        layout.addWidget(why)

        layout.addWidget(self._row_label("目标容器"))
        self._container_edit = QLineEdit(str(self._container or ""))
        self._container_edit.setFont(data_font(TOKENS.typography.size_sm))
        self._container_edit.setReadOnly(True)
        browse_target = QPushButton("选择…")
        browse_target.setCursor(Qt.PointingHandCursor)
        browse_target.clicked.connect(self._pick_container)
        row1 = QHBoxLayout()
        row1.addWidget(self._container_edit, 1)
        row1.addWidget(browse_target)
        layout.addLayout(row1)

        layout.addWidget(self._row_label("来源文件"))
        self._incoming_edit = QLineEdit()
        self._incoming_edit.setFont(data_font(TOKENS.typography.size_sm))
        self._incoming_edit.setPlaceholderText("还没有选（BA_Mod.zip 或 DataBaseCompiled.json）")
        self._incoming_edit.setReadOnly(True)
        browse_source = QPushButton("选择…")
        browse_source.setCursor(Qt.PointingHandCursor)
        browse_source.clicked.connect(self._pick_incoming)
        row2 = QHBoxLayout()
        row2.addWidget(self._incoming_edit, 1)
        row2.addWidget(browse_source)
        layout.addLayout(row2)

        self._preview = QLabel("选好来源后点「预演」，先看清楚会改哪几张表。")
        self._preview.setWordWrap(True)
        self._preview.setFont(ui_font(TOKENS.typography.size_sm))
        layout.addWidget(self._preview)

        # 代价警告**内嵌**在这里，而不是点「导入」之后再弹一次确认框。
        # 理由：用户点按钮之前就该看到代价；再弹一次只是多一次点击。
        self._warn = QLabel()
        self._warn.setWordWrap(True)
        self._warn.setFont(ui_font(TOKENS.typography.size_sm))
        self._warn.setVisible(False)
        layout.addWidget(self._warn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._gates = QCheckBox("保持三道闸（写前核对 sha256 / 检测游戏进程 / 写后复核）")
        self._gates.setChecked(True)
        self._gates.setFont(ui_font(TOKENS.typography.size_sm))
        layout.addWidget(self._gates)

        buttons = QHBoxLayout()
        self._preview_btn = QPushButton("预演")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._import_btn = QPushButton("导入")
        self._import_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.clicked.connect(self._on_preview)
        self._import_btn.clicked.connect(self._on_import)
        self._preview_btn.setEnabled(False)
        self._import_btn.setEnabled(False)
        buttons.addWidget(self._preview_btn)
        buttons.addWidget(self._import_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._refresh_enabled()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(TOKENS.typography.size_sm, bold=True))
        return label

    def _refresh_enabled(self) -> None:
        """刷新按钮可用性，并在**缺目标**时把话说清楚。"""
        has_target = bool(self._container and Path(self._container).is_file())
        ready = has_target and self._incoming is not None
        self._preview_btn.setEnabled(ready)
        self._import_btn.setEnabled(ready)
        if not has_target:
            # ⚠️ 必须写进提示里，不能只体现在按钮灰掉上 ——
            # 否则"两个按钮都是灰的"会被当成程序坏了，而不是"你还没选目标"。
            self._preview.setText(
                "找不到目标容器。请用「选择…」指定 <游戏根>/BrokenArrow_Data/data.unity3d。"
            )
        elif self._incoming is None and not self._previewed:
            self._preview.setText("选好来源后点「预演」，先看清楚会改哪几张表。")

    def _pick_container(self) -> None:
        start = str(self._container.parent) if self._container else str(Path.cwd())
        chosen, _f = QFileDialog.getOpenFileName(
            self, "选择目标容器 data.unity3d", start, "Unity 容器 (*.unity3d);;全部文件 (*)"
        )
        if chosen:
            self._container = Path(chosen)
            self._container_edit.setText(chosen)
            self._refresh_enabled()

    def _pick_incoming(self) -> None:
        start = str(self._incoming.parent) if self._incoming else str(Path.cwd())
        chosen, _f = QFileDialog.getOpenFileName(
            self,
            "选择来源文件",
            start,
            "可导入的文件 (*.zip *.json);;外部库 BA_Mod.zip (*.zip);;"
            "DataBaseCompiled (*.json);;全部文件 (*)",
        )
        if chosen:
            self._incoming = Path(chosen)
            self._incoming_edit.setText(chosen)
            self._previewed = False
            self._refresh_enabled()

    def _on_preview(self) -> None:
        if self._container is None or self._incoming is None:
            return
        try:
            info = session.preview_import(self._container, self._incoming)
        except UserFacingError as exc:
            self._preview.setText(f"预演失败：{exc}")
            return
        changed = info["changed"]
        if not changed:
            self._preview.setText(
                "预演：**没有任何改动** —— 这份来源与容器里的数据一致，导入会是空操作。"
            )
            self._import_btn.setEnabled(False)
            return
        shown = "、".join(changed[:10]) + ("…" if len(changed) > 10 else "")
        self._preview.setText(
            f"预演：会改动 {len(changed)} 张表 —— {shown}\n"
            f"目标 pathID {info['path_id']}，共 {info['tables']} 张表。"
        )
        self._warn.setText(
            "⚠️ 会重写整个 data.unity3d（7.1 GB，约 4 分钟）。"
            "期间不要开游戏，也不要动那个文件。"
            + ("" if self._gates.isChecked() else "\n⚠️ 三道闸已关闭：写坏了没有任何保护。")
        )
        self._warn.setVisible(True)
        self._import_btn.setEnabled(True)

    def _on_import(self) -> None:
        if self._container is None or self._incoming is None:
            return
        gates = self._gates.isChecked()
        # 目标与来源先取进局部变量：工作线程只认这两个值，不再回头读控件属性。
        container = self._container
        incoming = self._incoming
        # 不再二次确认：用户已经看过预演、也看到了内嵌的代价警告，
        # 点「导入」本身就是确认动作。
        self._running = True
        self._progress.setVisible(True)
        self._import_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self.setEnabled(False)
        self.repaint()

        def _work() -> object:
            return session.import_into_container(
                container,
                incoming,
                check_game_running=gates,
                check_sha256=gates,
                verify_after=gates,
            )

        def _done(result: object) -> None:
            self._running = False
            self.setEnabled(True)
            self._progress.setVisible(False)
            self._warn.setVisible(False)
            # 结果写在对话框里，不再弹"完成"框 —— 那个框除了要你点一下没别的作用。
            self._preview.setText(f"✅ {result}")

        def _failed(exc: object) -> None:
            self._running = False
            self.setEnabled(True)
            self._progress.setVisible(False)
            self._refresh_enabled()
            if not isinstance(exc, UserFacingError):
                # 内部缺陷不当成"用户操作失败"粉饰掉：原样抛出去（与从前同步调用时一致）。
                raise exc
            QMessageBox.warning(self, "没有导入成功", str(exc))

        # ⚠️ 这 4 分钟必须离开主线程：跑在主线程上，事件循环被占住 ——
        #    窗口会被 Windows 盖上「程序未响应」，上面那个进度条也不会动。
        #    详见 app/shell/tasks.py。
        tasks.run_in_thread(self, _work, on_success=_done, on_error=_failed)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """导入期间不许关窗。

        工作线程还握着这个对话框（回调要写它的标签），关了就是"线程还在写、界面没了"。
        宁可让那个 X 暂时按不动，也不要制造一个偶发崩溃。
        """
        if self._running:
            event.ignore()
            return
        super().closeEvent(event)


def open_import_dialog(parent=None, *, container: Path | None = None) -> None:
    """从菜单调用它。找不到容器时也让对话框开起来（它自己会说清楚）。"""
    DbImportDialog(parent, container=container).exec()
