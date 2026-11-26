"""读一条 Armors 表的实跑数字（plan 的 database 那一行要用）。"""

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "src")

from ba_mod_maker.core.data import load_dataset


def main() -> int:
    dataset = load_dataset(Path(r"D:\断箭模组制作\DataBaseCompiled"))
    inst_id = list(dataset.instances)[-1]
    inst = dataset.instance(inst_id)
    table = inst.table("Armors")
    names = [str(c) for c in table.columns]
    print(f"instance={inst_id} armors_rows={len(table.rows)}")
    print("armors_columns=" + repr(names))
    id_col = table.column_index("Id")
    hits = [row for row in table.rows if row[id_col] == 1]
    if hits and "MaxHealthPoints" in names:
        hp_col = table.column_index("MaxHealthPoints")
        print(f"armors_id1_maxhealthpoints={hits[0][hp_col]}")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
