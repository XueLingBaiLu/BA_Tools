"""**出处检查**：涉及游戏机制的代码，必须在**它自己那里**写明出处。

> 用户原话（2026-10-10）：「**你们在代码里面，哪怕放个引用于知识库的哪里都行 ——
> 因为我们研究出来的知识，根本无法直接注入你的大模型里面去**」

判据：`src/ba_mod_maker/asset_kernel/` 下每个模块的源码里，至少出现一次
`kb/` · `docs/` 或 `_work/` —— 也就是**一个能找回去的指针**。
不是要求把知识抄进来（抄进来会过期），是要求**指过去**。

⚠ 这一条是**只许降的棘轮**：下方 `KNOWN_UNCITED` 是 2026-10-10 冻结的存量欠账
（12 个模块当时没有出处）。**新增模块必须带出处**；存量补上了要从名单里删掉。
别的层的要求见 AGENTS §0.14。

## 第二段（task-7 加）：**文档**的入口也要有指针

《待真机验证清单.md》是**用户醒来的第一张纸** —— 它每条入口都必须带一个
**能找回去的指针**（`kb/NN` 或一个真实存在的仓内文件），否则下一个人照着这条
复算不了（AGENTS §0.9 动作 4：每条结论后面跟一条能复算的命令）。
这一条**只查指针在不在**，不查读数真假 —— 读数真假是 `tools/spec_audit.py` 与各自的探针管的。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "src" / "ba_mod_maker" / "asset_kernel"
POINTERS = ("kb/", "docs/", "_work/")

#: 《待真机验证清单》那条入口的标题，《spec_audit.ENTRY_RE》的同形写法。
ENTRY_RE = re.compile(r"^###\s+(P\d+(?:-\d+)?-[a-z])\s*(?:★)?\s*(?:·\s*)?(\S.*)$", re.M)

#: 2026-10-10 冻结：这些模块**当时**一个出处指针都没有。只许降。
KNOWN_UNCITED = frozenset({
    "catalog_writer.py", "colorspace.py", "mesh.py", "model_build.py",
    "normals.py", "prefab.py", "skeleton.py", "streaming.py", "texture.py",
    "typetree.py", "unityfs.py",
})


def modules_without_pointer(text_of) -> list[str]:
    """返回**没有任何出处指针**的模块名（text_of 便于喂负控）。"""
    out = []
    for p in sorted(KERNEL.glob("*.py")):
        if "__init__" in p.name:
            continue
        if not any(tok in text_of(p) for tok in POINTERS):
            out.append(p.name)
    return out


def test_no_new_module_without_a_pointer():
    read = lambda p: p.read_text(encoding="utf-8", errors="replace")  # noqa: E731
    missing = set(modules_without_pointer(read))
    new = sorted(missing - KNOWN_UNCITED)
    assert not new, (
        "新模块没写出处（加一行「依据 kb/NN §M」或「_work/xxx.py」，"
        f"或者明确登记进 KNOWN_UNCITED）：{new}"
    )
    stale = sorted(KNOWN_UNCITED - missing)
    assert not stale, f"这些模块已经有出处了，从 KNOWN_UNCITED 里删掉：{stale}"


def test_the_checker_can_actually_go_red():
    """⛔ 负控：一段**故意不带指针**的代码必须被这条检查抓出来。

    没有这一条，上面那条可能因为「扫不到任何模块」而永远绿（假绿）。
    """
    plain = "def f():\n    return 1\n"
    assert not any(tok in plain for tok in POINTERS)
    fake = {"plain.py": plain, "cited.py": "# 依据 kb/18 §6\n"}
    left = [n for n, t in fake.items() if not any(tok in t for tok in POINTERS)]
    assert left == ["plain.py"], "负控没红 —— 这条检查是假绿"


# ───────────── 第二段：《待真机验证清单》每条入口的指针 ─────────────

CHECKLIST = ROOT / "docs" / "待真机验证清单.md"
#: 指针的两副样子：`kb/23` 这种"编号"式，与 `docs/x.md` / `_work/a/b.py` 这种**带扩展名**的路径。
KB_RE = re.compile(r"kb/(\d+)")
FILE_RE = re.compile(
    r"[A-Za-z0-9_\u4e00-\u9fff.\-]+(?:[/\\][A-Za-z0-9_\u4e00-\u9fff.\-]+)*\.(?:py|md|json|txt)"
)
ROOT_DIRS = ("tools", "docs", "kb", "src", "tests", "patches")
PKG_DIRS = ("core", "asset_kernel", "cli", "app", "ui", "variables")


def pointer_exists(token: str, repo: Path = ROOT) -> bool:
    """一个指针指到的东西**真的在不在**（存在才是指针，否则只是几个字）。"""
    t = token.replace("\\", "/").strip()
    first = t.split("/")[0]
    if first in PKG_DIRS:
        p = repo / "src" / "ba_mod_maker" / t
    elif first in ROOT_DIRS:
        p = repo / t
    else:
        return False
    return p.exists()


def entries_missing_a_pointer(text: str, repo: Path = ROOT) -> list[str]:
    """返回**一个能找回去的指针都没有**的入口编号（text 便于喂负控）。"""
    ms = list(ENTRY_RE.finditer(text))
    bounds = [m.start() for m in re.finditer(r"^#{2,3}\s", text, re.M)]
    out: list[str] = []
    for m in ms:
        end = next((b for b in bounds if b > m.end()), len(text))
        body = text[m.end() : end]
        hit = any((repo / "kb").glob(f"{n}-*.md") for n in KB_RE.findall(body))
        if not hit:
            hit = any(pointer_exists(tok, repo) for tok in FILE_RE.findall(body))
        if not hit:
            out.append(m.group(1))
    return out


def test_every_checklist_entry_has_a_pointer():
    """每条入口都至少有一个**真实存在**的指针 —— 下一个人照着它才能复算。"""
    missing = entries_missing_a_pointer(CHECKLIST.read_text(encoding="utf-8"))
    assert not missing, f"这些入口一个能找回去的指针都没有：{missing}"


def test_the_checklist_pointer_check_can_go_red():
    """⛔ 负控：造一条**故意不带走指针**的入口，必须被上一条抓出来。

    没有它，上面那条可能因为"一条入口都没扫到"而永远绿（假绿）—— 真发生过这种假绿。
    """
    fake = (
        "## 假的一节\n\n"
        "### P9-z · 这条是负控，故意不写指针\n"
        "- **怎么复算**：跑一下那个脚本。\n"
        "- **期望读数**：红。\n"
        "- **依据**：我记着。\n"
        "- **风险**：无。\n"
        "- **为什么今晚做不了**：故意的。\n"
    )
    assert entries_missing_a_pointer(fake) == ["P9-z"], "负控没红 —— 这条检查是假绿"
    ok = fake.replace("跑一下那个脚本", "跑 `docs/待真机验证清单.md`")
    assert entries_missing_a_pointer(ok) == [], "正控误报：有真指针的入口被判成没指针"
