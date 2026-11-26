r"""`core/assets/catalog_bin_write.py`：二进制 catalog 的**写侧**（Addressables 2.x）。

**判据一行**：`.venv\Scripts\python.exe -m pytest tests/test_catalog_bin_write.py -q -n 0`
-> 收集用例数 **从 0（本文件之前不存在）变 N**，全 passed；真件那几条在装了 1.2.1 的机器上跑，
没装 / 还是 1.2.0（`catalog.json`）时**跳过**（`pytest.skip`，不是「绿」）。

| 用例 | 判据 |
|---|---|
| 判别位 | `.bin` 头 -> True；JSON 头 / 空 / 短 / 未知版本 -> False（写侧只认二进制） |
| ★ ① 往返（正控） | 真件重编码 -> **逐字节相同**（2,755,558 B / 同 sha256；另有不设闸的一条） |
| ★ 外部 oracle | `hash128_hex(整份 catalog.bin)` == `aa/catalog.hash`（官方源码写死的绑定） |
| ★ ② 新增别名键 | 新件能被**产品读器**查到新键、**其它键 0 条变动** |
| 删除 | 被删键查不到、**其它键 0 条变动** |
| ★ ③ 负控（尺子能红） | 去重表关掉 -> 往返判据**必须报红**（16,851,300 B ≠ 真件） |
| fail-closed | 键已存在 / 包不在 catalog 里 / 未知 resource-type / 空字段 ⇒ `AssetError` |
| ★ ④ 依赖集并集（正控） | 参照依赖里没有新包 ⇒ 加完地址后新包**必须**在依赖数组里 |
| ★ ④ 不重复 | 新包本来就在参照依赖里 ⇒ 只出现一次，集合不多不少 |
| ★ ④ 负控 | 去掉并集（恢复「整份照抄」）⇒ fail-closed 断言**必须**报 `AssetError` |
| CLI 接线 | `cli.address_edit.plan_add` / `plan_delete` 遇到 `.bin` **不再发 UnicodeDecodeError** |

⛔ 真件那几条**只读**游戏目录（一个字节都不写），产物全落 `tmp_path`；⛔ 不开 3.37 GB 的大包。

⚠ **负控 ③ 里藏着一处必须说清的边界**：把哈希**种子**换掉，往返判据**照样是绿的** ——
去重是"同一内容 -> 同一 hash"，换种子后同一内容仍映射到同一个 key，去重行为一字不变。
所以「往返逐字节相同」**不能**证明哈希实现与 Unity 一致；那一件事只由 `aa/catalog.hash`
这条 **外部 oracle** 建立（见 `test_real_hash_matches_the_catalog_hash_oracle`）。
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

from ba_mod_maker.cli import address_edit as ae
from ba_mod_maker.core.assets import catalog_bin_write as cbw
from ba_mod_maker.core.assets.catalog_bin import CATALOG_BIN_MAGIC, catalog_index
from ba_mod_maker.core.errors import AssetError

GAME = Path(r"D:\Steam\steamapps\common\broken_arrow")
AA = GAME / "BrokenArrow_Data" / "StreamingAssets" / "aa"
CATALOG_BIN = AA / "catalog.bin"
CATALOG_HASH = AA / "catalog.hash"

#: 实测读数（1.2.1 openbeta，**2026-10-18 重记**）—— 换版本时这几个数要跟着改，**别放宽**。
#:
#: ⚠ 上一组（2026-10-13）是 2,345,239 B / `6a983c92e520fbe3…` / (22492, 22492, 17963)。
#: 2026-10-17 11:30 Steam 更新后游戏发了**新的一份** `aa/catalog.bin`（mtime 2026-10-17 11:30:25），
#: `aa/catalog.hash` 也随之变成 `1b56bf58011095bcfe77be2b34b05b93`。
#:
#: 重记命令（在仓根跑；**只读**游戏目录）：
#:
#:     .venv\Scripts\python.exe tools\catalog_baseline.py
#:     .venv\Scripts\python.exe -m pytest tests/test_catalog_bin_write.py -q -n 0
#:
#: 本次读数 = 2,755,558 B · 6adccfb6… · counts (27764, 27764, 20614) · `catalog.hash` = 1b56bf58…
#: ⚠ 重记前先确认**往返仍然逐字节相同**（`catalog_baseline.py` 的 `hash128_matches_file = True`
#: 只说明哈希对；往返相同由上面那条 pytest 的 `①` 那一条守）——写侧没被这次更新打坏才轮到改数。
EXPECTED_BYTES = 2_755_558
EXPECTED_SHA256 = "6adccfb6a2b580c7e28b48e0a26b302898ba47c49168ecab67ff9625b43a45cf"
EXPECTED_COUNTS = (27764, 27764, 20614)
#: 真件里那条资产地址的键（借来当"别名"的靶子）。
SAMPLE_KEY = "Assets/EditorSystem/Editor2/A_MissionEditor.prefab"

#: 真机事故那条链的参照条目：`newasset add-model --key Phase1Box --unit RU_T72B`。
#: 真件读数（实测）：依赖 5 个包，头一个是 `units_assets_all_ebe39db6…bundle`、
#: 依赖 hash 0 —— **我们那个新包不在里面** ⇒ 游戏去 T-72B 的包里找新资产。
REF_KEY = "RU_T72B"
#: 全新包名（`add_bundle` 要求 `_all_` 尾段是 32 位十六进制；真件 76/76 都这样）。
NEW_BUNDLE = "ba_mod_maker_depset_probe_assets_all_0f1e2d3c4b5a69788796a5b4c3d2e1f0.bundle"


def _need_bin() -> bytes:
    if not CATALOG_BIN.is_file():
        pytest.skip("本机没有 aa/catalog.bin（没装 1.2.1，或还是 catalog.json）")
    return CATALOG_BIN.read_bytes()


def _need_shipped(raw: bytes) -> None:
    """**基线条目的前提**：现场那份 `aa/catalog.bin` 就是**出厂件**（游戏自己发的那一份）。

    ⚠ 这不是「跳过去就能绿」的阀门，而是**出处闸**：基线数字（`EXPECTED_BYTES` /
    `EXPECTED_SHA256` / `EXPECTED_COUNTS`）描述的是出厂件；只要跑过
    `newasset add-model`（往游戏里真装一份资产），现场那份就多出条目
    —— 实测 2,345,239 -> 2,345,771 B / 计数 (22492,22492,17963) -> (22494,22494,17965)。
    拿装过的文件去比出厂读数：红了没有信息，绿了更没有。

    ⛔ **2026-10-18 改造（原来这里是个假绿口子）**：旧版只写一句「不是出厂件」就 skip，
    于是 **sha 基线过期之后整个模块永远 skip、永远全绿** —— 2026-10-17 11:30 游戏更新后
    这个模块就是这么假绿的（2,755,558 B 的现场件被当成 2,345,239 B 的出厂件之外的东西）。
    ⇒ 现在两点改掉：① 基线已重记成新一代出厂件（sha 与现场相符 ⇒ **真跑**）；
    ② 闸不通过时把**三组读数逐字报出来**（现场 vs 期望），让两种情形在 skip 理由里一眼能分：

    * sha 不符**且计数也大改** ⇒ 多半是**游戏换代** ⇒ 按文件顶部那条重记命令重记基线；
    * sha 不符**但计数只是 +2/+2/+2 那种小幅** ⇒ 是**我们装过**⇒
      `newasset restore --game <游戏> --project-root <工程>` 还原后再跑。

    ★ 与出处无关的那一条**不设闸**：`test_real_roundtrip_is_byte_identical_whatever_is_installed`
    —— 不管现场是哪一份，重编码都必须逐字节回到原样（换代/装机都不会把它变成 skip）。
    """
    got_sha = hashlib.sha256(raw).hexdigest()
    if got_sha == EXPECTED_SHA256:
        return
    pytest.skip(
        f"现场 aa/catalog.bin 不是出厂件：sha {got_sha}（期望 {EXPECTED_SHA256}）· "
        f"字节 {len(raw):,}（期望 {EXPECTED_BYTES:,}）· "
        f"计数 {cbw.counts(raw)}（期望 {EXPECTED_COUNTS}）"
        " ⇒ 基线条目的前提不成立（游戏换代 ⇒ tools/catalog_baseline.py 重记；"
        "是我们装过 ⇒ newasset restore 还原）"
    )


def test_real_roundtrip_is_byte_identical_whatever_is_installed(
    raw: bytes, roundtripped: bytes
) -> None:
    """★ **不设出处闸**的那一条：现场是哪一份 `aa/catalog.bin` 都必须逐字节往返相同。

    为什么要单列：`test_real_roundtrip_is_byte_identical` 被出厂 sha 闸住（`EXPECTED_BYTES` /
    同一个 sha），一旦游戏换代而基线没跟着重记，那条会 skip 而不是报红 —— 2026-10-17 就发生过。
    这一条不引用任何 `EXPECTED_*`：写侧被打坏 / 新格式读不懂，它会**直接红**。
    """
    assert roundtripped == raw, "重编码产物与现场那份不逐字节相同 —— 写侧坏了，别拿它去改地址"
    assert cbw.counts(roundtripped) == cbw.counts(raw), "往返件的计数头变了（键/桶/条目）"
    assert cbw.key_names(roundtripped) == cbw.key_names(raw)


@pytest.fixture(scope="module")
def raw() -> bytes:
    """真件字节（**只读**；没装就整模块跳过）。"""
    return _need_bin()


@pytest.fixture(scope="module")
def roundtripped(raw: bytes) -> bytes:
    """原样往返的产物 —— 几条用例共用（一次 5 秒的重编码别算三遍）。"""
    return cbw.roundtrip(raw)


# --------------------------------------------------------------------------- #
# 判别位（不需要游戏）
# --------------------------------------------------------------------------- #
def test_discriminator_accepts_a_binary_head() -> None:
    head = struct.pack("<8I", CATALOG_BIN_MAGIC, 2, 36, 179976, 1, 2, 3, 4)
    assert cbw.looks_like_catalog_bin(head) is True


def test_discriminator_rejects_json_and_short_heads() -> None:
    """写侧只认二进制：JSON 那份（前 4 字节 0x7B）走的是**另一条**路。"""
    assert cbw.looks_like_catalog_bin(b"{") is False
    assert cbw.looks_like_catalog_bin(b"") is False
    head = struct.pack("<8I", CATALOG_BIN_MAGIC, 99, 36, 0, 1, 2, 3, 4)
    assert cbw.looks_like_catalog_bin(head) is False


def test_roundtrip_rejects_a_file_that_is_not_a_catalog_bin() -> None:
    """负控：不是这个格式就别装作能读（`struct.error` 也不行，要 `AssetError`）。"""
    with pytest.raises((AssetError, ValueError, struct.error)):
        cbw.roundtrip(b"not a catalog at all" * 4)


# --------------------------------------------------------------------------- #
# ★ ① 往返逐字节相同（真件·正控）
# --------------------------------------------------------------------------- #
def test_real_roundtrip_is_byte_identical(raw: bytes, roundtripped: bytes) -> None:
    """**这是本模块的核心判据**：读成无损模型再按官方书写顺序写回，必须逐字节回到原样。"""
    _need_shipped(raw)
    assert len(raw) == EXPECTED_BYTES
    assert len(roundtripped) == EXPECTED_BYTES
    assert roundtripped == raw, "重编码产物与真件不逐字节相同 —— 写侧坏了，别拿它去改地址"
    assert hashlib.sha256(roundtripped).hexdigest() == EXPECTED_SHA256
    assert hashlib.sha256(roundtripped).hexdigest() == hashlib.sha256(raw).hexdigest()


def test_real_counts_match_the_reader(raw: bytes, roundtripped: bytes) -> None:
    """往返件与真件的计数**逐项**相同（键 / 桶 / 条目）。"""
    _need_shipped(raw)
    assert cbw.counts(raw) == EXPECTED_COUNTS
    assert cbw.counts(roundtripped) == EXPECTED_COUNTS
    assert cbw.key_names(roundtripped) == cbw.key_names(raw)


# --------------------------------------------------------------------------- #
# ★ 外部 oracle：aa/catalog.hash（比往返更强的一条尺子）
# --------------------------------------------------------------------------- #
def test_real_hash_matches_the_catalog_hash_oracle(raw: bytes) -> None:
    """官方 `BuildScriptPackedMode.cs:690` 把 `HashingMethods.Calculate(字节)` 写进 `.hash`。

    判据：我们自己那把 SpookyHash V2 算出来的 32 位十六进制 **逐字**等于那个文件。
    ⚠ 这条是**外部**尺子 —— 往返相同**不能**替代它（换哈希种子往返照样绿）。
    """
    if not CATALOG_HASH.is_file():
        pytest.skip("本机没有 aa/catalog.hash")
    assert cbw.hash128_hex(raw) == CATALOG_HASH.read_text().strip()


# --------------------------------------------------------------------------- #
# ★ ② 新增一条别名键：新键可查 + 其它键 0 条变动
# --------------------------------------------------------------------------- #
def _index_of(data: bytes, tmp_path: Path, name: str) -> dict:
    p = tmp_path / name
    p.write_bytes(data)
    return catalog_index(p)


def test_real_add_alias_key_keeps_every_other_key(raw: bytes, tmp_path: Path) -> None:
    """新增一条**别名键**（同一个资产路径，换个键名）-> 新键可查 / 其它键 0 条变动。"""
    _need_shipped(raw)
    names = cbw.key_names(raw)
    if SAMPLE_KEY not in names:
        pytest.skip(f"这份 catalog 里没有 {SAMPLE_KEY}（版本换了就按新键改）")
    before = catalog_index(CATALOG_BIN)
    alias = "BA_Mod_Maker/alias-of-A_MissionEditor"
    bundle = next(k for k in names if k.startswith("units_assets_all"))

    plan = cbw.add_address(raw, key=alias, asset_path=SAMPLE_KEY, bundle_name=bundle)
    assert plan.action == "add"
    assert plan.counts_before == EXPECTED_COUNTS
    assert plan.counts_after == (
        EXPECTED_COUNTS[0] + 1,
        EXPECTED_COUNTS[1] + 1,
        EXPECTED_COUNTS[2] + 1,
    )

    after = _index_of(plan.after, tmp_path, "alias.bin")
    assert alias in after, "新增的键必须能被产品读器查到"
    assert {path for path, _b in after[alias]} == {SAMPLE_KEY}
    # 依赖声明按口径② 照抄了"同路径既有条目"那一份（A_MissionEditor 有 23 个候选包）
    # ⇒ 别名键与原键应当给出**同一组**候选包，而不是只有一个。
    assert after[alias] == before[SAMPLE_KEY], "别名键与原键给的候选包不一致"
    assert {b for _p, b in after[alias]} == {b for _p, b in before[SAMPLE_KEY]}
    changed = [k for k in before if after.get(k) != before[k]]
    assert changed == [], f"新增一条键却动了别的键：{changed[:5]}"


def test_real_delete_key_keeps_every_other_key(raw: bytes, tmp_path: Path) -> None:
    """删除一条键 -> 查不到 / 其它键 0 条变动（条目表不动，见模块 docstring 的口径）。"""
    _need_shipped(raw)
    victim = cbw.key_names(raw)[0]
    before = catalog_index(CATALOG_BIN)
    plan = cbw.delete_address(raw, key=victim)
    assert plan.counts_after == (EXPECTED_COUNTS[0] - 1, EXPECTED_COUNTS[1] - 1, EXPECTED_COUNTS[2])
    after = _index_of(plan.after, tmp_path, "deleted.bin")
    assert victim not in after
    changed = [k for k in before if k != victim and after.get(k) != before[k]]
    assert changed == [], f"删一条键却动了别的键：{changed[:5]}"


# --------------------------------------------------------------------------- #
# ★ ③ 负控：尺子必须能红
# --------------------------------------------------------------------------- #
def test_negative_control_dedup_off_turns_the_roundtrip_red(
    raw: bytes, roundtripped: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """故意把内容去重表关掉（`existingValues` 永远 miss）-> 往返判据**必须报红**。

    顺带自证这条判据不是永远绿的：正控（`roundtripped`）与真件逐字节相同，
    这一版的产物却大 6 倍（重记后 16,851,300 B；2,345,239 B 那一代是 14,147,284 B）且与真件不同。
    """
    _need_shipped(raw)
    assert roundtripped == raw, "正控得先绿，负控才有意义"

    original = cbw.Writer._write_internal

    def never_dedup(self, pdata: bytes, prefix: bool) -> int:
        if prefix:
            off = len(self.buf)
            self.buf += struct.pack("<I", len(pdata)) + pdata
            return off + 4
        if not pdata:
            return cbw.U32MAX
        off = len(self.buf)
        self.buf += pdata
        return off

    monkeypatch.setattr(cbw.Writer, "_write_internal", never_dedup)
    try:
        broken = cbw.roundtrip(raw)
    finally:
        monkeypatch.setattr(cbw.Writer, "_write_internal", original)

    assert len(broken) == 16_851_300
    assert broken != raw, "去重表关掉了还逐字节相同 —— 这条判据根本没在量东西"


# --------------------------------------------------------------------------- #
# ★ ④ 依赖集：`--bundle` 那个包条目必须**永远**在依赖数组里（真机 1.2.1 事故）
# --------------------------------------------------------------------------- #
def _ref_model_entry(raw: bytes) -> tuple[cbw.BinModel, cbw.BinEntry]:
    """整份模型 + `REF_KEY` 那条参照条目（多数用例两样都要）。"""
    model = cbw.read_model(raw)
    names = [k.name for k in model.keys]
    if REF_KEY not in names:
        pytest.skip(f"这份 catalog 里没有参照键 {REF_KEY}（版本换了就按新键改）")
    return model, model.entries[model.keys[names.index(REF_KEY)].ranks[0]]


def _bundle_rank(model: cbw.BinModel, name: str) -> int:
    """包名（= 键名）-> 那个包条目的下标。"""
    return model.keys[[k.name for k in model.keys].index(name)].ranks[0]


def _bundles_outside(model: cbw.BinModel, entry: cbw.BinEntry) -> list[str]:
    """键表里的 `.bundle` 包名中**不在** `entry` 依赖集里的那些（正控要造的情形）。"""
    inside = {model.entries[r].primary_key for r in entry.dep_ranks}
    return [
        k.name
        for k in model.keys
        if (k.name or "").endswith(".bundle") and k.name not in inside
    ]


def test_add_address_declares_the_brand_new_bundle(raw: bytes, tmp_path: Path) -> None:
    """★ 正控（真机事故的形状）：先登记新包、再加地址，新包**必须**在依赖数组里、且排第一。

    现场（1.2.1 真机）：`newasset add-model --key Phase1Box --unit RU_T72B` 之后游戏报
    `Error: Addressable prefab 'Phase1Box' was not loaded!`（×6），而那个新包
    **从头到尾没被打开过** —— 因为依赖集整份照抄了 `RU_T72B` 那 5 个包，里面没有它自己，
    于是游戏去 T-72B 的 `units_assets_all` 里找 `Phase1Box.prefab`。
    """
    model, ref_entry = _ref_model_entry(raw)
    ref_ranks = tuple(ref_entry.dep_ranks)
    assert ref_ranks, "参照条目没有依赖集 —— 本用例的前提不成立"
    facts = next(
        e
        for e in model.entries
        if e.extra is not None and (e.internal_id or "").endswith(".bundle")
    )
    registered = cbw.add_bundle(
        raw,
        bundle_name=NEW_BUNDLE,
        m_hash=facts.extra.hash_bytes.hex(),
        crc=int(facts.extra.crc),
        bundle_size=int(facts.extra.bundle_size),
        internal_name=facts.extra.bundle_name or "depset-probe",
    )
    probe_key = "BA_Mod_Maker/depset-probe"
    probe_path = "Assets/BA_Mod_Maker/depset-probe.prefab"
    plan = cbw.add_address(
        registered.after,
        key=probe_key,
        asset_path=probe_path,
        bundle_name=NEW_BUNDLE,
        resource_type=1,
        dependency_source_key=REF_KEY,
    )
    after = cbw.read_model(plan.after)
    deps = tuple(after.entries[plan.entry_index].dep_ranks)
    assert deps[0] == registered.bundle_entry_index, (
        "新包没排在依赖数组第一位 —— 口径是「先放 --bundle 那个包条目，再追加参照条目的依赖」"
    )
    assert deps == (registered.bundle_entry_index, *ref_ranks), (
        "依赖数组不是「新包 ∪ 参照条目依赖」：参照条目原有的次序被打乱、或有条目丢失"
    )
    # 机械读法：产品自己的读器（键 -> {(资产路径, 包名)}）里必须出现新包
    index = _index_of(plan.after, tmp_path, "depset-probe.bin")
    assert (probe_path, NEW_BUNDLE) in index[probe_key], (
        "新地址的依赖数组里没有新包 —— 装上去游戏会去参照条目的包里找资产"
    )
    ref_bundle_names = {model.entries[r].primary_key for r in ref_ranks}
    assert {(probe_path, n) for n in ref_bundle_names} <= index[probe_key], (
        "并集把参照条目原有的依赖丢了（只许多，不许少）"
    )


def test_add_address_declares_the_new_bundle_on_the_same_path_branch(raw: bytes) -> None:
    """分支 ②（没给 `--dependency-hash-from`、但这条资产路径已有条目）同样必须并入新包。"""
    model = cbw.read_model(raw)
    names = [k.name for k in model.keys]
    if SAMPLE_KEY not in names:
        pytest.skip(f"这份 catalog 里没有 {SAMPLE_KEY}（版本换了就按新键改）")
    same = model.entries[model.keys[names.index(SAMPLE_KEY)].ranks[0]]
    outside = _bundles_outside(model, same)
    if not outside:
        pytest.skip("这份 catalog 里没有「不在同路径条目依赖里」的包 —— 前提不成立")
    bundle = outside[0]
    bundle_rank = _bundle_rank(model, bundle)
    plan = cbw.add_address(
        raw,
        key="BA_Mod_Maker/depset-same-path",
        asset_path=SAMPLE_KEY,
        bundle_name=bundle,
    )
    dep_ranks = tuple(cbw.read_model(plan.after).entries[plan.entry_index].dep_ranks)
    assert bundle_rank in dep_ranks, "分支② 照抄同路径既有条目 ⇒ 新包又没进依赖数组（同一个缺陷）"
    assert set(dep_ranks) == set(same.dep_ranks) | {bundle_rank}, "并集该是「新包 ∪ 既有依赖」"
    assert len(dep_ranks) == len(set(dep_ranks)), f"依赖数组里有重复条目：{dep_ranks}"


def test_add_address_does_not_duplicate_a_bundle_the_reference_already_has(raw: bytes) -> None:
    """不重复：新包**本来就在**参照条目的依赖里 ⇒ 结果里只出现一次，集合不多不少。"""
    model, ref_entry = _ref_model_entry(raw)
    ref_ranks = tuple(ref_entry.dep_ranks)
    inside = [
        model.entries[r].primary_key
        for r in ref_ranks
        if (model.entries[r].primary_key or "").endswith(".bundle")
    ]
    if not inside:
        pytest.skip("参照条目的依赖里没有包名条目 —— 本用例的前提不成立")
    bundle = inside[0]
    bundle_rank = _bundle_rank(model, bundle)
    plan = cbw.add_address(
        raw,
        key="BA_Mod_Maker/depset-dedup",
        asset_path="Assets/BA_Mod_Maker/depset-dedup.prefab",
        bundle_name=bundle,
        dependency_source_key=REF_KEY,
    )
    dep_ranks = tuple(cbw.read_model(plan.after).entries[plan.entry_index].dep_ranks)
    assert dep_ranks.count(bundle_rank) == 1, f"新包在依赖数组里出现了不止一次：{dep_ranks}"
    assert len(dep_ranks) == len(set(dep_ranks)), f"依赖数组里有重复条目：{dep_ranks}"
    assert set(dep_ranks) == set(ref_ranks), "新包本来就在依赖里时，并集既不该多也不该少"


def test_negative_control_dropping_the_union_turns_the_guard_red(
    raw: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 负控：把并集去掉（恢复成「整份照抄参照条目」）⇒ fail-closed 断言**必须**报错。

    `_merge_bundle_deps` 是本模块里**唯一**的并集处；换成 `lambda _b, inherited: tuple(inherited)`
    就是修前那条行为（真机事故：依赖集与 `RU_T72B` 逐个相同，里面没有我们自己那个包）。
    先自证尺子能红（只照抄 ⇒ 新包确实不在依赖里），再要 `add_address` 抛 ——
    ⛔ 静默写出一份「依赖里没有新包」的 catalog 正是那个事故。
    """
    model, ref_entry = _ref_model_entry(raw)
    ref_ranks = tuple(ref_entry.dep_ranks)
    outside = _bundles_outside(model, ref_entry)
    if not outside:
        pytest.skip("参照条目的依赖吃掉了所有包 —— 本用例的前提不成立")
    bundle = outside[0]
    bundle_rank = _bundle_rank(model, bundle)

    monkeypatch.setattr(cbw, "_merge_bundle_deps", lambda _b, inherited: tuple(inherited))
    pre_fix = cbw._merge_bundle_deps((bundle_rank,), ref_ranks)
    assert bundle_rank not in pre_fix, "负控的前提：只照抄时新包确实不在依赖数组里"
    with pytest.raises(AssetError, match="依赖数组里没有新包"):
        cbw.add_address(
            raw,
            key="BA_Mod_Maker/depset-negative",
            asset_path="Assets/BA_Mod_Maker/depset-negative.prefab",
            bundle_name=bundle,
            dependency_source_key=REF_KEY,
        )

# --------------------------------------------------------------------------- #
# fail-closed（真件上跑，守卫要真能挡住）
# --------------------------------------------------------------------------- #
def test_add_refuses_a_key_that_is_already_there(raw: bytes) -> None:
    with pytest.raises(AssetError):
        cbw.add_address(
            raw, key=SAMPLE_KEY, asset_path=SAMPLE_KEY, bundle_name="whatever.bundle"
        )


def test_add_refuses_an_empty_field(raw: bytes) -> None:
    with pytest.raises(AssetError):
        cbw.add_address(raw, key="  ", asset_path="Assets/x.prefab", bundle_name="b.bundle")
    with pytest.raises(AssetError):
        cbw.add_address(raw, key="k", asset_path="", bundle_name="b.bundle")
    with pytest.raises(AssetError):
        cbw.add_address(raw, key="k", asset_path="Assets/x.prefab", bundle_name="")


def test_add_refuses_a_bundle_that_is_not_registered(raw: bytes) -> None:
    with pytest.raises(AssetError):
        cbw.add_address(
            raw,
            key="BA_Mod_Maker/new",
            asset_path="Assets/BA_Mod_Maker/new.prefab",
            bundle_name="no_such_bundle_hash.bundle",
        )


def test_add_refuses_an_unknown_resource_type(raw: bytes) -> None:
    """二进制版的类型是**真的 Unity 类型名**（不是 JSON 那种下标）—— 表外的取值不猜。"""
    bundle = next(k for k in cbw.key_names(raw) if k.startswith("units_assets_all"))
    with pytest.raises(AssetError):
        cbw.add_address(
            raw, key="BA_Mod_Maker/new", asset_path="Assets/x.prefab",
            bundle_name=bundle, resource_type=4,
        )


def test_delete_refuses_an_unknown_key(raw: bytes) -> None:
    with pytest.raises(AssetError):
        cbw.delete_address(raw, key="THIS_KEY_DOES_NOT_EXIST_0123456789")


# --------------------------------------------------------------------------- #
# CLI 接线：address add / delete 遇到 .bin 不再抛 UnicodeDecodeError
# --------------------------------------------------------------------------- #
def test_cli_plan_add_routes_the_binary_catalog(raw: bytes, tmp_path: Path) -> None:
    """接线前这里抛的是 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x89`。"""
    names = cbw.key_names(raw)
    if SAMPLE_KEY not in names:
        pytest.skip(f"这份 catalog 里没有 {SAMPLE_KEY}")
    bundle = next(k for k in names if k.startswith("units_assets_all"))
    plan = ae.plan_add(
        raw, key="BA_Mod_Maker/cli-alias", asset_path=SAMPLE_KEY, bundle_name=bundle
    )
    assert plan.action == "add"
    assert plan.before == raw
    assert plan.byte_delta > 0
    after = _index_of(plan.after, tmp_path, "cli-added.bin")
    assert "BA_Mod_Maker/cli-alias" in after


def test_cli_plan_delete_routes_the_binary_catalog(raw: bytes, tmp_path: Path) -> None:
    victim = cbw.key_names(raw)[0]
    plan = ae.plan_delete(raw, key=victim)
    assert plan.action == "delete"
    assert plan.byte_delta < 0
    after = _index_of(plan.after, tmp_path, "cli-deleted.bin")
    assert victim not in after
