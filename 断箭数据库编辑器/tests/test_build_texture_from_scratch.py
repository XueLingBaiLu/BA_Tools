r"""路线 A · **自造内联 Texture2D** 的机械判据（产品件 = asset_kernel/texture_build.py）。

## 这把尺子量什么（每条都带正 / 负控）

1. **类型树对账**：夹具里那棵真件 classID 28 的类型树，**24 个顶层键**必须与
   TEXTURE_FIELDS **逐字相等**（少一个/改个名 = 写出来的对象会缺字段或错位）。
2. **正控（真件字节）**：4 个真件 Texture2D：read_object -> write_object **逐字节相同**，
   且读出来的读数与夹具里录的一致（宽高/格式/mip/流大小）。
   ⇒ 「类型树当 schema」这条地基先自证，再谈自造。
3. **自造贴图往返**：build_texture_fields(...) -> write -> read，字段树相同；且
   image data 长度 == m_CompleteImageSize == 宽×高×每像素字节。
4. **内联的判据**：m_StreamData 三项全 0（真件 4/4 都是流式，这是**有意偏离**的那一处）。
5. **跨实现对拍**：同一份字段树，UnityPy 写的字节 == 我写的字节；UnityPy 读我的字节 == 我的字段树。
6. **负控**：像素长度对不上 / 像素不是 bytes / 格式是块压缩（DXT1/BC7）/ 宽高非法 —— 一律抛。

## 夹具从哪来（用例**不开包**）

_work/v3a_tex_fixture.json —— 由 _work/v3a-tex-fixture.py 从 units 包（3.38 GB）只读一次生成：
真件类型树原始 blob + 4 个真件 Texture2D 的对象字节（格式 29 ASTC / 12 DXT5 / 28 BC7 / 10 DXT1）。
⚠ units 是**重活**（一把锁串行）⇒ **不在用例里开包**（同 test_metadata_writer.py 的口径）；
没有那个 JSON 时本文件整组 skip。

## 真件读数（2026-10-06 实测）

* units 包 2,725 个 Texture2D，抽样 400/400 **全部流式**（m_ImageData 空 + m_StreamData.size > 0）；
* 抽样格式：{29 ASTC: 243, 12 DXT5: 75, 28 BC7: 65, 10 DXT1: 17} —— 全是块压缩；
* ⇒ 自造包没有 .resS，第一张自造贴图只能**内联**（image data 写在对象里）。

判据一行：pytest tests/test_build_texture_from_scratch.py -q -> 通过数 从 0 变 N。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import clone
from ba_mod_maker.asset_kernel import texture_build as tb

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_JSON = WORK / "v3a_tex_fixture.json"


def _load() -> dict | None:
    if not FIXTURE_JSON.is_file():
        return None
    return json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))


_FIXTURE = _load()
CASES: list[dict] = list(_FIXTURE["cases"]) if _FIXTURE else []
COMMON = clone.unitypy_common_strings()
NODES: tuple[clone.TypeNode, ...] = (
    clone.read_type_nodes(base64.b64decode(_FIXTURE["tree_blob_b64"]), common_strings=COMMON)
    if _FIXTURE
    else ()
)
PARAMS = CASES or [None]
PARAM_IDS = [str(c["slug"]) for c in CASES] or ["missing-fixture"]

pytestmark = pytest.mark.skipif(
    _FIXTURE is None, reason=f"没有夹具 {FIXTURE_JSON.name}（跑 _work/v3a-tex-fixture.py 生成）"
)


def _payload(case: dict) -> bytes:
    return base64.b64decode(case["payload_b64"])


# --------------------------------------------------------------------------- ① 类型树
def test_type_tree_has_exactly_the_declared_top_level_keys() -> None:
    """真件类型树的 24 个顶层键 == TEXTURE_FIELDS（逐字、含顺序）。"""
    got = [n.name for n in NODES if n.level == 1]
    assert got == list(tb.TEXTURE_FIELDS)
    assert "image data" in got, "image data 中间那一个空格是 Unity 自己起的，别改"
    assert len(got) == 24


# --------------------------------------------------------------------------- ② 真件往返
@pytest.mark.parametrize("case", PARAMS, ids=PARAM_IDS)
def test_recorded_textures_roundtrip_byte_identical(case: dict | None) -> None:
    """真件 Texture2D 字节读一遍再写一遍，必须逐字节相同；读数与录的一致。"""
    assert case is not None
    raw = _payload(case)
    fields = clone.read_object(raw, NODES)
    assert clone.write_object(fields, NODES) == raw
    assert set(fields) == set(tb.TEXTURE_FIELDS)
    reading = case["reading"]
    assert int(fields["m_Width"]) == reading["width"]
    assert int(fields["m_Height"]) == reading["height"]
    assert int(fields["m_TextureFormat"]) == reading["format"]
    assert int(fields["m_MipCount"]) == reading["mip_count"]
    assert int(fields["m_CompleteImageSize"]) == reading["complete_image_size"]
    # 真件是**流式**：像素不在对象里 —— 这正是我们要绕开的那件事
    assert len(fields["image data"]) == reading["image_data_len"] == 0
    assert int(fields["m_StreamData"]["size"]) == reading["stream_size"] > 0


# --------------------------------------------------------------------------- ③④ 自造贴图
def test_built_texture_roundtrips_and_is_inline() -> None:
    """自造一张 64×64 RGBA32 粗格纹：往返逐字节 + image data 非空 + stream 清零。"""
    pixels = tb.checker_pixels(64, 64, ((36, 58, 32), (96, 112, 62), (24, 24, 28)))
    assert len(pixels) == 64 * 64 * 4
    fields = tb.build_texture_fields("RouteA_Camo_64", 64, 64, pixels)
    assert set(fields) == set(tb.TEXTURE_FIELDS)
    raw = clone.write_object(fields, NODES)
    back = clone.read_object(raw, NODES)
    assert back == fields
    assert clone.write_object(back, NODES) == raw
    assert back["image data"] == pixels
    assert len(back["image data"]) == int(back["m_CompleteImageSize"]) == 16_384
    assert back["m_StreamData"] == tb.INLINE_STREAM == {"offset": 0, "size": 0, "path": ""}
    assert int(back["m_TextureDimension"]) == 2 and int(back["m_ImageCount"]) == 1


def test_byte_size_follows_the_format() -> None:
    """每像素字节由格式决定：Alpha8=1 / RGB24=3 / RGBA32=4 / R8=1。"""
    for fmt, bpp in ((1, 1), (3, 3), (4, 4), (63, 1)):
        pixels = bytes(8 * 8 * bpp)
        fields = tb.build_texture_fields("t", 8, 8, pixels, texture_format=fmt)
        assert int(fields["m_CompleteImageSize"]) == 8 * 8 * bpp
        assert clone.write_object(fields, NODES) is not None


# --------------------------------------------------------------------------- ⑤ 跨实现
def test_unitypy_writes_and_reads_the_same_bytes() -> None:
    """UnityPy 自己解树、自己写、自己读：字节与字段树两头都必须对上。"""
    pytest.importorskip("UnityPy")
    from UnityPy.helpers.TypeTreeHelper import read_typetree, write_typetree
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode
    from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

    root = TypeTreeNode.parse_blob(
        EndianBinaryReader(base64.b64decode(_FIXTURE["tree_blob_b64"]), "<"), 22
    )
    pixels = tb.checker_pixels(16, 16, ((200, 30, 30), (30, 30, 200)), block=4)
    fields = tb.build_texture_fields("RouteA_crosscheck", 16, 16, pixels)
    mine = clone.write_object(fields, NODES)
    writer = EndianBinaryWriter(endian="<")
    write_typetree(fields, root, writer)
    assert writer.bytes == mine, "UnityPy 写的字节与我的不同"
    theirs = read_typetree(root, EndianBinaryReader(mine, "<"), as_dict=True, byte_size=len(mine))
    assert theirs == fields
    # 真件那一个也过一遍（同一把尺子的正控）
    case = CASES[0]
    real = _payload(case)
    assert read_typetree(root, EndianBinaryReader(real, "<"), as_dict=True,
                         byte_size=len(real)) == clone.read_object(real, NODES)


# --------------------------------------------------------------------------- ⑥ 负控
def test_pixel_length_must_match_width_height_format() -> None:
    with pytest.raises(tb.TextureBuildError) as excinfo:
        tb.build_texture_fields("bad", 8, 8, bytes(8 * 8 * 4 - 1))
    assert "像素长度对不上" in str(excinfo.value)


def test_pixels_must_be_bytes() -> None:
    with pytest.raises(tb.TextureBuildError) as excinfo:
        tb.build_texture_fields("bad", 2, 2, [0, 0, 0, 255] * 4)  # type: ignore[arg-type]
    assert "必须是 bytes" in str(excinfo.value)


def test_block_compressed_has_no_bytes_per_pixel() -> None:
    """⛔ 原「必须抛」那条负控的**真正落点**：块压缩没有「每像素几字节」这个数。

    2026-10-09 改口径：块压缩从「一律拒收」变成「**原样搬运**」（见下一条用例），
    但 bytes_per_pixel() 这条**不许松** —— 28/29 是 crunched 压缩流，长度根本不由宽高决定；
    真松了的话调用方会拿一个不存在的 bpp 去算长度，正是当初要防的事。
    """
    for fmt in (10, 12, 28, 29):
        with pytest.raises(tb.TextureBuildError) as excinfo:
            tb.bytes_per_pixel(fmt)
        assert "块压缩" in str(excinfo.value)
    # 而且不许静默当成无压缩：块压缩格式一律不在 _BPP 里
    assert not (set(tb._BLOCK_FORMATS) & set(tb._BPP))


def test_block_compressed_rides_through_verbatim() -> None:
    """块压缩**原样搬运**：不查长度、不做块编解码、m_CompleteImageSize = len(pixels)。

    实证（2026-10-09 实机）：把游戏真件那张 750,645 B 的 DXT5Crunched 字节原样内联，
    游戏里显示与**原版逐字节一致**（sha256 相同）—— 所以「原样搬运」这条是真的能用的。
    """
    for fmt in (10, 12, 28, 29):
        blob = bytes(1000)                     # 长度**故意**不是 4×4×bpp —— 就是要它不查
        fields = tb.build_texture_fields("blk", 64, 64, blob, texture_format=fmt)
        assert int(fields["m_TextureFormat"]) == fmt
        assert int(fields["m_CompleteImageSize"]) == 1000 == len(fields["image data"])
        assert int(fields["m_MipCount"]) == 1
        back = clone.read_object(clone.write_object(fields, NODES), NODES)
        assert back["image data"] == blob


def test_mip_chain_is_self_proving() -> None:
    """全 mip 链：给了 mip_levels 就按链走，而且**链头必须与 pixels 逐字节相同**（自证）。

    实证：真件 2048² 是 12 级；此前本模块只造单 mip，几刀都得手搓（cut13 / loop1 / demo2）。
    """
    w, h = 4, 8
    red, blue = bytes((255, 0, 0, 255)), bytes((0, 0, 255, 255))
    px = red * w * 4 + blue * w * 4            # 上 4 行红、下 4 行蓝

    def _half(buf: bytes, hw: int, hh: int) -> bytes:
        out = bytearray()
        for y in range(max(1, hh // 2)):
            for x in range(max(1, hw // 2)):
                i = ((y * 2) * hw + (x * 2)) * 4
                out += buf[i : i + 4]
        return bytes(out)

    levels = [px]
    cw, ch = w, h
    while cw > 1 or ch > 1:
        nw, nh = max(1, cw // 2), max(1, ch // 2)
        levels.append(_half(levels[-1], cw, ch))
        cw, ch = nw, nh
    assert len(levels) == 4 and (cw, ch) == (1, 1)
    total = sum(len(x) for x in levels)

    fields = tb.build_texture_fields("mipped", w, h, px, mip_levels=levels)
    assert int(fields["m_MipCount"]) == len(levels)
    assert int(fields["m_CompleteImageSize"]) == total == len(fields["image data"])
    assert fields["image data"] == b"".join(levels)

    swapped = blue * w * 4 + red * w * 4
    with pytest.raises(tb.TextureBuildError) as e1:
        tb.build_texture_fields("bad", w, h, px, mip_levels=[swapped] + levels[1:])
    assert "mip_levels[0]" in str(e1.value)
    with pytest.raises(tb.TextureBuildError) as e2:
        tb.build_texture_fields("bad", w, h, px, mip_levels=levels[:2])
    assert "1×1" in str(e2.value)
    with pytest.raises(tb.TextureBuildError):
        tb.build_texture_fields("bad", w, h, px, mip_levels=[])


def test_flip_vertical_is_the_png_to_unity_contract() -> None:
    """★ 方向契约的机械判据（这条**能红**）。

    PNG 是 top-down，而 Unity 的 Texture2D.image data 是 bottom-up
    （GetPixels32 的 index 0 = 左下角）。
    把 PNG 的 tobytes() 直接塞进去 ⇒ **整张图上下颠倒**。
    实证（2026-10-09）：a vs b = 38.477/255，a vs flipud(b) = 0.002/255 ⇒ 差别**只是翻转**。
    """
    w, h = 4, 8
    red, blue = bytes((255, 0, 0, 255)), bytes((0, 0, 255, 255))
    px = red * w * 4 + blue * w * 4            # 顶向下：上红下蓝
    assert px[:3] == b"\xff\x00\x00"

    f = tb.build_texture_fields("flip", w, h, px, flip_vertical=True)
    got = f["image data"]
    assert len(got) == len(px)
    assert got[:3] == b"\x00\x00\xff", "翻转后第 0 行应当是源图的**最后**一行（蓝）"
    assert got[-4:-1] == b"\xff\x00\x00", "最后一行应当是源图的第 0 行（红）"
    # 翻回来必须逐字节等于原图（可逆）
    assert tb._flip_rows(got, w, h) == px
    # 负控：块压缩不能翻（块序不是行序）；非 RGBA32 也不能翻
    with pytest.raises(tb.TextureBuildError):
        tb.build_texture_fields("bad", 4, 4, bytes(64), texture_format=29, flip_vertical=True)
    with pytest.raises(tb.TextureBuildError):
        tb.build_texture_fields("bad", 4, 4, bytes(4 * 4 * 3), texture_format=3, flip_vertical=True)


def test_bad_dimensions_are_refused() -> None:
    for w, h in ((0, 4), (4, 0), (-1, 4), (20000, 4)):
        with pytest.raises(tb.TextureBuildError):
            tb.build_texture_fields("bad", w, h, b"")
    with pytest.raises(tb.TextureBuildError):
        tb.checker_pixels(4, 4, (), block=2)
    with pytest.raises(tb.TextureBuildError):
        tb.checker_pixels(4, 4, ((1, 2, 3),), block=0)


def test_deterministic_pixels() -> None:
    """同一组入参永远给同一串字节（⛔ 不许随机）—— 也为「图能不能认出来」留下基准。"""
    a = tb.checker_pixels(32, 32, ((1, 2, 3), (4, 5, 6)))
    b = tb.checker_pixels(32, 32, ((1, 2, 3), (4, 5, 6)))
    assert a == b and len(a) == 32 * 32 * 4
    assert tb.checker_pixels(32, 32, ((1, 2, 3), (4, 5, 6)), block=4) != a
