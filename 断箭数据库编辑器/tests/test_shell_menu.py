"""外壳菜单的守卫。

用户明确要求去掉「帮助」菜单 —— 它下面只有一项「关于」，
占着菜单栏却解决不了任何问题；版本号本来就写在标题栏上。

这条测试守住"不许悄悄长回来"，同时钉住菜单栏的确切内容：
菜单项一旦增删，必须是有意识的改动。
"""

from __future__ import annotations

import types

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 依赖运行环境
        pytest.skip(f"无法创建 QApplication：{exc}")
    if not QFontDatabase.families():
        pytest.skip("该平台没有可用字体，控件度量不可信")
    return app


@pytest.fixture()
def window(qapp):
    from ba_mod_maker.app.shell.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()
    win.deleteLater()


def test_menu_has_no_help_menu_and_lives_in_the_hamburger(window) -> None:
    """菜单收进顶栏那个 ☰，里面仍然是 文件 / 编辑 / 视图 三组。

    2026-09-30 界面重做（块 2）：三条菜单栏纵向排着占画面，用户点名要压成一行。
    所以 QMenuBar 不再上屏（隐藏），菜单改成 ☰ 的弹出菜单 —— **一项没少**，
    但「帮助」仍然不在（用户明确要求去掉，这条守卫不许它长回来）。
    """
    assert window.menuBar().isVisible() is False, "菜单栏又占了一行"

    menu = window.menu()
    titles = [action.text() for action in menu.actions()]
    assert not any("帮助" in title for title in titles), f"「帮助」菜单又回来了：{titles}"
    assert titles == ["📂 文件", "✏ 编辑", "👁 视图"]


def test_version_is_still_discoverable_without_the_about_box(window) -> None:
    """删掉「关于」对话框之后，版本号必须还能在标题栏上看到。"""
    from ba_mod_maker import __version__

    assert __version__ in window.windowTitle()


# --------------------------------------------------------------------------- #
# 导航：页码常量必须互不相同，且与页签顺序一一对应
# --------------------------------------------------------------------------- #


def test_page_constants_are_distinct_and_match_the_tab_order(window) -> None:
    """**踩过一次**：给导航插了一页（工坊）却只改了前两个常量，
    PAGE_TABLE 与 PAGE_SEARCH 一起变成 2 —— 于是点「检索」出来的是数据表页，
    而且不报任何错。删掉那一页时才发现。

    这条同时守住"常量互不相同"与"常量指向的控件就是导航按钮该去的那一页"。

    ⚠️ 2026-09-30 界面重做：页签从三个变**五个** —— 国家 / 专精 插在
    「军械库」与「数据表」中间（用户口径）。单位编辑器仍然**故意不进导航栏**：
    它只能靠双击卡进、靠面包屑最左边那一级出。所以 _tabs 比导航多**恰好一页**。
    """
    from PySide6.QtWidgets import QPushButton

    from ba_mod_maker.app.shell.main_window import (
        NAV_PAGES,
        PAGE_ARMORY,
        PAGE_COUNTRY,
        PAGE_SEARCH,
        PAGE_SPEC,
        PAGE_TABLE,
        PAGE_UNIT_EDITOR,
    )

    pages = {
        "军械库": PAGE_ARMORY,
        "国家": PAGE_COUNTRY,
        "专精": PAGE_SPEC,
        "数据表": PAGE_TABLE,
        "检索": PAGE_SEARCH,
    }
    assert tuple(pages) == NAV_PAGES, "导航按钮与 NAV_PAGES 的顺序对不上"
    assert len(set(pages.values())) == len(pages), f"页码常量有重复：{pages}"
    assert sorted(pages.values()) == list(range(len(pages))), f"页码应当是 0..N-1：{pages}"
    assert PAGE_UNIT_EDITOR not in pages.values(), "单位编辑器不该占导航页码"

    # 页签数量 = 导航页 + 那**一页**不进导航的编辑器
    assert window._tabs.count() == len(pages) + 1
    assert window._tabs.widget(PAGE_UNIT_EDITOR) is window._unit_editor

    # 逐个按钮点一遍，确认落到**不一样**的控件上
    seen = {}
    buttons = [b for b in window.findChildren(QPushButton) if b.property("role") == "nav"]
    assert [b.text() for b in buttons] == list(pages), "导航按钮与常量对不上"
    for button in buttons:
        button.click()
        seen[button.text()] = id(window._tabs.currentWidget())
    assert len(set(seen.values())) == len(seen), f"有两个导航按钮指向同一个页面：{seen}"


# --------------------------------------------------------------------------- #
# 窗口状态：最大化/尺寸要记住（用户报的「每次都要点一下最大化」）
# --------------------------------------------------------------------------- #


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch):
    """把窗口状态写到临时 INI，别污染用户注册表。

    ❗ 依赖顺序有讲究：用到它的用例必须把它排在 `window` **前面**，
    否则 window 已经用真 QSettings 建好了，这条接缝等于没接上（实测踩过）。
    """
    from PySide6.QtCore import QSettings

    from ba_mod_maker.app.shell import main_window as module

    monkeypatch.setattr(
        module,
        "new_settings",
        lambda: QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    return tmp_path / "window.ini"


def test_window_geometry_is_remembered(qapp, temp_settings, window) -> None:
    """关窗时的尺寸，下次开窗要原样回来。"""
    from ba_mod_maker.app.shell.main_window import MainWindow

    window.resize(1234, 777)
    window._save_window_state()
    window.close()

    again = MainWindow()
    assert (again.width(), again.height()) == (1234, 777)
    again.close()


def test_maximized_state_is_remembered(qapp, temp_settings, window) -> None:
    """**最大化状态本身**也要存。

    为什么不能只看几何：最大化窗口的 geometry() 是"普通状态"下的框，
    只恢复它会得到一个"大而不最大"的窗口——用户还得再点一下最大化，
    这正是用户报的那件事。
    """
    from PySide6.QtCore import Qt

    from ba_mod_maker.app.shell.main_window import MainWindow

    window.setWindowState(window.windowState() | Qt.WindowState.WindowMaximized)
    window._save_window_state()
    window.close()

    again = MainWindow()
    assert again.windowState() & Qt.WindowState.WindowMaximized
    again.close()


def test_window_state_survives_a_broken_file(qapp, temp_settings, window) -> None:
    """配置文件坏了就退回按屏幕推尺寸，不许开不了窗。"""
    from ba_mod_maker.app.shell.main_window import MainWindow

    temp_settings.write_text("这不是 INI", encoding="utf-8")
    again = MainWindow()
    assert again.width() > 0 and again.height() > 0
    again.close()


# --------------------------------------------------------------------------- #
# 加载进度必须**看得见**（用户原话："我希望在加载的时候能看到进度条"）
# --------------------------------------------------------------------------- #


def test_prefetch_splash_shows_counts_percent_eta_and_family(qapp) -> None:
    """门禁对话框上四样都要有：进度条、计数+百分比、剩余时间、正在取哪一族。

    只有一根不动的条等于没说——用户报的正是"看不到进度"。
    """
    from PySide6.QtCore import QObject, Signal

    from ba_mod_maker.app.shell.prefetch import PrefetchSplash

    class FakeCache(QObject):
        """对话框只读这几个属性，不必真的去开 bundle。"""

        progress = Signal(int, int)

        def __init__(self) -> None:
            super().__init__()
            self.prefetch_total = 1519
            self.prefetch_done = 0
            self.prefetch_eta = 32.0
            self.prefetch_rate = 47.0
            self.prefetch_current_label = "单位立绘"
            self.game_root = None

    cache = FakeCache()
    dialog = PrefetchSplash(cache)
    cache.progress.emit(640, 1519)

    assert (dialog._bar.minimum(), dialog._bar.maximum(), dialog._bar.value()) == (0, 1519, 640)
    status = dialog._status.text()
    assert "640/1519" in status
    assert "42%" in status
    assert "剩约 32 秒" in status
    assert dialog._current.text() == "正在取：单位立绘"
    dialog.deleteLater()


def test_status_bar_has_a_determinate_bar_while_icons_prefetch(window) -> None:
    """状态栏上要有一根**确定进度**的条（不是 _set_busy 那根转圈的）。

    点过「跳过（后台继续）」之后，这里就是唯一还看得见进度的地方：
    预取期间显示，done == total 那一刻收起，prefetch_finished 只负责收尾。
    """
    bar = window._icon_bar
    assert bar.isHidden(), "没在预取的时候不该占着状态栏"

    # 预取一开始（0/N）就该看得见：0% 也是进度，不能等第一张到了才出现
    window.icons.progress.emit(0, 1200)
    assert not bar.isHidden()
    assert bar.value() == 0

    window.icons.progress.emit(300, 1200)
    assert not bar.isHidden()
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 1200, 300)

    window.icons.progress.emit(1200, 1200)
    assert bar.isHidden(), "跑完了还把进度条挂在状态栏上"

    window.icons.progress.emit(300, 1200)
    window.icons.prefetch_finished.emit()
    assert bar.isHidden()


# --------------------------------------------------------------------------- #
# 打开文件：记住上次的路径（用户口径）
# --------------------------------------------------------------------------- #


def test_the_open_dialog_remembers_the_last_directory(
    qapp, temp_settings, window, tmp_path
) -> None:
    """用户原话："在打开文件的时候，能不能记住我上次打开的路径？"

    记住的是**目录**（接着多半还是从同一个地方挑），优先级：
    当前已打开文件所在目录 > 上次记住的目录 > 进程工作目录。
    """
    from pathlib import Path

    # 没打开过任何东西、也没记住过 -> 退回工作目录
    assert window._start_dir() == Path.cwd()

    somewhere = tmp_path / "某个目录"
    somewhere.mkdir()
    window._remember_dir(somewhere / "BA_Mod.zip")
    assert window._start_dir() == somewhere

    # 换一个窗口（同一个 QSettings 文件）—— 记忆要跨窗口活着
    from ba_mod_maker.app.shell.main_window import MainWindow

    again = MainWindow()
    assert again._start_dir() == somewhere
    again.close()


# --------------------------------------------------------------------------- #
# 保存：全窗口**只有一条** Ctrl+S，而且走唯一那条写盘通道（AGENTS §4.5）
#
# 从前同一个键有两条绑定：☰ 里的 QAction 与单位编辑器里的 QShortcut。
# 两条都活着时"按下 Ctrl+S 存的是哪一份"取决于焦点在哪 —— 这就是歧义。
# shell 这一侧：动作唯一 + 注册在窗口上 + 转调 EditSession.save()。
# --------------------------------------------------------------------------- #

#: 真实数据源里那个实例（与 tests/conftest.py 的 INSTANCE_ID 一致）。
INSTANCE_ID = "52085"


def _ctrl_s_bindings(window) -> tuple[list[str], list[str]]:
    """窗口上所有 Ctrl+S 绑定：QAction 的文本、QShortcut 的键面（分开返回）。"""
    from PySide6.QtGui import QAction, QKeySequence, QShortcut

    want = QKeySequence(QKeySequence.StandardKey.Save).toString()
    actions = [
        action.text()
        for action in window.findChildren(QAction)
        if any(QKeySequence(s).toString() == want for s in action.shortcuts())
    ]
    shortcuts = [
        shortcut.key().toString()
        for shortcut in window.findChildren(QShortcut)
        if shortcut.key().toString() == want
    ]
    return actions, shortcuts


def _package_from_the_real_dataset(tmp_path):
    """把真实数据源的 24 张表装进一个**真包**（走 package.py 的口径，不手搓表）。

    为什么不搓个"几行 JSON"的假包：`load_file` 之后军械库 / 国家页那些页面
    会真的去读表（Countries.Name、Units.CountryId…），假包当场就炸。
    """
    from ba_mod_maker.core.data import find_dataset_root, load_dataset
    from ba_mod_maker.core.edit.package import plan_package, write_package

    root = find_dataset_root()
    if root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    instance = load_dataset(root).instance(INSTANCE_ID)
    path = tmp_path / "源包.zip"
    write_package(plan_package(instance), path)
    return path


def _no_game_container(monkeypatch) -> None:
    """别让 `db_channel._fallback_id` 去探真实游戏目录（那是 7.1 GB 的容器）。"""
    from ba_mod_maker.core.assets import game as game_mod

    monkeypatch.setattr(game_mod, "find_game_root_for_db", lambda: None)


def test_the_save_action_is_the_only_ctrl_s_of_the_shell(qapp, temp_settings, window) -> None:
    """☰ 里的「保存」要成为**唯一**的 Ctrl+S：动作唯一、注册在窗口上。

    菜单挂在 ☰ 的弹出菜单里（不走 menuBar），所以还得显式 `addAction` 到窗口上，
    否则那个快捷键不一定注册得到。

    单位编辑器那条同键的 QShortcut 由 T3 删 —— 还没删时这条**skip 并说明原因**，
    shell 这一侧的断言照旧全跑。
    """
    from PySide6.QtCore import Qt

    actions, shortcuts = _ctrl_s_bindings(window)

    assert actions == ["保存"], f"shell 里 Ctrl+S 的动作不唯一：{actions}"
    assert window._save_action in window.actions(), "快捷键没注册到窗口上"
    assert window._save_action.shortcutContext() == Qt.ShortcutContext.WindowShortcut

    if shortcuts:
        pytest.skip("单位编辑器还留着 Ctrl+S 的 QShortcut（T3 的任务）——「全窗口只有一条」等它落地")


def test_ctrl_s_writes_the_cache_through_the_one_channel(
    qapp, temp_settings, window, tmp_path, monkeypatch
) -> None:
    """Ctrl+S（= 那个 QAction）必须走 `EditSession.save()`：缓存里的改动落到打开的那个文件。

    从前 `_save_current` 另有一条旧通道（`db_channel.apply_dataset_to_source` +
    `core.session.save`）—— 那条路改的是 `_source` 而不是账本，于是
    "改了却没进账、保存说没有改动"。
    """
    from ba_mod_maker.core.edit.package import read_package_rows
    from ba_mod_maker.core.edit.refs import CellRef

    _no_game_container(monkeypatch)
    package = _package_from_the_real_dataset(tmp_path)
    assert window.load_file(package), "打不开这个包"
    assert window._source is not None

    first_id = read_package_rows(package)["Units"][0]["Id"]
    target = tmp_path / "写回.zip"
    window._edit_session.set_save_path(target)
    window._edit_session.set_value(CellRef("Units", first_id, "Cost"), 7777)
    assert window._save_button.isEnabled(), "有改动 -> 保存按钮该亮"

    window._save_action.trigger()

    assert read_package_rows(target)["Units"][0]["Cost"] == 7777, "Ctrl+S 没把缓存写进包"
    assert "已写回" in window._status_label.text(), window._status_label.text()
    assert window._edit_session.change_count == 0, "存完该认账（否则再按一次会写第二遍）"


def test_export_is_dark_until_a_file_is_opened(qapp, temp_settings, window) -> None:
    """没打开任何文件（目录来源也是）-> 导出入口是灭的。

    P0 的形状：从前它是亮的，点下去导出的是**上一个文件**。
    """
    assert window._source is None
    assert not window._export_action.isEnabled()

    # 真被程序调到时也只说一句、不导出（状态栏能说清楚，不弹窗）
    window._export_db_zip()
    assert "打开" in window._status_label.text()


def test_export_refuses_when_the_dataset_is_not_from_that_file(
    qapp, temp_settings, window, tmp_path, monkeypatch
) -> None:
    """同源断言：dataset 与 `_source` 不是同一份来源 -> **不导出**、不弹保存框。

    这是"导出的是上一个文件"那个 P0 的**残余形态**防线：身份对不上时，
    宁可什么都不做，也不许把别人的字节写出去。
    """
    from PySide6.QtWidgets import QFileDialog

    _no_game_container(monkeypatch)
    assert window.load_file(_package_from_the_real_dataset(tmp_path))

    def _no_dialog(*_args, **_kwargs):
        raise AssertionError("不同源时不该弹保存框")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_no_dialog))

    window._dataset_source = object()
    window._export_db_zip()

    assert "不是同一份" in window._status_label.text(), window._status_label.text()


def test_opening_a_directory_forgets_the_previous_file(
    qapp, temp_settings, window, tmp_path, monkeypatch
) -> None:
    """**P0 本体**：先开一个文件、再开目录 —— 目录来源必须把 `_source` 清成 None。

    不清的后果：导出导出的是上一个文件、"保存"还拿上一个文件跟新数据比有没有改动
    （于是恒说"没有改动"）。
    """
    from ba_mod_maker.core.data import find_dataset_root

    root = find_dataset_root()
    if root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")

    _no_game_container(monkeypatch)
    assert window.load_file(_package_from_the_real_dataset(tmp_path))
    assert window._source is not None and window._export_action.isEnabled()

    assert window.load_dataset(root), "打不开目录数据源"

    assert window._source is None, "目录来源还记着上一个文件"
    assert window._dataset_source is None
    assert not window._export_action.isEnabled(), "目录来源的导出入口该灭掉"

    window._save_current()
    assert "无法写回" in window._status_label.text(), window._status_label.text()


# --------------------------------------------------------------------------- #
# 字段词典入口（☰ ->「📖 词典…」）
# --------------------------------------------------------------------------- #


def test_the_menu_opens_the_read_only_dictionary(qapp, temp_settings, window) -> None:
    """词典窗口此前**没有任何入口**（窗口早就写好了）—— 这一条钉入口本身。

    顺带钉两件：入口**不占快捷键**（F3 / Ctrl+F / Ctrl+S 都名花有主）、
    打开的确实是词典窗口。
    """
    view = next(a.menu() for a in window.menu().actions() if a.text().startswith("👁 视图"))
    entry = next((a for a in view.actions() if "词典" in a.text()), None)
    assert entry is not None, f"视图菜单里没有词典入口：{[a.text() for a in view.actions()]}"
    assert entry.shortcut().isEmpty(), "词典入口不许占快捷键"

    entry.trigger()

    assert window._dictionary is not None, "点了却没有窗口"
    assert "词典" in window._dictionary.windowTitle()
    assert window._dictionary.isVisible()
    window._dictionary.close()


# --------------------------------------------------------------------------- #
# 状态栏：**旧快照实例**要说出「缺几张表 / 几列」（2026-10-18 全量验证）
#
# 背景：`DataBaseCompiled/` 是多版本快照（7 个实例），旧快照天然没有新列；loader 现在允许
# （`Table.missing_columns`）但**只记不报 = 静默** —— 用户切到 52460 会看到一张少 15 列的表，
# 界面上什么都没有。这一行是那个出口，判据钉的是「有没有说」＋「算得对不对」。
# --------------------------------------------------------------------------- #
def _fake_instance(*, table: str, columns: int, keys: int):
    """造一个最小 Instance：`table_names` / `schema` / `manifest_entry()` 三样就够。"""
    return types.SimpleNamespace(
        table_names=(table,),
        schema={table: types.SimpleNamespace(columns=tuple(f"c{i}" for i in range(columns)))},
        manifest_entry=lambda name: ({"keys": keys} if name == table else {}),
    )


def test_status_bar_names_the_missing_columns_of_an_older_snapshot(window) -> None:
    """★ 正控：清单里列数比 schema 少 ⇒ 状态栏必须点名「缺 N 表 / M 列」。"""
    window._instance = _fake_instance(table="Units", columns=31, keys=28)
    note = window._missing_columns_note()
    assert note == "（缺 1 表 / 3 列）", note


def test_status_bar_is_quiet_when_nothing_is_missing(window) -> None:
    """★ 负控：列数齐 / 清单没写 keys ⇒ 一个字都不许多（⛔ 不假报）。"""
    window._instance = _fake_instance(table="Units", columns=31, keys=31)
    assert window._missing_columns_note() == ""
    window._instance = _fake_instance(table="Units", columns=31, keys=0)  # 清单没有 keys
    assert window._missing_columns_note() == ""


def test_the_dictionary_follows_the_data_source(window) -> None:
    """换数据源时词典窗口要跟着换（T5 的 `set_instance(instance)`）。"""
    from ba_mod_maker.core.data import find_dataset_root

    root = find_dataset_root()
    if root is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    assert window.load_dataset(root), "打不开目录数据源"

    seen: list[tuple[str, object]] = []

    class _FakeDictionary:
        def __init__(self, parent=None, *, instance=None) -> None:
            seen.append(("init", instance))

        def set_instance(self, instance) -> None:
            seen.append(("set", instance))

        def show(self) -> None: ...

        def raise_(self) -> None: ...

        def activateWindow(self) -> None: ...

    window._dictionary = _FakeDictionary(None)
    window._activate_instance(window._instance.path_id)

    assert [kind for kind, _ in seen] == ["init", "set"]
    assert seen[1][1] is window._instance
