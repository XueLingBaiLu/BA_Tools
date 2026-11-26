r"""单位节点名契约（asset_kernel/unit_shape.py）的机械判据。

契约逐条来自反编译字节码（原文 _work/audit-A-code.md §4）；本文件把这些条件**逐字**钉住，
尤其是两条最容易写错的边界：

* 车轮/悬挂的长度判据是 **> 3**（而前缀分别是 3 / 2 个字符）⇒ LR0（长 3）不命中、LR00 命中；
  L00（长 3）不命中、L001 命中；
* root / body 是 **ToLower 之后全等**；trackl / recoil_ 是 **Contains**（子串）。

## 一条真件反例（本文件钉住它）

真件 RU_T72B_Base.prefab 的履带节点叫 **RU_TanktrakL / RU_TanktrakR** —— 里面**没有 c**，
所以**不命中**引擎自己的 trackl 规则（_work/v3a-cache/v4-recon.txt 的层级 + 本文件的
test_real_t72b_names_do_not_match_the_track_rule）。⇒ 那条规则是**美术命名相关**的，
不是每个真件都满足；自造单位想有履带动画就得按规则命名。

判据一行：pytest tests/test_unit_shape.py -q -> 通过数 从 0 变 N。
"""

from __future__ import annotations

from ba_mod_maker.asset_kernel import unit_shape as us

#: 真件 RU_T72B_Base.prefab 的节点名（抄自 _work/v3a-cache/v4-recon.txt 的 52 节点层级）
REAL_T72B_NAMES = (
    ["RU_T72B_Base", "death", "Chassis", "BaseMobility", "RU_TanktrakL", "RU_TanktrakR",
     "lod_1x1x1", "root", "body", "VFXPoint_OnSmokeAbility", "dust_point_right",
     "dust_point_left", "exhaust"]
    + [f"LR{i:03d}" for i in (0, 7)] + [f"RR{i:03d}" for i in (0, 7)]
    + [f"L{i:03d}" for i in range(1, 7)] + [f"R{i:03d}" for i in range(1, 7)]
    + [f"LR{i:03d}" for i in range(1, 7)] + [f"RR{i:03d}" for i in range(1, 7)]
)


# --------------------------------------------------------------------------- 车轮
def test_wheel_rule_is_prefix_plus_length_strictly_greater_than_three() -> None:
    assert us.wheel_side("LR001") == "left"
    assert us.wheel_side("RR000") == "right"
    assert us.wheel_side("LR00") == "left", "长 4 = 边界内的最短名字"
    assert us.wheel_side("LR0") is None, "长 3 不命中（判据是 Length > 3）"
    assert us.wheel_side("RR0") is None
    assert us.wheel_side("lr001") is None, "Substring 比较区分大小写"
    assert us.wheel_side("L001") is None, "L0 是悬挂前缀，不是车轮的 LR0"
    assert us.wheel_side("") is None


# --------------------------------------------------------------------------- 悬挂
def test_suspension_rule_is_prefix_plus_length_strictly_greater_than_three() -> None:
    assert us.suspension_side("L001") == "left"
    assert us.suspension_side("R006") == "right"
    assert us.suspension_side("L00") is None, "长 3 不命中"
    assert us.suspension_side("R00") is None
    assert us.suspension_side("l001") is None
    assert us.suspension_side("LR001") is None, "前两个字符是 LR，不是 L0 ⇒ 不算悬挂"
    assert us.suspension_side("ROOT") is None


# --------------------------------------------------------------------------- 履带
def test_track_rule_is_case_insensitive_contains() -> None:
    assert us.track_side("trackl") == "left"
    assert us.track_side("TRACKR_01") == "right"
    assert us.track_side("TrackL_upper") == "left"
    assert us.track_side("chassis_track_left") is None, "必须是 trackl 连写（不是 track_left）"
    assert us.track_side("RU_TanktrakL") is None, "★ 真件那个名字里没有 c，不命中"


# --------------------------------------------------------------------------- root / body
def test_root_and_body_are_case_insensitive_exact_matches() -> None:
    shape = us.read_unit_shape(["ROOT", "Body", "Chassis"])
    assert shape.root is True and shape.body is True
    other = us.read_unit_shape(["rootTransform", "body_01"])
    assert other.root is False and other.body is False, "前缀/子串都不算，要全等"


# --------------------------------------------------------------------------- Chassis / Entrances
def test_chassis_is_case_sensitive_exact_and_entrances_too() -> None:
    assert us.read_unit_shape(["Chassis"]).chassis is True
    assert us.read_unit_shape(["chassis"]).chassis is False, "Find 是精确名（区分大小写）"
    assert us.read_unit_shape(["Entrances"]).entrances is True
    assert us.read_unit_shape(["Entrance"]).entrances is False


# --------------------------------------------------------------------------- 后坐（Contains 语义）
def test_recoil_uses_substring_matching() -> None:
    assert us.recoil_index("recoil_0", 0) is True
    assert us.recoil_index("recoil_1", 1) is True
    assert us.recoil_index("turret_1_recoil_2", 2) is True
    assert us.recoil_index("recoil_10", 1) is True, "Contains 语义：recoil_10 也含 recoil_1"
    assert us.recoil_index("recoil_0", 1) is False
    shape = us.read_unit_shape(["recoil_0", "recoil_1", "recoil_3"])
    assert [i for i, _n in shape.recoils] == [0, 1, 3]


# --------------------------------------------------------------------------- read_unit_shape
def test_read_unit_shape_takes_the_last_path_segment() -> None:
    # node_paths 会把每个节点都单独列出来（root / body 也是自己的那一行）
    shape = us.read_unit_shape(["RouteAUnit", "RouteAUnit/root", "RouteAUnit/root/body",
                               "RouteAUnit/root/body/LR001", "RouteAUnit/root/L001"])
    assert shape.wheels == ("LR001",)
    assert shape.suspensions == ("L001",)
    assert shape.root is True and shape.body is True
    assert us.read_unit_shape(["a/root/b"]).root is False, "只认末段，中间的 root 不算"


# --------------------------------------------------------------------------- 期望与缺件
def test_shape_problems_report_every_missing_contract_name() -> None:
    problems = us.shape_problems(us.read_unit_shape(["Chassis"]),
                                 us.ShapeExpect(entrances=True, recoil_indices=(0,)))
    joined = " | ".join(problems)
    for needle in ("root", "body", "左侧车轮", "右侧车轮", "左侧悬挂", "右侧悬挂",
                   "左履带", "右履带", "Entrances", "recoil_0"):
        assert needle in joined, f"缺件清单里少了 {needle}：{problems}"
    ok = us.read_unit_shape(["root", "body", "Chassis", "LR001", "RR001", "L001", "R001",
                            "trackl", "trackr", "Entrances", "recoil_0"])
    assert us.shape_problems(ok, us.ShapeExpect(entrances=True, recoil_indices=(0,))) == ()


def test_expectation_can_turn_rules_off() -> None:
    """轮式车没有履带 ⇒ 期望里关掉，不许报缺。"""
    shape = us.read_unit_shape(["root", "body", "Chassis", "LR001", "RR001", "L001", "R001"])
    assert us.shape_problems(shape, us.ShapeExpect(tracks=False)) == ()
    assert any("履带" in p for p in us.shape_problems(shape, us.ShapeExpect(tracks=True)))


# --------------------------------------------------------------------------- 真件反例
def test_real_t72b_names_do_not_match_the_track_rule() -> None:
    """★ 真件 T-72B 的履带节点名（RU_TanktrakL/R）**不满足**引擎的 trackl 规则。"""
    shape = us.read_unit_shape(REAL_T72B_NAMES)
    assert shape.chassis is True and shape.root is True and shape.body is True
    assert shape.base_mobility is True
    assert len(shape.left_wheels()) == len(shape.right_wheels()) == 8, "真件 8L/8R"
    assert len(shape.suspensions) == 12
    assert shape.tracks == (), "真件这两个名字里没有 c ⇒ 一个都不命中"
    problems = us.shape_problems(shape, us.ShapeExpect(entrances=False))
    assert any("左履带" in p for p in problems) and any("右履带" in p for p in problems)
    assert not any("车轮" in p or "悬挂" in p for p in problems), "轮子/悬挂真件是满足的"
