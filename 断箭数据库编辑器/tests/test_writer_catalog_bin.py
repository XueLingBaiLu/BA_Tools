r"""★ 回归（2026-10-15 真机）· `writer.catalog_entry` 必须认 1.2.1 那份**二进制** `catalog.bin`。

## 现场（真机黑匣子原文）

> `变换那一半：★ UnicodeDecodeError: 'utf-8' codec can\'t decode byte 0x89 in position 1`

根因：`asset_kernel/writer.py::catalog_entry` 把 catalog **按 UTF-8 JSON 读**
（`read_bytes().decode("utf-8")`），而 1.2.1 起游戏那份是二进制 `catalog.bin`
（magic `42 89 e3 0d` —— 第 2 个字节正是 `0x89`）。

修法：读侧加二进制分支（`core.assets.catalog_bin.bundle_entries`）＋ 写侧 `_build_catalog_patch_bin`
（`catalog_bin_write` 全量重编码，正控 `roundtrip`）—— 补丁对象多一个 `binary=True`。

## 判据一行

    .venv\Scripts\python.exe -m pytest tests\test_writer_catalog_bin.py -q -n 0
      -> 修前 **2 红**（UnicodeDecodeError / 拒不了）；修后 **3 passed**

⛔ 这条用例**不读游戏目录**：夹具是合成的二进制 catalog（用产品的 `catalog_bin_write.serialize`
把一份显式模型写成字节 —— 手拼字节等于第二份实现，必然漂移）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import writer as wr
from ba_mod_maker.core.assets import catalog_bin as cb
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.errors import AssetError

BUNDLE = "alpha_assets_all_" + "1" * 32 + ".bundle"
ADDR_ONE = "addr/one"
SHARED_ASSET = "Assets/Synth/Shared.prefab"
BUNDLE_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
BUNDLED_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
UNITY_CORE = "UnityEngine.CoreModule"


def _synth_bin_catalog() -> bytes:
    """合成的二进制 catalog（1.2.1 形状）：1 条包登记（crc=111 / size=1000）＋ 1 条地址。"""
    model = cbw.BinModel(
        provider_id="AddressablesMainContentCatalog",
        build_result_hash="0" * 32,
    )
    model.entries = [
        cbw.BinEntry(
            loc_offset=-1,
            primary_key=BUNDLE,
            internal_id="aa/PC/" + BUNDLE,
            provider=BUNDLE_PROVIDER,
            dep_ranks=(),
            dep_hash=0,
            extra=cbw.AssetBundleOptions(
                hash_bytes=bytes(range(16)),
                bundle_name=BUNDLE[: -len('.bundle')],
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
            dep_hash=999,
            type_asm=UNITY_CORE,
            type_cls="UnityEngine.GameObject",
            rank=1,
        ),
    ]
    model.keys = [
        cbw.BinKey(name=BUNDLE, ranks=(0,), type_asm="mscorlib", type_cls="System.String"),
        cbw.BinKey(name=ADDR_ONE, ranks=(1,), type_asm="mscorlib", type_cls="System.String"),
    ]
    raw = cbw.serialize(model)
    # 夹具自证：它得真的是「2 键 / 2 条目」的二进制 catalog（不然下面断言全是假的）
    assert cb.looks_like_catalog_bin(raw) and cbw.counts(raw) == (2, 2, 2)
    return raw


@pytest.fixture()
def catalog_bin_file(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.bin"
    path.write_bytes(_synth_bin_catalog())
    return path


def test_positive_binary_catalog_entry_reads_crc_and_size(catalog_bin_file: Path) -> None:
    """★ 正控：二进制 catalog 里那条包登记读得出来（**修前这里是 UnicodeDecodeError**）。"""
    entry = wr.catalog_entry(catalog_bin_file, BUNDLE)
    assert entry.crc == 111 and entry.bundle_size == 1000, entry
    assert BUNDLE in entry.internal_id, entry.internal_id
    # 与只读视图对得上（两把尺子互相印证）
    hit = cb.bundle_entries(catalog_bin_file)[BUNDLE]
    assert (entry.crc, entry.bundle_size) == (hit.crc, hit.bundle_size)


def test_positive_binary_patch_reencodes_and_keeps_the_control(
    catalog_bin_file: Path,
) -> None:
    """★ 正控：写侧补丁 —— 改 crc/size 之后仍是合法二进制 catalog，且标了 `binary=True`。"""
    patch = wr._build_catalog_patch(catalog_bin_file, BUNDLE, crc=222, bundle_size=2000)
    assert patch.binary is True
    assert patch.crc_old == 111 and patch.crc_new == 222
    assert patch.before == catalog_bin_file.read_bytes(), "盘上那份没被改（补丁只是给字节）"
    reader = getattr(cb, "bundle_entries_bytes", None)
    got = reader(patch.after) if reader is not None else None
    if got is None:      # 回退：先落临时文件再读（只读路径，同一个函数）
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            again = Path(tmp) / "after.bin"
            again.write_bytes(patch.after)
            got = cb.bundle_entries(again)
    assert got[BUNDLE].crc == 222 and got[BUNDLE].bundle_size == 2000
    assert "catalog.bin 全量重编码" in " ".join(patch.lines())


def test_negative_control_unknown_bundle_name_is_refused(catalog_bin_file: Path) -> None:
    """负控：问一条**不在这份 catalog 里**的包 —— 必须点名拒（⛔ 不是静默给个空值）。"""
    with pytest.raises(AssetError) as excinfo:
        wr.catalog_entry(catalog_bin_file, "no_such_bundle_9z.bundle")
    assert "no_such_bundle_9z.bundle" in str(excinfo.value)
    assert "catalog.bin" in str(excinfo.value)
