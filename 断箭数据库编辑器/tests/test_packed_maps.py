r"""**图打包在 .blend 里**也能用 —— 真 Blender 判据（task-8）。

**判据一行**：`pytest tests/test_packed_maps.py -q -n 0` -> 通过数 **从 0 变 11**
（本文件新建前 = 0 收集）。

## 用户目标

原话：「我的源的话，.blend 文件里可以包含浮雕、金属度、光滑度的文件吗？也就是说把模型中的
所有东西，都集成进一个 .blend 文件」。今天之前的缺口：`materials._load_user_image` 只吃
磁盘路径 ⇒ 图被打包、盘上原文件搬走之后就用不了。

## 量什么（真 Blender 里，不是读数行）

| 判据 | 通过线 | 出处 |
|---|---|---|
| ① 打包图能用 | `pack()` 之后**删掉盘上文件** ⇒ 仍接上，读数「从 .blend 里打包的图」 | `packed.*` |
| ② 两边都没有 | `ValueError` **点名两处**（磁盘 ＋ 打包），且节点树没动 | `neither.*` |
| ③ 回归 | 盘上文件在、没打包 ⇒ 读数与今天**逐字段相同**（`从磁盘读的`） | `disk.*` |
| ④ 自动接 | 抽图目录**空** / **整个不在** ⇒ 用同名打包图 | `auto_packed.*` |
| ⑤ 不猜 | 多张打包图候选又对不上单位键 ⇒ 明写「分不清」 | `packed_pick.*` |

## 正控（这把尺子能红）

* `packed_pick.keyed` 必须是**单位键对上**那张 —— 场景里同时有 `RU_OTHER_*`（字母序在
  `RU_FAKE` 前面），按字母序挑就会撞上它；
* `neither.raised == "ValueError"` 且 `touched is False` —— 报红不是恒真；
* `no_packed.paths == {}` —— 「没有打包图时行为不变」这条得先量出来，否则后面的绿没意义。

## 怎么跑

起一次无头 Blender 跑 `tests/probe_packed_maps.py`（**不开任何游戏包**，几秒），
再对探针打出来的 JSON 逐条断言。Blender 不在 -> skip（⛔ 不是「绿」）。

## 未做（如实）

* **不开游戏包**：这里量的是「图从哪来」这一跳；真件（RU_T72B）读数由
  `tests/test_vt_auto.py -m slow` 与 `tests/test_import_unit.py` 守；
* **没有真存过 .blend 再打开**：本探针在**同一个进程**里 `pack()` 之后删掉盘上原文件 ——
  这覆盖了「盘上文件没了，图还在 .blend 里」的全部机理（`packed_file`），但**没覆盖**
  「关掉 Blender 再打开那个 .blend」那一跳（那要真存盘，且属于「打开文件」的活）；
* **判不了观感**：遮蔽 / 倒影 / 条纹三类只有用户能判（AGENTS §0.12.1）。
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
from pathlib import Path

import pytest


def _retired(why: str):
    """如实 skip ＋ 点名为什么（与 tests/test_gui_rules.py 同一写法，⛔ 不硬凑假绿）。"""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*_args, **_kwargs):
            pytest.skip(why)
        return inner

    return wrap


#: 2026-10-18：VT 自动接那套设计在 2026-10-17 18:37 被重写（按材质 stacks ＋ 缓存抽取），
#: 本文件 F/G/J 三段量的是**旧行为** ⇒ 对应用例在这里如实退役。
#: 新设计的判据：`tests/test_vt_auto_bind.py`。
_RETIRED_VT = (
    "2026-10-17 重写了 VT 自动接法（按每颗材质的 (group,item) 从游戏原文件现抽 + 用户缓存），"
    "本用例量的「抽图目录空/不在时改用打包图」那套设计已不存在；新判据见 tests/test_vt_auto_bind.py"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).resolve().parent / "probe_packed_maps.py"
DEFAULT_BLENDER = Path(r"D:\Blender\blender.exe")

#: 探针里那几个名字（⛔ 与 probe 里逐字相同；两处漂了这个文件会当场红）。
UNIT_KEY = "RU_FAKE"
OTHER_KEY = "RU_OTHER"
PACKED_PREFIX = "BAMOD打包"
DISK_LABEL = "从磁盘读的"
PACKED_LABEL = "从 .blend 里打包的图"


def _blender() -> Path | None:
    """本机的 blender.exe（`BA_BLENDER` / `BAMOD_BLENDER` 可覆盖）；没有 -> None。"""
    override = os.environ.get("BA_BLENDER") or os.environ.get("BAMOD_BLENDER")
    if override and Path(override).is_file():
        return Path(override)
    return DEFAULT_BLENDER if DEFAULT_BLENDER.is_file() else None


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """一趟 Blender 的读数（整个模块共用一次；⛔ 不开游戏包）。"""
    blender = _blender()
    if blender is None:
        pytest.skip(f"没找到 {DEFAULT_BLENDER}（BA_BLENDER 可指到别处）")
    work = tmp_path_factory.mktemp("packed-maps")
    proc = subprocess.run(
        [
            str(blender), "-b", "--factory-startup",
            "--python", str(PROBE), "--", "--work", str(work),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=900, check=False,
    )
    assert proc.returncode == 0, f"Blender 退出码 {proc.returncode}\n{(proc.stderr or "")[-3000:]}"
    match = re.search(r"^BAMOD_PACKED_BEGIN$(.*?)^BAMOD_PACKED_END$", proc.stdout, re.M | re.S)
    assert match, f"探针没打 JSON 块\n{(proc.stdout or "")[-3000:]}"
    data = json.loads(match.group(1))
    assert data.get("probe_rc") == 0, str(data.get("error"))[-3000:]
    return data


# --------------------------------------------------------------------------- #
# 正控：没有打包图时，旧行为必须先量出来（否则后面的绿不算数）
# --------------------------------------------------------------------------- #
def test_no_packed_images_baseline_is_unchanged(facts: dict) -> None:
    """一张打包图都没有时：`game_map_files` 给空 ＋ note 仍然点到两个后缀。"""
    block = facts["no_packed"]
    assert block["packed_images"] == 0, block
    assert block["paths"] == {}, block
    assert block["note_names_globs"] is True, block["note"]


def test_neither_place_reports_red_and_names_both(facts: dict) -> None:
    """两边都没有 ⇒ `ValueError`，消息**把两处都点名**（⛔ 不静默、⛔ 不拿占位图冒充）。"""
    block = facts["neither"]
    assert block["raised"] == "ValueError", block
    assert block["touched"] is False, block
    assert block["packed_probe"] is True, "同名打包图本该找不到"
    message = block["message"]
    assert "找不到这张图" in message, message
    assert block["asked"] in message, message
    assert "磁盘上" in message and "打包" in message, message
    # 找过哪两个名字（带扩展名 / 不带）也要写在里面
    assert "BAMOD_not_here_normal.png" in message, message
    assert "BAMOD_not_here_normal " not in message


# --------------------------------------------------------------------------- #
# ③ 回归：盘上文件在、没打包 ⇒ 与今天逐字段相同
# --------------------------------------------------------------------------- #
def test_disk_route_is_unchanged(facts: dict) -> None:
    """磁盘那一版读数：来源「从磁盘读的」、记的是**路径**、8x8 Non-Color、链子照旧。"""
    block = facts["disk"]
    assert block["labels"] == [DISK_LABEL, DISK_LABEL], block["labels"]
    assert block["state_labels"]["normal"].startswith(DISK_LABEL), block["state_labels"]
    assert block["sources"]["normal"].endswith("BAMOD_disk_normal.png"), block["sources"]
    assert block["sources"]["mask"].endswith("BAMOD_disk_mask.png"), block["sources"]
    assert block["colorspaces"] == ["Non-Color", "Non-Color"], block["colorspaces"]
    assert block["sizes"] == [[8, 8], [8, 8]], block["sizes"]
    assert block["packed_flags"] == {
        "base_color": False, "normal": False, "mask": False,
    }, block["packed_flags"]
    # ⛔ 旧读数那两段的**原文**必须还在（今天那批用例锁的就是它们）
    normal_line = block["lines"][1]
    assert "法线 ← BAMOD_disk_normal.png（8x8 · Non-Color" in normal_line, normal_line
    assert "TexImage -> NormalMap(Strength=1) -> Principled.Normal" in normal_line, normal_line
    mask_line = block["lines"][2]
    assert "金属度 ← Mask.R · 粗糙度 ← Mask.A · AO ← Mask.G" in mask_line, mask_line


# --------------------------------------------------------------------------- #
# ① 打包图 ＋ 盘上文件删掉 ⇒ 仍能接上，读数点名来源
# --------------------------------------------------------------------------- #
def test_packed_route_works_after_the_disk_file_is_deleted(facts: dict) -> None:
    """★ 本卡的核心：盘上那两张文件**删掉**了，图还在 .blend 里 ⇒ 照样接上。"""
    block = facts["packed"]
    assert block["disk_gone"] == [True, True], block
    assert block["packed_before"] == [True, True], block["packed_before"]
    assert block["packed_after"]["normal"] is True and block["packed_after"]["mask"] is True, block
    assert block["labels"] == [PACKED_LABEL, PACKED_LABEL], block["labels"]
    assert block["images"] == ["BAMOD_gone_normal.png", "BAMOD_gone_mask.png"], block["images"]
    assert block["normal"] == "BAMOD_gone_normal.png", block["normal"]
    assert block["mask"] == "BAMOD_gone_mask.png", block["mask"]
    assert block["colorspaces"] == ["Non-Color", "Non-Color"], block["colorspaces"]
    assert block["sources"]["normal"].startswith(PACKED_PREFIX), block["sources"]
    assert block["state_labels"]["mask"].startswith(PACKED_LABEL), block["state_labels"]
    assert any(PACKED_LABEL in line for line in block["lines"]), block["lines"]


def test_packed_wins_when_both_sources_exist(facts: dict) -> None:
    """同名时**打包优先**（card ① 的顺序）—— 而且读数会说出用的是哪一张。"""
    block = facts["both"]
    assert block["disk_exists"] is True, "盘上那个同名文件本该在"
    assert block["label"] == PACKED_LABEL, block
    assert block["image"] == block["packed_name"], block
    assert block["packed"]["normal"] is True, block


# --------------------------------------------------------------------------- #
# ④ 自动接那条：抽图目录空 / 不在 ⇒ 用同名打包图
# --------------------------------------------------------------------------- #
@_retired(_RETIRED_VT)
def test_auto_bind_uses_packed_maps_when_the_dir_is_empty(facts: dict) -> None:
    """抽图目录空、场景里有同名打包图 ⇒ ① 拿 自动接上，且 paths 给的是**图名不是路径**。"""
    block = facts["auto_packed"]
    assert block["paths"] == {
        "normal": f"{UNIT_KEY}_normal_decoded.png",
        "mask": f"{UNIT_KEY}_mask_raw.png",
    }, block["paths"]
    for name in block["paths"].values():
        assert "/" not in name and "\\" not in name, name
    assert "打包" in block["note"], block["note"]
    assert "打包" in block["vt_map_note"], block["vt_map_note"]
    assert block["bound"] == 1, block["lines"]
    assert block["normal"] == f"{UNIT_KEY}_normal_decoded.png", block
    assert block["mask"] == f"{UNIT_KEY}_mask_raw.png", block
    assert block["packed_flags"]["normal"] is True and block["packed_flags"]["mask"] is True, block
    line = block["lines"][0]
    assert f"normal={UNIT_KEY}_normal_decoded.png（{PACKED_LABEL}）" in line, line
    assert f"mask={UNIT_KEY}_mask_raw.png（{PACKED_LABEL}）" in line, line


@_retired(_RETIRED_VT)
def test_auto_bind_uses_packed_maps_when_the_dir_is_gone(facts: dict) -> None:
    """抽图目录**整个不在**（用户只带一个 .blend）⇒ 打包图照样接上。"""
    block = facts["auto_packed_no_dir"]
    assert block["paths"] == {
        "normal": f"{UNIT_KEY}_normal_decoded.png",
        "mask": f"{UNIT_KEY}_mask_raw.png",
    }, block["paths"]
    assert block["bound"] == 1, block["lines"]
    assert block["normal"] == f"{UNIT_KEY}_normal_decoded.png", block
    assert block["mask"] == f"{UNIT_KEY}_mask_raw.png", block


# --------------------------------------------------------------------------- #
# ⑤ 不猜：单位键优先，分不清就明写
# --------------------------------------------------------------------------- #
@_retired(_RETIRED_VT)
def test_packed_candidates_are_keyed_then_refuse_to_guess(facts: dict) -> None:
    """按单位键挑中**它自己**那对；对不上且多张候选 ⇒ 返回空 ＋ note 写「分不清」。

    正控：场景里同时有 `RU_OTHER_*`（字母序在 `RU_FAKE` 前面）—— 按字母序挑就会撞上它。
    """
    block = facts["packed_pick"]
    assert block["keyed"] == f"{UNIT_KEY}_normal_decoded.png", block
    assert block["keyed_note"] == "", block
    assert OTHER_KEY in block["ambiguous_note"], block["ambiguous_note"]
    assert block["ambiguous"] == "", block
    assert "分不清" in block["ambiguous_note"], block["ambiguous_note"]


def test_half_pair_binds_nothing_and_does_not_crash(facts: dict) -> None:
    """这一对图**不全** ⇒ 两路都不接（⛔ 不半接），而且**不许把 ① 拿 带崩**。

    由来：`bind_user_maps` 的契约是「先取齐两张再动节点树」，少一张当场 `ValueError` ——
    那条异常会一路冒到 `_import_from_source`，整次「① 拿」就废了。
    """
    block = facts["half_pair"]
    assert block["skipped"] == ["no_image"], block
    assert block["bound"] == [False], block
    assert "不全" in block["lines"][0] and "不半接" in block["lines"][0], block["lines"]
    assert block["normal_after"] is None and block["mask_after"] is None, block


@_retired(_RETIRED_VT)
def test_missing_note_still_names_the_globs(facts: dict) -> None:
    """回归锁：缺图那句人话仍然点到**两个后缀**与目录（`tests/test_map_bind.py` 也依赖它）。"""
    block = facts["missing"]
    assert block["bound"] == 0, block["lines"]
    assert block["normal_after"] is None, block
    assert "*_normal_decoded.png" in block["note"], block["note"]
    assert "*_mask_raw.png" in block["note"], block["note"]
    assert "RU_不存在的单位_normal_decoded.png" in block["note"], block["note"]