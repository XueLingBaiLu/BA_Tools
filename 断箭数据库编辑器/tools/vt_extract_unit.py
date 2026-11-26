"""把一个单位的 VT 法线 / 遮罩**从游戏原文件里**抽出来，落成插件认的文件名。

为什么要它：`blender_addon/materials.py::game_map_files` 按**文件名约定**找
`<单位键>_normal_decoded.png` / `<单位键>_mask_raw.png`，而 `_work/vt-extract/` 里
**只有手工抽过的那几个单位**。没抽过的单位，旧版会退到「唯一候选」把**别的单位的图**
接上去（2026-10-17 已改成「对不上就不接」）。本脚本把「抽」做成一条命令。

用法（要系统 Python 3.14 的 numpy + PIL；仓内 venv 没有 numpy）：
    C:\Python314\python.exe tools\vt_extract_unit.py "US_ACV"
    C:\Python314\python.exe tools\vt_extract_unit.py "US_AH-1W Super_Cobra"

判据一行：产物里出现 `<单位键>_normal_decoded.png` 与 `<单位键>_mask_raw.png`，且印出的
`R_is_constant_1 True`、`decoded_out_of_range < 0.03`（DXT5nm 法线的两条编码特征）。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

REPO = Path(__file__).resolve().parents[1]
VT = WORK / 'vt-extract'
EXE = (
    VT / 'src' / 'GraniteTextureReader' / 'bin' / 'Release' / 'net10.0' / 'GraniteTextureReader.exe'
)
MATERIALS = VT / 'units_materials.json'
SA = Path(r'D:\Steam\steamapps\common\broken_arrow\BrokenArrow_Data\StreamingAssets')


def stacks_for(unit_key: str):
    rows = json.loads(MATERIALS.read_text(encoding='utf-8'))
    token = unit_key.split(maxsplit=1)[0]
    hit = []
    for m in rows:
        name = str(m.get('name') or '')
        if name.split(maxsplit=1)[0] == token and m.get('stacks'):
            hit.append((name, m['stacks'][0][0], m['stacks'][0][1]))
    return hit


#: 抽取器落 TGA 的固定目录（⛔ 别用 %TEMP% —— 短路径 `XUELIN~1` 下它不落文件，实测）。
RAW = VT / 'raw'


def extract(group: str, item: str, layer: int, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(EXE), 'extract', '-t', str(SA / (group + '.gts')),
        '-f', item, '-l', str(layer), '-o', str(outdir),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f'抽取失败（layer {layer}）：{r.stdout[-500:]} {r.stderr[-500:]}')
    hits = sorted(outdir.glob(item + '*.tga'))
    hits = [p for p in hits if p.name.startswith(item + '_' + str(layer))]
    if not hits:
        raise SystemExit(f'没找到产物：{outdir} 里的 {item}_{layer}*.tga')
    return hits[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('unit_key')
    ap.add_argument('--out', default=str(VT))
    a = ap.parse_args()
    key = a.unit_key
    rows = stacks_for(key)
    if not rows:
        print('NO_STACKS', key)
        return 2
    print('MATCHED', rows)
    group, item = rows[0][1], rows[0][2]
    out = Path(a.out)
    raw_n = extract(group, item, 0, RAW)
    raw_m = extract(group, item, 1, RAW)
    n = np.asarray(Image.open(raw_n).convert('RGBA'), dtype=np.float64) / 255.0
    m = np.asarray(Image.open(raw_m).convert('RGBA'), dtype=np.float64) / 255.0
    x = n[..., 3] * 2 - 1
    y = n[..., 1] * 2 - 1
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, None))
    dec = np.stack([(x + 1.0) * 0.5, (y + 1.0) * 0.5, z], axis=-1)
    Image.open(raw_n).convert('RGBA').save(out / f'{key}_normal_raw.png')
    Image.open(raw_m).convert('RGBA').save(out / f'{key}_mask_raw.png')
    png = np.clip(dec * 255.0 + 0.5, 0, 255).astype(np.uint8)
    Image.fromarray(png, 'RGB').save(out / f'{key}_normal_decoded.png')
    r_channel = n[..., 0]
    print(json.dumps({
        'unit_key': key, 'group': group, 'item': item,
        'normal_size': [int(n.shape[1]), int(n.shape[0])],
        'mask_size': [int(m.shape[1]), int(m.shape[0])],
        'R_is_constant_1': bool(r_channel.min() == 1.0 and r_channel.max() == 1.0),
        'decoded_out_of_range': round(float((x * x + y * y > 1).mean()), 4),
        'decoded_mean_XYZ': [round(float(dec[..., i].mean()), 4) for i in range(3)],
        'mask_channel_mean': [round(float(m[..., i].mean()), 4) for i in range(4)],
    }, ensure_ascii=False))
    return 0


sys.exit(main())