"""编辑器代理：让每种列用对的控件编辑，并在输入不合法时**拒绝关闭编辑框**。

为什么不能只靠 QLineEdit：

- 是非列用文本框编辑，用户得知道能填什么（该给下拉框）；
- 如果用户敲了 `abc` 到一个 int 列，默默失败是最差的结果——
  他以为改成了。本代理会弹出原因并把光标留在编辑框里。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QMessageBox,
    QStyledItemDelegate,
    QWidget,
)

from ba_mod_maker.app.widgets.table_model import TableModel
from ba_mod_maker.core.data import ColumnKind
from ba_mod_maker.core.errors import BaModMakerError

#: 下拉框里的两个选项（显示文本 -> 存盘文本）。
_BOOL_CHOICES: tuple[tuple[str, str], ...] = (("是", "true"), ("否", "false"))


class CellDelegate(QStyledItemDelegate):
    """按列类型选编辑器的代理。

    属性
    ----
    reject_message
        最近一次拒绝的原因（给外层在状态栏里显示）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.reject_message: str | None = None

    # ------------------------------------------------------------------ #

    def _model(self) -> TableModel | None:
        model = self.parent().model() if self.parent() is not None else None
        return model if isinstance(model, TableModel) else None

    def createEditor(  # noqa: N802 — Qt 命名
        self, parent: QWidget, option: Any, index: QModelIndex
    ) -> QWidget:
        model = self._model()
        spec = model.column_spec(index.column()) if model else None

        if spec is not None and spec.kind == ColumnKind.BOOL:
            editor = QComboBox(parent)
            for label, stored in _BOOL_CHOICES:
                editor.addItem(label, stored)
            return editor

        editor = QLineEdit(parent)
        if spec is not None:
            editor.setPlaceholderText(spec.placeholder)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # noqa: N802 — Qt 命名
        """把当前值填进编辑器。

        关键：这里用的是 `edit_text`（**未截断、可原样解析回去**），
        而不是表格里看到的那个截断版本。否则一编辑就把长值截没了。
        """
        model = self._model()
        if model is None or model.table is None:
            return

        source_row = model.source_row(index.row())
        text = model.edit_text(source_row, index.column())
        spec = model.column_spec(index.column())

        if isinstance(editor, QComboBox):
            stored = "true" if text.strip().lower() in ("true", "1", "yes") else "false"
            position = editor.findData(stored)
            editor.setCurrentIndex(max(position, 0))
            return

        if isinstance(editor, QLineEdit):
            editor.setText(text)
            editor.selectAll()
            if spec is not None:
                editor.setToolTip("" if not spec.placeholder else spec.placeholder)

    def setModelData(  # noqa: N802 — Qt 命名
        self, editor: QWidget, model: TableModel, index: QModelIndex
    ) -> None:
        """提交编辑。

        转换失败时**不提交、不关编辑框**，并告诉用户为什么。
        这是整个编辑链路里最重要的一步：默默失败比报错更糟。
        """
        if isinstance(editor, QComboBox):
            value = editor.currentData()
        elif isinstance(editor, QLineEdit):
            value = editor.text()
        else:
            return

        self.reject_message = None
        try:
            model.setData(index, value, Qt.ItemDataRole.EditRole)
        except BaModMakerError as exc:
            self.reject_message = str(exc)
            self._warn(editor, str(exc), exc.hint)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _warn(editor: QWidget, message: str, hint: str | None) -> None:
        box = QMessageBox(editor)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("这个值没接受")
        box.setText(message)
        if hint:
            box.setInformativeText(hint)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
