"""真件材质的**数值**（`m_SavedProperties`）—— 打包时按它把工程里的 `.mat` 对齐。

## 为什么要这一步（2026-10-26 实测，A 级）

用户在游戏里看到「T-72B3 的 Kord 机枪比原版亮一档、更平、没金属感」。查下来是**导出侧的数值错**：

```
真件（prefab_graph.json，从游戏包里读出来的原样，materials 里 name="Kord"）：
  _MetallicRemapMax = 1.0
工程里的 .mat（AssetRipper 导出，Assets/Models/Weapon/Kord/Kord.mat）：
  _MetallicRemapMax = 0
```

HDRP 17.3.0 `LitDataIndividualLayer.hlsl:257-259`（被 `LitData.hlsl:143` include）：带 `_MASKMAP` 时
`metallic = lerp(_MetallicRemapMin, _MetallicRemapMax, maskMap.r)`，**`_Metallic` 不参与**
⇒ remap `0→0` 让 metallic 恒为 0（漆面）⇒ 整片 albedo 当漫反射打出去 ⇒ 亮一档、更平、没金属感。
把它改回 `1` 后真机复判：「不亮了！看起来完全一样了」。

同类缺陷不是一颗：全库 9,642 颗 `.mat` 与游戏侧普查（2,320 颗，从 units 包直读）
同名可比的 **2,015** 颗里，**178 颗**是「游戏 = 1.0 / 我们导出 = 0.0」。
⇒ 手工改一颗治不了，得让导入器**每次造包都对齐**。

## 做法

从真件的 `prefab_graph.json` 抠每个材质的 `m_SavedProperties`（`m_Floats` / `m_Colors`），
再按**真件 prefab 引到的材质 GUID** 找到工程里对应的 `.mat`（经 `.meta`），
把**两边都有的键**的数值写成真件的值。
⛔ 只改**值**：不新增键、不动贴图槽、不动关键字（关键字那条在 `material_keywords`）、不动 prefab；
⛔ 幂等：已经相等的一个字节都不写（跑第二遍读数必须是 0）。
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "MATERIAL_HEAD_RE", "align_materials", "read_graph_saved_properties", "values_of",
]

#: 材质块在 prefab_graph.json 里的形状（与 `material_keywords` 同一口径）。
MATERIAL_HEAD_RE = re.compile(
    r'"m_Name":\s*"(?P<name>[^"]+)",\s*'
    r'"m_Shader":\s*\{[^{}]*\},\s*'
    r'"m_ValidKeywords":\s*\[[^\]]*\],\s*'
    r'"m_InvalidKeywords":\s*\[[^\]]*\]'
)
#: `m_SavedProperties` 后面隔多远还找不到就放弃（防一个坏块把整份图拖死）。
_SAVED_GAP = 20_000
#: 一个 `m_SavedProperties` 块的大小上限（实测一千多颗里最大也就几十 KB）。
_BLOCK_MAX = 400_000

_SAVED_KEY = '"m_SavedProperties":'
_MAT_REF_RE = re.compile(r'\{fileID:\s*2100000,\s*guid:\s*([0-9a-f]{32})')
_META_GUID_RE = re.compile(r'guid:\s*([0-9a-f]{32})')
#: `.mat` 里材质自己的名字 —— `[ \t]+` 是必须的：AssetRipper 的 MonoBlock 也有一行空的 `m_Name: `。
_MAT_NAME_RE = re.compile(r'(?m)^  m_Name:[ \t]+(\S.*?)[ \t]*$')
_NUM = r'-?[0-9.eE+-]+'


def _pairs(raw: Any) -> dict[str, Any]:
    """UnityPy typetree 的 `[(key, value), ...]` -> dict；别的形状一律当空（⛔ 不猜）。"""
    out: dict[str, Any] = {}
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out[str(item[0])] = item[1]
    return out


def read_graph_saved_properties(graph_path: str | Path) -> dict[str, dict[str, Any]]:
    """`prefab_graph.json` -> `{材质名: {"floats": {...}, "colors": {...}}}`。

    读不到 / 解不动就返回空表（fail-soft：这一步不该拦住造包）。
    """
    path = Path(graph_path)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return {}
    blocks: dict[str, Any] = {}
    for head in MATERIAL_HEAD_RE.finditer(text):
        name = head.group('name').strip()
        if not name or name in blocks:
            continue
        start = text.find(_SAVED_KEY, head.end())
        if start < 0 or start - head.end() > _SAVED_GAP:
            continue
        brace = text.find('{', start)
        depth = 0
        for index in range(brace, min(len(text), brace + _BLOCK_MAX)):
            char = text[index]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    with contextlib.suppress(ValueError):
                        blocks[name] = json.loads(text[brace:index + 1])
                    break
    return {name: {"floats": _pairs(body.get('m_Floats')), "colors": _pairs(body.get('m_Colors'))}
            for name, body in blocks.items()}


def values_of(project_dir: str | Path) -> dict[str, dict[str, Any]]:
    """工程目录（提取器输出）-> 真件材质数值表；没有 graph 就返回空表。"""
    return read_graph_saved_properties(Path(project_dir) / 'asset' / 'prefab_graph.json')


def _float_line(key: str) -> re.Pattern[str]:
    return re.compile(r'(?m)^(\s*-\s*' + re.escape(key) + r':\s*)(' + _NUM + r')(\s*)$')


def _color_line(key: str) -> re.Pattern[str]:
    return re.compile(
        r'(?m)^(\s*-\s*' + re.escape(key) + r':\s*)'
        r'\{\s*r:\s*(' + _NUM + r'),\s*g:\s*(' + _NUM + r'),'
        r'\s*b:\s*(' + _NUM + r'),\s*a:\s*(' + _NUM + r')\s*\}')


def _fmt(value: float, old: float | None) -> str:
    """按旧值的写法给新值（旧的整数就别突然写成 `1.0`，免得 Unity 侧看着像换了类型）。"""
    number = float(value)
    if old is not None and float(old).is_integer() and number.is_integer():
        return str(int(round(number)))
    return repr(number)


def _align_text(text: str, floats: dict[str, Any], colors: dict[str, Any]) -> tuple[str, int]:
    """把 `.mat` 文本里「真件也声明了」的 float / color 写成真件的值；返回 (新文本, 改了几处)。"""
    changed = 0
    for key, want in floats.items():
        try:
            target = float(want)
        except (TypeError, ValueError):
            continue
        match = _float_line(key).search(text)
        if not match:
            continue
        current = float(match.group(2))
        if abs(current - target) <= 1e-6:
            continue
        head, tail = text[:match.start()], text[match.end():]
        text = head + match.group(1) + _fmt(target, current) + match.group(3) + tail
        changed += 1
    for key, want in colors.items():
        if not isinstance(want, dict) or any(want.get(channel) is None for channel in 'rgba'):
            continue
        match = _color_line(key).search(text)
        if not match:
            continue
        current = [float(match.group(i)) for i in range(2, 6)]
        target = [float(want[channel]) for channel in 'rgba']
        if all(abs(a - b) <= 1e-6 for a, b in zip(current, target, strict=True)):
            continue
        pairs = [_fmt(target[i], current[i]) for i in range(4)]
        body = f'{{r: {pairs[0]}, g: {pairs[1]}, b: {pairs[2]}, a: {pairs[3]}}}'
        text = text[:match.start()] + match.group(1) + body + text[match.end():]
        changed += 1
    return text, changed


def _mats_by_guid(project_root: Path) -> dict[str, Path]:
    """`Assets/**/*.mat.meta` 的 guid -> `.mat` 路径（AssetRipper 的 .meta 第 2 行就是 guid）。"""
    index: dict[str, Path] = {}
    for meta in project_root.glob('Assets/**/*.mat.meta'):
        try:
            head = meta.read_text(encoding='utf-8', errors='replace')[:400]
        except OSError:
            continue
        match = _META_GUID_RE.search(head)
        if match:
            index[match.group(1)] = meta.with_suffix('')
    return index


def align_materials(project_root: str | Path, prefab_file: str | Path,
                    values: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """把 `prefab_file` 引到的材质按真件数值对齐（幂等）。返回读数，不改别的东西。

    ⛔ 范围只有「真件 prefab 引到的材质」—— 同名但没被引到的 `.mat` 一颗都不碰
    （实测 `Mat D_2` 在工程里有 5 份同名副本，其中只有 1 份是这个 prefab 的）。
    """
    root = Path(project_root)
    prefab = Path(prefab_file)
    rows = dict(values or {})
    report: dict[str, Any] = {'prefab': prefab.name, 'materials': 0, 'matched': 0,
                              'fields': 0, 'files': []}
    if not rows or not prefab.is_file():
        return report
    try:
        prefab_text = prefab.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return report
    guids = sorted(set(_MAT_REF_RE.findall(prefab_text)))
    if not guids:
        return report
    index = _mats_by_guid(root)
    for guid in guids:
        mat = index.get(guid)
        if mat is None or not mat.is_file():
            continue
        report['materials'] += 1
        try:
            mat_text = mat.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        name_match = _MAT_NAME_RE.search(mat_text)
        if not name_match:
            continue
        want = rows.get(name_match.group(1).strip())
        if not want:
            continue
        report['matched'] += 1
        new_text, changed = _align_text(
            mat_text, want.get('floats') or {}, want.get('colors') or {})
        if not changed:
            continue
        mat.write_text(new_text, encoding='utf-8')
        report['fields'] += changed
        report['files'].append(mat.name)
    return report
