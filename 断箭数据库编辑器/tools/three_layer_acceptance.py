"""W11-③ · 三层验收（设计稿 §12 P8 / 工单 W11 §3 的 P5）。

## 三层是哪三层

| 层 | 盘上那份是什么 | 怎么起 |
|---|---|---|
| ① 源码 | `blender_addon/` 原样 | 拷进**临时** `BLENDER_USER_SCRIPTS/addons/` |
| ② zip | `dist/blender_addon-<v>.zip` 里的那一份 | 解到同一个临时目录 |
| ③ 已安装 | Blender 自己装出来的那份 | `bpy.ops.preferences.addon_install` + `addon_enable` |

## 判据

* **三层版本号一致**：源码 `__init__.py` 的 `bl_info["version"]` == zip 里那份 == 已安装的那份；
* **三层注册探针用 `dir()` 通过**（⛔ 不用 `hasattr`：`bpy.ops` 是动态命名空间，
  假名字也恒为 True；`bpy.types` 里出现的是 **bl_idname** 而不是 Python 类名）。

## ⛔ 不许污染用户真实 addon 目录

三层都用 `BLENDER_USER_SCRIPTS=<tempdir>`（tempfile.mkdtemp + 收工删除），
真机 `~/.config/blender/…/scripts` 一个字节都不碰。三层各自的临时目录互不复用。

## 两种模式（同一个文件既是 CLI 又是 Blender 内的探针）

    python tools/three_layer_acceptance.py                 # 父进程：跑三层 + 出表
    python tools/three_layer_acceptance.py --list-required  # 只列判定用的名字集合

父进程用 `--probe {dir,install}` 把自己**再起一次**（在 Blender 里）：
`dir` = 插件目录已经在临时 user scripts 里，直接 import + register；
`install` = 空目录，走 `addon_install` + `addon_enable`。

## 为什么探针要分两个名字集合

* `REQUIRED`：**与接线无关**的三个名字（① 资源面板 / 检测环境算子 / PropertyGroup）——
  它们是三层一致性的判据，哪一层掉了都说明那一层的包坏了；
* `SELFCHECK`：⑥ 自检面板那三个名字。⛔ `blender_addon/__init__.py` 的 `_CLASSES` 由
  Lead 统一接线；在本单接线之前，这三件在三层里**都会是 False**，那是**预期**，
  本工具如实打出来（不是 PASS），并且 `--require-selfcheck` 可以把 rc 也挂上去 ——
  Lead 接完线再跑一次带这个开关的验收即可。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = REPO_ROOT / "blender_addon"
DIST_DIR = REPO_ROOT / "dist"
MODULE_NAME = "blender_addon"
#: 本机 Blender（不在 PATH；与 tests/test_contracts.py 同一处默认）。
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")

#: 与接线无关的注册名（三层一致性的判据）。
REQUIRED_PANELS: tuple[str, ...] = ("BAMOD_PT_main",)
REQUIRED_OPS: tuple[str, ...] = ("check_environment",)
REQUIRED_PROPERTYGROUP = "bamod"
#: ⑥ 自检面板那三件（Lead 接线后才应为 True）。
SELFCHECK_PANELS: tuple[str, ...] = ("BAMOD_PT_selfcheck",)
SELFCHECK_OPS: tuple[str, ...] = ("selfcheck_run", "selfcheck_side_by_side")

PROBE_BEGIN = "BAMOD_THREELAYER_BEGIN"
PROBE_END = "BAMOD_THREELAYER_END"


# --------------------------------------------------------------------------- #
# Blender 内的探针（`--probe` 那一支）
# --------------------------------------------------------------------------- #
def _bootstrap_paths() -> list[str]:
    """把仓库根与 `src` 挂进 sys.path。

    ⚠ 为什么这一步必须有：插件薄壳 import 的是**共用资产内核** `ba_mod_maker.asset_kernel`
    （设计稿 §4 D2）。本工具是**仓库侧**的验收夹具，所以内核由仓库提供（与
    `blender_addon/selftest.py::_bootstrap` 同一个姿势）。三层验收量的因此是
    「三层各自的**插件代码**一致」；内核随插自分发（D2 的 `_vendor` 那条线）是另一单。
    """
    added: list[str] = []
    # ⚠ **只加 src**（共用内核），⛔ 不加仓库根：加了以后 `import blender_addon` 会解析到
    # 仓库里那份源码，于是 zip / 已安装两层**根本没在量它们自己那份**（本文件第一版
    # 踩过这条：三层的 module_file 全是仓库路径）。加完还要按 scripts 目录复核路径。
    for candidate in (REPO_ROOT / "src",):
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)
            added.append(text)
    return added


def probe_main(args: argparse.Namespace) -> int:
    """在 Blender 里跑：装/启插件 -> dir() 探针 -> 打印 JSON。"""
    import bpy

    added = _bootstrap_paths()
    report: dict[str, object] = {
        "sys_path_added": added,
        "layer": args.layer,
        "blender_version": bpy.app.version_string,
        "error": "",
        "installed_module": "",
        "module_file": "",
        "version": None,
        "version_tuple": None,
        "panels": {},
        "ops": {},
        "absent_dir": {},
        "hasattr_false_positive": None,
    }
    try:
        if args.probe == "install":
            # ③ 已安装：Blender 自己解包 + 启用（临时 user scripts，见 BLENDER_USER_SCRIPTS）
            bpy.ops.preferences.addon_install(filepath=str(args.zip))
            bpy.ops.preferences.addon_enable(module=MODULE_NAME)
        else:
            if MODULE_NAME not in dir(bpy.types):
                pass
            bpy.ops.preferences.addon_enable(module=MODULE_NAME)
        import blender_addon

        report["installed_module"] = str(getattr(blender_addon, "__name__", ""))
        module_file = str(getattr(blender_addon, "__file__", ""))
        report["module_file"] = module_file
        # ⛔ 反假绿：这一层必须真的从**本层的临时 user scripts** 里加载，
        # 否则「三层一致」量的是同一份东西（本文件第一版就是这么绿的）。
        scripts_root = Path(str(args.scripts)).resolve()
        report["loaded_from_layer"] = bool(
            module_file and Path(module_file).resolve().is_relative_to(scripts_root)
        )
        if module_file and not report["loaded_from_layer"]:
            report["error"] = (
                f"加载的不是本层那份：{module_file}（本层 {scripts_root}）"
            )
        info = dict(getattr(blender_addon, "bl_info", {}) or {})
        report["version"] = ".".join(str(int(p)) for p in info.get("version", ()))
        report["version_tuple"] = list(info.get("version", ()))
        type_names = list(dir(bpy.types))
        op_group = getattr(bpy.ops, "bamod", None)
        op_names = list(dir(op_group)) if op_group is not None else []
        report["panels"] = {name: name in type_names for name in REQUIRED_PANELS + SELFCHECK_PANELS}
        report["ops"] = {name: name in op_names for name in REQUIRED_OPS + SELFCHECK_OPS}
        report["propertygroup_dir"] = REQUIRED_PROPERTYGROUP in list(dir(bpy.context.scene))
        report["absent_dir"] = {name: name in type_names for name in ("BAMOD_PT_nothere",)}
        report["absent_op_dir"] = "nothere" in op_names
        report["hasattr_false_positive"] = bool(
            op_group is not None and hasattr(op_group, "nothere")
        )
        report["types_hasattr_classname"] = hasattr(bpy.types, "BamodMainPanel")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    print(PROBE_BEGIN)
    print(json.dumps(report, ensure_ascii=False))
    print(PROBE_END)
    return 0 if not report["error"] else 1


# --------------------------------------------------------------------------- #
# 父进程侧：三层各自跑一次
# --------------------------------------------------------------------------- #
def source_version(addon_dir: Path | None = None) -> str:
    """从源码 `__init__.py` 取版本（AST 字面量；⛔ 不 import，那份文件顶层有 bpy）。"""
    path = (addon_dir or ADDON_DIR) / "__init__.py"
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign):
            continue
        if "bl_info" not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        info = ast.literal_eval(node.value)
        return ".".join(str(int(part)) for part in info["version"])
    raise SystemExit(f"[用法] {path} 里找不到 bl_info")


def zip_version(zip_path: Path) -> str:
    """从 zip 里那份 `__init__.py` 取版本。"""
    with zipfile.ZipFile(zip_path) as archive:
        text = archive.read(f"{MODULE_NAME}/__init__.py").decode("utf-8")
    for node in ast.parse(text).body:
        if not isinstance(node, ast.Assign):
            continue
        if "bl_info" not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        info = ast.literal_eval(node.value)
        return ".".join(str(int(part)) for part in info["version"])
    raise SystemExit(f"[用法] {zip_path} 里的 __init__.py 没有 bl_info")


def _blender_path() -> Path:
    override = os.environ.get("BA_BLENDER")
    if override:
        path = Path(override)
        if path.is_file():
            return path
    if DEFAULT_BLENDER.is_file():
        return DEFAULT_BLENDER
    raise SystemExit("[用法] 找不到 blender.exe（设 BA_BLENDER 指过去）")


def _run_probe(*, scripts_dir: Path, mode: str, layer: str, zip_path: Path | None) -> dict:
    """起一次 Blender，跑本文件的 `--probe` 分支，解析 JSON。"""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    argv = [
        str(_blender_path()),
        "-b",
        "--factory-startup",
        "--python",
        str(Path(__file__).resolve()),
        "--",
        "--probe",
        mode,
        "--layer",
        layer,
        "--scripts",
        str(scripts_dir),
    ]
    if zip_path is not None:
        argv += ["--zip", str(zip_path)]
    env = dict(os.environ)
    # ⛔ 只改这一层的临时目录：用户真实 addon 目录一个字节都不碰
    env["BLENDER_USER_SCRIPTS"] = str(scripts_dir)
    env["BLENDER_USER_EXTENSIONS"] = str(scripts_dir / "extensions")
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
        env=env,
    )
    out = str(proc.stdout or "") + str(proc.stderr or "")
    start = out.find(PROBE_BEGIN)
    stop = out.find(PROBE_END, start + 1)
    if start < 0 or stop < 0:
        raise SystemExit(
            f"[用法] {layer} 层的探针没打印夹标（尾部 400 字）：\n{out[-400:]}"
        )
    payload = json.loads(out[start + len(PROBE_BEGIN) : stop].strip())
    payload["returncode"] = int(proc.returncode)
    return payload


def _stage_dir(layer: str, source: Path, zip_path: Path | None, root: Path) -> tuple[Path, str]:
    """把某一层要装的东西摆进临时 user scripts；回 (scripts_dir, probe_mode)。"""
    scripts = root / layer
    addons = scripts / "addons"
    addons.mkdir(parents=True, exist_ok=True)
    if layer == "source":
        shutil.copytree(
            source, addons / MODULE_NAME, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        return scripts, "dir"
    if layer == "zip":
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(addons)
        return scripts, "dir"
    return scripts, "install"


def run_three_layers(*, blender: bool = True, zip_path: Path | None = None) -> dict:
    target_zip = zip_path or _newest_zip()
    src = source_version()
    layers: dict[str, dict] = {}
    root = Path(tempfile.mkdtemp(prefix="bamod_threelayer_"))
    try:
        for layer in ("source", "zip", "installed"):
            scripts, mode = _stage_dir(layer, ADDON_DIR, target_zip, root)
            if not blender:
                layers[layer] = {"skipped": "没跑 Blender（--no-blender）"}
                continue
            try:
                layers[layer] = _run_probe(
                    scripts_dir=scripts,
                    mode=mode,
                    layer=layer,
                    zip_path=target_zip if mode == "install" else None,
                )
            except SystemExit as exc:
                layers[layer] = {"error": str(exc)}
    finally:
        shutil.rmtree(root, ignore_errors=True)

    versions = {
        "source": src,
        "zip": zip_version(target_zip),
        "installed": str((layers.get("installed") or {}).get("version") or ""),
    }
    # 两层口径分开写：没跑 Blender 时「已安装」是空的，⛔ 不许拿「空 == 空」凑一个 True
    version_match_source_zip = bool(versions["source"]) and versions["source"] == versions["zip"]
    version_match = (
        version_match_source_zip
        and bool(versions["installed"])
        and versions["installed"] == versions["source"]
    )

    def _all(names: dict[str, bool]) -> bool:
        return bool(names) and all(names.values())

    registry = {
        layer: {
            "error": str((layers.get(layer) or {}).get("error") or ""),
            "panels": (layers.get(layer) or {}).get("panels") or {},
            "ops": (layers.get(layer) or {}).get("ops") or {},
            "propertygroup": bool((layers.get(layer) or {}).get("propertygroup_dir")),
            "required_ok": _all(
                {
                    **((layers.get(layer) or {}).get("panels") or {}),
                    **((layers.get(layer) or {}).get("ops") or {}),
                }
            ),
            "required_ok_full": False,
        }
        for layer in ("source", "zip", "installed")
    }
    for row in registry.values():
        panels = row["panels"]
        ops = row["ops"]
        row["required_ok"] = all(
            panels.get(name) for name in REQUIRED_PANELS
        ) and all(ops.get(name) for name in REQUIRED_OPS)
        row["required_ok_full"] = bool(row["required_ok"] and row["propertygroup"])
        row["selfcheck_ok"] = all(
            panels.get(name) for name in SELFCHECK_PANELS
        ) and all(ops.get(name) for name in SELFCHECK_OPS)
    required_ok = all(row["required_ok_full"] for row in registry.values())
    selfcheck_ok = all(row["selfcheck_ok"] for row in registry.values())
    return {
        "schema": "bamod-threelayer/1",
        "zip": str(target_zip),
        "versions": versions,
        "version_match": version_match,
        "version_match_source_zip": version_match_source_zip,
        "required_panels": list(REQUIRED_PANELS),
        "required_ops": list(REQUIRED_OPS),
        "selfcheck_panels": list(SELFCHECK_PANELS),
        "selfcheck_ops": list(SELFCHECK_OPS),
        "registry": registry,
        "required_ok": required_ok,
        "selfcheck_ok": selfcheck_ok,
        "layers": layers,
    }


def _newest_zip() -> Path:
    """最新的那个 addon zip；`dist/` 是空的就**现打一个**（判据一行就是一条命令跑完）。"""
    hits = sorted(DIST_DIR.glob(f"{MODULE_NAME}-*.zip"))
    if hits:
        return hits[-1]
    builder = _load_build_zip()
    report = builder.build_zip()
    return Path(str(report["path"]))


def _load_build_zip() -> object:
    """按路径加载同目录的 build_addon_zip（tools/ 不是包，⛔ 不能 import tools.xxx）。"""
    import importlib.util

    path = Path(__file__).resolve().parent / "build_addon_zip.py"
    spec = importlib.util.spec_from_file_location("bamod_build_addon_zip", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"[用法] 加载不了 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def human_table(report: dict) -> list[str]:
    """人话表（面板 / 交接都贴这一段）。"""
    versions = report["versions"]
    lines = [
        f"三层版本：源码 {versions['source']} / zip {versions['zip']}"
        f" / 已安装 {versions['installed'] or '（没读到）'} -> 一致={report['version_match']}",
        f"与接线无关的注册名（{', '.join(report['required_panels'] + report['required_ops'])}"
        f" + PropertyGroup）：三层全过={report['required_ok']}",
    ]
    for layer in ("source", "zip", "installed"):
        row = report["registry"][layer]
        lines.append(
            f"  {layer:<9} required={row['required_ok_full']}"
            f" 自检面板三件={row['selfcheck_ok']} error={row['error'] or '-'}"
        )
    lines.append(
        "自检面板三件（"
        + ", ".join(report["selfcheck_panels"] + report["selfcheck_ops"])
        + f"）三层注册：{report['selfcheck_ok']}"
        "（Lead 接线前预期为 False，如实报）"
    )
    return lines


def _own_argv(argv: list[str] | None) -> list[str]:
    """取「本工具自己的」参数。

    ⚠ Blender 用 `--python 本文件 -- --probe …` 起我们时，`sys.argv` 里还塞着
    `-b / --factory-startup / --python …` 那一截 —— 直接喂 argparse 会
    "unrecognized arguments"（本文件第一版就踩了这条）。
    """
    if argv is not None:
        return list(argv)
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1 :]
    return sys.argv[1:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三层验收：源码 / zip / 已安装")
    parser.add_argument("--probe", choices=("dir", "install"), default=None, help="Blender 内用")
    parser.add_argument("--layer", default="")
    parser.add_argument("--scripts", type=Path, default=None)
    parser.add_argument("--zip", type=Path, default=None)
    parser.add_argument("--no-blender", action="store_true", help="只比源码↔zip 两层版本")
    parser.add_argument("--require-selfcheck", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--list-required", action="store_true")
    args = parser.parse_args(_own_argv(argv))

    if args.list_required:
        print(json.dumps({
            "required_panels": list(REQUIRED_PANELS),
            "required_ops": list(REQUIRED_OPS),
            "propertygroup": REQUIRED_PROPERTYGROUP,
            "selfcheck_panels": list(SELFCHECK_PANELS),
            "selfcheck_ops": list(SELFCHECK_OPS),
        }, ensure_ascii=False))
        return 0
    if args.probe is not None:
        return probe_main(args)

    report = run_three_layers(blender=not args.no_blender, zip_path=args.zip)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(PROBE_BEGIN)
    print(json.dumps(report, ensure_ascii=False))
    print(PROBE_END)
    for line in human_table(report):
        print(line)
    ok = report["version_match"] and report["required_ok"]
    if args.require_selfcheck:
        ok = ok and report["selfcheck_ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
