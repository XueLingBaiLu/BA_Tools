"""美术列（缩略图 / 图标 / 立绘 / 改装图片）改一格 = **可编辑下拉 + 打字即筛**。

用户口径（2026-10-01）：「缩略图我也要能直接输入然后搜索，而不是我去找然后复制名字，
那个名字太长了，很难记」。

两半都钉：

1. **core**（`core/edit/foreign.py::resource_key_candidates`）：候选 = 这一列在这张表里用过的键，
   排序与检索页同源（完全相等 > 前缀 > 含），同形字也认；
2. **界面**（`inline_editor.InlineEditor`）：美术列那一格打开的是**可编辑下拉**（打字筛），
   不是纯文本框；而**别的列仍然是纯文本框**（别把全世界都变成下拉）。

❗ 还有一条不能破的老行为：打字 / 粘贴一个候选里没有的键**照旧算数** —— 换一张从没被用过
的图是正当需求（改动前那个纯文本框就是这个行为）。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.core.data import find_dataset_root, load_dataset
from ba_mod_maker.core.edit.foreign import resource_key_candidates

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

INSTANCE_ID = "52085"
#: Stryker RV：缩略图 `US_STRYKER_RV-Label`（就是用户说「太长、很难记」的那种名字）。
STRYKER = 21
THUMBNAIL = "ThumbnailFileName"
#: 用户截图那条路：单位 115 VDV Snaipery → 改装选项 → Options #1500
#: （库里的值 `RU_VDV_SNIPER_T5000-Label`；截图里那串 `2A72` 是**打进去的搜索词**）。
VDV_SNIPER = 115
THUMBNAIL_OPTION = 1500
#: 打 `2A72` 之后这一列应只剩这 4 条（实测：库里 196 个键里含 2A72 的就这 4 个）。
FILTER_EXPECTED = (
    "DLC3_BPM97_2A72-Label",
    "RU_BMP1_2A72-Label",
    "RU_MTLB_2A72-Label",
    "RU_PT76_2A72-Label",
)


@pytest.fixture
def instance():
    return load_dataset(DATASET_ROOT).instance(INSTANCE_ID)


def test_the_criterion_is_the_dictionary_control(instance) -> None:
    """哪一列该给这个下拉 —— 判据是**词典的 control**，界面不许自己再判一次列名。

    `core/edit/dictionary.py`：列的 control 是它定的（资源键 = 列名以 FileName / Icon /
    IconKey / Picture / AudioPreset / PresetName 结尾），那句注释写着「界面按它选控件，
    **不在界面里再判一次类型**」。
    """
    from ba_mod_maker.core.edit.cells import cell_at
    from ba_mod_maker.core.edit.dictionary import CONTROL_RESOURCE
    from ba_mod_maker.core.edit.refs import CellRef

    def control_of(table: str, row_id: int, column: str) -> str:
        cell = cell_at(instance, CellRef(table, row_id, column))
        assert cell is not None, (table, row_id, column)
        return cell.control

    assert control_of("Units", STRYKER, THUMBNAIL) == CONTROL_RESOURCE
    assert control_of("Weapons", 2, "HUDIcon") == CONTROL_RESOURCE
    assert control_of("Units", STRYKER, "PortraitFileName") == CONTROL_RESOURCE
    assert control_of("Units", STRYKER, "Cost") != CONTROL_RESOURCE
    assert control_of("Units", STRYKER, "HUDName") != CONTROL_RESOURCE


def test_candidates_come_from_that_column_and_rank_by_likeness(instance) -> None:
    """候选来自**这一列用过的值**；打字按「相等 > 前缀 > 含」排。"""
    all_keys = resource_key_candidates(instance, "Units", THUMBNAIL)
    assert all_keys, "单位缩略图这一列一个值都没有？"
    assert len(set(all_keys)) == len(all_keys), "候选里有重复"
    # 不筛的时候是**截断的**（上限 400 条、按名字排）—— 样本那个 U 开头的键落不进窗口，
    # 这恰恰是「打字搜」要解决的问题：打几个字就够得着（下面那条 hits 才是它的用法）。
    full = resource_key_candidates(instance, "Units", THUMBNAIL, limit=9999)
    assert "US_STRYKER_RV-Label" in full
    assert "US_STRYKER_RV-Label" not in all_keys, "样本键落进未筛窗口了，换个样本或改断言"

    hits = resource_key_candidates(instance, "Units", THUMBNAIL, needle="stryker")
    assert hits and all("stryker" in key.lower() for key in hits), hits[:5]
    assert hits[0].startswith("US_STRYKER"), hits[0]

    exact = resource_key_candidates(instance, "Units", THUMBNAIL, needle="US_STRYKER_RV-Label")
    assert exact[0] == "US_STRYKER_RV-Label", exact[:3]


def test_candidates_are_scoped_to_the_table_and_column(instance) -> None:
    """按 (表, 列) 取 —— 单位的缩略图与武器的图标是两批素材，不许混。"""
    units = set(resource_key_candidates(instance, "Units", THUMBNAIL))
    weapons = set(resource_key_candidates(instance, "Weapons", "HUDIcon"))
    assert units and weapons
    assert not (units & weapons), "两族的键混在一起了"
    # 没这一列 / 没这张表 -> 空，不炸（映射不该因此把界面搞崩）
    assert resource_key_candidates(instance, "Units", "没有这一列") == ()
    assert resource_key_candidates(instance, "没有这张表", THUMBNAIL) == ()


def test_the_limit_is_respected(instance) -> None:
    assert len(resource_key_candidates(instance, "Units", THUMBNAIL, limit=7)) == 7


def test_the_thumbnail_cell_opens_a_searchable_picker(window) -> None:
    """界面那一半：缩略图那一格是**可编辑下拉**，打字筛，选中即写回。"""
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(STRYKER)
    editor_screen = window._unit_editor
    page = editor_screen.main_page
    cell = page.context.cell(CellRef("Units", STRYKER, THUMBNAIL))
    assert cell.text, "样本单位的缩略图键是空的？换一个样本"

    page._open_inline_editor(cell)
    row = page.entries.open_row
    assert row is not None and row.editor is not None
    combo = row.editor.input_widget
    assert isinstance(combo, QComboBox), "缩略图那一格不是可编辑下拉 —— 又变回纯文本框了"
    assert combo.isEditable()
    items = [combo.itemText(index) for index in range(combo.count())]
    assert items, "下拉里一条候选都没有"
    # 当前值**一定写在框里**（哪怕它落在未筛窗口之外 —— 那是 setEditText 那条兜底）
    assert combo.currentText() == cell.text, (combo.currentText(), cell.text)

    # 打字筛：候选只剩含这几个字的
    combo.setEditText("stryker")
    filtered = [combo.itemText(index) for index in range(combo.count())]
    assert filtered and all("stryker" in key.lower() for key in filtered), filtered[:5]

    # 选一条 -> 写回那一格
    picked = filtered[1] if len(filtered) > 1 else filtered[0]
    combo.setCurrentIndex(combo.findText(picked))
    assert page.context.cell(CellRef("Units", STRYKER, THUMBNAIL)).text == picked


def test_every_key_column_the_sweep_found_gets_the_search_control(instance) -> None:
    """**全产品彻查那一条**：值是这个"又长又难记的键"的列，一格都不许再给纯文本框。

    两半判据（出处 `_work/probe_control_sweep.py`，全库 24 张表逐列扫值形状）：

    1. **图标族登记表**（`core/assets/families.py::FAMILIES[].column`）—— 那几列
       **定义上**就是游戏包里的键（界面拿它取图），必须给搜索控件；
    2. **实测补上的那几列**（`Options.ThumbnailOverride` 等，见 dictionary.py
       的 `_RESOURCE_COLUMNS`）—— 名字不带那些后缀，但值就是键。

    用户报的就是第 2 类里的 `Options.ThumbnailOverride`：
    「缩略图覆盖」那一格只给了个纯文本框 —— 打 `2A72` 什么都没弹，而且那串搜索词
    会被当成值写进缓存（截图里顶栏正是「1 处未保存」）。
    """
    from ba_mod_maker.core.assets.families import FAMILIES
    from ba_mod_maker.core.edit.cells import cell_at
    from ba_mod_maker.core.edit.dictionary import CONTROL_RESOURCE, control_for, field_info
    from ba_mod_maker.core.edit.refs import CellRef

    # ① 图标族登记表：一列都不许漏
    for entry in FAMILIES:
        if not entry.column or "." not in entry.column:
            continue
        table, column = entry.column.split(".", 1)
        assert control_for(field_info(table, column), "text") == CONTROL_RESOURCE, entry.column

    # ② **用户报的那一格**：截图里就是它（单位 115 → Options #1500 → 缩略图覆盖）
    user_cell = cell_at(instance, CellRef("Options", 1500, "ThumbnailOverride"))
    assert user_cell is not None
    assert user_cell.control == CONTROL_RESOURCE, "「缩略图覆盖」那一格又变回纯文本框了"

    # ③ 实测补上的其余几列：走**真实数据那条路**（cell_at -> FieldCell.control），
    #    不只看词典 —— 用户面前那一格就是这个 control（行号现取，别写死）。
    for table, column in (
        ("Specializations", "Illustration"),
        ("Ammunitions", "FXPreset"),
        ("Modifications", "UIName"),
        # ④ 用户 2026-10-02 报的那一格（右栏「选项界面名」）：这一列**不是**判据 2
        #    找出来的（形状 77% < 90% 门槛），是探针新加的**判据 3**（值翻得出来 ≥50%）
        #    扫出来的 —— 全库就这一列漏了。
        ("Options", "UIName"),
    ):
        rows = instance.table(table)
        id_index = rows.column_index("Id")
        row_id = next(row[id_index] for row in rows.rows if row[id_index] is not None)
        cell = cell_at(instance, CellRef(table, row_id, column))
        assert cell is not None, (table, row_id, column)
        assert cell.control == CONTROL_RESOURCE, f"{table}.{column} 又变回纯文本框了"


def test_a_number_column_never_gets_the_resource_control(instance) -> None:
    """名字像键、其实是**数**的列不许给下拉。

    `Options.StealthOverride` 是 float：给它一个"候选下拉"，选一条字符串再写回
    数字列 —— 那是在制造坏数据。判据取数据（column_kind），不是列名。
    """
    from ba_mod_maker.core.edit.cells import cell_at
    from ba_mod_maker.core.edit.dictionary import CONTROL_RESOURCE
    from ba_mod_maker.core.edit.refs import CellRef

    cell = cell_at(instance, CellRef("Options", 1500, "StealthOverride"))
    assert cell is not None
    assert cell.control != CONTROL_RESOURCE, "float 列被判成资源键了"


def test_the_thumbnail_override_cell_opens_the_searchable_picker(window) -> None:
    """用户截图那条路：单位 115 → 改装选项 → Options #1500 → 「缩略图覆盖」那一格。

    修前这里是纯文本框（打 `2A72` 什么都不弹）；修后是可编辑下拉 + 打字即筛。
    """
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(VDV_SNIPER)
    screen = window._unit_editor
    screen.open_options_page(THUMBNAIL_OPTION)
    page = screen.current_page
    cell = page.context.cell(CellRef("Options", THUMBNAIL_OPTION, "ThumbnailOverride"))
    assert cell is not None and cell.text, "样本变了：这一格在库里是空的"

    page._open_inline_editor(cell)
    combo = page.entries.open_row.editor.input_widget
    assert isinstance(combo, QComboBox), "「缩略图覆盖」那一格不是可编辑下拉"
    assert combo.isEditable()
    assert combo.currentText() == cell.text, (combo.currentText(), cell.text)

    # ❗ 用 **QTest 真按键**打，不用 setEditText：用户报过的原话是"打字之后候选
    #    **没弹出来**"，而"打字"这条路与"程序化设值"在 Qt 里不是同一条 —— 只断言
    #    `setEditText` 会漏掉"浮层被后一个事件收掉"那一类。这里连**浮层可见**一起钉。
    from PySide6.QtTest import QTest

    line = combo.lineEdit()
    assert line is not None
    line.selectAll()
    QTest.keyClicks(line, "2A72")
    filtered = [combo.itemText(index) for index in range(combo.count())]
    assert filtered and all("2A72" in key for key in filtered), filtered[:5]
    assert combo.completer().popup().isVisible(), "打完字候选浮层没弹出来"
    assert tuple(filtered) == FILTER_EXPECTED, filtered


#: 用户 2026-10-02 截图那条路：Ka-52（单位 46）→ 编成台 ② 改装槽 → 右栏「选项界面名」。
#: 样本 = 槽 #75 的 `InnerPylon KA52 S8`（Options #208，UIName = `Custom_Option_40xS8`）。
KA_52 = 46
OPTION_S8 = 208
OPTION_UI_KEY = "Custom_Option_40xS8"


def test_the_option_ui_name_cell_opens_the_searchable_picker(window) -> None:
    """⭐ 用户 2026-10-02：「我要画红圈的地方，也有检索功能」。

    红圈那一格 = **编成台右栏的「选项界面名」**（`Options.UIName`）。修前它是**纯文本框**：
    要填就得把 `Custom_Option_40xS8` 这种键一字不差打出来（这一列 2046 行里 1578 行是键）。
    修后与缩略图 / 图标那几格**同一套控件**：可编辑下拉 + 打字即筛。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox

    from ba_mod_maker.app.widgets.bench_rail import MODE_COLUMNS
    from ba_mod_maker.core.edit.dictionary import CONTROL_RESOURCE
    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(KA_52)
    page = window._unit_editor.bench_page()
    ref = CellRef("Options", OPTION_S8, "UIName")
    cell = page.context.cell(ref)
    assert cell is not None and cell.text == OPTION_UI_KEY, f"样本变了：{cell and cell.text!r}"
    assert cell.control == CONTROL_RESOURCE, "「选项界面名」那一格又变回纯文本框了"

    page._on_row_selected("Options", OPTION_S8)  # 右栏画这一条改装选项（点候选行走的就是它）
    rail = page.rail
    rail.set_mode(MODE_COLUMNS)
    assert "选项界面名" in rail.editable_labels(), rail.editable_labels()

    target = rail.value_widget("选项界面名")
    assert target is not None
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.rect().center())
    editor = rail.editor()
    assert editor is not None, "点了那一格没长出编辑控件"
    combo = editor.input_widget
    assert isinstance(combo, QComboBox), "「选项界面名」那一格不是可编辑下拉"
    assert combo.isEditable()
    assert combo.currentText() == OPTION_UI_KEY, combo.currentText()

    # 打字即筛：**真按键**（"打字"与"程序化设值"在 Qt 里不是同一条路）
    line = combo.lineEdit()
    assert line is not None
    line.selectAll()
    QTest.keyClicks(line, "20xS8")  # 真按键；这个词筛出来的是**换一个**键的那一条
    filtered = [combo.itemText(index) for index in range(combo.count())]
    assert filtered and all("20xs8" in key.lower() for key in filtered), filtered[:5]
    target = filtered[0]
    assert target != OPTION_UI_KEY, f"筛出来的第一条还是原值 —— 这条断言会恒真：{filtered[:3]}"
    assert page.context.cell(ref).text == OPTION_UI_KEY, "还没点候选就写了？"

    # 点候选 = 当场收工。`activated` 的语义是"用户**选了**一条"，与索引变没变无关 ——
    # ❗ 不能拿 `setCurrentIndex(findText(第一条))` 冒充这个手势：候选重建时索引已经是 0，
    #    那样等于没换索引、信号不发、**什么都不写**（真机探针量出来的，见交接）。
    completer = combo.completer()
    assert completer is not None
    completer.activated.emit(completer.completionModel().index(0, 0))

    assert page.context.cell(ref).text == target, "点了候选却没写回那一格"
    # 改回样本值：这一格是共享数据，别把这个单位的样本留给别的用例
    page.context.set_value(ref, OPTION_UI_KEY)
    assert page.context.cell(ref).text == OPTION_UI_KEY


def test_a_plain_column_still_opens_a_plain_text_box(window) -> None:
    """别把全世界都变成下拉：非美术列仍是纯文本框。"""
    from PySide6.QtWidgets import QLineEdit

    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(STRYKER)
    page = window._unit_editor.main_page
    cell = page.context.cell(CellRef("Units", STRYKER, "Cost"))
    page._open_inline_editor(cell)
    editor = page.entries.open_row.editor
    assert isinstance(editor.input_widget, QLineEdit), "非美术列不该变成下拉"


def test_typing_a_filter_word_does_not_write_it_into_the_cell(window) -> None:
    """打完**筛选词**直接点走 = 改主意了 —— 不许把那串搜索词写进这一格。

    可编辑下拉特有的坑：旧文本框下用户打错字也就写个错名字，而下拉这边写进去的会是
    **搜索词本身**（`stryker`）—— 那是悄悄把数据写坏。所以收工时只认两种：正好是某
    一项、或谁都对不上的新键；只是某个键的一部分就报错、不写、不关浮层。
    """
    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(STRYKER)
    page = window._unit_editor.main_page
    ref = CellRef("Units", STRYKER, THUMBNAIL)
    original = page.context.cell(ref).text

    page._open_inline_editor(page.context.cell(ref))
    editor = page.entries.open_row.editor
    combo = editor.input_widget
    combo.setEditText("stryker")  # 只是搜索词：能筛出东西，但它自己不是任何一项
    editor._commit()

    assert page.context.cell(ref).text == original, "把搜索词写进缩略图那一格了"
    # ❗ 窗口没 show 时 isVisible() 恒为 False —— 读 isHidden()（仓库里统一的写法）
    assert not editor._error.isHidden(), "既没写、也没说一句？那用户只会以为存上了"


def test_a_brand_new_key_can_still_be_typed(window) -> None:
    """库里还没有的键**照旧可以直接填** —— 换一张从没被用过的图是正当需求。

    （改动前那个纯文本框就是这个行为；换成下拉不能把这条路堵死。）
    """
    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(STRYKER)
    page = window._unit_editor.main_page
    ref = CellRef("Units", STRYKER, THUMBNAIL)

    page._open_inline_editor(page.context.cell(ref))
    editor = page.entries.open_row.editor
    editor.input_widget.setEditText("MY_CUSTOM_UNIT-Label")
    editor._commit()

    assert page.context.cell(ref).text == "MY_CUSTOM_UNIT-Label"


def test_typing_pops_up_the_filtered_candidates(window) -> None:
    """打字之后候选**要弹出来** —— 这条是用户实机报出来的「没反应」。

    根因（我漏了自己仓里早就写过的那条教训，见外键那条的注释）：可编辑下拉默认是
    `PopupCompletion`，它会拿框里那串字按**前缀**把模型再滤一遍；而我们给的候选**已经筛过**
    （`BLT_SNIPER-Label` 并不以 `sniper` 开头）→ 再滤一次一条不剩 → 弹出来是空的，
    看着就是「打字没反应」。所以必须 `UnfilteredPopupCompletion` + 每次重筛后 `complete()`。
    """
    from PySide6.QtWidgets import QCompleter

    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(STRYKER)
    page = window._unit_editor.main_page
    ref = CellRef("Units", STRYKER, THUMBNAIL)
    page._open_inline_editor(page.context.cell(ref))
    combo = page.entries.open_row.editor.input_widget

    completer = combo.completer()
    assert completer is not None
    assert completer.completionMode() == QCompleter.CompletionMode.UnfilteredPopupCompletion, (
        "本地过滤没关掉 —— 打字会弹出一个空列表（用户报的就是这个）"
    )

    combo.setEditText("sniper")
    assert combo.count() > 0, "筛完一条候选都不剩"
    assert completer.completionCount() > 0, "候选是空的，用户会以为没反应"
    texts = [combo.itemText(index) for index in range(combo.count())]
    assert all("sniper" in text.lower() for text in texts), texts[:5]


# --------------------------------------------------------------------------- #
# 点候选 = 当场收工（用户口径 2026-10-01：「我还要再点一下，这纯粹就是多余操作」）
# --------------------------------------------------------------------------- #

#: 用户实机那个场景用的是 PDSS 这个单位、词是 `PDSS`（恰好筛出一条）。
#: 但那一行的缩略图**本来就是** `RU_PDSS-Label` —— 拿它断言「写回了」是恒真的，
#: 所以这两条用例换 LAV-25（缩略图本来是 `US_LAV25-Label`）+ 词 `BALLOON`，
#: 同一个形状：**筛出来只剩一条、且与当前值不同**。
LAV25 = 29
NEEDLE = "BALLOON"


def _interaction(window, unit_id: int):
    """打开单位 -> 点开缩略图那一格 -> 交回 (page, ref, editor, combo)。"""
    from ba_mod_maker.core.edit.refs import CellRef

    window._open_unit_editor(unit_id)
    page = window._unit_editor.main_page
    ref = CellRef("Units", unit_id, THUMBNAIL)
    page._open_inline_editor(page.context.cell(ref))
    editor = page.entries.open_row.editor
    return page, ref, editor, editor.input_widget


def test_clicking_a_candidate_commits_at_once(window) -> None:
    """点候选 -> **当场写回并收起**；不该还要再点一下别处才收工。

    坑：重建候选时 `clear()` + `addItem()` 已经把 `currentIndex` 悄悄设成 0（那时信号
    关着），于是「点第一条」= 索引没变 = `currentIndexChanged` 不发 = 不写。筛出来只剩
    一条时**必然**撞上（用户就是这么报的）。
    """
    page, ref, _editor, combo = _interaction(window, LAV25)
    combo.setEditText(NEEDLE)
    assert combo.count() >= 1
    assert combo.currentIndex() == 0, "候选重建后 currentIndex 就是 0 —— 正是那个坑"
    target = combo.itemText(0)
    assert page.context.cell(ref).text != target

    completer = combo.completer()
    completer.activated.emit(completer.completionModel().index(0, 0))  # = 用户点了那一条

    assert page.context.cell(ref).text == target, "点了候选却没写回 —— 又要多点一下"


def test_a_search_word_with_exactly_one_match_is_taken(window) -> None:
    """打的只是搜索词、但**只筛出一条** -> 用它（与外键那条同口径：「恰好一条才用」）。

    不然按回车 / 点走之后还得回去点一下那条候选 —— 同一句用户口径。
    """
    page, ref, editor, combo = _interaction(window, LAV25)
    combo.setEditText(NEEDLE)
    only = combo.itemText(0)
    assert combo.count() == 1, f"这个词该只筛出一条，实际 {combo.count()} 条"

    editor._commit()
    assert page.context.cell(ref).text == only
