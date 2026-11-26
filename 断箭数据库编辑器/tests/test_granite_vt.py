r"""Granite VT（`asset_kernel.granite_vt`）的用例 —— 纯 Python 移植的机械判据。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_granite_vt.py -q`
-> 全 passed；`-m slow` 那两条在本机能跑（池子在盘上时）。

| 用例 | 钉住的是什么 |
|---|---|
| `test_fastlz_*` | ★ **唯一新写的算法**：FastLZ 0.1.0 level 1，锚点 = DotFastLZ `CompressLevel1` |
| `test_lz4_*` | codec 4 的裸 LZ4 块（对拍 `lz4.block`） |
| `test_bc3_alpha_*` | ★ BCnEncoder 的 alpha **四舍五入**、`texture2ddecoder` 截断 —— 差 0 的关键 |
| `test_bc1_colour_*` | 颜色通道相反：C# 用**截断** |
| `test_tga_*` | 判据要读 TGA（RLE ＋ 两种原点），读写必须成对 |
| `test_slow_baselines_*` | **验收**：4 张 C# 基线逐像素差 = 0 |
| `test_slow_header_*` | 与 `vt_pool.read_gts`（另一份独立实现）逐字段对得上 |

## 为什么 FastLZ 那条向量值得留着

三个 `.gts` 里抽样 5,158 个瓦片，codec **全是 4（LZ4）**，codec 9（FastLZ）出现 **0 次** ——
真数据覆盖不到那条路。所以这里拿 DotFastLZ 自己造的流当锚点（65 B -> 200 B，含字面量段、
短匹配、扩展长度匹配、**距离 < 长度的重叠拷贝**、结尾字面量段）。
初版把距离当成「存值」而不是「存值 + 1」，这条向量当时是**红**的（4 条流只过 1 条）。
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import granite_vt

# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

ROOT = Path(__file__).resolve().parents[1]
POOL_NAME = "f51bbaf7a40a0ab4909c16ff519cc9cf.gts"
STREAMING = ("BrokenArrow_Data", "StreamingAssets")
BASELINES = (
    ("aa1f19529e32d792e1a006d672908e72", 0),
    ("aa1f19529e32d792e1a006d672908e72", 1),
    ("e468dcd060a5fa6ffe952f2aa9663abc", 0),
    ("e468dcd060a5fa6ffe952f2aa9663abc", 1),
)


def _pool_path() -> Path | None:
    """主 VT 池在不在（环境变量 / 游戏目录 / 本机实测路径，三条都试）。"""
    roots = []
    override = os.environ.get("BA_MOD_MAKER_GAME")
    if override:
        roots.append(Path(override))
    try:
        from ba_mod_maker.core.assets.game import find_game_root
    except ImportError:  # pragma: no cover
        find_game_root = None
    if find_game_root is not None:
        found = find_game_root()
        if found is not None:
            roots.append(found)
    roots.append(Path(r"D:\Steam\steamapps\common\broken_arrow"))
    for root in roots:
        candidate = root.joinpath(*STREAMING) / POOL_NAME
        if candidate.is_file():
            return candidate
    return None


def _baseline_dir() -> Path:
    return WORK / "vt-extract" / "raw"


# --------------------------------------------------------------------------- FastLZ
#: DotFastLZ 2024.4.1 `FastLZ.CompressLevel1` 对下面这 200 B 的产物（65 B）。
FASTLZ_PLAIN = (b"the quick brown fox jumps over the lazy dog 0123456789 " * 8)[:200]
FASTLZ_STREAM = bytes.fromhex(
    "1e74686520717569636b2062726f776e20666f78206a756d7073206f76657220401e126c"
    "617a7920646f6720303132333435363738396017e07f36042074686520"
)


def test_fastlz_decodes_dotfastlz_level1_stream():
    """★ 独立对拍：DotFastLZ 编，本模块解，必须逐字节相同。"""
    assert len(FASTLZ_STREAM) == 65
    assert granite_vt.fastlz_decompress(FASTLZ_STREAM, 0x10000) == FASTLZ_PLAIN


def test_fastlz_overlapping_match_is_copied_bytewise():
    """距离 < 长度的那种匹配（这条向量里 `e0 7f 36` = 136 B / 距离 55）。"""
    out = granite_vt.fastlz_decompress(FASTLZ_STREAM, 0x10000)
    assert out[59:80] == FASTLZ_PLAIN[59:80]
    assert len(out) == 200


def test_fastlz_rejects_level2():
    """首字节高 3 位 = 1 是 level 2 —— ⛔ 不许当 level 1 硬解出垃圾。"""
    with pytest.raises(granite_vt.GraniteVtError):
        granite_vt.fastlz_decompress(bytes([0x20, 0x00]), 0x10000)


def test_fastlz_rejects_backreference_before_output_start():
    with pytest.raises(granite_vt.GraniteVtError):
        granite_vt.fastlz_decompress(bytes([0x00, 0x41, 0x60, 0x63]), 0x10000)


# --------------------------------------------------------------------------- LZ4
def test_lz4_block_matches_python_lz4():
    """codec 4 是**裸 LZ4 块**（无长度前缀）—— 对拍 `lz4.block`。"""
    lz4 = pytest.importorskip("lz4.block")
    payloads = [
        bytes(25920),
        (b"the quick brown fox jumps over the lazy dog " * 600)[:25920],
        bytes(range(256)) * 101,
        bytes((i * 37 + 11) % 256 for i in range(4096)),
    ]
    for payload in payloads:
        stream = lz4.compress(payload, store_size=False)
        assert granite_vt.lz4_block_decompress(stream, 0x10000) == payload


def test_lz4_rejects_zero_offset():
    with pytest.raises(granite_vt.GraniteVtError):
        granite_vt.lz4_block_decompress(bytes([0x00, 0x41, 0x00, 0x00]), None)


# --------------------------------------------------------------------------- BC
def _bc3_block(a0: int, a1: int, alpha_index: int, colour_index: int = 0) -> bytes:
    alpha = a0 | (a1 << 8) | (sum(alpha_index << (3 * i) for i in range(16)) << 16)
    colour = sum(colour_index << (2 * i) for i in range(16)) << 64
    return (alpha | colour).to_bytes(16, "little")


def test_bc3_alpha_is_rounded_not_truncated():
    """★ C# 侧（BCnEncoder）对 alpha 内插值**四舍五入**：a0=166 / a1=126 / index 7。

    精确值 (166 + 6x126) / 7 = 131.71 -> C# 给 132，截断给 131。
    实测：整张 2048x2048 基线（4,194,304 px）在截断下差 843,668 px。
    """
    block = _bc3_block(166, 126, 7)
    decoded = granite_vt.decode_bc(block, 4, 4, "BC3")
    assert [
        (decoded[i], decoded[i + 1], decoded[i + 2], decoded[i + 3]) for i in range(0, 64, 4)
    ] == [(0, 0, 0, 132)] * 16
    assert ((166 + 6 * 126) // 7) == 131  # 截断会得到 131（负控：两个读数必须不同）


def test_bc3_alpha_diverges_from_texture2ddecoder():
    """同一个块：`texture2ddecoder` 给截断值 —— 所以 BC1/3/4/5 ⛔ 不能借它。"""
    t2d = pytest.importorskip("texture2ddecoder")
    block = _bc3_block(166, 126, 7)
    bgra = bytes(t2d.decode_bc3(block, 4, 4))
    assert bgra[3] == 131
    assert granite_vt.decode_bc(block, 4, 4, "BC3")[3] == 132


def test_bc1_colour_uses_truncation():
    """颜色通道相反：C# 用**截断**（2*255 + 0)/3 = 170。"""
    bits = sum(2 << (2 * i) for i in range(16))
    block = (0xF800 | (0x001F << 16) | (bits << 32)).to_bytes(8, "little")
    decoded = granite_vt.decode_bc(block, 4, 4, "BC1")
    assert (decoded[0], decoded[1], decoded[2], decoded[3]) == (170, 0, 85, 255)


def test_bc4_puts_the_channel_in_red():
    block = (0x80 | (0x00 << 8)).to_bytes(8, "little")
    decoded = granite_vt.decode_bc(block, 4, 4, "BC4")
    assert (decoded[0], decoded[1], decoded[2], decoded[3]) == (128, 0, 0, 255)


# --------------------------------------------------------------------------- TGA
def _tga_rle(width: int, height: int, rows: list) -> bytes:
    """造一张 32 位 RLE TGA（**左下原点**，descriptor = 0x00）。"""
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 10, 0, 0, 0, 0, 0, width, height, 32, 0x00)
    flat = [pixel for row in rows for pixel in row]
    body = bytearray()
    for i in range(0, len(flat), 2):
        body.append(0x01)  # RLE 包头：低 7 位 = 2 - 1 -> 两个**原始**像素
        body += flat[i]
        body += flat[i + 1]
    return header + bytes(body)


def test_tga_reader_normalises_bottom_left_origin(tmp_path: Path):
    # TGA 文件里存的是 **BGRA**：文件字节 (255,0,0,255) 解出来是纯蓝。
    file_blue = bytes((255, 0, 0, 255))
    file_red = bytes((0, 0, 255, 255))
    path = tmp_path / "rle.tga"
    path.write_bytes(_tga_rle(2, 2, [[file_blue, file_blue], [file_red, file_red]]))
    width, height, pixels = granite_vt.read_tga_rgba(path)
    assert (width, height) == (2, 2)
    # descriptor 写的是「左下原点」⇒ 文件第一行是**底**行，归一化之后落到输出末行。
    assert pixels[0:4] == bytes((255, 0, 0, 255))
    assert pixels[4:8] == bytes((255, 0, 0, 255))
    assert pixels[8:12] == bytes((0, 0, 255, 255))
    assert pixels[12:16] == bytes((0, 0, 255, 255))


def test_tga_writer_from_the_cli_round_trips(tmp_path: Path):
    sys.path.insert(0, str(ROOT / "tools"))
    import vt_read

    pixels = bytes((i * 7 + 3) % 256 for i in range(4 * 3 * 4))
    path = tmp_path / "out.tga"
    vt_read.write_tga_rgba(path, 4, 3, pixels)
    width, height, back = granite_vt.read_tga_rgba(path)
    assert (width, height) == (4, 3)
    assert back == pixels


# --------------------------------------------------------------------------- 慢档：真池子
@pytest.mark.slow
def test_slow_baselines_are_pixel_exact():
    """**验收判据**：4 张 C# 基线（2048x2048 RGBA TGA）逐像素差 = 0。"""
    pool = _pool_path()
    if pool is None:
        pytest.skip("盘上没有主 VT 池")
    raw = _baseline_dir()
    readings = []
    for name, layer in BASELINES:
        baseline = raw / f"{name}_{layer}.tga"
        if not baseline.is_file():
            pytest.skip(f"缺基线 {baseline}")
        width, height, reference = granite_vt.read_tga_rgba(baseline)
        got_w, got_h, mine = granite_vt.extract_layer(pool, name, layer)
        assert (got_w, got_h) == (width, height)
        diff = sum(1 for i in range(0, len(mine), 4) if mine[i : i + 4] != reference[i : i + 4])
        readings.append((baseline.name, diff))
    assert readings == [(f"{name}_{layer}.tga", 0) for name, layer in BASELINES]


@pytest.mark.slow
def test_slow_header_agrees_with_vt_pool():
    """两份**独立实现**读同一份 .gts 的头，逐字段必须一致（防止布局各自漂）。"""
    pool_path = _pool_path()
    if pool_path is None:
        pytest.skip("盘上没有主 VT 池")
    try:
        from ba_mod_maker.asset_kernel import vt_pool
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"vt_pool 拉不起来：{exc}")
    mine = granite_vt.TileSetFile.from_path(pool_path)
    theirs = vt_pool.read_gts(pool_path)
    assert mine.num_layers == theirs.num_layers
    assert mine.num_levels == theirs.num_levels
    assert mine.tile_width == theirs.tile_pitch
    assert mine.tile_height == theirs.tile_height
    assert mine.tile_border == theirs.border
    assert mine.custom_page_size == theirs.custom_page_size
    assert mine.num_flat_tiles == theirs.num_flat
    assert mine.content == theirs.content
    assert mine.num_page_files == len(theirs.descriptors)
    for index in (0, theirs.num_levels - 1):
        level = theirs.levels[index]
        assert mine.level(index)[:3] == (level.nx, level.ny, level.tiles_offset)
    for index, descriptor in enumerate(theirs.descriptors):
        name, num_pages, size_in_bytes = mine.page_file_descriptor(index)
        # ⚠ 名字**只比前缀**：`vt_pool.read_gts` 只解描述符的头 64 B（= 32 个 UTF-16 字符），
        # 而这一段的字段宽是 0x200 B —— 组名正好 32 字符，于是那 64 B 之后的 `0_1.gtp`
        # 被它截掉了。实测：它给 `f51bbaf7...9cc9cf`，真名是 `f51bbaf7...9cc9cf0_1.gtp`。
        assert name.startswith(descriptor.name)
        assert num_pages > 0
        assert size_in_bytes > 0
