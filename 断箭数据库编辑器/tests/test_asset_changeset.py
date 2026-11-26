"""资产变更集（`core/edit/asset_changeset.py`）与资产引用（`asset_refs.py`）的测试。

全部内存构造，不碰游戏文件、不碰 Qt。

这里钉住的是**三条口径**（设计稿 §3「必须统一的四条口径」的前三条）＋ ADR 0008 决定 2 的
机械判据。⛔ 每条都配一条**能红**的负控 —— 尺子不能红等于没有（AGENTS §0.9 动作 2）：

1. **no-op**：改回原值 ⇒ 条目**整条消失**（不是留一条「58076 → 58076」）；
2. **`old` 只在第一次**改动时记（`550 → 580 → 700` 的「改前」恒为 550）；
3. **撤销不重读磁盘**：基线只在**首次编辑某一处**时被问一次 —— 用调用计数器量它，
   `revert()` / `revert_all()` 之后计数**一个都不许涨**；
4. ⛔ 对象层「先删后增、同一个 pathID、而内容不同」= 以 B 顶替 A ⇒ **当场抛**（判据 J4）。

⚠ 第 1 条与资产侧今天的口径是**相反**的（`asset_kernel/skin_install.py:139-143` 把
「新旧相同」当错误抛）—— ADR 0008 决定 6 统一到**本文件钉的这条**上。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from ba_mod_maker.core.edit.asset_changeset import (
    MISSING,
    AssetChangeset,
    AssetEdit,
    AssetPlan,
)
from ba_mod_maker.core.edit.asset_refs import AssetRef, ByteSpan, FieldPath
from ba_mod_maker.core.errors import AssetError

#: 用**短名**的容器：断言里要逐字比对，全名（`routebsoldier_assets_all_5847a22c37556b4a`）
#: 会把每一行撑到 100 列以上，反而看不清断言的是什么。
BUNDLE = Path("aa/PC/units.bundle")
OTHER = Path("aa/PC/skins.bundle")

#: 真件里那个材质槽的落点（真机跑过的那 8 字节的同一条链，见 asset_kernel/skin_install.py）。
SLOT = FieldPath("m_TexEnvs[Layer_A97CDC25].second.m_PathID")


def _field_ref(path_id: int = 58076, *, container: Path = BUNDLE) -> AssetRef:
    """字段级落点（`set` 用）。"""
    return AssetRef(container=container, path_id=path_id, field=SLOT)


def _object_ref(path_id: int = 58076, *, container: Path = BUNDLE) -> AssetRef:
    """整对象级落点（`add` / `delete` 用）。"""
    return AssetRef(container=container, path_id=path_id)


class _Baseline:
    """假基线：一份内存字典 ＋ **调用计数器**（第 3 条口径的尺子就是那个计数器）。

    真实实现由写盘那一步接（解析出来的对象 / 打开的那个包）；这里只要求它满足
    `AssetChangeset.__init__` 里写死的那三条契约。
    """

    def __init__(self, values: Mapping[tuple, Any] | None = None) -> None:
        self.values: dict[tuple, Any] = dict(values or {})
        self.calls: list[AssetRef] = []

    def seed(self, ref: AssetRef, value: Any) -> _Baseline:
        """登记「这一处现在是什么」（链式，方便一行造一个场景）。"""
        self.values[ref.key()] = value
        return self

    def __call__(self, ref: AssetRef) -> Any:
        self.calls.append(ref)
        return self.values.get(ref.key(), MISSING)

    @property
    def reads(self) -> int:
        """基线被问了几次。"""
        return len(self.calls)


class _KeepsNoopEntry(AssetChangeset):
    """**故意违规**的账本：改回原值也把条目留着（留一条「58076 → 58076」）。

    它不是产品代码，是尺子的**正控**：喂一个已知违规的实现进去，`count() == 0` 这条
    断言必须红 —— 否则那条断言只是「这一次恰好没留下」，证明不了尺子量得到。
    """

    def set_field(self, ref: AssetRef, new: Any) -> AssetEdit | None:
        edit = super().set_field(ref, new)
        if edit is None:
            self._edits[ref.key()] = AssetEdit(kind="set", ref=ref, old=new, new=new)
        return edit


class _RereadingChangeset(AssetChangeset):
    """**故意违规**的账本：撤销时重读一遍基线（`edit_session.py:146-149` 反对的那一下）。

    同上，它是**读计数器那把尺子**的正控。
    """

    def revert(self, edit: AssetEdit) -> None:
        self.baseline(edit.ref)  # ← 就是被禁的那一下
        super().revert(edit)


class _DriftingOldValue(AssetChangeset):
    """**故意违规**的账本：`old` 记成「上一次改完的值」（改前会随用户多改几次漂走）。

    同上，它是**「改前恒为第一次那个值」那把尺子**的正控：喂它进去，
    `old_value() == 58076` 那条断言必须红 —— 否则那条断言只是「这一次恰好没漂」。
    """

    def set_field(self, ref: AssetRef, new: Any) -> AssetEdit | None:
        previous = self._edits.get(ref.key())
        drifting = previous.new if previous is not None else None
        edit = super().set_field(ref, new)
        if edit is not None and drifting is not None:
            # ← 就是被禁的那一下：把已经记下的 old 改写成「上一次的新值」
            self._edits[ref.key()] = AssetEdit(kind="set", ref=ref, old=drifting, new=edit.new)
        return edit


# --------------------------------------------------------------------------- #
# 1. 旧值语义（口径 2）
# --------------------------------------------------------------------------- #


def test_set_field_records_the_old_value_and_the_new_one() -> None:
    ref = _field_ref()
    base = _Baseline().seed(ref, 58076)
    cs = AssetChangeset(base)

    edit = cs.set_field(ref, 58077)

    assert edit is not None
    assert (edit.kind, edit.old, edit.new) == ("set", 58076, 58077)
    assert edit.ref == ref
    # 值真的落到了账本上（`value()` 读得到），不是只记了个数字。
    assert cs.value(ref) == 58077
    # 而调用方那份数据**一个字节都没动**：改一格只进缓存（AGENTS §4.5）。
    assert base.values[ref.key()] == 58076
    assert cs.count() == 1 and not cs.is_empty()


def test_the_second_edit_keeps_the_first_old_value() -> None:
    """`58076 → 58077 → 58078` 的「改前」必须是 58076，不能漂成 58077。

    负控：把 `old` 记成「上一次的新值」时，这条会红 —— 而界面上「改前」会随用户多改几次
    而变化，用户看到的就不是「相对于最初改了多少」。
    """
    ref = _field_ref()
    cs = AssetChangeset(_Baseline().seed(ref, 58076))

    cs.set_field(ref, 58077)
    cs.set_field(ref, 58078)

    assert cs.count() == 1, "同一处的多次改动仍然只有一条"
    assert cs.old_value(ref) == 58076, f"「改前」漂成了 {cs.old_value(ref)}"
    assert cs.value(ref) == 58078


# --------------------------------------------------------------------------- #
# 2. no-op：改回原值 ⇒ 整条消失（口径 1）
# --------------------------------------------------------------------------- #


def test_setting_the_value_back_to_the_baseline_removes_the_entry() -> None:
    """`58077 → 58076`（= 基线值）⇒ 这一条**整条消失**，不是留一条「58076 → 58076」。

    负控：条目若留着，`count()` 会是 1、界面上会显示一条「改前改后一样」的空条目。
    """
    ref = _field_ref()
    cs = AssetChangeset(_Baseline().seed(ref, 58076))
    cs.set_field(ref, 58077)
    assert cs.count() == 1

    assert cs.set_field(ref, 58076) is None
    assert cs.count() == 0, "改回原值之后账本里还剩着条目"
    assert cs.is_empty()
    assert not cs.is_dirty(ref)
    assert cs.edit_for(ref) is None
    # 值回到基线那份（账本里不再有副本）。
    assert cs.value(ref) == 58076


# --------------------------------------------------------------------------- #
# 3. 撤销不重读磁盘（口径 3）—— 尺子是基线自己的调用计数
# --------------------------------------------------------------------------- #


def test_the_baseline_is_asked_once_per_place() -> None:
    ref = _field_ref()
    base = _Baseline().seed(ref, 58076)
    cs = AssetChangeset(base)

    cs.set_field(ref, 58077)
    assert base.reads == 1
    cs.set_field(ref, 58078)
    assert base.reads == 1, "同一处的第二次改动不该再问基线（旧值只在第一次记）"
    cs.set_field(ref, 58076)  # 改回原值
    assert base.reads == 1
    assert cs.is_empty()


def test_revert_does_not_ask_the_baseline_again() -> None:
    """撤销的旧值来自**账本**，不是重新去问一遍。

    负控：`revert` 里若调了一次 `self._baseline`，下面那行 `base.reads` 会变成 2 ⇒ 红。
    （`app/shell/edit_session.py:146-149` 反对的就是这种「重读」：磁盘上那份会盖掉内存。）
    """
    ref = _field_ref()
    base = _Baseline().seed(ref, 58076)
    cs = AssetChangeset(base)
    edit = cs.set_field(ref, 58077)
    assert edit is not None and base.reads == 1

    cs.revert(edit)

    assert cs.is_empty()
    assert base.reads == 1, f"revert 之后基线被问了 {base.reads} 次 —— 撤销不许重读"
    # 值回落到**基线**那一份（不是账本里存的副本）。
    assert cs.value(ref) == 58076


def test_revert_all_does_not_ask_the_baseline_again() -> None:
    first, second = _field_ref(58076), _field_ref(9001, container=OTHER)
    base = _Baseline().seed(first, 58076).seed(second, 5)
    cs = AssetChangeset(base)
    cs.set_field(first, 58077)
    cs.set_field(second, 6)
    assert base.reads == 2, "两处 = 问两次（每处第一次改动时各一次）"

    cs.revert_all()

    assert cs.is_empty()
    assert base.reads == 2, f"revert_all 之后基线被问了 {base.reads} 次 —— 撤销不许重读"


def test_the_entry_count_would_catch_a_kept_noop_entry() -> None:
    """尺子的**正控**：喂一个「改回原值仍留着条目」的实现，`count() == 0` 必须红。"""
    ref = _field_ref()
    cs = _KeepsNoopEntry(_Baseline().seed(ref, 58076))
    cs.set_field(ref, 58077)
    cs.set_field(ref, 58076)

    assert cs.count() == 1, "尺子看不见 no-op 条目 —— 上面那条 count() == 0 就不可信"
    assert cs.edits()[0].is_noop


def test_the_read_counter_would_catch_a_rereading_revert() -> None:
    """尺子的**正控**：喂一个「撤销时重读基线」的实现，计数器必须看见那一下。"""
    ref = _field_ref()
    base = _Baseline().seed(ref, 58076)
    cs = _RereadingChangeset(base)
    edit = cs.set_field(ref, 58077)
    assert edit is not None and base.reads == 1

    cs.revert(edit)

    assert base.reads == 2, "尺子看不见「撤销时重读」—— 那上面那条 reads == 1 就不可信"


def test_the_old_value_ruler_would_catch_a_drifting_old() -> None:
    """尺子的**正控**：喂一个「`old` 记成上一次改完的值」的实现，`old_value() == 58076` 必须红。

    判据一行：`cs.old_value(ref)` → **58077**（漂了）而不是 58076 —— 于是上面
    `test_the_second_edit_keeps_the_first_old_value` 那条断言量得到东西。
    """
    ref = _field_ref()
    cs = _DriftingOldValue(_Baseline().seed(ref, 58076))

    cs.set_field(ref, 58077)
    cs.set_field(ref, 58078)

    assert cs.count() == 1, "正控也要保住「同一处仍然只有一条」这一格，否则它红的不是 old"
    assert cs.old_value(ref) == 58077, (
        "尺子看不见「改前漂了」—— 那上面那条 old_value() == 58076 就不可信"
    )
    assert cs.value(ref) == 58078


# --------------------------------------------------------------------------- #
# 4. 新增 / 删除：净零与「以 B 顶替 A」（ADR 0008 决定 2 的机械判据）
# --------------------------------------------------------------------------- #


def test_add_then_delete_of_the_same_new_object_is_a_no_op() -> None:
    """刚加进来又删掉 = 什么也没发生（照 `changeset.py:338-342`）。"""
    ref = _object_ref(9001)
    cs = AssetChangeset(_Baseline())

    assert cs.add_object(ref, b"AAA") is not None
    assert cs.count() == 1

    assert cs.delete_object(ref) is None
    assert cs.count() == 0, "刚新增又删除，账本里不该留下任何一条"
    assert cs.value(ref) is MISSING


def test_delete_then_adding_the_same_payload_back_is_a_no_op() -> None:
    """先删后**原样**加回 = 什么也没发生（照 `changeset.py:305-316`）。"""
    ref = _object_ref()
    cs = AssetChangeset(_Baseline().seed(ref, b"AAA"))
    assert cs.delete_object(ref) is not None
    assert cs.count() == 1

    assert cs.add_object(ref, b"AAA") is None
    assert cs.count() == 0, "原样加回之后账本里还剩着条目"


def test_delete_then_adding_different_bytes_is_one_composite_action_and_is_refused() -> None:
    """⛔ **同一个 pathID 上「先删后增、内容不同」= 以 B 顶替 A** —— 判据 J4。

    正控是上一条（内容一样 ⇒ 净零，不抛）。这一条是把"两个值"换成"两个不同的值"，
    尺子**必须红** —— 红了才证明它挡的不是「先删后增」这个顺序，而是那个复合动作本身。
    """
    ref = _object_ref()
    cs = AssetChangeset(_Baseline().seed(ref, b"AAA"))
    cs.delete_object(ref)
    before = cs.edits()

    with pytest.raises(AssetError) as excinfo:
        cs.add_object(ref, b"BBB")

    assert "顶替" in str(excinfo.value), f"报错得说清是哪条口径：{excinfo.value}"
    # 抛之前**不许**留下半个改动（账本要还是刚才那一条删除）。
    assert cs.edits() == before
    assert cs.count() == 1


# --------------------------------------------------------------------------- #
# 5. 三个动词各自的守卫
# --------------------------------------------------------------------------- #


def test_add_refuses_a_path_id_the_container_already_has() -> None:
    """pathID 不许撞（`asset_kernel/writer.py::NewObject` 是同一条守卫）。"""
    ref = _object_ref()
    cs = AssetChangeset(_Baseline().seed(ref, b"AAA"))

    with pytest.raises(AssetError) as excinfo:
        cs.add_object(ref, b"BBB")

    assert ref.path_id is not None and str(ref.path_id) in str(excinfo.value)
    assert cs.is_empty()


def test_add_refuses_the_same_new_path_id_twice() -> None:
    ref = _object_ref(9001)
    cs = AssetChangeset(_Baseline())
    cs.add_object(ref, b"AAA")

    with pytest.raises(AssetError):
        cs.add_object(ref, b"AAA")
    assert cs.count() == 1


def test_delete_refuses_an_object_that_is_not_there() -> None:
    ref = _object_ref(9001)
    cs = AssetChangeset(_Baseline())

    with pytest.raises(AssetError) as excinfo:
        cs.delete_object(ref)

    assert "没有" in str(excinfo.value)
    assert cs.is_empty()


def test_delete_absorbs_the_field_edits_on_that_object() -> None:
    """对象没了，它身上的字段改动没有意义（照 `delete_row`，`changeset.py:334-335`）。

    负控：不吸收的话账本里会同时留着「改这个字段」与「把这个对象删掉」两条 ——
    写盘时那一笔字段改动会落到一个不存在的对象上。
    """
    field_ref, object_ref = _field_ref(), _object_ref()
    cs = AssetChangeset(_Baseline().seed(field_ref, 58076).seed(object_ref, b"AAA"))
    cs.set_field(field_ref, 58077)
    assert cs.count() == 1

    cs.delete_object(object_ref)

    assert [edit.kind for edit in cs.edits()] == ["delete"]
    assert cs.count() == 1, "被吸收的字段改动不许留在账本里"


def test_set_field_on_a_deleted_object_is_refused() -> None:
    field_ref, object_ref = _field_ref(), _object_ref()
    cs = AssetChangeset(_Baseline().seed(field_ref, 58076).seed(object_ref, b"AAA"))
    cs.delete_object(object_ref)

    with pytest.raises(AssetError):
        cs.set_field(field_ref, 58077)
    assert [edit.kind for edit in cs.edits()] == ["delete"]


def test_set_field_on_a_pending_new_object_is_refused() -> None:
    """新增的对象载荷是**整份字节** —— 账本没有它的字段，改不了（也就不能假装改了）。"""
    field_ref = _field_ref(9001)
    cs = AssetChangeset(_Baseline())
    cs.add_object(_object_ref(9001), b"AAA")

    with pytest.raises(AssetError):
        cs.set_field(field_ref, 58077)
    assert [edit.kind for edit in cs.edits()] == ["add"]


def test_value_of_a_field_on_a_deleted_object_is_missing() -> None:
    """字段的键与它所在对象的键不同 —— 读值也要认「对象被整个拿掉了」。"""
    field_ref, object_ref = _field_ref(), _object_ref()
    cs = AssetChangeset(_Baseline().seed(field_ref, 58076).seed(object_ref, b"AAA"))
    assert cs.value(field_ref) == 58076

    cs.delete_object(object_ref)

    assert cs.value(field_ref) is MISSING


def test_whole_object_verbs_refuse_a_field_ref() -> None:
    cs = AssetChangeset(_Baseline())
    with pytest.raises(AssetError):
        cs.add_object(_field_ref(), b"AAA")
    with pytest.raises(AssetError):
        cs.delete_object(_field_ref())
    assert cs.is_empty()


def test_whole_object_verbs_need_a_path_id() -> None:
    """`path_id=None` 且 `field=None` 是「整个容器」，不是某个对象。"""
    whole_container = AssetRef(container=BUNDLE)
    cs = AssetChangeset(_Baseline())
    with pytest.raises(AssetError):
        cs.add_object(whole_container, b"AAA")
    with pytest.raises(AssetError):
        cs.delete_object(whole_container)


def test_set_field_needs_a_field() -> None:
    cs = AssetChangeset(_Baseline().seed(_object_ref(), b"AAA"))
    with pytest.raises(AssetError):
        cs.set_field(_object_ref(), b"BBB")


# --------------------------------------------------------------------------- #
# 6. 交给写盘的形状（plan）
# --------------------------------------------------------------------------- #


def test_plan_groups_by_verb_and_splits_by_container() -> None:
    """三格分开 = 「先字段、后对象」不需要调用方自己再排一遍。"""
    here_field, here_new = _field_ref(), _object_ref(9001)
    there_field, there_gone = _field_ref(7, container=OTHER), _object_ref(555, container=OTHER)
    base = (
        _Baseline()
        .seed(here_field, 58076)
        .seed(there_field, 1)
        .seed(there_gone, b"ZZZ")
    )
    cs = AssetChangeset(base)
    cs.set_field(here_field, 58077)
    cs.add_object(here_new, b"AAA")
    cs.set_field(there_field, 2)
    cs.delete_object(there_gone)

    plan = cs.plan()

    assert isinstance(plan, AssetPlan)
    assert (len(plan.sets), len(plan.adds), len(plan.deletes)) == (2, 1, 1)
    assert plan.count == 4
    assert plan.containers() == (BUNDLE, OTHER)
    assert [edit.kind for edit in plan.edits()] == ["set", "set", "add", "delete"]

    only_other = plan.for_container(OTHER)
    assert only_other.count == 2
    assert {edit.ref.container for edit in only_other.edits()} == {OTHER}
    assert only_other.adds == ()


def test_an_empty_plan_is_falsy() -> None:
    plan = AssetChangeset(_Baseline()).plan()
    assert plan.is_empty and plan.count == 0 and not plan
    assert plan.containers() == ()


# --------------------------------------------------------------------------- #
# 7. 人话一行（状态栏 / 日志）
# --------------------------------------------------------------------------- #


def test_summary_is_one_human_line_per_verb() -> None:
    field_ref, object_ref = _field_ref(), _object_ref(9001)
    base = _Baseline().seed(field_ref, 58076).seed(_object_ref(), b"AAA")
    cs = AssetChangeset(base)

    assert cs.set_field(field_ref, 58077).summary == (
        f"修改 units.bundle#58076.{SLOT}（58076 → 58077）"
    )
    assert cs.add_object(object_ref, b"AAA").summary == "新增 units.bundle#9001（3 字节）"
    assert cs.delete_object(_object_ref()).summary == "删除 units.bundle#58076（3 字节）"


def test_missing_is_falsy_and_says_its_name() -> None:
    """哨兵要能一眼看懂：`None` 冒充「没有这个东西」会让两条守卫静默失效。"""
    assert not MISSING
    assert repr(MISSING) == "MISSING"


# --------------------------------------------------------------------------- #
# 8. 引用本身（asset_refs.py）
# --------------------------------------------------------------------------- #


def test_the_two_field_kinds_never_share_a_key() -> None:
    """有值语义的路径与无值语义的字节区间**不可比** ⇒ 键也不许撞（设计稿 §2 的 ⚠）。"""
    as_path = AssetRef(BUNDLE, 1, FieldPath("x"))
    as_bytes = AssetRef(BUNDLE, 1, ByteSpan(0, 8))

    assert as_path.key() != as_bytes.key()
    assert as_path.key() == AssetRef(BUNDLE, 1, FieldPath("x")).key()
    assert as_bytes.key() == AssetRef(BUNDLE, 1, ByteSpan(0, 8)).key()


def test_a_string_container_is_the_same_key_as_a_path() -> None:
    """同一个文件的两种写法**必须是同一个键** —— 否则同一次改动会记成两条。"""
    from_str = AssetRef("aa/PC/units.bundle", 58076, SLOT)
    from_path = AssetRef(Path("aa/PC/units.bundle"), 58076, SLOT)

    assert from_str.container == Path("aa/PC/units.bundle")
    assert from_str.key() == from_path.key()


def test_an_empty_container_is_refused() -> None:
    for text in ("", "   "):
        with pytest.raises(ValueError):
            AssetRef(text)


def test_ref_refuses_a_foreign_field_type() -> None:
    with pytest.raises(TypeError):
        AssetRef(BUNDLE, 1, "m_TexEnvs[0]")  # type: ignore[arg-type]


def test_ref_refuses_a_bool_path_id() -> None:
    """`True` 是 `int` 的子类 —— 不挡住就会静默指到 #1 那个对象上。"""
    with pytest.raises(TypeError):
        AssetRef(BUNDLE, True)  # type: ignore[arg-type]


def test_byte_span_refuses_bad_ranges() -> None:
    with pytest.raises(ValueError):
        ByteSpan(-1, 8)
    with pytest.raises(ValueError):
        ByteSpan(0, 0)
    with pytest.raises(TypeError):
        ByteSpan(0, 8.0)  # type: ignore[arg-type]


def test_ref_str_is_one_human_line() -> None:
    assert str(AssetRef(BUNDLE, 58076)) == "units.bundle#58076"
    assert str(AssetRef(BUNDLE, 58076, ByteSpan(0xC, 8))) == "units.bundle#58076.@0xC+8"
    assert AssetRef(BUNDLE, 58076).is_whole_object
    assert not AssetRef(BUNDLE, 58076, SLOT).is_whole_object
