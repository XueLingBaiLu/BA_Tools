"""军械库主屏（见 docs/adr/0002-主屏-军械库视图.md）。

结构：筛选带 → 编成头 → 类别行卡片网格 → 详情面板。

数据整形全部在 app/viewmodel/armory.py（零 Qt），
本包只负责**把数据画出来**和**把用户动作传出去**。
"""

from ba_mod_maker.app.screens.armory.armory_screen import ArmoryScreen
from ba_mod_maker.app.screens.armory.category_row import CategoryRow
from ba_mod_maker.app.screens.armory.detail_panel import UnitDetailPanel
from ba_mod_maker.app.screens.armory.unit_card import UnitCard

__all__ = ["ArmoryScreen", "CategoryRow", "UnitCard", "UnitDetailPanel"]
