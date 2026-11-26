r"""一辆车的**可走图**：一次开包 → 物化出「这一辆」的全部记录（⛔ 不 import bpy）。

## 为什么要有它（这一层解决的是「谁是权威」）

整改前：`importer` / `materials` / `xform_entry` / `effects_entry` /
`panels_skins` 各自**开一次包**、各自推一遍子集。推法一分叉，用户看到的就是
「材质改了炮塔没变」（`kb/18` §5.1 那两次相反观察的根因是结构，不是参数）。

现在：**开包只有一处**（`session.UnitSession`），推子集只有这里一处；
三个阶段看的是**同一个 graph 对象**（开包计数 = 1，见 `tests/test_unit_graph_rulers.py`）。

## 记录集与它对应哪条尺度（⛔ 挂不上尺度的字段先不加）

| 记录 | 内容 | 尺度（`docs/交接-2026-10-18-Blender插件整改.md` §6） |
|---|---|---|
| `identity` | unit_key / asset_path / bundle / bundle_path / bundle_sha | 同一份数据 |
| `roots` | game_root / aa_dir / catalog_path / project_root | 根解析只此一处 |
| `objects` | 节点：name / path_id / transform_id / parent / kinds | 节点数 |
| `renderers` | renderer：name / path_id / kind / lod_level / materials | **组成**（LOD 四级并集） |
| `skins` | 皮肤表里的一条：Id ＋ 材质 -> 渲染器**平行**配对 | **皮肤**（显式名单，炮塔不在里面） |
| `bones` | 骨：name / transform_id / parent / groups | **车轮**（16）/ 悬挂（12）/ 履带（2） |
| `turrets` | 挂载：slot / turret_id / model_filename / 来源 | **炮塔**（3 条 `None` ⇒ 不加载） |
| `lods` | 各级成员 ＋ 五档距离 | **LOD**（50/250/900/3000/10000） |
| `settings` | `AnimationManager._container._settings` 那 7 个 float | **车轮/履带**的速率常数 |

## 三条口径（都是实测钉的，别按直觉改）

1. **`13` 是「四级 LOD 并集」，不是「同屏可见数」**（Q1）：LOD0=7 / LOD1 与 LOD0 共用 3 个
   ⇒ 并集 13。所以 `counts()['lod_union']` 与 `counts()['renderers']` 在 T-72B 上**相同**，
   而 `lod0_members + lod_union 之外的数量` 才等于 7 —— ⛔ 别拿 7 去对 13。
2. **LOD 五档阈值是运行时读到的真值**（Q6 附录 A-1：`BaLodConfig` 实例 5 个 float 全正且严格递增
   的唯一一组）。⚠ 交接文档 §6 写的落点 `look_stage` 是错的：`look_stage` 只管渲染/观感预设，
   LOD 距离**没有**第二个出处 —— 唯一的落点是本文件 `LOD_DISTANCES`
   （更正记在 `_work/game-study/Q6-LOD与可见性.md`）。
3. **车轮转角本身纯只读读不到**（`UnityEngine.Transform` 在 `dump.cs` 里零 field）⇒ 本文件只给
   「公式 ＋ 参数」，⛔ 不声称验过「屏幕上转了多少」。

## 判据一行

    python _work/game-study/m0_graph_probe.py -> REPORT_COUNTS == GRAPH_COUNTS（逐字段）
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import anim_params, prefab_skins
from ba_mod_maker.asset_kernel import prefab as prefab_mod

from . import rig_plan

__all__ = [
    "LOD_DISTANCES",
    "TRACK_SCROLL_PER_METRE",
    "WHEEL_PREFIX",
    "SUSPENSION_PREFIX",
    "TRACK_NEEDLES",
    "GraphBone",
    "GraphIdentity",
    "GraphLods",
    "GraphObject",
    "GraphRenderer",
    "GraphRoots",
    "GraphSkin",
    "GraphTurret",
    "UnitGraph",
    "attach_turrets",
    "build_graph",
    "classify_bones",
    "script_class_names",
    "track_uv_slope",
    "wheel_degrees_per_metre",
    "wheel_radius",
]

#: LOD 五档阈值（米，运行时真值）。出处：`_work/game-study/Q6-LOD与可见性.md` 附录 A-1
#: （`BaLodConfig` 实例 `0x1fcaa2fadc0`，128 个候选里**唯一**满足「5 个 float 全正且严格递增」
#: 的那个）。
#: ⛔ 改这五个数 = 换尺度；要改先重跑那次运行时读数。
LOD_DISTANCES: tuple[float, ...] = (50.0, 250.0, 900.0, 3000.0, 10000.0)

#: 履带 UV 每米的滚动量 —— 游戏 `Tracks.Move`：`scroll += (speed+turn)*delta*0.55*trackSpeed`
#: （`_work/isil/IsilDump/.../Client/Ecs/Animations/Tracks.txt:531`）。
#: 实测（Q2 附录 B-6，6 段速度严格 12.5 的区段最小二乘）：斜率 −13.75 ± 0.2%，
#: 而 `0.55 * TracksSpeed(2.0) * 12.5 = 13.75` ⇒ 两把尺子对上了。
#: ⚠ **`turn` 那一项测不到**（Q2 附录 B-6 段末：turn 从 −0.50 到 +0.59，斜率极差 <0.4%）
#: ⇒ 本函数**只给 speed 那一路**，别把它写成 `(speed+turn)`。
TRACK_SCROLL_PER_METRE = 0.55

#: game 那两条骨名谓词用的前缀（⛔ 逐字，不是我们定的）：
#: `Wheels_NestedType___c.txt:109` · `Suspension_NestedType___c.txt:109` ·
#: `Tracks_NestedType___c.txt:96`。
WHEEL_PREFIX: tuple[str, str] = ("LR0", "RR0")
SUSPENSION_PREFIX: tuple[str, str] = ("L0", "R0")
TRACK_NEEDLES: tuple[str, str] = ("trackl", "trackr")


# --------------------------------------------------------------------------- #
# 记录（全是纯数据：能 JSON 落盘、能进用例、⛔ 不带任何 bpy 对象）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GraphRoots:
    """四个根 —— **只此一处解析**（会话解析一次，图记住它）。"""

    game_root: Path | None = None
    aa_dir: Path | None = None
    catalog_path: Path | None = None
    project_root: Path | None = None

    @property
    def aa_pc(self) -> Path | None:
        return None if self.aa_dir is None else self.aa_dir / "PC"


@dataclass(frozen=True, slots=True)
class GraphIdentity:
    """这一辆车是谁（开哪一份数据、那份数据是哪一版）。"""

    unit_key: str = ""
    asset_path: str = ""
    bundle: str = ""
    bundle_path: str = ""
    #: 包文件 sha256 —— 「三方看的是不是同一份数据」的机械判据（换包 = 换读数）。
    bundle_sha: str = ""
    #: 容器里那条地址的根 GameObject pathID（`UnitPrefabRoot` 所在的树根）。
    container_root: int = 0
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphObject:
    """一个节点（GameObject ＋ 它的 Transform）。"""

    name: str
    path_id: int
    transform_id: int
    parent_id: int | None
    depth: int
    path: str
    kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphRenderer:
    """一个 renderer（SMR / MR）＋ 它落在哪一级 LOD。"""

    name: str
    path_id: int
    kind: str
    node_id: int
    mesh_id: int | None
    mesh_file: int
    material_ids: tuple[int, ...]
    #: 在哪一级 LOD 里（0..n）；`None` = **不在任何 LOD 级里**（一律可见，不是 LOD 变体）。
    lod_level: int | None
    #: 网格是不是**本包**里的（外部网格 = 只能建占位，`ImportReport.placeholder_objects`）。
    mesh_local: bool

    @property
    def is_placeholder(self) -> bool:
        """建不出网格 = 外部引用 / 没有网格（⇐ 与 `importer._make_renderer_object` 同口径）。"""
        return self.mesh_id is None or not self.mesh_local


@dataclass(frozen=True, slots=True)
class GraphSkin:
    """皮肤表里的一条：`_keyData[i]`（材质）与 `_valueData[i].Renderers` 是一对。"""

    skin_id: int
    material_ids: tuple[int, ...]
    renderers: tuple[tuple[int, ...], ...]

    def renderer_ids(self) -> tuple[int, ...]:
        """这一条皮肤管到的**全部**渲染器（去重保序）—— 尺子量它。"""
        out: list[int] = []
        for group in self.renderers:
            for rid in group:
                if rid not in out:
                    out.append(int(rid))
        return tuple(out)


@dataclass(frozen=True, slots=True)
class GraphBone:
    """一根骨 ＋ 它按 game 谓词落在哪一族。

    族名：`wheels_left` / `wheels_right` / `suspension` / `track_left` / `track_right` / `other`。
    """

    name: str
    transform_id: int
    parent_id: int | None
    group: str


@dataclass(frozen=True, slots=True)
class GraphTurret:
    """挂载列表里的一条（游戏口径；`model_filename` 为空 = **不加载**，没有回退）。"""

    slot: int
    turret_id: int
    name: str
    model_filename: str
    is_default: bool
    source: str
    hardpoint: str


@dataclass(frozen=True, slots=True)
class GraphLods:
    """LOD 分解 ＋ 五档阈值（阈值来自 `LOD_DISTANCES`，不是从包里猜的）。"""

    levels: tuple[tuple[int, ...], ...] = ()
    size: float | None = None
    distances: tuple[float, ...] = LOD_DISTANCES


@dataclass(slots=True)
class UnitGraph:
    """一辆车的图 —— 三个阶段（importer / materials / turret_entry）看的是**它**。

    ⚠ `tree` / `source` 是**读侧句柄**（`PrefabTree` ＋ 已经开着的 `AssetSource`）：
    网格 / 贴图 / 字段树那几条只能从主语料里读的操作仍然要用它们，
    ⛔ 但**再开一次包**是不允许的（`open_count` 必须停在 1）。
    """

    identity: GraphIdentity = field(default_factory=GraphIdentity)
    roots: GraphRoots = field(default_factory=GraphRoots)
    objects: tuple[GraphObject, ...] = ()
    renderers: tuple[GraphRenderer, ...] = ()
    skins: tuple[GraphSkin, ...] = ()
    bones: tuple[GraphBone, ...] = ()
    #: 名册（`TurretUnits` 的每一行；`model_filename` 空 = **不加载模型**，没有回退）。
    turrets: tuple[GraphTurret, ...] = ()
    #: **游戏口径**的挂载列表（= `CardData.card_turrets` 那几颗 turret_id，按槽位顺序）。
    #: ⚠ 与 `turrets` 不是同一个量：T-72B 名册 6 行、挂载列表 1 条。
    turret_mounts: tuple[int, ...] = ()
    lods: GraphLods = field(default_factory=GraphLods)
    settings: dict[str, float] = field(default_factory=dict)
    #: `only_lod0=True` 时**参与建场景**的那批（默认 = 全部 renderer）。
    targets: tuple[GraphRenderer, ...] = ()
    #: 读侧句柄（⚠ 序列化不进 JSON：用例只读 counts / 记录）。
    tree: prefab_mod.PrefabTree | None = None
    source: Any = None
    #: `UnitPrefabRoot` / `AnimationManager` 的 pathID（读数 ＋ 写回用）。
    unit_prefab_root_id: int = 0
    animation_manager_id: int = 0
    #: 皮肤表 / 参数族读不通时的一句人话（⛔ 不静默当「没有」）。
    skin_note: str = ""
    settings_note: str = ""
    #: 可瞄武器朝向校正的读数（rig_plan.AimPlan）—— None = 没做校正（原样）。
    #: ⛔ 这是**校正**，不是 prefab 原样：读数要一路带到面板 / ImportReport 去。
    aim: Any = None
    #: MonoBehaviour 组件 pathID -> 脚本类名。
    #: 认 `UnitPrefabRoot` / `AnimationManager` 只靠这张表（⛔ 不靠组件顺序 / 不靠名字）。
    script_classes: dict[int, str] = field(default_factory=dict)

    # ---- 计数（= `ImportReport` 那些数，逐字段同名） ----
    def counts(self) -> dict[str, int]:
        """与 `ImportReport` **逐字段同名**的计数 —— M0 的对拍就比这张表。"""
        return counts_of(self)

    # ---- 查询 ----
    def renderer(self, path_id: int) -> GraphRenderer | None:
        for item in self.renderers:
            if item.path_id == int(path_id):
                return item
        return None

    def bone_names(self) -> tuple[str, ...]:
        return tuple(bone.name for bone in self.bones)

    def bones_in(self, group: str) -> tuple[GraphBone, ...]:
        return tuple(bone for bone in self.bones if bone.group == group)


# --------------------------------------------------------------------------- #
# 车轮：半径 <-> 游戏那份 `WheelSpeed`（⛔ 公式只有这一份，anim_drive 从这里转发）
# --------------------------------------------------------------------------- #
def wheel_degrees_per_metre(radius: float) -> float:
    """米 -> 度/米（就是游戏那份 `WheelSpeed`）：`WheelSpeed = 180 / (pi * radius)`。

    依据：`Transform.Rotate` 吃的是**度**，所以「每米转多少度」本身就是那个常量
    （`_work/isil/.../Client/Ecs/Animations/Wheels.txt:986`；真件 T-72B = 70.0）。
    ⛔ 本函数不做校验（负控 / 除零留给调用方那一层报人话）；纯数学只此一份。
    """
    return 180.0 / (math.pi * float(radius))


def wheel_radius(wheel_speed: float) -> float:
    """度/米 -> 米（上一式反解）：`radius = 180 / (pi * WheelSpeed)`。"""
    return 180.0 / (math.pi * float(wheel_speed))

# --------------------------------------------------------------------------- #
# 骨名谓词（⛔ 逐字照抄 game；本文件是**唯一**实现，anim_drive 从这里转发）
# --------------------------------------------------------------------------- #
def classify_bones(
    names: Sequence[str],
    *,
    left_prefix: str = WHEEL_PREFIX[0],
    right_prefix: str = WHEEL_PREFIX[1],
    width: int = 3,
) -> dict[str, list[str]]:
    """按 game 的谓词挑骨（`Substring(0,width)` ＋ `Length > width`）。

    返回 `{"left": [...], "right": [...], "neither": [...]}`，**保持输入顺序**、去重。
    ⛔ 不许「顺手」放宽大小写或改成 `startswith` —— 真件里 `RU_TanktrakL` 正是靠
    大小写敏感才落不进车轮那两族。
    """
    left: list[str] = []
    right: list[str] = []
    neither: list[str] = []
    for raw in names:
        name = str(raw)
        if name in left or name in right:
            continue
        head = name[:width]
        if len(name) > width and head == left_prefix:
            left.append(name)
        elif len(name) > width and head == right_prefix:
            right.append(name)
        else:
            neither.append(name)
    return {"left": left, "right": right, "neither": neither}


def _bone_group(name: str) -> str:
    """一根骨属于哪一族（game 的三条谓词；只做读数，⛔ 不参与「能不能转」的判断）。"""
    text = str(name)
    groups = classify_bones([text], left_prefix=WHEEL_PREFIX[0], right_prefix=WHEEL_PREFIX[1])
    if groups["left"]:
        return "wheels_left"
    if groups["right"]:
        return "wheels_right"
    lower = text.lower()
    if TRACK_NEEDLES[0] in lower:
        return "track_left"
    if TRACK_NEEDLES[1] in lower:
        return "track_right"
    susp = classify_bones(
        [text], left_prefix=SUSPENSION_PREFIX[0], right_prefix=SUSPENSION_PREFIX[1], width=2
    )
    if susp["left"] or susp["right"]:
        return "suspension"
    return "other"


def track_uv_slope(speed: float, track_speed: float, turn: float = 0.0) -> float:
    """履带材质 UV 的滚动斜率（**每秒**，UV 单位）—— 游戏那条 `Tracks.Move` 的唯一算式。

    实测（Q2 附录 B-6）：speed=12.5 / TracksSpeed=2.0 ⇒ **−13.75 UV/秒**（6 段最小二乘，
    误差 ≤0.2%）。换算成「每米」要再除以 speed ⇒ −0.55 × TracksSpeed = **−1.1**（2026-10-18
    在 `runtime/turn-163914.csv` 上用「位移」当横轴复算，正好量到 −1.1002 ⇒ 两把尺子互证）。
    ⚠ `turn` 传进来只进注释、不进公式：真机 6 段里 turn 变化对斜率的影响 <0.4%
    （若真是 `(speed+turn)` 应分别给出 −13.20 / −14.41，两个都没出现）。
    """
    _ = turn  # 见 docstring：这一项测不到，⛔ 不许悄悄加进公式
    return -float(TRACK_SCROLL_PER_METRE) * float(track_speed) * float(speed)


# --------------------------------------------------------------------------- #
# 从一次开包填充
# --------------------------------------------------------------------------- #
def _sha256_of(path: str | Path) -> str:
    """整文件 sha256（按块读 —— 3.4 GB 的包也不吃内存）。"""
    target = Path(path)
    if not target.is_file():
        return ""
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def script_class_names(source: Any, nodes: Sequence[Mapping[str, object]]) -> dict[int, str]:
    """MonoBehaviour 组件的 pathID -> 我们认得出的那一类脚本（按**字段**认，⛔ 不猜）。

    ⚠ 2026-10-18 实测推翻的第一版：原来想读 `m_Script` 指向的那颗 MonoScript 的 `m_ClassName`。
    **不行** —— prefab 里 `m_Script` 的 PPtr 指到 **monoscripts 包**里，那些 pathID 在**本包**
    不存在（`read_typetree` 抛「这个包里没有 pathID=…」，T-72B 实测 4/4 全抛）。
    产品自己也是按**字段**认的，本函数照抄那两条判据（⛔ 不另立一套）：

    * `UnitPrefabRoot` ⇒ 字段里**同时**有 `skin_install.SKIN_ROOT_FIELDS`
      （`Skins` / `PrefabRoot`）；
    * `AnimationManager` ⇒ 字段里有 `anim_params.SETTINGS_STEPS[0]`（`_container`）。

    ⚠ 代价：要读每颗 Mono 的字段树（T-90M 有一颗实测 5.63 s）。所以**同一颗脚本只读一次**
    （按 `m_Script` 的 pathID 去重）—— 读不出来就跳过那颗（不猜、不静默报成「没有」）。
    """
    from ba_mod_maker.asset_kernel import skin_install

    root_fields = tuple(skin_install.SKIN_ROOT_FIELDS)
    container_field = anim_params.SETTINGS_STEPS[0]
    out: dict[int, str] = {}
    by_script: dict[int, str] = {}
    for node in nodes:
        for comp in node.get("components") or ():
            if str(comp.get("kind", "")) != "MonoBehaviour":
                continue
            comp_id = int(comp.get("path_id", 0) or 0)
            script_id = int(comp.get("script", 0) or 0)
            if not comp_id:
                continue
            if script_id in by_script:
                name = by_script[script_id]
                if name:
                    out[comp_id] = name
                continue
            try:
                tree = dict(source.read_typetree(comp_id) or {})
            except Exception:  # noqa: BLE001 -- 单颗读不出来不废整张图
                continue
            name = ""
            if all(key in tree for key in root_fields):
                name = "UnitPrefabRoot"
            elif container_field in tree:
                name = "AnimationManager"
            by_script[script_id] = name
            if name:
                out[comp_id] = name
    return out




def _renderer_groups(slot: Any, row: Any) -> tuple[tuple[int, ...], ...]:
    """一条皮肤管到哪些渲染器 —— 两种**序列化形态**都认（2026-10-18 实测）。

    1. `Skins.Data[i].Data._valueData[j].Renderers`（`prefab_skins.skin_slots` 读的那份）；
    2. `Skins.Data[i].skinsSerializedRefs._valueData[j].Renderers`（序列化引用形态）。

    ⚠ T-72B 实测：8 条皮肤的形态 1 **全是空**（`_keyData` / `_valueData` 长度都是 0），
    真正那份 **8 个渲染器**的名单在形态 2 里。只看形态 1 会把读数变成「每条 0 个」，
    而 `kb/18` §5.1 的 8 个渲染器是**真机**看到的（运行期就是要它）⇒ 两种都读。
    """
    groups = tuple(tuple(int(r) for r in group) for group in slot.renderers)
    if any(groups):
        return groups
    refs = (row or {}).get("skinsSerializedRefs") or {}
    values = refs.get("_valueData") or ()
    out: list[tuple[int, ...]] = []
    for item in values:
        renders = (item or {}).get("Renderers") or ()
        out.append(
            tuple(
                int(ptr.get("m_PathID"))
                for ptr in renders
                if isinstance(ptr, Mapping) and ptr.get("m_PathID")
            )
        )
    return tuple(out)


def _skins_of(source: Any, uroot_id: int) -> tuple[GraphSkin, ...]:
    """`UnitPrefabRoot.Skins.Data` -> `GraphSkin` 元组（读法只有一份：`prefab_skins.skin_slots`）。

    ⛔ 不自己解那张字典：平行数组（`_keyData` / `_valueData`）的对齐规则在 `prefab_skins` 里。
    """
    if not uroot_id:
        return ()
    tree = dict(source.read_typetree(int(uroot_id)) or {})
    slots = prefab_skins.skin_slots(tree)
    rows = ((tree.get("Skins") or {}).get("Data")) or ()
    return tuple(
        GraphSkin(
            skin_id=int(slot.skin_id),
            material_ids=tuple(int(m) for m in slot.materials),
            renderers=_renderer_groups(slot, rows[index] if index < len(rows) else {}),
        )
        for index, slot in enumerate(slots)
    )


def _settings_of(source: Any, manager_id: int) -> dict[str, float]:
    """`AnimationManager._container._settings` 那 7 个 float。

    读法只有一份：`anim_params.read_family`（名字表在 `unit_script_shapes`）。
    """
    if not manager_id:
        return {}
    tree = dict(source.read_typetree(int(manager_id)) or {})
    reading = anim_params.read_family(tree, path_id=int(manager_id))
    # ⚠ `FamilyReading.values` 是**三元组**（`name` / 值 / 类型），不是二元对 —— 2026-10-18
    #   第一版写成 `dict(reading.values)` 当场 `ValueError: dictionary update sequence ...`。
    return {
        str(name): float(value)
        for name, value, _kind in reading.values
        if value is not None
    }


def build_graph(
    source: Any,
    asset_path: str,
    *,
    unit_key: str = "",
    bundle: str = "",
    bundle_path: str | Path = "",
    roots: GraphRoots | None = None,
    only_lod0: bool = False,
    instance: Any = None,
    unit_id: int | None = None,
    with_sha: bool = False,
    settings_manager_id: int | None = None,
    align_aimable: bool = False,
    scripts: Mapping[int, str] | None = None,
) -> UnitGraph:
    """**一次**走查把一辆车物化出来（开包归调用方；本函数⛔ 不开关包）。

    :param source: 已经开着的 `AssetSource` —— 会话给的是**同一个**对象（开包计数 = 1）。
    :param asset_path: 容器里的地址键。
    :param instance: 给了数据源就顺手挂炮塔（`attach_turrets`）；不给就空着并留 note。
    :param with_sha: True = 算包 sha256（对拍 / 补丁柜要；一个 3.4 GB 的包约 3~6 s）。
    """
    nodes, materials = source.read_prefab_nodes(asset_path)
    # ---- 第 0 步：可瞄武器朝向校正（★ 2026-10-15；图从这棵树折出来，所以先校再建树）----
    aim = None
    if align_aimable:
        nodes, aim = rig_plan.align_aim_nodes(nodes, unit_key=str(unit_key))
    tree = prefab_mod.build_tree(nodes, materials)
    container = {str(addr): int(pid) for addr, pid in source.container_entries()}
    container_root = int(container.get(str(asset_path), 0) or 0)

    identity = GraphIdentity(
        unit_key=str(unit_key),
        asset_path=str(asset_path),
        bundle=str(bundle),
        bundle_path=str(bundle_path),
        bundle_sha=_sha256_of(bundle_path) if with_sha and bundle_path else "",
        container_root=container_root,
    )

    objects = tuple(
        GraphObject(
            name=node.name,
            path_id=int(node.path_id),
            transform_id=int(node.transform_id),
            parent_id=None if node.parent_id is None else int(node.parent_id),
            depth=int(node.depth),
            path=node.path,
            kinds=tuple(node.kinds),
        )
        for node in tree.nodes
    )

    lod_of: dict[int, int] = {}
    for level, members in enumerate(tree.lods):
        for cid in members:
            lod_of.setdefault(int(cid), level)

    renderers = tuple(
        GraphRenderer(
            name=item.name,
            path_id=int(item.component_id),
            kind=str(item.kind),
            node_id=int(item.node_id),
            mesh_id=None if item.mesh_id is None else int(item.mesh_id),
            mesh_file=int(item.mesh_file),
            material_ids=tuple(int(m) for m in item.material_ids),
            lod_level=lod_of.get(int(item.component_id)),
            mesh_local=int(item.mesh_file) == 0,
        )
        for item in tree.renderers
    )
    # `only_lod0=True` = LOD0 **＋ 不在任何 LOD 级里的那些**（它们不是 LOD 变体，一律可见）。
    # ⚠ 只取 `lod_level == 0` 是错的：AH-1Z 有 6 个 renderer 不在任何 LODGroup 里，
    #   那样一刀切下去，导入只剩一个飞行员（真机踩过，见 importer 里 always_visible 那段）。
    targets = (
        tuple(r for r in renderers if r.lod_level in (0, None))
        if only_lod0
        else renderers
    )

    classes = dict(scripts) if scripts is not None else script_class_names(source, nodes)
    uroot_id = next((pid for pid, name in classes.items() if name == "UnitPrefabRoot"), 0)
    manager_id = (
        int(settings_manager_id)
        if settings_manager_id
        else next(
            (pid for pid, name in classes.items() if name == "AnimationManager"), 0
        )
    )

    skin_note = ""
    try:
        skins = _skins_of(source, uroot_id) if uroot_id else ()
        if not uroot_id:
            skin_note = "prefab 上没有 UnitPrefabRoot ⇒ 皮肤表读不到（⛔ 不是「没有皮肤」）"
    except Exception as exc:  # noqa: BLE001 -- 读不通要说出来，⛔ 不许静默当空
        skins = ()
        skin_note = f"皮肤表读不通：{type(exc).__name__}: {exc}"

    settings_note = ""
    try:
        settings = _settings_of(source, manager_id) if manager_id else {}
        if manager_id and not settings:
            settings_note = "AnimationManager 上读不到 _container._settings"
        elif not manager_id:
            settings_note = "prefab 上没有 AnimationManager ⇒ 参数族读不到"
    except Exception as exc:  # noqa: BLE001
        settings = {}
        settings_note = f"参数族读不通：{type(exc).__name__}: {exc}"

    bones: list[GraphBone] = []
    seen: set[int] = set()
    for item in tree.renderers:
        for bone_id in item.bone_ids:
            if not bone_id or int(bone_id) in seen:
                continue
            seen.add(int(bone_id))
            node = tree._by_transform.get(int(bone_id))  # noqa: SLF001 -- 读数：骨名同一棵树
            if node is None:
                continue
            bones.append(
                GraphBone(
                    name=node.name,
                    transform_id=int(node.transform_id),
                    parent_id=None if node.parent_id is None else int(node.parent_id),
                    group=_bone_group(node.name),
                )
            )

    graph = UnitGraph(
        identity=identity,
        roots=roots or GraphRoots(),
        objects=objects,
        renderers=renderers,
        skins=skins,
        bones=tuple(bones),
        lods=GraphLods(levels=tuple(tuple(m) for m in tree.lods), size=tree.lod_size),
        settings=dict(settings),
        targets=targets,
        tree=tree,
        source=source,
        unit_prefab_root_id=int(uroot_id),
        animation_manager_id=int(manager_id),
        skin_note=skin_note,
        settings_note=settings_note,
        aim=aim,
        script_classes=dict(classes),
    )
    if instance is not None:
        attach_turrets(graph, instance, unit_id=unit_id)
    return graph


def attach_turrets(graph: UnitGraph, instance: Any, *, unit_id: int | None = None) -> UnitGraph:
    """把数据侧那几条挂载挂上图（**就地**改 `graph.turrets`）—— 读法只有一份：`turret_entry`。

    ⛔ 不自己解 `Turrets` / `TurretUnits` / `Options` 三张表的配对规则：
    `turret_entry.effective_mounts` 已经与产品口径 `CardData.card_turrets` 对拍过。
    """
    # 反向依赖：阶段层从本文件拿骨名谓词，所以这里**延迟**回到阶段层（⛔ 模块级 import 会成环）。
    from . import turret_entry

    row = None
    if unit_id is None:
        row = turret_entry.find_unit(instance, unit=graph.identity.unit_key)
        unit_id = int(turret_entry._as_int(row.get("Id")))  # noqa: SLF001
    roster = turret_entry.roster(instance, unit_id)
    mounts = turret_entry.effective_mounts(instance, int(unit_id))
    graph.turrets = tuple(
        GraphTurret(
            slot=index,
            turret_id=int(item.get("turret_id") or 0),
            name=str(item.get("name") or ""),
            model_filename=str(item.get("model_filename") or ""),
            is_default=bool(item.get("is_default")),
            source="名册 TurretUnits#" + str(item.get("link_id") or 0),
            hardpoint="Order=" + str(item.get("order") or 0),
        )
        for index, item in enumerate(roster)
    )
    graph.turret_mounts = tuple(
        int(line.turret_id) for line in mounts
    )
    return graph


def counts_of(graph: UnitGraph) -> dict[str, int]:
    """图上算出来的计数 —— **字段名逐字**等于 `importer.ImportReport` 的那几个数。

    ⚠ 为什么按「图」算而不是「按场景里建出来的对象」算：M0 的判据是
    「两边字典相等」，而场景那一侧要真起 Blender；图这一侧在**任何** python 里都能复算。
    """
    targets = graph.targets or graph.renderers
    material_ids: list[int] = []
    for item in targets:
        for mid in item.material_ids:
            if mid not in material_ids:
                material_ids.append(int(mid))
    lod_union = tuple(graph.tree.lod_union()) if graph.tree is not None else ()
    lod0 = tuple(graph.tree.lod_members(0)) if graph.tree is not None else ()
    mesh_objects = sum(1 for item in targets if not item.is_placeholder)
    return {
        "nodes": len(graph.objects),
        "renderers": len(targets),
        "mesh_objects": mesh_objects,
        "placeholder_objects": len(targets) - mesh_objects,
        "non_renderer_objects": sum(
            1
            for node in graph.objects
            if not any(item.node_id == node.transform_id for item in targets)
        ),
        "materials": len(material_ids),
        # 贴图：按**材质引用到的**去重数（与包里 2725 张全解那件事无关）。
        "textures": len(_texture_ids_of(graph, material_ids)),
        "lod_levels": len(graph.lods.levels),
        "lod0_members": len(lod0),
        "lod_union": len(lod_union),
        "lod_outside": sum(1 for r in graph.renderers if r.lod_level is None),
        "lod_hidden": sum(
            1
            for item in targets
            if item.lod_level is not None and item.lod_level > 0
        ),
    }


def _texture_ids_of(graph: UnitGraph, material_ids: Iterable[int]) -> tuple[int, ...]:
    """这批材质引用到的贴图 pathID（去重保序）—— 走 `PrefabTree.materials` 那张表。"""
    tree = graph.tree
    if tree is None:
        return ()
    out: list[int] = []
    for mid in material_ids:
        entry = tree.materials.get(int(mid)) or {}
        for slot in entry.get("texture_slots") or ():
            tex = slot[1] if isinstance(slot, (tuple, list)) and len(slot) > 1 else None
            if tex and int(tex) not in out:
                out.append(int(tex))
    return tuple(out)
