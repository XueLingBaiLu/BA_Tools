"""shader_bind —— 把材质从 AssetRipper 的桩 shader 指回工程真件。

根因（2026-10-27 真机）：桩只有 1 个 Pass ⇒ 单位里**不被皮肤覆盖**的件（枪）在游戏里
「在、但平」；工程里同名真件（HDRP/Lit 8 Pass）就在 `Library/PackageCache/` 下。
"""
from __future__ import annotations

from pathlib import Path

from ba_mod_maker.core.assets import shader_bind

STUB_GUID = "a" * 32
REAL_GUID = "b" * 32
MAT_GUID = "c" * 32
OTHER_MAT_GUID = "d" * 32
GRAPH_GUID = "e" * 32


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _meta(path: Path, guid: str) -> None:
    _write(Path(str(path) + ".meta"), "fileFormatVersion: 2\nguid: " + guid + "\n")


def _mat(root: Path, name: str, shader_guid: str, mat_guid: str) -> Path:
    mat = _write(
        root / "Assets" / "Materials" / (name + ".mat"),
        "Material:\n  m_Name: " + name + "\n"
        "  m_Shader: {fileID: 4800000, guid: " + shader_guid + ", type: 3}\n")
    _meta(mat, mat_guid)
    return mat


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    stub = _write(root / "Assets" / "Shader" / "HDRP_Lit_0.shader",
                  'Shader "HDRP/Lit" {\n  SubShader { Pass { } }\n}\n')
    _meta(stub, STUB_GUID)
    real = _write(root / "Library" / "PackageCache" / "hdrp" / "Lit.shader",
                  'Shader "HDRP/Lit" {\n  SubShader { Pass { } Pass { } }\n}\n')
    _meta(real, REAL_GUID)
    graph = _write(root / "Assets" / "Shader" / "Shader Graphs_UnitV3_3.shader",
                   'Shader "Shader Graphs/UnitV3" { SubShader { Pass { } } }\n')
    _meta(graph, GRAPH_GUID)
    return root


def test_stub_is_repointed_to_real_and_rule_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)
    mat = _mat(root, "M2", STUB_GUID, MAT_GUID)

    first = shader_bind.bind_real_shaders(root)

    assert (first["stub"], first["repointed"]) == (1, 1)
    assert REAL_GUID in mat.read_text(encoding="utf-8")
    second = shader_bind.bind_real_shaders(root)
    assert (second["stub"], second["repointed"]) == (0, 0)


def test_dry_run_reports_but_does_not_write(tmp_path: Path) -> None:
    root = _project(tmp_path)
    mat = _mat(root, "M2", STUB_GUID, MAT_GUID)
    before = mat.read_text(encoding="utf-8")

    res = shader_bind.bind_real_shaders(root, dry_run=True)

    assert res["repointed"] == 1
    assert mat.read_text(encoding="utf-8") == before


def test_stub_without_real_counterpart_is_named_not_guessed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    mat = _mat(root, "US_M1A2C", GRAPH_GUID, MAT_GUID)
    before = mat.read_text(encoding="utf-8")

    res = shader_bind.bind_real_shaders(root)

    assert res["repointed"] == 0
    assert res["unmatched"] == [{"material": "US_M1A2C", "shader": "Shader Graphs/UnitV3"}]
    assert mat.read_text(encoding="utf-8") == before


def test_prefab_scope_leaves_unreferenced_materials_alone(tmp_path: Path) -> None:
    root = _project(tmp_path)
    used = _mat(root, "M2", STUB_GUID, MAT_GUID)
    other = _mat(root, "Unused", STUB_GUID, OTHER_MAT_GUID)
    prefab = _write(root / "Assets" / "Prefabs" / "US_M1A2C.prefab",
                    "Prefab:\n  m_Materials:\n  - {fileID: 2100000, guid: "
                    + MAT_GUID + ", type: 2}\n")
    other_before = other.read_text(encoding="utf-8")

    res = shader_bind.bind_real_shaders(root, prefab)

    assert res["materials"] == 1
    assert REAL_GUID in used.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == other_before


def test_prefab_path_may_be_relative_to_project(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _mat(root, "M2", STUB_GUID, MAT_GUID)
    _write(root / "Assets" / "Prefabs" / "P.prefab",
           "Prefab:\n  - {fileID: 2100000, guid: " + MAT_GUID + ", type: 2}\n")

    res = shader_bind.bind_real_shaders(root, "Assets/Prefabs/P.prefab")

    assert res["repointed"] == 1


def test_missing_meta_is_fail_soft(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    _write(root / "Assets" / "Materials" / "NoMeta.mat",
           "Material:\n  m_Name: NoMeta\n  m_Shader: {fileID: 4800000, guid: "
           + STUB_GUID + ", type: 3}\n")

    res = shader_bind.bind_real_shaders(root)

    assert res == {"prefab": None, "materials": 0, "stub": 0, "repointed": 0,
                  "unmatched": [], "files": []}
