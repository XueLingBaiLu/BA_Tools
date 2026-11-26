"""探针：编成台「挂载槽」块 —— 是否所有载具都显示 + 折叠栏叫什么。

只读（加载真实数据集，不写任何东西）。判据读数：

    vehicle_section_count=<n>        <- 改前 8 / 改后 9
    hardpoints_title=<折叠栏抬头>     <- 改前带「（一个都不折叠）」/ 改后只有「挂载槽」
    vehicle_with_slots=<n>/<total>   <- 载具里真的有挂载数据的那些（证明这块不是空壳）

跑法（仓根 = 断箭数据库编辑器）：

    .venv\\Scripts\\python.exe _work\\probe_bench_hardpoints_vehicle.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ba_mod_maker.app.screens.editor.bench_page import (  # noqa: E402
    KIND_AIR,
    KIND_SQUAD,
    KIND_VEHICLE,
    BenchPage,
    bench_kind,
)
from ba_mod_maker.app.viewmodel.bench_hardpoints import hardpoint_view  # noqa: E402
from ba_mod_maker.app.viewmodel.unit_card import CardData  # noqa: E402
from ba_mod_maker.core.data import find_dataset_root, load_dataset  # noqa: E402

#: 数据源里的实例 id 会随游戏版本变 —— 别写死，取最后一个（当前 52739）。
INSTANCE_ID = None
#: 样本：BTR-ZD（设计稿那张）/ Stryker RV / BTR-82 / 舰船 Zubr（0 格）。
VEHICLES = (206, 21, 1, 139)


def main() -> int:
    root = find_dataset_root()
    if root is None:
        print("NO_DATASET")
        return 2
    dataset = load_dataset(root)
    instance_id = INSTANCE_ID or list(dataset.instances)[-1]
    print(f"instance_id={instance_id} all={list(dataset.instances)}")
    inst = dataset.instance(instance_id)
    data = CardData(inst)

    units = inst.table("Units")
    ids = [row[units.column_index("Id")] for row in units.rows]
    kinds = {uid: bench_kind(data, uid) for uid in ids}
    counts = {
        k: sum(1 for v in kinds.values() if v == k) for k in (KIND_SQUAD, KIND_VEHICLE, KIND_AIR)
    }
    print(f"units_total={len(ids)}")
    print(f"kind_counts={counts}")

    for kind in (KIND_VEHICLE, KIND_AIR):
        attrs = BenchPage._SECTION_ATTRS.get(kind, ())
        print(f"{kind}_section_count={len(attrs)}")
        print(f"{kind}_sections=", ",".join(attrs))

    from ba_mod_maker.app.widgets.bench_hardpoints import TITLE

    print(f"hardpoints_title={TITLE}")

    for uid in VEHICLES:
        view = hardpoint_view(data, uid)
        print(
            f"vehicle_sample id={uid} kind={kinds.get(uid)} cells={len(view.cells)} "
            f"occupied={view.occupied_count} loaded={view.loaded_count} picks={len(view.picks)}"
        )

    vehicles = [uid for uid, kind in kinds.items() if kind == KIND_VEHICLE]
    with_slots = sum(1 for uid in vehicles if hardpoint_view(data, uid).occupied_count > 0)
    print(f"vehicle_with_slots={with_slots}/{len(vehicles)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
