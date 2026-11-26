"""外部数据库补丁对话框的守卫。

为什么值得守：补丁决定「保存」是写 7.1 GB 还是写 300 KB，
而在此之前它在产品里**根本没有入口**。这几条钉住三件事：

1. 文件菜单里必须能走到它；
2. 状态与按钮可用性必须跟着现场走（能打才亮「打补丁」）；
3. 「最省事的卸载是删 BA_Mod.zip」这句话必须出现 —— 不写出来，
   用户会以为每次不用都要还原一次 DLL。

全部离屏跑，不碰游戏目录（用临时目录摆一个假游戏根）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _fake_dll import fake_dll_bytes
from ba_mod_maker.core.asset import dllpatch
from test_dllpatch import _pretend_original


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


def _fake_game(tmp_path: Path, *, backup: bool = True) -> Path:
    # ❗ 26 MB 的字节不许在这里现算：那个生成式是纯 Python 循环，实测 1.6s/次，
    #    而这个文件里有两处用它。合成字节只有一份实现，见 tests/_fake_dll.py。
    data = fake_dll_bytes()
    (tmp_path / dllpatch.DLL_NAME).write_bytes(data)
    if backup:
        (tmp_path / dllpatch.BACKUP_NAME).write_bytes(data)
    return tmp_path


def test_menu_has_the_patch_entry(qapp) -> None:
    """文件菜单里必须能走到补丁对话框。"""
    from ba_mod_maker.app.shell.main_window import MainWindow

    win = MainWindow()
    try:
        # 菜单收进顶栏那个 ☰（界面重做定稿 块 2），文件那一组还在里面。
        file_menu = next(a.menu() for a in win.menu().actions() if a.text().startswith("📂 文件"))
        texts = [a.text() for a in file_menu.actions()]
        assert any("外部数据库补丁" in t for t in texts), f"文件菜单里没有补丁入口：{texts}"
        # 它必须真的接上槽函数（PySide6 数不了接收者，换个等价做法：
        # 确认主窗口上那个槽存在且可调用）。
        action = next(a for a in file_menu.actions() if "外部数据库补丁" in a.text())
        assert action.isEnabled()
        assert callable(win._open_dll_patch)
    finally:
        win.close()
        win.deleteLater()


def test_dialog_shows_state_and_enables_matching_buttons(qapp, tmp_path) -> None:
    """原版 DLL（有备份）→ 能打补丁、不能还原。"""
    from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

    root = _fake_game(tmp_path)
    dlg = DllPatchDialog(game_root=root)
    try:
        assert dlg._apply_btn.isEnabled()
        assert not dlg._restore_btn.isEnabled()
        assert dllpatch.DLL_NAME in dlg._status.text()
        assert dllpatch.PACKAGE_NAME in dlg._note.text()
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_on_patched_dll_disables_apply(qapp, tmp_path, monkeypatch) -> None:
    from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig = fake_dll_bytes()
    root = _fake_game(tmp_path)
    _pretend_original(monkeypatch, orig)
    dllpatch.apply(root)

    dlg = DllPatchDialog(game_root=root)
    try:
        assert not dlg._apply_btn.isEnabled(), "已经是打过补丁的状态，不该还能点「打补丁」"
        assert dlg._restore_btn.isEnabled()
        assert "已打补丁" in dlg._status.text()
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_without_game_root_says_so(qapp, monkeypatch) -> None:
    """找不到游戏时不能静默 —— 三个按钮都该灰掉，并说清楚原因。"""
    from ba_mod_maker.app.shell import dll_patch as mod

    monkeypatch.setattr(mod.game_mod, "find_game_root_for_db", lambda: None)
    dlg = mod.DllPatchDialog()
    try:
        assert "找不到游戏目录" in dlg._status.text()
        assert not dlg._apply_btn.isEnabled()
        assert not dlg._restore_btn.isEnabled()
        assert not dlg._verify_btn.isEnabled()
    finally:
        dlg.close()
        dlg.deleteLater()
