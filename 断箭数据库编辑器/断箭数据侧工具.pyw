# -*- coding: utf-8 -*-
r"""断箭数据侧工具 · 启动器（双击 = 无控制台、无 .cmd、只起一个 python 进程）。

2026-11-02 用户要求：GUI 启动不许「跳几次」。实测：venv 的 Scripts\pythonw.exe 是个 launcher
（249,856 B），它会再拉起 uv 的基础解释器 ⇒ 两个进程。
这里改成：直接起 pyvenv.cfg 里 home 指向的基础解释器，用 PYTHONPATH 指到 venv 的
site-packages + src ⇒ 实测 COUNT=1，import PySide6 / ba_mod_maker 都 OK。
找不到 pyvenv.cfg 就退回 venv 的 pythonw（老行为，仍可用）。
"""
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent


def _is_repo(folder):
    """仓的判据是**结构**（有 src/ba_mod_maker），不是名字。"""
    return (folder / "src" / "ba_mod_maker").is_dir()


def repo_dir():
    """找仓根。

    2026-11-09：仓已改名成「断箭数据库编辑器」。原来只按 `BA_Mod_Maker_2.0*` 前缀扫，
    改名后一个候选都不成立 ⇒ repo=None ⇒ 双击就弹「找不到仓内 .venv：None」。
    现在：① 启动器自己就在仓根（HERE）⇒ 先认它；② 否则按结构扫工作区子目录，优先带 .venv 的。
    """
    if _is_repo(HERE):
        return HERE
    fallback = None
    try:
        children = sorted(WORKSPACE.iterdir())
    except OSError:
        return None
    for folder in children:
        if not folder.is_dir() or not _is_repo(folder):
            continue
        if (folder / ".venv").is_dir():
            return folder
        fallback = fallback or folder
    return fallback


def base_pythonw(venv):
    """读 pyvenv.cfg 的 home，拿到基础解释器（绕过 venv launcher 那一跳）。"""
    cfg = venv / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().lower() != "home":
                continue
            cand = Path(value.strip()) / "pythonw.exe"
            if cand.is_file():
                return cand
    except Exception:
        pass
    return None


def launch_env(repo, venv):
    env = dict(os.environ)
    parts = [str(venv / "Lib" / "site-packages"), str(repo / "src")]
    if os.environ.get("PYTHONPATH"):
        parts.append(os.environ["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def fail(msg):
    try:
        import tkinter as tk
        import tkinter.messagebox as mb
        root = tk.Tk()
        root.withdraw()
        mb.showerror("断箭数据侧工具", msg)
    except Exception:
        pass


def main():
    repo = repo_dir()
    venv = repo / ".venv" if repo else None
    if repo is None or venv is None or not venv.is_dir():
        fail("找不到仓内 .venv：" + os.linesep + str(venv))
        return 1
    base = base_pythonw(venv)
    exe = base if base is not None else venv / "Scripts" / "pythonw.exe"
    env = launch_env(repo, venv)
    if os.environ.get("ASSET_MANAGER_TEST"):
        print("WOULD=%s -m ba_mod_maker.app single_proc=%s" % (exe, base is not None))
        return 0
    # ⛔ 不带 -X faulthandler：实测 pythonw 下它会直接 exit code=1（静默死），代价是没有崩溃栈。
    subprocess.Popen([str(exe), "-m", "ba_mod_maker.app"], cwd=str(repo), env=env)
    return 0


__name__ == "__main__" and main()