"""整单位克隆的**界面入口**（顶栏「⧉ 克隆整个单位」）。

核心语义在 tests/test_clone_unit.py（零 Qt，6 条）；这一份只钉界面那一半：
按钮什么时候出现 / 不在编辑器里不出现 / 点一下真的插了行 / **一次 Ctrl+Z 全退回去**。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

BTR82A = 1


def test_the_clone_button_clones_the_open_unit_in_one_undo(window) -> None:
    """按钮只在编辑器里出现；点一下 = 整套克隆；Ctrl+Z **一次**退回原样。"""
    button = window._clone_unit_button
    assert button.isHidden(), "还没进编辑器，这颗按钮不该露出来"

    window._open_unit_editor(BTR82A)
    screen = window._unit_editor
    context = screen.main_page.context
    assert context is not None
    assert not button.isHidden()

    units = context.instance.table("Units")
    before = len(units.rows)
    allowed_before = {row["UnitId"] for row in _dicts(context.instance, "Modifications")}

    button.click()

    assert len(units.rows) == before + 1, "点了一下没插新单位行"
    assert "已克隆为 Units#" in window._status_label.text()
    cloned_ids = {row["UnitId"] for row in _dicts(context.instance, "Modifications")}
    new_unit_ids = cloned_ids - allowed_before
    assert len(new_unit_ids) == 1, "克隆出来的改装槽没指到新单位"

    message = context.undo_last()
    assert "撤销" in message
    assert len(units.rows) == before, "一次 Ctrl+Z 没把新单位行退回去"
    assert {row["UnitId"] for row in _dicts(context.instance, "Modifications")} == allowed_before


def test_cloning_jumps_straight_to_the_new_unit(window) -> None:
    """⭐ 用户 2026-10-02：「**我克隆单位的时候，希望直接跳转到那个单位**」。

    从前是"不切过去"，让人按 F3 输一串号（而且那串号本身还是个 bug：把整句里的数字全拼起来，
    实测写成了 707571121111112312）。判据两层：① 克隆完编辑器停在**新单位**上；
    ② 撤销栈没被换单位清掉 —— 同一笔 Ctrl+Z 照样把新单位整组退回去。
    """
    window._open_unit_editor(BTR82A)
    screen = window._unit_editor
    units = screen.main_page.context.instance.table("Units")
    before = len(units.rows)

    assert window._clone_unit_button.isHidden() is False
    window._clone_unit_button.click()

    new_context = screen.main_page.context
    assert new_context.unit_id != BTR82A, "没跳过去（还停在原单位）"
    assert len(units.rows) == before + 1
    assert "已直接跳到新单位" in window._status_label.text(), window._status_label.text()
    assert f"Units#{new_context.unit_id}" in window._status_label.text()

    # 换单位不许把撤销栈丢掉（变更集归 EditSession 所有）
    assert new_context.undo_depth > 0, "换单位把撤销栈丢了（跳过去之后 Ctrl+Z 就没用了）"
    assert "撤销" in new_context.undo_last()
    assert len(units.rows) == before, "跳过去之后 Ctrl+Z 退不回来了"


def _dicts(instance, table: str) -> list[dict]:
    target = instance.table(table)
    return [dict(zip(target.columns, row, strict=False)) for row in target.rows]
