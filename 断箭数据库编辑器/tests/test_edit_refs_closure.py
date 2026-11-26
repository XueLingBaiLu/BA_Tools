r"""`reference_closure` / `orphan_rows` 的判据（删一行会牵连谁）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests/test_edit_refs_closure.py -n 0 -q
      -> 8 passed

## 为什么要单独一个文件

这两个函数是「删除」那条线上的**唯一一把尺子**：级联删哪些、删完谁会成孤儿，全靠它。
上一轮那把废尺子（「整数值命中新增 Id」）就是在这里被换掉的 —— 它只匹配到 6 行，
而 6 恰好是现场 zip 里 Units 的 Id 上限，纯巧合。所以本文件的第一条是**正控 + 回读校验**，
不是「跑通了就算」。

⚠ 源 = `DataBaseCompiled/BA_Mod.zip`（**只读**）；⛔ 用例绝不碰游戏目录、不写任何东西。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from ba_mod_maker.cli.source import load
from ba_mod_maker.core.edit.refs import FOREIGN_TABLE, orphan_rows, reference_closure

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT.parent / "DataBaseCompiled" / "BA_Mod.zip"

#: 正控：Units#3 = BMP-3。实测直接引用者 38、传递闭包 43（多出来的 5 行是 Options）。
DEEP = ("Units", 3, 38, 43)
#: 负控：Specializations#11 = Editor，整库没有一行指向它。
LONELY = ("Specializations", 11)


def _rows(table: str) -> list[dict]:
    with zipfile.ZipFile(SOURCE) as z:
        return json.loads(z.read(f"{table}.json"))


def test_positive_control_direct_references_are_verified_against_raw_json() -> None:
    """★ 正控：38 条引用者，**逐条回读原始 JSON 校验**（⛔ 不许编、不许只信计数）。"""
    table, row_id, direct, _ = DEEP
    with load(str(SOURCE)) as loaded:
        hits = orphan_rows(loaded.instance, [(table, row_id)])
    assert len(hits) == direct
    by_table: dict[str, list] = {}
    for hit in hits:
        by_table.setdefault(hit.table, []).append(hit)
    for name, group in by_table.items():
        raw = {int(r["Id"]): r for r in _rows(name)}
        for hit in group:
            where = f"{name}#{hit.row_id}.{hit.column}"
            assert raw[hit.row_id][hit.column] == row_id, f"{where} 并不指向它"
    assert set(by_table) == {
        "UnitAbilities", "UnitPropulsions", "TurretUnits", "WeaponAmmunitions",
        "SensorUnits", "Modifications", "UnitArmors", "TransportAvailabilities",
    }


def test_negative_control_reports_nothing() -> None:
    """★ 负控：没有引用的行必须报空 —— ⛔ 否则「有引用就拒」会变成「永远拒」。"""
    with load(str(SOURCE)) as loaded:
        assert orphan_rows(loaded.instance, [LONELY]) == ()
        assert reference_closure(loaded.instance, [LONELY]) == ()


def test_the_closure_is_one_level_deeper_than_the_direct_references() -> None:
    """★ 闭包**不止一层**：Units#3 直接 38、闭包 43。

    多出来的 5 行是 Options —— 它们指的是这个单位的 Modifications，
    而 Modifications 指的是这个单位。只做一层的实现会漏掉这 5 行。
    """
    table, row_id, direct, closure = DEEP
    assert closure > direct, "两者撞在一起的话，这条用例就抓不到「只做一层」"
    with load(str(SOURCE)) as loaded:
        direct_hits = {(h.table, h.row_id) for h in orphan_rows(loaded.instance, [(table, row_id)])}
        nodes = reference_closure(loaded.instance, [(table, row_id)])
    assert len(direct_hits) == direct
    assert len(nodes) == closure
    assert direct_hits <= set(nodes), "直接引用者必须都在闭包里"
    extra = set(nodes) - direct_hits
    assert {name for name, _ in extra} == {"Options"}


def test_the_roots_themselves_are_not_in_the_closure() -> None:
    """★ 闭包答的是「牵连进来的有哪些」—— **不含**点名的行（含了会让调用方重复计数）。"""
    table, row_id, _, _ = DEEP
    with load(str(SOURCE)) as loaded:
        assert (table, row_id) not in reference_closure(loaded.instance, [(table, row_id)])


def test_the_closure_is_deterministic_and_cycle_safe() -> None:
    """★ 同输入两次完全相同；坐标不重复（数据里确实可能成环）。"""
    with load(str(SOURCE)) as loaded:
        first = reference_closure(loaded.instance, [DEEP[:2]])
        second = reference_closure(loaded.instance, [DEEP[:2]])
    assert first == second, "顺序或内容在两次调用之间变了"
    assert len(first) == len(set(first)), "闭包里有重复坐标（成环时最容易出）"


def test_the_limit_refuses_instead_of_silently_truncating() -> None:
    """★ 超上限**抛错**，⛔ 不静默截断 —— 被截断的闭包会让人以为「只有这么多」。"""
    with load(str(SOURCE)) as loaded, pytest.raises(ValueError, match="牵连闭包"):
        reference_closure(loaded.instance, [DEEP[:2]], limit=1)


def test_orphan_rows_dedupes_the_same_hit() -> None:
    """★ 同一行同一个列只报一次（一行有两个列同时指过来时最容易重复计）。"""
    with load(str(SOURCE)) as loaded:
        hits = orphan_rows(loaded.instance, [DEEP[:2]])
    assert len(hits) == len({(h.table, h.row_id, h.column) for h in hits})


def test_row_id_zero_is_never_a_reference() -> None:
    """★ `Id=0` **不是一行**（外键里的 0 表示「不使用」，全库约定）⇒ 对 0 的反查必须直接空。

    不这么判的话，全库每一行都会变成 `Id=0` 那一行的「引用者」—— 噪声会盖掉真信号。
    这条用例先**证明这份数据里真的有大量 0**，再说反查为空；否则它是空转（AGENTS §0.9 动作 2）。
    """
    with load(str(SOURCE)) as loaded:
        instance = loaded.instance
        zeros = 0
        for name in instance.table_names:
            table = instance.table(name)
            pairs = [(c, table.column_index(c)) for c in table.columns if c in FOREIGN_TABLE]
            if not pairs:
                continue
            for row in table.rows:
                for _, index in pairs:
                    if row[index] == 0:
                        zeros += 1
        assert zeros > 100, f"这份数据里只有 {zeros} 个 0，这条用例测不出东西"
        for name in instance.table_names:
            assert orphan_rows(instance, [(name, 0)]) == ()
            assert reference_closure(instance, [(name, 0)]) == ()
