r"""VT 组指向判据：材质 `m_BuildTextureStacks` 那条链（`asset_kernel.vt_stack`）的四条自证 + 正控。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_vt_stack.py -q`
-> 收集用例数 **从 0（本文件之前不存在）变 N**，且全 passed。

| 用例 | 判据（task-2 的 J*） |
|---|---|
| J1 ① | 不改动的 typetree 往返**逐字节相同**；序列化不可复现的对象 ⇒ 抛（负控） |
| J1 ② | 改完**等长**；长度变了 ⇒ 抛（负控） |
| J1 ③ | 从**新字节读回**的 group / item == 新值 |
| J1 ④ | **新旧相同 ⇒ 抛错**（空改动不许写盘） |
| J2 | **正控**：group 里改**一个字符**（同长）⇒「读回 == 期望」那条断言**必须红** |
| J3 | `install` 走 `write_objects(..., patch_store=...)`：反向 Δ、**不传 backup_dir**（不拷整包） |
| 判据一行 | 契约：包名 / 材质 / 旧→新两个名字 / 改动字节数 / L1 等长 |
| 池子核对 | `pool_status` **只读**：就位 / 大小不符 / 缺 —— 三条必须分得开 |

## 为什么用离线夹具（而不是真件）

`build_stack_change` 要的三件事（`get_raw_data` / `read_typetree` / `save_typetree`）由夹具实现，
编码口径按 Unity 的字符串编码建模（`int32 长度 + 字节 + 4 字节对齐`）——
**这正是等长那条判据的形状**：32 位 hex 的名字改一个字仍然同长（对齐把 ±3 个字符吸收掉了）
⇒ **唯一能抓住"名字写错"的是 ③ 读回**，
所以 J2 的正控落在 ③ 上，不是落在"等长"上。

夹具的 `read_typetree_from_bytes` 就是 **③ 的接缝**：它**从字节解**（⛔ 不是把入参原样还回去）——
不这样的话 J2 那条正控是空转（AGENTS §0.9 动作 2：尺子必须先能红）。

⚠️ 真件那条（units 3.38 GB，只读干跑）**不在用例里**：重活一把锁（AGENTS §0 + §8.1），等 Lead 发话。
本文件**不写游戏目录**：`game_root` / `project_root` 全是 `tmp_path`。
"""

from __future__ import annotations

import argparse
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from ba_mod_maker.asset_kernel import reverse_delta, skin_install, vt_stack, writer
from ba_mod_maker.cli import vt as vt_cli
from ba_mod_maker.core.errors import AssetError

#: 真件读数（`_work/paint-design/vt-install-manifest.json`）：T-72B 现在指着自烘的**哑光组**。
GROUP_MATTE = "243ba7869bfa79d5ab794e9cc4a8329a"
ITEM_MATTE = "d81a0cdf4f975178e7379047107e318f"
#: 游戏自己的主组名（kb/18 §6.2 / §6.3，实测值）—— 当"改之前"的那一侧。
GROUP_GAME = "f51bbaf7a40a0ab4909c16ff519cc9cf"
#: item 那一侧**没读到完整真值** ⇒ 明写这是夹具值（⛔ 不编一个"看起来像"的真件值）。
ITEM_SYNTH = "0123456789abcdef0123456789abcdef"

MATERIAL_ID = -2100849908572284127
MATERIAL_NAME = "RU_T72B 1"
UNIT_KEY = "RU_T72B"

#: 夹具对象字节的头（真件这一段是 m_Name ＋ 一堆字段；这里只要一个固定长度的前缀）。
_HEADER = b"MATERIAL" + bytes(8)


# --------------------------------------------------------------------------- #
# 离线夹具：材质对象字节 = 头 + VT 栈表（字符串 4 字节对齐，与 Unity 同向）
# --------------------------------------------------------------------------- #
def _pack_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw + bytes((-(4 + len(raw))) % 4)


def _take_str(blob: bytes, pos: int) -> tuple[str, int]:
    (size,) = struct.unpack_from("<i", blob, pos)
    text = blob[pos + 4 : pos + 4 + size].decode("utf-8")
    return text, pos + 4 + size + (-(4 + size)) % 4


def _pack_stacks(entries: Any) -> bytes:
    out = bytearray(struct.pack("<i", len(entries)))
    for entry in entries:
        out += _pack_str(str(entry["groupName"])) + _pack_str(str(entry["itemName"]))
    return bytes(out)


def _unpack_stacks(blob: bytes, offset: int) -> list[dict[str, str]]:
    (count,) = struct.unpack_from("<i", blob, offset)
    pos = offset + 4
    out: list[dict[str, str]] = []
    for _ in range(count):
        group, pos = _take_str(blob, pos)
        item, pos = _take_str(blob, pos)
        out.append({"groupName": group, "itemName": item})
    assert pos == len(blob), "夹具布局与自己算的偏移对不上"
    return out


class _SyntheticMaterial:
    """`AssetSource.object_at` 返回的那种对象（只要 `build_stack_change` 用到的四个接口）。

    `reproducible=False` ⇒ `save_typetree` 每次给不同的字节（**J1① 的负控**）；
    `drops_changes=True` ⇒ 序列化**无视**传进来的树、照旧写原来的值（**J1③ 的正控**）。
    """

    def __init__(
        self,
        entries: list[dict[str, str]],
        *,
        name: str = MATERIAL_NAME,
        reproducible: bool = True,
        drops_changes: bool = False,
    ) -> None:
        self._entries = [dict(entry) for entry in entries]
        self.name = name
        self.reproducible = reproducible
        self.drops_changes = drops_changes

    def get_raw_data(self) -> bytes:
        return _HEADER + _pack_stacks(self._entries)

    def read_typetree(self) -> dict[str, Any]:
        return {
            "m_Name": self.name,
            "m_BuildTextureStacks": [dict(entry) for entry in self._entries],
        }

    def save_typetree(self, tree: Any) -> bytes:
        if not self.reproducible:
            return b"NOT-REPRODUCIBLE"
        entries = self._entries if self.drops_changes else list(tree["m_BuildTextureStacks"])
        return _HEADER + _pack_stacks(entries)

    def read_typetree_from_bytes(self, data: bytes) -> dict[str, Any]:
        """③ 的接缝：**从字节解**（⛔ 不是把入参还回去）。"""
        return {
            "m_Name": self.name,
            "m_BuildTextureStacks": _unpack_stacks(data, len(_HEADER)),
        }


class _SyntheticSource:
    """`AssetSource` 的最小替身（`object_at` ＋ `read_prefab_nodes` ＋ `read_typetree`）。

    `skin=(nodes, materials)` 时 `read_prefab_nodes` 就返回那一对 —— 用它造"skin_install
    能从皮肤字典里找到材质"的合成件（J1 那两条用例）。
    """

    def __init__(self, objects: dict[int, Any], *, skin: Any = None) -> None:
        self._objects = objects
        self._skin = skin

    def object_at(self, path_id: int) -> Any:
        return self._objects.get(int(path_id))

    def read_typetree(self, path_id: int) -> dict[str, Any]:
        return self._objects[int(path_id)].read_typetree()

    def read_prefab_nodes(self, asset_path: str) -> tuple[tuple[object, ...], dict[int, Any]]:
        if self._skin is not None:
            return self._skin
        return ((), {MATERIAL_ID: {"name": MATERIAL_NAME}})


def _entries(group: str = GROUP_GAME, item: str = ITEM_SYNTH) -> list[dict[str, str]]:
    return [{"groupName": group, "itemName": item}]


def _source(**kwargs: Any) -> _SyntheticSource:
    return _SyntheticSource({MATERIAL_ID: _SyntheticMaterial(_entries(), **kwargs)})


def _read_back(source: _SyntheticSource, payload: bytes) -> tuple[vt_stack.StackEntry, ...]:
    """用例自己走一遍 ③：从**新字节**读回来（夹具的接缝）。"""
    obj = source.object_at(MATERIAL_ID)
    return vt_stack.stack_entries(obj.read_typetree_from_bytes(payload))


def _one_char_off(text: str) -> str:
    """把最后一个字符换成另一个（**同长**）—— J2 正控要的就是这个。"""
    return text[:-1] + ("0" if text[-1] != "0" else "1")


# --------------------------------------------------------------------------- #
# 尺子正控：夹具自己的编解码
# --------------------------------------------------------------------------- #
def test_fixture_codec_round_trips_and_the_readback_decodes_bytes() -> None:
    """**尺子正控**：往返逐字节相同；读回是**从字节解出来的**（不是把入参还回来）。

    这两条不成立，后面「等长」「读回 == 新值」全是空转（AGENTS §0.9 动作 2）。
    """
    obj = _SyntheticMaterial(_entries())
    raw = obj.get_raw_data()
    assert obj.save_typetree(obj.read_typetree()) == raw
    assert _unpack_stacks(raw, len(_HEADER)) == _entries()
    # 正控的"能红"那一侧：把 group 改一个字符 ⇒ 解出来就是改过的那个
    broken = _HEADER + _pack_stacks(_entries(group=_one_char_off(GROUP_GAME)))
    assert _unpack_stacks(broken, len(_HEADER))[0]["groupName"] == _one_char_off(GROUP_GAME)
    assert len(broken) == len(raw), "一个字符之差必须仍然等长（对齐吸收）—— J2 的前提"


# --------------------------------------------------------------------------- #
# J1 ① ② ③
# --------------------------------------------------------------------------- #
def test_j1_untouched_roundtrip_is_byte_identical_and_the_change_is_equal_length() -> None:
    """J1①②：往返逐字节相同 ⇒ 改完**等长**（L1）；新字节确实与旧字节不同。"""
    source = _source()
    old = source.object_at(MATERIAL_ID).get_raw_data()
    change = vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE)
    assert len(change.payload) == len(old), "等长是这条链的前提（L1）"
    assert change.payload != old
    assert change.path_id == MATERIAL_ID
    assert change.type_name == "Material"
    assert change.name == MATERIAL_NAME, "名字缺省取 m_Name"
    # ② 只在两个名字上动：差异段必须落在栈表里、而且很短
    spans = skin_install.changed_spans(old, change.payload)
    assert spans and sum(end - lo for lo, end in spans) <= 70, f"不该改到别处：{spans}"


def test_j1_readback_from_the_new_bytes_equals_the_new_value() -> None:
    """J1③：从**新字节**读回来的 group / item == 新值（语义自证）。"""
    source = _source()
    change = vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE)
    assert _read_back(source, change.payload) == (vt_stack.StackEntry(GROUP_MATTE, ITEM_MATTE),)


def test_j1_readback_prefers_an_explicit_reader() -> None:
    """③ 的接缝顺序：显式给的 `read_tree` 优先，并且拿到的就是**新字节**。"""
    source = _source()
    seen: list[bytes] = []

    def reader(obj: Any, data: bytes) -> dict[str, Any]:
        seen.append(data)
        return {"m_Name": MATERIAL_NAME, "m_BuildTextureStacks": _unpack_stacks(data, len(_HEADER))}

    change = vt_stack.build_stack_change(
        source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE, read_tree=reader
    )
    assert seen == [change.payload]


# --------------------------------------------------------------------------- #
# J2 正控：那条「读回 == 期望」的断言必须能红
# --------------------------------------------------------------------------- #
def test_j2_ruler_can_go_red_group_changed_by_one_character() -> None:
    """J2 **正控**：把 group 改**一个字符**（同长）⇒「读回 == 期望」那条断言**必须红**。

    能红才算尺子（AGENTS §0.9 动作 2）。注意这条尺子量的**不是**长度 —— 一个字符之差仍然等长
    （对齐吸收），所以"等长"抓不住写错的名字，只有 ③ 抓得住。
    """
    source = _source()
    change = vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE)
    got = _read_back(source, change.payload)
    expected = vt_stack.StackEntry(group=GROUP_MATTE, item=ITEM_MATTE)
    assert got == (expected,)  # 写对了 ⇒ 绿
    one_off = vt_stack.StackEntry(group=_one_char_off(GROUP_MATTE), item=ITEM_MATTE)
    assert len(one_off.group) == len(GROUP_MATTE), "正控必须**等长**"
    assert one_off.group != GROUP_MATTE
    assert got != (one_off,), "改一个字符之后「读回 == 期望」这条断言必须红"


def test_j2_the_products_own_readback_check_goes_red() -> None:
    """J2 正控（产品那一侧）：序列化**无视**新值（照旧写原来的名字）⇒ ③ 必须抛。

    这是"尺子能红"的第二种喂法：不是把期望改错，而是让字节**真的**没有新值。
    """
    source = _source(drops_changes=True)
    with pytest.raises(AssetError, match="读回去"):
        vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE)


# --------------------------------------------------------------------------- #
# J1 ④ 与其它负控：不许写的东西
# --------------------------------------------------------------------------- #
def test_j1_the_same_value_is_rejected() -> None:
    """J1④：**新旧相同 = 空改动 ⇒ 抛错**（空改动不许写盘）；换成别的就绿。"""
    source = _source()
    with pytest.raises(AssetError, match="空改动"):
        vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_GAME, ITEM_SYNTH)
    assert vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE)


def test_j1_a_non_reproducible_object_is_rejected() -> None:
    """J1① 负控：typetree 往返不是逐字节相同 ⇒ 抛（等长覆盖会改坏别的字段）。"""
    source = _source(reproducible=False)
    with pytest.raises(AssetError, match="往返"):
        vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, ITEM_MATTE)


def test_j1_a_length_change_is_rejected() -> None:
    """J1② 负控：长度变了 ⇒ 抛（本单只做 L1；⛔ 不许硬写）。"""
    source = _source()
    with pytest.raises(AssetError, match="不是 L1"):
        vt_stack.build_stack_change(source, MATERIAL_ID, "a" * 40, ITEM_MATTE)


def test_unknown_path_id_goes_red() -> None:
    """负控：包里没有那个 pathID ⇒ 抛（⛔ 不拿相近的顶替）。"""
    with pytest.raises(AssetError, match="没有 pathID"):
        vt_stack.build_stack_change(_source(), MATERIAL_ID + 1, GROUP_MATTE, ITEM_MATTE)


def test_empty_names_are_rejected() -> None:
    """负控：group / item 空 ⇒ 抛（两个值都必须照编辑器烘出来的抄）。"""
    source = _source()
    with pytest.raises(AssetError, match="不许为空"):
        vt_stack.build_stack_change(source, MATERIAL_ID, "", ITEM_MATTE)
    with pytest.raises(AssetError, match="不许为空"):
        vt_stack.build_stack_change(source, MATERIAL_ID, GROUP_MATTE, "")


# --------------------------------------------------------------------------- #
# 纯函数：栈表怎么读、怎么改
# --------------------------------------------------------------------------- #
def test_set_stack_changes_only_the_two_fields() -> None:
    """纯树：`set_stack` 只改那两个字段，入参**不被改**，别的字段一个字不动。"""
    tree = {
        "m_Name": MATERIAL_NAME,
        "m_BuildTextureStacks": _entries(),
        "m_Shader": {"m_PathID": 7},
    }
    out = vt_stack.set_stack(tree, GROUP_MATTE, ITEM_MATTE)
    assert out["m_BuildTextureStacks"] == [{"groupName": GROUP_MATTE, "itemName": ITEM_MATTE}]
    assert out["m_Name"] == MATERIAL_NAME and out["m_Shader"] == {"m_PathID": 7}
    assert tree["m_BuildTextureStacks"] == _entries(), "入参不许被改"
    assert vt_stack.stack_entries(out) == (vt_stack.StackEntry(GROUP_MATTE, ITEM_MATTE),)


def test_stack_entries_reports_shape_problems_instead_of_an_empty_stack() -> None:
    """负控：读不出来就抛（⛔ **不许静默当空栈** —— 那等于没有 VT 也往下写）。"""
    with pytest.raises(AssetError, match="没有 m_BuildTextureStacks"):
        vt_stack.stack_entries({"m_Name": "x"})
    with pytest.raises(AssetError, match="不是序列"):
        vt_stack.stack_entries({"m_BuildTextureStacks": "243ba786"})
    with pytest.raises(AssetError, match="不是 Mapping"):
        vt_stack.stack_entries({"m_BuildTextureStacks": ["243ba786"]})
    with pytest.raises(AssetError, match="不是字符串"):
        vt_stack.stack_entries({"m_BuildTextureStacks": [{"groupName": 1, "itemName": 2}]})
    assert vt_stack.stack_entries({"m_BuildTextureStacks": []}) == ()


def test_set_stack_requires_exactly_one_entry() -> None:
    """负控：栈里 0 项 / 2 项 ⇒ 抛（项数一变长度必变，不是等长档）。

    真件是**一项**：研究件把整表换成一项之后**等长**，这正是本模块只改一项的依据。
    """
    for entries in ([], _entries() + _entries()):
        with pytest.raises(AssetError, match="恰好一项"):
            vt_stack.set_stack({"m_BuildTextureStacks": entries}, GROUP_MATTE, ITEM_MATTE)


# --------------------------------------------------------------------------- #
# 判据一行（Lead 的 CLI 就打印这些）
# --------------------------------------------------------------------------- #
def _fake_writeback(tmp_path: Path, *, old_bytes: int, stream_start: int) -> writer.WritebackPlan:
    row = writer.PlanRow(
        path_id=MATERIAL_ID,
        name=MATERIAL_NAME,
        type_name="Material",
        old_bytes=old_bytes,
        new_bytes=old_bytes,
        level=writer.LEVEL_EQUAL,
        block_first=2812,
        block_last=2812,
        stream_start=stream_start,
    )
    return writer.WritebackPlan(
        bundle=tmp_path / "units",
        bundle_name="units",
        rows=(row,),
        block_runs=((2812, 2812),),
        hit_blocks=1,
        eta_seconds=3.0,
        old_file_size=3_380_000_000,
        new_file_size=3_380_000_000,
        disk_crc=2_918_958_848,
        catalog_bundle_name="units",
        catalog_crc_old=2_918_958_848,
        catalog_crc_new=1_234_567_890,
        catalog_size_old=3_380_000_000,
        catalog_extra_offset=0,
    )


def _fake_plan(tmp_path: Path, *, material_bytes: int = 4844, stream_start: int = 938_569_800):
    return vt_stack.VtStackPlan(
        game_root=tmp_path,
        unit_key=UNIT_KEY,
        bundle=tmp_path / "units",
        bundle_name="units",
        catalog_path=tmp_path / "catalog.json",
        material_name=MATERIAL_NAME,
        material_path_id=MATERIAL_ID,
        material_bytes=material_bytes,
        old_group=GROUP_GAME,
        old_item=ITEM_SYNTH,
        new_group=GROUP_MATTE,
        new_item=ITEM_MATTE,
        change=writer.ObjectChange(
            path_id=MATERIAL_ID, payload=b"x" * material_bytes, name=MATERIAL_NAME,
            type_name="Material",
        ),
        writeback=_fake_writeback(tmp_path, old_bytes=material_bytes, stream_start=stream_start),
        changed_spans=((56, 120),),
    )


def test_criterion_lines_carry_the_two_names_and_the_equal_length(tmp_path: Path) -> None:
    """判据一行：包名 / 材质 / 旧→新两个名字 / 改动字节数 / L1 等长（＋ writer 那几行）。"""
    plan = _fake_plan(tmp_path)
    text = "\n".join(plan.criterion_lines())
    for needle in ("units", UNIT_KEY, MATERIAL_NAME, GROUP_GAME, GROUP_MATTE, ITEM_MATTE):
        assert needle in text, f"判据一行里缺 {needle!r}：\n{text}"
    assert "改动 64 字节" in text
    assert "L1" in text and "等长" in text
    assert plan.changed_bytes == 64
    assert plan.absolute_spans() == ((938_569_856, 938_569_920),)
    assert GROUP_MATTE[:8] in plan.summary()


# --------------------------------------------------------------------------- #
# 计划与安装的接线（J3）
# --------------------------------------------------------------------------- #
class _FakeOpenUnit:
    """`skin_install._open_unit` 的替身：只要 `source` / `bundle` / `catalog` 那几项。"""

    def __init__(self, source: Any, tmp_path: Path) -> None:
        self.source = source
        self.asset_path = "Assets/Units/RU_T72B.prefab"
        self.bundle = tmp_path / "units"
        self.bundle_name = "units"
        self.catalog = tmp_path / "catalog.json"


def _patch_unit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, plan_source: Any = None, **kwargs: Any
) -> None:
    source = plan_source if plan_source is not None else _source(**kwargs)
    unit = _FakeOpenUnit(source, tmp_path)

    import contextlib

    monkeypatch.setattr(skin_install, "_open_unit", lambda *a, **k: contextlib.nullcontext(unit))


def test_plan_install_wires_unit_to_material_and_asks_for_a_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """干跑那一条：unit_key -> 材质 -> 四条自证 -> `write_objects(dry_run=True)`（⛔ 不写盘）。"""
    _patch_unit(monkeypatch, tmp_path)
    seen: dict[str, Any] = {}

    def fake_write_objects(bundle: Any, changes: Any, **kwargs: Any) -> Any:
        seen["bundle"] = bundle
        seen["changes"] = list(changes)
        seen.update(kwargs)
        return writer.WriteReport(
            bundle=tmp_path / "units",
            bundle_name="units",
            dry_run=True,
            plan=_fake_writeback(tmp_path, old_bytes=4844, stream_start=938_569_800),
        )

    monkeypatch.setattr(vt_stack.writer, "write_objects", fake_write_objects)
    plan = vt_stack.plan_install(
        tmp_path, UNIT_KEY, material_name=MATERIAL_NAME, group=GROUP_MATTE, item=ITEM_MATTE
    )
    assert plan.old_group == GROUP_GAME and plan.old_item == ITEM_SYNTH
    assert plan.new_group == GROUP_MATTE and plan.new_item == ITEM_MATTE
    assert plan.material_path_id == MATERIAL_ID
    assert plan.material_bytes == len(_source().object_at(MATERIAL_ID).get_raw_data())
    assert plan.changed_spans, "两个 32 位名字必然有差异段"
    assert seen["dry_run"] is True, "计划这一趟必须是干跑"
    assert "patch_store" not in seen, "计划阶段不许建补丁"
    assert seen["changes"] == [plan.change]


def test_install_hands_the_patch_store_and_the_label_to_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J3：真装走**反向 Δ**（`patch_store` ＋ `patch_label`）。

    ⛔ **不传 backup_dir** —— 那才是"不拷整包"的机械证据。
    """
    plan = _fake_plan(tmp_path)
    seen: dict[str, Any] = {}

    def fake_write_objects(bundle: Any, changes: Any, **kwargs: Any) -> Any:
        seen["bundle"] = bundle
        seen["changes"] = list(changes)
        seen.update(kwargs)
        return "REPORT"

    monkeypatch.setattr(vt_stack.writer, "write_objects", fake_write_objects)
    out = vt_stack.install(plan, project_root=tmp_path / "proj")
    assert out == "REPORT"
    assert seen["bundle"] == plan.bundle
    assert seen["changes"] == [plan.change]
    assert seen["dry_run"] is False
    assert seen["game_root"] == plan.game_root
    assert seen["catalog_path"] == plan.catalog_path
    assert isinstance(seen["patch_store"], reverse_delta.PatchStore)
    assert seen["patch_store"].game_root == plan.game_root, "补丁柜必须跟随游戏所在盘"
    assert str(seen["patch_label"]).startswith("VT 组指向：")
    assert f"{GROUP_GAME}/{ITEM_SYNTH} → {GROUP_MATTE}/{ITEM_MATTE}" in str(seen["patch_label"])
    assert "backup_dir" not in seen, "⛔ 不许拷整包当备份（反向 Δ 取代它）"
    assert not (tmp_path / "proj" / "patches").exists(), "计划阶段不许建补丁目录"

    vt_stack.install(plan, project_root=tmp_path / "proj", dry_run=True)
    assert seen["dry_run"] is True
    assert not (tmp_path / "proj" / "patches").exists()


# --------------------------------------------------------------------------- #
# 池子就位核对（**只读**）
# --------------------------------------------------------------------------- #
def test_pool_status_separates_ready_mismatched_and_missing(tmp_path: Path) -> None:
    """池子核对分成三档：就位 / 大小不符 / 缺 —— 三档必须分得开（⛔ 缺不许当就位）。"""
    pool = tmp_path / "vt-matte-out"
    pool.mkdir()
    (pool / "g.gts").write_bytes(b"x" * 16)
    (pool / "g0_1.gtp").write_bytes(b"y" * 8)
    (pool / "g2.gtp").write_bytes(b"z")
    streaming = tmp_path / "StreamingAssets"
    streaming.mkdir()
    (streaming / "g.gts").write_bytes(b"x" * 16)
    (streaming / "g0_1.gtp").write_bytes(b"y" * 4)

    by_name = {entry.name: entry for entry in vt_stack.pool_status(pool, streaming)}
    assert by_name["g.gts"].ok and by_name["g.gts"].state == "就位"
    assert not by_name["g0_1.gtp"].ok and "大小不符" in by_name["g0_1.gtp"].state
    assert by_name["g2.gtp"].installed_size is None and by_name["g2.gtp"].state == "缺"
    assert [entry.name for entry in vt_stack.pool_status(pool, streaming)] == [
        "g.gts",
        "g0_1.gtp",
        "g2.gtp",
    ]


def test_pool_status_refuses_a_missing_or_empty_directory(tmp_path: Path) -> None:
    """负控：目录不在 / 空目录 ⇒ 抛（⛔ 不许当"全部就位"）。"""
    with pytest.raises(AssetError, match="不在了"):
        vt_stack.pool_status(tmp_path / "nope", tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AssetError, match="一个文件都没有"):
        vt_stack.pool_status(empty, tmp_path)
    assert vt_stack.streaming_dir_of("G") == Path("G") / "BrokenArrow_Data" / "StreamingAssets"


# --------------------------------------------------------------------------- #
# 薄 CLI 的参数契约（默认干跑）
# --------------------------------------------------------------------------- #
def test_j3_the_cli_defaults_to_a_dry_run() -> None:
    """J3：`vt` 的参数面 —— **默认干跑**（`--apply` 才写），并且两个名字是必填。"""
    top = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = top.add_subparsers(dest="command")
    vt_cli.add_parser(sub)
    args = top.parse_args(
        [
            "vt",
            UNIT_KEY,
            "--material",
            MATERIAL_NAME,
            "--group",
            GROUP_MATTE,
            "--item",
            ITEM_MATTE,
        ]
    )
    assert args.command == "vt"
    assert args.apply is False, "默认必须干跑（一个字节不写）"
    assert args.dry_run is False
    assert args.unit == UNIT_KEY and args.material == MATERIAL_NAME
    assert args.group == GROUP_MATTE and args.item == ITEM_MATTE
    with pytest.raises(SystemExit):
        top.parse_args(["vt", UNIT_KEY])


def test_j3_the_cli_reports_its_own_exit_codes() -> None:
    """契约：三个退出码的**名字与数**（Lead 的报告与用例都按它读）。"""
    assert (vt_cli.EXIT_OK, vt_cli.EXIT_RED, vt_cli.EXIT_UNUSABLE) == (0, 1, 2)

# --------------------------------------------------------------------------- #
# J1（2026-10-11）：**皮肤字典里那几颗材质**也要能改，并如实印出来路与同族
# --------------------------------------------------------------------------- #
#: 真件读数（`_work/audit-B-assets.md` §4.1 / §6.3）：T-72B **底盘** prefab 上的
#: UnitPrefabRoot（pid 1033192610857091880）的 `Skins.Data` 用 `RU_T72B 1..5` ——
#: `Data[0]` 指向 prefab 自己也引用的 `RU_T72B 1`，`Data[1]` 指向 7596694440997434446。
SKIN_ID = 7596694440997434446
SKIN_NAME = "RU_T72B 2"
SKIN_ROOT_ID = 1033192610857091880
RENDERER_IDS = (101, 102, 103)


class _SyntheticRoot:
    """UnitPrefabRoot 的替身：只要 `read_typetree` 给出一棵带 `Skins` 的树。"""

    def __init__(self, tree: dict[str, Any]) -> None:
        self._tree = tree

    def read_typetree(self) -> dict[str, Any]:
        return self._tree


def _skin_tree(
    pids: Sequence[int], renderers: Sequence[int] = RENDERER_IDS
) -> dict[str, Any]:
    """一棵最小的 UnitPrefabRoot 树。

    形状照真件：`_keyData` 是材质 PPtr，`_valueData[i].Renderers` 是它管的渲染器。
    """
    data = [
        {
            "Id": 2232 + index,
            "Data": {
                "_keyData": [{"m_FileID": 0, "m_PathID": pid}],
                "_valueData": [
                    {"Renderers": [{"m_FileID": 0, "m_PathID": rid} for rid in renderers]}
                ],
            },
        }
        for index, pid in enumerate(pids)
    ]
    return {"PrefabRoot": {"m_FileID": 0, "m_PathID": 1}, "Skins": {"Data": data}}


def _skin_source(pids: Sequence[int] = (MATERIAL_ID, SKIN_ID)) -> _SyntheticSource:
    """带皮肤字典的合成源：prefab 渲染器**只直接引用** `MATERIAL_ID`，字典里有两颗。"""
    objects: dict[int, Any] = {
        MATERIAL_ID: _SyntheticMaterial(_entries()),
        SKIN_ID: _SyntheticMaterial(_entries(), name=SKIN_NAME),
        SKIN_ROOT_ID: _SyntheticRoot(_skin_tree(pids)),
    }
    nodes = (
        {
            "name": "RU_T72B_Base",
            "path_id": 7,
            "components": (
                {"kind": "MonoBehaviour", "path_id": SKIN_ROOT_ID},
                {"kind": "MeshRenderer", "path_id": 11},
            ),
        },
    )
    return _SyntheticSource(objects, skin=(nodes, {MATERIAL_ID: {"name": MATERIAL_NAME}}))


def _stub_write_objects(tmp_path: Path) -> Any:
    """把 `vt_stack.writer.write_objects` 换成干跑桩（⛔ 一个字节都不写）。"""

    def fake(bundle: Any, changes: Any, **kwargs: Any) -> Any:
        return writer.WriteReport(
            bundle=tmp_path / "units",
            bundle_name="units",
            dry_run=True,
            plan=_fake_writeback(tmp_path, old_bytes=4844, stream_start=938_569_800),
        )

    return fake


def test_j1_a_material_only_in_the_skin_dictionary_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J1 正控：**只在皮肤字典里**的那颗（prefab 没直接引用）能找到，并印出来路 + 同族。"""
    _patch_unit(monkeypatch, tmp_path, plan_source=_skin_source())
    monkeypatch.setattr(vt_stack.writer, "write_objects", _stub_write_objects(tmp_path))
    plan = vt_stack.plan_install(
        tmp_path, UNIT_KEY, material_name=SKIN_NAME, group=GROUP_MATTE, item=ITEM_MATTE
    )
    assert plan.material_path_id == SKIN_ID
    assert plan.material_sources == ("皮肤字典 Skins.Data[1].Data._keyData[0]",)
    assert plan.material_kin == (MATERIAL_NAME,)
    text = "\n".join(plan.criterion_lines())
    assert "材质来路：皮肤字典 Skins.Data[1].Data._keyData[0]" in text
    assert "同族 1 颗：RU_T72B 1" in text


def test_j1_the_prefab_material_reports_its_skin_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正控的另一面：prefab 那颗自己也印出来路 ＋ 同族 —— 用户就是靠这一行知道"切涂装会顶掉"。"""
    _patch_unit(monkeypatch, tmp_path, plan_source=_skin_source())
    monkeypatch.setattr(vt_stack.writer, "write_objects", _stub_write_objects(tmp_path))
    plan = vt_stack.plan_install(
        tmp_path, UNIT_KEY, material_name=MATERIAL_NAME, group=GROUP_MATTE, item=ITEM_MATTE
    )
    assert plan.material_path_id == MATERIAL_ID
    assert plan.material_sources == (
        "prefab 渲染器",
        "皮肤字典 Skins.Data[0].Data._keyData[0]",
    )
    assert plan.material_kin == (SKIN_NAME,)
    text = "\n".join(plan.criterion_lines())
    assert "prefab 渲染器" in text and "同族 1 颗：RU_T72B 2" in text


def test_j1_a_name_in_neither_batch_is_refused_with_the_available_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J1 负控：prefab 与皮肤字典**两批都没有**这颗 ⇒ 拒绝，并把可用名（含字典里那颗）列出来。"""
    _patch_unit(monkeypatch, tmp_path, plan_source=_skin_source())
    with pytest.raises(AssetError) as excinfo:
        vt_stack.plan_install(
            tmp_path, UNIT_KEY, material_name="RU_T72B 9", group=GROUP_MATTE, item=ITEM_MATTE
        )
    message = excinfo.value.message
    assert "两批都找过" in message
    assert MATERIAL_NAME in message and SKIN_NAME in message


def test_j1_an_empty_skin_dictionary_changes_nothing(tmp_path: Path, monkeypatch) -> None:
    """负控：字典是空的（37 个真件实例就长这样）⇒ 行为与从前逐字相同，不做任何猜测。"""
    objects: dict[int, Any] = {
        MATERIAL_ID: _SyntheticMaterial(_entries()),
        SKIN_ROOT_ID: _SyntheticRoot({"PrefabRoot": {}, "Skins": {"Data": []}}),
    }
    source = _SyntheticSource(
        objects,
        skin=(
            ({"name": "x", "components": ({"kind": "MonoBehaviour", "path_id": SKIN_ROOT_ID},)},),
            {MATERIAL_ID: {"name": MATERIAL_NAME}},
        ),
    )
    _patch_unit(monkeypatch, tmp_path, plan_source=source)
    monkeypatch.setattr(vt_stack.writer, "write_objects", _stub_write_objects(tmp_path))
    plan = vt_stack.plan_install(
        tmp_path, UNIT_KEY, material_name=MATERIAL_NAME, group=GROUP_MATTE, item=ITEM_MATTE
    )
    assert plan.material_sources == ("prefab 渲染器",)
    assert plan.material_kin == ()
    assert "同族 0 颗" in "\n".join(plan.criterion_lines())

    top = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = top.add_subparsers(dest="command")
    parser = vt_cli.add_parser(sub)
    help_text = parser.format_help()
    assert "默认干跑" in help_text
    for code in ("0", "1", "2"):
        assert code in help_text
