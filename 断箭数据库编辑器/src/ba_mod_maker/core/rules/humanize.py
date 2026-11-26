"""把校验文案里的裸 Id 换成**名字**（零 Qt）。

用户口径（15 条硬约定第 14 条）：**界面不给用户看 Id**。而规则书里那些规则
的文案是给开发者看的，长这样（实测）：

    载具 Id 1 只有 2 个步兵位，乘客 Id 125 要坐 8 人（行 Id 1196）
    武器 Id 88 既不在 TurretWeapons 也不在 SquadWeapons 里
    单位 Id 1 没有 SquadMembers 行

设计稿要的是：

    载具 BTR-82 只有 2 个步兵位，乘客 Spetsnaz GRU Mix 要坐 8 人（行 #1196）

## 三条口径

1. **只换认识的词**。engine.py 里带 Id 的模板一共 5 种前缀（单位 / 载具 /
   乘客 / 武器 / 行），逐个登记；**遇到没登记的一律原样留着** ——
   宁可让人看见一个 Id，也不要瞎猜一张表（猜错比不换更糟）。
2. **行 Id 换成「行 #N」**：那本来就是一个行号（同行 Id 更容易被当成外键），
   而且换不成名字（它指向的行常常刚被删掉）。
3. **查不到就保留原样**：`Id 999999` 不会变成「（找不到 …）」——
   文案里那句"找不到"会盖住规则本来的意思。
"""

from __future__ import annotations

import re

from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.foreign import display_name as _display_name
from ba_mod_maker.core.edit.foreign import find_row

#: 「单位」的各种说法 -> Units 表。
#: 载具 / 乘客都是单位（运输配对那条规则两边都是 Units.Id）。
_UNIT_WORDS: tuple[str, ...] = ("单位", "载具", "乘客", "成员")

#: 名词 -> 表名。engine.py 目前只用到「武器」。
_NOUN_TABLE: dict[str, str] = {
    "武器": "Weapons",
    "弹药": "Ammunitions",
    "炮塔": "Turrets",
    "装甲": "Armors",
}

#: 「… Id 123」
_ID_PATTERN = re.compile(r"([\u4e00-\u9fff]{1,4})\s*Id\s*(\d+)")
#: 「行 Id 123」
_ROW_PATTERN = re.compile(r"行\s*Id\s*(\d+)")


def humanize_ids(instance: Instance, message: str) -> str:
    """把文案里的「X Id N」换成「X 名字」。不认识的写法原样保留。"""
    text = _ROW_PATTERN.sub(lambda m: f"行 #{m.group(1)}", message)

    def replace(match: re.Match[str]) -> str:
        word, digits = match.group(1), match.group(2)
        row_id = int(digits, 10)
        table = "Units" if word in _UNIT_WORDS else _NOUN_TABLE.get(word)
        if table is None or table not in instance.table_names:
            return match.group(0)
        if find_row(instance, table, row_id) is None:
            return match.group(0)  # 查不到就保留原样（见模块注释第 3 条）
        return f"{word} {_display_name(instance, table, row_id)}"

    return _ID_PATTERN.sub(replace, text)


__all__ = ["humanize_ids"]
