r"""⛔ 尺子：产品界面上**不许有「换」这个入口**（2026-10-19 用户命令；AGENTS §0.10）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_no_swap_entry.py -q`
-> 删掉三颗「🔁 换一个 / 🔁」之前 3 条红（row_entries / editable_card / ammo_block），
   删完 **0 条红**；负控（往临时文件里塞一句「🔁 换一个」）**照旧能红**。

## 量的是什么（只量**用户看得见的控件文案**，不量 docstring）

| # | 面 | 取法 |
|---|---|---|
| ① | `QPushButton("…")` 的文案 | 正则扫 `app/**/*.py` |
| ② | `.setText("…")` / `.setToolTip("…")` 的文案 | 同上 |
| ③ | `QMenu.addAction("…")` 的文案 | 同上 |

## ⛔ 边界（不在范围内，别拿它当违规）

- **docstring / 注释**：说明「这里原来有 🔁 换一个，已删」是**证据**，不是入口；
- **导航文案**：界面里「中栏换成那一行」这类视图切换（见 `test_no_swap_wording.py`）；
- **标识符名**：`plan_swap` / `install_swap` / `--swap-yz`（坐标轴开关）。
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "src" / "ba_mod_maker" / "app"

_WIDGET_TEXT = re.compile(
    r'(?:QPushButton|setText|setToolTip|addAction)\(\s*(?:f?)"([^"]*)"'
)
_BANNED = ("🔁", "换一个", "换…")


def widget_texts(root: Path) -> list[tuple[str, str]]:
    """(文件名, 文案) —— 纯读，不 import Qt。"""
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _WIDGET_TEXT.finditer(text):
            out.append((path.name, match.group(1)))
    return out


def test_no_swap_widget_text() -> None:
    hits = [(name, value) for name, value in widget_texts(APP)
            if any(bad in value for bad in _BANNED)]
    assert not hits, "界面上还有「换」的入口：" + repr(hits[:5])


def test_the_ruler_can_go_red(tmp_path: Path) -> None:
    """负控：往临时目录里放一颗「🔁 换一个」按钮，尺子必须抓到。"""
    sample = tmp_path / "fake.py"
    sample.write_text('btn = QPushButton("🔁 换一个")\n', encoding="utf-8")
    hits = [(n, v) for n, v in widget_texts(tmp_path) if any(b in v for b in _BANNED)]
    assert len(hits) == 1
