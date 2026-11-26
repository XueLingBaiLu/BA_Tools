r"""W11 追加 · 构建戳的机械判据（bpy-free，产品 venv 直接跑）。

**判据一行**：build_info.stamp() -> 有 sha 时是 v<版本> · <时间> · 源码 <sha8>；
没 sha 时退化成 v<版本> · <时间>（仓内开发那份就是这种）。

为什么值得钉：2026-10-04 用户装了新版界面却是旧的（Blender 覆盖安装不重载子模块），
**从界面看不出装的是哪一版**，白排查一轮。构建戳就是为了让人一眼看出来。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON = REPO_ROOT / "blender_addon"
PKG = "bamod_build_under_test"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_info():
    """合成包 + 影子加载：这两个模块都不 import bpy，所以 venv 里跑得动。"""
    package = types.ModuleType(PKG)
    package.__path__ = [str(ADDON)]  # type: ignore[attr-defined]
    sys.modules[PKG] = package
    build = _load("_build", ADDON / "_build.py")
    info = _load("build_info", ADDON / "build_info.py")
    return info, build


def test_stamp_with_source_sha_has_eight_chars(build_info) -> None:
    """有源码 sha ⇒ 戳里带**前 8 位**（完整 64 位太长，面板放不下）。"""
    info, build = build_info
    build.VERSION = "0.12.0"
    build.BUILT_AT = "2026-10-10 23:52"
    build.SOURCE_SHA256 = "6856bbff" + "0" * 56
    assert info.stamp() == "v0.12.0 · 2026-10-10 23:52 · 源码 6856bbff"


def test_stamp_without_sha_degrades(build_info) -> None:
    """负控：没 sha（仓内开发那份）⇒ 短形式，⛔ 不许出现空的「源码 」尾巴。"""
    info, build = build_info
    build.VERSION = "dev"
    build.BUILT_AT = "未打包（仓内开发）"
    build.SOURCE_SHA256 = ""
    text = info.stamp()
    assert text == "vdev · 未打包（仓内开发）"
    assert "源码" not in text


def test_repo_build_stamp_is_the_dev_placeholder() -> None:
    """仓内那份 _build.py 必须还是 dev 占位（⛔ 别把打包结果提交回仓里）。"""
    text = (ADDON / "_build.py").read_text(encoding="utf-8")
    assert 'VERSION = "dev"' in text
    assert 'SOURCE_SHA256 = ""' in text
