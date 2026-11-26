r"""`external_db.remove()` 的判据（卸载：**删文件** ＋ 留可还原条目）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_external_db_remove.py -n 0 -q
      -> 6 passed

## 为什么每条都要有负控

卸载是**不可逆**的（对用户而言），所以「干跑不写盘」「出了游戏根就拒」「写后复核」
三条都配了能红的尺子 —— 先喂一个已知违规的点进去，看它报不报（AGENTS §0.9 动作 2）。

⚠ 用例**不碰真游戏目录**：`_fake_game()` 在 `tmp_path` 下造一个只有 `BrokenArrow_Data`
的假游戏根。⛔ 谁改成指向 `D:\Steam\...` 谁负责。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import external_db
from ba_mod_maker.core.errors import AssetError
from ba_mod_maker.core.session import PACKAGE_DIR, PACKAGE_NAME

#: 卸掉之后那个文件在 `removed/` 里的**扁平名**（`_keep_path` 把分隔符换成 `__`）。
KEPT_ZIP = "__".join((PACKAGE_DIR, PACKAGE_NAME))


def _fake_game(tmp_path: Path) -> Path:
    """假游戏根：只要有 `BrokenArrow_Data` 就行（`remove()` 只查 root 在不在）。"""
    root = tmp_path / "game"
    (root / "BrokenArrow_Data").mkdir(parents=True)
    return root


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    """★ 干跑：源一个字节不动，连 `removed/` 都不建。"""
    root = _fake_game(tmp_path)
    zip_path = external_db.game_zip_path(root)
    external_db.ensure_zip_dir(root)
    zip_path.write_bytes(b"x" * 1024)
    before = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    project = tmp_path / "proj"
    report = external_db.remove(root, project_root=project, dry_run=True, check_game_running=False)
    assert zip_path.is_file()
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == before
    assert not project.exists(), "干跑居然建了 removed/"
    assert report.files and not report.files[0].keep.exists()
    assert not report.uninstalled


def test_remove_moves_the_file_and_writes_the_record(tmp_path: Path) -> None:
    """★ 真卸：原位置没了、搬过去那份逐字节一致、台账记下了 sha256，且**搬回去就还原**。"""
    root = _fake_game(tmp_path)
    zip_path = external_db.game_zip_path(root)
    payload = bytes(range(256)) * 8
    external_db.ensure_zip_dir(root)
    zip_path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    project = tmp_path / "proj"
    report = external_db.remove(root, project_root=project, check_game_running=False)
    assert not zip_path.exists(), "原位置还在"
    assert report.uninstalled
    kept = report.files[0].keep
    assert kept.is_file() and hashlib.sha256(kept.read_bytes()).hexdigest() == sha
    doc = json.loads((external_db.removed_root(project) / "removed.json").read_text("utf-8"))
    assert doc["kind"] == "external-db-removed"
    assert doc["files"][0]["sha256"] == sha
    assert doc["files"][0]["bytes"] == len(payload)
    kept.replace(zip_path)  # 还原 = 搬回去
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == sha


def test_removing_twice_is_idempotent(tmp_path: Path) -> None:
    """★ 幂等：已经没了再卸一次不报错（报告里进 `absent`，不是失败）。"""
    root = _fake_game(tmp_path)
    project = tmp_path / "proj"
    first = external_db.remove(root, project_root=project, check_game_running=False)
    assert first.files == () and first.absent, "本来就没有的应当进 absent"
    second = external_db.remove(root, project_root=project, check_game_running=False)
    assert not second.uninstalled
    assert second.lines() and "本来就没有" in " ".join(second.lines())


def test_extra_file_outside_the_game_root_is_refused(tmp_path: Path) -> None:
    """★ 负控：`--file` 出了游戏根就拒（别让它变成「随手删任意文件」的入口）；正控见下。"""
    root = _fake_game(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(AssetError) as exc:
        external_db.remove(
            root, project_root=tmp_path / "proj", extra_files=[outside], check_game_running=False
        )
    assert "不在游戏根里" in str(exc.value)
    assert outside.is_file(), "拒绝的时候不许动它"
    # ---- 正控：同一份内容放进游戏根里就必须能卸（证明上面拒的是**位置**，不是别的）----
    inside = root / "ok.bin"
    inside.write_bytes(b"payload")
    report = external_db.remove(
        root, project_root=tmp_path / "proj", extra_files=["ok.bin"], check_game_running=False
    )
    assert not inside.exists()
    assert any(item.path == inside.resolve() for item in report.files)


def test_the_after_write_check_can_go_red(tmp_path: Path, monkeypatch) -> None:
    """★ 正控：写后复核那把尺子**能红** —— 让「搬过去那份的 sha256」读出来是错的。"""
    root = _fake_game(tmp_path)
    external_db.ensure_zip_dir(root)
    external_db.game_zip_path(root).write_bytes(b"payload")
    real = external_db.sha256_of
    monkeypatch.setattr(external_db, "sha256_of", lambda p: "deadbeef" + real(p)[8:])
    with pytest.raises(AssetError) as exc:
        external_db.remove(root, project_root=tmp_path / "proj", check_game_running=False)
    assert "写后复核不通过" in str(exc.value)


def test_game_running_refuses_even_on_dry_run(tmp_path: Path, monkeypatch) -> None:
    """★ 红线 1：游戏在跑就拒，**干跑也拒** —— 否则那份判据描述的是一次注定失败的写。"""
    root = _fake_game(tmp_path)
    zip_path = external_db.game_zip_path(root)
    external_db.ensure_zip_dir(root)
    zip_path.write_bytes(b"payload")
    monkeypatch.setattr("ba_mod_maker.core.assets.game.is_game_running", lambda: True)
    with pytest.raises(AssetError) as exc:
        external_db.remove(root, project_root=tmp_path / "proj", dry_run=True)
    assert "游戏正在运行" in str(exc.value)
    assert zip_path.is_file(), "拒了却还是把文件搬走了"


# --------------------------------------------------------------------------- #
# unremove：按台账搬回去（remove 的逆操作）—— ⛔ 与 restore（补丁柜）不是一回事
# --------------------------------------------------------------------------- #


def _round_trip(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """装一个假游戏根 + 一份文件，卸掉；返回 (root, 原路径, 原始字节)。"""
    root = _fake_game(tmp_path)
    zip_path = external_db.game_zip_path(root)
    payload = bytes(range(256)) * 4
    external_db.ensure_zip_dir(root)
    zip_path.write_bytes(payload)
    external_db.remove(root, project_root=tmp_path / "proj", check_game_running=False)
    return root, zip_path, payload


def test_unremove_puts_it_back_byte_for_byte(tmp_path: Path) -> None:
    """★ 逆操作：搬回去之后**逐字节相同**，台账里那一条被划掉。"""
    root, zip_path, payload = _round_trip(tmp_path)
    assert not zip_path.exists()
    report = external_db.unremove(tmp_path / "proj", check_game_running=False)
    assert report.ok, report.problems
    assert zip_path.is_file()
    assert zip_path.read_bytes() == payload, "搬回来的不是原来那份"
    assert len(report.restored) == 1
    doc = json.loads(
        (external_db.removed_root(tmp_path / "proj") / "removed.json").read_text("utf-8")
    )
    assert doc["files"] == [], "搬回去了却还留在台账里"
    # 幂等：再搬一次不报错、也没有东西可搬
    again = external_db.unremove(tmp_path / "proj", check_game_running=False)
    assert again.ok and not again.restored


def test_unremove_dry_run_changes_nothing(tmp_path: Path) -> None:
    """★ 干跑：来源那份不动、原位置还是空的、台账也没改。"""
    root, zip_path, _ = _round_trip(tmp_path)
    kept = external_db.removed_root(tmp_path / "proj") / KEPT_ZIP
    report = external_db.unremove(tmp_path / "proj", dry_run=True, check_game_running=False)
    assert report.restored and not zip_path.exists()
    assert kept.is_file(), "干跑居然把台账里那份搬走了"
    doc = json.loads(
        (external_db.removed_root(tmp_path / "proj") / "removed.json").read_text("utf-8")
    )
    assert len(doc["files"]) == 1


def test_unremove_never_overwrites_something_else(tmp_path: Path) -> None:
    """★ 负控：原位置已经有**不一样**的东西 ⇒ 拒绝（⛔ 不覆盖用户后来放进去的）。"""
    root, zip_path, payload = _round_trip(tmp_path)
    external_db.ensure_zip_dir(root)
    zip_path.write_bytes(b"someone else put this here")
    report = external_db.unremove(tmp_path / "proj", check_game_running=False)
    assert not report.ok
    assert any("拒绝覆盖" in text for text in report.problems)
    assert zip_path.read_bytes() == b"someone else put this here"
    # 正控：把那份换回**逐字节相同**的 ⇒ 应当报「已经在原位上」并跳过，不算错
    external_db.ensure_zip_dir(root)
    zip_path.write_bytes(payload)
    ok = external_db.unremove(tmp_path / "proj", check_game_running=False)
    assert ok.ok
    assert ok.already and not ok.restored


def test_unremove_refuses_when_the_kept_copy_was_tampered(tmp_path: Path) -> None:
    """★ 负控：台账说 sha 是多少，盘上那份就得是多少 —— 被改过一个字节就**拒绝搬**。"""
    root, zip_path, _ = _round_trip(tmp_path)
    kept = external_db.removed_root(tmp_path / "proj") / KEPT_ZIP
    kept.write_bytes(kept.read_bytes() + b"tampered")
    report = external_db.unremove(tmp_path / "proj", check_game_running=False)
    assert not report.ok
    assert any("拒绝搬" in text for text in report.problems)
    assert not zip_path.exists(), "拒绝了却还是搬过去了"


def test_unremove_can_pick_one_file_by_basename(tmp_path: Path) -> None:
    """★ `--file` 给基名也认（用户手上不一定有完整路径），且只搬点名的那一件。"""
    root = _fake_game(tmp_path)
    (root / "a.bin").write_bytes(b"aaa")
    (root / "b.bin").write_bytes(b"bbb")
    external_db.remove(
        root,
        project_root=tmp_path / "proj",
        extra_files=["a.bin", "b.bin"],
        check_game_running=False,
    )
    report = external_db.unremove(tmp_path / "proj", files=["a.bin"], check_game_running=False)
    assert [item.path.name for item in report.restored] == ["a.bin"]
    assert (root / "a.bin").is_file() and not (root / "b.bin").exists()


def test_unremove_without_a_ledger_is_not_an_error(tmp_path: Path) -> None:
    """★ 没卸过 ⇒ 台账不存在，**不是错**（「撤销」点几次都该一样）。"""
    _fake_game(tmp_path)
    report = external_db.unremove(tmp_path / "proj", check_game_running=False)
    assert report.ok and not report.restored
