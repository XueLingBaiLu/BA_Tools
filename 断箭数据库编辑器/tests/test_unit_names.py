"""A8 名字表（`core/asset/unit_names.py`）的机械判据。

## 这条链是什么

用户 2026-10-12 选定：名字表读**游戏本体** `<游戏根>/BrokenArrow_Data/data.unity3d`
（⛔ 不是 zip —— 原话「你别用这种压缩包文件当名字表！」）。

Blender 自带 Python **读不了容器**（实测 Blender 5.2.1 / CPython 3.13.13：`HAS_CRYPTO False`；
容器里 24 张表全是 AES-256-CBC）⇒
    产品（有 Crypto，**实测读一次 1.32~1.47 s**）--names export--> 183,594 B 明文 JSON --> 插件。

## 判据一行

`pytest tests/test_unit_names.py -q` -> 通过数 从 0 收集变 **N passed**（下表的用例数）；
真件那条（`-m slow`）另跑：**542** 行 Units -> 335 个模型键 / 同名多行 12 个（读数见下面那条用例）。
⚠ 那几个数是**真件读数**、会随游戏更新变 —— 2026-10-13 那次 Steam 更新（buildid 25903544）
把 Units 540→542、Turrets 1055→1057，多出来的是 Id 707 `Mech. Riflemen MAAWS` 与
Id 708 `Mech. Engineers CQC`（都复用 `US_Rangers` 模型，故 `model_keys` 不变）。
⇒ 这条红了**先对游戏版本**（`<游戏>/GameLogs/Gamelog__*.log` 开头第 3 行就是版本号），别急着改代码。

⛔ 本文件默认档**不读游戏盘**（真件那条挂了 `slow`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import unit_names as un

GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")


# --------------------------------------------------------------------------- #
# 1 · 取名口径（与插件侧 _names_from_pairs 同一条：第一行赢 + 等 N 个）
# --------------------------------------------------------------------------- #
def test_first_row_wins_and_multi_row_is_labelled() -> None:
    rows = [
        {"ModelFileName": "US_Rangers", "HUDName": "Mech. Riflemen"},
        {"ModelFileName": "US_Rangers", "HUDName": "Troopers AA"},
        {"ModelFileName": "US_Rangers", "HUDName": "Spetsnaz GRU Test"},
        {"ModelFileName": "RU_T72B", "HUDName": "T-72B"},
    ]
    names = un._pairs_by_key(rows, "ModelFileName", ("HUDName", "Name"))
    assert names["US_Rangers"] == "Mech. Riflemen 等 3 个", names["US_Rangers"]
    # 单行键**逐字**就是游戏里的名字 —— ⛔ 不许给它加后缀
    assert names["RU_T72B"] == "T-72B"


def test_empty_key_rows_are_skipped() -> None:
    names = un._pairs_by_key(
        [{"ModelFileName": "", "HUDName": "x"}, {"ModelFileName": None, "HUDName": "y"}],
        "ModelFileName",
        ("HUDName", "Name"),
    )
    assert names == {}


def test_name_falls_back_to_the_second_column() -> None:
    rows = [{"ModelFileName": "K", "HUDName": "", "Name": "備"}]
    assert un._pairs_by_key(rows, "ModelFileName", ("HUDName", "Name")) == {"K": "備"}


# --------------------------------------------------------------------------- #
# 2 · 缓存形状与失效（指纹 = size + mtime_ns）
# --------------------------------------------------------------------------- #
def _payload(tmp_path: Path, *, size: int, mtime_ns: int) -> dict:
    return {
        "format": un.CACHE_FORMAT,
        "built_at": "2026-10-12T01:00:00+0800",
        "source": {"path": "X", "size": size, "mtime_ns": mtime_ns},
        "counts": {"units_rows": 1, "model_keys": 1},
        "units": {"RU_T72B": "T-72B"},
        "turrets": {},
        "pairs": {},
    }


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "u.json"
    un.save_name_table(_payload(tmp_path, size=1, mtime_ns=2), out)
    got = un.load_name_table(out)
    assert got is not None
    assert got["units"] == {"RU_T72B": "T-72B"}
    assert not (tmp_path / "u.json.tmp").exists(), "临时文件必须被 replace 掉"


def test_load_refuses_foreign_or_broken_files(tmp_path: Path) -> None:
    broken = tmp_path / "b.json"
    broken.write_text("{not json", encoding="utf-8")
    assert un.load_name_table(broken) is None
    other = tmp_path / "DataBaseCompiled.json"
    other.write_text(json.dumps({"Units": [1, 2, 3]}), encoding="utf-8")
    assert un.load_name_table(other) is None, "不是我们的形状就必须 None（⛔ 不许当缓存用）"
    wrong = tmp_path / "w.json"
    wrong.write_text(json.dumps({"format": 999, "units": {}}), encoding="utf-8")
    assert un.load_name_table(wrong) is None, "版本不对必须当没有"
    assert un.load_name_table(tmp_path / "nope.json") is None


def test_display_name_never_invents() -> None:
    payload = _payload(Path("."), size=1, mtime_ns=2)
    assert un.display_name(payload, "RU_T72B") == "T-72B"
    assert un.display_name(payload, "NOT_THERE") == ""
    assert un.display_name(None, "RU_T72B") == ""


def test_is_fresh_follows_the_source_fingerprint(tmp_path: Path) -> None:
    """正控：把指纹改一个数，`is_fresh` 必须变 False（能红才算尺子）。"""
    fake_game = tmp_path / "game"
    data = fake_game / "BrokenArrow_Data"
    data.mkdir(parents=True)
    container = data / "data.unity3d"
    container.write_bytes(b"x" * 10)
    good = {"source": un.fingerprint_of(container), "units": {}}
    assert un.is_fresh(good, fake_game) is True
    stale = {"source": dict(good["source"], size=11), "units": {}}
    assert un.is_fresh(stale, fake_game) is False
    assert un.is_fresh(None, fake_game) is False


def test_default_cache_path_is_not_in_the_game_directory() -> None:
    """⛔ 缓存不许落游戏目录（往游戏目录塞文件会触发「改过游戏目录就绝不联机」）。"""
    text = str(un.default_cache_path()).lower()
    assert "steamapps" not in text
    assert text.endswith(un.CACHE_NAME)


# --------------------------------------------------------------------------- #
# 3 · 真件（挂 slow：默认档不读游戏盘）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.skipif(not un.source_file(GAME).is_file(), reason="本机没有 data.unity3d")
def test_real_container_readings() -> None:
    payload = un.build_name_table(GAME)
    counts = payload["counts"]
    assert counts["units_rows"] == 542, counts
    assert counts["model_keys"] == 335, counts
    assert counts["multi_row_keys"] == 12, counts
    assert counts["turret_rows"] == 1057, counts
    assert counts["turret_keys"] == 671, counts
    assert payload["units"]["RU_T72B"] == "T-72B"
    assert payload["units"]["US_Rangers"] == "Mech. Riflemen 等 196 个"
    # T-72B 挂 3 条**有独立模型**的炮塔（kb/24 口径：7 条挂点里 3 条有模型）
    pairs = payload["pairs"]["RU_T72B"]
    assert [p["turret"] for p in pairs] == [
        "RU_T72B/RU_T72B",
        "RU_T72B/RU_T72BA",
        "RU_T72B/RU_T72B",
    ], pairs
    assert [p["turret_id"] for p in pairs] == [8, 464, 703], pairs
