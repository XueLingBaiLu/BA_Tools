"""把材质从 AssetRipper 的**桩 shader** 指回工程里真正的 shader。

2026-10-27 现场（**M1A2 SEP v3** 炮塔顶的 M2 / M240L「在、但平」）：

AssetRipper 给每个真 shader 都导出一份**同名桩**，集中放在 `Assets/Shader/`
（例 `Assets/Shader/HDRP_Lit_0.shader` —— 12,441 B、**1 个 Pass**、`_MainTex * _Color`），
材质默认全挂在桩上；而 HDRP **17.3.0** 的真件 `Lit.shader`（**81,936 B、8 个 Pass**）
就在 `Library/PackageCache/com.unity.render-pipelines.high-definition@…/Runtime/Material/Lit/`。

挂桩 vs 挂真件的差别是**包里那颗 shader 的形状**（实测同一个包里两颗同名 `HDRP/Lit` 并存）：

| 材质 `m_Shader` 指向 | 包里那颗 shader | 真机表现 |
|---|---|---|
| `Assets/Shader/HDRP_Lit_0.shader`（桩） | 18,240 B · 1 Pass | **在，但平**（不受光） |
| `PackageCache/…/Lit.shader`（真件） | 504,392 B · 8 Pass | **受光正常**（2026-10-27 判读） |

⇒ 造包前把「挂在桩上、而同名真件在工程里」的材质改指真件（幂等）。

⛔ 只动 `m_Shader` 那一行的 guid，别的字段一个字节都不碰；⛔ 没有同名真件的桩**不猜**，
原样留着并在报告里点名（例 `Shader Graphs/UnitV3` —— 游戏侧本来就用皮肤整套换掉它）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["STUB_DIR", "bind_real_shaders", "is_stub_shader", "shader_index"]

#: 桩 shader 所在目录（相对工程根，小写比较）—— AssetRipper 的产物集中在这里
STUB_DIR = "assets/shader"

_META_GUID_RE = re.compile(r"guid:\s*([0-9a-f]{32})")
_SHADER_NAME_RE = re.compile(r'\bShader\s+"([^"]+)"')
_MAT_SHADER_RE = re.compile(r"(m_Shader:\s*\{fileID:\s*-?\d+,\s*guid:\s*)([0-9a-f]{32})")
_MAT_REF_RE = re.compile(r"guid:\s*([0-9a-f]{32})")
_MAT_NAME_RE = re.compile(r"^  m_Name:\s*(.+?)\s*$", re.M)


def _relative(project_root: Path, path: Path) -> str:
    """工程内相对路径（小写）；不在工程里就返回空串。"""
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix().lower()
    except (ValueError, OSError):
        return ""


def is_stub_shader(project_root: str | Path, path: str | Path) -> bool:
    """这个 shader 文件是不是 AssetRipper 的桩（位于 `Assets/Shader/` 下）。"""
    rel = _relative(Path(project_root), Path(path))
    return bool(rel) and rel.startswith(STUB_DIR)


def _guid_of(meta: Path) -> str | None:
    """`.meta` 第二行的 guid（AssetRipper 就写在最前面，只读头部）。"""
    try:
        head = meta.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        return None
    match = _META_GUID_RE.search(head)
    return match.group(1) if match else None


def _shader_name(path: Path) -> str | None:
    """shader 文件里的 `Shader "名字"`；读不到就 None（⛔ 不按文件名猜）。"""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    match = _SHADER_NAME_RE.search(head)
    return match.group(1) if match else None


def shader_index(project_root: str | Path) -> dict[str, list[dict[str, Any]]]:
    """`{shader 名: [{guid, path, stub}, …]}`（只收有 `.meta`、读得到名字的那些）。"""
    root = Path(project_root)
    index: dict[str, list[dict[str, Any]]] = {}
    pattern = ("Assets/**/*.shader", "Library/PackageCache/**/*.shader")
    for item in pattern:
        for path in root.glob(item):
            meta = Path(str(path) + ".meta")
            if not meta.is_file():
                continue
            guid = _guid_of(meta)
            name = _shader_name(path)
            if not guid or not name:
                continue
            index.setdefault(name, []).append({
                "guid": guid, "path": path, "stub": is_stub_shader(root, path)})
    return index


def _real_guid(rows: list[dict[str, Any]], stub_guid: str) -> str | None:
    """同名 shader 里的真件 guid（有多个就取第一个，按路径排序保证可复现）。"""
    for row in sorted(rows, key=lambda r: str(r["path"])):
        if not row["stub"] and row["guid"] != stub_guid:
            return str(row["guid"])
    return None


def _mat_files(project_root: Path, prefab_file: Path | None) -> list[Path]:
    """要看的 `.mat`：给了 prefab 就只取它引到的那些（和 material_values 同口径）。"""
    if prefab_file is None:
        return sorted(project_root.glob("Assets/**/*.mat"))
    try:
        text = prefab_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    guids = set(_MAT_REF_RE.findall(text))
    out: list[Path] = []
    for meta in project_root.glob("Assets/**/*.mat.meta"):
        guid = _guid_of(meta)
        if guid in guids:
            out.append(meta.with_suffix(""))
    return sorted(out)


def bind_real_shaders(project_root: str | Path, prefab_file: str | Path | None = None,
                      *, dry_run: bool = False) -> dict[str, Any]:
    """把材质 `m_Shader` 从桩 guid 改成同名真件 guid（幂等，失败一律 fail-soft）。

    返回读数：`{prefab, materials, stub, repointed, unmatched, files}`。
    `unmatched` = 挂在桩上、但工程里找不到同名真件的材质（例 `Shader Graphs/UnitV3`），
    ⛔ 这些**不改**，只点名 —— 猜一个同名之外的东西会让材质直接失效。
    """
    root = Path(project_root)
    prefab = Path(prefab_file) if prefab_file else None
    if prefab is not None and not prefab.is_absolute():
        prefab = root / prefab
    report: dict[str, Any] = {
        "prefab": prefab.name if prefab else None,
        "materials": 0, "stub": 0, "repointed": 0,
        "unmatched": [], "files": [],
    }
    index = shader_index(root)
    if not index:
        return report
    by_guid = {str(row["guid"]): (name, row)
               for name, rows in index.items() for row in rows}
    for mat in _mat_files(root, prefab):
        try:
            text = mat.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _MAT_SHADER_RE.search(text)
        if not match:
            continue
        report["materials"] += 1
        stub_guid = match.group(2)
        owner = by_guid.get(stub_guid)
        if owner is None or not owner[1]["stub"]:
            continue
        name = owner[0]
        report["stub"] += 1
        want = _real_guid(index.get(name) or [], stub_guid)
        if want is None:
            row = _MAT_NAME_RE.search(text)
            report["unmatched"].append({
                "material": row.group(1).strip() if row else mat.stem,
                "shader": name,
            })
            continue
        if not dry_run:
            mat.write_text(text[:match.start(2)] + want + text[match.end(2):], encoding="utf-8")
        report["repointed"] += 1
        report["files"].append(mat.name)
    return report
