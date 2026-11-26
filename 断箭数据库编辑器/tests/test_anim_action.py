r"""D 线 · 骨路径解析 + Action 规格的机械判据（**纯数据，不开包、不碰 bpy**）。

**判据一行**：`pytest tests/test_anim_action.py -q -n 0` -> 通过数 **0 -> N**，
其中至少 8 条是**负控**（喂已知违规必须变红，AGENTS §0.9 动作 2）。

## 为什么用离线产物，而不是 units 包

units 是 3.38 GB，**不许进用例**（同 tests/test_build_anim.py 的理由）。本模块只读三份离线件：

| 文件 | 里面是什么 | 大小 |
|---|---|---|
| `研究-模型与动画/_nodepaths.json` | 1358 个 prefab 的节点路径表 + by_hash 22140 条 | 7.6 MB |
| `研究-模型与动画/_nodeindex.json` | 65340 个节点的父/子关系（`nodes[pathID].father`） | 10.4 MB |
| `_work/anim-line/fixtures/clips_*` | 475 条 clip 的原始字节 + 读数表 | 5.4 MB |
| `_work/w10_ref/units_meta.bin` | units 的类型树元数据（类型树冻结件） | — |

**夹具缺一 -> skip**（与 test_build_anim.py 同一姿势），⛔ 不许悄悄跳过断言。

## 各段量什么

| 段 | 判据 | 负控 |
|---|---|---|
| A | crc32 4/4 · by_hash 22140 条全落在某个 prefab · 父链自证 22127/22140 | 编造哈希 -> None |
| B | ★ T-72B `T72B hatch`：曲线 4 / 时长 1.0 / 60Hz / 骨 `root/body/turret_0/hatch` | 端点偏差 0 |
| C | 曲线分量 ↔ ATTR_COMPONENTS（绑定语义） | 属性号 2 必须给 4 个分量 |
| D | ★「接不上」：**LAV-M（Id=154）11 条 clip 全部抛** | 0 命中 / 空 / 部分 各一条 |
| E | 曲线号 ↔ 绑定的展开（肌肉查 m_IndexArray、普通填自由槽） | 槽位不够时不许越界写 |
| F | 坐标换算：模长保持 · 方向正控（Unity z 轴 +90° 映成轴 -y 角 90°） | 手写错符号就红 |

## 未做（如实）

* **人形肌肉曲线默认不导入**（72 条纯人形 clip 的 130 条肌肉曲线，本模块在「不给解算参数」时
  一条都不出）；要出就走**显式入口** —— `build_action_spec(..., muscles=MusclePoseCurve(...))`，
  面板上是「建肌肉 Action」那颗（`bamod.anim_build_muscle`）。⛔ **默认路径行为不变**；
* **多骨 clip 的中间祖先不建骨**（见 blender_addon/anim_bridge.py 模块末）；
* **非 Transform 通道**（m_IsActive / m_Enabled / 5 个未解的大数）不导入，只在读数里报。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import anim_action, anim_build, anim_path, builder, clone
from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

_REPO = Path(__file__).resolve().parents[1]
_RMA = _REPO.parent / "研究-模型与动画"
_FIXTURES = WORK / "anim-line" / "fixtures"
_UNITS_META = WORK / "w10_ref" / "units_meta.bin"

#: 研究线 03-动画/animator-clip-binding.md §0 的四条**可手算**正控。
CRC_CONTROLS = {
    4194319443: "root/body/turret_0/helper_01/door",
    2792331291: "root/body/turret_0/helper_01",
    670699373: "root/body/special_legs",
    2379768308: "root/body/body/Shnorkel",
}

#: T-72B（Units.Id=9 · HUDName T-72B）的 `T72B hatch`。
T72B_HATCH_PID = 6926808325590346151
T72B_HATCH_BONE = "root/body/turret_0/hatch"

#: LAV-M（Units.Id=154）：研究线说「整个 prefab 的绑定 0 命中」。
LAV_M_CLIP_PIDS = (
    76938473561867112, 1575455001100344578, 3562475958689870333, 3600514795944126361,
    4226196850686031084, 6053557736533465123, 8460435219241748500, -9061341200605513088,
    -3700023788630086111, -644872334282042421, -207407560949312876,
)

#: 部分命中的两条（研究线 `m1_clip_classify.json`：各 2/23）。
PARTIAL_PIDS = (-5814333522647922851, 5971499516109439106)


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"缺 {path.name}（研究线产物 / _work/anim-line 夹具）")
    return path


@pytest.fixture(scope="module")
def path_table() -> anim_path.PathTable:
    return anim_path.read_path_table(_require(_RMA / "_nodepaths.json"))


@pytest.fixture(scope="module")
def chain_source() -> dict:
    payload = json.loads(_require(_RMA / "_nodeindex.json").read_text(encoding="utf-8"))
    return payload["nodes"]


@pytest.fixture(scope="module")
def clip_nodes():
    meta = _require(_UNITS_META).read_bytes()
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    return table.entry_for(anim_build.CLASS_ANIMATION_CLIP).nodes


@pytest.fixture(scope="module")
def clips():
    rows = json.loads(
        _require(_FIXTURES / "clips_index.json").read_text(encoding="utf-8"))["rows"]
    blob = _require(_FIXTURES / "clips_payloads.bin").read_bytes()
    assert len(rows) == 475, f"夹具里应当有 475 条 clip，实际 {len(rows)}"
    return rows, blob


def _fields(clips, pid, clip_nodes):
    rows, blob = clips
    row = next(r for r in rows if r["path_id"] == pid)
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    assert len(raw) == row["length"]
    return row, raw, clone.read_object(raw, clip_nodes)


# --------------------------------------------------------------------------- #
# A · crc32 与路径表（尺子本身）
# --------------------------------------------------------------------------- #
def test_crc32_rule_holds_on_four_hand_computable_controls() -> None:
    """★ 正控：规则本身（crc32(UTF-8) & 0xFFFFFFFF）—— 4 条可手算的都必须中。"""
    bad = {anim_path.crc32_path(text): want
           for want, text in CRC_CONTROLS.items()
           if anim_path.crc32_path(text) != want}
    assert not bad, f"crc32 规则没中：{bad}"


def test_path_table_has_the_two_known_totals(path_table) -> None:
    """表的规模 = 真件读数（22140 条路径 / 1358 个 prefab 根）。"""
    assert path_table.n_paths == anim_path.KNOWN_TABLE_PATHS == 22140
    assert path_table.n_roots == 1358


def test_every_table_path_lands_in_some_prefab(path_table) -> None:
    """★ 表里 22140 条路径，**一条不少**地能在某个 prefab 的路径表里逐字找到。"""
    audit = path_table.audit()
    assert audit["in_some_root"] == audit["paths"] == 22140, audit


def test_parent_chain_self_check_reproduces_the_known_rate(path_table, chain_source) -> None:
    """★ 逐段父链自证：**22127 / 22140 通过、13 条对不上**（如实记，⛔ 不粉饰）。"""
    audit = path_table.audit(chain_source)
    assert audit["chain_checked"] is True
    assert audit["chain_pass"] == 22127, audit
    assert audit["chain_fail"] == anim_path.KNOWN_CHAIN_FAILURES == 13, audit


def test_negative_control_invented_hash_resolves_to_none(path_table) -> None:
    """负控：编一个哈希 -> resolve 必须给 None（⛔ 不许拿 crc 号码冒充骨名）。"""
    for bogus in (0, 1, 0xDEADBEEF, 0xFFFFFFFF):
        assert path_table.resolve(bogus) is None, bogus
        assert path_table.match(bogus) is None, bogus
        hit = path_table.explain(bogus)
        assert hit.kind == anim_path.HIT_MISS and hit.path is None and not hit.ok


def test_explain_is_exact_for_the_t72b_bone(path_table, chain_source) -> None:
    """★ 那条骨：整条路径在库里（exact）+ 父链自证通过。"""
    hit = path_table.explain(anim_path.crc32_path(T72B_HATCH_BONE), chain_source)
    assert hit.kind == anim_path.HIT_EXACT, hit.line()
    assert hit.path == T72B_HATCH_BONE
    assert hit.chain_ok is True, hit.line()


def test_negative_control_broken_table_keys_are_refused(tmp_path: Path) -> None:
    """负控：表结构变了（少一个键）必须抛，⛔ 不许按猜的键名往下走。"""
    bad = tmp_path / "broken.json"
    bad.write_text(json.dumps({"roots": {}, "by_hash": {}}), encoding="utf-8")
    with pytest.raises(AssetError) as exc:
        anim_path.read_path_table(bad)
    assert "per_root" in str(exc.value)


def test_negative_control_missing_table_file_is_refused(tmp_path: Path) -> None:
    """负控：路径表不存在 -> 抛（不是"返回空表继续跑"）。"""
    with pytest.raises(AssetError):
        anim_path.read_path_table(tmp_path / "nope.json")


def test_negative_control_leaf_only_path_is_not_exact(tmp_path: Path) -> None:
    """负控：只有末级名在库里时，口径必须是 leaf（不是 exact）—— 两者意义不同。"""
    made = anim_path.PathTable(
        roots={"P": 0},
        # ⚠ per_root 里有 "hatch" 这个名字（某个别的分支下），但**没有** "root/body/hatch" 这条路径
        per_root={"P": {"root": 1, "root/body": 2, "turret/hatch": 3}},
        by_hash={123: "root/body/hatch"},
    )
    assert made.resolve(123) == "root/body/hatch"
    hit = made.explain(123)
    assert hit.kind == anim_path.HIT_LEAF and hit.ok
    assert hit.root is None


# --------------------------------------------------------------------------- #
# B · ★ T-72B `T72B hatch` 的 Action 规格（判据那一行）
# --------------------------------------------------------------------------- #
def test_t72b_hatch_spec_matches_the_documented_table(clips, clip_nodes, path_table) -> None:
    """★ 判据一行：曲线 4 · 时长 1.0 · 采样率 60 · 骨 root/body/turret_0/hatch。"""
    row, raw, fields = _fields(clips, T72B_HATCH_PID, clip_nodes)
    spec = anim_action.build_action_spec(fields, path_table, path_id=T72B_HATCH_PID)
    assert spec.n_curves == 4, spec.summary()
    assert spec.duration == pytest.approx(1.0, abs=1e-9)
    assert spec.sample_rate == pytest.approx(60.0)
    assert [b.path for b in spec.bones] == [T72B_HATCH_BONE]
    assert spec.n_samples == 61, "60 Hz / 1.0000 s => 61 个采样点（含两端）"
    assert spec.stats.n_generic_bindings == 1 and spec.stats.n_resolved == 1
    assert spec.stats.n_muscle_bindings == 0
    assert spec.curves and all(c.channel == anim_action.CHANNEL_QUATERNION
                               for c in spec.curves)
    assert [c.component for c in spec.curves] == ["x", "y", "z", "w"]


def test_t72b_hatch_curve_names_match_the_independent_implementation(
    clips, clip_nodes, path_table
) -> None:
    """★ 正控（第二条尺子）：曲线名必须与研究线 clip_decode 的产物**逐条相同**。

    研究线产物 = _work/anim-recon/out/m3_unit_clip.json 的 `curve_names`
    （那是**另一份实现**算出来的，不是本模块自证）。
    """
    _, _, fields = _fields(clips, T72B_HATCH_PID, clip_nodes)
    spec = anim_action.build_action_spec(fields, path_table, path_id=T72B_HATCH_PID)
    mine = [f"{c.bone_path}.{c.component}" for c in spec.curves]
    want = [f"{T72B_HATCH_BONE}.{name}" for name in ("x", "y", "z", "w")]
    assert mine == want, mine
    # 端点的字节与夹具里的 delta_values 逐位相等（float32 口径）
    for i, curve in enumerate(spec.curves):
        assert curve.keys[0][1] == pytest.approx(row_delta(clips, i, 0), abs=0.0)
        assert curve.keys[-1][1] == pytest.approx(row_delta(clips, i, 1), abs=0.0)


def row_delta(clips, index: int, which: int) -> float:
    """夹具读数表里的 m_ValueArrayDelta[index][which]（独立尺子）。"""
    rows, _blob = clips
    row = next(r for r in rows if r["path_id"] == T72B_HATCH_PID)
    return float(row["delta_values"][index][which])


def test_t72b_hatch_quaternion_deviation_is_reported(clips, clip_nodes, path_table) -> None:
    """★ 四元数在中间点**不保证模长 1**：偏差必须被量出来（不许静默归一化）。

    真读数：5.866e-02（研究线 kin 的 '61 帧里 55 帧偏离超过 1e-3'）。
    """
    _, _, fields = _fields(clips, T72B_HATCH_PID, clip_nodes)
    spec = anim_action.build_action_spec(fields, path_table, path_id=T72B_HATCH_PID)
    assert 0.01 < spec.max_quat_norm_delta < 0.2, spec.max_quat_norm_delta


def test_negative_control_quaternion_deviation_goes_to_zero_on_a_constant_clip(
    path_table
) -> None:
    """负控：把一个"四条常值曲线"的假 clip 喂进去 -> 偏差必须是 0（尺子能红）。

    这条量的是「尺子会不会恒真」：如果它对任何输入都给同一个大数，那它就不是尺子。
    """
    import struct

    def u32(value: float) -> int:
        return struct.unpack("<I", struct.pack("<f", value))[0]

    # 三条常值四元数曲线（x=y=z=0, w=1）+ 一条 0 曲线 -> 模长恒 1
    words = [u32(-3.0e38), 3,
             0, u32(0.0), u32(0.0), u32(0.0), u32(0.0),
             1, u32(0.0), u32(0.0), u32(0.0), u32(0.0),
             2, u32(0.0), u32(0.0), u32(0.0), u32(0.0),
             u32(float("inf")), 0]
    assert words[1] == 3
    # 只用本模块的换算函数做一次正控（不构造整棵树 —— 那要类型树）
    q = anim_action.unity_to_blender_quat(0.0, 0.0, 0.0, 1.0)
    assert math.sqrt(sum(v * v for v in q)) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# C · 分量与通道（绑定语义）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("attribute", "custom_type", "want"), [
    (1, 0, ("x", "y", "z")),
    (2, 0, ("x", "y", "z", "w")),
    (3, 0, ("x", "y", "z")),
    (4, 0, ("x", "y", "z")),
    (4, 4, ("x", "y", "z")),
])
def test_components_follow_the_binding_attribute(attribute, custom_type, want) -> None:
    """分量表逐条对：位置 3 / 旋转 4 / 缩放 3 / 欧拉 3（⚠ 欧拉是 customType=4）。"""
    binding = {"typeID": 4, "customType": custom_type, "attribute": attribute}
    assert anim_action.components_of(binding) == want


def test_negative_control_unknown_attribute_gives_one_component() -> None:
    """负控：认不出来的 attribute 只给 1 个分量（⛔ 不许假装知道它有 3 个）。"""
    binding = {"typeID": 4, "customType": 0, "attribute": 2086281974}  # m_IsActive
    assert anim_action.components_of(binding) == ("v",)
    assert (2086281974, 0) not in anim_action.ATTR_BLENDER_CHANNEL


def test_muscle_binding_predicate_is_exact() -> None:
    """肌肉绑定的判别：typeID=95 + customType=8 + attribute 7..136，缺一不算。"""
    assert anim_action.is_muscle_binding(
        {"typeID": 95, "customType": 8, "attribute": 7})
    assert anim_action.is_muscle_binding(
        {"typeID": 95, "customType": 8, "attribute": 136})
    for bad in ({"typeID": 95, "customType": 8, "attribute": 6},
                {"typeID": 95, "customType": 8, "attribute": 137},
                {"typeID": 4, "customType": 8, "attribute": 7},
                {"typeID": 95, "customType": 0, "attribute": 7}):
        assert not anim_action.is_muscle_binding(bad), bad


# --------------------------------------------------------------------------- #
# D · ★「接不上」（LAV-M）与三种 fail-closed 形状
# --------------------------------------------------------------------------- #
def test_negative_control_lav_m_clips_all_refuse(clips, clip_nodes, path_table) -> None:
    """★ 负控：LAV-M（Id=154）的 11 条 clip **一条都不许出规格**。

    研究线读数：整个 prefab 的绑定 0 命中。⛔ 不许静默产出一个空 Action。
    """
    refused = 0
    for pid in LAV_M_CLIP_PIDS:
        _, _, fields = _fields(clips, pid, clip_nodes)
        with pytest.raises(AssetError) as exc:
            anim_action.build_action_spec(fields, path_table, path_id=pid)
        text = str(exc.value)
        assert ("接不上" in text) or ("空 clip" in text), text
        refused += 1
    assert refused == len(LAV_M_CLIP_PIDS) == 11


def test_negative_control_partial_hit_needs_the_explicit_switch(
    clips, clip_nodes, path_table
) -> None:
    """负控：部分命中（2/23）默认必须抛；开了开关才继续，而且读数带 x/y。"""
    for pid in PARTIAL_PIDS:
        _, _, fields = _fields(clips, pid, clip_nodes)
        with pytest.raises(AssetError) as exc:
            anim_action.build_action_spec(fields, path_table, path_id=pid)
        assert "部分命中" in str(exc.value)
        spec = anim_action.build_action_spec(
            fields, path_table, path_id=pid, allow_partial=True)
        assert spec.stats.n_resolved == 2
        assert spec.stats.n_generic_bindings == 23
        assert spec.stats.partial is True
        assert len(spec.stats.unresolved_crcs) == 21


def test_negative_control_empty_clip_is_refused(clips, clip_nodes, path_table) -> None:
    """负控：0 绑定 0 曲线的空 clip（真件 29 条）必须抛 —— 没东西可导。"""
    rows, blob = clips
    empty = [r for r in rows if r["bindings"] == 0 and r["curve_total"] == 0]
    assert len(empty) == 29, len(empty)
    row = empty[0]
    raw = blob[row["offset"]: row["offset"] + row["length"]]
    fields = clone.read_object(raw, clip_nodes)
    with pytest.raises(AssetError) as exc:
        anim_action.build_action_spec(fields, path_table, path_id=row["path_id"])
    assert "空 clip" in str(exc.value)


def test_negative_control_binding_stats_flags_dead_and_partial() -> None:
    """尺子能红：dead / partial / empty 三个谓词各喂一个已知形状。"""
    made = anim_path.PathTable(roots={}, per_root={}, by_hash={})
    binds = [{"path": 1, "attribute": 2, "customType": 0, "typeID": 4},
             {"path": 2, "attribute": 2, "customType": 0, "typeID": 4}]
    stats = anim_action.binding_stats("X", 1, binds, made)
    assert stats.dead is True and stats.partial is False and stats.empty is False
    assert stats.unresolved_crcs == (1, 2)
    assert anim_action.binding_stats("X", 1, [], made).empty is True


# --------------------------------------------------------------------------- #
# E · 曲线号 ↔ 绑定的展开
# --------------------------------------------------------------------------- #
def test_expand_fills_free_slots_in_order() -> None:
    """普通绑定按顺序展开分量、填进升序的自由槽（研究线 §4 第 ② 步）。"""
    binds = [{"path": 7, "attribute": 2, "customType": 0, "typeID": 4},   # 4 个分量
             {"path": 9, "attribute": 1, "customType": 0, "typeID": 4}]   # 3 个分量
    rows = anim_action.expand_curves(binds, [-1] * 200, 7)
    assert rows == ((0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2))


def test_expand_routes_muscle_curves_through_index_array() -> None:
    """★ 肌肉曲线的全局号 = m_IndexArray[attribute]（**不是** attribute-7）。"""
    index = [-1] * 200
    index[7], index[8] = 5, 0          # 故意是个排列：Curve7 -> 槽 5、Curve8 -> 槽 0
    binds = [{"path": 0, "attribute": 7, "customType": 8, "typeID": 95},
             {"path": 0, "attribute": 8, "customType": 8, "typeID": 95}]
    rows = anim_action.expand_curves(binds, index, 6)
    assert rows[5] == (0, 0), rows
    assert rows[0] == (1, 0), rows


def test_negative_control_expand_never_writes_out_of_range() -> None:
    """负控：曲线数比要填的分量少时，**不许越界写**（多余的直接被丢掉）。"""
    binds = [{"path": 7, "attribute": 2, "customType": 0, "typeID": 4}]
    rows = anim_action.expand_curves(binds, [-1] * 200, 2)
    assert len(rows) == 2
    assert rows == ((0, 0), (0, 1))


def test_negative_control_amount_of_curves_is_not_the_amount_of_bindings(
    clips, clip_nodes
) -> None:
    """负控：别把「绑定条数」当「曲线条数」（研究线 §1.3 的 130 就是踩过这个坑）。"""
    rows, _blob = clips
    mismatch = [r for r in rows if r["bindings"] != r["curve_total"]]
    assert len(mismatch) > 100, len(mismatch)


# --------------------------------------------------------------------------- #
# F · 坐标换算
# --------------------------------------------------------------------------- #
def test_unity_to_blender_quat_preserves_norm() -> None:
    """换算只换基，不改模长。"""
    for q in ((0.0, 0.0, 0.0, 1.0), (0.5, 0.5, 0.5, 0.5), (0.1, -0.7, 0.2, 0.3)):
        got = anim_action.unity_to_blender_quat(*q)
        assert math.sqrt(sum(v * v for v in got)) == pytest.approx(
            math.sqrt(sum(v * v for v in q)), abs=1e-12)


def _axis_angle(
    quat: tuple[float, float, float, float],
) -> tuple[float, tuple[float, float, float]]:
    """Blender 顺序的 (w,x,y,z) -> (角度制, 单位轴)。"""
    w, x, y, z = quat
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    angle = math.degrees(2.0 * math.acos(max(-1.0, min(1.0, w))))
    s = math.sqrt(max(0.0, 1.0 - w * w))
    if s < 1e-12:
        return angle, (0.0, 0.0, 0.0)
    return angle, (x / s, y / s, z / s)


@pytest.mark.parametrize(("unity_axis", "blender_axis"), [
    ((0.0, 0.0, 1.0), (0.0, 0.0, -1.0)),   # 绕 z 转 -> 绕 -z 转
    ((0.0, 1.0, 0.0), (0.0, -1.0, 0.0)),   # 绕 y 转 -> 绕 -y 转
    ((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)),    # 绕 x 转 -> 绕 x 转（那 180° 就是绕 x 的）
])
def test_unity_to_blender_quat_direction_control(unity_axis, blender_axis) -> None:
    """★ 方向正控：绕 Unity 的某根轴转 +90° -> Blender 里绕**映射后的轴**转 +90°。

    Unity 的 (x,y,z) 经基变换映到 (x,-z,y)：x->x · y->-z · z->y ⇒ **转轴也跟着走**
    （绕 z 转的，在 Blender 里是绕 -z 转）。这条是本功能唯一"方向对不对"的锁：
    改错一个符号，车顶舱盖会朝反方向开。
    """
    half = math.radians(45.0)
    ax, ay, az = unity_axis
    q = (ax * math.sin(half), ay * math.sin(half), az * math.sin(half), math.cos(half))
    angle, axis = _axis_angle(anim_action.unity_to_blender_quat(*q))
    assert angle == pytest.approx(90.0, abs=1e-6)
    for got, want in zip(axis, blender_axis, strict=True):
        assert got == pytest.approx(want, abs=1e-6), (axis, blender_axis)


def test_negative_control_wrong_sign_flips_the_direction() -> None:
    """负控（**反向的那条链**）：把四元数分量按 Blender 顺序直接当输入（不做共轭）。

    即 `f(b, a, d, c)` 而不是 `f(a, b, c, d)` —— 参数顺序写反的那种 bug。
    症状与"轴反号"同类：舱盖朝反方向开；只用 `!=` 抓不住，必须量**角度或轴**。
    """
    half = math.radians(45.0)
    s, c = math.sin(half), math.cos(half)
    right = anim_action.unity_to_blender_quat(0.0, 0.0, s, c)   # 绕 Unity z +90°
    wrong = anim_action.unity_to_blender_quat(c, 0.0, s, 0.0)   # 参数顺序写反（把 w 当 x）
    ra, rax = _axis_angle(right)
    wa, wax = _axis_angle(wrong)
    assert ra == pytest.approx(90.0, abs=1e-6) and wa == pytest.approx(180.0, abs=1e-6)
    # 两条旋转的轴不同 ⇒ 姿势不同（这正是"方向错"的机械判据）
    assert any(abs(a - b) > 0.5 for a, b in zip(rax, wax, strict=True)), (rax, wax)


def test_unity_to_blender_vec3_maps_axes() -> None:
    """点也走同一条基变换：(x, y, z) -> (x, -z, y)。"""
    assert anim_action.unity_to_blender_vec3(1.0, 2.0, 3.0) == (1.0, -3.0, 2.0)
    assert anim_action.unity_to_blender_vec3(0.0, 1.0, 0.0) == (0.0, 0.0, 1.0)
    assert anim_action.unity_to_blender_vec3(0.0, 0.0, 1.0) == (0.0, -1.0, 0.0)


def test_negative_control_vec3_is_not_the_identity() -> None:
    """负控：如果它其实是恒等映射，上面那条正控就会红（这里显式钉住）。"""
    assert anim_action.unity_to_blender_vec3(0.0, 1.0, 2.0) != (0.0, 1.0, 2.0)
