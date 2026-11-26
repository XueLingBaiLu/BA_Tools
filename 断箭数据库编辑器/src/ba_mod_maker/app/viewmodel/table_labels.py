"""表名 / 表记号这两张**登记表**（中文名 + emoji）—— 全产品唯一出处。

原来它们住在 `app/screens/editor/entries_panel.py`。搬出来只有一个原因：
`app/widgets/` 下的控件（数据表页的左栏）也要用，而 `from ...screens.editor.entries_panel import`
会顺着 `screens/editor/__init__.py` 把 `editable_card` 拉进来 —— 那条链又 import 回 widgets，
于是**成环**（实测：只能用函数内延迟 import 绕）。

放在 `app/viewmodel/` 下是因为它零 Qt、零依赖（`tests/test_core_layering.py` 会扫这一层）：
谁都能在模块级 import，没有环。

`app/screens/editor/entries_panel.py` 仍然 re-export 这几个名字（老调用方一行没改）。

## Emoji

用户口径：「多使用 Emoji，让所对应的东西看起来更直观」。
**只放在有语义的地方**（表是什么、这个按钮干什么），不撒花。
只用 Unicode 6.0 时代就有的那批 —— 新 emoji 在旧 Windows 上是豆腐块。
"""

from __future__ import annotations

#: 表的中文名。词典是**列**级的（field_labels 只有列），表名这里补一张小表。
#: 与 tools/gen_unit_editor_mock.py::TABLE_CN 同一份口径 —— 设计图上的分组标题就是它。
TABLE_CN: dict[str, str] = {
    "Units": "单位本体",
    "UnitPropulsions": "机动归属",
    "UnitArmors": "装甲归属",
    "SensorUnits": "传感器归属",
    "UnitAbilities": "能力归属",
    "TurretUnits": "炮塔归属",
    "WeaponAmmunitions": "武器携弹",
    "SquadMembers": "班组成员",
    "SquadWeapons": "班用武器",
    "Modifications": "改装槽",
    "Options": "改装选项",
    "TransportAvailabilities": "运输配对",
    "SpecializationAvailabilities": "专精可用性",
    "Countries": "阵营",
    "Specializations": "师级专精",
    "Armors": "装甲模板",
    "Mobility": "机动模板",
    "Sensors": "观瞄模板",
    "Abilities": "能力模板",
    "Turrets": "炮塔模板",
    "Weapons": "武器模板",
    "Ammunitions": "弹药模板",
    "TurretWeapons": "炮塔挂载",
    # 这张表的**规范名是 FlyPresets**（条目名 / 磁盘文件名才是 PlaneFlyPresets.json）——
    # 键必须用规范名：`load_dataset` 交出来的表名已经归一（见 loader 的模块说明）。
    "FlyPresets": "飞行包线",
}

#: 表 -> Emoji（用户口径：让所对应的东西看起来更直观）。
TABLE_EMOJI: dict[str, str] = {
    "Units": "📦",
    "UnitPropulsions": "🚗",
    "UnitArmors": "🛡",
    "SensorUnits": "📡",
    "UnitAbilities": "✨",
    "TurretUnits": "🗼",
    "WeaponAmmunitions": "🎯",
    "SquadMembers": "👥",
    "SquadWeapons": "🔫",
    "Modifications": "🔧",
    "Options": "⚙",
    "TransportAvailabilities": "🚚",
    "SpecializationAvailabilities": "🎓",
    "Armors": "🛡",
    "Mobility": "🚗",
    "Sensors": "📡",
    "Abilities": "✨",
    "Turrets": "🗼",
    "Weapons": "🔫",
    "Ammunitions": "💥",
    "TurretWeapons": "🔩",
    "FlyPresets": "✈",
    "Countries": "🏳",
    "Specializations": "🎓",
}


def table_title(name: str) -> str:
    """表的中文名（没有就退回表名 —— 不编）。"""
    return TABLE_CN.get(name, name)


def table_emoji(name: str) -> str:
    """表的记号；没登记的给一个中性记号（不留空，也不瞎编语义）。"""
    return TABLE_EMOJI.get(name, "🔹")


__all__ = ["TABLE_CN", "TABLE_EMOJI", "table_emoji", "table_title"]
