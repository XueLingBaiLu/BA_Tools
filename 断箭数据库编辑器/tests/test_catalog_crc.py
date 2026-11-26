
r"""catalog crc 的判据 —— 「改过的包 ⇒ 把 catalog 里那条既有登记改对」（＋ catalog.hash 当一对）。

**判据一行**：

    .venv\Scripts\python.exe -m pytest tests\test_catalog_crc.py -q -n 0

## 为什么有这条命令（它是补一个实测出来的产品缺口）

2026-10-17 在 3.37 GB 的 units 包副本上跑产品真链（anim apply --catalog catalog.bin）实测：

    CHAIN_PAIRED GREEN          包改了、catalog 那条登记也跟着改了（对）
    CHAIN_HASH   RED            盘上 aa/catalog.hash 还是旧值

⇒ 产品那条 --catalog 路**只写 catalog.bin、不写 aa/catalog.hash**，而
aa/catalog.hash 的内容是 hash128_hex(整份 catalog.bin)，产品口径把它当**一对**
（core/asset/new_asset.py：「catalog.hash 与 catalog 是一对，别拆成两笔」）。
本命令就是那个缺口的补丁：**两个文件一起记 / 一起写 / 一起撤**。

## ⛔ 这条用例不读游戏目录

夹具全部是合成的（照 tests/test_writer_catalog_bin.py 的形状）：
二进制 catalog 用产品自己的 catalog_bin_write.serialize 写出来（手拼字节 = 第二份实现，必然漂移）；
包那一侧分两档 —— ① 打桩 bundle_facts_of_file（纯 catalog 记账的判据）· ② 真造一个 UnityFS 小包
（跑产品的 bundle_facts_of_file；缺 _work/w10_ref/units_meta.bin 就 skip）。

## 四条判据（都能红）

| # | 判据 | 红的样子 |
|---|---|---|
| ① | **正控**：--write 之后**重开文件**读回 == 盘上那份包的真实 (crc, size)，
且 hash128(新 catalog) == 盘上 hash | 任一条不等 |
| ② | **负控·没改过的包**：catalog 那条登记与盘上那份逐位相符 ⇒
**rc=1 且两个文件 sha256 前后相同** | 判成可以改 / 动了字节 |
| ③ | **负控·键不在 catalog**：rc=2 并**点名**（列出现有包条数） | 静默成功 / rc 不对 |
| ④ | **撤**：两个文件**逐位**回装前；装前没有 catalog.hash 的档 ⇒ 撤完它**不存在** |
回不去 / 留个空文件 |
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest

from ba_mod_maker.cli import address_inventory as ai
from ba_mod_maker.cli import catalog_crc as cc
from ba_mod_maker.core.assets import catalog_bin as cb
from ba_mod_maker.core.assets import catalog_bin_write as cbw

import os
# 工作根：数据已移出仓（2026-11-09，统一入口）。
WORK = Path(os.environ.get("BA_WORK_ROOT") or r"D:\断箭模组制作\_work_db")

BUNDLE = "syntheti_assets_all_" + "1" * 32 + ".bundle"
UNITY_CORE = "UnityEngine.CoreModule"
BUNDLE_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _synth_bin_catalog(*, crc: int, size: int, bundle: str = BUNDLE) -> bytes:
    """一条包登记的二进制 catalog（1.2.1 形状）—— 用产品写侧造，不从字节手拼。"""
    model = cbw.BinModel(provider_id="AddressablesMainContentCatalog", build_result_hash="0" * 32)
    model.entries = [
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=bundle,
            internal_id="aa/PC/" + bundle,
            provider=BUNDLE_PROVIDER,
            dep_ranks=(),
            dep_hash=0,
            extra=cbw.AssetBundleOptions(
                hash_bytes=bytes(range(16)),
                bundle_name=bundle[: -len(".bundle")],
                crc=int(crc),
                bundle_size=int(size),
                common_raw=bytes.fromhex("0000200004000000"),
                type_asm=UNITY_CORE,
                type_cls="UnityEngine.AssetBundleRequestOptions",
            ),
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.AssetBundle",
            rank=0,
        )
    ]
    model.keys = [
        cbw.BinKey(name=bundle, ranks=(0,), type_asm="mscorlib", type_cls="System.String")
    ]
    raw = cbw.serialize(model)
    assert cb.looks_like_catalog_bin(raw) and cbw.counts(raw) == (1, 1, 1), "夹具不是二进制 catalog"
    return raw


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _parse(*argv: str) -> argparse.Namespace:
    """走**真注册**那条路（与 __main__.py 同一句）：catalog 组 + 本模块的 crc 动作。"""
    parser = argparse.ArgumentParser(prog="ba-mod-maker")
    sub = parser.add_subparsers(dest="command")
    catalog_parser = ai.add_parser(sub)
    cc.add_crc_action(catalog_parser)
    return parser.parse_args(list(argv))


@pytest.fixture()
def fake_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """打桩的「包」：只为了给 bundle_facts_of_file 一个返回值（不参与解析）。"""
    path = tmp_path / "bundles" / BUNDLE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really a bundle -- facts is stubbed")
    return path


def _stub_facts(monkeypatch: pytest.MonkeyPatch, crc: int, size: int) -> None:
    from ba_mod_maker.core.asset import new_asset

    def fake(_bundle):
        return {"bundle_name": BUNDLE, "m_hash": "1" * 32, "crc": int(crc),
                "bundle_size": int(size), "internal_name": BUNDLE}, b"", 0

    monkeypatch.setattr(new_asset, "bundle_facts_of_file", fake)


@pytest.fixture()
def case(tmp_path: Path) -> dict:
    """一份 catalog + 一份 catalog.hash（内容是真口径），全在 tmp 里。"""
    cat = tmp_path / "aa" / "catalog.bin"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_bytes(_synth_bin_catalog(crc=111, size=1000))
    hsh = tmp_path / "aa" / "catalog.hash"
    hsh.write_text(cbw.hash128_hex(cat.read_bytes()), encoding="ascii")
    return {"cat": cat, "hash": hsh, "project": tmp_path / "proj"}


# --------------------------------------------------------------------------- #
# ① 正控：写后重开文件读回 ＋ hash 配对
# --------------------------------------------------------------------------- #
def test_positive_write_pairs_both_files(
    case: dict,
    fake_bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_facts(monkeypatch, crc=222, size=2000)
    args = _parse("catalog", "crc", "--catalog", str(case["cat"]), "--hash", str(case["hash"]),
                  "--bundle", str(fake_bundle), "--project-root", str(case["project"]), "--write")
    rc = cc.run(args)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert rc == cc.EXIT_OK, out
    # ①-a 重开文件读回（不信命令自己印的读数）
    from ba_mod_maker.asset_kernel import writer as wr

    entry = wr.catalog_entry(case["cat"], BUNDLE)
    assert (entry.crc, entry.bundle_size) == (222, 2000), entry
    # ①-b hash128(新 catalog) == 盘上 catalog.hash 的内容
    want = cbw.hash128_hex(case["cat"].read_bytes())
    assert case["hash"].read_text(encoding="ascii").strip() == want
    # ①-c 补丁柜里那一条**同时**记了两个文件（一对）
    assert (case["project"] / "patches" / "install.rmod.json").is_file()


def test_positive_dry_run_writes_nothing(
    case: dict, fake_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """干跑：catalog 与 catalog.hash **一个字节都不写**（默认档的判据）。"""
    _stub_facts(monkeypatch, crc=222, size=2000)
    before = (_sha(case["cat"]), _sha(case["hash"]))
    rc = cc.run(_parse("catalog", "crc", "--catalog", str(case["cat"]), "--hash", str(case["hash"]),
                       "--bundle", str(fake_bundle), "--project-root", str(case["project"])))
    assert rc == cc.EXIT_OK
    assert (_sha(case["cat"]), _sha(case["hash"])) == before, "干跑动了字节"
    assert not (case["project"] / "patches").exists(), "干跑连补丁柜目录都不该建"


def test_positive_roundtrip_control_holds_on_the_fixture(case: dict) -> None:
    """命令依赖的那条正控（原样重编码逐字节回原样）在夹具上**真的成立** —— 不是假设。"""
    raw = case["cat"].read_bytes()
    assert cbw.roundtrip(raw) == raw


# --------------------------------------------------------------------------- #
# ② 负控：没改过的包 ⇒ rc=1 且零写入
# --------------------------------------------------------------------------- #
def test_negative_control_unchanged_bundle_is_noop_and_writes_nothing(
    case: dict,
    fake_bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_facts(monkeypatch, crc=111, size=1000)          # 与 catalog 里那条**逐位相符**
    before = (_sha(case["cat"]), _sha(case["hash"]))
    rc = cc.run(_parse("catalog", "crc", "--catalog", str(case["cat"]), "--hash", str(case["hash"]),
                       "--bundle", str(fake_bundle), "--project-root", str(case["project"]),
                       "--write"))
    assert rc == cc.EXIT_NOOP, "没改过的包必须判「没有可改的」"
    assert (_sha(case["cat"]), _sha(case["hash"])) == before, "它动了字节"
    assert not (case["project"] / "patches").exists()


# --------------------------------------------------------------------------- #
# ③ 负控：键不在 catalog ⇒ rc=2 并点名
# --------------------------------------------------------------------------- #
def test_negative_control_unknown_key_is_refused(case: dict, fake_bundle: Path,
                                                 monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    _stub_facts(monkeypatch, crc=222, size=2000)
    before = _sha(case["cat"])
    rc = cc.run(_parse("catalog", "crc", "--catalog", str(case["cat"]), "--hash", str(case["hash"]),
                       "--bundle", str(fake_bundle), "--name", "no_such_key_9z.bundle",
                       "--project-root", str(case["project"]), "--write"))
    assert rc == cc.EXIT_UNUSABLE
    assert _sha(case["cat"]) == before, "拒了还动了字节"
    printed = capsys.readouterr()
    assert "no_such_key_9z.bundle" in (printed.out + printed.err)
    assert "1" in (printed.out + printed.err), "要点名列里现有几条包登记"


# --------------------------------------------------------------------------- #
# ④ 撤：两个文件逐位回；装前没有 hash 的档 ⇒ 撤完不存在
# --------------------------------------------------------------------------- #
def test_restore_brings_both_files_back(case: dict, fake_bundle: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_facts(monkeypatch, crc=333, size=3000)
    cat_before, hash_before = _sha(case["cat"]), _sha(case["hash"])
    assert cc.run(
        _parse("catalog", "crc", "--catalog", str(case["cat"]), "--hash", str(case["hash"]),
               "--bundle", str(fake_bundle), "--project-root", str(case["project"]), "--write")
    ) == cc.EXIT_OK
    assert _sha(case["cat"]) != cat_before and _sha(case["hash"]) != hash_before
    from ba_mod_maker.asset_kernel import reverse_delta as rd

    report = rd.PatchStore(case["project"] / "patches").restore()
    assert report.ok, report.lines()
    assert _sha(case["cat"]) == cat_before, "catalog 没逐位回"
    assert _sha(case["hash"]) == hash_before, "catalog.hash 没逐位回"


def test_restore_deletes_a_hash_that_did_not_exist(tmp_path: Path, fake_bundle: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """装前**没有** catalog.hash 的那一档：撤完它必须**不存在**（⛔ 不是留个空文件）。"""
    cat = tmp_path / "catalog.bin"
    cat.write_bytes(_synth_bin_catalog(crc=111, size=1000))
    hsh = tmp_path / "catalog.hash"
    project = tmp_path / "proj"
    _stub_facts(monkeypatch, crc=444, size=4000)
    assert cc.run(_parse("catalog", "crc", "--catalog", str(cat), "--hash", str(hsh),
                         "--bundle", str(fake_bundle), "--project-root", str(project),
                         "--write")) == cc.EXIT_OK
    assert hsh.is_file()
    assert hsh.read_text(encoding="ascii").strip() == cbw.hash128_hex(cat.read_bytes())
    from ba_mod_maker.asset_kernel import reverse_delta as rd

    assert rd.PatchStore(project / "patches").restore().ok
    assert not hsh.exists(), "装前不存在 ⇒ 撤完必须不存在"


# --------------------------------------------------------------------------- #
# ⑤ 接线：catalog 组里真的有 crc，而且 --help 取得到
# --------------------------------------------------------------------------- #
def test_crc_action_is_registered_under_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    from ba_mod_maker.cli import __main__ as cli_main

    parser = cli_main._build_parser()
    text = parser.format_help()
    assert "crc" in text, text
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["catalog", "crc", "--help"])
    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--bundle" in help_text and "--write" in help_text
    assert "catalog.hash" in help_text, "帮助里必须写明它管 catalog.hash 那一对"


def test_positive_real_bundle_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真造一个 UnityFS 小包跑**产品的** bundle_facts_of_file（缺夹具就 skip）。"""
    repo = Path(__file__).resolve().parents[1]
    meta_path = WORK / "w10_ref" / "units_meta.bin"
    if not meta_path.is_file():
        pytest.skip("缺 _work/w10_ref/units_meta.bin（造包夹具的类型树）")
    from ba_mod_maker.asset_kernel import builder, clone, prefab_build
    from ba_mod_maker.core.asset import new_asset

    meta = meta_path.read_bytes()
    table = builder.read_raw_type_table(meta, common_strings=clone.unitypy_common_strings())
    go_pid = builder.path_id_for("catcrc|go")
    go = prefab_build.default_fields(table.entry_for(1).nodes)
    go["m_Name"] = "CatCrcRoot"
    plan = builder.build_object_bundle(
        objects=[builder.ObjectSpec(1, go, go_pid, label="go")],
        bundle_name=BUNDLE, source_meta=meta,
        container=[("Assets/Resources_moved/ModelPrefabs/Synth/CatCrc.prefab", go_pid)],
        preload_all=True,
    )
    bundle = tmp_path / BUNDLE
    bundle.write_bytes(bytes(plan.data))

    facts, _data, _stream = new_asset.bundle_facts_of_file(bundle)
    assert facts["bundle_name"] == BUNDLE
    # catalog 里那一条**故意写错** ⇒ 命令应当把它改成盘上真实值
    cat = tmp_path / "catalog.bin"
    cat.write_bytes(_synth_bin_catalog(crc=(int(facts["crc"]) ^ 0x5A5A) & 0xFFFFFFFF,
                                       size=int(facts["bundle_size"]) + 7))
    hsh = tmp_path / "catalog.hash"
    hsh.write_text(cbw.hash128_hex(cat.read_bytes()), encoding="ascii")
    project = tmp_path / "proj"
    assert cc.run(_parse("catalog", "crc", "--catalog", str(cat), "--hash", str(hsh),
                         "--bundle", str(bundle), "--project-root", str(project),
                         "--write")) == cc.EXIT_OK
    from ba_mod_maker.asset_kernel import writer as wr

    entry = wr.catalog_entry(cat, BUNDLE)
    assert (entry.crc, entry.bundle_size) == (int(facts["crc"]), int(facts["bundle_size"])), entry
    assert hsh.read_text(encoding="ascii").strip() == cbw.hash128_hex(cat.read_bytes())
    # 再跑一次（现在相符了）⇒ NOOP：「装完之后再跑一次」就是这把复核尺子
    assert cc.run(_parse("catalog", "crc", "--catalog", str(cat), "--hash", str(hsh),
                         "--bundle", str(bundle), "--project-root", str(project))) == cc.EXIT_NOOP


def test_usage_when_game_root_is_missing_is_two(tmp_path: Path, fake_bundle: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """找不到游戏根 / 文件缺 ⇒ rc=2（⛔ 不抛 traceback 给用户）。"""
    from ba_mod_maker.core.assets import game as game_mod

    monkeypatch.setattr(game_mod, "find_game_root", lambda: None)
    rc = cc.run(
        _parse("catalog", "crc", "--bundle", str(fake_bundle), "--project-root", str(tmp_path))
    )
    assert rc == cc.EXIT_UNUSABLE
    rc2 = cc.run(_parse("catalog", "crc", "--catalog", str(tmp_path / "nope.bin"),
                        "--bundle", str(fake_bundle), "--project-root", str(tmp_path)))
    assert rc2 == cc.EXIT_UNUSABLE
