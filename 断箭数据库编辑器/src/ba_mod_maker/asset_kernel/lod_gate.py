r"""P3-5 · **LOD 阶梯闸门**（一条 LOD 链还成不成立）—— 判据逻辑。

## 用途

一个 modder 换掉某个单位的网格之后，判「这条 LOD 链还成不成立」。
**逻辑住这里**；命令行入口是 `tools/lod_gate.py`（薄封装，形状照 `tools/gates.py`）。

## 判据一行

    .venv\Scripts\python.exe tools\lod_gate.py --selftest
      -> 违规夹具 3/3 报红 · 合法件 4/4 绿 · **rc = 0**

## 正控 / 负控（本模块按 AGENTS §0.9 动作 2 的叫法）

* **正控 = 已知违规夹具（必须红）**：R1 顶点增加 · R2 空级 · R3 阈值数 != LOD 级数
* **负控 = 合法件（必须绿，不许误伤）**：标准三级档 · 单级形态 · 步比 1.0 · bbox 不同

⚠ `tools/gates.py` 的表格用的是**相反**的标签（它把「实读真件」叫正控）。这里跟 AGENTS §0.9 走。

## ★★ 红档只有结构三条 —— 下面这三条 ⛔ 不许当红档（逐字保留，别再犯）

| 码 | 红档 | 为什么是它 |
|---|---|---|
| **R1** | 某级顶点数 **>** 上一级（越降越细） | 换件最常见：把高模挂到了 LOD1 |
| **R2** | 某一级 **没有 renderer** 或 **0 顶点** | 空级 = 链上出现"洞"，白占一级 |
| **R3** | **阈值个数 != LOD 级数** | 链断（原版：3 阈值 = 3 级） |

⛔ **不许当红档的三条，每条都带本机读数**（全库普查 `_work/p3lod/lod_units.json`：
3,103 个 LODGroup / 4,453 个网格 / 4,854 对）：

1. **bbox 逐位相同** —— 游戏原版件里只有 **54 / 3,248 = 1.7%** 的对是逐位相同的
   ⇒ 拿它当红档会把 **98.3% 的游戏原版件判红**。
2. **任何 ≥ 0.0814 的比值红线** —— 整体步比 p10 = **0.0814**
   ⇒ **任何 ≥ 0.0814 的比值红线都会让 ≥10% 的原版件被判红**，
   与第 1 条是**同一种错误**：**一条红线如果会把原版件判红，那它就不是规格，是 bug。**
3. **步比 = 1.0（没减面）** —— 原版步比 p90 = **1.0**，
   即「LOD1 与 LOD0 顶点数一样」在原版里**合法**。

比值只出**黄牌**（不改 `ok`）：超出原版分位带时提醒 ——
LOD0→1 带 `[0.2843, 1.0]` · LOD1→2 带 `[0.1867, 1.0]`。

## 未读（⛔ 不编）

* **LOD2→LOD3 的减面比未读**：全库只有 **1** 个样本（四级档第 4 级没有可用读数）；
* **低于 p10 的比值尾部未读**：`lod_units.json` 只存了 p10/p50/p90
  ⇒ 「比 < 0.05 的原版件占多少」读不出来 —— **这也是「比值不许当红线」的第二条理由**。

## 两份 lod_gate 的关系（kb/19 §1：入口只有一个）

* **`asset_kernel/lod_gate.py`（本文件）= 产品件**：
  判据逻辑（`judge` / 夹具 / `selftest`），要改判据改这里；
* **`tools/lod_gate.py` = 产品件**：CLI 薄封装，只打印不判；
* **`_work/p3lod/lod_gate.py` = 研究件 / 证据**（第一版）：⛔ 不再改，也别拿它当入口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

#: 黄牌带（照游戏原版的分位数给；⛔ 只当提醒，不当红档）
BAND = {"LOD0->LOD1": (0.2843, 1.0), "LOD1->LOD2": (0.1867, 1.0)}

#: `--json` 里那三条**不许当红档**的红线（原文，人读；⛔ 不许扩成判据）
FORBIDDEN_RED_LINES = (
    "bbox 逐位相同（原版只有 1.7% 相同 ⇒ 会把 98.3% 的原版件判红）",
    "任何 ≥0.0814 的比值红线（整体 p10 = 0.0814 ⇒ 会让 ≥10% 的原版件被判红）",
    "步比 = 1.0（原版 p90 = 1.0 ⇒ 合法）",
)

#: 原版分布参照（`--readings` 读它；读不到就如实说未读）
READINGS = WORK / "p3lod" / "lod_units.json"


# --------------------------------------------------------------------------- #
# 判据（纯函数：进这一层的只有"一条 LOD 链"的读数）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class LodLevel:
    """一级 LOD 的读数（renderer 为空时 vertices 记 0）。"""

    mesh: str
    vertices: int
    bbox: tuple[float, float, float] | None = None
    renderer: bool = True


@dataclass(frozen=True, slots=True)
class LodGroup:
    """一个 LODGroup：levels 按 LOD0..N 顺序，thresholds 是它自己带的阈值表。"""

    name: str
    levels: tuple[LodLevel, ...]
    thresholds: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reds: tuple[str, ...] = ()
    warns: tuple[str, ...] = ()
    steps: tuple[tuple[str, float], ...] = field(default=())

    def line(self) -> str:
        head = "OK " if self.ok else "RED"
        tail = " · ".join(self.reds) if self.reds else (" · ".join(self.warns) or "结构正常")
        return f"[{head}] {tail}"


def judge(group: LodGroup) -> Verdict:
    """一条 LOD 链的判定。**红档只有结构三条**（R1/R2/R3），比值只出黄牌。"""
    reds: list[str] = []
    warns: list[str] = []
    steps: list[tuple[str, float]] = []

    if not group.levels:
        return Verdict(ok=False, reds=("R2 一个 LOD 级都没有",))

    for i, lv in enumerate(group.levels):                       # R2 空级
        if not lv.renderer:
            reds.append(f"R2 LOD{i} 没有 renderer（空级）")
        elif lv.vertices <= 0:
            reds.append(f"R2 LOD{i} 顶点数为 0（空网格）")

    for i in range(1, len(group.levels)):                       # R1 单调不增
        a, b = group.levels[i - 1], group.levels[i]
        if a.vertices > 0 and b.vertices > 0 and b.vertices > a.vertices:
            reds.append(f"R1 LOD{i} 顶点 {b.vertices} > LOD{i - 1} 的 {a.vertices}（越降越细）")

    if group.thresholds and len(group.thresholds) != len(group.levels):   # R3 链断
        reds.append(f"R3 阈值 {len(group.thresholds)} 个 != LOD {len(group.levels)} 级（链断）")

    for i in range(1, len(group.levels)):                       # 黄牌：步比出带
        a, b = group.levels[i - 1], group.levels[i]
        if a.vertices <= 0 or b.vertices <= 0:
            continue
        ratio = b.vertices / a.vertices
        key = f"LOD{i - 1}->LOD{i}"
        steps.append((key, ratio))
        lo, hi = BAND.get(key, (0.0, 1.0))
        if not lo <= ratio <= hi:
            warns.append(f"黄 {key} 比 {ratio:.4f} 不在原版带 [{lo:.4f}, {hi:.2f}] 内")
    return Verdict(ok=not reds, reds=tuple(reds), warns=tuple(warns), steps=tuple(steps))


def group_from_dict(row) -> LodGroup:
    """把 `--check` 的 JSON 行读成 LodGroup（形状写死，⛔ 不猜别键）。"""
    return LodGroup(
        name=str(row.get("name", "?")),
        levels=tuple(
            LodLevel(mesh=str(x.get("mesh", "")), vertices=int(x.get("vertices", 0)),
                     bbox=tuple(x["bbox"]) if x.get("bbox") else None,
                     renderer=bool(x.get("renderer", True)))
            for x in row["levels"]
        ),
        thresholds=tuple(float(t) for t in row.get("thresholds", ())),
    )


# --------------------------------------------------------------------------- #
# 内置夹具（⛔ 每个判据都要有正控与负控，AGENTS §0.9 动作 2）
# --------------------------------------------------------------------------- #
def _lv(v: int, mesh: str = "m", bbox=(1.0, 1.0, 1.0), renderer: bool = True) -> LodLevel:
    return LodLevel(mesh=mesh, vertices=v, bbox=bbox, renderer=renderer)


#: 正控 = **已知违规**（必须红）：(行号, 标题, 链, 期望命中的码)
POSITIVE: tuple[tuple[str, str, LodGroup, str], ...] = (
    ("L1", "R1 顶点增加（高模挂到 LOD1）",
     LodGroup("p1", (_lv(1000), _lv(1200)), (0.1, 0.04)), "R1"),
    ("L2", "R2 空级（LOD1 没有 renderer）",
     LodGroup("p2", (_lv(1000), _lv(0, renderer=False)), (0.1, 0.04)), "R2"),
    ("L3", "R3 链断（3 个阈值只有 2 级）",
     LodGroup("p3", (_lv(1000), _lv(400)), (0.1, 0.04, 0.001)), "R3"),
)

#: 负控 = **合法件**（必须绿）：(行号, 标题, 链)
NEGATIVE: tuple[tuple[str, str, LodGroup], ...] = (
    ("N1", "标准三级档（比 = 1.0 / 0.44 / 0.28）",
     LodGroup("n1", (_lv(1000), _lv(440), _lv(123)), (0.1, 0.04, 0.001))),
    ("N2", "单级形态（原版 1,188/3,103 = 38.3% 就是它）",
     LodGroup("n2", (_lv(1000),), (0.02,))),
    ("N3", "步比 = 1.0（原版 p90 = 1.0，⛔ 不许当红档）",
     LodGroup("n3", (_lv(1000), _lv(1000)), (0.1, 0.04))),
    ("N4", "bbox 不同（原版 98.3% 都不同，⛔ 不许当红档）",
     LodGroup("n4", (_lv(1000, bbox=(1.0, 1.0, 1.0)), _lv(440, bbox=(1.0001, 1.0, 1.0))),
              (0.1, 0.04))),
)


@dataclass(frozen=True, slots=True)
class Run:
    """一次自检的读数（正控必须红、负控必须绿）。"""

    rows: tuple[tuple[str, str, str, bool], ...]     # (行号, 标题, 读数, 是否通过)
    positive_ok: int
    negative_ok: int

    @property
    def rc(self) -> int:
        return 0 if (self.positive_ok == len(POSITIVE) and self.negative_ok == len(NEGATIVE)) else 1


def selftest() -> Run:
    """跑内置夹具：**正控 3/3 报红 · 负控 4/4 绿** 才算 rc=0。"""
    rows: list[tuple[str, str, str, bool]] = []
    pos_ok = 0
    for row_id, title, group, want in POSITIVE:
        v = judge(group)
        hit = (not v.ok) and any(want in r for r in v.reds)
        pos_ok += 1 if hit else 0
        rows.append((row_id, f"正控·{title}", v.line(), hit))
    neg_ok = 0
    for row_id, title, group in NEGATIVE:
        v = judge(group)
        neg_ok += 1 if v.ok else 0
        rows.append((row_id, f"负控·{title}", v.line(), v.ok))
    return Run(rows=tuple(rows), positive_ok=pos_ok, negative_ok=neg_ok)


def readings_document() -> dict | None:
    """原版分布参照（读不到返回 None —— 由调用方印「未读」，⛔ 不许假装成功）。"""
    if not READINGS.is_file():
        return None
    return json.loads(READINGS.read_text(encoding="utf-8"))
