"""列出**所有位掩码列**（kind=mask）—— 统一设计要覆盖的完整清单。

只读：读词典 field_labels / field_enums，不碰数据库实例。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ba_mod_maker.core.edit.dictionary import field_enums  # noqa: E402


def main() -> int:
    enums = field_enums()
    masks = [(key, info) for key, info in enums.items() if str(info.get("kind")) == "mask"]
    print(f"mask_columns={len(masks)}")
    for key, info in sorted(masks):
        bits = [k for k in info.get("values", {}) if int(k) != 0]
        print(f"  {key}  bits={len(bits)}  cn={info.get('cn', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
