"""动画配置那一跳的**唯一实现**：工程（.blend）<-> 属性 / 配置 / fbx-glb。

三个动作（都只是**起 Blender 无窗口**跑提取器目录里的脚本，逻辑在那边）：

| 动作 | 输入 | 输出 |
|---|---|---|
| `export_from_blend` | `model.blend` | `model.fbx` + `model.glb` + `anim_config.json` |
| `import_props` | 真件 prefab | `props.json`（属性清单） |
| `apply_props` | `model.blend` + `props.json` | 属性写进 .blend |

`auto_export_from_blend` 给造包链用：`.blend` 比 `model.fbx` 新（或 fbx/glb/config 缺）
就自动重导 —— 用户改完 .blend **不用再手工点**。⛔ 不许静默用旧 fbx：起不来就抛，让人看见。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

__all__ = ["AnimPackError", "apply_props", "auto_export_from_blend", "blender_exe",
           "export_from_blend", "import_props", "tool_dir"]


class AnimPackError(AssetError):
    """起不来 Blender / 脚本缺失 / 导出失败 —— 是环境或产物问题，不是判定。"""


def tool_dir() -> Path:
    """放 Blender 侧脚本的目录（``断箭模型提取器``）：仓库同级的兄弟目录。"""
    here = Path(__file__).resolve()
    for base in here.parents:
        cand = base / "断箭模型提取器"
        if (cand / "export_project.py").is_file():
            return cand
    raise AnimPackError("找不到断箭模型提取器目录（里面有 export_project.py）",
                        hint="它应与 BA_Mod_Maker_2.0 同级：D:\\断箭模组制作\\断箭模型提取器")


def blender_exe() -> Path:
    """Blender 可执行（``BA_BLENDER`` 可覆盖）。"""
    raw = os.environ.get("BA_BLENDER") or r"D:\Blender\blender.exe"
    exe = Path(raw)
    if not exe.is_file():
        raise AnimPackError("找不到 Blender：" + str(exe),
                            hint="设环境变量 BA_BLENDER 指向 blender.exe")
    return exe


def _run(argv: list[str], *, timeout: float = 900.0) -> str:
    exe = blender_exe()
    proc = subprocess.run([str(exe), *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    out = (proc.stdout or '') + (proc.stderr or '')
    if proc.returncode != 0:
        raise AnimPackError(f"Blender 退出码 {proc.returncode}",
                            hint=out[-1200:] or "无输出")
    return out


def export_from_blend(blend: str | Path, out_dir: str | Path | None = None) -> dict[str, Any]:
    """起 Blender：``.blend`` -> model.fbx + model.glb + anim_config.json。"""
    blend = Path(blend)
    if not blend.is_file():
        raise AnimPackError("没有这个 .blend：" + str(blend))
    out = Path(out_dir) if out_dir else blend.parent
    script = tool_dir() / 'export_project.py'
    log = _run(["-b", str(blend), "--python", str(script), "--", str(out)])
    fbx, glb = out / 'model.fbx', out / 'model.glb'
    cfg = out / 'anim_config.json'
    if not fbx.is_file() or not glb.is_file():
        raise AnimPackError("Blender 跑完但没有 model.fbx / model.glb", hint=log[-1200:])
    behaviours = 0
    if cfg.is_file():
        try:
            doc = json.loads(cfg.read_text(encoding='utf-8'))
            behaviours = len(doc.get('animationBehaviours') or [])
        except (OSError, ValueError):
            behaviours = 0
    return {'fbx': str(fbx), 'glb': str(glb),
            'anim_config': str(cfg) if cfg.is_file() else '',
            'behaviours': behaviours, 'log': log[-800:]}


def import_props(prefab: str | Path, out: str | Path) -> dict[str, Any]:
    """真件 prefab -> props.json（纯 Python，⛔ 不起 Blender）。"""
    from ba_mod_maker.asset_kernel import anim_attrs

    p = Path(prefab)
    skipped: list[str] = []
    props = anim_attrs.props_from_prefab(p, skipped)
    if not props:
        raise AnimPackError("这个 prefab 上没有 AnimationHub：" + str(p))
    Path(out).write_text(json.dumps({'node': p.stem, 'props': props},
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    states: dict[str, int] = {}
    for rec in props:
        key = str(rec.get('key') or '')
        parts = key.split('.')
        if len(parts) > 1 and key.endswith('__class'):
            states[parts[1]] = states.get(parts[1], 0) + 1
    return {'props': len(props), 'states': states, 'out': str(out),
            'skipped': sorted(set(skipped))}


def apply_props(blend: str | Path, props_json: str | Path) -> dict[str, Any]:
    """把 props.json 写进 .blend（起 Blender）。"""
    blend, props = Path(blend), Path(props_json)
    for p, what in ((blend, '.blend'), (props, 'props.json')):
        if not p.is_file():
            raise AnimPackError("没有这个 " + what + "：" + str(p))
    script = tool_dir() / 'anim_props.py'
    log = _run(["-b", str(blend), "--python", str(script), "--", "apply", str(props)])
    line = next((ln.strip() for ln in log.splitlines() if "ANIMPROPS apply" in ln), "")
    return {'log': log[-800:], 'readout': line}


def auto_export_from_blend(project_dir: str | Path) -> dict[str, Any] | None:
    """造包前用：``.blend`` 比 ``model.fbx`` 新（或 fbx/glb/config 缺）就自动重导。"""
    proj = Path(project_dir)
    blend = proj / 'model.blend'
    if not blend.is_file():
        return None
    fbx, glb = proj / 'model.fbx', proj / 'model.glb'
    reasons = []
    if not fbx.is_file():
        reasons.append("model.fbx 缺")
    elif blend.stat().st_mtime > fbx.stat().st_mtime:
        reasons.append(".blend 比 model.fbx 新")
    if not glb.is_file():
        reasons.append("model.glb 缺")
    if not reasons:
        return None
    info = export_from_blend(blend, proj)
    info['reason'] = '、'.join(reasons)
    return info