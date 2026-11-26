"""版本号只有一处 —— 这条用例守的就是"只有一处"。

口径（2026-10-01 定）：**版本号住在 `src/ba_mod_maker/__init__.py::__version__`**，
`pyproject.toml` 不再写版本号，它写的是：

    dynamic = ["version"]
    [tool.hatch.version]
    path = "src/ba_mod_maker/__init__.py"

由 hatchling 在打包 / 安装时去读那个文件 —— 所以"改版本"这件事只有一步。

它要能抓到的三种失效（都是"改了一处忘另一处"的真身）：

1. 有人在 pyproject.toml 里又写回一个静态 `version = "0.2.0"`
   —— 从此两处数字各说各话，而先看到的那个还未必是生效的那个；
2. 有人把 `[tool.hatch.version] path` 指到别的文件（例如新加的 `_version.py`）
   —— 源码里读到的版本与装出来的包不再是同一个；
3. 源码里出现**第二个** `__version__` 赋值 —— 读到的字面量与 hatch 读到的可能不同。

不是"自己跟自己比"的地方在第 2、3 条：它们读的是**磁盘上那个文件**（hatch 真正会读的
那一份），再与**导入进来的模块对象**比 —— 只有"路径指对了 + 只有一个字面量"才能同时成立。
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest

import ba_mod_maker

REPO = Path(__file__).resolve().parents[1]
DIST_NAME = "ba-mod-maker"

#: hatch 真正会去读的那个文件（与 pyproject 里的 path 比对着看）。
VERSION_SOURCE = "src/ba_mod_maker/__init__.py"

#: PEP 440 的版本号（够用即可：本仓只发 X.Y.Z / X.Y.ZrcN 这类）。
_PEP440 = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")

#: 源码里那一行唯一的版本赋值。
_VERSION_LINE = re.compile(r'^__version__ = "(?P<v>[^"]+)"$', re.MULTILINE)


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_does_not_hold_a_second_version() -> None:
    """pyproject 里不许再出现静态版本号。"""
    project = _pyproject()["project"]
    assert "version" not in project, (
        "pyproject.toml 里又有 version = ... 了 —— 版本号只能有一处，"
        f"现在这一处在 {VERSION_SOURCE}::__version__"
    )
    assert project.get("dynamic") == ["version"]


def test_hatch_version_path_points_at_the_imported_module() -> None:
    """hatch 读的那个文件，必须就是 `import ba_mod_maker` 真正加载的文件。"""
    path = _pyproject()["tool"]["hatch"]["version"]["path"]
    assert path == VERSION_SOURCE, (
        f"[tool.hatch.version] path 被指到了 {path} —— 它必须与下面这个文件是同一个"
    )

    source = (REPO / path).resolve()
    loaded = Path(ba_mod_maker.__file__).resolve()
    assert source == loaded, f"hatch 读 {source}，而 import 拿到的是 {loaded}"

    # 只有**一个** __version__ 赋值：多一个就是多一个来源。
    found = _VERSION_LINE.findall(source.read_text(encoding="utf-8"))
    assert len(found) == 1, f"{path} 里有 {len(found)} 处 __version__ = ...，应当只有 1 处"
    assert found[0] == ba_mod_maker.__version__, (
        f"文件里写的是 {found[0]}，导入进来的是 {ba_mod_maker.__version__}"
    )


def test_source_version_is_readable_and_pep440() -> None:
    """能被读出来、没有首尾空白、是合法的 PEP 440 版本号。"""
    version = ba_mod_maker.__version__
    assert isinstance(version, str) and version
    assert version.strip() == version, f"版本号有首尾空白：{version!r}"
    assert _PEP440.match(version), f"不是 PEP 440 版本号：{version!r}"


def test_installed_metadata_matches_the_source() -> None:
    """装出来的那个包，版本必须等于源码里的 —— 这就是"改一处忘另一处"的最终症状。

    失败时先跑 `uv sync`（bootstrap.ps1 的第 [1/4] 步就是它）：editable 安装的
    dist-info 是**安装那一刻**由 hatch 生成的，改了源码不同步，这里就会差一版。
    """
    try:
        installed = importlib.metadata.version(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"环境里没有装 {DIST_NAME}（直接跑源码），只能验源码那一侧")
    assert installed == ba_mod_maker.__version__, (
        f"装出来的包是 {installed}，源码里是 {ba_mod_maker.__version__} —— 跑一次 uv sync"
    )
