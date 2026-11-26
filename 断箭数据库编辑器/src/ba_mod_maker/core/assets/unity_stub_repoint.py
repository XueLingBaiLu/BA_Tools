"""把「真件 prefab」里指向被移走 DLL 的 `m_Script` 改指到工程里的桩脚本。

## 为什么有这一步（2026-10-26 实测，A 级）

`参考件\\Broken Arrow Assets v1.2.2.1\\ExportedProject` 把游戏 DLL 全挪进了 `_moved_out/Plugins/`（100+ 个）。
真件 prefab 的 MonoBehaviour 里 `m_Script` 仍写着那些 DLL 的 guid ⇒ Unity 认不出类型 ⇒
`PrefabUtility.SaveAsPrefabAsset` 直接拒：

    Error while saving Prefab: '…/RU_T72B3.prefab'.
    You are trying to save a Prefab with a missing script. This is not allowed.

`RU_T72B_Base.prefab` 能过，是因为它**已经被人为改指**到 `Assets/Plugins/BrokenArrowStubs/` 的桩；
其余单位没有这一步 ⇒ 只有手改过的那一个能走通（本轮 BTR-90 / T-72B3 都卡在这里）。

## 怎么认「哪一个组件该指哪个桩」—— 按**字段签名**（实测唯一）

组件块里序列化出来的字段名，与 `dump.cs` 里类型的字段集一一对得上。实测（唯一命中）：

    {_container,_weaponsVfxContainer}
        -> BrokenArrow.Client.Ecs.Animations.AnimationManager
    {_deathState,_demoState,_gameState,…}
        -> BrokenArrow.Client.Ecs.Animations.AnimationHub
    {PrefabBody,PrefabRoot,Renderers,Skins,SpawnTimeObjects}
        -> BrokenArrow.Client.Ecs.Spawn.UnitPrefabRoot
    {AttachVfxToObject,LifeTime,PlayForce,…}
        -> BrokenArrow.Client.Ecs.Effects.EffectsSpawnPoint
    {TurretIndex,Weapons}
        -> BrokenArrow.Client.Ecs.Spawn.UnitPrefabTurretInfo
    {EventName,Strange,TargetBone}
        -> Plugins.FMOD.Bridge.FmodTurretsTurn（在 FMODUnity 程序集）

⛔ 不是按「相近名字」猜：每个组件必须在桩目录里**唯一**命中一个类的字段集，
否则进 `unmatched`（fail-closed）。
⛔ 只改 `m_Script:` 那一行，文件里其余字节一个不动（写后逐字节复核）。

## 边界

· 只处理 `MonoBehaviour`（`--- !u!114`）块；别的类型没有 `m_Script`；
· 已经指向桩的组件原样跳过（**幂等**：第二次跑 `rewritten=0`）；
· 改之前把原文件拷到 `_work/stub_repoint/<时间戳>/`（那是工程文件，不是游戏目录）。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
# 默认 D:\断箭模组制作\_work_db；换机器/换位置用环境变量 BA_WORK_ROOT 覆盖。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

__all__ = [
    "StubCatalog", "StubRepointError", "apply_plan", "apply_tree",
    "discover_prefab_dependencies", "load_catalog", "plan_prefab",
    "plan_prefab_tree", "prefab_references", "project_prefab_index",
]

#: `.cs` 脚本在工程里的脚本 fileID（Unity 对 .cs 一律用这个）
CS_SCRIPT_FILE_ID = 11500000
#: 桩目录（相对工程根）。两处都要扫：跨程序集的桩要放在同名 asmdef 的目录里
#: （例 Plugins.FMOD.Bridge.FmodTurretsTurn 在 FMODUnity 程序集 -> Assets/Plugins/FmodStubs）
STUB_DIRS = (
    Path("Assets/Plugins/BrokenArrowStubs"),
    Path("Assets/Plugins/FmodStubs"),
)
#: 被移走的插件目录（相对工程根）
MOVED_DIR = Path("_moved_out/Plugins")

#: 跟随嵌套 prefab 引用的上限（防环 · 防爆）。实测 2026-10-27（M113A3 / M1A2C）：
#: 主件引用到的工程内 prefab 是 3 / 8 个、全部深度 1、无环 ⇒ 上限留足余量；
#: 一旦触顶就在计划里报 `truncated=True`（⛔ 不静默丢）。
MAX_DEP_PREFABS = 64
MAX_DEP_DEPTH = 8
#: 构建产物目录：每次打包都重建，跟随依赖时**跳过**（它的引用指回源 prefab，跟上就是自环）
GENERATED_DIR = "Assets/_unitbuild"

#: 桩类**没写字段**时靠这张表认（签名来自 dump.cs 实测，不是猜）：
#:   BrokenArrow.Client.Ecs.Animations.AnimationManager  {_container,_weaponsVfxContainer}
#:   BrokenArrow.Client.Ecs.Animations.AnimationHub      {_deathState,_demoState,_gameState,
#:                                                        _preDeathState,_universalState}
#: 这两类的桩是空类（2026-10-22 那轮为了绕开编译依赖只留了类名），字段集对不上，
#: 所以只能用「字段集 -> 类名」的实测对照兜底；⛔ 仍然要求类名在桩目录里真实存在。
#: 空字段组件的通用桩名（真件的类名既不在 prefab 里、m_EditorClassIdentifier 也是空的 ⇒ 拿不到）。
#: 桩只用于「让 Unity 存得下这个组件」，游戏按自己的类型找组件，不会引用到它。
GENERIC_EMPTY_STUB = 'UnknownEmptyBehaviour'

KNOWN_SIGNATURES: dict[frozenset[str], str] = {
    frozenset({"_container", "_weaponsVfxContainer"}): "AnimationManager",
    frozenset({"_deathState", "_demoState", "_gameState", "_preDeathState",
               "_universalState"}): "AnimationHub",
}

_GUID_RE = re.compile(r"^guid:\s*([0-9a-f]{32})", re.M)
#: prefab 文本里任意位置的 guid（PPtr / m_SourcePrefab 都长这样）—— 拿它去查工程 prefab 索引
_GUID_REF_RE = re.compile(r"guid:\s*([0-9a-f]{32})")
_BLOCK_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)\s*$", re.M)
_SCRIPT_RE = re.compile(
    r"m_Script:\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-f]{32}),\s*type:\s*(\d+)\}"
)
_FIELD_RE = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*):", re.M)
_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")
#: 公开字段（Unity 口径：public 且非 static/const/readonly —— 这类一定被序列化）。
#: 允许出现在**任意位置**（桩经常把整个类写在一行里）。
_PUBFIELD_RE = re.compile(
    r"public\s+(?!static\b|const\b|readonly\b)"
    r"[A-Za-z_][A-Za-z0-9_<>,.\[\] ]*?\s+([A-Za-z_][A-Za-z0-9_]*)\s*[;=]"
)
#: 带 `[SerializeField]` / `[SerializeReference]` 的字段（可以私有；属性与字段可同行也可换行）。
#: ⚠ 修饰符可以是**省略**的（C# 字段默认 private）—— HDRP 的 `DecalProjector` 就是
#: `[SerializeField]\nRenderingLayerMask m_DecalLayerMask = …;`（没有 private 也没有 public）。
_SERIALIZED_FIELD_RE = re.compile(
    r"\[(?:SerializeField|SerializeReference)\][\s\S]{0,120}?"
    r"(?:(?:public|private|protected|internal)\s+)?(?:static\s+|readonly\s+|const\s+)*"
    r"[A-Za-z_][A-Za-z0-9_<>,.\[\] ]*?\s+([A-Za-z_][A-Za-z0-9_]*)\s*[;=]"
)


def _stub_fields(text: str) -> set[str]:
    """桩类的**序列化字段名**集合（Unity 口径）。

    ★ 2026-10-26：原来只认 `public <type> name;`，于是「`[SerializeField] private _recoils;`」这种桩
    （`AnimationManagerBridge` / `AnimationManager` / `AnimationHub`）**字段集永远是空的**
    ⇒ 桩目录里明明有类，`pack repoint-stubs` 照样报「认不出」（实测 240+ 个 prefab 卡在这条上）。
    """
    return set(_PUBFIELD_RE.findall(text)) | set(_SERIALIZED_FIELD_RE.findall(text))


class StubRepointError(AssetError):
    """桩化改不动 / 认不出 —— 这是**工程或产物**问题，不是「包不合格」的判定。"""


@dataclass
class StubCatalog:
    """工程里现有的桩：guid -> 类名 + 该类的字段集。"""

    root: Path
    by_guid: dict[str, dict[str, Any]] = field(default_factory=dict)
    moved_dlls: dict[str, str] = field(default_factory=dict)

    def stubs(self) -> list[dict[str, Any]]:
        return list(self.by_guid.values())

    def by_class(self) -> dict[str, dict[str, Any]]:
        return {s['class']: s for s in self.by_guid.values() if s['class']}

    def is_stub_guid(self, guid: str) -> bool:
        return guid in self.by_guid

    def is_moved_dll_guid(self, guid: str) -> bool:
        return guid in self.moved_dlls


def _read_exact(path: Path) -> str:
    """按字节读再解码 —— ⛔ 不用 read_text（它有换行翻译，会把 CRLF/LF 统一改写）。"""
    return path.read_bytes().decode('utf-8', errors='replace')


def _write_exact(path: Path, text: str) -> None:
    """按字节写回 —— 保证「只改 m_Script 那几行」在**字节**层面也成立。"""
    path.write_bytes(text.encode('utf-8'))


def _guid_of(meta: Path) -> str:
    try:
        head = meta.read_text(encoding='utf-8', errors='replace')[:400]
    except OSError:
        return ''
    m = _GUID_RE.search(head)
    return m.group(1) if m else ''


#: 工程里真实存在的脚本 guid 缓存（project_root -> {guid, ...}）。
#: ⚠ 只在「组件引用的 guid 既不是桩、也不是被移走的 DLL」时才扫一次 —— 正常 prefab 走不到这条路。
_PROJECT_SCRIPT_GUIDS: dict[str, set[str]] = {}


def project_script_guids(project_root: str | Path) -> set[str]:
    """工程里**真实存在**的脚本资产 guid（Assets / Packages / Library/PackageCache 的 `.meta`）。

    ★ 2026-10-26：真件 prefab 里可能写着一条 `m_Script` guid，而导出物里**根本没有**这个脚本
    （M113A3 的 `US_Army_granade` 就是：`5fc97c141232edee425cbc3d3d148d11` 在 Assets / Packages /
    PackageCache 三处都搜不到）⇒ Unity 保存 prefab 时直接拒：「trying to save a Prefab with a
    missing script. This is not allowed.」
    """
    key = str(Path(project_root))
    cached = _PROJECT_SCRIPT_GUIDS.get(key)
    if cached is not None:
        return cached
    found: set[str] = set()
    root = Path(project_root)
    for rel in ('Assets', 'Packages', Path('Library') / 'PackageCache'):
        base = root / rel
        if not base.is_dir():
            continue
        for meta in base.rglob('*.meta'):
            name = meta.name
            if not (name.endswith('.cs.meta') or name.endswith('.dll.meta')):
                continue
            try:
                head = meta.read_text(encoding='utf-8', errors='replace')[:400]
            except OSError:
                continue
            match = _GUID_RE.search(head)
            if match:
                found.add(match.group(1))
    _PROJECT_SCRIPT_GUIDS[key] = found
    return found


#: 工程里**真脚本**（Assets / Packages / Library/PackageCache 的 .cs）索引缓存。
_PROJECT_SCRIPT_CLASSES: dict[str, list[dict[str, Any]]] = {}


def project_script_classes(project_root: str | Path) -> list[dict[str, Any]]:
    """工程里所有 `.cs` 资产的 `{class, guid, cs, fields}`（按 guid 去重，取第一个 class）。"""
    key = str(Path(project_root))
    cached = _PROJECT_SCRIPT_CLASSES.get(key)
    if cached is not None:
        return cached
    root = Path(project_root)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel in ('Assets', 'Packages', Path('Library') / 'PackageCache'):
        base = root / rel
        if not base.is_dir():
            continue
        for meta in base.rglob('*.cs.meta'):
            cs = meta.with_suffix('')
            if not cs.is_file():
                continue
            guid = _guid_of(meta)
            if not guid or guid in seen:
                continue
            try:
                text = cs.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            match = _CLASS_RE.search(text)
            if not match:
                continue
            seen.add(guid)
            out.append({'guid': guid, 'class': match.group(1), 'cs': str(cs),
                        'fields': _stub_fields(text)})
    _PROJECT_SCRIPT_CLASSES[key] = out
    return out


#: 真脚本反查的覆盖率门槛。实测（US_LHA_Damage 的 DecalProjector 组件）：
#:   组件 15 个字段里 **14 个**能在 `DecalProjector.cs` 里找到（覆盖 0.93；少的那个是 `m_Version`，
#:   序列化版本号，当前 HDRP 类里已经不写），第二名 `inherited` 只覆盖 0.13 ⇒ 0.9 这条线区分度足够。
#: ⛔ 阈值不是拍脑袋：低于它的候选一律不认（不猜），宁可报「认不出」。
REAL_SCRIPT_MIN_COVERAGE = 0.9
#: 组件字段太少（<3）时不做反查（小字段集到处都能凑上，不唯一）。
REAL_SCRIPT_MIN_FIELDS = 3


def match_real_script(project_root: str | Path, fields: set[str]) -> dict[str, Any] | None:
    """在工程里的**真脚本**中按字段集反查候选，要求**唯一**且覆盖率 ≥ `REAL_SCRIPT_MIN_COVERAGE`。

    ★ 2026-10-26：真件引用的脚本属于 **Unity 包程序集**（例 HDRP 的 `DecalProjector`）时，
    工程里本来就有这个类 —— 指回真脚本才有行为；补桩只会让贴花不渲染。
    ⛔ 命中不唯一 / 覆盖率不够 ⇒ 返回 None（交回上层当「认不出」，不猜）。
    """
    if len(fields) < REAL_SCRIPT_MIN_FIELDS:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    tie = False
    for candidate in project_script_classes(project_root):
        if not candidate['fields']:
            continue
        coverage = len(fields & candidate['fields']) / len(fields)
        if coverage < REAL_SCRIPT_MIN_COVERAGE:
            continue
        if best is None or coverage > best[0]:
            best = (coverage, candidate)
            tie = False
        elif coverage == best[0]:
            tie = True
    if best is None or tie:
        return None
    return best[1]


def load_catalog(project_root: str | Path) -> StubCatalog:
    """读工程：桩目录（guid + 类名 + 公共字段）与被移走的 DLL guid。"""
    root = Path(project_root)
    stub_dirs = [root / rel for rel in STUB_DIRS]
    if not any(d.is_dir() for d in stub_dirs):
        raise StubRepointError(
            '工程里没有桩目录：' + '、'.join(str(d) for d in stub_dirs),
            hint='这是导出工程的既定布局（Assets/Plugins/BrokenArrowStubs）；缺它说明工程根指错了',
        )
    cat = StubCatalog(root=root)
    metas = [m for d in stub_dirs if d.is_dir() for m in sorted(d.glob('*.cs.meta'))]
    for meta in metas:
        guid = _guid_of(meta)
        if not guid:
            continue
        cs = meta.with_suffix('')            # foo.cs.meta -> foo.cs
        cls = ''
        fields: set[str] = set()
        if cs.is_file():
            text = cs.read_text(encoding='utf-8', errors='replace')
            m = _CLASS_RE.search(text)
            cls = m.group(1) if m else ''
            fields = _stub_fields(text)
        cat.by_guid[guid] = {'guid': guid, 'class': cls, 'cs': str(cs), 'fields': fields}
    moved = root / MOVED_DIR
    if moved.is_dir():
        for meta in moved.glob('*.dll.meta'):
            guid = _guid_of(meta)
            if guid:
                cat.moved_dlls[guid] = meta.with_suffix('').name
    return cat


def _blocks(text: str) -> list[tuple[int, int, str]]:
    """切 `--- !u!<class> &<fileID>` 块：返回 [(classId, fileID, 块正文)]。"""
    marks = list(_BLOCK_RE.finditer(text))
    out: list[tuple[int, int, str]] = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(m.group(1)), int(m.group(2)), text[start:end]))
    return out


def _owner_of(block: str) -> int:
    """组件块里的 `m_GameObject: {fileID: N}` = 它挂在哪个 GameObject 上（删组件要用）。"""
    m = re.search(r'm_GameObject:\s*\{fileID:\s*(-?\d+)\}', block)
    return int(m.group(1)) if m else 0


def _component_fields(block: str) -> set[str]:
    """组件块里 m_EditorClassIdentifier 之后、缩进两格的键 = 序列化字段名。"""
    tail = block.split('m_EditorClassIdentifier:', 1)[-1]
    return set(_FIELD_RE.findall(tail))


def _match_stub(cat: StubCatalog, fields: set[str]) -> list[dict[str, Any]]:
    """组件字段集 -> 候选桩。三档，全部要求**唯一**：

    ① 桩的字段全在组件里（桩是组件的子集）—— 最常见；
    ② 组件的字段全在桩里（桩更全，例 UnitPrefabTurretInfo 桩多了 ShellSpawn）；
    ③ 桩是空类时按 :data:`KNOWN_SIGNATURES` 的实测签名点名。
    """
    exact = [s for s in cat.stubs() if s['fields'] and s['fields'] <= fields and s['class']]
    if exact:
        return exact
    wider = [s for s in cat.stubs()
             if s['fields'] and fields and fields <= s['fields'] and s['class']]
    if wider:
        return wider
    # ③ 空桩兜底：签名的**全部**字段都在组件里，且只命中一个类名
    named = [cls for sig, cls in KNOWN_SIGNATURES.items() if sig <= fields]
    if len(named) == 1:
        hit = cat.by_class().get(named[0])
        return [hit] if hit else []
    # ④ 空字段组件：真件类名**拿不到**（prefab 里没有、`m_EditorClassIdentifier` 也是空的）。
    #    只能挂一个不参与任何查找的通用桩 —— ⛔ 不是「猜类名」，是「类名确实不存在于导出物里」，
    #    报告里会把条数点出来（`generic_empty`），别让它悄悄过去。
    if not fields:
        hit = cat.by_class().get(GENERIC_EMPTY_STUB)
        if hit:
            return [hit]
    return []


def plan_prefab(*, project_root: str | Path, prefab: str | Path) -> dict[str, Any]:
    """只读：算出「哪个组件该改指哪个桩」，认不出的进 `unmatched`（fail-closed）。"""
    cat = load_catalog(project_root)
    path = Path(prefab)
    if not path.is_absolute():
        path = cat.root / path
    if not path.is_file():
        raise StubRepointError("prefab 不存在：" + str(path))
    text = _read_exact(path)
    components: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    drop: list[dict[str, Any]] = []
    already = 0
    for class_id, file_id, block in _blocks(text):
        if class_id != 114:
            continue
        m = _SCRIPT_RE.search(block)
        if not m:
            continue
        script_fid, guid, _typ = m.group(1), m.group(2), m.group(3)
        if cat.is_stub_guid(guid):
            # ★ 2026-10-29：**已经挂着通用桩的历史组件**也要清掉 —— 幂等地把上一版留下的
            #   `UnknownEmptyBehaviour` 删干净（不清的话，改完之后那个假类还在包里）。
            generic = cat.by_class().get(GENERIC_EMPTY_STUB)
            if generic is not None and guid == generic['guid']:
                drop.append({
                    'file_id': file_id,
                    'owner_file_id': _owner_of(block),
                    'script_file_id': script_fid,
                    'script_guid': guid,
                    'dll': '',
                    'fields': [],
                    'drop_reason': 'was_generic_stub',
                })
                continue
            already += 1
            continue
        if not cat.is_moved_dll_guid(guid):
            if guid in project_script_guids(cat.root):
                continue                  # 指向工程里真实存在的脚本，不动
            # ★ 2026-10-26：既不是桩、也不是被移走的 DLL、工程里又**没有**这个脚本 ⇒ 真件引用了一条
            #   导出物里根本不存在的脚本 ⇒ Unity 存 prefab 时直接拒 ⇒ 只能挂通用桩。
            # ★ 2026-10-29：**不再挂通用桩**（`BrokenArrow.Stubs.UnknownEmptyBehaviour` 游戏里不存在，
            #   真机日志会刷 `referenced script ... is missing!`）。改成**把这条组件删掉**：
            #   语义上等于「原本就不跑」，但包里不再有一个假类。
            item = {
                'file_id': file_id,
                'owner_file_id': _owner_of(block),
                'script_file_id': script_fid,
                'script_guid': guid,
                'dll': '',
                'fields': [],
                'missing_script': True,
                'drop_reason': 'script_missing_in_export',
            }
            drop.append(item)
            continue
        fields = _component_fields(block)
        hits = _match_stub(cat, fields)
        item = {
            'file_id': file_id,
            'script_file_id': script_fid,
            'script_guid': guid,
            'dll': cat.moved_dlls.get(guid, ''),
            'fields': sorted(fields),
        }
        if len(hits) == 1 and hits[0]['class'] == GENERIC_EMPTY_STUB:
            # 空字段 + 认不出类名 ⇒ 同样是假类，**删组件**而不是挂通用桩。
            item['owner_file_id'] = _owner_of(block)
            item['drop_reason'] = 'no_fields_generic'
            drop.append(item)
        elif len(hits) == 1:
            item['stub_class'] = hits[0]['class']
            item['stub_guid'] = hits[0]['guid']
            components.append(item)
        else:
            # ★ 2026-10-26：桩匹不上时，先看**工程里有没有同一形状的真脚本**（HDRP 那类包组件）。
            real = match_real_script(cat.root, fields)
            if real is not None:
                item['stub_class'] = real['class']
                item['stub_guid'] = real['guid']
                item['real_script'] = True
                components.append(item)
            else:
                item['candidates'] = [h['class'] for h in hits]
                unmatched.append(item)
    return {
        'prefab': str(path),
        'components': components,
        'unmatched': unmatched,
        'drop': drop,
        'dropped': len(drop),
        'generic_empty': sum(1 for c in components if c.get('generic_empty')),
        'missing_script': sum(1 for c in components if c.get('missing_script')),
        'real_script': sum(1 for c in components if c.get('real_script')),
        'already_stubbed': already,
    }


def apply_plan(plan: dict[str, Any], *, backup_root: str | Path | None = None,
               backup_dir: str | Path | None = None) -> dict[str, Any]:
    """按 plan 改 `m_Script` 那一行（⛔ 不动别的字节），并在改前拷一份原件。

    `backup_dir` 给了就用它（:func:`apply_tree` 一份计划改多个 prefab 时共用一个时间戳目录）；
    没给就按 `backup_root`（默认 `_work/stub_repoint`）＋时间戳开一个。
    """
    path = Path(plan['prefab'])
    original = _read_exact(path)
    if plan.get('unmatched'):
        names = '、'.join(sorted(
            u['dll'] + ':' + '/'.join(u['candidates'] or ['?'])
            for u in plan['unmatched']))
        raise StubRepointError(
            '有组件认不出该指哪个桩，拒绝半改（会留下半套）：'
            + str(len(plan['unmatched'])) + ' 个',
            hint='先补桩类（字段集要覆盖）再重跑；现场：' + names,
        )
    rewritten = 0
    for item in plan['components']:
        marker = '--- !u!114 &' + str(item['file_id'])
        idx = original.find(marker)
        if idx < 0:
            raise StubRepointError('块找不到（文件在计划之后被改过？）：' + marker)
        seg_end = original.find('\n--- !u!', idx)
        seg_end = len(original) if seg_end < 0 else seg_end
        seg = original[idx:seg_end]
        old_line = ('m_Script: {fileID: ' + item['script_file_id']
                    + ', guid: ' + item['script_guid'] + ', type: 3}')
        if old_line not in seg:
            raise StubRepointError('这一行与计划不一致（⛔ 不按相近改）：' + old_line)
        new_line = ('m_Script: {fileID: ' + str(CS_SCRIPT_FILE_ID)
                    + ', guid: ' + item['stub_guid'] + ', type: 3}')
        seg_new = seg.replace(old_line, new_line, 1)
        original = original[:idx] + seg_new + original[seg_end:]
        rewritten += 1
    # ★ 2026-10-29：`drop` 里的组件**整条删掉**（块 ＋ 挂在 GameObject `m_Component` 里的那行引用），
    #   不给它们挂通用桩 —— 通用桩在游戏里根本不存在，只会刷「referenced script ... is missing!」。
    dropped = 0
    for item in plan.get('drop') or []:
        fid = item['file_id']
        marker = '--- !u!114 &' + str(fid)
        idx = original.find(marker)
        if idx < 0:
            raise StubRepointError('要删的组件块找不到（文件在计划之后被改过？）：' + marker)
        seg_end = original.find('\n--- !u!', idx)
        seg_end = len(original) if seg_end < 0 else seg_end
        block = original[idx:seg_end]
        owner = item.get('owner_file_id') or _owner_of(block)
        original = original[:idx] + (original[seg_end + 1:] if seg_end < len(original) else '')
        gmarker = '--- !u!1 &' + str(owner)
        gidx = original.find(gmarker)
        if gidx < 0:
            raise StubRepointError('组件挂的 GameObject 找不到：' + gmarker)
        gend = original.find('\n--- !u!', gidx)
        gend = len(original) if gend < 0 else gend
        gseg = original[gidx:gend]
        ref = '- component: {fileID: ' + str(fid) + '}'
        if ref not in gseg:
            raise StubRepointError('GameObject 的 m_Component 里没有这条引用：' + ref)
        gseg_new = re.sub(r'[ \t]*' + re.escape(ref) + r'\n', '', gseg, count=1)
        original = original[:gidx] + gseg_new + original[gend:]
        dropped += 1
    if rewritten or dropped:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        repo = Path(__file__).resolve().parents[4]
        base = Path(backup_root) if backup_root else (WORK / 'stub_repoint')
        dest = Path(backup_dir) if backup_dir else (base / stamp)
        dest.mkdir(parents=True, exist_ok=True)
        backup = dest / path.name
        # 一批里可能有两个同名 prefab（不同目录）⇒ 加后缀，⛔ 不覆盖上一份备份
        suffix = 1
        while backup.exists():
            backup = dest / (path.stem + '~' + str(suffix) + path.suffix)
            suffix += 1
        shutil.copy2(path, backup)
        _write_exact(path, original)
        (dest / (backup.name + ".plan.json")).write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf-8')
    else:
        dest = None
    return {
        'rewritten': rewritten,
        'dropped': dropped,
        'prefab': str(path),
        'backup_dir': str(dest) if dest else '',
        'bytes': path.stat().st_size,
    }


# --------------------------------------------------------------------------- #
# 嵌套 prefab 依赖（2026-10-27）
#
# 为什么要有这一段（A 级现场，都是量出来的）：
#   · `US_M113A3.prefab` 里有一条 `_newModel: {fileID: …,
#     guid: c2598f5d30b4fd04ba6e7af65817ac61, type: 2}`，
#     那个 guid 在 .meta 索引里是 `Assets/GameObject/Destroyed_M113.prefab`；
#   · 那个 prefab 的 `Decal D_2` 组件，`m_Script` 仍写着被移走 DLL 的占位 guid
#     `48132c8dbc97f956e52e75e839ab46f9`（AssetRipper 导出物）
#     ⇒ 打进包 = 「m_Script 指向包外」（check-model A0）；
#   · M1A2C 的销毁件**不叫** Destroyed_M1A2C —— 是 `Assets/GameObject/Destroyed_Abrams.prefab`
#     （guid 05a687f40a6eb65438eb69ce812b2f5d）⇒ 只能按 guid 反查，⛔ 不按名字猜。
#
# 认依赖的办法：把 prefab 文本里**所有** `guid: <32hex>` 拿去查「工程里 .prefab 资产的 guid 索引」，
#   命中的就是依赖件。这样 `m_SourcePrefab`（type: 3）与普通 PPtr（type: 2）两种写法都覆盖，
#   ⛔ 也不会把材质 / 贴图 / 脚本的 guid 当成 prefab（它们不在这个索引里）。
# --------------------------------------------------------------------------- #

#: 工程里 prefab 资产 guid 索引缓存（project_root -> {guid: {"prefab", "rel"}}）
_PROJECT_PREFABS: dict[str, dict[str, dict[str, Any]]] = {}


def project_prefab_index(project_root: str | Path) -> dict[str, dict[str, Any]]:
    """工程里 `.prefab` 资产的 guid 索引：`{guid: {"prefab": 绝对路径, "rel": 资产路径}}`。

    只扫 `Assets/` 与 `Packages/`（游戏资产都在 Assets；`Library/PackageCache` 是包源码，
    没有要跟的模型 prefab）。按工程根缓存 —— 一次计划只扫一次。
    """
    key = str(Path(project_root))
    cached = _PROJECT_PREFABS.get(key)
    if cached is not None:
        return cached
    root = Path(project_root)
    index: dict[str, dict[str, Any]] = {}
    for rel_root in ('Assets', 'Packages'):
        base = root / rel_root
        if not base.is_dir():
            continue
        for meta in base.rglob('*.prefab.meta'):
            guid = _guid_of(meta)
            if not guid or guid in index:
                continue
            prefab = meta.with_suffix('')          # foo.prefab.meta -> foo.prefab
            if not prefab.is_file():
                continue
            index[guid] = {'prefab': str(prefab.resolve()),
                           'rel': prefab.relative_to(root).as_posix()}
    _PROJECT_PREFABS[key] = index
    return index


def prefab_references(project_root: str | Path,
                      prefab: str | Path) -> list[dict[str, str]]:
    """这个 prefab 引用到的**工程内 prefab**（按出现顺序去重，跳过自己）。

    ⛔ 不看字段名（`m_SourcePrefab` / `_newModel` / `PrefabBody` …）猜 —— 只按 guid 查索引。
    """
    index = project_prefab_index(project_root)
    path = Path(prefab)
    text = _read_exact(path)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for guid in _GUID_REF_RE.findall(text):
        if guid in seen:
            continue
        seen.add(guid)
        hit = index.get(guid)
        if hit is None:
            continue
        if Path(hit['prefab']).resolve() == path.resolve():
            continue
        out.append({'guid': guid, 'prefab': hit['prefab'], 'rel': hit['rel']})
    return out


def discover_prefab_dependencies(
    *,
    project_root: str | Path,
    prefab: str | Path,
    max_depth: int = MAX_DEP_DEPTH,
    max_prefabs: int = MAX_DEP_PREFABS,
) -> dict[str, Any]:
    """广度优先跟出**嵌套 prefab 依赖**（递归 · 防环 · 有上限）。

    返回 ``{"main", "dependencies": [{guid, prefab, rel, depth, via, via_rel}],
    "visited", "truncated", "cycles"}``。
    · 防环：按解析后的绝对路径去重（回边记进 `cycles`，不再展开）；
    · 上限：深度 `max_depth`、件数 `max_prefabs`；撞上就 `truncated=True`（⛔ 不静默丢）；
    · `Assets/_unitbuild/…` 是每次构建都重建的产物目录 ⇒ 跳过，免得把产物当依赖。
    """
    root = Path(project_root)
    main = Path(prefab)
    if not main.is_absolute():
        candidate = root / main
        # 传进来的可以是「相对工程根」（主用法，例 Assets/Main.prefab），
        # 也可以是「相对当前目录」且已经带上了工程根前缀 —— 两种都认。
        main = candidate if candidate.is_file() else main
    if not main.is_file():
        raise StubRepointError("prefab 不存在：" + str(main))
    main = main.resolve()          # 计划里的路径一律绝对 ⇒ 下游 plan_prefab 不再二次拼工程根
    try:
        main_rel = main.relative_to(root.resolve()).as_posix()
    except ValueError:
        main_rel = main.as_posix()
    queue: list[tuple[Path, int]] = [(main, 0)]
    seen: set[Path] = {main.resolve()}
    rows: list[dict[str, Any]] = []
    cycles: list[str] = []
    truncated = False
    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        try:
            current_rel = current.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            current_rel = current.as_posix()
        for ref in prefab_references(root, current):
            if ref['rel'].startswith(GENERATED_DIR + '/'):
                continue
            resolved = Path(ref['prefab']).resolve()
            if resolved in seen:
                if resolved != Path(current).resolve():
                    cycles.append(ref['rel'])
                continue
            if len(rows) >= max_prefabs:
                truncated = True
                continue
            seen.add(resolved)
            rows.append({'guid': ref['guid'], 'prefab': ref['prefab'], 'rel': ref['rel'],
                         'depth': depth + 1, 'via': str(current), 'via_rel': current_rel})
            queue.append((Path(ref['prefab']), depth + 1))
    return {
        'main': str(main),
        'main_rel': main_rel,
        'dependencies': rows,
        'visited': len(seen),
        'truncated': truncated,
        'cycles': sorted(set(cycles)),
    }


def plan_prefab_tree(
    *,
    project_root: str | Path,
    prefab: str | Path,
    follow_deps: bool = True,
    max_depth: int = MAX_DEP_DEPTH,
    max_prefabs: int = MAX_DEP_PREFABS,
) -> dict[str, Any]:
    """主件 + 嵌套依赖件一起算「哪个组件该改指哪个桩」；**只读**。

    与 :func:`plan_prefab` 的区别：跟随主件引用到的工程内 prefab（递归 · 防环 · 有上限），
    并把**主件 / 依赖件分开**报（`prefabs[0]` 是主件）。老字段（`components` / `unmatched` /
    `already_stubbed` …）里 `components` 仍是**主件**那一条计划（老调用方不用改），
    `unmatched` 则是**合并**过的（每条带 `kind` / `rel`，看得出是哪个 prefab）。

    ⛔ 主件有认不出的组件 ⇒ :func:`apply_tree` 整次拒绝；依赖件有认不出的 ⇒ 只报不改
    （那个 prefab 本来就没被改过，保持现状不会比现在更坏 —— 但绝不半改它）。
    """
    root = Path(project_root)
    if follow_deps:
        found = discover_prefab_dependencies(
            project_root=root, prefab=prefab,
            max_depth=max_depth, max_prefabs=max_prefabs)
    else:
        main_path = Path(prefab)
        if not main_path.is_absolute():
            candidate = root / main_path
            main_path = candidate if candidate.is_file() else main_path
        main_path = main_path.resolve()
        found = {'main': str(main_path), 'main_rel': '', 'dependencies': [],
                 'visited': 1, 'truncated': False, 'cycles': []}
    main_plan = plan_prefab(project_root=root, prefab=found['main'])
    items: list[dict[str, Any]] = [dict(main_plan, kind='main', depth=0,
                                        rel=found.get('main_rel', ''))]
    for dep in found['dependencies']:
        sub = plan_prefab(project_root=root, prefab=dep['prefab'])
        items.append(dict(sub, kind='dependency', depth=dep['depth'], guid=dep['guid'],
                          via=dep['via'], via_rel=dep['via_rel'], rel=dep['rel']))
    merged: list[dict[str, Any]] = []
    for item in items:
        for miss in item['unmatched']:
            merged.append(dict(miss, prefab=item['prefab'], kind=item['kind'],
                               rel=item.get('rel', '')))
    totals = {name: sum(int(item.get(name) or 0) for item in items)
              for name in ('already_stubbed', 'real_script', 'missing_script', 'generic_empty')}
    plan = dict(main_plan)
    plan.update({
        'kind': 'main',
        'follow_deps': bool(follow_deps),
        'main_unmatched': len(main_plan['unmatched']),
        'prefabs': items,
        'dependencies': items[1:],
        'unmatched': merged,
        'already_stubbed': totals['already_stubbed'],
        'real_script': totals['real_script'],
        'missing_script': totals['missing_script'],
        'generic_empty': totals['generic_empty'],
        'to_change': sum(len(item['components']) for item in items),
        'to_drop': sum(len(item.get('drop') or []) for item in items),
        'changed_prefabs': sum(1 for item in items if item['components'] or item.get('drop')),
        'visited': found['visited'],
        'truncated': found['truncated'],
        'cycles': found['cycles'],
        'project_root': str(root),
    })
    return plan


def apply_tree(plan: dict[str, Any], *,
               backup_root: str | Path | None = None) -> dict[str, Any]:
    """按 :func:`plan_prefab_tree` 的结果改盘：主件 + 依赖件一起（**共用一个**备份目录）。

    ⛔ 主件认不出 ⇒ 拒绝（不半改）；依赖件认不出 ⇒ 那**一个 prefab 整份**跳过并记进 `skipped`
    （它原本就没被改过）。每个 prefab 仍走 :func:`apply_plan`（只改 `m_Script` 那一行）。
    """
    items = plan.get('prefabs')
    if not items:
        raise StubRepointError(
            '这不是 plan_prefab_tree 的结果（没有 prefabs 段）—— 单件计划请用 apply_plan',
        )
    main = items[0]
    if main.get('unmatched'):
        names = '、'.join(sorted(
            str(u.get('dll')) + ':' + '/'.join(u.get('candidates') or ['?'])
            for u in main['unmatched']))
        raise StubRepointError(
            '有组件认不出该指哪个桩，拒绝半改（会留下半套）：'
            + str(len(main['unmatched'])) + ' 个',
            hint='先补桩类（字段集要覆盖）再重跑；现场：' + names,
        )
    dest: Path | None = None
    rewritten = 0
    done: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    dropped = 0
    for item in items:
        if not item.get('components') and not item.get('drop'):
            continue
        if item.get('unmatched'):
            skipped.append({'prefab': item['prefab'], 'kind': item.get('kind', ''),
                            'rel': item.get('rel', ''),
                            'unmatched': len(item['unmatched'])})
            continue
        res = apply_plan(item, backup_root=backup_root, backup_dir=dest)
        if dest is None and res.get('backup_dir'):
            dest = Path(res['backup_dir'])
        rewritten += int(res['rewritten'])
        dropped += int(res.get('dropped') or 0)
        done.append({'prefab': res['prefab'], 'kind': item.get('kind', ''),
                     'rewritten': int(res['rewritten']),
                     'dropped': int(res.get('dropped') or 0)})
    return {
        'prefab': plan.get('prefab', ''),
        'rewritten': rewritten,
        'dropped': dropped,
        'prefabs': done,
        'skipped': skipped,
        'backup_dir': str(dest) if dest else '',
        'to_change': int(plan.get('to_change') or 0),
    }

