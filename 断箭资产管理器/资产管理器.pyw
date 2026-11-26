# -*- coding: utf-8 -*-
r"""断箭资产管理器 · 启动器（双击 = 无控制台、无 .cmd、只起一个 python 进程）。

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


def repo_dir():
    #: 2026-11-04：仓改名成「数据库编辑器」⇒ 按结构找（有 src/ba_mod_maker 的就是），不按名字前缀。
    for folder in sorted(WORKSPACE.iterdir()):
        if (folder / "src" / "ba_mod_maker").is_dir():
            return folder
    return None


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
        mb.showerror("断箭资产管理器", msg)
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
    target = HERE / "gui.py"
    if not target.is_file():
        fail("找不到 gui.py：" + os.linesep + str(target))
        return 1
    if os.environ.get("ASSET_MANAGER_TEST"):
        print("WOULD=%s %s single_proc=%s" % (exe, target, base is not None))
        return 0
    subprocess.Popen([str(exe), str(target)], cwd=str(HERE), env=env)
    return 0


__name__ == "__main__" and main()