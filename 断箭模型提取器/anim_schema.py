r"""从工程里的 C# 桩生成动画行为的**字段表**（anim_schema.json）。

为什么要有它：Blender 的自定义属性是无类型的，把属性还原成 prefab 上的
[SerializeReference] 对象时，必须知道每个字段是 float / Vector3 / Transform 引用 /
枚举 / 数组 / 嵌套结构 —— 那份知识只在 C# 桩里写着（字段名逐字抄自真件），所以从桩生成。

⚠ 坑（都真踩过）：
  1. 结构体/嵌套类**写在类之后**或**写在类里面** ⇒ 必须先收齐声明再判类型，
     并且按花括号深度把字段归给**最内层**那个类（否则父类字段被子类抢走）；
  2. `List<T>` 与 `T[]` 都是数组。

用法：python anim_schema.py --src "<ExportedProject>\\Assets\\Plugins\\BrokenArrowStubs" --out anim_schema.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

_DECL = re.compile(r'^\s*public\s+(?:sealed\s+|abstract\s+)?(class|struct|enum)\s+(\w+)')
_FIELD = re.compile(
    r'^\s*(?:\[SerializeField\]\s*)?(?:public|private|protected|internal)\s+'
    r'([\w\.<>\[\], ]+?)\s+(\w+)\s*(?:=[^;]*)?;')
_LIST = re.compile(r'^List<(.+)>$')

_VEC = {'Vector2': 2, 'Vector2Int': 2, 'Vector3': 3, 'Vector3Int': 3, 'Quaternion': 4}
_REF = {'Transform', 'GameObject', 'Material', 'Texture', 'Mesh', 'AudioClip', 'Animator'}
_SCALAR = {'float': 'float', 'double': 'float', 'int': 'int', 'uint': 'int',
           'short': 'int', 'bool': 'bool', 'string': 'string'}


def scan(src: pathlib.Path):
    """扫桩目录 -> ({名字: 声明}, 枚举名集合)；字段按花括号深度归属。"""
    decls = {}
    raws = {}
    for path in sorted(src.glob('*.cs')):
        depth = 0
        stack: list[tuple[str, int]] = []
        pending = None
        for i, ln in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            m = _DECL.match(ln)
            if m:
                pending = m.group(2)
                decls[pending] = {'kind': m.group(1), 'file': path.name, 'line': i}
                raws.setdefault(pending, [])
            fm = _FIELD.match(ln)
            if fm and stack:
                raws.setdefault(stack[-1][0], []).append((fm.group(1).strip(), fm.group(2)))
            for ch in ln:
                if ch == '{':
                    depth += 1
                    if pending:
                        stack.append((pending, depth))
                        pending = None
                elif ch == '}':
                    if stack and stack[-1][1] == depth:
                        stack.pop()
                    depth -= 1
    return decls, raws


def _classify(raw: str, decls: dict, enums: set) -> dict:
    t = ' '.join(raw.split())
    if t.endswith('[]'):
        return {'kind': 'array', 'of': _classify(t[:-2], decls, enums)}
    m = _LIST.match(t)
    if m:
        return {'kind': 'array', 'of': _classify(m.group(1), decls, enums)}
    if t in _SCALAR:
        return {'kind': _SCALAR[t]}
    if t in _VEC:
        return {'kind': 'vec', 'n': _VEC[t]}
    if t in _REF:
        return {'kind': 'ref', 'target': t}
    if t in enums:
        return {'kind': 'enum', 'name': t}
    if t in decls and decls[t]['kind'] in ('class', 'struct'):
        return {'kind': 'nested', 'name': t}
    return {'kind': 'unknown', 'raw': t}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    decls, raws = scan(pathlib.Path(a.src))
    enums = {k for k, v in decls.items() if v['kind'] == 'enum'}
    payload = {'behaviours': {}, 'structs': {}, 'enums': sorted(enums)}
    for name, d in sorted(decls.items()):
        if d['kind'] == 'enum':
            continue
        fields = [{'name': n, 'type': _classify(r, decls, enums)} for r, n in raws.get(name, [])]
        rec = {'file': d['file'], 'line': d['line'], 'fields': fields}
        key = 'behaviours' if d['kind'] == 'class' else 'structs'
        payload[key][name] = rec
    pathlib.Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    kinds = {}
    for b in payload['behaviours'].values():
        for f in b['fields']:
            kinds[f['type']['kind']] = kinds.get(f['type']['kind'], 0) + 1
    print('ANIMSCHEMA classes=%d structs=%d enums=%d fields=%d'
          % (len(payload['behaviours']), len(payload['structs']), len(enums), sum(kinds.values())))
    print('ANIMSCHEMA kinds=' + ', '.join('%s:%d' % (k, v) for k, v in sorted(kinds.items())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())