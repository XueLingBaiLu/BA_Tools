
import json, pathlib, collections
WS = pathlib.Path(r"D:\断箭模组制作")
base = WS/"DataBaseCompiled"/"52085"
rows = json.loads((base/"Mobility.json").read_text(encoding="utf-8"))
vals = collections.Counter()
for r in rows:
    v = r.get("FlyPreset")
    vals[type(v).__name__ + ":" + repr(v)[:40]] += 1
print("Mobility.FlyPreset value histogram over", len(rows), "rows:", dict(vals))
print("Mobility keys:", list(rows[0].keys()))
tt = json.loads((base/"Turrets.json").read_text(encoding="utf-8"))
cv = collections.Counter(type(r.get("ParentTurretId")).__name__ + ":" + repr(r.get("ParentTurretId"))[:10] for r in tt)
print("Turrets.ParentTurretId histogram (top10):", dict(list(cv.items())[:10]))
