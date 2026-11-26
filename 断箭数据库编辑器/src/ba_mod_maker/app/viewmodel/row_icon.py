"""一行该画哪张**游戏里的真图标**（零 Qt）。

用户口径（2026-10-01）：「我不是要 emoji，我是要图标，单位图标、武器图标、
弹药图标、改装图标这种」—— 中栏标题与面包屑上那一行前面要画**游戏里那张图**。

## 只回答"画哪张"，不取图

族名与键列**只有一份出处**：`core/assets/families.py::ROW_ICON_SOURCES`（+ `row_icon_source`）。
这里只负责"把这一行的键读出来"，取图由界面调 `IconStore.pixmap(family, key)`：
那是 **bundle 直读**那条路（catalog 键 → 资产路径 → Texture2D，见 `docs/adr/0003`）。

⛔ **不许**在这里（或在界面里）直接读 `cache/icons` 下的 PNG：那是"这台机器没装游戏"
时的兜底，而且旧提取按纹理名撞、把 151 张取成了 outline 空心版（用户报过的 Konkurs）。
ADR 0003 §决定 4 写死了这条。
"""

from __future__ import annotations

from dataclasses import dataclass

from ba_mod_maker.core.assets.families import enum_icon_source, row_icon_source
from ba_mod_maker.core.data.dataset import Instance
from ba_mod_maker.core.edit.foreign import find_row


@dataclass(frozen=True, slots=True)
class RowIcon:
    """一行对应的图标地址：族名 + 那一行键列里的键。"""

    family: str
    key: str


@dataclass(frozen=True, slots=True)
class RowMark:
    """**图标位**上该显示的那一样东西：真图（可能还没到）+ 没图时的兜底记号。

    用户口径（2026-10-01 第二轮）：「我不是要 emoji，我是要图标」——
    所以图标位**只能有一个**：有真图画真图，没真图才显示 emoji 兜底；
    两样都没有时整块藏起来（不留空框）。
    """

    icon: RowIcon | None = None
    emoji: str = ""

    @property
    def is_empty(self) -> bool:
        return self.icon is None and not self.emoji


def row_icon(instance: Instance | None, table: str, row_id: int) -> RowIcon | None:
    """这一行的图标；**没有键 / 没这一列 / 没这一行 / 键是空串** 都给 None。

    给 None 不是错误：界面照口径退回**表 emoji**（图标位只能有一个，见 `RowMark`）。

    两条路线，出处都在 `core/assets/families.py`：

    1. **整列就是一个键**（`ROW_ICON_SOURCES`）：Units / Weapons / Ammunitions / Options；
    2. **列是枚举、键要查常量表**（`ENUM_ICON_SOURCES`）：改装槽 —— `Modifications.Type`
       是 `ModificationType`，全游戏只有「装甲槽盾牌」与「通用齿轮」两张按钮图
       （反编译 `IconsLoader.LoadModificationButtonIcon`，见 families.py 的注释）。
       这一条**永远有键**（没登记的类型落到兜底那张），不是"没图"。
    """
    if instance is None or row_id <= 0:
        return None
    source = row_icon_source(table)
    enum_source = None if source is not None else enum_icon_source(table)
    if source is None and enum_source is None:
        return None
    target = instance.table(table) if table in instance.table_names else None
    if target is None:
        return None
    row = find_row(instance, table, row_id)
    if row is None:
        return None

    if source is not None:
        # 路线一：**整列就是一个键**（Units/Weapons/Ammunitions/Options）。
        family, column = source
        try:
            column_index = target.column_index(column)
        except KeyError:
            return None  # 这一版数据里没这一列（映射不该因此炸）
        value = row[column_index]
        key = value.strip() if isinstance(value, str) else ""
        return RowIcon(family=family, key=key) if key else None

    # 路线二：**列是枚举，键要查常量表**（改装槽的 Type -> 装甲 / 通用两张图）。
    family, column, keys, fallback = enum_source
    try:
        column_index = target.column_index(column)
    except KeyError:
        return None
    raw = row[column_index]
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    return RowIcon(family=family, key=keys.get(raw, fallback))


__all__ = ["RowIcon", "RowMark", "row_icon"]
