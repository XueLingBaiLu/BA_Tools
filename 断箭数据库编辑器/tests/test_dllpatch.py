"""外部数据库补丁（GameAssembly.dll）的测试。

全部用**内存里的合成 DLL**，不需要游戏，也不会碰任何真实文件。
需要真 DLL 的那条标了 slow。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import dllpatch
from ba_mod_maker.core.errors import UserFacingError


@pytest.fixture(autouse=True)
def _isolate_patch_delta(tmp_path, monkeypatch):
    """⛔ 用例**不许**把反向 Δ 写进用户真正的 `~/.ba_mod_maker`。

    2026-11-12 实测事故：跑一遍本文件，`apply()` 把**真实**的
    `GameAssembly.dll.patchdelta` 覆盖成了合成 DLL 的那份（size 26214400、build 1.2.0）——
    结果 GUI/CLI 认不出已经打好的补丁，`还原 DLL` / `核对` 直接变灰。
    隔离口径：只改环境变量（`delta_path()` 认它），不碰任何别的全局。
    """
    monkeypatch.setenv("BA_MOD_MAKER_VAR_DIR", str(tmp_path / "_var"))

# --------------------------------------------------------------------------- #
# 合成 DLL
# --------------------------------------------------------------------------- #

#: 地址表里最大的 cave 偏移，再加余量 —— 合成 DLL 至少要这么大。
_MIN_SIZE = 0x1900000

#: (i * 7 + 13) % 251 的**一个周期**。
#: 251 是质数、且 7 不整除它 —— 所以 i 每走 251 步，取值就整段重复一次；
#: 拼周期表比逐字节算快三个数量级（26 MB：约 1.6s -> 约 0.2ms）。
_PATTERN_PERIOD = 251
_PATTERN = bytes((i * 7 + 13) % 251 for i in range(_PATTERN_PERIOD))


def _fake_original(size: int = _MIN_SIZE) -> bytes:
    """造一份"原版 DLL"字节：长度够、内容确定。"""
    # 与逐字节的 bytes((i * 7 + 13) % 251 for i in range(size)) 完全等价，见 _PATTERN。
    return (_PATTERN * (size // _PATTERN_PERIOD + 1))[:size]


def _write_game(tmp_path: Path, *, original: bytes | None = None, backup: bool = True):
    """在临时目录里摆一个"游戏根"：DLL（+ 备份）。"""
    dll = tmp_path / dllpatch.DLL_NAME
    data = original if original is not None else _fake_original()
    dll.write_bytes(data)
    if backup:
        (tmp_path / dllpatch.BACKUP_NAME).write_bytes(data)
    return tmp_path


def _pretend_original(
    monkeypatch, data: bytes, *, backup_name: str | None = None
) -> dllpatch.Build:
    """把 `data` 冒充成「原版」：造一个假 `Build` 并顶掉 `BUILDS`。

    ⚠️ 为什么必须顶 `BUILDS`：2026-10-14 起地址表**按原版 sha256 分版本**，`apply()` 的版本闸
    只看 `BUILDS`。只 monkeypatch 常量 `ORIG_SHA256` 的话，这个合成 DLL 照样认不出来 ——
    `apply()` 会（正确地）拒绝。假 Build 的地址表照抄 `BUILD_120`，所以打出来的字节与改版前相同。
    """
    fake = replace(dllpatch.BUILD_120, orig_sha256=hashlib.sha256(data).hexdigest())
    if backup_name is not None:
        fake = replace(fake, backup_name=backup_name)
    monkeypatch.setattr(dllpatch, "BUILDS", (fake,))
    return fake


# --------------------------------------------------------------------------- #
# 纯函数：cave 与补丁镜像
# --------------------------------------------------------------------------- #


def test_cave_has_the_fixed_length() -> None:
    """cave 长度是硬约定；改内容必须同步改这个常数。"""
    for _name, _steal, ret, cave in dllpatch.SITES:
        assert len(dllpatch.build_cave(cave, ret)) == dllpatch.CAVE_LEN


def test_cave_starts_with_the_stack_save_sequence() -> None:
    """3 个 push + sub rsp,8 —— 保住 16 字节栈对齐（实测必须）。"""
    body = dllpatch.build_cave(dllpatch.SITES[0][3], dllpatch.SITES[0][2])
    assert body[:4] == b"\x53\x56\x57\x48"
    assert body[4:8] == b"\x83\xec\x08\x48"


def test_patched_image_keeps_the_length() -> None:
    """**这条是防静默失败的**：切片越界会变成追加，PE 加载器忽略尾部 ——
    症状是"游戏照跑、补丁没生效"。
    """
    orig = _fake_original()
    out = dllpatch.patched_image(orig)
    assert len(out) == len(orig)


def test_patched_image_only_touches_the_expected_sites() -> None:
    """只应该动 2 个 cave + 2 个插桩点，别的一个字节都不能变。"""
    orig = _fake_original()
    out = dllpatch.patched_image(orig)

    # 期望被改的区间：cave 整段 + 插桩点那 5 个字节。
    touched: list[tuple[int, int]] = []
    for _name, steal, _ret, cave in dllpatch.SITES:
        touched.append((dllpatch._off(dllpatch._rva(cave)), dllpatch.CAVE_LEN))
        touched.append((dllpatch._off(dllpatch._rva(steal)), 5))

    # 判据不变（"这几个区间之外一个字节都不许动"），做法换成：把区间在**两份**里
    # 都抹成 0，剩下的逐字节比。原来是 {i for i in range(len(orig)) if orig[i] != out[i]}
    # —— 26 MB 走 Python 循环，光这一条就 2.5s；抹零是 bytearray 切片，C 速度。
    left = bytearray(orig)
    right = bytearray(out)
    for start, length in touched:
        left[start : start + length] = bytes(length)
        right[start : start + length] = bytes(length)
    assert left == right, "cave / 插桩点之外有字节被改了"


def test_patched_image_is_deterministic() -> None:
    orig = _fake_original()
    assert dllpatch.patched_image(orig) == dllpatch.patched_image(orig)


def test_steal_site_is_a_relative_jump_to_the_cave() -> None:
    """插桩点必须是 `e9 rel32`，且算出来的目标正好是 cave。"""
    import struct

    orig = _fake_original()
    out = dllpatch.patched_image(orig)
    for name, steal, _ret, cave in dllpatch.SITES:
        o_steal = dllpatch._off(dllpatch._rva(steal))
        assert out[o_steal] == 0xE9, f"{name}: 不是 e9 相对跳转"
        (rel,) = struct.unpack_from("<i", out, o_steal + 1)
        assert steal + 5 + rel == cave, f"{name}: 跳转目标不是 cave"


def test_rel32_accepts_in_range_and_rejects_overflow() -> None:
    """rel32 的边界：装得下就编码，装不下必须报错（不能静默截断）。"""
    import struct

    assert dllpatch.rel32(0x180000000, 0x180000010) == struct.pack("<i", 0x10)
    assert dllpatch.rel32(0x180000010, 0x180000000) == struct.pack("<i", -0x10)
    # 正方向刚好装得下 / 差一就装不下
    assert dllpatch.rel32(0, 2**31 - 1) == struct.pack("<i", 2**31 - 1)
    with pytest.raises(ValueError, match="rel32"):
        dllpatch.rel32(0, 2**31)
    with pytest.raises(ValueError, match="rel32"):
        dllpatch.rel32(0, -(2**31) - 1)


# --------------------------------------------------------------------------- #
# 现场状态
# --------------------------------------------------------------------------- #


def test_status_missing_dll(tmp_path) -> None:
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.MISSING
    assert not st.can_apply
    assert not st.can_restore


def test_status_no_backup_and_unknown_dll(tmp_path) -> None:
    """没有备份、DLL 又不是已知原版 —— 既不能核对也不能还原。"""
    _write_game(tmp_path, backup=False)
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.NO_BACKUP
    assert not st.has_backup
    assert not st.can_restore


def test_apply_creates_backup_and_results_in_patched(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    _write_game(tmp_path, backup=False)
    _pretend_original(monkeypatch, _fake_original())
    dllpatch.apply(tmp_path)
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.PATCHED
    assert st.has_backup
    assert st.patched_sha256 == st.dll_sha256


def test_apply_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    _write_game(tmp_path, backup=False)
    _pretend_original(monkeypatch, _fake_original())
    dllpatch.apply(tmp_path)
    first = (tmp_path / dllpatch.DLL_NAME).read_bytes()
    msg = dllpatch.apply(tmp_path)
    assert "无需重复" in msg
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == first


def test_apply_refuses_unknown_original_without_force(tmp_path, monkeypatch) -> None:
    """**这条是防打坏 DLL 的**：sha256 不符就拒绝。

    2026-10-14 起 `force` 也救不了（版本闸是硬的）—— 下一条用例专门钉它。
    """
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    _write_game(tmp_path, backup=False)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.apply(tmp_path)
    assert "不是本工具已知的原版" in str(excinfo.value)


def test_apply_refuses_while_game_running(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: True, raising=True)
    _write_game(tmp_path)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.apply(tmp_path)
    assert "游戏正在运行" in str(excinfo.value)


def test_restore_round_trips(tmp_path, monkeypatch) -> None:
    """还原必须是**逐字节**回到备份。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig = _fake_original()
    _write_game(tmp_path, original=orig)
    _pretend_original(monkeypatch, orig)
    dllpatch.apply(tmp_path)
    assert dllpatch.status(tmp_path).state is dllpatch.PatchState.PATCHED
    dllpatch.restore(tmp_path)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == orig
    assert dllpatch.status(tmp_path).state is dllpatch.PatchState.NOT_INSTALLED


def test_restore_without_backup_is_loud(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    _write_game(tmp_path, backup=False)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.restore(tmp_path)
    assert "无法还原" in str(excinfo.value)


def test_verify_reports_failure_when_modified(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig = _fake_original()
    _write_game(tmp_path, original=orig)
    _pretend_original(monkeypatch, orig)
    dllpatch.apply(tmp_path)
    ok, _msg = dllpatch.verify(tmp_path)
    assert ok is True
    # 手动改一个字节 -> verify 必须报 FAILED
    dll = tmp_path / dllpatch.DLL_NAME
    data = bytearray(dll.read_bytes())
    data[0] ^= 0xFF
    dll.write_bytes(bytes(data))
    ok2, msg2 = dllpatch.verify(tmp_path)
    assert ok2 is False
    assert "FAILED" in msg2


def test_uninstall_hint_mentions_package_name() -> None:
    """卸载提示必须点名**我们产出的那个包**（BA_Mod.zip）—— 不写出来，
    用户会以为每次不用都要还原一次 DLL。"""
    hint = dllpatch.uninstall_hint()
    assert dllpatch.PACKAGE_NAME in hint


# --------------------------------------------------------------------------- #
# 多版本地址表（2026-10-14：按原版 sha256 分版本）
# --------------------------------------------------------------------------- #


def test_build_120_is_byte_for_byte_the_old_constant_set() -> None:
    """1.2.0 那一套**一字未改**，而且模块级常量就是它的字段（逻辑只有一份）。"""
    b = dllpatch.BUILD_120
    assert b.orig_sha256 == "2317e5e16493b8eb4838751f088d97619ab46334ca2d17579fe95e865089b8db"
    assert b.orig_sha256 == dllpatch.ORIG_SHA256
    assert b.base == dllpatch.BASE
    assert b.init_meta == dllpatch.INIT_META
    assert b.file_exists == dllpatch.FILE_EXISTS
    assert b.cave_len == dllpatch.CAVE_LEN == 138
    assert b.sites == dllpatch.SITES
    assert b.backup_name == "GameAssembly.dll.orig"
    assert b.backup_name == dllpatch.BACKUP_NAME
    # 老签名（不传 build）== 显式传 BUILD_120 —— 大量纯函数测试吃这一条
    orig = _fake_original()
    assert dllpatch.patched_image(orig) == dllpatch.patched_image(orig, b)
    for _name, _steal, ret, cave in b.sites:
        assert dllpatch.build_cave(cave, ret) == dllpatch.build_cave(cave, ret, b)


def test_build_121_pins_the_accepted_readings() -> None:
    """1.2.1 的读数钉在这里（源 `_work/patch121/table121.json`，验收尺子核过 34/34）。"""
    b = dllpatch.BUILD_121
    assert dllpatch.BUILDS == (
        dllpatch.BUILD_120,
        dllpatch.BUILD_121,
        dllpatch.BUILD_121P2,
        dllpatch.BUILD_121R,
        dllpatch.BUILD_1029,
    )
    assert b.label.startswith("1.2.1")
    assert b.orig_sha256 == "2412e79af4fed4613617fa7117e0e060f30800282f445507a7c18986868e3447"
    assert (b.base, b.init_meta, b.file_exists, b.object_new) == (
        0x180000000,
        0x1866BB6B0,
        0x1841BDA30,
        0x1866BB940,
    )
    assert (b.ctor, b.load_source, b.string_new, b.gchandle_new) == (
        0x1802746D0,
        0x180288580,
        0x1866EA5B0,
        0x1866E9B80,
    )
    assert b.slot_klass == 0x18871B798
    assert b.cave_len == 138
    assert b.sites == (
        ("Load()", 0x180289C91, 0x180289C96, 0x180289920),
        ("LoadCompiled()", 0x180289B1E, 0x180289B23, 0x180289F40),
    )
    assert b.backup_name == "GameAssembly.dll.2412e79a.orig"
    assert dllpatch.build_for_sha(b.orig_sha256) is b
    assert dllpatch.build_for_sha("00" * 32) is None


def test_build_121_release_shares_the_preview2_addresses() -> None:
    """1.2.1 正式版（2026-10-20）与 Preview2 只差 264 字节（全是 RIP 相对位移），
    本工具用到的 8 个目标函数 + 2 个插桩点 + 2 段 cave **逐字节相同**
    ⇒ 地址表原样沿用，只多一道 sha 门。

    判据来源：`_work/check121/patchsites.py`（新旧两份 DLL 全部 `same True`）。
    """
    r = dllpatch.BUILD_121R
    p = dllpatch.BUILD_121P2
    assert r.orig_sha256 == "da1668ac11bcdb036acc7807aec2aaf6f72e83921a05eaf0ae2255e7db005a26"
    assert r.label.startswith("1.2.1")
    assert (r.base, r.init_meta, r.file_exists, r.object_new, r.ctor) == (
        p.base, p.init_meta, p.file_exists, p.object_new, p.ctor,
    )
    assert (r.load_source, r.string_new, r.gchandle_new, r.slot_klass) == (
        p.load_source, p.string_new, p.gchandle_new, p.slot_klass,
    )
    assert r.sites == p.sites
    assert r.cave_len == p.cave_len == 138
    assert r.backup_name == "GameAssembly.dll.da1668ac.orig"
    assert dllpatch.build_for_sha(r.orig_sha256) is r


def test_build_1029_pins_the_accepted_readings() -> None:
    """1.2.1 2026-10-29 那版（Steam 更新，153,969,664 B / `12e70883…`）的读数钉在这里。

    源：`_work/patch1029/table1029.json`；同一把尺子核过 —— 正控 29/29（10-13 已知真值表）·
    负控 12/12（逐字段变异全部被抓）· 本表 34/34（含与 2026-10-20 那版的骨架同一性）。
    离线装配（纯内存）：`_work/patch1029/offline1029.py` —— 长度不变、变化 276 字节全落在
    4 个声明区间内、两个插桩点 e9 → cave 精确命中、cave 与 build_cave() 逐字节相同。
    """
    b = dllpatch.BUILD_1029
    assert b.label == "1.2.1 (2026-10-29)"
    assert b.orig_sha256 == "12e7088369539b029a7bf0064062ce030a53b5189e7067420903f47b4281fddf"
    assert (b.base, b.init_meta, b.file_exists, b.object_new) == (
        0x180000000,
        0x1866B0AE0,
        0x18421B400,
        0x1866B0D70,
    )
    assert (b.ctor, b.load_source, b.string_new, b.gchandle_new) == (
        0x18027CA80,
        0x180290D90,
        0x1866DF9E0,
        0x1866DEFB0,
    )
    assert b.slot_klass == 0x1886FBB60
    assert b.cave_len == 138
    assert b.sites == (
        ("Load()", 0x1802924A1, 0x1802924A6, 0x180292130),
        ("LoadCompiled()", 0x18029232E, 0x180292333, 0x180292750),
    )
    assert b.backup_name == "GameAssembly.dll.12e70883.orig"
    assert dllpatch.build_for_sha(b.orig_sha256) is b
def test_restore_accepts_an_older_patch_but_not_foreign_edits() -> None:
    """还原闸：**我们自己打的上一版补丁**要能还原；区间外被改过（例：游戏更新）必须拒。

    2026-10-27 的现场：cave 字面量从 `BA_Mod.zip` 改成 `BA_Mod\\BA_Mod.zip` ⇒ 旧补丁与
    `patched_image()` 算出来的不再逐字节相同。只认精确匹配的话 `restore()` 会把**自己的**
    旧补丁当成来路不明的 DLL 拒掉 —— 还原出口被锁死。这里用「把当前 patch 的字面量改成旧的」
    来复刻那一版（正控）。负控：区间外改 1 个字节 ⇒ 必须 False。
    """
    orig = _fake_original()
    build = dllpatch.BUILD_120
    current = dllpatch.patched_image(orig, build)
    assert dllpatch._only_patch_regions_differ(current, orig, build) is True

    # 正控 ①：模拟「上一版补丁」—— 字面量换成旧名字（仍在 cave 区间里）
    older = bytearray(current)
    lit_new = dllpatch.PACKAGE_REL_PATH.encode("ascii")
    lit_old = dllpatch.PACKAGE_NAME.encode("ascii")
    for _name, _steal, _ret, cave in build.sites:
        off = dllpatch._off(dllpatch._rva(cave, build))
        hit = bytes(older[off : off + build.cave_len]).find(lit_new)
        assert hit >= 0
        body = bytearray(older[off : off + build.cave_len])
        # 旧字面量短 7 字节 —— 用 NUL 补齐，**cave 长度必须不变**（否则差的是长度不是内容）
        pad = b"\x00" * (len(lit_new) - len(lit_old))
        assert pad
        body[hit : hit + len(lit_new)] = lit_old + pad
        older[off : off + build.cave_len] = body
    assert dllpatch._only_patch_regions_differ(bytes(older), orig, build) is True

    # 负控：区间外改 1 个字节 ⇒ 不许当成本工具的补丁
    foreign = bytearray(current)
    spans = dllpatch._patch_regions(build)
    spots = [i for i in range(0, len(orig)) if not any(a <= i < b for a, b in spans)]
    foreign[spots[0]] ^= 0xFF
    assert dllpatch._only_patch_regions_differ(bytes(foreign), orig, build) is False


def test_patched_image_follows_the_build_not_the_module_constants() -> None:
    """补丁按**那个 build 的地址表**打 —— 这就是「支持 1.2.1」的全部机理。"""
    orig = _fake_original()
    shifted = replace(
        dllpatch.BUILD_120,
        sites=tuple(
            (name, steal + 0x100, ret + 0x100, cave + 0x100)
            for name, steal, ret, cave in dllpatch.BUILD_120.sites
        ),
    )
    out = dllpatch.patched_image(orig, shifted)
    for _name, steal, _ret, _cave in dllpatch.BUILD_120.sites:
        o = dllpatch._off(dllpatch._rva(steal))
        assert out[o : o + 5] == orig[o : o + 5], "插桩点没跟着 build 走（还在用模块常量）"
    for _name, steal, _ret, _cave in shifted.sites:
        o = dllpatch._off(dllpatch._rva(steal, shifted))
        assert out[o] == 0xE9, "这个 build 的插桩点没被打上"


def test_status_reads_the_version_from_the_backup_content_not_its_name(
    tmp_path, monkeypatch
) -> None:
    """认版本靠**内容**：备份叫 `.orig` 还是 `<别的名字>.orig` 都一样。"""
    orig = _fake_original()
    fake = _pretend_original(monkeypatch, orig)
    (tmp_path / dllpatch.DLL_NAME).write_bytes(orig)
    (tmp_path / "GameAssembly.dll.deadbeef.orig").write_bytes(orig)  # 名字故意不是 `.orig`
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.NOT_INSTALLED
    assert st.build is fake
    assert st.backup.name == "GameAssembly.dll.deadbeef.orig"


def test_summary_lines_name_the_recognized_version(tmp_path, monkeypatch) -> None:
    orig = _fake_original()
    _pretend_original(monkeypatch, orig)
    _write_game(tmp_path)
    text = "\n".join(dllpatch.status(tmp_path).summary_lines())
    assert "认出来的版本" in text and "1.2.0" in text
    # 负控：认不出来时那一行必须说「没认出来」，不许静默省掉
    (tmp_path / dllpatch.BACKUP_NAME).unlink()
    (tmp_path / dllpatch.DLL_NAME).write_bytes(b"not the original")
    text2 = "\n".join(dllpatch.status(tmp_path).summary_lines())
    assert "没认出来" in text2


def test_apply_writes_the_backup_under_the_builds_own_name(tmp_path, monkeypatch) -> None:
    """备份落到 `build.backup_name`（不是写死 `.orig`），内容 = 打之前的原版。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig = _fake_original()
    (tmp_path / dllpatch.DLL_NAME).write_bytes(orig)
    fake = _pretend_original(monkeypatch, orig, backup_name="GameAssembly.dll.deadbeef.orig")
    msg = dllpatch.apply(tmp_path)
    assert (tmp_path / fake.backup_name).read_bytes() == orig
    assert not (tmp_path / dllpatch.BACKUP_NAME).is_file(), "不许再用历史遗留那个名字"
    assert fake.label in msg
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.PATCHED and st.build is fake


def test_apply_picks_the_build_by_sha_and_restores_that_version(tmp_path, monkeypatch) -> None:
    """两个版本同时在表里：按 sha256 选中 **1.2.1** 那一份，打/还原都围着它走。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig120 = _fake_original()
    bumped = bytearray(orig120)
    bumped[0x2000] ^= 0xFF
    orig121 = bytes(bumped)
    monkeypatch.setattr(
        dllpatch,
        "BUILDS",
        (
            replace(dllpatch.BUILD_120, orig_sha256=hashlib.sha256(orig120).hexdigest()),
            replace(dllpatch.BUILD_121, orig_sha256=hashlib.sha256(orig121).hexdigest()),
        ),
    )
    (tmp_path / dllpatch.DLL_NAME).write_bytes(orig121)
    dllpatch.apply(tmp_path)
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.PATCHED
    assert st.build is dllpatch.BUILDS[1]
    # 用的确实是 1.2.1 那张表：两个插桩点都落在 1.2.1 的位置上
    patched = (tmp_path / dllpatch.DLL_NAME).read_bytes()
    for _name, steal, _ret, _cave in dllpatch.BUILD_121.sites:
        assert patched[dllpatch._off(dllpatch._rva(steal, dllpatch.BUILD_121))] == 0xE9
    assert (tmp_path / dllpatch.BUILD_121.backup_name).read_bytes() == orig121
    dllpatch.restore(tmp_path)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == orig121
    assert dllpatch.status(tmp_path).state is dllpatch.PatchState.NOT_INSTALLED


def test_apply_refuses_unknown_original_even_with_force(tmp_path, monkeypatch) -> None:
    """**版本闸 force 也绕不过**：认不出版本一律拒绝，而且一个字节都不动。

    这正是「拿 1.2.0 的地址表打 1.2.1 的 DLL、11 个地址全指到别的函数上」那个口子。
    """
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    data = _fake_original()
    _write_game(tmp_path, original=data, backup=False)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.apply(tmp_path, force=True)
    assert "不是本工具已知的原版" in str(excinfo.value)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == data


def test_restore_refuses_an_older_backup_in_a_newer_game(tmp_path, monkeypatch) -> None:
    """⛔ **这条是今晚改动的全部意义**：游戏更新后，旧版本的备份还躺在旁边。

    老代码照着名字把旧备份盖上去 ⇒ **必崩**。新口径：不是 PATCHED 就拒绝，并指路 Steam 校验完整性。
    """
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig120 = _fake_original()
    bumped = bytearray(orig120)
    bumped[0x3000] ^= 0xFF
    orig121 = bytes(bumped)
    monkeypatch.setattr(
        dllpatch,
        "BUILDS",
        (
            replace(dllpatch.BUILD_120, orig_sha256=hashlib.sha256(orig120).hexdigest()),
            replace(dllpatch.BUILD_121, orig_sha256=hashlib.sha256(orig121).hexdigest()),
        ),
    )
    (tmp_path / dllpatch.DLL_NAME).write_bytes(orig121)  # 新版原版 DLL
    (tmp_path / dllpatch.BACKUP_NAME).write_bytes(orig120)  # 旧版备份（Steam 更新前留下的）
    st = dllpatch.status(tmp_path)
    assert st.state is dllpatch.PatchState.NOT_INSTALLED and st.build is dllpatch.BUILDS[1]
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.restore(tmp_path)
    assert "验证文件完整性" in str(excinfo.value)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == orig121, "旧版 DLL 被盖进新游戏了"


def test_restore_refuses_a_modified_dll_that_has_a_backup(tmp_path, monkeypatch) -> None:
    """MODIFIED + 有备份 ⇒ 拒绝（并指路 Steam 校验完整性），DLL 一个字节不动。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    orig = _fake_original()
    tampered = bytearray(orig)
    tampered[0x80] ^= 0x01
    _write_game(tmp_path, original=orig)
    (tmp_path / dllpatch.DLL_NAME).write_bytes(bytes(tampered))
    assert dllpatch.status(tmp_path).state is dllpatch.PatchState.MODIFIED
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.restore(tmp_path)
    assert "验证文件完整性" in str(excinfo.value)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == bytes(tampered)


# --------------------------------------------------------------------------- #
# 权限（2026-11-02）：写不进游戏目录必须是 UserFacingError，不许穿透成崩溃
# --------------------------------------------------------------------------- #


def _boom_copy(*args, **kwargs):
    raise PermissionError(13, "Access is denied", str(args[1] if len(args) > 1 else ""))


def test_apply_permission_error_on_backup_copy_is_user_facing(tmp_path, monkeypatch) -> None:
    """备份拷贝抛 PermissionError ⇒ UserFacingError，hint 指路「管理员 / 写权限」。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    _write_game(tmp_path, backup=False)
    fake = _pretend_original(monkeypatch, _fake_original())
    monkeypatch.setattr(dllpatch.shutil, "copy2", _boom_copy)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.apply(tmp_path)
    text = str(excinfo.value)
    assert "管理员" in text and "写权限" in text
    assert not (tmp_path / fake.backup_name).is_file()


def test_apply_permission_error_on_dll_write_is_user_facing(tmp_path, monkeypatch) -> None:
    """备份成功、写回 DLL 时抛 PermissionError ⇒ 也要 UserFacingError，DLL 原样不动。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    data = _fake_original()
    _write_game(tmp_path, original=data, backup=False)
    _pretend_original(monkeypatch, data)
    real_write = Path.write_bytes

    def boom(path, payload):
        if path.name == dllpatch.DLL_NAME:
            raise PermissionError(13, "Access is denied", str(path))
        return real_write(path, payload)

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.apply(tmp_path)
    assert "管理员" in str(excinfo.value)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == data


def test_apply_permission_precheck_blocks_before_any_write(tmp_path, monkeypatch) -> None:
    """可写性预检失败 ⇒ 直接 UserFacingError；**一个字节都不动**，连备份都不建。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    data = _fake_original()
    _write_game(tmp_path, original=data, backup=False)
    fake = _pretend_original(monkeypatch, data)

    def boom(*args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(dllpatch.tempfile, "mkstemp", boom)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.apply(tmp_path)
    assert "管理员" in str(excinfo.value)
    assert (tmp_path / dllpatch.DLL_NAME).read_bytes() == data
    assert not (tmp_path / fake.backup_name).is_file()


def test_restore_permission_error_is_user_facing(tmp_path, monkeypatch) -> None:
    """还原写回 DLL 时抛 PermissionError ⇒ 也要 UserFacingError（不是未捕获异常）。"""
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )
    _write_game(tmp_path)
    _pretend_original(monkeypatch, _fake_original())
    monkeypatch.setattr(dllpatch.shutil, "copy2", _boom_copy)
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.restore(tmp_path)
    assert "管理员" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 真 DLL（慢）
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_real_dll_status_is_readable() -> None:
    """真读游戏目录的 DLL 状态。**只读，不改任何文件。**"""
    from ba_mod_maker.core.assets import game as game_mod

    root = game_mod.find_game_root_for_db()
    if root is None:
        pytest.skip("这台机器上没有游戏")
    st = dllpatch.status(root)
    assert st.state is not dllpatch.PatchState.MISSING
    assert st.dll_size > 100_000_000
    # ★ 2026-10-18：原来断言「有备份 ⇒ 只能是 PATCHED / NOT_INSTALLED」—— 那条在
    #   **Steam 更新过游戏**的机器上必然红（实测 2026-10-17 11:30 更新后：DLL 换成新版，
    #   `.orig` 还是旧版 ⇒ 两边都对不上已知原版 ⇒ `MODIFIED`，这正是 `status()` 的定义）。
    #   所以判据改成两层：① 状态必须落在那几种合法值里；② **没认出游戏版本**时，
    #   摘要必须把「可能游戏更新过」这句话说出来（⛔ 不许让人以为是自己搞坏的）。
    assert st.state in (
        dllpatch.PatchState.PATCHED,
        dllpatch.PatchState.NOT_INSTALLED,
        dllpatch.PatchState.MODIFIED,
        dllpatch.PatchState.NO_BACKUP,
    )
    if st.has_backup and st.build is None:
        text = "\n".join(st.summary_lines())
        assert "游戏更新过" in text, text

# --------------------------------------------------------------------------- #
# 整段（字节串）改写：code_patches
# --------------------------------------------------------------------------- #

#: 1.2.1 那版 `EosOpenIdAuthService.Login()` 的 VA；测试里只要求它落在**假 DLL** 的范围内。
_EOS_LOGIN_VA = 0x180681B30
#: `mov dl,1; push rax; call rel32(B28); pop rax; ret`
_EOS_LOGIN_NEW = bytes.fromhex("b20150e8280b000058")


def _fake_with_code_patch(original: bytes, *, orig_bytes: bytes | None = None) -> dllpatch.Build:
    """造一份带 `code_patches` 的假 Build（地址表照抄 BUILD_120）。"""
    fake = replace(dllpatch.BUILD_120, orig_sha256=hashlib.sha256(original).hexdigest())
    off = dllpatch._off(dllpatch._rva(_EOS_LOGIN_VA, fake))
    want = original[off : off + len(_EOS_LOGIN_NEW)] if orig_bytes is None else orig_bytes
    return replace(fake, code_patches=(("EOS 登录放行（自建后端）", _EOS_LOGIN_VA, want, _EOS_LOGIN_NEW),))


def test_code_patches_write_the_whole_byte_string(monkeypatch, tmp_path):
    """9 字节整段写进去，且 `_patch_regions` / `_patched_regions` 认得出这一段。"""
    original = _fake_original()
    fake = _fake_with_code_patch(original)
    monkeypatch.setattr(dllpatch, "BUILDS", (fake,))
    off = dllpatch._off(dllpatch._rva(_EOS_LOGIN_VA, fake))

    assert (off, off + len(_EOS_LOGIN_NEW)) in dllpatch._patch_regions(fake)
    assert (off, _EOS_LOGIN_NEW) in dllpatch._patched_regions(fake)

    patched = dllpatch.patched_image(original, fake)
    assert patched[off : off + 9] == _EOS_LOGIN_NEW
    # 长度不变（PE 加载器会忽略尾部多余字节，静默失效最难查）
    assert len(patched) == len(original)


def test_code_patches_reject_when_original_bytes_differ(monkeypatch, tmp_path):
    """声明的原字节对不上 ⇒ 拒绝动手，且报错点名**第一个**差异的地址。"""
    original = _fake_original()
    fake = _fake_with_code_patch(original)
    off = dllpatch._off(dllpatch._rva(_EOS_LOGIN_VA, fake))
    wrong = bytearray(original[off : off + 9])
    wrong[3] ^= 0xFF
    bad = _fake_with_code_patch(original, orig_bytes=bytes(wrong))
    with pytest.raises(UserFacingError) as excinfo:
        dllpatch.patched_image(original, bad)
    assert f"{_EOS_LOGIN_VA + 3:#x}" in str(excinfo.value)


def test_code_patches_round_trip_through_apply_and_restore(monkeypatch, tmp_path):
    """apply → verify → restore 一整圈：还原后与原文逐字节相同。"""
    original = _fake_original()
    fake = _fake_with_code_patch(original)
    monkeypatch.setattr(dllpatch, "BUILDS", (fake,))
    _write_game(tmp_path, original=original, backup=False)
    off = dllpatch._off(dllpatch._rva(_EOS_LOGIN_VA, fake))

    dllpatch.apply(tmp_path)
    dll = tmp_path / dllpatch.DLL_NAME
    assert dll.read_bytes()[off : off + 9] == _EOS_LOGIN_NEW
    ok, _msg = dllpatch.verify(tmp_path)
    assert ok is True

    dllpatch.restore(tmp_path)
    assert dll.read_bytes() == original

