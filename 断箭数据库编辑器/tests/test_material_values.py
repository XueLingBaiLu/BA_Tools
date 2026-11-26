"""材质数值对齐（`core/assets/material_values.py`）—— 正控 + 三个负控。

来历：2026-10-26 用户真机判读「Kord 机枪比原版亮一档、更平、没金属感」，根因是 AssetRipper 把
`_MetallicRemapMax` 导成 0（真件 1.0）。这一层要保证：**真件 prefab 引到的材质**每次造包都被对齐，
而且**只改值**、幂等、不碰没被引到的同名副本。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ba_mod_maker.core.assets import material_values

GUID = "0123456789abcdef0123456789abcdef"
OTHER_GUID = "fedcba9876543210fedcba9876543210"

MAT_TEXT = """%YAML 1.1
--- !u!21 &2100000
Material:
  m_Name: 
  m_EditorClassIdentifier: Unity.RenderPipelines.HighDefinition.Editor::AssetVersion
  m_SavedProperties:
    m_Floats:
    - _Metallic: 0
    - _MetallicRemapMax: 0
    - _Smoothness: 0.5
    m_Colors:
    - _BaseColor: {r: 1, g: 1, b: 1, a: 1}
  m_AllowLocking: 0
--- !u!114 &-1
Material2:
  m_Name: Kord
"""


def _graph(tmp_path: Path) -> Path:
    body = {
        "m_TexEnvs": [],
        "m_Ints": [],
        "m_Floats": [["_Metallic", 0], ["_MetallicRemapMax", 1],
                     ["_Smoothness", 0.4472135901451111]],
        "m_Colors": [["_BaseColor", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}]],
    }
    mat = (
        '{"path_id": 1, "__type__": "Material", "typetree": {'
        '"m_Name": "Kord", "m_Shader": {"m_FileID": 0, "m_PathID": 7322088784485553867}, '
        '"m_ValidKeywords": ["_MASKMAP"], "m_InvalidKeywords": [], '
        '"m_SavedProperties": ' + json.dumps(body) + '}}'
    )
    path = tmp_path / "prefab_graph.json"
    path.write_text('{"objects": [' + mat + ']}', encoding='utf-8')
    return path


def _project(tmp_path: Path, *, guid: str = GUID,
             in_prefab: bool = True) -> tuple[Path, Path, Path]:
    root = tmp_path / "proj"
    assets = root / "Assets" / "Weapon"
    assets.mkdir(parents=True)
    mat = assets / "Kord.mat"
    mat.write_text(MAT_TEXT, encoding='utf-8')
    (assets / "Kord.mat.meta").write_text(
        "fileFormatVersion: 2\nguid: " + guid + "\ntimeCreated: 1\n", encoding='utf-8')
    prefab = root / "Assets" / "unit.prefab"
    ref = "" if not in_prefab else (
        "  m_Materials:\n  - {fileID: 2100000, guid: " + guid + ", type: 2}\n")
    prefab.write_text("MeshRenderer:\n" + ref, encoding='utf-8')
    return root, prefab, mat


def test_reads_saved_properties(tmp_path: Path) -> None:
    rows = material_values.read_graph_saved_properties(_graph(tmp_path))
    assert rows["Kord"]["floats"]["_MetallicRemapMax"] == 1
    assert rows["Kord"]["colors"]["_BaseColor"]["a"] == 1.0


def test_missing_graph_is_fail_soft(tmp_path: Path) -> None:
    assert material_values.read_graph_saved_properties(tmp_path / "nope.json") == {}
    assert material_values.values_of(tmp_path) == {}
    root, prefab, _ = _project(tmp_path)
    report = material_values.align_materials(root, prefab, {})
    assert report["fields"] == 0


def test_aligns_only_what_prefab_uses_and_is_idempotent(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    root, prefab, mat = _project(tmp_path)
    values = material_values.read_graph_saved_properties(graph)
    report = material_values.align_materials(root, prefab, values)
    assert report["materials"] == 1
    assert report["matched"] == 1
    # 两处：_MetallicRemapMax 0->1 与 _Smoothness 0.5->0.4472135901451111
    assert report["fields"] == 2, report
    text = mat.read_text(encoding='utf-8')
    assert "- _MetallicRemapMax: 1\n" in text
    assert "- _Metallic: 0\n" in text  # 真件也是 0，一个字节都不该动
    assert "m_Name: \n" in text  # MonoBlock 那行空名字不许被当成材质名
    first = hashlib.sha256(mat.read_bytes()).hexdigest()
    again = material_values.align_materials(root, prefab, values)
    assert again["fields"] == 0, again  # 幂等：第二遍必须零改动
    assert hashlib.sha256(mat.read_bytes()).hexdigest() == first


def test_unreferenced_material_is_untouched(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    root, prefab, mat = _project(tmp_path, in_prefab=False)
    values = material_values.read_graph_saved_properties(graph)
    report = material_values.align_materials(root, prefab, values)
    assert report["materials"] == 0 and report["fields"] == 0
    assert "- _MetallicRemapMax: 0\n" in mat.read_text(encoding='utf-8')


def test_material_without_graph_entry_is_untouched(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    root, prefab, mat = _project(tmp_path)
    values = material_values.read_graph_saved_properties(graph)
    values["Kord"]["floats"] = {}  # 真件没声明这个键 ⇒ 不该动
    values["Kord"]["colors"] = {}
    report = material_values.align_materials(root, prefab, values)
    assert report["fields"] == 0
    assert "- _MetallicRemapMax: 0\n" in mat.read_text(encoding='utf-8')
