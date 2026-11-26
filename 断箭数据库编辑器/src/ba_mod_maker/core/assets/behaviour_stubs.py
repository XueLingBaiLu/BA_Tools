'''造包前的「行为类桩覆盖」预检（2026-10-28 起）。

为什么存在（一次真事故的根因）：

- 真件 prefab 的 AnimationHub 五个状态数组是 [SerializeReference]，YAML 里每一项写
  type: {class: X, ns: Y, asm: Z}（ProceduralDirt 这种是**空命名空间**，写作 ns:）。
- Unity 导入时按 (class, ns, asm) 去工程里找 MonoScript；找不到就序列化成 null（包里
  rid=-2），运行期 AnimationHub.Init 对 null 抛 NRE（ISIL：UnitBuilder.InitAnimations
  → AnimationHub.Init → AllStates().ForEach(<Init>b__166_0)，lambda 里判空即抛）。
- 旧链路只在 Unity 跑完（约 80 s）、包写出来之后由 check_bundle 的 A10 报红 —— 那时包里
  的 null 已经丢了类名，报错说不清缺哪个类。所以把这条判据前移到**造 job 之前**。

口径：只查 AnimationHub 的五个状态数组（其他 [SerializeReference] 用途不在这里判，
免得把不参与 AnimationHub 的可选引用判红）；类型身份 = (namespace, class)。
'''

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: AnimationHub 上五个 [SerializeReference] IAnimationBehaviour[] 字段（顺序同真件声明）。
ANIM_HUB_STATE_FIELDS = (
    '_universalState',
    '_demoState',
    '_gameState',
    '_preDeathState',
    '_deathState',
)

#: 导出工程里桩的既定位置（与 unity_stub_repoint 同一口径）。
STUB_REL = Path('Assets') / 'Plugins' / 'BrokenArrowStubs'

#: `- rid: N` 后面跟 `type: {class: X, ns: Y, asm: Z}`；⚠ ns 可能为空（写作 ns:, asm:）。
_REF_TYPE = re.compile(
    r'- rid: (-?\d+)\n\s+type: \{class: ([^,}]*), ns: ?([^,}]*), asm: ?([^}]*)\}')
_STATE_BLOCK = r'%s:\n((?:  - rid: -?\d+\n)+)'
_NAMESPACE = re.compile(r'^\s*namespace\s+([\w.]+)', re.MULTILINE)
_CLASS = re.compile(r'\b(?:class|struct)\s+([A-Za-z_]\w*)')


class BehaviourStubError(RuntimeError):
    '''缺行为类桩 —— 造包前拦下，别等 Unity 跑完才红。'''


@dataclass(frozen=True)
class BehaviourRef:
    '''一处 [SerializeReference] 的类型身份 + 它挂在哪。'''

    field: str
    index: int
    cls: str  # 空串 = YAML 里本来就没解出来（那时更该拦）
    ns: str  # 空串 = 全局命名空间（ProceduralDirt 就是这种）
    asm: str

    def label(self) -> str:
        cls = self.cls or '（未解析）'
        ns = self.ns or '（空命名空间）'
        return f'{cls}（ns={ns}, asm={self.asm}）'


def behaviour_refs(prefab: str | Path) -> list[BehaviourRef]:
    '''读出 prefab 里 AnimationHub 五个状态数组引用的行为类型（按字段/下标排序）。'''
    text = Path(prefab).read_text(encoding='utf-8', errors='replace')
    refs: dict[str, tuple[str, str, str]] = {}
    for m in _REF_TYPE.finditer(text):
        refs[m.group(1)] = (m.group(2).strip(), m.group(3).strip(), m.group(4).strip())
    out: list[BehaviourRef] = []
    for field in ANIM_HUB_STATE_FIELDS:
        block = re.search(_STATE_BLOCK % re.escape(field), text)
        if not block:
            continue
        rids = re.findall(r'- rid: (-?\d+)', block.group(1))
        for index, rid in enumerate(rids):
            cls, ns, asm = refs.get(rid, ('', '', ''))
            out.append(BehaviourRef(field=field, index=index, cls=cls, ns=ns, asm=asm))
    return out


def stub_index(stub_root: str | Path) -> dict[tuple[str, str], str]:
    '''桩目录里的 (namespace, class) -> asm（namespace 空串 = 全局命名空间）。'''
    root = Path(stub_root)
    asm = ''
    for meta in sorted(root.glob('*.asmdef')):
        try:
            asm = str(json.loads(meta.read_text(encoding='utf-8')).get('name') or '')
        except (OSError, ValueError):
            asm = ''
        break
    index: dict[tuple[str, str], str] = {}
    for path in sorted(root.rglob('*.cs')):
        text = path.read_text(encoding='utf-8', errors='replace')
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        text = re.sub(r'//[^\n]*', '', text)
        # 一个文件里可能有多个 namespace 块（AnimationBehaviors.cs 就有 6 个）⇒
        # 按「类之前最近的那个 namespace」归属，别拿文件头那个套整文件。
        marks = [(m.start(), m.group(1)) for m in _NAMESPACE.finditer(text)]
        if not marks:
            marks = [(0, '')]
        for m in _CLASS.finditer(text):
            ns = ''
            for pos, name in marks:
                if pos < m.start():
                    ns = name
                else:
                    break
            index.setdefault((ns, m.group(1)), asm)
    return index


def missing_behaviour_stubs(
    prefab: str | Path,
    *,
    stub_root: str | Path,
) -> list[BehaviourRef]:
    '''返回「prefab 引用了、但桩目录里没有同 (ns, class) 实现」的那些条目。'''
    have = stub_index(stub_root)
    missing: list[BehaviourRef] = []
    for ref in behaviour_refs(prefab):
        if not ref.cls or (ref.ns, ref.cls) not in have:
            missing.append(ref)
    return missing


def describe_missing(missing: Iterable[BehaviourRef]) -> str:
    '''一条能直接贴到界面上的拒绝理由（点名到类、命名空间、asm、挂在哪）。'''
    rows = [f'  - {m.label()} ← AnimationHub.{m.field}[{m.index}]' for m in missing]
    return (
        '缺行为类桩（[SerializeReference] 解析不了 ⇒ 包里写成 null ⇒ '
        'AnimationHub.Init 抛 NRE ⇒ 整台单位加载失败）：\n'
        + '\n'.join(rows)
        + '\n在工程的 Assets/Plugins/BrokenArrowStubs/ 放同名同命名空间的类再重造'
        '（全局命名空间的类，命名空间就要空着）。'
    )


def assert_stubs_complete(prefab: str | Path, *, stub_root: str | Path) -> int:
    '''缺就抛 BehaviourStubError —— build_job 在起 Unity 之前调它；返回被检查的引用条数。'''
    missing = missing_behaviour_stubs(prefab, stub_root=stub_root)
    if missing:
        raise BehaviourStubError(describe_missing(missing))
    return len(behaviour_refs(prefab))
