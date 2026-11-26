r"""\`ba-mod-maker check-model\` 的判据（装机前用产品自己的三把尺子检查一个包）。

**判据一行**：pytest tests/test_cli_check_model.py -q -n 0 -> 通过数 0 -> N，其中 **2 条是负控**。

| # | 喂什么 | 必须 |
|---|---|---|
| 正控 | 我们那个从零装配的包（出厂闸全绿） | rc 0 + 输出里有 ①②③ |
| 正控 2 | 同上 ＋ \`--key\`（已装进 catalog） | rc 0 + 输出里有 ④ 解析链 |
| 负控 1 | 不存在的包路径 | rc **2**（用不了） |
| 负控 2 | 一个**带悬空 PPtr** 的包 | rc **1** + 输出里有「悬空」 |
| 负控 3 | `--spec` **两个来源都不给 / 都给** | rc **2** +「正好给一个」（⛔ 不替你挑） |
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone, prefab_build
from ba_mod_maker.cli.__main__ import main

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
ZERO = WORK / "route-b" / "out" / "routebzero_assets_all_17d58dc0450597d1.bundle"
ZERO_ASSET = "Assets/Resources_moved/ModelPrefabs/RouteA/RouteBZero.prefab"
W10_REF = WORK / "w10_ref"
UNITS_META = W10_REF / "units_meta.bin"


def test_check_model_passes_on_our_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    if not ZERO.is_file():
        pytest.skip(f"缺 {ZERO}（先跑 _work/route-b/routeb_zero.py）")
    rc = main(["check-model", "--bundle", str(ZERO), "--asset-path", ZERO_ASSET,
               "--unit-prefab"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "① 整包 PPtr 普查" in out and "② 出厂闸" in out and "③ catalog 四件套" in out
    assert "悬空 0" in out


def test_check_model_with_key_checks_the_chain(capsys: pytest.CaptureFixture[str]) -> None:
    if not ZERO.is_file():
        pytest.skip(f"缺 {ZERO}")
    from ba_mod_maker.core.assets.game import find_game_root

    root = find_game_root()
    if root is None or not (Path(root) / "BrokenArrow_Data" / "StreamingAssets" / "aa"
                            / "catalog.json").is_file():
        pytest.skip("没装游戏 / catalog 不在")
    rc = main(["check-model", "--bundle", str(ZERO), "--asset-path", ZERO_ASSET,
               "--unit-prefab", "--key", "RouteBZero"])
    out = capsys.readouterr().out
    assert "④ 解析链" in out
    assert rc in (0, 1), out          # 装了就是 0；被别人撤过就是 1（两种都算这把尺子在工作）


def test_check_model_missing_bundle_is_two(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["check-model", "--bundle", str(WORK / "没有这个包.bundle")])
    assert rc == 2
    assert "找不到包" in capsys.readouterr().err


def test_check_model_flags_dangling_pptr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """负控：造一个**指向包内不存在 pathID** 的包 —— 必须 rc 1 且点名。"""
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin")
    meta = UNITS_META.read_bytes()
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    go_pid = builder.path_id_for("chk|go")
    tr_pid = builder.path_id_for("chk|tr")
    missing = builder.path_id_for("chk|missing")
    go = prefab_build.default_fields(table.entry_for(1).nodes)
    go["m_Name"] = "ChkRoot"
    go["m_Component"] = [{"component": prefab_build.pptr(tr_pid)},
                         {"component": prefab_build.pptr(missing)}]
    tr = prefab_build.default_fields(table.entry_for(4).nodes)
    tr["m_GameObject"] = prefab_build.pptr(go_pid)
    plan = builder.build_object_bundle(
        objects=[builder.ObjectSpec(1, go, go_pid, label="go"),
                 builder.ObjectSpec(4, tr, tr_pid, label="tr")],
        bundle_name="chkprobe_assets_all_" + "0" * 16 + ".bundle", source_meta=meta,
        container=[("Assets/Resources_moved/ModelPrefabs/RouteA/Chk.prefab", go_pid)],
        preload_all=True)
    path = tmp_path / "chk.bundle"
    path.write_bytes(bytes(plan.data))
    rc = main(["check-model", "--bundle", str(path)])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "悬空" in out and str(missing) in out, out


def test_check_model_spec_needs_exactly_one_source(capsys: pytest.CaptureFixture[str]) -> None:
    """负控 3：`--spec` 要**正好一个**来源 —— 0 个或 2 个都 rc 2（⛔ 不静默挑一个）。"""
    assert main(["check-model", "--spec"]) == 2
    assert "正好给一个" in capsys.readouterr().err
    assert main(["check-model", "--spec", "--unit", "X", "--bundle", "Y"]) == 2
    assert "正好给一个" in capsys.readouterr().err
