"""编成台的**形态判据**：步兵 / 载具 / 飞机三类各落哪一张。

用户 2026-10-01 + 2026-10-02 拍板：
- 「载具 = ① 炮塔四层层级」；「**直升机 + 固定翼 = 挂载图**」；
- 中栏：步兵 8 块 = ①班 · ②班用武器 · ③–⑦ 属性五行 · ⑧改装槽；载具 9 块 = ①**挂载槽** ·
  ②炮塔层级 · ③改装槽 · ④–⑧ 同上 ＋ 运输；**飞机 9 块** = ①**挂载槽** · ②改装槽 ·
  ③炮塔层级 · ④–⑨ 同上（③ 是 2026-10-02 用户「在改装槽下，我还要能继续修改武器和弹药」加的；
  载具的 ① 挂载槽是 2026-11-11 用户「让所有载具都显示这些挂载槽」加的）；
  「⑤ 其他入口」2026-10-02 已整块删掉。

这一份只守**判据**（零 Qt、不建控件），装配那一层的事守 `tests/test_bench_page.py`。

判据为什么这么定（`bench_page._flies` 的注释里有完整论证）：
- `Units.Type` 是游戏枚举会漂，所以**不单用它**；
- `Mobility.FlyPresetId > 0` 是数据本身，但**只有固定翼有**（实测 Mi-8MTV-5 与 AH-64 都是 0），
  所以它单独用会把直升机判成载具 —— 两条取**并集**才是用户要的"直升机 + 固定翼"。
"""

from __future__ import annotations

import pytest

from ba_mod_maker.app.screens.editor.bench_page import (
    KIND_AIR,
    KIND_SQUAD,
    KIND_TIPS,
    KIND_VEHICLE,
    bench_kind,
)
from ba_mod_maker.app.viewmodel.unit_card import CardData
from ba_mod_maker.core.data import find_dataset_root

DATASET_ROOT = find_dataset_root()

pytestmark = pytest.mark.skipif(DATASET_ROOT is None, reason="未找到 DataBaseCompiled 数据源")

#: 样本（实例 52085 实测）。每一条都注明**为什么它必须落这一类**。
SAMPLES: tuple[tuple[int, str, str], ...] = (
    (70, "步兵 Marine Raiders（14 人班）", KIND_SQUAD),
    # 载具：地面车 —— 炮塔是"几座真的炮塔"，不是挂架。
    (1, "载具 BTR-82", KIND_VEHICLE),
    (206, "载具 BTR-ZD Skrezhet（设计稿那张）", KIND_VEHICLE),
    (21, "载具 Stryker RV（变体样本）", KIND_VEHICLE),
    # 舰船：也不飞，按载具形态走。
    (139, "舰船 Zubr", KIND_VEHICLE),
    # 直升机：FlyPresetId 是 0，只能靠 Units.Type(8) 判 —— 这正是"两条取并集"的理由。
    (7, "直升机 Mi-8MTV-5", KIND_AIR),
    (17, "直升机 AH-64", KIND_AIR),
    # 固定翼：Type(16) 与 FlyPresetId 都成立。
    (44, "固定翼 Su-57", KIND_AIR),
    (287, "固定翼 F-15E（设计稿那张）", KIND_AIR),
)


@pytest.fixture()
def data(instance):
    return CardData(instance)


@pytest.mark.parametrize(("unit_id", "label", "want"), SAMPLES)
def test_each_form_lands_on_its_own_bench(data, unit_id, label, want) -> None:
    assert bench_kind(data, unit_id) == want, f"{label}（Units#{unit_id}）判错了形态"


def test_the_three_forms_cover_every_unit_exactly_once(data, instance) -> None:
    """全库 **545** 个单位**一个不漏、一个不重**地落进三类 —— 防"某一类恒空"的假绿。

    实测分布 **198 / 108 / 239**（2026-11-11 · 数据源实例 52739；旧实例 52085 是
    540 个单位 / 194 / 107 / 239 —— 游戏版本换了，多 5 个）。这里的期望值**现算**
    （数 Type 与 FlyPresetId），不是把那些数字抄一遍 —— 抄了就等于拿常量断言常量。
    """
    units = instance.table("Units")
    icol, tcol = units.column_index("Id"), units.column_index("Type")
    types = {r[icol]: r[tcol] for r in units.rows}

    got = {unit_id: bench_kind(data, unit_id) for unit_id in types}
    counts = {
        k: sum(1 for v in got.values() if v == k) for k in (KIND_SQUAD, KIND_VEHICLE, KIND_AIR)
    }

    assert len(got) == 545, f"样本变了：这一条对着全库 545 个单位（实测 {len(got)}）"
    assert sum(counts.values()) == 545, f"有单位没落进任何一类：{counts}"
    assert all(v in (KIND_SQUAD, KIND_VEHICLE, KIND_AIR) for v in got.values()), "出现了第四种形态"

    # 独立复算：步兵 = Units.Type 不是 4/8/16/32 的那些？不对 —— 步兵判据是"有班成员"，
    # 而班成员只长在步兵上。这里只复算**飞行那一类**（可独立从 Type / FlyPresetId 推出）。
    flying = [u for u, t in types.items() if t in (8, 16)]
    assert counts[KIND_AIR] >= len(flying), "直升机/固定翼全都没被判成 air"
    assert counts[KIND_VEHICLE] > 0 and counts[KIND_SQUAD] > 0, f"有一类恒空：{counts}"


def test_a_unit_without_a_real_id_gets_no_bench(data) -> None:
    """还没进单位 / 单位 id 非法 -> 空形态（编成台一个字都不画）。"""
    assert bench_kind(data, -1) == ""
    assert bench_kind(None, 1) == ""


def test_every_kind_has_a_tooltip() -> None:
    """顶栏那颗切换钮的 tooltip 按形态说清进去看什么 —— 三种都得有（加了形态别忘了加文案）。"""
    for kind in (KIND_SQUAD, KIND_VEHICLE, KIND_AIR):
        assert KIND_TIPS.get(kind), f"{kind} 没有 tooltip 文案"
