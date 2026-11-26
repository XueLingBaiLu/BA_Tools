# -*- coding: utf-8 -*-
"""VT 的法线 / 遮罩：**从游戏原文件里现抽**，落进**用户缓存**，不再读开发机的目录。

为什么要有这一层（用户 2026-10-17 点名）：「你不要把图提到 `_work/vt-extract` 那种目录 ——
插件是要发布出去的，别人换别的模型，难道还要来问我们要两张图？让插件自己抽到缓存里。」

## 契约

* 落点：**游戏所在盘**的 `BAModMaker\\vt\\<group>\\<item>_{0,1}.png`（例 `D:\\BAModMaker\\vt\\…`）——
  ★ 2026-10-17 用户点名「缓存不要落到C盘」，所以**跟着游戏盘走**，`BAMOD_VT_CACHE` 仍可覆盖；
* **按 (group, item) 取，不按单位** —— 一颗材质一个 item（多材质单位不再共用一对图）；
* 命中缓存直接返回；没命中就用 `ba_mod_maker.asset_kernel.granite_vt` 现抽（纯标准库）；
* 抽不到（池不在 / item 不在）⇒ **返回空 dict ＋ 一句人话**，⛔ 不编、不退到别人的图。

## 为什么不用 numpy / PIL

插件装进 Blender 之后只有随包依赖；这一层只用 `zlib` + `struct` 写 PNG，法线解码靠
**256x256 的查表**（x 只取决 A、y 只取决 G ⇒ 只有 65,536 种组合），4.2M 像素也不用逐点开方。
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

__all__ = ["CACHE_ENV", "cache_root", "ensure_maps", "decoded_normal_rgb"]

#: 缓存根的环境变量（与 `BAMOD_VT_DIR` 分开：那个是**旧的手工图目录**，只当兼容来源）。
CACHE_ENV = "BAMOD_VT_CACHE"
#: 与 `panels_common.PROJECT_DIRNAME` 逐字同一个（用户目录约定）。
PROJECT_DIRNAME = "BAModMaker"
#: 游戏里 VT 池所在目录（相对游戏根）。
POOL_DIR = Path("BrokenArrow_Data") / "StreamingAssets"


def cache_root(game_root: str | Path | None = None) -> Path:
    """缓存根 —— 环境变量优先；否则**跟着游戏所在盘走，绝不落 C 盘**。

    ★ 2026-10-17 用户点名：「缓存不要落到C盘」。顺序：
    `BAMOD_VT_CACHE` → 游戏根所在盘 → 当前工作盘 → 用户目录（**只有全机只有 C 盘**时才走到）。
    """
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override)
    for source in (game_root, os.getcwd()):
        if not source:
            continue
        anchor = Path(str(source)).anchor
        if anchor and not anchor.upper().startswith("C:"):
            return Path(anchor) / PROJECT_DIRNAME / "vt"
    # 整台机器只有 C 盘时别无选择（用户目录是最后一档，⛔ 不是默认档）
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".bamod-maker"
    return root / PROJECT_DIRNAME / "vt"


def _write_png(path: Path, width: int, height: int, rows: list, channels: int) -> None:
    """最小 PNG 写出（filter=0）。`rows` 是每行 `bytes`，长度 = width*channels。"""
    color_type = 2 if channels == 3 else 6
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


#: 法线的 B 通道（Z）查表：`_Z[a][g]` 是 0..255。
_Z: list | None = None


def _z_table() -> list:
    """256x256 的 Z 查表（x 只由 A 决定、y 只由 G 决定 ⇒ 组合只有 65,536 种）。"""
    global _Z
    if _Z is not None:
        return _Z
    import math

    table = []
    for a in range(256):
        x = a / 255.0 * 2.0 - 1.0
        row = []
        for g in range(256):
            y = g / 255.0 * 2.0 - 1.0
            z = 1.0 - x * x - y * y
            z = math.sqrt(z) if z > 0.0 else 0.0
            row.append(int(z * 255.0 + 0.5))
        table.append(row)
    _Z = table
    return table


def decoded_normal_rgb(rgba: bytes) -> bytes:
    """DXT5nm 的 RGBA -> 可直接当法线图用的 RGB（R=A, G=G, B=sqrt(1-x^2-y^2)）。"""
    table = _z_table()
    out = bytearray(len(rgba) // 4 * 3)
    i = 0
    o = 0
    for _ in range(len(rgba) // 4):
        a = rgba[i + 3]
        g = rgba[i + 1]
        out[o] = a
        out[o + 1] = g
        out[o + 2] = table[a][g]
        i += 4
        o += 3
    return bytes(out)


def _rows(data: bytes, width: int, height: int, channels: int) -> list:
    stride = width * channels
    return [data[y * stride : (y + 1) * stride] for y in range(height)]


def ensure_maps(group: str, item: str, *, game_root, out_dir: Path | None = None) -> tuple:
    """`(paths, note)` —— 这一颗材质的法线/遮罩在缓存里的路径（没有就现抽）。

    `paths` 只有 `normal` / `mask` 两个键；抽不到时返回 `{}` ＋ 一句点名。
    """
    group = str(group or "").strip()
    item = str(item or "").strip()
    if not group or not item:
        return {}, "没有 VT 栈（这颗材质没有 m_BuildTextureStacks）"
    if game_root is None:
        # 给「磁盘上一个 .bundle 文件」那条路：那边不知道游戏根，就退回环境变量。
        game_root = os.environ.get("BA_MOD_MAKER_GAME") or ""
    if not game_root:
        return {}, "不知道游戏根（.bundle 文件那条路）⇒ 抽不了 VT 池"
    root = Path(out_dir) if out_dir is not None else cache_root(game_root)
    slot = root / group
    normal_png = slot / (item + "_0.png")
    mask_png = slot / (item + "_1.png")
    if normal_png.is_file() and mask_png.is_file():
        return {"normal": str(normal_png), "mask": str(mask_png)}, ""
    gts = Path(game_root) / POOL_DIR / (group + ".gts")
    if not gts.is_file():
        return {}, f"池文件不在：{gts}"
    try:
        from ba_mod_maker.asset_kernel import granite_vt

        width, height, layer0 = granite_vt.extract_layer(gts, item, 0)
        _w0, _h0, layer1 = granite_vt.extract_layer(gts, item, 1)
    except Exception as exc:  # noqa: BLE001 -- 抽不出来就如实说，不许编一张
        return {}, f"抽不出来（{type(exc).__name__}: {exc}）"
    _write_png(normal_png, width, height, _rows(decoded_normal_rgb(layer0), width, height, 3), 3)
    _write_png(mask_png, width, height, _rows(bytes(layer1), width, height, 4), 4)
    return {"normal": str(normal_png), "mask": str(mask_png)}, f"现抽并缓存（{width}x{height}）"
