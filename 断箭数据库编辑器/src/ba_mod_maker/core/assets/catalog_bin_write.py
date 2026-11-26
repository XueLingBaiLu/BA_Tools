r"""Addressables 2.x 二进制 catalog 的**写侧**（BinaryStorageBuffer）—— 纯标准库。

## 这一层解决什么

`catalog_bin.py` 是**只读查询视图**。1.2.1 起 `aa\catalog.bin` 取代了 `catalog.json`，
于是 `cli\address_edit.py` 的 `address add` / `address delete` 那条路直接抛
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0x89 in position 1` —— 本模块就是它的写侧。

**为什么不复用只读那个模块的读法**：`CatalogBin` 是**查询视图**（键 -> 候选包），它**有意丢掉**
重编码必需的东西（KeyData 的次序、键对象的编码、头部三个对象、类型的 assembly/class、依赖数组）。
所以这里带一份**无损模型**读法；而**格式常量与判别位从 `catalog_bin` 复用**
（`CATALOG_BIN_MAGIC` / `CATALOG_BIN_VERSION` / `looks_like_catalog_bin`），
保证"是不是这个格式"全仓只有一份口径。

## 权威出处（本机就有源码，不是逆向猜的）

* `参考件\refs\pkg\com.unity.addressables`2.9.1\package\Runtime\ResourceManager\Util\`
  `BinaryStorageBuffer.cs` —— `Writer.WriteInternal / ReserveInternal / Reserve<T> / Write<T> /
  WriteObjects / WriteObject / WriteString / WriteAutoEncodedString / WriteUnicodeString /
  WriteDynamicString / RecurseDynamicStringParts`；标志位 `kUnicodeStringFlag=0x80000000`、
  `kDynamicStringFlag=0x40000000`；
* 同包 `Runtime\ResourceLocators\ContentCatalogData.cs` —— `ResourceLocator.Serialize` 的
  **书写顺序**、`Header`(8×u32) / `KeyData`(2×u32) /
  `ResourceLocation.Serializer.Data`(7×u32) / `AssetBundleRequestOptions.SerializedData`(5×u32)；
* 同包 `Runtime\ResourceManager\Util\ResourceManagerConfig.cs` ——
  `ObjectInitializationData.Serializer`；
* 哈希：`研究-模型与动画\UnityCsReference-2022.3.62f3\UnityCsReference\Runtime\Export\`
  `Hashing\SpookyHash.cs` 与同目录 `HashUtilities.cs:78-96`
  （`ComputeHash128` 的种子**取自入参 hash 自身**；调用点写的是
  `Hash128 hash; ComputeHash(pData, size, &hash);`，C# 默认 `.locals init` 零初始化 ⇒ 种子 (0,0)）。

## 为什么能逐字节写回去（写侧次序 = Data 偏移升序）

`locationIds[i] = writer.WriteObject(entry_i, false)` 是**后序**的：
依赖先写、自己的 28 B `Data` 最后写。
于是 ① 依赖条目的 Data 偏移恒 < 自己的 Data 偏移；② 每个条目的 Data 是它的"首次写完时刻"；
③ `totalBytes` 单调增 ⇒ **按 Data 偏移升序 = 官方 Writer 的写入次序**。
把条目按这个次序重排后再跑一遍 Writer，DFS 退化成"逐条目写自身内容"，每次 `WriteObject(dep)`
都命中内容去重表 ⇒ 物理布局逐字节相同（真件 2,345,239 B 实测成立）。

**与 C# 的一处等价简化**：C# 用 1 MB chunk 列表，而 `totalBytes` 只累加真正写进去的字节、
`SerializeToStream` 只写各 chunk 的 `[0, position)` ⇒ 被放弃的尾部空隙既不计入偏移也不出现在输出里
⇒ 本模块用一条连续 `bytearray`，**逐位等价**。

## 包登记（`add_bundle`）与地址（`add_address`）是**两步**

`new_asset.py::add_catalog_entries` 先登记包、再加地址；而 `add_address` 要求那个包**已在键表里**
（要拿它当依赖数组的元素）。二进制版的包条目与普通地址**形状相反**（真件 76/76 实测）：
包条目 `dependencySetOffset = 0xFFFFFFFF`、`extra` 有效；普通地址反过来。
`add_bundle` 的 provider 串 / 两个 Unity 类型 / 内部 id 的运行时前缀 / `extra` 那 8 个公共字节
**全部从真件已有包条目上抄**，一条都不写死。

## 判据一行（真件）

    .venv\Scripts\python.exe -m pytest tests/test_catalog_bin_write.py -q -n 0
      -> 原样往返逐字节相同（sha256 == 磁盘那份）· 新增一条别名键后其它键 0 条变动 · 负控能红

## ⛔ 没验过的（别当成验过了）

1. **真机没验**：游戏能不能加载这份重编码 / 改过的 `catalog.bin`（要用户起游戏）。
   本模块**不写游戏目录**。
2. 新增地址在运行时**能不能被取到**没验（`internal_id` 指向的资产必须真的在那个包里；
   本模块不造包）。
3. ~~改了 `catalog.bin` 要不要同步改 `aa/catalog.hash` 没验~~ → **2026-10-14 已落产品**：
   `newasset add-model` 现在在写 `catalog.bin` 之后**紧跟着同步写** `aa/catalog.hash`
   （内容 = `hash128_hex(catalog.bin)`，32 个 ASCII 字符、无换行；与 `catalog.bin` 同一笔补丁柜，
   可一起撤）。
   ⚠ 但**运行时到底消不消费 `.hash` 仍未验** —— 1.2.1 的 IL2CPP dump 里
   `catalog.hash` 字面 **0 命中**。
   写它的理由不是「确定它被读」，而是「**不能留一份自相矛盾的现场**」：bin 变了而 hash 还是旧的，
   万一运行时读它，游戏就不认这份 catalog。
4. `dependencyHashValue` 的算法**仍未解**：真件 17,963 条全是 0，本模块照抄 0
   （官方 `Serialize` 也从不给它赋值）。
5. 依赖数组的元素序：官方写侧是 `HashSet<uint>` 的枚举序，本模块**照抄读出来的顺序**
   （对"改一条已有键"等价；**全新构造的依赖集**的元素序是否与 Unity 一致，没验）。
6. `AssetBundleRequestOptions.SerializedData` 里 `commonId` 指向的那 **8 个字节**
   （真件 76/76 = `0000200004000000`）**语义没解**，`add_bundle` **原样抄**它。
   实测它不是 `ObjectTypeData`：拿它当 `{typeId, objectId}` 解会立刻崩（`_Reader.otype` 抛）。
7. **包登记到地址之间的运行期要求**没验：新包条目指向的 `.bundle` 必须真的落在
   `internalId` 指的那个位置，游戏才加载得到（本模块不造包、也不写游戏目录）。
8. **孤儿条目**（没有任何键命名、也没有任何依赖指向）在格式里**没有索引能找回来** ⇒
   它在下一次重编码时会被丢掉。`delete_address` 只删键、条目留成死重量；被别人的依赖
   指着的那种**找得回来**（`read_model` 会顺着依赖追进去），**完全没有引用**的才会丢。
   真件里存不存在这种条目：**没验**。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Final

from ba_mod_maker.core.assets.catalog_bin import (
    CATALOG_BIN_MAGIC,
    CATALOG_BIN_VERSION,
    looks_like_catalog_bin,
)
from ba_mod_maker.core.errors import AssetError

__all__ = [
    "CATALOG_BIN_MAGIC",
    "CATALOG_BIN_VERSION",
    "RESOURCE_TYPE_CLASSES",
    "BinAddressPlan",
    "BinBundlePlan",
    "BinEntry",
    "BinKey",
    "BinModel",
    "Writer",
    "add_address",
    "add_bundle",
    "counts",
    "delete_address",
    "hash128_hex",
    "set_dependency_bundles",
    "key_names",
    "looks_like_catalog_bin",
    "read_model",
    "roundtrip",
    "serialize",
]

#: `--resource-type` 的**约定取值**（JSON 侧的口径）-> 二进制侧的 Unity 类型类名。
#: 证据（真件 1.2.1 实测）：3,005 条 `.prefab` 资产条目 **3,005/3,005** 都是
#: `UnityEngine.GameObject`；`.png` 那 13,023 条里 6,522 条是 `UnityEngine.Sprite`（图标线取它）。
#: ⛔ 装配名（assembly）**不写死**，从真件里同类型的既有条目上照抄 —— 见 `_type_for()`。
RESOURCE_TYPE_CLASSES: Final[dict[int, str]] = {
    1: "UnityEngine.GameObject",
    3: "UnityEngine.Sprite",
}


# ============================================================================= #
# SpookyHash V2（官方 C# 参考源码逐行移植）
# ============================================================================= #
_MASK64 = (1 << 64) - 1
_DEADBEEF = 0xDEADBEEFDEADBEEF
_NUM_VARS = 12
_BLOCK = _NUM_VARS * 8  # 96
_BUFFER = 2 * _BLOCK  # 192


def _rot(x: int, k: int) -> int:
    return ((x << k) | (x >> (64 - k))) & _MASK64


def _short_mix(h0, h1, h2, h3):
    h2 = _rot(h2, 50)
    h2 = (h2 + h3) & _MASK64
    h0 ^= h2
    h3 = _rot(h3, 52)
    h3 = (h3 + h0) & _MASK64
    h1 ^= h3
    h0 = _rot(h0, 30)
    h0 = (h0 + h1) & _MASK64
    h2 ^= h0
    h1 = _rot(h1, 41)
    h1 = (h1 + h2) & _MASK64
    h3 ^= h1
    h2 = _rot(h2, 54)
    h2 = (h2 + h3) & _MASK64
    h0 ^= h2
    h3 = _rot(h3, 48)
    h3 = (h3 + h0) & _MASK64
    h1 ^= h3
    h0 = _rot(h0, 38)
    h0 = (h0 + h1) & _MASK64
    h2 ^= h0
    h1 = _rot(h1, 37)
    h1 = (h1 + h2) & _MASK64
    h3 ^= h1
    h2 = _rot(h2, 62)
    h2 = (h2 + h3) & _MASK64
    h0 ^= h2
    h3 = _rot(h3, 34)
    h3 = (h3 + h0) & _MASK64
    h1 ^= h3
    h0 = _rot(h0, 5)
    h0 = (h0 + h1) & _MASK64
    h2 ^= h0
    h1 = _rot(h1, 36)
    h1 = (h1 + h2) & _MASK64
    h3 ^= h1
    return h0, h1, h2, h3


def _short_end(h0, h1, h2, h3):
    h3 ^= h2
    h2 = _rot(h2, 15)
    h3 = (h3 + h2) & _MASK64
    h0 ^= h3
    h3 = _rot(h3, 52)
    h0 = (h0 + h3) & _MASK64
    h1 ^= h0
    h0 = _rot(h0, 26)
    h1 = (h1 + h0) & _MASK64
    h2 ^= h1
    h1 = _rot(h1, 51)
    h2 = (h2 + h1) & _MASK64
    h3 ^= h2
    h2 = _rot(h2, 28)
    h3 = (h3 + h2) & _MASK64
    h0 ^= h3
    h3 = _rot(h3, 9)
    h0 = (h0 + h3) & _MASK64
    h1 ^= h0
    h0 = _rot(h0, 47)
    h1 = (h1 + h0) & _MASK64
    h2 ^= h1
    h1 = _rot(h1, 54)
    h2 = (h2 + h1) & _MASK64
    h3 ^= h2
    h2 = _rot(h2, 32)
    h3 = (h3 + h2) & _MASK64
    h0 ^= h3
    h3 = _rot(h3, 25)
    h0 = (h0 + h3) & _MASK64
    h1 ^= h0
    h0 = _rot(h0, 63)
    h1 = (h1 + h0) & _MASK64
    return h0, h1, h2, h3


def _u64le(b, i):
    return int.from_bytes(b[i : i + 8], "little")


def _u32le(b, i):
    return int.from_bytes(b[i : i + 4], "little")


def _byte(b, i):
    return b[i] if i < len(b) else 0


def _short(message: bytes, length: int, h1: int, h2: int):
    """SpookyHash.Short（官方 switch 的 fallthrough 逐条展开，不循环）。"""
    rem = length % 32
    a, b = h1, h2
    c = d = _DEADBEEF
    pos = 0
    if length > 15:
        end = (length // 32) * 32
        while pos < end:
            c = (c + _u64le(message, pos)) & _MASK64
            d = (d + _u64le(message, pos + 8)) & _MASK64
            a, b, c, d = _short_mix(a, b, c, d)
            a = (a + _u64le(message, pos + 16)) & _MASK64
            b = (b + _u64le(message, pos + 24)) & _MASK64
            pos += 32
        if rem >= 16:
            c = (c + _u64le(message, pos)) & _MASK64
            d = (d + _u64le(message, pos + 8)) & _MASK64
            a, b, c, d = _short_mix(a, b, c, d)
            pos += 16
            rem -= 16
    d = (d + (length << 56)) & _MASK64
    if rem == 15:
        d = (d + (_byte(message, pos + 14) << 48)) & _MASK64
    if 14 <= rem <= 15:
        d = (d + (_byte(message, pos + 13) << 40)) & _MASK64
    if 13 <= rem <= 15:
        d = (d + (_byte(message, pos + 12) << 32)) & _MASK64
    if rem >= 12:
        d = (d + _u32le(message, pos + 8)) & _MASK64
        c = (c + _u64le(message, pos)) & _MASK64
    elif rem == 11:
        d = (d + (_byte(message, pos + 10) << 16)) & _MASK64
        d = (d + (_byte(message, pos + 9) << 8)) & _MASK64
        d = (d + _byte(message, pos + 8)) & _MASK64
        c = (c + _u64le(message, pos)) & _MASK64
    elif rem == 10:
        d = (d + (_byte(message, pos + 9) << 8)) & _MASK64
        d = (d + _byte(message, pos + 8)) & _MASK64
        c = (c + _u64le(message, pos)) & _MASK64
    elif rem == 9:
        d = (d + _byte(message, pos + 8)) & _MASK64
        c = (c + _u64le(message, pos)) & _MASK64
    elif rem == 8:
        c = (c + _u64le(message, pos)) & _MASK64
    elif rem == 7:
        c = (c + (_byte(message, pos + 6) << 48)) & _MASK64
        c = (c + (_byte(message, pos + 5) << 40)) & _MASK64
        c = (c + (_byte(message, pos + 4) << 32)) & _MASK64
        c = (c + _u32le(message, pos)) & _MASK64
    elif rem == 6:
        c = (c + (_byte(message, pos + 5) << 40)) & _MASK64
        c = (c + (_byte(message, pos + 4) << 32)) & _MASK64
        c = (c + _u32le(message, pos)) & _MASK64
    elif rem == 5:
        c = (c + (_byte(message, pos + 4) << 32)) & _MASK64
        c = (c + _u32le(message, pos)) & _MASK64
    elif rem == 4:
        c = (c + _u32le(message, pos)) & _MASK64
    elif rem == 3:
        c = (c + (_byte(message, pos + 2) << 16)) & _MASK64
        c = (c + (_byte(message, pos + 1) << 8)) & _MASK64
        c = (c + _byte(message, pos)) & _MASK64
    elif rem == 2:
        c = (c + (_byte(message, pos + 1) << 8)) & _MASK64
        c = (c + _byte(message, pos)) & _MASK64
    elif rem == 1:
        c = (c + _byte(message, pos)) & _MASK64
    else:
        c = (c + _DEADBEEF) & _MASK64
        d = (d + _DEADBEEF) & _MASK64
    a, b, c, d = _short_end(a, b, c, d)
    return a, b


def _mix(data: bytes, off: int, s) -> None:
    s[0] = (s[0] + _u64le(data, off + 0)) & _MASK64
    s[2] ^= s[10]
    s[11] ^= s[0]
    s[0] = _rot(s[0], 11)
    s[11] = (s[11] + s[1]) & _MASK64
    s[1] = (s[1] + _u64le(data, off + 8)) & _MASK64
    s[3] ^= s[11]
    s[0] ^= s[1]
    s[1] = _rot(s[1], 32)
    s[0] = (s[0] + s[2]) & _MASK64
    s[2] = (s[2] + _u64le(data, off + 16)) & _MASK64
    s[4] ^= s[0]
    s[1] ^= s[2]
    s[2] = _rot(s[2], 43)
    s[1] = (s[1] + s[3]) & _MASK64
    s[3] = (s[3] + _u64le(data, off + 24)) & _MASK64
    s[5] ^= s[1]
    s[2] ^= s[3]
    s[3] = _rot(s[3], 31)
    s[2] = (s[2] + s[4]) & _MASK64
    s[4] = (s[4] + _u64le(data, off + 32)) & _MASK64
    s[6] ^= s[2]
    s[3] ^= s[4]
    s[4] = _rot(s[4], 17)
    s[3] = (s[3] + s[5]) & _MASK64
    s[5] = (s[5] + _u64le(data, off + 40)) & _MASK64
    s[7] ^= s[3]
    s[4] ^= s[5]
    s[5] = _rot(s[5], 28)
    s[4] = (s[4] + s[6]) & _MASK64
    s[6] = (s[6] + _u64le(data, off + 48)) & _MASK64
    s[8] ^= s[4]
    s[5] ^= s[6]
    s[6] = _rot(s[6], 39)
    s[5] = (s[5] + s[7]) & _MASK64
    s[7] = (s[7] + _u64le(data, off + 56)) & _MASK64
    s[9] ^= s[5]
    s[6] ^= s[7]
    s[7] = _rot(s[7], 57)
    s[6] = (s[6] + s[8]) & _MASK64
    s[8] = (s[8] + _u64le(data, off + 64)) & _MASK64
    s[10] ^= s[6]
    s[7] ^= s[8]
    s[8] = _rot(s[8], 55)
    s[7] = (s[7] + s[9]) & _MASK64
    s[9] = (s[9] + _u64le(data, off + 72)) & _MASK64
    s[11] ^= s[7]
    s[8] ^= s[9]
    s[9] = _rot(s[9], 54)
    s[8] = (s[8] + s[10]) & _MASK64
    s[10] = (s[10] + _u64le(data, off + 80)) & _MASK64
    s[0] ^= s[8]
    s[9] ^= s[10]
    s[10] = _rot(s[10], 22)
    s[9] = (s[9] + s[11]) & _MASK64
    s[11] = (s[11] + _u64le(data, off + 88)) & _MASK64
    s[1] ^= s[9]
    s[10] ^= s[11]
    s[11] = _rot(s[11], 46)
    s[10] = (s[10] + s[0]) & _MASK64


def _end_partial(s) -> None:
    s[11] = (s[11] + s[1]) & _MASK64
    s[2] ^= s[11]
    s[1] = _rot(s[1], 44)
    s[0] = (s[0] + s[2]) & _MASK64
    s[3] ^= s[0]
    s[2] = _rot(s[2], 15)
    s[1] = (s[1] + s[3]) & _MASK64
    s[4] ^= s[1]
    s[3] = _rot(s[3], 34)
    s[2] = (s[2] + s[4]) & _MASK64
    s[5] ^= s[2]
    s[4] = _rot(s[4], 21)
    s[3] = (s[3] + s[5]) & _MASK64
    s[6] ^= s[3]
    s[5] = _rot(s[5], 38)
    s[4] = (s[4] + s[6]) & _MASK64
    s[7] ^= s[4]
    s[6] = _rot(s[6], 33)
    s[5] = (s[5] + s[7]) & _MASK64
    s[8] ^= s[5]
    s[7] = _rot(s[7], 10)
    s[6] = (s[6] + s[8]) & _MASK64
    s[9] ^= s[6]
    s[8] = _rot(s[8], 13)
    s[7] = (s[7] + s[9]) & _MASK64
    s[10] ^= s[7]
    s[9] = _rot(s[9], 38)
    s[8] = (s[8] + s[10]) & _MASK64
    s[11] ^= s[8]
    s[10] = _rot(s[10], 53)
    s[9] = (s[9] + s[11]) & _MASK64
    s[0] ^= s[9]
    s[11] = _rot(s[11], 42)
    s[10] = (s[10] + s[0]) & _MASK64
    s[1] ^= s[10]
    s[0] = _rot(s[0], 54)


def spooky_hash128(message: bytes, h1: int = 0, h2: int = 0) -> bytes:
    """SpookyHash.Hash(message, length, &h1, &h2) -> 16 字节（LE u64 h1 + LE u64 h2）。

    等价于 HashUnsafeUtilities.ComputeHash128(data, len, Hash128* hash)（种子取自入参 hash）。
    """
    length = len(message)
    if length < _BUFFER:
        a, b = _short(message, length, h1, h2)
    else:
        s = [h1, h2, _DEADBEEF, h1, h2, _DEADBEEF, h1, h2, _DEADBEEF, h1, h2, _DEADBEEF]
        nblocks = length // _BLOCK
        off = 0
        for _ in range(nblocks):
            _mix(message, off, s)
            off += _BLOCK
        remainder = length - off
        buf = bytearray(_BLOCK)
        buf[0:remainder] = message[off : off + remainder]
        buf[_BLOCK - 1] = remainder & 0xFF
        bb = bytes(buf)
        for i in range(12):
            s[i] = (s[i] + _u64le(bb, i * 8)) & _MASK64
        _end_partial(s)
        _end_partial(s)
        _end_partial(s)
        a, b = s[0], s[1]
    return a.to_bytes(8, "little") + b.to_bytes(8, "little")


def hash128_hex(message: bytes) -> str:
    return spooky_hash128(message).hex()


def asset_path_of_key(raw: bytes, key: str) -> str | None:
    """键 -> 资产路径（那条条目的 `internal_id`）；键不在 / 那条是**包条目** -> None。

    口径只有这一份：`core/assets/unit_pack.py::_asset_path_of_key` 也走它。
    2026-10-22 提到这里，因为 `newasset repoint` 要判「这个键是不是模型地址」。
    """
    for entry in read_model(raw).entries:
        if str(getattr(entry, "primary_key", "")) == key:
            iid = str(entry.internal_id or "")
            if iid and not iid.endswith(".bundle"):
                return iid
    return None


# ============================================================================= #
# BinaryStorageBuffer.Writer（官方 C# 源码的逐语义移植）
# ============================================================================= #
U32MAX = 0xFFFFFFFF
K_UNICODE = 0x80000000
K_DYNAMIC = 0x40000000
K_MASK = 0x3FFFFFFF

_ZERO_HASH = b"\x00" * 16  # ComputeHash(pData, 0) -> *hash = default


def is_unicode(s: str) -> bool:
    """Writer.IsUnicode：有任何 char > 127 就是 unicode。"""
    return any(ord(c) > 127 for c in s)


def find_best_separator(s: str) -> str:
    """BuiltinTypesSerializer.FindBestSeparator(str, '/', '\\\\', '.', '-', '_', ',')。"""
    best_count = 0
    best_sep = ""
    for sep in ("/", "\\", ".", "-", "_", ","):
        c = s.count(sep)
        if c > best_count:
            best_count = c
            best_sep = sep
    if best_count == 0:
        return ""
    valid = sum(1 for p in s.split(best_sep) if len(p) > 4)
    return best_sep if valid > 1 else ""


class Writer:
    """BinaryStorageBuffer.Writer 的逐语义移植。"""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.existing: dict[bytes, int] = {}
        self.dup_hits = 0

    @property
    def length(self) -> int:
        return len(self.buf)

    # ---- 原语 ----

    def _hash(self, pdata: bytes) -> bytes:
        return spooky_hash128(pdata) if pdata else _ZERO_HASH

    def _write_internal(self, pdata: bytes, prefix: bool) -> int:
        h = self._hash(pdata)
        hit = self.existing.get(h)
        if hit is not None:
            self.dup_hits += 1
            return hit
        if prefix:
            off = len(self.buf)
            self.buf += struct.pack("<I", len(pdata))
            self.buf += pdata
            self.existing[h] = off + 4
            return off + 4
        if not pdata:
            return U32MAX
        off = len(self.buf)
        self.buf += pdata
        self.existing[h] = off
        return off

    def reserve(self, data_size: int, prefix: bool) -> int:
        """ReserveInternal(dataSize, prefixSize)。"""
        old = len(self.buf)
        self.buf += b"\x00" * (data_size + (4 if prefix else 0))
        return old + (4 if prefix else 0)

    def _write_internal_at(self, ident: int, pdata: bytes, prefix: bool) -> None:
        """WriteInternal(uint id, void* pData, uint dataSize, bool prefixSize)。"""
        h = self._hash(pdata)
        self.existing[h] = ident
        if prefix:
            struct.pack_into("<I", self.buf, ident - 4, len(pdata))
        self.buf[ident : ident + len(pdata)] = pdata

    def write(self, pdata: bytes) -> int:
        """Write<T>(in T val) / Write<T>(T val)：定长结构体，无前缀，按内容去重。"""
        return self._write_internal(pdata, False)

    def write_at(self, ident: int, pdata: bytes) -> int:
        """Write<T>(uint offset, in T val)。"""
        self._write_internal_at(ident, pdata, False)
        return ident

    def write_array(self, values: bytes) -> int:
        """Write<T>(T[] values, hashElements) —— 4 字节长度前缀 + 数据。"""
        return self._write_internal(values, True)

    def write_array_at(self, ident: int, values: bytes, elem_size: int) -> int:
        """Write<T>(uint offset, T[] values, bool hashElements = true)。"""
        self._write_internal_at(ident, values, True)
        if elem_size > 4:  # C#: hashElements && sizeof(T) > sizeof(uint)
            for i in range(0, len(values), elem_size):
                self.existing[spooky_hash128(values[i : i + elem_size])] = ident + i
        return ident

    def write_u32_array(self, u32s) -> int:
        return self.write_array(b"".join(struct.pack("<I", v) for v in u32s))

    # ---- 字符串 ----

    def write_string_internal(self, val: str | None, enc: str) -> int:
        if val is None:
            return U32MAX
        return self._write_internal(val.encode(enc), True)

    def write_unicode_string(self, val: str) -> int:
        return K_UNICODE | self.write_string_internal(val, "utf-16-le")

    def write_auto_encoded_string(self, val: str | None) -> int:
        if val is None:
            return U32MAX
        if is_unicode(val):
            return self.write_unicode_string(val)
        return self.write_string_internal(val, "ascii")

    def write_string(self, val: str | None, sep: str = "") -> int:
        if val is None:
            return U32MAX
        if not sep:
            return self.write_auto_encoded_string(val)
        return self.write_dynamic_string(val, sep)

    def write_dynamic_string(self, val: str, sep: str) -> int:
        parts = val.split(sep)
        min_size = 8  # sizeof(DynamicString)
        data = []
        for p in parts:
            size = len(p.encode("utf-16-le")) if is_unicode(p) else len(p)
            data.append([p, size, is_unicode(p)])
        if len(data) < 2 or (len(data) == 2 and (data[0][1] + data[1][1]) < min_size):
            return self.write_auto_encoded_string(val)
        return K_DYNAMIC | self._recurse_dynamic(data, len(data) - 1, sep, min_size)

    def _recurse_dynamic(self, parts, index: int, sep: str, min_size: int) -> int:
        while index > 0:
            curr = parts[index][1]
            if curr < min_size:
                parts[index - 1][0] = parts[index - 1][0] + sep + parts[index][0]
                parts[index - 1][1] += curr + 1
                parts[index - 1][2] = parts[index - 1][2] or parts[index][2]
                index -= 1
            else:
                break
        if parts[index][2]:
            str_id = self.write_unicode_string(parts[index][0])
        else:
            str_id = self.write_string_internal(parts[index][0], "ascii")
        nxt = self._recurse_dynamic(parts, index - 1, sep, min_size) if index > 0 else U32MAX
        return self.write(struct.pack("<II", str_id, nxt))

    # ---- 对象 ----

    def write_type(self, asm_full_name: str | None, class_full_name: str | None) -> int:
        """TypeSerializer.Serialize：Data{assemblyId, classId}，两串都用 '.' 分隔。"""
        asm_id = self.write_string(asm_full_name, ".")
        cls_id = self.write_string(class_full_name, ".")
        return self.write(struct.pack("<II", asm_id, cls_id))

    def write_typed(self, payload_prefix_writes, asm, cls):
        """WriteObject(obj, serializeTypeData=True) 的通用形状。

        C#: id = adapter.Serialize(...);
        id = Write(new ObjectTypeData{ typeId = WriteObject(objType,false), objectId = id });
        —— 初始化器按**源码顺序**求值：先 typeId 的 WriteObject，后 Write(ObjectTypeData)。
        """
        obj_id = payload_prefix_writes()
        type_id = self.write_type(asm, cls)
        return self.write(struct.pack("<II", type_id, obj_id))

    def write_key_object(self, key: str, asm: str | None, cls: str | None) -> int:
        """writer.WriteObject(k.Key, true)：先 BuiltinTypesSerializer，再 ObjectTypeData。"""
        sep = find_best_separator(key)
        string_id = self.write_string(key, sep)
        remap = self.write(struct.pack("<IH2x", string_id, ord(sep) if sep else 0))
        type_id = self.write_type(asm, cls)
        return self.write(struct.pack("<II", type_id, remap))


# ============================================================================= #
# 无损模型：读 / 改 / 按官方书写顺序写回
# ============================================================================= #
_HDR = struct.Struct("<8I")
_KD = struct.Struct("<2I")
_LOC = struct.Struct("<7I")
_AB = struct.Struct("<5I")
_OT = struct.Struct("<2I")
_OID = struct.Struct("<3I")


class _Reader:
    def __init__(self, raw: bytes) -> None:
        self.d = raw

    def sized(self, off: int) -> bytes:
        if off < 4 or off > len(self.d):
            raise ValueError(f"offset out of range: {off}")
        n = struct.unpack_from("<I", self.d, off - 4)[0]
        return self.d[off : off + n]

    def auto_str(self, oid: int):
        if oid == U32MAX:
            return None
        if oid & 0x80000000:
            return self.sized(oid & K_MASK).decode("utf-16-le", "replace")
        return self.sized(oid).decode("ascii", "replace")

    def rstr(self, oid: int, sep: str = ""):
        if oid == U32MAX:
            return None
        if not sep:
            return self.auto_str(oid)
        if oid & K_DYNAMIC:
            parts = []
            cur = oid & K_MASK
            while cur != U32MAX:
                sid, nxt = struct.unpack_from("<II", self.d, cur)
                parts.append(self.auto_str(sid) or "")
                cur = nxt
            return sep.join(reversed(parts))
        return self.auto_str(oid)

    def otype(self, oid: int):
        """ObjectTypeData -> (assembly, class, payload_offset)。"""
        if oid == U32MAX:
            return None
        tid, pid = _OT.unpack_from(self.d, oid)
        return self.rtype(tid) + (pid,)

    def rtype(self, tid: int):
        """TypeSerializer -> (assembly FullName, class FullName)。"""
        if tid == U32MAX:
            return (None, None)
        aid, cid = _OT.unpack_from(self.d, tid)
        return (self.rstr(aid, "."), self.rstr(cid, "."))

    def u32_array(self, off: int):
        if off == U32MAX:
            return None
        raw = self.sized(off)
        count = len(raw) // 4
        return list(struct.unpack_from(f"<{count}I", raw)) if raw else []


@dataclass
class InitializationData:
    """ObjectInitializationData（ResourceManagerConfig.Serializer）。"""

    ident: str | None = None
    type_asm: str | None = None
    type_cls: str | None = None
    data: str | None = None
    offset: int = 0


@dataclass
class AssetBundleOptions:
    """AssetBundleRequestOptions（ContentCatalogData.AssetBundleRequestOptionsSerializationAdapter）。"""

    hash_bytes: bytes = b""
    bundle_name: str | None = None
    crc: int = 0
    bundle_size: int = 0
    common_raw: bytes = b""
    type_asm: str | None = None
    type_cls: str | None = None
    offset: int = 0


@dataclass
class BinEntry:
    loc_offset: int
    primary_key: str | None
    internal_id: str | None
    provider: str | None
    src_dep_offsets: tuple = ()  # 真件里的原始偏移（读数用）
    dep_ranks: tuple = ()  # 条目下标（写侧用；-1 = 无依赖）
    dep_hash: int = 0
    extra: AssetBundleOptions | None = None
    type_asm: str | None = None
    type_cls: str | None = None
    rank: int = -1

    @property
    def bundle_name(self):
        iid = (self.internal_id or "").replace("\\", "/")
        return iid.rsplit("/", 1)[-1] if iid.endswith(".bundle") else None


@dataclass
class BinKey:
    name: str
    src_offsets: tuple = ()
    ranks: tuple = ()
    type_asm: str | None = None
    type_cls: str | None = None


@dataclass
class BinModel:
    provider_id: str | None = None
    instance_provider: InitializationData | None = None
    scene_provider: InitializationData | None = None
    resource_providers: list = field(default_factory=list)
    build_result_hash: str | None = None
    entries: list = field(default_factory=list)  # 按 Data 偏移升序 = 官方写入次序
    keys: list = field(default_factory=list)  # 文件里 KeyData 数组的次序
    raw_header: tuple = ()
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------- 读


def read_model(raw: bytes) -> BinModel:
    r = _Reader(raw)
    magic, version, keys_off, id_off, ip, sp, io, brh = _HDR.unpack_from(raw, 0)
    if magic != CATALOG_BIN_MAGIC:
        raise ValueError(f"bad magic {magic:#x}")
    m = BinModel(raw_header=(magic, version, keys_off, id_off, ip, sp, io, brh))
    m.provider_id = r.auto_str(id_off)
    m.build_result_hash = r.rstr(brh)

    def read_oid(off):
        if off == U32MAX:
            return None
        ident, typ, dat = _OID.unpack_from(raw, off)
        o = InitializationData(ident=r.auto_str(ident), data=r.auto_str(dat), offset=off)
        # C#: type = writer.WriteObject(oid.ObjectType.Value, false)  —— 值为 System.Type，
        # serializeTypeData=False ⇒ 返回的是 **TypeSerializer 的 8 字节 Data 本身**，
        # 读侧 reader.ReadObject<Type>(dataStruct.type) 也是直接走 TypeSerializer。
        o.type_asm, o.type_cls = r.rtype(typ)
        return o

    m.instance_provider = read_oid(ip)
    m.scene_provider = read_oid(sp)
    m.resource_providers = [read_oid(o) for o in (r.u32_array(io) or [])]

    kd = []
    n = struct.unpack_from("<I", raw, keys_off - 4)[0] // 8
    for i in range(n):
        kname, lset = _KD.unpack_from(raw, keys_off + 8 * i)
        t = r.otype(kname)
        if t is None or t[1] != "System.String":
            raise ValueError(f"key[{i}] is not System.String: {t!r}")
        sid, sep = struct.unpack_from("<IH", raw, t[2])
        kd.append(
            BinKey(
                name=r.rstr(sid, chr(sep) if sep else ""),
                src_offsets=tuple(r.u32_array(lset) or ()),
                type_asm=t[0],
                type_cls=t[1],
            )
        )
    m.keys = kd

    def dep_offsets_of(loc_off):
        dep = _LOC.unpack_from(raw, loc_off)[3]
        if dep == U32MAX:
            return ()
        return tuple(x for x in (r.u32_array(dep) or ()) if x != U32MAX)

    seen = {}
    for k in kd:
        for off in k.src_offsets:
            if off != U32MAX and off not in seen:
                seen[off] = None
    # ⚠ 依赖数组可能指向**没有任何键命名**的条目：`delete_address` 删掉一个键之后，那条条目
    #   就成孤儿，可别的条目的依赖数组还指着它。不追进去，下面 off2rank[d] 直接 KeyError
    #   （实测：删掉一个包键之后 read_model(那份产物) 抛 KeyError 181285）。
    #   对真件是**空操作**（所有依赖本来就在键表里）⇒ 逐字节正控不受影响。
    pending = list(seen)
    while pending:
        for dep_off in dep_offsets_of(pending.pop()):
            if dep_off not in seen:
                seen[dep_off] = None
                pending.append(dep_off)
    entries = []
    for off in sorted(seen):
        pk, iid, prov, dep, dh, extra, tid = _LOC.unpack_from(raw, off)
        e = BinEntry(
            loc_offset=off,
            primary_key=r.rstr(pk, "/"),
            internal_id=r.rstr(iid, "/"),
            provider=r.rstr(prov, "."),
            src_dep_offsets=tuple(x for x in (r.u32_array(dep) or ()) if x != U32MAX),
            dep_hash=dh,
        )
        e.type_asm, e.type_cls = r.rtype(tid)
        if extra != U32MAX:
            t = r.otype(extra)
            payload = t[2]
            hash_id, name_id, crc, size, common_id = _AB.unpack_from(raw, payload)
            e.extra = AssetBundleOptions(
                hash_bytes=raw[hash_id : hash_id + 16],
                bundle_name=r.rstr(name_id, "_"),
                crc=crc,
                bundle_size=size,
                common_raw=raw[common_id : common_id + 8],
                type_asm=t[0],
                type_cls=t[1],
                offset=extra,
            )
        entries.append(e)
    for i, e in enumerate(entries):
        e.rank = i
    m.entries = entries
    off2rank = {e.loc_offset: e.rank for e in entries}
    for k in kd:
        k.ranks = tuple(off2rank[o] for o in k.src_offsets)
    for e in entries:
        e.dep_ranks = tuple(off2rank[d] for d in e.src_dep_offsets)
        if e.dep_hash != 0:
            m.notes.append(f"dependencyHashValue != 0 @{e.loc_offset}")
        for d in e.src_dep_offsets:
            if d >= e.loc_offset:
                m.notes.append(f"dep {d} >= own {e.loc_offset}")
    return m


# ---------------------------------------------------------------- 写


def _serialize_full(m: BinModel, mutate=None) -> tuple[bytes, Writer, dict]:
    """按官方书写顺序重编码。mutate(model) 在写之前调用（用于新增/删除）。

    返回 (字节, Writer, rank->输出偏移)。"""
    if mutate is not None:
        mutate(m)
    w = Writer()

    header_off = w.reserve(_HDR.size, False)
    keys_off = w.reserve(_KD.size * len(m.keys), True)

    id_off = w.write_string(m.provider_id)
    ip = _write_oid(w, m.instance_provider)
    sp = _write_oid(w, m.scene_provider)
    io = _write_oids(w, m.resource_providers)
    brh = w.write_string(m.build_result_hash)
    w.write_at(
        header_off,
        _HDR.pack(CATALOG_BIN_MAGIC, CATALOG_BIN_VERSION, keys_off, id_off, ip, sp, io, brh),
    )

    loc_offset_by_rank = {}
    for e in m.entries:
        if e.dep_ranks:
            dep_id = w.write_u32_array([loc_offset_by_rank[r] for r in e.dep_ranks])
        else:
            dep_id = U32MAX
        pk = w.write_string(e.primary_key, "/")
        iid = w.write_string(e.internal_id, "/")
        prov = w.write_string(e.provider, ".")
        extra = _write_extra(w, e.extra)
        tid = w.write_type(e.type_asm, e.type_cls)
        got = w.write(_LOC.pack(pk, iid, prov, dep_id, e.dep_hash, extra, tid))
        loc_offset_by_rank[e.rank] = got

    kd = bytearray()
    for k in m.keys:
        kname = _write_key_object(w, k)
        lset = w.write_u32_array([loc_offset_by_rank[r] for r in k.ranks])
        kd += _KD.pack(kname, lset)
    w.write_array_at(keys_off, bytes(kd), _KD.size)
    return bytes(w.buf), w, loc_offset_by_rank


def _write_key_object(w: Writer, k: BinKey) -> int:
    best = 0
    sep = ""
    for s in ("/", "\\", ".", "-", "_", ","):
        c = k.name.count(s)
        if c > best:
            best, sep = c, s
    if best == 0 or sum(1 for p in k.name.split(sep) if len(p) > 4) <= 1:
        sep = ""
    string_id = w.write_string(k.name, sep)
    remap = w.write(struct.pack("<IH2x", string_id, ord(sep) if sep else 0))
    type_id = w.write_type(k.type_asm, k.type_cls)
    return w.write(struct.pack("<II", type_id, remap))


def _write_oid(w: Writer, o: InitializationData | None) -> int:
    if o is None:
        return U32MAX
    ident = w.write_string(o.ident)
    typ = w.write_type(o.type_asm, o.type_cls)
    data = w.write_string(o.data)
    return w.write(_OID.pack(ident, typ, data))


def _write_oids(w: Writer, objs) -> int:
    if objs is None:
        return U32MAX
    ids = [_write_oid(w, o) for o in objs]
    return w.write_u32_array(ids)


def _write_extra(w: Writer, x: AssetBundleOptions | None) -> int:
    if x is None:
        return U32MAX
    hash_id = w.write(x.hash_bytes)
    name_id = w.write_string(x.bundle_name, "_")
    common_id = w.write(x.common_raw)
    sd = w.write(_AB.pack(hash_id, name_id, x.crc, x.bundle_size, common_id))
    type_id = w.write_type(x.type_asm, x.type_cls)
    return w.write(struct.pack("<II", type_id, sd))


# =========================================================================== #
# 公开入口（CLI 与用例只用这一段）
# =========================================================================== #
def counts(raw: bytes) -> tuple[int, int, int]:
    """(键, 桶, 条目) —— 与 JSON 侧 `CatalogEdit.counts` **同一个口径**。

    二进制版把键与桶并成同一条 `KeyData`，所以前两项恒等（真件 27,764 / 27,764 / 20,614）。
    """
    model = read_model(raw)
    return _counts_of(model)


def key_names(raw: bytes) -> tuple[str, ...]:
    """键表里的键名，**文件次序**（= 官方 Writer 的写入次序）。"""
    return tuple(k.name for k in read_model(raw).keys)


def roundtrip(raw: bytes) -> bytes:
    """**等值往返**：读成无损模型，再按官方书写顺序写回去。

    判据（真件）：返回值与入参**逐字节相同**（2,345,239 B / sha256 不变）。
    """
    return serialize(read_model(raw))


def serialize(model: BinModel, mutate=None) -> bytes:
    """按官方书写顺序把模型写成字节（`mutate(model)` 在写之前调用）。"""
    return _serialize_full(model, mutate)[0]


@dataclass(frozen=True, slots=True)
class BinAddressPlan:
    """二进制 catalog 上一次地址改动的读数（`after` 是**整份新字节**）。"""

    action: str
    key: str
    before: bytes
    after: bytes
    counts_before: tuple[int, int, int]
    counts_after: tuple[int, int, int]
    lines: tuple[str, ...] = ()
    entry_index: int = -1
    internal_id_index: int = -1
    bundle_key_index: int = -1
    asset_path: str = ""
    bundle_name: str = ""
    resource_type: int = 1
    dependency_hash: int | None = None

    @property
    def is_empty(self) -> bool:
        """一个字节都没变 => 算不上一次改动（调用方据此给退出码 1）。"""
        return self.before == self.after


def _counts_of(model: BinModel) -> tuple[int, int, int]:
    return (len(model.keys), len(model.keys), len(model.entries))


def _is_bundle_entry(entry: BinEntry) -> bool:
    return (entry.internal_id or "").replace("\\", "/").endswith(".bundle")


def _provider_like(model: BinModel, suffix: str) -> str | None:
    """真件里以 `suffix` 结尾的 provider **原串**（⛔ 不写死全名，从模型里取）。"""
    for entry in model.entries:
        name = entry.provider or ""
        if name.endswith(suffix):
            return name
    return None


def _type_for(model: BinModel, resource_type: int) -> tuple[str, str]:
    """`--resource-type` -> 真件里同类型的既有条目的 (assembly, class)。

    ⛔ 三处 fail-closed：不在约定表里、真件里没有这个类、类名对不上 —— 都不猜，直接报。
    """
    wanted = RESOURCE_TYPE_CLASSES.get(int(resource_type))
    if wanted is None:
        raise AssetError(
            f"--resource-type {resource_type} 在二进制 catalog 上没有约定的类型"
            f"（JSON 侧的真件取值 {sorted(RESOURCE_TYPE_CLASSES)} 之外没有证据）",
            hint="二进制版的类型是**真的 Unity 类型名**（不是 JSON 那种下标）—— "
            "1 = prefab（UnityEngine.GameObject）、3 = 图标（UnityEngine.Sprite）",
        )
    for entry in model.entries:
        if entry.type_cls == wanted and entry.type_asm:
            return entry.type_asm, entry.type_cls
    raise AssetError(
        f"这份 catalog 里没有任何 {wanted} 型条目 —— 抄不到装配名",
        hint="宁可不写：二进制版的类型要连 assembly 一起写，猜一个装配名会写出读不回来的件",
    )


def _key_type(model: BinModel) -> tuple[str, str]:
    """键对象用的 (assembly, class) —— 真件**全是** `System.String`（复核 27,764/27,764）。"""
    for key in model.keys:
        if key.type_cls == "System.String" and key.type_asm:
            return key.type_asm, key.type_cls
    raise AssetError(
        "这份 catalog 里没有 System.String 型的键 —— 键对象怎么写抄不到",
        hint="二进制版的键是 ObjectTypeData 包着的对象，本模块只认 System.String",
    )


@dataclass(frozen=True, slots=True)
class BinBundlePlan:
    """二进制 catalog 上一次**包登记**的读数（`after` 是**整份新字节**）。"""

    bundle_name: str
    before: bytes
    after: bytes
    counts_before: tuple[int, int, int]
    counts_after: tuple[int, int, int]
    lines: tuple[str, ...] = ()
    bundle_entry_index: int = -1
    bundle_key_index: int = -1
    internal_id_index: int = -1
    internal_id: str = ""
    internal_name: str = ""
    provider: str = ""

    @property
    def is_empty(self) -> bool:
        """一个字节都没变 => 算不上一次改动（调用方据此给退出码 1）。"""
        return self.before == self.after


def _bundle_reference(model: BinModel) -> BinEntry:
    """真件里第一条**带 extra 的包条目** —— 新增包条目的形状全从它抄。"""
    for entry in model.entries:
        if _is_bundle_entry(entry) and entry.extra is not None:
            return entry
    raise AssetError(
        "这份 catalog 里没有带 extra 的 .bundle 条目 —— 包条目的形状抄不到",
        hint="新增包条目要连 provider / 两个 Unity 类型 / 运行时前缀 / 公共八字节一起写",
    )


def add_bundle(
    raw: bytes,
    *,
    bundle_name: str,
    m_hash: str,
    crc: int,
    bundle_size: int,
    internal_name: str,
) -> BinBundlePlan:
    """**纯函数**：二进制 catalog 字节 -> 登记一个新包之后的字节（⛔ 不碰盘）。

    `new_asset.py::add_catalog_entries` 是**两步**：先登记包、再加地址；而 `add_address` 要求
    那个包**已在键表里**（要拿它当依赖数组的元素）⇒ 缺了这一半，「落一个新包 + 加一条地址」
    走不到头。

    二进制版的包条目与普通地址**形状相反**（真件 76/76 实测）：包条目
    `dependencySetOffset = 0xFFFFFFFF`（没有依赖）、`extra` 有效；普通地址反过来。

    ⛔ 形状一条都不写死：provider 串、两个 Unity 类型的 assembly/class、内部 id 的运行时前缀、
    `extra` 里那 8 个公共字节，**全部从真件已有的包条目上抄**（抄不到就报错，不许猜）。

    四条 fail-closed：包名空 / 包名不以 `.bundle` 结尾 / 键表里**已有**这个名字 / 条目表里
    已有同名的包条目（覆盖会静默改掉别人的依赖）。
    """
    text = str(bundle_name).strip()
    asm_name = str(internal_name).strip()
    digest = str(m_hash).strip().lower()
    if not text:
        raise AssetError(
            "--bundle 是空的", hint="包名 = catalog 的键 = 磁盘上那个 .bundle 的文件名"
        )
    if not text.endswith(".bundle"):
        raise AssetError(
            f"包名必须以 .bundle 结尾：{text!r}",
            hint="条目表按这个后缀认包条目（_is_bundle_entry）；真件 76/76 个包键都带它",
        )
    # ★ 2026-10-14 加：`_all_` 尾段必须是 **32 位十六进制**（真件 76/76 实测）。
    #   为什么单独一条：产品口径是「m_Hash = 包名 `_all_` 后那一段」（new_asset.py:22）
    #   ⇒ 写个 12 位的进去，catalog 里的 m_Hash 就与真件**不同形**（下游按 16 字节内联值用它）。
    #   实测现场：Unity 侧把名字写死成 `phase1box_assets_all_9f1e2b3c4d5a.bundle`（12 位）⇒ 被拦。
    #   正确姿势：造完包用 `.manifest` 的 `AssetFileHash` 改名（那个就是 32 位）。
    if "_all_" in text:
        tail = text.rsplit("_all_", 1)[1].removesuffix(".bundle").lower()
        if len(tail) != 32 or any(c not in "0123456789abcdef" for c in tail):
            raise AssetError(
                f"包名 `_all_` 尾段要是 32 位十六进制（Hash128）：{tail!r}（{len(tail)} 位）",
                hint="真件 76/76 都是 32 位；产品取这一段当 m_Hash。Unity 侧造完包用 "
                     ".manifest 的 AssetFileHash 改名即可（阶段 1 的 Phase1Build.cs 已照此改）",
            )
    if not asm_name:
        raise AssetError(
            "--internal-name 是空的",
            hint="包内 AssetBundle 对象的 m_Name（真件里是包名去掉 .bundle，或一个十六进制串）",
        )
    if len(digest) != 32 or any(c not in "0123456789abcdef" for c in digest):
        raise AssetError(
            f"--hash 要是 32 位十六进制（Hash128）：{m_hash!r}",
            hint="二进制版把它当 **16 字节内联值**写进去（不是字符串）；猜一个会写出读不回来的件",
        )
    size = int(bundle_size)
    if size < 0 or size > 0xFFFFFFFF:
        raise AssetError(
            f"--size 越界：{bundle_size!r}",
            hint="真件那一格是 u32（units 包 = 3,352,484,707；2026-10-13 那一代是 3,374,243,605）",
        )
    crc_value = int(crc) & 0xFFFFFFFF
    before = bytes(raw)
    model = read_model(before)
    counts_before = _counts_of(model)
    names = [k.name for k in model.keys]
    if text in names:
        raise AssetError(
            f"catalog 里已经有这个包：{text!r}",
            hint="登记过的包不许覆盖 —— 与 JSON 那条路同口径（要改就 删除 + 新增 两个动作）",
        )
    for entry in model.entries:
        if _is_bundle_entry(entry) and (entry.internal_id or "").endswith(text):
            raise AssetError(
                f"catalog 里已经有这个包的条目：{text!r}（条目 rank {entry.rank}）",
                hint="键表里没有它、条目表里有 ⇒ 同样算已登记（覆盖会静默改掉别人的依赖）",
            )
    provider = _provider_like(model, "AssetBundleProvider")
    if provider is None:
        raise AssetError(
            "这份 catalog 里没有 AssetBundleProvider 的条目 —— 抄不到 provider 串",
            hint="本模块不写死 provider 全名，只从真件里取",
        )
    ref = _bundle_reference(model)
    ref_base = ref.primary_key or ""
    ref_iid = ref.internal_id or ""
    if not ref_base or not ref_iid.endswith(ref_base):
        raise AssetError(
            "真件包条目的 internalId 不以它的 primaryKey 结尾 —— 运行时前缀抄不到",
            hint=f"条目 rank {ref.rank}：internalId={ref_iid!r} primaryKey={ref_base!r}",
        )
    prefix = ref_iid[: len(ref_iid) - len(ref_base)]
    internal_id = prefix + text
    key_asm, key_cls = _key_type(model)
    rank = len(model.entries)
    model.entries.append(
        BinEntry(
            loc_offset=-1,
            primary_key=text,
            internal_id=internal_id,
            provider=provider,
            dep_ranks=(),
            dep_hash=0,
            extra=AssetBundleOptions(
                hash_bytes=bytes.fromhex(digest),
                bundle_name=asm_name,
                crc=crc_value,
                bundle_size=size,
                common_raw=ref.extra.common_raw,
                type_asm=ref.extra.type_asm,
                type_cls=ref.extra.type_cls,
            ),
            type_asm=ref.type_asm,
            type_cls=ref.type_cls,
            rank=rank,
        )
    )
    model.keys.append(
        BinKey(name=text, src_offsets=(), ranks=(rank,), type_asm=key_asm, type_cls=key_cls)
    )
    after = serialize(model)
    lines = [
        f"登记包：{text}",
        f"  条目 rank #{rank}（追加在条目表末尾，它的 Data 最后写）· 键 #{len(model.keys) - 1}",
        f"  internalId：{internal_id}（运行时前缀抄自条目 rank {ref.rank}）",
        f"  provider：{provider}（抄自真件，未写死）",
        "  依赖数组：**空**（dependencySetOffset = 0xFFFFFFFF —— 与普通地址相反）  依赖 hash：0",
        f"  extra：hash={digest} crc={crc_value} size={size} 内部名={asm_name}",
        f"  extra 的公共八字节：{ref.extra.common_raw.hex()}（抄自真件，76/76 同值；语义未解）",
        f"  类型：{ref.type_cls} / extra {ref.extra.type_cls}（都抄自真件）",
        "  internal_id_index = -1：二进制版没有 m_InternalIds 数组，内部 id 就是字符串本身",
    ]
    return BinBundlePlan(
        bundle_name=text,
        before=before,
        after=after,
        counts_before=counts_before,
        counts_after=_counts_of(model),
        lines=tuple(lines),
        bundle_entry_index=rank,
        bundle_key_index=len(model.keys) - 1,
        internal_id_index=-1,
        internal_id=internal_id,
        internal_name=asm_name,
        provider=provider,
    )


def _merge_bundle_deps(bundle_ranks: tuple, inherited: tuple) -> tuple:
    """`--bundle` 那个包条目 ∪ 抄来的依赖（去重、稳定序：**新包在前**，抄来的按原序追加）。

    ⛔ 硬不变式（真机 1.2.1 事故换来的，见 `add_address` 的 docstring）：`bundle_ranks`
    一个都不许丢 —— Addressables 只打开**依赖数组里**的包。只照抄参照条目的依赖
    ⇒ 我们那个新包不在里面 ⇒ 游戏去参照条目的包里找新资产 ⇒
    `Addressable prefab 'Phase1Box' was not loaded!`，而新包**从未被打开**。
    """
    merged = list(bundle_ranks)
    seen = set(merged)
    for rank in inherited:
        if rank in seen:
            continue
        seen.add(rank)
        merged.append(rank)
    return tuple(merged)


def add_address(
    raw: bytes,
    *,
    key: str,
    asset_path: str,
    bundle_name: str,
    resource_type: int = 1,
    dependency_source_key: str | None = None,
    dependency_policy: str = "auto",
) -> BinAddressPlan:
    """**纯函数**：二进制 catalog 字节 -> 加一条地址之后的字节（⛔ 不碰盘）。

    与 JSON 那条路同口径的四条 fail-closed：键是空的 / 键已经在 catalog 里（入库的键不许覆盖）/
    `--bundle` 不在 catalog 里 / 自检不过。另有两条二进制特有的：`--bundle` 指的不是包条目、
    类型抄不到。

    依赖声明（二进制版是 `dependencySetOffset` 那个**包条目数组** + `dependencyHashValue`）：
    ① 给了 `--dependency-hash-from` ⇒ **照抄那条参照条目的依赖**，并**并入** `--bundle` 那个包条目；
    ② 没给、但这条资产路径在 catalog 里已有资产条目 ⇒ 照抄它既有那条，同样**并入** `--bundle`；
    ③ 都没有 ⇒ 依赖数组 = `[--bundle` 那个包条目]、hash = **0**（真件 17,963 条全是 0）。

    ⛔ **硬不变式（三条分支都要）**：`--bundle` 那个包条目**永远**在最终的依赖数组里 ——
    Addressables 只打开**依赖数组里**的包；只照抄参照条目的依赖会把我们**自己那个新包抄没**，
    于是游戏去参照条目的包里找新资产 ⇒ 真机 1.2.1 实测的
    `Error: Addressable prefab 'Phase1Box' was not loaded!`，而**新包从头到尾没被打开过**。
    并集由 `_merge_bundle_deps` 做；写出去之前还有一条 fail-closed 断言兜底
    （缺了直接抛，不静默放过）。
    """
    text_key = str(key).strip()
    text_path = str(asset_path).strip()
    bundle = str(bundle_name).strip()
    if not text_key:
        raise AssetError("--key 是空的", hint="地址键是 catalog 的主键，例 MyIcon")
    if not text_path:
        raise AssetError(
            "--path 是空的",
            hint="资产路径 = 条目 internalId 那条，例 Assets/Mods/My/icon.png",
        )
    if not bundle:
        raise AssetError(
            "--bundle 是空的",
            hint="新地址必须挂在一条**已登记**的包上（依赖数组里要放那个包条目）",
        )
    before = bytes(raw)
    model = read_model(before)
    counts_before = _counts_of(model)
    names = [k.name for k in model.keys]
    if text_key in names:
        raise AssetError(
            f"catalog 里已经有这个键：{text_key!r}",
            hint="入库的键不许覆盖 —— 要改就 delete 旧的再加新的（两个动作各自可撤）",
        )
    if bundle not in names:
        raise AssetError(
            f"catalog 里没有这个包：{bundle!r}",
            hint="先把包登记进 catalog（newasset add-model / 包登记那条链），再来加地址",
        )
    bundle_key_index = names.index(bundle)
    bundle_ranks = tuple(model.keys[bundle_key_index].ranks)
    stale = [r for r in bundle_ranks if not _is_bundle_entry(model.entries[r])]
    if not bundle_ranks or stale:
        raise AssetError(
            f"`--bundle {bundle!r}` 指的条目不是包条目（entry {'/'.join(map(str, stale))}）",
            hint="依赖数组里必须放**.bundle 条目**；这条键指向别的东西",
        )
    provider = _provider_like(model, "BundledAssetProvider")
    if provider is None:
        raise AssetError(
            "这份 catalog 里没有 BundledAssetProvider 的条目 —— 抄不到 provider 串",
            hint="本模块不写死 provider 全名，只从真件里取",
        )
    type_asm, type_cls = _type_for(model, resource_type)

    if dependency_policy not in ("auto", "own-only"):
        raise AssetError(
            f"未知的 dependency_policy：{dependency_policy!r}",
            hint="只认 auto（照抄参照）或 own-only（只依赖自己那个包）",
        )
    notes: list[str] = []
    dep_hash = 0
    dep_ranks = _merge_bundle_deps(bundle_ranks, ())
    if dependency_policy == "own-only":
        # ★ 2026-10-27：**只依赖自己**。用于验证「自包含的包（externals=0）到底要不要
        #   抄原版那张依赖表」——依赖数组 = [自己的包条目]、hash = 0（真件 17,963 条全是 0）。
        notes.append("依赖声明=只含自己那个包条目（own-only），hash=0")
    elif dependency_source_key:
        ref = str(dependency_source_key)
        if ref not in names:
            raise AssetError(
                f"参照键不在 catalog 里：{ref!r}",
                hint="要一个**已入库的真件地址键**当参照（例 DLC2_CV9035）",
            )
        rows = tuple(model.keys[names.index(ref)].ranks)
        if not rows:
            raise AssetError(f"参照键 {ref!r} 的位置集是空的 —— 抄不到依赖声明")
        ref_entry = model.entries[rows[0]]
        if not (ref_entry.provider or "").endswith("BundledAssetProvider"):
            raise AssetError(
                f"参照键 {ref!r} 指的不是一条资产地址（provider={ref_entry.provider}）",
                hint=f"要抄的是 provider 结尾为 {provider!r} 的地址条目",
            )
        dep_ranks = _merge_bundle_deps(bundle_ranks, ref_entry.dep_ranks)
        dep_hash = int(ref_entry.dep_hash)
        notes.append(f"依赖声明抄自 {ref}，并入了 --bundle 那个包条目")
    else:
        same = [
            e
            for e in model.entries
            if e.internal_id == text_path and (e.provider or "").endswith("BundledAssetProvider")
        ]
        if same:
            dep_ranks = _merge_bundle_deps(bundle_ranks, same[0].dep_ranks)
            dep_hash = int(same[0].dep_hash)
            notes.append(f"依赖声明抄自同路径既有条目 rank={same[0].rank}，并入了 --bundle")

    missing = [r for r in bundle_ranks if r not in dep_ranks]
    if missing:
        raise AssetError(
            f"依赖数组里没有新包（包条目 {'/'.join(map(str, missing))} 缺）—— "
            "装上去游戏会去别的包里找资产",
            hint=(
                f"`--bundle {bundle!r}` 的包条目必须永远在依赖数组里：Addressables 只打开"
                "依赖数组里的包，照抄参照条目的依赖会把**自己那个新包**抄没 —— "
                "真机症状是 `Addressable prefab '…' was not loaded!` 且新包从未被打开"
            ),
        )

    rank = len(model.entries)
    model.entries.append(
        BinEntry(
            loc_offset=-1,
            primary_key=text_key,
            internal_id=text_path,
            provider=provider,
            dep_ranks=tuple(dep_ranks),
            dep_hash=dep_hash,
            type_asm=type_asm,
            type_cls=type_cls,
            rank=rank,
        )
    )
    key_asm, key_cls = _key_type(model)
    model.keys.append(
        BinKey(name=text_key, src_offsets=(), ranks=(rank,), type_asm=key_asm, type_cls=key_cls)
    )
    after = serialize(model)
    lines = [
        f"新地址：{text_key} -> {text_path}",
        f"  包登记：{bundle}（键 #{bundle_key_index} -> 条目 rank {bundle_ranks}）"
        f"   resource_type {int(resource_type)} -> {type_cls}",
        f"  落位：新条目 rank #{rank}（追加在**条目表末尾**，它的 Data 最后写）"
        f" · 新键 #{len(model.keys) - 1}",
        f"  依赖数组：{list(dep_ranks)}（{len(dep_ranks)} 个包条目，**含 --bundle 那条**）"
        f"  依赖 hash：{dep_hash}",
    ]
    lines.extend(f"  （{note}）" for note in notes)
    return BinAddressPlan(
        action="add",
        key=text_key,
        before=before,
        after=after,
        counts_before=counts_before,
        counts_after=_counts_of(model),
        lines=tuple(lines),
        entry_index=rank,
        bundle_key_index=bundle_key_index,
        asset_path=text_path,
        bundle_name=bundle,
        resource_type=int(resource_type),
        dependency_hash=dep_hash,
    )


def delete_address(raw: bytes, *, key: str) -> BinAddressPlan:
    """**纯函数**：二进制 catalog 字节 -> 删掉一条地址之后的字节（⛔ 不碰盘）。

    ⚠ 口径（与 JSON 侧的 `remove_address` **有一处不同，如实写**）：这里只把**键**从键表拿掉，
    **不拆**那个条目 —— 二进制版的条目可能被别的键（别名）或别的条目的依赖数组指着，
    拆了会静默弄坏它们。留下来的条目游戏读不到（键表里没有它），是**死重量**，不是错。
    """
    text_key = str(key).strip()
    if not text_key:
        raise AssetError("--key 是空的", hint="要删哪一条地址就写那个键")
    before = bytes(raw)
    model = read_model(before)
    counts_before = _counts_of(model)
    index = next((i for i, k in enumerate(model.keys) if k.name == text_key), None)
    if index is None:
        raise AssetError(
            f"catalog 里没有这个键：{text_key!r}",
            hint="删一个不存在的键会静默通过 —— 本命令不干那件事（先核 catalog 里那条键）",
        )
    orphaned = tuple(model.keys[index].ranks)
    del model.keys[index]
    still = {r for k in model.keys for r in k.ranks}
    still.update(r for e in model.entries for r in e.dep_ranks)
    dangling = tuple(r for r in orphaned if r not in still)
    after = serialize(model)
    lines = [
        f"删地址：{text_key}",
        f"  键表 {counts_before[0]} -> {len(model.keys)} 条键；"
        f"条目表**不动**（{len(model.entries)} 条）",
        f"  该键原先指向条目 rank {orphaned}"
        + (
            f"，其中 {list(dangling)} 已无任何键/依赖引用（保留，成死重量）"
            if dangling
            else "（仍被别处引用）"
        ),
    ]
    return BinAddressPlan(
        action="delete",
        key=text_key,
        before=before,
        after=after,
        counts_before=counts_before,
        counts_after=_counts_of(model),
        lines=tuple(lines),
        entry_index=orphaned[0] if orphaned else -1,
    )


def set_dependency_bundles(
    raw: bytes, *, key: str, bundles: tuple[str, ...] | list[str]
) -> BinAddressPlan:
    """**纯函数**：把一条**已有**地址的依赖数组换成 `bundles` 那几个包条目（⛔ 不碰盘）。

    口径（与 `add_address` 的 own-only 分支同一个不变量）：依赖数组里**必须**留着指向资产
    本体的那个包，否则 Addressables 根本不打开它（真机症状见 `_merge_bundle_deps` 的 hint）。
    依赖 hash（`entry[3]` 后面那格）一律写 0 —— 真件 17,963 条全是 0，`add_address` 也这么写。
    """
    text_key = str(key).strip()
    if not text_key:
        raise AssetError("--key 是空的", hint="要改哪条地址就写那个键")
    names = [str(b).strip() for b in bundles if str(b).strip()]
    if not names:
        raise AssetError(
            "依赖数组不能是空的",
            hint=(
                "至少要留**自己那个包**：Addressables 只打开依赖数组里的包，"
                "清空等于让它永远打不开"
            ),
        )
    before = bytes(raw)
    model = read_model(before)
    counts_before = _counts_of(model)
    key_obj = next((k for k in model.keys if k.name == text_key), None)
    if key_obj is None:
        raise AssetError(
            f"catalog 里没有这个键：{text_key!r}",
            hint="先用 `catalog key <键>` 看一眼它在不在（改一条不存在的地址 = 静默通过）",
        )
    targets = [model.entries[r] for r in key_obj.ranks if model.entries[r].dep_ranks]
    if not targets:
        raise AssetError(
            f"{text_key!r} 这条键下没有**带依赖的资产地址**条目",
            hint="这条键可能是包登记 / 内部键；依赖数组只挂在资产地址那一条上",
        )
    wanted: list[int] = []
    for name in names:
        ranks = {
            r
            for k in model.keys
            if k.name == name
            for r in k.ranks
            if _is_bundle_entry(model.entries[r])
        }
        if not ranks:
            raise AssetError(
                f"catalog 里没有这个包：{name!r}",
                hint=(
                    "包名 = `<工程名>_assets_all_<32hex>.bundle`"
                    "（`catalog key <键>` 的依赖表里能看到）"
                ),
            )
        if len(ranks) > 1:
            raise AssetError(
                f"包名 {name!r} 对到多条包条目：{sorted(ranks)}",
                hint="不猜 —— 先看清楚为什么同一个包名登记了两条",
            )
        wanted.append(next(iter(ranks)))
    entry = targets[0]
    entry_index = model.entries.index(entry)
    old_ranks, old_hash = tuple(entry.dep_ranks), entry.dep_hash
    entry.dep_ranks = tuple(wanted)
    entry.dep_hash = 0
    after = serialize(model)
    lines = [
        f"改依赖：{text_key}（条目 rank #{entry_index}）",
        f"  依赖数组 {list(old_ranks)}（{len(old_ranks)} 个）-> {list(wanted)}（{len(wanted)} 个）",
        f"  依赖 hash {old_hash} -> 0（真件全是 0）",
        f"  新字节 {len(after):,} B（原 {len(before):,} B）· 条目表与键表**条数不变**",
    ]
    return BinAddressPlan(
        action="set-deps",
        key=text_key,
        before=before,
        after=after,
        counts_before=counts_before,
        counts_after=_counts_of(model),
        lines=tuple(lines),
        entry_index=entry_index,
        bundle_name=names[0],
        dependency_hash=0,
    )

