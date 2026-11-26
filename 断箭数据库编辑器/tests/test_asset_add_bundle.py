r"""`newasset add-bundle`：只落包 ＋ 只登记一条地址（⛔ 不碰数据库 · ⛔ 不打 DLL）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests\test_asset_add_bundle.py -q -n 0`
→ 通过用例数 **从 0（本文件之前不存在）变 16 passed**（实跑读数，见汇报）。

⛔ 全部用例都在 `tmp_path` 造的**假游戏根**上跑 —— 一个字节都不写 `D:\Steam\...`。

| 用例组 | 判据 |
|---|---|
| 登记形状 | 计数头 (2,2,2) -> (4,4,4)；地址只有 1 条 entry、provider 以 BundledAssetProvider 收尾 |
| 依赖声明 | hash 抄参照键那条（用例里 = 999，⛔ 不是算的）；**新包必须在依赖数组里**（正控） |
| 计划文本 | `Units[` / `ModelFileName` / `ThumbnailFileName` **0 命中**（这一档没有数据库那一行） |
| 干跑 | 整棵游戏树 sha256 逐条不变，且**连补丁柜都没建** |
| ★ 不碰数据库 | 装完 `BA_Mod.zip` 的 sha256 **前后相同**；它那条尺子的正控见下一条 |
| ★ 尺子正控 | 真跑一次 `rewrite_zip_row`（数据库那一行的唯一写入口）⇒ zip 字节**必须变** |
| catalog.hash | 装完 `aa/catalog.hash` == `hash128_hex(整份 catalog.bin)` |
| 一条链 | 装完 -> `restore` -> 整棵树回到装前、新包被删（`BA_Mod\` 与 `aa\PC` 都不残留） |
| ★ 落点（块 B） | 包落 `<游戏根>\BA_Mod\<包>`；catalog 包条目指同一处（`..\..\..\BA_Mod\`） |
| 写盘炸 | 第 3 次写盘抛 ⇒ 补丁撤回：游戏树回到装前、清单不残留 |
| 负控 | 依赖来源键不在 catalog ⇒ `AssetError` 且**点名那个键**；同名包已登记 ⇒ 拒 |
| CLI | 退出码：干跑 0（stdout 里 `Units[` 0 命中）/ 依赖来源不在 2（键名在 stderr 上） |

**为什么这一档要单独一份判据**：`newasset add` / `add-model` 把「落包 + 登记」与
「重写 Units 行」「打 DLL」焊在同一条链上（`run_add` 里的 `external_db.install` 与
`dllpatch.apply`）。这一档的卖点是**那两段根本不存在** —— 所以判据不是"它也说不变"，
而是「计划里没有那个对象（`Units[` 0 命中）＋ 数据库那份 zip 的 sha256 前后逐位相同」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import builder
from ba_mod_maker.asset_kernel import metadata_writer as mw
from ba_mod_maker.cli import __main__ as cli_main
from ba_mod_maker.core.asset import dbzip, external_db, new_asset
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError

KEY = "BUNDLE_ADD_TEST"
UNIT_UNIQUE = "SYNTH_UNIQUE"
ALPHA = "alpha_assets_all_" + "1" * 32 + ".bundle"
ADDR_ONE = "addr/one"
SHARED_ASSET = "Assets/Synth/Shared.prefab"
#: 真件模型地址那条 entry[3] 的**合成**值（形状与 RU_T72B 的 1392117474 同款）。
DEP_HASH = 999
#: 二进制 catalog 里两条 provider 串的**真件后缀**（产品按后缀认，⛔ 不写死全名）。
BUNDLE_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
BUNDLED_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
#: 二进制 catalog 里的 Unity 装配名（形状抄真件；夹具自己选一个）。
UNITY_CORE = "UnityEngine.CoreModule"
#: 我们要搬进去的那个包（`add_bundle` 要求 `_all_` 尾段是 32 位十六进制）。
NEW_BUNDLE_NAME = "bundleaddtest_assets_all_" + "b" * 32 + ".bundle"
#: 负控用的键：它**不在** catalog 里。
MISSING_KEY = "NOPE_NOT_IN_THE_CATALOG"


def _aa(root: Path) -> Path:
    return root / "BrokenArrow_Data" / "StreamingAssets" / "aa"


def _units_payload(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _units_rows() -> list[dict]:
    return [
        {
            "Id": 1,
            "Name": "唯一",
            "ModelFileName": UNIT_UNIQUE,
            "ThumbnailFileName": "ONE-Label",
        },
    ]


def _write_zip(path: Path, units: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    dbzip.DbZip(tables={"Units": units, "Armors": b'[{"Id":1}]'}).write(path)
    return path


def _synth_bin_catalog() -> bytes:
    """合成的**二进制** catalog（1.2.1 形状）：1 条包登记 ＋ 1 条模型地址。

    ⛔ 用产品的 `catalog_bin_write.serialize` 把一份显式模型写成字节（纯函数、不碰盘）——
    手拼二进制等于**第二份实现**，必然漂移（与 tests/test_asset_add_model.py 同一份夹具口径）。
    """
    model = cbw.BinModel(
        provider_id="AddressablesMainContentCatalog",
        build_result_hash="0" * 32,
    )
    model.entries = [
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=ALPHA,
            # 真件形状（kb/02 §4）：包地址 = {…RuntimePath}\PC\<包>。落点改写只认这个形状。
            internal_id="{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\PC\\" + ALPHA,
            provider=BUNDLE_PROVIDER,
            dep_ranks=(),
            dep_hash=0,
            extra=cbw.AssetBundleOptions(
                hash_bytes=bytes(range(16)),
                bundle_name=ALPHA[: -len(".bundle")],
                crc=111,
                bundle_size=1000,
                common_raw=bytes.fromhex("0000200004000000"),
                type_asm=UNITY_CORE,
                type_cls="UnityEngine.AssetBundleRequestOptions",
            ),
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.AssetBundle",
            rank=0,
        ),
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=ADDR_ONE,
            internal_id=SHARED_ASSET,
            provider=BUNDLED_PROVIDER,
            dep_ranks=(0,),
            dep_hash=DEP_HASH,
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.GameObject",
            rank=1,
        ),
    ]
    model.keys = [
        cbw.BinKey(name=ALPHA, ranks=(0,), type_asm="mscorlib", type_cls="System.String"),
        cbw.BinKey(name=ADDR_ONE, ranks=(1,), type_asm="mscorlib", type_cls="System.String"),
    ]
    raw = cbw.serialize(model)
    # 夹具自证：它得真的是「2 键 / 2 条目」的二进制 catalog（不然下面的断言全是假的）
    assert cbw.looks_like_catalog_bin(raw) and cbw.counts(raw) == (2, 2, 2)
    return raw


def _make_game(tmp_path: Path, *, catalog: bytes | None = None) -> tuple[Path, Path]:
    """假游戏根：`aa/catalog.bin` ＋ `aa/catalog.hash` ＋ 一份 `BA_Mod\\BA_Mod.zip`。**没有 DLL**。

    ⛔ 故意不放假 `GameAssembly.dll`：这一档**根本不该看 DLL**（`run_add` 里那道
    `dllpatch.status` 闸在这条链上不存在）；放了反而把"它没碰 DLL"这件事盖住。
    """
    root = tmp_path / "game"
    aa = _aa(root)
    (aa / "PC").mkdir(parents=True)
    bin_bytes = _synth_bin_catalog() if catalog is None else catalog
    (aa / "catalog.bin").write_bytes(bin_bytes)
    (aa / "catalog.hash").write_bytes(cbw.hash128_hex(bin_bytes).encode("ascii"))
    external_db.ensure_zip_dir(root)
    _write_zip(external_db.game_zip_path(root), _units_payload(_units_rows()))
    project = tmp_path / "project"
    project.mkdir()
    return root, project


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    return _make_game(tmp_path)[0]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


@pytest.fixture()
def source_bundle(tmp_path: Path) -> Path:
    """一个**真的 UnityFS 文件**（元数据区是产品自己编的：0 类型 / 0 对象）。

    ⛔ 手搓容器就够了：本档验的是 catalog 的形状与"数据库没动"，不是造包（造包在 asset_kernel）。
    """
    meta = mw.SerializedMetadata(
        header=mw.SerializedFileHeader(0, 0, 22, 0, 0, b"\x00" * 3, 0, 0, 0, 0),
        unity_version="2022.3.62f3",
        target_platform=19,
        enable_type_tree=False,
        types=[],
        objects=[],
        script_types=[],
        externals=[],
        ref_types=[],
        user_information="",
    )
    container = builder.build_unityfs(
        mw.build_metadata(meta),
        builder.node_path_for(NEW_BUNDLE_NAME),
        block_type=builder.BLOCK_NONE,
    )
    path = tmp_path / NEW_BUNDLE_NAME
    path.write_bytes(container.data)
    return path


def _plan(game: Path, project: Path, bundle: Path, **kwargs: object):
    params: dict[str, object] = {
        "bundle": bundle,
        "key": KEY,
        "dependency_source": ADDR_ONE,
        "game_root": game,
        "project_root": project,
    }
    params.update(kwargs)
    return new_asset.plan_add_bundle(**params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ① 登记形状（计数头 / 地址那一条）
# --------------------------------------------------------------------------- #
def test_plan_registers_one_bundle_and_one_address(
    game: Path, project: Path, source_bundle: Path
) -> None:
    plan = _plan(game, project, source_bundle)
    assert plan.kind == "bundle"
    assert plan.registration.counts_before == (2, 2, 2)
    assert plan.registration.counts_after == (4, 4, 4)
    assert plan.bundle_name == NEW_BUNDLE_NAME
    # ★ 块 B：落点只有一处 —— `<游戏根>\BA_Mod\<包>`（⛔ 不再落 `aa\PC`）。
    assert plan.bundle_path == game / new_asset.MOD_DIR_NAME / NEW_BUNDLE_NAME
    assert not plan.bundle_path.exists()
    model = cbw.read_model(plan.catalog_after)
    names = [key.name for key in model.keys]
    bundle = model.entries[model.keys[names.index(NEW_BUNDLE_NAME)].ranks[0]]
    assert bundle.internal_id == plan.registration.internal_id
    assert bundle.internal_id.endswith(new_asset.MOD_SEGMENT + NEW_BUNDLE_NAME)


def test_the_default_asset_path_is_the_model_shape(
    game: Path, project: Path, source_bundle: Path
) -> None:
    plan = _plan(game, project, source_bundle)
    assert plan.asset_path == "Assets/Resources_moved/ModelPrefabs/" + KEY + ".prefab"


def test_address_entry_shape_and_copied_dependency_hash(
    game: Path, project: Path, source_bundle: Path
) -> None:
    """★ 正控：新包**必须在依赖数组里**（缺了游戏就去参照条目的包里找我们的资产）。"""
    plan = _plan(game, project, source_bundle)
    model = cbw.read_model(plan.catalog_after)
    names = [key.name for key in model.keys]
    entry = model.entries[model.keys[names.index(KEY)].ranks[0]]
    assert entry.internal_id == plan.asset_path
    assert (entry.provider or "").endswith("BundledAssetProvider")
    assert entry.type_cls == "UnityEngine.GameObject"
    assert entry.dep_hash == DEP_HASH
    ranks = list(entry.dep_ranks)
    new_bundle = model.entries[model.keys[names.index(NEW_BUNDLE_NAME)].ranks[0]]
    assert new_bundle.rank in ranks
    assert 0 in ranks


def test_plan_text_has_no_database_row(
    game: Path, project: Path, source_bundle: Path
) -> None:
    """本单的卖点：计划文本里**没有**数据库那一行（`Units[` 0 命中）。"""
    text = "\n".join(_plan(game, project, source_bundle).plan_lines())
    assert "Units[" not in text
    assert "ModelFileName" not in text
    assert "ThumbnailFileName" not in text
    assert "DLL（补丁那一段）：**本命令不动**" in text


# --------------------------------------------------------------------------- #
# ② 干跑 / 真装：数据库那份 zip 逐位不变（＋尺子正控）
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(game: Path, project: Path, source_bundle: Path) -> None:
    plan = _plan(game, project, source_bundle)
    before_game, before_project = _snapshot(game), _snapshot(project)
    report = new_asset.run_add_bundle_dry(plan)
    assert report.dry_run is True
    assert _snapshot(game) == before_game
    assert _snapshot(project) == before_project
    assert not (project / "patches").exists()


def test_the_zip_ruler_can_go_red(game: Path, project: Path) -> None:
    """★ 尺子正控：`rewrite_zip_row`（数据库那一行的写入口）**必须**把下面那条尺子弄红。

    没有这一条，"zip 的 sha256 前后相同"只证明了两份读数一样，证明不了尺子看得见改动。
    """
    zip_path = external_db.game_zip_path(game)
    before = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    edit = new_asset.rewrite_zip_row(zip_path, unit=UNIT_UNIQUE, key="RULER-Test")
    assert hashlib.sha256(edit.zip_after).hexdigest() != before


def test_install_keeps_the_database_zip_byte_identical(
    game: Path, project: Path, source_bundle: Path
) -> None:
    """★ 本单的核心判据：装完 BA_Mod.zip 与装前**逐位相同**。"""
    zip_path = external_db.game_zip_path(game)
    before = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    report = new_asset.run_add_bundle(_plan(game, project, source_bundle))
    assert report.verified is True
    assert report.zip_sha256 == before
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == before
    assert report.bundle_sha256 != "" and report.catalog_sha256 != ""


def test_install_makes_catalog_hash_match_the_bin(
    game: Path, project: Path, source_bundle: Path
) -> None:
    new_asset.run_add_bundle(_plan(game, project, source_bundle))
    aa = _aa(game)
    want = cbw.hash128_hex((aa / "catalog.bin").read_bytes()).encode("ascii")
    assert (aa / "catalog.hash").read_bytes() == want
    assert (game / new_asset.MOD_DIR_NAME / NEW_BUNDLE_NAME).is_file()
    assert not (aa / "PC" / NEW_BUNDLE_NAME).exists()


# --------------------------------------------------------------------------- #
# ③ 一条链：装 -> 全撤（逐位回来）／写盘炸 -> 补丁撤回
# --------------------------------------------------------------------------- #
def test_install_then_restore_returns_the_whole_tree(
    game: Path, project: Path, source_bundle: Path
) -> None:
    before = _snapshot(game)
    new_asset.run_add_bundle(_plan(game, project, source_bundle))
    assert _snapshot(game) != before
    outcome = new_asset.restore(game_root=game, project_root=project)
    assert outcome.ok is True
    assert _snapshot(game) == before
    assert not (game / new_asset.MOD_DIR_NAME / NEW_BUNDLE_NAME).exists()
    # 块 B 的遗留缺陷：restore 之后整棵树下都不许残留这个包。
    assert [p for p in game.rglob("*") if p.is_file() and NEW_BUNDLE_NAME in p.name] == []


def test_a_write_failure_rolls_the_tree_back(
    game: Path, project: Path, source_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写盘炸在第 3 次（新包那一步）⇒ 前两步也得撤回来，且清单不许残留。"""
    before = _snapshot(game)
    plan = _plan(game, project, source_bundle)
    real = new_asset._write_verified
    calls = {"n": 0}

    def boom(path: Path, data: bytes, *, what: str) -> None:
        calls["n"] += 1
        if calls["n"] >= 3:
            raise AssetError("用例故意在第 3 次写盘炸")
        real(path, data, what=what)

    monkeypatch.setattr(new_asset, "_write_verified", boom)
    with pytest.raises(AssetError):
        new_asset.run_add_bundle(plan)
    assert calls["n"] == 3
    assert _snapshot(game) == before
    assert new_asset._read_manifest(project) is None


# --------------------------------------------------------------------------- #
# ④ 负控（都要能红，而且要**点名**）
# --------------------------------------------------------------------------- #
def test_a_dependency_source_outside_the_catalog_is_refused_by_name(
    game: Path, project: Path, source_bundle: Path
) -> None:
    before = _snapshot(game)
    with pytest.raises(AssetError) as excinfo:
        _plan(game, project, source_bundle, dependency_source=MISSING_KEY)
    assert MISSING_KEY in str(excinfo.value)
    assert _snapshot(game) == before


def test_an_already_registered_bundle_name_is_refused(
    tmp_path: Path, source_bundle: Path
) -> None:
    """同名包已登记 ⇒ 拒（覆盖会静默改掉别人的依赖数组）。"""
    with_ours = cbw.add_bundle(
        _synth_bin_catalog(),
        bundle_name=NEW_BUNDLE_NAME,
        m_hash="b" * 32,
        crc=1,
        bundle_size=2,
        internal_name="probe",
    )
    game, project = _make_game(tmp_path, catalog=with_ours.after)
    with pytest.raises(AssetError) as excinfo:
        _plan(game, project, source_bundle)
    assert NEW_BUNDLE_NAME in str(excinfo.value)


def test_a_bundle_file_that_does_not_exist_is_refused(game: Path, project: Path) -> None:
    with pytest.raises(AssetError):
        _plan(game, project, game / "nope.bundle")


def test_an_empty_dependency_source_is_refused(
    game: Path, project: Path, source_bundle: Path
) -> None:
    with pytest.raises(AssetError):
        _plan(game, project, source_bundle, dependency_source="")


# --------------------------------------------------------------------------- #
# ⑤ CLI 接线（退出码 + 计划文本里 0 处数据库那一行）
# --------------------------------------------------------------------------- #
def _cli_args(game: Path, project: Path, bundle: Path, *extra: str) -> list[str]:
    return [
        "newasset",
        "add-bundle",
        "--bundle",
        str(bundle),
        "--key",
        KEY,
        "--dependency-source",
        ADDR_ONE,
        "--game",
        str(game),
        "--project-root",
        str(project),
        *extra,
    ]


def test_cli_dry_run_exits_zero_and_prints_no_database_row(
    game: Path, project: Path, source_bundle: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_main.main(_cli_args(game, project, source_bundle, "--dry-run"))
    assert code == 0
    out = capsys.readouterr().out
    assert "Units[" not in out
    assert KEY in out


def test_cli_names_the_missing_dependency_source_and_exits_two(
    game: Path, project: Path, source_bundle: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _cli_args(game, project, source_bundle, "--dry-run")
    argv[argv.index("--dependency-source") + 1] = MISSING_KEY
    code = cli_main.main(argv)
    assert code == 2
    err = capsys.readouterr().err
    assert MISSING_KEY in err

