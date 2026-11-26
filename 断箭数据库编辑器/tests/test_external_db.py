"""外部库通道（`<游戏根>/BA_Mod.zip`）的安装 / 还原判据 —— ADR 0005 + ADR 0007。

**判据一行**：`.venv\\Scripts\\python.exe -m pytest tests/test_external_db.py -q`
→ 通过用例数 **从 0（本文件不存在）变 11 passed**。

⛔ 全部用例都在 `tmp_path` 造的**假游戏根**上跑 —— **一个字节都不写 `D:\\Steam\\...`**：
`game_root` 是显式参数，`external_db` 自己不找游戏根（这正是它好测的原因）。

| 用例 | 判据 |
|---|---|
| 装（原本没有 zip）| 内容 == 源包；还原 = 那个文件被删 |
| 装（原本已有别人的 zip）| 还原后逐位 == 原来那份 |
| **正控**：装完手改 1 字节 | 必须抛 + 文件逐位不动 + 补丁还在 |
| 幂等 | 第二次还原、以及从没装过就撤，都不报错 |
| 干跑 | 游戏根 / 工程目录都没多出任何东西 |
| 再装覆盖它 | 装 A → 装 B → 还原 == **最初**那份 |
| 游戏在跑 | 抛「游戏正在运行」，装与撤都拦 |
| 写盘失败 | 用补丁撤回 + 报告里记撤销结果 + 不留 .tmp |
| 不是外部库的文件 | 抛 DataSourceError，盘上没动 |
| status 只读 | 不在 / 0 / 空 sha；补丁那条要给了工程目录才报 |
| CLI 契约 | 两个报告对象都有 `.lines() -> tuple[str, ...]`，错误一律 AssetError |

⚠ 正控（第 3 条）为什么算正控：同一个 `restore` 在**没动过文件**时是绿的（第 1、2 条），
手改 1 个字节后必须抛 —— 抛不出来就说明「还原前的 sha 锁」是假的（AGENTS §0.9 动作 2）。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import NamedTuple

import pytest

from ba_mod_maker.asset_kernel.reverse_delta import RestoreReport
from ba_mod_maker.core.asset import dbzip, external_db
from ba_mod_maker.core.errors import AssetError
from ba_mod_maker.core.session import PACKAGE_DIR, PACKAGE_NAME


class Scene(NamedTuple):
    """一套**假现场**：临时根 + 假游戏根 + 工程目录 + 一份要装的包。"""

    root: Path
    game: Path
    project: Path
    source: Path


@pytest.fixture()
def no_game_running(monkeypatch):
    """把「游戏在跑吗」钉成 False —— 与 tests/test_dllpatch.py 同一姿势。

    不钉的话，用户**正好开着游戏**跑这套用例就会红，而那红跟被测逻辑无关。
    """
    monkeypatch.setattr(
        "ba_mod_maker.core.assets.game.is_game_running", lambda: False, raising=True
    )


def make_package(path: Path, *, units_json: bytes) -> Path:
    """造一份**认得出来**的外部库包（至少一张已知表），返回它的路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    dbzip.DbZip(tables={"Units": units_json}).write(path)
    return path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def zip_target(game: Path, *, make_dir: bool = False) -> Path:
    """落点 = `<游戏根>/BA_Mod/BA_Mod.zip`（2026-10-27 起）。

    ⚠ **用常量拼、不调 `game_zip_path()`** —— 拿被测函数当期望值，用例就永远绿了。
    `make_dir=True` 只给「要往落点写字节」的用例用（真实写盘前产品也会造这个目录）。
    """
    path = game / PACKAGE_DIR / PACKAGE_NAME
    if make_dir:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def scene(tmp_path) -> Scene:
    game = tmp_path / "game"
    game.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    source = make_package(tmp_path / "export" / PACKAGE_NAME, units_json=b'[{"Id": 42}]')
    return Scene(root=tmp_path, game=game, project=project, source=source)


def test_install_into_empty_root_then_restore_deletes_it(scene: Scene, no_game_running) -> None:
    """原本没有 zip：装完内容 == 源包，还原 = 那个文件被删。"""
    target = zip_target(scene.game, make_dir=True)
    assert not target.exists()

    rep = external_db.install(scene.game, scene.source, project_root=scene.project)
    assert rep.action == "create"
    assert rep.installed
    assert (rep.existed_before, rep.size_before, rep.sha_before) == (False, 0, "")
    assert rep.sha_after == sha256_bytes(scene.source.read_bytes())
    assert target.read_bytes() == scene.source.read_bytes()
    assert rep.patch_path is not None and rep.patch_path.is_file()
    assert rep.patch_bytes > 0
    assert external_db.status(scene.game, project_root=scene.project).patch_matches_disk is True

    out = external_db.restore(scene.project)
    assert out.ok
    assert not target.exists()  # 装前不存在 ⇒ 还原就是删掉它（删文件即卸载）
    assert not external_db.status(scene.game, project_root=scene.project).has_patch


def test_install_over_someone_elses_zip_restores_it_bitwise(scene: Scene, no_game_running) -> None:
    """原本已有别人的 zip：还原后必须**逐位** == 原来那份。"""
    target = zip_target(scene.game, make_dir=True)
    someone = make_package(scene.root / "someone.zip", units_json=b'[{"Id": 7}]').read_bytes()
    target.write_bytes(someone)

    rep = external_db.install(scene.game, scene.source, project_root=scene.project)
    assert rep.action == "replace"
    assert (rep.size_before, rep.sha_before) == (len(someone), sha256_bytes(someone))
    assert target.read_bytes() == scene.source.read_bytes()

    out = external_db.restore(scene.project, game_root=scene.game)  # 顺带走一遍同盘守卫那条参数
    assert out.ok
    assert target.read_bytes() == someone
    assert sha256_bytes(target.read_bytes()) == sha256_bytes(someone)


def test_restore_refuses_after_one_byte_edit(scene: Scene, no_game_running) -> None:
    """**正控**：装完手改 1 个字节 ⇒ 还原必须拒绝，且文件一个字节都不动。

    这个量法能红才算尺子（AGENTS §0.9 动作 2）：同一个 `restore` 在文件没被动过时是绿的
    （上一条用例），这里改成 1 个字节就必须抛。
    """
    target = zip_target(scene.game, make_dir=True)
    external_db.install(scene.game, scene.source, project_root=scene.project)
    data = bytearray(target.read_bytes())
    data[-1] ^= 0x01
    target.write_bytes(bytes(data))
    edited = target.read_bytes()

    with pytest.raises(AssetError) as excinfo:
        external_db.restore(scene.project)
    msg = str(excinfo.value)
    assert "sha256 与补丁的「装后」不符" in msg  # 报的就是 sha 不符
    assert "拒绝" in msg
    assert target.read_bytes() == edited  # 一个字节都没动
    assert external_db.status(scene.game, project_root=scene.project).has_patch  # 补丁还在


def test_restore_twice_is_idempotent(scene: Scene, no_game_running) -> None:
    """还原两次不报错；从没装过就撤也不报错。"""
    assert external_db.restore(scene.project).ok  # 没有补丁 ⇒ 「无需还原」，不是错误

    target = zip_target(scene.game, make_dir=True)
    external_db.install(scene.game, scene.source, project_root=scene.project)
    first = external_db.restore(scene.project)
    assert first.ok and not target.exists()
    assert not first.entries[0].get("skipped")  # 这次是真撤了（不是「本来就不用撤」）

    second = external_db.restore(scene.project)  # ★ 还原两次
    assert second.ok
    assert second.entries[0]["skipped"] == "no_patch"


def test_dry_run_writes_nothing(scene: Scene, no_game_running) -> None:
    """干跑必须给得出「判据一行」，而且**一个字节都不写**。"""
    target = zip_target(scene.game, make_dir=True)
    rep = external_db.install(scene.game, scene.source, project_root=scene.project, dry_run=True)
    assert rep.dry_run and not rep.installed
    assert rep.action == "create"
    assert rep.sha_after == sha256_bytes(scene.source.read_bytes())
    assert rep.patch_estimated and rep.patch_bytes > 0
    line = rep.criteria_line()
    assert str(target) in line
    assert "装前 不存在" in line and rep.sha_after[:16] in line
    assert not target.exists()  # 没写文件
    assert not (scene.project / "patches").exists()  # 连补丁柜都没建

    someone = make_package(scene.root / "someone.zip", units_json=b'[{"Id": 9}]').read_bytes()
    target.write_bytes(someone)
    rep2 = external_db.install(scene.game, scene.source, project_root=scene.project, dry_run=True)
    assert rep2.action == "replace"
    assert (rep2.size_before, rep2.sha_before) == (len(someone), sha256_bytes(someone))
    assert target.read_bytes() == someone  # 别人的那份没被动


def test_reinstall_merges_back_to_the_first_baseline(scene: Scene, no_game_running) -> None:
    """「再装覆盖它」= 合并：基线永远是**用户装之前那一刻**，不是第一次装完的样子。"""
    target = zip_target(scene.game, make_dir=True)
    first = make_package(scene.root / "first.zip", units_json=b'[{"Id": 1}]').read_bytes()
    target.write_bytes(first)
    pkg_a = make_package(scene.root / "a.zip", units_json=b'[{"Id": 2}]')
    pkg_b = make_package(scene.root / "b.zip", units_json=b'[{"Id": 3}]')

    external_db.install(scene.game, pkg_a, project_root=scene.project)
    assert target.read_bytes() == pkg_a.read_bytes()
    rep2 = external_db.install(scene.game, pkg_b, project_root=scene.project)
    assert rep2.installed
    assert rep2.sha_after == sha256_bytes(pkg_b.read_bytes())
    assert target.read_bytes() == pkg_b.read_bytes()
    # 合并之后补丁的「装后」必须指向**这次**装出来的字节，否则还原会被自己的锁拒绝
    assert external_db.status(scene.game, project_root=scene.project).patch_matches_disk is True

    external_db.restore(scene.project)
    assert target.read_bytes() == first  # ★ 回到最初那份，不是 pkg_a


def test_install_refuses_while_game_running(scene: Scene, monkeypatch) -> None:
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: True, raising=True)
    with pytest.raises(AssetError) as excinfo:
        external_db.install(scene.game, scene.source, project_root=scene.project)
    assert "游戏正在运行" in str(excinfo.value)
    assert not zip_target(scene.game).exists()
    assert not (scene.project / "patches").exists()

    with pytest.raises(AssetError) as excinfo2:
        external_db.restore(scene.project)
    assert "游戏正在运行" in str(excinfo2.value)


def test_install_refuses_a_file_that_is_not_a_package(scene: Scene, no_game_running) -> None:
    """按**内容**验一遍：不认识的文件不许写进游戏根。"""
    junk = scene.root / "junk.zip"
    junk.write_bytes(b"this is not a zip at all")
    with pytest.raises(AssetError):  # CLI 只 catch 这一族，别的异常它不会打 hint
        external_db.install(scene.game, junk, project_root=scene.project)
    assert not zip_target(scene.game).exists()
    assert not (scene.project / "patches").exists()


def test_write_failure_rolls_back_with_the_patch(
    scene: Scene, no_game_running, monkeypatch
) -> None:
    """写盘失败：必须**用补丁撤回**、把撤销结果写进报告、并且不留半成品（Lead 定的口径）。

    注入点：只让「替换落点」那一次 `os.replace` 抛（别的调用原样委托给真的那个），
    模拟 WinError 5「有程序占着这个文件」。
    """
    target = zip_target(scene.game, make_dir=True)
    real_replace = os.replace

    def boom(src, dst, *args, **kwargs):
        if Path(dst) == target:
            raise PermissionError(5, "拒绝访问")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(external_db.os, "replace", boom)
    with pytest.raises(AssetError) as excinfo:
        external_db.install(scene.game, scene.source, project_root=scene.project)
    msg = str(excinfo.value)
    assert f"装 {PACKAGE_NAME} 失败" in msg
    assert "已用反向 Δ 撤回装前状态" in msg  # 撤销结果跟着原错一起抛出来，没被吞

    report = excinfo.value.context["report"]  # 报告也跟着抛出来了
    assert report.rollback_ok is True
    assert report.patch_path is None  # 撤回成功 = 补丁已删
    assert not target.exists()  # 落点回到「装前不存在」
    assert not zip_target(scene.game).with_name(PACKAGE_NAME + ".tmp").exists()  # 不留临时文件
    assert not external_db.status(scene.game, project_root=scene.project).has_patch


def test_cli_contract_shape(scene: Scene, no_game_running) -> None:
    """CLI 要用的那几样（Lead 钉死的契约，别悄悄改）。

    `status / install` 的返回值必须有 `.lines() -> tuple[str, ...]`；
    `install` 还要有 `.dry_run / .zip_path / .sha_before / .sha_after / .patch_bytes`；
    `restore` 直接返回 `reverse_delta.RestoreReport`；错误一律 `core.errors.AssetError`。
    """
    st = external_db.status(scene.game, project_root=scene.project)
    assert isinstance(st.lines(), tuple)
    assert all(isinstance(line, str) for line in st.lines())

    dry = external_db.install(scene.game, scene.source, project_root=scene.project, dry_run=True)
    assert isinstance(dry.lines(), tuple)
    assert dry.dry_run is True
    assert isinstance(dry.zip_path, Path) and dry.zip_path == zip_target(scene.game)
    assert dry.sha_before == "" and len(dry.sha_after) == 64
    assert isinstance(dry.patch_bytes, int) and dry.patch_bytes > 0

    rep = external_db.install(scene.game, scene.source, project_root=scene.project)
    assert isinstance(rep.lines(), tuple) and rep.verified is True
    assert rep.sha_after == dry.sha_after and rep.patch_bytes > 0

    out = external_db.restore(scene.project)
    assert isinstance(out, RestoreReport)
    assert isinstance(out.lines(), tuple)


def test_status_is_read_only_and_reports_absence(scene: Scene) -> None:
    st = external_db.status(scene.game)
    assert not st.present and st.size == 0 and st.sha256 == ""
    assert st.patch_dir is None and not st.has_patch
    assert isinstance(st.lines(), tuple)
    assert "没有" in st.lines()[1]
    assert external_db.game_zip_path(scene.game) == zip_target(scene.game)
