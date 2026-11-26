r"""tools/kb_lookup.py 的用例：**正控**（尺子必须能红）+ **负控** + **反恒真**。

判据一行：

    .venv\Scripts\python.exe -m pytest tests/test_kb_lookup.py -q
    -> 17 条通过。

为什么必须做正控：一把**永远返回空**的检索器也能让负控变绿 ——
所以每条正控都先喂一个**已知在库里的问法**，看它报不报。

由来（2026-10-11）：用户报「Agent 遇到知识库本来就有的东西也不去翻，纯靠猜」。
实测旧门 0（整句子串匹配）对 6 条自然语言问句 **6/6 全 0 命中**，
还打印「这只说明**这份知识库没写过**」—— 一句假阴性的**劝退**，
而 kb/24 的**文件名就是那句问话**。

---

## ⛔ 两条血泪口径（2026-10-11 当天踩的，别退回去）

**① 断言不能落在"全库没有某个字符串"上。**
第一版写的是「整串『怎么造一个炮塔包』在**全库** 0 次出现」。
然后 kb/29 把这句话当**案例**写进了文档 —— 前提当场失效，两条用例变红。
⇒ 正确落点：**答案那一篇**（kb/24）里没有整串（所以旧的整句匹配器在它上面**必红**），
   且新检索器把它排到**第 1 名**。文档爱讨论什么就讨论什么。

**② 负控不能用"真实词"。**
第一版拿「手柄」当负控（当时全库 0 次）。kb/29 一写"当年那条负控问的是手柄"，
`手柄⟫ 就有 1 篇了 —— 负控失效。
⇒ 正确做法：负控夹一个**合成的、全库一定没有的**词（`kb_lookup.NEGATIVE_ABSENT⟫），
   断言的是**机制**（词级 0 次出现 ⇒ 不许报"写过"），不是某个具体词。

**③ 劝退话术的检查只约束"工具的结论区"。**
kb/29 作为**史料**引用原话是合法的；不合格的是**工具自己**说这句话。
所以用 `kb_lookup.VERDICT_MARK⟫ 把结论区切出来再断言。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
#: tools/ 不在包里 —— 与 tests/ 下其他"戳脚本"的用例同款，运行时挂 sys.path。
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import kb_lookup  # noqa: E402
import three_doors  # noqa: E402

#: 正控问法全部是**用户会说的话**（不是关键词）—— 这是本用例的立场：
#: 用户说人话，检索器就必须认得。改这些期望值之前先问"是不是在把尺子调松"。
POSITIVE = kb_lookup.POSITIVE

#: 反恒真用的那一问，以及它的**答案篇**。
PROBE = "怎么造一个炮塔包"
ANSWER_DOC = "kb/24-造一个炮塔包"


@pytest.fixture(scope="module")
def docs():
    """全库只读一次（2026-10-18 实测：**9.3 MB / 536 篇**，读一遍约 3.2 秒）。

    重记命令（语料一变这几个数就会漂，别凭印象改）：

        .venv\\Scripts\\python.exe _work\\probe_kb_stats.py

    ⚠️ 2026-10-18 之前这里写的是「5 MB / 353 篇」—— 那时候 `kb_lookup._iter_md` 还会把
    `_work/` 下几个 Unity 工程的 `Library/PackageCache` 与 `Packages` 里的第三方包文档
    （**8118 篇** CHANGELOG / LICENSE）也扫进语料；那是 `--selftest` 负控变红的根因，
    已在 `kb_lookup.VENDOR_DIRS` 里堵掉。
    """
    return kb_lookup.load()


def _verdict(text: str) -> str:
    """把"检索器自己的结论"那一段切出来（引用的史料不算）。"""
    return text.split(kb_lookup.VERDICT_MARK, 1)[-1]


# --------------------------------------------------------------------------- #
# 正控：已知在库里的问法，必须强命中
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("query", "expect"), POSITIVE)
def test_positive_control_strongly_hits(query: str, expect: str, docs) -> None:
    res = kb_lookup.search(query, docs)
    strong = [h["rel"] for h in res["hits"] if h["strong"]]
    assert any(expect in rel for rel in strong), (
        f"问「{query}」必须**强命中** {expect}；实得强命中 {strong or '（空）'}"
    )


# --------------------------------------------------------------------------- #
# 反恒真：旧匹配器在答案篇上必红，新匹配器必须排第 1
# --------------------------------------------------------------------------- #


def test_answer_doc_does_not_contain_the_whole_question(docs) -> None:
    """答案篇里没有整串 ⇒ 旧的整句匹配器**在它上面必红**。

    ⛔ 别改成"全库 0 次"：kb/29 把这句话当案例写进了文档，那样会自己把自己打红。
    """
    answer = next((d for d in docs if ANSWER_DOC in d.rel), None)
    assert answer is not None, f"语料里找不到答案篇 {ANSWER_DOC}"
    assert PROBE not in answer.text, (
        f"{ANSWER_DOC} 里居然有整串「{PROBE}」—— 反恒真前提没了，请另选一句问法"
    )


def test_same_question_ranks_the_answer_doc_first(docs) -> None:
    """同一句话，新检索器必须把答案篇排到**第 1 名** —— 与上一条**成对**，缺一条都不算尺子。"""
    res = kb_lookup.search(PROBE, docs)
    assert res["hits"], "一篇都没命中"
    top1 = res["hits"][0]["rel"]
    assert ANSWER_DOC in top1, f"top1 应是 {ANSWER_DOC}，实得 {top1}"


# --------------------------------------------------------------------------- #
# 负控：合成的、库里一定没有的词
# --------------------------------------------------------------------------- #


def test_negative_control_term_is_absent(docs) -> None:
    """前提：负控那个合成词**确实**全库 0 次 —— 前提没了整条负控就没意义。

    ⛔ 那个词是**每次现挑**的（kb_lookup.pick_absent_nonce），不许在别处写死：
    2026-10-11 一天里被自己打脸三次 —— 每把负控问法写进 kb/29 一次，词就有 df 了。
    """
    _query, nonce = kb_lookup.negative_query(docs)
    res = kb_lookup.search(_query, docs)
    assert res["term_df"].get(nonce) == 0, f"合成词 {nonce} 竟然在库里出现了 —— 换一个"


def test_negative_control_claims_no_coverage(docs) -> None:
    """有词全库 0 次出现 ⇒ 必须**不报强命中**，且把缺口说出来。"""
    query, nonce = kb_lookup.negative_query(docs)
    res = kb_lookup.search(query, docs)
    assert res["strong_n"] == 0, f"不该有强命中：{res['hits'][:2]}"
    assert nonce in res["missing"], f"实得 missing={res['missing']}"


def test_negative_control_prints_entry_titles(docs) -> None:
    """强命中为 0 时**必须摆出入口清单** —— 词表鸿沟只能靠人点，不能靠一句"没找到"。"""
    query, _nonce = kb_lookup.negative_query(docs)
    text = kb_lookup.render(kb_lookup.search(query, docs), query)
    assert "kb/21-贴图方向契约与素材管线.md" in text
    assert "不能证明知识库里没有" in text


# --------------------------------------------------------------------------- #
# 禁止话术：工具自己不许说那句劝退（史料引用不算）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        PROBE,
        "场景编辑器能加自定义模型吗",
        "改单位外观该动哪儿",
        "贴图方向哪个契约",
        "联机封号",
        "这个游戏支持 zqnx 吗",
    ],
)
def test_forbidden_phrase_never_appears_in_verdict(query: str, docs) -> None:
    """「这份知识库没写过」是**假阴性的源头**（实测把它自己标题里的东西报成"没写过"）。

    只查**工具的结论区** —— kb/29 作为史料引用原话是合法的。
    """
    verdict = _verdict(kb_lookup.render(kb_lookup.search(query, docs), query))
    assert kb_lookup.FORBIDDEN not in verdict, f"劝退话术复活了，问的是「{query}」"


# --------------------------------------------------------------------------- #
# 接线：three_doors 的门 0 必须是薄壳，且不再劝退
# --------------------------------------------------------------------------- #


def test_three_doors_gate0_delegates_and_does_not_lie(capsys) -> None:
    """旧入口问这句话曾经 0 命中 + 劝退；接线后必须**排第 1** 且结论区不劝退。"""
    three_doors.main([PROBE])
    out = capsys.readouterr().out
    assert "唯一实现 = tools/kb_lookup.py" in out
    assert "1. " in out and ANSWER_DOC in out
    assert kb_lookup.FORBIDDEN not in _verdict(out)


# --------------------------------------------------------------------------- #
# 「用户的话」这座桥（词表鸿沟）—— 2026-10-11 加
# --------------------------------------------------------------------------- #


def test_quote_index_is_not_empty(docs) -> None:
    """引语索引不许是空的 —— 它一空，「用户的话」这座桥就**静默失效**。

    ⛔ 这条是给文档习惯上的保险：谁把 ⟪用户原话：「…」⟫ 这种写法改没了，这里必须红。
    """
    pairs = kb_lookup.exam_pairs(docs, 500)
    assert len(pairs) >= 30, f"全库只抽到 {len(pairs)} 条用户原话 —— 引语标记要跟着文档习惯更新"


def test_quote_bridge_beats_pure_lexical(docs) -> None:
    """前后对照：拿**用户原话**当问句，开了引语信号必须比关掉好。

    这条是这座桥的**唯一验收** —— 没有它，"桥到底有没有在起作用"没人知道。
    实测（40 条，2026-10-11）：关 top1 37% / top3 67%；开 top1 85% / top3 100%。
    """
    pairs = kb_lookup.exam_pairs(docs, 12)

    def top3(use_quotes: bool) -> int:
        hits = 0
        for quote, rel in pairs:
            res = kb_lookup.search(quote, docs, limit=3, use_quotes=use_quotes)
            hits += rel in [h["rel"] for h in res["hits"]]
        return hits

    off, on = top3(False), top3(True)
    assert on >= off, f"开了引语信号反而更差：关 {off} / 开 {on}"
    assert on >= len(pairs) * 3 // 4, f"开了引语信号 top3 只有 {on}/{len(pairs)} —— 桥退化了"


def test_exact_quote_boost_is_needed_for_rolling_files(docs) -> None:
    """逐字记着你原话的大文件（STATUS.md）不许被长度归一压出 top3。

    ⛔ 这条守着 ⟪EXACT_QUOTE_BOOST⟫：删掉它，滚动文件那几条会当场掉出去。
    """
    pairs = [
        (q, rel)
        for q, rel in kb_lookup.exam_pairs(docs, 500)
        # 2026-11-09：工作根从仓内 `_work/` 移到仓外 `_work_db/`，两种前缀都认。
        if "STATUS" in rel or "_work/" in rel or "_work_db/" in rel
    ]
    assert pairs, "没抽到滚动文件里的原话 —— 语料结构变了，请复核这条用例的前提"
    ok = 0
    for quote, rel in pairs[:6]:
        res = kb_lookup.search(quote, docs, limit=3)
        ok += rel in [h["rel"] for h in res["hits"]]
    assert ok == min(6, len(pairs)), f"滚动文件的原话命中 {ok}/{min(6, len(pairs))}"


# --------------------------------------------------------------------------- #
# 自动注入用的 --inject：**只在强命中时给东西，空命中必须什么都不给**
# --------------------------------------------------------------------------- #


def test_inject_returns_nothing_when_no_strong_hit(docs) -> None:
    """⛔ 空命中必须返回**空串**。

    空的注入和"知识库没有"在 Agent 眼里长得一样 —— 所以宁可什么都不说，
    也绝不能把那份 34 行的入口清单塞进上下文。
    """
    query, _nonce = kb_lookup.negative_query(docs)
    assert kb_lookup.render_inject(kb_lookup.search(query, docs)) == ""


def test_inject_only_lists_strong_hits(docs) -> None:
    """弱命中**一条都不许进注入** —— 进了上下文的东西，Agent 会当权威。"""
    res = kb_lookup.search("怎么造一个炮塔包", docs)
    text = kb_lookup.render_inject(res)
    assert text, "这条应当有强命中"
    for hit in [h for h in res["hits"] if not h["strong"]]:
        assert hit["rel"] not in text, f"弱命中 {hit['rel']} 混进注入了"


def test_inject_excludes_rolling_files(docs) -> None:
    """滚动件（STATUS.md）与 _work/ 的证据**不进注入**。

    依据 = _work/audit-material/00-Lead-深挖发现.md 的 F4：
    **「教训写在滚动文件里 = 等于没有」** —— 那更不该把它当权威推给 Agent。
    """
    text = kb_lookup.render_inject(kb_lookup.search("怎么造一个炮塔包", docs))
    #: ⚠ 只查**出处行**：正文里引用 _work/ 是合法且常见的（那正是它的证据栏），
    #: 不合格的是"把它当权威**推**过来"。2026-10-11 实测：kb/24 的正文第一段就写着
    #: 「证据：_work/tierB/b2_build.py …」—— 断言落在整段文本上会误伤。
    heads = [ln for ln in text.splitlines() if ln.startswith("【")]
    assert heads, "没有出处行"
    for ln in heads:
        assert "STATUS.md" not in ln, f"滚动件进了注入：{ln}"
        assert "_work/" not in ln, f"证据件进了注入：{ln}"


def test_inject_payload_is_small(docs) -> None:
    """注入是**永久**的、每步都吃一遍 token ⇒ 限篇数、限长。"""
    for query in ("怎么造一个炮塔包", "改单位外观该动哪儿", "联机封号"):
        text = kb_lookup.render_inject(kb_lookup.search(query, docs))
        assert len(text) <= 900, f"「{query}」注入了 {len(text)} 字符，太长"
        #: 抬头 1 行 + 每条 2 行（出处 + 正文）+ 回执行 1 行
        assert len(text.splitlines()) <= kb_lookup.INJECT_MAX_HITS * 2 + 2


def test_inject_inlines_content_not_just_a_pointer(docs) -> None:
    """★ 注入必须**内联正文**，不许只给指针。

    2026-10-11 用户实测「注入了但会话没读」—— 根因就是原来只给「文件:行 + 标题」，
    「读」要再花一次工具调用，有成本 ⇒ 被跳过。把正文摆进来，读就不再是一个动作。
    ⛔ 这条守着别退回"只给指针"。
    """
    text = kb_lookup.render_inject(kb_lookup.search("改单位外观该动哪儿", docs))
    assert text, "这条应当注入"
    #: 正文长度必须显著超过"出处那一行"，否则就是退回了指针形态。
    body_lines = [ln for ln in text.splitlines() if not ln.startswith(("⚠️", "【", "→"))]
    assert body_lines, "没有正文行 —— 退回指针形态了"
    assert len(body_lines[0]) >= 80, f"正文只有 {len(body_lines[0])} 字符，等于没给内容"
    assert "皮肤" in text or "涂装" in text, "正文里应当真的出现该节的词"


def test_inject_demands_a_receipt(docs) -> None:
    """★ 注入必须要求一句**回执** —— 「先读再动手」没有产出，跳过看不出来。"""
    text = kb_lookup.render_inject(kb_lookup.search("怎么造一个炮塔包", docs))
    assert "第一行" in text and "kb:" in text, "没有回执要求，读没读就又变成不可见了"


# --------------------------------------------------------------------------- #
# 自动注入的**更严**闸门（injectable）—— 推送是 Agent 无法否决的
# --------------------------------------------------------------------------- #


def test_injectable_rejects_vague_two_term_question(docs) -> None:
    """⛔ 真机误命中现场（2026-10-11 用户消息「这一行也加上吧」）。

    检索词只有 2 个（一行 df=191 / 加上 df=8），却靠"一个有辨识度的词落在小标题里"
    够到了强命中，于是推了一篇毫不相干的编成台交接文档。
    **泛用动词会随机稀有** —— 所以推送不许走那条路。
    """
    res = kb_lookup.search("这一行也加上吧", docs)
    assert kb_lookup.render_inject(res) == "", "这句不该注入任何东西"


def test_injectable_keeps_short_but_named_question(docs) -> None:
    """「外部 mod 是怎么逆的」只有 2 个检索词，但**两个都进了文件名** ⇒ 必须注入。

    （这条守着闸门别矫枉过正：文件名命中是最强信号，词少也认。）
    """
    res = kb_lookup.search("外部 mod 是怎么逆的", docs)
    assert "kb/25-外部mod逆向" in kb_lookup.render_inject(res)


def test_injectable_requires_name_or_repeat(docs) -> None:
    """够格推送只有两条路：**文件名里两个词**，或**词 >= 3 且问法重合 >= 60%**。"""
    res = kb_lookup.search("这一行也加上吧", docs)
    n = len(res["terms"])
    for hit in res["hits"]:
        if not hit["strong"]:
            continue
        assert not kb_lookup.injectable(hit, n), f"{hit['rel']} 不该够格注入"
    good = kb_lookup.search("怎么造一个炮塔包", docs)
    assert any(kb_lookup.injectable(h, len(good["terms"])) for h in good["hits"])


def test_injectable_coverage_is_about_coverage_not_votes() -> None:
    """⛔ 判据是**覆盖率**，不是票数 —— 一个词投三票不许够格。

    真机现场：⟪--explain⟫ 打出 ⟪知识(df=56) + 识库(df=23) + 知识库(df=23)⟫，
    同一个词的三个重叠 n-gram 把旧的"票数 >= 2"凑够了，于是
    「我只是测试一下知识库阅读而已」被推了百科式的无关文档。

    ⛔⛔ **这条一个字都不许碰语料**。第一版写的是"那句话不许注入"，
    结果我把那句话写进 kb/29 当案例的**下一格**，它就被引语桥认成了"记录过的问法"、
    绕开覆盖率从 ⟪asked_ratio⟫ 那条路注入了 —— **本仓到今天踩了五次同一个坑**。
    闸门的性质只能在**闸门函数**上验，不能在语料上验。
    """
    #: 18 个检索词里只命中 3 个（同一个词的三个 n-gram）⇒ 17% ⇒ 不够格
    assert not kb_lookup.injectable({"name_located": 3, "asked_ratio": 0.0}, 18)
    #: 两个词全进文件名 ⇒ 100% ⇒ 够格（不管词多短）
    assert kb_lookup.injectable({"name_located": 2, "asked_ratio": 0.0}, 2)
    #: 一个词 ⇒ 覆盖率恒 100%，靠**词数下限**挡掉
    assert not kb_lookup.injectable({"name_located": 1, "asked_ratio": 0.0}, 1)
    #: 文件名零命中，但问法重合 100% 且词够多 ⇒ 走第二条路
    assert kb_lookup.injectable({"name_located": 0, "asked_ratio": 1.0}, 7)
    #: 问法重合不够 ⇒ 两条路都不通
    assert not kb_lookup.injectable({"name_located": 0, "asked_ratio": 0.5}, 7)
    #: 词不够多时，问法重合那条路也不许走（两个词的问句重合率会虚高）
    assert not kb_lookup.injectable({"name_located": 0, "asked_ratio": 1.0}, 2)


def test_injectable_coverage_keeps_short_but_complete_match(docs) -> None:
    """正控：只 2 个检索词，但**两个都在文件名里**（覆盖率 100%）⇒ 必须注入。

    这条守着覆盖率**别矫枉过正**：它压的是"票数被同一个词刷高"，不是"词少"。
    """
    text = kb_lookup.render_inject(kb_lookup.search("外部 mod 是怎么逆的", docs))
    assert "kb/25-外部mod逆向" in text


def test_injectable_coverage_needs_more_than_one_term(docs) -> None:
    """⚠ 一个词的问句**覆盖率恒为 100%**（命中一次就满分）⇒ 词数下限不许删。

    实测：「炮塔」命中的 top1 是 ⟪docs/交接-2026-10-02-飞机也摆炮塔武器弹药.md⟫，
    也是 1/1 = 100% —— 但它不是"造炮塔包"的答案。
    """
    assert kb_lookup.render_inject(kb_lookup.search("炮塔", docs)) == ""


# --------------------------------------------------------------------------- #
# 自检入口本身
# --------------------------------------------------------------------------- #


def test_selftest_exit_code_is_zero(capsys) -> None:
    """「--selftest」必须能跑通（它是给人肉复算的那把尺子）。"""
    assert kb_lookup.selftest() == 0
    assert "自检全绿" in capsys.readouterr().out