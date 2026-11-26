"""四个专用页（步兵班 / 武器 / 弹药 / 改装选项）。

契约在 base.py：同一套版式（左「这一行的上下文」+ 右「全部词条」），
写数据走同一个 EditorContext（含共用的撤销 / 重做栈）；落盘只有一条通道
（app/shell/edit_session.py，顶栏那颗「💾 保存」）。
"""

from ba_mod_maker.app.screens.editor.pages.ammo_page import AmmoPage
from ba_mod_maker.app.screens.editor.pages.base import (
    EditorContext,
    EditorPage,
    PageSpec,
    UndoEntry,
)
from ba_mod_maker.app.screens.editor.pages.options_page import OptionsPage
from ba_mod_maker.app.screens.editor.pages.squad_page import SquadPage
from ba_mod_maker.app.screens.editor.pages.weapon_page import WeaponPage

__all__ = [
    "AmmoPage",
    "EditorContext",
    "EditorPage",
    "OptionsPage",
    "PageSpec",
    "SquadPage",
    "UndoEntry",
    "WeaponPage",
]
