"""[守] 词典里 IsDefault 那一族的中文名。

用户 2026-10-03（指着右栏 Armors.IsDefault 那一行）：「**这个词条的意思是是否默认**，
把所有类似词条的注释都改过来」。

真源是 core/fields/*.json（产品自带词典，界面直接读它）；
运行时名字走 core/edit/dictionary.py::field_info().cn。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ROOT / "src" / "ba_mod_maker" / "core" / "fields"
OLD = "允许被单位直接引用"
NEW = "是否默认"


def _entries(node, path=""):
    """摊平词典（两种形状都认：扁平键 或 表.列 的嵌套）。"""
    if isinstance(node, dict):
        if "cn" in node or "doc" in node:
            yield path, node
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _entries(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _entries(value, f"{path}[{i}]")


def test_every_isdefault_entry_is_called_the_default_flag() -> None:
    """每一处 .IsDefault 的中文名都必须是「是否默认」，且旧名一句都不许留。"""
    seen: list[str] = []
    for name in ("field_labels.json", "field_enums.json"):
        data = json.loads((FIELDS / name).read_text(encoding="utf-8"))
        for path, leaf in _entries(data):
            cn = leaf.get("cn")
            if not isinstance(cn, str):
                continue
            assert cn != OLD, f"{name} 的 {path} 还留着旧名 {OLD!r}"
            if path.endswith(".IsDefault"):
                assert cn == NEW, f"{name} 的 {path} 叫 {cn!r}，应当是 {NEW!r}"
                seen.append(path)
    assert len(seen) == 12, f"该有 12 处 .IsDefault（两份词典各 6），实际 {len(seen)}：{seen}"
