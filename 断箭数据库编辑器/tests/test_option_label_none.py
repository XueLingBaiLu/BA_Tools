"""「界面名显示 None」那条 bug 的判据（用户 2026-10-03 当场报）。

用户原话：「这个改装的图标跟游戏是反的，而且 GUI 似乎找不到这个改装，左边显示的是 None」。

根因（实测，不是推断）：游戏本地化包里那 175 条的值是 **JSON null**，而
`read_bundle_entries` 早先一句 `str(value)` 把 null 变成了**字符串 "None"** ——
界面分不清它和真译文，于是照画。
复算：`.venv/Scripts/python.exe _work/w9c_probe_loc_null.py`
（value types = {'str': 5204, 'NoneType': 175}，literal 'None' = 0）。

全库扫描：`.venv/Scripts/python.exe _work/w9c_scan_none.py` ——
非空字符串格子 22259 个，「显示 None 的格子」**1 -> 0**（唯一那格是 Options.UIName Id=3039）。

口径（Lead 定）：**null 就是缺失** —— 那 175 个键干脆不进词典；
而「词典里查不到」分两种，显示回退链必须分开判（实测 Options.UIName 上是 1 : 469）。
"""

from __future__ import annotations

import json

import pytest

from ba_mod_maker.app.viewmodel.unit_card import CardData, _option_label
from ba_mod_maker.core.assets.localization import (
    CACHE_VERSION,
    SOURCE_BUILTIN,
    Localization,
    entries_from_tables,
    load_localization,
    null_keys_from_tables,
    read_cache_table,
    write_cache,
)
from ba_mod_maker.core.data import find_dataset_root, load_dataset

DATASET_ROOT = find_dataset_root()


# --------------------------------------------------------------------------- #
# ① 精确判定：null 不进词典（不是「值像 None 就当占位符」那种启发式）
# --------------------------------------------------------------------------- #


def test_null_values_never_enter_the_dictionary() -> None:
    """值是 null 的键**不进词典** —— 既不留空串，更不许变成字符串 "None"。"""
    keys = ["dlc_3_custom_option_osv96", "ui_skinpack_desc_air_t1", "custom_option_pkp"]
    values = [None, None, "佩切涅格轻机枪(PKP)"]
    entries = entries_from_tables(keys, values)
    assert entries == {"custom_option_pkp": "佩切涅格轻机枪(PKP)"}
    assert "None" not in entries.values(), 'null 又被 str() 成 "None" 了'
    assert "" not in entries.values(), "空串是另一种含糊 —— 缺就干脆不进词典"
    assert null_keys_from_tables(keys, values) == frozenset(keys[:2])


def test_a_real_None_text_is_not_guessed_away() -> None:
    """负控的另一面：表里**真是字符串** "None" 时不许被启发式吃掉。

    口径是「null 就是缺失」这条精确判定；真有人把正文写成 "None" 那是数据的事，
    词典照收 —— 所以这条**必须绿**，它是上面那条的边界。
    """
    entries = entries_from_tables(["k"], ["None"])
    assert entries == {"k": "None"}


# --------------------------------------------------------------------------- #
# ② 旧缓存：用户盘上那份 chi.json 里实打实写着 "None"
# --------------------------------------------------------------------------- #


def _legacy_cache_payload() -> dict:
    """v1 那一版缓存的样子：entries 里就有字符串 "None"，且没有 nulls 字段。"""
    return {
        "version": 1,
        "language": "chi",
        "bundle": None,
        "count": 2,
        "entries": {"dlc_3_custom_option_osv96": "None", "ui_infocard_armor": "装甲值"},
    }


def test_a_legacy_cache_carrying_None_is_rejected(tmp_path) -> None:
    """负控（必须红）：旧缓存里带着 "None" 时，读缓存这道门要把它拦下来。

    为什么必须抬 CACHE_VERSION：不抬的话这份缓存会被原样读回来，
    修法在真机上**看不到效果**（用户盘上那份就是它）。
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "chi.json").write_text(json.dumps(_legacy_cache_payload()), encoding="utf-8")

    assert CACHE_VERSION != 1, "抬版本号是这条判据的前提"
    assert read_cache_table(cache) is None, "旧版本的缓存必须被拒"


def test_a_current_cache_still_reads_back_green(tmp_path) -> None:
    """正控（同一条尺子要能绿）：**当前版本**的缓存必须照样读得回来。

    没有这一条，「读缓存永远返回 None」也能让上面那条过 —— 那是假绿。
    """
    cache = tmp_path / "cache"
    write_cache(
        cache,
        {"custom_option_pkp": "佩切涅格轻机枪(PKP)"},
        nulls={"dlc_3_custom_option_osv96"},
    )
    table = read_cache_table(cache)
    assert table is not None
    assert dict(table.entries) == {"custom_option_pkp": "佩切涅格轻机枪(PKP)"}
    assert table.nulls == frozenset({"dlc_3_custom_option_osv96"})


def test_loading_with_only_a_legacy_cache_never_yields_None(tmp_path) -> None:
    """端到端：只有那份旧缓存、又没有游戏目录时，也不许把 "None" 交出去。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "chi.json").write_text(json.dumps(_legacy_cache_payload()), encoding="utf-8")
    loc = load_localization(tmp_path / "no-game", cache_dir=cache)
    assert loc.source == SOURCE_BUILTIN
    assert loc.text("dlc_3_custom_option_osv96") == ""


# --------------------------------------------------------------------------- #
# ③ 显示回退链：「没翻出来」与「这格是明文」必须分开判
# --------------------------------------------------------------------------- #


def test_display_name_separates_null_from_plaintext() -> None:
    """表里声明过、值是 null -> 回退；压根不在表里 -> 明文照实画。"""
    loc = Localization(
        entries={"custom_option_pkp": "佩切涅格轻机枪(PKP)"},
        nulls=frozenset({"dlc_3_custom_option_osv96"}),
    )
    assert loc.has("custom_option_pkp")
    assert loc.text("custom_option_pkp") == "佩切涅格轻机枪(PKP)"
    assert not loc.has("dlc_3_custom_option_osv96")
    assert loc.declared("dlc_3_custom_option_osv96")
    # 实测口径：DB 那格写驼峰、本地化表写全小写 —— 大小写不敏感这条要一起成立
    assert loc.declared("dlc_3_Custom_Option_OSV96")
    assert not loc.declared("Dragoon")


def test_option_3039_no_longer_renders_None() -> None:
    """用户报的那一格：_option_label(3039) 不许是 "None"，也不许是键名。"""
    if DATASET_ROOT is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    data = CardData(load_dataset(DATASET_ROOT).instance("52085"))
    label = _option_label(data, 3039)
    assert label != "None"
    assert label != "dlc_3_Custom_Option_OSV96", "键名是机器名，不能当文案"
    assert label.strip() != ""


def test_no_string_cell_in_the_whole_library_renders_as_None() -> None:
    """全库扫描（1 -> 0 那条）：24 张表的每个字符串格子都不许显示成 "None"。"""
    if DATASET_ROOT is None:
        pytest.skip("未找到 DataBaseCompiled 数据源")
    loc = load_localization()
    if loc.source == SOURCE_BUILTIN:
        pytest.skip("这台机器读不到游戏本地化包，扫不出这条")
    dataset = load_dataset(DATASET_ROOT)
    instance = dataset.instance(dataset.default_instance_id)
    hits: list[str] = []
    cells = 0
    for table_name in sorted(instance.table_names):
        table = instance.table(table_name)
        columns = tuple(table.columns)
        for row in table.rows:
            for index, column in enumerate(columns):
                if index >= len(row):
                    break
                value = row[index]
                if not isinstance(value, str) or not value.strip():
                    continue
                cells += 1
                if (loc.text(value) or value).strip().lower() == "none":
                    hits.append(f"{table_name}.{column} raw={value!r}")
    assert cells > 20000, f"扫到的格子太少（{cells}），这条尺子没在工作"
    assert hits == [], f"这些格子会显示成 None：{hits[:5]}"
