r"""提取器只读**原版包**：catalog 的 bundle 字段被我们装机改过时也要砍回文件名。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_original_bundle_name.py -q`
→ 4 passed（含 2 条负控：BA_Mod 的相对路径 / 绝对路径都必须砍成 `x.bundle`）。

## 为什么需要

用户 2026-10-20 报：「模型提取器有时候会从我们新造的 bundle 包里提取模型」。
根因：我们自己装机时把 catalog 的包路径改写成 `..\..\..\BA_Mod\<包>`（STATUS 2026-10-19 20:0x），
提取器照字面跟过去就拿到了我们改过的包。`original_bundle_name()` 把那一律砍成文件名，
上层只在 `aa\PC` 里按同名找原版（我们"新增"的资产没有原版同名件 ⇒ 报错，不猜）。
"""
from __future__ import annotations

import pytest

from ba_mod_maker.core.assets.game import original_bundle_name


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        # 正控：本来就是裸文件名 —— 一个字都不许改
        ("units_assets_all_1e6c04ce42984f32a0891b92fab010e8.bundle",
         "units_assets_all_1e6c04ce42984f32a0891b92fab010e8.bundle"),
        # 负控 1：我们装机的相对路径（B 方案）
        ("..\\..\\..\\BA_Mod\\t72b_assets_all_5a72.bundle", "t72b_assets_all_5a72.bundle"),
        # 负控 2：正斜杠 + 绝对路径
        ("C:/Steam/steamapps/common/broken_arrow/BA_Mod/x.bundle", "x.bundle"),
    ],
)
def test_bundle_name_is_reduced_to_file_name(raw: str, want: str) -> None:
    assert original_bundle_name(raw) == want


def test_empty_is_empty() -> None:
    assert original_bundle_name(None) == ""
    assert original_bundle_name("  ") == ""
