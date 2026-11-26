"""可见性层 user_items 的机械判据（设计稿 ④ 层）。

## 为什么在**合成容器**上跑，而不是真实 data.unity3d

判据全部落在**这一层自己的不变量**上（对象字节、反向 Δ、守卫），与容器多大无关：
小容器与 7.13 GB 的真件**同构**（同一个 ufs.rewrite_container、同一棵 417 节点类型树）。
真件那个只读正控单列一条 slow 用例（pytest -m slow 才会跑；⛔ 它一个字节都不写）。

## 尺子先做正控（AGENTS §0.9 动作 2）

test_write_check_ruler_goes_red_on_a_foreign_container 是**写后复核那把尺子**的正控：
拿一份内容不同的容器喂进去，它**必须红**。没有它，剩下那些"复核通过"全是假绿。

## J6 反向 Δ

test_reverse_delta_restores_the_container_bit_for_bit：装完 -> 反向 Δ 还原 ->
产物与原件**逐字节**相同（不是"内容看起来一样"）。
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ba_mod_maker.asset_kernel import clone, reverse_delta
from ba_mod_maker.asset_kernel import user_items as ui
from ba_mod_maker.core.asset import ufs
from ba_mod_maker.core.errors import AssetError

#: 造容器用的块大小（与产品一致：128 KiB）。
CHUNK = ufs.RECHUNK_SIZE
#: 节点名（产品读侧从这里找对象表）。
NODE = ui.NODE_PATH
#: 真件里那个 220,220 B 的实例号 —— 合成件沿用它，读起来跟 kb/30 §4 的表对得上。
PID = 52174
#: 同节点里的另一个对象（用于「别的对象逐字节未变」这条判据）。
OTHER_PID = 52175
#: 真件里 = T-72B 的 Units.Id（设计稿 §1.1：Skins._keyData[210] = 9）。
UNIT = 9
#: 真件里单位 9 的 8 条皮肤 Id（设计稿 §1.1 实测 2232..2239）。
REAL_IDS = (2232, 2233, 2234, 2235, 2236, 2237, 2238, 2239)


# --------------------------------------------------------------------------- #
# 合成容器
# --------------------------------------------------------------------------- #
def _blob(tag: bytes, n: int) -> bytes:
    return (tag * (n // len(tag) + 1))[:n]


def _sf(objs: list[tuple[int, bytes]]) -> bytes:
    """造一个最小的 SerializedFile v22 节点（口径同 tests/test_ufs_writeback.py）。

    type_count = 0：MonoBehaviour 的字段树**不由容器提供**（真件的 91 个节点
    实测 enable_type_tree 全是 False），由 user_items.type_tree() 冻结着给。
    """
    body = bytearray()
    body += b"2022.3.62f3\x00"
    body += struct.pack("<i", 19)
    body += b"\x00"
    body += struct.pack("<i", 0)
    body += struct.pack("<i", len(objs))
    table: list[tuple[int, int, int]] = []
    cursor = 0
    for pid, payload in objs:
        table.append((pid, cursor, len(payload)))
        cursor += len(payload)
    for pid, stored, size in table:
        body += bytes((-len(body)) % 4)
        body += struct.pack("<qqii", pid, stored, size, 0)
    data_offset = 48 + len(body)
    total = data_offset + cursor
    head = bytearray(48)
    struct.pack_into(">I", head, 8, 22)
    head[16] = 0
    struct.pack_into(">I", head, 20, len(body))
    struct.pack_into(">q", head, 24, total)
    struct.pack_into(">q", head, 32, data_offset)
    return bytes(head) + bytes(body) + b"".join(p for _pid, p in objs)


def _container(sf_blob: bytes) -> bytes:
    blocks: list[ufs.Block] = []
    payload = bytearray()
    for i in range(0, len(sf_blob), CHUNK):
        raw = sf_blob[i : i + CHUNK]
        comp = ufs.lz4hc(raw)
        if len(comp) >= len(raw):
            payload += raw
            blocks.append(ufs.Block(len(raw), len(raw), 0))
        else:
            payload += comp
            blocks.append(ufs.Block(len(raw), len(comp), 3))
    nodes = [ufs.Node(offset=0, size=len(sf_blob), flags=4, path=NODE)]
    raw_info = ufs.serialize_blocks_info(b"\x11" * 16, blocks, nodes)
    comp_info = ufs.lz4hc(raw_info)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", 0, len(comp_info), len(raw_info), 0x43
    )
    total = len(header) + len(comp_info) + len(payload)
    header = ufs.serialize_header(
        6, "5.x.x", "2022.3.62f3", total, len(comp_info), len(raw_info), 0x43
    )
    return header + comp_info + bytes(payload)


# --------------------------------------------------------------------------- #
# 合成 typetree
# --------------------------------------------------------------------------- #
def _row(
    item_id: int,
    pack: str = "1 Basic camouflage",
    triggers: tuple[dict, ...] = (),
    is_default: int = 0,
    free: int = 1,
    disabled: int = 0,
    grade: int = 3,
    hide_unlock: int = 1,
    none_element: int = 0,
    hide_if: int = 1,
) -> dict:
    """一条真件形状的 SkinItemData（10 个字段，产品只管其中 6 个）。"""
    return {
        "Id": item_id,
        "Grade": grade,
        "SkinPack": pack,
        "Triggers": [dict(t) for t in triggers],
        "HideUnlockInfo": hide_unlock,
        "IsDefault": is_default,
        "NoneElement": none_element,
        "HideIfNotUnlocked": hide_if,
        "Disabled": disabled,
        "Free": free,
    }


def _tree(units: dict[int, list[dict]] | None = None) -> dict:
    if units is None:
        units = {UNIT: [_row(2232, is_default=1), _row(2233, free=0)]}
    keys = list(units)
    return {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
        "m_Enabled": 1,
        "m_Script": {"m_FileID": 0, "m_PathID": 10835},
        "m_Name": ui.CONFIG_NAME,
        "_errors": [],
        "Avatars": [],
        "Patches": [],
        "Frames": [],
        "Icons": [],
        "Epaulets": [],
        "Skins": {
            "_keyData": keys,
            "_valueData": [{"Data": [dict(r) for r in units[k]]} for k in keys],
        },
        "SkinPacks": {"_keyData": [], "_valueData": []},
        "DLC": {"_keyData": [], "_valueData": []},
        "DLCContainers": [],
    }


def _payload(tree: dict) -> bytes:
    return clone.write_object(tree, ui.type_tree())


@pytest.fixture
def container(tmp_path: Path) -> Path:
    """一份小容器：一个 200 KB 的填充对象 ＋ 那颗合成的 UserItemsConfig（跨若干块）。"""
    objs = [(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(_tree()))]
    path = tmp_path / "globalgamemanagers.unity3d"
    path.write_bytes(_container(_sf(objs)))
    return path


def _real_binary() -> Path:
    from ba_mod_maker.core.assets.game import find_game_root

    return find_game_root() / "BrokenArrow_Data" / "data.unity3d"


# --------------------------------------------------------------------------- #
# 0 · 类型树与夹具自身的正控
# --------------------------------------------------------------------------- #
def test_frozen_type_tree_has_417_nodes_in_document_order() -> None:
    """冻结的类型树：417 个节点、level 从 0 起、第一项是 MonoBehaviour 的 Base。"""
    nodes = ui.type_tree()
    assert len(nodes) == 417
    assert nodes[0].level == 0 and nodes[0].type == "MonoBehaviour" and nodes[0].name == "Base"
    assert nodes[1].level == 1 and nodes[1].name == "m_GameObject"
    assert ui.type_tree() is nodes, "类型树要缓存（每次重解析 417 行太浪费）"


def test_synthetic_tree_round_trips_through_the_frozen_tree() -> None:
    """夹具自身的正控：合成 typetree 写完再读 == 原样（否则后面所有用例都是假的）。"""
    tree = _tree()
    assert clone.read_object(_payload(tree), ui.type_tree()) == tree


# --------------------------------------------------------------------------- #
# 1 · 纯函数层：读
# --------------------------------------------------------------------------- #
def test_skin_items_reads_the_six_product_fields() -> None:
    items = ui.skin_items(_tree(), UNIT)
    assert [i.item_id for i in items] == [2232, 2233]
    assert items[0].skin_pack == "1 Basic camouflage"
    assert (items[0].is_default, items[0].free, items[0].disabled) == (1, 1, 0)
    assert items[0].triggers == ()


def test_skin_items_keeps_whole_trigger_rows() -> None:
    """触发器整条原样保留（Trigger / DescriptionLocKey / Count 三项，外加以后的字段）。"""
    trig = {"Trigger": "mission_complete_RU_N04", "DescriptionLocKey": "ui_x", "Count": 3}
    tree = _tree({UNIT: [_row(2232, triggers=(trig,))]})
    got = ui.skin_items(tree, UNIT)[0].triggers
    assert got == (trig,)
    assert got[0]["Count"] == 3
    # 读出来的触发器不能与入参共享（改了读到的会不会顺手改坏原树）
    assert got[0] is not trig


def test_skin_items_does_not_mutate_the_input_tree() -> None:
    tree = _tree()
    before = _payload(tree)
    ui.skin_items(tree, UNIT)
    assert _payload(tree) == before


def test_unit_id_not_in_table_raises_with_the_existing_keys() -> None:
    with pytest.raises(AssetError) as exc:
        ui.skin_items(_tree(), 999999)
    assert "999999" in str(exc.value)
    assert "9" in str(exc.value)


def test_parallel_arrays_must_be_equal_length() -> None:
    tree = _tree()
    tree["Skins"]["_valueData"] = []
    with pytest.raises(AssetError) as exc:
        ui.skin_items(tree, UNIT)
    assert "平行数组" in str(exc.value)


def test_unit_ids_lists_the_keys_in_order() -> None:
    assert ui.unit_ids(_tree({3: [], 1: []})) == (3, 1)


# --------------------------------------------------------------------------- #
# 2 · 纯函数层：三个动词
# --------------------------------------------------------------------------- #
def test_add_copies_the_four_private_fields_from_the_reference() -> None:
    """新增**照抄参照条**：产品只管 6 个字段，其余 4 个逐字段等于参照条。"""
    tree = _tree()
    ref_row = tree["Skins"]["_valueData"][0]["Data"][1]      # 2233，free=0
    item = ui.SkinItem(9999, "9 Probe",
                ({"Trigger": "t", "DescriptionLocKey": "k", "Count": 1},), 1, 1, 0)
    after = ui.add_skin_item(tree, unit_id=UNIT, item=item, reference_id=2233)
    rows = after["Skins"]["_valueData"][0]["Data"]
    assert len(rows) == 3
    assert ui.skin_items(after, UNIT)[-1] == item
    assert [rows[-1][f] for f in ui.COPIED_FIELDS] == [ref_row[f] for f in ui.COPIED_FIELDS]
    # 入参树没被改
    assert len(ui.skin_items(tree, UNIT)) == 2


def test_add_rejects_a_duplicate_id_and_lists_the_existing_ids() -> None:
    with pytest.raises(AssetError) as exc:
        ui.add_skin_item(
            _tree(), unit_id=UNIT, item=ui.SkinItem(2232, "x", (), 0, 0, 0), reference_id=2233
        )
    assert "2232" in str(exc.value)


def test_add_rejects_a_missing_reference() -> None:
    with pytest.raises(AssetError) as exc:
        ui.add_skin_item(
            _tree(), unit_id=UNIT, item=ui.SkinItem(9999, "x", (), 0, 0, 0), reference_id=8888
        )
    assert "8888" in str(exc.value)


def test_add_rejects_a_reference_from_another_unit() -> None:
    tree = _tree({UNIT: [_row(2232)], 10: [_row(5000)]})
    with pytest.raises(AssetError):
        ui.add_skin_item(
            tree, unit_id=UNIT, item=ui.SkinItem(9999, "x", (), 0, 0, 0), reference_id=5000
        )

def test_add_rejects_an_id_owned_by_another_unit_and_names_it() -> None:
    """★ 2026-10-11 **真机现场**学到的守卫：皮肤 Id 是**全局**键。

    只查本单位那张表是不够的 —— 游戏侧 `GetAllSkins()` 把**所有单位**的皮肤摊平成
    一个 `Dictionary<int, SkinItemData>`，撞号 ⇒ `ArgumentException: An item with the
    same key has already been added` ⇒ `UnitInfoCard.SetUnitSkins` 整个抛出去 ⇒
    **军械库那张卡根本建不出来**（真机：卡片整块消失 ＋ 日志 `Couldn't load unit: T-72B`）。
    """
    # 2240 是别家单位的（真件 = T-72B3 的皮肤）
    tree = _tree({UNIT: [_row(2232)], 157: [_row(2240)]})
    with pytest.raises(AssetError) as exc:
        ui.add_skin_item(
            tree, unit_id=UNIT, item=ui.SkinItem(2240, "x", (), 0, 0, 0), reference_id=2232
        )
    assert "157" in str(exc.value) and "全局" in str(exc.value)


def test_add_accepts_a_globally_free_id() -> None:
    """正控：**全局没人用过**的号 ⇒ 放行（`item_owner` 的读数一并钉住）。"""
    tree = _tree({UNIT: [_row(2232)], 157: [_row(2240)]})
    assert ui.item_owner(tree, 2240) == 157
    assert ui.item_owner(tree, 2730) is None
    after = ui.add_skin_item(
        tree, unit_id=UNIT, item=ui.SkinItem(2730, "x", (), 0, 0, 0), reference_id=2232
    )
    assert [i.item_id for i in ui.skin_items(after, UNIT)] == [2232, 2730]
    assert ui.item_owner(after, 2730) == UNIT


def test_add_rejects_a_non_positive_id() -> None:
    for bad in (0, -1):
        with pytest.raises(AssetError) as exc:
            ui.add_skin_item(
                _tree(), unit_id=UNIT, item=ui.SkinItem(bad, "x", (), 0, 0, 0), reference_id=2232
            )
        assert "不是正数" in str(exc.value)


def test_remove_drops_exactly_one_row() -> None:
    after = ui.remove_skin_item(_tree(), unit_id=UNIT, item_id=2232)
    assert [i.item_id for i in ui.skin_items(after, UNIT)] == [2233]


def test_remove_of_an_unknown_id_raises() -> None:
    with pytest.raises(AssetError) as exc:
        ui.remove_skin_item(_tree(), unit_id=UNIT, item_id=8888)
    assert "8888" in str(exc.value)


def test_update_changes_only_the_given_field_and_leaves_other_rows_alone() -> None:
    tree = _tree()
    before_rows = tree["Skins"]["_valueData"][0]["Data"]
    after = ui.update_skin_item(tree, unit_id=UNIT, item_id=2232, free=0)
    rows = after["Skins"]["_valueData"][0]["Data"]
    assert rows[0]["Free"] == 0
    assert rows[0]["Grade"] == before_rows[0]["Grade"]      # 产品不管的字段原样
    assert rows[1] == before_rows[1], "没动过的那一行必须原样照抄"


def test_update_rejects_an_empty_change() -> None:
    with pytest.raises(AssetError) as exc:
        ui.update_skin_item(_tree(), unit_id=UNIT, item_id=2232)
    assert "空改动" in str(exc.value)
    with pytest.raises(AssetError) as exc2:
        ui.update_skin_item(_tree(), unit_id=UNIT, item_id=2232, free=1)
    assert "没有任何变化" in str(exc2.value)


def test_update_rejects_fields_the_product_does_not_own() -> None:
    with pytest.raises(AssetError) as exc:
        ui.update_skin_item(_tree(), unit_id=UNIT, item_id=2232, Grade=7)
    assert "Grade" in str(exc.value)


def test_set_skin_items_rejects_a_non_positive_new_id() -> None:
    with pytest.raises(AssetError):
        ui.set_skin_items(_tree(), UNIT, (ui.SkinItem(0, "x", (), 0, 0, 0),))


# --------------------------------------------------------------------------- #
# 2.1 · 「产品不管的」那 4 个字段从哪里来（⛔ **不是第 0 行**）
#
# 2026-10-11 量出来的真 bug：update_skin_item 改**非第一行**时，那 4 个字段被
# **第 0 行**的值洗掉（实测 Grade [3, 7] -> [3, 3]）。根因是 set_skin_items 里
# 「值变了的条目」也走 _item_row(item, reference)，而 reference 默认 = raw[0]。
# 复算：_work\dgaps\probe_private_fields.py
# --------------------------------------------------------------------------- #
#: 产品**不管**的 4 个字段（新增时照抄参照条；修改时照抄**它自己**那一行）。
PRIVATE_FIELDS = ("Grade", "HideUnlockInfo", "NoneElement", "HideIfNotUnlocked")


def _private(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row[field] for field in PRIVATE_FIELDS)


def _two_rows_with_different_private_fields() -> dict:
    """两行的私有字段**故意不同**（Grade 3 / 7）—— 下面那把尺子能红的前提。"""
    return _tree({UNIT: [_row(2232, grade=3), _row(2233, grade=7)]})


def test_the_private_field_ruler_can_go_red() -> None:
    """**尺子正控**：两行的私有字段真的不同，且「拿第 0 行当来源」**看得见**这个差别。

    没有这一条，下面两条断言在私有字段相同的夹具上**永远绿**（假绿）。
    """
    rows = _two_rows_with_different_private_fields()["Skins"]["_valueData"][0]["Data"]
    assert _private(rows[0]) != _private(rows[1]), "夹具坏了：两行私有字段相同，尺子红不了"
    item = ui.skin_items(_two_rows_with_different_private_fields(), UNIT)[1]
    washed = ui._item_row(replace(item, disabled=1), rows[0])  # 旧行为：来源 = 第 0 行
    assert _private(washed) != _private(rows[1]), "「拿第 0 行当来源」与 truth 一样 —— 尺子看不见"


def test_update_keeps_the_private_fields_of_a_non_first_row() -> None:
    """改**第 2 行**：改的那一格要落位，它自己的 4 个私有字段**一字不变**。"""
    tree = _two_rows_with_different_private_fields()
    rows_before = tree["Skins"]["_valueData"][0]["Data"]
    after = ui.update_skin_item(tree, unit_id=UNIT, item_id=2233, disabled=1)
    rows = after["Skins"]["_valueData"][0]["Data"]
    assert rows[1]["Disabled"] == 1, "要改的那一格没落位"
    assert _private(rows[1]) == _private(rows_before[1]), "改一行把它自己的私有字段洗掉了"
    assert _private(rows[0]) == _private(rows_before[0])
    assert rows[0] == rows_before[0], "没动过的那一行必须原样照抄"


def test_update_keeps_the_private_fields_of_the_first_row() -> None:
    """**正控的另一半**：改第 0 行同样一字不变（口径与行号无关）。"""
    tree = _two_rows_with_different_private_fields()
    rows_before = tree["Skins"]["_valueData"][0]["Data"]
    rows = ui.update_skin_item(tree, unit_id=UNIT, item_id=2232, free=0)[
        "Skins"
    ]["_valueData"][0]["Data"]
    assert rows[0]["Free"] == 0 and _private(rows[0]) == _private(rows_before[0])


# --------------------------------------------------------------------------- #
# 2.2 · 触发器（UnlockTriggersData）的三个动词：新增 / 删除 / 修改
# --------------------------------------------------------------------------- #
def _trigger(name: str, loc: str = "loc_a", count: int = 3) -> dict:
    return {"Trigger": name, "DescriptionLocKey": loc, "Count": count}


def _item_with_triggers(*names: str) -> ui.SkinItem:
    tree = _tree({UNIT: [_row(2232, triggers=tuple(_trigger(n) for n in names))]})
    return ui.skin_items(tree, UNIT)[0]


def test_add_trigger_appends_and_keeps_the_existing_indexes() -> None:
    """**新增**一条触发器：条数 N -> N+1，追加在末尾（既有下标一个都不动）。"""
    item = _item_with_triggers("a", "b")
    after = ui.add_trigger(item, trigger="c", description_loc_key="loc_c", count=0)
    assert [t["Trigger"] for t in after.triggers] == ["a", "b", "c"]
    assert after.triggers[2] == _trigger("c", "loc_c", 0)
    assert item.triggers[0]["Trigger"] == "a", "入参不该被改（冻结数据类，逐字段）"
    assert len(item.triggers) == 2


def test_remove_trigger_drops_exactly_that_index() -> None:
    """**删除**第 index 条：只少那一条，其余顺序不变。"""
    after = ui.remove_trigger(_item_with_triggers("a", "b", "c"), index=1)
    assert [t["Trigger"] for t in after.triggers] == ["a", "c"]


@pytest.mark.parametrize("index", [-1, 2, 99])
def test_remove_trigger_out_of_range_names_the_existing_table(index: int) -> None:
    """**负控**：越界 ⇒ 抛，并把现有的那条清单印出来（⛔ 不静默当空）。"""
    with pytest.raises(AssetError) as exc:
        ui.remove_trigger(_item_with_triggers("a", "b"), index=index)
    text = str(exc.value)
    assert str(index) in text and "[0] 'a'" in text and "[1] 'b'" in text


def test_update_trigger_touches_only_the_given_field_and_keeps_extra_keys() -> None:
    """**修改**第 index 条：只动给出来的那几格；这一条上多出来的键**原样留着**。"""
    row = {**_trigger("a"), "FutureField": "游戏以后加的"}
    item = ui.skin_items(_tree({UNIT: [_row(2232, triggers=(row,))]}), UNIT)[0]
    after = ui.update_trigger(item, index=0, count=9)
    assert after.triggers[0] == {**_trigger("a", count=9), "FutureField": "游戏以后加的"}
    assert item.triggers[0]["Count"] == 3, "入参不该被改"


def test_update_trigger_rejects_empty_and_unchanged() -> None:
    item = _item_with_triggers("a")
    with pytest.raises(AssetError) as exc:
        ui.update_trigger(item, index=0)
    assert "空改动" in str(exc.value)
    with pytest.raises(AssetError) as exc2:
        ui.update_trigger(item, index=0, count=3)
    assert "没有任何变化" in str(exc2.value)


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"trigger": 7}, "Trigger"),
        ({"description_loc_key": None}, "DescriptionLocKey"),
        ({"count": "3"}, "Count"),
        ({"count": True}, "Count"),
        ({"count": -1}, "负数"),
    ],
    ids=["trigger-int", "loc-key-none", "count-str", "count-bool", "count-negative"],
)
def test_make_trigger_refuses_wrong_types(kwargs: dict, needle: str) -> None:
    """⛔ 不造默认值、也不容忍类型不对：string / string / unsigned int 三格各有各的形状。"""
    values = {"trigger": "a", "description_loc_key": "b", "count": 1, **kwargs}
    with pytest.raises(AssetError) as exc:
        ui.make_trigger(**values)
    assert needle in str(exc.value), str(exc.value)


def test_make_trigger_accepts_count_zero() -> None:
    """**正控**：0 是 Count 的合法取值（⛔ 别拿 0 当「没给」）。"""
    assert ui.make_trigger(trigger="a", description_loc_key="b", count=0) == _trigger("a", "b", 0)


def test_plan_trigger_update_writes_and_reads_back(tmp_path: Path) -> None:
    """端到端：在**非第一行**上改一条触发器 -> 真写 -> 读回：触发器变了、私有字段一字不变。"""
    tree = _tree({UNIT: [_row(2232, grade=3), _row(2233, grade=7, triggers=(_trigger("a"),))]})
    case = tmp_path / "globalgamemanagers.unity3d"
    case.write_bytes(
        _container(_sf([(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(tree))]))
    )
    item = ui.item_of(tree, UNIT, 2233)
    plan = ui.plan_update(
        case,
        PID,
        unit_id=UNIT,
        item_id=2233,
        changes={"triggers": ui.update_trigger(item, index=0, count=9).triggers},
        operation="修改触发器",
    )
    assert plan.operation == "修改触发器"
    out = ui.install(plan, tmp_path / "out-trigger.unity3d")
    assert ui.read_unit_items(out.target, PID, UNIT)[1].triggers == (_trigger("a", count=9),)
    rows = ui.read_container_config(out.target, PID).tree["Skins"]["_valueData"][0]["Data"]
    assert rows[1]["Grade"] == 7 and rows[0]["Grade"] == 3, "私有字段被洗了"


# --------------------------------------------------------------------------- #
# 3 · 容器层：读 / 干跑 / 真写 / 反向 Δ
# --------------------------------------------------------------------------- #
def test_read_container_config_round_trips_the_object(container: Path) -> None:
    obj = ui.read_container_config(container, PID)
    assert obj.name == ui.CONFIG_NAME
    assert obj.size == len(_payload(_tree()))
    assert obj.unit_count == 1
    assert ui.skin_items(obj.tree, UNIT)[0].item_id == 2232
    assert obj.class_id == ufs.MONO_BEHAVIOUR_CLASS_ID


def test_read_container_config_rejects_the_wrong_object(container: Path) -> None:
    with pytest.raises(AssetError):
        ui.read_container_config(container, OTHER_PID)


def test_plan_add_is_a_dry_run(container: Path) -> None:
    before = container.read_bytes()
    plan = ui.plan_add(
        container,
        PID,
        unit_id=UNIT,
        item=ui.SkinItem(9999, "9 Probe", (), 1, 1, 0),
        reference_id=2232,
    )
    assert plan.delta > 0 and plan.level.startswith("变长")
    assert len(plan.items_after) == len(plan.items_before) + 1
    assert container.read_bytes() == before, "干跑必须一个字节都不写"
    assert plan.criterion_lines()[1].startswith("  单位 9 的皮肤条目 2 -> 3")


@pytest.mark.parametrize(
    ("name", "maker"),
    [
        ("新增", lambda c: ui.plan_add(
            c, PID, unit_id=UNIT, item=ui.SkinItem(9999, "9 Probe", (), 1, 1, 0),
            reference_id=2232)),
        ("删除", lambda c: ui.plan_remove(c, PID, unit_id=UNIT, item_id=2232)),
        ("修改", lambda c: ui.plan_update(c, PID, unit_id=UNIT, item_id=2232, changes={"free": 0})),
    ],
)
def test_three_verbs_really_write_and_verify(container: Path, tmp_path: Path, name, maker) -> None:
    """端到端：干跑 -> 真写一个新容器 -> 写后复核 -> 从盘上读回期望的那张表。"""
    plan = maker(container)
    out = ui.install(plan, tmp_path / ("out-" + str(name) + ".unity3d"))
    assert out.target.is_file()
    assert plan.operation == name
    got = ui.read_unit_items(out.target, PID, UNIT)
    assert got == plan.items_after
    # 同节点里别的对象逐字节没变
    with ufs.UfsReader(out.target) as reader:
        node = reader.node(NODE)
        info = ufs.read_serialized_file(reader, node)
        row = next(r for r in info.objects if r[1] == OTHER_PID)
        other = reader.read_range(node.offset + info.data_offset + row[2], row[3])
    assert other == _blob(b"F", 200_000)
    assert out.report.delta == plan.delta



def test_reverse_delta_restores_the_container_bit_for_bit(container: Path, tmp_path: Path) -> None:
    """**J6**：装完 -> 反向 Δ 还原 -> 产物与原件**逐字节**相同。"""
    original = container.read_bytes()
    plan = ui.plan_add(
        container,
        PID,
        unit_id=UNIT,
        item=ui.SkinItem(9999, "9 Probe", (), 1, 1, 0),
        reference_id=2232,
    )
    out = ui.install(plan, tmp_path / "installed.unity3d")
    assert out.target.read_bytes() != original
    back = tmp_path / "restored.unity3d"
    report = ui.restore(out.reverse, back)
    assert back.read_bytes() == original, "反向 Δ 还原必须逐位相同"
    assert report.delta == -plan.delta
    assert out.reverse.record()["origin"] == str(container)
    assert "单位 9" in out.reverse.label()


def test_restore_refuses_when_the_source_is_not_the_installed_one(
    container: Path, tmp_path: Path
) -> None:
    """负控：把反向 Δ 指向**没装过**的那份（对象还是旧长度）=> 必须抛，不许硬写。"""
    plan = ui.plan_remove(container, PID, unit_id=UNIT, item_id=2232)
    out = ui.install(plan, tmp_path / "installed.unity3d")
    delta = replace(out.reverse, container=container)
    bad = tmp_path / "x.unity3d"
    with pytest.raises(AssetError) as exc:
        ui.restore(delta, bad)
    assert "记的是" in str(exc.value)
    assert not bad.exists(), "抛错之后不许留下半成品"


def test_install_refuses_to_write_over_the_source(container: Path) -> None:
    plan = ui.plan_remove(container, PID, unit_id=UNIT, item_id=2232)
    with pytest.raises(AssetError):
        ui.install(plan, container)


def test_write_check_ruler_goes_red_on_a_foreign_container(container: Path, tmp_path: Path) -> None:
    """**尺子正控**（AGENTS §0.9 动作 2）：写后复核必须能红。

    绿的那一侧先跑一遍 —— 否则「红了」分不清是尺子坏还是夹具坏。
    """
    plan = ui.plan_remove(container, PID, unit_id=UNIT, item_id=2232)
    out = ui.install(plan, tmp_path / "ok.unity3d")
    assert ui.verify_install(plan, out.target), "正控的绿侧：装完必须复核通过"
    foreign = tmp_path / "foreign.unity3d"
    foreign.write_bytes(
        _container(_sf([(OTHER_PID, _blob(b"F", 200_000)), (PID, _payload(_tree()))]))
    )
    with pytest.raises(AssetError):
        ui.verify_install(plan, foreign)


def test_install_removes_the_half_product_when_verification_fails(
    container: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写后复核不过 => 半成品必须删掉（否则游戏目录里留下一个满尺寸的怪文件）。"""
    plan = ui.plan_remove(container, PID, unit_id=UNIT, item_id=2232)
    out = tmp_path / "half.unity3d"

    def _boom(_plan, _target):
        raise AssetError("复核故意红")

    monkeypatch.setattr(ui, "verify_install", _boom)
    with pytest.raises(AssetError):
        ui.install(plan, out)
    assert not out.exists()


@pytest.mark.slow
def test_real_container_both_instances_are_readable_and_guarded() -> None:
    """真件只读正控（pytest -m slow 才会跑）：⛔ 一个字节都不写。

    读数：两个实例都能被这棵冻结树**逐字节往返**；单位 9 的 8 条 Id 就是设计稿 §1.1 那批。
    """
    data = _real_binary()
    if not data.is_file():
        pytest.skip("这台机器上没有 data.unity3d")
    # ★ 2026-10-18（Steam 2026-10-17 11:30 更新后真机）：配置对象仍在
    #   `globalgamemanagers.assets`（52174 / 52209 都在，该节点 65,949 对象 / 611 类），
    #   但**读字段时错位**：`clone.read_object` 在某颗对象的 `Frames` 字段上读出
    #   数组长度 1,088,421,888（把别的字段当成了 count）⇒ 新版类型树/字段布局与读器假设不符。
    #   ⛔ 不拿「反正能打开」凑绿：这条判的是**逐字节往返**，读错位了就不许说绿。
    #   缺口记在 STATUS；修它要先量新版那颗对象的类型树。
    try:
        probe = ui.read_container_config(data, 52174)
    except Exception as exc:  # —— 读不动就是缺口的现场，如实 skip
        pytest.skip(
            "1.2.1 起容器配置读不动（clone.read_object 在 Frames 处错位："
            f"{type(exc).__name__}: {str(exc)[:80]}）—— 待量新版类型树（见 STATUS 2026-10-18）"
        )
    assert probe.name == ui.CONFIG_NAME
    for pid, size in ((52174, 220_220), (52209, 212_504)):
        obj = ui.read_container_config(data, pid)
        assert obj.name == ui.CONFIG_NAME
        assert obj.size == size, "实例大小与 kb/30 §4 的表对不上"
        assert obj.unit_count == (250 if pid == 52174 else 249)
    tree = ui.read_container_config(data, 52174).tree
    assert tuple(i.item_id for i in ui.skin_items(tree, UNIT)) == REAL_IDS
    plan = ui.plan_update(data, 52174, unit_id=UNIT, item_id=REAL_IDS[0],
                          changes={"free": 1 - ui.item_of(tree, UNIT, REAL_IDS[0]).free})
    assert plan.delta == 0, "改一个既有字段的值必须是等长档"
    for pid in (52174, 52209):
        with pytest.raises(AssetError):
            ui.plan_remove(data, pid, unit_id=UNIT, item_id=888_888)



# --------------------------------------------------------------------------- #
# 6 · 补丁柜（task-17：⑥ 层的 range 反向 Δ —— 另起一个进程也撤得回来）
# --------------------------------------------------------------------------- #
def _add_plan(container: Path):
    return ui.plan_add(
        container,
        PID,
        unit_id=UNIT,
        item=ui.SkinItem(9999, "9 Probe", (), 1, 1, 0),
        reference_id=2232,
    )


def test_install_records_a_range_patch_when_a_store_is_given(
    container: Path, tmp_path: Path
) -> None:
    """给了补丁柜 ⇒ 这一笔落盘（range 口径：只有那一段的原始字节）。"""
    store = reverse_delta.PatchStore(tmp_path / "patches", game_root=tmp_path)
    out = tmp_path / "visible.unity3d"
    result = ui.install(_add_plan(container), out, patch_store=store)
    assert result.patch_path is not None and result.patch_path.is_file()
    assert result.patch_bytes > 0
    assert result.patch_lines and "可见性层新增" in result.patch_lines[0]
    patch = store.pending()
    assert patch is not None
    assert len(patch.targets) == 1
    target = patch.targets[0]
    assert isinstance(target, reverse_delta.RangeTarget)
    assert target.path_id == PID and target.node_path == ui.NODE_PATH
    assert target.old_size == len(ui.read_container_config(container, PID).payload)
    assert target.stored_bytes == target.old_size, "⛔ 不拷整包：存的就是那一段"
    # ⚠ 分母用**节点**，别用容器文件：合成件的填充对象一压就剩几百字节，
    #    拿它当分母会把「常数级元数据」误判成"补丁比容器还大"。
    with ufs.UfsReader(container) as reader:
        node_size = reader.node(ui.NODE_PATH).size
    assert target.stored_bytes < node_size // 10


def test_install_without_a_store_records_nothing(container: Path, tmp_path: Path) -> None:
    """不给补丁柜 ⇒ 老行为不变（产物照写，patch_path 为空 ⇒ 调用方自己负责退路）。"""
    out = tmp_path / "visible.unity3d"
    result = ui.install(_add_plan(container), out)
    assert out.is_file()
    assert result.patch_path is None and result.patch_bytes == 0
    assert result.patch_lines == ()
    assert not (tmp_path / "patches").exists()
