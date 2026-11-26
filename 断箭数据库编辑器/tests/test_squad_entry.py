"""设计稿 §7 的 d：老 SquadPage 的入口（顶栏「👥 步兵班」）。

`UnitEditorScreen.open_squad_page()` 原来**全仓零调用**（只有 test_editor_squad_page.py 直接调它）。
这一份钉那颗按钮的两条判据：非步兵整颗藏起来、步兵点了真进 SquadPage 那一页。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 步兵样本：VDV Shturm（5 名成员）；载具样本：BTR-82A（没有班成员行）。
INFANTRY = 645
VEHICLE = 1


def test_the_squad_entry_is_only_for_infantry_and_really_opens(window) -> None:
    """非步兵不露；步兵点了进 SquadPage。"""
    button = window._squad_page_button
    assert button.isHidden(), "没进编辑器时不该露出来"

    window._open_unit_editor(VEHICLE)
    assert button.isHidden(), "非步兵不该有「步兵班」入口"

    window._open_unit_editor(INFANTRY)
    assert not button.isHidden(), "步兵该看得到入口"

    button.click()
    page = window._unit_editor._stack.currentWidget()
    assert type(page).__name__ == "SquadPage", type(page).__name__
