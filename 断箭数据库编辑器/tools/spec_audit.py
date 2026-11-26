r"""spec_audit —— 五份 modder 文档的机械审计器（通宵线 P1 的判据）。

用途
----
把 P1 的五份文档按**机械断言**扫一遍，回答这几件事：

  ① 《零件规格书》            —— 12 步齐备 · 每步「规格 / 判据 / 症状」三列齐全
  ② 《烘焙规范》              —— 每条规则都带 依据＝（读数 / 自定 / 待回填 P0-x）· 四个小节齐备
  ③ 《零件规格书-逆向版》      —— 十六环 · 每环「检查项 / 用什么量 / 不合格怎么办」三列齐全
  ④ 《高模挑选清单》          —— 八条属性 · 每条带「好改 / 难改 / 判据」· 许可行含「一票否决」
  ⑤ 《待真机验证清单》        —— **用户醒来的第一张纸**（task-7 纳入）：入口编号唯一 ·
                                 五件齐（怎么复算 / 期望读数 / 依据 / 风险 / 今晚做不了）·
                                 **标题是症状不是疑问句** · 已标废的旧口径必须带标记；
                                 ⛔ 修前它**不在本审计器里** ⇒ 零机械覆盖，LOD 的 bbox 旧红线
                                 就是这么扩散进用户那张纸的
  ⑥ 五份文档引用的**仓内路径**—— 全部必须真实存在（悬空指针 = 红）
  ⑦ **空洞**                 —— 每条带 待回填 的断言必须指名 P0-<n>，不许静默留白

判据一行
-------
    .venv\Scripts\python.exe tools\spec_audit.py
      -> 五份文档各印一行读数；末行「结论：全绿」；**rc = 0**
      -> 任一断言不成立：印 ✘ ＋ 原因；**rc = 1**
      -> 五份文档不在（路径不对）：**rc = 2**

正控 / 负控
----------
* **正控**：五份**原件**必须全绿 —— 不带参数跑的就是它。
  尺子先自证：负控若一条都命中不了，说明这把尺子**红不了**，当时的"全绿"不算数。
* **负控**（`--selftest` 上半段，**18 条文档变异**，**全部必须命中**；都在临时目录里改副本，
  ⛔ 不碰真文档）。每条还多一道：**变异必须真的改动了字** —— 锚点烂掉（被后续回填改掉）
  就印「空操作 ✘」并判失败，⛔ 不许"改不动任何字"却报命中。

    1. 挖掉《零件规格书》第 ⑤ 步的「症状」列      -> 必须报「三列缺失」
    2. 删掉《零件规格书》第 ⑨ 步整节              -> 必须报「步数 11/12」
    3. 把 `待回填 P0-2` 改成 `待回填`               -> 必须报「待回填没指名 P0」
    4. 抽掉《烘焙规范》一条规则的 `依据＝`          -> 必须报「规则缺依据」
    5. 把《烘焙规范》所有 `依据＝读数` 改成 `依据＝自定` -> 必须报「读数条数不足」
    6. 《逆向版》删掉 5 行表行                    -> 必须报「环数不足」
    7. 《逆向版》清空某行的「不合格怎么办」列      -> 必须报「三列有空」
    8. 《高模挑选清单》删掉「许可」那行           -> 必须报「缺许可行」
    9. 把「一票否决」改成「可以考虑」             -> 必须报「许可行没写一票否决」
   10. 注入一条假路径 `_work/这是负控-假路径.py`      -> 必须报「指针缺」

   11. 把**已标废**的 LOD 旧口径塞回《零件规格书》        -> 必须报「已标废的旧口径」
   12. 把它塞回**《逆向版》**（守卫扩展是否真覆盖）        -> 必须报「已标废的旧口径」
   13. 塞一句**没带样本的密度数**                        -> 必须报「密度数没带样本」

   14. 《待真机验证清单》抽掉一条的「期望读数」列          -> 必须报「条目缺列」
   15. 把它一条标题改成疑问句（末尾加 ？）                 -> 必须报「标题是疑问句」
   16. 把它两条改成同一个编号                             -> 必须报「编号重复」
   17. 把已标废的 LOD 旧口径塞进它                         -> 必须报「已标废的旧口径」
   18. 给它塞一条假路径                                   -> 必须报「指针缺」

* **空洞尺子**（`--selftest` 下半段，12 例，表在 `SCAN_CASES`）—— task-7 把 `scan_holes`
  的每一条口径都做成**能红的一例**：例 C 是"台账跨标题必须复位"（修前 3 条 / 真值 1 条）；
  4 条是**负控**（真空洞 —— `| ⑤ | 未读 | 堵在哪 |` —— 照旧必须算出来）；
  3 条是**正控**（task-7 修掉的那 5 条假阳性）。
  ⛔ 这张表**只有一份**：`tests/test_spec_audit.py` 跑的是同一张表，别抄第二份。

  末行必须印「负控 18/18 命中 · 空洞尺子 12/12 命中 / 正控 5 份全绿」，rc = 0。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = ROOT / "docs"

SPEC_BOOK = "零件规格书.md"
BAKING = "烘焙规范.md"
REVERSE = "零件规格书-逆向版.md"
CHECKLIST = "高模挑选清单.md"
#: 《待真机验证清单》= **用户醒来的第一张纸**（task-7 纳入）。
#: 它修前**不在本审计器里** ⇒ 零机械覆盖，已废口径（LOD 的 bbox 旧红线）就是那么扩散进去的。
REALTIME = "待真机验证清单.md"
DOC_NAMES = (SPEC_BOOK, BAKING, REVERSE, CHECKLIST, REALTIME)

STEP_MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"
STEP_RE = re.compile(r"^###\s*([①-⑫])\s*(\S.*)$", re.M)
FIELD_RE = re.compile(r"^\s*[-*]\s*\*\*(规格|判据|症状)\*\*[^\n]{0,200}?[：:]\s*(\S.*)$", re.M)
SUMMARY_RE = re.compile(r"^\|\s*([①-⑫])\s*\|", re.M)
RULE_RE = re.compile(r"^\s*[-*]\s*\*\*([A-Z]\d+)\s*(?:·|\*\*)?")
TAG_RE = re.compile(r"依据\s*[＝=]\s*(读数|自定|待回填)")
#: 《逆向版》那张表的三列（负控里也要用，所以提到模块级）
COLS_REVERSE = ("检查项", "用什么量", "不合格怎么办")
#: 密度单位 —— ⛔ 出现它就**必须**在同一行说明**样本或区间**（task-25 立的规矩）：
#: 密度不是一个常数（同一个 49 件样本内部按面积四分位 = 141.2 ~ 46.6，跨度 2.6×），
#: 所以"单一 150 texel/m"这种写法本身就是误导。
DENSITY_RE = re.compile(r"texels?/(?:m|cm)")
SAMPLE_MARKS = ("样本", "n =", "n=", "口径", "区间", "四分位", "分档", "上表", "见 §")

#: 《待真机验证清单》一条入口的标题：`### P0-a · <一句话症状>`
#: （★ 与分隔的 · 都可省 —— 现文档两种都出现过）。
ENTRY_RE = re.compile(r"^###\s+(P\d+(?:-\d+)?-[a-z])\s*(?:★)?\s*(?:·\s*)?(\S.*)$", re.M)
#: 切一条入口的正文：下一个 ## / ### 标题处收尾（⛔ 不许让它吃到下一节的表格）
SECTION_RE = re.compile(r"^#{2,3}\s", re.M)
#: 每条入口**必须齐**的列：四列（"症状"写在标题里）＋ 文档模板自己的 风险 与 为什么今晚做不了。
ENTRY_COLS = ("怎么复算", "期望读数", "依据", "风险", "为什么今晚做不了")
#: 标题**不许是疑问句**（模板要的是"一句话症状"，不是"一个问题"）。
QUESTION_TAIL_RE = re.compile(r"[?？]\s*$")

PENDING_RE = re.compile(r"待回填")
#: 已回填的读数标记 —— **两种写法**（task-7 扩口径，只加这两种，⛔ 不是"什么都认"的白名单）：
#:   ① `【读数·P0-4】` / `【读数·P0-6 / P0-8】` —— 一条读数可以同时回填两个空洞
#:      （修前只认单编号 ⇒ 规格书 L283/L284、逆向版 L64 被误算成空洞）；
#:   ② 台账里**整行划掉**的写法：`~~3~~ … ✅ **已回填**`（烘焙 L141 / 逆向版 L64 同形）
#:      —— 它带的是正文读数，没有【读数·P0-x】戳。
FILLED_RE = re.compile(r"【读数·P0-\d+(?:\s*/\s*P0-\d+)*】")
FILLED_STRIKE_RE = re.compile(r"~~[^~]+~~")
FILLED_MARK_RE = re.compile(r"已回填")

#: 台账/§0 那行**图例**：整格字面就是**模板** `待回填 P0-x`（`x` 是占位符，不是编号）
#: —— 它定义的是记号本身的含义，**不是一条断言**（烘焙 L14 就是那条假"待回填 1"）。
#: ⛔ 只认"**整格**等于模板"；真空洞行（`| ⑤ | 未读 | 堵在哪 |`）照旧算数（负控守着）。
LEGEND_CELL_RE = re.compile(r"^待回填\s*P0-x$")

#: ⛔ **已标废的旧口径**（P3-5 实测推翻，见 _work/p3lod/lod_spec.md §③）。
#: 规则：**提到它就必须在同一行带上"已废"的证据** —— 否则下一个人会把它当现行规格。
#: 依据：原版只有 54/3,248 = 1.7% 的 LOD 对满足它 ⇒ 当红线会判红 98.3% 的原版件。
CONDEMNED = ("bbox 与 LOD0", "bbox 逐位相同", "1/8 ~ 1/10", "1/8.9", "1/9.0")
CONDEMN_MARKS = ("标废", "已废", "不许当红档", "1.7%", "54/3,248", "禁红线")

#: 已废口径守卫**覆盖哪几份**。
#: task-22 起《逆向版》也进来了（它的第 13 环原来还写着旧口径，已标废）——
#: 不在这个元组里的文档只出**黄**不出红，⚠ 那本身就是"守卫留了个洞"，别再留。
CONDEMNED_DOCS = (SPEC_BOOK, REVERSE, REALTIME)
P0_RE = re.compile(r"P0-[0-9x]")

#: 仓内路径的第一段（决定从哪个根解析）
ROOT_DIRS = ("tools", "docs", "kb", "src", "tests", "patches")
#: 产品包内的模块前缀（解析到 src/ba_mod_maker/）
PKG_DIRS = ("core", "asset_kernel", "cli", "app", "ui", "variables")
PATH_RE = re.compile(
    r"[A-Za-z0-9_\u4e00-\u9fff.\-]+(?:[/\\][A-Za-z0-9_\u4e00-\u9fff.\-]+)*\.(?:py|md|json|txt)"
)

RED = "红"


class Findings:
    """一份文档的审计结果：facts = 印给人看的读数，bad = 红。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.facts: list[str] = []
        self.bad: list[str] = []
        #: 黄：不进 rc，但必须印出来（例：已废口径出现在**写面之外**的文档里）。
        self.warn: list[str] = []

    def red(self, why: str) -> None:
        self.bad.append(why)

    @property
    def ok(self) -> bool:
        return not self.bad


def read_doc(doc_dir: Path, name: str) -> str | None:
    p = doc_dir / name
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8")


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def find_table(text: str, cols: tuple[str, ...]):
    """找到一个表头含全部 cols 的表；回 (列下标, 数据行)。

    数据行 = 表头之后连续以 | 开头的行，遇到分隔行（---）跳过，遇到非表行停。
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if not ln.strip().startswith("|"):
            continue
        cells = split_row(ln)
        if not all(any(c in cell for cell in cells) for c in cols):
            continue
        idx: dict[str, int] = {}
        for c in cols:
            for j, cell in enumerate(cells):
                if c in cell:
                    idx[c] = j
                    break
        rows: list[list[str]] = []
        for ln2 in lines[i + 1:]:
            if not ln2.strip().startswith("|"):
                break
            cells2 = split_row(ln2)
            if all(set(c) <= set("-: ") for c in cells2 if c):
                continue
            rows.append(cells2)
        return idx, rows
    return None, []


# ───────────────────────── ① 零件规格书 ─────────────────────────


def density_lines(text: str) -> list[str]:
    """出现密度单位、却没在同一行说明**样本或区间**的行（task-25）。

    ⛔ 这不是"不许写数"：**带了样本或区间的行照样写数**。缺的是**出处**。
    """
    out: list[str] = []
    for n, ln in enumerate(text.splitlines(), 1):
        if DENSITY_RE.search(ln) and not any(m in ln for m in SAMPLE_MARKS):
            out.append(f"L{n}: {ln.strip()[:60]}")
    return out


def condemned_lines(text: str) -> list[str]:
    """返回**提到已标废旧口径却没带"已废"证据**的那些行（P3-5）。

    ⛔ 这不是"关键词黑名单"：**写了"已废"的行照样可以出现这些词**
    （第 ⑨ 步那张「三条不许当红档」表就必须写出来）。缺的是**标记**，不是词。
    """
    out: list[str] = []
    for n, ln in enumerate(text.splitlines(), 1):
        if any(p in ln for p in CONDEMNED) and not any(m in ln for m in CONDEMN_MARKS):
            out.append(f"L{n}: {ln.strip()[:60]}")
    return out


def check_spec_book(text: str) -> Findings:
    f = Findings(SPEC_BOOK)
    steps = STEP_RE.findall(text)
    marks = [m for m, _ in steps]
    seen = [m for m in STEP_MARKS if m in marks]
    if len(steps) != 12 or len(seen) != 12:
        f.red(f"步数 {len(steps)}/12（去重 {len(seen)}）—— 十二步必须各一节 ### ①..⑫")
    dup = [m for m in marks if marks.count(m) > 1]
    if dup:
        f.red(f"步骤号重复：{sorted(set(dup))}")

    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if SUMMARY_RE.match(ln)), -1)
    n_sum = 0
    if start >= 0:
        for ln in lines[start:]:
            if SUMMARY_RE.match(ln):
                n_sum += 1
            else:
                break
    if n_sum != 12:
        f.red(f"速查表第一段 12 行 / 实到 {n_sum} 行")

    blocks = re.split(r"^###\s*[①-⑫]", text, flags=re.M)[1:]
    per_step_missing: list[str] = []
    for mark, block in zip(marks, blocks, strict=False):
        body = block.split("\n## ")[0]
        have = {m for m, _ in FIELD_RE.findall(body)}
        lack = {"规格", "判据", "症状"} - have
        if lack:
            per_step_missing.append(f"{mark} 缺 {''.join(sorted(lack))}")
    if per_step_missing:
        f.red("三列缺失：" + " · ".join(per_step_missing))

    holes, pending, unread, bad_pending = scan_holes(text)
    if bad_pending:
        f.red(f"待回填没指名 P0：{bad_pending[0][:80]}")
    stale = condemned_lines(text)
    if stale:
        f.red("已标废的旧口径没带「已废」标记：" + " · ".join(stale[:3]))
    f.facts.append(
        f"12 步齐备（①..⑫ 各一次）· 速查表 12 行 · "
        f"规格+判据+症状 三列齐全 {12 - len(per_step_missing)}/12 · "
        f"空洞 {len(holes)} 条（待回填 {len(pending)} / 未读 {len(unread)}）· "
        f"已回填【读数·P0-x】{len(FILLED_RE.findall(text))} 处 · "
        f"已废口径守卫 {sum(1 for ln in text.splitlines() if any(p in ln for p in CONDEMNED))} 处"
    )
    return f


# ───────────────────────── ② 烘焙规范 ─────────────────────────


def check_baking(text: str) -> Findings:
    f = Findings(BAKING)
    rules: list[tuple[str, str]] = []
    for ln in text.splitlines():
        m = RULE_RE.match(ln)
        if m:
            rules.append((m.group(1), ln))
    if len(rules) < 20:
        f.red(f"规则条数 {len(rules)} < 20")

    no_tag = [rid for rid, ln in rules if not TAG_RE.search(ln)]
    if no_tag:
        f.red(f"规则缺『依据＝』：{', '.join(no_tag[:6])}")

    kinds = {"读数": 0, "自定": 0, "待回填": 0}
    for _rid, ln in rules:
        m = TAG_RE.search(ln)
        if m:
            kinds[m.group(1)] += 1
    if kinds["读数"] < 8:
        f.red(f"『依据＝读数』只有 {kinds['读数']} 条（要 ≥ 8）")
    if kinds["自定"] < 8:
        f.red(f"『依据＝自定』只有 {kinds['自定']} 条（要 ≥ 8）")

    keywords = {"cage": "cage", "射线": "射线", "采样": "采样", "镜像": "镜像"}
    lack_kw = [nice for nice, kw in keywords.items() if kw.lower() not in text.lower()]
    if lack_kw:
        f.red(f"缺小节关键词：{', '.join(lack_kw)}")

    holes, pending, unread, bad_pending = scan_holes(text)
    if bad_pending:
        f.red(f"待回填没指名 P0：{bad_pending[0][:80]}")
    f.facts.append(
        f"规则 {len(rules)} 条（依据＝读数 {kinds['读数']} / 自定 {kinds['自定']} / "
        f"待回填 {kinds['待回填']}）· 四个小节（cage/射线/采样/镜像）齐备 · "
        f"空洞 {len(holes)} 条（待回填 {len(pending)} / 未读 {len(unread)}）· "
        f"已回填【读数·P0-x】{len(FILLED_RE.findall(text))} 处"
    )
    return f


# ───────────────────────── ③ 逆向版 ─────────────────────────


def check_reverse(text: str) -> Findings:
    f = Findings(REVERSE)
    idx, rows = find_table(text, ("检查项", "用什么量", "不合格怎么办"))
    if idx is None:
        f.red("找不到『检查项 / 用什么量 / 不合格怎么办』的表")
        f.facts.append("环数 0 · 三列齐全 0/0")
        return f
    floor = 12
    if len(rows) < floor:
        f.red(f"环数 {len(rows)} < {floor}")
    empty: list[str] = []
    ji = idx["检查项"]
    for r in rows:
        label = r[ji][:14] if ji < len(r) and r[ji] else "?"
        for c in COLS_REVERSE:
            j = idx[c]
            if j >= len(r) or not r[j] or r[j] in ("-", "—"):
                empty.append(f"「{label}」缺 {c}")
    if empty:
        f.red("三列有空：" + " · ".join(empty[:4]))

    holes, pending, unread, bad_pending = scan_holes(text)
    if bad_pending:
        f.red(f"待回填没指名 P0：{bad_pending[0][:80]}")
    stale = condemned_lines(text)
    if stale:
        if REVERSE in CONDEMNED_DOCS:
            f.red("已标废的旧口径没带「已废」标记：" + " · ".join(stale[:3]))
        else:
            # 守卫没覆盖这份 ⇒ 只出黄（**这本身是个洞**：把它加进 CONDEMNED_DOCS，或说明为什么）
            f.warn.append("已标废的旧口径仍出现在本文件（守卫未覆盖）：" + " · ".join(stale[:2]))
    f.facts.append(
        f"环数 {len(rows)} · 三列齐全 {len(rows) - len(empty)}/{len(rows)} · "
        f"空洞 {len(holes)} 条（待回填 {len(pending)} / 未读 {len(unread)}）· "
        f"已回填【读数·P0-x】{len(FILLED_RE.findall(text))} 处"
    )
    return f


# ───────────────────────── ④ 高模挑选清单 ─────────────────────────


def check_checklist(text: str) -> Findings:
    f = Findings(CHECKLIST)
    idx, rows = find_table(text, ("属性", "好改", "难改", "判据"))
    if idx is None:
        f.red("找不到『属性 / 好改 / 难改 / 判据』的表")
        f.facts.append("属性 0 条")
        return f
    if len(rows) < 8:
        f.red(f"属性 {len(rows)} 条 < 8")
    empty: list[str] = []
    for r in rows:
        for c in ("属性", "好改", "难改", "判据"):
            j = idx[c]
            if j >= len(r) or not r[j] or r[j] in ("-", "—"):
                empty.append(f"第 {rows.index(r) + 1} 行缺 {c}")
    if empty:
        f.red("四列有空：" + " · ".join(empty[:4]))

    lic = [r for r in rows if idx["属性"] < len(r) and "许可" in r[idx["属性"]]]
    if not lic:
        f.red("缺『许可』那一行")
    elif not any("一票否决" in cell for cell in lic[0]):
        f.red("许可行没写『一票否决』")

    holes, pending, unread, bad_pending = scan_holes(text)
    if bad_pending:
        f.red(f"待回填没指名 P0：{bad_pending[0][:80]}")
    f.facts.append(
        f"属性 {len(rows)} 条 · 好改/难改/判据 齐全 {len(rows) - len(empty)}/{len(rows)} · "
        f"许可行 {'含' if lic and any('一票否决' in c for c in lic[0]) else '无'}一票否决 · "
        f"空洞 {len(holes)} 条（待回填 {len(pending)} / 未读 {len(unread)}）· "
        f"已回填【读数·P0-x】{len(FILLED_RE.findall(text))} 处"
    )
    return f


# ───────────────────────── ⑤ 待真机验证清单 ─────────────────────────


def entries_of(text: str) -> list[tuple[str, str, str]]:
    """把《待真机验证清单》切成 (编号, 标题, 正文)。

    正文在**下一个 ## / ### 标题**处收尾 —— ⛔ 不许让它吃到下一节的表格，
    否则"这一条缺列"会被下一节的内容补上（假绿）。
    """
    ms = list(ENTRY_RE.finditer(text))
    bounds = [m.start() for m in SECTION_RE.finditer(text)]
    out: list[tuple[str, str, str]] = []
    for m in ms:
        end = next((b for b in bounds if b > m.end()), len(text))
        out.append((m.group(1), m.group(2).strip(), text[m.end() : end]))
    return out


def check_realtime(text: str) -> Findings:
    """《待真机验证清单》—— **用户醒来的第一张纸**的机械断言。

    四条（task-7 立；每条都有负控，见 NEG_CONTROLS 14–18）：

      * **编号唯一** —— 同一次真机动作不许登记成两个入口（P0-a/P1-a 就是这么重出来的）；
      * **五件齐** —— 怎么复算 / 期望读数 / 依据 / 风险 / 为什么今晚做不了
        （"症状"写在标题里，见下一条）；
      * **标题是症状不是疑问句** —— `是不是反了？` 这种写法用户读完不知道该看什么；
      * **已标废的旧口径必须带标记**（复用 CONDEMNED / CONDEMN_MARKS）。

    ⚠ 这里**不套**"待回填必须指名 P0-n"那条（scan_holes 的 bad 分支）：本文件用的是
    **入口编号**（P0-a / P5），不是 P0-<n> 那种"哪个 P0 线来填"的编号 ⇒ 套上去会
    逼着人写出不成立的编号。空洞数照样印在读数里（现在 2 条：§P0 余下条目 / §P5 动画）。
    """
    f = Findings(REALTIME)
    entries = entries_of(text)
    if len(entries) < 12:
        f.red(f"入口 {len(entries)} 条 < 12 —— 这张纸是用户醒来的第一张纸，不许只剩几条")
    ids = [eid for eid, _t, _b in entries]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        f.red(f"编号重复（同一次真机动作登记成两个入口）：{dup}")
    lack_cols: list[str] = []
    questions: list[str] = []
    for eid, title, body in entries:
        lack = [c for c in ENTRY_COLS if f"**{c}**" not in body]
        if lack:
            lack_cols.append(f"{eid} 缺 {''.join(lack)}")
        if QUESTION_TAIL_RE.search(title):
            questions.append(eid)
    if lack_cols:
        f.red("条目缺列：" + " · ".join(lack_cols[:6]))
    if questions:
        f.red(f"标题是疑问句不是症状（{len(questions)} 条）：" + " · ".join(questions[:8]))
    stale = condemned_lines(text)
    if stale:
        f.red("已标废的旧口径没带「已废」标记：" + " · ".join(stale[:3]))
    holes, pending, unread, _bad_pending = scan_holes(text)
    n_guard = sum(1 for ln in text.splitlines() if any(p in ln for p in CONDEMNED))
    f.facts.append(
        f"入口 {len(entries)} 条（编号唯一 · 五件齐 · 标题全是症状）· "
        f"已废口径守卫 {n_guard} 处 · "
        f"空洞 {len(holes)} 条（待回填 {len(pending)} / 未读 {len(unread)}）"
    )
    return f


# ───────────────────────── ⑥ 指针 ─────────────────────────


#: 行里带这些字 = 那条路径是**历史注记**（那份文件已经下线了），⛔ 不算悬空指针。
#: 依据（2026-10-15「插件重做」）：`docs/闸门与正负控表.md` · `tools/gates.py` ·
#: `blender_addon/criteria.py` 三件一起下线，而三份文档**故意**留着它们的名字，
#: 写成「（2026-10-15 已随插件重做下线）」—— 那不是悬空，是**记着它曾经在**。
#: ⚠ 豁免只认**行内明写**；去掉这半句，同一条路径立刻回来报红（用例里有这条反控）。
RETIRED_MARKS: tuple[str, ...] = ("已下线", "已删", "已退役", "已随插件重做下线")


def is_retired_line(line: str) -> bool:
    """这一行是不是在**交代一条已下线的路径**（是 ⇒ 不查它的存在性）。"""
    return any(mark in line for mark in RETIRED_MARKS)


def resolve(token: str, repo_root: Path) -> Path | None:
    t = token.replace("\\", "/").strip()
    if re.match(r"^[A-Za-z]:/", t):
        return Path(t)
    first = t.split("/")[0]
    if first in ROOT_DIRS:
        return repo_root / t
    if first in PKG_DIRS:
        return repo_root / "src" / "ba_mod_maker" / t
    return None


def check_pointers(texts: dict[str, str], repo_root: Path) -> Findings:
    f = Findings("指针")
    misses: list[str] = []
    seen: set[str] = set()
    for name, text in texts.items():
        for ln in text.splitlines():
            if is_retired_line(ln):
                continue        # 历史注记：那份文件已经下线，⛔ 不算悬空
            for raw in PATH_RE.findall(ln):
                token = raw.split("::")[0].split()[0]
                p = resolve(token, repo_root)
                if p is None:
                    continue
                key = str(p)
                if key in seen:
                    continue
                seen.add(key)
                if "…" in token or "<" in token:
                    continue
                if not p.exists():
                    misses.append(f"{name}: {token}")
    if misses:
        f.red(f"指针缺 {len(misses)} 条：" + " · ".join(misses[:6]))
    f.facts.append(f"五份文档引用的仓内路径 {len(seen)} 条，缺 {len(misses)}")
    return f


# ───────────────────────── ⑦ 空洞 ─────────────────────────


def is_filled_row(line: str) -> bool:
    """表格行是不是**已回填**（两种标记见 FILLED_RE / FILLED_STRIKE_RE）。

    ⛔ 这里只加"能指到读数的标记"，**不是**"看着像填过就算"：
    必须带 `【读数·P0-<n>】` 戳，或者是 `~~N~~` 划掉 **＋** 明写「已回填」。
    """
    if FILLED_RE.search(line):
        return True
    return bool(FILLED_STRIKE_RE.search(line) and FILLED_MARK_RE.search(line))


def is_table_header(lines: list[str], i: int) -> bool:
    """第 i 行是不是**表头**（判据：后面第一个非空行是分隔行 `|---|---|`）。

    台账段里可以有多张表（例：`## 2 · 空洞台账` 下先一张"已回填"、再一张"明确未读"），
    ⛔ 每张表的表头都不是空洞 —— 只跳过第一张会让第二张的表头混进读数。
    """
    for ln in lines[i + 1:]:
        s = ln.strip()
        if not s:
            continue
        if not s.startswith("|"):
            return False
        cells = split_row(s)
        return bool(cells) and all(set(c) <= set("-: ") for c in cells if c)
    return False


def is_legend_row(line: str) -> bool:
    """是不是 §0 那种**图例行**（整格字面 = 模板 `待回填 P0-x`）。

    烘焙 L14 `| **待回填 P0-x** | 那条读数还在跑；⛔ 不许用「一般游戏都…」填 |`
    定义的是"这个记号什么意思"，**不是一条断言** —— 修前它被算成"待回填 1"。
    """
    s = line.strip()
    if not s.startswith("|"):
        return False
    return any(LEGEND_CELL_RE.match(c.replace("*", "").strip()) for c in split_row(s))


def scan_holes(text: str):
    """数空洞。

    两类都算（**按行号去重**，一行只算一条）：
      ① 行内含 待回填 / 未读 关键词的行；
      ② **标题带「空洞 / 未读 / 待回填」之后那张台账表的每一行数据**
         （台账表不会把关键词写进每一格，所以表体单独算）。

    四条口径：
      * 标题（# 开头）自带 待回填 时**不**要求指名 P0 —— 它是结构，不是断言；
      * 台账表的**表头行**不算（跳过一次）；
      * **已回填的行不算**（两种标记，见 `is_filled_row`）；
      * §0 **图例行**不算（定义记号的模板行，见 `is_legend_row`）。

    台账模式**跨标题复位**（task-7 修）：台账标题打开一段；此后遇到**同级或更浅**、
    又不带关键词的标题 ⇒ 台账**结束**（修前是 sticky，隔一个无关标题的表格被整张吃掉：
    `_work/verify/lead/scan_holes_probe.py` 案例 C 报 3 条，真值 1 条）。更深的小标题
    （例 `### 2.1` 在 `## 2 · 空洞台账` 之下）**留在台账里**。
    """
    lines = text.splitlines()
    hits: set[int] = set()
    pending: set[int] = set()
    unread: set[int] = set()
    bad: list[str] = []
    mode = False          # 是否正在「空洞台账」这一段里
    mode_level = 0        # 打开这段台账的标题层级
    first_row = True      # 下一个表行是表头（跳过）
    for n, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            if any(k in s for k in ("空洞", "未读", "待回填")):
                mode = True
                mode_level = level
                first_row = True
            elif mode and level <= mode_level:
                mode = False          # 同级/更浅的无关标题 ⇒ 离开台账（不再 sticky）
            continue
        if not s:
            continue
        if is_legend_row(s):
            continue                  # 图例行：定义记号，不是断言
        if mode and s.startswith("|"):
            cells = split_row(s)
            if all(set(c) <= set("-: ") for c in cells if c):
                continue
            if first_row or is_table_header(lines, n):
                first_row = False
                continue          # 表头行（每张表各一次）
            if is_filled_row(s):
                continue          # 已回填：不是空洞
            hits.add(n)
            for c in cells:
                if PENDING_RE.search(c):
                    pending.add(n)
                if "未读" in c:
                    unread.add(n)
            continue
        mode = False
        has_pending = bool(PENDING_RE.search(s))
        has_unread = "未读" in s
        if has_pending:
            hits.add(n)
            pending.add(n)
            m = PENDING_RE.search(s)
            if not P0_RE.search(s[m.end(): m.end() + 40]):
                bad.append(s)
        if has_unread:
            hits.add(n)
            unread.add(n)
    pick = lambda idxs: [lines[i] for i in sorted(idxs)]  # noqa: E731
    return pick(hits), pick(pending), pick(unread), bad


CHECKS = {
    SPEC_BOOK: check_spec_book,
    BAKING: check_baking,
    REVERSE: check_reverse,
    CHECKLIST: check_checklist,
    REALTIME: check_realtime,
}


def run_all(doc_dir: Path, repo_root: Path, quiet: bool = False) -> tuple[list[Findings], bool]:
    texts: dict[str, str] = {}
    results: list[Findings] = []
    missing = False
    for name in DOC_NAMES:
        t = read_doc(doc_dir, name)
        if t is None:
            results.append(Findings(name))
            results[-1].red(f"文档不在：{doc_dir / name}")
            missing = True
            continue
        texts[name] = t
        r = CHECKS[name](t)
        bare = density_lines(t)
        if bare:
            r.red("密度数没带样本/区间：" + " · ".join(bare[:3]))
        elif DENSITY_RE.search(t):
            r.facts.append(f"密度行全部带样本（{len(DENSITY_RE.findall(t))} 处）")
        results.append(r)
    results.append(check_pointers(texts, repo_root))
    if not quiet:
        for r in results:
            mark = "✔" if r.ok else "✘"
            head = f"{mark} {r.name:<22}"
            print(head + "  " + " · ".join(r.facts))
            for why in r.bad:
                print(f"    ⛔ {why}")
            for w in r.warn:
                print(f"    ⚠ {w}")
    return results, missing


# ───────────────────────── 负控 ─────────────────────────


def mutate_drop_symptom(text: str) -> str:
    lines = text.splitlines()
    out, done = [], False
    for ln in lines:
        if not done and ln.startswith("- **症状**"):
            done = True
            continue
        out.append(ln)
    return "\n".join(out) + "\n"


def mutate_drop_step(text: str) -> str:
    parts = re.split(r"(?m)^(?=###\s*[①-⑫])", text)
    kept = [p for p in parts if not p.startswith("### ⑨")]
    return "".join(kept)


def mutate_drop_p0(text: str) -> str:
    # task-26 之后《零件规格书》已无 待回填 ⇒ 锚点换到《烘焙规范》那条图例上
    # ⛔ 锚点会随回填腐坏（这已是第三次）—— selftest 里那条「变异必须真的改动了字」就是防它。
    return text.replace("待回填 P0-x", "待回填", 1)


def mutate_drop_tag(text: str) -> str:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if RULE_RE.match(ln) and TAG_RE.search(ln):
            lines[i] = TAG_RE.sub("依据＝无", ln, count=1)
            break
    return "\n".join(lines) + "\n"


def mutate_no_reading(text: str) -> str:
    return text.replace("依据＝读数", "依据＝自定")


def mutate_drop_rows(text: str) -> str:
    idx, rows = find_table(text, ("检查项", "用什么量", "不合格怎么办"))
    if idx is None:
        return text
    keep = rows[: len(rows) - 5]
    lines = text.splitlines()
    out, emitted = [], 0
    in_table = False
    for ln in lines:
        if ln.strip().startswith("|") and idx is not None:
            cells = split_row(ln)
            if all(any(c in cell for cell in cells) for c in COLS_REVERSE):
                in_table = True
                out.append(ln)
                continue
            if in_table:
                if all(set(c) <= set("-: ") for c in cells if c):
                    out.append(ln)
                    continue
                if emitted < len(keep):
                    out.append(ln)
                    emitted += 1
                    continue
                continue
        out.append(ln)
    return "\n".join(out) + "\n"


def mutate_empty_cell(text: str) -> str:
    idx, rows = find_table(text, ("检查项", "用什么量", "不合格怎么办"))
    if idx is None or not rows:
        return text
    j = idx["不合格怎么办"]
    target = rows[2]
    if j >= len(target):
        return text
    old = target[j]
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("|") and old and old in ln:
            cells = split_row(ln)
            if len(cells) > j and cells[j] == old:
                lines[i] = ln.replace("| " + old + " |", "|  |", 1)
                break
    return "\n".join(lines) + "\n"


def mutate_drop_license(text: str) -> str:
    keep = [ln for ln in text.splitlines() if not ln.strip().startswith("| **8** | **许可**")]
    return "\n".join(keep) + "\n"


def mutate_no_veto(text: str) -> str:
    return text.replace("一票否决", "可以考虑")


def mutate_reinject_old_lod(text: str) -> str:
    """把**已标废**的 LOD 旧口径塞回去（P3-5 推翻的那条）—— 守卫必须红。"""
    line = "- **判据**：减面后 **bbox 与 LOD0 逐位相同**，顶点数落在 1/8 ~ 1/10。"
    return text + "\n" + line + "\n"


def mutate_reinject_old_lod_rev(text: str) -> str:
    """把已标废的 LOD 旧口径塞进《逆向版》—— 证明 task-22 扩展的守卫**真的覆盖到了**它。"""
    line = "| **13** | 手做 LOD1/2 时 **bbox 逐位相同**、顶点落在 1/8 ~ 1/10。 |"
    return text + "\n" + line + "\n"


def mutate_bare_density(text: str) -> str:
    """塞一句"单一密度数"（没有样本、没有区间）—— task-25 的守卫必须红。"""
    line = "- **4** | 目标密度 **≈ 150 texel/m**，直接照这个做。"
    return text + "\n" + line + "\n"


def mutate_fake_pointer(text: str) -> str:
    return text + "\n\n<!-- 负控 --> 见 `_work/这是负控-假路径.py` 。\n".replace("`", "`")


def mutate_realtime_drop_col(text: str) -> str:
    """抽掉《待真机验证清单》**第一条入口**的一列（把 `**期望读数**` 这个记号改掉）。

    ⚠ 两件事写在注释里，因为都真踩过：
      1. 改的是**记号**不是内容 —— 机械断言只能保证"这一列在不在"，保证不了读数真假；
      2. ⛔ 不许直接 `text.replace(..., 1)` —— 文件**开头那段格式模板**里也有同样一行，
         朴素的"第一次出现"会改到模板上，于是这条负控**假装红不了**（第一次就是这么失败的）。
    """
    m = ENTRY_RE.search(text)
    if m is None:
        return text
    head, tail = text[: m.end()], text[m.end():]
    return head + tail.replace("- **期望读数**：", "- **期望读数（负控已删）**：", 1)


def mutate_realtime_question(text: str) -> str:
    """把第一条入口的标题改成**疑问句**（末尾加 ？）—— 必须报「标题是疑问句」。

    ⛔ 不锚死在某一句话上（锚点会随文案改动腐坏），只动正则抓到的那一段。
    """
    return ENTRY_RE.sub(lambda m: f"### {m.group(1)} · {m.group(2)}？", text, count=1)


def mutate_realtime_dup_id(text: str) -> str:
    """把第二条入口的编号改成与第一条**相同** —— 必须报「编号重复」。"""
    ms = list(ENTRY_RE.finditer(text))
    if len(ms) < 2:
        return text
    first, second = ms[0], ms[1]
    return text[: second.start(1)] + first.group(1) + text[second.end(1):]


def mutate_reinject_old_lod_realtime(text: str) -> str:
    """把已标废的 LOD 旧口径塞进《待真机验证清单》—— 证明守卫**真的覆盖到了**它。"""
    line = "- **怎么复算**：减面后 **bbox 与 LOD0 逐位相同**、顶点落在 1/8 ~ 1/10。"
    return text + "\n" + line + "\n"


def mutate_fake_pointer_realtime(text: str) -> str:
    """给《待真机验证清单》塞一条假路径 —— 它的指针也必须被查。"""
    return text + "\n\n<!-- 负控 --> 见 `_work/这是负控-清单假路径.py` 。\n"


NEG_CONTROLS = [
    ("挖掉《零件规格书》第 ⑤ 步的「症状」列", SPEC_BOOK, mutate_drop_symptom, "三列缺失"),
    ("删掉《零件规格书》第 ⑨ 步整节", SPEC_BOOK, mutate_drop_step, "步数"),
    ("把图例的 待回填 P0-x 挖成 待回填", BAKING, mutate_drop_p0, "待回填没指名 P0"),
    ("抽掉《烘焙规范》一条规则的 依据＝", BAKING, mutate_drop_tag, "规则缺"),
    ("把《烘焙规范》所有 依据＝读数 改成 依据＝自定", BAKING, mutate_no_reading, "读数"),
    ("《逆向版》删掉 5 行表行", REVERSE, mutate_drop_rows, "环数"),
    ("《逆向版》清空某行的「不合格怎么办」列", REVERSE, mutate_empty_cell, "三列有空"),
    ("《高模挑选清单》删掉「许可」那行", CHECKLIST, mutate_drop_license, "缺"),
    ("把「一票否决」改成「可以考虑」", CHECKLIST, mutate_no_veto, "一票否决"),
    ("注入一条假路径", SPEC_BOOK, mutate_fake_pointer, "指针缺"),
    ("把已标废的 LOD 旧口径塞回规格书", SPEC_BOOK, mutate_reinject_old_lod, "已标废的旧口径"),
    ("把旧口径塞回《逆向版》（验扩展）", REVERSE, mutate_reinject_old_lod_rev, "已标废的旧口径"),
    ("塞一句没带样本的密度数", CHECKLIST, mutate_bare_density, "密度数没带样本"),
    ("《待真机验证清单》抽掉一条的「期望读数」列", REALTIME, mutate_realtime_drop_col, "条目缺列"),
    ("把《待真机验证清单》一条标题改成疑问句", REALTIME, mutate_realtime_question, "标题是疑问句"),
    ("把《待真机验证清单》两条改成同一编号", REALTIME, mutate_realtime_dup_id, "编号重复"),
    (
        "把已标废的 LOD 旧口径塞进《待真机验证清单》",
        REALTIME,
        mutate_reinject_old_lod_realtime,
        "已标废的旧口径",
    ),
    ("给《待真机验证清单》塞一条假路径", REALTIME, mutate_fake_pointer_realtime, "指针缺"),
]

#: `scan_holes` 的**逐例**断言（逻辑只有一份：pytest 也跑这张表，见 tests/test_spec_audit.py）。
#: 前 5 例是台账模式 —— 例 C 就是 task-7 修的那条"跨标题必须复位"（修前 3 条，真值 1 条）；
#: 中间 4 条是**负控**（真空洞必须仍被算出来，⛔ 尺子不许"什么都认"）；
#: 最后 3 条是**正控**（task-7 修掉的那 5 条假阳性）。
SCAN_CASES: list[tuple[str, str, int]] = [
    ("A 普通章节 + 普通表格", "## 普通章节\n| a | b |\n|---|---|\n| r1 | v1 |\n", 0),
    ("B 空洞标题 + 表格", "## 9. 仍然空洞\n| a | b |\n|---|---|\n| r1 | v1 |\n", 1),
    (
        "C 台账 + 无关标题 + 另一张表（跨标题必须复位）",
        "## 9. 仍然空洞\n| a | b |\n|---|---|\n| r1 | v1 |\n"
        "## 10. 无关章节\n| c | d |\n|---|---|\n| r2 | v2 |\n",
        1,
    ),
    (
        "D 台账 + 普通正文 + 另一张表",
        "## 9. 仍然空洞\n| a | b |\n|---|---|\n| r1 | v1 |\n普通正文。\n"
        "| c | d |\n|---|---|\n| r2 | v2 |\n",
        1,
    ),
    (
        "E 台账 + 更深小标题 + 另一张表（仍留在台账里）",
        "## 9. 空洞\n| a | b |\n|---|---|\n| r1 | v1 |\n### 9.1 子节\n"
        "| c | d |\n|---|---|\n| r2 | v2 |\n",
        2,
    ),
    (
        "负控 真空洞行（未读·台账里）",
        "## 9. 空洞\n| # | 空洞 | 堵在哪 |\n|---|---|---|\n| 5 | 未读 | 堵在哪 |\n",
        1,
    ),
    ("负控 真空洞行（没有台账标题，靠关键词）", "| 5 | 未读 | 堵在哪 |\n", 1),
    ("负控 待回填行", "| 5 | 待回填 P0-4 | 还在跑 |\n", 1),
    (
        "负控 划掉但没写「已回填」",
        "## 9. 空洞\n| # | 空洞 | 状态 |\n|---|---|---|\n| ~~3~~ | ~~Mask 语义~~ | 还在跑 |\n",
        1,
    ),
    (
        "正控 §0 图例行",
        "## 0 · 口径\n| 标记 | 意思 |\n|---|---|\n| **待回填 P0-x** | 那条读数还在跑 |\n",
        0,
    ),
    (
        "正控 双编号标记",
        "## 9. 空洞\n| # | 空洞 | 状态 |\n|---|---|---|\n| 8 | 骨名 | 填了【读数·P0-6 / P0-8】 |\n",
        0,
    ),
    (
        "正控 划掉式回填",
        "## 9. 空洞\n| # | 空洞 | 状态 |\n|---|---|---|\n"
        "| ~~3~~ | ~~Mask 语义~~ | ✅ **已回填**：R→o2.x |\n",
        0,
    ),
]


def selftest() -> int:
    print(
        f"spec_audit --selftest（正控 + {len(NEG_CONTROLS)} 条负控；"
        "都在临时目录里改副本，⛔ 不碰真文档）"
    )
    print("-" * 78)
    texts: dict[str, str] = {}
    missing = []
    for name in DOC_NAMES:
        t = read_doc(DOC_DIR, name)
        if t is None:
            missing.append(name)
        else:
            texts[name] = t
    if missing:
        print(f"✘ 原件读不到：{missing} —— 先把五份文档写出来再跑自检")
        return 2

    with tempfile.TemporaryDirectory(prefix="spec_audit_selftest_") as tmp:
        tmpdir = Path(tmp)
        for name, t in texts.items():
            (tmpdir / name).write_text(t, encoding="utf-8")
        results, _ = run_all(tmpdir, ROOT, quiet=True)
        pos_ok = all(r.ok for r in results)
        print(f"正控：五份原件副本 → {'全绿 ✔' if pos_ok else '有红 ✘'}")
        if not pos_ok:
            for r in results:
                for why in r.bad:
                    print(f"    ⛔ {r.name}: {why}")
            print("结论：正控不过 —— 尺子自己就是红的，先修审计器/文档")
            return 1

        hit = 0
        for i, (title, doc, mutate, expect) in enumerate(NEG_CONTROLS, 1):
            work = tmpdir / "_neg"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            for name, t in texts.items():
                (work / name).write_text(t, encoding="utf-8")
            mutated = mutate(texts[doc])
            if mutated == texts[doc]:
                # ⚠ 锚点烂掉 = 这条负控变成"空操作"，它会**假装绿**
                # （回填把那句话改掉了就会这样；task-15/task-22 各踩过一次）。
                print(f"  负控 {i:>2}/{len(NEG_CONTROLS)} {title:<40} 空操作 ✘（锚点已不存在）")
                continue
            (work / doc).write_text(mutated, encoding="utf-8")
            res, _ = run_all(work, ROOT, quiet=True)
            reds = [why for r in res for why in r.bad]
            got = any(expect in why for why in reds)
            print(f"  负控 {i:>2}/{len(NEG_CONTROLS)} {title:<44} {'命中 ✔' if got else '没红 ✘'}")
            if not got:
                print(f"      期望含『{expect}』，实到：{reds[:3]}")
            else:
                hit += 1
        # ── 空洞尺子的逐例断言（正/负控都在 SCAN_CASES 里） ──
        sc_hit = 0
        for i, (title, sample, expect) in enumerate(SCAN_CASES, 1):
            holes, _p, _u, _b = scan_holes(sample)
            got = len(holes) == expect
            sc_hit += got
            print(f"  尺子 {i:>2}/{len(SCAN_CASES)} {title:<46} {'命中 ✔' if got else '没红 ✘'}")
            if not got:
                shown = [h.strip() for h in holes]
                print(f"      期望 {expect} 条空洞，实到 {len(holes)} 条：{shown}")
        print("-" * 78)
        ok = hit == len(NEG_CONTROLS) and sc_hit == len(SCAN_CASES)
        print(
            f"负控 {hit}/{len(NEG_CONTROLS)} 命中 · 空洞尺子 {sc_hit}/{len(SCAN_CASES)} 命中 "
            f"/ 正控 5 份全绿"
        )
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="五份 modder 文档的机械审计（12 步 / 三列 / 依据 / 待真机清单 / 指针 / 空洞）"
    )
    ap.add_argument(
        "--selftest", action="store_true", help="跑正控 + 10 条负控（临时目录，不碰真文档）"
    )
    ap.add_argument("--doc-dir", default=str(DOC_DIR), help="文档目录（默认 <仓>/docs）")
    ap.add_argument("--repo-root", default=str(ROOT), help="指针解析的仓根（默认本脚本的上一级）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    doc_dir = Path(args.doc_dir)
    print(f"spec_audit · 五份文档审计（doc_dir = {doc_dir}）")
    print("-" * 78)
    results, missing = run_all(doc_dir, Path(args.repo_root))
    print("-" * 78)
    reds = [why for r in results for why in r.bad]
    if missing:
        print("结论：用不了（五份文档里有读不到的）")
        return 2
    if reds:
        print(f"结论：有红 {len(reds)} 条")
        return 1
    print("结论：全绿（五份文档 ＋ 指针）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
