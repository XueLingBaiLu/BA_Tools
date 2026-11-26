r"""给 IsilDump 的每个方法贴标签：实测(measured) / 推断(inferred_file) / 无依据。

只读：不写盘（--json 也只到 stdout）。

用法：

    python tools/isil_label.py --dump _work/isil/IsilDump/BrokenArrow/BrokenArrow/Client/Ecs/Spawn/UnitBuilder.txt
    python tools/isil_label.py --type BrokenArrow.Client.Ecs.Spawn.UnitBuilder --method InitView --body 20

判据（能红）：

    ① 方法必须落在它自己那个 .cs 上（UnitBuilder.* -> UnitBuilder*.cs）；
    ② 没有源行的方法要显式标 nosource / inferred_file，⛔ 不许假装 measured；
    ③ 同名重载（同一 Type$$Method 多行）必须标 ambiguous，不许随便挑一条。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ISIL_ROOT = Path('_work/isil/IsilDump')
METHOD_LINE = re.compile(r'^Method:\s*(.*)$')
SIG_RE = re.compile(r'^(?P<ret>[^(]+?)\s+(?P<name>[A-Za-z_][\w.]*|\.[A-Za-z_][\w.]*)\s*\((?P<params>.*)\)\s*$')


def split_top(text: str) -> list[str]:
    """按顶层逗号切参数（忽略 <> 与 () 里的逗号）。"""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in '<([':
            depth += 1
        elif ch in '>)]':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(''.join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if ''.join(cur).strip():
        out.append(''.join(cur).strip())
    return out


def parse_dump(path: Path) -> tuple[str, list[dict]]:
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    type_name = ''
    for ln in lines[:4]:
        if ln.startswith('Type: '):
            type_name = ln[6:].strip()
            break
    blocks: list[dict] = []
    cur = None
    for i, ln in enumerate(lines, start=1):
        m = METHOD_LINE.match(ln)
        if m:
            if cur:
                cur['end'] = i - 1
                blocks.append(cur)
            sig = m.group(1).strip()
            sm = SIG_RE.match(sig)
            name = sm.group('name') if sm else sig.split('(')[0].split()[-1]
            params = split_top(sm.group('params')) if sm else []
            cur = {'start': i, 'end': len(lines), 'sig': sig, 'name': name,
                   'params': len(params), 'ret': sm.group('ret').strip() if sm else ''}
    if cur:
        blocks.append(cur)
    return type_name, blocks


def load_rows(index: Path, type_name: str) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    prefix = type_name + '$$'
    files = {}
    fpath = index / 'files.tsv'
    if fpath.is_file():
        for ln in fpath.read_text(encoding='utf-8').splitlines():
            fid, p = ln.split('\t', 1)
            files[int(fid)] = p
    for ln in (index / 'methods.full.tsv').read_text(encoding='utf-8').splitlines():
        parts = ln.split('\t')
        if len(parts) < 6 or not parts[5].startswith(prefix):
            continue
        addr, fid, line, delta, ev, name = (int(parts[0]), int(parts[1]), int(parts[2]),
                                            int(parts[3]), parts[4], parts[5])
        out.setdefault(name, []).append((addr, fid, line, delta, ev, files.get(fid, '')))
    return out


def label_for(rows: list[tuple]) -> tuple[str, str, str]:
    """-> (evidence, 位置文本, 说明)"""
    if not rows:
        return 'none', '-', '记录表里没有这个方法'
    if len(rows) > 1:
        return 'ambiguous', '-', '同名重载 %d 条：%s' % (
            len(rows), ' / '.join('%s:%d' % (r[5].rsplit('/', 1)[-1], r[2]) for r in rows[:4]))
    addr, fid, line, delta, ev, path = rows[0]
    short = path.rsplit('/', 1)[-1] if path else '?'
    if ev == 'measured':
        return 'measured', '%s:%d' % (short, line), 'RVA=%d' % addr + ('' if delta == 0 else '（+%d B）' % delta)
    if ev == 'inferred_file':
        return 'inferred_file', '%s（行号未补）' % short, 'RVA=%d · 同类型多数投票 ~80%%' % addr
    if ev == 'inferred_file_nocode':
        return 'inferred_file_nocode', '%s（无代码）' % short, '同类型投票'
    return ev, '<no-source>', 'RVA=%d' % addr


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='IsilDump 方法贴标签')
    ap.add_argument('--dump', default='')
    ap.add_argument('--type', default='')
    ap.add_argument('--index', default='_work/usym')
    ap.add_argument('--method', default='')
    ap.add_argument('--body', type=int, default=0)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)

    if args.dump:
        path = Path(args.dump)
    elif args.type:
        parts = args.type.split('.')
        path = (ISIL_ROOT / parts[0] / Path(*parts[1:])).with_suffix('.txt')
        if not path.is_file():
            tail = '/'.join(parts[1:]) + '.txt'
            hits = [p for p in ISIL_ROOT.rglob(parts[-1] + '.txt')
                    if p.as_posix().endswith(tail)]
            if not hits:
                hits = list(ISIL_ROOT.rglob(parts[-1] + '.txt'))
            path = hits[0] if hits else path
    else:
        print('要么 --dump <txt>，要么 --type <全限定类型名>', file=sys.stderr)
        return 2
    if not path.is_file():
        print('★ 找不到 dump：%s' % path, file=sys.stderr)
        return 2

    type_name, blocks = parse_dump(path)
    rows_by_name = load_rows(Path(args.index), type_name)
    short_type = type_name.split('.')[-1].split('<')[0]
    out = []
    for b in blocks:
        if args.method and args.method not in b['name']:
            continue
        rows = rows_by_name.get(type_name + '$$' + b['name'], [])
        ev, where, note = label_for(rows)
        # ★ 同名文件校验：实测记录落在**别的类型**的文件里 => 可疑（IL2CPP 合并了同体方法 / 地址撞了）
        if ev == 'measured' and short_type and short_type not in where:
            ev = 'suspect'
            note += ' · 文件不属于该类型（可能同体合并）'
        out.append({'method': b['name'], 'params': b['params'], 'lines': [b['start'], b['end']],
                    'evidence': ev, 'where': where, 'note': note, 'sig': b['sig']})

    if args.json:
        print(json.dumps({'type': type_name, 'dump': str(path), 'methods': out},
                         ensure_ascii=False, indent=2))
        return 0

    print('[isil] %s · %d 个方法块' % (path.name, len(blocks)))
    for m in out:
        print('  %-11s %-34s %-42s %s' % (m['evidence'], m['method'], m['where'], m['note']))

    if args.body and args.method:
        target = next((b for b in blocks if args.method in b['name']), None)
        if target is None:
            print('★ --body：没有匹配 %r 的方法块' % args.method, file=sys.stderr)
            return 2
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        print('')
        print('--- %s（dump 第 %d-%d 行）---' % (target['name'], target['start'], target['end']))
        for ln in lines[target['start'] - 1:target['start'] - 1 + args.body]:
            print('  ' + ln)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
