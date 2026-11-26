r"""D 线 · Blender 侧入口（**bpy 那一层**）：拿件时把 clip 一起建 Action / 播一遍 / 改时长 / 写回。

## 对外 API（★ 面板与探针都走这四条，**都返回读数行 `list[str]`**）

    import_actions(game_root, unit_key, *, clips=None, armature=None, offline=False) -> list[str]
        拿件时**一起建**：该件的每条 clip -> 真 `bpy.types.Action` -> 挂到骨架上。
    play_once(scene=None, *, action=None, fps=None) -> list[str]
        设 `scene.frame_start/frame_end` 并**播一遍**（帧区间来自 Action 自己的关键帧）。
    stretch(action, seconds, *, fps=None) -> list[str]
        整条时间轴缩放到 `seconds`（**同一条 clip、不增删关键帧**）—— 写回时 `k = new/old`。
    apply_writeback(bundle, spec, *, ...) -> list[str]
        走产品 `anim apply`：默认**干跑**，一个字节都不写；读数里带干跑前后 sha256。

## 这一跳的要害（2026-10-15 无头实测，别凭印象）

clip 绑的是 **prefab 节点路径**（`T72B hatch` -> `root/body/turret_0/hatch`），**不是**蒙皮骨表：

- 车体 `RU_T72B` 那副骨架 **30** 根骨全是轮系（`L001..RR007` + `body`/`root`）—— 没有 `hatch`；
- 炮塔 `RU_T72B/RU_T72B` 那副骨架 **11** 根骨**含 `hatch`/`turret_0`** —— 那条 clip 挂在**它**身上。

⇒ 「挂到骨架上」= **在这一串路径解得开的那副骨架上挂**（`pick_armature`），
⛔ 不是「挂到当前激活的那副」—— 那样建 Action 会当场抛。
真读数：`1 条节点路径在骨架上找不到对应的骨：['root/body/turret_0/hatch']`。

## 三条硬口径

1. **解不开就报红**（点名哪几条路径、试过哪几副骨架）—— ⛔ 不许造指向空气的 FCurve
   （那种「面板上有、动起来没有」的假成功正是这一线要压掉的东西）；
2. **不闭眼写**：`apply_writeback` 默认干跑；真写要显式 `dry_run=False` **并给 `project_root`**
   （不给的话内核那条链会去拷整包 —— 实测 +3.4 GB / 73.6 s）；
3. **建 Action 只有一份实现**：转调 `panels_09_anim` 那几条（`create_action` / `ensure_fcurves` /
   `action_fcurves` / `read_action` / `ruler_of`）—— 本模块**不另写**一套 FCurve 写入。
"""

# ruff: noqa: SLF001
# ↑ 豁免只给这一个文件：⑨ 那条链的**唯一实现**在 panels_09_anim.py（真机验过的 slotted-action
#   兼容封装 ensure_fcurves/action_fcurves 就在那儿）—— 本模块**转调**它，⛔ 不抄第二份。
#   口径同 blender_addon/anim_entry.py 的前身（baseline_tree 那份）。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import bpy

from ba_mod_maker.asset_kernel import anim_write, clone, reverse_delta

from . import anim_bridge
from . import panels_09_anim as pa

__all__ = [
    "ACTION_PREFIX",
    "ANIM_OPERATORS",
    "OPERATORS",
    "K_PROP",
    "AnimationActionError",
    "BamodAnimImportActions",
    "BamodAnimPlay",
    "BamodAnimWriteback",
    "BamodAnimWritebackCheck",
    "BamodAnimWritebackPlan",
    "action_seconds",
    "apply_writeback",
    "import_actions",
    "last_lines",
    "pick_armature",
    "play_once",
    "read_action",
    "stretch",
    "stretched_clip_bytes",
    "writeback_guards",
]

#: Action 的名字前缀（`T72B hatch` -> `BA_T72B hatch`；⛔ 不让游戏来的 Action
#: 和用户自己做的分不清）—— 名字那份实现只有 `panels_09_anim.action_name`。
ACTION_PREFIX = pa.ACTION_PREFIX
#: 与 `anim_bridge.AnimActionError` **同一个类型**（面板/算子捕一个就够了）。
AnimationActionError = anim_bridge.AnimActionError
#: 面板那一层读的 `bpy.types.Action`（转调，⛔ 不是副本）。
read_action = pa.read_action

#: 上一次的读数（算子写、面板读）。⚠ 与 ⑨ 面板同一个取舍：重启 Blender 即清空。
_LINES: list[str] = []


def last_lines() -> list[str]:
    """上一次 API / 算子留下的读数行（面板与探针读**同一份**）。"""
    return list(_LINES)


def _set_lines(lines: Sequence[str]) -> list[str]:
    """记下这一次的读数（顺带写进会话摘要，面板只画不计算）。"""
    global _LINES
    _LINES = [str(line) for line in lines]
    return list(_LINES)


# --------------------------------------------------------------------------- #
# 挂哪副骨架（★ 这一跳最容易想错的地方）
# --------------------------------------------------------------------------- #
def _scene(scene: Any = None) -> Any:
    """拿场景：显式给的优先，否则 `bpy.context.scene`（无头下也可能没有）。"""
    if scene is not None:
        return scene
    return getattr(bpy.context, "scene", None)


def scene_armatures(scene: Any = None, *, prefer: Any = None) -> tuple[Any, ...]:
    """场景里的骨架，**激活的那个排第一**，其余按名字（顺序固定 = 读数可复算）。"""
    context = bpy.context
    active = prefer if prefer is not None else getattr(context, "object", None)
    if active is not None and getattr(active, "type", None) != "ARMATURE":
        active = None
    found = [obj for obj in bpy.data.objects if getattr(obj, "type", None) == "ARMATURE"]
    found.sort(key=lambda obj: str(obj.name))
    if active is not None and active in found:
        found.remove(active)
        found.insert(0, active)
    return tuple(found)


def pick_armature(
    spec: Any,
    armatures: Sequence[Any],
) -> tuple[Any | None, str]:
    """挑**这一串路径解得开**的那副骨架（⛔ 不挑"当前激活的"）。

    :return: `(骨架或 None, 一行读数)`。`None` 时那一行点名哪几条路径对不上、试过哪几副骨架。
    """
    tried: list[str] = []
    for armature in armatures:
        names = [bone.name for bone in armature.data.bones]
        count, line = pa.ruler_of(spec, names)
        tried.append(f"{armature.name}（{len(names)} 根骨，对不上 {count} 条）")
        if count == 0:
            return armature, f"骨架 {armature.name!r}：{line}"
    if not tried:
        return None, "场景里一个 ARMATURE 都没有 —— 拿件时要连骨架一起拿进来"
    _count, line = pa.ruler_of(spec, [])
    return None, f"没找到解得开这条 clip 的骨架：{line}；试过 {tried}"


def _attach(armature: Any, action: Any) -> str:
    """把 Action **挂上去**（视口里能播的最后一公里），返回那一行读数。"""
    if armature.animation_data is None:
        armature.animation_data_create()
    armature.animation_data.action = action
    slots = list(getattr(action, "slots", ()) or ())
    note = ""
    if slots:
        try:
            armature.animation_data.action_slot = slots[0]
        except (AttributeError, TypeError) as exc:
            note = f"（action_slot 没设上：{type(exc).__name__}）"
    return f"挂到 {armature.name!r} 的 animation_data.action{note}"


# --------------------------------------------------------------------------- #
# ① 拿件时一起建（★ 本单的主活）
# --------------------------------------------------------------------------- #
def _spec_get(spec: Any, name: str, default: Any = None) -> Any:
    """从「映射或对象」里取一个字段（写回那几颗按钮两种都传得进来）。"""
    if spec is None:
        return default
    if isinstance(spec, Mapping):
        return spec.get(name, default)
    return getattr(spec, name, default)


def import_actions(
    game_root: Any,
    unit_key: str,
    *,
    clips: Any = None,
    armature: Any = None,
    offline: bool = False,
    fixtures: Any = None,
    meta: Any = None,
    path_table: Any = None,
    node_index: Any = None,
    allow_partial: bool = True,
    attach: bool = True,
) -> list[str]:
    """★ **拿件时把该件的 clip 一起建成真 Action 并挂到骨架上**（返回读数行）。

    :param clips: 已经解过的 `UnitAnim`（面板缓存用；不给就现解）。
    :param armature: 优先考虑的那副骨架（不给就用激活对象）。
    :param offline: True = 走冻结夹具（不开那个 3.4 GB 的包）；读数里写明出处。
    :return: 读数行：每条 clip 一行「绿 / 红（点名）」＋ 合计那一行。
    """
    fallback_note = ""
    if isinstance(clips, anim_bridge.UnitAnim):
        unit = clips
    else:
        try:
            unit = anim_bridge.clips_of_unit(
                game_root,
                unit_key,
                offline=offline,
                fixtures=fixtures,
                meta=meta,
            )
        except anim_bridge.AnimBridgeError as exc:
            # ★ 2026-10-18（用户真机）：炮塔 prefab（`RU_T72B/RU_T72B`）**没有 Animator**
            #   —— 动画在**车体**那条 controller 上。炮塔键来点「建该件的动画」时，
            #   自动退到它的车体键重试，并把这句如实写进读数（⛔ 不静默换件）。
            head, sep, _tail = str(unit_key).partition("/")
            if not (sep and head) or offline:
                raise
            unit = anim_bridge.clips_of_unit(
                game_root,
                head,
                offline=offline,
                fixtures=fixtures,
                meta=meta,
            )
            fallback_note = (
                f"    ⚠ {unit_key} 的 prefab 没有 Animator（{str(exc)[:60]}…）"
                f" ⇒ 改用它的车体 {head} 的 controller"
            )
    lines = [f"① 这一件挂的动画（{unit.unit_key}）："]
    if fallback_note:
        lines.insert(0, fallback_note)
    lines.extend("    " + item for item in unit.lines())
    if not unit.clips:
        lines.append(
            f"红 —— controller {unit.controller!r} 一条 clip 都没引用（⛔ 不造空 Action 冒充成功）",
        )
        return _set_lines(lines)
    store = anim_bridge.open_clip_store(unit, offline=offline, fixtures=fixtures, meta=meta)
    table = anim_bridge.read_path_table(path_table)
    chain = anim_bridge.read_chain(node_index)
    armatures = scene_armatures(prefer=armature)
    built = 0
    hooked = 0
    curves = 0
    skipped: list[str] = []
    for ref in store.refs:
        try:
            spec = anim_bridge.spec_for(
                store.store,
                ref.path_id,
                table,
                chain,
                allow_partial=allow_partial,
            )
        except Exception as exc:  # noqa: BLE001 —— 一条坏 clip 不许带垮整件
            skipped.append(ref.name)
            lines.append(
                f"    clip {ref.name!r}（pathID={ref.path_id}）：红 —— 解不成规格："
                f"{type(exc).__name__}: {str(exc)[:120]}",
            )
            continue
        found, why = pick_armature(spec, armatures)
        if found is None:
            skipped.append(ref.name)
            lines.append(f"    clip {ref.name!r}：红 —— {why}")
            continue
        try:
            action = pa.create_action(spec, found)
        except Exception as exc:  # noqa: BLE001
            skipped.append(ref.name)
            lines.append(
                f"    clip {ref.name!r}：红 —— 建 Action 失败："
                f"{type(exc).__name__}: {str(exc)[:160]}",
            )
            continue
        read = pa.read_action(action)
        _remember(action, ref, spec)
        built += 1
        curves += int(read["fcurves"])
        lo, hi = read["frame_range"]
        lines.append(
            f"    clip {ref.name!r}：绿 —— Action {action.name!r} · FCurve {read['fcurves']} 条"
            f" · 关键帧 {read['keys']} 个 · 帧区间 {lo:g}-{hi:g} · {why}",
        )
        if attach:
            lines.append("        " + _attach(found, action))
            hooked += 1
    lines.append(
        f"② 合计：建成 Action {built} 条（挂上 {hooked} 条）· FCurve {curves} 条"
        f" · 试过骨架 {[obj.name for obj in armatures]}"
    )
    if skipped:
        lines.append(f"    ⚠ 没建成的：{skipped}（上面每一条都有点名）")
    return _set_lines(lines)


def _remember(action: Any, ref: anim_bridge.ClipRef, spec: Any) -> None:
    """把「这条 Action 是哪条 clip / 几赫兹」记在 Action 自己身上（读回时不用再解一遍）。"""
    for key, value in (
        ("bamod_clip", str(spec.name)),
        # ⚠ pathID 是 **int64**（真件 `T72B hatch` = 6926808325590346151），而 Blender 的
        #   IDProperty 整数是 32 位 ⇒ 存 int 当场 `OverflowError: Python int too large to
        #   convert to C int`（2026-10-15 探针真踩）。⇒ 存**字符串**，读的时候自己 int()。
        ("bamod_path_id", str(int(ref.path_id))),
        ("bamod_fps", float(spec.sample_rate)),
        ("bamod_seconds", float(spec.duration)),
    ):
        try:
            action[key] = value
        except (AttributeError, TypeError, ValueError):
            continue


# --------------------------------------------------------------------------- #
# ② 播一遍（帧区间 ＋ 真的按播放键）
# --------------------------------------------------------------------------- #
def _action_owner(action: Any = None) -> tuple[Any | None, Any | None]:
    """`(挂着 Action 的对象, Action)` —— 激活对象优先，其余按名字（顺序固定）。"""
    context = bpy.context
    if action is not None:
        for obj in bpy.data.objects:
            data = getattr(obj, "animation_data", None)
            if data is not None and data.action is action:
                return obj, action
        return None, action
    active = getattr(context, "object", None)
    data = getattr(active, "animation_data", None) if active is not None else None
    if data is not None and data.action is not None:
        return active, data.action
    for obj in sorted(bpy.data.objects, key=lambda item: str(item.name)):
        data = getattr(obj, "animation_data", None)
        if data is not None and data.action is not None:
            return obj, data.action
    return None, None


def _fps_of(action: Any, fps: float | None = None) -> float:
    """这条 Action 的帧率：显式给的 > Action 自己记着的 > 场景的 > 24。"""
    if fps:
        return float(fps)
    recorded = action.get("bamod_fps") if hasattr(action, "get") else None
    if recorded:
        return float(recorded)
    scene = getattr(bpy.context, "scene", None)
    return float(getattr(getattr(scene, "render", None), "fps", 0) or 24)


def action_seconds(action: Any, *, fps: float | None = None) -> tuple[float, float, float]:
    """`(起始帧, 结束帧, 时长秒)` —— 时长 = 帧跨度 / fps（**帧号就是采样点**）。"""
    read = pa.read_action(action)
    lo = float(read["frame_range"][0])
    hi = float(read["frame_range"][1])
    rate = _fps_of(action, fps)
    return lo, hi, ((hi - lo) / rate if rate > 0 else 0.0)


def play_once(
    scene: Any = None,
    *,
    action: Any = None,
    fps: float | None = None,
    repeat: bool = False,
) -> list[str]:
    """★ **播一遍**：设 `frame_start/frame_end`（来自 Action 自己的关键帧）并按下播放。

    ⚠ 无头（`blender -b`）里没有窗口，`screen.animation_play()` 点不动 —— 那时读数里
    明写「帧区间设好了、播放键点不了」，⛔ 不谎报「正在播」。
    """
    scene = _scene(scene)
    if scene is None:
        raise AnimationActionError("没有场景 —— play_once 要一个 scene（无头下显式传）")
    owner, act = _action_owner(action)
    if act is None:
        return _set_lines(
            [
                "② 播一遍：红 —— 场景里没有任何对象挂着 Action（先「建该件的动画」）",
            ]
        )
    lo, hi = pa.read_action(act)["frame_range"]
    rate = _fps_of(act, fps)
    scene.frame_start = int(round(float(lo)))
    scene.frame_end = int(round(float(hi)))
    scene.frame_set(int(round(float(lo))))
    lines = [
        f"② 播一遍：{act.name!r}" + (f"（挂在 {owner.name!r} 上）" if owner is not None else ""),
        f"    帧区间 frame_start={scene.frame_start} · frame_end={scene.frame_end}"
        + f" · 共 {scene.frame_end - scene.frame_start + 1} 帧",
        f"    帧率 {rate:g} Hz（1 个采样点 = 1 帧）",
    ]
    if abs(float(getattr(scene.render, "fps", rate)) - rate) > 1e-6:
        scene.render.fps = max(1, int(round(rate)))
        lines.append(f"    场景 render.fps 跟到 {scene.render.fps}（否则播起来不是实时）")
    try:
        bpy.ops.screen.animation_play()
    except Exception as exc:  # noqa: BLE001 —— 无头 / 没有窗口是**正常**情形
        lines.append(
            f"    ⚠ 播放键点不了（{type(exc).__name__}: {str(exc)[:80]}）——"
            " 帧区间已经设好，真机上按空格就是这一遍",
        )
    else:
        lines.append("    正在播（再按一次播放键就停）")
    return _set_lines(lines)


# --------------------------------------------------------------------------- #
# ③ 改时长（stretch：同一条 clip、不增删关键帧）
# --------------------------------------------------------------------------- #
def stretch(action: Any, seconds: float, *, fps: float | None = None) -> list[str]:
    """★ 把整条时间轴缩放到 `seconds`（返回读数行；写回时 `k = 新/旧`）。

    ⛔ 只**改帧号**（含两个把手），关键帧个数一个不变 —— 所以写回天然等长。
    """
    if action is None:
        raise AnimationActionError("stretch 要一个 Action（先「建该件的动画」）")
    target = float(seconds)
    if target <= 0:
        raise AnimationActionError(f"目标时长 {target!r} 不合法（要 > 0 秒）")
    lo, hi, before = action_seconds(action, fps=fps)
    if before <= 0:
        raise AnimationActionError(
            f"{action.name!r} 的帧区间是空的（{lo:g}-{hi:g}）—— 没有时间轴可缩放",
        )
    k = target / before
    curves = pa.action_fcurves(action)
    keys = 0
    for fcurve in curves:
        for point in fcurve.keyframe_points:
            point.co = (lo + (float(point.co[0]) - lo) * k, float(point.co[1]))
            point.handle_left = (
                lo + (float(point.handle_left[0]) - lo) * k,
                float(point.handle_left[1]),
            )
            point.handle_right = (
                lo + (float(point.handle_right[0]) - lo) * k,
                float(point.handle_right[1]),
            )
            keys += 1
        fcurve.update()
    _lo2, _hi2, after = action_seconds(action, fps=fps)
    if abs(after - target) > max(1e-6, target * 1e-6):
        raise AnimationActionError(
            f"缩放后回读 {after:.6f} s ≠ 目标 {target:.6f} s —— 时间轴没改到",
        )
    return _set_lines(
        [
            f"③ 改时长：{action.name!r}",
            f"    k = {k:.6f}（{before:.4f} s -> {after:.4f} s）"
            + f" · FCurve {len(curves)} 条 · 关键帧 {keys} 个（一个没增减）",
            f"    写回用：k={k:.6f}（等价 ba-mod-maker anim apply --k {k:.6f}）",
        ]
    )


# --------------------------------------------------------------------------- #
# ④ 写回（★ 走产品 `anim apply`；⛔ 本模块不碰一个字节的写盘）
# --------------------------------------------------------------------------- #
def writeback_guards(source_raw: bytes, new_raw: bytes) -> None:
    """写回前的两条闸 —— 不成立就抛 `AnimationActionError`（⛔ 不静默写一次空改动）。

    ① **等长**：字节数必须与源**逐条相同**（变长 = 增删关键帧那一类，要动对象表）；
    ② **真的改了**：与源逐字节相同就是空改动（真件里有一类 clip 就是这样：
       `m_StopTime = 0.0` ⇒ 乘 k 还是 0）。
    """
    if len(new_raw) != len(source_raw):
        raise AnimationActionError(
            f"新对象 {len(new_raw):,} B 与源 {len(source_raw):,} B 不等：这不是「等长改写」",
        )
    if bytes(new_raw) == bytes(source_raw):
        raise AnimationActionError(
            "拉伸后逐字节不变（这条 clip 没有可改的时间轴）—— ⛔ 不写空改动",
        )


def stretched_clip_bytes(
    fields: Mapping[str, Any],
    payload: bytes,
    nodes: Sequence[Any],
    k: float,
) -> bytes:
    """这条 clip 拉伸 k 倍后的**对象字节**（⛔ 只有一份实现：`anim_write.stretch_clip`）。"""
    after, _report = anim_write.stretch_clip(fields, float(k), payload=payload, nodes=nodes)
    return clone.write_object(after, nodes)


def apply_writeback(
    bundle: Any,
    spec: Any = None,
    *,
    clip: Any = None,
    k: float | None = None,
    from_file: Any = None,
    dry_run: bool | None = None,
    project_root: Any = None,
    catalog: Any = None,
    game_root: Any = None,
) -> list[str]:
    """★ 把这条 clip 写回包（**默认干跑**：一个字节都不写，读数里带 sha256 逐位核对）。

    :param spec: 映射或对象，字段 `clip` / `k` / `from_file` / `dry_run` / `project_root` /
        `catalog` / `game_root`（显式关键字优先于它）—— 面板那两颗按钮直接传 dict。
    :raises AnimationActionError: 包打不开 / 没给 clip / 真写没给 `project_root` / 产品那一步失败。
    """
    raw_bundle = bundle if bundle not in (None, "") else _spec_get(spec, "bundle", "")
    path = Path(str(raw_bundle))
    if not path.is_file():
        raise AnimationActionError(f"打不开这个包：{path}")
    needle = clip if clip not in (None, "") else _spec_get(spec, "clip", "")
    if needle in (None, ""):
        raise AnimationActionError("没给 clip —— 写回要指定写哪一条（名字或 pathID）")
    ratio = k if k is not None else _spec_get(spec, "k")
    source_file = from_file if from_file is not None else _spec_get(spec, "from_file")
    dry = bool(_spec_get(spec, "dry_run", True)) if dry_run is None else bool(dry_run)
    root = project_root if project_root not in (None, "") else _spec_get(spec, "project_root")
    cat = catalog if catalog not in (None, "") else _spec_get(spec, "catalog")
    game = game_root if game_root not in (None, "") else _spec_get(spec, "game_root")
    if not dry and root in (None, ""):
        raise AnimationActionError(
            "真写必须先给 project_root（补丁柜落 <工程目录>/patches）"
            " —— 不给的话内核那条链会去拷整包（实测 +3.4 GB / 73.6 s）",
        )
    argv = anim_bridge.writeback_argv(
        path,
        needle,
        k=ratio,
        from_file=source_file,
        dry_run=dry,
        project_root=root,
        catalog=cat,
        game_root=game,
    )
    targets = [path]
    if cat not in (None, ""):
        targets.append(Path(str(cat)))
    targets = [item for item in targets if item.is_file()]

    def _stamp() -> dict[str, tuple[str, int]]:
        return {
            str(item): (reverse_delta.sha256_of(item), item.stat().st_mtime_ns) for item in targets
        }

    before = _stamp()
    result = anim_bridge.apply_via_cli(argv)
    after = _stamp()
    doc = dict(result.doc)
    human = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    mode = "真写" if not dry else "干跑"
    lines = [f"④ 写回（{mode}）：{path.name} · clip {needle!r} · rc={result.rc}"]
    lines.extend("    " + line for line in human[-14:])
    digest = f"{before[str(path)][0][:16]}… -> {after[str(path)][0][:16]}…"
    lines.append(f"    目标包 {path.name}：{digest}")
    lines.append(
        "    零写入核对（sha256 ＋ mtime 逐位）："
        + ("**未变** ✅" if before == after else "**变了** ❌"),
    )
    for key in ("mode", "clip", "object_bytes", "changed_bytes", "patch", "patch_bytes"):
        if key in doc:
            lines.append(f"    {key} = {doc[key]}")
    _set_lines(lines)
    if not result.ok:
        raise AnimationActionError(
            f"写回失败（{path.name} · clip {needle!r}）：{result.error_line()}",
        )
    return list(lines)


# --------------------------------------------------------------------------- #
# 算子（★ idname 是**合同**：面板按它们接线，⛔ 改了就是换合同）
# --------------------------------------------------------------------------- #
#: 面板上那个拉伸系数（算子自己的属性；⛔ 不往 Scene 上加字段）。
K_PROP: dict[str, Any] = {
    "name": "拉伸系数",
    "default": 2.0,
    "min": 0.01,
    "max": 100.0,
    "description": "时间轴整段拉伸的倍数（等价 CLI 的 anim apply --k）",
}
_BUNDLE_PROP: dict[str, Any] = {
    "name": "单位包",
    "default": "",
    "description": "units 的 UnityFS 包（要写回的那一个）",
}
_CLIP_PROP: dict[str, Any] = {
    "name": "clip（名字或 pathID）",
    "default": "",
    "description": "写哪一条（同名多条必须用 pathID）",
}
_ROOT_PROP: dict[str, Any] = {
    "name": "工程目录",
    "default": "",
    "description": "补丁柜落 <工程目录>/patches；真写必填",
}


class BamodAnimImportActions(bpy.types.Operator):
    """① 拿件时把该件的 clip 一起建成 Action 并挂到骨架上。"""

    bl_idname = "bamod.anim_import_actions"
    bl_label = "建该件的动画"
    bl_description = "读这个件挂的 clip，逐条建成真 Action 并挂到解得开的那副骨架上"
    bl_options = {"REGISTER"}

    unit_key: bpy.props.StringProperty(
        name="件（地址键）",
        default="",
        description="例 RU_T72B（Units.ModelFileName）",
    )
    game_root: bpy.props.StringProperty(
        name="游戏根目录",
        default="",
        description="不给 = 自动探测",
    )
    offline: bpy.props.BoolProperty(name="走夹具（不开包）", default=False)

    def execute(self, context: Any) -> set[str]:
        key = str(self.unit_key or "").strip()
        if not key:
            self.report({"ERROR"}, "先填「件」（地址键，例 RU_T72B）")
            return {"CANCELLED"}
        try:
            lines = import_actions(
                str(self.game_root or "") or None,
                key,
                offline=bool(self.offline),
            )
        except Exception as exc:  # noqa: BLE001 —— 报给面板，⛔ 不许把 Blender 弄崩
            self.report({"ERROR"}, f"建动画失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        built = [line for line in lines if "：绿 ——" in line]
        if not built:
            self.report({"ERROR"}, lines[-1] if lines else "（一条都没建成）")
            return {"CANCELLED"}
        self.report({"INFO"}, lines[-1])
        return {"FINISHED"}


class BamodAnimPlay(bpy.types.Operator):
    """② 播一遍：设帧区间并按播放键（无头下只设区间，读数里明写）。"""

    bl_idname = "bamod.anim_play"
    bl_label = "播一遍"
    bl_description = "按当前 Action 的帧区间设 frame_start/frame_end，然后播一遍"
    bl_options = {"REGISTER"}

    def execute(self, context: Any) -> set[str]:
        try:
            lines = play_once(getattr(context, "scene", None))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"播一遍失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, lines[0])
        return {"FINISHED"}


class BamodAnimWritebackCheck(bpy.types.Operator):
    """只读核对：这条 clip 的形状 ＋ 能不能就地拉伸（一个字节都不写）。"""

    bl_idname = "bamod.anim_writeback_check"
    bl_label = "核对写回"
    bl_description = "只读：报这条 clip 的曲线数 / 词数 / 时长 / 字节数，并说它能不能就地拉伸"
    bl_options = {"REGISTER"}

    bundle: bpy.props.StringProperty(**_BUNDLE_PROP)
    clip: bpy.props.StringProperty(**_CLIP_PROP)

    def execute(self, context: Any) -> set[str]:
        if not (self.bundle and self.clip):
            self.report({"ERROR"}, "先填「单位包」与「clip」")
            return {"CANCELLED"}
        try:
            store = anim_bridge._cli_anim().ClipStore.from_bundle(self.bundle)
            record, fields = store.by_clip(str(self.clip))
            name = str(fields.get("m_Name") or "")
            can, why = anim_write.stretchable(fields)
            shape = anim_write.shape_of(fields, record.payload, name=name)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"核对失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        _set_lines(
            [
                f"核对写回：{shape.name!r}（pathID={record.path_id}）",
                f"    {shape.line()}",
                f"    改时长：{'可以就地拉伸' if can else '**不行**'} —— {why}",
                "    （只读：一个字节都没写；下一步点「写回干跑」看计划）",
            ]
        )
        self.report({"INFO"}, f"{shape.name} · 曲线 {shape.curves} 条")
        return {"FINISHED"}


class BamodAnimWritebackPlan(bpy.types.Operator):
    """干跑：出写回计划（产品的 dry_run）—— ⛔ 一个字节都不写。"""

    bl_idname = "bamod.anim_writeback_plan"
    bl_label = "写回干跑"
    bl_description = "按拉伸系数走一遍产品 anim apply 的干跑；不写盘"
    bl_options = {"REGISTER"}

    k: bpy.props.FloatProperty(**K_PROP)
    bundle: bpy.props.StringProperty(**_BUNDLE_PROP)
    clip: bpy.props.StringProperty(**_CLIP_PROP)

    def invoke(self, context: Any, event: Any) -> set[str]:
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: Any) -> set[str]:
        try:
            lines = apply_writeback(
                self.bundle,
                {"clip": self.clip, "k": float(self.k), "dry_run": True},
            )
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"干跑失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, lines[0])
        return {"FINISHED"}


class BamodAnimWriteback(bpy.types.Operator):
    """真写：系数输入 ＋ 确认 -> 反向 Δ 补丁柜 -> 写后读回。"""

    bl_idname = "bamod.anim_writeback"
    bl_label = "写回游戏"
    bl_description = "把这条 clip 改后的等长字节写回包里；先确认、走反向 Δ、写后读回"
    bl_options = {"REGISTER"}

    k: bpy.props.FloatProperty(**K_PROP)
    bundle: bpy.props.StringProperty(**_BUNDLE_PROP)
    clip: bpy.props.StringProperty(**_CLIP_PROP)
    project_root: bpy.props.StringProperty(**_ROOT_PROP)
    confirmed: bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context: Any, event: Any) -> set[str]:
        """不可逆的大动作：系数输入框那一次「确定」就是确认（AGENTS 弹窗规则）。"""
        self.confirmed = True
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: Any) -> set[str]:
        if not self.confirmed:
            self.report({"ERROR"}, "真写回要先在「写回游戏」那个框里点确定")
            return {"CANCELLED"}
        try:
            lines = apply_writeback(
                self.bundle,
                {
                    "clip": self.clip,
                    "k": float(self.k),
                    "dry_run": False,
                    "project_root": self.project_root,
                },
            )
        except Exception as exc:  # noqa: BLE001 —— 错误必须弹（AGENTS 弹窗规则）
            self.report({"ERROR"}, f"动画写回失败：{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, lines[0])
        return {"FINISHED"}


#: 本模块注册的算子（★ `__init__.py` 与面板都按这个元组接线）。
ANIM_OPERATORS: tuple[type, ...] = (
    BamodAnimImportActions,
    BamodAnimPlay,
    BamodAnimWritebackCheck,
    BamodAnimWritebackPlan,
    BamodAnimWriteback,
)
#: ★ 兼容 `__init__.py` 收集器认的那个名字（`extra.extend(getattr(mod, 'OPERATORS', ()))`）：
#: 把 `anim_entry` 加进那份模块表，这五颗就自动注册上了（⛔ 本模块不自己注册）。
OPERATORS = ANIM_OPERATORS
