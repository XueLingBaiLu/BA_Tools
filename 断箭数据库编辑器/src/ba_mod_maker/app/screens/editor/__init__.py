"""单位编辑器（双击军械库卡片墙上的一个单位进入）。

- unit_editor_screen —— 第 4 个页（不进导航栏，只能双击进 / 返回出）
- editable_card —— 可写卡（UnitCardPanel 的子类：卡片版式照抄，只是每个数字可点）
- entries_panel —— 表名 / 表记号那两张登记表（table_title / table_emoji）。
  ❗ 中栏不是它：中栏 = widgets/row_entries.py 的 RowEntriesPanel
- inline_editor —— 「点开一格」那个浮层
- pages —— 四个专用页（步兵班 / 武器 / 弹药 / 改装选项）

界线：界面只做展示。三件事（哪些行属于这个单位 / 一行被几个单位共用 /
这一格落在哪）全在 core/ 里，各有纯函数与用例。
"""

from ba_mod_maker.app.screens.editor.editable_card import EditableCardPanel
from ba_mod_maker.app.screens.editor.inline_editor import InlineEditor
from ba_mod_maker.app.screens.editor.unit_editor_screen import UnitEditorScreen

__all__ = ["EditableCardPanel", "InlineEditor", "UnitEditorScreen"]
