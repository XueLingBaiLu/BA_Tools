"""真件 prefab 的 AnimationHub -> 「对象属性」列表（纯数据，⛔ 不 import bpy）。

链路上的位置：

    真件 prefab（Unity YAML + references 表）
        --props_from_prefab-->  [{object, key, value}, ...]
        --> .blend 里物体上的自定义属性（``ba.<状态>.<序号>.<字段>``）

属性命名（Blender 侧唯一口径）：``ba.<状态>.<序号>.__class`` / ``ba.<状态>.<序号>.<字段>``；
状态 = universalState / demoState / gameState / preDeathState / deathState；嵌套结构展开成点路径
（``_x.Source``）；数组或复杂结构走 ``<字段>__json``；同名节点用 ``#N``（真件里确有同名对象）。

⛔ 不用 PyYAML：Unity YAML 带 ``!u!`` 标签与 ``%TAG`` 指令，产品 venv 里也没有 yaml。
本模块只认：映射 / 列表（含与键同缩进的列表）/ 内联 ``{a: 1}`` / 标量。

出处：从 ``断箭模型提取器/anim_import.py`` 迁进来（那份现在只做薄壳，逻辑只此一份）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = ["PROPS_PREFIX", "STATES", "props_from_prefab", "read_prefab_behaviours"]

PROPS_PREFIX = 'ba.'
STATES = ('universalState', 'demoState', 'gameState', 'preDeathState', 'deathState')
_DOC = re.compile(r'^--- !u!(\d+) &(-?\d+)')


def _val(tok: str) -> Any:
    tok = tok.strip()
    if tok.startswith('{') and tok.endswith('}'):
        out = {}
        for part in tok[1:-1].split(','):
            if ':' in part:
                k, v = part.split(':', 1)
                out[k.strip()] = _val(v)
        return out
    if tok.startswith('[') and tok.endswith(']'):
        return [_val(p) for p in tok[1:-1].split(',') if p.strip()]
    if tok in ('', '~', 'null', 'None'):
        return None
    if tok in ('true', 'True'):
        return True
    if tok in ('false', 'False'):
        return False
    if len(tok) > 1 and tok[0] == tok[-1] and tok[0] in ('\'', '"'):
        return tok[1:-1]
    for cast in (int, float):
        try:
            return cast(tok)
        except ValueError:
            pass
    return tok


def _ind(ln: str) -> int:
    return len(ln) - len(ln.lstrip())


def _skip(lines: list[str], i: int) -> int:
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i


def _map(lines: list[str], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines):
        i = _skip(lines, i)
        if i >= len(lines):
            break
        ind, body = _ind(lines[i]), lines[i].strip()
        if ind < indent or body.startswith('- '):
            break
        if ind > indent or ':' not in body:
            i += 1
            continue
        key, rest = (s.strip() for s in body.split(':', 1))
        if rest:
            out[key] = _val(rest)
            i += 1
            continue
        j = _skip(lines, i + 1)
        same = (j < len(lines) and _ind(lines[j]) == ind
                and lines[j].strip().startswith('- '))
        if j < len(lines) and (_ind(lines[j]) > ind or same):
            pick = _lst if lines[j].strip().startswith('- ') else _map
            out[key], i = pick(lines, j, _ind(lines[j]))
        else:
            out[key], i = None, j
    return out, i


def _lst(lines: list[str], i: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while i < len(lines):
        i = _skip(lines, i)
        if i >= len(lines):
            break
        ind, body = _ind(lines[i]), lines[i].strip()
        if ind != indent or not body.startswith('- '):
            break
        item = body[2:].strip()
        if ':' not in item or item.startswith('{'):
            out.append(_val(item))
            i += 1
            continue
        key, rest = (s.strip() for s in item.split(':', 1))
        entry: dict[str, Any] = {}
        if rest:
            entry[key] = _val(rest)
            i += 1
        else:
            j = _skip(lines, i + 1)
            if j < len(lines) and _ind(lines[j]) > indent:
                pick = _lst if lines[j].strip().startswith('- ') else _map
                entry[key], i = pick(lines, j, _ind(lines[j]))
            else:
                entry[key], i = None, j
        cont = indent + 2
        while True:
            k = _skip(lines, i)
            if k >= len(lines):
                i = k
                break
            ind2 = _ind(lines[k])
            if ind2 < cont or lines[k].strip().startswith('- '):
                i = k
                break
            if ':' not in lines[k].strip():
                i = k + 1
                continue
            k2, r2 = (s.strip() for s in lines[k].strip().split(':', 1))
            if r2:
                entry[k2] = _val(r2)
                i = k + 1
                continue
            j2 = _skip(lines, k + 1)
            same2 = (j2 < len(lines) and _ind(lines[j2]) == ind2
                     and lines[j2].strip().startswith('- '))
            if j2 < len(lines) and (_ind(lines[j2]) > ind2 or same2):
                pick = _lst if lines[j2].strip().startswith('- ') else _map
                entry[k2], i = pick(lines, j2, _ind(lines[j2]))
            else:
                entry[k2], i = None, j2
        out.append(entry)
    return out, i


def _docs(text: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    cur = None
    for line in text.splitlines():
        m = _DOC.match(line)
        if m:
            if cur:
                docs.append(cur)
            cur = {'class': int(m.group(1)), 'id': int(m.group(2)), 'lines': []}
        elif cur is not None:
            cur['lines'].append(line)
    if cur:
        docs.append(cur)
    return docs


def _go_ref(d: dict[str, Any]) -> int:
    """组件 / GameObject 文档里的 ``m_GameObject: {fileID: N}`` → N（没有就 0）。"""
    for ln in d['lines']:
        s = ln.strip()
        if s.startswith('m_GameObject:'):
            m = re.search(r'fileID:\s*(-?\d+)', s)
            return int(m.group(1)) if m else 0
    return 0


def _names(docs: list[dict[str, Any]]) -> dict[int, str]:
    """fileID → 节点名。

    ① Transform（class 4）：它所在 GameObject 的名字，同名按先序加 ``#N``；
    ② 其它组件（Renderer / Animator / …）与 GameObject 本身：映射到**同一个**节点名。
       ⛔ 只映射 Transform 时，行为里指向**组件**的引用会退化成空串 ——
       实测 US_ACV 的 ``ProceduralDirt._renderers`` 指向 Renderer 137632746491512912，
       旧实现给出 ``[""]`` ⇒ AnimConfig 解析节点名失败 ⇒ 整条行为被跳过。
    """
    go_name: dict[int, str] = {}
    for d in docs:
        if d['class'] != 1:
            continue
        for ln in d['lines']:
            s = ln.strip()
            if s.startswith('m_Name:'):
                go_name[d['id']] = s.split(':', 1)[1].strip()
                break
    tr_go: dict[int, int] = {}
    for d in docs:
        if d['class'] != 4:
            continue
        g = _go_ref(d)
        if g:
            tr_go[d['id']] = g
    names: dict[int, str] = {}
    for tr, g in tr_go.items():
        names[tr] = go_name.get(g, '')
    dup: dict[str, list[int]] = {}
    for fid in sorted(names):
        if names[fid]:
            dup.setdefault(names[fid], []).append(fid)
    for nm, fids in dup.items():
        if len(fids) > 1:
            for i, fid in enumerate(fids):
                names[fid] = f'{nm}#{i}'
    tr_of_go: dict[int, int] = {g: tr for tr, g in tr_go.items()}
    # 组件引用（Renderer / Animator / …）⇒ 它所在节点名（Transform 那份已去重）
    for d in docs:
        if d['class'] in (1, 4):
            continue
        g = _go_ref(d)
        if not g:
            continue
        nm = names.get(tr_of_go.get(g, 0), '') or go_name.get(g, '')
        if nm:
            names[d['id']] = nm
    # GameObject 引用 ⇒ 同一个节点名
    for g, tr in tr_of_go.items():
        nm = names.get(tr)
        if nm:
            names[g] = nm
    return names


def _refs(lines: list[str]) -> dict[int, dict[str, Any]]:
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == 'RefIds:')
    except StopIteration:
        return {}
    starts = [(i, int(m.group(1))) for i in range(start + 1, len(lines))
              if (m := re.match(r'- rid:\s*(-?\d+)',
                                 lines[i].strip()))]
    out: dict[int, dict[str, Any]] = {}
    for k, (i, rid) in enumerate(starts):
        chunk = lines[i:starts[k + 1][0] if k + 1 < len(starts) else len(lines)]
        cls = ns = asm = ''
        data: dict[str, Any] = {}
        for j, ln in enumerate(chunk):
            t = ln.strip()
            if t.startswith('type:'):
                mm = re.search(r'class:\s*([^,}]+),\s*ns:\s*([^,}]+),\s*asm:\s*([^,}]+)', t)
                if mm:
                    cls, ns, asm = (g.strip() for g in mm.groups())
                continue
            if t.startswith('data:'):
                k2 = j + 1
                while k2 < len(chunk) and not chunk[k2].strip():
                    k2 += 1
                if k2 < len(chunk):
                    data, _ = _map(chunk, k2, _ind(chunk[k2]))
                break
        out[rid] = {'class': cls, 'ns': ns, 'asm': asm, 'data': data}
    return out


def _hub(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    lines = doc['lines']
    rids: dict[str, list[int]] = {}
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s.endswith(':'):
            continue
        state = s[:-1].lstrip('_')
        if state not in STATES:
            continue
        base, got, j = _ind(ln), [], i + 1
        while j < len(lines):
            t = lines[j].strip()
            if not t:
                j += 1
                continue
            if _ind(lines[j]) <= base and not t.startswith('- rid:'):
                break
            m = re.match(r'- rid:\s*(-?\d+)', t)
            if m:
                got.append(int(m.group(1)))
            j += 1
        rids[state] = got
    refs = _refs(lines)
    return {s: [refs[r] for r in got if r in refs] for s, got in rids.items()}


def _find_hub(docs):
    for d in docs:
        if any(ln.strip().startswith('_universalState:') for ln in d['lines']):
            return d
    return None


def read_prefab_behaviours(prefab_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """真件 prefab -> {状态: [{class, ns, asm, data}]}（没有 AnimationHub 就是空 dict）。"""
    docs = _docs(Path(prefab_path).read_text(encoding='utf-8', errors='replace'))
    hub = _find_hub(docs)
    return {} if hub is None else _hub(hub)


def _has_external(value: Any) -> bool:
    """带 guid 的引用 = 包外资产（VFX / 残骸 prefab）—— 属性里没法表达成节点名。"""
    if isinstance(value, dict):
        if 'fileID' in value and 'guid' in value:
            return True
        return any(_has_external(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_external(v) for v in value)
    return False


# `TerrainType`（dump.cs:1997771）里 **Water = 5**；真件 prefab 把这个字典序列化成
#   `_keyData: 05` + `_valueData: [{OnEnter: OnWater, OnExit: WaterExit}]`
# （44 个 prefab 是这个形状）。AssetRipper 对 US_ACV 只写了 `_keyData:`（空）——
# Unity 的 SerializableDictionary 是**按 key 重建**的，空 key ⇒ 整条地形触发**静默消失**
# （实机表现：ACV 下水挡浪板不展开）。名字能唯一判出地形时把键补回去，判不出就留痕不硬编。
_TERRAIN_BY_TRIGGER = {'onwater': 5, 'waterenter': 5, 'waterexit': 5}


def _repair_terrain_triggers(data: dict[str, Any], tag: str, skipped: list[str]) -> None:
    """地形触发字典「键丢、值在」时补键（见上面的常量注释）；补不出来就整条留空并留痕。"""
    t = data.get('_terrainTypeTriggers')
    if not isinstance(t, dict):
        return
    keys, vals = t.get('_keyData'), t.get('_valueData')
    if isinstance(keys, list) and keys:
        return
    if not isinstance(vals, list) or not vals:
        return
    fixed: list[int] = []
    for v in vals:
        name = ''
        if isinstance(v, dict):
            name = str(v.get('OnEnter') or v.get('OnExit') or '').strip().lower()
        k = _TERRAIN_BY_TRIGGER.get(name)
        if k is None:
            t['_keyData'] = []
            skipped.append(f'{tag}:_terrainTypeTriggers(键丢失且触发名 {name!r} 判不出地形)')
            return
        fixed.append(k)
    t['_keyData'] = fixed
    skipped.append(f'{tag}:_terrainTypeTriggers(键丢失→按触发名恢复 {fixed})')


def _to_plain(value: Any, names: dict[int, str]) -> Any:
    """把 ``{fileID: N}`` 换成节点名（数组/嵌套里也要换，否则 Unity 侧收不到字符串）。"""
    if isinstance(value, dict):
        if 'fileID' in value:
            fid = int(value['fileID'])
            return names.get(fid, '') if fid else None
        return {k: _to_plain(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v, names) for v in value]
    return value


def _flat(prefix: str, value: Any, names: dict[int, str], props: list, host: str) -> None:
    if isinstance(value, dict):
        if 'fileID' in value:
            fid = int(value['fileID'])
            name = names.get(fid, '') if fid else ''
            props.append({'object': host, 'key': prefix, 'value': name})
        elif value and set(value) <= {'x', 'y', 'z', 'w'}:
            ks = ('x', 'y') if ('z' not in value and 'y' in value) else ('x', 'y', 'z')
            vec = [float(value.get(k, 0)) for k in ks]
            props.append({'object': host, 'key': prefix, 'value': vec})
        else:
            for k, v in value.items():
                _flat(prefix + '.' + k, v, names, props, host)
        return
    if isinstance(value, list):
        blob = json.dumps(_to_plain(value, names), ensure_ascii=False)
        props.append({'object': host, 'key': prefix + '__json', 'value': blob})
        return
    props.append({'object': host, 'key': prefix, 'value': value})


def props_from_prefab(prefab_path: str | Path,
                      skipped: list[str] | None = None) -> list[dict[str, Any]]:
    """真件 prefab -> 属性列表（每项 ``{object, key, value}``）。"""
    p = Path(prefab_path)
    docs = _docs(p.read_text(encoding='utf-8', errors='replace'))
    names = _names(docs)
    hub = _find_hub(docs)
    if hub is None:
        return []
    props: list[dict[str, Any]] = []
    if skipped is None:
        skipped = []
    for state, behaviours in _hub(hub).items():
        if state not in STATES:
            continue
        for idx, beh in enumerate(behaviours):
            cls, data = beh['class'], beh['data'] or {}
            if not cls:
                # rid = -2 是 Unity 序列化的 **null 槽**（真件 733 个 hub 里只有 US_ACV._gameState
                # 第 4 格是这种）。⛔ 不许编成 'Unknown' 类：工程里没有这个类，打包时照样被跳过，
                # 却让 anim_config 看起来多了一条行为（第 8 轮就是这么把 Unknown 记进去的）。
                skipped.append(f'{state}[{idx}]:<null>')
                continue
            if _has_external(data):
                # ⛔ 含包外资产（VFX / 残骸 prefab）的行为没法用「节点名」表达 ——
                #    整条跳过（打包链不会重建它，真件那份也就不动）。
                skipped.append(cls)
                continue
            _repair_terrain_triggers(data, f'{state}[{idx}].{cls}', skipped)
            host = p.stem
            tgt = data.get('_target')
            if isinstance(tgt, dict) and tgt.get('fileID'):
                host = names.get(int(tgt['fileID']), host)
            elif cls == 'AxisRandom':
                for ax in ('_x', '_y', '_z'):
                    axd = data.get(ax)
                    src = axd.get('Source') if isinstance(axd, dict) else None
                    if isinstance(src, dict) and src.get('fileID'):
                        host = names.get(int(src['fileID']), host)
                        break
            base = f'{PROPS_PREFIX}{state}.{idx}.'
            props.append({'object': host, 'key': base + '__class', 'value': cls})
            _flat(base[:-1], data, names, props, host)
    return props