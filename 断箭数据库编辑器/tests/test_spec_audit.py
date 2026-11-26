"""`tools/spec_audit.py` 的机械断言 **＋ 每条尺子的负控**（task-7 · AGENTS §0.9 动作 2）。

这份用例只做一件事：**证明那把尺子能红**。

- **空洞尺子**：表在工具里（`spec_audit.SCAN_CASES`），这里只跑它 —— ⛔ 逻辑只有一份，
  别在本文件抄第二张表。正控（不算空洞）与负控（必须算）成对摆着，谁松了都当场红。
- **《待真机验证清单.md》**（用户醒来的第一张纸）：五件齐 · 标题是症状不是疑问句 ·
  编号唯一 · 已标废的旧口径必须带标记 —— 每条都在这里喂一个**已知违规**的点，
  看它报不报。
- **正控**：五份真文档跑一遍审计，必须零红。

负控一律改**临时目录里的副本**（`tmp_path`），⛔ 不碰真文档。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DOCS = ROOT / "docs"

#: 已标废的 LOD 旧口径 —— 喂给 `CONDEMNED_DOCS` 里**每一份**，守卫都必须红。
CONDEMNED_SAMPLE = "- **判据**：减面后 **bbox 与 LOD0 逐位相同**，顶点落在 1/8 ~ 1/10。"


def load_spec_audit():
    """把 `tools/spec_audit.py` 当模块加载（`tools/` 不是包，不能用 import）。"""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("spec_audit_under_test", TOOLS / "spec_audit.py")
    assert spec and spec.loader, "spec_audit.py 加载不了 —— 路径变了？"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def real_texts(sa) -> dict[str, str]:
    """五份真文档的正文（负控只改副本）。"""
    return {name: (DOCS / name).read_text(encoding="utf-8") for name in sa.DOC_NAMES}


def reds_of(sa, tmp_path: Path, texts: dict[str, str]) -> list[str]:
    """把 texts 落到临时目录跑一遍审计，回所有红。"""
    for name, text in texts.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    results, _missing = sa.run_all(tmp_path, ROOT, quiet=True)
    return [why for r in results for why in r.bad]


# ───────────────────────── ① 空洞尺子（表在工具里） ─────────────────────────


def test_scan_holes_case_matrix():
    """现场逐例对拍 `spec_audit.SCAN_CASES`（正控与负控都在那张表里）。"""
    sa = load_spec_audit()
    wrong = []
    for name, sample, expect in sa.SCAN_CASES:
        holes, _pending, _unread, _bad = sa.scan_holes(sample)
        if len(holes) != expect:
            wrong.append(
                f"{name}：期望 {expect} 条，实到 {len(holes)} 条 {[h.strip() for h in holes]}"
            )
    assert not wrong, "scan_holes 逐例对拍失败：\n" + "\n".join(wrong)


def test_the_ruler_can_go_red():
    """⛔ 负控：喂一个**已知空洞**进去，它必须报出来。

    没有这一条，上面那张矩阵可能因为"什么都认"而全绿（假绿）。
    """
    sa = load_spec_audit()
    sample = "## 9. 空洞\n| # | 空洞 | 堵在哪 |\n|---|---|---|\n| 5 | 未读 | 堵在哪 |\n"
    holes, _pending, unread, _bad = sa.scan_holes(sample)
    assert len(holes) == 1, f"真空洞行没被算出来 —— 尺子红不了：{holes}"
    assert len(unread) == 1, f"真空洞行没被算成「未读」：{unread}"


def test_filled_tag_accepts_multi_id_but_not_everything():
    """`FILLED_RE` 只多认"一个标记指多个编号"，⛔ 不是"什么都认"。"""
    sa = load_spec_audit()
    assert sa.FILLED_RE.search("【读数·P0-6 / P0-8】")
    assert sa.FILLED_RE.search("【读数·P0-4】")
    assert not sa.FILLED_RE.search("【读数·P0-x】"), "占位符不该被当成回填标记"
    assert not sa.FILLED_RE.search("待回填 P0-6"), "没有戳的不算回填"


# ───────────────────── ② 《待真机验证清单》＝ 用户第一张纸 ─────────────────────


def test_realtime_checklist_is_covered():
    """那张纸必须**在机械覆盖里** —— 它修前不在 DOC_NAMES，所以已废口径没人管。"""
    sa = load_spec_audit()
    assert sa.REALTIME == "待真机验证清单.md"
    assert sa.REALTIME in sa.DOC_NAMES, "《待真机验证清单》掉出 DOC_NAMES 了 —— 零机械覆盖"
    assert sa.REALTIME in sa.CONDEMNED_DOCS, "已废口径守卫没覆盖这张纸"
    assert (DOCS / sa.REALTIME).is_file(), "这张纸不在 docs/ 下"


def test_realtime_entries_are_complete_and_stated_as_symptoms():
    """五件齐 ＋ 标题不是疑问句 ＋ 编号唯一 —— **现文档**的读数。"""
    sa = load_spec_audit()
    entries = sa.entries_of((DOCS / sa.REALTIME).read_text(encoding="utf-8"))
    assert len(entries) >= 12, f"入口只剩 {len(entries)} 条 —— 这张纸不该缩成这样"
    ids = [eid for eid, _title, _body in entries]
    assert len(set(ids)) == len(ids), f"编号重复：{sorted({i for i in ids if ids.count(i) > 1})}"
    for eid, title, body in entries:
        lack = [c for c in sa.ENTRY_COLS if f"**{c}**" not in body]
        assert not lack, f"{eid} 缺列 {lack}"
        assert not sa.QUESTION_TAIL_RE.search(title), f"{eid} 的标题是疑问句不是症状：{title}"


def test_realtime_assertions_can_go_red(tmp_path):
    """⛔ 负控：四条新断言，每条喂一个**已知违规**的点进去，都必须报出来。"""
    sa = load_spec_audit()
    base = real_texts(sa)
    cases = [
        ("条目缺列", sa.mutate_realtime_drop_col),
        ("标题是疑问句", sa.mutate_realtime_question),
        ("编号重复", sa.mutate_realtime_dup_id),
        ("已标废的旧口径", sa.mutate_reinject_old_lod_realtime),
        ("指针缺", sa.mutate_fake_pointer_realtime),
    ]
    for expect, mutate in cases:
        texts = dict(base)
        mutated = mutate(base[sa.REALTIME])
        assert mutated != base[sa.REALTIME], f"负控『{expect}』是空操作（锚点已不存在）"
        texts[sa.REALTIME] = mutated
        reds = reds_of(sa, tmp_path, texts)
        assert any(expect in why for why in reds), f"负控『{expect}』没红，实到：{reds[:3]}"


def test_every_condemned_doc_is_actually_guarded(tmp_path):
    """`CONDEMNED_DOCS` 里**每一份**都要真的能红 —— 否则那个元组就是装饰。"""
    sa = load_spec_audit()
    base = real_texts(sa)
    unguarded = []
    for name in sa.CONDEMNED_DOCS:
        texts = dict(base)
        texts[name] = base[name] + "\n" + CONDEMNED_SAMPLE + "\n"
        reds = reds_of(sa, tmp_path, texts)
        if not any("已标废的旧口径" in why for why in reds):
            unguarded.append(name)
    assert not unguarded, f"这些文档在 CONDEMNED_DOCS 里，但喂了旧口径**不红**：{unguarded}"


# ───────────────────────── ③ 正控：五份真文档 ─────────────────────────


def test_five_docs_pass_the_audit(tmp_path):
    """正控：五份**真文档**跑一遍审计，必须零红（rc 口径见 `spec_audit.main`）。"""
    sa = load_spec_audit()
    reds = reds_of(sa, tmp_path, real_texts(sa))
    assert not reds, "正控有红：\n" + "\n".join(reds)

# ─────────────────── ④ 指针尺子的**边界**（历史注记 vs 悬空） ───────────────────


def test_retired_pointer_is_not_dangling_but_loses_its_pass_when_bare(tmp_path):
    """行内写明「已下线」的路径 = **历史注记**，⛔ 不算悬空指针 —— 但豁免只认那半句。

    由来（2026-10-16）：`docs/闸门与正负控表.md` · `tools/gates.py` ·
    `blender_addon/criteria.py` 三件在 2026-10-15「插件重做」里一起下线，三份文档**故意**
    留着它们的名字并注明「（2026-10-15 已随插件重做下线）」⇒ 尺子把它们报成「指针缺 3 条」，
    **是假红**。这里给豁免配一对正反控：**带那半句 ⇒ 不红；把半句删掉 ⇒ 立刻红**。
    """
    sa = load_spec_audit()
    base = real_texts(sa)
    fake = "tools/这是负控-假路径.py"

    texts = dict(base)
    texts[sa.REALTIME] = base[sa.REALTIME] + f"\n> 依据：`{fake}（2026-10-15 已随插件重做下线）`\n"
    reds = reds_of(sa, tmp_path, texts)
    assert not any("指针缺" in why for why in reds), "标了已下线还被报悬空：" + str(reds)

    texts = dict(base)
    texts[sa.REALTIME] = base[sa.REALTIME] + f"\n> 依据：`{fake}`\n"
    reds = reds_of(sa, tmp_path, texts)
    assert any("指针缺" in why for why in reds), "去掉已下线标记后**没红** —— 豁免变成什么都放行了"
