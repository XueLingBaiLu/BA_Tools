r"""地址层判据：catalog 的**全结构**新增 / 删除（asset_kernel/catalog_edit.py）。

**判据一行**：pytest tests/test_catalog_edit.py -q -> 通过数从 0 变 **38 collected**
（本文件新建前不存在），其中 **15 条**是负控 / 能红用例（喂一个已知违规的点进去必须红）。

⚠ **2026-10-18 起多了一档 skip**：真件那 15 条吃的是 `catalog.json`（≤1.2.0 的格式），
而 1.2.1（2026-10-17 11:30 的更新）**只发 `catalog.bin`** ⇒ 这类机器上是
**23 passed / 15 skipped / 0 failed**，skip 理由逐字写明「JSON 全结构编辑通道没有活样本；
二进制侧的等价覆盖在 tests/test_catalog_bin_write.py」。还有 `catalog.json` 的机器上仍是 38 passed。
⛔ 别把那些 skip 当绿：它们是「这台机器上没有这个格式的活样本」，不是「跑过了」。

| # | 用例 | 怎么量 | 通过线 |
|---|---|---|---|
| R1 | 真件逐字节往返 | to_bytes(from_bytes(4,016,273 B)) == 原字节 | 相等（打印两个 sha256） |
| R2 | R1 的尺子**能红** | 改一条 entry 的 provider 再出字节 | 与原字节**不等** |
| R3 | 真件「新增包 -> 删除包」= 恒等 | add_bundle 再 remove_bundle | 逐字节 == 原文件 |
| R4 | 真件「新增包+地址 -> 删除地址+删除包」= 恒等 | 两条原子操作各自撤销 | 逐字节 == 原文件 |
| R5 | 真件删一个**中间**的包（重编号） | 计数头 -1 / 自检 0 / 读侧全键对拍 | 变化只是"少了这个包" |
| N1 | 删不存在的包名 | remove_bundle("no_such…") | AssetError |
| N2 | 删不存在的地址键 | remove_address("no/such") | AssetError |
| N3 | 删一个**被别的条目当依赖键**的包 | 合成件：addr/two 依赖 BETA 的键 | AssetError |
| N4 | 删完还有悬空下标 | 桶里塞一个越界的 entry 下标 -> to_bytes | AssetError |
| N5 | 悬空的 InternalId 下标 | entry[0] 越界 -> to_bytes | AssetError |
| N6 | 共用的 extra 段 | 两个条目指同一条 extra 再删其中一个 | AssetError |
| P1 | 共享的 InternalId 不许盲删 | 两条地址共用一个 internalId，删其中一条 | 留着 + 读侧照旧 |
| P2 | 删包要摘掉依赖组里的成员关系 | 合成件 group 桶里有 3 个包条目 | 删完剩 2 + 报告里有数 |
| P3 | 新增那半与 catalog_writer 口径一致 | 同一份合成件走两边 add_* | to_bytes 逐字节相等 |
| P4 | 非磁盘口径的 JSON 不许进 | json.dumps 默认分隔符那份 | AssetError |
| R6 | 真件删一条**共享**地址 | 地址键 + GUID 键指着同一条目 | 条目留着 + [5] 重指 + 兄弟键照旧 |
| R7 | 真件规格：资产条目**全是共享的** | 每条资产条目的引用桶数 | >=2（独享的只有包条目） |
| P5 | 合成件的共享模型 | 两条键指同一条目，删其一 / 删别名 | 条目留着 + 重指 + 别名照旧 |
| N7 | keep 一条没人引用的条目 | 独享条目写进 keep_entries | AssetError |
| P6 | dependents_of 列出要逐个删的地址键 | 合成件 addr/two 依赖 BETA | 清单 = (addr/two,) |
| P6b | dependents_of 未知包名 | 不存在的包 | AssetError |
| R8 | 真件 dependents_of = 删除清单 | 被依赖的包（39/77 之一） | 非空 + 去重有序 + 每条都真依赖它 |
| R9 | 真件更新 unitlabels 的 m_Crc+1 | 位数不变（等长档） | **条目表逐字节不动** + 只动那 845 B |
| R9B | **对偶**：改回原值 | +1 再改回 | 逐字节 == 原文件 |
| R9C | m_Crc 999999999 -> 1000000000 | 9 位->10 位 = +2 B | 5 条偏移 +2 + 77 条读回逐字段相同 |
| P7 | 同值更新是空操作 | crc/size/hash 都传原值 | changed_fields 空 + 逐字节 == 原文件 |
| N8 | 漏改一条 extraDataOffset | 改完长度后把某条偏移退回旧值 | to_bytes() -> AssetError |
| N9 | 更新不存在的包 / 地址键 / 组键 | 三种都不是包登记 | AssetError |
| N10 | 共用的 extra | 两条条目指同一条 extra 再更新 | AssetError |

真件那几条在**没装游戏**、或**装了 1.2.1（只有 catalog.bin）**时 skip；合成件永远跑。
真件读数想直接看到（pytest 默认吞 print）：加上 -s。

    .venv\Scripts\python.exe -m pytest tests/test_catalog_edit.py -q -s

## 为什么真件那几条必须存在

合成件测不出"真件上的重编号对不对"：真件有 28,937 个键 / 21,274 条 entry / 77 个包，
且**桶不只装自己那条键的条目**（一个包条目会同时列在依赖组的桶里）。R5 就是奔着这件事去的。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path

import pytest

from ba_mod_maker.asset_kernel import catalog_edit as ce
from ba_mod_maker.asset_kernel import catalog_writer as cw
from ba_mod_maker.core.assets.catalog import AddressablesCatalog
from ba_mod_maker.core.errors import AssetError

# --------------------------------------------------------------------------- #
# 合成件（手搓，不用被测代码拼结构）
# --------------------------------------------------------------------------- #
ALPHA = "alpha_assets_all_" + "1" * 32 + ".bundle"
BETA = "beta_assets_all_" + "2" * 32 + ".bundle"
GAMMA = "gamma_assets_all_" + "3" * 32 + ".bundle"
GROUP = "group/dep1"
ADDR_ONE = "addr/one"
ADDR_TWO = "addr/two"
SHARED_ASSET = "Assets/Synth/Shared.prefab"
NEW_BUNDLE = "synthnew_assets_all_" + "9" * 32 + ".bundle"
NEW_ASSET = "Assets/Synth/New.prefab"
NEW_ADDRESS = "addr/new"


def _blob(key: str, crc: int, size: int) -> bytes:
    options = cw.CatalogBundle(
        key, key[0] * 32, crc, size, internal_name=key[: -len(".bundle")]
    ).options()
    return cw.extra_data_blob(options)


def _synth() -> dict:
    """一份最小但合规的 catalog：3 个包 + 1 个依赖组 + 2 条地址（共用一个 internalId）。

    桶（桶 i 对应键 i）：

        0 ALPHA -> [entry0]      1 BETA -> [entry1]     2 GAMMA -> [entry2]
        3 GROUP -> [entry0, entry1, entry2]   <- 包条目也列在依赖组的桶里（真件就是这样）
        4 addr/one -> [entry3]   5 addr/two -> [entry4]

    entry3 的依赖键 = GROUP；entry4 的依赖键 = **BETA 那条键**（N3 用的就是它）。
    entry3 与 entry4 的 internalId **同一个下标 3**（P1 用的就是它）。
    """
    internal = [
        cw.RUNTIME_PATH + cw.PC_SEGMENT + ALPHA,
        cw.RUNTIME_PATH + cw.PC_SEGMENT + BETA,
        cw.RUNTIME_PATH + cw.PC_SEGMENT + GAMMA,
        SHARED_ASSET,
    ]
    extra = bytearray()
    offsets: list[int] = []
    for key, crc in ((ALPHA, 111), (BETA, 222), (GAMMA, 333)):
        offsets.append(len(extra))
        extra += _blob(key, crc, 1000 + crc)
    key_values = (ALPHA, BETA, GAMMA, GROUP, ADDR_ONE, ADDR_TWO)
    key_blobs = [cw.key_bytes(cw.KEY_UTF8, value) for value in key_values]
    key_offsets: list[int] = []
    pos = 4
    for blob in key_blobs:
        key_offsets.append(pos)
        pos += len(blob)
    buckets = [[0], [1], [2], [0, 1, 2], [3], [4]]
    entries = [
        [0, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH, offsets[0], 0, 0],
        [1, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH, offsets[1], 1, 0],
        [2, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH, offsets[2], 2, 0],
        [3, cw.BUNDLED_ASSET_PROVIDER, 3, 999, -1, 4, 1],
        [3, cw.BUNDLED_ASSET_PROVIDER, 1, 888, -1, 5, 1],
    ]
    key_data = bytearray(struct.pack("<i", len(key_blobs)))
    for blob in key_blobs:
        key_data += blob
    bucket_data = bytearray(struct.pack("<i", len(buckets)))
    for index, members in enumerate(buckets):
        bucket_data += struct.pack("<ii", key_offsets[index], len(members))
        bucket_data += struct.pack(f"<{len(members)}i", *members)
    entry_data = bytearray(struct.pack("<i", len(entries)))
    for row in entries:
        entry_data += struct.pack("<7i", *row)
    return {
        "m_LocatorId": "AddressablesMainContentCatalog",
        "m_BuildResultHash": "0" * 32,
        "m_ProviderIds": [
            "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.LegacyResourcesProvider",
            "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
            "",
        ],
        "m_InternalIds": internal,
        "m_KeyDataString": base64.b64encode(bytes(key_data)).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bytes(bucket_data)).decode("ascii"),
        "m_EntryDataString": base64.b64encode(bytes(entry_data)).decode("ascii"),
        "m_ExtraDataString": base64.b64encode(bytes(extra)).decode("ascii"),
        "m_resourceTypes": [
            {"m_AssemblyName": "Unity.RM", "m_ClassName": "GameObject"},
            {"m_AssemblyName": "Unity.RM", "m_ClassName": "Texture2D"},
        ],
        "m_InternalIdPrefixes": [],
    }


def _bytes(raw: dict) -> bytes:
    return json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _disk(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / cw.CATALOG_FILENAME
    path.write_bytes(_bytes(raw))
    return path


def _index(tmp_path: Path, name: str, data: bytes) -> dict[str, set[tuple[str, str]]]:
    """产品读侧（core/assets/catalog.py）读一份 catalog 的字节。"""
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / cw.CATALOG_FILENAME).write_bytes(data)
    catalog = AddressablesCatalog.load(folder)
    assert catalog is not None, f"产品读侧读不回来：{folder}"
    return catalog.index


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


# --------------------------------------------------------------------------- #
# 真件（没装游戏就 skip）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_bytes() -> bytes:
    """真件 **JSON** catalog 的整份字节（这是本模块**唯一**能吃的形状）。

    ⚠ 2026-10-18 改口径：这里原来调 `game.catalog_file()`，而那个函数按设计回答
    「**盘上那份 catalog 是哪个**」—— 1.2.1 起它就是 `aa/catalog.bin`（二进制），
    喂进 `CatalogEdit.from_bytes` 直接 `UnicodeDecodeError: 0x89` ⇒ 本文件 15 条真件用例
    集体假红（2026-10-17 11:30 游戏更新之后现场就是这个状态，看着像产品读写坏了）。

    ⇒ 现在只问 `game.catalog_json_file()`（它只回答「**还有没有 JSON 那一份**」）。
    没有就 skip —— 下面那 15 条**不是变绿，是这台机器上不适用**（skip 理由逐字写明）。
    """
    from ba_mod_maker.core.assets.game import catalog_json_file, find_game_root

    root = find_game_root()
    path = catalog_json_file(root) if root is not None else None
    if path is None:
        pytest.skip(
            "1.2.1（2026-10-17 11:30）起游戏只发 catalog.bin；JSON 全结构编辑通道没有活样本；"
            "二进制侧的等价覆盖在 tests/test_catalog_bin_write.py"
        )
    return path.read_bytes()


@pytest.fixture(scope="module")
def free_bundle_name(real_bytes: bytes) -> str:
    """真件里一个**没被别的条目当依赖键**的包（那种才删得掉，实测 38/77）。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    table = edit.to_table()
    dependency_keys = {row[2] for row in table.entries() if row[2] != -1}
    for index in range(table.key_count):
        value = table.key_value(index)
        if (
            isinstance(value, str)
            and value.endswith(".bundle")
            and index not in dependency_keys
        ):
            return value
    pytest.skip("真件里找不到没被依赖的包（catalog 换了版本？）")


# --------------------------------------------------------------------------- #
# 守卫：JSON 通道遇到 1.2.1 的 catalog.bin 必须**报不支持**（⛔ 不是裸 UnicodeDecodeError）
# --------------------------------------------------------------------------- #
def test_json_channel_refuses_a_binary_catalog_explicitly() -> None:
    """不变式（`core/assets/catalog_bin.py` 文件头逐字写着）：JSON 写侧遇到 `.bin` 必须报不支持。

    2026-10-18 之前这里抛的是裸 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x89
    in position 1`：既不是本产品的错误类型、也没有指路 ⇒ 现场表现是 15 条用例集体假红，
    看上去像「产品读写坏了」，其实是**喂错了格式**。

    ✅ **能红**：把 `from_bytes` 里那段判别去掉，这条立刻红。**负控**见下一条。
    """
    from ba_mod_maker.core.assets.catalog_bin import CATALOG_BIN_MAGIC

    head = struct.pack("<8I", CATALOG_BIN_MAGIC, 2, 36, 179976, 1, 2, 3, 4)
    with pytest.raises(AssetError) as caught:
        ce.CatalogEdit.from_bytes(head)
    message = str(caught.value)
    assert "catalog.bin" in message, f"报错没点名二进制那份：{message}"
    assert "catalog_bin_write" in message, f"报错没指路到二进制写侧：{message}"


def test_the_binary_refusal_is_not_a_blanket_rejection() -> None:
    """**负控（尺子能红）**：上一条的判据不是「凡是我都拒」。

    同一个入口喂**合法 JSON** 必须照收 —— 否则「遇到 .bin 报不支持」这条断言恒真、
    根本没在量东西（这正是 §0.9 动作 2 要防的假尺子）。
    """
    edit = ce.CatalogEdit.from_bytes(_bytes(_synth()))
    assert edit.counts == (6, 6, 5)
    assert edit.to_bytes() == _bytes(_synth()), "合法 JSON 进来还得能逐字节回去"


# --------------------------------------------------------------------------- #
# R1 / R2 · 逐字节往返 + 尺子能红
# --------------------------------------------------------------------------- #
def test_r1_real_catalog_roundtrip_is_byte_exact(real_bytes: bytes) -> None:
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    out = edit.to_bytes()
    print(f"\n[R1] 原文件 {len(real_bytes):,} B sha256 {_sha(real_bytes)}")
    print(f"[R1] 重排后 {len(out):,} B sha256 {_sha(out)}")
    print(f"[R1] {edit.counts_line()}")
    assert out == real_bytes, "catalog 的逐字节往返断了（这就是不许写坏的那条线）"
    assert cw.check_pairs(edit.to_table()) == ()


def test_r2_roundtrip_ruler_can_go_red(real_bytes: bytes) -> None:
    """R1 的尺子不是恒真：把一条 entry 的 provider 改一改，字节必须**不再相等**。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    edit.entries[0][ce.ENTRY_PROVIDER] = 2
    assert edit.to_bytes() != real_bytes
    # 而且改的是"一格"：只这一处不同（把 provider 改回去就回到原字节）
    edit.entries[0][ce.ENTRY_PROVIDER] = 0
    assert edit.to_bytes() == real_bytes


# --------------------------------------------------------------------------- #
# R3 / R4 · 新增 + 删除 = 恒等（真件副本上）
# --------------------------------------------------------------------------- #
def _probe_bundle() -> cw.CatalogBundle:
    return cw.CatalogBundle(
        NEW_BUNDLE, "9" * 32, 123456789, 4096, internal_name="synthnew"
    )


def test_r3_add_then_remove_bundle_is_identity_on_real_catalog(real_bytes: bytes) -> None:
    """判据一行：add_bundle 再 remove_bundle -> 产物 sha256 从 X 回到原文件的 Y。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    before = edit.counts
    registration = edit.add_bundle(_probe_bundle())
    added = edit.to_bytes()
    print(f"\n[R3] 原文件      {len(real_bytes):,} B sha256 {_sha(real_bytes)}")
    print(f"[R3] 新增一条包后 {len(added):,} B sha256 {_sha(added)}")
    assert added != real_bytes
    assert edit.counts == (before[0] + 1, before[1] + 1, before[2] + 1)
    report = edit.remove_bundle(NEW_BUNDLE)
    back = edit.to_bytes()
    print(f"[R3] 删掉那一条后 {len(back):,} B sha256 {_sha(back)}")
    print(f"[R3] {report.summary()}")
    assert report.entry_indices == (registration.entry_index,)
    assert report.group_memberships_dropped == 0
    assert back == real_bytes, "新增 + 删除必须逐字节回到原文件"
    assert edit.counts == before


def test_r4_add_address_then_remove_is_identity_on_real_catalog(real_bytes: bytes) -> None:
    """判据一行：两条原子操作各自撤销（新增包+地址 -> 删地址+删包）-> 逐字节回到原文件。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    registration = edit.add_bundle(_probe_bundle())
    edit.add_address(
        cw.CatalogAddress(NEW_ASSET, NEW_ADDRESS, resource_type=1),
        dependency_key=registration.key_index,
    )
    with_address = edit.to_bytes()
    assert with_address != real_bytes
    # 产品读侧读得回来（真件副本上走一次完整的那条链）
    edit.remove_address(NEW_ADDRESS)
    middle = edit.to_bytes()
    report = edit.remove_bundle(NEW_BUNDLE)
    back = edit.to_bytes()
    print(f"\n[R4] 原文件        sha256 {_sha(real_bytes)}")
    print(f"[R4] 加包+加地址后 sha256 {_sha(with_address)}")
    print(f"[R4] 删掉地址后    sha256 {_sha(middle)}")
    print(f"[R4] 再删掉包      sha256 {_sha(back)}   {report.summary()}")
    assert back == real_bytes


def test_r4b_added_address_is_readable_by_the_product_reader(
    tmp_path: Path, real_bytes: bytes
) -> None:
    """真件副本上：新增地址 -> 产品读侧（core/assets/catalog.py）读回来（地址 -> 资产 + 新包）。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    registration = edit.add_bundle(_probe_bundle())
    edit.add_address(
        cw.CatalogAddress(NEW_ASSET, NEW_ADDRESS, resource_type=1),
        dependency_key=registration.key_index,
    )
    index = _index(tmp_path, "with_address", edit.to_bytes())
    assert index[NEW_ADDRESS] == {(NEW_ASSET, NEW_BUNDLE)}
    assert index[NEW_BUNDLE] == {(cw.RUNTIME_PATH + cw.PC_SEGMENT + NEW_BUNDLE, "")}


def test_r4c_added_address_dependency_hash_falls_back_to_the_sentinel(real_bytes: bytes) -> None:
    """依赖 hash 抄不到就写 17（真件上就是这个哨兵，不许编一个数出来）。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    registration = edit.add_bundle(_probe_bundle())
    address = edit.add_address(
        cw.CatalogAddress(NEW_ASSET, NEW_ADDRESS, resource_type=1),
        dependency_key=registration.key_index,
    )
    assert edit.entries[address.entry_index][ce.ENTRY_DEPENDENCY_HASH] == (
        cw.INVALID_DEPENDENCY_HASH
    )
    # 抄得到的时候必须抄（真件里 76 个依赖键各有自己的值）
    table = edit.to_table()
    dependency_key = next(
        row[2] for row in table.entries() if row[2] != -1 and row[3] != cw.INVALID_DEPENDENCY_HASH
    )
    copied = edit.add_address(
        cw.CatalogAddress("Assets/Synth/Copied.prefab", "addr/copied", resource_type=1),
        dependency_key=dependency_key,
    )
    assert edit.entries[copied.entry_index][ce.ENTRY_DEPENDENCY_HASH] == edit.dependency_hash_of(
        dependency_key
    )


# --------------------------------------------------------------------------- #
# R5 · 真件上删一个**中间**的包（重编号）
# --------------------------------------------------------------------------- #
def test_r5_remove_middle_bundle_renumbers_and_the_reader_agrees(
    tmp_path: Path, real_bytes: bytes, free_bundle_name: str
) -> None:
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    before_counts = edit.counts
    before_internal = len(edit.internal_ids)
    before_index = _index(tmp_path, "before", real_bytes)

    report = edit.remove_bundle(free_bundle_name)
    after = edit.to_bytes()
    print(f"\n[R5] 删 {free_bundle_name}")
    print(f"[R5] {report.summary()}")
    assert edit.counts == (before_counts[0] - 1, before_counts[1] - 1, before_counts[2] - 1)
    assert len(edit.internal_ids) == before_internal - 1
    assert edit.self_check() == ()
    assert cw.check_pairs(edit.to_table()) == ()
    # 出字节 -> 再读回来 -> 再出字节：结构必须稳（幂等）
    assert ce.CatalogEdit.from_bytes(after).to_bytes() == after

    after_index = _index(tmp_path, "after", after)
    assert set(before_index) - set(after_index) == {free_bundle_name}
    assert not set(after_index) - set(before_index)
    changed = 0
    for key, values in before_index.items():
        if key not in after_index or values == after_index[key]:
            continue
        changed += 1
        gone = values - after_index[key]
        assert not after_index[key] - values, f"{key} 多出了东西"
        assert all(
            free_bundle_name in path or bundle == free_bundle_name for path, bundle in gone
        ), f"{key} 的变化不是「正好少了那个包」：{gone}"
    print(f"[R5] 解析结果变了的键 {changed} 个（全都只是少了这个包）；删后指向它的 0 条")
    hits = [
        (path, bundle)
        for values in after_index.values()
        for path, bundle in values
        if bundle == free_bundle_name
    ]
    assert hits == []


# --------------------------------------------------------------------------- #
# N1 / N2 / N3 · 负控：删不存在的、删会被依赖的
# --------------------------------------------------------------------------- #
def test_n1_removing_an_unknown_bundle_name_is_red() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    with pytest.raises(AssetError):
        edit.remove_bundle("no_such_assets_all_" + "0" * 32 + ".bundle")


def test_n2_removing_an_unknown_address_key_is_red() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    with pytest.raises(AssetError):
        edit.remove_address("addr/not-there")


def test_n3_a_bundle_used_as_a_dependency_key_cannot_be_removed() -> None:
    """addr/two 的依赖键就是 BETA 那条键 => 删 BETA 必须红（不许留悬空依赖）。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    assert edit.dependent_entries(1) == (4,)
    with pytest.raises(AssetError) as excinfo:
        edit.remove_bundle(BETA)
    assert "依赖" in str(excinfo.value)
    # 正控：同一个件里 GAMMA 没人依赖，删得掉
    assert edit.remove_bundle(GAMMA).key == GAMMA


def test_n3b_removing_a_bundle_key_that_is_not_a_bundle_entry_is_red() -> None:
    """GROUP 那条键指向的是资产条目（不是包）=> 走 remove_bundle 必须红。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    with pytest.raises(AssetError):
        edit.remove_bundle(GROUP)


def test_n3c_address_key_of_a_bundle_registration_must_use_remove_bundle() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    with pytest.raises(AssetError):
        edit.remove_address(ALPHA)


# --------------------------------------------------------------------------- #
# N4 / N5 / N6 · 负控：悬空引用与共用 extra
# --------------------------------------------------------------------------- #
def test_n4_a_dangling_bucket_member_makes_to_bytes_red() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    edit.buckets[0].append(len(edit.entries))  # 越界的成员下标
    with pytest.raises(AssetError):
        edit.to_bytes()


def test_n5_a_dangling_internal_id_makes_to_bytes_red() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    edit.entries[0][ce.ENTRY_INTERNAL_ID] = len(edit.internal_ids)
    with pytest.raises(AssetError):
        edit.to_bytes()


def test_n6_a_shared_extra_object_cannot_be_cut_out() -> None:
    """两条 entry 指同一条 extra 时，删其中一条不许把那一段切掉（切了就把另一条指坏）。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    shared = edit.entries[2][ce.ENTRY_EXTRA_OFFSET]
    edit.entries[3][ce.ENTRY_EXTRA_OFFSET] = shared  # addr/one 改用 GAMMA 那条 extra
    edit.assert_consistent()
    with pytest.raises(AssetError):
        edit.remove_address(ADDR_ONE)


def test_n6b_a_non_canonical_json_is_rejected() -> None:
    """不是磁盘序列化口径的 JSON 一律不收（宁可拒绝，也不要把 catalog 重排坏）。"""
    padded = json.dumps(_synth(), indent=2).encode("utf-8")
    with pytest.raises(AssetError):
        ce.CatalogEdit.from_bytes(padded)


# --------------------------------------------------------------------------- #
# P1 / P2 / P3 · 正控：共享的 internalId / 依赖组成员关系 / 与 catalog_writer 对拍
# --------------------------------------------------------------------------- #
def test_p1_removing_one_address_keeps_a_shared_internal_id(tmp_path: Path) -> None:
    """两条地址共用一个 internalId（真件 6,686 例）：删一条，那个 id 必须留着。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    before = _index(tmp_path, "p1_before", _bytes(edit.to_raw()))
    assert before[ADDR_ONE] == {(SHARED_ASSET, ALPHA), (SHARED_ASSET, BETA), (SHARED_ASSET, GAMMA)}
    edit.remove_address(ADDR_ONE)
    assert SHARED_ASSET in edit.internal_ids, "还被 addr/two 引用着，不许删"
    assert len(edit.internal_ids) == 4
    assert edit.self_check() == ()
    after = _index(tmp_path, "p1_after", edit.to_bytes())
    # addr/two 的依赖键是 BETA 那条键（不是 GROUP），所以它只解析到 BETA
    assert after[ADDR_TWO] == {(SHARED_ASSET, BETA)}
    assert ADDR_ONE not in after


def test_p1b_an_unreferenced_internal_id_is_dropped() -> None:
    """反过来：删完没人引用它了，就要跟着删（新增+删除才回得去）。"""
    fresh = ce.CatalogEdit.from_raw(_synth())
    edit = ce.CatalogEdit.from_raw(_synth())
    edit.remove_address(ADDR_ONE)
    edit.remove_address(ADDR_TWO)
    assert SHARED_ASSET not in edit.internal_ids
    # 删掉两条地址之后，剩下的三条包条目与原来的前三条**逐格相同**（含 extra 偏移）
    assert edit.entries == fresh.entries[:3]
    assert edit.internal_ids == fresh.internal_ids[:3]
    assert edit.counts == (4, 4, 3)


def test_p2_removing_a_bundle_also_drops_it_from_dependency_buckets() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    group_before = edit.key_index_of(GROUP)
    assert group_before is not None and edit.buckets[group_before] == [0, 1, 2]
    report = edit.remove_bundle(GAMMA)
    assert report.group_memberships_dropped == 1
    group_after = edit.key_index_of(GROUP)
    assert group_after == group_before - 1, "GAMMA 的键在 GROUP 之前，删完 GROUP 要前移一位"
    assert edit.buckets[group_after] == [0, 1]
    assert edit.self_check() == ()


def test_p3_add_matches_catalog_writer_byte_for_byte() -> None:
    """新增那半与 catalog_writer 逐字节对拍（两边各跑一次，产物必须一模一样）。"""
    raw = _synth()
    bundle = cw.CatalogBundle(NEW_BUNDLE, "9" * 32, 123456789, 4096, internal_name="synthnew")
    address = cw.CatalogAddress(NEW_ASSET, NEW_ADDRESS, resource_type=1)

    theirs = cw.CatalogTable.from_raw(raw)
    their_bundle = cw.apply_bundle(theirs, bundle)
    their_address = cw.apply_address(
        theirs, address, dependency_key=their_bundle.key_index, dependency_hash=777
    )

    mine = ce.CatalogEdit.from_raw(raw)
    my_bundle = mine.add_bundle(bundle)
    my_address = mine.add_address(
        address, dependency_key=my_bundle.key_index, dependency_hash=777
    )

    assert (my_bundle.key_index, my_bundle.entry_index, my_bundle.extra_offset) == (
        their_bundle.key_index,
        their_bundle.entry_index,
        their_bundle.extra_offset,
    )
    assert (my_address.key_index, my_address.entry_index) == (
        their_address.key_index,
        their_address.entry_index,
    )
    assert mine.to_raw() == theirs.to_raw()
    assert mine.to_bytes() == cw.table_bytes(theirs)


def test_p4_extra_object_length_walks_the_whole_blob() -> None:
    """extra 长度那把尺子：三条紧密排列，长度和 == 表长（表尾那条也要算得出来）。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    offsets = sorted(row[ce.ENTRY_EXTRA_OFFSET] for row in edit.entries if row[4] >= 0)
    assert offsets[0] == 0
    total = sum(ce.extra_object_length(edit.extra_data, offset) for offset in offsets)
    assert total == len(edit.extra_data)
    with pytest.raises(AssetError):
        ce.extra_object_length(edit.extra_data, len(edit.extra_data))


#: 游戏**自带**的包条目数（真件的基线；2026-10-08 实测 77）。
#: 多于它 = 这个目录里**装过东西**（装进来的包会多一条包条目）。
GAME_BUNDLE_COUNT = 77


# --------------------------------------------------------------------------- #
# R6 / R7 / P5 / N7 · **共享条目**模型（真件的常态：地址键 + GUID 键指着同一条目）
# --------------------------------------------------------------------------- #
def _find_shared_address(edit: ce.CatalogEdit) -> tuple[int, int, int] | None:
    """找一条「真件常态」的地址键：它独占 1 条资产条目，而那条条目**还有一条别的键**指着。

    返回 (键下标, 条目下标, 兄弟键下标)。
    """
    owners = edit.owners_of_entries()
    table = edit.to_table()
    for key_index in range(len(edit.keys)):
        owned = edit.owned_entries(key_index)
        if len(owned) != 1:
            continue
        entry = owned[0]
        if edit.is_bundle_entry(entry):
            continue
        others = [k for k in owners.get(entry, ()) if k != key_index]
        if len(others) != 1:
            continue
        if not isinstance(table.key_value(key_index), str):
            continue
        if not isinstance(table.key_value(others[0]), str):
            continue
        return key_index, entry, others[0]
    return None


def test_r7_live_asset_entries_are_shared_with_another_key(real_bytes: bytes) -> None:
    """规格：**真件**里每一条资产条目都还有别的键指着它（独享只出现在包条目上）。

    ⚠ 2026-10-08 实测更正（原句写「每一条资产条目都共享」—— 在**装过东西**的目录上为假）：
    现场 = `aa/PC/acvgold_assets_all_79fc33552808c30e.bundle`（21,906,177 B，2026-10-07 由
    `_work/armory/acv_install.py` 装，KEY = "ACVGold"）**还在** ⇒ 包条目 **78**（自带 77），
    键 `ACVGold` 独享 **2** 条资产条目（21275 / 21276，同桶 28937 偏移 17）。
    10-08 那次「撤净」的正则只擦了 `^(routeb|t1)` 两个前缀，把它漏了。

    ⇒ 判据分两段（**装过东西就报数，不拿真件规格判它**：装进来的条目本来就不必共享）：

    * 包条目 == `GAME_BUNDLE_COUNT` ⇒ 这是真件，**断言**规格（共享模型一变就红）；
    * 多出来 ⇒ **skip** 并报出「多的是哪几个键 + 独享的是哪几条」（`-rs` 看得见）——
      要拿回这条断言，就把装进去的包从游戏目录里撤掉（`kb/16` 有还原三步）。
    """
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    table = edit.to_table()
    owners = edit.owners_of_entries()
    bundles = [e for e in range(len(edit.entries)) if edit.is_bundle_entry(e)]
    assets = [e for e in range(len(edit.entries)) if not edit.is_bundle_entry(e)]
    shared_assets = [e for e in assets if len(owners.get(e, ())) >= 2]
    exclusive = [e for e in range(len(edit.entries)) if len(owners.get(e, ())) == 1]
    exclusive_assets = [e for e in exclusive if not edit.is_bundle_entry(e)]
    print(
        f"\n[R7] 包条目 {len(bundles)}（自带 {GAME_BUNDLE_COUNT}）· 资产条目 {len(assets):,}"
        f"（>=2 条键指着 {len(shared_assets):,}）· 独享 {len(exclusive)} 条"
        f"（其中非包条目 {len(exclusive_assets)}）"
    )
    if len(bundles) != GAME_BUNDLE_COUNT:
        culprits = sorted(
            {str(table.key_value(k)) for e in exclusive_assets for k in owners.get(e, ())}
        )
        pytest.skip(
            f"这个目录里装过东西：包条目 {len(bundles)} > 自带 {GAME_BUNDLE_COUNT}；"
            f"独享的非包条目 {exclusive_assets}，它们的键 = {culprits}"
            " ⇒ 真件规格不适用（要断言就把装进去的包撤掉）"
        )
    assert shared_assets == assets, "居然有资产条目是独享的 —— 删除规则要跟着改"
    assert all(edit.is_bundle_entry(e) for e in exclusive)
    assert len(bundles) == GAME_BUNDLE_COUNT


def test_r6_removing_a_live_address_keeps_its_shared_entry(
    tmp_path: Path, real_bytes: bytes
) -> None:
    """★ 真件常态：删掉地址键，**条目必须留着**，并把它 [5] 重指到兄弟键上。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    found = _find_shared_address(edit)
    assert found is not None, "真件里挑不出共享模型的地址（catalog 换了？）"
    key_index, entry, sibling = found
    table = edit.to_table()
    address = table.key_value(key_index)
    sibling_key = table.key_value(sibling)
    before_counts = edit.counts
    before_internal = len(edit.internal_ids)
    before_entry = list(edit.entries[entry])
    before_index = _index(tmp_path, "r6_before", real_bytes)
    before_sibling = before_index[sibling_key]

    report = edit.remove_address(address)
    after = edit.to_bytes()
    print(f"\n[R6] 删地址 {address!r}（条目 #{entry} 的兄弟键 {sibling_key!r}）")
    print(f"[R6] {report.summary()}")
    assert report.entry_indices == ()
    assert report.kept_entry_indices == (entry,)
    assert report.repointed_entries == (entry,)
    # 条目一条没少、internalId 一条没少 —— 只是键少了一条
    assert edit.counts == (before_counts[0] - 1, before_counts[1] - 1, before_counts[2])
    assert len(edit.internal_ids) == before_internal
    assert edit.entries[entry][ce.ENTRY_INTERNAL_ID] == before_entry[ce.ENTRY_INTERNAL_ID]
    # [5] 换成了兄弟键（删掉的那条键之后的下标 = sibling - 1）
    assert edit.entries[entry][ce.ENTRY_PRIMARY_KEY] == sibling - 1
    assert edit.self_check() == ()
    assert cw.check_pairs(edit.to_table()) == ()

    after_index = _index(tmp_path, "r6_after", after)
    assert address not in after_index
    assert after_index[sibling_key] == before_sibling, "兄弟键的解析结果被弄坏了"


# --------------------------------------------------------------------------- #
# P5 · 合成件上的共享模型（正控）+ N7（能红）
# --------------------------------------------------------------------------- #
ALIAS = "f69153ad65c580d49a8a77c3ad9242b1"
ADDR_SHARED = "addr/shared"
SHARED_ASSET2 = "Assets/Synth/Shared2.prefab"


def _synth_shared() -> dict:
    """两条键（地址键 + GUID 键）指着**同一条**资产条目 —— 真件的常态模型。"""
    internal = [cw.RUNTIME_PATH + cw.PC_SEGMENT + ALPHA, SHARED_ASSET2]
    extra = _blob(ALPHA, 111, 1000)
    key_blobs = [cw.key_bytes(cw.KEY_UTF8, value) for value in (ALPHA, ADDR_SHARED, ALIAS)]
    key_offsets: list[int] = []
    pos = 4
    for blob in key_blobs:
        key_offsets.append(pos)
        pos += len(blob)
    key_data = bytearray(struct.pack("<i", len(key_blobs)))
    for blob in key_blobs:
        key_data += blob
    buckets = [[0], [1], [1]]
    bucket_data = bytearray(struct.pack("<i", len(buckets)))
    for index, members in enumerate(buckets):
        bucket_data += struct.pack("<ii", key_offsets[index], len(members))
        bucket_data += struct.pack(f"<{len(members)}i", *members)
    entries = [
        [0, cw.ASSET_BUNDLE_PROVIDER, -1, cw.INVALID_DEPENDENCY_HASH, 0, 0, 0],
        [1, cw.BUNDLED_ASSET_PROVIDER, 0, 555, -1, 1, 1],
    ]
    entry_data = bytearray(struct.pack("<i", len(entries)))
    for row in entries:
        entry_data += struct.pack("<7i", *row)
    raw = _synth()
    raw["m_InternalIds"] = internal
    raw["m_KeyDataString"] = base64.b64encode(bytes(key_data)).decode("ascii")
    raw["m_BucketDataString"] = base64.b64encode(bytes(bucket_data)).decode("ascii")
    raw["m_EntryDataString"] = base64.b64encode(bytes(entry_data)).decode("ascii")
    raw["m_ExtraDataString"] = base64.b64encode(extra).decode("ascii")
    return raw


def test_p5_removing_one_of_two_keys_keeps_the_shared_entry(tmp_path: Path) -> None:
    edit = ce.CatalogEdit.from_raw(_synth_shared())
    before = _index(tmp_path, "p5_before", _bytes(edit.to_raw()))
    assert before[ADDR_SHARED] == {(SHARED_ASSET2, ALPHA)}
    assert before[ALIAS] == {(SHARED_ASSET2, ALPHA)}
    report = edit.remove_address(ADDR_SHARED)
    assert report.entry_indices == (), "共享条目绝不许跟着删"
    assert report.kept_entry_indices == (1,)
    assert report.repointed_entries == (1,)
    assert edit.counts == (2, 2, 2)
    assert edit.entries[1][ce.ENTRY_PRIMARY_KEY] == 1, "[5] 要重指到还指着它的那条键"
    assert edit.self_check() == ()
    after = _index(tmp_path, "p5_after", edit.to_bytes())
    assert ADDR_SHARED not in after
    assert after[ALIAS] == {(SHARED_ASSET2, ALPHA)}


def test_p5b_removing_the_alias_key_leaves_the_address_alone(tmp_path: Path) -> None:
    """反向：删 GUID 那条别名键 —— 它一条条目也不拥有，条目与地址键都原样。"""
    edit = ce.CatalogEdit.from_raw(_synth_shared())
    report = edit.remove_address(ALIAS)
    assert report.entry_indices == () and report.kept_entry_indices == ()
    assert edit.counts == (2, 2, 2)
    assert edit.entries[1][ce.ENTRY_PRIMARY_KEY] == 1
    after = _index(tmp_path, "p5b_after", edit.to_bytes())
    assert after[ADDR_SHARED] == {(SHARED_ASSET2, ALPHA)}
    assert ALIAS not in after


def test_n7_keeping_an_entry_that_nothing_else_references_is_red() -> None:
    """能红：独享条目写进 keep_entries => 抛（留着就成死角）。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    assert [k for k in edit.owners_of_entries()[3]] == [4]
    with pytest.raises(AssetError):
        edit._remove(  # 负控就是要戳这一层：
            kind="probe", key=ADDR_ONE, key_index=4, delete_entries=(), keep_entries=(3,)
        )


# --------------------------------------------------------------------------- #
# P6 / R8 · dependents_of：删包被拒时，用户要逐个删的那份清单（Lead 2026-10-06 定）
# --------------------------------------------------------------------------- #
def test_p6_dependents_of_lists_the_address_keys_to_delete_first() -> None:
    """合成件：addr/two 的依赖键就是 BETA 那条键 => 清单里正好是它。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    assert edit.dependents_of(BETA) == (ADDR_TWO,)
    assert edit.dependents_of(ALPHA) == (), "没人依赖 ALPHA"
    # 清单与「删包被拒」是同一件事：报错里带上条数与建议
    with pytest.raises(AssetError) as excinfo:
        edit.remove_bundle(BETA)
    message = str(excinfo.value)
    assert "1 条资产条目" in message and "dependents_of" in message
    # 逐条删是可行的（每条都是独立的原子删除）—— 删完就不再被拒
    edit.remove_address(ADDR_TWO)
    assert edit.dependents_of(BETA) == ()
    assert edit.remove_bundle(BETA).key == BETA


def test_p6b_dependents_of_an_unknown_bundle_is_red() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    with pytest.raises(AssetError):
        edit.dependents_of("no_such_assets_all_" + "0" * 32 + ".bundle")


def test_r8_dependents_of_the_real_catalog_is_the_delete_list(real_bytes: bytes) -> None:
    """真件：被依赖的那个包（实测 39/77）—— 清单非空、去重有序、每条都真的依赖它。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    groups = edit.dependency_group_keys()
    target = None
    for key_index in range(len(edit.keys)):
        owned = edit.owned_entries(key_index)
        if len(owned) == 1 and edit.is_bundle_entry(owned[0]) and key_index in groups:
            target = key_index
            break
    assert target is not None, "真件里找不到被依赖的包（catalog 换了？）"
    name = edit.to_table().key_value(target)
    addresses = edit.dependents_of(name)
    dependents = edit.dependent_entries(target)
    print(f"\n[R8] 包 {name} 被 {len(dependents)} 条条目依赖，涉及 {len(addresses)} 条地址键")
    print(f"[R8] 清单前 3：{list(addresses[:3])}")
    assert addresses == tuple(sorted(set(addresses)))
    assert addresses, "都进了依赖组却没人依赖它？"
    for address in addresses[:20]:
        key_index = edit.key_index_of(address)
        assert key_index is not None, f"{address!r} 不在键表里"
        assert any(
            edit.entries[entry][ce.ENTRY_DEPENDENCY_KEY] == target
            for entry in edit.owned_entries(key_index)
        ), f"{address!r} 并不真的依赖这个包"
    with pytest.raises(AssetError) as excinfo:
        edit.remove_bundle(name)
    assert f"{len(dependents)} 条资产条目" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# R9 / N8–N10 / P7 · 更新既有包登记（m_Crc / m_BundleSize + extra 偏移重编号）
# --------------------------------------------------------------------------- #
def _bundle_entry_of(edit: ce.CatalogEdit, prefix: str) -> tuple[int, int, str]:
    """找名字以 prefix 开头的那个包，返回 (键下标, 条目下标, 包名)。"""
    table = edit.to_table()
    for key_index in range(len(edit.keys)):
        value = table.key_value(key_index)
        if not isinstance(value, str) or not value.startswith(prefix):
            continue
        owned = edit.owned_entries(key_index)
        if len(owned) == 1 and edit.is_bundle_entry(owned[0]):
            return key_index, owned[0], value
    pytest.skip(f"真件里找不到 {prefix}* 这个包（catalog 换了？）")


def _extras_of(edit: ce.CatalogEdit) -> dict[int, dict]:
    """条目下标 -> 它那条 extra 解出来的字段（用现有读侧解，不用被测代码）。"""
    table = edit.to_table()
    return {
        index: table.entry_options(index)
        for index, row in enumerate(edit.entries)
        if row[ce.ENTRY_EXTRA_OFFSET] >= 0
    }


def test_r9_real_update_bundle_touches_only_that_record(real_bytes: bytes) -> None:
    """真件：unitlabels 的 m_Crc +1（位数不变 ⇒ 等长）—— 条目表**逐字节**不动。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    _key, entry, name = _bundle_entry_of(edit, "unitlabels")
    table = edit.to_table()
    options = table.entry_options(entry)
    before_entries = edit.entry_data_bytes()
    before_extra = bytes(edit.extra_data)
    before_extras = _extras_of(edit)
    offset = edit.entries[entry][ce.ENTRY_EXTRA_OFFSET]
    size = ce.extra_object_length(before_extra, offset)

    report = edit.update_bundle(
        name, crc=options["m_Crc"] + 1, bundle_size=options["m_BundleSize"]
    )
    print(f"\n[R9] {report.summary()}")
    print(f"[R9] 那条 extra @{offset} {size} B；m_Crc {options['m_Crc']} -> {options['m_Crc'] + 1}")
    assert report.changed_fields == ("m_Crc",)
    assert report.delta == 0 and report.renumbered_entries == ()
    # ① 条目表逐字节不动（21,274 条一条没动）
    assert edit.entry_data_bytes() == before_entries
    # ② extra 表只有那一段变了，前后逐字节相同
    after_extra = bytes(edit.extra_data)
    assert len(after_extra) == len(before_extra)
    assert after_extra[:offset] == before_extra[:offset]
    assert after_extra[offset + size :] == before_extra[offset + size :]
    assert after_extra[offset : offset + size] != before_extra[offset : offset + size]
    # ③ 读回来：只有那一条的 m_Crc 变，其余 76 条逐字段相同
    after_extras = _extras_of(edit)
    assert set(after_extras) == set(before_extras)
    for other, got in after_extras.items():
        if other == entry:
            assert got["m_Crc"] == options["m_Crc"] + 1
            assert {k: v for k, v in got.items() if k != "m_Crc"} == {
                k: v for k, v in before_extras[other].items() if k != "m_Crc"
            }
        else:
            assert got == before_extras[other], f"entry {other} 的 extra 被动了"
    assert edit.self_check() == ()
    # 出字节再读回来，结构仍稳
    assert ce.CatalogEdit.from_bytes(edit.to_bytes()).to_bytes() == edit.to_bytes()


def test_r9b_update_back_is_byte_identical(real_bytes: bytes) -> None:
    """对偶：+1 再改回原值 ⇒ 与原文件**逐字节相等**。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    _key, entry, name = _bundle_entry_of(edit, "unitlabels")
    options = edit.to_table().entry_options(entry)
    edit.update_bundle(name, crc=options["m_Crc"] + 1, bundle_size=options["m_BundleSize"])
    changed = edit.to_bytes()
    assert changed != real_bytes
    edit.update_bundle(name, crc=options["m_Crc"], bundle_size=options["m_BundleSize"])
    back = edit.to_bytes()
    print(f"\n[R9B] 原文件 {len(real_bytes):,} B / 改后 {len(changed):,} B / 改回 {len(back):,} B")
    print(f"[R9B] 改回逐字节 == 原文件：{back == real_bytes}")
    assert back == real_bytes


def test_r9c_crossing_a_digit_boundary_renumbers_the_offsets(real_bytes: bytes) -> None:
    """长度变化那一档：m_Crc 999999999 -> 1000000000（9 位 -> 10 位 = +2 B，UTF-16）。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    _key, entry, name = _bundle_entry_of(edit, "unitlabels")
    options = edit.to_table().entry_options(entry)
    before_extra = bytes(edit.extra_data)
    before_offsets = [row[ce.ENTRY_EXTRA_OFFSET] for row in edit.entries]
    before_extras = _extras_of(edit)
    offset = before_offsets[entry]
    after_it = [e for e, o in enumerate(before_offsets) if o > offset]

    edit.update_bundle(name, crc=999999999, bundle_size=options["m_BundleSize"])
    assert edit.entries[entry][ce.ENTRY_EXTRA_OFFSET] == offset, "等长档起点不该动"
    report = edit.update_bundle(name, crc=1000000000, bundle_size=options["m_BundleSize"])
    print(f"\n[R9C] {report.summary()}")
    print(f"[R9C] 起点 {offset}；跟着平移 {len(after_it)} 条 = {after_it}")
    assert report.changed_fields == ("m_Crc",)
    assert report.delta == 2, "9 位 -> 10 位在 UTF-16LE 里正好 +2 字节"
    assert report.renumbered_entries == tuple(after_it)
    assert len(edit.extra_data) == len(before_extra) + 2
    assert edit.entries[entry][ce.ENTRY_EXTRA_OFFSET] == offset
    for other in after_it:
        assert edit.entries[other][ce.ENTRY_EXTRA_OFFSET] == before_offsets[other] + 2
    # 77 条 extra 逐字段读回来：只有那一条变了，其余一条不差（重编号对不对就看这一条）
    after_extras = _extras_of(edit)
    assert after_extras[entry]["m_Crc"] == 1000000000
    for other, got in after_extras.items():
        if other != entry:
            assert got == before_extras[other], f"entry {other} 的 extra 被平移弄坏了"
    assert edit.self_check() == ()
    # 对偶（负 delta）：改回 9 位原值 => 逐字节回原文件
    edit.update_bundle(name, crc=options["m_Crc"], bundle_size=options["m_BundleSize"])
    assert edit.to_bytes() == real_bytes


def test_p7_updating_with_the_same_values_is_a_no_op(real_bytes: bytes) -> None:
    """正控：一个字段都没变时，重新编码必须逐字节回到原样（这条不成立就不许写）。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    _key, entry, name = _bundle_entry_of(edit, "unitlabels")
    options = edit.to_table().entry_options(entry)
    report = edit.update_bundle(
        name, crc=options["m_Crc"], bundle_size=options["m_BundleSize"], m_hash=options["m_Hash"]
    )
    assert report.changed_fields == ()
    assert report.delta == 0 and report.renumbered_entries == ()
    assert edit.to_bytes() == real_bytes


def test_n8_a_stale_extra_offset_after_the_update_goes_red(real_bytes: bytes) -> None:
    """能红：改完长度**故意漏改一条**后面的偏移 => to_bytes() 必须抛。"""
    edit = ce.CatalogEdit.from_bytes(real_bytes)
    _key, entry, name = _bundle_entry_of(edit, "unitlabels")
    options = edit.to_table().entry_options(entry)
    before_offsets = [row[ce.ENTRY_EXTRA_OFFSET] for row in edit.entries]
    offset = before_offsets[entry]
    edit.update_bundle(name, crc=1000000000, bundle_size=options["m_BundleSize"])
    victim = min((e for e in range(len(before_offsets)) if before_offsets[e] > offset),
                 key=lambda e: before_offsets[e])
    good = edit.entries[victim][ce.ENTRY_EXTRA_OFFSET]
    assert good == before_offsets[victim] + 2
    edit.entries[victim][ce.ENTRY_EXTRA_OFFSET] = before_offsets[victim]  # 漏改这一条
    with pytest.raises(AssetError):
        edit.to_bytes()
    edit.entries[victim][ce.ENTRY_EXTRA_OFFSET] = good  # 改回来就绿
    assert edit.self_check() == ()


def test_n9_update_bundle_rejects_what_it_should() -> None:
    edit = ce.CatalogEdit.from_raw(_synth())
    with pytest.raises(AssetError):
        edit.update_bundle("no_such_assets_all_" + "0" * 32 + ".bundle", crc=1, bundle_size=1)
    with pytest.raises(AssetError):
        edit.update_bundle(ADDR_ONE, crc=1, bundle_size=1)  # 资产地址键，不是包登记
    with pytest.raises(AssetError):
        edit.update_bundle(GROUP, crc=1, bundle_size=1)  # 依赖组键，一条条目也不拥有


def test_n10_update_bundle_refuses_a_shared_extra() -> None:
    """能红：两条条目共用同一条 extra 时，改它会连带改别人 => 抛。"""
    edit = ce.CatalogEdit.from_raw(_synth())
    edit.entries[1][ce.ENTRY_EXTRA_OFFSET] = edit.entries[0][ce.ENTRY_EXTRA_OFFSET]
    edit.assert_consistent()
    with pytest.raises(AssetError):
        edit.update_bundle(ALPHA, crc=1, bundle_size=1)
