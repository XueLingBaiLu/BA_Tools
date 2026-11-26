r"""联机红线（kb/26）的**产品拦阻** —— 守卫"三条硬结论落地到用户看得见的地方"。

为什么值得守：**用户账号已经因为"改了客户端"被判 7 天**（kb/26 实测），触发场景就是
**进遭遇战**，而且当时 **EAC 是关的、我们的数据库补丁也是关的**（唯一生效的只是只读探针）。
kb/26 §3 的结论 ③ 原话：「产品里凡是"改动游戏本体 / 注入"的通道，
在用户可见处都要有一条**不可忽略**的联机警告」—— 而在此之前，产品界面上**一条都没有**。

⚠ 2026-10-17 口径收窄（kb/26 §7，用户一手）：**形态决定能不能联机** ——
**进程内注入**（MelonLoader / `version.dll` / `Mods/*.dll`）⇒ **绝对不许联机**；
**静态文件替换**（DLL 字节补丁 / `BA_Mod.zip` / 资产与数据包）⇒ 可以联机、**风险自负**。
下面 F/G/H/I 就是钉这一条的尺子（F/G 正控 · H 负控 · I 形态断言）。

判据一行
-------
    .venv\Scripts\python.exe -m pytest tests\test_online_redline.py -q
    -> 正控（有改动 ⇒ 警告）全绿 **且** 负控（无改动 ⇒ 不报）全绿

控制矩阵（⛔ 没有负控的判据不许进表）
------------------------------------
* **正控 A** 补丁打了（PATCHED）⇒ 必须报，且 kb/26 的三条硬结论逐条在文本里；
* **正控 B** 只有 BA_Mod.zip（补丁没打）⇒ 也要报 —— 那是"让游戏读我们的数据"；
* **正控 C** DLL 被别人改过（MODIFIED / NO_BACKUP）⇒ 也要报 —— 对服务端都是"改过的客户端"；
* **负控 D** 现场干净、这个动作也不产生改动 ⇒ **一个字都不许报**（假绿会让用户无视它）；
* **变异自检 E** 把"有改动"的判据挖空（redline_reasons 恒返回 []）⇒ 正控必须**当场变红**
  （证明上面那些断言不是恒真的）；
* **正控 F** 游戏根里放 `version.dll`（MelonLoader 的劫持点）⇒ 判定行必须是 `_FORM_INJECTION`；
* **正控 G** `Mods/BaModProbe.dll`（2026-10-10 那次事故的真实探针形态）⇒ 同样命中注入；
* **负控 H** `Mods/Maps`（游戏自建的空目录）⇒ **不算**注入（这条是假绿的尺子）；
* **断言 I** 只有静态补丁时判定行是 `_FORM_STATIC`（⛔ **不是**「绝对不许」那一句）——
  这条把「按形态区分」钉死；
* **GUI 注入** 注入在场时 `DllPatchDialog` 正文含 `_FORM_INJECTION`。

全部离屏跑，用临时目录摆假游戏根，**不碰游戏目录**（kb/26 的现场清理清单用户要自己看/自己删）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _fake_dll import fake_dll_bytes
from ba_mod_maker.core.asset import dllpatch, external_db
from test_dllpatch import _pretend_original

#: kb/26 §3 的三条硬结论 —— 每条挑一个**不会被改写掉**的锚点词。
CONCLUSION_ANCHORS = (
    "绝对不许进联机入口",   # ① 硬禁令（不是"不承诺"）
    "关掉 EAC 不是安全措施",  # ② 两条别混为一谈
    "被判 7 天",             # ③ 代价是实测的
    "in a skirmish",        # ③ 外部独立佐证的原文
)


# --------------------------------------------------------------------------- #
# 夹具：临时目录里的假游戏根
# --------------------------------------------------------------------------- #


def _fake_game(tmp_path: Path, *, backup: bool = True, dll: bytes | None = None) -> Path:
    """摆一个"游戏根"：DLL（+ 备份）。字节与 test_dllpatch / test_dll_patch_dialog 同一份实现。"""
    data = dll if dll is not None else fake_dll_bytes()
    (tmp_path / dllpatch.DLL_NAME).write_bytes(data)
    if backup:
        (tmp_path / dllpatch.BACKUP_NAME).write_bytes(data)
    return tmp_path


@pytest.fixture
def patched_game(tmp_path: Path, monkeypatch) -> Path:
    """**正控 A 的现场**：合成 DLL 当原版 → 打补丁 → 真状态 = PATCHED。"""
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: False)
    orig = fake_dll_bytes()
    root = _fake_game(tmp_path, backup=False)
    _pretend_original(monkeypatch, orig)
    dllpatch.apply(root)
    assert dllpatch.status(root).state is dllpatch.PatchState.PATCHED, "夹具没造出 PATCHED"
    return root


# --------------------------------------------------------------------------- #
# 负控 D：没有改动 ⇒ 一个字都不许报
# --------------------------------------------------------------------------- #


def test_clean_install_is_silent(tmp_path: Path) -> None:
    """原版 DLL + 没有 BA_Mod.zip ⇒ 现场是干净的：警告必须是 None。"""
    root = _fake_game(tmp_path)
    assert dllpatch.status(root).state is dllpatch.PatchState.NOT_INSTALLED
    assert dllpatch.redline_reasons(dllpatch.status(root)) == []
    assert dllpatch.online_redline(root) is None, "无改动却报了 —— 这就是要防的假绿"


def test_missing_dll_is_silent(tmp_path: Path) -> None:
    """连 DLL 都没有（游戏没装对）时也不是"有改动" ⇒ 不许报。"""
    assert dllpatch.online_redline(tmp_path) is None


def test_no_game_root_is_silent() -> None:
    """拿不到游戏根 ⇒ 没有任何"改动"可谈 ⇒ 不许报（GUI 那一支三个按钮本来也是灰的）。"""
    assert dllpatch.online_redline(None) is None


def test_the_ruler_responds_to_the_variable(tmp_path: Path) -> None:
    """**同一个干净现场**，只翻 pending_change 这一个变量 ⇒ 答案必须跟着翻。

    这条同时是"尺子能红"的自证：如果 online_redline 恒真或恒假，这一对断言必有一条挂。
    """
    root = _fake_game(tmp_path)
    assert dllpatch.online_redline(root, pending_change=False) is None
    pending = dllpatch.online_redline(root, pending_change=True)
    assert pending is not None, "「这个入口本身会改游戏目录」都没触发警告 —— 尺子死了"
    assert "这个入口本身就会改游戏目录" in pending


# --------------------------------------------------------------------------- #
# 正控 A / B / C：三种"有改动"各自都要报
# --------------------------------------------------------------------------- #


def test_patched_dll_raises_the_redline(patched_game: Path) -> None:
    """**正控 A**：补丁打了 ⇒ 警告必须出现，且三条硬结论逐条在里面。"""
    warn = dllpatch.online_redline(patched_game)
    assert warn is not None, "打了补丁还不报 —— kb/26 结论 ③ 没落地"
    assert warn.startswith(dllpatch.ONLINE_REDLINE_HEAD)
    for anchor in CONCLUSION_ANCHORS:
        assert anchor in warn, f"警告里缺了 kb/26 的硬结论锚点：{anchor!r}"
    assert dllpatch.PACKAGE_NAME in warn, "现场读数里没点名 BA_Mod.zip"


def test_db_zip_alone_raises_the_redline(tmp_path: Path) -> None:
    """**正控 B**：补丁没打、只往游戏根目录放了 `BA_Mod\\BA_Mod.zip` ⇒ 也要报。

    那是"让游戏读我们的数据"的另一半 —— 补丁是休眠的，包放进去就等于改了现场。
    """
    root = _fake_game(tmp_path)
    assert dllpatch.status(root).state is dllpatch.PatchState.NOT_INSTALLED
    external_db.ensure_zip_dir(root)
    external_db.game_zip_path(root).write_bytes(b"not a real package")
    warn = dllpatch.online_redline(root)
    assert warn is not None, "游戏根目录里有 BA_Mod.zip 却不报"
    assert dllpatch.PACKAGE_NAME in warn


@pytest.mark.parametrize("with_backup", [True, False])
def test_foreign_modified_dll_raises_the_redline(tmp_path: Path, with_backup: bool) -> None:
    """**正控 C**：DLL 被改过但不是本工具打的（MODIFIED / NO_BACKUP）⇒ 也要报。

    ⛔ 判据只看"改没改"，不看"谁改的" —— 对服务端来说都是改过的客户端。
    """
    orig = fake_dll_bytes()
    tampered = bytearray(orig)
    tampered[0x40] ^= 0xFF  # 改一个字节：不再是本工具已知的原版
    # ⚠ 备份写的必须是**另一份**字节：DLL 与备份逐字节相同的话，status 会判 NOT_INSTALLED
    #   （"DLL 还是原版"），那就落在负控那一支上了 —— 第一版就是这么写的，用例当场红。
    (tmp_path / dllpatch.DLL_NAME).write_bytes(bytes(tampered))
    if with_backup:
        (tmp_path / dllpatch.BACKUP_NAME).write_bytes(orig)
    st = dllpatch.status(tmp_path)
    assert st.state in (dllpatch.PatchState.MODIFIED, dllpatch.PatchState.NO_BACKUP), st.state
    assert dllpatch.online_redline(tmp_path) is not None


# --------------------------------------------------------------------------- #
# 正控 F / G、负控 H、形态断言 I：**形态决定能不能联机**（kb/26 §7）
# --------------------------------------------------------------------------- #


def test_version_dll_is_the_injection_form(tmp_path: Path) -> None:
    """**正控 F**：游戏根里放了 `version.dll` ⇒ 判定行是注入形态，正文含「绝对不许联机」。

    `version.dll` 只要几个字节就够 —— 判据只查"这个名字在不在"，不看内容（⛔ 不做启发式）。
    """
    root = _fake_game(tmp_path)
    (root / "version.dll").write_bytes(b"MZ")
    reason = "；".join(dllpatch.injection_reasons(root))
    assert "version.dll" in reason, f"没点名 version.dll：{reason!r}"
    warn = dllpatch.online_redline(root)
    assert warn is not None, "有注入却不报 —— 这正是 7 天那种形态"
    assert dllpatch.ONLINE_REDLINE_FORM_INJECTION in warn
    assert "绝对不许联机" in warn
    assert dllpatch.ONLINE_REDLINE_FORM_STATIC not in warn, "注入现场却说成静态形态"
    assert dllpatch.ONLINE_REDLINE_FORM_PENDING not in warn


def test_mods_probe_dll_is_the_injection_form(tmp_path: Path) -> None:
    """**正控 G**：`Mods/BaModProbe.dll`（2026-10-10 那次事故的真实探针）⇒ 同样命中。

    ⛔ 判的是 `Mods` **下面的 DLL**，不是 `Mods` 目录本身（H 就是它的负控）。
    """
    root = _fake_game(tmp_path)
    mods = root / dllpatch.MODS_DIR_NAME
    mods.mkdir()
    (mods / "BaModProbe.dll").write_bytes(b"MZ")
    reason = "；".join(dllpatch.injection_reasons(root))
    assert "BaModProbe.dll" in reason, f"没点名探针 DLL：{reason!r}"
    warn = dllpatch.online_redline(root)
    assert warn is not None
    assert dllpatch.ONLINE_REDLINE_FORM_INJECTION in warn
    assert "绝对不许联机" in warn


def test_mods_subdirectory_is_not_an_injection(tmp_path: Path) -> None:
    """**负控 H**：`Mods/Maps`（游戏自建的空目录）**不算**注入 —— 这条是假绿的尺子。

    ⛔ `Mods` 目录**本身**是游戏自己建的（`Maps/Scenarios/Units` 三个空目录，kb/26 §4）。
    把它当注入 ⇒ 每个干净现场都被说成"7 天那种形态"，用户从那一刻起就会无视整条警告。
    """
    root = _fake_game(tmp_path)
    (root / dllpatch.MODS_DIR_NAME / "Maps").mkdir(parents=True)
    assert dllpatch.injection_reasons(root) == [], "空目录 `Mods/Maps` 被当成注入了"
    assert dllpatch.online_redline(root) is None, "干净现场＋游戏自建空目录 ⇒ 一个字都不该报"


def test_static_patch_is_the_static_form_not_the_ban_form(patched_game: Path) -> None:
    """**断言 I**：只有静态补丁时判定行是 `_FORM_STATIC`（⛔ **不是**「绝对不许」那一句）。

    这条把「按形态区分」钉死：谁把两种形态合回一句（改前的口径）⇒ 这条当场红。
    """
    warn = dllpatch.online_redline(patched_game)
    assert warn is not None
    assert dllpatch.ONLINE_REDLINE_FORM_STATIC in warn
    assert "可以联机" in warn and "风险自负" in warn
    assert dllpatch.ONLINE_REDLINE_FORM_INJECTION not in warn


def test_pending_change_on_a_clean_site_uses_the_pending_form(tmp_path: Path) -> None:
    """即将写静态文件（现场还干净）⇒ `_FORM_PENDING`，措辞仍是"可以联机、风险自负"。"""
    root = _fake_game(tmp_path)
    warn = dllpatch.online_redline(root, pending_change=True)
    assert warn is not None
    assert dllpatch.ONLINE_REDLINE_FORM_PENDING in warn
    assert "可以联机" in warn and "风险自负" in warn


def test_injection_dominates_the_static_form(patched_game: Path) -> None:
    """注入压过静态：两者同时在现场 ⇒ 必须说「绝对不许联机」。"""
    (patched_game / "version.dll").write_bytes(b"MZ")
    warn = dllpatch.online_redline(patched_game)
    assert warn is not None
    assert dllpatch.ONLINE_REDLINE_FORM_INJECTION in warn
    assert dllpatch.ONLINE_REDLINE_FORM_STATIC not in warn


@pytest.mark.parametrize("name", [n for n, _ in dllpatch.INJECTION_MARKERS])
def test_every_declared_marker_counts_as_an_injection(tmp_path: Path, name: str) -> None:
    """整类断言：`INJECTION_MARKERS` 里**每一条**单独出现都要命中（⛔ 一条都不许是摆设）。"""
    root = _fake_game(tmp_path)
    (root / name).write_bytes(b"MZ")
    assert any(name in r for r in dllpatch.injection_reasons(root)), f"{name} 没被认出来"


def test_summary_lines_carry_the_injection(tmp_path: Path) -> None:
    """CLI 侧：**只有注入**（DLL 原版、没有 zip）时，summary_lines 里也要有 HEAD ＋ 原因。"""
    root = _fake_game(tmp_path)
    assert dllpatch.redline_reasons(dllpatch.status(root)) == []
    (root / "version.dll").write_bytes(b"MZ")
    text = "\n".join(dllpatch.status(root).summary_lines())
    assert dllpatch.ONLINE_REDLINE_HEAD in text
    assert "version.dll" in text


# --------------------------------------------------------------------------- #
# 落点：CLI 能看见（summary_lines 与动作的返回值）
# --------------------------------------------------------------------------- #


def test_summary_lines_carry_the_redline(patched_game: Path) -> None:
    """CLI 的 status/apply/restore 共用 summary_lines ⇒ 警告必须在里面。

    这条是"产品可见处"的机械判据：CLI 那三个入口**一行代码都没改**
    （写入范围不许动 cli/__main__.py），靠的是共用的这几行。
    """
    text = "\n".join(dllpatch.status(patched_game).summary_lines())
    assert dllpatch.ONLINE_REDLINE_HEAD in text


def test_summary_lines_stay_clean_when_nothing_changed(tmp_path: Path) -> None:
    """负控：干净现场的 summary_lines **不许**夹带红线。"""
    text = "\n".join(dllpatch.status(_fake_game(tmp_path)).summary_lines())
    assert dllpatch.ONLINE_REDLINE_HEAD not in text


def test_apply_result_carries_the_redline(patched_game: Path) -> None:
    """打补丁的返回值**以红线开头**（CLI 打完之后那一眼就能看到）。"""
    msg = dllpatch.apply(patched_game)  # 已经是 PATCHED ⇒ 走"无需重复"那一支
    assert "无需重复" in msg
    assert msg.startswith(dllpatch.ONLINE_REDLINE_HEAD), "动作的返回消息里没有红线"


def test_restore_keeps_warning_while_the_zip_stays(patched_game: Path, monkeypatch) -> None:
    """还原回原版、但 BA_Mod.zip 还在 ⇒ **仍然要报**（现场还没干净）。

    2026-11-10 起 restore 的返回消息不再挂红线前缀（对话框顶部常驻）——
    改钉：还原后 status 摘要里红线仍在（zip 没删 ⇒ reasons 非空）。
    """
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: False)
    external_db.ensure_zip_dir(patched_game)
    external_db.game_zip_path(patched_game).write_bytes(b"x")
    msg = dllpatch.restore(patched_game)
    assert "已还原" in msg
    text = "\n".join(dllpatch.status(patched_game).summary_lines())
    assert dllpatch.ONLINE_REDLINE_HEAD in text
    # 把包也删掉 ⇒ 现场干净了，必须恢复安静（负控收尾）
    external_db.game_zip_path(patched_game).unlink()
    assert dllpatch.online_redline(patched_game) is None


# --------------------------------------------------------------------------- #
# 变异自检 E：断言不是恒真的
# --------------------------------------------------------------------------- #


def test_redline_is_driven_by_the_reasons_not_by_a_constant(patched_game, monkeypatch) -> None:
    """**变异自检**：把"有改动"的判据挖空 ⇒ 正控必须当场变红。

    这一条是 AGENTS §0.9 动作 2 的机械版：喂一个已知违规的点（这里是把判据挖空），
    尺子必须红。它红了 ⇒ 上面 `test_patched_dll_raises_the_redline` 那些断言才是真的在断言。
    """
    assert dllpatch.online_redline(patched_game) is not None
    monkeypatch.setattr(dllpatch, "redline_reasons", lambda _st: [])
    assert dllpatch.online_redline(patched_game) is None, "挖空判据之后还报 —— 说明它没接判据"


def test_mutated_negative_would_fail_the_positive_assertion(tmp_path, monkeypatch) -> None:
    """反向变异：让"没有改动"也说成有改动 ⇒ 负控断言必须红。

    做法：把 NOT_INSTALLED 也塞进 reasons。若 `test_clean_install_is_silent` 的断言是真的，
    这里就必须能造出"干净现场也报警"的状态。
    """
    root = _fake_game(tmp_path)
    monkeypatch.setattr(dllpatch, "redline_reasons", lambda _st: ["人为塞进去的一条"])
    assert dllpatch.online_redline(root) is not None, "变异没生效 —— 负控那条断言就是空的"


# --------------------------------------------------------------------------- #
# GUI：对话框顶部那条收不起来的警告
# --------------------------------------------------------------------------- #


def test_dialog_shows_redline_banner_when_dirty(qapp, patched_game: Path) -> None:
    from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

    dlg = DllPatchDialog(game_root=patched_game)
    try:
        assert not dlg._redline_badge.isHidden(), "有改动时徽章必须是显示的"
        assert not dlg._redline.isHidden(), "有改动时正文必须是显示的"
        assert dlg._redline_badge.text() == dllpatch.ONLINE_REDLINE_HEAD
        body = dlg._redline.text()
        for anchor in CONCLUSION_ANCHORS:
            assert anchor in body, f"对话框正文里缺硬结论锚点：{anchor!r}"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_hides_redline_banner_when_clean(qapp, tmp_path: Path) -> None:
    """**负控 D 的 GUI 版**：干净现场 ⇒ 顶部一个字都不显示。"""
    from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

    dlg = DllPatchDialog(game_root=_fake_game(tmp_path))
    try:
        assert dlg._redline_badge.isHidden(), "没改动却亮着警告 —— 用户会开始无视它"
        assert dlg._redline.isHidden()
        assert dlg._redline.text() == ""
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_without_game_root_keeps_the_banner_hidden(qapp, monkeypatch) -> None:
    from ba_mod_maker.app.shell import dll_patch as mod

    monkeypatch.setattr(mod.game_mod, "find_game_root_for_db", lambda: None)
    dlg = mod.DllPatchDialog()
    try:
        assert dlg._redline_badge.isHidden()
        assert dlg._redline.isHidden()
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_banner_turns_on_after_apply(qapp, tmp_path: Path, monkeypatch) -> None:
    """**端到端的那一下**：从干净现场出发 → 打完补丁 → 徽章自己亮起来。

    这条覆盖的是"用户真的走了一遍"：进对话框（干净、无警告）→ 点打补丁 → 警告出现。
    """
    from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: False)
    orig = fake_dll_bytes()
    root = _fake_game(tmp_path, backup=False)
    _pretend_original(monkeypatch, orig)

    dlg = DllPatchDialog(game_root=root)
    try:
        assert dlg._redline_badge.isHidden(), "一开始是干净的，不该亮"
        dllpatch.apply(root)
        dlg.refresh()
        assert not dlg._redline_badge.isHidden(), "打完之后警告必须自己亮起来"
        assert dllpatch.ONLINE_REDLINE_HEAD in dlg._status.text()
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_shows_the_injection_form(qapp, tmp_path: Path) -> None:
    """GUI：注入在场时正文里就是**注入那条**判定行（不是"静态可以联机"那条）。"""
    from ba_mod_maker.app.shell.dll_patch import DllPatchDialog

    root = _fake_game(tmp_path)
    (root / "version.dll").write_bytes(b"MZ")
    dlg = DllPatchDialog(game_root=root)
    try:
        assert not dlg._redline_badge.isHidden()
        body = dlg._redline.text()
        assert dllpatch.ONLINE_REDLINE_FORM_INJECTION in body
        assert "绝对不许联机" in body
        assert dllpatch.ONLINE_REDLINE_FORM_STATIC not in body
    finally:
        dlg.close()
        dlg.deleteLater()
