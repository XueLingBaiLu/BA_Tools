r"""VT 池（`.gts` ＋ `.gtp`）读取器命令行 —— 纯 Python，给 Blender 插件那条链当尺子。

## 判据一行

    C:\Python314\python.exe tools\vt_read.py check
    -> 4 张 C# 基线逐像素差 = 0；正控（改 1 个像素）必须报红

## 命令

    vt_read.py list                 列池子信息 ＋ item 名
    vt_read.py list --all           连坐标一起列（2109 条，含同名多条）
    vt_read.py extract --item X --layer 0 --out y.png
    vt_read.py check                四张基线逐像素 ＋ 正控（rc=0 才算过）

## 为什么 check 要在意「能红」

逐像素差 = 0 是**唯一**的判据；一把永远绿的尺子等于没有尺子。所以 check 里先跑正控：
把基线的一个像素改掉，同一把尺子必须报出 `diff_px >= 1`。正控不红 ⇒ 整体不算过（rc=1）。
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import time
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 输出一律 UTF-8：本机控制台默认 GBK，中文读数会被写成乱码（实测）。
for _stream in (sys.stdout, sys.stderr):
    with suppress(AttributeError, OSError):  # pragma: no cover - 非常规流
        _stream.reconfigure(encoding="utf-8", errors="replace")
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ba_mod_maker.asset_kernel import granite_vt  # noqa: E402

#: 主池的组名（`StreamingAssets\<组名>.gts`）。
POOL_NAME = "f51bbaf7a40a0ab4909c16ff519cc9cf.gts"
#: 找不到游戏目录时的兜底路径（本机实测位置）。
FALLBACK_GTS = (
    Path(r"D:\Steam\steamapps\common\broken_arrow\BrokenArrow_Data\StreamingAssets") / POOL_NAME
)
#: 四张 C# 基线（**别改别删**：它们是这条链的唯一真值）。
BASELINES = (
    ("aa1f19529e32d792e1a006d672908e72", 0, "ACV 法线层"),
    ("aa1f19529e32d792e1a006d672908e72", 1, "ACV 遮罩层"),
    ("e468dcd060a5fa6ffe952f2aa9663abc", 0, "AH-1W 法线层"),
    ("e468dcd060a5fa6ffe952f2aa9663abc", 1, "AH-1W 遮罩层"),
)


def default_gts() -> Path:
    """默认池：游戏目录（`BA_MOD_MAKER_GAME` 可覆盖）里的主 .gts，找不到就退回实测路径。"""
    try:
        from ba_mod_maker.core.assets.game import find_game_root
    except ImportError:  # pragma: no cover - 只在缺依赖时走这里
        find_game_root = None
    root = find_game_root() if find_game_root is not None else None
    if root is not None:
        candidate = root / "BrokenArrow_Data" / "StreamingAssets" / POOL_NAME
        if candidate.is_file():
            return candidate
    return FALLBACK_GTS


def compare_rgba(mine: bytes, reference: bytes) -> tuple:
    """逐像素比两段 RGBA8：`(差几个像素, 单通道最大差)`。"""
    if len(mine) != len(reference):
        raise granite_vt.GraniteVtError(f"两张图字节数不同：{len(mine)} vs {len(reference)}")
    diff = 0
    max_delta = 0
    for i in range(0, len(mine), 4):
        if mine[i : i + 4] != reference[i : i + 4]:
            diff += 1
            for channel in range(4):
                delta = abs(mine[i + channel] - reference[i + channel])
                if delta > max_delta:
                    max_delta = delta
    return diff, max_delta


def write_tga_rgba(path: Path, width: int, height: int, pixels: bytes) -> None:
    """写 32 位、**左上原点**、不压缩的 TGA（与 `granite_vt.read_tga_rgba` 对得上）。"""
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, width, height, 32, 0x28)
    body = bytearray(pixels)
    body[0::4], body[2::4] = body[2::4], body[0::4]  # RGBA -> BGRA
    path.write_bytes(header + bytes(body))


def write_png_rgba(path: Path, width: int, height: int, pixels: bytes) -> bool:
    """有 PIL 就写 PNG；没有返回 False（调用方退回 TGA）。"""
    try:
        from PIL import Image
    except ImportError:
        return False
    Image.frombytes("RGBA", (width, height), pixels).save(path)
    return True


def cmd_list(args: argparse.Namespace) -> int:
    pool = granite_vt.open_pool(args.gts)
    for line in pool.describe():
        print(line)
    for warning in pool.warnings[:10]:
        print(f"  ⚠ {warning}")
    entries = pool.texture_entries()
    if args.all:
        for entry in entries:
            print(entry.line)
    else:
        names = pool.list_textures()
        for name in names[: args.limit]:
            print(name)
        if args.limit and len(names) > args.limit:
            print(f"... 还有 {len(names) - args.limit} 个（--limit 0 全列）")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    started = time.time()
    width, height, pixels = granite_vt.extract_layer(args.gts, args.item, args.layer, args.index)
    out = Path(args.out) if args.out else WORK / "gvt" / f"{args.item}_{args.layer}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    written = True
    if out.suffix.lower() == ".tga":
        write_tga_rgba(out, width, height, pixels)
    else:
        written = write_png_rgba(out, width, height, pixels)
        if not written:
            out = out.with_suffix(".tga")
            write_tga_rgba(out, width, height, pixels)
    digest = hashlib.sha256(pixels).hexdigest()
    print(f"{args.item} 层 {args.layer} -> {width}x{height} RGBA8  {out}")
    print(f"  sha256(RGBA8) {digest}")
    print(f"  用时 {time.time() - started:.2f} s")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw)
    print(f"池：{args.gts}")
    print(f"基线目录：{raw_dir}")
    failures = []
    for name, layer, label in BASELINES:
        baseline = raw_dir / f"{name}_{layer}.tga"
        if not baseline.is_file():
            print(f"  ❌ 基线不在：{baseline}")
            failures.append(baseline.name)
            continue
        width, height, reference = granite_vt.read_tga_rgba(baseline)
        started = time.time()
        got_w, got_h, mine = granite_vt.extract_layer(args.gts, name, layer)
        elapsed = time.time() - started
        if (got_w, got_h) != (width, height):
            print(f"  ❌ {baseline.name} 尺寸不符：{got_w}x{got_h} vs {width}x{height}")
            failures.append(baseline.name)
            continue
        diff, max_delta = compare_rgba(mine, reference)
        verdict = "PASS" if diff == 0 else "FAIL"
        print(
            f"  {baseline.name}  {width}x{height}  diff_px={diff}  max_delta={max_delta}"
            f"  {verdict}  ({label}, {elapsed:.2f} s)"
        )
        if diff != 0:
            failures.append(baseline.name)

    # 正控：把第一张基线的**一个像素**改掉，同一把尺子必须报红
    control_name, control_layer, _label = BASELINES[0]
    control_path = raw_dir / f"{control_name}_{control_layer}.tga"
    control_ok = False
    control_line = "正控：基线缺失，跑不了"
    if control_path.is_file():
        width, height, reference = granite_vt.read_tga_rgba(control_path)
        broken = bytearray(reference)
        broken[4] ^= 0x01  # 第 1 个像素的 G 通道改 1 个 bit
        _w, _h, mine = granite_vt.extract_layer(args.gts, control_name, control_layer)
        diff, max_delta = compare_rgba(mine, bytes(broken))
        control_ok = diff >= 1
        control_line = (
            f"正控（{control_path.name} 第 1 个像素 G+1）：diff_px={diff} max_delta={max_delta} "
            f"-> {'能红' if control_ok else '**红不了**'}"
        )
        print(f"  {control_line}")

    passed = not failures and control_ok
    print(
        f"结论：{len(BASELINES) - len(failures)}/{len(BASELINES)} 逐像素差 0；"
        f"正控 {'红' if control_ok else '不红'}"
    )
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vt_read", description="读 Granite VT 池（.gts/.gtp）")
    parser.add_argument("--gts", default=None, help=".gts 路径（默认：游戏目录里的主池）")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="列池子信息与 item 名")
    listing.add_argument("--all", action="store_true", help="连坐标一起列（含同名多条）")
    listing.add_argument("--limit", type=int, default=0, help="只列前 N 个唯一名（0 = 全列）")

    extract = sub.add_parser("extract", help="取一层存成 PNG / TGA")
    extract.add_argument("--item", required=True, help="item 名（32 位 hex）")
    extract.add_argument("--layer", type=int, default=0, help="层号（默认 0）")
    extract.add_argument("--index", type=int, default=-1, help="同名条目取第几条（-1 = 最后一条）")
    extract.add_argument("--out", default="", help="输出路径（.png 走 PIL，.tga 走内置写手）")

    check = sub.add_parser("check", help="四张 C# 基线逐像素 ＋ 正控")
    check.add_argument(
        "--raw",
        default=str(WORK / "vt-extract" / "raw"),
        help="四张基线 .tga 所在目录",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "gts", None):
        args.gts = default_gts()
    if not Path(args.gts).is_file():
        print(f"❌ 找不到 .gts：{args.gts}", file=sys.stderr)
        return 2
    if args.command == "list":
        return cmd_list(args)
    if args.command == "extract":
        return cmd_extract(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
