r"""名字表缓存的**大小闸**（旧家 `importer.read_unit_hud_names_cache` ⇒ 新家 `unit_names.py`）。

## 这条规矩是什么（照抄旧用例，⛔ 不是我新编的）

旧 `importer.read_unit_hud_names_cache()` 会对**任何**路径先做 `Path.read_text()`。
而名字表的**第 1 号候选是 7.12 GB 的 `BrokenArrow_Data/data.unity3d`** ——
不加闸就是「把 7.12 GB 读进内存再解 UTF-8」。

## 现场读数（可复算）

`blender --background --factory-startup --python _work/blender-test/a8_breakdown.py`
-> `PHASE_OPEN 1.21 / PHASE_DATASET 0.04 / PHASE_TABLE 0.0` **TOTAL 1.26**；
同一个包走「名字表」那条链时 `SECONDS 8.82` ⇒ **多出来的 7.5 s 全在那一行 read_text 上**。

## 新家（2026-10-15 移植）

`core/asset/unit_names.py::load_name_table` —— 形态与旧家**逐字同款**（给任何路径就 `read_text`），
所以闸跟着搬：`CACHE_MAX_BYTES = 8 * 1024 * 1024`（口径照旧家 `importer.py:407`：严格大于才拒），
判在 `read_text` **之前**（一次 `stat` 就能回答「这不是缓存」）。
现在有三条路能把一个超大路径喂到它嘴里：`names status --cache <容器>` /
`names pairs --cache <容器>` / 插件 `panels_take.read_names` 之后的任何显式路径。

## 判据一行

`pytest tests/test_name_cache_guard.py -q -n 0` -> **6 passed**；
* **正控**：7.12 GB（稀疏文件）喂进去 ⇒ `Path.read_text` 被调 **0 次**
  （⛔ 不是「读了之后返回 None」—— 那种一样把内存吃光）；
* **负控（尺子能红）**：monkeypatch `unit_names.CACHE_MAX_BYTES = 1` ⇒
  一份**合法**的小缓存必须被拒 —— 这条红了才证明上面量的是**那道闸**，不是别的。

⛔ 本文件**不读游戏盘**：超限件是 **NTFS 稀疏文件**（`seek(size-1)` ＋ 写 1 字节，
毫秒级、不真占盘、`st_size` 照样是那个数）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ba_mod_maker.core.asset import unit_names as un

#: 真件读数：游戏容器 `BrokenArrow_Data/data.unity3d` 的字节数（kb/20 跳 1 实读）。
CONTAINER_BYTES = 7_122_751_635


def _sparse(path: Path, size: int) -> Path:
    """造一个 `st_size == size` 的**稀疏**文件（毫秒级，⛔ 不真写 size 个字节）。"""
    with path.open("wb") as handle:
        handle.seek(size - 1)
        handle.write(b"x")
    return path


def _good_payload() -> dict:
    """一份**合法**缓存的最小形状（`format` 对 ＋ `units` 是 dict）。"""
    return {
        "format": un.CACHE_FORMAT,
        "built_at": "2026-10-15T00:00:00",
        "source": {"path": "x", "size": 1, "mtime_ns": 1},
        "counts": {"units_rows": 1, "model_keys": 1},
        "units": {"RU_T72B": "T-72B"},
        "turrets": {},
        "pairs": {},
    }


def _read_spy() -> tuple[list[str], object]:
    """计次间谍：只记账，**不真读**（这样闸没了也不会把内存吃光，只会让断言红）。

    ⚠ 必须是**普通函数**（不是可调用对象）—— `Path.read_text` 是实例方法，
    只有描述符协议才会把 `self` 绑进来；可调用对象绑不了 ⇒ 闸一没就变成
    `TypeError: missing 1 required positional argument`，红了也看不清是哪条规矩。
    """
    calls: list[str] = []

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        calls.append(str(self))
        return ""

    return calls, spy


# --------------------------------------------------------------------------- #
# 1 · 闸本身：常量 ≪ 容器
# --------------------------------------------------------------------------- #
def test_the_cap_is_smaller_than_the_game_container() -> None:
    """8 MiB 的上限必须远小于 7.12 GB 的容器 —— 这条就是「闸存在」本身。"""
    assert un.CACHE_MAX_BYTES == 8 * 1024 * 1024
    assert un.CACHE_MAX_BYTES < CONTAINER_BYTES


def test_the_cap_covers_the_real_cache_with_room_to_spare() -> None:
    """闸不能把**真缓存**也挡掉：实读那份 183,594 B ⇒ 上限至少是它的 4 倍。"""
    real_cache_bytes = 183_594
    assert real_cache_bytes < un.CACHE_MAX_BYTES // 4


# --------------------------------------------------------------------------- #
# 2 · 正控：超限件必须在 read_text **之前**被挡掉
# --------------------------------------------------------------------------- #
def test_an_oversized_file_is_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正控：超限文件必须在 `read_text` **之前**就被挡掉（一次都不许读）。"""
    big = _sparse(tmp_path / "huge.json", un.CACHE_MAX_BYTES + 1)
    assert big.stat().st_size > un.CACHE_MAX_BYTES
    calls, spy = _read_spy()
    monkeypatch.setattr(Path, "read_text", spy)
    assert un.load_name_table(big) is None
    assert calls == [], f"超限文件居然被读了：{calls[:2]}"


def test_the_game_container_itself_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """这条才是原案的**形状**：把 `data.unity3d` 那个路径喂进去 ⇒ 一个字节都不许读。

    7.12 GB 用**稀疏**文件造（不真占盘）；间谍不真读 ⇒ 就算哪一天闸没了，
    这条也只是**红**，不会把机器拖死。
    """
    container = _sparse(tmp_path / "data.unity3d", CONTAINER_BYTES)
    calls, spy = _read_spy()
    monkeypatch.setattr(Path, "read_text", spy)
    assert un.load_name_table(container) is None
    assert calls == [], f"7.12 GB 的容器居然被读了：{calls[:2]}"


# --------------------------------------------------------------------------- #
# 3 · 负控（尺子能红）：把闸收紧，合法缓存必须被拒
# --------------------------------------------------------------------------- #
def test_negative_control_a_tightened_cap_refuses_a_legal_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 负控：闸一收紧，**合法**缓存必须被拒 —— 证明上面两条量的是那道闸。

    `CACHE_MAX_BYTES = 1` 是「故意违反」：一份几百字节的真缓存比它大 ⇒ 必须 `None`。
    这条如果还是绿的，说明 `load_name_table` 根本没在判大小（上面那两条就都是假绿）。
    """
    good = un.save_name_table(_good_payload(), tmp_path / "units_names.json")
    assert good.stat().st_size > 1, "夹具坏了：合法缓存比 1 字节还小"
    assert un.load_name_table(good) is not None, "正控：这份缓存本来就该被接受"

    monkeypatch.setattr(un, "CACHE_MAX_BYTES", 1)
    assert un.load_name_table(good) is None, "闸收紧了却照样读 —— 那道闸没在起作用"


# --------------------------------------------------------------------------- #
# 4 · 大小闸不是唯一判据：小件也要按形状拒
# --------------------------------------------------------------------------- #
def test_a_small_foreign_json_is_still_refused(tmp_path: Path) -> None:
    """小文件也要按形状拒（`DataBaseCompiled.json` 也是 .json，而且不大）。"""
    other = tmp_path / "DataBaseCompiled.json"
    other.write_text('{"Units": [1, 2, 3]}', encoding="utf-8")
    assert other.stat().st_size < un.CACHE_MAX_BYTES
    assert un.load_name_table(other) is None


def test_a_wrong_version_is_refused(tmp_path: Path) -> None:
    """版本不对 ⇒ 当没有（缓存格式一变就得重读，⛔ 不许拿旧版凑合）。"""
    stale = tmp_path / "units_names.json"
    payload = dict(_good_payload(), format=un.CACHE_FORMAT + 1)
    stale.write_text(json.dumps(payload), encoding="utf-8")
    assert un.load_name_table(stale) is None
