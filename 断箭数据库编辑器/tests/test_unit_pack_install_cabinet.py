r'''`pack install` / `pack restore` 的**补丁柜账** ＋ 三条装机路径**同一落点**（块 B）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_unit_pack_install_cabinet.py -q -n 0`
→ 通过用例数 **从 3（块 B 之前）变 7 passed**（新增的 4 条是真路径用例，读数见汇报）。

被守的**两件**事：

① **补丁柜的账**（P0，2026-10-22 起）：`install_pack` 只转调 `newasset add-model`，后者按
   「装前 -> 装后」把 catalog / catalog.hash / 新包记进柜子；`pack install` 收尾**读回**柜子核对
   「装后 == 盘上」。修前那个现场（柜里记的是 repoint **之前**的字节 ⇒ `pack restore` 报
   「与补丁的『装后』不符」）由 `test_restore_refuses_when_the_cabinet_holds_a_stale_after_value`
   这条负控守着（它守的是「柜里的账 == 盘上」这条不变式，与 repoint 在不在无关）。

② ★ **落点只有一处**（2026-10-25 块 B）：`new_asset.install_landing()` = `<游戏根>\\BA_Mod\\`，
   catalog 那条包地址与它**同一处**（`…\\..\\..\\..\\BA_Mod\\<包>`）。三条真入口
   （`newasset add-model` / `newasset add-bundle` / `pack install`）在**各自一份**假游戏根上装
   同一份包 ⇒ catalog 里那条 internal_id **逐字相同**；装完再 `newasset restore` ⇒
   `BA_Mod\\` 与 `aa\\PC` **零残留**、整棵树 sha256 逐条回到装前。
   ⛔ 这一组**不用替身 CLI**：走真 `run_add` / 真 `run_add_bundle` / 真 `install_pack`。

⚠ 真路径用例要起**子进程**（`install_pack` -> CLI）。子进程看不到父进程 monkeypatch 的
`dllpatch.BUILDS` ⇒ 用例把「这份合成 DLL 就当原版」写进一个临时 `sitecustomize.py`
（`PYTHONPATH` ＋ `BA_MEASURE_FAKE_DLL_SHA` 传下去）。它**只**替 `dllpatch.status()` 认版本，
路径本身（真子进程、真 CLI、真写盘）一点没绕。同理那个垫片顺手把 `os.mkdir(0o700)` 折成
0o755 —— 本沙箱里 0o700 建出来的目录写不进去（`_work/patch_A/sitecustomize.py` 同一口径）。
'''
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from _fake_dll import fake_dll_bytes  # tests/ 由 pytest 插进 sys.path
from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.asset_kernel import reverse_delta as rd
from ba_mod_maker.core.asset import dbzip, dllpatch, external_db, new_asset
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.assets import unit_pack
from ba_mod_maker.core.errors import AssetError
from test_dllpatch import _pretend_original

#: 真件 catalog 的 internalId 前缀（真件形状：`{…RuntimePath}\PC\<包>`）。
RUNTIME = '{UnityEngine.AddressableAssets.Addressables.RuntimePath}'
#: 这一单要**登记进去**的新包（真件形状：`_all_` 后 32 位十六进制）。
BUNDLE = 'p0test_assets_all_' + 'a' * 32 + '.bundle'
#: `newasset add-bundle` 那一档用的包名（每个假游戏根里只装一份，别撞名）。
BUNDLE_ADD = 'blockbadd_assets_all_' + 'b' * 32 + '.bundle'
#: 已经在 catalog 里的老包键（真件形状：`{…RuntimePath}\PC\<包>`）。
BASE_BUNDLE = 'base_assets_all_' + 'b' * 32 + '.bundle'
BASE_KEY = 'BASE_TEST-Unit'
NEW_KEY = 'P0TEST'
UNIT = 'A2BR8'
ASSET_PATH = 'Assets/Resources_moved/BAMod/RU_T72B_Base.prefab'
BUNDLE_PROVIDER = 'UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider'
BUNDLED_PROVIDER = 'UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider'
UNITY_CORE = 'UnityEngine.CoreModule'
#: 真件模型地址那条 entry[3] 的**合成**值。
DEP_HASH = 999


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_entry(bundle: str, *, rank: int, crc: int, mod: bool = False) -> cbw.BinEntry:
    '''一条包条目。`mod=True` 表示**我们装的**那种形状（internalId 指 `BA_Mod\\`）。'''
    prefix = (RUNTIME + new_asset.MOD_SEGMENT) if mod else (RUNTIME + new_asset.PC_SEGMENT)
    return cbw.BinEntry(
        loc_offset=-1,
        primary_key=bundle,
        internal_id=prefix + bundle,
        provider=BUNDLE_PROVIDER,
        dep_ranks=(),
        dep_hash=0,
        extra=cbw.AssetBundleOptions(
            hash_bytes=bytes(range(16)),
            bundle_name=bundle[: -len('.bundle')],
            crc=crc,
            bundle_size=1000 + crc,
            common_raw=bytes.fromhex('0000200004000000'),
            type_asm=UNITY_CORE,
            type_cls='UnityEngine.AssetBundleRequestOptions',
        ),
        type_asm=UNITY_CORE,
        type_cls='UnityEngine.AssetBundle',
        rank=rank,
    )


def _synth_catalog() -> bytes:
    '''2 条目 / 2 键的二进制 catalog：一条老包 ＋ 一条模型地址（形状抄真件）。'''
    model = cbw.BinModel(
        provider_id='AddressablesMainContentCatalog',
        build_result_hash='0' * 32,
    )
    model.entries = [
        _bundle_entry(BASE_BUNDLE, rank=0, crc=111),
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=BASE_KEY,
            internal_id='Assets/Synth/Shared.prefab',
            provider=BUNDLED_PROVIDER,
            dep_ranks=(0,),
            dep_hash=DEP_HASH,
            type_asm=UNITY_CORE,
            type_cls='UnityEngine.GameObject',
            rank=1,
        ),
    ]
    model.keys = [
        cbw.BinKey(name=BASE_BUNDLE, ranks=(0,), type_asm='mscorlib', type_cls='System.String'),
        cbw.BinKey(name=BASE_KEY, ranks=(1,), type_asm='mscorlib', type_cls='System.String'),
    ]
    raw = cbw.serialize(model)
    # 夹具自证：真是一份能读回来的二进制 catalog（不然下面全是假绿）
    assert cbw.looks_like_catalog_bin(raw)
    return raw


def _catalog_with_new_bundle(raw: bytes, bundle: str) -> bytes:
    '''替身模拟 `newasset add-model` 那一步：**加**一条包条目 ＋ 一条包键（落点 = BA_Mod）。'''
    model = cbw.read_model(raw)
    rank = len(model.entries)
    model.entries.append(_bundle_entry(bundle, rank=rank, crc=222, mod=True))
    model.keys.append(
        cbw.BinKey(name=bundle, ranks=(rank,), type_asm='mscorlib', type_cls='System.String')
    )
    return cbw.serialize(model)


def _units_payload() -> bytes:
    rows = [{'Id': 9, 'Name': 'u', 'ModelFileName': BASE_KEY, 'ThumbnailFileName': 'ONE-Label'}]
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _make_fixture(tmp_path: Path, *, with_dll: bool = False, real_src: bool = False):
    '''假游戏根（catalog.bin ＋ catalog.hash ＋ aa/PC）＋ 工程目录 ＋ 待装的源包。

    `real_src=True` 时源包是**真的 UnityFS 文件**（真路径用例必需：`plan_add_model` 要读容器
    算 crc / bundle_size）；替身 CLI 那三条只测柜子，假字节就够。
    '''
    root = tmp_path / 'game'
    aa = root / 'BrokenArrow_Data' / 'StreamingAssets' / 'aa'
    (aa / 'PC').mkdir(parents=True)
    raw = _synth_catalog()
    (aa / 'catalog.bin').write_bytes(raw)
    (aa / 'catalog.hash').write_bytes(cbw.hash128_hex(raw).encode('ascii'))
    if real_src:
        src = _source_bundle(tmp_path / 'src', BUNDLE)
    else:
        src = tmp_path / 'src' / BUNDLE
        src.parent.mkdir(parents=True)
        src.write_bytes(b'FAKE-BUNDLE-' + bytes(range(64)))
    project = tmp_path / 'project'
    project.mkdir()
    if with_dll:
        external_db.ensure_zip_dir(root)
        dbzip.DbZip(tables={'Units': _units_payload(), 'Armors': b'[]'}).write(
            external_db.game_zip_path(root)
        )
        original = fake_dll_bytes()
        (root / dllpatch.DLL_NAME).write_bytes(original)
        (root / dllpatch.BACKUP_NAME).write_bytes(original)
    return root, project, src


def _source_bundle(tmp_path: Path, name: str) -> Path:
    '''一个**真的 UnityFS 文件**（元数据区 0 类型 / 0 对象 —— 本档验的是落点与账，不是造包）。'''
    meta = mw.SerializedMetadata(
        header=mw.SerializedFileHeader(0, 0, 22, 0, 0, bytes(3), 0, 0, 0, 0),
        unity_version='2022.3.62f3',
        target_platform=19,
        enable_type_tree=False,
        types=[],
        objects=[],
        script_types=[],
        externals=[],
        ref_types=[],
        user_information='',
    )
    container = builder.build_unityfs(
        mw.build_metadata(meta), builder.node_path_for(name), block_type=builder.BLOCK_NONE
    )
    path = tmp_path / 'bundles' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(container.data)
    return path


def _setup_child_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original: bytes) -> None:
    '''让**子进程**（`install_pack` -> CLI）也把这份合成 DLL 当原版。

    子进程看不到父进程 monkeypatch 的 `dllpatch.BUILDS` ⇒ 写一个临时 `sitecustomize.py`
    挂到 `PYTHONPATH` 上，用环境变量把 sha 传下去。⛔ 它只替 `dllpatch.status()` 认版本，
    真实路径（子进程 / CLI / 写盘 / 补丁柜）一点没绕。
    '''
    shim = tmp_path / 'childshim'
    shim.mkdir()
    (shim / 'sitecustomize.py').write_text(
        'import os\n'
        'from dataclasses import replace\n'
        '_orig_mkdir = os.mkdir\n'
        'def _mkdir(path, mode=0o777, *a, **k):\n'
        '    return _orig_mkdir(path, 0o755 if mode == 0o700 else mode, *a, **k)\n'
        'os.mkdir = _mkdir\n'
        '_sha = os.environ.get(\'BA_MEASURE_FAKE_DLL_SHA\')\n'
        'if _sha:\n'
        '    from ba_mod_maker.core.asset import dllpatch\n'
        '    dllpatch.BUILDS = (replace(dllpatch.BUILD_120, orig_sha256=_sha),)\n',
        encoding='utf-8',
    )
    parts = [str(shim)]
    extra_dirs = (
        str(Path(new_asset.__file__).resolve().parent.parent),
        os.environ.get('PYTHONPATH', ''),
    )
    for extra in extra_dirs:
        if extra:
            parts.append(extra)
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join(parts))
    monkeypatch.setenv('BA_MEASURE_FAKE_DLL_SHA', hashlib.sha256(original).hexdigest())


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob('*'))
        if path.is_file()
    }


def _registered_bundle_internal_id(raw: bytes, bundle: str) -> str:
    for entry in cbw.read_model(raw).entries:
        if (entry.internal_id or '').endswith(bundle):
            return str(entry.internal_id)
    raise AssertionError('catalog 里没有这个包的条目：' + bundle)


# --------------------------------------------------------------------------- #
# ① 补丁柜的账（替身 CLI，只测 install_pack / restore_pack 自己的那一层）
# --------------------------------------------------------------------------- #
def _stub_cli(root: Path, *, state: dict):
    '''替身 CLI：照 `run_add` 的**顺序**记柜再写盘（落点 = `BA_Mod\\`，新口径）。'''

    def run(project: Path, args) -> str:
        catalog = root / unit_pack.CATALOG
        hash_path = root / unit_pack.CATALOG_HASH
        store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
        if args[1] == 'restore':
            store.restore()
            return 'stub: restore'
        assert args[1] == 'add-model', args
        named = Path(args[args.index('--bundle') + 1]).name
        landing = root / unit_pack.MOD_DIR / named
        raw_before = catalog.read_bytes()
        hash_before = hash_path.read_bytes()
        raw_after = _catalog_with_new_bundle(raw_before, named)
        hash_after = cbw.hash128_hex(raw_after).encode('ascii')
        data = Path(args[args.index('--bundle') + 1]).read_bytes()
        recorder = store.begin('资产新增 ' + NEW_KEY + '（' + UNIT + '）')
        recorder.capture_file(catalog, before=raw_before, after=raw_after)
        recorder.capture_file(hash_path, before=hash_before, after=hash_after)
        recorder.capture_file(landing, before=None, after=data)
        recorder.commit()
        catalog.write_bytes(raw_after)
        hash_path.write_bytes(hash_after)
        landing.parent.mkdir(parents=True, exist_ok=True)
        landing.write_bytes(data)
        manifest = project / 'patches' / 'new_asset.json'
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({'bundle': str(landing)}), encoding='utf-8')
        return 'stub: add-model'

    return run


def test_install_pack_hands_an_absolute_bundle_path_to_the_cli(tmp_path, monkeypatch):
    '''`--bundle` 必须**绝对**：`_run_cli` 的 cwd 是 project，相对串会被按新 cwd 再解析一次。

    2026-10-26 端到端实测的原话：「找不到要新增的包：_work\\unitybuild\\out-gui\\…bundle」
    —— 那条路径在 repo 下是对的，在 project 下解析就找不到。
    '''
    root = tmp_path / 'game'
    root.mkdir()
    project = tmp_path / 'proj'
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    src = out_dir / ('x_assets_all_' + 'a' * 32 + '.bundle')
    src.write_bytes(b'B')
    seen: dict = {}

    def fake_run(project_arg, args):
        seen['project'] = project_arg
        seen['args'] = list(args)
        return 'stub: add-model'

    monkeypatch.setattr(unit_pack, '_run_cli', fake_run)
    monkeypatch.setattr(unit_pack, '_check_cabinet_matches_disk', lambda **kw: {})
    monkeypatch.setattr(unit_pack, '_catalog_bundle_internal_id', lambda *a, **k: 'id')
    monkeypatch.chdir(tmp_path)
    unit_pack.install_pack(
        game=root, project_root=project, bundle=Path('out') / src.name,
        unit=UNIT, key=NEW_KEY, asset_path=ASSET_PATH, dependency_source=BASE_KEY, row_id=9,
    )
    args = seen['args']
    handed = Path(args[args.index('--bundle') + 1])
    assert handed.is_absolute(), handed
    assert handed.resolve() == src.resolve(), handed

def _target_of(store: rd.PatchStore, path: Path):
    pending = store.pending()
    assert pending is not None, '补丁柜里没有这一单'
    for target in pending.targets:
        if Path(target.path) == path:
            return target
    raise AssertionError('柜里没有这条目标：' + str(path))


def _install(root: Path, project: Path, src: Path) -> dict:
    return unit_pack.install_pack(
        game=root, project_root=project, bundle=src, unit=UNIT, key=NEW_KEY,
        asset_path=ASSET_PATH, dependency_source=BASE_KEY, row_id=9,
    )


def test_cabinet_after_matches_disk_and_restore_gives_the_bytes_back(tmp_path, monkeypatch):
    '''★ 两条一起守：① 柜里的「装后」== 盘上；② restore 逐字节回到装前且包被删掉。'''
    root, project, src = _make_fixture(tmp_path)
    catalog = root / unit_pack.CATALOG
    hash_path = root / unit_pack.CATALOG_HASH
    before_raw, before_hash = catalog.read_bytes(), hash_path.read_bytes()
    monkeypatch.setattr(unit_pack, '_run_cli', _stub_cli(root, state={}))

    res = _install(root, project, src)

    # —— 正控（尺子能红）：catalog 真的变了，且新包那条指到 `BA_Mod\`（替身照新 kernel 写）
    after_raw = catalog.read_bytes()
    assert after_raw != before_raw, 'catalog 没变 —— 这个夹具测不了柜子的账'
    assert res['internal_id'].endswith(new_asset.MOD_SEGMENT + BUNDLE)
    assert Path(res['bundle']) == root / unit_pack.MOD_DIR / BUNDLE
    assert (root / unit_pack.MOD_DIR / BUNDLE).is_file()

    # —— 柜里记的「装后」必须 == 盘上（修前红在这里）
    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    assert _target_of(store, catalog).sha_after == _sha(catalog), (
        '柜里的 catalog「装后」与盘上不符 —— pack restore 会报「与补丁的装后不符」'
    )
    assert _target_of(store, hash_path).sha_after == _sha(hash_path), (
        '柜里的 catalog.hash「装后」与盘上不符'
    )
    assert _target_of(store, catalog).sha_before == hashlib.sha256(before_raw).hexdigest()
    assert _target_of(store, hash_path).sha_before == hashlib.sha256(before_hash).hexdigest()

    # —— 还原：catalog / catalog.hash 逐字节回到装前，包删掉，柜子清空
    unit_pack.restore_pack(game=root, project_root=project)
    assert catalog.read_bytes() == before_raw
    assert hash_path.read_bytes() == before_hash
    assert not (root / unit_pack.MOD_DIR / BUNDLE).exists()
    assert store.pending() is None, '还原成功后柜子该清空'


def test_restore_refuses_when_the_cabinet_holds_a_stale_after_value(tmp_path, monkeypatch):
    '''负控：把柜里的「装后」改成一个**过期**值（= 修前那个现场）⇒ restore 必须点名拒绝。

    「柜里的账 == 盘上」这条不变式与 repoint 在不在无关：它守的是「不许按一条过期的账去还原」。
    '''
    root, project, src = _make_fixture(tmp_path)
    catalog = root / unit_pack.CATALOG
    monkeypatch.setattr(unit_pack, '_run_cli', _stub_cli(root, state={}))
    _install(root, project, src)

    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    pending = store.pending()
    assert pending is not None
    for target in pending.targets:
        if Path(target.path) == catalog:
            target.sha_after = '0' * 64
    pending.save(store.json_path, store.blob_path)

    with pytest.raises(AssetError) as info:
        unit_pack.restore_pack(game=root, project_root=project)
    assert '装后' in str(info.value)


def test_two_installs_share_one_cabinet_then_restore(tmp_path, monkeypatch):
    '''同一个工程目录连装两单（生产上就是这样攒补丁的）：第二单不许报「装后与磁盘不符」。'''
    root, project, src_one = _make_fixture(tmp_path)
    src_two = tmp_path / 'src' / ('p0test2_assets_all_' + 'c' * 32 + '.bundle')
    src_two.write_bytes(b'FAKE-BUNDLE-2-' + bytes(range(32)))
    catalog = root / unit_pack.CATALOG
    hash_path = root / unit_pack.CATALOG_HASH
    before_raw, before_hash = catalog.read_bytes(), hash_path.read_bytes()
    monkeypatch.setattr(unit_pack, '_run_cli', _stub_cli(root, state={}))

    _install(root, project, src_one)
    _install(root, project, src_two)

    store = rd.PatchStore(rd.patch_store_root(project), game_root=root)
    assert _target_of(store, catalog).sha_after == _sha(catalog)
    assert _target_of(store, hash_path).sha_after == _sha(hash_path)

    unit_pack.restore_pack(game=root, project_root=project)
    assert catalog.read_bytes() == before_raw
    assert hash_path.read_bytes() == before_hash
    assert store.pending() is None


# --------------------------------------------------------------------------- #
# ② 三条真路径：同一落点 ＋ restore 零残留（⛔ 不用替身 CLI）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fake_original(monkeypatch):
    '''本档的假游戏根拿 `fake_dll_bytes()` 当原版 DLL ⇒ 父进程里先让它对得上 `BUILDS`。'''
    data = fake_dll_bytes()
    _pretend_original(monkeypatch, data)
    return data


def _path_add_model(root: Path, project: Path, src: Path) -> None:
    plan = new_asset.plan_add_model(
        bundle=src, unit=BASE_KEY, key=NEW_KEY, row_id=9,
        game_root=root, project_root=project,
    )
    new_asset.run_add(plan, force_dll=True, check_game_running=False)


def _path_add_bundle(root: Path, project: Path, src: Path) -> None:
    plan = new_asset.plan_add_bundle(
        bundle=src, key=NEW_KEY, dependency_source=BASE_KEY,
        game_root=root, project_root=project,
    )
    new_asset.run_add_bundle(plan, check_game_running=False)


def _path_pack_install(root: Path, project: Path, src: Path) -> None:
    unit_pack.install_pack(
        game=root, project_root=project, bundle=src, unit=BASE_KEY, key=NEW_KEY,
        asset_path=ASSET_PATH, dependency_source=BASE_KEY, row_id=9,
    )


PATH_INSTALLERS = (
    ('add-model', _path_add_model),
    ('add-bundle', _path_add_bundle),
    ('pack-install', _path_pack_install),
)


def test_three_install_paths_write_the_same_internal_id(tmp_path, fake_original, monkeypatch):
    '''★★ 判据 1（块 B）：三条**真**入口装同一份包 ⇒ catalog 里那条 internal_id **逐字相同**。

    落点唯一规则 = `new_asset.install_landing()`（`<游戏根>\\BA_Mod\\`）。块 B 之前实测
    `THREE_WAY_SAME=0`（add-model / add-bundle 是 `…\\PC\\<包>`，pack install 被 repoint 成
    `…\\..\\..\\..\\BA_Mod\\<包>`）；改完是 1。
    '''
    ids: list[str] = []
    for label, install in PATH_INSTALLERS:
        case = tmp_path / label
        case.mkdir()
        root, project, src = _make_fixture(case, with_dll=True, real_src=True)
        if label == 'pack-install':
            _setup_child_env(case, monkeypatch, fake_original)
        install(root, project, src)
        bundle = src.name
        # 落点：包只在 BA_Mod\ 里，aa\PC 一份都不许有。
        assert (root / unit_pack.MOD_DIR / bundle).is_file(), label
        assert not (root / 'BrokenArrow_Data' / 'StreamingAssets' / 'aa' / 'PC' / bundle).exists()
        ids.append(_registered_bundle_internal_id((root / unit_pack.CATALOG).read_bytes(), bundle))
    assert len(set(ids)) == 1, '三条路写出的 internal_id 不是同一个：' + repr(ids)
    assert ids[0].endswith(new_asset.MOD_SEGMENT + BUNDLE)
    assert ids[0] == RUNTIME + new_asset.MOD_SEGMENT + BUNDLE


@pytest.mark.parametrize('label,install', PATH_INSTALLERS, ids=[p[0] for p in PATH_INSTALLERS])
def test_restore_leaves_no_residue_for_each_path(tmp_path, fake_original, monkeypatch, label,
                                                 install):
    '''★★ 判据 2（块 B）：每条真路径各装一次 -> `newasset restore` -> **零残留** ＋ 树回到装前。

    块 B 之前 `pack install` 那一档实测 `RESIDUE_PACK_INSTALL=1`（`BA_Mod\\` 里留一个孤儿包，
    直接 `newasset restore` 删不掉）；改完 0。
    '''
    case = tmp_path / label
    case.mkdir()
    root, project, src = _make_fixture(case, with_dll=True, real_src=True)
    if label == 'pack-install':
        _setup_child_env(case, monkeypatch, fake_original)
    before = _snapshot(root)
    install(root, project, src)
    assert (root / unit_pack.MOD_DIR / src.name).is_file()
    assert _snapshot(root) != before

    outcome = new_asset.restore(game_root=root, project_root=project, check_game_running=False)
    assert outcome.ok, outcome.lines()
    assert _snapshot(root) == before, '整棵树没回到装前'
    leftovers = [p for p in root.rglob('*') if p.is_file() and src.name in p.name]
    assert leftovers == [], 'restore 之后还有残留：' + repr(leftovers)
