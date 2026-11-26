'''行为类桩预检（core.assets.behaviour_stubs）的用例。

每条都成对：**缺桩必须红、有桩必须绿** —— 尺子不能只会在真实工程上对。

⚠ 这里不用 pytest 的 tmp_path：本机（DSH 沙箱）下 pytest 自建的 basetemp 目录会让
Python 自己 listdir 报 WinError 5（2026-10-28 实测），所以目录用普通 mkdir 自建
（`tempfile.mkdtemp` 也一样不可读，见同一批实测）。
'''

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ba_mod_maker.core.assets import behaviour_stubs as bs


def _repo_root() -> Path:
    '''往上找 pyproject.toml —— 用例被复制到临时目录里跑时也能定位仓库根。'''
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        if (parent / 'pyproject.toml').is_file():
            return parent
    return here


import os
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")


@contextmanager
def sandbox() -> Iterator[Path]:
    '''自建目录（⛔ 不用 tempfile.mkdtemp：本机沙箱下 mkdtemp 出来的目录内容不可读）。'''
    WORK.mkdir(parents=True, exist_ok=True)
    root = WORK / ('stubtest-' + uuid.uuid4().hex[:8])
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _prefab(root: Path, refs: list[tuple[str, str]], *,
            extra_rids: list[str] | None = None) -> Path:
    '''造一份最小 prefab：`_gameState` 列出 rids，references 给出 (rid, (class, ns))。'''
    lines = ['--- !u!114 &2', 'MonoBehaviour:', '  _gameState:']
    for rid in (extra_rids or [r[0] for r in refs]):
        lines.append('  - rid: ' + rid)
    lines += ['  references:', '    version: 2', '    RefIds:']
    for rid, (cls, ns) in refs:
        lines.append('    - rid: ' + rid)
        lines.append(f'      type: {{class: {cls}, ns: {ns}, asm: BrokenArrow}}')
        lines.append('      data: {}')
    path = root / 'p.prefab'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _stub(root: Path, name: str, ns: str | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    body = 'public class ' + name + ' : IAnimationBehaviour { }'
    text = ('namespace ' + ns + ' {\n' + body + '\n}\n') if ns else (body + '\n')
    (root / (name + '.cs')).write_text(text, encoding='utf-8')


def test_global_type_is_missing_when_stub_is_namespaced() -> None:
    '''负控：`ns:` 为空（ProceduralDirt 那种）不能被同名但带命名空间的桩满足。'''
    with sandbox() as root:
        prefab = _prefab(root, [('111', ('ProceduralDirt', ''))])
        stub = root / 'stubs'
        _stub(stub, 'ProceduralDirt', 'BrokenArrow.Client.Ecs.AnimationBehaviors')
        missing = bs.missing_behaviour_stubs(prefab, stub_root=stub)
        assert [m.cls for m in missing] == ['ProceduralDirt']
        assert missing[0].ns == ''


def test_global_stub_satisfies_global_type() -> None:
    '''正控：全局命名空间的桩必须能对上（不然负控就是假红）。'''
    with sandbox() as root:
        prefab = _prefab(root, [('111', ('ProceduralDirt', ''))])
        stub = root / 'stubs'
        _stub(stub, 'ProceduralDirt', None)
        assert bs.missing_behaviour_stubs(prefab, stub_root=stub) == []


def test_assert_raises_and_names_the_class() -> None:
    with sandbox() as root:
        prefab = _prefab(root, [('111', ('ProceduralDirt', ''))])
        with pytest.raises(bs.BehaviourStubError) as exc:
            bs.assert_stubs_complete(prefab, stub_root=root / 'empty')
        message = str(exc.value)
        assert 'ProceduralDirt' in message
        assert '_gameState[0]' in message


def test_class_is_attributed_to_its_own_namespace_block() -> None:
    '''回归：一个文件里多个 namespace 块时，别把第一个命名空间套给整文件。'''
    with sandbox() as root:
        stub = root / 'stubs'
        stub.mkdir()
        (stub / 'mix.cs').write_text(
            'namespace A.Shared {\npublic class Other { }\n}\n'
            'namespace A.Behaviours {\npublic class ProceduralDirt { }\n}\n',
            encoding='utf-8',
        )
        prefab = _prefab(root, [('111', ('ProceduralDirt', 'A.Behaviours'))])
        assert bs.missing_behaviour_stubs(prefab, stub_root=stub) == []


def test_unresolved_rid_counts_as_missing() -> None:
    '''YAML 里 rid 没对应 type（块丢了）—— 更该拦，不能当没关系。'''
    with sandbox() as root:
        prefab = _prefab(
            root, [('111', ('ShowVFX', 'A.Behaviours'))], extra_rids=['111', '222'])
        stub = root / 'stubs'
        _stub(stub, 'ShowVFX', 'A.Behaviours')
        missing = bs.missing_behaviour_stubs(prefab, stub_root=stub)
        assert [(m.cls, m.index) for m in missing] == [('', 1)]

