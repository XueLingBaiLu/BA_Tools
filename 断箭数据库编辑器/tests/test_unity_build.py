"""`core/assets/unity_build.py` 的用例 —— ⛔ **不起 Unity**（假的 Popen ＋ 临时工程树）。

为什么这些断言值得写：导入器 GUI 的四步链全靠这层的**纯函数**拼命令 / 读报告 ——
命令拼错（车体炮塔判据混了、漏 `--asset-path`）会让 `check-model` 红绿不一，
所以既比字符串，也把 argv 交给真正的 CLI 解析器（`_build_parser().parse_args`）：
后者能红出「我拼的开关命令行里根本不存在」。

口径（都是 2026-10-25 实测出来的，改坏了要能红）：
  · 车体 `--unit-prefab` / 炮塔 `--turret-prefab` —— 两条判据不许混用；
  · `check-model` 必须带 `--asset-path`（命名契约 `unit_root.name` 拿它比顶层节点名）；
  · 炮塔真件 prefab 在 `ModelPrefabs/<国>/Turrets/<键>/<键>.prefab`，
    ⛔ 不能复用 `unity_prefab.find_prefab`（它先命中 `<键>_Base.prefab` ⇒ 炮塔被换成车体）；
  · `report.json` 的形状：`ok` 必须有且是布尔；`ok:true` 还要有 `bundle` / `bundle_bytes`；
  · `run()` 只起进程不判红绿 —— 退出码非 0 也**正常返回**（判据是 report.ok）。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ba_mod_maker.core.assets import unity_build as ub
from ba_mod_maker.core.errors import AssetError

CHASSIS_PREFAB = "Assets/Resources_moved/ModelPrefabs/RU/RU_T72B_Base.prefab"
TURRET_PREFAB = "Assets/Resources_moved/ModelPrefabs/RU/Turrets/RU_T72B/RU_T72B.prefab"


def _write_report(tmp_path: Path, payload: dict, name: str = "report.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _ok_payload() -> dict:
    return {
        "ok": True,
        "unity": "6000.3.24f1",
        "prefab": "Assets/_unitbuild/20261025-105941/RU_T72B_Base.prefab",
        "prefab_source": CHASSIS_PREFAB,
        "bundle": "D:/work/unitybuild/out-py3" + chr(92) + "ru_t72b_base",
        "bundle_bytes": 36355912,
        "renderers": 13,
        "elapsed_ms": 73077,
        "parts": [
            {"node": "Chassis", "before_vertices": 44164, "after_vertices": 44169,
             "before_indices": 119484, "after_indices": 119484, "lod_siblings": 2,
             "bones": 38, "has_uv1": False, "tangents": "recalculated", "skinned": True},
        ],
        "known_gaps": [{"node": "BaseMobility", "missing": ["Color/4", "TexCoord2/2"],
                         "channels_real": "x", "channels_built": "y"}],
        "warnings": ["part Chassis：LOD3 同槽位 0 的 lod_1x1x1 是 MeshRenderer"],
    }


# --------------------------------------------------------------------------- #
# read_report：只校验形状，⛔ 不判红绿
# --------------------------------------------------------------------------- #


def test_read_report_ok_keeps_parts_and_fills_defaults(tmp_path: Path) -> None:
    payload = _ok_payload()
    del payload["known_gaps"]
    del payload["warnings"]
    report = ub.read_report(_write_report(tmp_path, payload))
    assert report["ok"] is True
    assert report["parts"] == payload["parts"]
    assert report["known_gaps"] == []
    assert report["warnings"] == []
    assert report["bundle_bytes"] == 36355912
    assert Path(report["path"]).name == "report.json"


def test_read_report_failure_is_returned_not_raised(tmp_path: Path) -> None:
    path = _write_report(tmp_path, {"ok": False, "error": "Exception: boom",
                                    "stack": "at UnitBuild.Run ()", "parts": []})
    report = ub.read_report(path)
    assert report["ok"] is False
    assert "boom" in report["error"]


def test_read_report_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(tmp_path / "report.json")
    assert "还没生成" in str(exc.value)


def test_read_report_missing_ok_field(tmp_path: Path) -> None:
    path = _write_report(tmp_path, {"parts": []})
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(path)
    assert "缺 `ok`" in str(exc.value)


def test_read_report_ok_must_be_bool(tmp_path: Path) -> None:
    path = _write_report(tmp_path, {"ok": "true", "bundle": "D:/x", "bundle_bytes": 1})
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(path)
    assert "不是布尔" in str(exc.value)


def test_read_report_ok_true_requires_bundle_and_size(tmp_path: Path) -> None:
    payload = _ok_payload()
    del payload["bundle_bytes"]
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(_write_report(tmp_path, payload))
    assert "bundle_bytes" in str(exc.value)


def test_read_report_ok_true_requires_bundle_path(tmp_path: Path) -> None:
    payload = _ok_payload()
    payload["bundle"] = ""
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(_write_report(tmp_path, payload))
    assert "bundle" in str(exc.value)


def test_read_report_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text('{"ok": true, ', encoding="utf-8")
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(path)
    assert "读不开" in str(exc.value)


def test_read_report_parts_must_be_list(tmp_path: Path) -> None:
    payload = _ok_payload()
    payload["parts"] = {"node": "Chassis"}
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.read_report(_write_report(tmp_path, payload))
    assert "parts" in str(exc.value)


def test_unity_build_error_is_asset_error() -> None:
    assert issubclass(ub.UnityBuildError, AssetError)

# --------------------------------------------------------------------------- #
# report_lines：界面那四样必须都在（parts / known_gaps / warnings / bundle_bytes）
# --------------------------------------------------------------------------- #


def test_report_lines_surfaces_the_four_things_the_gui_must_show(tmp_path: Path) -> None:
    report = ub.read_report(_write_report(tmp_path, _ok_payload()))
    text = "\n".join(ub.report_lines(report))
    assert "ok=True" in text
    assert "36,355,912 B" in text                     # bundle_bytes（带千分位）
    assert "Chassis" in text and "44,164→44,169" in text  # parts 的顶点读数
    assert "已知缺口 1 件 / 2 条通道" in text           # known_gaps
    assert "BaseMobility  缺 Color/4、TexCoord2/2" in text
    assert "警告 1 条" in text                         # warnings
    assert "LOD3 同槽位 0" in text
    assert CHASSIS_PREFAB in text                      # prefab_source（下一步要用）


def test_report_lines_failure_shows_error_and_stack(tmp_path: Path) -> None:
    payload = {"ok": False, "error": "Exception: prefab 里找不到节点",
               "stack": "at UnitBuild.Run ()" + chr(10) + "at UnitBuild.Main ()"}
    text = "\n".join(ub.report_lines(ub.read_report(_write_report(tmp_path, payload))))
    assert "ok=False" in text
    assert "prefab 里找不到节点" in text
    assert "at UnitBuild.Run ()" in text


def test_report_lines_tolerates_empty_parts() -> None:
    text = "\n".join(ub.report_lines({"ok": True, "bundle_bytes": 1, "parts": []}))
    assert "件 0" in text


# --------------------------------------------------------------------------- #
# argv：车体/炮塔判据不许混，check-model 必须带 --asset-path
# --------------------------------------------------------------------------- #


def test_check_model_argv_chassis_uses_unit_prefab() -> None:
    argv = ub.check_model_argv("D:/b/x", asset_path=CHASSIS_PREFAB, kind="chassis")
    assert argv == ["check-model", "--bundle", "D:/b/x",
                    "--asset-path", CHASSIS_PREFAB, "--unit-prefab"]
    assert "--turret-prefab" not in argv


def test_check_model_argv_turret_uses_turret_prefab() -> None:
    argv = ub.check_model_argv("D:/b/y", asset_path=TURRET_PREFAB, kind="turret")
    assert argv == ["check-model", "--bundle", "D:/b/y",
                    "--asset-path", TURRET_PREFAB, "--turret-prefab"]
    assert "--unit-prefab" not in argv


def test_check_model_argv_rejects_unknown_kind() -> None:
    with pytest.raises(ub.UnityBuildError):
        ub.check_model_argv("D:/b/y", asset_path=TURRET_PREFAB, kind="tank")


def test_add_bundle_argv_exact() -> None:
    argv = ub.add_bundle_argv(
        "D:/b/z", key="RU_T72B_BASE", asset_path=CHASSIS_PREFAB,
        dependency_source="RU_T72B", project_root="D:/repo")
    assert argv == ["newasset", "add-bundle", "--bundle", "D:/b/z",
                    "--key", "RU_T72B_BASE", "--dependency-source", "RU_T72B",
                    "--asset-path", CHASSIS_PREFAB, "--project-root", "D:/repo"]
    assert "--dry-run" not in argv


def test_add_bundle_argv_dry_run_flag() -> None:
    argv = ub.add_bundle_argv(
        "D:/b/z", key="K", asset_path=None, dependency_source="RU_T72B", dry_run=True)
    assert argv[-1] == "--dry-run"
    assert "--asset-path" not in argv        # 不给就让它自己推，⛔ 不拼空串


def test_add_bundle_argv_requires_key_and_dependency() -> None:
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.add_bundle_argv("D:/b/z", key="  ", asset_path=None, dependency_source="")
    assert "--key" in str(exc.value) and "--dependency-source" in str(exc.value)


def test_unity_build_argv_exact() -> None:
    argv = ub.unity_build_argv(
        "D:/工程 x", base_unit="RU_T72B", out="D:/jobs/j1",
        prefab=TURRET_PREFAB, bundle_name="ru_t72b_turret", bundle_out="D:/out",
        parts=["turret", "K1"], job_only=True)
    assert argv == ["pack", "unity-build", "--from", "D:/工程 x",
                    "--base-unit", "RU_T72B", "--out", "D:/jobs/j1",
                    "--prefab", TURRET_PREFAB, "--bundle-name", "ru_t72b_turret",
                    "--bundle-out", "D:/out", "--parts", "turret,K1", "--job-only"]


def test_unity_build_argv_no_suffix_flags_when_defaults() -> None:
    argv = ub.unity_build_argv("D:/p", base_unit="K", out="D:/o", lod=False, job_only=False)
    assert argv == ["pack", "unity-build", "--from", "D:/p",
                    "--base-unit", "K", "--out", "D:/o", "--no-lod"]
    assert "--job-only" not in argv


def test_generated_argv_is_accepted_by_the_real_cli_parser() -> None:
    """⛔ 别写错开关名：把拼出来的 argv 交给真正的解析器跑一遍。"""
    from ba_mod_maker.cli.__main__ import _build_parser

    parser = _build_parser()
    cases = [
        ub.unity_build_argv("D:/p", base_unit="RU_T72B", out="D:/o",
                            prefab=CHASSIS_PREFAB, bundle_name="ru_t72b_base",
                            bundle_out="D:/b", parts=["turret"]) + ["--run"],
        ub.check_model_argv("D:/b/x", asset_path=CHASSIS_PREFAB, kind="chassis"),
        ub.check_model_argv("D:/b/y", asset_path=TURRET_PREFAB, kind="turret"),
        ub.add_bundle_argv("D:/b/z", key="K", asset_path=CHASSIS_PREFAB,
                           dependency_source="RU_T72B", project_root="D:/repo"),
    ]
    for argv in cases:
        assert parser.parse_args(argv) is not None
    assert parser.parse_args(cases[0]).run is True
    assert parser.parse_args(cases[1]).unit_prefab is True
    assert parser.parse_args(cases[2]).turret_prefab is True
    assert parser.parse_args(cases[3]).dry_run is False


# --------------------------------------------------------------------------- #
# 真件 prefab 定位：炮塔⛔ 不许退化成车体 prefab
# --------------------------------------------------------------------------- #


def _fake_project(root: Path) -> Path:
    base = root / "Assets" / "Resources_moved" / "ModelPrefabs" / "RU"
    (base / "Turrets" / "RU_T72B").mkdir(parents=True)
    (base / "RU_T72B_Base.prefab").write_text("%YAML 1.1", encoding="utf-8")
    (base / "Turrets" / "RU_T72B" / "RU_T72B.prefab").write_text("%YAML 1.1", encoding="utf-8")
    return root


def test_find_base_prefab(tmp_path: Path) -> None:
    root = _fake_project(tmp_path)
    assert ub.find_base_prefab(root, "RU_T72B") == CHASSIS_PREFAB


def test_find_turret_prefab_does_not_fall_back_to_the_base_prefab(tmp_path: Path) -> None:
    root = _fake_project(tmp_path)
    assert ub.find_turret_prefab(root, "RU_T72B") == TURRET_PREFAB


def test_find_turret_prefab_missing_raises(tmp_path: Path) -> None:
    root = _fake_project(tmp_path)
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.find_turret_prefab(root, "RU_T72B3")
    assert "没有" in str(exc.value)


def test_find_turret_prefab_ambiguous_raises(tmp_path: Path) -> None:
    root = _fake_project(tmp_path)
    other = root / "Assets" / "Resources_moved" / "ModelPrefabs" / "US" / "Turrets" / "RU_T72B"
    other.mkdir(parents=True)
    (other / "RU_T72B.prefab").write_text("%YAML 1.1", encoding="utf-8")
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.find_turret_prefab(root, "RU_T72B")
    assert "不唯一" in str(exc.value)


# --------------------------------------------------------------------------- #
# run()：只起进程、只记读数 —— ⛔ 不判红绿，⛔ 真 Unity 一次都不许起
# --------------------------------------------------------------------------- #


class _FakeProc:
    """替身：记下 cmd/env，按剧本退出（⛔ 不真起 Unity）。"""

    instances: list[_FakeProc] = []

    def __init__(self, cmd, *, env=None, rc=0, stdout="", timeout_once=False, **_kw):
        self.cmd = list(cmd)
        self.env = dict(env or {})
        self.returncode = rc
        self.stdout = stdout
        self.timeout_once = timeout_once
        self.killed = False
        self.communicate_calls = 0
        _FakeProc.instances.append(self)

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self.timeout_once and self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(self.cmd, timeout or 1)
        return self.stdout, ""

    def kill(self):
        self.killed = True


@pytest.fixture()
def fake_popen(monkeypatch):
    _FakeProc.instances = []

    def factory(**scenario):
        def make(cmd, **popen_kw):
            # ⚠ Popen 的 kwargs 里有 `stdout=subprocess.PIPE`，⛔ 别让它盖掉剧本里的 stdout
            opts = dict(scenario)
            opts["env"] = popen_kw.get("env")
            return _FakeProc(cmd, **opts)
        return make

    def install(**kwargs):
        monkeypatch.setattr(ub.subprocess, "Popen", factory(**kwargs))
        return _FakeProc

    return install


def _job_and_exe(tmp_path: Path) -> tuple[Path, Path, Path]:
    job = tmp_path / "job.json"
    job.write_text("{}", encoding="utf-8")
    exe = tmp_path / "Unity.exe"
    exe.write_bytes(b"stub")
    proj = tmp_path / "proj"
    proj.mkdir()
    return job, exe, proj


def test_run_builds_the_spec_command_and_passes_job_env(tmp_path: Path, fake_popen) -> None:
    job, exe, proj = _job_and_exe(tmp_path)
    procs = fake_popen(rc=0, stdout="noise")
    out = ub.run(job, unity_exe=exe, project=proj, timeout_s=5)
    proc = procs.instances[-1]
    cmd = proc.cmd
    assert cmd[0] == str(exe)
    assert cmd[1:3] == ["-batchmode", "-quit"]
    assert cmd[cmd.index("-projectPath") + 1] == str(proj.resolve())
    assert cmd[cmd.index("-executeMethod") + 1] == ub.EXECUTE_METHOD == "UnitBuild.Run"
    assert cmd.count("-logFile") == 1
    assert proc.env[ub.JOB_ENV] == str(job.resolve())
    assert out["returncode"] == 0 and out["timed_out"] is False
    assert Path(out["log"]).parent == job.parent
    assert "UNITBUILD_JOB" in out["command"] and "-executeMethod UnitBuild.Run" in out["command"]
    assert out["log_bytes"] == 0          # 假进程没写日志 ⇒ 读数是 0，⛔ 不编


def test_run_nonzero_exit_is_returned_not_raised(tmp_path: Path, fake_popen) -> None:
    job, exe, proj = _job_and_exe(tmp_path)
    fake_popen(rc=1, stdout="Aborting batchmode due to failure")
    out = ub.run(job, unity_exe=exe, project=proj, timeout_s=5)
    assert out["returncode"] == 1
    assert out["timed_out"] is False
    assert "Aborting" in out["stdout"]


def test_run_timeout_kills_and_reports(tmp_path: Path, fake_popen) -> None:
    job, exe, proj = _job_and_exe(tmp_path)
    procs = fake_popen(rc=0, timeout_once=True)
    out = ub.run(job, unity_exe=exe, project=proj, timeout_s=0.01)
    assert out["timed_out"] is True
    assert out["returncode"] is None
    assert procs.instances[-1].killed is True


def test_run_missing_job_raises_before_touching_unity(tmp_path: Path) -> None:
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.run(tmp_path / "nope.json", unity_exe=tmp_path / "x.exe", project=tmp_path)
    assert "job.json 不存在" in str(exc.value)


def test_run_missing_unity_exe_raises_with_hint(tmp_path: Path) -> None:
    job, _exe, proj = _job_and_exe(tmp_path)
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.run(job, unity_exe=tmp_path / "nope" / "Unity.exe", project=proj)
    assert "UNITBUILD_UNITY_EXE" in str(exc.value.hint)


def test_run_missing_project_raises(tmp_path: Path) -> None:
    job, exe, _proj = _job_and_exe(tmp_path)
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.run(job, unity_exe=exe, project=tmp_path / "nope")
    assert "导出工程不存在" in str(exc.value)


# --------------------------------------------------------------------------- #
# 5 · 包名归一（2026-10-26：`install` 只认 `<slug>_assets_all_<32hex>.bundle`）
# --------------------------------------------------------------------------- #
#: Unity 给每个包写的 `.manifest` 摘录（真件形状；`AssetFileHash` 就是 `_all_` 尾段）
MANIFEST_TEXT = (
    "ManifestFileVersion: 0\n"
    "UnityVersion: 6000.3.24f1\n"
    "CRC: 885274694\n"
    "Hashes:\n"
    "  AssetFileHash:\n"
    "    serializedVersion: 2\n"
    "    Hash: b7417aeb5225dfae8fe468d8bc670424\n"
    "  TypeTreeHash:\n"
    "    serializedVersion: 2\n"
    "    Hash: dda2de27b36f2702ef39aa422447ba53\n"
)
HASH32 = "b7417aeb5225dfae8fe468d8bc670424"


def _fake_unity_bundle(tmp_path: Path, name: str, *, manifest: bool = True,
                       payload: bytes = b"BUNDLE-BYTES") -> Path:
    bundle = tmp_path / name
    bundle.write_bytes(payload)
    if manifest:
        bundle.with_name(bundle.name + ".manifest").write_text(MANIFEST_TEXT, encoding="utf-8")
    return bundle


def test_a_bare_name_becomes_the_install_contract(tmp_path: Path) -> None:
    """Unity 默认落 `ru_t72b_base`（没 `.bundle`）⇒ 归一成 install 认的形状。"""
    src = _fake_unity_bundle(tmp_path, "ru_t72b_base")
    res = ub.normalise_bundle(src)
    assert res["renamed"] is True
    assert res["name"] == "ru_t72b_base_assets_all_" + HASH32 + ".bundle"
    assert ub.is_install_bundle_name(res["name"])
    target = Path(res["path"])
    assert target.is_file() and not src.exists()
    assert target.read_bytes() == b"BUNDLE-BYTES", "改名动了包里的字节 —— 不许"
    assert Path(res["manifest"]).name == target.name + ".manifest"


def test_a_16_hex_name_is_rewritten_to_32(tmp_path: Path) -> None:
    """16 位是**图标线**的派生口径；模型线必须换成 32 位（否则 `install` 拒）。"""
    src = _fake_unity_bundle(tmp_path, "guie2e72b_assets_all_94da722ccfe9a4c5.bundle")
    res = ub.normalise_bundle(src)
    assert res["name"] == "guie2e72b_assets_all_" + HASH32 + ".bundle"


def test_normalise_is_idempotent(tmp_path: Path) -> None:
    """第二次跑不许再改（GUI 可能连着跑两趟）。"""
    src = _fake_unity_bundle(tmp_path, "ru_t72b_base")
    first = ub.normalise_bundle(src)
    second = ub.normalise_bundle(first["path"])
    assert second["renamed"] is False
    assert second["path"] == first["path"]


def test_normalise_without_manifest_is_refused(tmp_path: Path) -> None:
    """⛔ 没有 `.manifest` 就没有 AssetFileHash ⇒ fail-closed（不许拿 16 位凑）。"""
    src = _fake_unity_bundle(tmp_path, "ru_t72b_base", manifest=False)
    with pytest.raises(ub.UnityBuildError) as exc:
        ub.normalise_bundle(src)
    assert ".manifest" in str(exc.value)


def test_normalise_refuses_to_overwrite_different_bytes(tmp_path: Path) -> None:
    """同名目标已存在且内容不同 ⇒ 报错（⛔ 不覆盖）。"""
    src = _fake_unity_bundle(tmp_path, "ru_t72b_base")
    clash = tmp_path / ("ru_t72b_base_assets_all_" + HASH32 + ".bundle")
    clash.write_bytes(b"OTHER")
    with pytest.raises(ub.UnityBuildError) as excinfo:
        ub.normalise_bundle(src)
    assert "内容不同" in str(excinfo.value)
    assert clash.read_bytes() == b"OTHER"

def test_unity_command_is_copy_pasteable(tmp_path: Path) -> None:
    text = ub.unity_command(unity_exe=tmp_path / "Unity.exe", project=tmp_path,
                            job=tmp_path / "job.json", log=tmp_path / "u.log")
    assert "$env:UNITBUILD_JOB" in text
    assert "-executeMethod UnitBuild.Run" in text
    assert "-logFile" in text
    assert text.count(chr(34)) >= 6        # 三处绝对路径都加了引号
