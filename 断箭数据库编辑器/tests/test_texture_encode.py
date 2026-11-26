r"""L5 · BC7 / BC4 编码接线的机械判据（产品件 = asset_kernel/texture_encode.py）。

## 这把尺子量什么（每条都带正控 / 负控）

* **P1 解得回**：编码 -> `texture2ddecoder.decode_bc7` -> 像素长度 == w×h×4，
  且与源图（**翻过行的**）的 MAE 落在 BC7 该有的档位；
* **P2 真的是 BC7**：同一张噪声图 MAE(BC7) `<` MAE(DXT5)（实测 **21.53 vs 36.21**）——
  只把格式码改成 25、字节还是 DXT5 的话这条红；
* **P3 满链**：`full_mip_count(256,128)=9` · `(2048,2048)=12` ·
  2048² BC7 满 12 级 = **5,592,432 B**（少烘一级就红）；
* **P4 非 4 倍数**：101x67 一级 `26x17x16 = 7072 B`、解回 27,068 B（不补块直接编会抛）；
* **P5 单通道**：灰图 -> BC4，解回 G/B **全 0**、R 的 MAE 实测 0.544；
* **N1 ★ 格式码写错（25 -> 12）必须红** —— 交接点名的那条负控：
  真包读回 `m_TextureFormat=12` 而判据期望 25；
* **N2 长度留旧值必须红**：树里 `m_CompleteImageSize` 故意写 1234；
* **N3 Crunched 编不出来必须指名报**：`encodable_format(28/29)` 抛且理由里有 crn；
* **N4 ⛔ 不许调坏的 etcpak 解码器**：扫源码 AST，`decompress_bc7` 出现 **0** 次；
* **M M5 没变宽成「什么都行」**：10/12 仍绿、20（BC5）/48（ASTC）仍**红**、
  25/26 变绿（L5 的预期变更）。

## 负控 N1 的实测输出（本机，原样；脚本 = _work/l5/negative_control.py）

    [负控]  坏包已写: _work\l5\out\L5_WRONG_fmt12.bundle 48524 B
    [负控]  读回  : m_TextureFormat = 12 ( DXT5 ) · 256 x 128 · mip 9 · 流 size 43728
    [负控]  判据(拿对的期望量坏包) => 红 · 红 1 条
            X m_TextureFormat 12 != 25（BC7）

## 夹具从哪来（**不开 units 包**）

* PNG 在 `tmp_path` 里现造（确定性 LCG 伪噪声 / 灰阶梯），不依赖任何素材文件；
* 「类型树」从游戏里那个 **1.08 MB 的小真包**（`ammoicons*.bundle`）取元数据区 ——
  与 tests/test_build_min_bundle.py 同一个姿势；没装游戏就 **skip**（⛔ 不造假 schema）。
  ⚠ units 包 3.38 GB 是**重活**（一把锁串行）⇒ ⛔ 本文件不开它。

判据一行：`pytest tests/test_texture_encode.py -q -n 0` -> 通过数从 0 变 N（含 4 条负控）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import texture_build as tb
from ba_mod_maker.asset_kernel import texture_encode as te
from ba_mod_maker.asset_kernel import texture_install as ti

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SRC = REPO_ROOT / "src" / "ba_mod_maker" / "asset_kernel" / "texture_encode.py"
SOURCE_PREFIX = "ammoicons"
ASSET_PATH = "Assets/Resources_moved/L5/L5_Albedo_256x128.png"


# --------------------------------------------------------------------------- #
# 夹具：确定性 PNG ＋ 真包的元数据区
# --------------------------------------------------------------------------- #
def _write_rgba_png(path: Path, width: int, height: int) -> Path:
    """确定性伪噪声 RGBA 图（LCG，⛔ 不 random）—— 块压缩的好坏在小图上分得开。"""
    pil = pytest.importorskip("PIL.Image")
    image = pil.new("RGBA", (width, height))
    px = image.load()
    seed = 12345
    for y in range(height):
        for x in range(width):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            n = (seed >> 16) & 0x3F
            r = min(255, (x * 200) // max(1, width - 1) + n)
            g = min(255, (y * 200) // max(1, height - 1) + n)
            px[x, y] = (r, g, min(255, 64 + n), 255)
    image.save(path)
    return path


def _write_gray_png(path: Path, width: int, height: int) -> Path:
    """单通道灰图（BC4 那条路）。"""
    pil = pytest.importorskip("PIL.Image")
    image = pil.new("L", (width, height))
    px = image.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = (x * 3 + y * 2) % 256
    image.save(path)
    return path


def _bundle_dir() -> Path | None:
    from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

    root = find_game_root()
    return bundle_dir(root) if root is not None else None


def _source_bundle() -> Path:
    base = _bundle_dir()
    hits = sorted(base.glob(f"{SOURCE_PREFIX}*.bundle")) if base is not None else []
    if not hits:
        pytest.skip(f"找不到 {SOURCE_PREFIX}*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return hits[0]


@pytest.fixture(scope="module")
def source_meta() -> bytes:
    """真包的元数据区（类型树唯一来源）—— 只读，1.08 MB 的小包。"""
    return te.read_source_meta(_source_bundle())


def _mae(a: bytes, b: bytes) -> float:
    assert len(a) == len(b)
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def _unity_rows(png: Path) -> bytes:
    """源图翻成 Unity 的行序（自下而上）—— 解回的字节是**这个序**。"""
    image = ti.read_png(png)
    return bytes(ti.flip_rows(image.pixels, image.width, image.height))


def _pid_of(plan: builder.ObjectBundlePlan) -> int:
    return int(plan.container[0][1])


def _write(plan: builder.ObjectBundlePlan, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(plan.data))
    return out


# --------------------------------------------------------------------------- #
# P1 / P2 · 正控：编得出、解得多、而且**真的是 BC7**
# --------------------------------------------------------------------------- #
def test_p1_bc7_encodes_and_decodes_back(tmp_path: Path) -> None:
    """BC7 编一张 256x128：格式码 25、满链 9 级、长度合公式、解回像素长度对且像源图。"""
    png = _write_rgba_png(tmp_path / "L5_P1.png", 256, 128)
    enc = te.encode_png(png, format_id=te.BC7)
    assert enc.format == 25 == te.BC7
    assert (enc.width, enc.height) == (256, 128)
    assert enc.mip_count == te.full_mip_count(256, 128) == 9
    assert enc.payload_bytes == te.payload_size(256, 128, te.BC7, 9)
    assert enc.level_sizes == te.level_sizes(256, 128, te.BC7, 9)
    assert enc.level_sizes[-1] == 16, "末级 1x1 的 BC7 是一块 = 16 B"
    back = te.decode_level(enc)
    assert len(back) == 256 * 128 * 4
    mae = _mae(_unity_rows(png), back)
    assert mae < 30, f"BC7 解回与源图差太多（MAE {mae:.2f}）—— 编码器是不是没走 BC7？"


def test_p2_bc7_really_beats_dxt5_on_the_same_image(tmp_path: Path) -> None:
    """同一张噪声图：**BC7 必须比 DXT5 好**（实测 21.53 vs 36.21）。

    只把格式码改成 25、字节还是 DXT5 的话这条**当场红** —— 这是「格式码」之外的第二把锁。
    """
    png = _write_rgba_png(tmp_path / "L5_P2.png", 256, 128)
    src = _unity_rows(png)
    mae: dict[int, float] = {}
    for fmt in (te.BC7, te.DXT5, te.DXT1):
        enc = te.encode_png(png, format_id=fmt, mip_count=1)
        mae[fmt] = _mae(src, te.decode_level(enc))
    assert mae[te.BC7] < mae[te.DXT5] * 0.8, f"BC7 没比 DXT5 好：{mae}"
    assert mae[te.DXT5] <= mae[te.DXT1] + 1e-6, f"DXT5 不该比 DXT1 差：{mae}"


# --------------------------------------------------------------------------- #
# P3 · mip 链 = 满链（口径只有一份）
# --------------------------------------------------------------------------- #
def test_p3_full_chain_counts_and_anchors() -> None:
    """满链级数 + 两个 W6 锚点（2048² 12 级 = 5,592,432 B · 512² 10 级 = 349,552 B）。"""
    assert te.full_mip_count(256, 128) == 9
    assert te.full_mip_count(64, 32) == 7
    assert te.full_mip_count(2048, 2048) == 12
    assert te.full_mip_count(1024, 512) == 11
    assert te.payload_size(2048, 2048, te.BC7, 12) == 5_592_432, "W6 锚点"
    assert te.payload_size(512, 512, te.BC7, 10) == 349_552, "W6 锚点"
    assert te.level_sizes(256, 128, te.BC7, 9)[0] == 64 * 32 * 16 == 32_768
    assert te.payload_size(64, 64, te.BC4, 7) < te.payload_size(64, 64, te.BC7, 7)


def test_p3b_default_mip_count_is_the_full_chain(tmp_path: Path) -> None:
    png = _write_rgba_png(tmp_path / "L5_P3b.png", 64, 32)
    enc = te.encode_png(png, format_id=te.BC7)
    assert enc.mip_count == te.full_mip_count(64, 32) == 7
    assert enc.payload_bytes == te.payload_size(64, 32, te.BC7, 7)
    with pytest.raises(te.AssetError) as excinfo:
        te.encode_pixels(bytes(64 * 32 * 4), 64, 32, mip_count=99)
    assert "超过满链" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# P4 · 边界：尺寸不是 4 的倍数（编码器要求 4 的倍数，靠补块）
# --------------------------------------------------------------------------- #
def test_p4_size_not_multiple_of_4_is_padded_and_still_decodes(tmp_path: Path) -> None:
    """101x67：一级 `26x17x16 = 7072 B`，解回来还是 101x67x4 —— 补块没有污染主像素。"""
    png = _write_rgba_png(tmp_path / "L5_P4.png", 101, 67)
    enc = te.encode_png(png, format_id=te.BC7)
    assert enc.width % 4 == 1 and enc.height % 4 == 3, "这一条要的是**真的**非 4 倍数"
    assert enc.level_sizes[0] == 26 * 17 * 16 == 7072
    back = te.decode_level(enc)
    assert len(back) == 101 * 67 * 4
    mae = _mae(_unity_rows(png), back)
    assert mae < 30, f"非 4 倍数尺寸解回偏了（MAE {mae:.2f}）"


def test_p4b_etcpak_refuses_non_multiple_of_4_by_itself() -> None:
    """正控的反面：**不补块**直接喂 etcpak 必须抛 —— 这就是 `_pad_to_block` 存在的理由。"""
    etcpak = pytest.importorskip("etcpak")
    with pytest.raises(ValueError) as excinfo:
        etcpak.compress_bc7(bytes(101 * 67 * 4), 101, 67, None)
    assert "multiple of 4" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# P5 · 边界：单通道图走 BC4
# --------------------------------------------------------------------------- #
def test_p5_single_channel_image_goes_bc4(tmp_path: Path) -> None:
    """灰图 -> BC4（fmt 26）：块 8 B、解回**只有 R 有值**、R 的 MAE 实测 0.544。"""
    png = _write_gray_png(tmp_path / "L5_Mask_64x64.png", 64, 64)
    enc = te.encode_png(png, format_id=te.SINGLE_CHANNEL_FORMAT, mip_count=1)
    assert enc.format == 26 == te.BC4
    assert enc.payload_bytes == 16 * 16 * 8 == 2048, "BC4 是 8 B/块"
    back = te.decode_level(enc)
    assert len(back) == 64 * 64 * 4
    assert max(back[1::4]) == 0 and max(back[2::4]) == 0, "BC4 只保 R：G/B 必须全 0"
    src = _unity_rows(png)
    mae = _mae(src[0::4], back[0::4])
    assert mae < 4, f"BC4 的 R 通道差太多（MAE {mae:.3f}）"


def test_p5b_channel_gate_flags_a_color_image(tmp_path: Path) -> None:
    """单通道门：灰图判成单通道；彩色噪声图一个都不是 —— 这条读数**能红**。"""
    gray = ti.read_png(_write_gray_png(tmp_path / "g.png", 32, 32))
    gstats = te.channel_stats(gray.pixels, 32, 32)
    assert gstats["same_rgb_pixels"] == gstats["total"] == 1024
    color = ti.read_png(_write_rgba_png(tmp_path / "c.png", 32, 32))
    cstats = te.channel_stats(color.pixels, 32, 32)
    assert cstats["same_rgb_pixels"] < cstats["total"] * 0.5, "噪声图不该被判成单通道"
    assert cstats["total"] == 1024


# --------------------------------------------------------------------------- #
# N1 · ★ 交接点名的负控：**格式码写错（25 -> 12）必须当场红**
# --------------------------------------------------------------------------- #
def test_n1_wrong_format_code_25_to_12_must_go_red(tmp_path: Path, source_meta: bytes) -> None:
    """真包、真字节、真的写一个坏包出来 —— 只把 `m_TextureFormat` 从 25 改成 12。

    ⚠ BC7 与 DXT5 的块都是 16 B ⇒ **长度一模一样**，出包里唯一能看出问题的就是格式码。
    所以这条负控同时也是「格式码那一条判据真的在管事」的证明：

    * 拿**坏包自己的**期望去量 ⇒ 自洽，必须绿（证明这把尺子不是乱红）；
    * 拿**对的**期望去量 ⇒ 必须红，而且红的那一行点名 `m_TextureFormat 12 != 25`。
    """
    png = _write_rgba_png(tmp_path / "L5_N1.png", 256, 128)
    good = te.encode_png(png, format_id=te.BC7)
    bad = dataclasses.replace(good, format=te.DXT5)  # ★ 格式码写错：25 -> 12
    plan = te.build_encoded_bundle(
        encoded=bad,
        asset_path=ASSET_PATH,
        source_meta=source_meta,
        bundle_name="L5_WRONG_25to12.bundle",
    )
    out = _write(plan, tmp_path / "L5_WRONG_fmt12.bundle")
    pid = _pid_of(plan)
    reading = ti.read_texture_back(out, pid)
    assert reading.texture_format == 12, "坏包里的格式码没写进去"
    assert reading.size == good.payload_bytes, "字节还是那份 BC7 的（两者块大小相同）"
    assert reading.complete_image_size == good.payload_bytes
    assert te.verify_encoded_out(out, pid, encoded=bad) == (), "自洽的坏包在这把尺子下是绿的"
    problems = te.verify_encoded_out(out, pid, encoded=good)
    assert problems, "★ 格式码写错却判绿 —— 这把尺子是坏的"
    assert any("m_TextureFormat 12 != 25" in p for p in problems), problems


def test_n1b_the_good_bundle_passes_the_same_ruler(tmp_path: Path, source_meta: bytes) -> None:
    """正控的另一半：**同一把尺子**量我们自己的好包必须**绿**（空元组）。"""
    png = _write_rgba_png(tmp_path / "L5_N1b.png", 256, 128)
    enc = te.encode_png(png, format_id=te.BC7)
    plan = te.build_encoded_bundle(
        encoded=enc,
        asset_path=ASSET_PATH,
        source_meta=source_meta,
        bundle_name="L5_GOOD_bc7.bundle",
    )
    out = _write(plan, tmp_path / "L5_GOOD_bc7.bundle")
    pid = _pid_of(plan)
    reading = ti.read_texture_back(out, pid)
    assert reading.texture_format == 25, "包内 m_TextureFormat 必须是 25（BC7）"
    assert (reading.width, reading.height) == (256, 128)
    assert reading.mip_count == enc.mip_count == 9
    assert reading.pixels == enc.payload, "`.resS` 里那一段必须与载荷逐字节相同"
    assert reading.offset == 0
    assert reading.stream_node_size == enc.payload_bytes
    assert te.verify_encoded_out(out, pid, encoded=enc,
                                 expect_stream_node_size=enc.payload_bytes) == ()


# --------------------------------------------------------------------------- #
# N2 · 工单 §7 坑 2：**只改格式、长度留旧值**必须红
# --------------------------------------------------------------------------- #
def test_n2_stale_complete_image_size_must_go_red(tmp_path: Path, source_meta: bytes) -> None:
    png = _write_rgba_png(tmp_path / "L5_N2.png", 256, 128)
    enc = te.encode_png(png, format_id=te.BC7)
    node = builder.node_path_for("L5_WRONG_len.bundle")
    fields = tb.build_streaming_texture_fields(
        "L5_N2",
        enc.width,
        enc.height,
        texture_format=te.BC7,
        mip_count=enc.mip_count,
        size=enc.payload_bytes,
        stream_path=te.stream_path_for(node),
        offset=0,
        readable=False,
    )
    fields["m_CompleteImageSize"] = 1234  # ★ 故意留旧值
    pid = builder.path_id_for("L5_WRONG_len|" + ASSET_PATH)
    plan = builder.build_object_bundle(
        objects=[
            builder.ObjectSpec(
                class_id=builder.CLASS_TEXTURE2D, fields=fields, path_id=pid, label="n2"
            )
        ],
        bundle_name="L5_WRONG_len.bundle",
        source_meta=source_meta,
        container=[(ASSET_PATH, pid)],
        node_path=node,
        preload_all=True,
        stream_payload=enc.payload,
    )
    out = _write(plan, tmp_path / "L5_WRONG_len.bundle")
    problems = te.verify_encoded_out(out, pid, encoded=enc)
    assert any("m_CompleteImageSize" in p for p in problems), problems


# --------------------------------------------------------------------------- #
# N3 · Crunched 编不出来：必须**指名报**（⛔ 不静默换格式）
# --------------------------------------------------------------------------- #
def test_n3_crunched_is_refused_by_name() -> None:
    for fmt in (28, 29):
        assert fmt in te.CRUNCHED_FORMATS
        with pytest.raises(te.AssetError) as excinfo:
            te.encodable_format(fmt)
        message = str(excinfo.value)
        assert "crn" in message and "编码器" in message, message
        assert "Unity" in message, "必须指出可行的那条路（借 Unity 编辑器出）"
    with pytest.raises(te.AssetError):
        te.format_of_name("crunched")
    with pytest.raises(te.AssetError):
        te.encodable_format(48)  # ASTC 也编不动
    assert 28 not in te.FORMAT_BY_NAME.values() and 29 not in te.FORMAT_BY_NAME.values()


# --------------------------------------------------------------------------- #
# N4 · ⛔ 不许调坏的 etcpak 解码器（扫源码 —— 真调它会崩进程，测不了）
# --------------------------------------------------------------------------- #
def test_n4_the_broken_etcpak_decoder_is_never_referenced() -> None:
    """`etcpak.decompress_bc7` 实测：64x64 **0xC0000005 访问违例**（进程当场死）、

    4x4 `MemoryError`、8x8 返回垃圾字节 ⇒ 这条探针**只能扫源码**（真调会崩掉 pytest 进程）。
    「解回」一律走 `texture2ddecoder`（独立实现）。
    """
    import ast

    tree = ast.parse(MODULE_SRC.read_text(encoding="utf-8"))
    hits = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == "decompress_bc7")
        or (isinstance(node, ast.Name) and node.id == "decompress_bc7")
    ]
    assert not hits, f"模块里出现了 {len(hits)} 处 decompress_bc7 —— 那个函数会崩进程"
    assert te.DECODER_NAMES[te.BC7] == "decode_bc7"
    assert te.DECODER_NAMES[te.BC4] == "decode_bc4"
    assert set(te.DECODER_NAMES) == set(te.textures.ENCODABLE_FORMATS)


# --------------------------------------------------------------------------- #
# M · M5「可编码集」变宽了**但没变成什么都行**
# --------------------------------------------------------------------------- #
def test_m_encodable_set_widened_exactly_by_the_new_formats() -> None:
    """L5 把可编码集从 {10,12} 加宽到 {10,12,25,26}（BC7/BC4 现在真能编了）。

    Lead 2026-10-17 要求把这一点**量出来**：旧行为一条都没松 ——
    BC5(20) / ASTC(48) 照样红，DXT1(10) / DXT5(12) 照样绿。
    """
    from ba_mod_maker.asset_kernel import material_check as mc
    from ba_mod_maker.asset_kernel import textures

    assert textures.ENCODABLE_FORMATS == (10, 12, 25, 26)
    assert textures.BLOCK_BYTES == {10: 8, 12: 16, 25: 16, 26: 8}
    for fmt in (10, 12, 25, 26):
        assert mc.m5_texture_format(texture_format=fmt).ok, f"格式 {fmt} 该是可编的"
    for fmt in (20, 48, 28, 29, 47):
        assert not mc.m5_texture_format(texture_format=fmt).ok, f"格式 {fmt} 不该被放行"
    reading = mc.m5_texture_format(texture_format=25).readings
    assert "在 可编码集" in reading[0]


# --------------------------------------------------------------------------- #
# 端到端：CLI（干跑 + 写 + 读回）
# --------------------------------------------------------------------------- #
def test_cli_build_dry_run_writes_nothing_and_write_reads_back(tmp_path: Path) -> None:
    """`texture build` 干跑不落盘；给了 `--out` 就写 ＋ 读回（rc=0）。"""
    import argparse

    from ba_mod_maker.cli import texture as clip

    bundle = _source_bundle()
    png = _write_rgba_png(tmp_path / "L5_CLI.png", 128, 64)
    asset = "Assets/Resources_moved/L5/L5_CLI_128x64.png"
    out = tmp_path / "L5_CLI_bc7.bundle"
    argv = [
        "build",
        "--png", str(png),
        "--source", str(bundle),
        "--asset-path", asset,
        "--format", "bc7",
        "--out", str(out),
    ]
    sub = argparse.ArgumentParser().add_subparsers()
    parser = clip.add_parser(sub)
    assert clip.run(parser.parse_args(argv)) == 0
    assert out.is_file() and out.stat().st_size > 0
    info = builder.reopen_container(out)
    assert str(info["nodes"][-1][0]).endswith(".resS"), "双节点：CAB-<hash> + CAB-<hash>.resS"
    before = sorted(p.name for p in tmp_path.iterdir())
    dry = [a for a in argv if a not in ("--out", str(out))]
    assert clip.run(parser.parse_args(dry)) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "干跑不许落盘"


# --------------------------------------------------------------------------- #
# 接进既有资产链 · 外科手术重编（真件包；没装游戏就 skip）
# --------------------------------------------------------------------------- #
def _unitmod_bundle() -> Path:
    """`unitmodifications_*.bundle`（2.2 MB，2fa01537ccaa7d0c5a24ec0870b5b1ee）—— **只读**。"""
    base = _bundle_dir()
    hits = sorted(base.glob("unitmodifications_*.bundle")) if base is not None else []
    if not hits:
        pytest.skip("找不到 unitmodifications_*.bundle（没装游戏 / BA_MOD_MAKER_GAME 指错了）")
    return hits[0]


def _dir_snapshot(path: Path) -> list[tuple[str, int, int]]:
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in path.iterdir())


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_dxt5(path: Path) -> int:
    """挑第一张 DXT5(12) 且宽高都是 4 的倍数的贴图当靶子（⛔ 不写死 pathID）。"""
    hits = [
        t for t in ti.read_stream_textures(path)
        if t.texture_format == te.DXT5 and t.width % 4 == 0 and t.height % 4 == 0
    ]
    assert hits, "这个包里没有 DXT5 的流式贴图 —— 换个包"
    return int(hits[0].path_id)


def test_reencode_is_surgical_and_leaves_the_game_dir_alone(tmp_path: Path) -> None:
    """外科手术改包：靶子变 BC7、其余 162 张逐字节没变、反向 Δ 小、游戏目录一个字节没动。

    实测读数（本机）：2,217,910 -> 2,220,004 B · 其余 162 张逐字节没变 ·
    柜子 73,319 B = **3.30%**（整包重写那条路是 99.82% ⇒ 那是拷整包，⛔ 不许）。
    """
    src = _unitmod_bundle()
    aa = src.parent
    before_dir = _dir_snapshot(aa)
    before_sha = _sha256(src)
    pid = _first_dxt5(src)
    proj = tmp_path / "proj"
    out = tmp_path / src.name
    plan = te.reencode_in_bundle(src, path_id=pid, out=out, project_root=proj)
    assert plan.ok, plan.problems
    assert plan.format_before == 12 and plan.format_after == 25
    assert plan.stream_after == plan.stream_before, "等长：DXT5 与 BC7 都是 16 B/块"
    assert plan.others_changed == (), f"不该动的贴图跟着变了：{plan.others_changed}"
    assert plan.others_checked > 100, f"复核覆盖面太小：{plan.others_checked}"
    # 读回：包内 m_TextureFormat 必须是 25，且 .resS 那一段 == 我们的载荷
    reading = ti.read_texture_back(out, pid)
    assert reading.texture_format == 25
    assert reading.pixels == plan.payload_after
    assert (reading.width, reading.height) == (plan.width, plan.height)
    assert reading.mip_count == plan.mip_count
    # 产品自己的写后复核（F2/F3/G2/G3/F4/F5/F7/F5b）—— 红一条就不算过
    assert plan.verify, "没有拿到产品那两条写后复核的读数"
    red = [k for k, ok, _d in plan.verify if not ok]
    assert not red, f"产品的写后复核红了：{red}"
    # 反向 Δ：柜子（JSON 清单 ＋ .bin 原始字节）必须远小于整包
    assert plan.patch is not None and plan.patch.is_file()
    assert plan.patch_bytes > 0
    ratio = plan.patch_bytes / len(plan.data)
    assert ratio < 0.10, f"反向 Δ 占整包 {ratio:.2%} —— 这不是外科手术（⛔ 不许拷整包）"
    # ★ 游戏目录：一个字节都没动
    assert _sha256(src) == before_sha, "源包被改了！"
    assert _dir_snapshot(aa) == before_dir, "游戏目录 aa/PC 被动过！"


def test_reencode_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = _unitmod_bundle()
    before_sha = _sha256(src)
    plan = te.reencode_in_bundle(src, path_id=_first_dxt5(src))
    assert plan.out is None and plan.data == b""
    assert len(plan.plans) == 2, "干跑要把两条外科计划都算出来"
    assert {row["step"] for row in plan.plans} == {"objects", "stream"}
    assert _sha256(src) == before_sha


def test_negative_out_inside_the_game_dir_is_refused(tmp_path: Path) -> None:
    """⛔ **装机红线负控**：`--out` 指进游戏目录 ⇒ 当场拒 ＋ **零写入**。

    零写入的尺子 = ① 目标文件不存在 ② `aa/PC` 逐条（名/长/mtime）前后相同 ③ 源包 sha 未变。
    """
    src = _unitmod_bundle()
    aa = src.parent
    before_dir = _dir_snapshot(aa)
    before_sha = _sha256(src)
    bad = aa / "L5_SHOULD_NOT_EXIST.bundle"
    with pytest.raises(te.AssetError) as excinfo:
        te.reencode_in_bundle(src, path_id=_first_dxt5(src), out=bad, project_root=tmp_path)
    message = str(excinfo.value)
    assert "游戏目录" in message, message
    assert str(bad) in message, "拒的时候必须**点名那条路径**"
    assert not bad.exists(), "★ 拒了却还是写了 —— 这条闸是假的"
    assert _sha256(src) == before_sha
    assert _dir_snapshot(aa) == before_dir


def test_guard_out_path_positive_inside_workspace(tmp_path: Path) -> None:
    """正控：工程目录里的落点**照常放行**（闸不能把正常路也挡了）。"""
    te.guard_out_path(tmp_path / "ok.bundle")
    hit, _root = te.inside_game_dir(tmp_path / "ok.bundle")
    assert hit is False


def test_revert_patch_refuses_a_whole_file_delta(tmp_path: Path) -> None:
    """⛔ `write_revert_patch` 的守卫：差异占整包 > 35% 的「Δ」一律拒（那是拷整包）。

    实测由来：第一版 `reencode` 走 `builder` 整包重写 ⇒ 文件级差异 **99.82%**；
    改成 `writer` 外科链之后 **3.30%**。
    """
    old = tmp_path / "old.bin"
    new = tmp_path / "new.bin"
    old.write_bytes(bytes((i * 7) % 251 for i in range(64_000)))
    new.write_bytes(bytes((i * 13 + 5) % 251 for i in range(64_000)))
    with pytest.raises(te.AssetError) as excinfo:
        te.write_revert_patch(old, new, tmp_path / "p.json")
    assert "拷整包" in str(excinfo.value)
    # 正控：只有一小段不同的两个文件**照常**出柜子，而且撤回去逐字节相同
    new2 = tmp_path / "new2.bin"
    payload = bytearray(old.read_bytes())
    payload[100:120] = b"\x00" * 20
    new2.write_bytes(bytes(payload))
    ranges, blob_bytes = te.write_revert_patch(old, new2, tmp_path / "p2.json")
    assert len(ranges) >= 1
    assert 0 < blob_bytes < len(old.read_bytes()), "正控的柜子不该是整包"
    back = te.restore_from_delta(new2, tmp_path / "p2.json", tmp_path / "back.bin")
    assert back == old.read_bytes(), "反向 Δ 撤不回来"