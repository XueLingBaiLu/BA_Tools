"""`core/assets/unity_stub_repoint.py` 的用例（2026-10-26：真件 prefab -> 桩）。

守四条：
  · 认组件只能靠**字段签名**唯一命中（⛔ 不按名字凑）；
  · 只改 `m_Script:` 那一行，其余字节逐字节不变；
  · 幂等（第二次跑 rewritten=0）；
  · 有一个认不出就**整体拒绝**（不半改）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.core.assets import unity_stub_repoint as sr

DLL_GUID = "b" * 32
STUB_GUID = "a" * 32
OTHER_STUB_GUID = "c" * 32


def _project(tmp_path: Path, *, stubs: dict[str, str]) -> Path:
    root = tmp_path / 'proj'
    stub_dir = root / 'Assets' / 'Plugins' / 'BrokenArrowStubs'
    stub_dir.mkdir(parents=True)
    for i, (name, body) in enumerate(sorted(stubs.items())):
        guid = STUB_GUID if i == 0 else (OTHER_STUB_GUID if i == 1 else str(i) * 32)
        (stub_dir / (name + '.cs')).write_text(body, encoding='utf-8')
        (stub_dir / (name + '.cs.meta')).write_text(
            'fileFormatVersion: 2\nguid: ' + guid + '\n', encoding='utf-8')
    moved = root / '_moved_out' / 'Plugins'
    moved.mkdir(parents=True)
    (moved / 'BrokenArrow.dll').write_bytes(b'MZ')
    (moved / 'BrokenArrow.dll.meta').write_text(
        'fileFormatVersion: 2\nguid: ' + DLL_GUID + '\n', encoding='utf-8')
    return root


def _prefab(root: Path, *, alpha_only: bool = False) -> Path:
    body = (
        '%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n'
        '--- !u!1 &100\nGameObject:\n  m_Name: Root\n'
        '--- !u!114 &200\nMonoBehaviour:\n'
    )
    body += ('  m_Script: {fileID: -570658340, guid: ' + DLL_GUID + ', type: 3}\n')
    body += '  m_Name: \n  m_EditorClassIdentifier: \n  Alpha: 3\n'
    body += '  Beta: 4\n' if not alpha_only else '  Gamma: 9\n'
    path = root / 'Assets' / 'P.prefab'
    path.parent.mkdir(parents=True, exist_ok=True)
    # ⛔ 按字节写：文本模式的 write_text 会在 Windows 上把 \n 翻成 \r\n，
    #   那样「字节级没动别处」这条断言测的就不是模块而是夹具了。
    path.write_bytes(body.encode('utf-8'))
    return path


STUB_BODY = 'namespace X { public class Foo { public int Alpha; public int Beta; } }'


def test_plan_maps_by_field_signature(tmp_path: Path) -> None:
    root = _project(tmp_path, stubs={'Foo': STUB_BODY})
    prefab = _prefab(root)
    plan = sr.plan_prefab(project_root=root, prefab=prefab)
    assert plan['unmatched'] == []
    assert plan['already_stubbed'] == 0
    assert [c['stub_class'] for c in plan['components']] == ['Foo']
    assert plan['components'][0]['dll'] == 'BrokenArrow.dll'


def test_unknown_signature_goes_to_unmatched_and_blocks_apply(tmp_path: Path) -> None:
    root = _project(tmp_path, stubs={'Foo': STUB_BODY})
    prefab = _prefab(root, alpha_only=True)   # 字段是 Alpha/Gamma，不是 Alpha/Beta
    plan = sr.plan_prefab(project_root=root, prefab=prefab)
    assert len(plan['unmatched']) == 1
    assert plan['components'] == []
    before = prefab.read_text(encoding='utf-8')
    with pytest.raises(sr.StubRepointError) as exc:
        sr.apply_plan(plan, backup_root=tmp_path / 'bk')
    assert '拒绝半改' in str(exc.value)
    assert prefab.read_text(encoding='utf-8') == before      # 一个字都没动


def test_apply_rewrites_only_the_script_line(tmp_path: Path) -> None:
    root = _project(tmp_path, stubs={'Foo': STUB_BODY})
    prefab = _prefab(root)
    before_bytes = prefab.read_bytes()
    before = prefab.read_text(encoding='utf-8')
    plan = sr.plan_prefab(project_root=root, prefab=prefab)
    res = sr.apply_plan(plan, backup_root=tmp_path / 'bk')
    after = prefab.read_text(encoding='utf-8')
    assert res['rewritten'] == 1
    assert after.count(STUB_GUID) == 1
    assert DLL_GUID not in after
    assert after.replace(
        'm_Script: {fileID: 11500000, guid: ' + STUB_GUID + ', type: 3}',
        'm_Script: {fileID: -570658340, guid: ' + DLL_GUID + ', type: 3}',
    ) == before
    # ★ 字节级：换行符一个都不许被翻译（2026-10-26 实测踩过：文本模式写回把 LF 全改 CRLF）
    assert prefab.read_bytes().replace(
        b'm_Script: {fileID: 11500000, guid: ' + STUB_GUID.encode() + b', type: 3}',
        b'm_Script: {fileID: -570658340, guid: ' + DLL_GUID.encode() + b', type: 3}',
    ) == before_bytes
    assert Path(res['backup_dir'], 'P.prefab').read_text(encoding='utf-8') == before


def test_second_run_is_a_no_op(tmp_path: Path) -> None:
    root = _project(tmp_path, stubs={'Foo': STUB_BODY})
    prefab = _prefab(root)
    first = sr.apply_plan(sr.plan_prefab(project_root=root, prefab=prefab),
                          backup_root=tmp_path / 'bk')
    assert first['rewritten'] == 1
    again = sr.plan_prefab(project_root=root, prefab=prefab)
    assert again['components'] == []
    assert again['already_stubbed'] == 1
    second = sr.apply_plan(again, backup_root=tmp_path / 'bk')
    assert second['rewritten'] == 0
