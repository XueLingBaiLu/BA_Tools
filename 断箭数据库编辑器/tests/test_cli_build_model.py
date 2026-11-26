r"""\`ba-mod-maker build-model\` 的判据：**一条命令把 OBJ 变成游戏包**。

**判据一行**：pytest tests/test_cli_build_model.py -q -n 0 -> 通过数 0 -> N，其中 **2 条是负控**。

| # | 喂什么 | 必须 |
|---|---|---|
| 正控 | 3×2×6 盒子 OBJ（**不带 vt/vn** ⇒ 8 个顶点）+ 模板包 | rc 0 + 输出里有顶点/悬空 0/全绿 |
| 负控 1 | OBJ 路径不存在 | rc **2** |
| 负控 2 | 模板包路径不存在 | rc **2** |
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.cli.__main__ import main

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = (WORK / "route-b" / "out"
            / "routebunit_assets_all_5847a22c37556b4a.bundle")

BOX = """v -1.5 -1 -3
v  1.5 -1 -3
v  1.5  1 -3
v -1.5  1 -3
v -1.5 -1  3
v  1.5 -1  3
v  1.5  1  3
v -1.5  1  3
f 1 4 3 2
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


@pytest.fixture()
def obj_file(tmp_path: Path) -> Path:
    if not TEMPLATE.is_file():
        pytest.skip(f"缺 {TEMPLATE}（先跑 _work/route-b/routeb_assemble.py）")
    path = tmp_path / "box.obj"
    path.write_text(BOX, encoding="utf-8")
    return path


def test_build_model_makes_a_bundle(obj_file: Path, tmp_path: Path,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["build-model", "--obj", str(obj_file), "--template-bundle", str(TEMPLATE),
               "--key", "CliCube", "--mesh-template", "RouteAHull",
               "--material", "RouteAPaint", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "顶点 8" in out, out          # 不带 vt/vn ⇒ 按位置去重，8 个顶点
    assert "悬空 0" in out, out
    assert "全绿" in out, out
    assert list(tmp_path.glob("clicube_assets_all_*.bundle")), "包没落盘"
    # ★ 默认资产路径必须与 add-model 的派生式**同一个**（第一版写成 .../RouteA/<key> 就与
    #   new_asset.model_asset_path_of 不一致 ⇒ 容器里没有 catalog 那条 internalId）
    from ba_mod_maker.core.asset.new_asset import model_asset_path_of

    assert f"资产路径 {model_asset_path_of('CliCube')}" in out, out


def test_missing_obj_is_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["build-model", "--obj", str(tmp_path / "nope.obj"),
               "--template-bundle", str(TEMPLATE), "--key", "X", "--out", str(tmp_path)])
    assert rc == 2
    assert "找不到 OBJ" in capsys.readouterr().err


def test_missing_template_is_two(obj_file: Path, tmp_path: Path,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["build-model", "--obj", str(obj_file),
               "--template-bundle", str(tmp_path / "nope.bundle"), "--key", "X",
               "--out", str(tmp_path)])
    assert rc == 2
    assert "找不到模板包" in capsys.readouterr().err
