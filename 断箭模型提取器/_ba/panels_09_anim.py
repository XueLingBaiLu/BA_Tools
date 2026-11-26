r"""面板 08 · 动画 —— 游戏里的 clip -> Blender 的 Action（＋一把**能红**的尺子）。

出口读数：**clip 条数 / 当前那条的曲线数 / 骨名对不上的条数**（① ② ③ 的明细走「看详情 ▸」）。

## 这一格走到哪一步（逐跳，⛔ 不粉饰）

| 跳 | 干什么 | 今天 |
|---|---|---|
| ① | 列出游戏里的 clip | ✔ 算子 `ba_mod.anim_scan` —— 读真件冻结的 `clips_index.json`（475 条） |
| ② | 选一条 -> 它的曲线 / 骨名读数 | ✔ 算子 `ba_mod.anim_spec`（内核 `build_action_spec`） |
| ③ | 规格 -> 真 `bpy.types.Action`（FCurve ＋ 关键帧，建完回读） | ✔ 算子 `ba_mod.anim_build` |
| ④ | **尺子**：骨名对不上的条数（能红） | ✔ 算子 `ba_mod.anim_ruler` ＋ 负控 `ba_mod.anim_negctl` |
| ⑤ | 改时长 / 写回包 | ✘ 在 `ba-mod-maker anim stretch|apply` —— **不在本格** |
| ⑤ | Animator 状态机 / 依赖 hash | ✘ **谁都没有这一跳** |

③ 建完**回读四项**：曲线数 / 骨名 / 帧区间 / 关键帧数 —— 对不上就抛。

### ③ 缺的那一跳（如实写）

建 Action 要**一个骨架**（`ARMATURE` 对象）——它的真骨名就是合同。本插件**没有**
「把游戏里的骨架导进来」这一跳（那在导入侧 `rig_plan` / 骨表那条线）⇒ 场景里没有骨架时，
本格**明说缺这一跳**，⛔ 不凭空造骨冒充成功（旧插件 `anim_entry.ensure_armature` 那种造法
只配给无头探针用，不是产品入口）。

### ④ 这把尺子量什么（AGENTS §0.9 动作 2：**能红**才算尺子）

量的是**条数**：规格里那些节点路径，有几条在**当前骨架的真骨名表**里找不到对应的骨。
正控 = 骨名齐全 ⇒ 0 条（绿）；负控 = 抽掉一根 ⇒ ≥1 条（红）。面板上那颗
「负控（必须红）」喂的就是一个**已知对不上**的进去 —— 它绿了就是尺子坏了。

⛔ 本面板**只读 ＋ 只在 Blender 里建 Action**：不起任何进程、不写游戏目录、不碰包。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import bpy

from . import panels_common as pc
from .panels_common import reading

#: 真件（**离线夹具** = 从 units 包冻出来的 475 条 clip，`kb/23` 同数）。
#: ⛔ 只读：面板**不开**那个 3.4 GB 的 units 包（开了要吃约 10 GB 内存，AGENTS 明令串行）。
#: ★ 2026-10-17（用户裁定）：原来是写死的开发机绝对路径 ⇒ 装成 zip 之后这些夹具根本不在，
#:   而本格是**无条件读**它们。现在一律走 `panels_common` 那一套解析器（唯一来源）；
#:   解析不到 = `None` ⇒ 画一句人话（⛔ 不抛、⛔ 不画空白）。
CLIP_INDEX_RELATIVE = ("_work", "anim-line", "fixtures", "clips_index.json")
CLIP_BLOB_RELATIVE = ("_work", "anim-line", "fixtures", "clips_payloads.bin")
CLIP_META_RELATIVE = ("_work", "w10_ref", "units_meta.bin")
#: 研究线的节点路径表（crc32 -> 路径）与父链自证件 —— 在产品的工作区根下的 `研究-模型与动画/`。
RESEARCH_DIRNAME = "研究-模型与动画"
PATH_TABLE_NAME = "_nodepaths.json"
NODE_INDEX_NAME = "_nodeindex.json"
#: 第一次点「解规格」时的默认那条（真件先例：4 条四元数曲线 · 1 根骨 · 1.0000 s / 60 Hz）。
DEFAULT_CLIP = "T72B hatch"
#: Action 的名字前缀（游戏来的 Action 与用户自己做的分得开）。
ACTION_PREFIX = "BA_"
#: 去重后缀的最大尝试次数（`_1` … `_8`）—— 与旧插件 `anim_bridge._MAX_DUP_SUFFIX` 同口径。
_MAX_DUP_SUFFIX = 8
#: `pose.bones["骨名"].通道` 的形状。★ **别用 `split(".")[0]`**：
#: `pose.bones["hatch"].rotation_quaternion` 切出来的是 `pose`（旧插件真机踩过：回读骨名全空）。
_BONE_RE = re.compile(r'^pose\.bones\["(?P<bone>[^"]*)"\]\.')


class AnimActionError(RuntimeError):
    """规格落不成 Blender 数据时抛它（⛔ fail-closed：接不上就报，不静默改名）。"""


# --------------------------------------------------------------------------- #
# 上一次的读数（算子写、面板读）。⚠ 与旧插件同一个取舍：重启 Blender 即清空。
# --------------------------------------------------------------------------- #
_CLIPS: list[dict[str, Any]] = []
_SPEC: Any = None
_SPEC_LINES: list[str] = []
_ACTION_LINES: list[str] = []
#: `(对不上的条数, 人读那一行)`；`None` = 还没量过。
_RULER: tuple[int, str] | None = None
_NEGCTL_LINE = ""
#: ③ 的那句话 `(文案, 图标)`；空串 = 还没建过（摘要就不画这一行）。
_ACTION_NOTE: tuple[str, str] = ("", "INFO")


def last_clips() -> list[dict[str, Any]]:
    """面板要画的 clip 清单（没读过 = 空）。★ 判据脚本直接读它。"""
    return list(_CLIPS)


def last_spec() -> Any:
    """上一次解出来的规格（没解过 = None）。探针也走这个入口，保证读的是同一份。"""
    return _SPEC


def last_ruler() -> tuple[int, str] | None:
    """上一次量出来的 `(对不上的条数, 人读那一行)`。"""
    return _RULER


def last_negative() -> str:
    """上一次负控那一行（没跑过 = 空串）。★ 判据脚本直接读它。"""
    return _NEGCTL_LINE


def set_clips(rows: list[dict[str, Any]]) -> None:
    """算子写（探针亦可用）。"""
    global _CLIPS
    _CLIPS = list(rows)


def set_spec(spec: Any, lines: list[str]) -> None:
    """算子写：规格 ＋ 它的读数几行。"""
    global _SPEC, _SPEC_LINES
    _SPEC = spec
    _SPEC_LINES = list(lines)


def last_action_note() -> tuple[str, str]:
    """③ 的摘要那句话 `(文案, 图标)`（没建过 = 空串）。★ 判据脚本直接读它。"""
    return _ACTION_NOTE


# --------------------------------------------------------------------------- #
# 纯函数（函数体内**不碰 bpy** —— 判据靠它们，不靠截图）
# --------------------------------------------------------------------------- #
def file_note(path: Path) -> str:
    """`<在|不在> <字节> <路径>` —— 与 05 面板同一个形状。"""
    try:
        st = path.stat()
    except OSError:
        return f"不在  {path}"
    return f"在 {st.st_size:,} B  {path}"


def clip_index() -> Path | None:
    """冻结的 clip 清单（475 行）—— 工作区解析不到就 `None`。"""
    repo = pc.repo_root()
    return None if repo is None else Path(repo).joinpath(*CLIP_INDEX_RELATIVE)


def clip_blob() -> Path | None:
    """冻结夹具的字节文件（`clips_payloads.bin`）—— 同上。"""
    repo = pc.repo_root()
    return None if repo is None else Path(repo).joinpath(*CLIP_BLOB_RELATIVE)


def clip_meta() -> Path | None:
    """`units_meta.bin`（类型树来源）—— 同上。"""
    repo = pc.repo_root()
    return None if repo is None else Path(repo).joinpath(*CLIP_META_RELATIVE)


def research_dir() -> Path | None:
    """研究线产物目录（工作区根下的 `研究-模型与动画/`）—— 同上。"""
    workspace = pc.workspace_root()
    return None if workspace is None else Path(workspace) / RESEARCH_DIRNAME


def path_table() -> Path | None:
    """节点路径表 `_nodepaths.json` —— 同上。"""
    base = research_dir()
    return None if base is None else base / PATH_TABLE_NAME


def node_index() -> Path | None:
    """父链自证件 `_nodeindex.json` —— 同上。"""
    base = research_dir()
    return None if base is None else base / NODE_INDEX_NAME


def require_asset(path: Path | None, name: str) -> Path:
    """取一份素材的路径；**解析不到**就抛一句人话（调用方把它变成面板上的红行）。"""
    if path is None:
        raise AnimActionError(f"动画素材不在：{name}（{pc.WORKSPACE_HINT}）")
    return Path(path)


def asset_line(name: str, path: Path | None) -> str:
    """面板那一行：在/不在 ＋ 路径；解析不到就给一句人话（⛔ 不许画 None）。"""
    if path is None:
        return f"动画素材不在：{name}"
    return file_note(Path(path))


def assets_note() -> str:
    """素材缺不缺 -> 一句人话（空串 = 都在）。⛔ 不抛、⛔ 不静默空。"""
    for name, path in (
        ("clips_index.json", clip_index()),
        ("clips_payloads.bin", clip_blob()),
        ("units_meta.bin", clip_meta()),
        ("_nodepaths.json", path_table()),
        ("_nodeindex.json", node_index()),
    ):
        if path is None or not Path(path).is_file():
            return f"动画素材不在：{name}"
    return ""

def read_clip_index(path: Path | None = None) -> list[dict[str, Any]]:
    """读真件冻结的 clip 清单（475 行）。读不动就抛 —— 调用方把它变成面板上的红行。

    ⛔ 这里**不开包**：清单是研究线从 units 包冻出来的（`_work/anim-line/p3_freeze.py`）。
    """
    target = require_asset(path if path else clip_index(), "clips_index.json")
    payload = json.loads(Path(target).read_text(encoding="utf-8"))
    rows = payload["rows"]
    if not isinstance(rows, list) or not rows:
        raise AnimActionError(f"{path} 里没有 rows（清单形状变了）")
    for key in ("name", "path_id", "offset", "length"):
        if key not in rows[0]:
            raise AnimActionError(f"{path} 的 rows[0] 少一个键 {key!r}（清单形状变了）")
    return [dict(r) for r in rows]


def index_line(rows: list[dict[str, Any]]) -> str:
    """清单的一句话读数：条数 · 曲线合计 · 最长那条。"""
    curves = sum(int(r.get("curve_total") or 0) for r in rows)
    worst = max(rows, key=lambda r: float(r.get("stop_time") or 0.0))
    return (
        f"{len(rows)} 条 clip · 曲线合计 {curves} 条 · 最长 "
        f"{float(worst.get('stop_time') or 0.0):.4f} s（{worst.get('name')}）"
    )


#: 状态词（用户口径 2026-10-14）：摘要里只许用这几个 —— 就绪 ✔ / 不在 / 红 N 条 / 绿 / 阻塞。
#: 本格用到的是「绿 · N 条」/「不在」两种（红由 ruler_line 那句自己带）。
GONE = "不在"


def clips_note(rows: list[dict[str, Any]]) -> str:
    """① 的一句话：清单读了没有（⛔ 没读 = 不在，不冒充绿）。"""
    return f"绿 · {len(rows)} 条" if rows else f"{GONE} —— 点「读 clip 清单」"


def spec_note(spec: Any) -> str:
    """② 的一句话：当前选的那条 ＋ 曲线数（逐行明细在详情里）。"""
    if spec is None:
        return f"{GONE} —— 点「解规格（读 clip）」，默认 {DEFAULT_CLIP}"
    return f"绿 · {spec.name} · 曲线 {spec.n_curves} 条 · 骨 {len(spec.bones)} 根"


def clip_candidates(path: str) -> tuple[str, ...]:
    """一条 Unity 路径在 Blender 里**可能**叫什么（按优先级）。

    照 `rig_plan.slot_names_of` 的口径：末级节点名就是骨名，同名按出现次序加 `_1`/`_2`。
    ⛔ 这是**候选**，不是猜中的答案 —— 调用方必须拿骨架的**真骨名表**逐条核对。
    """
    leaf = str(path).rsplit("/", 1)[-1]
    out = [leaf, str(path).replace("/", "_"), str(path)]
    out += [f"{leaf}_{i}" for i in range(1, _MAX_DUP_SUFFIX + 1)]
    seen: list[str] = []
    for name in out:
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def resolve_bone_names(paths: Any, bone_names: Any) -> tuple[dict[str, str], tuple[str, ...]]:
    """节点路径 -> 骨名（**逐条核对骨架的真骨名**）。

    :return: `(对上的映射, 对不上的路径)`。⛔ 对不上就是**报出来** —— 不跳过、不改名；
        硬塞一个名字会造出一条**指向空气的 FCurve**（界面看着有、动起来没有）。
    """
    have = {str(n): i for i, n in enumerate(bone_names)}
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for path in paths:
        for cand in clip_candidates(path):
            if cand in have:
                mapping[str(path)] = cand
                break
        else:
            missing.append(str(path))
    return mapping, tuple(missing)


def ruler_line(missing: tuple[str, ...], total: int) -> str:
    """尺子那一行（★ 判据 P9b 就认它）：0 条 = 绿，≥1 条 = **红**。"""
    if total == 0:
        return "没有骨路径要核对（规格是空的）—— ⛔ 不许当绿"
    if not missing:
        return f"0 / {total} 条对不上 —— 骨名齐全（绿）"
    head = list(missing[:3])
    more = f" …（共 {len(missing)} 条）" if len(missing) > 3 else ""
    return f"{len(missing)} / {total} 条对不上（红）：{head}{more}"


def ruler_note(ruler: tuple[int, str] | None) -> tuple[str, str]:
    """④ 的 `(文案, 图标)` —— 量过才敢说绿/红（没量 = 不在，⛔ 不冒充绿）。"""
    if ruler is None:
        return f"{GONE} —— 选中一个骨架，点「骨名对账（尺子）」", "INFO"
    count, line = ruler
    return line, "CHECKMARK" if count == 0 else "ERROR"


def scaled_frames(times: Any, start: float, fps: float) -> tuple[float, ...]:
    """clip 内绝对秒 -> Blender 帧号（`(t - start) * fps`）。

    `fps` 默认取 clip 自己的 `m_SampleRate` ⇒ **一个采样点占一帧**
    （真件 60 Hz / 1.0000 s ⇒ 61 帧，帧号 0..60）。
    """
    if fps <= 0:
        raise AnimActionError(f"帧率 {fps!r} 不合法")
    return tuple((float(t) - float(start)) * float(fps) for t in times)


def action_name(clip_name: str) -> str:
    """`T72B hatch` -> `BA_T72B hatch`（⛔ 不让游戏来的 Action 和用户自己做的分不清）。"""
    text = str(clip_name).strip() or "clip"
    return text if text.startswith(ACTION_PREFIX) else ACTION_PREFIX + text


def spec_lines(spec: Any) -> list[str]:
    """规格的人读几行（面板与探针看**同一份**）。"""
    bones = [b.path for b in spec.bones]
    channels = sorted({str(c.channel) for c in spec.curves})
    return [
        f"{spec.name}（pathID={spec.path_id}）",
        f"曲线 {spec.n_curves} 条 · 时长 {spec.duration:.4f} s · "
        f"采样率 {spec.sample_rate:g} Hz · 采样 {spec.n_samples} 帧",
        f"骨 {len(bones)} 根：{bones}",
        f"通道：{channels}",
        str(spec.stats.line()),
        f"四元数归一化最大偏差 {spec.max_quat_norm_delta:.3e}",
    ]


# --------------------------------------------------------------------------- #
# ★ 两代 Action API 的收口（4.3- 的 action.fcurves / 4.4+ 的 layers->strips->channelbag）
#   真机实测（Blender 5.2.1）：`Action.fcurves` 不存在；`getattr(action, "fcurves", None)`
#   会**抛** AttributeError（RNA 缺属性就是这样，不是返回 None）。
# --------------------------------------------------------------------------- #
def _legacy_fcurves(action: Any) -> Any | None:
    """4.3- 的 `Action.fcurves`；5.2.1 上没有 —— 必须真的去试一次。"""
    try:
        return action.fcurves
    except AttributeError:
        return None


def ensure_fcurves(action: Any) -> Any:
    """拿到（必要时**新建**）一个可以 `.new(...)` 的 FCurve 容器（两代 API 都认）。

    ⚠ 一律**先 new 再取**：刚 `actions.new()` 出来的 action，`slots`/`layers` 是懒创建的，
      不显式 new 一个，写进去的关键帧**读不回来**。
    """
    legacy = _legacy_fcurves(action)
    if legacy is not None:
        return legacy
    slot = action.slots.new(id_type="OBJECT", name=action.name)
    layer = action.layers.new("Layer")
    strip = layer.strips.new(type="KEYFRAME")
    return strip.channelbag(slot, ensure=True).fcurves


def action_fcurves(action: Any) -> tuple[Any, ...]:
    """回读一个 Action 的**全部** FCurve（两代 API 都认）。"""
    legacy = _legacy_fcurves(action)
    if legacy is not None:
        return tuple(legacy)
    out: list[Any] = []
    for layer in getattr(action, "layers", ()) or ():
        for strip in getattr(layer, "strips", ()) or ():
            for bag in getattr(strip, "channelbags", ()) or ():
                out.extend(tuple(bag.fcurves))
    return tuple(out)


def _bone_of(fcurve: Any) -> str | None:
    """FCurve 的 data_path -> 骨名（⛔ 不用 split，见 `_BONE_RE` 上面那条）。"""
    match = _BONE_RE.match(str(fcurve.data_path))
    return match.group("bone") if match else None


def _normalize(values: Any) -> tuple[float, ...]:
    """四元数归一化（模长 0 就回单位四元数，⛔ 不产生 NaN）。"""
    norm = math.sqrt(sum(float(v) * float(v) for v in values))
    if norm <= 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return tuple(float(v) / norm for v in values)


def _quat_values(spec: Any, path: str, n: int, bone: Any) -> list[tuple[float, ...]] | None:
    """一根骨的四条四元数曲线 -> `(w, x, y, z)` 序列（换算 ＋ 归一化）。

    ⛔ 换算走内核的 `unity_to_blender_quat`（含符号与手性），本模块不另写一份。
    返回 `None` = 四条不齐（调用方必须**抛**，⛔ 不摆一个缺分量的姿势）。
    """
    got = {c.component: c for c in spec.curves if c.bone_path == path and c.channel == bone}
    if set(got) != {"x", "y", "z", "w"}:
        return None
    if any(len(got[k].keys) != n for k in got):
        raise AnimActionError(f"{path} 的四元数四条曲线长度不一致")
    out: list[tuple[float, ...]] = []
    for i in range(n):
        q = (got["x"].keys[i][1], got["y"].keys[i][1], got["z"].keys[i][1], got["w"].keys[i][1])
        out.append(_normalize(_to_blender(q)))
    return out


def _to_blender(q: tuple[float, ...]) -> tuple[float, ...]:
    """Unity 四元数 -> Blender 四元数（内核那一份实现，延迟 import）。"""
    from ba_mod_maker.asset_kernel.anim_action import unity_to_blender_quat

    return tuple(unity_to_blender_quat(*q))


def create_action(
    spec: Any,
    armature: Any,
    *,
    fps: float | None = None,
    interpolation: str = "LINEAR",
) -> Any:
    """规格 -> 真 Action（落在骨架的 pose bone 上），**建完回读四项**。

    :param armature: `ARMATURE` 类型的对象（它的 `pose.bones` 才是数据路径的宿主）。
    :param fps: 「1 秒占几帧」；默认 = clip 的 `m_SampleRate`（一个采样点一帧）。
    :raises AnimActionError: 骨名接不上 / 四元数不齐 / 回读对不上 —— 一条都不许静默带过。
    """
    if getattr(armature, "type", None) != "ARMATURE":
        raise AnimActionError(
            f"{getattr(armature, 'name', armature)!r} 不是骨架对象"
            f"（type={getattr(armature, 'type', None)!r}）—— Action 要落在骨架的 pose bone 上",
        )
    names = tuple(b.name for b in armature.data.bones)
    mapping, missing = resolve_bone_names((b.path for b in spec.bones), names)
    if missing:
        raise AnimActionError(
            f"{len(missing)} 条节点路径在骨架上找不到对应的骨：{list(missing[:4])}"
            f"（骨架 {len(names)} 根：{sorted(names)[:8]}…）",
        )
    rate = float(fps if fps is not None else spec.sample_rate)
    frames = scaled_frames(spec.times, spec.start_time, rate)
    action = bpy.data.actions.new(action_name(str(spec.name)))
    action.use_fake_user = True
    container = ensure_fcurves(action)
    legacy = _legacy_fcurves(action) is not None
    if not frames:
        raise AnimActionError("这条 clip 的采样时刻是空的 —— 建不出关键帧")
    for bone in spec.bones:
        bone_name = mapping[bone.path]
        for curve in spec.curves:
            if curve.bone_path != bone.path or curve.channel is None:
                continue
            if curve.channel == _QUAT_CHANNEL:
                quat = _quat_values(spec, bone.path, len(frames), curve.channel)
                if quat is None:
                    raise AnimActionError(
                        f"{bone.path} 的四元数不齐（要 x/y/z/w 四条曲线）—— 缺一条就摆不对姿势",
                    )
                values = [row[curve.fcurve_key[2]] for row in quat]
            else:
                values = [float(v) for _t, v in curve.keys]
            kwargs: dict[str, Any] = {
                "data_path": f'pose.bones["{bone_name}"].{curve.channel}',
                "index": int(curve.fcurve_key[2]),
            }
            if legacy:
                # ⚠ 4.4+ 的 channelbag fcurves.new() **不认** action_group（真机 TypeError）
                kwargs["action_group"] = f"pose.bones[{bone_name!r}]"
            fcurve = container.new(**kwargs)
            fcurve.keyframe_points.add(len(frames))
            for i, (frame, value) in enumerate(zip(frames, values, strict=True)):
                point = fcurve.keyframe_points[i]
                point.co = (float(frame), float(value))
                point.interpolation = interpolation
            fcurve.update()
    _verify(action, spec, mapping, frames)
    return action


#: 四元数通道名（= 内核 `anim_action.CHANNEL_QUATERNION` 的**同一个值**）。
_QUAT_CHANNEL = "rotation_quaternion"


def _verify(action: Any, spec: Any, mapping: dict[str, str], frames: tuple[float, ...]) -> None:
    """建完回读：曲线数 / 骨名 / 帧区间 / 关键帧数四项；对不上就抛。"""
    curves = action_fcurves(action)
    want_names = {mapping[b.path] for b in spec.bones}
    got_names = {name for name in (_bone_of(fc) for fc in curves) if name is not None}
    if got_names != want_names:
        raise AnimActionError(
            f"回读骨名对不上：建成 {sorted(got_names)}，规格要 {sorted(want_names)}"
        )
    if len(curves) != spec.n_curves:
        raise AnimActionError(f"回读曲线数对不上：Action {len(curves)} 条，规格 {spec.n_curves} 条")
    want_range = (min(frames), max(frames))
    got_range = (
        min((fc.keyframe_points[0].co[0] for fc in curves), default=0.0),
        max((fc.keyframe_points[-1].co[0] for fc in curves), default=0.0),
    )
    if any(abs(a - b) > 1e-6 for a, b in zip(want_range, got_range, strict=True)):
        raise AnimActionError(f"回读帧区间对不上：Action {got_range}，规格 {want_range}")
    for fc in curves:
        if len(fc.keyframe_points) != len(frames):
            raise AnimActionError(
                f"回读关键帧数对不上：{fc.data_path}[{fc.array_index}] "
                f"{len(fc.keyframe_points)} 个，规格 {len(frames)} 个"
            )


def read_action(action: Any) -> dict[str, Any]:
    """回读一份 Action 的读数（面板与探针都用它）。"""
    curves = action_fcurves(action)
    frames = [p.co[0] for fc in curves for p in fc.keyframe_points]
    bones: dict[str, list[str]] = {}
    for fc in curves:
        name = _bone_of(fc) or str(fc.data_path)
        channel = str(fc.data_path).rsplit(".", 1)[-1]
        bones.setdefault(name, [])
        if channel not in bones[name]:
            bones[name].append(channel)
    return {
        "name": action.name,
        "fcurves": len(curves),
        "keys": sum(len(fc.keyframe_points) for fc in curves),
        "frame_range": (min(frames), max(frames)) if frames else (0.0, 0.0),
        "bones": bones,
    }


def active_armature(context: Any) -> Any | None:
    """当前激活对象里的骨架（⛔ 不猜别的类型、⛔ 不凭空造一个）。"""
    obj = getattr(context, "object", None)
    if obj is not None and getattr(obj, "type", None) == "ARMATURE":
        return obj
    for candidate in getattr(context, "selected_objects", ()) or ():
        if getattr(candidate, "type", None) == "ARMATURE":
            return candidate
    return None


# --------------------------------------------------------------------------- #
# 解规格（**唯一入口**：算子与探针走同一条）
# --------------------------------------------------------------------------- #
def load_spec(clip: str) -> tuple[Any, list[str]]:
    """一条 clip -> 内核的 `AnimSpec`（＋人读几行）。**只读夹具**，⛔ 不开那个 3.4 GB 的包。

    零件全是产品内核的（`builder.read_raw_type_table` / `clone.read_object` /
    `anim_path.read_path_table` / `anim_action.build_action_spec`）——
    本模块**不另写**一套解码。
    """
    from ba_mod_maker.asset_kernel import anim_action, anim_build, anim_path, builder, clone

    rows = _CLIPS or read_clip_index()
    text = str(clip).strip()
    hits = [r for r in rows if str(r.get("name")) == text or str(r.get("path_id")) == text]
    if not hits:
        raise AnimActionError(
            f"清单里没有 {text!r} 的 clip（共 {len(rows)} 条）—— 先点「读 clip 清单」看名字"
        )
    if len(hits) > 1:
        raise AnimActionError(
            f"有 {len(hits)} 条同名 clip {text!r}：pathID={[r['path_id'] for r in hits]}"
            " —— 同名请直接填 pathID"
        )
    row = hits[0]
    meta = require_asset(clip_meta(), "units_meta.bin").read_bytes()
    nodes = (
        builder.read_raw_type_table(
            meta,
            common_strings=clone.unitypy_common_strings(),
        )
        .entry_for(anim_build.CLASS_ANIMATION_CLIP)
        .nodes
    )
    blob = require_asset(clip_blob(), "clips_payloads.bin").read_bytes()
    start = int(row["offset"])
    raw = blob[start : start + int(row["length"])]
    fields = clone.read_object(raw, nodes)
    table = anim_path.read_path_table(require_asset(path_table(), "_nodepaths.json"))
    chain_path = require_asset(node_index(), "_nodeindex.json")
    chain = json.loads(chain_path.read_text(encoding="utf-8"))["nodes"]
    spec = anim_action.build_action_spec(
        fields,
        table,
        path_id=int(row["path_id"]),
        allow_partial=True,
        chain_source=chain,
    )
    return spec, spec_lines(spec)


def ruler_of(spec: Any, bone_names: Any) -> tuple[int, str]:
    """★ **尺子**：规格里的骨路径 与 这一串骨名 对不上的条数（＋人读那一行）。

    ⛔ 纯函数、不碰 bpy、不读盘 —— 正控/负控与面板画的是**同一条**。
    """
    paths = [b.path for b in spec.bones]
    _mapping, missing = resolve_bone_names(paths, bone_names)
    return len(missing), ruler_line(missing, len(paths))


# --------------------------------------------------------------------------- #
# 算子
# --------------------------------------------------------------------------- #
def operator_ready(idname: str) -> bool:
    """这个算子注册上了吗。⚠ 没注册就画红行 —— `layout.operator()` 在 draw 里抛异常会让整格空白。"""
    space, _, name = idname.partition(".")
    return hasattr(bpy.types, f"{space.upper()}_OT_{name}")


def _redraw() -> None:
    """让 3D 视图按需重画（后台模式没有窗口，重画是尽力而为，⛔ 不抛）。"""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:  # noqa: BLE001  —— 尽力而为
        return


class BamodAnimScan(bpy.types.Operator):
    """读游戏里的 clip 清单（**只读**：一个字节都不写、不开包）。"""

    bl_idname = "ba_mod.anim_scan"
    bl_label = "读 clip 清单"
    bl_description = "读真件冻结的 clips_index.json（475 条）—— 只读，不开那个 3.4 GB 的包"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _NEGCTL_LINE
        try:
            rows = read_clip_index()
        except Exception as exc:  # noqa: BLE001  —— 报给面板，⛔ 不许把 Blender 弄崩
            _NEGCTL_LINE = ""
            self.report({"ERROR"}, f"读 clip 清单失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        set_clips(rows)
        setter = getattr(pc, "set_session", None)
        if callable(setter):
            setter(anim_clips=len(rows))
        _redraw()
        self.report({"INFO"}, index_line(rows))
        return {"FINISHED"}


class BamodAnimSpec(bpy.types.Operator):
    """选一条 clip -> 解成规格并画读数（只读）。"""

    bl_idname = "ba_mod.anim_spec"
    bl_label = "解规格（读 clip）"
    bl_description = "把一条 clip 解成 Action 规格（曲线 / 骨路径 / 通道 / 采样）—— 只读"

    clip: bpy.props.StringProperty(  # type: ignore[attr-defined]
        name="clip（名字或 pathID）",
        default=DEFAULT_CLIP,
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _ACTION_LINES, _ACTION_NOTE, _RULER, _NEGCTL_LINE
        try:
            spec, lines = load_spec(self.clip)
        except Exception as exc:  # noqa: BLE001
            _ACTION_LINES, _RULER, _NEGCTL_LINE = [], None, ""
            _ACTION_NOTE = ("", "INFO")
            self.report({"ERROR"}, f"解规格失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        set_spec(spec, lines)
        _ACTION_LINES, _RULER, _NEGCTL_LINE = [], None, ""
        _ACTION_NOTE = ("", "INFO")
        setter = getattr(pc, "set_session", None)
        if callable(setter):
            setter(anim_clip=spec.name, anim_curves=spec.n_curves)
        _redraw()
        self.report({"INFO"}, f"{spec.name}：曲线 {spec.n_curves} 条 · {spec.stats.line()}")
        return {"FINISHED"}


class BamodAnimBuild(bpy.types.Operator):
    """规格 -> 真 Action（落在当前骨架的 pose bone 上）。"""

    bl_idname = "ba_mod.anim_build"
    bl_label = "建 Action"
    bl_description = "把解出来的规格建成真 Action（要先在视口里选中一个骨架）"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _ACTION_LINES, _ACTION_NOTE
        spec = _SPEC
        if spec is None:
            self.report({"ERROR"}, "还没有规格 —— 先点「解规格（读 clip）」")
            return {"CANCELLED"}
        armature = active_armature(context)
        if armature is None:
            self.report(
                {"ERROR"},
                "缺这一跳：场景里没有骨架 —— 建 Action 要有 ARMATURE；"
                "「把游戏里的骨架导进来」本插件还没有（导入侧 rig_plan 那条线）",
            )
            _ACTION_NOTE = ("红 —— 缺这一跳：场景里没有骨架（建 Action 要有 ARMATURE）", "ERROR")
            return {"CANCELLED"}
        try:
            action = create_action(spec, armature)
        except Exception as exc:  # noqa: BLE001
            _ACTION_LINES = [f"建 Action 失败：{type(exc).__name__}: {exc}"]
            _ACTION_NOTE = ("红 —— " + _ACTION_LINES[0], "ERROR")
            self.report({"ERROR"}, _ACTION_LINES[0])
            return {"CANCELLED"}
        read = read_action(action)
        _ACTION_LINES = [
            f"Action {action.name!r} 落在 {armature.name!r} 上",
            f"FCurve {read['fcurves']} 条 · 关键帧 {read['keys']} 个"
            f" · 帧区间 {read['frame_range']}",
            f"骨：{read['bones']}",
        ]
        _ACTION_NOTE = (
            f"绿 · {action.name} · FCurve {read['fcurves']} 条 · 关键帧 {read['keys']} 个",
            "CHECKMARK",
        )
        _redraw()
        note = f"{action.name}：FCurve {read['fcurves']} 条 · 关键帧 {read['keys']} 个"
        self.report({"INFO"}, note)
        return {"FINISHED"}


class BamodAnimRuler(bpy.types.Operator):
    """★ 尺子：规格的骨路径 与 当前骨架的真骨名 对不上的条数（0 = 绿 / ≥1 = **红**）。"""

    bl_idname = "ba_mod.anim_ruler"
    bl_label = "骨名对账（尺子）"
    bl_description = "量「规格里的骨路径有几条在当前骨架上找不到」—— 0 条绿，≥1 条红"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _RULER
        spec = _SPEC
        if spec is None:
            self.report({"ERROR"}, "还没有规格 —— 先点「解规格（读 clip）」")
            return {"CANCELLED"}
        armature = active_armature(context)
        if armature is None:
            self.report(
                {"ERROR"},
                "缺这一跳：场景里没有骨架 —— 尺子要拿骨架的真骨名表来对；"
                "「把游戏里的骨架导进来」本插件还没有",
            )
            return {"CANCELLED"}
        names = [b.name for b in armature.data.bones]
        count, line = ruler_of(spec, names)
        _RULER = (count, line)
        _redraw()
        if count:
            self.report({"ERROR"}, line)
            return {"CANCELLED"}
        self.report({"INFO"}, line)
        return {"FINISHED"}


class BamodAnimNegCtl(bpy.types.Operator):
    """负控：喂一个**已知对不上**的骨名表进去 —— 它必须红（§0.9 动作 2）。"""

    bl_idname = "ba_mod.anim_negctl"
    bl_label = "负控（必须红）"
    bl_description = "抽掉一根骨再量一次；没红就是尺子坏了"

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _NEGCTL_LINE
        spec = _SPEC
        if spec is None:
            self.report({"ERROR"}, "还没有规格 —— 先点「解规格（读 clip）」，负控要拿真规格来喂")
            return {"CANCELLED"}
        paths = [b.path for b in spec.bones]
        leaves = [b.path.rsplit("/", 1)[-1] for b in spec.bones]
        mapping, _missing = resolve_bone_names(paths, leaves)
        good = sorted(mapping.values())
        if not good:
            self.report({"ERROR"}, "规格里一根骨都没有 —— 负控喂不进去")
            return {"CANCELLED"}
        broken = good[:-1]
        dropped = good[-1]
        count, line = ruler_of(spec, broken)
        _NEGCTL_LINE = f"抽掉 {dropped!r} 之后：{line}"
        if count == 0:
            self.report({"ERROR"}, f"负控**没红** —— 尺子坏了（抽掉 {dropped!r} 都还报 0 条）")
            return {"CANCELLED"}
        _redraw()
        self.report({"INFO"}, f"负控如预期：{_NEGCTL_LINE}")
        return {"FINISHED"}


OPERATORS: tuple[type, ...] = (
    BamodAnimScan,
    BamodAnimSpec,
    BamodAnimBuild,
    BamodAnimRuler,
    BamodAnimNegCtl,
)


# --------------------------------------------------------------------------- #
# 面板
# --------------------------------------------------------------------------- #
class VIEW3D_PT_ba_anim:
    """⑨ 动画：清单 -> 规格 -> Action -> 尺子。⛔ 只读 ＋ 只在 Blender 里建 Action。

    ★ 2026-10-15 五格归并：**面板已退役** —— 内容搬进「④ 出 · 装机 · 登记」里的折叠块；
    `draw(layout, context)` **逐行未动**，只是调用方从 Blender 换成了那一格。
    ⛔ 别把它加回 `__init__._CLASSES` —— 它已经不是 `bpy.types.Panel` 了。
    """

    bl_label = "⌁ 动画（② 改的一条路）"
    bl_idname = "VIEW3D_PT_ba_anim"

    @staticmethod
    def draw(layout, context: bpy.types.Context) -> None:
        arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        skins = [
            o
            for o in bpy.data.objects
            if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)
        ]
        bone_names = {b.name for b in arms[0].data.bones} if arms else set()
        bad: list[str] = []
        if arms and bone_names:
            for o in skins[:20]:
                miss = sorted({g.name for g in o.vertex_groups} - bone_names)
                if miss:
                    bad.append(f"{o.name}:{len(miss)}")

        # ---- 摘要（≤6 行）：clip 条数 / 当前那条 / 骨名对不上几条（红必须看得见）----
        box = layout.box()
        pc.label_wrapped(
            box, "① clip：" + clips_note(_CLIPS), icon="CHECKMARK" if _CLIPS else "INFO"
        )
        pc.label_wrapped(
            box, "② 规格：" + spec_note(_SPEC), icon="CHECKMARK" if _SPEC is not None else "INFO"
        )
        if _ACTION_NOTE[0]:
            pc.label_wrapped(box, "③ Action：" + _ACTION_NOTE[0], icon=_ACTION_NOTE[1])
        ruler_text, ruler_icon = ruler_note(_RULER)
        pc.label_wrapped(box, "④ 尺子：" + ruler_text, icon=ruler_icon)
        site = f"现场：骨架 {len(arms)} 个"
        if arms:
            site += f" · 骨 {len(bone_names)} 根"
        if skins:
            site += f" · 顶点组对不上 {len(bad)} 个网格"
        pc.label_wrapped(box, site, icon="ERROR" if bad else "INFO")
        # ★ 2026-10-17：夹具缺了就**在摘要里**说一句（装成插件之后它们本来就不在）——
        #   ⛔ 不许静默空，也⛔ 不许等用户点一下才抛出来。
        missing_assets = assets_note()
        if missing_assets:
            pc.label_wrapped(box, missing_assets, icon="ERROR")
            if pc.workspace_root() is None:
                pc.label_wrapped(box, pc.WORKSPACE_HINT, icon="INFO")

        row = layout.row(align=True)
        for idname, label, icon in (
            ("ba_mod.anim_scan", "读 clip 清单", "FILE_REFRESH"),
            ("ba_mod.anim_spec", "解规格（读 clip）", "FCURVE"),
        ):
            if operator_ready(idname):
                row.operator(idname, text=label, icon=icon)
            else:
                pc.label_wrapped(row, f"{label}：算子未注册", icon="ERROR")
        # ★ u5：一行 ≤2 颗（这里原来 3 颗）⇒ 分两行；**每行就地创建再填**
        #   （⛔ 不许提前建好两行存起来：尺子的记录器按「当前行」记账，提前建的两行会记成两个空行）
        for entries in (
            (
                ("ba_mod.anim_build", "建 Action", "ARMATURE_DATA"),
                ("ba_mod.anim_ruler", "骨名对账（尺子）", "DRIVER"),
            ),
            (("ba_mod.anim_negctl", "负控（必须红）", "GHOST_ENABLED"),),
        ):
            row2 = layout.row(align=True)
            for idname, label, icon in entries:
                if operator_ready(idname):
                    row2.operator(idname, text=label, icon=icon)
                else:
                    pc.label_wrapped(row2, f"{label}：算子未注册", icon="ERROR")

        pc.detail_row(layout, "08")
        if not pc.detail_on("08"):
            return

        # ---- 明细（默认不画；⛔ 数据没删，只是收进这里）----
        clip = layout.box()
        pc.label_wrapped(clip, "① 游戏里的 clip（真件冻结的清单）", icon="ACTION")
        reading(clip, "清单文件", asset_line("clips_index.json", clip_index()))
        reading(clip, "夹具字节数", asset_line("clips_payloads.bin", clip_blob()))
        pc.label_wrapped(clip, "来源 = 研究线从 units 包冻出来的那份（kb/23 同数：475 条）")
        pc.label_wrapped(clip, "⛔ 本格不开那个 3.4 GB 的包；要现读真包走 CLI：anim list --bundle")
        if _CLIPS:
            reading(clip, "读数", index_line(_CLIPS))
            pc.label_wrapped(clip, "例：" + ", ".join(str(r.get("name")) for r in _CLIPS[:4]))
        else:
            pc.label_wrapped(clip, "还没读 —— 点上面「读 clip 清单」", icon="INFO")

        spec = layout.box()
        pc.label_wrapped(spec, "② 选中那条的曲线 / 骨名读数", icon="FCURVE")
        if _SPEC is None:
            pc.label_wrapped(
                spec, f"还没解 —— 点「解规格（读 clip）」，默认 {DEFAULT_CLIP}", icon="INFO"
            )
        else:
            for line in _SPEC_LINES:
                pc.label_wrapped(spec, line)

        if _ACTION_LINES:
            built = layout.box()
            if _ACTION_NOTE[1] == "CHECKMARK":
                pc.label_wrapped(built, "③ 建出来的 Action（回读四项）", icon="CHECKMARK")
            else:
                pc.label_wrapped(built, "③ 建 Action 没成", icon="ERROR")
            for line in _ACTION_LINES:
                pc.label_wrapped(built, line)

        ruler = layout.box()
        pc.label_wrapped(ruler, "④ 尺子：骨名对不上的条数", icon="GHOST_ENABLED")
        pc.label_wrapped(ruler, "正控 = 骨名齐全 ⇒ 0 条（绿）；负控 = 抽掉一根 ⇒ ≥1 条（红）")
        if _RULER is not None:
            count, line = _RULER
            pc.label_wrapped(ruler, line, icon="CHECKMARK" if count == 0 else "ERROR")
        if _NEGCTL_LINE:
            pc.label_wrapped(ruler, "负控：" + _NEGCTL_LINE, icon="GHOST_ENABLED")

        here = layout.box()
        pc.label_wrapped(here, "现场（当前场景）", icon="ARMATURE_DATA")
        reading(here, "Armature 数", str(len(arms)))
        if arms:
            names = sorted(bone_names)
            reading(here, "第一根的名字", names[0] if names else "(空)")
            reading(here, "骨数", str(len(names)))
            pc.label_wrapped(here, "前 6 根：" + ", ".join(names[:6]))
        reading(here, "挂了 Armature 修改器的网格", str(len(skins)))
        pc.label_wrapped(here, "合同：骨名与顶点组名必须逐字相同", icon="INFO")
        pc.label_wrapped(here, "差一个字符 = 那根骨静默不生效 —— 建完必须回读核对")
        pc.label_wrapped(here, "⛔ Blender 重名会自动加 .001（也是静默失效）")
        if skins:
            reading(here, "顶点组名对不上骨架的网格", ", ".join(bad) if bad else "无")

        todo = layout.box()
        pc.label_wrapped(todo, "⛔ 本格不做的（如实写，不画假按钮）", icon="ERROR")
        pc.label_wrapped(todo, "⑤ 写回游戏：改时长 / 装回包在 CLI（anim stretch · anim apply）")
        pc.label_wrapped(todo, "   但 Animator 状态机 / 依赖 hash —— 谁都没有这一跳")
        pc.label_wrapped(
            todo, "③ 的前一跳：把游戏里的骨架导进 Blender（rig_plan 那条线）—— 本插件没有"
        )
        pc.label_wrapped(todo, "人形肌肉曲线那条族**不导入**（数学不在托管源码里）")
        pc.label_wrapped(todo, "实测基准：T-72B 30 根骨（docs/讲-7-骨架（Armature）.md）")
        pc.label_wrapped(
            layout, "本面板不起任何进程：解规格 / 建 Action 全在 Blender 内完成", icon="INFO"
        )
