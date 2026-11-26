r"""完整资产导出：prefab 对象图 + 网格二进制 + 外部依赖清单 + pathID 对照。

由 ba_extract.py 在 Blender 那一步之后调用（用**仓库 venv** 跑，因为要 UnityPy）。
⛔ 不是一次性脚本：这是提取器 `extract` 的正式产物之一，落在 <out>\asset\。
"""

from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    bundle, key, out = argv[0], argv[1], argv[2]
    import UnityPy

    env = UnityPy.load(bundle)
    bf = env.files[bundle]
    sf_name, sf = next((n, f) for n, f in bf.files.items() if hasattr(f, "objects"))
    # 容器键是完整资产路径；用户给的是短键（Units.ModelFileName）→ 按后缀找
    ckey = key
    if ckey not in env.container:
        cands = [k for k in env.container if k.lower().endswith(("/" + key + ".prefab").lower())
                 or k.lower().endswith(("/" + key).lower()) or ("/" + key.lower() + "/") in k.lower()]
        if not cands:
            print("✗ 容器里找不到该资产：" + key, file=sys.stderr)
            return 2
        ckey = sorted(cands, key=len)[0]
    ref = env.container[ckey]
    pid = getattr(ref, "path_id", None) or int(ref)
    key = ckey
    exts = [getattr(e, "path", "") for e in (getattr(sf, "externals", None) or [])]

    adir = os.path.join(out, "asset")
    mdir = os.path.join(adir, "meshes")
    os.makedirs(mdir, exist_ok=True)

    seen: dict[int, dict] = {}
    queue = [pid]
    meshes: list[dict] = []

    def walk(tree, sink):
        if isinstance(tree, dict) and "m_PathID" in tree and "m_FileID" in tree:
            sink.append((tree.get("m_FileID", 0), tree.get("m_PathID", 0)))
        elif isinstance(tree, dict):
            for v in tree.values():
                walk(v, sink)
        elif isinstance(tree, list):
            for v in tree:
                walk(v, sink)

    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        o = sf.objects.get(cur)
        if o is None:
            continue
        tname = o.type.name
        if tname == "Mesh":
            vraw = bytes(o.get_raw_data() or b"")
            iraw = b""
            try:
                name = o.read().m_Name
            except Exception:
                name = "mesh"
            stem = "%s_%s" % (name.replace("/", "_").replace("\\", "_")[:40], cur)
            vb = os.path.join(mdir, stem + ".vert.bin")
            ib = os.path.join(mdir, stem + ".idx.bin")
            open(vb, "wb").write(vraw)
            open(ib, "wb").write(iraw)
            entry = {"path_id": cur, "name": name,
                     "vertex_bytes": len(vraw), "index_bytes": len(iraw),
                     "vert_file": os.path.relpath(vb, out), "idx_file": os.path.relpath(ib, out)}
            meshes.append(entry)
            seen[cur] = {"__type__": "Mesh", **{k: v for k, v in entry.items() if k != "path_id"}}
            continue
        try:
            tree = o.read_typetree()
        except Exception as exc:
            seen[cur] = {"__type__": tname, "__error__": f"{type(exc).__name__}: {exc}"}
            continue
        seen[cur] = {"__type__": tname, "typetree": tree}
        refs: list[tuple[int, int]] = []
        walk(tree, refs)
        for fid, pp in refs:
            if pp and fid == 0 and pp not in seen:
                queue.append(pp)

    graph = {
        "source_bundle": os.path.basename(bundle),
        "container_key": key,
        "root_path_id": pid,
        "serialized_file": sf_name,
        "externals": exts,
        "objects": [{"path_id": k, **v} for k, v in seen.items()],
        "meshes": meshes,
    }
    gp = os.path.join(adir, "prefab_graph.json")
    json.dump(graph, open(gp, "w", encoding="utf-8"), ensure_ascii=False, default=str)
    print("ASSET_OK " + json.dumps({"objects": len(seen), "meshes": len(meshes),
                                    "graph_mb": round(os.path.getsize(gp) / 1e6, 1),
                                    "externals": len(exts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
