r"""解一行卡片：本地化键 -> 静态字段偏移 -> 读点（触发条件与取值就在那附近）。

用法（在 BA_Mod_Maker_2.0 目录下）：

    .venv\Scripts\python.exe tools\find_card_row.py --list
    .venv\Scripts\python.exe tools\find_card_row.py ui_infocard_ammo_ignore_cover
    .venv\Scripts\python.exe tools\find_card_row.py 无视掩体        # 中文也行（读游戏本地化反查）
    .venv\Scripts\python.exe tools\find_card_row.py 齐射时长 --all   # 全 isil 树里搜读点

为什么要有它（见 kb/13-怎么解一条卡片行.md）：

卡上每一行的标题都来自 `InfoCardLocalization`，而那个类的静态初始化里**每个键都有**一句
`Move rdx, "ui_infocard_xxx"` + `LoadAddress r8, [rbp+NNN]`，后面接 `Move [rax+OFF], reg`
把它存进静态字段。所以「哪一行」这件事根本不用在汇编里瞎找：

    ① 键 -> 字段偏移（本脚本第一步，机械可做）
    ② 拿偏移去 `Menu/Arsenal/InfoCard/*.txt` 搜 `[reg+OFF]` —— 命中处就是读点
    ③ 读点那一段里写着**触发条件、取值、格式**（这才是真正要读汇编的地方，且只需读一小段）

前置：`_work/isil/`（反编译产物，不入库）。没有它会明确报错告诉你缺什么。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

ROOT = Path(__file__).resolve().parents[1]

#: 反编译产物的根（`_work/` 不入库）。
ISIL = WORK / "isil"

#: 键 -> 字段偏移 的解析来源。
LOC_FILE = "InfoCardLocalization.txt"

_KEY_RE = re.compile(r'Move rdx, "(ui_[a-z0-9_]+)"')
_SLOT_RE = re.compile(r"LoadAddress r8, \[rbp\+(\d+)\]")
_MOV_RE = re.compile(r"Move (r[a-z0-9]+), \[rbp\+(\d+)\]")
_STORE_RE = re.compile(r"Move \[rax\+(\d+)\], (r[a-z0-9]+)\b")
_READ_RE = re.compile(r"\[(r[a-z0-9]+)\+(\d+)\]")


def _find(name: str, *, under: Path) -> Path | None:
    """在 under 下按文件名找（找不到返回 None）。"""
    for path in under.rglob(name):
        return path
    return None


def key_offsets(loc_path: Path) -> dict[str, int]:
    """本地化键 -> `InfoCardLocalization` 的静态字段偏移。

    静态初始化是"先解析到栈槽、再存进静态字段"两段式：

        Move rdx, "ui_infocard_xxx"      ; 键
        LoadAddress r8, [rbp+424]        ; 出参：栈槽 424
        ...
        Move rbx, [rbp+424]              ; 取回栈槽
        ...
        Move [rax+648], rbx              ; 存进静态字段 +648

    所以先把 (栈槽 -> 键)、(栈槽 -> 偏移) 两张表都抽出来，再合并。
    """
    lines = loc_path.read_text(encoding="utf-8", errors="replace").splitlines()
    slot_key: dict[int, str] = {}
    pending: str | None = None
    for line in lines:
        hit = _KEY_RE.search(line)
        if hit:
            pending = hit.group(1)
        hit = _SLOT_RE.search(line)
        if hit and pending:
            slot_key[int(hit.group(1))] = pending
            pending = None

    slot_offset: dict[int, int] = {}
    for index, line in enumerate(lines):
        hit = _MOV_RE.search(line)
        if not hit:
            continue
        register, slot = hit.group(1), int(hit.group(2))
        for later in lines[index + 1 : index + 40]:
            store = _STORE_RE.search(later)
            if store and store.group(2) == register:
                slot_offset.setdefault(slot, int(store.group(1)))
                break

    return {key: slot_offset[slot] for slot, key in slot_key.items() if slot in slot_offset}


def key_for_chinese(label: str) -> list[str]:
    """中文标签 -> 本地化键（可能多条：键与文案不是一一对应）。"""
    sys.path.insert(0, str(ROOT / "src"))
    from ba_mod_maker.app.viewmodel.unit_card import CardData
    from ba_mod_maker.core.data import find_dataset_root, load_dataset

    data = CardData(load_dataset(find_dataset_root()).instance("52085"))
    wanted = label.strip()
    return sorted(
        key
        for key, text in data.loc.entries.items()
        if key.startswith("ui_") and text.split("\n", 1)[0].strip() == wanted
    )


def read_sites(
    offset: int, *, search_root: Path, context: int = 8, limit: int = 6
) -> list[tuple[Path, int, list[str]]]:
    """搜 `[reg+OFF]` 的命中处，连带上下文几行一起给。

    同一个文件里的相邻命中合并成一个块（一个方法里往往连着读好几次这个字段），
    每个文件只留第一块，最多给 `limit` 个文件 —— 读点是给人看的，不是越多越好。
    """
    hits: list[tuple[Path, int, list[str]]] = []
    needle = f"+{offset}]"
    for path in sorted(search_root.rglob("*.txt")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        first = next(
            (index for index, line in enumerate(lines) if needle in line and _READ_RE.search(line)),
            None,
        )
        if first is None:
            continue
        lo = max(0, first - context)
        hits.append((path, first + 1, lines[lo : first + context + 1]))
        if len(hits) >= limit:
            break
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="解一行卡片：键 -> 字段偏移 -> 读点")
    parser.add_argument("query", nargs="?", default="", help="本地化键，或卡片上那行的中文名")
    parser.add_argument("--list", action="store_true", help="把 (键 -> 偏移) 全表打出来")
    parser.add_argument(
        "--all", action="store_true", help="全 isil 树里搜读点（默认只搜 InfoCard）"
    )
    parser.add_argument("--context", type=int, default=8, help="读点上下各打几行")
    parser.add_argument("--limit", type=int, default=6, help="最多打几个文件")
    args = parser.parse_args(argv)

    loc = _find(LOC_FILE, under=ISIL)
    if loc is None:
        print(
            f"找不到 {LOC_FILE} —— 需要反编译产物 _work/isil/（不入库）。\n"
            "它从哪来见 kb/07-IL2CPP还原可行性.md；本脚本只是读它。",
            file=sys.stderr,
        )
        return 2
    table = key_offsets(loc)

    if args.list or not args.query:
        print(f"键 -> 字段偏移（来源 {loc.relative_to(ROOT)}，共 {len(table)} 条）")
        for key, offset in sorted(table.items(), key=lambda item: item[1]):
            print(f"  {offset:#06x}  {key}")
        if not args.list:
            print("\n给一个键或中文名就能找读点，例：find_card_row.py 无视掩体")
        return 0

    query = args.query
    keys = [query] if query.startswith("ui_") else key_for_chinese(query)
    if not keys:
        print(f"本地化表里没有叫「{query}」的键/文案", file=sys.stderr)
        return 1

    # 默认只在 InfoCard 目录里搜 —— 那正是 `InfoCardLocalization.txt` 所在目录
    # （卡片的读点全在里面；全树搜会先命中一堆同名偏移的无关类）。
    search_root = ISIL if args.all else loc.parent
    for key in keys:
        offset = table.get(key)
        head = f"{offset:#06x}" if offset is not None else "没解出偏移"
        print(f"\n=== {key} -> 字段偏移 {head}")
        if offset is None:
            continue
        hits = read_sites(offset, search_root=search_root, context=args.context, limit=args.limit)
        if not hits:
            print(f"   {search_root.relative_to(ROOT)} 里没有 `+{offset}]` 的读点（试试 --all）")
            continue
        for path, line_no, block in hits:
            print(f"\n   {path.relative_to(ROOT)}:{line_no}")
            base = line_no - args.context
            for i, text in enumerate(block):
                print(f"     {base + i:>6}| {text.strip()[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
