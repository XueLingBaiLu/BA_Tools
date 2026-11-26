"""`asset_kernel/texture_install.py` 的用例：纯函数那一层 + alpha 门控 + **变长档那把尺子**。

⛔ 这里**不搬大件包**（源包 449 MB）：等长档端到端那三条（逐字节对拍 / 逐对象对拍 / 负控）
在 `_work/texprod/e2e2.py` 与 `_work/texd/verify.py` 里真跑过，读数写进 `_work/texd/读数.md`。

**变长档（2026-10-11）那几条用的夹具是合成件**：`_work/w10_ref/units_meta.bin`（真件 units
元数据区冻结件）当类型树 schema，现造一个 5 个对象、10 KB 级的小单位模型包 ——
⛔ 不碰游戏目录、⛔ 不开 units 包（3.38 GB）。冻结件缺了就整组 skip（不造假 schema）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder, clone, material_build, prefab_build, textures
from ba_mod_maker.asset_kernel import texture_install as ti
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: 真件 units 元数据区冻结件（4,129,040 B）—— 夹具的类型树（Texture2D 28 / Material 21 /
#: MeshRenderer 23 / GameObject 1 / Transform 4）全部从它取，⛔ 不自己编字段名。
UNITS_META = WORK / "w10_ref" / "units_meta.bin"
ASSET = "Assets/Resources_moved/ModelPrefabs/TexVar/TexVarProbe.prefab"
SLOT = "Layer_Albedo"
#: 夹具受害者的形状（照抄真件：29 = DXT5Crunched、m_StreamData.size == m_CompleteImageSize）。
VICTIM = (64, 64, 7, 29, 1000)
#: 64x64 DXT5 满 7 级的字节数（64,32,16,8,4,2,1）。
SAME_PAYLOAD = 5488


def _png(tmp_path, size=(8, 8), alpha_ramp=True):
    from PIL import Image

    w, h = size
    im = Image.new("RGBA", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            a = int(255 * x / max(1, w - 1)) if alpha_ramp else 255
            px[x, y] = (10, 20 + y, 30 + x, a)
    path = tmp_path / f"img_{w}x{h}_{int(alpha_ramp)}.png"
    im.save(path)
    return path


# --------------------------------------------------------------------------- #
# 纯函数那一层（原样保留）
# --------------------------------------------------------------------------- #
def test_read_png_is_rgba_topdown(tmp_path):
    img = ti.read_png(_png(tmp_path, (4, 2)))
    assert (img.width, img.height) == (4, 2)
    assert len(img.pixels) == 4 * 2 * 4
    assert img.pixel_count == 8


def test_replace_alpha_leaves_rgb_alone(tmp_path):
    img = ti.read_png(_png(tmp_path, (8, 4), alpha_ramp=True))
    filled = ti.replace_alpha(img)
    assert filled.pixels[3::4] == bytes([255]) * img.pixel_count
    assert [p for i, p in enumerate(filled.pixels) if i % 4 != 3] == [
        p for i, p in enumerate(img.pixels) if i % 4 != 3
    ]


def test_flip_rows_is_its_own_inverse():
    buf = bytes(range(4 * 3 * 4))
    assert ti.flip_rows(ti.flip_rows(buf, 4, 3), 4, 3) == buf
    # 行序反了：新第 0 行 = 原最后一行（每行 4px * 4B = 16 B）
    assert ti.flip_rows(buf, 4, 3)[:16] == buf[2 * 16: 3 * 16]


def test_alpha_stats_and_note(tmp_path):
    ramp = ti.read_png(_png(tmp_path, (8, 4), alpha_ramp=True))
    below, total, amin, amax = ti.alpha_stats(ramp, 0.5)
    assert total == 32 and amin == 0 and amax == 255
    assert 0 < below < total
    note = ti.alpha_note(ramp, gate=1.0, cutoff=0.5)
    assert note.triggered is True and note.ratio == pytest.approx(below / total)
    assert "_ENABLEALPHACLIPPING=1" in note.summary()
    opaque = ti.replace_alpha(ramp)
    assert ti.alpha_note(opaque, gate=1.0, cutoff=0.5).triggered is False
    # 门控没开 ⇒ 就算图里有会被裁的像素，也**不是**这条尺子的事
    assert ti.alpha_note(ramp, gate=0.0, cutoff=0.5).triggered is False


def test_encode_payload_size_follows_formula():
    img = ti.RgbaImage(width=64, height=64, pixels=bytes([1, 2, 3, 4]) * (64 * 64))
    payload = ti.encode_payload(img, mip_count=7)
    assert len(payload) == textures.dxt_payload_size(64, 64, ti.DXT5, 7)


def test_material_alpha_gate_reads_both_pair_shapes():
    def floats(pairs):
        return {"m_SavedProperties": {"m_Floats": pairs}}

    dict_shape = floats([{"first": "_ENABLEALPHACLIPPING", "second": 1.0},
                         {"first": "_AlphaCutoff", "second": 0.5}])
    list_shape = floats([["_ENABLEALPHACLIPPING", 1.0], ["_AlphaCutoff", 0.5]])
    assert ti.material_alpha_gate(dict_shape) == (1.0, 0.5)
    assert ti.material_alpha_gate(list_shape) == (1.0, 0.5)
    # 认不出就 0.0（⛔ 不猜）
    assert ti.material_alpha_gate({}) == (0.0, 0.0)
    # HDRP/Lit 叫 _AlphaClipThreshold
    assert ti.material_alpha_gate(floats([["_AlphaClipThreshold", 0.25]])) == (0.0, 0.25)


def test_pair_shape_bridge_round_trips():
    tree = {"m_Name": "M", "m_SavedProperties": {
        "m_TexEnvs": [{"first": "Layer_A97CDC25", "second": {"m_Texture": {"m_PathID": 7}}},
                      {"first": "_Emission", "second": {"m_Texture": {"m_PathID": 0}}}]}}
    lists = ti.as_pair_lists(tree)
    assert lists["m_SavedProperties"]["m_TexEnvs"][0][0] == "Layer_A97CDC25"
    back = ti.as_pair_dicts(lists)
    assert back["m_SavedProperties"]["m_TexEnvs"] == tree["m_SavedProperties"]["m_TexEnvs"]
    assert tree["m_SavedProperties"]["m_TexEnvs"][0]["first"] == "Layer_A97CDC25"  # 入参不动


def test_missing_png_is_unusable(tmp_path):
    with pytest.raises(AssetError):
        ti.read_png(tmp_path / "没有这张图.png")


def test_plan_apply_rejects_bad_alpha_mode(tmp_path):
    with pytest.raises(AssetError):
        ti.plan_texture_apply(bundle=tmp_path / "x.bundle", material="M", slot="S",
                              png=_png(tmp_path), out=tmp_path / "o.bundle", alpha="乱写")


# --------------------------------------------------------------------------- #
# 变长档：满链级数 + 出包读回那把尺子
# --------------------------------------------------------------------------- #
def test_full_mip_count_goes_all_the_way_to_one_by_one():
    assert ti.full_mip_count(64, 64) == 7        # 64,32,16,8,4,2,1
    assert ti.full_mip_count(128, 48) == 8       # 128,64,32,16,8,4,2,1
    assert ti.full_mip_count(4, 4) == 3
    assert ti.full_mip_count(1, 1) == 1
    assert ti.full_mip_count(1, 64) == 7         # 一边到底了、另一边接着降
    assert ti.full_mip_count(2048, 2048) == 12   # 真件锚点（工单 §2：2048² DXT5 12 级）
    # 满链的**最后一级必须是 1x1**（⛔ 不服就说明这个函数不是"满链"）
    for w, h in ((64, 64), (128, 48), (4, 4), (1, 64), (2048, 2048)):
        assert textures.mip_dims(w, h, ti.full_mip_count(w, h))[-1] == (1, 1)


def test_readback_ruler_is_green_then_red_on_the_format_only_slip():
    """★ 尺子正控（AGENTS §0.9 动作 2）：喂「只改 m_TextureFormat、长度留旧值」必须报红。"""
    good = ti.TextureReading(
        path_id=7, class_id=ti.CLASS_TEXTURE2D, name="T", width=64, height=64,
        texture_format=ti.DXT5, mip_count=7, complete_image_size=SAME_PAYLOAD,
        stream_path="archive:/CAB-x/CAB-x.resS", offset=0, size=SAME_PAYLOAD,
        stream_node_path="CAB-x.resS", stream_node_size=SAME_PAYLOAD,
        pixels=b"\x01" * SAME_PAYLOAD)
    assert ti.texture_readback_problems(good) == ()
    assert ti.texture_readback_problems(good, expect_width=64, expect_height=64,
                                        expect_mip_count=7, expect_payload=good.pixels) == ()
    # 只改格式（49 -> 12）、三个长度字段留旧值 —— ⛔ 一条外部期望都不给，尺子也必须红
    bad = replace(good, complete_image_size=1000, size=1000, stream_node_size=1000)
    problems = ti.texture_readback_problems(bad)
    assert problems, "「只改 m_TextureFormat」那种读数必须被同一把尺子报红"
    assert any("m_CompleteImageSize" in p for p in problems), problems
    # 每一类都能单独红：路径指向别处、offset 不是 0
    assert ti.texture_readback_problems(replace(good, offset=16))
    assert ti.texture_readback_problems(replace(good, stream_node_size=SAME_PAYLOAD - 1))


# --------------------------------------------------------------------------- #
# 变长档：合成夹具 + 端到端（10 KB 级）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ExtraStream:
    """夹具里「闭包内**其它**流式贴图」的一条（A5 那条红的素材）。

    `path=None` ⇒ 指着**源包自己的** .resS（搬得动）；给了路径 ⇒ 指着别处（搬不动，如实报）。
    `offset + size` 超出源包 .resS 长度也是"搬不动"（源包里压根没有那段字节）。
    """

    slot: str
    offset: int
    size: int
    path: str | None = None


@dataclass(frozen=True, slots=True)
class TexVarFixture:
    bundle: Path
    png_same: Path
    png_var: Path
    victim: int
    #: 源包 .resS 的长度（夹具里 = VICTIM[4]）。
    source_stream_size: int = 0
    #: 那些"其它流式贴图"的 pathID（顺序 = 传进来的 `extra_streams` 顺序）。
    extra_pids: tuple[int, ...] = ()
    extras: tuple[ExtraStream, ...] = ()


def _meta() -> bytes:
    if not UNITS_META.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（真件 units 元数据区冻结件）")
    return UNITS_META.read_bytes()


def _png_at(path: Path, w: int, h: int) -> Path:
    from PIL import Image

    im = Image.new("RGBA", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 3) & 0xFF, (y * 5) & 0xFF, ((x + y) * 2) & 0xFF, 255)
    im.save(path)
    return path


def make_texvar_fixture(
    tmp_path: Path, *, extra_streams: Sequence[ExtraStream] = ()
) -> TexVarFixture:
    """造一个**合成**的最小单位模型包当 `texture apply` 的源包（5 个对象 ＋ 若干流式贴图）。

    形状：GameObject(1) + Transform(4) + MeshRenderer(23) + Material(21) + Texture2D(28 流式)，
    容器条目 -> 根 GameObject；`node[1]` = `.resS`（受害者那颗贴图的"旧纹素"）。
    `extra_streams` 那几条会挂在材质的**其它槽**上（这样它们落进闭包 ⇒ 走 A5 那条路）。
    `test_cli_texture.py` 也 import 这个函数（⛔ 不复制第二份夹具）。
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    meta = _meta()
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    go_pid = builder.path_id_for("texvar|go")
    tr_pid = builder.path_id_for("texvar|tr")
    mr_pid = builder.path_id_for("texvar|mr")
    mat_pid = builder.path_id_for("texvar|mat")
    tex_pid = builder.path_id_for("texvar|tex")
    bundle = tmp_path / "texvar_src.bundle"
    w, h, mips, fmt, size = VICTIM

    go = prefab_build.default_fields(table.entry_for(1).nodes)
    go["m_Name"] = "TexVarRoot"
    go["m_Component"] = [{"component": prefab_build.pptr(tr_pid)},
                         {"component": prefab_build.pptr(mr_pid)}]
    tr = prefab_build.default_fields(table.entry_for(4).nodes)
    tr["m_GameObject"] = prefab_build.pptr(go_pid)
    tr["m_Father"] = prefab_build.pptr(0)
    tr["m_Children"] = []
    mr = prefab_build.default_fields(table.entry_for(23).nodes)
    mr["m_GameObject"] = prefab_build.pptr(go_pid)
    mr["m_Materials"] = [prefab_build.pptr(mat_pid)]
    mat = prefab_build.default_fields(table.entry_for(21).nodes)
    mat["m_Name"] = "TexVarMat"
    mat["m_Shader"] = prefab_build.pptr(0)   # 没有 shader 对象 ⇒ color_layers() 给空元组
    mat["m_SavedProperties"]["m_TexEnvs"] = [material_build.tex_env(SLOT, tex_pid)]
    tex = prefab_build.default_fields(table.entry_for(28).nodes)
    tex["m_Name"] = "TexVar_BaseMap"
    tex["m_Width"], tex["m_Height"] = w, h
    tex["m_TextureFormat"] = fmt
    tex["m_MipCount"] = mips
    tex["m_CompleteImageSize"] = size
    tex["m_ColorSpace"] = 1
    tex["image data"] = b""
    node = builder.node_path_for(bundle.name)
    source_stream = f"archive:/{node}/{node}.resS"
    tex["m_StreamData"] = {"offset": 0, "size": size, "path": source_stream}
    objects = [builder.ObjectSpec(1, go, go_pid, label="go"),
               builder.ObjectSpec(4, tr, tr_pid, label="tr"),
               builder.ObjectSpec(23, mr, mr_pid, label="mr"),
               builder.ObjectSpec(21, mat, mat_pid, label="mat"),
               builder.ObjectSpec(28, tex, tex_pid, label="tex")]
    extra_pids: list[int] = []
    for i, extra in enumerate(extra_streams):
        ep = builder.path_id_for(f"texvar|extra|{extra.slot}")
        e_tex = prefab_build.default_fields(table.entry_for(28).nodes)
        e_tex["m_Name"] = f"TexVar_{extra.slot}"
        e_tex["m_Width"], e_tex["m_Height"] = 16, 16
        e_tex["m_TextureFormat"] = fmt
        e_tex["m_MipCount"] = 5
        e_tex["m_CompleteImageSize"] = int(extra.size)
        e_tex["m_ColorSpace"] = 1
        e_tex["image data"] = b""
        e_tex["m_StreamData"] = {"offset": int(extra.offset), "size": int(extra.size),
                                 "path": extra.path or source_stream}
        objects.append(builder.ObjectSpec(28, e_tex, ep, label=f"extra{i}"))
        mat["m_SavedProperties"]["m_TexEnvs"].append(material_build.tex_env(extra.slot, ep))
        extra_pids.append(ep)
    plan = builder.build_object_bundle(
        objects=objects,
        bundle_name=bundle.name, source_meta=meta, container=[(ASSET, go_pid)],
        common_strings=clone.unitypy_common_strings(), block_type=builder.BLOCK_LZ4HC,
        preload_all=True, copy_source_tables=True,
        stream_payload=bytes((i * 7 + 3) & 0xFF for i in range(size)),
    )
    bundle.write_bytes(bytes(plan.data))
    return TexVarFixture(bundle=bundle, png_same=_png_at(tmp_path / "same_64x64.png", w, h),
                         png_var=_png_at(tmp_path / "var_128x48.png", 128, 48), victim=tex_pid,
                         source_stream_size=size, extra_pids=tuple(extra_pids),
                         extras=tuple(extra_streams))


def _source_res_s(fx: TexVarFixture) -> bytes:
    """源包 `.resS` **整段**字节 —— J1 拿它当"原样搬"的基准。"""
    with ti._Reader(fx.bundle) as rd:
        node = rd.stream_node
        return rd.stream_bytes(0, int(node.size))


def _a5_reading(bundle: Path) -> str:
    """`check-model` 那条 A5 读数（独立尺子：`unit_bundle.verify_unit_bundle`）。"""
    from ba_mod_maker.asset_kernel import unit_bundle

    gate = unit_bundle.verify_unit_bundle(bundle, asset_path=ASSET)
    hits = [line for line in gate.readings if line.startswith("A5 ")]
    assert hits, f"出厂闸没给 A5 读数（读数共 {len(gate.readings)} 条）"
    return hits[0]


def _sabotage_format_only(fx: TexVarFixture, out: Path, *, payload: bytes) -> Path:
    """造「**只改 `m_TextureFormat`**、其余三字段（path / offset / size）留旧值」的出包。

    这是工单 §7 坑 2 那个形状：`.resS` 里放的是**我们新编的载荷**，但贴图那三个长度字段
    还写着旧值 ⇒ Unity 会按新格式去解一段旧长度的字节。⛔ 故意不做成"能过"的样子。
    """
    meta = _meta()
    with ti._Reader(fx.bundle) as rd:
        # ⛔ 不搬 AssetBundle(142) 那一个：它由 build_object_bundle 按 container 自己生成
        pids = [p for p in rd.by_pid if rd.class_of(p) != ti.CLASS_ASSETBUNDLE]
        classes = {p: rd.class_of(p) for p in pids}
        raws = {p: rd.raw_of(p) for p in pids}
        bad = dict(rd.fields_of(fx.victim))
        root = next(iter(ti.container_map(rd)))
    bad["m_TextureFormat"] = ti.DXT5          # ← 只动这一个字段
    specs = [
        builder.ObjectSpec(classes[p], bad if p == fx.victim else None, path_id=p,
                           payload=None if p == fx.victim else raws[p], label="sab")
        for p in raws
    ]
    plan = builder.build_object_bundle(
        objects=specs, bundle_name=out.name, source_meta=meta,
        container=[(ASSET, int(root))], common_strings=clone.unitypy_common_strings(),
        block_type=builder.BLOCK_LZ4HC, preload_all=True, copy_source_tables=True,
        stream_payload=payload)
    out.write_bytes(bytes(plan.data))
    return out


def test_same_size_png_keeps_the_equal_length_shape(tmp_path):
    """J1 的用例版：尺寸与受害者相同 ⇒ 照抄受害者的 mip 数、对象长度一个字节不变。"""
    fx = make_texvar_fixture(tmp_path)
    out = tmp_path / "out_same.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_same, out=out, victim=fx.victim)
    assert plan.shape_unchanged is True
    assert plan.mip_count == VICTIM[2]
    assert len(plan.payload) == textures.dxt_payload_size(64, 64, ti.DXT5, 7) == SAME_PAYLOAD
    victim_edit = next(e for e in plan.edits if e.path_id == fx.victim)
    assert victim_edit.bytes_before == victim_edit.bytes_after   # 等长档：长度没变
    assert plan.problems == ()
    assert "（等长）" in "\n".join(plan.lines())


def test_variable_size_png_lands_all_six_fields(tmp_path):
    """J2：PNG 尺寸 != 受害者 ⇒ 出包成功；六个读数逐条对上（尺子自己也会跑一遍）。"""
    fx = make_texvar_fixture(tmp_path)
    out = tmp_path / "out_var.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_var, out=out, victim=fx.victim)
    assert (plan.image_in.width, plan.image_in.height) == (128, 48)
    assert plan.shape_unchanged is False and plan.problems == ()
    assert plan.mip_count == 8 == ti.full_mip_count(128, 48)
    want = textures.dxt_payload_size(128, 48, ti.DXT5, 8)
    rb = ti.read_texture_back(out, fx.victim)
    assert (rb.width, rb.height) == (128, 48)                  # m_Width / m_Height
    assert rb.texture_format == ti.DXT5                        # m_TextureFormat
    assert rb.mip_count == 8                                   # m_MipCount（满链到 1x1）
    assert rb.complete_image_size == want                      # m_CompleteImageSize
    assert rb.offset == 0                                      # m_StreamData.offset
    assert rb.size == len(plan.payload) == want                # m_StreamData.size
    assert rb.stream_node_size == len(plan.payload)            # 新包 .resS 节点 size
    assert rb.pixels == plan.payload                           # .resS 里就是我们的像素
    assert ti.verify_texture_out(out, fx.victim, expect_width=128, expect_height=48,
                                 expect_mip_count=8, expect_payload=plan.payload) == ()
    assert "（★ 变长：六个数一起落位）" in "\n".join(plan.lines())


def test_mips_override_differs_from_the_victim_and_still_lands(tmp_path):
    """mip 数与受害者不同（尺寸相同）也收 —— `m_CompleteImageSize` / size 必须跟着走。"""
    fx = make_texvar_fixture(tmp_path)
    out = tmp_path / "out_mips.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_same, out=out, victim=fx.victim, mips=4)
    assert plan.mip_count == 4 and plan.shape_unchanged is False
    want = textures.dxt_payload_size(64, 64, ti.DXT5, 4)
    rb = ti.read_texture_back(out, fx.victim)
    assert (rb.mip_count, rb.complete_image_size, rb.size) == (4, want, want)
    assert (rb.width, rb.height) == (64, 64)
    assert plan.problems == ()


def test_mips_beyond_the_full_chain_is_rejected(tmp_path):
    fx = make_texvar_fixture(tmp_path)
    with pytest.raises(AssetError, match="超过满链"):
        ti.plan_texture_apply(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                              png=fx.png_same, out=tmp_path / "o.bundle", victim=fx.victim,
                              mips=9)
    with pytest.raises(AssetError, match=">= 1"):
        ti.plan_texture_apply(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                              png=fx.png_same, out=tmp_path / "o2.bundle", victim=fx.victim,
                              mips=0)


def test_format_only_sabotage_goes_red_on_the_same_ruler(tmp_path):
    """★ J3：造「只改 m_TextureFormat、其余三字段留旧值」的出包 ⇒ 同一把尺子必须报红。

    正控与负控在**同一条用例**里：好包必须 0 条红，坏包必须非空 —— 否则尺子是假的。
    """
    fx = make_texvar_fixture(tmp_path)
    good_out = tmp_path / "good.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_same, out=good_out, victim=fx.victim)
    assert ti.verify_texture_out(good_out, fx.victim) == ()          # 正控：好包必须绿
    sab = _sabotage_format_only(fx, tmp_path / "sab.bundle", payload=plan.payload)
    problems = ti.verify_texture_out(sab, fx.victim)   # ⛔ 一条外部期望都不给
    assert problems, "「只改 m_TextureFormat」的坏包必须被这把尺子拦下"
    assert any("m_CompleteImageSize" in p for p in problems), problems
    rb = ti.read_texture_back(sab, fx.victim)
    assert rb.texture_format == ti.DXT5
    assert rb.complete_image_size == VICTIM[4]      # 长度字段还是旧值（这就是那处坑）
    assert rb.size == VICTIM[4]
    assert rb.stream_node_size == len(plan.payload)  # 而 .resS 里已经是新载荷了


# --------------------------------------------------------------------------- #
# A5 自足化：闭包里**其它**流式贴图的纹素也要跟过来（2026-10-11 · 交接 §3 第 5 条）
# --------------------------------------------------------------------------- #
def test_a5_moves_other_streams_verbatim_and_points_them_at_this_package(tmp_path):
    """J1：闭包里每张 Texture2D 都指本包 .resS，纹素与源包对应区间**逐字节相同**。"""
    extras = (ExtraStream("Layer_Detail", 200, 300), ExtraStream("_Emission", 0, 100))
    fx = make_texvar_fixture(tmp_path, extra_streams=extras)
    src = _source_res_s(fx)
    out = tmp_path / "out_a5.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_same, out=out, victim=fx.victim)
    assert plan.stream_stuck == () and plan.self_sufficient is True
    assert {m.path_id for m in plan.stream_moved} == set(fx.extra_pids)
    # 每段起点是 16 的倍数（交接 §4 第 4 条：流追加对齐 16）
    assert all(m.offset % ti.STREAM_APPEND_ALIGN == 0 for m in plan.stream_moved)
    readings = {r.path_id: r for r in ti.read_stream_textures(out)}
    for m in plan.stream_moved:
        r = readings[m.path_id]
        # ① 每张的 m_StreamData.path == 本包 .resS 节点名
        assert r.stream_path.rsplit("/", 1)[-1] == r.stream_node_path
        # ② 每张的纹素与源包对应区间**逐字节相同**（⛔ 原样搬，没重编码）
        assert r.pixels == src[m.source_offset:m.source_offset + m.size]
        assert r.pixels_sha256() == m.sha256
        assert (r.texture_format, r.mip_count, r.complete_image_size) == (VICTIM[3], 5, m.size)
    assert ti.foreign_streams(out) == ()
    assert "指向本包外 0" in _a5_reading(out)        # check-model 那条独立读数
    # .resS 总长 = 我们的载荷 ＋ 对齐补位 ＋ 两段搬来的字节
    assert plan.stream_bytes >= plan.payload_bytes + sum(e.size for e in extras)


def test_a5_reports_the_ones_it_cannot_move(tmp_path):
    """J2 负控：源包里**也没有**那段字节 / 指着别的包 ⇒ 必须如实报出来（不许静默算绿）。"""
    extras = (ExtraStream("Layer_Detail", 900, 400),      # 900+400 > 源包 .resS 的 1,000 B
              ExtraStream("_Emission", 0, 100,
                          path="archive:/CAB-0badc0de/CAB-0badc0de.resS"))
    fx = make_texvar_fixture(tmp_path, extra_streams=extras)
    out = tmp_path / "out_a5_stuck.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_same, out=out, victim=fx.victim)
    assert plan.stream_moved == () and plan.self_sufficient is False
    assert {s.path_id for s in plan.stream_stuck} == set(fx.extra_pids)
    reasons = " | ".join(s.reason for s in plan.stream_stuck)
    assert "装不下" in reasons and "不在源包里" in reasons, reasons
    # 出包读回：那两张还是落在包外 ⇒ 同一把尺子必须报出来（不是"没搬就等于没事"）
    assert {f.path_id for f in ti.foreign_streams(out)} == set(fx.extra_pids)
    # ⛔ 但 apply_texture 不许因此抛：我们**如实报过**了（CLI 会逐张印出来 + 退出码 1）
    assert plan.problems == ()
    assert f"指向本包外 {len(extras)}" in _a5_reading(out)


def test_verify_streams_out_is_green_then_red_on_a_doctored_move(tmp_path):
    """尺子正控：好包必须绿；把记录改一个字节（sha256）就必须红。"""
    fx = make_texvar_fixture(tmp_path, extra_streams=(ExtraStream("Layer_Detail", 200, 300),))
    out = tmp_path / "out_ctl.bundle"
    plan = ti.apply_texture(bundle=fx.bundle, material="TexVarMat", slot=SLOT,
                            png=fx.png_same, out=out, victim=fx.victim)
    moved = plan.stream_moved
    assert ti.verify_streams_out(out, moved=moved) == ()          # 正控：好包绿
    assert ti.verify_streams_out(out, moved=(replace(moved[0], sha256="0" * 64),))
    red = ti.verify_streams_out(out, moved=(replace(moved[0], offset=moved[0].offset + 16),))
    assert any("offset/size" in p for p in red), red
    # ⛔ 只把"搬不动"登记成允许，别的落包外一律红
    assert ti.verify_streams_out(out, expect_foreign=[]) == ()   # 这张包本来就自足
    assert ti.verify_streams_out(out, moved=moved, expect_foreign=set()) == ()
