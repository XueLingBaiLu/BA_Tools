r"""tools/install_kb_plugin.py —— 装 / 查 / 卸 dsh-kb-lookup 插件（每轮注入知识库强命中）。

判据一行：

    .venv\Scripts\python.exe tools\install_kb_plugin.py --check
    -> 源 / 目标 / 补丁条目 **三处齐** = 装好了；任何一处缺就报出来（能红才算尺子）

⛔ **为什么要这个脚本，而不是手敲**：本仓的规矩是「逻辑只有一份、生成物别手改」。
   插件**源**在仓内 ⟪tools/dsh-plugin-kb-lookup/⟫，profile 下那份是**拷贝** ——
   ⟪--check⟫ 就是查这两份有没有漂移。

装到哪里（不是随便放的，见 profile 自己的踩坑记录）：

    ~/.dsh/profiles/desktop/node_modules/dsh-kb-lookup/     ← **当前 profile 自己的** node_modules
    ⛔ 不能放 ~/.dsh/profiles/node_modules/（那是 sharedFallbackDirectory，
       Desktop 的 ESM 解析器会判 PackageOverlayNotFoundError）
"""

from __future__ import annotations

import argparse
import contextlib
import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: 本机控制台默认 GBK ⇒ 直接 print("✅ …") 会 UnicodeEncodeError 把**成功路径**判成 rc=1
#: （2026-10-10 独立核验实测）。照本仓别的工具一样把两个流钉成 utf-8。
for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "tools" / "dsh-plugin-kb-lookup"
PKG_NAME = "dsh-kb-lookup"
PATCH_FILE = "cordis.patch.yml"

BEGIN = "# >>> kb-lookup-plugin (managed by tools/install_kb_plugin.py) >>>"
END = "# <<< kb-lookup-plugin <<<"


def profile_dir() -> Path:
    """当前 profile 目录。DSH_PROFILE_DIR 优先（DSH 自己就是这么告诉插件的）。"""
    env = os.environ.get("DSH_PROFILE_DIR")
    if env:
        return Path(env)
    home = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
    return home / "profiles" / "desktop"


def patch_block(profile: Path) -> str:
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    script = REPO_ROOT / "tools" / "kb_lookup.py"
    #: ★ 同一个插件里挂第二条注入（2026-10-11）：成功率闸。
    #: 用户原话「你能不能让 Agent 自己会用，而不是有这个功能但不用」——
    #: 判定在 tools/success_guard.py（--inject 有信号才输出），这里只喂路径。
    guard = REPO_ROOT / "tools" / "success_guard.py"
    return (
        f"{BEGIN}\n"
        f"# 每轮两条注入：① 知识库强命中（{script}）② 成功率闸（{guard}）。\n"
        f"# 两条都是「有信号才注入」；判定全在仓内 Python，插件只是薄壳。\n"
        f"# 卸载：python tools/install_kb_plugin.py --uninstall\n"
        f"- insert:\n"
        f"    - id: kb-lookup\n"
        f"      name: '{PKG_NAME}'\n"
        f"      config:\n"
        f"        python: '{python}'\n"
        f"        script: '{script}'\n"
        f"        guard: '{guard}'\n"
        f"        heartbeat: '{WORK / 'kb-lookup-heartbeat.json'}'\n"
        f"{END}\n"
    )


def read_patch(profile: Path) -> str:
    path = profile / PATCH_FILE
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def strip_block(text: str) -> str:
    """把托管块整段拿掉（含首尾标记），其余一字不动。"""
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if line.strip() == BEGIN:
            skipping = True
            continue
        if line.strip() == END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def files_match(src: Path, dst: Path) -> bool:
    names = ("index.js", "package.json", "selftest.mjs")
    return all((dst / n).is_file() and filecmp.cmp(src / n, dst / n, shallow=False) for n in names)


def do_install(profile: Path) -> int:
    target = profile / "node_modules" / PKG_NAME
    target.mkdir(parents=True, exist_ok=True)
    for name in ("index.js", "package.json", "selftest.mjs"):
        shutil.copyfile(SRC_DIR / name, target / name)
    text = strip_block(read_patch(profile))
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n" + patch_block(profile)
    (profile / PATCH_FILE).write_text(text, encoding="utf-8")
    print(f"已装：{target}")
    print(f"已改：{profile / PATCH_FILE}（加了托管块，卸载即整段删除）")
    return 0


def do_check(profile: Path) -> int:
    target = profile / "node_modules" / PKG_NAME
    patch = read_patch(profile)
    ok_src = all(
        (SRC_DIR / n).is_file() for n in ("index.js", "package.json", "selftest.mjs")
    )
    ok_dst = target.is_dir()
    ok_same = ok_dst and files_match(SRC_DIR, target)
    ok_patch = BEGIN in patch and END in patch
    rows = [
        ("源（仓内）", ok_src, str(SRC_DIR)),
        ("目标（profile node_modules）", ok_dst, str(target)),
        ("与源逐字节一致（无漂移）", ok_same, "" if ok_same else "拷贝与源不一致 ⇒ 重跑装一次"),
        ("补丁条目（cordis.patch.yml）", ok_patch, "" if ok_patch else "托管块不在 ⇒ 不会被加载"),
    ]
    for name, good, extra in rows:
        print(f"  [{'OK' if good else '缺'}] {name}" + (f" —— {extra}" if extra else ""))
    #: ★ 心跳：宿主**挂载过**本插件的唯一凭证。没有它 = 从没加载过（或装完还没重启）。
    hb = WORK / "kb-lookup-heartbeat.json"
    if hb.is_file():
        print(f"  心跳：{hb.read_text(encoding='utf-8').strip()[:220]}")
    else:
        print("  心跳：**没有** —— 宿主从没加载过本插件（或装完还没重启过）")
    if all(r[1] for r in rows):
        print()
        print("✅ 三处齐 —— 装好了。**重启 DSH 后生效**（插件在宿主启动时加载）。")
        return 0
    print()
    print("❌ 没装齐 —— 跑 --install 装上。")
    return 1


def do_uninstall(profile: Path) -> int:
    target = profile / "node_modules" / PKG_NAME
    if target.is_dir():
        shutil.rmtree(target)
        print(f"已删：{target}")
    patch_path = profile / PATCH_FILE
    text = read_patch(profile)
    new = strip_block(text)
    if new != text:
        patch_path.write_text(new, encoding="utf-8")
        print(f"已改：{patch_path}（托管块已整段移除，别处一字未动）")
    else:
        print(f"补丁里没有托管块：{patch_path}")
    print("⇒ 重启 DSH 后彻底不生效。")
    return 0


def do_plugin_selftest(profile: Path) -> int:
    """在装了的那份上跑**插件自己的**端到端自检（假 ctx 把 apply() 真跑起来）。

    ⚠ 必须在 profile 下跑：只有那里解析得到 ⟪@deepseek-ai/dsh-llm⟫。
    """
    target = profile / "node_modules" / PKG_NAME / "selftest.mjs"
    if not target.is_file():
        print(f"❌ 先装：{target} 不存在（跑 --install）")
        return 1
    proc = subprocess.run(
        ["node", str(target)],
        cwd=str(profile),
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode


def selftest() -> int:
    """在临时目录里跑一遍装/查/卸 —— 尺子必须能红（这里红的是"卸载后还残留"）。"""
    import tempfile

    bad: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        profile = Path(tmp)
        before = "# 别人的配置\n- id: someone-else\n  name: 'x'\n"
        (profile / PATCH_FILE).write_text(before, encoding="utf-8")

        if do_check(profile) == 0:
            bad.append("没装就该报缺，却报了三处齐")
        do_install(profile)
        if do_check(profile) != 0:
            bad.append("装完却报缺")
        if not files_match(SRC_DIR, profile / "node_modules" / PKG_NAME):
            bad.append("装完两份不一致")
        text = read_patch(profile)
        if BEGIN not in text or "someone-else" not in text:
            bad.append("装的时候动了别人的配置")
        do_install(profile)
        text2 = read_patch(profile)
        if text2.count(BEGIN) != 1:
            bad.append(f"装两次出现 {text2.count(BEGIN)} 个托管块（不幂等）")
        do_uninstall(profile)
        text3 = read_patch(profile)
        if BEGIN in text3 or END in text3:
            bad.append("卸载后还残留标记")
        if "someone-else" not in text3:
            bad.append("卸载把别人的配置也删了")
        if (profile / "node_modules" / PKG_NAME).exists():
            bad.append("卸载后目录还在")

    if bad:
        print("❌ 自检失败：")
        for row in bad:
            print(f"   · {row}")
        return 1
    print("✅ 自检全绿（7 条：报缺 / 装 / 一致 / 不动别人 / 幂等 / 卸干净 / 目录删掉）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="装 / 查 / 卸 dsh-kb-lookup 插件。")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="只查三处状态（默认动作）")
    group.add_argument("--install", action="store_true", help="装（拷贝 + 写托管块）")
    group.add_argument("--uninstall", action="store_true", help="卸（删目录 + 移除托管块）")
    group.add_argument("--selftest", action="store_true", help="在临时目录里跑装/查/卸自检")
    group.add_argument(
        "--plugin-selftest",
        action="store_true",
        help="跑**插件自己的**端到端自检（假 ctx 把 apply() 真跑起来，12 条）",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.plugin_selftest:
        return do_plugin_selftest(profile_dir())
    if not SRC_DIR.is_dir():
        print(f"❌ 仓内没有插件源：{SRC_DIR}")
        return 1
    profile = profile_dir()
    print(f"profile：{profile}")
    print()
    if args.install:
        return do_install(profile)
    if args.uninstall:
        return do_uninstall(profile)
    return do_check(profile)


if __name__ == "__main__":
    sys.exit(main())
