"""外键：显示**名字**、按**名字**搜（零 Qt）。

用户口径（本轮 15 条硬约定第 14 条）：**外键一律显示名字、按名字搜，
界面不给用户看 Id**。所以"这个 Id 叫什么""打几个字能筛出哪几行"
必须是可直接断言的纯函数 —— 界面只负责画。

## 每一张表的「显示名」列

实测 24 张表的命名习惯分三族：

| 族 | 表 | 列 |
|---|---|---|
| 装备 / 单位 / 弹药 | Units / Weapons / Ammunitions | HUDName |
| 阵营 / 专精 / 改装 | Countries / Specializations / Modifications / Options | UIName |
| 其余模板表 | Armors / Mobility / Sensors / Abilities / Turrets / PlaneFlyPresets | Name |

关联行（SquadMembers / UnitArmors …）没有名字 —— 它们**本来就不给别人引用**，
所以 `display_name` 对它们返回「行 #Id」。这一点是**故意的**：
与其编一个假名字，不如让界面显示一句"这行没有名字"。

## 找不到就不猜

- 表不存在 / 行不存在 -> 返回「（找不到 …）」这种**说明性文本**，不是空串
  （空串在界面上等于"这一格是空的"，那是另一种意思）；
- 搜索命中 0 行 -> 返回空元组。界面照设计稿写「没找到」。

## 两套查法都住在这里（别把口径拆成两份）

| 函数 | 谁在用 | 查什么 |
|---|---|---|
| `search_rows` | RowPicker 的候选列表 | **一张表**里挑一行（Name / HUDName） |
| `lookup_rows` | 检索页 | **整库**找东西（+ 美术文件名，纯数字认 Id） |

"哪一列算名字"的规则只该有一份 —— 所以第二个不另起炉灶，就放在这里。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.edit.refs import FOREIGN_TABLE

#: 表名 -> 显示名列。**首选项**，取不到时按 FALLBACK_NAME_COLUMNS 依次退。
NAME_COLUMN: dict[str, str] = {
    "Units": "HUDName",
    "Weapons": "HUDName",
    "Ammunitions": "HUDName",
    "Countries": "UIName",
    "Specializations": "UIName",
    "Modifications": "UIName",
    "Options": "UIName",
    "Armors": "Name",
    "Mobility": "Name",
    "Sensors": "Name",
    "Abilities": "Name",
    "Turrets": "Name",
    "FlyPresets": "Name",
}

#: 退路顺序。为什么留着：列表里的首选项为空串是常事
#: （实测 Units 里 Name 与 HUDName 都叫 BTR-82，但有的行 HUDName 是空的）。
FALLBACK_NAME_COLUMNS: tuple[str, ...] = ("HUDName", "UIName", "Name")

#: 搜索一次最多返回多少行（界面是下拉，给多了反而难选）。
SEARCH_LIMIT = 40


@dataclass(frozen=True, slots=True)
class NameHit:
    """一条搜索命中。**只有名字与 Id**，界面不显示 Id（除了小字备查）。"""

    table: str
    row_id: int
    name: str
    #: 命中的是哪一列（HUDName / UIName / Name）。
    column: str


def _id_index(table: Table) -> int:
    return table.column_index("Id")


def find_row(instance: Instance, table: str, row_id: int) -> list | None:
    """按 Id 找一行；找不到返回 None（**不抛异常** —— 界面要能显示"找不到"）。"""
    if table not in instance.table_names:
        return None
    if not isinstance(row_id, int) or isinstance(row_id, bool):
        return None
    target = instance.table(table)
    index = _id_index(target)
    for row in target.rows:
        if row[index] == row_id:
            return row
    return None


def name_columns(instance: Instance, table: str) -> tuple[str, ...]:
    """这张表的「显示名」候选列，**按能不能读排序**。

    顺序 = 表的首选列（NAME_COLUMN）-> Name -> HUDName -> UIName，去重。
    为什么把 Name 排到 UIName 前面：见 `display_name` 的说明 ——
    这一版数据里 UIName 常常是本地化键或内部标识。
    """
    if table not in instance.table_names:
        return ()
    columns = instance.table(table).columns
    out: list[str] = []
    for candidate in (NAME_COLUMN.get(table), "Name", "HUDName", "UIName"):
        if candidate and candidate in columns and candidate not in out:
            out.append(candidate)
    return tuple(out)


def name_column(instance: Instance, table: str) -> str:
    """这张表用哪一列当显示名；一列都不合适时返回空串。"""
    if table not in instance.table_names:
        return ""
    columns = instance.table(table).columns
    preferred = NAME_COLUMN.get(table)
    if preferred and preferred in columns:
        return preferred
    for candidate in FALLBACK_NAME_COLUMNS:
        if candidate in columns:
            return candidate
    return ""


#: 看着像**本地化键**的值（ui_spec_mech_name 这种）。
#: 它本身不是给人看的名字 —— 游戏靠它去查本地化表，我们这边查不到，显示出来就是天书。
_LOC_KEY = re.compile(r"^ui_[a-z0-9_]+$")

#: 看着像**内部标识**的值（Custom_Slot_Weapon_package）。
#: 判据：有下划线、又没有空格 —— 人写的名字（Armor BTR80 Base）不会长这样。
_INTERNAL = re.compile(r"^[A-Za-z0-9_.-]*_[A-Za-z0-9_.-]*$")


def is_readable_name(value: str) -> bool:
    """这个值能不能直接给人看。"""
    text = value.strip()
    if not text:
        return False
    return not _LOC_KEY.match(text) and not _INTERNAL.match(text)


# --------------------------------------------------------------------------- #
# 同形字折叠（**只给匹配用**；显示与数据一个字节都不动）
# --------------------------------------------------------------------------- #

#: 西里尔 -> 拉丁 的**视觉同形**对。数据里真踩到的那个（2026-09-30 用户报的）：
#: `Weapons.HUDName` 写着「2А72 30mm」，那个 «А» 是**西里尔 А（U+0410）**，
#: 而用户键盘打的是拉丁 A（U+0041）—— 于是"打 2A72 搜不出来、打 72 反而能搜出来"。
#: 大小写都收（А->A、а->a）：折叠**保留大小写**，归大小写仍交给 `.casefold()`。
_CONFUSABLES: dict[int, str] = {
    0x0410: "A",  # А
    0x0412: "B",  # В
    0x0415: "E",  # Е
    0x0401: "E",  # Ё
    0x0417: "3",  # З（数字 3 的同形）
    0x041A: "K",  # К
    0x041C: "M",  # М
    0x041D: "H",  # Н
    0x041E: "O",  # О
    0x0420: "P",  # Р
    0x0421: "C",  # С
    0x0422: "T",  # Т
    0x0423: "Y",  # У
    0x0425: "X",  # Х
    0x0405: "S",  # Ѕ
    0x0406: "I",  # І
    0x0408: "J",  # Ј
    0x0492: "F",  # Ғ
    0x04BA: "H",  # Һ
    0x04AE: "Y",  # Ү
    0x0430: "a",  # а
    0x0432: "b",  # в
    0x0435: "e",  # е
    0x0451: "e",  # ё
    0x0437: "3",  # з
    0x043A: "k",  # к
    0x043C: "m",  # м
    0x043D: "h",  # н
    0x043E: "o",  # о
    0x0440: "p",  # р
    0x0441: "c",  # с
    0x0442: "t",  # т
    0x0443: "y",  # у
    0x0445: "x",  # х
    0x0455: "s",  # ѕ
    0x0456: "i",  # і
    0x0458: "j",  # ј
    0x0493: "f",  # ғ
    0x04BB: "h",  # һ
    0x04AF: "y",  # ү
}

#: `str.translate` 的表：只建一次（每次搜索都建会白花钱）。
_FOLD_TABLE = str.maketrans(_CONFUSABLES)


def fold_confusables(text: str) -> str:
    """把**视觉同形**的西里尔字符折成拉丁字符（只给匹配用，显示不动）。

    - 保留大小写：`А`->`A`、`а`->`a`，所以调用方照旧 `.casefold()` 归大小写；
    - 不碰数字、不碰拉丁字母、不碰其它字符 —— 这不是"归一化整个字符串"，
      只是"把看起来一样的字符看成同一个"。
    """
    return text.translate(_FOLD_TABLE)


def match_text(text: str) -> str:
    """匹配用的归一化：**先折同形字、再 casefold**。

    逻辑只有一份：`search_rows`（候选列表）、`lookup_rows`（检索页）与界面那一侧
    都走这里 —— 免得三处各写一遍"要不要折、什么时候折"。
    """
    return fold_confusables(text).casefold()


#: 没有名字列的「关联行」-> 从它的哪一列**再往下走一跳**，去拿一个人看得懂的名字。
#:
#: 为什么需要（用户 2026-10-02 实机截图）：`TransportAvailabilities.乘客侧` 指向
#: `SpecializationAvailabilities` —— 那是一张关联表（只有 SpecializationId / UnitId /
#: 几档经验），于是被显示成 `SpecializationAvailabilities #773`。用户原话：
#: 「应该是单位的 HUDName，不应该是这么一长串」。往下走一跳就对了：
#: 那张表 #773 的 UnitId = 277 -> `Units#277` = **Scouts**（与同一屏 ④运输 那块显示的一致）。
#:
#: ⚠️ 只走**一跳**：再长的链就该去数据表页看，不在这一格上猜。
LINK_VIA_COLUMN = "UnitId"


def resolved_target(instance: Instance, table: str, row_id: int) -> tuple[str, int] | None:
    """这一行**其实指哪个单位**（没有就 None）。

    这是「显示」与「跳转」共用的**唯一一份**判断 —— 界面拿到它就直接去那一行，
    而不是去那张没有名字的关联表。
    """
    if table not in instance.table_names:
        return None
    if LINK_VIA_COLUMN not in instance.table(table).columns:
        return None
    row = find_row(instance, table, row_id)
    if row is None:
        return None
    value = row[instance.table(table).column_index(LINK_VIA_COLUMN)]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    via_table = FOREIGN_TABLE.get(LINK_VIA_COLUMN)
    if not via_table or via_table not in instance.table_names:
        return None
    return (via_table, value)


def display_name(instance: Instance, table: str, row_id: int) -> str:
    """这一行叫什么（外键就显示它）。找不到时给一句**说明性**的话。

    ❗ 候选顺序有讲究（用户口径："修改栏里的 ID 还是没有显示具体的 HUDName 或者 Name"）：
    这一版数据里「界面名」列（UIName）**常常是本地化键或内部标识** ——
    Specializations.UIName = ui_spec_mech_name、Modifications.UIName =
    Custom_Slot_Weapon_package —— 而 Name 才是人看的（Mechanized Infantry、Weapons BTR82）。
    所以：**先挑能读的**；实在没有才退回原样（宁可显示天书，也不要空着 ——
    空着看起来像"这一行没名字"）。
    """
    if table not in instance.table_names:
        return f"（没有表 {table}）"
    row = find_row(instance, table, row_id)
    if row is None:
        return f"（找不到 {table} #{row_id}）"
    target = instance.table(table)
    candidates: list[str] = []
    for candidate in (NAME_COLUMN.get(table), "Name", "HUDName", "UIName"):
        if candidate and candidate in target.columns and candidate not in candidates:
            candidates.append(candidate)
    fallback = ""
    for candidate in candidates:
        value = row[target.column_index(candidate)]
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if is_readable_name(text):
            return text
        fallback = fallback or text
    if fallback:
        return fallback
    # 关联行没有名字：如实说它是第几行，不编一个假名字。
    #
    # ❗ 想更进一步（"它指着的那个单位叫什么"）的调用方**自己**调 `resolved_target` ——
    #    别把那一跳塞进这里：`display_name` 还被 `row_label`（挑行的下拉）等地方用，
    #    那些地方要的是"这一行是什么"，不是"它指着谁"。
    #    （2026-10-02 实测踩过：塞进来之后 `row_label` 把「专精 X · 单位 Y」缩成了一个单位名。）
    return f"{table} #{row_id}"


#: 关联行的标签里最多拼几个外键（再多就是一面墙）。
LABEL_MAX_FOREIGN = 2

_PAREN = re.compile(r"（[^）]*）")
_TRAILING_ID = re.compile(r"(?:行\s*)?ID\s*$")


def _short_column_cn(table: str, column: str) -> str:
    """列的中文名缩成拼标签用的短名。

    “专精 ID” -> “专精”；“乘客侧（可用性行 ID）” -> “乘客侧”；查不到就原样给列名。
    """
    # 延迟导入：词典住在 core/fields 下，这一层只借它的中文列名。
    from ba_mod_maker.core.edit.dictionary import field_info

    text = _PAREN.sub("", field_info(table, column).cn).strip()
    return _TRAILING_ID.sub("", text).strip() or column


def _label_of_row(
    instance: Instance,
    table: str,
    target: Table,
    row: list,
    memo: dict[tuple[str, int], str],
) -> str:
    """没有名字列的行：拿它登记的**外键**拼一句能读的话。

    例：`SpecializationAvailabilities#773` = `专精 机械化步兵 · 单位 BTR-80`。
    被指向那一行自己也没名字时（SA 指 SA 这种）**不往里堆「表 #Id」** ——
    宁可退回「表 #Id」，也不要拼出一串天书。
    """
    id_index = _id_index(target)
    parts: list[str] = []
    index_of = target.column_index
    for column in target.columns:
        ref = FOREIGN_TABLE.get(column)
        # ReplaceUnitId 指向的是“另一个单位”，不是这一行的组成部分（见 refs.NOT_FOREIGN）。
        if not ref or ref == table or column == "ReplaceUnitId":
            continue
        value = row[index_of(column)]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            continue
        name = memo.get((ref, value))
        if name is None:
            name = display_name(instance, ref, value)
            memo[(ref, value)] = name
        if not name or name.startswith("（") or name == f"{ref} #{value}":
            continue
        prefix = _short_column_cn(table, column)
        parts.append(f"{prefix} {name}" if prefix else name)
        if len(parts) >= LABEL_MAX_FOREIGN:
            break
    return " · ".join(parts) if parts else f"{table} #{row[id_index]}"


def row_label(instance: Instance, table: str, row_id: int) -> str:
    """这一行在**候选列表**里叫什么：名字列读不出来就按外键拼。

    为什么不改 `display_name`：它对关联行的「表 #Id」是**故意的**（“与其编一个假名字，”
    “不如说这行没有名字”，还有用例守着）。候选列表的诉求不一样 —— 那里一条都不能少，
    而「运输」那一块的候选（TransportAvailabilities / SpecializationAvailabilities）
    恰恰整张表都没有名字列。
    """
    if table not in instance.table_names:
        return f"（没有表 {table}）"
    row = find_row(instance, table, row_id)
    if row is None:
        return f"（找不到 {table} #{row_id}）"
    # 有名字的表照旧走 display_name（别抢戏）；只有**名字一个都读不出来**的
    # 关联行才退到外键拼法。
    name = display_name(instance, table, row_id)
    if not name.startswith("（") and name != f"{table} #{row_id}":
        return name
    return _label_of_row(instance, table, instance.table(table), row, {})


def search_rows(
    instance: Instance, table: str, needle: str, *, limit: int = SEARCH_LIMIT
) -> tuple[NameHit, ...]:
    """按名字筛这张表的行。**Name 与 HUDName 两个键都认**。

    为什么两个键都认：数据里 Name 与 HUDName 常常一模一样（BTR-82），
    但也有只有其中一个有值、另一个是空串的行；只认一个就会出现
    "明明库里有这个名字却搜不到"。匹配是**不区分大小写**的子串匹配。

    ❗ **整张表都没有名字列时也要列**（2026-09-30 修）：关联行
    （TransportAvailabilities / SpecializationAvailabilities…）一个 Name/HUDName
    都没有，早先这里直接 `return ()` —— 「运输」那一块的「＋ / 换…」候选恒为 0 条，
    用户实机看到的就是“点开一个空框”。现在退一步：用 `row_label` 按外键拼出
    「专精 机械化步兵 · 单位 BTR-80」，筛的时候也拿它匹配（打 “Stryker” 能筛到）。
    """
    if table not in instance.table_names:
        return ()
    target = instance.table(table)
    id_index = _id_index(target)
    # 折同形字（西里尔 А / 拉丁 A 这类）：匹配时折、显示不动 ——
    # 数据里 Weapons.HUDName 用的就是西里尔那个 А，不折的话打拉丁 2A72 一条都搜不到。
    text = match_text(needle.strip())
    hits: list[NameHit] = []
    indexes = [(name, target.column_index(name)) for name in name_columns(instance, table)]
    if not indexes:
        memo: dict[tuple[str, int], str] = {}
        for row in target.rows:
            label = _label_of_row(instance, table, target, row, memo)
            if text and text not in match_text(label):
                continue
            hits.append(NameHit(table=table, row_id=row[id_index], name=label, column=""))
            if len(hits) >= limit:
                break
        if not text:
            hits.sort(key=lambda hit: hit.name.casefold())
        return tuple(hits)
    for row in target.rows:
        chosen: NameHit | None = None
        unreadable: NameHit | None = None
        for name, index in indexes:
            value = row[index]
            if not isinstance(value, str) or not value.strip():
                continue
            if text and text not in match_text(value):
                continue
            hit = NameHit(
                table=table,
                row_id=row[id_index],
                name=value.strip(),
                column=name,
            )
            if is_readable_name(value):
                chosen = hit
                break
            unreadable = unreadable or hit
        chosen = chosen or unreadable
        if chosen is not None:
            hits.append(chosen)
        if len(hits) >= limit:
            break
    # 空前缀 = "把这张表列出来"：按名字排序，方便人挑。
    if not text:
        hits.sort(key=lambda hit: hit.name.casefold())
    return tuple(hits)


def reference_summary(instance: Instance, table: str, row_id: int) -> str:
    """外键的小字：**名字** + 「表 #Id · 列名」的一半（列名由调用方补）。

    这里只给"名字（表 #Id）"这一半，因为列名那一半调用方手里就有。
    """
    return f"{display_name(instance, table, row_id)} · {table} #{row_id}"


# --------------------------------------------------------------------------- #
# 检索页：一个框，整库找东西（2026-09-30 重做）
# --------------------------------------------------------------------------- #

#: 检索页搜的**名字**列。与 `NAME_COLUMN` 不是一回事：
#: 那是"这一行**显示**叫什么"，这里是"用户**打什么字**能把这行找出来"。
LOOKUP_NAME_COLUMNS: tuple[str, ...] = ("Name", "HUDName", "UIName")

#: 检索页搜的**美术文件名**列（图标 / 缩略图 / 立绘）。
#: 用户口径（_work/design/overhaul3.png 底部那条）：「输入名字 / HUD 名 / UIName /
#: 图标·缩略图·立绘文件名 / Id，24 张表一起搜」。
#: 做图标的人问的是"这张图是哪个单位的" —— 那串文件名只写在美术列里，
#: 不搜它就只能人肉翻 24 张表。实测这些列只分布在 6 张表上，表里没有的自动跳过。
LOOKUP_ART_COLUMNS: tuple[str, ...] = (
    "OptionPicture",
    "ThumbnailFileName",
    "ThumbnailOverride",
    "HUDIcon",
    "PortraitFileName",
    "PortraitOverride",
)

#: 检索页一屏最多给多少条（再多就是一面墙）。超出的**如实报**「共 N 条」。
LOOKUP_LIMIT = 100

#: 像不像的三档：完全相等 > 以它开头 > 只是含它。数字越小越靠前。
_RANK_EXACT = 0
_RANK_PREFIX = 1
_RANK_PART = 2


@dataclass(frozen=True, slots=True)
class LookupHit:
    """检索页的一条命中：**一行一条**，不是"一格一条"。

    同一行有两列都命中（Options 的 UIName 与 OptionPicture 常常是同一串字）
    只算一条 —— `columns` 把命中的列全记下来，界面显示「UIName · OptionPicture」。
    """

    table: str
    row_id: int
    name: str
    #: 命中的列，至少一个。
    columns: tuple[str, ...]
    #: 命中的那个值。Id 命中时就是行 Id（int）。
    value: Any

    @property
    def column(self) -> str:
        """第一个命中的列 —— 跳转要用**真列名**（拼起来的串不是列名）。"""
        return self.columns[0] if self.columns else ""

    @property
    def column_text(self) -> str:
        """界面上「命中的是哪一列」那一格。"""
        return " · ".join(self.columns)


@dataclass(frozen=True, slots=True)
class LookupResult:
    """一次检索的全部结果。`hits` 被 `limit` 截过，`total` 是**真数**。"""

    query: str
    hits: tuple[LookupHit, ...]
    total: int
    truncated: bool

    @property
    def count(self) -> int:
        return len(self.hits)

    def by_table(self) -> dict[str, list[LookupHit]]:
        """按表分组，保持命中顺序。"""
        grouped: dict[str, list[LookupHit]] = {}
        for hit in self.hits:
            grouped.setdefault(hit.table, []).append(hit)
        return grouped


def lookup_columns(instance: Instance, table: str) -> tuple[str, ...]:
    """这张表里检索页要搜的列（名字 + 美术）；表里没有的跳过。

    实测 24 张表里只有 13 张有名字列、6 张有美术列 —— 关联表
    （UnitArmors / SquadWeapons…）一个都没有，它们本来就不给人查，
    所以"搜不到"在这里是**真没有**，不是漏了。
    """
    if table not in instance.table_names:
        return ()
    columns = instance.table(table).columns
    return tuple(
        column for column in (*LOOKUP_NAME_COLUMNS, *LOOKUP_ART_COLUMNS) if column in columns
    )


def lookup_rows(
    instance: Instance,
    query: str,
    *,
    limit: int = LOOKUP_LIMIT,
    tables: Sequence[str] | None = None,
) -> LookupResult:
    """整库检索：名字 / HUD 名 / UIName / 美术文件名，**纯数字按 Id 精确命中**。

    与 `search_rows` 的分工（别把两个合成一个）：
    那个是「**一张表**里挑一行」—— RowPicker 的候选列表在用，只认 Name/HUDName；
    这个是「**整库**找东西」—— 检索页在用，跨 24 张表、搜美术列、纯数字认 Id。
    口径不同，所以是两个函数而不是给 `search_rows` 加参数：加参数会把候选列表
    也拖去搜图标文件名、还会让它把 Id 当关键词。

    排序：**先按像不像**（完全相等 > 以它开头 > 含它），再按表的原顺序、行顺序 ——
    搜 `BTR-82` 时 `Units #1` 排最前，而不是让一堆"名字里带这几个字"的先冒出来。

    空关键词**不扫库**（返回空结果）：检索页照设计稿写一句提示，不倒一面墙。
    """
    # 同形字一样要折（检索页走的是另一段匹配代码，别只修一半）。
    needle = match_text(query.strip())
    if not needle:
        return LookupResult(query=query, hits=(), total=0, truncated=False)

    names = tuple(tables) if tables is not None else instance.table_names
    # 纯数字：按 Id 精确命中（口径「搜 18 就要出 Id=18 的那几行」）。
    wanted_id = int(needle) if needle.isdigit() else None
    #: (像不像, 表序, 行序, 表名, 命中的列, 命中的值, 行 Id)
    found: list[tuple[int, int, int, str, tuple[str, ...], Any, int]] = []

    for table_order, name in enumerate(names):
        if name not in instance.table_names:
            continue
        table = instance.table(name)
        columns = lookup_columns(instance, name)
        if not columns and wanted_id is None:
            continue
        id_index = _id_index(table)
        indexes = [(column, table.column_index(column)) for column in columns]
        for row_order, row in enumerate(table.rows):
            row_id = row[id_index]
            matched: list[str] = []
            rank = _RANK_PART + 1
            value: Any = None
            if wanted_id is not None and row_id == wanted_id:
                matched.append("Id")
                rank = _RANK_EXACT
                value = row_id
            for column, index in indexes:
                cell = row[index]
                if not isinstance(cell, str) or not cell:
                    continue
                text = match_text(cell)
                if needle not in text:
                    continue
                matched.append(column)
                if text == needle:
                    this = _RANK_EXACT
                elif text.startswith(needle):
                    this = _RANK_PREFIX
                else:
                    this = _RANK_PART
                if this < rank:
                    rank = this
                    value = cell
            if matched:
                found.append((rank, table_order, row_order, name, tuple(matched), value, row_id))

    found.sort(key=lambda item: item[:3])
    hits = tuple(
        LookupHit(
            table=name,
            row_id=row_id,
            name=row_label(instance, name, row_id),
            columns=columns,
            value=value,
        )
        for _, _, _, name, columns, value, row_id in found[:limit]
    )
    total = len(found)
    return LookupResult(query=query, hits=hits, total=total, truncated=total > limit)


#: **资源键**那一列的下拉一次最多给多少条候选。
#: 实测取值种类：`Units.ThumbnailFileName` 536 / `Weapons.HUDIcon` 740 /
#: `Ammunitions.HUDIcon` 约 600 / `Options.OptionPicture` 约 400 —— 上限留够，再多也没人翻。
RESOURCE_KEY_LIMIT = 400


def resource_key_candidates(
    instance: Instance,
    table: str,
    column: str,
    *,
    needle: str = "",
    limit: int = RESOURCE_KEY_LIMIT,
) -> tuple[str, ...]:
    """**资源键**那一列在这张表里用过的值，按「像不像 `needle`」排好序；最多 `limit` 条。

    适用面 = 词典判成 `CONTROL_RESOURCE` 的那些列（列名以 FileName / Icon / IconKey /
    Picture / AudioPreset / PresetName 结尾，见 `core/edit/dictionary.py`）——
    缩略图、图标、立绘、音效预设一视同仁，它们**都是又长又难记的资源键**。

    用户口径（2026-10-01）：「缩略图我也要能直接输入然后搜索，而不是我去找然后复制名字，
    那个名字太长了，很难记」—— `US_STRYKER_RV-Label` 这种不该由人背下来。

    候选只取**同一张表同一列已经用过的值**，三条理由：

    1. 它们天然是「游戏认的合法键」（本来就写在这一列里，不是我们编的）；
    2. 不用装游戏 —— 走 catalog 那条路要游戏目录，而且几族的图混在一起会给出无关的图
       （单位的缩略图与弹药的图标是两批素材）；
    3. 这正是用户今天手工做的事：翻一行用着它的，把名字复制过来。

    排序三档与检索页同源（完全相等 > 以它开头 > 只是含它），比较走 `match_text`
    —— 西里尔 / 拉丁同形字也认（那条真 bug 的教训见 `fold_confusables`）。
    """
    if table not in instance.table_names:
        return ()
    target = instance.table(table)
    try:
        index = target.column_index(column)
    except KeyError:
        return ()  # 这一版数据里没这一列（映射不该因此炸）
    uniques: dict[str, None] = {}
    for row in target.rows:
        value = row[index]
        if isinstance(value, str) and value.strip():
            uniques.setdefault(value, None)
    if not uniques:
        return ()
    if not needle.strip():
        return tuple(sorted(uniques)[:limit])
    want = match_text(needle)
    ranked: list[tuple[int, str]] = []
    for value in uniques:
        folded = match_text(value)
        if folded == want:
            ranked.append((_RANK_EXACT, value))
        elif folded.startswith(want):
            ranked.append((_RANK_PREFIX, value))
        elif want in folded:
            ranked.append((_RANK_PART, value))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(value for _rank, value in ranked[:limit])


__all__ = [
    "FALLBACK_NAME_COLUMNS",
    "LABEL_MAX_FOREIGN",
    "LOOKUP_ART_COLUMNS",
    "LOOKUP_LIMIT",
    "LOOKUP_NAME_COLUMNS",
    "NAME_COLUMN",
    "SEARCH_LIMIT",
    "LookupHit",
    "LookupResult",
    "NameHit",
    "display_name",
    "fold_confusables",
    "match_text",
    "find_row",
    "is_readable_name",
    "lookup_columns",
    "lookup_rows",
    "name_column",
    "name_columns",
    "reference_summary",
    "row_label",
    "search_rows",
]
