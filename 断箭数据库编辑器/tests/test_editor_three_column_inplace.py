"""三栏那一页（载具 / 飞机落这里）**改一格 = 就地更新** 的守卫（D 块）。

口径与依据：AGENTS §0.7（改一格 = 就地更新）、
`docs/交接-2026-10-01-编成台落地-骨架与步兵.md` §8/§9（上一手对**编成台**做的就是这一套）。

## 为什么是这一页

双击步兵进的是编成台；**载具 / 飞机仍进三栏那一页**（MainEditorPage），
而它是多数单位平时编辑的地方。改一格原来要 **146.6ms**（尺子
`_work/probe_vehicle_page.py`，offscreen 单位 21 Stryker RV）：

| 段 | 改前 | 改后（本轮） | 归谁 |
|---|---|---|---|
| `page.refresh()` 合计 | **146.6ms** | **12.2ms** | — |
| 其中 `_rebuild_left_only()` | 21.2ms | **0.0ms** | 本文件用例 2 |
| 其中 `tree.refresh()` | 27.2ms | **3.5ms** | 用例 3 / 4 |
| 其中 `entries.refresh_values()` | 9.3ms | **0.3ms** | 用例 5 |
| 其中 `card.refresh_from_changes()` | 52.4ms | 8.5ms | 队友 B 的卡（用例 1 一起守） |

## 判据是**两条一起**（AGENTS §0.7）

控件**零重建** **且** 值**必须是新的** —— "保住了控件但值还是旧的"同样是没做到。
所以每条用例都是成对断言：`_ids()` 一个控件都不许换，**并且**那一格的字确实变了。

## 为什么不写"耗时 < N ms"

ms 断言在并行（`-n auto`）的机器上必假红。这里守的是**机理**：
控件集合是不是原封不动（`_ids()` 精确相等）、左栏有没有被摘过一次
（事件过滤器数 ParentChange）、中栏写了几行（数 `set_dirty`）。
机理守住了，ms 自然回去；机理破了，ms 断言也拦不住是**哪一处**破的。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from ba_mod_maker.app.screens.editor.ammo_block import AmmoBlock
from ba_mod_maker.app.widgets.row_entries import EntryRow
from ba_mod_maker.core.data import find_dataset_root
from ba_mod_maker.core.edit.refs import CellRef
from ba_mod_maker.core.edit.scope import RowRef
from conftest import settle

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 载具（Stryker RV）—— 双击它落的就是三栏那一页（编成台只做步兵形态）。
STRYKER = 21
#: 这台车挂着的那门枪（Weapons#18 = M2 Browning，挂了一条携弹行 ×200 —— 实测）——
#: 中栏换成它时，下面会多一块「💣 弹药」。
WEAPON_WITH_AMMO = 18


def _open(window):
    """进编辑器，返回 (编辑器, 主编辑页)。三栏那一页就是 `main_page`。"""
    window._open_unit_editor(STRYKER)
    window._unit_editor.show_main()
    page = window._unit_editor.main_page
    # ❗ 先把"进场那一下"排下的销毁跑干净再交出去：`_ids()` 的基准里只要混进
    #    一个**待销毁**的控件，"改前/改后"就会读成"控件被重建了"（实测踩过）。
    _settle_all()
    page.refresh()
    _settle_all()
    return window._unit_editor, page


def _ids(root: QWidget) -> set[int]:
    """这棵控件子树的成员（含自己）—— 判"有没有被销毁重建"用 id 集合。"""
    return {id(widget) for widget in root.findChildren(QWidget)} | {id(root)}


def _shown(tree) -> list[tuple[str, str, str]]:
    """树上**实际画着**的东西（先序）：(key, 名字列, #Id 列)。"""
    from ba_mod_maker.app.screens.editor.relation_tree_panel import _KEY_ROLE

    out: list[tuple[str, str, str]] = []

    def walk(item) -> None:
        out.append((item.data(0, _KEY_ROLE), item.text(0), item.text(1)))
        for index in range(item.childCount()):
            walk(item.child(index))

    for index in range(tree.widget.topLevelItemCount()):
        walk(tree.widget.topLevelItem(index))
    return out


def _model(tree) -> list[tuple[str, str, str]]:
    """模型说**应该**画着什么 —— 与 _shown 同一份口径（_name_text / id_short）。"""
    from ba_mod_maker.app.screens.editor.relation_tree_panel import _name_text

    return [(node.key, _name_text(node), node.id_short) for node in tree.tree.flatten()]


def _settle_all() -> None:
    """把"排到下一轮"的刷新与 detach() 排下的 deleteLater 都跑掉。

    ❗ `processEvents()` **不处理 DeferredDelete**（见 `tests/conftest.py` 的
    `_flush_deferred_deletes`）：不冲这一下，被换掉的控件还挂在树上，
    `_ids()` 会把"旧的没走 + 新的来了"读成"控件一个没少"。
    """
    settle()
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle()


def _hud_multiplier(editor, row_id: int) -> int:
    """那一行弹药的 HUDMultiplier —— 树上那个 **×N = Quantity × max(1, 它)**。

    口径只有一份（`viewmodel/relation_tree.py`：`hud = quantity * max(1, multiplier)`，
    note 写 `×{hud}`）。这里按同一份口径**独立算一遍**期望值，
    免得"显示变了没有"变成"拿模型跟自己比"。
    """
    context = editor.context
    ammo_id = int(context.cell(CellRef("WeaponAmmunitions", row_id, "AmmunitionId")).raw)
    ammo_rows = context.data.t("Ammunitions")
    template = ammo_rows.by_id(ammo_id)
    if template is None:
        return 1
    return max(1, int(ammo_rows.i(template, "HUDMultiplier")))


def _edit_and_refresh(page, ref: CellRef, new_value) -> None:
    """改一格（只进缓存）-> 走界面真正走的那一步 `page.refresh()`。"""
    page.context.set_value(ref, new_value)
    page.refresh()
    _settle_all()


# --------------------------------------------------------------------------- #
# 1. ⭐ 主判据：改一格，三块控件一个不换，而值必须是新的
# --------------------------------------------------------------------------- #


def test_one_edit_rebuilds_nothing_and_still_shows_the_new_value(window) -> None:
    """⭐ AGENTS §0.7 的两条一起：零重建 **且** 值真的变了。

    量法：改 `Units#21.Cost`（三栏那一页中栏第一格）前后各拍一次 id 集合 ——
    左树 / 中栏 / 右卡三块**逐块精确相等**；同时中栏那一格的文字与缓存都必须是新值。
    """
    editor, page = _open(window)
    tree, entries, card = page.tree, page.entries, page.card
    ref = CellRef("Units", STRYKER, "Cost")

    row = entries.row_for(ref)
    assert row is not None, "中栏第一格就是 Units 这一行的 Cost"
    before_tree, before_mid, before_card = _ids(tree), _ids(entries), _ids(card)
    before_shown = row.value_text
    before_raw = int(editor.context.cell(ref).raw)

    _edit_and_refresh(page, ref, before_raw + 5)

    assert _ids(tree) == before_tree, "左树被重建了（改一格不该动它）"
    assert _ids(entries) == before_mid, "中栏被重建了"
    assert _ids(card) == before_card, "右卡被重建了"
    after = entries.row_for(ref)
    assert after is row, "那一行必须还是原来那个控件"
    assert after.value_text != before_shown, "控件保住了、值还是旧的 —— 这不算做到"
    assert editor.context.changeset.value("Units", STRYKER, "Cost") == before_raw + 5
    assert str(before_raw + 5) == after.value_text


# --------------------------------------------------------------------------- #
# 2. 左栏：build_left() 返回的还是原来那个控件 -> 不摘不挂
# --------------------------------------------------------------------------- #


def test_the_left_column_is_never_detached(window) -> None:
    """左栏是那棵树，且**跨刷新是同一个对象** —— 一个字节的 hide/setParent 都不许有。

    量法：在树上装事件过滤器数 `ParentChange` / `Hide`。老写法每次都
    `hide() + setParent(None)` 再挂回去（实测 21.2ms，而且"把一整棵可见的树摘下来
    再挂回去"本身就是用户看到的那一次闪）。反证：把 `page._left` 清掉逼出老路径时，
    同一个过滤器立刻收到 Hide + ParentChange×2 + Show。
    """
    _editor, page = _open(window)
    tree = page.tree
    assert page._left is tree, "三栏这一页的左栏就是那棵关系树"
    assert tree.isVisibleTo(page), "前提：它现在是画着的"

    class _Watch(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[str] = []

        def eventFilter(self, obj, event) -> bool:
            if event.type() in (QEvent.Type.Hide, QEvent.Type.ParentChange):
                self.seen.append(str(event.type()).split(".")[-1])
            return False

    watch = _Watch()
    tree.installEventFilter(watch)
    try:
        page.refresh()
        _settle_all()
    finally:
        tree.removeEventFilter(watch)

    assert watch.seen == [], f"左栏被摘过一次（{watch.seen}）—— 改一格不该动它"
    assert page._left is tree
    assert page._left_layout.indexOf(tree) >= 0
    assert tree.isVisibleTo(page), "跳过重挂之后它必须还是画着的"


# --------------------------------------------------------------------------- #
# 3. 左树：结构署名没变 -> 只改字（值必须是新的，行尾按钮还留着）
# --------------------------------------------------------------------------- #


def test_the_tree_rewrites_the_value_in_place(window) -> None:
    """⭐ 树上那一行**就地改字**：控件一个不换，而 ×N 必须变成新的。

    挑的是弹药行（`WeaponAmmunitions.Quantity`）—— 它是"树上真的画着这个值"的那一格：
    改完弹药数量，那一行要变成 `12.7x99mm · ×N+3`，武器行尾的"共 … 发"也要跟着走。
    """
    editor, page = _open(window)
    tree = page.tree
    node = next(
        item for item in tree.tree.flatten() if item.table == "WeaponAmmunitions" and item.has_row
    )
    item = tree.item(node.key)
    assert item is not None
    before_shape = tree._shape(tree.tree)
    before_ids = _ids(tree)
    before_text = item.text(0)
    tools_before = tree.tool_buttons(node.key)

    ref = CellRef("WeaponAmmunitions", node.row_id, "Quantity")
    raw = int(editor.context.cell(ref).raw)
    expected = (raw + 3) * _hud_multiplier(editor, node.row_id)
    _edit_and_refresh(page, ref, raw + 3)

    assert _ids(tree) == before_ids, "结构没变却重建了控件"
    assert tree._shape(tree.tree) == before_shape, "署名不该变"
    assert tree.item(node.key) is item, "同一行必须还是原来那一项"
    after_text = item.text(0)
    assert after_text != before_text, "控件保住了、值还是旧的 —— 这不算做到"
    fresh = next(item for item in tree.tree.flatten() if item.key == node.key)
    assert fresh.note == f"×{expected}", "模型没跟上新数据"
    assert f"×{expected}" in after_text, f"{after_text!r} 里应当写着 ×{expected}"
    assert tree.tool_buttons(node.key) == tools_before, "行尾那几颗按钮不许被换掉"
    # 最强的那条：树上画着的**每一项**都必须与模型逐项一致（就地改字漏一项就红）
    assert _shown(tree) == _model(tree)


# --------------------------------------------------------------------------- #
# 4. 结构真变了 -> 才整套重建（而且下一次刷新要回到"就地"那条路）
# --------------------------------------------------------------------------- #


def test_a_structural_change_still_rebuilds_the_tree(window) -> None:
    """署名变了才重建：删掉本单位的一行关联，树要跟着少东西。

    ❗ 顺带守一个**记账**：重建那一步必须把新的署名写回去
    （`self._shape_sig = self._shape(tree)`）—— 忘了它，下一次刷新就会永远重建
    （看着"能用"，实际每一格都在全量重画）。
    """
    editor, page = _open(window)
    tree = page.tree
    node = next(item for item in tree.tree.flatten() if item.remove_refs)
    before_shape = tree._shape(tree.tree)
    before_keys = tree.visible_keys()

    editor.context.remove_rows(node.remove_refs)
    page.refresh()
    _settle_all()

    assert tree._shape(tree.tree) != before_shape, "删了一行关联，署名应当变"
    assert tree.visible_keys() != before_keys, "树上应当少掉/换掉东西"
    # 模型与项必须一致（重建之后 key -> item 的映射没有残留）
    assert set(tree._items) == {item.key for item in tree.tree.flatten()}
    assert _shown(tree) == _model(tree), "画出来的东西必须与模型逐项一致"

    # 记账对不对：紧接着那次"什么都没改"的刷新必须回到就地那条路（控件一个不换）
    settled_ids = _ids(tree)
    page.refresh()
    _settle_all()
    assert _ids(tree) == settled_ids, "重建之后应当又走「只改字」那条路"


# --------------------------------------------------------------------------- #
# 5. 中栏：只有真的变了的那一行才写控件
# --------------------------------------------------------------------------- #


def test_the_middle_column_only_rewrites_the_row_that_changed(window, monkeypatch) -> None:
    """⭐ 改一格只碰那一格：数 `EntryRow.set_dirty`（每次真写会调它一次）。

    三栏那一页中栏有 31 行，老写法每次刷新都把 31 行全写一遍（实测 8.7ms，大头是
    每行 4 次 unpolish/polish）。判据两条一起：**没变 -> 一行都不写**；
    **改一格 -> 恰好写那一行**（而且那一行的字必须真的变了）。
    """
    editor, page = _open(window)
    entries = page.entries
    calls: list[EntryRow] = []
    original = EntryRow.set_dirty

    def spy(self, dirty: bool) -> None:
        calls.append(self)
        original(self, dirty)

    monkeypatch.setattr(EntryRow, "set_dirty", spy)

    def lookup(ref: CellRef):
        return editor.context.cell(ref)

    def dirty(ref: CellRef) -> bool:
        return editor.context.is_dirty(ref)

    page.refresh()
    _settle_all()
    calls.clear()

    entries.refresh_values(lookup, dirty)  # 数据没变
    assert calls == [], "一行都没变，却写了控件"

    ref = CellRef("Units", STRYKER, "Cost")
    row = entries.row_for(ref)
    assert row is not None
    before_shown = row.value_text
    editor.context.set_value(ref, int(editor.context.cell(ref).raw) + 1)
    entries.refresh_values(lookup, dirty)
    assert [item.cell.ref for item in calls] == [ref], "只该写变了的那一行"
    assert row.value_text != before_shown, "写是写了，但值还是旧的"


# --------------------------------------------------------------------------- #
# 6. 中栏下面那块「💣 弹药」：改携弹量时行不许重建
# --------------------------------------------------------------------------- #


def test_the_ammo_block_rewrites_the_number_in_place(window) -> None:
    """⭐ 改一格携弹量：那一行**还是原来那个控件**，而 ×N 必须变成新数。

    老写法 `AmmoBlock.reload()` 无条件把每一行 detach + 重建 —— 实测 **14 个控件
    只保住 6 个**、单这一块 3.6ms（尺子同一套：offscreen 单位 21 聚焦 Weapons#18）。
    判据两条一起（AGENTS §0.7）：行对象没换 **且** 数字是新的。
    """
    editor, page = _open(window)
    page.set_focus(RowRef("Weapons", WEAPON_WITH_AMMO))
    page.refresh()
    _settle_all()
    block = next(item for item in page._blocks if isinstance(item, AmmoBlock))
    line = block.lines[0]
    before_ids = _ids(block)
    before_text = line.quantity_text

    ref = CellRef("WeaponAmmunitions", line.row_id, "Quantity")
    raw = int(editor.context.cell(ref).raw)
    _edit_and_refresh(page, ref, raw + 2)

    after_block = next(item for item in page._blocks if isinstance(item, AmmoBlock))
    assert after_block is block, "整块被换掉了"
    assert _ids(block) == before_ids, "弹药块的行被重建了（改一格不该动它们）"
    assert block.lines[0] is line, "那一行必须还是原来那个控件"
    assert line.quantity_text == f"×{raw + 2}", "控件保住了、值还是旧的 —— 这不算做到"
    assert line.quantity_text != before_text
    assert editor.context.changeset.value("WeaponAmmunitions", line.row_id, "Quantity") == raw + 2
