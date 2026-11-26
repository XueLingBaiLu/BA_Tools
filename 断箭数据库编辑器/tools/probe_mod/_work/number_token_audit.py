
import json, pathlib, collections
PROJ = pathlib.Path(r"D:\断箭模组制作\BA_Mod_Maker_2.0\tools\probe_mod")
WS   = pathlib.Path(r"D:\断箭模组制作")
proxy = json.loads((PROJ/"_work"/"proxy_members.json").read_text(encoding="utf-8"))["tables"]
base = WS/"DataBaseCompiled"/"52085"

# model CLR type -> set of python value types actually present, per table
seen = collections.defaultdict(collections.Counter)
examples = collections.defaultdict(list)

for table, info in proxy.items():
    p = base/(table+".json")
    if not p.exists(): continue
    rows = json.loads(p.read_text(encoding="utf-8"))
    for col, rec in info["columns"].items():
        ty = rec["type"]
        c = collections.Counter()
        for r in rows:
            if col not in r: continue
            v = r[col]
            if v is None: c["NoneType"] += 1
            else:
                c[type(v).__name__] += 1
                if len(examples[(ty, type(v).__name__)]) < 4:
                    examples[(ty, type(v).__name__)].append(v)
        seen[ty].update(c)

print("=== model CLR type  ->  python value types seen in DataBaseCompiled/52085 ===")
for ty in sorted(seen):
    print(f"  {ty:45s} {dict(seen[ty])}")

print()
print("=== decisive: for model type System.Single, how are whole numbers written? ===")
for (ty, pyty), ex in sorted(examples.items()):
    if ty == "System.Single":
        print(f"   Single value tokenised as {pyty}: examples={ex}")

print()
print("=== raw text check: does Ammunitions.json contain '45' or '45.0' for Damage? ===")
import re
txt = (base/"Ammunitions.json").read_text(encoding="utf-8")
for pat in [r'"Damage":\s*[-\d.]+', r'"MuzzleVelocity":\s*[-\d.]+', r'"CanBeIntercepted":\s*\w+', r'"MinimalRange":\s*[-\d.]+']:
    hits = re.findall(pat, txt)[:6]
    print(f"   {pat:34s} -> {hits}")
