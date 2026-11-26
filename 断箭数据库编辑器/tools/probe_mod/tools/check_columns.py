r"""check_columns.py -- the ruler for task-4's dump contract (hard rule #1).

Question it answers mechanically:
    for every column key in DataBaseCompiled/<ver>/<Table>.json,
    is there a property on the game row type (own / explicit-interface-impl /
    interface / base class) that can supply it?

It also EMITS the single shared column manifest (columns.json) that both the
in-game probe (C#) and the offline comparator read, so the two sides can never
drift apart.

Inputs
    _work/il2cpp_types.json          produced by tools/dump_types.ps1
                                     (Mono.Cecil over the dummy dlls)
    <workspace>/DataBaseCompiled/<ver>/*.json   authoritative column names

Outputs
    _work/columns.json               { "version", "tables": { "<Table>": ["Id", ...] } }
    stdout                           one line per table + per unresolved key

Exit code 0 only when all 24 tables fully resolve.

    python.exe tools\probe_mod\tools\check_columns.py
    python.exe tools\probe_mod\tools\check_columns.py --selftest   # positive control: must go red
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PROJ = HERE.parent                       # .../BA_Mod_Maker_2.0/tools/probe_mod
REPO = PROJ.parents[1]                   # .../BA_Mod_Maker_2.0
WORKSPACE = REPO.parent                  # D:\<workspace>
TYPES_JSON = PROJ / "_work" / "il2cpp_types.json"
COLUMNS_JSON = PROJ / "_work" / "columns.json"
DBCOMPILED = WORKSPACE / "DataBaseCompiled"


def load_types(path: pathlib.Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {t["table"]: t for t in doc["tables"]}


def json_keys(ver_dir: pathlib.Path, table: str) -> list[str] | None:
    """Union of row keys, in first-seen order (== DataBaseCompiled column order)."""
    p = ver_dir / f"{table}.json"
    if not p.exists():
        return None
    rows = json.loads(p.read_text(encoding="utf-8"))
    keys: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            for k in row:
                if k not in keys:
                    keys.append(k)
    return keys


def resolve(entry: dict, name: str) -> str | None:
    """Mirror of the C# resolver in src/RowAccess.cs::FindProperty.

    Returns a human-readable provenance string, or None when unresolvable.
    """
    for p in entry.get("ownProps", []):
        if p["name"] == name:
            return "explicit-iface-impl" if p.get("explicitIface") else "own"
    for p in entry.get("ifaceProps", []):
        if p["name"] == name:
            return "interface:" + p.get("fromInterface", "?")
    for f in entry.get("fields", []):
        if f["name"] == name:
            return "field"
    return None


def run(types: dict, ver_dir: pathlib.Path, inject: tuple[str, str] | None = None):
    lines: list[str] = []
    columns: dict[str, list[str]] = {}
    bad = 0
    for table in sorted(types):
        keys = json_keys(ver_dir, table)
        if keys is None:
            lines.append(f"[MISSING-FILE] {table}: no {table}.json in {ver_dir}")
            bad += 1
            continue
        entry = types[table]
        if inject and inject[0] == table:
            keys = keys + [inject[1]]
        columns[table] = keys
        unresolved = [k for k in keys if resolve(entry, k) is None]
        if unresolved:
            bad += 1
            lines.append(f"[UNRESOLVED] {table:30s} keys={len(keys):3d} unresolved={unresolved}")
        else:
            lines.append(f"[OK]         {table:30s} keys={len(keys):3d}")
    return bad, lines, columns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default=str(TYPES_JSON))
    ap.add_argument("--dbcompiled", default=str(DBCOMPILED))
    ap.add_argument("--ver", default="52085")
    ap.add_argument("--columns-out", default=str(COLUMNS_JSON))
    ap.add_argument(
        "--selftest", action="store_true",
        help="positive control: inject a column that cannot resolve; the ruler MUST go red")
    args = ap.parse_args()

    types_path = pathlib.Path(args.types)
    if not types_path.exists():
        print(f"missing {types_path} -- run tools/dump_types.ps1 first", file=sys.stderr)
        return 2
    ver_dir = pathlib.Path(args.dbcompiled) / args.ver
    if not ver_dir.is_dir():
        print(f"no such dir: {ver_dir}", file=sys.stderr)
        return 2

    types = load_types(types_path)

    if args.selftest:
        probe_col = "NoSuchColumn_ZZZ"
        bad, lines, _ = run(types, ver_dir, inject=("Units", probe_col))
        for ln in lines:
            if ln.startswith("[UNRESOLVED]"):
                print(ln)
        print(f"positive-control: injected '{probe_col}' into Units"
              f" -> ruler red = {'YES' if bad else 'NO'}")
        return 0 if bad else 1

    bad, lines, columns = run(types, ver_dir)
    for ln in lines:
        print(ln)
    out = pathlib.Path(args.columns_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": f"DataBaseCompiled/{args.ver}",
        "version": args.ver,
        "table_count": len(columns),
        "tables": columns,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    total_cols = sum(len(v) for v in columns.values())
    print("-" * 72)
    print(f"tables={len(columns)}  columns={total_cols}  unresolved_tables={bad}")
    print(f"wrote {out}")
    print("RESULT:",
          "ALL COLUMNS RESOLVE" if bad == 0 else f"{bad} TABLE(S) HAVE UNRESOLVED COLUMNS")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
