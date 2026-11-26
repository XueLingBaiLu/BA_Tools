"""389 条 generic/empty clip 的性质（D 线）—— **离线**判据，⛔ 不开 units 包、⛔ 不碰游戏目录。

**对外只报三档**（Lead 口径 2026-10-13）：**A 完整可做 = direct 254（65.30%）** ·
**B 可做但缺曲线 = needs_channel 41 ＋ partial_high 10 = 51（13.11%）** ·
**C 不做 = partial_low 9 ＋ dead 46 ＋ empty 29 = 84（21.59%）**。
⛔ 「A ＋ B」**不是**一个可对外报的「可做率」—— 那会把「完整可做」与「缺胳膊少腿」混成一个数。

这份报告回答的是「这 389 条**能不能做成 Blender 的 Action**」。它量的东西只有一件：
**产品自己的造规格函数真的跑过一遍之后，有几条曲线落进 Action、丢了几条、为什么丢**
（`ba_mod_maker.asset_kernel.anim_action.build_action_spec`），不是估的。

台账由 `_work/genericline/g2_classify.py` 落成 `_work/genericline/generic_clips.json`（清单）
＋ `generic_clips.tsv` ＋ `tables.md`（报告里的表）。本文把她最关键的 4 条读数钉成断言。

尺子自证（缺一条都不算数，都在 g2 里跑）：
    * 正控  crc32 四条可手算路径（**逐字照抄** `_work/anim-recon/m1_clip_classify.py` 的
            `CRC_CONTROLS`）—— 本文件另测一次，并另喂一条**改了一个字符**的负控（必须不命中）
    * 负控  把路径表换成空表 ⇒ 「有 generic 绑定的 clip」必须**全部**判 dead（374/374）
    * 交叉  清单逐条与产品 `anim list` 的 `kind` 逐条相同（两种代码路径，同一批输入）

**⛔ 这把尺子看不见的（明写在下面，别拿它当「都对」）**：
    ① **没跑过 Blender** —— 数的是"规格里的曲线条数"，不是"Action 造出来什么样、动起来对不对"；
       观感只有用户能判（AGENTS §0.12.1）；
    ② `dead` 那 46 条**为什么**没有骨名（运行时拼接？属于别的包？）—— **本文件不判**，
       它只钉住「units 包里确实没有这些路径」这条**可复算的读数**；
    ③ 清单本身是生成物：断言的是**它与两条独立来源对得上**（研究线 m1 逐条 / 产品 anim list 逐条），
       不是"清单里的数字天生正确"。
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
BASE = REPO.parent
MANIFEST = WORK / "genericline" / "generic_clips.json"
CENSUS = WORK / "anim-recon" / "out" / "m1_clip_classify.json"

REGEN = (f"重生成（约 40 秒，纯离线零开包）：cd {REPO} 后 "
         ".venv/Scripts/python.exe _work/genericline/g2_classify.py")

VERDICT_ORDER = ("direct", "needs_channel", "needs_partial", "dead", "empty")
#: 报告 §3.2 的四个数 —— 任何一条变了都必须是有意的改动（否则说明数据或口径动了）
FROZEN_COUNTS = {"direct": 254, "needs_channel": 41, "needs_partial": 19,
                 "dead": 46, "empty": 29}
FROZEN_TOTALS = {"curves": 12338, "curves_into_action": 9919,
                 "curves_dropped": 2419, "bones": 1925}
#: ★ 对外报数只看这三档（B 里含 partial_high、C 里含 partial_low）
FROZEN_TIERS = {"A": 254, "B": 60, "C": 75}
#: 六子档（needs_partial 必须拆开 —— 里面是两个群体）
FROZEN_SUBS = {"direct": 254, "needs_channel": 41, "partial_high": 10,
               "partial_low": 9, "dead": 46, "empty": 29}
#: REPORT §3.2 那张表的四列 (曲线, 进 Action, 丢, 骨) —— **从清单抄，⛔ 一个数都不许估**
#: （v2 第一版这四个数是我把 needs_partial 的合计**手拆**出来的，被本文件当场抓红）
FROZEN_SUB_ROWS = {
    "direct": (6197, 6197, 0, 1279),
    "needs_channel": (2113, 1953, 160, 419),
    "partial_high": (1784, 1720, 64, 216),
    "partial_low": (334, 49, 285, 11),
    "dead": (1910, 0, 1910, 0),
    "empty": (0, 0, 0, 0),
}
#: 逐字照抄 m1_clip_classify.py 的 CRC_CONTROLS（⛔ 不许凭记忆改一个字符）
CRC_CONTROLS = {4194319443: "root/body/turret_0/helper_01/door",
                2792331291: "root/body/turret_0/helper_01",
                670699373: "root/body/special_legs",
                2379768308: "root/body/body/Shnorkel"}


def crc32(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


@pytest.fixture(scope="module")
def manifest() -> dict:
    if not MANIFEST.exists():
        pytest.fail(f"缺落盘清单 {MANIFEST}（这把尺子读真读数，不许跳）。{REGEN}")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def census() -> dict:
    if not CENSUS.exists():
        pytest.fail(f"缺独立对拍源 {CENSUS}（研究线**开包**跑的 475 条归类）。"
                    "复算：_work/anim-recon/m1_clip_classify.py（要 units 包副本）")
    return json.loads(CENSUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_verdict(manifest: dict) -> dict:
    out: dict[str, list[dict]] = {v: [] for v in VERDICT_ORDER}
    for row in manifest["clips"]:
        out[row["verdict"]].append(row)
    return out


# --------------------------------------------------------------------------- #
# 尺子先自证
# --------------------------------------------------------------------------- #
def test_crc32_ruler_is_alive_and_can_go_red() -> None:
    """正控 4/4 命中；**再喂一条改了一个字符的**必须不命中（不能红的尺子不算尺子）。"""
    for want, text in CRC_CONTROLS.items():
        assert crc32(text) == want, f"crc32({text!r}) 应为 {want}，实得 {crc32(text)}"
    controls = set(CRC_CONTROLS.values())
    mutated = {t + "x" for t in controls} | {t.replace("/", "_", 1) for t in controls}
    for text in mutated:
        assert crc32(text) not in CRC_CONTROLS, f"负控 {text!r} 竟然命中了 —— 尺子坏了"


# --------------------------------------------------------------------------- #
# ① 这 389 条是谁、与两条独立来源对不对得上
# --------------------------------------------------------------------------- #
def test_the_389_are_exactly_the_muscle_free_clips(manifest: dict, census: dict) -> None:
    """389 = 全库 475 − 有肌肉绑定的 86；且逐条 6 个字段与研究线**开包**的读数完全相同。"""
    assert len(manifest["clips"]) == 389
    assert all(r["muscle_bindings"] == 0 for r in manifest["clips"])

    research = {int(c["pid"]): c for c in census["clips"]}
    expected = {p for p, c in research.items() if c["kind"] in ("generic", "empty")}
    assert len(expected) == 389, f"研究线口径变了：generic+empty = {len(expected)}"
    assert {r["path_id"] for r in manifest["clips"]} == expected

    bad: list[str] = []
    for row in manifest["clips"]:
        ref = research[row["path_id"]]
        for mine, theirs, label in ((row["curves"], ref["curves"], "curves"),
                                    (row["generic_bindings"], ref["n_generic_bindings"],
                                     "generic_bindings"),
                                    (row["muscle_bindings"], ref["n_muscle_bindings"],
                                     "muscle_bindings"),
                                    (row["m1_kind"], ref["kind"], "kind"),
                                    (row["name"], ref["name"], "name")):
            if mine != theirs:
                bad.append(f"{row['name']}：{label} {mine!r} != 研究线 {theirs!r}")
        if abs(row["duration"] - ref["duration"]) > 1e-9:
            bad.append(f"{row['name']}：duration {row['duration']} != {ref['duration']}")
    assert not bad, "清单与研究线逐条对不上（前 5 条）：" + "；".join(bad[:5])


# --------------------------------------------------------------------------- #
# ② 五档 + 冻结的数字
# --------------------------------------------------------------------------- #
def test_five_verdicts_partition_the_389(manifest: dict, by_verdict: dict) -> None:
    """五档必须**恰好划分** 389 条（无重叠、无遗漏、无未知档），且四个总量读数不变。"""
    summary = manifest["summary"]
    counts = {v: len(by_verdict[v]) for v in VERDICT_ORDER}
    assert counts == FROZEN_COUNTS, f"五档读数变了：{counts}（报告 §0/§3.2 要同步改）"
    assert sum(counts.values()) == 389
    assert summary["verdicts"] == FROZEN_COUNTS
    for key, want in FROZEN_TOTALS.items():
        assert summary["totals"][key] == want, f"totals[{key}] = {summary['totals'][key]} != {want}"

    # ★ 三档（对外报数只看这个）
    assert summary["tiers"] == FROZEN_TIERS, (
        f"三档读数变了：{summary['tiers']}（REPORT §0.1 要同步改）")
    assert summary["sub_verdicts"] == FROZEN_SUBS
    for key, want in FROZEN_SUB_ROWS.items():
        row = summary["per_sub"][key]
        got = (row["curves"], row["curves_into_action"],
               row["curves_dropped"], row["bones"])
        assert got == want, f"per_sub[{key}] = {got} != {want}（REPORT §3.2 要同步改）"
    # 三档必须**由六子档映射而来**（口径自洽，⛔ 不是两套独立的数）
    # ★ 三档**按语义**映射（Lead 口径 2026-10-13）：
    #   A=direct · B=needs_channel+needs_partial(全部) · C=dead+empty
    assert summary["tiers"]["A"] == FROZEN_SUBS["direct"]
    assert summary["tiers"]["B"] == (FROZEN_SUBS["needs_channel"]
                                     + FROZEN_SUBS["partial_high"]
                                     + FROZEN_SUBS["partial_low"])
    assert summary["tiers"]["C"] == FROZEN_SUBS["dead"] + FROZEN_SUBS["empty"]
    assert sum(summary["tiers"].values()) == 389

    # ★ needs_partial 的拆档：阈值**从清单读**（改常量重生成即可，⛔ 不许在这里写死另一套）
    thr = summary["partial_high_min_ratio"]
    assert thr == 0.5, f"拆档阈值变了：{thr}"
    partial = by_verdict["needs_partial"]
    hi = [r for r in partial if r["resolve_ratio"] >= thr]
    lo = [r for r in partial if r["resolve_ratio"] < thr]
    assert len(hi) == 10 and len(lo) == 9, f"拆档变了：high {len(hi)} / low {len(lo)}"
    assert all(r["sub_verdict"] == "partial_high" for r in hi)
    assert all(r["sub_verdict"] == "partial_low" for r in lo)
    # 尺子分得开这两群（两边都非空 —— 不是一个桶）
    assert hi and lo
    # partial_low 那 9 条：曲线**绝大部分进不了** Action（这正是它归 C 的理由）
    # ⚠ 不是「一条都进不了」—— 它们仍有 2 条左右的绑定解析出来了，那几条曲线是能进 Action 的
    pl = summary["per_sub"]["partial_low"]
    assert (pl["curves_into_action"], pl["curves_dropped"]) == (49, 285)
    assert all(r["curves"] > 0 for r in lo), "partial_low 不该有空 clip"
    assert all(r["resolve_ratio"] < thr for r in lo)


# --------------------------------------------------------------------------- #
# ③ 能直接做的那一档：一条曲线都不丢
# --------------------------------------------------------------------------- #
def test_direct_bucket_loses_no_curve(by_verdict: dict) -> None:
    """`direct` = 绑定全解析 **且** 产品的造规格把它**一条不丢**地收进 Action。"""
    rows = by_verdict["direct"]
    assert len(rows) == FROZEN_COUNTS["direct"]
    for row in rows:
        assert row["curves"] > 0, f"{row['name']} 是空 clip，不该判 direct"
        assert row["resolved"] == row["generic_bindings"], row["name"]
        assert row["dropped_curves"] == 0, row["name"]
        assert row["action_curves"] == row["curves"], row["name"]
        assert row["action_bones"] > 0, row["name"]
    # 反向：`dead` 那档**不可能**满足上面任何一条（尺子分得开这两群）
    for row in by_verdict["dead"]:
        assert row["resolved"] == 0 and row["action_curves"] == 0


# --------------------------------------------------------------------------- #
# ④ 做不了的那一档：有绑定、但一条路径都解析不出
# --------------------------------------------------------------------------- #
def test_dead_bucket_has_bindings_but_zero_resolvable_paths(by_verdict: dict) -> None:
    """`dead` = **有 generic 绑定**却 **0 条**解析出骨名 ⇒ 一条曲线都进不了 Action。"""
    rows = by_verdict["dead"]
    assert len(rows) == FROZEN_COUNTS["dead"]
    for row in rows:
        assert row["generic_bindings"] > 0, f"{row['name']} 没有 generic 绑定，应判 empty"
        assert row["resolved"] == 0, row["name"]
        assert row["spec_error"] == "dead", row["name"]
        assert row["action_curves"] == 0 and row["action_bones"] == 0, row["name"]
        assert row["dropped_curves"] == row["curves"], row["name"]
        assert row["unresolved_crcs"], row["name"]
    # 这几条一起说明「分类器不是一个桶」——它确实在分群（五档都非空）
    assert all(len(by_verdict[v]) > 0 for v in VERDICT_ORDER)
    # 三档归属：dead / empty 必须落 C；direct 必须落 A（⛔ 没有一条 dead 混进 B）
    assert {r["tier"] for r in rows} == {"C"}
    assert {r["tier"] for r in by_verdict["empty"]} == {"C"}
    assert {r["tier"] for r in by_verdict["direct"]} == {"A"}
    # needs_partial **全部**归 B —— 两个子档都是 B（⛔ 阈值不参与分档）
    assert {r["tier"] for r in by_verdict["needs_partial"]} == {"B"}
    assert {r["tier"] for r in by_verdict["needs_channel"]} == {"B"}


# --------------------------------------------------------------------------- #
# ⑤ 报告 §3.3 / §5.3 引的两个读数
# --------------------------------------------------------------------------- #
def test_static_poses_and_owners_are_recorded(manifest: dict) -> None:
    """时长 0 s 的 **61 条**不是空的（静态姿势）；389 条**全部**都有 animator 归属。"""
    clips = manifest["clips"]
    static = [r for r in clips if r["duration"] <= 0]
    assert len(static) == 61
    assert all(r["curves"] > 0 for r in static), "静态姿势那 61 条一条都不该是空 clip"
    assert sum(r["curves"] for r in static) == 1967
    assert all(r["owners"] for r in clips), "有 clip 没解出归属（报告 §5.3 那张表会不完整）"
