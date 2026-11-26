import json, glob, os
dd = r'D:\断箭模组制作\DataBaseCompiled'
cands = [d for d in glob.glob(dd + '\\*') if os.path.isdir(d)]
print('build dirs', cands[:5])
base = None
for c in [dd] + sorted(cands):
    if os.path.isfile(os.path.join(c, 'Units.json')): base = c; break
print('base', base)
units = json.load(open(os.path.join(base,'Units.json'), encoding='utf-8'))
rows = units if isinstance(units, list) else units.get('Rows')
u = [r for r in rows if r.get('ModelFileName')=='RU_T72B'][0]
print('unit', u.get('Id'), u.get('HUDName'), u.get('Name'))
turs = json.load(open(os.path.join(base,'Turrets.json'), encoding='utf-8'))
turs = turs if isinstance(turs, list) else turs.get('Rows')
tmap = {t['Id']: t for t in turs}
tu = json.load(open(os.path.join(base,'TurretUnits.json'), encoding='utf-8'))
tu = tu if isinstance(tu, list) else tu.get('Rows')
mine = sorted([r for r in tu if r.get('UnitId')==u.get('Id')], key=lambda r:(r.get('Order') or 0, r.get('Id') or 0))
for r in mine:
    t = tmap.get(r.get('TurretId')) or {}
    print('  TurretId=%-6s IsDefault=%-6s key=%-24s name=%s' % (r.get('TurretId'), t.get('IsDefault'), t.get('ModelFileName'), t.get('Name')))