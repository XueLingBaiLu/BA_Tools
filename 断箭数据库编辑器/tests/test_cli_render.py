"""CLI 的 `render` 子命令（零 Qt，**默认不起 Unity**）。

这一层钉四件事：

1. **参数面**：`add_parser(sub)` 注册的就是 `render`，八个开关都在 ——
   `__main__.py` 接线只调这一句；⛔ 那个文件由 Lead 独占，所以这里**不调** `cli_main`，
   而是自己拼一个 subparsers 再走 `render.run`；
2. **退出码**：0 出图 / 1 有红 / 2 用不了；
3. **每条判据的负控**（这一份的重点）：喂一个**已知违规**进去，看它报不报 ——
   包不在 ⇒ 2（且 ⛔ **不许**静默出一张空图）；读数没变新 ⇒ 1（白跑）；读数变了却没新图 ⇒ 1；
   读不出「渲染器 N 个」⇒ 1；超时 ⇒ 1；工程被编辑器占着 ⇒ 2（`--force` 能跳过）；
4. **读数解析**：HDRP / Built-in 两份**真读数**都要读得出 SRP / shader / 渲染器数 / 视图。

⛔ 用例**不真起 Unity**：`render._invoke` 就是那条缝（`tests/**` 的 SLF001 豁免正是给它用的）。
真机那一档（真跑一趟 HDRP、约 21 秒，判据是**产物 mtime**）按 kb/14 §6 由人手工跑。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ba_mod_maker.cli import render
from ba_mod_maker.core.errors import AssetError

# --------------------------------------------------------------------------- #
# 两份**真读数**（从 _work/ 抄的原文；只为行宽做了字面量拼接，内容一字未改）
# --------------------------------------------------------------------------- #

HDRP_READINGS = (
    "HDRP 渲染 · bundle="
    "D:\\断箭模组制作\\BA_Mod_Maker_2.0\\_work\\g7b\\out\\"
    "g7b_assets_all_84525add423f1efb.bundle\n"
    "SRP: UnityEngine.Rendering.HighDefinition.HDRenderPipelineAsset\n"
    "Volume: 物理天空 + 固定曝光 12.5 EV + Neutral\n"
    "太阳: 12000 lux · 48/38 度\n"
    "包里 GameObject 共 1 个\n"
    "预制体 G7BRoot · activeInHierarchy=True · 渲染器 9 个\n"
    "  着色器: HDRP/Lit\n"
    "合并包围盒 center=(0.500, 0.750, 0.000) size=(7.400, 1.500, 6.000)\n"
    "相机: HDAdditionalCameraData=OK · AA=None · clearFlags=Skybox\n"
    "视图 side           -> hdrp-render-side.png · 渲染器 isVisible 3/9\n"
    "视图 three-quarter  -> hdrp-render-three-quarter.png · 渲染器 isVisible 3/9\n"
    "视图 front          -> hdrp-render-front.png · 渲染器 isVisible 3/9\n"
    "视图 top            -> hdrp-render-top.png · 渲染器 isVisible 3/9\n"
)

BUILTIN_READINGS = (
    "BUNDLE D:\\断箭模组制作\\BA_Mod_Maker_2.0\\_work\\a2\\"
    "routea2paint_assets_all_cc1dc0904b895fd1.bundle\n"
    "包内资产: Assets/Resources_moved/ModelPrefabs/RouteA/RouteA2Paint.prefab\n"
    "预制体: RU_T72B_Base\n"
    "激活后 activeInHierarchy=True\n"
    "渲染器 25 个\n"
    "  材质 RU_T72B 1 -> Standard · 贴图 RU_T72B_1_BaseMap (1024x1024)\n"
    "  材质 RU_T72B_track -> Standard · 贴图 RU_Track7290_Standard_22_AlbedoTransparency"
    " (512x512)\n"
    "  材质 LODs -> Standard · 贴图 (无) (-)\n"
    "换材质 25 槽 · 绑到贴图 24 槽\n"
    "合并包围盒 center=(0.003, 1.328, -0.236) size=(3.676, 2.662, 7.372)\n"
    "视图 side           -> unity-render-side.png · 渲染后 isVisible 19/25 · ortho=True\n"
    "视图 three-quarter  -> unity-render-three-quarter.png · 渲染后 isVisible 19/25 · ortho=False\n"
    "视图 front          -> unity-render-front.png · 渲染后 isVisible 19/25 · ortho=True\n"
    "视图 top            -> unity-render-top.png · 渲染后 isVisible 19/25 · ortho=True\n"
)


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #


@pytest.fixture()
def world(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """一个假世界：假包 + 假工程 + 假 Unity.exe（⛔ 都不真跑）。

    顺手把 `_unity_processes` 钉成「探不到」—— 用例不该随**这台机器**上开着别的
    Unity 工程而变，也不该为一次 tasklist 付时间。
    """
    monkeypatch.setattr(render, "_unity_processes", lambda: None)
    bundle = tmp_path / "some_assets_all_deadbeef.bundle"
    bundle.write_bytes(b"UnityFS fake bundle")
    project = tmp_path / "unity-hdrp"
    (project / "ProjectSettings").mkdir(parents=True)
    (project / "Assets").mkdir()
    unity = tmp_path / "Unity.exe"
    unity.write_bytes(b"fake")
    return SimpleNamespace(
        bundle=bundle, project=project, unity=unity, out=tmp_path / "out", home=tmp_path
    )


def _parser() -> argparse.ArgumentParser:
    """自己拼一个 subparsers —— ⛔ 不碰 Lead 独占的 `__main__.py`。"""
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    render.add_parser(sub)
    return parser


def _argv(world: SimpleNamespace, *extra: str) -> list[str]:
    """一条命令行的坐标全指到假世界里（所以绝不会真起编辑器）。"""
    return [
        str(world.bundle),
        "--project",
        str(world.project),
        "--unity",
        str(world.unity),
        "--out",
        str(world.out),
        *extra,
    ]


def _run(capsys, world: SimpleNamespace, *extra: str) -> tuple[int, str, str]:
    """走 `add_parser` + `run`（与 `__main__.py` 接线同一条路）。"""
    args = _parser().parse_args(["render", *_argv(world, *extra)])
    code = render.run(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _fake_invoke(*, readings: str | None = HDRP_READINGS, pngs: int = 4, rc: int | None = 0):
    """把 `_invoke` 换成「写一份读数 + 出 N 张图」的假编辑器。

    ⛔ 这就是**不真起 Unity** 的那条缝；真机那一档由人按 kb/14 §6 手工跑。
    """

    def invoke(plan):
        if readings is not None:
            plan.readings.write_text(readings, encoding="utf-8")
        for index in range(pngs):
            target = plan.out_dir / f"{plan.prefix}-view{index}.png"
            target.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        return rc, False

    return invoke


# --------------------------------------------------------------------------- #
# 1 · 参数面
# --------------------------------------------------------------------------- #


def test_add_parser_registers_the_render_subcommand() -> None:
    """`__main__.py` 只调 `render.add_parser(sub)` —— 注册的必须是 `render`。"""
    args = _parser().parse_args(["render", "x.bundle"])
    assert args.command == "render"
    assert args.bundle == "x.bundle"
    assert (args.hdrp, args.builtin, args.force, args.json_out) == (False, False, False, False)


def test_parser_carries_every_flag_the_shim_documents() -> None:
    args = _parser().parse_args(
        [
            "render",
            "x.bundle",
            "--builtin",
            "--out",
            "o",
            "--project",
            "p",
            "--unity",
            "u.exe",
            "--timeout",
            "5",
            "--force",
            "--json",
        ]
    )
    assert (args.builtin, args.out, args.project) == (True, "o", "p")
    assert (args.unity, args.timeout, args.force, args.json_out) == ("u.exe", 5.0, True, True)


# --------------------------------------------------------------------------- #
# 2 · 负控：包不在 ⇒ 2，且**一个产物都不许有**
# --------------------------------------------------------------------------- #


def test_missing_bundle_exits_two_and_writes_nothing(capsys, world) -> None:
    """⛔ 负控：包不在时不许静默出一张空图，也不许退出码 0。"""
    missing = world.home / "没有这个包.bundle"
    assert not missing.exists()
    args = _parser().parse_args(
        [
            "render",
            str(missing),
            "--project",
            str(world.project),
            "--unity",
            str(world.unity),
            "--out",
            str(world.out),
        ]
    )
    code = render.run(args)
    captured = capsys.readouterr()
    assert code == render.EXIT_UNUSABLE == 2
    assert "包不在" in captured.err
    produced = list(world.out.glob("*")) if world.out.exists() else []
    assert produced == []


# --------------------------------------------------------------------------- #
# 3 · 负控：三条「结果不干净」的判据都要报红
# --------------------------------------------------------------------------- #


def test_stale_readings_exits_one(monkeypatch, capsys, world) -> None:
    """**白跑**的样子：读数文件没变新（工程被编辑器占着时 Unity 会静默交接）。"""
    monkeypatch.setattr(render, "_invoke", _fake_invoke(readings=None, pngs=0))
    code, _out, err = _run(capsys, world)
    assert code == render.EXIT_RED == 1
    assert "没真跑" in err


def test_fresh_readings_but_no_png_exits_one(monkeypatch, capsys, world) -> None:
    monkeypatch.setattr(render, "_invoke", _fake_invoke(pngs=0))
    code, _out, err = _run(capsys, world)
    assert code == render.EXIT_RED == 1
    assert "PNG" in err


def test_readings_without_renderer_count_exits_one(monkeypatch, capsys, world) -> None:
    """读数形状不对也要红，⛔ 不许当 0 蒙过去。"""
    bad = HDRP_READINGS.replace("渲染器 9 个", "渲染器许多个")
    monkeypatch.setattr(render, "_invoke", _fake_invoke(readings=bad))
    code, _out, err = _run(capsys, world)
    assert code == render.EXIT_RED == 1
    assert "渲染器" in err


def test_timeout_is_red(monkeypatch, capsys, world) -> None:
    """超时（进程被杀）必须红，⛔ 不许因为「退出码是空的」就当成功。"""
    monkeypatch.setattr(render, "_invoke", lambda plan: (None, True))
    code, _out, err = _run(capsys, world)
    assert code == render.EXIT_RED == 1
    assert "超时" in err


# --------------------------------------------------------------------------- #
# 4 · 负控：工程被编辑器占着 ⇒ 2（`--force` 能跳过）
# --------------------------------------------------------------------------- #


def test_editor_holding_the_project_refuses_to_start(capsys, world) -> None:
    """**先探编辑器开着没**：锁文件在 ⇒ 拒绝起（否则这一轮静默交接、白跑）。"""
    lock = world.project / render.LOCK_REL
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    code, out, err = _run(capsys, world)
    assert code == render.EXIT_UNUSABLE == 2
    assert "编辑器还开着" in err
    assert "UnityLockfile" in (out + err)
    assert list(world.out.glob("*")) == []


def test_force_overrides_the_editor_preflight(monkeypatch, capsys, world) -> None:
    """`--force` 是逃生口：照样起，但读数里要说清是强起的。"""
    lock = world.project / render.LOCK_REL
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    monkeypatch.setattr(render, "_invoke", _fake_invoke())
    code, out, _err = _run(capsys, world, "--force")
    assert code == render.EXIT_OK == 0
    assert "--force" in out


def test_probe_says_unknown_instead_of_zero(monkeypatch) -> None:
    """⛔ 「探不到」不许说成 0（那是把不知道当依据）。"""
    monkeypatch.setattr(render, "_unity_processes", lambda: None)
    _locked, note = render._editor_probe(Path("D:/没有这个工程"))
    assert "探不到" in note

    monkeypatch.setattr(render, "_unity_processes", lambda: 3)
    _locked, note = render._editor_probe(Path("D:/没有这个工程"))
    assert "3 个" in note


# --------------------------------------------------------------------------- #
# 5 · 正控：一条命令出图（假编辑器，坐标全真）
# --------------------------------------------------------------------------- #


def test_positive_run_prints_srp_shader_renderers_and_paths(monkeypatch, capsys, world) -> None:
    """J-T4-1 的形状：stdout 里必须**印出** SRP 名 / 材质 shader 名 / 渲染器数 / 产物路径。"""
    monkeypatch.setattr(render, "_invoke", _fake_invoke())
    code, out, err = _run(capsys, world)
    assert code == render.EXIT_OK == 0, err
    assert "UnityEngine.Rendering.HighDefinition.HDRenderPipelineAsset" in out
    assert "HDRP/Lit" in out
    assert "9 个" in out
    for index in range(4):
        png = (world.out / f"hdrp-render-view{index}.png").resolve()
        assert str(png) in out
    assert "已更新" in out
    assert len(list(world.out.glob("hdrp-render-*.png"))) == 4


def test_plan_builds_the_batchmode_command_line(world) -> None:
    """命令行形状本身是判据：少一个 `-batchmode` 就是**弹一个编辑器窗口**（用户看得见）。"""
    plan = render.plan_render(
        bundle=world.bundle, out_dir=world.out, project=world.project, unity=world.unity
    )
    head = plan.argv[:7]
    expected = ("-batchmode", "-quit", "-projectPath", str(world.project.resolve()))
    assert head[1:5] == expected
    assert head[5:7] == ("-executeMethod", "RouteARenderHDRP.Run")
    assert "-routeBundle" in plan.argv and str(plan.bundle) in plan.argv
    assert "-logFile" in plan.argv
    assert plan.prefix == "hdrp-render"


def test_plan_switches_project_and_method_for_builtin(world) -> None:
    plan = render.plan_render(
        bundle=world.bundle,
        mode="builtin",
        out_dir=world.out,
        project=world.project,
        unity=world.unity,
    )
    assert plan.method == "RouteARender.Run"
    assert plan.prefix == "unity-render"


def test_project_without_projectsettings_is_unusable(world) -> None:
    empty = world.home / "not-a-project"
    empty.mkdir()
    with pytest.raises(AssetError) as caught:
        render.plan_render(bundle=world.bundle, out_dir=world.out, project=empty, unity=world.unity)
    assert "不像工程" in caught.value.message


def test_missing_unity_is_unusable(world) -> None:
    with pytest.raises(AssetError) as caught:
        render.plan_render(
            bundle=world.bundle,
            out_dir=world.out,
            project=world.project,
            unity=world.home / "没有Unity.exe",
        )
    assert "找不到 Unity.exe" in caught.value.message


# --------------------------------------------------------------------------- #
# 6 · `--json` 契约：stdout 只有一个 JSON 文档，人读那份改走 stderr
# --------------------------------------------------------------------------- #


def test_json_mode_puts_exactly_one_document_on_stdout(monkeypatch, capsys, world) -> None:
    monkeypatch.setattr(render, "_invoke", _fake_invoke())
    code, out, err = _run(capsys, world, "--json")
    assert code == render.EXIT_OK == 0
    document = json.loads(out)
    assert document["schema"] == 1 and document["ok"] is True
    assert document["mode"] == "hdrp"
    assert document["shader"] == "HDRP/Lit"
    assert document["renderers"] == 9
    assert document["readings_updated"] is True
    assert len(document["views"]) == 4
    assert document["problems"] == []
    assert err != ""  # 人读那份没丢，只是改走了 stderr


def test_json_failure_is_ok_false_on_stdout(capsys, world) -> None:
    """失败时 stdout 上也有一份 `ok:false` —— stdout 上永远不会出现非 JSON。"""
    missing = world.home / "没有这个包.bundle"
    args = _parser().parse_args(
        [
            "render",
            str(missing),
            "--project",
            str(world.project),
            "--unity",
            str(world.unity),
            "--out",
            str(world.out),
            "--json",
        ]
    )
    code = render.run(args)
    captured = capsys.readouterr()
    assert code == render.EXIT_UNUSABLE
    document = json.loads(captured.out)
    assert document["ok"] is False
    assert "包不在" in document["error"]["message"]


# --------------------------------------------------------------------------- #
# 7 · 读数解析（两份**真读数**）
# --------------------------------------------------------------------------- #


def test_parses_the_hdrp_readings() -> None:
    readings = render.parse_readings(HDRP_READINGS)
    assert readings.srp == "UnityEngine.Rendering.HighDefinition.HDRenderPipelineAsset"
    assert readings.shader == "HDRP/Lit"
    assert readings.renderers == 9
    assert readings.views[0] == ("side", "hdrp-render-side.png")
    assert [view for view, _png in readings.views] == ["side", "three-quarter", "front", "top"]


def test_parses_the_builtin_readings() -> None:
    """Built-in 那一档**没有**「着色器:」行 —— shader 名要从材质行抬（⛔ 不是编一个）。"""
    readings = render.parse_readings(BUILTIN_READINGS)
    assert readings.srp is None
    assert readings.shader == "Standard（3 个材质槽）"
    assert readings.renderers == 25
    assert len(readings.views) == 4
    assert readings.views[0] == ("side", "unity-render-side.png")


def test_parse_readings_never_invents_a_zero() -> None:
    """空读数 ⇒ 字段是 `None`，⛔ 不是 0 / 空串（「不知道」不许当「是 0」）。"""
    readings = render.parse_readings("")
    assert readings.srp is None and readings.shader is None
    assert readings.renderers is None and readings.views == ()


# --------------------------------------------------------------------------- #
# 8 · 默认 Unity.exe 与薄封装那条路
# --------------------------------------------------------------------------- #


def test_default_unity_is_the_same_one_vt_bake_uses() -> None:
    """⛔ 不许有第二份「本机 Unity 在哪」—— 与 asset_kernel/vt_bake.py 同一个常量。"""
    from ba_mod_maker.asset_kernel.vt_bake import DEFAULT_UNITY

    assert render._default_unity() == Path(DEFAULT_UNITY)


def test_standalone_main_is_the_same_command(capsys, tmp_path) -> None:
    """根 `tools/unity_render.py` 调的 `render.main` —— 包不在时也要报红（2）。"""
    code = render.main([str(tmp_path / "没有这个包.bundle")])
    captured = capsys.readouterr()
    assert code == render.EXIT_UNUSABLE
    assert "包不在" in captured.err
