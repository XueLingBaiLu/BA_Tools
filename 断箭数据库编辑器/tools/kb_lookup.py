r"""tools/kb_lookup.py —— 知识库的**唯一检索入口**：拿你**原话**去查，返回"读哪一篇、哪一段"。

由来（2026-10-11，用户点名要的）：

    用户：「Agent 根本记不住知识库里的东西，遇到知识库本来就有的东西，也不会去翻，
           而是纯靠猜，给我的工作带来了异常巨大的阻力。」

实测（正控已做，读数在下）——旧入口 tools/three_doors.py 的门 0 是**整句子串匹配**：

    问「怎么造一个炮塔包」 -> 0 命中，并打印「这只说明**这份知识库没写过**」
    而 kb/24-造一个炮塔包（B2全链）.md 的**标题就是这句话**。

6 条自然语言问句 **6/6 全 0 命中**；同一批词拆成单词（炮塔 / 场景编辑器 / 封号 …）则全部命中
⇒ 尺子能红，失败是真的。于是链路变成：

    用户说人话 -> Agent 拿人话去查 -> 0 命中 -> 工具盖章"没写过"
    -> Agent 合理地放弃检索 -> 靠参数记忆作答 = 猜。

**本文件修的就是这一条。四条硬口径：**

1. **输入是原话**，不让 Agent 先"转写成关键词" —— 那一转写本身就是猜的入口；
2. ⛔ **0 命中不许写"知识库没写过"**。词组没出现 != 知识没有。唯一合法的
   "大概没写过"证据是：**词本身在库里 0 次出现**（词级，不是句级）；
3. **强命中只认"有辨识度的词"** —— 光命中「游戏」这种到处都是的词不算数，
   否则「这个游戏支持手柄吗」也会报"知识库写过"（这条是实测出来的，见负控）；
4. **词表鸿沟要自陈** —— 用户说「外观」，本库写的是「皮肤/涂装」（kb/21 §六）。
   纯词法检索**跨不过这道沟**，所以强命中为 0 时必须把**入口清单**摆出来让人点，
   而不是回一句"没找到"。这不是妥协，这是把判断交还给人。

判据一行：

    .venv\Scripts\python.exe tools\kb_lookup.py 怎么造一个炮塔包
    -> 强命中含 kb/24-造一个炮塔包（B2全链）.md（旧入口：0 命中）

    .venv\Scripts\python.exe tools\kb_lookup.py --selftest
    -> 正控 5 + 反恒真 1 + 负控 1 = 7 条全绿；改回整句匹配必红
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _doc_root(name: str) -> Path:
    """语料根解析：**2026-11-24 用户要求把 `docs/` 与 `kb/` 搬出仓**（仓里只留代码），
    搬到了 `<工作区>/_非代码/<仓名>/<name>`。这里按 env → 仓内 → 搬家位置 依次找，
    ⛔ 不再写死仓内路径（写死会让搬完之后检索静默 0 命中）。"""
    env = os.environ.get("BA_DOCS_ROOT")
    if env:
        return Path(env) / name
    local = REPO_ROOT / name
    if local.is_dir():
        return local
    moved = REPO_ROOT.parent / "_非代码" / REPO_ROOT.name / name
    return moved if moved.is_dir() else local


KB = _doc_root("kb")
DOCS = _doc_root("docs")
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")
AGENTS = REPO_ROOT / "AGENTS.md"
STATUS = REPO_ROOT.parent / "STATUS.md"

#: 主语料（长期件）与证据语料（_work / STATUS 是滚动或一次性件，降权）。
TIER_MAIN = 1.0
TIER_EVIDENCE = 0.45

#: 单文件上限：超过就不读（本库最大也就几十 KB，这条只为防意外）。
MAX_BYTES = 1_500_000

#: BM25 的两个标准常数（饱和 + 长度归一）。
#: ⛔ **长度归一不许删**：少了它，STATUS.md 这种滚动大文件会因为"什么都提过一句"
#: 而稳居第 1 —— 2026-10-11 实测，它把 kb/24 挤到第 2，正控当场变红。
BM25_K1 = 1.2
BM25_B = 0.75

#: 「你把**原话**又问了一遍，而这一篇**逐字记着**它」的加成。
#: ⛔ 别删：长度归一（BM25_B）会把 STATUS.md 这类滚动大文件压下去 ——
#: 实测 40 条用户原话里有 3 条**逐字记着却进不了 top3**，全是这个原因。
EXACT_QUOTE_BOOST = 3.0

#: 停用词：只收**纯功能词**。实词一律留给 IDF 去压（别在这里手删实词）。
STOPWORDS = frozenset(
    [
        "的", "是", "了", "在", "和", "与", "或", "吗", "呢", "吧", "啊",
        "我", "你", "他", "它", "这", "那", "有", "没", "不", "要", "会",
        "能", "就", "都", "也", "很", "个", "把", "被", "为", "以", "及",
        "而", "但", "又", "从", "到", "对", "于", "上", "下", "里", "中",
        "外", "之", "其", "该", "此", "等", "让", "使", "得", "着", "过",
        "怎么", "什么", "哪个", "哪些", "如何", "可以", "一个", "一下",
        "我们", "你们", "他们", "应该", "需要", "时候", "东西", "请问",
        "帮我", "这个", "那个", "为什么", "是否", "没有", "就是", "还是",
        "或者", "然后", "因为", "所以", "如果", "那么", "这样", "那样",
    ]
)

#: 中文**首字/尾字**若是这些，该 n-gram 是**跨词边界的碎块**（例：「个游」「柄吗」「怎么造」）。
#: ⛔ 只收**不会开启/收尾一个实词**的字。像 外/上/中/为/对/使/等/及/能/要/不/可 都**故意不在**里面
#: （「外观」「对抗」「使用」「等级」「能力」都是实词）—— 这是踩过才知道的取舍。
STOP_CHARS = frozenset("的了是吗呢吧啊个把之其该此什么怎哪谁我你他它这那就都也很请帮")

#: 中文连续段 + ASCII 标识符段。
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z_][A-Za-z0-9_.:]*")
_ASCII_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

#: ★ 词表鸿沟的桥：**用户自己的话**。
#: 本仓 334 行 / 111 篇里写着「用户原话：「…」」这类记录 —— 那是**用户词汇**与
#: 「哪一篇回答过它」的唯一现成对应关系，以前从没被索引过。
#: 判据（2026-10-11 实测）：你说「外观」，库里写「皮肤/涂装」，
#: 而「外观」与「皮肤/涂装」在全库**只共现 1 行** ⇒ 共现扩展没用，只能靠引语。
_QUOTE_MARK_RE = re.compile(r"用户原话|原话|用户说|用户问|用户：|触发：|反馈")
_QUOTED_RE = re.compile(r"[「“]([^「」“”\n]{4,120})[」”]")
#: 问题形状的小标题（「怎么解一条卡片行」这种）—— 也是"这一篇回答过什么"。
_QUESTION_RE = re.compile(r"[?？]|怎么|如何|为什么|哪些|哪个|什么|多少|能不能|要不要|是否|有没有")


# --------------------------------------------------------------------------- #
# 分词
# --------------------------------------------------------------------------- #


def _cjk_terms(run: str) -> list[str]:
    """一个中文连续段 -> 检索词（2-gram 打底，只留**两块 2-gram 都在**的 3-gram）。

    两层过滤，都是"碎块"过滤器：

    1. **边界字**：首字或尾字是 STOP_CHARS 的，丢（这就是「个游」「柄吗」的来源）；
    2. **3-gram 继承**：一个 3-gram 至少要有**一个 2-gram 子块活着**才留。
       ⛔ 判据是「至少一个」不是「两个都」—— 实测「怎么造一个炮塔包」里
       `造一个` 会被"两个都"误杀（`一个` 是停用词），而它是货真价实的说法。
       反向也成立：「一个炮塔」生出的 `一个炮` 两个子块都死，被丢掉。
    """
    if len(run) < 2:
        return []
    pairs = [run[i : i + 2] for i in range(len(run) - 1)]
    kept_pairs: list[str] = []
    for pair in pairs:
        if pair in STOPWORDS or pair in kept_pairs:
            continue
        if pair[0] in STOP_CHARS or pair[-1] in STOP_CHARS:
            continue
        kept_pairs.append(pair)
    alive = set(kept_pairs)

    out: list[str] = []
    safe_ends = run[0] not in STOP_CHARS and run[-1] not in STOP_CHARS
    if len(run) <= 4 and run not in STOPWORDS and safe_ends:
        out.append(run)
    out.extend(kept_pairs)
    for i in range(len(run) - 2):
        triple = run[i : i + 3]
        if triple in STOPWORDS or triple in out:
            continue
        #: ⛔ 3-gram **也要过边界检查**。漏了这一步，「这个游戏支持…」会切出
        #: `个游戏`（首字 个 是停用字）—— 它 df=17，看着"有辨识度"，
        #: 而且和 `游戏` **重复计数同一段文字**，于是把无关帖顶成强命中。
        #: 2026-10-11 实测：加了 `--explain` 才看出来的（"凭什么强"必须能打印出来）。
        if triple[0] in STOP_CHARS or triple[-1] in STOP_CHARS:
            continue
        if triple[:2] in alive or triple[1:] in alive:
            out.append(triple)
    return out


def terms_of(text: str) -> list[str]:
    """把任意说法切成检索词：中文滑窗 2/3-gram + ASCII 标识符（含子词）。"""
    out: list[str] = []
    seen: set[str] = set()

    def push(cand: str) -> None:
        if len(cand) < 2 or cand in STOPWORDS or cand in seen:
            return
        seen.add(cand)
        out.append(cand)

    for run in _TOKEN_RE.findall(text):
        if run[0].isascii():
            low = run.lower()
            push(low)
            for part in _ASCII_SPLIT_RE.split(low):
                push(part)
        else:
            for cand in _cjk_terms(run):
                push(cand)
    return out


# --------------------------------------------------------------------------- #
# 语料
# --------------------------------------------------------------------------- #


def _iter_md(root: Path, tier: float):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        if not path.is_file() or "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield path, tier


def corpus(include_evidence: bool = True) -> list[tuple[Path, float]]:
    """全部语料（路径 + 权重）。kb/ docs/ AGENTS.md 是主语料。"""
    items = list(_iter_md(KB, TIER_MAIN)) + list(_iter_md(DOCS, TIER_MAIN))
    if AGENTS.is_file():
        items.append((AGENTS, TIER_MAIN))
    if include_evidence:
        items.extend(_iter_md(WORK, TIER_EVIDENCE))
        if STATUS.is_file():
            items.append((STATUS, TIER_EVIDENCE))
    return items


class Doc:
    """一篇文档的检索视图（文本只读一次）。"""

    __slots__ = (
        "asked_low",
        "heads_low",
        "low",
        "name_low",
        "path",
        "rel",
        "text",
        "tier",
        "title",
    )

    def __init__(self, path: Path, tier: float) -> None:
        self.path = path
        self.tier = tier
        self.text = path.read_text(encoding="utf-8", errors="ignore")
        self.low = self.text.lower()
        first = next((ln for ln in self.text.splitlines() if ln.startswith("# ")), "")
        self.title = first.lstrip("# ").strip()
        #: ★ **全部**小标题（不只是 H1）拼在一起 —— 这是"这一篇**讲的是**什么"的判据。
        #: 2026-10-11 加：kb/29 逐字引用了所有控制问法，于是它成了"万能吸引文档"，
        #: 把真正的答案篇（kb/24，H1 里就写着「炮塔包」）挤到第 2 名。
        #: 结论：**正文里"说到了"不等于"讲的是"** —— 讲的是要看标题/小标题/文件名。
        self.heads_low = "\n".join(
            ln.lstrip("# ").strip() for ln in self.text.splitlines() if ln.startswith("#")
        ).lower()
        #: 「这一篇**回答过什么**」—— 用户原话 ＋ 问题形状的小标题。见 _QUOTE_MARK_RE 的注释。
        asked: list[str] = []
        in_marked_quote = False
        for raw_line in self.text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("#"):
                in_marked_quote = False
                if _QUESTION_RE.search(stripped):
                    asked.append(stripped.lstrip("# ").strip())
                continue
            marked = bool(_QUOTE_MARK_RE.search(raw_line))
            #: ⛔ **引号续行也要收**。本仓的写法常是
            #:     > 用户原话：「…」  换行   > 「…」
            #: 只认"标记与引语同一行"会把续行上的引语全漏掉
            #: （2026-10-11 实测：往 kb/21 补了「改单位外观该动哪儿」却查不到，就是这条）。
            if marked or in_marked_quote:
                asked.extend(_QUOTED_RE.findall(raw_line))
            in_marked_quote = marked or (in_marked_quote and stripped.startswith(">"))
        self.asked_low = "\n".join(asked).lower()
        self.name_low = path.name.lower()
        try:
            rel = path.relative_to(REPO_ROOT.parent)
        except ValueError:
            rel = path
        self.rel = str(rel).replace("\\", "/")


def load(include_evidence: bool = True) -> list[Doc]:
    docs = []
    for path, tier in corpus(include_evidence):
        try:
            docs.append(Doc(path, tier))
        except OSError:
            continue
    return docs


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #


def search(query: str, docs: list[Doc], limit: int = 6, use_quotes: bool = True) -> dict:
    """返回 {terms, hits, strong_n, missing, n_docs, n_bytes}。

    hits 每项：{rel, score, strong, why[], title, line_no, line, heading}
    """
    terms = terms_of(query)
    if not terms:
        return {
            "terms": [],
            "term_df": {},
            "hits": [],
            "strong_n": 0,
            "missing": [],
            "n_docs": len(docs),
            "n_bytes": 0,
        }

    counts = [[doc.low.count(t) for t in terms] for doc in docs]

    n_docs = max(len(docs), 1)
    df = [sum(1 for row in counts if row[i] > 0) for i in range(len(terms))]
    idf = [math.log(1.0 + n_docs / (1.0 + d)) for d in df]
    #: 「有辨识度」= 全库命中它的文档数不超过 5%（下限 3 篇）。
    rare_cut = max(3.0, n_docs * 0.05)
    avgdl = max(1.0, sum(len(d.text) for d in docs) / n_docs)

    hits = []
    for doc, row in zip(docs, counts, strict=True):
        score = 0.0
        why: set[str] = set()
        distinctive_located = False
        name_located = 0
        located = 0
        located_terms: list[str] = []
        norm = 1.0 - BM25_B + BM25_B * (len(doc.text) / avgdl)
        for term, count, weight, doc_freq in zip(terms, row, idf, df, strict=True):
            if count <= 0 or weight <= 0:
                continue
            piece = weight * (count * (BM25_K1 + 1.0)) / (count + BM25_K1 * norm)
            on_title = False
            if term in doc.name_low:
                name_located += 1
            if term in doc.heads_low:
                piece *= 3.0
                why.add("标题命中")
                on_title = True
            elif term in doc.name_low:
                piece *= 2.5
                why.add("文件名命中")
                on_title = True
            if on_title:
                located += 1
                located_terms.append(f"{term}(df={doc_freq})")
            #: 「有辨识度」要求 2 <= df <= 5%。⛔ 下界 2 不是洁癖：df==1 的**跨词碎块**
            #: （例：「这个游戏支持手柄吗」切出的 `戏支`/`戏支持`）会被误判成
            #: "稀有 ⇒ 有信息量"（2026-10-11 实测踩到）。
            if on_title and 2 <= doc_freq <= rare_cut:
                distinctive_located = True
            score += piece * doc.tier
        if score <= 0:
            continue
        #: ★ 词表鸿沟的桥：这一篇**回答过的问法**与你这一问有多像（asked_low 见 Doc.__init__）。
        #: 关掉它只剩词面匹配（--exam 就是拿这个做前后对照的）。
        asked_hits = sum(1 for t in terms if t in doc.asked_low) if use_quotes else 0
        asked_ratio = asked_hits / len(terms)
        score *= 1.0 + asked_ratio
        asked_exact = bool(
            use_quotes and len(query) >= 6 and query.strip().lower() in doc.asked_low
        )
        if asked_exact:
            score *= EXACT_QUOTE_BOOST
        #: ★ 强命中 = **这一篇"讲的是"你问的东西**，判据只看标题 / 小标题 / 文件名：
        #:   ① **文件名里**有两个以上检索词（「外部 mod」两个都进
        #:      `25-外部mod逆向-BARO.md` 的名字）；
        #:   ② 一个有辨识度的词（2 <= df <= 5%）落在标题/文件名里。
        #: ⛔ **别把"正文命中了 N 个稀有词"当强命中** —— 一个句子切出来的**跨词碎块**
        #:   （`戏支` `戏支持`）会随机稀有，把不相干的帖子顶成强命中：
        #:   2026-10-11 实测，「这个游戏支持 zqnx 吗」曾报 3 篇强命中。
        #: ⛔ 也别只看分数：「游戏」这种到处都是的词会把噪声篇顶起来。
        #:   ③ 这一篇**回答过的问法**与你这一问有 60% 以上重合（用户原话那一条桥）。
        strong = bool(name_located >= 2 or distinctive_located or asked_ratio >= 0.6)
        hits.append(
            {
                "doc": doc,
                "score": score,
                "strong": strong,
                "why": sorted(why),
                "located_terms": located_terms,
                "name_located": name_located,
                "asked_ratio": asked_ratio,
                "asked_exact": asked_exact,
                "tier": doc.tier,
            }
        )

    hits.sort(key=lambda h: h["score"], reverse=True)
    out = []
    for hit in hits[:limit]:
        doc: Doc = hit["doc"]
        line_no, line, heading = _best_span(doc, terms, idf)
        out.append(
            {
                "rel": doc.rel,
                "score": round(hit["score"], 2),
                "strong": hit["strong"],
                "why": hit["why"],
                "located_terms": hit["located_terms"],
                "name_located": hit["name_located"],
                "asked_ratio": round(hit["asked_ratio"], 2),
                "asked_exact": hit["asked_exact"],
                "tier": hit["tier"],
                "title": doc.title,
                "line_no": line_no,
                "line": line,
                "heading": heading,
                "snippet": _section_snippet(doc, line_no),
            }
        )

    missing = [t for t, d in zip(terms, df, strict=True) if d == 0 and len(t) >= 2]
    return {
        "terms": terms,
        "term_df": dict(zip(terms, df, strict=True)),
        "hits": out,
        "strong_n": sum(1 for h in out if h["strong"]),
        "missing": missing,
        "n_docs": len(docs),
        "n_bytes": sum(len(d.text.encode("utf-8", "ignore")) for d in docs),
    }


def _best_span(doc: Doc, terms: list[str], idf: list[float]) -> tuple[int, str, str]:
    """这一篇里**最该读的那一行** + 它所属的标题路径。"""
    weights = dict(zip(terms, idf, strict=True))
    best_no, best_line, best_score, best_heading = 0, "", -1.0, ""
    heading_stack: list[tuple[int, str]] = []
    for number, raw in enumerate(doc.text.splitlines(), 1):
        stripped = raw.strip()
        match = _HEADING_RE.match(stripped)
        if match:
            level = len(match.group(1))
            heading_stack = [h for h in heading_stack if h[0] < level]
            heading_stack.append((level, match.group(2).strip()))
        low = raw.lower()
        score = sum(w for t, w in weights.items() if t in low)
        if score > best_score:
            best_score = score
            best_no = number
            best_line = stripped
            best_heading = " § ".join(h[1] for h in heading_stack)
    return best_no, best_line[:150], best_heading


def _section_snippet(doc: Doc, line_no: int, limit: int | None = None) -> str:
    """那一节的**正文**（不含标题行），压成一行、截到 limit。

    ★ 为什么必须内联正文：原来的注入块只给**指针**（文件:行 + 标题路径），
    "读"这一步要再花一次工具调用 ⇒ 有成本 ⇒ 被跳过。
    2026-10-11 用户实测「注入了但会话没读」。把正文摆进来，"读"就不再是一个动作。
    """
    #: ⚠ 默认值**不能在签名里**取 INJECT_SNIPPET_CHARS —— 那是在**定义时**求值，
    #: 而常量在文件后面才定义 ⇒ 导入即 NameError（2026-10-11 实测踩到）。
    limit = INJECT_SNIPPET_CHARS if limit is None else limit
    lines = doc.text.splitlines()
    if line_no <= 0 or line_no > len(lines):
        return ""
    start = 0
    for i in range(line_no - 1, -1, -1):
        if _HEADING_RE.match(lines[i].strip()):
            start = i + 1
            break
    body: list[str] = []
    size = 0
    for raw in lines[start:]:
        text = raw.strip()
        #: 表格行与围栏压成一行后读不出意思，跳过（它们是给人对着表看的）。
        if not text or text.startswith("|") or text.startswith(chr(96) * 3):
            continue
        if _HEADING_RE.match(text) and body:
            break
        body.append(text)
        size += len(text)
        if size >= limit:
            break
    return " ".join(body)[:limit]


# --------------------------------------------------------------------------- #
# 打印
# --------------------------------------------------------------------------- #

#: 注入块的上限 —— 自动注入是**已记账消息**，会永久写进会话日志、每步都吃 token，必须限死。
#: 2026-10-11 起每条**带正文**，所以篇数从 3 降到 1、正文限 240 字。
INJECT_MAX_HITS = 1
INJECT_SNIPPET_CHARS = 240

#: ⛔ 三句话都不许删（每一句都对应一个实测出来的失败）：
#:   ① 抬头点明「**不是用户发言**」—— 否则模型把它当用户捎带的环境信息，读完不办事；
#:   ② 内联正文 —— 原来只给「文件:行 + 标题」这根**指针**，"读"要再花一次工具调用，
#:      2026-10-11 用户实测「注入了但会话没读」，就是这一步被跳过；
#:   ③ 要**回执** —— 「先读再动手」没有产出，跳过你看不出来；要它写一行，
#:      读没读就变成**可见**的（同 §0.9「结论自证」的路子）。
INJECT_HEADER = "⚠️ 知识库命中（自动注入，**不是用户发言**）—— 动手前先看这段："
INJECT_FOOTER = (
    "→ 用上了就在回复**第一行**写「kb: 已用 <文件>」；"
    "判为不相关就写「kb: 不相关 —— <为什么>」。这一行是给用户看的回执。"
)

#: 自动注入的**更严**闸门。⛔ 交互显示可以宽，**推送必须严** —— 推送进来的东西
#: Agent 无法否决，会直接当权威用。
#: 依据（2026-10-11 实测）：用户说「这一行也加上吧」，检索词只有 2 个
#: （一行 df=191、加上 df=8），却靠"一个有辨识度的词落在小标题里"够到了强命中，
#: 于是推了一篇毫不相干的编成台交接文档。这就是「推送放大错误」的现场。
INJECT_MIN_TERMS = 3

#: ★ 覆盖率闸门（2026-10-11 用户拍板改的）。
#: 起因：用户说「我只是测试一下**知识库**阅读而已」→ 被推了一篇 ⟪kb/29⟫（检索器自己的口径文档）。
#: ⟪--explain⟫ 打出来是 ⟪知识(df=56) + 识库(df=23) + 知识库(df=23)⟫ ——
#: **一个词投了三张票**（三个重叠 n-gram），把旧的 ⟪name_located >= 2⟧ 凑够了。
#: 用户以后每说一次「知识库」都会被推一次 ⇒ 必须治。
#:
#: 判据不是"票数"而是**覆盖率**：命中的词占这一问的多少。
#: ⚠ 单靠覆盖率不够 —— **一个词的问句覆盖率恒为 1.0**（「炮塔」命中一次就是 100%），
#: 所以还有词数下限 ⟪INJECT_MIN_TERMS_FOR_COVERAGE⟧。
INJECT_NAME_COVERAGE = 0.5
INJECT_MIN_TERMS_FOR_COVERAGE = 2

#: "检索器自己的结论"这一段的分隔行 —— 用例靠它把**工具的结论**与**引用的史料**分开。
VERDICT_MARK = "── 检索器的话 ──"

LIMIT_NOTE = (
    "  ⛔ 本工具**不能证明知识库里没有** —— 它只证明「这些字没在库里出现」。\n"
    "     词组没命中 != 知识没有：换个说法再搜，或者去 _work/ 挖、读反编译、问用户。"
)


def explain(result: dict) -> None:
    """逐篇打印"凭什么强/弱" —— 判据说不出理由时，先看这里，别改权重。"""
    term_df = result.get("term_df", {})
    listing = "  ".join(f"{t}(df={term_df.get(t, 0)})" for t in result["terms"])
    print(f"检索词（{len(result['terms'])}）：{listing}")
    print()
    for hit in result["hits"]:
        flag = "强" if hit["strong"] else "弱"
        where = " + ".join(hit.get("located_terms") or []) or "（正文命中，标题/文件名都没进）"
        where += f"  问法重合={hit.get('asked_ratio', 0)}"
        if hit.get("asked_exact"):
            where += "  ★原话逐字命中"
        print(f"[{flag}] {hit['rel']}  {hit['score']}  落在标题/文件名里的词：{where}")
        print(f"      标题：{hit['title']}")


def injectable(hit: dict, n_terms: int) -> bool:
    """这一条够不够格被**自动推送** —— 比"强命中"再严一级。

    两条同时满足才推（都是被上面那个误命中逼出来的）：

    1. **文件名覆盖率 >= 50% 且这一问不止一个词** ——
       （「外部 mod 是怎么逆的」只 2 个词，但两个都进了
       ⟪25-外部mod逆向-BARO.md⟫ 的名字 = 100%）；
    2. 或者 **检索词 >= 3 个** 且 **这一篇回答过的问法重合 >= 60%**。
       词数门槛只管这一条路：两个词的问句夹着泛用词时，重合率会虚高。
    ⛔ 比显示**少一条路**：**"只有小标题命中了一个稀有词"不算数** ——
    泛用动词（加上 / 做成 / 改成）会随机稀有，那正是 2026-10-11 那次误命中的来路。
    """
    #: ① 文件名覆盖率够高（且这一问不止一个词）。
    #: ⛔ 别退回去用"票数 >= 2"：同一个词的多个重叠 n-gram 会把票数刷上去
    #: （「知识库」→ 知识 + 识库 + 知识库 = 3 票，实测）。
    if n_terms >= INJECT_MIN_TERMS_FOR_COVERAGE and (
        hit.get("name_located", 0) / n_terms >= INJECT_NAME_COVERAGE
    ):
        return True
    #: ② 词够多 且 这一篇回答过的问法重合高（⟪改单位外观该动哪儿⟫ 走的就是这条）。
    return n_terms >= INJECT_MIN_TERMS and hit.get("asked_ratio", 0) >= 0.6


def render_inject(result: dict) -> str:
    """给 DSH 插件用的**极简**注入块：**只在强命中时**给东西，否则返回空串。

    ⛔ 三条不许改（每条都对应一个真后果）：

    1. **0 强命中必须返回空串** —— 空的注入和"知识库没有"在 Agent 眼里长得一样，
        所以宁可**什么都不说**，也绝不能把那份 34 行的入口清单塞进上下文；
    2. **只列强命中，弱命中一条都不进** —— 进了上下文的东西，Agent 会当权威；
    3. **限 3 篇 / 每行 110 字** —— 注入是永久的，每一步都要重新吃一遍 token。
    """
    #: ⛔ 只注入**长期件**（kb/ docs/ AGENTS.md），滚动件与 ⟪_work/⟫ 的证据不进注入。
    #: 理由就是 kb/19 与 ⟪_work/audit-material/00-Lead-深挖发现.md⟫ 的 F4：
    #: **「教训写在滚动文件里 = 等于没有」** —— 那注入就更不该把它当权威推给 Agent。
    #: （2026-10-11 实测：不滤掉的话 STATUS.md 会被当强命中推进来，纯噪声。）
    n_terms = len(result["terms"])
    strong = [
        h
        for h in result["hits"]
        if h["strong"] and h.get("tier", 0) >= TIER_MAIN and injectable(h, n_terms)
    ]
    if not strong:
        return ""
    lines = [INJECT_HEADER]
    for hit in strong[:INJECT_MAX_HITS]:
        where = f"{hit['rel']}:{hit['line_no']}" if hit["line_no"] else hit["rel"]
        section = hit["heading"] or hit["title"]
        lines.append(f"【{where} § {section}】")
        snippet = hit.get("snippet") or ""
        if snippet:
            lines.append(snippet)
    lines.append(INJECT_FOOTER)
    return "\n".join(lines)


def render(result: dict, query: str) -> str:
    lines: list[str] = []
    mb = result["n_bytes"] / 1024 / 1024
    lines.append(f"# 知识库检索：{query}")
    scope = "kb/ + docs/ + AGENTS.md；_work/ 降权"
    lines.append(f"  语料 {result['n_docs']} 篇 / {mb:.2f} MB（{scope}）")
    #: 只印**库里真出现过**的词；跨词碎块（df=0）对打分零贡献，混在一起只会让人不信任这行。
    term_df = result.get("term_df", {})
    present = [t for t in result["terms"] if term_df.get(t, 0) > 0]
    dots = " …" if len(present) > 20 else ""
    lines.append(f"  检索词：{' '.join(present[:20])}{dots}")
    if len(present) < len(result["terms"]):
        lines.append(f"          （另有 {len(result['terms']) - len(present)} 个词全库 0 次出现）")
    lines.append("")

    strong = [h for h in result["hits"] if h["strong"]]
    weak = [h for h in result["hits"] if not h["strong"]]

    if strong:
        lines.append(f"── 强命中 {len(strong)} 篇（**先读这些**）" + "─" * 20)
        for rank, hit in enumerate(strong, 1):
            tags = "".join(f" [{w}]" for w in hit["why"])
            lines.append(f" {rank}. {hit['rel']}   分数 {hit['score']}{tags}")
            if hit["title"]:
                lines.append(f"      标题：{hit['title']}")
            if hit["heading"]:
                lines.append(f"      段落：{hit['heading']}")
            if hit["line"]:
                lines.append(f"      {hit['line_no']}: {hit['line']}")
        lines.append("")
    else:
        lines.append("── 强命中 0 篇 ──")
        lines.append("")

    if weak:
        lines.append("── 弱命中（只是同词，**不代表相关**，别当依据）──")
        for hit in weak:
            title = f"  标题：{hit['title']}" if hit["title"] else ""
            lines.append(f"  · {hit['rel']}   分数 {hit['score']}{title}")
        lines.append("")

    lines.append(VERDICT_MARK)
    if strong:
        lines.append(f"  ✅ 有强命中 {len(strong)} 篇：**知识库里写过**。")
        lines.append("     先读第 1 篇再动手，别凭记忆答。")
    #: ★ 缺口**永远要播报**，哪怕同时有强命中 —— 它是**独立信息**：
    #: 找到了相关的篇，不代表你问的那个词库里有。
    #: ⛔ 第一版写成"只在没找到时才报"，于是「这个游戏支持手柄吗」会显示
    #: "✅ 知识库里写过"，而「手柄」全库 0 次这件事被压掉 —— 那正是最该看见的信号。
    if result["missing"]:
        shown_missing = " / ".join(result["missing"][:6])
        lines.append(f"  ⚠ 这些说法在库里 **0 次出现**：{shown_missing}")
        lines.append("     （含跨词碎块）—— **若其中有你真正想问的那个词**，说明本库没写过它。")
    lines.append(LIMIT_NOTE)
    if not strong:
        lines.append("")
        lines.append("  📖 强命中为 0 时，**必须从下面这些入口里挑**（本库按主题名编排；")
        lines.append("     你说「外观」而库里写「皮肤/涂装」这种词表鸿沟，词法检索跨不过去）：")
        for rel in entry_titles():
            lines.append(f"      {rel}")
    return "\n".join(lines)


#: 入口清单缓存。⛔ **不许要求调用方先"刷新"它** —— 第一版就是那样，
#: 结果 render() 在没先跑过 main() 的地方（用例里）静默少了整块入口清单。
#: 隐藏前置条件 = 下一个踩坑的人查不出来。改成惰性求值，用的时候自己算。
_ENTRY_TITLES: list[str] = []


def entry_titles() -> list[str]:
    """kb/ 全部入口（文件名即主题名）—— 强命中为 0 时摆出来，让人自己点。"""
    if not _ENTRY_TITLES:
        _ENTRY_TITLES.extend(f"kb/{path.name}" for path, _tier in _iter_md(KB, TIER_MAIN))
    return _ENTRY_TITLES


# --------------------------------------------------------------------------- #
# 正控 / 负控
# --------------------------------------------------------------------------- #

#: (问法, 必须强命中的路径片段) —— 全部是**用户会说的话**，不是关键词。
POSITIVE: tuple[tuple[str, str], ...] = (
    ("怎么造一个炮塔包", "kb/24-造一个炮塔包"),
    ("场景编辑器能加自定义模型吗", "kb/28-场景编辑器与自定义模型"),
    ("贴图方向哪个契约", "kb/21-贴图方向契约"),
    ("联机封号", "kb/26-联机封号"),
    ("外部 mod 是怎么逆的", "kb/25-外部mod逆向"),
)

#: 负控问法的**模板**（{nonce} 是每次现挑的合成词）。
NEGATIVE_TEMPLATE = "这个游戏支持 {nonce} 吗"


def pick_absent_nonce(docs: list[Doc]) -> str:
    """现挑一个**全库一定没有**的合成词。

    ⛔ **别写死一个词** —— 2026-10-11 一天之内被自己打脸**三次**：
       每把负控问法写进 kb/29 一次，那个词就有 df 了，负控当场失效
       （第一次是真实词「手柄」，第二次、第三次是写死的合成词）。
       根因是"测试耦合了语料内容"，而**文档本来就该引用案例**。
       负控要的是**机制**（词级 0 次出现 ⇒ 不许报"写过"），不是某个具体词。
    """
    for i in range(26):
        cand = "zqn" + chr(ord("a") + i)
        if all(cand not in d.low for d in docs):
            return cand
    raise AssertionError("26 个候选合成词全被语料占了 —— 扩充候选，别 skip")


def negative_query(docs: list[Doc]) -> tuple[str, str]:
    """返回 (问法, 那个合成词)。"""
    nonce = pick_absent_nonce(docs)
    return NEGATIVE_TEMPLATE.format(nonce=nonce), nonce
#: 旧入口那句劝退话：**本工具自己的结论区**一个字都不许再出现。
#: ⚠ 只约束结论区 —— kb/29 作为**史料**引用原话是合法的（断言不能反噬文档）。
FORBIDDEN = "这份知识库没写过"


def exam_pairs(docs: list[Doc], want: int = 40) -> list[tuple[str, str]]:
    """全库现成的「用户原话 -> 它原来那一篇」配对 —— 这是**天然考卷**，不用手写题。

    ⚠ 均匀取样、**不用 random**：判据要能复算。
    """
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for doc in docs:
        for line in doc.text.splitlines():
            if not _QUOTE_MARK_RE.search(line):
                continue
            for quote in _QUOTED_RE.findall(line):
                quote = quote.strip()
                if not (6 <= len(quote) <= 60) or quote in seen:
                    continue
                seen.add(quote)
                uniq.append((quote, doc.rel))
    step = max(1, len(uniq) // max(want, 1))
    return uniq[::step][:want]


def exam(docs: list[Doc], want: int = 40) -> int:
    """拿**用户原话**当问句去查，看找不找得到它原来那一篇。

    前后对照：关掉引语信号 = 只剩词面匹配（**修之前的处境**）；打开 = 加上"用户的话"这座桥。
    """
    pairs = exam_pairs(docs, want)
    if not pairs:
        print("❌ 一条用户原话都没抽到 —— 引语标记可能要跟着文档习惯更新")
        return 1
    print(f"考卷：{len(pairs)} 条**用户原话**（全库均匀取样，可复算）")
    print()
    tagged: list[tuple[str, str, list[str]]] = []
    report: list[tuple[str, int, int, list[tuple[str, str]]]] = []
    for label, use_quotes in (("关掉引语信号", False), ("打开引语信号", True)):
        top1 = top3 = 0
        misses: list[tuple[str, str]] = []
        for quote, rel in pairs:
            rels = [h["rel"] for h in search(quote, docs, limit=3, use_quotes=use_quotes)["hits"]]
            if rels and rels[0] == rel:
                top1 += 1
            if rel in rels:
                top3 += 1
            else:
                misses.append((quote, rel))
            if use_quotes:
                tagged.append((quote, rel, rels))
        report.append((label, top1, top3, misses))
    n = len(pairs)
    for label, top1, top3, _ in report:
        p1, p3 = top1 * 100 // n, top3 * 100 // n
        print(f"  {label}：top1 {top1}/{n}（{p1}%） · top3 {top3}/{n}（{p3}%）")
    print()
    final_miss = report[-1][3]
    print(f"开了引语信号仍 top3 找不到的（{len(final_miss)} 条，**这才是真缺口**）：")
    for quote, rel in final_miss[:8]:
        print(f"  · 「{quote[:34]}」 -> 原本在 {rel}")
    return 0


def selftest() -> int:
    docs = load()
    checks: list[tuple[str, bool, str]] = []

    for query, expect in POSITIVE:
        res = search(query, docs)
        hit = next((h for h in res["hits"] if h["strong"] and expect in h["rel"]), None)
        got = ", ".join(h["rel"] for h in res["hits"][:2]) or "（空）"
        checks.append((f"正控·{query}", hit is not None, f"要求强命中 {expect}；实得 {got}"))

    #: ★ 反恒真：**答案那一篇**里没有整串 ⇒ 旧的整句匹配器在它上面**必红**；
    #: 而新检索器必须把它排到**第 1 名**。
    #: ⛔ 别把这条写成"整串在**全库** 0 次" —— 那样 kb/29 一引用这句问法就把它打红，
    #: 变成"文档不能讨论自己当年的 bug"（2026-10-11 实测踩到）。断言要落在**答案篇**上。
    probe = "怎么造一个炮塔包"
    answer = next((d for d in docs if "kb/24-造一个炮塔包" in d.rel), None)
    res = search(probe, docs)
    top1 = res["hits"][0]["rel"] if res["hits"] else "（空）"
    contains_probe = answer is not None and probe in answer.text
    ok = answer is not None and not contains_probe and "kb/24-造一个炮塔包" in top1
    checks.append(
        (
            "反恒真·答案篇不含整串但排第 1",
            ok,
            f"答案篇在={answer is not None}；答案篇含整串={contains_probe}；top1={top1}",
        )
    )

    query, nonce = negative_query(docs)
    res = search(query, docs)
    text = render(res, query)
    verdict = text.split(VERDICT_MARK, 1)[-1]
    absent_df = res["term_df"].get(nonce, -1)
    ok = (
        res["strong_n"] == 0
        and absent_df == 0
        and FORBIDDEN not in verdict
        and "0 次出现" in verdict
        and "kb/21-贴图方向契约与素材管线.md" in text  # 词表鸿沟 -> 必须摆入口
    )
    checks.append(
        (
            "负控·合成的库里没有的词",
            ok,
            f"强命中 {res['strong_n']}；{nonce} 的 df={absent_df}；"
            f"结论区有劝退词={FORBIDDEN in verdict}；报了 0 次出现={'0 次出现' in verdict}；"
            f"摆了入口={'kb/21' in text}",
        )
    )

    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} —— {detail}")
    print()
    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        print(f"❌ 自检失败 {len(failed)} 条：{' / '.join(failed)}")
        return 1
    print(f"✅ 自检全绿（正控 {len(POSITIVE)} + 反恒真 1 + 负控 1 = {len(checks)} 条）")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="拿**原话**查知识库：返回读哪一篇、哪一段（本库唯一检索入口）。",
    )
    parser.add_argument("question", nargs="*", help="就用你平时怎么说的（整句也行）")
    parser.add_argument("--limit", type=int, default=6, help="最多打印几篇（默认 6）")
    parser.add_argument("--no-evidence", action="store_true", help="不查 _work/ 与 STATUS.md")
    parser.add_argument("--json", action="store_true", help="输出 JSON（给自动注入用）")
    parser.add_argument("--selftest", action="store_true", help="正控/负控自检（能红才算尺子）")
    parser.add_argument(
        "--exam", type=int, default=0, metavar="N", help="拿 N 条**用户原话**当问句考它（前后对照）"
    )
    parser.add_argument(
        "--inject",
        action="store_true",
        help="给 DSH 插件用：只输出极简强命中块；0 命中输出空串（空 = 不注入任何东西）",
    )
    parser.add_argument("--explain", action="store_true", help="逐篇打印凭什么强/弱（维护用）")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.exam:
        return exam(load(), args.exam)

    query = " ".join(args.question).strip()
    if not query:
        parser.print_help()
        return 2

    docs = load(include_evidence=not args.no_evidence)
    result = search(query, docs, limit=args.limit)
    if args.inject:
        text = render_inject(result)
        if text:
            sys.stdout.write(text + "\n")
        return 0
    if args.explain:
        explain(result)
    elif args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result, query))
    return 0


if __name__ == "__main__":
    sys.exit(main())
