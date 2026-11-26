r"""断箭 · 动画参数 —— 存在 .blend 里，挂在**物体**的自定义属性上（打开 .blend 就能改）。

## 属性命名（通用，不限旋翼）

    ba.<状态>.<序号>.__class = "Torque"         ← 这条行为是哪个类
    ba.<状态>.<序号>.<字段>       = 值            ← 该类的字段，名字逐字同 C# 桩

    状态：universalState / demoState / gameState / preDeathState / deathState
    例：ba.gameState.0.__class = "Torque"
        ba.gameState.0._target  = "Rotorangle_0"   （字符串 = 物体名）
        ba.gameState.0._speed   = 2160.0
        ba.gameState.0._direction = (0, -1, 0)      （向量属性）

嵌套字段用点：`ba.universalState.0._x.Source`、`ba.universalState.0._x.MinimalAngle`。
数组 / 复杂结构：`ba.<状态>.<序号>.<字段>__json` = 一段 JSON 字符串。

## 三条命令（都不需要装插件，Blender 无窗口跑）

    blender -b "<工程>.blend" --python anim_props.py -- add Torque gameState --on Rotorangle_0
        # 按字段表给这个物体铺一条新行为（字段默认 0 / 空），你再改数字
    blender -b "<工程>.blend" --python anim_props.py -- list
        # 看这个工程里现在配了哪些行为
    blender -b "<工程>.blend" --python anim_props.py -- dump <out.json>
        # 读回属性 -> anim_config.json（给打包链用）

字段表来自 `anim_schema.json`（由 anim_schema.py 从 C# 桩生成，别手抄）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy

_HERE = Path(__file__).resolve().parent
_SCHEMA_PATH = _HERE / 'anim_schema.json'
_STATES = ('universalState', 'demoState', 'gameState', 'preDeathState', 'deathState')
_PREFIX = 'ba.'


def load_schema() -> dict:
    if not _SCHEMA_PATH.is_file():
        raise SystemExit(
            'ANIMPROPS 缺 %s —— 先跑：python anim_schema.py --src "<ExportedProject>\\Assets\\Plugins\\BrokenArrowStubs" --out anim_schema.json' % _SCHEMA_PATH)
    return json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))


def _default_value(t: dict):
    """按类型给一个空值（铺属性用）。"""
    k = t['kind']
    if k in ('float', 'int', 'enum'):
        return 0.0 if k == 'float' else 0
    if k == 'bool':
        return False
    if k == 'string':
        return ''
    if k == 'vec':
        return tuple([0.0] * t.get('n', 3))
    if k == 'ref':
        return ''
    return '{}' if k == 'nested' else '[]'


def _prop_name(t: dict) -> str:
    return '__json' if t['kind'] in ('array', 'nested', 'unknown') else ''


def _fields_of(schema: dict, name: str) -> list:
    """嵌套类型的字段：结构体在 structs 里，但桩里有的写成 class，两处都找。"""
    hit = (schema['structs'].get(name) or {}).get('fields')
    if hit:
        return hit
    return ((schema['behaviours'].get(name) or {}).get('fields')) or []


def _flatten(schema: dict, prefix: str, fields: list, out: list) -> None:
    """字段表 -> [(属性路径, 类型)]；nested 展开成点路径，array/unknown 走 __json。"""
    for f in fields:
        name = prefix + f['name']
        t = f['type']
        if t['kind'] == 'nested':
            _flatten(schema, name + '.', _fields_of(schema, t['name']), out)
        else:
            out.append((name + _prop_name(t), t))
    return out


def _object_for(name: str):
    ob = bpy.data.objects.get(name)
    return ob


def _scan(schema: dict) -> dict:
    """扫所有物体的 ba.* 属性 -> {(物体名, 状态, 序号): {路径: (值, 属性名)}}。"""
    found = {}
    for ob in bpy.data.objects:
        for key in ob.keys():
            if not key.startswith(_PREFIX):
                continue
            parts = key[len(_PREFIX):].split('.', 2)
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            state, idx, path = parts[0], int(parts[1]), parts[2]
            found.setdefault((ob.name, state, idx), {})[path] = key
    return found



def _coerce_struct(schema: dict, struct_name: str, value):
    """按 struct 字段表**纠正形状**：`array` 字段收到标量 ⇒ 包成 1 元素数组；`nested` 递归。

    2026-11-02 现场：`anim_config.json` 里 `_terrainTypeTriggers._keyData` 是标量 `5`，
    而 schema/桩类都是 `int[]` ⇒ Unity 建包时 `AnimatorConnect` 被整条跳过
    （报告原话：`_terrainTypeTriggers._keyData：期望数组，实得 数字 5`）。
    形状是 schema 的契约，这里按契约补齐，并把补了什么印出来（⛔ 不静默改数据）。"""
    if not isinstance(value, dict):
        return value
    fields = _fields_of(schema, struct_name) or []
    out = dict(value)
    for f in fields:
        name = f['name']
        if name not in out:
            continue
        t = f['type']
        kind = t['kind']
        v = out[name]
        if kind == 'array':
            if v is None:
                out[name] = []
            elif not isinstance(v, list):
                print('ANIMPROPS 形状纠正: %s.%s 标量 %r -> [%r]' % (struct_name, name, v, v))
                out[name] = [v]
        elif kind == 'nested':
            if isinstance(v, dict):
                out[name] = _coerce_struct(schema, t['name'], v)
            elif isinstance(v, list):
                out[name] = [_coerce_struct(schema, t['name'], x) for x in v]
    return out


def _convert(value, t: dict):
    k = t['kind']
    if k == 'float':
        return float(value)
    if k == 'int' or k == 'enum':
        return int(value)
    if k == 'bool':
        return bool(value)
    if k == 'string':
        return str(value)
    if k == 'vec':
        n = t.get('n', 3)
        vals = [float(v) for v in value] + [0.0] * n
        names = ('x', 'y', 'z') if n == 3 else (('x', 'y') if n == 2 else ('x', 'y', 'z', 'w'))
        return {nm: vals[i] for i, nm in enumerate(names)}
    if k == 'array':
        # ★ 2026-11-02：`array` 之前落到最底下那句 json.loads ⇒ 标量原样返回（`5` 就是 `5`），
        #   于是 `_keyData` 进包时被 Unity 判「期望数组，实得 数字」⇒ `AnimatorConnect` 整条被跳过。
        #   契约在 schema 里：**数组字段必须是数组**，标量包成 1 元素（并印出来，⛔ 不静默改）。
        try:
            v = json.loads(str(value)) if isinstance(value, str) else value
        except (TypeError, ValueError):
            v = value
        if v is None:
            return []
        if isinstance(v, list):
            return v
        print('ANIMPROPS 形状纠正(array): %r -> [%r]' % (v, v))
        return [v]
    if k == 'ref':
        s = str(value)
        return s or None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return None


def _field_type(schema: dict, cls: str, path: str):
    """按点路径在字段表里找类型（支持嵌套 struct）。"""
    t = None
    fields = ((schema['behaviours'].get(cls) or {}).get('fields') or [])
    for seg in path.split('.'):
        hit = None
        for f in fields:
            if f['name'] == seg:
                hit = f
                break
        if hit is None:
            return None
        t = hit['type']
        if t['kind'] == 'nested':
            fields = _fields_of(schema, t['name'])
        else:
            fields = []
    return t


def cmd_add(schema: dict, cls: str, state: str, on: str) -> int:
    if state not in _STATES:
        print('ANIMPROPS 未知状态 %r（可选：%s）' % (state, ' / '.join(_STATES)))
        return 2
    spec = schema['behaviours'].get(cls)
    if spec is None:
        print('ANIMPROPS 字段表里没有类 %r' % cls)
        return 2
    ob = _object_for(on)
    if ob is None:
        print('ANIMPROPS 工程里没有物体 %r' % on)
        return 2
    used = set()
    for key in ob.keys():
        parts = key[len(_PREFIX):].split('.', 2) if key.startswith(_PREFIX) else []
        if len(parts) >= 3 and parts[0] == state and parts[1].isdigit():
            used.add(int(parts[1]))
    idx = 0
    while idx in used:
        idx += 1
    base = '%s%s.%d.' % (_PREFIX, state, idx)
    ob[base + '__class'] = cls
    added = 0
    flat = _flatten(schema, '', (spec.get('fields') or []), [])
    for path, t in flat:
        ob[base + path] = _default_value(t)
        added += 1
    bpy.ops.wm.save_mainfile()
    print('ANIMPROPS add class=%s state=%s on=%s idx=%d props=%d' % (cls, state, on, idx, added + 1))
    return 0


def _resolve_object(name: str):
    """`Head#1` -> Blender 里第 2 个以 Head 开头的物体（同名物体靠后缀区分）。"""
    if '#' not in name:
        return bpy.data.objects.get(name)
    base, idx = name.rsplit('#', 1)
    try:
        i = int(idx)
    except ValueError:
        return bpy.data.objects.get(name)
    cands = sorted((o for o in bpy.data.objects
                    if o.name == base or o.name.startswith(base + '.')),
                   key=lambda o: o.name)
    return cands[i] if i < len(cands) else None


def apply_props(props: list) -> tuple[int, list]:
    """把 props 列表写进当前 .blend（先清旧 ba.*）；返回 (写入条数, 缺物体名单)。"""
    for ob in bpy.data.objects:
        for key in [k for k in ob.keys() if k.startswith(_PREFIX)]:
            del ob[key]
    missing = []
    applied = 0
    for rec in props:
        ob = _resolve_object(str(rec.get('object') or ''))
        if ob is None:
            missing.append(str(rec.get('object')))
            continue
        val = rec.get('value')
        if isinstance(val, list):
            val = tuple(float(v) for v in val)
        ob[str(rec['key'])] = val
        applied += 1
    return applied, missing


def cmd_apply(props_path: str) -> int:
    """把 anim_import.py 产出的 props.json 写进 .blend（先清掉旧的 ba.* 属性）。"""
    payload = json.loads(Path(props_path).read_text(encoding='utf-8'))
    applied, missing = apply_props(payload.get('props') or [])
    bpy.ops.wm.save_mainfile()
    for nm in sorted(set(missing)):
        print('ANIMPROPS 警告: 物体不存在 %r（这条没写进去）' % nm)
    print('ANIMPROPS apply props=%d missing=%d' % (applied, len(set(missing))))
    return 0 if applied and not missing else (3 if missing else 2)


def cmd_list() -> int:
    rows = []
    for (obj, state, idx), props in sorted(_scan({}).items()):
        cls = props.get('__class')
        rows.append((state, idx, obj, str(bpy.data.objects[obj][cls]) if cls else '?'))
    if not rows:
        print('ANIMPROPS list 空 —— 这个工程没有任何 ba.* 属性')
        return 0
    order = {s: i for i, s in enumerate(_STATES)}
    for state, idx, obj, cls in sorted(rows, key=lambda r: (order.get(r[0], 9), r[1])):
        print('  %-16s #%d  %-22s %s' % (state, idx, obj, cls))
    print('ANIMPROPS list behaviours=%d' % len(rows))
    return 0


def cmd_dump(schema: dict, out_path: str) -> int:
    groups = _scan(schema)
    order = {s: i for i, s in enumerate(_STATES)}
    beh = []
    problems = []
    for (obj, state, idx) in sorted(groups, key=lambda k: (order.get(k[1], 9), k[2], k[0])):
        props = groups[(obj, state, idx)]
        cls_key = props.get('__class')
        if cls_key is None:
            problems.append('%s/%s/%d 缺 __class' % (obj, state, idx))
            continue
        cls = str(bpy.data.objects[obj][cls_key])
        fields = {}
        for path, key in props.items():
            if path == '__class':
                continue
            val = bpy.data.objects[obj][key]
            base = path[:-len('__json')] if path.endswith('__json') else path
            t = _field_type(schema, cls, base)
            if t is None:
                t = {'kind': 'unknown'}
            try:
                conv = _convert(val, t)
                if t.get('kind') == 'nested':
                    conv = _coerce_struct(schema, t['name'], conv)
                # ★ 2026-11-02（实机崩溃现场）：`AnimatorConnect` 的七个触发器字段在 .blend 里是字面量
                #   `"None"`，而真件 prefab 里这些字段是**空串**。`InitTriggerDictionary` 会把它们
                #   逐个塞进字典 ⇒ 多个 `"None"` 撞键 ⇒ `ArgumentException: An item with the same key
                #   has already been added. Key: None` ⇒ 玩家看到的「没有模型、没有卡片」
                #   （游戏日志：`Error occured while spawning MODEL part of unit. ID=180 Name=ACV`）。
                #   ⇒ `*Trigger` 字段的字面量 `None` 按**空串**导出。
                if (t.get('kind') == 'string' and isinstance(conv, str)
                        and conv.strip().lower() == 'none'
                        and base.split('.')[-1].lower().endswith('trigger')):
                    print('ANIMPROPS 空串化: %s "None" -> ""' % base)
                    conv = ''
            except (TypeError, ValueError) as exc:
                problems.append('%s: %s=%r 转换失败（%s）' % (obj, path, val, exc))
                continue
            _assign(fields, base.split('.'), conv)
        beh.append({'class': cls, 'state': '_' + state, 'fields': fields})
    stem = Path(bpy.data.filepath).stem if bpy.data.filepath else 'unit'
    payload = {'node': stem, 'animationBehaviours': beh}
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    for bad in problems:
        print('ANIMPROPS 警告: ' + bad)
    print('ANIMPROPS dump behaviours=%d -> %s' % (len(beh), p))
    return 0 if beh and not problems else (3 if problems else 2)


def _assign(root: dict, path: list, value) -> None:
    cur = root
    for seg in path[:-1]:
        cur = cur.setdefault(seg, {})
    cur[path[-1]] = value


def main() -> int:
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if not argv:
        print(__doc__)
        return 1
    mode = argv[0]
    if mode == 'list':
        return cmd_list()
    if mode == 'apply':
        if len(argv) < 2:
            print('apply 要给 props.json 路径')
            return 1
        return cmd_apply(argv[1])
    schema = load_schema()
    if mode == 'add':
        if len(argv) < 4 or argv[3] != '--on':
            print('用法: add <类名> <状态> --on <物体名>')
            return 1
        return cmd_add(schema, argv[1], argv[2], argv[4] if len(argv) > 4 else '')
    if mode == 'dump':
        if len(argv) < 2:
            print('dump 要给输出路径')
            return 1
        return cmd_dump(schema, argv[1])
    print('未知模式: %r（可用：add / list / dump）' % mode)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())