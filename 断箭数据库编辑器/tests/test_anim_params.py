r"""L11 · `asset_kernel.anim_params` 与 `anim params` 那条产品入口的判据。

**判据一行**：`pytest tests/test_anim_params.py -q -n 0` -> 全绿；把 `anim_params.PARAM_ORDER`
改宽（多塞一个名字）⇒ `test_mutation_*` **当场红**（「不在族里就拒」这条不再成立）。

夹具（真件，**只读**）：`_work/p3skin/bundles/p3skin-src.bundle` —— 真件 T-72B 底盘抽件，
里面那颗 `AnimationManager`（pathID **9177362236935111464**）的读数与
`D:\断箭模组制作\_work\dig\anim\params.tsv` 的 `RU_T72B_Base` 那一组**逐条相同**。

| # | 用例 | 必须 |
|---|---|---|
| 正控 1 | 名字与顺序 | 与 `unit_script_shapes.py` 的 AnimationManager 项**逐条相同** |
| 正控 2 | 真件 T-72B 整族读 | 7/7 格 · `TracksSpeed == 2.0` |
| 正控 3 | 与 283 件那张表对拍 | `RU_T72B_Base` 那 7 格逐条相同（表不在就 skip） |
| 正控 4 | `apply_family` 改 1 格 / 3 格 | 变了的叶子**恰好** 1 / 3 条，源树没被动 |
| 负控 1 | 名字不在族里 | 抛，且消息里把 7 个名字都点出来 |
| 负控 2 | 值不是数 / nan / inf / bool | 抛 |
| 负控 3 | 同一个名字给两遍 / 空改动 | 抛 |
| 负控 4 | 拿别的对象的树（没有 `_container._settings`） | 抛，且点名走到哪一跳断的 |
| 负控 5 | `--set` 同值（改完逐字节不变） | 读出来一个叶子都没变 |
| E2E 正控 | 走 CLI 真写 1 格 | rc=0 · 写后读回逐格相同 · 只 1 个叶子变 |
| E2E 负控 | 走 CLI 纯读 / 坏名字 | 纯读 sha256 逐位不变 · 坏名字 rc=2 |

⛔ 用例**不写游戏目录**：要真写就复制到 `tmp_path`；纯读那一条直接读真件夹具（只读）。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import anim_params, unit_script_shapes, unitypy_source

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
FIXTURE = WORK / "p3skin" / "bundles" / "p3skin-src.bundle"
#: 真件 T-72B 底盘 prefab 上那颗 AnimationManager（跨包稳定的 pathID）。
MANAGER_PID = 9177362236935111464
#: 283 件那张表（**在工作区根**，不在仓内 —— 不在就 skip 那一条）。
TABLE = Path("D:/断箭模组制作/_work/dig/anim/params.tsv")
#: 真件读数（出处：上面那张表的 RU_T72B_Base 一组 + docs/规格-素材盒与命名.md:166）。
T72B_SETTINGS = {
    "WheelSpeed": 70.0,
    "SuspensionSpeed": 0.800000011920929,
    "TracksSpeed": 2.0,
    "TurnSpeedMod": 0.0,
    "BodyShake": 20.0,
    "BodyShakeAggressive": 0.30000001192092896,
    "BodyMass": 11.0,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def manager_tree() -> dict:
    """开一次夹具包，把那个 AnimationManager 的字段树读进内存（模块级复用）。"""
    if not FIXTURE.is_file():
        pytest.skip(f"没有真件抽件夹具：{FIXTURE}")
    with unitypy_source.open_source(FIXTURE) as source:
        return dict(source.read_typetree(MANAGER_PID))


def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ba_mod_maker.cli", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, cwd=str(REPO),
    )


# --------------------------------------------------------------------------- #
# 正控
# --------------------------------------------------------------------------- #
def test_positive_control_names_match_the_generated_shape_baseline() -> None:
    """正控 1：这 7 个名字与顺序**照抄** `unit_script_shapes.py`（⛔ 不另造一份）。"""
    key = "BrokenArrow|BrokenArrow.Client.Ecs.Animations|AnimationManager"
    shape = unit_script_shapes.UNIT_SCRIPT_SHAPES[key]
    floats = tuple(name for level, kind, name in shape
                   if level == 3 and kind == anim_params.PARAM_KIND)
    assert floats == anim_params.PARAM_ORDER
    # `_container` 底下的第一格就是 UseContainer，紧跟着 `_settings`，7 个 float 挂在它下面
    # （⛔ level==2 不能整取：PPtr 的 m_FileID / m_PathID 也是 level 2）。
    names = [name for _level, _kind, name in shape]
    assert "UseContainer" not in floats
    assert names.index("_container") < names.index("UseContainer")
    assert names.index("UseContainer") < names.index("_settings")
    assert names.index("_settings") < names.index("WheelSpeed")
    assert anim_params.SETTINGS_STEPS == ("_container", "_settings")


def test_positive_control_reads_seven_of_seven_from_the_real_manager(manager_tree: dict) -> None:
    """正控 2：真件 T-72B 那颗 AnimationManager —— 7/7 格有值，`TracksSpeed == 2.0`。"""
    reading = anim_params.read_family(manager_tree)
    assert reading.present == len(anim_params.PARAM_ORDER) == 7
    assert reading.missing == ()
    assert reading.get("TracksSpeed") == 2.0
    for name, expected in T72B_SETTINGS.items():
        assert reading.get(name) == expected, name


def test_positive_control_matches_the_283_unit_table(manager_tree: dict) -> None:
    """正控 3：与 283 件那张表的 `RU_T72B_Base` 一组**逐条相同**（表不在就 skip）。"""
    if not TABLE.is_file():
        pytest.skip(f"没有那张 283 件的表：{TABLE}")
    rows = [line.split("\t") for line in TABLE.read_text(encoding="utf-8").splitlines()[1:]]
    head = TABLE.read_text(encoding="utf-8").splitlines()[0].split("\t")
    wanted = {
        row[head.index("key")].split(".")[-1]: row[head.index("value")]
        for row in rows
        if row[head.index("kind")] == "animation_manager"
        and row[head.index("prefab_root")] == "RU_T72B_Base"
        and row[head.index("key")].startswith("_settings.")
    }
    assert set(wanted) == set(anim_params.PARAM_ORDER)
    reading = anim_params.read_family(manager_tree)
    for name, text in wanted.items():
        assert reading.get(name) == float(text), name


def test_positive_control_apply_touches_exactly_the_requested_leaves(manager_tree: dict) -> None:
    """正控 4：改 1 格 = 恰好 1 个叶子变；改 3 格 = 恰好 3 个；源树一个字节没动。"""
    frozen = repr(manager_tree)
    one = anim_params.apply_family(manager_tree, {"TracksSpeed": 5.0})
    assert anim_params.unchanged_paths(manager_tree, one) == ("_container._settings.TracksSpeed",)
    three = anim_params.apply_family(
        manager_tree, {"TracksSpeed": 5.0, "WheelSpeed": 90.0, "BodyMass": 15.0}
    )
    assert set(anim_params.unchanged_paths(manager_tree, three)) == {
        "_container._settings.TracksSpeed",
        "_container._settings.WheelSpeed",
        "_container._settings.BodyMass",
    }
    assert repr(manager_tree) == frozen
    assert "_traks" in repr(manager_tree) and "_suspension" in repr(manager_tree)


def test_positive_control_changed_params_reports_old_and_new(manager_tree: dict) -> None:
    """正控 4b：`changed_params` 给出 (名字, 旧值, 新值) —— 面板与 CLI 读的就是它。"""
    before = anim_params.read_family(manager_tree)
    after = anim_params.read_family(anim_params.apply_family(manager_tree, {"TracksSpeed": 5.0}))
    assert anim_params.changed_params(before, after) == (("TracksSpeed", 2.0, 5.0),)
    assert anim_params.changed_params(before, before) == ()


# --------------------------------------------------------------------------- #
# 负控
# --------------------------------------------------------------------------- #
def _family_names_in(message: str) -> bool:
    return all(name in message for name in anim_params.PARAM_ORDER)


def test_negative_control_unknown_name_is_refused_and_lists_the_family() -> None:
    """负控 1：`TrackSpeed`（少个 s）⇒ 拒，且消息里把 7 个名字都点出来。"""
    with pytest.raises(anim_params.AnimParamsError) as caught:
        anim_params.parse_value("TrackSpeed", 1.0)
    assert "TrackSpeed" in caught.value.message
    assert _family_names_in(str(caught.value))


@pytest.mark.parametrize("bad", ["fast", "", "nan", "inf", "-inf", None, True])
def test_negative_control_bad_values_are_refused(bad: object) -> None:
    """负控 2：不是数 / 非有限 / bool / 空串 —— 一律拒（⛔ 不许把 nan 写进包）。"""
    with pytest.raises(anim_params.AnimParamsError):
        anim_params.parse_value("TracksSpeed", bad)


def test_negative_control_duplicate_and_empty_are_refused(manager_tree: dict) -> None:
    """负控 3：同一个名字给两遍 ⇒ 拒；空改动 ⇒ 拒（⛔ 不把「什么都没改」当成功）。"""
    with pytest.raises(anim_params.AnimParamsError):
        anim_params.parse_changes(["TracksSpeed=1", "TracksSpeed=2"])
    with pytest.raises(anim_params.AnimParamsError):
        anim_params.apply_family(manager_tree, {})


def test_negative_control_wrong_tree_is_refused() -> None:
    """负控 4：拿别的对象的树 ⇒ 拒，且点名走到哪一跳断的。"""
    with pytest.raises(anim_params.AnimParamsError) as caught:
        anim_params.settings_of({"_container": {"UseContainer": 1}})
    assert "_settings" in caught.value.message
    with pytest.raises(anim_params.AnimParamsError):
        anim_params.settings_of({"m_Name": "不是 AnimationManager"})


def test_negative_control_other_family_fields_are_refused() -> None:
    """负控 4b：`UseContainer` / `_root` / 乱造的名字都**不在**这一族里。"""
    for bad in ("UseContainer", "_container.UseContainer", "_root", "WheelSpeed2"):
        with pytest.raises(anim_params.AnimParamsError):
            anim_params.parse_value(bad, 1.0)


def test_negative_control_same_value_changes_no_leaf(manager_tree: dict) -> None:
    """负控 5（纯函数层）：「不改 ⇒ 不走样」—— 写回同值一个叶子都不变。"""
    same = anim_params.apply_family(manager_tree, {"TracksSpeed": 2.0})
    assert anim_params.unchanged_paths(manager_tree, same) == ()


# --------------------------------------------------------------------------- #
# 变异自检（判据能不能红）
# --------------------------------------------------------------------------- #
def _unknown_name_is_refused() -> bool:
    try:
        anim_params.parse_value("TrackSpeed", 1.0)
    except anim_params.AnimParamsError:
        return True
    return False


def test_mutation_widening_the_family_turns_the_negative_control_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """变异自检：把族放宽（多塞一个名字）⇒ **同一条负控当场不红**（撑住它的是 PARAM_ORDER）。"""
    assert _unknown_name_is_refused() is True
    monkeypatch.setattr(
        anim_params, "PARAM_ORDER", (*anim_params.PARAM_ORDER, "TrackSpeed")
    )
    assert _unknown_name_is_refused() is False


# --------------------------------------------------------------------------- #
# E2E：走 CLI（产品入口）
# --------------------------------------------------------------------------- #
def test_e2e_read_only_leaves_the_real_fixture_byte_identical() -> None:
    """E2E 负控：不给 `--set` ⇒ 纯读，夹具 sha256 **逐位不变**（⛔ 一个字节都不写）。"""
    if not FIXTURE.is_file():
        pytest.skip(f"没有真件抽件夹具：{FIXTURE}")
    before = _sha256(FIXTURE)
    done = _run_cli(
        "anim", "params", "--bundle", str(FIXTURE), "--path-id", str(MANAGER_PID)
    )
    assert done.returncode == 0, done.stderr
    assert "TracksSpeed = 2" in done.stdout
    assert "纯读" in done.stdout
    assert _sha256(FIXTURE) == before


@pytest.fixture(scope="module")
def writable_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """真写那两条用的**副本**（⛔ 不碰真件夹具、⛔ 不碰游戏目录）。"""
    if not FIXTURE.is_file():
        pytest.skip(f"没有真件抽件夹具：{FIXTURE}")
    target = tmp_path_factory.mktemp("l11") / "anim.bundle"
    shutil.copy2(FIXTURE, target)
    return target


def test_e2e_write_one_leaf_and_read_it_back(writable_copy: Path) -> None:
    """E2E 正控：`--set TracksSpeed=5.0 --write` ⇒ rc=0 · 写后读回逐格相同 · 只 1 个叶子变。"""
    before = _sha256(writable_copy)
    done = _run_cli(
        "anim", "params", "--bundle", str(writable_copy), "--path-id", str(MANAGER_PID),
        "--set", "TracksSpeed=5.0", "--write",
        "--project-root", str(writable_copy.parent),
    )
    assert done.returncode == 0, done.stderr
    assert "写后读回    : 逐格相同" in done.stdout
    assert _sha256(writable_copy) != before, "真写那一档必须真的落了字节"
    with unitypy_source.open_source(writable_copy) as source:
        after = anim_params.read_family(
            source.read_typetree(MANAGER_PID), path_id=MANAGER_PID
        )
    assert after.get("TracksSpeed") == 5.0
    for name in anim_params.PARAM_ORDER:
        if name != "TracksSpeed":
            assert after.get(name) == T72B_SETTINGS[name], name


def test_e2e_unknown_name_is_rc2_and_writes_nothing(writable_copy: Path) -> None:
    """E2E 负控：坏名字 ⇒ rc=2，且**零写入**（sha256 逐位不变）。"""
    before = _sha256(writable_copy)
    done = _run_cli(
        "anim", "params", "--bundle", str(writable_copy), "--path-id", str(MANAGER_PID),
        "--set", "TrackSpeed=1.0", "--write",
    )
    assert done.returncode == 2
    assert "这一族只有 7 个" in done.stderr
    assert _sha256(writable_copy) == before


# --------------------------------------------------------------------------- #
# float32 口径（2026-10-17 实测踩到的两个真缺陷，都落在这两条上）
# --------------------------------------------------------------------------- #
def test_positive_control_values_are_quantised_to_float32() -> None:
    """★ 0.45 在包里是 0.44999998807907104 —— 不量化就出「写 0.45 读 0.45 却不相等」的假红。"""
    assert anim_params.float32(0.45) == 0.44999998807907104
    assert anim_params.parse_value("BodyShakeAggressive", "0.45") == anim_params.float32(0.45)
    assert anim_params.same_value(0.45, anim_params.float32(0.45)) is True
    assert anim_params.same_value(0.45, 0.46) is False
    assert anim_params.same_value(None, None) is True
    assert anim_params.same_value(None, 0.0) is False


def test_positive_control_changed_params_is_float32_exact() -> None:
    """★ 写后读回那条判据：包里那 4 字节浮点 == 计划值 ⇒ **不算改动**（否则恒报红）。"""
    stored = anim_params.float32(0.45)
    planned = anim_params.FamilyReading(
        path_id=1, source="x",
        values=(("BodyShakeAggressive", stored, "float"),),
    )
    back = anim_params.FamilyReading(
        path_id=1, source="x",
        values=(("BodyShakeAggressive", anim_params.float32(stored), "float"),),
    )
    assert anim_params.changed_params(planned, back) == ()


def test_negative_control_value_out_of_float32_range_is_refused() -> None:
    """负控：1e40 装不进 float32 ⇒ 点名拒（⛔ 不静默变 inf）。"""
    with pytest.raises(anim_params.AnimParamsError) as caught:
        anim_params.parse_value("TracksSpeed", 1e40)
    assert "float32" in caught.value.message


def test_e2e_from_json_with_a_bom_and_a_not_exact_value(tmp_path: Path) -> None:
    """E2E：`--from-json`（**带 BOM** 的文件）整族 3 格 —— rc=0 · 写后读回逐格相同。"""
    if not FIXTURE.is_file():
        pytest.skip(f"没有真件抽件夹具：{FIXTURE}")
    copy = tmp_path / "fromjson.bundle"
    shutil.copy2(FIXTURE, copy)
    family = tmp_path / "family.json"
    # ⚠ 带 BOM 写（Windows 上 PowerShell 落的那份 JSON 就带 BOM —— 实测踩过
    #   「Unexpected UTF-8 BOM」），且值取 **float32 不精确** 的 0.45。
    family.write_text(
        '{"TracksSpeed": 5.0, "WheelSpeed": 90.0, "BodyShakeAggressive": 0.45}',
        encoding="utf-8-sig",
    )
    assert family.read_bytes()[:3] == b"\xef\xbb\xbf"
    done = _run_cli(
        "anim", "params", "--bundle", str(copy), "--path-id", str(MANAGER_PID),
        "--from-json", str(family), "--write", "--project-root", str(tmp_path),
    )
    assert done.returncode == 0, done.stderr
    assert "写后读回    : 逐格相同" in done.stdout
    with unitypy_source.open_source(copy) as source:
        after = anim_params.read_family(source.read_typetree(MANAGER_PID))
    assert after.get("TracksSpeed") == 5.0
    assert after.get("WheelSpeed") == 90.0
    assert after.get("BodyShakeAggressive") == anim_params.float32(0.45)
    for name in ("SuspensionSpeed", "TurnSpeedMod", "BodyShake", "BodyMass"):
        assert after.get(name) == T72B_SETTINGS[name], name


def test_e2e_set_and_from_json_sharing_a_name_is_rc2(tmp_path: Path) -> None:
    """E2E 负控：同一个名字在 --set 与 --from-json 里各给一遍 ⇒ rc=2、零写入。"""
    if not FIXTURE.is_file():
        pytest.skip(f"没有真件抽件夹具：{FIXTURE}")
    copy = tmp_path / "dup.bundle"
    shutil.copy2(FIXTURE, copy)
    family = tmp_path / "dup.json"
    family.write_text('{"TracksSpeed": 5.0}', encoding="utf-8")
    before = _sha256(copy)
    done = _run_cli(
        "anim", "params", "--bundle", str(copy), "--path-id", str(MANAGER_PID),
        "--set", "TracksSpeed=1.0", "--from-json", str(family), "--write",
    )
    assert done.returncode == 2
    assert "给了两遍" in done.stderr
    assert _sha256(copy) == before

