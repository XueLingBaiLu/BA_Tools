"""真件材质的**着色器关键字列表** —— 打包时按它重写工程里的 .mat。

## 为什么要这一步（2026-10-26 实测，A 级）

用户在游戏里看到「Kord 子炮塔的法线、遮罩图没显示」。查下来：

```
真件（prefab_graph.json，从游戏包里读出来的原样）：
  m_ValidKeywords:   [_DISABLE_SSR_TRANSPARENT, _MASKMAP, _NORMALMAP, _NORMALMAP_TANGENT_SPACE]
  m_InvalidKeywords: []
工程里的 .mat（AssetRipper 导出，Assets/Models/Weapon/Kord/Kord.mat）：
  m_ValidKeywords:   []
  m_InvalidKeywords: [_DISABLE_SSR_TRANSPARENT, _MASKMAP, _NORMALMAP, _NORMALMAP_TANGENT_SPACE]
```

Unity 的语义：`m_ValidKeywords` = 显式**打开**，`m_InvalidKeywords` = 显式**关掉**。
AssetRipper 把同一批关键字写进了**反的那一栏** ⇒ 打包后 `_NORMALMAP`/`_MASKMAP` 是关的
⇒ 法线图 / 遮罩图「挂上了但不起作用」，而基色照常显示 —— 与用户描述完全一致。

## 做法

从真件的 `prefab_graph.json` 逐条抠出 `(名字, valid, invalid)`，写进 job.json 的
`material_keywords`；C# 侧（`UnitBuild`）在建 prefab 之前把这些材质的关键字**照抄**一遍。
⛔ 只重写**真件里有的**材质；真件里没有的保持原样（并记一条读数）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "MATERIAL_KEYWORD_RE", "ensure_shader_keywords", "keywords_of", "read_graph_keywords",
]

#: 材质块在 prefab_graph.json 里的形状：名字之后紧跟着 shader / 两组关键字。
#: （这个顺序是 `asset_kernel` 的序列化器写出来的，实测 9642 个材质全是这个形状）
MATERIAL_KEYWORD_RE = re.compile(
    r'"m_Name":\s*"(?P<name>[^"]+)",\s*'
    r'"m_Shader":\s*\{[^{}]*\},\s*'
    r'"m_ValidKeywords":\s*(?P<valid>\[[^\]]*\]),\s*'
    r'"m_InvalidKeywords":\s*(?P<invalid>\[[^\]]*\])'
)

#: 分块读的块大小（graph 实测 169 MB，一次读进来会把内存抬到 GB 级）
_CHUNK = 4 * 1024 * 1024
#: 块与块之间留的重叠（一个材质块最长也就几百字节）
_OVERLAP = 4096


def _parse_list(raw: str) -> list[str]:
    """把 `["a","b"]` 解成 list[str]；解不动就当空表（⛔ 不猜）。"""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def read_graph_keywords(graph_path: str | Path) -> list[dict[str, Any]]:
    """`prefab_graph.json` -> `[{"name", "valid", "invalid"}]`（按名字去重，先到先得）。"""
    path = Path(graph_path)
    if not path.is_file():
        return []
    rows: dict[str, dict[str, Any]] = {}
    tail = ''
    with path.open('r', encoding='utf-8', errors='replace') as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf = tail + chunk
            for match in MATERIAL_KEYWORD_RE.finditer(buf):
                name = match.group('name').strip()
                if not name or name in rows:
                    continue
                rows[name] = {
                    'name': name,
                    'valid': _parse_list(match.group('valid')),
                    'invalid': _parse_list(match.group('invalid')),
                }
            tail = buf[-_OVERLAP:]
    # 只保留「两组不一样」的：一样说明工程那份本来就对，不用重写（读数会变短）
    return [row for row in rows.values()
            if sorted(row['valid']) != sorted(row['invalid']) and row['valid']]

def keywords_of(project_dir: str | Path) -> list[dict[str, Any]]:
    """工程目录（提取器输出）-> 材质关键字表；没有 graph 就返回空表（fail-soft）。"""
    proj = Path(project_dir)
    return read_graph_keywords(proj / 'asset' / 'prefab_graph.json')
#: 桩 shader 里补关键字声明用的前缀（Unity 只认 hlsl 里出现过的关键字）
_PRAGMA_PREFIX = '#pragma shader_feature_local '
#: program 块的开头（插在它后面）
_PROGRAM_OPEN = ('HLSLPROGRAM', 'CGPROGRAM')


def ensure_shader_keywords(project_root: str | Path, rows):
    """给工程里的桩 shader 补关键字声明（幂等）。

    为什么：AssetRipper 导出的 shader 只有属性表 + 一个「贴图直乘」的 Pass，**没有任何
    `#pragma shader_feature`** ⇒ Unity 认为 `_NORMALMAP`/`_MASKMAP` 这些「不合法」，
    材质里开不起来（实测 `EnableKeyword` 与 `SetKeyword(LocalKeyword)` 都无效，`.mat` 一字未变）。
    这里把真件材质用到的关键字逐个声明进每个 shader 的第一个 program 块。
    ⛔ 只加声明，不动 Pass 里的代码（运行时用的仍是游戏自己的 HDRP/Lit）。
    """
    root = Path(project_root)
    wanted = sorted({kw for row in rows for kw in (row.get('valid') or []) if kw})
    if not wanted:
        return {'shaders': 0, 'patched': 0, 'keywords': 0}
    shaders = sorted(root.glob('Assets/**/*.shader'))
    patched = 0
    for shader in shaders:
        try:
            text = shader.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        missing = [kw for kw in wanted if (_PRAGMA_PREFIX + kw) not in text]
        if not missing:
            continue
        lines = text.splitlines(keepends=True)
        insert_at = None
        for index, line in enumerate(lines):
            if any(token in line for token in _PROGRAM_OPEN):
                insert_at = index + 1
                break
        if insert_at is None:
            continue
        block = ''.join(_PRAGMA_PREFIX + kw + '\n' for kw in missing)
        lines.insert(insert_at, block)
        shader.write_text(''.join(lines), encoding='utf-8')
        patched += 1
    return {'shaders': len(shaders), 'patched': patched, 'keywords': len(wanted)}

