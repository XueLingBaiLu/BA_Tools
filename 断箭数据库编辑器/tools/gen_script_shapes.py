"""从**真件包**里抽「单位脚本的字段形状」基线 —— 出厂闸 A6 的对照物（生成器）。

判据一行：
  python tools/gen_script_shapes.py <真件包> [--out <目标 .py>] [--merge] [--exclude CLASS]
  -> 打印抽到的类数 / 每类节点数；不传 --out 只打印（dry-run）

为什么要有这条：
  2026-10-13 实测 —— 我们的桩把 UnitPrefabRoot.PrefabRoot/PrefabBody 写成 GameObject，
  真件是 **Transform**；出厂闸 A0~A5 **全绿**，因为那几条只查 PPtr「在不在包里」，
  不查「指向的对象是什么类型」。这条尺子补的正是那个盲区：
  比的是**类型树的每一个节点** (level, 类型, 字段名)，逐字相同才算过。

内存口径（用户要求「内存占用不能太高」）：
  · 只读 SerializedFile 的**元数据区**（类型表 + 类型树）＋ 逐对象的**小对象体**；
  · 不把整个包读成一个 bytes —— UfsReader 是按区间读的（3.37 GB 的 units 包也走得动）；
  · 每个 type entry 的类型树只解析一次（按 tid 缓存）。

⚠ 路径一律写**正斜杠**：本文件生成的产物是 .py 源码，而 Windows 风格的分隔符在 Python
  字符串里会被当成转义序列 —— 路径段 ba_mod_maker 前面那个反斜杠连同后面的 u 会被读成
  一个 Unicode 转义的开头，直接 SyntaxError（2026-10-13 连踩两次）。
"""
from __future__ import annotations

import argparse
import contextlib
import struct
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ba_mod_maker.asset_kernel import builder, clone, metadata_writer  # noqa: E402
from ba_mod_maker.core.asset import ufs  # noqa: E402

TARGET = "src/ba_mod_maker/asset_kernel/unit_script_shapes.py"


def harvest(path: Path) -> dict[str, tuple[tuple[int, str, str], ...]]:
    """-> {(assembly|namespace|class): ((level, type, name), ...)}"""
    cs = clone.unitypy_common_strings()
    out: dict[str, tuple[tuple[int, str, str], ...]] = {}
    with ufs.UfsReader(path) as r:
        node0 = r.nodes[0]
        info = ufs.read_serialized_file(r, node0)
        meta = metadata_writer.parse_serialized_metadata(
            r.read_range(node0.offset, info.data_offset))
        types = list(meta.types)
        cid = {i: t.class_id for i, t in enumerate(types)}
        pool = clone.ref_type_pool(meta, common_strings=cs)
        objs = {int(o[1]): o for o in info.objects}
        cache: dict[int, tuple] = {}

        def nodes_for(tid: int):
            if tid not in cache:
                tt = types[tid].type_tree
                blob = (struct.pack("<ii", tt.node_count, tt.string_buffer_size)
                        + tt.nodes + tt.strings)
                cache[tid] = clone.read_type_nodes(blob, common_strings=cs)
            return cache[tid]

        def read(pid: int) -> dict:
            o = objs[pid]
            return clone.read_object(
                bytes(r.read_range(node0.offset + info.data_offset + o[2], o[3])),
                nodes_for(o[4]), ref_types=pool)

        scripts: dict[int, tuple[str, str, str]] = {}
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_MONO_SCRIPT:
                continue
            d = read(int(o[1]))
            scripts[int(o[1])] = (str(d.get("m_AssemblyName") or ""),
                                  str(d.get("m_Namespace") or ""),
                                  str(d.get("m_ClassName") or ""))
        for o in info.objects:
            if cid.get(o[4]) != builder.CLASS_MONO_BEHAVIOUR:
                continue
            d = read(int(o[1]))
            s = d.get("m_Script") or {}
            if int(s.get("m_FileID", 0)) != 0:
                continue            # 跨包引用：本包抽不到形状（1.2.1 的真 units 包就是这个形态）
            key3 = scripts.get(int(s.get("m_PathID", 0)))
            if key3 is None:
                continue
            key = "|".join(key3)
            rows = tuple((n.level, n.type, n.name) for n in nodes_for(o[4]))
            if key in out and out[key] != rows:
                print(f"  ⚠ 同一个类 {key} 在本包里出现两种形状"
                      f"（{len(out[key])} vs {len(rows)} 节点）—— 取节点多的那份，但**请人核**",
                      file=sys.stderr)
                if len(rows) <= len(out[key]):
                    continue
            out[key] = rows
    return out


def _scripts_of_bundle(path: Path) -> dict[int, tuple[str, str, str]]:
    """脚本包（1.2.1 的 `*_monoscripts_*.bundle`）里的 pathID -> (asm, ns, class)。"""
    cs = clone.unitypy_common_strings()
    out: dict[int, tuple[str, str, str]] = {}
    with ufs.UfsReader(path) as r:
        node0 = r.nodes[0]
        info = ufs.read_serialized_file(r, node0)
        meta = metadata_writer.parse_serialized_metadata(
            r.read_range(node0.offset, info.data_offset))
        types = list(meta.types)
        pool = clone.ref_type_pool(meta, common_strings=cs)
        cache: dict[int, tuple] = {}

        def nodes_for(tid: int):
            if tid not in cache:
                tt = types[tid].type_tree
                blob = (struct.pack("<ii", tt.node_count, tt.string_buffer_size)
                        + tt.nodes + tt.strings)
                cache[tid] = clone.read_type_nodes(blob, common_strings=cs)
            return cache[tid]

        for o in info.objects:
            if types[o[4]].class_id != builder.CLASS_MONO_SCRIPT:
                continue
            raw = bytes(r.read_range(node0.offset + info.data_offset + o[2], o[3]))
            f = clone.read_object(raw, nodes_for(o[4]), ref_types=pool)
            out[int(o[1])] = (
                str(f.get("m_AssemblyName") or ""),
                str(f.get("m_Namespace") or ""),
                str(f.get("m_ClassName") or ""),
            )
    return out


def harvest_from_type_table(
    path: Path, scripts: dict[int, tuple[str, str, str]],
) -> dict[str, tuple[tuple[int, str, str], ...]]:
    """★ 1.2.1：MonoBehaviour 的 `m_Script` 是**跨包**的，从对象读会把它们全跳过。

    改从**类型表**取：每个 classID=114 的类型项有自己的 script_type_index；
    `m_ScriptTypes[si].local_identifier` 就是脚本包里那颗 MonoScript 的 pathID。
    形状 = 这个类型项的**类型树**（与对象无关，真件就是这么定义的）。
    """
    cs = clone.unitypy_common_strings()
    out: dict[str, tuple[tuple[int, str, str], ...]] = {}
    with ufs.UfsReader(path) as r:
        node0 = r.nodes[0]
        info = ufs.read_serialized_file(r, node0)
        meta = metadata_writer.parse_serialized_metadata(
            r.read_range(node0.offset, info.data_offset))
        types = list(meta.types)
        st = list(meta.script_types)
        for t in types:
            if int(t.class_id) != builder.CLASS_MONO_BEHAVIOUR:
                continue
            si = int(t.script_type_index)
            if si < 0 or si >= len(st):
                continue
            key3 = scripts.get(int(st[si].local_identifier))
            if key3 is None:
                continue
            tt = t.type_tree
            if tt is None:
                continue
            blob = (struct.pack("<ii", tt.node_count, tt.string_buffer_size)
                    + tt.nodes + tt.strings)
            nodes = clone.read_type_nodes(blob, common_strings=cs)
            rows = tuple((n.level, n.type, n.name) for n in nodes)
            key = "|".join(key3)
            if key in out and out[key] != rows:
                print(f"  ⚠ 同一个类 {key} 在类型表里有两种形状"
                      f"（{len(out[key])} vs {len(rows)} 节点）—— 取节点多的那份，但**请人核**",
                      file=sys.stderr)
                if len(rows) <= len(out[key]):
                    continue
            out[key] = rows
    return out

def render(shapes: dict, header: str, extra_cmd: str = "") -> str:
    lines = [
        '"""真件单位脚本的**字段形状基线** —— 出厂闸 A6 的对照物。',
        '',
        '⛔ 本文件是**生成物**，别手改。重取：',
        '',
        '    cd D:/断箭模组制作/BA_Mod_Maker_2.0',
        '    .venv/Scripts/python.exe tools/gen_script_shapes.py <真件包>' + extra_cmd,
        '        --out ' + TARGET,
        '',
        '形状 = 类型树的**逐节点** (level, 类型, 字段名)，与**值**无关。',
        '比它是因为：PPtr 的目标类型就写在类型树里 —— 桩写 GameObject、真件是 Transform 时，',
        'A0~A5 全绿也照样穿透（2026-10-13 现场）。',
        '',
        '只登记**我们自己的桩/组件**。第三方组件（HDRP/UI/TMP）不进基线：它们的字段跟着',
        '**包里那个版本**走，登记进来会假红（DecalProjector 在 HDRP 14→17 就多了 m_Version）。',
        '',
        f'来源：{header}',
        '"""',
        'from __future__ import annotations',
        '',
        '# (assembly, namespace, class) 用 "|" 连成键；值是类型树的逐节点三元组。',
        'UNIT_SCRIPT_SHAPES: dict[str, tuple[tuple[int, str, str], ...]] = {',
    ]
    for key in sorted(shapes):
        lines.append(f'    {key!r}: (')
        for lv, tp, nm in shapes[key]:
            lines.append(f'        ({lv}, {tp!r}, {nm!r}),')
        lines.append('    ),')
    lines.append('}')
    lines.append('')
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="从真件包抽单位脚本的字段形状基线")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="目标 .py；不传 = dry-run 只打印")
    ap.add_argument("--merge", action="store_true", help="与 --out 已有内容合并（旧键优先）")
    ap.add_argument("--only-existing", action="store_true",
                    help="只更新 --out 里已登记的类（不新增第三方组件）")
    ap.add_argument("--scripts-bundle", type=Path, default=None,
                    help=("1.2.1：脚本包（*_monoscripts_*.bundle）。给了就从类型表抽，"
                          "不靠对象上的 m_Script（1.2.1 是跨包的，对象路径抽不到）"))
    ap.add_argument("--exclude", action="append", default=[], metavar="CLASS",
                    help="按类名排除（可重复）：第三方组件用这个剥掉，否则随版本假红")
    args = ap.parse_args()
    if not args.bundle.is_file():
        print(f"错误：找不到 {args.bundle}", file=sys.stderr)
        return 2
    if args.merge and args.only_existing:
        print("错误：--merge 与 --only-existing 只能选一个", file=sys.stderr)
        return 2
    if args.scripts_bundle is not None and not args.scripts_bundle.is_file():
        print(f"错误：找不到脚本包 {args.scripts_bundle}", file=sys.stderr)
        return 2
    print(f"真件包 = {args.bundle}  {args.bundle.stat().st_size:,} B")
    extra_cmd = ""
    if args.scripts_bundle is not None:
        print(f"脚本包 = {args.scripts_bundle}  {args.scripts_bundle.stat().st_size:,} B")
        scripts = _scripts_of_bundle(args.scripts_bundle)
        print(f"脚本表 = {len(scripts)} 颗 MonoScript")
        shapes = harvest_from_type_table(args.bundle, scripts)
        extra_cmd += f" --scripts-bundle <{args.scripts_bundle.name}>"
    else:
        shapes = harvest(args.bundle)
    for drop in args.exclude:
        for k in [k for k in shapes if k.rsplit("|", 1)[-1] == drop]:
            del shapes[k]
            print(f"排除（--exclude {drop}）：{k}")
    if args.only_existing:
        if args.out is None or not args.out.is_file():
            print("错误：--only-existing 必须配一个已存在的 --out", file=sys.stderr)
            return 2
        ns: dict = {}
        exec(compile(args.out.read_text(encoding="utf-8"), str(args.out), "exec"), ns)
        old = ns.get("UNIT_SCRIPT_SHAPES") or {}
        kept: dict = {}
        for k, v in old.items():
            if k in shapes:
                kept[k] = shapes[k]
                if shapes[k] != v:
                    print(f"  更新 {k}: {len(v)} -> {len(shapes[k])} 节点")
            else:
                kept[k] = v
                print(f"  ⚠ 新包里没有 {k}，保留旧形状")
        shapes = kept
        extra_cmd += " --only-existing"
        print(f"只更新已登记：{len(shapes)} 个类")
    print(f"抽到 **{len(shapes)}** 个类的形状：")
    for k in sorted(shapes):
        print(f"  {k:<70} {len(shapes[k]):>3} 节点")
    if args.out is None:
        print("\n（dry-run：没传 --out，不落盘）")
        return 0
    if args.merge and args.out.is_file():
        ns: dict = {}
        exec(compile(args.out.read_text(encoding="utf-8"), str(args.out), "exec"), ns)
        old = ns.get("UNIT_SCRIPT_SHAPES") or {}
        merged = dict(old)
        for k, v in shapes.items():
            if k in old and old[k] != v:
                print(f"  ⚠ {k} 旧 {len(old[k])} 节点 / 新 {len(v)} —— **保留旧的**，差异要人核",
                      file=sys.stderr)
                continue
            merged[k] = v
        shapes = merged
        print(f"合并后 {len(shapes)} 个类")
    src = f"{args.bundle.name} ({args.bundle.stat().st_size:,} B)"
    if args.scripts_bundle is not None:
        src += f" + {args.scripts_bundle.name} ({args.scripts_bundle.stat().st_size:,} B)"
    args.out.write_text(render(shapes, src, extra_cmd), encoding="utf-8")
    print(f"已落盘 -> {args.out}")
    return 0


raise SystemExit(main())
