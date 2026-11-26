"""国家页 / 专精页（界面重做定稿 块 3）。

这两张是**分类表**（Countries 3×7、Specializations 13×24），
按用户口径单独成页、插在「军械库」与「数据表」中间，
不再出现在单位的词条下面（见 core/edit/scope.py::REFERENCE_ONLY_TABLES）。
"""

from ba_mod_maker.app.screens.reference.country_page import CountryPage
from ba_mod_maker.app.screens.reference.spec_page import SpecPage

__all__ = ["CountryPage", "SpecPage"]
