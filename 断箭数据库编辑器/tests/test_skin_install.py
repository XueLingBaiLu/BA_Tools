r"""皮肤线判据：材质槽换图那条链（asset_kernel.skin_install）的纯函数 + 负控。

**判据一行**：.venv\Scripts\python.exe -m pytest tests/test_skin_install.py -q
-> 收集用例数 **从 0（本文件之前不存在）变 N**，且全 passed。

| 用例 | 判据 |
|---|---|
| 编解码往返 | **尺子正控**：往返逐字节相同 + 偏移助手指的就是 pathID（改它必须报出来） |
| 换槽字节 | 新字节**等长**、差异**只有 8 字节**、别的字段与入参一个字不动 |
| 换槽纯树 | m_FileID 不传就原样保留；别的槽 / 浮点 / 名字 / shader 不动 |
| 空改动 | 负控：新旧相同 ⇒ AssetError（空改动不许写盘）；换别的就绿 |
| 槽名不存在 | 负控：AssetError 且**印出可用槽名**（⛔ 不许静默用默认值） |
| 空 PPtr | 负控：槽在但 PPtr 是空的（pathID=0）⇒ AssetError |
| 长度变了 | 负控：AssetError（等长这条判据在函数里拦死） |
| 贴图名 | 负控：不存在 ⇒ 印出可用名；重名 ⇒ 拒绝；空名单 ⇒ 拒绝 |
| 差异段助手 | 相邻段要并、无差异给空、长度不等要红 |
| 材质名 | 负控：不在 ⇒ 印出可用名；重名 ⇒ 拒绝 |
| 判据一行 | 契约（Lead 的 CLI 要打印）：包名 / 材质 / 槽 / 旧→新 / **8 字节** / L1 |
| install 接线 | 契约：走反向 Δ（patch_store + patch_label）、干跑不建补丁目录 |

⚠ 真件那条（units 3.38 GB，只读干跑）**不在用例里**：重活一把锁（AGENTS §0 + §8.1），
由 Lead 统一发话再跑 —— 这里只放能在 tmp_path 上秒级复算的判据。
"""

from __future__ import annotations

import copy
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import reverse_delta, skin_install, writer
from ba_mod_maker.core.errors import AssetError

#: 真机读数里的那一对（研究-模型与动画/18-皮肤线/05-写侧-真机结论.md §1）：
#: RU_BMD-2 1 的 Layer_A97CDC25 槽 BMD-2_1_BaseMap -> BMD-2_3_BaseMap（8 字节）。
TEX_OLD = -8116562946395082289
TEX_NEW = -2698481110755143032
NAME = "RU_BMD-2 1"
SLOT_A = "Layer_A97CDC25"
SLOT_B = "Layer_B"
MATERIAL_ID = -2100849908572284127
FLOATS = (("_Metallic", 0.25), ("_Smoothness", 0.5))
SHADER = (0, 7)
#: 槽 0 的 m_FileID = 3：用来证明「file_id 不传就原样保留」。
SLOTS = ((SLOT_A, 3, TEX_OLD), (SLOT_B, 0, -12345))
INVENTORY = {"BMD-2_1_BaseMap": (TEX_OLD,), "BMD-2_3_BaseMap": (TEX_NEW,)}


# --------------------------------------------------------------------------- #
# 合成材质：定长编解码（形状就是 UnityPy typetree 的那几个字段）
#
# 布局（对齐与真件同向：每个串 4 字节对齐）：
#     m_Name(i32+串+pad4) | m_Shader(i32 fileID + i64 pathID) | 浮点个数(i32)
#     每个浮点：串(i32+串+pad4) + f32          | 槽个数(i32)
#     每个槽：  串(i32+串+pad4) + i32 fileID + i64 pathID
# --------------------------------------------------------------------------- #
def _pack_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw + bytes((-(4 + len(raw))) % 4)


def _pack_material(
    *,
    name: str = NAME,
    shader: tuple[int, int] = SHADER,
    floats: tuple[tuple[str, float], ...] = FLOATS,
    slots: tuple[tuple[str, int, int], ...] = SLOTS,
) -> bytes:
    out = bytearray(_pack_str(name))
    out += struct.pack("<iq", shader[0], shader[1])
    out += struct.pack("<i", len(floats))
    for key, value in floats:
        out += _pack_str(key) + struct.pack("<f", value)
    out += struct.pack("<i", len(slots))
    for slot, file_id, path_id in slots:
        out += _pack_str(slot) + struct.pack("<iq", file_id, path_id)
    return bytes(out)


def _slot_path_id_offset(
    index: int,
    *,
    name: str = NAME,
    shader: tuple[int, int] = SHADER,
    floats: tuple[tuple[str, float], ...] = FLOATS,
    slots: tuple[tuple[str, int, int], ...] = SLOTS,
) -> int:
    """第 index 个槽的 m_PathID 在对象字节里的**相对偏移**（用例自己算，⛔ 不借产品代码）。"""
    offset = len(_pack_str(name)) + 12 + 4
    for key, _value in floats:
        offset += len(_pack_str(key)) + 4
    offset += 4
    for i, (slot, _file_id, _path_id) in enumerate(slots):
        offset += len(_pack_str(slot))
        if i == index:
            return offset + 4
        offset += 12
    raise AssertionError(f"夹具里没有第 {index} 个槽")


def _unpack_material(data: bytes) -> dict[str, object]:
    """字节 -> typetree 形状的 dict（UnityPy read_typetree 的那几个字段）。"""
    pos = 0

    def take_str() -> str:
        nonlocal pos
        (size,) = struct.unpack_from("<i", data, pos)
        text = data[pos + 4 : pos + 4 + size].decode("utf-8")
        pos += 4 + size + (-(4 + size)) % 4
        return text

    def take_pptr() -> dict[str, int]:
        nonlocal pos
        file_id, path_id = struct.unpack_from("<iq", data, pos)
        pos += 12
        return {"m_FileID": file_id, "m_PathID": path_id}

    name = take_str()
    shader = take_pptr()
    (float_count,) = struct.unpack_from("<i", data, pos)
    pos += 4
    floats: list[list[object]] = []
    for _ in range(float_count):
        key = take_str()
        (value,) = struct.unpack_from("<f", data, pos)
        pos += 4
        floats.append([key, value])
    (slot_count,) = struct.unpack_from("<i", data, pos)
    pos += 4
    envs: list[list[object]] = []
    for _ in range(slot_count):
        slot = take_str()
        envs.append([slot, {"m_Texture": take_pptr()}])
    assert pos == len(data), "夹具布局与自己算的偏移对不上"
    return {
        "m_Name": name,
        "m_Shader": shader,
        "m_SavedProperties": {"m_Floats": floats, "m_Colors": [], "m_TexEnvs": envs},
    }


def _save_material(tree: dict[str, object]) -> bytes:
    """dict -> 字节（换槽那个纯函数的序列化器；与 _pack_material 同一个布局）。"""
    props = tree["m_SavedProperties"]
    assert isinstance(props, dict)
    shader = tree["m_Shader"]
    assert isinstance(shader, dict)
    return _pack_material(
        name=str(tree["m_Name"]),
        shader=(int(shader["m_FileID"]), int(shader["m_PathID"])),
        floats=tuple((str(k), float(v)) for k, v in props["m_Floats"]),
        slots=tuple(
            (
                str(entry[0]),
                int(entry[1]["m_Texture"]["m_FileID"]),
                int(entry[1]["m_Texture"]["m_PathID"]),
            )
            for entry in props["m_TexEnvs"]
        ),
    )


# --------------------------------------------------------------------------- #
# 尺子正控：编解码往返 + 偏移助手
# --------------------------------------------------------------------------- #
def test_synthetic_material_codec_round_trips() -> None:
    """**尺子正控**：往返逐字节相同；偏移助手指的就是那个 pathID；改它必须被报出来。

    这三条不成立的话，「差异只有 8 字节」那类断言全是空转（AGENTS §0.9 动作 2）。
    """
    raw = _pack_material()
    assert _save_material(_unpack_material(raw)) == raw
    offset = _slot_path_id_offset(0)
    assert struct.unpack_from("<q", raw, offset)[0] == TEX_OLD
    assert struct.unpack_from("<i", raw, offset - 4)[0] == 3, "m_FileID 就在 pathID 前面 4 字节"
    # 负控（尺子能红）：把那 8 个字节改掉 ⇒ 差异段助手必须报出这一段
    broken = raw[:offset] + struct.pack("<q", TEX_NEW) + raw[offset + 8 :]
    assert skin_install.changed_spans(raw, broken) == ((offset, offset + 8),)


# --------------------------------------------------------------------------- #
# 纯函数：树 / PPtr / 字节
# --------------------------------------------------------------------------- #
def test_swapped_material_bytes_changes_exactly_eight_bytes() -> None:
    """换槽 ⇒ **等长**新字节，差异**只有 8 字节**（全落在 m_PathID 上），别的字段一个字不动。"""
    raw = _pack_material()
    tree = _unpack_material(raw)
    frozen = copy.deepcopy(tree)
    offset = _slot_path_id_offset(0)

    new = skin_install.swapped_material_bytes(
        tree, SLOT_A, path_id=TEX_NEW, save=_save_material, old_size=len(raw)
    )

    assert len(new) == len(raw), "等长是这条链的前提（L1）"
    assert new != raw
    assert skin_install.changed_spans(raw, new) == ((offset, offset + 8),)
    back = _unpack_material(new)
    props = back["m_SavedProperties"]
    assert props["m_TexEnvs"][0][1]["m_Texture"] == {"m_FileID": 3, "m_PathID": TEX_NEW}
    # 别的字段一个字不动
    assert props["m_TexEnvs"][1] == tree["m_SavedProperties"]["m_TexEnvs"][1]
    assert props["m_Floats"] == tree["m_SavedProperties"]["m_Floats"]
    assert back["m_Name"] == tree["m_Name"]
    assert back["m_Shader"] == tree["m_Shader"]
    # 入参不被改
    assert tree == frozen


def test_swap_slot_texture_keeps_file_id_and_other_slots() -> None:
    """纯树那一层：m_FileID **不传就原样保留**（少改字节）；别的槽 / 名字 / shader 不动。"""
    tree = _unpack_material(_pack_material())
    frozen = copy.deepcopy(tree)

    out = skin_install.swap_slot_texture(tree, SLOT_A, path_id=TEX_NEW)
    props = out["m_SavedProperties"]
    assert props["m_TexEnvs"][0][1]["m_Texture"] == {"m_FileID": 3, "m_PathID": TEX_NEW}
    assert props["m_TexEnvs"][1] == tree["m_SavedProperties"]["m_TexEnvs"][1]
    assert props["m_Floats"] == tree["m_SavedProperties"]["m_Floats"]
    assert out["m_Name"] == tree["m_Name"]
    assert out["m_Shader"] == tree["m_Shader"]
    assert tree == frozen, "入参不许被改"
    assert skin_install.slot_texture_pptr(out, SLOT_A)["m_PathID"] == TEX_NEW

    # file_id 显式给了才动
    out2 = skin_install.swap_slot_texture(tree, SLOT_A, path_id=TEX_NEW, file_id=0)
    assert out2["m_SavedProperties"]["m_TexEnvs"][0][1]["m_Texture"] == {
        "m_FileID": 0,
        "m_PathID": TEX_NEW,
    }


def test_swapped_pptr_refuses_a_noop_swap() -> None:
    """负控：新旧相同 ⇒ **空改动**，拒绝（空改动不该写盘）；正控换别的就绿。"""
    pptr = {"m_FileID": 3, "m_PathID": TEX_OLD}
    with pytest.raises(AssetError, match="空改动"):
        skin_install.swapped_pptr(pptr, path_id=TEX_OLD)
    assert skin_install.swapped_pptr(pptr, path_id=TEX_NEW)["m_PathID"] == TEX_NEW
    assert pptr == {"m_FileID": 3, "m_PathID": TEX_OLD}, "入参不许被改"


def test_swapped_material_bytes_refuses_non_equal_length() -> None:
    """负控：**长度变了**不许硬写（等长这条判据在函数里拦死，不是靠调用方自觉）。"""
    raw = _pack_material()
    tree = _unpack_material(raw)
    with pytest.raises(AssetError, match="不是等长"):
        skin_install.swapped_material_bytes(
            tree, SLOT_A, path_id=TEX_NEW, save=_save_material, old_size=len(raw) + 1
        )
    # 真的会变长的那种：材质多一个浮点 ⇒ 序列化出来更长
    bigger = _unpack_material(_pack_material(floats=(*FLOATS, ("_Extra", 1.0))))
    with pytest.raises(AssetError, match="不是等长"):
        skin_install.swapped_material_bytes(
            bigger, SLOT_A, path_id=TEX_NEW, save=_save_material, old_size=len(raw)
        )


# --------------------------------------------------------------------------- #
# 负控：名字不许猜（槽 / 贴图 / 材质）
# --------------------------------------------------------------------------- #
def test_unknown_slot_goes_red_with_the_available_names() -> None:
    """槽名不存在 ⇒ AssetError 且**印出可用槽名**（⛔ 不许静默用默认值 / 第一个槽顶替）。"""
    tree = _unpack_material(_pack_material())
    with pytest.raises(AssetError) as err:
        skin_install.slot_texture_pptr(tree, "Layer_NOT_THERE")
    message = str(err.value)
    assert "Layer_NOT_THERE" in message
    assert SLOT_A in message and SLOT_B in message, "负控必须说清可用值"
    assert err.value.hint
    with pytest.raises(AssetError, match="没有贴图槽"):
        skin_install.swap_slot_texture(tree, "Layer_NOT_THERE", path_id=TEX_NEW)


def test_empty_slot_pptr_is_rejected() -> None:
    """负控：槽在但 PPtr 是空的（pathID = 0）⇒ 拒绝（拿 0 当 pathID 会改错对象）。"""
    tree = _unpack_material(_pack_material(slots=((SLOT_A, 0, 0), (SLOT_B, 0, -1))))
    with pytest.raises(AssetError, match="PPtr 是空的"):
        skin_install.slot_texture_pptr(tree, SLOT_A)


def test_resolve_texture_finds_the_target_and_reports_the_available_names() -> None:
    """贴图名找不到 ⇒ AssetError 且印出可用名（⛔ 不许拿最像的那一个顶替）。"""
    assert skin_install.resolve_texture(INVENTORY, "BMD-2_3_BaseMap") == TEX_NEW
    with pytest.raises(AssetError) as err:
        skin_install.resolve_texture(INVENTORY, "BMD-2_9_BaseMap")
    message = str(err.value)
    assert "BMD-2_9_BaseMap" in message
    assert "BMD-2_1_BaseMap" in message and "BMD-2_3_BaseMap" in message
    with pytest.raises(AssetError, match="没有贴图"):
        skin_install.resolve_texture({}, "whatever")


def test_resolve_texture_refuses_a_duplicated_name() -> None:
    """重名 ⇒ 拒绝（一个名字命中两个 pathID 时不许猜是哪一个）。"""
    dup = {"BMD-2_3_BaseMap": (TEX_NEW, TEX_NEW + 1)}
    with pytest.raises(AssetError, match="命中 2"):
        skin_install.resolve_texture(dup, "BMD-2_3_BaseMap")


def test_find_material_reports_available_and_refuses_duplicates() -> None:
    """材质名找不到 / 重名都拒绝，可用值印在消息里。"""
    materials = {11: {"name": NAME}, 22: {"name": SLOT_B}}
    assert skin_install._find_material(materials, NAME, "RU_BMD-2") == 11
    with pytest.raises(AssetError) as err:
        skin_install._find_material(materials, "RU_BMD-2 9", "RU_BMD-2")
    assert NAME in str(err.value) and SLOT_B in str(err.value)
    with pytest.raises(AssetError, match="命中 2"):
        skin_install._find_material({11: {"name": NAME}, 12: {"name": NAME}}, NAME, "RU_BMD-2")


def test_changed_spans_merges_runs_and_requires_equal_length() -> None:
    """差异段助手：相邻段要并、无差异给空、长度不等要红。"""
    assert skin_install.changed_spans(b"abcdef", b"abcdef") == ()
    assert skin_install.changed_spans(b"abcdef", b"abXdeY") == ((2, 3), (5, 6))
    assert skin_install.changed_spans(b"abcdef", b"aXXXXf") == ((1, 5),)
    with pytest.raises(AssetError, match="长度不等"):
        skin_install.changed_spans(b"abc", b"abcd")


# --------------------------------------------------------------------------- #
# 契约：判据一行 + install_swap 的接线（Lead 的 CLI 就打印这些）
# --------------------------------------------------------------------------- #
def _fake_writeback(tmp_path: Path, *, old_bytes: int, stream_start: int) -> writer.WritebackPlan:
    row = writer.PlanRow(
        path_id=MATERIAL_ID,
        name=NAME,
        type_name="Material",
        old_bytes=old_bytes,
        new_bytes=old_bytes,
        level=writer.LEVEL_EQUAL,
        block_first=3,
        block_last=3,
        stream_start=stream_start,
    )
    return writer.WritebackPlan(
        bundle=tmp_path / "units",
        bundle_name="units",
        rows=(row,),
        block_runs=((3, 3),),
        hit_blocks=1,
        eta_seconds=3.0,
        old_file_size=3_380_000_000,
        new_file_size=3_380_000_000,
        disk_crc=2918958848,
        catalog_bundle_name="units",
        catalog_crc_old=2918958848,
        catalog_crc_new=1234567890,
        catalog_size_old=3_380_000_000,
        catalog_extra_offset=0,
    )


def _fake_plan(tmp_path: Path, *, material_bytes: int = 4844, stream_start: int = 938_569_800):
    return skin_install.SkinSwapPlan(
        game_root=tmp_path,
        unit_key="RU_BMD-2",
        bundle=tmp_path / "units",
        bundle_name="units",
        catalog_path=tmp_path / "catalog.json",
        material_name=NAME,
        material_path_id=MATERIAL_ID,
        material_bytes=material_bytes,
        slot=SLOT_A,
        old_texture="BMD-2_1_BaseMap",
        old_texture_path_id=TEX_OLD,
        new_texture="BMD-2_3_BaseMap",
        new_texture_path_id=TEX_NEW,
        change=writer.ObjectChange(
            path_id=MATERIAL_ID, payload=b"x" * material_bytes, name=NAME, type_name="Material"
        ),
        writeback=_fake_writeback(tmp_path, old_bytes=material_bytes, stream_start=stream_start),
        changed_spans=((56, 64),),
    )


def test_criterion_lines_carry_name_slot_and_the_eight_bytes(tmp_path: Path) -> None:
    """判据一行：包名 / 材质 / 槽 / 旧→新贴图 / 字节数 / 级别 —— 真机那条是 8 字节 + L1。"""
    plan = _fake_plan(tmp_path)
    text = "\n".join(plan.criterion_lines())
    for needle in ("units", "RU_BMD-2", NAME, SLOT_A, "BMD-2_1_BaseMap", "BMD-2_3_BaseMap"):
        assert needle in text, f"判据一行里缺 {needle!r}：\n{text}"
    assert "改动 8 字节" in text
    assert "L1" in text and "等长" in text
    assert "938,569,856" in text, "解压流绝对偏移要对得上真机记录"
    assert plan.changed_bytes == 8
    assert plan.absolute_spans() == ((938_569_856, 938_569_864),)
    assert "BMD-2_1_BaseMap" in plan.summary()


def test_install_swap_hands_the_patch_store_and_the_label_to_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接线：走反向 Δ（patch_store + patch_label），不是整包副本；计划阶段不建补丁目录。"""
    plan = _fake_plan(tmp_path)
    seen: dict[str, object] = {}

    def fake_write_objects(bundle, changes, **kwargs):
        seen["bundle"] = bundle
        seen["changes"] = list(changes)
        seen.update(kwargs)
        return "REPORT"

    monkeypatch.setattr(skin_install.writer, "write_objects", fake_write_objects)

    out = skin_install.install_swap(plan, project_root=tmp_path / "proj")
    assert out == "REPORT"
    assert seen["bundle"] == plan.bundle
    assert seen["changes"] == [plan.change]
    assert seen["dry_run"] is False
    assert seen["catalog_path"] == plan.catalog_path
    assert seen["game_root"] == plan.game_root
    assert isinstance(seen["patch_store"], reverse_delta.PatchStore)
    assert seen["patch_store"].game_root == plan.game_root, "补丁柜必须跟随游戏所在盘"
    assert str(seen["patch_label"]).startswith("皮肤：")
    assert "旧值 BMD-2_1_BaseMap → 新值 BMD-2_3_BaseMap" in str(seen["patch_label"])
    assert not (tmp_path / "proj" / "patches").exists(), "计划阶段不许建补丁目录"

    skin_install.install_swap(plan, project_root=tmp_path / "proj", dry_run=True)
    assert seen["dry_run"] is True
    assert not (tmp_path / "proj" / "patches").exists()
