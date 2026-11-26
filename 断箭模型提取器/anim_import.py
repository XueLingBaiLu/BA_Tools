r"""真件 prefab -> props.json（**薄壳**：逻辑在 `ba_mod_maker.asset_kernel.anim_attrs`）。

为什么是薄壳：解析 Unity YAML + fileID→物体名 这件事只有一份实现（内核里），
提取器、CLI（`ba-mod-maker anim attrs-import`）、产品造包链用的是同一份。

用法：python anim_import.py --prefab "<真件.prefab>" --out props.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _kernel():
    """把 ba_mod_maker 弄进 sys.path（venv 里已装；Blender 里走 _ba/_kernel 的 junction）。"""
    try:
        from ba_mod_maker.asset_kernel import anim_attrs
        return anim_attrs
    except ImportError:
        #: 2026-11-04：仓改名成「断箭数据库编辑器」⇒ **按结构找**（谁有 src/ba_mod_maker 谁就是仓），
        #: 不再按 BA_Mod_Maker_2.0* 前缀找（改名后会一个都找不到，Blender 里 _kernel() 直接炸）。
        cands = []
        for base in (_HERE.parent, _HERE.parent.parent):
            if not base.is_dir():
                continue
            cands.append(base / "断箭数据库编辑器")
            cands.append(base / "BA_Mod_Maker_2.0")
            try:
                cands.extend(sorted(p for p in base.iterdir() if p.is_dir()))
            except OSError:
                pass
        for repo in cands:
            src = repo / "src"
            if (repo / "src" / "ba_mod_maker").is_dir() and src.is_dir() and str(src) not in sys.path:
                sys.path.insert(0, str(src))
        from ba_mod_maker.asset_kernel import anim_attrs
        return anim_attrs


def load_props(prefab_path) -> dict:
    """真件 prefab -> {"node": 件名, "props": [...]}（提取器在 Blender 里直接调它）。"""
    p = Path(prefab_path)
    return {"node": p.stem, "props": _kernel().props_from_prefab(p)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefab', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--root', default='')
    a = ap.parse_args()
    payload = load_props(a.prefab)
    props = payload['props']
    if not props:
        print('ANIMIMPORT 这个 prefab 上没有 AnimationHub')
        return 2
    Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    states = {}
    for rec in props:
        key = str(rec.get('key') or '')
        if key.endswith('__class'):
            parts = key.split('.')
            if len(parts) > 1:
                states[parts[1]] = states.get(parts[1], 0) + 1
    detail = ' '.join('%s=%d' % (k, v) for k, v in sorted(states.items()))
    print('ANIMIMPORT behaviours=%d props=%d  %s'
          % (sum(states.values()), len(props), detail))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())