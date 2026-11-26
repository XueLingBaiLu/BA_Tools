r"""usym 索引器：Unity il2cpp.usym -> 「方法 RVA -> 源文件:行」。

格式（Unity usym v2；本机 1.2.1 实测，非文档抄写）：

    Header  24 B : magic "sym-"(4) | version=2(4) | record_count(4) | id(4) | name(4) | os(4)
    Record  24 B : address(u64) | f1(u32) | file_off(u32) | line(u32) | parent(u32)
    Strings      : 起点 = 24 + record_count*24；NUL 结尾；偏移相对该起点

字段语义（正控定出来）：

    * file_off -> 绝对路径（C:/bender_slots/.../Assets/Scripts/....cs），'<no-source>' 表示无源；
    * line     -> 该记录对应的源行号；
    * parent   -> 0xFFFFFFFF = 非内联；否则是父记录下标（内联点）。

用法：

    python tools/usym_index.py <il2cpp.usym> --script <script.json> --out _work/usym
    python tools/usym_index.py <il2cpp.usym> --script <script.json> --probe 'UnitBuilder$$Init'

判据（能红）：

    ① --probe 打印的方法必须落在它自己那个 .cs 上，行号 > 0 且同文件内随方法顺序递增；
    ② --probe 一个不存在的名字必须 exit 2；
    ③ --out 的 methods.tsv 行数 == script.json 的方法数。
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path

MAGIC = b'sym-'
HEADER_SIZE = 24
RECORD_SIZE = 24
NO_PARENT = 0xFFFFFFFF

METHOD_RE = re.compile(rb'"Address": ([0-9]+),\s*"Name": "([^"]+)"')


class UsymError(RuntimeError):
    """结构不符合已知口径。"""


def parse_header(buf: bytes) -> dict:
    if buf[:4] != MAGIC:
        raise UsymError('magic 不是 %r：%r' % (MAGIC, buf[:4]))
    version, count, ident, name, os_name = struct.unpack_from('<5I', buf, 4)
    if version != 2:
        raise UsymError('版本不是 2：%d' % version)
    start = HEADER_SIZE + count * RECORD_SIZE
    if start > len(buf):
        raise UsymError('字符串区起点 %d 超出文件 %d' % (start, len(buf)))
    return {'version': version, 'count': count, 'id': ident, 'name': name,
            'os': os_name, 'strings_start': start, 'size': len(buf)}


def read_cstr(buf: bytes, start: int, off: int) -> str | None:
    if off < 0 or start + off >= len(buf):
        return None
    p = start + off
    e = buf.find(b'\x00', p)
    if e < 0:
        e = len(buf)
    return buf[p:e].decode('utf-8', 'replace')


def load_methods(path: Path) -> list[tuple[int, str]]:
    raw = path.read_bytes()
    return [(int(m.group(1)), m.group(2).decode('utf-8', 'replace'))
            for m in METHOD_RE.finditer(raw)]


def build_index(buf: bytes, head: dict, want: set[int]) -> dict[int, tuple[str | None, int, int]]:
    start = head['strings_start']
    out: dict[int, tuple[str | None, int, int]] = {}
    fmt = struct.Struct('<QIIII')
    for i in range(head['count']):
        address, f1, f2, f3, f4 = fmt.unpack_from(buf, HEADER_SIZE + i * RECORD_SIZE)
        if address in want and address not in out:
            out[address] = (read_cstr(buf, start, f2), f3, f4)
        if len(out) == len(want):
            break
    return out


def selftest(usym: str, script: str) -> int:
    """正控 + 负控 + 单调性 + 覆盖率：这把尺子必须能红。"""
    buf = Path(usym).read_bytes()
    head = parse_header(buf)
    methods = load_methods(Path(script))
    want = {a for a, _ in methods}
    index = build_index(buf, head, want)
    passed = 0
    total = 0

    def check(label: str, ok: bool, detail: str = '') -> None:
        nonlocal passed, total
        total += 1
        passed += int(ok)
        print('  %s %s%s' % ('PASS' if ok else '[FAIL]', label, ('  ' + detail) if detail else ''))

    # ① 正控：方法必须落在**它自己那个 .cs** 上，且行号 > 0
    for needle, suffix in (('UnitBuilder$$InitView', 'UnitBuilder.Ecs.cs'),
                           ('UnitBuilder$$InitOptics', 'UnitBuilder.Model.cs')):
        hits = [(a, n) for a, n in methods if needle in n]
        rec = index.get(hits[0][0]) if hits else None
        path, line = (rec[0] or '', rec[1]) if rec else ('', 0)
        check('正控 %s -> %s' % (needle, suffix), path.endswith(suffix) and line > 0,
              '%s:%d' % (path.rsplit('/', 1)[-1], line))

    # ② 负控：不存在的方法必须返回 2
    rc = main([usym, '--script', script, '--probe', 'Zzz_NoSuchSymbol__'])
    check('负控（不存在的名字 -> exit 2）', rc == 2, 'rc=%d' % rc)

    # ③ 单调性：同一文件内，按 metadata 顺序，行号基本非降
    rows = [(index[a][0], index[a][1]) for a, _ in methods if a in index]
    pick = 'UnitBuilder.Ecs.cs'
    seq = [ln for path, ln in rows if (path or '').endswith(pick) and ln > 0]
    inv = sum(1 for i in range(1, len(seq)) if seq[i] < seq[i - 1])
    ratio = inv / max(1, len(seq))
    check('单调性 %s（逆序比 %.3f）' % (pick, ratio), len(seq) > 50 and ratio < 0.05,
          '%d 行' % len(seq))

    # ④ 覆盖率
    cover = len(index) / max(1, len(want))
    check('覆盖率 %.1f%%' % (100.0 * cover), cover > 0.70, '%d/%d' % (len(index), len(want)))

    print('  => %d/%d' % (passed, total))
    return 0 if passed == total else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='usym -> 方法 RVA -> 源文件:行')
    ap.add_argument('usym')
    ap.add_argument('--script', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--probe', default='')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--query', type=int, default=-1)
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest(args.usym, args.script)
    if args.query >= 0:
        return query(args.usym, args.out, args.query)
    if args.full:
        return build_full(args.usym, args.script, args.out)

    buf = Path(args.usym).read_bytes()
    head = parse_header(buf)
    start = head['strings_start']
    methods = load_methods(Path(args.script))
    print('[usym] %s' % args.usym)
    print('  版本 %d · 记录 %s · 字符串 %s B · 系统 %s/%s' % (
        head['version'], format(head['count'], ','), format(len(buf) - start, ','),
        read_cstr(buf, start, head['name']), read_cstr(buf, start, head['os'])))
    print('  script.json 方法 %s 个' % format(len(methods), ','))

    want = {a for a, _ in methods}
    index = build_index(buf, head, want)
    found = len(index)
    print('  命中 %s / %s（%.1f%%）· 未命中 %s（内联 / 剥除 / 无记录）' % (
        format(found, ','), format(len(want), ','), 100.0 * found / max(1, len(want)),
        format(len(want) - found, ',')))

    if args.probe:
        hits = [(a, n) for a, n in methods if args.probe in n]
        if not hits:
            print('★ --probe 一个都没命中：%r（负控：应当 exit 2）' % args.probe, file=sys.stderr)
            return 2
        print('  正控 %r 命中 %d 个：' % (args.probe, len(hits)))
        for addr, name in hits[:20]:
            rec = index.get(addr)
            if rec is None:
                print('    %-58s RVA=%-9d <无记录>' % (name, addr))
                continue
            path, line, parent = rec
            print('    %-58s RVA=%-9d %s:%d%s' % (
                name, addr, path, line, '' if parent == NO_PARENT else '  (内联点)'))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        files: dict[str, int] = {}
        rows: list[str] = []
        miss = 0
        for addr, name in methods:
            rec = index.get(addr)
            if rec is None:
                miss += 1
                rows.append('%d\t-1\t0\t%s' % (addr, name))
                continue
            path, line, _parent = rec
            path = path or ''
            fid = files.setdefault(path, len(files))
            rows.append('%d\t%d\t%d\t%s' % (addr, fid, line, name))
        (out / 'files.tsv').write_text(
            ''.join('%d\t%s\n' % (i, p) for p, i in sorted(files.items(), key=lambda kv: kv[1])),
            encoding='utf-8')
        (out / 'methods.tsv').write_text(''.join(r + '\n' for r in rows), encoding='utf-8')
        summary = {'usym': str(args.usym), 'records': head['count'],
                   'methods': len(methods), 'found': found, 'missing': miss,
                   'files': len(files)}
        (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                          encoding='utf-8')
        print('  落盘 %s：files.tsv %d · methods.tsv %d 行（无记录 %d）' % (
            out, len(files), len(rows), miss))
    return 0


def build_full(usym: str, script: str, out_dir: str) -> int:
    """全量：落 records.bin（按地址排序）+ methods.full.tsv（入口 + 区间匹配）+ 内联账本。"""
    import array
    import bisect

    buf = Path(usym).read_bytes()
    head = parse_header(buf)
    n = head['count']
    start = head['strings_start']
    fmt = struct.Struct('<QIIII')

    addrs = array.array('Q')
    f2s = array.array('I')
    lines = array.array('I')
    parents = array.array('I')
    for i in range(n):
        a, _f1, f2, f3, f4 = fmt.unpack_from(buf, HEADER_SIZE + i * RECORD_SIZE)
        addrs.append(a)
        f2s.append(f2)
        lines.append(f3)
        parents.append(f4)

    files: dict[str, int] = {}
    ids = array.array('I')
    for i in range(n):
        text = read_cstr(buf, start, f2s[i]) or ''
        ids.append(files.setdefault(text, len(files)))

    parent_addr = array.array('Q')
    inline = 0
    for i in range(n):
        p = parents[i]
        if p == NO_PARENT or p >= n:
            parent_addr.append(0xFFFFFFFF)
        else:
            parent_addr.append(addrs[p])
            inline += 1

    order = sorted(range(n), key=addrs.__getitem__)
    sorted_addrs = array.array('Q')
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = struct.Struct('<QIII')
    with open(out / 'records.bin', 'wb') as fh:
        for i in order:
            pa = parent_addr[i]
            fh.write(rec.pack(addrs[i], ids[i], lines[i], pa if pa != 0xFFFFFFFF else 0xFFFFFFFF))
            sorted_addrs.append(addrs[i])
    (out / 'files.tsv').write_text(
        ''.join('%d\t%s\n' % (i, p) for p, i in sorted(files.items(), key=lambda kv: kv[1])),
        encoding='utf-8')

    methods = load_methods(Path(script))
    no_src = {i for p, i in files.items() if p in ('<no-source>', '')}
    parsed = []
    type_files: dict[str, Counter] = {}
    exact = near = measured = nosource = inferred = 0
    for addr, name in methods:
        tname = name.split('$$')[0]
        k = bisect.bisect_left(sorted_addrs, addr)
        fid, line, delta, ev = -1, 0, -1, 'none'
        if k < n and (sorted_addrs[k] == addr or sorted_addrs[k] - addr <= 512):
            fid = ids[order[k]]
            line = lines[order[k]]
            delta = sorted_addrs[k] - addr
            if delta == 0:
                exact += 1
            else:
                near += 1
            if fid in no_src:
                ev = 'nosource'
                nosource += 1
            else:
                ev = 'measured'
                measured += 1
                type_files.setdefault(tname, Counter())[fid] += 1
        parsed.append([addr, tname, name, fid, line, delta, ev])

    # 推断：同类型多数文件（>=3 个实测样本才敢推），**只给文件、不给行号**
    for row in parsed:
        if row[6] not in ('none', 'nosource'):
            continue
        counter = type_files.get(row[1])
        if not counter:
            continue
        fid, cnt = counter.most_common(1)[0]
        if cnt < 3 or fid in no_src:
            continue
        row[3] = fid
        row[4] = 0
        row[5] = -1
        row[6] = 'inferred_file' if row[6] == 'nosource' else 'inferred_file_nocode'

    rows = ['%d\t%d\t%d\t%d\t%s\t%s' %
            (a, fid, line, delta, ev, name)
            for a, _tn, name, fid, line, delta, ev in parsed]
    (out / 'methods.full.tsv').write_text(''.join(r + '\n' for r in rows), encoding='utf-8')

    ev = Counter(r[6] for r in parsed)
    game = Counter(r[6] for r in parsed if r[1].startswith('BrokenArrow'))
    summary = {
        'records': n, 'files': len(files), 'methods': len(methods),
        'exact': exact, 'near': near,
        'evidence': dict(ev),
        'evidence_game_BrokenArrow': dict(game),
        'inline_records': inline,
        'records_bin_bytes': (out / 'records.bin').stat().st_size,
        'columns': 'addr\tfid\tline\tdelta\tevidence\tname',
    }
    (out / 'summary_full.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                           encoding='utf-8')
    print('  全量落盘 %s' % out)
    print('    records.bin %s B（%s 条，按地址排序）' % (
        format(summary['records_bin_bytes'], ','), format(n, ',')))
    print('    方法 %s：实测 %s（精确 %s / 区间 %s）· no-source %s · 推断(有代码) %s · 推断(无代码) %s · 无依据 %s' % (
        format(len(methods), ','), format(ev['measured'], ','), format(exact, ','),
        format(near, ','), format(ev['nosource'], ','),
        format(ev['inferred_file'], ','), format(ev['inferred_file_nocode'], ','),
        format(ev['none'], ',')))
    print('    游戏代码 BrokenArrow.*：' + ' · '.join(
        '%s %s' % (k, format(v, ',')) for k, v in sorted(game.items())))
    print('    内联点记录 %s 条（parent != -1）' % format(inline, ','))
    return 0


def query(usym: str, out_dir: str, rva: int) -> int:
    """按 RVA 查 records.bin（二分）：打印 file:line，以及它相对入口的偏移。"""
    import array
    import bisect

    rec = struct.Struct('<QIII')
    data = (Path(out_dir) / 'records.bin').read_bytes()
    n = len(data) // rec.size
    addrs = array.array('Q')
    idx = array.array('I')
    for i in range(n):
        a, fid, line, _pa = rec.unpack_from(data, i * rec.size)
        addrs.append(a)
        idx.append(i)
    k = bisect.bisect_left(addrs, rva)
    if k >= n:
        print('RVA %d 超出记录范围（最大 %d）' % (rva, addrs[-1]))
        return 1
    a, fid, line, pa = rec.unpack_from(data, idx[k] * rec.size)
    files = {}
    for ln in (Path(out_dir) / 'files.tsv').read_text(encoding='utf-8').splitlines():
        fid2, path = ln.split('\t', 1)
        files[int(fid2)] = path
    print('RVA %d -> %s:%d （最近记录 @%d，差 %d B）%s' % (
        rva, files.get(fid, '?'), line, a, a - rva,
        '  [内联点]' if pa != 0xFFFFFFFF else ''))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
