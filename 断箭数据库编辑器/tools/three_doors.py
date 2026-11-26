r"""三条门 —— 把**一个症状/关键词**摆成「产品代码 / 数据库 / 反编译」三扇门，再打印「读经三行」。

由来（2026-10-01，用户问的）：

    用户：「在我提示之前，你有没有自己通读产品代码、数据库、反编译代码？」
    答：**没有** —— 当时 AGENTS.md §0.5 就在上下文里，可它的触发条件是
    「要解卡片上某一行 / 某个值之前」，而那次的开场是「用户报一个症状」；
    那一刻我不觉得自己在解字段，规则根本没上桌。**规则写了 ≠ 规则会触发。**

所以入口压到一条命令（口径见 AGENTS.md §0.4「开工门」）：

    .venv\Scripts\python.exe tools\three_doors.py 点了不亮
    .venv\Scripts\python.exe tools\three_doors.py Options.IsDefault
    .venv\Scripts\python.exe tools\three_doors.py slot_selected_option

关键词三种写法都认：**症状用大白话**（点了不亮 / 显示不全）、**字段用 表.列**、**函数用名字**。

❗ 它**只摆门、不替人读**：

- 门 2 那一行要的是**实跑出来的数字**（例：615 个槽 / 436 个没有 IsDefault），不是"我看了一眼"；
- 「某个字段落在哪一行」仍然只有一份实现：`tools/find_card_row.py`（本工具只指路，不复制那份映射）；
- 通读 = **打开命中的那个文件整个读**，不是只读命中那几行 —— 2026-10-01 那一轮就是这么漏掉
  `unit_card.py::option_groups`（真正藏着根因的那一段）的。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: tools/ 不在包里 —— 门 0 的**唯一实现**是 tools/kb_lookup.py（AGENTS §5「逻辑只有一份」）。
#: 本文件只把它的输出摆到第一扇门，**不复制那份检索逻辑**。
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

#: 门 3 的**唯一实现**（2026-10-10 加）：原来这一扇只按**文件名**匹配 _work/isil，
#: 不读内容、也**完全没有 dump.cs 的入口** ⇒ 读 LOD 机制那次靠手工 Get-ChildItem 才找到文件。
import decomp_index
import kb_lookup

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"
DOCS = ROOT / "docs"
KB = ROOT / "kb"
ISIL = WORK / "isil"

#: 数据库那扇门的三份文档（“哪一列 / 什么语义 / 卡上哪一格”都在这里）。**先查它们，再翻反编译。**
DB_DOCS = (
    DOCS / "数据-表与列清单.md",
    DOCS / "字段词典-词条中文.md",
    DOCS / "字段词典-枚举与掩码.md",
    DOCS / "数据-信息卡字段映射.md",
)

#: 反编译那扇门：VA 一览与“怎么解一条卡片行”的操作版。
VA_DOCS = (DOCS / "研究-单位卡片公式.md", KB / "13-怎么解一条卡片行.md")

#: ★ 门 0（2026-10-10 加）：**整个知识库** —— kb/ 全部 + docs/ 全部。
#: 为什么加：本文件一直定义了 KB，但**只在门 3 用了一份**（kb/13），
#: 其余 30+ 份 kb 不在任何一扇门里。
#: 代价（实测）：问「改单位外观该动哪儿」→ 三扇门全 0 命中，答案却白纸黑字在
#: `kb/21-贴图方向契约与素材管线.md:131`（皮肤不是 DB 表，是 UserItemsConfig）。
#: ⇒ **凡是不在门里的地方，就等于没有。**

#: 单文件超过这个大小就不逐行扫（几十万行的 isil dump 只按**文件名**找）。
MAX_TEXT_BYTES = 2_000_000

READING_TEMPLATE = """读经（改代码之前）
- 产品代码：<文件>::<函数> —— 读了哪一段、为什么是它
- 数据库  ：<表>.<列> —— 附一条**实跑出来**的数字（不是“我看了一眼”）
- 反编译  ：<_work/isil/...txt:行  或  VA> —— **没读就写「没读」**，并写清依据是哪份文档"""


def _text_files(roots: tuple[Path, ...], suffixes: tuple[str, ...]):
    """这些**目录或文件**里的文本文件（跳过 build 缓存 / 超过上限的大文件）。

    ❗ 两种都收：门 2/3 传的是**指定的几份文档**（文件），门 1 传的是 src/tests（目录）。
    第一版只认目录，于是门 2/3 永远报"没命中" —— 跑一次就露馅（不是想出来的）。
    """
    for root in roots:
        if root.is_file():
            if root.suffix in suffixes:
                yield root
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            try:
                if path.stat().st_size > MAX_TEXT_BYTES:
                    continue
            except OSError:
                continue
            yield path


def _needles(needle: str) -> list[str]:
    """搜索用的写法：原样 + 「表.列」的**列名那一半**。

    ❗ 为什么要有这一条：列词典是按**列名**编排的（标题写作「IsDefault · 缺省选中」），
    而人给的关键词常带着表名（Options.IsDefault）—— 只按原样搜会一份都命不中
    （第一版就是这样，门 2 永远说"没命中"）。
    """
    out = [needle]
    if "." in needle:
        tail = needle.rsplit(".", 1)[-1].strip()
        if tail and tail.lower() != needle.lower():
            out.append(tail)
    return out


def _grep(paths, needle: str, per_file: int = 4, max_files: int = 8):
    """逐行扫，大小写不敏感。返回 [(文件, [(行号, 行文本), ...]), ...]。"""
    lowered = needle.lower()
    hits: list[tuple[Path, list[tuple[int, str]]]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found = [
            (number, line.strip())
            for number, line in enumerate(text.splitlines(), 1)
            if lowered in line.lower()
        ]
        if found:
            hits.append((path, found[:per_file]))
            if len(hits) >= max_files:
                break
    return hits


def _grep_any(paths, needle: str, per_file: int = 3, max_files: int = 6):
    """按 needles 里的写法依次试，返回**第一组命中的**（附用的是哪种写法）。

    ❗ paths 先落成元组：它是生成器（_text_files），**第一个候选词就把它读干了** ——
    第二个候选词对着一个空迭代器搜，结果是"永远说没命中"。实测踩过：门 3 对
    Options.IsDefault 明明有 IsDefault 的命中却报空。跑一次就露馅。
    """
    paths = tuple(paths)
    for candidate in _needles(needle):
        hits = _grep(paths, candidate, per_file=per_file, max_files=max_files)
        if hits:
            return hits, candidate
    return [], needle


def _recent_notes(limit: int = 6):
    """上一轮的现场：`docs/` 与 `_work/*.md` 里最近改过的几份。"""
    notes = [p for p in (*DOCS.glob("*.md"), *(WORK).glob("*.md")) if p.is_file()]
    notes.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return notes[:limit]



def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


#: 语料只读一次（门 0 每次都要用它）。
_KB_DOCS: list = []


def _print_kb_door(needle: str) -> None:
    """门 0：知识库检索。**先查这里**，再往下三扇。

    ❗ 2026-10-11 换实现：原来是**整句子串匹配**，实测 6 条自然语言问句 **6/6 全 0 命中**，
    而且会打印「这只说明**这份知识库没写过**」—— 那是一句**假阴性的劝退**，
    典型案例：问「怎么造一个炮塔包」报"没写过"，而那个文件的**名字就是这句**。
    现在整扇门交给 tools/kb_lookup.py（分词 + 标题/文件名加权 + 强命中判定），
    0 命中时**只许说「本工具不能证明知识库里没有」**并摆出入口清单。
    """
    print("=== 门 0/3 · 知识库（唯一实现 = tools/kb_lookup.py）—— 先查这里，再看下面三扇 ===")
    if not _KB_DOCS:
        _KB_DOCS.extend(kb_lookup.load())
    print(kb_lookup.render(kb_lookup.search(needle, _KB_DOCS), needle))


def _print_code_door(needle: str) -> None:
    print("=== 门 1/3 · 产品代码（src/ + tests/）===")
    hits = _grep(_text_files((SRC, TESTS), (".py",)), needle)
    if not hits:
        print(f"  没命中「{needle}」—— 换成大白话的症状、`表.列`、或一个函数名再试。")
    for path, found in hits:
        print(f"  {_rel(path)}  （{len(found)} 处）")
        for number, line in found:
            print(f"      {number}: {line[:150]}")
    print("  ⚠️ 通读 = 打开命中那个文件**整个读**，不是只读这几行 —— 2026-10-01 那一轮就是这么漏掉")
    print("     `unit_card.py::option_groups`（真正藏着根因的那一段）的。")
    print("  📌 上一轮的现场（最近改过的文档，先扫一眼有没有人已经解过）：")
    for note in _recent_notes():
        print(f"      {_rel(note)}")


def _print_db_door(needle: str) -> None:
    print()
    print("=== 门 2/3 · 数据库（先查列，再翻反编译）===")
    hits, used = _grep_any(_text_files(DB_DOCS, (".md",)), needle, per_file=3, max_files=6)
    if hits:
        print(f"  （按「{used}」搜到）")
    else:
        print(f"  这四份里没命中「{needle}」—— 按**列名**再搜一次：")
        print("  （Dispersion / IgnoreCover 这类一搜就有）")
        for doc in DB_DOCS:
            print(f"      {_rel(doc)}")
    for path, found in hits:
        print(f"  {_rel(path)}")
        for number, line in found:
            print(f"      {number}: {line[:160]}")
    print("  下一跳（**机械的，别靠文件名猜**）：")
    print("      .venv\\Scripts\\python.exe tools\\find_card_row.py " + needle)
    print("      取一列的取值分布：")
    print("          .venv\\Scripts\\python.exe _work\\probe_enum_scan.py --col 表.列")
    print("  ⚠️ 读经那一行要的是**实跑出来的数字**")
    print("     （例：615 个槽 / 436 个没有 IsDefault），不是“我看了一眼”。")


def _print_decompile_door(needle: str) -> None:
    print()
    print("=== 门 3/3 · 反编译（只回答“读哪一列、什么条件”）===")
    doc_hits, used = _grep_any(_text_files(VA_DOCS, (".md",)), needle, per_file=3, max_files=4)
    if doc_hits:
        print(f"  （按「{used}」搜到）")
        for path, found in doc_hits:
            print(f"  {_rel(path)}")
            for number, line in found:
                print(f"      {number}: {line[:160]}")
    else:
        print(f"  VA 那两份文档里没命中「{needle}」—— 按**函数名或字段名**再搜一次：")
        for doc in VA_DOCS:
            print(f"      {_rel(doc)}")
    print("  ── 反编译索引（唯一实现 = tools/decomp_index.py）──")
    print("     全部类型/字段/方法 ＋ 24,059 个有函数体的 txt")
    if (decomp_index.INDEX_DIR / decomp_index.TYPES_TSV).is_file():
        out = decomp_index.render(
            decomp_index.query(needle, decomp_index.INDEX_DIR), decomp_index.INDEX_DIR)
        for line in out.splitlines()[1:]:          # 去掉它自带的表头（本门已有抬头）
            print("  " + line)
    else:
        print("      ⛔ 索引还没建 ⇒ .venv\\Scripts\\python.exe tools\\decomp_index.py build")
        print("         （一次性，约 12 秒）")
        print("         索引不存在时**不许**说「反编译里没有」—— 那是没查，不是没有。")
    print("  ⚠️ 没读就**明写「没读」**，并写清依据是哪份文档 —— 这一行才是“读过”和“没读”的区别。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把一个症状/关键词摆成「产品代码 / 数据库 / 反编译」三扇门 + 读经三行。",
    )
    parser.add_argument(
        "keyword", nargs="*", help="症状（点了不亮）/ 表.列（Options.IsDefault）/ 函数名"
    )
    args = parser.parse_args(argv)
    needle = " ".join(args.keyword).strip()
    if not needle:
        parser.print_help()
        print()
        print(READING_TEMPLATE)
        return 2
    print(f"# 关键词：{needle}")
    print()
    _print_kb_door(needle)
    print()
    _print_code_door(needle)
    _print_db_door(needle)
    _print_decompile_door(needle)
    print()
    print("=== 读经三行（贴进回复 / 交接 / 提交信息；缺哪样就写「没读」）===")
    print(READING_TEMPLATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
