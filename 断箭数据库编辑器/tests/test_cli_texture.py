"""`cli/texture.py` 的用例：接线面 ＋ 退出码（⛔ 不碰大件包、不碰游戏目录）。"""

from __future__ import annotations

import argparse
import json

from ba_mod_maker.cli import texture


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    texture.add_parser(sub)
    return p


def test_add_parser_registers_texture_group():
    p = _parser()
    args = p.parse_args(["texture", "encode", "a.png"])
    assert args.command == "texture" and args.action == "encode" and args.png_pos == "a.png"
    assert args.alpha == "opaque" and args.json_out is False
    assert p.parse_args(["texture", "apply", "--bundle", "b", "--material", "M",
                         "--png", "a.png", "--out", "o.bundle"]).slot is None   # --slot 可选
    args2 = p.parse_args(["texture", "apply", "--bundle", "b", "--material", "M", "--slot", "S",
                          "--png", "a.png", "--out", "o.bundle", "--victim", "123",
                          "--alpha", "keep", "--json"])
    assert args2.victim == 123 and args2.alpha == "keep" and args2.json_out is True
    assert args2.asset_path is None          # 不给 ⇒ None
    asset = "Assets/Resources_moved/ModelPrefabs/DLC2/DLC2_CV9035.prefab"
    args3 = p.parse_args(["texture", "apply", "--bundle", "b", "--material", "M",
                          "--slot", "S", "--png", "a.png", "--out", "o.bundle",
                          "--asset-path", asset])
    assert args3.asset_path == asset
    # --mips：不给是 None（= 自动：尺寸同受害者就照抄、不同就用满链），给了就按给的
    assert p.parse_args(["texture", "apply", "--bundle", "b", "--material", "M",
                         "--png", "a.png", "--out", "o.bundle"]).mips is None
    assert p.parse_args(["texture", "apply", "--bundle", "b", "--material", "M",
                         "--png", "a.png", "--out", "o.bundle", "--mips", "4"]).mips == 4


def test_run_without_action_is_unusable(capsys):
    args = argparse.Namespace(action="nope")
    assert texture.run(args) == texture.EXIT_UNUSABLE
    assert "texture encode" in capsys.readouterr().err


def test_encode_missing_png_is_unusable(capsys):
    args = argparse.Namespace(action="encode", png_pos=None, png_opt=None, out=None, json_out=False)
    assert texture.run(args) == texture.EXIT_UNUSABLE
    assert "要一个 PNG" in capsys.readouterr().err


def test_apply_missing_params_is_unusable(capsys):
    args = argparse.Namespace(action="apply", bundle=None, material=None, slot=None,
                              png_pos=None, png_opt=None, out=None, victim=None,
                              alpha="opaque", json_out=False)
    assert texture.run(args) == texture.EXIT_UNUSABLE
    assert "缺参数" in capsys.readouterr().err


def test_apply_with_a_different_size_png_end_to_end(tmp_path, capsys):
    """变长档走**产品入口**（`texture.run`）：PNG 尺寸 != 受害者也出包，JSON 里给六读数。"""
    from test_texture_install import make_texvar_fixture

    fx = make_texvar_fixture(tmp_path)
    out = tmp_path / "cli_var.bundle"
    args = argparse.Namespace(action="apply", bundle=str(fx.bundle), material="TexVarMat",
                              slot="Layer_Albedo", png_pos=None, png_opt=str(fx.png_var),
                              out=str(out), victim=fx.victim, alpha="opaque", mips=None,
                              asset_path=None, json_out=True)
    assert texture.run(args) == texture.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert (doc["image"]["width"], doc["image"]["height"]) == (128, 48)
    assert doc["victim"]["shape_changed"] is True and doc["problems"] == []
    assert doc["readback"]["width"] == 128 and doc["readback"]["mip_count"] == 8
    assert doc["readback"]["stream"]["size"] == doc["payload"]["bytes"]
    assert doc["readback"]["res_s"]["size"] == doc["payload"]["bytes"]
    assert doc["readback"]["stream"]["offset"] == 0
    # --mips 4：同一个源包、同一张图，只改 mip 级数 —— 长度字段必须跟着走
    out2 = tmp_path / "cli_mips.bundle"
    args2 = argparse.Namespace(action="apply", bundle=str(fx.bundle), material="TexVarMat",
                               slot="Layer_Albedo", png_pos=None, png_opt=str(fx.png_same),
                               out=str(out2), victim=fx.victim, alpha="opaque", mips=4,
                               asset_path=None, json_out=True)
    assert texture.run(args2) == texture.EXIT_OK
    doc2 = json.loads(capsys.readouterr().out)
    assert doc2["image"]["mip_count"] == 4
    assert doc2["readback"]["mip_count"] == 4
    assert doc2["readback"]["complete_image_size"] == doc2["payload"]["bytes"]


def test_cli_apply_moves_the_other_streams_and_exits_clean(tmp_path, capsys):
    """A5 收官走**产品入口**：闭包里其它流式贴图的纹素跟过来了 ⇒ 退出码 0（不再是恒 1）。"""
    from test_texture_install import ExtraStream, make_texvar_fixture

    base = tmp_path / "a5"
    fx = make_texvar_fixture(base, extra_streams=(ExtraStream("Layer_Detail", 200, 300),))
    out = base / "cli_a5.bundle"
    args = argparse.Namespace(action="apply", bundle=str(fx.bundle), material="TexVarMat",
                              slot="Layer_Albedo", png_pos=None, png_opt=str(fx.png_same),
                              out=str(out), victim=fx.victim, alpha="opaque", mips=None,
                              asset_path=None, json_out=True)
    assert texture.run(args) == texture.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["streams"]["self_sufficient"] is True
    assert len(doc["streams"]["moved"]) == 1 and doc["streams"]["stuck"] == []
    assert doc["streams"]["moved"][0]["offset"] % 16 == 0          # 流追加对齐 16
    assert doc["dirty"]["foreign_textures"] == 0


def test_cli_apply_reports_an_unmovable_stream_as_dirty(tmp_path, capsys):
    """搬不动的那张**必须报出来**：退出码 1 ＋ 逐张原因（⛔ 不许静默算绿）。"""
    from test_texture_install import ExtraStream, make_texvar_fixture

    base = tmp_path / "stuck"
    fx = make_texvar_fixture(base, extra_streams=(ExtraStream("Layer_Detail", 900, 400),))
    out = base / "cli_stuck.bundle"
    args = argparse.Namespace(action="apply", bundle=str(fx.bundle), material="TexVarMat",
                              slot="Layer_Albedo", png_pos=None, png_opt=str(fx.png_same),
                              out=str(out), victim=fx.victim, alpha="opaque", mips=None,
                              asset_path=None, json_out=True)
    assert texture.run(args) == texture.EXIT_DIRTY
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["streams"]["self_sufficient"] is False
    assert len(doc["streams"]["stuck"]) == 1
    assert "装不下" in doc["streams"]["stuck"][0]["reason"]
    assert doc["dirty"]["foreign_textures"] == 1 and doc["dirty"]["reasons"]
    assert "结果不干净" in captured.err and "装不下" in captured.err   # 人读那份也印出来


def test_apply_bundle_not_found_is_unusable(tmp_path, capsys):
    png = tmp_path / "a.png"
    from PIL import Image

    Image.new("RGBA", (8, 8), (1, 2, 3, 4)).save(png)
    args = argparse.Namespace(action="apply", bundle=str(tmp_path / "没有这个包.bundle"),
                              material="M", slot="S", png_pos=None, png_opt=str(png),
                              out=str(tmp_path / "o.bundle"), victim=None, alpha="opaque",
                              json_out=False)
    assert texture.run(args) == texture.EXIT_UNUSABLE
    assert "找不到件包" in capsys.readouterr().err


def test_encode_writes_payload_and_json(tmp_path, capsys):
    from PIL import Image

    from ba_mod_maker.asset_kernel import textures

    png = tmp_path / "img.png"
    Image.new("RGBA", (64, 64), (9, 8, 7, 255)).save(png)
    out = tmp_path / "img.bc3"
    args = argparse.Namespace(action="encode", png_pos=str(png), png_opt=None, out=str(out),
                              json_out=True)
    assert texture.run(args) == texture.EXIT_OK
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["schema"] and doc["ok"] is True and doc["action"] == "encode"
    assert doc["mip_count"] == 7 and doc["format"] == 12
    assert out.read_bytes() == bytes(out.read_bytes())
    assert out.stat().st_size == textures.dxt_payload_size(64, 64, 12, 7)
    assert doc["payload_bytes"] == out.stat().st_size
    assert "n/a" in captured.err          # 人读那份走 stderr（--json 时）
