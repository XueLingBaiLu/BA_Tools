"""从游戏里取 UI 字体（**零 Qt**）。

为什么要有这一层
----------------
用户口径：产品里的字要和游戏里长得一样。游戏自己的字体在
`aa/PC/fonts_assets_all_<hash>.bundle` 里（实测 383 MB / 98 个资产）：既有 Font
（TTF 字节就在 `m_FontData` 里），也有 TMP 烘焙出来的 SDF 资产。

**实测出来的字体链**（读 TMP 字体的 `m_SourceFontFile` 与 `m_FallbackFontAssetTable`）：

    拉丁  Inter-<字重> SDF     ← 每个 Inter SDF 的来源都是 Inter-<字重>.ttf
    中文  NotoSerifCJK-<字重> -> NotoSerifCJKsc-<字重> -> Arial Unicode MS
    其它  Sora-*（另一套标题字）、ConsolaMono（等宽）

所以产品取 **拉丁 Inter + 中文 Noto Serif CJK SC**（sc 那份是简体字形，且就在游戏的
回退链里）。字重只要 Regular 与 SemiBold —— 产品的 bold 走 DemiBold。

❗ **不把 TTF 放进仓库**（游戏素材的派生数据，与 `cache/icons`、本地化缓存同一约定）：
运行时从包里取出来落到 `%LOCALAPPDATA%/BA_Mod_Maker/fonts`，之后直接读缓存。
游戏更新（包名/大小/mtime 变了）缓存自动作废。

找不到游戏 / 没装 UnityPy -> 返回空元组，界面回落系统字体族（**不是错误**）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

from ba_mod_maker.core.assets.bundles import BundleUnavailableError, unitypy
from ba_mod_maker.core.assets.game import bundle_dir, find_game_root

#: 包名带 hash，只能 glob（游戏更新会变）。
BUNDLE_GLOB = "fonts_assets_all_*.bundle"

#: 缓存目录名：%LOCALAPPDATA%/BA_Mod_Maker/fonts。
CACHE_DIR_NAME = "fonts"

#: 缓存格式版本。改了结构就 +1（旧缓存自动作废）。
CACHE_VERSION = 1

#: 环境变量覆盖缓存位置（测试用）。
ENV_CACHE = "BA_MOD_MAKER_FONT_CACHE"

#: 要抽出来的 Font 资产名。顺序无所谓，只是列清楚要哪几个字重。
#: 实测：6 个文件共 50.4 MB，UnityPy.load 0.65s + 抽出 1.33s（首次冷启动约 2 秒）。
WANTED: tuple[str, ...] = (
    "Inter-Regular",
    "Inter-Medium",
    "Inter-SemiBold",
    "Inter-Bold",
    "NotoSerifCJKsc-Regular",
    "NotoSerifCJKsc-SemiBold",
)


def default_cache_dir() -> Path:
    """字体缓存的落点。与图标 / 本地化同一约定：用户目录，**不进仓库**。"""
    override = os.environ.get(ENV_CACHE)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "BA_Mod_Maker" / CACHE_DIR_NAME


def find_fonts_bundle(game_root: Path | None = None) -> Path | None:
    """定位字体包；找不到返回 None（**不是错误**）。

    多个包同时存在时取 **mtime 最新**的那个：游戏更新后旧包常常还留在盘上。
    """
    root = game_root if game_root is not None else find_game_root()
    if root is None:
        return None
    directory = bundle_dir(root)
    if not directory.is_dir():
        return None
    candidates = [path for path in directory.glob(BUNDLE_GLOB) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def bundle_stamp(bundle: Path) -> dict[str, object]:
    """包的指纹。缓存要与它绑定，否则游戏更新后会用旧字。"""
    try:
        stat = bundle.stat()
    except OSError:
        return {}
    return {"name": bundle.name, "size": stat.st_size, "mtime": int(stat.st_mtime)}


def read_font_data(bundle: Path, names: Sequence[str] = WANTED) -> dict[str, bytes]:
    """从包里读出这几个 Font 的 TTF 字节（`Font.m_FontData`）。

    ⚠️ 惰性 import UnityPy（0.65s），且**只开这一个包**；读不到的族直接不出现在结果里。
    """
    try:
        env = unitypy().load(str(bundle))
    except BundleUnavailableError:
        raise
    except Exception:  # noqa: BLE001 - 包坏了不该让界面起不来
        return {}
    wanted = set(names)
    found: dict[str, bytes] = {}
    for obj in env.objects:
        if obj.type.name != "Font":
            continue
        try:
            data = obj.read()
        except Exception:  # noqa: BLE001 - 单个资产读不出就跳过
            continue
        name = getattr(data, "m_Name", "")
        if name not in wanted or name in found:
            continue
        raw = getattr(data, "m_FontData", None)
        if raw:
            found[name] = bytes(raw)
    return found


def read_cache(cache_dir: Path, stamp: dict[str, object] | None = None) -> dict[str, Path]:
    """读缓存。指纹对不上（或没给指纹）返回空表。"""
    manifest = cache_dir / "_manifest.json"
    if not manifest.is_file():
        return {}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if payload.get("version") != CACHE_VERSION:
        return {}
    if stamp is not None and payload.get("bundle") != stamp:
        return {}
    files = payload.get("files") or {}
    out: dict[str, Path] = {}
    for name, relative in files.items():
        path = cache_dir / str(relative)
        if path.is_file():
            out[str(name)] = path
    return out


def ensure_fonts(
    game_root: Path | None = None,
    cache_dir: Path | None = None,
    names: Sequence[str] = WANTED,
) -> tuple[Path, ...]:
    """拿到可用的 TTF 路径（缓存优先）。取不到就返回空元组。

    顺序：缓存 -> 从游戏包里抽（并落盘）-> 空。**任何一步失败都不抛**：
    没装游戏、没装 UnityPy、包读坏了，都只是"用系统字体"，界面照常能用。
    """
    directory = cache_dir if cache_dir is not None else default_cache_dir()
    bundle = find_fonts_bundle(game_root)
    stamp = bundle_stamp(bundle) if bundle is not None else None

    cached = read_cache(directory, stamp)
    if cached:
        return tuple(cached[name] for name in names if name in cached)

    if bundle is None:
        return ()
    try:
        data = read_font_data(bundle, names)
    except BundleUnavailableError:
        return ()
    if not data:
        return ()

    written: dict[str, str] = {}
    paths: dict[str, Path] = {}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for name, payload in data.items():
            target = directory / f"{name}.ttf"
            target.write_bytes(payload)
            written[name] = target.name
            paths[name] = target
        # ❗ manifest 最后写：它是"这一批完整落地"的标记，
        # 半途失败下次会重抽，不会留下半份缓存。
        (directory / "_manifest.json").write_text(
            json.dumps(
                {"version": CACHE_VERSION, "bundle": stamp, "files": written},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    except OSError:
        return tuple(paths[name] for name in names if name in paths)
    return tuple(paths[name] for name in names if name in paths)


__all__ = [
    "BUNDLE_GLOB",
    "CACHE_VERSION",
    "ENV_CACHE",
    "WANTED",
    "bundle_stamp",
    "default_cache_dir",
    "ensure_fonts",
    "find_fonts_bundle",
    "read_cache",
    "read_font_data",
]
