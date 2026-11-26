
import json, pathlib, struct, math
WS = pathlib.Path(r"D:\断箭模组制作")
off = {r["Id"]: r for r in json.loads((WS/"DataBaseCompiled"/"52085"/"PlaneFlyPresets.json").read_text(encoding="utf-8"))}
G = pathlib.Path(r"D:\Steam\steamapps\common\broken_arrow\UserData\BaModProbe")
dump = json.loads(sorted((G/"dumps").glob("dump_*.json"))[-1].read_text(encoding="utf-8"))
on = {r["Id"]: r for r in dump["tables"]["PlaneFlyPresets"]}

def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]
def bits(x): return struct.unpack("<I", struct.pack("<f", x))[0]
def is_f32_exact(x):
    """True when the double x is exactly a float32 value (so it could have come from a float)."""
    return f32(x) == x

print("Is the OFFLINE value exactly representable as float32?")
for i in (3, 10):
    a = off[i]["CornerSpeedYaw"]; b = on[i]["CornerSpeedYaw"]
    print(f"  Id={i}: offline={a!r}  float32_exact={is_f32_exact(a)}   f32(offline)={f32(a)!r}")
    print(f"          online ={b!r}  float32_exact={is_f32_exact(b)}")
    print(f"          bits  offline={bits(a):#010x} online={bits(b):#010x}  ulp_gap={bits(b)-bits(a)}")

print()
print("Does float32(offline) == online ?  ->", f32(off[3]["CornerSpeedYaw"]) == on[3]["CornerSpeedYaw"],
      "/", f32(off[10]["CornerSpeedYaw"]) == on[10]["CornerSpeedYaw"])

print()
print("Whole column: how many offline values are NOT float32-exact?")
bad = [i for i in off if "CornerSpeedYaw" in off[i] and not is_f32_exact(off[i]["CornerSpeedYaw"])]
print("  ", len(bad), "of", len(off), "  ids:", bad[:12])

print()
print("Same test across EVERY column of every table (is offline JSON float32-exact wherever C# says Single)?")
proxy = json.loads((WS/"BA_Mod_Maker_2.0"/"tools"/"probe_mod"/"_work"/"proxy_members.json").read_text(encoding="utf-8"))["tables"]
single_cols = [(t, c) for t, info in proxy.items() for c, rec in info["columns"].items() if rec["type"] == "System.Single"]
non_exact = 0; total = 0; examples = []
for t, c in single_cols:
    rows = json.loads((WS/"DataBaseCompiled"/"52085"/(t+".json")).read_text(encoding="utf-8"))
    for r in rows:
        v = r.get(c)
        if isinstance(v, float):
            total += 1
            if not is_f32_exact(v):
                non_exact += 1
                if len(examples) < 8: examples.append(f"{t}.{c}={v!r}")
print(f"  Single-typed values: {total}   not float32-exact: {non_exact}")
for e in examples: print("    ", e)
