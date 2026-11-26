"""资产变更集：资产侧**所有**改动的唯一入口（零 Qt，纯数据，不碰磁盘）。

与数据库侧的 [Changeset](changeset.py) 并列：那边管 24 张表，这边管包 / catalog / 容器里的
对象与字段。落点用的是 [AssetRef](asset_refs.py)（与 `CellRef` 同形，ADR 0008 决定 5）。

## 与 Changeset 逐条对齐的四条口径

| 口径 | 数据库侧（已存在） | 这里 |
|---|---|---|
| no-op | 改回原值 ⇒ **整条消失**（`changeset.py:254-258`） | 同（`set_field` 返回 None） |
| `old` | **只在第一次**改动时记（`changeset.py:20-23`） | 同 |
| 撤销 | 写回旧值，**不重读磁盘**（`edit_session.py:146-149`） | 同（基线只在首次编辑时问一次） |
| 顺序 | **先列后行**（`edit_session.py:159-162`） | **先字段、后对象**（`AssetPlan` 三格就是它） |

⚠ 资产侧今天是**相反**的口径：`asset_kernel/skin_install.py::swapped_pptr`（:128）把
「新旧相同」当**错误**抛（:139-143）。本条按 ADR 0008 决定 6 **统一到前者**。

## 三个原子动词，⛔ 没有「换」

`set_field` / `add_object` / `delete_object`：一条 [AssetEdit] 只发**一个**指令
（`kind` 是单值）—— 于是「一次动作同时发起删除与新增」在**结构上**写不出来
（ADR 0008 决定 2 的第一道锁）。第二道锁在 `add_object`：同一个 pathID 上
「先删后增、而内容不同」= 以 B 顶替 A ⇒ **当场抛**（判据 J4，能红）。

## 值住在哪

账本是**缓存**（AGENTS §4.5「改一格只进缓存，不碰盘」）：

- 改一格：只记进账本（`value()` 读得到新值），**不写**调用方那份解析结果、更不碰盘；
- 撤销：条目从账本里移除，**不重读磁盘**（也不问基线）—— 这是 `__init__` 里那条契约；
- 落成字节：`plan()` 把账本按动词分好组交出去，由 `asset_kernel` 的
  `ObjectChange` / `NewObject` / `ObjectRemoval` 落成 Δ（设计稿 §7 第 2 步，本模块不做）。

## 不在这一步（照设计稿 §7 的顺序）

- 写盘（两条产物：包 + Δ 计划）与 `EditSession` 的脏态 —— 第 2 步；
- CLI 三动词 `object set/add/delete` —— 第 3 步；
- **地址级新增 / 删除**（catalog 的 `add_address` / `remove_address`：键是地址字符串、
  没有 pathID）—— 形状未定，跟 CLI 的 `address add/delete` 一起做；
- `redo` / `commit`：设计稿 §8 第 2 条**未决**，本模块**不实现**（不发明口径）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from ba_mod_maker.core.edit.asset_refs import AssetRef
from ba_mod_maker.core.errors import AssetError

__all__ = ["MISSING", "AssetChangeset", "AssetEdit", "AssetPlan"]


class _Missing:
    """「基线里**没有**这个东西」的哨兵（与「值是 None」不是一回事）。

    用 `None` 冒充会让「删一个不存在的对象」静默通过、也会让「新增一个已占用的 pathID」
    悄悄顶掉别人的数据。它同时是**假值**，所以 `if base:` 这种写法不会误判。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


#: 基线读不到这一处时**必须**返回它（见 `AssetChangeset.__init__` 的契约）。
MISSING: Final[_Missing] = _Missing()


def _display(value: Any) -> str:
    """人话显示一个值：bytes 只报长度（一屏放不下 500 KB 的载荷）。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"{len(value)} 字节"
    return str(value)


@dataclass(frozen=True, slots=True)
class AssetEdit:
    """一条资产改动。`old` 是**首次**改动前的值（照 `changeset.py:20-23`）。

    - `kind` 是**单值**：一条条目只发一个原子动词的指令；
    - `summary` 是**算出来的**（照 `RowChange.summary`，`changeset.py:105-109`）——
      写成字段，就会有人手写一份、和 `kind` / `ref` 漂开。
    """

    kind: Literal["set", "add", "delete"]
    ref: AssetRef
    old: Any = None
    new: Any = None

    @property
    def key(self) -> tuple[Path, int | None, tuple[object, ...] | None]:
        """账本键（就是 `ref.key()`）。"""
        return self.ref.key()

    @property
    def is_noop(self) -> bool:
        return self.old == self.new

    @property
    def summary(self) -> str:
        """状态栏 / 日志用的一句话：`修改 units.bundle#58076.m_PathID（58076 → 58077）`。"""
        if self.kind == "add":
            return f"新增 {self.ref}（{_display(self.new)}）"
        if self.kind == "delete":
            return f"删除 {self.ref}（{_display(self.old)}）"
        return f"修改 {self.ref}（{_display(self.old)} → {_display(self.new)}）"


@dataclass(frozen=True, slots=True)
class AssetPlan:
    """一次写盘要做的事（**纯数据**：没有字节、没有 IO）。

    三个动词分成三格 —— 于是「**先字段、后对象**」不需要调用方自己再排一遍。
    落成 `asset_kernel` 的 Δ 是下一步的事（`ObjectChange` / `NewObject` / `ObjectRemoval`）。
    """

    sets: tuple[AssetEdit, ...] = ()
    adds: tuple[AssetEdit, ...] = ()
    deletes: tuple[AssetEdit, ...] = ()

    @property
    def count(self) -> int:
        return len(self.sets) + len(self.adds) + len(self.deletes)

    @property
    def is_empty(self) -> bool:
        return not (self.sets or self.adds or self.deletes)

    def __bool__(self) -> bool:
        return not self.is_empty

    def edits(self) -> tuple[AssetEdit, ...]:
        """全部条目，顺序 = **字段 -> 新增 -> 删除**（`revert_all` 用的也是这个序）。"""
        return (*self.sets, *self.adds, *self.deletes)

    def containers(self) -> tuple[Path, ...]:
        """改动落在哪几个文件上（去重，按首次出现顺序）—— 写盘是一包一趟。"""
        seen: dict[Path, None] = {}
        for edit in self.edits():
            seen.setdefault(edit.ref.container, None)
        return tuple(seen)

    def for_container(self, container: Path) -> AssetPlan:
        """只留落在这**一个**文件上的条目。"""
        return AssetPlan(
            sets=tuple(e for e in self.sets if e.ref.container == container),
            adds=tuple(e for e in self.adds if e.ref.container == container),
            deletes=tuple(e for e in self.deletes if e.ref.container == container),
        )


class AssetChangeset:
    """一次编辑会话里的全部**资产**待保存改动。

        cs = AssetChangeset(baseline)          # baseline: AssetRef -> 现在的值 / MISSING
        cs.set_field(ref, 58077)               # 记下旧值 58076，值只进缓存
        cs.value(ref)                          # 58077
        cs.revert(cs.edits()[0])               # 条目消失，**不再问 baseline**

    `baseline` 的**契约**（三条，都是可断言的）：

    1. 返回「这一处现在是什么」；**没有**这一处时必须返回 [MISSING]；
    2. 只在**首次编辑某一处**时被问一次；再改同一处、撤销、`revert_all` 一律**不再问** ——
       这就是「撤销不重读磁盘」（`edit_session.py:146-149` 的理由）；
    3. 它**只读**：改一格不写调用方那份数据（写盘是 `plan()` 之后的事）。
    """

    def __init__(self, baseline: Callable[[AssetRef], Any]) -> None:
        if not callable(baseline):
            raise TypeError(
                "baseline 必须可调用（AssetRef -> 现在的值 / MISSING）："
                "它就是「这一处现在是什么」的唯一来源"
            )
        self._baseline = baseline
        #: 账本键 -> AssetEdit。**有序**：界面按改动发生的顺序展示。
        self._edits: dict[tuple[Path, int | None, tuple[object, ...] | None], AssetEdit] = {}

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #

    @property
    def baseline(self) -> Callable[[AssetRef], Any]:
        return self._baseline

    def edits(self) -> tuple[AssetEdit, ...]:
        """全部条目，按发生顺序。"""
        return tuple(self._edits.values())

    def count(self) -> int:
        """改动**条数**（一处字段 / 一个对象 = 一条）。"""
        return len(self._edits)

    def is_empty(self) -> bool:
        return not self._edits

    def edit_for(self, ref: AssetRef) -> AssetEdit | None:
        return self._edits.get(ref.key())

    def is_dirty(self, ref: AssetRef) -> bool:
        return ref.key() in self._edits

    def old_value(self, ref: AssetRef) -> Any:
        """**首次**改动前的值；没改过抛 `KeyError`（与 `Changeset.old_value` 同口径）。"""
        return self._edits[ref.key()].old

    def value(self, ref: AssetRef) -> Any:
        """这一处**当前**的值（含未保存的改动）。

        - 没改过 -> 问基线（这是基线被问的**唯一**时机）；
        - 有 `set` / `add` -> 那条的 `new`；
        - 有 `delete` -> [MISSING]（这个对象在本会话里已经被拿掉）。
        """
        edit = self._edits.get(ref.key())
        if edit is not None:
            return MISSING if edit.kind == "delete" else edit.new
        # 字段级的落点与**它所在对象**的落点是两个键（键的第三格不同）—— 所以要查两次：
        # 对象整个被拿掉时，它身上的字段自然也读不到。
        object_edit = self._edits.get((ref.container, ref.path_id, None))
        if object_edit is not None and object_edit.kind == "delete":
            return MISSING
        return self._baseline(ref)

    # ------------------------------------------------------------------ #
    # 写：三个原子动词
    # ------------------------------------------------------------------ #

    def set_field(self, ref: AssetRef, new: Any) -> AssetEdit | None:
        """**修改**一个既有对象的字段（原子动词 `set`）。

        返回 `AssetEdit`（真的改了）/ `None`（值回到了**首次改前**的样子 —— 该条目
        整条移除，照 `changeset.py:254-258`；界面因此不会显示一条「58076 → 58076」）。
        """
        self._require_field(ref, verb="set")
        key = ref.key()
        existing = self._edits.get(key)
        # 字段级的落点与**它所在对象**的落点是两个键（键的第三格不同）—— 所以要查两次。
        object_edit = self._edits.get((ref.container, ref.path_id, None))
        if object_edit is not None:
            if object_edit.kind == "delete":
                raise AssetError(
                    f"{ref} 所在的那个对象在本会话里已经被删除",
                    hint="先撤销那条删除，再来改它的字段",
                )
            raise AssetError(
                f"{ref} 所在的那个对象在本会话里是**新增**的（载荷是整份字节）",
                hint="要改它的内容就撤销那条新增、重新给一份载荷；"
                "字段级修改只对容器里已有的对象有效",
            )
        if existing is not None and existing.kind != "set":
            raise AssetError(f"{ref} 上已经有一条 {existing.kind} 改动，不能再按字段改它")
        # 旧值**只在第一次**改动时记（第二条口径）。
        baseline = existing.old if existing is not None else self._baseline(ref)
        if baseline == new:
            # 改回原值 —— 这不是「一处改动」，是不再有改动。
            self._edits.pop(key, None)
            return None
        edit = AssetEdit(kind="set", ref=ref, old=baseline, new=new)
        self._edits[key] = edit
        return edit

    def add_object(self, ref: AssetRef, payload: bytes) -> AssetEdit | None:
        """**新增**一个对象（原子动词 `add`）。`ref.field` 必须是 None、`path_id` 必须给。

        返回 `None` 表示**净零**：这一处刚被删过，而加回来的内容与删掉的一模一样
        （照 `insert_row` 的「先删后原样加回 = 什么也没发生」，`changeset.py:305-316`）。

        ⛔ **第二个原子动词的守卫**：同一个 pathID 上「先删后增、**内容不同**」=
        以 B 顶替 A —— 那正是 ADR 0008 决定 2 禁掉的复合动作，当场抛 `AssetError`
        （判据 J4）。要顶替就**两个动作各自撤销**，要并存就**另取一个没被占用的 pathID**。
        """
        self._require_whole_object(ref, verb="add")
        payload = self._as_bytes(payload)
        key = ref.key()
        existing = self._edits.get(key)
        if existing is not None:
            if existing.kind == "add":
                raise AssetError(
                    f"{ref} 在本会话里已经新增过了",
                    hint="同一次新增不要提交两遍；要改内容就先把那一条撤销掉",
                )
            if existing.kind == "delete":
                if existing.old == payload:
                    self._edits.pop(key, None)  # 先删后原样加回 = 什么也没发生
                    return None
                raise AssetError(
                    f"{ref} 上先删后增、而内容不同 —— 那是「以 B 顶替 A」这一个复合动作，"
                    "本产品只提供删除与新增两个**各自可撤销**的动作",
                    hint="要顶替就把那条删除撤销掉（两步各自可撤）；要并存就另取一个空闲 pathID",
                )
            raise AssetError(f"{ref} 上已经有一条 {existing.kind} 改动，不能再新增对象")
        if self._baseline(ref) is not MISSING:
            raise AssetError(
                f"{ref} 在容器里已经存在（pathID 不许撞）",
                hint="取一个没被占用的 pathID —— asset_kernel/writer.py::NewObject 是同一条守卫",
            )
        edit = AssetEdit(kind="add", ref=ref, old=None, new=payload)
        self._edits[key] = edit
        return edit

    def delete_object(self, ref: AssetRef) -> AssetEdit | None:
        """**删除**一个对象（原子动词 `delete`）。`ref.field` 必须是 None。

        - 返回 `None` 表示**净零**：这一处是本次会话里刚加进来的（照 `delete_row` 的
          「刚插进来又删掉 = 什么也没发生」，`changeset.py:338-342`）；
        - **吸收**它身上的字段改动（同 `delete_row`，`changeset.py:334-335`）：对象没了，
          那些字段改动没有意义 —— 留着它们，写盘时会落到一个不存在的对象上。
        - 被删掉的旧载荷**此刻**记进 `old`（撤销的承诺是回到基线，见模块头）。
        """
        self._require_whole_object(ref, verb="delete")
        key = ref.key()
        existing = self._edits.get(key)
        if existing is not None and existing.kind == "add":
            self._edits.pop(key, None)
            self._drop_fields_of(ref)
            return None
        if existing is not None and existing.kind == "delete":
            return existing  # 同一条删除再来一次：不是两笔改动（也不必再问基线）
        if existing is not None:
            raise AssetError(f"{ref} 上已经有一条 {existing.kind} 改动，不能再删除它")
        old = self._baseline(ref)
        if old is MISSING:
            raise AssetError(
                f"{ref} 在容器里没有这个东西，删不了",
                hint="pathID 可能抄错了；也可能它本来就在另一个包里",
            )
        self._drop_fields_of(ref)
        edit = AssetEdit(kind="delete", ref=ref, old=old, new=None)
        self._edits[key] = edit
        return edit

    # ------------------------------------------------------------------ #
    # 撤销
    # ------------------------------------------------------------------ #

    def revert(self, edit: AssetEdit) -> None:
        """撤销一条：条目从账本里移除，**不重读磁盘**。

        与 `Changeset.revert` 只差「旧值写到哪」：数据库侧直接写回 `Table.rows`，
        资产侧**什么都不写** —— 值就在账本里（`value()` 读它），落成字节是 `plan()` 之后的事。
        ⛔ 所以这里**一次都不许**调 `self._baseline`：那正是 `edit_session.py:146-149`
        反对的那种「重读」。
        """
        self._edits.pop(edit.key, None)

    def commit(self) -> None:
        """把当前这批改动**认下来**：条目清空。

        与 `Changeset.commit` 是同一条口径（`changeset.py:386`）：save() 成功写出第二条产物
        （Δ 计划）之后必须认账，否则「没有新改动再点一次保存」会把同一份计划重写一遍 ——
        AGENTS §4.5 明确禁止那件事。

        ⚠ 设计稿 §8 第 2 条把 `redo` / `commit` 标着「未定、本模块不实现」。
        这里是 T2（`EditSession.save()` 第二条产物）把它**顶出来**的：不认账就没法满足
        「没有新改动不写盘」。`redo` 仍然不做（资产侧的价值区不在重做）。
        """
        self._edits.clear()

    def revert_all(self) -> None:
        """撤销全部 —— **先字段、后对象**（照 `edit_session.py:159-162` 的「先列后行」）。

        为什么顺序有讲究：字段那一笔的落点在对象上。先把对象的删除撤了再撤字段，
        中间会有一段时间是「往一个刚被拿掉的对象上写旧值」。账本这一层没有副作用，
        但写盘那一层是照这个顺序做的，所以序在这里就先立住（`AssetPlan` 也是三格分开）。
        """
        for edit in tuple(self._edits.values()):
            if edit.kind == "set":
                self._edits.pop(edit.key, None)
        for edit in tuple(self._edits.values()):
            self._edits.pop(edit.key, None)

    # ------------------------------------------------------------------ #
    # 交给写盘
    # ------------------------------------------------------------------ #

    def plan(self) -> AssetPlan:
        """把账本按**动词**分好组交出去（**纯数据**：没有字节、没有 IO）。

        落成 `asset_kernel` 的 Δ（`ObjectChange` / `NewObject` / `ObjectRemoval`）
        是下一步的事（设计稿 §7 第 2 步）；这一步只保证「哪些改动、落在哪个文件」是确定的。
        """
        edits = tuple(self._edits.values())
        return AssetPlan(
            sets=tuple(e for e in edits if e.kind == "set"),
            adds=tuple(e for e in edits if e.kind == "add"),
            deletes=tuple(e for e in edits if e.kind == "delete"),
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _drop_fields_of(self, ref: AssetRef) -> None:
        """丢掉落在**同一个对象**上的字段改动（照 `delete_row` 的吸收口径）。"""
        doomed = [
            key
            for key, edit in self._edits.items()
            if edit.kind == "set"
            and edit.ref.container == ref.container
            and edit.ref.path_id == ref.path_id
        ]
        for key in doomed:
            self._edits.pop(key, None)

    def _require_field(self, ref: AssetRef, *, verb: str) -> None:
        if ref.field is None:
            raise AssetError(
                f"{ref} 没有字段落点：`{verb}` 改的就是字段",
                hint="整对象的新增 / 删除走 add_object / delete_object；"
                "地址级字段请给出 FieldPath（例 m_InternalIds[9001]）",
            )

    def _require_whole_object(self, ref: AssetRef, *, verb: str) -> None:
        if ref.field is not None:
            raise AssetError(
                f"{ref} 带字段落点：`{verb}` 动的是**整个对象**",
                hint="改字段走 set_field；整个对象的新增 / 删除才用这一条",
            )
        if ref.path_id is None:
            raise AssetError(
                f"{ref} 没有 pathID：`{verb}` 说的是包里的某个对象",
                hint="地址级（catalog 条目）的新增 / 删除还没有形状，见本模块头部「不在这一步」",
            )

    @staticmethod
    def _as_bytes(payload: Any) -> bytes:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"payload 必须是字节，拿到 {type(payload).__name__}"
                "：对象的载荷是序列化后的字节，不是值"
            )
        return bytes(payload)
