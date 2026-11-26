
import json, pathlib, collections
PROJ = pathlib.Path(r"D:\断箭模组制作\BA_Mod_Maker_2.0\tools\probe_mod")
types = {t["table"]: t for t in json.loads((PROJ/"_work"/"il2cpp_types.json").read_text(encoding="utf-8"))["tables"]}
cols  = json.loads((PROJ/"_work"/"columns.json").read_text(encoding="utf-8"))["tables"]

def find(entry, name):
    for p in entry["ownProps"]:
        if p["name"] == name: return p, ("explicit-iface" if p.get("explicitIface") else "own-prop")
    for p in entry["ifaceProps"]:
        if p["name"] == name: return p, "iface:" + p.get("fromInterface", "?")
    for f in entry["fields"]:
        if f["name"] == name: return {"name": name, "type": f["type"]}, "field"
    return None, None

hist = collections.Counter(); prov = collections.Counter(); by_type = collections.defaultdict(list)
for table, keys in cols.items():
    for k in keys:
        p, how = find(types[table], k)
        ty = p["type"] if p else "<UNRESOLVED>"
        hist[ty] += 1; prov[how] += 1; by_type[ty].append(f"{table}.{k}")

SCALAR = {"System.Int32", "System.Single", "System.Boolean", "System.String"}
def is_enum(ty):   return ".Enums." in ty or ty.endswith(".Enum")
def is_nullable(ty): return ty.startswith("System.Nullable")

print("=== column type histogram (all", sum(hist.values()), "columns) ===")
for ty, n in hist.most_common(): print(f"{n:5d}  {ty}")
print()
print("=== resolution provenance ===")
for how, n in prov.most_common(): print(f"{n:5d}  {how}")
print()
scalars = sum(n for ty, n in hist.items() if ty in SCALAR)
enums   = sum(n for ty, n in hist.items() if is_enum(ty))
nulls   = sum(n for ty, n in hist.items() if is_nullable(ty))
refs    = {ty: v for ty, v in by_type.items() if ty not in SCALAR and not is_enum(ty) and not is_nullable(ty)}
print(f"scalar={scalars}  enum={enums}  nullable={nulls}  reference={sum(len(v) for v in refs.values())}")
print("REFERENCE COLUMNS (navigation, need the null/Id treatment):")
for ty, names in refs.items(): print(f"  {ty}: {names}")
assert sum(len(v) for v in refs.values()) == 1, "expected exactly one navigation column"
print("OK: exactly one navigation column, as documented in README.md")
