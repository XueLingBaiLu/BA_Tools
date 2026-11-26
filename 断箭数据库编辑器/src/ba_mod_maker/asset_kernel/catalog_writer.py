r"""W8-B · catalog 的地址登记：**六件套 + 三个计数头**（设计稿 §9.6，四个坑各一条负控）。

## 这一模块解决什么

往 Addressables 的 catalog.json 里**追加**一条 bundle 登记（新包），要同一次改完六处：

    1. m_InternalIds      追加一条 {RuntimePath}\PC\<bundle 文件名>
    2. m_KeyDataString    追加一条 keyType = 0 的 key（值 = **bundle 文件名**）
    3. m_BucketDataString 追加一个 bucket（keyDataOffset 指回该 key、entryIndex 指新 entry）
    4. m_EntryDataString  追加 7 个 int32
                          （internalIdIndex / 0 / -1 / 17 / extraOffset / keyIndex / 0）
    5. m_ExtraDataString  追加 AssetBundleRequestOptions（UTF-16LE 紧凑 JSON）
    6. 三个计数头         keyCount / bucketCount / entryCount 各 +N
    7.（可选）资产地址    primaryKey（如 Assets/...）自己的 key + bucket + entry
                          （依赖键 = 第 2 步那条 key）

## 实测（2026-10-03，本机 catalog.json，自己量的）

| 项 | 读数 |
|---|---|
| 四张表长 | KeyData 1,169,750 B / BucketData 436,832 B / EntryData 595,676 B / ExtraData 65,207 B |
| 三个计数头 | keyCount **28,937** / bucketCount **28,937** |
| 同上 | entryCount **21,274**（三条各是表首 4 字节的那一格） |
| 第一个 bucket 的 keyDataOffset | **4**（正好跳过 keyCount 那一格） |
| entry 步长 | **28 B = 7 x int32**（21,274 x 28 + 4 = 595,676，分毫不差） |
| 桶大小 | 实测**不是恒为 1**：size-1 **15,478** 个 / size-2 13,156 个 / 再大的 303 个 |
| entry[5]（primaryKey） | 21,274/21,274 都落在「桶指向它的那些 key」里 |
| 同上 | 0 例外 —— 这就是 check_pairs 那条尺子的正控 |
| 77 条 bundle 条目 | 一律 (internalIdIndex, **0**, **-1**, **17**, extraOffset, keyIndex, **0**) |
| 资产条目 | (internalIdIndex, **2**, 依赖 key 下标, 依赖 hash, **-1**, keyIndex, 类型下标) |
| 依赖 hash | 每个依赖 key 一个固定值（77 个 key -> 77 个 hash，1:1）；**算法未知**（见下） |

## 四个坑（都会静默错）

1. **字节长不是字符数** —— keyType = 1（UTF16）与 extra data 的 JSON 长度都按**字节**算；
   算错一次整张表错位（core/assets/catalog.py 会当场报 key 长度与桶边界不符）。
2. **偏移会连锁平移** —— extraDataOffset 是**字节偏移**；改一次 m_Crc 的十进制位数就让后面
   所有 offset 平移，所以必须**全量重算**，不许字符串替换。
3. **不许抄模板条目的 7 元组** —— 抄来的 [2]（依赖键）指向的是**模板资产所在的那个包**；
   新 bundle 条目一律 [2] = **-1**（设计稿 §9.6 第 3 条）。
4. **entry 与 primaryKey 必须一对一** —— 一个包挂多个地址时，一条资产 entry 配一条自己的
   key/bucket；顺序错了地址就指到别的资产上，而且**不报错**。
   尺子：check_pairs（entry[5] 必须落在「桶指向它的那些 key」里；真件上 21,274/21,274 成立）。

## 与设计稿 §9.6 的一处**实测不符**（如实报，不按文档改代码）

设计稿写「桶大小**恒为 1**」。实测本机 catalog.json **不是**：size-1 有 15,478 个，
size-2 有 13,156 个（一条 key 会把它解析到的 entry 都列进自己的桶，例如 bundle entry 0
同时出现在 37 个桶里）。本模块追加的是**size-1 桶** —— 产品读侧（core/assets/catalog.py）
靠 entry[2] 的依赖键 + 被依赖 key 自己的桶推导 bundle，size-1 足够读回来（用例在合成件上
逐条核过）。**游戏运行时是否需要把被依赖 entry 也塞进依赖 key 的桶，本单没量**，
已列为接缝问题交给 Lead。


## 未知量（如实写，不许编）

* **依赖 hash（entry[3]）的算法没找出来**：试过 mono string hash / crc32 / adler32 / FNV-1a /
  文件名前 8 字节 / 后 8 字节，与实测的 77 个值都对不上。本模块的做法是从**既有条目**里
  抄同一个依赖键的那个值（dependency_hash_of），抄不到就要求调用方显式给；
  绝不编一个数出来。
* **真写**（改磁盘上那份 catalog.json）不在本模块的验收面里：本模块只出字节与计划，
  写盘由集成阶段统一做。

## 边界

* 顶层零第三方（只有 json / struct / base64 与 core.errors）。
* 不认游戏路径：调用方给什么路径就写什么路径（用例全在 tmp_path 上）。
"""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ba_mod_maker.core.errors import AssetError

__all__ = [
    "ASSET_BUNDLE_PROVIDER",
    "BUNDLED_ASSET_PROVIDER",
    "CATALOG_FILENAME",
    "ENTRY_FIELDS",
    "INVALID_DEPENDENCY_HASH",
    "KEY_HASH128",
    "KEY_INT32",
    "KEY_UINT16",
    "KEY_UINT32",
    "KEY_UTF8",
    "KEY_UTF16",
    "OPTIONS_FIELDS",
    "PC_SEGMENT",
    "RUNTIME_PATH",
    "AddressRegistration",
    "BundleRegistration",
    "CatalogAddress",
    "CatalogBundle",
    "CatalogTable",
    "apply_address",
    "apply_bundle",
    "bucket_owners",
    "bundle_key_index",
    "check_pairs",
    "entry_options",
    "extra_data_blob",
    "key_bytes",
    "load_catalog",
    "options_json",
    "table_bytes",
]

CATALOG_FILENAME = "catalog.json"

#: m_InternalIds 里的 RuntimePath 占位符（实测 77/77 条都是它 + \PC\）。
RUNTIME_PATH = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
PC_SEGMENT = "\\PC\\"

KEY_UTF8 = 0
KEY_UTF16 = 1
KEY_UINT16 = 2
KEY_UINT32 = 3
KEY_INT32 = 4
KEY_HASH128 = 5

#: m_ProviderIds 的下标（实测本机 = [AssetBundleProvider, LegacyResourcesProvider,
#: BundledAssetProvider, '']）。这两个常量只对**本机那份** catalog 成立，所以用例里
#: 会拿 m_ProviderIds 的名字复核一次（⛔ 不假定别的游戏版本一样）。
ASSET_BUNDLE_PROVIDER = 0
BUNDLED_ASSET_PROVIDER = 2

#: 没有依赖时 entry[3] 的值（实测 77/77 个 bundle 条目都是 17）。
INVALID_DEPENDENCY_HASH = 17

#: AssetBundleRequestOptions 那条 extra data 的类型标记（实测每一条都长这样）。
EXTRA_TYPE_MARKER = 0x07
_EXTRA_ASSEMBLY = b"Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
_EXTRA_CLASS = b"UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"

#: catalog.json 的序列化口径（与 writer 那边同一组参数；正控在 load_catalog 里）。
_CATALOG_JSON_KW: dict[str, Any] = {"separators": (",", ":"), "ensure_ascii": False}

#: AssetBundleRequestOptions 的字段顺序（逐字段照抄磁盘上那条 JSON 的顺序）。
OPTIONS_FIELDS: tuple[str, ...] = (
    "m_Hash",
    "m_Crc",
    "m_Timeout",
    "m_ChunkedTransfer",
    "m_RedirectLimit",
    "m_RetryCount",
    "m_BundleName",
    "m_AssetLoadMode",
    "m_BundleSize",
    "m_UseCrcForCachedBundles",
    "m_UseUWRForLocalBundles",
    "m_ClearOtherCachedVersionsWhenLoaded",
)

#: 每条 entry 的 int32 个数（实测 7；本版只认 7）。
ENTRY_FIELDS = 7


# --------------------------------------------------------------------------- #
# 单件：key 的编码 / extra data 的编码
# --------------------------------------------------------------------------- #
def key_bytes(kind: int, value: Any) -> bytes:
    """把一条 key 编成 m_KeyDataString 里的那段字节。

    坑 1：字符串的长度一律是**编码后的字节数**，不是字符数（UTF-16 的中文 = 2 B/字）。
    """
    if kind == KEY_UTF8:
        raw = str(value).encode("utf-8")
        return bytes([kind]) + struct.pack("<i", len(raw)) + raw
    if kind == KEY_UTF16:
        raw = str(value).encode("utf-16-le")
        return bytes([kind]) + struct.pack("<i", len(raw)) + raw
    if kind == KEY_UINT16:
        return bytes([kind]) + struct.pack("<H", int(value))
    if kind == KEY_UINT32:
        return bytes([kind]) + struct.pack("<I", int(value))
    if kind == KEY_INT32:
        return bytes([kind]) + struct.pack("<i", int(value))
    if kind == KEY_HASH128:
        raw16 = bytes.fromhex(str(value))
        if len(raw16) != 16:
            raise ValueError(f"HASH128 要 16 字节，拿到 {len(raw16)}")
        return bytes([kind]) + raw16
    raise ValueError(f"未知的 key 类型 {kind}")


def options_json(options: Mapping[str, Any]) -> bytes:
    """AssetBundleRequestOptions 的 UTF-16LE 紧凑 JSON（长度按**字节**算）。"""
    return json.dumps(options, **_CATALOG_JSON_KW).encode("utf-16-le")


def extra_data_blob(options: Mapping[str, Any]) -> bytes:
    """一条 extra data：SerializedType 头 + UTF-16LE JSON。

        [1B 0x07][1B 程序集名长 + 名][1B 类名长 + 名][i32 字节长][JSON]

    实测 77 条都是这个头（157 B + JSON）；extraDataOffset 指的是**整条的头**，不是 JSON 起点。
    """
    blob = options_json(options)
    return (
        bytes([EXTRA_TYPE_MARKER, len(_EXTRA_ASSEMBLY)])
        + _EXTRA_ASSEMBLY
        + bytes([len(_EXTRA_CLASS)])
        + _EXTRA_CLASS
        + struct.pack("<i", len(blob))
        + blob
    )


def _decode_key(blob: bytes | bytearray, start: int) -> tuple[Any, int]:
    """从 key_data 的 start 起解一条 key，返回 (值, 下一条的起点)。"""
    kind = blob[start]
    cur = start + 1
    if kind in (KEY_UTF8, KEY_UTF16):
        (size,) = struct.unpack_from("<i", blob, cur)
        cur += 4
        raw = bytes(blob[cur : cur + size])
        cur += size
        value = raw.decode("utf-8" if kind == KEY_UTF8 else "utf-16-le", "replace")
    elif kind == KEY_UINT16:
        (value,) = struct.unpack_from("<H", blob, cur)
        cur += 2
    elif kind == KEY_UINT32:
        (value,) = struct.unpack_from("<I", blob, cur)
        cur += 4
    elif kind == KEY_INT32:
        (value,) = struct.unpack_from("<i", blob, cur)
        cur += 4
    elif kind == KEY_HASH128:
        value = "HASH128:" + bytes(blob[cur : cur + 16]).hex()
        cur += 16
    else:
        raise AssetError(f"未知的 key 类型 {kind} @{start}")
    return value, cur


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CatalogBundle:
    """要登记的那个新包（不传 internal_id 就按 RuntimePath + \\PC\\ + 文件名拼）。"""

    bundle_name: str
    m_hash: str
    crc: int
    bundle_size: int
    internal_name: str = ""
    internal_id: str | None = None
    timeout: int = 0
    chunked_transfer: bool = False
    redirect_limit: int = -1
    retry_count: int = 0
    asset_load_mode: int = 0
    use_crc_for_cached_bundles: bool = True
    use_uwr_for_local_bundles: bool = False
    clear_other_cached_versions_when_loaded: bool = False

    def options(self) -> dict[str, Any]:
        values = {
            "m_Hash": str(self.m_hash),
            "m_Crc": int(self.crc),
            "m_Timeout": int(self.timeout),
            "m_ChunkedTransfer": bool(self.chunked_transfer),
            "m_RedirectLimit": int(self.redirect_limit),
            "m_RetryCount": int(self.retry_count),
            "m_BundleName": str(self.internal_name),
            "m_AssetLoadMode": int(self.asset_load_mode),
            "m_BundleSize": int(self.bundle_size),
            "m_UseCrcForCachedBundles": bool(self.use_crc_for_cached_bundles),
            "m_UseUWRForLocalBundles": bool(self.use_uwr_for_local_bundles),
            "m_ClearOtherCachedVersionsWhenLoaded": bool(
                self.clear_other_cached_versions_when_loaded
            ),
        }
        return {name: values[name] for name in OPTIONS_FIELDS}


@dataclass(frozen=True, slots=True)
class CatalogAddress:
    """要新增的**资产地址**（primaryKey -> m_InternalIds 里那条 Assets/...）。"""

    asset_path: str
    primary_key: str
    resource_type: int


@dataclass(frozen=True, slots=True)
class BundleRegistration:
    bundle_name: str
    internal_id: str
    internal_id_index: int
    key_index: int
    bucket_index: int
    entry_index: int
    extra_offset: int


@dataclass(frozen=True, slots=True)
class AddressRegistration:
    primary_key: str
    asset_path: str
    internal_id_index: int
    key_index: int
    bucket_index: int
    entry_index: int


@dataclass
class CatalogTable:
    """一份 catalog 的可写视图（四张表 + m_InternalIds；其余字段**原样保留**）。"""

    raw: dict[str, Any]
    internal_ids: list[str]
    key_data: bytearray
    bucket_data: bytearray
    entry_data: bytearray
    extra_data: bytearray
    key_count: int
    bucket_count: int
    entry_count: int
    key_offsets: list[int] = field(default_factory=list)

    # ---- 读 ----
    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> CatalogTable:
        data = dict(raw)
        internal_ids = list(raw["m_InternalIds"])
        key_data = bytearray(base64.b64decode(raw["m_KeyDataString"]))
        bucket_data = bytearray(base64.b64decode(raw["m_BucketDataString"]))
        entry_data = bytearray(base64.b64decode(raw["m_EntryDataString"]))
        extra_data = bytearray(base64.b64decode(raw["m_ExtraDataString"]))
        if len(key_data) < 4 or len(bucket_data) < 4 or len(entry_data) < 4:
            raise AssetError("catalog 的三张表至少要有 4 字节的计数头")
        (key_count,) = struct.unpack_from("<i", key_data, 0)
        (bucket_count,) = struct.unpack_from("<i", bucket_data, 0)
        (entry_count,) = struct.unpack_from("<i", entry_data, 0)
        if entry_count <= 0:
            raise AssetError("m_EntryDataString 里一条条目都没有")
        stride = (len(entry_data) - 4) // entry_count
        if stride != ENTRY_FIELDS * 4:
            raise AssetError(
                f"entry 步长 {stride} B（= {stride // 4} 个 int32），本版只认 {ENTRY_FIELDS} 个",
                hint="设计稿 §9.6 的规格是 7 x int32；换了游戏版本先复核再动",
            )
        key_offsets: list[int] = []
        pos = 4
        for _ in range(bucket_count):
            if pos + 8 > len(bucket_data):
                raise AssetError("m_BucketDataString 比声明的桶数还短")
            (key_offset, entry_len) = struct.unpack_from("<ii", bucket_data, pos)
            pos += 8 + 4 * entry_len
            key_offsets.append(key_offset)
        if key_count != bucket_count:
            raise AssetError(
                f"keyCount {key_count} != bucketCount {bucket_count}",
                hint="一个 key 一个桶（桶大小恒为 1）是本格式的口径；不等说明表已经坏了",
            )
        return cls(
            raw=data,
            internal_ids=internal_ids,
            key_data=key_data,
            bucket_data=bucket_data,
            entry_data=entry_data,
            extra_data=extra_data,
            key_count=key_count,
            bucket_count=bucket_count,
            entry_count=entry_count,
            key_offsets=key_offsets,
        )

    @property
    def counts(self) -> tuple[int, int, int]:
        return (self.key_count, self.bucket_count, self.entry_count)

    def key_value(self, key_index: int) -> Any:
        return _decode_key(self.key_data, self.key_offsets[key_index])[0]

    def key_of_offset(self, key_offset: int) -> Any:
        return _decode_key(self.key_data, key_offset)[0]

    def entry(self, entry_index: int) -> tuple[int, ...]:
        return struct.unpack_from("<7i", self.entry_data, 4 + entry_index * 28)

    def entries(self) -> list[tuple[int, ...]]:
        return [self.entry(i) for i in range(self.entry_count)]

    def buckets(self) -> list[tuple[int, list[int]]]:
        out: list[tuple[int, list[int]]] = []
        pos = 4
        for _ in range(self.bucket_count):
            (key_offset, entry_len) = struct.unpack_from("<ii", self.bucket_data, pos)
            pos += 8
            out.append(
                (key_offset, list(struct.unpack_from(f"<{entry_len}i", self.bucket_data, pos)))
            )
            pos += 4 * entry_len
        return out

    def entry_options(self, entry_index: int) -> dict[str, Any] | None:
        """entry -> 它那条 AssetBundleRequestOptions（extraDataOffset = -1 时返回 None）。"""
        offset = self.entry(entry_index)[4]
        if offset < 0:
            return None
        return entry_options(bytes(self.extra_data), offset)

    def dependency_hash_of(self, key_index: int) -> int | None:
        """从**既有条目**里抄出这个依赖 key 的 hash（值的算法未知，见模块头）。"""
        for row in self.entries():
            if row[2] == key_index and row[3] != INVALID_DEPENDENCY_HASH:
                return int(row[3])
        return None

    # ---- 写（只追加）----
    def add_internal_id(self, value: str) -> int:
        self.internal_ids.append(str(value))
        return len(self.internal_ids) - 1

    def add_key(self, value: Any, kind: int = KEY_UTF8) -> int:
        encoded = key_bytes(kind, value)
        self.key_offsets.append(len(self.key_data))
        self.key_data += encoded
        self.key_count += 1
        return self.key_count - 1

    def add_bucket(self, key_index: int, entry_index: int) -> int:
        offset = self.key_offsets[key_index]
        self.bucket_data += struct.pack("<iii", offset, 1, entry_index)
        self.bucket_count += 1
        return self.bucket_count - 1

    def add_entry(self, values: Sequence[int]) -> int:
        if len(values) != ENTRY_FIELDS:
            raise ValueError(f"entry 要 {ENTRY_FIELDS} 个 int32，拿到 {len(values)}")
        self.entry_data += struct.pack("<7i", *(int(v) for v in values))
        self.entry_count += 1
        return self.entry_count - 1

    def add_extra(self, blob: bytes) -> int:
        offset = len(self.extra_data)
        self.extra_data += blob
        return offset

    def to_raw(self) -> dict[str, Any]:
        """三个计数头一起 +N，四张表重新编码（其余 JSON 字段原样保留）。"""
        out = dict(self.raw)
        struct.pack_into("<i", self.key_data, 0, self.key_count)
        struct.pack_into("<i", self.bucket_data, 0, self.bucket_count)
        struct.pack_into("<i", self.entry_data, 0, self.entry_count)
        out["m_InternalIds"] = list(self.internal_ids)
        out["m_KeyDataString"] = base64.b64encode(bytes(self.key_data)).decode("ascii")
        out["m_BucketDataString"] = base64.b64encode(bytes(self.bucket_data)).decode("ascii")
        out["m_EntryDataString"] = base64.b64encode(bytes(self.entry_data)).decode("ascii")
        out["m_ExtraDataString"] = base64.b64encode(bytes(self.extra_data)).decode("ascii")
        return out

    def counts_line(self) -> str:
        return (
            f"计数头 keyCount {self.key_count:,} / bucketCount {self.bucket_count:,} / "
            f"entryCount {self.entry_count:,}；m_InternalIds {len(self.internal_ids):,}"
        )


def entry_options(blob: bytes, offset: int) -> dict[str, Any]:
    """按 extraDataOffset 解出那条 AssetBundleRequestOptions（跳过 SerializedType 头）。"""
    if offset < 0 or offset + 4 > len(blob):
        raise AssetError(f"extraDataOffset {offset} 越出 m_ExtraDataString（{len(blob)} B）")
    if blob[offset] != EXTRA_TYPE_MARKER:
        raise AssetError(
            f"extraDataOffset {offset} 的首字节是 {blob[offset]:#x}，不是 {EXTRA_TYPE_MARKER:#x}",
            hint="偏移指错地方了（extraDataOffset 指的是整条 extra data 的开头）",
        )
    pos = offset + 1
    pos += 1 + blob[pos]
    pos += 1 + blob[pos]
    (byte_len,) = struct.unpack_from("<i", blob, pos)
    pos += 4
    end = pos + byte_len
    if byte_len <= 0 or end > len(blob):
        raise AssetError(f"extraDataOffset {offset} 声明的 JSON 字节长 {byte_len} 越界")
    return json.loads(blob[pos:end].decode("utf-16-le"))


# --------------------------------------------------------------------------- #
# 六件套：追加一条 bundle / 一条资产地址
# --------------------------------------------------------------------------- #
def apply_bundle(table: CatalogTable, bundle: CatalogBundle) -> BundleRegistration:
    """六件套：一条新 bundle 的登记（entry 的 [2] 一律 -1，坑 3）。"""
    internal_id = bundle.internal_id or (RUNTIME_PATH + PC_SEGMENT + bundle.bundle_name)
    internal_index = table.add_internal_id(internal_id)
    extra_offset = table.add_extra(extra_data_blob(bundle.options()))
    key_index = table.add_key(bundle.bundle_name, KEY_UTF8)
    entry_index = table.add_entry(
        [
            internal_index,
            ASSET_BUNDLE_PROVIDER,
            -1,  # 坑 3：不许抄模板条目的依赖键（那会指向模板资产所在的那个包）
            INVALID_DEPENDENCY_HASH,
            extra_offset,
            key_index,  # 坑 4：entry 的 primaryKey 必须是自己那条 key
            0,
        ]
    )
    bucket_index = table.add_bucket(key_index, entry_index)
    return BundleRegistration(
        bundle_name=bundle.bundle_name,
        internal_id=internal_id,
        internal_id_index=internal_index,
        key_index=key_index,
        bucket_index=bucket_index,
        entry_index=entry_index,
        extra_offset=extra_offset,
    )


def apply_address(
    table: CatalogTable,
    address: CatalogAddress,
    *,
    dependency_key: int,
    dependency_hash: int | None = None,
) -> AddressRegistration:
    """第 7 件：新增一条**资产地址**（依赖键 = bundle 那条 key 的下标）。

    坑 4 的要害：primaryKey 必须是**这条 entry 自己的** key 下标；多条地址共用一个包时，
    每条地址各配一条 key/bucket。
    """
    dep_hash = dependency_hash
    if dep_hash is None:
        dep_hash = table.dependency_hash_of(dependency_key)
    if dep_hash is None:
        dep_hash = INVALID_DEPENDENCY_HASH
    internal_index = table.add_internal_id(address.asset_path)
    key_index = table.add_key(address.primary_key, KEY_UTF8)
    entry_index = table.add_entry(
        [
            internal_index,
            BUNDLED_ASSET_PROVIDER,
            dependency_key,
            int(dep_hash),
            -1,
            key_index,
            int(address.resource_type),
        ]
    )
    bucket_index = table.add_bucket(key_index, entry_index)
    return AddressRegistration(
        primary_key=address.primary_key,
        asset_path=address.asset_path,
        internal_id_index=internal_index,
        key_index=key_index,
        bucket_index=bucket_index,
        entry_index=entry_index,
    )


def bucket_owners(table: CatalogTable) -> dict[int, tuple[int, ...]]:
    """entry 下标 -> 指向它的那些 key 下标（真件里一个 entry 可以被多个桶指着）。"""
    out: dict[int, list[int]] = {}
    for key_index, (_offset, entries) in enumerate(table.buckets()):
        for entry_index in entries:
            out.setdefault(entry_index, []).append(key_index)
    return {entry: tuple(keys) for entry, keys in out.items()}


def check_pairs(table: CatalogTable) -> tuple[str, ...]:
    """坑 4 的尺子：每条 entry 的 primaryKey（entry[5]）必须落在「桶指向它的那些 key」里。

    实测本机真件 21,274/21,274 成立（0 例外）—— 所以这条尺子**能红**：
    把两条 entry 的 primaryKey 对调，立刻两条都不成立。
    """
    owners = bucket_owners(table)
    problems: list[str] = []
    for entry_index in range(table.entry_count):
        primary = table.entry(entry_index)[5]
        keys = owners.get(entry_index, ())
        if not keys:
            problems.append(f"entry {entry_index} 没有任何桶指向它")
        elif primary not in keys:
            problems.append(
                f"entry {entry_index} 的 primaryKey {primary} 不在指向它的 key {list(keys)} 里"
            )
    return tuple(problems)


def bundle_key_index(table: CatalogTable, bundle_name: str) -> int | None:
    """找一条 bundle 的 key 下标（按 key 的值 = bundle 文件名）。"""
    for index in range(table.key_count):
        if table.key_value(index) == bundle_name:
            return index
    return None


def table_bytes(table: CatalogTable) -> bytes:
    """把表序列化成 catalog.json 的字节（磁盘口径：紧凑 + 不转义非 ASCII）。"""
    return json.dumps(table.to_raw(), **_CATALOG_JSON_KW).encode("utf-8")


def load_catalog(path: str | Path, *, raw_bytes: bytes | None = None) -> CatalogTable:
    """读一份 catalog.json（带**正控**：重新序列化必须逐字节回到原样）。

    raw_bytes 是给「还没落盘的合成件」用的；给了它就不去读磁盘。
    """
    if raw_bytes is None:
        raw_bytes = Path(path).read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8"))
    if json.dumps(raw, **_CATALOG_JSON_KW).encode("utf-8") != raw_bytes:
        raise AssetError(
            "catalog.json 重新序列化不是逐字节原样",
            hint="序列化口径与磁盘上那份不一致；宁可不写，也不要把 catalog 重排坏",
        )
    return CatalogTable.from_raw(raw)
