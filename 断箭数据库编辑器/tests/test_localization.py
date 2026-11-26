"""本地化读取器的测试（零 Qt）。

这个模块踩过三个坑，每个都值得一条用例：

1. m_Script 取出来可能是 **str**（UnityPy 对非 UTF-8 字节做了 surrogateescape 解码）
   —— 那时 str.encode("utf-8") 会炸，必须先还原字节；
2. 包名带 hash，只能 glob，不能写死文件名；
3. 数据库里的 UIName 是**驼峰**（Custom_Slot_Squad_loadout），而本地化键全小写
   —— 查表必须大小写不敏感，否则改型组的名字永远显示成英文键。

读真实 bundle 的那条标了 slow（与仓里其它读游戏文件的用例同一约定）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.core.assets.game import find_game_root
from ba_mod_maker.core.assets.localization import (
    BUILTIN_LABELS,
    CACHE_VERSION,
    SOURCE_BUILTIN,
    SOURCE_CACHE,
    Localization,
    decode_script,
    find_localization_bundle,
    load_localization,
    read_cache,
    write_cache,
)

# --------------------------------------------------------------------------- #
# m_Script 的两种形态
# --------------------------------------------------------------------------- #


def test_decode_script_accepts_bytes() -> None:
    raw = '["chi", "应用"]'.encode()
    assert decode_script(raw) == ["chi", "应用"]


def test_decode_script_accepts_a_str_with_surrogates() -> None:
    """**这个坑的准确形状**（实测过，别照抄旧说法）：

    UnityPy 对非 UTF-8 字节会 decode(errors=surrogateescape) 出一个带代理字符的 str。
    - json.loads(那个 str) **其实能过**（不是这里炸）；
    - 炸的是 str.encode(utf-8)：代理字符不允许编码 -> UnicodeEncodeError。

    所以 decode_script 反过来先 encode(utf-8, surrogateescape) 还原字节。
    本版游戏包里那串字节本来就是合法 UTF-8，两种形态都读得出来。
    """

    raw = '["chi", "应用"]'.encode()
    lossy = raw.decode("utf-8", "surrogateescape")
    assert decode_script(lossy) == ["chi", "应用"]

    # 真的带代理字符时（字节不是 UTF-8）：严格 encode 会炸，surrogateescape 不会
    broken = b'["chi", ' + bytes([0xFF]) + b'"]'
    broken = broken.decode("utf-8", "surrogateescape")
    with pytest.raises(UnicodeEncodeError):
        broken.encode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        decode_script(broken)  # 那串字节本来就不是 UTF-8：宁可炸，也不要静默给坏文本


def test_decode_script_rejects_other_types() -> None:
    with pytest.raises(ValueError):
        decode_script(12345)


# --------------------------------------------------------------------------- #
# 查表：label / hint / 大小写 / 枚举
# --------------------------------------------------------------------------- #


def test_label_takes_the_first_line_and_hint_the_rest() -> None:
    """游戏 tooltip 文案本身就是「标题 + 换行 + 说明」两段。"""
    text = "装甲值" + chr(10) + "当装甲值高于装甲穿透时, 受到的伤害将降低."
    loc = Localization(entries={"ui_infocard_armor": text})
    assert loc.label("ui_infocard_armor") == "装甲值"
    assert loc.hint("ui_infocard_armor").startswith("当装甲值高于")
    assert loc.text("ui_infocard_armor").startswith("装甲值")


def test_lookup_is_case_insensitive() -> None:
    """数据库里写 Custom_Slot_Squad_loadout，键是全小写的 custom_slot_squad_loadout。"""
    loc = Localization(entries={"custom_slot_squad_loadout": "小队装备配置"})
    assert loc.text("Custom_Slot_Squad_loadout") == "小队装备配置"
    assert loc.has("CUSTOM_SLOT_SQUAD_LOADOUT")
    assert loc.text("不存在的键", "兜底") == "兜底"


def test_enum_key_rule() -> None:
    loc = Localization(
        entries={"ui_enum_weapontype_rifle": "步枪", "ui_enum_trajectory_direct": "直射弹药"}
    )
    assert loc.enum("weapontype", "rifle") == "步枪"
    # ❗ 前缀不是类型名的小写：TrajectoryType 的前缀是 trajectory
    assert loc.enum("trajectory", "direct") == "直射弹药"
    assert loc.enum("weapontype", "none", "—") == "—"


def test_builtin_fallback_says_it_is_degraded() -> None:
    loc = Localization(entries=BUILTIN_LABELS, source=SOURCE_BUILTIN)
    assert loc.is_degraded
    assert "降级" in loc.source_label
    assert loc.label("ui_infocard_optics") == "视野"
    assert loc.label("ui_infocard_squad_count") == "小队成员数量"
    assert loc.enum("weapontype", "rpg") == "火箭弹发射器"


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #


def test_cache_round_trip(tmp_path: Path) -> None:
    entries = {"keys": "x", "a": "甲", "b": "乙"}
    bundle = tmp_path / "localization_assets_all_deadbeef.bundle"
    bundle.write_bytes(b"not really a bundle")
    assert write_cache(tmp_path / "cache", entries, bundle=bundle) is not None
    assert read_cache(tmp_path / "cache", bundle=bundle) == entries


def test_cache_is_invalidated_when_the_bundle_changes(tmp_path: Path) -> None:
    """游戏更新后包换了（名字/大小/mtime），旧缓存必须作废 —— 否则中文会串到新数据上。"""
    entries = {"keys": "x"}
    bundle = tmp_path / "localization_assets_all_aaaa.bundle"
    bundle.write_bytes(b"v1")
    write_cache(tmp_path / "cache", entries, bundle=bundle)
    changed = tmp_path / "localization_assets_all_bbbb.bundle"
    changed.write_bytes(b"v2")
    assert read_cache(tmp_path / "cache", bundle=changed) is None
    # 不带 bundle 校验时仍读得出来（降级路径要用它）
    assert read_cache(tmp_path / "cache") == entries


def test_cache_format_version_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "cache"
    path.mkdir()
    payload = {"version": CACHE_VERSION + 1, "language": "chi", "entries": {"a": "b"}}
    (path / "chi.json").write_text(json.dumps(payload), encoding="utf-8")
    assert read_cache(path) is None


# --------------------------------------------------------------------------- #
# 降级：没有游戏也要能用，而且要说出来
# --------------------------------------------------------------------------- #


def test_missing_game_degrades_to_builtin_labels(tmp_path: Path) -> None:
    """找不到游戏 -> 内置中文 + is_degraded。**绝不抛异常**：界面必须照常能用。"""
    loc = load_localization(tmp_path / "no-game", cache_dir=tmp_path / "cache")
    assert loc.source == SOURCE_BUILTIN
    assert loc.is_degraded
    assert loc.note  # 必须说清为什么
    assert loc.count > 20


def test_stale_cache_is_used_when_the_game_is_gone(tmp_path: Path) -> None:
    """游戏挪走了、但盘上还有上次的缓存：用它，并在 note 里说明。"""
    cache = tmp_path / "cache"
    write_cache(cache, {"k": "旧的中文"})
    loc = load_localization(tmp_path / "no-game", cache_dir=cache)
    assert loc.source == SOURCE_CACHE
    assert loc.text("k") == "旧的中文"
    assert "缓存" in loc.note


def test_find_bundle_returns_none_without_a_game(tmp_path: Path) -> None:
    assert find_localization_bundle(tmp_path / "nope") is None


# --------------------------------------------------------------------------- #
# 真读游戏包（slow）
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_reads_the_real_game_bundle(tmp_path: Path) -> None:
    """读真实 localization bundle：截图里用到的那些词一个不少。"""
    game = find_game_root()
    if game is None:
        pytest.skip("本机没装游戏")
    loc = load_localization(game, cache_dir=tmp_path / "cache")
    assert loc.count > 5000
    assert not loc.is_degraded
    for key, want in (
        ("ui_infocard_armor", "装甲值"),
        ("ui_infocard_optics", "视野"),
        ("ui_infocard_squad_count", "小队成员数量"),
        ("custom_slot_squad_loadout", "小队装备配置"),
        ("ui_enum_weapontype_rpg", "火箭弹发射器"),
    ):
        assert loc.label(key) == want, f"{key} = {loc.text(key)!r}"
    # 第二次走缓存，仍然是对的
    again = load_localization(game, cache_dir=tmp_path / "cache")
    assert again.source == SOURCE_CACHE
    assert again.count == loc.count
