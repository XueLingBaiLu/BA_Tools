"""变更集：界面里**所有**编辑的唯一入口（零 Qt）。

## 为什么要有它

在此之前，编辑是"直接改内存里的行"——`TableModel.setData` 把值写进 `Table.rows`，
脏标记是 `(行号, 列)` 的集合。这个模型有三个后果，而设计稿 A 右栏的三块
**一块都用不上它**：

1. **没有旧值。** `table_model.revert_all` 的注释写得很直白："不保留原值副本"。
   于是「改前 55」「55 → 60」这类文案无从生成，撤销只能整表重读。
2. **按行号寻址。** 「克隆新增」要往表里插行，行号会整体位移；插一次之后，
   之前记下的 `(行号, 列)` 全部错位。
3. **只管一张表。** 「这次改动会写到哪」要跨表汇总，行号制的脏标记跨不了表。

## 两个决定（写进代码，不靠自觉）

- **按业务键寻址，不按行号。** 键就是该表的 `Id` 列。
  实测依据：`_relations.json` 的 `inventory` 里 **24/24 张表 `id_unique=true`**，
  所以这个键在每一张表上都能用（数据表本身没有第二候选键）。
- **旧值在第一次改动时记下，之后不再覆盖。** `55 → 60 → 70` 的「改前」是
  `55` 而不是 `60`——否则用户看到的"改了什么"会随他多改几次而漂移。
  改回原值时该条目**整条消失**："没有改动"就该是没有改动，
  而不是"改了两次互相抵消"。

## 撤销与重做

- **撤销**：`revert(change)` / `revert_row(row_change)` / `revert_all()` —— 把旧值写回，
  条目从待保存清单里移除；
- **重做**：`redo()` 把**最近一次撤销**放回来（`can_redo` 给界面决定快捷键亮不亮）。
  栈是**后进先出**，上限 `HISTORY_LIMIT = 100`；**任何新的写操作都会清空重做栈** ——
  历史一分叉，重做就会把用户刚做的那笔改动顶掉，那比没有重做更糟。

⚠️ 重做栈只认**经 `revert*()` 撤下来**的条目。界面上「拿公开写操作把值改回去」
（`app/screens/editor/pages/base.py::EditorContext.undo_last` 就是那么做的：
它 `set_value(old)` 而不是 `revert`）**不经过这里** —— 那种撤销不会进这个栈，
它那条 Ctrl+Y 得自己记一份（否则重做栈恒空）。

## 刻意不做

- **不碰磁盘。** 落盘仍走 `core.session`；本模块只动内存。
- **不记行号。** 行级撤销把行放回**末尾**（行序是显示顺序，不是数据）——
  要严格复原行号得连它一起记，那是编辑器历史的活。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ba_mod_maker.core.data.dataset import Instance, Table
from ba_mod_maker.core.errors import TableError

#: 业务键 = 该表 `Id` 列的值。24 张表全部唯一（见模块 docstring）。
RowId = int

#: 业务键所在列名。24 张表一致。
ID_COLUMN = "Id"

#: 重做栈的上限（用户口径：Ctrl+Z / Ctrl+Y，栈上限 100）。
#: 超了丢**最旧**那条：无限长的历史既占内存，也没人会按 100 次 Ctrl+Y。
HISTORY_LIMIT = 100


@dataclass(frozen=True, slots=True)
class Change:
    """一处**列级**改动。`old` 是**首次**改动前的值。"""

    table: str
    row_id: RowId
    column: str
    old: Any
    new: Any

    @property
    def is_noop(self) -> bool:
        return self.old == self.new


@dataclass(frozen=True, slots=True)
class RowChange:
    """一处**整行级**改动：新增 / 删除。

        为什么不塞进 `Change`：列级改动的语义是「55 → 60」，行级改动是「多了一行 /
        少了一行」—— 后者没有“旧值”这一说。混成一个类型，界面就分不清
    “把一个空单元格填上”和“插了一行”了。
    """

    table: str
    row_id: RowId
    kind: str  # "insert" | "delete"
    #: 整行的值（插入 = 新值；删除 = 被删掉的旧值），按列名。
    values: tuple[tuple[str, Any], ...]

    @property
    def is_insert(self) -> bool:
        return self.kind == "insert"

    def value_of(self, column: str) -> Any:
        for name, value in self.values:
            if name == column:
                return value
        return None

    @property
    def summary(self) -> str:
        """界面用的一句话：新增 Sensors 行 #37 / 删除 SquadMembers 行 #1470。"""
        verb = "新增" if self.is_insert else "删除"
        return f"{verb} {self.table} 行 #{self.row_id}"


def _unset_defaults(table: Table) -> dict[str, Any]:
    """新增行时"没给值"的列该填什么 —— **只看证据，不猜类型**。

    规则只有一条：**这一列在本表里一个 None 都没有**（= 全表同一种值类型）才给零值，
    `int->0` / `float->0.0` / `bool->False` / `str->""`；其余列（本来就能空的）继续给 None。

    为什么必须这么做（2026-11-03 实测事故）：`insert_row` 原来一律填 None，
    于是新增的 `SpecializationAvailabilities#1232` 把 `MaxAvailabilityXp0..3` 写成了 JSON null；
    而游戏侧 `dump.cs:53804` 的 `public int MaxAvailabilityXp0` 是**不可空 int** ⇒
    存档进 `BA_Mod.zip` 后游戏在首个场景加载期间抛托管异常闪退（0xE06D7363）。
    官方库里这四列 338/338 行全是 int、零个 null —— 正是这条规则能认出来的形态。

    ⚠️ 不给默认值的两类，别扩：① 本表有 None 的列（可空是事实，填零值反而改语义）；
    ② list / dict 之类复合值（没有"零值"这回事，让调用方给）。
    """
    if not table.rows:
        return {}
    defaults: dict[str, Any] = {}
    for i, column in enumerate(table.columns):
        values = [row[i] for row in table.rows]
        if any(value is None for value in values):
            continue
        sample = values[0]
        # bool 必须先判：Python 里 bool 是 int 的子类，先判 int 会把 False 变成 0。
        if isinstance(sample, bool):
            defaults[column] = False
        elif isinstance(sample, int):
            defaults[column] = 0
        elif isinstance(sample, float):
            defaults[column] = 0.0
        elif isinstance(sample, str):
            defaults[column] = ""
    return defaults


class Changeset:
    """一次编辑会话里的全部待保存改动。

    用法：

        cs = Changeset(instance)
        cs.set_value("Units", 1, "Cost", 60)   # 直接落到 Table 上，并记下旧值 55
        cs.changes                              # (Change("Units", 1, "Cost", 55, 60),)
        cs.revert(cs.changes[0])                # 写回 55，条目消失
    """

    def __init__(self, instance: Instance) -> None:
        self._instance = instance
        #: 表名 -> {Id: 行下标}。惰性建，插删行后由 `invalidate()` 丢掉。
        self._index: dict[str, dict[RowId, int]] = {}
        #: (表名, Id, 列名) -> Change。**有序**：界面按改动发生的顺序展示。
        self._changes: dict[tuple[str, RowId, str], Change] = {}
        #: (表名, Id) -> RowChange（新增/删除）。与列级改动分开记，顺序也分开。
        self._row_changes: dict[tuple[str, RowId], RowChange] = {}
        #: **撤销过的记录**（最近一次在末尾）。`redo()` 从末尾取 —— 后进先出。
        #: 任何新的写操作都会清空它（见模块注释「撤销与重做」）。
        self._redo: list[Change | RowChange] = []
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #

    @property
    def instance(self) -> Instance:
        return self._instance

    def table(self, name: str) -> Table:
        return self._instance.table(name)

    def row_of(self, table_name: str, row_id: RowId) -> int:
        """业务键 -> 行下标。找不到抛 `TableError`（带 hint）。"""
        index = self._index.get(table_name)
        if index is None:
            index = self._build_index(table_name)
        row = index.get(row_id)
        if row is None:
            raise TableError(
                f"{table_name} 里没有 {ID_COLUMN}={row_id} 的行",
                hint=(
                    "键是各表的 Id 列。行可能刚被删掉，或数据源刚被换过"
                    "——换数据源时要新建变更集，不要复用旧的"
                ),
                table=table_name,
            )
        return row

    def value(self, table_name: str, row_id: RowId, column: str) -> Any:
        """该单元格**当前**的值（含未保存的改动）。"""
        table = self.table(table_name)
        return table.cell(self.row_of(table_name, row_id), _column_index(table, column))

    def old_value(self, table_name: str, row_id: RowId, column: str) -> Any:
        """首次改动前的值；没改过则抛 `KeyError`。"""
        return self._changes[(table_name, row_id, column)].old

    def change_for(self, table_name: str, row_id: RowId, column: str) -> Change | None:
        return self._changes.get((table_name, row_id, column))

    def is_dirty(self, table_name: str, row_id: RowId, column: str) -> bool:
        return (table_name, row_id, column) in self._changes

    @property
    def changes(self) -> tuple[Change, ...]:
        """全部**列级**改动，按发生顺序。"""
        return tuple(self._changes.values())

    @property
    def row_changes(self) -> tuple[RowChange, ...]:
        """全部**行级**改动（新增/删除），按发生顺序。"""
        return tuple(self._row_changes.values())

    @property
    def count(self) -> int:
        """改动**条数**（一处单元格 = 一条；插/删一行也算一条）。"""
        return len(self._changes) + len(self._row_changes)

    @property
    def is_empty(self) -> bool:
        return not self._changes and not self._row_changes

    def changed_tables(self) -> tuple[str, ...]:
        """改到过的表名，按首次出现顺序。"""
        seen: dict[str, None] = {}
        for change in self._changes.values():
            seen.setdefault(change.table, None)
        for row_change in self._row_changes.values():
            seen.setdefault(row_change.table, None)
        return tuple(seen)

    def changed_rows(self, table_name: str) -> tuple[RowId, ...]:
        """该表里被改到过的**行**（去重，按首次出现顺序）。

        界面用它在左栏打「已改」角标、在「写到哪」里报"共 N 行"。
        """
        seen: dict[RowId, None] = {}
        for change in self._changes.values():
            if change.table == table_name:
                seen.setdefault(change.row_id, None)
        for row_change in self._row_changes.values():
            if row_change.table == table_name:
                seen.setdefault(row_change.row_id, None)
        return tuple(seen)

    def changes_of(self, table_name: str) -> tuple[Change, ...]:
        return tuple(c for c in self._changes.values() if c.table == table_name)

    def total_rows(self) -> int:
        """跨表去重后的改动**行数**（设计稿的「共 N 行」）。"""
        return sum(len(self.changed_rows(name)) for name in self.changed_tables())

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #

    def set_value(self, table_name: str, row_id: RowId, column: str, new: Any) -> Change | None:
        """把值写进表里并记下改动。

        返回：

        - `Change` —— 真的改了（新值与首次改前不同）；
        - `None` —— 值回到了**首次改前**的样子，该条目已整条移除。

        为什么返回 `None` 而不是"一条 old==new 的改动"：界面要按
        `changes` 渲染「变更清单」，留着空条目就会显示"55 → 55"。
        """
        table = self.table(table_name)
        row = self.row_of(table_name, row_id)
        col = _column_index(table, column)

        existing = self._changes.get((table_name, row_id, column))
        baseline = existing.old if existing is not None else table.rows[row][col]

        # 新的写操作 = 历史分叉：重做栈作废（否则 Ctrl+Y 会把刚改的顶掉）。
        self._redo.clear()
        table.rows[row][col] = new

        if baseline == new:
            # 改回原值 —— 这不是"一处改动"，是不再有改动。
            self._changes.pop((table_name, row_id, column), None)
            self._notify()
            return None

        change = Change(table=table_name, row_id=row_id, column=column, old=baseline, new=new)
        self._changes[(table_name, row_id, column)] = change
        self._notify()
        return change

    def revert(self, change: Change) -> None:
        """撤销一条：把**首次改前**的值写回去，条目移除，并记进重做栈。"""
        table = self.table(change.table)
        row = self.row_of(change.table, change.row_id)
        col = _column_index(table, change.column)
        table.rows[row][col] = change.old
        self._changes.pop((change.table, change.row_id, change.column), None)
        self._push_redo(change)
        self._notify()

    def insert_row(
        self, table_name: str, values: Mapping[str, Any], index: int | None = None
    ) -> RowId:
        """插一行。**Id 必须自己给**（24 张表的业务键就是 Id，没有自增）。

        - 表里已经有同 Id 的行 -> TableError（**不覆盖**：那会把别人的数据吃掉）
        - 没给的列按 `_unset_defaults()` 兜：**全表没有 None 的列**给类型零值
          （int->0 / float->0.0 / bool->False / str->""），其余列还是 None
        - 新行默认追加在末尾；invalidate() 之后按键寻址照常有效

        `index` **可选**（2026-10-12 加，为了 `row-restore` 的逐字节还原）：

        - 不给（`None`）= 追加到末尾，**与从前逐字节相同的行为**；
        - 给了 = 插在该下标处（`0` = 最前，`len(rows)` = 末尾），越界抛 `TableError`。

        ⚠️ 一次插多行时，**必须按 index 升序逐条插** —— 下标量的是"插之前那一版
        数组"，升序插入时前一次插入不会挪动后面要用的那个下标（降序就会挪，
        插出来的行序与原表不一致）。`cli/row_restore.py` 钉着这条。
        """
        table = self.table(table_name)
        row_id = values.get(ID_COLUMN)
        if not isinstance(row_id, int) or isinstance(row_id, bool):
            raise TableError(
                f"{table_name} 插行必须给整数 {ID_COLUMN}",
                hint="键就是各表的 Id 列（24/24 张表一致）",
                table=table_name,
            )
        if row_id in self._build_index(table_name):
            raise TableError(
                f"{table_name} 里已经有 {ID_COLUMN}={row_id} 的行",
                hint="换一个没被占用的 Id（见 core/edit/clone.py 的 next_row_id）",
                table=table_name,
            )
        # ❗ 缺列**不能**一律填 None：目标模型的字段可能写成不可空 int，
        #   JSON null 会让游戏反序列化就抛（见 `_unset_defaults` 的事故记录）。
        defaults = _unset_defaults(table)
        row = []
        for column in table.columns:
            value = values.get(column)
            row.append(defaults.get(column) if value is None else value)
        if index is not None and not 0 <= index <= len(table.rows):
            # 下标只认 0..len(rows)（= 末尾）；越界**报错**，绝不静默退回"追加到末尾" ——
            # 静默追加的症状是"行数对、行序错"，而那正是逐字节还原失败的样子。
            raise TableError(
                f"插入下标 {index} 越界：{table_name} 现有 {len(table.rows)} 行"
                f"（可用 0..{len(table.rows)}）",
                hint="下标量的是**插之前**那一版数组；一次插多行要按 index 升序逐条插",
                table=table_name,
            )
        self._redo.clear()  # 新的写操作 -> 重做栈作废
        if index is None:
            table.rows.append(row)
        else:
            table.rows.insert(index, row)
        recorded = RowChange(
            table=table_name,
            row_id=row_id,
            kind="insert",
            values=tuple(zip(table.columns, row, strict=True)),
        )
        previous = self._row_changes.get((table_name, row_id))
        if previous is not None and not previous.is_insert:
            # 先删后原样加回来 = 什么也没发生（撤销时不该把它整行抹掉）。
            # 值不一样就说明这是另一行数据，不能悄悄顶掉原来的删除记录。
            if previous.values == recorded.values:
                self._row_changes.pop((table_name, row_id), None)
            else:
                raise TableError(
                    f"{table_name}#{row_id} 本次会话里刚被删掉，且新值与旧值不同",
                    hint="要么先用 revert_row 撤销那次删除，要么换一个 Id",
                    table=table_name,
                )
        else:
            self._row_changes[(table_name, row_id)] = recorded
        self.invalidate(table_name)
        self._notify()
        return row_id

    def delete_row(self, table_name: str, row_id: RowId) -> RowChange:
        """删一行（旧值整行留着，撤销时放回去）。**行上的列级改动被它吸收。**"""
        table = self.table(table_name)
        index = self.row_of(table_name, row_id)
        row = list(table.rows[index])
        change = RowChange(
            table=table_name,
            row_id=row_id,
            kind="delete",
            values=tuple(zip(table.columns, row, strict=True)),
        )
        for key in [k for k in self._changes if k[0] == table_name and k[1] == row_id]:
            self._changes.pop(key, None)
        self._redo.clear()  # 新的写操作 -> 重做栈作废
        del table.rows[index]
        previous = self._row_changes.get((table_name, row_id))
        if previous is not None and previous.is_insert:
            # 刚插进来又删掉 = 什么也没发生。留一条「删除」记录的话，
            # 撤销全部会把它**变回来**（实测踩过：5 人改 6 人再改回 5 人，撤销后成了 6 人）。
            self._row_changes.pop((table_name, row_id), None)
        else:
            self._row_changes[(table_name, row_id)] = change
        self.invalidate(table_name)
        self._notify()
        return change

    def revert_row(self, row_change: RowChange) -> None:
        """撤销一处行级改动：插进去的拿掉，删掉的放回**末尾**，并记进重做栈。

        ⚠️ 放回末尾会改变表内行序。行序是「显示顺序」而不是数据，
        但真要严格复原，得连行号一起记 —— 那是编辑器历史的活（见模块注释）。
        """
        self._revert_row_quiet(row_change)
        self._push_redo(row_change)
        self._notify()

    @property
    def can_redo(self) -> bool:
        """有没有可以重做的撤销（界面据此决定 Ctrl+Y 亮不亮）。"""
        return bool(self._redo)

    def redo(self) -> Change | RowChange | None:
        """重做**最近一次**撤销（Ctrl+Z 的逆操作）。没有可重做的返回 `None`。

        - 列级：把 `new` 写回表，并把这条改动放回待保存清单（于是它又「脏」了）；
        - 行级：插入的重新插回，删除的重新删掉（记录放回清单）。

        重做本身**不动**重做栈（还能接着往上重做）；但**任何新的写操作**会清空它。
        """
        if not self._redo:
            return None
        record = self._redo.pop()
        if isinstance(record, RowChange):
            self._apply_row_change(record)
        else:
            table = self.table(record.table)
            row = self.row_of(record.table, record.row_id)
            col = _column_index(table, record.column)
            table.rows[row][col] = record.new
            self._changes[(record.table, record.row_id, record.column)] = record
        self._notify()
        return record

    def commit(self) -> None:
        """把当前这批改动**认下来**：条目清空、旧值不再保留。

        为什么必须有它（单位编辑器本轮加的）：改动是**直接写进 Table.rows** 的，
        所以"保存之后"内存里已经是新值了；但不 commit 的话条目还在，于是
        - 再点一次保存会写第二遍（还会说"有 N 张表改动"）—— 那句话在说假话；
        - 退出编辑器时若按"未保存就还原"处理，会把用户**刚保存成功**的改动也撤回去，
          内存与磁盘就此不一致。

        与 revert_all 的区别：那个是"不要了"，这个是"要了"。

        ❗ 重做栈也一起清掉：commit 之后"撤销回 commit 之前"这件事已经不成立了
        （清单清了、基数是新值），留着那些记录只会重做出一批**不该再有**的改动。
        """
        self._changes.clear()
        self._row_changes.clear()
        self._redo.clear()
        self._notify()

    def revert_all(self) -> None:
        """撤销全部改动（列级 + 行级）。

        ⚠️ 与 `TableModel.revert_all`（从磁盘重读整表）不同：这里是逐条写回旧值，
        所以**不会**丢掉磁盘上本来就不一致的地方，也不会触发重读。

        顺序：先还原列，再撤行 —— 否则「往一行写旧值」可能落在一个已经被拿掉的行上。
        """
        for change in tuple(self._changes.values()):
            table = self.table(change.table)
            row = self.row_of(change.table, change.row_id)
            col = _column_index(table, change.column)
            table.rows[row][col] = change.old
        # 每一条都进重做栈，顺序与「撤销」一致 —— 于是 Ctrl+Y 能一条条放回来
        # （栈是后进先出：最后撤的那一条最先重做）。
        for change in tuple(self._changes.values()):
            self._push_redo(change)
        self._changes.clear()
        for row_change in tuple(self._row_changes.values()):
            self._revert_row_quiet(row_change)
            self._push_redo(row_change)
        self._row_changes.clear()
        self._notify()

    def _revert_row_quiet(self, row_change: RowChange) -> None:
        table = self.table(row_change.table)
        if row_change.is_insert:
            index = self.row_of(row_change.table, row_change.row_id)
            del table.rows[index]
        else:
            table.rows.append([row_change.value_of(column) for column in table.columns])
        self._row_changes.pop((row_change.table, row_change.row_id), None)
        self.invalidate(row_change.table)

    def _apply_row_change(self, row_change: RowChange) -> None:
        """把一条行级改动**再做一遍** —— `redo()` 用，与 `_revert_row_quiet` 互为逆操作。"""
        table = self.table(row_change.table)
        if row_change.is_insert:
            # 行已经在了（不该发生）就不重复插：Id 是业务键，插出两行同 Id 是数据损坏。
            if not self._has_row(row_change.table, row_change.row_id):
                table.rows.append([row_change.value_of(column) for column in table.columns])
        else:
            del table.rows[self.row_of(row_change.table, row_change.row_id)]
        self._row_changes[(row_change.table, row_change.row_id)] = row_change
        self.invalidate(row_change.table)

    def _has_row(self, table_name: str, row_id: RowId) -> bool:
        """这一行在不在（`row_of` 找不到会抛 TableError，这里只想要是/否）。"""
        try:
            self.row_of(table_name, row_id)
        except TableError:
            return False
        return True

    def _push_redo(self, record: Change | RowChange) -> None:
        """把一条**刚被撤销**的记录放进重做栈；超上限丢最旧的。"""
        self._redo.append(record)
        if len(self._redo) > HISTORY_LIMIT:
            del self._redo[: len(self._redo) - HISTORY_LIMIT]

    # ------------------------------------------------------------------ #
    # 索引与订阅
    # ------------------------------------------------------------------ #

    def invalidate(self, table_name: str | None = None) -> None:
        """丢掉行索引缓存。**插行/删行之后必须调用。**

        改动本身按 `Id` 记，位移不影响它；会失效的只有"Id -> 行下标"这份缓存。
        """
        if table_name is None:
            self._index.clear()
        else:
            self._index.pop(table_name, None)

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """登记一个"改动集变了"的回调。返回值是退订函数。"""
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _notify(self) -> None:
        for callback in tuple(self._listeners):
            callback()

    def _build_index(self, table_name: str) -> dict[RowId, int]:
        table = self.table(table_name)
        col = _column_index(table, ID_COLUMN)
        index: dict[RowId, int] = {}
        for row_number, row in enumerate(table.rows):
            index[row[col]] = row_number
        self._index[table_name] = index
        return index


def unit_ids_from(changes: Iterable[Change]) -> frozenset[RowId]:
    """从改动集合里挑出 `Units` 表上被改到的 Id。

    左栏要给"改过"的单位打角标，而改动可能落在任何一张表上
    （改一个武器弹药数量，动的是 `WeaponAmmunitions`，不是 `Units`）。
    这个函数只做"Units 表本身的改动"这一件事；跨表反查属于关系图，不在这里。
    """
    return frozenset(c.row_id for c in changes if c.table == "Units")


def _column_index(table: Table, column: str) -> int:
    try:
        return table.column_index(column)
    except KeyError:
        raise TableError(
            f"表 {table.name} 没有列 {column}",
            hint="列名以 _schema.json 为准；表结构变了说明数据源换了版本",
            table=table.name,
        ) from None
