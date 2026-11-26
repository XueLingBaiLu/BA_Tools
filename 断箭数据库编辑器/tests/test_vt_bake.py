"""tests/test_vt_bake.py —— **离线夹具**测 vt-bake 的编排。

⛔ 这里一次真 Unity 都不跑：走 `plan_bake(unity_argv=...)` 这个接缝，喂一个
本文件自己写出来的**假编辑器**（真 python 进程），三种情形各自断言：

| 情形 | 假编辑器干什么 | 断言什么 |
|---|---|---|
| 正常返回 | 写读数 + 造 5 个假池子文件 | outcome.ok / group / item / 5 个产物 / 收尾干净 |
| 超时 | sleep 120 秒 | timed_out + 被杀 + **一个池子文件都不产出** |
| 日志里有 error CS | 照写好 VERDICT，但日志里塞一条 error CS | **判红**（不许假绿）+ 不收割池子 |
| 死在半路 | 写完 setup 读数就 exit(3)（三合一那一趟） | 兜底再起一趟只跑复原 + 判红 + 不产出池子 |
| 超时（三合一） | 同上但 sleep 120 秒 | 三站全部记超时 + 兜底复原 + 不产出池子 |

**三合一（一趟会话跑完三站）这一档**：假编辑器把"一趟 -executeMethod 里按序跑哪几站"
照产品 PHASES 实现，并**逐趟追加一行**到 unity-invocations.txt —— 于是
"Unity 起了几趟"变成一条能数的机械判据（正常路径必须**只有一行**）。
--split（merged=False）是它的对照档；--only 照旧一趟一站。

**N 颗材质（一趟可能烘出不止一套池子）这一档**：假编辑器按 `BAVTB_SOURCES` 写 N 条
`mat<i>_item=` 与 N 条 `item_bundle=`，产品那侧判：
条数 == 材质数 · 两两不同 · 与逐材质那几条对得上 · 只有一个 group。

另外几条正控：
* 假编辑器写的**读数文件名**必须与产品 `READING_NAMES` 对齐（夹具不许悄悄漂移）；
* `check_pool_shape` 对已知好的一组**绿**、对每组坏法**红**（尺子先做正控）；
* J3：源图不存在 / 是坏图 / **张数不成对** -> `AssetError`（CLI 退出码 2），
  且 **`--out` 目录都没建**、**一个池子文件都没有**；
* N>1 的正控：两颗材质**共用一个 item**（被挤掉）/ 有一条 item 没写出来 -> 判红。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from ba_mod_maker.asset_kernel import vt_bake
from ba_mod_maker.cli import vt_bake as vt_bake_cli
from ba_mod_maker.core.errors import AssetError

GROUP = "0123456789abcdef0123456789abcdef"
ITEM = "fedcba9876543210fedcba9876543210"
OTHER_GROUP = "abcdef0123456789abcdef0123456789"
GTS_BYTES = 1_048_576
SHARDS = ("0_1", "2", "3", "4")

#: 假编辑器：只认「-executeMethod 是哪一个」与「日志要说什么」。
#: 它是**真的 python 进程**（Unity 那边同样是子进程 + 退出码 + 日志文件），
#: 所以超时那条能真的考到 taskkill 与 KILL_GRACE。
FAKE_UNITY_SOURCE = r'''import os
import pathlib
import sys
import time

ARGS = sys.argv[1:]
OUT = os.environ["BAVTB_OUTDIR"]
MODE = os.environ.get("FAKE_MODE", "ok")
ITEM0 = "fedcba9876543210fedcba9876543210"

#: 站 -> 读数文件（与产品 READING_NAMES 同一套）
READING = {
    "setup": "vtbake-setup.txt",
    "build": "vtbake-build.txt",
    "restore": "vtbake-restore.txt",
}
#: 一趟 -executeMethod 里按序跑哪几站（与产品 METHODS / PHASES 同一套）
PHASES = {
    "VtBakeOne.Run": ["setup", "build", "restore"],
    "VtBakeSetup.Run": ["setup"],
    "VtBakeBuild.Run": ["build"],
    "VtBakeSetup.Restore": ["restore"],
}
#: 每站自己报的分段秒数 —— 用例靠它验证「分段耗时是从读数里读回来的」
SECONDS = {"setup": "1.5", "build": "2.5", "restore": "3.5"}


def arg(name):
    for i, a in enumerate(ARGS):
        if a == name and i + 1 < len(ARGS):
            return ARGS[i + 1]
    return ""


METHOD = arg("-executeMethod")
LOG = arg("-logFile")

pathlib.Path(OUT).mkdir(parents=True, exist_ok=True)
# 一趟一行：用例靠它数「Unity 起了几趟」（三合一的机械判据 = 这里**只有一行**）
with open(os.path.join(OUT, "unity-invocations.txt"), "a", encoding="utf-8") as fh:
    fh.write(METHOD + "\n")

if LOG:
    pathlib.Path(LOG).parent.mkdir(parents=True, exist_ok=True)
    text = "fake unity: " + METHOD + "\n"
    if MODE == "error_cs":
        text += "Assets/VtBake/Editor/VtBakeBuild.cs(12,34): error CS0103: no such name\n"
    pathlib.Path(LOG).write_text(text, encoding="utf-8")

if MODE == "timeout":
    time.sleep(120)

def write_reading(stage, lines):
    body = ["seconds=" + SECONDS[stage]] + lines
    pathlib.Path(OUT, READING[stage]).write_text("\n".join(body) + "\n", encoding="utf-8")


def do_setup():
    write_reading("setup", ["PlayerSettings.VirtualTexturingSupportEnabled(原) = False",
                            "VERDICT setup-ok"])


def do_restore():
    write_reading("restore", ["VT 开关恢复 -> False", "VERDICT restore-ok"])


def do_build():
    # N = 产品告诉我们的材质颗数（run_editor / run_merged 把 BAVTB_SOURCES 塞进环境）
    n = int(os.environ.get("BAVTB_SOURCES", "1"))
    exe = pathlib.Path(os.environ["BAVTB_PLAYER_EXE"])
    sa = exe.parent / (exe.stem + "_Data") / "StreamingAssets"
    sa.mkdir(parents=True, exist_ok=True)
    # 盘上写哪几套池子 / 读数里报哪几个 group —— 两条独立来源，可以故意对不上
    pool_group = os.environ.get("FAKE_GROUP", "0123456789abcdef0123456789abcdef")
    bundle = os.environ.get("FAKE_BUNDLE_GROUP", pool_group)
    extra = os.environ.get("FAKE_EXTRA_GROUP", "")
    disk = [pool_group] * n
    read = [bundle] * n
    if extra and n > 1:
        # 实测 _work/vtb1/extreme：两颗材质同 shader 同两层，只因 Mask 不同就两个 group
        disk[1] = extra
        read[1] = extra
    drop = os.environ.get("FAKE_DROP_SHARD", "")
    for one in dict.fromkeys(disk):
        (sa / (one + ".gts")).write_bytes(b"G" * 1048576)
        for shard in ("0_1", "2", "3", "4"):
            if drop == one and shard == "0_1":
                continue          # 负控：这一套缺第一片
            (sa / (one + shard + ".gtp")).write_bytes(b"P" * 64)
    # 一颗材质一个 item：第 0 颗是固定的 ITEM0，其余按序号派生（互不相同）
    items = [ITEM0] + [("%032x" % (i * 0x1111)) for i in range(1, n)]
    if os.environ.get("FAKE_SAME_ITEM"):
        items = [ITEM0] * n
    if os.environ.get("FAKE_ITEMS"):
        items = os.environ["FAKE_ITEMS"].split(",")
    lines = ["group=" + read[0],
             "item=" + items[0]]
    for i, it in enumerate(items):
        lines.append("mat%d_name=VtBake%s" % (i, "" if i == 0 else i))
        lines.append("mat%d_group=%s" % (i, read[i]))
        lines.append("mat%d_item=%s" % (i, it))
    for one in read:
        lines.append("group_bundle=" + one)
    for it in items:
        lines.append("item_bundle=" + it)
    lines.append("group_pool=" + disk[0])
    lines.append("VERDICT bake-ok")
    write_reading("build", lines)


DO = {"setup": do_setup, "build": do_build, "restore": do_restore}

# --keep：三合一那一趟跳过复原（⛔ 与 C# 侧同一个键名）
wanted = PHASES[METHOD]
if os.environ.get("BAVTB_KEEP") == "1" and METHOD == "VtBakeOne.Run":
    wanted = [s for s in wanted if s != "restore"]

for stage in wanted:
    # 正控：三合一那一趟在 setup 之后**死掉**（既没写 restore 读数、也没复原工程）
    if MODE == "crash_after_setup" and METHOD == "VtBakeOne.Run" and stage != "setup":
        sys.exit(3)
    DO[stage]()

# 正控：三站在读数上都绿，但进程在复原之前就死了
if MODE == "crash_before_restore" and METHOD == "VtBakeOne.Run":
    sys.exit(3)
sys.exit(0)
'''


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir(parents=True)
    library = project / "Library"
    library.mkdir(parents=True)
    (library / "EditorOnlyVirtualTextureState.json").write_text(
        '{"m_VirtualTexturingEnabled": false, "m_VirtualTexturingEnabledInitialized": true}',
        encoding="utf-8",
    )
    return project


def _make_image(tmp_path: Path, name: str, size: tuple[int, int] = (128, 128)) -> Path:
    path = tmp_path / name
    Image.new("RGB", size, (128, 128, 255)).save(path)
    return path


def _fake_unity(tmp_path: Path) -> Path:
    script = tmp_path / "fake_unity.py"
    script.write_text(FAKE_UNITY_SOURCE, encoding="utf-8")
    return script


def _pairs(tmp_path: Path, count: int) -> list[tuple[Path, Path]]:
    """count 组源图（每组内容不同 —— 免得两组被当成同一颗）。"""
    got: list[tuple[Path, Path]] = []
    for index in range(count):
        normal = tmp_path / f"pair{index}_n.png"
        mask = tmp_path / f"pair{index}_m.png"
        Image.new("RGB", (128, 128), (index * 10 + 5, 20, 30)).save(normal)
        Image.new("RGB", (128, 128), (30, 20, index * 10 + 5)).save(mask)
        got.append((normal, mask))
    return got


def _plan(tmp_path: Path, *, count: int = 1, **overrides: object) -> vt_bake.BakePlan:
    """默认走**老写法**（normal + mask 一组）；count > 1 时走 pairs。"""
    kwargs: dict[str, object] = {
        "project": _make_project(tmp_path),
        "out_dir": tmp_path / "out",
        "unity_argv": (sys.executable, str(_fake_unity(tmp_path))),
        "player_dir": tmp_path / "player",
        "timeout_setup": 60.0,
        "timeout_build": 60.0,
        "timeout_restore": 60.0,
    }
    if count == 1:
        kwargs["normal"] = _make_image(tmp_path, "normal.png")
        kwargs["mask"] = _make_image(tmp_path, "mask.png")
    else:
        kwargs["pairs"] = _pairs(tmp_path, count)
    kwargs.update(overrides)
    return vt_bake.plan_bake(**kwargs)  # type: ignore[arg-type]


def _bake(plan: vt_bake.BakePlan) -> vt_bake.BakeOutcome:
    return vt_bake.bake(plan, log=lambda _line: None, unity_probe=lambda: 0)


def _invocations(plan: vt_bake.BakePlan) -> list[str]:
    """这一轮 Unity 一共被起了几趟、每趟调的是哪个方法（假编辑器逐趟追加一行）。

    三合一的机械判据就是这里**只有一行** —— 数读数文件是数不出来的。
    """
    path = plan.log_dir / "unity-invocations.txt"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").split()


# --------------------------------------------------------------------------- #
# 读数解析 / 尺子正控
# --------------------------------------------------------------------------- #
def test_reading_parser_takes_key_values_and_verdict_and_ignores_human_lines() -> None:
    text = (
        "=== VtBakeBuild ===\n"
        "     改前: cpuMB=4 gpu={BC3_SRGB(int=100),128MiB}\n"
        "group=" + GROUP + "\n"
        "item_bundle=" + ITEM + "\n"
        "VERDICT bake-ok\n"
    )
    got = vt_bake.parse_reading(text)
    assert got.one("group") == GROUP
    assert got.one("item_bundle") == ITEM
    assert got.one("改前: cpuMB") is None  # 带空格的人读行不许被当读数
    assert got.verdict == vt_bake.VERDICT_BAKE


def test_fake_unity_speaks_the_reading_names_the_product_reads() -> None:
    """夹具不许悄悄漂移：假编辑器写的名字必须就是产品读的那三个。"""
    assert vt_bake.READING_NAMES == {
        "setup": "vtbake-setup.txt",
        "build": "vtbake-build.txt",
        "restore": "vtbake-restore.txt",
    }


def _pool(tmp_path: Path) -> list[Path]:
    directory = tmp_path / "pool"
    directory.mkdir(exist_ok=True)
    written = [directory / f"{GROUP}.gts"]
    written += [directory / f"{GROUP}{shard}.gtp" for shard in SHARDS]
    for path in written:
        path.write_bytes(b"x" * (GTS_BYTES if path.suffix == ".gts" else 16))
    return written


def test_pool_shape_ruler_is_green_on_a_known_good_set(tmp_path: Path) -> None:
    group, problems = vt_bake.check_pool_shape(_pool(tmp_path))
    assert problems == ()
    assert group == GROUP


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda files: files[1].unlink(), id="缺一个分片"),
        pytest.param(lambda files: files[1].rename(files[1].with_name("deadbeef0_1.gtp")),
                     id="分片不属于这一组"),
        pytest.param(lambda files: files[0].unlink(), id="没有 gts"),
        pytest.param(lambda files: files[0].write_bytes(b"x" * 16), id="gts 大小不对"),
    ],
)
def test_pool_shape_ruler_goes_red(tmp_path: Path, mutate) -> None:
    files = _pool(tmp_path)
    mutate(files)
    _group, problems = vt_bake.check_pool_shape(vt_bake.pool_files(files[0].parent))
    assert problems


# --------------------------------------------------------------------------- #
# J3：源图坏 -> 报错，且一个池子文件都不产出
# --------------------------------------------------------------------------- #
def test_plan_bake_refuses_a_missing_normal_and_writes_nothing(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    with pytest.raises(AssetError) as excinfo:
        vt_bake.plan_bake(
            project=_make_project(tmp_path),
            normal=tmp_path / "nope.png",
            mask=_make_image(tmp_path, "mask.png"),
            out_dir=out_dir,
            unity_argv=(sys.executable, str(_fake_unity(tmp_path))),
        )
    assert "不在了" in excinfo.value.message
    assert not out_dir.exists()
    assert not (tmp_path / "proj" / "Assets" / vt_bake.WORK_SUBDIR).exists()


def test_plan_bake_refuses_a_broken_image_and_writes_nothing(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"this is not a png at all")
    out_dir = tmp_path / "out"
    with pytest.raises(AssetError) as excinfo:
        vt_bake.plan_bake(
            project=_make_project(tmp_path),
            normal=broken,
            mask=_make_image(tmp_path, "mask.png"),
            out_dir=out_dir,
            unity_argv=(sys.executable, str(_fake_unity(tmp_path))),
        )
    assert "不是能解码的图" in excinfo.value.message
    assert not out_dir.exists()
    assert vt_bake.pool_files(out_dir) == ()


def test_plan_bake_pins_every_path_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""⛔ 相对路径会落进**工程根**里 —— 这是真踩过的坑。

    实测：Unity 编辑器的当前目录会变成工程根（VtBakeSetup.cs 里的
    Directory.GetCurrentDirectory() = <工程>），于是相对路径的读数 / 日志 / 证据
    全写进了 <工程>\_work\...，而 python 侧在仓目录里等它 ⇒ 三站全报「读数没更新」。
    这道用例就是那条坑的回归：计划里的每条路径都必须是绝对的。
    """
    project = _make_project(tmp_path)
    _make_image(tmp_path, "normal.png")
    _make_image(tmp_path, "mask.png")
    fake = str(_fake_unity(tmp_path))
    monkeypatch.chdir(tmp_path)
    plan = vt_bake.plan_bake(
        project="proj",
        normal="normal.png",
        mask="mask.png",
        out_dir="out",
        player_dir="player",
        unity_argv=(sys.executable, fake),
    )
    assert plan.project == project.resolve()
    assert plan.normal == (tmp_path / "normal.png").resolve()
    assert plan.out_dir == (tmp_path / "out").resolve()
    assert plan.player_dir == (tmp_path / "player").resolve()
    for path in (plan.asset_dir, plan.log_dir, plan.bundle_dir, plan.snapshot_path,
                 plan.vt_state_path, plan.player_exe, plan.reading_path("build"),
                 plan.log_path("build")):
        assert path.is_absolute(), path


def test_plan_bake_refuses_a_directory_that_is_not_a_unity_project(tmp_path: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        vt_bake.plan_bake(
            project=tmp_path / "nothing-here",
            normal=_make_image(tmp_path, "normal.png"),
            mask=_make_image(tmp_path, "mask.png"),
            out_dir=tmp_path / "out",
            unity_argv=(sys.executable, str(_fake_unity(tmp_path))),
        )
    assert "不像 Unity 工程" in excinfo.value.message


# --------------------------------------------------------------------------- #
# 正常返回
# --------------------------------------------------------------------------- #
def test_bake_happy_path_produces_five_files_and_cleans_up(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    outcome = _bake(plan)

    assert outcome.ok, outcome.lines()
    assert outcome.group == GROUP
    assert outcome.item == ITEM
    assert len(outcome.files) == 5
    assert {p.name for p in outcome.files} == {f"{GROUP}.gts"} | {
        f"{GROUP}{shard}.gtp" for shard in SHARDS
    }
    assert len(vt_bake.pool_files(plan.out_dir)) == 5
    # 收尾：工程临时件没了、VT 状态文件回原样、没有残留 Unity
    assert not plan.asset_dir.exists()
    assert not plan.asset_dir.with_name(plan.asset_dir.name + ".meta").exists()
    assert plan.vt_state_path.read_text(encoding="utf-8") == (
        '{"m_VirtualTexturingEnabled": false, "m_VirtualTexturingEnabledInitialized": true}'
    )
    assert outcome.unity_left == 0
    assert [stage.stage for stage in outcome.stages] == ["setup", "build", "restore"]
    assert all(stage.verdict_ok for stage in outcome.stages)
    # ★ 三合一的机械判据：**只起了一趟** Unity，三站共用它
    assert _invocations(plan) == [vt_bake.METHOD_ONE]
    assert {stage.session for stage in outcome.stages} == {vt_bake.SESSION_ONE}
    # 每站的秒数是从它自己写进读数的 seconds= 读回来的（不是把整趟墙钟印三遍）
    assert [stage.seconds for stage in outcome.stages] == [1.5, 2.5, 3.5]


def test_bake_goes_red_when_the_reading_group_differs_from_the_pool_file_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读数里的 group 与落盘 .gts 的名字对不上 = 红（两处独立来源必须一致）。

    假编辑器把读数里的 group 做成别的值、池子文件名仍是 FAKE_GROUP ——
    读数 VERDICT 照写 bake-ok，**这一条必须由产品自己判红**（不许只信 VERDICT）。
    """
    monkeypatch.setenv("FAKE_GROUP", GROUP)
    monkeypatch.setenv("FAKE_BUNDLE_GROUP", OTHER_GROUP)
    plan = _plan(tmp_path)
    outcome = _bake(plan)
    assert outcome.group == OTHER_GROUP
    assert not outcome.ok
    hit = [item for item in outcome.problems if "对不上" in item or "自相矛盾" in item]
    assert hit, outcome.problems


def test_two_groups_are_both_harvested_each_with_its_own_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J2 正控：一趟烘出**两套**池子时，两套都收下、**各自有账**。

    旧行为是「.gts 应该恰好 1 个」当场报红（两套文件其实都烘出来了）。
    现场：`_work/vtb1/extreme/pool/` —— 两颗材质同 shader、同两层，只因 Mask 内容不同
    就拿到两个 group，读数里 `mat0_group=243ba786…` / `mat1_group=48bd9cef…`。
    """
    monkeypatch.setenv("FAKE_EXTRA_GROUP", OTHER_GROUP)
    plan = _plan(tmp_path, count=2)
    outcome = _bake(plan)

    assert outcome.ok, outcome.lines()
    assert len(outcome.pools) == 2
    assert {got.group for got in outcome.pools} == {GROUP, OTHER_GROUP}
    assert all(got.ok for got in outcome.pools)
    assert len(outcome.files) == 10
    assert len(vt_bake.pool_files(plan.out_dir)) == 10
    for got in outcome.pools:                     # 每套自己的账：1 个 .gts + 4 个 .gtp
        assert got.gts is not None and len(got.shards) == 4
        assert "5 个文件" in got.account()
    # 逐材质那串 group：CLI 的"下一步"就是按它给 --group 的
    assert outcome.material_groups == (GROUP, OTHER_GROUP)


def test_a_group_missing_its_first_shard_is_red_and_collects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J2 负控：两套里有一套缺第一片 ⇒ 红，且**一个文件都不收**（--out 保持空的）。"""
    monkeypatch.setenv("FAKE_EXTRA_GROUP", OTHER_GROUP)
    monkeypatch.setenv("FAKE_DROP_SHARD", OTHER_GROUP)
    plan = _plan(tmp_path, count=2)
    outcome = _bake(plan)

    assert not outcome.ok
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()
    assert any("第一片" in item for item in outcome.problems), outcome.problems
    assert any("池子组 " + OTHER_GROUP in item for item in outcome.problems), outcome.problems


# --------------------------------------------------------------------------- #
# 超时
# --------------------------------------------------------------------------- #
def test_bake_times_out_kills_the_tree_and_produces_no_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "timeout")
    plan = _plan(tmp_path, only="setup", timeout_setup=2.0)
    outcome = _bake(plan)

    assert not outcome.ok
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()
    setup = outcome.stages[0]
    assert setup.stage == "setup"
    assert setup.timed_out
    assert any("硬超时" in item for item in outcome.problems), outcome.problems
    # 工程临时件照样要清掉（finally 里那一步不许因为超时被跳过）
    assert not plan.asset_dir.exists()


# --------------------------------------------------------------------------- #
# 日志里有 error CS -> 不许假绿
# --------------------------------------------------------------------------- #
def test_error_cs_in_the_log_goes_red_even_though_the_verdict_says_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "error_cs")
    plan = _plan(tmp_path)
    outcome = _bake(plan)

    # 三合一只有**一份**日志（一趟进程）⇒ error CS 记在第一站，红一次就够
    hit = [stage for stage in outcome.stages if stage.cs_errors]
    assert hit, "日志里有 error CS，却没有任何一站立起来"
    assert hit[0].verdict_ok  # 读数本身是「通过」的 —— 假绿就藏在这里
    assert not hit[0].ok
    assert not outcome.ok
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()
    assert any("error CS" in item for item in outcome.problems), outcome.problems


# --------------------------------------------------------------------------- #
# CLI 三档退出码
# --------------------------------------------------------------------------- #
def _cli_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "project": str(tmp_path / "proj"),
        "normal": str(tmp_path / "normal.png"),
        "mask": str(tmp_path / "mask.png"),
        "out": str(tmp_path / "out"),
        "unity": None,
        "player_dir": str(tmp_path / "player"),
        "timeout_setup": 60.0,
        "timeout_build": 60.0,
        "timeout_restore": 60.0,
        "only": "all",
        "split": False,
        "keep": False,
        "json_out": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cli_returns_2_on_a_bad_source_image(tmp_path: Path, capsys) -> None:
    _make_project(tmp_path)
    _make_image(tmp_path, "mask.png")
    code = vt_bake_cli.run(_cli_args(tmp_path))
    assert code == vt_bake_cli.EXIT_UNUSABLE
    assert "错误" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_returns_0_and_prints_group_and_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _make_image(tmp_path, "normal.png")
    _make_image(tmp_path, "mask.png")
    _make_project(tmp_path)
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)
    code = vt_bake_cli.run(_cli_args(tmp_path))
    out = capsys.readouterr().out
    assert code == vt_bake_cli.EXIT_OK
    assert GROUP in out and ITEM in out
    assert "下一步" in out


def test_cli_returns_1_when_the_build_goes_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _make_image(tmp_path, "normal.png")
    _make_image(tmp_path, "mask.png")
    _make_project(tmp_path)
    monkeypatch.setenv("FAKE_MODE", "error_cs")
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)
    code = vt_bake_cli.run(_cli_args(tmp_path))
    captured = capsys.readouterr()
    assert code == vt_bake_cli.EXIT_RED
    assert "有红" in captured.err

# --------------------------------------------------------------------------- #
# N 颗材质 -> 同一个 group 下 N 个 item（Q1 / Q2 / Q5）
# --------------------------------------------------------------------------- #
def _multi_plan(tmp_path: Path, count: int) -> vt_bake.BakePlan:
    return vt_bake.plan_bake(
        project=_make_project(tmp_path),
        pairs=_pairs(tmp_path, count),
        out_dir=tmp_path / "out",
        unity_argv=(sys.executable, str(_fake_unity(tmp_path))),
        player_dir=tmp_path / "player",
    )


def test_a_single_pair_is_the_same_plan_whether_written_one_way_or_the_other(
    tmp_path: Path,
) -> None:
    """pairs 那一路不是第二条实现：一组源图两种写法得到**逐字相同**的计划。"""
    project = _make_project(tmp_path)
    normal = _make_image(tmp_path, "normal.png")
    mask = _make_image(tmp_path, "mask.png")
    shared: dict[str, object] = {
        "project": project,
        "out_dir": tmp_path / "out",
        "unity_argv": (sys.executable, str(_fake_unity(tmp_path))),
    }
    one = vt_bake.plan_bake(normal=normal, mask=mask, **shared)  # type: ignore[arg-type]
    two = vt_bake.plan_bake(pairs=[(normal, mask)], **shared)  # type: ignore[arg-type]
    assert one.sources == two.sources
    assert one.material_count == two.material_count == 1


def test_the_second_material_is_named_by_index_and_they_share_one_shader(
    tmp_path: Path,
) -> None:
    """第 0 颗沿用老名字（N=1 兼容），第 i 颗加下标；N 颗**共用同一颗 shader**。"""
    plan = _multi_plan(tmp_path, 3)
    assert plan.material_count == 3
    assert [s.normal_name for s in plan.sources] == [
        "src_normal.png", "src_normal1.png", "src_normal2.png",
    ]
    assert [s.mask_name for s in plan.sources] == [
        "src_mask.png", "src_mask1.png", "src_mask2.png",
    ]
    assert [s.material_name for s in plan.sources] == [
        "VtBake.mat", "VtBake1.mat", "VtBake2.mat",
    ]
    # 材质在包里的 m.name（不带扩展名）—— 产品的读回就是按它对号入座
    assert [s.asset_name for s in plan.sources] == ["VtBake", "VtBake1", "VtBake2"]
    # 一颗 shader：三个材质落点不同，但 shader 只有一个（同组的前提）
    assert len({plan.shader_path}) == 1
    assert [plan.material_path(i).name for i in range(3)] == [
        "VtBake.mat", "VtBake1.mat", "VtBake2.mat",
    ]


def test_stage_assets_writes_one_shader_and_every_pair(tmp_path: Path) -> None:
    plan = _multi_plan(tmp_path, 2)
    written = vt_bake.stage_assets(plan)
    assert [path.name for path in written] == [
        "VtBake.shader",
        "src_normal.png", "src_mask.png",
        "src_normal1.png", "src_mask1.png",
        "VtBakeSetup.cs", "VtBakeBuild.cs", "VtBakeOne.cs",
    ]
    assert written[1].read_bytes() == plan.sources[0].normal.read_bytes()
    assert written[3].read_bytes() == plan.sources[1].normal.read_bytes()
    assert written[4].read_bytes() == plan.sources[1].mask.read_bytes()


def test_n1_still_writes_exactly_the_old_file_set(tmp_path: Path) -> None:
    """向后兼容的机械判据：N=1 时工程里写下的临时件集合 = 老集合 + 三合一那颗脚本。

    ⛔ 源图 / shader / 材质名一个字都没动（那才决定池子）；本轮只多了一颗
    VtBakeOne.cs —— 它是"一趟会话跑三站"的入口，调的还是前两颗的站内主体。
    """
    plan = _plan(tmp_path)
    written = vt_bake.stage_assets(plan)
    assert [path.name for path in written] == [
        "VtBake.shader", "src_normal.png", "src_mask.png",
        "VtBakeSetup.cs", "VtBakeBuild.cs", "VtBakeOne.cs",
    ]


def test_two_sources_give_one_pool_and_two_different_items(tmp_path: Path) -> None:
    plan = _plan(tmp_path, count=2)
    outcome = _bake(plan)

    assert outcome.ok, outcome.lines()
    assert outcome.group == GROUP
    assert len(outcome.items) == 2
    assert outcome.items[0] == ITEM
    assert outcome.items[0] != outcome.items[1]  # Q2：两颗材质不许写成同一个 item
    assert outcome.item == outcome.items[0]      # 老字段 = 第一颗
    assert len(outcome.files) == 5               # **一个**池子（5 个文件）
    assert len(vt_bake.pool_files(plan.out_dir)) == 5
    assert [stage.stage for stage in outcome.stages] == ["setup", "build", "restore"]
    assert not plan.asset_dir.exists()
    assert outcome.unity_left == 0


def test_three_sources_give_three_different_items(tmp_path: Path) -> None:
    plan = _plan(tmp_path, count=3)
    outcome = _bake(plan)
    assert outcome.ok, outcome.lines()
    assert len(outcome.items) == 3
    assert len(set(outcome.items)) == 3


def test_multi_item_reading_cross_checks_each_material(tmp_path: Path) -> None:
    """逐材质那几条（mat<i>_item）与按序那一串必须逐字相同（两种独立来源）。"""
    reading = vt_bake.parse_reading(
        "group=" + GROUP + "\n"
        "item=" + ITEM + "\n"
        "item_bundle=" + ITEM + "\n"
        "mat0_item=" + ITEM + "\n"
        "item_bundle=" + OTHER_GROUP + "\n"
        "mat1_item=" + OTHER_GROUP + "\n"
        "VERDICT bake-ok\n"
    )
    plan = _plan(tmp_path, count=2)
    assert vt_bake.check_items(plan, reading, tuple(reading.many("item_bundle"))) == ()
    # 正控：把逐材质那条改掉，尺子必须红
    bad = vt_bake.parse_reading(
        "group=" + GROUP + "\n"
        "item=" + ITEM + "\n"
        "item_bundle=" + ITEM + "\n"
        "mat0_item=" + ITEM + "\n"
        "item_bundle=" + OTHER_GROUP + "\n"
        "mat1_item=" + ITEM + "\n"
        "VERDICT bake-ok\n"
    )
    problems = vt_bake.check_items(plan, bad, tuple(bad.many("item_bundle")))
    assert any("自相矛盾" in item for item in problems), problems


def test_two_materials_sharing_one_item_is_red(tmp_path: Path, monkeypatch) -> None:
    """Q2 的正控：两颗材质共用一个 item = 后一颗把前一颗挤掉了，必须红。"""
    monkeypatch.setenv("FAKE_SAME_ITEM", "1")
    plan = _plan(tmp_path, count=2)
    outcome = _bake(plan)
    assert not outcome.ok
    assert any("重复" in item for item in outcome.problems), outcome.problems


def test_a_material_without_an_item_is_red(tmp_path: Path, monkeypatch) -> None:
    """少一条 item = 有一颗材质没进池子，必须红。"""
    monkeypatch.setenv("FAKE_ITEMS", ITEM)  # 只写了一条，而材质有两颗
    plan = _plan(tmp_path, count=2)
    outcome = _bake(plan)
    assert not outcome.ok
    assert any("材质有 2 颗" in item for item in outcome.problems), outcome.problems


def test_multi_item_pool_ruler_wants_exactly_one_group(tmp_path: Path) -> None:
    """N>1 的池子尺子：还是**只该有一个 group**，但分片个数不钉死。"""
    directory = tmp_path / "mpool"
    directory.mkdir()
    files = [directory / f"{GROUP}.gts"]
    files += [directory / f"{GROUP}{shard}.gtp" for shard in SHARDS]
    for path in files:
        path.write_bytes(b"x" * (GTS_BYTES if path.suffix == ".gts" else 16))
    group, problems = vt_bake.check_pool_shape(files, items=2)
    assert problems == ()
    assert group == GROUP

    # 只有 0_1 一片也认（游戏自己的小组就是 3 / 4 / 5 个文件）
    few = [files[0], files[1]]
    _group, problems = vt_bake.check_pool_shape(few, items=2)
    assert problems == ()

    # 正控 ①：多了一个**别的组**的 .gts
    second = directory / f"{OTHER_GROUP}.gts"
    second.write_bytes(b"x" * GTS_BYTES)
    _group, problems = vt_bake.check_pool_shape(vt_bake.pool_files(directory), items=2)
    assert problems
    assert any("恰好 1 个" in item for item in problems), problems
    second.unlink()

    # 正控 ②：混进一个别的组的分片 —— 它自成一"组"、而那一组没有 .gts ⇒ 红且点名
    stray = directory / f"{OTHER_GROUP}0_1.gtp"
    stray.write_bytes(b"x" * 16)
    _group, problems = vt_bake.check_pool_shape(vt_bake.pool_files(directory), items=2)
    assert problems
    assert any(OTHER_GROUP in item for item in problems), problems
    stray.unlink()

    # 正控 ③：连第一片都没有
    files[1].unlink()
    _group, problems = vt_bake.check_pool_shape(vt_bake.pool_files(directory), items=2)
    assert any("缺" in item for item in problems), problems


# --------------------------------------------------------------------------- #
# N 的前置检查（Q4：exit 2，且一个池子文件都不产出）
# --------------------------------------------------------------------------- #
def test_cli_returns_2_when_the_normal_and_mask_lists_have_different_lengths(
    tmp_path: Path, capsys
) -> None:
    _make_project(tmp_path)
    one = _make_image(tmp_path, "normal.png")
    two = _make_image(tmp_path, "normal1.png")
    mask = _make_image(tmp_path, "mask.png")
    code = vt_bake_cli.run(
        _cli_args(tmp_path, normal=[str(one), str(two)], mask=[str(mask)])
    )
    assert code == vt_bake_cli.EXIT_UNUSABLE
    assert "成对" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "proj" / "Assets" / vt_bake.WORK_SUBDIR).exists()


def test_cli_returns_2_when_the_second_pair_has_a_missing_source(
    tmp_path: Path, capsys
) -> None:
    """两组里有一组的源图不存在 -> 指名道姓地报 --mask[1]，且什么都不写。"""
    _make_project(tmp_path)
    normal0 = _make_image(tmp_path, "normal.png")
    mask0 = _make_image(tmp_path, "mask.png")
    normal1 = _make_image(tmp_path, "normal1.png")
    code = vt_bake_cli.run(
        _cli_args(
            tmp_path,
            normal=[str(normal0), str(normal1)],
            mask=[str(mask0), str(tmp_path / "nope.png")],
        )
    )
    assert code == vt_bake_cli.EXIT_UNUSABLE
    err = capsys.readouterr().err
    assert "--mask[1]" in err
    assert not (tmp_path / "out").exists()
    assert vt_bake.pool_files(tmp_path / "out") == ()


def test_plan_bake_refuses_pairs_and_normal_at_the_same_time(tmp_path: Path) -> None:
    with pytest.raises(AssetError) as excinfo:
        vt_bake.plan_bake(
            project=_make_project(tmp_path),
            normal=_make_image(tmp_path, "normal.png"),
            mask=_make_image(tmp_path, "mask.png"),
            pairs=_pairs(tmp_path, 2),
            out_dir=tmp_path / "out",
            unity_argv=(sys.executable, str(_fake_unity(tmp_path))),
        )
    assert "只能给一种" in excinfo.value.message
    assert not (tmp_path / "out").exists()


def test_cli_returns_0_and_prints_one_line_per_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _make_project(tmp_path)
    normal = [str(_make_image(tmp_path, "normal.png")),
              str(_make_image(tmp_path, "normal1.png"))]
    mask = [str(_make_image(tmp_path, "mask.png")),
            str(_make_image(tmp_path, "mask1.png"))]
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)
    code = vt_bake_cli.run(_cli_args(tmp_path, normal=normal, mask=mask))
    out = capsys.readouterr().out
    assert code == vt_bake_cli.EXIT_OK
    assert "item[1]" in out
    assert "VtBake1.mat" in out
    assert GROUP in out


def test_cli_split_flag_goes_back_to_three_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """--split 是 A/B 对照那一档：命令行原样能跑，且确实起三趟（不是只改了个文档）。"""
    _make_project(tmp_path)
    normal = [str(_make_image(tmp_path, "normal.png"))]
    mask = [str(_make_image(tmp_path, "mask.png"))]
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)
    code = vt_bake_cli.run(_cli_args(tmp_path, normal=normal, mask=mask, split=True))
    assert code == vt_bake_cli.EXIT_OK
    capsys.readouterr()
    history = (tmp_path / "out" / "_vtbake" / "unity-invocations.txt").read_text(encoding="utf-8")
    assert history.split() == ["VtBakeSetup.Run", "VtBakeBuild.Run", "VtBakeSetup.Restore"]


def test_cli_json_document_carries_one_entry_per_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _make_project(tmp_path)
    normal = [str(_make_image(tmp_path, "normal.png")),
              str(_make_image(tmp_path, "normal1.png"))]
    mask = [str(_make_image(tmp_path, "mask.png")),
            str(_make_image(tmp_path, "mask1.png"))]
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)
    code = vt_bake_cli.run(_cli_args(tmp_path, normal=normal, mask=mask, json_out=True))
    payload = json.loads(capsys.readouterr().out)
    assert code == vt_bake_cli.EXIT_OK
    assert len(payload["items"]) == 2
    assert payload["items"][0] != payload["items"][1]
    assert [entry["name"] for entry in payload["materials"]] == ["VtBake.mat", "VtBake1.mat"]
    assert len(payload["sources"]) == 2
    # 三合一：三站共用一趟会话 —— --json 里也要看得见（merged + 每站的 session）
    assert payload["merged"] is True
    assert {entry["session"] for entry in payload["stages"]} == {vt_bake.SESSION_ONE}

def test_error_cs_with_two_materials_is_still_red(tmp_path: Path, monkeypatch) -> None:
    """N>1 时 error CS 照样判红，且不收割池子（假绿藏不住）。"""
    monkeypatch.setenv("FAKE_MODE", "error_cs")
    plan = _plan(tmp_path, count=2)
    outcome = _bake(plan)
    hit = [stage for stage in outcome.stages if stage.cs_errors]
    assert hit and hit[0].verdict_ok
    assert not outcome.ok
    assert outcome.items == ()
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()


def test_timeout_with_two_materials_produces_no_pool(tmp_path: Path, monkeypatch) -> None:
    """N>1 时超时照样杀进程树、一个池子文件都不产出、工程临时件照样清掉。"""
    monkeypatch.setenv("FAKE_MODE", "timeout")
    plan = _plan(tmp_path, count=2, only="setup", timeout_setup=2.0)
    outcome = _bake(plan)
    assert not outcome.ok
    assert outcome.items == ()
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()
    assert outcome.stages[0].timed_out
    assert not plan.asset_dir.exists()


# --------------------------------------------------------------------------- #
# 三合一：一趟会话跑完三站（K1 的机械判据 / K3 的离线半边）
# --------------------------------------------------------------------------- #
def test_split_mode_is_still_three_processes(tmp_path: Path) -> None:
    """对照档：--split（merged=False）照旧三趟 —— 三合一的「一趟」才有比较基准。"""
    plan = _plan(tmp_path, merged=False)
    outcome = _bake(plan)
    assert outcome.ok, outcome.lines()
    assert _invocations(plan) == ["VtBakeSetup.Run", "VtBakeBuild.Run", "VtBakeSetup.Restore"]
    assert [stage.stage for stage in outcome.stages] == ["setup", "build", "restore"]
    # 一次进程一站：谁都不带会话标识（三合一才带）
    assert all(stage.session == "" for stage in outcome.stages)


def test_only_still_runs_one_station_per_process(tmp_path: Path) -> None:
    """--only setup 照旧只管一站、只起一趟（不因为默认三合一而变）。"""
    plan = _plan(tmp_path, only="setup")
    outcome = _bake(plan)
    assert [stage.stage for stage in outcome.stages] == ["setup"]
    assert _invocations(plan) == ["VtBakeSetup.Run"]


def test_a_session_that_dies_midway_still_gets_a_fallback_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K3 的离线半边：三合一那一趟**死在半路** ⇒ 产品兜底再起一趟专跑复原。

    真机那一半（BuildPlayer 之前抛异常）见 _work/vtbake/README.md 里的 K3 正控；
    这一条管的是另一种死法：进程根本没走到复原那一步，工程不许留在半路。
    """
    monkeypatch.setenv("FAKE_MODE", "crash_after_setup")
    plan = _plan(tmp_path)
    outcome = _bake(plan)

    assert not outcome.ok
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()
    # 第一趟死在半路（没写 restore 读数）⇒ 产品自己又起了一趟，只跑复原
    assert _invocations(plan) == [vt_bake.METHOD_ONE, "VtBakeSetup.Restore"]
    restore = [stage for stage in outcome.stages if stage.stage == "restore"][-1]
    assert restore.verdict_ok and restore.session == ""
    # 收尾三件事照样成立：工程临时件删掉（含 .meta）、VT 状态文件回原样、无残留进程
    assert not plan.asset_dir.exists()
    assert not plan.asset_dir.with_name(plan.asset_dir.name + ".meta").exists()
    assert plan.vt_state_path.read_text(encoding="utf-8") == (
        '{"m_VirtualTexturingEnabled": false, "m_VirtualTexturingEnabledInitialized": true}'
    )
    assert outcome.unity_left == 0


def test_one_session_timeout_kills_the_tree_and_still_tries_to_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三合一超时：杀进程树、判红、不产出池子；随后兜底再起一趟复原。"""
    monkeypatch.setenv("FAKE_MODE", "timeout")
    plan = _plan(tmp_path, timeout_setup=1.0, timeout_build=0.1, timeout_restore=0.1)
    outcome = _bake(plan)

    assert not outcome.ok
    assert outcome.files == ()
    assert vt_bake.pool_files(plan.out_dir) == ()
    assert _invocations(plan) == [vt_bake.METHOD_ONE, "VtBakeSetup.Restore"]
    # 三站都没写成读数 ⇒ 三站都记「超时」；已经在读数里报过到的那几站才不连坐
    merged_stages = [s for s in outcome.stages if s.session == vt_bake.SESSION_ONE]
    assert [s.stage for s in merged_stages] == ["setup", "build", "restore"]
    assert all(s.timed_out for s in merged_stages)
    assert any("硬超时" in item for item in outcome.problems), outcome.problems
    assert not plan.asset_dir.exists()


def test_keep_skips_the_restore_station_inside_the_one_session(tmp_path: Path) -> None:
    """--keep：三合一那一趟**跳过复原**（与老的三趟写法同一个语义）。"""
    plan = _plan(tmp_path, keep=True)
    outcome = _bake(plan)
    assert outcome.ok, outcome.lines()
    assert _invocations(plan) == [vt_bake.METHOD_ONE]
    assert [stage.stage for stage in outcome.stages] == ["setup", "build"]
    assert plan.asset_dir.exists()          # --keep：工程临时件留着（调试用）


def test_an_ok_one_session_never_starts_a_second_process(tmp_path: Path) -> None:
    """正常路径的**唯一**判据：读数三站全绿时，Unity 只被起了一趟（不多不少）。"""
    plan = _plan(tmp_path)
    outcome = _bake(plan)
    assert outcome.ok, outcome.lines()
    assert _invocations(plan) == [vt_bake.METHOD_ONE]
    assert len(outcome.stages) == 3

# --------------------------------------------------------------------------- #
# J2：按 group 分组收产物（`pool_sets` 这把新尺子）
# --------------------------------------------------------------------------- #
def _group_files(
    directory: Path, group: str, *, shards: Sequence[str] = SHARDS, gts: int = GTS_BYTES
) -> list[Path]:
    """造一套池子（默认 1 个 .gts + 4 个分片）。"""
    directory.mkdir(exist_ok=True)
    written = [directory / f"{group}.gts"]
    written += [directory / f"{group}{shard}.gtp" for shard in shards]
    for path in written:
        path.write_bytes(b"x" * (gts if path.suffix == ".gts" else 16))
    return written


def test_pool_sets_groups_by_group_and_a_known_good_pair_is_green(tmp_path: Path) -> None:
    """两套**完整**的池子：分得开、各自绿、各自 5 个文件（J2 的正控）。"""
    directory = tmp_path / "two"
    _group_files(directory, GROUP)
    _group_files(directory, OTHER_GROUP)
    sets = vt_bake.pool_sets(vt_bake.pool_files(directory))
    assert [got.group for got in sets] == sorted([GROUP, OTHER_GROUP])
    assert all(got.ok for got in sets)
    assert [len(got.files) for got in sets] == [5, 5]
    assert all(got.gts is not None and len(got.shards) == 4 for got in sets)


def test_pool_sets_goes_red_per_group_and_names_the_group(tmp_path: Path) -> None:
    """每一套**自己**过尺子：缺第一片 / 页表太小 / 只有分片没有 .gts —— 各自点名，不连坐。"""
    directory = tmp_path / "mixed"
    orphan_group = "00112233445566778899aabbccddeeff"
    _group_files(directory, GROUP, shards=("2", "3", "4"))          # 缺 0_1（第一片）
    _group_files(directory, OTHER_GROUP, gts=16)                    # 页表太小
    (directory / f"{orphan_group}2.gtp").write_bytes(b"x" * 16)     # 只有一片，没有 .gts
    sets = {got.group: got for got in vt_bake.pool_sets(vt_bake.pool_files(directory))}
    assert set(sets) == {GROUP, OTHER_GROUP, orphan_group}
    assert any("第一片" in item for item in sets[GROUP].problems), sets[GROUP].problems
    assert any("还小" in item for item in sets[OTHER_GROUP].problems), sets[OTHER_GROUP].problems
    assert any(".gts 应该恰好 1 个" in item for item in sets[orphan_group].problems)
    assert vt_bake.check_pool_shape(vt_bake.pool_files(directory), items=2)[0] is None


# --------------------------------------------------------------------------- #
# J3：源图档位体检（Mask 的 R = 金属度；参照 = 游戏自用 p50 0.42 / p95 0.86）
# --------------------------------------------------------------------------- #
def _make_level(tmp_path: Path, name: str, value: int, *, alpha: int | None = None) -> Path:
    """造一张**整片同一个值**的 Mask 源图（R = value；给了 alpha 就带 A 通道）。"""
    path = tmp_path / name
    if alpha is None:
        Image.new("RGB", (128, 128), (value, 128, 128)).save(path)
    else:
        Image.new("RGBA", (128, 128), (value, 128, 128, alpha)).save(path)
    return path


def test_level_ruler_can_go_red_on_a_known_out_of_range_mask(tmp_path: Path) -> None:
    """★ 正控：R = 0.902（kb/22 §5 那套「车体可见像素 26,239」的档位）⇒ **必须报红**。"""
    got = vt_bake.source_levels(_make_level(tmp_path, "m230.png", 230), "--mask")
    assert (got.r50, got.r95, got.rmax) == (230, 230, 230)
    assert got.over and got.problems()
    assert "档位越界" in got.problems()[0]
    assert any("★ 红" in line for line in got.lines())


def test_level_ruler_is_green_inside_the_game_own_range(tmp_path: Path) -> None:
    """负控：游戏自用档位内 ⇒ 绿（含边界：2 级容差 —— 221 绿 / 222 红）。"""
    for name, value in (("m45.png", 115), ("m05.png", 13), ("m219.png", 219), ("m221.png", 221)):
        got = vt_bake.source_levels(_make_level(tmp_path, name, value), "--mask")
        assert not got.over, got.lines()
        assert got.problems() == ()
    assert vt_bake.source_levels(_make_level(tmp_path, "m222.png", 222), "--mask").over
    assert vt_bake.MASK_R_LIMIT_BYTE == 221


def test_level_a_and_a_missing_alpha_are_notes_not_red(tmp_path: Path) -> None:
    """A（光滑度）越界 / 没有 alpha ⇒ **只出备注**（设计稿 §2.7 第①轮那次 A=255 是用户判过的）。"""
    high = vt_bake.source_levels(_make_level(tmp_path, "a255.png", 115, alpha=255), "--mask")
    assert high.has_alpha and high.a_over and not high.over
    assert high.problems() == ()
    assert any("高于游戏自用" in line for line in high.lines())
    flat = vt_bake.source_levels(_make_level(tmp_path, "rgb.png", 115), "--mask")
    assert not flat.has_alpha and flat.a95 is None
    assert any("没有 alpha 通道" in line for line in flat.lines())


def test_plan_bake_records_one_level_reading_per_mask(tmp_path: Path) -> None:
    plan = _plan(tmp_path, count=2)
    assert [got.label for got in plan.levels] == ["--mask[0]", "--mask[1]"]
    assert plan.level_problems == ()


def test_a_red_level_stops_before_any_unity_session(tmp_path: Path) -> None:
    """体检红了 ⇒ **一趟 Unity 都不起**、一个池子文件都不产出、连工程临时件都不写。"""
    plan = _plan(tmp_path, mask=_make_level(tmp_path, "mask90.png", 230))
    outcome = _bake(plan)
    assert not outcome.ok
    assert outcome.files == ()
    assert _invocations(plan) == []
    assert any("档位越界" in item for item in outcome.problems), outcome.problems
    assert not plan.asset_dir.exists()


def test_allow_levels_downgrades_the_red_to_a_note(tmp_path: Path) -> None:
    """放行口（strict_levels=False）：越界降级成备注，烘照跑。"""
    plan = _plan(tmp_path, mask=_make_level(tmp_path, "mask90.png", 230), strict_levels=False)
    assert plan.level_problems == ()
    assert any("档位体检放行" in note for note in plan.notes), plan.notes
    outcome = _bake(plan)
    assert outcome.ok, outcome.lines()


def test_cli_exit_1_and_no_unity_on_a_red_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """CLI 那一档：红 ⇒ 退出码 1、读数印出来、一行 Unity 都没起；--allow-levels 才放行。"""
    _make_project(tmp_path)
    _make_image(tmp_path, "normal.png")
    bad = str(_make_level(tmp_path, "mask90.png", 230))
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)

    code = vt_bake_cli.run(_cli_args(tmp_path, mask=bad))
    captured = capsys.readouterr()
    assert code == vt_bake_cli.EXIT_RED
    assert "档位越界" in captured.err
    assert "p95 0.902" in captured.out           # 两个 p95 数字都印出来了
    assert "0.86" in captured.out
    assert not (tmp_path / "out" / "_vtbake" / "unity-invocations.txt").exists()

    code = vt_bake_cli.run(_cli_args(tmp_path, mask=bad, allow_levels=True))
    assert code == vt_bake_cli.EXIT_OK
    assert (tmp_path / "out" / "_vtbake" / "unity-invocations.txt").is_file()


def test_cli_prints_one_group_per_material_when_two_pools_come_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """两套池子时"下一步"要**按材质给各自的 group**（⛔ 别拿第一套的 group 糊第二颗）。"""
    _make_project(tmp_path)
    monkeypatch.setenv("FAKE_EXTRA_GROUP", OTHER_GROUP)
    normal = [str(_make_image(tmp_path, "normal.png")),
              str(_make_image(tmp_path, "normal1.png"))]
    mask = [str(_make_image(tmp_path, "mask.png")),
            str(_make_image(tmp_path, "mask1.png"))]
    argv = (sys.executable, str(_fake_unity(tmp_path)))
    real_plan = vt_bake.plan_bake

    def fake_plan(**kwargs: object) -> vt_bake.BakePlan:
        kwargs.pop("unity", None)
        return real_plan(unity_argv=argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(vt_bake, "plan_bake", fake_plan)
    monkeypatch.setattr(vt_bake, "count_unity_processes", lambda: 0)
    code = vt_bake_cli.run(_cli_args(tmp_path, normal=normal, mask=mask))
    out = capsys.readouterr().out
    assert code == vt_bake_cli.EXIT_OK
    assert "池子套数" in out
    assert f"--group {GROUP} " in out
    assert f"--group {OTHER_GROUP} " in out





