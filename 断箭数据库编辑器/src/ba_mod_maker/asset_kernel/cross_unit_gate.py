r"""跨单位回归尺子（B 类：「代码看着通用、只在一个件上验证过」的近似）。

## 为什么有这件东西

用户问：「我们的 Blender 插件，是不是有很多功能，一换其它模型就用不了？」
其中 **B 类** = 代码读着完全通用、但只在某一个件上验证过的近似 —— grep 抓不到，
**只有换件跑才暴露**。本模块就是那把「换件尺子」的判据层（纯函数，不碰 Blender 的 Python API）。

采集端 = `tools/cross_unit_gate.py --emit-probe` 生成的无头 Blender 探针
（`_work/audit-hardcode/probe_cross_unit.py`），它逐件落 JSON；本模块只读那份 JSON 下判。

## 四条硬判据（每条都能红）

* **R1** 每个 renderer 都有主：`report.lod_*` 记数对不上、LOD 替身名被判「一律可见」、
  多个 LODGroup 时产品没用**根上那一个**；
* **R2** 物理尺寸不许量级离群：同名件跨 LOD 档比 > `CROSS_LOD_RATIO`（实测 US_AH-1W 39.028 倍）；
* **R2b** 单网格最大边 > 该件中位数 × `UNIT_OUTLIER_FACTOR`；
* **R3** 材质接的每张图都属于本单位：图名里出现**别的**单位键（规范化后互不为子串）；
* **R4** 件级 bbox 落在合理区间：超出 `[UNIT_BBOX_MIN, UNIT_BBOX_MAX]`（实测「686 m 的车」）。

## 三条便宜判据

| 码 | 判据 |
|---|---|
| **C1** | `report.errors` 为空（另附 `rig_error` 黄牌：骨架没建起来必须显形） |
| **C2** | `report.renderers` == prefab 树里的 renderer 数 == 真建出来的 renderer 对象数 |
| **C3** | 骨槽数不丢：蒙皮对象顶点组数 == 它那个 renderer 的槽数；骨 + 空槽 >= 槽表 |

## 阈值口径（⛔ 一条红线如果会把原始件判红，那它是 bug 不是规格）

* `CROSS_LOD_RATIO = 2.0` —— LOD 降面不改尺寸；实测坏件是 39.028 倍，距红线 19.5 倍；
* `UNIT_OUTLIER_FACTOR = 20.0` —— 对照量 = 该件网格最大边长的**中位数**（校准见常量注释）；
* `UNIT_BBOX_MAX = 100.0` m —— 全库最大的件（米格-31 ≈ 23 m，直升机含旋翼 ≈ 20 m）离它 4 倍以上；
* `UNIT_BBOX_MIN = 0.30` m —— 防「整件被缩成 0」。

## 判据一行

    .venv\Scripts\python.exe tools\cross_unit_gate.py --selftest --json <读数字典>
      （默认 _work\audit-hardcode\cross-unit.json）
      -> 注入的红 5/5 报出 · 真件 RU_T72B 绿 · **rc = 0**
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

#: 同名件跨 LOD 档的世界尺寸比上限（LOD 降面不改尺寸）。
CROSS_LOD_RATIO = 2.0
#: 单个网格 / 该件网格最大边长中位数的上限（量级离群的兜底红线）。
#: ★ 实测校准（2026-10-17，9 件）：合法件最大比 = US_AH-1Z 的 18.405 / 1.800 = **10.2**；
#:   坏件（US_AH-1W 机身）= 686.607 / 1.837 = **373.7** ⇒ 红线取两者之间（2 倍余量 / 19 倍余量）。
#:   ⛔ 校准前取 6.0 —— 会把 US_AH-1Z / US_AH-1W 的**合法** LOD0 机身一起判红（假红）。
UNIT_OUTLIER_FACTOR = 20.0
#: 件级 bbox 的合理区间（米）。
UNIT_BBOX_MIN = 0.30
UNIT_BBOX_MAX = 100.0

#: 名字像 LOD 替身的三种写法（⛔ 它们不许被判成「一律可见」）。
LOD_LOOKALIKE: tuple[tuple[str, str], ...] = (
    (r"^Level\d+$", "Level<数字>"),
    (r"(?i)lod_\d+", "lod_<数字>"),
    (r"(?i)\d{3}_skinned_", "<3位数字>_skinned_"),
    (r"^LOD$", "裸名 LOD"),  # 实测 DLC2_XA180EST 有一个 renderer 就叫 LOD
)

_TRAILING_INDEX = re.compile(r"\.\d{3}$")
_LOD_PREFIX = re.compile(r"(?i)^(?:\d{3}_skinned_|lod_?\d+_?|level\d+_?)")
_LOD_SUFFIX = re.compile(r"(?i)_lod\d+$")
_NON_ALNUM = re.compile(r"[^0-9a-z]")
_NON_ALNUM_UP = re.compile(r"[^A-Za-z0-9]")


def lod_lookalike(name: str) -> str | None:
    """名字像不像 LOD 替身？像就返回像哪一种（不像返回 None）。"""
    for pattern, label in LOD_LOOKALIKE:
        if re.search(pattern, name):
            return label
    return None


def part_key(name: str) -> str:
    """网格名 -> 跨 LOD 档的「同一个件」键。

    去掉 Blender 的 `.001` 去重后缀、去掉 LOD 替身的前后缀（`000_skinned_` / `lod_1` /
    `Level02_` / `_lod1`），再只留小写字母数字。实测：`body2` 与 `000_skinned_body2`
    都折成 `body2` ⇒ 它俩就是同一条 LOD 链上的同一个件。
    """
    s = _TRAILING_INDEX.sub("", name)
    s = _LOD_PREFIX.sub("", s)
    s = _LOD_SUFFIX.sub("", s)
    return _NON_ALNUM.sub("", s.lower())


def norm_key(text: str) -> str:
    """单位键规范化：只留字母数字、大写（`US_AH-1W Super_Cobra` -> `USAH1WSUPERCOBRA`）。"""
    return _NON_ALNUM_UP.sub("", text).upper()


def foreign_unit_keys(
    image_name: str, unit_key: str, known_keys: tuple[str, ...]
) -> tuple[str, ...]:
    """图名里出现了哪些**别的**单位键（互不为子串才算：`US_M1A2` 与 `US_M1A2SEP` 不算）。"""
    name = norm_key(image_name)
    own = norm_key(unit_key)
    hits: set[str] = set()
    for key in known_keys:
        if key == unit_key:
            continue
        kn = norm_key(key)
        if len(kn) < 4 or kn not in name:
            continue
        if kn in own or own in kn:
            continue
        hits.add(key)
    return tuple(sorted(hits))


@dataclass(frozen=True, slots=True)
class Thresholds:
    """判据阈值（**可注入** —— 自检里把它改小，验证尺子确实读这个旋钮）。"""

    cross_lod_ratio: float = CROSS_LOD_RATIO
    unit_outlier_factor: float = UNIT_OUTLIER_FACTOR
    unit_bbox_min: float = UNIT_BBOX_MIN
    unit_bbox_max: float = UNIT_BBOX_MAX


@dataclass(frozen=True, slots=True)
class Red:
    """一条红：码 + 可复算的一句话。"""

    rule: str
    detail: str


@dataclass(slots=True)
class UnitVerdict:
    """一件的判定 + 逐条读数（四条判据的数字都在 readings 里）。"""

    unit: str
    reds: tuple[Red, ...] = ()
    warns: tuple[str, ...] = ()
    readings: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.reds

    def line(self) -> str:
        head = "OK " if self.ok else "RED"
        if self.reds:
            tail = " · ".join(f"[{r.rule}] {r.detail}" for r in self.reds)
        elif self.warns:
            tail = " · ".join(self.warns)
        else:
            tail = "四条判据 + 三条便宜判据全绿"
        return f"[{head}] {self.unit}: {tail}"


def evaluate(
    record: dict[str, Any],
    *,

    known_keys: tuple[str, ...] = (),
    thresholds: Thresholds | None = None,
) -> UnitVerdict:
    """一份探针读数 -> 一件的判定（纯函数）。"""
    th = thresholds or Thresholds()
    unit = str(record.get("unit_key") or "?")
    if record.get("probe_error"):
        return UnitVerdict(
            unit=unit, reds=(Red("P0-没采到", str(record["probe_error"])),)
        )
    rep: dict[str, Any] = dict(record.get("report") or {})
    lod: dict[str, Any] = dict(record.get("lod") or {})
    objects: list[dict[str, Any]] = list(record.get("objects") or [])
    reds: list[Red] = []
    warns: list[str] = []
    readings: dict[str, Any] = {}

    # ---------------------------------------------------------------- R1
    levels = [list(lv) for lv in (lod.get("levels") or [])]
    rends = [dict(r) for r in (lod.get("renderers") or [])]
    owner: dict[int, list[int]] = {}
    for index, level in enumerate(levels):
        for cid in level:
            owner.setdefault(int(cid), []).append(index)
    outside = [r for r in rends if int(r["component_id"]) not in owner]
    multi = [r for r in rends if len(owner.get(int(r["component_id"]), ())) > 1]
    hidden = sum(
        1
        for r in rends
        if owner.get(int(r["component_id"])) and min(owner[int(r["component_id"])]) > 0
    )
    lod0 = len(levels[0]) if levels else 0
    readings["lod"] = {
        "renderers": len(rends),
        "levels": len(levels),
        "lod0": lod0,
        "union": len(owner),
        "outside": len(outside),
        "hidden": hidden,
        "outside_names": [str(r.get("name")) for r in outside],
        "multi_owner": [str(r.get("name")) for r in multi],
        "lod0_names": [
            str(r.get("name"))
            for r in rends
            if owner.get(int(r["component_id"]), [None])[0] == 0
        ],
    }
    for key, want in (
        ("lod_levels", len(levels)),
        ("lod0_members", lod0),
        ("lod_union", len(owner)),
        ("lod_outside", len(outside)),
        ("lod_hidden", hidden),
    ):
        got = rep.get(key)
        if got != want:
            reds.append(Red("R1-记数", f"report.{key}={got}，按 prefab 树折出来是 {want}"))
    groups = list(record.get("lod_groups") or [])
    readings["lod_groups"] = groups
    if groups:
        root = groups[0]
        root_ids = {int(c) for c in (root.get("ids") or [])}
        if root_ids != set(owner):
            used = groups[-1] if len(groups) > 1 else root
            reds.append(
                Red(
                    "R1-LOD组",
                    f"prefab 里有 {len(groups)} 个 LODGroup；**根上那一个**才是真件的档位"
                    f"（{root.get('node')}，并集 {len(root_ids)}），产品用的却是"
                    f"遍历里最后一个（{used.get('node')}，并集 {len(owner)}）"
                    "（prefab.build_tree 逐组覆盖 lods，最后一个赢）",
                )
            )
    seen_outside: set[str] = set()
    for r in outside:
        name = str(r.get("name") or "")
        label = lod_lookalike(name)
        if label and name not in seen_outside:
            seen_outside.add(name)
            reds.append(
                Red(
                    "R1-LOD替身",
                    f"{name} 不在任何 LOD 档里，却被算成「一律可见」（名字像 {label}）",
                )
            )
    for r in multi:
        # ⚠ 实测 RU_T72B：炮/机枪（T72BA / K1 / K5）LOD0 与 LOD1 **共用同一个 renderer**
        #   —— 原版件就这么用（不降面的件挂两级）⇒ ⛔ 这不当红档，只记读数。
        warns.append(
            f"{r.get('name')} 同时落在 LOD{owner[int(r['component_id'])]} 里"
            "（原版件合法：不降面的件挂两级）"
        )

    # ---------------------------------------------------------------- R2
    meshes: list[tuple[dict[str, Any], float]] = []
    for obj in objects:
        dims = obj.get("dims")
        if not obj.get("verts") or not dims:
            continue
        meshes.append((obj, max(float(x) for x in dims)))
    groups: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for obj, size in meshes:
        groups.setdefault(part_key(str(obj.get("name") or "")), []).append((obj, size))
    cross_rows = []
    for key, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        lo = min(s for _, s in rows)
        hi = max(s for _, s in rows)
        if lo <= 1e-9:
            continue
        ratio = hi / lo
        row = {
            "key": key,
            "ratio": round(ratio, 6),
            "lo": round(lo, 4),
            "hi": round(hi, 4),
            "members": [str(o.get("name")) for o, _ in rows],
        }
        cross_rows.append(row)
        if ratio > th.cross_lod_ratio:
            reds.append(
                Red(
                    "R2-跨档",
                    f"同一条 LOD 链上的 {key} 尺寸差 {ratio:.3f} 倍："
                    f"{row['lo']} m vs {row['hi']} m（{'、'.join(row['members'])}）",
                )
            )
    sizes = [s for _, s in meshes]
    med = median(sizes) if sizes else 0.0
    top = max(meshes, key=lambda kv: kv[1]) if meshes else None
    outlier_rows = []
    for obj, size in meshes:
        if med > 0 and size > th.unit_outlier_factor * med:
            outlier_rows.append({"name": str(obj.get("name")), "size": round(size, 4)})
            reds.append(
                Red(
                    "R2-离群",
                    f"{obj.get('name')} 最大边 {size:.3f} m > 中位 {med:.3f} m"
                    f" 的 {th.unit_outlier_factor:g} 倍",
                )
            )
    readings["size"] = {
        "meshes": len(meshes),
        "median_max_dim": round(med, 4),
        "max_dim": round(top[1], 4) if top else 0.0,
        "max_dim_object": str(top[0].get("name")) if top else "",
        "cross_lod": cross_rows,
        "outliers": outlier_rows,
    }

    # ---------------------------------------------------------------- R3
    images = [str(x) for x in (record.get("images") or [])]
    foreign = []
    for image in images:
        hits = foreign_unit_keys(image, unit, known_keys)
        if hits:
            foreign.append({"image": image, "keys": list(hits)})
            reds.append(Red("R3-异物图", f"图 {image} 带别的单位键：{'、'.join(hits)}"))
    readings["textures"] = {
        "images": len(images),
        "wanted": len(known_keys),
        "foreign": foreign,
    }

    # ---------------------------------------------------------------- R4
    bbox = [float(x) for x in (record.get("unit_bbox") or [0.0, 0.0, 0.0])]
    bbox_max = max(bbox) if bbox else 0.0
    readings["bbox"] = {"dims": [round(x, 4) for x in bbox], "max": round(bbox_max, 4)}
    if bbox_max > th.unit_bbox_max:
        reds.append(Red("R4-bbox", f"件级 bbox 最大边 {bbox_max:.3f} m > {th.unit_bbox_max:g} m"))
    if bbox_max < th.unit_bbox_min:
        reds.append(Red("R4-bbox", f"件级 bbox 最大边 {bbox_max:.4f} m < {th.unit_bbox_min:g} m"))

    # ---------------------------------------------------------------- C1
    errors = list(rep.get("errors") or [])
    readings["errors"] = errors
    if errors:
        reds.append(Red("C1-errors", f"report.errors 非空（{len(errors)} 条）：{errors[0]}"))
    if rep.get("rig_error"):
        warns.append(f"骨架没建起来：{rep.get('rig_error')}")

    # ---------------------------------------------------------------- C2
    counts = dict(record.get("counts") or {})
    tree_renderers = len(rends)
    if rep.get("renderers") != tree_renderers:
        reds.append(
            Red(
                "C2-renderer数",
                f"report.renderers={rep.get('renderers')} != 树里的 {tree_renderers}",
            )
        )
    if counts.get("renderer_objects") != tree_renderers:
        reds.append(
            Red(
                "C2-renderer数",
                f"真建出来的 renderer 对象 {counts.get('renderer_objects')}"
                f" != 树里的 {tree_renderers}",
            )
        )
    readings["counts"] = {
        "report": rep.get("renderers"),
        "tree": tree_renderers,
        "objects": counts.get("renderer_objects"),
    }

    # ---------------------------------------------------------------- C3
    rig = dict(record.get("rig") or {})
    slots = int(rig.get("slots") or 0)
    empty_slots = int(rig.get("empty_slots") or 0)
    bones = int(rep.get("bones") or 0)
    bad_groups = [
        {"object": str(o.get("name")), "vgroups": o.get("vgroups"), "slots": o.get("slots")}
        for o in objects
        if o.get("slots") and int(o.get("vgroups") or 0) != int(o["slots"])
    ]
    for row in bad_groups:
        reds.append(
            Red(
                "C3-骨槽",
                f"{row['object']} 的顶点组 {row['vgroups']} 个 != 它的骨槽 {row['slots']} 个",
            )
        )
    if slots and bones + empty_slots < slots:
        reds.append(
            Red(
                "C3-骨槽",
                f"骨 {bones} + 空槽 {empty_slots} < 槽表 {slots}"
                f"（丢了 {slots - bones - empty_slots} 个）",
            )
        )
    if slots and bones == 0 and not rep.get("rig_error"):
        reds.append(Red("C3-骨槽", f"槽表 {slots} 个却一根骨都没建，也没报错"))
    readings["rig"] = {"slots": slots, "empty_slots": empty_slots, "bones": bones,
                       "bad_groups": bad_groups, "rig_summary": rep.get("rig_summary") or ""}

    return UnitVerdict(unit=unit, reds=tuple(reds), warns=tuple(warns), readings=readings)


def load_records(path: str | Path) -> dict[str, dict[str, Any]]:
    """读探针落的那份 JSON（件名 -> 读数）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 不是「件名 -> 读数」的形状")
    return {str(k): dict(v) for k, v in raw.items()}


def evaluate_all(
    records: dict[str, dict[str, Any]],
    *,

    known_keys: tuple[str, ...] = (),
    thresholds: Thresholds | None = None,
) -> list[UnitVerdict]:
    """逐件判，按件名排序。"""
    return [
        evaluate(records[key], known_keys=known_keys, thresholds=thresholds)
        for key in sorted(records)
    ]

# --------------------------------------------------------------------------- #
# 自检夹具（正控 = 人工注入的红「必须报出来」，负控 = 真绿件「不许误伤」）
# --------------------------------------------------------------------------- #


def _good_record(unit: str = "RU_T72B") -> dict[str, Any]:
    """一份形状完整的**绿**夹具（照真件 RU_T72B 的读数形状写）。"""
    levels = [[101, 102, 103], [201, 202], [301]]
    rends = []
    names = {
        101: "body", 102: "track_L", 103: "track_R",
        201: "000_skinned_body", 202: "lod_1_track_L",
        301: "001_skinned_body",
    }
    for cid, name in names.items():
        rends.append({"component_id": cid, "name": name, "kind": "SkinnedMeshRenderer",
                      "node": name, "mesh_id": cid, "mesh_file": 0, "slots": 4, "empty_slots": 1})
    objects = [
        {"name": "body", "verts": 1000, "dims": [7.0, 3.0, 2.4], "vgroups": 4, "slots": 4},
        {"name": "track_L", "verts": 400, "dims": [6.8, 0.6, 0.9], "vgroups": 4, "slots": 4},
        {"name": "track_R", "verts": 400, "dims": [6.8, 0.6, 0.9], "vgroups": 4, "slots": 4},
        {"name": "000_skinned_body", "verts": 300, "dims": [7.0, 3.0, 2.4],
         "vgroups": 4, "slots": 4},
        {"name": "lod_1_track_L", "verts": 100, "dims": [6.8, 0.6, 0.9], "vgroups": 4, "slots": 4},
        {"name": "001_skinned_body", "verts": 80, "dims": [7.0, 3.0, 2.4],
         "vgroups": 4, "slots": 4},
    ]
    return {
        "unit_key": unit,
        "report": {"renderers": 6, "lod_levels": 3, "lod0_members": 3, "lod_union": 6,
                   "lod_outside": 0, "lod_hidden": 3, "errors": [], "bones": 3,
                   "rig_summary": "骨架 3 根骨", "rig_error": ""},
        "lod": {"levels": levels, "renderers": rends},
        "objects": objects,
        "images": [f"{unit}__{unit.lower()}_hull_d", "common_track_n"],
        "unit_bbox": [7.0, 3.0, 2.4],
        "counts": {"renderer_objects": 6, "mesh_objects": 6},
        "rig": {"slots": 4, "empty_slots": 1, "bones": 3},
    }


def _mutations(record: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], str]]:
    """对**任意**记录做人工注入：(行号, 标题, 注入后的记录, 期望命中的码)。

    这是我给这把尺子做的**负控**：把已知好的件伪造一条违规，尺子必须报得出来。
    """
    out: list[tuple[str, str, dict[str, Any], str]] = []

    # P1 同一 LOD 链上把一个替身缩 39.028 倍（US_AH-1W 的真实倍数）
    rec = json.loads(json.dumps(record))
    targets = [o for o in rec["lod"]["renderers"] if lod_lookalike(str(o["name"]))]
    key = part_key(str(targets[0]["name"])) if targets else None
    done = False
    for obj in rec.get("objects") or []:
        if key and part_key(str(obj["name"])) == key and lod_lookalike(str(obj["name"])):
            obj["dims"] = [max(0.05, float(x) / 39.028) for x in obj["dims"]]
            done = True
            break
    if done:
        out.append(("P1", "同一 LOD 链上的替身被缩小 39.028 倍", rec, "R2-跨档"))

    # P2 名字像 LOD 替身的件被判成「一律可见」（从所有 LOD 档里摘出来）
    rec = json.loads(json.dumps(record))
    look = [r for r in rec["lod"]["renderers"] if lod_lookalike(str(r["name"]))]
    if look:
        cid = int(look[0]["component_id"])
        rec["lod"]["levels"] = [[c for c in lv if int(c) != cid] for lv in rec["lod"]["levels"]]
        before = rec["report"]
        before["lod_union"] = len({int(c) for lv in rec["lod"]["levels"] for c in lv})
        before["lod0_members"] = len(rec["lod"]["levels"][0])
        before["lod_outside"] = int(before.get("lod_outside") or 0) + 1
        before["lod_hidden"] = max(0, int(before.get("lod_hidden") or 0) - 1)
        out.append(("P2", "LOD 替身被判「一律可见」", rec, "R1-LOD替身"))

    # P3 记数对不上（树里有一个 outside，report 说 0）
    rec = json.loads(json.dumps(record))
    rec["report"]["lod_outside"] = 99
    out.append(("P3", "report.lod_outside 与树对不上", rec, "R1-记数"))

    # P4 异物贴图（别单位的图名）
    rec = json.loads(json.dumps(record))
    rec["images"] = list(rec.get("images") or []) + ["US_M1A2_SEP__us_m1a2_hull_d"]
    out.append(("P4", "材质上接了别单位的图", rec, "R3-异物图"))

    # P5 686 m 的车（真实读数：131.631 x 686.607 x 162.787）
    rec = json.loads(json.dumps(record))
    rec["unit_bbox"] = [131.631, 686.607, 162.787]
    out.append(("P5", "件级 bbox 686 m", rec, "R4-bbox"))

    return out


@dataclass(frozen=True, slots=True)
class Run:
    """一次自检的读数（注入的必须红、真绿件必须绿）。"""

    rows: tuple[tuple[str, str, str, bool], ...]
    positive_ok: int
    negative_ok: int
    positive_total: int
    negative_total: int

    @property
    def rc(self) -> int:
        good = self.positive_ok == self.positive_total and self.negative_ok == self.negative_total
        return 0 if good else 1


def selftest(
    *,
    known_keys: tuple[str, ...] = (),
    good_records: dict[str, dict[str, Any]] | None = None,
) -> Run:
    """跑自检：**注入的红必须报出来 + 已知绿件不许误伤**。

    :param good_records: 传真件读数（例 `{RU_T72B: ...}`）就把注入打在**真件**上；
        不传就用内置的合成绿夹具。
    """
    rows: list[tuple[str, str, str, bool]] = []
    pos_ok = pos_total = 0
    neg_ok = neg_total = 0

    targets = dict(good_records) if good_records else {"RU_T72B": _good_record()}
    for unit, base in sorted(targets.items()):
        # 负控：真件本身必须绿
        verdict = evaluate(base, known_keys=known_keys)
        neg_total += 1
        neg_ok += 1 if verdict.ok else 0
        rows.append((f"N-{unit}", f"负控·真件 {unit} 必须绿", verdict.line(), verdict.ok))
        # 正控：在真件上人工注入 5 条
        for row_id, title, mutated, want in _mutations(base):
            mut = evaluate(mutated, known_keys=known_keys)
            hit = any(want in r.rule for r in mut.reds)
            pos_total += 1
            pos_ok += 1 if hit else 0
            rows.append((f"P{row_id[1:]}-{unit}", f"正控·{title}", mut.line(), hit))
        # 旋钮读不读它：把跨档阈值临时改到 0.5，绿件必须变红
        knob = evaluate(base, known_keys=known_keys, thresholds=Thresholds(cross_lod_ratio=0.5))
        got = any(r.rule.startswith("R2") for r in knob.reds)
        pos_total += 1
        pos_ok += 1 if got else 0
        rows.append((f"K-{unit}", "正控·跨档阈值临时改小到 0.5 必须报红", knob.line(), got))
    return Run(rows=tuple(rows), positive_ok=pos_ok, negative_ok=neg_ok,
               positive_total=pos_total, negative_total=neg_total)


def markdown_table(verdicts: list[UnitVerdict]) -> str:
    """逐件四条判据读数表（报告里直接贴）。"""
    head = (
        "| 件 | R1 LOD（级/并集/LOD0/外/隐） | R2 跨档比 max | R2 中位/最大 m |"
        " R3 图 异物 | R4 bbox max m | C1 err | C2 renderer | C3 槽 | 判定 |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for v in verdicts:
        r = v.readings
        lod = r.get("lod", {})
        size = r.get("size", {})
        tex = r.get("textures", {})
        bbox = r.get("bbox", {})
        cnt = r.get("counts", {})
        rig = r.get("rig", {})
        cross = size.get("cross_lod") or []
        ratio = f"{max(x['ratio'] for x in cross):.3f}" if cross else "—"
        errs = len(r.get("errors") or [])
        lines.append(
            f"| {v.unit} | {lod.get('levels')}/{lod.get('union')}/{lod.get('lod0')}/"
            f"{lod.get('outside')}/{lod.get('hidden')} | {ratio} | "
            f"{size.get('median_max_dim')}/{size.get('max_dim')} | "
            f"{tex.get('images')} / {len(tex.get('foreign') or [])} | {bbox.get('max')} | "
            f"{errs} | {cnt.get('objects')}=={cnt.get('tree')} | "
            f"{rig.get('slots')}槽/{rig.get('empty_slots')}空/{rig.get('bones')}骨 | "
            f"{'绿' if v.ok else '红'} |"
        )
    return "\n".join(lines)

# --------------------------------------------------------------------------- #
# 出处（tests/test_kb_citations.py 要求的「能找回去的指针」）：
#   kb/19-唯一判据与唯一入口.md（「换件尺子」属于判据层，口径与已作废尺子都在这一页）
# --------------------------------------------------------------------------- #
