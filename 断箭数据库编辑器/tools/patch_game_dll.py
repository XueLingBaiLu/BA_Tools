"""外部数据库补丁（GameAssembly.dll）—— **薄封装**。

⚠️ 逻辑只有一份，在 `src/ba_mod_maker/core/asset/dllpatch.py`。
本脚本只是把它包成命令行，方便不装产品、不进 GUI 时直接用。

为什么改成薄封装：
此前这里有一份**独立的实现**（自己的地址表与 cave 构造）。
两份实现意味着两套常量 —— 游戏更新后改一处忘一处，
症状是"一个入口能打、另一个入口打坏 DLL"。所以全部收敛到 core。

用法（与产品 CLI 完全等价）：

    python tools\\patch_game_dll.py status
    python tools\\patch_game_dll.py apply       # 需未打过补丁，会先备份
    python tools\\patch_game_dll.py verify
    python tools\\patch_game_dll.py restore

    --game DIR   指定游戏根目录（默认取 BA_MOD_MAKER_GAME，再退回实测路径）
    --force      当前 DLL 不是已知原版时也继续打

等价的产品命令：`ba-mod-maker patch status|apply|restore|verify`
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让脚本能直接跑（不必先 pip install -e .）
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ba_mod_maker.core.asset import dllpatch  # noqa: E402
from ba_mod_maker.core.errors import UserFacingError  # noqa: E402


def game_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("BA_MOD_MAKER_GAME")
    if env:
        return Path(env)
    return Path(r"D:\Steam\steamapps\common\broken_arrow")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="外部数据库补丁（GameAssembly.dll）")
    ap.add_argument("action", choices=("status", "apply", "restore", "verify"))
    ap.add_argument("--game")
    ap.add_argument("--force", action="store_true", help="即使原版 sha256 不在已知表里也继续")
    a = ap.parse_args(argv)

    root = game_dir(a.game)
    st = dllpatch.status(root)
    if st.state is dllpatch.PatchState.MISSING:
        print(f"找不到 {st.dll}")
        return 2

    try:
        if a.action == "status":
            for line in st.summary_lines():
                print(line)
            print()
            print(dllpatch.uninstall_hint())
            return 0

        if a.action == "verify":
            ok, message = dllpatch.verify(root)
            print(f"verify: {message}")
            return 0 if ok else 1

        if a.action == "apply":
            print(dllpatch.apply(root, force=a.force))
            if not st.db_zip_present:
                print(f"提示：游戏根目录还没有 {dllpatch.PACKAGE_NAME} —— 补丁处于休眠状态。")
            return 0

        print(dllpatch.restore(root))
        return 0
    except UserFacingError as exc:
        print(exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
