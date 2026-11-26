"""tools/decomp_index.py 的用例 —— **全部在临时夹具上跑**，不依赖 1 GB 的真索引。

⚠ 这一条是 2026-10-11 那条坑的正面版：**用例只要依赖语料，文档一引用它就失效**
（kb/29 第四节「第四次踩同一个坑」）。所以这里 build 一份两行的假 dump.cs ＋ 一个假 ISIL 目录，
正控/负控都在这份夹具上断 —— 真索引在不在都不影响本文件的 rc。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import decomp_index as di  # noqa: E402

DUMP = (
    "// Namespace: Fixture.Lods\n"
    "public class FixtureConfig : ScriptableObject // TypeDefIndex: 126\n"
    "{\n"
    "\t// Fields\n"
    "\tpublic float Lod0; // 0x18\n"
    "\tpublic float Lod1; // 0x1C\n"
    "\n"
    "\t// Methods\n"
    "\n"
    "\t// RVA: 0x30D90 Offset: 0x30190 VA: 0x180030D90\n"
    "\tpublic void .ctor() { }\n"
    "}\n"
    "\n"
    "// Namespace: Fixture.Other\n"
    "public class Background4721 // TypeDefIndex: 9\n"
    "{\n"
    "\tpublic int Field; // 0x10\n"
    "}\n"
)


@pytest.fixture()
def idx(tmp_path):
    (tmp_path / "dump.cs").write_text(DUMP, encoding="utf-8")
    isil = tmp_path / "IsilDump" / "Fixture" / "Lods"
    isil.mkdir(parents=True)
    (isil / "FixtureConfig.txt").write_text(
        ";; Type: Fixture.Lods.FixtureConfig\n;; 12 instructions\n", encoding="latin-1")
    out = tmp_path / "idx"
    assert di.build(tmp_path / "dump.cs", tmp_path / "IsilDump", out) == 0
    return out


def test_build_writes_the_three_tables(idx):
    assert (idx / di.TYPES_TSV).is_file()
    assert (idx / di.MEMBERS_TSV).is_file()
    assert (idx / di.FILES_TSV).is_file()


def test_hits_all_three_sections(idx):
    r = di.query("FixtureConfig", idx)
    assert r["types"] and r["types"][0]["ns"] == "Fixture.Lods"
    assert r["files"], "ISIL 那一节必须命中"
    assert r["files"][0]["full"] == "Fixture.Lods.FixtureConfig", "要印出 ;; Type: 里的全名"
    assert r["files"][0]["bytes"] > 0, "字节数是判据：几十 KB 的才读得动"
    assert any(m["full"] == "FixtureConfig.Lod0" for m in r["members"])


def test_exact_name_outranks_substring(idx):
    """Background4721 里含 Background；查整串时不许让子串命中排在真名前面。"""
    r = di.query("Background4721", idx)
    assert r["types"], "整串应当命中"
    assert r["types"][0]["name"] == "Background4721"


def test_whole_needle_first_then_camel_fallback(idx):
    """整串 0 命中才退到驼峰词 —— 这是 2026-10-10 那次噪声的根因。"""
    r = di.query("FixtureConfig", idx)
    assert r["used_fallback"] is False
    assert r["tokens"] == ["FixtureConfig"]
    r2 = di.query("FixtureConfigLod0", idx)          # 整串不存在 ⇒ 走退路
    assert r2["used_fallback"] is True
    assert r2["members"], "退到驼峰词之后应当能命中 FixtureConfig.Lod0"


def test_negative_control_is_zero_and_not_discouraging(idx):
    r = di.query("ZqnaWobbleXyz999", idx)
    assert not (r["types"] or r["members"] or r["files"])
    txt = di.render(r, idx)
    for word in ("没写过", "不存在", "知识库里没有"):
        assert word not in txt, f"负控印了劝退话术：{word}"
    assert "不能证明反编译里没有" in txt


def test_hit_output_says_locating_not_conclusion(idx):
    """命中时要说清这是**定位**、不是结论 —— 两个库的分工要写在输出里，别只写在文档里。"""
    txt = di.render(di.query("FixtureConfig", idx), idx)
    assert "定位" in txt


def test_selftest_is_green():
    assert di.selftest() == 0
