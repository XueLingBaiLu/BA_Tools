r"""D 线 · Blender 侧落点（**bpy 那一层之外的全部纯数据**）：件 -> clip -> AnimSpec -> 写回计划。

## 对外 API（★ 面板 / 探针 / 用例都走这几条）

    clips_of_unit(game_root, unit_key, *, offline=False) -> UnitAnim
        一个**件**挂了哪些 clip：真包那条路走 prefab 里的 `Animator` -> `m_Controller`
        -> `AnimatorController.m_AnimationClips`；离线路走冻结夹具（按 controller 名对上）。
    open_clip_store(unit, *, offline=False) -> AnimClipStore
        `(ClipStore, 本件那几条 ClipRef)` —— 真包走 `ClipStore.from_bundle`（**我们自己的**
        容器读侧，⛔ 不是 `UnityPy.load` 那 10 GB），离线走 `from_fixture`。
    spec_for(store, path_id, table, chain, allow_partial=True) -> AnimSpec
    reset_candidates / resolve_bone_names / scaled_frames / plan_bones / bone_parents
        **纯函数**（`tests/test_anim_bridge.py` 逐条钉住；⛔ 不许改口径）。
    writeback_argv(bundle, clip, *, k=..., dry_run=True, ...) -> list[str]
        产品 `anim apply` 的**命令行**（本模块不自己拼写盘逻辑）。
    apply_via_cli(argv) -> CliResult
        走**产品自己的入口**：用产品自己的 argparse 建 Namespace，再调 `cli.anim.run()`。

## 为什么这个模块**零 bpy**

`blender_addon/__init__.py` 顶层 `import bpy`（只有 Blender 里有）。本模块能被**按路径加载**，
于是产品 venv 里跑得动 `tests/test_anim_bridge.py`（与 `tests/test_blender_skin.py` 同一姿势）。
碰 bpy 的那一半在 `anim_entry.py` —— 壳与数据分开，是它当初拆出来的理由。

## 实测读数（2026-10-15，无头 Blender，可复算）

| 读数 | 值 |
|---|---|
| 车体 `RU_T72B`（prefab `.../RU/RU_T72B_Base.prefab`） | 52 节点 · 骨架 **30** 根骨 |
|  | （`L001..RR007` ＋ `body`/`root`）· 拿一次 9.2 ~ 9.8 s |
| 炮塔 `RU_T72B/RU_T72B` | 29 个新对象 · 骨架 **11** 根骨（**含 `hatch`/`turret_0`**）· 8.6 s |
| 车体的 clip（controller 名 = `RU_T72B`） | `T72B default`（**空 clip**：0 绑定 / 0 曲线） |
|  | `T72B hatch`（4 曲线 / 1 骨）—— 它在 `m_AnimationClips` 里**重复出现两次** |
| `T72B hatch` 的骨路径 | `root/body/turret_0/hatch` —— 车体骨架上**没有**它，炮塔骨架上有 `hatch` |
| UnityPy 开 units 包到读完 prefab | **6.4 s**（`open_unique_source` + `read_prefab_nodes`） |

★ 上表最后两行是这一跳的**要害**：clip 绑的是 **prefab 节点树**里的路径，不是蒙皮骨表；
所以「挂到骨架上」必须先问**这一串路径在哪个骨架/对象上解得出**（`anim_entry.pick_armature`）。

## 三条硬口径

1. **接不上就抛**（`AnimActionError`）：⛔ 不许硬塞一个名字让 FCurve 指向空气；
2. **不许静默换源**：真包读不动就报，⛔ 不偷偷退到夹具（退源只在 `offline=True` 时发生）；
3. **写盘只有一处**：`writeback_argv` 只拼命令行，落字节归产品 `anim apply`。
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ba_mod_maker.asset_kernel import anim_action, anim_path
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "DEFAULT_FIXTURES",
    "REPO_ROOT",
    "RESEARCH_DIRNAME",
    "research_dir",
    "ROOT_BONE_NAME",
    "AnimActionError",
    "AnimBridgeError",
    "AnimClipStore",
    "BonePlan",
    "ClipRef",
    "CliResult",
    "UnitAnim",
    "apply_via_cli",
    "bone_parents",
    "clips_of_unit",
    "find_node_index",
    "find_path_table",
    "open_clip_store",
    "plan_bones",
    "pptr_file_id",
    "pptr_path_id",
    "read_chain",
    "read_path_table",
    "resolve_bone_names",
    "reset_candidates",
    "scaled_frames",
    "spec_for",
    "unique_refs",
    "writeback_argv",
]

#: 本模块所在的那棵树（装机那份里是 `.../addons/blender_addon`）。
REPO_ROOT = Path(__file__).resolve().parents[1]
#: 研究线产物目录名（`_nodepaths.json` / `_nodeindex.json`）—— 与 ⑨ 面板同一个出处。
#: ★ 2026-10-17（用户裁定）：不在本模块里写死开发机绝对路径，改成**懒解析**（见 `research_dir()`）。
RESEARCH_DIRNAME = "研究-模型与动画"


def research_dir() -> Path | None:
    """研究线产物目录（工作区根下的 `研究-模型与动画/`）—— 解析不到就 `None`。

    ⚠ 惰性 import + 兜住：`panels_common` 顶层 import `bpy`，而本模块**必须**在不带 bpy 的
    环境里也能导（用例与 CLI 都这么用）⇒ ⛔ 不许在本模块顶层 import 它。
    ⛔ 解析器只有 `panels_common` 那一套（这里是**取用**，不是第二份实现）。
    """
    try:
        from . import panels_common as pc

        workspace = pc.workspace_root()
    except Exception:  # noqa: BLE001  —— 没有包上下文 / 没有 bpy：如实当「解析不到」
        return None
    return None if workspace is None else Path(workspace) / RESEARCH_DIRNAME


def _research_hint() -> str:
    """找不到研究线产物时那句**怎么修**（⛔ 别只说「找不到」）。"""
    if research_dir() is None:
        return (
            "找不到工作区 —— 设 BA_MOD_MAKER_WORKSPACE 指到工作区根"
            "（研究线产物在工作区根下的 研究-模型与动画/）"
        )
    return "研究线产物在 研究-模型与动画/ 下（_nodepaths.json / _nodeindex.json）"
#: 冻结夹具（研究线从 units 包冻出来的 475 条 clip / 165 个 controller）。
DEFAULT_FIXTURES = REPO_ROOT / "_work" / "anim-line" / "fixtures"

#: Blender 里那个"整体根骨"的名字（与 `rig_plan.ROOT_BONE_NAME` 同一个口径；
#: `tests/test_anim_bridge.py` 有一条口径锁逐字比对这两份常量）。
ROOT_BONE_NAME = "root"

#: 去重后缀的最大尝试次数（`_1` … `_8`）。
_MAX_DUP_SUFFIX = 8


class AnimBridgeError(AssetError):
    """这一跳**自己**的错（读不到器件 / 没有 Animator / 没有 controller）。"""


class AnimActionError(AssetError):
    """规格落不成 Blender 数据时抛它（⛔ fail-closed：接不上就报，不静默改名）。"""


@dataclass(frozen=True, slots=True)
class ClipRef:
    """一条被这个件引用到的 clip（pathID ＋ 名字）。"""

    path_id: int
    name: str

    def line(self) -> str:
        return f"clip {self.name!r}（pathID={self.path_id}）"


@dataclass(frozen=True, slots=True)
class UnitAnim:
    """**一个件挂了哪些 clip** 的读数（真包那条路 / 离线路共用这一个形状）。"""

    unit_key: str
    source: str
    bundle: str = ""
    asset_path: str = ""
    controller: str = ""
    clips: tuple[ClipRef, ...] = ()
    notes: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        """人读几行（面板摘要与探针看**同一份**）。"""
        head = f"{self.unit_key}：{len(self.clips)} 条 clip · 出处 {self.source}" + (
            f" · controller {self.controller!r}" if self.controller else ""
        )
        out = [head]
        if self.asset_path:
            out.append(f"    件：{self.asset_path}")
        if self.bundle:
            out.append(f"    包：{self.bundle}")
        out.extend(f"    {c.line()}" for c in self.clips)
        out.extend(f"    {n}" for n in self.notes)
        return out


@dataclass(frozen=True, slots=True)
class AnimClipStore:
    """`(ClipStore, 本件那几条 ClipRef)` 的收口 —— 免得调用方到处传两个东西。"""

    store: Any
    refs: tuple[ClipRef, ...]
    unit: UnitAnim


@dataclass(frozen=True, slots=True)
class CliResult:
    """一次产品 CLI 调用的产物（`anim apply` 干跑 / 真写都走它）。"""

    rc: int
    doc: Mapping[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0 and bool(self.doc.get("ok", True))

    def error_line(self) -> str:
        """失败时那一句（点名 **哪个 clip / 哪个包**，⛔ 不许只说"失败了"）。"""
        err = self.doc.get("error")
        if isinstance(err, Mapping):
            hint = err.get("hint")
            text = str(err.get("message") or "")
            return text + (f"（建议：{hint}）" if hint else "")
        tail = [line for line in self.stderr.splitlines() if line.strip()]
        return tail[-1] if tail else f"rc={self.rc}"


# --------------------------------------------------------------------------- #
# 纯函数（★ 用例逐条钉住；⛔ 改口径就是改合同）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BonePlan:
    """一根骨在 Blender 里的落点（路径 -> 名字 + 父名 + 它承载的通道）。"""

    path: str
    name: str
    parent: str | None
    channels: tuple[str, ...]

    def line(self) -> str:
        parent = self.parent if self.parent is not None else "（骨架根）"
        return f"{self.path} -> 骨 {self.name!r}（父 {parent}）· 通道 {list(self.channels)}"


def plan_bones(spec: anim_action.AnimSpec) -> tuple[BonePlan, ...]:
    """把规格里的节点路径排成骨规划（父 = 最近的、也在本 clip 里的祖先）。

    ⛔ 这里只用**路径集合**，不读 prefab 树 —— 所以「中间祖先」不会凭空出现。
    """
    paths = [b.path for b in spec.bones]
    known = set(paths)
    plans: list[BonePlan] = []
    for bone in spec.bones:
        segs = bone.path.split("/")
        parent: str | None = None
        for i in range(len(segs) - 1, 0, -1):
            candidate = "/".join(segs[:i])
            if candidate in known:
                parent = candidate
                break
        plans.append(
            BonePlan(
                path=bone.path,
                name=bone.path.rsplit("/", 1)[-1],
                parent=parent,
                channels=bone.channels,
            )
        )
    return tuple(plans)


def bone_parents(plans: Sequence[BonePlan]) -> dict[str, str | None]:
    """`{骨名: 父骨名}`（值 `None` = 挂骨架根）。骨名重复就抛（Blender 会改名的前兆）。"""
    names = [p.name for p in plans]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise AnimActionError(
            f"骨名重复 {dupes} —— Blender 会给后建的那根自动加 .001，FCurve 就指到别处了",
            hint="先修 rig_plan 的槽名（同一个 prefab 里同名节点要按出现次序加 _1/_2）",
        )
    by_path = {p.path: p.name for p in plans}
    return {p.name: (by_path[p.parent] if p.parent is not None else None) for p in plans}


def reset_candidates(path: str) -> tuple[str, ...]:
    """一条 Unity 路径在 Blender 里**可能**叫什么（按优先级）。

    ⛔ 这是**候选**，不是"猜中的答案"：调用方必须拿骨架的**真骨名表**逐条核对，
    全都不中就是接不上（`resolve_bone_names` 会抛）。
    """
    leaf = str(path).rsplit("/", 1)[-1]
    out = [leaf, str(path).replace("/", "_"), str(path)]
    for i in range(1, _MAX_DUP_SUFFIX + 1):
        out.append(f"{leaf}_{i}")
    seen: list[str] = []
    for name in out:
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def resolve_bone_names(paths: Iterable[str], bone_names: Iterable[str]) -> dict[str, str]:
    """Unity 节点路径 -> Blender 骨名（**逐条核对骨架真骨名**）。

    :raises AnimActionError: 任何一条路径在 `bone_names` 里找不到对应的骨 —— 抛，
        ⛔ 不跳过、不改名（"接不上"必须看得见）。
    """
    have = {str(n): i for i, n in enumerate(bone_names)}
    out: dict[str, str] = {}
    missing: list[str] = []
    for path in paths:
        for cand in reset_candidates(path):
            if cand in have:
                out[str(path)] = cand
                break
        else:
            missing.append(str(path))
    if missing:
        raise AnimActionError(
            f"{len(missing)} 条节点路径在骨架上找不到对应的骨：{missing[:6]}"
            f"（骨架共 {len(have)} 根骨：{sorted(have)[:8]}…）",
            hint="要么这条 clip 绑的这些节点这次没导进来（LAV-M 那类整块接不上），"
            "要么骨名口径变了 —— ⛔ 不许硬塞一个名字让 FCurve 指向空气",
        )
    return out


def scaled_frames(times: Sequence[float], start: float, fps: float) -> tuple[float, ...]:
    """clip 内绝对秒 -> Blender 帧号（`(t - start) * fps`）。

    `fps` 就是「1 秒动画在 Blender 里占几帧」—— 默认按 clip 自己的 `m_SampleRate` 取，
    于是**一个采样点占一帧**（真件 60 Hz ⇒ 1.0000 s 的 clip = 61 帧，帧号 0..60）。
    """
    if fps <= 0:
        raise AnimActionError(f"帧率 {fps!r} 不合法")
    return tuple((float(t) - float(start)) * float(fps) for t in times)


# --------------------------------------------------------------------------- #
# 出处：路径表 / 父链自证（与 ⑨ 面板**同一批候选**，⛔ 不各自写一份）
# --------------------------------------------------------------------------- #
def _research_file(name: str) -> Path | None:
    """研究线那一份（`研究-模型与动画/<name>`）—— 解析不到就 None（候选列表会跳过它）。"""
    base = research_dir()
    return None if base is None else base / name


def _first_existing(cands: Sequence[Any], what: str) -> Path:
    """第一个存在的候选；一个都没有就**点名**列出试过哪些（⛔ 不许静默退到别处）。"""
    tried: list[str] = []
    for cand in cands:
        if cand in (None, ""):
            continue
        path = Path(cand)
        tried.append(str(path))
        if path.is_file():
            return path
    # ★ 2026-10-17：hint 现在会**说清怎么修**（工作区解析不到时点名环境变量），
    #   而消息本身是一句人话 —— ⛔ 不是 FileNotFoundError 那种原始异常。
    raise AnimBridgeError(f"找不到 {what}：{tried}", hint=_research_hint())


def find_path_table(explicit: Any = None) -> Path:
    """节点路径表 `_nodepaths.json`（crc32 -> 路径）。"""
    return _first_existing(
        [explicit, DEFAULT_FIXTURES / "_nodepaths.json", _research_file("_nodepaths.json")],
        "节点路径表 _nodepaths.json",
    )


def find_node_index(explicit: Any = None) -> Path:
    """父链自证件 `_nodeindex.json`（给了才做父链自证）。"""
    return _first_existing(
        [explicit, DEFAULT_FIXTURES / "_nodeindex.json", _research_file("_nodeindex.json")],
        "父链自证件 _nodeindex.json",
    )


def read_path_table(path: Any = None) -> anim_path.PathTable:
    """读路径表（**唯一实现**是内核的 `anim_path.read_path_table`）。"""
    return anim_path.read_path_table(find_path_table(path))


def read_chain(path: Any = None) -> dict[str, Any] | None:
    """读父链自证件；`None` = 没给（那时**不做**父链自证 —— 不猜）。"""
    if path in (None, ""):
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        # ⛔ 不抛 FileNotFoundError、也⛔ 不静默当空 —— 给一句人话（带怎么修）
        raise AnimBridgeError(
            f"父链自证件读不到：{path}（{type(exc).__name__}）", hint=_research_hint()
        ) from exc
    payload = json.loads(text)
    chain = payload.get("nodes")
    return dict(chain) if isinstance(chain, Mapping) else None


# --------------------------------------------------------------------------- #
# 延迟 import（这两样都**不能**在本模块顶层出现：一个要 bpy，一个是装机的 _vendor）
# --------------------------------------------------------------------------- #
def _take_importer() -> Any:
    """拿件那一层的定位函数（`locate_unit` / `aa_dir_of`）。⚠ 那个模块顶层 import bpy。"""
    try:
        from blender_addon import importer as take_importer
    except ImportError:  # pragma: no cover - 只在非包上下文里走这条
        from . import importer as take_importer
    return take_importer


def _unitypy_source() -> Any:
    """UnityPy 适配层（**只借它的 typetree 解析**；容器读侧仍是我们自己的）。"""
    from ba_mod_maker.asset_kernel import unitypy_source

    return unitypy_source


def _cli_anim() -> Any:
    """产品 `anim` 子命令（写回那一条链**只有这一份**实现）。"""
    from ba_mod_maker.cli import anim as cli_anim

    return cli_anim


# --------------------------------------------------------------------------- #
# PPtr：**两种形状**都要认（typetree 给的是 dict，直接属性访问给的是对象）
# --------------------------------------------------------------------------- #
def pptr_path_id(ptr: Any) -> int | None:
    """PPtr -> `m_PathID`（空指针 / 0 -> None）。

    ⚠ 2026-10-15 实测踩过：`source.read_typetree()` 里的 PPtr 是 **dict**
    (`{"m_FileID":…, "m_PathID":…}`)，而 `obj.m_Controller` 给的是对象 ——
    只写 `getattr(ptr, "m_PathID", None)` 会**静默**得到 None（我的第一版就是）。
    """
    if ptr is None:
        return None
    value = ptr.get("m_PathID") if isinstance(ptr, Mapping) else getattr(ptr, "m_PathID", None)
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def pptr_file_id(ptr: Any) -> int:
    """PPtr -> `m_FileID`（0 = 本包；非 0 = 外部引用，本模块**解不了**）。"""
    if ptr is None:
        return 0
    value = ptr.get("m_FileID", 0) if isinstance(ptr, Mapping) else getattr(ptr, "m_FileID", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# 一个件挂了哪些 clip（★ 这一跳的核心）
# --------------------------------------------------------------------------- #
class AnimatorOutsideError(AnimBridgeError):
    """controller 指向**包外**（`m_FileID` ≠ 0）—— 本模块解不了，点名报。"""

    def __init__(self, node_name: str, file_id: int) -> None:
        super().__init__(
            f"节点 {node_name!r} 的 Animator 指向包外的 controller（m_FileID={file_id}）",
            hint="这条动画不在这个包里 —— 换包，或先把依赖包拉下来",
        )


def animator_clips(
    source: Any, asset_path: str
) -> tuple[tuple[str, str, tuple[ClipRef, ...]], ...]:
    """prefab 里的每个 `Animator` -> `m_Controller` -> `m_AnimationClips`。

    :return: `((挂 Animator 的节点名, controller 名, 它引用的 clip), …)`。
    :raises AnimBridgeError: 这个 prefab 里没有 Animator / controller 指向包外 —— 点名报。
    """
    nodes, _materials = source.read_prefab_nodes(asset_path)
    pairs = [
        (str(node["name"]), int(comp["path_id"]))
        for node in nodes
        for comp in (node.get("components") or ())
        if str(comp.get("kind")) == "Animator"
    ]
    if not pairs:
        raise AnimBridgeError(
            f"{asset_path} 的 {len(nodes)} 个节点里没有 Animator —— 这个件没有动画可拿",
            hint="检查拿的是不是车体/炮塔那个 prefab（Animator 挂在 prefab 根上）",
        )
    found: list[tuple[str, str, tuple[ClipRef, ...]]] = []
    for node_name, animator_id in pairs:
        tree = source.read_typetree(animator_id)
        ctrl = tree.get("m_Controller")
        ctrl_id = pptr_path_id(ctrl)
        if ctrl_id is None:
            found.append((node_name, "", ()))
            continue
        file_id = pptr_file_id(ctrl)
        if file_id:
            raise AnimatorOutsideError(node_name, file_id)
        ctrl_tree = source.read_typetree(ctrl_id)
        name = str(ctrl_tree.get("m_Name") or "")
        refs: list[ClipRef] = []
        for item in ctrl_tree.get("m_AnimationClips") or ():
            clip_id = pptr_path_id(item)
            if clip_id is None:
                continue
            if pptr_file_id(item):
                continue
            refs.append(ClipRef(path_id=int(clip_id), name=_clip_name_of(source, int(clip_id))))
        found.append((node_name, name, tuple(refs)))
    return tuple(found)


def _clip_name_of(source: Any, path_id: int) -> str:
    """一个 clip 的 `m_Name`（读不动就给空串 —— 名字只是读数，不是判据）。

    ⚠ 2026-10-15 实测：`getattr(obj, "m_Name", "")` 在这条路上给的是**空串**
    （读数里一排 `clip ''`）⇒ 走 `read_typetree()`，属性访问只当兜底。
    """
    if source.object_at(path_id) is None:
        return ""
    try:
        tree = source.read_typetree(path_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        tree = None
    if isinstance(tree, Mapping):
        name = str(tree.get("m_Name") or "")
        if name:
            return name
    with contextlib.suppress(AttributeError, KeyError, TypeError, ValueError):
        return str(getattr(source.object_at(path_id), "m_Name", "") or "")
    return ""


def _fixture_meta(fixtures: Path, meta: Any = None) -> Path:
    """类型树那件（`units_meta.bin`）—— 夹具里没有类型树，必须显式给或按默认位置找。"""
    if meta not in (None, ""):
        return Path(meta)
    candidate = fixtures.parents[1] / "w10_ref" / "units_meta.bin"
    if candidate.is_file():
        return candidate
    raise AnimBridgeError(
        f"夹具那条路要 `units_meta.bin`，{candidate} 不在",
        hint="研究线把 SerializedFile 元数据冻在 _work/w10_ref/units_meta.bin",
    )


def _fixture_unit_anim(unit_key: str, fixtures: Path, meta: Any = None) -> UnitAnim:
    """离线路：夹具里按 **controller 名** 对上这个件（真包那条路见 `_online_unit_anim`）。"""
    from ba_mod_maker.asset_kernel import anim_build, builder, clone

    index_path = fixtures / "controllers_index.json"
    if not index_path.is_file():
        raise AnimBridgeError(f"夹具目录里没有 {index_path.name}：{fixtures}")
    rows = json.loads(index_path.read_text(encoding="utf-8"))["rows"]
    hits = [r for r in rows if str(r.get("name")) == unit_key]
    if not hits:
        raise AnimBridgeError(
            f"夹具里没有 controller 叫 {unit_key!r}（共 {len(rows)} 个）",
            hint="controller 名 = `Units.ModelFileName`（真件实测：`RU_T72B` / `RU_T34` 这种）",
        )
    if len(hits) > 1:
        raise AnimBridgeError(
            f"夹具里有 {len(hits)} 个 controller 都叫 {unit_key!r}：",
            f"{[int(r['path_id']) for r in hits]} —— 同名不许猜",
        )
    row = hits[0]
    blob = (fixtures / "controllers_payloads.bin").read_bytes()
    raw = blob[int(row["offset"]) : int(row["offset"]) + int(row["length"])]
    meta_bytes = _fixture_meta(fixtures, meta).read_bytes()
    table = builder.read_raw_type_table(meta_bytes, common_strings=clone.unitypy_common_strings())
    nodes = table.entry_for(anim_build.CLASS_ANIMATOR_CONTROLLER).nodes
    fields = clone.read_object(raw, nodes)
    clip_index = fixtures / "clips_index.json"
    names = {
        int(r["path_id"]): str(r["name"])
        for r in json.loads(clip_index.read_text(encoding="utf-8"))["rows"]
    }
    refs = tuple(
        ClipRef(path_id=cid, name=names.get(cid, ""))
        for cid in (pptr_path_id(p) for p in (fields.get("m_AnimationClips") or ()))
        if cid is not None
    )
    return UnitAnim(
        unit_key=unit_key,
        source="夹具",
        bundle="",
        asset_path="",
        controller=str(fields.get("m_Name") or unit_key),
        clips=refs,
        notes=(f"离线夹具：{fixtures}",),
    )


def _online_unit_anim(game_root: Any, unit_key: str) -> UnitAnim:
    """真包那条路：catalog -> 唯一的包 -> prefab 的 Animator -> controller -> clips。

    ★ 2026-10-18（用户真机：「建动画失败：没给游戏根目录」）：面板只传了**件**，没传游戏根
    —— 而这里原来直接抛错。现在先按产品既有的解析顺序**自己探**（`game.find_game_root()`，
    与 ① 拿 / ④ 出 同一个入口），探不到才报错，并且报错里写清「探过哪些地方」。
    """
    if not game_root:
        try:
            from ba_mod_maker.core.assets.game import find_game_root

            found = find_game_root()
        except Exception:  # noqa: BLE001 -- 探测本身失败也走下面那条可读的报错
            found = None
        if found is not None:
            game_root = str(found)
    if not game_root:
        raise AnimBridgeError(
            "没给游戏根目录 —— 真包那条路要它才找得到件（自动探测也没探到）",
            hint="给 game_root，或在 Blender 里设 BA_MOD_MAKER_GAME；"
            "或者 offline=True 走冻结夹具（那时读数里会写明出处）",
        )
    take = _take_importer()
    loc = take.locate_unit(game_root, unit_key)
    aa_pc = take.aa_dir_of(game_root) / "PC"
    notes: list[str] = []
    with _unitypy_source().open_unique_source(aa_pc, loc.candidates, loc.asset_path) as (
        source,
        bundle_name,
    ):
        found = animator_clips(source, loc.asset_path)
        # ★ 存**全路径**：`open_unique_source` 给的是包**名**（写回要的是路径）。
        bundle_path = str(source.path)
    best = max(found, key=lambda item: len(item[2]))
    if len(found) > 1:
        notes.append(
            f"这个 prefab 上有 {len(found)} 个 Animator：",
            f"{[(n, c, len(r)) for n, c, r in found]} —— 取 clip 最多的那个",
        )
    node_name, controller, refs = best
    notes.append(f"包名 {bundle_name}")
    if not refs:
        notes.append(f"节点 {node_name!r} 上的 controller {controller!r} 一条 clip 都没引用")
    return UnitAnim(
        unit_key=unit_key,
        source="真包",
        bundle=bundle_path,
        asset_path=str(loc.asset_path),
        controller=controller,
        clips=refs,
        notes=tuple(notes),
    )


def unique_refs(refs: Sequence[ClipRef]) -> tuple[ClipRef, ...]:
    """按 pathID 去重（保持出现次序）。

    ⚠ 真件的 `m_AnimationClips` **有重复槽**：实测 `RU_T72B` 的 3 槽里 `T72B hatch`
    出现两次 ⇒ 不去重就会把同一条 clip 建两遍 Action（第二遍叫 `BA_T72B hatch.001`）。
    """
    seen: set[int] = set()
    out: list[ClipRef] = []
    for ref in refs:
        key = int(ref.path_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return tuple(out)


def clips_of_unit(
    game_root: Any,
    unit_key: str,
    *,
    offline: bool = False,
    fixtures: Any = None,
    meta: Any = None,
) -> UnitAnim:
    """★ **一个件挂了哪些 clip**（这一跳的唯一入口）。

    :param offline: True = 走冻结夹具（不开包），读数里写明出处；⛔ 默认**不偷偷退源**。
    :raises AnimBridgeError: 件不在 catalog / 包里没有 Animator / 包里没有这个 clip。
    """
    key = str(unit_key).strip()
    if not key:
        raise AnimBridgeError("unit_key 是空的 —— 拿件用的是地址键（`Units.ModelFileName`）")
    unit = (
        _fixture_unit_anim(key, Path(fixtures) if fixtures else DEFAULT_FIXTURES, meta)
        if offline
        else _online_unit_anim(game_root, key)
    )
    unique = unique_refs(unit.clips)
    if len(unique) == len(unit.clips):
        return unit
    dropped = len(unit.clips) - len(unique)
    return replace(
        unit,
        clips=unique,
        notes=(*unit.notes, f"controller 的 m_AnimationClips 里有 {dropped} 个重复槽（已去重）"),
    )


def open_clip_store(
    unit: UnitAnim,
    *,
    offline: bool = False,
    fixtures: Any = None,
    meta: Any = None,
) -> AnimClipStore:
    """本件的 clip 字节 + 类型树（`ClipStore` 是**产品那一份**实现）。

    ⛔ 真包走 `ClipStore.from_bundle`（我们自己的容器读侧，range 读，**不** `UnityPy.load`）；
    离线走 `from_fixture`。两条路都**逐条复核**：引用的 pathID 真的在 store 里。
    """
    cli = _cli_anim()
    fx = Path(fixtures) if fixtures else DEFAULT_FIXTURES
    if unit.source == "真包" and not offline:
        if not unit.bundle:
            raise AnimBridgeError(f"{unit.unit_key}：出处写着真包，却没有包路径")
        store = cli.ClipStore.from_bundle(unit.bundle)
    else:
        store = cli.ClipStore.from_fixture(
            fx / "clips_payloads.bin",
            fx / "clips_index.json",
            _fixture_meta(fx, meta),
        )
    for ref in unit.clips:
        try:
            store.by_clip(str(int(ref.path_id)))
        except AssetError as exc:
            raise AnimBridgeError(
                f"controller {unit.controller!r} 引用了 pathID={ref.path_id}，"
                f"但{'真包' if unit.source == '真包' else '夹具'}里没有这条 clip：{exc}",
                hint="包/夹具不是同一版（重新拿件 / 重新冻结夹具）",
            ) from exc
    return AnimClipStore(store=store, refs=tuple(unit.clips), unit=unit)


def spec_for(
    store: Any,
    path_id: int,
    table: anim_path.PathTable,
    chain: Any = None,
    *,
    allow_partial: bool = True,
) -> anim_action.AnimSpec:
    """一条 clip -> 内核的 `AnimSpec`（⛔ 解码只有内核那一份，本模块不另写）。

    :raises AssetError: 没有这条 clip / 绑定一条都解不出（空 clip 就是这一类）。
    """
    record, fields = store.by_clip(str(int(path_id)))
    return anim_action.build_action_spec(
        fields,
        table,
        path_id=int(path_id),
        allow_partial=allow_partial,
        chain_source=chain,
    )


# --------------------------------------------------------------------------- #
# 写回：只拼命令行 ＋ 走产品自己的入口（⛔ 本模块不碰一个字节的写盘）
# --------------------------------------------------------------------------- #
def writeback_argv(
    bundle: Any,
    clip: Any,
    *,
    k: float | None = None,
    from_file: Any = None,
    dry_run: bool = True,
    project_root: Any = None,
    catalog: Any = None,
    game_root: Any = None,
) -> list[str]:
    """产品 `anim apply` 的 argv（默认**干跑**：⛔ 不加 `--write`）。

    :raises AnimBridgeError: `k` 与 `from_file` 二选一没做到（⛔ 不替调用方猜）。
    """
    if (k is None) == (from_file is None):
        raise AnimBridgeError(
            "写回要么给 k（就地拉伸时间轴）、要么给 from_file（装一份现成的对象字节）—— 二选一",
        )
    argv = ["anim", "apply", "--bundle", str(bundle), "--clip", str(clip)]
    if k is not None:
        argv += ["--k", repr(float(k))]
    if from_file is not None:
        argv += ["--from", str(from_file)]
    if not dry_run:
        argv.append("--write")
    if project_root not in (None, ""):
        argv += ["--project-root", str(project_root)]
    if catalog not in (None, ""):
        argv += ["--catalog", str(catalog)]
    if game_root not in (None, ""):
        argv += ["--game", str(game_root)]
    return argv


def apply_via_cli(argv: Sequence[str]) -> CliResult:
    """跑一次产品 `anim apply`（**进程内**，用产品自己的 argparse 建 Namespace）。

    ⛔ 不 subprocess、不自己拼写盘逻辑 —— 落字节的唯一实现在 `asset_kernel/anim_write.py`。
    永远要 `--json`：机器读那一份进 `doc`，人读那一份进 `stderr`（`Emitter` 在 json 模式下
    把人读行写 stderr）—— 两边都当读数带回去。
    """
    import argparse

    cli = _cli_anim()
    parser = argparse.ArgumentParser(prog="ba-mod-maker", add_help=False)
    sub = parser.add_subparsers(dest="command")
    cli.add_parser(sub)
    args = [str(item) for item in argv]
    if "--json" not in args:
        args.append("--json")
    try:
        namespace = parser.parse_args(args)
    except SystemExit:
        return CliResult(
            rc=2,
            doc={"ok": False, "error": {"message": f"anim apply 的参数不合法：{args}"}},
        )
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = int(cli.run(namespace))
    text = out.getvalue().strip()
    doc: Mapping[str, Any] = {}
    if text:
        with contextlib.suppress(json.JSONDecodeError):
            loaded = json.loads(text)
            if isinstance(loaded, Mapping):
                doc = loaded
    return CliResult(rc=rc, doc=doc, stdout=text, stderr=err.getvalue())
