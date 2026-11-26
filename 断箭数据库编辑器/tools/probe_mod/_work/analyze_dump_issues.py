
import json, pathlib, struct
G = pathlib.Path(r"D:\Steam\steamapps\common\broken_arrow\UserData\BaModProbe")
dumps = sorted((G/"dumps").glob("dump_*.json"))
big = [d for d in dumps if d.stat().st_size > 1_000_000]
print("dumps:", [(d.name, d.stat().st_size) for d in dumps])
dump = json.loads(big[-1].read_text(encoding="utf-8"))
print("newest big dump:", big[-1].name, " hook=", dump["probe"]["hook"], " row_source=", dump["probe"].get("row_source"))
off = json.loads(pathlib.Path(r"D:\断箭模组制作\DataBaseCompiled\52085\Turrets.json").read_text(encoding="utf-8"))

online = {r["Id"]: r for r in dump["tables"]["Turrets"]}
print()
print("=== Turrets.ParentTurretId: key present? value? ===")
import collections
c = collections.Counter()
for i, r in online.items():
    if "ParentTurretId" not in r: c["KEY OMITTED"] += 1
    else: c["key=" + repr(r["ParentTurretId"])] += 1
print("  dump:", dict(c))
oc = collections.Counter(repr(r.get("ParentTurretId")) for r in off)
print("  offline:", dict(list(oc.items())[:6]))
print("  warnings about ParentTurretId:", sum(1 for w in dump["probe"]["warnings"] if "ParentTurretId" in w))
print("  total warnings:", len(dump["probe"]["warnings"]))

print()
print("=== PlaneFlyPresets.CornerSpeedYaw: float32 bits ===")
offp = {r["Id"]: r for r in json.loads(pathlib.Path(r"D:\断箭模组制作\DataBaseCompiled\52085\PlaneFlyPresets.json").read_text(encoding="utf-8"))}
onp = {r["Id"]: r for r in dump["tables"]["PlaneFlyPresets"]}
diff = 0
for i in sorted(offp):
    a, b = offp[i].get("CornerSpeedYaw"), onp.get(i, {}).get("CornerSpeedYaw")
    if a is None or b is None: continue
    ba, bb = struct.pack("<f", a), struct.pack("<f", b)
    if ba != bb:
        diff += 1
        fa = struct.unpack("<f", ba)[0]; fb = struct.unpack("<f", bb)[0]
        if diff <= 6:
            print(f"  Id={i:4d} offline={a!r:20s} online_float={b!r:20s} bits_differ rel={(fb-fa)/fa:+.3e}")
print("  total differing cells:", diff)
