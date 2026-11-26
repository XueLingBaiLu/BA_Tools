"""`EditSession` 的**资产**那一半（ADR 0008 实施顺序第 2 步）。

判据 J1（`docs/设计-资产编辑入口.md` §6）的一句话口径：

    EditSession.set_asset_field(改材质槽 PPtr) -> save()
    资产账本计数 **0 → 1**，且 save() **不再**命中「没有改动，未写盘」那条早退

**负控（必须报红的那一半）**：不加条目时 `save()` **必须仍早退**，而且**一个文件都不许建**。
尺子不能红等于没有（AGENTS §0.9 动作 2）—— 所以下面 `test_save_...` 那三条是成对的。

不碰 Qt 事件循环、不碰真实游戏文件：`Instance` 是内存里造的，Δ 计划写在 `tmp_path`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ba_mod_maker.app.shell.edit_session import PROJECT_ROOT_ENV, EditSession
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.asset_changeset import MISSING
from ba_mod_maker.core.edit.asset_plan_io import load_plan_document, plan_path
from ba_mod_maker.core.edit.asset_refs import AssetRef, FieldPath
from ba_mod_maker.core.errors import AssetError

#: 真件里那个材质槽的落点（真机跑过的那 8 字节的同一条链，见 asset_kernel/skin_install.py）。
BUNDLE = Path("aa/PC/units.bundle")
SLOT = FieldPath("m_TexEnvs[Layer_A97CDC25].second.m_PathID")

#: 改一处要写的那个值（新贴图的 pathID）。
OLD_TEXTURE, NEW_TEXTURE = 58076, 58077


@pytest.fixture(autouse=True)
def _no_project_root_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """用例永远不许往**真实工程目录**写 patches/：把环境接缝摘掉，只用 set_project_root()。"""
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)


class _Baseline:
    """假基线：一份内存字典 ＋ **调用计数器**（尺子就是那个计数器）。"""

    def __init__(self, values: dict[tuple, Any] | None = None) -> None:
        self.values: dict[tuple, Any] = dict(values or {})
        self.calls = 0

    def __call__(self, ref: AssetRef) -> Any:
        self.calls += 1
        return self.values.get(ref.key(), MISSING)


def _instance() -> Instance:
    """一份够用的空实例：save() 只要求 _instance / _changeset 在（不读任何表）。"""
    return Instance(path_id="52085", directory=Path("."), schema={}, manifest={})


def _ref() -> AssetRef:
    return AssetRef(container=BUNDLE, path_id=58076, field=SLOT)


def _session(tmp_path: Path) -> EditSession:
    session = EditSession()
    session.set_instance(_instance())
    session.set_project_root(tmp_path)
    return session


# --------------------------------------------------------------------------- #
# 接缝：账本计数 0 -> 1（正控那一半）
# --------------------------------------------------------------------------- #
def test_an_asset_edit_moves_the_asset_ledger_and_not_the_table_one(tmp_path: Path) -> None:
    """判据 J1 的前半：**资产账本 0 → 1**，而表账本纹丝不动。"""
    ref = _ref()
    session = _session(tmp_path)
    session.set_asset_baseline(_Baseline({ref.key(): OLD_TEXTURE}))
    assert (session.change_count, session.asset_change_count) == (0, 0)

    session.set_asset_field(ref, NEW_TEXTURE)

    assert session.asset_change_count == 1, "资产条目没有记进账本"
    assert session.change_count == 0, "资产改动不该动表账本（ADR 0008 决定 3：两本账）"
    assert session.total_change_count == 1, "顶栏胶囊那个数要看得见资产条目"
    # 账本里记的**旧值**来自基线，且改一格不落盘。
    assert session.asset_changeset is not None
    assert session.asset_changeset.old_value(ref) == OLD_TEXTURE


def test_revert_assets_empties_only_the_asset_ledger(tmp_path: Path) -> None:
    ref = _ref()
    session = _session(tmp_path)
    session.set_asset_baseline(_Baseline({ref.key(): OLD_TEXTURE}))
    session.set_asset_field(ref, NEW_TEXTURE)

    assert session.revert_assets() == 1

    assert session.asset_change_count == 0
    assert session.is_empty


def test_an_asset_verb_without_a_baseline_is_refused(tmp_path: Path) -> None:
    """⛔ 没接基线就不许记改动：`old` 只能从基线来，编一个出来比报错糟得多。"""
    session = _session(tmp_path)

    with pytest.raises(AssetError, match="基线"):
        session.set_asset_field(_ref(), NEW_TEXTURE)


# --------------------------------------------------------------------------- #
# 判据 J1：一条资产改动**进得了** save()（正控 + 负控成对）
# --------------------------------------------------------------------------- #
def test_save_no_longer_early_returns_when_only_an_asset_changed(tmp_path: Path) -> None:
    """**正控**：加一条资产条目后，save() 走的是第二条产物那条路。"""
    ref = _ref()
    session = _session(tmp_path)
    session.set_asset_baseline(_Baseline({ref.key(): OLD_TEXTURE}))
    session.set_asset_field(ref, NEW_TEXTURE)

    sentence = session.save()

    assert sentence != "没有改动，未写盘", "资产条目还是被早退挡住了（判据 J1 没过）"
    assert "资产 Δ 计划" in sentence, sentence
    target = plan_path(tmp_path)
    assert target.is_file(), f"第二条产物没落地：{target}"
    document = load_plan_document(target)
    assert document["count"] == 1
    assert document["edits"][0]["kind"] == "set"
    assert document["edits"][0]["old"] == OLD_TEXTURE
    assert document["edits"][0]["new"] == NEW_TEXTURE
    # ⛔ 只改资产时**一个字节的包都不许写**（AGENTS §0.7.1：别替看不见的东西干活）。
    assert [p.name for p in tmp_path.iterdir()] == ["patches"]


def test_save_without_the_entry_still_early_returns(tmp_path: Path) -> None:
    """**负控**：不加条目时 save() 必须**仍早退**，而且**一个文件都不建**。

    这一条是上面那条尺子的正控 —— 它若绿不起来，「save() 不再早退」就没有证明力。
    """
    session = _session(tmp_path)
    session.set_asset_baseline(_Baseline())

    assert session.save() == "没有改动，未写盘"
    assert list(tmp_path.iterdir()) == [], "没有改动却建了文件"


def test_the_second_save_without_new_edits_writes_nothing(tmp_path: Path) -> None:
    """AGENTS §4.5：**没有新改动再点一次保存，一次盘都不写**（资产那一半也一样）。"""
    ref = _ref()
    session = _session(tmp_path)
    session.set_asset_baseline(_Baseline({ref.key(): OLD_TEXTURE}))
    session.set_asset_field(ref, NEW_TEXTURE)
    session.save()
    target = plan_path(tmp_path)
    stamp = target.stat().st_mtime_ns

    assert session.save() == "没有改动，未写盘"

    assert target.stat().st_mtime_ns == stamp, "计划被重写了一遍（commit 没生效）"
    assert session.asset_change_count == 0


def test_a_failed_plan_write_is_reported_and_keeps_the_ledger(tmp_path: Path) -> None:
    """写不出去要**如实报**，而且账本**不清**（失败保留改动让用户重试）。

    做法：把落点指到一个**是文件而不是目录**的路径上 —— mkdir 必失败。
    """
    ref = _ref()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    session = _session(blocker)
    session.set_asset_baseline(_Baseline({ref.key(): OLD_TEXTURE}))
    session.set_asset_field(ref, NEW_TEXTURE)

    seen: list[str] = []
    session.failed.connect(seen.append)
    sentence = session.save()

    assert sentence != "没有改动，未写盘"
    assert "资产 Δ 计划" not in sentence, sentence
    assert seen and seen[-1] == sentence, "错误没有发 failed 信号（AGENTS §5）"
    assert session.asset_change_count == 1, "写失败了却把账本清了 —— 用户没法重试"
