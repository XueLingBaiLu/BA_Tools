"""Unity 造包的**运行壳**：起 Unity（batchmode）＋ 读 `report.json`。

判定**不在这里**（`:mod:`unity_prefab` 造 job，C# 造包，`check-model` 出红绿）：
这一层只做两件机械的事 —— ① 把 `Unity.exe` 按规格 §6 起起来并等它退；② 把 `report.json` 读成 dict。

## 2026-10-25 实测（推翻了 AGENTS §0.17 里「Python 起不了 Unity」那半条）

在**本机当前环境**里用 Python `subprocess` 起 Unity **能成**，读数：

    probe: .venv\\Scripts\\python.exe -> subprocess.run(Unity.exe -batchmode -quit
           -projectPath <导出工程> -executeMethod UnitBuild.NoSuchMethodProbe)
    -> returncode=1（只因方法名不存在而中止）, elapsed_s=14.9, log=26,883 B
    -> 日志里 `[Package Manager] Connected to IPC stream "Upm-41064"` 与
       `[Package Manager] Registered 47 packages` 都在（EPERM 没复现）
    -> 同一进程里 `tempfile.mkstemp(dir=%TEMP%)` 成功（历史上报 EPERM 的那一步）

⇒ 产品这一层**直接起 Unity**（GUI 显示进度）；同时也把等价命令打印出来给人抄，
  机器上真起不来时按命令手跑（`run()` 的返回里带 `command`）。

## 边界（⛔ 别把判定塞进来）

· `run()` **不判** report：Unity 退出码在某些故障下不可靠（规格 §5 第 7 条），
  判据永远是 `report.ok`；
· `read_report()` **不判**「能不能装机」：只校验报告**形状**（`ok` 在不在、类型对不对）；
· 红绿由 `check-model` 出（`check_model_argv()` 只拼参数，⛔ 不跑）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

__all__ = [
    "DEFAULT_TIMEOUT_S", "EXECUTE_METHOD", "JOB_ENV", "PREFAB_ROOTS", "UnityBuildError",
    "add_bundle_argv", "bundle_asset_hash", "check_model_argv", "find_base_prefab",
    "find_turret_prefab", "install_bundle_name", "normalise_bundle", "read_report",
    "report_lines", "run", "unity_build_argv", "unity_command",
    "unity_exe_default", "unity_project_default",
]

#: C# 侧入口（规格 §5，`UnitBuild.cs`）
EXECUTE_METHOD = "UnitBuild.Run"
#: job 路径走环境变量传（与 `MeshBridge.Run` 同一套起法，规格 §5 第 1 条）
JOB_ENV = "UNITBUILD_JOB"
#: 默认等多久（秒）。RU_T72B 车体实测 73 s、炮塔 62 s；给足余量。
DEFAULT_TIMEOUT_S = 1800.0
#: 真件 prefab 的落脚目录（相对导出工程根）
PREFAB_ROOTS = ("Assets/Resources_moved/ModelPrefabs",)

#: 报告里必须有的顶层键（规格 §4：成功 `ok:true`、失败 `ok:false` + `error`）
_REQUIRED_KEYS = ("ok",)
#: `ok:true` 时下一步（check-model / add-bundle）要用到的键
_OK_REQUIRED_KEYS = ("bundle", "bundle_bytes")


class UnityBuildError(AssetError):
    """起不来 Unity / 读不了 report —— 这是**环境或产物**问题，不是「包不合格」的判定。"""


def unity_exe_default() -> Path:
    """Unity 可执行（环境变量 `UNITBUILD_UNITY_EXE` 可覆盖；默认见规格 §1）。"""
    from ba_mod_maker.core.assets.unity_prefab import DEFAULT_UNITY_EXE

    raw = os.environ.get("UNITBUILD_UNITY_EXE")
    return Path(raw) if raw else Path(DEFAULT_UNITY_EXE)


def unity_project_default() -> Path:
    """导出工程根（环境变量 `UNITBUILD_UNITY_PROJECT` 可覆盖；默认见规格 §1）。"""
    from ba_mod_maker.core.assets.unity_prefab import DEFAULT_UNITY_PROJECT

    raw = os.environ.get("UNITBUILD_UNITY_PROJECT")
    return Path(raw) if raw else Path(DEFAULT_UNITY_PROJECT)


def _one(candidates: list[Path], *, what: str, key: str, root: Path) -> str:
    """唯一命中 -> 资产路径；0 个 / 多个都抛（⛔ 不猜）。"""
    if len(candidates) == 1:
        return candidates[0].relative_to(root).as_posix()
    names = [c.relative_to(root).as_posix() for c in candidates]
    if not candidates:
        raise UnityBuildError(
            what + "里没有 " + key + "（导出工程：" + str(root) + "）",
            hint="核对真件键（Units.ModelFileName 原样值）；⛔ 这里不按相近名字凑",
        )
    raise UnityBuildError(
        what + "里 " + key + " 有 " + str(len(names)) + " 个命中 —— 不唯一，请点名",
        hint="候选：" + "、".join(names[:8]),
    )


def find_base_prefab(unity_project: str | Path, key: str) -> str:
    """车体真件 prefab 的资产路径（**唯一命中**才算数）。

    两种真件命名都认（2026-10-26 端到端实测补的第二档）：
      · `<键>_Base.prefab` —— T-72B / BMP-3 这类（`Units.ModelFileName` 不带后缀）；
      · `<键>.prefab`     —— BTR-90 / Flyer GMV（`US_M1288\\US_m1288_base`）/ AC-130
        （`US_AC130_Base`）这类：**键本身**已经带 `_base`/`_Base`，真件就叫那个名字。

    ⛔ 第二档必须排除 `Turrets/` 那一支：`Turrets/<键>/<键>.prefab` 是**炮塔**真件
    （`find_turret_prefab` 专门认它），混进来会让车体被悄悄换成炮塔。
    """
    root = Path(unity_project)
    def _hits(name: str) -> list[Path]:
        # ⛔ 两档都排除 `Turrets/`：BTR-90 那种「炮塔目录里也有一份 <键>_Base.prefab」
        #   的布局真存在，混进来就会把车体换成炮塔真件。
        return sorted(
            p for base in PREFAB_ROOTS
            for p in (root / base).rglob(name)
            if "Turrets" not in p.relative_to(root).parts
        )

    capital = str(key) + "_Base.prefab"
    hits = _hits(capital)
    if hits:
        return _one(hits, what="车体真件 prefab", key=capital, root=root)
    plain = str(key) + ".prefab"
    return _one(_hits(plain), what="车体真件 prefab", key=plain, root=root)


def find_turret_prefab(unity_project: str | Path, key: str) -> str:
    """炮塔真件 prefab 的资产路径：`ModelPrefabs/<国>/Turrets/<key>/<key>.prefab`。

    为什么不能复用 `unity_prefab.find_prefab`：它先找 `<key>_Base.prefab`、再找 `<key>.prefab`，
    而 `<key>_Base.prefab` 一定先命中（车体）⇒ 炮塔会被悄悄换成车体（实测 RU_T72B 就是这样）。
    """
    root = Path(unity_project)
    name = str(key) + ".prefab"
    hits = [
        p for base in PREFAB_ROOTS
        for p in (root / base).rglob(name)
        if p.parent.name == str(key) and p.parent.parent.name == "Turrets"
    ]
    return _one(sorted(hits), what="炮塔真件 prefab", key=name, root=root)


def unity_command(*, unity_exe: str | Path, project: str | Path, job: str | Path,
                  log: str | Path | None = None) -> str:
    """规格 §6 起法的**可复制版**（PowerShell；含 `UNITBUILD_JOB` 那一行）。

    产品自己会起 Unity（见模块头实测），这段是给人抄的备份路径 ——
    命令行里出现绝对路径一律加引号（本机路径含空格与中文）。
    """
    log_path = Path(log) if log else Path(job).with_name(_log_name(Path(job)))
    return "".join([
        '$env:' + JOB_ENV + ' = "' + str(Path(job).resolve()) + '"',
        "\n",
        '& "' + str(Path(unity_exe)) + '" -batchmode -quit '
        + '-projectPath "' + str(Path(project).resolve()) + '" '
        + '-executeMethod ' + EXECUTE_METHOD + ' -logFile "' + str(Path(log_path)) + '"',
    ])


def _log_name(job: Path) -> str:
    return "unity-" + time.strftime("%Y%m%d-%H%M%S") + ".log"


def _log_tail(path: Path, lines: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = text.replace("\r", "").split("\n")
    return rows[-lines:] if lines > 0 else rows


def run(job_path: str | Path, *, unity_exe: str | Path | None = None,
        project: str | Path | None = None, timeout_s: float = DEFAULT_TIMEOUT_S,
        log_path: str | Path | None = None) -> dict[str, Any]:
    """起 Unity 跑这个 job，**等它退**，返回读数（⛔ 不判 report，不判红绿）。

    返回的键：`returncode` / `timed_out` / `elapsed_s` / `cmd` / `command` / `job` /
    `unity` / `project` / `log` / `log_bytes` / `log_tail` / `stdout` / `job_env`。

    起都起不来（exe 不在 / 目录不在 / OSError）⇒ 抛 :class:`UnityBuildError`；
    起来了但退出码非 0 / 超时 ⇒ **正常返回**（report 才是判据，规格 §5 第 7 条）。
    """
    job = Path(job_path)
    if not job.is_file():
        raise UnityBuildError(
            "job.json 不存在：" + str(job),
            hint="先跑 `pack unity-build`（或 GUI 的「导入车体 / 导入炮塔」第 1 步）",
        )
    exe = Path(unity_exe) if unity_exe else unity_exe_default()
    if not exe.is_file():
        raise UnityBuildError(
            "Unity 可执行不存在：" + str(exe),
            hint="本机实测在 D:\\Unity\\6000.3.24f1\\Editor\\Unity.exe；"
                 "可用 UNITBUILD_UNITY_EXE 覆盖，或勾「不启 Unity」拿命令手跑",
        )
    proj = Path(project) if project else unity_project_default()
    if not proj.is_dir():
        raise UnityBuildError(
            "Unity 导出工程不存在：" + str(proj),
            hint="可用 UNITBUILD_UNITY_PROJECT 覆盖（规格 §1 钉死那条路径）",
        )
    log = Path(log_path) if log_path else job.with_name(_log_name(job))
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "-batchmode", "-quit", "-projectPath", str(proj.resolve()),
           "-executeMethod", EXECUTE_METHOD, "-logFile", str(log)]
    env = os.environ.copy()
    env[JOB_ENV] = str(job.resolve())
    # ★ 2026-10-29：job 里带动画配置就交给 C#（UnitBuild -> AnimConfig.Apply）。
    #   来源 = 工程目录里 `model.fbx` 旁边的 `anim_config.json`（见 unity_prefab._emit_fbx_job）。
    try:
        _job = json.loads(Path(job).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _job = {}
    _anim = str(_job.get("anim_config") or "")
    if _anim and Path(_anim).is_file():
        env["ANIMCFG_JSON"] = _anim
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise UnityBuildError(
            "起不来 Unity：" + type(exc).__name__ + ": " + str(exc),
            hint="现场命令：" + unity_command(unity_exe=exe, project=proj, job=job, log=log),
        ) from exc
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
    elapsed = round(time.time() - started, 1)
    return {
        "returncode": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "elapsed_s": elapsed,
        "cmd": cmd,
        "command": unity_command(unity_exe=exe, project=proj, job=job, log=log),
        "job": str(job),
        "unity": str(exe),
        "project": str(proj),
        "log": str(log),
        "log_bytes": log.stat().st_size if log.is_file() else 0,
        "log_tail": _log_tail(log, 40),
        "stdout": (out or "")[-8000:],
        "job_env": JOB_ENV,
        "timeout_s": timeout_s,
    }


# --------------------------------------------------------------------------- #
# 包名归一（2026-10-26：`pack unity-build` 产出的名字 install 不认 —— 这条补齐）
# --------------------------------------------------------------------------- #
#: install 侧的唯一包名契约（`new_asset` 的「包名必须以 .bundle 结尾」＋「`_all_` 尾段
#: 要 32 位十六进制」两条合起来就是它）。字面量与那两条校验**必须**一致。
_INSTALL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+_assets_all_[0-9a-f]{32}\.bundle$")
#: Unity 写的 `<包>.manifest` 里那一段（缩进两行的 YAML；⛔ 不引 PyYAML）
_ASSET_HASH_RE = re.compile(
    r"AssetFileHash:\s*\n\s*serializedVersion:\s*\d+\s*\n\s*Hash:\s*([0-9a-fA-F]{32})"
)


def bundle_asset_hash(bundle: str | Path) -> str:
    """Unity 给这个包写的 `<包>.manifest` 里的 `AssetFileHash`（32 hex）。

    ⛔ 这不是「随便一个 hash」：真件 76/76 个包键的 `_all_` 尾段就是它，`new_asset` 拿这一段
    当 catalog 的 `m_Hash`。读不到就 fail-closed（⛔ 不拿文件名里的 16 位凑）。
    """
    src = Path(bundle)
    manifest = src.with_name(src.name + '.manifest')
    if not manifest.is_file():
        raise UnityBuildError(
            '找不到 Unity 写的 manifest：' + str(manifest),
            hint='打完包它就在 `<包>.manifest`；缺它多半说明这个包不是 Unity 造的，'
                 '或者打包目录被清理过 —— 重跑一次 UnitBuild.Run 再试',
        )
    text = manifest.read_text(encoding='utf-8', errors='replace')
    found = _ASSET_HASH_RE.search(text)
    if not found:
        raise UnityBuildError(
            'manifest 里没有 AssetFileHash：' + str(manifest),
            hint='Unity 版本换了写法？把 manifest 头 20 行贴出来，别在这里猜',
        )
    return found.group(1).lower()


def _slug_of(name: str) -> str:
    """包名 -> 给 install 用的 slug（`_assets_all_` 之前那段；没有就整段主干）。"""
    stem = name[:-len('.bundle')] if name.endswith('.bundle') else name
    head, sep, _tail = stem.partition('_assets_all_')
    slug = head if sep else stem
    slug = re.sub(r'[^A-Za-z0-9]+', '_', slug).strip('_').lower()
    return slug or 'asset'


def install_bundle_name(bundle: str | Path) -> str:
    """这个包在 install 侧**应该**叫什么：`<slug>_assets_all_<AssetFileHash>.bundle`。"""
    src = Path(bundle)
    return _slug_of(src.name) + '_assets_all_' + bundle_asset_hash(src) + '.bundle'


def is_install_bundle_name(name: str) -> bool:
    """这个名字是不是已经符合 install 的契约（`_all_` 尾段 == 32 位十六进制）。"""
    return bool(_INSTALL_NAME_RE.match(str(name)))


def normalise_bundle(bundle: str | Path) -> dict[str, Any]:
    """把一个 Unity 造出来的包改成 **install 认的名字**（幂等；脚本与 GUI 共用这一份）。

    为什么需要它（2026-10-26 端到端实测）：`pack unity-build` 把 `bundle_name` 原样交给 Unity，
    Unity 就按那个名字落盘（默认是 `ru_t72b_base`，没有 `.bundle`）⇒ `pack install` 第一道校验
    就拒。改名的依据只有一处：Unity 自己写的 `AssetFileHash`（32 位），**⛔ 不要拿文件名里
    那 16 位凑**（那是 `new_asset.bundle_file_name` 给图标线用的派生，模型线不认）。

    返回 `{renamed, path, name, hash, manifest}`：`path` 是**改完之后**的路径（调用方用它继续）。
    
    ⛔ 边界：只改**文件名**（连同 `.manifest`），不动包里的任何一个字节 —— 所以 CRC / m_BundleSize
    与 `check-model` 的读数逐位不变。
    """
    src = Path(bundle)
    if not src.is_file():
        raise UnityBuildError('要改名的包不存在：' + str(src))
    digest = bundle_asset_hash(src)
    target = src.with_name(_slug_of(src.name) + '_assets_all_' + digest + '.bundle')
    if target == src:
        return {'renamed': False, 'path': str(src), 'name': src.name,
                'hash': digest, 'manifest': str(src.with_name(src.name + '.manifest'))}
    if target.exists():
        if target.read_bytes() != src.read_bytes():
            raise UnityBuildError(
                '改名目标已存在且内容不同：' + str(target),
                hint='同一个 AssetFileHash 却是两份不同的字节 —— 先把旧的那份挪走，别覆盖',
            )
        src.unlink()
    else:
        src.rename(target)
    old_manifest = src.with_name(src.name + '.manifest')
    new_manifest = target.with_name(target.name + '.manifest')
    if old_manifest.is_file() and not new_manifest.exists():
        old_manifest.rename(new_manifest)
    return {'renamed': True, 'path': str(target), 'name': target.name,
            'hash': digest, 'manifest': str(new_manifest)}


def read_report(path: str | Path) -> dict[str, Any]:
    """读 `report.json`（规格 §4）—— **只校验形状**，⛔ 不判「能不能装机」。

    · 文件不在 / 不是 JSON / 不是对象 / 缺 `ok` / `ok` 不是布尔 ⇒ 抛 :class:`UnityBuildError`；
    · `ok:true` 但缺 `bundle` 或 `bundle_bytes` ⇒ 抛（下一步要用，缺了就是 C# 写漏了）；
    · `ok:false` ⇒ 原样返回（`error` / `stack` 由调用方展示，⛔ 这里不改判）；
    · `parts` / `warnings` / `known_gaps` 缺省补空列表，并按类型校验。
    """
    p = Path(path)
    if not p.is_file():
        raise UnityBuildError(
            "report.json 还没生成：" + str(p),
            hint="Unity 跑完了才写它；先确认 Unity 那一步成功（日志尾部有没有 UnitBuild.Run）",
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnityBuildError(
            "report.json 读不开：" + str(p) + "（" + type(exc).__name__ + ": " + str(exc) + "）",
            hint="C# 侧写一半就崩了会留半截；看同目录的 unity-*.log 尾部",
        ) from exc
    if not isinstance(data, dict):
        raise UnityBuildError("report.json 不是 JSON 对象：" + str(p))
    for key in _REQUIRED_KEYS:
        if key not in data:
            raise UnityBuildError(
                "report.json 缺 `" + key + "` 字段：" + str(p),
                hint="规格 §4：成功 {ok:true,...}、失败 {ok:false,error,...}",
            )
    if not isinstance(data["ok"], bool):
        raise UnityBuildError(
            "report.json 的 `ok` 不是布尔：" + repr(data["ok"]) + "（" + str(p) + "）"
        )
    for key in ("parts", "warnings", "known_gaps"):
        if data.get(key) is None:
            data[key] = []
        if not isinstance(data[key], list):
            raise UnityBuildError(
                "report.json 的 `" + key + "` 不是列表：" + str(p)
            )
    if data["ok"]:
        for key in _OK_REQUIRED_KEYS:
            if data.get(key) in (None, ""):
                raise UnityBuildError(
                    "report.json 说 ok=true 但缺 `" + key + "`：" + str(p),
                    hint="下一步（check-model / add-bundle）要用它；C# 侧写漏了",
                )
    data["path"] = str(p)
    return data


def _num(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "—"
    if isinstance(value, int):
        return format(value, ",")
    return str(value)


def report_lines(report: dict[str, Any]) -> list[str]:
    """把 report 摊成给人看的行（GUI 直接逐行显示）。**只读**，不改 report。"""
    lines: list[str] = []
    ok = bool(report.get("ok"))
    head = ["[report] ok=" + ("True" if ok else "False")]
    if report.get("unity"):
        head.append("unity=" + str(report["unity"]))
    if report.get("elapsed_ms") is not None:
        head.append("用时 " + str(round(float(report["elapsed_ms"]) / 1000.0, 1)) + " s")
    if report.get("renderers") is not None:
        head.append("渲染器 " + _num(report["renderers"]) + " 个")
    lines.append(" · ".join(head))
    if not ok:
        lines.append("  错误 " + str(report.get("error") or "（report 里没写 error）"))
        stack = str(report.get("stack") or "").strip()
        if stack:
            lines.append("  栈 " + stack.splitlines()[0][:300])
    for label, key in (("真件 prefab", "prefab_source"), ("产物 prefab", "prefab"),
                       ("包", "bundle"), ("日志", "log")):
        if report.get(key):
            lines.append("  " + label + "  " + str(report[key]))
    if report.get("bundle_bytes") is not None:
        lines.append("  包大小    " + _num(report["bundle_bytes"]) + " B")
    parts = report.get("parts") or []
    lines.append("  件 " + str(len(parts)))
    for row in parts:
        if not isinstance(row, dict):
            lines.append("    - " + str(row))
            continue
        bits = [str(row.get("node") or "?")]
        if row.get("before_vertices") is not None or row.get("after_vertices") is not None:
            bits.append(_num(row.get("before_vertices")) + "→" + _num(row.get("after_vertices"))
                        + " 顶点")
        if row.get("before_indices") is not None:
            bits.append(_num(row.get("before_indices")) + " 索引")
        if row.get("lod_siblings"):
            bits.append("LOD 同槽 " + str(row["lod_siblings"]))
        if row.get("bones"):
            bits.append("骨 " + str(row["bones"]))
        if "has_uv1" in row:
            bits.append("uv1 " + ("是" if row.get("has_uv1") else "否"))
        if row.get("tangents"):
            bits.append("切线 " + str(row["tangents"]))
        lines.append("    - " + " · ".join(bits))
    gaps = report.get("known_gaps") or []
    if gaps:
        total = sum(len(g.get("missing") or []) for g in gaps if isinstance(g, dict))
        lines.append("  已知缺口 " + str(len(gaps)) + " 件 / " + str(total) + " 条通道")
        for gap in gaps:
            if not isinstance(gap, dict):
                continue
            missing = gap.get("missing") or []
            lines.append("    - " + str(gap.get("node") or "?") + "  缺 "
                         + ("、".join(str(m) for m in missing) if missing else "（无）"))
    warnings = report.get("warnings") or []
    lines.append("  警告 " + str(len(warnings)) + " 条")
    lines.extend("    - " + str(w) for w in warnings)
    return lines


#: 车体 / 炮塔 ⇒ check-model 的判据开关
#: （⛔ 两条不能混：车体 `--unit-prefab`、炮塔 `--turret-prefab`）
CHECK_MODEL_FLAG = {"chassis": "--unit-prefab", "turret": "--turret-prefab"}


def check_model_argv(bundle: str | Path, *, asset_path: str | None, kind: str) -> list[str]:
    """`check-model` 的参数（⛔ 这里**不跑**）。

    **`--asset-path` 必须给**：命名契约里 `unit_root.name` 比的是「prefab 顶层节点名」与
    「资产路径末段」，不给就是空 ⇒ 2026-10-25 实测 `out-turret` 会留 1 条红
    （顶层 `RU_T72B` vs 资产末段 `ru_t72b`）；给真件路径（= report 的 `prefab_source`）后全绿。
    """
    if kind not in CHECK_MODEL_FLAG:
        raise UnityBuildError(
            "check-model 只认 chassis / turret，收到 " + repr(kind),
            hint="车体走 --unit-prefab、炮塔走 --turret-prefab（判据不同，⛔ 别混）",
        )
    argv = ["check-model", "--bundle", str(bundle)]
    if asset_path:
        argv += ["--asset-path", str(asset_path)]
    argv.append(CHECK_MODEL_FLAG[kind])
    return argv


def add_bundle_argv(
    bundle: str | Path,
    *,
    key: str,
    asset_path: str | None,
    dependency_source: str,
    dry_run: bool = False,
    game: str | Path | None = None,
    project_root: str | Path | None = None,
) -> list[str]:
    """`newasset add-bundle` 的参数（⛔ 不跑；**只登记地址，不碰任何表**）。"""
    missing = [name for name, value in (("--key", key),
                                        ("--dependency-source", dependency_source))
               if not str(value or "").strip()]
    if missing:
        raise UnityBuildError(
            "add-bundle 缺 " + "、".join(missing),
            hint="--dependency-source = 一条**已入库**的地址键（例 RU_T72B），依赖声明抄它",
        )
    argv = ["newasset", "add-bundle",
            "--bundle", str(bundle),
            "--key", str(key),
            "--dependency-source", str(dependency_source)]
    if asset_path:
        argv += ["--asset-path", str(asset_path)]
    if game:
        argv += ["--game", str(game)]
    if project_root:
        argv += ["--project-root", str(project_root)]
    if dry_run:
        argv.append("--dry-run")
    return argv


def install_spec(bundle: str | Path, *, unit: str, key: str | None = None,
                 game: str | Path | None = None,
                 project_root: str | Path | None = None) -> dict[str, Any]:
    """算好 `newasset add-model` 的参数 —— `asset_path` 从**包容器**里读（⛔ 不许手填）。

    2026-10-29 现场：手填 `Assets/_unitbuild/<ts>/…` ⇒ 游戏报
    `Addressable prefab '<key>' was not loaded!`（军械库无卡片无模型）。
    容器里真正的地址由 `unitypy_source.containers_map_via_ufs` 读出来，这里只用它。
    """
    from ba_mod_maker.asset_kernel import unitypy_source

    b = Path(bundle)
    if not b.is_file():
        raise UnityBuildError("包不存在：" + str(b))
    addrs = [a for a in unitypy_source.containers_map_via_ufs(b)
             if a.lower().endswith(".prefab")]
    if len(addrs) != 1:
        raise UnityBuildError(
            f"包里 .prefab 地址数量 = {len(addrs)}（要正好 1 条）",
            hint="容器地址：" + repr(addrs[:5]),
        )
    asset_path = addrs[0]
    unit = str(unit or "").strip()
    if not unit:
        raise UnityBuildError("install_spec 缺 unit（Units.ModelFileName 的原样值）")
    key = str(key or (unit + "_MOD")).strip()
    argv = ["newasset", "add-model",
            "--bundle", str(b), "--unit", unit, "--key", key,
            "--asset-path", asset_path]
    if game:
        argv += ["--game", str(game)]
    if project_root:
        argv += ["--project-root", str(project_root)]
    command = ".venv\\Scripts\\python.exe -m ba_mod_maker.cli " + " ".join(argv)
    return {"bundle": str(b), "unit": unit, "key": key,
            "asset_path": asset_path, "argv": argv, "command": command}


def unity_build_argv(
    from_dir: str | Path,
    *,
    base_unit: str,
    out: str | Path,
    prefab: str | None = None,
    unity_project: str | Path | None = None,
    bundle_name: str | None = None,
    bundle_out: str | Path | None = None,
    parts: list[str] | None = None,
    lod: bool = True,
    job_only: bool = True,
) -> list[str]:
    """`pack unity-build` 的参数（第 1 步：工程 -> job/bin；⛔ 不启 Unity）。"""
    argv = ["pack", "unity-build", "--from", str(from_dir),
            "--base-unit", str(base_unit), "--out", str(out)]
    if prefab:
        argv += ["--prefab", str(prefab)]
    if unity_project:
        argv += ["--unity-project", str(unity_project)]
    if bundle_name:
        argv += ["--bundle-name", str(bundle_name)]
    if bundle_out:
        argv += ["--bundle-out", str(bundle_out)]
    if parts:
        argv += ["--parts", ",".join(str(p) for p in parts)]
    if not lod:
        argv.append("--no-lod")
    if job_only:
        argv.append("--job-only")
    return argv


