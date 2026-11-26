"""整屏级容器。

与 app/widgets 的区别：widgets 里是**可单独嵌入**的通用控件；
这里是把控件和数据拼成"一整屏"的地方，会读 instance、会发高层信号。

| 屏 | 职责 |
|---|---|
| armory.ArmoryScreen | 军械库主屏（ADR 0002） |
| editor.UnitEditorScreen | 单位编辑器（双击一张卡进） |
| reference.CountryPage / SpecPage | 国家页 / 专精页（界面重做定稿 块 3） |
"""

from ba_mod_maker.app.screens.armory import ArmoryScreen
from ba_mod_maker.app.screens.reference import CountryPage, SpecPage

__all__ = ["ArmoryScreen", "CountryPage", "SpecPage"]
