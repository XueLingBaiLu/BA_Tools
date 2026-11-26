
import json, pathlib
WS = pathlib.Path(r"D:\断箭模组制作")
manifest = json.loads(pathlib.Path(r"D:\断箭模组制作\BA_Mod_Maker_2.0\tools\probe_mod\_work\columns.json").read_text(encoding="utf-8"))
dep = json.loads(pathlib.Path(r"D:\Steam\steamapps\common\broken_arrow\UserData\BaModProbe\columns.json").read_text(encoding="utf-8"))
mine = set(manifest["tables"])
files = {p.stem for p in (WS/"DataBaseCompiled"/"52085").glob("*.json")} - {"_manifest"}
print("columns.json tables      :", len(mine))
print("DataBaseCompiled/52085   :", len(files))
print("only in columns.json     :", sorted(mine - files))
print("only in DataBaseCompiled :", sorted(files - mine))
print("SET EQUALITY             :", "IDENTICAL" if mine == files else "MISMATCH")
print("deployed manifest == build manifest :", dep["tables"] == manifest["tables"])
print("PlaneFlyPresets plural present      :", "PlaneFlyPresets" in mine)
print("singular FlyPresets present         :", "FlyPresets" in mine or "PlaneFlyPreset" in mine)
