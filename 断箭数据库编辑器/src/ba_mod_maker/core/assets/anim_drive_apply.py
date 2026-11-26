r"""L13 · 把游戏驱动公式**应用到一份 .blend** 上（起 Blender 跑内嵌脚本）。

## 链路（与 `blender_bridge` 同一个姿势）

    anim_drive_sim.simulate()  ← 纯函数算每帧序列（轮角 / 履带 UV / 悬挂 / 车体摇摆）
        ↓ 写进内嵌 Blender 脚本
    Blender -b <in.blend> --python <tmp.py>  → <out.blend>

## 三条口径

1. **找不到 Blender 就报红**（⛔ 不静默跳过）；
2. **输出是另一个 .blend**（默认 `<in>_drive.blend`），⛔ 不覆盖用户的原件；
3. 脚本**只用 `anim_drive_sim`**（逻辑只一份），⛔ 不在字符串里重复公式。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ba_mod_maker.core.assets.blender_bridge import find_blender
from ba_mod_maker.core.errors import AssetError

__all__ = ["DriveRun", "apply_drive"]

_SCRIPT_HEAD = """
import bpy, sys, math
from pathlib import Path
ROOT = Path({repo!r})
sys.path.insert(0, str(ROOT / "src"))
from ba_mod_maker.asset_kernel.anim_drive_sim import DriveParams, simulate
SPEED, TURN, FRAMES, FPS, GAIN = {speed!r}, {turn!r}, {frames!r}, {fps!r}, {gain!r}
HIDE = {hide!r}
d = simulate(frames=FRAMES, fps=FPS, speed=SPEED, turn=TURN,
            params=DriveParams(visual_gain=GAIN))
print("SIM_WHEELS", round(d.wheel_left_deg[-1], 1), round(d.wheel_right_deg[-1], 1))
arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
arm = next((a for a in arms if any(b.name.startswith("LR") for b in a.data.bones)), None)
if arm is None:
    raise SystemExit("RROR 找不到带 LR* 骨的骨架")
names = [b.name for b in arm.data.bones]
lefts = [n for n in names if n.startswith("LR")]
rights = [n for n in names if n.startswith("RR")]
bpy.context.view_layer.objects.active = arm
arm.select_set(True)
bpy.ops.object.mode_set(mode="POSE")
def key(names_, angles, axis=0):
    for nm in names_:
        pb = arm.pose.bones.get(nm)
        if pb is None:
            continue
        pb.rotation_mode = "XYZ"
        for i, deg in enumerate(angles):
            eul = [0.0, 0.0, 0.0]
            eul[axis] = math.radians(deg)
            pb.rotation_euler = eul
            pb.keyframe_insert("rotation_euler", index=axis, frame=i + 1)
key(lefts, d.wheel_left_deg)
key(rights, d.wheel_right_deg)
body = arm.pose.bones.get("body")
if body is not None:
    body.rotation_mode = "XYZ"
    for i, deg in enumerate(d.body_deg):
        body.rotation_euler = (0.0, 0.0, math.radians(deg))
        body.keyframe_insert("rotation_euler", index=2, frame=i + 1)
for nm, seq in d.suspension_y.items():
    pb = arm.pose.bones.get(nm)
    if pb is None:
        continue
    for i, y in enumerate(seq):
        pb.location = (0.0, 0.0, y)
        pb.keyframe_insert("location", index=2, frame=i + 1)
bpy.ops.object.mode_set(mode="OBJECT")
tracked = 0
for obj in bpy.data.objects:
    if obj.type != "MESH" or "trak" not in obj.name.lower():
        continue
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        nt = mat.node_tree
        node = next((n for n in nt.nodes if n.type == "MAPPING"), None)
        if node is None:
            node = nt.nodes.new("ShaderNodeMapping")
            tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
            if tex is not None:
                nt.links.new(node.outputs["Vector"], tex.inputs["Vector"])
        for i, u in enumerate(d.track_u):
            node.inputs["Location"].default_value[0] = u
            node.inputs["Location"].keyframe_insert("default_value", index=0, frame=i + 1)
        tracked += 1
# 演示：把用户点名的网格藏起来（侧裙挡轮子时用），⛔ 不改几何、只改 hide_render
for _n in HIDE:
    _o = bpy.data.objects.get(_n)
    if _o is not None:
        _o.hide_render = True
        print("HIDDEN", _n)
sc = bpy.context.scene
sc.render.fps = int(round(FPS))   # ★ 场景 fps 要跟仿真一致，否则播出来快慢不对（默认 24）
sc.frame_start, sc.frame_end = 1, FRAMES
sc.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath={out!r})
print("DRIVE_OK", tracked, len(lefts), len(rights), FRAMES)
"""


class DriveRun:
    """一次 `anim drive` 的读数。"""

    def __init__(self, *, out: Path, wheels: int, tracked: int, frames: int,
                 seconds: float, gain: float = 1.0) -> None:
        self.out = out
        self.wheels = wheels
        self.tracked = tracked
        self.frames = frames
        self.seconds = seconds
        self.gain = gain

    def lines(self) -> tuple[str, ...]:
        return (
            f"驱动演示：{self.frames} 帧 · 轮骨 {self.wheels} 根",
            f"  履带材质 {self.tracked} 个 · 用时 {self.seconds:.1f} s",
            f"  放大系数 {self.gain:g}（1.0 = 游戏口径）",
            f"  产物：{self.out}",
        )


def apply_drive(
    blend: str | Path,
    *,
    speed: float = 0.25,
    turn: float = 0.35,
    frames: int = 60,
    fps: float = 60.0,
    hide: tuple[str, ...] = (),
    exaggerate: float = 1.0,
    out: str | Path | None = None,
    repo: str | Path | None = None,
    timeout: float = 900.0,
) -> DriveRun:
    """把驱动公式应用到 `blend`，写成 `out`（默认 `<blend 名>_drive.blend`）。"""
    import time

    src = Path(blend)
    if not src.is_file():
        raise AssetError(f"没有这份 .blend：{src}")
    target = Path(out) if out else src.with_name(src.stem + "_drive.blend")
    blender = find_blender()
    if blender is None:
        raise AssetError("找不到 Blender（设 BA_BLENDER）",
                         hint="先装 Blender，或设 BA_BLENDER 指向 blender.exe")
    # 仓库根：本文件在 `<repo>/src/ba_mod_maker/core/assets/anim_drive_apply.py`
    here = Path(repo) if repo else Path(__file__).resolve().parents[4]
    script = target.with_suffix(".drive.py")
    script.write_text(
        _SCRIPT_HEAD.format(repo=str(here), speed=float(speed), turn=float(turn),
                            frames=int(frames), fps=float(fps), out=str(target),
                            hide=tuple(hide), gain=float(exaggerate)),
        encoding="utf-8",
    )
    t0 = time.time()
    try:
        done = subprocess.run(
            [str(blender), "-b", str(src), "--python-exit-code", "1",
             "--python", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssetError(f"Blender 应用驱动超时（{timeout:.0f} s）") from exc
    except OSError as exc:
        raise AssetError(f"起不了 Blender：{exc}") from exc
    seconds = time.time() - t0
    out_text = (done.stdout or "") + (done.stderr or "")
    script.unlink(missing_ok=True)
    if done.returncode != 0 or "DRIVE_OK" not in out_text:
        tail = out_text.strip().splitlines()[-3:]
        raise AssetError(f"Blender 应用驱动失败（退出码 {done.returncode}）",
                         hint=" | ".join(tail) or "看 Blender 输出")
    if not target.is_file():
        raise AssetError(f"Blender 说做完了，但 {target} 不在")
    for line in out_text.splitlines():
        if line.startswith("DRIVE_OK"):
            parts = line.split()
            tracked = int(parts[1])
            wheels = int(parts[2]) + int(parts[3])
            return DriveRun(out=target, wheels=wheels, tracked=tracked,
                            frames=int(parts[4]), seconds=seconds,
                            gain=float(exaggerate))
    raise AssetError("Blender 输出里读不到 DRIVE_OK 读数")