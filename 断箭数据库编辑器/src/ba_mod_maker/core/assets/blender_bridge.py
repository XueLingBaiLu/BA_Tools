r"""Blender 桥：**只做一件事** —— 用 Blender 把工程目录里的 `model.blend` 导成 `model.glb`。

## 为什么需要（用户 2026-10-20 定）

导入器读的是 `model.glb`（零依赖的 `glb_import` 解析）。用户在 Blender 里改的是 `model.blend`，
改完**忘了导出**时，导入器会静默用旧 glb。本模块把这一步做成产品行为：
`model.blend` 比 `model.glb` 新 ⇒ **自动起 Blender 导一次**
（实测无界面启动 **0.7 s**、导 31 MB 的 glb **2.2 s**；Blender 5.2.1 LTS）。

## 三条口径

1. **找不到 Blender 就报红**，⛔ 绝不静默用旧 glb（那比报错更糟：用户以为改动进去了）；
2. **导出失败也报红**（Blender 退出码非 0 / glb 没生成 / 没变大）；
3. 覆盖写的是**工程目录里那个 `model.glb`**（与 `scene.json` / `textures/` 同级）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ba_mod_maker.core.errors import AssetError

__all__ = ["BlenderExport", "export_glb", "find_blender", "ensure_glb_fresh"]

#: 找不到 `BA_BLENDER` 时按这几个位置找（与提取器 `ba_extract.py::find_blender` 同一口径）。
DEFAULT_BLENDER_PATHS: tuple[str, ...] = (
    r"D:\Blender\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
)

_EXPORT_SCRIPT = (
    "import bpy, os\n"
    "out = os.environ['BA_EXPORT_GLB']\n"
    "bpy.ops.export_scene.gltf(filepath=out, export_format='GLB')\n"
    "print('BA_EXPORT_OK', os.path.getsize(out))\n"
)


class BlenderExport:
    """一次「blend -> glb」的结果（给调用方印读数用）。"""

    def __init__(self, *, exported: bool, reason: str, glb: Path, blender: Path | None = None,
                 size: int = 0, seconds: float = 0.0) -> None:
        self.exported = exported
        self.reason = reason
        self.glb = glb
        self.blender = blender
        self.size = size
        self.seconds = seconds

    def lines(self) -> tuple[str, ...]:
        if not self.exported:
            return (f"glb 字段：不动（{self.reason}）",)
        return (
            f"glb 字段：**自动导出**（blend 比 glb 新）· {self.size:,} B · {self.seconds:.1f} s",
            f"  用的 Blender：{self.blender}",
        )


def find_blender() -> Path | None:
    """找 `blender.exe`：先 `BA_BLENDER`，再默认几个位置。找不到返回 None。"""
    env = os.environ.get("BA_BLENDER")
    cands: list[Path] = []
    if env:
        cands.append(Path(env))
    cands.extend(Path(p) for p in DEFAULT_BLENDER_PATHS)
    for cand in cands:
        if cand.is_file():
            return cand
    return None


def export_glb(project_dir: str | Path, *, timeout: float = 900.0) -> BlenderExport:
    """把 `project_dir/model.blend` 导成 `project_dir/model.glb`（覆盖）。失败抛 AssetError。"""
    import time

    proj = Path(project_dir)
    blend = proj / "model.blend"
    glb = proj / "model.glb"
    if not blend.is_file():
        raise AssetError(f"没有 model.blend：{blend}")
    blender = find_blender()
    if blender is None:
        raise AssetError(
            "model.blend 比 model.glb 新，但找不到 Blender（无法自动导出）",
            hint="设 BA_BLENDER 指向 blender.exe；或先在 Blender 里"
                 "「文件 → 导出 → glTF 2.0 (.glb)」覆盖 model.glb 再跑一次",
        )
    script = proj / "_export_glb.py"
    script.write_text(_EXPORT_SCRIPT, encoding="utf-8")
    env = dict(os.environ)
    env["BA_EXPORT_GLB"] = str(glb)
    t0 = time.time()
    try:
        done = subprocess.run(
            [str(blender), "-b", str(blend), "--python-exit-code", "1", "--python", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssetError(f"Blender 导出超时（{timeout:.0f} s）") from exc
    except OSError as exc:
        raise AssetError(f"起不了 Blender：{exc}") from exc
    finally:
        script.unlink(missing_ok=True)
    seconds = time.time() - t0
    if done.returncode != 0:
        tail = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()[-3:]
        raise AssetError(
            f"Blender 导出失败（退出码 {done.returncode}）",
            hint=" | ".join(tail) or "看 Blender 输出",
        )
    if not glb.is_file() or glb.stat().st_size == 0:
        raise AssetError(f"Blender 说导出了，但 {glb} 不在或为空")
    return BlenderExport(exported=True, reason="blend 更新", glb=glb, blender=blender,
                         size=glb.stat().st_size, seconds=seconds)


def ensure_glb_fresh(project_dir: str | Path, *, auto: bool = True) -> BlenderExport:
    """`model.blend` 比 `model.glb` 新（或 glb 不存在）⇒ 导一次；否则什么都不做。

    `auto=False` 时**只报告不动手**（调用方自己决定要不要报红）。
    """
    proj = Path(project_dir)
    blend = proj / "model.blend"
    glb = proj / "model.glb"
    if not blend.is_file():
        return BlenderExport(exported=False, reason="没有 model.blend", glb=glb)
    if glb.is_file() and glb.stat().st_mtime >= blend.stat().st_mtime:
        return BlenderExport(exported=False, reason="glb 不比 blend 旧", glb=glb)
    if not auto:
        raise AssetError(
            "model.blend 比 model.glb 新（改动还没导出）",
            hint="重跑时加 --export-glb 自动导出，或先在 Blender 里导出 model.glb",
        )
    return export_glb(proj)
