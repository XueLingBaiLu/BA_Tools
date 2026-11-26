"""一行读数：Options.Turret0Id 全库非零行数（plan 的读经第 2 行要的实跑数字）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ba_mod_maker.core.data import find_dataset_root, load_dataset  # noqa: E402


def main() -> int:
    dataset = load_dataset(find_dataset_root())
    inst = dataset.instance(list(dataset.instances)[-1])
    table = inst.table("Options")
    print(f"options_rows={len(table.rows)}")
    for column in ("Turret0Id", "Turret3Id", "Turret20Id"):
        index = table.column_index(column)
        print(f"{column}_nonzero={sum(1 for row in table.rows if row[index] != 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
